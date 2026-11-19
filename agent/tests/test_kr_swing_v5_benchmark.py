from datetime import date
import importlib.util
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

spec = importlib.util.spec_from_file_location('v5bench', Path(__file__).resolve().parents[2] / 'scripts/kr_swing_v5_benchmark.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def bars():
    ix = pd.bdate_range('2026-07-01', periods=100)
    out = {}
    for n, code in enumerate(m.CODES):
        close = 10000 * np.exp(np.cumsum(np.sin(np.arange(100) / 4 + n) * .02 + .001))
        out[code] = pd.DataFrame({'open': close, 'close': close}, index=ix)
    return out


def test_frozen_weights_are_causal_and_no_fill_on_initialization():
    frames = bars(); baseline = frames[m.CODES[0]].index[75].date()
    reg = m.register(frames, baseline, date(2027, 1, 1))
    changed = {k: v.copy() for k, v in frames.items()}
    for frame in changed.values(): frame.loc[frame.index > pd.Timestamp(baseline), 'close'] *= 5
    assert m.register(changed, baseline, date(2027, 1, 1))['gross_weight'] == reg['gross_weight']
    assert reg['pending_entry'] and reg['cash'] == m.CAPITAL and reg['positions'] == {}
    assert reg['gross_weight'] <= .5


def test_execution_cost_idempotence_and_unmatched_days():
    frames = bars(); idx = frames[m.CODES[0]].index
    reg = m.register(frames, idx[75].date(), idx[76].date())
    same_day = m.advance(reg, frames, idx[76].date(), date(2027,1,1))
    assert same_day['pending_entry'] and same_day['observations'] == []
    state = m.advance(same_day, frames, idx[77].date(), date(2027,1,1))
    assert not state['pending_entry'] and len(state['positions']) == 5
    assert state['nav'] < m.CAPITAL and state['cash'] >= 0
    assert m.advance(state, frames, idx[77].date(), date(2027,1,1)) == state
    changed = {k: v.copy() for k,v in frames.items()}
    changed[m.CODES[0]] = changed[m.CODES[0]].drop(idx[78])
    with pytest.raises(ValueError): m.advance(state, changed, idx[78].date(), date(2027,1,1))


def test_refuses_lookahead_stale_and_invalid_history():
    frames = bars(); idx = frames[m.CODES[0]].index
    with pytest.raises(ValueError): m.register(frames, idx[75].date(), idx[75].date())
    bad = {k: v.copy() for k,v in frames.items()}; bad[m.CODES[0]].loc[idx[74], 'close'] = float('nan')
    with pytest.raises(ValueError): m.register(bad, idx[75].date(), date(2027,1,1))
    flat = {k: v.assign(close=1.) for k,v in frames.items()}
    with pytest.raises(ValueError): m.register(flat, idx[75].date(), date(2027,1,1))
