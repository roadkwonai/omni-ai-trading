import json
from datetime import datetime
from zoneinfo import ZoneInfo

import jev_context as C

KST = ZoneInfo('Asia/Seoul')
NOW = datetime(2026, 11, 18, 11, 30, tzinfo=KST)


def bar(hhmm, close, vol=100):
    return {'bsop_time': f'{hhmm}00', 'stck_oprc': close, 'stck_hgpr': close + 1, 'stck_lwpr': close - 1, 'stck_prpr': close, 'vol': vol}


def minutes(start_h, start_m, n):
    out = []
    for i in range(n):
        h, m = divmod(start_h * 60 + start_m + i, 60)
        out.append(f'{h:02d}{m:02d}')
    return out


def test_backfill_from_logged_bars_then_session_summary(tmp_path):
    times = minutes(9, 1, 70)
    logged = {'K200': [bar(t, 1000 + i) for i, t in enumerate(times)][::-1]}  # a steady climb
    events = tmp_path / 'events.jsonl'
    events.write_text(json.dumps({'time': '2026-11-18T10:11:00+09:00', 'event': 'market_context', 'market_bars': logged}) + '\n'
                      + json.dumps({'time': '2026-11-17T10:11:00+09:00', 'event': 'market_context',
                                    'market_bars': {'K200': [bar('0930', 5)]}}) + '\n')  # another day: ignored
    newest = [bar(t, 1069 - i) for i, t in enumerate(minutes(10, 11, 10))][::-1]  # the call's own 10 bars, a pullback
    chart, extra = C.context({'K200': newest}, NOW, events)
    assert len(chart['K200']) == 60 and chart['K200'][0]['bsop_time'] == '102000'  # last hour, newest first
    s = extra['intraday_summary']['K200']
    assert s['open'] == 1000 and s['high'] == 1070 and s['bars'] == 80 and s['from'] == '0901'
    assert s['last'] == 1060 and s['from_high_pct'] == round((1060 / 1070 - 1) * 100, 2)
    assert 0 < s['range_position_pct'] < 100 and 'vs_vwap_pct' in s and 'change_60m_pct' in s
    assert extra['intraday_note'].startswith('intraday_summary')


def test_new_day_resets_and_short_history_gives_no_summary(tmp_path):
    C.context({'INV': [bar(t, 10) for t in minutes(9, 1, 12)]}, NOW, tmp_path / 'none')
    chart, extra = C.context({'INV': [bar(t, 10) for t in minutes(9, 1, 5)]}, datetime(2026, 11, 19, 9, 10, tzinfo=KST), tmp_path / 'none')
    assert len(chart['INV']) == 5 and extra == {}  # under 10 bars: no summary, the call's own bars are charted


def test_evidence_questions_are_choices_and_parse_skips_bad_answers():
    qs = C.questions()
    assert all(q['type'] == 'choice' and q['criteria'] for q in qs.values()) and all(k.startswith('why_') for k in qs)
    got = C.parse({'why_driver': {'choice': 'REGIME', 'confidence': 0.61, 'probabilities': {'REGIME': 0.61, 'NEWS': 0.2, 'X': 1}},
                   'why_news': {'choice': 'SIDEWAYS', 'confidence': 0.5},  # not an offered choice
                   'why_data': {'choice': 'CONFLICT', 'confidence': 1.4},  # out of range
                   'candidate': {'choice': 'INV', 'confidence': 0.5}})
    assert got == {'why_driver': {'choice': 'REGIME', 'confidence': 0.61, 'probabilities': {'REGIME': 0.61, 'NEWS': 0.2}}}
    assert C.parse(None) == {}


def test_jev_call_sends_session_context_and_records_evidence(monkeypatch):
    import sqlite3
    from unittest.mock import patch
    import nh_etf_auto_v2 as ai
    import research_store
    from etf_universe import SYMBOLS
    sent = []

    class Response:
        def __init__(self, data): self.data = data
        def __enter__(self): return self
        def __exit__(self, *a): return None
        def read(self, *a): return json.dumps(self.data).encode()

    def respond(req, timeout):
        payload = json.loads(req.data)
        sent.append(payload)
        return Response({'model': 'jev-1.14.0', 'answers': {
            'candidate': {'type': 'choice', 'choice': 'INV', 'confidence': 0.55},
            'why_driver': {'type': 'choice', 'choice': 'REGIME', 'confidence': 0.7},
            'why_data': {'type': 'choice', 'choice': 'CHART_SHORT', 'confidence': 0.4}}})
    now = datetime.now(KST)
    times = [f'{h:02d}{m:02d}' for h, m in (divmod(9 * 60 + 1 + i, 60) for i in range(12))]
    bars = {name: [{k: str(v) for k, v in bar(t, 100 + i).items()} for i, t in enumerate(times)][::-1] for name in SYMBOLS}
    news = [{'title': '시장', 'published': now.isoformat()}]
    with patch.object(ai.old, 'dotenv_values', return_value={'JEV_API_KEY': 'fake'}), \
            patch.object(ai.urllib.request, 'urlopen', side_effect=respond):
        result = ai.jev7(bars, news)
    state, questions = sent[0]['state'], sent[0]['questions']
    assert set(state['intraday_summary']) == set(SYMBOLS) and state['intraday_summary']['K200']['bars'] == 12
    assert {'candidate', 'why_driver', 'why_data'} <= set(questions)
    assert result['candidate']['choice'] == 'INV'  # the trading answer is unchanged by the evidence questions
    assert result['evidence'] == {'why_driver': {'choice': 'REGIME', 'confidence': 0.7}, 'why_data': {'choice': 'CHART_SHORT', 'confidence': 0.4}}
    with sqlite3.connect(research_store.ROOT / 'market-research.sqlite3') as db:
        (stored,) = db.execute('SELECT evidence FROM model_evidence WHERE digest=?', (result['input_digest'],)).fetchone()
    assert json.loads(stored)['why_driver']['choice'] == 'REGIME'


def test_explain_one_call_shows_answer_evidence_and_inputs(tmp_path):
    import sqlite3
    import jev_explain
    with sqlite3.connect(tmp_path / 'market-research.sqlite3') as db:
        db.execute('CREATE TABLE model_inputs (digest TEXT PRIMARY KEY, time TEXT, engine TEXT, question TEXT, inputs TEXT, response TEXT)')
        db.execute('CREATE TABLE model_evidence (digest TEXT PRIMARY KEY, time TEXT, engine TEXT, question TEXT, evidence TEXT)')
        state = {'recent_news_headlines': [{'title': '코스피 급등', 'published': '2026-11-18T01:51:00+00:00'}],
                 'intraday_summary': {'INV': {'range_position_pct': 94}}, 'market_regime_themes_global': {'as_of': '2026-11-18T11:16'}}
        db.execute('INSERT INTO model_inputs VALUES (?,?,?,?,?,?)', ('d', '2026-11-18T11:21:06+09:00', 'jev', 'candidate',
                   json.dumps({'state': state, 'instructions': 'x'}), json.dumps({'choice': 'INV', 'confidence': 0.46})))
        db.execute('INSERT INTO model_evidence VALUES (?,?,?,?,?)', ('d', 'x', 'jev', 'candidate', json.dumps({'why_driver': {'choice': 'CHART', 'confidence': 0.4}})))
    item = jev_explain.one(tmp_path, '2026-11-18T11:00')
    assert item['answer']['choice'] == 'INV' and item['evidence']['why_driver']['choice'] == 'CHART'
    assert item['session_summary'] == {'range_position_pct': 94} and item['headlines'] == [{'age_min': 30, 'title': '코스피 급등'}]
    assert jev_explain.one(tmp_path, '2026-11-18T12:00') is None


def test_previous_session_rows_are_charted_but_never_kept_as_today():
    """11-19 opening review: yesterday's 15:19 bar must not become today's 15:19 in the session history."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import jev_context as c
    now = datetime(2026, 11, 6, 9, 1, tzinfo=ZoneInfo('Asia/Seoul'))
    row = lambda d, t: {'bsop_date': d, 'bsop_time': t, 'stck_oprc': '1', 'stck_hgpr': '1', 'stck_lwpr': '1',
                        'stck_prpr': '1', 'vol': '1'}
    rows = [row('20261106', '090000'), row('20261105', '151900')]
    chart, _ = c.context({'K200': rows}, now=now, events='/nonexistent')
    assert chart['K200'] == rows
    assert list(c._DAY['bars']['K200']) == ['090000']


def test_rows_dated_today_still_get_the_session_chart():
    """vibe_fast_exit_watch._recorded_bars dates every row with today: the remainder review's chart fell to 10 bars."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import jev_context as c
    now = datetime(2026, 11, 6, 10, 30, tzinfo=ZoneInfo('Asia/Seoul'))
    row = lambda t: {'bsop_date': '20261106', 'bsop_time': t, 'stck_oprc': '1', 'stck_hgpr': '1', 'stck_lwpr': '1',
                     'stck_prpr': '1', 'vol': '1'}
    c.context({'K200': [row(f'10{m:02d}00') for m in range(29, -1, -1)]}, now=now, events='/nonexistent')
    chart, _ = c.context({'K200': [row('102900'), row('102800')]}, now=now, events='/nonexistent')
    assert len(chart['K200']) == 30


def test_nxt_premarket_rows_are_charted_but_never_kept_as_todays_bars():
    import jev_context as J
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime(2026, 11, 20, 9, 0, 20, tzinfo=ZoneInfo('Asia/Seoul'))
    row = {'bsop_date': '20261120', 'market': 'NXT', 'bsop_time': '084900', 'stck_oprc': '1', 'stck_hgpr': '1',
           'stck_lwpr': '1', 'stck_prpr': '1', 'vol': '1'}
    chart, _ = J.context({'005930': [row]}, now, events=None)
    assert chart['005930'] == [row] and '084900' not in J._DAY['bars'].get('005930', {})
