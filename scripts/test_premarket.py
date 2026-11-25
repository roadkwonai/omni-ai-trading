import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import premarket as p


def test_load_and_engine_filter_sources_and_future(tmp_path):
    now = datetime(2026, 11, 16, 9, 0, tzinfo=ZoneInfo('Asia/Seoul'))
    doc = {'time': (now - timedelta(minutes=1)).isoformat(), 'trading_day': '2026-11-16', 'sections': {
        'krx': {'source': '한국거래소 통계정보', 'status': 'ok', 'data': [{'secret_marker': 'must_not_leave'}]},
        'news': {'source': 'Google News RSS', 'status': 'ok', 'data': [
            {'published': (now - timedelta(hours=1)).isoformat(), 'link': 'https://example.com/a', 'title': 'good'},
            {'published': (now + timedelta(minutes=1)).isoformat(), 'link': 'https://example.com/b', 'title': 'future'}]},
        'dart': {'source': 'Open DART', 'status': 'ok', 'data': [
            {'receipt_date': '20261113', 'title': 'old'}, {'receipt_date': '20261116', 'title': 'today'}]},
        'futures': {'source': 'Yahoo Finance', 'status': 'unknown', 'data': {'price': 100}}}}
    for section in doc['sections'].values():
        section['observed_at'] = doc['time']
    p.save(doc, tmp_path)
    assert p.load(tmp_path, now) == doc
    safe = p.engine_brief(tmp_path, now)
    assert [r['title'] for r in safe['news']] == ['good']
    assert [r['title'] for r in safe['disclosures']] == ['old']
    assert 'must_not_leave' not in json.dumps(safe)
    assert 'futures' not in safe
    assert p.engine_brief(tmp_path, now + timedelta(minutes=31))['historical'] is True
    assert p.engine_brief(tmp_path, now + timedelta(days=1)) is None
    assert p.load(tmp_path, now - timedelta(days=1)) is None


def test_krx_and_dart_missing_credentials_do_not_call_network(monkeypatch):
    import requests
    monkeypatch.setattr(requests, 'get', lambda *a, **k: (_ for _ in ()).throw(AssertionError('unexpected request')))
    now = datetime(2026, 11, 16, 8, tzinfo=ZoneInfo('Asia/Seoul'))
    assert p._krx(now, ['005930'], '2026-11-13', None, None)['status'] == 'unavailable'
    assert p._dart(now, ['005930'], None, None)['status'] == 'unavailable'


def test_error_cache_and_global_date_validation(tmp_path, monkeypatch):
    import requests
    now = datetime(2026, 11, 16, 9, 0, tzinfo=ZoneInfo('Asia/Seoul'))
    previous = {'source': '한국거래소 통계정보', 'observed_at': now.isoformat(), 'status': 'error',
                'data': [], 'base_date': '2026-11-13', 'codes': ['005930']}
    monkeypatch.setattr(requests, 'get', lambda *a, **k: (_ for _ in ()).throw(AssertionError('unexpected request')))
    assert p._krx(now, ['005930'], '2026-11-13', 'configured', previous) == previous
    doc = {'time': now.isoformat(), 'trading_day': '2026-11-16', 'sections': {
        'market': {'source': '네이버 금융·Yahoo Finance', 'status': 'ok', 'observed_at': now.isoformat(),
                   'data': {'global': {'old': {'date': '2024-01-01'}, 'bad': {'date': 'not-a-date'},
                                       'recent': {'date': '2026-11-13'}}}},
        'news': {'source': 'Google News RSS', 'status': 'ok', 'observed_at': (now + timedelta(minutes=1)).isoformat(),
                 'data': [{'published': now.isoformat(), 'link': 'https://example.com', 'title': 'future source'}]}}}
    p.save(doc, tmp_path)
    result = p.engine_brief(tmp_path, now)
    assert list(result['global_daily']) == ['recent']
    assert result['news'] == []


def test_keeps_same_day_nxt_observation_after_premarket(monkeypatch):
    now = datetime(2026, 11, 16, 9, 0, tzinfo=ZoneInfo('Asia/Seoul'))
    last = {'observed_at': (now - timedelta(minutes=20)).isoformat(), 'data': [{'code': '005930'}]}
    monkeypatch.setattr(p, '_nxt_quotes', lambda *_: {'status': 'unavailable'})
    assert p._nxt_snapshot(now, [], last)['data'] == last['data']
    assert p._nxt_snapshot(now + timedelta(days=1), [], last)['status'] == 'unavailable'


def test_watchlist_respects_manual_edits(tmp_path):
    (tmp_path / 'watchlist.json').write_text(json.dumps({'selected': ['005930', '000660']}))
    (tmp_path / 'watchlist-manual.json').write_text(json.dumps({'add': {'373220': 'LG에너지솔루션'}, 'remove': ['005930']}))
    codes = list(p.watch(tmp_path))
    assert codes[7:9] == ['373220', '000660']  # after the seven ETFs
    assert '005930' not in codes


def test_watch_puts_holdings_and_the_traded_etfs_first_and_caps_the_stocks(tmp_path):
    from etf_universe import SYMBOLS

    (tmp_path / 'account-state.json').write_text(json.dumps({'holdings': {'000660': {'rsdl_qty': 1}, '069500': {'rsdl_qty': 2}}}))
    (tmp_path / 'watchlist.json').write_text(json.dumps({'selected': [f'{n:06d}' for n in range(100, 120)]}))
    roles = p.watch(tmp_path)
    assert list(roles)[:2] == ['000660', '069500'] and roles['069500'] == '보유'  # a held ETF stays a holding
    assert set(SYMBOLS.values()) <= set(roles)
    assert sum(role == '스캐너' for role in roles.values()) == p.MAX_STOCKS
    assert not any(role == '대형주' for role in roles.values())  # the cap is reached before the large caps
    assert p._stocks(roles) == ['000660', *[c for c, r in roles.items() if r == '스캐너']]  # no DART/NXT calls for ETFs


def test_report_template_identifies_missing_sources_and_preserves_source_time():
    from premarket_template import build

    brief = {'trading_day': '2026-11-16', 'time': '2026-11-16T08:40:00+09:00',
             'sections': {'news': {'status': 'partial', 'observed_at': '2026-11-16T08:39:00+09:00'}}}
    report = build(brief)
    assert [item['key'] for item in report['sections']] == ['status', 'overnight', 'issues', 'impacts', 'strategy', 'sources']
    assert report['sections'][2]['sources'][0]['observed_at'] == '2026-11-16T08:39:00+09:00'
    assert report['sections'][1]['sources'][0]['status'] == 'unavailable'
    assert report['title'] == '# 장전 분석 · 2026-11-16 (월)'


def test_engine_impacts_use_only_validated_sources(tmp_path):
    now = datetime(2026, 11, 16, 9, 5, tzinfo=ZoneInfo('Asia/Seoul'))
    doc = {'time': now.isoformat(), 'trading_day': '2026-11-16', 'sections': {
        'market': {'source': '네이버 금융·Yahoo Finance', 'status': 'ok', 'observed_at': now.isoformat(),
                   'data': {'global': {'나스닥100': {'date': '2026-11-13', 'change_pct': -1.2}}}},
        'news': {'source': 'Google News RSS', 'status': 'ok', 'observed_at': now.isoformat(),
                 'data': [{'title': '반도체 전망', 'published': now.isoformat(), 'publisher': '언론사',
                           'link': 'https://example.com/news'},
                          {'title': '금리 인상', 'published': (now + timedelta(minutes=1)).isoformat(),
                           'link': 'https://example.com/future'}]},
        'krx': {'source': '한국거래소 통계정보', 'status': 'ok', 'observed_at': now.isoformat(),
                'data': [{'secret_marker': 'must_not_leave'}]}}}
    p.save(doc, tmp_path)
    result = p.engine_brief(tmp_path, now)
    assert [row['scope'] for row in result['impact_context']] == ['국내 시장', '반도체']
    assert result['impact_context'][1]['link'] == 'https://example.com/news'
    assert 'must_not_leave' not in json.dumps(result, ensure_ascii=False)
    assert '외부 자료' in result['note']



def test_historical_brief_rejects_stale_future_failed_and_untrusted_sections(tmp_path):
    now = datetime(2026, 11, 16, 9, 10, tzinfo=ZoneInfo('Asia/Seoul'))
    doc = {'time': now.isoformat(), 'trading_day': '2026-11-16', 'sections': {
        'market': {'source': '네이버 금융·Yahoo Finance', 'status': 'ok',
                   'observed_at': (now - timedelta(minutes=31)).isoformat(),
                   'data': {'global': {'나스닥100': {'date': '2026-11-13', 'change_pct': 1}}}},
        'news': {'source': 'Google News RSS', 'status': 'error', 'observed_at': now.isoformat(),
                 'data': [{'title': '오류', 'published': now.isoformat(), 'link': 'https://example.com'}]},
        'dart': {'source': 'Open DART', 'status': 'ok', 'observed_at': (now + timedelta(minutes=1)).isoformat(),
                 'data': [{'receipt_date': '20261113'}]},
        'krx': {'source': '한국거래소 통계정보', 'status': 'ok', 'observed_at': now.isoformat(),
                'data': [{'secret_marker': 'must_not_leave'}]}}}
    p.save(doc, tmp_path)
    for moment in (now + timedelta(minutes=31), now.replace(hour=14)):
        result = p.engine_brief(tmp_path, moment)
        assert result['as_of'] == doc['time'] and result['historical']
        assert result['news'] == [] and 'global_daily' not in result and 'disclosures' not in result
        assert 'must_not_leave' not in json.dumps(result)
    assert p.engine_brief(tmp_path, now - timedelta(seconds=1)) is None
    assert p.engine_brief(tmp_path, now + timedelta(days=1)) is None


def test_historical_news_cannot_include_future_publication(tmp_path):
    now = datetime(2026, 11, 16, 14, 0, tzinfo=ZoneInfo('Asia/Seoul'))
    captured = now.replace(hour=9)
    p.save({'time': captured.isoformat(), 'trading_day': '2026-11-16', 'sections': {
        'news': {'source': 'Google News RSS', 'status': 'ok', 'observed_at': captured.isoformat(), 'data': [
            {'title': '수집 후 기사', 'published': (captured + timedelta(hours=1)).isoformat(),
             'link': 'https://example.com/future'}]}}}, tmp_path)
    assert p.engine_brief(tmp_path, now)['news'] == []



class _Response:
    def __init__(self, status=200, body=None):
        self.status_code, self.body = status, body

    def json(self):
        return self.body


def test_krx_empty_day_is_not_cached_and_includes_etfs(monkeypatch):
    import requests
    now = datetime(2026, 11, 17, 7, 40, tzinfo=ZoneInfo('Asia/Seoul'))
    calls = []

    def unpublished(url, **kwargs):
        calls.append(url.rsplit('/', 1)[1])
        return _Response(body={'OutBlock_1': []})

    monkeypatch.setattr(requests, 'get', unpublished)
    empty = p._krx(now, ['005930', '069500'], '2026-11-16', 'configured', None)
    assert (empty['status'], empty['data']) == ('empty', []) and '08시' in empty['note']
    assert calls == ['stk_bydd_trd', 'ksq_bydd_trd', 'etf_bydd_trd']
    # 08:00: the empty result is fetched again, not reused (it was cached as 'ok' all day on 11/17)
    later = now.replace(hour=8)
    assert p._krx(later, ['005930', '069500'], '2026-11-16', 'configured', empty)['status'] == 'empty'
    assert len(calls) == 6
    old_ok_empty = {**empty, 'status': 'ok'}  # a brief saved by the earlier code
    p._krx(later.replace(hour=9), ['005930', '069500'], '2026-11-16', 'configured', old_ok_empty)
    assert len(calls) == 9

    def published(url, **kwargs):
        rows = {'stk_bydd_trd': [{'ISU_CD': '005930', 'ISU_NM': '삼성전자'}, {'ISU_CD': '000660'}],
                'etf_bydd_trd': [{'ISU_CD': '069500', 'ISU_NM': 'KODEX 200', 'NAV': '1'}]}
        return _Response(body={'OutBlock_1': rows.get(url.rsplit('/', 1)[1], [{'ISU_CD': '999999'}])})

    monkeypatch.setattr(requests, 'get', published)
    ok = p._krx(later, ['005930', '069500'], '2026-11-16', 'configured', empty)
    assert ok['status'] == 'ok' and [r['ISU_CD'] for r in ok['data']] == ['005930', '069500'] and ok['note'] is None
    monkeypatch.setattr(requests, 'get', lambda *a, **k: (_ for _ in ()).throw(AssertionError('reuse the ok result')))
    assert p._krx(later.replace(hour=9), ['005930', '069500'], '2026-11-16', 'configured', ok) == ok


def test_night_futures_closed_session_is_a_delayed_close_not_unavailable():
    now = datetime(2026, 11, 17, 7, 40, tzinfo=ZoneInfo('Asia/Seoul'))
    ended = {'available': False, 'session': 'night', 'pct': -1.76, 'price': 775.45,
             'session_open': '2026-11-16T18:00:00+09:00', 'traded_at': '2026-11-17T06:20:01+09:00'}
    closed = p._night_section(now, ended)
    assert closed['status'] == 'closed' and '11/17 06:20' in closed['note'] and '현재가 아님' in closed['note']
    assert p._night_section(now, {**ended, 'available': True})['status'] == 'ok'
    assert p._night_section(now, {**ended, 'session': 'day'})['status'] == 'unavailable'
    assert p._night_section(now, {'available': False})['status'] == 'unavailable'


RSS = (b'<rss><channel><item><title>\xec\xbd\x94\xec\x8a\xa4\xed\x94\xbc \xea\xb8\x89\xeb\x9d\xbd - M</title><source>M</source>'
       b'<link>https://news.example/1</link><pubDate>Mon, 16 Nov 2026 22:24:14 GMT</pubDate></item></channel></rss>')


def test_news_retries_rate_limits_and_stores_kst(monkeypatch, tmp_path):
    import io
    import urllib.error
    import urllib.request
    now = datetime(2026, 11, 17, 7, 40, tzinfo=ZoneInfo('Asia/Seoul'))
    attempts, slept = [], []

    def urlopen(request, timeout):
        attempts.append(request.full_url)
        if len(attempts) == 1:  # the first request of the burst is rate-limited, as on 11/17 07:40
            raise urllib.error.HTTPError(request.full_url, 429, 'Too Many Requests', {}, None)
        return io.BytesIO(RSS)

    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
    monkeypatch.setattr(p, '_sleep', slept.append)
    news = p._news(now)
    assert news['status'] == 'ok' and news['failed_topics'] == [] and len(attempts) == 4 and 3 in slept
    assert news['data'][0]['published'] == '2026-11-17T07:24:14+09:00'  # KST like every other source

    def limited(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 429, 'Too Many Requests', {}, None)

    monkeypatch.setattr(urllib.request, 'urlopen', limited)
    failed = p._news(now)
    assert failed['status'] == 'error' and failed['errors'] == dict.fromkeys(p.NEWS_QUERIES, 'HTTP 429')
    # the engine brief still takes the KST times (3-day window against the capture time)
    p.save({'time': now.isoformat(), 'trading_day': '2026-11-17', 'sections': {'news': news}}, tmp_path)
    assert [row['link'] for row in p.engine_brief(tmp_path, now)['news']] == ['https://news.example/1']


def test_rank_news_scores_with_the_journal_rules_and_overnight_moves():
    section = {'status': 'ok', 'data': [
        {'title': '나스닥 급락 마감', 'topic': '미국 증시 나스닥', 'publisher': 'A', 'link': 'https://a.example'},
        {'title': '환율 소폭 상승', 'topic': '금리 환율 유가 반도체', 'publisher': 'B', 'link': 'https://b.example'}]}
    market = {'global': {'나스닥100': {'date': '2026-11-16', 'change_pct': -2.67}},
              'indices': {'KOSPI': {'change_pct': -3.59}}}
    ranked = p.rank_news(section, market, '2026-11-17')
    assert [row['category'] for row in ranked] == ['미국 증시', '환율']
    assert '나스닥 -2.7%' in ranked[0]['why'] and ranked[0]['stars'] > ranked[1]['stars']
    assert '나스닥 -2.7%' not in p.rank_news(section, market, '2026-11-16')[0]['why']  # only earlier closes count
    assert p.rank_news({'status': 'error', 'data': section['data']}) == []


def _full_brief(now):
    at = now.isoformat()
    return {'time': at, 'trading_day': now.date().isoformat(), 'watch_codes': ['069500', '005930'],
            'watch_roles': {'069500': 'ETF', '005930': '대형주'}, 'strategy': ['하락 출발 시: 손절 우선'],
            'sections': {
                'market': {'source': '네이버 금융·Yahoo Finance', 'status': 'ok', 'observed_at': at, 'data': {
                    'global': {'나스닥100': {'date': '2026-11-16', 'price': 27197.15, 'change_pct': -2.67}},
                    'indices': {'KOSPI': {'name': '코스피', 'close': 5100.5, 'change_pct': -3.59, 'traded_at': '2026-11-16T20:15:00+09:00',
                                          'regime': {'label': '하락장'}, 'investors_100m': {'외국인': -13287.0}, 'program_100m': {}}},
                    'themes': {'groups': [{'group': '반도체', 'change_pct': -7.46}, {'group': '금융', 'change_pct': -1.19}]}}},
                'news': {'source': 'Google News RSS', 'status': 'partial', 'observed_at': at, 'failed_topics': ['금리 환율 유가 반도체'],
                         'data': [{'title': '나스닥 급락 | 마감', 'topic': '미국 증시 나스닥', 'publisher': 'A',
                                   'published': '2026-11-17T07:15:00+09:00', 'link': 'https://news.example/1'}]},
                'futures': {'source': 'Yahoo Finance', 'status': 'unknown', 'observed_at': at, 'data': {'nasdaq': {'price': 1, 'pct': -1.88}}},
                'night_futures': {'source': 'TradingView', 'status': 'closed', 'observed_at': at,
                                  'data': {'price': 775.45, 'pct': -1.76, 'traded_at': '2026-11-17T06:20:01+09:00'}},
                'krx': {'source': '한국거래소 통계정보', 'status': 'ok', 'observed_at': at, 'base_date': '2026-11-16',
                        'data': [{'ISU_CD': '069500', 'ISU_NM': 'KODEX 200', 'BAS_DD': '20261116', 'TDD_CLSPRC': '80000', 'FLUC_RT': '-3.1'}]},
                'dart': {'source': 'Open DART', 'status': 'ok', 'observed_at': at, 'data': [
                    {'code': '005930', 'company': '삼성전자', 'title': '분기보고서 ', 'receipt_date': '20261116',
                     'link': 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261116001482'}]}}}


def test_report_md_fills_every_template_section_and_check_names_problems():
    import premarket_template as T
    now = datetime(2026, 11, 17, 7, 40, tzinfo=ZoneInfo('Asia/Seoul'))
    for brief in ({'time': now.isoformat(), 'trading_day': '2026-11-17'}, _full_brief(now)):
        md = p.report_md(brief, now)
        assert T.check(md) == [], md
        assert [line[3:] for line in md.splitlines() if line.startswith('## ')] == T.headings()
        assert md.splitlines()[0] == '# 장전 분석 · 2026-11-17 (화)'
    md = p.report_md(_full_brief(now), now + timedelta(minutes=5))
    for text in ('작성: 11/17 07:45 KST (개장 전) · 브리프 수집 11/17 07:40 KST (5분 전)','| 나스닥100 | 2026-11-16 | 27,197.15 | -2.67% |', '종료 세션 마감값(지연) · 마지막 체결 11/17 06:20 KST',
                 '| 코스피 | 11/16 20:15 | 5,100.50 | -3.59% | 하락장 | -13,287억', '[나스닥 급락 ／ 마감](https://news.example/1)',
                 '| 069500 | KODEX 200 | ETF | 20261116 | 80,000 | -3.10% | 0건 |', '2026-11-16 [분기보고서](https://dart',
                 '실패 주제: 금리 환율 유가 반도체', '- 하락 출발 시: 손절 우선', '약세: 반도체 -7.46%'):
        assert text in md, text
    assert T.check(md, written=True) == [f"자동 틀의 '작성 전' 자리표시가 {len(T.SECTIONS) + 1}곳 남아 있습니다"]
    written = md.replace(T.SUMMARY_PENDING, '미국 급락 뒤 하락 출발 가능성').replace(T.WRITE, '- 분석')
    assert T.check(written, written=True, day='2026-11-17') == []
    assert T.check(written, written=True, day='2026-11-18') == ['제목의 날짜가 2026-11-18가 아닙니다']
    assert "'## 3. 핵심 이슈' 절이 없습니다" in T.check(written.replace('## 3. 핵심 이슈', '## 핵심 이슈'))
    a, b = '## 1. 수집 상태', '## 2. 밤사이 시장'
    assert any('순서' in x for x in T.check(written.replace(a, '@@').replace(b, a).replace('@@', b)))
    no_links = written.split('## 6. 출처와 한계')[0] + '## 6. 출처와 한계\n\n- 메모\n'
    assert "'## 6. 출처와 한계'에 링크가 없습니다" in T.check(no_links)
    assert "제목 아래 '> 한 줄 요약'이 없습니다" in T.check(written.replace('> ', ''))
    # written after the open: the stamp says so, and a stamp that hides it is a problem
    late = p.report_md(_full_brief(now), now.replace(hour=9, minute=40))
    assert '작성: 11/17 09:40 KST (개장 후 사후 작성) · 브리프 수집 11/17 07:40 KST (120분 전 · 지연' in late
    assert T.check(late) == []
    hidden = late.replace('(개장 후 사후 작성)', '(개장 전)')
    assert T.check(hidden) == ['09:00 이후 작성인데 작성 줄에 사후 작성 표기가 없습니다']
    assert any('작성: ' in x for x in T.check('\n'.join(line for line in md.splitlines() if not line.startswith('작성: '))))


def test_status_names_the_trading_day_the_brief_age_and_the_written_check(tmp_path, monkeypatch):
    import trading_day_start
    import premarket_template as T
    now = datetime(2026, 11, 17, 8, 20, tzinfo=ZoneInfo('Asia/Seoul'))
    monkeypatch.setattr(trading_day_start, 'trading_day', lambda at: (True, 'holiday_table_unavailable:OSError'))
    assert p.status(tmp_path, now) == {'trading_day': {'date': '2026-11-17', 'open': None, 'note': 'holiday_table_unavailable:OSError'},
                                       'brief': None, 'note': '오늘 수집된 브리프가 없습니다. 거래일이면 premarket.py --once로 수집합니다.'}
    monkeypatch.setattr(trading_day_start, 'trading_day', lambda at: (True, 'trading_day'))
    p.save(_full_brief(now - timedelta(minutes=40)), tmp_path)
    state = p.status(tmp_path, now)
    assert (state['trading_day']['open'], state['stale'], state['written'], state['checked']) == (True, True, False, None)
    (tmp_path / 'reviews').mkdir()
    (tmp_path / 'reviews/premarket-2026-11-17.md').write_text(p.report_md(_full_brief(now - timedelta(minutes=40)), now))
    assert p.status(tmp_path, now)['checked'] == [f"자동 틀의 '작성 전' 자리표시가 {len(T.SECTIONS) + 1}곳 남아 있습니다"]


class TestSkillSync:
    REPO = Path(__file__).resolve().parents[1]

    def test_the_skill_template_lists_every_section_in_order(self):
        import premarket_template as T
        text = (self.REPO / '.agents/skills/premarket-analysis/references/template.md').read_text(encoding='utf-8')
        found = [line[3:] for line in text.splitlines() if line.startswith('## ')]
        assert [h for h in found if h in T.headings()] == T.headings()

    def test_the_skill_names_the_commands_and_the_claude_entry_points_to_it(self):
        skill = (self.REPO / '.agents/skills/premarket-analysis/SKILL.md').read_text(encoding='utf-8')
        for command in ('scripts/ro premarket.py --status', 'scripts/ro --out .scratch/premarket-draft.md premarket.py --report',
                        'scripts/ro premarket_template.py --check', 'reviews/premarket-YYYY-MM-DD.md', 'premarket.rank_news'):
            assert command in skill, command
        assert 'PYTHONPATH=agent:scripts' not in skill and '/tmp/' not in skill  # scheduled runs only have Bash(scripts/ro *)
        assert 'trading_journal.rank_issues`를 쓰' not in skill
        claude = (self.REPO / '.claude/skills/premarket-analysis/SKILL.md').read_text(encoding='utf-8')
        assert '.agents/skills/premarket-analysis/SKILL.md' in claude


def test_the_documented_commands_end_to_end(tmp_path):
    """--status → --report → write → chmod 600 → --check, in a throwaway home folder."""
    import os
    import subprocess
    import sys
    import premarket_template as T
    repo = Path(__file__).resolve().parents[1]
    env = {**os.environ, 'HOME': str(tmp_path), 'PYTHONPATH': os.pathsep.join(str(repo / d) for d in ('agent', 'scripts'))}
    run = lambda *args: subprocess.run([sys.executable, *args], cwd=repo, env=env, capture_output=True, text=True, timeout=120)
    root = tmp_path / '.vibe-trading/nhplug/shared-etf-paper'
    assert json.loads(run('scripts/premarket.py', '--status').stdout)['brief'] is None  # no brief yet
    now = datetime.now(ZoneInfo('Asia/Seoul')) - timedelta(minutes=1)
    p.save(_full_brief(now), root)
    auto = run('scripts/premarket.py', '--report')
    assert auto.returncode == 0, auto.stderr
    assert T.check(auto.stdout) == []
    day = now.date().isoformat()
    path = root / 'reviews' / f'premarket-{day}.md'
    path.parent.mkdir()
    path.write_text(auto.stdout.replace(T.SUMMARY_PENDING, '요약').replace(T.WRITE, '- 분석'), encoding='utf-8')
    path.chmod(0o600)
    ok = run('scripts/premarket_template.py', '--check', str(path))
    assert (ok.returncode, ok.stdout.strip()) == (0, '템플릿과 일치합니다.')
    state = json.loads(run('scripts/premarket.py', '--status').stdout)
    assert (state['written'], state['checked'], state['sections']['night_futures']) == (True, [], 'closed')
    path.write_text(auto.stdout, encoding='utf-8')  # the skeleton itself is not a written analysis
    bad = run('scripts/premarket_template.py', '--check', str(path))
    assert bad.returncode == 1 and '작성 전' in bad.stdout


def test_previous_session_skips_weekends_and_holidays_and_partial_krx_is_fetched_again(monkeypatch):
    import requests
    import trading_day_start
    kst = ZoneInfo('Asia/Seoul')
    holidays = {'2026-11-13'}
    monkeypatch.setattr(trading_day_start, 'trading_day',
                        lambda at: (at.weekday() < 5 and at.date().isoformat() not in holidays, 'x'))
    # 11/17 08:00: the Naver index history already stops at 11/13; the base day is the session before, 11/16
    assert p.previous_session(datetime(2026, 11, 17, 8, tzinfo=kst)) == '2026-11-16'
    assert p.previous_session(datetime(2026, 11, 16, 8, tzinfo=kst)) == '2026-11-12'  # weekend and the 11/13 holiday
    now = datetime(2026, 11, 17, 8, 10, tzinfo=kst)
    partial = {'source': '한국거래소 통계정보', 'observed_at': (now - timedelta(minutes=10)).isoformat(), 'status': 'partial',
               'data': [{'ISU_CD': '069500'}], 'base_date': '2026-11-16', 'codes': ['069500']}
    calls = []
    monkeypatch.setattr(requests, 'get', lambda url, **k: calls.append(url) or _Response(body={'OutBlock_1': [{'ISU_CD': '069500'}]}))
    assert p._krx(now, ['069500'], '2026-11-16', 'configured', partial)['status'] == 'ok' and len(calls) == 3


def test_previous_session_survives_naver_switching_to_the_premarket_snapshot():
    import premarket_template as T
    from datetime import date
    day = date(2026, 11, 17)
    closed = {'indices': {'KOSPI': {'name': '코스피', 'close': 5100.5, 'change_pct': -3.59, 'traded_at': '2026-11-16T20:15:00+09:00',
                                    'investors_100m': {'외국인': -13287.0}, 'program_100m': {'전체': -5944.0},
                                    'history': [['2026-11-13', 5290.53], ['2026-11-16', 5100.5]]}},
              'themes': {'groups': [{'group': '반도체', 'change_pct': -7.46}]}}
    snapshot = {'indices': {'KOSPI': {'name': '코스피', 'close': 5100.5, 'change_pct': 0.0, 'traded_at': '2026-11-17T08:00:42+09:00',
                                      'investors_100m': {'외국인': 0.0}, 'program_100m': {'전체': 0.0},
                                      'history': [['2026-11-12', 5342.73], ['2026-11-13', 5290.53]]}},
                'themes': {'groups': []}}
    assert p.previous_market(closed, [], day) == closed  # before 08:00 the collection itself is the previous session
    half_reset = {**closed, 'themes': {'groups': [{'group': '금융', 'change_pct': -1.19}]}}  # 11/17 08:00:05
    earlier = [{'trading_day': '2026-11-17', 'time': '2026-11-17T08:03', 'sections': {'market': {'data': {**snapshot, 'previous_session': half_reset}}}},
               {'trading_day': '2026-11-17', 'time': '2026-11-17T08:00', 'sections': {'market': {'data': half_reset}}},
               {'trading_day': '2026-11-17', 'time': '2026-11-17T07:40', 'sections': {'market': {'data': closed}}}]
    assert p.previous_market(snapshot, earlier, day) == closed  # the earliest closed record of the day
    assert p.previous_market(half_reset, earlier, day) == closed
    assert p.previous_market(snapshot, [{'trading_day': '2026-11-16', 'sections': {'market': {'data': closed}}}], day) is None
    brief = {'time': '2026-11-17T08:03:00+09:00', 'trading_day': '2026-11-17', 'sections': {
        'market': {'status': 'ok', 'data': {**snapshot, 'previous_session': closed}}}}
    md = p.report_md(brief, datetime(2026, 11, 17, 8, 5, tzinfo=ZoneInfo('Asia/Seoul')))
    assert '| 코스피 | 11/16 20:15 | 5,100.50 | -3.59% | — | -13,287억 | — | — | -5,944억 |' in md and '반도체 -7.46%' in md
    alone = p.report_md({**brief, 'sections': {'market': {'status': 'ok', 'data': snapshot}}}, datetime(2026, 11, 17, 8, 5, tzinfo=ZoneInfo('Asia/Seoul')))
    assert '| 코스피 | 11/17 08:00 | 5,100.50 | -3.59% | — | — | — | — | — |' in alone and '확인할 수 없습니다' in alone
    assert T.check(alone) == []
    assert p._moves({**snapshot}, day)['코스피'] == {'change_pct': -3.59}


def test_nxt_gap_only_in_the_pre_market_and_strips_the_nx_suffix(monkeypatch):
    import kiwoom_data
    kst = ZoneInfo('Asia/Seoul')
    calls = []
    rows = {'1': [{'stk_cd': '419530_NX', 'stk_nm': 'SAMG엔터', 'disp_rt': '+1.18', 'nxt_flu_rt': '-4.90', 'krx_flu_rt': '-6.00',
                   'nxt_trde_prica': '223'}] * 20,
            '2': [{'stk_cd': '005930_NX', 'stk_nm': '삼성전자', 'disp_rt': '-0.05', 'nxt_flu_rt': '-2.13', 'krx_flu_rt': '-2.08',
                   'nxt_trde_prica': '754410'}]}
    monkeypatch.setattr(kiwoom_data, 'call', lambda api, body, key, **kw: calls.append(api) or rows[body['sort_tp_2']])
    assert p._nxt_gap(datetime(2026, 11, 17, 9, 5, tzinfo=kst), ['005930'])['status'] == 'unavailable'
    assert p._nxt_gap(datetime(2026, 11, 17, 8, 30, tzinfo=kst), ['005930'])['note'] == '키움 키 없음'
    assert calls == []
    monkeypatch.setattr(kiwoom_data, 'configured', lambda: True)
    section = p._nxt_gap(datetime(2026, 11, 17, 8, 30, tzinfo=kst), ['005930'])
    assert section['status'] == 'ok' and len(section['data']['top']) == p.NXT_GAP_TOP
    assert section['data']['top'][0]['code'] == '419530' and section['data']['top'][0]['gap_pct'] == 1.18
    assert [r['code'] for r in section['data']['watched']] == ['005930']
    monkeypatch.setattr(kiwoom_data, 'call', lambda *a, **kw: (_ for _ in ()).throw(OSError()))
    assert p._nxt_gap(datetime(2026, 11, 17, 8, 30, tzinfo=kst), [])['status'] == 'error'


def test_nxt_gap_keeps_the_last_pre_market_observation_after_0850():
    kst = ZoneInfo('Asia/Seoul')
    seen = {'source': 's', 'observed_at': '2026-11-17T08:40:00+09:00', 'status': 'ok', 'data': {'top': [{'code': '1'}], 'bottom': []}}
    kept = p._nxt_gap(datetime(2026, 11, 17, 9, 10, tzinfo=kst), ['005930'], seen)
    assert kept['data'] == seen['data'] and kept['observed_at'] == seen['observed_at'] and '마지막 장전 관측' in kept['note']
    yesterday = {**seen, 'observed_at': '2026-11-16T08:40:00+09:00'}
    assert p._nxt_gap(datetime(2026, 11, 17, 9, 10, tzinfo=kst), ['005930'], yesterday)['status'] == 'unavailable'


def test_the_brief_keeps_news_by_its_collection_status_not_its_source_name(tmp_path):
    """Review 4 (2026-11-19): engine_brief only took news named 'Google News RSS'; a fallback source dropped it silently."""
    now = datetime(2026, 11, 16, 9, 5, tzinfo=ZoneInfo('Asia/Seoul'))
    row = {'title': '반도체 전망', 'published': now.isoformat(), 'publisher': '언론사', 'link': 'https://example.com/news'}
    for status, kept in (('partial', [row]), ('error', [])):
        p.save({'time': now.isoformat(), 'trading_day': '2026-11-16', 'sections': {
            'news': {'source': 'Bing News RSS', 'status': status, 'observed_at': now.isoformat(), 'data': [row]}}}, tmp_path)
        assert p.engine_brief(tmp_path, now)['news'] == kept


def _bar(hm, close, volume, day='2026-11-18'):
    at = datetime.fromisoformat(f'{day}T{hm}:00+09:00')
    return (at, close, close, close, close, volume)


def test_nxt_premarket_summarises_todays_pre_market_from_toss(monkeypatch):
    """User 2026-11-19: NXT pre-market trades (08:00~08:50) with their times reach the 09:00 decisions."""
    import toss_data
    kst = ZoneInfo('Asia/Seoul')
    bars = [_bar('15:30', 194700, 5, '2026-11-17'), _bar('08:01', 197800, 400000), _bar('08:10', 197700, 50000),
            _bar('08:30', 197300, 30000), _bar('08:49', 196900, 50000)]
    monkeypatch.setattr(toss_data, 'configured', lambda: True)
    monkeypatch.setattr(toss_data, 'stock_minutes', lambda code: bars)
    section = p._nxt_premarket(datetime(2026, 11, 18, 8, 50, tzinfo=kst), ['005930'], {'005930': ('삼성전자', 194700)})
    assert section['status'] == 'ok' and section['source'] == '토스증권 Open API(NXT 프리마켓 1분봉)'
    row = section['data'][0]
    assert row['code'] == '005930' and row['name'] == '삼성전자' and row['last_time'] == '08:49'
    assert row['first'] == 197800 and row['last'] == 196900 and row['high'] == 197800 and row['low'] == 196900
    assert row['change_pct'] == round((196900 / 194700 - 1) * 100, 2)
    assert row['volume'] == 530000 and row['value_mil'] == round((197800 * 400000 + 197700 * 50000 + 197300 * 30000 + 196900 * 50000) / 1e6)
    assert row['from_first_pct'] == round((196900 / 197800 - 1) * 100, 2)


def test_nxt_premarket_window_fallback_and_keep(monkeypatch):
    import toss_data
    kst = ZoneInfo('Asia/Seoul')
    monkeypatch.setattr(toss_data, 'configured', lambda: True)
    monkeypatch.setattr(toss_data, 'stock_minutes', lambda code: (_ for _ in ()).throw(toss_data.TossError('429')))
    monkeypatch.setattr(p, '_naver_over_market', lambda code: {'code': code, 'last': 196900, 'change_pct': 1.13,
                                                                'last_time': '08:49', 'source': 'naver'})
    early = p._nxt_premarket(datetime(2026, 11, 18, 7, 50, tzinfo=kst), ['005930'], {})
    assert early['status'] == 'unavailable'
    section = p._nxt_premarket(datetime(2026, 11, 18, 8, 30, tzinfo=kst), ['005930'], {})
    assert section['status'] == 'partial' and section['data'][0]['source'] == 'naver'
    kept = p._nxt_premarket(datetime(2026, 11, 18, 9, 5, tzinfo=kst), ['005930'], {}, section)
    assert kept['data'] == section['data'] and '마지막 장전 관측' in kept['note']


def test_engine_brief_passes_the_nxt_pre_market_rows(tmp_path):
    now = datetime(2026, 11, 18, 8, 56, tzinfo=ZoneInfo('Asia/Seoul'))
    rows = [{'code': '005930', 'name': '삼성전자', 'last': 196900, 'change_pct': 1.13, 'last_time': '08:49'}]
    doc = {'time': now.isoformat(), 'trading_day': '2026-11-18', 'sections': {
        'nxt_premarket': {'source': '토스증권 Open API(NXT 프리마켓 1분봉)', 'status': 'ok', 'data': rows,
                          'observed_at': now.isoformat()}}}
    p.save(doc, tmp_path)
    safe = p.engine_brief(tmp_path, now)
    assert safe['nxt_premarket']['rows'] == rows and '08:00~08:50' in safe['nxt_premarket']['note']


def test_nxt_premarket_leaves_out_the_bar_still_forming(monkeypatch):
    """Toss stamps a bar with its end minute; the newest one can still be forming (toss_data.index_minutes)."""
    import toss_data
    kst = ZoneInfo('Asia/Seoul')
    bars = [_bar('08:01', 197800, 400000), _bar('08:31', 197000, 10)]
    monkeypatch.setattr(toss_data, 'configured', lambda: True)
    monkeypatch.setattr(toss_data, 'stock_minutes', lambda code: bars)
    row = p._nxt_premarket(datetime(2026, 11, 18, 8, 30, 40, tzinfo=kst), ['005930'], {})['data'][0]
    assert row['last_time'] == '08:01' and row['volume'] == 400000


def _overnight_scan(root, monkeypatch, day='2026-11-26'):
    import json
    scan = {'day': day, 'time': f'{day}T08:20:00+09:00', 'since': '2026-11-25T15:30:00+09:00', 'scanned': 350, 'failed': 1,
            'stocks': {'000001': {'name': '가나', 'direction': '호재', 'items': [
                {'time': '2026-11-25T18:00:00+09:00', 'title': '가나 | 공급계약', 'source': 'P', 'link': 'https://n/1', 'type': '수주·계약', 'direction': 1}]},
                       '000002': {'name': '다라', 'direction': '악재', 'items': [
                           {'time': '2026-11-25T19:00:00+09:00', 'title': '유상증자결정', 'source': '공시', 'link': '', 'type': '자금', 'direction': -1}]}},
            'overseas': [{'signal': '엔비디아', 'move_pct': 5.2, 'threshold_pct': 2.0, 'codes': ['000660'], 'names': ['SK하이닉스'], 'big': True, 'news': 'down',
                          'items': [{'time': '2026-11-26T06:00:00+09:00', 'title': '엔비디아 하락', 'source': '로이터', 'link': 'https://n/2', 'direction': -1}]}]}
    (root / 'overnight-morning.json').write_text(json.dumps(scan), encoding='utf-8')
    return scan


def test_the_overnight_material_is_a_section_with_the_pre_market_move(tmp_path, monkeypatch):
    import kiwoom_data
    import theme_data
    at = lambda hhmm: datetime.fromisoformat(f'2026-11-26T{hhmm}:00+09:00')
    assert p._overnight(at('07:40'), tmp_path)['status'] == 'empty' and p._overnight(at('08:20'), tmp_path)['status'] == 'unavailable'
    _overnight_scan(tmp_path, monkeypatch)
    asked = []
    monkeypatch.setattr(kiwoom_data, 'configured', lambda: True)
    monkeypatch.setattr(theme_data, 'kiwoom_quotes', lambda codes: (asked.append(codes) or {
        '000001': {'change_pct': -1.5, 'as_of': '20261126 084000'}, '000002': {'change_pct': -3.0, 'as_of': '20261125 195900'}}, 1))
    section = p._overnight(at('08:45'), tmp_path)
    assert section['status'] == 'partial' and asked == [['000001_NX', '000002_NX']]
    assert section['data']['nxt'] == {'000001': {'change_pct': -1.5, 'as_of': '20261126 084000'}}  # yesterday's trade is not today's move
    later = p._overnight(at('09:10'), tmp_path, section)
    assert later['data']['nxt'] == section['data']['nxt'] and len(asked) == 1
    text = '\n'.join(p.overnight_lines(section))
    assert '가나(000001)' in text and '[가나 ／ 공급계약](https://n/1)' in text and '호재인데 내리는 종목(키움): 가나 -1.50%' in text and '| **엔비디아** | +5.20% | 내림 | SK하이닉스 | [엔비디아 하락](https://n/2) (로이터 11/26 06:00) |' in text
    assert p.overnight_lines({}) == []


def test_the_engines_get_the_watched_stocks_material_until_half_past_ten(tmp_path, monkeypatch):
    _overnight_scan(tmp_path, monkeypatch)
    at = lambda hhmm: datetime.fromisoformat(f'2026-11-26T{hhmm}:00+09:00')
    (tmp_path / 'watchlist.json').write_text('{"selected": ["000002"]}', encoding='utf-8')
    got = p.overnight_context({'watch_codes': ['005930']}, tmp_path, at('09:01'))
    assert list(got['stocks']) == ['000002'] and got['stocks']['000002']['direction'] == 'bad' and got['overseas'][0]['signal'] == '엔비디아'
    assert p.overnight_context({'watch_codes': []}, tmp_path, at('10:31')) is None
    assert p.overnight_context({'watch_codes': []}, tmp_path, at('09:01') + timedelta(days=1)) is None


def test_a_failed_scan_does_not_fail_the_brief(tmp_path, monkeypatch, capsys):
    import overnight_catalysts
    monkeypatch.setattr(p, 'ROOT', tmp_path)
    monkeypatch.setattr(p, 'collect', lambda: {'time': '2026-11-26T07:40:00+09:00', 'trading_day': '2026-11-26', 'sections': {}})
    import research_store
    monkeypatch.setattr(research_store, 'record', lambda *a, **k: True)
    monkeypatch.setattr(overnight_catalysts, 'morning', lambda root: (_ for _ in ()).throw(RuntimeError('x')))
    p.run_once()
    assert (tmp_path / p.FILE).is_file() and 'overnight_error' in capsys.readouterr().out
