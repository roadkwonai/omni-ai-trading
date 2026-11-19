import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from nh_live_position_watch import order_pages
import nh_live_position_watch as w
from unittest.mock import patch
class TestPages(unittest.TestCase):
 def test_continuation_and_missing_metadata(self):
  class Adapter:
   def __init__(self): self.n=0
   def call_tool(self,tool,args):
    self.n+=1
    if self.n==1:return {'status':'ok','data':{'rsp_cd':'00218','rsp_msg':'계속 조회시 다음(연속조회) 버튼을 누르시기 바랍니다.','Output_0':[{'ny_cns_qty':1}],'_nh_pagination':{'complete':False,'cts':'c','cts_flag':'Y'}}}
    return {'status':'ok','data':{'rsp_cd':'00166','rsp_msg':'조회가 완료되었습니다.','Output_0':[],'_nh_pagination':{'complete':True}}}
  self.assertEqual(len(order_pages(Adapter(),'hidden',datetime.now(ZoneInfo('Asia/Seoul')))),1)
  class Bad:
   def call_tool(self,*args):return {'status':'ok','data':{'rsp_cd':'00166','rsp_msg':'조회가 완료되었습니다.','Output_0':[]}}
  with self.assertRaises(ValueError):order_pages(Bad(),'hidden',datetime.now(ZoneInfo('Asia/Seoul')))
 def test_snapshot_records_risk_and_rejects_missing_pnl(self):
  class Adapter:
   def call_tool(self,tool,args):
    if tool=='get_stock_price':return {'status':'ok','data':{'Output_0':{'iem_cd':'114800','stck_prpr':1300}}}
    return {'status':'ok','data':{'rsp_cd':'00166','rsp_msg':'조회가 완료되었습니다.','Output_0':{'sby_pls_amt':-20000,'sdr_xps':0,'eal_pls_amt':0}}}
  import tempfile
  from pathlib import Path
  with tempfile.TemporaryDirectory() as root, patch.object(w,'_pages',return_value={'Output_1':[]}), patch.object(w,'order_pages',return_value=[]), patch.object(w,'get_runtime_root',return_value=Path(root)):
   result=w.snapshot(Adapter(),'12345678901',datetime.now(ZoneInfo('Asia/Seoul')))
   self.assertTrue(result['risk']['halt_latched'])
   self.assertFalse(result['risk']['live_orders_enabled'])
if __name__=='__main__':unittest.main()
