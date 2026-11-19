import nh_ws_monitor as ws


def test_quote_requires_matching_channel_symbol_and_sane_values():
 msg={'header':{'tr_cd':'ob','tr_key':'114800'},'body':{'code':'114800','hotime':'13:49:56','bid':'1300','offer':'1301'}}
 assert ws.parse(msg,'quote')['bid']==1300
 for bad in [{**msg,'header':{'tr_cd':'mb','tr_key':'114800'}},{**msg,'body':{**msg['body'],'bid':'0'}},{**msg,'body':{**msg['body'],'offer':'1299'}},{**msg,'body':{**msg['body'],'code':'005930'}}]:
  assert ws.parse(bad,'quote') is None


def test_notice_is_only_reconciliation_hint_and_never_copies_account():
 msg={'header':{'tr_cd':'d2','tr_key':''},'body':{'account':'secret','anything':'data'}}
 assert ws.parse(msg,'paper_notice') == {'kind':'reconcile_required'}
 assert ws.parse({'header':{'tr_cd':'d2','rsp_cd':'00000'},'body':{}},'paper_notice') is None


def test_subscribe_message_is_fixed_to_safe_channels():
 assert ws.subscription('test','quote')['body']=={'tr_cd':'ob','tr_key':'114800'}
 assert ws.subscription('test','paper_notice')['body']=={'tr_cd':'d2','tr_key':''}


def test_watch_reconnects_after_disconnect_and_uses_bounded_backoff(monkeypatch):
 import threading
 stop=threading.Event(); calls=[]; delays=[]
 def fake_probe(mode, seconds, on_event=None):
  calls.append(mode)
  if len(calls)==1: raise ConnectionError()
  on_event({'kind':'quote','bid':100,'ask':101,'exchange_time':'13:00:00'})
  stop.set()
 monkeypatch.setattr(ws,'probe',fake_probe)
 monkeypatch.setattr(ws,'wait_or_stop',lambda event,delay: delays.append(delay))
 events=[]
 ws.watch('quote',stop,events.append)
 assert len(calls)==2 and delays==[1]
 assert events[0]['kind']=='disconnected' and events[1]['kind']=='quote'


def test_wake_hint_never_executing_orders_and_ignores_disconnect():
 import threading
 event=threading.Event()
 ws.wake_on_data(event, {'kind':'disconnected'})
 assert not event.is_set()
 ws.wake_on_data(event, {'kind':'reconcile_required'})
 assert event.is_set()


def test_paper_observation_does_not_cancel_pending_orders(monkeypatch, tmp_path):
 import nh_paper_isolated as p
 from unittest.mock import MagicMock
 from datetime import datetime
 from zoneinfo import ZoneInfo
 db=MagicMock(); db.execute.return_value.fetchall.return_value=[('intent',)]
 seen=[]
 monkeypatch.setattr(p,'reconcile',lambda *args, **kwargs: seen.append(kwargs) or False)
 assert p.cycle(db,None,None,'paper',tmp_path,False,datetime.now(ZoneInfo('Asia/Seoul'))) == 'pending'
 assert seen==[{'allow_cancel':False}]
