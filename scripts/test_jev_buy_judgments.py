from datetime import datetime, timedelta

import pytest

from jev_buy_judgments import compare


NOW = datetime.fromisoformat('2026-11-27T10:00:00+09:00')


def frame():
    return {'symbol': '005930', 'market': 'KOSPI', 'model': 'jev-1.14.0',
            'decision_at': NOW.isoformat(), 'input_digest': 'a' * 64,
            'capture': {'state': {'news': ['headline'], 'position': {'qty': 1}}},
            'series': {'005930': [(NOW - timedelta(minutes=1), 100)], '069500': [(NOW - timedelta(minutes=1), 200)]}}


def experience():
    return {'research_only': True, 'version': 'b' * 64, 'lessons': [{'market': 'KOSPI'}]}


def test_two_arms_keep_inputs_equal_except_qualified_experience():
    calls = []

    def judge(payload, image, key):
        calls.append((payload, image, key))
        return {'choice': '005930', 'confidence': .7, 'experience_use': 'USED', 'input_tokens': 100}

    result = compare(frame(), experience(), isolated_key='isolated', production_key='production',
                     independent_quota=True, reverse=True, judge=judge, clock=lambda: NOW)
    assert result['order'] == ['experience', 'baseline']
    assert len(calls) == 2 and calls[0][1] == calls[1][1]
    first, second = calls[0][0], calls[1][0]
    expected = first['state'].copy()
    expected.pop('validated_experience')
    assert expected == second['state']
    assert first['questions'] == second['questions']
    assert first['questions']['candidate']['criteria']['005930'].find('KOSPI') >= 0
    assert all(call[2] == 'isolated' for call in calls)
    assert result['arms']['experience']['decided_at'] == NOW.isoformat()
    assert result['arms']['baseline']['choice'] == '005930'


def test_unavailable_or_shared_key_and_unqualified_experience_never_call():
    calls = []

    def judge(*args):
        calls.append(args)
    for key, quota, summary in [('same', True, experience()), ('new', False, experience()), ('new', True, {})]:
        with pytest.raises(ValueError):
            compare(frame(), summary, isolated_key=key, production_key='same', independent_quota=quota, judge=judge)
    assert not calls


def test_partial_failure_preserves_success_and_no_retry():
    calls = []

    def judge(payload, image, key):
        calls.append(payload)
        if len(calls) == 2:
            raise TimeoutError('provider')
        return {'choice': 'WAIT', 'confidence': .8, 'experience_use': 'ABSENT', 'input_tokens': None}

    result = compare(frame(), experience(), isolated_key='new', production_key='old', independent_quota=True,
                     judge=judge, clock=lambda: NOW)
    assert len(calls) == 2
    assert result['arms']['baseline']['choice'] == 'WAIT'
    assert result['failed_arm'] == 'experience'
    assert result['error_type'] == 'TimeoutError'


def test_future_or_incomplete_bars_are_not_sent():
    calls = []
    candidate = frame()
    candidate['series']['005930'] = [(NOW, 100)]
    with pytest.raises(ValueError):
        compare(candidate, experience(), isolated_key='new', production_key='old', independent_quota=True,
                judge=lambda *x: calls.append(x), clock=lambda: NOW)
    assert not calls
