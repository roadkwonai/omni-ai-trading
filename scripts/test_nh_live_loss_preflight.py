import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import nh_live_loss_preflight as x

NOW = datetime(2026, 11, 6, 10, tzinfo=ZoneInfo('Asia/Seoul'))
def snap(realized=0, fees=0, unrealized=0):
    return {'rsp_cd':'00166','rsp_msg':'조회가 완료되었습니다.', 'Output_0':{'sby_pls_amt':realized,'sdr_xps':fees,'eal_pls_amt':unrealized}}

class LiveLossPreflightTests(unittest.TestCase):
    def test_order_summary_requires_valid_rows_and_complete_pagination(self):
        empty={'rsp_cd':'11512','rsp_msg':'데이터가 존재하지 않습니다.', '_nh_pagination':{'complete':True}}
        self.assertEqual(x.summarize_orders(empty)['open_orders'], [])
        self.assertTrue(x.summarize_orders(empty)['pagination_complete'])
        with self.assertRaises(ValueError): x.summarize_orders({**empty, '_nh_pagination':{'complete':None}})
        data={'rsp_cd':'00166','rsp_msg':'조회가 완료되었습니다.',
              '_nh_pagination':{'complete':True}, 'Output_0':[{'iem_cd':'069500','orr_qty':1,
                                                             'tot_cns_qty':0,'ny_cns_qty':1,'can_qty':0}]}
        self.assertEqual(x.summarize_orders(data)['open_orders'][0]['remaining'], 1)
        data['Output_0'][0]['ny_cns_qty'] = '0'
        with self.assertRaises(ValueError): x.summarize_orders(data)
        with self.assertRaises(ValueError): x.summarize_orders({'rsp_cd':'error','Output_0':[]})

    def test_threshold_persists_for_day_and_account(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            self.assertFalse(x.evaluate(snap(-19999), NOW, 'a', root)['halt_latched'])
            self.assertTrue(x.evaluate(snap(-19999,1), NOW, 'a', root)['halt_latched'])
            self.assertTrue(x.evaluate(snap(), NOW, 'a', root)['halt_latched'])
            self.assertFalse(x.evaluate(snap(), NOW, 'b', root)['halt_latched'])
            self.assertFalse(x.evaluate(snap(), NOW+timedelta(days=1), 'a', root)['halt_latched'])

    def test_invalid_broker_data_never_produces_allowance(self):
        with tempfile.TemporaryDirectory() as t:
            for data in ({}, snap('NaN'), snap(fees=-1), snap(realized=None)):
                with self.assertRaises(ValueError): x.evaluate(data, NOW, 'a', Path(t))

if __name__ == '__main__': unittest.main()
