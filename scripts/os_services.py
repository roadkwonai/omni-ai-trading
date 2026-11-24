"""The services of launchd_services.SERVICES on macOS (launchd), Windows (Task Scheduler) and Linux (systemd --user):
definitions to register, and one way to ask for their state and to start one.

A manager that cannot be asked raises Unknown. It never reads as "not running": callers refuse, or show 확인 불가.
Nothing here registers, stops or replaces a service; the definitions are files a person registers (docs/automation.md).
"""
from __future__ import annotations

import getpass
import json
import os
import plistlib
import re
import shlex
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import launchd_services as L

LABEL = 'com.vibetrading.'
SYSTEM = {'darwin': 'macos', 'win32': 'windows', 'linux': 'linux'}.get(sys.platform, sys.platform)
TASK_FOLDER = '\\VibeTrading\\'
MISSING = {'registered': False, 'running': False, 'pid': None, 'last_exit': None, 'started': None}
FAILED = (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError)


class Unknown(Exception):
    """The service manager could not be asked."""


def _text(run, args, timeout, check=True):
    return run(args, check=check, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)


# --- state --------------------------------------------------------------------------------------------------------------------

# state 4 is Running; the process is the one whose command line holds the task's own arguments (the scheduler gives no pid)
POWERSHELL = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$tasks = @(Get-ScheduledTask | Where-Object { $_.TaskPath -eq '\VibeTrading\' } | ForEach-Object {
  @{ name = $_.TaskName; state = [int]$_.State; result = (Get-ScheduledTaskInfo -InputObject $_).LastTaskResult; args = [string]$_.Actions[0].Arguments } })
$procs = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" | Where-Object { $_.CommandLine -like '*run_service.py*' } | ForEach-Object {
  @{ pid = $_.ProcessId; line = $_.CommandLine; started = $_.CreationDate.ToString('o') } })
ConvertTo-Json -Compress -Depth 4 @{ tasks = $tasks; procs = $procs }
"""


def _macos(run, timeout):
    found = {}
    for line in _text(run, ['launchctl', 'list'], timeout).stdout.splitlines():
        cols = line.split()
        if len(cols) == 3 and cols[2].startswith(LABEL):
            pid = int(cols[0]) if cols[0].isdigit() else None
            found[cols[2]] = {'registered': True, 'running': pid is not None, 'pid': pid,
                              'last_exit': int(cols[1]) if cols[1].lstrip('-').isdigit() else None, 'started': None}
    return found


def _windows_raw(run, timeout):
    return json.loads(_text(run, ['powershell', '-NoProfile', '-NonInteractive', '-Command', POWERSHELL], timeout).stdout)


def _windows(run, timeout):
    data, found = _windows_raw(run, timeout), {}
    for task in data['tasks']:
        proc = next((p for p in data['procs'] if task['args'] and task['args'] in (p['line'] or '')), {})
        found[task['name']] = {'registered': True, 'running': task['state'] == 4, 'pid': proc.get('pid'), 'last_exit': task['result'],
                               'started': proc.get('started')}
    return found


def _linux(run, timeout):
    units = [f'{LABEL}{name}.service' for name in L.SERVICES]
    shown = _text(run, ['systemctl', '--user', 'show', '-p', 'Id,LoadState,ActiveState,MainPID,ExecMainStatus,ExecMainStartTimestamp', *units], timeout).stdout
    found = {}
    for block in shown.strip().split('\n\n'):
        row = dict(line.split('=', 1) for line in block.splitlines())
        if row['LoadState'] == 'not-found':
            continue
        pid = int(row['MainPID']) or None
        found[row['Id'].removesuffix('.service')] = {'registered': True, 'running': row['ActiveState'] in ('active', 'activating', 'deactivating'), 'pid': pid,
                                                     'last_exit': int(row['ExecMainStatus']), 'started': row.get('ExecMainStartTimestamp') or None}
    return found


READERS = {'macos': _macos, 'windows': _windows, 'linux': _linux}


def states(run=subprocess.run, timeout=15, system=None):
    """{service: {'registered', 'running', 'pid', 'last_exit', 'started'}} of the registered ones; Unknown when the manager
    cannot be asked."""
    system = system or SYSTEM
    if system not in READERS:
        raise Unknown(f'no service manager for {system}')
    try:
        return {label.removeprefix(LABEL): row for label, row in READERS[system](run, timeout).items()}
    except FAILED as exc:
        raise Unknown(f'{system}: {type(exc).__name__}') from exc


def state(name, run=subprocess.run, timeout=15, system=None):
    """One service (an unregistered one is MISSING); Unknown when the manager cannot be asked."""
    if (system or SYSTEM) != 'macos':
        return states(run, timeout, system).get(name, dict(MISSING))
    try:
        got = _text(run, ['launchctl', 'list', LABEL + name], timeout, check=False)
        if got.returncode == 113:  # launchctl: Could not find service
            return dict(MISSING)
        if got.returncode != 0:
            raise ValueError(f'launchctl {got.returncode}')
        pid = re.search(r'"PID" = (\d+);', got.stdout)
        last = re.search(r'"LastExitStatus" = (-?\d+);', got.stdout)
        row = {'registered': True, 'running': bool(pid), 'pid': int(pid.group(1)) if pid else None, 'last_exit': int(last.group(1)) if last else None, 'started': None}
        if pid:
            row['started'] = _text(run, ['ps', '-o', 'lstart=', '-p', pid.group(1)], timeout, check=False).stdout.strip() or None
        return row
    except FAILED as exc:
        raise Unknown(f'macos: {type(exc).__name__}') from exc


def start(name, run=subprocess.run, timeout=15, system=None):
    """Ask the manager to start a registered service now; whether it took the request (an already running one stays)."""
    if run is subprocess.run and 'PYTEST_CURRENT_TEST' in os.environ:  # a test started the real paper runner once (2026-11-24 20:12)
        raise RuntimeError('a test may not start a real service')
    system = system or SYSTEM
    args = {'macos': lambda: ['launchctl', 'kickstart', f'gui/{os.getuid()}/{LABEL}{name}'],
            'windows': lambda: ['schtasks', '/Run', '/TN', f'{TASK_FOLDER}{LABEL}{name}'],
            'linux': lambda: ['systemctl', '--user', 'start', '--no-block', f'{LABEL}{name}.service']}.get(system)
    if args is None:
        return False
    try:
        return _text(run, args(), timeout, check=False).returncode == 0
    except FAILED:
        return False


def split_arguments(line):
    """A Windows command line's arguments (ours hold quotes only around a whole argument)."""
    return [a or b for a, b in re.findall(r'"([^"]*)"|(\S+)', line or '')]


def definitions(system=None):
    """Where registered definitions live (Windows keeps them in the scheduler, not in files)."""
    return {'macos': Path.home() / 'Library/LaunchAgents', 'linux': Path.home() / '.config/systemd/user'}.get(system or SYSTEM)


def registered_args(name, folder=None, run=subprocess.run, timeout=15, system=None):
    """The command a service was registered with, or None when it is not registered; Unknown when that cannot be read."""
    system = system or SYSTEM
    base = folder or definitions(system)
    try:
        if system == 'macos':
            return plistlib.loads((Path(base) / f'{LABEL}{name}.plist').read_bytes()).get('ProgramArguments')
        if system == 'linux':
            unit = (Path(base) / f'{LABEL}{name}.service').read_text(encoding='utf-8')
            return shlex.split(next(line for line in unit.splitlines() if line.startswith('ExecStart=')).removeprefix('ExecStart='))
        if system == 'windows':
            return next((split_arguments(t['args']) for t in _windows_raw(run, timeout)['tasks'] if t['name'] == LABEL + name), None)
    except FileNotFoundError:
        if system == 'windows':  # no PowerShell, not "no such definition"
            raise Unknown('windows: FileNotFoundError') from None
    except (*FAILED, StopIteration, plistlib.InvalidFileException) as exc:
        raise Unknown(f'{system}: {type(exc).__name__}') from exc
    return None


# --- definitions ----------------------------------------------------------------------------------------------------------------

DAYS = {1: 'Monday', 2: 'Tuesday', 3: 'Wednesday', 4: 'Thursday', 5: 'Friday'}
STREAMLIT = ['--server.address', '127.0.0.1', '--server.port', '8501', '--server.headless', 'true']


def _times(schedule):
    return L._times(schedule) if schedule else ()


def _task(name, repo, home, user):
    """A Task Scheduler definition. Times are the PC's local time: the PC must be set to Seoul time, as a Mac must."""
    args, logs, at_load, schedule = L.SERVICES[name]
    log = home / '.vibe-trading' / 'nhplug' / logs / f'{LABEL}{name}.log'
    run = ['-X', 'utf8', '-u', str(repo / 'scripts' / 'run_service.py'), '--log', str(log)]
    run += ['--module', 'streamlit', '--', 'run', str(repo / args[0]), *STREAMLIT] if name == 'shared-etf-dashboard' else ['--', *args]
    line = ' '.join(f'"{a}"' if ' ' in a else a for a in run)
    triggers = ''.join(f'<CalendarTrigger><StartBoundary>2026-01-01T{h:02}:{m:02}:00</StartBoundary><Enabled>true</Enabled><ScheduleByWeek>'
                       f'<WeeksInterval>1</WeeksInterval><DaysOfWeek>{"".join(f"<{DAYS[d]} />" for d in days)}</DaysOfWeek></ScheduleByWeek></CalendarTrigger>'
                       for h, m, days in _times(schedule))
    if at_load:  # without the user a logon trigger means any user, which needs an administrator
        triggers += f'<LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId></LogonTrigger>'
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><URI>{TASK_FOLDER}{LABEL}{name}</URI></RegistrationInfo>
  <Triggers>{triggers}</Triggers>
  <Principals><Principal id="Author"><UserId>{escape(user)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <WakeToRun>true</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>5</Priority>
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(str(repo / '.venv' / 'Scripts' / 'pythonw.exe'))}</Command><Arguments>{escape(line)}</Arguments><WorkingDirectory>{escape(str(repo))}</WorkingDirectory></Exec></Actions>
</Task>
'''.encode('utf-16')


def _units(name, repo, home):
    """A systemd user service, and a timer when it has a schedule (systemd reads the time zone in OnCalendar)."""
    plist = L.render(name, repo=repo, home=home)
    quote = lambda a: '"' + a.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'  # noqa: E731
    env = {**plist['EnvironmentVariables'], 'PATH': f'{home}/.local/bin:/usr/local/bin:/usr/bin:/bin', 'PYTHONUTF8': '1'}
    lines = ['[Unit]', f'Description=Omni AI Trading {name}', '', '[Service]', 'Type=exec', f'WorkingDirectory={repo}',
             *(f'Environment={quote(f"{k}={v}")}' for k, v in env.items()), 'ExecStart=' + ' '.join(map(quote, plist['ProgramArguments'])),
             f"StandardOutput=append:{plist['StandardOutPath']}", f"StandardError=append:{plist['StandardErrorPath']}", 'Restart=no']
    if plist['RunAtLoad']:
        lines += ['', '[Install]', 'WantedBy=default.target']
    out = {f'{LABEL}{name}.service': '\n'.join(lines) + '\n'}
    times = _times(L.SERVICES[name][3])
    if times:
        out[f'{LABEL}{name}.timer'] = '\n'.join(['[Unit]', f'Description=Omni AI Trading {name} schedule', '', '[Timer]',
                                                 *(f"OnCalendar={','.join(DAYS[d][:3] for d in days)} *-*-* {h:02}:{m:02}:00 Asia/Seoul" for h, m, days in times),
                                                 'Persistent=false', 'AccuracySec=1s', '', '[Install]', 'WantedBy=timers.target']) + '\n'
    return {k: v.encode() for k, v in out.items()}


def current_user():
    domain = os.environ.get('USERDOMAIN', '')
    return f'{domain}\\{getpass.getuser()}' if domain else getpass.getuser()


def render(system, repo=L.REPO, home=None, user=None):
    """{file name: contents} of every service's definition on that system."""
    home, out = Path(home or Path.home()), {}
    for name in L.SERVICES:
        if system == 'macos':
            out[f'{LABEL}{name}.plist'] = plistlib.dumps(L.render(name, repo=repo, home=home))
        elif system == 'windows':
            out[f'{LABEL}{name}.xml'] = _task(name, repo, home, user or current_user())
        elif system == 'linux':
            out |= _units(name, repo, home)
        else:
            raise ValueError(system)
    return out


def write(system, target, **kw):
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    for name, data in render(system, **kw).items():
        (target / name).write_bytes(data)
