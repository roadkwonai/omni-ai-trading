"""The trading-day clock of the shared paper runner and everything that mirrors it.

User 2026-11-13: the new-buy cutoff and the same-day flatten are dashboard settings (strategy_settings
entry_cutoff_hhmm / flatten_hhmm, e.g. 1430 = 14:30). Selling everything takes about two minutes per order, so the
session runs at least 20 minutes past the flatten (never before 15:00 as before) but stops by 15:18: KRX continuous
trading ends 15:20 and orders after that go to the closing auction, whose fill is unknown until 15:30.
"""
from __future__ import annotations

from tunables import setting

# tunables.json (운영 상태 → 운영 조정값); the last order can only move earlier than 15:18
SESSION_OPEN = setting('session_open')
LAST_ORDER = setting('last_order')
FLATTEN_LEAD_MIN = setting('flatten_lead_min')
DEFAULT_CUTOFF, DEFAULT_FLATTEN = 1430, 1440
MARKET_OPEN = '09:00'      # KRX regular session opens; runners observe from here
FIRST_ORDER = '09:00'      # user 2026-11-19: orders from the open (was 09:10, 09:00~09:09 observation); prep is done pre-open


# KRX days whose whole clock moves (minutes late): 수능 day opens 10:00 and closes 16:30 (KRX notice 2026-11-05).
# shortcut: a hand-kept table; add each year's 수능 day (and any KRX special-hours notice) when it is announced.
SHIFTED_DAYS = {'2026-11-19': 60}


def shift_min(day):
    """Minutes the exchange clock runs late on `day` (a date), 0 on an ordinary day."""
    return SHIFTED_DAYS.get(day.isoformat(), 0)


def shifted(text, day):
    """'HH:MM' moved by that day's exchange shift."""
    return clock_text(minutes(text) + shift_min(day))


def order_start(day=None):
    """First minute any runner may order on `day` (default today): the exchange open (shifted on special days) or the
    later 장 시작 setting."""
    if day is None:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        day = datetime.now(ZoneInfo('Asia/Seoul')).date()
    return max(shifted(FIRST_ORDER, day), SESSION_OPEN)


def hhmm(value):
    """1430 -> '14:30'."""
    value = int(round(value))
    return f'{value // 100:02d}:{value % 100:02d}'


def minutes(text):
    hours, mins = text.split(':')
    return int(hours) * 60 + int(mins)


def clock_text(total):
    return f'{total // 60:02d}:{total % 60:02d}'


def times(settings=None):
    """{'cutoff', 'flatten', 'close'} as HH:MM from strategy settings (defaults when missing)."""
    settings = settings or {}
    cutoff = hhmm(settings.get('entry_cutoff_hhmm', DEFAULT_CUTOFF))
    flatten = hhmm(settings.get('flatten_hhmm', DEFAULT_FLATTEN))
    close = min(max(minutes('15:00'), minutes(flatten) + FLATTEN_LEAD_MIN), minutes(LAST_ORDER))
    return {'cutoff': cutoff, 'flatten': flatten, 'close': clock_text(close)}


def load(root):
    """times() from the saved settings under `root` (the shared paper folder)."""
    from strategy_settings import read_settings
    return times(read_settings(root)[0])
