import numpy as np
import pandas as pd
from backtest.engines.base import _align
from strategies.kr_swing_v4.signal_engine import SignalEngine


def test_prefix_invariance_warmup_and_next_open():
    dates = pd.bdate_range('2023-01-01', periods=240)
    close = pd.Series(np.arange(1., 241.), index=dates)
    frame = pd.DataFrame({'open':close, 'high':close, 'low':close, 'close':close, 'volume':1000})
    signal = SignalEngine().generate({'x':frame})['x']
    pd.testing.assert_series_equal(signal.iloc[:220], SignalEngine().generate({'x':frame.iloc[:220]})['x'])
    assert signal.iloc[:199].eq(0).all() and signal.iloc[199] == .025
    _, _, _, targets, _ = _align({'x':frame}, {'x':signal}, ['x'])
    assert targets['x'].iloc[199] == 0 and targets['x'].iloc[200] == .025
