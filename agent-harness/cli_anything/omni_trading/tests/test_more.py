"""The commands that compute, read a broker, or write a file of their own; no network and nothing of the real runtime."""
import json
import re
from datetime import datetime

import pytest
from click.testing import CliRunner

from cli_anything.omni_trading import more_commands as M
from cli_anything.omni_trading.core import output as O
from cli_anything.omni_trading.omni_trading_cli import cli

from .test_full_e2e import omni



def run(*args):
    done = CliRunner().invoke(cli, ['--json', *args])
    lines = [ln for ln in done.output.splitlines() if ln.startswith('{"ok"')]
    return done.exit_code, json.loads(lines[-1]) if lines else {'error': {'message': done.output}}


def data(*args):
    code, got = run(*args)
    assert code == 0, got
    return got['data']


EXTERNAL = {'repl', 'market quote', 'market brief', 'market bars', 'market refresh', 'premarket preflight', 'status refresh', 'live balance', 'market investors', 'lt chart',
            'jev chart', 'journal rebuild', 'backup create', 'backup list', 'backup verify', 'lt report-data', 'lt audit-data', 'journal improvement',
            'ops notify-preview', 'ops doctor', 'research replay', 'lt cases-refresh', 'journal merge', 'ops audit', 'backup tasks', 'trade live-sell', 'trade live-buy', 'backup import'}
GIVEN = {'account show': ['live-1'], 'account match': ['live-1', '--reason', 'x'], 'account quote': ['live-1', '1000', '3'], 'account checklist': ['live-1'],
         'live log': ['live-1'], 'live fills': ['live-1'], 'live longterm': ['live-2'], 'live pause': ['--reason', 'x'], 'live off': ['live-1', '--reason', 'x'],
         'live halt': ['--reason', 'x'], 'report read': ['journal', 'x'], 'report preview': ['journal', 'x.md', '--out', 'x'], 'report check-page': ['x.html'],
         'trade paper-sell': ['069500', '--qty', '1', '--reason', 'x'], 'trade paper-buy': ['069500', '--qty', '1', '--reason', 'x'],
         'trade live-sell': ['live-1', '069500', '--qty', '1', '--reason', 'x'], 'trade live-buy': ['live-1', '069500', '--qty', '1', '--reason', 'x'],
         'live on': ['live-1', '--reason', 'x'], 'backup import': ['x', '--reason', 'x'], 'trade cancel': ['paper', '069500', '--reason', 'x'],
         **{c: ['--reason', 'x'] for c in ('jev stop', 'jev unstop', 'live resume', 'live unhalt', 'jev restart', 'lt stop', 'lt unstop', 'ops day-start')}, 'ops start': ['market-movers', '--reason', 'x'],
         'report publish': ['journal', 'x', '--reason', 'x'], 'report send': ['journal', 'x', '--reason', 'x'], 'ops notify-test': ['--reason', 'x'],
         'ai search': ['x'], 'ai ask': ['x'], 'research replay': ['yeo'], 'journal merge': ['--day', '2026-11-23'], 'plan show': ['x'], 'plan drop': ['x'], 'plan approve': ['x'], 'plan apply': ['x'],
         'ops logs': ['preflight'], 'ops service-files': ['--system', 'linux', '--out', '/nonexistent/x'], 'query': ['x.sqlite3', 'SELECT 1'], 'engine': ['off', '--reason', 'x'], 'journal check': ['2026-11-23'],
         'compare export': ['--out', '/nonexistent/x.csv'], 'research cases': ['005930', '5'], 'ai local': ['날씨'], 'ai talk': ['1'],
         'template startup': ['--blank'], 'settings get': ['strategy.add_step_pct'], 'settings explain': ['strategy.add_step_pct'],
         'settings plan': ['strategy.add_step_pct=0.9', '--reason', 'x'], 'settings preset': ['volume', '엄격', '--reason', 'x'], 'settings show-plan': ['x'],
         'settings approve': ['x'], 'settings apply': ['x'], 'settings revert': ['1', '--reason', 'x'],
         'settings list-edit': ['watch', 'add', '005930', '--reason', 'x'],
         'schedule export': ['--out', '/nonexistent/schedules.json'], 'schedule plan': ['--to', 'codex:work', '--all'],
         'schedule profile-add': ['invalid', 'work', '--model', 'example'], 'schedule probe': ['codex:work'],
         'schedule validate-model': ['schedule-123456abcdef', 'sample', '--fixture', 'fixture.json'],
         'schedule verify': ['schedule-123456abcdef'], 'schedule rollback': ['schedule-123456abcdef'],
         'schedule handoff': ['--to-device', 'invalid', '--job', 'sample', '--out', '/nonexistent/receipt.json'],
         'schedule receipt-export': ['invalid', '--out', '/nonexistent/receipt.json']}


def test_every_command_answers_in_the_envelope_on_an_empty_machine(home):
    names = [c['command'] for c in data('guide')['commands']]
    assert len(names) >= 120
    for name in names:
        if name in EXTERNAL:
            continue
        code, got = run(*name.split(), *GIVEN.get(name, []))
        assert set(got) >= {'ok', 'command', 'code', 'time', 'data', 'source', 'warnings'}, (name, got)
        found = name.endswith('-check') and got.get('data')  # a check that found problems answers 1, and says which
        assert got['code'] == code and (code in (0, 2, 3, 4, 5, 6) or found) and 'Traceback' not in json.dumps(got), (name, got)
        assert 'h' * 64 not in json.dumps(got), name


def test_files_are_new_and_outside_the_runtime(home, tmp_path):
    (tmp_path / 'old.csv').write_text('x')
    for path in (tmp_path / 'old.csv', tmp_path / 'none/x.csv', home / 'x.csv', home.parent / 'cli' / '..' / 'nhplug' / 'y.csv'):
        with pytest.raises(O.Refused):
            M.new_file(path)
    assert M.new_file(tmp_path / 'new.csv') == (tmp_path / 'new.csv').resolve()


def longterm(home, **plan):
    import vibe_longterm as L
    with L.connect(home / 'vibe-longterm') as db:
        L.put_state(db, plan={'day': '2026-11-24', 'regime': 'correction', 'active': ['bogle_core'],
                              'indicators': {'dd': -0.1, 'day_ret': 0.0, 'vix': 20, 'gap': 0.0, 'sats': {}, 'ath': {'ndx': {'dd': -0.14, 'peak': 30000}}}, **plan})
        db.commit()


def test_preview_uses_the_asked_numbers_and_saves_nothing(quiet):
    import vibe_longterm as L
    longterm(quiet)
    before = L.load_settings(quiet / 'vibe-longterm')
    rows = {r['종목']: r for r in data('lt', 'preview', 'lt.weights.NDX=0.25', 'lt.weights.SPX=0.35', 'lt.modules.lynch_story=off')['targets']}
    assert rows['KODEX 미국나스닥100']['오늘 목표(저장)'] == '30.0%' and rows['KODEX 미국나스닥100']['오늘 목표(바꾸면)'] == '25.0%'
    assert run('lt', 'preview', 'lt.weights.NDX=0.5')[0] == 4
    assert run('lt', 'preview', 'lt.tuning.band=0.06')[0] == 4
    assert run('lt', 'preview', 'lt.params.yeo_reservoir.step_2=-0.15')[0] == 4
    assert L.load_settings(quiet / 'vibe-longterm') == before and not (quiet / 'settings-history.sqlite3').exists()


def test_dca_preview_follows_the_asked_amount_and_saves_nothing(quiet):
    import vibe_longterm as L
    longterm(quiet)
    before = L.load_settings(quiet / 'vibe-longterm')['dca']
    got = data('lt', 'dca-preview', 'actual', 'amount_krw=300000', 'tune.rate=0.005')
    assert got['book'] == 'actual' and got['regime'] == 'correction' and any('300,000' in n for n in got['notes'] + [f['value'] for f in got['fields']])
    assert run('lt', 'dca-preview', 'nobody')[0] == 4 and run('lt', 'dca-preview', 'actual', 'amount_krw=1')[0] == 4
    assert L.load_settings(quiet / 'vibe-longterm')['dca'] == before


def test_quote_is_the_mirrors_own_calculation(home):
    from nh_live_follow import follow_quote
    import accounts as A
    got = data('account', 'quote', 'live-1', '5000', '20')
    assert got['share']['shares'] == follow_quote(A.load(home / 'accounts.json')['live-1'], 5000, 20, A.load(home / 'accounts.json')['paper-shared'])['share'][0] == 1
    assert run('account', 'quote', 'live-2', '5000', '20')[0] == 4
    assert run('account', 'quote', 'paper-shared', '5000', '20')[0] == 4


def test_checklist_tells_each_condition(home, monkeypatch):
    import os_services
    monkeypatch.setattr(os_services, 'registered_args', lambda service, folder=None: ['x', '--execute'] if service == 'live-follow-1' else None)
    got = data('account', 'checklist', 'live-1')
    assert got == {'service': 'live-follow-1', 'conditions': {'registered': True, 'registered_for_real_orders': True, 'auto': True, 'live_orders': True, 'halt_off': True}}
    assert data('account', 'checklist', 'live-2')['conditions']['registered'] is False


def test_a_broker_failure_never_shows_its_message(home, monkeypatch):
    import nh_live_broker
    monkeypatch.setattr(M, 'readonly', lambda: object())
    def fail(*a):
        raise ValueError('account 123-45-678901 refused')
    monkeypatch.setattr(nh_live_broker, 'read_linked_balance', fail)
    for args in (('live', 'balance', 'live-1'), ('status', 'refresh')):
        code, got = run(*args)
        assert code == 6 and '678901' not in json.dumps(got)


def test_live_balance_shows_only_chosen_fields(home, monkeypatch):
    import nh_live_broker
    monkeypatch.setattr(M, 'readonly', lambda: object())
    totals = {k: 1 for k in nh_live_broker.BALANCE_TOTALS}
    answer = {'Output_0': {**totals, 'act_no': '12345678901', 'orr_pbl_amt1': 10}, 'Output_1': [{'iem_cd': '005930', 'rsdl_qty': 1, 'act_no': '12345678901'}, {'iem_cd': '1', 'rsdl_qty': 0}]}
    monkeypatch.setattr(nh_live_broker, 'read_linked_balance', lambda *a: (answer, datetime(2026, 11, 24, tzinfo=O.KST)))
    got = data('live', 'balance', 'live-1')
    assert got['holdings'] == [{'iem_cd': '005930', 'rsdl_qty': 1}] and '12345678901' not in json.dumps(got)


def test_only_the_readonly_connection_is_used():
    from pathlib import Path
    from cli_anything.omni_trading.utils import omni_backend
    here = Path(M.__file__).parent
    uses = {p.name for p in here.rglob('*.py') if 'tests' not in p.parts and 'mcp_servers' in p.read_text(encoding='utf-8')}
    assert uses == {Path(omni_backend.__file__).name}


def test_a_connection_that_could_order_is_refused(home, monkeypatch):
    import nh_live_broker
    from cli_anything.omni_trading.core import reads
    def refused(adapter):
        raise ValueError('can order')
    monkeypatch.setattr(nh_live_broker, 'require_readonly', refused)
    import src.config.loader as loader
    import src.tools.mcp as mcp
    monkeypatch.setattr(loader, 'load_agent_config', lambda: type('C', (), {'mcp_servers': {'nhplug_readonly': None}}))
    monkeypatch.setattr(mcp, 'MCPServerAdapter', lambda *a: pytest.fail('called') if len(a) != 2 else object())
    with pytest.raises(ValueError, match='can order'):
        reads.quote('005930')


def test_a_quote_only_asks_for_the_price(home, monkeypatch):
    from cli_anything.omni_trading.core import reads
    from cli_anything.omni_trading.utils import omni_backend
    asked = []
    class Read:
        def call_tool(self, name, args):
            asked.append((name, args))
            return {'status': 'ok', 'data': {'Output_0': {'now_pr': '1'}}}
    monkeypatch.setattr(omni_backend, 'readonly', Read)
    assert reads.quote('005930') == {'now_pr': '1'} and asked == [('get_stock_price', {'stock_code': '005930'})]


def test_backup_create_waits_an_hour(home, monkeypatch, tmp_path):
    import state_sync
    monkeypatch.setattr(state_sync, 'STATE', tmp_path / 'state')
    (tmp_path / 'state/backups').mkdir(parents=True)
    first = data('backup', 'create')
    assert first['name'] in [b['name'] for b in data('backup', 'list')['backups']] and data('backup', 'verify', first['name'])['ok']
    assert run('backup', 'create')[0] == 4
    assert run('backup', 'verify', '../x.tar.gz')[0] == 5


def test_journal_rebuild_refuses_weekends_the_future_and_unknown_days(home):
    for day, want in (('2026-11-22', 4), ('2099-01-05', 4), ('2026-11-03', 5), ('x', 2)):
        assert run('journal', 'rebuild', '--day', day)[0] == want


# --- what another agent could not do in the ten-request trial (2026-11-24) ------------------------------------------------

def test_settings_are_found_by_the_words_a_person_uses(home):
    for words, address in ((['종목당', '최대'], 'accounts.paper-shared.budget.per_symbol_krw'), (['분할', '1회', '매수', '금액(원)'], 'accounts.paper-shared.budget.first_buy_krw'),
                           (['prefs'], 'prefs.theme'), (['시작', '탭'], 'prefs.start_tab'), (['1회', '적립', '금액'], 'dca.actual.amount_krw')):
        assert address in [s['setting'] for s in data('settings', 'list', '--find', *words)['settings']], words
    raw = [s['setting'] for s in data('settings', 'list')['settings'] if s['label'].split(': ')[-1] == s['setting'].rsplit('.', 1)[-1]]
    assert not raw  # no setting is named by its variable


def test_the_day_s_block_reasons_are_counted_without_a_row_limit(home):
    from cli_anything.omni_trading.core.reads import today
    rows = [{'time': f'{today()}T10:00:00+09:00', 'event': 'v2_entry_gate', 'permitted': False, 'reason': 'jev_wait'}] * 600
    rows += [{'time': f'{today()}T11:00:00+09:00', 'event': 'v2_entry_gate', 'permitted': False, 'reason': 'x', 'strength_reason': 'held'},
             {'time': f'{today()}T11:01:00+09:00', 'event': 'v2_entry_gate', 'permitted': True, 'reason': 'entry_conditions_met'}]
    (home / 'shared-etf-paper/events.jsonl').write_text('\n'.join(map(json.dumps, rows)) + '\n')
    got = data('jev', 'blocks')
    assert got['blocked_cycles'] == 601 and [(r['reason'], r['cycles']) for r in got['reasons']] == [('jev_wait', 600), ('x', 1)]
    assert got['strength_candidates'][0]['reason'] == 'held'


def test_a_savings_plan_that_is_off_has_no_next_day(home):
    got = data('lt', 'dca-preview', 'live-2')
    assert got['on'] is False and got['next_day'] is None and '꺼져' in got['off_note']
    on = data('lt', 'dca-preview', 'live-2', 'on=true')
    assert on['next_day'] and 'off_note' not in on


def test_guide_takes_several_words_and_says_when_nothing_matches(home):
    assert 'jev blocks' in [c['command'] for c in data('guide', '차단', '사유')['commands']]
    code, got = run('guide', '없는낱말')
    assert code == 0 and not got['data']['commands'] and got['warnings']


def test_the_agent_of_the_plan_is_in_the_history_and_the_live_effect_is_in_words(home):
    _, planned, _ = omni(home, '--agent', 'lab', 'settings', 'plan', 'prefs.status_blink=off', '--reason', 'x')
    _, got, _ = omni(home, 'settings', 'apply', planned['data']['plan'])
    assert got['data']['who'].startswith('cli:lab ')
    _, planned, _ = omni(home, 'settings', 'plan', 'accounts.paper-shared.budget.first_buy_krw=800000', '--reason', 'x')
    assert planned['data']['level'] == 'H' and any('live-1' in line for line in planned['data']['how'])


def test_services_say_unknown_instead_of_stopped_and_definitions_go_to_a_new_folder(home, monkeypatch, tmp_path):
    import os_services
    monkeypatch.setattr(os_services, 'states', lambda: {'shared-etf-paper': {'registered': True, 'running': True, 'pid': 7, 'last_exit': 0, 'started': None}})
    rows = {r['service']: r for r in data('ops', 'services')['services']}
    assert rows['shared-etf-paper']['running'] is True and rows['preflight']['registered'] is False

    def unknown():
        raise os_services.Unknown('x')
    monkeypatch.setattr(os_services, 'states', unknown)
    assert run('ops', 'services')[0] == 6
    made = data('ops', 'service-files', '--system', 'windows', '--out', str(tmp_path / 'win'))
    assert 'com.vibetrading.preflight.xml' in made['files']
    assert run('ops', 'service-files', '--system', 'windows', '--out', str(tmp_path / 'win'))[0] == 4
