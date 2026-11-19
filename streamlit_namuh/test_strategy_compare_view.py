from datetime import date

import strategy_compare_view as V


def row(day, book, ret, pnl, trades=None):
    return {'day': day, 'book': book, 'ret_day': ret, 'pnl_day': pnl, 'trades': trades, 'fees_est': 0}


def test_period_window_and_stats():
    days = ['2026-11-11', '2026-11-17', '2026-11-19']
    assert V.window(days, '이번 주', date(2026, 11, 19)) == ('2026-11-16', '2026-11-19')
    assert V.window(days, '이번 달', date(2026, 11, 19)) == ('2026-11-01', '2026-11-19')
    assert V.window(days, '전체', date(2026, 11, 19)) == ('2026-11-11', '2026-11-19')
    assert V.window(days, '직접', date(2026, 11, 19), (date(2026, 11, 12), date(2026, 11, 18))) == ('2026-11-12', '2026-11-18')
    s = V.stats([row('a', 'longterm', 0.10, 100, 2), row('b', 'longterm', -0.20, -220, 3), row('c', 'longterm', 0.0, 0, 0)], 'longterm')
    assert round(s['ret'], 4) == round(1.1 * 0.8 - 1, 4) and round(s['mdd'], 4) == -0.2
    assert (s['win_days'], s['active'], s['trades'], s['pnl']) == (1, 2, 5, -120)
    assert V.stats([], 'jev') is None


def test_jev_return_follows_its_pnl_on_one_base():
    # 11-11: −25원 on 1,277원 in use was −2% and outweighed +75,990원 over the period
    rows = [{**row('a', 'jev', -0.0196, -25), 'deployed': 1_277}, {**row('b', 'jev', 0.01, 100_000), 'deployed': 10_000_000},
            {**row('c', 'jev', 0, 0), 'deployed': 0}]
    s = V.stats(rows, 'jev')
    base = (1_277 + 10_000_000) / 2
    assert abs(s['ret'] - (100_000 - 25) / base) < 1e-12 and s['ret'] > 0 and s['pnl'] == 99_975


def test_relation_needs_two_shared_days():
    assert V.relation({'a': 0.1}, {'a': 0.2}) == (1, None, None)
    n, corr, up = V.relation({'a': 0.1, 'b': -0.1, 'c': 0.2}, {'a': 0.2, 'b': -0.2, 'c': 0.3})
    assert n == 3 and corr > 0.98 and up == 2 / 3


def _page():
    import strategy_compare_view as V
    V.render(__import__('pathlib').Path(__import__('os').environ['CMP_RUNTIME']))


def test_the_tab_renders_every_section_from_the_stored_rows(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import strategy_compare as S
    rows = [{**{c: None for c in S.COLUMNS}, 'day': d, 'book': b, 'ret_day': r, 'pnl_day': r * 1e6, 'pnl_cum': 0, 'deployed': 1e6,
             'drawdown': 0, 'trades': 1 if b == 'jev' else None, 'fees_est': 10 if b == 'jev' else None, 'regime_kr': '하락장',
             'regime_us': 'correction', 'bench': {'069500': 1.0, 'NDX': -0.5}, 'built': '2026-11-19T16:00:00+09:00'}
            for d, b, r in (('2026-11-18', 'jev', 0.01), ('2026-11-18', 'longterm', -0.002), ('2026-11-19', 'jev', -0.02),
                            ('2026-11-19', 'longterm', 0.004), ('2026-11-19', 'longterm_base', 0.001))]
    monkeypatch.setenv('CMP_RUNTIME', str(tmp_path))
    monkeypatch.setattr(S, 'rows', lambda runtime: rows)
    monkeypatch.setattr(S, 'stale', lambda runtime: False)
    page = AppTest.from_function(_page, default_timeout=60).run()
    assert not page.exception, page.exception
    labels = [t.label for t in page.tabs]
    assert labels[:7] == ['성과 곡선', '위험', '국면별 성적', '두 방식 관계', '방식 한눈에', '일별 기록', '기준·데이터 흐름']
    html = ' '.join(m.value for m in page.markdown)
    assert 'class="gd-cmp"' in html and 'Jev 트레이딩' in html  # the side-by-side comparison, not a picture
    assert any('판단하지 않습니다' in str(w.value) for w in page.warning)  # 2 shared days < MIN_DAYS
