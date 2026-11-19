from datetime import date, timedelta

import pytest

import longterm_macro as M

TODAY = date(2026, 11, 18)
FOMC_HTML = ('<h4><a id="1">2026 FOMC Meetings</a></h4>'
             '<div class="fomc-meeting__month col-xs-5"><strong>October</strong></div><div class="fomc-meeting__date col">27-28</div>'
             '<div class="fomc-meeting__month col-xs-5"><strong>December</strong></div><div class="fomc-meeting__date col">8-9*</div>'
             '<h4><a id="2">2025 FOMC Meetings</a></h4>'
             '<div class="fomc-meeting__month"><strong>August</strong></div><div class="fomc-meeting__date">22 (notation vote)</div>'
             '<div class="fomc-meeting__month"><strong>Apr/May</strong></div><div class="fomc-meeting__date">30-1</div>')
page = lambda: FOMC_HTML


def observations(last, start, step=0.01, n=300, gaps=()):
    """Daily FRED observations ending the day before TODAY; '.' marks a missing value."""
    first = TODAY - timedelta(days=n)
    return [{'date': (first + timedelta(days=i)).isoformat(), 'value': '.' if i in gaps else f'{start + i * step:.2f}'}
            for i in range(n)]


def fake_get(calls=None, fail=(), stale=()):
    def get(path, **params):
        if calls is not None:
            calls.append((path, params))
        if path == 'series/observations':
            sid = params['series_id']
            if sid in fail:
                raise TimeoutError('slow host with a secret-looking query')
            obs = observations(None, 1.0, gaps=(299,))
            if sid in stale:
                obs = obs[:200]
            return {'observations': obs}
        if path == 'releases/dates':
            return {'release_dates': [
                {'release_id': 1, 'release_name': 'Employment Situation', 'date': '2026-12-04'},
                {'release_id': 2, 'release_name': 'Consumer Price Index', 'date': '2026-12-10'},
                {'release_id': 3, 'release_name': 'FOMC Press Release', 'date': '2026-11-19'},  # FRED lists it daily
                {'release_id': 4, 'release_name': 'H.4.1 Factors Affecting Reserve Balances', 'date': '2026-11-19'},
                {'release_id': 2, 'release_name': 'Consumer Price Index', 'date': '2026-12-10'},
                {'release_id': 1, 'release_name': 'Employment Situation', 'date': '2027-01-08'}]}  # past the window
        raise AssertionError(path)
    return get


def test_collect_reads_each_series_and_the_calendar():
    calls = []
    m = M.collect(TODAY, get=fake_get(calls), key='k', page=page)
    assert set(m['series']) == set(M.SERIES) and m['errors'] == []
    s = m['series']['BAMLH0A0HYM2']
    # the last value is '.', so the latest is the one before it (day 298 → 1.0 + 298 × 0.01)
    assert s['value'] == pytest.approx(3.98) and s['date'] == (TODAY - timedelta(days=2)).isoformat()
    assert s['chg_1m'] == pytest.approx(0.21) and s['low_1y'] == pytest.approx(1.0) and s['high_1y'] == pytest.approx(3.98)
    assert s['label'] == '하이일드 스프레드' and not s['stale']
    # watched releases only, one row each, FOMC from the Fed's page, in date order within 30 days
    assert [(e['date'], e['label']) for e in m['events']] == [('2026-12-04', '고용'), ('2026-12-09', 'FOMC'), ('2026-12-10', 'CPI')]
    assert {p for p, _ in calls} == {'series/observations', 'releases/dates'}


def test_fomc_days_are_announcement_days_of_scheduled_meetings():
    assert M.fomc_days(FOMC_HTML) == [date(2025, 5, 1), date(2026, 10, 28), date(2026, 12, 9)]


def test_no_key_means_no_fred_call_and_a_reason():
    calls = []
    m = M.collect(TODAY, get=fake_get(calls), key='', page=page)
    assert calls == [] and m['series'] == {} and m['events'] == [{'date': '2026-12-09', 'label': 'FOMC'}]
    assert m['errors'] == ['FRED_API_KEY 없음']
    m = M.collect(TODAY, get=fake_get(), key='', page=lambda: 1 / 0)
    assert m['errors'] == ['fomc: ZeroDivisionError', 'FRED_API_KEY 없음']


def test_one_failure_drops_only_that_input_and_records_the_type_only():
    m = M.collect(TODAY, get=fake_get(fail={'DFII10'}), key='k', page=page)
    assert 'DFII10' not in m['series'] and len(m['series']) == len(M.SERIES) - 1
    assert m['errors'] == ['DFII10: TimeoutError']  # never the message (it may carry the request URL)


def test_stale_series_are_flagged():
    m = M.collect(TODAY, get=fake_get(stale={'T10Y2Y'}), key='k', page=page)
    assert m['series']['T10Y2Y']['stale'] and not m['series']['DGS10']['stale']


def test_brief_is_short_text_with_the_basis():
    m = M.collect(TODAY, get=fake_get(), key='k', page=page)
    text = M.brief(m)
    assert '하이일드 스프레드 3.98%p' in text and '1개월 +0.21' in text and 'FOMC 2026-12-09' in text
    assert M.brief({'series': {}, 'events': [], 'errors': ['FRED_API_KEY 없음']}) == '거시 자료 없음(FRED_API_KEY 없음)'


def test_decide_records_macro_and_hands_it_to_the_story_check_without_touching_the_rules(tmp_path, monkeypatch):
    import json
    from datetime import datetime
    import vibe_longterm as L
    from test_vibe_longterm import DAY, fake_fetch
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    now = datetime.combine(DAY, datetime.min.time(), L.KST).replace(hour=8, minute=45)
    answer = json.dumps({'market_view': 'v', 'stories': {k: {'status': 'intact', 'tilt': 0, 'reason': 'ok'} for k in L.SATS}})
    prompts = []
    ask = lambda text: prompts.append(text) or answer
    facts = M.collect(DAY, get=fake_get(), key='k', page=page)
    assert L.decide(tmp_path / 'a', now, fetch=fake_fetch, ask=ask, macro=lambda d: facts) == 'decided'
    assert '하이일드 스프레드 3.98%p' in prompts[-1]
    with_macro = L.read_state(tmp_path / 'a')['plan']
    assert with_macro['macro']['series']['DGS10']['label'] == '미 10년 국채금리'
    rows = L.connect(tmp_path / 'a').execute("SELECT reason FROM decisions WHERE kind = 'macro'").fetchall()
    assert len(rows) == 1 and '다가오는 발표' in rows[0][0]
    # without the key (and the Fed page down) the day is decided the same way
    assert L.decide(tmp_path / 'b', now, fetch=fake_fetch, ask=ask) == 'decided'
    assert 'a reference, not a rule):\nnot available' not in prompts[-1] and '거시 자료 없음' in prompts[-1]  # the macro slot (news has its own)
    without = L.read_state(tmp_path / 'b')['plan']
    for k in ('regime', 'raw', 'active', 'impacts', 'levels', 'weights'):
        assert with_macro[k] == without[k], k
    assert without['macro']['errors'] == ['fomc: OSError', 'FRED_API_KEY 없음']
