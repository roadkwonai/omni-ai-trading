"""Read-only answers from the records the engines keep. No broker call unless a command says so."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .output import KST, Answer, Refused, age_text, source


def today():
    return datetime.now(KST).strftime('%Y-%m-%d')


def read_json(path, missing=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return missing
    except (OSError, ValueError):
        raise Refused(f'{Path(path).name}을 읽을 수 없습니다.') from None


def need(path):
    if not Path(path).exists():
        raise Refused(f'{Path(path).name}이 없습니다.', code=5)


STAMP = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d[\d:.]*(Z|[+-]\d\d:\d\d)')


def seoul(value):
    """The same data with every stored time of another zone in Seoul time."""
    if isinstance(value, dict):
        return {k: seoul(v) for k, v in value.items()}
    if isinstance(value, list):
        return [seoul(v) for v in value]
    if isinstance(value, str) and STAMP.fullmatch(value) and not value.endswith('+09:00'):
        try:
            return datetime.fromisoformat(value).astimezone(KST).isoformat(timespec='seconds')
        except ValueError:
            return value
    return value


def file_answer(path, pick=None, stale_s=None):
    path = Path(path)
    data = read_json(path)
    if data is None:
        raise Refused(f'{path.name}이 없습니다.', code=5)
    if pick and not any(k in data for k in pick):
        raise Refused(f'해당 항목이 없습니다. 쓸 수 있는 값: {", ".join(sorted(data)[:40])}')
    src = source(path)
    warn = [f"자료가 {age_text(src['age_s'])} 것입니다."] if stale_s and src.get('age_s', 0) > stale_s else []
    return Answer(seoul({k: data[k] for k in pick if k in data} if pick else data), [src], warn)


def tail(path, limit=20, day=None, match=None):
    """The newest matching rows of a JSON-lines record, read from its end (the files grow to hundreds of MB)."""
    path, rows, rest = Path(path), [], b''
    if not path.exists():
        return []
    with path.open('rb') as f:
        position = f.seek(0, 2)
        while position and len(rows) < limit:
            size = min(1 << 16, position)
            position -= size
            f.seek(position)
            parts = (f.read(size) + rest).split(b'\n')
            rest = parts.pop(0) if position else b''
            for line in reversed(parts):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                stamp = str(row.get('time', ''))[:10]
                if day and stamp and stamp < day:
                    return rows
                if (not day or stamp == day) and (match is None or match(row)):
                    rows.append(row)
                    if len(rows) >= limit:
                        break
    return rows


def rows(path, sql, args=()):
    import sqlite3
    from sqlite_ro import ro_connect
    if not Path(path).exists():
        return []
    with ro_connect(path) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(sql, args)]


def records(nh):
    import accounts
    return accounts.load(nh / 'accounts.json')


def status(nh):
    import engine_ops
    from automation_status import processes
    from nh_live_buy_control import buy_allowed
    from src.live.halt import halt_flag_set
    import overview
    accounts = records(nh)
    halts = overview.loss_halts(nh, accounts, today())
    live = {k: {'engine': r['engine'], 'auto': r['auto'], 'live_orders': r.get('live_orders') is True, 'loss_halt_today': '확인 불가' if halts[k] is None else halts[k]}
            for k, r in accounts.items() if r['kind'] == 'live'}
    warn = [f"{accounts[k].get('label', k)}: 하루 손실 정지{'로 오늘 새 매수가 없습니다.' if on else ' 여부를 확인할 수 없습니다.'}" for k, on in halts.items() if on is not False]
    return Answer({'engines': engine_ops.status(nh), 'live_accounts': live, 'halt': halt_flag_set('nhplug'), 'buy_paused': not buy_allowed(nh),
                   'stop_files': [name for name in ('shared-etf-paper/STOP', 'vibe-longterm/STOP') if (nh / name).exists()], 'services': dict(processes())}, warnings=warn)


def items(nh):
    import overview
    return overview, overview.accounts(nh, records(nh), datetime.now(KST))


def alerts(nh):
    import overview
    from automation_status import processes
    return overview.alerts(nh, dict(processes()), overview.latest_follow(nh), today(), datetime.now(KST))


def balance(nh):
    overview, found = items(nh)
    return overview.balance_overview(found, nh, today(), overview.principals_of(records(nh)))


def realized(nh):
    overview, found = items(nh)
    return overview.realized_overview(found, datetime.now(KST))


def trades(nh, day):
    _, found = items(nh)
    keep = ('time', 'side', 'symbol', 'qty', 'price', 'pnl', 'route', 'reason', 'source')
    return [{'account': i['label'], **{k: r.get(k) for k in keep if r.get(k) is not None}} for i in found for r in i['rows'] if str(r['time'])[:10] == day]


def names(nh):
    """Display names by code, the same map the screen uses."""
    import stock_names
    return stock_names.load(nh)


def timeline(nh, day, limit):
    import overview
    from entry_reasons import ENTRY
    found, waits = overview.timeline(nh, day, names(nh), lambda r: ENTRY.get(r, r or '-'))
    return {'rows': found[-limit:], 'waits': waits}


def positions(nh):
    state = file_answer(nh / 'shared-etf-paper/account-state.json', stale_s=600)
    plans = read_json(nh / 'shared-etf-paper/exit-plans.json', {})
    return Answer({**state.data, 'exit_plans': plans}, [*state.sources, source(nh / 'shared-etf-paper/exit-plans.json')], state.warnings)


def requests(nh):
    out = {name: read_json(nh / 'shared-etf-paper' / f'{name}.json', {}) for name in ('exit-requests', 'manual-buys')}
    return out | {p.stem: read_json(p, {}) for pattern in ('live-exit-requests-*.json', 'live-manual-buys-*.json') for p in sorted(nh.glob(pattern))}


def longterm_keys(nh):
    import accounts
    return accounts.longterm_accounts(records(nh))


def longterm_today(nh):
    import vibe_longterm as L
    saved, state = L.load_settings(nh / 'vibe-longterm'), L.read_state(nh / 'vibe-longterm', ('plan', 'regime'))
    last = rows(nh / 'vibe-longterm/longterm.sqlite3', 'SELECT time, day, kind, regime, mode, reason FROM decisions ORDER BY rowid DESC LIMIT 8')
    plan = state.get('plan') or {}
    keep = ('day', 'regime', 'active', 'mode', 'recommended', 'weights')
    books = {b: L.book_strategy_label(saved, b) for b in sorted({'actual', *saved['books'], *longterm_keys(nh)})}
    warn = [f'{b}은 {label.split(" (")[0]}으로 운용합니다. 아래 모듈·목표 비중은 그 장부에 쓰이지 않습니다.' for b, label in books.items() if label.startswith('여운봉')]
    return Answer({'mode': saved['mode'], 'books': books, 'execute': saved['execute'], 'enabled': saved['enabled'], 'regime': state.get('regime'),
                   'plan': {k: plan[k] for k in keep if k in plan}, 'recent': last}, [source(nh / 'vibe-longterm/longterm.sqlite3')], warn)


def longterm_levels(nh):
    import vibe_longterm as L
    plan = (L.read_state(nh / 'vibe-longterm', ('plan',)).get('plan') or {})
    return {'day': plan.get('day'), 'levels': plan.get('levels') or []}


def documents(nh):
    import report_pages
    return [{'kind': kind, 'key': key, 'file': Path(path).name} for kind, key, path, *_ in report_pages.sources(nh.parent)]


def api_load(nh):
    import api_load as A
    facts = A.collect(nh, datetime.now(KST))
    return {'findings': A.findings(facts), 'providers': A.table(facts), 'blocked': A.blocked(facts)}


def quote(code):
    from ..utils.omni_backend import readonly
    got = readonly().call_tool('get_stock_price', {'stock_code': code})
    if got.get('status') != 'ok':
        raise Refused('NH 시세를 받지 못했습니다.', code=6)
    data = got.get('data') or {}
    return data.get('Output_0', data) if isinstance(data, dict) else data
