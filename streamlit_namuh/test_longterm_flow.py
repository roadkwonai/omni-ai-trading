import shutil
import subprocess
from datetime import datetime

import pytest

import longterm_flow as F
import strategy_map
import vibe_longterm as L


def seeded(tmp_path, **plan):
    db = L.connect(tmp_path)
    L.put_state(db, regime={'regime': 'correction', 'since': '2026-11-16', 'pending': 'bear', 'pending_days': 3},
                plan={'day': '2026-11-18', 'time': '2026-11-18T08:45:00+09:00', 'regime': 'correction', 'raw': 'bear',
                      'active': ['bogle_core', 'dalio_rebalance', 'lynch_story'], 'recommended': ['bogle_core', 'yeo_reservoir'],
                      'mode': 'manual', 'macro': {'errors': ['DFII10: TimeoutError'], 'series': {}, 'events': []}, **plan},
                last_plan={'day': '2026-11-18', 'why': 'monthly_band', 'execute': False,
                           'orders': [{'key': 'NDX', 'side': 'BUY', 'qty': 1, 'price': 10}]},
                story={'source': 'llm', 'stories': {}})
    return F.state(tmp_path, live={'live-2': {'label': '실계좌 2', 'execute': False, 'orders': 2}})


def edges_of(diagram):
    return {(e['from'], e['to']) for e in diagram.get('edges') or diagram.get('transitions')}


def ids_of(diagram):
    return [n['id'] for n in diagram.get('nodes') or diagram.get('states')]


def test_empty_record_still_draws_every_diagram(tmp_path):
    st = F.state(tmp_path, live={})
    for diagram in (F.architecture(st), F.algorithm(st), F.regimes(st)):
        ids = ids_of(diagram)
        assert len(ids) == len(set(ids)) and all(a in ids and b in ids for a, b in edges_of(diagram))
    assert F.regimes(st)['meta']['title'].endswith('판정 전')


def test_algorithm_reads_the_live_settings_and_highlights_todays_route(tmp_path):
    st = seeded(tmp_path)
    d = F.algorithm(st)
    text = str(d)
    t = st['settings']['tuning']
    assert f"±{t['band'] * 100:.0f}%p" in text and f"{t['confirm_days']}거래일" in text
    assert '수동' in text and '기준 비중' in text and '린치' in text
    route = d['mainPath']
    assert route[0] == 'market' and 'story' in route and route[-1] == 'record' and 'orders' in route
    assert all((a, b) in edges_of(d) for a, b in zip(route, route[1:]))
    st['plan']['active'] = ['bogle_core']  # no story module, no orders today
    st['last_plan']['orders'] = []
    route = F.algorithm(st)['mainPath']
    assert 'story' not in route and 'orders' not in route
    assert all((a, b) in edges_of(F.algorithm(st)) for a, b in zip(route, route[1:]))


def test_regime_diagram_has_the_engine_regimes_and_marks_now_and_pending(tmp_path):
    st = seeded(tmp_path)
    d = F.regimes(st)
    assert set(ids_of(d)) == set(L.REGIMES)
    tags = {s['id']: s.get('tag', '') for s in d['states']}
    assert tags['correction'].startswith('● 지금') and tags['bear'].startswith('◌ 확정 대기 3/10')
    for k in L.REGIMES:  # every regime can be entered and left
        assert any(b == k for _, b in edges_of(d)) and any(a == k for a, _ in edges_of(d))


def test_architecture_shows_freshness_of_each_part(tmp_path):
    text = str(F.architecture(seeded(tmp_path)))
    assert '11-18 08:45' in text and 'DFII10' in text and '실계좌 2 · 계획만 · 주문 2건' in text and 'dry-run' in text


def test_component_rows_and_summary_follow_the_engine_constants(tmp_path):
    st = seeded(tmp_path)
    rows = F.components(st)
    assert len({r['단계'] for r in rows}) == len(rows) and any('confirm_days' in r['설정'] for r in rows)
    summary = F.summary(st)
    assert '코어 60' in summary and '위성 25' in summary and '저수지 15' in summary


needs_archify = pytest.mark.skipif(not (shutil.which('node') and strategy_map.ARCHIFY.exists()), reason='archify not installed')


@needs_archify
@pytest.mark.parametrize('name', ['architecture', 'algorithm', 'regimes', 'module_map', 'today_flow'])
def test_archify_accepts_each_diagram(tmp_path, name):
    st = seeded(tmp_path)
    diagram = getattr(F, name)(st)
    html = strategy_map.render(diagram, tmp_path / 'out', kind=diagram['diagram_type'], prefix=f'longterm-{name}')
    assert html.exists() and html.stat().st_size > 1000


def _view():
    import app
    app.longterm_algorithm_view()


def test_strategy_tab_long_term_view_renders_without_a_record(tmp_path):
    from unittest.mock import patch
    from streamlit.testing.v1 import AppTest
    import app
    with patch.object(app, 'get_runtime_root', return_value=tmp_path):
        ui = AppTest.from_function(_view).run()
    assert not ui.exception
    assert any('코어 60' in i.value for i in ui.info)
    assert [t.label for t in ui.tabs] == ['아키텍처', '하루 알고리즘', '국면 전이', '거장 모듈', '구성 요소 · 오늘 운영 상태']


def test_guides_use_the_saved_long_term_numbers(monkeypatch):
    import guides
    settings = L.load_settings(L.ROOT)
    settings['tuning']['band'] = 0.07
    monkeypatch.setattr(L, 'load_settings', lambda root=None: settings)
    assert '밴드 ±7%p' in guides.engines_dot(guides.values())
    assert 'longterm' in guides.TOPICS and 'longterm' in guides.GUIDES and '밴드 ±7%p' in guides.TOPIC_TEXT['longterm']({})
    assert 'digraph' in guides.DIAGRAMS['longterm_regimes'][1]({}) and 'digraph' in guides.DIAGRAMS['longterm'][1]({})


def test_module_map_and_today_flow_follow_the_record(tmp_path):
    st = seeded(tmp_path)
    m = F.module_map(st)
    assert {n['id'] for n in m['nodes']} >= set(L.MODULES)
    tags = {n['id']: n['tag'] for n in m['nodes']}
    assert tags['lynch_story'] == '● 켜짐' and tags['yeo_reservoir'] == '○ 꺼짐 · ★ 추천' and '목표 60%' in tags['core']
    assert all(e.get('role') == 'async' for e in m['edges'] if e['from'] == 'yeo_reservoir')  # an off module is dashed
    t = F.today_flow(st)
    tags = {n['id']: n['tag'] for n in t['nodes']}
    assert tags['macro'] == '✗ 빠짐 1' and tags['plan'].startswith('✓') and tags['fill'] == '— 계획만(dry-run)'
    assert tags['live_live-2'] == '— 계획만 · 2건' and t['mainPath'] == ['market', 'regime', 'story', 'plan']
    empty = F.today_flow(F.state(tmp_path / 'none', live={}))
    assert 'mainPath' not in empty and all(n['tag'].startswith(('…', '—')) for n in empty['nodes'])


def test_jev_like_parts_read_the_engine_constants(tmp_path):
    from datetime import datetime
    st = seeded(tmp_path)
    rules = {r['항목']: r['지금 값'] for r in F.rule_rows(st)}
    assert rules['위성 합계 상한'] == f'{L.SAT_MAX * 100:.0f}%' and rules['익절 제안'] == '+20% · +40%' and rules['손절'] == '없음'
    assert rules['KODEX 미국나스닥100 (379810)'] == '30%' and rules[L.TUNING['band'][3]] == '5%'
    regimes = F.regime_rows(st)
    assert [r['국면'] for r in regimes] == [v[0] for v in L.REGIMES.values()]
    assert next(r for r in regimes if r['국면'] == '조정')[''] == '▶ 지금' and next(r for r in regimes if r['국면'] == '약세')[''] == '◌ 대기'
    srcs = F.sources(st, tmp_path, datetime.fromisoformat('2026-11-18T10:00:00+09:00'))
    assert any(s['데이터'] == '금리·신용·발표 일정' and 'DFII10' in s['기준'] for s in srcs)
    cards = {title: (dict(items), on) for title, items, _, _, on in F.route_cards(st)}
    assert cards['2 · 월 밴드 리밸런싱'][1] and not cards['3 · 저수지 단계 투입'][1]
    assert cards['3 · 저수지 단계 투입'][0]['조건'] == L.MODULES['yeo_reservoir'][2]
    labels = [c['value'] for c in F.status(st, datetime.fromisoformat('2026-11-18T09:00:00+09:00'))]
    assert labels == ['켜짐', '수동', '계획만', '09:40 주문 계획']


def test_live_accounts_are_only_valid_long_term_live_accounts(tmp_path):
    import json
    import longterm_flow as LF
    base = {'kind': 'live', 'auto': True, 'budget': {'capital_krw': 500_000, 'order_price_max_krw': 10_000, 'daily_loss_krw': 20_000}}
    (tmp_path / 'accounts.json').write_text(json.dumps({'accounts': {
        'live-2': {**base, 'label': '실계좌 2', 'engine': 'longterm'},
        'live-3': {**base, 'label': '실계좌 3', 'engine': 'longterm', 'auto': 'yes'}}}))  # invalid: falls back to defaults
    assert list(LF.live_accounts(tmp_path)) == ['live-2']


def test_module_rows_keep_running_and_recommended_apart(tmp_path):
    st = seeded(tmp_path)
    rows = {r['모듈']: r for r in F.modules(st)}
    lynch, yeo = rows[L.MODULES['lynch_story'][1]], rows[L.MODULES['yeo_reservoir'][1]]
    assert (lynch['운용'], lynch['국면 추천']) == ('켜짐', '')
    assert (yeo['운용'], yeo['국면 추천']) == ('꺼짐', '★ 추천')
    assert '상태' not in lynch
    summary = F.module_summary(st)
    assert summary.startswith('켜짐 ') and '추천인데 꺼짐' in summary and L.MODULES['yeo_reservoir'][0] in summary
