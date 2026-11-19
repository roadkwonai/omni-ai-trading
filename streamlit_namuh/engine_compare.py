"""Engine comparison: period_view (Jev and the long-term engine) and the archived intraday Jev vs vibe-trading comparison: each engine's trades with the decision behind them, results by period, open positions
and the latest judgments side by side. Read-only over each engine's ledger (engine_roles.ledger_root)."""
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
UNITS = {'일': 10, '월': 7, '년': 4, '전체': 0}  # ISO-time prefix length of the chosen day / month / year
PATHS = {'watchlist_strength': '관심종목 강도', 'jev': 'Jev 후보', 'split': '분할 추가', 'ipo': '신규주', 'oversold': '낙폭과대 반등', 'oversold_v2': '낙폭과대 선별 v2', 'oversold_v2_add': '선별 v2 2차 매수', 'oversold_v2_reentry': '선별 v2 저점 재매수',
         'vibe-trading': 'vibe 점수', 'manual': '수동 정리', 'jev_fast': 'Jev 빠른 매수(vibe 보조)'}
EXITS = {'take_profit': '익절', 'trailing_stop': '추적 손절', 'stop_loss': '손절', 'session_flatten': '장 마감 정리',
         'jev_sell': 'Jev 매도 의견', 'regime': '국면 청산', 'risk': '위험 관리', 'manual_exit': '수동 정리',
         'split_take_profit': '분할 익절', 'vibe-trading': 'vibe 매도 점수', 'model': 'Jev 매도 판단',
         'regime_exit': '국면 청산', 'protective': '보호 매도', 'direction_switch': '방향 전환 매도',
         'take_profit_all': '전량 익절', 'take_profit_half': '절반 익절', 'weak_market_targets': '약세장 익절',
         'ipo_time_stop': '신규주 시간 손절'}


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


def entry_split(rows, path):
    """(realized of episodes entered by `path`, their count, realized of the rest): vibe's book also holds Jev's 1-minute
    picks (jev_fast, user 2026-11-17), so an engine comparison shows vibe with and without them."""
    eps = [e for e in episodes(rows) if e['cost']]
    mine = [e for e in eps if e['entry'] == path]
    return round(sum(e['pnl'] for e in mine)), len(mine), round(sum(e['pnl'] for e in eps if e['entry'] != path))


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


def jev_call(row, entry=0.5, sell=0.7):
    """Jev's own call on a symbol in words: its holding SELL at the sell confidence, else the candidate probability."""
    holding = row.get('jev_holding') or ''
    if holding.startswith('SELL') and (row.get('jev_holding_conf') or 0) >= sell:
        return '보유 매도'
    prob = row.get('jev_prob')
    return '—' if prob is None else '매수 후보' if prob >= entry else '관망'


def vibe_call(row, buy=0.5, strength=0.2, sell=-0.3):
    """vibe-trading's call in words: buy (score route), strength buy (watchlist strength route), sell, or wait."""
    score = row.get('vibe_score')
    if score is None:
        return '—'
    return '매수' if score >= buy else '강도 매수' if row.get('strength') and score >= strength else '매도' if score <= sell else '관망'


DIRECTION = {'매수 후보': '매수', '매수': '매수', '강도 매수': '매수', '매도': '매도', '보유 매도': '매도', '관망': '관망'}


def stance(row, entry=0.5, sell=0.7):
    """Whether the two engines lean the same way on a symbol, in words (never colour alone)."""
    j, v = jev_call(row, entry, sell), vibe_call(row)
    if '—' in (j, v):
        return '한쪽만 판단'
    return f'같음 · {DIRECTION[j]}' if DIRECTION[j] == DIRECTION[v] else f'다름 · Jev {j} / vibe {v}'


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
        state = json.loads((Path(root) / 'account-state.json').read_text())
        return state if now - datetime.fromisoformat(state['time']) <= max_age else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def live_journal(record, engine_rows, today, today_events=()):
    """Estimate a live account's trades from the engine ledger and base stop, in journal() form.
    The estimate does not reproduce the regime-dependent 1% stop. The decision path/basis comes
    from the engine trade with the same time and symbol; a live-only trade (the live stop) keeps the mirror's reason.
    `today_events` (recorded 1-minute closes) lets today's live stop run; earlier days have no bars here."""
    from nh_live_shadow import account_shadow
    by_fill = {(r['time'], r['symbol'], r['side']): r for r in engine_rows}
    days = sorted({r['time'][:10] for r in engine_rows} | {today})
    books, rows = {}, []
    for day in days:
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
def _cached_live(record_key, stamps, today, minute):
    record, engine_rows = json.loads(record_key)
    return live_journal(record, engine_rows, today, _today_bars(today))


def _today_bars(today):
    """Today's recorded 1-minute closes from the shared runner journal (the live stop check needs them)."""
    from src.config.paths import get_runtime_root
    return [e for e in recent_events(get_runtime_root() / 'nhplug' / 'shared-etf-paper' / 'events.jsonl', 6000)
            if str(e.get('time', '')).startswith(today) and e.get('market_bars')]


def cached_live(record, engine_rows, root, now):
    """live_journal() recomputed when the engine ledger changes or once a minute (today's live stop)."""
    orders = Path(root) / 'orders.sqlite3'
    key = json.dumps([record, engine_rows], sort_keys=True, default=str)
    return _cached_live(key, orders.stat().st_mtime if orders.exists() else 0, now.strftime('%Y-%m-%d'), now.strftime('%H%M'))


def account_rows(runtime, key, record, engine_rows, root, now):
    """A live account's trades: broker fills on the days they exist (live_fills), the mirror calculation elsewhere."""
    from live_fills import rows as live_fill_rows
    plain = [{k: r[k] for k in ('time', 'symbol', 'side', 'path', 'basis')} for r in engine_rows]
    try:
        mirrored = cached_live({k: v for k, v in record.items() if k != 'mode'}, plain, root, now)
    except Exception:
        mirrored = []
    real = real_journal(live_fill_rows(runtime, key), engine_rows)
    real_days = {r['time'][:10] for r in real}  # a day with broker fills shows those, never the computed mirror
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


def _scorecard(engine, label, role, state, result, held, live_state, names, runner_label=True):
    import status_cards as sc
    import streamlit as st
    tone, icon, pulse = sc.service(state)
    with st.container(border=True):
        st.markdown(f"<span style='color:{COLORS[label]};font-weight:700'>■ {label}</span> · {role} · "
                    + (f'실행기 {state}' if runner_label else state), unsafe_allow_html=True)
        win = f"{result['wins'] / result['sells']:.0%}" if result['sells'] else '—'
        holding = ', '.join(f"{names.get(c, c)} {p['qty']:,}주" for c, p in held.items())
        unrealized = sum(h.get('eal_pls_amt') or 0 for h in ((live_state or {}).get('holdings') or {}).values())
        sc.row([{'label': '실현손익', 'value': _won(result['pnl']), 'tone': sc.signed(result['pnl']),
                 'icon': 'payments', 'sub': f"판 금액 대비 {_pct(result['return_pct'])}",
                 'help': '이 기간에 판 거래의 손익입니다. 이동평균 매입가 기준이며 수수료·세금은 빠져 있습니다.'},
                {'label': '승률', 'value': win, 'tone': 'ai' if result['sells'] else 'off', 'icon': 'target',
                 'sub': f"이익 {result['wins']} · 손실 {result['losses']} / 매도 {result['sells']}건"}], 2)
        sc.row([{'label': '매도 평균 수익률', 'value': _pct(result['avg_return_pct']),
                 'tone': sc.signed(result['avg_return_pct'] or 0), 'icon': 'percent',
                 'sub': f"최고 {_pct(result['best'])} · 최저 {_pct(result['worst'])}"},
                {'label': '지금 보유', 'value': f'{len(held)}종목', 'tone': 'up' if held else 'off', 'icon': 'inventory_2',
                 'sub': (holding + (f' · 평가 {unrealized:+,}원' if live_state and held else '')) or '없음',
                 'pulse': bool(held)}], 2)


def _overview_card(label, role, live_label, paper, live, paper_held, live_held):
    """One engine on the overview: its paper ledger and its live mirror side by side."""
    import status_cards as sc
    import streamlit as st
    win = lambda r: f"{r['wins']}/{r['sells']}" if r['sells'] else '—'
    with st.container(border=True):
        st.markdown(f"<span style='color:{COLORS[label]};font-weight:700'>■ {label}</span> · 모의 {role} · 실계좌 {live_label}",
                    unsafe_allow_html=True)
        for name, result, held in (('모의', paper, paper_held), ('실계좌', live, live_held)):
            sc.row([{'label': f'{name} 실현손익', 'value': _won(result['pnl']), 'tone': sc.signed(result['pnl']),
                     'icon': 'payments' if name == '모의' else 'account_balance',
                     'sub': f"판 금액 대비 {_pct(result['return_pct'])} · 승률 {win(result)}"},
                    {'label': f'{name} 거래·보유', 'value': f"{result['trades']}건", 'tone': 'ai' if result['trades'] else 'off',
                     'icon': 'swap_vert', 'sub': f"매수 {result['buys']} · 매도 {result['sells']} · 보유 {len(held)}종목"}], 2)


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


def _trades_frame(rows, names):
    import pandas as pd
    return pd.DataFrame([{
        '시각': r['time'][5:16].replace('T', ' '), '엔진': r['engine'],
        '범위': ' · '.join(x for x in (r.get('account') or r.get('scope', ''), r.get('source', '')) if x),
        '종목': names.get(r['symbol'], r['symbol']),
        '구분': '매수 ▲' if r['side'] == 'BUY' else '매도 ▼', '수량': r['qty'], '가격': r['price'], '금액': r['amount'],
        '수익률': _pct(r['return_pct']) if r['return_pct'] is not None else '',
        '실현손익': _won(r['pnl']) if r['pnl'] is not None else '', '판단 경로': r['path'], '판단 근거': r['basis']}
        for r in reversed(rows)])


def render(runtime, states, names, settings):
    """The whole Jev vs vibe-trading tab. states: launchd labels -> state; names: code -> display name."""
    import altair as alt
    import pandas as pd
    import streamlit as st
    from engine_roles import ledger_root, main_engine, scoreboard
    from research_store import history
    runtime, now = Path(runtime), datetime.now().astimezone()
    main = main_engine(runtime)
    roots = {e: ledger_root(runtime, e) for e in ENGINES}
    role = {e: ('메인 · 공유 모의계좌' if e == main else '보조 · 가상 체결') for e in ENGINES}
    runner = {'jev': states.get('공유 모의 ETF 실행기', '확인 불가'),
              'vibe': states.get('vibe-trading 실행기', '확인 불가')}
    advice_history = history('vibe_advice', 300)
    rows = {e: [{**r, 'engine': ENGINES[e], 'scope': '모의'} for r in cached_journal(roots[e], e, advice_history)] for e in ENGINES}
    live_rows, accounts = {e: [] for e in ENGINES}, {e: [] for e in ENGINES}
    try:
        from accounts import load as load_accounts
        records = {k: r for k, r in load_accounts().items() if r.get('kind') == 'live' and not r.get('invalid')}
    except Exception:
        records = {}
    for key, record in records.items():
        engine = record.get('engine')
        if engine not in ENGINES:
            continue
        label = record.get('label', key)
        accounts[engine].append(label)
        live_rows[engine] += [{**r, 'engine': ENGINES[engine], 'scope': '실계좌', 'account': label}
                              for r in account_rows(runtime, key, record, rows[engine], roots[engine], now)]

    head, scope_col, unit_col, key_col = st.columns([3, 2, 2, 2], vertical_alignment='bottom')
    head.subheader('Jev vs vibe 장중 (보관 · ~11-17)')
    scope = scope_col.segmented_control('범위', ['종합', '모의', '실계좌'], default='종합', key='compare_scope') or '종합'
    unit = unit_col.segmented_control('기간', list(UNITS), default='일', key='compare_unit') or '일'
    size = UNITS[unit]
    if size:
        stamps = {r['time'][:size] for e in ENGINES for r in rows[e] + live_rows[e]} | {now.isoformat()[:size]}
        prefix = key_col.selectbox(f'{unit} 선택', sorted(stamps, reverse=True), key=f'compare_key_{unit}')
    else:
        prefix = None
        key_col.caption('모든 기록')
    today_key = now.isoformat()[:10]
    period = '오늘' if prefix == today_key else (prefix or '전체')
    scopes = ['모의', '실계좌'] if scope == '종합' else [scope]
    source = {('모의', e): rows[e] for e in ENGINES} | {('실계좌', e): live_rows[e] for e in ENGINES}
    view = {e: sorted((r for sc_ in scopes for r in source[(sc_, e)]), key=lambda r: r['time']) for e in ENGINES}
    modes = {}
    for engine in ENGINES:
        for key, record in records.items():
            if record.get('engine') == engine:
                state = _follow_mode(runtime, key)
                modes.setdefault(engine, set()).add(state)
    mode_text = lambda e: '·'.join(sorted(modes.get(e, {'연결 계좌 없음'})))
    st.caption('모의 = 엔진 원장(메인은 공유 모의계좌, 보조는 가상 체결) · 실계좌 = 그 엔진을 따라가는 실계좌 미러 '
               f"(Jev → {', '.join(accounts['jev']) or '없음'} {mode_text('jev')}, vibe → {', '.join(accounts['vibe']) or '없음'} "
               f"{mode_text('vibe')}) · 조회 {now:%H:%M:%S} · 수수료·세금 미반영")
    if any('관찰' in m for m in modes.get('jev', set()) | modes.get('vibe', set())) and scope != '모의':
        st.caption('실계좌 결과: 증권사 체결이 기록된 날은 실체결(미러 실행기가 매분 기록), 그 밖의 날은 엔진 원장과 계좌 기본 손절 등을 바탕으로 한 가상 추정입니다. '
                   '시황별 1% 손절은 반영하지 않습니다. '
                   '거래 표의 "범위" 열에 실체결/가상이 표시됩니다.')

    held = {(sc_, e): positions(source[(sc_, e)]) for sc_ in ('모의', '실계좌') for e in ENGINES}
    live = {e: account_state(roots[e], now) for e in ENGINES}
    results = {(sc_, e): summary(in_period(source[(sc_, e)], prefix)) for sc_ in ('모의', '실계좌') for e in ENGINES}
    left, right = st.columns(2)
    for column, engine in ((left, 'jev'), (right, 'vibe')):
        with column:
            if scope == '종합':
                _overview_card(ENGINES[engine], role[engine], ', '.join(accounts[engine]) or '연결 실계좌 없음',
                               results[('모의', engine)], results[('실계좌', engine)], held[('모의', engine)], held[('실계좌', engine)])
            elif scope == '모의':
                _scorecard(engine, ENGINES[engine], role[engine], runner[engine], results[('모의', engine)],
                           held[('모의', engine)], live[engine], names)
            else:
                mirror = ', '.join(f"{label} 미러 실행기 {states.get(f'{label} 미러 실행기', '확인 불가')}" for label in accounts[engine])
                _scorecard(engine, ENGINES[engine], f"{', '.join(accounts[engine]) or '연결 실계좌 없음'} · {mode_text(engine)}",
                           mirror or '없음', results[('실계좌', engine)], held[('실계좌', engine)], None, names, runner_label=False)
    for sc_ in scopes:
        a, b = results[(sc_, 'jev')], results[(sc_, 'vibe')]
        if a['sells'] or b['sells']:
            gap = a['pnl'] - b['pnl']
            lead = 'Jev' if gap > 0 else 'vibe-trading' if gap < 0 else None
            st.markdown(f"**{period} · {sc_}:** " + (f"{lead}가 실현손익 {abs(gap):,.0f}원 앞섭니다" if lead else '실현손익이 같습니다')
                        + f" · Jev {_won(a['pnl'])} ({_pct(a['return_pct'])}) / vibe {_won(b['pnl'])} ({_pct(b['return_pct'])})"
                        + " · 승률 Jev " + (f"{a['wins']}/{a['sells']}" if a['sells'] else '—') + ' / vibe '
                        + (f"{b['wins']}/{b['sells']}" if b['sells'] else '—'))
            fast, count, rest = entry_split(in_period(source[(sc_, 'vibe')], prefix), PATHS['jev_fast'])
            if count:
                st.caption(f"vibe 실현손익 중 Jev 빠른 매수(jev_fast)로 들어간 {count}건이 {_won(fast)}입니다. "
                           f"이 몫을 빼면 vibe 자체 판단은 {_won(rest)}입니다(엔진 비교는 이 값으로 보세요).")
        else:
            st.caption(f'{period} · {sc_}: 두 엔진 모두 매도 기록이 없어 손익을 비교할 수 없습니다.')

    sells = [r for e in ENGINES for r in in_period(view[e], prefix) if r['pnl'] is not None]
    if sells:
        frame = pd.DataFrame(sells).sort_values('time')
        frame['시각'] = pd.to_datetime(frame['time'], format='ISO8601')
        frame['계열'] = frame['engine'] + ' · ' + frame['scope']
        frame['누적 실현손익'] = frame.groupby('계열')['pnl'].cumsum()
        frame['종목'] = frame['symbol'].map(lambda c: names.get(c, c))
        color = alt.Color('engine:N', title='엔진', scale=alt.Scale(domain=list(COLORS), range=list(COLORS.values())),
                          legend=alt.Legend(orient='top'))
        dash = alt.StrokeDash('scope:N', title='범위', scale=alt.Scale(domain=['모의', '실계좌'], range=[[1, 0], [6, 4]]),
                              legend=alt.Legend(orient='top'))
        tip = [alt.Tooltip('시각:T', format='%m/%d %H:%M'), alt.Tooltip('계열:N'), '종목', 'path',
               alt.Tooltip('return_pct:Q', title='수익률(%)', format='+.2f'), alt.Tooltip('pnl:Q', title='손익(원)', format='+,.0f')]
        fmt = '%H:%M' if prefix and len(prefix) == 10 else '%m/%d'
        def cumulative(part, title):
            base = alt.Chart(part).encode(x=alt.X('시각:T', title=None, axis=alt.Axis(format=fmt)), color=color, detail='계열:N')
            st.markdown(f'###### {title}')
            st.altair_chart((alt.Chart(pd.DataFrame({'y': [0]})).mark_rule(color='#475569', strokeDash=[4, 4]).encode(y='y:Q')
                             + base.mark_line(interpolate='step-after', strokeWidth=2.5).encode(
                                 y=alt.Y('누적 실현손익:Q', title='원', axis=alt.Axis(format=',.0f')), strokeDash=dash)
                             + base.mark_point(filled=True, size=70).encode(
                                 y='누적 실현손익:Q', shape=alt.Shape('scope:N', legend=None), tooltip=tip)
                             ).properties(height=220), width='stretch')

        returns = (alt.Chart(pd.DataFrame({'y': [0]})).mark_rule(color='#475569').encode(y='y:Q')
                   + alt.Chart(frame).mark_point(size=110, filled=True, opacity=0.9).encode(
                       x=alt.X('계열:N', title=None, axis=alt.Axis(labelAngle=0)),
                       y=alt.Y('return_pct:Q', title='%', axis=alt.Axis(format='+.1f')),
                       color=color, shape=alt.Shape('scope:N', title='범위', legend=None), tooltip=tip)).properties(height=220)
        if scope == '종합':  # paper and live differ in size by ~100x: each gets its own scale
            columns = st.columns(len(scopes))
            for column, sc_ in zip(columns, scopes):
                with column:
                    part = frame[frame['scope'] == sc_]
                    if len(part):
                        cumulative(part, f'누적 실현손익 · {sc_}')
                    else:
                        st.markdown(f'###### 누적 실현손익 · {sc_}')
                        st.caption('이 기간 매도 기록이 없습니다.')
            st.markdown('###### 매도별 수익률')
            st.altair_chart(returns, width='stretch')
        else:
            c1, c2 = st.columns([3, 2])
            with c1:
                cumulative(frame, '누적 실현손익')
            with c2:
                st.markdown('###### 매도별 수익률')
                st.altair_chart(returns, width='stretch')
        if not (prefix and len(prefix) == 10):
            daily = frame.assign(일=frame['time'].str[:10]).groupby(['일', '계열', 'engine', 'scope'], as_index=False)['pnl'].sum()
            st.markdown('###### 일별 실현손익')
            st.altair_chart(alt.Chart(daily).mark_bar().encode(
                x=alt.X('일:N', title=None, axis=alt.Axis(labelAngle=0)), xOffset='계열:N',
                y=alt.Y('pnl:Q', title='원', axis=alt.Axis(format=',.0f')), color=color,
                opacity=alt.Opacity('scope:N', title='범위', scale=alt.Scale(domain=['모의', '실계좌'], range=[1.0, 0.55])),
                tooltip=['일', '계열', alt.Tooltip('pnl:Q', title='손익(원)', format='+,.0f')]
            ).properties(height=200), width='stretch')

    trades_tab, holding_tab, opinion_tab, settle_tab = st.tabs(['거래 결과·판단 근거', '보유·진행', '판단 비교', '판단 정산·이력'])
    with trades_tab:
        f1, f2 = st.columns(2)
        who = f1.segmented_control('엔진', ['두 엔진', 'Jev', 'vibe-trading'], default='두 엔진', key='compare_trades_engine')
        side = f2.segmented_control('구분', ['전체', '매수', '매도'], default='전체', key='compare_trades_side')
        picked = [r for e in ENGINES for r in in_period(view[e], prefix)
                  if who in (None, '두 엔진', ENGINES[e]) and (side in (None, '전체') or r['side'] == ('BUY' if side == '매수' else 'SELL'))]
        picked.sort(key=lambda r: r['time'])
        if picked:
            tone = lambda v: ('color:#ef4444' if str(v).startswith(('+', '매수')) and v not in ('+0원', '+0.00%')
                              else 'color:#3b82f6' if str(v).startswith(('-', '매도')) else '')
            frame = _trades_frame(picked, names)
            st.dataframe(frame.style.map(tone, subset=['구분', '수익률', '실현손익']), hide_index=True, width='stretch',
                         column_config={'가격': st.column_config.NumberColumn(format='%,.0f'),
                                        '금액': st.column_config.NumberColumn(format='%,.0f'),
                                        '판단 근거': st.column_config.TextColumn(width='large')})
            st.caption('판단 경로·근거는 그 주문을 낸 실행기 회차의 기록(Jev 확률·신뢰도, vibe 점수와 근거, 익절·손절 사유)입니다. '
                       '실계좌 행은 같은 엔진 거래의 근거를 이어받고, 실계좌만의 매도(손절)는 그 사유를 적습니다.')
        else:
            st.caption(f'{period}에 조건에 맞는 거래가 없습니다.')
    with holding_tab:
        cols = st.columns(2)
        plans = {}
        try:
            plans = json.loads((roots['jev'] / 'exit-plans.json').read_text())
        except (OSError, ValueError):
            pass
        for column, engine in zip(cols, ENGINES):
            with column:
                st.markdown(f"<span style='color:{COLORS[ENGINES[engine]]};font-weight:700'>■ {ENGINES[engine]}</span>",
                            unsafe_allow_html=True)
                state = (live[engine] or {}).get('holdings') or {}
                table = []
                for sc_ in scopes:
                    for code, p in held[(sc_, engine)].items():
                        now_pr = (state.get(code) or {}).get('now_pr')
                        plan = plans.get(code) if engine == 'jev' else None
                        rate = (now_pr / p['avg'] - 1) * 100 if now_pr and p.get('avg') else None
                        table.append({'범위': sc_, '종목': names.get(code, code), '수량': p['qty'], '평균가': round(p['avg'] or 0),
                                      '현재가': now_pr, '평가수익률': _pct(rate) if rate is not None else '—',
                                      '진입 경로': p['path'], '보유 시작': p['opened'][5:16].replace('T', ' '),
                                      '청산 계획': (f"{'추세형' if plan.get('profile') == 'trend' else '방어형'} · 손절 −{plan.get('stop_pct')}%"
                                                  f" · {plan.get('flatten_at') or '이월 가능'}") if plan else ''})
                if table:
                    st.dataframe(pd.DataFrame(table), hide_index=True, width='stretch',
                                 column_config={'평균가': st.column_config.NumberColumn(format='%,.0f'),
                                                '현재가': st.column_config.NumberColumn(format='%,.0f')})
                else:
                    st.caption('보유 없음')
                for sc_ in scopes:
                    today = summary(in_period(source[(sc_, engine)], now.isoformat()[:10]))
                    st.caption(f"{sc_} 오늘 매수 {today['buys']} · 매도 {today['sells']} · 실현 {_won(today['pnl'])}")
        spans = []
        for e in ENGINES:
            for sc_ in scopes:
                for ep in episodes(source[(sc_, e)]):
                    if prefix and not ((ep['close'] or now.isoformat()) >= prefix and ep['open'][:len(prefix)] <= prefix):
                        continue
                    state = '보유 중' if ep['close'] is None else '수익' if ep['pnl'] > 0 else '손실' if ep['pnl'] < 0 else '본전'
                    spans.append({'행': f"{ENGINES[e]} · {sc_} · {names.get(ep['symbol'], ep['symbol'])}", '엔진': ENGINES[e],
                                  '시작': ep['open'], '끝': ep['close'] or now.isoformat(), '결과': state,
                                  '수익률': _pct(ep['return_pct']), '손익': _won(ep['pnl']) if ep['cost'] else '—',
                                  '진입': ep['entry'], '청산': ep['exit'] or ('진행 중' if ep['close'] is None else ''), '최대 수량': ep['qty']})
        st.markdown('###### 보유 기간 타임라인 (진입 → 청산)')
        if spans:
            frame = pd.DataFrame(spans)
            frame['시작'], frame['끝'] = pd.to_datetime(frame['시작'], utc=True, format='ISO8601'), pd.to_datetime(frame['끝'], utc=True, format='ISO8601')
            st.altair_chart(alt.Chart(frame).mark_bar(cornerRadius=3, height=14).encode(
                x=alt.X('시작:T', title=None, axis=alt.Axis(format='%m/%d %H:%M' if not (prefix and len(prefix) == 10) else '%H:%M')),
                x2='끝:T', y=alt.Y('행:N', title=None, sort=None, axis=alt.Axis(labelLimit=320, labelOverlap=False)),
                color=alt.Color('결과:N', scale=alt.Scale(domain=['수익', '손실', '본전', '보유 중'],
                                                         range=['#ef4444', '#3b82f6', '#64748b', '#a855f7']),
                                legend=alt.Legend(orient='top', title=None)),
                tooltip=['행', '진입', '청산', '수익률', '손익', '최대 수량',
                         alt.Tooltip('시작:T', format='%m/%d %H:%M'), alt.Tooltip('끝:T', format='%m/%d %H:%M')]
            ).properties(height=50 + 30 * frame['행'].nunique()), width='stretch')
            st.caption('막대 하나가 첫 매수부터 다 팔 때까지의 보유입니다. 빨강 수익 · 파랑 손실 · 보라 보유 중. 마우스를 올리면 진입 경로와 청산 사유가 나옵니다.')
        else:
            st.caption(f'{period}에 보유했던 종목이 없습니다.')
    with opinion_tab:
        jev_events = recent_events(roots['jev'] / 'events.jsonl')
        try:
            advice = json.loads((runtime / 'vibe-paper' / 'vibe-advice.json').read_text())
        except (OSError, ValueError):
            advice = None
        candidate, table = opinions(jev_events, advice)
        entry = settings.get('entry_confidence', 0.5)
        c1, c2 = st.columns(2)
        with c1.container(border=True):
            st.markdown(f"<span style='color:{COLORS['Jev']};font-weight:700'>■ Jev</span> 최근 판단", unsafe_allow_html=True)
            if candidate:
                from etf_universe import SYMBOLS
                choice = candidate.get('choice')
                label = '대기(WAIT)' if choice == 'WAIT' else names.get(SYMBOLS.get(choice, choice), choice)
                st.markdown(f"후보 **{label}** · 신뢰도 "
                            f"**{candidate.get('confidence', 0):.2f}** (진입 기준 {entry:.2f}) · {str(candidate.get('time', ''))[11:16]}")
                st.caption('입력: 관심 ETF·종목 1분봉 차트 이미지와 최근 뉴스 헤드라인. 확률은 수익 확률이 아닙니다.')
            else:
                st.caption('최근 Jev 후보 기록이 없습니다.')
        with c2.container(border=True):
            st.markdown(f"<span style='color:{COLORS['vibe-trading']};font-weight:700'>■ vibe-trading</span> 최근 판단",
                        unsafe_allow_html=True)
            if advice:
                st.markdown(f"{str(advice.get('time', ''))[11:16]} · {advice.get('market_view', '')}")
                st.caption('입력: NH 시세·지수·투자자 흐름, 관심종목 거래량·거래대금·뉴스, 분봉 스킬(technical-basic · minute-analysis · candlestick), Jev 의견(참고). 점수 −1~+1: 두 회차 평균 0.5 이상 매수, 또는 관심종목 강도(오늘 +2~25% · 거래량 증가) 종목은 0.2 이상이면 100만 원 매수.')
            else:
                st.caption('최근 vibe 분석 기록이 없습니다.')
        if table:
            sell_conf = settings.get('sell_confidence', 0.7)
            order = sorted(table, key=lambda c: (-(table[c].get('vibe_score') if table[c].get('vibe_score') is not None else -9),
                                                 -(table[c].get('jev_prob') or 0)))
            rows_ = [{'종목': names.get(c, c), 'Jev 판정': jev_call(table[c], entry, sell_conf), 'Jev 확률': table[c].get('jev_prob'),
                      'vibe 판정': vibe_call(table[c]), 'vibe 점수': table[c].get('vibe_score'), '두 엔진': stance(table[c], entry, sell_conf)}
                     for c in order]
            st.markdown('###### 두 엔진 판단 지도')
            chart = pd.DataFrame(rows_)
            y = alt.Y('종목:N', sort=list(chart['종목']), title=None, axis=alt.Axis(labelLimit=220))
            calls = {'매수 후보': '#ef4444', '매수': '#ef4444', '강도 매수': '#f87171', '매도': '#3b82f6', '보유 매도': '#3b82f6',
                     '관망': '#64748b', '—': '#334155'}
            left = (alt.Chart(chart).mark_bar(cornerRadiusEnd=3).encode(
                        y=y, x=alt.X('Jev 확률:Q', title='Jev 후보 확률 (0~1)', scale=alt.Scale(domain=[0, 1])),
                        color=alt.Color('Jev 판정:N', scale=alt.Scale(domain=list(calls), range=list(calls.values())), legend=None),
                        tooltip=['종목', 'Jev 판정', alt.Tooltip('Jev 확률:Q', format='.2f')])
                    + alt.Chart(pd.DataFrame({'x': [entry]})).mark_rule(color='#f59e0b', strokeDash=[4, 4]).encode(x='x:Q')
                    ).properties(width=300, height=30 * len(chart), title=alt.TitleParams('■ Jev', color=COLORS['Jev'], anchor='start'))
            lines = pd.DataFrame([{'x': 0.5, '기준': '매수 +0.50'}, {'x': 0.2, '기준': '강도 매수 +0.20'}, {'x': -0.3, '기준': '매도 −0.30'}])
            right = (alt.Chart(chart).mark_bar(cornerRadiusEnd=3).encode(
                         y=alt.Y('종목:N', sort=list(chart['종목']), title=None, axis=alt.Axis(labels=False, ticks=False)),
                         x=alt.X('vibe 점수:Q', title='vibe 점수 (−1~+1)', scale=alt.Scale(domain=[-1, 1])),
                         color=alt.Color('vibe 판정:N', scale=alt.Scale(domain=list(calls), range=list(calls.values())), legend=None),
                         tooltip=['종목', 'vibe 판정', alt.Tooltip('vibe 점수:Q', format='+.2f'), '두 엔진'])
                     + alt.Chart(lines).mark_rule(color='#f59e0b', strokeDash=[4, 4]).encode(x='x:Q', tooltip=['기준'])
                     + alt.Chart(pd.DataFrame({'x': [0]})).mark_rule(color='#475569').encode(x='x:Q')
                     ).properties(width=360, height=30 * len(chart),
                                  title=alt.TitleParams('■ vibe-trading', color=COLORS['vibe-trading'], anchor='start'))
            st.altair_chart(alt.hconcat(left, right, spacing=12), width='content')
            st.caption(f'왼쪽 Jev 후보 확률(주황 점선 = 진입 기준 {entry:.2f}), 오른쪽 vibe 점수(점선 = 매수 +0.50 · 강도 매수 +0.20 · 매도 −0.30). '
                       '막대 색: 빨강 매수 쪽 · 연빨강 강도 매수 · 파랑 매도 쪽 · 회색 관망. 같은 줄이 같은 종목입니다.')
            frame = pd.DataFrame([{**r, 'Jev 보유 의견': table[c].get('jev_holding') or '—',
                                   '기술 투표': {1: '+1 매수', -1: '−1 매도', 0: '0 관망'}.get(table[c].get('vote'), '—'),
                                   'VWAP 대비': _pct(table[c].get('vs_vwap_pct')) if table[c].get('vs_vwap_pct') is not None else '—',
                                   '거래량 배율': f"{table[c]['volume_ratio']:.2f}배" if table[c].get('volume_ratio') is not None else '—',
                                   '강도 후보': '예' if table[c].get('strength') else '', 'vibe 근거': table[c].get('vibe_evidence', '')}
                                  for c, r in zip(order, rows_)])
            frame = frame[['종목', '두 엔진', 'Jev 판정', 'Jev 확률', 'Jev 보유 의견', 'vibe 판정', 'vibe 점수', '기술 투표',
                           'VWAP 대비', '거래량 배율', '강도 후보', 'vibe 근거']]
            tint = lambda v: ('color:#ef4444' if v in ('매수 후보', '매수', '강도 매수') or str(v).endswith('· 매수')
                              else 'color:#3b82f6' if v in ('매도', '보유 매도') or str(v).endswith('· 매도')
                              else 'color:#f59e0b' if str(v).startswith('다름') else '')
            st.markdown('###### 종목별 판단 (Jev | vibe-trading)')
            st.dataframe(frame.style.map(tint, subset=['두 엔진', 'Jev 판정', 'vibe 판정']), hide_index=True, width='stretch',
                         column_config={'Jev 확률': st.column_config.ProgressColumn('Jev · 확률', format='%.2f', min_value=0.0, max_value=1.0),
                                        'Jev 판정': st.column_config.TextColumn('Jev · 판정'),
                                        'Jev 보유 의견': st.column_config.TextColumn('Jev · 보유 의견'),
                                        'vibe 판정': st.column_config.TextColumn('vibe · 판정'),
                                        'vibe 점수': st.column_config.NumberColumn('vibe · 점수', format='%+.2f'),
                                        '기술 투표': st.column_config.TextColumn('vibe · 기술 투표'),
                                        'VWAP 대비': st.column_config.TextColumn('vibe · VWAP 대비'),
                                        '거래량 배율': st.column_config.TextColumn('vibe · 거래량 배율'),
                                        '강도 후보': st.column_config.TextColumn('vibe · 강도 후보'),
                                        'vibe 근거': st.column_config.TextColumn('vibe · 근거', width='large')})
            st.caption(f'판정: Jev는 후보 확률 {entry:.2f} 이상이면 매수 후보, 보유 SELL 신뢰도 {sell_conf:.2f} 이상이면 보유 매도. '
                       'vibe는 점수 +0.50 이상 매수, 강도 후보이고 +0.20 이상이면 강도 매수, −0.30 이하 매도. '
                       '"두 엔진"은 둘의 방향(매수·관망·매도)이 같은지입니다. 입력과 시각이 달라 같은 조건의 정확도 비교가 아닙니다.')
    with settle_tab:
        try:
            board = scoreboard(runtime)
            settled = [{'엔진': ENGINES.get(e, e), '역할': role.get(e, ''), '판단 종류': kind.split(' ', 1)[-1], '정산 수': c['n'],
                        '30분 뒤 적중률': c['hit_rate_30m'], '30분 평균 수익률(%)': c['avg_return_30m_pct'],
                        'KODEX 200 대비(%)': c['avg_vs_kodex200_30m_pct']}
                       for e, row in board.items() for kind, c in (row.get('settled_calls') or {}).items()]
        except Exception:
            settled = []
        if settled:
            frame = pd.DataFrame(settled)
            c1, c2 = st.columns(2)
            with c1:
                st.markdown('###### 30분 뒤 적중률')
                st.altair_chart(alt.Chart(frame).mark_bar().encode(
                    y=alt.Y('판단 종류:N', title=None, axis=alt.Axis(labelLimit=200)), yOffset='엔진:N',
                    x=alt.X('30분 뒤 적중률:Q', title=None, scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format='%')),
                    color=alt.Color('엔진:N', scale=alt.Scale(domain=list(COLORS), range=list(COLORS.values())),
                                    legend=alt.Legend(orient='top', title=None)),
                    tooltip=['엔진', '판단 종류', '정산 수', alt.Tooltip('30분 뒤 적중률:Q', format='.0%')]).properties(height=200),
                    width='stretch')
            with c2:
                st.markdown('###### 30분 평균 수익률 (KODEX 200 대비)')
                st.altair_chart(alt.Chart(frame).mark_bar().encode(
                    y=alt.Y('판단 종류:N', title=None, axis=alt.Axis(labelLimit=200)), yOffset='엔진:N',
                    x=alt.X('KODEX 200 대비(%):Q', title='%p'),
                    color=alt.condition(alt.datum['KODEX 200 대비(%)'] >= 0, alt.value('#ef4444'), alt.value('#3b82f6')),
                    tooltip=['엔진', '판단 종류', '정산 수', '30분 평균 수익률(%)', 'KODEX 200 대비(%)']).properties(height=200),
                    width='stretch')
            st.dataframe(frame, hide_index=True, width='stretch')
            st.caption('판단 30분 뒤 실제 움직임으로 정산한 값입니다. 표본이 작고 가상 체결은 실제 체결이 아니므로 수익성 검증이 아닙니다.')
        else:
            st.caption('정산된 판단이 아직 없습니다.')
        strength = [{'시각': str(h.get('time', '')), '엔진': 'vibe 최고 점수',
                     '값': max((x.get('score', 0) for x in (h.get('scores') or {}).values()), default=None),
                     '매매 결정': len(h.get('decisions') or [])}
                    for h in advice_history if not prefix or str(h.get('time', '')).startswith(prefix)]
        for event in recent_events(roots['jev'] / 'events.jsonl', 12000):
            c = ((event.get('jev') or {}).get('candidate') or {}) if event.get('event') == 'jev_shadow' else None
            if c and (not prefix or str(event.get('time', '')).startswith(prefix)):
                strength.append({'시각': str(event.get('time', '')), '엔진': 'Jev 후보 신뢰도', '값': c.get('confidence'), '매매 결정': None})
        strength = [x for x in strength if x['값'] is not None]
        st.markdown(f'###### 판단 강도 이력 ({period})')
        if strength:
            frame = pd.DataFrame(strength)
            frame['시각'] = pd.to_datetime(frame['시각'], utc=True, format='ISO8601')
            entry = settings.get('entry_confidence', 0.5)
            st.altair_chart((alt.Chart(frame).mark_line(point=alt.OverlayMarkDef(size=18), strokeWidth=1.5).encode(
                x=alt.X('시각:T', title=None), y=alt.Y('값:Q', title='신뢰도 · 점수'),
                color=alt.Color('엔진:N', scale=alt.Scale(domain=['Jev 후보 신뢰도', 'vibe 최고 점수'],
                                                         range=[COLORS['Jev'], COLORS['vibe-trading']]), legend=alt.Legend(orient='top', title=None)),
                tooltip=[alt.Tooltip('시각:T', format='%m/%d %H:%M'), '엔진', alt.Tooltip('값:Q', format='.2f'), '매매 결정'])
                + alt.Chart(pd.DataFrame([{'y': entry, '기준': f'Jev 진입 기준 {entry:.2f}'}, {'y': 0.5, '기준': 'vibe 매수 기준 +0.50'}]))
                .mark_rule(strokeDash=[4, 4], color='#f59e0b').encode(y='y:Q', tooltip=['기준'])
            ).properties(height=240), width='stretch')
            above = lambda name, line: sum(1 for x in strength if x['엔진'] == name and x['값'] >= line)
            st.caption(f"기준 이상 회차: Jev {above('Jev 후보 신뢰도', entry)}/{sum(x['엔진'] == 'Jev 후보 신뢰도' for x in strength)} · "
                       f"vibe {above('vibe 최고 점수', 0.5)}/{sum(x['엔진'] == 'vibe 최고 점수' for x in strength)}. "
                       'vibe는 직전 회차와 평균한 점수가 0.5 이상이어야 사므로, 한 번 0.5에 닿아도 매수가 안 날 수 있습니다.')
        else:
            st.caption('이 기간의 판단 기록이 없습니다.')
        recent = [{'시각': str(h.get('time', ''))[5:16].replace('T', ' '), '시황': (h.get('market_view') or '')[:80],
                   '최고 점수': max((s.get('score', 0) for s in (h.get('scores') or {}).values()), default=None),
                   '매매': len(h.get('decisions') or [])} for h in advice_history[:40]]
        with st.expander(f'vibe-trading 분석 이력 ({len(recent)}회)'):
            st.dataframe(recent, hide_index=True, width='stretch') if recent else st.caption('이력이 없습니다.')


def period_daily(jev_rows, nav, contributed=None):
    """Per day since the long-term engine's first NAV: Jev realized P&L (and its running total) beside the long-term
    books' P&L against their 1억 원 start plus the 적립식 money paid in by then. nav: [(day, book, value)] of the books,
    contributed: {day: paid in by then} (vibe_longterm_view.nav_history)."""
    import vibe_longterm as LT
    by_day = {}
    for day, book, value in nav:
        by_day.setdefault(day, {})[book] = value
    if not by_day:
        return []
    start = min(by_day)
    realized = {}
    for r in jev_rows:
        if r['side'] == 'SELL' and r.get('pnl') is not None and r['time'][:10] >= start:
            realized[r['time'][:10]] = realized.get(r['time'][:10], 0.0) + r['pnl']
    out, running, books, paid = [], 0.0, {}, 0
    for day in sorted(set(by_day) | set(realized)):
        running += realized.get(day, 0.0)
        books = {**books, **by_day.get(day, {})}  # before today's 15:40 record the last known value stands
        paid = (contributed or {}).get(day, paid)
        out.append({'날짜': day, 'Jev 실현손익': round(realized.get(day, 0.0)), 'Jev 누적': round(running),
                    **{label: round(LT.profit(books[b], paid)) if b in books else None for b, label in BOOKS}, '적립 누계': paid})
    return out


BOOKS = (('actual', '장기투자 누적'), ('base', '기본 그림자 누적'), ('auto', '자동 그림자 누적'))


def regime_days(path):
    """[(day, official regime)] from the long-term engine's daily regime decisions (last one of each day)."""
    with ro_connect(path) as db:
        rows = db.execute("SELECT day, regime FROM decisions WHERE kind = 'regime' ORDER BY time, rowid").fetchall()
    return sorted(dict(rows).items())


def period_view(runtime):
    """Jev (intraday, shared paper ledger) and the long-term engine (virtual book) over the same days."""
    import altair as alt
    import pandas as pd
    import streamlit as st
    import status_cards as sc
    from engine_roles import ledger_root
    runtime = Path(runtime)
    from vibe_longterm_view import REGIME_COLOR, nav_history
    path = runtime / 'vibe-longterm' / 'longterm.sqlite3'
    nav, contributed = nav_history(path.parent)
    regimes = regime_days(path) if path.exists() else []
    rows = period_daily(cached_journal(ledger_root(runtime, 'jev'), 'jev'), nav, contributed)
    st.caption('기준이 다릅니다: Jev는 공유 모의계좌 원장의 실현손익(수수료·세금 전), 장기투자는 1억 원 가상 계좌의 순자산 평가(수수료 포함)입니다. '
               '장기투자 첫 순자산 기록일부터 비교합니다.')
    if not rows:
        st.info('장기투자 순자산 기록(평일 15:40)이 쌓이면 여기서 같은 날짜로 비교합니다.', icon=':material/schedule:')
        return
    last = rows[-1]
    sc.row([{'label': f"Jev 누적 실현손익 · {rows[0]['날짜']}~", 'value': f"{last['Jev 누적']:+,}원", 'icon': 'psychology',
             'tone': 'up' if last['Jev 누적'] >= 0 else 'down', 'sub': '공유 모의계좌 원장'},
            {'label': '장기투자 누적 손익', 'value': '—' if last['장기투자 누적'] is None else f"{last['장기투자 누적']:+,}원", 'icon': 'savings',
             'tone': 'up' if (last['장기투자 누적'] or 0) >= 0 else 'down',
             'sub': '1억 원' + (f" + 적립 누계 {last['적립 누계']:,}원" if last['적립 누계'] else '') + ' 대비 순자산 평가'},
            {'label': '기본 그림자 누적', 'value': '—' if last['기본 그림자 누적'] is None else f"{last['기본 그림자 누적']:+,}원", 'icon': 'shield',
             'tone': 'off', 'sub': '기준 비중+달리오만(비교 기준)'},
            {'label': '자동 그림자 누적', 'value': '—' if last['자동 그림자 누적'] is None else f"{last['자동 그림자 누적']:+,}원", 'icon': 'auto_mode',
             'tone': 'off', 'sub': '국면 추천 조합(비교 기준)'}])
    from vibe_longterm_view import drawdowns
    falls = drawdowns(nav, contributed)
    st.caption('기간 최대 낙폭(순자산 고점 대비): ' + ' · '.join(f"{label.replace(' 누적', '')} {falls[b]:.2%}" for b, label in BOOKS if b in falls)
               + ' · Jev는 실현손익 기준이라 낙폭을 따로 계산하지 않습니다.')
    frame = pd.DataFrame(rows)
    long = frame.melt(id_vars='날짜', value_vars=['Jev 누적', *(label for _, label in BOOKS)], var_name='구분', value_name='원').dropna()
    x = alt.X('날짜:N', title=None, sort=list(frame['날짜']))
    chart = alt.Chart(long).mark_line(point=True).encode(
        x=x, y=alt.Y('원:Q', title='누적 손익(원)'), color=alt.Color('구분:N', legend=alt.Legend(orient='bottom')),
        strokeDash='구분:N', tooltip=['날짜', '구분', alt.Tooltip('원:Q', format=',.0f')])
    st.altair_chart(chart.properties(height=260), width='stretch')
    shown = [(d, r) for d, r in regimes if d in set(frame['날짜'])]
    if shown:  # the long-term regime of each day under the lines: colour plus the name, never colour alone
        import vibe_longterm as LT
        band = pd.DataFrame([{'날짜': d, 'key': r, '국면': LT.REGIMES[r][0]} for d, r in shown if r in LT.REGIMES])
        rect = alt.Chart(band).mark_rect().encode(x=x, color=alt.Color('key:N', scale=alt.Scale(domain=list(REGIME_COLOR), range=list(REGIME_COLOR.values())),
                                                                     legend=None), tooltip=['날짜', '국면'])
        st.altair_chart((rect + alt.Chart(band).mark_text(color='white', fontSize=11).encode(x=x, text='국면:N')).properties(height=36),
                        width='stretch')
        st.caption('아래 띠: 그날 장기 엔진의 확정 국면')
    st.dataframe(frame, hide_index=True, width='stretch')

