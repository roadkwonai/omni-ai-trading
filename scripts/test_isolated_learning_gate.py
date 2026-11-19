import sqlite3,tempfile,unittest
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from nh_paper_isolated import learned_filter
class Tests(unittest.TestCase):
 def test_minimum_and_time_boundary(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d); now=datetime(2026,11,6,14,0,tzinfo=ZoneInfo('Asia/Seoul'))
   self.assertEqual(learned_filter(root,'INV',now)['status'],'insufficient')
   with sqlite3.connect(root/'paper-model.sqlite3') as db:
    db.execute('CREATE TABLE predictions (symbol TEXT, decision_time TEXT, alpha INTEGER, beta INTEGER, probability REAL)')
    db.execute('INSERT INTO predictions VALUES (?,?,?,?,?)',('INV','2026-11-06T13:00:00+09:00',4,18,4/22))
   x=learned_filter(root,'INV',now);self.assertEqual(x['status'],'ready');self.assertEqual(x['samples'],20)
   with sqlite3.connect(root/'paper-model.sqlite3') as db:db.execute('UPDATE predictions SET decision_time=?',('2026-11-06T14:00:00+09:00',))
   with self.assertRaises(ValueError):learned_filter(root,'INV',now)
if __name__=='__main__':unittest.main()
