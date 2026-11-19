import numpy as np
import pandas as pd
from strategies.kr_swing_v3.signal_engine import SignalEngine
from backtest.engines.base import _align


def test_200_day_warmup_prefix_invariance_and_next_bar():
    idx = pd.bdate_range('2023-01-01', periods=240)
    close = pd.Series(np.arange(1., 241.), index=idx)
    frame = pd.DataFrame({'open': close, 'high': close, 'low': close, 'close': close, 'volume': 1000})
    whole = SignalEngine().generate({'x': frame})['x']
    partial = SignalEngine().generate({'x': frame.iloc[:220]})['x']
    pd.testing.assert_series_equal(whole.iloc[:220], partial)
    assert whole.iloc[:199].eq(0).all() and whole.iloc[199] == .05
    _, _, _, positions, _ = _align({'x': frame}, {'x': whole}, ['x'])
    assert positions['x'].iloc[199] == 0 and positions['x'].iloc[200] == .05
