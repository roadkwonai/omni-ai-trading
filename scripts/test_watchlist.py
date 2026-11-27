import io
import json
import tempfile
import unittest

import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import nh_paper_isolated  # noqa: F401 - bound before a test patches nh_etf_paper_runner.valid_bars
import watchlist as w

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=ZoneInfo('Asia/Seoul'))
BARS = [{'bsop_time': f'09{58 - i:02d}00', 'stck_prpr': str(100 + i)} for i in range(8)]


@pytest.fixture(autouse=True)
def extra_sources(monkeypatch):
    monkeypatch.setattr('stock_material.publisher_batch', lambda slot: {})
    monkeypatch.setattr('stock_material.daum_batch', lambda slot: [])
    monkeypatch.setattr('stock_material.broker_news', lambda code, now: None)
    monkeypatch.setattr('stock_material.disclosures', lambda code, now: {'items': []})


class Reader:
    def call_tool(self, tool, params):
        if tool == 'get_stock_price':
            code = params['stock_code']
            return {'status': 'ok', 'data': {'Output_0': {'iem_cd': code, 'per': 10.5, 'pbr': 1.2, 'hts_avls': 5000,
                                                          'bstp_kor_isnm': '전기전자', 'stck_prpr': 110, 'stck_prdy_clpr': 100}}}
        if params['operationId'] == 'krstockQuoteCurrentInvestor':
            return {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': [
                {'bsop_date1': '20261111', 'frgn_ntby_qty': 5, 'gigwan': -2, 'person': -3}]}}
        return {'status': 'ok', 'data': {}}


class WatchlistTests(unittest.TestCase):
    def candidates(self):
        return {c: {'name': n, 'index': 'KOSPI200', 'reasons': ['급상승'], 'price': 110, 'change_pct': 10.0,
                    'volume': 1, 'value_mil': 1} for c, n in (('000001', 'A'), ('000002', 'B'), ('000003', 'C'), ('000004', 'D'))}

    def test_select_keeps_three_highest_jev_probabilities_with_evidence(self):
        probs = {'NONE': 0.1, '000001': 0.1, '000002': 0.4, '000003': 0.3, '000004': 0.1}
        answer = {'watchlist': {'type': 'choice', 'choice': '000002', 'confidence': 0.4, 'probabilities': probs},
                  'chart_sha256': 'a' * 64}
        # Review 4 C5: each stock's news comes through the shared 5-minute cache (fresh_stock_news), never a fetch of its own
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ', {'VIBE_TRADING_STOCK_NEWS_CACHE': f'{temp}/news.json'}), \
                patch('nh_etf_paper_runner.valid_bars', return_value=BARS), patch('nh_paper_isolated.fresh_series', return_value=True), \
                patch('etf_news.headlines7', return_value=[{'title': 'm', 'published': NOW.isoformat()}]), \
                patch.object(w, 'FETCH_STOCK_NEWS', return_value=[{'title': 'A 뉴스', 'published': (NOW - timedelta(minutes=20)).isoformat()},
                                                                  {'title': 'A 어제', 'published': (NOW - timedelta(hours=20)).isoformat()}]), \
                patch.object(w.urllib.request, 'urlopen', side_effect=OSError('no network in tests')), \
                patch.object(w, 'FOREIGN', return_value=5), \
                patch('nh_etf_auto_v2._jev7_question', return_value=answer) as jev:
            record = w.select(Reader(), {**self.candidates(), '000009': {'name': 'Z', 'price': 1_500_000}}, NOW)
        self.assertEqual(record['selected'], ['000002', '000003', '000001'])
        state = jev.call_args.kwargs['extra_state']['stocks']['000002']
        self.assertEqual(state['facts']['PER'], 10.5)
        self.assertEqual(state['investor_daily_net_qty']['last_close_day']['foreign'], 5)
        # r3 E8: Jev's pick sees the stock's news of the last etf_news.RECENT only, as the runner's calls do; the record keeps the day's
        self.assertEqual(state['news'], [{'title': 'A 뉴스', 'age_min': 20}])
        self.assertEqual(len(record['stocks']['000002']['news']), 2)
        self.assertIn('NONE', jev.call_args.args[3])
        self.assertNotIn('000009', jev.call_args.args[3])  # one share above the 1,000,000 tranche
        self.assertEqual(record['market']['K200']['change_pct'], 10.0)
        # 2026-11-25: the overnight material of the candidates is a field of its own, and a failure is named, not raised
        self.assertEqual(jev.call_args.kwargs['extra_state']['overnight_material']['not_checked'], sorted(record['stocks']))

    def test_no_market_news_does_not_stop_the_watchlist(self):
        answer = {'watchlist': {'type': 'choice', 'choice': '000002', 'confidence': 0.4}, 'chart_sha256': 'a' * 64}
        for failure in (OSError('offline'), ValueError('no recent news')):
            with self.subTest(failure=failure), patch('nh_etf_paper_runner.valid_bars', return_value=BARS), \
                    patch('nh_paper_isolated.fresh_series', return_value=True), patch('etf_news.headlines7', side_effect=failure), \
                    patch.object(w, 'fresh_stock_news', return_value=[]), \
                    patch('nh_etf_auto_v2._jev7_question', return_value=answer) as jev:
                record = w.select(Reader(), self.candidates(), NOW)
            self.assertEqual((jev.call_args.args[1], record['market_news'], record['selected']), ([], [], ['000002']))

    def test_due_load_and_expiry(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'watchlist.json'
            at = lambda minute: NOW.replace(hour=9, minute=minute, second=0)  # noqa: E731
            # user 2026-11-25: stocks from the first minute (11-20~11-25 Jev had none before 09:10), then better charts
            self.assertFalse(w.due(at(0), path))
            self.assertTrue(w.due(at(1), path))
            picked = lambda when: w.write({'time': when.isoformat(), 'selected': [], 'stocks': {}}, path)  # noqa: E731
            picked(NOW.replace(hour=8, minute=52))  # the pre-open pick
            self.assertTrue(w.due(at(1), path))
            picked(at(1))
            self.assertFalse(w.due(at(3), path))
            self.assertTrue(w.due(at(5), path))
            picked(at(7))
            self.assertFalse(w.due(at(7), path))
            self.assertTrue(w.due(at(8), path))  # six settled bars now
            picked(at(8))
            self.assertFalse(w.due(at(20), path))
            self.assertTrue(w.due(at(38), path))
            path.unlink()
            self.assertTrue(w.due(NOW, path))
            record = {'time': (NOW - timedelta(minutes=10)).isoformat(), 'selected': ['000001', 'bad'],
                      'stocks': {'000001': {'name': 'A'}, 'bad': {}}}
            w.write(record, path)
            self.assertFalse(w.due(NOW, path))
            self.assertTrue(w.due(NOW + timedelta(minutes=21), path))
            self.assertEqual(w.load_candidates(NOW, path), {'000001': {'name': 'A'}})
            self.assertEqual(w.load_candidates(NOW + timedelta(minutes=40), path), {})
            self.assertEqual(w.load_candidates(NOW + timedelta(days=1), path), {})

    def test_manual_add_and_remove_override_jev_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            path, manual = Path(temp) / 'watchlist.json', Path(temp) / 'manual.json'
            w.write({'time': NOW.isoformat(), 'selected': ['000001', '000002'],
                     'stocks': {'000001': {'name': 'A'}, '000002': {'name': 'B'}}}, path)
            w.edit_manual(add='005930', name='삼성전자', path=manual)
            w.edit_manual(remove='000002', path=manual)
            result = w.load_candidates(NOW, path, manual)
            self.assertEqual(set(result), {'000001', '005930'})
            self.assertEqual(result['005930']['reasons'], ['사용자 관심종목'])
            # a stale Jev selection still leaves the user's own additions
            self.assertEqual(set(w.load_candidates(NOW + timedelta(hours=2), path, manual)), {'005930'})
            w.edit_manual(add='000002', name='B', path=manual)
            self.assertIn('000002', w.load_candidates(NOW, path, manual))
            with self.assertRaises(ValueError):
                w.edit_manual(add='SAMSUNG', path=manual)

    def test_stock_news_keeps_newest_same_day_mentions(self):
        items = ''.join(f'<item><title>{t}</title><pubDate>{d}</pubDate><source>P</source></item>' for t, d in (
            ('삼성전자 오른다', 'Thu, 12 Nov 2026 09:30:00 +0900'), ('무관한 기사', 'Thu, 12 Nov 2026 09:40:00 +0900'),
            ('삼성전자 어제', 'Tue, 10 Nov 2026 09:00:00 +0900'), ('삼성전자 최신', 'Thu, 12 Nov 2026 09:50:00 +0900')))
        items += '<item><title>삼성전자 속보 - 연합뉴스</title><pubDate>Thu, 12 Nov 2026 09:55:00 +0900</pubDate><source>연합뉴스</source></item>'
        feed = io.BytesIO(f'<rss><channel>{items}</channel></rss>'.encode())
        with patch.object(w.urllib.request, 'urlopen', return_value=feed):
            rows = w.stock_news('삼성전자', NOW)
        self.assertEqual([r['title'] for r in rows], ['삼성전자 최신', '삼성전자 오른다'])  # 연합뉴스 is never kept (Review 4 C5)


class AccountWatchlistTests(unittest.TestCase):
    def test_each_account_has_its_own_edits_over_the_shared_jev_selection(self):
        import tempfile
        from pathlib import Path
        import watchlist as w
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'watchlist.json').write_text(json.dumps({'selected': ['000001', '000002'],
                                                             'stocks': {'000001': {'name': '*가'}, '000002': {'name': '나'}}}))
            w.edit_manual(remove='000002', path=w.manual_path('live-1', root))
            w.edit_manual(add='000003', name='다', path=w.manual_path('live-1', root))
            self.assertEqual(w.account_watchlist('live-1', root), [('000001', '가', 'Jev'), ('000003', '다', '추가')])
            self.assertEqual(w.account_watchlist('paper-shared', root), [('000001', '가', 'Jev'), ('000002', '나', 'Jev')])
            self.assertEqual(w.manual_path('paper-shared', root).name, 'watchlist-manual.json')
            with self.assertRaises(ValueError):
                w.manual_path('../x', root)


if __name__ == '__main__':
    unittest.main()


def test_stock_news_refreshes_every_five_minutes_and_backs_off_on_failure(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    import watchlist
    monkeypatch.setenv('VIBE_TRADING_STOCK_NEWS_CACHE', str(tmp_path / 'stock-news.json'))
    now = datetime(2026, 11, 18, 12, 50, tzinfo=watchlist.KST)
    calls = []

    def fetch(name, at):
        calls.append(at)
        return [{'title': f'{name} {at:%H:%M}', 'published': at.isoformat()}]
    assert watchlist.fresh_stock_news('043260', '성호전자', now, fetch)[0]['title'] == '성호전자 12:50'
    assert watchlist.fresh_stock_news('043260', '성호전자', now + timedelta(minutes=4), fetch)[0]['title'] == '성호전자 12:50'  # cached
    assert watchlist.fresh_stock_news('043260', '성호전자', now + timedelta(minutes=5), fetch)[0]['title'] == '성호전자 12:55'
    assert len(calls) == 2

    def blocked(name, at):
        calls.append(at)
        raise OSError('429')
    later = now + timedelta(minutes=11)
    assert watchlist.fresh_stock_news('043260', '성호전자', later, blocked)[0]['title'] == '성호전자 12:55'  # last good rows
    assert watchlist.fresh_stock_news('043260', '성호전자', later + timedelta(minutes=2), blocked)[0]['title'] == '성호전자 12:55'
    assert len(calls) == 3  # backed off: no second try within 5 minutes
    assert watchlist.fresh_stock_news('999999', None, now, fetch) is None  # no name: nothing to search


def test_stock_news_combines_portals_publishers_and_broker_even_when_google_has_news(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_STOCK_NEWS_CACHE', str(tmp_path / 'news.json'))
    monkeypatch.setattr('etf_news.resting', lambda *_: False)
    google = {'title': '클로봇 동향 - 매체', 'publisher': '매체', 'published': NOW.isoformat()}
    naver = {**google, 'title': '클로봇 동향', 'link': 'https://example.org/story'}
    direct = {**google, 'title': '클로봇 새 사업'}
    broker = {**google, 'title': '클로봇 증권사 속보'}
    monkeypatch.setattr(w, 'FETCH_STOCK_FALLBACK', lambda *args: [naver])
    monkeypatch.setattr('stock_material.publisher_batch', lambda slot: {'매체': [direct], '다른 매체': None})
    monkeypatch.setattr('stock_material.broker_news', lambda *args: [broker])
    coverage = {}

    rows = w.fresh_stock_news('466100', '클로봇', NOW, lambda *args: [google], diagnostics=coverage)

    assert {row['title'] for row in rows} == {naver['title'], direct['title'], broker['title']}
    assert next(row for row in rows if row['title'] == naver['title'])['link'] == naver['link']
    assert coverage['sources']['다른 매체'] == 'error'
    assert coverage['sources']['kis_stock_news'] == 'recent'


def test_daum_stock_news_filters_and_preserves_publisher(monkeypatch):
    import stock_material
    items = [('클로봇 신규 사업', '이데일리', NOW - timedelta(minutes=5), 'https://example.org/a'),
             ('삼성전자 소식', '이데일리', NOW, ''),
             ('클로봇 오래된 기사', '이데일리', NOW - timedelta(days=2), ''),
             ('클로봇 속보', '연합뉴스', NOW, '')]
    monkeypatch.setattr(stock_material, 'daum_batch', lambda slot: items)
    rows = stock_material.daum_stock_news('클로봇', NOW)
    assert len(rows) == 1 and rows[0]['title'] == '클로봇 신규 사업'
    assert rows[0]['publisher'] == '이데일리' and rows[0]['link'] == 'https://example.org/a'


def test_publisher_batch_reuses_disk_cache_after_memory_cache_is_cleared(tmp_path, monkeypatch):
    import stock_material
    path = tmp_path / 'publishers.json'
    feeds = (('테스트', 'https://example.org/feed'),)
    calls = []

    def read(source, url):
        calls.append(url)
        return [{'title': '테스트 기사'}]

    monkeypatch.setattr(stock_material, 'publisher_rows', read)
    expected = {'테스트': [{'title': '테스트 기사'}]}
    assert stock_material._publisher_batch(path, 100, feeds) == expected
    stock_material._publisher_batch.cache_clear()
    assert stock_material._publisher_batch(path, 100, feeds) == expected
    assert calls == ['https://example.org/feed']
    assert stock_material._publisher_batch(path, 101, feeds) == expected
    assert len(calls) == 2


def test_the_watchlist_files_follow_vibe_trading_home(tmp_path, monkeypatch):
    # r3 U15: under VIBE_TRADING_HOME (dry runs, portability, tests) it read and wrote the real runtime
    import importlib
    monkeypatch.setenv('VIBE_TRADING_HOME', str(tmp_path))
    try:
        assert importlib.reload(w).ROOT == tmp_path / 'nhplug' / 'shared-etf-paper'
    finally:
        monkeypatch.delenv('VIBE_TRADING_HOME')
        importlib.reload(w)


def test_enrich_gives_jev_the_nh_figures_not_the_ranking_sources():
    # 2026-11-20 NH call plan P2 (option 1): the ranking service may price from Kiwoom; what Jev reads for a candidate is
    # NH's quote, which enrich() reads anyway
    class Quoting(Reader):
        def call_tool(self, tool, params):
            if tool == 'get_stock_price':
                return {'status': 'ok', 'data': {'Output_0': {'iem_cd': params['stock_code'], 'stck_prpr': 110, 'stck_prdy_clpr': 100,
                                                              'acml_vol': 5000, 'acml_tr_pbmn': 55}}}
            return super().call_tool(tool, params)
    kiwoom = {'000001': {'name': 'A', 'index': 'KOSPI200', 'reasons': ['급상승'], 'price': 109, 'change_pct': 9.0,
                         'volume': 4999, 'value_mil': 54, 'source': 'kiwoom'}}
    with patch('nh_etf_paper_runner.valid_bars', return_value=BARS), patch('nh_paper_isolated.fresh_series', return_value=True), \
            patch.object(w, 'fresh_stock_news', return_value=[]):
        stocks, _ = w.enrich(Quoting(), kiwoom, NOW)
    assert {k: stocks['000001'][k] for k in ('price', 'change_pct', 'volume', 'value_mil')} == \
        {'price': 110, 'change_pct': 10.0, 'volume': 5000, 'value_mil': 55}


def test_stock_news_goes_round_a_refused_google_and_keeps_what_it_had(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    from urllib.error import HTTPError
    import watchlist
    monkeypatch.setenv('VIBE_TRADING_STOCK_NEWS_CACHE', str(tmp_path / 'stock-news.json'))
    now, calls = datetime(2026, 11, 25, 10, 0, tzinfo=watchlist.KST), []

    def story(title, at):
        return [{'title': title, 'published': at.isoformat(), 'publisher': 'P'}]

    def refused(name, at):
        calls.append(name)
        raise HTTPError('u', 429, 'Too Many Requests', {}, None)
    monkeypatch.setattr(watchlist, 'FETCH_STOCK_FALLBACK', lambda code, name, at: story(f'{name} 네이버 {at:%H:%M}', at))
    assert watchlist.fresh_stock_news('005930', '삼성전자', now, refused)[0]['title'] == '삼성전자 네이버 10:00'
    # Google refused a moment ago: the other stocks of the same burst go straight to Naver
    assert watchlist.fresh_stock_news('000660', 'SK하이닉스', now, refused)[0]['title'] == 'SK하이닉스 네이버 10:00'
    assert calls == ['삼성전자']
    rows = watchlist.fresh_stock_news('005930', '삼성전자', now + timedelta(minutes=6), refused)
    assert [r['title'] for r in rows] == ['삼성전자 네이버 10:06', '삼성전자 네이버 10:00'] and calls == ['삼성전자']
    monkeypatch.setattr(watchlist, 'FETCH_STOCK_FALLBACK', lambda *a: (_ for _ in ()).throw(OSError('down')))
    assert watchlist.fresh_stock_news('005930', '삼성전자', now + timedelta(minutes=12), refused)[0]['title'] == '삼성전자 네이버 10:06'


def test_naver_stock_news_keeps_the_stocks_own_stories_of_the_day(monkeypatch):
    import io
    from datetime import datetime
    import watchlist
    now = datetime(2026, 11, 25, 10, 0, tzinfo=watchlist.KST)
    item = lambda title, at, office='한 매체': {'title': title, 'datetime': at, 'officeName': office}  # noqa: E731
    body = [{'items': [item('삼성전자 &quot;신기록&quot;', '202611250950'), item('삼성전자 &quot;신기록&quot;', '202611250950')]},
            {'items': [item('삼성전자 속보', '202611250955', '연합뉴스'), item('무관한 기사', '202611250955'), item('삼성전자 옛 기사', '202611230900'),
                       item('삼성전자 미래', '202611251100'), item('삼성전자 시각 오류', 'x'), None]}, 'x']
    monkeypatch.setattr(watchlist.urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(json.dumps(body).encode()))
    rows = watchlist.naver_stock_news('005930', '삼성전자', now)
    assert [(r['title'], r['published']) for r in rows] == [('삼성전자 "신기록"', '2026-11-25T00:50:00+00:00')]


def _bar(day, at, **more):
    return {'bsop_date': day, 'bsop_time': at, 'stck_oprc': '100', 'stck_hgpr': '100', 'stck_lwpr': '100', 'stck_prpr': '100', 'vol': '1', **more}


def test_the_pre_open_pick_reads_the_nxt_pre_market_and_not_yesterdays_quote():
    """User 2026-11-25: the 09:00:00 cycle must already have stocks. NH still quotes yesterday before the bell."""
    from datetime import datetime
    now = datetime(2026, 11, 25, 8, 52, tzinfo=w.KST)
    nxt = [_bar('20261125', f'08{m:02d}00', market='NXT') for m in range(49, 39, -1)]
    prior = [_bar('20261124', f'15{m:02d}00') for m in range(30, 10, -1)]
    asked = []

    class Read:
        def call_tool(self, tool, params):
            if tool == 'get_stock_price':
                return {'status': 'ok', 'data': {'Output_0': {'iem_cd': params['stock_code'], 'stck_prpr': 50, 'stck_prdy_clpr': 40, 'acml_vol': 9, 'acml_tr_pbmn': 9}}}
            asked.append((params['input']['iem_cd'], params['input'].get('today_cls_code')))
            return {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': {'iem_cd': params['input']['iem_cd']}, 'Output_1': prior}}
    candidates = {'000001': {'name': 'A', 'price': 110, 'change_pct': 4.0, 'volume': 5, 'value_mil': 7, 'reasons': ['급상승']},
                  '000002': {'name': 'B', 'price': 110, 'change_pct': 4.0, 'volume': 5, 'value_mil': 7, 'reasons': ['급상승']}}
    answer = {'watchlist': {'type': 'choice', 'choice': '000001', 'confidence': 0.4, 'probabilities': {'000001': 0.4, 'NONE': 0.6}}, 'chart_sha256': 'a' * 64}
    with patch('nh_paper_isolated.nxt_premarket', side_effect=lambda code, at: nxt if code == '000001' else nxt[:5]), \
            patch.object(w, 'investor_days', return_value=None), patch.object(w, 'fresh_stock_news', return_value=[]), \
            patch('etf_news.headlines7', return_value=[]), patch('nh_etf_auto_v2._jev7_question', return_value=answer) as jev:
        record = w.select(Read(), candidates, now, preopen=True)
    assert record['selected'] == ['000001'] and set(record['stocks']) == {'000001'}  # five rows are no chart
    assert record['stocks']['000001']['change_pct'] == 4.0 and record['market'] == {}
    bars = jev.call_args.args[0]
    assert bars['000001'] == nxt and bars['K200'][0]['bsop_date'] == '20261124'
    assert {cls for _, cls in asked} == {'0'} and 'pre-market' in jev.call_args.kwargs['extra_state']['note']


def investor_reader(*rows):
    class Read:
        def call_tool(self, tool, params):
            return {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': [
                dict(zip(('bsop_date1', 'frgn_ntby_qty', 'gigwan', 'person'), r)) for r in rows]}}
    return Read()


def test_the_closed_days_foreign_figure_is_kiwooms_and_asked_once_a_day(monkeypatch):
    """11-25: NH's foreign figure of a closed day missed Naver's by 36% (median of 57), Kiwoom's by 1.6%."""
    asked = []
    monkeypatch.setattr(w, 'FOREIGN', lambda code, day: asked.append((code, day)) or -4965611)
    read = investor_reader(('20261124', -9965611, -15287759, 24479858))
    for _ in range(2):
        assert w.investor_days(read, '114800', '20261125') == {'last_close_day': {
            'date': '20261124', 'foreign': -4965611, 'institution': -15287759, 'individual': 24479858}}
    assert asked == [('114800', '20261124')]


def test_a_failed_kiwoom_read_leaves_the_foreign_figure_out_and_is_not_repeated_at_once(monkeypatch):
    calls, clock = [], [1000.0]
    monkeypatch.setattr(w, 'FOREIGN', lambda code, day: calls.append(code) or (_ for _ in ()).throw(TimeoutError()))
    monkeypatch.setattr(w.time, 'monotonic', lambda: clock[0])
    read = investor_reader(('20261124', 5, 6, -11))
    for code in ('000001', '000002', '000003'):  # one slow answer must not be waited for once per stock
        assert w.investor_days(read, code, '20261125')['last_close_day'] == {'date': '20261124', 'institution': 6, 'individual': -11}
    assert calls == ['000001']
    clock[0] += w.FOREIGN_RETRY + 1
    monkeypatch.setattr(w, 'FOREIGN', lambda code, day: 7)
    assert w.investor_days(read, '000001', '20261125')['last_close_day']['foreign'] == 7
    monkeypatch.setattr(w, 'FOREIGN', lambda code, day: None)  # answered, but no row of that day
    assert 'foreign' not in w.investor_days(read, '000002', '20261125')['last_close_day']


def test_an_etfs_foreign_figure_of_today_is_not_sent(monkeypatch):
    """11-25: NH's value of 09:00 stood all day and two of three had the wrong sign at the close."""
    today = ('20261125', -1100000, 0, 0)
    assert 'today_provisional' not in w.investor_days(investor_reader(today), '251340', '20261125')
    closed = w.investor_days(investor_reader(('20261125', -1100000, -2116966, 761678)), '251340', '20261125')
    assert closed['today_provisional'] == {'date': '20261125', 'institution': -2116966, 'individual': 761678, 'not_tallied_yet': ['foreign']}
    stock = w.investor_days(investor_reader(('20261125', 120, 45, 0)), '005930', '20261125')['today_provisional']
    assert stock == {'date': '20261125', 'foreign': 120, 'institution': 45, 'not_tallied_yet': ['individual']}


def test_the_first_minute_charts_are_the_runners_opening_ones():
    from datetime import datetime
    now = datetime(2026, 11, 25, 9, 1, 20, tzinfo=w.KST)
    rows = [_bar('20261124', f'15{m:02d}00') for m in range(30, 10, -1)]

    class Read:
        def call_tool(self, tool, params):
            return {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': {'iem_cd': '069500'}, 'Output_1': rows}}
    assert w.chart(Read(), '069500', now)[0]['bsop_date'] == '20261124'
    nxt = [_bar('20261125', '084900', market='NXT')] * 6
    assert w.chart(Read(), '069500', now, premarket=lambda code: nxt)[0]['market'] == 'NXT'
    with pytest.raises(ValueError):
        w.chart(Read(), '069500', now.replace(minute=20))
