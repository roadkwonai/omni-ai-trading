"""Pre-registered synthetic risk-matched basket; never contacts or orders a broker."""
from __future__ import annotations

import argparse
import copy
import fcntl
from datetime import date, datetime
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CODES = ('005930.KS', '000660.KS', '035420.KS', '005380.KS', '105560.KS')
CAPITAL = 30_000_000
TARGET_VOL = 0.059378058755319554  # v5 2025-01-01 to 2026-08-06 research volatility
FEE = 0.002
SLIPPAGE = 0.0005


def _history(frames, end):
    if set(frames) != set(CODES):
        raise ValueError('expected the complete five-symbol universe')
    result = {}
    ref = None
    for code in CODES:
        frame = frames[code]
        if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is not None:
            raise ValueError('expected timezone-naive daily index')
        if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
            raise ValueError('duplicate or unsorted sessions')
        part = frame.loc[:pd.Timestamp(end), ['open', 'close']].copy()
        if part.empty or not np.isfinite(part.to_numpy(dtype=float)).all() or (part <= 0).any().any():
            raise ValueError('missing or invalid prices')
        if ref is not None and not part.index.equals(ref):
            raise ValueError('incomplete or mismatched sessions')
        ref = part.index
        result[code] = part
    return result


def _digest(frames):
    # Exact date and decimal values bind the complete history already processed.
    h = sha256()
    for code in CODES:
        h.update(code.encode())
        h.update(frames[code].to_csv(index_label='date', float_format='%.12g').encode())
    return h.hexdigest()


def register(frames, baseline: date, today: date):
    if baseline >= today:
        raise ValueError('baseline must be a completed prior day')
    clean = _history(frames, baseline)
    index = clean[CODES[0]].index
    if len(index) < 61 or index[-1].date() != baseline:
        raise ValueError('at least 61 aligned completed sessions ending on baseline required')
    closes = pd.DataFrame({c: clean[c]['close'] for c in CODES})
    basket = closes.pct_change(fill_method=None).tail(60).mean(axis=1)
    vol = float(basket.std(ddof=1) * math.sqrt(252))
    if not math.isfinite(vol) or vol <= 0:
        raise ValueError('basket volatility unavailable')
    gross = min(TARGET_VOL / vol, .5)
    return {'version': 1, 'status': 'registered_pending_first_future_session',
            'baseline': baseline.isoformat(), 'registered_date_kst': today.isoformat(), 'last_session': baseline.isoformat(),
            'history_sha256': _digest(clean), 'cash': float(CAPITAL), 'positions': {},
            'pending_entry': True, 'gross_weight': gross, 'target_annual_vol': TARGET_VOL,
            'basket_trailing_60_vol': vol, 'nav': float(CAPITAL), 'observations': [],
            'source': 'Yahoo split/dividend-adjusted OHLC; synthetic integer shares, no broker orders',
            'risk_note': '5.94% is v5 historical volatility over 2025-01-01 through 2026-08-06; forecast matching cannot guarantee equal future risk',
            'cost_note': '0.20% buy commission and 0.05% buy slippage; 0.20% sell tax + 0.20% sell commission if liquidation is later modeled',
            'policy': 'equal weights sized once at first subsequent observed open, remainder cash; no rebalancing or liquidation; no future prices used at registration'}


def advance(state, frames, end: date, today: date):
    if end >= today:
        raise ValueError('only completed prior sessions can be processed')
    state = copy.deepcopy(state)
    try:
        registered = date.fromisoformat(state['registered_date_kst'])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('registration date missing; original start must be verified') from exc
    prior = date.fromisoformat(state['last_session'])
    if end < prior:
        raise ValueError('cannot move backwards')
    if _digest(_history(frames, prior)) != state['history_sha256']:
        raise ValueError('previously processed data changed')
    clean = _history(frames, end)
    index = clean[CODES[0]].index
    if index[-1].date() != end:
        raise ValueError('requested completed session missing')
    if end == prior:
        return state
    for ts in index[index > pd.Timestamp(prior)]:
        # The registration-day open was already in the past when the state was created.
        if ts.date() <= registered:
            continue
        if state['pending_entry']:
            for code in CODES:
                price = float(clean[code].loc[ts, 'open']) * (1 + SLIPPAGE)
                target = CAPITAL * state['gross_weight'] / len(CODES)
                shares = math.floor(target / (price * (1 + FEE)))
                cost = shares * price * (1 + FEE)
                if cost > state['cash'] + 1e-6:
                    raise ValueError('insufficient cash')
                state['cash'] -= cost
                state['positions'][code] = shares
            state['pending_entry'] = False
            state['entry_session'] = ts.date().isoformat()
            state['status'] = 'observing'
        state['nav'] = state['cash'] + sum(state['positions'][c] * float(clean[c].loc[ts, 'close']) for c in CODES)
        state['observations'].append({'date': ts.date().isoformat(), 'nav': state['nav'],
                                      'return': state['nav'] / CAPITAL - 1})
    state['last_session'] = end.isoformat()
    state['history_sha256'] = _digest(clean)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=ROOT / '.venv/kr-swing-data')
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--as-of', type=date.fromisoformat, required=True)
    parser.add_argument('--init', action='store_true')
    args = parser.parse_args()
    today = datetime.now(ZoneInfo('Asia/Seoul')).date()
    frames = {c: pd.read_csv(args.data / f'{c}.csv', parse_dates=['date']).set_index('date') for c in CODES}
    args.state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = args.state.parent / (args.state.name + '.lock')
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(lock_fd, 'r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.init:
            if args.state.exists():
                raise ValueError('existing state will not be overwritten')
            state = register(frames, args.as_of, today)
        else:
            state = advance(json.loads(args.state.read_text()), frames, args.as_of, today)
        fd, name = tempfile.mkstemp(prefix='.benchmark-', dir=args.state.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w') as f:
                json.dump(state, f, ensure_ascii=False, indent=2, allow_nan=False)
                f.flush(); os.fsync(f.fileno())
            if args.init:
                os.link(name, args.state)  # exclusive creation; never overwrite another registration
            else:
                os.replace(name, args.state)
        finally:
            if os.path.exists(name): os.unlink(name)
    print(json.dumps({'status': state['status'], 'baseline': state['baseline'],
                      'last_session': state['last_session'], 'nav': state['nav']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
