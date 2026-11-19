import numpy as np
import pandas as pd
from strategies.kr_swing_v5.signal_engine import SignalEngine
from backtest.engines.base import _align


def test_signal_causal_and_filled_next_bar():
    ix = pd.bdate_range('2023-01-01', periods=180)
    close = pd.Series(np.arange(1., 181.), index=ix)
    frame = pd.DataFrame({'open':close,'high':close,'low':close,'close':close,'volume':1000})
    sig = SignalEngine().generate({'x':frame})['x']
    pd.testing.assert_series_equal(sig.iloc[:140],SignalEngine().generate({'x':frame.iloc[:140]})['x'])
    assert sig.iloc[:119].eq(0).all() and sig.iloc[119] == .025
    _, _, _, target, _ = _align({'x':frame},{'x':sig},['x'])
    assert target['x'].iloc[119] == 0 and target['x'].iloc[120] == .025
