"""End-to-end order lifecycle with an in-memory broker; no network access."""
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo
import pytest
from src.tools.nhplug_live_guard import LiveOrderGuard
import nh_live_auto as auto

ACCOUNT='12345678901'
NOW=datetime(2026,11,6,13,0,10,tzinfo=ZoneInfo('Asia/Seoul'))

@pytest.mark.parametrize('new_bid', [1279, 1280])
def test_external_buys_cancel_sell_reprice_and_fill(tmp_path,monkeypatch,new_bid):
 monkeypatch.setenv('VIBE_TRADING_HOME',str(tmp_path/'runtime'))
 guard=LiveOrderGuard(ACCOUNT,tmp_path/'guard')
 def row(number,side='현금매수',filled=0,remaining=1,cancelled=0):
  return {'itg_orr_no':number,'iem_cd':'114800','sby_dit_cd_nm':side,'orr_rjt_rsn_cd_nm':'정상','orr_qty':1,'tot_cns_qty':filled,'ny_cns_qty':remaining,'can_qty':cancelled}
 history={'rsp_cd':'00166','rsp_msg':'조회가 완료되었습니다.','_account':ACCOUNT,'_day':'20261106','_pagination_complete':True,'Output_0':[row(1),row(2)]}
 balance={'rsp_cd':'00166','rsp_msg':'조회가 완료되었습니다.','_account':ACCOUNT,'_pagination_complete':True,'Output_0':{'orr_pbl_amt1':1000000,'tot_aet_amt':1000000,'tot_byn_amt':1309},'Output_1':[{'iem_cd':'114800','rsdl_qty':1,'phs_pr':1309}]}
 sellable={'rsp_cd':'00166','_account':ACCOUNT,'_symbol':'114800','sll_pbl_qty':1}
 quote={'status':'ok','data':{'rsp_cd':'00000','Output_0':{'iem_cd':'114800','market_status':'1','hoga_bsop_hour':'13:00:09','bidp':1280,'askp':1281}}}
 previous={'status':'ok','data':{'rsp_cd':'00000','Output_0':{'iem_cd':'114800','market_status':'1','hoga_bsop_hour':'13:00:07','bidp':1280,'askp':1281}}}
 snap={'accounts':{'rsp_cd':'00000','Output_0':[{'acct_no':ACCOUNT,'acct_type':'01'}]},'history':history,'balance':balance,'pnl':{'rsp_cd':'00166','rsp_msg':'조회가 완료되었습니다.','Output_0':{'sby_pls_amt':0,'sdr_xps':0,'eal_pls_amt':0}},'quote':quote,'previous_quote':previous}
 guard.adopt_existing(now=NOW,balance=balance,sellable=sellable,quantity=1)
 calls=[]
 def submit(reader,g,credentials,op,fields,*,replacement_of=None):
  data={**snap,'sellable':sellable};fp=g.reserve(op,fields,now=NOW,replacement_of=replacement_of,**data);num=100+len(calls)
  calls.append((op,dict(fields),fp,num));msg={'krstockOrderCancel':('00192','취소'),'krstockOrderCashSell':('00047','매도')}[op]
  g.acknowledge(fp,{'rsp_cd':msg[0],'rsp_msg':msg[1]+'주문이 완료되었습니다','Output_0':{'mkt_orr_no':num}});return fp
 with patch.object(auto,'collect_snapshots',return_value=snap),patch.object(auto,'datetime') as clock,patch.object(auto,'halt_flag_set',return_value=False),patch('src.tools.nhplug_live_guard.halt_flag_set',return_value=False),patch.object(auto,'execute',side_effect=submit):
  clock.now.return_value=NOW
  for number in (1,2):
   result=auto.step(None,guard,{},True,True)
   assert result['execution']=='CANCEL_ACKNOWLEDGED_NOT_RECONCILED'
   original=next(x for x in history['Output_0'] if x['itg_orr_no']==number);original.update(ny_cns_qty=0,can_qty=1)
   auto.step(None,guard,{},True,True)
  result=auto.step(None,guard,{},True,True)
  assert calls[-1][0]=='krstockOrderCashSell'
  sell_no=calls[-1][3];history['Output_0'].append(row(sell_no,side='현금매도'))
  with guard._db() as db:db.execute('UPDATE observed_pending SET first_seen=0')
  auto.step(None,guard,{},True,True)
  with guard._db() as db:db.execute('UPDATE observed_pending SET first_seen=0')
  result=auto.step(None,guard,{},True,True)
  assert calls[-1][0]=='krstockOrderCancel'
  original=next(x for x in history['Output_0'] if x['itg_orr_no']==sell_no);original.update(ny_cns_qty=0,can_qty=1)
  auto.step(None,guard,{},True,True)
  quote['data']['Output_0'].update(bidp=new_bid,askp=new_bid+1)
  auto.step(None,guard,{},True,True)
  assert calls[-1][0]=='krstockOrderCashSell' and calls[-1][1]['orr_pr']==new_bid
  sell_no=calls[-1][3];history['Output_0'].append(row(sell_no,side='현금매도',filled=1,remaining=0))
  balance['Output_1'][0]['rsdl_qty']=0
  auto.step(None,guard,{},True,True)
  with guard._db() as db:assert guard._owned(db)==0
