import hashlib
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest


DAY = '2026-11-25'
SCRIPT = Path(__file__).with_name('jev_entry_study.py')
CYCLE = 'a' * 32
INPUT = 'b' * 64


def at(hhmm):
    return f'{DAY}T{hhmm}:00+09:00'


@pytest.fixture
def snapshot(tmp_path):
    root = tmp_path / DAY / 'shared-etf-paper'
    root.mkdir(parents=True)
    with closing(sqlite3.connect(root / 'trading-ledger.sqlite3')) as db, db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE events_v2 (source TEXT, offset INTEGER, digest TEXT, time TEXT, event TEXT, data TEXT, schema_version INTEGER)')
        db.execute('CREATE INDEX events_v2_event_time ON events_v2(event, time)')

        def event(kind, time, *, duplicate=False, **fields):
            data = json.dumps({'event': kind, 'time': time, **fields})
            digest = hashlib.sha256(data.encode()).hexdigest()
            row = ('fixture', 0, digest, time, kind, data, 2)
            db.execute('INSERT INTO events_v2 VALUES (?,?,?,?,?,?,?)', row)
            if duplicate:
                db.execute('INSERT INTO events_v2 VALUES (?,?,?,?,?,?,?)', ('copy', *row[1:]))

        def market(observed, minute, price):
            bar = {'bsop_time': minute, 'stck_oprc': price, 'stck_hgpr': price, 'stck_lwpr': price, 'stck_prpr': price}
            event('market_context', at(observed), market_bars={'005930': [bar]})

        event('jev_shadow', at('10:01'), cycle_id=CYCLE, jev_input_digest=INPUT,
              jev_response_time=at('10:01'), jev_choice='K200',
              jev_probabilities={'K200': 0.5, '005930': 0.3, '000660': 0.15, 'WAIT': 0.05}, duplicate=True)
        event('v2_entry_gate', at('10:01'), cycle_id=CYCLE, choice='000660',
              reason='entry_conditions_met', permitted=True, source='watchlist_strength')
        event('submitted', at('10:02'), cycle_id=CYCLE, symbol='000660', qty=4, source='watchlist_strength')
        event('v2_stopped', at('15:18'), reason='session or STOP')
        market('10:00', '095900', 100)
        market('10:03', '100000', 180)
        market('10:06', '100500', 110)

    with closing(sqlite3.connect(root / 'market-research.sqlite3')) as db, db:
        db.execute('CREATE TABLE model_inputs (digest TEXT PRIMARY KEY, time TEXT, engine TEXT, question TEXT, inputs TEXT, response TEXT)')
        db.execute('INSERT INTO model_inputs VALUES (?,?,?,?,?,?)', (INPUT, at('10:01'), 'jev', 'candidate', '{}', '{}'))
    with closing(sqlite3.connect(root / 'orders.sqlite3')) as db, db:
        db.execute('CREATE TABLE intents (id TEXT, day TEXT, symbol TEXT, side TEXT, qty INTEGER, price INTEGER, status TEXT, order_number INTEGER, created REAL)')
        db.execute('CREATE TABLE decision_order_links (order_id TEXT PRIMARY KEY, cycle_id TEXT)')
        db.execute('CREATE TABLE fills (order_id TEXT PRIMARY KEY, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
        db.execute("INSERT INTO intents VALUES ('x','20261125','000660','BUY',4,100,'terminal',1,0)")
        db.execute('INSERT INTO decision_order_links VALUES (?,?)', ('x', CYCLE))
        db.execute("INSERT INTO fills VALUES ('x','000660','BUY',2,100,?)", (at('10:03'),))
    return root


def run(snapshot, output):
    return subprocess.run(
        [sys.executable, str(SCRIPT), '--snapshot', str(snapshot), '--from', DAY, '--to', DAY, '--output', str(output)],
        capture_output=True, text=True, check=False,
    )


def test_replays_without_mutating_the_wal_backup(snapshot, tmp_path):
    output = tmp_path / 'report'
    before = {path.name: path.read_bytes() for path in snapshot.iterdir()}
    result = run(snapshot.parent, output)
    assert result.returncode == 0, result.stderr
    report = output / f'{DAY}.json'
    first = report.read_bytes()
    data = json.loads(first)
    assert len(data['cycles']) == 1
    cycle = data['cycles'][0]
    assert cycle['model_choice'] == '069500'
    assert cycle['input_linked'] is True
    assert cycle['submissions'][0]['source'] == 'watchlist_strength'
    assert cycle['submissions'][0]['qty'] == 4
    assert cycle['fills'][0]['quantity'] == 2
    assert cycle['execution_status'] == 'partial_fill'
    assert [row['symbol'] for row in data['candidates']] == ['005930']
    candidate = data['candidates'][0]
    assert candidate['eligibility'] == 'unknown'
    assert candidate['rank'] == 1
    assert candidate['model_rank'] == 2
    assert candidate['price_movement']['base'] == 100
    assert candidate['price_movement']['r5_pct'] == 10
    assert candidate['price_movement']['r15_pct'] is None
    assert candidate['price_movement']['close_pct'] is None
    assert run(snapshot, output).returncode == 0
    assert report.read_bytes() == first
    assert {path.name: path.read_bytes() for path in snapshot.iterdir()} == before


def test_rejects_output_symlink_into_backup(snapshot, tmp_path):
    alias = tmp_path / 'alias'
    alias.symlink_to(snapshot, target_is_directory=True)
    result = run(snapshot, alias)
    assert result.returncode != 0
    assert not (snapshot / f'{DAY}.json').exists()


def test_corrupt_database_exposes_failure(snapshot, tmp_path):
    (snapshot / 'market-research.sqlite3').write_bytes(b'broken database')
    result = run(snapshot, tmp_path / 'report')
    assert result.returncode != 0
    assert 'Traceback' not in result.stderr


def test_missing_input_is_exposed_in_report(snapshot, tmp_path):
    with closing(sqlite3.connect(snapshot / 'market-research.sqlite3')) as db, db:
        db.execute('DELETE FROM model_inputs')
    output = tmp_path / 'report'
    result = run(snapshot, output)
    assert result.returncode == 0, result.stderr
    report = json.loads((output / f'{DAY}.json').read_text())
    assert report['status'] == 'incomplete'
    assert 'model_inputs_missing' in report['completeness']['warnings']


def test_rejects_backup_with_wal_sidecar(snapshot, tmp_path):
    (snapshot / 'orders.sqlite3-wal').touch()
    output = tmp_path / 'report'
    result = run(snapshot, output)
    assert result.returncode != 0
    assert not output.exists()
