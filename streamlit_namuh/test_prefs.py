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


def test_tabs_come_in_three_groups_and_old_names_find_their_new_place():
    """2026-11-19 menu plan: 10 tabs, 주문·정리 inside 실계좌, the four admin screens inside 운영."""
    assert [name for name, _ in prefs.GROUPS] == ['매일', '계좌', '관리']
    assert prefs.TABS[-4:] == ['계좌·자동매매', '장기투자', '실계좌', '운영'] and len(prefs.TABS) == 10
    assert prefs.place('엔진 비교') == ('성과 비교', None, None)
    assert prefs.place('주문·정리') == ('실계좌', 'live_view', prefs.LIVE_ORDERS)
    assert prefs.place('API 연동') == ('운영', 'ops_view', 'API 연동')
    assert prefs.place('매매일지') == ('매매일지', None, None) and prefs.place('없는 탭') is None
    assert prefs.place('운영', 'API 연동') == ('운영', 'ops_view', 'API 연동') and prefs.place('매매일지', 'x') == ('매매일지', None, None)
    assert prefs.place('운영', '없는 화면') == ('운영', None, None)
    for old, new in (('백업·이식', '운영'), ('엔진 비교', '성과 비교'), ('주문·정리', '실계좌'), ('없는 탭', '종합 현황')):
        assert prefs.load_from({'start_tab': old})['start_tab'] == new


def test_the_sidebar_shows_the_groups_and_the_tab_bar_gets_dividers_before_each_later_group():
    html = sidebar.nav_html(prefs.TABS, prefs.GROUPS)
    assert [g for g in ('매일', '계좌', '관리') if f'class="nav-group">{g}<' in html] == ['매일', '계좌', '관리']
    assert html.count('class="nav-btn"') == len(prefs.TABS)
    css = sidebar.tab_groups_css(prefs.GROUPS)
    assert ':nth-of-type(7)' in css and ':nth-of-type(10)' in css and ':nth-of-type(1)' not in css
    assert css.count('.st-key-main_tab > div > [role="tablist"]') == 4  # main bar only, never a tab's own sub-tabs
