"""Recent ETF market headlines for Jev: Google News searches and Naver's lists in turn, Daum, 한국경제 and Bing behind them."""
from __future__ import annotations

import html
import http.client
import json
import os
import urllib.request
import xml.etree.ElementTree as ET
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')


# 'when:2h' restricts Google News to the last two hours (RECENT is 90 minutes). Over a day the relevance ranking left the newest
# stories out: 2026-11-24 13:32 the market search held 2 stories within 90 minutes with 'when:1d' and 8 with 'when:2h'.
FEEDS = (
    ('market', '코스피 증시 when:2h', ('코스피', '코스닥', 'kospi', 'kosdaq', '나스닥', 'nasdaq', '증시')),
    ('kosdaq150', '코스닥 when:2h', ('코스닥', 'kosdaq')),
    ('semiconductor', '반도체 주가 when:2h', ('반도체', 'semiconductor')),
)
RESTRICTED_PUBLISHERS = ('연합뉴스', 'yonhap')
# Sources are used in turn (one per fresh fetch) and a blocked or empty one hands its missing topics to the next.
SEARCH_SOURCES = ('google_news_rss', 'bing_news_rss', 'naver_finance_news')  # what longterm_news asks its own topics
# user 2026-11-25: Daum's and 한국경제's lists (HTTPS, one request for every topic) come before Bing when Google and Naver leave a topic
SOURCES = ('google_news_rss', 'naver_finance_news', 'daum_finance_news', 'hankyung_rss', 'bing_news_rss')
# headlines7 keeps Bing last: its feed is relevance-ranked over a day, so inside RECENT it rarely adds a story, and first in
# the turn it cost three requests (about 3.5 s inside the entry cycle) before the others filled the topics (r3 E9).
FALLBACK = ('daum_finance_news', 'hankyung_rss', 'bing_news_rss')
NAVER = 'naver_finance_news'
KST = timezone(timedelta(hours=9))
# Every runner (shared paper, Jev virtual, vibe advisor, watchlist) asks each minute; one shared fetch per FRESH keeps
# Google News under its rate limit (HTTP 429 from 2026-11-17 09:09 stopped every entry cycle).
FRESH = timedelta(minutes=3)
BACKOFF = timedelta(minutes=5)    # after every source failed, nobody retries them all for this long (TOP_UP still may)
# Naver's two lists cost about 0.1 s and were never refused, so between full fetches they bring the newest stories in (user
# 2026-11-25); each story stays in the shared cache and is used until it leaves RECENT, whatever a later fetch returns.
TOP_UP = timedelta(seconds=55)  # once a runner cycle
READ_TIMEOUT = 2  # seconds a source may take before the next one is asked: these reads sit inside the trading cycle
REST_REFUSED, REST_DOWN = timedelta(minutes=10), timedelta(minutes=2)  # a source that refused (429, 403) / failed is left alone
# Only recent headlines reach the engines (user 2026-11-18: "오래된 뉴스를 참고하고 판단하면 안돼"): 11/18 a third of Jev's calls
# got only Bing items 2-10 hours old (its 24-hour window). A topic without a story this recent is left out; with none at all
# headlines7 raises and Jev is told there is none (no_fresh_news). Overnight and premarket stories reach Jev through premarket_context instead.
RECENT = timedelta(minutes=90)
EARLIER = timedelta(hours=3)  # how long a fetched story stays available as background (no_fresh_news)
JEV_NEWS_PRIORITY_MINUTES = 30
JEV_NEWS_MAX_MINUTES = 90  # user 2026-11-24: 60 (11-23) left every Jev entry call without news from 13:12


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
                'next': int(data.get('next') or 0) % len(SOURCES), 'error': data.get('error'),
                'seen': list(data.get('seen') or []),
                'topped': datetime.fromisoformat(data['topped']) if data.get('topped') else None}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _write_cache(fetched: datetime, rows: list, retry_after: datetime | None = None, next_source: int = 0,
                 error: str | None = None, topped: datetime | None = None) -> None:
    from config_store import write_json
    seen = _earlier([*rows, *_read_cache().get('seen', [])], max(fetched, topped or fetched), timedelta(seconds=-1))  # no_fresh_news reads these later
    try:
        write_json(cache_path(), {'fetched': fetched.isoformat(), 'rows': rows, 'seen': seen, 'next': next_source, 'error': error,
                                  'topped': topped.isoformat() if topped else None,
                                  'retry_after': retry_after.isoformat() if retry_after else None}, indent=None)
    except OSError:
        pass  # a cache that cannot be written only costs another fetch


def _merge(rows, now):
    """Each topic's newest three stories within RECENT, each story once (Google repeats the publisher after the title)."""
    out = {topic: {} for topic, *_ in FEEDS}
    for row in rows:
        try:
            published = datetime.fromisoformat(row['published'])
            if row['topic'] in out and timedelta(0) <= now - published <= RECENT:
                out[row['topic']].setdefault(row['title'].removesuffix(f" - {row.get('publisher')}"), (published, row))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return [row for stories in out.values() for _, row in sorted(stories.values(), key=lambda x: x[0], reverse=True)[:3]]


def _rest_path() -> Path:
    return cache_path().with_name('news-sources.json')


def _rests() -> dict:
    try:
        data = json.loads(_rest_path().read_text(encoding='utf-8'))
        return {k: datetime.fromisoformat(v) for k, v in data.items()}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def resting(source: str, now: datetime) -> bool:
    """Whether a program found this source refusing or down a moment ago (shared: one refusal spares every caller)."""
    until = _rests().get(source)
    return until is not None and now < until


def rest(source: str, now: datetime, exc: Exception) -> None:
    if isinstance(exc, ValueError) and not isinstance(exc, OSError):
        return  # it answered; only the content was unusable
    from config_store import write_json
    until = now + (REST_REFUSED if getattr(exc, 'code', None) in (429, 403) else REST_DOWN)
    try:
        write_json(_rest_path(), {k: v.isoformat() for k, v in {**_rests(), source: until}.items() if v > now}, indent=None)
    except OSError:
        pass  # an unwritten rest only costs another try


def _earlier(rows, now, older_than):
    """Each story once, newest first, published more than older_than and up to EARLIER before now."""
    out = {}
    for row in rows:
        try:
            age = now - datetime.fromisoformat(row['published'])
            if older_than < age <= EARLIER:
                out.setdefault(row['title'], (age, row))
        except (KeyError, TypeError, ValueError):
            continue
    return [row for _, row in sorted(out.values(), key=lambda x: x[0])]


def no_fresh_news(now):
    """What Jev is told when no market story is fresh enough (user 2026-11-24: its calls must not stop for that): whether the
    feeds had none or failed, and today's earlier stories, kept apart from the recent ones and with their age."""
    cached = _read_cache()
    today, earlier = now.astimezone(KST).date(), []
    for row in _earlier([*cached.get('rows', []), *cached.get('seen', [])], now, timedelta(minutes=JEV_NEWS_MAX_MINUTES)):
        published = datetime.fromisoformat(row['published'])
        if published.astimezone(KST).date() == today:
            earlier.append({**{k: row[k] for k in ('title', 'topic', 'source') if k in row},
                            'age_min': round((now - published).total_seconds() / 60)})
    return {'news_status': 'unavailable' if cached.get('error') == 'OSError' else 'none_recent',
            'news_note': (f'No market headline from the last {JEV_NEWS_MAX_MINUTES} minutes was available (news_status: none_recent = '
                          'the feeds had none, unavailable = the feeds could not be read). Judge by the chart, flows and market '
                          'context; earlier_news_headlines are older stories of today, background only and probably already in the price.'),
            'earlier_news_headlines': earlier[:5]}


def jev_market_headlines(rows, now):
    """Keep recent headlines and use older ones only to fill a missing known topic."""
    topics = {topic for topic, *_ in FEEDS}
    preferred, fallback = [], []
    priority = timedelta(minutes=JEV_NEWS_PRIORITY_MINUTES)
    maximum = timedelta(minutes=JEV_NEWS_MAX_MINUTES)
    for row in rows:
        try:
            age = now - datetime.fromisoformat(row['published'])
        except (KeyError, TypeError, ValueError):
            continue
        if not timedelta(0) <= age <= maximum:
            continue
        if age <= priority:
            preferred.append({**row, 'news_role': 'priority'})
        elif row.get('topic') in topics:
            fallback.append({**row, 'news_role': 'topic_fallback'})
    covered = {row.get('topic') for row in preferred}
    return preferred + [row for row in fallback if row.get('topic') not in covered]


def status(now):
    """The shared news files as they are, for a screen (no request): when they were filled, each topic's and source's stories
    within RECENT, and until when a refusing source rests."""
    cached, rests = _read_cache(), _rests()
    pool = [*cached.get('rows', []), *cached.get('seen', [])]
    rows, stories = _merge(pool, now), {}
    for row in pool:
        try:
            if timedelta(0) <= now - datetime.fromisoformat(row['published']) <= RECENT:
                stories[row['source'], row['title']] = row
        except (KeyError, TypeError, ValueError):
            continue

    def told(kept):
        ages = [(now - datetime.fromisoformat(r['published'])).total_seconds() / 60 for r in kept]
        return {'count': len(kept), 'newest_min': round(min(ages)) if ages else None}
    return {'fetched': cached.get('fetched'), 'topped': cached.get('topped'), 'error': cached.get('error'), 'retry_after': cached.get('retry_after'),
            'topics': [{'topic': topic, **told([r for r in rows if r['topic'] == topic]),
                        'newest_title': max((r for r in rows if r['topic'] == topic), key=lambda r: r['published'], default={}).get('title')} for topic, *_ in FEEDS],
            'sources': [{'source': source, **told([r for (s, _), r in stories.items() if s == source]),
                         'rest_until': until if (until := rests.get(source)) and now < until else None} for source in SOURCES]}


def headlines7(now: datetime) -> list[dict[str, str]]:
    """The recent, relevant headlines (within RECENT; shared cache, see FRESH and SOURCES); fails closed with none."""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('news observation time requires timezone')
    cached = _read_cache()
    if cached and cached['fetched'] > now:  # written by a clock ahead of this one
        cached = {}
    pool = [*cached.get('rows', []), *cached.get('seen', [])]
    usable = _merge(pool, now)
    backing_off = bool(cached and cached['retry_after'] and now < cached['retry_after'])
    if cached and (backing_off or (usable and now - cached['fetched'] <= FRESH)):
        usable = _top_up(cached, pool, now) or usable
        if usable:
            return usable
        # the last fetch failed with nothing to fall back on: nobody retries all the feeds until BACKOFF ends
        raise (ValueError if cached.get('error') == 'ValueError' else OSError)('news feeds backing off after a failure')
    turn = [s for s in SOURCES if s not in FALLBACK] or list(SOURCES)
    first = cached.get('next', 0) % len(turn)
    order = (*turn[first:], *turn[:first], *(s for s in SOURCES if s not in turn))
    order = [s for s in order if not resting(s, now)]
    failed = {}
    try:
        if not order:
            raise OSError('every news source is resting after a failure')
        found = _fetch7(now, order, timeout=READ_TIMEOUT, failed=failed)
        rows = _merge([*found, *pool], now)
    except (OSError, ValueError, ET.ParseError) as exc:
        # 4th review: back off even with no usable cache, or every caller re-reads every source each minute (11/17 429)
        _write_cache(cached['fetched'] if usable else now, usable, now + BACKOFF, (first + 1) % len(turn),
                     'ValueError' if isinstance(exc, ValueError) else 'OSError', now)
        if usable:
            return usable
        raise
    finally:
        for source, exc in failed.items():
            rest(source, now, exc)
    read = order[0] == NAVER or any(row['source'] == NAVER for row in found)
    _write_cache(now, rows, None, (first + 1) % len(turn), topped=now if read else cached.get('topped'))
    return rows


def _top_up(cached, pool, now):
    """The cached stories with Naver's newest added, or None when it is not due, resting or brings nothing."""
    if NAVER not in SOURCES or (cached['topped'] and now - cached['topped'] < TOP_UP) or resting(NAVER, now):
        return None
    failed = {}
    try:
        rows = _merge([*_fetch7(now, (NAVER,), timeout=READ_TIMEOUT, failed=failed), *pool], now)
        error, retry = None, None
    except (OSError, ValueError, ET.ParseError):
        rows, error, retry = _merge(pool, now), cached['error'], cached['retry_after']
    for exc in failed.values():
        rest(NAVER, now, exc)
    _write_cache(cached['fetched'], rows, retry, cached['next'], error, now)
    return rows or None


def _read(url: str, *, timeout=10, headers=None) -> bytes:
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read(300_001)
    except http.client.HTTPException as exc:  # a broken reply (IncompleteRead) is no OSError
        raise OSError('news feed reply broken') from exc
    if len(payload) > 300_000:
        raise ValueError('news feed too large')
    return payload


def _rss(url: str, *, timeout=None):
    root = ET.fromstring(_read(url) if timeout is None else _read(url, timeout=timeout))
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


def _naver(*, timeout=None):
    # main news changes slowly; flash news carries the newest stories (2026-11-24). Either list alone is enough.
    rows, seen, error = [], set(), None
    for category in ('mainnews', 'flashnews'):
        url = f'https://m.stock.naver.com/api/news/list?category={category}&pageSize=50&page=1'
        try:
            data = json.loads(_read(url) if timeout is None else _read(url, timeout=timeout / 2))
            if not isinstance(data, list):
                raise ValueError('news feed invalid')
        except (OSError, ValueError) as exc:
            error = error or exc
            continue
        rows += data
    if error and not rows:
        raise error
    for row in rows:
        if (key := (row.get('tit'), row.get('dt'))) in seen:
            continue
        seen.add(key)
        try:
            published = datetime.strptime(str(row.get('dt')), '%Y%m%d%H%M%S').replace(tzinfo=KST)
        except (TypeError, ValueError, AttributeError):
            published = None
        office, article = row.get('oid') or row.get('officeId'), row.get('aid') or row.get('articleId')
        link = f'https://n.news.naver.com/mnews/article/{office}/{article}' if office and article else ''
        yield html.unescape(str(row.get('tit') or '')), html.unescape(str(row.get('ohnm') or '')), published, link


def _daum(*, timeout=None):
    data = json.loads(_read('https://finance.daum.net/content/news?page=1&perPage=100&category=stock', timeout=timeout or 10,
                            headers={'Referer': 'https://finance.daum.net/'}))  # refused without it
    if not isinstance(data, dict) or not isinstance(data.get('data'), list):
        raise ValueError('news feed invalid')
    for row in data['data']:
        if not isinstance(row, dict):
            continue
        try:
            published = datetime.strptime(str(row.get('createdAt')), '%Y-%m-%d %H:%M:%S').replace(tzinfo=KST)
        except ValueError:
            published = None
        yield html.unescape(str(row.get('title') or '')), html.unescape(str(row.get('cpKorName') or '')), published, ''


def _hankyung(*, timeout=None):
    for title, publisher, published, link in _rss('https://www.hankyung.com/feed/all-news', timeout=timeout):
        yield title, publisher or '한국경제', published, link  # its feed names no source: every story is its own


LISTS = {'naver_finance_news': lambda **kw: _naver(**kw), 'daum_finance_news': lambda **kw: _daum(**kw), 'hankyung_rss': lambda **kw: _hankyung(**kw)}


def _items(source: str, topic: str, query: str, shared: dict, *, timeout=None):
    if source in LISTS:  # one list serves every topic
        if source not in shared:
            shared[source] = list(LISTS[source](timeout=timeout))
        return shared[source]
    if source == 'bing_news_rss':
        url = ('https://www.bing.com/news/search?q=' + quote(re.sub(r' when:\d+[dh]', '', query)) +
               '&format=rss&setlang=ko&cc=KR&qft=' + quote('interval="%s"' % ('8' if re.search(r' when:(?!1d)\d+d', query) else '7')))  # 7 = last 24 hours, 8 = last 7 days
    else:
        url = 'https://news.google.com/rss/search?q=' + quote(query) + '&hl=ko&gl=KR&ceid=KR:ko'
    return _rss(url) if timeout is None else _rss(url, timeout=timeout)


def _topic_rows(items, now, topic, keywords, source, recent, links=False):
    kept = []
    for title, publisher, published, *rest in items:
        title, publisher = (title or '').strip(), (publisher or '').strip()
        if published is None or published.tzinfo is None or published.utcoffset() is None:
            continue
        published = published.astimezone(timezone.utc)
        text = (title + ' ' + publisher).casefold()
        if (not title or not publisher or not any(keyword in title.casefold() for keyword in keywords)
                or any(name in text for name in RESTRICTED_PUBLISHERS)
                or not timedelta(0) <= now - published <= recent):
            continue
        kept.append({'title': title[:200], 'published': published.isoformat(), 'source': source,
                     'topic': topic, 'publisher': publisher[:80], **({'link': str(rest[0])[:500]} if links and rest and rest[0] else {})})
    return sorted(kept, key=lambda row: row['published'], reverse=True)[:3]


_JEV_TOPIC_CACHE = {}
JEV_TOPIC_BUDGET_SECONDS = 2.4


def supplement_jev_topics(rows, now):
    """Probe alternate feeds only for missing topics, with per-topic cache/backoff."""
    import time

    settings = {topic: (query, keys) for topic, query, keys in FEEDS}
    original = {row['topic'] for row in rows if row.get('topic') in settings}
    if not original:
        return rows, None  # no topic provenance to supplement
    selected = {row.get('topic') for row in jev_market_headlines(rows, now)}
    missing = [topic for topic in settings if topic not in selected]
    if not missing:
        return rows, None

    start = time.monotonic()
    collected, shared, probes = [], {}, 0
    sources = (*[source for source in SOURCES if source not in FALLBACK], *FALLBACK)
    for topic in missing:
        prior = frozenset(row.get('source') for row in rows if row.get('topic') == topic)
        key = (topic, prior)
        cached = _JEV_TOPIC_CACHE.get(key)
        if cached and timedelta(0) <= now - cached[0] < (FRESH if cached[1] else BACKOFF):
            collected.extend(cached[1])
            continue
        query, keywords = settings[topic]
        found, attempted = [], False
        for source in sources:
            if source in prior or resting(source, now):
                continue
            remaining = JEV_TOPIC_BUDGET_SECONDS - (time.monotonic() - start)
            if remaining <= 0:
                break
            attempted = True
            probes += 1
            try:
                items = list(_items(source, topic, query, shared, timeout=min(1.2, remaining)))
                found = _topic_rows(items, now, topic, keywords, source, timedelta(minutes=JEV_NEWS_MAX_MINUTES))
            except (OSError, ValueError, ET.ParseError) as exc:
                if getattr(exc, 'code', None) in (429, 403):  # a timeout under this short budget says little
                    rest(source, now, exc)
                continue
            if found:
                break
        if attempted:
            _JEV_TOPIC_CACHE[key] = (now, found)
        collected.extend(found)
    return [*rows, *collected], {'missing_before': missing, 'probes': probes,
                                 'elapsed_ms': round((time.monotonic() - start) * 1000, 1)}


def _fetch7(now: datetime, order=SOURCES, feeds=FEEDS, recent=RECENT, links=False, *, timeout=None, failed=None) -> list[dict[str, str]]:
    """The sources in `order`, a blocked or empty one handing its missing topics to the next. feeds / recent / links: the
    long-term news (longterm_news) asks its own topics over a day and keeps each story's link; the runners' defaults stay.
    failed: filled with {source: its error}."""
    observed = now.astimezone(timezone.utc)
    found, error, shared = {}, None, {}
    for source in order:
        for topic, query, keywords in feeds:
            if topic in found:
                continue
            try:
                items = list(_items(source, topic, query, shared) if timeout is None else _items(source, topic, query, shared, timeout=timeout))
            except (OSError, ValueError, ET.ParseError) as exc:
                error = exc
                if failed is not None:
                    failed[source] = exc
                break  # blocked, offline or malformed: the next source takes the remaining topics
            kept = _topic_rows(items, observed, topic, keywords, source, recent, links)
            if kept:
                found[topic] = kept
            else:
                error = ValueError('no eligible recent news for ' + topic)
        if len(found) == len(feeds):
            return [row for topic, _, _ in feeds for row in found[topic]]
    if found:  # a topic no source covered recently is left out rather than filled with old stories
        return [row for topic, _, _ in feeds for row in found.get(topic, [])]
    raise error or ValueError('no recent news')
