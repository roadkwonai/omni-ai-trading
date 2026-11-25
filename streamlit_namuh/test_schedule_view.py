from datetime import date, datetime, timezone

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
    import dialogs
    import schedule_view
    dialogs.keep_open()
    schedule_view.render()


def test_the_tab_shows_every_kind_and_the_days_timeline():
    from streamlit.testing.v1 import AppTest
    page = AppTest.from_function(_page, default_timeout=60).run()
    assert not page.exception, page.exception
    full = page.dataframe[0].value
    assert {'launchd 서비스', 'Claude 예약 작업'} <= set(full['구분'])
    assert '장기투자 실행기' in set(full['작업'])
    assert {'일정', 'AI', '설정 모델', '최근 실행', '최근 실행 모델', '최근 결과'} <= set(full.columns)


def test_job_rows_show_configured_and_observed_models_separately():
    jobs = [{'name': '감사', 'kind': 'Claude 예약 작업', 'state': '등록됨', 'when': '평일 09:20',
             'provider': 'Claude', 'model': '지정 Sonnet', 'last_model': 'Opus', 'last_status': '완료',
             'last_run': datetime(2026, 11, 25, 0, 20, tzinfo=timezone.utc)}]
    row = V.job_rows(jobs)[0]
    assert row['설정 모델'] == '지정 Sonnet'
    assert row['최근 실행 모델'] == 'Opus'
    assert row['최근 실행'] == '2026-11-25 09:20:00'
    assert V.date_text(None) == '기록 없음'


def test_sort_toggles_list_order_and_keeps_the_selected_task(monkeypatch):
    import schedules as S
    import automation_status
    from streamlit.testing.v1 import AppTest

    base = {'kind': 'launchd 서비스', 'state': '종료', 'when': '로그인 시 시작', 'what': '', 'always': True}
    jobs = [
        {**base, 'key': 'none', 'name': '가 자료'},
        {**base, 'key': 'old', 'name': '다 점검', 'last_run': datetime(2026, 11, 25, 9, 20)},
        {**base, 'key': 'observed', 'name': '나 관측', 'last_observed': datetime(2026, 11, 25, 11, 0)},
        {**base, 'key': 'latest', 'name': '라 감사', 'last_run': datetime(2026, 11, 25, 1, 0, tzinfo=timezone.utc)},
    ]
    monkeypatch.setattr(S, 'all_jobs', lambda *args, **kwargs: jobs)
    monkeypatch.setattr(automation_status, 'processes', lambda: {})
    page = AppTest.from_function(_page).run()
    assert not page.exception
    assert list(page.dataframe[0].value['작업']) == ['라 감사', '다 점검', '나 관측', '가 자료']
    page.selectbox(key='sched_detail_pick').select('launchd 서비스:old').run()
    order = next(widget for widget in page.get('button_group') if widget.key == 'sched_sort')
    order.set_value('이름순').run()
    assert not page.exception
    assert list(page.dataframe[0].value['작업']) == ['가 자료', '나 관측', '다 점검', '라 감사']
    assert page.selectbox(key='sched_detail_pick').value == 'launchd 서비스:old'


def test_detail_opens_retained_history(monkeypatch):
    import sys
    from types import SimpleNamespace
    import schedules as S
    import automation_status
    from streamlit.testing.v1 import AppTest

    job = {'kind': 'Claude 예약 작업', 'key': 'audit', 'name': '장중 감사', 'state': '등록됨',
           'when': '평일 09:20', 'what': '감사', 'always': False, 'days': frozenset(range(5)),
           'monthday': None, 'date': None, 'times': [(9, 20)], 'model': 'Sonnet', 'provider': 'Claude',
           'last_run': datetime(2026, 11, 25, 9, 20), 'last_model': 'Opus', 'last_status': '완료'}
    history = [{'started': datetime(2026, 11, 25, 9, 20), 'finished': datetime(2026, 11, 25, 9, 22),
                'status': '완료', 'model': 'Opus', 'source': 'Claude 세션', 'provider': 'Claude'}]
    monkeypatch.setattr(S, 'all_jobs', lambda *args, **kwargs: [job])
    monkeypatch.setattr(S, 'job_history', lambda *args, **kwargs: history, raising=False)
    monkeypatch.setattr(automation_status, 'processes', lambda: {})
    saved = []
    monkeypatch.setitem(sys.modules, 'schedule_dispatch', SimpleNamespace(OPTIONS={'Claude': ('Sonnet',), 'Codex': ('GPT',)},
                                                                         eligibility=lambda _: '', set_route=lambda *args: saved.append(args)))
    page = AppTest.from_function(_page, default_timeout=60).run()
    assert not page.exception, page.exception
    page.button(key='sched_detail').click().run()
    assert not page.exception, page.exception
    assert any('실행 이력' in item.value for item in page.markdown)
    assert any('Opus' in str(frame.value) for frame in page.dataframe)
    page.selectbox(key='sched_provider_Claude 예약 작업:audit').select('Codex').run()
    assert not page.exception, page.exception
    page.button(key='sched_save_Claude 예약 작업:audit').click().run()
    assert not page.exception, page.exception
    assert saved == [(None, 'audit', 'Codex', 'GPT')]
