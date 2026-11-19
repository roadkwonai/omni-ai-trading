"""Research-only ETF market context; this module never generates orders."""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')


def _time(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('timezone required')
    return value.astimezone(KST)


def _bar(row):
    if not isinstance(row, dict):
        raise ValueError('invalid bar')
    timestamp = _time(row['time'])
    raw = [row[key] for key in ('open', 'high', 'low', 'close', 'volume')]
    if any(isinstance(item, bool) for item in raw):
        raise ValueError('invalid numeric value')
    opening, high, low, close, volume = (float(item) for item in raw)
    if (not all(math.isfinite(item) for item in (opening, high, low, close, volume))
            or low <= 0 or not low <= opening <= high or not low <= close <= high or volume <= 0):
        raise ValueError('invalid OHLCV')
    return timestamp, opening, high, low, close, volume


def classify_regime(now, minute_bars, daily_bars):
    """Classify a regime for research, using only fully completed input bars.

    Bars must be oldest first, each with timezone-aware `time` and OHLCV.
    Thresholds are experimental and are not calibrated for leveraged ETFs.
    """
    result = {'mode': 'wait', 'reason': 'invalid_or_incomplete_data',
              'data_quality': 'invalid', 'purpose': 'research_only',
              'calibrated_for_leverage': False,
              'thresholds_pct': {'intraday_range': 0.8, 'swing_max_range': 0.35,
                                 'swing_min_daily_trend': 0.5},
              'range_10m_pct': None, 'daily_sma_gap_pct': None}
    try:
        now = _time(now)
        if now.weekday() >= 5 or not '09:10' <= now.strftime('%H:%M') < '15:00':
            raise ValueError('outside research session')
        if not isinstance(minute_bars, list) or not isinstance(daily_bars, list):
            raise ValueError('bars must be lists')
        if len(minute_bars) < 10 or len(daily_bars) < 20:
            raise ValueError('insufficient bars')
        minutes = [_bar(row) for row in minute_bars]
        days = [_bar(row) for row in daily_bars]
        if any(stamp.date() != now.date() or stamp.second or stamp.microsecond
               or stamp + timedelta(minutes=1) > now for stamp, *_ in minutes):
            raise ValueError('minute bar not completed')
        if any(a[0] >= b[0] for a, b in zip(minutes, minutes[1:])):
            raise ValueError('minute order invalid')
        if any(stamp.date() >= now.date() for stamp, *_ in days):
            raise ValueError('daily bar not completed')
        if any(a[0].date() >= b[0].date() for a, b in zip(days, days[1:])):
            raise ValueError('daily order invalid')
        minutes, days = minutes[-10:], days[-20:]
        if (any(b[0] - a[0] != timedelta(minutes=1) for a, b in zip(minutes, minutes[1:]))
                or now - minutes[-1][0] > timedelta(minutes=4)
                or (now.date() - days[-1][0].date()).days > 4
                or any((b[0].date() - a[0].date()).days > 4 for a, b in zip(days, days[1:]))
                or len({item[4] for item in minutes}) < 2):
            raise ValueError('stale or incomplete bars')
        last_close = minutes[-1][4]
        price_range = (max(item[2] for item in minutes) - min(item[3] for item in minutes)) / last_close * 100
        closes = [item[4] for item in days]
        average_20 = sum(closes) / 20
        daily_gap = (sum(closes[-5:]) / 5 / average_20 - 1) * 100
        if not math.isfinite(price_range) or not math.isfinite(daily_gap):
            raise ValueError('non-finite calculation')
        result.update(data_quality='valid', range_10m_pct=round(price_range, 4),
                      daily_sma_gap_pct=round(daily_gap, 4))
        if price_range >= 0.8:
            result.update(mode='intraday', reason='experimental_high_range')
        elif price_range <= 0.35 and abs(daily_gap) >= 0.5:
            result.update(mode='swing_candidate', reason='experimental_low_range_with_daily_trend')
        else:
            result['reason'] = 'experimental_no_clear_regime'
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    return result
