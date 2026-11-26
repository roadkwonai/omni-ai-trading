from datetime import datetime, timedelta
import json
import sqlite3

from jev_buy_run import advance, collect_tick, refresh_experience


KST_TIME = datetime.fromisoformat('2026-11-27T10:00:00+09:00')


def test_model_attempt_is_claimed_once_and_failure_is_not_retried(tmp_path):
    calls = []
    def judge(frame):
        calls.append(frame['cycle_id'])
        raise TimeoutError('response unknown')

    signal = {'day': '2026-11-27', 'cycle_id': 'cycle-1', 'symbol': '005930', 'digest': 'a' * 64,
              'response_at': (KST_TIME - timedelta(seconds=5)).isoformat()}
    db = tmp_path / 'state.sqlite3'
    assert advance(db, signal, KST_TIME, judge)['status'] == 'request_unknown'
    assert advance(db, signal, KST_TIME, judge)['status'] == 'request_unknown'
    assert calls == ['cycle-1']
    assert db.stat().st_mode & 0o777 == 0o600


def test_experience_version_is_fixed_for_the_day_and_no_future_sample(tmp_path):
    samples = []
    for i in range(6):
        samples.append({'sample_id': str(i), 'symbol': f'{i:06d}', 'day': '2026-11-26',
                        'purpose': 'candidate', 'market': 'KOSDAQ', 'route': 'watchlist_strength',
                        'label_type': 'paper_fill', 'cost_basis': 'included', 'return_pct': -1 if i % 2 else 1,
                        'outcome_available_at': '2026-11-26T15:00:00+09:00'})
    root = tmp_path / 'experience'
    first = refresh_experience(root, KST_TIME, samples)
    assert first['lessons'][0]['symbol_days'] == 6
    second = refresh_experience(root, KST_TIME, samples + [{**samples[0], 'sample_id': 'new',
        'symbol': '999999', 'day': '2026-11-27', 'outcome_available_at': KST_TIME.isoformat()}])
    assert second['version'] == first['version']
    assert json.loads((root / '2026-11-27' / 'current.json').read_text())['version'] == first['version']


def test_observation_judgment_and_books_are_recoverable_without_duplicate_requests(tmp_path, monkeypatch):
    import jev_buy_judgments
    import jev_buy_observer
    import jev_buy_run

    decision_at = KST_TIME - timedelta(seconds=5)
    class Reader:
        calls = 0
        def call_tool(self, name, params):
            assert name == 'get_stock_price'
            self.calls += 1
            if self.calls == 1:
                return {'status': 'error'}
            return {'status': 'ok', 'data': {'Output_0': {'iem_cd': '005930', 'hoga_bsop_hour': '10:00:00',
                                                       'bidp': 999, 'askp': 1000}}}

    def observe(_ledger, _research, path, now, reader, **kwargs):
        del reader, kwargs
        with sqlite3.connect(path) as db:
            db.executescript(jev_buy_observer.SCHEMA)
            db.execute('INSERT OR IGNORE INTO signals VALUES (?,?,?,?,?,?,?,?)', ('2026-11-27', 'abc1', '005930',
                       'a' * 64, decision_at.isoformat(), decision_at.isoformat(), .8, 'jev-1.14.0'))
        return {'status': 'ok', 'signals_added': 1, 'quotes_added': 0, 'quote_failures': 0}

    calls = []
    def compare(frame, experience, **kwargs):
        calls.append((frame, experience, kwargs))
        return {'failed_arm': None, 'arms': {'baseline': {'choice': '005930', 'confidence': .8,
               'decided_at': decision_at.isoformat()}, 'experience': {'choice': 'WAIT', 'confidence': .8,
               'decided_at': decision_at.isoformat()}}}

    monkeypatch.setattr(jev_buy_observer, 'observe', observe)
    monkeypatch.setattr(jev_buy_judgments, 'compare', compare)
    frame_calls = []
    def frame(*_):
        frame_calls.append(1)
        return {'symbol': '005930', 'market': 'KOSDAQ'}
    monkeypatch.setattr(jev_buy_run, 'experiment_frame', frame)
    monkeypatch.setattr(jev_buy_run, 'refresh_experience', lambda *_, **__: {'version': 'v', 'lessons': [{'route': 'jev'}]})
    credentials = {'JEV_API_KEY': 'main', 'JEV_ISOLATED_API_KEY': 'separate'}
    root = tmp_path / 'isolated'
    reader = Reader()
    first = collect_tick('ledger', 'research', root, KST_TIME, reader, [], credentials,
                         trading_day=True, clock=lambda: KST_TIME)
    assert first['books']['baseline']['holdings'] == {}
    second = collect_tick('ledger', 'research', root, KST_TIME, reader, [], credentials,
                          trading_day=True, clock=lambda: KST_TIME)
    assert second['books']['baseline']['holdings']['005930']['qty'] > 0
    assert first['books']['experience']['holdings'] == {}
    third = collect_tick('ledger', 'research', root, KST_TIME, reader, [], credentials,
                         trading_day=True, clock=lambda: KST_TIME)
    assert len(calls) == 1
    assert third['books']['baseline']['fills'] == 1
    assert len(frame_calls) == 2


def test_complete_judgment_can_retry_quote_without_repeating_model(tmp_path):
    signal = {'day': '2026-11-27', 'cycle_id': 'cycle-2', 'symbol': '005930', 'digest': 'b' * 64,
              'response_at': (KST_TIME - timedelta(seconds=5)).isoformat()}
    calls = []
    def judge(_):
        calls.append(1)
        return {'arms': {'baseline': {}, 'experience': {}}}
    path = tmp_path / 'state.sqlite3'
    assert advance(path, signal, KST_TIME, judge)['status'] == 'complete'
    from jev_buy_run import record_status
    record_status(path, signal, 'quote_attempted')
    assert advance(path, signal, KST_TIME + timedelta(seconds=5), judge)['status'] == 'quote_attempted'
    assert calls == [1]


def test_missing_experience_creates_separate_noncomparable_baseline(tmp_path, monkeypatch):
    import jev_buy_observer
    import jev_buy_run

    decided = KST_TIME - timedelta(seconds=5)
    def observe(_ledger, _research, path, now, reader, **kwargs):
        del now, reader, kwargs
        with sqlite3.connect(path) as db:
            db.executescript(jev_buy_observer.SCHEMA)
            db.execute('INSERT OR IGNORE INTO signals VALUES (?,?,?,?,?,?,?,?)', ('2026-11-27', 'def2', '005930',
                       'b' * 64, decided.isoformat(), decided.isoformat(), .8, 'jev-1.14.0'))
        return {'status': 'ok', 'signals_added': 1, 'quotes_added': 0, 'quote_failures': 0}
    class Reader:
        def call_tool(self, name, params):
            assert name == 'get_stock_price'
            return {'status': 'ok', 'data': {'Output_0': {'iem_cd': params['stock_code'],
                    'hoga_bsop_hour': '10:00:00', 'bidp': 999, 'askp': 1000}}}
    monkeypatch.setattr(jev_buy_observer, 'observe', observe)
    monkeypatch.setattr(jev_buy_run, 'experiment_frame', lambda *_: {'symbol': '005930', 'market': 'KOSPI'})
    monkeypatch.setattr(jev_buy_run, 'refresh_experience', lambda *_, **__: {})
    result = collect_tick('ledger', 'research', tmp_path / 'isolated', KST_TIME, Reader(), [], {},
                          trading_day=True, clock=lambda: KST_TIME)
    assert result['bootstrap_books']['comparison_cases'] == {'unavailable': 1}
    assert result['bootstrap_books']['baseline']['fills'] == 1
    assert result['books']['baseline']['fills'] == 0
