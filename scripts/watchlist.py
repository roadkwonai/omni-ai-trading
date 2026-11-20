"""Daily watchlist: Jev picks today's trading stocks from the market-movers candidates.

For each candidate it gathers stock facts (valuation, size, sector, 52-week range, foreign ownership), daily
investor net buying, same-day stock news and 1-minute bars; adds market context (KOSPI200/KOSDAQ150 ETFs and
market headlines); asks Jev once; keeps the 3 highest-probability stocks. Called by market_movers.py every
30 minutes. Writes watchlist.json; never orders.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo
from etf_universe import CODE_PATTERN
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')

from src.config.paths import get_runtime_root

KST = ZoneInfo('Asia/Seoul')
ROOT = get_runtime_root() / 'nhplug' / 'shared-etf-paper'
SIZE = 3                              # user-approved 2026-11-11
REFRESH = timedelta(minutes=30)       # user-approved 2026-11-11
MAX_AGE = timedelta(minutes=45)       # runner falls back to ETFs only after a missed refresh or two
FACTS = {'per': 'PER', 'pbr': 'PBR', 'hts_avls': '시가총액(억)', 'bstp_kor_isnm': '업종', 'w52_hgpr': '52주 최고',
         'w52_lwpr': '52주 최저', 'for_rate': '외국인 보유율(%)', 'stck_oprc': '시가', 'stck_hgpr': '고가', 'stck_lwpr': '저가'}


def stock_news(name, now, limit=3):
    """Newest same-day Google News headlines that mention the stock name (no restricted publisher, as etf_news)."""
    from etf_news import RESTRICTED_PUBLISHERS
    url = 'https://news.google.com/rss/search?q=' + quote(f'"{name}" when:1d') + '&hl=ko&gl=KR&ceid=KR:ko'
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=10) as response:
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


FETCH_STOCK_NEWS = stock_news             # the fetch fresh_stock_news uses (tests swap it)
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
    try:
        rows = (fetch or FETCH_STOCK_NEWS)(name, now)
        data[code] = {'fetched': now.isoformat(), 'rows': rows, 'retry_after': None}
    except (OSError, ValueError, ET.ParseError):
        rows = entry.get('rows') if entry else None
        data[code] = {'fetched': now.isoformat(), 'rows': rows, 'retry_after': (now + STOCK_NEWS_BACKOFF).isoformat()}
    for old in [c for c, e in data.items() if not isinstance(e, dict) or str(e.get('fetched', ''))[:10] < (now - timedelta(days=1)).isoformat()[:10]]:
        data.pop(old)
    try:
        write_json(path, data, indent=None)
    except OSError:
        pass  # an unwritten cache only costs another fetch
    return rows


def investor_days(read, code, today):
    response = read.call_tool('call_api', {'operationId': 'krstockQuoteCurrentInvestor',
                                           'input': {'market_cd': 'KRX', 'iem_cd': code, 'array_cnt': '5'}})
    data = response.get('data') if isinstance(response, dict) else None
    if response.get('status') != 'ok' or not isinstance(data, dict) or data.get('rsp_cd') != '00000':
        return None
    keys = (('frgn_ntby_qty', 'foreign'), ('gigwan', 'institution'), ('person', 'individual'))
    result = {}
    rows = [r for r in data.get('Output_0') or [] if isinstance(r, dict) and isinstance(r.get('bsop_date1'), str)
            and all(type(r.get(k)) in (int, float) for k, _ in keys) and any(r[k] for k, _ in keys)]
    for label, matches in (('last_close_day', [r for r in rows if r['bsop_date1'] < today]),
                           ('today_provisional', [r for r in rows if r['bsop_date1'] == today])):
        if matches:
            result[label] = {'date': matches[0]['bsop_date1'], **{out: matches[0][k] for k, out in keys}}
    return result


def enrich(read, candidates, now):
    """Facts, investor flows, news and bars per candidate; a candidate without fresh bars is dropped."""
    from nh_etf_paper_runner import valid_bars
    from nh_paper_isolated import fresh_series
    today = now.astimezone(KST).strftime('%Y%m%d')
    stocks, bars = {}, {}
    for code, details in candidates.items():
        try:
            quote_data = (read.call_tool('get_stock_price', {'stock_code': code}).get('data') or {}).get('Output_0') or {}
            response = read.call_tool('call_api', {'operationId': 'krstockQuotePeriod', 'input': {'market_cd': 'KRX',
                'iem_cd': code, 'gubun': '5', 'xtick': '1', 'today_cls_code': '1', 'view_main_yn': 'Y', 'array_cnt': '30'}})
            rows = valid_bars(response.get('data', {}), code, now) if response.get('status') == 'ok' else None
        except (AttributeError, TypeError, ValueError):
            continue
        if not rows or not fresh_series(rows, now) or quote_data.get('iem_cd') != code:
            continue
        info = {k: details.get(k) for k in ('name', 'index', 'reasons', 'price', 'change_pct', 'volume', 'value_mil')}
        # what Jev reads is NH's quote (2026-11-20 NH call plan P2, option 1): the ranking service may price from Kiwoom
        price, previous, volume, value = (quote_data.get(k) for k in ('stck_prpr', 'stck_prdy_clpr', 'acml_vol', 'acml_tr_pbmn'))
        if all(type(v) in (int, float) for v in (price, previous, volume, value)) and price > 0 and previous > 0:
            info.update(price=int(price), change_pct=round((price / previous - 1) * 100, 2), volume=int(volume), value_mil=int(value))
        info['facts'] = {label: quote_data.get(key) for key, label in FACTS.items() if quote_data.get(key) not in (None, '')}
        info['investor_daily_net_qty'] = investor_days(read, code, today)
        info['news'] = fresh_stock_news(code, info['name'], now) or []  # the shared 5-minute cache the runner reads too
        stocks[code], bars[code] = info, rows
    return stocks, bars


def select(read, candidates, now):
    """Ask Jev which candidate to trade today; return the watchlist record (not yet written)."""
    from etf_news import headlines7
    from etf_universe import SYMBOLS as ETFS
    from nh_etf_auto_v2 import _jev7_question
    from nh_etf_paper_runner import valid_bars
    from nh_paper_isolated import SPLIT_TRANCHE_KRW, recent_stock_headlines
    # A stock whose single share costs more than one buy tranche could never be bought; don't spend a slot on it.
    affordable = {c: d for c, d in candidates.items() if type(d.get('price')) in (int, float) and 0 < d['price'] <= SPLIT_TRANCHE_KRW}
    stocks, bars = enrich(read, affordable, now)
    if not stocks:
        raise ValueError('no candidate with fresh data')
    market = {}
    for name, code in ETFS.items():
        response = read.call_tool('call_api', {'operationId': 'krstockQuotePeriod', 'input': {'market_cd': 'KRX',
            'iem_cd': code, 'gubun': '5', 'xtick': '1', 'today_cls_code': '1', 'view_main_yn': 'Y', 'array_cnt': '30'}})
        if response.get('status') != 'ok':
            raise ValueError('market bars unavailable')
        bars[name] = valid_bars(response.get('data', {}), code, now)
    for name in ('K200', 'KQ150'):
        quote_data = (read.call_tool('get_stock_price', {'stock_code': ETFS[name]}).get('data') or {}).get('Output_0') or {}
        price, previous = quote_data.get('stck_prpr'), quote_data.get('stck_prdy_clpr')
        if type(price) in (int, float) and type(previous) in (int, float) and previous > 0:
            market[name] = {'price': price, 'change_pct': round((price / previous - 1) * 100, 2)}
    news = headlines7(now)
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
                                         'note': 'Charts labelled by 6-digit code are the candidate stocks; the others are market ETFs.'})
    verdict = answer['watchlist']
    probabilities = verdict.get('probabilities') or {verdict['choice']: verdict['confidence']}
    ranked = sorted((code for code in stocks if code in probabilities), key=lambda c: probabilities[c], reverse=True)
    return {'time': datetime.now(KST).isoformat(), 'selected': ranked[:SIZE], 'jev_choice': verdict['choice'],
            'jev_confidence': verdict['confidence'], 'probabilities': probabilities, 'stocks': stocks,
            'market': market, 'market_news': news, 'chart_sha256': answer['chart_sha256']}


def due(now, path=ROOT / 'watchlist.json'):
    """A selection is due at 09:05+ when none exists today or the last is 30 minutes old."""
    if now.astimezone(KST).strftime('%H:%M') < '09:05':
        return False
    try:
        at = datetime.fromisoformat(json.loads(Path(path).read_text())['time'])
    except (OSError, ValueError, KeyError, TypeError):
        return True
    return at.astimezone(KST).date() != now.astimezone(KST).date() or now - at >= REFRESH


def write(record, path=ROOT / 'watchlist.json'):
    tmp = Path(path).with_suffix('.tmp')
    tmp.write_text(json.dumps(record, ensure_ascii=False))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def manual(path=ROOT / 'watchlist-manual.json'):
    """User edits that persist across days: {'add': {code: name}, 'remove': [code]}."""
    try:
        data = json.loads(Path(path).read_text())
        add = {c: str(n)[:40] for c, n in (data.get('add') or {}).items() if re.fullmatch(CODE_PATTERN, str(c))}
        remove = [c for c in data.get('remove') or [] if re.fullmatch(CODE_PATTERN, str(c))]
        return {'add': add, 'remove': remove}
    except (OSError, ValueError, AttributeError):
        return {'add': {}, 'remove': []}


def edit_manual(add=None, name=None, remove=None, path=ROOT / 'watchlist-manual.json'):
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
    config_store.record(f'watchlist:{Path(path).stem}', before, state, saved=path)
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
        data = json.loads((Path(root) / 'watchlist.json').read_text())
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
        data = json.loads(Path(path).read_text())
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
