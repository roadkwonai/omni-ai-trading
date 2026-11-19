import json
import sqlite3

import accounts
from test_vibe_fast_exit_sim import event_fixture
from vibe_event_daily import build, run

DAY = '2026-11-16'


def setup(tmp_path):
    source, ledger, events = event_fixture(tmp_path)
    root = tmp_path / 'nhplug'
    observer = root / 'vibe-fast-exit-shadow'
    observer.mkdir(parents=True)
    engine = root / 'vibe-paper'
    engine.mkdir()
    event_dir = root / 'vibe-event-analysis'
    event_dir.mkdir()
    (root / 'accounts.json').write_text(json.dumps(accounts.DEFAULTS))
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    for row in rows:
        row['latest_fill_id'] = 'entry'
    rows[0]['event_trigger'] = {'triggered': True}
    obs = observer / f'observations-{DAY}.jsonl'
    obs.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    ledger.rename(engine / 'orders.sqlite3')
    with sqlite3.connect(events) as db:
        old = db.execute('SELECT id,status,result FROM attempts').fetchall()
        db.execute('DROP TABLE attempts')
        db.execute('CREATE TABLE attempts(id TEXT,day TEXT,symbol TEXT,time TEXT,status TEXT,result TEXT)')
        db.executemany('INSERT INTO attempts VALUES (?,?,?,?,?,?)',
                       [(ident, DAY, '100790', f'{DAY}T12:30:00+09:00', status, result)
                        for ident, status, result in old])
    events.rename(event_dir / 'events.sqlite3')
    return root


def test_daily_replay_is_separate_idempotent_and_read_only(tmp_path):
    root = setup(tmp_path)
    paths = [root / 'vibe-paper/orders.sqlite3', root / 'vibe-event-analysis/events.sqlite3',
             root / f'vibe-fast-exit-shadow/observations-{DAY}.jsonl', root / 'accounts.json']
    before = [path.read_bytes() for path in paths]
    first, second = run(root, DAY), run(root, DAY)
    assert first == second and first['status'] == 'ok'
    assert first['ledger'] == 'vibe-paper' and first['recorded_triggers'] == first['recorded_attempts'] == 1
    assert len(first['event_simulation']) == 1
    assert '가상 체결 가정' in first['limitations'] and '확인되지' in first['worker_coverage']
    assert [path.read_bytes() for path in paths] == before
    saved = root / f'vibe-fast-exit-shadow/reviews/event-replay-{DAY}.json'
    assert json.loads(saved.read_text()) == first


def test_daily_report_distinguishes_jev_gpt_and_unknown_costs(tmp_path):
    root = setup(tmp_path)
    with sqlite3.connect(root / 'vibe-event-analysis/events.sqlite3') as db:
        record = json.loads(db.execute('SELECT result FROM attempts').fetchone()[0])
        record['source'] = 'jev_event'
        record['api_calls'] = 1
        record['analysis'].update(confidence=0.9, sell_confidence=0.7)
        record['model_evidence'] = {'choice': 'SELL', 'confidence': 0.9, 'sell_confidence': 0.7}
        db.execute('UPDATE attempts SET result=?', (json.dumps(record),))
        gpt = {**record, 'source': 'gpt'}
        db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?)', ('gpt2', DAY, '100790',
                   f'{DAY}T12:30:01+09:00', 'completed', json.dumps(gpt)))
    result = build(root, DAY)
    assert result['status'] == 'ok'
    assert result['event_sources']['jev_event']['completed'] == 1
    assert result['event_sources']['gpt']['completed'] == 1
    assert result['event_simulation'][0]['decision_source'] == 'jev_event'
    assert result['event_sources']['jev_event']['api_cost'] == 'unknown'
    assert result['event_api_calls']['known_attempts'] == 1
    assert result['event_api_calls']['unknown_attempts'] == 1


def test_timeout_with_missing_result_counts_as_unknown_attempt_not_zero_jev_calls(tmp_path):
    root = setup(tmp_path)
    with sqlite3.connect(root / 'vibe-event-analysis/events.sqlite3') as db:
        db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?)', ('timeout', DAY, '100790',
                   f'{DAY}T12:30:01+09:00', 'model_timeout', None))
    result = build(root, DAY)
    assert result['status'] == 'ok' and result['recorded_attempts'] == 2
    assert result['event_sources']['unknown']['attempts'] == 2
    assert result['event_sources']['unknown']['completed'] == 1
    assert result['event_sources']['jev_event']['attempts'] == 0
    assert result['event_sources']['jev_event']['api_calls'] == 'unknown'
    assert result['event_sources']['unknown']['api_cost'] == 'unknown'


def test_missing_event_db_is_unavailable_and_previous_report_preserved(tmp_path):
    root = setup(tmp_path)
    ok = run(root, DAY)
    saved = root / f'vibe-fast-exit-shadow/reviews/event-replay-{DAY}.json'
    (root / 'vibe-event-analysis/events.sqlite3').unlink()
    result = run(root, DAY)
    assert result['status'] == 'unavailable' and result['reason'] == 'event_database_unavailable'
    assert result['previous_report_preserved'] is True and json.loads(saved.read_text()) == ok


def test_ledger_mismatch_and_missing_ledger_are_not_empty_trading(tmp_path):
    root = setup(tmp_path)
    path = root / 'vibe-paper/orders.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute("UPDATE fills SET quantity=9 WHERE order_id='entry'")
    assert build(root, DAY)['reason'] == 'engine_ledger_identity_unverified'
    path.unlink()
    assert build(root, DAY)['reason'] == 'engine_ledger_unavailable'


def test_other_day_attempts_do_not_leak_into_replay(tmp_path):
    root = setup(tmp_path)
    with sqlite3.connect(root / 'vibe-event-analysis/events.sqlite3') as db:
        row = db.execute('SELECT result FROM attempts').fetchone()[0]
        db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?)', ('e2', '2026-11-13', '100790',
                                                                  '2026-11-13T12:30:00+09:00', 'completed', row))
    result = build(root, DAY)
    assert result['status'] == 'ok' and result['recorded_attempts'] == 1
    assert len(result['event_simulation']) == 1 and result['event_simulation'][0]['event_id'] == 'e1'


def test_no_position_evidence_never_claims_no_events_or_writes_outside_root(tmp_path):
    root = setup(tmp_path)
    path = root / f'vibe-fast-exit-shadow/observations-{DAY}.jsonl'
    path.write_text(json.dumps({'observed_at': f'{DAY}T12:30:00+09:00', 'state': 'unavailable'}) + '\n')
    result = run(root, DAY)
    assert result['status'] == 'unavailable' and result['reason'] == 'engine_ledger_identity_unverified'
    assert 'recorded_attempts' not in result
    assert run(root, '../../escape')['status'] == 'unavailable'
    assert not (tmp_path / 'escape.json').exists()


def test_recorded_trigger_without_attempt_is_partial_not_success(tmp_path):
    root = setup(tmp_path)
    with sqlite3.connect(root / 'vibe-event-analysis/events.sqlite3') as db:
        db.execute('DELETE FROM attempts')
    report = run(root, DAY)
    assert report['status'] == 'partial'
    assert report['reason'] == 'trigger_without_attempt_worker_status_unverified'
    assert report['recorded_triggers'] == 1 and report['recorded_attempts'] == 0
    assert '확인되지' in report['worker_coverage']
    saved = root / f'vibe-fast-exit-shadow/reviews/event-replay-{DAY}.json'
    assert json.loads(saved.read_text())['status'] == 'partial'
