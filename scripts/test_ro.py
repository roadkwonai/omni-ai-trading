"""scripts/ro is the only command the scheduled tasks may run without approval (2026-11-18): it must refuse anything
outside its list, every --execute, writes outside its folders and SQL that is not a single read."""
import os
import subprocess
from pathlib import Path

import pytest

RO = Path(__file__).resolve().parent / 'ro'


def run(*args, env=None):
    return subprocess.run([str(RO), *args], capture_output=True, text=True, timeout=60, env=env)


@pytest.mark.parametrize('args', [
    ('nh_guarded_live.py',), ('nh_live_follow.py',), ('vibe_longterm.py', '--run'), ('longterm_report.py', '--execute'),
    ('etf_regime_observer.py', '--watch'), ('market_movers.py',), ('--out', '/tmp/x', 'longterm_audit.py', '--status'),
    ('--out', '.scratch/../x', 'longterm_audit.py', '--status'), ('pytest', '/tmp/test_x.py'), ('bash',), ('../ro',),
    ('longterm_evidence.py', '2026-11-18'),  # reconstruct() writes evidence rows into the engine database
    ('ipo_shadow.py', '--day', '2026-11-19'),  # update() rewrites the shadow rows; only --summary reads
    ('fade_shadow.py',),  # update() rewrites the day; only --summary reads
    ('overnight_catalysts.py', '--day', '2026-11-25'),  # update() rewrites the day; only --summary reads
    ('longterm_report.py', '--day', '2026-11-18', '--fill', '/tmp/x.json', '--reports', '/tmp/r'),  # reports only into its folders
])
def test_refuses(args):
    assert run(*args).returncode != 0


def test_sql_reads_only(tmp_path):
    db = Path.home() / '.vibe-trading' / 'nhplug' / 'vibe-longterm' / 'longterm.sqlite3'
    if not db.exists():
        pytest.skip('no engine database on this machine')
    assert run('sql', str(db), 'SELECT 1 AS one').stdout.strip() == '{"one": 1}'
    for q in ('DELETE FROM decisions', 'SELECT 1; DROP TABLE decisions', 'UPDATE state SET value=1'):
        assert run('sql', str(db), q).returncode != 0
    assert run('sql', str(tmp_path / 'x.sqlite3'), 'SELECT 1').returncode != 0  # outside ~/.vibe-trading


def test_runs_a_listed_script():
    assert run('longterm_audit.py', '--status').returncode == 0


def test_pytest_cannot_delete_a_folder(tmp_path):
    """pytest wipes its --basetemp folder; only -q -x -v -k -p no:cacheprovider and test files pass."""
    (tmp_path / 'keep.txt').write_text('keep')
    assert run('pytest', '-q', '-pno:cacheprovider', f'--basetemp={tmp_path}', 'scripts/test_engine_ops.py').returncode != 0
    assert (tmp_path / 'keep.txt').exists()


def test_pytest_runs_only_repo_tests(tmp_path):
    """bash case patterns let * match '/', so scripts/test_/../<anything>.py used to run any file."""
    marker = tmp_path / 'RAN'
    (tmp_path / 'payload.py').write_text(f'from pathlib import Path\ndef test_p():\n    Path({str(marker)!r}).write_text("x")\n')
    rel = os.path.relpath(tmp_path / 'payload.py', RO.parent)
    assert run('pytest', '-q', f'scripts/test_/../{rel}').returncode != 0
    assert not marker.exists()


def test_out_cannot_overwrite_engine_data(tmp_path):
    home = tmp_path / 'home'
    engine = home / '.vibe-trading' / 'nhplug' / 'vibe-longterm'
    engine.mkdir(parents=True)
    (engine / 'longterm.sqlite3').write_text('engine data')
    assert run('--out', str(engine / 'longterm.sqlite3'), 'longterm_audit.py', '--status',
               env={**os.environ, 'HOME': str(home)}).returncode != 0
    assert (engine / 'longterm.sqlite3').read_text() == 'engine data'


@pytest.mark.parametrize('args', [
    ('launchd_services.py', '--out', '{tmp}'), ('launchd_services.py', '--out={tmp}'),
    ('intraday_audit.py', '--incidents-db', '{tmp}/i.sqlite3'), ('audit_incidents.py', '--db', '{tmp}/i.sqlite3'),
    ('us_ideas.py', '--root', '{tmp}', '--status'), ('longterm_report.py', '--reports=..', '--day', '2026-11-18', '--status'),
])
def test_script_path_arguments_stay_in_task_folders(tmp_path, args):
    """A listed script's own path options (plists, incident db, idea db, report folder) write nowhere else."""
    assert run(*(a.format(tmp=tmp_path) for a in args)).returncode != 0
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize('args', [
    ('etf_regime_observer.py', '--wat'), ('longterm_report.py', '--exec'), ('nh_live_shadow.py', '--li=x'),  # argparse takes a unique prefix of an option
    ('longterm_report.py longterm_audit.py', '--status'), (), ('--out',), ('--out', '.scratch/x'), ('sql',), ('sql', '~/.vibe-trading/nhplug/x.sqlite3', ''),
    ('pytest',), ('pytest', '-q'), ('pytest', '-k'),
])
def test_refuses_abbreviations_and_incomplete_calls(args):
    assert run(*args).returncode != 0


def test_services_prints_the_table_and_says_when_it_cannot():
    import os_services
    import ro
    import io
    out = io.StringIO()
    row = {'registered': True, 'running': True, 'pid': 7, 'last_exit': 0, 'started': None}
    assert ro.services(out, lambda: {'preflight': {**row, 'running': False, 'pid': None, 'last_exit': 1}, 'shared-etf-paper': row}) == 0
    assert out.getvalue().splitlines() == ['-\t1\tcom.vibetrading.preflight', '7\t0\tcom.vibetrading.shared-etf-paper']

    def unknown():
        raise os_services.Unknown('x')
    with pytest.raises(ro.Refused):
        ro.services(out, unknown)


def test_windows_paths_are_paths_too(monkeypatch):
    import ro
    monkeypatch.setattr(ro, 'WINDOWS', True)
    monkeypatch.setattr(ro, 'HOME', 'C:/Users/me')
    for bad in (r'C:\x', r'..\x', r'a\b', 'C:x', r'C:\Users\me\.vibe-trading\nhplug\vibe-longterm\longterm.sqlite3', '//server/x'):
        assert ro.is_path(bad) and not ro.task_path(bad), bad
    for good in (r'.scratch\x.md', '.scratch/x.md', r'C:\Users\me\.vibe-trading\nhplug\vibe-longterm\reviews\a.md'):
        assert ro.task_path(good), good
    monkeypatch.setattr(ro, 'WINDOWS', False)
    assert not ro.is_path('a:b') and not ro.task_path(r'.scratch\x')


@pytest.mark.skipif(os.name == 'nt', reason='POSIX signals')
def test_stopping_ro_stops_the_script_it_runs(tmp_path):
    """As the bash runner did (exec): a killed task must not leave its script behind."""
    import signal
    import sys
    import time
    probe = ('import sys, os; sys.path.insert(0, "scripts"); import ro; ro.plan = lambda argv: (None, ["-c", "import time, os; open(%r, \'w\').write(str(os.getpid())); time.sleep(30)"]);'
             ' sys.exit(ro.main([]))') % str(tmp_path / 'pid')
    child = subprocess.Popen([sys.executable, '-c', probe], cwd=RO.parent.parent)
    for _ in range(100):
        if (tmp_path / 'pid').exists() and (tmp_path / 'pid').read_text():
            break
        time.sleep(0.05)
    child.send_signal(signal.SIGTERM)
    child.wait(5)
    time.sleep(0.2)
    pid = int((tmp_path / 'pid').read_text())
    try:
        os.kill(pid, 0)
        alive = True
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        alive = False
    assert not alive and child.returncode == -signal.SIGTERM


def test_json_shows_one_part_of_a_task_file():
    """11-18, 11-19 and 11-20 runs stalled on `python3 -c` because Read cannot pick one part of a large JSON."""
    path = RO.parents[1] / '.scratch' / 'test-ro-json.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text('{"views": {"longterm": [{"글": "가"}, {"글": "나"}]}, "day": "2026-11-25"}', encoding='utf-8')
    try:
        name = '.scratch/test-ro-json.json'
        assert run('json', name).stdout.split('\n')[:2] == ['views\t38', 'day\t12']  # the parts and their sizes
        assert run('json', name, 'views', 'longterm', '1').stdout.strip() == '{\n "글": "나"\n}'
        missing = run('json', name, 'views', 'nothing')
        assert missing.returncode == 2 and 'longterm' in missing.stderr  # says what there is
        assert run('json', name, 'views', 'longterm', '9').returncode == 2
    finally:
        path.unlink()


@pytest.mark.parametrize('name', ['~/.vibe-trading/agent.json', '~/.vibe-trading/nhplug/accounts.json', '.env', 'scripts/ro.py',
                                  '.scratch/../.env', '.scratch/no-such-file.json'])
def test_json_reads_only_the_task_folders(name):
    done = run('json', os.path.expanduser(name))
    assert done.returncode == 2 and not done.stdout
