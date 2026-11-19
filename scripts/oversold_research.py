"""Oversold-rebound (낙폭과대 반등) research. RESEARCH ONLY: no orders, no accounts, no trading runners touched.

Fetches Yahoo Finance daily (2y) and 5-minute (60d) bars for six KRX instruments and ^IXIC/^NDX, measures how
far a day low sits below the previous close (in % and in ATR20 multiples), how much of that gap is recovered,
and grid-searches intraday "fall stopped -> buy -> sell the overshoot" rules. Costs: COST % round trip is deducted.
Intrabar order is conservative (stop checked before targets in the same bar). Usage:
  PYTHONPATH=agent:scripts .venv/bin/python scripts/oversold_research.py [--no-record]
"""
from __future__ import annotations

import itertools
import json
import statistics
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, time
from zoneinfo import ZoneInfo
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')

KST = ZoneInfo('Asia/Seoul')
NY = ZoneInfo('America/New_York')
TICKERS = {'005930': '005930.KS', '000660': '000660.KS', '069500': '069500.KS',
           '122630': '122630.KS', '494310': '494310.KS', '196170': '196170.KQ'}
GROUP = {'005930': 'stock', '000660': 'stock', '196170': 'stock', '069500': 'etf1x', '122630': 'etf2x', '494310': 'etf2x'}
COST = 0.15  # % round trip (fees, ETF no tax; stock tax ~0.18% would add a bit)
EXIT_BY = time(15, 20)


def fetch(symbol, interval, rng, volume=False):
    """[(datetime KST, o, h, l, c)] (plus volume when asked) from the Yahoo chart API; [] when blocked or empty."""
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval={interval}&range={rng}'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        res = json.load(urllib.request.urlopen(req, timeout=20))['chart']['result'][0]
    except Exception as exc:  # noqa: BLE001 - blocked/missing ticker must not stop the rest
        print(f'WARN {symbol} {interval}: {exc}', file=sys.stderr)
        return []
    q = res['indicators']['quote'][0]
    tz = ZoneInfo(res['meta'].get('exchangeTimezoneName', 'Asia/Seoul'))
    out = []
    for i, t in enumerate(res.get('timestamp') or []):
        vals = [q[k][i] for k in ('open', 'high', 'low', 'close')]
        if None not in vals:
            out.append((datetime.fromtimestamp(t, tz), *vals, *([q['volume'][i] or 0] if volume else [])))
    return out


def atr_pct(daily):
    """{date: ATR20 % of prev close, known BEFORE that day} from daily bars."""
    tr, out = [], {}
    for i in range(1, len(daily)):
        pc = daily[i - 1][4]
        if len(tr) >= 20:
            out[daily[i][0].date()] = statistics.mean(tr[-20:])
        tr.append((max(daily[i][2], pc) - min(daily[i][3], pc)) / pc * 100)
    return out


def nasdaq_prev_night(ixic):
    """{kr_date: % change of the last US session that closed before that KR date}."""
    ch = {ixic[i][0].date(): (ixic[i][4] / ixic[i - 1][4] - 1) * 100 for i in range(1, len(ixic))}
    days = sorted(ch)
    return lambda d: next((ch[x] for x in reversed(days) if x < d), None)


def daily_events(code, daily, atr, nq, min_drop=2.0, min_k=1.5):
    ev = []
    for i in range(1, len(daily) - 3):
        d = daily[i][0].date()
        pc, lo, cl = daily[i - 1][4], daily[i][3], daily[i][4]
        a = atr.get(d)
        drop = (lo / pc - 1) * 100
        if a is None or drop > -min_drop or -drop < min_k * a:
            continue
        gap = pc - lo
        ev.append({'code': code, 'date': str(d), 'drop': round(drop, 2), 'k': round(-drop / a, 2), 'nq': nq(d),
                   'low_to_close': round((cl / lo - 1) * 100, 2), 'gap_recovered': round((cl - lo) / gap, 2),
                   'close_vs_prev': round((cl / pc - 1) * 100, 2),
                   **{f'high_d{n}': round((max(x[2] for x in daily[i + 1:i + 1 + n]) / lo - 1) * 100, 2) for n in (1, 2, 3)},
                   'next_close': round((daily[i + 1][4] / cl - 1) * 100, 2)})
    return ev


def daily_summary(ev):
    out = {}
    for g in ('stock', 'etf1x', 'etf2x'):
        for tag, sub in ((g, [e for e in ev if GROUP[e['code']] == g]),
                         (g + '_nq<=-2', [e for e in ev if GROUP[e['code']] == g and e['nq'] is not None and e['nq'] <= -2])):
            if sub:
                f = lambda k: [round(statistics.mean(e[k] for e in sub), 2), round(statistics.median(e[k] for e in sub), 2)]
                out[tag] = {'events': len(sub), 'low_to_close': f('low_to_close'), 'gap_recovered': f('gap_recovered'),
                            'high_d1': f('high_d1'), 'high_d3': f('high_d3'), 'next_close_after_close': f('next_close'),
                            'win_next_close': round(sum(e['next_close'] > 0 for e in sub) / len(sub) * 100, 1)}
    return out


def simulate(bars, pc, atr_a, nxt_close, p):
    """One day, one instrument. bars=[(dt,o,h,l,c)] of one KR session. Returns (ret %, why, adverse %) or None."""
    low, calm, entry = float('inf'), 0, None
    for dt, o, h, l, c in bars:
        if entry is None:
            if l < low:
                low, calm = l, 0
            else:
                calm += 1
            drop = (low / pc - 1) * 100
            if drop > -p['x'] or -drop < p['k'] * atr_a or dt.time() >= EXIT_BY:
                continue
            if calm >= p['n'] and c >= low * (1 + p['y'] / 100):
                entry, worst = c, c
                stop, tp, gap_px = low * (1 - p['s'] / 100), c * (1 + p['tp'] / 100), low + p['r'] * (pc - low)
                target = min(tp, gap_px)
            continue
        worst = min(worst, l)
        if l <= stop:
            return (stop / entry - 1) * 100 - COST, 'stop', (worst / entry - 1) * 100
        if h >= target:
            return (target / entry - 1) * 100 - COST, 'target', (worst / entry - 1) * 100
        if dt.time() >= EXIT_BY:
            last = c
            break
    else:
        last = bars[-1][4]
    if entry is None:
        return None
    if p['hold'] and nxt_close:
        last = nxt_close
    return (last / entry - 1) * 100 - COST, 'close', (worst / entry - 1) * 100


def intraday_days(m5):
    days = defaultdict(list)
    for b in m5:
        if time(9, 0) <= b[0].time() <= time(15, 30):
            days[b[0].date()].append(b)
    return days


def grid_search(data, grid):
    """data: {code: (daily_by_date, m5_days, atr)}; returns rows per (group, params)."""
    rows = []
    keys = list(grid)
    for combo in itertools.product(*grid.values()):
        p = dict(zip(keys, combo))
        by_group = defaultdict(list)
        for code, (dly, days, atr) in data.items():
            dates = sorted(dly)
            for d, bars in days.items():
                if d not in dly or d not in atr or len(bars) < 40:
                    continue
                pc = dly[d]['prev']
                nxt = next((dly[x]['close'] for x in dates if x > d), None)
                nxt_ret_base = nxt
                res = simulate(bars, pc, atr[d], nxt_ret_base, p)
                if res:
                    by_group[GROUP[code]].append((code, str(d), *res))
        for g, rs in by_group.items():
            r = [x[2] for x in rs]
            rows.append({'group': g, **p, 'events': len(r), 'win': round(sum(v > 0 for v in r) / len(r) * 100, 1),
                         'mean': round(statistics.mean(r), 2), 'median': round(statistics.median(r), 2),
                         'worst': round(min(r), 2), 'worst_adverse': round(min(x[4] for x in rs), 2),
                         'stops': sum(x[3] == 'stop' for x in rs), 'days': sorted({x[1] for x in rs})})
    return rows


CASES = [('005930', '2026-11-06'), ('000660', '2026-11-06'), ('122630', '2026-11-06'), ('069500', '2026-11-06'),
         ('196170', '2026-11-09'), ('196170', '2026-11-11')]
CASE_RULES = {'A n2 tp3 s1': {'x': 3.0, 'k': 0.0, 'n': 2, 'y': 0.3, 'tp': 3.0, 'r': 1.0, 's': 1.0, 'hold': False},
              'B n4 tp1.5 s0.5': {'x': 3.0, 'k': 0.0, 'n': 4, 'y': 0.3, 'tp': 1.5, 'r': 1.0, 's': 0.5, 'hold': False},
              'C =A, hold overnight': {'x': 3.0, 'k': 0.0, 'n': 2, 'y': 0.3, 'tp': 3.0, 'r': 1.0, 's': 1.0, 'hold': True}}


def case_notes(data):
    out = []
    for code, ds in CASES:
        dly, days, atr = data.get(code, ({}, {}, {}))
        d = datetime.fromisoformat(ds).date()
        if d not in dly or d not in days:
            continue
        v, bars = dly[d], days[d]
        nxt = next((dly[x]['close'] for x in sorted(dly) if x > d), None)
        row = {'code': code, 'date': ds, 'prev': v['prev'], 'low': v['low'], 'close': v['close'],
               'drop_pct': round((v['low'] / v['prev'] - 1) * 100, 2), 'atr20_pct': round(atr.get(d, 0), 2),
               'open': bars[0][1], 'rules': {}}
        for name, p in CASE_RULES.items():
            r = simulate(bars, v['prev'], 0.0, nxt, p)
            row['rules'][name] = None if r is None else {'ret': round(r[0], 2), 'exit': r[1], 'adverse': round(r[2], 2)}
        out.append(row)
    return out


def main(record=True):
    ixic, ndx = fetch('^IXIC', '1d', '2y'), fetch('^NDX', '1d', '2y')
    nq = nasdaq_prev_night(ixic or ndx) if (ixic or ndx) else (lambda d: None)
    all_ev, data, avail = [], {}, {}
    for code, sym in TICKERS.items():
        daily, m5 = fetch(sym, '1d', '2y'), fetch(sym, '5m', '60d')
        avail[code] = {'daily': len(daily), 'm5': len(m5)}
        if len(daily) < 30:
            continue
        atr = atr_pct(daily)
        all_ev += daily_events(code, daily, atr, nq)
        dly = {daily[i][0].date(): {'prev': daily[i - 1][4], 'close': daily[i][4], 'low': daily[i][3]} for i in range(1, len(daily))}
        data[code] = (dly, intraday_days(m5), atr)
    grid = {'x': [3.0, 6.0], 'k': [0.0, 1.0, 1.5], 'n': [2, 4, 8], 'y': [0.0, 0.3], 'tp': [1.5, 3.0],
            'r': [0.5, 1.0], 's': [0.5, 1.0], 'hold': [False, True]}
    rows = grid_search(data, grid)
    top = {}
    for g in ('stock', 'etf1x', 'etf2x'):
        gr = sorted((r for r in rows if r['group'] == g and r['events'] >= 5), key=lambda r: r['mean'] * min(1, r['events'] / 10), reverse=True)
        top[g] = [{k: v for k, v in r.items() if k != 'days'} for r in gr[:6]]
    cases = case_notes(data)
    summary = {'cases': cases, 'time': datetime.now(KST).isoformat(timespec='seconds'), 'availability': avail, 'cost_pct': COST,
               'daily_summary': daily_summary(all_ev), 'daily_events': all_ev, 'top_rules': top, 'grid_rows': len(rows)}
    if record:
        import research_store
        summary['recorded'] = research_store.record('oversold_research', {**summary, 'daily_events': all_ev[:200]})
    return summary, rows, data


if __name__ == '__main__':
    s, rows, data = main('--no-record' not in sys.argv)
    print(json.dumps({k: v for k, v in s.items() if k != 'daily_events'}, ensure_ascii=False, indent=1))
    print('daily events:', len(s['daily_events']))
