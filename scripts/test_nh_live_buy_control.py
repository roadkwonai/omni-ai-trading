import pytest
from nh_live_buy_control import pause, resume, buy_allowed, buy_lock
from src.tools.nhplug_live_guard import LiveOrderBlocked


def test_pause_blocks_buys_and_resume_only_removes_own_marker(tmp_path):
 assert buy_allowed(tmp_path)
 pause(tmp_path)
 with pytest.raises(LiveOrderBlocked):
  with buy_lock(tmp_path): pass
 resume(tmp_path)
 assert buy_allowed(tmp_path)
 (tmp_path/'LIVE_BUY_PAUSED').write_text('another owner')
 with pytest.raises(ValueError): resume(tmp_path)
 assert not buy_allowed(tmp_path)


def test_pause_preserves_protective_sells_and_cancels_pending_buy(tmp_path,monkeypatch):
 from test_nh_live_auto import Auto, NOW
 from src.tools.nhplug_live_guard import LiveOrderGuard
 from unittest.mock import MagicMock, patch
 import nh_live_auto as a
 guard=LiveOrderGuard('12345678901',tmp_path); guard.observe_loss=lambda *args:{'loss':'0','halted':False}
 pause(tmp_path)
 snap=Auto().snap(); snap['quote']['data']['Output_0'].update(bidp=1280,askp=1281)
 with patch.object(a,'collect_snapshots',return_value=snap),patch.object(a,'datetime') as clock,patch.object(a,'halt_flag_set',return_value=False),patch.object(a,'execute',return_value='fake') as send:
  clock.now.return_value=NOW
  assert a.step(MagicMock(),guard,{},True)['action']=='SELL'
  assert send.call_args.args[3]=='krstockOrderCashSell'
 with guard._db() as db: db.execute('INSERT INTO live_intents VALUES (?,?,?,?,?,?,?,?,?)',('buy',guard.scope,'20261106','krstockOrderCashBuy','114800',1,42,'acknowledged',0))
 with patch.object(a,'collect_snapshots',return_value=snap),patch.object(a,'datetime') as clock,patch.object(a,'halt_flag_set',return_value=False),patch.object(guard,'reconcile',side_effect=LiveOrderBlocked('pending')),patch.object(a,'execute',return_value='cancel') as send:
  clock.now.return_value=NOW
  assert a.step(MagicMock(),guard,{},True)['action']=='CANCEL_PENDING'
  assert send.call_args.args[3]=='krstockOrderCancel'


def test_pause_waits_for_inflight_buy_lock(tmp_path):
 import threading
 entered=threading.Event(); release=threading.Event(); stopped=threading.Event()
 def order():
  with buy_lock(tmp_path):
   entered.set(); release.wait(2)
 def stop():
  pause(tmp_path); stopped.set()
 first=threading.Thread(target=order); first.start(); assert entered.wait(1)
 second=threading.Thread(target=stop); second.start()
 assert not stopped.wait(0.1)
 release.set(); first.join(2); second.join(2)
 assert stopped.is_set() and not buy_allowed(tmp_path)
