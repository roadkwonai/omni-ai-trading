"""Recent ETF market headlines from three independent Google News searches."""
from __future__ import annotations

import html
import json
import os
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')


# 'when:1d' restricts Google News to the last day; without it the relevance-ranked feed held one stale item per topic.
FEEDS = (
    ('market', '코스피 증시 when:1d', ('코스피', '코스닥', 'kospi', 'kosdaq', '나스닥', 'nasdaq', '증시')),
    ('kosdaq150', '코스닥 when:1d', ('코스닥', 'kosdaq')),
    ('semiconductor', '반도체 주가 when:1d', ('반도체', 'semiconductor')),
)
RESTRICTED_PUBLISHERS = ('연합뉴스', 'yonhap')
# Sources are used in turn (one per fresh fetch) and a blocked or empty one hands its missing topics to the next.
SOURCES = ('google_news_rss', 'bing_news_rss', 'naver_finance_news')
# headlines7 keeps Bing last: its feed is relevance-ranked over a day, so inside RECENT it rarely adds a story, and first in
# the turn it cost three requests (about 3.5 s inside the entry cycle) before the others filled the topics (r3 E9).
FALLBACK = ('bing_news_rss',)
KST = timezone(timedelta(hours=9))
# Every runner (shared paper, Jev virtual, vibe advisor, watchlist) asks each minute; one shared fetch per FRESH keeps
# Google News under its rate limit (HTTP 429 from 2026-11-17 09:09 stopped every entry cycle).
FRESH = timedelta(minutes=3)
STALE_OK = timedelta(minutes=30)  # a network failure (429, timeout) reuses the last good set this long
BACKOFF = timedelta(minutes=5)    # and nobody retries the feed for this long after a failure
# Only recent headlines reach the engines (user 2026-11-18: "오래된 뉴스를 참고하고 판단하면 안돼"): 11/18 a third of Jev's calls
# got only Bing items 2-10 hours old (its 24-hour window). A topic without a story this recent is left out; with none at all
# the call fails closed (the runner waits). Overnight and premarket stories reach Jev through premarket_context instead.
RECENT = timedelta(minutes=90)


def cache_path() -> Path:
    override = os.environ.get('VIBE_TRADING_NEWS_CACHE', '').strip()
    if override:
        return Path(override)
    home = Path(os.environ.get('VIBE_TRADING_HOME') or Path.home() / '.vibe-trading').expanduser()
    return home / 'nhplug' / 'news-cache.json'


def _read_cache() -> dict:
    try:
        data = json.loads(cache_path().read_text(encoding='utf-8'))
        return {'fetched': datetime.fromisoformat(data['fetched']), 'rows': list(data['rows']),
                'retry_after': datetime.fromisoformat(data['retry_after']) if data.get('retry_after') else None,
                'next': int(data.get('next') or 0) % len(SOURCES), 'error': data.get('error')}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _write_cache(fetched: datetime, rows: list, retry_after: datetime | None = None, next_source: int = 0,
                 error: str | None = None) -> None:
    from config_store import write_json
    try:
        write_json(cache_path(), {'fetched': fetched.isoformat(), 'rows': rows, 'next': next_source, 'error': error,
                                  'retry_after': retry_after.isoformat() if retry_after else None}, indent=None)
    except OSError:
        pass  # a cache that cannot be written only costs another fetch


def _still_recent(rows: list, now: datetime) -> list | None:
    """Cached rows re-checked against now: each must still be within RECENT; None when none is."""
    try:
        kept = [r for r in rows if timedelta(0) <= now - datetime.fromisoformat(r['published']) <= RECENT]
    except (KeyError, TypeError, ValueError):
        return None
    return kept or None


def headlines7(now: datetime) -> list[dict[str, str]]:
    """The recent, relevant headlines (within RECENT; shared cache, see FRESH and SOURCES); fails closed with none."""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('news observation time requires timezone')
    cached = _read_cache()
    age = now - cached['fetched'] if cached else None
    usable = _still_recent(cached['rows'], now) if cached and timedelta(0) <= age <= STALE_OK else None
    backing_off = bool(cached and cached['retry_after'] and now < cached['retry_after'])
    if usable and (age <= FRESH or backing_off):
        return usable
    if backing_off:  # the last fetch failed with nothing to fall back on: nobody retries the feeds until BACKOFF ends
        raise (ValueError if cached.get('error') == 'ValueError' else OSError)('news feeds backing off after a failure')
    turn = [s for s in SOURCES if s not in FALLBACK] or list(SOURCES)
    first = cached.get('next', 0) % len(turn)
    order = (*turn[first:], *turn[:first], *(s for s in SOURCES if s not in turn))
    try:
        rows = _fetch7(now, order)
    except (OSError, ValueError, ET.ParseError) as exc:
        # 4th review: back off even with no usable cache, or every caller re-reads every source each minute (11/17 429)
        _write_cache(cached['fetched'] if usable else now, cached['rows'] if usable else [], now + BACKOFF,
                     (first + 1) % len(turn), 'ValueError' if isinstance(exc, ValueError) else 'OSError')
        if usable:
            return usable
        raise
    _write_cache(now, rows, None, (first + 1) % len(turn))
    return rows


def _read(url: str) -> bytes:
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = response.read(300_001)
    if len(payload) > 300_000:
        raise ValueError('news feed too large')
    return payload


def _rss(url: str):
    root = ET.fromstring(_read(url))
    if root.tag != 'rss' or root.find('channel') is None:
        raise ValueError('news feed invalid')
    for item in root.findall('./channel/item'):
        # Google names the publisher <source>; Bing puts it in a namespaced <...}Source>.
        publisher = item.findtext('source') or next((c.text for c in item if c.tag.endswith('}Source')), '')
        try:
            published = parsedate_to_datetime(item.findtext('pubDate'))
        except (TypeError, ValueError, OverflowError):
            published = None
        yield item.findtext('title'), publisher, published, (item.findtext('link') or '').strip()


def _naver():
    data = json.loads(_read('https://m.stock.naver.com/api/news/list?category=mainnews&pageSize=50&page=1'))
    if not isinstance(data, list):
        raise ValueError('news feed invalid')
    for row in data:
        try:
            published = datetime.strptime(str(row.get('dt')), '%Y%m%d%H%M%S').replace(tzinfo=KST)
        except (TypeError, ValueError, AttributeError):
            published = None
        office, article = row.get('oid') or row.get('officeId'), row.get('aid') or row.get('articleId')
        link = f'https://n.news.naver.com/mnews/article/{office}/{article}' if office and article else ''
        yield html.unescape(str(row.get('tit') or '')), html.unescape(str(row.get('ohnm') or '')), published, link


def _items(source: str, topic: str, query: str, shared: dict):
    if source == 'naver_finance_news':  # one list serves every topic
        if 'naver' not in shared:
            shared['naver'] = list(_naver())
        return shared['naver']
    if source == 'bing_news_rss':
        return _rss('https://www.bing.com/news/search?q=' + quote(query.replace(' when:1d', '')) +
                    '&format=rss&setlang=ko&cc=KR&qft=' + quote('interval="7"'))  # interval 7 = last 24 hours
    return _rss('https://news.google.com/rss/search?q=' + quote(query) + '&hl=ko&gl=KR&ceid=KR:ko')


def _fetch7(now: datetime, order=SOURCES, feeds=FEEDS, recent=RECENT, links=False) -> list[dict[str, str]]:
    """The sources in `order`, a blocked or empty one handing its missing topics to the next. feeds / recent / links: the
    long-term news (longterm_news) asks its own topics over a day and keeps each story's link; the runners' defaults stay."""
    observed = now.astimezone(timezone.utc)
    found, error, shared = {}, None, {}
    for source in order:
        for topic, query, keywords in feeds:
            if topic in found:
                continue
            try:
                items = list(_items(source, topic, query, shared))
            except (OSError, ValueError, ET.ParseError) as exc:
                error = exc
                break  # blocked, offline or malformed: the next source takes the remaining topics
            kept = []
            for title, publisher, published, *rest in items:
                title, publisher = (title or '').strip(), (publisher or '').strip()
                if published is None or published.tzinfo is None or published.utcoffset() is None:
                    continue
                published = published.astimezone(timezone.utc)
                text = (title + ' ' + publisher).casefold()
                if (not title or not publisher or not any(keyword in title.casefold() for keyword in keywords)
                        or any(name in text for name in RESTRICTED_PUBLISHERS)
                        or not timedelta(0) <= observed - published <= recent):
                    continue
                kept.append({'title': title[:200], 'published': published.isoformat(), 'source': source,
                             'topic': topic, 'publisher': publisher[:80], **({'link': str(rest[0])[:500]} if links and rest and rest[0] else {})})
            if kept:
                # Feeds are ordered by relevance; take the newest three so yesterday's story doesn't crowd out today's.
                found[topic] = sorted(kept, key=lambda row: row['published'], reverse=True)[:3]
            else:
                error = ValueError('no eligible recent news for ' + topic)
        if len(found) == len(feeds):
            return [row for topic, _, _ in feeds for row in found[topic]]
    if found:  # a topic no source covered recently is left out rather than filled with old stories
        return [row for topic, _, _ in feeds for row in found.get(topic, [])]
    raise error or ValueError('no recent news')
