from datetime import date
from pathlib import Path
import hashlib
import json
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import kr_swing_v5_daily_data as data


def bars():
    index = pd.bdate_range(end='2026-11-05', periods=125)
    close = np.linspace(100, 200, len(index))
    return pd.DataFrame({'open':close, 'high':close+1, 'low':close-1,
                         'close':close, 'volume':np.full(len(index),100)}, index=index)


def test_reject_future_missing_and_revised_history():
    old = {c:bars() for c in data.CODES}
    future = {c:f.copy() for c,f in old.items()}
    row = future[data.CODES[0]].iloc[-1].copy()
    for f in future.values(): f.loc[pd.Timestamp('2026-11-06')] = row
    with pytest.raises(ValueError, match='completed'):
        data.merge_frames(old, future, date(2026,11,6), date(2026,11,6))
    bad = {c:f.copy() for c,f in future.items()}
    bad[data.CODES[0]].iloc[-10, 0] *= 1.001
    with pytest.raises(ValueError, match='revised'):
        data.merge_frames(old, bad, date(2026,11,6), date(2026,11,7))
    bad = {c:f.copy() for c,f in future.items()}
    bad[data.CODES[0]] = bad[data.CODES[0]].iloc[:-1]
    with pytest.raises(ValueError, match='mismatch'):
        data.merge_frames(old, bad, date(2026,11,6), date(2026,11,7))


def test_noise_keeps_exact_old_values_and_new_days_are_shared():
    old = {c:bars() for c in data.CODES}
    fetched = {c:f.copy() for c,f in old.items()}
    for f in fetched.values():
        f.loc[:, 'open'] *= 1 + 1e-8
        f.loc[pd.Timestamp('2026-11-06')] = f.iloc[-1]
    merged = data.merge_frames(old, fetched, date(2026,11,6), date(2026,11,7))
    for code in data.CODES:
        pd.testing.assert_frame_equal(merged[code].iloc[:-1], old[code], check_freq=False, check_dtype=False)
        assert merged[code].index[-1] == pd.Timestamp('2026-11-06')


def test_small_rounding_change_that_flips_signal_is_not_accepted():
    old = {c:bars() for c in data.CODES}
    for frame in old.values():
        frame.loc[:, 'open'] = 100.
        frame.loc[:, 'high'] = 101.
        frame.loc[:, 'low'] = 99.
        frame.loc[:, 'close'] = 100.
    fetched = {c:f.copy() for c,f in old.items()}
    fetched[data.CODES[0]].iloc[-1, fetched[data.CODES[0]].columns.get_loc('close')] += 0.00001
    with pytest.raises(ValueError, match='signal revised'):
        data.merge_frames(old, fetched, date(2026,11,5), date(2026,11,6))


def test_weekday_gap_is_not_silently_assumed_holiday():
    old = {c:bars() for c in data.CODES}
    fetched = {c:f.copy() for c,f in old.items()}
    for frame in fetched.values():
        frame.loc[pd.Timestamp('2026-11-09')] = frame.iloc[-1]
    with pytest.raises(ValueError, match='holiday requires verification'):
        data.merge_frames(old, fetched, date(2026,11,9), date(2026,11,10))


def test_official_holiday_allows_gap_but_not_missing_trading_day():
    old = {c:bars() for c in data.CODES}
    fetched = {c:f.copy() for c,f in old.items()}
    for frame in fetched.values():
        frame.loc[pd.Timestamp('2026-11-09')] = frame.iloc[-1]
    merged = data.merge_frames(old, fetched, date(2026,11,9), date(2026,11,10),
                               holidays={date(2026,11,6)})
    assert merged[data.CODES[0]].index[-1] == pd.Timestamp('2026-11-09')
    with pytest.raises(ValueError, match='missing or mismatch'):
        data.merge_frames(old, fetched, date(2026,11,9), date(2026,11,10), holidays=set())


def test_krx_calendar_is_validated_and_unavailable_fails_closed(monkeypatch):
    class Response:
        status_code = 200
        def __init__(self, text='', obj=None): self.text, self.obj = text, obj
        def json(self): return self.obj
    class Session:
        def __init__(self): self.headers={}; self.trust_env=True
        def get(self, url, **kwargs):
            if 'GenerateOTP' in url: return Response(text='official-otp')
            if 'OPN99000001' in url: return Response(obj={'block1':[{'bas_yy':'2026'}]})
            return Response(text='html')
        def post(self, url, **kwargs):
            return Response(obj={'block1':[{'calnd_dd':'2026-05-01','holdy_nm':'근로자의날'}]})
        def close(self): pass
    monkeypatch.setattr(data.requests, 'Session', Session)
    calendar = data.krx_holidays({2026})
    assert calendar == {date(2026,5,1)}
    class Offline(Session):
        def post(self, *args, **kwargs): raise data.requests.ConnectionError()
    monkeypatch.setattr(data.requests, 'Session', Offline)
    with pytest.raises(ValueError, match='KRX holiday'):
        data.krx_holidays({2026})


def test_krx_calendar_rejects_unknown_year_empty_and_malformed(monkeypatch):
    class Response:
        status_code = 200
        text = 'otp'
        def __init__(self, obj): self.obj=obj
        def json(self): return self.obj
    class Session:
        def __init__(self): self.headers={}; self.trust_env=True
        def get(self, url, **kwargs):
            if 'OPN99000001' in url: return Response({'block1':[{'bas_yy':'2026'}]})
            return Response(None)
        def post(self, *args, **kwargs): return Response({'block1':[]})
        def close(self): pass
    monkeypatch.setattr(data.requests, 'Session', Session)
    for years in ({2027},{2026}):
        with pytest.raises(ValueError, match='KRX holiday'):
            data.krx_holidays(years)


def test_published_snapshot_survives_failed_stage(tmp_path, monkeypatch):
    source = tmp_path/'source'; source.mkdir()
    symbols = {}
    for code in data.CODES:
        p=source/f'{code}.csv'; bars().to_csv(p,index_label='date')
        symbols[code]={'clean_sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    (source/'metadata.json').write_text(json.dumps({'symbols':symbols,'common_last':'2026-11-05','common_rows':125}))
    root=tmp_path/'runtime'
    data.initialize(root, source)
    previous=(root/'current').resolve()
    assert all((previous/f'{code}.csv').read_bytes()==(source/f'{code}.csv').read_bytes() for code in data.CODES)
    frames={c:bars() for c in data.CODES}
    for f in frames.values(): f.loc[pd.Timestamp('2026-11-06')] = f.iloc[-1]
    def fail(*args, **kwargs): raise OSError('simulated stage failure')
    monkeypatch.setattr(data, '_write_csv', fail)
    with pytest.raises(OSError, match='stage failure'):
        data.update(root, frames, date(2026,11,6), date(2026,11,7))
    assert (root/'current').resolve()==previous
    data.verify_snapshot(previous)


def test_update_preserves_prior_csv_bytes(tmp_path):
    source = tmp_path/'source'; source.mkdir()
    symbols = {}
    for code in data.CODES:
        path = source/f'{code}.csv'; bars().to_csv(path, index_label='date')
        symbols[code] = {'clean_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    (source/'metadata.json').write_text(json.dumps({'symbols':symbols,'common_last':'2026-11-05','common_rows':125}))
    root=tmp_path/'runtime'
    first=data.initialize(root, source)
    original={c:(first/f'{c}.csv').read_bytes() for c in data.CODES}
    fetched={c:bars() for c in data.CODES}
    for frame in fetched.values():
        frame.loc[pd.Timestamp('2026-11-06')] = frame.iloc[-1]
    second=data.update(root,fetched,date(2026,11,6),date(2026,11,7))
    assert second != first
    for code in data.CODES:
        assert (second/f'{code}.csv').read_bytes().startswith(original[code])
        assert (first/f'{code}.csv').read_bytes() == original[code]
    assert data.verify_snapshot(second)[data.CODES[0]].index[-1] == pd.Timestamp('2026-11-06')
