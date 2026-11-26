import hashlib
import json

import pytest

import jev_input_compare as c


def capture():
    return {'state': {'chart': 'chart-a', 'premarket_context': {'as_of': 'today', 'sources': {'news': {'observed_at': '09:00'}},
             'news': [{'title': str(i)} for i in range(9)], 'disclosures': [{'code': '005930'}, {'code': '000001'}],
             'overnight_material': {'stocks': {'005930': {'headline': 'x'}, '000001': {'headline': 'y'}}}},
             'trading_journal': {'sessions': [{'day': str(i), 'lessons': ['keep']} for i in range(3)], 'periods': [{'period': 'x'}]},
             'watchlist_details': {'005930': {'name': '삼성'}}, 'position': {'symbol': '005930'}},
            'criteria': {'BUY': 'buy', 'WAIT': 'wait'}, 'instructions': 'assess', 'chart_sha256': hashlib.sha256(b'chart').hexdigest()}


def test_comparison_preserves_decision_and_provenance():
    data = capture()
    original = json.loads(json.dumps(data))
    variant = c.condense(data)
    assert data == original
    assert variant['criteria'] == original['criteria'] and variant['instructions'] == original['instructions']
    assert variant['state']['chart'] == original['state']['chart']
    assert variant['state']['premarket_context']['sources'] == original['state']['premarket_context']['sources']
    assert len(variant['state']['premarket_context']['news']) == 5
    assert [x['code'] for x in variant['state']['premarket_context']['disclosures']] == ['005930']
    assert len(variant['state']['trading_journal']['sessions']) == 1
    assert c.comparison(data)['condensed_bytes'] < c.comparison(data)['original_bytes']


def test_require_original_matching_chart(tmp_path):
    png = tmp_path / 'chart.png'
    png.write_bytes(b'bad')
    with pytest.raises(ValueError):
        c.validate_chart(capture(), png)
    png.write_bytes(b'chart')
    assert c.validate_chart(capture(), png) == b'chart'


def test_byte_comparison_works_without_archived_image(tmp_path, monkeypatch, capsys):
    source = tmp_path / 'capture.json'
    source.write_text(json.dumps(capture()))
    monkeypatch.setattr('sys.argv', ['jev_input_compare', str(source)])
    c.main()
    result = json.loads(capsys.readouterr().out)
    assert result['model_calls'] == 0 and result['chart_verified'] is False
    assert result['condensed_bytes'] < result['original_bytes']


def test_comparison_keeps_recorded_question_and_model(tmp_path, monkeypatch, capsys):
    source = tmp_path / 'capture.json'
    source.write_text(json.dumps({**capture(), 'question': 'holding', 'model': 'jev-1.13.0'}))
    monkeypatch.setattr('sys.argv', ['jev_input_compare', str(source)])
    c.main()
    result = json.loads(capsys.readouterr().out)
    assert (result['question'], result['model']) == ('holding', 'jev-1.13.0')
    monkeypatch.setattr('sys.argv', ['jev_input_compare', str(source), '--question', 'candidate'])
    with pytest.raises(SystemExit) as exc:
        c.main()
    assert exc.value.code == 2


def test_market_hours_forbid_api():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    with pytest.raises(ValueError):
        c.check_market(datetime(2026, 11, 26, 10, 0, tzinfo=ZoneInfo('Asia/Seoul')))


@pytest.mark.parametrize('probabilities', [{'WAIT': .2, 'BUY': .8}, {'WAIT': .8, 'BUY': .8}])
def test_comparison_rejects_inconsistent_probability_distribution(probabilities):
    result = {'model': 'jev-1.14.0', 'answers': {'candidate': {'type': 'choice', 'choice': 'WAIT',
              'confidence': .5, 'probabilities': probabilities}}}
    with pytest.raises(ValueError):
        c.answer(result, 'candidate', {'WAIT': 'wait', 'BUY': 'buy'}, 'jev-1.14.0')


@pytest.mark.parametrize('failure', [None, 'second_request', 'output_conflict'])
def test_two_requests_change_only_the_two_context_fields(tmp_path, monkeypatch, capsys, failure):
    import io
    import sys
    from PIL import Image
    import dotenv

    out = io.BytesIO()
    Image.new('RGB', (300, 300)).save(out, 'PNG')
    png = tmp_path / 'chart.png'
    png.write_bytes(out.getvalue())
    data = capture()
    data['chart_sha256'] = hashlib.sha256(png.read_bytes()).hexdigest()
    source = tmp_path / 'capture.json'
    source.write_text(json.dumps(data))
    output = tmp_path / 'result.json'
    sent = []
    def fake_request(payload, key, image):
        assert image == png.read_bytes()
        sent.append(payload)
        if failure == 'second_request' and len(sent) == 2:
            raise TimeoutError('private response contents')
        if failure == 'output_conflict' and len(sent) == 2:
            output.write_text('existing result')
        return {'model': 'jev-1.14.0', 'answers': {'candidate': {'type': 'choice', 'choice': 'WAIT', 'confidence': 0.5}},
                'usage': {'input_tokens': 100 if len(sent) == 1 else 60}}
    monkeypatch.setattr(c, 'check_market', lambda _: None)
    monkeypatch.setattr(c, 'request', fake_request)
    monkeypatch.setattr(dotenv, 'dotenv_values', lambda *a, **k: {'JEV_API_KEY': 'private'})
    monkeypatch.setattr(sys, 'argv', ['jev_input_compare', str(source), '--chart', str(png), '--question', 'candidate',
                                       '--model', 'jev-1.14.0', '--ask-jev', '--output', str(output)])
    if failure:
        with pytest.raises(SystemExit) as exc:
            c.main()
        assert exc.value.code == 1
    else:
        c.main()
    result = json.loads(capsys.readouterr().out)
    assert result['answers']['original']['input_tokens'] == 100
    if failure == 'second_request':
        assert result['failed_arm'] == {'arm': 'condensed', 'error_type': 'TimeoutError'}
        assert result['choice_matches'] is None
        assert json.loads(output.read_text()) == result
        assert 'private response contents' not in output.read_text()
        return
    if failure == 'output_conflict':
        assert output.read_text() == 'existing result'
        assert result['answers']['condensed']['input_tokens'] == 60
        return
    assert result['answers']['condensed']['input_tokens'] == 60
    assert result['choice_matches'] is True
    assert 'private' not in output.read_text()
    assert sent[0]['assets'] == sent[1]['assets'] and sent[0]['questions'] == sent[1]['questions']
    for field in sent[0]['state']:
        if field not in ('premarket_context', 'trading_journal'):
            assert sent[0]['state'][field] == sent[1]['state'][field]


def test_experience_comparison_uses_archived_time_and_only_changes_experience(tmp_path, monkeypatch, capsys):
    import sys
    import types
    from datetime import datetime

    source = tmp_path / 'capture.json'
    source.write_text(json.dumps({**capture(), 'question': 'candidate', 'model': 'jev-1.14.0'}))
    seen = []
    def read(directory, *, at, purpose, market, route):
        seen.append((directory, at, purpose, market, route))
        return {'version': 'v1', 'lessons': [{'id': 'l1'}]}
    monkeypatch.setitem(sys.modules, 'jev_experience', types.SimpleNamespace(read=read))
    monkeypatch.setattr(c, 'request', lambda *args: pytest.fail('model was called'))
    monkeypatch.setattr(sys, 'argv', ['jev_input_compare', str(source), '--experience-dir', str(tmp_path),
                        '--decision-at', '2026-11-25T10:00:00+09:00', '--market', 'KOSPI', '--route', 'jev'])
    c.main()
    result = json.loads(capsys.readouterr().out)
    assert result['model_calls'] == 0
    assert result['experience_version'] == 'v1'
    assert result['experience_status'] == 'available'
    assert seen == [(tmp_path, datetime.fromisoformat('2026-11-25T10:00:00+09:00'), 'candidate', 'KOSPI', 'jev')]
    variant = c.with_experience(capture(), {'version': 'v1', 'lessons': [{'id': 'l1'}]})
    assert variant['criteria'] == capture()['criteria']
    assert {k: v for k, v in variant['state'].items() if k != 'validated_experience'} == capture()['state']


def test_experience_comparison_requires_aware_decision_time_and_context(tmp_path, monkeypatch):
    import sys

    source = tmp_path / 'capture.json'
    source.write_text(json.dumps({**capture(), 'question': 'candidate', 'model': 'jev-1.14.0'}))
    for extra in ([], ['--decision-at', '2026-11-25T10:00:00', '--market', 'KOSPI', '--route', 'jev']):
        monkeypatch.setattr(sys, 'argv', ['jev_input_compare', str(source), '--experience-dir', str(tmp_path), *extra])
        with pytest.raises(SystemExit) as exc:
            c.main()
        assert exc.value.code == 2


def test_experience_comparison_records_missing_as_incomparable(tmp_path, monkeypatch, capsys):
    import sys
    import types

    source = tmp_path / 'capture.json'
    source.write_text(json.dumps({**capture(), 'question': 'candidate', 'model': 'jev-1.14.0'}))
    monkeypatch.setitem(sys.modules, 'jev_experience', types.SimpleNamespace(read=lambda *a, **k: {}))
    monkeypatch.setattr(sys, 'argv', ['jev_input_compare', str(source), '--experience-dir', str(tmp_path),
                        '--decision-at', '2026-11-25T10:00:00+09:00', '--market', 'KOSPI', '--route', 'jev'])
    c.main()
    result = json.loads(capsys.readouterr().out)
    assert result['experience_status'] == 'unavailable'
    assert result['experience_version'] is None
    assert result['model_calls'] == 0


def test_experience_paired_requests_change_only_experience(tmp_path, monkeypatch, capsys):
    import io
    import sys
    import types
    from PIL import Image
    import dotenv

    png = tmp_path / 'chart.png'
    image = io.BytesIO()
    Image.new('RGB', (300, 300)).save(image, 'PNG')
    png.write_bytes(image.getvalue())
    data = {**capture(), 'question': 'candidate', 'model': 'jev-1.14.0',
            'decision_at': '2026-11-25T10:00:00+09:00'}
    data['chart_sha256'] = hashlib.sha256(png.read_bytes()).hexdigest()
    source = tmp_path / 'capture.json'
    source.write_text(json.dumps(data))
    sent = []
    def request(payload, key, raw):
        assert raw == png.read_bytes()
        sent.append(payload)
        return {'model': 'jev-1.14.0', 'answers': {'candidate': {'type': 'choice', 'choice': 'WAIT', 'confidence': 0.5}}}
    monkeypatch.setitem(sys.modules, 'jev_experience', types.SimpleNamespace(
        read=lambda *a, **k: {'version': 'v1', 'research_only': True, 'lessons': [{'id': 'l1'}]}))
    monkeypatch.setattr(c, 'check_market', lambda _: None)
    monkeypatch.setattr(c, 'request', request)
    monkeypatch.setattr(dotenv, 'dotenv_values', lambda *a, **k: {'JEV_API_KEY': 'private'})
    monkeypatch.setattr(sys, 'argv', ['jev_input_compare', str(source), '--chart', str(png), '--ask-jev',
                        '--experience-dir', str(tmp_path), '--decision-at', data['decision_at'],
                        '--market', 'KOSPI', '--route', 'jev'])
    c.main()
    result = json.loads(capsys.readouterr().out)
    assert result['decision_time_provenance'] == 'capture'
    assert result['model_calls'] == 2 and result['choice_matches'] is True
    assert sent[0]['model'] == sent[1]['model']
    assert sent[0]['assets'] == sent[1]['assets'] and sent[0]['questions'] == sent[1]['questions']
    assert {k: v for k, v in sent[1]['state'].items() if k != 'validated_experience'} == sent[0]['state']


def test_experience_timestamp_mismatch_rejected_before_read(tmp_path, monkeypatch):
    import sys
    import types

    source = tmp_path / 'capture.json'
    source.write_text(json.dumps({**capture(), 'question': 'candidate', 'model': 'jev-1.14.0',
                                  'decision_at': '2026-11-25T10:00:00+09:00'}))
    monkeypatch.setitem(sys.modules, 'jev_experience', types.SimpleNamespace(read=lambda *a, **k: pytest.fail('read called')))
    monkeypatch.setattr(sys, 'argv', ['jev_input_compare', str(source), '--experience-dir', str(tmp_path),
                        '--decision-at', '2026-11-25T10:01:00+09:00', '--market', 'KOSPI', '--route', 'jev'])
    with pytest.raises(SystemExit):
        c.main()


def test_experience_model_comparison_requires_archived_decision_time(tmp_path, monkeypatch):
    import sys
    import types

    source = tmp_path / 'capture.json'
    source.write_text(json.dumps({**capture(), 'question': 'candidate', 'model': 'jev-1.14.0'}))
    monkeypatch.setitem(sys.modules, 'jev_experience', types.SimpleNamespace(read=lambda *a, **k: pytest.fail('read called')))
    monkeypatch.setattr(sys, 'argv', ['jev_input_compare', str(source), '--experience-dir', str(tmp_path), '--ask-jev',
                        '--decision-at', '2026-11-25T10:00:00+09:00', '--market', 'KOSPI', '--route', 'jev'])
    with pytest.raises(SystemExit):
        c.main()


def test_experience_comparison_reads_real_published_artifact(tmp_path, monkeypatch, capsys):
    import sys
    from datetime import datetime
    from jev_experience import build, publish

    source = tmp_path / 'capture.json'
    source.write_text(json.dumps({**capture(), 'question': 'candidate', 'model': 'jev-1.14.0'}))
    records = [{'sample_id': f'sample-{i}', 'symbol': f'{i:06d}', 'day': '2026-11-24',
                'purpose': 'candidate', 'market': 'KOSPI', 'route': 'jev', 'label_type': 'paper_fill',
                'cost_basis': 'included', 'return_pct': -1 if i == 0 else 1,
                'outcome_available_at': '2026-11-24T16:00:00+09:00'} for i in range(5)]
    artifact = build(records, published_at='2026-11-24T17:00:00+09:00',
                     eligible_from='2026-11-25T00:00:00+09:00', expires_at='2026-12-01T00:00:00+09:00')
    path = publish(artifact, tmp_path / 'experience')
    monkeypatch.setattr(sys, 'argv', ['jev_input_compare', str(source), '--experience-dir', str(path.parent),
                        '--decision-at', datetime.fromisoformat('2026-11-25T10:00:00+09:00').isoformat(),
                        '--market', 'KOSPI', '--route', 'jev'])
    c.main()
    result = json.loads(capsys.readouterr().out)
    assert result['experience_status'] == 'available'
    assert result['experience_version'] == path.stem
    assert result['model_calls'] == 0
