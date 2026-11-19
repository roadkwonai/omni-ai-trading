from streamlit.testing.v1 import AppTest


def _app():
    import api_status_view
    from pathlib import Path
    api_status_view.render(Path('/nonexistent-runtime'))


def test_api_tab_renders_status_usage_and_failures(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    import api_calls
    monkeypatch.setattr(api_calls, 'DB', tmp_path / 'api.sqlite3')
    monkeypatch.setattr(api_calls, '_conn', {})
    monkeypatch.setattr(api_calls, 'ENABLED', True)
    now = datetime.now(api_calls.KST)
    api_calls.record('kiwoom', 'api.kiwoom.com/api/dostk/chart#ka10080', True, 120, status=200, size=2048, now=now, who='market_movers')
    api_calls.record('google_news', 'news.google.com/rss/search', False, 900, status=429, error='HTTP 429', now=now, who='nh_paper_isolated')
    api_calls.record('nh', 'mcp:get_stock_price', True, 80, now=now - timedelta(days=2), who='dashboard')
    monkeypatch.setattr(api_calls, 'ENABLED', False)
    at = AppTest.from_function(_app, default_timeout=60).run()
    assert not at.exception
    heads = [m.value for m in at.markdown if m.value.startswith('#####')]
    assert heads[:2] == ['##### API별 연동 상태', '##### 오늘 호출 흐름'] and '##### 화면별 데이터 출처' in heads
    status = at.dataframe[0].value
    assert set(status.loc[status['API'] == 'Google 뉴스 RSS', '상태']) == {'실패'}
    assert int(status.loc[status['API'] == '키움 REST', '호출'].iloc[0]) == 1
    assert [t.label for t in at.tabs] == ['데이터·용도', '엔드포인트별 호출', '호출한 프로그램', '실패 이력', '산출물']
    assert any('HTTP 오류' in str(df.value.to_dict()) for df in at.dataframe)


def test_ago_handles_missing_stamps():
    from datetime import datetime
    import api_status_view as v
    now = datetime.now(v.KST)
    assert v.ago(float('nan'), now) == '—' and v.ago(None, now) == '—'
    assert v.ago(now.isoformat(timespec='seconds'), now).endswith('(0분 전)')


def test_recent_window_is_the_last_30_minutes_across_midnight():
    from datetime import datetime
    import api_status_view as v
    row = lambda day, slot, fail: {'day': day, 'slot': slot, 'provider': 'kiwoom', 'n': 2, 'fail': fail, 'bad': 0, 'ms_sum': 2.0,  # noqa: E731
                                   'ms_max': 1.0, 'bytes': 0, 'last_ok': None, 'last_fail': None}
    rows = [row('2026-11-17', '23:50', 2), row('2026-11-18', '00:00', 0), row('2026-11-17', '23:20', 2)]
    got = v.recent_totals(rows, datetime(2026, 11, 18, 0, 5, tzinfo=v.KST))['kiwoom']
    assert (got['n'], got['fail']) == (4, 2)  # 23:50 and 00:00 slots; 23:20 is older than 30 minutes
    late = v.recent_totals([row('2026-11-17', '10:00', 2), row('2026-11-17', '10:10', 0)], datetime(2026, 11, 17, 10, 39, tzinfo=v.KST))
    assert late['kiwoom']['fail'] == 0  # the 10:00 slot started 39 minutes ago


def _row(provider, when, n=1, fail=0):
    return {'day': when.strftime('%Y-%m-%d'), 'slot': f'{when:%H}:{when.minute // 10 * 10:02d}', 'provider': provider, 'endpoint': 'e',
            'caller': 'c', 'n': n, 'fail': fail, 'bad': 0, 'ms_sum': 1.0, 'ms_max': 1.0, 'bytes': 0,
            'last_ok': when.isoformat(timespec='seconds') if n > fail else None, 'last_fail': when.isoformat(timespec='seconds') if fail else None}


def test_an_api_that_stopped_answering_in_session_is_delayed():
    from datetime import datetime, timedelta
    import api_status_view as v
    keys = {k: True for p in v.cat.CATALOG for k in p['keys']}
    now = datetime(2026, 11, 18, 10, 30, tzinfo=v.KST)  # a Wednesday in session
    calls = [_row('nh', now - timedelta(minutes=20)), _row('naver', now - timedelta(minutes=4))]
    states = v.provider_states(calls, keys, now)
    assert states['nh'][0] == '지연' and '10:10' in states['nh'][1]  # 1-minute API silent for 20 minutes
    assert states['naver'][0] == '정상'
    assert states['jev'][0] == '지연' and '오늘 성공 없음' in states['jev'][1]  # services are up, Jev never answered today
    assert states['openai'][0] == '호출 없음'  # no expected cadence: only used in the OpenAI mode
    after = v.provider_states(calls, keys, datetime(2026, 11, 18, 16, 0, tzinfo=v.KST))
    assert after['nh'][0] == '정상'  # after the session silence is normal
    assert v.provider_states([], keys, now)['nh'][0] == '호출 없음'  # nothing called today (holiday, services down): no alarm


def test_alert_lines_name_failing_and_delayed_apis_only():
    import api_status_view as v
    states = {'nh': ('지연', '마지막 성공 10:10'), 'google_news': ('실패', '최근 30분 4건 중 4건 실패'), 'naver': ('정상', '오늘 3건'),
              'krx': ('키 없음', 'KRX_API_KEY 미설정')}
    assert v.alert_lines(states) == ['API NH 나무 PLUG 지연: 마지막 성공 10:10', 'API Google 뉴스 RSS 실패: 최근 30분 4건 중 4건 실패']


def test_endpoint_totals_handle_slots_without_a_success():
    from datetime import datetime, timedelta
    import pandas as pd
    import api_status_view as v
    now = datetime(2026, 11, 18, 10, 30, tzinfo=v.KST)
    frame = pd.DataFrame([_row('kiwoom', now - timedelta(minutes=20)), _row('kiwoom', now, n=1, fail=1)])
    got = v.totals(frame, by='endpoint')['e']
    assert (got['n'], got['fail'], got['last_ok'][11:16], got['last_fail'][11:16]) == (2, 1, '10:10', '10:30')


def test_token_expiry_reads_only_the_expiry_and_flags_expired(tmp_path):
    import json
    from datetime import datetime, timedelta
    import api_status_view as v
    now = datetime(2026, 11, 18, 10, 0, tzinfo=v.KST)
    (tmp_path / 'kiwoom').mkdir()
    (tmp_path / 'kiwoom' / 'token.json').write_text(json.dumps({'token': 'SECRET', 'expires_at': (now + timedelta(hours=20)).isoformat()}))
    (tmp_path / 'nhplug' / 'tokens').mkdir(parents=True)
    (tmp_path / 'nhplug' / 'tokens' / 'a.json').write_text(json.dumps({'token': 'SECRET', 'expiresAt': int((now - timedelta(minutes=1)).timestamp() * 1000)}))
    assert v.token_status(v.cat.BY_KEY['kiwoom'], tmp_path, now) == '토큰 11-19 06:00까지'
    assert v.token_status(v.cat.BY_KEY['nh'], tmp_path, now) == '토큰 만료 11-18 09:59 (다음 호출 때 재발급)'
    assert v.token_status(v.cat.BY_KEY['toss'], tmp_path, now) == '토큰 없음 (첫 호출 때 발급)'
    assert v.token_status(v.cat.BY_KEY['naver'], tmp_path, now) is None


def test_skills_section_lists_jobs_and_skill_results(tmp_path):
    import os
    os.environ['SKILLS_VIEW_RUNTIME'] = str(tmp_path)
    reviews = tmp_path / 'nhplug/shared-etf-paper/reviews'
    reviews.mkdir(parents=True)
    (reviews / 'journal-2026-11-17.md').write_text('x')

    def app():
        import os
        from pathlib import Path
        import skills_view
        skills_view.render(Path(os.environ['SKILLS_VIEW_RUNTIME']))
    at = AppTest.from_function(app, default_timeout=60).run()
    assert not at.exception
    jobs, skills = at.dataframe[0].value, at.dataframe[1].value
    assert '매매일지 작성' in set(jobs['작업']) and list(jobs.columns) == ['시각', '작업', '작업 ID', '상태', '쓰는 스킬', '결과', '일정(cron·한 번)']
    assert len(jobs) == len(__import__('skill_catalog').jobs())  # every backed-up task, not a hand-kept subset
    assert skills.loc[skills['스킬'].str.contains('trading-journal-writer'), '최근 결과'].iloc[0].startswith('journal-2026-11-17.md')
    detail = at.dataframe[2].value  # the first skill (trading-journal-writer) is selected
    assert detail.loc[detail['결과'] == '하루 매매관점 리포트', '최근 파일'].iloc[0] == 'journal-2026-11-17.md'


def test_token_status_follows_the_token_in_use_not_an_old_file(tmp_path):
    import json
    from datetime import datetime, timedelta
    import api_status_view as v
    now = datetime(2026, 11, 18, 10, 0, tzinfo=v.KST)
    folder = tmp_path / 'nhplug' / 'tokens'
    folder.mkdir(parents=True)
    (folder / 'old.json').write_text(json.dumps({'expiresAt': int((now - timedelta(days=10)).timestamp() * 1000)}))
    (folder / 'used.json').write_text(json.dumps({'expiresAt': int((now + timedelta(hours=3)).timestamp() * 1000)}))
    assert v.token_status(v.cat.BY_KEY['nh'], tmp_path, now) == '토큰 11-18 13:00까지'


def _macro_app():
    import os
    from pathlib import Path
    import streamlit as st
    import api_status_view
    st.session_state.setdefault('api_pick', 'FRED (세인트루이스 연준)')
    api_status_view.render(Path(os.environ['API_VIEW_RUNTIME']))


def test_fred_detail_shows_the_values_the_engine_received(tmp_path, monkeypatch):
    import api_calls
    import vibe_longterm as L
    monkeypatch.setattr(api_calls, 'DB', tmp_path / 'api.sqlite3')
    monkeypatch.setattr(api_calls, '_conn', {})
    monkeypatch.setenv('API_VIEW_RUNTIME', str(tmp_path))
    L.put_state(L.connect(tmp_path / 'nhplug/vibe-longterm'), plan={'time': '2026-11-18T08:45:00+09:00', 'macro': {
        'as_of': '2026-11-18', 'errors': ['DFII10: TimeoutError'], 'events': [{'date': '2026-12-09', 'label': 'FOMC'}],
        'series': {'BAMLH0A0HYM2': {'label': '하이일드 스프레드', 'unit': '%p', 'date': '2026-11-16', 'value': 3.23, 'chg_1m': 0.0,
                                    'low_1y': 2.6, 'high_1y': 3.48, 'stale': False}}}})
    at = AppTest.from_function(_macro_app, default_timeout=60).run()
    assert not at.exception
    assert [t.label for t in at.tabs][-1] == '최근 데이터'
    tables = [df.value for df in at.dataframe]
    assert any('하이일드 스프레드' in set(t.get('지표', [])) for t in tables)
    assert any('FOMC' in set(t.get('발표', [])) for t in tables)
    assert any('빠진 자료: DFII10: TimeoutError' in c.value for c in at.caption)


def test_fred_check_now_button_shows_a_fresh_result(tmp_path, monkeypatch):
    import api_calls
    import longterm_macro
    monkeypatch.setattr(api_calls, 'DB', tmp_path / 'api.sqlite3')
    monkeypatch.setattr(api_calls, '_conn', {})
    monkeypatch.setenv('API_VIEW_RUNTIME', str(tmp_path))
    monkeypatch.setattr(longterm_macro, 'collect', lambda day: {'as_of': str(day), 'errors': [], 'events': [{'date': '2026-12-09', 'label': 'FOMC'}],
                                                                  'series': {'DGS10': {'label': '미 10년 국채금리', 'unit': '%', 'date': '2026-11-16',
                                                                                       'value': 5.15, 'chg_1m': -0.04, 'low_1y': 3.97, 'high_1y': 5.31, 'stale': False}}})
    at = AppTest.from_function(_macro_app, default_timeout=60).run()
    at.button(key='api_macro_check').click().run()
    assert not at.exception
    assert any('이 화면에서 지금 확인' in c.value for c in at.caption)
    assert any('미 10년 국채금리' in set(df.value.get('지표', [])) for df in at.dataframe)


def test_fred_check_now_gives_way_to_a_newer_engine_decision(tmp_path, monkeypatch):
    import api_calls
    import longterm_macro
    import vibe_longterm as L
    monkeypatch.setattr(api_calls, 'DB', tmp_path / 'api.sqlite3')
    monkeypatch.setattr(api_calls, '_conn', {})
    monkeypatch.setenv('API_VIEW_RUNTIME', str(tmp_path))
    monkeypatch.setattr(longterm_macro, 'collect', lambda day: {'as_of': str(day), 'errors': [], 'events': [], 'series': {}})
    root = tmp_path / 'nhplug/vibe-longterm'
    L.put_state(L.connect(root), plan={'time': '2026-01-02T08:45:00+09:00', 'macro': {'as_of': '2026-01-02', 'errors': [], 'events': [], 'series': {}}})
    at = AppTest.from_function(_macro_app, default_timeout=60).run()
    at.button(key='api_macro_check').click().run()
    assert any('이 화면에서 지금 확인' in c.value for c in at.caption)  # newer than the engine's morning data
    L.put_state(L.connect(root), plan={'time': '2099-01-02T08:45:00+09:00', 'macro': {'as_of': '2099-01-02', 'errors': [], 'events': [], 'series': {}}})
    at.run()
    captions = ' '.join(c.value for c in at.caption)
    assert '이 화면에서 지금 확인' not in captions and '장기 엔진 아침 판단 01-02 08:45' in captions


def test_a_weekday_krx_holiday_is_not_late(monkeypatch):
    # review4 D-4: the 08:40 long-term engine and discord-notify still call out on a KRX holiday; the cadence check took
    # any call today for a trading day and flagged every 1-minute API as 지연
    from datetime import date, datetime
    import nh_paper_isolated
    import api_status_view as v
    monkeypatch.setattr(nh_paper_isolated, '_HOLIDAYS', {2026: (frozenset({date(2026, 12, 31)}), 0.0)})
    keys = {k: True for p in v.cat.CATALOG for k in p['keys']}
    morning = lambda day: [{'day': day, 'slot': '08:40', 'provider': 'fred', 'endpoint': 'x', 'caller': 'vibe_longterm', 'n': 5,  # noqa: E731
                            'fail': 0, 'bad': 0, 'ms_sum': 500.0, 'ms_max': 200.0, 'bytes': 100,
                            'last_ok': f'{day}T08:40:10+09:00', 'last_fail': None}]
    holiday = v.provider_states(morning('2026-12-31'), keys, datetime(2026, 12, 31, 10, 30, tzinfo=v.KST))  # 연말 휴장일, a Thursday
    assert not [line for line in v.alert_lines(holiday) if '지연' in line]
    assert v.provider_states(morning('2026-12-30'), keys, datetime(2026, 12, 30, 10, 30, tzinfo=v.KST))['nh'][0] == '지연'  # it trades
