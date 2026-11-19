"""One rule for 'this log line belongs to that day' (sqlite_ro.day_rows): the line's own time, not any mention of the day."""
import json

import daily_improvement
import intraday_audit
from sqlite_ro import day_rows

LINES = [{'time': '2026-11-17T10:00:00+09:00', 'event': 'decision'},
         {'time': '2026-11-18T10:00:00+09:00', 'event': 'market_context', 'daily': [{'day': '2026-11-17', 'close': 1}]},
         {'event': 'news', 'published': '2026-11-17T09:00:00+09:00'}]


def write(path, lines, extra=''):
    path.write_text(''.join(json.dumps(line) + '\n' for line in lines) + extra)
    return path


def test_a_next_day_line_that_mentions_the_day_is_not_that_days_row(tmp_path):
    path = write(tmp_path / 'events.jsonl', LINES, '{"time": "2026-11-17T10:05:00+09:00", "event": "trunc\n')
    rows, bad = day_rows(path, '2026-11-17')
    assert [r['event'] for r in rows] == ['decision'] and bad == 1
    assert day_rows(tmp_path / 'missing.jsonl', '2026-11-17') == (None, 0)


def test_the_hourly_audit_counts_only_the_days_events(tmp_path):
    write(tmp_path / 'events.jsonl', LINES)
    out = intraday_audit.ledger(tmp_path, '2026-11-17', None)
    assert out['events'] == {'decision': 1}
    assert out['last_event'].startswith('2026-11-17')


def test_the_journal_counts_errors_of_the_day_only(tmp_path, monkeypatch):
    import trading_journal
    write(tmp_path / 'events.jsonl', [{'time': '2026-11-17T10:00:00+09:00', 'event': 'order_error', 'error_type': 'X'},
                                      {'time': '2026-11-18T10:00:00+09:00', 'event': 'quote_error', 'note': '2026-11-17'}])
    monkeypatch.setattr(trading_journal, 'ROOT', tmp_path)
    monkeypatch.setattr(trading_journal, 'REVIEWS', tmp_path / 'reviews')
    monkeypatch.setattr(daily_improvement, 'build', lambda day: (_ for _ in ()).throw(OSError('not under test')))
    assert trading_journal.system('2026-11-17')['errors'] == {'order_error:X': 1}
