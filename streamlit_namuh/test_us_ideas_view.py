import json
from datetime import date, timedelta

import us_ideas as U
import us_ideas_view as V


def test_etf_text_names_our_krx_etfs():
    assert V.etf_text({'QQQM': 2.5, 'IWO': 0.1}) == 'KODEX 미국나스닥100 2.50% · 러셀2000 성장 0.10%'
    assert V.etf_text({}) == '—'


def test_tab_renders_empty_and_with_picks(tmp_path):
    from streamlit.testing.v1 import AppTest

    def view(root):
        import us_ideas_view
        us_ideas_view.render(root)
    at = AppTest.from_function(view, args=(tmp_path,)).run()
    assert not at.exception and any('주간 스크린 결과가 없습니다' in i.value for i in at.info)
    db = U.connect(tmp_path)
    day = date.today().isoformat()
    with db:
        db.execute('INSERT INTO picks VALUES (?,?,?,?,?,?,?,?,?,?,?)', (day, 'tenbagger', 1, 'AAA', 'Alpha', 10.0, day, 1.2,
                   '{}', json.dumps(['매출 성장 40%']), json.dumps({'IWO': 0.1})))
        db.execute('INSERT INTO outcomes VALUES (?,?,?,?,?,?,?)', ((date.today() - timedelta(days=40)).isoformat(), 'tenbagger', 'AAA', 1, 0.1, 0.02, 0.01))
        db.execute('INSERT INTO runs VALUES (?,?,?,?)', (day, 'weekly', day, json.dumps({'prefiltered': 3, 'passed': {'tenbagger': 1}})))
    at = AppTest.from_function(view, args=(tmp_path,)).run()
    assert not at.exception and not at.info and len(at.dataframe) >= 2  # ten-baggers and the scorecard


def test_a_picked_name_opens_its_chart(tmp_path):
    # user request 2026-11-18: each idea can be opened as a chart right away (reference only)
    from streamlit.testing.v1 import AppTest
    db = U.connect(tmp_path)
    day = date.today().isoformat()
    with db:
        db.execute('INSERT INTO picks VALUES (?,?,?,?,?,?,?,?,?,?,?)', (day, 'fisher', 1, 'NVDA', 'NVIDIA', 150.0, day, 1.2,
                   '{}', json.dumps(['매출 3년 연평균 110%']), json.dumps({'QQQM': 8.0})))

    def view(root):
        import streamlit as st
        import us_ideas_view
        import vibe_longterm_view as V
        V.yahoo_bars = lambda symbol: [{'time': 1_700_000_000 + i * 86_400, 'open': 100 + i, 'high': 101 + i, 'low': 99 + i,
                                        'close': 100 + i, 'volume': 1} for i in range(300)]

        def chart(bars, markers, basis, **kw):
            st.session_state['idea_chart'] = {'bars': len(bars), 'averages': sorted(kw['averages']), 'levels': kw['levels'],
                                              'volume_pane': kw.get('volume_pane')}
        us_ideas_view.render(root, chart)
    at = AppTest.from_function(view, args=(tmp_path,)).run()
    assert not at.exception and 'idea_chart' not in at.session_state  # nothing picked: no chart, no Yahoo call
    at.selectbox(key='idea_chart_ticker').set_value('NVDA').run()
    call = at.session_state['idea_chart']
    assert not at.exception and call['bars'] == 300 and call['averages'] == [5, 20, 60, 120]
    assert call['volume_pane'] is True  # volume in its own pane with its own axis
    assert any('추천일 종가' in x['label'] for x in call['levels'])
    assert any('참고 정보 · 주문 없음' in c.value and '매출 3년 연평균 110%' in c.value for c in at.caption)
    at.segmented_control(key='idea_chart_unit').set_value('주').run()
    assert at.session_state['idea_chart']['averages'] == [5, 20, 60, 120]  # the same counts on weekly bars
    at.multiselect(key='idea_chart_ma').set_value([3, 5, 20, 60, 120]).run()
    assert at.session_state['idea_chart']['averages'] == [3, 5, 20, 60, 120]


def test_names_are_picked_by_master_and_found_by_search(tmp_path):
    # user request 2026-11-18: choose a master first (its picks or its 13F holdings), and search by ticker or company name
    from streamlit.testing.v1 import AppTest
    db = U.connect(tmp_path)
    day = date.today().isoformat()
    with db:
        for master, ticker, name in (('buffett', 'AMP', 'Ameriprise Financial'), ('fisher', 'NVDA', 'NVIDIA'), ('tenbagger', 'HIMS', 'Hims & Hers')):
            db.execute('INSERT INTO picks VALUES (?,?,?,?,?,?,?,?,?,?,?)', (day, master, 1, ticker, name, 10.0, day, 1.0, '{}', '[]', '{}'))
        db.executemany('INSERT INTO universe VALUES (?,?,?,?,?,?)', [(day, 'AAPL', '1', 'Apple Inc.', 'IVV', 6.0),
                                                                      (day, 'AMP', '2', 'Ameriprise Financial, Inc.', 'IVV', 0.1)])
        db.execute('INSERT INTO managers13f VALUES (?,?,?)', ('2026Q3', 'Berkshire Hathaway', json.dumps(
            {'master': '워런 버핏', 'firm': '버크셔 해서웨이', 'positions': [{'issuer': 'APPLE INC'}, {'issuer': 'NOT LISTED CORP'}]})))

    def view(root):
        import us_ideas_view
        import vibe_longterm_view as V
        V.yahoo_bars = lambda symbol: []
        us_ideas_view.render(root, lambda *a, **k: None)
    at = AppTest.from_function(view, args=(tmp_path,)).run()
    names = lambda: [o.split(' · ')[0] for o in at.selectbox(key='idea_chart_ticker').options]
    assert not at.exception and names() == ['선택 안 함', 'AMP', 'NVDA', 'HIMS', 'AAPL']  # every master, picks first
    at.selectbox(key='idea_chart_group').set_value('fisher').run()
    assert names() == ['선택 안 함', 'NVDA']
    at.selectbox(key='idea_chart_group').set_value('13f:Berkshire Hathaway').run()
    assert names() == ['선택 안 함', 'AAPL']  # a 13F holding is found by its company name in the universe
    at.selectbox(key='idea_chart_group').set_value('all').run()
    at.text_input(key='idea_chart_query').input('hims').run()
    assert names() == ['선택 안 함', 'HIMS']  # case-insensitive, ticker or company name
    at.text_input(key='idea_chart_query').input('ameri').run()
    assert names() == ['선택 안 함', 'AMP']


def seed_detail(root, thesis=True):
    from test_us_ideas import facts
    db = U.connect(root)
    day = date.today().isoformat()
    with db:
        db.execute('INSERT INTO picks VALUES (?,?,?,?,?,?,?,?,?,?,?)', (day, 'tenbagger', 1, 'AAA', 'Alpha Corp', 10.0, day, 1.0, '{}',
                   json.dumps(['매출 성장 40%']), json.dumps({'IWO': 0.1})))
        db.execute('INSERT INTO universe VALUES (?,?,?,?,?,?)', (day, 'AAA', '0000000123', 'Alpha Corp', 'IWO', 0.1))
        db.execute('INSERT INTO facts VALUES (?,?,?)', (day, 'AAA', json.dumps(facts())))
        db.execute('INSERT INTO managers13f VALUES (?,?,?)', ('2026Q3', 'Berkshire Hathaway', json.dumps(
            {'master': '워런 버핏', 'firm': '버크셔 해서웨이', 'positions': [{'issuer': 'ALPHA CORP', 'shares': 1000.0, 'value_usd': 5e6, 'weight_pct': 1.5}],
             'changes': [{'action': 'increased', 'issuer': 'ALPHA CORP', 'shares_change_pct': 12.5}]})))
        if thesis:
            db.execute('INSERT INTO theses VALUES (?,?,?,?)', ('2026-W47', 'AAA', 'tenbagger', json.dumps(
                {'ticker': 'AAA', 'master': 'tenbagger', 'lenses': ['lynch-garp', 'chanos-forensic-short'], 'verdict': '관찰',
                 'thesis': {'core': '구독 매출이 빠르게 는다.', 'falsifiers': ['성장 둔화', '희석', '경쟁'], 'redline': '2분기 연속 매출 감소',
                            'recheck': '2027-02-20'},
                 'filings': [{'form': '10-Q', 'date': '2026-11-05', 'url': 'https://www.sec.gov/x.htm'}]}, ensure_ascii=False)))
    reports = root / 'reports'
    reports.mkdir(exist_ok=True)
    (reports / 'strategy-2026-W47.md').write_text('# 장기투자 주간 전략 리포트 · 2026-W47\n\n## 5. 미국 종목 아이디어\n\n| AAA | 텐베거 |\n\n## 6. 타점 지도\n\nx',
                                                  encoding='utf-8')


def detail_page(root):
    import streamlit as st
    import us_ideas_view as UV
    st.session_state.setdefault('idea_chart_ticker', 'AAA')
    UV.render(root, lambda *a, **k: None)


def fake_sources(monkeypatch, news_ok=True):
    """Yahoo bars, SEC filings and news for the detail tests, restored after each test (no leak into the next one)."""
    import us_ideas_view as UV
    import vibe_longterm_view as V
    monkeypatch.setattr(V, 'yahoo_bars', lambda symbol: [{'time': 1_700_000_000 + i * 86_400, 'open': 1e8, 'high': 1e8, 'low': 1e8,
                                                          'close': 1e8, 'volume': 1} for i in range(300)])
    monkeypatch.setattr(UV, 'sec_filings', lambda cik: {'sic': 'Services-Prepackaged Software', 'filings': [
        {'form': '10-Q', 'date': '2026-11-05', 'url': 'https://www.sec.gov/Archives/edgar/data/123/x/q.htm'}]})

    def broken(t, name=''):
        raise OSError('offline')
    monkeypatch.setattr(UV, 'stock_news', (lambda t, name='': [{'title': 'Alpha beats estimates', 'url': 'https://news.example/a',
                                                                 'source': 'Reuters', 'published': '2026-11-17 21:00'}]) if news_ok else broken)


def test_a_picked_name_shows_info_news_and_the_masters_thinking(tmp_path, monkeypatch):
    # user request 2026-11-18: click a master's name to see its news, its facts and the masters' reports (reference only)
    from streamlit.testing.v1 import AppTest
    seed_detail(tmp_path)
    fake_sources(monkeypatch)
    at = AppTest.from_function(detail_page, args=(tmp_path,)).run()
    assert not at.exception
    text = ' '.join([m.value for m in at.markdown] + [c.value for c in at.caption])
    info = next(df.value for df in at.dataframe if '지표' in df.value.columns)
    assert '시가총액' in set(info['지표']) and info['기준'].str.contains('근사').any() and info['출처'].str.contains('SEC').any()
    assert 'Services-Prepackaged Software' in text and '[10-Q 2026-11-05](https://www.sec.gov/' in text
    holders = next(df.value for df in at.dataframe if '보유 주식' in df.value.columns)
    assert holders.iloc[0]['거장'].startswith('워런 버핏') and '+12.5%' in holders.iloc[0]['분기 변화']
    assert 'Alpha beats estimates' in text and 'https://news.example/a' in text and '지시가 아닙니다' in text
    assert '구독 매출이 빠르게 는다.' in text and '2분기 연속 매출 감소' in text and '2026-W47' in text
    assert any('2026-W47' in e.label for e in at.expander)  # the weekly report that names it
    assert '참고 정보 · 주문 없음' in text and '목표가' not in text


def test_without_a_card_the_screen_explains_itself_and_a_news_failure_keeps_the_page(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    seed_detail(tmp_path, thesis=False)
    fake_sources(monkeypatch, news_ok=False)
    at = AppTest.from_function(detail_page, args=(tmp_path,)).run()
    assert not at.exception
    text = ' '.join([m.value for m in at.markdown] + [c.value for c in at.caption] + [i.value for i in at.info])
    assert '주간 렌즈 리포트 대기 중' in text and '뉴스를 불러오지 못했습니다' in text
    crit = next(df.value for df in at.dataframe if '기준' in df.value.columns and '통과' in df.value.columns)
    assert '시가총액 3억~100억 달러' in set(crit['기준']) and set(crit['통과']) <= {'✅ 통과', '❌ 미달'}


def test_news_keeps_only_articles_about_the_name(monkeypatch):
    import us_ideas_view as UV
    from backtest.loaders import yahoo_client
    items = [{'title': 'Apple Pay outage', 'link': 'u1', 'publisher': 'A', 'providerPublishTime': 1_763_400_000},
             {'title': 'Tesla bulls cheer', 'link': 'u2', 'publisher': 'B', 'providerPublishTime': 1_763_400_000},
             {'title': 'Big tech rally', 'link': 'u3', 'publisher': 'C', 'providerPublishTime': 1_763_400_000, 'relatedTickers': ['AAPL', 'MSFT']},
             {'title': 'Apple supplier note', 'link': 'u4', 'publisher': 'D', 'providerPublishTime': 1_763_400_000, 'relatedTickers': ['TSM']},
             {'title': 'Pineapple prices jump', 'link': 'u5', 'publisher': 'E', 'providerPublishTime': 1_763_400_000},
             {'title': 'Tesla bulls cheer again', 'link': 'u6', 'publisher': 'F', 'providerPublishTime': 1_763_400_000,
              'relatedTickers': ['TSLA', '^GSPC', 'AAPL']}]  # Yahoo tags mega-caps on market stories: not the subject
    monkeypatch.setattr(yahoo_client, 'search_news', lambda q, n: items)
    got = UV.stock_news.__wrapped__('AAPL', 'Apple Inc.')
    assert [n['url'] for n in got] == ['u1', 'u3', 'u4']  # named in the title (a whole word), or the ticker is the first tag


THIRTEEN_F = {'master': '워런 버핏', 'firm': '버크셔 해서웨이', 'positions': [
    {'issuer': 'LENNAR CORP', 'cusip': '526057104', 'title_of_class': 'CL A', 'put_call': None, 'weight_pct': 0.7},
    {'issuer': 'ISHARES TR', 'cusip': '464287200', 'title_of_class': 'CORE S&P500 ETF', 'put_call': None, 'weight_pct': 9.2},
    {'issuer': 'APPLE INC', 'cusip': '037833100', 'title_of_class': 'COM', 'put_call': None, 'weight_pct': 25.0}],
    'changes': [
        {'action': 'reduced', 'issuer': 'APPLE INC', 'cusip': '037833100', 'put_call': None, 'shares_change_pct': -5.0, 'value_usd_after': 7e10, 'value_usd_change': -3e9},
        {'action': 'increased', 'issuer': 'LENNAR CORP', 'cusip': '526057104', 'put_call': None, 'shares_change_pct': 98.6, 'value_usd_after': 2.1e9, 'value_usd_change': 1e9},
        {'action': 'closed', 'issuer': 'NUCOR CORP', 'cusip': '670346105', 'put_call': None, 'shares_change_pct': -100.0, 'value_usd_after': 0, 'value_usd_change': -4e8},
        {'action': 'increased', 'issuer': 'LENNAR CORP', 'cusip': '526057302', 'put_call': None, 'shares_change_pct': 85.5, 'value_usd_after': 4e7, 'value_usd_change': 2e7},
        {'action': 'new', 'issuer': 'STATE STREET CORP', 'cusip': '78462F103', 'put_call': 'Put', 'shares_change_pct': 0, 'value_usd_after': 1e9, 'value_usd_change': 1e9},
        {'action': 'increased', 'issuer': 'ISHARES TR', 'cusip': '464287200', 'put_call': None, 'shares_change_pct': 12.1, 'value_usd_after': 2.2e9, 'value_usd_change': 2e8},
        {'action': 'reduced', 'issuer': 'KROGER CO', 'cusip': '501044101', 'put_call': None, 'shares_change_pct': -22.0, 'value_usd_after': 2e9, 'value_usd_change': -5e8}]}


def test_13f_changes_are_told_apart_ordered_and_named_clearly():
    # user request 2026-11-18: 증가·감소·신규·청산 must stand out; share classes, ETF products and options named
    import us_ideas_view as UV
    rows = UV.change_rows(THIRTEEN_F, {'AAPL': 'Apple Inc.', 'LEN': 'Lennar Corp'})
    assert [r['action'] for r in rows] == ['new', 'increased', 'increased', 'increased', 'reduced', 'reduced', 'closed']
    assert [r['종목'] for r in rows[1:4]] == ['LENNAR CORP · CL A', 'LENNAR CORP · CUSIP 526057302', 'ISHARES TR · CORE S&P500 ETF']
    assert rows[0]['종목'] == 'STATE STREET CORP · 풋 옵션' and rows[0]['변화'] == 'NEW 신규'
    assert rows[0]['▲ 증가 크기'] is None and rows[0]['▼ 감소 크기'] is None
    assert [r['변화'] for r in rows[4:]] == ['▼ 감소', '▼ 감소', '청산'] and rows[4]['종목'] == 'KROGER CO'  # bigger cut first
    assert rows[1]['변화'] == '▲ 증가' and rows[1]['비중(%)'] == 0.7 and rows[1]['▲ 증가 크기'] == 98.6 and rows[1]['▼ 감소 크기'] is None
    assert rows[4]['▼ 감소 크기'] == 22.0 and rows[6]['▼ 감소 크기'] == 100.0  # red bars up, blue bars down
    assert rows[5]['티커'] == 'AAPL' and rows[1]['티커'] == 'LEN' and rows[6]['티커'] == ''  # Nucor is outside the universe
    by_value = UV.change_rows(THIRTEEN_F, {}, sort='value')
    assert by_value[0]['종목'] == 'APPLE INC'


def test_13f_tab_filters_by_change_with_counts(tmp_path):
    from streamlit.testing.v1 import AppTest
    db = U.connect(tmp_path)
    day = date.today().isoformat()
    with db:
        db.execute('INSERT INTO picks VALUES (?,?,?,?,?,?,?,?,?,?,?)', (day, 'fisher', 1, 'NVDA', 'NVIDIA', 1.0, day, 1.0, '{}', '[]', '{}'))
        db.execute('INSERT INTO managers13f VALUES (?,?,?)', ('2026Q3', 'Berkshire Hathaway', json.dumps(THIRTEEN_F)))

    def view(root):
        import us_ideas_view
        us_ideas_view.render(root, lambda *a, **k: None)
    at = AppTest.from_function(view, args=(tmp_path,)).run()
    assert not at.exception
    table = lambda: next(df.value for df in at.dataframe if '▲ 증가 크기' in df.value.columns)
    assert len(table()) == 7
    at.session_state['13f_filter_Berkshire Hathaway'] = 'increased'
    at.run()
    assert len(table()) == 3 and set(table()['변화']) == {'▲ 증가'}
    at.session_state['13f_filter_Berkshire Hathaway'] = 'closed'
    at.run()
    assert list(table()['종목']) == ['NUCOR CORP']


def test_13f_table_shows_blanks_not_none():
    import pandas as pd
    import us_ideas_view as UV
    frame = UV.change_frame(UV.change_rows(THIRTEEN_F, {}))
    assert pd.isna(frame.loc[0, '주식 수 변화(%)']) and frame['주식 수 변화(%)'].dtype.kind == 'f'
    assert frame.loc[0, '주식 수 변화'] == '—' and frame.loc[0, '비중'] == '—' and frame.loc[1, '비중'] == '0.70%'


def test_13f_styled_table_writes_a_dash_for_a_new_line():
    import us_ideas_view as UV
    html = UV.change_styler(UV.change_frame(UV.change_rows(THIRTEEN_F, {}))).to_html()
    assert '—' in html and 'None' not in html and '+98.6%' in html and '−22.0%' in html and '#ef4444' in html and '#3b82f6' in html


def test_each_pick_shows_the_domestic_etf_it_is_exposed_through():
    # user approval 2026-11-18 (docs/longterm-account-settings-plan.md 3-A): the US stock stays reference only
    picks = [{'rank': 1, 'master': 'buffett', 'ticker': 'AMP', 'name': 'Ameriprise', 'reasons': ['r'], 'etfs': {'IVV': 0.31, 'IWO': 0.1},
              'price': 500.0, 'price_date': '2026-11-13'}]
    assert V.rows(picks, 'buffett')[0]['국내 상장 ETF로 노출'] == 'KODEX 미국S&P500'
    assert V.rows([{**picks[0], 'etfs': {'IWO': 0.1}}], 'buffett')[0]['국내 상장 ETF로 노출'] == '없음'
