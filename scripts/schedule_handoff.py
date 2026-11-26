"""Release local scheduler ownership and make a destination-bound handoff receipt."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

import schedule_bundle
import schedule_dispatch as dispatch
from schedules import KINDS, KST, REPO, claude_jobs, codex_jobs
from state_sync import runtime_root

DEVICE_ID = re.compile(r'device-[0-9a-f]{32}')
TRANSFER_ID = re.compile(r'transfer-[0-9a-f]{32}')


def _tables(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS schedule_device (id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS schedule_transfers (id TEXT PRIMARY KEY, destination TEXT NOT NULL, receipt TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS schedule_released (task TEXT PRIMARY KEY, transfer_id TEXT NOT NULL, destination TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS schedule_signing_key (id INTEGER PRIMARY KEY CHECK(id=1), private_key BLOB NOT NULL);
    """)


def device_id(runtime=None):
    """Stable opaque ID for this installation, stored with the scheduler."""
    with closing(dispatch._open(runtime, write=True)) as db, db:
        _tables(db)
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT id FROM schedule_device').fetchone()
        if row:
            return row['id']
        value = f'device-{uuid.uuid4().hex}'
        db.execute('INSERT INTO schedule_device VALUES (?)', (value,))
        return value


def _digest(value):
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(canonical).hexdigest()


def _signing_key(db):
    row = db.execute('SELECT private_key FROM schedule_signing_key WHERE id=1').fetchone()
    if row:
        return Ed25519PrivateKey.from_private_bytes(row[0])
    key = Ed25519PrivateKey.generate()
    db.execute('INSERT INTO schedule_signing_key VALUES (1, ?)', (key.private_bytes_raw(),))
    return key


def device_info(runtime=None):
    device = device_id(runtime)
    with closing(dispatch._open(runtime, write=True)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        public = _signing_key(db).public_key().public_bytes_raw()
    return {'device': device, 'signing_fingerprint': hashlib.sha256(public).hexdigest()}


def released(db, task):
    """Whether this source installation permanently released this task."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schedule_released'").fetchone():
        return False
    return bool(db.execute('SELECT 1 FROM schedule_released WHERE task=?', (task,)).fetchone())


def _check_source(job, native, codex):
    key = job['key']
    if job['kind'] != KINDS[1] or key not in dispatch.TARGETS:
        raise ValueError(f'{key}: 현재 이전할 수 있는 Claude 정기 작업이 아닙니다.')
    if not job.get('prompt_sha256') or not job.get('prompt') or not job.get('schedule', {}).get('cron'):
        raise ValueError(f'{key}: 원본 지시문 또는 일정이 없습니다.')
    entry = native.get(key)
    if not entry or entry.get('native') is not True or entry.get('enabled') is not False:
        raise ValueError(f'{key}: 원본 Claude 예약의 일시 정지 상태를 확인할 수 없습니다.')
    if entry.get('cron') != job['schedule']['cron']:
        raise ValueError(f'{key}: 원본 일정이 변경됐습니다.')
    peer_id = dispatch.CODEX_EQUIVALENTS.get(key)
    if peer_id:
        peer = codex.get(peer_id)
        if peer is None or peer.get('state') != '일시 정지':
            raise ValueError(f'{key}: 같은 작업의 Codex 예약 일시 정지를 확인할 수 없습니다.')


def prepare(runtime, destination, job_ids):
    """Atomically release selected jobs. Existing released jobs can only be exported to their original destination."""
    source = device_id(runtime)
    if not isinstance(destination, str) or not DEVICE_ID.fullmatch(destination) or destination == source:
        raise ValueError('다른 기기의 예약 실행기 ID가 필요합니다.')
    if not job_ids or len(set(job_ids)) != len(job_ids):
        raise ValueError('중복되지 않은 이전 작업 ID를 선택하세요.')
    all_jobs = {j['id']: j for j in schedule_bundle.inventory(runtime)}
    if any(key not in all_jobs for key in job_ids):
        raise ValueError('원본 목록에 없는 작업이 있습니다.')
    chosen = [all_jobs[key] for key in job_ids]
    native = {j['key']: j for j in claude_jobs()}
    codex = {j['key']: j for j in codex_jobs()}
    for job in chosen:
        _check_source(job, native, codex)

    with closing(dispatch._open(runtime, write=True)) as db, db:
        _tables(db)
        db.execute('BEGIN IMMEDIATE')
        placeholders = ','.join('?' for _ in chosen)
        keys = [j['key'] for j in chosen]
        rows = list(db.execute(f'SELECT task, transfer_id, destination FROM schedule_released WHERE task IN ({placeholders})', keys))
        if rows:
            ids = {r['transfer_id'] for r in rows}
            if len(rows) != len(keys) or len(ids) != 1 or any(r['destination'] != destination for r in rows):
                raise ValueError('이미 다른 이관에 포함된 작업입니다.')
            saved = db.execute('SELECT receipt FROM schedule_transfers WHERE id=?', (rows[0]['transfer_id'],)).fetchone()
            if saved is None:
                raise ValueError('기록된 이전 영수증을 찾을 수 없습니다.')
            return json.loads(saved['receipt'])
        native = {j['key']: j for j in claude_jobs()}
        codex = {j['key']: j for j in codex_jobs()}
        fresh = {job['id']: job for job in schedule_bundle.inventory(runtime)}
        for job in chosen:
            _check_source(job, native, codex)
            current = fresh.get(job['id']) or {}
            if (job.get('prompt_sha256') != current.get('prompt_sha256')
                    or job.get('dependency_hashes') != current.get('dependency_hashes')):
                raise ValueError(f"{job['key']}: 원본 지시문이나 참조 자료가 변경됐습니다.")
            running = db.execute("SELECT 1 FROM runs WHERE task=? AND status='실행 중'", (job['key'],)).fetchone()
            if running:
                raise ValueError(f"{job['key']}: 실행 중인 회차가 있습니다.")
        transfer = f'transfer-{uuid.uuid4().hex}'
        released_at = datetime.now(KST).isoformat()
        for job in chosen:
            latest = db.execute('SELECT max(slot) FROM runs WHERE task=?', (job['key'],)).fetchone()[0]
            job['last_slot'] = latest
            db.execute('INSERT INTO schedule_released VALUES (?, ?, ?)', (job['key'], transfer, destination))
            db.execute('DELETE FROM routes WHERE task=?', (job['key'],))
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='schedule_bindings'").fetchone():
                db.execute('DELETE FROM schedule_bindings WHERE task=?', (job['key'],))
        signing = _signing_key(db)
        body = {'version': 1, 'transfer_id': transfer, 'source': source, 'destination': destination,
                'source_repo': str(REPO), 'source_runtime': str(runtime_root(runtime)),
                'released_at': released_at, 'jobs': chosen, 'signing_key': signing.public_key().public_bytes_raw().hex()}
        digest = _digest(body)
        receipt = {**body, 'sha256': digest, 'signature': signing.sign(bytes.fromhex(digest)).hex()}
        db.execute('INSERT INTO schedule_transfers VALUES (?, ?, ?)',
                   (transfer, destination, json.dumps(receipt, ensure_ascii=False)))
        return receipt


def export_receipt(runtime, transfer_id, path):
    """Export a committed receipt. Repeated calls can use another destination file after an I/O failure."""
    if not TRANSFER_ID.fullmatch(str(transfer_id)):
        raise ValueError('이전 ID가 올바르지 않습니다.')
    with closing(dispatch._open(runtime)) as db:
        try:
            row = db.execute('SELECT receipt FROM schedule_transfers WHERE id=?', (transfer_id,)).fetchone()
        except Exception as exc:
            raise ValueError('이전 영수증을 찾을 수 없습니다.') from exc
        if not row:
            raise ValueError('이전 영수증을 찾을 수 없습니다.')
        data = row['receipt'].encode('utf-8')
    target = Path(path).expanduser()
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {'id': transfer_id, 'path': str(target)}


def validate_receipt(receipt, destination, *, source_key=None):
    """Validate content and signature. Pass a trusted source fingerprint to verify origin."""
    if not isinstance(receipt, dict) or receipt.get('version') != 1:
        raise ValueError('지원하지 않는 이전 영수증입니다.')
    digest = receipt.get('sha256')
    body = {k: v for k, v in receipt.items() if k not in ('sha256', 'signature')}
    if not isinstance(digest, str) or not hmac.compare_digest(digest, _digest(body)):
        raise ValueError('이전 영수증이 변경됐습니다.')
    try:
        public = bytes.fromhex(receipt['signing_key'])
        Ed25519PublicKey.from_public_bytes(public).verify(bytes.fromhex(receipt['signature']), bytes.fromhex(digest))
    except (KeyError, TypeError, ValueError, InvalidSignature) as exc:
        raise ValueError('이전 영수증의 원본 서명을 확인할 수 없습니다.') from exc
    if source_key is not None and not hmac.compare_digest(hashlib.sha256(public).hexdigest(), source_key.lower()):
        raise ValueError('원본 기기에서 확인한 서명 지문과 다릅니다.')
    source = receipt.get('source')
    target = receipt.get('destination')
    if (not isinstance(source, str) or not DEVICE_ID.fullmatch(source) or not isinstance(target, str)
            or not DEVICE_ID.fullmatch(target) or target != destination or source == target):
        raise ValueError('이 기기용 이전 영수증이 아닙니다.')
    if not isinstance(receipt.get('transfer_id'), str) or not TRANSFER_ID.fullmatch(receipt['transfer_id']):
        raise ValueError('이전 ID가 올바르지 않습니다.')
    for field in ('source_repo', 'source_runtime'):
        if not isinstance(receipt.get(field), str) or not Path(receipt[field]).is_absolute():
            raise ValueError('원본 경로가 올바르지 않습니다.')
    try:
        released = datetime.fromisoformat(receipt['released_at'])
        if released.tzinfo is None:
            raise ValueError
    except (KeyError, ValueError, TypeError):
        raise ValueError('이전 시각을 확인할 수 없습니다.') from None
    schedule_bundle.validate_bundle({'version': schedule_bundle.VERSION, 'jobs': receipt.get('jobs')})
    if not receipt['jobs']:
        raise ValueError('이전할 작업이 없습니다.')
    for job in receipt['jobs']:
        if (job['kind'] != KINDS[1] or job['key'] not in dispatch.TARGETS
                or not job.get('prompt') or not job.get('schedule', {}).get('cron')):
            raise ValueError('지원하지 않는 작업이 포함되어 있습니다.')
        try:
            from schedules import cron_job
            if job.get('last_slot') and datetime.fromisoformat(job['last_slot']).tzinfo is None:
                raise ValueError
            if job['schedule'].get('timezone', 'Asia/Seoul') != 'Asia/Seoul':
                raise ValueError
            cron_job(job['schedule']['cron'])
        except (ValueError, TypeError, KeyError):
            raise ValueError('지원하지 않는 작업 일정입니다.') from None
    return receipt
