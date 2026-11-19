"""US stock ideas for the long-term reports: masters' picks and ten-bagger candidates (docs/longterm-master-dca-ideas-plan.md 4,
S3; user approval 2026-11-18).

Reference only. Nothing here orders, books or feeds the engine: the long-term engine trades six KRX ETFs and nothing else.
domestic_etfs() names the engine ETFs a pick is held through; the 적립식 tab lets the user add those ETFs (never the US
stock) to its picks (user approval 2026-11-18, docs/longterm-account-settings-plan.md 3-A).
Numbers come from code (SEC filings and prices); the weekly report's lens step (S4) writes the words and thesis cards.

  --universe  quarterly  holdings of the US ETFs behind our KRX ETFs (SEC N-PORT), SEC company facts of every holding
                         (TTM, five yearly points), and three masters' 13F (Berkshire, Bridgewater, Oaktree)
  --weekly    Friday     prices (Yahoo) for the names that pass the fundamentals, the four screens, the picks and the
                         follow-up of past picks against QQQ and SPY (추천 성적표)
  --status               what the dashboard tab reads, as JSON
  --scheduled            launchd us-ideas (Fridays 07:00 and 07:30): the universe then the screen on the third Friday of
                         February, May, August and November at 07:00 (13F due 45 days after the quarter), the screen at
                         07:30 unless it already ran that day

The screens are hypotheses (docs 4-3·4-4) checked by the follow-up and S5. Market cap is price x the latest diluted shares
(a split after the last filing makes it wrong by the split factor). SPY and QQQ are unit trusts without N-PORT, so IVV and
QQQM stand for the S&P 500 and the Nasdaq-100. Holdings without a ticker are matched to SEC tickers by company name.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import statistics
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')

KST = ZoneInfo('Asia/Seoul')
ROOT = Path.home() / '.vibe-trading/nhplug/vibe-longterm'
# US ETF: (Korean label, our KRX ETF code or None). The KRX ETFs track similar but not identical indexes.
ETFS = {'IVV': ('S&P500', '379800'), 'QQQM': ('나스닥100', '379810'), 'SOXX': ('반도체', '381180'),
        'QTUM': ('양자컴퓨팅', '0023A0'), 'UFO': ('우주', '0183J0'), 'IWO': ('러셀2000 성장', None)}
LARGE = ('IVV', 'QQQM', 'SOXX', 'QTUM', 'UFO')
# look-alike US ETFs of the engine's US proxies (vibe_longterm.ASSETS[k][4]: QQQ, SPY, SOXX, QTUM, UFO), user approval 2026-11-18
ALIASES = {'IVV': 'SPY', 'VOO': 'SPY', 'QQQM': 'QQQ', 'SMH': 'SOXX'}
MANAGERS = {'Berkshire Hathaway': ('워렌 버핏', '버크셔 해서웨이'), 'Bridgewater Associates': ('레이 달리오', '브리지워터'),
            'Oaktree Capital Management': ('하워드 막스', '오크트리')}
MASTERS = {'buffett': '워렌 버핏 · 품질 프랜차이즈', 'lynch': '피터 린치 · 성장 대비 가격(PEG)',
           'fisher': '필립 피셔 · 성장의 질', 'tenbagger': '텐베거 후보 · 린치의 빠른 성장주'}
FIELDS = ('revenue', 'gross_profit', 'operating_income', 'net_income', 'cfo', 'capex', 'shares_diluted', 'total_debt',
          'total_equity', 'cash')
ANNUAL = ('cfo', 'capex')  # cash-flow statements are year-to-date in 10-Qs: the loader's TTM comes back empty, so fiscal years
YEARS = 5      # yearly points, oldest first: t-4y .. t
TOP = 10
HORIZONS = (1, 3, 6, 12)  # months after a pick
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (day TEXT, kind TEXT, at TEXT, info TEXT, PRIMARY KEY (day, kind));
CREATE TABLE IF NOT EXISTS universe (day TEXT, ticker TEXT, cik TEXT, name TEXT, etf TEXT, pct REAL, PRIMARY KEY (day, ticker, etf));
CREATE TABLE IF NOT EXISTS facts (day TEXT, ticker TEXT, data TEXT, PRIMARY KEY (day, ticker));
CREATE TABLE IF NOT EXISTS picks (day TEXT, master TEXT, rank INTEGER, ticker TEXT, name TEXT, price REAL, price_date TEXT,
    score REAL, metrics TEXT, reasons TEXT, etfs TEXT, PRIMARY KEY (day, master, ticker));
CREATE TABLE IF NOT EXISTS outcomes (day TEXT, master TEXT, ticker TEXT, months INTEGER, ret REAL, qqq REAL, spy REAL,
    PRIMARY KEY (day, master, ticker, months));
CREATE TABLE IF NOT EXISTS theses (day TEXT, ticker TEXT, master TEXT, card TEXT, PRIMARY KEY (day, ticker, master));
CREATE TABLE IF NOT EXISTS managers13f (quarter TEXT, manager TEXT, data TEXT, PRIMARY KEY (quarter, manager));
"""


def connect(root=ROOT):
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'ideas.sqlite3')
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


# ---------- names -> SEC tickers ----------

_STOP = re.compile(r'\b(inc|incorporated|corp|corporation|co|cos|company|companies|ltd|limited|plc|holdings?|group|sa|nv|ag|se|the|'
                   r'class [a-z]|cl [a-z]|new|lp|llc|trust|reit)\b')


def norm(name):
    """A company name reduced for matching: 'C. H. Robinson Worldwide, Inc.' and 'C.H. ROBINSON WORLDWIDE INC /DE/' agree."""
    text = re.sub(r'/[a-z]{2,3}/?', ' ', (name or '').lower().replace('&', ' and '))
    text = _STOP.sub(' ', re.sub(r'[^a-z0-9 ]', ' ', text))
    return re.sub(r'\s+', '', text)


def ticker_index(payload):
    """{normalized name: (ticker, cik)} from SEC company_tickers.json; the first listing of a company wins (its main class)."""
    out = {}
    for row in payload.values():
        out.setdefault(norm(row['title']), (row['ticker'].upper(), f"{int(row['cik_str']):010d}"))
    return out


def match(holdings, index, by_ticker):
    """[(ticker, cik, name, pct)] for equity holdings; a holding is kept when its own ticker or its name is known to SEC."""
    rows, missed = [], []
    for h in holdings:
        if h.get('asset_category') != 'EC':
            continue
        ticker = (h.get('ticker') or '').upper().replace('.', '-')
        hit = (ticker, by_ticker[ticker]) if ticker in by_ticker else index.get(norm(h.get('name')))
        if hit:
            rows.append((hit[0], hit[1], h.get('name') or hit[0], h.get('pct_of_net_assets')))
        else:
            missed.append(h.get('name'))
    return rows, missed


# ---------- facts -> metrics -> screens (pure) ----------

def _div(a, b):
    return a / b if a is not None and b not in (None, 0) and not (isinstance(b, float) and math.isnan(b)) else None


def _num(v):
    return v if isinstance(v, (int, float)) and math.isfinite(v) else None


def metrics(facts, price=None, closes=None):
    """Ratios from five yearly points of TTM facts (oldest first) and, when given, the last price and a year of closes."""
    f = {k: [_num(v) for v in (facts.get(k) or [None] * YEARS)] for k in FIELDS}
    rev, ni, oi, gp, eq = f['revenue'], f['net_income'], f['operating_income'], f['gross_profit'], f['total_equity']
    last = lambda k: f[k][-1]
    m = {'revenue': last('revenue'), 'net_income': last('net_income')}
    m['rev_growth'] = _div(rev[-1], rev[-2]) - 1 if _div(rev[-1], rev[-2]) else None
    m['rev_cagr3'] = (rev[-1] / rev[-4]) ** (1 / 3) - 1 if rev[-1] and rev[-4] and rev[-1] > 0 and rev[-4] > 0 else None
    m['ni_growth'] = ni[-1] / ni[-2] - 1 if ni[-1] is not None and ni[-2] and ni[-2] > 0 else None
    m['gm'] = [_div(g, r) for g, r in zip(gp, rev)]
    m['om'] = [_div(o, r) for o, r in zip(oi, rev)]
    m['roe'] = [_div(n, e) if e and e > 0 else None for n, e in zip(ni, eq)]
    m['de'] = _div(last('total_debt') or 0, eq[-1]) if eq[-1] and eq[-1] > 0 else None
    capex = abs(last('capex') or 0)
    m['fcf'] = last('cfo') - capex if last('cfo') is not None else None
    m['cfo_up'] = f['cfo'][-1] is not None and f['cfo'][-2] is not None and f['cfo'][-1] > f['cfo'][-2]
    ratio = _div(f['shares_diluted'][-1], f['shares_diluted'][-2])
    m['dilution'] = ratio - 1 if ratio and 0.67 < ratio < 1.5 else None  # a jump this size is a split, not dilution
    debt = f['total_debt']
    m['debt_growth'] = debt[-1] / debt[-2] - 1 if debt[-1] is not None and debt[-2] and debt[-2] > 0 else None
    m['runway_years'] = _div(last('cash'), -m['fcf']) if m['fcf'] is not None and m['fcf'] < 0 else None
    shares = f['shares_diluted'][-1]
    if price and shares:
        m['mcap'] = price * shares
        m['pe'] = m['mcap'] / ni[-1] if ni[-1] and ni[-1] > 0 else None
        m['peg'] = m['pe'] / (m['ni_growth'] * 100) if m['pe'] and m['ni_growth'] and m['ni_growth'] > 0 else None
        m['fcf_yield'] = _div(m['fcf'], m['mcap'])
    if closes and len(closes) >= 200:
        year = closes[-252:]
        m['above200'] = closes[-1] > sum(closes[-200:]) / 200
        m['pos52'] = (closes[-1] - min(year)) / (max(year) - min(year)) if max(year) > min(year) else None
    return m


pct = lambda v: f'{v * 100:.0f}%' if v is not None else '—'  # noqa: E731


def suspect(m):
    """A Korean reason when the numbers cannot be right (a share class or unit mismatch, a financial firm's customer cash in
    its operating cash flow, cost of revenue missing): such a name is left out, never ranked first."""
    if m.get('fcf_yield') is not None and m['fcf_yield'] > 0.25:
        return f"FCF 수익률 {m['fcf_yield'] * 100:,.0f}%"
    if m.get('pe') is not None and m['pe'] < 3:
        return f"PER {m['pe']:.1f}"
    if (m['gm'][-1] or 0) > 0.97:
        return f"총이익률 {pct(m['gm'][-1])}"
    return None


def criteria(master, m, etfs):
    """[(criterion, the name's value, passed)] of one screen: the thresholds live here only (screen() decides by these rows;
    the dashboard shows them). Thresholds are docs 4-3·4-4 hypotheses; a missing value fails as '자료 없음'."""
    v = lambda x, f: '자료 없음' if x is None else f(x)  # noqa: E731
    ok = lambda x, test: x is not None and test(x)  # noqa: E731
    large = bool(set(etfs) & set(LARGE))
    if master == 'buffett':
        roe = [r for r in m['roe'] if r is not None]
        gm = [g for g in m['gm'] if g is not None]
        spread = statistics.pstdev(gm) if len(gm) >= 3 else None
        return [('S&P500(IVV) 소속', '소속' if 'IVV' in etfs else '아님', 'IVV' in etfs),
                ('ROE 15% 이상(4년 이상 자료, 최저값)', v(min(roe) if len(roe) >= 4 else None, lambda x: f'최저 {pct(x)} · {len(roe)}년'),
                 len(roe) >= 4 and min(roe) >= 0.15),
                ('부채/자본 1.0 이하', v(m['de'], lambda x: f'{x:.2f}'), ok(m['de'], lambda x: x <= 1.0)),
                ('잉여현금흐름 플러스', v(m['fcf'], lambda x: f'{x / 1e6:,.0f}백만 달러'), ok(m['fcf'], lambda x: x > 0)),
                ('총이익률 흔들림 5%p 이하(3년 이상일 때)', '자료 3년 미만' if spread is None else f'표준편차 {spread * 100:.1f}%p',
                 spread is None or spread <= 0.05)]
    if master == 'lynch':
        return [('대형 유니버스(S&P500·나스닥100·테마 ETF) 소속', '소속' if large else '아님', large),
                ('순이익 성장 15~30%', v(m['ni_growth'], pct), ok(m['ni_growth'], lambda x: 0.15 <= x <= 0.30)),
                ('PEG 1.0 이하(PER ÷ 순이익 성장률, 근사)', v(m.get('peg'), lambda x: f'{x:.2f}'), ok(m.get('peg'), lambda x: x <= 1.0)),
                ('부채/자본 0.8 이하', v(m['de'], lambda x: f'{x:.2f}'), ok(m['de'], lambda x: x <= 0.8))]
    if master == 'fisher':
        om = m['om']
        return [('대형 유니버스 소속', '소속' if large else '아님', large),
                ('매출 3년 연평균 15% 이상', v(m['rev_cagr3'], pct), ok(m['rev_cagr3'], lambda x: x >= 0.15)),
                ('영업이익률이 2년 전보다 개선', '자료 없음' if None in (om[-1], om[-3]) else f'{pct(om[-3])} → {pct(om[-1])}',
                 None not in (om[-1], om[-3]) and om[-1] > om[-3]),
                ('영업이익률 플러스', v(om[-1], pct), ok(om[-1], lambda x: x > 0))]
    if master == 'tenbagger':
        gm = m['gm']
        return [('시가총액 3억~100억 달러', v(m.get('mcap'), lambda x: f'{x / 1e8:,.0f}억 달러(근사)'), ok(m.get('mcap'), lambda x: 3e8 <= x <= 1e10)),
                ('매출 성장(1년) 25% 이상', v(m['rev_growth'], pct), ok(m['rev_growth'], lambda x: x >= 0.25)),
                ('매출 3년 연평균 20% 이상', v(m['rev_cagr3'], pct), ok(m['rev_cagr3'], lambda x: x >= 0.20)),
                ('총이익률 40% 이상 또는 개선', v(gm[-1], pct), ok(gm[-1], lambda x: x >= 0.40 or (gm[-2] is not None and x > gm[-2]))),
                ('희석 연 5% 이하', '미확인(분할 추정) · 통과 처리' if m['dilution'] is None else pct(m['dilution']),
                 m['dilution'] is None or m['dilution'] <= 0.05),
                ('부채 증가가 매출 증가 이하', v(m['debt_growth'], pct),
                 m['debt_growth'] is None or (m['rev_growth'] is not None and m['debt_growth'] <= m['rev_growth'])),
                ('적자라면 현금으로 1년 이상', '흑자(해당 없음)' if m['runway_years'] is None else f"{m['runway_years']:.1f}년",
                 m['runway_years'] is None or m['runway_years'] >= 1),
                ('영업현금 개선 또는 잉여현금 플러스', '개선' if m['cfo_up'] else v(m['fcf'], lambda x: f'FCF {x / 1e6:,.0f}백만 달러'),
                 bool(m['cfo_up'] or (m['fcf'] or 0) > 0))]
    raise ValueError(master)


def passes(master, m, etfs):
    return all(row[2] for row in criteria(master, m, etfs))


def screen(master, m, etfs):
    """(passed, score, Korean reasons); passing is decided by criteria()."""
    need = lambda *keys: all(m.get(k) is not None for k in keys)
    if (odd := suspect(m)):
        return False, None, [f'자료 이상({odd}): 주식 수·단위·업종 확인 필요']
    if master == 'buffett':
        roe = [r for r in m['roe'] if r is not None]
        if 'IVV' not in etfs or len(roe) < 4 or not need('de', 'fcf', 'fcf_yield'):
            return False, None, ['S&P500 밖이거나 자료 부족']
        return passes(master, m, etfs), m['fcf_yield'], [f'ROE {len(roe)}년 최저 {pct(min(roe))}', f"부채/자본 {m['de']:.1f}",
                                                         f"FCF 수익률 {m['fcf_yield'] * 100:.1f}%"]
    if master == 'lynch':
        if not set(etfs) & set(LARGE) or not need('ni_growth', 'pe', 'peg', 'de'):
            return False, None, ['대상 밖이거나 자료 부족']
        return passes(master, m, etfs), -m['peg'], [f"순이익 성장 {pct(m['ni_growth'])}", f"PER {m['pe']:.0f}", f"PEG {m['peg']:.2f}", f"부채/자본 {m['de']:.1f}"]
    if master == 'fisher':
        om = m['om']
        if not set(etfs) & set(LARGE) or not need('rev_cagr3') or om[-1] is None or om[-3] is None:
            return False, None, ['대상 밖이거나 자료 부족']
        return passes(master, m, etfs), m['rev_cagr3'], [f"매출 3년 연평균 {pct(m['rev_cagr3'])}", f'영업이익률 {pct(om[-3])} → {pct(om[-1])}']
    if master == 'tenbagger':
        gm = m['gm']
        if not need('mcap', 'rev_growth', 'rev_cagr3') or gm[-1] is None:
            return False, None, ['자료 부족']
        ok = passes(master, m, etfs)
        reasons = [f"시가총액 {m['mcap'] / 1e8:,.0f}억 달러", f"매출 성장 {pct(m['rev_growth'])} · 3년 {pct(m['rev_cagr3'])}", f'총이익률 {pct(gm[-1])}',
                   '희석률 미확인(분할 추정)' if m['dilution'] is None else f"희석 {pct(m['dilution'])}"]
        return ok, m['rev_growth'] + m['rev_cagr3'] + 0.5 * gm[-1], reasons
    raise ValueError(master)


def timing(m):
    """래리 윌리엄스 tag: where the price sits (never a filter)."""
    if m.get('above200') is None:
        return None
    return ('200일선 위' if m['above200'] else '200일선 아래') + (f" · 52주 범위 {pct(m['pos52'])}" if m.get('pos52') is not None else '')


def prefilter(master, m, etfs):
    """Fundamentals alone (before prices): which names deserve a price fetch for this screen."""
    roe = [r for r in m['roe'] if r is not None]
    if master == 'buffett':
        return 'IVV' in etfs and len(roe) >= 4 and min(roe) >= 0.15 and m['de'] is not None and m['de'] <= 1.0 and (m['fcf'] or 0) > 0
    if master == 'lynch':
        return bool(set(etfs) & set(LARGE)) and m['ni_growth'] is not None and 0.15 <= m['ni_growth'] <= 0.30
    if master == 'fisher':
        return bool(set(etfs) & set(LARGE)) and (m['rev_cagr3'] or 0) >= 0.15
    return (m['rev_growth'] or 0) >= 0.25 and (m['rev_cagr3'] or 0) >= 0.20


def outcome(series, day, months):
    """Return from the close on or before `day` to the close on or before `day + months` (None when not reached yet)."""
    if not series:
        return None
    days = sorted(series)
    end = day + timedelta(days=round(months * 30.44))
    if end > days[-1]:
        return None
    at = lambda d: series[max(x for x in days if x <= d)] if any(x <= d for x in days) else None  # noqa: E731
    a, b = at(day), at(end)
    return b / a - 1 if a and b else None


# ---------- collection (network) ----------

def nport(symbol):
    """The latest full N-PORT portfolio of one US ETF: (as_of, holdings)."""
    from src.tools import etf_holdings_tool as E
    fund = E._us_match(symbol, 1)[0]
    filing = E._select_nport_filing(E._us_nport_filings(fund['series_id']), E._us_indexed_periods(fund['series_id']))
    parsed = E._parse_nport(E._sec_get_text(E._nport_document_url(fund['cik'] or '0', filing['accession'])))
    return parsed['as_of'], parsed['holdings']


def load_facts(tickers, today, batch=40):
    """{ticker: {field: [five yearly values]}} from SEC company facts (point in time): TTM, cash flows by fiscal year."""
    import pandas as pd
    from backtest.loaders.fundamentals_loader import load_fundamental_panel
    points = pd.DatetimeIndex([today - timedelta(days=365 * (YEARS - 1 - i)) for i in range(YEARS)])
    out, start = {}, points[0].date().isoformat()
    for i in range(0, len(tickers), batch):
        part = tickers[i:i + batch]
        panel = load_fundamental_panel(part, [k for k in FIELDS if k not in ANNUAL], start, today.isoformat(), freq='ttm', index=points)
        panel.update(load_fundamental_panel(part, list(ANNUAL), start, today.isoformat(), freq='annual', index=points))
        for t in part:
            out[t] = {k: [None if pd.isna(v) else float(v) for v in panel[k][t].tolist()] for k in FIELDS}
    return out


def prices(symbol, years=2):
    from vibe_longterm_replay import fetch
    return fetch(symbol, years)


def collect_universe(db, today, notes):
    from backtest.loaders import sec_edgar_client as S
    payload = S._sec_get_json(S._TICKERS_URL)
    index = ticker_index(payload)
    by_ticker = {r['ticker'].upper(): f"{int(r['cik_str']):010d}" for r in payload.values()}
    day, rows, info = today.isoformat(), [], {}
    for etf in ETFS:
        try:
            as_of, holdings = nport(etf)
        except Exception as exc:  # one fund failing leaves the others
            info[etf] = {'error': type(exc).__name__}
            continue
        matched, missed = match(holdings, index, by_ticker)
        info[etf] = {'as_of': as_of, 'matched': len(matched), 'missed': len(missed), 'missed_sample': missed[:5]}
        rows += [(day, t, cik, name, etf, p) for t, cik, name, p in matched]
    with db:
        db.executemany('INSERT OR REPLACE INTO universe VALUES (?,?,?,?,?,?)', rows)
    notes['universe'] = info
    return sorted({r[1] for r in rows})


def collect_13f(db, notes, tool=None):
    """Each master's latest 13F: every position (100 a page, paged on) so a change line finds its share class or ETF product,
    and the quarter's changes."""
    if tool is None:
        from src.tools.institutional_holdings_tool import InstitutionalHoldingsTool
        tool = InstitutionalHoldingsTool()
    done = {}
    for manager, (master, firm) in MANAGERS.items():
        try:
            positions, offset = [], 0
            while True:
                out = json.loads(tool.execute(mode='manager_holdings', manager=manager, include_changes=True, top_n=100, offset=offset))
                positions += out['data'].get('positions') or []
                if offset == 0:
                    data = out['data']
                offset = (out.get('paging') or {}).get('next_offset')
                if not offset or len(positions) >= 1000:
                    break
            data = {**data, 'positions': positions}
            quarter = (data.get('filing') or {}).get('quarter') or (data.get('filing') or {}).get('period_end')
            keep = {'master': master, 'firm': firm, 'cik': data.get('cik'), 'filing': data.get('filing'),
                    'portfolio_value_usd': data.get('portfolio_value_usd'), 'positions': data.get('positions'),
                    'changes': (data.get('changes') or {}).get('items')}
            with db:
                db.execute('INSERT OR REPLACE INTO managers13f VALUES (?,?,?)', (str(quarter), manager, json.dumps(keep, ensure_ascii=False)))
            done[manager] = str(quarter)
        except Exception as exc:
            done[manager] = f'error {type(exc).__name__}'
    notes['13f'] = done


def run_universe(root, today):
    db, notes = connect(root), {}
    tickers = collect_universe(db, today, notes)
    collect_13f(db, notes)
    facts = load_facts(tickers, today)
    with db:
        db.executemany('INSERT OR REPLACE INTO facts VALUES (?,?,?)',
                       [(today.isoformat(), t, json.dumps(v)) for t, v in facts.items()])
        db.execute('INSERT OR REPLACE INTO runs VALUES (?,?,?,?)', (today.isoformat(), 'universe', datetime.now(KST).isoformat(),
                                                                  json.dumps({**notes, 'tickers': len(tickers)}, ensure_ascii=False)))
    return {**notes, 'tickers': len(tickers), 'facts': len(facts)}


def latest(db, table):
    row = db.execute(f'SELECT MAX(day) FROM {table}').fetchone()
    return row[0]


def run_weekly(root, today, fetch=prices, pause=0.2):
    db = connect(root)
    uday, fday = latest(db, 'universe'), latest(db, 'facts')
    if not uday or not fday:
        raise SystemExit('유니버스·재무가 없습니다. 먼저 --universe를 실행하세요.')
    etfs, names = {}, {}
    for r in db.execute('SELECT ticker, name, etf, pct FROM universe WHERE day=?', (uday,)):
        etfs.setdefault(r['ticker'], {})[r['etf']] = r['pct']
        names.setdefault(r['ticker'], r['name'])
    facts = {r['ticker']: json.loads(r['data']) for r in db.execute('SELECT ticker, data FROM facts WHERE day=?', (fday,))}
    base = {t: metrics(f) for t, f in facts.items()}
    wanted = sorted(t for t, m in base.items() if any(prefilter(k, m, etfs.get(t, {})) for k in MASTERS))
    series, failed = {}, []
    for t in wanted + ['QQQ', 'SPY']:
        try:
            series[t] = fetch(t)
        except Exception:
            failed.append(t)
        time.sleep(pause)
    day, picks, counts, odd = today.isoformat(), [], {}, {}
    for master in MASTERS:
        passed = []
        for t in wanted:
            s = series.get(t)
            if not s:
                continue
            closes = [s[d] for d in sorted(s)]
            m = metrics(facts[t], closes[-1], closes)
            ok, score, reasons = screen(master, m, etfs.get(t, {}))
            if reasons[0].startswith('자료 이상'):
                odd[t] = reasons[0]
            if ok:
                passed.append((score, t, m, reasons + ([timing(m)] if timing(m) else []), max(s)))
        passed.sort(key=lambda x: x[0], reverse=True)
        counts[master] = len(passed)
        for rank, (score, t, m, reasons, pdate) in enumerate(passed[:TOP], 1):
            keep = {k: v for k, v in m.items() if k in ('mcap', 'pe', 'peg', 'fcf_yield', 'rev_growth', 'rev_cagr3', 'ni_growth', 'de',
                                                         'dilution', 'above200', 'pos52')}
            picks.append((day, master, rank, t, names.get(t, t), series[t][pdate], pdate.isoformat(), score,
                          json.dumps(keep), json.dumps(reasons, ensure_ascii=False), json.dumps(etfs.get(t, {}))))
    with db:
        db.execute('DELETE FROM picks WHERE day=?', (day,))  # a rerun on the same day replaces that day's list
        db.executemany('INSERT INTO picks VALUES (?,?,?,?,?,?,?,?,?,?,?)', picks)
    rows = follow_up(db, series, fetch)
    info = {'universe_day': uday, 'facts_day': fday, 'prefiltered': len(wanted), 'price_failed': len(failed), 'passed': counts,
            'suspect': len(odd), 'suspect_sample': dict(list(odd.items())[:8]),
            'outcomes': rows}
    with db:
        db.execute('INSERT OR REPLACE INTO runs VALUES (?,?,?,?)', (day, 'weekly', datetime.now(KST).isoformat(), json.dumps(info)))
    return info


def follow_up(db, series, fetch):
    """추천 성적표: every past pick's return 1·3·6·12 months later against QQQ and SPY (what has been reached so far)."""
    rows = []
    for p in db.execute('SELECT day, master, ticker, price_date FROM picks').fetchall():
        start = date.fromisoformat(p['price_date'])
        for months in HORIZONS:
            if db.execute('SELECT 1 FROM outcomes WHERE day=? AND master=? AND ticker=? AND months=?',
                          (p['day'], p['master'], p['ticker'], months)).fetchone():
                continue
            if p['ticker'] not in series:
                try:
                    series[p['ticker']] = fetch(p['ticker'], 3)
                except Exception:
                    series[p['ticker']] = {}
            got = [outcome(series.get(k), start, months) for k in (p['ticker'], 'QQQ', 'SPY')]
            if got[0] is not None:
                rows.append((p['day'], p['master'], p['ticker'], months, *got))
    with db:
        db.executemany('INSERT OR REPLACE INTO outcomes VALUES (?,?,?,?,?,?,?)', rows)
    return len(rows)


def domestic_etfs(pick):
    """{engine ETF key: the name's weight % in that US ETF or None}, largest first: the KRX-listed ETFs the 적립식 may buy
    for exposure to a US pick (docs/longterm-account-settings-plan.md 3-A). The US stock itself is never ordered."""
    from vibe_longterm import ASSETS
    by_proxy = {a[4]: k for k, a in ASSETS.items() if a[4]}
    out = {}
    for etf, pct in (pick.get('etfs') or {}).items():
        if (key := by_proxy.get(ALIASES.get(etf, etf))) and (key not in out or (pct or 0) > (out[key] or 0)):
            out[key] = pct
    return dict(sorted(out.items(), key=lambda item: -(item[1] or 0)))


def universe_names(root=ROOT):
    """{ticker: company name} of the latest universe (the chart box names 13F holdings through it); {} before the first run."""
    if not (Path(root) / 'ideas.sqlite3').exists():
        return {}
    db = connect(Path(root))
    day = latest(db, 'universe')
    return {r['ticker']: r['name'] for r in db.execute('SELECT ticker, name FROM universe WHERE day=? ORDER BY ticker', (day,))} if day else {}


def stock_detail(root, ticker):
    """One name's record for the dashboard: its latest SEC facts (and their day), CIK, company name, the ETFs that hold it,
    every pick and every thesis card (newest first), and the masters' 13F lines on it (matched by company name)."""
    db = connect(Path(root))
    fact = db.execute('SELECT day, data FROM facts WHERE ticker=? ORDER BY day DESC LIMIT 1', (ticker,)).fetchone()
    uday = latest(db, 'universe')
    held = db.execute('SELECT cik, name, etf, pct FROM universe WHERE day=? AND ticker=?', (uday, ticker)).fetchall() if uday else []
    picks = [dict(r) | {'reasons': json.loads(r['reasons'])} for r in db.execute(
        'SELECT day, master, rank, price, price_date, reasons FROM picks WHERE ticker=? ORDER BY day DESC, master', (ticker,))]
    theses = [{'day': r['day'], 'master': r['master'], **json.loads(r['card'])}
              for r in db.execute('SELECT day, master, card FROM theses WHERE ticker=? ORDER BY day DESC', (ticker,))]
    name = held[0]['name'] if held else (db.execute('SELECT name FROM picks WHERE ticker=? LIMIT 1', (ticker,)).fetchone() or [ticker])[0]
    holders = []
    for r in db.execute('SELECT * FROM managers13f m WHERE quarter = (SELECT MAX(quarter) FROM managers13f WHERE manager = m.manager) '
                        'ORDER BY manager'):
        d = json.loads(r['data'])
        line = next((p for p in d.get('positions') or [] if norm(p.get('issuer')) == norm(name)), None)
        change = next((c for c in d.get('changes') or [] if norm(c.get('issuer')) == norm(name)), None)
        if line or change:
            holders.append({'master': d.get('master'), 'firm': d.get('firm'), 'quarter': r['quarter'], 'position': line, 'change': change})
    return {'ticker': ticker, 'name': name, 'cik': held[0]['cik'] if held else None, 'etfs': {r['etf']: r['pct'] for r in held},
            'facts': json.loads(fact['data']) if fact else {}, 'facts_day': fact['day'] if fact else None,
            'picks': picks, 'theses': theses, 'holders': holders}


def status(root=ROOT):
    """What the dashboard and the reports read: the latest picks, the scorecard, 13F and run notes."""
    path = root / 'ideas.sqlite3'
    if not path.exists():
        return {'ready': False}
    db = connect(root)
    day = latest(db, 'picks')
    picks = [dict(r) | {k: json.loads(r[k]) for k in ('metrics', 'reasons', 'etfs')}
             for r in db.execute('SELECT * FROM picks WHERE day=? ORDER BY master, rank', (day,))] if day else []
    prev = db.execute('SELECT MAX(day) FROM picks WHERE day < ?', (day,)).fetchone()[0] if day else None
    before = {(r['master'], r['ticker']) for r in db.execute('SELECT master, ticker FROM picks WHERE day=?', (prev,))} if prev else set()
    for p in picks:
        p['new'] = bool(prev) and (p['master'], p['ticker']) not in before
    score = [dict(r) for r in db.execute(
        'SELECT master, months, COUNT(*) n, AVG(ret) ret, AVG(qqq) qqq, AVG(spy) spy, AVG(ret > qqq) beat '
        'FROM outcomes GROUP BY master, months ORDER BY master, months')]
    managers = [{'manager': r['manager'], 'quarter': r['quarter'], **json.loads(r['data'])} for r in db.execute(  # each one's latest
        'SELECT * FROM managers13f m WHERE quarter = (SELECT MAX(quarter) FROM managers13f WHERE manager = m.manager) ORDER BY manager')]
    q = max((m['quarter'] for m in managers), default=None)
    runs = {r['kind']: {'day': r['day'], 'at': r['at'], **json.loads(r['info'])}
            for r in db.execute('SELECT * FROM runs WHERE (kind, day) IN (SELECT kind, MAX(day) FROM runs GROUP BY kind)')}
    return {'ready': bool(picks), 'day': day, 'previous': prev, 'picks': picks, 'scorecard': score, 'quarter_13f': q,
            'managers': managers, 'runs': runs}


QUARTER_MONTHS = (2, 5, 8, 11)


def third_friday(day):
    return day.month in QUARTER_MONTHS and day.weekday() == 4 and 15 <= day.day <= 21


def scheduled(root, now):
    """One launchd firing: what it ran ('universe+weekly', 'weekly', 'already' or 'not_due')."""
    today = now.date()
    if now.strftime('%H:%M') < '07:20':
        if not third_friday(today):
            return 'not_due'
        run_universe(root, today)
        run_weekly(root, today)
        return 'universe+weekly'
    if connect(root).execute("SELECT 1 FROM runs WHERE kind='weekly' AND day=?", (today.isoformat(),)).fetchone():
        return 'already'
    run_weekly(root, today)
    return 'weekly'


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--universe', action='store_true')
    parser.add_argument('--weekly', action='store_true')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--scheduled', action='store_true', help='launchd us-ideas: decides by the time and the date')
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    today = datetime.now(KST).date()
    if args.scheduled:
        print(json.dumps({'at': datetime.now(KST).isoformat(), 'ran': scheduled(args.root, datetime.now(KST))}, ensure_ascii=False))
        return
    if args.universe:
        print(json.dumps(run_universe(args.root, today), ensure_ascii=False, default=str))
    if args.weekly:
        print(json.dumps(run_weekly(args.root, today), ensure_ascii=False, default=str))
    if args.status or not (args.universe or args.weekly):
        print(json.dumps(status(args.root), ensure_ascii=False, default=str, indent=1))


if __name__ == '__main__':
    main()
