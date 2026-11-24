"""Plans and the person's approval of the ones that can reach real money.

A plan is written by `settings plan` and applied by `settings apply`. When it can reach a live account (level H) the apply
needs a one-time code that only the notification channel shows: a typed phrase is no approval, an agent can make itself a
terminal (measured 2026-11-24). Only a slow hash of the code is kept. This stops mistakes and overreach; it is not a wall
against a program running as the same user, which the live switches, HALT and LiveOrderGuard are.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import signal
import sqlite3
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta

import click

from . import settings as S
from .output import KST, OmniError, Refused

PLAN_LIFE = timedelta(minutes=30)
CODE_LIFE = timedelta(minutes=10)
TRIES = 3
SENDS = 3
HOURLY = 10
ALPHABET = '23456789ABCDEFGHJKMNPQRSTUVWXYZ'  # 31 letters: eight of them are about 40 bits
MARKET = ('08:55', '15:20')
SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, created TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL, result TEXT);
CREATE TABLE IF NOT EXISTS codes (plan TEXT NOT NULL, created TEXT NOT NULL, salt BLOB NOT NULL, hash BLOB NOT NULL,
    tries INTEGER NOT NULL DEFAULT 0, used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS paid (time TEXT NOT NULL, kind TEXT NOT NULL);
"""


def now():
    return datetime.now(KST)


@contextmanager
def store(nh):
    """The CLI's own file, locked for writing: one apply at a time, on any OS."""
    folder = nh.parent / 'cli'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / 'cli.sqlite3'
    db = sqlite3.connect(path, timeout=20, isolation_level=None)
    try:
        db.executescript(SCHEMA)
        os.chmod(path, 0o600)
        db.execute('BEGIN IMMEDIATE')
        yield db
        db.execute('COMMIT')
    except BaseException:
        if db.in_transaction:
            db.execute('ROLLBACK')
        raise
    finally:
        db.close()


def one_line(text):
    """What a person reads in a notice: never a line of its own that looks like another notice."""
    return re.sub(r'\s+', ' ', text or '').strip()[:200]


def make(nh, group, changes, reason, title=''):
    """Rehearse and keep a plan; nothing is written to a setting."""
    reason = one_line(reason)
    if not reason:
        raise Refused('변경 이유를 --reason으로 적으세요(이력에 남습니다).')
    stamp = S.stamp(group, nh)
    changed = S.rehearse(group, nh, changes)
    if not changed:
        raise Refused('지금 값과 같아 바뀌는 것이 없습니다.')
    level, affects = S.level(nh, changed)
    return keep(nh, {'type': 'settings', 'group': group.name, 'input': changes, 'changed': changed, 'stamp': stamp,
                     'reason': reason, 'title': title, 'level': level, 'affects': affects, 'reach': S.reach(nh, affects)})


def keep(nh, body):
    ctx = click.get_current_context(silent=True)
    body['agent'] = (ctx.find_root().obj or {}).get('agent', 'cli') if ctx else 'cli'
    created = now().isoformat(timespec='seconds')
    text = json.dumps(body, ensure_ascii=False, sort_keys=True)
    pid = hashlib.sha256(f'{created}{secrets.token_hex(4)}{text}'.encode()).hexdigest()[:8]
    with store(nh) as db:
        if body['type'] == 'action':  # a new request of the same kind on the same target replaces the one still open
            target = lambda b: (b.get('action'), {k: v for k, v in b.get('args', {}).items() if k != 'qty'})  # noqa: E731
            for old, text_ in db.execute("SELECT id, body FROM plans WHERE status='open' AND created>=?", ((now() - PLAN_LIFE).isoformat(timespec='seconds'),)).fetchall():
                if target(json.loads(text_)) == target(body):
                    close(db, old, 'replaced')
        db.execute('INSERT INTO plans VALUES (?,?,?,?,NULL)', (pid, created, text, 'open'))
    return view(pid, created, body, 'open')


def view(pid, created, body, status, result=None):
    asked = {f"{body.get('group')}.{k}" for k in body.get('input', {})} if body['type'] == 'settings' else set()
    if status == 'applying' and now() - datetime.fromisoformat(created) > PLAN_LIFE:
        status, result = 'unknown', '실행 중에 끊겼습니다. 화면과 기록, STOP 파일을 확인하세요.'
    if status == 'open' and now() - datetime.fromisoformat(created) > life(body):
        status = 'expired'
    return {'plan': pid, 'status': status, 'level': body['level'], 'affects': body['affects'], 'reason': body['reason'],
            **({'phrase': body['phrase']} if body.get('phrase') else {}), **({'how': body['reach']} if body.get('reach') else {}), **({'title': body['title']} if body.get('title') else {}), 'created': created,
            'expires': (datetime.fromisoformat(created) + life(body)).isoformat(timespec='seconds'),
            'changes': [{'setting': a, 'before': b, 'after': c, **({} if a in asked or not asked else {'linked': True})} for a, b, c in body['changed']],
            **({'result': result} if result else {})}


def close(db, pid, status):
    db.execute('UPDATE plans SET status=? WHERE id=?', (status, pid))
    db.execute('UPDATE codes SET used=1 WHERE plan=?', (pid,))


def drop(nh, pid):
    """Throw an open plan away (nothing is applied, so no code is needed)."""
    with store(nh) as db:
        if (status := get(db, pid)[2]) != 'open':
            raise Refused(f'이미 처리된 계획입니다({status}).')
        close(db, pid, 'dropped')
    return {'plan': pid, 'status': 'dropped'}


def get(db, pid):
    row = db.execute('SELECT created, body, status, result FROM plans WHERE id=?', (pid,)).fetchone()
    if not row:
        raise Refused(f'계획 {pid}을 찾지 못했습니다.')
    return row[0], json.loads(row[1]), row[2], row[3]


def show(nh, pid):
    with store(nh) as db:
        return view(pid, *get(db, pid))


def plans(nh, limit=20):
    with store(nh) as db:
        return [view(pid, created, json.loads(body), status, result) for pid, created, body, status, result in
                db.execute('SELECT id, created, body, status, result FROM plans ORDER BY created DESC LIMIT ?', (limit,))]


def life(body):
    return timedelta(minutes=body['minutes']) if body and body.get('minutes') else PLAN_LIFE


def usable(created, status, body=None):
    if status != 'open':
        raise Refused(f'이미 처리된 계획입니다({status}). 다시 계획하세요.')
    if now() - datetime.fromisoformat(created) > life(body):
        raise Refused(f'계획이 {int(life(body).total_seconds() // 60)}분을 넘어 만료됐습니다. 다시 계획하세요.')


def slow(code, salt):
    return hashlib.scrypt(code.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)


def notify(text):
    """Send to every real-time destination in .env; how many took it."""
    import discord_notify as N
    return sum(N.send_everywhere(N.urls_from_env().get('realtime'), text).values())


def approve(nh, pid, sender=None):
    """Send the plan and a one-time code to the notification channel. Nothing is applied."""
    sender = notify if sender is None else sender
    with store(nh) as db:
        created, body, status, _ = get(db, pid)
        usable(created, status, body)
        if body['level'] != 'H':
            raise Refused('승인이 필요 없는 계획입니다. `omni plan apply`로 적용하세요.')
        if db.execute('SELECT count(*) FROM codes WHERE plan=?', (pid,)).fetchone()[0] >= SENDS:
            raise Refused(f'코드는 계획마다 {SENDS}번까지 보냅니다. 다시 계획하세요.')
        since = (now() - timedelta(hours=1)).isoformat(timespec='seconds')
        if db.execute('SELECT count(*) FROM codes WHERE created>=?', (since,)).fetchone()[0] >= HOURLY:
            raise Refused(f'승인 요청은 한 시간에 {HOURLY}번까지입니다.')
        db.execute('UPDATE codes SET used=1 WHERE plan=?', (pid,))  # only the newest code works
        code = ''.join(secrets.choice(ALPHABET) for _ in range(8))
        salt = secrets.token_bytes(16)
        db.execute('INSERT INTO codes (plan, created, salt, hash) VALUES (?,?,?,?)', (pid, now().isoformat(timespec='seconds'), salt, slow(code, salt)))
    show_value = lambda v: '없음' if v is None else json.dumps(v, ensure_ascii=False)  # noqa: E731
    head = '⚠️ 실제 돈이 움직일 수 있습니다 · ' if body.get('risk') else ''
    lines = [f"{head}🔐 Omni {body.get('title') if body['type'] == 'action' else '설정 변경'} 승인 요청 · 계획 {pid}", f"이유: {body['reason']}", f"영향: {', '.join(body['affects'])}"]
    lines += [f'- {a}: {show_value(b)} → {show_value(c)}' for a, b, c in body['changed'][:25]]
    if len(body['changed']) > 25:
        lines.append(f"- 외 {len(body['changed']) - 25}개")
    if body.get('phrase'):
        lines.append(f"확인 문구도 필요합니다: {body['phrase']}")
    lines += [f'승인하려면 이 코드를 알려 주세요: {code}', '10분 동안 한 번만 쓸 수 있습니다. 요청하지 않았다면 무시하세요.']
    try:
        sent = sender('\n'.join(lines))
    except Exception:  # noqa: BLE001 - never show an address
        sent = 0
    if not sent:
        with store(nh) as db:
            db.execute('UPDATE codes SET used=1 WHERE plan=?', (pid,))
        raise Refused('알림 채널로 보내지 못했습니다. 실계좌에 영향을 주는 변경은 대시보드에서 하세요.', code=6)
    return {'plan': pid, 'sent': sent, 'expires_minutes': int(CODE_LIFE.total_seconds() // 60), 'next': f'omni plan apply {pid} --code <코드>'}


def check_code(nh, pid, code):
    """Accept the newest unused code once; every wrong try counts (kept even when the apply fails afterwards)."""
    need = OmniError('실계좌에 영향이 있어 사용자의 승인 코드가 필요합니다.', 3, {'plan': pid}, [f'omni plan approve {pid}'])
    if not code:
        raise need
    with store(nh) as db:
        row = db.execute('SELECT rowid, created, salt, hash, tries FROM codes WHERE plan=? AND used=0 ORDER BY rowid DESC LIMIT 1', (pid,)).fetchone()
        ok = bool(row) and now() - datetime.fromisoformat(row[1]) <= CODE_LIFE and row[4] < TRIES
        good = ok and hmac.compare_digest(slow(code.strip().upper(), row[2]), row[3])
        if row:
            db.execute('UPDATE codes SET tries=tries+1, used=? WHERE rowid=?', (1 if good or not ok or row[4] + 1 >= TRIES else 0, row[0]))
    if not good:
        raise OmniError('승인 코드가 맞지 않거나 만료됐습니다.', 3, {'plan': pid}, [f'omni plan approve {pid}'])


def in_market(at):
    import session_times
    clock, shift = at.strftime('%H:%M'), session_times.shift_min(at.date())
    start, end = ((datetime.strptime(t, '%H:%M') + timedelta(minutes=shift)).strftime('%H:%M') for t in MARKET)
    return at.weekday() < 5 and start <= clock < end


def ready(nh, body):
    """The plan again on today's files: refused when they changed; its level as it is now."""
    group = S.ALL[body['group']]
    if S.stamp(group, nh) != body['stamp']:
        raise Refused('계획을 만든 뒤 설정 파일이 바뀌었습니다. 다시 계획하세요.')
    changed = S.rehearse(group, nh, body['input'])
    if json.loads(json.dumps(changed)) != body['changed']:
        raise Refused('계획을 만든 뒤 결과가 달라졌습니다. 다시 계획하세요.')
    return group, S.level(nh, changed)


def apply(nh, pid, code=None, agent='cli', during_market=False, phrase=None, sender=None):
    with store(nh) as db:
        created, body, status, _ = get(db, pid)
    usable(created, status, body)
    if body['type'] != 'settings':
        return act(nh, pid, body, code, phrase, sender)
    if body['group'] not in ('prefs',) and in_market(now()) and not during_market:
        raise Refused('장중(08:55~15:20)에는 진행 중인 판단에 바로 반영됩니다. 그래도 적용하려면 --during-market을 붙이세요.', data={'plan': pid})
    _, (level, affects) = ready(nh, body)
    if level == 'H':
        if body['level'] != 'H':
            raise Refused(f'계획을 만든 뒤 실계좌에 영향을 주는 변경이 됐습니다({", ".join(affects)}). 다시 계획하세요.')
        check_code(nh, pid, code)
    name = agent if agent != 'cli' else body.get('agent', 'cli')  # the apply call may leave --agent out
    who = (f'cli:approved {pid} ' if level == 'H' else f'cli:{name} ') + body['reason']
    with store(nh) as db:
        created, body, status, _ = get(db, pid)
        usable(created, status)
        group, (again, _) = ready(nh, body)
        if again == 'H' and level != 'H':
            raise Refused('적용 직전에 실계좌에 영향을 주는 변경이 됐습니다. 다시 계획하세요.')
        before = S.snapshot(group, nh)
        error = None
        try:
            group.write(nh, body['input'], who[:200])
        except Exception as exc:  # noqa: BLE001 - what happened is read back below
            error = type(exc).__name__
        after = S.diff(before, S.snapshot(group, nh))
        done = json.loads(json.dumps(after)) == body['changed']
        result = 'applied' if done else 'partial' if after else 'failed'
        db.execute('UPDATE plans SET status=?, result=? WHERE id=?', (result, error, pid))
    if not done:
        raise OmniError(f'저장 결과가 계획과 다릅니다({result}{", " + error if error else ""}). 화면과 이력에서 값을 확인하세요.', 1, {'plan': pid, 'now': after})
    effects = sorted({group.specs(nh)[k].effect for k in body['input'] if k in group.specs(nh)} - {''})
    from .reads import rows
    ids = [r['id'] for r in rows(nh / 'settings-history.sqlite3', 'SELECT rowid AS id FROM changes WHERE who=? ORDER BY rowid DESC LIMIT ?', (who[:200], len(after)))]
    return {'plan': pid, 'status': 'applied', 'who': who[:200], 'history': ids, 'changes': [{'setting': a, 'before': b, 'after': c} for a, b, c in after], 'takes_effect': effects}


# --- actions (core/actions.py): each is approved like an H setting ------------------------------------------------------------

def plan_action(nh, action, reason, **args):
    from .actions import ACTIONS, risky
    reason = one_line(reason)
    if not reason:
        raise Refused('이유를 --reason으로 적으세요.')
    found = ACTIONS[action]
    changed, affects = found.prepare(nh, args)
    reach = S.reach(nh, affects)
    return keep(nh, {'type': 'action', 'action': action, 'args': args, 'reason': reason, 'title': found.title, 'level': 'H', 'affects': affects,
                     'changed': changed, 'reach': reach, 'minutes': found.minutes,
                     'phrase': found.phrase(nh, args) if found.phrase else None, 'risk': risky(action, args) or bool(reach) and action.endswith(('_sell', '_buy', '_cancel'))})


def act(nh, pid, body, code, phrase=None, sender=None):
    """Look again, take the phrase and the code, then do it once: the plan is marked before it runs, so a crash never runs
    it twice. What can expose real money is reported on the channel either way."""
    from .actions import ACTIONS
    found = ACTIONS[body['action']]
    changed, affects = found.prepare(nh, body['args'])
    if json.loads(json.dumps(changed)) != body['changed']:
        raise Refused('계획을 만든 뒤 상황이 바뀌었습니다. 다시 계획하세요.')
    if set(affects) - set(body['affects']):
        raise Refused(f'계획을 만든 뒤 실계좌에 영향을 주는 요청이 됐습니다({", ".join(affects)}). 다시 계획하세요.')
    if body.get('phrase') and (phrase or '').strip() != body['phrase']:  # before the code, so a missing phrase does not spend it
        raise OmniError(f'확인 문구가 필요합니다: "{body["phrase"]}"', 3, {'plan': pid}, [f'omni plan apply {pid} --code <코드> --phrase "{body["phrase"]}"'])
    check_code(nh, pid, code)
    with store(nh) as db:
        created, _, status, _ = get(db, pid)
        usable(created, status, body)
        db.execute("UPDATE plans SET status='applying' WHERE id=?", (pid,))
    status, error, out = 'failed', None, {}
    main = threading.current_thread() is threading.main_thread()
    before = signal.signal(signal.SIGTERM, lambda *_: sys.exit(143)) if main else None  # so a stopped call still cleans up (restart_runner removes STOP)
    try:
        out = found.run(nh, {**body['args'], 'who': f"cli:approved {pid} {body['reason']}"[:200]})
        status = 'applied'
    except OmniError as exc:
        error = exc
    except Exception as exc:  # noqa: BLE001 - the type only: a message may hold an account
        error = OmniError(f'실행 결과를 확인하지 못했습니다({type(exc).__name__}). 화면과 기록에서 확인하세요.', 1, {'plan': pid})
    finally:
        if main:
            signal.signal(signal.SIGTERM, before)
        with store(nh) as db:
            db.execute('UPDATE plans SET status=?, result=? WHERE id=?', (status, type(error).__name__ if error else None, pid))
        told = tell(body, pid, status, sender) if body.get('risk') else True
    if error:
        raise error
    return {'plan': pid, 'status': status, 'done': found.title, 'affects': body['affects'], **out, **({} if told else {'warning': '결과 알림을 보내지 못했습니다.'})}


def tell(body, pid, status, sender=None):
    words = {'applied': '✅ 적용됨', 'failed': '❌ 실패(확인 필요)'}[status]
    try:
        return bool((notify if sender is None else sender)(f"{words} · {body['title']} · 계획 {pid}\n영향: {', '.join(body['affects'])}\n이유: {body['reason']}"))
    except Exception:  # noqa: BLE001 - the action stands
        return False
