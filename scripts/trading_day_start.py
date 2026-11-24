"""Start the shared paper day at 08:55 (launchd job com.vibetrading.trading-day-start): the runners start before the bell,
connect and check, then wait for 09:00 themselves (session_times.until_open; user 2026-11-20, "장전 준비 다 해 달라").

Checks, then starts only what is needed: a KRX trading day (weekend and the KRX holiday table; an unreadable table
is logged and the start goes ahead, because the runner itself cannot trade without live quotes), no STOP file, and
no runner already running. Unfinished order intents are reported but do not block: the runner's first cycle
reconciles them against the broker and sends nothing new until they are resolved. Every run appends one line to
trading-day-start.jsonl. Never orders, never deletes STOP or records.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import os_services
from sqlite_ro import ro_connect
from src.config.paths import get_runtime_root  # VIBE_TRADING_HOME, else ~/.vibe-trading (one rule for every tool)

KST = ZoneInfo('Asia/Seoul')
ROOT = get_runtime_root() / 'nhplug' / 'shared-etf-paper'
SERVICES = ('shared-etf-paper', 'shared-etf-learning', 'shared-etf-regime')


def trading_day(now):
    """(is_trading_day, note) for today in Asia/Seoul."""
    if now.weekday() >= 5:
        return False, 'weekend'
    try:
        from kr_swing_v5_daily_data import krx_holidays
        if now.date() in krx_holidays({now.year}):
            return False, 'krx_holiday'
    except Exception as exc:  # the runner still needs live quotes to act, so an unknown table does not block
        return True, f'holiday_table_unavailable:{type(exc).__name__}'
    return True, 'trading_day'


def unfinished_intents(root=ROOT):
    try:
        with ro_connect(Path(root) / 'orders.sqlite3') as db:
            return db.execute("SELECT COUNT(*) FROM intents WHERE status != 'terminal'").fetchone()[0]
    except sqlite3.Error:
        return None


def running(name):
    """True, False, or None when it is not registered; Unknown when the service manager cannot be asked."""
    found = os_services.state(name)
    return found['running'] if found['registered'] else None


UNKNOWN = 'unknown'
ASK_TRIES, ASK_WAIT = 3, 5


def asked(name, wait=time.sleep):
    """running(), asked again a few times before giving up: one slow launchctl must not cost the day."""
    for left in range(ASK_TRIES, 0, -1):
        try:
            return running(name)
        except os_services.Unknown:
            if left > 1:
                wait(ASK_WAIT)
    return UNKNOWN


def alive(name):
    try:
        return running(name) is True
    except os_services.Unknown:
        return False


def decide(day_ok, stop_exists, runner_running):
    """The start decision alone, so it can be tested without launchd."""
    if not day_ok:
        return 'skip_closed'
    if stop_exists:
        return 'skip_stop_file'
    if runner_running:
        return 'already_running'
    return 'start'


START_ATTEMPTS = 5  # a runner that stops right after start (a transient NH read failure) is started again
START_CHECK_SECONDS = 30


def start_services(states, *, kick, alive, wait=time.sleep):
    """Start every registered service that is not running and check each is still up START_CHECK_SECONDS later,
    starting it again up to START_ATTEMPTS times in all. Returns {label: starts}."""
    started = {}
    for label, state in states.items():
        if state is not False:  # not registered, or already running
            continue
        for attempt in range(1, START_ATTEMPTS + 1):
            kick(label)
            started[label] = attempt
            wait(START_CHECK_SECONDS)
            if alive(label):
                break
    return started


def main():
    now = datetime.now(KST)
    day_ok, note = trading_day(now)
    states = {name: asked(name) for name in SERVICES}
    runner = states[SERVICES[0]]
    if runner is UNKNOWN:  # whether the runner runs is not known: start nothing
        decision = 'services_unknown'
    else:  # a helper nobody can answer for is left alone, as an unregistered one is
        decision = decide(day_ok, (ROOT / 'STOP').exists(), runner)
    started = {}
    if decision == 'start':
        started = start_services(states, alive=alive, kick=os_services.start)
    running_after = {name: alive(name) for name in started}
    try:
        context = json.loads((ROOT / 'market-context.json').read_text(encoding='utf-8')).get('time')
    except (OSError, ValueError):
        context = None
    record = {'time': now.isoformat(timespec='seconds'), 'decision': decision, 'note': note, 'started': list(started),
              'starts': started, 'running_after': running_after,
              'unfinished_intents': unfinished_intents(), 'market_context_time': context}
    with (ROOT / 'trading-day-start.jsonl').open('a', encoding='utf-8') as log:
        log.write(json.dumps(record, ensure_ascii=False) + '\n')
    print(json.dumps(record, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
