"""The day's trading cost two ways: what NH paper actually charged, and an estimate at real KRX rates. Read-only.

    .venv/bin/python scripts/paper_costs.py --day 2026-11-18

NH paper returns no fee or tax per fill, so the charged cost is measured from the account totals the runner records
(account-snapshots.jsonl, nh_paper_isolated.account_snapshot) between the day's first and last snapshot:

    gross P&L   = sells - buys + (evaluation_last - evaluation_first)      (fills from orders.sqlite3 in that window)
    paper cost  = gross P&L - (total_assets_last - total_assets_first)

The real estimate is commission on both sides plus the securities transaction tax on stock sells (domestic ETFs are
exempt). The rates are estimates to confirm against the broker's fee table; pass --commission / --sell-tax to change.
Use both when judging a strategy on paper results (11/17 review: a paper-only cost rate understates real results).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

COMMISSION_PCT = 0.015  # estimate: online commission incl. exchange fees, both sides — check the broker's fee table
SELL_TAX_PCT = 0.20     # estimate: 2026 securities transaction tax on stock sells (KOSPI incl. rural tax, KOSDAQ) — check


def day_costs(folder, day, *, etfs, commission=COMMISSION_PCT, sell_tax=SELL_TAX_PCT):
    folder = Path(folder)
    try:
        rows = [json.loads(l) for l in (folder / 'account-snapshots.jsonl').read_text().splitlines() if l.strip()]
    except OSError:
        rows = []
    rows = [r for r in rows if str(r.get('time', '')).startswith(day)]
    if len(rows) < 2:
        return {'day': day, 'status': 'no_snapshots', 'snapshots': len(rows)}
    first, last = rows[0], rows[-1]
    with sqlite3.connect(f'file:{folder / "orders.sqlite3"}?mode=ro', uri=True) as db:
        fills = db.execute('SELECT symbol, side, quantity, price FROM fills WHERE time>? AND time<=?',
                           (first['time'], last['time'])).fetchall()
    buys = sum(q * p for _, side, q, p in fills if side == 'BUY')
    sells = sum(q * p for _, side, q, p in fills if side == 'SELL')
    stock_sells = sum(q * p for code, side, q, p in fills if side == 'SELL' and code not in etfs)
    gross = sells - buys + (last['evaluation'] - first['evaluation'])
    paper = gross - (last['total_assets'] - first['total_assets'])
    turnover = buys + sells
    real = turnover * commission / 100 + stock_sells * sell_tax / 100
    pct = lambda value: round(value / turnover * 100, 4) if turnover else None
    return {'day': day, 'status': 'ok', 'window': [first['time'], last['time']], 'fills': len(fills),
            'buys': round(buys), 'sells': round(sells), 'stock_sells': round(stock_sells), 'gross_pnl': round(gross),
            'paper_cost': round(paper), 'paper_cost_pct_of_turnover': pct(paper),
            'real_cost_estimate': round(real), 'real_cost_pct_of_turnover': pct(real),
            'net_pnl_paper': round(gross - paper), 'net_pnl_real_estimate': round(gross - real),
            'rates': {'commission_pct': commission, 'sell_tax_pct': sell_tax, 'etf_sell_tax': 0}}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--day', required=True, help='YYYY-MM-DD')
    parser.add_argument('--folder', default=None, help='runner folder (default: the shared paper runner)')
    parser.add_argument('--commission', type=float, default=COMMISSION_PCT)
    parser.add_argument('--sell-tax', type=float, default=SELL_TAX_PCT)
    args = parser.parse_args()
    from etf_universe import SYMBOLS
    from src.config.paths import get_runtime_root
    folder = args.folder or get_runtime_root() / 'nhplug' / 'shared-etf-paper'
    print(json.dumps(day_costs(folder, args.day, etfs=set(SYMBOLS.values()), commission=args.commission,
                               sell_tax=args.sell_tax), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
