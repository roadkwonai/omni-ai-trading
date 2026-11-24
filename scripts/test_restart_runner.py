import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import restart_runner as r

KST = ZoneInfo('Asia/Seoul')
T0 = datetime(2026, 11, 17, 13, 13, 0, tzinfo=KST)


class FakeRunner:
    """launchctl + a runner that writes `stopped` when STOP appears and a first cycle after kickstart."""

    def __init__(self, folder, stops=True):
        self.folder, self.stops, self.alive, self.calls, self.now = folder, stops, True, [], T0

    def clock(self):
        return self.now

    def write(self, event):
        with (self.folder / 'events.jsonl').open('a') as f:
            f.write(json.dumps({'time': self.now.isoformat(), **event}) + '\n')

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)
        if self.alive and self.stops and (self.folder / 'STOP').exists():
            self.write({'event': 'stopped', 'reason': 'session or STOP', 'remaining': {}, 'reconciliation_required': False})
            self.alive = False
        elif self.alive and any(c[1] == 'kickstart' for c in self.calls) and not getattr(self, 'cycled', False):
            self.write({'event': 'market_context'})
            self.cycled = True

    def run(self, args, **kwargs):
        self.calls.append(args)
        if args[1] == 'kickstart':
            self.alive = True
            self.write({'event': 'strategy_settings'})
        label = 'com.vibetrading.' + r.RUNNERS['shared'][0]
        return SimpleNamespace(returncode=0, stdout=f"{'123' if self.alive else '-'}\t0\t{label}\n")


class RestartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / 'shared-etf-paper'
        self.folder.mkdir()
        with sqlite3.connect(self.folder / 'orders.sqlite3') as db:
            db.execute('CREATE TABLE intents (id TEXT, status TEXT)')
        self.holdings({})

    def holdings(self, rows):
        (self.folder / 'account-state.json').write_text(json.dumps({'holdings': rows}))

    def go(self, fake, **kw):
        return r.restart('shared', 'test', root=self.root, run=fake.run, sleep=fake.sleep, clock=fake.clock, timeout=0.5, **kw)

    def test_the_documented_procedure_runs_in_order_and_is_logged(self):
        fake = FakeRunner(self.folder)
        result = self.go(fake)
        self.assertEqual(result['result'], 'restarted')
        self.assertFalse((self.folder / 'STOP').exists())
        self.assertEqual([c[1] for c in fake.calls if c[1] == 'kickstart'], ['kickstart'])
        logged = [json.loads(l) for l in (self.folder / 'restarts.jsonl').read_text().splitlines()]
        self.assertEqual((logged[-1]['result'], logged[-1]['reason']), ('restarted', 'test'))
        self.assertIn('watch_paused_seconds', logged[-1])

    def test_refuses_with_an_unconfirmed_order_or_a_holding_near_its_stop(self):
        with sqlite3.connect(self.folder / 'orders.sqlite3') as db:
            db.execute("INSERT INTO intents VALUES ('a', 'acknowledged')")
        self.assertEqual(self.go(FakeRunner(self.folder))['result'], 'refused')
        with sqlite3.connect(self.folder / 'orders.sqlite3') as db:
            db.execute("UPDATE intents SET status='terminal'")
        self.holdings({'114800': {'phs_pr': 1343, 'now_pr': 1322}})  # -1.56%, within 0.5%p of the 2% stop
        fake = FakeRunner(self.folder)
        refused = self.go(fake)
        self.assertEqual((refused['result'], list(refused['near_stop'])), ('refused', ['114800']))
        self.assertFalse(any(c[1] == 'kickstart' for c in fake.calls))
        self.assertEqual(self.go(FakeRunner(self.folder), force=True)['result'], 'restarted')

    def test_a_holding_near_its_regime_tightened_stop_refuses(self):
        """In a 하락장 the runner stops a long ETF at -1% (regime_stop_levels), not at the plan's 2%: -0.8% is 0.2%p away."""
        import market_context
        (self.folder / 'exit-plans.json').write_text(json.dumps({'069500': {'profile': 'defensive', 'stop_pct': 2.0,
                                                                            'half_pct': 1.0, 'all_pct': 2.0}}))
        self.holdings({'069500': {'phs_pr': '10000', 'now_pr': '9920'}})
        self.assertEqual(r.near_stops(self.folder, 0.5), {})  # no regime known: the plan's 2%
        (self.folder / 'market-context.json').write_text(json.dumps({
            'time': datetime.now(KST).isoformat(), 'indices': {'KOSPI': {
                'name': '코스피', 'regime': market_context.regime([200 - i for i in range(80)]), 'change_pct': -1.2,
                'flow': '약세', 'investors_100m': {}, 'program_100m': {}, 'breadth': {'상승': 300, '하락': 500}}}}))
        self.assertEqual(list(r.near_stops(self.folder, 0.5)), ['069500'])

    def test_a_runner_that_does_not_stop_keeps_running_and_nothing_is_kickstarted(self):
        fake = FakeRunner(self.folder, stops=False)
        result = self.go(fake)
        self.assertEqual(result['result'], 'failed')
        self.assertFalse((self.folder / 'STOP').exists())
        self.assertFalse(any(c[1] == 'kickstart' for c in fake.calls))


if __name__ == '__main__':
    unittest.main()
