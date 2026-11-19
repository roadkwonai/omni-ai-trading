from datetime import datetime, time
from zoneinfo import ZoneInfo
import pytest
from src.tools.nhplug_calendar import check_session

KST = ZoneInfo('Asia/Seoul')
NOW = datetime(2026, 11, 6, 10, 30, tzinfo=KST)

def quote(**changes):
    return {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': {
        'iem_cd': '005930', 'market_status': '1', 'hoga_bsop_hour': '10:29:30', **changes}}}

def test_fresh_open_market():
    assert check_session(NOW, quote(), quote(hoga_bsop_hour='10:29:29'))['allowed']

def test_frozen_or_previous_day_same_time_is_denied():
    assert not check_session(NOW, quote(), quote())['allowed']
    assert not check_session(NOW, quote(), quote(hoga_bsop_hour='10:29:31'))['allowed']
    assert not check_session(NOW, quote(), None)['allowed']

def test_different_symbols_are_denied():
    assert not check_session(NOW, quote(), quote(iem_cd='000660', hoga_bsop_hour='10:29:29'))['allowed']

@pytest.mark.parametrize('status', ['X', 'D', 'A', 'C', '', None])
def test_unknown_or_closed_market_is_denied(status):
    assert not check_session(NOW, quote(market_status=status))['allowed']

@pytest.mark.parametrize('stamp', ['10:27:59', '10:30:06', '', '10:61:00', None])
def test_stale_future_or_invalid_is_denied(stamp):
    assert not check_session(NOW, quote(hoga_bsop_hour=stamp))['allowed']

def test_weekends_and_outside_window_and_naive_are_denied():
    for now in (datetime(2026,11,7,10,30,tzinfo=KST), datetime(2026,11,6,9,0,tzinfo=KST), datetime(2026,11,6,15,0,tzinfo=KST), NOW.replace(tzinfo=None)):
        assert not check_session(now, quote())['allowed']

def test_missing_or_failed_quotes_denied():
    for item in ({}, {'status':'error'}, {'status':'ok','data':{'rsp_cd':'00000','Output_0':{}}}, quote(iem_cd='bad')):
        assert not check_session(NOW,item)['allowed']

def test_sell_can_use_same_second_but_not_reversed_or_old_quotes():
    assert check_session(NOW, quote(), quote(), require_advance=False)['allowed']
    assert not check_session(NOW, quote(), quote(hoga_bsop_hour='10:29:31'), require_advance=False)['allowed']
    assert not check_session(NOW, quote(), quote(hoga_bsop_hour='10:27:59'), require_advance=False)['allowed']
    assert not check_session(NOW, quote(), quote())['allowed']  # buy still requires advancement

def test_sell_close_window_and_explicit_trading_dates():
    for stamp in ('15:00:10', '15:05:10', '15:17:59'):
        at = datetime.fromisoformat(f'2026-11-06T{stamp}+09:00')
        q = quote(hoga_bsop_hour=stamp, bsop_date='20261106')
        assert check_session(at, q, q, require_advance=False, session_end=time(15, 18))['allowed']
    end = datetime.fromisoformat('2026-11-06T15:18:00+09:00')
    q = quote(hoga_bsop_hour='15:18:00')
    assert not check_session(end, q, q, require_advance=False, session_end=time(15, 18))['allowed']
    assert not check_session(NOW, quote(bsop_date='20261105'), quote(), require_advance=False)['allowed']
    assert not check_session(NOW, quote(), quote(bsop_date='20261105'), require_advance=False)['allowed']

def test_holiday_market_status_is_not_treated_as_open():
    holiday = datetime(2026, 12, 25, 10, 30, tzinfo=KST)
    assert not check_session(holiday, quote(market_status='0'), quote(market_status='0'), require_advance=False)['allowed']


def test_krx_codes_with_letters_are_valid_symbols():
    # 2026-11-18: long-term ETFs 0023A0 (SOL 양자) and 0183J0 (TIGER 우주) were refused as 'quote symbol invalid'
    for code in ('0023A0', '0183J0'):
        assert check_session(NOW, quote(iem_cd=code), quote(iem_cd=code, hoga_bsop_hour='10:29:29'))['allowed'], code
    for bad in ('A0023A', '0023a0', '12345', '1234567'):
        assert not check_session(NOW, quote(iem_cd=bad), quote(iem_cd=bad, hoga_bsop_hour='10:29:29'))['allowed'], bad


def test_session_opens_at_0900():
    """User 2026-11-19: orders from the 09:00 open (the 09:00~09:09 observation rule is gone)."""
    for stamp, allowed in (('08:59:59', False), ('09:00:05', True)):
        at = datetime.fromisoformat(f'2026-11-06T{stamp}+09:00')
        q = quote(hoga_bsop_hour=stamp, bsop_date='20261106')
        assert check_session(at, q, q, require_advance=False)['allowed'] is allowed, stamp


def test_a_shifted_exchange_day_moves_the_whole_permitted_window(monkeypatch):
    """수능 day (user 2026-11-19): KRX opens 10:00 and closes 16:30, so the 15:00 buy / 15:18 sell ends move an hour too;
    the caps themselves (session_end <= 15:18 on the ordinary clock) are unchanged."""
    import src.tools.nhplug_calendar as cal
    monkeypatch.setattr(cal, 'SHIFTED_DAYS', {'2026-11-19': 60})
    day = lambda h, m, s=30: datetime(2026, 11, 19, h, m, s, tzinfo=KST)  # noqa: E731
    at = lambda h, m: quote(hoga_bsop_hour=f'{h:02}:{m:02}:00')  # noqa: E731
    assert not check_session(day(9, 30), at(9, 30))['allowed']  # before the 10:00 open
    assert check_session(day(15, 30), at(15, 30), at(15, 29))['allowed']  # 15:00 buy end is 16:00 today
    assert not check_session(day(16, 0), at(16, 0), at(15, 59))['allowed']
    assert check_session(day(16, 10), at(16, 10), at(16, 10), require_advance=False, session_end=time(15, 18))['allowed']
    assert not check_session(day(16, 18), at(16, 18), at(16, 18), require_advance=False, session_end=time(15, 18))['allowed']
    assert not check_session(datetime(2026, 11, 20, 15, 30, tzinfo=KST), at(15, 30), at(15, 29))['allowed']  # an ordinary day
