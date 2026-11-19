import unittest
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch
import nh_etf_paper_runner as r

class RunnerTests(unittest.TestCase):
    def test_volatility_range_and_invalid_prices(self):
        bars = [{'stck_hgpr':'105', 'stck_lwpr':'100'}, {'stck_hgpr':'110', 'stck_lwpr':'102'}]
        self.assertAlmostEqual(r.volatility(bars), 10.0)
        with self.assertRaises(ValueError): r.volatility([{'stck_hgpr':'100', 'stck_lwpr':'0'}])
        with self.assertRaises(ValueError): r.volatility([{'stck_hgpr':'NaN', 'stck_lwpr':'90'}])

    def test_decision_includes_every_instrument(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, *args):
                return json.dumps({'answers':[{'type':'choice','choice':'SAMSUNG','confidence':0.9}], 'usage':{}}).encode()
        def request(req, timeout):
            payload = json.loads(req.data)
            self.assertEqual({item['value'] for item in payload['questions'][0]['choices']}, set(r.SYMBOLS) | {'WAIT'})
            return Response()
        with patch.object(r, 'dotenv_values', return_value={'OPENAI_API_KEY':'test'}), patch.object(r.urllib.request, 'urlopen', side_effect=request):
            self.assertEqual(r.decision({}, [])[0], 'SAMSUNG')

    def test_bars_exclude_recent_and_reject_stale(self):
        now = datetime(2026, 11, 6, 11, 0, 30, tzinfo=ZoneInfo('Asia/Seoul'))
        rows = []
        for i in range(10):
            t = now - timedelta(minutes=i)
            rows.append(dict(bsop_date=t.strftime('%Y%m%d'), bsop_time=t.strftime('%H%M00'),
                             stck_oprc='100', stck_hgpr='101', stck_lwpr='99', stck_prpr='100', vol='1'))
        d = {'rsp_cd':'00000', 'Output_0':{'iem_cd':'069500'}, 'Output_1':rows}
        self.assertEqual(r.valid_bars(d, '069500', now)[0]['bsop_time'], '105800')
        with self.assertRaises(ValueError): r.valid_bars(d, '114800', now)
        with self.assertRaises(ValueError): r.valid_bars(d, '069500', now + timedelta(minutes=10))

    def test_order_settlement_waits_for_terminal(self):
        result = {'itg_orr_no': 10, 'iem_cd':'069500', 'tot_cns_qty':1, 'ny_cns_qty':0, 'can_qty':0}
        with patch.object(r, 'order_rows', return_value=[result]), patch.object(r.time, 'sleep'), patch.object(r, 'log'):
            self.assertEqual(r.settle(None, 'hidden', '20261106', '069500', 10)['tot_cns_qty'], 1)

    def test_order_settlement_cancels_unfilled(self):
        pending = {'itg_orr_no':10, 'iem_cd':'069500', 'tot_cns_qty':0, 'ny_cns_qty':1, 'can_qty':0}
        cancelled = {**pending, 'ny_cns_qty':0, 'can_qty':1}
        with patch.object(r, 'order_rows', side_effect=[pending and [pending]]*6 + [[cancelled]]), patch.object(r.time, 'sleep'), patch.object(r, 'log'), patch.object(r, 'api') as api:
            self.assertEqual(r.settle(None, 'hidden', '20261106', '069500', 10)['can_qty'], 1)
            self.assertEqual(api.call_args.args[2], 'krstockOrderCancel')

if __name__ == '__main__': unittest.main()
