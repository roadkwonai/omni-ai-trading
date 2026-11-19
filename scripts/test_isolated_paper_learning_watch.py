import contextlib
import io
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import isolated_paper_learning_watch as watch


class LearningWatchTest(unittest.TestCase):
    def test_each_mode_uses_only_its_own_runtime_files(self):
        for args, directory in (([], 'isolated-paper'), (['--shared-default'], 'shared-etf-paper')):
            with self.subTest(args=args), patch.object(watch, 'datetime') as clock, \
                    patch.object(watch, 'ingest', return_value=0) as ingest, \
                    patch.object(watch, 'evaluate', return_value={}) as evaluate, \
                    patch.object(watch, 'train', return_value={}) as train, \
                    patch.object(watch, 'train_jev', return_value={}) as train_jev, \
                    patch.object(watch, 'learning_inputs', return_value=1), patch.object(watch, 'decision_mode', return_value=True), \
                    patch.object(watch.time, 'sleep', side_effect=InterruptedError), \
                    contextlib.redirect_stdout(io.StringIO()):
                clock.now.return_value = datetime(2026, 11, 11, 10, tzinfo=ZoneInfo('Asia/Seoul'))
                with self.assertRaises(InterruptedError):
                    watch.main(args)
                root = Path.home() / '.vibe-trading/nhplug' / directory
                ingest.assert_called_once_with(root / 'events.jsonl', root / 'trading-ledger.sqlite3')
                evaluate.assert_called_once_with(root / 'trading-ledger.sqlite3', root / 'paper-observations.sqlite3')
                train.assert_called_once_with(root / 'paper-observations.sqlite3', root / 'paper-model.sqlite3')
                train_jev.assert_called_once_with(root / 'trading-ledger.sqlite3', root / 'paper-observations.sqlite3', root / 'paper-model.sqlite3')

    def test_runtime_override_applies_to_both_modes(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'VIBE_TRADING_HOME': directory}):
            for args, name in (([], 'isolated-paper'), (['--shared-default'], 'shared-etf-paper')):
                with self.subTest(args=args), patch.object(watch, 'datetime') as clock, \
                        patch.object(watch, 'ingest', side_effect=OSError) as ingest, \
                        patch.object(watch.time, 'sleep', side_effect=InterruptedError), \
                        contextlib.redirect_stdout(io.StringIO()):
                    clock.now.return_value = datetime(2026, 11, 11, 10, tzinfo=ZoneInfo('Asia/Seoul'))
                    with self.assertRaises(InterruptedError):
                        watch.main(args)
                    root = Path(directory) / 'nhplug' / name
                    ingest.assert_called_once_with(root / 'events.jsonl', root / 'trading-ledger.sqlite3')

    def test_learning_reruns_only_on_new_learning_events_and_skips_decision_training_when_off(self):
        """Review 4 C3: every minute re-read the whole ledger even when nothing it learns from had arrived."""
        import json
        import sqlite3
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'VIBE_TRADING_HOME': directory}):
            root = Path(directory) / 'nhplug' / 'shared-etf-paper'
            root.mkdir(parents=True)
            (root / 'ai-modes.json').write_text(json.dumps({'version': 1, 'jev': True, 'decision': False}))
            ledger = root / 'trading-ledger.sqlite3'
            add = lambda event: sqlite3.connect(ledger).execute('INSERT INTO events_v2 VALUES (?)', (event,)).connection.commit()  # noqa: E731
            sqlite3.connect(ledger).execute('CREATE TABLE events_v2 (event TEXT)').connection.close()
            add('market_context')
            arrivals = iter([lambda: add('submitted'), lambda: add('jev_shadow'), lambda: (_ for _ in ()).throw(InterruptedError)])
            with patch.object(watch, 'datetime') as clock, patch.object(watch, 'ingest', return_value=0), \
                    patch.object(watch, 'evaluate', return_value={}) as evaluate, patch.object(watch, 'train', return_value={}) as train, \
                    patch.object(watch, 'train_jev', return_value={}) as train_jev, \
                    patch.object(watch.time, 'sleep', side_effect=lambda _: next(arrivals)()), \
                    contextlib.redirect_stdout(io.StringIO()):
                clock.now.return_value = datetime(2026, 11, 19, 10, tzinfo=ZoneInfo('Asia/Seoul'))
                with self.assertRaises(InterruptedError):
                    watch.main(['--shared-default'])
            self.assertEqual((evaluate.call_count, train_jev.call_count), (2, 2))  # round 2 only had an order event
            train.assert_not_called()  # the OpenAI decision mode is off: no decision picks to learn


if __name__ == '__main__':
    unittest.main()


def test_repeated_sqlite_errors_replace_the_process():
    """11-19 09:03~10:26: every round failed with DatabaseError in the long-running watcher (stale WAL handles) while a
    fresh process read the same files fine; after three in a row it now execs itself again."""
    import sqlite3
    calls = []
    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'VIBE_TRADING_HOME': directory}), \
            patch.object(watch, 'datetime') as clock, \
            patch.object(watch, 'ingest', side_effect=sqlite3.DatabaseError('database disk image is malformed')), \
            patch.object(watch.time, 'sleep'), \
            patch.object(watch.os, 'execv', side_effect=lambda *a: calls.append(a) or (_ for _ in ()).throw(SystemExit)), \
            contextlib.redirect_stdout(io.StringIO()) as out:
        clock.now.return_value = datetime(2026, 11, 19, 10, tzinfo=ZoneInfo('Asia/Seoul'))
        with unittest.TestCase().assertRaises(SystemExit):
            watch.main(['--shared-default'])
    assert len(calls) == 1 and calls[0][1][-1] == '--shared-default'
    assert out.getvalue().count('DatabaseError') == watch.DB_ERROR_RESTART and 'self_restart' in out.getvalue()


def test_other_errors_do_not_restart():
    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'VIBE_TRADING_HOME': directory}), \
            patch.object(watch, 'datetime') as clock, patch.object(watch, 'ingest', side_effect=OSError), \
            patch.object(watch.time, 'sleep', side_effect=[None] * 5 + [InterruptedError]), \
            patch.object(watch.os, 'execv') as execv, contextlib.redirect_stdout(io.StringIO()):
        clock.now.return_value = datetime(2026, 11, 19, 10, tzinfo=ZoneInfo('Asia/Seoul'))
        with unittest.TestCase().assertRaises(InterruptedError):
            watch.main(['--shared-default'])
    execv.assert_not_called()
