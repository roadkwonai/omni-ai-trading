import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import overnight_catalysts as o


@pytest.mark.parametrize('title, expect', [
    ('JYP Ent., 3분기 영업익 480억…전년비 17.6% ↑', ('실적', 1)),
    ('성호전자, 3분기 영업익 703% 증가…M&A 효과 본격화', ('실적', 1)),
    ('메리츠금융지주 3분기 영업익 5천199억원…39.7% 감소', ('실적', -1)),
    ('에스엘, 외형성장 불구 수익성 악화 판관비·환손실 급증', ('실적', -1)),
    ('리가켐바이오, 3분기 영업손실 451억⋯전년 대비 141.2%↑', ('실적', -1)),  # a loss that grew is not a rise
    ('휴림로봇 주가 불기둥...한화로보틱스와 로봇 공급 계약', ('수주·계약', 1)),
    ('삼성전기, 엔비디아 협력확대로 기판 수익성 40%로 확대 목표가 상향', ('목표가', 1)),
    ('KB증권, 에스엘 목표가 하향', ('목표가', -1)),
    ('휴림로봇 상한가…피지컬 AI 확산에 로봇주 들썩', ('급등', 1)),
    ('주식회사 실리콘투 자기주식취득 신탁계약 해지 결정', (None, 0)),
    ('단일판매ㆍ공급계약체결', ('수주·계약', 1)),
    ('유상증자결정', ('자금', -1)),
    ('두산, 다문화 가정 등에 장학금 5억 원 지원', (None, 0)),
    ('삼성전기, 부사장 5명 등 15명 승진…성과주의 적용', (None, 0)),
    ('주식선물 2단계 가격제한폭 확대요건 도달(상승)', (None, 0)),
    ('삼성SDI, 美 RE+ 2026서 북미 ESS 공략…현지 생산 LFP 배터리 공개', (None, 0)),
])
def test_a_title_is_typed_by_rule(title, expect):
    assert o.classify(title) == expect


def test_a_code_with_good_and_bad_news_is_neither():
    good, bad = {'type': '실적', 'direction': 1}, {'type': '자금', 'direction': -1}
    assert [o.direction(x) for x in ([good], [bad], [good, bad], [{'type': None, 'direction': 0}], [])] == ['호재', '악재', '혼재', '무관', '없음']


def test_the_window_is_the_previous_close_to_the_bell(monkeypatch):
    monkeypatch.setattr(o, 'trading_day', lambda now: (now.weekday() < 5 and now.strftime('%Y-%m-%d') != '2026-11-23', ''))
    start, end = o.window('2026-11-24')  # 11-23 a holiday here, 21~22 the weekend
    assert (start, end) == (datetime(2026, 11, 20, 15, 30, tzinfo=o.KST), datetime(2026, 11, 24, 9, 0, tzinfo=o.KST))


def answers(pages, filings):
    asked = []

    def get(url):
        asked.append(url)
        if '/disclosure' in url:
            return filings
        page = int(url.rsplit('=', 1)[1])
        return [{'items': [i]} for i in pages[page - 1]] if page <= len(pages) else []
    return get, asked


def test_collect_takes_the_titles_of_the_window_that_name_the_stock():
    item = lambda t, title, office='뉴스핌': {'datetime': t, 'title': title, 'officeName': office, 'mobileNewsUrl': 'https://n/1'}
    get, asked = answers([[item('202611240910', '가나 수주 계약 체결'), item('202611240855', '가나 수주 &quot;대형&quot; 계약 체결'),
                           item('202611240700', '가나 3분기 영업익 10% 증가', '연합뉴스')],  # after the bell; in; a publisher that is not stored
                          [item('202611231600', '다른 회사 흑자전환'), item('202611231529', '가나 흑자전환')],  # not its name; before the close
                          [item('202611200900', '가나 상한가')]],
                         [{'title': '단일판매ㆍ공급계약체결', 'datetime': '2026-11-23T17:00:01'}, {'title': '유상증자결정', 'datetime': '2026-11-23T15:00:00'}])
    start, end = datetime(2026, 11, 23, 15, 30, tzinfo=o.KST), datetime(2026, 11, 24, 9, 0, tzinfo=o.KST)
    rows = o.collect('000001', '가나', start, end, get=get, rest=lambda s: None)
    assert [(r['time'][11:16], r['source'], r['type']) for r in rows] == [('08:55', '뉴스핌', '수주·계약'), ('17:00', '공시', '수주·계약')]
    assert rows[0]['title'] == '가나 수주 "대형" 계약 체결' and rows[0]['link'] == 'https://n/1'
    assert sum('/news/' in u for u in asked) == 2  # the page that reached the close ends it


def test_a_list_that_never_reaches_the_close_is_a_failure_not_a_day_without_news():
    get, _ = answers([[{'datetime': '202611240910', 'title': '가나', 'officeName': 's'}]] * 99, [])
    with pytest.raises(ValueError, match='too long'):
        o.collect('000001', '가나', datetime(2026, 11, 23, 15, 30, tzinfo=o.KST), datetime(2026, 11, 24, 9, tzinfo=o.KST), get=get, rest=lambda s: None)


def bars(day, closes):
    """Five-minute bars from 09:00 (stamped at their start): [(datetime, o, h, l, c)]."""
    y, m, d = map(int, day.split('-'))
    return [(datetime(y, m, d, 9 + i // 12, i % 12 * 5, tzinfo=o.KST), c, c + 1, c - 1, c) for i, c in enumerate(closes)]


def test_the_outcome_starts_at_ten_past_nine():
    prev = [(datetime(2026, 11, 23, 15, 25, tzinfo=o.KST), 100, 100, 100, 100)]
    got = o.outcome('2026-11-24', prev + bars('2026-11-24', [104, 106] + [106] * 6 + [109] * 64))
    assert (got['gap_pct'], got['chg_0910']) == (4.0, 6.0)
    assert (got['r30'], got['r60'], got['r_last']) == (0.0, 2.83, 2.83)  # 106 at 09:10
    assert o.outcome('2026-11-24', prev) is None


def test_update_stores_the_day_once_and_the_summary_splits_by_the_price_reaction(tmp_path):
    research = tmp_path / 'market-research.sqlite3'
    with sqlite3.connect(research) as db:
        db.execute('CREATE TABLE model_inputs (digest TEXT PRIMARY KEY, time TEXT, engine TEXT, question TEXT, inputs TEXT, response TEXT)')
        db.execute('INSERT INTO model_inputs VALUES (?,?,?,?,?,?)', ('x', '2026-11-24T09:11:00+09:00', 'jev', 'watchlist', json.dumps(
            {'state': {'stocks': {'000001': {'name': '가나'}, '000009': {'name': '후보'}}}}), json.dumps({'probabilities': {'000001': 0.2, '000009': 0.1}})))
    found = {'000001': [{'time': '2026-11-24T08:00:00+09:00', 'title': 't', 'source': 's', 'link': '', 'type': '실적', 'direction': 1}],
             '000002': [{'time': '2026-11-24T08:00:00+09:00', 'title': 't', 'source': 's', 'link': '', 'type': None, 'direction': 0}]}
    prev = [(datetime(2026, 11, 23, 15, 25, tzinfo=o.KST), 100, 100, 100, 100)]
    prices = lambda code, day: prev + bars(day, [104, 106] + [107] * 70)
    for _ in range(2):  # a rerun replaces the day
        out = o.update(tmp_path, '2026-11-24', universe={'000001': {'name': '가나'}, '000002': {'name': '다라'}},
                       window_of=lambda day: (None, None), collect_of=lambda code, name, s, e: found.get(code, []), prices_of=prices)
    assert out == {'day': '2026-11-24', 'rows': 2, '호재': 1, '없음': 1, 'failed': 0}  # 무관 is not followed; 000009 is the candidate without news
    with sqlite3.connect(research) as db:
        assert db.execute('SELECT code, direction, jev_prob, chg_0910 FROM overnight_catalysts ORDER BY code').fetchall() == [
            ('000001', '호재', 0.2, 6.0), ('000009', '없음', 0.1, 6.0)]
    got = o.summary(research, '2026-11-24')
    assert got['호재']['+2% 이상']['n'] == 1 and got['호재']['+2% 이상']['avg_r30_pct'] == 0.94 and got['호재']['+2% 이상']['vs_kodex200_r30_pct'] == 0.0
    assert o.summary(tmp_path / 'none.sqlite3', '2026-11-24') == {}


def test_a_day_with_almost_no_answers_is_not_stored(tmp_path):
    def down(*a):
        raise OSError('blocked')
    with pytest.raises(RuntimeError, match='0/2'):
        o.update(tmp_path, '2026-11-24', universe={'000001': {'name': '가'}, '000002': {'name': '나'}}, window_of=lambda d: (None, None),
                 collect_of=down, prices_of=lambda c, d: [])
    assert not (tmp_path / 'market-research.sqlite3').exists()


def daily(*closes, start=datetime(2026, 11, 2, tzinfo=o.KST)):
    """Daily bars of weekdays from 11-02: [(datetime, o, h, l, c)]."""
    from datetime import timedelta
    out, at = [], start
    for c in closes:
        while at.weekday() >= 5:
            at += timedelta(days=1)
        out.append((at, c, c, c, c))
        at += timedelta(days=1)
    return out


def test_the_overseas_move_adds_up_the_sessions_between_two_korean_days():
    rows = daily(*[100, 101] * 5, 110, 121, 133.1)  # ... 11-16 110, 11-17 121, 11-18 133.1
    ret, threshold = o.overseas_move(rows, '2026-11-17', '2026-11-18')
    assert round(ret, 2) == 10.0 and round(threshold, 2) == 3.72  # the session of 11-17 only
    assert round(o.overseas_move(rows, '2026-11-16', '2026-11-18')[0], 2) == 19.8  # a Korean holiday: both sessions, +8.91% and +10%
    assert o.overseas_move(rows, '2026-11-20', '2026-11-23') is None  # a US holiday: nothing new came out


def test_overseas_rows_are_stored_for_every_pair_and_summed_by_signal_and_gap(tmp_path):
    rows = daily(*[100, 101] * 5, 100, 110)  # 11-17 +10%
    bars_of = lambda code, day: [(datetime(2026, 11, 17, 15, 25, tzinfo=o.KST), 100, 100, 100, 100)] + bars('2026-11-18', [104, 106] + [107] * 70)
    pairs = {'반도체': ('^SOX', ('000001', '000002')), '끊긴 신호': ('X', ('000003',))}
    for _ in range(2):  # a rerun replaces the day
        out = o.update_overseas(tmp_path, '2026-11-18', pairs=pairs, window_of=lambda d: (datetime(2026, 11, 17, 15, 30, tzinfo=o.KST), None),
                                daily_of=lambda s: rows if s == '^SOX' else [], prices_of=bars_of)
    assert out == {'day': '2026-11-18', 'rows': 2, 'signals': 1}
    with sqlite3.connect(tmp_path / 'market-research.sqlite3') as db:
        assert db.execute('SELECT signal, code, us_ret, gap_pct, r30 FROM overnight_overseas ORDER BY code').fetchall() == [
            ('반도체', '000001', 10.0, 4.0, 0.94), ('반도체', '000002', 10.0, 4.0, 0.94)]
    got = o.summary(tmp_path / 'market-research.sqlite3', '2026-11-18')
    assert got['해외']['급등']['갭 +3% 이상'] == {'n': 2, 'avg_r30_pct': 0.94, 'avg_r60_pct': 0.94, 'avg_to_close_pct': 0.94}
    assert got['해외']['기록일'] == 1


def test_one_record_failing_does_not_lose_the_other(tmp_path, monkeypatch):
    monkeypatch.setattr(o, 'update', lambda root, day: (_ for _ in ()).throw(RuntimeError('blocked')))
    monkeypatch.setattr(o, 'update_overseas', lambda root, day: {'rows': 3})
    assert o.run(tmp_path, '2026-11-18') == {'news_error': 'RuntimeError', 'overseas': {'rows': 3}}


def _clock(hhmm, day='2026-11-26'):
    return datetime.fromisoformat(f'{day}T{hhmm}:00').replace(tzinfo=o.KST)


UNIVERSE = {'000001': {'name': '가나'}, '000002': {'name': '다라'}, '000003': {'name': '마바'}}
NEWS = {'000001': [{'time': '2026-11-25T18:00:00+09:00', 'title': '가나 대규모 공급계약', 'source': 'P', 'link': 'https://x', 'type': '수주·계약', 'direction': 1}],
        '000002': [{'time': '2026-11-25T19:00:00+09:00', 'title': '다라 유상증자결정', 'source': '공시', 'link': '', 'type': '자금', 'direction': -1}],
        '000003': []}


@pytest.mark.parametrize('stored, hhmm, expect', [
    (None, '07:40', 'full'), (None, '08:20', 'full'), (None, '08:38', None), (None, '06:50', None), (None, '09:00', None),
    ('07:46', '08:00', None), ('07:46', '08:20', 'update'), ('08:24', '08:25', None), ('07:46', '08:40', None)])
def test_the_morning_scan_runs_once_and_is_updated_once(stored, hhmm, expect):
    record = stored and {'day': '2026-11-26', 'time': _clock(stored).isoformat()}
    assert o.due(_clock(hhmm), record) == expect


def test_yesterdays_scan_is_not_todays():
    assert o.due(_clock('07:40'), {'day': '2026-11-25', 'time': '2026-11-25T08:24:00+09:00'}) == 'full'


def test_the_morning_scan_keeps_what_the_full_one_found(tmp_path, monkeypatch):
    monkeypatch.setattr(o, 'trading_day', lambda now: (now.weekday() < 5, ''))
    asked = []

    def collect(code, name, start, end, filings=True):
        asked.append((code, start.strftime('%d %H:%M'), filings))
        return NEWS[code] if filings else ([{**NEWS['000001'][0], 'time': '2026-11-26T08:10:00+09:00', 'title': '마바 영업익 급증', 'type': '실적'}]
                                           if code == '000003' else [])
    signals = lambda *a: {'엔비디아': (5.2, 2.0, ('000660',)), '유가': (0.1, 2.0, ('010950',))}
    first = o.morning(tmp_path, _clock('07:40'), UNIVERSE, collect, signals, lambda *a: {})
    assert first['scanned'] == 3 and set(first['stocks']) == {'000001', '000002'}
    assert [s['signal'] for s in first['overseas']] == ['엔비디아']  # moves inside one standard deviation are not signals
    asked.clear()
    second = o.morning(tmp_path, _clock('08:20'), UNIVERSE, collect, signals, lambda *a: {})
    assert asked[0] == ('000001', '26 07:40', False)  # only what came since, news only
    assert set(second['stocks']) == {'000001', '000002', '000003'} and second['stocks']['000003']['direction'] == '호재'
    assert o.morning(tmp_path, _clock('08:25'), UNIVERSE, collect, signals) is None
    assert o.load_morning(tmp_path, _clock('09:30'))['stocks'] == second['stocks'] and o.load_morning(tmp_path, _clock('09:30', '2026-11-27')) is None


def test_a_morning_scan_that_hardly_answers_is_not_stored(tmp_path, monkeypatch):
    monkeypatch.setattr(o, 'trading_day', lambda now: (now.weekday() < 5, ''))

    def down(*a, **k):
        raise OSError
    with pytest.raises(RuntimeError):
        o.morning(tmp_path, _clock('07:40'), UNIVERSE, down, lambda *a: {})
    assert o.load_morning(tmp_path, _clock('07:50')) is None


def test_the_candidates_get_their_own_overnight_news_with_what_the_price_did(tmp_path, monkeypatch):
    monkeypatch.setattr(o, 'trading_day', lambda now: (now.weekday() < 5, ''))
    o.morning(tmp_path, _clock('07:40'), UNIVERSE, lambda c, n, s, e, filings=True: NEWS[c], lambda *a: {'엔비디아': (5.2, 2.0, ('000001',))}, lambda *a: {})
    starts = []

    def later(code, name, start, end):
        starts.append(start.strftime('%H:%M'))
        return [{**NEWS['000002'][0], 'title': '다라 리콜'}] if code == '000002' else []
    got = o.for_candidates({'000001': {'name': '가나', 'change_pct': 3.1}, '000002': {'name': '다라', 'change_pct': -1.0},
                            '000003': {'name': '마바', 'change_pct': 2.5}}, _clock('08:51'), tmp_path, later)
    assert set(starts) == {'07:40'}
    assert got['stocks']['000001']['direction'] == 'good' and got['stocks']['000001']['price_reaction'] == 'up'
    assert [t['title'] for t in got['stocks']['000002']['titles']] == ['다라 리콜', '다라 유상증자결정'] and got['stocks']['000002']['price_reaction'] == 'flat'
    assert '000003' not in got['stocks']
    assert got['overseas'] == [{'signal': '엔비디아', 'move_pct': 5.2, 'move': 'surge', 'stocks': ['000001'], 'headlines': None, 'titles': []}]


def test_the_field_ends_at_half_past_ten_and_a_dead_source_costs_one_call(tmp_path, monkeypatch):
    monkeypatch.setattr(o, 'trading_day', lambda now: (now.weekday() < 5, ''))
    calls = []

    def down(*a):
        calls.append(1)
        raise OSError
    stocks = {c: {'name': m['name'], 'change_pct': 1.0} for c, m in UNIVERSE.items()}
    assert o.for_candidates(stocks, _clock('10:31'), tmp_path, down) is None and calls == []
    assert o.for_candidates(stocks, _clock('09:05'), tmp_path, down)['not_checked'] == sorted(UNIVERSE) and len(calls) == 1


def test_candidates_are_asked_once_after_the_bell(tmp_path, monkeypatch):
    monkeypatch.setattr(o, 'trading_day', lambda now: (now.weekday() < 5, ''))
    calls = []
    ask = lambda *a: calls.append(1) or []
    stocks = {'000001': {'name': '가나', 'change_pct': 1.0}}
    for hhmm in ('08:51', '09:01', '09:03'):
        o.for_candidates(stocks, _clock(hhmm), tmp_path, ask)
    assert len(calls) == 2  # 08:51 is before the window closed


@pytest.mark.parametrize('title, expect', [
    ('월요일 엔비디아 호재에 힘입은 급등세 이후 나스닥·S&P 500 지수 하락', -1),  # the predicate ends a Korean title
    ('엔비디아 반등세 주춤', -1), ('델, AI 서버 수요 급증으로 분기 실적 사상 최고치 기록', 1),
    ('Cogent, FDA의 종양 치료제 승인 소식에 주가 상승', 1), ('황 씨의 엔비디아 복귀로 AI 규제 논쟁이 수면 위로', 0)])
def test_an_overseas_title_says_up_or_down_by_its_last_word(title, expect):
    assert o.tone(title) == expect


def _world(*rows):
    return [{'oid': 'fnGuide', 'aid': str(i), 'ohnm': '로이터', 'tit': t, 'dt': f'202611{d}'} for i, (d, t) in enumerate(rows)]


def test_the_world_news_is_read_back_to_the_close_and_filed_by_signal():
    pages = [_world(('26080000', '엔비디아 주가 급락'), ('26075000', '엔비디아 약세 지속'), ('26070000', '[속보] 엔비디아 마진 매수 잔고 상승'), ('26060000', '월가, 반도체주 약세에 하락 마감')),
             _world(('25160000', '국제유가 반등'), ('25155000', '원유 재고 감소에 상승'), ('25150000', '엔비디아 급등'))]  # 15:00 is before the close
    asked = []

    def get(url):
        asked.append(url)
        return pages[len(asked) - 1]
    start = datetime(2026, 11, 25, 15, 30, tzinfo=o.KST)
    got = o.world_news(start, datetime(2026, 11, 26, 9, 0, tzinfo=o.KST), get, lambda s: None)
    assert len(asked) == 2 and set(got) == {'엔비디아', '필라델피아반도체', '나스닥100', '유가'}
    assert got['엔비디아']['news'] == 'down' and [i['title'] for i in got['엔비디아']['items']] == ['엔비디아 주가 급락', '엔비디아 약세 지속']
    assert got['유가']['news'] == 'up' and got['유가']['items'][0]['link'] == 'https://m.stock.naver.com/investment/news/worldnews/fnGuide/0'
    assert got['나스닥100']['news'] is None  # one title is no direction
    assert got['필라델피아반도체']['items'][0]['title'].startswith('월가, 반도체주')


def test_a_world_list_that_is_not_one_is_a_failure():
    with pytest.raises(ValueError):
        o.world_news(datetime(2026, 11, 25, 15, 30, tzinfo=o.KST), datetime(2026, 11, 26, 9, 0, tzinfo=o.KST), lambda url: {}, lambda s: None)


def test_the_morning_joins_the_moves_abroad_with_their_headlines(tmp_path, monkeypatch):
    monkeypatch.setattr(o, 'trading_day', lambda now: (now.weekday() < 5, ''))
    item = {'title': '국제유가 반등', 'time': '2026-11-26T06:00:00+09:00', 'source': '로이터', 'link': 'https://x', 'direction': 1}
    world = lambda *a: {'유가': {'news': 'up', 'items': [item]}}
    signals = lambda *a: {'엔비디아': (-5.2, 2.0, ('000660',)), '유가': (0.1, 2.0, ('010950',))}
    quiet = lambda code, name, start, end, filings=True: []
    first = o.morning(tmp_path, _clock('07:40'), {**UNIVERSE, '010950': {'name': 'S-Oil'}}, quiet, signals, world)
    rows = {r['signal']: r for r in first['overseas']}
    assert rows['엔비디아'] == {'signal': '엔비디아', 'codes': ['000660'], 'names': ['000660'], 'move_pct': -5.2, 'threshold_pct': 2.0, 'big': True, 'news': None, 'items': []}
    assert rows['유가']['big'] is False and rows['유가']['names'] == ['S-Oil'] and rows['유가']['items'] == [item]

    def down(*a):
        raise OSError
    second = o.morning(tmp_path, _clock('08:20'), {**UNIVERSE, '010950': {'name': 'S-Oil'}}, quiet, signals, down)
    assert {r['signal']: r['items'] for r in second['overseas']} == {'엔비디아': [], '유가': [item]}  # the first look's headlines stay
    got = o.for_candidates({'010950': {'name': 'S-Oil', 'change_pct': 0.5}}, _clock('08:51'), tmp_path, lambda *a: [])
    assert got['overseas'] == [{'signal': '유가', 'move_pct': 0.1, 'move': 'normal', 'stocks': ['010950'], 'headlines': 'up', 'titles': ['국제유가 반등']}]


def test_a_bar_still_moving_in_the_korean_session_is_not_last_nights():
    """11-25: Yahoo's crude bar of 10:41 KST stood as the overnight move."""
    new_york = timezone(timedelta(hours=-5))
    rows = daily(*[100, 101] * 5, 110)  # ... 11-16 110
    live = lambda hour: (datetime(2026, 11, 17, hour, 41, tzinfo=new_york), 0, 0, 0, 121)
    assert o.overseas_move([*rows, live(20)], '2026-11-17', '2026-11-18') is None  # 10:41 KST of the Korean day
    assert round(o.overseas_move([*rows, live(17)], '2026-11-17', '2026-11-18')[0], 2) == 10.0  # 07:41 KST, before the bell


def test_the_last_close_stands_in_for_a_daily_bar_yahoo_has_not_filled(monkeypatch):
    """11-25: NVDA, TSLA, XBI... had a null bar for the night until the afternoon, so the surge signals were missing."""
    import io
    import oversold_research
    stamp = lambda text: int(datetime.fromisoformat(text).timestamp())
    result = {'meta': {'exchangeTimezoneName': 'America/New_York', 'regularMarketPrice': 155.38, 'regularMarketTime': stamp('2026-11-24T16:00:01-05:00')},
              'timestamp': [stamp('2026-11-23T09:30:00-05:00'), stamp('2026-11-24T09:30:00-05:00')],
              'indicators': {'quote': [{k: [165.05, None] for k in ('open', 'high', 'low', 'close')}]}}
    body = json.dumps({'chart': {'result': [result]}}).encode()
    monkeypatch.setattr(oversold_research.urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(body))
    assert [b[4] for b in oversold_research.fetch('NVDA', '1d', '2y')] == [165.05]
    rows = oversold_research.fetch('NVDA', '1d', '2y', last=True)
    assert [b[4] for b in rows] == [165.05, 155.38] and rows[-1][0].isoformat() == '2026-11-24T16:00:01-05:00'
    result['indicators']['quote'][0]['close'] = result['indicators']['quote'][0]['open'] = [165.05, 155.0]
    body = json.dumps({'chart': {'result': [{**result, 'indicators': {'quote': [{k: [165.05, 155.0] for k in ('open', 'high', 'low', 'close')}]}}]}}).encode()
    assert [b[4] for b in oversold_research.fetch('NVDA', '1d', '2y', last=True)] == [165.05, 155.0]  # a filled bar is the close


def test_the_evening_record_uses_the_moves_the_morning_saw(tmp_path):
    (tmp_path / o.MORNING).write_text(json.dumps({'day': '2026-11-18', 'time': '2026-11-18T08:20:00+09:00', 'moves': {'반도체': [10.0, 3.0]}}), encoding='utf-8')
    bars_of = lambda code, day: [(datetime(2026, 11, 17, 15, 25, tzinfo=o.KST), 100, 100, 100, 100)] + bars('2026-11-18', [104, 106] + [107] * 70)
    pairs = {'반도체': ('^SOX', ('000001',)), '유가': ('CL=F', ('000002',))}
    out = o.update_overseas(tmp_path, '2026-11-18', pairs=pairs, window_of=lambda d: (datetime(2026, 11, 17, 15, 30, tzinfo=o.KST), None),
                            daily_of=lambda s: [], prices_of=bars_of)
    assert out == {'day': '2026-11-18', 'rows': 1, 'signals': 1}
