from datetime import timedelta
import pytest
import nh_guarded_live as x
from src.tools.nhplug_live_guard import LiveOrderGuard, LiveOrderBlocked
from test_nh_live_broker import FakeReader, A, NOW, FIELDS


def test_buy_audit_uses_guarded_quote_without_extra_calls(tmp_path, monkeypatch):
 monkeypatch.setenv('VIBE_TRADING_HOME', str(tmp_path / 'runtime'))
 monkeypatch.setattr(x, 'halt_flag_set', lambda *_: False)
 monkeypatch.setattr(x, 'datetime', type('Clock', (), {'now': staticmethod(lambda _: NOW)}))
 reader = FakeReader()
 snapshots = __import__('nh_live_broker').collect_snapshots(reader, A, NOW, sleep=lambda _: None)
 snapshots['quote']['data']['Output_0']['stck_prdy_clpr'] = '1250'
 monkeypatch.setattr(x, 'collect_snapshots', lambda *_, **__: snapshots)
 def transport(*args, **kwargs):
  assert kwargs['pre_send_check']()
  return {'rsp_cd': '00048', 'rsp_msg': '매수주문이 완료되었습니다', 'Output_0': {'mkt_orr_no': 42}}
 monkeypatch.setattr(x, '_transport', transport)
 calls = len(reader.calls)
 audit = {}
 x.execute(reader, LiveOrderGuard(A, tmp_path / 'guard'), {}, 'krstockOrderCashBuy', FIELDS, quote_audit=audit)
 assert len(reader.calls) == calls
 assert audit['ask'] == 1400 and audit['previous_close'] == 1250 and audit['gap_pct'] == 12.0
 assert audit['previous_close_date'] is None and audit['reason'] == 'previous_close_date_not_provided'


def test_guarded_write_never_retries_unknown(tmp_path,monkeypatch):
 monkeypatch.setenv('VIBE_TRADING_HOME',str(tmp_path/'runtime'))
 monkeypatch.setattr(x,'halt_flag_set',lambda *_:False)
 monkeypatch.setattr(x,'collect_snapshots',lambda *_,**__:__import__('nh_live_broker').collect_snapshots(FakeReader(),A,NOW,sleep=lambda _:None))
 monkeypatch.setattr(x,'datetime',type('Clock',(),{'now':staticmethod(lambda _:NOW)}))
 calls=[]
 def failure(*args, **kwargs):calls.append(1);raise TimeoutError()
 monkeypatch.setattr(x,'_transport',failure)
 g=LiveOrderGuard(A,tmp_path/'guard')
 with pytest.raises(TimeoutError):x.execute(FakeReader(),g,{},'krstockOrderCashBuy',FIELDS)
 with pytest.raises(LiveOrderBlocked):x.execute(FakeReader(),g,{},'krstockOrderCashBuy',FIELDS)
 assert len(calls)==1
 with g._db() as db:fingerprint=db.execute('select fingerprint from live_intents').fetchone()[0]
 with pytest.raises(LiveOrderBlocked,match='not owned'):
  g.abort_before_send(fingerprint)
 with pytest.raises(LiveOrderBlocked,match='not owned'):
  LiveOrderGuard(A,tmp_path/'guard').abort_before_send(fingerprint)

def test_halt_prevents_even_preflight(tmp_path,monkeypatch):
 monkeypatch.setattr(x,'halt_flag_set',lambda *_:True)
 monkeypatch.setattr(x,'collect_snapshots',lambda *_,**__:pytest.fail('must not read'))
 with pytest.raises(LiveOrderBlocked):x.execute(FakeReader(),LiveOrderGuard(A,tmp_path),{},'krstockOrderCashBuy',FIELDS)

def test_expired_ai_blocks_after_snapshot_before_reserve(tmp_path,monkeypatch):
 monkeypatch.setenv('VIBE_TRADING_HOME',str(tmp_path/'runtime'))
 monkeypatch.setattr(x,'halt_flag_set',lambda *_:False)
 monkeypatch.setattr(x,'collect_snapshots',lambda *_,**__:__import__('nh_live_broker').collect_snapshots(FakeReader(),A,NOW,sleep=lambda _:None))
 monkeypatch.setattr(x,'datetime',type('Clock',(),{'now':staticmethod(lambda _:NOW)}))
 monkeypatch.setattr(x,'_transport',lambda *_args,**_kwargs:pytest.fail('must not send'))
 g=LiveOrderGuard(A,tmp_path/'guard')
 with pytest.raises(LiveOrderBlocked,match='before reservation'):
  x.execute(FakeReader(),g,{},'krstockOrderCashBuy',FIELDS,pre_reserve_check=lambda:False)
 with g._db() as db:assert db.execute('select count(*) from live_intents').fetchone()[0]==0

def test_expired_ai_after_authentication_never_sends_order(tmp_path,monkeypatch):
 class Opener:
  def open(self,*_args,**_kwargs):pytest.fail('must not send')
 monkeypatch.setattr(x.urllib.request,'build_opener',lambda *_:Opener())
 monkeypatch.setattr(x,'_token',lambda *_:'fake-token')
 monkeypatch.setattr(x,'halt_flag_set',lambda *_:False)
 with pytest.raises(LiveOrderBlocked,match='before send'):
  x._transport({'APP_KEY':'fake','APP_SECRET':'fake'},'krstockOrderCashBuy',FIELDS,pre_send_check=lambda:False)

@pytest.mark.parametrize('operation', ['krstockOrderCashBuy', 'krstockOrderCashSell'])
def test_market_expiry_after_reservation_aborts_before_order_transport(tmp_path, monkeypatch, operation):
 monkeypatch.setenv('VIBE_TRADING_HOME', str(tmp_path / 'runtime'))
 monkeypatch.setattr(x, 'halt_flag_set', lambda *_: False)
 current = [NOW]
 monkeypatch.setattr(x, 'datetime', type('Clock', (), {'now': staticmethod(lambda _: current[0])}))
 snapshots = __import__('nh_live_broker').collect_snapshots(FakeReader(), A, NOW, sleep=lambda _: None)
 guard = LiveOrderGuard(A, tmp_path / 'guard')
 if operation == 'krstockOrderCashSell':
  balance = {'_pagination_complete':True, '_account':A, 'rsp_cd':'00166', 'rsp_msg':'조회가 완료되었습니다.',
             'Output_1':[{'iem_cd':'114800','rsdl_qty':1}]}
  guard.adopt_existing(now=NOW, balance=balance,
                       sellable={'_account':A,'_symbol':'114800','rsp_cd':'00166','sll_pbl_qty':1})
  snapshots['balance'] = {**snapshots['balance'], 'Output_1': balance['Output_1']}
 monkeypatch.setattr(x, 'collect_snapshots', lambda *_, **__: snapshots)
 class Reader(FakeReader):
  def call_tool(self, name, args):
   if args.get('operationId') == 'krstockInquirySellableQuantity':
    return {'status':'ok','data':{'rsp_cd':'00166','Output_0':{'sll_pbl_qty':1}}}
   return super().call_tool(name, args)
 class Opener:
  def open(self, *_args, **_kwargs): pytest.fail('stale quote must not reach order transport')
 monkeypatch.setattr(x.urllib.request, 'build_opener', lambda *_: Opener())
 def token(*_):
  current[0] = NOW + timedelta(seconds=121)
  return 'fake-token'
 monkeypatch.setattr(x, '_token', token)
 with pytest.raises(x.AISignalBeforeSend, match='before send'):
  x.execute(Reader(), guard, {'APP_KEY':'fake','APP_SECRET':'fake'}, operation, FIELDS)
 with guard._db() as db:
  assert db.execute('SELECT state,filled FROM live_intents').fetchall() == [('terminal', 0)]
  assert db.execute('SELECT count(*) FROM live_pre_send_aborts').fetchone()[0] == 1

def test_signal_change_after_reservation_is_audited_and_allows_protective_sale(tmp_path,monkeypatch):
 monkeypatch.setenv('VIBE_TRADING_HOME',str(tmp_path/'runtime'))
 monkeypatch.setattr(x,'halt_flag_set',lambda *_:False)
 monkeypatch.setattr(x,'collect_snapshots',lambda *_,**__:__import__('nh_live_broker').collect_snapshots(FakeReader(),A,NOW,sleep=lambda _:None))
 monkeypatch.setattr(x,'datetime',type('Clock',(),{'now':staticmethod(lambda _:NOW)}))
 monkeypatch.setattr(x,'_token',lambda *_:'fake-token')
 class Opener:
  def open(self,*_args,**_kwargs):pytest.fail('must not send')
 monkeypatch.setattr(x.urllib.request,'build_opener',lambda *_:Opener())
 g=LiveOrderGuard(A,tmp_path/'guard'); checks=iter([True,False])
 with pytest.raises(LiveOrderBlocked,match='before send'):
  x.execute(FakeReader(),g,{'APP_KEY':'fake','APP_SECRET':'fake'},'krstockOrderCashBuy',FIELDS,pre_reserve_check=lambda:next(checks))
 with g._db() as db:
  assert db.execute('select state,filled from live_intents').fetchone()==('terminal',0)
  assert db.execute('select count(*) from live_pre_send_aborts').fetchone()[0]==1
 # A proven pre-send abort does not use up a day's buy (2026-11-12: no daily buy count, at most 5 new symbols).
 checks=iter([True,False])
 with pytest.raises(LiveOrderBlocked,match='before send'):
  x.execute(FakeReader(),g,{'APP_KEY':'fake','APP_SECRET':'fake'},'krstockOrderCashBuy',{**FIELDS,'orr_pr':1399},pre_reserve_check=lambda:next(checks))
 with g._db() as db:
  assert db.execute('select count(*) from live_pre_send_aborts').fetchone()[0]==2
 balance={'_pagination_complete':True,'_account':A,'rsp_cd':'00166','rsp_msg':'조회가 완료되었습니다.','Output_1':[{'iem_cd':'114800','rsdl_qty':1}]}
 sellable={'_account':A,'_symbol':'114800','rsp_cd':'00166','sll_pbl_qty':1}
 g.adopt_existing(now=NOW,balance=balance,sellable=sellable)
 snapshots=__import__('nh_live_broker').collect_snapshots(FakeReader(),A,NOW,sleep=lambda _:None)
 snapshots['balance']={**snapshots['balance'],'Output_1':balance['Output_1']}
 monkeypatch.setattr(x,'collect_snapshots',lambda *_,**__:snapshots)
 class SellReader(FakeReader):
  def call_tool(self,name,args):
   if args.get('operationId')=='krstockInquirySellableQuantity':
    return {'status':'ok','data':{'rsp_cd':'00166','Output_0':{'sll_pbl_qty':1}}}
   return super().call_tool(name,args)
 monkeypatch.setattr(x,'_transport',lambda *_,**__:{'rsp_cd':'00047','rsp_msg':'매도 주문이 완료되었습니다','Output_0':{'mkt_orr_no':77}})
 assert x.execute(SellReader(),g,{},'krstockOrderCashSell',FIELDS)

def test_aborted_ai_sell_allows_identical_protective_sell(tmp_path,monkeypatch):
 monkeypatch.setenv('VIBE_TRADING_HOME',str(tmp_path/'runtime'))
 monkeypatch.setattr(x,'halt_flag_set',lambda *_:False)
 monkeypatch.setattr(x,'datetime',type('Clock',(),{'now':staticmethod(lambda _:NOW)}))
 monkeypatch.setattr(x,'_token',lambda *_:'fake-token')
 class Opener:
  def open(self,*_args,**_kwargs):pytest.fail('must not send')
 monkeypatch.setattr(x.urllib.request,'build_opener',lambda *_:Opener())
 g=LiveOrderGuard(A,tmp_path/'guard')
 balance={'_pagination_complete':True,'_account':A,'rsp_cd':'00166','rsp_msg':'조회가 완료되었습니다.','Output_1':[{'iem_cd':'114800','rsdl_qty':1}]}
 g.adopt_existing(now=NOW,balance=balance,sellable={'_account':A,'_symbol':'114800','rsp_cd':'00166','sll_pbl_qty':1})
 snapshots=__import__('nh_live_broker').collect_snapshots(FakeReader(),A,NOW,sleep=lambda _:None)
 snapshots['balance']={**snapshots['balance'],'Output_1':balance['Output_1']}
 monkeypatch.setattr(x,'collect_snapshots',lambda *_,**__:snapshots)
 class SellReader(FakeReader):
  def call_tool(self,name,args):
   if args.get('operationId')=='krstockInquirySellableQuantity':
    return {'status':'ok','data':{'rsp_cd':'00166','Output_0':{'sll_pbl_qty':1}}}
   return super().call_tool(name,args)
 checks=iter([True,False])
 with pytest.raises(LiveOrderBlocked,match='before send'):
  x.execute(SellReader(),g,{'APP_KEY':'fake','APP_SECRET':'fake'},'krstockOrderCashSell',FIELDS,pre_reserve_check=lambda:next(checks))
 monkeypatch.setattr(x,'_transport',lambda *_,**__:{'rsp_cd':'00047','rsp_msg':'매도 주문이 완료되었습니다','Output_0':{'mkt_orr_no':77}})
 assert x.execute(SellReader(),g,{},'krstockOrderCashSell',FIELDS)

def test_explicit_adoption_requires_exactly_one(tmp_path):
 g=LiveOrderGuard(A,tmp_path)
 balance={'_pagination_complete':True,'_account':A,'rsp_cd':'00166','rsp_msg':'조회가 완료되었습니다.','Output_1':[{'iem_cd':'114800','rsdl_qty':1}]}
 sellable={'_account':A,'_symbol':'114800','rsp_cd':'00166','sll_pbl_qty':1}
 with pytest.raises(LiveOrderBlocked):g.adopt_existing(now=NOW,balance={**balance,'Output_1':[]},sellable=sellable)
 g.adopt_existing(now=NOW,balance=balance,sellable=sellable)
 with g._db() as db:assert db.execute('select count(*) from live_adoptions').fetchone()[0]==1

def test_http_auth_cache_and_exactly_once_write(tmp_path,monkeypatch):
 import io,json,urllib.error
 class Response(io.BytesIO):
  def __enter__(self):return self
  def __exit__(self,*args):self.close()
 class Opener:
  def __init__(self):self.auth=0;self.orders=0
  def open(self,req,timeout):
   if '/oauth2/token?' in req.full_url:
    self.auth+=1;return Response(json.dumps({'access_token':'fake','expires_in':86400}).encode())
   self.orders+=1
   raise urllib.error.HTTPError(req.full_url,401,'unauthorized',{},None)
 opener=Opener();monkeypatch.setattr(x.Path,'home',lambda:tmp_path)
 monkeypatch.setattr(x.urllib.request,'build_opener',lambda *_:opener)
 monkeypatch.setattr(x,'halt_flag_set',lambda *_:False)
 creds={'APP_KEY':'fake','APP_SECRET':'secret'}
 with pytest.raises(LiveOrderBlocked,match='outcome unknown'):
  x._transport(creds,'krstockOrderCashBuy',FIELDS)
 assert opener.auth==1 and opener.orders==1
 with pytest.raises(LiveOrderBlocked):x._transport(creds,'krstockOrderCashBuy',FIELDS)
 assert opener.auth==1 and opener.orders==2
 assert all((p.stat().st_mode & 0o077)==0 for p in tmp_path.rglob('*.json'))

def test_redirect_refused():
 assert x._NoRedirect().redirect_request(None,None,None,None,None,None) is None

def test_account_price_cap_limits_buys_only(tmp_path,monkeypatch):
 # 2026-11-12: a share that rose above the account cap must stay sellable (stop loss, take profit, loss latch exits).
 import accounts
 reads=[]
 record={'kind':'live','engine':'jev','hash':accounts.digest(A),'budget':{'capital_krw':1_000_000,'order_price_max_krw':1000,'daily_loss_krw':20_000}}
 monkeypatch.setattr(accounts,'load',lambda *a,**k:reads.append(1) or {'live-1':record})
 monkeypatch.setattr(x,'halt_flag_set',lambda *_:False)
 class Reached(Exception):pass
 def reached(*_a,**_k):raise Reached()
 monkeypatch.setattr(x,'collect_snapshots',reached)
 g=LiveOrderGuard(A,tmp_path)
 with pytest.raises(LiveOrderBlocked,match='price cap'):x.execute(FakeReader(),g,{},'krstockOrderCashBuy',FIELDS)
 with pytest.raises(Reached):x.execute(FakeReader(),g,{},'krstockOrderCashSell',FIELDS)
 assert len(reads)==2  # one accounts.json read per order
