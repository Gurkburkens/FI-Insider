"""
FI Insiderhandel Scraper
Hämtar insynshandel från Finansinspektionens publiceringsklient,
filtrerar köp (Förvärv) över 1 miljon kr.
Berikar med ticker-symbol från Avanzas öppna sök-API.
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
DAYS_BACK      = 30
OUTPUT_FILE    = Path("data/trades.json")
BASE_URL       = "https://marknadssok.fi.se/Publiceringsklient/sv-SE/Search/Search/Insyn"
FI_BASE        = "https://marknadssok.fi.se"


# ── Ticker-lookup via Avanza ───────────────────────────────────────────────────
_ticker_cache: dict = {}

def lookup_ticker(isin: str, session: requests.Session) -> dict:
    """Slår upp ticker-symbol och börs från Avanzas öppna sök-API."""
    if not isin:
        return {}
    if isin in _ticker_cache:
        return _ticker_cache[isin]

    try:
        r = session.get(
            "https://www.avanza.se/ab/sok/inline",
            params={"query": isin},
            timeout=8,
        )
        if r.status_code == 200:
            data = r.json()
            # Avanza returnerar en lista med träffar
            hits = data if isinstance(data, list) else data.get("hits", {}).get("hits", [])
            for hit in hits[:3]:
                src = hit.get("_source", hit)
                hit_isin = src.get("isin", src.get("ISIN", ""))
                if hit_isin == isin:
                    result = {
                        "ticker":   src.get("tickerSymbol", src.get("ticker", "")),
                        "exchange": src.get("listName", src.get("exchange", "")),
                        "name":     src.get("name", ""),
                    }
                    _ticker_cache[isin] = result
                    return result
    except Exception as e:
        pass

    _ticker_cache[isin] = {}
    return {}


# ── Score-beräkning ────────────────────────────────────────────────────────────
def calc_score(trade: dict) -> int:
    score = 0

    # Belopp (0–35p)
    amount = trade.get("amount_sek", 0)
    if   amount >= 50_000_000: score += 35
    elif amount >= 20_000_000: score += 30
    elif amount >= 10_000_000: score += 25
    elif amount >=  5_000_000: score += 20
    elif amount >=  3_000_000: score += 15
    elif amount >=  2_000_000: score += 10
    else:                      score +=  5

    # Roll (0–30p)
    role = trade.get("role", "").lower()
    if any(k in role for k in ["verkställande direktör", "vd", "ceo"]):
        score += 30
    elif any(k in role for k in ["ordförande", "chairman"]):
        score += 25
    elif any(k in role for k in ["styrelseledamot", "styrelse"]):
        score += 20
    elif any(k in role for k in ["ekonomichef", "cfo", "finanschef"]):
        score += 15
    elif any(k in role for k in ["ledande", "befattning"]):
        score += 10
    else:
        score += 5

    # Instrumenttyp (0–20p)
    itype = trade.get("instrument_type", "").lower()
    if "aktie" in itype:
        score += 20
    elif "teckningsoption" in itype or "warrant" in itype:
        score += 8
    else:
        score += 12

    # Färskhet (0–15p)
    try:
        trade_date = datetime.fromisoformat(trade.get("trade_date", "")[:10])
        days_ago = (datetime.now() - trade_date).days
        if   days_ago <= 3:  score += 15
        elif days_ago <= 7:  score += 12
        elif days_ago <= 14: score += 8
        elif days_ago <= 21: score += 4
        else:                score += 1
    except Exception:
        score += 5

    return min(score, 100)


# ── Scraper ────────────────────────────────────────────────────────────────────
def fetch_fi_trades(days_back: int = DAYS_BACK) -> list[dict]:
    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    date_to   = datetime.now().strftime("%Y-%m-%d")

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    })

    try:
        session.get("https://marknadssok.fi.se/Publiceringsklient/sv-SE/Search/Start/Insyn", timeout=15)
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
        "page":   1,
    }

    trades = []
    page   = 1

    while True:
        params["page"] = page
        print(f"  Hämtar sida {page}...")

        try:
            resp = session.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  Fel vid hämtning: {e}")
            time.sleep(3)
            try:
                resp = session.get(BASE_URL, params=params, timeout=30)
                resp.raise_for_status()
            except Exception:
                print(f"  Ger upp efter retry.")
                break

        soup = BeautifulSoup(resp.text, "html.parser")
        rows = soup.select("table tbody tr")

        if not rows:
            print(f"  Inga fler rader på sida {page}, avslutar.")
            break

        found_on_page = 0
        for row in rows:
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            link_tag = row.select_one("td a[href]")
            fi_url = (FI_BASE + link_tag["href"]) if link_tag else ""

            if len(cells) < 14:
                continue
            trade = parse_row(cells, fi_url)
            if trade:
                # Hämta ticker från Avanza
                if trade["isin"]:
                    ticker_info = lookup_ticker(trade["isin"], session)
                    trade["ticker"]   = ticker_info.get("ticker", "")
                    trade["exchange"] = ticker_info.get("exchange", "")
                    time.sleep(0.2)

                trade["score"] = calc_score(trade)
                trades.append(trade)
                found_on_page += 1

        print(f"    → {found_on_page} affärer över tröskel på sida {page}")

        has_next = (
            soup.find("a", string=lambda t: t and "Nästa" in t) or
            soup.find("a", rel="next") or
            soup.find("a", title=lambda t: t and f"Sida {page+1}" in t) or
            soup.find("a", href=lambda h: h and f"page={page+1}" in h)
        )

        if not has_next:
            print(f"  Ingen nästa-sida, avslutar efter sida {page}.")
            break

        page += 1
        time.sleep(0.8)

    return trades


def parse_row(cells: list[str], fi_url: str = "") -> dict | None:
    try:
        if cells[5] != "Förvärv":
            return None

        amount_sek = parse_amount(cells[10], cells[12])
        if amount_sek < THRESHOLD_SEK:
            return None

        isin = cells[8].strip()

        return {
            "published":       cells[0],
            "company":         cells[1],
            "person":          cells[2],
            "role":            cells[3],
            "instrument":      cells[6],
            "instrument_type": cells[7],
            "isin":            isin,
            "ticker":          "",
            "exchange":        "",
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


def parse_amount(volume_str: str, price_str: str) -> float:
    def clean(s: str) -> float:
        s = s.replace("\xa0", "").replace("\u202f", "").replace(" ", "")
        s = s.replace(",", ".")
        s = re.sub(r"[^\d\.]", "", s)
        return float(s) if s else 0.0
    return clean(volume_str) * clean(price_str)


# ── Spara data ─────────────────────────────────────────────────────────────────
def save(trades: list[dict]) -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    existing = []
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE) as f:
            existing = json.load(f).get("trades", [])

    seen = {
        (t["company"], t["person"], t["trade_date"], t["volume"])
        for t in existing
    }
    new_trades = [
        t for t in trades
        if (t["company"], t["person"], t["trade_date"], t["volume"]) not in seen
    ]

    all_trades = new_trades + existing
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
    print(f"=== FI Insider Scraper · {datetime.now():%Y-%m-%d %H:%M} ===")
    print(f"Tröskel: {THRESHOLD_SEK:,} SEK · Senaste {DAYS_BACK} dagarna\n")

    print("1/2 Hämtar data från FI...")
    trades = fetch_fi_trades()
    print(f"    → {len(trades)} affärer totalt hittades.\n")

    print("2/2 Sparar data...")
    save(trades)
    print("\nKlart!")
