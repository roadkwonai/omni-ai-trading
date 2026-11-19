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


def shift_min(day):
    """Minutes the exchange clock runs late on `day` (a date), 0 on an ordinary day: the one table is
    src.tools.nhplug_calendar.SHIFTED_DAYS (수능 day opens 10:00 and closes 16:30), which the order guards read too."""
    from src.tools.nhplug_calendar import shift_min as exchange_shift
    return exchange_shift(day)


def today():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo('Asia/Seoul')).date()


def shifted(text, day):
    """'HH:MM' moved by that day's exchange shift."""
    return clock_text(minutes(text) + shift_min(day))


def order_start(day=None):
    """First minute any runner may order on `day` (default today): the exchange open (shifted on special days) or the
    later 장 시작 setting."""
    return max(shifted(FIRST_ORDER, day or today()), SESSION_OPEN)


def hhmm(value):
    """1430 -> '14:30'."""
    value = int(round(value))
    return f'{value // 100:02d}:{value % 100:02d}'


def minutes(text):
    hours, mins = text.split(':')
    return int(hours) * 60 + int(mins)


def clock_text(total):
    return f'{total // 60:02d}:{total % 60:02d}'


def times(settings=None, day=None):
    """{'cutoff', 'flatten', 'close'} as HH:MM from strategy settings (defaults when missing) on `day`'s exchange clock
    (default today): the settings and the 15:18 cap are ordinary-day times, and a shifted day (수능) moves all three
    (user 2026-11-19: 11-19 the runner stopped buying at 14:50 and quit at 15:18 while KRX ran to 16:30)."""
    settings = settings or {}
    cutoff = hhmm(settings.get('entry_cutoff_hhmm', DEFAULT_CUTOFF))
    flatten = hhmm(settings.get('flatten_hhmm', DEFAULT_FLATTEN))
    close = min(max(minutes('15:00'), minutes(flatten) + FLATTEN_LEAD_MIN), minutes(LAST_ORDER))
    late = shift_min(day or today())
    return {'cutoff': clock_text(minutes(cutoff) + late), 'flatten': clock_text(minutes(flatten) + late),
            'close': clock_text(close + late)}


def load(root, day=None):
    """times() from the saved settings under `root` (the shared paper folder)."""
    from strategy_settings import read_settings
    return times(read_settings(root)[0], day)
