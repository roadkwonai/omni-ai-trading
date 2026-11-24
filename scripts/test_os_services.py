"""One service list, three operating systems (docs/cli/windows-support.md W2). Only macOS is run for real; the Windows Task
Scheduler and systemd answers are the shapes their documentation gives."""
import json
import plistlib
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

import launchd_services as L
import os_services as S

NS = {'t': 'http://schemas.microsoft.com/windows/2004/02/mit/task'}


def out(stdout='', code=0):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr='')


# --- definitions ------------------------------------------------------------------------------------------------------------

def test_every_service_has_a_definition_on_every_system(tmp_path):
    for system, files in (('macos', 1), ('windows', 1), ('linux', None)):
        made = S.render(system, repo=Path('/r'), home=Path('/h'), user='PC\\me')
        assert {n.split('.')[2] if n.startswith('com.') else n for n in made} >= set(L.SERVICES), system
        S.write(system, tmp_path / system, repo=Path('/r'), home=Path('/h'), user='PC\\me')
        assert sorted(p.name for p in (tmp_path / system).iterdir()) == sorted(made)


def test_the_macos_files_are_what_launchd_services_renders():
    made = S.render('macos', repo=Path('/r'), home=Path('/h'))
    for name in L.SERVICES:
        assert plistlib.loads(made[f'com.vibetrading.{name}.plist']) == L.render(name, repo=Path('/r'), home=Path('/h'))


def task(name):
    return ET.fromstring(S.render('windows', repo=Path('/r'), home=Path('/h'), user='PC\\me')[f'com.vibetrading.{name}.xml'].decode('utf-16'))


def test_windows_tasks_start_on_the_same_days_and_never_twice():
    days = {1: 'Monday', 2: 'Tuesday', 3: 'Wednesday', 4: 'Thursday', 5: 'Friday'}
    for name, (_, _, at_load, schedule) in L.SERVICES.items():
        root = task(name)
        found = {(t.find('t:StartBoundary', NS).text[11:16], tuple(d.tag.split('}')[1] for d in t.find('t:ScheduleByWeek/t:DaysOfWeek', NS)))
                 for t in root.findall('t:Triggers/t:CalendarTrigger', NS)}
        assert found == {(f'{h:02}:{m:02}', tuple(days[d] for d in ds)) for h, m, ds in (L._times(schedule) if schedule else ())}, name
        logon = root.findall('t:Triggers/t:LogonTrigger', NS)
        assert bool(logon) is at_load and all(t.find('t:UserId', NS).text == 'PC\\me' for t in logon)
        settings = root.find('t:Settings', NS)
        assert settings.find('t:MultipleInstancesPolicy', NS).text == 'IgnoreNew'
        assert settings.find('t:ExecutionTimeLimit', NS).text == 'PT0S'  # the default stops a task after three days
        assert settings.find('t:DisallowStartIfOnBatteries', NS).text == settings.find('t:StopIfGoingOnBatteries', NS).text == 'false'
        assert settings.find('t:StartWhenAvailable', NS).text == 'false'  # a start missed while off is not made up later, as with launchd
        assert root.find('t:Principals/t:Principal/t:RunLevel', NS).text == 'LeastPrivilege'


def test_a_windows_task_runs_the_same_script_and_arguments_through_the_log_wrapper():
    for name, (args, folder, _, _) in L.SERVICES.items():
        action = task(name).find('t:Actions/t:Exec', NS)
        line = S.split_arguments(action.find('t:Arguments', NS).text)
        assert line[-len(args):] == ([*args] if name != 'shared-etf-dashboard' else line[-len(args):]), name
        assert 'run_service.py' in action.find('t:Arguments', NS).text and folder in line[line.index('--log') + 1]
        assert action.find('t:Command', NS).text.endswith('pythonw.exe')
    assert '--execute' in S.split_arguments(task('live-follow-1').find('t:Actions/t:Exec/t:Arguments', NS).text)
    dashboard = S.split_arguments(task('shared-etf-dashboard').find('t:Actions/t:Exec/t:Arguments', NS).text)
    assert dashboard[dashboard.index('--module') + 1] == 'streamlit' and '127.0.0.1' in dashboard and '8501' in dashboard


def test_systemd_units_keep_seoul_time_whatever_the_machine_clock_is_set_to():
    made = S.render('linux', repo=Path('/my repo'), home=Path('/h'))
    for name, (args, folder, at_load, schedule) in L.SERVICES.items():
        unit = made[f'com.vibetrading.{name}.service'].decode()
        start = next(line for line in unit.splitlines() if line.startswith('ExecStart='))
        assert name == 'shared-etf-dashboard' or shlex.split(start.removeprefix('ExecStart='))[-len(args):] == [f'/my repo/{args[0]}', *args[1:]]
        assert 'WorkingDirectory=/my repo' in unit and 'PYTHONUTF8=1' in unit and f'StandardOutput=append:/h/.vibe-trading/nhplug/{folder}/' in unit
        assert 'Restart=no' in unit  # a runner that stopped is checked before it is started again
        assert ('WantedBy=default.target' in unit) is at_load
        timer = made.get(f'com.vibetrading.{name}.timer')
        assert (timer is not None) is bool(schedule)
        if timer:
            text = timer.decode()
            assert text.count('OnCalendar=') == len(L._times(schedule)) and 'Persistent=false' in text
            assert all(line.endswith(' Asia/Seoul') for line in text.splitlines() if line.startswith('OnCalendar='))
    assert 'OnCalendar=Mon,Tue,Wed,Thu,Fri *-*-* 08:40:00 Asia/Seoul' in made['com.vibetrading.vibe-longterm.timer'].decode()
    assert 'OnCalendar=Fri *-*-* 16:10:00 Asia/Seoul' in made['com.vibetrading.exit-replay.timer'].decode()


def test_the_wrapper_runs_a_script_with_its_arguments_and_logs_korean(tmp_path):
    script = tmp_path / 'job.py'
    script.write_text('import sys, os, subprocess\nprint("한글", sys.argv[1:], os.environ["PYTHONUTF8"], __name__, flush=True)\n'
                      'subprocess.run([sys.executable, "-c", "print(\'자식\')"])\nprint("오류 줄", file=sys.stderr)\nraise SystemExit(3)\n', encoding='utf-8')
    log = tmp_path / 'logs/x.log'
    done = subprocess.run([sys.executable, '-X', 'utf8', str(Path(S.__file__).with_name('run_service.py')), '--log', str(log), '--', str(script), '--execute', 'a b'],
                          env={'PATH': '/usr/bin:/bin'})
    text = log.read_text(encoding='utf-8')
    assert done.returncode == 3 and "한글 ['--execute', 'a b'] 1 __main__" in text and '자식' in text and '오류 줄' in text
    subprocess.run([sys.executable, str(Path(S.__file__).with_name('run_service.py')), '--log', str(log), '--module', 'json.tool', '--', '--help'],
                   env={'PATH': '/usr/bin:/bin'})
    assert log.read_text(encoding='utf-8').startswith(text) and 'usage' in log.read_text(encoding='utf-8')


# --- state --------------------------------------------------------------------------------------------------------------------

WINDOWS = {'tasks': [{'name': 'com.vibetrading.shared-etf-paper', 'state': 4, 'result': 267009, 'args': '-u x\\run_service.py --log l -- scripts/nh_paper_isolated.py --run'},
                     {'name': 'com.vibetrading.preflight', 'state': 3, 'result': 1, 'args': '-u x\\run_service.py --log l -- scripts/preflight.py'},
                     {'name': 'com.vibetrading.us-ideas', 'state': 3, 'result': 267011, 'args': ''}],
           'procs': [{'pid': 42, 'line': 'C:\\p\\pythonw.exe -u x\\run_service.py --log l -- scripts/nh_paper_isolated.py --run', 'started': '2026-11-25T09:00:01+09:00'}]}
SYSTEMD = '''Id=com.vibetrading.shared-etf-paper.service
LoadState=loaded
ActiveState=active
MainPID=42
ExecMainStatus=0
ExecMainStartTimestamp=Wed 2026-11-25 09:00:01 KST

Id=com.vibetrading.preflight.service
LoadState=loaded
ActiveState=failed
MainPID=0
ExecMainStatus=1
ExecMainStartTimestamp=

Id=com.vibetrading.exit-replay.service
LoadState=not-found
ActiveState=inactive
MainPID=0
ExecMainStatus=0
'''


def fake(system):
    def run(args, **kw):
        if system == 'macos':
            assert args == ['launchctl', 'list'] and kw.get('check')
            return out('PID\tStatus\tLabel\n42\t0\tcom.vibetrading.shared-etf-paper\n-\t1\tcom.vibetrading.preflight\n9\t0\tcom.apple.x\n')
        if system == 'windows':
            assert args[0] == 'powershell' and '-NoProfile' in args
            return out(json.dumps(WINDOWS))
        assert args[:3] == ['systemctl', '--user', 'show']
        return out(SYSTEMD)
    return run


@pytest.mark.parametrize('system', ['macos', 'windows', 'linux'])
def test_states_are_the_same_on_every_system(system):
    got = S.states(run=fake(system), system=system)
    assert got['shared-etf-paper']['running'] is True and got['shared-etf-paper']['pid'] == 42
    assert got['preflight']['running'] is False and got['preflight']['last_exit'] == 1
    assert 'exit-replay' not in got


@pytest.mark.parametrize('system', ['macos', 'windows', 'linux'])
@pytest.mark.parametrize('failure', [FileNotFoundError('x'), subprocess.TimeoutExpired('x', 1), subprocess.CalledProcessError(1, 'x'), 'garbage'])
def test_a_manager_that_cannot_be_asked_is_unknown_not_stopped(system, failure):
    def run(args, **kw):
        if failure == 'garbage':
            return out('???')
        raise failure
    if system == 'macos' and failure == 'garbage':
        assert S.states(run=run, system=system) == {}  # a table without our labels is simply no service of ours
        return
    with pytest.raises(S.Unknown):
        S.states(run=run, system=system)
    if failure != 'garbage':
        with pytest.raises(S.Unknown):
            S.state('preflight', run=run, system=system)


def test_an_unknown_operating_system_is_unknown():
    with pytest.raises(S.Unknown):
        S.states(run=lambda *a, **k: out(), system='plan9')


def test_one_macos_service_is_asked_by_its_label():
    calls = []

    def run(args, **kw):
        calls.append(args)
        return out('{\n\t"LastExitStatus" = 15;\n\t"PID" = 7;\n};') if args[0] == 'launchctl' else out('Wed Nov 25 09:00:01 2026\n')
    assert S.state('preflight', run=run, system='macos') == {'registered': True, 'running': True, 'pid': 7, 'last_exit': 15, 'started': 'Wed Nov 25 09:00:01 2026'}
    assert calls[0] == ['launchctl', 'list', 'com.vibetrading.preflight']
    assert S.state('x', run=lambda *a, **k: out(code=113), system='macos') == {'registered': False, 'running': False, 'pid': None, 'last_exit': None, 'started': None}

    with pytest.raises(S.Unknown):  # any other refusal is not "not registered"
        S.state('x', run=lambda *a, **k: out(code=1), system='macos')


@pytest.mark.parametrize('system', ['windows', 'linux'])
def test_one_service_elsewhere(system):
    assert S.state('shared-etf-paper', run=fake(system), system=system)['running'] is True
    assert S.state('exit-replay', run=fake(system), system=system)['registered'] is False


@pytest.mark.parametrize('system, want', [('macos', ['launchctl', 'kickstart']), ('windows', ['schtasks', '/Run', '/TN']), ('linux', ['systemctl', '--user', 'start', '--no-block'])])
def test_start_asks_the_manager_and_reports_a_refusal(system, want):
    calls = []
    assert S.start('preflight', run=lambda a, **k: calls.append(a) or out(), system=system) is True
    assert calls[0][:len(want)] == want and 'preflight' in calls[0][-1]
    assert S.start('preflight', run=lambda a, **k: out(code=1), system=system) is False

    def missing(args, **kw):
        raise FileNotFoundError(args[0])
    assert S.start('preflight', run=missing, system=system) is False


def test_the_registered_arguments_decide_whether_real_orders_are_armed(tmp_path):
    (tmp_path / 'com.vibetrading.live-follow-1.plist').write_bytes(plistlib.dumps(L.render('live-follow-1')))
    assert '--execute' in S.registered_args('live-follow-1', folder=tmp_path, system='macos')
    assert S.registered_args('longterm-live-2', folder=tmp_path, system='macos') is None
    for name, data in S.render('linux', repo=Path('/my repo'), home=Path('/h')).items():
        (tmp_path / name).write_bytes(data)
    assert S.registered_args('live-follow-1', folder=tmp_path, system='linux')[-3:] == ['--execute', '--account', 'live-1']
    assert S.registered_args('shared-etf-paper', run=fake('windows'), system='windows')[-1] == '--run'
    assert S.registered_args('exit-replay', run=fake('windows'), system='windows') is None

    def broken(*a, **k):
        raise FileNotFoundError('powershell')
    with pytest.raises(S.Unknown):
        S.registered_args('shared-etf-paper', run=broken, system='windows')
    (tmp_path / 'com.vibetrading.x.plist').write_bytes(b'not a plist')
    with pytest.raises(S.Unknown):
        S.registered_args('x', folder=tmp_path, system='macos')


# --- callers: unknown never reads as stopped or registered -------------------------------------------------------------------

def unknown(*a, **k):
    raise S.Unknown('x')


def test_a_backup_is_not_restored_over_a_runtime_whose_services_are_unknown(tmp_path, monkeypatch):
    import state_sync as ss
    from test_state_sync import make_runtime
    root = make_runtime(tmp_path)
    archive, _ = ss.export_archive(root, tmp_path / 'out')
    monkeypatch.setattr(S, 'states', unknown)
    with pytest.raises(SystemExit, match='확인'):
        ss.import_archive(archive, root)
    assert ss.running_services() is None
    assert ss.import_archive(archive, root, force_offline=True)['restored']
    assert set(ss.check(root, tmp_path / 'out')['services'].values()) <= {'unknown', 'plist'}


def test_preflight_fails_when_the_services_cannot_be_read(monkeypatch):
    import preflight as pf
    monkeypatch.setattr(S, 'states', unknown)
    ok, why = pf.services(None)
    assert ok is False and '확인 불가' in why
    monkeypatch.setattr(S, 'state', unknown)
    assert pf._running('market-movers') is False


def test_the_day_start_starts_nothing_it_cannot_see(monkeypatch, tmp_path, capsys):
    import trading_day_start as d
    monkeypatch.setattr(S, 'states', unknown)
    monkeypatch.setattr(S, 'state', unknown)
    monkeypatch.setattr(S, 'start', lambda *a, **k: pytest.fail('started'))
    monkeypatch.setattr(d, 'ROOT', tmp_path)
    monkeypatch.setattr(d, 'ASK_WAIT', 0)
    monkeypatch.setattr(d, 'trading_day', lambda now: (True, 'trading_day'))
    d.main()
    assert json.loads((tmp_path / 'trading-day-start.jsonl').read_text(encoding='utf-8'))['decision'] == 'services_unknown'


def test_a_restart_refuses_when_the_runner_s_state_is_unknown(tmp_path):
    import restart_runner as r
    (tmp_path / 'shared-etf-paper').mkdir()
    (tmp_path / 'shared-etf-paper/account-state.json').write_text('{"holdings": {}}', encoding='utf-8')

    def broken(args, **kw):
        raise FileNotFoundError(args[0])
    got = r.restart('shared', 'test', root=tmp_path, run=broken, sleep=lambda s: None, timeout=0.1)
    assert got['result'] == 'refused' and 'unknown' in got['why'] and not (tmp_path / 'shared-etf-paper/STOP').exists()


def test_the_audits_say_unknown(monkeypatch, tmp_path):
    import intraday_audit as A

    def broken(args, **kw):
        raise FileNotFoundError(args[0])
    assert all(row['unknown'] for row in A.services(run=broken).values())
    import vibe_market_brief as B
    monkeypatch.setattr(S, 'state', unknown)
    assert B.service_state() == 'unknown'


def test_a_test_cannot_start_a_real_service():
    with pytest.raises(RuntimeError):
        S.start('preflight')


@pytest.mark.parametrize('state, running', [('activating', True), ('deactivating', True), ('active', True), ('inactive', False), ('failed', False)])
def test_a_systemd_unit_on_its_way_up_or_down_counts_as_running(state, running):
    text = f'Id=com.vibetrading.preflight.service\nLoadState=loaded\nActiveState={state}\nMainPID=0\nExecMainStatus=0\nExecMainStartTimestamp=\n'
    assert S.states(run=lambda *a, **k: out(text), system='linux')['preflight']['running'] is running


def test_a_process_killed_by_a_signal_keeps_its_negative_exit():
    got = S.states(run=lambda *a, **k: out('PID\tStatus\tLabel\n-\t-9\tcom.vibetrading.preflight\n'), system='macos')
    assert got['preflight']['last_exit'] == -9


def test_windows_tasks_wake_the_pc():
    assert b'<WakeToRun>true</WakeToRun>' in S.render('windows', user='pc\\me')['com.vibetrading.trading-day-start.xml'].decode('utf-16').encode()
