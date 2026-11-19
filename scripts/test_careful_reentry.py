"""신중 재진입 (user 2026-11-18): same-day re-entry in a regime against the position."""
import json
import sqlite3
from contextlib import ExitStack
from datetime import datetime, timedelta
from unittest.mock import patch

import jev_context
import nh_paper_isolated as N

VIEW = {'코스닥': {'regime': '하락장'}, '코스피': {'regime': '상승장'}}


def bar(t, close, vol=100):
    return {'bsop_time': t + '00', 'stck_oprc': close, 'stck_hgpr': close, 'stck_lwpr': close, 'stck_prpr': close, 'vol': vol}


def session(name, closes):
    rows = [bar(f'{9 + (i + 1) // 60:02d}{(i + 1) % 60:02d}', c) for i, c in enumerate(closes)]
    jev_context._DAY.update(day=datetime.now(N.KST).strftime('%Y%m%d'), bars={})
    jev_context._merge({name: rows})
    return rows[::-1]


def test_due_only_for_same_day_reentry_in_a_regime_against_the_position():
    closed = {'043260': 26875}
    assert N.careful_due('043260', {}, 'watchlist_strength', closed, VIEW, '코스닥') == '하락장'
    assert N.careful_due('043260', {}, 'jev', closed, VIEW, '코스닥') == '하락장'
    assert N.careful_due('043260', {}, 'jev', {}, VIEW, '코스닥') is None  # not traded today
    assert N.careful_due('043260', {'043260': 5}, 'jev', closed, VIEW, '코스닥') is None  # held: an add, not a re-entry
    assert N.careful_due('043260', {}, 'new_listing', closed, VIEW, '코스닥') is None
    assert N.careful_due('005930', {}, 'jev', {'005930': 1}, VIEW, '코스피') is None  # 상승장 favours a long
    assert N.careful_due('114800', {}, 'jev', {'114800': 1}, {'코스피': {'regime': '하락장'}}, '코스피') is None  # favours the inverse
    # r3 E11: an unknown regime is checked as careful (missing data waits); r3 E12: an inverse re-enters only through
    # inverse_gate (하락장 with every view agreeing), so it is never a careful re-entry
    assert N.careful_due('043260', {}, 'jev', closed, None, '코스닥') == '판단 불가'
    assert N.careful_due('043260', {}, 'jev', closed, {'코스닥': {'regime': '판단 불가'}}, '코스닥') == '판단 불가'
    assert N.careful_due('114800', {}, 'jev', {'114800': 1}, None, '코스피') is None
    with patch.dict(N.CAREFUL, on=False):
        assert N.careful_due('043260', {}, 'jev', closed, VIEW, '코스닥') is None


def test_tape_blocks_chasing_and_weakening_and_unknown_sessions():
    rising = [100 + i * 0.1 for i in range(40)]  # steady climb: the last price is the day's high
    assert N.careful_tape('043260', session('043260', rising))[0] == 'careful_reentry_chase'
    falling = [110 - i * 0.2 for i in range(40)]  # under VWAP
    assert N.careful_tape('043260', session('043260', falling))[0] == 'careful_reentry_weak'
    pullback = [100 + i for i in range(20)] + [119 - i * 0.25 for i in range(20)]  # ran, then came back near VWAP
    why, summary = N.careful_tape('043260', session('043260', pullback))
    assert why is None and 0 <= summary['vs_vwap_pct'] <= 2 and summary['range_position_pct'] < 85
    assert N.careful_tape('999999', session('999999', [100] * 5))[0] == 'careful_reentry_no_session'


def test_review_asks_jev_with_todays_trades_and_reuses_the_answer(tmp_path):
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE fills (order_id TEXT, symbol TEXT, side TEXT, quantity INT, price REAL, time TEXT)')
    db.execute('CREATE TABLE sell_order_triggers (order_id TEXT PRIMARY KEY, trigger TEXT NOT NULL)')
    day = datetime.now(N.KST).strftime('%Y-%m-%d')
    db.execute("INSERT INTO fills VALUES ('b','043260','BUY',19,26000,?)", (f'{day}T09:28:00+09:00',))
    db.execute("INSERT INTO fills VALUES ('s','043260','SELL',19,26875,?)", (f'{day}T09:38:00+09:00',))
    db.execute("INSERT INTO sell_order_triggers VALUES ('s','risk')")
    asked = []

    def jev(bars, news, name, code, facts, investor=None, extra_state=None):
        asked.append(facts)
        return {'reentry': {'choice': 'BUY', 'confidence': 0.55}, 'input_digest': 'd', 'evidence': {'why_driver': {'choice': 'INTRADAY', 'confidence': 0.4}}}
    N._CAREFUL_REVIEW.clear()
    with patch('nh_etf_auto_v2.jev_reentry7', jev), patch.object(N, 'market_view', lambda root: VIEW), \
            patch.object(N, 'premarket_view', lambda: None), patch.object(N, 'trading_journal_brief', lambda: {}), \
            patch.object(N, 'track_record', lambda root: {}):
        first = N.careful_review(tmp_path, db, day.replace('-', ''), '043260', '043260', {}, [], None, '하락장', {'vs_vwap_pct': 1}, 'c')
        again = N.careful_review(tmp_path, db, day.replace('-', ''), '043260', '043260', {}, [], None, '하락장', {}, 'c')
    assert first == again == 'careful_reentry_jev_wait' and len(asked) == 1  # BUY 0.55 < 0.6; reused within 5 minutes
    (trade,) = asked[0]['today_trades']
    assert trade['opened'] == '09:28' and trade['closed'] == '09:38' and trade['exit_trigger'] == 'risk' and trade['return_pct'] == 3.37
    (event,) = [json.loads(line) for line in (tmp_path / 'events.jsonl').read_text().splitlines()]
    assert event['event'] == 'careful_reentry_review' and event['permitted'] is False and event['evidence']
    N._CAREFUL_REVIEW.clear()
    with patch('nh_etf_auto_v2.jev_reentry7', side_effect=OSError('down')), patch.object(N, 'market_view', lambda root: VIEW):
        assert N.careful_review(tmp_path, db, day.replace('-', ''), '043260', '043260', {}, [], None, '하락장', {}, 'c') == 'careful_reentry_review_error'
    N._CAREFUL_REVIEW.clear()


def test_entry_block_applies_the_tape_rule_only_to_a_careful_reentry(tmp_path):
    chase = session('043260', [100 + i * 0.1 for i in range(40)])
    with patch.object(N, 'market_view', lambda root: VIEW), patch.object(N, 'symbol_index', lambda root, code: '코스닥'), \
            patch.object(N, 'entry_confirmation', lambda *a, **k: None):
        assert N.entry_block(tmp_path, '043260', {}, 'jev', chase, {'043260': 1}, False, name='043260') == 'careful_reentry_chase'
        assert N.entry_block(tmp_path, '043260', {}, 'jev', chase, {}, False, name='043260') is None  # first entry today


def test_reentry_question_sends_the_trades_and_the_chart_label():
    import nh_etf_auto_v2 as ai
    from etf_universe import SYMBOLS
    sent = []

    class Response:
        def __init__(self, data): self.data = data
        def __enter__(self): return self
        def __exit__(self, *a): return None
        def read(self, *a): return json.dumps(self.data).encode()

    def respond(req, timeout):
        sent.append(json.loads(req.data))
        return Response({'model': 'jev-1.14.0', 'answers': {'reentry': {'type': 'choice', 'choice': 'WAIT', 'confidence': 0.7}}})
    now = datetime.now(N.KST)
    bars = {name: [{'bsop_time': '0930' + '00', 'stck_oprc': '1', 'stck_hgpr': '1', 'stck_lwpr': '1', 'stck_prpr': str(100 + i), 'vol': '1'}
                   for i in range(6)] for name in [*SYMBOLS, '043260']}
    with patch.object(ai.old, 'dotenv_values', return_value={'JEV_API_KEY': 'fake'}), patch.object(ai.urllib.request, 'urlopen', side_effect=respond):
        out = ai.jev_reentry7(bars, [{'title': 't', 'published': now.isoformat()}], '043260', '043260', {'regime': '하락장', 'today_trades': [{'closed': '09:38'}]})
    position = sent[0]['state']['position']
    assert out['reentry']['choice'] == 'WAIT' and position['chart_label'] == '043260' and position['reentry']['today_trades']
    assert set(sent[0]['questions']['reentry']['criteria']) == {'BUY', 'WAIT'}


def test_stock_headlines_are_recent_and_carry_their_age():
    from datetime import timedelta
    now = datetime(2026, 11, 18, 12, 40, tzinfo=N.KST)
    news = [{'title': '성호전자 어제 기사', 'published': (now - timedelta(hours=20)).isoformat()},
            {'title': '성호전자 방금', 'published': (now - timedelta(minutes=5)).isoformat()},
            {'title': '성호전자 한 시간 전', 'published': (now - timedelta(minutes=60)).isoformat()},
            {'title': '시각 없음'}, '문자열 기사']
    assert N.recent_stock_headlines(news, now) == [{'title': '성호전자 방금', 'age_min': 5}, {'title': '성호전자 한 시간 전', 'age_min': 60}]
    assert N.recent_stock_headlines(None, now) == []


def test_candidate_figures_come_from_the_latest_ranking_quote(tmp_path):
    from datetime import timedelta
    now = datetime(2026, 11, 18, 13, 5, tzinfo=N.KST)
    (tmp_path / 'movers.json').write_text(json.dumps({'time': (now - timedelta(minutes=4)).isoformat(),
                                                     'rows': {'043260': {'price': 26000, 'change_pct': 12.4, 'volume': 5, 'value_mil': 120000}}}))
    ranked = N.latest_mover_rows(tmp_path, now)
    stale = {'name': '성호전자', 'change_pct': 10.99, 'value_mil': 100575, 'price': 25250}
    fresh = N.with_latest_figures(stale, ranked.get('043260'), ranked['_as_of'])
    assert fresh['change_pct'] == 12.4 and fresh['price'] == 26000 and fresh['figures_as_of'] == '13:01' and fresh['name'] == '성호전자'
    assert N.with_latest_figures(stale, None, None) is stale  # not in the ranking: the watchlist figures stay
    assert N.latest_mover_rows(tmp_path, now + timedelta(minutes=20)) == {}  # a stale ranking is not used


def fills_db(*rows):
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE fills (order_id TEXT, symbol TEXT, side TEXT, quantity INT, price REAL, time TEXT)')
    db.execute('CREATE TABLE sell_order_triggers (order_id TEXT PRIMARY KEY, trigger TEXT NOT NULL)')
    db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?)', rows)
    return db


def round_trip(n, minute, day=None):
    day = day or datetime.now(N.KST).strftime('%Y-%m-%d')
    return [(f'b{n}', '043260', 'BUY', 9, 26000, f'{day}T10:{minute:02d}:00+09:00'),
            (f's{n}', '043260', 'SELL', 9, 26300, f'{day}T10:{minute + 3:02d}:00+09:00')]


def jev_saying(choice, asked):
    def jev(bars, news, name, code, facts, investor=None, extra_state=None):
        asked.append({'facts': facts, 'extra_state': extra_state})
        return {'reentry': {'choice': choice, 'confidence': 0.7}}
    return jev


def quiet(*more):
    """The review's other context, held still; `more` patches join it."""
    stack = ExitStack()
    for p_ in (patch.object(N, 'market_view', lambda root: VIEW), patch.object(N, 'premarket_view', lambda: None),
               patch.object(N, 'trading_journal_brief', lambda: {}), patch.object(N, 'track_record', lambda root: {}), *more):
        stack.enter_context(p_)
    return stack


def test_a_strength_slot_the_careful_review_refuses_does_not_take_jevs_turn(tmp_path):
    # r3 E1: the slot is ready only when the careful re-entry review would let it buy; a cached WAIT keeps it off too
    rows = session('043260', [100 + i for i in range(20)] + [119 - i * 0.25 for i in range(20)])  # passes the tape
    db, asked = fills_db(*round_trip(1, 0)), []
    review = lambda name, code, regime, summary: N.careful_review(tmp_path, db, datetime.now(N.KST).strftime('%Y%m%d'), name, code,
                                                                {name: rows}, [], None, regime, summary, 'c')
    N._CAREFUL_REVIEW.clear()
    with quiet(patch.object(N, 'symbol_index', lambda root, c: '코스닥'), patch.object(N, 'entry_confirmation', lambda *a, **k: None),
               patch('nh_etf_auto_v2.jev_reentry7', jev_saying('WAIT', asked)),
               patch.object(N, 'strength_candidate', lambda *a, **k: ('043260', {'jev_watchlist_confidence': 0.7, 'change_pct': 5.0})),
               patch.object(N, 'entries_today', lambda *a: 0), patch.object(N, 'exit_plans', lambda root: {}),
               patch.multiple(N, STRENGTH_SLOT=True, STRENGTH_TOP_N=3, IPO_SLOT=False, REENTRY_ALLOW=True)):
        slot = lambda: N.slot_entry(None, tmp_path, {}, {'043260': {}}, {'043260': rows}, {'043260': 26300}, {'114800'},
                                    datetime.now(N.KST), review=review)
        assert slot() is None and len(asked) == 1  # Jev said WAIT: Jev's own pick keeps the cycle
        assert slot() is None and len(asked) == 1  # the cached WAIT, without asking again
    N._CAREFUL_REVIEW.clear()


def test_a_new_round_trip_asks_the_careful_review_again(tmp_path):
    # r3 E2: a cached answer stands only for the round trips Jev saw; a trade closed since needs a new review
    db, asked = fills_db(*round_trip(1, 0)), []
    day = datetime.now(N.KST).strftime('%Y%m%d')
    N._CAREFUL_REVIEW.clear()
    with quiet(patch('nh_etf_auto_v2.jev_reentry7', jev_saying('BUY', asked))):
        assert N.careful_review(tmp_path, db, day, '043260', '043260', {}, [], None, '횡보장', {}, 'c1') is True
        assert N.careful_review(tmp_path, db, day, '043260', '043260', {}, [], None, '횡보장', {}, 'c2') is True
        assert len(asked) == 1  # same trades: reused
        db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?)', round_trip(2, 20))  # bought again and stopped out
        assert N.careful_review(tmp_path, db, day, '043260', '043260', {}, [], None, '횡보장', {}, 'c3') is True
    assert [len(a['facts']['today_trades']) for a in asked] == [1, 2]
    N._CAREFUL_REVIEW.clear()


def test_the_careful_tape_uses_nhs_session_figures(tmp_path):
    # r3 E3: the bars this process saw start late in the session; NH's quote has the day's high, low and 가중가 (VWAP)
    name = 'E3X'
    early = [120 + (i % 5) * 2 for i in range(30)]                                    # 09:01-09:30, the day's high
    later = [120 - i * 0.33 for i in range(30)] + [110 + i * 0.4 for i in range(20)] + [117.6 - i * 0.2 for i in range(8)]
    seen = session(name, (early + later)[-30:])                                     # first seen about 10:00
    assert N.careful_tape(name, seen)[0] is None                                    # the partial session passes it
    quote = {'stck_oprc': 120, 'stck_hgpr': 128, 'stck_lwpr': 110, 'stck_prpr': 116, 'wghn_avrg_prc': 122}
    why, summary = N.careful_tape(name, seen, quote)
    assert why == 'careful_reentry_weak' and (summary['vwap'], summary['high'], summary['low'], summary['open']) == (122, 128, 110, 120)
    assert jev_context.summary(name)['vwap'] == 122  # the Jev re-entry review's intraday_summary reads the same session
    no_vwap = N.careful_tape(name, seen, {k: v for k, v in quote.items() if k != 'wghn_avrg_prc'})[1]
    assert no_vwap['high'] == 128 and no_vwap['vwap_partial'] is True  # VWAP from the bars seen, marked partial
    assert 'session_source' not in N.careful_tape(name, seen)[1]  # no quote this time: the bars alone again


def test_entry_block_reads_nhs_quote_for_a_careful_reentry(tmp_path):
    name = 'E3Y'
    rows = session(name, [100 + i for i in range(20)] + [119 - i * 0.25 for i in range(20)])  # passes on its own bars
    now = datetime.now(N.KST)

    class Read:
        def call_tool(self, tool, params):
            assert tool == 'get_stock_price' and params == {'stock_code': '043260'}
            return {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': {
                'iem_cd': '043260', 'market_status': '1', 'hoga_bsop_hour': now.strftime('%H:%M:%S'),
                'stck_oprc': 100, 'stck_hgpr': 140, 'stck_lwpr': 100, 'stck_prpr': 114, 'wghn_avrg_prc': 125}}}
    with patch.object(N, 'market_view', lambda root: VIEW), patch.object(N, 'symbol_index', lambda root, code: '코스닥'), \
            patch.object(N, 'entry_confirmation', lambda *a, **k: None):
        assert N.entry_block(tmp_path, '043260', {}, 'jev', rows, {'043260': 1}, False, name=name, read=Read()) == 'careful_reentry_weak'


def test_the_careful_review_sends_the_stocks_news_and_the_playbook(tmp_path):
    # r3 E6: the same stock facts, recent headlines and market_playbook the main entry call sends
    import market_playbook
    now = datetime.now(N.KST)
    details = {'name': '성호전자', 'price': 26000, 'news': [{'title': '성호전자 방금', 'published': now.isoformat()},
                                                          {'title': '성호전자 어제', 'published': (now - timedelta(hours=20)).isoformat()}]}
    db, asked = fills_db(*round_trip(1, 0)), []
    N._CAREFUL_REVIEW.clear()
    with quiet(patch('nh_etf_auto_v2.jev_reentry7', jev_saying('WAIT', asked)), patch('watchlist.fresh_stock_news', lambda *a, **k: None)):
        N.careful_review(tmp_path, db, now.strftime('%Y%m%d'), '043260', '043260', {}, [], None, '횡보장', {}, 'c', details)
    sent = asked[0]['extra_state']
    assert sent['market_playbook'] == market_playbook.brief('entry')
    assert [h['title'] for h in sent['watchlist_details']['043260']['headlines']] == ['성호전자 방금']
    N._CAREFUL_REVIEW.clear()


def test_an_unknown_regime_reentry_gets_the_careful_checks(tmp_path):
    # r3 E11: no fresh market view used to skip the tape, the review and the half size
    rows = session('043260', [100 + i for i in range(20)] + [119 - i * 0.25 for i in range(20)])
    with patch.object(N, 'market_view', lambda root: None), patch.object(N, 'symbol_index', lambda root, c: '코스닥'), \
            patch.object(N, 'entry_confirmation', lambda *a, **k: None):
        seen = []
        refused = N.entry_block(tmp_path, '043260', {}, 'jev', rows, {'043260': 1}, False, name='043260',
                                review=lambda name, code, regime, summary: seen.append(regime) or 'careful_reentry_jev_wait')
    assert (refused, seen) == ('careful_reentry_jev_wait', ['판단 불가'])
