"""낙폭과대 반등 outcome log (research only; never orders). Run after the close (the learning watch does it daily).

For each watched code on a session it records, with Yahoo 5-minute bars:
- how far it fell (day low vs previous close, in % and in ATR20 multiples) and the Nasdaq move of the night before;
- whether the current rule triggered, or nearly did (a near miss: at least NEAR x the required fall);
- what the current rule would have done from there (entry when the fall stopped, then target / stop / close or the
  next close when the rule may hold a day), with the best and worst point after the entry;
- what the engines actually said and did that day (oversold_scan / oversold_signal events and the fills tagged
  oversold in the Jev runner's and vibe-trading's journals).
Each session is stored once more on every run (research_store kind 'oversold_outcome'); the newest record of a day
wins, so a held trade's next-day result fills in on the following run. `summary(days)` pools them for the reviews.

  PYTHONPATH=agent:scripts .venv/bin/python scripts/oversold_outcomes.py [--day YYYY-MM-DD] [--summary 20]
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
RUNTIME = Path.home() / '.vibe-trading/nhplug'
NEAR = 0.6  # a near miss fell at least this share of the required fall


def _events(root, day):
    from sqlite_ro import day_rows
    return [e for e in day_rows(Path(root) / 'events.jsonl', day)[0] or [] if str(e.get('event', '')).startswith('oversold')]


def _trades(root, day, code):
    """Oversold fills of one journal that day: [(side, qty, price, pnl)] (engine_compare.journal paths)."""
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'streamlit_namuh'))
        import engine_compare as ec
        rows = ec.journal(Path(root) / 'orders.sqlite3', Path(root) / 'events.jsonl', 'vibe' if 'vibe' in str(root) else 'jev')
    except Exception:
        return []
    bought = {r['time'] for r in rows if r['symbol'] == code and r['side'] == 'BUY' and r['path'] == '낙폭과대 반등'}
    return [(r['side'], r['qty'], r['price'], r['pnl']) for r in rows
            if r['symbol'] == code and r['time'].startswith(day) and (r['time'] in bought or r['side'] == 'SELL' and bought)]


def rule_params(settings, kind):
    """The live settings as oversold_research.simulate() parameters (5-minute bars: calm minutes / 5, rounded up)."""
    return {'x': settings[f'oversold_{kind}_drop_pct'], 'k': settings['oversold_atr_mult'],
            'n': max(1, math.ceil(settings['oversold_calm_min'] / 5)), 'y': settings['oversold_rebound_pct'],
            'tp': settings['oversold_tp_pct'], 'r': settings['oversold_recover_pct'] / 100, 's': settings['oversold_stop_pct'],
            'hold': settings['oversold_hold_days'] > 0}


def evaluate_day(day, code, daily, m5, atr, nq, settings):
    """One code on one session: the fall, trigger / near miss, and the rule's simulated outcome; None if no data."""
    import oversold
    import oversold_research as research
    target = date.fromisoformat(day)
    days = [d for d in daily if d[0].date() <= target]
    if len(days) < 2 or days[-1][0].date() != target:
        return None
    prev_close, low, close = days[-2][4], days[-1][3], days[-1][4]
    after = [d for d in daily if d[0].date() > target]
    kind = oversold.kind(code)
    atr_a = atr.get(target)
    drop = (low / prev_close - 1) * 100
    need = max(settings[f'oversold_{kind}_drop_pct'], settings['oversold_atr_mult'] * atr_a) if atr_a else settings[f'oversold_{kind}_drop_pct']
    off = kind == 'lev' and not settings.get('oversold_lev_on')
    status = 'triggered' if -drop >= need and not off else 'near' if -drop >= NEAR * need else None
    if status is None:
        return None
    bars = [b for b in m5 if b[0].date() == target]
    sim = research.simulate(bars, prev_close, atr_a or 0.0, after[0][4] if after else None, rule_params(settings, kind)) if bars else None
    pending = bool(sim) and settings['oversold_hold_days'] > 0 and not after
    return {'day': day, 'code': code, 'kind': kind, 'status': status, 'drop_pct': round(drop, 2), 'need_pct': round(need, 2),
            'atr_pct': round(atr_a, 2) if atr_a else None, 'atr_multiple': round(-drop / atr_a, 2) if atr_a else None,
            'nasdaq_prev_pct': round(nq(target), 2) if nq(target) is not None else None, 'close_vs_low_pct': round((close / low - 1) * 100, 2),
            'gap_recovered_pct': round((close - low) / (prev_close - low) * 100, 1) if prev_close > low else None,
            'next_close_pct': round((after[0][4] / close - 1) * 100, 2) if after else None,
            'rule': None if not sim else {'return_pct': round(sim[0], 2), 'exit': sim[1], 'adverse_pct': round(sim[2], 2),
                                          'pending_next_day': pending},
            'no_bars': not bars}


def market_data(codes, day=None):
    """{code: (daily, 5-minute)} and the Nasdaq function, fetched once (Yahoo keeps 60 days of 5-minute bars)."""
    import oversold_research as research
    from oversold_cases import yahoo_symbols
    old = day is None or (datetime.now(KST).date() - date.fromisoformat(day)).days > 4
    data = {}
    for code in codes:
        for symbol in yahoo_symbols(code):
            daily = research.fetch(symbol, '1d', '6mo')
            if daily:
                data[code] = (daily, research.fetch(symbol, '5m', '60d' if old else '5d'))
                break
    ixic = research.fetch('^IXIC', '1d', '6mo')
    return data, research.nasdaq_prev_night(ixic) if ixic else (lambda d: None)


def collect(day, settings=None, codes=None, record=True, data=None):
    """Outcome rows of `day` for every watched code (plus the engines' answers and fills); stored when `record`.
    `data`: market_data() to reuse across days (backfill)."""
    import oversold
    import oversold_research as research
    from strategy_settings import read_settings
    settings = settings or read_settings(RUNTIME / 'shared-etf-paper')[0]
    names = oversold.universe(RUNTIME / 'shared-etf-paper')
    bars, nq = data or market_data(codes or names, day)
    rows = []
    engines = {'jev': RUNTIME / 'shared-etf-paper', 'vibe': RUNTIME / 'vibe-paper'}
    events = {name: _events(root, day) for name, root in engines.items()}
    for code in codes or names:
        if code not in bars:
            continue
        daily, m5 = bars[code]
        row = evaluate_day(day, code, daily, m5, research.atr_pct(daily), nq, settings)
        if not row:
            continue
        row['name'] = names.get(code, code)
        for name, items in events.items():
            mine = [e for e in items if e.get('symbol') == code]
            signal = next((e for e in reversed(mine) if e.get('event') == 'oversold_signal'), None)
            row[name] = {'scanned': any(e.get('event') == 'oversold_scan' for e in mine),
                         'answer': (f"{signal.get('choice')} {signal.get('confidence')}" if signal and signal.get('engine') == 'jev'
                                    else f"score {signal.get('score')}" if signal else None),
                         'bought': bool(signal and signal.get('permitted')),
                         'trades': _trades(engines[name], day, code)}
        rows.append(row)
    payload = {'time': datetime.now(KST).isoformat(timespec='seconds'), 'day': day, 'rows': rows,
               'settings': {k: v for k, v in settings.items() if k.startswith('oversold_')}}
    if record:
        import research_store
        research_store.record('oversold_outcome', payload, RUNTIME / 'shared-etf-paper' / 'market-research.sqlite3')
    return payload


def backfill(record=True):
    """collect() for every session Yahoo still has 5-minute bars for (about 60), fetching the data once; returns the
    number of sessions with at least one triggered or near-miss code."""
    import oversold
    names = oversold.universe(RUNTIME / 'shared-etf-paper')
    data = market_data(names)
    sessions = sorted({b[0].date().isoformat() for _, m5 in data[0].values() for b in m5})
    found = 0
    for day in sessions:
        found += bool(collect(day, codes=list(names), record=record, data=data)['rows'])
    return found


def summary(days=20):
    """The newest record of each of the last `days` sessions, pooled: counts and the rule's results by kind and status."""
    from research_store import history
    latest = {}
    for item in history('oversold_outcome', days * 3, RUNTIME / 'shared-etf-paper' / 'market-research.sqlite3'):
        latest.setdefault(item.get('day'), item)  # newest first
    rows = [r for day in sorted(latest)[-days:] for r in latest[day].get('rows', [])]
    groups = {}
    for r in rows:
        g = groups.setdefault(f"{r['kind']}:{r['status']}", {'n': 0, 'simulated': 0, 'wins': 0, 'returns': [], 'bought_jev': 0, 'bought_vibe': 0})
        g['n'] += 1
        rule = r.get('rule')
        if rule and not rule.get('pending_next_day'):
            g['simulated'] += 1
            g['wins'] += rule['return_pct'] > 0
            g['returns'].append(rule['return_pct'])
        g['bought_jev'] += bool((r.get('jev') or {}).get('bought'))
        g['bought_vibe'] += bool((r.get('vibe') or {}).get('bought'))
    for g in groups.values():
        rets = g.pop('returns')
        g['mean_return_pct'] = round(sum(rets) / len(rets), 2) if rets else None
        g['worst_pct'] = min(rets) if rets else None
    return {'sessions': sorted(latest)[-days:], 'events': len(rows), 'groups': groups, 'rows': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--day', help='YYYY-MM-DD (default today, Asia/Seoul)')
    parser.add_argument('--summary', type=int, help='pool the last N sessions instead of collecting')
    parser.add_argument('--no-record', action='store_true')
    parser.add_argument('--backfill', action='store_true', help='every session Yahoo still has 5-minute bars for')
    args = parser.parse_args()
    if args.backfill:
        print(json.dumps({'sessions_with_events': backfill(record=not args.no_record)}))
        return
    if args.summary:
        print(json.dumps(summary(args.summary), ensure_ascii=False, indent=1, default=str))
        return
    day = args.day or datetime.now(KST).strftime('%Y-%m-%d')
    print(json.dumps(collect(day, record=not args.no_record), ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__':
    main()
