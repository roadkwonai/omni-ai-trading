"""Experimental, observation-only early-exit candidate; never sends orders or writes a ledger.

The 0.5% thresholds are unvalidated hypotheses, not profit targets or advice. A quote is an
observation, not a fill; fees, taxes, spread changes and market impact are not simulated.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
MIN_BARS = 6
PEAK_GAIN = 0.005
RETRACEMENT = 0.005


def _number(value):
    try:
        if isinstance(value, bool):
            return None
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _stamp(value):
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        return result.astimezone(KST) if result.tzinfo is not None and result.utcoffset() is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def evaluate(*, symbol, now, entry_at, entry_price, profile, existing_exit,
             bars, bars_symbol, bars_observed_at, quote, advice, context_status=None):
    """Return an auditable candidate or wait, with missing inputs and failed tests separated.

    Bars are raw NH rows with their original date, not the date-stripped valid_bars result.
    Their observation time and an advice's start time must both be known. The six-bar VWAP is
    local to the validated window; it is not the session VWAP. Call only on a verified holding.
    """
    result = {'mode': 'observation_only', 'candidate': False, 'state': 'unavailable',
              'unavailable': [], 'not_met': [], 'inputs': {},
              'context_status': {},
              'limitations': '실제 체결 아님; 수수료·세금·호가 변동 미반영; 기준값은 검증 전 가설'}
    missing = result['unavailable']
    local, entered, observed = _stamp(now), _stamp(entry_at), _stamp(bars_observed_at)
    avg = _number(entry_price)
    if not isinstance(symbol, str) or not re.fullmatch(r'\d{6}', symbol):
        missing.append('symbol_invalid')
    if not isinstance(bars_symbol, str) or bars_symbol != symbol:
        missing.append('bars_symbol_mismatch')
    if local is None or entered is None or entered > local or avg is None or avg <= 0:
        missing.append('position_invalid')
    if profile != 'normal':
        missing.append('non_general_position')
    if existing_exit is not False:
        missing.append('existing_exit_or_unknown')
    if local is None:
        return result
    result['inputs']['evaluated_at'] = local.isoformat()
    for key, max_age in (('market', timedelta(minutes=20)), ('news', timedelta(minutes=30))):
        item = context_status.get(key) if isinstance(context_status, dict) else None
        at = _stamp(item.get('observed_at')) if isinstance(item, dict) else None
        if (not isinstance(item, dict) or item.get('status') != 'ok' or at is None
                or not timedelta(0) <= local - at <= max_age):
            missing.append(f'{key}_unavailable_or_stale')
            result['context_status'][key] = 'unavailable'
        else:
            result['context_status'][key] = 'ok'
            result['inputs'][f'{key}_observed_at'] = at.isoformat()
    if observed is None or not timedelta(0) <= local - observed <= timedelta(seconds=30):
        missing.append('bars_observation_stale')
    else:
        result['inputs']['bars_observed_at'] = observed.isoformat()

    parsed = []
    try:
        if not isinstance(bars, list):
            raise ValueError
        for row in bars:
            if not isinstance(row, dict) or row.get('bsop_date') != local.strftime('%Y%m%d'):
                raise ValueError
            stamp = datetime.strptime(row['bsop_date'] + row['bsop_time'], '%Y%m%d%H%M%S').replace(tzinfo=KST)
            # Match the existing NH conservative cutoff: discard current and previous minute.
            if stamp.second or stamp > local:
                raise ValueError
            if stamp >= local.replace(second=0, microsecond=0) - timedelta(minutes=1):
                continue
            if entered is None or stamp < entered:
                continue
            o, h, low, close, vol = (_number(row.get(k)) for k in
                                      ('stck_oprc', 'stck_hgpr', 'stck_lwpr', 'stck_prpr', 'vol'))
            if any(v is None for v in (o, h, low, close, vol)) or low <= 0 or vol < 0 or not low <= min(o, close) <= max(o, close) <= h:
                raise ValueError
            parsed.append((stamp, h, low, close, vol))
        parsed.sort(key=lambda row: row[0])
        parsed = parsed[-MIN_BARS:]
        if len(parsed) < MIN_BARS or any(b[0] - a[0] != timedelta(minutes=1) for a, b in zip(parsed, parsed[1:])):
            raise ValueError
        if not timedelta(0) < local - parsed[-1][0] <= timedelta(minutes=4):
            raise ValueError
        if not sum(row[4] for row in parsed) or not sum(row[4] for row in parsed[:3]):
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
        if not timedelta(0) <= local - quote_at <= timedelta(seconds=15) or bid is None or bid <= 0:
            raise ValueError
        result['inputs']['quote_at'] = quote_at.isoformat()
        result['inputs']['bid'] = bid
    except (KeyError, TypeError, ValueError, OverflowError):
        missing.append('quote_invalid_or_stale')
        bid = None

    advice_at = _stamp(advice.get('time')) if isinstance(advice, dict) else None
    signal = _number((advice.get('signals') or {}).get(symbol)) if isinstance(advice, dict) and isinstance(advice.get('signals'), dict) else None
    if (not isinstance(advice, dict) or advice.get('grounded') is not True or advice.get('confirmed_by_previous_run') is not True
            or advice_at is None or not timedelta(0) <= local - advice_at <= timedelta(minutes=5)
            or signal is None or not -1 <= signal <= 1):
        missing.append('model_unavailable_or_stale')
    else:
        result['inputs']['model_at'] = advice_at.isoformat()
        for key in ('market', 'news'):
            observed_context = _stamp(result['inputs'].get(f'{key}_observed_at'))
            if observed_context is not None and observed_context > advice_at:
                missing.append(f'{key}_after_model_start')
                result['context_status'][key] = 'unavailable'
    technical = (advice.get('technical_basic') or {}).get(symbol) if isinstance(advice, dict) and isinstance(advice.get('technical_basic'), dict) else None
    technical_at = None
    try:
        raw = technical.get('as_of') if isinstance(technical, dict) else None
        if isinstance(raw, str) and re.fullmatch(r'\d{2}:\d{2}', raw):
            technical_at = datetime.strptime(local.strftime('%Y%m%d') + raw, '%Y%m%d%H:%M').replace(tzinfo=KST)
        else:
            technical_at = _stamp(raw)
    except (TypeError, ValueError):
        pass
    if (not isinstance(technical, dict) or technical.get('status') in ('unavailable', 'insufficient_bars', 'error')
            or technical_at is None or advice_at is None or technical_at.date() != local.date()
            or not timedelta(0) <= advice_at - technical_at <= timedelta(minutes=4)
            or technical_at > local):
        missing.append('model_technical_unavailable_or_stale')
    else:
        result['inputs']['model_technical_at'] = technical_at.isoformat()
    if missing:
        return result

    volume = sum(row[4] for row in parsed)
    vwap = sum((row[1] + row[2] + row[3]) / 3 * row[4] for row in parsed) / volume
    peak = max(row[1] for row in parsed)
    checks = {'peak_gain': peak >= avg * (1 + PEAK_GAIN),
              'retracement': bid <= peak * (1 - RETRACEMENT),
              'below_window_vwap': parsed[-1][3] < vwap,
              'volume_rising': sum(row[4] for row in parsed[-3:]) > sum(row[4] for row in parsed[:3]),
              'model_not_positive': signal <= 0}
    result['inputs'].update({'bars_from': parsed[0][0].isoformat(), 'bars_through': parsed[-1][0].isoformat(),
                             'entry_at': entered.isoformat(), 'entry_price': avg, 'bid': bid, 'window_peak': peak,
                             'window_vwap': vwap, 'model_signal': signal})
    result.update(state='candidate' if all(checks.values()) else 'wait', candidate=all(checks.values()),
                  checks=checks, not_met=[key for key, value in checks.items() if not value])
    return result
