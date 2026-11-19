import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import new_listings as n
import nh_paper_isolated as p

KST = n.KST
HTML = '''<table><tr><th>회사명</th><th>종목코드</th><th>상장일</th><th>상장유형</th><th>증권구분</th></tr>
<tr><td>넥스아이</td><td>493410</td><td>2026-11-12</td><td>신규상장</td><td>주권</td></tr>
<tr><td>바로팜</td><td>0153R0</td><td>2026-11-11</td><td>신규상장</td><td>주권</td></tr>
<tr><td>아이엠스팩1호</td><td>0222E0</td><td>2026-11-04</td><td>신규상장</td><td>주권</td></tr>
<tr><td>어떤리츠</td><td>123450</td><td>2026-11-05</td><td>신규상장</td><td>투자회사</td></tr>
<tr><td>이전상장</td><td>234560</td><td>2026-11-06</td><td>이전상장</td><td>주권</td></tr></table>'''


class NewListingTests(unittest.TestCase):
    def test_parse_keeps_new_common_stock_listings_and_letter_codes_only(self):
        self.assertEqual(n.parse(HTML), [{'name': '넥스아이', 'code': '493410', 'listed': '20261112'},
                                         {'name': '바로팜', 'code': '0153R0', 'listed': '20261111'}])

    def test_list_is_fetched_once_a_day_and_retried_after_a_failure(self):
        now = datetime(2026, 11, 12, 9, 30, tzinfo=KST)
        calls = []
        def fetcher(at):
            calls.append(at)
            return n.parse(HTML)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(n.recent(d, now, fetcher)[0]['code'], '493410')
            n.recent(d, now.replace(minute=40), fetcher)
            self.assertEqual(len(calls), 1)
            failing = lambda at: (_ for _ in ()).throw(OSError('down'))
            self.assertEqual(n.recent(d, now.replace(day=13), failing), [])
            self.assertEqual(n.recent(d, now.replace(day=13, minute=35), fetcher), [])        # within the retry wait
            self.assertEqual(len(n.recent(d, now.replace(day=13, minute=50), fetcher)), 2)  # retried

    def test_toss_stands_in_when_kind_fails_and_kind_is_retried(self):
        now = datetime(2026, 11, 17, 9, 30, tzinfo=KST)
        toss = [{'name': '옵토닉스', 'code': '493260', 'listed': '20261117', 'market': 'KOSDAQ'},
                {'name': '넥스아이', 'code': '493410', 'listed': '20261112', 'market': 'KOSDAQ'}]
        failing = lambda at: (_ for _ in ()).throw(OSError('down'))  # noqa: E731
        calls = []
        def fallback(at):
            calls.append(at)
            return toss
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual([r['code'] for r in n.recent(d, now, failing, fallback)], ['493260', '493410'])
            cache = __import__('json').loads((Path(d) / n.FILE).read_text())
            self.assertEqual((cache['ok'], cache['source'], cache['error']), (False, 'Toss Open API', 'OSError'))
            self.assertEqual(len(n.recent(d, now.replace(minute=40), failing, fallback)), 2)  # cached within the retry wait
            self.assertEqual(len(calls), 1)
            self.assertEqual(n.recent(d, now.replace(minute=50), lambda at: n.parse(HTML), fallback)[0]['code'], '493410')  # KIND back
            self.assertEqual(len(calls), 1)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(n.recent(d, now, failing), [])  # no Toss key in tests: nothing, as before
            cache = __import__('json').loads((Path(d) / n.FILE).read_text())
            self.assertEqual(cache['fallback_error'], 'RuntimeError')

    def test_ipo_plan_has_its_own_levels_and_is_kept(self):
        with patch.multiple(p, IPO_STOP_PCT=3.0, IPO_HALF_PCT=4.0, IPO_TRAIL_PCT=2.0):
            plan = p.new_plan('0153R0', 'ipo')
        levels = p.plan_levels(plan)
        self.assertEqual((float(levels['stop']), float(levels['half']), levels['all'], float(levels['trail'])), (0.97, 1.04, None, 0.02))
        with tempfile.TemporaryDirectory() as d:
            p.write_exit_plans(d, {'0153R0': plan})
            self.assertEqual(p.exit_plans(d)['0153R0']['profile'], 'ipo')

    def test_ipo_candidate_needs_confirmed_listing_and_strength(self):
        now = datetime(2026, 11, 12, 10, 0, tzinfo=KST)
        quote = {'iem_cd': '493410', 'stck_lstn_date': '20261112', 'stck_prpr': 8600, 'stck_prdy_clpr': 8000,
                 'stck_oprc': 8200, 'stck_hgpr': 8800, 'acml_vol': 9_000_000, 'lstn_stcn_unit0': 45_000_000}
        class Read:
            def __init__(self, q): self.q = q
            def call_tool(self, tool, params): return {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': self.q}}
        rows = [{'stck_prpr': str(8600 - i * 10), 'vol': str(200 if i < 5 else 100)} for i in range(10)]
        listing = [{'name': '넥스아이', 'code': '493410', 'listed': '20261112'}]
        with tempfile.TemporaryDirectory() as d, patch('new_listings.recent', return_value=listing), \
             patch.object(p, 'valid_bars', return_value=rows), patch.object(p, 'minute_bars', return_value={'data': {}}), \
             patch.multiple(p, IPO_DAYS=2, IPO_NEAR_HIGH_PCT=3.0):
            code, facts, _ = p.ipo_candidate(Read(quote), d, {}, now)
            self.assertEqual((code, facts['sessions'], facts['change_pct'], facts['turnover_pct']), ('493410', 0, 7.5, 20.0))
            cases = {'below_base': {'stck_prpr': 7900, 'stck_oprc': 7800}, 'below_open': {'stck_oprc': 8700},
                     'off_high': {'stck_hgpr': 9000}, 'listing_unconfirmed': {'stck_lstn_date': '20261111'}}
            for why, change in cases.items():
                with self.subTest(why=why):
                    self.assertEqual(p.ipo_candidate(Read({**quote, **change}), d, {}, now)[1], why)
            self.assertEqual(p.ipo_candidate(Read(quote), d, {}, now.replace(hour=9, minute=15))[1], 'ipo_too_early')
            self.assertEqual(p.ipo_candidate(Read(quote), d, {}, now.replace(day=16))[1], 'no_new_listing')  # 2 sessions later
            p.write_exit_plans(d, {'0153R0': {**p.new_plan('0153R0', 'ipo')}})
            self.assertEqual(p.ipo_candidate(Read(quote), d, {'0153R0': 3}, now)[1], 'ipo_already_held')


if __name__ == '__main__':
    unittest.main()
