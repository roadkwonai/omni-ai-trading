from datetime import date

import schedule_view as V


def test_times_read_as_spans_and_a_day_is_one_row_per_job():
    assert V.times_text([(7, 40), *[(8, m) for m in range(0, 60, 5)], (9, 0), (9, 10)]) == '07:40 · 08:00~09:00 5분마다 · 09:10'
    assert V.times_text([(h, 20) for h in range(9, 16)]) == '09:20~15:20 매시'
    assert V.times_text([(8, 20), (8, 53)]) == '08:20 · 08:53'
    job = {'kind': 'launchd 서비스', 'key': 'p', 'name': '장전 자료 수집', 'state': '종료', 'what': 'scripts/premarket.py', 'always': False,
           'days': frozenset(range(5)), 'monthday': None, 'date': None, 'times': [(7, 40), (8, 0), (8, 5)]}
    rows = V.day_rows([job], date(2026, 11, 20))
    assert rows == [{'첫 시작': '07:40', '구분': 'launchd 서비스', '작업': '장전 자료 수집', '이날 시각': '07:40 · 08:00 · 08:05', '횟수': 3,
                     '상태': '종료', '하는 일': 'scripts/premarket.py'}]


def _page():
    import schedule_view
    schedule_view.render()


def test_the_tab_shows_every_kind_and_the_days_timeline():
    from streamlit.testing.v1 import AppTest
    page = AppTest.from_function(_page, default_timeout=60).run()
    assert not page.exception, page.exception
    full = page.dataframe[-1].value
    assert {'launchd 서비스', 'Claude 예약 작업'} <= set(full['구분'])
    assert '장기투자 실행기' in set(full['작업'])
