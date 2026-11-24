import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import watchlist as w

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=ZoneInfo('Asia/Seoul'))
BARS = [{'bsop_time': f'09{58 - i:02d}00', 'stck_prpr': str(100 + i)} for i in range(8)]


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
            self.assertFalse(w.due(NOW.replace(hour=9, minute=1), path))
            self.assertFalse(w.due(NOW.replace(hour=9, minute=5), path))
            self.assertFalse(w.due(NOW.replace(hour=9, minute=7), path))
            self.assertTrue(w.due(NOW.replace(hour=9, minute=8), path))
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
