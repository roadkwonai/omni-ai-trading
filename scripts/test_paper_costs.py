import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import nh_paper_isolated as p
import paper_costs as c

KST = ZoneInfo('Asia/Seoul')


class PaperCostTests(unittest.TestCase):
    def test_cost_is_measured_from_totals_and_compared_with_real_rates(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            with sqlite3.connect(folder / 'orders.sqlite3') as db:
                db.execute('CREATE TABLE fills (order_id TEXT, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
                db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?)', [
                    ('a', '114800', 'BUY', 1000, 1300.0, '2026-11-18T10:00:00+09:00'),
                    ('b', '114800', 'SELL', 1000, 1310.0, '2026-11-18T11:00:00+09:00'),   # ETF: no sell tax
                    ('c', '005930', 'BUY', 10, 200000.0, '2026-11-18T12:00:00+09:00'),
                    ('d', '005930', 'SELL', 10, 201000.0, '2026-11-18T13:00:00+09:00')])
            snaps = [{'time': '2026-11-18T09:10:00+09:00', 'total_assets': 5_000_000_000, 'evaluation': 0},
                     {'time': '2026-11-18T15:00:00+09:00', 'total_assets': 5_000_000_000 + 20_000 - 3_000, 'evaluation': 0}]
            (folder / 'account-snapshots.jsonl').write_text(''.join(json.dumps(s) + '\n' for s in snaps))
            r = c.day_costs(folder, '2026-11-18', etfs={'114800'})
        self.assertEqual((r['gross_pnl'], r['paper_cost'], r['net_pnl_paper']), (20_000, 3_000, 17_000))
        turnover = 1_300_000 + 1_310_000 + 2_000_000 + 2_010_000
        self.assertEqual(r['real_cost_estimate'], round(turnover * 0.00015 + 2_010_000 * 0.002))
        self.assertEqual(r['stock_sells'], 2_010_000)

    def test_too_few_snapshots_say_so(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(c.day_costs(td, '2026-11-18', etfs=set())['status'], 'no_snapshots')

    def test_runner_snapshot_is_throttled_and_skips_partial_pages(self):
        summary = {'orr_pbl_amt1': 1, 'dca': 2, 'nxt2_dd_dca': 3, 'tot_aet_amt': 4, 'tot_byn_amt': 5, 'tot_eal_amt': 6}
        t = datetime(2026, 11, 18, 9, 10, tzinfo=KST)
        with tempfile.TemporaryDirectory() as td:
            p.account_snapshot(td, summary, t)
            p.account_snapshot(td, summary, t.replace(minute=12))      # within 5 minutes: skipped
            p.account_snapshot(td, summary, t.replace(minute=16))
            p.account_snapshot(td, {'dca': 2}, t.replace(minute=30))    # partial page: skipped
            rows = [json.loads(l) for l in (Path(td) / 'account-snapshots.jsonl').read_text().splitlines()]
        self.assertEqual([r['time'][11:16] for r in rows], ['09:10', '09:16'])
        self.assertEqual(rows[0]['total_assets'], 4)


if __name__ == '__main__':
    unittest.main()
