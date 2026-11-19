import unittest
from nh_live_strategy import intent
class Rules(unittest.TestCase):
 def test_either_stop_is_sufficient_at_exact_boundary(self):
  self.assertEqual(intent(**self.args(entry=1000,bid=980,ask=981,daily_loss=0))['reason'],'position loss reached 2 percent')
  self.assertEqual(intent(**self.args(entry=1000,bid=981,ask=982,daily_loss=19999))['action'],'HOLD')
  self.assertEqual(intent(**self.args(entry=1000,bid=1000,ask=1001,daily_loss=20000))['reason'],'daily account loss limit')
  self.assertEqual(intent(**self.args(entry=1000,bid=980,ask=981,pending=1))['action'],'CANCEL_THEN_SELL')
 def args(self,**kw):
  d={'shares':1,'pending':0,'daily_loss':0,'bid':1309,'ask':1310,'entry':1309};d.update(kw);return d
 def test_exits_and_pending_priority(self):
  self.assertEqual(intent(**self.args(bid=1282,ask=1283))['action'],'SELL')
  self.assertEqual(intent(**self.args(bid=1336,ask=1337,pending=2))['action'],'CANCEL_THEN_SELL')
  self.assertEqual(intent(**self.args(daily_loss=20000))['action'],'SELL')
  self.assertEqual(intent(**self.args(daily_loss=20000,shares=2))['action'],'SELL')
  self.assertEqual(intent(**self.args(shares=2))['action'],'HOLD')
  self.assertEqual(intent(**self.args(daily_loss=20000,shares=0,pending=1))['action'],'CANCEL_ORDERS')
  self.assertEqual(intent(**self.args())['action'],'HOLD')
 def test_entry_does_not_ignore_orders(self):
  self.assertEqual(intent(**self.args(shares=0,pending=2,closes=range(1300,1310)))['action'],'RECONCILE')
  self.assertEqual(intent(**self.args(shares=0,closes=range(1300,1310)))['action'],'BUY')
  self.assertEqual(intent(**self.args(shares=0,closes=[]))['action'],'WAIT')
 def test_invalid_prices(self):
  with self.assertRaises(ValueError):intent(**self.args(bid='NaN'))
if __name__=='__main__':unittest.main()


class PriceCap(unittest.TestCase):
 def test_entry_waits_above_the_account_price_cap(self):
  rising=[200000+i*100 for i in range(10)]
  args=dict(shares=0,pending=0,daily_loss=0,bid=210000,ask=210100,closes=rising)
  self.assertEqual(intent(**args)['reason'],'price cap or spread')           # default 10,000 KRW cap
  self.assertEqual(intent(**args,price_cap=300_000)['action'],'BUY')
