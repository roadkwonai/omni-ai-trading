"""Shared NH call budget across processes (2026-11-20, docs/nh-call-reduction-plan.md 3-1, P1).

One NH app key serves every program (paper runner, live mirror, ranking service, dashboard, observers), and NH refuses
an app key past five calls a second (IGW42902). Each process used to call on its own clock, so the five-minute marks
(ranking scan, balance snapshots, long-term engine) collided: 11-20 09:40~09:50 had 23 refusals.

`take()` runs before every NH MCP call (mcp.MCPServerAdapter._call_tool). Under one file lock it keeps the start times
of the last second: a trading process may start a call while fewer than RATE started; any other program only while
fewer than RATE - RESERVE did, so a burst from the ranking service or the dashboard never takes the runner's slot.
A caller past its share waits for a slot, up to its cap, and then goes anyway (NH's own refusal and the callers'
retries stay as they were). Orders never wait. A broken or missing budget file lets the call through at once.
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from pathlib import Path

RATE = 4      # calls a second across all processes; NH refuses an app key past five
RESERVE = 2   # of those, kept for the trading processes
WINDOW = 1.0
_wait_deadline = {'high': 1.0, 'low': 2.0}  # longest wait for a slot before the call goes anyway
HIGH = frozenset({'nh_paper_isolated', 'nh_live_follow', 'longterm_live', 'nh_guarded_live', 'nh_live_auto',
                  'nh_live_position_watch', 'restart_runner'})
TRIES = 50
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
    with os.fdopen(fd, 'r+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        text = stream.read()
        stamps = json.loads(text) if text.strip() else []
        if not isinstance(stamps, list):
            raise ValueError('budget file shape')
        kept = update([float(t) for t in stamps])
        stream.seek(0)
        stream.truncate()
        stream.write(json.dumps(kept))
        return kept


def take(tool: str, arguments, *, caller: str | None = None, clock=time.time, sleep=time.sleep, path=None) -> float:
    """Wait for a slot in the shared budget, then count this call; returns the seconds waited. Never raises."""
    if path is None and 'pytest' in sys.modules:
        return 0.0  # tests never touch the live budget (as api_calls)
    try:
        name = (arguments or {}).get('operationId') if isinstance(arguments, dict) else None
        order = str(name or tool).startswith(ORDERS)
        tier = 'high' if order or high_priority(caller or program()) else 'low'
        limit = RATE if tier == 'high' else RATE - RESERVE
        path = Path(path) if path is not None else _default_path()
        started = clock()
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
                return round(max(0.0, now - started), 3)
            sleep(min(max(pause[0], 0.01), max(0.0, deadline - now)))
        return round(max(0.0, clock() - started), 3)  # unreachable: the last try always counts the call
    except Exception:  # noqa: BLE001 - the budget must never stop a call
        return 0.0
