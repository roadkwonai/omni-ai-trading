import io
import unittest
from datetime import datetime
from unittest.mock import patch
from urllib.error import URLError
from zoneinfo import ZoneInfo

import etf_news
import pytest


NOW = datetime(2026, 11, 11, 11, 0, tzinfo=ZoneInfo('Asia/Seoul'))


@pytest.fixture(autouse=True)
def extra_sources(monkeypatch):
    monkeypatch.setattr('stock_material.publisher_batch', lambda slot: {})
    monkeypatch.setattr('stock_material.broker_news', lambda code, now: None)


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
            # user 2026-11-25: the cached stories serve until they leave RECENT, however long the feeds stay down
            self.assertEqual(etf_news.headlines7(NOW + timedelta(minutes=31)), first)
            with self.assertRaises(HTTPError):  # then the failure is reported, not hidden
                etf_news.headlines7(NOW + timedelta(minutes=62))
        self.assertEqual(self.run_feeds([feed('코스피 새 소식', 'Wed, 11 Nov 2026 12:10:00 +0900'), *good[1:]],
                                        NOW + timedelta(minutes=75))[0]['title'], '코스피 새 소식')

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
            for host, body in (('finance.daum.net', b'{"data": []}'), ('hankyung.com', b'<rss><channel></channel></rss>')):
                if host in request.full_url and host not in routes:  # quiet unless the test is about them
                    return Response(body)
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
            rows = etf_news.headlines7(NOW + timedelta(minutes=4))  # the next fetch starts at Naver, its two lists for all topics
            self.assertEqual([r['source'] for r in rows if r['title'] == '반도체 수출'], ['naver_finance_news'])
            self.assertIn('반도체 업황', [r['title'] for r in rows])  # Bing's earlier story is still recent: kept
            self.assertEqual((len(calls), rows[0]['published']), (2, '2026-11-11T01:30:00+00:00'))
            calls.clear()
            etf_news.headlines7(NOW + timedelta(minutes=11))  # ... then Google again (its ten minutes' rest is over), never Bing first
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
                         [('market', 'google_news_rss'), ('market', 'naver_finance_news'), ('kosdaq150', 'naver_finance_news'),
                          ('semiconductor', 'naver_finance_news')])
        self.assertEqual(rows[1]['title'], '코스닥 "강세"')

    def test_every_source_blocked_raises_a_network_error(self):
        def down(url):
            raise OSError('offline')
        calls, patcher = self.serve({'google': down, 'bing': down, 'naver': down})
        with patcher, self.assertRaises(OSError):
            etf_news.headlines7(NOW)
        self.assertEqual(len(calls), 4)  # Google, Naver's two lists, Bing

    def test_the_search_asks_google_for_the_last_two_hours_and_bing_for_its_shortest_range(self):
        # user 2026-11-24: 'when:1d' is relevance-ranked over a day and left out the newest stories (13:12 no Jev call)
        calls, patcher = self.serve({'google': lambda url: feed('무관한 기사'), 'naver': lambda url: naver('무관한 기사'),
                                     'bing': lambda url: bing('코스피 코스닥 반도체')})
        with patcher:
            etf_news.headlines7(NOW)
        google, bings = ([c for c in calls if host in c] for host in ('google', 'bing'))
        self.assertTrue(len(google) == 3 and all('when%3A2h' in c for c in google))
        self.assertTrue(bings and all('when' not in c and 'interval%3D%227%22' in c for c in bings))

    def test_naver_reads_main_and_flash_news_and_keeps_either_when_the_other_fails(self):
        def lists(main, flash):
            def answer(url):
                body = flash if 'category=flashnews' in url else main
                if body is None:
                    raise OSError('offline')
                return body
            return answer
        main, flash = naver('코스피 마감'), naver('코스피 마감', '코스닥 속보')  # the same story in both lists counts once
        for pair, expected in (((main, flash), ['코스피 마감', '코스닥 속보']), ((None, flash), ['코스피 마감', '코스닥 속보']),
                               ((main, None), ['코스피 마감'])):
            with self.subTest(pair=expected):
                _, patcher = self.serve({'naver': lists(*pair)})
                with patcher:
                    self.assertEqual([row[0] for row in etf_news._naver()], expected)
        _, patcher = self.serve({'naver': lists(None, None)})
        with patcher, self.assertRaises(OSError):
            list(etf_news._naver())


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
                                      ('코스닥 정확히 90분', 'kosdaq150', 90),
                                      ('반도체 시간 초과', 'semiconductor', 90.01),
                                      ('주제 미확인 보충 제외', None, 40),
                                      ('미래 기사 제외', 'semiconductor', -1)]]
    actual = etf_news.jev_market_headlines(rows, now)
    assert [(n['title'], n['news_role']) for n in actual] == [
        ('시장 정확히 30분', 'priority'), ('코스닥 정확히 90분', 'topic_fallback')]
    assert all('news_role' not in row for row in rows)


def test_jev_backfill_checks_missing_topics_from_alternate_sources_and_caches_results():
    from datetime import timedelta

    now = datetime(2026, 11, 23, 12, 46, tzinfo=ZoneInfo('Asia/Seoul'))
    rows = [{'title': topic, 'topic': topic, 'source': src, 'published': (now - timedelta(minutes=age)).isoformat()}
            for topic, src, age in [('market', 'naver_finance_news', 35), ('kosdaq150', 'google_news_rss', 95),
                                    ('semiconductor', 'naver_finance_news', 106)]]
    calls = []

    def items(source, topic, query, shared, *, timeout):
        assert 0 < timeout <= 1.2
        calls.append((source, topic))
        if (source, topic) == ('google_news_rss', 'semiconductor'):
            return [('반도체 장중 시황', '매체', now - timedelta(minutes=31), '')]
        return [('코스닥 이전 기사', '매체', now - timedelta(minutes=91), '')]

    with patch.dict(etf_news._JEV_TOPIC_CACHE, {}, clear=True), patch.object(etf_news, '_items', side_effect=items):
        improved, stats = etf_news.supplement_jev_topics(rows, now)
        cached, warm = etf_news.supplement_jev_topics(rows, now + timedelta(seconds=15))
        assert cached == improved
        assert warm['probes'] == 0
        assert stats['missing_before'] == ['kosdaq150', 'semiconductor']
        assert calls == [('naver_finance_news', 'kosdaq150'), ('daum_finance_news', 'kosdaq150'), ('hankyung_rss', 'kosdaq150'), ('bing_news_rss', 'kosdaq150'),
                         ('google_news_rss', 'semiconductor')]
        result = etf_news.jev_market_headlines(improved, now)
        assert [row['topic'] for row in result] == ['market', 'semiconductor']


class NewsBackoffTests(unittest.TestCase):
    """4th review C2: a failure with no usable cache must still back off, or every caller re-reads every feed each minute."""

    def run_twice(self, items, error):
        import os, tempfile
        from datetime import timedelta
        calls = []

        def fake(source, topic, query, shared, *, timeout=None):
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


class NoFreshNewsTests(unittest.TestCase):
    """User 2026-11-24: no fresh market story, or every feed down, must not stop Jev; it is told so and older stories of the
    day go along as background only."""

    def cache(self, rows, seen=(), error=None):
        import json, os, tempfile
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = folder.name + '/c.json'
        with open(path, 'w') as out:
            json.dump({'fetched': NOW.isoformat(), 'rows': rows, 'seen': list(seen), 'error': error, 'next': 0}, out)
        patcher = patch.dict(os.environ, {'VIBE_TRADING_NEWS_CACHE': path})
        patcher.start()
        self.addCleanup(patcher.stop)
        return path

    def story(self, title, minutes):
        from datetime import timedelta
        return {'title': title, 'topic': 'market', 'source': 's', 'published': (NOW - timedelta(minutes=minutes)).isoformat()}

    def test_earlier_stories_of_the_day_carry_their_age_and_stop_at_three_hours(self):
        self.cache([self.story('최근', 90), self.story('둘째', 120)],
                   [self.story('첫째', 91), self.story('둘째', 120), self.story('끝', 180), self.story('넘음', 181), {'title': '시각 없음'}])
        told = etf_news.no_fresh_news(NOW)
        self.assertEqual(told['news_status'], 'none_recent')
        self.assertEqual([(r['title'], r['age_min']) for r in told['earlier_news_headlines']], [('첫째', 91), ('둘째', 120), ('끝', 180)])
        self.assertIn('background', told['news_note'])

    def test_yesterdays_story_is_not_earlier_news(self):
        from datetime import timedelta
        night = NOW.replace(hour=1)
        self.cache([], [{**self.story('어제', 0), 'published': (night - timedelta(minutes=100)).isoformat()}])
        self.assertEqual(etf_news.no_fresh_news(night)['earlier_news_headlines'], [])

    def test_a_failed_fetch_is_told_apart_from_a_quiet_hour(self):
        self.cache([], error='OSError')
        self.assertEqual(etf_news.no_fresh_news(NOW)['news_status'], 'unavailable')

    def test_a_fetched_story_is_remembered_after_it_leaves_the_recent_window(self):
        from datetime import timedelta
        import os
        os.remove(self.cache([]))
        with patch.object(etf_news, '_fetch7', return_value=[self.story('오전 속보', 10)]):
            etf_news.headlines7(NOW)
        later = NOW + timedelta(minutes=100)
        with patch.object(etf_news, '_fetch7', side_effect=OSError('offline')), self.assertRaises(OSError):
            etf_news.headlines7(later)
        told = etf_news.no_fresh_news(later)
        self.assertEqual((told['news_status'], [(r['title'], r['age_min']) for r in told['earlier_news_headlines']]),
                         ('unavailable', [('오전 속보', 110)]))


class CacheAndFailoverTests(unittest.TestCase):
    serve = RotationTests.serve

    """User 2026-11-25: keep the newest stories coming, go round a source that fails at once, and use what is cached."""
    ALL = ('코스피 상승', '코스닥 강세', '반도체 수출')

    def at(self, minutes, seconds=0):
        from datetime import timedelta
        return NOW + timedelta(minutes=minutes, seconds=seconds)

    def stamp(self, at):
        return at.strftime('%Y%m%d%H%M%S')

    def google(self, titles=ALL):
        def answer(url):
            query = next(k for k, q in (('코스피', '%EC%BD%94%EC%8A%A4%ED%94%BC'), ('코스닥', '%EC%BD%94%EC%8A%A4%EB%8B%A5'), ('반도체', '%EB%B0%98')) if 'q=' + q in url)
            return feed(next((t for t in titles if query in t), '무관한 기사'))
        return answer

    @staticmethod
    def refused(url):
        from urllib.error import HTTPError
        raise HTTPError(url, 429, 'Too Many Requests', {}, None)

    def test_a_story_a_later_fetch_no_longer_lists_is_kept_until_it_is_old(self):
        calls, patcher = self.serve({'news.google.com': self.google(), 'stock.naver.com': lambda url: naver('무관한 기사'), 'bing.com': lambda url: bing('무관한 기사')})
        with patcher:
            etf_news.headlines7(NOW)
        calls, patcher = self.serve({'news.google.com': self.google(('코스피 새 소식',)), 'stock.naver.com': lambda url: naver('코스피 속보', dt=self.stamp(self.at(4))),
                                     'bing.com': lambda url: bing('무관한 기사')})
        with patcher:
            rows = etf_news.headlines7(self.at(5))
            self.assertEqual({r['topic'] for r in rows}, {'market', 'kosdaq150', 'semiconductor'})
            self.assertEqual([r['title'] for r in rows if r['topic'] == 'market'], ['코스피 속보', '코스피 새 소식', '코스피 상승'])
        with patch.object(etf_news, '_fetch7', side_effect=OSError('offline')):  # published 10:30: past 90 minutes it is no recent story
            self.assertEqual([r['title'] for r in etf_news.headlines7(self.at(61))], ['코스피 속보'])

    def test_naver_brings_the_newest_stories_every_cycle_without_asking_google_again(self):
        stories = ['코스피 상승']
        calls, patcher = self.serve({'news.google.com': self.google(), 'stock.naver.com': lambda url: naver(*stories, dt=self.stamp(self.at(1)))})
        with patcher:
            etf_news.headlines7(NOW)
            self.assertEqual(len(calls), 5)  # Google's three topics and Naver's two lists
            calls.clear()
            stories[:] = ['코스피 급락 속보']
            rows = etf_news.headlines7(self.at(1, 5))
            self.assertEqual(rows[0]['title'], '코스피 급락 속보')
            self.assertTrue(len(calls) == 2 and all('naver' in c for c in calls))
            etf_news.headlines7(self.at(1, 30))  # the same cycle's other callers read the cache
            self.assertEqual(len(calls), 2)

    def test_a_failed_top_up_costs_nothing(self):
        def down(url):
            raise OSError('offline')
        calls, patcher = self.serve({'news.google.com': self.google(), 'stock.naver.com': down})
        with patcher:
            first = etf_news.headlines7(NOW)
            self.assertEqual(etf_news.headlines7(self.at(1, 5)), first)
            self.assertEqual(etf_news.headlines7(self.at(1, 30)), first)
            self.assertEqual(sum('naver' in c for c in calls), 2)  # resting two minutes: not asked again

    def test_a_refused_google_is_left_alone_for_ten_minutes_and_naver_serves_at_once(self):
        calls, patcher = self.serve({'news.google.com': self.refused, 'stock.naver.com': lambda url: naver(*self.ALL, dt=self.stamp(NOW))})
        with patcher:
            self.assertEqual({r['source'] for r in etf_news.headlines7(NOW)}, {'naver_finance_news'})
            for minutes in (4, 8):
                etf_news.headlines7(self.at(minutes))
            self.assertEqual(sum('google' in c for c in calls), 1)
            etf_news.headlines7(self.at(12))
            etf_news.headlines7(self.at(16))
            self.assertEqual(sum('google' in c for c in calls), 2)

    def test_every_source_down_is_not_asked_again_and_the_first_to_return_serves(self):
        down = [True]
        def flaky(url):
            if down[0]:
                raise OSError('offline')
            return naver(*self.ALL, dt=self.stamp(self.at(2)))
        calls, patcher = self.serve({'news.google.com': self.refused, 'bing.com': self.refused, 'stock.naver.com': flaky})
        with patcher:
            with self.assertRaises(OSError):
                etf_news.headlines7(NOW)
            first = len(calls)
            with self.assertRaises(OSError):
                etf_news.headlines7(self.at(1))
            self.assertEqual(len(calls), first)
            down[0] = False
            self.assertEqual(len({r['topic'] for r in etf_news.headlines7(self.at(2, 5))}), 3)  # not the whole five minutes
            self.assertEqual(sum('google' in c for c in calls), 1)

    def test_one_slow_source_may_not_hold_the_cycle(self):
        waits = []
        def urlopen(request, timeout=None):
            waits.append(timeout)
            raise TimeoutError('timed out')
        with patch.object(etf_news.urllib.request, 'urlopen', side_effect=urlopen), self.assertRaises(OSError):
            etf_news.headlines7(NOW)
        self.assertLessEqual(sum(waits), 10)

    def test_daum_and_hankyung_fill_what_google_and_naver_miss_before_bing(self):
        import json
        asked = []
        def daum(url):
            asked.append('daum')
            rows = [{'title': '코스피 &quot;상승&quot;', 'cpKorName': '한 매체', 'createdAt': '2026-11-11 10:50:00'},
                    {'title': '코스피 속보', 'cpKorName': '연합뉴스', 'createdAt': '2026-11-11 10:55:00'},
                    {'title': '코스피 시각 오류', 'cpKorName': '한 매체', 'createdAt': 'x'}, 'x', {'title': '코스피 오래됨', 'cpKorName': '한 매체', 'createdAt': '2026-11-11 08:00:00'}]
            return json.dumps({'data': rows}, ensure_ascii=False).encode()
        def hankyung(url):
            asked.append('hankyung')
            return ('<rss><channel><item><title>반도체 수출</title><pubDate>Wed, 11 Nov 2026 10:40:00 +0900</pubDate></item></channel></rss>').encode()
        def bings(url):
            asked.append('bing')
            return bing('무관한 기사')
        calls, patcher = self.serve({'news.google.com': self.refused, 'stock.naver.com': lambda url: naver('코스닥 강세'),
                                     'finance.daum.net': daum, 'hankyung.com': hankyung, 'bing.com': bings})
        with patcher:
            rows = etf_news.headlines7(NOW)
        self.assertEqual({(r['topic'], r['source']) for r in rows if r['topic'] != 'market'},
                         {('kosdaq150', 'naver_finance_news'), ('semiconductor', 'hankyung_rss')})
        self.assertEqual(asked, ['daum', 'hankyung'])  # Bing is not needed
        self.assertEqual([r['publisher'] for r in rows if r['source'] == 'hankyung_rss'], ['한국경제'])
        calls, patcher = self.serve({'news.google.com': self.refused, 'stock.naver.com': self.refused, 'finance.daum.net': daum, 'hankyung.com': hankyung, 'bing.com': bings})
        with patch.object(etf_news, '_read_cache', return_value={}), patch.object(etf_news, '_rests', return_value={}), patcher:
            rows = etf_news.headlines7(NOW)
        self.assertEqual([r['title'] for r in rows if r['source'] == 'daum_finance_news'], ['코스피 "상승"'])

    def test_a_changed_daum_answer_moves_on(self):
        calls, patcher = self.serve({'news.google.com': self.refused, 'stock.naver.com': self.refused, 'finance.daum.net': lambda url: b'<html>',
                                     'hankyung.com': lambda url: b'<rss><channel><item><title>\xec\xbd\x94\xec\x8a\xa4\xed\x94\xbc</title><pubDate>Wed, 11 Nov 2026 10:40:00 +0900</pubDate></item></channel></rss>',
                                     'bing.com': self.refused})
        with patcher:
            self.assertEqual([r['source'] for r in etf_news.headlines7(NOW)], ['hankyung_rss'])

    def test_a_broken_reply_moves_on_to_the_next_source(self):
        import http.client
        def broken(url):
            raise http.client.IncompleteRead(b'')
        calls, patcher = self.serve({'news.google.com': broken, 'stock.naver.com': lambda url: naver(*self.ALL)})
        with patcher:
            self.assertEqual(len({r['topic'] for r in etf_news.headlines7(NOW)}), 3)

    def test_google_and_naver_giving_one_story_count_once(self):
        row = {'topic': 'market', 'published': NOW.isoformat(), 'publisher': '한 매체', 'source': 'g'}
        self.assertEqual(len(etf_news._merge([{**row, 'title': '코스피 상승 - 한 매체'}, {**row, 'title': '코스피 상승'}, {'title': 1}, 'x'], NOW)), 1)

    def test_status_reads_what_the_shared_files_say_and_asks_nobody(self):
        empty = etf_news.status(NOW)
        self.assertEqual((empty['fetched'], empty['topped'], [t['count'] for t in empty['topics']]), (None, None, [0, 0, 0]))
        calls, patcher = self.serve({'news.google.com': self.refused, 'stock.naver.com': lambda url: naver('코스피 상승', dt=self.stamp(self.at(-5))), 'bing.com': lambda url: bing('무관한 기사')})
        with patcher:
            etf_news.headlines7(NOW)
        with patch.object(etf_news.urllib.request, 'urlopen', side_effect=AssertionError('no network')):
            told = etf_news.status(self.at(1))
        self.assertEqual([(t['topic'], t['count'], t['newest_min']) for t in told['topics']], [('market', 1, 6), ('kosdaq150', 0, None), ('semiconductor', 0, None)])
        by = {s['source']: s for s in told['sources']}
        self.assertEqual((by['google_news_rss']['rest_until'], by['naver_finance_news']['rest_until']), (self.at(10), None))
        self.assertEqual((by['naver_finance_news']['count'], by['naver_finance_news']['newest_min']), (1, 6))
        self.assertIsNone({s['source']: s for s in etf_news.status(self.at(11))['sources']}['google_news_rss']['rest_until'])
