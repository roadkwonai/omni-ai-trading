import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config_store
import tunables


class ConfigStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        env = mock.patch.dict(os.environ, {'VIBE_TRADING_SETTINGS_HISTORY': str(Path(self.dir.name) / 'h.sqlite3'),
                                           'VIBE_TRADING_TUNABLES': str(Path(self.dir.name) / 'tunables.json')})
        env.start()
        self.addCleanup(env.stop)

    def test_changes_are_flattened_and_recorded_newest_first(self):
        self.assertEqual(config_store.changes({'a': {'b': 1}, 'c': 2}, {'a': {'b': 2}, 'c': 2, 'd': 3}),
                         [('a.b', 1, 2), ('d', None, 3)])
        self.assertEqual(config_store.record('strategy', {'x': 1}, {'x': 1}), 0)  # nothing changed, nothing written
        config_store.record('strategy', {'x': 1}, {'x': 2})
        config_store.record('prefs', {}, {'y': 'a'})
        rows = config_store.history(10)
        self.assertEqual([(r[1], r[2], r[3], r[4]) for r in rows], [('prefs', 'y', None, '"a"'), ('strategy', 'x', '1', '2')])
        self.assertEqual(len(config_store.history(10, 'strategy')), 1)

    def test_write_json_is_private_and_complete(self):
        path = Path(self.dir.name) / 'sub' / 's.json'
        config_store.write_json(path, {'k': '값'})
        self.assertEqual(json.loads(path.read_text()), {'k': '값'})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual([p.name for p in path.parent.iterdir()], ['s.json'])  # no temp file left behind

    def test_write_text_replaces_a_file_another_process_reads_in_one_step(self):
        path = Path(self.dir.name) / 'r' / 'strategy.md'
        config_store.write_text(path, '# 초안\n')
        config_store.write_text(path, '# 리포트\n본문\n')
        self.assertEqual(path.read_text(encoding='utf-8'), '# 리포트\n본문\n')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual([p.name for p in path.parent.iterdir()], ['strategy.md'])

    def test_jsonl_audit_moves_into_history_once(self):
        old = Path(self.dir.name) / 'live-order-switch.jsonl'
        old.write_text('{"time": "2026-11-13T10:00:00+09:00", "account": "live-1", "live_orders": true}\n')
        to_change = lambda e: (e['time'], f"{e['account']}.live_orders", None, e['live_orders'])
        self.assertEqual(config_store.import_jsonl(old, 'accounts', to_change), 1)
        self.assertEqual(config_store.import_jsonl(old, 'accounts', to_change), 0)  # renamed *.imported
        self.assertEqual(config_store.history(5)[0][2:5], ('live-1.live_orders', 'null', 'true'))


class HistoryFollowsTheSavedFileTests(unittest.TestCase):
    """11-18 16:01 a rehearsal on a copy wrote 'books.live-2.strategy -> yeo_method' into the live history: the history
    goes next to the nhplug root of the file that was saved, the runtime one only for the runtime's files."""
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        env = mock.patch.dict(os.environ, {'VIBE_TRADING_HOME': str(self.dir / 'live'), 'VIBE_TRADING_SETTINGS_HISTORY': ''})
        env.start()
        self.addCleanup(env.stop)

    def rows(self, db):
        import sqlite3
        if not db.exists():
            return []
        with sqlite3.connect(db) as con:
            return [r[0] for r in con.execute('SELECT key FROM changes')]

    def test_a_copy_keeps_its_own_history(self):
        import accounts
        import strategy_settings
        import vibe_longterm
        live_db = self.dir / 'live' / 'nhplug' / 'settings-history.sqlite3'
        copy = self.dir / 'rehearsal' / 'nhplug'  # a copied runtime tree
        accounts.save(accounts.load(copy / 'none.json'), copy / 'accounts.json')
        strategy_settings.write_settings(copy / 'shared-etf-paper', {**strategy_settings.DEFAULTS, 'take_profit_half_pct': 1.5})
        vibe_longterm.save_settings(copy / 'vibe-longterm', {'enabled': False}, who='rehearsal')
        with mock.patch.dict(os.environ, {'VIBE_TRADING_TUNABLES': str(copy / 'tunables.json')}):
            tunables.save({'losing_streak': 4}, who='rehearsal')
        import paper_ai_modes
        paper_ai_modes.write_modes(copy / 'shared-etf-paper', jev=True, decision=False)
        self.assertEqual(self.rows(live_db), [])
        keys = self.rows(copy / 'settings-history.sqlite3')
        for key in ('paper-shared.auto', 'take_profit_half_pct', 'enabled', 'losing_streak'):
            self.assertIn(key, keys)
        self.assertTrue(any(k.startswith('jev') or k.startswith('decision') for k in keys), keys)

    def test_the_runtime_files_keep_the_runtime_history(self):
        import accounts
        live = self.dir / 'live' / 'nhplug'
        accounts.save(accounts.load(live / 'none.json'), live / 'accounts.json')
        self.assertIn('paper-shared.auto', [r[2] for r in config_store.history(50, 'accounts')])


class TunablesTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        env = mock.patch.dict(os.environ, {'VIBE_TRADING_SETTINGS_HISTORY': str(Path(self.dir.name) / 'h.sqlite3'),
                                           'VIBE_TRADING_TUNABLES': str(Path(self.dir.name) / 'tunables.json')})
        env.start()
        self.addCleanup(env.stop)

    def test_defaults_types_ranges_and_history(self):
        self.assertEqual(tunables.setting('paper_max_order_qty'), 100)
        saved = tunables.save({'paper_max_order_qty': 5_000_000, 'last_order': '15:30', 'vibe_buy_score': '0.6'})
        self.assertEqual(saved, {'paper_max_order_qty': 1_000_000, 'last_order': '15:18', 'vibe_buy_score': 0.6})  # caps hold
        self.assertEqual(tunables.setting('vibe_buy_score'), 0.6)
        self.assertIn(('tunables', 'paper_max_order_qty'), [(r[1], r[2]) for r in config_store.history(10)])
        with self.assertRaises(ValueError):
            tunables.save({'paper_max_order_qty': 2.5})
        with self.assertRaises(ValueError):
            tunables.save({'session_open': '25:00'})
        Path(os.environ['VIBE_TRADING_TUNABLES']).write_text('{"values": {"losing_streak": "x"}}')
        self.assertEqual(tunables.setting('losing_streak'), 3)  # unreadable stored value: the default

    def test_non_finite_and_huge_values_are_unreadable(self):
        """json reads Infinity/NaN; session_times reads flatten_lead_min at import (int(inf) raised OverflowError)."""
        Path(os.environ['VIBE_TRADING_TUNABLES']).write_text('{"values": {"flatten_lead_min": Infinity, "vibe_buy_score": NaN}}')
        self.assertEqual(tunables.setting('flatten_lead_min'), 20)
        self.assertEqual(tunables.setting('vibe_buy_score'), tunables.TUNABLES['vibe_buy_score'][0])
        for raw in ('nan', 'inf', '-inf', 1e400, '1e999'):
            with self.subTest(raw=raw):
                self.assertIsNone(tunables.clean('vibe_buy_score', raw))
                self.assertIsNone(tunables.clean('flatten_lead_min', raw))
        with self.assertRaises(ValueError):
            tunables.save({'losing_streak': 'inf'})


if __name__ == '__main__':
    unittest.main()
