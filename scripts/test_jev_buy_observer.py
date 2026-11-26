from datetime import datetime
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from jev_buy_observer import observe

DAY = '2026-11-27'
DIGEST = 'a' * 64


def at(value):
    return datetime.fromisoformat(f'{DAY}T{value}+09:00')


def fixture(tmp_path):
    ledger, research, dest = [tmp_path / x for x in ('ledger.db', 'research.db', 'isolated.db')]
    with sqlite3.connect(ledger) as db:
        db.execute('CREATE TABLE events_v2(time TEXT, event TEXT, data TEXT)')
        row = {'cycle_id': 'cycle1', 'jev_choice': '005930', 'jev_confidence': 0.7,
               'jev_response_time': at('09:30:00').isoformat(), 'jev_input_digest': DIGEST, 'jev_model': 'jev-1.14.0'}
        db.execute('INSERT INTO events_v2 VALUES(?,?,?)', (at('09:30:01').isoformat(), 'jev_shadow', json.dumps(row)))
    with sqlite3.connect(research) as db:
        db.execute('CREATE TABLE model_inputs(digest TEXT, time TEXT, engine TEXT, question TEXT, inputs TEXT, response TEXT)')
        db.execute('INSERT INTO model_inputs VALUES (?,?,?,?,?,?)',
                   (DIGEST, at('09:30:01').isoformat(), 'jev', 'candidate', json.dumps({'criteria': {'005930': 'stock'}}),
                    json.dumps({'choice': '005930', 'confidence': .7, 'type': 'choice'})))
    return ledger, research, dest


class Reader:
    def __init__(self, source='093002'):
        self.source = source
        self.calls = 0

    def call_tool(self, name, args):
        assert name == 'get_stock_price' and args == {'stock_code': '005930'}
        self.calls += 1
        return {'status': 'ok', 'data': {'Output_0': {'iem_cd': '005930', 'hoga_bsop_hour': self.source,
                                                          'bidp': 1000, 'askp': 1001}}}


def test_records_valid_buy_and_fresh_after_quote_once_per_minute(tmp_path):
    ledger, research, dest = fixture(tmp_path)
    reader = Reader()
    result = observe(ledger, research, dest, at('09:30:02'), reader, trading_day=True, clock=lambda: at('09:30:03'))
    assert result['signals_added'] == 1 and result['quotes_added'] == 1
    again = observe(ledger, research, dest, at('09:30:04'), reader, trading_day=True, clock=lambda: at('09:30:04'))
    assert again['signals_added'] == 0 and reader.calls == 1
    with sqlite3.connect(dest) as db:
        row = db.execute('SELECT symbol, source_at, received_at, bid, ask FROM quotes').fetchone()
    assert row == ('005930', at('09:30:02').isoformat(), at('09:30:03').isoformat(), 1000, 1001)


def test_old_or_future_or_wrong_judgment_never_becomes_buy(tmp_path):
    ledger, research, dest = fixture(tmp_path)
    with sqlite3.connect(research) as db:
        db.execute("UPDATE model_inputs SET response=?", (json.dumps({'choice': 'WAIT', 'confidence': .7}),))
    reader = Reader()
    result = observe(ledger, research, dest, at('09:30:02'), reader, trading_day=True, clock=lambda: at('09:30:03'))
    assert result['signals_added'] == 0 and reader.calls == 0


def test_old_quote_records_failure_without_repeated_attempt(tmp_path):
    ledger, research, dest = fixture(tmp_path)
    reader = Reader('092959')
    result = observe(ledger, research, dest, at('09:30:02'), reader, trading_day=True, clock=lambda: at('09:30:03'))
    assert result['quotes_added'] == 0 and result['quote_failures'] == 1
    observe(ledger, research, dest, at('09:30:04'), reader, trading_day=True, clock=lambda: at('09:30:04'))
    assert reader.calls == 1
    with sqlite3.connect(dest) as db:
        assert db.execute('SELECT status FROM quote_attempts').fetchone() == ('invalid_quote',)


def test_no_session_and_destination_cannot_be_source(tmp_path):
    ledger, research, dest = fixture(tmp_path)
    reader = Reader()
    assert observe(ledger, research, dest, at('09:30:02'), reader, trading_day=False)['status'] == 'closed_or_unknown'
    assert not dest.exists() and reader.calls == 0
    with pytest.raises(ValueError):
        observe(ledger, research, ledger, at('09:30:02'), reader, trading_day=True)


def test_held_symbols_are_quoted_after_signal_tracking_window(tmp_path):
    ledger, research, dest = fixture(tmp_path)
    reader = Reader('095002')
    result = observe(ledger, research, dest, at('09:50:02'), reader, trading_day=True,
                     clock=lambda: at('09:50:03'), watch_symbols=('005930',))
    assert result['signals_added'] == 0 and result['quotes_added'] == 1


def test_concurrent_same_minute_only_fetches_once(tmp_path):
    ledger, research, dest = fixture(tmp_path)
    reader = Reader()
    observe(ledger, research, dest, at('09:30:02'), reader, trading_day=True, clock=lambda: at('09:30:03'))
    reader.calls = 0
    reader.source = '093102'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: observe(ledger, research, dest, at('09:31:02'), reader,
                                                 trading_day=True, clock=lambda: at('09:31:03')), range(2)))
    assert reader.calls == 1
    assert sum(row['quotes_added'] for row in results) == 1


def test_model_input_must_arrive_before_event(tmp_path):
    ledger, research, dest = fixture(tmp_path)
    with sqlite3.connect(research) as db:
        db.execute('UPDATE model_inputs SET time=?', (at('09:30:02').isoformat(),))
    reader = Reader()
    result = observe(ledger, research, dest, at('09:30:02'), reader, trading_day=True, clock=lambda: at('09:30:03'))
    assert result['signals_added'] == 0 and reader.calls == 0
    assert result['rejections_added'] == 1
    observe(ledger, research, dest, at('09:30:04'), reader, trading_day=True, clock=lambda: at('09:30:04'))
    with sqlite3.connect(dest) as db:
        assert db.execute('SELECT reason FROM signal_rejections').fetchall() == [('input_time_mismatch',)]


def test_missing_input_records_rejection_and_source_failure_is_distinct(tmp_path):
    ledger, research, dest = fixture(tmp_path)
    with sqlite3.connect(research) as db:
        db.execute('DELETE FROM model_inputs')
    result = observe(ledger, research, dest, at('09:30:02'), Reader(), trading_day=True, clock=lambda: at('09:30:03'))
    assert result['rejections_added'] == 1
    with sqlite3.connect(dest) as db:
        assert db.execute('SELECT reason FROM signal_rejections').fetchone() == ('input_missing',)
    research.unlink()
    with pytest.raises(sqlite3.Error):
        observe(ledger, research, dest, at('09:30:04'), Reader(), trading_day=True)


def test_delayed_run_records_expiry_without_quote_and_deduplicates(tmp_path):
    ledger, research, dest = fixture(tmp_path)
    reader = Reader()
    now = at('09:32:00')
    first = observe(ledger, research, dest, now, reader, trading_day=True, clock=lambda: now)
    second = observe(ledger, research, dest, now, reader, trading_day=True, clock=lambda: now)
    assert first['signals_added'] == 0 and first['rejections_added'] == 1
    assert second['rejections_added'] == 0 and reader.calls == 0
    with sqlite3.connect(dest) as db:
        assert db.execute('SELECT reason FROM signal_rejections').fetchall() == [('expired_before_observation',)]


def test_late_insert_after_previous_scan_is_recorded_once(tmp_path):
    ledger, research, dest = fixture(tmp_path)
    reader = Reader()
    now = at('09:32:00')
    observe(ledger, research, dest, now, reader, trading_day=True, clock=lambda: now)
    with sqlite3.connect(ledger) as db:
        row = {'cycle_id': 'late', 'jev_choice': '005930', 'jev_confidence': 0.7,
               'jev_response_time': at('09:30:00').isoformat(), 'jev_input_digest': DIGEST, 'jev_model': 'jev-1.14.0'}
        db.execute('INSERT INTO events_v2 VALUES(?,?,?)', (at('09:30:01').isoformat(), 'jev_shadow', json.dumps(row)))
    result = observe(ledger, research, dest, at('09:32:10'), reader, trading_day=True, clock=lambda: at('09:32:10'))
    assert result['rejections_added'] == 1 and reader.calls == 0
    with sqlite3.connect(dest) as db:
        assert db.execute('SELECT cycle_id,reason FROM signal_rejections ORDER BY cycle_id').fetchall() == [
            ('cycle1', 'expired_before_observation'), ('late', 'expired_before_observation')]


def test_research_database_is_private(tmp_path):
    ledger, research, dest = fixture(tmp_path)
    observe(ledger, research, dest, at('09:30:02'), Reader(), trading_day=True, clock=lambda: at('09:30:03'))
    assert os.stat(dest).st_mode & 0o777 == 0o600


def test_concurrent_delayed_scans_advance_cursor_once(tmp_path):
    ledger, research, dest = fixture(tmp_path)
    reader = Reader()
    now = at('09:32:00')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: observe(ledger, research, dest, now, reader,
                                                 trading_day=True, clock=lambda: now), range(2)))
    assert sum(row['rejections_added'] for row in results) == 1
    assert reader.calls == 0
    with sqlite3.connect(dest) as db:
        assert db.execute('SELECT count(*) FROM signal_rejections').fetchone()[0] == 1
        assert db.execute('SELECT last_rowid FROM observer_cursors').fetchone()[0] == 1
