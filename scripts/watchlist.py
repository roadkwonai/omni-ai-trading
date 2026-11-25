"""Daily watchlist: Jev picks today's trading stocks from the market-movers candidates.

For each candidate it gathers stock facts (valuation, size, sector, 52-week range, foreign ownership), daily
investor net buying, same-day stock news and 1-minute bars; adds market context (KOSPI200/KOSDAQ150 ETFs and
market headlines); asks Jev once; keeps the 3 highest-probability stocks. Called by market_movers.py every
30 minutes. Writes watchlist.json; never orders.
"""
from __future__ import annotations

import html
import http.client
import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo
from etf_universe import CODE_PATTERN, SYMBOLS as ETF_CODES
from kiwoom_data import foreign_net as FOREIGN
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')

from src.config.paths import get_runtime_root

KST = ZoneInfo('Asia/Seoul')
ROOT = get_runtime_root() / 'nhplug' / 'shared-etf-paper'
SIZE = 3                              # user-approved 2026-11-11
REFRESH = timedelta(minutes=30)       # user-approved 2026-11-11
FIRST_PICK, FULL_BARS = '09:01', '09:08'  # moved with the exchange clock
OPENING_REFRESH = timedelta(minutes=3)
MAX_AGE = timedelta(minutes=45)       # runner falls back to ETFs only after a missed refresh or two
FACTS = {'per': 'PER', 'pbr': 'PBR', 'hts_avls': '시가총액(억)', 'bstp_kor_isnm': '업종', 'w52_hgpr': '52주 최고',
         'w52_lwpr': '52주 최저', 'for_rate': '외국인 보유율(%)', 'stck_oprc': '시가', 'stck_hgpr': '고가', 'stck_lwpr': '저가'}


def stock_news(name, now, limit=3, *, timeout=3):
    """Newest same-day Google News headlines that mention the stock name (no restricted publisher, as etf_news)."""
    from etf_news import RESTRICTED_PUBLISHERS
    url = 'https://news.google.com/rss/search?q=' + quote(f'"{name}" when:1d') + '&hl=ko&gl=KR&ceid=KR:ko'
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        root = ET.fromstring(response.read(300_001))
    rows = []
    for item in root.findall('./channel/item'):
        title = (item.findtext('title') or '').strip()
        try:
            published = parsedate_to_datetime(item.findtext('pubDate')).astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            continue
        publisher = (item.findtext('source') or '').strip()
        if (name in title and timedelta(0) <= now.astimezone(timezone.utc) - published <= timedelta(hours=24)
                and not any(p in (title + ' ' + publisher).casefold() for p in RESTRICTED_PUBLISHERS)):
            rows.append({'title': title[:200], 'published': published.isoformat(), 'publisher': publisher[:80]})
    return sorted(rows, key=lambda r: r['published'], reverse=True)[:limit]


def naver_stock_news(code, name, now, limit=3, *, timeout=3):
    """The same from Naver's list of the stock's own news (user 2026-11-25: what stands in when Google refuses)."""
    from etf_news import RESTRICTED_PUBLISHERS
    request = urllib.request.Request(f'https://m.stock.naver.com/api/news/stock/{quote(code)}?pageSize=20&page=1', headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        groups = json.loads(response.read(1_000_001))
    if not isinstance(groups, list):
        raise ValueError('stock news invalid')
    rows = {}
    for item in (i for g in groups if isinstance(g, dict) for i in g.get('items') or [] if isinstance(i, dict)):
        title, publisher = html.unescape(str(item.get('title') or '')).strip(), html.unescape(str(item.get('officeName') or '')).strip()
        try:
            published = datetime.strptime(str(item.get('datetime')), '%Y%m%d%H%M').replace(tzinfo=KST).astimezone(timezone.utc)
        except ValueError:
            continue
        if (name in title and timedelta(0) <= now.astimezone(timezone.utc) - published <= timedelta(hours=24)
                and not any(p in (title + ' ' + publisher).casefold() for p in RESTRICTED_PUBLISHERS)):
            rows.setdefault(title, {'title': title[:200], 'published': published.isoformat(), 'publisher': publisher[:80]})
    return sorted(rows.values(), key=lambda r: r['published'], reverse=True)[:limit]


FETCH_STOCK_NEWS = stock_news             # the fetch fresh_stock_news uses (tests swap it)
FETCH_STOCK_FALLBACK = naver_stock_news   # asked when that one fails or Google is resting (etf_news.resting)
STOCK_NEWS_TTL = timedelta(minutes=5)      # user 2026-11-18: each watchlist stock's own news refreshed every 5 minutes
STOCK_NEWS_BACKOFF = timedelta(minutes=5)  # after a failed fetch (429, timeout) nobody retries that stock for this long


def stock_news_cache_path():
    override = os.environ.get('VIBE_TRADING_STOCK_NEWS_CACHE', '').strip()
    if override:
        return Path(override)
    home = Path(os.environ.get('VIBE_TRADING_HOME') or Path.home() / '.vibe-trading').expanduser()
    return home / 'nhplug' / 'stock-news-cache.json'


def fresh_stock_news(code, name, now, fetch=None):
    """One watchlist stock's newest headlines, refreshed every STOCK_NEWS_TTL through a shared cache (the selection
    every 30 minutes left Jev with stock news up to 35 minutes behind). A failed fetch returns the cached rows and
    backs off; the caller still filters by age (nh_paper_isolated.recent_stock_headlines). None when unknown."""
    from config_store import write_json
    path = stock_news_cache_path()
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        data = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        data = {}
    entry = data.get(code) if isinstance(data.get(code), dict) else None
    try:
        fetched = datetime.fromisoformat(entry['fetched']) if entry else None
        retry = datetime.fromisoformat(entry['retry_after']) if entry and entry.get('retry_after') else None
    except (TypeError, ValueError, KeyError):
        fetched = retry = None
    if fetched and (timedelta(0) <= now - fetched < STOCK_NEWS_TTL or (retry and now < retry)):
        return entry.get('rows')
    if not name:
        return entry.get('rows') if entry else None
    import etf_news
    google = 'google_news_rss'
    try:
        rows = None
        if not etf_news.resting(google, now):
            try:
                rows = (fetch or FETCH_STOCK_NEWS)(name, now)
            except (OSError, ValueError, ET.ParseError, http.client.HTTPException) as exc:
                etf_news.rest(google, now, exc)
        if rows is None:
            rows = FETCH_STOCK_FALLBACK(code, name, now)
        kept = {r.get('title'): r for r in [*(entry.get('rows') or [] if entry else []), *rows]}  # a story a source no longer lists stays
        rows = sorted(kept.values(), key=lambda r: str(r.get('published')), reverse=True)[:3]
        data[code] = {'fetched': now.isoformat(), 'rows': rows, 'retry_after': None}
    except (OSError, ValueError, ET.ParseError, http.client.HTTPException):
        rows = entry.get('rows') if entry else None
        data[code] = {'fetched': now.isoformat(), 'rows': rows, 'retry_after': (now + STOCK_NEWS_BACKOFF).isoformat()}
    for old in [c for c, e in data.items() if not isinstance(e, dict) or str(e.get('fetched', ''))[:10] < (now - timedelta(days=1)).isoformat()[:10]]:
        data.pop(old)
    try:
        write_json(path, data, indent=None)
    except OSError:
        pass  # an unwritten cache only costs another fetch
    return rows


FOREIGN_RETRY = 300  # seconds no code is asked again after a Kiwoom failure, so one outage costs one wait
_FOREIGN = {}        # (code, closed day) -> shares, and 'failed' -> monotonic seconds of the last failure


def closed_foreign(code, day):
    """Kiwoom's foreign net quantity of a closed day (it does not change again), or None."""
    if (code, day) not in _FOREIGN:
        if 'failed' in _FOREIGN and time.monotonic() - _FOREIGN['failed'] < FOREIGN_RETRY:
            return None
        try:
            _FOREIGN[code, day] = FOREIGN(code, day)
        except Exception:  # noqa: BLE001 - an optional figure
            _FOREIGN['failed'] = time.monotonic()
            return None
    return _FOREIGN[code, day]


def investor_days(read, code, today):
    """Daily investor net quantities of one code (a stock or an ETF): the last closed day and today's provisional row; None
    when they cannot be read. From NH, except the closed day's foreign figure, which is Kiwoom's or left out, and an ETF's
    foreign figure of today, which is never sent (user-approved 2026-11-25: against Naver over three days NH's closed-day
    figure was off by 36% (median) and Kiwoom's by 1.6%; NH's ETF figure of 09:00 stood all day, two of three with the
    wrong sign). NH sends 0 for a type it has not tallied yet (27 of 27 stock days whose foreign row was 0
    in the session closed at another figure, 11-11~11-24), so today's zeros are named as not tallied instead."""
    response = read.call_tool('call_api', {'operationId': 'krstockQuoteCurrentInvestor',
                                           'input': {'market_cd': 'KRX', 'iem_cd': code, 'array_cnt': '5'}})
    data = response.get('data') if isinstance(response, dict) else None
    if (response.get('status') != 'ok' or not isinstance(data, dict) or data.get('rsp_cd') != '00000'
            or not isinstance(data.get('Output_0'), list)):
        return None
    keys = (('frgn_ntby_qty', 'foreign'), ('gigwan', 'institution'), ('person', 'individual'))
    result = {}
    rows = [r for r in data['Output_0'] if isinstance(r, dict) and isinstance(r.get('bsop_date1'), str)
            and all(type(r.get(k)) in (int, float) for k, _ in keys) and any(r[k] for k, _ in keys)]
    for label, matches in (('last_close_day', [r for r in rows if r['bsop_date1'] < today]),
                           ('today_provisional', [r for r in rows if r['bsop_date1'] == today])):
        if matches:
            row = {out: matches[0][k] for k, out in keys}
            if label == 'last_close_day':
                row.pop('foreign')
                if (foreign := closed_foreign(code, matches[0]['bsop_date1'])) is not None:
                    row = {'foreign': foreign, **row}
            elif code in ETF_CODES.values():
                row['foreign'] = 0
                if not any(row.values()):
                    continue
            if label == 'today_provisional' and (missing := [out for out, v in row.items() if not v]):
                row = {**{out: v for out, v in row.items() if v}, 'not_tallied_yet': missing}
            result[label] = {'date': matches[0]['bsop_date1'], **row}
    return result


def chart(read, code, now, preopen=False, premarket=None):
    """1-minute rows of one chart: six settled bars of today, or around the bell what the runner's own opening charts hold
    (today's settled bars and the NXT pre-market's / the previous session's); ValueError or None when there are none."""
    from nh_etf_paper_runner import valid_bars
    from nh_paper_isolated import entry_rows, opening_bars, opening_window
    opening = preopen or opening_window(now)
    if preopen and premarket:
        return premarket(code)
    response = read.call_tool('call_api', {'operationId': 'krstockQuotePeriod', 'input': {'market_cd': 'KRX',
        'iem_cd': code, 'gubun': '5', 'xtick': '1', 'today_cls_code': '0' if opening else '1', 'view_main_yn': 'Y', 'array_cnt': '30'}})
    if response.get('status') != 'ok':
        return None
    data = response.get('data', {})
    return opening_bars(data, code, now) if preopen else entry_rows(data, code, now, True, premarket) if opening else valid_bars(data, code, now)


def enrich(read, candidates, now, preopen=False):
    """Facts, investor flows, news and bars per candidate; a candidate without fresh bars is dropped. preopen: before the
    bell the bars and figures are the NXT pre-market's (NH still quotes yesterday)."""
    from nh_paper_isolated import fresh_series, nxt_premarket, opening_series
    today = now.astimezone(KST).strftime('%Y%m%d')
    stocks, bars = {}, {}
    for code, details in candidates.items():
        try:
            quote_data = (read.call_tool('get_stock_price', {'stock_code': code}).get('data') or {}).get('Output_0') or {}
            rows = chart(read, code, now, preopen, lambda c: nxt_premarket(c, now))
        except (AttributeError, TypeError, ValueError):
            continue
        if not rows or len(rows) < 6 or not (preopen or fresh_series(rows, now) or opening_series(rows, now)) or quote_data.get('iem_cd') != code:
            continue
        info = {k: details.get(k) for k in ('name', 'index', 'reasons', 'price', 'change_pct', 'volume', 'value_mil')}
        # what Jev reads is NH's quote (2026-11-20 NH call plan P2, option 1): the ranking service may price from Kiwoom
        price, previous, volume, value = (quote_data.get(k) for k in ('stck_prpr', 'stck_prdy_clpr', 'acml_vol', 'acml_tr_pbmn'))
        if not preopen and all(type(v) in (int, float) for v in (price, previous, volume, value)) and price > 0 and previous > 0:
            info.update(price=int(price), change_pct=round((price / previous - 1) * 100, 2), volume=int(volume), value_mil=int(value))
        info['facts'] = {label: quote_data.get(key) for key, label in FACTS.items() if quote_data.get(key) not in (None, '')}
        info['investor_daily_net_qty'] = investor_days(read, code, today)
        info['news'] = fresh_stock_news(code, info['name'], now) or []  # the shared 5-minute cache the runner reads too
        stocks[code], bars[code] = info, rows
    return stocks, bars


def select(read, candidates, now, preopen=False):
    """Ask Jev which candidate to trade today; return the watchlist record (not yet written). preopen: before the bell."""
    from etf_news import headlines7
    from etf_universe import SYMBOLS as ETFS
    from nh_etf_auto_v2 import _jev7_question
    from nh_paper_isolated import SPLIT_TRANCHE_KRW, recent_stock_headlines
    # A stock whose single share costs more than one buy tranche could never be bought; don't spend a slot on it.
    affordable = {c: d for c, d in candidates.items() if type(d.get('price')) in (int, float) and 0 < d['price'] <= SPLIT_TRANCHE_KRW}
    stocks, bars = enrich(read, affordable, now, preopen)
    if not stocks:
        raise ValueError('no candidate with fresh data')
    market = {}
    for name, code in ETFS.items():
        rows = chart(read, code, now, preopen)
        if not rows:
            raise ValueError('market bars unavailable')
        bars[name] = rows
    for name in () if preopen else ('K200', 'KQ150'):  # before the bell NH still gives yesterday's change
        quote_data = (read.call_tool('get_stock_price', {'stock_code': ETFS[name]}).get('data') or {}).get('Output_0') or {}
        price, previous = quote_data.get('stck_prpr'), quote_data.get('stck_prdy_clpr')
        if type(price) in (int, float) and type(previous) in (int, float) and previous > 0:
            market[name] = {'price': price, 'change_pct': round((price / previous - 1) * 100, 2)}
    try:
        news = headlines7(now)
    except (OSError, ValueError, ET.ParseError):  # user 2026-11-24: the pick goes on without market news and Jev is told so
        news = []
    from overnight_catalysts import for_candidates
    try:
        overnight = for_candidates(stocks, now)
    except Exception:  # noqa: BLE001 - background for the pick, never a reason not to pick
        overnight = None
    criteria = {'NONE': 'no stock is suitable to trade today',
                **{code: f"{s['name']} ({s.get('index')}): {', '.join(s.get('reasons') or [])}" for code, s in stocks.items()}}
    answer = _jev7_question(bars, news, 'watchlist', criteria,
                            instructions=('Which one Korean stock is the best candidate to trade today in a paper account, '
                                          'judging momentum and liquidity on the chart, its own news, investor flows, valuation '
                                          'and the market backdrop? Choose NONE if none is suitable.'),
                            # each stock's news within etf_news.RECENT, as the runner's Jev calls get it (r3 E8)
                            extra_state={'stocks': {code: {**s, 'news': recent_stock_headlines(s.get('news'), now, limit=3)}
                                                    for code, s in stocks.items()},
                                         'market_etf_change_pct': market,
                                         **({'overnight_material': overnight} if overnight else {}),
                                         'note': 'Charts labelled by 6-digit code are the candidate stocks; the others are market ETFs.'
                                                 + (' The session has not opened: stock charts and figures are today\'s NXT pre-market (08:00~08:50), '
                                                    'ETF charts the previous session\'s.' if preopen else '')})
    verdict = answer['watchlist']
    probabilities = verdict.get('probabilities') or {verdict['choice']: verdict['confidence']}
    ranked = sorted((code for code in stocks if code in probabilities), key=lambda c: probabilities[c], reverse=True)
    return {'time': datetime.now(KST).isoformat(), 'selected': ranked[:SIZE], 'jev_choice': verdict['choice'],
            'jev_confidence': verdict['confidence'], 'probabilities': probabilities, 'stocks': stocks,
            'market': market, 'market_news': news, 'overnight': overnight, 'chart_sha256': answer['chart_sha256']}


def due(now, path=ROOT / 'watchlist.json'):
    """From one minute after the bell (user 2026-11-25: until then Jev had no stock to choose before 09:10), again every
    OPENING_REFRESH while the charts are opening ones and once six settled bars are there (FULL_BARS), then every REFRESH."""
    from session_times import shifted
    local = now.astimezone(KST)
    start, full = (shifted(t, local.date()) for t in (FIRST_PICK, FULL_BARS))
    clock = local.strftime('%H:%M')
    if clock < start:
        return False
    try:
        at = datetime.fromisoformat(json.loads(Path(path).read_text(encoding='utf-8'))['time'])
    except (OSError, ValueError, KeyError, TypeError):
        return True
    at = at.astimezone(KST)
    if at.date() != local.date():
        return True
    if at.strftime('%H:%M') < full:
        return clock >= full or now - at >= OPENING_REFRESH
    return now - at >= REFRESH


def write(record, path=ROOT / 'watchlist.json'):
    from config_store import replace
    tmp = Path(path).with_suffix('.tmp')
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
    os.chmod(tmp, 0o600)
    replace(tmp, path)


def manual(path=ROOT / 'watchlist-manual.json'):
    """User edits that persist across days: {'add': {code: name}, 'remove': [code]}."""
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        add = {c: str(n)[:40] for c, n in (data.get('add') or {}).items() if re.fullmatch(CODE_PATTERN, str(c))}
        remove = [c for c in data.get('remove') or [] if re.fullmatch(CODE_PATTERN, str(c))]
        return {'add': add, 'remove': remove}
    except (OSError, ValueError, AttributeError):
        return {'add': {}, 'remove': []}


def edit_manual(add=None, name=None, remove=None, path=ROOT / 'watchlist-manual.json', who='dashboard'):
    """Add one code (with its display name) or remove one code; adding undoes an earlier removal and vice versa."""
    import config_store
    state = manual(path)
    before = json.loads(json.dumps(state))
    for code in (add, remove):
        if code is not None and not re.fullmatch(CODE_PATTERN, str(code)):
            raise ValueError('종목코드는 6자리 숫자입니다.')
    if add:
        state['add'][add] = str(name or add)[:40]
        state['remove'] = [c for c in state['remove'] if c != add]
    if remove:
        state['add'].pop(remove, None)
        state['remove'] = sorted(set(state['remove']) | {remove})
    config_store.write_json(path, state, indent=None)
    config_store.record(f'watchlist:{Path(path).stem}', before, state, who, saved=path)
    return state


def manual_path(account='paper-shared', root=ROOT):
    """Each account keeps its own add/remove edits; the paper runner reads the shared paper account's file."""
    if not re.fullmatch(r'[a-z0-9-]{1,40}', str(account)):
        raise ValueError('계좌 키가 올바르지 않습니다.')
    return Path(root) / ('watchlist-manual.json' if account == 'paper-shared' else f'watchlist-manual-{account}.json')


def account_watchlist(account, root=ROOT):
    """[(code, name, source)]: the latest Jev selection minus this account's removals, plus its additions."""
    edits = manual(manual_path(account, root))
    try:
        data = json.loads((Path(root) / 'watchlist.json').read_text(encoding='utf-8'))
        stocks = data.get('stocks') or {}
        selected = [c for c in data.get('selected') or [] if isinstance(c, str) and re.fullmatch(CODE_PATTERN, c)]
    except (OSError, ValueError, AttributeError):
        stocks, selected = {}, []
    rows = [(c, str((stocks.get(c) or {}).get('name') or c).lstrip('*'), 'Jev') for c in selected if c not in edits['remove']]
    shown = {c for c, _, _ in rows}
    return rows + [(c, n, '추가') for c, n in edits['add'].items() if c not in shown]


def load_candidates(now, path=ROOT / 'watchlist.json', manual_path=ROOT / 'watchlist-manual.json'):
    """Trading stocks {code: details}: today's fresh Jev selection minus user removals, plus user additions."""
    edits = manual(manual_path)
    result = {}
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        at = datetime.fromisoformat(data['time'])
        if at.tzinfo is not None and at.astimezone(KST).date() == now.astimezone(KST).date() and timedelta(0) <= now - at <= MAX_AGE:
            result = {code: data['stocks'][code] for code in data['selected']
                      if isinstance(code, str) and re.fullmatch(CODE_PATTERN, code) and code in data['stocks']}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    for code in edits['remove']:
        result.pop(code, None)
    for code, name in edits['add'].items():
        result.setdefault(code, {'name': name, 'reasons': ['사용자 관심종목']})
    return result
