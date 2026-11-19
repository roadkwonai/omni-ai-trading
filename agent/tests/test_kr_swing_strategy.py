import pandas as pd
import numpy as np
from strategies.kr_swing_v1.signal_engine import SignalEngine


def test_prefix_invariance_and_warmup():
    ix = pd.bdate_range('2020-01-01', periods=160)
    close = pd.Series(np.r_[np.arange(1, 121), np.arange(120, 80, -1)], index=ix, dtype=float)
    frame = pd.DataFrame({'close': close})
    engine = SignalEngine()
    whole = engine.generate({'x': frame})['x']
    partial = engine.generate({'x': frame.iloc[:120]})['x']
    pd.testing.assert_series_equal(whole.iloc[:120], partial)
    assert (whole.iloc[:59] == 0).all()
    assert whole.iloc[80] == .1
    assert whole.iloc[-1] == 0


def test_evaluation_starts_flat_but_indicators_use_history():
    idx = pd.bdate_range('2020-01-01', periods=100)
    frame = pd.DataFrame({'close': np.arange(1., 101.)}, index=idx)
    signal = SignalEngine(str(idx[80].date())).generate({'x': frame})['x']
    assert (signal.iloc[:80] == 0).all()
    assert signal.iloc[80] == .1


def test_order_cap_daily_cap_and_quota():
    from scripts.validate_kr_swing import CappedEngine
    from backtest.engines.base import _OpenOrder
    from unittest.mock import patch
    cfg = {'initial_cash': 30_000_000}
    engine = CappedEngine(cfg, quota=3_000_000)
    day = pd.Timestamp('2025-01-02')
    order = _OpenOrder('005930.KS', 1, 100, 30000, 1, 3_000_000, 0)
    with patch.object(type(engine).__mro__[1], '_execute_open_order') as execute:
        engine._execute_open_order(order, day)
        engine._execute_open_order(order, day + pd.Timedelta(days=1))
    assert execute.call_count == 1 and engine.buy_total == 3_000_000
    daily = CappedEngine(cfg)
    with patch.object(type(daily).__mro__[1], '_execute_open_order') as execute:
        for _ in range(3): daily._execute_open_order(order, day)
    assert execute.call_count == 2 and daily.buy_by_day[str(day.date())] == 6_000_000


def test_snapshot_rejects_byte_or_date_tampering(tmp_path):
    import hashlib, json, pytest
    from scripts.validate_kr_swing import validate_snapshot
    csv = tmp_path / 'x.csv'
    csv.write_text('date,open,high,low,close,volume\n2024-01-02,1,1,1,1,1\n')
    digest = hashlib.sha256(csv.read_bytes()).hexdigest()
    metadata = {'symbols': {'x': {'clean_sha256': digest}}, 'common_rows': 1,
                'common_first': '2024-01-02', 'common_last': '2024-01-02', 'excluded_dates': []}
    (tmp_path / 'metadata.json').write_text(json.dumps(metadata))
    assert len(validate_snapshot(tmp_path, ['x'])['x']) == 1
    csv.write_text(csv.read_text() + '\n')
    with pytest.raises(ValueError, match='hash mismatch'): validate_snapshot(tmp_path, ['x'])
    metadata['symbols']['x']['clean_sha256'] = hashlib.sha256(csv.read_bytes()).hexdigest()
    metadata['excluded_dates'] = ['2024-01-02']
    (tmp_path / 'metadata.json').write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match='quarantined'): validate_snapshot(tmp_path, ['x'])
