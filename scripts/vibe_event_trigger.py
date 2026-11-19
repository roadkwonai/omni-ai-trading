"""Price-only triggers for an isolated AI review; never submits an order.

Thresholds are hypotheses for paper validation, not proven trading rules.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from vibe_fast_exit import KST, _number, _stamp

MIN_BARS = 3
PEAK_BARS = 6
RISE_PCT = 0.005
RETRACEMENT_PCT = 0.005
EARLY_LOSS_PCT = 0.005


def detect(*, symbol, position_id, qty, now, entry_at, entry_price, profile,
           bars, bars_symbol, bars_observed_at, quote, quote_observed_at):
    """Describe a fresh price event without relying on prior model or news availability.

    The caller must independently verify the current position and its generation.
    Bars are original NH rows with their date; only complete post-entry minutes count.
    """
    result = {'state': 'unavailable', 'triggered': False, 'reasons': [],
              'unavailable': [], 'not_met': [], 'checks': {}, 'inputs': {},
              'mode': 'observation_only', 'limitations': '시험 가설이며 주문·체결 판단 아님'}
    missing = result['unavailable']
    local, entered = _stamp(now), _stamp(entry_at)
    observed, quote_observed = _stamp(bars_observed_at), _stamp(quote_observed_at)
    avg = _number(entry_price)
    if not isinstance(symbol, str) or not re.fullmatch(r'\d{6}', symbol):
        missing.append('symbol_invalid')
    if not isinstance(position_id, str) or not re.fullmatch(r'[a-f0-9]{20}', position_id) or type(qty) is not int or qty <= 0:
        missing.append('position_identity_invalid')
    if local is None or entered is None or entered > local or avg is None or avg <= 0:
        missing.append('position_invalid')
    if profile != 'normal':
        missing.append('non_general_position')
    if bars_symbol != symbol or not isinstance(bars_symbol, str):
        missing.append('bars_symbol_mismatch')
    if local is None:
        return result
    result['inputs']['evaluated_at'] = local.isoformat()
    if not ('09:00' <= local.strftime('%H:%M') < '15:20') or local.weekday() >= 5:
        missing.append('outside_regular_session')
    if observed is None or not timedelta(0) <= local - observed <= timedelta(seconds=30):
        missing.append('bars_observation_stale')

    parsed = []
    try:
        if not isinstance(bars, list):
            raise ValueError
        for row in bars:
            if not isinstance(row, dict) or row.get('bsop_date') != local.strftime('%Y%m%d'):
                raise ValueError
            stamp = datetime.strptime(row['bsop_date'] + row['bsop_time'], '%Y%m%d%H%M%S').replace(tzinfo=KST)
            if stamp.second or observed is None or stamp > observed:
                raise ValueError
            if stamp >= local.replace(second=0, microsecond=0) - timedelta(minutes=1):
                continue
            if entered is None or stamp < entered:
                continue
            opn, high, low, close, vol = (_number(row.get(k)) for k in
                                           ('stck_oprc', 'stck_hgpr', 'stck_lwpr', 'stck_prpr', 'vol'))
            if (any(v is None for v in (opn, high, low, close, vol)) or low <= 0 or vol < 0
                    or not low <= min(opn, close) <= max(opn, close) <= high):
                raise ValueError
            parsed.append((stamp, high, close, vol))
        parsed.sort(key=lambda row: row[0])
        parsed = parsed[-PEAK_BARS:]
        if (len(parsed) < MIN_BARS or any(b[0] - a[0] != timedelta(minutes=1) for a, b in zip(parsed, parsed[1:]))
                or observed is None or parsed[-1][0] + timedelta(minutes=1) > observed
                or not timedelta(0) < local - parsed[-1][0] <= timedelta(minutes=4)):
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        missing.append('bars_invalid_or_insufficient')
        parsed = []

    bid = None
    try:
        if not isinstance(quote, dict) or quote.get('iem_cd') != symbol or quote.get('market_status') != '1':
            raise ValueError
        quote_at = datetime.strptime(local.strftime('%Y%m%d') + quote['hoga_bsop_hour'], '%Y%m%d%H:%M:%S').replace(tzinfo=KST)
        bid = _number(quote.get('bidp'))
        if (quote_observed is None or not quote_at <= quote_observed <= local
                or not timedelta(0) <= local - quote_at <= timedelta(seconds=15) or bid is None or bid <= 0):
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        missing.append('quote_invalid_or_stale')
        bid = None
    if missing:
        return result

    recent = parsed[-MIN_BARS:]
    peak = max(row[1] for row in parsed)
    preceding = parsed[:MIN_BARS]
    recent_volume = sum(row[3] for row in recent)
    earlier_volume = sum(row[3] for row in preceding)
    checks = {
        'rise_from_entry': peak >= avg * (1 + RISE_PCT),
        'rise_in_window': peak >= parsed[0][2] * (1 + RISE_PCT),
        'retracement': bid <= peak * (1 - RETRACEMENT_PCT),
        'volume_rising': len(parsed) == PEAK_BARS and earlier_volume > 0 and recent_volume > earlier_volume,
        'early_loss': bid <= avg * (1 - EARLY_LOSS_PCT),
        'falling_closes': recent[0][2] > recent[1][2] > recent[2][2],
        'latest_volume_rising': recent[-1][3] > 0 and recent[-1][3] >= recent[-2][3],
    }
    reasons = []
    if all(checks[k] for k in ('rise_from_entry', 'rise_in_window', 'retracement', 'volume_rising')):
        reasons.append('rapid_rise_retracement')
    if all(checks[k] for k in ('early_loss', 'falling_closes', 'latest_volume_rising')):
        reasons.append('rapid_loss')
    result['inputs'].update({'symbol': symbol, 'position_id': position_id, 'quantity': qty,
                             'entry_at': entered.isoformat(), 'entry_price': avg, 'bid': bid,
                             'quote_at': quote_at.isoformat(), 'quote_observed_at': quote_observed.isoformat(),
                             'bars_observed_at': observed.isoformat(), 'bars_from': parsed[0][0].isoformat(),
                             'bars_through': parsed[-1][0].isoformat(), 'window_peak': peak,
                             'first_close': parsed[0][2], 'last_three_closes': [row[2] for row in recent],
                             'earlier_volume': earlier_volume if len(parsed) == PEAK_BARS else None,
                             'recent_volume': recent_volume})
    result.update(state='triggered' if reasons else 'wait', triggered=bool(reasons), reasons=reasons,
                  checks=checks, not_met=[key for key, value in checks.items() if not value])
    return result
