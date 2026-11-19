"""Notifier: a decoupled watcher that reads the engines' own records and posts them to two channels, each delivered to
every configured destination: Discord webhooks, a Telegram bot and Slack incoming webhooks (user 2026-11-19).

REALTIME (Jev): shared paper fills, fills of every Jev live account, a 15:40 day summary (KRX trading days).
LONGTERM (vibe long-term): daily decision, virtual orders, closing NAV, long-term live account fills and plans.
Report documents (journal, improvement, oversold audit, premarket, long-term strategy) are HTML pages on GitHub Pages:
report_pages.cycle builds, publishes and sends their links in the same pass (2026-11-17).
Read-only on every ledger; the only thing written is the cursor file (config_store.write_json), after each delivered
message. Addresses come from .env and are never logged: DISCORD_WEBHOOK_REALTIME / _LONGTERM, SLACK_WEBHOOK_REALTIME /
_LONGTERM, TELEGRAM_BOT_TOKEN with TELEGRAM_CHAT_REALTIME / _LONGTERM. Each destination keeps its own sent list, so a
failure at one never resends to another and a failed send keeps the item unsent for that destination only. Fills older than RECENT_DAYS are not alerted (the cursor keeps only the last SEEN_KEEP ids).

    PYTHONPATH=agent:scripts:streamlit_namuh .venv/bin/python scripts/discord_notify.py --watch     # every 30 s
    ... --once [--dry-run]     one pass (dry-run prints the messages, sends nothing, writes no cursor)
    ... --test                 one "connection check" message to every configured destination
"""
from __future__ import annotations

import argparse
import http.client
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

sys.path.append(str(Path(__file__).resolve().parents[1] / 'streamlit_namuh'))  # engine_compare (P&L); launchd's PYTHONPATH lacks it

import api_calls  # noqa: F401 - counts this process's external calls (dashboard 'API 연동')
import vibe_longterm as L
from config_store import write_json
from local_env import ENV

KST = ZoneInfo('Asia/Seoul')
HOME = Path.home() / '.vibe-trading' / 'nhplug'
STATE = HOME / 'discord-notify.json'
LIMIT = 1900  # Discord allows 2000 characters per message
SEEN_KEEP = 3000
RECENT_DAYS = 3  # fills older than this are never sent: the full history is re-read every pass but seen keeps SEEN_KEEP ids
SUMMARY_AT = '15:40'
WEBHOOK_KEYS = {'realtime': 'DISCORD_WEBHOOK_REALTIME', 'longterm': 'DISCORD_WEBHOOK_LONGTERM'}
CHANNEL_KEYS = {  # destination -> channel -> .env key (Telegram also needs TELEGRAM_BOT_TOKEN)
    'discord': WEBHOOK_KEYS,
    'telegram': {'realtime': 'TELEGRAM_CHAT_REALTIME', 'longterm': 'TELEGRAM_CHAT_LONGTERM'},
    'slack': {'realtime': 'SLACK_WEBHOOK_REALTIME', 'longterm': 'SLACK_WEBHOOK_LONGTERM'},
}
TELEGRAM_TOKEN_KEY = 'TELEGRAM_BOT_TOKEN'
DEST_KO = {'discord': 'Discord', 'telegram': 'Telegram', 'slack': 'Slack'}
DEST_LIMIT = {'discord': LIMIT, 'telegram': 4000, 'slack': 3500}  # Telegram allows 4096, Slack keeps a message readable
SEEN_KEY = {'discord': 'seen', 'telegram': 'seen_telegram', 'slack': 'seen_slack'}  # Discord keeps the old key
ROUTE_KO = {'jev': 'Jev 판단', 'watchlist_strength': '관심종목 강도 경로', 'split': '가격 단계 추가', 'manual': '수동',
            'new_listing': '신규주', 'vibe': 'vibe'}


# ---------- formatting ----------
def split_text(text, limit=LIMIT):
    """Pieces of at most `limit` characters, cut at line breaks where possible."""
    parts, current = [], ''
    for line in str(text).splitlines() or ['']:
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ''
            parts.append(line[:limit])
            line = line[limit:]
        if current and len(current) + len(line) + 1 > limit:
            parts.append(current)
            current = ''
        current = f'{current}\n{line}' if current else line
    return parts + [current] if current else parts


def clock(stamp):
    try:
        return datetime.fromisoformat(str(stamp)).astimezone(KST).strftime('%H:%M')
    except ValueError:
        return ''


def won(value):
    return f'{round(value):,}'


EXIT_KO = {'breakeven_stop': '본전 손절', 'take_profit': '익절', 'stop_loss': '손절', 'trailing_stop': '추적 손절',
           'session_flatten': '장 마감 정리', 'regime_trim': '국면 절반 매도', 'ipo_time_stop': '신규주 시간 손절',
           'direction_switch': '방향 전환 매도'}


def sell_head(route, pnl, closed=False):
    """user 2026-11-19: '🛑 손절' only for a loss taken by a stop route; the whole position sold is '전량 매도' (💰 at a profit);
    otherwise '💰 익절' for a take-profit, else '🔴 매도'. Unknown P&L never claims a stop."""
    text = f"{route} {EXIT_KO.get(route, '')}"
    if pnl is not None and pnl < 0 and ('손절' in text or 'stop' in text):
        return '🛑 손절'
    if closed:
        return '💰 전량 매도' if pnl is not None and pnl > 0 else '🔴 전량 매도'
    if '익절' in text or 'take_profit' in text:
        return '💰 익절'
    return '🔴 매도'


def exit_reason(route, pnl):
    """The exit route in Korean; a stop route that did not lose says 청산, not 손절 (a trailing or break-even stop above cost)."""
    reason = EXIT_KO.get(route) or ROUTE_KO.get(route, route)
    return reason.replace('손절', '청산') if '손절' in reason and not (pnl is not None and pnl < 0) else reason


def fill_line(row, names, label='', route='', pnl=None, ret=None, closed=False):
    """One fill. Sells say whether they stopped out at a loss, took profit, sold everything or just sold, and the realized
    P&L in won and % against the moving-average cost (before fees and tax)."""
    buy = row['side'] == 'BUY'
    code = row['symbol']
    name = names.get(code)
    head = '🟢 매수' if buy else sell_head(route or '', pnl, closed)
    parts = [f"{head} · {name + '(' + code + ')' if name else code} {row['qty']:,}주 × {won(row['price'])}원"]
    if label:
        parts.append(label)
    reason = exit_reason(route, pnl) if route else ''
    if route and not head.endswith(reason):  # '🛑 손절 · 손절' says it twice
        parts.append(reason)
    if not buy:
        parts.append(f"실현손익 **{pnl:+,.0f}원" + (f' ({ret:+.2f}%)' if ret is not None else '') + '**' if pnl is not None else '실현손익 확인 불가')
    parts.append(clock(row['time']))
    return ' · '.join(parts)


def names_map():
    from etf_universe import NAMES, SYMBOLS  # the 7 ETFs Jev trades (114800 KODEX 인버스 ...)
    names = {code: NAMES[key] for key, code in SYMBOLS.items()}
    names.update({a[0]: a[1] for a in L.ASSETS.values()})  # the long-term ETFs (379810 KODEX 미국나스닥100 ...)
    for file, key in (('movers.json', 'rows'), ('watchlist.json', 'stocks')):
        try:
            data = json.loads((HOME / 'shared-etf-paper' / file).read_text())
        except (OSError, ValueError):
            continue
        for code, row in ((data or {}).get(key) or {}).items():
            if isinstance(row, dict) and row.get('name'):
                names.setdefault(code, row['name'])
    return names


BOOK_LABELS = {**L.BOOK_LABELS, L.CONTRIBUTED: '적립 누계'}


# ---------- sources: each returns [(channel, id, text)] ----------
def recent_day():
    """The oldest fill day still alerted (YYYY-MM-DD)."""
    return (datetime.now(KST) - timedelta(days=RECENT_DAYS)).strftime('%Y-%m-%d')


def paper_fills():
    db = HOME / 'shared-etf-paper' / 'orders.sqlite3'
    if not db.exists():
        return []
    from sqlite_ro import ro_connect
    try:
        with ro_connect(db) as con:
            fills = con.execute('SELECT order_id,symbol,side,quantity,price,time FROM fills ORDER BY rowid').fetchall()
    except sqlite3.Error:
        return []
    return [(oid, {'symbol': s, 'side': side, 'qty': q, 'price': p, 'time': t}) for oid, s, side, q, p, t in fills]


def paper_items(names, seen):
    since = recent_day()
    fills = paper_fills()
    fresh = [(oid, r) for oid, r in fills if str(r['time'])[:10] >= since and f'paper:{oid}' not in seen]
    if not fresh:
        return []
    info = {}
    try:  # P&L and route come from the dashboard's journal; a failure only drops those details
        from engine_compare import journal
        root = HOME / 'shared-etf-paper'
        info = {(r['time'], r['symbol'], r['side']): r for r in journal(root / 'orders.sqlite3', root / 'events.jsonl')}
    except Exception:
        pass
    held, after = {}, {}
    for oid, row in fills:  # shares held after each fill (the whole history), so a sale knows it closed the position
        held[row['symbol']] = held.get(row['symbol'], 0) + (row['qty'] if row['side'] == 'BUY' else -row['qty'])
        after[oid] = held[row['symbol']]
    items, cache = [], {}
    for oid, row in fresh:
        j = info.get((row['time'], row['symbol'], row['side'])) or {}
        text = fill_line(row, names, '모의', j.get('path') or '', j.get('pnl'), j.get('return_pct'),
                         closed=row['side'] == 'SELL' and after.get(oid, 1) <= 0)
        items.append(('realtime', f'paper:{oid}', text + day_status(cache, {}, 'paper-shared', row)))
    return items


def paper_exits():
    """[(time, symbol, route)] of the paper runner's sells, for naming a mirrored live sell; [] when unreadable."""
    try:
        from engine_compare import journal
        root = HOME / 'shared-etf-paper'
        return [(r['time'], r['symbol'], r.get('path') or '') for r in journal(root / 'orders.sqlite3', root / 'events.jsonl') if r['side'] == 'SELL']
    except Exception:
        return []


def live_exit_route(row):
    """The live mirror's own exit behind this sell (follow_exits: the stop from the live average price, or a dashboard
    sell request), joined to the sell's order number; '' when none or the guard DB is unreadable (review5)."""
    from sqlite_ro import ro_connect
    try:
        with ro_connect(HOME / 'live-orders.sqlite3', 2) as db:
            got = db.execute('SELECT f.reason FROM live_intents i JOIN follow_exits f ON f.scope=i.scope AND f.day=i.day AND '
                             'f.symbol=i.symbol WHERE i.order_number=? AND i.day=? AND i.symbol=?',
                             (row['order_no'], str(row['time'])[:10].replace('-', ''), row['symbol'])).fetchone()
    except sqlite3.Error:
        return ''
    if not got:
        return ''
    return '실계좌 손절' if 'stop' in got[0] else '대시보드 매도 요청' if 'requested' in got[0] else ''


def mirrored_route(exits, row, window_min=20):
    """The paper sell a live sell followed: same symbol, at most `window_min` minutes earlier (the mirror lags a little)."""
    try:
        at = datetime.fromisoformat(str(row['time']))
    except ValueError:
        return ''
    best = ''
    for stamp, symbol, route in exits:
        try:
            gap = (at - datetime.fromisoformat(str(stamp))).total_seconds() / 60
        except ValueError:
            continue
        if symbol == row['symbol'] and -1 <= gap <= window_min:
            best = route
    return best


def live_items(accounts, names, seen):
    """Live fills with realized P&L on a moving-average cost book of that account's own recorded fills. A sale of
    shares bought before fills were recorded is not covered and says so instead of guessing."""
    from live_fills import rows
    from engine_compare import apply_fill
    items, exits, cache, since = [], None, {}, recent_day()
    for key, record in accounts.items():
        channel = {'jev': 'realtime', 'longterm': 'longterm'}.get(record.get('engine'))
        if record.get('kind') != 'live' or not channel:
            continue
        book = {}
        for r in rows(HOME, key):
            held, avg = book.get(r['symbol'], (0, 0.0))
            covered = r['side'] == 'BUY' or held >= r['qty']
            held, avg, _, pnl, ret = apply_fill(held, avg, r['side'], r['qty'], r['price'])
            book[r['symbol']] = (held, avg)
            # the day, not the time: a stored T00:00:00 resolves to its submission time only while the guard DB reads
            ident = f"live:{key}:{r['time'][:10]}:{r['order_no']}"
            if r['time'][:10] < since or ident in seen:
                continue
            route = ''
            if r['side'] == 'SELL' and channel == 'realtime':
                exits = paper_exits() if exits is None else exits
                route = mirrored_route(exits, r) or live_exit_route(r)
            elif r['side'] == 'SELL':
                route = '리밸런싱 매도'
            text = fill_line(r, names, record.get('label') or key, route, pnl if covered else None, ret if covered else None,
                             closed=r['side'] == 'SELL' and covered and held == 0)
            items.append((channel, ident, text + day_status(cache, accounts, key, r)))
    return items


def longterm_items(seen, cursor, names_ko):
    db = HOME / 'vibe-longterm' / 'longterm.sqlite3'
    if not db.exists():
        return [], cursor
    from sqlite_ro import ro_connect
    items, now = [], datetime.now(KST)
    today = now.strftime('%Y-%m-%d')
    try:
        with ro_connect(db) as con:
            rows = con.execute('SELECT rowid,time,day,kind,regime,mode,modules,reason,orders FROM decisions WHERE rowid>? ORDER BY rowid',
                               (cursor,)).fetchall()
            books = dict(con.execute('SELECT book, value FROM nav WHERE day=?', (today,)).fetchall()) if now.strftime('%H:%M') >= '15:40' else {}
            evidence = {}
            if rows and con.execute("SELECT 1 FROM sqlite_master WHERE name='evidence'").fetchone():
                for t, k, summary in con.execute('SELECT time, kind, summary FROM evidence WHERE time IN (%s) ORDER BY rowid' % ','.join('?' * len(rows)),
                                                 [r[1] for r in rows]):
                    evidence.setdefault((t, k), []).append(summary)
    except sqlite3.Error:
        return [], cursor
    for rowid, stamp, day, kind, regime, mode, modules, reason, orders in rows:
        cursor = max(cursor, rowid)
        if f'lt:{rowid}' in seen:  # sent on an earlier pass (the cursor stepped back for an unsent row before it)
            continue
        try:
            orders = json.loads(orders) if orders else []
            modules = json.loads(modules) if modules else []
        except ValueError:
            orders, modules = [], []
        label = L.REGIMES.get(regime, (regime,))[0]
        if kind == 'regime':
            active = ', '.join(names_ko.get(m, m) for m in modules) or '없음'
            items.append(('longterm', f'lt:{rowid}', f'🧭 {day} 장기투자 판단 · 국면 {label} · 모드 {L.MODES.get(mode, mode)}\n활성 거장: {active}\n{reason or ""}'.strip()))
        elif kind in ('plan', 'order', 'dca') and orders:
            body = '\n'.join(f"{'🟢 매수' if o.get('side') == 'BUY' else '🔴 매도'} · {o.get('code')} {o.get('qty'):,}주 × {won(o.get('price') or 0)}원"
                             for o in orders)
            dca = '💰 적립식 ' + ('가상 주문' if any('status' in o for o in orders) else '계획')  # a sent order has a status
            title = {'order': '🧾 가상 주문', 'plan': '📝 주문 계획', 'dca': dca}[kind]
            why = evidence.get((stamp, kind), [])  # the orders' evidence summaries (longterm_evidence, 2026-11-18)
            tail = ''.join(f'\n근거: {w}' for w in why[:2]) + ('\n전체 근거: 대시보드 장기투자 → 계좌·실행 → 포트폴리오·주문 → 왜 샀나·팔았나' if why else '')
            items.append(('longterm', f'lt:{rowid}', f"{title} · {day} · {label}\n{body}" + (f'\n{reason}' if kind == 'dca' and reason else '') + tail))
        elif kind in ('error', 'missed'):  # a long-term step that failed or ran out of its window (2026-11-18)
            items.append(('longterm', f'lt:{rowid}', f"⚠️ {day} 장기투자 {L.DECISION_KINDS[kind]}\n{reason or ''}".strip()))
    ident = f'nav:{today}'
    if books and ident not in seen:
        items.append(('longterm', ident, f'📈 {today} 장기투자 가상 NAV · ' + ' · '.join(f"{BOOK_LABELS.get(b, b)} {won(v)}원" for b, v in sorted(books.items()))))
    return items, cursor


def plan_items(seen):
    path = HOME / 'longterm-live.jsonl'
    items = []
    try:
        lines = path.read_text().splitlines()[-400:]
    except OSError:
        return items
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get('action') not in ('PLAN', 'DCA') or not e.get('orders'):
            continue
        day = str(e.get('time', ''))[:10]
        # once per kind and mode a day: a plan made live after a dry one is sent too, and so is the day's 적립식
        ident = f"{e['action'].lower()}:{e.get('account')}:{day}:{e.get('execution', '')}"
        if ident in seen or any(i[1] == ident for i in items):
            continue
        body = '\n'.join(f"{'🟢 매수' if o.get('side') == 'BUY' else '🔴 매도'} · {o.get('code')} {o.get('qty')}주" for o in e['orders'])
        title = '📝 실계좌 주문 계획' if e['action'] == 'PLAN' else '💰 실계좌 적립식 주문'
        items.append(('longterm', ident, f"{title} · {e.get('account')} · {day} · {e.get('execution', '')}\n{body}"))
    return items


def sold_cost(row):
    """Cost of the shares a sell closed: proceeds minus its realized P&L (moving-average cost, before fees and tax)."""
    return row['qty'] * row['price'] - row['pnl']


def paper_balances(day):
    """Today's paper account snapshots (account-snapshots.jsonl, every 5 minutes), oldest first."""
    try:
        lines = (HOME / 'shared-etf-paper' / 'account-snapshots.jsonl').read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if str(row.get('time', '')).startswith(day) and isinstance(row.get('total_assets'), (int, float)):
            out.append(row)
    return out


def live_closed(key, day):
    """Today's covered sells of one live account with realized P&L on its moving-average book."""
    from live_fills import rows
    from engine_compare import apply_fill
    book, out = {}, []
    for r in rows(HOME, key):
        held, avg = book.get(r['symbol'], (0, 0.0))
        covered = r['side'] == 'BUY' or held >= r['qty']
        held, avg, _, pnl, _ = apply_fill(held, avg, r['side'], r['qty'], r['price'])
        book[r['symbol']] = (held, avg)
        if r['side'] == 'SELL' and covered and pnl is not None and str(r['time']).startswith(day):
            out.append({**r, 'pnl': pnl})
    return out


def day_books(accounts, day=None):
    """{key: {label, pnl, cost, wins, closed, first, last, at}} for the paper account and every live account: today's
    realized P&L and the total assets from today's first to latest snapshot. A part that cannot be read is None."""
    day = day or datetime.now(KST).strftime('%Y-%m-%d')
    books = {}
    try:
        from engine_compare import journal
        root = HOME / 'shared-etf-paper'
        closed = [r for r in journal(root / 'orders.sqlite3', root / 'events.jsonl')
                  if r['side'] == 'SELL' and r.get('pnl') is not None and str(r['time']).startswith(day)]
    except Exception:
        closed = None
    snaps = paper_balances(day)
    books['paper-shared'] = {'label': '모의(Jev)', 'closed': closed, 'snaps': snaps}
    from live_fills import balances
    for key, record in accounts.items():
        if record.get('kind') != 'live':
            continue
        try:
            closed = live_closed(key, day)
        except Exception:
            closed = None
        books[key] = {'label': record.get('label') or key, 'closed': closed, 'snaps': balances(HOME, key, day)}
    for book in books.values():
        closed, snaps = book.pop('closed'), book.pop('snaps')
        book['pnl'] = sum(r['pnl'] for r in closed) if closed is not None else None
        book['cost'] = sum(sold_cost(r) for r in closed) if closed else 0
        book['closed'] = len(closed) if closed is not None else None
        book['wins'] = sum(1 for r in closed if r['pnl'] > 0) if closed else 0
        book['first'] = snaps[0]['total_assets'] if snaps else None
        book['last'] = snaps[-1]['total_assets'] if snaps else None
        book['at'] = clock(snaps[-1]['time']) if snaps else ''
    return books


def pct(part, whole):
    return f' ({part / whole * 100:+.2f}%)' if whole else ''


def book_line(book):
    """'오늘 실현 +12,000원 (+1.20%) · 총자산 1,012,000원 (오늘 +12,000원 +1.20%, 14:55 기준)'."""
    parts = []
    if book['pnl'] is None:
        parts.append('오늘 실현 확인 불가')
    else:
        parts.append(f"오늘 실현 {book['pnl']:+,.0f}원{pct(book['pnl'], book['cost'])}")
    if book['last'] is None:
        parts.append('총자산 기록 없음')
    else:
        change = book['last'] - book['first']
        parts.append(f"총자산 {won(book['last'])}원 (오늘 {change:+,.0f}원{pct(change, book['first']).replace(' (', ' ').replace(')', '')}, {book['at']} 기준)")
    return ' · '.join(parts)


def day_status(cache, accounts, key, row):
    """Second line of a sell: that account's realized P&L so far today and its total assets with today's change.
    Computed once per pass and day (`cache`); buys get nothing, a failure drops only this line."""
    if row['side'] != 'SELL':
        return ''
    day = str(row['time'])[:10]
    try:
        if day not in cache:
            cache[day] = day_books(accounts, day)
        book = cache[day].get(key)
    except Exception:
        return ''
    return f'\n　{book_line(book)}' if book else ''


def summary_item(seen, accounts=None):
    now = datetime.now(KST)
    today = now.strftime('%Y-%m-%d')
    ident = f'summary:{today}'
    if now.strftime('%H:%M') < SUMMARY_AT or ident in seen or not L.trading_day(now.date()):
        return []
    try:
        books = day_books(accounts or {}, today)
    except Exception:
        return []
    lines = [f'🏁 {today} 마감 요약']
    for book in books.values():
        if book['closed'] is None and book['last'] is None:
            continue
        record = f" · 청산 {book['closed']}건 · 승 {book['wins']}/패 {book['closed'] - book['wins']}" if book['closed'] else ''
        lines.append(f"**{book['label']}**{record}\n　{book_line(book)}")
    live = [b for k, b in books.items() if k != 'paper-shared' and b['last'] is not None and b['pnl'] is not None]
    if len(live) > 1:  # the live accounts together (the paper account's virtual money is kept apart)
        total = {'pnl': sum(b['pnl'] for b in live), 'cost': sum(b['cost'] for b in live), 'first': sum(b['first'] for b in live),
                 'last': sum(b['last'] for b in live), 'at': max(b['at'] for b in live)}
        lines.append(f"**실계좌 합계**\n　{book_line(total)}")
    return [('realtime', ident, '\n'.join(lines))]


def collect(state, accounts, names, names_ko, urls=None):
    seen = done_ids(state, {ch: as_targets(v) for ch, v in (urls or {}).items()}) if urls else set(state.get('seen', []))
    items = paper_items(names, seen) + live_items(accounts, names, seen) + plan_items(seen) + summary_item(seen, accounts)
    extra, state['lt_cursor'] = longterm_items(seen, state.get('lt_cursor', 0), names_ko)
    return items + extra


def baseline(state, accounts, names, names_ko):
    """First run: everything that exists now counts as already sent (no history flood)."""
    state.setdefault('seen', [])
    state['lt_cursor'] = 0
    items = collect(state, accounts, names, names_ko)
    state['seen'] = [i[1] for i in items]
    state['fail'] = {}
    state['started'] = datetime.now(KST).isoformat(timespec='seconds')


# ---------- sending ----------
def post(url, text, timeout=10):
    """POST one message. Returns (ok, retry_after_seconds, permanent_error)."""
    try:
        request = urllib.request.Request(url, json.dumps({'content': text, 'allowed_mentions': {'parse': []}}).encode(),
                                         {'Content-Type': 'application/json', 'User-Agent': 'vibe-trading-notify/1'})
        urllib.request.urlopen(request, timeout=timeout).close()
        return True, 0, False
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            try:
                wait = float(json.loads(exc.read()).get('retry_after', 2))
            except (ValueError, OSError):
                wait = 2.0
            return False, min(wait, 30.0), False
        return False, 0, 400 <= exc.code < 500
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError):  # a broken reply or webhook URL
        return False, 0, False


def send_all(url, texts, sender=post, sleep=time.sleep):
    """Send every piece in order; (ok, permanent) where permanent means a non-429 4xx (do not retry forever)."""
    for index, piece in enumerate(texts):
        for _ in range(3):  # 429 retries
            ok, wait, permanent = sender(url, piece)
            if ok or not wait:
                break
            sleep(wait)
        if not ok:
            return False, permanent
        if index + 1 < len(texts):
            sleep(1)
    return True, False


MD_LINK = re.compile(r'\[([^\]\n]+)\]\((https?://[^)\s]+)\)')


def render(kind, text):
    """The message in each destination's markup: Discord markdown as written, Slack mrkdwn, Telegram plain text."""
    text = str(text)
    if kind == 'slack':
        text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        text = MD_LINK.sub(lambda m: f'<{m[2]}|{m[1]}>', text)
        return re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
    if kind == 'telegram':
        return MD_LINK.sub(lambda m: f'{m[1]} ({m[2]})', text).replace('**', '')
    return text


def _http(url, body, retry_after, timeout):
    """POST JSON. (ok, retry_after_seconds, permanent_error); the address never reaches a log or an exception text."""
    try:
        request = urllib.request.Request(url, json.dumps(body).encode(),
                                         {'Content-Type': 'application/json', 'User-Agent': 'vibe-trading-notify/1'})
        urllib.request.urlopen(request, timeout=timeout).close()
        return True, 0, False
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            return False, min(retry_after(exc), 30.0), False
        return False, 0, 400 <= exc.code < 500
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError):
        return False, 0, False


def post_slack(url, text, timeout=10):
    """One Slack incoming-webhook message (429 waits for Retry-After)."""
    def wait(exc):
        try:
            return float(exc.headers.get('Retry-After') or 2)
        except (TypeError, ValueError):
            return 2.0
    return _http(url, {'text': text, 'unfurl_links': False, 'unfurl_media': False}, wait, timeout)


def post_telegram(address, text, timeout=10):
    """One Telegram Bot API sendMessage to (token, chat_id); plain text, so no markup can break a message."""
    token, chat = address

    def wait(exc):
        try:
            return float(json.loads(exc.read()).get('parameters', {}).get('retry_after', 2))
        except (ValueError, OSError, AttributeError):
            return 2.0
    return _http(f'https://api.telegram.org/bot{token}/sendMessage',
                 {'chat_id': chat, 'text': text, 'disable_web_page_preview': False}, wait, timeout)


POSTERS = {'discord': post, 'telegram': post_telegram, 'slack': post_slack}


def as_targets(value):
    """[(kind, address)] of one channel; a bare string is a Discord webhook (the form every caller used before)."""
    if not value:
        return []
    return [('discord', value)] if isinstance(value, str) else list(value)


def send_to(kind, address, text, sender=None, sleep=time.sleep):
    """Send `text` to one destination in its markup and size; (ok, permanent). `sender` replaces the poster (tests)."""
    return send_all(address, split_text(render(kind, text), DEST_LIMIT[kind]), sender or POSTERS[kind], sleep)


def send_everywhere(targets, text, sender=None, sleep=time.sleep):
    """{kind: ok} for every destination of one channel (one message, no cursor: tests and alerts)."""
    return {kind: send_to(kind, address, text, sender if kind == 'discord' else None, sleep)[0] for kind, address in as_targets(targets)}


def seen_lists(state, targets):
    """Each active destination's sent ids. A destination met for the first time starts from what Discord already sent,
    so turning Telegram or Slack on does not replay the history."""
    kinds = {kind for dests in targets.values() for kind, _ in dests} or {'discord'}
    base = list(state.get('seen', []))
    return {kind: state.setdefault(SEEN_KEY[kind], list(base)) for kind in kinds}


def done_ids(state, targets):
    """Ids every active destination has (what collect may skip); with nothing configured, Discord's list as before."""
    lists = seen_lists(state, targets)
    sets = [set(v) for v in lists.values()]
    return set.intersection(*sets) if sets else set(state.get('seen', []))


def deliver(items, state, urls, sender=post, sleep=time.sleep, save=lambda: None):
    """Send each channel's items in order to each of its destinations; an item unsent at one destination stays for that
    destination on the next pass (its own sent list), never resent where it already went. `save` runs after each
    delivered message, so a pass killed mid-way does not send again what already went out."""
    targets = {ch: as_targets(v) for ch, v in (urls or {}).items()}
    lists, fail = seen_lists(state, targets), state.setdefault('fail', {})
    blocked, collected = set(), state.get('lt_cursor', 0)
    rows = [int(ident[3:]) for _, ident, _ in items if ident.startswith('lt:')]
    if rows:  # a save mid-pass must not carry the cursor past a long-term row not sent yet
        state['lt_cursor'] = min(collected, min(rows) - 1)
    for channel, ident, text in items:
        for kind, address in targets.get(channel) or []:
            sent = lists[kind]
            if ident in sent or (channel, kind) in blocked:
                continue
            ok, permanent = send_to(kind, address, text, sender if kind == 'discord' else None, sleep)
            key = ident if kind == 'discord' else f'{kind}:{ident}'
            if ok:
                sent.append(ident)
                fail.pop(key, None)
                save()
                sleep(1)
                continue
            fail[key] = fail.get(key, 0) + 1
            if permanent and fail[key] >= 3:
                print(f'skip after 3 rejected tries ({DEST_KO[kind]}): {ident[:40]}', file=sys.stderr)
                sent.append(ident)
                continue
            blocked.add((channel, kind))  # keep the order: later items of this channel wait at this destination

    def finished(channel, ident):
        dests = targets.get(channel) or []
        return bool(dests) and all(ident in lists[kind] for kind, _ in dests)
    unsent = [int(ident[3:]) for channel, ident, _ in items if ident.startswith('lt:') and not finished(channel, ident)]
    # the long-term cursor steps back to the first unsent row, so it is collected again
    state['lt_cursor'] = min(collected, min(unsent) - 1) if unsent else collected
    for kind, sent in lists.items():
        state[SEEN_KEY[kind]] = sent[-SEEN_KEEP:]


# ---------- loop ----------
def load_state():
    try:
        state = json.loads(STATE.read_text())
    except (OSError, ValueError):
        return None
    # live ids carried the fill time until 2026-11-19 (it flips once the guard DB resolves it): keep only its day
    state['seen'] = [re.sub(r'^(live:[^:]+:\d{4}-\d{2}-\d{2})T.*(:\d+)$', r'\1\2', i) for i in state.get('seen', [])]
    return state


def urls_from_env(values=None):
    """{channel: [(kind, address)]} for every destination set in .env (Discord, Telegram, Slack)."""
    values = dotenv_values(ENV, interpolate=False) if values is None else values

    def get(key):
        return (values.get(key) or '').strip()
    token, out = get(TELEGRAM_TOKEN_KEY), {}
    for channel in WEBHOOK_KEYS:
        dests = []
        for kind, keys in CHANNEL_KEYS.items():
            value = get(keys[channel])
            if value and kind != 'telegram':
                dests.append((kind, value))
            elif value and token:
                dests.append((kind, (token, value)))
        out[channel] = dests
    return out


def report_links(urls):
    """Report pages: generate, publish and send links (report_pages). A failure there never stops trade messages."""
    import report_pages
    try:
        return report_pages.cycle(urls)
    except Exception as exc:  # noqa: BLE001 - logged by type only; the dashboard shows per-report reasons
        print(f'report pages failed: {type(exc).__name__}', file=sys.stderr)
        return None


def run_once(urls, dry_run=False, sender=post, sleep=time.sleep, state_path=None, documents=report_links):
    from accounts import load as load_accounts
    names_ko = {k: v[0] for k, v in L.MODULES.items()}
    accounts, names = load_accounts(), names_map()
    state = load_state()
    if state is None and dry_run:
        state = {}  # sample everything that exists; nothing is written
    elif state is None:
        state = {}
        baseline(state, accounts, names, names_ko)
        write_json(state_path or STATE, state)
        print('first run: cursors set to now')
        return []
    items = collect(state, accounts, names, names_ko, urls)
    if dry_run:
        for channel, _, text in items:
            print(f'[{channel}]\n{text[:500]}\n')
        return items
    deliver(items, state, urls, sender, sleep, save=lambda: write_json(state_path or STATE, state))
    write_json(state_path or STATE, state)
    if documents:
        documents(urls)
    return items


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--test', action='store_true')
    args = parser.parse_args()
    urls = urls_from_env()
    if args.test:
        for channel, dests in urls.items():
            if not dests:
                print(channel, 'no destination')
            for kind, ok in send_everywhere(dests, '✅ 연결 확인 · Omni AI Trading 알림').items():
                print(channel, DEST_KO[kind], 'sent' if ok else 'failed')
        return
    if args.once:
        run_once(urls, args.dry_run)
    elif args.watch:
        while True:
            try:
                run_once(urls)
            except Exception as exc:  # never exit on a send/read error
                print(f'pass failed: {type(exc).__name__}', file=sys.stderr)
            time.sleep(30)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
