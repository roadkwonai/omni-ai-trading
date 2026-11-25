"""The Korean NH services and their macOS launchd definitions, rendered for this checkout and this user's home.

    PYTHONPATH=agent:scripts .venv/bin/python scripts/launchd_services.py            # list services
    PYTHONPATH=agent:scripts .venv/bin/python scripts/launchd_services.py --out DIR  # write plists into DIR
    ... --out DIR --system windows | linux                                           # Task Scheduler XML, systemd units (os_services)

Writes files only; it never loads, starts or replaces a running service. To install one, copy its plist to
~/Library/LaunchAgents and `launchctl bootstrap gui/$(id -u) <plist>`. `shared-etf-paper` runs with `--execute`
(paper orders on the shared NH paper account): set up `.env`, the paper account and AGENTS.md rules first.
Times are Asia/Seoul wall-clock as seen by launchd (the Mac must be on KST). Weekday 1-5 = Mon-Fri.
"""
from __future__ import annotations

import argparse
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PATH = '/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'
WEEKDAYS = (1, 2, 3, 4, 5)

# label: (script and args, log folder under ~/.vibe-trading/nhplug, run at load, (hour, minute, weekdays), several of them, or None)
SERVICES = {
    'shared-etf-paper': (['scripts/nh_paper_isolated.py', '--run', '--shared-default', '--execute'], 'shared-etf-paper', True, None),
    'shared-etf-learning': (['scripts/isolated_paper_learning_watch.py', '--shared-default'], 'shared-etf-paper', True, None),
    'shared-etf-regime': (['scripts/etf_regime_observer.py', '--watch'], 'shared-etf-paper', True, None),
    'shared-etf-dashboard': (['streamlit_namuh/app.py'], 'shared-etf-paper', True, None),
    'market-movers': (['scripts/market_movers.py'], 'shared-etf-paper', False, (8, 50, WEEKDAYS)),
    'premarket': (['scripts/premarket.py', '--once'], 'shared-etf-paper', False,
                  tuple((hour, minute, WEEKDAYS) for hour, minute in [(7, 40), *[(8, m) for m in range(0, 60, 5)], (9, 0), (9, 10)])),  # NXT pre-market every 5 min (2026-11-19)
    'trading-day-start': (['scripts/trading_day_start.py'], 'shared-etf-paper', False, (8, 55, WEEKDAYS)),
    # 장전 사전 점검 (user 2026-11-18): read-only checks, failures to Discord; the 08:25 task 'trading-preflight' fixes them
    'preflight': (['scripts/preflight.py'], 'shared-etf-paper', False, ((8, 20, WEEKDAYS), (8, 53, WEEKDAYS))),
    # retired 2026-11-17 (user): vibe-advisor, vibe-paper, vibe-fast-exit-observer, vibe-event-advisor, vibe-event-replay and
    # live-follow-2. vibe becomes a long-term US ETF engine on its own book (docs/vibe-longterm-plan.md) without Jev's
    # 1-minute buys and sells; its services return with that runner.
    # The long-term vibe engine (docs/vibe-longterm-plan.md 15): a dry run (plans only) until the user approves --execute;
    # defined here, loaded only after the user's approval.
    # at load too: launchd does not run a start missed while the Mac was off (11-23: login 08:41, no decision until a manual
    # start at 08:59); a run waits for its windows, does each step once a day and ends on a holiday
    'vibe-longterm': (['scripts/vibe_longterm.py', '--run'], 'vibe-longterm', True, (8, 40, WEEKDAYS)),
    # 장중 상시 감시 of the long-term engine and 실계좌 2 (user 2026-11-24): reads records only, changes go to the long-term
    # Discord channel until 16:00; at load too, for a Mac turned on after 08:40
    'longterm-watch': (['scripts/longterm_watch.py', '--watch'], 'vibe-longterm', True, (8, 40, WEEKDAYS)),
    # API 부하·분산 상시 감사 (user 2026-11-24): reads the call log only, new findings go to the realtime Discord channel, 15:40 report
    'api-watch': (['scripts/api_load.py', '--watch'], 'shared-etf-paper', True, (8, 40, WEEKDAYS)),
    # US stock ideas for the long-term reports, reference only (docs/longterm-master-dca-ideas-plan.md 4, user 2026-11-18):
    # Fridays 10:30 the screen; 10:00 the quarterly universe + 13F, acting only on the third Friday of Feb/May/Aug/Nov
    'us-ideas': (['scripts/us_ideas.py', '--scheduled'], 'vibe-longterm', False, ((10, 0, (5,)), (10, 30, (5,)))),
    'trading-backup': (['scripts/backup_trading_data.py'], 'shared-etf-paper', False, (15, 50, WEEKDAYS)),
    # 2026-11-17: the runtime data synced into the project (state/settings sanitized for git, state/backups archives)
    'state-sync': (['scripts/state_sync.py', 'export'], 'shared-etf-paper', False, (16, 55, WEEKDAYS)),
    'ai-schedule-runner': (['scripts/schedule_dispatch.py'], 'scheduled-ai', True, None),
    # 2026-11-17: Jev → DISCORD_WEBHOOK_REALTIME, long-term → DISCORD_WEBHOOK_LONGTERM (reads only; cursor file)
    'discord-notify': (['scripts/discord_notify.py', '--watch'], 'shared-etf-paper', True, None),
    'exit-replay': (['scripts/exit_replay.py'], 'shared-etf-paper', False, (16, 10, (5,))),
    # Live mirror of the engine ledger per live account, dry-run (plans only); --execute is a separate user approval.
    # armed with --execute (user request 2026-11-13); orders go out only while the dashboard's live-order switch
    # (accounts.json live_orders, default off) and the automation switch are on, otherwise the mirror only plans
    'live-follow-1': (['scripts/nh_live_follow.py', '--execute', '--account', 'live-1'], 'live-follow', False, (8, 58, WEEKDAYS)),
    # 실계좌 2 on the long-term engine (user 2026-11-17, docs/longterm-live-account-plan.md); armed with --execute
    # (user approval 2026-11-20): orders go out only while the account's live-order and automation switches are on
    'longterm-live-2': (['scripts/longterm_live.py', '--execute', '--account', 'live-2'], 'longterm-live', False, (9, 35, WEEKDAYS)),
    # NXT close-bet strategy on virtual fills: the evening session decides, the morning session exits and labels.
    # run at load too (11/17): a reboot after 07:55 (11/13 08:07, 11/16 08:08) skipped the morning session; outside its
    # windows the script does nothing
    'nxt-virtual': (['scripts/nxt_virtual.py'], 'nxt-virtual', True, ((7, 55, WEEKDAYS), (15, 35, WEEKDAYS))),
}


def render(name, repo=REPO, home=None):
    args, folder, at_load, schedule = SERVICES[name]
    home = Path(home or Path.home())
    label = f'com.vibetrading.{name}'
    logs = home / '.vibe-trading/nhplug' / folder
    if name == 'shared-etf-dashboard':
        program = [str(repo / '.venv/bin/streamlit'), 'run', str(repo / args[0]),
                   '--server.address', '127.0.0.1', '--server.port', '8501', '--server.headless', 'true']  # no new browser tab per restart
        log = logs / 'streamlit.log'
        # PYTHONPATH too: Streamlit re-imports a changed module only from the app folder or PYTHONPATH, so without it
        # a function another session adds to a scripts/ module stayed missing until a restart (11-18 ImportError twice)
        env = {'PATH': PATH, 'PYTHONPATH': f'{repo / "agent"}:{repo / "scripts"}'}
    else:
        program = [str(repo / '.venv/bin/python'), '-u', str(repo / args[0]), *args[1:]]
        log = logs / f'{label}.log'
        env = {'PATH': PATH, 'PYTHONPATH': f'{repo / "agent"}:{repo / "scripts"}'}
    plist = {'Label': label, 'ProgramArguments': program, 'WorkingDirectory': str(repo), 'EnvironmentVariables': env,
             'RunAtLoad': at_load, 'KeepAlive': False, 'StandardOutPath': str(log), 'StandardErrorPath': str(log)}
    if schedule:
        plist['StartCalendarInterval'] = [{'Weekday': day, 'Hour': hour, 'Minute': minute}
                                          for hour, minute, days in _times(schedule) for day in days]
    if name == 'ai-schedule-runner':
        plist['StartInterval'] = 60
    return plist


def _times(schedule):
    return schedule if isinstance(schedule[0], tuple) else (schedule,)


def schedule_text(name):
    schedule = SERVICES[name][3]
    if not schedule:
        return '로그인 시 시작'
    return ' · '.join(f"{'평일' if days == WEEKDAYS else ','.join('월화수목금'[d - 1] for d in days)} {hour:02}:{minute:02}"
                      for hour, minute, days in _times(schedule))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', type=Path, help='folder to write the definitions into')
    parser.add_argument('--system', choices=['macos', 'windows', 'linux'], default='macos')
    args = parser.parse_args()
    if args.out:
        import os_services
        os_services.write(args.system, args.out)
    for name in SERVICES:
        print(f'{"com.vibetrading." + name:42} {schedule_text(name)}')

if __name__ == '__main__':
    main()
