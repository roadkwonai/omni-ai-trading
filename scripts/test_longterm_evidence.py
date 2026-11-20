"""Why each long-term order was placed (user request 2026-11-18): one evidence bundle per order, every item marked as
used by the order calculation or reference only."""
import json

import longterm_evidence as E
import vibe_longterm as L
from test_vibe_longterm import DAY, Read, dca_switch, decided, krx_calendar  # noqa: F401

PARTS = ('reason', 'regime', 'masters', 'macro', 'story', 'news', 'trading', 'research')


def evidence(root, **where):
    db = L.connect(root)
    E.ensure(db)
    db.row_factory = __import__('sqlite3').Row
    sql = 'SELECT * FROM evidence' + (' WHERE ' + ' AND '.join(f'{k}=?' for k in where) if where else '') + ' ORDER BY rowid'
    return [dict(r) for r in db.execute(sql, tuple(where.values()))]


def test_every_planned_order_gets_a_bundle_with_eight_parts_marked_used_or_reference(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=9, minute=40)
    assert L.trade(tmp_path, Read(), now) == 'planned'
    sent = L.read_state(tmp_path)['last_plan']['orders']
    rows = evidence(tmp_path)
    assert len(rows) == len(sent) and {r['kind'] for r in rows} == {'plan'} and not any(r['reconstructed'] for r in rows)
    bundle = json.loads(rows[0]['bundle'])
    assert tuple(bundle['parts']) == PARTS
    assert all(isinstance(p['used'], bool) and p['title'] for p in bundle['parts'].values())
    assert bundle['parts']['reason']['used'] and not bundle['parts']['macro']['used'] and not bundle['parts']['research']['used']
    assert '초기 투입' in rows[0]['summary'] and rows[0]['why'] == 'initial_deployment'


def test_the_master_steps_add_up_to_the_final_target_and_name_the_masters_that_were_off(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=9, minute=40)
    L.trade(tmp_path, Read(), now)
    want = L.read_state(tmp_path)['last_plan']['want']
    for row in evidence(tmp_path):
        masters = json.loads(row['bundle'])['parts']['masters']
        on = [s for s in masters['steps'] if s['active']]
        assert on[0]['before'] == masters['base'] and abs(on[-1]['after'] - want[row['key']]) < 1e-6
        assert all(a['after'] == b['before'] for a, b in zip(on, on[1:]))
        off = [s for s in masters['steps'] if not s['active']]
        assert off and all('꺼짐' in s['note'] for s in off)


def test_a_satellite_story_counts_only_when_lynch_is_on_and_news_says_when_none_came_in(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=9, minute=40)
    L.trade(tmp_path, Read(), now)
    sat = next(r for r in evidence(tmp_path) if r['key'] in L.SATS)
    parts = json.loads(sat['bundle'])['parts']
    assert parts['story']['used'] == ('lynch_story' in L.read_state(tmp_path)['plan']['active'])
    assert parts['news']['used'] is False and '뉴스 입력 없음' in parts['news']['text']  # conftest: no news reached the engine


def test_an_executed_order_links_its_number_and_fill_and_a_dca_order_names_the_profile_rule(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=10)
    L.save_settings(tmp_path, {'execute': True})
    dca_switch(tmp_path, style='master', profile='dalio')
    assert L.trade(tmp_path, Read(), now) == 'traded'
    assert L.dca(tmp_path, Read(), now) == 'dca_traded'
    order = evidence(tmp_path, kind='order')[0]
    assert order['number'] and order['filled'] == order['qty'] and order['fill_price']
    dca = evidence(tmp_path, kind='dca')
    assert dca and '레이 달리오' in json.loads(dca[0]['bundle'])['parts']['reason']['text']


def test_todays_fills_can_be_rebuilt_from_the_records_and_say_so(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=10)
    L.save_settings(tmp_path, {'execute': True})
    L.trade(tmp_path, Read(), now)
    db = L.connect(tmp_path)
    E.ensure(db)
    with db:
        db.execute('DELETE FROM evidence')
    assert E.reconstruct(tmp_path, DAY.isoformat()) > 0
    rows = evidence(tmp_path)
    assert rows and all(r['reconstructed'] for r in rows) and '재구성' in json.loads(rows[0]['bundle'])['note']
    assert E.reconstruct(tmp_path, DAY.isoformat()) == 0  # once


def test_an_order_that_did_not_fill_says_so_in_its_summary():
    assert E.unfilled({'number': 9, 'filled': 0}) == ' (미체결 취소)'
    assert E.unfilled({'number': 9, 'filled': 2}) == '' and E.unfilled({'filled': 0}) == ''  # a dry plan has no number


def test_the_news_part_lists_the_headlines_the_engine_received_with_links(tmp_path, monkeypatch):
    from datetime import datetime
    from test_vibe_longterm import KST, fake_fetch, ANSWER
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    now = datetime.combine(DAY, datetime.min.time(), KST).replace(hour=8, minute=45)
    news = {'as_of': now.isoformat(), 'rows': [{'title': '연준 금리 동결', 'publisher': '한경', 'published': now.isoformat(), 'topic': 'fed',
                                                'link': 'https://news.example/1', 'source': 'google_news_rss'}], 'error': None}
    L.decide(tmp_path, now, fetch=fake_fetch, ask=lambda text: ANSWER, news=lambda at: news)
    L.trade(tmp_path, Read(), now.replace(hour=9, minute=40))
    part = json.loads(evidence(tmp_path)[0]['bundle'])['parts']['news']
    assert part['used'] is False and '1건' in part['text'] and any('https://news.example/1' in i for i in part['items'])


def test_the_research_part_finds_the_level_map_of_the_orders_etf(tmp_path, monkeypatch):
    """Review 2026-11-18: levels key the ETF as 'asset', so every bundle said '타점 지도 없음'."""
    now = decided(tmp_path, monkeypatch).replace(hour=9, minute=40)
    st = L.read_state(tmp_path)
    level = {'asset': 'NDX', 'horizon': '장기', 'master': 'yeo_reservoir', 'side': 'buy', 'price': 120.0, 'condition': '고점 −20%',
             'kind': 'rule', 'active': True}
    L.put_state(L.connect(tmp_path), plan={**st['plan'], 'levels': [level]})
    assert L.trade(tmp_path, Read(), now) == 'planned'
    ndx = json.loads(evidence(tmp_path, key='NDX')[0]['bundle'])
    research = ndx['parts']['research']
    assert '타점 지도 1개' in research['text'] and any('고점 −20%' in i for i in research['items'])


def test_a_tuned_dca_names_the_tune_in_its_record_and_evidence(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=10)
    L.save_settings(tmp_path, {'execute': True})
    dca_switch(tmp_path, style='master', profile='dalio', tune={'wait': 3})
    assert L.dca(tmp_path, Read(), now) == 'dca_traded'
    row = L.connect(tmp_path).execute("SELECT indicators, reason FROM decisions WHERE kind='dca'").fetchone()
    assert json.loads(row[0])['tune'] == {'wait': 3} and '내 조정: 하락일 최대 3일' in row[1]
    assert '내 조정: 하락일 최대 3일' in json.loads(evidence(tmp_path, kind='dca')[0]['bundle'])['parts']['reason']['text']


def test_a_reservoir_order_is_named_by_the_etf_the_book_holds(tmp_path, monkeypatch):
    # 2026-11-20: the virtual book bought CD금리(357870) and the summary said 'TIGER KOFR금리액티브(합성) 매수 82주'
    now = decided(tmp_path, monkeypatch).replace(hour=9, minute=40)
    L.save_settings(tmp_path, {'books': {'actual': {'reservoir': '357870'}}})
    assert L.trade(tmp_path, Read(), now) == 'planned'
    res = [r for r in evidence(tmp_path) if r['code'] == '357870']
    assert res and all(L.RESERVOIRS['357870'] in r['summary'] and 'KOFR' not in r['summary'] for r in res)
