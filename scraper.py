"""
FI Insiderhandel Scraper — optimerad version
- Hämtar bara nya affärer sedan senaste körningen
- Exponentiell backoff vid anslutningsfel
- Robustare beloppsparser
- Deduplicering på anmälnings-ID
"""

import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup


# ── Konfiguration ──────────────────────────────────────────────────────────────
THRESHOLD_SEK  = 1_000_000
OUTPUT_FILE    = Path("data/trades.json")
BASE_URL       = "https://marknadssok.fi.se/Publiceringsklient/sv-SE/Search/Search/Insyn"
FI_BASE        = "https://marknadssok.fi.se"
MAX_RETRIES    = 4          # Antal retry-försök per sida
BASE_DELAY     = 1.0        # Grundpaus mellan sidor (sekunder)


# ── Hjälpfunktioner ────────────────────────────────────────────────────────────
def load_existing() -> dict:
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"updated": "", "threshold_sek": THRESHOLD_SEK, "total": 0, "trades": []}


def days_back_needed(existing: dict) -> int:
    """Räknar ut hur många dagar tillbaka vi behöver hämta baserat på senaste körning."""
    last = existing.get("updated", "")
    if not last:
        print("  Ingen tidigare data — hämtar 30 dagars historik.")
        return 30
    try:
        last_dt = datetime.fromisoformat(last)
        days = (datetime.now() - last_dt).days + 2  # +2 dagars marginal
        days = max(2, min(days, 30))
        print(f"  Senaste körning: {last_dt:%Y-%m-%d} — hämtar {days} dagars data.")
        return days
    except Exception:
        return 30


def parse_amount(volume_str: str, price_str: str) -> float:
    """Robust beloppsparser som hanterar alla format FI använder."""
    def clean(s: str) -> float:
        if not s or s.strip() == "-" or s.strip() == "":
            return 0.0
        # Ta bort alla mellanslag (inkl. non-breaking)
        s = s.replace("\xa0", "").replace("\u202f", "").replace("\u00a0", "").replace(" ", "")
        # FI använder komma som decimalseparator
        # Om det finns både punkt och komma, är punkt tusentals och komma decimal
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", ".")
        s = re.sub(r"[^\d\.]", "", s)
        # Ta bort extra punkter (behåll bara sista)
        parts = s.split(".")
        if len(parts) > 2:
            s = "".join(parts[:-1]) + "." + parts[-1]
        return float(s) if s else 0.0

    return clean(volume_str) * clean(price_str)


# ── Score-beräkning ────────────────────────────────────────────────────────────
def calc_base_score(trade: dict) -> int:
    """Grundscore baserat på enskild affär (0-80p)."""
    score = 0

    # ── Belopp (0–30p) ──
    amount = trade.get("amount_sek", 0)
    if   amount >= 50_000_000: score += 30
    elif amount >= 20_000_000: score += 25
    elif amount >= 10_000_000: score += 20
    elif amount >=  5_000_000: score += 16
    elif amount >=  3_000_000: score += 12
    elif amount >=  2_000_000: score +=  8
    else:                      score +=  4

    # ── Roll (0–25p) ──
    role = trade.get("role", "").lower()
    if any(k in role for k in ["verkställande direktör", "vd", "ceo"]):
        score += 25
    elif any(k in role for k in ["ordförande", "chairman"]):
        score += 22
    elif any(k in role for k in ["styrelseledamot", "styrelse"]):
        score += 17
    elif any(k in role for k in ["ekonomichef", "cfo", "finanschef"]):
        score += 13
    elif any(k in role for k in ["ledande", "befattning"]):
        score += 3  # Kan vara obligatoriskt köp enligt avtal
    else:
        score += 4

    # ── Instrumenttyp (0–15p) ──
    itype = trade.get("instrument_type", "").lower()
    if "aktie" in itype:
        score += 15   # Riktiga pengar på bordet
    elif "teckningsoption" in itype or "warrant" in itype:
        score += 4    # Ofta gratis tilldelning — svag signal
    else:
        score += 9

    # ── Färskhet (0–10p) ──
    try:
        trade_date = datetime.fromisoformat(trade.get("trade_date", "")[:10])
        days_ago = (datetime.now() - trade_date).days
        if   days_ago <= 3:  score += 10
        elif days_ago <= 7:  score += 8
        elif days_ago <= 14: score += 5
        elif days_ago <= 21: score += 2
        else:                score += 0
    except Exception:
        score += 4

    return score


def enrich_scores(trades: list[dict]) -> list[dict]:
    """
    Beräknar slutlig score med kontext från alla affärer.
    Lägger till bonus för:
    - Upprepade köp av samma person (visar övertygelse)
    - Flera insiders i samma bolag inom 14 dagar (klusterköp)
    """
    from collections import defaultdict

    # Räkna köp per person och per bolag+period
    person_counts: dict[str, int] = defaultdict(int)
    company_dates: dict[str, list] = defaultdict(list)

    for t in trades:
        key = (t.get("company",""), t.get("person",""))
        person_counts[key] += 1
        try:
            d = datetime.fromisoformat(t.get("trade_date","")[:10])
            company_dates[t.get("company","")].append(d)
        except Exception:
            pass

    for t in trades:
        score = calc_base_score(t)
        company = t.get("company", "")
        person_key = (company, t.get("person",""))
        bonuses = []

        # ── Bonus: upprepade köp av samma person (0–10p) ──
        repeat = person_counts[person_key]
        if repeat >= 3:
            score += 10
            bonuses.append(f"{repeat}x köp av samma person")
        elif repeat == 2:
            score += 5
            bonuses.append("2x köp av samma person")

        # ── Bonus: klusterköp — flera insiders i bolaget inom 14 dagar (0–15p) ──
        try:
            this_date = datetime.fromisoformat(t.get("trade_date","")[:10])
            nearby = [
                d for d in company_dates[company]
                if abs((d - this_date).days) <= 14
            ]
            unique_days = len(set(nearby))
            unique_persons = len({
                t2.get("person","") for t2 in trades
                if t2.get("company","") == company
                and t2.get("person","") != t.get("person","")
                and abs((datetime.fromisoformat(t2.get("trade_date","")[:10]) - this_date).days) <= 14
            })
            if unique_persons >= 3:
                score += 15
            elif unique_persons == 2:
                score += 8
            elif unique_persons == 1:
                score += 4
        except Exception:
            pass

        t["score"] = min(score, 100)
        t["score_detail"] = " · ".join(bonuses) if bonuses else ""

    return trades


# ── Scraper ────────────────────────────────────────────────────────────────────
def fetch_page(session: requests.Session, params: dict, page: int) -> bytes | None:
    """Hämtar en sida med exponentiell backoff vid fel."""
    params = {**params, "page": page}
    delay = BASE_DELAY

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            return resp.content
        except requests.RequestException as e:
            if attempt == MAX_RETRIES:
                print(f"  Sida {page}: ger upp efter {MAX_RETRIES} försök. ({e})")
                return None
            print(f"  Sida {page}: fel ({e}), väntar {delay:.0f}s och försöker igen ({attempt}/{MAX_RETRIES})...")
            time.sleep(delay)
            delay *= 2  # Exponentiell backoff: 1s → 2s → 4s → 8s

    return None


def parse_row(cells: list[str], fi_url: str = "") -> dict | None:
    """
    Kolumnordning FI:
    0: Publiceringsdatum, 1: Emittent, 2: Person, 3: Befattning,
    4: Närstående, 5: Karaktär, 6: Instrumentnamn, 7: Instrumenttyp,
    8: ISIN, 9: Transaktionsdatum, 10: Volym, 11: Volymsenhet,
    12: Pris, 13: Valuta
    """
    try:
        if cells[5] != "Förvärv":
            return None

        amount_sek = parse_amount(cells[10], cells[12])
        if amount_sek < THRESHOLD_SEK:
            return None

        # Unikt ID baserat på anmälnings-URL (förhindrar dubbletter från samma anmälan)
        trade_id = fi_url.split("/")[-1].split("?")[0] if fi_url else ""

        return {
            "id":              trade_id,
            "published":       cells[0],
            "company":         cells[1],
            "person":          cells[2],
            "role":            cells[3],
            "instrument":      cells[6],
            "instrument_type": cells[7],
            "isin":            cells[8].strip(),
            "trade_type":      cells[5],
            "volume":          cells[10],
            "price":           cells[12],
            "currency":        cells[13],
            "amount_sek":      amount_sek,
            "trade_date":      cells[9],
            "fi_url":          fi_url,
            "score":           0,
        }
    except (IndexError, ValueError) as e:
        print(f"  Parse-fel: {e} | {cells}")
        return None


def fetch_fi_trades(days_back: int) -> list[dict]:
    """Hämtar i veckovisa batcher för att undvika att FI kopplar ner oss."""
    all_trades = []
    today = datetime.now()

    # Dela upp perioden i 7-dagars batcher
    chunk = 7
    for offset in range(0, days_back, chunk):
        date_to   = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        date_from = (today - timedelta(days=min(offset + chunk, days_back))).strftime("%Y-%m-%d")
        print(f"  Batch: {date_from} → {date_to}")
        batch = fetch_batch(date_from, date_to)
        all_trades.extend(batch)
        if offset + chunk < days_back:
            time.sleep(2)  # Paus mellan batcher

    return all_trades


def fetch_batch(date_from: str, date_to: str) -> list[dict]:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    })

    # Hämta cookies från startsidan
    try:
        session.get(
            "https://marknadssok.fi.se/Publiceringsklient/sv-SE/Search/Start/Insyn",
            timeout=15
        )
        time.sleep(1)
    except Exception:
        pass

    params = {
        "button":             "search",
        "SearchFunctionType": "Insyn",
        "language":           "sv-se",
        "Transaktionsdatum.From": date_from,
        "Transaktionsdatum.To":   date_to,
        "paging": "True",
    }

    trades = []
    page   = 1

    while True:
        print(f"  Hämtar sida {page}...")

        content = fetch_page(session, params, page)
        if content is None:
            break

        soup = BeautifulSoup(content, "html.parser")
        rows = soup.select("table tbody tr")

        if not rows:
            print(f"  Inga fler rader, avslutar.")
            break

        found_on_page = 0
        for row in rows:
            cells  = [td.get_text(strip=True) for td in row.find_all("td")]
            link_tag = row.select_one("td a[href]")
            fi_url   = (FI_BASE + link_tag["href"]) if link_tag else ""

            if len(cells) < 14:
                continue
            trade = parse_row(cells, fi_url)
            if trade:
                trades.append(trade)
                found_on_page += 1

        print(f"    → {found_on_page} affärer över tröskel")

        has_next = (
            soup.find("a", string=lambda t: t and "Nästa" in t) or
            soup.find("a", href=lambda h: h and f"page={page+1}" in h)
        )
        if not has_next:
            print(f"  Klar efter sida {page}.")
            break

        page += 1
        time.sleep(BASE_DELAY)

    return trades


# ── Spara data ─────────────────────────────────────────────────────────────────
def save(trades: list[dict], existing: dict) -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    old_trades = existing.get("trades", [])

    # Deduplicera på anmälnings-ID (primärt) eller company+person+datum+volym (fallback)
    seen_ids  = {t["id"] for t in old_trades if t.get("id")}
    seen_keys = {
        (t["company"], t["person"], t["trade_date"], t["volume"])
        for t in old_trades
    }

    new_trades = []
    for t in trades:
        tid = t.get("id", "")
        key = (t["company"], t["person"], t["trade_date"], t["volume"])
        if tid and tid in seen_ids:
            continue
        if key in seen_keys:
            continue
        new_trades.append(t)
        if tid:
            seen_ids.add(tid)
        seen_keys.add(key)

    all_trades = new_trades + old_trades
    all_trades.sort(key=lambda t: t.get("trade_date", ""), reverse=True)

    output = {
        "updated":       datetime.now().isoformat(),
        "threshold_sek": THRESHOLD_SEK,
        "total":         len(all_trades),
        "trades":        all_trades,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✓ Sparade {len(new_trades)} nya affärer ({len(all_trades)} totalt) → {OUTPUT_FILE}")


# ── Main ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"=== FI Insider Scraper · {datetime.now():%Y-%m-%d %H:%M} ===\n")

    existing  = load_existing()
    days_back = days_back_needed(existing)

    print(f"\n1/3 Hämtar data från FI (senaste {days_back} dagarna)...")
    trades = fetch_fi_trades(days_back)
    print(f"    → {len(trades)} affärer totalt hittades.\n")

    print("2/3 Beräknar scores...")
    # Slå ihop med befintliga för att klusterköp ska fungera över tid
    all_for_scoring = trades + existing.get("trades", [])
    all_for_scoring = enrich_scores(all_for_scoring)
    # Plocka ut bara de nya med uppdaterade scores
    new_ids = {(t["company"], t["person"], t["trade_date"], t["volume"]) for t in trades}
    trades = [t for t in all_for_scoring if (t["company"], t["person"], t["trade_date"], t["volume"]) in new_ids]
    print(f"    → Scores beräknade.\n")

    print("3/3 Sparar data...")
    save(trades, existing)
    print("\nKlart!")
