"""Restart a paper runner during the session by the documented procedure, in one step.

    .venv/bin/python scripts/restart_runner.py --runner shared --reason "load the 11/17 ledger fix"

Steps (.claude/skills/vibe-trading/SKILL.md): no unconfirmed order -> STOP -> wait for the runner's own `stopped` event
and the process to exit -> delete STOP -> launchctl kickstart -> wait for its first cycle. It refuses when an order is
unconfirmed or a holding sits within --stop-margin %p of its stop (the stop watch pauses for the restart; pass --force
to accept that). Every attempt is appended to <runner folder>/restarts.jsonl with its reason and downtime, so the day's
restarts can be counted (11/17 had seven, each pausing the stop watch). Never places or cancels an order.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
RUNNERS = {'shared': ('com.vibetrading.shared-etf-paper', 'shared-etf-paper')}
FIRST_CYCLE = ('market_context', 'v2_entry_gate', 'jev_shadow', 'jev_holding_decision', 'v2_risk_exit')


def unconfirmed_orders(folder):
    """Orders the runner has not settled: paper intents not terminal, or virtual orders neither filled nor cancelled."""
    folder, count = Path(folder), 0
    for name, sql in (('orders.sqlite3', "SELECT count(*) FROM intents WHERE status!='terminal'"),
                      ('virtual-account.sqlite3', 'SELECT count(*) FROM orders WHERE filled<qty AND cancelled=0')):
        if (folder / name).exists():
            with sqlite3.connect(f'file:{folder / name}?mode=ro', uri=True) as db:
                try:
                    count += db.execute(sql).fetchone()[0]
                except sqlite3.OperationalError:
                    pass  # this ledger has no such table (e.g. a virtual runner's orders.sqlite3)
    return count


def near_stops(folder, margin):
    """Holdings within `margin` %p of the stop the runner applies now (account-state.json now/avg; engine_exits: the
    saved plan or the settings' defensive one, tightened by the index regime the way the runner does)."""
    import engine_exits
    from nh_paper_isolated import market_view, stop_symbol_index
    from strategy_settings import read_settings
    folder = Path(folder)
    try:
        holdings = json.loads((folder / 'account-state.json').read_text()).get('holdings') or {}
    except (OSError, ValueError, AttributeError):
        return {'unknown': 'account-state.json unreadable'}
    settings, view, near = read_settings(folder)[0], market_view(folder), {}
    for code, row in holdings.items():
        try:
            pnl = (float(row['now_pr']) / float(row['phs_pr']) - 1) * 100
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            near[code] = 'price unknown'
            continue
        plan = engine_exits.plan_for(folder, code, settings)[0]  # RUNNERS are nh_paper_isolated folders: never a vibe special
        stop = float(1 - engine_exits.levels(plan, view, stop_symbol_index(folder, code), code)['stop']) * 100
        if pnl <= -(stop - margin):
            near[code] = f'{pnl:+.2f}% (stop -{stop:g}%)'
    return near


def events_after(folder, since):
    try:
        lines = (Path(folder) / 'events.jsonl').read_text().splitlines()[-400:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if str(event.get('time', '')) > since:
            out.append(event)
    return out


def pid(label, run=subprocess.run):
    result = run(['launchctl', 'list'], capture_output=True, text=True, check=False)
    for line in result.stdout.splitlines():
        parts = line.split('\t')
        if len(parts) == 3 and parts[2] == label:
            return None if parts[0] == '-' else parts[0]
    return None


def restart(runner, reason, *, root, force=False, stop_margin=0.5, timeout=150, run=subprocess.run,
            sleep=time.sleep, clock=lambda: datetime.now(KST)):
    label, name = RUNNERS[runner]
    folder = Path(root) / name
    started = clock()
    record = {'time': started.isoformat(), 'runner': runner, 'reason': reason}

    def finish(**fields):
        record.update(fields, seconds=round((clock() - started).total_seconds(), 1))
        with (folder / 'restarts.jsonl').open('a') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        os.chmod(folder / 'restarts.jsonl', 0o600)
        return record

    if (folder / 'STOP').exists():
        return finish(result='refused', why='a STOP file is already there; someone else is stopping this runner')
    open_orders = unconfirmed_orders(folder)
    if open_orders:
        return finish(result='refused', why=f'{open_orders} unconfirmed order(s); reconcile first')
    near = near_stops(folder, stop_margin)
    record['near_stop'] = near
    if near and not force:
        return finish(result='refused', why='a holding is near its stop and the stop watch pauses during the restart')
    if pid(label, run) is None:
        return finish(result='refused', why='the runner is not running; start it through trading-day-start or kickstart')
    since = started.isoformat()
    (folder / 'STOP').touch()
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            stopped = [e for e in events_after(folder, since) if e.get('event') == 'stopped']
            if stopped and pid(label, run) is None:
                break
            sleep(2)
        else:
            return finish(result='failed', why='the runner did not stop in time; STOP removed, it keeps running')
    finally:
        (folder / 'STOP').unlink(missing_ok=True)
    record['stopped'] = {k: stopped[-1].get(k) for k in ('time', 'reason', 'remaining', 'reconciliation_required')}
    down = clock()
    run(['launchctl', 'kickstart', f'gui/{os.getuid()}/{label}'], check=False, capture_output=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        cycle = [e for e in events_after(folder, stopped[-1]['time']) if e.get('event') in FIRST_CYCLE]
        if cycle and pid(label, run):
            return finish(result='restarted', first_cycle=cycle[0]['time'],
                          watch_paused_seconds=round((datetime.fromisoformat(cycle[0]['time']) -
                                                      datetime.fromisoformat(stopped[-1]['time'])).total_seconds(), 1))
        sleep(2)
    return finish(result='failed', why='kickstarted but no first cycle yet; check the runner log', kicked=down.isoformat())


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--runner', choices=sorted(RUNNERS), default='shared')
    parser.add_argument('--reason', required=True, help='why (kept in restarts.jsonl)')
    parser.add_argument('--force', action='store_true', help='restart even with a holding near its stop')
    parser.add_argument('--stop-margin', type=float, default=0.5, help='%%p from the stop that counts as near (default 0.5)')
    args = parser.parse_args()
    from src.config.paths import get_runtime_root
    print(json.dumps(restart(args.runner, args.reason, root=get_runtime_root() / 'nhplug', force=args.force,
                             stop_margin=args.stop_margin), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
