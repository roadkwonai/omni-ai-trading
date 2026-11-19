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
