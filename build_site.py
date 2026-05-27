"""
build_site.py
Genererar docs/index.html från data/trades.json.
"""

import json
from pathlib import Path
from datetime import datetime

DATA_FILE = Path("data/trades.json")
OUT_FILE  = Path("docs/index.html")


def fmt_sek(amount: float) -> str:
    if amount >= 1_000_000:
        return f"{amount/1_000_000:.1f} Mkr"
    return f"{amount:,.0f} kr"


def fmt_date(s: str) -> str:
    try:
        return datetime.fromisoformat(s[:10]).strftime("%d %b %Y")
    except Exception:
        return s


def role_badge(role: str) -> str:
    role_lower = role.lower()
    if any(k in role_lower for k in ["verkställande direktör", "vd", "ceo"]):
        return '<span class="badge badge-ceo">VD</span>'
    if any(k in role_lower for k in ["ordförande", "chairman"]):
        return '<span class="badge badge-chair">Ordf.</span>'
    if any(k in role_lower for k in ["styrelseledamot", "styrelse"]):
        return '<span class="badge badge-board">Styrelse</span>'
    if any(k in role_lower for k in ["ekonomichef", "cfo", "finanschef"]):
        return '<span class="badge badge-cfo">CFO</span>'
    return f'<span class="badge badge-other">{role[:18]}</span>'


def score_bar(score: int) -> str:
    if score >= 75:
        color = "#4ade80"
        label = "Stark"
    elif score >= 50:
        color = "#fbbf24"
        label = "Medel"
    else:
        color = "#f87171"
        label = "Svag"
    return f'''<div class="score-wrap">
      <div class="score-num" style="color:{color}">{score}</div>
      <div class="score-bar-bg">
        <div class="score-bar-fill" style="width:{score}%;background:{color}"></div>
      </div>
      <div class="score-label" style="color:{color}">{label}</div>
    </div>'''


def build_rows(trades: list[dict]) -> str:
    if not trades:
        return '<tr><td colspan="7" class="empty">Inga affärer hittades.</td></tr>'

    rows = []
    for t in trades:
        fi_url      = t.get("fi_url", "")
        nordnet_url = t.get("nordnet_url", "")
        isin        = t.get("isin", "")
        score       = t.get("score", 0)
        instrument  = t.get("instrument", "")

        company = t.get("company", "")
        nordnet_url = f"https://www.nordnet.se/marknaden/aktiekurser?searchText={company.replace(' ', '+').replace('/', '%2F')}"
        nordnet_cell = f'<a href="{nordnet_url}" target="_blank" class="company-link">{instrument}</a><br><span class="isin">{isin}</span>' if isin else f'<a href="{nordnet_url}" target="_blank" class="company-link">{instrument}</a>'

        # FI-länk
        fi_link = f'<a href="{fi_url}" target="_blank" class="fi-link">FI ↗</a>' if fi_url else "–"

        rows.append(f"""
    <tr>
      <td class="td-date">{fmt_date(t.get("trade_date",""))}</td>
      <td class="td-company"><strong>{t.get("company","")}</strong></td>
      <td class="td-person">
        {t.get("person","")}
        <br>{role_badge(t.get("role",""))}
      </td>
      <td class="td-amount">{fmt_sek(t.get("amount_sek",0))}</td>
      <td class="td-instrument">{nordnet_cell}</td>
      <td class="td-score">{score_bar(score)}</td>
      <td class="td-fi">{fi_link}</td>
    </tr>""")
    return "\n".join(rows)


def build_html(data: dict) -> str:
    trades    = data.get("trades", [])
    updated   = fmt_date(data.get("updated", "")[:10])
    total     = data.get("total", 0)
    threshold = data.get("threshold_sek", 1_000_000)
    rows      = build_rows(trades)

    return f"""<!DOCTYPE html>
<html lang="sv">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FI Insiderhandel · Stora köp</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@300;400;500&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    :root {{
      --bg:        #0d0f0e;
      --surface:   #141614;
      --border:    #222422;
      --text:      #e8ebe8;
      --muted:     #6b736b;
      --accent:    #4ade80;
      --accent-dk: #16a34a;
      --red:       #f87171;
      --amber:     #fbbf24;
      --blue:      #60a5fa;
      --mono:      'IBM Plex Mono', monospace;
      --sans:      'IBM Plex Sans', sans-serif;
    }}

    body {{
      background: var(--bg);
      color: var(--text);
      font-family: var(--sans);
      font-size: 14px;
      line-height: 1.6;
    }}

    header {{
      padding: 3rem 2rem 2rem;
      border-bottom: 1px solid var(--border);
      max-width: 1400px;
      margin: 0 auto;
    }}
    .header-top {{
      display: flex;
      align-items: baseline;
      gap: 1rem;
      margin-bottom: 0.5rem;
    }}
    h1 {{
      font-family: var(--mono);
      font-size: 1.1rem;
      font-weight: 500;
      color: var(--accent);
      letter-spacing: 0.05em;
    }}
    .header-sub {{
      font-size: 0.8rem;
      color: var(--muted);
      font-family: var(--mono);
    }}
    .meta {{
      display: flex;
      gap: 2rem;
      margin-top: 1rem;
      font-family: var(--mono);
      font-size: 0.75rem;
      color: var(--muted);
    }}
    .meta span {{ color: var(--text); }}

    .filterbar {{
      max-width: 1400px;
      margin: 1.5rem auto 0;
      padding: 0 2rem;
      display: flex;
      gap: 0.75rem;
      flex-wrap: wrap;
      align-items: center;
    }}
    .filterbar input, .filterbar select {{
      background: var(--surface);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 0.4rem 0.75rem;
      font-family: var(--mono);
      font-size: 0.75rem;
      border-radius: 3px;
      outline: none;
    }}
    .filterbar input:focus, .filterbar select:focus {{
      border-color: var(--accent-dk);
    }}
    .filterbar input {{ width: 220px; }}
    .count {{
      margin-left: auto;
      font-family: var(--mono);
      font-size: 0.75rem;
      color: var(--muted);
    }}

    .table-wrap {{
      max-width: 1400px;
      margin: 1.5rem auto 4rem;
      padding: 0 2rem;
      overflow-x: auto;
    }}
    table {{ width: 100%; border-collapse: collapse; }}
    thead th {{
      font-family: var(--mono);
      font-size: 0.7rem;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      padding: 0.6rem 1rem;
      text-align: left;
      border-bottom: 1px solid var(--border);
      white-space: nowrap;
      cursor: pointer;
      user-select: none;
    }}
    thead th:hover {{ color: var(--text); }}
    thead th.sorted {{ color: var(--accent); }}
    tbody tr {{ border-bottom: 1px solid var(--border); transition: background 0.1s; }}
    tbody tr:hover {{ background: var(--surface); }}
    td {{ padding: 0.9rem 1rem; vertical-align: middle; }}

    .td-date       {{ font-family: var(--mono); font-size: 0.75rem; color: var(--muted); white-space: nowrap; }}
    .td-company    {{ font-size: 0.9rem; }}
    .td-company strong {{ font-weight: 500; }}
    .td-person     {{ font-size: 0.85rem; }}
    .td-amount     {{ font-family: var(--mono); font-weight: 500; color: var(--accent); white-space: nowrap; font-size: 0.9rem; }}
    .td-instrument {{ font-size: 0.8rem; }}
    .company-link {{ color: var(--blue); text-decoration: none; font-size: 0.85rem; }}
    .company-link:hover {{ text-decoration: underline; }}
    .td-score      {{ min-width: 110px; }}
    .td-fi         {{ font-family: var(--mono); font-size: 0.75rem; }}

    .nordnet-link {{
      color: var(--blue);
      text-decoration: none;
      display: flex;
      flex-direction: column;
      gap: 2px;
    }}
    .nordnet-link:hover {{ text-decoration: underline; }}
    .isin {{
      font-family: var(--mono);
      font-size: 0.65rem;
      color: var(--muted);
    }}

    .fi-link {{
      color: var(--muted);
      text-decoration: none;
      font-size: 0.7rem;
    }}
    .fi-link:hover {{ color: var(--text); }}

    /* Score */
    .score-wrap {{
      display: flex;
      flex-direction: column;
      gap: 3px;
    }}
    .score-num {{
      font-family: var(--mono);
      font-size: 0.85rem;
      font-weight: 500;
    }}
    .score-bar-bg {{
      height: 3px;
      background: var(--border);
      border-radius: 2px;
      width: 80px;
    }}
    .score-bar-fill {{
      height: 3px;
      border-radius: 2px;
      transition: width 0.3s;
    }}
    .score-label {{
      font-family: var(--mono);
      font-size: 0.6rem;
      letter-spacing: 0.05em;
      text-transform: uppercase;
    }}

    /* Score-förklaring */
    .score-info {{
      max-width: 1400px;
      margin: 1rem auto 0;
      padding: 0 2rem;
      font-family: var(--mono);
      font-size: 0.7rem;
      color: var(--muted);
      display: flex;
      gap: 1.5rem;
      flex-wrap: wrap;
    }}
    .score-info span {{ display: flex; align-items: center; gap: 0.4rem; }}
    .dot {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block; }}

    .badge {{
      display: inline-block;
      padding: 0.15rem 0.5rem;
      border-radius: 2px;
      font-family: var(--mono);
      font-size: 0.65rem;
      font-weight: 500;
      letter-spacing: 0.04em;
      margin-top: 3px;
    }}
    .badge-ceo   {{ background: #14532d; color: #86efac; }}
    .badge-chair {{ background: #1e3a5f; color: #93c5fd; }}
    .badge-board {{ background: #1c2b4a; color: #7dd3fc; }}
    .badge-cfo   {{ background: #451a03; color: #fcd34d; }}
    .badge-other {{ background: #1c1c1c; color: var(--muted); }}

    .empty {{ text-align: center; color: var(--muted); padding: 3rem; font-family: var(--mono); }}

    footer {{
      text-align: center;
      padding: 2rem;
      font-family: var(--mono);
      font-size: 0.7rem;
      color: var(--muted);
      border-top: 1px solid var(--border);
    }}
    footer a {{ color: var(--accent-dk); text-decoration: none; }}
  </style>
</head>
<body>

<header>
  <div class="header-top">
    <h1>FI // INSIDERHANDEL</h1>
    <span class="header-sub">köp &gt; {threshold/1_000_000:.0f} Mkr · VD &amp; styrelse</span>
  </div>
  <p style="color:var(--muted);font-size:0.8rem;max-width:600px;">
    Automatisk bevakning av insynspersoners aktieköp registrerade hos
    Finansinspektionen. Score 0–100 baseras på belopp, roll, instrumenttyp och aktualitet.
  </p>
  <div class="meta">
    <div>Senast uppdaterad <span>{updated}</span></div>
    <div>Totalt <span>{total}</span> affärer</div>
    <div>Källa <span><a href="https://marknadssok.fi.se" target="_blank" style="color:var(--accent-dk)">marknadssok.fi.se</a></span></div>
  </div>
</header>

<div class="score-info">
  <span><span class="dot" style="background:#4ade80"></span> 75–100 Stark signal</span>
  <span><span class="dot" style="background:#fbbf24"></span> 50–74 Medel signal</span>
  <span><span class="dot" style="background:#f87171"></span> 0–49 Svag signal</span>
  <span style="margin-left:auto">Score baseras på: belopp · roll · instrumenttyp · aktualitet</span>
</div>

<div class="filterbar">
  <input type="text" id="search" placeholder="Sök bolag eller person...">
  <select id="role-filter">
    <option value="">Alla roller</option>
    <option value="vd">VD</option>
    <option value="styrelse">Styrelse</option>
    <option value="cfo">CFO</option>
  </select>
  <select id="score-filter">
    <option value="0">Alla scores</option>
    <option value="75">Stark (75+)</option>
    <option value="50">Medel (50+)</option>
  </select>
  <div class="count" id="count">{total} affärer</div>
</div>

<div class="table-wrap">
  <table id="main-table">
    <thead>
      <tr>
        <th data-col="0">Datum ↕</th>
        <th data-col="1">Bolag ↕</th>
        <th data-col="2">Person / Roll</th>
        <th data-col="3">Belopp ↕</th>
        <th data-col="4">Aktie / ISIN</th>
        <th data-col="5">Score ↕</th>
        <th>FI</th>
      </tr>
    </thead>
    <tbody id="tbody">
{rows}
    </tbody>
  </table>
</div>

<footer>
  Data från <a href="https://www.fi.se" target="_blank">Finansinspektionen</a> ·
  Aktier på <a href="https://www.nordnet.se" target="_blank">Nordnet</a> ·
  <a href="https://github.com/Gurkburkens/FI-Insider" target="_blank">Källkod på GitHub</a> ·
  Ej finansiell rådgivning ·
  Skapad av Gustav Engström
</footer>

<script>
  const search      = document.getElementById('search');
  const roleFilter  = document.getElementById('role-filter');
  const scoreFilter = document.getElementById('score-filter');
  const tbody       = document.getElementById('tbody');
  const countEl     = document.getElementById('count');
  const rows        = Array.from(tbody.querySelectorAll('tr'));

  function filter() {{
    const q      = search.value.toLowerCase();
    const r      = roleFilter.value.toLowerCase();
    const minScore = parseInt(scoreFilter.value) || 0;
    let visible  = 0;

    rows.forEach(row => {{
      const text  = row.textContent.toLowerCase();
      const scoreEl = row.querySelector('.score-num');
      const score = scoreEl ? parseInt(scoreEl.textContent) : 0;

      const matchQ = !q || text.includes(q);
      const matchR = !r || text.includes(r);
      const matchS = score >= minScore;

      row.style.display = matchQ && matchR && matchS ? '' : 'none';
      if (matchQ && matchR && matchS) visible++;
    }});
    countEl.textContent = visible + ' affärer';
  }}

  search.addEventListener('input', filter);
  roleFilter.addEventListener('change', filter);
  scoreFilter.addEventListener('change', filter);

  // Sortering
  let sortCol = -1, sortAsc = true;
  document.querySelectorAll('thead th[data-col]').forEach(th => {{
    th.addEventListener('click', () => {{
      const col = +th.dataset.col;
      if (sortCol === col) sortAsc = !sortAsc;
      else {{ sortCol = col; sortAsc = true; }}
      document.querySelectorAll('thead th').forEach(h => h.classList.remove('sorted'));
      th.classList.add('sorted');
      const sorted = [...rows].sort((a, b) => {{
        const av = a.cells[col]?.textContent.trim() || '';
        const bv = b.cells[col]?.textContent.trim() || '';
        if (col === 3) {{
          const toNum = s => parseFloat(s.replace(/[^0-9,.]/g,'').replace(',','.')) || 0;
          return sortAsc ? toNum(av) - toNum(bv) : toNum(bv) - toNum(av);
        }}
        if (col === 5) {{
          const toNum = s => parseInt(s) || 0;
          return sortAsc ? toNum(av) - toNum(bv) : toNum(bv) - toNum(av);
        }}
        return sortAsc ? av.localeCompare(bv,'sv') : bv.localeCompare(av,'sv');
      }});
      sorted.forEach(r => tbody.appendChild(r));
    }});
  }});
</script>
</body>
</html>"""


def main():
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    if DATA_FILE.exists():
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
    else:
        print("Ingen datafil hittad – genererar tom sida.")
        data = {"trades": [], "updated": datetime.now().isoformat(),
                "total": 0, "threshold_sek": 1_000_000}

    html = build_html(data)
    OUT_FILE.write_text(html, encoding="utf-8")
    print(f"✓ Byggde {OUT_FILE} ({len(data.get('trades',[]))} rader)")


if __name__ == "__main__":
    main()
