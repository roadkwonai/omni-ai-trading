import tempfile,unittest
from unittest.mock import patch
from pathlib import Path
from src.live.halt import trip_halt,read_halt,halt_flag_set
from nh_live_activation import resume_nh
class Activation(unittest.TestCase):
 def test_resume_preserves_other_broker_pauses(self):
  with tempfile.TemporaryDirectory() as d, patch.dict('os.environ',{'VIBE_TRADING_HOME':d}):
   trip_halt('cli','test global pause');before=read_halt()
   result=resume_nh(before)
   self.assertTrue(result['nh_resumed']);self.assertFalse(halt_flag_set('nhplug'));self.assertTrue(halt_flag_set('robinhood'))
   self.assertTrue((Path(d)/'live/nh-activation.json').exists())
 def test_changed_halt_is_not_cleared(self):
  with tempfile.TemporaryDirectory() as d, patch.dict('os.environ',{'VIBE_TRADING_HOME':d}):
   trip_halt('cli','new stop')
   with self.assertRaises(ValueError):resume_nh({'reason':'old stop'})
   self.assertTrue(halt_flag_set())
if __name__=='__main__':unittest.main()
