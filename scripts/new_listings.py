"""Recent new listings (IPOs) from KRX KIND's 신규상장기업현황, for the runner's new-listing entry.

KIND only proposes names (Toss's stock master when KIND fails): the runner trades one only after NH's own quote
confirms the same listing date
(`stck_lstn_date`). Regular common stocks newly listed (주권 · 신규상장) are kept; SPACs are skipped. The list is
fetched at most once a day (retried every 15 minutes after a failure) and cached in new-listings.json. Never orders.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from config_store import write_json
from etf_universe import CODE_PATTERN
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')

KST = ZoneInfo('Asia/Seoul')
URL = 'https://kind.krx.co.kr/listinvstg/listingcompany.do'
FILE = 'new-listings.json'
LOOKBACK_DAYS = 14
RETRY = timedelta(minutes=15)


def parse(html):
    """Rows of KIND's listing table (Excel form): name, code, listing date, kept only for new common-stock listings."""
    rows = []
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S | re.I):
        cells = [re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', c)).strip() for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.S | re.I)]
        if len(cells) < 5:
            continue
        name, code, listed, kind, security = cells[:5]
        if (re.fullmatch(CODE_PATTERN, code) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', listed) and kind == '신규상장'
                and security == '주권' and '스팩' not in name):
            rows.append({'name': name, 'code': code, 'listed': listed.replace('-', '')})
    return rows


def fetch(now):
    import requests
    session = requests.Session()
    session.headers['User-Agent'] = 'Mozilla/5.0'
    session.get(URL, params={'method': 'searchListingTypeMain'}, timeout=15)
    start = (now - timedelta(days=LOOKBACK_DAYS)).strftime('%Y-%m-%d')
    data = {'method': 'searchListingTypeSub', 'currentPageSize': '100', 'pageIndex': '1', 'orderMode': '1', 'orderStat': 'D',
            'forward': 'listingtype_down', 'searchCodeType': '', 'repIsuSrtCd': '', 'isurCd': '', 'searchCorpName': '',
            'marketType': '', 'listTypeArrStr': '01|02|03|04|05|', 'choicTypeArrStr': '', 'secuGrpArrStr': '0|ST|FS|MF|SC|RT|IF|DR|',
            'industry': '', 'repMajAgntComp': '', 'designAdvserComp': '', 'fromDate': start, 'toDate': now.strftime('%Y-%m-%d')}
    response = session.post(URL, data=data, headers={'Referer': URL + '?method=searchListingTypeMain'}, timeout=15)
    response.raise_for_status()
    rows = parse(response.text)
    if '신규상장기업현황' not in response.text:
        raise ValueError('KIND listing page changed')
    return rows


def _toss_recent(now):
    import toss_data
    if not toss_data.configured():
        raise RuntimeError('Toss key missing')
    return toss_data.recent_listings(now, LOOKBACK_DAYS)


def _daily(path, now, answer):
    """Today's cached `answer(now)` (a dict with ok and rows): fetched once a day, again RETRY after a failure.

    The cache only saves a refetch, so an unwritable folder never hides the answer (the dashboard crashed on it once)."""
    try:
        cache = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        cache = {}
    day, checked = now.strftime('%Y%m%d'), cache.get('checked')
    if not (cache.get('day') == day and (cache.get('ok') or (checked and now - datetime.fromisoformat(checked) < RETRY))):
        cache = {'day': day, 'checked': now.isoformat(), **answer(now)}
        try:
            write_json(path, cache, indent=None)
        except OSError:
            pass
    return cache


def recent_cache(root, now, fetcher=fetch, fallback=_toss_recent):
    """The day's recent-listings answer: {'ok', 'rows', 'source'?, 'error'?, 'fallback_error'?}.

    When KIND fails, Toss's stock master (listing dates of the last LOOKBACK_DAYS) stands in (user-approved 2026-11-17);
    that answer is kept with ok False so KIND is tried again after RETRY. The runner still trades a name only after
    NH's quote confirms the listing date."""
    def answer(now):
        try:
            return {'ok': True, 'rows': fetcher(now)}
        except Exception as exc:  # network or page change: the fallback this time, KIND again later
            out = {'ok': False, 'rows': [], 'error': type(exc).__name__}
            try:
                out.update(rows=fallback(now), source='Toss Open API')
            except Exception as fallback_exc:
                out['fallback_error'] = type(fallback_exc).__name__
            return out
    return _daily(Path(root) / FILE, now, answer)


def recent(root, now, fetcher=fetch, fallback=_toss_recent):
    """Cached recent listings (newest first); [] when neither KIND nor its fallback can be read today."""
    return sorted(recent_cache(root, now, fetcher, fallback).get('rows') or [], key=lambda r: r['listed'], reverse=True)


UPCOMING_FILE = 'upcoming-listings.json'


def _toss_scheduled(now):
    import toss_data
    if not toss_data.configured():
        raise RuntimeError('Toss key missing')
    return toss_data.scheduled_listings(now)


def upcoming_cache(root, now, fetcher=_toss_scheduled):
    """The day's scheduled-listings answer (Toss Open API): {'ok', 'rows', 'source', 'error'?}.

    Reference for the pre-market brief and the dashboard only: `recent()` (KIND) stays the runner's source, and the
    runner still trades a new listing only after NH confirms the listing date."""
    def answer(now):
        try:
            return {'ok': True, 'source': 'Toss Open API', 'rows': fetcher(now)}
        except Exception as exc:  # no key, network or API change: nothing this time, try again later
            return {'ok': False, 'source': 'Toss Open API', 'rows': [], 'error': type(exc).__name__}
    return _daily(Path(root) / UPCOMING_FILE, now, answer)


if __name__ == '__main__':
    print(json.dumps(recent(Path.home() / '.vibe-trading/nhplug/shared-etf-paper', datetime.now(KST)), ensure_ascii=False, indent=1))
