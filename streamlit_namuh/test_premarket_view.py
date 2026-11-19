from unittest.mock import patch
import json
from datetime import datetime
import sqlite3
from zoneinfo import ZoneInfo

import premarket
from streamlit.testing.v1 import AppTest


def _view():
    import premarket_view
    premarket_view.render()


def test_engine_data_status_uses_filtered_engine_brief(tmp_path):
    from premarket_view import engine_data_status

    now = datetime(2026, 11, 16, 8, 40, tzinfo=ZoneInfo('Asia/Seoul'))
    section = lambda source, at, data: {'source': source, 'observed_at': at, 'status': 'ok', 'data': data}
    brief = {'time': now.isoformat(), 'trading_day': '2026-11-16', 'sections': {
        'market': section('네이버 금융·Yahoo Finance', '2026-11-16T08:39:00+09:00',
                          {'global': {'나스닥100': {'date': '2026-11-13', 'change_pct': 1}}}),
        'news': section('Google News RSS', '2026-11-16T07:00:00+09:00', []),
        'dart': section('Open DART', '2026-11-16T08:39:00+09:00', [
            {'receipt_date': '20261116'}, {'receipt_date': '20261113'}]),
        'krx': section('KRX', '2026-11-16T08:39:00+09:00', [])}}
    (tmp_path / premarket.FILE).write_text(json.dumps(brief), encoding='utf-8')
    context = premarket.engine_brief(root=tmp_path, now=now)
    states = {label: status for label, _, status in engine_data_status(brief, context)}
    assert states['해외 일봉'] == '전달 가능'
    assert states['뉴스'] == '미포함 · 자료 또는 수집 시각 부적합'
    assert states['공시'] == '전달 가능'
    assert len(context['disclosures']) == 1
    assert states['KRX 일별 통계'] == '엔진 브리프 직접 미포함'
    assert engine_data_status(brief, {'as_of': 'different'})[0][2] == '전달 가능 여부 확인 불가'


def test_engine_status_does_not_claim_actual_model_receipt():
    brief = {'time': '2026-11-16T08:40:00+09:00', 'sections': {}}
    with patch.object(premarket, 'load', return_value=brief), patch.object(premarket, 'engine_brief', return_value=None):
        app = AppTest.from_function(_view).run()
    assert not app.exception
    assert any('실제 모델 입력·수신이나 판단에 반영됐다는 증거는 아닙니다' in item.value for item in app.caption)
    assert any('전달 가능 여부 확인 불가' in item.value for item in app.markdown)
    assert any('상세 분석문은 엔진 브리프에 직접 포함되지 않습니다' in item.value for item in app.caption)


def test_recorded_inputs_shows_actual_context_and_missing_records(tmp_path):
    from premarket_view import recorded_engine_inputs

    path = tmp_path / 'market-research.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE model_inputs (digest TEXT, time TEXT, engine TEXT, inputs TEXT)')
        db.executemany('INSERT INTO model_inputs VALUES (?, ?, ?, ?)', [
            ('jev-id', '2026-11-16T09:11:00+09:00', 'jev',
             json.dumps({'state': {'premarket_context': {'as_of': '2026-11-16T08:40:00+09:00'}}})),
            ('vibe-id', '2026-11-16T09:15:00+09:00', 'vibe',
             json.dumps({'context': {'premarket_context': None}})),
            ('yesterday', '2026-11-15T09:15:00+09:00', 'vibe', '{}')])
    found = recorded_engine_inputs(tmp_path, {'trading_day': '2026-11-16'})
    assert found['jev']['as_of'] == '2026-11-16T08:40:00+09:00'
    assert found['jev']['digest'] == 'jev-id'
    assert 'vibe' not in found  # the intraday vibe advisor stopped on 2026-11-17
    assert recorded_engine_inputs(tmp_path, {'trading_day': '2026-11-17'}) == {}
    assert recorded_engine_inputs(tmp_path / 'missing', {'trading_day': '2026-11-16'}) == {}


def test_brief_shows_strategy_source_and_missing_data():
    brief = {
        'time': '2026-11-16T08:40:00+09:00',
        'summary': ['미국 증시 마감 확인'], 'strategy': ['호가 확인 후 기존 조건 적용'],
        'sections': {
            'news': {'status': 'ok', 'source': 'Google News', 'observed_at': '08:40', 'data': [
                {'title': '시장 뉴스', 'publisher': '신문', 'published': '08:15', 'link': 'https://example.com/news'}]},
            'futures': {'status': 'partial', 'source': 'Yahoo', 'data': {'NQ': {'price': 100}}},
            'krx': {'status': 'ok', 'source': 'KRX', 'data': {'day': '2026-11-13'}},
            'dart': {'status': 'ok', 'source': 'DART', 'data': []},
        },
    }
    with patch.object(premarket, 'load', return_value=brief):
        app = AppTest.from_function(_view).run()
        assert not app.exception
        assert any('일부 수집' in item.value for item in app.caption)
        assert app.dataframe[0].value.iloc[0]['제목'] == '시장 뉴스'
        assert any('호가 확인' in item.value for item in app.markdown)
        app.selectbox(key='premarket_detail').select('dart').run()
        assert not app.dataframe
        assert any('접수일만으로' in item.value for item in app.caption)
        app.selectbox(key='premarket_detail').select('krx').run()
        assert any('한국거래소 통계정보' in item.value for item in app.caption)


def test_missing_brief_is_not_replaced_by_yesterday():
    with patch.object(premarket, 'load', return_value=None):
        app = AppTest.from_function(_view).run()
    assert not app.exception
    assert '오늘 수집된 장전 분석이 없습니다' in app.info[0].value


def test_detailed_report_only_reads_brief_date_and_displays_missing(tmp_path):
    import premarket_view

    reviews = tmp_path / 'reviews'
    reviews.mkdir()
    (reviews / 'premarket-2026-11-16.md').write_text('## 확인된 상세 분석', encoding='utf-8')
    brief = {'trading_day': '2026-11-16', 'time': '2026-11-16T08:40:00+09:00', 'sections': {}}
    with patch.object(premarket, 'load', return_value=brief), patch.object(premarket, 'ROOT', tmp_path):
        app = AppTest.from_function(_view).run()
    assert not app.exception
    assert any('확인된 상세 분석' in item.value for item in app.markdown)
    assert any('공통 템플릿과 다릅니다' in item.value and '절이 없습니다' in item.value for item in app.warning)
    assert premarket_view.detailed_report({'trading_day': '../2026-11-16'}, tmp_path) is None
    assert premarket_view.detailed_report({'trading_day': '2026-11-15'}, tmp_path) is None
    with patch.object(premarket, 'load', return_value={**brief, 'trading_day': '2026-11-15'}), \
            patch.object(premarket, 'ROOT', tmp_path):
        missing = AppTest.from_function(_view).run()
    assert not missing.exception
    assert any('아직 작성되지 않았습니다' in item.value for item in missing.caption)
    assert not any('확인된 상세 분석' in item.value for item in missing.markdown)


def test_before_open_uses_previous_close_and_reports_no_breadth():
    from market_view import breadth, index_display

    item = {'traded_at': '2026-11-16T08:23:00+09:00', 'close': 5290.53, 'change_pct': 0.0,
            'history': [['2026-11-13', 5290.53]], 'breadth': {'상승': 0, '하락': 0}}
    assert index_display(item) == ('2026-11-13', 5290.53, None)
    assert breadth(item) is None
    item['breadth']['하락'] = 10
    assert breadth(item) == (0, 10)


def _market():
    import app

    app.market_panel()


def test_market_shows_summary_and_indicators_together_without_selection():
    import market_context

    indices = {code: {'name': name, 'regime': {'label': '하락장'}, 'traded_at': '2026-11-16T08:23:00+09:00',
                      'history': [['2026-11-13', 100]], 'breadth': {'상승': 0, '하락': 0}}
               for code, name in market_context.INDICES.items()}
    data = {'time': '2026-11-16T08:23:00+09:00', 'indices': indices,
            'themes': {'groups': [{'group': '반도체', 'change_pct': 1.0}],
                       'top': [{'name': '반도체', 'change_pct': 1.0, 'rise': 2, 'fall': 1}]},
            'global': {'나스닥100': {'date': '2026-11-13', 'price': 100, 'change_pct': 0.5}}}
    with patch.object(market_context, 'load', return_value=data), patch.object(premarket, 'load', return_value=None):
        result = AppTest.from_function(_market).run()
    assert not result.exception
    text = '\n'.join(node.value for node in result.markdown)
    assert all(label in text for label in ('시장 요약', '코스피 일봉 국면', '코스닥 일봉 국면', '미집계',
                                           '시장 자료 수집', '국내 지수·수급', '테마 강세·약세', '해외 지표'))
    assert sum('집계 대기' in node.value for node in result.caption) == 2
    assert '### 코스피 100.00' in text and '### 코스닥 100.00' in text
    assert any('반도체' in frame.value.to_string() for frame in result.dataframe)
    assert any('나스닥100' in node.value and '2026-11-13' in node.value for node in result.caption)
    assert any('100.00' in node.value and '+0.50%' in node.value for node in result.markdown)
    assert any('오늘 수집된 장전 분석이 없습니다' in node.value for node in result.info)



def test_ranked_news_uses_journal_scoring_and_retains_sources():
    from premarket_view import ranked_news

    data = {'status': 'ok', 'data': [
        {'title': '금리 인상 쇼크와 급락', 'topic': '금리 환율 유가 반도체',
         'publisher': '언론사', 'link': 'https://example.com/a'},
        {'title': '코스피 보합', 'topic': '한국 증시 코스피',
         'publisher': '언론사', 'link': 'https://example.com/b'}]}
    ranked = ranked_news(data)
    assert ranked[0]['title'] == '금리 인상 쇼크와 급락'
    assert ranked[0]['category'] == '금리·연준'
    assert ranked[0]['stars'] > ranked[1]['stars']
    assert ranked[0]['link'] == 'https://example.com/a'
    assert ranked_news({'status': 'error', 'data': data['data']}) == []



def test_impact_cards_uses_observed_sources_and_company_mentions():
    from premarket_view import impact_cards

    brief = {'sections': {
        'market': {'status': 'ok', 'data': {'global': {'나스닥100': {'date': '2026-11-13', 'change_pct': -1.2}}}},
        'news': {'status': 'ok', 'data': [{'title': '삼성전자 반도체 투자 발표', 'topic': '한국 증시 코스피',
                                          'publisher': '언론사', 'link': 'https://example.com'}]},
        'krx': {'status': 'ok', 'data': [{'ISU_NM': '삼성전자'}]}}}
    cards = impact_cards(brief)
    assert [card['scope'] for card in cards] == ['국내 시장', '반도체', '삼성전자']
    assert '2026-11-13' in cards[0]['evidence']
    assert cards[1]['link'] == 'https://example.com'
    assert impact_cards({'sections': {}}) == []


def _highlight():
    import premarket_view
    premarket_view.highlight_news({'status': 'ok'})


def test_high_importance_news_uses_color_and_explicit_rating():
    import premarket_view

    news = [{'stars': n, 'category': '시황', 'title': f'뉴스 {n}', 'why': [],
             'publisher': '매체', 'published': '2026-11-16T08:00:00+09:00'} for n in (5, 4, 3)]
    with patch.object(premarket_view, 'ranked_news', return_value=news):
        app = AppTest.from_function(_highlight).run()
    assert not app.exception
    labels = [item.value for item in app.markdown]
    assert any(':red-badge[' in text and '중요도 5/5' in text for text in labels)
    assert any(':orange-badge[' in text and '중요도 4/5' in text for text in labels)
    assert any('중요도 3/5' in text and '-badge[' not in text for text in labels)


def test_a_report_that_follows_the_template_shows_no_warning(tmp_path):
    import premarket_template as T

    reviews = tmp_path / 'reviews'
    reviews.mkdir()
    brief = {'trading_day': '2026-11-17', 'time': '2026-11-17T07:40:00+09:00', 'sections': {}}
    written = premarket.report_md(brief).replace(T.SUMMARY_PENDING, '요약').replace(T.WRITE, '- 분석')
    (reviews / 'premarket-2026-11-17.md').write_text(written, encoding='utf-8')
    with patch.object(premarket, 'load', return_value=brief), patch.object(premarket, 'ROOT', tmp_path):
        app = AppTest.from_function(_view).run()
    assert not app.exception
    assert not app.warning
    assert any('1. 수집 상태' in item.value for item in app.markdown)


def test_dashboard_ranking_is_the_premarket_ranking():
    import premarket_view

    section = {'status': 'ok', 'data': [{'title': '나스닥 급락', 'topic': '미국 증시 나스닥', 'link': 'https://a.example'}]}
    market = {'global': {'나스닥100': {'date': '2026-11-16', 'change_pct': -2.67}}}
    assert premarket_view.ranked_news(section, market, '2026-11-17') == premarket.rank_news(section, market, '2026-11-17')
    assert '나스닥 -2.7%' in premarket_view.ranked_news(section, market, '2026-11-17')[0]['why']
