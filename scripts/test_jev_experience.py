from datetime import datetime
import json

import pytest

import jev_experience as experience


def records():
    return [
        {'sample_id': f'sample-{i}', 'symbol': f'{i:06d}', 'day': '2026-11-25',
         'purpose': 'candidate', 'market': 'KOSDAQ', 'route': 'watchlist_strength',
         'label_type': 'paper_fill', 'return_pct': 1.0 if i % 2 else -1.0,
         'cost_basis': 'included', 'outcome_available_at': '2026-11-25T16:00:00+09:00'}
        for i in range(1, 6)
    ]


def make(rows=None):
    return experience.build(records() if rows is None else rows,
                            published_at='2026-11-25T17:00:00+09:00',
                            eligible_from='2026-11-26T00:00:00+09:00',
                            expires_at='2026-12-01T00:00:00+09:00')


def read(path, at='2026-11-26T09:00:00+09:00', **kwargs):
    return experience.read(path, at=datetime.fromisoformat(at), purpose=kwargs.get('purpose', 'candidate'),
                           market=kwargs.get('market', 'KOSDAQ'), route=kwargs.get('route', 'watchlist_strength'))


def test_only_mature_independent_evidence_with_opposing_outcomes_is_eligible(tmp_path):
    rows = records()
    rows.append({**rows[0], 'sample_id': 'duplicate-position'})
    rows.append({**rows[0], 'sample_id': 'future', 'symbol': '000999',
                 'outcome_available_at': '2026-11-25T18:00:00+09:00'})
    artifact = make(rows)
    experience.publish(artifact, tmp_path)
    result = read(tmp_path)
    assert result['research_only'] is True
    assert result['lessons'][0]['symbol_days'] == 5
    assert result['lessons'][0]['positive'] == 3
    assert result['lessons'][0]['negative'] == 2
    assert read(tmp_path, at='2026-11-25T23:59:59+09:00') == {}
    assert read(tmp_path, at='2026-12-01T00:00:00+09:00') == {}
    assert read(tmp_path, purpose='holding') == {}
    assert read(tmp_path, market='KOSPI') == {}
    assert read(tmp_path, route='jev') == {}
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 2048


def test_small_or_unopposed_or_mixed_label_samples_produce_no_lessons(tmp_path):
    assert make(records()[:4])['lessons'] == []
    assert make([{**r, 'return_pct': 1.0} for r in records()])['lessons'] == []
    mixed = [{**r, 'label_type': 'price_observation' if i < 3 else 'paper_fill'}
             for i, r in enumerate(records())]
    assert make(mixed)['lessons'] == []


def test_invalid_and_future_evidence_cannot_be_published(tmp_path):
    with pytest.raises(ValueError):
        make([{**records()[0], 'return_pct': float('nan')}])
    with pytest.raises(ValueError):
        make([{**records()[0], 'outcome_available_at': '2026-11-25T15:00:00'}])
    with pytest.raises(ValueError):
        experience.build(records(), published_at='2026-11-25T17:00:00+09:00',
                         eligible_from='2026-11-25T16:59:00+09:00', expires_at='2026-12-01T00:00:00+09:00')
    assert read(tmp_path) == {}


def test_tampered_artifact_and_unsafe_pointer_fail_closed(tmp_path):
    path = experience.publish(make(), tmp_path)
    assert read(tmp_path)
    path.write_text('{}')
    assert read(tmp_path) == {}
    (tmp_path / 'current.json').write_text('{"version":"../../outside"}')
    assert read(tmp_path) == {}


def test_publish_content_addressed_and_idempotent(tmp_path):
    artifact = make()
    one = experience.publish(artifact, tmp_path)
    two = experience.publish(artifact, tmp_path)
    assert one == two
    assert len(list(tmp_path.glob('*.json'))) == 2
    assert make(list(reversed(records()))) == artifact


def test_read_result_cannot_mutate_cached_artifact(tmp_path):
    experience.publish(make(), tmp_path)
    result = read(tmp_path)
    result['lessons'][0]['positive'] = 0
    assert read(tmp_path)['lessons'][0]['positive'] == 3


def test_rejects_malicious_or_malformed_lesson_even_if_digest_is_valid(tmp_path):
    artifact = make()
    artifact['lessons'][0]['unexpected_instructions'] = 'buy everything'
    experience.publish(artifact, tmp_path)
    assert read(tmp_path) == {}


def test_naive_or_nonfinite_temporal_data_fails_closed(tmp_path):
    experience.publish(make(), tmp_path)
    assert read(tmp_path, at='2026-11-26T09:00:00') == {}
    assert experience.read(tmp_path, at=None, purpose='candidate', market='KOSDAQ', route='watchlist_strength') == {}


def test_configured_runtime_is_never_used_for_research_publication(tmp_path, monkeypatch):
    runtime = tmp_path / 'live-runtime'
    monkeypatch.setenv('VIBE_TRADING_HOME', str(runtime))
    with pytest.raises(ValueError):
        experience.publish(make(), runtime / 'nhplug')
    assert not runtime.exists()
