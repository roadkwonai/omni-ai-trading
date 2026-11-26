"""Failure diagnostics and serialization at the Jev HTTP boundary."""
from contextlib import nullcontext
import io
import json
import sqlite3
import urllib.error

import pytest

import nh_etf_auto_v2 as model


def test_json_safe_replaces_nonfinite_values_without_mutating_input():
    source = {'values': [float('nan'), float('inf'), -float('inf'), 4, {'x': 1.5}], 'ok': False}
    assert model.jev_json_safe(source) == {'values': [None, None, None, 4, {'x': 1.5}], 'ok': False}
    assert source['values'][0] != source['values'][0]


def test_jev_http_400_preserves_exception_and_safe_diagnostics(tmp_path, monkeypatch):
    import etf_news
    import jev_activity

    monkeypatch.setattr(model.old, 'dotenv_values', lambda *a, **kw: {'JEV_API_KEY': 'do-not-store-secret'})
    monkeypatch.setattr(jev_activity, 'regular_request', nullcontext)
    monkeypatch.setattr(etf_news, 'supplement_jev_topics', lambda *a: ([], None))
    attempts = []

    def fail(req, timeout):
        attempts.append(json.loads(req.data, parse_constant=lambda x: pytest.fail('invalid JSON constant')))
        body = b'{"error":{"message":"Invalid image dimensions. do-not-store-secret"}}'
        raise urllib.error.HTTPError(req.full_url, 400, 'Bad Request', {'x-typesafe-request-id': 'req_abcd1234'}, io.BytesIO(body))

    monkeypatch.setattr(model.urllib.request, 'urlopen', fail)
    bars = {'005930': [{'bsop_time': f'090{i:02d}00', 'stck_prpr': 100,
                        'stck_oprc': 100, 'stck_hgpr': 101, 'stck_lwpr': 99, 'vol': 1} for i in range(6)]}
    db_path = tmp_path / 'diagnostics.sqlite3'
    with pytest.raises(urllib.error.HTTPError) as raised:
        model._jev7_question(bars, [], 'program_entry', {'BUY': 'buy', 'WAIT': 'wait'},
                             extra_state={'volume': float('nan')}, program_research_db=db_path)
    assert raised.value.code == 400
    assert len(attempts) == 1
    assert attempts[0]['state']['volume'] is None
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT payload FROM snapshots WHERE kind='jev_http_error'").fetchall()
    assert len(rows) == 1
    text = rows[0][0]
    assert 'do-not-store-secret' not in text
    assert 'data_base64' not in text
    details = json.loads(text)
    assert details['http_status'] == 400
    assert details['reason'] == 'image_dimensions'
    assert details['request_id'] == 'req_abcd1234'
    assert details['image_pixels'] == [1200, 300]
    assert details['request_bytes'] > 0
    assert len(details['request_sha256']) == 64


def test_unrecognized_error_response_is_not_persisted(tmp_path):
    secret = 'account-or-secret-do-not-store'
    error = urllib.error.HTTPError('https://example.invalid', 400, 'bad', {}, io.BytesIO(secret.encode()))
    payload = {'state': {'x': 1}, 'model': 'test', 'questions': {'candidate': {'criteria': {'WAIT': 'wait'}}}}
    path = tmp_path / 'errors.sqlite3'
    model.record_jev_http_error(error, payload, b'{}', b'image', 'candidate', path=path)
    with sqlite3.connect(path) as conn:
        text = conn.execute("SELECT payload FROM snapshots WHERE kind='jev_http_error'").fetchone()[0]
    assert secret not in text
    assert json.loads(text)['reason'] == 'unclassified'


def test_preflight_rejects_deep_input_before_sending(tmp_path, monkeypatch):
    import jev_activity

    monkeypatch.setattr(jev_activity, 'regular_request', nullcontext)
    monkeypatch.setattr(model.old, 'dotenv_values', lambda *a, **kw: {'JEV_API_KEY': 'test'})
    monkeypatch.setattr(model.urllib.request, 'urlopen', lambda *a, **kw: pytest.fail('invalid request reached HTTP'))
    nested = None
    for _ in range(65):
        nested = [nested]
    db_path = tmp_path / 'preflight.sqlite3'
    with pytest.raises(ValueError, match='json_depth'):
        model._jev7_question(research_bars(), [], 'program_entry', {'WAIT': 'wait'},
                             extra_state={'deep': nested}, program_research_db=db_path)
    with sqlite3.connect(db_path) as db:
        details = json.loads(db.execute("SELECT payload FROM snapshots WHERE kind='jev_request_rejected'").fetchone()[0])
    assert details['reason'] == 'json_depth'


def research_bars():
    return {'005930': [{'bsop_time': f'09{i:02d}00', 'stck_prpr': 100,
                        'stck_oprc': 100, 'stck_hgpr': 101, 'stck_lwpr': 99, 'vol': 1} for i in range(6)]}


@pytest.mark.parametrize('response,reason', [('not-json', 'response_json'), ('{}', 'response_choice')])
def test_malformed_success_response_is_audited(response, reason, tmp_path, monkeypatch):
    import jev_activity

    monkeypatch.setattr(jev_activity, 'regular_request', nullcontext)
    monkeypatch.setattr(model.old, 'dotenv_values', lambda *a, **kw: {'JEV_API_KEY': 'test'})
    monkeypatch.setattr(model.urllib.request, 'urlopen', lambda *a, **kw: io.StringIO(response))
    path = tmp_path / 'bad-response.sqlite3'
    with pytest.raises(ValueError):
        model._jev7_question(research_bars(), [], 'program_entry', {'WAIT': 'wait'}, program_research_db=path)
    with sqlite3.connect(path) as db:
        payload = db.execute("SELECT payload FROM snapshots WHERE kind='jev_response_rejected'").fetchone()[0]
    assert json.loads(payload)['reason'] == reason


def test_transport_timeout_is_distinct_from_http_or_response_rejection(tmp_path, monkeypatch):
    import jev_activity

    monkeypatch.setattr(jev_activity, 'regular_request', nullcontext)
    monkeypatch.setattr(model.old, 'dotenv_values', lambda *a, **kw: {'JEV_API_KEY': 'test'})

    def timeout(*args, **kwargs):
        raise urllib.error.URLError(TimeoutError('private endpoint detail'))

    monkeypatch.setattr(model.urllib.request, 'urlopen', timeout)
    path = tmp_path / 'transport.sqlite3'
    with pytest.raises(urllib.error.URLError):
        model._jev7_question(research_bars(), [], 'program_entry', {'WAIT': 'wait'}, program_research_db=path)
    with sqlite3.connect(path) as db:
        payload = db.execute("SELECT payload FROM snapshots WHERE kind='jev_transport_error'").fetchone()[0]
    assert json.loads(payload)['reason'] == 'timeout'
    assert 'private endpoint detail' not in payload


def test_success_records_provider_tokens_and_timings_without_changing_choice(tmp_path, monkeypatch):
    import jev_activity

    monkeypatch.setattr(jev_activity, 'regular_request', nullcontext)
    monkeypatch.setattr(model.old, 'dotenv_values', lambda *a, **kw: {'JEV_API_KEY': 'test'})
    monkeypatch.setattr(model.urllib.request, 'urlopen', lambda *a, **kw: io.StringIO(json.dumps({
        'model': model.JEV_MODEL, 'usage': {'input_tokens': 321, 'ignored': 'secret'},
        'answers': {'program_entry': {'type': 'choice', 'choice': 'WAIT', 'confidence': 0.5}},
    })))
    db_path = tmp_path / 'metrics.sqlite3'
    result = model._jev7_question(research_bars(), [], 'program_entry', {'WAIT': 'wait'}, program_research_db=db_path)
    assert result['program_entry'] == {'type': 'choice', 'choice': 'WAIT', 'confidence': 0.5}
    with sqlite3.connect(db_path) as db:
        recorded = json.loads(db.execute('SELECT response FROM model_inputs').fetchone()[0])
    metrics = recorded['request_metrics']
    assert metrics['input_tokens'] == 321
    assert metrics['image_pixels'] == [1200, 300]
    assert metrics['criteria_count'] == 1
    assert metrics['total_ms'] >= metrics['chart_ms'] >= 0
    assert metrics['total_ms'] >= metrics['api_ms'] >= 0
    assert 'secret' not in json.dumps(recorded)


def test_metrics_keep_legacy_records_out_of_error_rate(tmp_path):
    from jev_explain import request_metrics

    with sqlite3.connect(tmp_path / 'market-research.sqlite3') as db:
        db.execute('CREATE TABLE model_inputs(time TEXT, engine TEXT, question TEXT, response TEXT)')
        db.execute('CREATE TABLE snapshots(time TEXT, kind TEXT, payload TEXT)')
        ts = '2026-11-26T10:00:00+09:00'
        for metrics in ({}, {'request_metrics': {'input_tokens': 40, 'total_ms': 10}},
                        {'request_metrics': {'input_tokens': None, 'total_ms': 20}}):
            db.execute('INSERT INTO model_inputs VALUES (?,?,?,?)', (ts, 'jev', 'candidate', json.dumps(metrics)))
        db.execute('INSERT INTO snapshots VALUES (?,?,?)', (ts, 'jev_http_error', json.dumps({
            'question': 'candidate', 'request_metrics': {'total_ms': 9}})))
    row = request_metrics(tmp_path, '2026-11-26')['candidate']
    assert row['success_measured'] == 2 and row['success_unmeasured'] == 1 and row['http_errors_measured'] == 1
    assert row['input_tokens'] == {'samples': 1, 'p50': 40, 'p95': 40}
    assert row['total_ms'] == {'samples': 2, 'p50': 15, 'p95': 20}
    assert request_metrics(tmp_path, '2026-11-26T01:01:00Z') == {}


def test_metrics_isolates_malformed_rows(tmp_path):
    from jev_explain import request_metrics

    with sqlite3.connect(tmp_path / 'market-research.sqlite3') as db:
        db.execute('CREATE TABLE model_inputs(time TEXT, engine TEXT, question TEXT, response TEXT)')
        db.execute('CREATE TABLE snapshots(time TEXT, kind TEXT, payload TEXT)')
        ts = '2026-11-26T10:00:00+09:00'
        for raw in ('null', 'bad-json', '{"request_metrics":{"input_tokens":"invalid","total_ms":9}}',
                    '{"request_metrics":{"input_tokens":20,"total_ms":12}}'):
            db.execute('INSERT INTO model_inputs VALUES (?,?,?,?)', (ts, 'jev', 'candidate', raw))
        db.execute('INSERT INTO snapshots VALUES (?,?,?)', (ts, 'jev_http_error', 'null'))
    result = request_metrics(tmp_path, '2026-11-26')
    assert result['candidate']['invalid_rows'] == 2
    assert result['candidate']['input_tokens'] == {'samples': 1, 'p50': 20, 'p95': 20}
    assert result['candidate']['total_ms']['samples'] == 2
    assert result['_unattributed']['invalid_rows'] == 1


def test_archive_uses_the_transmitted_questions(tmp_path, monkeypatch):
    import jev_activity

    monkeypatch.setattr(jev_activity, 'regular_request', nullcontext)
    monkeypatch.setattr(model.old, 'dotenv_values', lambda *a, **kw: {'JEV_API_KEY': 'test'})
    sent = []

    def respond(req, **kwargs):
        sent.append(json.loads(req.data)['questions']['program_entry'])
        return io.StringIO(json.dumps({'model': model.JEV_MODEL, 'answers': {'program_entry': {
            'type': 'choice', 'choice': 'WAIT', 'confidence': 0.5}}}))

    monkeypatch.setattr(model.urllib.request, 'urlopen', respond)
    path = tmp_path / 'archive.sqlite3'
    model._jev7_question(research_bars(), [], 'program_entry', {'WAIT': {'data': float('nan')}},
                         instructions={'example': float('inf')}, program_research_db=path)
    with sqlite3.connect(path) as db:
        saved = json.loads(db.execute('SELECT inputs FROM model_inputs').fetchone()[0])
    assert {key: saved[key] for key in ('criteria', 'instructions')} == {
        key: sent[0][key] for key in ('criteria', 'instructions')}
    assert (saved['question'], saved['model']) == ('program_entry', model.JEV_MODEL)


def test_preflight_sends_the_image_it_validated():
    import base64
    from PIL import Image
    from jev_request import prepare_request

    image = io.BytesIO()
    Image.new('RGB', (280, 300)).save(image, 'PNG')
    payload = {'state': {'chart': 'asset'}, 'questions': {},
               'assets': {'asset': {'type': 'image', 'format': 'png', 'data_base64': 'd3Jvbmc='}}}
    wire = json.loads(prepare_request(payload, image.getvalue(), 'asset').body)
    assert base64.b64decode(wire['assets']['asset']['data_base64']) == image.getvalue()


def test_png_without_end_chunk_is_rejected():
    from PIL import Image
    from jev_request import JevRequestRejected, png_pixels

    image = io.BytesIO()
    Image.new('RGB', (280, 300)).save(image, 'PNG')
    with pytest.raises(JevRequestRejected, match='image_format'):
        png_pixels(image.getvalue()[:-12])


def test_png_with_corrupt_image_data_is_rejected():
    from PIL import Image
    from jev_request import JevRequestRejected, png_pixels

    image = io.BytesIO()
    Image.new('RGB', (280, 300)).save(image, 'PNG')
    damaged = bytearray(image.getvalue())
    damaged[damaged.index(b'IDAT') + 6] ^= 1
    with pytest.raises(JevRequestRejected, match='image_format'):
        png_pixels(bytes(damaged))


def test_animated_png_is_rejected_before_sending():
    from PIL import Image
    from jev_request import JevRequestRejected, png_pixels

    image = io.BytesIO()
    frame = Image.new('RGB', (300, 300), 'black')
    frame.save(image, 'PNG', save_all=True, append_images=[Image.new('RGB', frame.size, 'white')], duration=100)
    with pytest.raises(JevRequestRejected, match='image_animation'):
        png_pixels(image.getvalue())


@pytest.mark.parametrize('problem,reason', [('small_image', 'image_dimensions'), ('wrong_ref', 'image_reference'),
                                           ('large_int', 'json_number_range'), ('large_body', 'request_bytes')])
def test_request_preflight_validates_serialized_limits(problem, reason, monkeypatch):
    from PIL import Image
    import jev_request

    image = io.BytesIO()
    Image.new('RGB', (279 if problem == 'small_image' else 280, 300)).save(image, 'PNG')
    payload = {'state': {'chart': 'other' if problem == 'wrong_ref' else 'chart'},
               'assets': {'chart': {'type': 'image'}}, 'questions': {'entry': {'criteria': {'WAIT': 'wait'}}}}
    if problem == 'large_int':
        payload['state']['x'] = 1 << 2048
    if problem == 'large_body':
        monkeypatch.setattr(jev_request, 'MAX_BODY_BYTES', 30)
    with pytest.raises(jev_request.JevRequestRejected, match=reason):
        jev_request.prepare_request(payload, image.getvalue(), 'chart')
