import os
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

import backup_trading_data as b
from sqlite_ro import ro_connect


class BackupTests(unittest.TestCase):
    def test_symlinks_are_not_copied_again_and_a_wal_database_copies_its_committed_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / 'nhplug', Path(temp) / 'backups'
            shared, retired = source / 'shared-etf-paper', source / 'jev-virtual'
            shared.mkdir(parents=True); retired.mkdir()
            db = shared / 'market-research.sqlite3'
            with sqlite3.connect(db) as c:
                c.execute('PRAGMA journal_mode=WAL'); c.execute('CREATE TABLE t (x)'); c.execute('INSERT INTO t VALUES (1)')
            (shared / 'events.jsonl').write_text('{}\n')
            os.symlink(db, retired / 'market-research.sqlite3')
            os.symlink(shared / 'events.jsonl', retired / 'events.jsonl')
            writer = sqlite3.connect(db, isolation_level=None)
            try:
                writer.execute('BEGIN EXCLUSIVE'); writer.execute('INSERT INTO t VALUES (2)')  # a service mid-write
                result = b.backup(source, target, date(2026, 11, 18))
            finally:
                writer.close()
            self.assertEqual(sorted(str(p.relative_to(target / '2026-11-18')) for p in (target / '2026-11-18').rglob('*') if p.is_file()),
                             ['shared-etf-paper/events.jsonl', 'shared-etf-paper/market-research.sqlite3'])
            self.assertEqual(result['files'], 2)
            with ro_connect(target / '2026-11-18/shared-etf-paper/market-research.sqlite3') as copy:
                self.assertEqual(copy.execute('SELECT x FROM t').fetchall(), [(1,)])


if __name__ == '__main__':
    unittest.main()
