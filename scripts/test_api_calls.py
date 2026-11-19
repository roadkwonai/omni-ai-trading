from datetime import datetime, timedelta, timezone

import pytest

import api_calls
import api_catalog

KST = timezone(timedelta(hours=9))


@pytest.fixture
def log(tmp_path, monkeypatch):
    monkeypatch.setattr(api_calls, 'DB', tmp_path / 'api-calls.sqlite3')
    monkeypatch.setattr(api_calls, 'ENABLED', True)
    monkeypatch.setattr(api_calls, '_conn', {})
    return api_calls


def test_disabled_under_pytest_by_default():
    assert api_calls.ENABLED is False  # tests never write the live log unless a test opts in


def test_endpoint_masks_codes_and_drops_query():
    assert api_calls.endpoint_of('https://m.stock.naver.com/api/stock/005930/trend?x=1') == 'm.stock.naver.com/api/stock/{code}/trend'
    assert api_calls.endpoint_of('https://opendart.fss.or.kr/api/list.json?crtfc_key=SECRET') == 'opendart.fss.or.kr/api/list.json'
    assert api_calls.endpoint_of('https://query1.finance.yahoo.com/v8/finance/chart/^KS11') == 'query1.finance.yahoo.com/v8/finance/chart/{code}'
    assert api_calls.endpoint_of('https://api.kiwoom.com/api/dostk/chart', 'ka10080') == 'api.kiwoom.com/api/dostk/chart#ka10080'
    assert api_calls.provider_of('m.stock.naver.com') == 'naver' and api_calls.provider_of('kind.krx.co.kr') == 'kind'
    assert api_calls.provider_of('data-dbg.krx.co.kr') == 'krx' and api_calls.provider_of('evil-naver.com') == 'other'
    assert {k for _, k in api_calls.HOSTS} <= set(api_catalog.BY_KEY)


def test_slots_failures_and_problems(log):
    now = datetime(2026, 11, 17, 13, 27, tzinfo=KST)
    log.record('kiwoom', 'api.kiwoom.com/x#ka1', True, 100, status=200, size=500, now=now, who='market_movers')
    log.record('kiwoom', 'api.kiwoom.com/x#ka1', False, 300, status=429, error='HTTP 429', now=now + timedelta(minutes=1), who='market_movers')
    log.record('kiwoom', 'api.kiwoom.com/x#ka1', None, 0, bad=True, detail='return_code 3 acct 12345678', now=now, who='market_movers')
    (row,) = log.rows('2026-11-17')
    assert (row['slot'], row['n'], row['fail'], row['bad'], row['ms_sum'], row['ms_max'], row['bytes']) == ('13:20', 2, 1, 1, 400, 300, 500)
    assert row['last_ok'] == now.isoformat(timespec='seconds')
    fails = log.failures('2026-11-17')
    assert [f['kind'] for f in fails] == ['http', 'response'] and fails[0]['status'] == 429
    assert '12345678' not in fails[1]['detail'] and '******' in fails[1]['detail']
    assert log.last_seen()['kiwoom'][1] == (now + timedelta(minutes=1)).isoformat(timespec='seconds')


def test_http_and_mcp_entry_points(log):
    import time
    log._http('https://openapi.tossinvest.com/api/v1/stocks/A005930/info?k=v', None, time.monotonic(), 200, 10, None)
    log._http('http://127.0.0.1:8501/x', None, time.monotonic(), 200, 10, None)  # local traffic is not an external API
    log.mcp_call('nhplug_readonly', 'call_api', {'operationId': 'krstockQuotePeriod', 'input': {'act_no': '1234567890'}}, time.monotonic(), None, None)
    log.mcp_call('nhplug_paper', 'get_stock_price', {}, time.monotonic(), None, TimeoutError())
    log.mcp_call('other_server', 'x', {}, time.monotonic(), None, None)
    rows = {(r['provider'], r['endpoint']): r for r in log.rows(datetime.now(KST).strftime('%Y-%m-%d'))}
    assert set(rows) == {('toss', 'openapi.tossinvest.com/api/v1/stocks/{code}/info'), ('nh', 'mcp:krstockQuotePeriod'), ('nh', 'mcp:get_stock_price')}
    assert rows[('nh', 'mcp:get_stock_price')]['fail'] == 1
    assert log.failures('2000')[0]['detail'] == 'TimeoutError'


def test_track_counts_and_reraises(log):
    with log.track('yahoo', 'yfinance/download'):
        pass
    with pytest.raises(ValueError), log.track('yahoo', 'yfinance/download'):
        raise ValueError('x')
    (row,) = log.rows(datetime.now(KST).strftime('%Y-%m-%d'))
    assert (row['n'], row['fail']) == (2, 1)


def test_catalog_screen_map_rows():
    rows = api_catalog.screen_map()
    assert rows and all(r['API'] and r['화면'] for r in rows)
    assert all(p['uses'] and p['outputs'] is not None for p in api_catalog.CATALOG)


def test_readers_tolerate_an_empty_log_file(log):
    log.DB.write_bytes(b'')
    assert log.rows('2026-11-17') == [] and log.failures('2026') == [] and log.last_seen() == {}


@pytest.fixture
def redirecting_server():
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/old':
                self.send_response(302)
                self.send_header('Location', '/new')
                self.send_header('Content-Length', '0')
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'ok')

        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{server.server_address[1]}'
    server.shutdown()


def test_a_redirected_call_counts_once_in_both_stacks(log, monkeypatch, redirecting_server):
    import urllib.request
    import requests
    monkeypatch.setattr(log, 'LOCAL', set())  # count this local test server like an external API
    requests.get(redirecting_server + '/old', timeout=5)
    urllib.request.urlopen(redirecting_server + '/old', timeout=5).read()
    rows = log.rows(datetime.now(KST).strftime('%Y-%m-%d'))
    assert sum(r['n'] for r in rows) == 2 and sum(r['fail'] for r in rows) == 0


def test_nh_pre_send_rejection_is_not_an_nh_call_and_tool_error_is_a_response_error(log):
    import time

    class NHPreSendCheckFailed(Exception):
        pass

    class Result:
        is_error = True
    log.mcp_call('nhplug_paper', 'call_api_guarded', {'operationId': 'krstockOrderCashBuy'}, time.monotonic(), None, NHPreSendCheckFailed())
    log.mcp_call('nhplug_readonly', 'get_stock_price', {}, time.monotonic(), Result(), None)
    (row,) = log.rows(datetime.now(KST).strftime('%Y-%m-%d'))
    assert (row['endpoint'], row['n'], row['fail'], row['bad']) == ('mcp:get_stock_price', 1, 0, 1)
    assert [f['kind'] for f in log.failures('2000')] == ['response']


def test_provider_filter_on_failures(log):
    log.record('google_news', 'news.google.com/rss', False, 1, status=429)
    log.record('kiwoom', 'api.kiwoom.com/x', False, 1, status=500)
    assert [f['provider'] for f in log.failures('2000', provider='kiwoom')] == ['kiwoom']


def test_stocks_listing_path_is_not_masked_as_a_code():
    assert api_calls.endpoint_of('https://openapi.tossinvest.com/api/v1/stocks/all') == 'openapi.tossinvest.com/api/v1/stocks/all'


def test_a_tool_error_keeps_its_category_so_a_rate_limit_shows(log):
    import time

    class Text:
        text = '오류: 호출 유량을 초과했습니다.\n{"category": "rate_limit", "code": "IGW42902", "status": 429}'

    class Result:
        is_error = True
        content = [Text()]
    log.mcp_call('nhplug_readonly', 'get_stock_price', {}, time.monotonic(), Result(), None)
    assert [f['detail'] for f in log.failures('2000')] == ['tool error: rate_limit IGW42902']


def test_a_record_waits_out_another_writer_and_the_connection_stays_open(log):
    import os
    import sqlite3
    import threading
    log.record('nh', 'x/y', True, 5)  # creates the file
    log._conn.clear()  # a new process (a restart) opens the file while the dashboard or another runner is mid-write
    holder = sqlite3.connect(log.DB, timeout=0, isolation_level=None, check_same_thread=False)
    holder.execute('BEGIN IMMEDIATE')
    threading.Timer(0.3, lambda: holder.execute('COMMIT')).start()
    log.record('nh', 'x/y', True, 5)
    assert os.getpid() in log._conn  # the 30-day prune it could not do does not drop the connection
    assert sqlite3.connect(log.DB).execute('SELECT sum(n) FROM calls').fetchone()[0] == 2


def test_a_long_lock_drops_only_its_own_records_and_never_raises(log, monkeypatch):
    import os
    import sqlite3
    monkeypatch.setattr(log, 'BUSY_TIMEOUT', 0.05)
    log.record('nh', 'x/y', True, 5)
    log._conn.clear()
    holder = sqlite3.connect(log.DB, timeout=0, isolation_level=None)
    holder.execute('BEGIN IMMEDIATE')
    log.record('nh', 'x/y', True, 5)  # lost, quietly: the HTTP call it describes already happened
    cached = log._conn.get(os.getpid())
    holder.execute('COMMIT')
    log.record('nh', 'x/y', True, 5)
    assert cached is not None and log._conn[os.getpid()] is cached  # one connection per process, not one per call
    assert sqlite3.connect(log.DB).execute('SELECT sum(n) FROM calls').fetchone()[0] == 2
