"""Long-term news (user approval 2026-11-18, 점검 보고서 개선안 1): the morning headlines of the Fed, US macro and the three
themes reach the decision log and the story check; a failure leaves only that input out."""
import json
from datetime import datetime, timedelta, timezone

import etf_news
import longterm_news as N
import vibe_longterm as L
from test_vibe_longterm import DAY, KST, fake_fetch, krx_calendar  # noqa: F401

NOW = datetime.combine(DAY, datetime.min.time(), KST).replace(hour=8, minute=45)
REAL_COLLECT = N.collect  # conftest replaces it in every test so the engine never fetches; these tests use the real one


def items(*rows):
    return lambda source, topic, query, shared: [(t, p, NOW - timedelta(hours=h), f'https://news.example/{i}') for i, (t, p, h) in enumerate(rows)]


def test_long_term_topics_use_the_shared_source_rotation_with_a_day_window_and_links(tmp_path, monkeypatch):
    monkeypatch.setattr(etf_news, '_items', items(('연준 파월 금리 동결 시사', '한경', 3), ('엔비디아 반도체 실적', '매경', 20),
                                                  ('양자컴퓨터 아이온큐 계약', '조선', 30), ('스페이스X 로켓 발사', '서울', 5),
                                                  ('미국 CPI 물가 둔화 뉴욕증시', '머니', 10), ('연합뉴스 반도체', '연합뉴스', 1),
                                                  ('너무 오래된 우주 기사', '한경', 60)))
    got = REAL_COLLECT(NOW, tmp_path)
    topics = {r['topic'] for r in got['rows']}
    assert topics == {'fed', 'us_macro', 'semi', 'quantum', 'space'} and got['error'] is None
    assert all(r['link'].startswith('https://') for r in got['rows']) and not any('연합' in r['publisher'] for r in got['rows'])
    assert not any('오래된' in r['title'] for r in got['rows'])  # older than the window
    assert REAL_COLLECT(NOW + timedelta(hours=1), tmp_path)['rows'] == got['rows']  # once a day: the morning set is reused
    assert '연준' in N.brief(got)


def test_a_failed_fetch_leaves_the_input_out(tmp_path, monkeypatch):
    def down(*a):
        raise OSError('blocked')
    monkeypatch.setattr(etf_news, '_items', down)
    got = REAL_COLLECT(NOW, tmp_path)
    assert got['rows'] == [] and got['error'] and N.brief(got) == ''


def test_the_morning_decision_logs_the_news_and_passes_it_to_the_story_check(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    seen = {}
    answer = json.dumps({'market_view': '', 'stories': {k: {'status': 'intact', 'tilt': 0, 'reason': 'ok'} for k in L.SATS}})
    news = {'as_of': NOW.isoformat(), 'rows': [{'title': '연준 금리', 'publisher': '한경', 'published': NOW.isoformat(), 'topic': 'fed',
                                                'link': 'https://x', 'source': 'google_news_rss'}], 'error': None}

    def ask(text):
        seen['prompt'] = text
        return answer
    assert L.decide(tmp_path, NOW, fetch=fake_fetch, ask=ask, news=lambda now: news) == 'decided'
    assert '연준 금리' in seen['prompt']
    row = L.connect(tmp_path).execute("SELECT indicators, reason FROM decisions WHERE kind='news'").fetchone()
    assert json.loads(row[0])['rows'][0]['link'] == 'https://x' and '연준 금리' in row[1]
    story = json.loads(L.connect(tmp_path).execute("SELECT targets FROM decisions WHERE kind='story'").fetchone()[0])
    assert story['inputs']['news'] == 1


def test_the_semiconductor_story_also_reads_export_controls_tariffs_and_taiwan():
    # 2026-11-18 (user approval): what can change the semiconductor theme's story itself, apart from a market-wide shock
    # (which the regime already reads through VIX and the drawdown)
    import longterm_news as N
    feed = next(f for f in N.FEEDS if f[0] == 'semi_policy')
    assert all(w in feed[1] for w in ('수출', '관세', '대만')) and {'수출통제', '관세', '대만'} <= set(feed[2])
    assert N.TOPIC_LABELS['semi_policy'] == '반도체 정책·지정학'
    assert N.brief({'rows': [{'topic': 'semi_policy', 'title': '미국, 대중 반도체 수출통제 확대', 'publisher': 'x',
                              'published': '2026-11-19T07:00:00+09:00'}]}).startswith('[반도체 정책·지정학]')
