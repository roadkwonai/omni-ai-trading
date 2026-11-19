import unittest,tempfile
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch,MagicMock
from src.tools.nhplug_live_guard import LiveOrderGuard
import nh_live_auto as a
NOW=datetime(2026,11,6,13,0,tzinfo=ZoneInfo('Asia/Seoul'))
class Auto(unittest.TestCase):
 def test_loss_stop_reprices_pending_exit_only_after_timeout(self):
  with tempfile.TemporaryDirectory() as d:
   guard=LiveOrderGuard('12345678901',Path(d));guard.observe_loss=lambda *args:{'loss':'20000','halted':True}
   with guard._db() as db:
    db.execute('INSERT INTO live_intents VALUES (?,?,?,?,?,?,?,?,?)',('sell',guard.scope,'20261106','krstockOrderCashSell','114800',1,42,'acknowledged',0))
    db.execute('CREATE TABLE observed_pending (fingerprint TEXT PRIMARY KEY, first_seen REAL NOT NULL)')
    db.execute('INSERT INTO observed_pending VALUES (?,?)',('sell',0))
   with patch.object(a,'collect_snapshots',return_value=self.snap()),patch.object(a,'datetime') as clock,patch.object(a,'halt_flag_set',return_value=False),patch.object(guard,'reconcile',side_effect=a.LiveOrderBlocked('pending')),patch.object(a,'execute',return_value='cancel') as send:
    clock.now.return_value=NOW.replace(hour=14,minute=51)
    self.assertEqual(a.step(MagicMock(),guard,{},True)['action'],'CANCEL_PENDING')
    self.assertEqual(send.call_args.args[3],'krstockOrderCancel')
 def test_loss_latch_cancels_pending_buy_immediately(self):
  with tempfile.TemporaryDirectory() as d:
   guard=LiveOrderGuard('12345678901',Path(d));guard.observe_loss=lambda *args:{'loss':'20000','halted':True}
   with guard._db() as db:
    db.execute('INSERT INTO live_intents VALUES (?,?,?,?,?,?,?,?,?)',('buy',guard.scope,'20261106','krstockOrderCashBuy','114800',1,42,'acknowledged',0))
   with patch.object(a,'collect_snapshots',return_value=self.snap()),patch.object(a,'datetime') as clock,patch.object(a,'halt_flag_set',return_value=False),patch.object(guard,'reconcile',side_effect=a.LiveOrderBlocked('pending')),patch.object(a,'execute',return_value='cancel') as send:
    clock.now.return_value=NOW
    self.assertEqual(a.step(MagicMock(),guard,{},True)['action'],'CANCEL_PENDING')
    self.assertEqual(send.call_args.args[3],'krstockOrderCancel')
 def test_loss_latch_without_position_still_cancels_external_buy(self):
  with tempfile.TemporaryDirectory() as d:
   guard=LiveOrderGuard('12345678901',Path(d));guard.observe_loss=lambda *args:{'loss':'20000','halted':True}
   s=self.snap();s['balance']['Output_1']=[];s['history']['Output_0']=[{'iem_cd':'114800','sby_dit_cd_nm':'현금매수','orr_qty':1,'ny_cns_qty':1,'itg_orr_no':42}]
   with patch.object(a,'collect_snapshots',return_value=s),patch.object(a,'datetime') as clock,patch.object(a,'halt_flag_set',return_value=False),patch.object(guard,'adopt_order') as adopt,patch.object(a,'execute',return_value='cancel') as send:
    clock.now.return_value=NOW
    result=a.step(MagicMock(),guard,{},True,True)
    self.assertEqual(result['execution'],'CANCEL_ACKNOWLEDGED_NOT_RECONCILED')
    send.assert_called_once();adopt.assert_called_once()
 def test_owned_pending_cancel_at_cutoff_without_resubmitting(self):
  with tempfile.TemporaryDirectory() as d:
   guard=LiveOrderGuard('12345678901',Path(d));guard.observe_loss=lambda *args:{'loss':'0','halted':False}
   with guard._db() as db:
    db.execute('INSERT INTO live_intents VALUES (?,?,?,?,?,?,?,?,?)',('buy',guard.scope,'20261106','krstockOrderCashBuy','114800',1,42,'acknowledged',0))
   with patch.object(a,'collect_snapshots',return_value=self.snap()),patch.object(a,'datetime') as clock,patch.object(a,'halt_flag_set',return_value=False),patch.object(guard,'reconcile',side_effect=a.LiveOrderBlocked('pending')),patch.object(a,'execute',return_value='cancel') as send:
    clock.now.return_value=NOW.replace(hour=14,minute=51)
    result=a.step(MagicMock(),guard,{},True)
    self.assertEqual(result['action'],'CANCEL_PENDING');self.assertEqual(send.call_args.args[3],'krstockOrderCancel')
   with guard._db() as db:db.execute("UPDATE live_intents SET state='uncertain'")
   with patch.object(a,'collect_snapshots',return_value=self.snap()),patch.object(a,'datetime') as clock,patch.object(a,'halt_flag_set',return_value=False),patch.object(guard,'reconcile',side_effect=a.LiveOrderBlocked('pending')),patch.object(a,'execute') as send:
    clock.now.return_value=NOW.replace(hour=14,minute=51)
    a.step(MagicMock(),guard,{},True);send.assert_not_called()
 def test_end_of_session_does_not_force_sale(self):
  with tempfile.TemporaryDirectory() as d:
   guard=LiveOrderGuard('12345678901',Path(d));guard.observe_loss=lambda *args:{'loss':'0','halted':False}
   s=self.snap();s['quote']['data']['Output_0'].update(bidp=1320,askp=1321)
   with patch.object(a,'collect_snapshots',return_value=s),patch.object(a,'datetime') as clock,patch.object(a,'halt_flag_set',return_value=False),patch.object(a,'execute') as send:
    clock.now.return_value=NOW.replace(hour=14,minute=51)
    result=a.step(MagicMock(),guard,{})
    self.assertEqual(result['action'],'HOLD');send.assert_not_called()
    clock.now.return_value=NOW.replace(hour=15,minute=1)
    self.assertEqual(a.step(MagicMock(),guard,{})['action'],'HOLD')
    s['quote']['data']['Output_0'].update(bidp=1280,askp=1281)
    self.assertEqual(a.step(MagicMock(),guard,{})['action'],'SELL')
    clock.now.return_value=NOW.replace(hour=15,minute=11)
    self.assertEqual(a.step(MagicMock(),guard,{},True)['execution'],'END_SESSION_OBSERVATION')
    send.assert_not_called()
 def test_explicit_external_order_management_dispatches_one_cancel(self):
  with tempfile.TemporaryDirectory() as d:
   guard=LiveOrderGuard('12345678901',Path(d));guard.observe_loss=lambda *args:{'loss':'0','halted':False}
   s=self.snap();s['history']['Output_0']=[{'iem_cd':'114800','sby_dit_cd_nm':'현금매수','orr_qty':1,'ny_cns_qty':1,'itg_orr_no':42}]
   with patch.object(a,'collect_snapshots',return_value=s),patch.object(a,'datetime') as clock,patch.object(a,'halt_flag_set',return_value=False),patch.object(guard,'adopt_order') as adopt,patch.object(a,'execute',return_value='cancel-intent') as send:
    clock.now.return_value=NOW
    result=a.step(MagicMock(),guard,{},True,True)
    self.assertEqual(result['execution'],'CANCEL_ACKNOWLEDGED_NOT_RECONCILED')
    adopt.assert_called_once();self.assertEqual(send.call_args.args[3],'krstockOrderCancel')
 def snap(self):
  return {'pnl':{},'balance':{'Output_1':[{'iem_cd':'114800','rsdl_qty':1,'phs_pr':1309}]},'history':{'Output_0':[]},'quote':{'data':{'Output_0':{'bidp':1340,'askp':1341}}}}
 def test_halt_and_default_observation_never_write(self):
  with tempfile.TemporaryDirectory() as d:
   guard=LiveOrderGuard('12345678901',Path(d));guard.observe_loss=lambda *args:{'loss':'0','halted':False}
   with patch.object(a,'collect_snapshots',return_value=self.snap()),patch.object(a,'datetime') as clock,patch.object(a,'halt_flag_set',return_value=True),patch.object(a,'execute') as send:
    clock.now.return_value=NOW
    result=a.step(MagicMock(),guard,{},True)
    self.assertEqual(result['execution'],'BLOCKED');send.assert_not_called()
   with patch.object(a,'collect_snapshots',return_value=self.snap()),patch.object(a,'datetime') as clock,patch.object(a,'halt_flag_set',return_value=False),patch.object(a,'execute') as send:
    clock.now.return_value=NOW
    self.assertEqual(a.step(MagicMock(),guard,{})['execution'],'OBSERVATION');send.assert_not_called()
 def test_pending_external_orders_prevent_dispatch(self):
  with tempfile.TemporaryDirectory() as d:
   guard=LiveOrderGuard('12345678901',Path(d));guard.observe_loss=lambda *args:{'loss':'0','halted':False}
   s=self.snap();s['history']['Output_0']=[{'ny_cns_qty':1}]
   with patch.object(a,'collect_snapshots',return_value=s),patch.object(a,'datetime') as clock,patch.object(a,'halt_flag_set',return_value=False),patch.object(a,'execute') as send:
    clock.now.return_value=NOW
    result=a.step(MagicMock(),guard,{},True)
    self.assertEqual(result['execution'],'BLOCKED');send.assert_not_called()
if __name__=='__main__':unittest.main()

class AIGate(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'observation.json'
  self.guard=LiveOrderGuard('12345678901',Path(self.tmp.name))
  self.data={'mode':'live-observation','scope':self.guard.scope,'symbol':'114800','time':NOW.isoformat(),
      'bar_time':{'K200':'125800','INV':'125800'},
      'decision':{'choice':'INV','confidence':0.9,'time':NOW.isoformat()},
      'exit':{'choice':'HOLD','confidence':0.9,'time':NOW.isoformat()},
      'jev':{'choice':'INV','confidence':0.9,'time':NOW.isoformat(),'model':'jev-1.14.0','chart_sha256':'a'*64}}
 def tearDown(self):self.tmp.cleanup()
 def signal(self, data):
  import json
  self.path.write_text(json.dumps(data));return a.ai_signal(self.path,self.guard.scope,NOW)
 def test_only_fresh_agreeing_scoped_valid_signals(self):
  self.assertEqual(self.signal(self.data),'INV')
  for change in ({'scope':'wrong'},{'symbol':'069500'},{'time':'2026-11-06T12:59:00+09:00'},
                 {'bar_time':{'K200':'125000','INV':'125800'}},
                 {'jev':{**self.data['jev'],'choice':'K200'}},
                 {'jev':{**self.data['jev'],'confidence':float('nan')}},
                 {'jev':{**self.data['jev'],'model':'other'}},
                 {'jev':{**self.data['jev'],'time':'2026-11-06T12:59:00+09:00'}},
                 {'decision':{'choice':'INV','confidence':True}}):
   self.assertIsNone(self.signal({**self.data,**change}))
 def test_early_exit_requires_separate_sell_and_jev_agreement(self):
  data={**self.data,'decision':{**self.data['decision'],'choice':'K200'},
        'jev':{**self.data['jev'],'choice':'K200'}}
  self.assertIsNone(self.signal(data))
  data['exit']={**data['exit'],'choice':'SELL'}
  self.assertEqual(self.signal(data),'K200')
  data['exit']={**data['exit'],'confidence':0.79}
  self.assertIsNone(self.signal(data))
 def test_signal_digest_and_session_boundary(self):
  self.signal(self.data)
  signal,digest=a.ai_signal(self.path,self.guard.scope,NOW,with_digest=True)
  self.assertEqual(signal,'INV')
  self.assertEqual(a.ai_signal(self.path,self.guard.scope,NOW,expected_digest=digest),'INV')
  self.path.write_text(self.path.read_text()+' ')
  self.assertIsNone(a.ai_signal(self.path,self.guard.scope,NOW,expected_digest=digest))
  self.assertIsNone(a.ai_signal(self.path,self.guard.scope,NOW.replace(hour=15),with_digest=True))
  self.assertIsNone(a.ai_signal(self.path,self.guard.scope,NOW.replace(tzinfo=None)))
 def test_stop_loss_never_reads_ai_signal(self):
  g=self.guard;g.observe_loss=lambda *args:{'loss':'0','halted':False}
  s=Auto().snap();s['quote']['data']['Output_0'].update(bidp=1280,askp=1281)
  with patch.object(a,'collect_snapshots',return_value=s),patch.object(a,'datetime') as clock,patch.object(a,'halt_flag_set',return_value=False),patch.object(a,'ai_signal',side_effect=AssertionError('must not evaluate AI')),patch.object(a,'execute',return_value='fake') as send:
   clock.now.return_value=NOW
   self.assertEqual(a.step(MagicMock(),g,{},True,ai_confirm=True)['action'],'SELL')
   send.assert_called_once()
 def test_early_ai_exit_only_when_explicitly_enabled(self):
  g=self.guard;g.observe_loss=lambda *args:{'loss':'0','halted':False}
  s=Auto().snap();s['quote']['data']['Output_0'].update(bidp=1320,askp=1321)
  with patch.object(a,'collect_snapshots',return_value=s),patch.object(a,'datetime') as clock,patch.object(a,'halt_flag_set',return_value=False),patch.object(a,'ai_signal',return_value=('K200','digest')) as signal,patch.object(a,'execute',return_value='fake') as send:
   clock.now.return_value=NOW
   self.assertEqual(a.step(MagicMock(),g,{},True)['action'],'HOLD');signal.assert_not_called();send.assert_not_called()
   self.assertEqual(a.step(MagicMock(),g,{},True,ai_confirm=True)['action'],'SELL');send.assert_called_once()
 def test_buy_requires_both_existing_strategy_and_ai_when_enabled(self):
  g=self.guard;g.observe_loss=lambda *args:{'loss':'0','halted':False}
  s=Auto().snap();s['balance']['Output_1']=[];s['quote']['data']['Output_0'].update(bidp=1300,askp=1301)
  reader=MagicMock();reader.call_tool.return_value={'status':'ok','data':{}}
  rows=[{'stck_prpr':x} for x in range(1310,1300,-1)]
  with patch.object(a,'collect_snapshots',return_value=s),patch.object(a,'datetime') as clock,patch.object(a,'valid_bars',return_value=rows),patch.object(a,'halt_flag_set',return_value=False),patch.object(a,'ai_signal',return_value=None),patch.object(a,'execute',return_value='fake') as send:
   clock.now.return_value=NOW
   self.assertEqual(a.step(reader,g,{},True,ai_confirm=True)['action'],'WAIT');send.assert_not_called()
