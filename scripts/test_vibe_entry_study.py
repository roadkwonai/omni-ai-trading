from copy import deepcopy
import hashlib
import json
import sqlite3

import pytest

from vibe_entry_study import analyze, load_and_analyze


DAY = '2026-11-16'


def data():
    advice = [{'id': 'a', 'time': f'{DAY}T11:55:00+09:00',
               'technical_basic': {'100790': {'as_of': '11:52', 'range_position_pct': 92, 'rsi': 74.1, 'vs_vwap_pct': 3.42}},
               'decisions': [{'action': 'BUY', 'symbol': '100790', 'route': 'strength'}]}]
    executions = [('a', 0, 'buy_submitted', f'{DAY}T11:56:29+09:00')]
    events = [{'event': 'submitted', 'symbol': '100790', 'source': 'vibe-trading', 'qty': 10, 'time': f'{DAY}T11:56:28+09:00',
               'decision_available_at': f'{DAY}T11:56:20+09:00', 'advice_id': 'a', 'decision_idx': 0, 'cycle_id': 'c'}]
    fills = [('b', '100790', 'BUY', 10, 100, f'{DAY}T11:57:00+09:00'),
             ('s', '100790', 'SELL', 10, 98, f'{DAY}T12:00:00+09:00')]
    intents = [('b', '100790', 'BUY', 10, 'terminal')]
    links = [('b', 'c')]
    return advice, executions, events, fills, intents, links


def run(parts):
    return analyze(*parts, DAY)


def test_explicit_evidence_single_entry_closed_and_selection_bias():
    report = run(data())
    row = report['decisions'][0]
    assert row['status'] == 'closed_single_entry'
    assert row['evidence'] == 'explicit'
    assert row['realized_before_costs'] == -20
    assert row['technical_as_of'].endswith('11:52:00+09:00')
    assert row['decision_available_at'].endswith('11:56:20+09:00')
    assert report['groups']['range_ge_90']['verified_closed'] == 1
    assert '사후 선정' in report['hypothesis_selection']
    assert '비용' in report['limitations']


def test_historical_temporal_coincidence_is_not_proof():
    parts = data()
    del parts[2][0]['advice_id']
    report = run(parts)
    assert report['decisions'][0]['status'] == 'association_unverified'
    assert report['groups']['range_ge_90']['realized_before_costs'] is None


def test_future_or_missing_available_time_cannot_create_outcome():
    for available in (None, f'{DAY}T11:56:29+09:00', f'{DAY}T11:54:59+09:00'):
        parts = data()
        parts[2][0]['decision_available_at'] = available
        assert run(parts)['decisions'][0]['status'] == 'explicit_link_incomplete'


def test_same_id_advice_available_at_proves_publication_before_submit():
    parts = data()
    parts[2][0].pop('decision_available_at')
    parts[0][0]['available_at'] = f'{DAY}T11:56:20+09:00'
    assert run(parts)['decisions'][0]['status'] == 'closed_single_entry'
    parts[0][0]['available_at'] = f'{DAY}T11:56:30+09:00'
    assert run(parts)['decisions'][0]['status'] == 'explicit_link_incomplete'


def test_future_technical_bar_and_bad_range_are_unavailable():
    for asof, position in (('11:56', 92), ('11:52', 101), ('11:52', True), (None, 92)):
        parts = data()
        parts[0][0]['technical_basic']['100790'].update(as_of=asof, range_position_pct=position)
        row = run(parts)['decisions'][0]
        assert row['hypothesis_group'] == 'technical_unavailable'


def test_partial_or_unfilled_never_count_as_complete():
    parts = data()
    parts[4][0] = ('b', '100790', 'BUY', 11, 'terminal')
    parts[2][0]['qty'] = 11
    assert run(parts)['decisions'][0]['status'] == 'partial_or_unresolved'
    parts = data()
    parts[3][:] = []
    assert run(parts)['decisions'][0]['status'] == 'terminal_no_confirmed_fill'
    parts[4][0] = ('b', '100790', 'BUY', 10, 'acknowledged')
    assert run(parts)['decisions'][0]['status'] == 'unresolved_no_confirmed_fill'
    parts = data()
    parts[4][0] = ('b', '100790', 'BUY', 10, 'acknowledged')
    assert run(parts)['decisions'][0]['status'] == 'partial_or_unresolved'


def test_multiple_buys_in_position_cannot_be_attributed():
    parts = data()
    parts[3][:] = [('b', '100790', 'BUY', 10, 100, f'{DAY}T11:57:00+09:00'),
                   ('b2', '100790', 'BUY', 2, 101, f'{DAY}T11:58:00+09:00'),
                   ('s', '100790', 'SELL', 12, 98, f'{DAY}T12:00:00+09:00')]
    assert run(parts)['decisions'][0]['status'] == 'multiple_entries_unattributable'


def test_duplicates_and_invalid_sequence_fail_closed():
    parts = data()
    parts[2].append(deepcopy(parts[2][0]))
    assert run(parts)['decisions'][0]['status'] == 'duplicate_or_ambiguous'
    parts = data()
    parts[3][1] = ('s', '100790', 'SELL', 11, 98, f'{DAY}T12:00:00+09:00')
    with pytest.raises(ValueError, match='sell_exceeds_position'):
        run(parts)


def test_missing_execution_and_control_group_are_distinct():
    parts = data()
    parts[0][0]['technical_basic']['100790']['range_position_pct'] = 50
    parts[1].clear()
    row = run(parts)['decisions'][0]
    assert row['hypothesis_group'] == 'range_lt_90'
    assert row['execution_outcome'] == 'no_execution_record'
    assert row['status'] == 'explicit_link_incomplete'


def test_market_regime_is_bound_to_digest_and_prepublication_timestamp():
    parts = data()
    context = {'time': f'{DAY}T11:55:00+09:00', 'market_regime_themes_global': {
        'as_of': f'{DAY}T11:52', '코스피': {'regime': '하락장'}, '코스닥': {'regime': '하락장'}}}
    inputs = json.dumps({'context': context}, ensure_ascii=False, sort_keys=True)
    response = json.dumps({'scores': []}, ensure_ascii=False, sort_keys=True)
    parts[0][0]['input_digest'] = hashlib.sha256(f'vibe:scores:{inputs}:{response}'.encode()).hexdigest()
    parts[0][0]['_study_model_input'] = {'stored_at': f'{DAY}T11:56:10+09:00', 'inputs': inputs, 'response': response}
    row = run(parts)['decisions'][0]
    assert row['market_context']['status'] == 'ok'
    assert row['market_context']['regimes']['코스닥'] == '하락장'
    assert row['status'] == 'closed_single_entry'
    parts[0][0]['_study_model_input']['stored_at'] = f'{DAY}T11:56:21+09:00'
    assert run(parts)['decisions'][0]['status'] == 'explicit_link_incomplete'
    parts[0][0]['_study_model_input']['inputs'] += ' '
    assert run(parts)['decisions'][0]['market_context']['status'] == 'unavailable'


def test_wrong_source_or_quantity_never_joins():
    parts = data()
    parts[2][0]['source'] = 'oversold_v2'
    assert run(parts)['decisions'][0]['status'] == 'explicit_link_incomplete'
    parts = data()
    parts[2][0]['qty'] = 11
    assert run(parts)['decisions'][0]['status'] == 'intent_unverified'


def test_file_integration_reads_only_and_no_event_fabrication(tmp_path):
    shared, ledger = tmp_path / 'shared-etf-paper', tmp_path / 'vibe-paper'
    shared.mkdir(); ledger.mkdir()
    advice, executions, events, fills, intents, links = data()
    research = shared / 'market-research.sqlite3'
    with sqlite3.connect(research) as db:
        db.execute('CREATE TABLE snapshots(id INTEGER PRIMARY KEY,kind TEXT,time TEXT,payload TEXT)')
        db.execute('CREATE TABLE model_inputs(digest TEXT, time TEXT, engine TEXT, question TEXT, inputs TEXT, response TEXT)')
        db.execute('INSERT INTO snapshots(kind,time,payload) VALUES (?,?,?)', ('vibe_advice', advice[0]['time'], json.dumps(advice[0])))
    orderdb = ledger / 'orders.sqlite3'
    with sqlite3.connect(orderdb) as db:
        db.execute('CREATE TABLE fills(order_id TEXT,symbol TEXT,side TEXT,quantity INTEGER,price REAL,time TEXT)')
        db.execute('CREATE TABLE advice_executions(advice_id TEXT,idx INTEGER,outcome TEXT,time TEXT)')
        db.execute('CREATE TABLE decision_order_links(order_id TEXT,cycle_id TEXT)')
        db.execute('CREATE TABLE intents(id TEXT,symbol TEXT,side TEXT,qty INTEGER,status TEXT)')
        db.executemany('INSERT INTO fills VALUES(?,?,?,?,?,?)', fills)
        db.executemany('INSERT INTO advice_executions VALUES(?,?,?,?)', executions)
        db.executemany('INSERT INTO decision_order_links VALUES(?,?)', links)
        db.executemany('INSERT INTO intents VALUES(?,?,?,?,?)', intents)
    logfile = ledger / 'events.jsonl'
    noise = {'event': 'submitted', 'time': f'{DAY}T11:00:00+09:00',
             'advice_id': 'unrelated', 'decision_idx': 0, 'payload': 'x' * 200}
    logfile.write_text((json.dumps(noise) + '\n') * 1000 + json.dumps(events[0], ensure_ascii=False) + '\n')
    before = [p.read_bytes() for p in (research, orderdb, logfile)]
    report = load_and_analyze(tmp_path, DAY, ledger)
    assert report['decisions'][0]['status'] == 'closed_single_entry'
    assert [p.read_bytes() for p in (research, orderdb, logfile)] == before
    with logfile.open('a') as stream:
        stream.write(json.dumps(events[0]) + '\n')
    assert load_and_analyze(tmp_path, DAY, ledger)['decisions'][0]['status'] == 'duplicate_or_ambiguous'
    with logfile.open('a') as stream:
        stream.write(f'{{"event":"submitted","time":"{DAY}",\n')
    with pytest.raises(ValueError):
        load_and_analyze(tmp_path, DAY, ledger)
    logfile.unlink()
    with pytest.raises(OSError):
        load_and_analyze(tmp_path, DAY, ledger)
