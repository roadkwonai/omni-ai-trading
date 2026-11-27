"""Direct publisher headlines and stock-code disclosures for Jev."""
import html
import fcntl
import http.client
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from zoneinfo import ZoneInfo


FEEDS = {
    '이투데이': 'https://rss.etoday.co.kr/eto/etoday_news_all.xml',
    '머니투데이': 'https://rss.mt.co.kr/mt_news.xml',
    '한국경제': 'https://www.hankyung.com/feed/all-news',
    '이데일리': 'http://rss.edaily.co.kr/edaily_news.xml',
    '서울경제': 'https://www.sedaily.com/RSS/Finance',
    'MTN': 'https://www.mtn.co.kr/rss',
    '인포스탁데일리': 'https://www.infostockdaily.co.kr/rss/allArticle.xml',
    '이뉴스투데이': 'https://www.enewstoday.co.kr/rss/allArticle.xml',
    '프라임경제': 'https://www.newsprime.co.kr/data/rss/news.xml',
    '딜사이트경제TV': 'https://news.dealsitetv.com/rss/021000.xml',
    '데이터투자뉴스': 'https://www.datatooza.com/rss_feed.php',
    '아시아경제': 'https://view.asiae.co.kr/rss/all.htm',
    'AI타임스': 'https://www.aitimes.com/rss/allArticle.xml',
    'Investing.com': 'https://kr.investing.com/rss/news_25.rss',
}
READ_ERRORS = (OSError, ValueError, ET.ParseError, http.client.HTTPException)


@contextmanager
def cache_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a', encoding='utf-8') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def read_cache(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def broker_news(code, now):
    import kis_data
    if not kis_data.configured():
        return None
    try:
        return kis_data.news_titles(code, now)
    except kis_data.KISError as exc:
        raise OSError(str(exc)) from None


def read(url, timeout=2):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read(2_000_001)
    if len(body) > 2_000_000:
        raise ValueError('feed too large')
    return body


def publisher_rows(source, url):
    channel = ET.fromstring(read(url)).find('channel')
    if channel is None:
        raise ValueError('RSS channel missing')
    rows = []
    for item in channel.findall('item'):
        raw = item.findtext('pubDate') or item.findtext('{http://purl.org/dc/elements/1.1/}date')
        try:
            at = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            try:
                at = datetime.fromisoformat(raw or '')
            except ValueError:
                continue
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc if source == 'Investing.com' else ZoneInfo('Asia/Seoul'))
        title = html.unescape(item.findtext('title') or '').strip()
        if title and at.tzinfo is not None:
            rows.append({'title': title[:200], 'publisher': source,
                         'published': at.astimezone(timezone.utc).isoformat(), 'link': item.findtext('link') or ''})
    return rows


def publisher_batch(slot):
    from etf_news import cache_path
    path = cache_path().with_suffix('.publishers.json')
    return _publisher_batch(path, slot, tuple(FEEDS.items()))


@lru_cache(maxsize=4)
def _publisher_batch(path, slot, feeds):
    from config_store import write_json

    def fetch(item):
        source, url = item
        try:
            return source, publisher_rows(source, url)
        except READ_ERRORS:
            return source, None
    try:
        with cache_lock(path.with_suffix('.lock')):
            data = read_cache(path)
            if data.get('slot') == slot and data.get('feeds') == dict(feeds) and isinstance(data.get('rows'), dict):
                return data['rows']
            with ThreadPoolExecutor(max_workers=8) as pool:
                rows = dict(pool.map(fetch, feeds))
            try:
                write_json(path, {'slot': slot, 'feeds': dict(feeds), 'rows': rows}, indent=None)
            except OSError:
                pass
            return rows
    except OSError:
        return {source: None for source, _ in feeds}


def direct_news(name, now):
    from etf_news import RESTRICTED_PUBLISHERS
    rows, status = [], {}
    for source, articles in publisher_batch(int(now.timestamp()) // 300).items():
        matched = [row for row in articles or [] if name in row['title']
                   and timedelta(0) <= now - datetime.fromisoformat(row['published']) <= timedelta(hours=24)
                   and not any(p in (row['title'] + ' ' + source).casefold() for p in RESTRICTED_PUBLISHERS)]
        status[source] = 'error' if articles is None else 'found' if matched else 'empty'
        rows.extend(matched)
    return rows, status


@lru_cache(maxsize=2)
def daum_batch(slot):
    from etf_news import _daum
    return list(_daum(timeout=2))


def daum_stock_news(name, now):
    from etf_news import RESTRICTED_PUBLISHERS
    rows = []
    for title, publisher, published, link in daum_batch(int(now.timestamp()) // 300):
        if (name in title and published is not None and published.tzinfo is not None
                and timedelta(0) <= now - published <= timedelta(hours=24)
                and not any(p in (title + ' ' + publisher).casefold() for p in RESTRICTED_PUBLISHERS)):
            rows.append({'title': title[:200], 'publisher': publisher[:80],
                         'published': published.isoformat(), 'link': link})
    return rows


@lru_cache(maxsize=128)
def disclosure_batch(code, slot):
    try:
        rows = json.loads(read(f'https://m.stock.naver.com/api/stock/{code}/disclosure?pageSize=20&page=1'))
        if not isinstance(rows, list):
            raise ValueError('invalid disclosure list')
    except READ_ERRORS:
        return None
    return rows


@lru_cache(maxsize=4)
def dart_corps(path, modified):
    from defusedxml import ElementTree
    return {(row.findtext('stock_code') or '').strip(): (row.findtext('corp_code') or '').strip()
            for row in ElementTree.parse(path).getroot().findall('list')}


@lru_cache(maxsize=128)
def dart_batch(code, slot, day):
    import requests
    from dotenv import dotenv_values
    from local_env import ENV
    from src.config.paths import get_runtime_root

    key = dotenv_values(ENV, interpolate=False).get('OPENDART_API_KEY')
    path = get_runtime_root() / 'cache' / 'opendart-corpcode.xml'
    try:
        corp = dart_corps(path, path.stat().st_mtime_ns).get(code, '')
    except (OSError, ValueError, ET.ParseError):
        corp = ''
    if not key or not re.fullmatch(r'[0-9]{8}', corp):
        return 'unavailable', []
    try:
        response = requests.get('https://opendart.fss.or.kr/api/list.json', timeout=2, allow_redirects=False, params={
            'crtfc_key': key, 'corp_code': corp, 'bgn_de': (day - timedelta(days=3)).strftime('%Y%m%d'),
            'end_de': day.strftime('%Y%m%d'), 'page_count': 20})
        if response.status_code != 200:
            return 'error', []
        data = response.json()
        if not isinstance(data, dict) or data.get('status') not in ('000', '013'):
            return 'error', []
        items = []
        for row in data.get('list') or []:
            receipt, number, title = row.get('rcept_dt'), row.get('rcept_no'), row.get('report_nm')
            if not (isinstance(title, str) and title.strip() and re.fullmatch(r'[0-9]{14}', str(number))):
                continue
            at = datetime.strptime(str(receipt), '%Y%m%d').date()
            if timedelta(0) <= day - at <= timedelta(days=3):
                items.append({'title': title[:160], 'date': at.isoformat(), 'source': 'OpenDART',
                              'link': 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=' + number})
        return ('partial' if int(data.get('total_page') or 0) > 1 else 'found' if items else 'empty'), items[:3]
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        return 'error', []


def disclosures(code, now):
    if len(code) != 6 or not code.isascii() or not code.isalnum():
        return {'status': 'invalid_code', 'items': []}
    rows = disclosure_batch(code, int(now.timestamp()) // 300)
    items = []
    for row in rows or []:
        if not isinstance(row, dict) or row.get('itemCode') != code:
            continue
        try:
            stamp = datetime.fromisoformat(str(row.get('datetime')))
        except ValueError:
            continue
        # The provider can omit a timezone. Keep its date without guessing the release time.
        day = stamp.astimezone(now.tzinfo).date() if stamp.tzinfo else stamp.date()
        title = str(row.get('title') or '').strip()
        if title and timedelta(0) <= now.date() - day <= timedelta(days=3) and (not stamp.tzinfo or stamp <= now):
            items.append({'title': title[:160], 'date': day.isoformat(),
                          'source': str(row.get('author') or '네이버 증권')[:40]})
    naver_status = 'error' if rows is None else 'found' if items else 'empty'
    dart_status, dart_items = dart_batch(code, int(now.timestamp()) // 900, now.date())
    merged = {(row['date'], ''.join(row['title'].split())): row for row in [*items, *dart_items]}
    return {'sources': {'naver_disclosure': naver_status, 'opendart': dart_status},
            'checked_at': now.isoformat(),
            'items': sorted(merged.values(), key=lambda row: row['date'], reverse=True)[:4]}
