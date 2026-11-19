import json
import unittest
from unittest.mock import patch

import nh_live_ai_observer as o


class AIObserver(unittest.TestCase):
 def test_models_are_market_only_and_no_account_sent(self):
  payloads=[]
  def send(url,key,payload,*,image=False):
   payloads.append(payload)
   if image:
    return {'model':'jev-1.14.0','answers':{'candidate':{'type':'choice','choice':'INV','confidence':0.9}}}
   return {'answers':[{'name':'candidate','type':'choice','choice':'INV','confidence':0.9},{'name':'exit','type':'choice','choice':'HOLD','confidence':0.9}]}
  bars={name:[{'stck_prpr':'100'}]*6 for name in o.SYMBOLS}
  with patch.object(o,'_post',side_effect=send):
   result=o.judgments(bars,[],{'OPENAI_API_KEY':'openai-test','JEV_API_KEY':'jev-test','ACCOUNT_NUMBER':'12345678901'})
  self.assertEqual(result['decision']['choice'],'INV')
  self.assertEqual(result['jev']['choice'],'INV')
  text=json.dumps(payloads)
  self.assertNotIn('12345678901',text)
  self.assertNotIn('openai-test',text)
  self.assertIn('실계좌',json.dumps(payloads,ensure_ascii=False))
 def test_unexpected_jev_model_is_not_a_signal(self):
  bars={name:[{'stck_prpr':'100'}]*6 for name in o.SYMBOLS}
  with patch.object(o,'_post',side_effect=[{'answers':[{'name':'candidate','type':'choice','choice':'INV','confidence':0.9},{'name':'exit','type':'choice','choice':'HOLD','confidence':0.9}]}, {'model':'other','answers':{'candidate':{'type':'choice','choice':'INV','confidence':0.9}}}]):
   with self.assertRaises(ValueError):o.judgments(bars,[],{'OPENAI_API_KEY':'x','JEV_API_KEY':'y'})
 def test_atomic_publish_is_private_and_replaces(self):
  import os,tempfile
  from pathlib import Path
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'result.json'
   o.publish(path,{'status':'observed'})
   self.assertEqual(json.loads(path.read_text()),{'status':'observed'})
   self.assertEqual(os.stat(path).st_mode & 0o777,0o600)
   o.publish(path,{'status':'unavailable'})
   self.assertEqual(json.loads(path.read_text()),{'status':'unavailable'})
 def test_observer_lock_blocks_competing_writer(self):
  import tempfile
  from pathlib import Path
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'observer.lock'
   with o.observer_lock(path):
    with self.assertRaises(BlockingIOError):
     with o.observer_lock(path):pass
   with o.observer_lock(path):pass

if __name__=='__main__':unittest.main()
