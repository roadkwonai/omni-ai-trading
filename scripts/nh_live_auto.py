"""One-share inverse policy loop using the guarded live entrypoint.

Default is observation. --execute still obeys the global HALT and pending intents.
"""
import argparse
import hashlib
import json
import math
import os
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import dotenv_values
from src import file_lock
from src.config.loader import load_agent_config
from src.config.paths import get_runtime_root
from src.live.halt import halt_flag_set
from src.tools.mcp import MCPServerAdapter
from src.tools.nhplug_live_guard import LiveOrderGuard, LiveOrderBlocked
from nh_guarded_live import ENV, execute
from nh_live_broker import collect_snapshots
from nh_live_buy_control import buy_allowed
from nh_live_strategy import intent, VERSION
from accounts import live_price_cap
from nh_etf_paper_runner import valid_bars
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')
KST=ZoneInfo('Asia/Seoul')
CHECK_INTERVAL_SECONDS=30
PENDING_CANCEL_SECONDS=30


def ai_signal(path, scope, now, *, with_digest=False, expected_digest=None):
    """Read a recent, account-bound observation; any uncertainty means no signal."""
    try:
        if now.tzinfo is None or now.utcoffset() is None:return None
        now=now.astimezone(KST)
        if now.weekday()>=5 or not '09:10'<=now.strftime('%H:%M')<'15:00':return None
        with open(path, 'rb') as stream:
            raw=stream.read(65537)
        if len(raw)>65536:return None
        digest=hashlib.sha256(raw).hexdigest()
        if expected_digest is not None and digest!=expected_digest:return None
        data=json.loads(raw)
        stamp=datetime.fromisoformat(data['time'])
        if stamp.tzinfo is None or stamp.utcoffset() is None or not 0 <= (now-stamp).total_seconds() <= 30:
            return None
        if data.get('mode')!='live-observation' or data.get('scope')!=scope or data.get('symbol')!='114800':
            return None
        for name in ('K200','INV'):
            bar=data['bar_time'][name]
            if not isinstance(bar,str) or not re.fullmatch(r'\d{6}',bar):return None
            observed=datetime.strptime(now.strftime('%Y%m%d')+bar,'%Y%m%d%H%M%S').replace(tzinfo=KST)
            if not timedelta(0)<=now-observed<=timedelta(minutes=4):return None
        decision,jev=data['decision'],data['jev']
        if not isinstance(decision,dict) or not isinstance(jev,dict) or jev.get('model')!='jev-1.14.0':return None
        if not isinstance(jev.get('chart_sha256'),str) or not re.fullmatch(r'[0-9a-f]{64}',jev['chart_sha256']):return None
        for item in (decision,jev):
            score=item.get('confidence')
            if item.get('choice') not in ('INV','K200') or type(score) not in (int,float) or not math.isfinite(score) or not 0.8<=score<=1:return None
            answered=datetime.fromisoformat(item['time'])
            if answered.tzinfo is None or answered.utcoffset() is None or not 0 <= (now-answered).total_seconds() <= 30 or answered>stamp:return None
        if decision['choice']!=jev['choice']:return None
        if decision['choice']=='K200':
            exit_decision=data['exit']
            score=exit_decision['confidence']
            answered=datetime.fromisoformat(exit_decision['time'])
            if (exit_decision['choice']!='SELL' or type(score) not in (int,float) or not math.isfinite(score)
                    or not 0.8<=score<=1 or answered.tzinfo is None or answered.utcoffset() is None
                    or not 0 <= (now-answered).total_seconds() <= 30 or answered>stamp):return None
        return (decision['choice'],digest) if with_digest else decision['choice']
    except (OSError,ValueError,TypeError,KeyError,OverflowError):
        return None


def step(reader, guard, credentials, execute_orders=False, manage_existing=False, ai_confirm=False):
    now=datetime.now(KST)
    if now.weekday()>=5 or not '09:10'<=now.strftime('%H:%M')<'15:30':
        return {'action':'STOP','reason':'outside session'}
    final_observation=now.strftime('%H:%M')>='15:10'
    if final_observation:execute_orders=False
    snapshots=collect_snapshots(reader,guard.account,now)
    risk=guard.observe_loss(snapshots['pnl'],datetime.now(KST))
    positions=[r for r in snapshots['balance']['Output_1'] if r.get('iem_cd')=='114800']
    shares=sum(r.get('rsdl_qty',0) for r in positions)
    if type(shares) is not int:raise LiveOrderBlocked('position quantity invalid')
    q=snapshots['quote']['data']['Output_0']
    exit_check=intent(shares=shares,pending=0,daily_loss=20000 if risk['halted'] else risk['loss'],
                      bid=q.get('bidp'),ask=q.get('askp'),entry=positions[0].get('phs_pr') if positions else None)
    with guard._db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS exit_latches (scope TEXT PRIMARY KEY, reason TEXT NOT NULL)')
        if shares and exit_check['action']=='SELL':
            db.execute('INSERT OR IGNORE INTO exit_latches VALUES (?,?)',(guard.scope,exit_check['reason']))
        if not shares:
            db.execute('DELETE FROM exit_latches WHERE scope=?',(guard.scope,))
        exit_latched=db.execute('SELECT reason FROM exit_latches WHERE scope=?',(guard.scope,)).fetchone()
        pending=db.execute("SELECT fingerprint,state,operation,order_number FROM live_intents WHERE scope=? AND state!='terminal'",(guard.scope,)).fetchall()
        db.execute('CREATE TABLE IF NOT EXISTS observed_pending (fingerprint TEXT PRIMARY KEY, first_seen REAL NOT NULL)')
        for row in pending:db.execute('INSERT OR IGNORE INTO observed_pending VALUES (?,?)',(row[0],time.time()))
    if pending:
        has_cancel=any(row[2]=='krstockOrderCancel' for row in pending)
        for fingerprint,state,operation,number in pending:
            if state in ('acknowledged','uncertain'):
                try:
                    guard.reconcile(fingerprint,snapshots['history'])
                    continue
                except LiveOrderBlocked:
                    pass
            with guard._db() as db:
                first_seen=db.execute('SELECT first_seen FROM observed_pending WHERE fingerprint=?',(fingerprint,)).fetchone()[0]
            cancel_due=(operation=='krstockOrderCashBuy' and (risk['halted'] or not buy_allowed(guard.root))) or time.time()-first_seen>=PENDING_CANCEL_SECONDS or now.strftime('%H:%M')>='14:50'
            if (state=='acknowledged' and operation!='krstockOrderCancel' and not has_cancel
                    and execute_orders and not halt_flag_set('nhplug')
                    and cancel_due):
                fields={'act_no':guard.account,'iem_cd':'114800','org_mkt_orr_no':number,'all_pat_dit_cd':'1'}
                cancel=execute(reader,guard,credentials,'krstockOrderCancel',fields)
                return {'action':'CANCEL_PENDING','execution':'CANCEL_ACKNOWLEDGED_NOT_RECONCILED','intent':cancel}
        return {'action':'RECONCILE','reason':'own outstanding or uncertain intent; no new submission'}
    opened=sum(r.get('ny_cns_qty',0)>0 for r in snapshots['history']['Output_0'])
    closes=[]
    if not shares and not opened:
        result=reader.call_tool('call_api',{'operationId':'krstockQuotePeriod','input':{'market_cd':'KRX','iem_cd':'114800','gubun':'5','xtick':'1','today_cls_code':'1','view_main_yn':'Y','array_cnt':'30'}})
        if result.get('status')!='ok':raise LiveOrderBlocked('bars unavailable')
        closes=[r['stck_prpr'] for r in reversed(valid_bars(result['data'],'114800',datetime.now(KST)))]
    plan=intent(shares=shares,pending=opened,daily_loss=20000 if risk['halted'] else risk['loss'],bid=q.get('bidp'),ask=q.get('askp'),entry=positions[0].get('phs_pr') if positions else None,closes=closes,price_cap=live_price_cap(guard.account))
    if exit_latched and shares:
        plan={'action':'CANCEL_THEN_SELL' if opened else 'SELL','reason':exit_latched[0]}
    if not buy_allowed(guard.root) and plan['action']=='BUY':
        plan={'action':'WAIT','reason':'new buys paused by operator'}
    if not buy_allowed(guard.root) and opened and plan['action']=='RECONCILE':
        plan={'action':'CANCEL_ORDERS','reason':'new buys paused by operator'}
    if now.strftime('%H:%M') >= '14:45' and plan['action']=='BUY':
        plan={'action':'WAIT','reason':'new entry cutoff'}
    plan.update(policy_version=VERSION,shares=shares,open_orders=opened,loss=risk['loss'],
                bid=q.get('bidp'),ask=q.get('askp'),quote_time=q.get('hoga_bsop_hour'))
    if halt_flag_set('nhplug'):
        plan.update(execution='BLOCKED',execution_reason='global or NH live halt');return plan
    # A cached market-only observation never runs network calls on the protective stop path.
    ai_preflight=None
    if ai_confirm and plan['action'] in ('BUY','HOLD'):
        ai_path=get_runtime_root()/'nhplug/live-ai-observation.json'
        observation=ai_signal(ai_path,guard.scope,datetime.now(KST),with_digest=True)
        signal,digest=observation or (None,None)
        plan['ai_signal']=signal or 'WAIT'
        if plan['action']=='BUY' and signal!='INV':
            plan.update(action='WAIT',reason='AI confirmation unavailable or disagrees')
        elif plan['action']=='BUY':
            ai_preflight=lambda: ai_signal(ai_path,guard.scope,datetime.now(KST),expected_digest=digest)=='INV'
        elif plan['action']=='HOLD' and signal=='K200':
            plan.update(action='SELL',reason='AI market observation supports early exit')
            ai_preflight=lambda: ai_signal(ai_path,guard.scope,datetime.now(KST),expected_digest=digest)=='K200'
    if not execute_orders:
        plan.update(execution='END_SESSION_OBSERVATION' if final_observation else 'OBSERVATION');return plan
    if plan['action'] in ('CANCEL_THEN_SELL','CANCEL_ORDERS','RECONCILE'):
        if manage_existing and opened:
            rows=[r for r in snapshots['history']['Output_0'] if r.get('ny_cns_qty',0)>0]
            if any(r.get('iem_cd')!='114800' or r.get('sby_dit_cd_nm')!='현금매수' or r.get('orr_qty')!=1 for r in rows):
                raise LiveOrderBlocked('external order scope mismatch')
            number=rows[0].get('itg_orr_no')
            guard.adopt_order(now=datetime.now(KST),history=snapshots['history'],order_number=number)
            fields={'act_no':guard.account,'iem_cd':'114800','org_mkt_orr_no':number,'all_pat_dit_cd':'1'}
            plan['intent']=execute(reader,guard,credentials,'krstockOrderCancel',fields)
            plan.update(execution='CANCEL_ACKNOWLEDGED_NOT_RECONCILED')
            return plan
        plan.update(execution='BLOCKED',execution_reason='existing orders require explicit management')
        return plan
    if plan['action'] not in ('BUY','SELL'):
        plan.update(execution='NO_ORDER');return plan
    op='krstockOrderCashBuy' if plan['action']=='BUY' else 'krstockOrderCashSell'
    price=q.get('askp') if plan['action']=='BUY' else q.get('bidp')
    if type(price) is not int or price<=0 or (op=='krstockOrderCashBuy' and price>live_price_cap(guard.account)):raise LiveOrderBlocked('policy price cap')  # cap limits buys only (2026-11-12)
    fields={'act_no':guard.account,'iem_cd':'114800','orr_qty':1,'orr_pr':price,'nmn_pr_tp_cd':'01','orr_cnd_dit_cd':'00','ssl_nmn_pr_dit_cd':'00','rmt_mkt_cd':'KRX','sor_mkt_sli_yn':'N'}
    replacement=None
    if op=='krstockOrderCashSell':
        with guard._db() as db:
            latest=db.execute("SELECT fingerprint FROM live_intents WHERE scope=? AND operation='krstockOrderCashSell' AND fingerprint NOT IN (SELECT fingerprint FROM live_pre_send_aborts) ORDER BY rowid DESC LIMIT 1",(guard.scope,)).fetchone()
            if latest and db.execute('SELECT 1 FROM sell_cancellations WHERE fingerprint=?',(latest[0],)).fetchone():
                replacement=latest[0]
    options={}
    if replacement:options['replacement_of']=replacement
    if ai_preflight is not None:options['pre_reserve_check']=ai_preflight
    plan['intent']=execute(reader,guard,credentials,op,fields,**options)
    plan['execution']='ACKNOWLEDGED_NOT_FILLED'
    return plan


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--execute',action='store_true');parser.add_argument('--manage-existing',action='store_true');parser.add_argument('--ai-confirm',action='store_true');parser.add_argument('--once',action='store_true');args=parser.parse_args()
    root=get_runtime_root()/'nhplug';root.mkdir(mode=0o700,parents=True,exist_ok=True);os.umask(0o077)
    values=dotenv_values(ENV,interpolate=False);account=''.join(c for c in (values.get('ACCOUNT_NUMBER') or '') if c.isdigit());guard=LiveOrderGuard(account,root)
    reader=MCPServerAdapter('nhplug_readonly',load_agent_config().mcp_servers['nhplug_readonly'])
    with (root/'live-auto.lock').open('a', encoding='utf-8') as lock:
        file_lock.lock(lock, blocking=False)
        (root/'live-buy-control.pid').write_text(str(os.getpid()), encoding='utf-8')
        while True:
            try:result=step(reader,guard,values,args.execute,args.manage_existing,args.ai_confirm)
            except Exception as exc:result={'action':'BLOCK','error_type':type(exc).__name__}
            event={'time':datetime.now(KST).isoformat(),**result}
            with (root/'live-auto.jsonl').open('a', encoding='utf-8') as stream:stream.write(json.dumps(event,ensure_ascii=False)+'\n')
            print(json.dumps(event,ensure_ascii=False),flush=True)
            if args.once or result.get('action')=='STOP':break
            time.sleep(CHECK_INTERVAL_SECONDS)
if __name__=='__main__':main()
