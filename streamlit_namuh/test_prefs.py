import tempfile
import unittest
from pathlib import Path

import prefs
import sidebar


class PrefsTests(unittest.TestCase):
    def test_bad_or_missing_values_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'p.json'
            self.assertEqual(prefs.load(path), prefs.DEFAULTS)
            path.write_text('{"refresh_s": true, "start_tab": "없는 탭", "money_unit": "억원", "x": 1}')
            loaded = prefs.load(path)
            self.assertEqual((loaded['refresh_s'], loaded['start_tab'], loaded['money_unit']), (30, '종합 현황', '억원'))
            saved = prefs.save(path, {**loaded, 'refresh_s': 60, 'status_blink': False})
            self.assertEqual(prefs.load(path), saved)
            self.assertNotIn('x', saved)

    def test_nav_buttons_escape_and_cover_every_tab(self):
        html = sidebar.nav_html(prefs.TABS + ['<x>'])
        self.assertEqual(html.count('class="nav-btn"'), len(prefs.TABS) + 1)
        self.assertIn('data-tab="&lt;x&gt;"', html)

    def test_font_scale_is_validated_and_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'p.json'
            self.assertEqual(prefs.load(path)['font_scale'], 100)
            self.assertEqual(prefs.save(path, {'font_scale': 85})['font_scale'], 85)
            self.assertEqual(prefs.load(path)['font_scale'], 85)
            for invalid in (True, 1, 0, 200, '85'):
                self.assertEqual(prefs.load_from({'font_scale': invalid})['font_scale'], 100)


if __name__ == '__main__':
    unittest.main()


class PrefsHistoryTests(unittest.TestCase):
    def test_saving_prefs_records_history_next_to_the_saved_file(self):
        """4th review B3: a copy's save wrote the runtime settings history."""
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'VIBE_TRADING_SETTINGS_HISTORY': ''}):
            path = Path(tmp) / 'nhplug' / 'dashboard-prefs.json'
            path.parent.mkdir()
            prefs.save(path, {'refresh_s': 60})
            self.assertTrue((Path(tmp) / 'nhplug' / 'settings-history.sqlite3').exists())
