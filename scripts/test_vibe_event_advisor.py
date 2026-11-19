import json
import sqlite3
import subprocess
import sys
import types
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

import vibe_event_advisor as event


NOW = datetime.fromisoformat('2026-11-16T13:30:00+09:00')


def row(now=NOW, *, symbol='100790', position='a' * 20):
    at = lambda seconds: (now - timedelta(seconds=seconds)).isoformat()
    return {'symbol': symbol, 'qty': 10, 'position_id': position, 'latest_fill_id': 'fill-a',
            'observed_at': at(1), 'event_trigger': {'state': 'triggered', 'triggered': True,
                'reasons': ['rapid_loss'], 'inputs': {'symbol': symbol, 'position_id': position, 'quantity': 10,
                    'evaluated_at': at(2), 'quote_at': at(5), 'quote_observed_at': at(4),
                    'bars_observed_at': at(8), 'bars_through': at(130)}}}


def run(tmp_path, item, *, model=None, clock=None):
    return event.process(item, tmp_path / 'ledger', tmp_path / 'results',
                         clock=clock or (lambda: NOW), model=model or (lambda _: '{"stance":"exit","reason":"위험"}'),
                         lock_path=tmp_path / 'shared.lock')


def fetch(tmp_path):
    with sqlite3.connect(tmp_path / 'results/events.sqlite3') as db:
        return db.execute('SELECT status,result FROM attempts').fetchall()


def assert_unknown_failure(tmp_path, status):
    records = fetch(tmp_path)
    assert len(records) == 1 and records[0][0] == status
    evidence = json.loads(records[0][1])
    assert evidence['source'] == 'unknown' and evidence['api_calls'] is None
    assert evidence['api_call_status'] == 'unknown'


def test_current_vibe_root_requires_explicit_valid_role(tmp_path):
    import accounts
    path = tmp_path / 'accounts.json'
    root = tmp_path / 'vibe-paper'
    assert not event.current_vibe_root(root)  # no implicit fallback to the default role
    shared = dict(accounts.DEFAULTS['accounts']['paper-shared'])
    shared['engine'] = 'jev'
    path.write_text(json.dumps({'accounts': {'paper-shared': shared}}))
    assert event.current_vibe_root(root)
    assert not event.current_vibe_root(tmp_path / 'shared-etf-paper')
    shared['engine'] = 'vibe'
    path.write_text(json.dumps({'accounts': {'paper-shared': shared}}))
    assert not event.current_vibe_root(root)  # vibe can no longer be the shared main (user 2026-11-17): invalid
    assert not event.current_vibe_root(tmp_path / 'shared-etf-paper')
    shared['engine'] = 'unknown'
    path.write_text(json.dumps({'accounts': {'paper-shared': shared}}))
    assert not event.current_vibe_root(root)


def test_engine_role_change_during_analysis_discards_result(tmp_path):
    import accounts
    path = tmp_path / 'accounts.json'
    root = tmp_path / 'vibe-paper'
    shared = dict(accounts.DEFAULTS['accounts']['paper-shared'])
    shared['engine'] = 'jev'
    path.write_text(json.dumps({'accounts': {'paper-shared': shared}}))
    item = row()
    identity = {k: item[k] for k in ('symbol', 'qty', 'position_id', 'latest_fill_id')}
    def model(_):
        shared['engine'] = 'vibe'
        path.write_text(json.dumps({'accounts': {'paper-shared': shared}}))
        return '{"stance":"exit","reason":"위험"}'
    with patch('vibe_fast_exit_watch.positions', return_value=[identity]), \
         patch.object(event, 'context_snapshot', return_value={}):
        assert event.process(item, root, tmp_path / 'results', clock=lambda: NOW, model=model,
                             lock_path=tmp_path / 'jev.lock') == 'position_changed_or_stale'
    assert_unknown_failure(tmp_path, 'position_changed_or_stale')


def test_analysis_isolated_and_duplicate_survives_restart(tmp_path):
    context = {name: {'status': 'ok', 'observed_at': NOW.isoformat()} for name in ('market', 'news')}
    with patch.object(event, 'current_position', return_value=True), patch.object(event, 'context_snapshot', return_value=context):
        assert run(tmp_path, row()) == 'completed'
        assert run(tmp_path, row()) == 'duplicate'
    records = fetch(tmp_path)
    assert len(records) == 1 and records[0][0] == 'completed'
    data = json.loads(records[0][1])
    assert data['mode'] == 'analysis_only' and data['analysis']['stance'] == 'exit' and data['analysis']['status'] == 'ok'
    assert data['trigger']['reasons'] == ['rapid_loss']
    assert not (tmp_path / 'ledger/vibe-advice.json').exists()


def test_default_event_uses_jev_without_regular_gpt_or_its_lock(tmp_path):
    called = []
    def review(item, context, root, *, clock):
        called.append((item['symbol'], root, clock()))
        return {'text': '{"stance":"hold","reason":"Jev 관망"}', 'source': 'jev_event', 'choice': 'HOLD',
                'usage': {'status': 'unavailable'}, 'api_cost': '미확인'}
    context = {name: {'status': 'ok', 'observed_at': NOW.isoformat()} for name in ('market', 'news')}
    with patch.dict(sys.modules, {'vibe_event_jev': types.SimpleNamespace(review=review)}), \
         patch.object(event, 'current_position', return_value=True), \
         patch.object(event, 'context_snapshot', return_value=context), \
         event.model_lock(tmp_path / 'regular-gpt.lock'):
        assert event.process(row(), tmp_path / 'ledger', tmp_path / 'results', clock=lambda: NOW,
                             lock_path=tmp_path / 'jev.lock') == 'completed'
    result = json.loads(fetch(tmp_path)[0][1])
    assert called == [('100790', tmp_path / 'ledger', NOW)]
    assert result['source'] == 'jev_event' and result['analysis']['stance'] == 'hold'
    assert result['api_cost'] == '미확인' and result['usage']['status'] == 'unavailable'
    assert not hasattr(event, 'invoke_model')


def test_real_jev_review_reuses_same_position_without_extra_call(tmp_path):
    import vibe_event_jev as jev
    from test_vibe_event_jev import existing

    root, item, context = existing(tmp_path)
    ledger = (root / 'orders.sqlite3').read_bytes()
    research = (root.parent / 'shared-etf-paper/market-research.sqlite3').read_bytes()
    with patch.object(event, 'current_position', return_value=True), \
         patch.object(event, 'context_snapshot', return_value=context), \
         patch.object(jev, '_same_vibe_root', return_value=True), \
         patch.object(jev, 'current_position', return_value=True), \
         patch.object(jev, '_position_plan', return_value={'quantity': 10}), patch.object(jev, '_invoke') as call:
        assert event.process(item, root, tmp_path / 'results', clock=lambda: NOW,
                             lock_path=tmp_path / 'jev.lock') == 'completed'
    data = json.loads(fetch(tmp_path)[0][1])
    assert data['source'] == 'jev_existing' and data['analysis']['stance'] == 'exit'
    assert data['analysis']['confidence'] == 0.9 and data['analysis']['sell_confidence'] == 0.7
    call.assert_not_called()
    assert (root / 'orders.sqlite3').read_bytes() == ledger
    assert (root.parent / 'shared-etf-paper/market-research.sqlite3').read_bytes() == research


def test_real_jev_new_review_keeps_configured_exit_and_separate_cost(tmp_path):
    import vibe_event_jev as jev
    from test_vibe_event_jev import inputs

    item, context = inputs()
    root = tmp_path / 'vibe-paper'
    response = {'model': jev.MODEL, 'holding': {'type': 'choice', 'choice': 'SELL', 'confidence': 0.8},
                'response_time': NOW.isoformat(), 'chart_sha256': 'b' * 64, 'input_digest': 'c' * 64}
    with patch.object(event, 'current_position', return_value=True), \
         patch.object(event, 'context_snapshot', return_value=context), \
         patch.object(jev, '_same_vibe_root', return_value=True), \
         patch.object(jev, 'current_position', return_value=True), \
         patch.object(jev, '_position_plan', return_value={'quantity': 10}), \
         patch.object(jev, '_recent_existing', return_value=None), \
         patch.object(jev, '_fresh_inputs', return_value={'position_extra': {'quantity': 10}}), \
         patch.object(jev, '_verify_stored_input', return_value=True), \
         patch.object(jev, '_invoke', return_value=response) as call:
        assert event.process(item, root, tmp_path / 'results', clock=lambda: NOW,
                             lock_path=tmp_path / 'jev.lock') == 'completed'
    data = json.loads(fetch(tmp_path)[0][1])
    assert data['source'] == 'jev_event' and data['analysis']['stance'] == 'exit'
    assert data['analysis']['confidence'] == 0.8 and data['analysis']['sell_confidence'] == 0.7
    assert data['model_evidence']['input_digest'] == 'c' * 64 and data['api_cost'] == 'unknown'
    assert data['api_calls'] == 1 and type(data['elapsed_ms']) is int
    assert call.call_count == 1 and not (root / 'orders.sqlite3').exists()


def test_unavailable_jev_never_falls_back_to_gpt_or_produces_exit(tmp_path):
    def review(*_args, **_kwargs):
        return {'text': '{"stance":"uncertain","reason":"입력 부족"}', 'source': 'unavailable'}
    with patch.dict(sys.modules, {'vibe_event_jev': types.SimpleNamespace(review=review)}), \
         patch.object(event, 'current_position', return_value=True), \
         patch.object(event, 'context_snapshot', return_value={}):
        assert event.process(row(), tmp_path / 'ledger', tmp_path / 'results', clock=lambda: NOW,
                             lock_path=tmp_path / 'jev.lock') == 'completed'
    result = json.loads(fetch(tmp_path)[0][1])
    assert result['source'] == 'unavailable' and result['analysis']['stance'] == 'uncertain'
    assert result['analysis']['status'] == 'model_unavailable'
    assert not hasattr(event, 'invoke_model')


def test_unknown_jev_source_or_inconsistent_choice_is_rejected(tmp_path):
    def review(*_args, **_kwargs):
        return {'text': '{"stance":"exit","reason":"위험"}', 'source': 'jev_event',
                'choice': 'HOLD', 'confidence': 0.9}
    with patch.dict(sys.modules, {'vibe_event_jev': types.SimpleNamespace(review=review)}), \
         patch.object(event, 'current_position', return_value=True), \
         patch.object(event, 'context_snapshot', return_value={}):
        assert event.process(row(), tmp_path / 'ledger', tmp_path / 'results', clock=lambda: NOW,
                             lock_path=tmp_path / 'jev.lock') == 'model_invalid_or_unavailable'
    assert_unknown_failure(tmp_path, 'model_invalid_or_unavailable')


def test_missing_or_stale_market_news_never_exports_exit_signal(tmp_path):
    context = {'market': {'status': 'ok', 'observed_at': NOW.isoformat()}, 'news': {'status': 'unavailable'}}
    with patch.object(event, 'current_position', return_value=True), patch.object(event, 'context_snapshot', return_value=context):
        assert run(tmp_path, row()) == 'completed'
    data = json.loads(fetch(tmp_path)[0][1])
    assert data['analysis']['stance'] == 'uncertain'
    assert data['analysis']['status'] == 'insufficient_context'
    assert data['analysis']['model_stance'] == 'exit'
    assert not event.context_sufficient({'market': {'status': 'ok', 'observed_at': NOW.isoformat()},
        'news': {'status': 'ok', 'observed_at': (NOW - timedelta(minutes=31)).isoformat()}}, NOW)


@pytest.mark.parametrize(('confidence', 'status', 'expected'), [
    (0.69, 'custom', 'uncertain'), (0.7, 'custom', 'exit'),
    (None, 'custom', 'uncertain'), (0.9, 'invalid', 'uncertain'),
])
@pytest.mark.parametrize('source', ['jev_event', 'jev_existing'])
def test_jev_exit_requires_current_valid_configured_confidence(tmp_path, confidence, status, expected, source):
    context = {name: {'status': 'ok', 'observed_at': NOW.isoformat()} for name in ('market', 'news')}
    def review(*_args, **_kwargs):
        return {'text': '{"stance":"exit","reason":"위험"}', 'source': source, 'choice': 'SELL', 'confidence': confidence}
    with patch.dict(sys.modules, {'vibe_event_jev': types.SimpleNamespace(review=review)}), \
         patch.object(event, 'current_position', return_value=True), \
         patch.object(event, 'context_snapshot', return_value=context), \
         patch('strategy_settings.read_settings', return_value=({'sell_confidence': 0.7}, status)):
        assert event.process(row(), tmp_path / 'ledger', tmp_path / 'results', clock=lambda: NOW,
                             lock_path=tmp_path / 'jev.lock') == 'completed'
    analysis = json.loads(fetch(tmp_path)[0][1])['analysis']
    assert analysis['stance'] == expected
    assert (analysis['status'] == 'ok') is (expected == 'exit')


def test_position_change_after_model_discards_result(tmp_path):
    with patch.object(event, 'current_position', side_effect=[True, False]), patch.object(event, 'context_snapshot', return_value={}):
        assert run(tmp_path, row()) == 'position_changed_or_stale'
    assert_unknown_failure(tmp_path, 'position_changed_or_stale')


def test_reserved_attempt_does_not_assume_call_outcome_on_crash(tmp_path):
    identity = event._identity(row(), NOW)
    with sqlite3.connect(tmp_path / 'reserved.sqlite3') as db:
        assert event._reserve(db, identity, NOW) is None
        status, payload = db.execute('SELECT status,result FROM attempts').fetchone()
    evidence = json.loads(payload)
    assert status == 'reserved' and evidence['api_calls'] is None
    assert evidence['source'] == 'unknown' and evidence['api_call_status'] == 'unknown'


def test_stale_result_keeps_proven_jev_call_count_without_exit(tmp_path):
    def review(*_args, **_kwargs):
        return {'text': '{"stance":"exit","reason":"위험"}', 'source': 'jev_event',
                'choice': 'SELL', 'confidence': 0.9, 'api_calls': 1}
    with patch.dict(sys.modules, {'vibe_event_jev': types.SimpleNamespace(review=review)}), \
         patch.object(event, 'current_position', side_effect=[True, False]), \
         patch.object(event, 'context_snapshot', return_value={}):
        assert event.process(row(), tmp_path / 'ledger', tmp_path / 'results', clock=lambda: NOW,
                             lock_path=tmp_path / 'jev.lock') == 'position_changed_or_stale'
    status, payload = fetch(tmp_path)[0]
    evidence = json.loads(payload)
    assert status == 'position_changed_or_stale' and evidence['api_calls'] == 1
    assert evidence['source'] == 'jev_event' and evidence['api_call_status'] == 'reported'
    assert 'analysis' not in evidence


def test_expired_event_or_mismatched_identity_does_not_call_model(tmp_path):
    called = []
    item = row()
    item['event_trigger']['inputs']['quantity'] = 11
    assert run(tmp_path, item, model=lambda _: called.append(True)) == 'event_invalid_or_stale'
    assert run(tmp_path, row(NOW - timedelta(minutes=2)), model=lambda _: called.append(True)) == 'event_invalid_or_stale'
    assert not called


def test_model_timeout_counts_as_attempt_and_does_not_retry(tmp_path):
    def timeout(_):
        raise subprocess.TimeoutExpired('fake', 60)
    with patch.object(event, 'current_position', return_value=True), patch.object(event, 'context_snapshot', return_value={}):
        assert run(tmp_path, row(), model=timeout) == 'model_timeout'
        assert run(tmp_path, row(), model=timeout) == 'duplicate'
    assert_unknown_failure(tmp_path, 'model_timeout')


def test_shared_lock_busy_does_not_reserve_or_call_model(tmp_path):
    called = []
    with patch.object(event, 'current_position', return_value=True), event.model_lock(tmp_path / 'shared.lock'):
        assert run(tmp_path, row(), model=lambda _: called.append(True)) == 'advisor_busy'
    assert not called and not (tmp_path / 'results/events.sqlite3').exists()
    with patch.object(event, 'current_position', return_value=True), patch.object(event, 'context_snapshot', return_value={}):
        assert run(tmp_path, row()) == 'completed'


def test_symbol_cooldown_and_daily_limit_include_failed_attempts(tmp_path):
    with patch.object(event, 'current_position', return_value=True), patch.object(event, 'context_snapshot', return_value={}):
        assert run(tmp_path, row(), model=lambda _: 'invalid') == 'model_invalid_or_unavailable'
        later = NOW + timedelta(minutes=1)
        assert run(tmp_path, row(later), clock=lambda: later) == 'cooldown'
        for i in range(1, 6):
            symbol = f'{i:06d}'
            assert run(tmp_path, row(later, symbol=symbol), clock=lambda: later) == 'completed'
        assert run(tmp_path, row(later, symbol='000006'), clock=lambda: later) == 'daily_limit'
    assert len(fetch(tmp_path)) == 6


def test_model_result_arriving_after_event_ttl_is_discarded(tmp_path):
    clock = iter([NOW, NOW + timedelta(seconds=61)])
    with patch.object(event, 'current_position', return_value=True), patch.object(event, 'context_snapshot', return_value={}):
        assert run(tmp_path, row(), clock=lambda: next(clock)) == 'position_changed_or_stale'
    assert_unknown_failure(tmp_path, 'position_changed_or_stale')


def test_input_freshness_and_json_result_are_validated():
    item = row()
    item['event_trigger']['inputs']['quote_at'] = (NOW - timedelta(minutes=5)).isoformat()
    assert event._identity(item, NOW) is None
    assert event.parse('noise {"stance":"hold","reason":"변화 미확인"} trailing')['stance'] == 'hold'
    with pytest.raises(ValueError):
        event.parse('{"stance":"BUY","reason":"bad"}')


def test_worker_failures_are_visible_throttled_and_do_not_log_exception_text(tmp_path, capsys):
    seen = {}
    event.record_worker_error(tmp_path, ValueError('secret-value'), NOW, seen)
    event.record_worker_error(tmp_path, ValueError('secret-value'), NOW + timedelta(seconds=10), seen)
    event.record_worker_error(tmp_path, OSError('another-secret'), NOW + timedelta(seconds=11), seen)
    event.record_worker_error(tmp_path, ValueError('secret-value'), NOW + timedelta(minutes=5), seen)
    lines = (tmp_path / 'worker-status-2026-11-16.jsonl').read_text().splitlines()
    assert len(lines) == 3
    assert [json.loads(line)['error_type'] for line in lines] == ['ValueError', 'OSError', 'ValueError']
    assert 'secret' not in ''.join(lines) and 'secret' not in capsys.readouterr().out
