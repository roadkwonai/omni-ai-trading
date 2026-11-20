import unittest

import session_times as t
from strategy_settings import DEFAULTS, problems


class SessionTimesTests(unittest.TestCase):
    def test_defaults_keep_the_old_day(self):
        self.assertEqual(t.times(DEFAULTS), {'cutoff': '14:30', 'flatten': '14:40', 'close': '15:00'})
        self.assertEqual(t.times(None), {'cutoff': '14:30', 'flatten': '14:40', 'close': '15:00'})

    def test_later_flatten_runs_longer_but_never_past_1518(self):
        self.assertEqual(t.times({'entry_cutoff_hhmm': 1450, 'flatten_hhmm': 1455})['close'], '15:15')
        self.assertEqual(t.times({'entry_cutoff_hhmm': 1500, 'flatten_hhmm': 1505})['close'], '15:18')

    def test_settings_reject_bad_or_inverted_times(self):
        self.assertEqual(problems({**DEFAULTS, 'entry_cutoff_hhmm': 1500, 'flatten_hhmm': 1505}), [])
        self.assertTrue(problems({**DEFAULTS, 'entry_cutoff_hhmm': 1475}))         # minute 75
        self.assertTrue(problems({**DEFAULTS, 'entry_cutoff_hhmm': 1450, 'flatten_hhmm': 1440}))
        self.assertTrue(problems({**DEFAULTS, 'flatten_hhmm': 1510}))               # past 15:05
        self.assertTrue(problems({**DEFAULTS, 'ipo_max_positions': 1.5}))



class PrecloseTests(unittest.TestCase):
    def test_preclose_is_the_last_ten_minutes_of_the_session(self):
        import nh_paper_isolated as p
        saved = p.SESSION_CLOSE
        try:
            p.SESSION_CLOSE = '15:00'
            self.assertEqual(p.preclose_from(), '14:50')  # the old fixed value
            p.SESSION_CLOSE = '15:18'
            self.assertEqual(p.preclose_from(), '15:08')
        finally:
            p.SESSION_CLOSE = saved


def test_order_start_is_the_open_or_the_later_session_open_setting():
    """User 2026-11-19: orders start at the 09:00 open (the 09:00~09:09 observation rule is gone)."""
    import session_times
    from datetime import date
    from unittest.mock import patch
    for opening, expected in (('09:00', '09:00'), ('09:10', '09:10'), ('09:30', '09:30')):
        with patch.object(session_times, 'SESSION_OPEN', opening):
            assert session_times.order_start(date(2026, 11, 20)) == expected


def test_order_start_follows_a_shifted_exchange_open():
    """수능 day opens 10:00: no order before it, without editing the 장 시작 setting."""
    import session_times
    from datetime import date
    from unittest.mock import patch
    with patch.object(session_times, 'SESSION_OPEN', '09:00'), \
            patch.object(__import__('src.tools.nhplug_calendar', fromlist=['x']), 'SHIFTED_DAYS', {'2026-11-19': 60}):
        assert session_times.order_start(date(2026, 11, 19)) == '10:00'
        assert session_times.order_start(date(2026, 11, 20)) == '09:00'

if __name__ == '__main__':
    unittest.main()


def test_special_session_days_shift_the_exchange_clock():
    """KRX opens and closes an hour late on 수능 day (2026-11-19: 10:00~16:30, KRX notice 2026-11-05)."""
    from datetime import date
    import session_times as s
    import src.tools.nhplug_calendar as calendar; calendar.SHIFTED_DAYS = {"2026-11-19": 60}  # restored by conftest
    assert s.shift_min(date(2026, 11, 19)) == 60 and s.shift_min(date(2026, 11, 20)) == 0
    assert s.shifted('09:40', date(2026, 11, 19)) == '10:40' and s.shifted('15:40', date(2026, 11, 18)) == '15:40'


def test_long_term_engine_windows_follow_the_exchange_clock():
    """11/19 09:18: the engine (가상 체결 on) would have traded at 09:40 on pre-open quotes and closed at 15:40."""
    from datetime import date
    import vibe_longterm as L
    import src.tools.nhplug_calendar as calendar; calendar.SHIFTED_DAYS = {"2026-11-19": 60}  # restored by conftest
    normal = dict((names, (a, b)) for names, a, b in L.windows(date(2026, 11, 18)))
    late = dict((names, (a, b)) for names, a, b in L.windows(date(2026, 11, 19)))
    assert normal[('trade', 'dca')] == ('09:40', '14:30') and late[('trade', 'dca')] == ('10:40', '15:30')
    assert late[('close',)] == ('16:40', '17:30') and late[('decide',)] == normal[('decide',)]  # the plan is made before the open


class UntilOpenTest(unittest.TestCase):
    def test_pre_open_waits_to_the_bell_else_zero(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from session_times import until_open
        kst = ZoneInfo('Asia/Seoul')
        self.assertEqual(until_open(datetime(2026, 11, 20, 8, 55, 30, tzinfo=kst)), 270.0)  # Friday 08:55:30 -> 09:00
        self.assertEqual(until_open(datetime(2026, 11, 20, 9, 0, 1, tzinfo=kst)), 0.0)
        self.assertEqual(until_open(datetime(2026, 11, 20, 7, 30, tzinfo=kst)), 0.0)  # too early: the old 'outside session'
        self.assertEqual(until_open(datetime(2026, 11, 21, 8, 55, tzinfo=kst)), 0.0)  # Saturday
