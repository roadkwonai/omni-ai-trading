import json
from datetime import datetime

from streamlit.testing.v1 import AppTest

from vibe_longterm_view import goto, yahoo_bars  # bound at collection: test_us_ideas_view rebinds V.yahoo_bars


def page(root):
    import vibe_longterm_view
    vibe_longterm_view.render(root)


def seed(root):
    import vibe_longterm as L
    db = L.connect(root)
    ind = {'as_of': '2026-11-17', 'dd': -0.13, 'gap': -0.03, 'slope': 0.01, 'vix': 22.9, 'usdkrw': 1337, 'season_month_mean': 0.02, 'sats': {}}
    L.put_state(db, regime={'regime': 'correction', 'since': '2026-11-18', 'pending': 'bear', 'pending_days': 3},
                plan={'day': '2026-11-18', 'time': '2026-11-18T08:45:00+09:00', 'regime': 'correction', 'raw': 'bear', 'indicators': ind,
                      'why': '조정', 'recommended': ['bogle_core', 'yeo_reservoir'], 'active': ['bogle_core'],
                      'impacts': {'yeo_reservoir': [], 'lynch_story': ['양자 스토리 주의 → 절반']}, 'tuning': L.DEFAULT_SETTINGS['tuning']},
                story={'time': datetime.now().isoformat(), 'market_view': '보', 'source': 'llm',
                       'stories': {'QUANT': {'status': 'watch', 'tilt': -1, 'reason': '희석'}}},
                last_plan={'day': '2026-11-18', 'want': {k: a[3] for k, a in L.ASSETS.items()}, 'trade_to': None, 'weights': {},
                           'why': 'initial_deployment', 'notes': [['lynch_story', '양자 절반']], 'nav': 1e8,
                           'orders': [{'key': 'NDX', 'side': 'BUY', 'qty': 1, 'price': 10}], 'execute': False})
    L.log(db, 'regime', '2026-11-18', regime='correction', raw='bear', mode='manual', modules=['bogle_core'], reason='조정')
    L.log(db, 'story', '2026-11-18', regime='correction', indicators={'QUANT': {'status': 'watch', 'tilt': -1, 'reason': '희석'}}, reason='s')
    L.log(db, 'plan', '2026-11-18', regime='correction', mode='manual', modules=['bogle_core'],
          orders=[{'key': 'NDX', 'side': 'BUY', 'qty': 1, 'price': 10}], reason='p')
    with db:
        db.executemany("INSERT INTO nav VALUES (?,?,?)", [('2026-11-18', 'base', 99_000_000), ('2026-11-18', 'actual', 100_000_000),
                                                          ('2026-11-18', 'auto', 99_500_000)])


def open_tab(root, tab, **state):
    at = AppTest.from_function(page, args=(root,))
    goto(at.session_state, tab)  # an old tab name opens its new tab and sub-view (S4)
    for key, value in state.items():
        at.session_state[key] = value
    return at.run()


def cards_text(at):
    return ' '.join(m.value for m in at.markdown)


def tables(at):
    return [df.value for df in at.dataframe]


def test_renders_empty_and_with_records(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    at = AppTest.from_function(page, args=(tmp_path,)).run()
    assert not at.exception
    assert any('08:45' in m.value for m in at.info)  # empty state says what comes next
    seed(tmp_path)
    at = AppTest.from_function(page, args=(tmp_path,)).run()
    assert not at.exception
    assert any('조정 국면' in m.value for m in at.info)
    text = ' '.join(m.value for m in at.markdown)
    assert '100,000,000원' in text and '99,000,000원' in text and '99,500,000원' in text  # each book's NAV card
    assert '-1.00%' in text and '+1.01%p' in text  # base against 1억, actual against base
    assert '**계획(주문 없음)**' in text and '**스토리**' in text and '**국면**' in text  # the latest decisions by kind
    nav = next(df.value for df in at.dataframe if '실제(가상 계좌)' in df.value.columns)
    assert nav.loc['2026-11-18', '기본 비교 전략(기준 비중+달리오)'] == 99_000_000


def test_execute_switch_needs_the_phrase_and_records_history(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    import vibe_longterm as L
    seed(tmp_path)
    at = open_tab(tmp_path, '운용 설정')
    at.toggle(key='lt_execute').set_value(True).run()
    assert at.button(key='lt_save_execute').disabled
    at.text_input(key='lt_execute_phrase').input('장기투자 가상 체결').run()
    at.button(key='lt_save_execute').click().run()
    assert not at.exception and L.load_settings(tmp_path)['execute'] is True
    goto(at.session_state, '전략 설정')
    at.run()
    at.toggle(key='lt_mod_yeo_reservoir').set_value(True).run()
    at.button(key='lt_save_modules').click().run()
    assert L.load_settings(tmp_path)['modules']['yeo_reservoir'] is True


def test_coming_back_to_auto_asks_for_the_phrase_again(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    import vibe_longterm as L
    seed(tmp_path)
    L.save_settings(tmp_path, {**L.load_settings(tmp_path), 'mode': 'auto', 'auto_live': True})
    L.save_settings(tmp_path, {'mode': 'manual'})
    at = open_tab(tmp_path, '전략 설정')
    at.segmented_control(key='lt_mode').set_value('auto').run()
    assert at.toggle(key='lt_auto_live').value is False  # the live switch did not survive leaving auto
    at.toggle(key='lt_auto_live').set_value(True).run()
    assert at.button(key='lt_save_mode').disabled  # the phrase is asked again
    assert not at.exception


def chart_page(root):
    import vibe_longterm_view as V
    calls = []
    V.daily_bars = lambda code: [{'time': 1_700_000_000 + i * 86_400, 'open': 100 + i, 'high': 101 + i, 'low': 99 + i, 'close': 100 + i,
                                  'volume': 1} for i in range(300)]
    def chart(bars, markers, basis, **kw):
        import streamlit as st
        st.session_state['chart_call'] = {'bars': len(bars), 'levels': kw['levels'], 'averages': sorted(kw['averages']), 'markers': markers}
    V.render(root, chart=chart)


def test_chart_draws_the_level_map_and_the_report_tab_shows_a_written_report(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    import vibe_longterm as L
    seed(tmp_path)
    st_ = L.read_state(tmp_path)
    level = {'asset': 'NDX', 'horizon': '장기', 'master': 'yeo_reservoir', 'side': 'buy', 'price': 120.0,
             'size': {'weight': 0.0375, 'krw': 3_750_000}, 'condition': 'c', 'kind': 'rule', 'case': 'dd20', 'active': True,
             'distance': -0.2, 'status': 'waiting'}
    L.put_state(L.connect(tmp_path), plan={**st_['plan'], 'levels': [level]})
    at = AppTest.from_function(chart_page, args=(tmp_path,))
    goto(at.session_state, '차트')
    at.run()
    assert not at.exception
    call = at.session_state['chart_call']
    assert call['bars'] == 300 and call['averages'] == [20, 50, 200]
    assert any('저수지 분할(일반)' in line['label'] and line['price'] == 120.0 for line in call['levels'])
    assert any('고점' in line['label'] for line in call['levels'])
    reports = tmp_path / 'reports'
    reports.mkdir()
    (reports / 'strategy-2026-11-18.md').write_text('# 장기투자 일일 전략 리포트 · 2026-11-18\n\n본문', encoding='utf-8')
    (reports / 'strategy-2026-11-18.json').write_text(json.dumps({'summary': '오늘은 관망', 'scenarios': [
        {'name': '급락', 'trigger': '나스닥 −3%', 'action': '저수지 5%'}]}, ensure_ascii=False), encoding='utf-8')
    goto(at.session_state, '전략 리포트')
    at.run()
    assert not at.exception and any('오늘은 관망' in m.value for m in at.info)
    import longterm_report as R
    views = {k: {'view': '원칙으로 본 시장', 'action': '기다리기', 'vs_engine': '다름: 엔진은 산다'} for k in R.MASTER_VIEWS}
    (reports / 'strategy-2026-11-18.json').write_text(json.dumps({'summary': '오늘은 관망', 'master_views': views}, ensure_ascii=False), encoding='utf-8')
    at.run()
    assert not at.exception and any('거장별 관점 의견' in m.value for m in at.markdown)
    assert all(any(f"**{m['name']}**" == x.value for x in at.markdown) for m in R.MASTER_VIEWS.values())


def test_market_tab_shows_the_macro_background_with_its_basis(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    import vibe_longterm as L
    seed(tmp_path)
    db = L.connect(tmp_path)
    plan = L.get_state(db)['plan']
    plan['macro'] = {'as_of': '2026-11-18', 'source': 'FRED · 연준', 'errors': ['DFII10: TimeoutError'],
                     'series': {'BAMLH0A0HYM2': {'label': '하이일드 스프레드', 'unit': '%p', 'date': '2026-11-16', 'value': 3.23,
                                                 'chg_1m': 0.0, 'low_1y': 2.6, 'high_1y': 3.48, 'stale': False}},
                     'events': [{'date': '2026-12-09', 'label': 'FOMC'}]}
    L.put_state(db, plan=plan)
    at = open_tab(tmp_path, '시황·국면')
    assert not at.exception
    text = ' '.join(c.value for c in at.caption) + ' '.join(m.value for m in at.markdown)
    assert '하이일드 스프레드' in text and '3.23%p' in text
    assert '다가오는 미국 발표(30일): FOMC 12-09 · 출처 FRED · 연준 · 빠진 자료: DFII10: TimeoutError' in text


def test_freshness_cards_flag_a_missing_decision_after_nine(tmp_path):
    import vibe_longterm as L
    import vibe_longterm_view as V
    wed = lambda hm: datetime.fromisoformat(f'2026-11-18T{hm}:00+09:00')  # noqa: E731
    L.put_state(L.connect(tmp_path), plan={'day': '2026-11-17', 'time': '2026-11-17T08:45:00+09:00', 'macro': {'errors': ['DFII10: X']}},
                nav_latest={'day': '2026-11-17', 'actual': 100_500_000})
    cards = {c['label']: c for c in V.freshness_cards(tmp_path, '종료', wed('09:30'))}
    assert cards['마지막 판단']['tone'] == 'alert' and cards['마지막 판단']['sub'] == '오늘 판단 없음'
    assert cards['마지막 순자산']['value'] == '1.0050억' and cards['거시 자료(FRED·연준)']['value'] == '빠짐 1'
    assert cards['오늘 전략 리포트']['tone'] == 'warn'
    early = {c['label']: c for c in V.freshness_cards(tmp_path, '종료', wed('08:10'))}
    assert early['마지막 판단']['tone'] == 'off' and early['장기투자 실행기']['tone'] == 'off' and early['오늘 전략 리포트']['tone'] == 'off'
    L.put_state(L.connect(tmp_path), plan={'day': '2026-11-18', 'time': '2026-11-18T08:45:00+09:00'})  # a decision made before P1: no macro
    cards = {c['label']: c for c in V.freshness_cards(tmp_path, '종료', wed('09:30'))}
    assert cards['거시 자료(FRED·연준)']['value'] == '기록 전' and cards['마지막 판단']['tone'] == 'ok'


def _market_summary(root):
    import vibe_longterm_view
    vibe_longterm_view.market_summary(root)


def test_market_tab_summary_shows_regime_in_words_and_waits_before_the_first_decision(tmp_path):
    at = AppTest.from_function(_market_summary, args=(tmp_path,)).run()
    assert not at.exception and any('첫 판단' in c.value for c in at.caption)
    seed(tmp_path)
    at = AppTest.from_function(_market_summary, args=(tmp_path,)).run()
    assert not at.exception
    text = ' '.join(m.value for m in at.markdown)
    assert '● 조정' in text and '약세 확정 대기 3일째' in text and '미국 종가 2026-11-17' in ' '.join(c.value for c in at.caption)


def test_weights_frame_marks_rows_outside_the_band():
    import vibe_longterm_view as V
    state = {'last_plan': {'want': {'NDX': 0.30, 'SPX': 0.30, 'SEMI': 0.10}, 'weights': {'NDX': 0.37, 'SPX': 0.29, 'SEMI': 0.02}},
             'plan': {'tuning': {'band': 0.05}}}
    f = V.weights_frame(state).set_index('종목')
    assert f.loc['KODEX 미국나스닥100', '밴드'] == '밖 ▲' and f.loc['KODEX 미국S&P500', '밴드'] == '안'
    assert f.loc['TIGER 미국필라델피아반도체나스닥', '밴드'] == '밖 ▼' and f.loc['TIGER 미국필라델피아반도체나스닥', '밴드 하단'] == 5.0
    assert V.weights_frame({'last_plan': {}}) is None


def seed_dca(root):
    """A 적립식 day: 1,000,000 KRW paid in and its order, recorded by the engine."""
    import vibe_longterm as L
    db = L.connect(root)
    L.log(db, 'dca', '2026-11-18', regime='correction', mode='manual', orders=[{'key': 'SPX', 'side': 'BUY', 'qty': 50, 'price': 20_000}],
          reason='정기 적립 1,000,000원')
    with db:
        db.executemany('INSERT OR REPLACE INTO nav VALUES (?,?,?)', [('2026-11-18', 'actual', 101_000_000), ('2026-11-18', 'base', 100_000_000),
                                                                     ('2026-11-18', L.CONTRIBUTED, 1_000_000)])


def test_dca_decisions_show_in_the_log_and_the_order_table(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    seed(tmp_path)
    seed_dca(tmp_path)
    at = open_tab(tmp_path, '기록')
    assert not at.exception
    log = next(t for t in tables(at) if '운용 모듈' in t)
    assert '적립식' in set(log['종류']) and '정기 적립 1,000,000원' in set(log['이유'])
    at = open_tab(tmp_path, '포트폴리오·주문')
    orders = next(t for t in tables(at) if '방향' in t and '시각' in t)
    assert ('적립식', 'KODEX 미국S&P500', 50) in set(zip(orders['종류'], orders['종목'], orders['수량']))


def test_kpis_and_nav_do_not_count_contributions_as_profit(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    seed(tmp_path)
    seed_dca(tmp_path)
    at = open_tab(tmp_path, '개요')
    assert not at.exception
    text = cards_text(at)
    assert '101,000,000원' in text and '+0.00%' in text  # 1억 + 적립 100만 = 1.01억: no gain yet
    assert '적립 누계' in text and '1,000,000원' in text
    nav_table = next(t for t in tables(at) if '실제(가상 계좌)' in t.columns)
    assert not any('contributed' in str(c) for c in nav_table.columns)


def test_engine_switched_off_shows_in_the_status_line(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    import vibe_longterm as L
    seed(tmp_path)
    L.save_settings(tmp_path, {'enabled': False})
    at = open_tab(tmp_path, '개요')
    assert not at.exception and '장기투자 운영' in cards_text(at) and '꺼짐' in cards_text(at)


def test_today_sentence_names_active_modules_outside_the_recommendation():
    def sentence():
        import vibe_longterm as L
        import vibe_longterm_view as V
        plan = {'day': '2026-11-18', 'regime': 'bull', 'indicators': {'dd': -0.01, 'gap': 0.05, 'vix': 14},
                'recommended': ['bogle_core', 'buffett_lowturn', 'dalio_rebalance', 'lynch_story'],
                'active': ['bogle_core', 'buffett_lowturn', 'dalio_rebalance', 'lynch_story', 'williams_trend', 'yeo_reservoir']}
        V.today_sentence(plan, {**L.DEFAULT_SETTINGS}, {})
    text = AppTest.from_function(sentence).run().info[0].value
    assert '같습니다' not in text and '래리 윌리엄스' in text and '저수지 분할(일반)' in text


def test_pending_regime_needs_the_saved_confirm_days(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    import vibe_longterm as L
    seed(tmp_path)
    db = L.connect(tmp_path)
    plan = L.get_state(db)['plan']
    del plan['tuning']  # a plan written before the tuning was saved with it
    L.put_state(db, plan=plan)
    L.save_settings(tmp_path, {'tuning': {**L.DEFAULT_SETTINGS['tuning'], 'confirm_days': 15}})
    at = open_tab(tmp_path, '시황·국면')
    assert not at.exception
    assert any('약세 확정까지 3/15일' in getattr(p, 'text', '') for p in at.get('progress'))
    assert '약세 확정 대기 3/15일' in cards_text(at)


def test_freshness_does_not_alert_on_a_holiday_or_when_switched_off(tmp_path, monkeypatch):
    import vibe_longterm as L
    import vibe_longterm_view as V
    christmas = datetime.fromisoformat('2026-12-25T10:00:00+09:00')  # a Friday, KRX closed
    L.put_state(L.connect(tmp_path), plan={'day': '2026-12-24', 'time': '2026-12-24T08:45:00+09:00'})
    monkeypatch.setattr(V, 'trading_day', lambda day: day.isoformat() != '2026-12-25')
    cards = {c['label']: c for c in V.freshness_cards(tmp_path, '종료', christmas)}
    assert cards['마지막 판단']['tone'] == 'off' and cards['마지막 판단']['sub'] != '오늘 판단 없음'
    assert cards['오늘 전략 리포트']['tone'] == 'off'
    L.save_settings(tmp_path, {'enabled': False})
    boxing = datetime.fromisoformat('2026-12-28T10:00:00+09:00')
    cards = {c['label']: c for c in V.freshness_cards(tmp_path, '종료', boxing)}
    assert cards['마지막 판단']['tone'] == 'off' and cards['오늘 전략 리포트']['tone'] == 'off'


def test_report_list_puts_todays_daily_report_first(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    seed(tmp_path)
    reports = tmp_path / 'reports'
    reports.mkdir()
    for key in ('2026-11-17', '2026-11-18', '2026-W46', '2026-10'):
        (reports / f'strategy-{key}.md').write_text(f'# 리포트 {key}\n', encoding='utf-8')
    at = open_tab(tmp_path, '전략 리포트')
    assert not at.exception
    assert list(at.selectbox(key='lt_report_day').options) == ['2026-11-18', '2026-11-17', '2026-W46', '2026-10']


def test_weekly_chart_puts_fills_on_their_own_week(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    import vibe_longterm as L
    seed(tmp_path)
    monkeypatch.setattr(L, 'journal_rows', lambda root: [{'symbol': '379810', 'side': 'BUY', 'qty': 3, 'time': '2024-01-11T09:41:00+09:00', 'after': 3, 'avg': 100.0}])
    at = AppTest.from_function(chart_page, args=(tmp_path,))
    goto(at.session_state, '차트')
    at.session_state['lt_chart_unit'] = '주'
    at.run()
    assert not at.exception
    marker = next(m for m in at.session_state['chart_call']['markers'] if '매수' in m['text'])
    assert datetime.fromtimestamp(marker['time']).date().isocalendar()[:2] == (2024, 2)  # Thu 2024-01-11 is ISO week 2


def test_drawdown_is_measured_on_money_earned_not_money_paid_in():
    import vibe_longterm_view as V
    nav = [('d1', 'actual', 100_000_000), ('d2', 'actual', 102_000_000), ('d3', 'actual', 100_980_000)]
    paid = {'d2': 2_000_000, 'd3': 2_000_000}  # d2 is a 2M deposit, not a gain; d3 falls 1.02M
    assert round(V.drawdowns(nav, paid)['actual'], 4) == round(98_980_000 / 100_000_000 - 1, 4)  # −1.02%, not −1.0%
    assert V.drawdowns([('d1', 'base', 101), ('d2', 'base', 99.99)], {})['base'] < 0


def test_chart_shows_the_dca_ways_buy_levels_and_contribution_days(tmp_path, monkeypatch):
    # user request 2026-11-18: the chosen 적립식 way's buy levels on the long-term chart, apart from the engine's levels
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    import vibe_longterm as L
    import vibe_longterm_view as V
    seed(tmp_path)
    L.save_settings(tmp_path, {'dca': {'actual': {'on': True, 'profile': 'yeo', 'day': 18}}})
    st_ = L.read_state(tmp_path)
    L.put_state(L.connect(tmp_path), plan={**st_['plan'], 'indicators': {**(st_['plan'].get('indicators') or {}), 'krx_close': {'NDX': 380.0}}})
    first = datetime.fromtimestamp(1_700_000_000 + 290 * 86_400).date().isoformat()  # a bar of the fake chart
    L.log(L.connect(tmp_path), 'dca', first, orders=[{'key': 'NDX', 'side': 'BUY', 'qty': 2, 'price': 390}], reason='여운봉 적립')
    at = AppTest.from_function(chart_page, args=(tmp_path,))
    goto(at.session_state, '차트')
    at.run()
    assert not at.exception
    dca = [x for x in at.session_state['chart_call']['levels'] if x['color'] == V.DCA_COLOR]
    # v3 (2026-11-18): every book's next 적립 buy with its master and size; 여운봉 waits for a down day: yesterday's close
    assert len(dca) == 1 and dca[0]['label'].startswith('장기투자 가상 계좌 · 여운봉 적립 · 하락일') and '배' in dca[0]['label'] and dca[0]['price'] == 380.0
    assert all(x['style'] == 3 for x in dca)  # wide dashes: never mistaken for an engine level
    assert any(m['text'] == '적립' for m in at.session_state['chart_call']['markers'])
    assert any(c.value.startswith('적립식 · 장기투자 가상 계좌: 거장 투자 · 여운봉') for c in at.caption)
    at.session_state['lt_chart_asset'] = 'SEMI'  # no yesterday close recorded for it: a down-day way has no price line yet
    at.run()
    assert not at.exception and not [x for x in at.session_state['chart_call']['levels'] if x['color'] == V.DCA_COLOR]


def test_regime_table_orders_by_risk_and_marks_the_current_regime():
    # user request 2026-11-18: 투매·약세·조정·회복·과열·강세 안정 told apart by importance, the current one highlighted
    import vibe_longterm_view as V
    rows = V.regime_rows('correction')
    assert [r['key'] for r in rows] == ['panic', 'bear', 'correction', 'overheat', 'recovery', 'bull']
    now = [r for r in rows if r['지금']]
    assert len(now) == 1 and now[0]['key'] == 'correction' and now[0]['지금'] == '▶ 지금'
    assert rows[0]['위험도'].startswith('■■■■■') and '매우 높음' in rows[0]['위험도'] and rows[-1]['위험도'].startswith('■□□□□')
    assert all(r['판정 순서'] for r in rows) and all(r['적립 시황 자동'] for r in rows)
    assert not [r for r in V.regime_rows(None) if r['지금']]


def test_overview_shows_the_dca_state_of_the_virtual_book(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    import vibe_longterm as L
    import vibe_longterm_view as V
    seed(tmp_path)
    cards = V.dca_cards(L.load_settings(tmp_path), {})
    assert cards[0]['value'] == '꺼짐' and '얼마나 내가' in cards[0]['sub']  # v3: the default buys my amount on my day
    L.save_settings(tmp_path, {'dca': {'actual': {'on': True, 'style': 'manual', 'mode': 'dip'}}})
    cards = V.dca_cards(L.load_settings(tmp_path), {'dca:actual': {'contributed_krw': 2_000_000, 'waiting_krw': 500_000}})
    assert cards[0]['value'] == '켜짐' and cards[0]['sub'] == '수동 투자' and '하락일 우선' in cards[1]['sub']
    assert [c['value'] for c in cards[2:]] == ['2,000,000원', '500,000원']
    at = open_tab(tmp_path, '개요')
    assert not at.exception and '적립식' in cards_text(at) + ' '.join(c.value for c in at.caption)


def test_scenario_kind_tells_the_cards_apart():
    """User 2026-11-18: the report's scenario cards differ by nature (up, flat, crash, deeper fall, overheat)."""
    from vibe_longterm_view import scenario_kind
    kinds = [scenario_kind(n)[0] for n in ('상승 지속', '횡보', '급락', '조정 심화', '과열', '투매', '회복', '무엇')]
    assert kinds == ['상승', '횡보', '급락', '하락', '과열', '급락', '상승', '기타']
    assert scenario_kind('조정 심화')[1] == 'orange' and scenario_kind('급락')[1] == 'red'


def test_each_masters_numbers_live_in_its_card_and_save_with_the_strategy(tmp_path, monkeypatch):
    # S1 (docs/longterm-strategy-settings-redesign.md, user 2026-11-18): 기준 비중 in 보글's card, the band in 달리오's,
    # the monthly sell cap in 버핏's, the pace apart, and one save for modules and numbers; the run switch is not there
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    import vibe_longterm as L
    seed(tmp_path)
    at = open_tab(tmp_path, '전략 설정')
    assert not at.exception
    keys = {w.key for w in at.number_input}
    assert {f'lt_w_{k}' for k in L.ASSETS} | {'lt_t_band', 'lt_t_monthly_sell_share', 'lt_t_daily_buy_share', 'lt_t_deploy_weeks'} <= keys
    assert 'lt_t_confirm_days' not in keys and not any(t.key == 'lt_execute' for t in at.toggle)
    assert at.button(key='lt_save_modules').disabled  # nothing changed yet
    preview = next(df.value for df in at.dataframe if '오늘 목표(바꾸면)' in df.value.columns)
    assert (preview['차이'] == '—').all()  # unedited: the saved and the on-screen strategy give the same targets
    at.number_input(key='lt_t_band').set_value(7.0).run()
    assert any('재현 기준값과 다름' in c.value for c in at.caption)
    at.number_input(key='lt_w_NDX').set_value(25.0).run()
    at.number_input(key='lt_w_RES').set_value(20.0).run()
    at.button(key='lt_save_modules').click().run()
    saved = L.load_settings(tmp_path)
    assert saved['tuning']['band'] == 0.07 and saved['weights']['NDX'] == 0.25 and saved['weights']['RES'] == 0.20


def test_the_run_view_keeps_only_the_run_switches_and_the_market_tab_sets_the_confirm_days(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    import vibe_longterm as L
    seed(tmp_path)
    at = open_tab(tmp_path, '운용 설정')  # the old tab name opens 계좌·실행 → 실행
    assert [t.key for t in at.toggle] == ['lt_execute'] and not any(w.key.startswith('lt_w_') for w in at.number_input)
    at = open_tab(tmp_path, '시황·국면')
    at.number_input(key='lt_t_confirm_days').set_value(12).run()
    at.button(key='lt_save_confirm').click().run()
    assert not at.exception and L.load_settings(tmp_path)['tuning']['confirm_days'] == 12


def test_every_view_has_a_place_in_the_seven_tabs():
    import vibe_longterm_view as V
    assert list(dict.fromkeys(tab for tab, _ in V.PLACES.values())) == ['개요', '시장', '전략', '계좌·실행', '리포트·감사',
                                                                        '미국 종목 아이디어', '변경 이력']
    state = {}
    V.goto(state, '적립식')
    assert state == {'lt_tab': '전략', 'lt_sub_전략': '적립식'}


def test_a_masters_rule_numbers_are_edited_in_its_card_and_saved_with_the_strategy(tmp_path, monkeypatch):
    # S3 (user 2026-11-18): the reservoir's per-step share, folded under its card, saved by the one 전략 저장
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    import vibe_longterm as L
    seed(tmp_path)
    at = open_tab(tmp_path, '전략 설정')
    assert at.number_input(key='lt_p_yeo_reservoir_per_step').value == 7.5
    at.number_input(key='lt_p_yeo_reservoir_step_2').set_value(-15.0).run()  # shallower than step 1: refused
    assert at.button(key='lt_save_modules').disabled
    at.number_input(key='lt_p_yeo_reservoir_step_2').set_value(-30.0).run()
    at.number_input(key='lt_p_yeo_reservoir_per_step').set_value(10.0).run()
    at.button(key='lt_save_modules').click().run()
    assert not at.exception and L.load_settings(tmp_path)['params']['yeo_reservoir']['per_step'] == 0.10


def test_the_recipe_comparison_lists_every_recipe_with_its_paper_result(tmp_path, monkeypatch):
    # S5 (user 2026-11-18)
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    import vibe_longterm as L
    seed(tmp_path)
    at = open_tab(tmp_path, '레시피 비교')
    assert not at.exception and any('아직 레시피 비교 기록이 없습니다' in i.value for i in at.info)
    db = L.connect(tmp_path)
    with db:
        db.executemany('INSERT INTO account_nav VALUES (?,?,?,?)', [(d, f'recipe:{k}', v, L.CAPITAL) for k in L.RECIPES
                                                                    for d, v in (('2026-11-18', 1e8), ('2026-11-19', 1.02e8))])
    at = open_tab(tmp_path, '레시피 비교')
    table = next(df.value for df in at.dataframe if '레시피' in df.value.columns)
    assert len(table) == len(L.RECIPES) and (table['손익'] == '+2,000,000원 (+2.00%)').all()
    assert not at.exception


def test_the_strategy_tab_switches_between_the_virtual_book_and_a_live_account(tmp_path, monkeypatch):
    # user 2026-11-18: "가상계좌와 실계좌 운용 방식을 어떻게 다르게 설정해?" — one 장부 picker, the account's own editor
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    import accounts
    import vibe_longterm as L
    monkeypatch.setattr(accounts, 'load', lambda *a, **k: {'live-2': {'kind': 'live', 'engine': 'longterm', 'label': '실계좌 2',
                                                                       'budget': {'capital_krw': 500_000}}})
    seed(tmp_path)
    at = open_tab(tmp_path, '전략 설정')
    assert at.segmented_control(key='lt_book_pick').value == 'actual' and at.radio(key='lt_actual_way')
    at.segmented_control(key='lt_book_pick').set_value('live-2').run()
    assert not at.exception and at.radio(key='ltv_lt_book_strategy_live-2').value == 'engine'
    at.radio(key='ltv_lt_book_strategy_live-2').set_value('yeo_method').run()
    assert not at.exception  # switching to the 여운봉 매매법 raised before (stories unset)
    at.checkbox(key='ltv_lt_book_yeo_ok_live-2').check().run()
    at.button(key='ltv_lt_book_save_live-2').click().run()
    assert L.load_settings(tmp_path)['books']['live-2']['strategy'] == 'yeo_method'
    assert (L.load_settings(tmp_path)['books'].get('actual') or {}).get('strategy', 'engine') != 'yeo_method'  # the virtual book untouched


def test_the_strategy_guide_opens_with_its_diagrams_and_every_section(tmp_path, monkeypatch):
    # user 2026-11-18: an easy guide with diagrams in a pop-up, and the tooltips on the strategy screen
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    import longterm_strategy_guide as G
    seed(tmp_path)
    at = open_tab(tmp_path, '전략 설정')
    assert at.segmented_control(key='lt_book_pick').help and at.button(key='lt_save_modules').help
    assert '재현 기준값' in at.number_input(key='lt_t_band').help and G.TUNING_MEANING['band'] in at.number_input(key='lt_t_band').help
    at.button(key='lt_strategy_guide').click().run()
    assert not at.exception
    text = ' '.join(m.value for m in at.markdown)
    assert all(label in [t.label for t in at.tabs] for label, _ in G.PARTS)
    assert '설정할 장부' in text and '이 계좌 수치' not in text and '가상 체결' in text
    assert len(at.get('iframe')) + len(at.get('graphviz_chart')) >= 2  # the structure and the day flow (Archify, or Graphviz fallback)


def test_scenario_badges_follow_the_settings_now_not_the_reports_words():
    # user 2026-11-18: every card read "실행 안 됨 · 설정 꺼짐" from the 08:50 wording although 가상 체결 was on by noon
    import vibe_longterm as L
    import vibe_longterm_view as V
    settings = {**L.DEFAULT_SETTINGS, 'execute': True, 'modules': {**L.DEFAULT_SETTINGS['modules'], 'williams_rebound': False, 'marks_pendulum': True}}
    flat = V.scenario_status('첫 투입 뒤 추가 거래 없음. 월 1일 적립(가상 체결 꺼짐이라 계획만).', settings)
    assert [b[0] for b in flat] == ['가상 체결 켜짐 → 가상 매매']
    crash = V.scenario_status('윌리엄스 단기 규칙상 저수지 5% 나스닥100 10거래일이지만 모듈 꺼짐이라 미집행.', settings)
    assert crash[0][0] == '래리 윌리엄스 급락 단기 반등 꺼짐 → 기록만' and crash[0][1] == 'gray'
    hot = V.scenario_status('막스 규칙상 코어 분할 매도(모듈 꺼짐).', settings)
    assert hot[0][0].endswith('켜짐 → 실행')


def test_the_inputs_guide_lists_every_source_and_opens_on_the_asked_part(tmp_path, monkeypatch):
    # user 2026-11-18: "거장 시황은 어떤 것들을 보는 거야?" — every source, how it is used, what is not used, today's values
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    import longterm_inputs_guide as IG
    seed(tmp_path)
    at = open_tab(tmp_path, '시황·국면')
    at.button(key='lt_inputs_market').click().run()
    assert not at.exception
    text = ' '.join(m.value for m in at.markdown)
    assert '^NDX' in text and 'VIX' in text and 'FRED' in text and '거래대금' in text  # used and not used
    assert len(at.get('iframe')) + len(at.get('graphviz_chart')) >= 1  # Archify, or Graphviz fallback
    assert {s[4] for s in IG.SOURCES} == {IG.RULE, IG.VIA_STORY, IG.REF}


# ---- review 2026-11-18 (3rd, TDD) ----

def test_an_order_for_a_yeo_or_watchlist_etf_shows_its_name_and_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    import vibe_longterm as L
    import longterm_account_view as A
    seed(tmp_path)
    db = L.connect(tmp_path)
    last = L.get_state(db)['last_plan']
    last['orders'] = [{'key': 'NDXMIX', 'code': '0261H0', 'side': 'BUY', 'qty': 6, 'price': 12_000},
                      {'key': '005930', 'code': '005930', 'side': 'BUY', 'qty': 1, 'price': 50_000}]
    L.put_state(db, last_plan=last)
    at = open_tab(tmp_path, '포트폴리오·주문')
    assert not at.exception
    assert L.asset_name('NDXMIX') in ' '.join(str(v) for t in tables(at) for v in t['종목'].tolist() if '종목' in t)
    rows = A.order_rows({'orders': [{**o, 'status': 'planned'} for o in last['orders']]})
    assert rows[0]['종목'].startswith(L.asset_name('NDXMIX'))


def test_the_strategy_report_shows_its_own_text_under_the_scenario_badges(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    seed(tmp_path)
    folder = tmp_path / 'reports'
    folder.mkdir()
    (folder / 'strategy-2026-11-18.md').write_text('# 장기투자 전략 리포트\n\nREPORT_BODY_MARKER', encoding='utf-8')
    (folder / 'strategy-2026-11-18.json').write_text(json.dumps({'scenarios': [
        {'name': '급락', 'trigger': 'VIX 30', 'action': '달리오 월 밴드로 리밸런싱'}]}, ensure_ascii=False), encoding='utf-8')
    def report(root):
        import vibe_longterm_view
        vibe_longterm_view.report_tab(root)
    at = AppTest.from_function(report, args=(tmp_path,)).run()
    assert not at.exception and any('REPORT_BODY_MARKER' in m.value for m in at.markdown)


def test_indicator_cards_take_a_plan_with_missing_indicators():
    import vibe_longterm_view as V
    cards = V.indicator_cards({'vix': 22})
    assert [c['value'] for c in cards][2] == '22.0'


def test_a_saved_one_decimal_percent_reads_back_as_the_same_share():
    def view():
        import streamlit as st
        import vibe_longterm_view as V
        st.session_state['out'] = V.tuning_input(st, 'band', 0.041)
    at = AppTest.from_function(view).run()
    assert at.session_state['out'] == 0.041  # equal to the saved value, so '전략 저장' stays off without an edit


def test_the_yeo_steps_of_a_live_account_show_its_own_used_steps():
    import vibe_longterm_view as V
    books = [{'key': 'live-2', 'label': '실계좌 2', 'yeo': {}, 'yeo_state': {'ndx': {'used': [-0.2]}}}]
    lines, _ = V.yeo_chart('NDX', [{'close': 100.0}], {'indicators': {'ath': {'ndx': {'dd': -0.21}}}}, books)
    assert [x['label'].endswith('집행됨') for x in lines] == [True, False, False]


def test_the_weight_chart_shows_every_etf_the_book_holds_or_targets():
    # review4 D-6: since 11-18 17:29 the virtual book runs the 여운봉 매매법 and trades NDXMIX (0261H0); the chart left it out
    import vibe_longterm_view as V
    state = {'last_plan': {'want': {'RES': 0.80, 'SPX': 0.074, 'NDX': 0.052, 'SEMI': 0.025, 'NDXMIX': 0.022, 'SPACE': 0.018, 'QUANT': 0.01},
                           'weights': {'RES': 0.80, 'SPX': 0.074, 'NDX': 0.074, 'SEMI': 0.025, 'SPACE': 0.018, 'QUANT': 0.01, 'NDXMIX': 0.0}}}
    frame = V.weights_frame(state, band=0.05).set_index('종목')
    mix = frame.loc['KODEX 미국나스닥100미국채혼합50']
    assert (mix['현재'], mix['목표'], mix['역할']) == (0.0, 2.2, '여운봉 매매법')
    assert round(frame['목표'].sum(), 1) == 100.1  # every target is drawn (the plan's own rounding)


def test_a_failed_yahoo_read_is_asked_again_not_kept_for_an_hour(monkeypatch):
    # review4 D-10: an empty answer was cached for an hour, so one network blip blanked the chart until then
    import io
    import time
    import urllib.request
    monkeypatch.setattr(urllib.request, 'urlopen', lambda req, timeout: (_ for _ in ()).throw(OSError('network')))
    symbol = f'R4-{time.time_ns()}'  # a symbol no other test has cached
    assert yahoo_bars(symbol) == []
    body = {'chart': {'result': [{'timestamp': [1], 'indicators': {'quote': [{'open': [1], 'high': [2], 'low': [0.5], 'close': [1.5], 'volume': [10]}]}}]}}
    monkeypatch.setattr(urllib.request, 'urlopen', lambda req, timeout: io.BytesIO(json.dumps(body).encode()))
    assert yahoo_bars(symbol) == [{'time': 1, 'open': 1, 'high': 2, 'low': 0.5, 'close': 1.5, 'volume': 10}]
