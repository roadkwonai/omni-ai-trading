import copy
import hashlib
import json
from contextlib import closing

import pytest

import schedule_dispatch as dispatch
import schedule_handoff as handoff
import schedules


@pytest.fixture
def source(tmp_path, monkeypatch):
    task = 'intraday-audit-hourly'
    prompt = 'audit instructions'
    job = {'id': f'{schedules.KINDS[1]}:{task}', 'kind': schedules.KINDS[1], 'key': task,
           'name': '감사', 'prompt': prompt, 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
           'schedule': {'cron': '20 9-15 * * 1-5'}, 'dependency_hashes': {'skill': '123'}}
    native = {'key': task, 'native': True, 'enabled': False, 'cron': job['schedule']['cron']}
    peer = {'key': 'automation', 'state': '일시 정지'}
    monkeypatch.setattr(handoff.schedule_bundle, 'inventory', lambda runtime: [job])
    monkeypatch.setattr(handoff, 'claude_jobs', lambda: [native])
    monkeypatch.setattr(handoff, 'codex_jobs', lambda: [peer])
    with closing(dispatch._open(tmp_path, write=True)) as db, db:
        db.execute('INSERT INTO routes VALUES (?, ?, ?, ?, ?)', (task, 'Claude', 'model', job['schedule']['cron'], '2026-11-25T09:00:00+09:00'))
    return tmp_path, job, native, peer


def test_handoff_releases_only_once_and_receipt_is_bound(source):
    runtime, job, _, _ = source
    dest = 'device-' + 'a' * 32
    receipt = handoff.prepare(runtime, dest, [job['id']])
    assert handoff.validate_receipt(receipt, dest) == receipt
    assert receipt['source_repo'] and receipt['source_runtime']
    assert handoff.prepare(runtime, dest, [job['id']]) == receipt
    with closing(dispatch._open(runtime)) as db:
        assert handoff.released(db, job['key'])
        assert db.execute('SELECT * FROM routes').fetchone() is None
    out = runtime / 'receipt.json'
    handoff.export_receipt(runtime, receipt['transfer_id'], out)
    assert out.stat().st_mode & 0o777 == 0o600
    assert json.loads(out.read_text()) == receipt
    with pytest.raises(FileExistsError):
        handoff.export_receipt(runtime, receipt['transfer_id'], out)
    second = runtime / 'receipt-second.json'
    handoff.export_receipt(runtime, receipt['transfer_id'], second)
    assert json.loads(second.read_text()) == receipt
    tampered = copy.deepcopy(receipt)
    tampered['jobs'][0]['schedule']['cron'] = '0 9 * * *'
    with pytest.raises(ValueError, match='변경'):
        handoff.validate_receipt(tampered, dest)
    with pytest.raises(ValueError, match='이 기기'):
        handoff.validate_receipt(receipt, 'device-' + 'b' * 32)


def test_unpaused_missing_peer_and_inflight_prevent_release(source):
    runtime, job, native, peer = source
    dest = 'device-' + 'a' * 32
    native['enabled'] = True
    with pytest.raises(ValueError, match='일시 정지'):
        handoff.prepare(runtime, dest, [job['id']])
    native['enabled'] = False
    peer['state'] = '확인 불가'
    with pytest.raises(ValueError, match='Codex'):
        handoff.prepare(runtime, dest, [job['id']])
    peer['state'] = '일시 정지'
    with closing(dispatch._open(runtime, write=True)) as db, db:
        db.execute("INSERT INTO runs(task,slot,started,provider,model,status) VALUES(?,?,?,?,?,?)",
                   (job['key'], '2026-11-25T09:20:00+09:00', '2026-11-25T09:20:00+09:00', 'Claude', 'model', '실행 중'))
    with pytest.raises(ValueError, match='실행 중'):
        handoff.prepare(runtime, dest, [job['id']])
    with closing(dispatch._open(runtime)) as db:
        assert not handoff.released(db, job['key'])
        assert db.execute('SELECT * FROM routes').fetchone()
