from streamlit.testing.v1 import AppTest


def _app():
    import broker_data_view as b
    row = {'code': '009150', 'name': '삼성전기', 'net_100m': 300.2}
    b.market_extras({
        'indices': {'KOSPI': {'name': '코스피', 'fallback': {'source': 'kiwoom:ka20001'}}}, 'themes': {'source': 'kiwoom:ka90001'},
        'index_intraday': {'markets': {'KOSPI': {'as_of': '11:15', 'last': 5029.41, 'change_pct': -1.39, 'from_open_pct': 0.43,
                                                 'change_30m_pct': -0.56, 'range_position_pct': 45, 'last_30': [['11:14', 5030.0], ['11:15', 5029.41]]}}},
        'program_intraday': {'markets': {'KOSPI': {'rows': [['09:42', 368.0, -1969.0, -1601.0, 778.9, -1.95]]}}},
        'foreign_desks': {'net_buy': [{**row, 'change_pct': -0.76}], 'net_sell': []},
        'investor_rankings': {'investors': {'외국인': {'ranked_at': '2026-11-17T12:04:36+09:00', 'net_buy': [row], 'net_sell': []},
                                            '개인': {'ranked_at': None, 'net_buy': [], 'net_sell': []}}}})
    b.journal_extras({'pressure': {'005930': {'short': {'date': '2026-11-16', 'amount_100m': 778.6, 'amount_rate_pct': 2.41},
                                              'credit': {'error': 'TossError'}, 'lending': None}},
                      'investor_rankings': {'외국인': {'ranked_at': '2026-11-17T15:40:00+09:00', 'net_buy': [row], 'net_sell': []}}},
                     {'005930': '삼성전자'})
    b.premarket_listings([{'name': '에이엘로봇', 'code': '471800', 'listed': '20261118', 'market': 'KOSDAQ'}])
    b.premarket_nxt_gap({'top': [{'code': '419530', 'name': 'SAMG엔터', 'gap_pct': 1.18}], 'bottom': [], 'watched': []})
    b.premarket_nxt_gap({})


def test_broker_sections_render_without_errors():
    at = AppTest.from_function(_app, default_timeout=60).run()
    assert not at.exception
    assert [m.value for m in at.markdown if m.value.startswith('###')] == ['### 장중 지수 흐름', '### 프로그램매매 장중 추이',
                                                                           '### 투자자별 순매수 상위']
    assert [t.label for t in at.tabs] == ['외국계 창구 (키움)', '외국인 (토스)', '개인 (토스)']
    assert any('기준 시각 11-17 12:04' in c.value for c in at.caption)
    assert any('기준 시각 미확인' in c.value for c in at.caption)
    assert any('표시할 괴리율 자료가 없습니다' in c.value for c in at.caption)


def test_buy_event_warnings():
    import dialogs
    assert dialogs.market_warnings({'high_gap_pct': -1.2, 'vi_up_gap_pct': 3.5, 'overheat_code': '2', 'alarm_code': '0', 'vi_halt': '1'}) == [
        '고가 대비 -1.20%', 'VI 상단까지 +3.50%', '단기과열 지정', 'VI 발동 중 (KRX)']
    assert dialogs.market_warnings({'alarm_code': '2'}) == ['투자경고']
