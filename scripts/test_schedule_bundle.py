import hashlib
import json
import os

import pytest

import schedule_bundle as bundle


def job():
    prompt = '검증용\n'
    return {'id': 'Claude 예약 작업:review', 'kind': 'Claude 예약 작업', 'key': 'review', 'name': '감사',
            'schedule': {'timezone': 'Asia/Seoul', 'cron': '20 9-15 * * 1-5'},
            'prompt': prompt, 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()}


def test_rejects_duplicate_tampering_path_and_secret():
    item = job()
    original = {'version': 1, 'jobs': [item]}
    assert bundle.validate_bundle(original) == original
    for invalid in (
        {'version': 2, 'jobs': [item]},
        {'version': 1, 'jobs': [item, item]},
        {'version': 1, 'jobs': [{**item, 'prompt': 'changed'}]},
        {'version': 1, 'jobs': [{**item, 'key': '../secret'}]},
        {'version': 1, 'jobs': [{**item, 'prompt': 'api_key=abcdefghijklmnop'}]},
    ):
        with pytest.raises(ValueError):
            bundle.validate_bundle(invalid)


def test_export_is_private_exclusive_and_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, 'inventory', lambda **kwargs: [job()])
    path = tmp_path / 'tasks.json'
    result = bundle.export_bundle(None, path)
    assert result['jobs'] == 1
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert bundle.read_bundle(path)['jobs'] == [job()]
    with pytest.raises(FileExistsError):
        bundle.export_bundle(None, path)
    assert bundle.read_bundle(path)['jobs'] == [job()]


def test_inventory_does_not_export_prompt_with_secret(tmp_path, monkeypatch):
    import schedules
    import state_sync
    import schedule_dispatch
    import automation_status
    home = tmp_path / 'home'
    task = home / '.claude/scheduled-tasks/review'
    task.mkdir(parents=True)
    (task / 'SKILL.md').write_text('API_KEY=abcdefghijklmnop', encoding='utf-8')
    monkeypatch.setattr(schedules, 'all_jobs', lambda *a, **k: [{
        'kind': 'Claude 예약 작업', 'key': 'review', 'name': '감사', 'state': '등록됨',
        'cron': '0 9 * * 1-5', 'times': [(9, 0)], 'days': frozenset(range(5))}])
    monkeypatch.setattr(state_sync, 'app_schedules', lambda *a, **k: ({'review': {'cron': '0 9 * * 1-5'}}, set()))
    monkeypatch.setattr(schedule_dispatch, 'route_rows', lambda *a, **k: ({}, {}))
    monkeypatch.setattr(automation_status, 'processes', lambda: {})
    item, = bundle.inventory(home=home)
    assert item['prompt'] is None and item['prompt_sha256'] is None
    assert item['prompt_status'] == '민감정보 확인 필요'
    assert 'abcdefghijklmnop' not in json.dumps(item, ensure_ascii=False)
