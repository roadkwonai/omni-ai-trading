import unittest
import pandas as pd
from fetch_kr_swing_data import invalid_rows, clean_frames

class DataValidation(unittest.TestCase):
    def test_nonfinite_and_duplicate_rejected(self):
        frame = pd.DataFrame({'open':1.,'high':2.,'low':1.,'close':1.,'volume':1.}, index=pd.bdate_range('2020-01-01', periods=1002))
        frame.iloc[5, 3] = float('nan')
        self.assertEqual(len(invalid_rows(frame)), 1)
        clean, invalid = clean_frames({'a': frame, 'b': frame.fillna(1)})
        self.assertEqual(len(invalid['a']), 1)
        self.assertEqual(clean['a'].index.tolist(), clean['b'].index.tolist())
        self.assertNotIn(frame.index[5], clean['b'].index)
        frame.index = frame.index.where(frame.index != frame.index[1], frame.index[0])
        with self.assertRaises(ValueError): invalid_rows(frame)

if __name__ == '__main__': unittest.main()
