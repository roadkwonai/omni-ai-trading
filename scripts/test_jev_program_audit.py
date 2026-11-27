import json
import sqlite3
from datetime import datetime

import jev_program_audit as audit


def recorded_choice(tmp_path, now, choice, checked_at):
    db = tmp_path / 'research.sqlite3'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE model_inputs (digest, time, engine, question, inputs, response)')
        conn.execute('INSERT INTO model_inputs VALUES (?,?,?,?,?,?)', ('digest', now.isoformat(), 'jev', 'candidate',
                     json.dumps({'state': {'news_checked_at': checked_at}}), json.dumps({'choice': choice})))
    db.with_name('trading-day-start.jsonl').write_text(json.dumps({'time': now.isoformat(), 'note': 'trading_day'}) + '\n')
    return db


def test_audit_records_and_caches_read_only_comparison(tmp_path, monkeypatch):
    import kiwoom_data
    monkeypatch.setattr(kiwoom_data, 'call', lambda *args, **kwargs: [{'for_netprps': '12'}])
    now = datetime(2026, 11, 26, 10, 12, tzinfo=audit.KST)
    db = recorded_choice(tmp_path, now, 'K200', now.isoformat())
    calls = []
    def program(code, day, now):
        calls.append(('program', code))
        return []
    def bars(code, day, now):
        calls.append(('bars', code))
        return {'bars': []}
    kwargs = {'source': db, 'root': tmp_path, 'fetch_program': program, 'fetch_bars': bars}
    result = audit.run(now, **kwargs)
    assert result['decisions'][0]['symbol'] == '069500'
    assert result['decisions'][0]['signal']['status'] == 'program_source_empty'
    assert result['captures']['069500']['etf_flow']['status'] == 'observed'
    assert audit.run(now, **kwargs) == result
    assert calls == [('program', '069500'), ('bars', '069500')]
    saved = json.loads((tmp_path / 'audit/2026-11-26/1010.json').read_text())
    assert saved['captures']['069500']['captured_at'] == now.isoformat()
    assert saved['decisions'][0]['digest'] == 'digest'


def test_audit_preserves_program_capture_when_chart_read_fails(tmp_path):
    now = datetime(2026, 11, 26, 10, 12, tzinfo=audit.KST)
    db = recorded_choice(tmp_path, now, '005930', None)

    def no_bars(*args, **kwargs):
        raise TimeoutError

    result = audit.run(now, source=db, root=tmp_path, fetch_program=lambda *a, **kw: [{'tm': '101100'}], fetch_bars=no_bars)
    assert result['status'] == 'partial'
    assert result['captures']['005930']['program'] == [{'tm': '101100'}]
    assert result['captures']['005930']['error_type'] == 'TimeoutError'


def test_audit_never_fetches_on_closed_or_outside_session(tmp_path):
    def fail(*args, **kwargs):
        raise AssertionError('unexpected fetch')
    now = datetime(2026, 11, 26, 10, 12, tzinfo=audit.KST)
    assert audit.run(now, source=tmp_path / 'research.sqlite3', root=tmp_path, fetch_program=fail)['status'] == 'closed_or_unknown'
    assert audit.run(now.replace(hour=8), root=tmp_path, fetch_program=fail)['status'] == 'outside_session'


def test_exit_outcome_uses_later_quote_and_confirmed_remaining_fills(tmp_path):
    with sqlite3.connect(tmp_path / 'orders.sqlite3') as db:
        db.execute('CREATE TABLE fills (symbol, side, quantity, price, time)')
        db.executemany('INSERT INTO fills VALUES (?,?,?,?,?)', [
            ('005930', 'BUY', 3, 100, '2026-11-26T09:50:00+09:00'),
            ('005930', 'SELL', 3, 104, '2026-11-26T10:02:00+09:00'),
        ])
    with sqlite3.connect(tmp_path / 'trading-ledger.sqlite3') as db:
        db.execute('CREATE TABLE events_v2 (time, event, data)')
        db.executemany('INSERT INTO events_v2 VALUES (?,?,?)', [
            (t, 'holding_quote', json.dumps({'symbol': '005930', 'qty': 3, 'bid': bid}))
            for t, bid in [('2026-11-26T10:00:00+09:00', 110), ('2026-11-26T10:01:00+09:00', 106)]
        ])
    pick = {'at': '2026-11-26T09:59:59+09:00', 'reviewable_at': '2026-11-26T10:00:00+09:00',
            'symbol': '005930', 'position': {'quantity': 3}}
    now = datetime.fromisoformat('2026-11-26T10:01:30+09:00')
    assert audit.exit_outcome(tmp_path, pick, now)['status'] == 'awaiting_confirmed_exit'
    settled = audit.exit_outcome(tmp_path, pick, datetime.fromisoformat('2026-11-26T10:03:00+09:00'))
    assert (settled['next_bid'], settled['actual_remaining_avg_price'], settled['gross_difference_krw']) == (106, 104, 6)
