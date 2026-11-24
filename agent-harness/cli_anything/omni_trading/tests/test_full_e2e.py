"""The installed command as another program runs it, on a temporary HOME with no notification channel."""
import json
import os
import subprocess
import sys
import time

import pytest

from cli_anything.omni_trading.utils.omni_backend import repo


def omni(home, *args, stdin=None):
    env = {**os.environ, 'PYTHONPATH': str(repo() / 'agent-harness')}
    done = subprocess.run([sys.executable, '-m', 'cli_anything.omni_trading', '--json', *args], capture_output=True, text=True,
                          encoding='utf-8', env=env, input=stdin, timeout=60)
    return done.returncode, json.loads(done.stdout) if done.stdout.strip() else {}, done.stderr


def now(home):
    return omni(home, 'settings', 'get', 'strategy.add_step_pct')[1]['data']['settings'][0]['now']


def test_plan_apply_history_revert(quiet):
    code, got, _ = omni(quiet, '--agent', 'codex', 'settings', 'plan', 'strategy.add_step_pct=0.9', '--reason', '시험')
    assert code == 0 and got['data']['level'] == 'W2' and now(quiet) == 0.7
    flags = ['--during-market']
    code, done, _ = omni(quiet, '--agent', 'codex', 'settings', 'apply', got['data']['plan'], *flags)
    assert (code, done['ok'], now(quiet)) == (0, True, 0.9)
    row = omni(quiet, 'settings', 'history', '--limit', '1')[1]['data']['changes'][0]
    assert row['setting'] == 'strategy.add_step_pct' and row['who'] == 'cli:codex 시험'
    back = omni(quiet, 'settings', 'revert', str(row['id']), '--reason', '되돌림')[1]['data']['plan']
    assert omni(quiet, 'settings', 'apply', back, *flags)[0] == 0 and now(quiet) == 0.7
    assert omni(quiet, 'settings', 'revert', str(row['id']), '--reason', '또')[0] == 4


def test_a_live_plan_cannot_be_applied_without_the_channel(home):
    plan = omni(home, 'settings', 'plan', 'strategy.add_step_pct=0.9', '--reason', '시험')[1]['data']['plan']
    for stdin in (None, f'{plan} 적용\nyes\n'):
        code, got, _ = omni(home, 'settings', 'apply', plan, '--during-market', stdin=stdin)
        assert code == 3 and got['error']['type'] == 'approval_required' and got['error']['next']
    assert omni(home, 'settings', 'approve', plan)[0] == 6
    assert now(home) == 0.7


@pytest.mark.skipif(sys.platform == 'win32', reason='pty')
def test_a_made_up_terminal_is_no_approval(home):
    import pty
    plan = omni(home, 'settings', 'plan', 'strategy.add_step_pct=0.9', '--reason', '시험')[1]['data']['plan']
    pid, fd = pty.fork()
    if pid == 0:
        os.environ['PYTHONPATH'] = str(repo() / 'agent-harness')
        os.execv(sys.executable, [sys.executable, '-m', 'cli_anything.omni_trading', 'settings', 'apply', plan, '--during-market'])
    time.sleep(1.5)
    try:
        os.write(fd, f'{plan} 적용\nAAAAAAAA\n'.encode())
    except OSError:
        pass
    _, status = os.waitpid(pid, 0)
    os.close(fd)
    assert os.waitstatus_to_exitcode(status) == 3 and now(home) == 0.7


def test_errors_have_their_codes_and_one_json_line(home):
    assert omni(home, 'settings', 'get', 'nope.x')[0] == 4
    code, got, _ = omni(home, 'settings', 'plan', 'strategy.add_step=1', '--reason', 'x')
    assert code == 4 and 'strategy.add_step_pct' in got['error']['next']
    assert omni(home, 'settings', 'plan', 'strategy.add_step_pct=0.9', 'tunables.losing_streak=4', '--reason', 'x')[0] == 4
    assert omni(home, 'query', '../../x.sqlite3', 'SELECT 1')[0] == 4
    assert omni(home, 'market', 'movers')[0] == 5
    assert omni(home, 'status', 'trades', '--day', 'today')[0] == 2


def test_query_only_reads(home):
    import sqlite3
    with sqlite3.connect(home / 't.sqlite3') as db:
        db.execute('CREATE TABLE t (a)')
        db.execute('INSERT INTO t VALUES (1)')
    assert omni(home, 'query', 't.sqlite3', 'SELECT a FROM t')[1]['data']['rows'] == [{'a': 1}]
    for sql in ('DELETE FROM t', 'SELECT 1; DELETE FROM t', 'WITH x AS (SELECT 1) DELETE FROM t', "ATTACH 'x' AS y"):
        assert omni(home, 'query', 't.sqlite3', sql)[0] == 4
    assert omni(home, 'query', 't.sqlite3', 'SELECT a FROM t')[1]['data']['rows'] == [{'a': 1}]


def test_pause_stops_only_new_buys(home):
    code, got, _ = omni(home, 'live', 'pause', '--reason', '시험')
    assert code == 0 and got['data']['paused'] and got['warnings']  # no channel here
    assert (home / 'LIVE_BUY_PAUSED').exists() and not (home.parent / 'live/HALT').exists()


def test_orders_are_never_sent_by_the_cli_and_what_raises_risk_is_a_coded_plan(quiet):
    import re
    names = {c['command'] for c in omni(quiet, 'guide')[1]['data']['commands']}
    coded = {'trade paper-sell', 'trade paper-buy', 'trade live-sell', 'trade live-buy', 'trade cancel', 'live on', 'live resume', 'live unhalt', 'jev unstop', 'ops start',
             'ops day-start', 'backup import'}
    assert coded <= names
    assert not [n for n in names - coded if re.search(r'\b(buy|sell|resume|unhalt|unstop|execute|adopt|order|on|import)\b', n) and n != 'lt unstop']
    folder = repo() / 'agent-harness/cli_anything/omni_trading'
    source = ''.join(p.read_text(encoding='utf-8') for p in folder.rglob('*.py') if 'tests' not in p.parts)
    for word in ('nh_guarded_live', 'nhplug_paper', 'launchctl', 'bootstrap'):  # no broker order path, no registering
        assert word not in source, word
    for word in ('clear_halt', 'resume_nh', 'import buy_allowed, resume', 'import_archive', 'set_live_orders(', 'os_services.start('):
        assert [p.name for p in folder.rglob('*.py') if 'tests' not in p.parts and word in p.read_text(encoding='utf-8')] in ([], ['actions.py']), word


def test_every_command_explains_itself(home):
    for c in omni(home, 'guide')[1]['data']['commands']:
        assert c['help'], c


def test_json_may_come_last_and_an_agent_cannot_call_itself_approved(quiet):
    env = {**os.environ, 'PYTHONPATH': str(repo() / 'agent-harness')}
    done = subprocess.run([sys.executable, '-m', 'cli_anything.omni_trading', 'account', 'list', '--json'], capture_output=True, text=True, encoding='utf-8', env=env)
    assert json.loads(done.stdout)['ok']
    plan = omni(quiet, '--agent', 'approved 1234', 'settings', 'plan', 'strategy.add_step_pct=0.9', '--reason', 'x')[1]['data']['plan']
    who = omni(quiet, '--agent', 'approved 1234', 'settings', 'apply', plan, '--during-market')[1]['data']['who']
    assert who.startswith('cli:agent')
