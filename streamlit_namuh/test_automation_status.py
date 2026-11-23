import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import automation_status as status


class AutomationStatusTest(unittest.TestCase):
    def test_process_cache_peek_never_runs_launchctl_and_failure_expires(self):
        with patch.object(status.time, 'monotonic', return_value=100), \
                patch.object(status.subprocess, 'run', side_effect=OSError) as run:
            status.forget_processes()
            self.assertIsNone(status.peek_processes(max_age=5))
            run.assert_not_called()
            self.assertTrue(all(value == '확인 불가' for value in status.processes(max_age=5).values()))
            self.assertTrue(all(value == '확인 불가' for value in status.peek_processes(max_age=5).values()))
            status.processes(max_age=5)
            self.assertEqual(run.call_count, 1)
        with patch.object(status.time, 'monotonic', return_value=106):
            self.assertIsNone(status.peek_processes(max_age=5))
        status.forget_processes()

    def test_processes_distinguishes_running_stopped_and_missing(self):
        result = SimpleNamespace(stdout='PID Status Label\n123 0 com.vibetrading.shared-etf-paper\n- 0 com.vibetrading.live-follow-1\n'
                                        '9 0 com.vibetrading.live-policy-auto\n')
        with patch.object(status.subprocess, 'run', return_value=result):
            states = status.processes(max_age=0)
        self.assertEqual((states['공유 모의 ETF 실행기'], states['실계좌 1 미러 실행기'], states['장전 사전 점검']),
                         ('실행 중', '종료', '미등록'))
        with patch.object(status.subprocess, 'run', side_effect=OSError):
            self.assertTrue(all(value == '확인 불가' for value in status.processes(max_age=0).values()))

    def test_the_service_list_is_every_defined_launchd_service_and_nothing_retired(self):
        # 11-18: a hand list kept 4 retired services (shown 미등록, "등록돼 있어야 합니다") and missed 8 real ones
        from launchd_services import SERVICES
        self.assertEqual(set(status.LABELS.values()), {f'com.vibetrading.{name}' for name in SERVICES})
        self.assertEqual(set(status.NAMES), set(SERVICES))  # each has a Korean name
        self.assertEqual(len(set(status.LABELS)), len(SERVICES))

    def test_learning_reads_existing_databases_without_creating_missing_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            paper = root / 'nhplug' / 'isolated-paper'
            paper.mkdir(parents=True)
            with sqlite3.connect(paper / 'paper-observations.sqlite3') as db:
                db.execute('create table price_observations(status text)')
                db.executemany('insert into price_observations values (?)', [('observed',), ('insufficient',)])
            result = status.learning(root)
            self.assertEqual(result['observations'], {'전체': 2, '후속 가격 확인': 1, '후속 가격 부족': 1})
            self.assertIsNone(result['orders'])
            self.assertFalse((paper / 'orders.sqlite3').exists())

    def test_jev_research_counts_unique_inputs_and_missing_is_not_zero(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            paper = root / 'nhplug' / 'isolated-paper'
            paper.mkdir(parents=True)
            model = paper / 'paper-model.sqlite3'
            with sqlite3.connect(model) as db:
                db.execute('create table runs(digest text)')
                db.execute('create table predictions(source text)')
            self.assertIsNone(status.learning(root)['jev_model'])
            with sqlite3.connect(model) as db:
                db.execute('create table jev_runs(digest text)')
                db.execute('create table jev_predictions(run_digest text, source text, offset integer, symbol text)')  # kept from before 11/18
                db.execute('create table jev_predictions_latest(run_digest text, source text, offset integer, symbol text)')
                db.executemany('insert into jev_runs values (?)', [('a',), ('b',)])
                db.executemany('insert into jev_predictions values (?,?,?,?)', [
                    ('a', 'source', 10, 'INV'), ('a', 'source', 30, 'LEV'), ('a', 'source', 40, 'K200')])
                db.executemany('insert into jev_predictions_latest values (?,?,?,?)', [
                    ('b', 'source', 10, 'INV'), ('b', 'source', 20, 'K200')])
            self.assertEqual(status.learning(root)['jev_model'], {'연구 실행': 2, '서로 다른 예측 대상': 2})

    def test_shared_learning_reads_only_its_own_journal(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            isolated = root / 'nhplug' / 'isolated-paper'
            shared = root / 'nhplug' / 'shared-etf-paper'
            isolated.mkdir(parents=True)
            shared.mkdir(parents=True)
            for path, count in ((isolated, 2), (shared, 1)):
                with sqlite3.connect(path / 'trading-ledger.sqlite3') as db:
                    db.execute('create table events_v2(event text)')
                    db.executemany('insert into events_v2 values (?)', [('decision',)] * count)
            self.assertEqual(status.learning(root)['ledger'], {'이벤트': 2})
            self.assertEqual(status.learning(root, shared=True)['ledger'], {'이벤트': 1})
            self.assertIsNone(status.learning(root, shared=True)['orders'])

    def test_events_are_allowlisted_and_invalid_data_is_not_silently_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'events.jsonl'
            path.write_text(json.dumps({'time': 'now', 'action': 'HOLD', 'account': 'private'}) + '\n')
            self.assertNotIn('account', status.recent_events(path)[0])
            path.write_text('{invalid')
            self.assertIsNone(status.recent_events(path))

    def test_counts_are_reused_until_the_file_changes_and_the_minute_passes(self):
        # P4: count(*) over the 170 MB ledger and 371 MB model database ran on every 운영 상태 rerun
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'ledger.sqlite3'
            with sqlite3.connect(path) as db:
                db.execute('create table events(event text)')
                db.execute("insert into events values ('a')")
            query = {'n': 'select count(*) from events'}
            self.assertEqual(status.counts(path, query), {'n': 1})
            with patch.object(status.sqlite3, 'connect', side_effect=AssertionError('read again')):
                self.assertEqual(status.counts(path, query), {'n': 1})  # unchanged file: no query
            with sqlite3.connect(path) as db:
                db.execute("insert into events values ('b')")
            self.assertEqual(status.counts(path, query), {'n': 1})  # changed, but read less than a minute ago
            with patch.object(status, 'COUNT_CACHE_S', 0):
                self.assertEqual(status.counts(path, query), {'n': 2})

class PaperControlTest(unittest.TestCase):
    def test_shared_stop_is_independent_and_preserves_existing_reason(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            status.stop_shared_paper(root)
            shared = root / 'nhplug/shared-etf-paper/STOP'
            isolated = root / 'nhplug/isolated-paper/STOP'
            self.assertEqual(shared.read_text(), 'streamlit-shared-etf-stop-v1\n')
            self.assertFalse(isolated.exists())
            shared.write_text('다른 중단 사유')
            status.stop_shared_paper(root)
            self.assertEqual(shared.read_text(), '다른 중단 사유')


class LivePauseReportTest(unittest.TestCase):
    def test_pause_is_reported_from_the_saved_marker_not_a_retired_service(self):
        # D4: stop_live asked launchd about com.vibetrading.live-policy-auto (unregistered since the mirror took over),
        # so a saved pause was always reported as a failure
        import subprocess
        from nh_live_buy_control import buy_allowed
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            missing = subprocess.CalledProcessError(113, ['launchctl', 'list'])
            with patch.object(status.subprocess, 'run', side_effect=missing):
                self.assertTrue(status.stop_live(root))
            self.assertFalse(buy_allowed(root / 'nhplug'))
