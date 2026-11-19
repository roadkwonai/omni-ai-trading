"""트레이딩 현황 → 순위 (user 2026-11-19): the 주도 테마 동반주 picks reach Jev outside the four rankings, so the panel
lists them; the caption counts them."""
from datetime import datetime
from zoneinfo import ZoneInfo

NOW = datetime(2026, 11, 20, 10, 30, tzinfo=ZoneInfo('Asia/Seoul'))


def _panel(movers, now):
    import app
    app.movers_panel(movers, now)


def test_the_rankings_panel_lists_theme_picks_with_their_group():
    from streamlit.testing.v1 import AppTest
    row = {'code': '090430', 'name': '아모레퍼시픽', 'price': 141000, 'change_pct': 7.88, 'value_mil': 65269}
    movers = {'time': NOW.isoformat(), 'universe': 351, 'scanned': 59, 'source': 'kiwoom_preselect', 'seconds': 20,
              'rankings': {k: [] for k in ('trading_value', 'volume', 'rise', 'fall')},
              'candidates': {'090430': {**row, 'reasons': ['주도 테마 동반주 (화장품·소비)'], 'theme_group': '화장품·소비'}},
              'theme_picks': [{'code': '090430', 'group': '화장품·소비'}], 'rows': {'090430': row}}
    page = AppTest.from_function(_panel, args=(movers, NOW), default_timeout=30).run()
    assert not page.exception, page.exception
    tables = [t.value for t in page.dataframe]
    picked = [t for t in tables if '테마 묶음' in t.columns]
    assert picked and list(picked[0]['종목']) == ['아모레퍼시픽'] and list(picked[0]['테마 묶음']) == ['화장품·소비']
    assert any('테마 동반주' in c.value for c in page.caption)


def test_no_theme_pick_table_without_picks():
    from streamlit.testing.v1 import AppTest
    movers = {'time': NOW.isoformat(), 'rankings': {k: [] for k in ('trading_value', 'volume', 'rise', 'fall')},
              'candidates': {}, 'rows': {}}
    page = AppTest.from_function(_panel, args=(movers, NOW), default_timeout=30).run()
    assert not page.exception and not [t for t in page.dataframe if '테마 묶음' in t.value.columns]
