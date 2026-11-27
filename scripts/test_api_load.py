import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import api_calls
import api_load as A

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 24, 10, 25, tzinfo=KST)  # window: slots from 10:00


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(A, 'open_files', lambda: ({}, 256))  # never this Mac's own services
    (tmp_path / 'shared-etf-paper' / 'reviews').mkdir(parents=True)
    with sqlite3.connect(tmp_path / 'api-calls.sqlite3') as db:
        db.executescript(api_calls.SCHEMA)
    return tmp_path


def calls(root, provider, n, ms=200, caller='nh_paper_isolated', day='2026-11-24', slot='10:00', fail=0):
    with sqlite3.connect(root / 'api-calls.sqlite3') as db:
        db.execute('INSERT INTO calls VALUES (?,?,?,?,?,?,?,0,?,?,0,NULL,NULL)', (day, slot, provider, f'ep-{caller}', caller, n, fail, n * ms, ms))


def failed(root, provider, detail, count, status=None, caller='nh_paper_isolated', minute=20, endpoint='mcp:krstockInquiryBalance'):
    with sqlite3.connect(root / 'api-calls.sqlite3') as db:
        db.executemany('INSERT INTO failures VALUES (?,?,?,?,?,?,?)',
                       [(f'2026-11-24T10:{minute:02d}:{s:02d}+09:00', provider, endpoint, caller, 'x', status, detail) for s in range(count)])


def keys(root, now=NOW):
    return {f['key']: f for f in A.findings(A.collect(root, now))}


def test_an_empty_or_missing_log_finds_nothing(tmp_path, root):
    assert A.findings(A.collect(tmp_path / 'none', NOW)) == [] and keys(root) == {}
    with sqlite3.connect(root / 'api-calls.sqlite3') as db:  # a log written by older code has no seconds table yet
        db.execute('DROP TABLE seconds')
    assert keys(root) == {}


def test_load_report_reads_one_connection_and_keeps_available_tables(root, monkeypatch):
    opened = []
    original = A.ro_connect

    def connect(path, **kwargs):
        opened.append(path)
        return original(path, **kwargs)

    monkeypatch.setattr(A, 'ro_connect', connect)
    calls(root, 'kis', 1)
    calls(root, 'kis', 2, day='2026-11-23')
    with sqlite3.connect(root / 'api-calls.sqlite3') as db:
        db.execute('DROP TABLE seconds')
    data = A.collect(root, NOW)
    assert [row['n'] for row in data['today']] == [1]
    assert data['days'] == ['2026-11-23'] and [row['n'] for row in data['base']] == [2]
    assert data['seconds'] == []
    assert len(opened) == 1


def test_refusals_are_told_apart_from_a_slow_local_start_and_other_errors(root):
    calls(root, 'nh', 100)
    failed(root, 'nh', 'tool error: rate_limit IGW42903', 3)
    failed(root, 'google_news', 'HTTP 429', 3, status=429)
    failed(root, 'nh', 'RuntimeError', 1, caller='dashboard')           # rows of the older log
    failed(root, 'nh', api_calls.LOCAL_START, 1, caller='nh_live_follow')
    failed(root, 'nh', 'tool error: network', 4)                        # 4% of the calls: not failing
    found = keys(root)
    assert set(found) == {'api.refused:nh', 'api.refused:google_news', 'api.local_start:nh'}
    assert '3건' in found['api.refused:nh']['detail'] and '공유 모의 실행기' in found['api.refused:nh']['detail']
    assert '2건' in found['api.local_start:nh']['detail'] and all(f['advice'] for f in found.values())


def test_a_refusal_before_the_window_or_below_the_threshold_is_quiet(root):
    failed(root, 'nh', 'tool error: rate_limit IGW42902', 2)
    failed(root, 'toss', 'HTTP 429', 5, status=429, minute=0)
    assert 'api.refused:nh' not in keys(root) and 'api.refused:toss' not in keys(root, NOW + timedelta(minutes=10))


def test_failing_share(root):
    calls(root, 'kiwoom', 20, fail=5)
    failed(root, 'kiwoom', 'HTTP 502', 5, status=502, caller='market_movers')
    assert set(keys(root)) == {'api.failing:kiwoom'}


def test_slow_needs_both_an_absolute_and_a_relative_jump(root):
    for day in ('2026-11-19', '2026-11-20', '2026-11-23'):
        calls(root, 'nh', 100, ms=300, day=day)
        calls(root, 'openai', 100, ms=3000, day=day)
    calls(root, 'nh', 100, ms=2000)
    calls(root, 'openai', 100, ms=3200)      # always slow: not news
    calls(root, 'naver', 5, ms=9000)         # too few calls to judge
    assert set(keys(root)) == {'api.slow:nh'}


def test_a_caller_far_above_its_usual_volume_is_a_surge(root):
    for day in ('2026-11-19', '2026-11-20', '2026-11-23'):
        calls(root, 'nh', 100, day=day, caller='dashboard')
        calls(root, 'nh', 300, day=day)
    calls(root, 'nh', 260, caller='dashboard', slot='10:10')
    calls(root, 'nh', 380)
    calls(root, 'nh', 40, caller='probe')
    assert set(keys(root)) == {'api.surge:nh:dashboard'}
    with sqlite3.connect(root / 'api-calls.sqlite3') as db:
        db.execute("UPDATE calls SET slot = '09:00'")
    assert keys(root, NOW.replace(hour=9)) == {}  # not judged at the open


def test_seconds_at_the_limit_are_a_burst_and_kiwoom_counts_per_tr(root):
    with sqlite3.connect(root / 'api-calls.sqlite3') as db:
        for s in range(3):
            stamp = f'2026-11-24T10:20:0{s}+09:00'
            db.execute('INSERT INTO seconds VALUES (?,?,?,?,?)', (stamp, 'nh', 'a', 'x', 3))
            db.execute('INSERT INTO seconds VALUES (?,?,?,?,?)', (stamp, 'nh', 'b', 'y', 2))
            db.execute('INSERT INTO seconds VALUES (?,?,?,?,?)', (stamp, 'kiwoom', 'a', 'x', 4))
            db.execute('INSERT INTO seconds VALUES (?,?,?,?,?)', (stamp, 'kiwoom', 'b', 'x', 4))
    assert set(keys(root)) == {'api.burst:nh'}  # kiwoom: 8 a second over two TRs is inside five a TR


def test_the_watch_reports_a_key_once_until_it_has_been_gone(root):
    failed(root, 'nh', 'tool error: rate_limit IGW42903', 3)
    sent, ok = [], []
    send = lambda text: bool(sent.append(text) or ok)  # noqa: E731
    A.step(send, root, NOW)                                 # not delivered: it stays unreported
    ok.append(1)
    A.step(send, root, NOW + timedelta(minutes=1))
    A.step(send, root, NOW + timedelta(minutes=2))
    assert len(sent) == 2 and 'IGW42903' in sent[0] and '→' in sent[0]
    failed(root, 'nh', 'tool error: rate_limit IGW42903', 3, minute=59, caller='dashboard')
    A.step(send, root, NOW + timedelta(minutes=60))          # gone for over REPEAT_MIN, back again
    assert len(sent) == 3
    assert A.step(send, root, NOW + timedelta(days=1)) == [] and len(sent) == 3  # a new day starts clean


def test_report_covers_every_called_api_the_blocked_requests_and_the_advice(root):
    calls(root, 'nh', 100, slot='09:50')
    calls(root, 'nh', 300, caller='dashboard')
    calls(root, 'kiwoom', 50, caller='market_movers')
    failed(root, 'nh', 'tool error: rate_limit IGW42903', 4, caller='dashboard')
    A.step(lambda text: True, root, NOW)
    text = A.report(root, NOW)
    for part in ('NH 나무 PLUG', '키움 REST', '## 막힌 요청', 'IGW42903', '대시보드 75%', '## 보완 제안', A.ADVICE['refused']):
        assert part in text
    path = A.write_report(root, NOW)
    assert path.name == 'api-audit-2026-11-24.md' and path.read_text() == text
    with sqlite3.connect(root / 'api-calls.sqlite3') as db:
        db.execute("INSERT INTO seconds VALUES ('2026-11-24T10:20:00+09:00', 'nh', 'a', 'x', 2)")
    assert '2회 (일부)' in A.report(root, NOW) and '측정 전' in text  # seconds are not measured yet: said, not shown as zero


def test_the_log_keeps_call_starts_by_the_second_and_names_a_local_start(tmp_path, monkeypatch):
    monkeypatch.setattr(api_calls, 'DB', tmp_path / 'nhplug' / 'api-calls.sqlite3')
    monkeypatch.setattr(api_calls, 'ENABLED', True)
    monkeypatch.setattr(api_calls, '_conn', {})
    at = datetime(2026, 11, 24, 10, 0, 5, tzinfo=KST)
    api_calls.record('nh', 'mcp:a', True, 1500, who='x', now=at)
    api_calls.record('nh', 'mcp:a', True, 1600, who='x', now=at)
    api_calls.record('nh', 'mcp:a', None, 0, bad=True, detail='e', who='x', now=at)  # problem(): not another call
    assert api_calls._read('SELECT * FROM seconds') == [
        {'time': '2026-11-24T10:00:03+09:00', 'provider': 'nh', 'endpoint': 'mcp:a', 'caller': 'x', 'n': 2}]
    api_calls.mcp_call('nhplug_paper', 'get_stock_price', {}, 0, None, RuntimeError('Failed to initialize server session'))
    api_calls.mcp_call('nhplug_paper', 'get_stock_price', {}, 0, None, RuntimeError('other'))
    assert {f['detail'] for f in api_calls.failures('2000') if f['detail'] != 'e'} == {api_calls.LOCAL_START, 'RuntimeError'}


def test_the_watch_survives_a_bad_round_and_ends_with_the_report(root, monkeypatch):
    times = iter([NOW.replace(hour=9, minute=0), NOW, NOW + timedelta(minutes=1), NOW.replace(hour=15, minute=40)])
    rounds, sent = [], []
    monkeypatch.setattr(A, 'step', lambda send, r, now: rounds.append(now) or 1 / 0)
    A.watch(root, clock=lambda: next(times), sleep=lambda s: None, send=lambda text: sent.append(text) or True)
    assert len(rounds) == 2 and len(sent) == 1 and '마감' in sent[0]  # 09:00 is before the first round
    assert (root / 'shared-etf-paper' / 'reviews' / 'api-audit-2026-11-24.md').exists()


def test_a_service_near_its_open_file_limit_is_told_before_its_calls_fail(root, monkeypatch):
    # 2026-11-25: the dashboard's open files grew to 253 of 256 and only the 48 failed calls told anyone
    sent, files = [], {'shared-etf-dashboard': 178, 'vibe-longterm': 19}
    monkeypatch.setattr(A, 'open_files', lambda: (files, 256))
    look = lambda m: A.step(lambda text: sent.append(text) or True, root, NOW + timedelta(minutes=m))  # noqa: E731
    assert look(0) == [] and sent == []          # 69%
    files['shared-etf-dashboard'] = 180          # 70%
    assert [x['key'] for x in look(1)] == ['api.files:shared-etf-dashboard'] and '180/256' in sent[0] and '→' in sent[0]
    look(2)
    assert len(sent) == 1                        # once a level
    files['shared-etf-dashboard'] = 232          # 90%: told again
    assert [x['key'] for x in look(3)] == ['api.files:shared-etf-dashboard:90'] and len(sent) == 2
    files['shared-etf-dashboard'] = 35           # restarted
    assert look(4) == [] and len(sent) == 2
    assert '180/256' in A.report(root, NOW + timedelta(minutes=5))


def test_open_files_counts_each_service_and_reads_the_limit():
    def run(args, **_):
        out = {'launchctl list': '2043\t0\tcom.vibetrading.vibe-longterm\n26109\t0\tcom.vibetrading.shared-etf-dashboard\n-\t0\tcom.vibetrading.preflight\n',
               'launchctl limit': '\tmaxfiles    256            unlimited      \n',
               'lsof': 'p2043\nfcwd\nftxt\nf0\nf1\np26109\nfcwd\nf0\nf12\nf13\n'}
        key = ' '.join(args[:2]) if args[0] == 'launchctl' else args[0]
        return type('R', (), {'returncode': 1 if key == 'lsof' else 0, 'stdout': out[key]})()  # lsof says 1 when one pid is gone
    assert A.open_files(run) == ({'vibe-longterm': 2, 'shared-etf-dashboard': 3}, 256)


def test_open_files_that_cannot_be_read_are_not_a_finding():
    def run(args, **_):
        raise FileNotFoundError(args[0])
    assert A.open_files(run) == ({}, 0) and A.file_findings({}, 0) == []
    assert A.file_findings({'x': 9000}, 0) == []  # an unlimited service
