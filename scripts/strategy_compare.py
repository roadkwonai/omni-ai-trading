"""성과 비교 data (user-approved plan docs/dashboard-menu-plan-2026-11-19.md 5-4): one row per day and book of Jev trading
(shared paper ledger) and the long-term engine (virtual book and its two shadows) in nhplug/strategy-compare.sqlite3.

    PYTHONPATH=agent:scripts:streamlit_namuh .venv/bin/python scripts/strategy_compare.py --build
    PYTHONPATH=agent:scripts:streamlit_namuh .venv/bin/python scripts/strategy_compare.py --check

Every build recomputes all days from the source records (a few hundred rows), so it is idempotent and a late source
(the 15:40 long-term NAV) fills in on the next build. Read-only over the sources; it writes only its own file.

Return bases (decision 2 and 5 of the plan):
- Jev: (realized + change in unrealized − estimated fee/tax) ÷ the day's largest money in positions. A day with no
  position returns 0. The paper account holds far more cash than the runner uses, so its total assets are no base.
- long-term: the day's NAV change less money paid in, ÷ the previous NAV plus today's payment (fees already in NAV).
Benchmarks: KODEX 200 (069500) the same day, Nasdaq-100 the last US session before the Korean day.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / 'streamlit_namuh'))  # engine_compare.journal

FILE = 'strategy-compare.sqlite3'
BOOKS = {'jev': 'Jev 트레이딩', 'longterm': '장기투자', 'longterm_base': '기본 그림자', 'longterm_auto': '자동 그림자'}
LT_BOOKS = {'actual': 'longterm', 'base': 'longterm_base', 'auto': 'longterm_auto'}
COLUMNS = ('day', 'book', 'nav_index', 'contributed', 'pnl_day', 'pnl_cum', 'realized_day', 'unrealized', 'deployed',
           'ret_day', 'drawdown', 'trades', 'buys', 'sells', 'wins', 'turnover', 'avg_hold_min', 'fees_est',
           'regime_kr', 'regime_us', 'bench', 'source', 'built')
SCHEMA = """CREATE TABLE IF NOT EXISTS compare_daily (
  day TEXT, book TEXT, nav_index REAL, contributed REAL, pnl_day REAL, pnl_cum REAL, realized_day REAL, unrealized REAL,
  deployed REAL, ret_day REAL, drawdown REAL, trades INT, buys INT, sells INT, wins INT, turnover REAL, avg_hold_min REAL,
  fees_est REAL, regime_kr TEXT, regime_us TEXT, bench TEXT, source TEXT, built TEXT, PRIMARY KEY (day, book))"""


def _fees(day_rows, etfs):
    """Estimated commission both sides plus the transaction tax on stock (not ETF) sells, paper_costs' rates."""
    from paper_costs import COMMISSION_PCT, SELL_TAX_PCT
    turnover = sum(r['amount'] for r in day_rows)
    stock_sells = sum(r['amount'] for r in day_rows if r['side'] == 'SELL' and r['symbol'] not in etfs)
    return turnover * COMMISSION_PCT / 100 + stock_sells * SELL_TAX_PCT / 100


def _snapshots(path):
    """{day: last account snapshot of the day} from the runner's account-snapshots.jsonl."""
    from live_fills import plausible
    out = {}
    try:
        lines = Path(path).read_text(encoding='utf-8').splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue  # a half-written last line
        if plausible(row) and isinstance(row.get('time'), str):
            out[row['time'][:10]] = row
    return out


def jev_days(rows, days, snapshots, etfs):
    """[{day, ...}] for each day from the first fill on: realized, unrealized at the close, the day's largest cost in
    positions, fees, counts and the minutes held of positions closed that day. rows: engine_compare.journal()."""
    from engine_compare import apply_fill, episodes
    if not rows:
        return []
    first = rows[0]['time'][:10]
    days = sorted(d for d in set(days) | {r['time'][:10] for r in rows} if d >= first)
    books, out, last_unreal, i = {}, [], 0.0, 0
    closed = {}
    for ep in episodes(rows):
        if ep['close']:
            minutes = (datetime.fromisoformat(ep['close']) - datetime.fromisoformat(ep['open'])).total_seconds() / 60
            closed.setdefault(ep['close'][:10], []).append(minutes)
    def cost():
        return sum(q * a for q, a in books.values())

    for day in days:
        peak, day_rows = cost(), []
        while i < len(rows) and rows[i]['time'][:10] == day:
            r = rows[i]
            qty, avg, *_ = apply_fill(*books.get(r['symbol'], (0, 0.0)), r['side'], r['qty'], r['price'])
            books[r['symbol']] = (qty, avg)
            peak, i = max(peak, cost()), i + 1
            day_rows.append(r)
        snap = snapshots.get(day)
        unreal = (snap['evaluation'] - snap['invested'] if snap and all(type(snap.get(k)) in (int, float) for k in ('evaluation', 'invested'))
                  else 0.0 if cost() == 0 else last_unreal)  # no snapshot while holding: carry the last known value
        realized = sum(r['pnl'] for r in day_rows if r['side'] == 'SELL' and r['pnl'] is not None)
        fees = _fees(day_rows, etfs)
        sells = [r for r in day_rows if r['side'] == 'SELL']
        out.append({'day': day, 'realized_day': realized, 'unrealized': unreal, 'deployed': peak, 'fees_est': fees,
                    'pnl_day': realized + unreal - last_unreal - fees, 'trades': len(day_rows),
                    'buys': len(day_rows) - len(sells), 'sells': len(sells),
                    'wins': sum(1 for r in sells if (r['pnl'] or 0) > 0), 'turnover': sum(r['amount'] for r in day_rows),
                    'avg_hold_min': sum(closed[day]) / len(closed[day]) if closed.get(day) else None})
        last_unreal = unreal
    return out


def longterm_days(nav, contributed, fills):
    """{book: [{day, ...}]} from the long-term NAV rows [(day, book, value)] and paid-in money {day: total}; fills
    (vibe_longterm.journal_rows) give the actual book's trades."""
    import vibe_longterm as L
    by_book, out = {}, {}
    for day, book, value in nav:
        if book in LT_BOOKS:
            by_book.setdefault(book, []).append((day, value))
    for book, points in by_book.items():
        rows, prev_value, prev_paid, paid = [], None, 0, 0
        for day, value in sorted(points):
            paid = contributed.get(day, paid)
            base = (L.CAPITAL if prev_value is None else prev_value) + paid - prev_paid
            pnl = value - base
            day_fills = [f for f in fills if f['time'][:10] == day] if book == 'actual' else []
            sells = [f for f in day_fills if f['side'] == 'SELL']
            rows.append({'day': day, 'contributed': paid, 'pnl_day': pnl, 'pnl_cum': L.profit(value, paid), 'deployed': base,
                         'realized_day': sum(f['pnl'] or 0 for f in sells) if book == 'actual' else None,
                         'trades': len(day_fills) if book == 'actual' else None, 'buys': len(day_fills) - len(sells) if book == 'actual' else None,
                         'sells': len(sells) if book == 'actual' else None,
                         'wins': sum(1 for f in sells if (f['pnl'] or 0) > 0) if book == 'actual' else None,
                         'turnover': sum(f['amount'] for f in day_fills) if book == 'actual' else None})
            prev_value, prev_paid = value, paid
        out[LT_BOOKS[book]] = rows
    return out


def finish(rows):
    """Add the return, growth index, drawdown and running P&L to one book's day rows (oldest first)."""
    index, peak, total = 1.0, 1.0, 0.0
    for r in rows:
        r['ret_day'] = r['pnl_day'] / r['deployed'] if r.get('deployed') else 0.0
        index *= 1 + r['ret_day']
        peak = max(peak, index)
        total += r['pnl_day']
        r['nav_index'], r['drawdown'] = index, index / peak - 1
        r.setdefault('pnl_cum', total)
    return rows


def _bench(cases_db):
    """({day: KODEX 200 % change}, [(US day, Nasdaq-100 % change)]) from the 낙폭과대 case book's daily series."""
    if not Path(cases_db).exists():
        return {}, []
    from sqlite_ro import ro_connect
    with ro_connect(cases_db) as db:
        series = {s: db.execute('SELECT day, c FROM series WHERE symbol=? ORDER BY day', (s,)).fetchall() for s in ('069500.KS', '^NDX')}
    def pct(rows):
        return [(d, (c / p - 1) * 100) for (_, p), (d, c) in zip(rows, rows[1:]) if p]

    return dict(pct(series['069500.KS'])), pct(series['^NDX'])


def _regimes_kr(journal_db):
    """{day: KOSPI regime} from the trading journal's market context."""
    if not Path(journal_db).exists():
        return {}
    from sqlite_ro import ro_connect
    out = {}
    with ro_connect(journal_db) as db:
        for day, data in db.execute('SELECT day, data FROM days'):
            try:
                regime = json.loads(data)['market_context']['indices']['KOSPI']['regime']
            except (ValueError, KeyError, TypeError):
                continue
            label = regime.get('label') if isinstance(regime, dict) else regime  # {'label': '하락장', 'score': ...}
            if isinstance(label, str):
                out[day] = label
    return out


def regime_days(path):
    """{day: the long-term engine's confirmed regime} (last regime decision of each day); {} without the file."""
    if not Path(path).exists():
        return {}
    from sqlite_ro import ro_connect
    with ro_connect(path) as db:
        return dict(db.execute("SELECT day, regime FROM decisions WHERE kind = 'regime' ORDER BY time, rowid").fetchall())


def build(runtime):
    """Recompute every day and book from the sources and replace the table; returns {book: rows written}."""
    import engine_compare as ec
    import vibe_longterm as L
    from engine_roles import SHARED, ledger_root
    from etf_universe import SYMBOLS as ETF_CODES
    from vibe_longterm_view import nav_history
    runtime = Path(runtime)
    jev_root = ledger_root(runtime, 'jev')
    lt_root = runtime / 'vibe-longterm'
    journal_db = runtime / 'shared-etf-paper' / 'trading-journal.sqlite3'
    regimes_kr = _regimes_kr(journal_db)
    nav, contributed = nav_history(lt_root)
    jev = jev_days(ec.journal(jev_root / 'orders.sqlite3'), regimes_kr.keys() | {d for d, _, _ in nav},
                   _snapshots(jev_root / 'account-snapshots.jsonl'), set(ETF_CODES.values()))
    books = {'jev': jev, **longterm_days(nav, contributed, L.journal_rows(lt_root))}
    regimes_us = regime_days(lt_root / 'longterm.sqlite3')
    kodex, ndx = _bench(runtime / 'shared-etf-paper' / 'oversold-cases.sqlite3')
    built = datetime.now().astimezone().isoformat(timespec='seconds')
    jev_source = 'shared paper' if jev_root.name == SHARED else 'Jev virtual'
    sources = {'jev': f'{jev_source} orders.sqlite3 · account-snapshots.jsonl', **{b: 'vibe-longterm nav' for b in LT_BOOKS.values()}}
    table = []
    for book, rows in books.items():
        for r in finish(rows):
            us = [p for d, p in ndx if d < r['day']]
            bench = {'069500': kodex.get(r['day']), 'NDX': us[-1] if us else None}
            table.append({**{c: None for c in COLUMNS}, **r, 'book': book, 'regime_kr': regimes_kr.get(r['day']),
                          'regime_us': regimes_us.get(r['day']), 'bench': json.dumps(bench), 'source': sources[book], 'built': built})
    path = runtime / FILE
    from src.tools.private_db import private_file
    private_file(path)
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute(SCHEMA)
        db.execute('DELETE FROM compare_daily')
        db.executemany(f"INSERT INTO compare_daily VALUES ({','.join('?' * len(COLUMNS))})",
                       [tuple(r[c] for c in COLUMNS) for r in table])
    return {book: len(rows) for book, rows in books.items()}


def rows(runtime, *, start=None, end=None, book=None, timeout=5):
    """Stored rows, oldest first, optionally limited by trading day and book."""
    path = Path(runtime) / FILE
    if not path.exists():
        return []
    from sqlite_ro import ro_connect
    filters = [(column, value) for column, value in (('day >= ?', start), ('day <= ?', end), ('book = ?', book))
               if value is not None]
    where = ' WHERE ' + ' AND '.join(column for column, _ in filters) if filters else ''
    with ro_connect(path, timeout) as db:
        db.row_factory = sqlite3.Row
        found = [dict(r) for r in db.execute('SELECT * FROM compare_daily' + where + ' ORDER BY day, book',
                                             [value for _, value in filters])]
    for r in found:
        r['bench'] = json.loads(r['bench'] or '{}')
    return found


def returns(rows, book):
    """Daily returns for the period. Jev: P&L ÷ the period's average money in positions — each day's own base let a
    1,277원 day's −2% outweigh +75,990원 of P&L (11-11) — so the curve is the P&L curve; 장기투자: the stored day return."""
    if book != 'jev':
        return [r['ret_day'] or 0 for r in rows]
    used = [r['deployed'] for r in rows if r['deployed']]
    base = sum(used) / len(used) if used else 0
    return [(r['pnl_day'] or 0) / base if base else 0.0 for r in rows]


def curve(rows, book):
    """[(day, cumulative return, drawdown)]: Jev adds its days (one base), 장기투자 compounds its NAV changes."""
    out, index, peak = [], 1.0, 1.0
    for r, ret in zip(rows, returns(rows, book)):
        index = index + ret if book == 'jev' else index * (1 + ret)
        peak = max(peak, index)
        out.append((r['day'], index - 1, index / peak - 1))
    return out


def stats(rows, book):
    """Period figures of one book's rows (oldest first): P&L, return, worst fall, day win rate and trades."""
    if not rows:
        return None
    line = curve(rows, book)
    index, worst = 1 + line[-1][1], min(d for _, _, d in line)
    active = [r for r in rows if r['pnl_day'] and abs(r['pnl_day']) >= 1]
    trades = [r['trades'] for r in rows if r['trades'] is not None]
    return {'pnl': sum(r['pnl_day'] or 0 for r in rows), 'ret': index - 1, 'mdd': worst, 'days': len(rows),
            'win_days': sum(1 for r in active if r['pnl_day'] > 0), 'active': len(active),
            'trades': sum(trades) if trades else None, 'fees': sum(r['fees_est'] or 0 for r in rows)}


def stale(runtime):
    """True when a source changed after the last build (the screen then rebuilds; a build takes about 2 s)."""
    runtime = Path(runtime)
    target = runtime / FILE
    if not target.exists():
        return True
    from engine_roles import ledger_root
    jev = ledger_root(runtime, 'jev')
    sources = [jev / 'orders.sqlite3', jev / 'account-snapshots.jsonl', runtime / 'vibe-longterm' / 'longterm.sqlite3',
               runtime / 'vibe-longterm' / 'virtual-account.sqlite3', runtime / 'shared-etf-paper' / 'trading-journal.sqlite3']
    built = target.stat().st_mtime
    return any(p.exists() and p.stat().st_mtime > built for p in sources)


def check(runtime):
    """Stored Jev realized total against the ledger's own: {'ledger', 'table', 'ok'}."""
    import engine_compare as ec
    from engine_roles import ledger_root
    ledger = sum(r['pnl'] for r in ec.journal(ledger_root(Path(runtime), 'jev') / 'orders.sqlite3')
                 if r['side'] == 'SELL' and r['pnl'] is not None)
    table = sum(r['realized_day'] or 0 for r in rows(runtime) if r['book'] == 'jev')
    return {'ledger': round(ledger), 'table': round(table), 'ok': abs(ledger - table) < 1}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--build', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    from src.config.paths import get_runtime_root
    runtime = get_runtime_root() / 'nhplug'
    if args.build:
        print(json.dumps(build(runtime)))
    if args.check or not args.build:
        print(json.dumps(check(runtime)))


if __name__ == '__main__':
    main()
