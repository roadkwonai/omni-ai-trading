import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import audit_incidents as incidents

KST = incidents.KST
T0 = datetime(2026, 11, 18, 10, 20, tzinfo=KST)


def _keys(result, status):
    return [item['key'] for item in result[status]]


def test_record_walks_new_open_resolved_recurred(tmp_path):
    db = tmp_path / 'audit-incidents.sqlite3'
    first = incidents.record({'jev.news_old': '오래된 헤드라인 1건'}, T0, path=db)
    assert _keys(first, 'new') == ['jev.news_old'] and not first['open'] + first['resolved'] + first['recurred']
    assert first['new'][0]['title'] == incidents.REGISTRY['jev.news_old']['title'] and first['new'][0]['count'] == 1

    second = incidents.record({'jev.news_old': '2건'}, T0 + timedelta(hours=1), path=db)
    assert _keys(second, 'open') == ['jev.news_old'] and second['open'][0]['count'] == 2

    third = incidents.record({}, T0 + timedelta(hours=2), path=db)
    assert _keys(third, 'resolved') == ['jev.news_old']
    assert incidents.record({}, T0 + timedelta(hours=3), path=db) == {'new': [], 'recurred': [], 'resolved': [], 'open': []}

    back = incidents.record({'jev.news_old': '다시'}, T0 + timedelta(days=1), path=db)
    assert _keys(back, 'recurred') == ['jev.news_old']
    item = back['recurred'][0]
    assert item['recurrences'] == 1 and item['count'] == 3 and item['first_seen'] == T0.isoformat(timespec='seconds')
    [row] = incidents.history(db)
    assert row['status'] == 'recurred' and row['last_detail'] == '다시' and row['resolved_at'] is None


def test_the_same_call_twice_changes_nothing(tmp_path):
    db = tmp_path / 'audit-incidents.sqlite3'
    incidents.record({'jev.news_old': 'a', 'service.down:x': 'b'}, T0 - timedelta(hours=1), path=db)
    findings, now = {'jev.news_old': 'a'}, T0
    once = incidents.record(findings, now, path=db)
    assert incidents.record(findings, now, path=db) == once
    assert _keys(once, 'open') == ['jev.news_old'] and _keys(once, 'resolved') == ['service.down:x']
    counts = {row['key']: row['count'] for row in incidents.history(db)}
    assert counts == {'jev.news_old': 2, 'service.down:x': 1}


def test_occurrences_are_kept_at_most_every_ten_minutes(tmp_path):
    db = tmp_path / 'audit-incidents.sqlite3'
    for minutes in (0, 5, 11):
        incidents.record({'jev.news_old': f'{minutes}'}, T0 + timedelta(minutes=minutes), path=db)
    with sqlite3.connect(db) as conn:
        details = [d for (d,) in conn.execute("SELECT detail FROM occurrences WHERE key='jev.news_old' ORDER BY time")]
    assert details == ['0', '11']
    assert incidents.history(db)[0]['count'] == 3


def test_a_qualified_key_takes_its_prefix_title_and_unknown_keys_have_none(tmp_path):
    result = incidents.record({'source.lagging:investor_rankings': '기관 160분', 'service.down:x': 'down'}, T0,
                              path=tmp_path / 'db.sqlite3')
    titles = {item['key']: item['title'] for item in result['new']}
    assert titles == {'source.lagging:investor_rankings': incidents.REGISTRY['source.lagging']['title'],
                      'service.down:x': None}


def test_history_since_filters_by_last_seen(tmp_path):
    db = tmp_path / 'db.sqlite3'
    incidents.record({'jev.news_old': 'a', 'ledger.ingest_error:shared-etf-paper': 'b'}, T0, path=db)
    incidents.record({'jev.news_old': 'a'}, T0 + timedelta(days=1), path=db)
    assert [r['key'] for r in incidents.history(db, since=(T0 + timedelta(hours=1)).isoformat())] == ['jev.news_old']
    assert incidents.history(tmp_path / 'missing.sqlite3') == []


def test_a_database_failure_returns_an_error_instead_of_raising(tmp_path):
    assert incidents.record({'jev.news_old': 'a'}, T0, path=tmp_path) == {'error': 'OperationalError'}  # a folder


def test_every_registry_entry_names_its_fix_and_an_existing_guard_test():
    tests = ''.join(p.read_text(encoding='utf-8') for p in Path(__file__).parent.glob('test_*.py'))
    for prefix, entry in incidents.REGISTRY.items():
        assert re.fullmatch(r'[a-z_]+\.[a-z_]+', prefix), prefix
        assert set(entry) == {'title', 'since', 'cause', 'fix', 'guard'} and all(entry.values()), prefix
        assert f"def {entry['guard']}(" in tests, entry['guard']


def test_a_check_that_did_not_run_neither_resolves_nor_recurs(tmp_path):
    # 11-18 15:21: the hourly run after the session skipped the balance check, marked it resolved with no fix, and the
    # next session's sighting would have been a false 재발 (recurred → 즉시 보완)
    db = tmp_path / 'audit-incidents.sqlite3'
    key = 'live.balance_missing:live-1'
    incidents.record({key: '오늘 잔고 기록 없음', 'jev.news_old': 'a'}, T0, path=db)
    after = incidents.record({}, T0.replace(hour=15, minute=21), path=db, unchecked={'live.balance_missing'})
    assert _keys(after, 'resolved') == ['jev.news_old']  # a check that ran and found nothing still resolves
    nextday = incidents.record({key: '오늘 잔고 기록 없음'}, T0 + timedelta(days=1), path=db)
    assert _keys(nextday, 'open') == [key] and not nextday['recurred']
    one = incidents.record({}, T0 + timedelta(days=1, hours=1), path=db, unchecked={'live.balance_missing:live-2'})
    assert _keys(one, 'resolved') == [key]  # a qualified skip leaves other qualifiers to their own check
