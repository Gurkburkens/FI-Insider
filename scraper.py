"""
FI Insiderhandel Scraper
Hämtar insynshandel från Finansinspektionens register
och filtrerar köp över 1 miljon kr.
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
FI_SEARCH_URL  = "https://marknadssok.fi.se/Publikt/sv/Search/Search"


# ── FI-scraper ─────────────────────────────────────────────────────────────────
def fetch_fi_trades(days_back: int = DAYS_BACK) -> list[dict]:
    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    date_to   = datetime.now().strftime("%Y-%m-%d")

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; FI-Insider-Tracker/1.0)"
    })

    params = {
        "SearchFunctionType": "Insyn",
        "Transaktionsdatum.From": date_from,
        "Transaktionsdatum.To": date_to,
        "Transaktionstyp": "Förvärv",
        "button": "search",
        "Page": 1,
    }

    trades = []
    page   = 1

    while True:
        params["Page"] = page
        print(f"  Hämtar sida {page}...")

        try:
            resp = session.get(FI_SEARCH_URL, params=params, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  Fel vid hämtning: {e}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        rows = soup.select("table.table tbody tr")

        if not rows:
            print(f"  Inga fler rader på sida {page}.")
            break

        for row in rows:
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) < 9:
                continue
            trade = parse_row(cells)
            if trade:
                trades.append(trade)

        next_btn = soup.select_one("a[rel='next']")
        if not next_btn:
            break

        page += 1
        time.sleep(0.5)

    return trades


def parse_row(cells: list[str]) -> dict | None:
    """Tolkar en tabellrad från FI:s sökresultat.
    Kolumnordning: 0=Publiceringsdatum, 1=Bolag, 2=LEI, 3=Person, 4=Befattning,
    5=Närstående, 6=Instrument, 7=ISIN, 8=Transaktionstyp,
    9=Volym, 10=Pris, 11=Valuta, 12=Handelsplats, 13=Datum
    """
    try:
        amount_sek = parse_amount(cells[9], cells[10])
        if amount_sek < THRESHOLD_SEK:
            return None

        return {
            "published":  cells[0],
            "company":    cells[1],
            "person":     cells[3],
            "role":       cells[4],
            "instrument": cells[6],
            "trade_type": cells[8],
            "volume":     cells[9],
            "price":      cells[10],
            "currency":   cells[11] if len(cells) > 11 else "SEK",
            "amount_sek": amount_sek,
            "trade_date": cells[13] if len(cells) > 13 else cells[0],
        }
    except (IndexError, ValueError) as e:
        print(f"  Parse-fel: {e} | {cells}")
        return None


def parse_amount(volume_str: str, price_str: str) -> float:
    def clean(s: str) -> float:
        s = re.sub(r"[^\d,\.]", "", s).replace(",", ".")
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
    print(f"    → {len(trades)} affärer över tröskel hittades.\n")

    print("2/2 Sparar data...")
    save(trades)
    print("\nKlart!")
