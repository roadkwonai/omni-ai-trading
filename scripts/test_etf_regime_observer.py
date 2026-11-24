import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from etf_regime_observer import claim_attempt, observe, publish, run
from etf_universe import SYMBOLS
from src import file_lock

KST = ZoneInfo('Asia/Seoul')


def fixture(now):
    minute = []
    for i in range(10):
        t = now.replace(second=0, microsecond=0) - timedelta(minutes=12-i)
        minute.append({'bsop_time': t.strftime('%H%M%S'), 'stck_oprc': '100', 'stck_hgpr': '103',
                       'stck_lwpr': '99', 'stck_prpr': str(100 + i % 2), 'vol': '100'})
    rows = []
    day = now.date()
    while len(rows) < 22:
        if day.weekday() < 5:
            rows.append({'bsop_date': day.strftime('%Y%m%d'), 'stck_oprc': '100', 'stck_hgpr': '103',
                         'stck_lwpr': '99', 'stck_prpr': '101', 'vol': '100'})
        day -= timedelta(days=1)
    return {'time': now.isoformat(), 'event': 'decision',
            'market_bars': {name: list(reversed(minute)) for name in SYMBOLS}}, rows


class Reader:
    server_name = 'nhplug_readonly'
    server_config = SimpleNamespace(args=['--mode', 'readonly'])

    def __init__(self, now, rows):
        self.now, self.rows, self.calls = now, rows, []
        self.wrong_code = False
        self.stale = False
        event, _ = fixture(now)
        self.minute = [{**row, 'bsop_date': now.strftime('%Y%m%d')}
                       for row in event['market_bars']['K200']]

    def call_tool(self, name, payload):
        self.calls.append((name, payload))
        code = payload['input']['iem_cd']
        at = self.now - timedelta(minutes=5) if self.stale else self.now
        if payload['input']['gubun'] == '5':
            return {'status': 'ok', 'data': {'rsp_cd': '00000',
                    'Output_0': {'iem_cd': code}, 'Output_1': self.minute}}
        return {'status': 'ok', 'data': {'rsp_cd': '00000',
            'Output_0': {'iem_cd': '000000' if self.wrong_code else code,
                         'qry_date': at.strftime('%Y%m%d'), 'qry_time': at.strftime('%H%M%S')},
            'Output_1': self.rows}}


class ObserverTest(unittest.TestCase):
    def test_seven_readonly_quotes_and_atomic_publish(self):
        now = datetime(2026, 11, 11, 11, 10, tzinfo=KST)
        event, days = fixture(now)
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'events.jsonl'
            source.write_text(json.dumps(event) + '\n')
            reader = Reader(now, days)
            result = observe(reader, source, lambda: now)
            self.assertEqual(result['status'], 'ready')
            self.assertEqual(set(result['symbols']), set(SYMBOLS))
            self.assertEqual(len(reader.calls), 7)
            self.assertTrue(all(name == 'call_api' and payload['operationId'] == 'krstockQuotePeriod'
                                and payload['input']['gubun'] == '1' for name, payload in reader.calls))
            self.assertTrue(all(row['purpose'] == 'research_only' for row in result['symbols'].values()))
            path = Path(td) / 'regime-observation.json'
            publish(path, result)
            self.assertEqual(json.loads(path.read_text()), result)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_latest_complete_holding_event_reuses_bars(self):
        now = datetime(2026, 11, 11, 11, 10, tzinfo=KST)
        event, days = fixture(now)
        holding = {**event, 'event': 'v2_holding_decision', 'choice': 'HOLD'}
        older = {**event, 'time': (now - timedelta(minutes=3)).isoformat()}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'events.jsonl'
            path.write_text(json.dumps(older) + '\n' + json.dumps(holding) + '\n')
            reader = Reader(now, days)
            result = observe(reader, path, lambda: now)
            self.assertEqual(result['status'], 'ready')
            self.assertEqual(result['minute_event_time'], now.isoformat())
            self.assertEqual(len(reader.calls), 7)
            self.assertTrue(all(row['minute_source'] == 'decision_event' for row in result['symbols'].values()))

    def test_jev_only_market_context_reuses_bars_without_claiming_decision(self):
        now = datetime(2026, 11, 11, 11, 10, tzinfo=KST)
        event, days = fixture(now)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'events.jsonl'
            for context in ('holding', 'entry'):
                market = {**event, 'event': 'market_context', 'universe': 'etf7', 'context': context}
                market.pop('choice', None)
                path.write_text(json.dumps(market) + '\n')
                reader = Reader(now, days)
                result = observe(reader, path, lambda: now)
                self.assertEqual(result['status'], 'ready')
                self.assertEqual(len(reader.calls), 7)
                self.assertTrue(all(row['minute_source'] == 'market_context' for row in result['symbols'].values()))

    def test_market_context_with_watchlist_charts_still_lends_its_etf_bars(self):
        # 11-20: the runner logs ETF7 plus watchlist stock charts; an exact-set match never matched, so every run read
        # the seven minute charts from NH again
        now = datetime(2026, 11, 11, 11, 10, tzinfo=KST)
        event, days = fixture(now)
        market = {**event, 'event': 'market_context', 'universe': 'etf7', 'context': 'entry',
                  'market_bars': {**event['market_bars'], '005930': event['market_bars']['K200']}}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'events.jsonl'
            path.write_text(json.dumps(market) + '\n')
            reader = Reader(now, days)
            result = observe(reader, path, lambda: now)
        self.assertEqual(result['status'], 'ready')
        self.assertFalse([p for _, p in reader.calls if p['input']['gubun'] == '5'])

    def test_the_daily_history_is_read_once_a_day_with_a_shared_cache(self):
        # 11-20: today's daily row is dropped, so the history does not change intraday; it was read every two minutes
        now = datetime(2026, 11, 11, 11, 10, tzinfo=KST)
        event, days = fixture(now)
        cache = {}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'events.jsonl'
            path.write_text(json.dumps(event) + '\n')
            reader = Reader(now, days)
            first = observe(reader, path, lambda: now, daily_cache=cache)
            later = now + timedelta(minutes=2)
            path.write_text(json.dumps(fixture(later)[0]) + '\n')
            again = Reader(later, days)
            second = observe(again, path, lambda: later, daily_cache=cache)
            tomorrow = now + timedelta(days=1)
            path.write_text(json.dumps(fixture(tomorrow)[0]) + '\n')
            fresh = Reader(tomorrow, fixture(tomorrow)[1])
            observe(fresh, path, lambda: tomorrow, daily_cache=cache)
        self.assertEqual((first['status'], second['status']), ('ready', 'ready'))
        self.assertEqual((len(reader.calls), len(again.calls), len(fresh.calls)), (7, 0, 7))
        self.assertEqual(second['symbols']['K200']['daily_sma_gap_pct'], first['symbols']['K200']['daily_sma_gap_pct'])

    def test_invalid_market_context_falls_back_to_readonly_minutes(self):
        now = datetime(2026, 11, 11, 11, 10, tzinfo=KST)
        event, days = fixture(now)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'events.jsonl'
            variants = [
                {**event, 'event': 'market_context', 'universe': 'wrong', 'context': 'entry'},
                {**event, 'event': 'market_context', 'universe': 'etf7', 'context': 'other'},
                {**event, 'event': 'market_context', 'universe': 'etf7', 'context': 'entry',
                 'market_bars': {'K200': event['market_bars']['K200']}},
                {**event, 'event': 'market_context', 'universe': 'etf7', 'context': 'entry',
                 'time': (now - timedelta(minutes=3)).isoformat()},
            ]
            for market in variants:
                with self.subTest(variant=market.get('context'), time=market['time']):
                    path.write_text(json.dumps(market) + '\n')
                    reader = Reader(now, days)
                    result = observe(reader, path, lambda: now)
                    self.assertEqual(result['status'], 'ready')
                    self.assertEqual(len(reader.calls), 14)
                    self.assertTrue(all(row['minute_source'] == 'nh_direct' for row in result['symbols'].values()))

    def test_market_context_rejects_incomplete_minute(self):
        now = datetime(2026, 11, 11, 11, 10, tzinfo=KST)
        event, days = fixture(now)
        event.update(event='market_context', universe='etf7', context='holding')
        event['market_bars']['K200'] = [dict(row) for row in event['market_bars']['K200']]
        event['market_bars']['K200'][0]['bsop_time'] = now.strftime('%H%M%S')
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'events.jsonl'; path.write_text(json.dumps(event) + '\n')
            reader = Reader(now, days)
            result = observe(reader, path, lambda: now)
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(result['symbols']['K200']['mode'], 'wait')
            self.assertEqual(result['symbols']['K200']['data_quality'], 'invalid')
            self.assertEqual(len(reader.calls), 6)

    def test_partial_holding_event_does_not_borrow_unverified_bars(self):
        now = datetime(2026, 11, 11, 11, 10, tzinfo=KST)
        event, days = fixture(now)
        event['event'] = 'v2_holding_decision'
        event['market_bars'].pop('INV')
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'events.jsonl'; path.write_text(json.dumps(event) + '\n')
            reader = Reader(now, days)
            result = observe(reader, path, lambda: now)
            self.assertEqual(result['status'], 'ready')
            self.assertEqual(len(reader.calls), 14)
            self.assertTrue(all(row['minute_source'] == 'nh_direct' for row in result['symbols'].values()))

    def test_stale_holding_event_falls_back_to_fresh_quotes(self):
        now = datetime(2026, 11, 11, 11, 10, tzinfo=KST)
        event, days = fixture(now)
        event.update(event='v2_holding_decision', time=(now - timedelta(minutes=3)).isoformat())
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'events.jsonl'; path.write_text(json.dumps(event) + '\n')
            reader = Reader(now, days)
            result = observe(reader, path, lambda: now)
            self.assertEqual(result['status'], 'ready')
            self.assertEqual(len(reader.calls), 14)
            self.assertTrue(all(row['minute_source'] == 'nh_direct' for row in result['symbols'].values()))

    def test_holding_event_rejects_incomplete_minute_for_that_symbol(self):
        now = datetime(2026, 11, 11, 11, 10, tzinfo=KST)
        event, days = fixture(now)
        event['event'] = 'v2_holding_decision'
        event['market_bars']['K200'] = [dict(row) for row in event['market_bars']['K200']]
        event['market_bars']['K200'][0]['bsop_time'] = now.strftime('%H%M%S')
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'events.jsonl'; path.write_text(json.dumps(event) + '\n')
            reader = Reader(now, days)
            result = observe(reader, path, lambda: now)
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(result['symbols']['K200']['mode'], 'wait')
            self.assertEqual(result['symbols']['K200']['data_quality'], 'invalid')
            self.assertEqual(len(reader.calls), 6)

    def test_stale_or_invalid_event_falls_back_to_readonly_minute_data(self):
        now = datetime(2026, 11, 11, 11, 10, tzinfo=KST)
        event, days = fixture(now)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'events.jsonl'
            for stamp in (now - timedelta(minutes=3), now + timedelta(minutes=1)):
                event['time'] = stamp.isoformat()
                path.write_text(json.dumps(event) + '\n')
                reader = Reader(now, days)
                result = observe(reader, path, lambda: now)
                self.assertEqual(result['status'], 'ready')
                self.assertEqual(len(reader.calls), 14)
                self.assertTrue(all(row['minute_source'] == 'nh_direct' for row in result['symbols'].values()))

    def test_stale_wrong_symbol_future_daily_and_malformed_input_wait(self):
        now = datetime(2026, 11, 11, 11, 10, tzinfo=KST)
        event, days = fixture(now)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'events.jsonl'; path.write_text(json.dumps(event) + '\n')
            for variant in ('stale', 'wrong_code', 'future'):
                reader = Reader(now, [dict(row) for row in days])
                if variant == 'future':
                    reader.rows[0]['bsop_date'] = (now + timedelta(days=1)).strftime('%Y%m%d')
                else:
                    setattr(reader, variant, True)
                with self.subTest(variant=variant):
                    result = observe(reader, path, lambda: now)
                    self.assertEqual(result['status'], 'unavailable')
                    self.assertTrue(all(row['mode'] == 'wait' for row in result['symbols'].values()))
            path.write_text('{')
            self.assertEqual(observe(Reader(now, days), path, lambda: now)['status'], 'ready')

    def test_direct_minute_failure_remains_wait(self):
        now = datetime(2026, 11, 11, 11, 10, tzinfo=KST)
        event, days = fixture(now)
        event['time'] = (now - timedelta(minutes=3)).isoformat()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'events.jsonl'; path.write_text(json.dumps(event) + '\n')
            reader = Reader(now, days)
            for row in reader.minute:
                row['bsop_date'] = (now - timedelta(days=1)).strftime('%Y%m%d')
            result = observe(reader, path, lambda: now)
            self.assertEqual(result['status'], 'unavailable')
            self.assertTrue(all(row['mode'] == 'wait' for row in result['symbols'].values()))

    def test_persisted_rate_limit_prevents_overlap_after_crash(self):
        now = datetime(2026, 11, 11, 11, 10, tzinfo=KST)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'attempt.json'
            self.assertTrue(claim_attempt(path, now))
            self.assertFalse(claim_attempt(path, now + timedelta(seconds=119)))
            self.assertFalse(claim_attempt(path, now - timedelta(seconds=1)))
            self.assertTrue(claim_attempt(path, now + timedelta(seconds=120)))

    def test_watch_runs_at_minimum_interval_and_stops_at_close(self):
        now = datetime(2026, 11, 11, 14, 56, 0, tzinfo=KST)
        event, days = fixture(now)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'events.jsonl').write_text(json.dumps(event) + '\n')
            current = [now]
            requests = []
            def sleep(seconds):
                current[0] += timedelta(seconds=seconds)
            def fake_observe(reader, path, clock, sleeper, daily_cache=None):
                requests.append(clock())
                return {'time': clock().isoformat(), 'status': 'unavailable', 'symbols': {}}
            with patch('etf_regime_observer.observe', side_effect=fake_observe):
                result = run(root, Reader(now, days), watch=True, clock=lambda: current[0], sleeper=sleep)
            self.assertEqual(result, 'outside_session')
            self.assertEqual(len(requests), 2)
            self.assertGreaterEqual((requests[1] - requests[0]).total_seconds(), 120)
            self.assertEqual(json.loads((root / 'regime-observation.json').read_text())['time'], requests[-1].isoformat())

    def test_watch_started_at_nine_waits_until_0910_before_observing(self):
        now = datetime(2026, 11, 16, 9, 0, tzinfo=KST)
        _, days = fixture(now)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); current = [now]; observations = []; sleeps = []
            def sleep(seconds):
                sleeps.append(seconds)
                current[0] += timedelta(seconds=seconds)
                if observations:
                    current[0] = current[0].replace(hour=15, minute=0)
            def fake_observe(reader, path, clock, sleeper, daily_cache=None):
                observations.append(clock())
                return {'time': clock().isoformat(), 'status': 'ready', 'symbols': {}}
            with patch('etf_regime_observer.observe', side_effect=fake_observe):
                self.assertEqual(run(root, Reader(now, days), watch=True, clock=lambda: current[0], sleeper=sleep),
                                 'outside_session')
            self.assertEqual(observations, [now.replace(minute=10)])
            self.assertEqual(sum(sleeps[:-1]), 600)
            self.assertTrue(all(0 < seconds <= 30 for seconds in sleeps[:-1]))

    def test_watch_started_at_nine_survives_a_runner_restart_after_the_open(self):
        now = datetime(2026, 11, 17, 9, 0, tzinfo=KST)
        _, days = fixture(now)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); current = [now]; observations = []
            def sleep(seconds):
                current[0] += timedelta(seconds=seconds)
                if len(observations) == 1:
                    (root / 'STOP').touch()  # the runner's STOP-restart procedure, 09:36 on 11/17
                elif len(observations) == 2:
                    (root / 'STOP').unlink(missing_ok=True)
                    current[0] = current[0].replace(hour=15, minute=0)
            def fake_observe(reader, path, clock, sleeper, daily_cache=None):
                observations.append(clock())
                return {'time': clock().isoformat(), 'status': 'ready', 'symbols': {}}
            with patch('etf_regime_observer.observe', side_effect=fake_observe):
                self.assertEqual(run(root, Reader(now, days), watch=True, clock=lambda: current[0], sleeper=sleep),
                                 'outside_session')
            self.assertEqual(len(observations), 2)  # kept observing after the STOP appeared

    def test_watch_survives_a_runner_restart_while_waiting_for_ten_past_nine(self):
        """11-24 09:07: the shared runner's STOP-restart fell in the 09:00~09:10 wait and ended the observer until 09:58."""
        now = datetime(2026, 11, 24, 9, 6, 40, tzinfo=KST)
        _, days = fixture(now)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); current = [now]; observations = []
            def sleep(seconds):
                current[0] += timedelta(seconds=seconds)
                (root / 'STOP').touch() if current[0].minute < 8 else (root / 'STOP').unlink(missing_ok=True)
                if observations:
                    current[0] = current[0].replace(hour=15, minute=0)
            def fake_observe(reader, path, clock, sleeper, daily_cache=None):
                observations.append(clock())
                return {'time': clock().isoformat(), 'status': 'ready', 'symbols': {}}
            with patch('etf_regime_observer.observe', side_effect=fake_observe):
                self.assertEqual(run(root, Reader(now, days), watch=True, clock=lambda: current[0], sleeper=sleep), 'outside_session')
            self.assertEqual([t.strftime('%H:%M') for t in observations], ['09:10'])

    def test_watch_before_nine_waits_for_the_bell(self):  # user 2026-11-20: started 08:55, ready at 09:00
        now = datetime(2026, 11, 16, 8, 59, tzinfo=KST)
        _, days = fixture(now)
        with tempfile.TemporaryDirectory() as td:
            slept, current = [], [now]
            def sleep(seconds):
                slept.append(seconds); current[0] += timedelta(seconds=seconds)
                if current[0] >= now.replace(hour=9, minute=0):
                    current[0] = now.replace(hour=15, minute=0)
            run(Path(td), Reader(now, days), watch=True, clock=lambda: current[0], sleeper=sleep)
            self.assertEqual(slept[:2], [30, 30])  # waited the minute before the open, in 30-second steps

    def test_watch_preopen_weekend_and_before_nine_exit(self):
        for now in (datetime(2026, 11, 14, 9, 0, tzinfo=KST), datetime(2026, 11, 16, 7, 59, tzinfo=KST)):
            _, days = fixture(now)
            with self.subTest(now=now), tempfile.TemporaryDirectory() as td:
                reader = Reader(now, days)
                self.assertEqual(run(Path(td), reader, watch=True, clock=lambda: now,
                                     sleeper=lambda _: self.fail('outside session must not sleep')), 'outside_session')
                self.assertFalse(reader.calls)

    def test_watch_error_records_wait_and_continues(self):
        now = datetime(2026, 11, 11, 14, 58, 0, tzinfo=KST)
        _, days = fixture(now)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); current = [now]
            def sleep(seconds): current[0] += timedelta(seconds=seconds)
            with patch('etf_regime_observer.observe', side_effect=OSError('private detail')):
                self.assertEqual(run(root, Reader(now, days), watch=True, clock=lambda: current[0], sleeper=sleep),
                                 'outside_session')
            output = (root / 'regime-observation.json').read_text()
            result = json.loads(output)
            self.assertEqual(result['status'], 'unavailable')
            self.assertTrue(all(row['mode'] == 'wait' for row in result['symbols'].values()))
            self.assertNotIn('private detail', output)

    def test_watch_recovers_on_next_cycle_after_error(self):
        now = datetime(2026, 11, 11, 14, 56, 0, tzinfo=KST)
        _, days = fixture(now)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); current = [now]; attempts = []
            def sleep(seconds): current[0] += timedelta(seconds=seconds)
            def fake_observe(reader, path, clock, sleeper, daily_cache=None):
                attempts.append(clock())
                if len(attempts) == 1:
                    raise TimeoutError('private detail')
                return {'time': clock().isoformat(), 'status': 'ready', 'symbols': {}}
            with patch('etf_regime_observer.observe', side_effect=fake_observe):
                self.assertEqual(run(root, Reader(now, days), watch=True, clock=lambda: current[0], sleeper=sleep),
                                 'outside_session')
            self.assertEqual(len(attempts), 2)
            self.assertGreaterEqual((attempts[1] - attempts[0]).total_seconds(), 120)
            self.assertEqual(json.loads((root / 'regime-observation.json').read_text())['status'], 'ready')

    def test_watch_uses_single_process_lock(self):
        now = datetime(2026, 11, 11, 11, 10, tzinfo=KST)
        _, days = fixture(now)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with (root / 'regime-observer.lock').open('a') as lock:
                file_lock.lock(lock, blocking=False)
                self.assertEqual(run(root, Reader(now, days), watch=True, clock=lambda: now), 'already_running')
            self.assertFalse((root / 'regime-observation.json').exists())

    def test_watch_invalid_rate_limit_is_unavailable_without_fetching(self):
        now = datetime(2026, 11, 11, 14, 58, 0, tzinfo=KST)
        _, days = fixture(now)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); current = [now]
            (root / 'regime-last-attempt.json').write_text('{')
            def sleep(seconds): current[0] += timedelta(seconds=seconds)
            reader = Reader(now, days)
            self.assertEqual(run(root, reader, watch=True, clock=lambda: current[0], sleeper=sleep),
                             'outside_session')
            result = json.loads((root / 'regime-observation.json').read_text())
            self.assertEqual(result['status'], 'unavailable')
            self.assertTrue(all(row['reason'] == 'rate_limit_unavailable' for row in result['symbols'].values()))
            self.assertFalse(reader.calls)

    def test_non_readonly_adapter_is_rejected(self):
        now = datetime(2026, 11, 11, 11, 10, tzinfo=KST)
        event, days = fixture(now)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'events.jsonl'; path.write_text(json.dumps(event) + '\n')
            reader = Reader(now, days)
            reader.server_name = 'nhplug_paper'
            self.assertEqual(observe(reader, path, lambda: now)['status'], 'unavailable')
            self.assertFalse(reader.calls)

    def test_invalid_clock_or_closed_session_never_fetches(self):
        now = datetime(2026, 11, 11, 15, 5, tzinfo=KST)
        event, days = fixture(now)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'events.jsonl'; path.write_text(json.dumps(event) + '\n')
            for current in (now, now.replace(tzinfo=None)):
                reader = Reader(now, days)
                result = observe(reader, path, lambda: current)
                self.assertEqual(result['status'], 'unavailable')
                self.assertFalse(reader.calls)


if __name__ == '__main__':
    unittest.main()
