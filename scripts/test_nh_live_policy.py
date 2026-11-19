import unittest,tempfile,sqlite3
from pathlib import Path
from nh_live_policy import decide,record
class PolicyTests(unittest.TestCase):
 def obs(self,loss=0,shares=1,opened=0,latched=False):
  return {'time':'2026-11-06T13:00:00+09:00','symbol':'114800','shares':shares,'open_order_rows':opened,'risk':{'loss_estimate_krw':loss,'halt_latched':latched}}
 def test_threshold_and_pending_orders(self):
  self.assertEqual(decide(self.obs(19999),halted=False)['desired'],'HOLD')
  self.assertEqual(decide(self.obs(20000),halted=False)['desired'],'SELL')
  self.assertEqual(decide(self.obs(20000,opened=2),halted=False)['desired'],'RECONCILE_THEN_SELL')
  self.assertEqual(decide(self.obs(latched=True),halted=False)['desired'],'SELL')
 def test_halt_transport_and_unknown_input(self):
  self.assertEqual(decide(self.obs(20000),halted=True,transport_ready=True)['execution'],'BLOCKED')
  self.assertEqual(decide(self.obs(20000),halted=False)['execution'],'BLOCKED')
  self.assertEqual(decide({},halted=False)['execution'],'BLOCKED')
  self.assertEqual(decide(self.obs(opened=2),halted=False)['desired'],'RECONCILE')
 def test_audit_is_idempotent(self):
  with tempfile.TemporaryDirectory() as root:
   p=Path(root)/'policy.db';o=self.obs();d=decide(o,halted=False);record(p,o,d);record(p,o,d)
   with sqlite3.connect(p) as db:self.assertEqual(db.execute('select count(*) from decisions').fetchone()[0],1)
if __name__=='__main__':unittest.main()
