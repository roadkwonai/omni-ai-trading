"""수능 day (user 2026-11-19: '수능일 마감 시각 보정도 고쳐줘'): KRX opens 10:00 and closes 16:30, so every session time of
the runners moves by the day's shift, not only the open (11-19 the runner stopped buying at 14:50 and quit at 15:18
while the market ran to 16:30)."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

KST = ZoneInfo('Asia/Seoul')
DAY = date(2026, 11, 19)


@pytest.fixture
def shifted(monkeypatch):
    import src.tools.nhplug_calendar as cal
    monkeypatch.setattr(cal, 'SHIFTED_DAYS', {'2026-11-19': 60})


def test_the_session_clock_moves_on_a_shifted_day_only(shifted):
    import session_times as t
    settings = {'entry_cutoff_hhmm': 1450, 'flatten_hhmm': 1500}
    assert t.times(settings, DAY) == {'cutoff': '15:50', 'flatten': '16:00', 'close': '16:18'}
    assert t.times(settings, date(2026, 11, 20)) == {'cutoff': '14:50', 'flatten': '15:00', 'close': '15:18'}
    assert t.shifted('14:30', DAY) == '15:30' and t.order_start(DAY) == '10:00'


def test_the_runner_windows_follow_the_shift(shifted, tmp_path):
    import nh_paper_isolated as p
    saved = dict(p.OVERSOLD), p.IPO_START
    try:
        p.OVERSOLD.update(oversold_v2_on=1, oversold_v2_from_hhmm=1430, oversold_v2_until_hhmm=1455)
        assert not p.v2_window(datetime(2026, 11, 19, 14, 40, tzinfo=KST))
        assert p.v2_window(datetime(2026, 11, 19, 15, 40, tzinfo=KST))
        assert p.v2_window(datetime(2026, 11, 20, 14, 40, tzinfo=KST))
        p.IPO_START = '09:20'
        assert p.ipo_open(datetime(2026, 11, 19, 10, 10, tzinfo=KST)) is False
        assert p.ipo_open(datetime(2026, 11, 19, 10, 20, tzinfo=KST)) is True
        assert p.ipo_open(datetime(2026, 11, 20, 9, 20, tzinfo=KST)) is True
    finally:
        p.OVERSOLD.clear(); p.OVERSOLD.update(saved[0]); p.IPO_START = saved[1]


def test_the_v2_rebuy_watch_and_the_live_mirror_follow_the_shift(shifted):
    import nh_live_follow as f
    import v2_reentry as r
    entry = {'opened': '2026-11-18T14:40:00+09:00', 'hold_days': 2}
    settings = {'oversold_v2_until_hhmm': 1455}
    held = lambda opened, today: (today - opened).days  # noqa: E731
    assert r.open_for(entry, datetime(2026, 11, 19, 14, 50, tzinfo=KST), settings, held)  # last session: before 15:00 today
    assert not r.open_for(entry, datetime(2026, 11, 19, 15, 0, tzinfo=KST), settings, held)
    assert f.session_over(datetime(2026, 11, 19, 16, 0, tzinfo=KST)) is False
    assert f.session_over(datetime(2026, 11, 19, 16, 20, tzinfo=KST)) is True
    assert f.session_over(datetime(2026, 11, 20, 15, 20, tzinfo=KST)) is True


def test_the_ranking_service_and_the_learning_watch_end_later(shifted, tmp_path):
    import isolated_paper_learning_watch as w
    import market_movers as m
    assert m.running(datetime(2026, 11, 19, 16, 0, tzinfo=KST)) and not m.running(datetime(2026, 11, 19, 16, 5, tzinfo=KST))
    assert not m.running(datetime(2026, 11, 20, 15, 5, tzinfo=KST))
    assert w.review_time(tmp_path, DAY) == '16:10' and w.review_time(tmp_path, date(2026, 11, 20)) == '15:10'


def test_a_past_day_replay_uses_that_days_clock(shifted, tmp_path):
    """The dashboard replays past days (nh_live_shadow.ledger_shadow): a 수능 day keeps its 16:00 flatten when viewed later."""
    import nh_live_follow as f
    (tmp_path / 'shared-etf-paper').mkdir()
    assert f.session_clock(tmp_path, DAY)['flatten'] == '15:40' and f.session_clock(tmp_path, date(2026, 11, 20))['flatten'] == '14:40'
