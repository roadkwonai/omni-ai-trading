import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import new_listings
import toss_data as t

NOW = datetime(2026, 11, 17, 8, 30, tzinfo=t.KST)


class TossDataTests(unittest.TestCase):
    def test_only_allowed_read_paths(self):
        for path in ('/api/v1/orders', '/api/v1/holdings', '/api/v1/accounts', '/oauth2/token'):
            with self.assertRaises(t.TossError):
                t.get(path)  # refused before any request or token

    def test_scheduled_listings_keep_dated_common_stocks_soonest_first(self):
        stocks = [{'symbol': '498150', 'name': '엠비디', 'securityType': 'STOCK', 'isCommonShare': True, 'listDate': '2026-11-24', 'market': 'KOSDAQ'},
                  {'symbol': '471800', 'name': '에이엘로봇', 'securityType': 'STOCK', 'isCommonShare': True, 'listDate': '2026-11-18', 'market': 'KOSDAQ'},
                  {'symbol': '499990', 'name': '하나스팩40호', 'securityType': 'STOCK', 'isCommonShare': True, 'listDate': '2026-11-20', 'market': 'KOSDAQ'},
                  {'symbol': '271160', 'name': '하나에어로다이내믹스', 'securityType': 'STOCK', 'isCommonShare': True, 'listDate': None, 'market': 'KOSDAQ'},
                  {'symbol': '0999A0', 'name': '어떤ETF', 'securityType': 'ETF', 'isCommonShare': True, 'listDate': '2026-11-19', 'market': 'KOSPI'}]

        def get(path, params=None):
            if path == '/api/v1/stocks/all':
                return [{'symbol': s['symbol']} for s in stocks if s['market'] == params['market']]
            return [s for s in stocks if s['symbol'] in params['symbols'].split(',')]
        with patch.object(t, 'get', autospec=True, side_effect=get):
            rows = t.scheduled_listings(NOW)
        self.assertEqual([(r['code'], r['listed']) for r in rows], [('471800', '20261118'), ('498150', '20261124')])


class UpcomingTests(unittest.TestCase):
    def test_cached_once_a_day_and_failure_retried_later(self):
        root = Path(tempfile.mkdtemp())
        rows = [{'name': '에이엘로봇', 'code': '471800', 'listed': '20261118', 'market': 'KOSDAQ'}]
        fetch = Mock(return_value=rows)
        self.assertEqual(new_listings.upcoming_cache(root, NOW, fetch)['rows'], rows)
        self.assertEqual(new_listings.upcoming_cache(root, NOW.replace(hour=9), fetch)['rows'], rows)
        self.assertEqual(fetch.call_count, 1)
        import premarket
        with patch.object(new_listings, 'upcoming_cache', return_value={'ok': True, 'rows': rows}):
            self.assertEqual(premarket._listings(datetime(2026, 11, 19, 8, tzinfo=t.KST), root)['data'], [])  # listed already

        failing = Mock(side_effect=OSError)
        other = Path(tempfile.mkdtemp())
        self.assertEqual(new_listings.upcoming_cache(other, NOW, failing)['rows'], [])
        self.assertFalse(json.loads((other / new_listings.UPCOMING_FILE).read_text())['ok'])
        new_listings.upcoming_cache(other, NOW.replace(minute=40), failing)
        self.assertEqual(failing.call_count, 1)  # within 15 minutes: not retried
        new_listings.upcoming_cache(other, NOW.replace(minute=50), failing)
        self.assertEqual(failing.call_count, 2)

    def test_no_key_is_unavailable_not_a_crash(self):
        root = Path(tempfile.mkdtemp())
        self.assertEqual(new_listings.upcoming_cache(root, NOW)['rows'], [])  # conftest blanks the Toss key
        self.assertEqual(json.loads((root / new_listings.UPCOMING_FILE).read_text())['error'], 'RuntimeError')


if __name__ == '__main__':
    unittest.main()


class StockPressureTests(unittest.TestCase):
    def test_latest_record_on_or_before_the_day_with_balance_change(self):
        answers = {
            'short-selling': {'records': [{'date': '2026-11-17', 'shortSellingAmount': '1'},
                                          {'date': '2026-11-16', 'updatedAt': 'u', 'shortSellingVolume': '390078',
                                           'shortSellingAmount': '77860223250', 'shortSellingVolumeRate': '0.02413',
                                           'shortSellingAmountRate': None}]},
            'credit-trades': {'records': [{'date': '2026-11-16', 'marginLoan': {'balanceQuantity': '100', 'balanceRate': '0.0028',
                                                                               'tradingRate': '0.0542'}, 'stockLoan': None},
                                          {'date': '2026-11-13', 'marginLoan': {'balanceQuantity': '130'}}]},
        }

        def get(path, params=None):
            kind = path.rsplit('/', 1)[1]
            if kind == 'securities-lending':
                raise t.TossError('HTTP 500')
            self.assertEqual(params['until'], '2026-11-16')
            return answers[kind]
        with patch.object(t, 'get', autospec=True, side_effect=get):
            got = t.stock_pressure('005930', '2026-11-16')
        self.assertEqual(got['short'], {'date': '2026-11-16', 'updated_at': 'u', 'volume': 390078.0, 'amount_100m': 778.6,
                                        'volume_rate_pct': 2.413, 'amount_rate_pct': None})
        self.assertEqual((got['credit']['margin_balance'], got['credit']['margin_balance_change'],
                          got['credit']['margin_balance_rate_pct'], got['credit']['stock_loan_balance']), (100.0, -30.0, 0.28, None))
        self.assertEqual(got['lending'], {'error': 'TossError'})

    def test_journal_skips_without_key_and_keeps_old_on_rebuild(self):
        import trading_journal as tj
        self.assertEqual(tj.stock_pressure('2026-11-16', ['005930']), {})  # conftest blanks the Toss key
        old = {'day': '2026-11-16', 'flows': {'pressure': {'005930': {'short': {'date': '2026-11-16'}}}}}
        db = Path(tempfile.mkdtemp()) / 'journal.sqlite3'  # never the live journal DB (rebuild stores)
        with patch.object(tj, 'build', return_value={'day': '2026-11-16', 'flows': {}}):
            tj.store(old, db)
            tj.rebuild('2026-11-16', db)
        new = tj.load('2026-11-16', db)
        self.assertEqual(new['flows']['pressure'], old['flows']['pressure'])
        md = tj.report_md(new)
        self.assertIn('### 매매 종목 공매도·신용·대차 (토스증권)', md)


class MarketInvestorTests(unittest.TestCase):
    def test_net_by_investor_and_institution_kind(self):
        both = lambda b, s: {'buyAmount': str(b), 'sellAmount': str(s)}  # noqa: E731
        record = {'date': '2026-11-16', 'updatedAt': 'u', 'individual': both(3e11, 1e11), 'foreigner': both(1e11, 2e11),
                  'institution': {**both(5e10, 6e10), 'breakdown': {'pensionFund': both(1e10, 3e10), 'trust': both(4e10, 3e10)}},
                  'otherCorporation': None}
        with patch.object(t, 'get', autospec=True, return_value={'records': [{'date': '2026-11-17'}, record]}) as get:
            got = t.market_investors('KOSPI', '2026-11-16')
        self.assertEqual(get.call_args.args[1], {'interval': '1d', 'count': '3', 'until': '2026-11-16'})
        self.assertEqual((got['개인'], got['외국인'], got['기관'], got['기타법인']), (2000.0, -1000.0, -100.0, None))
        self.assertEqual((got['institution_detail']['연기금'], got['institution_detail']['투신'], got['institution_detail']['은행']),
                         (-200.0, 100.0, None))
        with patch.object(t, 'get', autospec=True, return_value={'records': [{'date': '2026-11-13'}]}):
            self.assertIsNone(t.market_investors('KOSPI', '2026-11-16'))

    def test_journal_detail_beside_naver_and_kept_on_rebuild(self):
        import trading_journal as tj
        self.assertIsNone(tj.investor_detail('KOSPI', '2026-11-16'))  # no Toss key in tests
        detail = {'date': '2026-11-16', 'updated_at': '2026-11-16T20:06:03+09:00', '기관': -3686.9, '기타법인': 4857.4,
                  'institution_detail': {'금융투자': 6201.1, '연기금': -5647.8}}
        md = tj.report_md({'day': '2026-11-16', 'flows': {'market': {'KOSPI': {'개인': 1.0, 'detail': detail}}}})
        self.assertIn('| 코스피 | **+6,201** |', md)
        self.assertIn('**-5,648** | **-3,687** | **+4,857** | 11-16 20:06 |', md)
        db = Path(tempfile.mkdtemp()) / 'journal.sqlite3'
        with patch.object(tj, 'build', return_value={'day': '2026-11-16', 'flows': {'market': {'KOSPI': {'개인': 2.0}}}}):
            tj.store({'day': '2026-11-16', 'flows': {'market': {'KOSPI': {'detail': detail}}}}, db)
            tj.rebuild('2026-11-16', db)
        self.assertEqual(tj.load('2026-11-16', db)['flows']['market']['KOSPI'], {'개인': 2.0, 'detail': detail})


class InvestorRankingTests(unittest.TestCase):
    def test_leaders_in_100m_krw_with_names_and_sells_negative(self):
        ranking = {'foreigner': {'rankedAt': '2026-11-17T12:04:36+09:00',
                                 'netBuyRankings': [{'rank': 1, 'symbol': '009150', 'netBuyAmount': '30020000000'}],
                                 'netSellRankings': [{'rank': 1, 'symbol': '005930', 'netSellAmount': '375140000000'}]},
                   'institution': {'rankedAt': None, 'netBuyRankings': [], 'netSellRankings': []}}

        def get(path, params=None):
            if path == '/api/v1/rankings/investor-trading':
                self.assertEqual(params, {'count': '20'})
                return ranking
            self.assertEqual(params['symbols'], '005930,009150')
            return [{'symbol': '005930', 'name': '삼성전자'}, {'symbol': '009150', 'name': '삼성전기'}]
        with patch.object(t, 'get', autospec=True, side_effect=get):
            got = t.investor_rankings()
        self.assertEqual(got['외국인'], {'ranked_at': '2026-11-17T12:04:36+09:00',
                                       'net_buy': [{'code': '009150', 'net_100m': 300.2, 'name': '삼성전기'}],
                                       'net_sell': [{'code': '005930', 'net_100m': -3751.4, 'name': '삼성전자'}]})
        self.assertEqual((got['기관']['net_buy'], got['개인']['net_sell']), ([], []))

    def test_journal_keeps_captured_leaders_on_rebuild(self):
        import trading_journal as tj
        self.assertEqual(tj.investor_leaders('2026-11-17'), {})  # no Toss key in tests
        leaders = {'외국인': {'ranked_at': '2026-11-17T15:40:00+09:00', 'net_buy': [{'code': '009150', 'name': '삼성전기', 'net_100m': 300.2}],
                            'net_sell': []}}
        db = Path(tempfile.mkdtemp()) / 'journal.sqlite3'
        with patch.object(tj, 'build', return_value={'day': '2026-11-17', 'flows': {}}):
            tj.store({'day': '2026-11-17', 'flows': {'investor_rankings': leaders}}, db)
            tj.rebuild('2026-11-17', db)
        kept = tj.load('2026-11-17', db)
        self.assertEqual(kept['flows']['investor_rankings'], leaders)
        self.assertIn('| 외국인 | [삼성전기](', tj.report_md(kept))


class RecentListingTests(unittest.TestCase):
    def test_recent_common_stocks_newest_first_within_the_lookback(self):
        stocks = [{'symbol': '493260', 'name': '옵토닉스', 'securityType': 'STOCK', 'isCommonShare': True, 'listDate': '2026-11-17', 'market': 'KOSDAQ'},
                  {'symbol': '0147B0', 'name': '래블업', 'securityType': 'STOCK', 'isCommonShare': True, 'listDate': '2026-11-03', 'market': 'KOSDAQ'},
                  {'symbol': '005930', 'name': '삼성전자', 'securityType': 'STOCK', 'isCommonShare': True, 'listDate': '1975-06-11', 'market': 'KOSPI'},
                  {'symbol': '498390', 'name': '한화플러스제5호스팩', 'securityType': 'STOCK', 'isCommonShare': True, 'listDate': '2026-11-10', 'market': 'KOSDAQ'},
                  {'symbol': '471800', 'name': '에이엘로봇', 'securityType': 'STOCK', 'isCommonShare': True, 'listDate': '2026-11-18', 'market': 'KOSDAQ'}]

        def get(path, params=None):
            if path == '/api/v1/stocks/all':
                self.assertEqual((params['status'], params['securityType']), ('ACTIVE', 'STOCK'))
                return [{'symbol': s['symbol']} for s in stocks if s['market'] == params['market']]
            return [s for s in stocks if s['symbol'] in params['symbols'].split(',')]
        with patch.object(t, 'get', autospec=True, side_effect=get):
            rows = t.recent_listings(NOW, 14)
        self.assertEqual([(r['code'], r['listed']) for r in rows], [('493260', '20261117'), ('0147B0', '20261103')])



class GetTests(unittest.TestCase):
    def test_revoked_token_retried_once_with_a_fresh_one(self):
        tokens = []
        def token(stale=None):
            tokens.append(stale)
            return 'fresh' if stale else 'cached'
        answers = [Mock(status_code=401), Mock(status_code=200, json=lambda: {'result': [1]})]
        with patch.object(t, '_token', side_effect=token), patch('requests.get', side_effect=answers) as get:
            self.assertEqual(t.get('/api/v1/stocks', {}), [1])
        self.assertEqual(tokens, [None, 'cached'])
        self.assertEqual(get.call_args.kwargs['headers']['Authorization'], 'Bearer fresh')


def test_stock_minutes_reads_dated_one_minute_candles(monkeypatch):
    """11-19: Toss 1-minute candles carry the NXT pre-market (08:00~08:50) with timestamps."""
    import toss_data as t
    seen = []
    monkeypatch.setattr(t, 'get', lambda path, params=None: seen.append((path, params)) or {'candles': [
        {'timestamp': '2026-11-18T08:02:00.000+09:00', 'openPrice': '197800', 'highPrice': '197900', 'lowPrice': '197700',
         'closePrice': '197800', 'volume': '1000'},
        {'timestamp': '2026-11-18T08:01:00.000+09:00', 'openPrice': '197500', 'highPrice': '197800', 'lowPrice': '197500',
         'closePrice': '197800', 'volume': '399573'},
        {'timestamp': 'bad', 'closePrice': '1'}]})
    bars = t.stock_minutes('005930')
    assert seen[0][0] == '/api/v1/candles' and seen[0][1]['symbol'] == '005930' and seen[0][1]['interval'] == '1m'
    assert [(b[0].strftime('%H:%M'), b[4], b[5]) for b in bars] == [('08:01', 197800.0, 399573.0), ('08:02', 197800.0, 1000.0)]
    import importlib; assert '/api/v1/candles'.startswith(importlib.reload(t).ALLOWED)
