import tempfile
import unittest
from pathlib import Path

import research_store as r


class ResearchStoreTests(unittest.TestCase):
    def test_appends_and_reads_newest_first(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'm.sqlite3'
            self.assertTrue(r.record('movers', {'time': 't1', 'x': 1}, path))
            self.assertTrue(r.record('movers', {'time': 't2', 'x': 2}, path))
            self.assertTrue(r.record('watchlist', {'time': 't3'}, path))
            self.assertEqual([p['x'] for p in r.history('movers', path=path)], [2, 1])
            self.assertEqual(r.history('none', path=path), [])
            self.assertFalse(r.record('movers', {'bad': {1, 2}}, path))



class ModelInputTests(unittest.TestCase):
    def test_inputs_are_stored_once_per_identical_input(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'r.sqlite3'
            first = r.record_input('jev', 'candidate', {'state': {'b': 1, 'a': 2}}, {'choice': 'K200'}, path)
            again = r.record_input('jev', 'candidate', {'state': {'a': 2, 'b': 1}}, {'choice': 'K200'}, path)
            other = r.record_input('vibe', 'scores', {'state': {'a': 2, 'b': 1}}, {}, path)
            self.assertEqual(first, again)
            self.assertNotEqual(first, other)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM model_inputs').fetchone()[0], 2)
            self.assertIsNone(r.record_input('jev', 'q', {'x': 1}, {}, Path(d) / 'missing' / 'r.sqlite3'))


class ModelInputAnswerTests(unittest.TestCase):
    def test_a_different_answer_to_the_same_input_is_kept(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'r.sqlite3'
            a = r.record_input('jev', 'q', {'x': 1}, {'choice': 'K200'}, path)
            b = r.record_input('jev', 'q', {'x': 1}, {'choice': 'WAIT'}, path)
            self.assertNotEqual(a, b)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM model_inputs').fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()


class WalTests(unittest.TestCase):
    def test_every_writer_leaves_readers_unblocked_by_an_open_write(self):
        import sqlite3
        from sqlite_ro import ro_connect
        for write in (lambda p: r.record('movers', {'time': 't'}, p),
                      lambda p: r.record_input('jev', 'candidate', {'a': 1}, {}, p),
                      lambda p: r.record_evidence('d' * 64, 'jev', 'candidate', {'why': 'x'}, p)):
            with tempfile.TemporaryDirectory() as d:
                path = Path(d) / 'r.sqlite3'
                self.assertTrue(write(path))
                writer = sqlite3.connect(path, isolation_level=None)
                try:
                    writer.execute('BEGIN EXCLUSIVE')
                    writer.execute('CREATE TABLE pending (x)')
                    with ro_connect(path, timeout=0) as reader:
                        self.assertEqual(reader.execute("SELECT count(*) FROM sqlite_master WHERE name='pending'").fetchone()[0], 0)
                finally:
                    writer.close()
                self.assertTrue(write(path))  # a second write still works on the WAL file
