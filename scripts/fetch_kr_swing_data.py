"""Freeze public Korean daily bars; quarantine invalid and non-common dates."""
import hashlib
import json
import time
from pathlib import Path
import numpy as np
import pandas as pd
from backtest.loaders.yahoo_loader import DataLoader
from backtest.loaders.registry import frame_caliber

SYMBOLS = ('005930.KS', '000660.KS', '035420.KS', '005380.KS', '105560.KS', '069500.KS')
START, END = '2020-01-01', '2026-11-05'
COLUMNS = ['open', 'high', 'low', 'close', 'volume']


def invalid_rows(frame):
    if frame is None or len(frame) < 1000 or not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError('missing daily data or insufficient history')
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError('dates must be unique and sorted')
    if frame.index.min() < pd.Timestamp(START) or frame.index.max() > pd.Timestamp(END):
        raise ValueError('data outside closed-bar date range')
    values = frame[COLUMNS].to_numpy(dtype=float)
    bad = (~np.isfinite(values).all(axis=1) | (values[:, :4] <= 0).any(axis=1) |
           (values[:, 4] < 0) | (values[:, 1] < values[:, [0, 2, 3]].max(axis=1)) |
           (values[:, 2] > values[:, [0, 1, 3]].min(axis=1)))
    return frame.loc[bad, COLUMNS]


def clean_frames(frames):
    invalid = {symbol: invalid_rows(frame) for symbol, frame in frames.items()}
    common = None
    for symbol, frame in frames.items():
        valid = frame.index.difference(invalid[symbol].index)
        common = valid if common is None else common.intersection(valid)
    if common is None or len(common) < 1000:
        raise ValueError('insufficient valid common history')
    return {symbol: frame.loc[common, COLUMNS] for symbol, frame in frames.items()}, invalid


def main():
    out = Path('.venv/kr-swing-data')
    raw = out / 'raw'
    raw.mkdir(parents=True, exist_ok=True)
    loader = DataLoader()
    frames = {}
    metadata = {'start': START, 'end': END, 'source': 'yahoo', 'survivorship_caveat': 'Universe selected in 2026, not point-in-time constituents.', 'quality_policy': 'Freeze unmodified raw bars; exclude every date with missing or invalid OHLCV for any symbol from all symbols. No fill or adjustment.', 'symbols': {}}
    for symbol in SYMBOLS:
        frame = loader.fetch([symbol], START, END, interval='1D').get(symbol)
        invalid_rows(frame)
        frames[symbol] = frame
        path = raw / (symbol + '.csv')
        frame[COLUMNS].to_csv(path, index_label='date')
        metadata['symbols'][symbol] = {'raw_rows': len(frame), 'raw_first': str(frame.index.min().date()), 'raw_last': str(frame.index.max().date()), 'raw_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'adjustment': frame_caliber(frame, 'yahoo', 'kr_equity', symbol)}
        print('Fetched', symbol, len(frame), flush=True)
        time.sleep(1)
    clean, invalid = clean_frames(frames)
    union = frames[SYMBOLS[0]].index
    for frame in list(frames.values())[1:]:
        union = union.union(frame.index)
    common = clean[SYMBOLS[0]].index
    metadata['excluded_dates'] = [str(d.date()) for d in union.difference(common)]
    metadata['excluded_count'] = len(metadata['excluded_dates'])
    metadata['common_rows'] = len(common)
    metadata['common_first'] = str(common.min().date())
    metadata['common_last'] = str(common.max().date())
    metadata['missing_dates'] = {s: [str(d.date()) for d in union.difference(f.index)] for s, f in frames.items()}
    metadata['invalid_dates'] = {s: [str(d.date()) for d in f.index] for s, f in invalid.items()}
    quarantined = {s: [{'date': str(date.date()), 'reason': 'invalid OHLCV: nonfinite, nonpositive, negative volume, or high/low inconsistency', 'row': {k: (None if not np.isfinite(float(v)) else float(v)) for k, v in row.items()}} for date, row in f.iterrows()] for s, f in invalid.items()}
    (out / 'invalid_rows.json').write_text(json.dumps(quarantined, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    for symbol, frame in clean.items():
        path = out / (symbol + '.csv')
        frame.to_csv(path, index_label='date')
        metadata['symbols'][symbol]['clean_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    (out / 'metadata.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    print('Common rows:', len(common), 'excluded dates:', metadata['excluded_count'], flush=True)


if __name__ == '__main__':
    main()
