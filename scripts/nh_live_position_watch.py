"""Read-only KODEX inverse position monitor; never submits or cancels orders."""
import json
import hashlib
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import dotenv_values
from src.config.loader import load_agent_config
from src.tools.mcp import MCPServerAdapter
from nh_live_broker import _pages
from nh_live_loss_preflight import evaluate
from src.config.paths import get_runtime_root
from src.live.halt import halt_flag_set
from nh_live_policy import decide, record

KST = ZoneInfo('Asia/Seoul')
ROOT = Path.home()/'.vibe-trading/nhplug/etf-experiment'

def order_pages(adapter, account, now):
    rows=[]; cursor=None; flag=None; seen=set()
    for _ in range(20):
        request={'operationId':'krstockInquiryDailyOrderExecution','input':{'act_no':account,'orr_dt':now.strftime('%Y%m%d'),'orr_mkt_cd':'00','ost_cns_dit':'0'},'want_pagination':True}
        if cursor: request.update(cts=cursor,cts_flag=flag)
        response=adapter.call_tool('call_api',request); data=response.get('data',{}); meta=data.get('_nh_pagination',{})
        message=data.get('rsp_msg','').strip().rstrip('.')
        accepted={('00218','계속 조회시 다음(연속조회) 버튼을 누르시기 바랍니다'),('00166','조회가 완료되었습니다'),('00000','조회가 완료되었습니다'),('11512','데이터가 존재하지 않습니다')}
        if response.get('status')!='ok' or (data.get('rsp_cd'),message) not in accepted: raise ValueError('order response unavailable')
        page=data.get('Output_0',[])
        if not isinstance(page,list): raise ValueError('invalid order rows')
        rows.extend(page)
        if meta.get('complete') is True: return rows
        if meta.get('complete') is not False: raise ValueError('pagination unknown')
        cursor,flag=meta.get('cts'),meta.get('cts_flag')
        if not cursor or not flag or (cursor,flag) in seen: raise ValueError('pagination cursor invalid')
        seen.add((cursor,flag))
    raise ValueError('pagination limit')

def snapshot(adapter, account, now):
    args={'act_no':account,'bnc_bse_cd':'5','ltg_aot_dit_cd':'9','aet_bse':'2','qut_dit_cd':'UNT','aly_qut_cd':'1'}
    balance=_pages(adapter,'krstockInquiryBalance',args,'Output_1')
    history=order_pages(adapter,account,now)
    quote=adapter.call_tool('get_stock_price',{'stock_code':'114800'})
    if quote.get('status')!='ok' or not isinstance(quote.get('data',{}).get('Output_0'),dict): raise ValueError('quote unavailable')
    q=quote['data']['Output_0']
    if q.get('iem_cd')!='114800': raise ValueError('quote symbol mismatch')
    positions=[z for z in balance['Output_1'] if z.get('iem_cd')=='114800']
    if any(type(z.get('rsdl_qty')) not in (int,float) or z['rsdl_qty']<0 for z in positions): raise ValueError('invalid position')
    rows=[z for z in history if z.get('iem_cd')=='114800']
    if any(type(z.get('ny_cns_qty')) is not int or z['ny_cns_qty']<0 for z in rows): raise ValueError('invalid order state')
    pnl=adapter.call_tool('call_api',{'operationId':'krstockInquiryRealizedPnl','input':{'act_no':account,'iqr_dit_cd1':'0','fee_dit_cd':'1','qut_dit_cd':'KRX','aly_qut_cd':'1'}})
    if pnl.get('status')!='ok' or not isinstance(pnl.get('data'),dict): raise ValueError('loss unavailable')
    scope=hashlib.sha256(('nh-live:'+account).encode()).hexdigest()
    risk=evaluate(pnl['data'],now,scope,get_runtime_root()/'nhplug')
    return {'time':now.isoformat(),'event':'live_readonly','symbol':'114800','shares':sum(z['rsdl_qty'] for z in positions),'open_order_rows':sum(z['ny_cns_qty']>0 for z in rows),'price':q.get('stck_prpr'),'quote_time':q.get('hoga_bsop_hour'),'market_status':q.get('market_status'),'risk':risk}

def main():
    os.umask(0o077);ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
    from local_env import ENV;v=dotenv_values(ENV,interpolate=False);account=''.join(c for c in (v.get('ACCOUNT_NUMBER') or '') if c.isdigit())
    if len(account)!=11: raise ValueError('account unavailable')
    adapter=MCPServerAdapter('nhplug_readonly',load_agent_config().mcp_servers['nhplug_readonly'])
    while True:
        now=datetime.now(KST)
        if now.weekday()>=5 or not '09:00'<=now.strftime('%H:%M')<'15:05': break
        try:
            event=snapshot(adapter,account,now)
            event['policy']=decide(event,halted=halt_flag_set('nhplug'),transport_ready=False)
            record(ROOT/'live-policy.sqlite3',event,event['policy'])
        except Exception as exc: event={'time':now.isoformat(),'event':'live_readonly_error','error_type':type(exc).__name__}
        with (ROOT/'live-watch.jsonl').open('a') as f:f.write(json.dumps(event,ensure_ascii=False)+'\n')
        print(json.dumps(event,ensure_ascii=False),flush=True)
        time.sleep(60)
if __name__=='__main__':main()
