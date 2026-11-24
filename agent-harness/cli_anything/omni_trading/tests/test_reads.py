"""What a reader (a person or another agent) is told: the same values as the screen, unknown said as unknown, and clear refusals."""
import json
from datetime import datetime, timedelta

import pytest

from cli_anything.omni_trading.core import output as O
from cli_anything.omni_trading.core import reads

from .conftest import account, write
from .test_full_e2e import omni
from .test_more import data, run


def test_simulated_live_trades_are_marked(home, monkeypatch):
    row = {'time': '2026-11-12T10:00:00+09:00', 'side': 'BUY', 'symbol': '069500', 'qty': 1, 'price': 1}
    monkeypatch.setattr(reads, 'items', lambda nh: (None, [{'label': '실계좌 1', 'rows': [{**row, 'source': '가상(기본 손절 추정)'}]}]))
    code, got = run('status', 'trades', '--day', '2026-11-12')
    assert got['data']['trades'][0]['source'].startswith('가상') and any('추정' in w for w in got['warnings'])
    monkeypatch.setattr(reads, 'items', lambda nh: (None, [{'label': '실계좌 1', 'rows': [{**row, 'source': '실체결'}]}]))
    assert not run('status', 'trades', '--day', '2026-11-12')[1]['warnings']


def test_a_failed_check_is_a_failure(home, tmp_path):
    bad = tmp_path / 'x.md'
    bad.write_text('# 아무것도 아님\n', encoding='utf-8')
    code, got = run('template', 'startup', '--check', str(bad))
    assert code == 1 and got['ok'] is False


def test_a_yeo_book_s_savings_preview_counts_the_book_s_value(home, monkeypatch):
    import longterm_dca as D
    import longterm_dca_effective as E
    import vibe_longterm as L
    seen = {}
    monkeypatch.setattr(E, 'example_inputs', lambda root, book: ('correction', {}, {'NDX': 1.0}, ({'NDX': 1000}, 'x')))
    monkeypatch.setattr(L, 'yeo_settings', lambda saved, book: {'risk': 0.7})
    monkeypatch.setattr(D, 'effective', lambda *a, **k: seen.update(k) or {'fields': [], 'rows': [], 'notes': []})
    data('lt', 'dca-preview', 'live-2')
    assert seen['yeo'] is True and seen['cash'] == 1_000_000  # the live book's capital, as the screen passes it


def test_the_schedule_says_what_runs(home, monkeypatch):
    import automation_status
    monkeypatch.setattr(automation_status, 'processes', lambda *a, **k: {n: '종료' for n in automation_status.LABELS})
    rows = data('ops', 'schedule', '--day', '2026-11-25')['jobs']
    first = next(r for r in rows if '장전' in r['작업'])
    assert first['상태'] != '확인 불가' and 'frozenset' not in json.dumps(rows) and ':' in first['첫 시작']


def test_whether_a_service_is_armed_is_unknown_when_nobody_answers(home, monkeypatch):
    import os_services
    from cli_anything.omni_trading.core import approval as AP

    def unknown(*a, **k):
        raise os_services.Unknown('x')
    monkeypatch.setattr(os_services, 'registered_args', unknown)
    assert data('account', 'checklist', 'live-1')['conditions']['registered'] == '확인 불가'
    from .conftest import account, write
    write(home, {'paper-shared': account(False), 'live-1': account(live_orders=False)})
    assert '확인 불가' in json.dumps(AP.plan_action(home, 'live_on', 'x', account='live-1'), ensure_ascii=False)


def test_usage_errors_keep_the_envelope(home):
    for args in (['nope'], ['jev', 'events', '--limit', '999999'], ['settings', 'plan']):
        code, got, err = omni(home, *args)
        assert code == 2 and got and got['ok'] is False and got['error']['type'] == 'usage', args


@pytest.mark.parametrize('args', [('jev', 'events', '--day', '2026-13-45'), ('status', 'timeline', '--day', '2026-02-30'), ('ops', 'schedule', '--day', '2026-13-45'),
                                  ('journal', 'due', '--day', 'abc'), ('lt', 'audit-data', '--key', 'zzz'), ('compare', 'show', '--start', 'abc'), ('settings', 'history', '--since', 'x'),
                                  ('jev', 'explain', '--at', '13:00', '--since', '2026-11-18')])
def test_a_date_that_is_none_is_refused(home, args):
    code, got, _ = omni(home, *args)
    assert code == 2 and 'Traceback' not in json.dumps(got, ensure_ascii=False)


@pytest.mark.parametrize('args', [('live', 'fills', 'nope'), ('lt', 'log', '--kind', 'nope'), ('compare', 'show', '--book', 'nope'), ('settings', 'history', '--group', 'nope')])
def test_a_name_that_does_not_exist_is_refused(home, args):
    assert omni(home, *args)[0] in (2, 4), args


def test_settings_are_found_with_the_account_s_name_and_an_empty_search_says_so(home):
    found = data('settings', 'list', '--find', '운용금', '실계좌', '1')['settings']
    assert 'accounts.live-1.budget.capital_krw' in [s['setting'] for s in found]
    code, got = run('settings', 'list', '--find', '없는낱말')
    assert code == 0 and not got['data']['settings'] and got['warnings']


def test_guide_searches_the_group_s_words_too(home):
    assert 'market context' in [c['command'] for c in data('guide', '시황')['commands']]


def test_ages_are_readable_and_a_wal_counts_as_the_file(tmp_path):
    import os
    db = tmp_path / 'x.sqlite3'
    db.write_text('')
    old = datetime.now().timestamp() - 7200
    os.utime(db, (old, old))
    assert O.source(db)['age_s'] >= 7199 and O.age_text(O.source(db)['age_s']) == '2시간 전'
    (tmp_path / 'x.sqlite3-wal').write_text('')
    assert O.source(db)['age_s'] < 60
    assert [O.age_text(s) for s in (5, 130, 90_000)] == ['방금', '2분 전', '1일 전']


def test_text_rounds_numbers_and_stops_a_flood_but_json_keeps_everything(home):
    assert O.text({'a': 101975840.78875002, 'b': -0.00140635, 'c': True}) == 'a: 101,975,841\nb: -0.001406\nc: True'
    long = [{'x': 'y' * 100} for _ in range(400)]
    shown = O.shorten(O.text(long))
    assert len(shown) < O.TEXT_LIMIT + 200 and '--json' in shown
    assert O.shorten('짧음') == '짧음'


def test_the_strategy_that_drives_each_book_is_named(home, monkeypatch):
    import vibe_longterm as L
    monkeypatch.setattr(L, 'read_state', lambda root, keys: {'plan': {'day': '2026-11-24', 'regime': 'correction', 'active': ['dalio_rebalance']}, 'regime': {}})
    L.save_settings(home / 'vibe-longterm', {'books': {'actual': {'strategy': 'yeo_method'}}}, who='t')
    code, got = run('lt', 'today')
    assert got['data']['books']['actual'].startswith('여운봉') and any('여운봉' in w for w in got['warnings'])


def test_the_timeline_uses_the_screen_s_names_and_reasons(home, monkeypatch):
    import overview
    seen = {}
    monkeypatch.setattr(overview, 'timeline', lambda runtime, day, names, label=str: seen.update(names=names, label=label('jev_low_confidence')) or ([], {}))
    data('status', 'timeline')
    assert seen['names'].get('069500') and seen['label'] == 'Jev 신뢰도가 진입 기준 미만'


def test_only_the_asked_day_s_trades_and_orders_are_shown(home, monkeypatch):
    rows = [{'time': f'2026-11-{d}T10:00:00+09:00', 'side': 'BUY', 'symbol': '069500', 'qty': 1, 'price': 1, 'source': '실체결'} for d in (12, 13)]
    monkeypatch.setattr(reads, 'items', lambda nh: (None, [{'label': '실계좌 1', 'rows': rows}]))
    assert [t['time'][:10] for t in data('status', 'trades', '--day', '2026-11-12')['trades']] == ['2026-11-12']
    import longterm_evidence as EV
    import vibe_longterm as L
    with L.connect(home / 'vibe-longterm') as db:
        db.execute(EV.SCHEMA)
        for day in ('2026-11-12', '2026-11-13'):
            db.execute("INSERT INTO evidence (time, day, book, kind, key, code, side, qty, price) VALUES (?,?,?,?,?,?,?,?,?)",
                       (day + 'T10:00:00+09:00', day, 'actual', 'order', 'NDX', '379810', 'BUY', 1, 1))
        db.commit()
    assert [o['day'] for o in data('lt', 'orders', '--day', '2026-11-12')['orders']] == ['2026-11-12']
    assert len(data('lt', 'orders')['orders']) == 2


def test_an_old_file_is_marked_old(home):
    import os
    path = home / 'shared-etf-paper/movers.json'
    path.write_text('{"rows": {}}')
    assert not run('market', 'movers')[1]['warnings']
    os.utime(path, (1, 1))
    assert any('자료가' in w for w in run('market', 'movers')[1]['warnings'])


def test_account_digests_never_appear_in_the_history(home):
    import config_store
    config_store.record('accounts', {'live-1': {'hash': 'a' * 64, 'masked': '1', 'auto': True}}, {'live-1': {'hash': 'b' * 64, 'masked': '2', 'auto': False}})
    found = data('settings', 'history', '--limit', '50')['changes']
    assert len(found) == 1 and 'auto' in found[0]['setting']


def test_a_one_minute_chart_asks_for_a_real_code(home, monkeypatch):
    from cli_anything.omni_trading import more_commands as M
    asked = []
    class Read:
        def call_tool(self, name, args):
            asked.append(args['input']['iem_cd'])
            return {'status': 'ok', 'data': {'Output_1': [{'bsop_time': '090100', 'act_no': '1'}]}}
    monkeypatch.setattr(M, 'readonly', Read)
    assert run('market', 'bars', 'ABC')[0] == 2 and not asked
    assert 'act_no' not in data('market', 'bars', '005930')['bars'][0] and asked == ['005930']


def test_a_stored_journal_day_and_the_api_load_are_read(home):
    import trading_journal as J
    J.store({'day': '2026-11-20', 'summary': {'x': 1}, 'charts': {'y': 1}})
    assert data('journal', 'show', '--day', '2026-11-20')['summary'] == {'x': 1} and 'charts' not in data('journal', 'show', '--day', '2026-11-20')
    assert set(data('ops', 'api')) == {'findings', 'providers', 'blocked'}


@pytest.mark.parametrize('args', [('account', 'shadow'), ('jev', 'explain'), ('premarket', 'template')])
def test_no_record_yet_is_missing_not_a_crash(home, args):
    code, got = run(*args)
    assert code == (4 if args[0] == 'premarket' else 5) and 'Traceback' not in json.dumps(got)


def test_a_fall_may_be_written_with_its_minus(home, monkeypatch):
    from cli_anything.omni_trading import more_commands as M
    asked = []
    monkeypatch.setattr(M, 'run_script', lambda *a, **k: asked.append(a) or {})
    data('research', 'cases', '005930', '-6')
    assert asked == [('oversold_cases.py', '--similar', '005930', '-6.0')]


def test_no_report_is_made_up_for_a_day_without_a_stored_journal(home):
    assert run('journal', 'report', '--day', '2026-11-22')[0] == 5


def test_stored_times_are_shown_in_seoul_time(home):
    (home / 'news-cache.json').write_text(json.dumps({'fetched': '2026-11-24T18:55:14+09:00', 'error': None, 'rows': [
        {'title': 'x', 'published': '2026-11-24T09:34:15+00:00'}, {'title': 'y', 'published': 'soon'}]}))
    assert [r['published'] for r in data('market', 'news')['rows']] == ['2026-11-24T18:34:15+09:00', 'soon']
    folder = home / 'vibe-longterm/ndx-intraday'
    folder.mkdir()
    (folder / '2026-11-24.json').write_text(json.dumps({'checks': [{'at': '2026-11-24T14:30:00Z'}]}))
    assert data('market', 'ndx')['checks'][0]['at'] == '2026-11-24T23:30:00+09:00'


def test_one_day_of_live_fills(home, monkeypatch):
    import live_fills
    rows = [{'time': f'2026-11-{d}T10:00:00+09:00', 'symbol': '069500'} for d in (23, 24)]
    monkeypatch.setattr(live_fills, 'rows', lambda nh, key: rows)
    assert [r['time'][:10] for r in data('live', 'fills', 'live-1', '--day', '2026-11-23')['fills']] == ['2026-11-23']
    assert len(data('live', 'fills', 'live-1')['fills']) == 2


def test_the_balance_says_how_old_its_records_are(home):
    (home / 'live-balance-live-1.jsonl').write_text('')
    found = {s['path'] for s in run('status', 'balance')[1]['source']}
    assert {'account-snapshots.jsonl', 'live-balance-live-1.jsonl', 'longterm.sqlite3'} <= found


def test_a_history_cut_by_the_limit_says_there_is_more(home):
    import config_store
    config_store.record('prefs', {}, {f'k{i}': i for i in range(4)})
    assert any('더 있' in w for w in run('settings', 'history', '--limit', '2')[1]['warnings'])
    assert not run('settings', 'history', '--limit', '50')[1]['warnings']


def test_the_savings_preview_takes_the_settings_own_addresses_too(home, monkeypatch):
    import longterm_dca as D
    import longterm_dca_effective as E
    seen = []
    monkeypatch.setattr(E, 'example_inputs', lambda root, book: ('bull', {}, {'NDX': 1.0}, ({}, 'x')))
    monkeypatch.setattr(D, 'effective', lambda cfg, *a, **k: seen.append(cfg['amount_krw']) or {'fields': [], 'rows': [], 'notes': []})
    from cli_anything.omni_trading import more_commands as M
    import vibe_longterm as L
    monkeypatch.setattr(M, 'engine_state', lambda: (L, {}))
    data('lt', 'dca-preview', 'actual', 'dca.actual.amount_krw=300000')
    data('lt', 'dca-preview', 'actual', 'amount_krw=200000')
    assert seen == [300_000, 200_000]


# ---- 2026-11-24 가이드 검수 ----

def test_a_clock_time_means_today(home, monkeypatch):
    from cli_anything.omni_trading import omni_trading_cli as C
    (home / 'shared-etf-paper/market-research.sqlite3').write_text('')
    asked = []
    monkeypatch.setattr(C, 'run_script', lambda name, *a, **k: asked.append(a) or {})
    data('jev', 'explain', '--at', '10:30')
    data('jev', 'explain', '--at', '2026-11-20T10:30')
    assert asked == [('--json', '--at', reads.today() + 'T10:30'), ('--json', '--at', '2026-11-20T10:30')]
    assert run('jev', 'explain', '--at', '1030')[0] == 2


def test_a_strategy_switch_takes_on_and_off_like_every_other_switch(quiet):
    import strategy_settings as ST
    key = sorted(ST.SWITCH_KEYS)[0]
    now = data('settings', 'get', f'strategy.{key}')['settings'][0]['now']
    made = data('settings', 'plan', f"strategy.{key}={'off' if now else 'on'}", '--reason', 'x')
    assert made['changes'][0]['after'] == (0.0 if now else 1.0)


def test_events_are_found_by_the_strength_reason_too(home):
    stamp = reads.today() + 'T10:00:00+09:00'
    rows = [{'time': stamp, 'event': 'v2_entry_gate', 'reason': 'jev_wait', 'strength_reason': 'trend_not_rising'}, {'time': stamp, 'event': 'x', 'reason': 'y'}]
    (home / 'shared-etf-paper/events.jsonl').write_text('\n'.join(map(json.dumps, rows)))
    found = data('jev', 'events', '--reason', 'trend_not_rising')['events']
    assert len(found) == 1 and found[0]['strength_reason'] == 'trend_not_rising'


def test_a_check_with_problems_is_a_failure_that_says_so(home):
    code, got = run('lt', 'audit-check', '--key', '2026-11-20')
    assert (code, got['ok'], got['data']['ok']) == (1, False, False) and got['error']['message']


def test_turning_on_a_phrase_switch_is_explained_with_its_real_level(home):
    write(home, {'paper-shared': account(False), 'live-1': account(live_orders=False)})
    assert data('settings', 'explain', 'lt.execute')['level'] == 'H'


def test_a_long_term_stop_says_what_it_does_not_stop(home):
    from cli_anything.omni_trading.core.actions import ACTIONS
    assert '실계좌' in ACTIONS['lt_stop'].title
