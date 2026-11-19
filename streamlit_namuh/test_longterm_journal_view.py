import json
from datetime import datetime

import longterm_journal_view as J
import vibe_longterm as L


def seed(root):
    db = L.connect(root)
    for day, regime in (('2026-11-17', 'correction'), ('2026-11-18', 'correction'), ('2026-11-19', 'recovery')):
        L.log(db, 'regime', day, regime=regime, mode='manual', modules=['bogle_core', 'yeo_reservoir'], reason='국면 판단',
              now=datetime.fromisoformat(f'{day}T08:45:00+09:00'))
    with db:
        db.executemany('INSERT INTO nav VALUES (?,?,?)', [('2026-11-17', 'actual', 100_000_000), ('2026-11-18', 'actual', 99_500_000),
                                                          ('2026-11-18', 'base', 99_800_000)])
    db.close()
    reports = root / 'reports'
    reports.mkdir()
    for key in ('2026-11-18', '2026-W47', '2026-11'):
        (reports / f'strategy-{key}.md').write_text(f'# 장기투자 리포트 · {key}\n\n## 2. 거시 시황\n\n금리 4.6%\n')
    (reports / 'strategy-2026-11-18.json').write_text(json.dumps({'summary': '조정 2주째', 'masters': {'bogle_core': '계속 모은다'}}))


def test_day_data_reads_the_reports_decisions_and_nav_of_the_day(tmp_path):
    seed(tmp_path)
    data = J.day_data('2026-11-18', tmp_path)
    assert list(data['reports']) == ['2026-11-18', '2026-W47', '2026-11']  # the day, its week and its month
    assert data['reports']['2026-11-18'][1]['masters'] == {'bogle_core': '계속 모은다'}
    assert data['reports']['2026-W47'][1] == {}  # no json: the md alone
    assert [d['regime'] for d in data['decisions']] == ['correction']
    assert data['nav'] == {'actual': (99_500_000, 100_000_000), 'base': (99_800_000, None)}
    assert J.day_data('2026-11-18', tmp_path / 'none') == {'reports': {}, 'decisions': [], 'nav': {}, 'contributed': 0, 'contributed_before': 0,
                                                       'macro': None}


def test_period_data_keeps_regime_changes_and_period_reports(tmp_path):
    seed(tmp_path)
    data = J.period_data('2026-11-17', '2026-11-19', tmp_path)
    assert data['regimes'] == [('2026-11-17', 'correction'), ('2026-11-19', 'recovery')]
    assert data['reports'] == ['2026-11', '2026-W47'] and len(data['nav']) == 3


def test_day_data_carries_that_mornings_macro(tmp_path):
    import longterm_journal_view as V
    import vibe_longterm as L
    db = L.connect(tmp_path)
    macro = {'series': {'DGS10': {'label': '미 10년 국채금리', 'unit': '%', 'date': '2026-11-16', 'value': 5.15, 'chg_1m': -0.04,
                                  'low_1y': 3.97, 'high_1y': 5.31, 'stale': False}}, 'events': [], 'errors': []}
    L.log(db, 'macro', '2026-11-18', indicators=macro, reason='x')
    assert V.day_data('2026-11-18', tmp_path)['macro']['series']['DGS10']['value'] == 5.15
    assert V.day_data('2026-11-17', tmp_path)['macro'] is None


def test_contributions_are_not_a_book_and_not_a_gain(tmp_path):
    seed(tmp_path)
    db = L.connect(tmp_path)
    with db:
        db.executemany('INSERT INTO nav VALUES (?,?,?)', [('2026-11-19', 'actual', 100_500_000), ('2026-11-19', L.CONTRIBUTED, 1_000_000)])
    data = J.day_data('2026-11-19', tmp_path)
    assert set(data['nav']) == {'actual'} and data['contributed'] == 1_000_000
    assert J.day_change(data, 'actual') == 0  # 99.5M → 100.5M with 1M paid in that day
    period = J.period_data('2026-11-17', '2026-11-19', tmp_path)
    assert {b for _, b, _ in period['nav']} == {'actual', 'base'} and period['contributed'] == {'2026-11-19': 1_000_000}


def test_journal_metrics_and_fills_read_like_the_dashboard():
    from streamlit.testing.v1 import AppTest

    def view():
        import longterm_journal_view as J
        data = {'nav': {'actual': (100_500_000, 99_500_000)}, 'contributed': 1_000_000, 'contributed_before': 0, 'decisions': []}
        books = {'live-2': {'label': '실계좌 2 장기투자', 'fills': [{'time': '2026-11-19T09:41:00', 'symbol': '379810', 'side': 'BUY', 'qty': 2,
                                                                 'price': 20000}]}}
        J.journal_view(data, books)
    at = AppTest.from_function(view).run()
    assert not at.exception
    assert [(m.label, m.value, m.delta) for m in at.metric] == [('실제(가상 계좌)', '100,500,000원', '+0원'), ('적립 누계', '1,000,000원', '+1,000,000원')]
    assert at.dataframe[-1].value['종목'][0] == 'KODEX 미국나스닥100 (379810)'


def test_period_reports_are_found_without_probing_every_day(tmp_path, monkeypatch):
    seed(tmp_path)
    probes = []
    real = J.Path.exists
    monkeypatch.setattr(J.Path, 'exists', lambda self: probes.append(self) or real(self))
    assert J.period_data('2026-01-01', '2026-12-31', tmp_path)['reports'] == ['2026-11', '2026-W47']
    assert len([p for p in probes if p.name.startswith('strategy-')]) <= 53 + 12
