import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import backup_trading_data as b
import trading_day_start as t


class TradingDayStartTests(unittest.TestCase):
    def test_start_only_on_an_open_day_without_stop_or_a_running_runner(self):
        self.assertEqual(t.decide(True, False, False), 'start')
        self.assertEqual(t.decide(False, False, False), 'skip_closed')
        self.assertEqual(t.decide(True, True, False), 'skip_stop_file')
        self.assertEqual(t.decide(True, False, True), 'already_running')

    def test_weekend_holiday_and_unknown_table(self):
        kst = ZoneInfo('Asia/Seoul')
        self.assertEqual(t.trading_day(datetime(2026, 11, 14, 9, 10, tzinfo=kst)), (False, 'weekend'))
        with patch('kr_swing_v5_daily_data.krx_holidays', return_value={date(2026, 12, 25)}):
            self.assertEqual(t.trading_day(datetime(2026, 12, 25, 9, 10, tzinfo=kst)), (False, 'krx_holiday'))
            self.assertEqual(t.trading_day(datetime(2026, 12, 24, 9, 10, tzinfo=kst)), (True, 'trading_day'))
        with patch('kr_swing_v5_daily_data.krx_holidays', side_effect=OSError):
            self.assertEqual(t.trading_day(datetime(2026, 12, 24, 9, 10, tzinfo=kst))[0], True)


class BackupTests(unittest.TestCase):
    def test_databases_and_logs_are_copied_and_old_folders_pruned(self):
        with tempfile.TemporaryDirectory() as d:
            source, target = Path(d) / 'nhplug', Path(d) / 'backups'
            (source / 'shared').mkdir(parents=True)
            with sqlite3.connect(source / 'shared' / 'orders.sqlite3') as db:
                db.execute('CREATE TABLE fills (x)'); db.execute('INSERT INTO fills VALUES (1)')
            (source / 'shared' / 'events.jsonl').write_text('{}\n')
            (source / 'shared' / 'service.log').write_text('noise')
            (target / '2026-10-01').mkdir(parents=True)
            result = b.backup(source, target, date(2026, 11, 12))
            self.assertEqual(result['files'], 2)  # the log file is not a record
            self.assertEqual(result['removed'], ['2026-10-01'])
            with sqlite3.connect(target / '2026-11-12' / 'shared' / 'orders.sqlite3') as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM fills').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
