"""매매일지 (trading journal): one record per trading day, kept for days, months and years (user 2026-11-13).

Each day gathers, after the close:
- every fill of the day: the shared paper account (Jev), vibe-trading's virtual book and each live account, with
  realized P&L, round trips, routes and exits;
- performance: realized P&L, return on the day's buys, wins/losses, average win/loss %, profit factor, best and worst
  trade and symbol, the minute the day's realized P&L peaked;
- the market: KOSPI/KOSDAQ, Nasdaq, S&P 500, Dow, SOX, VIX, gold, WTI, bitcoin, ethereum, USD/KRW, US 10-year yield,
  the latest market-context snapshot (investor/program flows, themes) when built on the day;
- issues by perspective (Google News, that day): market, US stocks, rates, FX, commodities, crypto, war, politics,
  geopolitics, earnings, innovation, semiconductors, analyst calls; and the analyst consensus of the traded codes;
- the system: runner health, entry gates, guard usage, restriction blocks, errors; the 16:15 improvement report;
- the perspective written by the daily journal task (reviews/journal-YYYY-MM-DD.json): a view per category, the
  retrospective, strategy and system improvements and notes for the engines.
Stored in shared-etf-paper/trading-journal.sqlite3. `period()` pools any range; `brief()` is what Jev and vibe-trading
read before trading. Research and records only; never orders.

  PYTHONPATH=agent:scripts .venv/bin/python scripts/trading_journal.py [--day YYYY-MM-DD] [--backfill N] [--merge]
"""
from __future__ import annotations

import argparse
import functools
import json
import re
import sqlite3
import statistics
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import journal_template as T
from etf_news import RESTRICTED_PUBLISHERS  # their headlines are not stored
from market_context import naver as _naver, num as _num  # one Naver client for the dashboard
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')

KST = ZoneInfo('Asia/Seoul')
RUNTIME = Path.home() / '.vibe-trading/nhplug'
ROOT = RUNTIME / 'shared-etf-paper'
DB = ROOT / 'trading-journal.sqlite3'
REVIEWS = ROOT / 'reviews'
MARKET = {'코스피': '^KS11', '코스닥': '^KQ11', '나스닥': '^IXIC', 'S&P500': '^GSPC', '다우': '^DJI', '필라델피아 반도체': '^SOX',
          'VIX': '^VIX', '금 선물': 'GC=F', 'WTI 유가': 'CL=F', '비트코인': 'BTC-USD', '이더리움': 'ETH-USD',
          '원/달러': 'KRW=X', '미 국채 10년': '^TNX'}
# perspective -> Google News query (Korean); one day's headlines each
ISSUES = {'시황': '코스피 마감', '미국 증시': '뉴욕증시 OR 나스닥 OR 다우', '금리·연준': '연준 OR 기준금리 OR 국채금리', '환율': '원달러 환율',
          '원자재': '국제유가 OR 금값', '코인': '비트코인', '전쟁·분쟁': '전쟁 OR 공습 OR 휴전', '정치': '정치 증시 OR 대통령 증시',
          '지정학': '지정학 OR 관세 OR 제재', '기업 실적': '실적 발표 OR 영업이익 OR 어닝', '혁신 발표': '신제품 공개 OR AI 발표',
          '반도체': '반도체 OR HBM', '애널리스트': '목표주가 OR 투자의견'}
QUOTE_LINKS = {name: f"https://finance.yahoo.com/quote/{quote(symbol)}" for name, symbol in MARKET.items()}


def stock_link(code):
    return f'https://m.stock.naver.com/domestic/stock/{code}/total'


def research_link(nid):
    return f'https://finance.naver.com/research/company_read.naver?nid={nid}' if nid else None


def news_search_link(title):
    return 'https://news.google.com/search?q=' + quote(f'"{title}"') + '&hl=ko&gl=KR&ceid=KR:ko'


# ── trades ──────────────────────────────────────────────────────────────────
def _live_rows(key, path='미러'):
    """A live account's recorded broker fills as journal rows (moving-average P&L like the engine ledgers)."""
    import live_fills
    import engine_compare as ec
    held, avg, out = {}, {}, []
    for r in live_fills.rows(RUNTIME, key):
        h, a, cost, pnl, ret = ec.apply_fill(held.get(r['symbol'], 0), avg.get(r['symbol'], 0.0), r['side'], r['qty'], r['price'])
        held[r['symbol']], avg[r['symbol']] = h, a
        # the engine journal's row shape (engine_compare.journal): positions()/summary() read after, avg and cost
        out.append({'time': r['time'], 'symbol': r['symbol'], 'side': r['side'], 'qty': r['qty'], 'price': r['price'],
                    'amount': r['qty'] * r['price'], 'avg': a if r['side'] == 'BUY' else (cost / r['qty'] if cost else None),
                    'pnl': pnl, 'cost': cost, 'return_pct': ret, 'after': h, 'path': path, 'basis': ''})
    return out


def books():
    """[(key, label, rows)] of every book: the shared paper account (Jev), the long-term virtual book (vibe), each live account."""
    import engine_compare as ec
    out = [('paper', '모의 (Jev)', ec.journal(ROOT / 'orders.sqlite3', ROOT / 'events.jsonl', 'jev'))]
    try:
        import vibe_longterm  # vibe is the long-term engine since 2026-11-17; its old intraday book is frozen
        out.append(('longterm', '장기투자 (vibe 가상)', vibe_longterm.journal_rows(RUNTIME / 'vibe-longterm')))
    except Exception:
        pass
    try:
        from accounts import load
        for key, record in load(RUNTIME / 'accounts.json').items():
            if key.startswith('live'):
                name = str(record.get('label') or key)
                label = name if name.startswith('실계좌') else f'실계좌 {name}'
                if record.get('engine') == 'longterm':  # user 2026-11-17: a live account on the long-term engine
                    out.append((key, f'{label} (장기투자)', _live_rows(key, '장기투자')))
                else:
                    out.append((key, label, _live_rows(key)))
    except Exception:
        pass
    return out


def performance(rows):
    """The day's (or period's) numbers from journal rows with realized P&L on sells."""
    sells = [r for r in rows if r['side'] == 'SELL' and r.get('pnl') is not None]
    buys = [r for r in rows if r['side'] == 'BUY']
    wins, losses = [r for r in sells if r['pnl'] > 0], [r for r in sells if r['pnl'] < 0]
    bought = sum(r['amount'] for r in buys)
    realized = round(sum(r['pnl'] for r in sells))
    by_symbol = defaultdict(float)
    for r in sells:
        by_symbol[r['symbol']] += r['pnl']
    curve, running, peak = [], 0.0, None
    for r in sorted(sells, key=lambda r: r['time']):
        running += r['pnl']
        curve.append((r['time'], round(running)))
        if running > 0 and (peak is None or running > peak[1]):  # a day that never went positive has no peak
            peak = (r['time'], round(running))
    return {'buys': len(buys), 'sells': len(sells), 'bought_krw': round(bought), 'realized_krw': realized,
            'return_on_buys_pct': round(realized / bought * 100, 2) if bought else None,
            'wins': len(wins), 'losses': len(losses), 'win_rate': round(len(wins) / len(sells) * 100) if sells else None,
            'avg_win_pct': round(statistics.mean(r['return_pct'] for r in wins if r.get('return_pct') is not None), 2) if wins else None,
            'avg_loss_pct': round(statistics.mean(r['return_pct'] for r in losses if r.get('return_pct') is not None), 2) if losses else None,
            'profit_factor': round(sum(r['pnl'] for r in wins) / -sum(r['pnl'] for r in losses), 2) if losses and wins else None,
            'best_trade': max(wins, key=lambda r: r['pnl'], default=None), 'worst_trade': min(losses, key=lambda r: r['pnl'], default=None),
            'by_symbol': {k: round(v) for k, v in sorted(by_symbol.items(), key=lambda kv: -kv[1])},
            'peak': {'time': peak[0], 'realized_krw': peak[1]} if peak else None, 'curve': curve}


def trades(day):
    """{book key: {'label', 'fills', 'performance', 'round_trips'}} for one day."""
    import daily_improvement as di
    out = {}
    for key, label, rows in books():
        mine = [r for r in rows if str(r['time']).startswith(day)]
        trips = []
        if key == 'paper':  # the long-term book holds positions for months: no same-day round trips
            try:
                trips = di.round_trips(rows, day, di.paths_today(ROOT, day))
            except Exception:
                trips = []
        keep = ('time', 'symbol', 'side', 'qty', 'price', 'amount', 'pnl', 'return_pct', 'path')
        out[key] = {'label': label, 'fills': [{k: r.get(k) for k in keep} for r in mine], 'performance': performance(mine),
                    'round_trips': trips}
    return out


# ── market and issues ───────────────────────────────────────────────────────
def _daily_range(target):
    """Yahoo daily range that still reaches `target` (3 months covered only the last quarter)."""
    age = (datetime.now(KST).date() - target).days
    return '3mo' if age < 80 else '2y' if age < 700 else '10y'


KOREAN_INDICES = ('^KS11', '^KQ11')


def _started_by_close(symbol, stamp, target):
    """Whether a daily bar had started by the Korean close of `target`. Judged by its own-exchange date, because Yahoo
    stamps a still-open bar with its last trade time: a US cash session dated `target` opens after the Korean close;
    futures (=F), crypto and FX bars dated `target` began before it."""
    if stamp.date() != target:
        return stamp.date() < target
    return symbol in KOREAN_INDICES or symbol.endswith('=F') or not str(stamp.tzinfo).startswith('America/')


def market(day):
    """{name: {'close', 'change_pct', 'on'}} as of the Korean close (15:30 KST) of `day`: only daily bars that had
    started by then, so a later build of a past day gets what a build on the day saw (US markets: the night before,
    not the session that opens after the Korean close). A Korean index whose `day` bar is missing is left out rather
    than shown with an older day's close."""
    import oversold_research as research
    target = date.fromisoformat(day)
    out = {}
    for name, symbol in MARKET.items():
        bars = [b for b in research.fetch(symbol, '1d', _daily_range(target)) if _started_by_close(symbol, b[0], target)]
        if symbol in KOREAN_INDICES and bars and bars[-1][0].date() != target:
            continue
        if len(bars) >= 2:
            out[name] = {'close': round(bars[-1][4], 2), 'change_pct': round((bars[-1][4] / bars[-2][4] - 1) * 100, 2),
                         'on': bars[-1][0].date().isoformat()}
    return out


def headlines(query, day, limit=6, timeout=8):
    """[{'title', 'publisher'}] of one Google News search restricted to `day` (Korean); None when it could not be asked."""
    d = date.fromisoformat(day)
    url = ('https://news.google.com/rss/search?q=' + quote(f'{query} after:{d - timedelta(days=1)} before:{d + timedelta(days=1)}')
           + '&hl=ko&gl=KR&ceid=KR:ko')
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=timeout) as response:
            root = ET.fromstring(response.read(400_000))
    except Exception:
        return None
    out = []
    for item in root.findall('./channel/item'):
        title, publisher = (item.findtext('title') or '').strip(), (item.findtext('source') or '').strip()
        if not title or any(p.lower() in publisher.lower() for p in RESTRICTED_PUBLISHERS):
            continue
        out.append({'title': re.sub(r'\s+-\s+[^-]+$', '', title)[:120], 'publisher': publisher[:40],
                    'link': (item.findtext('link') or '').strip() or news_search_link(title)})
        if len(out) >= limit:
            break
    return out


ISSUE_RETRY_WAIT = 30  # seconds before the topics Google refused (429) are asked once more


def issues(day):
    found = {name: headlines(query, day) for name, query in ISSUES.items()}
    failed = [name for name, items in found.items() if items is None]
    if failed:
        time.sleep(ISSUE_RETRY_WAIT)
        found.update({name: headlines(ISSUES[name], day) for name in failed})
    return {name: items or [] for name, items in found.items()}


def refill_issues(record):
    """Ask again for the topics stored without an article (a refused search looks the same); True when one was filled."""
    kept = record.get('issues') or {}
    found = {name: items for name in kept if not kept[name] and name in ISSUES and (items := headlines(ISSUES[name], record['day']))}
    kept.update(found)
    return bool(found)


def engine_news(day, limit=15, lookups=10):
    """The headlines the engines were actually given that day (the runner's market_context events), most seen first,
    with how many minutes each was in front of them and a link (the article for the top ones, else a search)."""
    seen = {}
    try:
        with open(ROOT / 'events.jsonl', encoding='utf-8') as stream:
            for line in stream:
                if f'"{day}' not in line[:40] or '"news"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                for n in e.get('news') or []:
                    title = str(n.get('title') or '')
                    if title:
                        item = seen.setdefault(title, {'title': re.sub(r'\s+-\s+[^-]+$', '', title)[:160], 'publisher': n.get('publisher'),
                                                       'topic': n.get('topic'), 'published': n.get('published'), 'minutes': 0})
                        item['minutes'] += 1
    except OSError:
        return []
    top = sorted(seen.values(), key=lambda x: -x['minutes'])[:limit]
    for i, item in enumerate(top):
        found = headlines(f'"{item["title"][:80]}"', day, limit=1) if i < lookups else []
        item['link'] = found[0]['link'] if found else news_search_link(item['title'])
    return top


def analysts(codes):
    import oversold
    out = {}
    for code in list(dict.fromkeys(codes))[:10]:
        info = oversold.analyst(code)
        if info:
            out[code] = {k: info.get(k) for k in ('recomm_mean', 'target_mean', 'report_tone')} | {
                'link': stock_link(code),
                'reports': [{'text': f"{r['date']} {r['broker']}: {r['title']}", 'link': research_link(r.get('nid'))}
                            for r in (info.get('reports') or [])[:3]]}
    return out


# ── investor flows ──────────────────────────────────────────────────────────


def _sessions_back(day, n):
    d, out = date.fromisoformat(day), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out


def market_flows(day, history=5):
    """{'KOSPI'|'KOSDAQ': {'개인', '외국인', '기관' (억 원, net buy), 'history': [[day, 개인, 외국인, 기관], ...]}} from Naver's
    daily investor trend (by date); the day's program trading and breadth come from the close snapshot when built then."""
    out = {}
    for code in ('KOSPI', 'KOSDAQ'):
        rows = []
        for d in _sessions_back(day, history):
            try:
                r = _naver(f'index/{code}/trend?bizdate={d:%Y%m%d}')
            except Exception:
                continue
            if isinstance(r, dict) and r.get('bizdate') == f'{d:%Y%m%d}':
                values = [_num(r.get(k)) for k in ('personalValue', 'foreignValue', 'institutionalValue')]
                if any(values):  # a holiday answers with zeros
                    rows.append([d.isoformat(), *values])
        today = next((r for r in rows if r[0] == day), None)
        out[code] = {'개인': today[1], '외국인': today[2], '기관': today[3]} if today else {}
        out[code]['history'] = list(reversed(rows))
        detail = investor_detail(code, day)
        if detail:
            out[code]['detail'] = detail
    return out


def investor_detail(code, day):
    """Toss's KRX investor trading value for KOSPI/KOSDAQ with the institution's seven kinds (금융투자·보험·투신·사모·은행·
    기타금융·연기금), net 억 원; None without a Toss key or record. Its foreigners include unregistered ones, so its totals
    differ from Naver's: shown beside them, never mixed in."""
    import toss_data
    if not toss_data.configured():
        return None
    try:
        return toss_data.market_investors(code, day)
    except Exception:  # Toss down or refused: the Naver figures stand alone
        return None


def stock_flows(day, codes, prices=None):
    """{code: {'외국인'|'기관'|'개인': (shares, 억 원 approx.)}} of the traded codes that day (Naver; the latest day can be provisional)."""
    out = {}
    target = date.fromisoformat(day)
    for code in codes:
        try:
            rows = _naver(f'stock/{code}/trend?pageSize=5&bizdate={target + timedelta(days=1):%Y%m%d}')
        except Exception:
            continue
        row = next((r for r in rows or [] if r.get('bizdate') == f'{target:%Y%m%d}'), None)
        if not row:
            continue
        price = _num(row.get('closePrice')) or (prices or {}).get(code)
        shares = {'외국인': _num(row.get('foreignerPureBuyQuant')), '기관': _num(row.get('organPureBuyQuant')),
                  '개인': _num(row.get('individualPureBuyQuant'))}
        out[code] = {k: {'shares': v, 'value_100m': round(v * price / 1e8, 1) if v is not None and price else None} for k, v in shares.items()}
        out[code]['provisional'] = row.get('closePrice') in (None, '-')
    return out


def stock_pressure(day, codes):
    """{code: short selling / credit / securities lending} of the traded codes as of `day` (Toss Open API), {} without
    a Toss key. Credit lands the next business day's early morning, so a journal built at the close shows the day before
    for it (each part carries its own `date`); a later rebuild refreshes it."""
    import toss_data
    if not toss_data.configured():
        return {}
    out = {}
    for code in codes:
        try:
            out[code] = toss_data.stock_pressure(code, day)
        except Exception as exc:  # Toss down or refused: the rest of the journal is built as before
            out[code] = {'error': type(exc).__name__}
    return out


PRESSURE_PARTS = ('short', 'credit', 'lending')


def pressure_parts(entry):
    """(short, credit, lending) of one stock_pressure() entry; {} for a part that is missing or failed."""
    return tuple(entry.get(k) if isinstance(entry.get(k), dict) and 'error' not in entry[k] else {} for k in PRESSURE_PARTS)


def same_day_leaders(leaders, day):
    """Only the investors ranked on `day`: during the session Toss still serves the previous day's 개인 ranking."""
    return {who: side for who, side in (leaders or {}).items() if str(side.get('ranked_at') or '')[:10] == day}


def investor_leaders(day, count=10):
    """Toss's net-buy / net-sell leaders by investor (외국인·기관·개인) ranked on `day`, {} without a Toss key or on
    failure. Only the latest business day is served, so a journal built later keeps what it captured (rebuild())."""
    import toss_data
    if not toss_data.configured():
        return {}
    try:
        return same_day_leaders(toss_data.investor_rankings(count), day)
    except Exception:  # Toss down or refused
        return {}


def program_snapshot():
    """Today's program trading and breadth per market from Naver (only meaningful when built on the day)."""
    out = {}
    for code in ('KOSPI', 'KOSDAQ'):
        try:
            data = _naver(f'index/{code}/integration')
        except Exception:
            continue
        program, breadth = data.get('programTrendInfo') or {}, data.get('upDownStockInfo') or {}
        out[code] = {'bizdate': program.get('bizdate'),
                     'program': {'차익': _num(program.get('indexDifferenceReal')), '비차익': _num(program.get('indexBiDifferenceReal')),
                                 '전체': _num(program.get('indexTotalReal'))},
                     'breadth': {'상승': _num(breadth.get('riseCount')), '하락': _num(breadth.get('fallCount')),
                                 '보합': _num(breadth.get('steadyCount')), '상한': _num(breadth.get('upperCount')),
                                 '하한': _num(breadth.get('lowerCount'))}}
    return out


# ── intraday charts ─────────────────────────────────────────────────────────
MARKET_PROXY = '069500'  # KODEX 200, the market line next to every traded code


def _ledger_bars(day, code):
    """The runner's own NH 1-minute bars of `code` that day (it records the 7 ETFs every minute): [[HH:MM, o, h, l, c, v]]."""
    from etf_universe import SYMBOLS
    from sqlite_ro import ro_connect
    names = {k for k, v in SYMBOLS.items() if v == code} | {code}
    seen = {}
    try:
        with ro_connect(ROOT / 'trading-ledger.sqlite3') as conn:
            rows = conn.execute("SELECT data FROM events_v2 WHERE time >= ? AND time < ? AND data LIKE '%market_bars%'",
                                (day, (date.fromisoformat(day) + timedelta(days=1)).isoformat())).fetchall()
    except Exception:
        return []
    for (data,) in rows:
        for key, bars in (json.loads(data).get('market_bars') or {}).items():
            if key not in names:
                continue
            for b in bars or []:
                try:
                    t = f"{b['bsop_time'][:2]}:{b['bsop_time'][2:4]}"
                    seen[t] = [t, *(float(b[k]) for k in ('stck_oprc', 'stck_hgpr', 'stck_lwpr', 'stck_prpr')), float(b.get('vol') or 0)]
                except (KeyError, TypeError, ValueError):
                    continue
    return [seen[t] for t in sorted(seen)]


def chart_data(day, code, today=None):
    """{'bars': [[HH:MM, o, h, l, c, v]], 'interval', 'source', 'prev_close'} of one code on `day`: the runner's own NH
    1-minute bars when it recorded them, else Kiwoom 1-minute (ka10080, about a year back, when the key is set), else
    Yahoo 1-minute (7 days back), 5-minute (60 days) or 60-minute (2 years)."""
    import oversold_research as research
    target = date.fromisoformat(day)
    age = ((today or datetime.now(KST).date()) - target).days
    out = {'bars': [], 'interval': None, 'source': None, 'prev_close': None}
    own = _ledger_bars(day, code)
    if len(own) >= 300 and own[-1][0] >= '14:50':  # the whole session; a scanner stock seen part of the day is not
        out.update(bars=own, interval='1m', source='NH (실행기 기록)')
    kiwoom_prev = None
    if not out['bars']:
        import kiwoom_data
        if kiwoom_data.configured():
            try:  # raw prices (fills compare with them), regular session, about a year back
                got = kiwoom_data.day_bars(code, target)
                if got['bars']:
                    out.update(bars=[[b[0].strftime('%H:%M'), *b[1:]] for b in got['bars']], interval='1m', source='Kiwoom 1m')
                    kiwoom_prev = got['prev_close']
            except Exception:  # Kiwoom down or refused: Yahoo below as before
                pass
    import oversold_cases
    symbols = oversold_cases.yahoo_symbols(code)
    for symbol in ([] if kiwoom_prev is not None else symbols):  # Kiwoom bars bring their own raw previous close
        daily = [b for b in research.fetch(symbol, '1d', _daily_range(target)) if b[0].date() < target]
        if not daily:
            continue
        out['prev_close'] = daily[-1][4]
        if not out['bars']:
            for interval, rng, max_age in (('1m', '7d', 6), ('5m', '60d', 58), ('60m', '2y', 700)):
                if age > max_age:
                    continue
                bars = [[b[0].strftime('%H:%M'), *b[1:5], b[5]] for b in research.fetch(symbol, interval, rng, volume=True)
                        if b[0].date() == target]
                if bars:
                    out.update(bars=bars, interval=interval, source=f'Yahoo {interval}')
                    break
        break
    if out['prev_close'] is None:
        out['prev_close'] = kiwoom_prev
    if own and len(own) > len(out['bars']) and out['interval'] in (None, '1m'):
        out.update(bars=own, interval='1m', source='NH (실행기 기록, 일부 시간)')
    return out


def charts(day, codes, today=None):
    """chart_data() for every traded code and the market proxy."""
    return {code: chart_data(day, code, today) for code in dict.fromkeys([*codes, MARKET_PROXY])}


# ── system and reviews ──────────────────────────────────────────────────────
def improvement_report(day):
    """The 16:15 장 마감 개선안 for the day, or None before it is written."""
    report = REVIEWS / f'improvement-{day}.md'
    return report.read_text(encoding='utf-8')[:20000] if report.exists() else None


def system(day):
    """Runner health, gates, guard, blocks and error counts (daily_improvement.build), and the day's reports."""
    import daily_improvement as di
    from sqlite_ro import day_rows
    try:
        facts = di.build(day)
    except Exception as exc:
        facts = {'error': type(exc).__name__}
    errors = defaultdict(int)
    for e in day_rows(ROOT / 'events.jsonl', day)[0] or ():
        if 'error' in str(e.get('event', '')) or e.get('event') in ('order_limit', 'buy_unfilled'):
            errors[f"{e['event']}:{e.get('error_type') or e.get('reason') or ''}".rstrip(':')] += 1
    return {'trading_day': facts.get('trading_day'), 'health': facts.get('health'), 'entry_gates': facts.get('entry_gates'),
            'guard': facts.get('guard'), 'restriction_blocks': facts.get('restriction_blocks'), 'errors': dict(errors),
            'by_entry_route': facts.get('by_entry_route'), 'by_exit': facts.get('by_exit'),
            'daily_review_lessons': facts.get('daily_review_lessons'),
            'improvement_report': improvement_report(day)}


def perspective(day):
    """The written perspective of a day or a period (reviews/journal-<YYYY-MM-DD or period key>.json), or None."""
    path = REVIEWS / f'journal-{day}.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return clean_perspective(data) if isinstance(data, dict) else None


LIST_FIELDS = ('retrospective', 'strategy_improvements', 'system_improvements', 'engine_notes', 'watch_next')


def clean_perspective(data):
    """Shapes the readers rely on: views a dict of text, the list fields lists of text, top_issues / sources dicts."""
    out = dict(data)
    out['summary'] = str(out['summary']) if out.get('summary') else ''
    views = out.get('views')
    out['views'] = {str(k): str(v) for k, v in views.items() if v} if isinstance(views, dict) else {}
    for key in LIST_FIELDS:
        value = out.get(key) or []
        out[key] = [str(x) for x in (value if isinstance(value, list) else [value]) if x]
    for key in ('top_issues', 'sources'):
        value = out.get(key)
        out[key] = [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []
    for item in out['top_issues']:
        item['links'] = [x for x in item.get('links') or [] if isinstance(x, dict)] if isinstance(item.get('links'), list) else []
    return out


def build(day, network=True):
    """The day's journal record."""
    record = {'day': day, 'built': datetime.now(KST).isoformat(timespec='seconds'), 'books': trades(day), 'system': system(day)}
    codes = [f['symbol'] for b in record['books'].values() for f in b['fills']]
    if network:
        record['charts'] = charts(day, codes)
        prices = {c: (v.get('bars') or [[0, 0, 0, 0, None]])[-1][4] for c, v in record['charts'].items()}
        record['flows'] = {'market': market_flows(day), 'stocks': stock_flows(day, list(dict.fromkeys(codes)), prices)}
        pressure = stock_pressure(day, list(dict.fromkeys(codes)))
        if pressure:
            record['flows']['pressure'] = pressure
        if day == datetime.now(KST).strftime('%Y-%m-%d'):  # Toss keeps only the latest business day's ranking
            leaders = investor_leaders(day)
            if leaders:
                record['flows']['investor_rankings'] = leaders
        if day == datetime.now(KST).strftime('%Y-%m-%d'):
            for code, snap in program_snapshot().items():
                if snap.get('bizdate') == day.replace('-', ''):
                    record['flows']['market'].setdefault(code, {}).update(snap)
        record['market'] = market(day)
        record['issues'] = issues(day)
        record['engine_news'] = engine_news(day)
        record['analysts'] = analysts(codes)
    if day == datetime.now(KST).strftime('%Y-%m-%d'):
        try:
            import market_context
            snap = market_context.load()
            record['market_context'] = {'time': snap.get('time'), 'indices': snap.get('indices'), 'themes': snap.get('themes')}
        except Exception:
            pass
    record['perspective'] = perspective(day)
    return record


# ── storage and periods ─────────────────────────────────────────────────────
def store(record, db=DB):
    db = Path(db)
    with sqlite3.connect(db, timeout=10) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS days (day TEXT PRIMARY KEY, data TEXT, updated TEXT)')
        conn.execute('INSERT OR REPLACE INTO days VALUES (?, ?, ?)',
                     (record['day'], json.dumps(record, ensure_ascii=False, default=str), datetime.now(KST).isoformat(timespec='seconds')))
    db.chmod(0o600)
    return record['day']


INTERVAL_RANK = {'1m': 0, '5m': 1, '60m': 2}


def rebuild(day, db=DB):
    """build() again for a stored day, keeping what only that day could capture: the close snapshot of program
    trading and breadth, the market_context snapshot, finer or fuller intraday charts than the sources still keep,
    the indicators and issue headlines when the day was built on the day, and anything a source no longer returns.
    Investor flows are taken fresh (Naver's by-date figures are final the next day)."""
    old, new = load(day, db) or {}, build(day)
    for code, was in ((old.get('flows') or {}).get('market') or {}).items():
        now = new.setdefault('flows', {}).setdefault('market', {}).setdefault(code, {})
        for key in ('program', 'breadth', 'bizdate', 'detail'):  # detail: Toss down at rebuild keeps what was fetched
            if was.get(key) and not now.get(key):
                now[key] = was[key]
    if old.get('market_context') and not new.get('market_context'):
        new['market_context'] = old['market_context']
    kept = same_day_leaders((old.get('flows') or {}).get('investor_rankings'), day)  # not served for past days
    fresh = (new.get('flows') or {}).get('investor_rankings') or {}
    if kept.keys() - fresh.keys():
        new.setdefault('flows', {})['investor_rankings'] = {**kept, **fresh}
    for code, was in ((old.get('flows') or {}).get('pressure') or {}).items():  # Toss down at rebuild: keep each part fetched before
        now = new.setdefault('flows', {}).setdefault('pressure', {}).setdefault(code, {})
        for part, before in zip(PRESSURE_PARTS, pressure_parts(was)):
            if before and not pressure_parts(now)[PRESSURE_PARTS.index(part)]:
                now[part] = was[part]
    for code, was in (old.get('charts') or {}).items():
        now = (new.get('charts') or {}).get(code) or {}
        was_rank, now_rank = INTERVAL_RANK.get(was.get('interval'), 9), INTERVAL_RANK.get(now.get('interval'), 9)
        if was.get('bars') and (was_rank < now_rank or was_rank == now_rank and len(was['bars']) > len(now.get('bars') or ())):
            new.setdefault('charts', {})[code] = was
    # Built on the day, the old indicators and headlines are what the day saw and what the written journal cites;
    # daily bars of 24-hour markets fetched later carry that date's later close, and a later news search returns
    # other articles. Otherwise keep only what the sources no longer return.
    on_day = str(old.get('built', ''))[:10] == day
    for name, was in (old.get('market') or {}).items():
        if on_day or name not in (new.get('market') or {}):
            new.setdefault('market', {})[name] = was
    for topic, was in (old.get('issues') or {}).items():
        if was and (on_day or not (new.get('issues') or {}).get(topic)):
            new.setdefault('issues', {})[topic] = was
    for key in ('engine_news', 'analysts'):
        if old.get(key) and not new.get(key):
            new[key] = old[key]
    return store(new, db)


def _private(key):
    """The written journal is the user's: owner-only, whoever created the files."""
    for ext in ('json', 'md'):
        path = REVIEWS / f'journal-{key}.{ext}'
        if path.exists():
            path.chmod(0o600)


def merge(day, db=DB):
    """Attach the written perspective to a stored day (after the journal task wrote it). A day never collected is
    collected in full first: merging into a bare record would store a day without market, news or charts."""
    record = load(day, db) or build(day)
    refill_issues(record)
    record['perspective'] = perspective(day)
    # The learning watcher stores the day at ~15:29, before the 16:15 improvement report exists; take it now.
    report = improvement_report(day)
    if report:
        record.setdefault('system', {})['improvement_report'] = report
    _private(day)
    store(record, db)
    update_periods(day, db)  # the day's written lessons reach its month, quarter, half and year
    return day


def load(day, db=DB):
    try:
        from sqlite_ro import ro_connect
        with ro_connect(Path(db)) as conn:
            row = conn.execute('SELECT data FROM days WHERE day = ?', (day,)).fetchone()
    except Exception:
        return None
    return _record(row[0]) if row else None


def _record(text):
    """A stored record, its perspective in the shapes the readers expect (older rows kept the task's raw JSON)."""
    record = json.loads(text)
    view = record.get('perspective')
    record['perspective'] = clean_perspective(view) if isinstance(view, dict) and view else None
    return record


def days(since='0000', until='9999', db=DB):
    """Stored records with since <= day <= until (prefixes work: '2026-11' is the month), oldest first. Parsed once per
    DB change (the dashboard reruns on every click); callers must not mutate the records."""
    try:
        mtime = Path(db).stat().st_mtime_ns
    except OSError:
        return []
    try:
        return list(_days(since, until, str(db), mtime))
    except Exception:  # locked mid-write: try again next run (lru_cache does not keep exceptions)
        return []


def day_list(db=DB):
    """The stored days, oldest first, without parsing a record ([] without a journal)."""
    try:
        from sqlite_ro import ro_connect
        with ro_connect(Path(db)) as conn:
            return [r[0] for r in conn.execute('SELECT day FROM days ORDER BY day')]
    except sqlite3.Error:
        return []


@functools.lru_cache(maxsize=16)
def _days(since, until, db, mtime):
    try:
        from sqlite_ro import ro_connect
        with ro_connect(Path(db)) as conn:
            rows = conn.execute('SELECT data FROM days WHERE day >= ? AND day <= ? ORDER BY day', (since, until + '\uffff')).fetchall()
    except sqlite3.OperationalError as exc:
        if 'no such table' in str(exc):
            return ()
        raise
    return tuple(_record(r[0]) for r in rows)


def period(records):
    """A range of days pooled per book: performance over every fill, daily P&L series, best/worst day and symbol."""
    out = {}
    keys = list(dict.fromkeys(k for r in reversed(records) for k in (r.get('books') or {})))  # paper, vibe, live in book order
    for key in keys:
        fills, daily, label = [], [], key
        for r in records:
            book = (r.get('books') or {}).get(key)
            if not book:
                continue
            label = book['label']
            fills += book['fills']
            daily.append((r['day'], book['performance']['realized_krw'], book['performance']['bought_krw']))
        perf = performance(fills)
        traded = [d for d in daily if d[1] or d[2]]
        cumulative, running, low, peak = [], 0, 0, 0
        for day, pnl, _ in daily:
            running += pnl
            peak = max(peak, running)
            low = min(low, running - peak)
            cumulative.append((day, running))
        out[key] = {'label': label, 'performance': perf, 'daily': daily, 'cumulative': cumulative, 'max_drawdown_krw': low,
                    'trading_days': len(traded), 'up_days': sum(1 for d in traded if d[1] > 0),
                    'best_day': max(traded, key=lambda d: d[1], default=None), 'worst_day': min(traded, key=lambda d: d[1], default=None)}
    return out


# ── what the engines read ───────────────────────────────────────────────────
_BRIEF = {}


def brief(now=None, sessions=3, db=DB):
    """Compact recent journal for Jev and vibe-trading: the last sessions' P&L, what worked, the written lessons and
    the notes for the engines, and the issues to watch. {} without a journal; cached until the journal file changes."""
    today = (now or datetime.now(KST)).strftime('%Y-%m-%d')
    try:
        stamp = (today, Path(db).stat().st_mtime_ns)
    except OSError:
        return {}
    if _BRIEF.get('stamp') == stamp:
        return _BRIEF['value']
    try:  # only the sessions it needs, not every stored record
        from sqlite_ro import ro_connect
        with ro_connect(Path(db)) as conn:
            rows = conn.execute('SELECT data FROM days WHERE day < ? ORDER BY day DESC LIMIT ?', (today, sessions)).fetchall()
        recent = [_record(r[0]) for r in reversed(rows)]
    except (sqlite3.Error, ValueError):
        recent = []
    value = _brief(recent)
    if value:
        value['periods'] = [_period_brief(load_period(T.key_of(kind, today), db)) for kind in ('month', 'quarter')]
        value['periods'] = [x for x in value['periods'] if x]
    _BRIEF.update(stamp=stamp, value=value)
    return _BRIEF['value']


def _brief(recent):
    if not recent:
        return {}
    out = []
    for r in reversed(recent):
        paper = ((r.get('books') or {}).get('paper') or {}).get('performance') or {}
        view = r.get('perspective') or {}
        out.append({'day': r['day'], 'paper_realized_krw': paper.get('realized_krw'), 'win_rate': paper.get('win_rate'),
                    'by_route': (r.get('system') or {}).get('by_entry_route'),
                    'summary': str(view.get('summary', ''))[:300],
                    'lessons': [str(x)[:200] for x in (view.get('retrospective') or [])[:4]],
                    'engine_notes': [str(x)[:200] for x in (view.get('engine_notes') or [])[:5]],
                    'watch': [str(x)[:160] for x in (view.get('watch_next') or [])[:5]]})
    return {'about': ('The trading journal of the last sessions: realized P&L of the shared paper account, results by '
                      'entry route, the written retrospective, notes for the engines and the issues to watch, plus the month and '
                      'quarter so far (periods). Context for judgment, not a trading rule.'), 'sessions': out}


def _period_brief(record):
    """The month / quarter so far for the engines: P&L per book, the entry routes' record and the period lessons."""
    if not record:
        return None
    view = record.get('perspective') or {}
    return {'period': record['label'], 'days': record['days'],
            'books': {b['label']: {'realized_krw': b['performance']['realized_krw'], 'win_rate': b['performance']['win_rate'],
                                   'max_drawdown_krw': b['max_drawdown_krw']} for b in (record.get('books') or {}).values()},
            'routes': {k: {'n': v['n'], 'wins': v['wins'], 'realized_krw': v['realized_krw']} for k, v in list((record.get('routes') or {}).items())[:6]},
            'lessons': [str(x)[:200] for x in (view.get('retrospective') or [])[:4]],
            'engine_notes': [str(x)[:200] for x in (view.get('engine_notes') or [])[:4]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--day', help='YYYY-MM-DD (default today, Asia/Seoul)')
    parser.add_argument('--backfill', type=int, help='build the last N weekdays')
    parser.add_argument('--merge', action='store_true', help='only attach reviews/journal-DAY.json to the stored day')
    parser.add_argument('--no-network', action='store_true')
    parser.add_argument('--period', choices=list(T.KINDS), help='a period journal (month/quarter/half/year) from the stored days')
    parser.add_argument('--key', help='the period key (2026-11, 2026-Q4, 2026-H2, 2026); default the period holding --day')
    parser.add_argument('--report', action='store_true', help='print the Markdown report (with --day or --period)')
    parser.add_argument('--blank', action='store_true', help='with --report: the skeleton without the written perspective (to write anew)')
    parser.add_argument('--rebuild', action='store_true', help='collect a stored day again, keeping what only that day captured')
    parser.add_argument('--status', action='store_true', help='trading day? collected? written? merged? (with --day)')
    parser.add_argument('--week', action='store_true', help='the week so far up to --day, pooled per book')
    args = parser.parse_args()
    day = args.day or datetime.now(KST).strftime('%Y-%m-%d')
    if args.period:
        key = args.key or T.key_of(args.period, day)
        if args.merge:
            print(json.dumps({'merged': merge_period(key)}))
            return
        record = store_period(build_period(args.period, key))
        if args.report:
            print(period_report_md({**record, 'perspective': None} if args.blank else record))
        else:
            print(json.dumps({'period': key, 'days': record['days'], 'checked': T.check(period_report_md(record), 'period')}, ensure_ascii=False))
        return
    if args.status:
        print(json.dumps(status(day), ensure_ascii=False))
        return
    if args.week:
        print(json.dumps({k: {'label': b['label'], 'realized_krw': b['performance']['realized_krw'], 'win_rate': b['performance']['win_rate'],
                              'trading_days': b['trading_days'], 'best_day': b['best_day'], 'worst_day': b['worst_day'],
                              'by_symbol': b['performance']['by_symbol']} for k, b in week(day).items()}, ensure_ascii=False))
        return
    if args.rebuild:
        print(json.dumps({'rebuilt': rebuild(day)}))
        return
    if args.report:
        record = load(day) or build(day, network=not args.no_network)
        if args.blank and not args.no_network and refill_issues(record):  # the writer starts here: the topics missed at the close
            store(record)
        print(report_md({**record, 'perspective': None} if args.blank else record))
        return
    if args.merge:
        print(json.dumps({'merged': merge(day)}))
        return
    targets = [day]
    if args.backfill:
        d, targets = date.fromisoformat(day), []
        while len(targets) < args.backfill:
            if d.weekday() < 5:
                targets.append(d.isoformat())
            d -= timedelta(days=1)
    for target in sorted(targets):
        record = build(target, network=not args.no_network)
        store(record)
        paper = record['books'].get('paper', {}).get('performance', {})
        print(json.dumps({'day': target, 'paper_realized_krw': paper.get('realized_krw'), 'fills': sum(len(b['fills']) for b in record['books'].values())},
                         ensure_ascii=False))



# ── the day's report ────────────────────────────────────────────────────────
WEEKDAYS = T.WEEKDAYS
ROUTE_EXITS = {'inverse_protect': '인버스 수익 지키기 청산', 'inverse_market_rebound': '지수 반등 인버스 청산',  # 2026-11-20
    'take_profit': '익절', 'trailing_stop': '추적 손절', 'stop_loss': '손절', 'session_flatten': '장 마감 정리', 'fade_exit': '매수세 약화 익절',
               'jev_sell': 'Jev 매도 의견', 'manual_exit': '수동 정리', 'split_take_profit': '분할 익절'}


def _won(v):
    return '—' if v is None else f'{v:+,.0f}원'


def _pct(v):
    return '—' if v is None else f'{v:+.2f}%'


def _md_links_safe(text):
    """Written Markdown for Streamlit: a range like +2~25% would turn into strikethrough."""
    return re.sub(r'(?<!\\)\$', '\\$', re.sub(r'(?<![~\\])~(?!~)', '\\~', str(text)))


def _md(text):
    return _md_links_safe(str(text).replace('|', '/').replace('\n', ' '))


def safe_url(url):
    """Only web links go into the report and the cards (the 16:40 task writes some of them)."""
    return url if isinstance(url, str) and url.startswith(('http://', 'https://')) else None


def trade_lines(bars, prev_close, fills, market_bars=None):
    """Where the day's buys and sells sat: the day's range, VWAP at the time, what came after the last sale and the
    market (KODEX 200) — the same reading the dashboard chart shows. `bars`: [[HH:MM, o, h, l, c, v]]; `fills`: rows."""
    if not bars:
        return []
    high, low = max(b[2] for b in bars), min(b[3] for b in bars)
    first, last = bars[0][1], bars[-1][4]
    vwap, cum_pv, cum_v = {}, 0.0, 0.0
    for t, o, h, lo, c, v in bars:
        cum_pv, cum_v = cum_pv + (h + lo + c) / 3 * max(v, 0), cum_v + max(v, 0)
        vwap[t] = cum_pv / cum_v if cum_v else None
    out = [f"시가 {first:,.0f} · 고가 {high:,.0f} · 저가 {low:,.0f} · 마지막 {last:,.0f}"
           + (f" · 전일 대비 {_pct((last / prev_close - 1) * 100)}" if prev_close else '')]
    minute = lambda f: str(f['time'])[11:16]  # noqa: E731
    for side, name in (('BUY', '매수'), ('SELL', '매도')):
        rows = [f for f in fills if f['side'] == side]
        if not rows:
            continue
        avg = sum(f['price'] * f['qty'] for f in rows) / sum(f['qty'] for f in rows)
        at = [w for t, w in vwap.items() if t <= minute(min(rows, key=lambda f: f['time'])) and w]
        line = f"{name} 평균 {avg:,.0f}원"
        if at:
            line += f" · 그때 VWAP 대비 {_pct((avg / at[-1] - 1) * 100)}"
        if high > low:
            line += f" · 하루 범위의 {(avg - low) / (high - low) * 100:.0f}% 자리"
        out.append(line)
    sells = sorted((f for f in fills if f['side'] == 'SELL'), key=lambda f: f['time'])
    if sells:
        cut = minute(sells[-1])
        after = [b for b in bars if b[0] > cut]
        if after:
            px = sells[-1]['price']
            out.append(f"마지막 매도({cut}, {px:,.0f}) 뒤 최고 {_pct((max(b[2] for b in after) / px - 1) * 100)} · "
                       f"최저 {_pct((min(b[3] for b in after) / px - 1) * 100)} · 마감 {_pct((after[-1][4] / px - 1) * 100)}")
    if market_bars:
        mine, mkt = (last / first - 1) * 100, (market_bars[-1][4] / market_bars[0][1] - 1) * 100
        out.append(f"시가 대비 종목 {_pct(mine)} · KODEX 200 {_pct(mkt)} → 시장 대비 {_pct(mine - mkt)}")
    return out


def report_md(record):
    """The day's trading-perspective report in Markdown, from the record (and the written perspective when there is
    one): numbers, every round trip with its reason, each traded code read against its chart, the market, the issues
    and the news the engines saw (linked), analysts, the system, the retrospective and the sources."""
    day = record['day']
    books = record.get('books') or {}
    view = record.get('perspective') or {}
    paper = (books.get('paper') or {}).get('performance') or {}
    H = dict(T.DAY)  # the template's headings, every section always present (journal_template.py)
    lines = [T.title('day', day), '']
    summary = view.get('summary') or (
        f"모의(Jev) 실현 {_won(paper.get('realized_krw'))}, 매도 {paper.get('sells', 0)}건 승률 "
        f"{paper.get('win_rate') if paper.get('win_rate') is not None else '—'}%. " + T.SUMMARY_PENDING)
    lines += [f"> {_md(summary)}", '']
    heads, ranked = rank_issues(record)
    stars_by_title = {h['title']: h['stars'] for h in heads}
    lines += [f"## {H['core_issues']}", '']
    if not (view.get('top_issues') or ranked):
        lines += ['_이날 모은 이슈가 없습니다._', '']
    else:
        for i, item in enumerate(view.get('top_issues') or [], 1):  # the 16:40 analysis's own ranking
            n = star_count(item.get('stars'))
            bold = '**' if n >= 4 else ''
            lines.append(f"{i}. {star_text(n)} {bold}{_md(item.get('title', ''))}{bold} — {_md(item.get('why', ''))}")
            lines += [f"   - [{_md(x.get('title'))}]({safe_url(x.get('url'))})" for x in item.get('links') or [] if isinstance(x, dict) and safe_url(x.get('url'))]
        if view.get('top_issues'):
            lines += ['', '_아래는 데이터로 매긴 순위입니다._', '']
        for i, g in enumerate(ranked[:6], 1):
            top = g['top'][0]
            bold = '**' if g['stars'] >= 4 else ''
            lines.append(f"{i}. {star_text(g['stars'])} {bold}{g['category']} — [{_md(top['title'])}]({top.get('link') or news_search_link(top['title'])}){bold}"
                         f" · {top.get('publisher') or ''}")
            lines.append(f"   - 근거: {' · '.join(top['why'][1:]) or '분야 가중치'}")
            lines += [f"   - {'★' * h['stars']} [{_md(h['title'])}]({h.get('link') or news_search_link(h['title'])})" for h in g['top'][1:]]
        lines += ['', '_별점: 분야 가중치 + 핵심어 + 그 분야 지표의 큰 움직임 + 매매 종목 언급 + 엔진 입력 시간 + 비슷한 보도 수_', '']
    lines += [f"## {H['numbers']}", '',
              '| 계좌 | 실현손익 | 매수 대비 | 매수·매도 | 승률 | 평균 이익 / 손실 | 손익비 | 가장 수익이 컸던 시점 |', '|---|---|---|---|---|---|---|---|']
    for book in books.values():
        p = book['performance']
        peak = f"{str(p['peak']['time'])[11:16]} ({_won(p['peak']['realized_krw'])})" if p.get('peak') else '—'
        lines.append(f"| {book['label']} | {_won(p['realized_krw'])} | {_pct(p['return_on_buys_pct'])} | {p['buys']} · {p['sells']} | "
                     f"{p['win_rate'] if p['win_rate'] is not None else '—'}% | {_pct(p['avg_win_pct'])} / {_pct(p['avg_loss_pct'])} | "
                     f"{p['profit_factor'] or '—'} | {peak} |")
    if not books:
        lines.append('| — | 체결 없음 | — | — | — | — | — | — |')
    lines += ['', '_체결가 기준 · 수수료·세금 미반영_', '']
    trips = [(book['label'], t) for book in books.values() for t in book.get('round_trips') or []]
    lines += [f"## {H['trades']}", '']
    if not trips:
        lines += ['_이날 왕복 매매가 없습니다._', '']
    else:
        lines += [
                  '| 계좌 | 종목 | 진입 | 진입 경로 | 청산 | 청산 이유 | 보유 | 손익 | 수익률 | 보유 중 최고 / 최저 |', '|---|---|---|---|---|---|---|---|---|---|']
        for label, t in trips:
            lines.append(f"| {label} | [{t['symbol']}]({stock_link(t['symbol'])}) | {t['open'][:5]} | {_md(t['entry_route'] or '—')} | "
                         f"{(t['close'] or '보유')[:5]} | {ROUTE_EXITS.get(t['exit'], t['exit'] or '—')} | {t['held_min'] if t['held_min'] is not None else '—'}분 | "
                         f"{_won(t['pnl'])} | {_pct(t['return_pct'])} | {_pct(t.get('mfe_pct'))} / {_pct(t.get('mae_pct'))} |")
        basis = [(t['symbol'], t['entry_basis']) for _, t in trips if t.get('entry_basis')]
        if basis:
            lines += ['', '**진입 근거**'] + [f"- {s}: {_md(b)}" for s, b in basis]
        lines.append('')
    charts = record.get('charts') or {}
    market_bars = (charts.get(MARKET_PROXY) or {}).get('bars')
    codes = list(dict.fromkeys(f['symbol'] for book in books.values() for f in book['fills']))
    lines += [f"## {H['charts']}", '']
    if not codes:
        lines += ['_매매한 종목이 없습니다._', '']
    else:
        for code in codes:
            data = charts.get(code) or {}
            for book in books.values():  # one reading per account: paper and live trades are not pooled
                mine = [f for f in book['fills'] if f['symbol'] == code]
                if not mine:
                    continue
                pnl = sum(f['pnl'] or 0 for f in mine if f['side'] == 'SELL')
                lines.append(f"### [{code}]({stock_link(code)}) · {book['label']} · 매수 {sum(f['side'] == 'BUY' for f in mine)} · 매도 "
                             f"{sum(f['side'] == 'SELL' for f in mine)} · 실현 {_won(pnl)}")
                lines += [f'- {x}' for x in trade_lines(data.get('bars'), data.get('prev_close'), mine, market_bars)] or ['- 분봉 없음']
                lines.append('')
    market = record.get('market') or {}
    lines += [f"## {H['market']}", '']
    if not market:
        lines += ['_이날 지표를 모으지 않았습니다._', '']
    else:
        lines += ['| 지표 | 종가 | 등락 | 기준일 |', '|---|---|---|---|']
        lines += [f"| [{n}]({QUOTE_LINKS.get(n, '#')}) | {v['close']:,.2f} | {_pct(v['change_pct'])} | {v['on']} |" for n, v in market.items()]
        for key, title in (('market', '시황·수급'), ('global', '해외 증시·금리·환율·원자재·코인')):
            if (view.get('views') or {}).get(key):
                lines += ['', f"**{title}** — {_md_links_safe(view['views'][key])}"]
        lines.append('')
    flows = record.get('flows') or {}
    if (flows.get('market') or {}):
        lines += ['### 투자자·프로그램 수급 (순매수, 억 원)', '',
                  '| 시장 | 개인 | 외국인 | 기관 | 프로그램 차익 | 비차익 | 프로그램 전체 | 상승 / 하락 |', '|---|---|---|---|---|---|---|---|']
        fmt = lambda v: '—' if v is None else (f'**{v:+,.0f}**' if abs(v) >= 3000 else f'{v:+,.0f}')  # noqa: E731
        for code, name in (('KOSPI', '코스피'), ('KOSDAQ', '코스닥')):
            m = flows['market'].get(code) or {}
            prog, breadth = m.get('program') or {}, m.get('breadth') or {}
            lines.append(f"| {name} | {fmt(m.get('개인'))} | {fmt(m.get('외국인'))} | {fmt(m.get('기관'))} | {fmt(prog.get('차익'))} | "
                         f"{fmt(prog.get('비차익'))} | {fmt(prog.get('전체'))} | "
                         + (f"{breadth['상승']:,.0f} / {breadth.get('하락', 0):,.0f}" if breadth.get('상승') is not None else '—') + ' |')
        lines += ['', '_굵은 숫자는 3,000억 원 이상 · 프로그램·상승/하락은 장 마감 당일에 만든 일지에만 있습니다._', '']
        details = [(name, flows['market'][code]['detail']) for code, name in (('KOSPI', '코스피'), ('KOSDAQ', '코스닥'))
                   if isinstance((flows['market'].get(code) or {}).get('detail'), dict)]
        if details:
            import toss_data
            kinds = list(toss_data.INSTITUTION_KINDS.values())
            lines += ['### 기관 세부·기타법인 (토스증권, 순매수 억 원)', '',
                      '| 시장 | ' + ' | '.join(kinds) + ' | 기관 계 | 기타법인 | 기준 시각 |', '|' + '---|' * (len(kinds) + 4)]
            for name, d in details:
                inst = d.get('institution_detail') or {}
                lines.append(f'| {name} | ' + ' | '.join(fmt(inst.get(k)) for k in kinds)
                             + f" | {fmt(d.get('기관'))} | {fmt(d.get('기타법인'))} | {str(d.get('updated_at') or '')[5:16].replace('T', ' ')} |")
            lines += ['', '_토스증권 외국인은 미등록 외국인을 포함해 위 네이버 합계와 다를 수 있습니다._', '']
    if flows.get('stocks'):
        names = stock_names(set(flows['stocks']))
        lines += ['### 매매 종목 수급 (그날 순매수)', '', '| 종목 | 외국인 | 기관 | 개인 | 비고 |', '|---|---|---|---|---|']
        for code, f in flows['stocks'].items():
            cell = lambda who: (f"{f[who]['shares']:+,.0f}주" + (f" ({f[who]['value_100m']:+,.1f}억)" if f[who].get('value_100m') is not None else '')  # noqa: E731
                                if (f.get(who) or {}).get('shares') is not None else '—')
            lines.append(f"| [{names.get(code, code)}]({stock_link(code)}) | {cell('외국인')} | {cell('기관')} | {cell('개인')} | "
                         f"{'잠정' if f.get('provisional') else ''} |")
        lines.append('')
    leaders = flows.get('investor_rankings') or {}
    if leaders:
        lines += ['### 투자자별 순매수·순매도 상위 (토스증권, 억 원)', '',
                  '| 투자자 | 순매수 상위 | 순매도 상위 | 기준 시각 |', '|---|---|---|---|']
        item = lambda r: f"[{_md(r.get('name') or r['code'])}]({stock_link(r['code'])}) {r['net_100m']:+,.0f}" if r.get('net_100m') is not None else r['code']  # noqa: E731
        for who in ('외국인', '기관', '개인'):
            side = leaders.get(who) or {}
            lines.append(f"| {who} | {', '.join(item(r) for r in side.get('net_buy', [])[:5]) or '—'} | "
                         f"{', '.join(item(r) for r in side.get('net_sell', [])[:5]) or '—'} | {str(side.get('ranked_at') or '')[5:16].replace('T', ' ')} |")
        lines += ['', '_KRX·NXT 통합, ETF 포함. 외국인·기관은 장중 잠정, 개인은 정규장 종료 뒤 값이며 기준 시각이 서로 다를 수 있습니다._', '']
    if flows.get('pressure'):
        names = stock_names(set(flows['pressure']))
        lines += ['### 매매 종목 공매도·신용·대차 (토스증권)', '',
                  '| 종목 | 공매도 (거래대금 비중) | 신용융자 잔고 (전일 대비, 상장주식 대비) | 대차 잔고 (전일 대비) | 기준일 |', '|---|---|---|---|---|']
        qty = lambda v: '—' if v is None else f'{v:+,.0f}주'  # noqa: E731
        for code, f in flows['pressure'].items():
            short, credit, lend = pressure_parts(f)
            dates = sorted({x['date'] for x in (short, credit, lend) if x.get('date')})
            lines.append(f"| [{names.get(code, code)}]({stock_link(code)}) | "
                         + (f"{short['amount_100m']:,.1f}억" + (f" ({short['amount_rate_pct']:.2f}%)" if short.get('amount_rate_pct') is not None else '')
                            if short.get('amount_100m') is not None else '—') + ' | '
                         + (f"{credit['margin_balance']:,.0f}주 ({qty(credit.get('margin_balance_change'))}"
                            + (f", {credit['margin_balance_rate_pct']:.2f}%" if credit.get('margin_balance_rate_pct') is not None else '') + ')'
                            if credit.get('margin_balance') is not None else '—') + ' | '
                         + (f"{lend['balance']:,.0f}주 ({qty(lend.get('balance_change'))})" if lend.get('balance') is not None else '—')
                         + f" | {' · '.join(dates) or '—'} |")
        lines += ['', '_신용은 다음 영업일 새벽(T+1)에 반영돼 장 마감 당일 일지에는 전 영업일 값입니다._', '']
    issue_view = view.get('views') or {}
    lines += [f"## {H['news']}", '']
    if not (record.get('issues') or record.get('engine_news') or issue_view):
        lines += ['_이날 모은 뉴스가 없습니다._', '']
    for key, title in (('issues', '전쟁·정치·지정학'), ('earnings', '실적·혁신 발표'), ('stocks', '종목')):
        if issue_view.get(key):
            lines += [f"**{title}** — {_md_links_safe(issue_view[key])}", '']
    for name, items in (record.get('issues') or {}).items():
        if items:
            lines.append(f"**{name}**")
            lines += [f"- {'★' * stars_by_title.get(h['title'], 1)} {'**' if stars_by_title.get(h['title'], 1) >= 4 else ''}"
                      f"[{_md(h['title'])}]({h.get('link') or news_search_link(h['title'])}){'**' if stars_by_title.get(h['title'], 1) >= 4 else ''}"
                      f" · {h.get('publisher') or ''}" for h in items]
            lines.append('')
    if record.get('engine_news'):
        lines += ['**Jev·vibe가 그날 받은 뉴스** (본 시간 순)', '']
        lines += [f"- [{_md(n.get('title'))}]({n.get('link') or news_search_link(n.get('title') or '')}) · {n.get('publisher') or ''} · {n.get('minutes', 0)}분"
                  for n in record['engine_news']]
        lines.append('')
    lines += [f"## {H['analysts']}", '']
    if not record.get('analysts'):
        lines += ['_매매 종목의 애널리스트 자료가 없습니다._', '']
    else:
        lines += ['| 종목 | 평균 의견 (5=강력 매수) | 평균 목표가 | 리포트 어조 | 최근 리포트 |', '|---|---|---|---|---|']
        for code, a in record['analysts'].items():
            reports = ' / '.join(f"[{_md(r['text'])}]({r['link']})" if r.get('link') else _md(r['text'])
                                 for r in (a.get('reports') or []) if isinstance(r, dict))
            target = f"{a['target_mean']:,.0f}원" if a.get('target_mean') else '—'
            lines.append(f"| [{code}]({a.get('link') or stock_link(code)}) | {a.get('recomm_mean') or '—'} | {target} | "
                         f"{ {'up': '상향', 'down': '하향', 'neutral': '중립'}.get(a.get('report_tone'), '—') } | {reports or '—'} |")
        if issue_view.get('analysts'):
            lines += ['', f"**관점** — {issue_view['analysts']}"]
        lines.append('')
    system = record.get('system') or {}
    lines += [f"## {H['system']}", '']
    if system.get('errors'):
        lines += ["- 오류·결함: " + ' · '.join(f'{k} {v}건' for k, v in system['errors'].items())]
    if system.get('entry_gates'):
        lines += ['- 진입하지 않은 이유(상위): ' + ' · '.join(f'{k} {v}' for k, v in list(system['entry_gates'].items())[:6])]
    if system.get('restriction_blocks'):
        lines += ['- 제한 규칙이 막은 횟수: ' + ' · '.join(f'{k} {v}' for k, v in system['restriction_blocks'].items())]
    if not any(system.get(k) for k in ('errors', 'entry_gates', 'restriction_blocks')):
        lines.append('- 오류·진입 차단 기록이 없습니다.')
    lines.append('')
    return '\n'.join(lines + written_sections(view, T.DAY)).rstrip() + '\n'


def written_sections(view, template, extra=None):
    """The template's written sections (retrospective … watch_next, 출처) from a perspective; `extra` adds automatic
    lines under a section (a period's daily lessons) before the analysis is written."""
    lines = []
    for key, heading in template:
        if key not in T.LISTS:
            continue
        items = [f'- {_md_links_safe(x)}' for x in view.get(key) or []] or (extra or {}).get(key) or [T.EMPTY_WRITTEN]
        lines += [f'## {heading}', ''] + items + ['']
    lines += ['## 출처', '']
    for src in view.get('sources') or []:
        text = ' · '.join(str(src.get(k)) for k in ('publisher', 'date', 'title') if src.get(k))
        lines.append(f"- [{_md(text)}]({safe_url(src.get('url'))})" if safe_url(src.get('url')) else f'- {_md(text)}')
    if not view.get('sources'):
        lines.append(T.EMPTY_WRITTEN)
    return lines


# ── importance: stars for the day's issues and headlines ────────────────────
CATEGORY_WEIGHT = {'전쟁·분쟁': 3.0, '지정학': 3.0, '금리·연준': 3.0, '미국 증시': 3.0, '반도체': 2.5, '시황': 2.0, '기업 실적': 2.0,
                   '환율': 2.0, '원자재': 2.0, '애널리스트': 1.5, '정치': 1.5, '혁신 발표': 1.5, '코인': 1.0}
SEVERE = ('폭락', '급락', '패닉', '공포', '전쟁', '공습', '미사일', '확전', '휴전', '관세', '제재', '디폴트', '금리 인상', '금리 인하',
          '동결', '서프라이즈', '쇼크', '사상 최대', '사상 최고', '최고치', '최저치', '서킷', '사이드카', '셧다운', '탄핵', '계엄',
          '폭등', '급등', '어닝', '가이던스', '파업', '부도', '하향', '상향')
# the market move that makes a category matter more that day: (indicator, |change| for +1, |change| for +2)
MOVE_LINKS = {'미국 증시': ('나스닥', 2.0, 4.0), '원자재': ('WTI 유가', 3.0, 6.0), '코인': ('비트코인', 5.0, 10.0),
              '환율': ('원/달러', 0.8, 1.5), '시황': ('코스피', 2.0, 4.0), '반도체': ('필라델피아 반도체', 3.0, 6.0),
              '금리·연준': ('미 국채 10년', 2.0, 4.0)}


def stars_of(score):
    return 5 if score >= 7 else 4 if score >= 5.5 else 3 if score >= 4 else 2 if score >= 2.5 else 1


def stock_names(codes):
    """{code: name} for the codes anything here can name (stock_names.py)."""
    import stock_names as names
    return {c: n for c, n in names.load(ROOT.parent).items() if c in codes}


def rank_issues(record):
    """(headlines, issues): every headline of the day scored for importance with 1~5 stars and the reasons, and the
    categories ranked by their strongest headlines. Score = category weight + severe words + the market move behind
    the category + mentions of the codes traded that day + how much the engines saw it + how many outlets repeated it."""
    market = record.get('market') or {}
    traded = {f['symbol'] for b in (record.get('books') or {}).values() for f in b.get('fills') or []}
    names = [n for n in stock_names(traded).values() if n and len(n) >= 2]
    seen = {re.sub(r'\s+', '', n['title'])[:24]: n.get('minutes', 0) for n in record.get('engine_news') or []}
    every = [(cat, h) for cat, items in (record.get('issues') or {}).items() for h in items or []]
    tokens = [set(t for t in re.findall(r'[가-힣A-Za-z0-9]{2,}', h['title']) if len(t) >= 2) for _, h in every]
    out = []
    for i, (cat, h) in enumerate(every):
        score, why = CATEGORY_WEIGHT.get(cat, 1.0), [cat]
        hits = [w for w in SEVERE if w in h['title']]
        if hits:
            score += min(2.0, len(hits))
            why.append('핵심어 ' + '·'.join(hits[:3]))
        link = MOVE_LINKS.get(cat)
        if link and link[0] in market:
            move = abs(market[link[0]]['change_pct'])
            if move >= link[1]:
                score += 2.0 if move >= link[2] else 1.0
                why.append(f"{link[0]} {market[link[0]]['change_pct']:+.1f}%")
        mentioned = [n for n in names if n in h['title']]
        if mentioned:
            score += 1.5
            why.append('매매 종목 ' + '·'.join(mentioned[:2]))
        exposure = max((m for k, m in seen.items() if k and k in re.sub(r'\s+', '', h['title'])), default=0)
        if exposure:
            score += 1.0
            why.append(f'엔진 입력 {exposure}분')
        echoes = sum(1 for j, other in enumerate(tokens) if j != i and len(tokens[i] & other) >= 2)
        if echoes:
            score += min(1.5, 0.5 * echoes)
            why.append(f'비슷한 보도 {echoes}건')
        out.append({**h, 'category': cat, 'score': round(score, 1), 'stars': stars_of(score), 'why': why})
    for n in record.get('engine_news') or []:  # what the engines read but the issue searches did not return
        if not any(re.sub(r'\s+', '', n['title'])[:24] in re.sub(r'\s+', '', h['title']) for _, h in every):
            score = 2.0 + min(2.0, n.get('minutes', 0) / 120) + min(2.0, sum(w in n['title'] for w in SEVERE))
            out.append({'title': n['title'], 'publisher': n.get('publisher'), 'link': n.get('link'), 'category': '엔진 입력 뉴스',
                        'score': round(score, 1), 'stars': stars_of(score), 'why': ['엔진 입력', f"{n.get('minutes', 0)}분"]})
    out.sort(key=lambda h: -h['score'])
    unique, keys = [], set()
    for h in out:  # the same story from several outlets is listed once (the best-scored copy)
        key = re.sub(r'[^가-힣A-Za-z0-9]', '', h['title'])[:16]
        if key not in keys:
            keys.add(key)
            unique.append(h)
    out = unique
    groups = defaultdict(list)
    for h in out:
        groups[h['category']].append(h)
    issues = sorted(({'category': cat, 'score': round(items[0]['score'] + 0.3 * sum(x['stars'] >= 3 for x in items[1:]), 1),
                      'stars': max(x['stars'] for x in items), 'top': items[:3], 'count': len(items)} for cat, items in groups.items()),
                    key=lambda g: -g['score'])
    return out, issues


def star_text(n):
    return '★' * n + '☆' * (5 - n)


def star_count(value):
    """1~5 from a number, '4' or '★★★★' (the 16:40 task writes top_issues stars)."""
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        n = str(value or '').count('★') or 3
    return min(5, max(1, n))


# ── period journals: month / quarter / half / year (user 2026-11-13) ────────
def route_stats(records):
    """Entry-route results of the shared paper account over the days: {route: {'n', 'wins', 'realized_krw'}}."""
    routes = {}
    for r in records:
        for name, s in ((r.get('system') or {}).get('by_entry_route') or {}).items():
            agg = routes.setdefault(name, {'n': 0, 'wins': 0, 'realized_krw': 0})
            for k in agg:
                agg[k] += s.get(k) or 0
    return dict(sorted(routes.items(), key=lambda kv: -kv[1]['realized_krw']))


def build_period(kind, key, db=DB):
    """A period journal from the stored days: every book pooled, the monthly P&L, routes, symbols, the market's move
    over the period and its big days, the period's 4~5 star headlines, the system totals, the daily lessons and the
    written period perspective (reviews/journal-<key>.json)."""
    since, until = T.bounds(kind, key)
    records = days(since, until, db)
    monthly = {}
    for r in records:
        for b in (r.get('books') or {}).values():
            row = monthly.setdefault(r['day'][:7], {})
            row[b['label']] = row.get(b['label'], 0) + (b['performance']['realized_krw'] or 0)
    market = {}
    for r in records:
        for name, v in (r.get('market') or {}).items():
            if v.get('close') is None:
                continue
            m = market.setdefault(name, {'start': v['close'] / (1 + (v.get('change_pct') or 0) / 100), 'up': [], 'down': []})
            m['end'], m['end_on'] = v['close'], v.get('on')
            if name == '코스피' and abs(v.get('change_pct') or 0) >= 2:
                m['up' if v['change_pct'] > 0 else 'down'].append((r['day'], v['change_pct']))
    for m in market.values():
        m['change_pct'] = round((m['end'] / m['start'] - 1) * 100, 2) if m['start'] else None
    issues, categories, seen = [], {}, set()
    for r in records:
        for h in rank_issues(r)[0]:
            if h['stars'] >= 4 and h['title'] not in seen:  # the same headline searched again the next day counts once
                seen.add(h['title'])
                issues.append({'day': r['day'], 'title': h['title'], 'link': h.get('link') or news_search_link(h['title']),
                               'category': h['category'], 'stars': h['stars'], 'publisher': h.get('publisher')})
                categories[h['category']] = categories.get(h['category'], 0) + 1
    errors, gates = {}, {}
    for r in records:
        for k, v in ((r.get('system') or {}).get('errors') or {}).items():
            errors[k] = errors.get(k, 0) + v
        for k, v in ((r.get('system') or {}).get('entry_gates') or {}).items():
            gates[k] = gates.get(k, 0) + v
    lessons = {key_: [(r['day'], x) for r in records for x in ((r.get('perspective') or {}).get(key_) or [])] for key_ in T.LISTS}
    lessons['summaries'] = [(r['day'], (r.get('perspective') or {}).get('summary')) for r in records if (r.get('perspective') or {}).get('summary')]
    return {'kind': kind, 'key': key, 'label': T.label(kind, key), 'since': since, 'until': until,
            'built': datetime.now(KST).isoformat(timespec='seconds'), 'days': len(records),
            'first': records[0]['day'] if records else None, 'last': records[-1]['day'] if records else None,
            'books': period(records), 'routes': route_stats(records), 'monthly': monthly, 'market': market,
            'issues': sorted(issues, key=lambda h: (-h['stars'], h['day']))[:30],
            'issue_categories': dict(sorted(categories.items(), key=lambda kv: -kv[1])),
            'system': {'errors': dict(sorted(errors.items(), key=lambda kv: -kv[1])), 'entry_gates': dict(sorted(gates.items(), key=lambda kv: -kv[1]))},
            'lessons': lessons, 'perspective': perspective(key)}


def store_period(record, db=DB):
    db = Path(db)
    with sqlite3.connect(db, timeout=10) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS periods (key TEXT PRIMARY KEY, kind TEXT, data TEXT, updated TEXT)')
        conn.execute('INSERT OR REPLACE INTO periods VALUES (?, ?, ?, ?)',
                     (record['key'], record['kind'], json.dumps(record, ensure_ascii=False, default=str), datetime.now(KST).isoformat(timespec='seconds')))
    db.chmod(0o600)
    return record


def load_period(key, db=DB):
    try:
        from sqlite_ro import ro_connect
        with ro_connect(Path(db)) as conn:
            row = conn.execute('SELECT data FROM periods WHERE key = ?', (key,)).fetchone()
    except Exception:
        return None
    return _record(row[0]) if row else None


def week(day, db=DB):
    """The week so far (Monday .. `day`) pooled per book, like period(): the Friday journal's weekly line."""
    d = date.fromisoformat(day)
    return period(days((d - timedelta(days=d.weekday())).isoformat(), day, db))


def status(day, db=DB):
    """Where a day's journal stands, for the writer: trading day or not, collected or not, written or not."""
    import trading_day_start
    is_open, note = trading_day_start.trading_day(datetime.fromisoformat(day))
    record = load(day, db)
    return {'day': day, 'trading_day': is_open, 'note': note, 'collected': record is not None,
            'fills': sum(len(b['fills']) for b in (record or {}).get('books', {}).values()),
            'written': {ext: (REVIEWS / f'journal-{day}.{ext}').exists() for ext in ('json', 'md')},
            'merged': bool((record or {}).get('perspective'))}


def update_periods(day, db=DB):
    """After a day is stored: the month, quarter, half and year holding it (the learning watch, after the close)."""
    return [store_period(build_period(kind, T.key_of(kind, day), db), db)['key'] for kind in T.KINDS]


def merge_period(key, db=DB):
    """Rebuild a period with its written perspective (after the period task wrote reviews/journal-<key>.json)."""
    _private(key)
    return store_period(build_period(T.kind_of(key), key, db), db)['key']


def period_report_md(record):
    """The period journal in the template's sections (journal_template.PERIOD)."""
    H = dict(T.PERIOD)
    view = record.get('perspective') or {}
    books = record.get('books') or {}
    paper = (books.get('paper') or {})
    pp = paper.get('performance') or {}
    lines = [T.title(record['kind'], record['key']), '']
    summary = view.get('summary') or (
        f"{record['label']} 일지 {record['days']}일 · 모의(Jev) 실현 {_won(pp.get('realized_krw'))}(매수 대비 {_pct(pp.get('return_on_buys_pct'))}), "
        f"승률 {pp.get('win_rate') if pp.get('win_rate') is not None else '—'}%, 최대 낙폭 {_won(paper.get('max_drawdown_krw'))}. "
        + T.SUMMARY_PENDING)
    lines += [f'> {_md(summary)}', '']

    lines += [f"## {H['numbers']}", '', '| 계좌 | 실현손익 | 매수 대비 | 승률 | 손익비 | 매매일 · 이익일 | 최대 낙폭 |', '|---|---|---|---|---|---|---|']
    for b in books.values():
        p = b['performance']
        lines.append(f"| {b['label']} | {_won(p['realized_krw'])} | {_pct(p['return_on_buys_pct'])} | "
                     f"{p['win_rate'] if p['win_rate'] is not None else '—'}% | {p['profit_factor'] or '—'} | "
                     f"{b['trading_days']} · {b['up_days']} | {_won(b['max_drawdown_krw'])} |")
    if not books:
        lines.append('| — | 일지 없음 | — | — | — | — | — |')
    lines += ['', f"_일지 {record['days']}일 ({record.get('first') or '—'} ~ {record.get('last') or '—'}) · 체결가 기준 · 수수료·세금 미반영_", '']

    lines += [f"## {H['flow']}", '']
    labels = [b['label'] for b in books.values()]
    if record['kind'] == 'month':
        daily = {}
        for b in books.values():
            for day, pnl, _ in b['daily']:
                daily.setdefault(day, {})[b['label']] = pnl
        rows, head = sorted(daily.items()), '날짜'
    else:
        rows, head = sorted((record.get('monthly') or {}).items()), '월'
    if rows and labels:
        lines += [f"| {head} | " + ' | '.join(labels) + ' |', '|---|' + '---|' * len(labels)]
        lines += [f'| {k} | ' + ' | '.join(_won(v.get(label)) for label in labels) + ' |' for k, v in rows]
        lines.append('')
    for b in books.values():
        p = b['performance']
        best, worst = b.get('best_day'), b.get('worst_day')
        bits = [f"최고일 {best[0]} {_won(best[1])}" if best and best[1] > 0 else None,
                f"최저일 {worst[0]} {_won(worst[1])}" if worst and worst[1] < 0 else None,
                f"가장 큰 이익 {str(p['best_trade']['time'])[:10]} {p['best_trade']['symbol']} {_won(p['best_trade']['pnl'])}" if p.get('best_trade') else None,
                f"가장 큰 손실 {str(p['worst_trade']['time'])[:10]} {p['worst_trade']['symbol']} {_won(p['worst_trade']['pnl'])}" if p.get('worst_trade') else None]
        if any(bits):
            lines.append(f"- **{b['label']}**: " + ' · '.join(x for x in bits if x))
    if not rows:
        lines.append('_이 기간 손익 기록이 없습니다._')
    lines.append('')

    lines += [f"## {H['routes']}", '']
    routes = record.get('routes') or {}
    if routes:
        lines += ['| 경로 | 왕복 | 이익 | 승률 | 실현 |', '|---|---|---|---|---|']
        lines += [f"| {_md(k)} | {v['n']} | {v['wins']} | {format(v['wins'] / v['n'] * 100, '.0f') + '%' if v['n'] else '—'} | {_won(v['realized_krw'])} |"
                  for k, v in routes.items()]
    else:
        lines.append('_진입 경로 기록이 없습니다._')
    lines.append('')

    lines += [f"## {H['symbols']}", '']
    any_symbol = False
    for b in books.values():
        ranked = list(b['performance']['by_symbol'].items())
        if not ranked:
            continue
        any_symbol = True
        names = stock_names({k for k, _ in ranked})
        top = ranked[:5] + [x for x in ranked[-5:] if x not in ranked[:5]]
        lines += [f"**{b['label']}** (이익 큰 5 · 손실 큰 5)", '', '| 종목 | 실현 |', '|---|---|']
        lines += [f"| [{names.get(k, k)} ({k})]({stock_link(k)}) | {_won(v)} |" for k, v in top] + ['']
    if not any_symbol:
        lines += ['_청산한 종목이 없습니다._', '']

    lines += [f"## {H['market']}", '']
    market = record.get('market') or {}
    if market:
        lines += ['| 지표 | 기간 시작 | 기간 끝 | 변화 |', '|---|---|---|---|']
        lines += [f"| [{n}]({QUOTE_LINKS.get(n, '#')}) | {m['start']:,.2f} | {m['end']:,.2f} | {_pct(m.get('change_pct'))} |" for n, m in market.items()]
        kospi = market.get('코스피') or {}
        big = [f"{d} {_pct(c)}" for d, c in sorted(kospi.get('down', []) + kospi.get('up', []))]
        lines += ['', f"- 코스피 ±2% 이상 움직인 날 {len(big)}일" + (f": {' · '.join(big)}" if big else '')]
        for key, title in (('market', '시황·수급'), ('global', '해외 증시·금리·환율·원자재·코인')):
            if (view.get('views') or {}).get(key):
                lines += ['', f"**{title}** — {_md_links_safe(view['views'][key])}"]
    else:
        lines.append('_이 기간 지표 기록이 없습니다._')
    lines.append('')

    lines += [f"## {H['core_issues']}", '']
    for i, item in enumerate(view.get('top_issues') or [], 1):
        n = star_count(item.get('stars'))
        bold = '**' if n >= 4 else ''
        lines.append(f"{i}. {star_text(n)} {bold}{_md(item.get('title', ''))}{bold} — {_md(item.get('why', ''))}")
        lines += [f"   - [{_md(x.get('title'))}]({safe_url(x.get('url'))})" for x in item.get('links') or [] if safe_url(x.get('url'))]
    if view.get('top_issues'):
        lines += ['', '_아래는 일지에 쌓인 ★4~5 헤드라인입니다._', '']
    if record.get('issue_categories'):
        lines += ['분야별 ★4~5 건수: ' + ' · '.join(f'{k} {v}' for k, v in record['issue_categories'].items()), '']
    lines += [f"- {star_text(h['stars'])} {h['day']} · {'**' if h['stars'] >= 5 else ''}[{_md(h['title'])}]({h['link']})"
              f"{'**' if h['stars'] >= 5 else ''} · {h.get('publisher') or ''} · {h['category']}" for h in (record.get('issues') or [])[:15]]
    if not (record.get('issues') or view.get('top_issues')):
        lines.append('_이 기간 ★4 이상 이슈가 없습니다._')
    lines.append('')

    lines += [f"## {H['system']}", '']
    system = record.get('system') or {}
    if system.get('errors'):
        lines.append('- 오류·결함 합계: ' + ' · '.join(f'{k} {v}건' for k, v in system['errors'].items()))
    if system.get('entry_gates'):
        lines.append('- 진입하지 않은 이유 합계(상위): ' + ' · '.join(f'{k} {v}' for k, v in list(system['entry_gates'].items())[:8]))
    if not (system.get('errors') or system.get('entry_gates')):
        lines.append('- 오류·진입 차단 기록이 없습니다.')
    lines.append('')

    lessons = record.get('lessons') or {}
    extra = {'retrospective': [f'- {d} · {_md_links_safe(x)}' for d, x in lessons.get('summaries') or []],
             'strategy_improvements': [f'- {d} · {_md_links_safe(x)}' for d, x in lessons.get('strategy_improvements') or []],
             'system_improvements': [f'- {d} · {_md_links_safe(x)}' for d, x in lessons.get('system_improvements') or []]}
    extra = {k: ['_기간 분석 작성 전 — 일별 일지에서 모은 내용입니다._', ''] + v for k, v in extra.items() if v}
    return '\n'.join(lines + written_sections(view, T.PERIOD, extra)).rstrip() + '\n'


if __name__ == '__main__':
    main()
