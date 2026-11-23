import io
import unittest
from datetime import datetime
from unittest.mock import patch
from urllib.error import URLError
from zoneinfo import ZoneInfo

import etf_news


NOW = datetime(2026, 11, 11, 11, 0, tzinfo=ZoneInfo('Asia/Seoul'))


def feed(title, published='Wed, 11 Nov 2026 10:30:00 +0900', publisher='Sample Publisher'):
    return (f'<rss><channel><item><title>{title}</title><pubDate>{published}</pubDate>'
            f'<source>{publisher}</source></item></channel></rss>').encode()


class Response(io.BytesIO):
    pass


class EtfNewsTests(unittest.TestCase):
    def setUp(self):
        # The single-source tests below pin Google; RotationTests covers the alternates.
        patcher = patch.object(etf_news, 'SOURCES', ('google_news_rss',))
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_feeds(self, documents, now=NOW):
        with patch.object(etf_news.urllib.request, 'urlopen', side_effect=[Response(x) for x in documents]):
            return etf_news.headlines7(now)

    def test_three_distinct_recent_sources_preserve_provenance(self):
        rows = self.run_feeds([feed('코스피 상승'), feed('코스닥150 동향'), feed('반도체 업황')])
        self.assertEqual([x['topic'] for x in rows], ['market', 'kosdaq150', 'semiconductor'])
        self.assertTrue(all(x['source'] == 'google_news_rss' and x['publisher'] == 'Sample Publisher' for x in rows))
        self.assertEqual(rows[0]['published'], '2026-11-11T01:30:00+00:00')
        self.assertEqual(set(rows[0]), {'title', 'published', 'source', 'topic', 'publisher'})

    def test_newest_headlines_win_over_feed_order(self):
        items = ''.join(f'<item><title>코스피 {n}</title><pubDate>{d}</pubDate><source>P</source></item>' for n, d in (
            ('어제', 'Tue, 10 Nov 2026 18:00:00 +0900'), ('오늘1', 'Wed, 11 Nov 2026 09:00:00 +0900'),
            ('오늘3', 'Wed, 11 Nov 2026 10:50:00 +0900'), ('오늘2', 'Wed, 11 Nov 2026 10:00:00 +0900')))
        rows = self.run_feeds([f'<rss><channel>{items}</channel></rss>'.encode(), feed('코스닥150 동향'), feed('반도체 업황')])
        # newest first; 09:00 is two hours old at 11:00, past RECENT (user 2026-11-18: no old news)
        self.assertEqual([r['title'] for r in rows if r['topic'] == 'market'], ['코스피 오늘3', '코스피 오늘2'])

    def test_missing_old_future_naive_or_unrelated_topic_is_left_out(self):
        invalid = [
            feed('코스닥150 동향', 'Tue, 10 Nov 2026 10:00:00 +0900'),
            feed('코스닥150 동향', 'Wed, 11 Nov 2026 11:00:01 +0900'),
            feed('코스닥150 동향', 'Wed, 11 Nov 2026 10:30:00'),
            feed('무관한 기사'),
            feed('무관한 기사', publisher='코스닥150 소식'),
            b'<rss><channel></channel></rss>',
            feed('코스닥150 동향', publisher=''),
        ]
        for item in invalid:  # that topic is left out (2026-11-18), the recent ones still reach Jev
            with self.subTest(item=item):
                rows = self.run_feeds([feed('코스피 상승'), item, feed('반도체 업황')])
                self.assertNotIn('kosdaq150', {r['topic'] for r in rows})
        stale = [feed('코스피', 'Tue, 10 Nov 2026 10:00:00 +0900'), feed('무관한 기사'), b'<rss><channel></channel></rss>']
        with patch.object(etf_news.urllib.request, 'urlopen', side_effect=[Response(x) for x in stale]), \
                self.assertRaises(ValueError):  # no recent headline at all: fails closed (the cache is bypassed here)
            etf_news._fetch7(NOW, ('google_news_rss',))

    def test_timeout_malformed_and_oversized_content_fail_closed(self):
        with patch.object(etf_news.urllib.request, 'urlopen', side_effect=URLError('offline')):
            with self.assertRaises(URLError):
                etf_news.headlines7(NOW)
        from datetime import timedelta
        for n, bad in enumerate((b'<not-rss/>', b'<rss>', b'x' * 300001), 1):  # each after the previous failure's backoff
            with self.subTest(size=len(bad)), self.assertRaises((ValueError, etf_news.ET.ParseError)):
                self.run_feeds([bad], NOW + n * (etf_news.BACKOFF + timedelta(minutes=1)))

    def test_publisher_with_known_ai_use_restriction_is_excluded(self):
        rows = self.run_feeds([feed('코스피 상승', publisher='연합뉴스'), feed('코스닥150 동향'), feed('반도체 업황')])
        self.assertFalse(any('연합뉴스' in r['publisher'] for r in rows))

    def test_one_fetch_serves_every_runner_and_rate_limit_reuses_last_good_set(self):
        from datetime import timedelta
        from urllib.error import HTTPError
        good = [feed('코스피 상승'), feed('코스닥150 동향'), feed('반도체 업황')]
        first = self.run_feeds(good)
        with patch.object(etf_news.urllib.request, 'urlopen') as urlopen:
            self.assertEqual(etf_news.headlines7(NOW + timedelta(minutes=2)), first)
            urlopen.assert_not_called()
        limited = HTTPError('u', 429, 'Too Many Requests', {}, None)
        with patch.object(etf_news.urllib.request, 'urlopen', side_effect=limited) as urlopen:
            self.assertEqual(etf_news.headlines7(NOW + timedelta(minutes=4)), first)
            self.assertEqual(etf_news.headlines7(NOW + timedelta(minutes=6)), first)  # backoff: no retry yet
            self.assertEqual(urlopen.call_count, 1)
            with self.assertRaises(HTTPError):  # past STALE_OK the failure is reported, not hidden
                etf_news.headlines7(NOW + timedelta(minutes=31))
        self.assertEqual(self.run_feeds([feed('코스피 새 소식', 'Wed, 11 Nov 2026 11:40:00 +0900'), *good[1:]],
                                        NOW + timedelta(minutes=45))[0]['title'], '코스피 새 소식')

    def test_cached_rows_older_than_a_day_are_not_reused(self):
        from datetime import timedelta
        self.run_feeds([feed('코스피 상승'), feed('코스닥150 동향'), feed('반도체 업황')])
        etf_news._write_cache(NOW + timedelta(hours=24), etf_news._read_cache()['rows'])
        with patch.object(etf_news.urllib.request, 'urlopen', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                etf_news.headlines7(NOW + timedelta(hours=24, minutes=1))

    def test_naive_observation_time_rejected_before_network(self):
        with patch.object(etf_news.urllib.request, 'urlopen') as urlopen:
            with self.assertRaises(ValueError):
                etf_news.headlines7(NOW.replace(tzinfo=None))
            urlopen.assert_not_called()


def bing(title, published='Wed, 11 Nov 2026 01:30:00 GMT', publisher='Bing Publisher'):
    return (f'<rss xmlns:News="https://www.bing.com/news/search?q=x&amp;format=rss"><channel><item><title>{title}</title>'
            f'<pubDate>{published}</pubDate><News:Source>{publisher}</News:Source></item></channel></rss>').encode()


def naver(*titles, dt='20261111103000'):
    import json
    return json.dumps([{'tit': t, 'ohnm': '네이버 매체', 'dt': dt} for t in titles], ensure_ascii=False).encode()


class RotationTests(unittest.TestCase):
    def serve(self, routes):
        """routes: host fragment -> callable(url) returning bytes or raising."""
        calls = []
        def urlopen(request, timeout=None):
            calls.append(request.full_url)
            for host, answer in routes.items():
                if host in request.full_url:
                    return Response(answer(request.full_url))
            raise AssertionError(request.full_url)
        return calls, patch.object(etf_news.urllib.request, 'urlopen', side_effect=urlopen)

    def test_google_and_naver_take_turns_and_bing_only_fills_what_both_miss(self):
        # r3 E9: Bing's feed is relevance-ranked over a day, so inside RECENT it rarely adds a story; first in the rotation
        # it cost three requests (about 3.5 s) before Naver and Google filled the topics. It is the fallback only.
        from datetime import timedelta
        from urllib.error import HTTPError
        def blocked(url):
            raise HTTPError(url, 429, 'Too Many Requests', {}, None)
        bing_titles = {'%EC%BD%94%EC%8A%A4%ED%94%BC': '코스피 상승', '%EC%BD%94%EC%8A%A4%EB%8B%A5': '코스닥 동향',
                       '%EB%B0%98%EB%8F%84%EC%B2%B4': '반도체 업황'}
        def bing_feed(url):
            return bing(next(t for q, t in bing_titles.items() if 'q=' + q in url))
        naver_titles = ['코스피 마감', '코스닥 강세']  # nothing on chips: Bing fills that topic only
        calls, patcher = self.serve({'news.google.com': blocked, 'bing.com': bing_feed,
                                     'stock.naver.com': lambda url: naver(*naver_titles)})
        with patcher:
            rows = etf_news.headlines7(NOW)  # Google blocked: Naver next, then Bing for the missing topic
            self.assertEqual({(r['topic'], r['source']) for r in rows},
                             {('market', 'naver_finance_news'), ('kosdaq150', 'naver_finance_news'), ('semiconductor', 'bing_news_rss')})
            self.assertEqual((sum('google' in c for c in calls), sum('bing' in c for c in calls)), (1, 1))
            calls.clear()
            naver_titles.append('반도체 수출')
            rows = etf_news.headlines7(NOW + timedelta(minutes=4))  # the next fetch starts at Naver, one request for all topics
            self.assertEqual({r['source'] for r in rows}, {'naver_finance_news'})
            self.assertEqual((len(calls), rows[0]['published']), (1, '2026-11-11T01:30:00+00:00'))
            calls.clear()
            etf_news.headlines7(NOW + timedelta(minutes=8))  # ... then Google again, never Bing first
            self.assertIn('google', calls[0])
            self.assertFalse(any('bing' in c for c in calls))

    def test_a_topic_missing_in_one_source_is_filled_from_the_next(self):
        calls, patcher = self.serve({'news.google.com': lambda url: feed('코스피 상승') if 'when' in url and
                                     '%EC%BD%94%EC%8A%A4%ED%94%BC' in url else feed('무관한 기사'),
                                     'bing.com': lambda url: bing('무관한 기사'),
                                     'stock.naver.com': lambda url: naver('코스닥 &quot;강세&quot;', '반도체 수출')})
        with patcher:
            rows = etf_news.headlines7(NOW)
        self.assertEqual([(r['topic'], r['source']) for r in rows],
                         [('market', 'google_news_rss'), ('kosdaq150', 'naver_finance_news'),
                          ('semiconductor', 'naver_finance_news')])
        self.assertEqual(rows[1]['title'], '코스닥 "강세"')

    def test_every_source_blocked_raises_a_network_error(self):
        def down(url):
            raise OSError('offline')
        calls, patcher = self.serve({'google': down, 'bing': down, 'naver': down})
        with patcher, self.assertRaises(OSError):
            etf_news.headlines7(NOW)
        self.assertEqual(len(calls), 3)


if __name__ == '__main__':
    unittest.main()


def test_only_recent_headlines_reach_the_engines():
    """User 2026-11-18: old news must not be used. Within RECENT (90 min) only; a quiet topic is left out; none fails."""
    from datetime import timedelta
    import pytest
    now = datetime(2026, 11, 18, 11, 20, tzinfo=ZoneInfo('Asia/Seoul'))
    words = {'market': '코스피', 'kosdaq150': '코스닥', 'semiconductor': '반도체'}

    def items(source, topic, query, shared):
        if source == 'bing_news_rss':  # 24-hour window: last night's stories only
            return [(f'{words[topic]} 어젯밤 기사', '신문', now - timedelta(hours=9))]
        if source == 'naver_finance_news':  # today's stories, but nothing on chips in the last 90 minutes
            return [('코스피 오늘 기사', '경제지', now - timedelta(minutes=10)), ('코스닥 오늘 기사', '경제지', now - timedelta(minutes=40)),
                    ('반도체 아침 기사', '경제지', now - timedelta(minutes=150))]
        raise OSError('blocked')
    with patch.object(etf_news, '_items', items):
        rows = etf_news._fetch7(now, ('bing_news_rss', 'naver_finance_news', 'google_news_rss'))
    assert {r['topic'] for r in rows} == {'market', 'kosdaq150'} and {r['source'] for r in rows} == {'naver_finance_news'}
    assert all('아침' not in r['title'] and '어젯밤' not in r['title'] for r in rows)

    def only_old(source, topic, query, shared):
        if source == 'bing_news_rss':
            return [(f'{words[topic]} 어젯밤 기사', '신문', now - timedelta(hours=9))]
        raise OSError('blocked')
    with patch.object(etf_news, '_items', only_old), pytest.raises((OSError, ValueError)):
        etf_news._fetch7(now, ('bing_news_rss', 'naver_finance_news', 'google_news_rss'))  # nothing recent: the runner waits


def test_jev_market_headlines_only_backfills_topics_missing_a_recent_article():
    from datetime import timedelta

    now = datetime(2026, 11, 23, 12, 40, tzinfo=ZoneInfo('Asia/Seoul'))
    rows = [{'title': title, 'topic': topic, 'published': (now - timedelta(minutes=age)).isoformat()}
            for title, topic, age in [('시장 보충 불필요', 'market', 31),
                                      ('시장 정확히 30분', 'market', 30),
                                      ('코스닥 정확히 60분', 'kosdaq150', 60),
                                      ('반도체 시간 초과', 'semiconductor', 60.01),
                                      ('주제 미확인 보충 제외', None, 40),
                                      ('미래 기사 제외', 'semiconductor', -1)]]
    actual = etf_news.jev_market_headlines(rows, now)
    assert [(n['title'], n['news_role']) for n in actual] == [
        ('시장 정확히 30분', 'priority'), ('코스닥 정확히 60분', 'topic_fallback')]
    assert all('news_role' not in row for row in rows)


def test_jev_backfill_checks_missing_topics_from_alternate_sources_and_caches_results():
    from datetime import timedelta

    now = datetime(2026, 11, 23, 12, 46, tzinfo=ZoneInfo('Asia/Seoul'))
    rows = [{'title': topic, 'topic': topic, 'source': src, 'published': (now - timedelta(minutes=age)).isoformat()}
            for topic, src, age in [('market', 'naver_finance_news', 35), ('kosdaq150', 'google_news_rss', 65),
                                    ('semiconductor', 'naver_finance_news', 76)]]
    calls = []

    def items(source, topic, query, shared, *, timeout):
        assert 0 < timeout <= 1.2
        calls.append((source, topic))
        if (source, topic) == ('google_news_rss', 'semiconductor'):
            return [('반도체 장중 시황', '매체', now - timedelta(minutes=31), '')]
        return [('코스닥 이전 기사', '매체', now - timedelta(minutes=61), '')]

    with patch.dict(etf_news._JEV_TOPIC_CACHE, {}, clear=True), patch.object(etf_news, '_items', side_effect=items):
        improved, stats = etf_news.supplement_jev_topics(rows, now)
        cached, warm = etf_news.supplement_jev_topics(rows, now + timedelta(seconds=15))
        assert cached == improved
        assert warm['probes'] == 0
        assert stats['missing_before'] == ['kosdaq150', 'semiconductor']
        assert calls == [('naver_finance_news', 'kosdaq150'), ('bing_news_rss', 'kosdaq150'),
                         ('google_news_rss', 'semiconductor')]
        result = etf_news.jev_market_headlines(improved, now)
        assert [row['topic'] for row in result] == ['market', 'semiconductor']


class NewsBackoffTests(unittest.TestCase):
    """4th review C2: a failure with no usable cache must still back off, or every caller re-reads every feed each minute."""

    def run_twice(self, items, error):
        import os, tempfile
        from datetime import timedelta
        calls = []

        def fake(source, topic, query, shared):
            calls.append(source)
            return items(source)
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {'VIBE_TRADING_NEWS_CACHE': td + '/c.json'}), \
                patch.object(etf_news, '_items', fake):
            now = datetime(2026, 11, 19, 10, 0, tzinfo=ZoneInfo('Asia/Seoul'))
            with self.assertRaises(error):
                etf_news.headlines7(now)
            first = len(calls)
            with self.assertRaises(error):
                etf_news.headlines7(now + timedelta(seconds=30))
            self.assertGreater(first, 0)
            self.assertEqual(len(calls), first)
            with self.assertRaises(error):
                etf_news.headlines7(now + etf_news.BACKOFF + timedelta(seconds=1))
            self.assertGreater(len(calls), first)  # tried again once the backoff ended

    def test_blocked_feeds_back_off_without_a_cache(self):
        def blocked(source):
            raise OSError('HTTP 429')
        self.run_twice(blocked, OSError)

    def test_no_recent_story_is_not_refetched_every_call(self):
        old = datetime(2026, 11, 19, 7, 0, tzinfo=ZoneInfo('Asia/Seoul'))
        self.run_twice(lambda source: [('코스피 코스닥 반도체 증시', 'pub', old, '')], ValueError)
