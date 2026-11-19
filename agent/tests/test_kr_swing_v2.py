import numpy as np
import pandas as pd
from strategies.kr_swing_v2.signal_engine import SignalEngine
from backtest.engines.base import _align


def test_signal_uses_only_prior_data_and_executes_next_bar():
    index = pd.bdate_range('2024-01-01', periods=160)
    close = pd.Series(np.arange(1.0, 161.0), index=index)
    frame = pd.DataFrame({'open':close, 'high':close, 'low':close, 'close':close, 'volume':1000})
    whole = SignalEngine().generate({'x':frame})['x']
    prefix = SignalEngine().generate({'x':frame.iloc[:130]})['x']
    pd.testing.assert_series_equal(whole.iloc[:130], prefix)
    assert whole.iloc[:119].eq(0).all() and whole.iloc[119] == .1
    _, _, _, positions, _ = _align({'x':frame}, {'x':whole}, ['x'])
    assert positions['x'].iloc[119] == 0 and positions['x'].iloc[120] == .1
