"""Shared readers over each engine's ledger (journal, positions, events, live mirror rows) and the 성과 비교 tab's
period_view. Read-only (engine_roles.ledger_root). The intraday Jev vs vibe-trading archive screen left on 2026-11-19."""
from __future__ import annotations

import json
import math
import os
import sqlite3
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from sqlite_ro import ro_connect

ENGINES = {'jev': 'Jev', 'vibe': 'vibe-trading'}
COLORS = {'Jev': '#a855f7', 'vibe-trading': '#22c55e'}  # AI violet vs green; every mark also carries the engine name
PATHS = {'watchlist_strength': '관심종목 강도', 'jev': 'Jev 후보', 'split': '분할 추가', 'ipo': '신규주', 'oversold': '낙폭과대 반등', 'oversold_v2': '낙폭과대 선별 v2', 'oversold_v2_add': '선별 v2 2차 매수', 'oversold_v2_reentry': '선별 v2 저점 재매수',
         'vibe-trading': 'vibe 점수', 'manual': '수동 정리', 'jev_fast': 'Jev 빠른 매수(vibe 보조)'}
EXITS = {'inverse_protect': '인버스 수익 지키기 청산', 'inverse_market_rebound': '지수 반등 인버스 청산',  # 2026-11-20
    'take_profit': '익절', 'trailing_stop': '추적 손절', 'stop_loss': '손절', 'session_flatten': '장 마감 정리',
         'jev_sell': 'Jev 매도 의견', 'regime': '국면 청산', 'risk': '위험 관리', 'manual_exit': '수동 정리',
         'split_take_profit': '분할 익절', 'vibe-trading': 'vibe 매도 점수', 'model': 'Jev 매도 판단',
         'regime_exit': '국면 청산', 'protective': '보호 매도', 'direction_switch': '방향 전환 매도',
         'take_profit_all': '전량 익절', 'take_profit_half': '절반 익절', 'weak_market_targets': '약세장 익절',
         'ipo_time_stop': '신규주 시간 손절', 'fade_exit': '매수세 약화 익절'}


def _cycles(events_path, wanted):
    """{cycle_id: [events]} for the wanted cycles; reads the (large) journal once, parsing only matching lines."""
    found = {}
    if not wanted or not events_path or not Path(events_path).exists():
        return found
    marker = '"cycle_id": "'
    with open(events_path, encoding='utf-8') as stream:
        for line in stream:
            at = line.find(marker)
            if at < 0 or line[at + len(marker):at + len(marker) + 32] not in wanted:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            found.setdefault(event['cycle_id'], []).append(event)
    return found


def _advice_for(advice, stamp, symbol, side):
    """The latest vibe advice at or before `stamp` that decided `side` on `symbol`: (score, evidence) or None."""
    for item in advice or []:  # newest first
        if str(item.get('time', '')) > stamp:
            continue
        for decision in item.get('decisions') or []:
            if decision.get('symbol') == symbol and decision.get('action') == side:
                score = ((item.get('scores') or {}).get(symbol) or {})
                return score.get('score'), score.get('evidence') or decision.get('reason') or '', decision.get('route')
    return None


def _reason(side, events, engine, advice, stamp, symbol):
    """(path, basis): how the trade came about and the numbers behind it, from the same runner cycle."""
    kinds = {e.get('event'): e for e in events}
    if side == 'BUY':
        gate, add, submitted = kinds.get('v2_entry_gate'), kinds.get('split_add'), kinds.get('submitted') or {}
        source = (gate or {}).get('source') or submitted.get('source')
        path = f"분할 추가 {add.get('tranche')}차" if add else PATHS.get(source, source or '확인 불가')
        found = _advice_for(advice, stamp, symbol, 'BUY') if (source == 'vibe-trading' or engine == 'vibe') else None
        if found and found[2] == 'strength':
            path = '관심종목 강도(vibe 점수)'
        basis = []
        strength = kinds.get('watchlist_strength')
        if strength and not add:
            basis.append(f"Jev 확률 {strength.get('confidence', 0):.2f} · 오늘 {strength.get('change_pct', 0):+.2f}%")
        candidate = ((kinds.get('jev_shadow') or {}).get('jev') or {}).get('candidate') or {}
        if source == 'jev' and candidate:
            basis.append(f"Jev 후보 {candidate.get('choice')} 신뢰도 {candidate.get('confidence', 0):.2f}")
        if add:
            basis.append(f"첫 매수가 대비 하락 후 반등 · {add.get('qty')}주")
        if source == 'oversold':
            scan = next((e for e in events if e.get('event') == 'oversold_scan' and e.get('symbol') == symbol), {})
            signal = next((e for e in events if e.get('event') == 'oversold_signal' and e.get('symbol') == symbol), {})
            if scan:
                basis.append(f"낙폭 {scan.get('drop_pct', 0):+.2f}% (평소 변동폭 {scan.get('atr_multiple') or 0:.1f}배) · 저가 대비 +{scan.get('bounce_pct', 0):.2f}%")
            if signal.get('engine') == 'jev':
                basis.append(f"Jev {signal.get('choice')} {signal.get('confidence', 0):.2f}")
            elif signal.get('engine') == 'vibe':
                basis.append(f"vibe 점수 {signal.get('score', 0):+.2f}")
        if source in ('oversold_v2', 'oversold_v2_add', 'oversold_v2_reentry'):
            signal = next((e for e in events if e.get('event') == 'oversold_v2_signal' and e.get('symbol') == symbol), {})
            if signal:
                basis.append(f"선별 v2: 낙폭 {signal.get('drop_pct', 0):+.2f}% · 시장 {signal.get('market_low_pct', 0):+.2f}% · "
                             f"{signal.get('hold_days')}거래일 보유 · 손절 −{signal.get('stop_pct', 0):.1f}%"
                             + (f" · 리서치 {({'boost': '강한 동의', 'normal': '보통', 'veto': '거부'}).get(signal.get('research'), '—')}"))
        if found:
            if found:
                basis.append(f"vibe 점수 {found[0]:+.2f} · {found[1]}" if found[0] is not None else found[1])
        return path, ' / '.join(basis)
    risk, sell = kinds.get('v2_risk_exit'), kinds.get('sell_submitted') or {}
    key = (risk or {}).get('reason') or next((k for k in ('split_take_profit', 'manual_exit') if k in kinds), None) \
        or sell.get('trigger')
    path = EXITS.get(key, key or '확인 불가')
    basis = []
    holding = kinds.get('jev_holding_decision')
    if holding and holding.get('choice') == 'SELL':
        basis.append(f"Jev 보유 의견 SELL {holding.get('confidence', 0):.2f}")
    if engine == 'vibe':
        found = _advice_for(advice, stamp, symbol, 'SELL')
        if found:
            basis.append(f"vibe 점수 {found[0]:+.2f} · {found[1]}" if found[0] is not None else found[1])
    return path, ' / '.join(basis)


def apply_fill(held, avg, side, qty, price):
    """One fill on a moving-average cost book: (shares held, average cost) -> (held, avg, cost, pnl, return %).
    A sale beyond the recorded shares counts only the covered part; cost/pnl/return are None when nothing is covered."""
    if side == 'BUY':
        return held + qty, (avg * held + price * qty) / (held + qty), None, None, None
    sold = min(qty, held)
    if not sold:
        return held, avg, None, None, None
    left = held - sold
    return left, avg if left else 0.0, avg * sold, (price - avg) * sold, round((price / avg - 1) * 100, 2)


def journal(orders_db, events_path=None, engine='jev', advice=None):
    """Every verified fill of one engine ledger, oldest first, with moving-average cost, realized P&L/return on sells
    and the decision behind it: [{time, symbol, side, qty, price, amount, avg, pnl, cost, return_pct, after, path, basis}]."""
    if not orders_db or not Path(orders_db).exists():
        return []
    with ro_connect(orders_db) as db:
        fills = db.execute('SELECT order_id,symbol,side,quantity,price,time FROM fills ORDER BY rowid').fetchall()
        try:
            links = dict(db.execute('SELECT order_id,cycle_id FROM decision_order_links').fetchall())
        except sqlite3.Error:
            links = {}
    cycles = _cycles(events_path, {links[f[0]] for f in fills if f[0] in links})
    books, rows = {}, []
    for order_id, symbol, side, qty, price, stamp in fills:
        qty_held, avg, cost, pnl, ret = apply_fill(*books.get(symbol, (0, 0.0)), side, qty, price)
        books[symbol] = (qty_held, avg)
        path, basis = _reason(side, cycles.get(links.get(order_id), []), engine, advice, stamp, symbol)
        rows.append({'time': stamp, 'symbol': symbol, 'side': side, 'qty': qty, 'price': price, 'amount': price * qty,
                     'avg': books[symbol][1] if side == 'BUY' else (cost / qty if cost else None), 'pnl': pnl, 'cost': cost,
                     'return_pct': ret, 'after': qty_held, 'path': path, 'basis': basis})
    return rows


@lru_cache(maxsize=8)
def _cached_journal(orders_db, events_path, engine, stamps, advice_key):
    return journal(orders_db, events_path, engine, json.loads(advice_key) if advice_key else None)


def cached_journal(root, engine, advice=None):
    """journal() re-read only when the ledger, journal or advice changed (the shared journal is ~10 MB)."""
    root = Path(root)
    orders, events = root / 'orders.sqlite3', root / 'events.jsonl'
    stamps = tuple(p.stat().st_mtime if p.exists() else 0 for p in (orders, events))
    key = json.dumps(advice, sort_keys=True, default=str) if engine == 'vibe' and advice else ''
    return _cached_journal(str(orders), str(events), engine, stamps, key)


def in_period(rows, prefix):
    return [r for r in rows if not prefix or r['time'].startswith(prefix)]


def summary(rows):
    """Results of the sells in `rows`: count, wins, losses, realized P&L, return on the cost sold, best/worst."""
    sells = [r for r in rows if r['pnl'] is not None]
    pnl, cost = sum(r['pnl'] for r in sells), sum(r['cost'] for r in sells)
    returns = [r['return_pct'] for r in sells]
    return {'trades': len(rows), 'buys': sum(r['side'] == 'BUY' for r in rows), 'sells': len(sells),
            'wins': sum(r['pnl'] > 0 for r in sells), 'losses': sum(r['pnl'] < 0 for r in sells),
            'pnl': round(pnl), 'return_pct': round(pnl / cost * 100, 2) if cost else None,
            'avg_return_pct': round(sum(returns) / len(returns), 2) if returns else None,
            'best': max(returns, default=None), 'worst': min(returns, default=None)}


def positions(rows):
    """Open positions after the last fill: {symbol: {qty, avg, cost, opened, path}} (opened = first buy since flat)."""
    open_, started = {}, {}
    for r in rows:
        if r['side'] == 'BUY' and r['symbol'] not in started:
            started[r['symbol']] = (r['time'], r['path'])
        if r['after'] == 0:
            started.pop(r['symbol'], None)
            open_.pop(r['symbol'], None)
        elif r['side'] == 'BUY':
            open_[r['symbol']] = {'qty': r['after'], 'avg': r['avg'], 'cost': r['avg'] * r['after'],
                                  'opened': started[r['symbol']][0], 'path': started[r['symbol']][1]}
        else:
            open_[r['symbol']] = {**open_.get(r['symbol'], {}), 'qty': r['after'],
                                  'cost': (open_.get(r['symbol'], {}).get('avg') or 0) * r['after']}
    return open_


def episodes(rows):
    """Each holding from first buy to flat: [{symbol, open, close (None while held), qty, pnl, cost, return_pct, entry, exit}]."""
    current, done = {}, []
    for r in rows:
        ep = current.get(r['symbol'])
        if r['side'] == 'BUY' and ep is None:
            ep = current[r['symbol']] = {'symbol': r['symbol'], 'open': r['time'], 'close': None, 'qty': 0, 'pnl': 0.0,
                                         'cost': 0.0, 'entry': r['path'], 'exit': ''}
        if ep is None:
            continue  # a sale of shares bought before these rows
        ep['qty'] = max(ep['qty'], r['after'])
        if r['pnl'] is not None:
            ep['pnl'] += r['pnl']
            ep['cost'] += r['cost']
            ep['exit'] = r['path']
        if r['after'] == 0:
            ep['close'] = r['time']
            done.append(current.pop(r['symbol']))
    for ep in [*done, *current.values()]:
        ep['return_pct'] = round(ep['pnl'] / ep['cost'] * 100, 2) if ep['cost'] else None
    return sorted([*done, *current.values()], key=lambda e: e['open'])


def opinions(jev_events, advice):
    """Per symbol, the latest views of both engines: Jev candidate probability and holding opinion, vibe score and the
    technical-basic vote. jev_events: recent journal events (oldest first)."""
    from etf_universe import SYMBOLS
    rows = {}
    candidate = None
    for event in reversed(jev_events or []):
        if event.get('event') == 'jev_shadow':
            candidate = ((event.get('jev') or {}).get('candidate') or {})
            if candidate.get('probabilities'):
                candidate = {**candidate, 'time': event.get('time')}
                break
    for choice, prob in ((candidate or {}).get('probabilities') or {}).items():
        if choice != 'WAIT':
            rows.setdefault(SYMBOLS.get(choice, choice), {})['jev_prob'] = prob
    seen = set()
    for event in reversed(jev_events or []):
        if event.get('event') == 'jev_holding_decision' and event.get('symbol') not in seen:
            seen.add(event['symbol'])
            rows.setdefault(event['symbol'], {}).update(jev_holding=f"{event.get('choice')} {event.get('confidence', 0):.2f}",
                                                        jev_holding_conf=event.get('confidence') if event.get('choice') == 'SELL' else None)
    for code, score in ((advice or {}).get('scores') or {}).items():
        rows.setdefault(code, {})['vibe_score'] = score.get('score')
        rows[code]['vibe_evidence'] = score.get('evidence', '')
    for code, vote in ((advice or {}).get('technical_basic') or {}).items():
        if code in rows:
            rows[code].update(vote=vote.get('vote'), vs_vwap_pct=vote.get('vs_vwap_pct'), volume_ratio=vote.get('volume_ratio'))
    for code in (advice or {}).get('strength_candidates') or []:
        rows.setdefault(code, {})['strength'] = True
    return candidate, rows


_TAILS = {}  # path -> {stamp, inode, offset, depth, covered, rows, bad}: one parse per file change serves every limit
TAIL_DEPTH = 20000  # the longest tail a screen asks for: smaller limits slice it instead of parsing again


def _parse(lines):
    """(events, corrupt lines, unread bytes) of raw journal lines. A torn or corrupt line is skipped and counted rather
    than hiding every other event; a last line without its newline that does not parse yet is left unread, because
    the runner may still be writing it."""
    rows, bad, unread = [], 0, 0
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            if n == len(lines) and not line.endswith(b'\n'):
                unread = len(line)
            else:
                bad += 1
    return rows, bad, unread


def _parsed_tail(path, stat, limit):
    from collections import deque
    stamp, hit = (stat.st_mtime_ns, stat.st_size), _TAILS.get(path)
    if hit and hit['inode'] == stat.st_ino and hit['covered'] >= limit:
        if hit['stamp'] == stamp:
            return hit['rows'][-limit:]
        if stat.st_size > hit['stamp'][1]:  # the runners only append: parse the new lines alone (39 MB took ~0.4 s)
            with open(path, 'rb') as stream:
                stream.seek(hit['offset'])
                rows, bad, unread = _parse(stream.readlines())
                offset = stream.tell() - unread
            rows = hit['rows'] + rows
            hit.update(stamp=stamp, offset=offset, rows=rows[-hit['depth']:], bad=hit['bad'] + bad,
                       covered=hit['depth'] if len(rows) > hit['depth'] else hit['covered'])
            return hit['rows'][-limit:]
    depth = max(limit, TAIL_DEPTH)  # first read, a larger limit, or the file was truncated or replaced
    with open(path, 'rb') as stream:
        lines = deque(stream, maxlen=depth)
        offset = stream.tell()
    rows, bad, unread = _parse(lines)
    _TAILS[path] = {'stamp': stamp, 'inode': stat.st_ino, 'offset': offset - unread, 'depth': depth, 'rows': rows, 'bad': bad,
                    'covered': depth if len(lines) == depth else math.inf}  # shorter: the whole file
    return rows[-limit:]


def read_events(path, limit=3000):
    """The last `limit` JSON lines of a journal, parsed once per file change and then only its appended lines (the
    shared events.jsonl is ~39 MB and every tab used to parse it again on each rerun). Corrupt lines are skipped and
    counted (bad_lines). Raises OSError like reading it directly would.
    The event dicts are shared between callers: read them, do not change them."""
    return _parsed_tail(str(path), os.stat(path), limit)  # a new list (a slice): callers may reorder it


def bad_lines(path):
    """Corrupt lines read_events skipped in the tail it holds of `path` (0 before the first read)."""
    return (_TAILS.get(str(path)) or {}).get('bad', 0)


def recent_events(path, limit=3000):
    try:
        return read_events(path, limit)
    except (OSError, ValueError):
        return []


def account_state(root, now, max_age=timedelta(minutes=5)):
    """The runner's last broker/virtual balance (holdings with current price) when fresh, else None."""
    try:
        state = json.loads((Path(root) / 'account-state.json').read_text(encoding='utf-8'))
        return state if now - datetime.fromisoformat(state['time']) <= max_age else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def live_journal(record, engine_rows, today, today_events=(), *, simulate_today=True):
    """Estimate a live account's trades from the engine ledger and base stop, in journal() form.
    The estimate does not reproduce the regime-dependent 1% stop. The decision path/basis comes
    from the engine trade with the same time and symbol; a live-only trade (the live stop) keeps the mirror's reason.
    `today_events` (recorded 1-minute closes) lets today's live stop run; earlier days have no bars here."""
    from nh_live_shadow import account_shadow
    by_fill = {(r['time'], r['symbol'], r['side']): r for r in engine_rows}
    days = {r['time'][:10] for r in engine_rows}
    if simulate_today:
        days.add(today)
    else:
        days.discard(today)
    books, rows = {}, []
    for day in sorted(days):
        result = account_shadow(record, list(today_events) if day == today else [], day)
        for t in result['trades']:
            qty_held, avg, cost, pnl, ret = apply_fill(*books.get(t['symbol'], (0, 0.0)), t['side'], t['qty'], t['price'])
            books[t['symbol']] = (qty_held, avg)
            source = by_fill.get((t['time'], t['symbol'], t['side'])) or {}
            rows.append({'time': t['time'], 'symbol': t['symbol'], 'side': t['side'], 'qty': t['qty'], 'price': t['price'],
                         'amount': t['price'] * t['qty'], 'avg': avg if t['side'] == 'BUY' else (cost / t['qty'] if cost else None),
                         'pnl': pnl, 'cost': cost, 'return_pct': ret, 'after': qty_held,
                         'path': source.get('path') or t.get('reason', ''), 'basis': source.get('basis', '')})
    return rows


def real_journal(fills, engine_rows, window=timedelta(minutes=30)):
    """Recorded broker fills of a live account (live_fills.rows) in journal() form. The decision path/basis is the
    engine's latest trade of the same symbol and side up to `window` before the fill; anything else (a manual order)
    is marked as such. A sale of shares bought before recording began has no known cost and is not counted."""
    def at(stamp):
        try:
            return datetime.fromisoformat(stamp)
        except (TypeError, ValueError):
            return None
    engine = [(at(r['time']), r) for r in engine_rows]
    books, rows = {}, []
    for f in fills:
        when = at(f['time'])
        qty_held, avg, cost, pnl, ret = apply_fill(*books.get(f['symbol'], (0, 0.0)), f['side'], f['qty'], f['price'])
        books[f['symbol']] = (qty_held, avg)
        source = max((r for t, r in engine if when and t and r['symbol'] == f['symbol'] and r['side'] == f['side']
                      and timedelta(0) <= when - t <= window), key=lambda r: r['time'], default=None)
        rows.append({'time': f['time'], 'symbol': f['symbol'], 'side': f['side'], 'qty': f['qty'], 'price': f['price'],
                     'amount': f['price'] * f['qty'], 'avg': avg if f['side'] == 'BUY' else (cost / f['qty'] if cost else None),
                     'pnl': pnl, 'cost': cost, 'return_pct': ret, 'after': qty_held, 'source': '실체결',
                     'path': source['path'] if source else '엔진 거래와 연결 안 됨(수동·기타 주문)',
                     'basis': source['basis'] if source else ''})
    return rows


@lru_cache(maxsize=8)
def _cached_live(record_key, stamps, today, minute, simulate_today=True):
    record, engine_rows = json.loads(record_key)
    return live_journal(record, engine_rows, today, _today_bars(today) if simulate_today else (), simulate_today=simulate_today)


def _today_bars(today):
    """Today's recorded 1-minute closes from the shared runner journal (the live stop check needs them)."""
    from src.config.paths import get_runtime_root
    return [e for e in recent_events(get_runtime_root() / 'nhplug' / 'shared-etf-paper' / 'events.jsonl', 6000)
            if str(e.get('time', '')).startswith(today) and e.get('market_bars')]


def cached_live(record, engine_rows, root, now, *, simulate_today=True):
    """live_journal() recomputed when the engine ledger changes or once a minute (today's live stop)."""
    orders = Path(root) / 'orders.sqlite3'
    key = json.dumps([record, engine_rows], sort_keys=True, default=str)
    return _cached_live(key, orders.stat().st_mtime if orders.exists() else 0, now.strftime('%Y-%m-%d'),
                        now.strftime('%H%M') if simulate_today else '', simulate_today)


def account_rows(runtime, key, record, engine_rows, root, now):
    """A live account's trades: broker fills on the days they exist (live_fills), the mirror calculation elsewhere."""
    from live_fills import rows as live_fill_rows
    plain = [{k: r[k] for k in ('time', 'symbol', 'side', 'path', 'basis')} for r in engine_rows]
    real = real_journal(live_fill_rows(runtime, key), engine_rows)
    real_days = {r['time'][:10] for r in real}
    today = now.strftime('%Y-%m-%d')
    # When today's simulated trades would all be discarded, their only remaining use is as basis for later simulations.
    simulate_today = today not in real_days or any(r['time'][:10] > today for r in plain)
    try:
        mirrored = cached_live({k: v for k, v in record.items() if k != 'mode'}, plain, root, now,
                               simulate_today=simulate_today)
    except Exception:
        mirrored = []
    merged = [{**r, 'source': '가상(기본 손절 추정)'} for r in mirrored if r['time'][:10] not in real_days] + real
    return sorted(merged, key=lambda r: r['time'])


# ── screen ────────────────────────────────────────────────────────────────────
def signed(value, unit=''):
    """+1,234원 style (the dashboard's one signed-number format); '—' for a missing value."""
    return '—' if type(value) not in (int, float) else f'{value:+,.0f}{unit}'


def _won(value):
    return signed(value, '원')


def _pct(value):
    return '—' if value is None else f'{value:+.2f}%'


def latest_follow(runtime, limit=600):
    """{live account key: its latest live-follow.jsonl record} (one read for every account; the file is re-parsed only
    when it changes)."""
    latest = {}
    for event in recent_events(Path(runtime) / 'live-follow.jsonl', limit):
        if isinstance(event, dict) and event.get('account'):
            latest[event['account']] = event
    return latest


def _follow_mode(runtime, key):
    """'실주문' or '관찰(dry-run)' from the live account's latest mirror record."""
    return '실주문' if (latest_follow(runtime).get(key) or {}).get('mode') == 'execute' else '관찰(dry-run)'
