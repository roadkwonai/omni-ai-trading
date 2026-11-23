"""Single guarded NH live order entrypoint; disabled while the global HALT is set."""
from __future__ import annotations
import argparse
import json
import hashlib
import os
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import dotenv_values
from src.config.loader import load_agent_config
from src.config.paths import get_runtime_root
from src.live.halt import halt_flag_set
from src.tools.mcp import MCPServerAdapter
from src.tools.nhplug_live_guard import LiveOrderBlocked, LiveOrderGuard, verify_order_market
from nh_live_broker import collect_snapshots
from nh_live_buy_control import buy_lock
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')

BASE = 'https://api.nhplug.com:8443'
from local_env import ENV  # <repo>/.env (see .env.example)
KST = ZoneInfo('Asia/Seoul')
PATHS = {'krstockOrderCashBuy':'/krstock/order/v1/cashBuy', 'krstockOrderCashSell':'/krstock/order/v1/cashSell', 'krstockOrderCancel':'/krstock/order/v1/cancel'}

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class AISignalBeforeSend(LiveOrderBlocked):
    """A strategy or market check failed before the order request was sent."""


def _transport(credentials, op, fields, *, pre_send_check=None):
    # A submission error is deliberately never retried: its outcome may be unknown.
    key, secret = credentials['APP_KEY'], credentials['APP_SECRET']
    opener=urllib.request.build_opener(_NoRedirect())
    token=_token(opener,key,secret)
    if halt_flag_set('nhplug'): raise LiveOrderBlocked('live kill switch active before send; reconcile intent')
    if pre_send_check is not None:
        try:
            valid = pre_send_check() is True
        except Exception:
            valid = False
        if not valid:
            raise AISignalBeforeSend('strategy or market data expired or changed before send')
    req=urllib.request.Request(BASE+PATHS[op], data=json.dumps({'Input_0':fields}).encode(), headers={'Authorization':'Bearer '+token,'x-client-id':key,'x-client-secret':secret,'Content-Type':'application/json'})
    try:
        with opener.open(req, timeout=10) as response: return json.load(response)
    except Exception:
        raise LiveOrderBlocked('broker write outcome unknown; reconcile without retry') from None


def _token(opener,key,secret):
    scope=hashlib.sha256((BASE+'\0'+key).encode()).hexdigest()
    path=Path.home()/'.vibe-trading/nhplug/tokens'/(scope+'.json')
    try:
        cached=json.loads(path.read_text())
        if isinstance(cached.get('token'),str) and cached['token'] and float(cached['expiresAt']) > time.time()*1000+30000:
            return cached['token']
    except (OSError, ValueError, KeyError, TypeError):
        pass
    query=urllib.parse.urlencode({'appkey':key,'appsecretkey':secret,'grant_type':'client_credentials','scope':'oob'})
    req=urllib.request.Request(BASE+'/oauth2/token?'+query, data=b'', headers={'Content-Type':'application/x-www-form-urlencoded'})
    try:
        with opener.open(req,timeout=10) as response: data=json.load(response)
        token=data['access_token']; lifetime=float(data.get('expires_in',86400))
        if not isinstance(token,str) or not token or not 60 < lifetime <= 86400: raise ValueError('invalid token')
        path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix='.nh-token-',dir=path.parent)
        try:
            with os.fdopen(fd,'w') as f: json.dump({'token':token,'expiresAt':time.time()*1000+lifetime*1000},f)
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        return token
    except Exception:
        raise LiveOrderBlocked('broker authentication failed; intent requires reconciliation') from None


def execute(adapter, guard, credentials, op, fields, *, replacement_of=None, pre_reserve_check=None, quote_audit=None):
    if op == 'krstockOrderCashBuy':
        with buy_lock(guard.root):
            return _execute(adapter, guard, credentials, op, fields, replacement_of=replacement_of, pre_reserve_check=pre_reserve_check, quote_audit=quote_audit)
    return _execute(adapter, guard, credentials, op, fields, replacement_of=replacement_of, pre_reserve_check=pre_reserve_check, quote_audit=quote_audit)


def _execute(adapter, guard, credentials, op, fields, *, replacement_of=None, pre_reserve_check=None, quote_audit=None):
    if op not in PATHS: raise LiveOrderBlocked('unsupported operation')
    from accounts import DEFAULT_BUDGET, LIVE_PRICE_HARD_CAP, live_limits, live_loss_realized_only, live_record
    # one accounts.json read per order, so a dashboard edit mid-order cannot mix limits (accounts.live_* use the same fields)
    record = live_record(guard.account)
    budget = record['budget'] if record else DEFAULT_BUDGET['live']
    realized_only = live_loss_realized_only(record=record)  # long-term accounts: realized loss + fees only
    # User decision 2026-11-12: the cap limits what one bought share may cost; sells only reduce holdings, so a share that
    # rose above the cap must still be sellable (the guard keeps its 2,000,000 ceiling and the bid floor for sells).
    if op == 'krstockOrderCashBuy' and (type(fields.get('orr_pr')) is not int
                                        or not 0 < fields['orr_pr'] <= min(budget['order_price_max_krw'], LIVE_PRICE_HARD_CAP)):
        raise LiveOrderBlocked('share price exceeds the account price cap')
    if halt_flag_set('nhplug'): raise LiveOrderBlocked('live kill switch active')
    symbol=fields.get('iem_cd')
    if symbol not in guard.symbols: raise LiveOrderBlocked('account or symbol mismatch')
    snapshots=collect_snapshots(adapter,guard.account,datetime.now(KST),symbol=symbol)
    guard.observe_loss(snapshots['pnl'],datetime.now(KST),budget['daily_loss_krw'],realized_only)
    if op=='krstockOrderCashSell':
        res=adapter.call_tool('call_api', {'operationId':'krstockInquirySellableQuantity','input':{'act_no':guard.account,'iem_cd':symbol,'cfd_lon_cd':'00'}})
        if res.get('status')!='ok' or not isinstance(res.get('data'),dict): raise LiveOrderBlocked('sellable unavailable')
        data=res['data']; snapshots['sellable']={**data.get('Output_0',{}),'rsp_cd':data.get('rsp_cd'),'_account':guard.account,'_symbol':symbol}
    if pre_reserve_check is not None and pre_reserve_check() is not True:
        raise LiveOrderBlocked('AI signal expired or changed before reservation')
    limits=live_limits(record=record)  # the account's own capital (set within the account's money) bounds price x quantity
    fingerprint=guard.reserve(op,fields,now=datetime.now(KST),replacement_of=replacement_of,capital=budget['capital_krw'],
                              order_max=limits['order_max_krw'],new_symbols=limits['daily_new_symbols'],
                              loss_limit=budget['daily_loss_krw'],realized_only=realized_only,**snapshots)
    if halt_flag_set('nhplug'):
        guard.mark_send_attempted(fingerprint)
        raise LiveOrderBlocked('live kill switch active after reserve; reconcile intent')
    def pre_send_check():
        if op != 'krstockOrderCancel':
            verify_order_market(op, symbol, fields.get('orr_pr'), datetime.now(KST),
                                snapshots['quote'], snapshots['previous_quote'])
        if op == 'krstockOrderCashBuy' and isinstance(quote_audit, dict):
            try:
                import math
                current = snapshots['quote']['data']['Output_0']
                previous = current.get('stck_prdy_clpr')
                ask = current.get('askp')
                quote_audit.update({'ask': ask, 'observed_at': datetime.now(KST).isoformat(),
                                    'quote_time': current.get('hoga_bsop_hour'), 'source': 'NH 보호 경로 주문 전 호가',
                                    'previous_close_date': None,
                                    'note': 'NH 전일 종가 필드 미확인'})
                previous = float(previous) if not isinstance(previous, bool) else 0
                if math.isfinite(previous) and previous > 0 and type(ask) is int and ask > 0:
                    quote_audit.update(previous_close=previous, gap_pct=round((ask / previous - 1) * 100, 4),
                                       reason='previous_close_date_not_provided',
                                       note='NH 전일 종가 필드 기준. 전 거래일 날짜는 응답에서 확인되지 않았습니다.')
            except (KeyError, TypeError, ValueError, OverflowError):
                pass
        return pre_reserve_check is None or pre_reserve_check() is True
    try:
        response=(_transport(credentials,op,fields,pre_send_check=pre_send_check)
                  if op != 'krstockOrderCancel' or pre_reserve_check is not None else _transport(credentials,op,fields))
    except AISignalBeforeSend:
        guard.abort_before_send(fingerprint)
        raise
    except BaseException:
        guard.mark_send_attempted(fingerprint)
        raise
    guard.mark_send_attempted(fingerprint)
    guard.acknowledge(fingerprint,response)
    return fingerprint


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',nargs='?',choices=('buy','sell','cancel','reconcile'))
    parser.add_argument('--adopt-existing',metavar='114800:수량')
    parser.add_argument('--adopt-order',type=int)
    parser.add_argument('--intent')
    parser.add_argument('--price',type=int,default=0)
    parser.add_argument('--order-number',type=int)
    parser.add_argument('--replacement-of')
    args=parser.parse_args()
    values=dotenv_values(ENV,interpolate=False);account=''.join(c for c in (values.get('ACCOUNT_NUMBER') or '') if c.isdigit())
    if len(account)!=11 or not values.get('APP_KEY') or not values.get('APP_SECRET'): raise ValueError('credentials unavailable')
    cfg=load_agent_config().mcp_servers['nhplug_readonly']; adapter=MCPServerAdapter('nhplug_readonly',cfg)
    guard=LiveOrderGuard(account,get_runtime_root()/'nhplug')
    if args.adopt_order is not None:
        if args.operation or args.adopt_existing: raise ValueError('order adoption must be a separate action')
        now=datetime.now(KST)
        snapshots=collect_snapshots(adapter,account,now)
        accounts=snapshots['accounts'];matches=[x for x in accounts.get('Output_0',[]) if x.get('acct_no')==account and x.get('acct_type')=='01']
        if accounts.get('rsp_cd')!='00000' or len(matches)!=1: raise LiveOrderBlocked('account identity invalid')
        print(json.dumps({'intent':guard.adopt_order(now=now,history=snapshots['history'],order_number=args.adopt_order)}));return
    if args.adopt_existing:
        if args.operation: raise ValueError('adoption must be a separate action')
        symbol, sep, quantity = args.adopt_existing.partition(':')
        if symbol != '114800' or sep != ':' or not quantity.isascii() or not quantity.isdigit() or int(quantity) <= 0:
            raise ValueError('adoption requires 114800:positive_quantity')
        snapshots=collect_snapshots(adapter,account,datetime.now(KST))
        accounts=snapshots['accounts']; matches=[x for x in accounts.get('Output_0',[]) if x.get('acct_no')==account and x.get('acct_type')=='01']
        if accounts.get('rsp_cd')!='00000' or len(matches)!=1: raise LiveOrderBlocked('account identity invalid')
        res=adapter.call_tool('call_api',{'operationId':'krstockInquirySellableQuantity','input':{'act_no':account,'iem_cd':'114800','cfd_lon_cd':'00'}})
        if res.get('status')!='ok' or not isinstance(res.get('data'),dict): raise LiveOrderBlocked('sellable unavailable')
        data=res['data']; sellable={**data.get('Output_0',{}),'rsp_cd':data.get('rsp_cd'),'_account':account,'_symbol':'114800'}
        guard.adopt_existing(now=datetime.now(KST),balance=snapshots['balance'],sellable=sellable,quantity=int(quantity))
        print('holdings adoption recorded'); return
    if not args.operation: parser.error('operation or --adopt-existing required')
    if args.operation=='reconcile':
        if not args.intent: parser.error('--intent required')
        snapshots=collect_snapshots(adapter,account,datetime.now(KST))
        print(json.dumps(guard.reconcile(args.intent,snapshots['history']))); return
    op={'buy':'krstockOrderCashBuy','sell':'krstockOrderCashSell','cancel':'krstockOrderCancel'}[args.operation]
    fields={'act_no':account,'iem_cd':'114800'}
    if args.operation=='cancel': fields.update(org_mkt_orr_no=args.order_number,all_pat_dit_cd='1')
    else: fields.update(orr_qty=1,orr_pr=args.price,nmn_pr_tp_cd='01',rmt_mkt_cd='KRX',sor_mkt_sli_yn='N',orr_cnd_dit_cd='00',ssl_nmn_pr_dit_cd='00')
    print(json.dumps({'intent':execute(adapter,guard,values,op,fields,replacement_of=args.replacement_of),'state':'acknowledged; reconciliation required'}))

if __name__=='__main__': main()
