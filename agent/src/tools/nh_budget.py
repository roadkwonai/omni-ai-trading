"""Shared NH call budget across processes (2026-11-20, docs/nh-call-reduction-plan.md 3-1, P1).

One NH app key serves every program (paper runner, live mirror, ranking service, dashboard, observers), and NH refuses
an app key past five calls a second (IGW42902). Each process used to call on its own clock, so the five-minute marks
(ranking scan, balance snapshots, long-term engine) collided: 11-20 09:40~09:50 had 23 refusals.

`take()` runs before every NH MCP call (mcp.MCPServerAdapter._call_tool). Under one file lock it keeps the start times
of the last second: a trading process may start a call while fewer than RATE started; any other program only while
fewer than RATE - RESERVE did, so a burst from the ranking service or the dashboard never takes the runner's slot.
A caller past its share waits for a slot, up to its cap, and then goes anyway (NH's own refusal and the callers'
retries stay as they were). NH also answers one account query (operation and account) only about once a second
(IGW42903), so those are started SAME_GAP apart across processes (2026-11-24). Orders never wait. A broken or missing budget file lets the call through at once.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

from src import file_lock

RATE = 4      # calls a second across all processes; NH refuses an app key past five
RESERVE = 2   # of those, kept for the trading processes
WINDOW = 1.0
_wait_deadline = {'high': 1.0, 'low': 2.0}  # longest wait for a slot before the call goes anyway
HIGH = frozenset({'nh_paper_isolated', 'nh_live_follow', 'longterm_live', 'nh_guarded_live', 'nh_live_auto',
                  'nh_live_position_watch', 'restart_runner'})
TRIES = 50
SAME_GAP = 1.5  # NH needs about a second between arrivals (2026-11-19: 0.5 s refused, 0.7-1.0 s answered); at 1.1 s between
# starts a first call that reached NH late was still followed too soon (2026-11-25: 5 refusals in 90 minutes)
SAME_FILE = 'nh-budget-same.json'  # its own file: programs still running older code read nh-budget.json as a list
SAME_QUERIES = ('krstockInquiry',)
_same_deadline = {'high': 1.6, 'low': 4.0}  # past it the call goes at once; a trading program does not queue behind a screen
ORDERS = ('krstockOrder',)  # operationId prefix of order, modify and cancel calls


def program() -> str:
    name = Path(sys.argv[0] if sys.argv and sys.argv[0] else 'python').stem
    return 'dashboard' if name in ('streamlit', 'app') else name or 'python'


def high_priority(name: str) -> bool:
    return name in HIGH


def _default_path() -> Path:
    from src.config.paths import get_runtime_root
    return get_runtime_root() / 'nhplug' / 'nh-budget.json'


def _locked(path: Path, update):
    """update(stamps) -> stamps to keep, under an exclusive lock on the budget file itself."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, 'r+', encoding='utf-8') as stream:
        file_lock.lock(stream)
        text = stream.read()
        stamps = json.loads(text) if text.strip() else []
        if not isinstance(stamps, list):
            raise ValueError('budget file shape')
        kept = update([float(t) for t in stamps])
        stream.seek(0)
        stream.truncate()
        stream.write(json.dumps(kept))
        return kept


def _wait_same(operation, arguments, tier, path, clock, sleep, diagnostic=None) -> float:
    """Seconds waited so that this account query starts SAME_GAP after the last one of any program. The slot is reserved
    under the lock, so two waiting programs get different ones. The key is a hash: no account number is written.
    `tier` None: only note that the query starts now (take, after its wait for a slot)."""
    try:
        account = str((arguments.get('input') or {}).get('act_no'))
        key = hashlib.sha256(f'{operation}:{account}'.encode()).hexdigest()[:16]
        fd = os.open(path.with_name(SAME_FILE), os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'r+', encoding='utf-8') as stream:
            file_lock.lock(stream)
            now = clock()
            text = stream.read()
            starts = {k: float(v) for k, v in (json.loads(text) if text.strip() else {}).items() if abs(now - float(v)) < 10}
            wait = max(0.0, starts.get(key, 0.0) + SAME_GAP - now) if tier else 0.0
            if diagnostic is not None and tier:
                diagnostic.update(previous_reservation=starts.get(key), requested_wait=wait,
                                  same_cap_exceeded=wait > _same_deadline[tier])
            if tier is None:
                starts[key] = max(starts.get(key, 0.0), now)
            elif wait > _same_deadline[tier]:
                wait = 0.0
            else:
                starts[key] = now + wait
            stream.seek(0)
            stream.truncate()
            stream.write(json.dumps(starts))
        if wait:
            sleep(wait)
        return wait
    except Exception:  # noqa: BLE001 - spacing is a courtesy; it must never stop a call
        return 0.0


def take(tool: str, arguments, *, caller: str | None = None, clock=time.time, sleep=time.sleep, path=None, diagnostic=None) -> float:
    """Wait for a slot in the shared budget, then count this call; returns the seconds waited. Never raises."""
    if path is None and 'pytest' in sys.modules:
        return 0.0  # tests never touch the live budget (as api_calls)
    try:
        name = (arguments or {}).get('operationId') if isinstance(arguments, dict) else None
        order = str(name or tool).startswith(ORDERS)
        tier = 'high' if order or high_priority(caller or program()) else 'low'
        limit = RATE if tier == 'high' else RATE - RESERVE
        path = Path(path) if path is not None else _default_path()
        spaced = tool == 'call_api' and str(name).startswith(SAME_QUERIES)
        if diagnostic is not None and spaced:
            diagnostic.update(operation=name, tier=tier, requested_at=clock())
        same = _wait_same(name, arguments, tier, path, clock, sleep, diagnostic) if spaced else 0.0
        started = clock()

        def went(now):
            # a slot waited for after the reservation made the real call later: the next same query counts from here
            # (2026-11-25: it came less than a second behind and NH refused it, IGW42903)
            if spaced and now > started:
                _wait_same(name, arguments, None, path, clock, sleep)
        deadline = started + _wait_deadline[tier]
        for attempt in range(TRIES):  # bounded: a clock that never moves cannot hold a call
            now, pause, last = clock(), [None], attempt == TRIES - 1

            def update(stamps):
                recent = sorted(t for t in stamps if 0 <= now - t < WINDOW)
                if order or last or len(recent) < limit or now >= deadline:
                    return recent + [now]
                pause[0] = recent[len(recent) - limit] + WINDOW - now  # until enough of them age out
                return recent
            _locked(path, update)
            if pause[0] is None:
                went(now)
                if diagnostic is not None and spaced:
                    diagnostic.update(admitted_at=now, same_wait=same, budget_wait=max(0.0, now - started),
                                      budget_cap_exceeded=now >= deadline or last)
                return round(same + max(0.0, now - started), 3)
            sleep(min(max(pause[0], 0.01), max(0.0, deadline - now)))
        return round(same + max(0.0, clock() - started), 3)  # unreachable: the last try always counts the call
    except Exception:  # noqa: BLE001 - the budget must never stop a call
        return 0.0
