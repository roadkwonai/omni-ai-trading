"""Live mirror of the paper runner: the paper account's verified fills (shared-etf-paper/orders.sqlite3, the same ledger
the paper runner trades from) are the target, so every entry, add, partial and full exit, overnight hold and any later
strategy or learning change of the paper runner reaches the live account unchanged. Each paper tranche becomes the
account's live size (`tranche_shares`: one share, or — amount sizing, user decision 2026-11-17 — the fill's KRW scaled by
live capital / the paper's full plan, at most tranche_krw) and the broker balance is compared every minute; share-sized
buys are capped at max_order_qty shares (amount sizing: only the KRW limits) and sells follow the full gap. A paper buy is
followed the same day while the ask is within the account's chase_pct of the paper fill, or beyond it when Jev still
picks the stock and the price is below the paper plan's first take-profit (`engine_supports`). Older paper fills only
resize a holding the live account carried into today; otherwise only today's paper buys may open one (`opening_units`).
Only the approved live limits can make the live account differ: price cap, capital, 5 new symbols a day, daily loss
latch and the per-symbol stop from the live average price. Hand trades from the dashboard (`apply_manual`) move the
target on top of the mirror. When the paper sells a position out, the live account sells it out too (user 2026-11-18;
this replaced the 11/17 rule that kept such a remainder). Live shares the plan still keeps above the paper (hand buys)
get Jev's holding view every cycle that can order (`review_remainders`; bars read from NH when the runner has none).

Default is --dry-run: broker reads only, the plan is logged, nothing is ordered. --execute orders only through
nh_guarded_live.execute (LiveOrderGuard: the account's capital, 20,000 KRW daily loss latch, up to ORDER_QTY_MAX shares per buy,
at most 5 new symbols a day, the account's price cap on buys, a bid floor on sells, no order while any order is outstanding or uncertain, HALT, buy pause).
Only Jev live accounts (accounts.json engine 'jev') run here; long-term accounts run in longterm_live.py. launchd runs
`live-follow-1` armed with --execute (scripts/launchd_services.py, user approval 2026-11-13); an order also needs the
account's automation and dashboard live-order switches.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timedelta
import sqlite3
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from etf_universe import is_code
from sqlite_ro import ro_connect
from tunables import setting
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')

from src import file_lock

KST = ZoneInfo('Asia/Seoul')
CHECK_INTERVAL_SECONDS = 60
ACCOUNT_LIST_WAIT = 10  # between two asks of the account list at the start
EVIDENCE_MINUTES = 15  # how recent the engine's own pick must be for a buy beyond the chase limit
PENDING_CANCEL_SECONDS = 60   # an acknowledged order still open after this is cancelled (execute mode)
# user 2026-11-25 (모의–실계좌 1 차이 보완 1·2): the mirror followed a paper fill 45 s later (median) and an unfilled sell was
# sold again 3~7 minutes later, 1,300~2,300원 lower
SELL_CANCEL_SECONDS = 10   # the mirror's own unfilled sells; a buy keeps PENDING_CANCEL_SECONDS
SELL_TICKS = 2             # a sell limit this many ticks under the bid: it fills at the best bid and survives a falling one
ORDER_CHECK_SECONDS, OPEN_CHECK_SECONDS, BLOCK_CHECK_SECONDS = 4, 5, 10  # after an order, while one is open, after a refusal
FAST_CYCLES = 24           # quick cycles in a row (an uncertain order must not read the broker every 5 s all day)
LEDGER_POLL_SECONDS = 1.0
SESSION_END = setting('live_session_end')  # can only move earlier


def session_clock(root=None, day=None):
    """The shared paper runner's new-buy cutoff and flatten time (session_times, dashboard settings) under the runtime
    nhplug folder `root` (default: this runtime's) on `day`'s exchange clock (default today); the defaults when
    unreadable."""
    import session_times
    if root is None:
        from src.config.paths import get_runtime_root
        root = get_runtime_root() / 'nhplug'
    try:
        return session_times.load(Path(root) / 'shared-etf-paper', day)
    except (OSError, ValueError, KeyError, TypeError):
        return session_times.times(None, day)


def v2_late_buys(source):
    """(codes, 'HH:MM'): the paper's 선별 v2 positions and the end of its v2 buy window. Its buys (14:30~14:55 by
    default) fall after the new-buy cutoff, and the live mirror follows them the same day (user 2026-11-18)."""
    from nh_paper_isolated import exit_plans, is_v2
    from session_times import hhmm, shifted, today
    import strategy_settings
    settings = strategy_settings.read_settings(source.parent)[0]
    if not settings.get('oversold_v2_on'):
        return frozenset(), None
    return frozenset(c for c, plan in exit_plans(source.parent).items() if is_v2(plan)), shifted(hhmm(settings['oversold_v2_until_hhmm']), today())


def session_over(now):
    """At or past the mirror's end (live_session_end, 15:20) on the day's exchange clock (16:20 on 수능 day)."""
    from session_times import shifted
    return now.weekday() >= 5 or now.strftime('%H:%M') >= shifted(SESSION_END, now.date())


def buy_window_end(root=None):
    """End of the live buy window: the paper runner's new-buy cutoff (the start is session_times.order_start())."""
    return session_clock(root)['cutoff']


LOG = 'live-follow.jsonl'
MIRROR_LAG_ALERT_MIN = 5  # minutes a live account may hold more than the mirror target before it is flagged (11/17)
# user 2026-11-19: paper new-listing buys are followed like any paper fill (the 11/17 exclusion is gone; fills seen while it
# applied stay pinned at 0, so nothing is bought after the fact)
DRY_REPEAT_SECONDS = 120  # a dry-run plan that repeats is written once, then every 2 minutes (the dashboard's 3-minute freshness holds)
CASH = {'BUY': 'krstockOrderCashBuy', 'SELL': 'krstockOrderCashSell'}


def decide(*, target, buyable, owned, held, own_pending, broker_open, halted, exits, buy_paused, clock, late=frozenset(),
           late_until=None):
    """One action for this cycle (pure). target/owned/held: {symbol: shares} mirrored from the paper fills, this
    runner's ledger and the broker; buyable: symbols the paper runner bought today whose ask is still within
    the account chase limit of its fill (or later with engine evidence); exits: {symbol: reason} latched today (per-symbol stop);
    late: 선별 v2 positions that may still buy after the cutoff until late_until (user 2026-11-18). Sells come before
    buys; at most one order per cycle."""
    if own_pending:
        return {'action': 'RECONCILE', 'reason': 'own order outstanding or uncertain'}
    if broker_open:
        return {'action': 'BLOCK', 'reason': 'broker has outstanding orders this runner does not own'}
    short = sorted(c for c, q in owned.items() if q > held.get(c, 0))
    if short:
        return {'action': 'BLOCK', 'symbol': short[0], 'reason': 'ledger holds more than the broker balance'}
    want = {} if halted else dict(target)
    for code in exits:
        want[code] = 0
    sells = sorted(c for c, q in owned.items() if q > want.get(c, 0))
    if sells:
        code = sells[0]
        reason = 'daily loss latch' if halted else exits.get(code) or 'paper runner holds less'
        return {'action': 'SELL', 'symbol': code, 'reason': reason}
    buys = sorted(c for c, q in want.items() if q > owned.get(c, 0))
    if not buys:  # a halted day wants nothing, so it never buys
        return {'action': 'HOLD', 'reason': 'daily loss latch' if halted else 'matches the paper runner'}
    from session_times import order_start
    if not order_start() <= clock < buy_window_end():
        buys = [c for c in buys if c in late] if late_until and order_start() <= clock < late_until else []
        if not buys:
            return {'action': 'WAIT', 'reason': 'outside the entry window'}
    if buy_paused:
        return {'action': 'WAIT', 'reason': 'new buys paused by operator'}
    ready = [c for c in buys if c in buyable]
    if not ready:
        return {'action': 'WAIT', 'symbol': buys[0], 'reason': 'not bought by the paper runner today or price moved past its fill'}
    return {'action': 'BUY', 'symbol': ready[0], 'reason': 'paper runner bought'}


def apply_mirror_units(pos, side, qty, units_bought=1):
    """Reduce existing live units on each sale; later buys must not inherit an earlier sale fraction."""
    held = pos['qty']
    units = pos.get('remaining_units', Decimal(0)) if held else Decimal(0)
    if side == 'BUY':
        units += Decimal(units_bought)
    elif side == 'SELL' and 0 < qty <= held:
        units *= Decimal(held - qty) / Decimal(held)
    else:
        raise ValueError('paper sale exceeds paper holdings')
    pos['remaining_units'] = units


def paper_book(path, shares_for=None):
    """Open paper positions from the paper runner's verified fills (read-only): {symbol: {qty, tranches, cost, price,
    remaining_units, lots}} where tranches counts the buys since the position last opened, cost is the paper cost basis,
    price the last buy, remaining_units the live shares the held tranches map to (`shares_for(price, qty, fill, nth)` per
    tranche, one by default; sold down pro rata) and lots the same units per buy as [(fill time, units)], the first being
    the position's opening buy."""
    db = sqlite3.connect(f'file:{Path(path)}?mode=ro', uri=True, timeout=5)
    try:
        rows = db.execute('SELECT rowid,symbol,side,quantity,price,time FROM fills ORDER BY rowid').fetchall()
    finally:
        db.close()
    book = {}
    for rowid, symbol, side, qty, price, stamp in rows:
        if type(qty) is not int or qty <= 0 or side not in ('BUY', 'SELL'):
            raise ValueError('paper fill invalid')
        pos = book.setdefault(symbol, {'qty': 0, 'tranches': 0, 'cost': 0.0, 'price': None, 'lots': []})
        held = pos['qty']
        units = (shares_for(price, qty, fill_id(rowid, symbol, qty, price, stamp), (pos['tranches'] if held else 0) + 1)
                 if shares_for else 1) if side == 'BUY' else 0
        apply_mirror_units(pos, side, qty, units)  # raises on a sale beyond the paper holding
        if side == 'BUY':
            if held == 0:
                pos.update(tranches=0, cost=0.0, lots=[])
            pos.update(qty=held + qty, tranches=pos['tranches'] + 1, cost=pos['cost'] + qty * price, price=price,
                       lots=pos['lots'] + [(str(stamp), Decimal(units))])
        else:
            pos.update(qty=held - qty, cost=pos['cost'] - pos['cost'] / held * qty,
                       lots=[(t, u * (held - qty) / held) for t, u in pos['lots']])
    return {s: p for s, p in book.items() if p['qty'] > 0}


def opening_units(pos, today):
    """Live shares the units of today's paper buys of this position map to (at least one): all a paper position may open
    today when the live account did not carry it into today (review 2 M3: one paper add must not open older units)."""
    units = sum((u for stamp, u in pos['lots'] if stamp.startswith(today)), Decimal(0))
    return max(1, int(units.to_integral_value(ROUND_HALF_UP))) if units else 0


def carried(guard, code, day):
    """Shares of `code` this runner's ledger held before `day` (YYYYMMDD): adoptions and terminal fills of earlier days."""
    with guard._db() as db:
        flows = dict(db.execute("SELECT operation, COALESCE(SUM(filled),0) FROM live_intents WHERE scope=? AND symbol=? AND day<? "
                                "AND state='terminal' GROUP BY operation", (guard.scope, code, day)).fetchall())
        adopted = db.execute('SELECT COALESCE(SUM(qty),0) FROM live_adoptions WHERE scope=? AND symbol=? AND day<?',
                             (guard.scope, code, day)).fetchone()[0]
    return adopted + flows.get('krstockOrderCashBuy', 0) - flows.get('krstockOrderCashSell', 0)


def paper_source(root, engine):
    """The ledger that trades `engine`'s decisions (engine_roles): the shared paper account for Jev, the main engine.
    None for an engine without a ledger (a long-term account)."""
    from engine_roles import ledger_root
    folder = ledger_root(root, engine)
    return folder / 'orders.sqlite3' if folder else None


def mirror_target(book):
    """Live shares per symbol: the live units of the paper tranches still held (sold down pro rata, rounded), at least
    one while the paper runner holds any; none when no tranche maps to a share (above the price cap in amount mode)."""
    return {s: max(1, int(p['remaining_units'].to_integral_value(ROUND_HALF_UP)))
            for s, p in book.items() if p['remaining_units']}


def tranche_shares(record, paper_record=None, root=None, source=None):
    """Live shares for one paper buy fill of `qty` at `price`: one (sizing share), or (sizing amount, user decision
    2026-11-17) the fill's KRW scaled by live capital / the paper's full plan (first_buy_krw x 3 splits x daily new
    symbols, e.g. 1,000,000 / 15,000,000 = 6.7%: a paper book filled to plan maps to the live capital), at most
    tranche_krw, as a fraction of a share. The paper holding limit is not the basis: it is a ceiling (10억), not what
    the paper spends. Fractions add up per position and mirror_target rounds them, at least one share while the paper
    holds: a share dearer than the scaled amount (e.g. 190,000 KRW) is still bought once when it is within tranche_krw
    and the price cap; one above either maps to none."""
    from accounts import live_rules
    rules = live_rules(record)
    if rules['sizing'] != 'amount':
        return pinned(lambda price, qty=1: 1, record, root, source, rules['max_adds'])
    return pinned(amount_shares(record, paper_record)[1], record, root, source, rules['max_adds'])


def amount_shares(record, paper_record=None):
    """(ratio, shares(price, qty)) of the amount basis for this live account, whatever basis it has chosen."""
    from accounts import DEFAULT_BUDGET, live_rules, load, paper_limits
    from nh_paper_isolated import SPLIT_TRANCHES
    rules = live_rules(record)
    budget = record.get('budget') or {}
    capital = int(budget.get('capital_krw') or DEFAULT_BUDGET['live']['capital_krw'])  # the account's own capital
    if paper_record is None:
        paper_record = load().get('paper-shared') or {}
    first_buy = int((paper_record.get('budget') or {}).get('first_buy_krw') or 1_000_000)
    follow = rules.get('follow_pct')  # 직접 정한 모의 대비 비율 (user 2026-11-20); None = 자동
    ratio = (Decimal(str(follow)) / 100 if type(follow) in (int, float) and 0 < follow <= 100
             else Decimal(capital) / Decimal(first_buy * SPLIT_TRANCHES * paper_limits(paper_record)['daily_new_symbols']))
    amount, cap = rules['tranche_krw'], min(budget.get('order_price_max_krw', 0), rules['tranche_krw'])

    def shares(price, qty=1):
        if not 0 < price <= cap:
            return 0
        return min(Decimal(qty) * Decimal(price) * ratio, Decimal(amount)) / Decimal(price)
    return ratio, shares


def follow_quote(record, price, qty, paper_record=None):
    """{'amount': (shares, why), 'share': (shares, why)} the live account buys when the paper buys `qty` at `price`,
    with this account's own caps (1주 가격 상한, 한 번에 따라 살 최대 금액, 회당 매수 한도, 금액 비율) — the dashboard
    calculator and example table (user 2026-11-20: "내 한도에 맞춰 계산되게")."""
    from accounts import live_limits
    budget = record.get('budget') or {}
    price_cap = int(budget.get('order_price_max_krw', 0))
    order_max = int(live_limits(record=record)['order_max_krw'])
    ratio, shares = amount_shares(record, paper_record)
    out = {}
    if not 0 < price <= price_cap:
        why = f'1주 {price:,}원이 1주 가격 상한 {price_cap:,}원보다 비쌈'
        return {'amount': (0, why), 'share': (0, why)}
    units = shares(price, qty)
    if not units:
        out['amount'] = (0, '1주가 한 번에 따라 살 최대 금액보다 비쌈')
    else:
        n = max(1, int(Decimal(units).to_integral_value(ROUND_HALF_UP)))
        why = f'모의 {qty * price:,}원 × {float(ratio):.1%} = {int(Decimal(qty * price) * ratio):,}원'
        if Decimal(qty * price) * ratio > Decimal(live_rules_of(record)['tranche_krw']):
            why += f' → 한 번 최대 {live_rules_of(record)["tranche_krw"]:,}원까지'
        if n * price > order_max:
            n, why = order_max // price, why + f' → 회당 매수 한도 {order_max:,}원까지'
        out['amount'] = (n, why if n else f'1주도 회당 매수 한도 {order_max:,}원을 넘음')
    out['share'] = (1, '모의가 몇 주를 사든 1주') if price <= order_max else (0, f'1주가 회당 매수 한도 {order_max:,}원을 넘음')
    return out


def live_rules_of(record):
    from accounts import live_rules
    return live_rules(record)


def sizing_example(record, paper_record=None, prices=None):
    """For the dashboard: one paper tranche (first_buy_krw) bought at each price, and the live shares each basis buys
    for it with this account's caps (follow_quote). Prices around the account's own 1-share cap unless given."""
    from accounts import load
    if paper_record is None:
        paper_record = load().get('paper-shared') or {}
    first_buy = int((paper_record.get('budget') or {}).get('first_buy_krw') or 1_000_000)
    price_cap = int((record.get('budget') or {}).get('order_price_max_krw', 0))
    if prices is None:
        prices = sorted({10_000, 50_000, max(10_000, price_cap // 2 // 10_000 * 10_000), max(10_000, price_cap),
                         max(20_000, price_cap * 3 // 2 // 10_000 * 10_000)})
    ratio, _ = amount_shares(record, paper_record)
    rows = []
    for price in prices:
        qty = max(1, first_buy // price)
        quote = follow_quote(record, price, qty, paper_record)
        rows.append({'price': price, 'paper_qty': qty, 'paper_krw': qty * price, 'live_krw': int(Decimal(qty * price) * ratio),
                     'amount': quote['amount'][0], 'amount_why': quote['amount'][1],
                     'share': quote['share'][0], 'share_why': quote['share'][1]})
    return ratio, rows


def fill_id(rowid, symbol, qty, price, stamp):
    return f'{rowid}|{symbol}|{qty}|{price}|{stamp}'


def pins_path(root, record):
    return Path(root) / 'live-follow' / f"units-{str(record.get('hash', 'none'))[:16]}.json"


def pinned(size, record, root=None, source=None, max_adds=None):
    """`size(price, qty)` with each paper fill's live units fixed the first time it is seen (user decision 2026-11-17):
    a later sizing change (sizing, tranche_krw, capital, price cap, the paper plan) applies to new paper fills only,
    so changing a setting never re-sizes a held position and never sells by itself. The live mirror saves the pins
    (save_pins); the dashboard only reads them. Pins belong to one engine ledger: another ledger starts fresh."""
    pins, ledger = {}, str(source.parent.name) if source is not None else None
    if root is not None and source is not None:
        try:
            data = json.loads(pins_path(root, record).read_text(encoding='utf-8'))
            if data.get('ledger') == ledger:
                pins = {k: Decimal(v) for k, v in data['units'].items()}
        except (OSError, ValueError, KeyError, TypeError, ArithmeticError):
            pins = {}

    picks = {}
    if source is not None:  # 주도 테마 동반주 picks of the ranking service (market_movers.THEME_PICKS)
        try:
            picks = json.loads((Path(source).parent / 'theme-picks.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            picks = {}
    themed = {}  # symbol -> its open paper position started on a theme pick

    def units(price, qty=1, fill=None, nth=1):
        """`nth`: this buy's number since the paper position opened (1 = the first buy)."""
        symbol = None
        if fill is not None:
            _, symbol, *_, stamp = fill.split('|')
            if nth == 1:  # user 2026-11-19: a position the paper opened on a theme pick is not followed yet
                pick = (picks.get(stamp[:10]) or {}).get(symbol) if isinstance(picks, dict) else None
                try:  # to the second: a pick noted at 10:16:40 does not claim a position opened at 10:16:05
                    themed[symbol] = isinstance(pick, dict) and datetime.fromisoformat(stamp) >= datetime.fromisoformat(pick['first'])
                except (KeyError, TypeError, ValueError):
                    themed[symbol] = False
        if fill is not None and fill in pins:
            return pins[fill]
        # the account's own add cap (user 2026-11-17): paper adds past it map to no live shares; pinned like any size,
        # so lowering the cap later never sells what was already followed
        value = (Decimal(0) if themed.get(symbol) or (max_adds is not None and nth - 1 > max_adds)
                 else Decimal(size(price, qty)))
        if fill is not None:
            pins[fill] = value
            units.dirty = True
        return value
    units.pins, units.ledger, units.dirty = pins, ledger, False
    return units


def save_pins(root, record, units):
    """Persist new pins (atomic, 0600). A failure stops this cycle's order: unsaved pins could re-size later."""
    if getattr(units, 'dirty', False) and units.ledger is not None:
        from config_store import write_json
        write_json(pins_path(root, record), {'ledger': units.ledger, 'units': {k: str(v) for k, v in units.pins.items()}}, indent=None)
        units.dirty = False


JOURNAL_TAIL_LINES = 400  # lines the tail readers look back over (engine_supports' window; recent_bars needs 30 s)
_JOURNALS = {}  # path -> what journal() has read of it
_JOURNAL_LOCK = threading.Lock()  # the dashboard calls the readers from several script threads


def _journal_line(submitted, tail, raw):
    try:
        line = raw.decode('utf-8')
    except UnicodeDecodeError:
        return
    tail.append(line)
    if '"submitted"' in line:  # text prefilter: json.loads only the few order lines
        try:
            event = json.loads(line)
        except ValueError:
            return  # one unreadable line is skipped, never the whole journal (review 3 X5)
        if isinstance(event, dict) and event.get('event') == 'submitted':
            submitted.append(event)


def journal(path):
    """(every `submitted` event, the last JOURNAL_TAIL_LINES lines) of a runner's append-only events.jsonl. Only bytes
    added since the previous call are read (review 3 M6: the whole 39 MB file was parsed every cycle); a replaced or
    shrunk file is read from the start. An unterminated last line (the runner mid-append) is not kept, but counts in
    this answer while it parses, as the full readers counted it. Raises OSError when the file cannot be read."""
    path = Path(path)
    with _JOURNAL_LOCK:
        st = path.stat()
        state = _JOURNALS.get(path)
        if state is None or state['file'] != (st.st_dev, st.st_ino) or st.st_size < state['offset']:
            state = _JOURNALS[path] = {'file': (st.st_dev, st.st_ino), 'offset': 0, 'stamp': None, 'rest': b'',
                                       'submitted': [], 'tail': deque(maxlen=JOURNAL_TAIL_LINES)}
        if state['stamp'] != (st.st_size, st.st_mtime_ns):
            with path.open('rb') as stream:
                stream.seek(state['offset'])
                data = stream.read()
            end = data.rfind(b'\n') + 1
            for raw in data[:end].splitlines():
                _journal_line(state['submitted'], state['tail'], raw)
            state.update(offset=state['offset'] + end, rest=data[end:], stamp=(st.st_size, st.st_mtime_ns))
        submitted, tail = list(state['submitted']), deque(state['tail'], maxlen=JOURNAL_TAIL_LINES)
        if state['rest'].strip():
            _journal_line(submitted, tail, state['rest'])
    return submitted, tail


def engine_supports(source, code, now):
    """True when Jev still picks `code`: a permitted entry or a Jev candidate at the entry confidence in the last
    EVIDENCE_MINUTES, from the journal of the runner that keeps the mirrored ledger `source`. Reference for a buy beyond
    the chase limit only."""
    from datetime import timedelta
    from etf_universe import SYMBOLS
    from strategy_settings import read_settings
    try:
        entry = read_settings(source.parent)[0]['entry_confidence']
        lines = journal(source.parent / 'events.jsonl')[1]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    for line in reversed(lines):
        try:
            event = json.loads(line)
            if now - datetime.fromisoformat(event['time']) > timedelta(minutes=EVIDENCE_MINUTES):
                return False
        except (ValueError, KeyError, TypeError):
            continue
        if event.get('event') == 'v2_entry_gate' and event.get('permitted') is True and SYMBOLS.get(event.get('choice'), event.get('choice')) == code:
            return True
        candidate = (event.get('jev') or {}).get('candidate') if event.get('event') == 'jev_shadow' else None
        if (isinstance(candidate, dict) and SYMBOLS.get(candidate.get('choice'), candidate.get('choice')) == code
                and type(candidate.get('confidence')) in (int, float) and candidate['confidence'] >= entry):
            return True
    return False


def recent_bars(path, symbols, now):
    """{code: (rows, observed)}: vibe_fast_exit_watch._recent_bars (the newest recorded minute per symbol within 30 s of
    `now`) over the journal tail instead of the whole journal (review 3 X4)."""
    from datetime import timedelta
    from vibe_fast_exit_watch import _recorded_bars, _time
    found = {}
    try:
        lines = journal(path)[1]
    except OSError:
        return found
    for line in lines:
        if '"market_bars"' not in line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get('event') not in ('market_context', 'decision'):
            continue
        at = _time(event.get('time'))
        if at is None or not timedelta(0) <= now - at <= timedelta(seconds=30):
            continue
        for code in symbols:
            if code not in found or found[code][1] < at:
                rows, observed = _recorded_bars(event, code, now)
                if rows:
                    found[code] = (rows, observed)
    return found


def first_target(source, code, avg):
    """The paper plan's first take-profit price for `code` (half level), from the ledger runner's exit plan."""
    from nh_paper_isolated import exit_plans, plan_levels
    return Decimal(str(avg)) * plan_levels(exit_plans(source.parent).get(code))['half']


def ledger_symbols(guard):
    with guard._db() as db:
        rows = db.execute('SELECT symbol FROM live_intents WHERE scope=? UNION SELECT symbol FROM live_adoptions WHERE scope=?',
                          (guard.scope, guard.scope)).fetchall()
    return {r[0] for r in rows}


def ledger_owned(account, root):
    """{symbol: shares} this account's mirror ledger holds. A default guard knows only 114800 and refuses any other
    symbol, so read with one that knows every symbol in the ledger (as the mirror itself does)."""
    from src.tools.nhplug_live_guard import SYMBOL, LiveOrderGuard
    guard = LiveOrderGuard(account, root, symbols=ledger_symbols(LiveOrderGuard(account, root)) | {SYMBOL})
    return guard, {c: n for c in sorted(guard.symbols) if (n := guard.owned(c))}


def positive(value):
    try:
        result = Decimal(str(value)) if isinstance(value, (str, int, float)) and not isinstance(value, bool) else None
    except ArithmeticError:
        return None
    return result if result is not None and result.is_finite() and result > 0 else None


def broker_open(rows):
    """Any order still open at the broker; a row without an integer open quantity counts as open (as the paper runner)."""
    return any(not isinstance(r, dict) or type(r.get('ny_cns_qty')) is not int or r['ny_cns_qty'] != 0 for r in rows)


def bid_ask(reader, code):
    result = reader.call_tool('get_stock_price', {'stock_code': code})
    q = (result.get('data') or {}).get('Output_0') or {} if isinstance(result, dict) and result.get('status') == 'ok' else {}
    if q.get('iem_cd') != code or type(q.get('bidp')) is not int or type(q.get('askp')) is not int or not 0 < q['bidp'] <= q['askp']:
        return None
    return q['bidp'], q['askp']



JEV_BUSY_WAIT_SECONDS = 10  # how long a remainder waits for Jev main to finish before this cycle gives up


def first_alert_today(root, key, code, now):
    """True the first time today that `code` of account `key` could not get Jev's view; the day's codes are kept in
    live-follow/remainder-alerts-<key>.json, so a restart does not alert again (review 3 M9)."""
    path, day = Path(root) / 'live-follow' / f'remainder-alerts-{key}.json', now.strftime('%Y-%m-%d')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        sent = set(data['sent']) if data.get('day') == day else set()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        sent = set()
    if code in sent:
        return False
    from config_store import write_json
    try:
        write_json(path, {'day': day, 'sent': sorted(sent | {code})}, indent=None)
    except OSError:
        pass  # the alert is advisory: it still goes out
    return True


def remainder_jev_view(reader, root, symbol, avg, bid, now, *, sleep=time.sleep):
    """Jev's HOLD/SELL view of live shares the paper engine does not hold (user 2026-11-18: such shares must never sit
    without one). Bars come from the shared runner's recorded minute when fresh, otherwise straight from NH (the runner
    stops recording a symbol once it sold it out, which left the 11/18 remainder without any view). Jev is asked in a
    child with its own lock and research store, as the vibe event Jev; while Jev main is busy it waits up to
    JEV_BUSY_WAIT_SECONDS instead of giving up at once. Returns (view, None) or (None, why)."""
    from etf_universe import SYMBOLS
    from etf_news import headlines7
    from jev_activity import regular_request_busy
    from nh_etf_paper_runner import valid_bars
    from nh_paper_isolated import fresh_etf7_bars, market_view, minute_bars
    from vibe_event_jev import JevEventNotSent, _invoke
    shared = Path(root) / 'shared-etf-paper'
    series = dict(SYMBOLS)
    if symbol not in series.values():
        series[symbol] = symbol  # a scanner stock is charted under its own code, as the paper runner does
    recorded = recent_bars(shared / 'events.jsonl', set(series.values()), now)
    bars = {}
    for name, code in series.items():
        if code in recorded:
            bars[name] = recorded[code][0]
            continue
        try:
            response = minute_bars(reader, code)
            if response.get('status') != 'ok':
                return None, f'bars_unavailable:{code}'
            bars[name] = valid_bars(response.get('data', {}), code, now)
        except (ValueError, TypeError, KeyError, AttributeError):
            return None, f'bars_invalid:{code}'
    if not fresh_etf7_bars(bars, now):
        return None, 'bars_stale'
    for _ in range(JEV_BUSY_WAIT_SECONDS):
        if not regular_request_busy():
            break
        sleep(1)
    else:
        return None, 'jev_main_busy'
    payload = {'bars': bars, 'news': headlines7(now), 'symbol': symbol, 'avg': float(avg), 'bid': float(bid),
               'position_extra': {'context': 'live account shares the paper engine does not hold'},
               'extra_state': {'market_regime_themes_global': market_view(shared)}}
    try:
        result = _invoke(payload, Path(root) / 'live-follow' / 'jev-research')
    except JevEventNotSent:
        return None, 'jev_lock_busy'
    except Exception as exc:  # timeout, child failure, unreadable answer
        return None, f'jev_failed:{type(exc).__name__}'
    answer = result.get('holding') if isinstance(result, dict) else None
    if (not isinstance(answer, dict) or answer.get('choice') not in ('HOLD', 'SELL')
            or type(answer.get('confidence')) not in (int, float) or not 0 <= answer['confidence'] <= 1):
        return None, 'jev_answer_invalid'
    return {'choice': answer['choice'], 'confidence': answer['confidence'], 'time': result.get('response_time')}, None


def review_remainders(reader, root, key, record, target, paper_target, owned, avg, quotes, manual, now, *,
                      view=None, notify=None):
    """Live shares above the paper mirror's target that the plan keeps (today: hand buys, which wait for the flatten
    time) get Jev's holding view every cycle that can order. SELL at the strategy sell_confidence drops their hand
    offset, so this cycle sells them; no view keeps them (the live stop and the daily loss latch still sell without
    Jev) and is logged and alerted once a day per symbol. Mutates `manual`; returns the plan record ({code: {..., 'action'}}:
    step sells those shares this cycle)."""
    from strategy_settings import read_settings
    extra = {c: min(target[c], owned.get(c, 0)) - paper_target.get(c, 0) for c in target}
    extra = {c: n for c, n in extra.items() if n > 0}
    if not extra:
        return {}
    sell_at = read_settings(Path(root) / 'shared-etf-paper')[0].get('sell_confidence', 0.7)
    out = {}
    for code, shares in sorted(extra.items()):
        price, quote = positive(avg.get(code)), quotes.get(code)
        try:  # nothing here may end the cycle: its stop and loss-latch sells come after this review
            answer, why = (None, 'no average price or quote') if not price or not quote else \
                (view or remainder_jev_view)(reader, root, code, price, quote[0], now)
        except Exception as exc:
            answer, why = None, f'error:{type(exc).__name__}'
        if answer is None:
            out[code] = {'shares': shares, 'jev': None, 'why': why}
            if first_alert_today(root, key, code, now):
                (notify or mac_notify)('실계좌 남은 보유 Jev 판단 실패', f'{key} {code} {shares}주: {why}. 손절·하루 손실 보호는 그대로 작동합니다.')
            continue
        out[code] = {'shares': shares, 'jev': answer}
        if answer['choice'] == 'SELL' and answer['confidence'] >= sell_at:
            out[code]['action'] = f"sell: jev SELL {answer['confidence']:.2f}"
            state = manual_state(root, record)
            for part in state.values():
                part.pop(code, None)
            save_manual(root, record, state, now)
            manual['offsets'].pop(code, None)
            manual['left'].pop(code, None)
    return out


def follow_order_current(account, root, record, side, source, book, *, protective=False):
    """Recheck the account switch and, for buys, the settings and engine fills used for this decision."""
    from accounts import live_record
    from nh_live_buy_control import buy_allowed
    latest = live_record(account, Path(root) / 'accounts.json')
    if not latest or latest.get('auto') is not True or latest.get('live_orders') is not True:
        return False
    if latest.get('hash') != record.get('hash'):
        return False
    if side == 'SELL' and protective:
        return True  # sizing changes must not prevent an already-required protective sale
    if (any(latest.get(k) != record.get(k) for k in ('engine', 'budget', 'rules', 'limits'))
            or (side == 'BUY' and not buy_allowed(root))):
        return False
    try:
        return (source is not None and source == paper_source(root, latest.get('engine')) and source.exists()
                and paper_book(source, tranche_shares(latest, root=root, source=source)) == book)
    except (OSError, ValueError, TypeError, sqlite3.Error, ArithmeticError):
        return False


def ordinary_positions(source):
    """Identify open positions only when every buy has a linked, recorded ordinary route of the runner that keeps the
    ledger (engine_exits.ORDINARY_SOURCES)."""
    if source is None or not source.exists():
        return {}
    try:
        with sqlite3.connect(f'file:{source}?mode=ro', uri=True, timeout=5) as db:
            rows = db.execute("SELECT f.symbol,f.side,f.quantity,l.cycle_id FROM fills f "
                              "LEFT JOIN decision_order_links l ON l.order_id=f.order_id ORDER BY f.rowid").fetchall()
        active = {}
        for symbol, side, qty, cycle in rows:
            if type(qty) is not int or qty <= 0 or side not in ('BUY', 'SELL'):
                return {}
            pos = active.setdefault(symbol, {'qty': 0, 'cycles': []})
            if side == 'BUY':
                pos['qty'] += qty
                pos['cycles'].append(cycle)
            else:
                pos['qty'] -= qty
                if pos['qty'] < 0:
                    return {}
                if not pos['qty']:
                    pos['cycles'] = []
        wanted = {cycle for pos in active.values() if pos['qty'] for cycle in pos['cycles'] if cycle}
        events = {}
        for event in journal(source.parent / 'events.jsonl')[0]:
            if isinstance(event.get('cycle_id'), str) and event['cycle_id'] in wanted:
                events.setdefault(event['cycle_id'], []).append(event)
        from engine_exits import ORDINARY_SOURCES, runner_of
        ordinary = ORDINARY_SOURCES[runner_of(source.parent)]
        return {symbol: all(cycle and len(events.get(cycle, [])) == 1
                            and events[cycle][0].get('symbol') == symbol
                            and events[cycle][0].get('source') in ordinary
                            for cycle in pos['cycles'])
                for symbol, pos in active.items() if pos['qty'] and pos['cycles']}
    except (OSError, ValueError, TypeError, sqlite3.Error, AttributeError):
        return {}


def stop_percent_for_positions(source, root, owned, default):
    """{code: {'pct', 'profile', 'regime'}}: the live stop of each held symbol (read-only; the dashboard card uses it too)."""
    from nh_paper_isolated import exit_plans, market_view, stop_symbol_index, tightening_regimes
    profiles = ordinary_positions(source)
    plans = exit_plans(source.parent) if source is not None else {}
    view = market_view(Path(root) / 'shared-etf-paper')
    result = {}
    for code in owned:
        index = stop_symbol_index(Path(root) / 'shared-etf-paper', code)
        regime = ((view or {}).get(index) or {}).get('regime')
        confirmed = profiles.get(code) is True
        plan = plans.get(code) or {}
        if not confirmed and plan.get('profile') in ('oversold', 'ipo'):
            # the paper plan's own stop as is (user 2026-11-17 "대부분 모의매매 따라가게": no 2% cap for these plans; the
            # account's daily loss latch still applies). shortcut: the plan's % is from the paper average, applied to the NH average
            result[code] = {'pct': plan['stop_pct'], 'profile': plan['profile'], 'regime': regime or 'unavailable'}
            continue
        result[code] = {'pct': min(default, 1.0) if confirmed and regime in tightening_regimes(code) else default,  # direction-aware (11/17)
                        'profile': 'ordinary' if confirmed else 'unconfirmed_or_special',
                        'regime': regime or 'unavailable'}
    return result


def cost_pct(code):
    """Estimated round-trip commission and sell tax (%) of a live trade (paper_costs' rates; domestic ETFs pay no tax)."""
    from etf_universe import SYMBOLS
    from paper_costs import COMMISSION_PCT, SELL_TAX_PCT
    return 2 * COMMISSION_PCT + (0 if code in SYMBOLS.values() else SELL_TAX_PCT)


def loss_limit(record):
    """The account's daily loss limit as the guard applies it (never above the approved one)."""
    from src.tools.nhplug_live_guard import LIMIT, LIMIT_MAX
    own = (record.get('budget') or {}).get('daily_loss_krw')
    return min(own, LIMIT_MAX) if type(own) is int and own > 0 else LIMIT


def loss_room(limit, loss, positions):
    """KRW of today's loss limit still free: what is lost now and what the held positions would still lose at their stops are
    taken off. positions: [(shares, bid, average, stop %)]; an unknown bid or average counts as the whole stop."""
    room = Decimal(str(limit)) - Decimal(str(loss))
    for shares, bid, average, pct in positions:
        base = positive(average) or positive(bid)
        if base:
            base = Decimal(str(base))
            stop = base * (1 - Decimal(str(pct)) / 100)
            room -= max((Decimal(str(positive(bid) or base)) - stop) * shares, 0)
    return max(room, Decimal(0))


def loss_room_shares(room, price, stop_pct, code):
    """Shares whose stop and costs together fit in `room` (user 2026-11-25: 안랩 435,000원 with 2,978원 left ended the day)."""
    each = Decimal(str(price)) * Decimal(str(stop_pct + cost_pct(code))) / 100
    return int(Decimal(str(room)) // each) if each > 0 else 0


TICKS = ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500))  # KRX; valid for an ETF too (5)


def tick_size(price):
    return next((tick for below, tick in TICKS if price < below), 1_000)


def sell_limit(bid, ticks=SELL_TICKS):
    """A limit `ticks` under the bid on the tick grid, never below the guard's floor (the bid itself when that is closer)."""
    from src.tools.nhplug_live_guard import SELL_FLOOR_PCT
    price = bid
    for _ in range(ticks):
        lower = (price - 1) // (tick := tick_size(price - 1)) * tick
        if lower * 100 < bid * (100 - SELL_FLOOR_PCT) or lower <= 0:
            break
        price = lower
    return price


def pending_step(reader, guard, credentials, account, snapshots, info, may_cancel, sell_cancel_seconds=PENDING_CANCEL_SECONDS):
    """Reconcile this account's open live intents before anything new (shared by the Jev mirror and longterm_live):
    None when nothing is open; else the cycle's result (RECONCILE / RECONCILED / CANCEL_PENDING). An acknowledged order
    open PENDING_CANCEL_SECONDS is cancelled when may_cancel (also with the live-order switch off: it only lowers risk)."""
    from nh_guarded_live import execute
    from src.live.halt import halt_flag_set
    from src.tools.nhplug_live_guard import LiveOrderBlocked
    with guard._db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS observed_pending (fingerprint TEXT PRIMARY KEY, first_seen REAL NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS follow_exits (scope TEXT NOT NULL, day TEXT NOT NULL, symbol TEXT NOT NULL, reason TEXT NOT NULL, PRIMARY KEY(scope,day,symbol))')
        pending = db.execute("SELECT fingerprint,state,operation,order_number,symbol FROM live_intents WHERE scope=? AND state!='terminal'", (guard.scope,)).fetchall()
        for row in pending:
            db.execute('INSERT OR IGNORE INTO observed_pending VALUES (?,?)', (row[0], time.time()))
    if pending:  # reconcile fills/remainders separately from submission; never submit while anything is open
        left = []
        cancelled_sells = set()
        for fingerprint, state, operation, number, symbol in pending:
            if state == 'acknowledged':
                try:
                    outcome = guard.reconcile(fingerprint, snapshots['history'])
                    info.setdefault('reconciled', {})[fingerprint[:12]] = outcome
                    if operation == 'krstockOrderCashSell' and outcome['filled'] == 0 and outcome['cancelled'] > 0:
                        cancelled_sells.add(symbol)
                    continue
                except LiveOrderBlocked:
                    pass
            left.append((fingerprint, state, operation, number, symbol))
        if cancelled_sells:
            # Reconciling the original order can also close the cancellation from the input list.
            with guard._db() as db:
                still_open = {r[0] for r in db.execute(
                    "SELECT fingerprint FROM live_intents WHERE scope=? AND state!='terminal'", (guard.scope,))}
            left = [row for row in left if row[0] in still_open]
            if not still_open:
                info['cancelled_sells'] = sorted(cancelled_sells)
        has_cancel = any(r[2] == 'krstockOrderCancel' for r in left)
        for fingerprint, state, operation, number, symbol in left:
            with guard._db() as db:
                seen = db.execute('SELECT first_seen FROM observed_pending WHERE fingerprint=?', (fingerprint,)).fetchone()[0]
            # an own order left open is still cancelled after the live-order switch is turned off (it only lowers risk)
            if (may_cancel and state == 'acknowledged' and operation != 'krstockOrderCancel' and not has_cancel
                    and not halt_flag_set('nhplug')
                    and time.time() - seen >= (sell_cancel_seconds if operation == 'krstockOrderCashSell' else PENDING_CANCEL_SECONDS)):
                fields = {'act_no': account, 'iem_cd': symbol, 'org_mkt_orr_no': number, 'all_pat_dit_cd': '1'}
                info['intent'] = execute(reader, guard, credentials, 'krstockOrderCancel', fields)
                return {'action': 'CANCEL_PENDING', 'symbol': symbol, 'execution': 'CANCEL_ACKNOWLEDGED_NOT_RECONCILED', **info}
        return {'action': 'RECONCILE' if left else 'RECONCILED', 'pending': len(left), **info}
    return None


QUIET_RECHECK = timedelta(minutes=5)
_QUIET = {}  # (runtime root, account key) -> when a full read last found nothing held, open, halted or to mirror


def step(reader, account, credentials, record, *, execute_orders=False, root, now=None, key=None, may_cancel=None):
    result = _step(reader, account, credentials, record, execute_orders=execute_orders, root=root, now=now,
                   key=key, may_cancel=may_cancel)
    cancelled_sells = result.get('cancelled_sells')
    if not execute_orders or result['action'] != 'RECONCILED' or not cancelled_sells:
        return result
    started = time.monotonic()
    replacement = _step(reader, account, credentials, record, execute_orders=True, root=root, now=now,
                        key=key, may_cancel=False, replacement_only=cancelled_sells)
    replacement['reconciled'] = result['reconciled']
    replacement['cancelled_sells'] = cancelled_sells
    replacement['replacement_recheck_seconds'] = round(time.monotonic() - started, 2)
    replacement['same_cycle_replacement'] = replacement.get('execution') == 'ACKNOWLEDGED_NOT_FILLED'
    return replacement


def _step(reader, account, credentials, record, *, execute_orders, root, now, key, may_cancel, replacement_only=()):
    from etf_universe import SYMBOLS
    from nh_guarded_live import execute
    from nh_live_broker import collect_snapshots, orderable_cash
    from nh_live_buy_control import buy_allowed
    from nh_live_shadow import effective_rules
    from accounts import live_price_cap
    from src.live.halt import halt_flag_set
    from src.tools.nhplug_live_guard import LiveOrderGuard

    now = (now or datetime.now(KST)).astimezone(KST)
    clock = now.strftime('%H:%M')
    if session_over(now):
        return {'action': 'STOP', 'reason': 'outside session'}
    from session_times import MARKET_OPEN
    if clock < MARKET_OPEN:
        return {'action': 'WAIT', 'reason': 'before the session'}
    from accounts import live_rules
    rules = live_rules(record)
    source = paper_source(root, record.get('engine'))
    try:  # the paper side (another runner's files) must never stop the live stop and loss latch below (review 3 M3)
        sizing = tranche_shares(record, root=root, source=source)
        book = paper_book(source, sizing) if source is not None and source.exists() else None
        save_pins(root, record, sizing)  # unsaved pins could re-size later: no mirror order without them
        paper_error = None
    except Exception as exc:
        book, paper_error = None, type(exc).__name__
    target = mirror_target(book or {})
    requests = claim_requests(root, key) if key else {'exit': {}, 'buy': {}, 'ids': {}}
    probe = LiveOrderGuard(account, root)
    hand = set(manual_state(root, record)['offsets']) | set(requests['buy'])  # hand-traded codes (user 2026-11-17)
    guard = LiveOrderGuard(account, root, symbols=set(SYMBOLS.values()) | set(target) | ledger_symbols(probe) | hand)
    # 2026-11-20 NH call plan P3: a quiet account (nothing held, open, halted or to mirror at the last full read, and
    # still nothing to mirror, no request and nothing this runner owns) re-reads the broker every QUIET_RECHECK instead
    # of every cycle; any of those changing reads at once, and every order still reads the broker right before it
    quiet_key, owned_now = (str(root), key), {c for c in guard.symbols if guard.owned(c)}
    since = _QUIET.get(quiet_key) if key else None
    if (since is not None and timedelta(0) <= now - since < QUIET_RECHECK and book is not None and not any(target.values())
            and not owned_now and not hand and not requests['exit'] and not requests['buy'] and not replacement_only):
        return {'action': 'HOLD', 'reason': 'quiet: broker re-read every 5 minutes',
                'idle': True, 'paper_ledger': source.parent.name if source else None, 'paper': {}, 'target': {}, 'owned': {}, 'held': {},
                'quiet_since': since.isoformat(timespec='seconds')}
    snapshots = collect_snapshots(reader, account, now, symbol=None)  # no quotes here: the cycle quotes its own symbols once
    if key:  # confirmed fills kept past today (live_fills; keyed by the accounts.json key, never the number)
        from live_fills import record as record_fills
        record_fills(root, key, snapshots['history'], now)
        from live_fills import record_balance
        record_balance(root, key, snapshots['balance'], now)  # account totals for Discord and the dashboard (user 2026-11-18)
    risk = guard.observe_loss(snapshots['pnl'], now, (record.get('budget') or {}).get('daily_loss_krw'))  # the account's own limit
    rows = snapshots['balance']['Output_1']
    held, avg = {}, {}
    for r in rows:
        if r.get('iem_cd') and type(r.get('rsdl_qty')) is int and r['rsdl_qty'] > 0:
            held[r['iem_cd']] = held.get(r['iem_cd'], 0) + r['rsdl_qty']
            avg[r['iem_cd']] = r.get('phs_pr')
    owned = {c: n for c in guard.symbols if (n := guard.owned(c))}
    if key:
        quiet = (not held and not owned and not risk['halted'] and book is not None and not any(target.values())
                 and not broker_open(snapshots['history']['Output_0']))
        if quiet:
            _QUIET[quiet_key] = now  # the next QUIET_RECHECK may skip the broker reads
        else:
            _QUIET.pop(quiet_key, None)
    info = {'paper_ledger': source.parent.name if source else None, 'paper': {c: p['qty'] for c, p in (book or {}).items()},
            'target': target, 'owned': owned,
            'held': {c: held.get(c, 0) for c in set(owned) | set(target)}, 'loss': risk['loss'], 'halted': risk['halted']}
    if book is None:
        # no paper runner trades this account's engine, or its ledger is unreadable: nothing to mirror. Hold what this
        # runner owns (the live stop and loss latch still apply) and buy nothing.
        target = dict(owned)
        info.update(target=target, warning=f'paper ledger unreadable: {paper_error}' if paper_error
                    else f"no paper ledger for engine {record.get('engine')}")

    pending = pending_step(reader, guard, credentials, account, snapshots, info,
                           execute_orders if may_cancel is None else may_cancel, SELL_CANCEL_SECONDS)
    if pending is not None:
        return pending

    day = now.strftime('%Y%m%d')
    stop_basis = stop_percent_for_positions(source, root, owned, effective_rules(record)['stop_loss_pct'])
    info['stop_basis'] = stop_basis
    quotes = info['quotes'] = {}  # kept in the record: the (bid, ask) each stop, buy and sell of this cycle was judged on
    for code in owned:  # per-symbol stop from the broker's average price; latched for the rest of the day
        quotes[code] = bid_ask(reader, code)
        price = positive(avg.get(code))  # NH may send the average price as text
        stop = 1 - Decimal(str(stop_basis[code]['pct'])) / 100
        if quotes[code] and price and quotes[code][0] <= price * stop:
            with guard._db() as db:
                db.execute('INSERT OR IGNORE INTO follow_exits VALUES (?,?,?,?)', (guard.scope, day, code, f'{(1 - stop) * 100:g}% stop from average price at {now.isoformat(timespec="seconds")}'))
    requested = [c for c, q in requests['exit'].items() if q is None]  # quantity requests: apply_manual
    if requested:  # a sell-all request from the dashboard: latched like the stop, so the mirror sells and does not re-buy today
        with guard._db() as db:
            for code in requested:
                db.execute('INSERT OR IGNORE INTO follow_exits VALUES (?,?,?,?)', (guard.scope, day, code, 'exit requested from the dashboard'))
    with guard._db() as db:
        exits = dict(db.execute('SELECT symbol,reason FROM follow_exits WHERE scope=? AND day=?', (guard.scope, day)).fetchall())
    stopped_at = {code: why.rpartition(' at ')[2] for code, why in exits.items() if 'stop from average price at ' in why}
    if stopped_at:  # the paper's 선별 v2 re-bought at a held low after its own stop: follow it again (user 2026-11-17)
        rebought = paper_reentries(source, now)
        for code, at in stopped_at.items():
            try:
                again = code in rebought and rebought[code] > datetime.fromisoformat(at)
            except ValueError:
                again = False
            if again:
                with guard._db() as db:
                    db.execute('DELETE FROM follow_exits WHERE scope=? AND day=? AND symbol=?', (guard.scope, day, code))
                exits.pop(code)
    open_orders = broker_open(snapshots['history']['Output_0'])
    paper_target = dict(target)
    manual, degraded = {'offsets': {}, 'left': {}, 'refused': {}}, bool(paper_error)
    if key and not paper_error:  # hand requests wait for a readable paper ledger (their anchors)
        try:
            manual = apply_manual(root, key, record, target, now, position_anchors(book or {}), requests,
                                  quote=lambda code: bid_ask(reader, code), owned=owned)
        except Exception as exc:  # e.g. an unwritable hand-trade file: hold what this runner owns; the stop and latch still sell
            target, degraded = dict(owned), True
            info.update(target=target, warning=f'hand trades unavailable: {type(exc).__name__}')
    if manual['offsets']:
        info['manual'] = manual['offsets']
    if manual['refused']:
        info['manual_refused'] = manual['refused']
    # a cycle that can order (AGENTS 11-18 '주문할 수 있는 회차마다'): shares the paper does not hold get Jev's view
    if key and execute_orders and not degraded and not risk['halted'] and not open_orders:
        try:
            remainder = review_remainders(reader, root, key, record, {c: n for c, n in target.items() if c not in exits},
                                          paper_target, owned, avg, quotes, manual, now)
        except Exception as exc:  # e.g. an unreadable settings or hand-trade file: keep the cycle (stops, loss latch)
            remainder = {'_error': type(exc).__name__}
        for code, entry in remainder.items():
            if isinstance(entry, dict) and 'action' in entry:
                target[code] = paper_target.get(code, 0)
        if remainder:
            info['remainder'] = remainder
            quotes = {}  # Jev took seconds (up to 45): every order of this cycle reads a fresh quote (review 3 M4)

    today = now.strftime('%Y-%m-%d')
    can_buy, mirrored, basis = {}, {}, {}  # shares each code may buy now; the mirror's own part of that
    chase = Decimal(str(rules['chase_pct'])) / 100
    for code, shares in target.items():
        have, pos = owned.get(code, 0), (book or {}).get(code)
        if shares <= have:
            continue
        hand_held, waiting = max(manual['offsets'].get(code, 0), 0), manual['left'].get(code, 0)
        base = shares - hand_held  # the mirror's own target: older paper fills resize a carried holding, never open one
        limit = base if have and carried(guard, code, day) > 0 else min(base, opening_units(pos, today) if pos else 0)
        if pos and limit + hand_held - waiting > have:
            quotes.setdefault(code, bid_ask(reader, code))
            ask = quotes[code][1] if quotes[code] else None
            if ask is not None and ask <= Decimal(str(pos['price'])) * (1 + chase):
                basis[code] = f'within {rules["chase_pct"]:g}% of the paper fill'
            elif (ask is not None and rules['evidence_chase'] and ask < first_target(source, code, pos['cost'] / pos['qty'])
                  and engine_supports(source, code, now)):
                basis[code] = 'engine still picks it, below the paper first target'
            if code in basis:
                stop = stop_percent_for_positions(source, root, [code], effective_rules(record)['stop_loss_pct'])[code]['pct']
                room = loss_room(loss_limit(record), risk['loss'], [(n, (quotes.get(c) or (None,))[0], avg.get(c), stop_basis[c]['pct'])
                                                                    for c, n in owned.items()])
                wanted, fits = limit + hand_held - waiting - have, loss_room_shares(room, ask, stop, code)
                info.setdefault('loss_room', {})[code] = {'room': int(room), 'stop_pct': stop, 'wanted': wanted, 'fits': fits}
                if fits > 0:
                    mirrored[code] = can_buy[code] = min(wanted, fits)
        if waiting:  # a hand buy buys its own shares only, without a paper fill or chase check (the guard's limits hold)
            can_buy[code] = can_buy.get(code, 0) + waiting
            basis.setdefault(code, 'manual buy request')
    info['buy_basis'] = basis
    plan = decide(target=target, buyable=set(can_buy), owned=owned, held=held, own_pending=False, broker_open=open_orders,
                  halted=risk['halted'], exits=exits, buy_paused=not buy_allowed(root), clock=clock,
                  **(dict(zip(('late', 'late_until'), v2_late_buys(source))) if source is not None else {}))
    plan.update(info)
    if plan['action'] == 'WAIT' and (info.get('loss_room') or {}).get(plan.get('symbol'), {}).get('fits') == 0:
        plan['reason'] = 'too little of the daily loss limit left for one share'
    if replacement_only and plan['action'] in CASH and (
            plan['action'] != 'SELL' or plan.get('symbol') not in replacement_only):
        return {**info, 'action': 'RECONCILED', 'pending': 0, 'reason': 'no current matching replacement sell'}
    if plan['action'] not in CASH:
        plan['execution'] = 'NO_ORDER'
        return plan
    code = plan['symbol']
    quote = (bid_ask(reader, code) if plan['action'] == 'BUY' and code in manual.get('amounts', {})
             else quotes.get(code) or bid_ask(reader, code))
    if quote is None:
        plan.update(execution='NO_ORDER', execution_reason='quote unavailable')
        return plan
    price = quote[1] if plan['action'] == 'BUY' else sell_limit(quote[0])
    want = {} if risk['halted'] else dict(target)
    for name in exits:
        want[name] = 0
    gap = want.get(code, 0) - owned.get(code, 0) if plan['action'] == 'BUY' else owned.get(code, 0) - want.get(code, 0)
    # amount sizing has no share cap of its own (user 2026-11-17): the KRW limits below and the guard's ORDER_QTY_MAX hold it
    from src.tools.nhplug_live_guard import ORDER_QTY_MAX
    share_cap = rules['max_order_qty'] if rules['sizing'] != 'amount' else ORDER_QTY_MAX
    qty = max(1, min(gap, share_cap, can_buy[code]) if plan['action'] == 'BUY' else gap)
    if plan['action'] == 'BUY':  # the account's per-order cap and capital room (the guard enforces both too)
        from accounts import DEFAULT_BUDGET, live_limits
        summary = snapshots['balance'].get('Output_0') or {}
        capital = int((record.get('budget') or {}).get('capital_krw') or DEFAULT_BUDGET['live']['capital_krw'])
        invested, cash = positive(summary.get('tot_byn_amt')) or 0, positive(orderable_cash(summary)) or 0
        room = min(capital - int(invested), int(cash))
        qty = min(qty, live_limits(record=record)['order_max_krw'] // price, max(room, 0) // price) if price else 0
        plan['capital_room'] = max(room, 0)
        if code in manual.get('amounts', {}):
            manual_part = max(0, qty - mirrored.get(code, 0))
            allowed = manual['amounts'][code] // price if price else 0
            qty -= max(0, manual_part - allowed)
    plan.update(price=price, qty=qty)
    if plan['action'] == 'BUY' and price > live_price_cap(account):
        plan.update(execution='NO_ORDER', execution_reason='above the account price cap')
        return plan
    if plan['action'] == 'BUY':  # the account's daily new-symbol cap (the guard refuses a 6th; show it in the plan too)
        with guard._db() as db:
            bought = guard._bought(db, day)
        if code not in bought and len(bought) >= min(live_limits(record=record)['daily_new_symbols'], 5):
            plan.update(execution='NO_ORDER', execution_reason='daily new symbol limit')
            return plan
    if plan['action'] == 'BUY' and qty < 1:
        reason = ('manual amount below one share at current ask' if code in manual.get('amounts', {})
                  and manual['amounts'][code] < price else
                  'capital limit reached' if plan.get('capital_room', 0) < price else 'above the account order limit')
        plan.update(execution='NO_ORDER', execution_reason=reason)
        return plan
    if halt_flag_set('nhplug'):
        plan.update(execution='BLOCKED', execution_reason='global or NH live halt')
        return plan
    if not execute_orders:
        plan['execution'] = 'DRY_RUN'
        return plan
    fields = {'act_no': account, 'iem_cd': code, 'orr_qty': qty, 'orr_pr': price, 'nmn_pr_tp_cd': '01', 'orr_cnd_dit_cd': '00',
              'ssl_nmn_pr_dit_cd': '00', 'rmt_mkt_cd': 'KRX', 'sor_mkt_sli_yn': 'N'}
    options = {'pre_reserve_check': lambda: follow_order_current(
        account, root, record, plan['action'], source, book,
        protective=plan['action'] == 'SELL' and plan.get('reason') != 'paper runner holds less')}
    if plan['action'] == 'SELL':
        prior = cancelled_sell(guard, code)
        if replacement_only and prior is None:
            return {**info, 'action': 'RECONCILED', 'pending': 0, 'reason': 'replacement sale identity unavailable'}
        if prior is not None:  # a verified cancelled sell is replaced explicitly
            options['replacement_of'] = prior
    hand_qty = qty - mirrored.get(code, 0) if plan['action'] == 'BUY' else 0
    if hand_qty > 0:  # spent before sending: an unknown outcome never buys it twice
        spend_hand_buy(root, record, code, hand_qty, price)
        with guard._db() as db:
            last = db.execute('SELECT COALESCE(MAX(rowid), 0) FROM live_intents').fetchone()[0]
    try:
        plan['intent'] = execute(reader, guard, credentials, CASH[plan['action']], fields, **options)
    except BaseException:
        if hand_qty > 0 and not sent_since(guard, last):  # refused or aborted before sending: next cycle buys it (review 3 M1)
            spend_hand_buy(root, record, code, -hand_qty, price)
        raise
    plan['execution'] = 'ACKNOWLEDGED_NOT_FILLED'
    return plan


def sent_since(guard, rowid):
    """True when this account has an intent after `rowid` that may have reached the broker (not proven unsent)."""
    with guard._db() as db:
        return db.execute('SELECT 1 FROM live_intents WHERE scope=? AND rowid>? AND fingerprint NOT IN '
                          '(SELECT fingerprint FROM live_pre_send_aborts) LIMIT 1', (guard.scope, rowid)).fetchone() is not None


def cancelled_sell(guard, code):
    """The fingerprint of this symbol's latest sell when the broker verified it cancelled, else None: the guard refuses a
    new sell of the symbol unless it names that one as its replacement (shared by this mirror and longterm_live)."""
    with guard._db() as db:
        latest = db.execute("SELECT fingerprint FROM live_intents WHERE scope=? AND symbol=? AND operation='krstockOrderCashSell' "
                            "AND fingerprint NOT IN (SELECT fingerprint FROM live_pre_send_aborts) ORDER BY rowid DESC LIMIT 1",
                            (guard.scope, code)).fetchone()
        if latest and db.execute('SELECT 1 FROM sell_cancellations WHERE fingerprint=?', (latest[0],)).fetchone():
            return latest[0]
    return None


def exit_request_path(root, key):
    return Path(root) / f'live-exit-requests-{key}.json'


def paper_reentries(source, now):
    """{symbol: latest time} of today's 선별 v2 re-buys the paper runner journaled (source 'oversold_v2_reentry')."""
    out = {}
    try:
        submitted = journal(Path(source).parent / 'events.jsonl')[0]
    except (OSError, TypeError):
        return out
    day = now.strftime('%Y-%m-%d')
    for event in submitted:
        if event.get('source') != 'oversold_v2_reentry' or not isinstance(event.get('symbol'), str):
            continue
        try:
            at = datetime.fromisoformat(event['time'])
        except (ValueError, KeyError, TypeError):
            continue
        if event['time'].startswith(day):
            out[event['symbol']] = max(at, out.get(event['symbol'], at))
    return out


def exit_requests(root, key):
    """{code: shares or None (all)} the dashboard asked this live account to sell: a JSON list sells all, an object
    {code: shares} sells that many (user 2026-11-17); empty on any doubt."""
    return _exit_requests(exit_request_path(root, key)) if key else {}


def _valid_manual_amount(q):
    from accounts import LIVE_CAPITAL_MAX
    return (isinstance(q, dict) and set(q) == {'amount_krw', 'day'}
            and type(q['amount_krw']) is int and 0 < q['amount_krw'] <= LIVE_CAPITAL_MAX
            and isinstance(q['day'], str) and len(q['day']) == 10)


def _exit_requests(path):
    try:
        codes = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if isinstance(codes, list):  # a non-string item makes the whole file malformed (never a TypeError)
        codes = {c: None for c in codes} if all(isinstance(c, str) for c in codes) else None
    if not isinstance(codes, dict):
        return {}
    return {c: q for c, q in codes.items() if is_code(c) and (
            q is None or (type(q) is int and q > 0) or
            _valid_manual_amount(q))}


def write_request_file(path, requests):
    path = Path(path)
    if not requests:
        path.unlink(missing_ok=True)
        return
    from config_store import write_json
    write_json(path, requests, indent=None)


def manual_buy_path(root, key):
    return Path(root) / f'live-manual-buys-{key}.json'


def manual_buy_requests(root, key):
    """{code: shares} the dashboard asked this live account to buy by hand; empty on any doubt."""
    return _buy_requests(manual_buy_path(root, key)) if key else {}


def _buy_requests(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return {c: q for c, q in data.items() if is_code(c) and (
            (type(q) is int and q > 0) or
            _valid_manual_amount(q))} \
        if isinstance(data, dict) else {}


CLAIM = '.claim'


def claim_requests(root, key):
    """This cycle's dashboard requests (review 3 M5). Each request file is first renamed to <name>.claim, so a request
    the dashboard writes meanwhile lands in a new file for the next cycle, and a claim a stopped cycle left behind is
    taken again; apply_manual records the claims it applied, so a claim is never applied twice. Never raises (a request
    that cannot be taken waits; the cycle's stop and loss latch must run). Exit and buy values can be shares or a dated
    amount request; None for exit means sell all. IDs identify each claimed file."""
    out = {'exit': {}, 'buy': {}, 'ids': {}}
    for kind, path, parse in (('exit', exit_request_path(root, key), _exit_requests),
                              ('buy', manual_buy_path(root, key), _buy_requests)):
        claim = path.with_name(path.name + CLAIM)
        try:
            if not claim.exists():
                os.rename(path, claim)
            st = claim.stat()
        except OSError:  # nothing requested (or not takeable now)
            continue
        out[kind], out['ids'][kind] = parse(claim), f'{st.st_ino}:{st.st_mtime_ns}:{st.st_size}'
    return out


def release_requests(root, key):
    for path in (exit_request_path(root, key), manual_buy_path(root, key)):
        path.with_name(path.name + CLAIM).unlink(missing_ok=True)


def manual_path(root, record):
    return Path(root) / 'live-follow' / f"manual-{str(record.get('hash', 'none'))[:16]}.json"


def manual_state(root, record):
    """The account's hand trades (read-only; the dashboard card reads it too): {'offsets': {code: shares}, 'anchors':
    {code: the position each offset belongs to}, 'since': {code: when it was last changed}, 'left': {code: hand-bought
    shares not yet ordered}, 'claims': {kind: the request claim last applied (claim_requests)}}."""
    try:
        data = json.loads(manual_path(root, record).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {}
    def part(name):
        return data[name] if isinstance(data, dict) and isinstance(data.get(name), dict) else {}
    offsets = {c: v for c, v in part('offsets').items() if type(v) is int and v}
    return {'offsets': offsets, 'anchors': {c: v for c, v in part('anchors').items() if c in offsets},
            'since': {c: v for c, v in part('since').items() if c in offsets and isinstance(v, str)},
            'left': {c: min(v, offsets[c]) for c, v in part('left').items() if c in offsets and type(v) is int and v > 0 and offsets[c] > 0},
            'amounts': {c: v for c, v in part('amounts').items() if c in offsets and type(v) is int and v >= 0},
            'amount_days': {c: v for c, v in part('amount_days').items() if c in offsets and isinstance(v, str)},
            'claims': {k: v for k, v in part('claims').items() if isinstance(v, str)}}


def save_manual(root, record, state, now):
    from config_store import write_json
    write_json(manual_path(root, record), {**state, 'time': now.isoformat()}, indent=None)


def position_anchors(book):
    """{code: the position a hand offset belongs to}: the paper position (its opening buy). A new position starts
    without the previous one's hand cut (review 2 M4)."""
    return {code: f"paper:{pos['lots'][0][0]}" for code, pos in book.items()}


def apply_manual(root, key, record, target, now, anchors, requests, *, quote=None, owned=None):
    """Hand trades on top of the mirror (user 2026-11-17). A quantity sell request lowers this account's target for the
    symbol by that many shares, kept until the position it was made on ends (`anchors`: a new paper position starts
    clean), so the mirror never buys them back; a buy request raises it until the shared flatten time and only those
    shares may be bought for it (`left`). From the end of the live buy window a hand buy is refused, and shares not yet
    bought lapse, with the reason in the plan (review 3 M2; the paper refuses its own from the flatten). Sell-all
    requests stay on the dashboard latch. `requests` are this cycle's claims (claim_requests): applied, recorded in the
    saved state and only then released. Mutates `target`; returns {'offsets', 'left', 'refused'}."""
    state = manual_state(root, record)
    offsets, marks, since, left, amounts, amount_days = (state['offsets'], state['anchors'], state['since'],
                                                           state['left'], state['amounts'], state['amount_days'])
    clock = now.strftime('%H:%M')
    late = clock >= session_clock(root)['flatten']
    end = buy_window_end(root)
    def fresh(kind):
        return kind in requests['ids'] and state['claims'].get(kind) != requests['ids'][kind]

    def drop(code):
        for part in (offsets, marks, since, left, amounts, amount_days):
            part.pop(code, None)

    for code in list(offsets):
        if code in amounts and amount_days.get(code) != now.date().isoformat():
            offsets[code] -= left.pop(code, 0)
            amounts.pop(code, None)
            amount_days.pop(code, None)
            if not offsets[code]:
                drop(code)
                continue
        here = anchors.get(code)
        moved = marks.get(code, here) != here  # a file written before anchors belongs to the current position
        if (offsets[code] < 0 and moved) or (offsets[code] > 0 and late):
            drop(code)
        else:
            marks[code] = here
    paper_target = dict(target)
    sells = {c: q for c, q in requests['exit'].items() if q} if fresh('exit') else {}
    buys = dict(requests['buy']) if fresh('buy') else {}
    refused = {}
    for code, request in list(sells.items()):
        if not isinstance(request, dict):
            continue
        amount = request['amount_krw']
        if request['day'] != now.date().isoformat():
            refused[code] = {'amount_krw': amount, 'why': 'request is from another day'}
        elif not (owned or {}).get(code):
            refused[code] = {'amount_krw': amount, 'why': 'mirror no longer owns this symbol'}
        elif code in buys or left.get(code):
            refused[code] = {'amount_krw': amount, 'why': 'another manual buy is pending'}
        elif max(0, paper_target.get(code, 0) + offsets.get(code, 0)) != owned[code]:
            refused[code] = {'amount_krw': amount, 'why': 'mirror target and holdings differ'}
        else:
            current = quote(code) if quote is not None else None
            if current is None:
                raise ValueError('manual amount quote unavailable')
            count = min(amount // sell_limit(current[0]), owned[code])
            if count:
                sells[code] = count
                continue
            refused[code] = {'amount_krw': amount, 'why': 'amount below one share'}
        del sells[code]
    for code, request in list(buys.items()):
        if isinstance(request, dict):
            if request['day'] != now.date().isoformat():
                refused[code] = {'amount_krw': request['amount_krw'], 'why': 'request is from another day'}
                del buys[code]
                continue
            if not (owned or {}).get(code):
                refused[code] = {'amount_krw': request['amount_krw'], 'why': 'mirror no longer owns this symbol'}
                del buys[code]
                continue
            if left.get(code):
                refused[code] = {'amount_krw': request['amount_krw'], 'why': 'another manual buy is pending'}
                del buys[code]
                continue
            if clock >= end:
                refused[code] = {'amount_krw': request['amount_krw'], 'why': f'after the live buy window ({end})'}
                del buys[code]
                continue
            current = quote(code) if quote is not None else None
            if current is None:
                raise ValueError('manual amount quote unavailable')
            count = request['amount_krw'] // current[1]
            if count < 1:
                refused[code] = {'amount_krw': request['amount_krw'], 'why': 'amount below one share'}
                del buys[code]
                continue
            buys[code] = count
            amounts[code] = request['amount_krw']
            amount_days[code] = request['day']
        elif code in amounts and left.get(code):
            refused[code] = {'shares': request, 'why': 'another manual buy is pending'}
            del buys[code]
        else:
            amounts.pop(code, None)
            amount_days.pop(code, None)
    if clock >= end:
        for code, qty in list(buys.items()) + list(left.items()):
            refused[code] = {'shares': refused.get(code, {}).get('shares', 0) + qty, 'why': f'after the live buy window ({end})'}
        for code, qty in list(left.items()):
            offsets[code] -= qty
            if not offsets[code]:
                drop(code)
        buys, left = {}, {}
    for code, qty in sells.items():
        offsets[code] = offsets.get(code, 0) - min(qty, max(0, paper_target.get(code, 0) + offsets.get(code, 0)))
    for code, qty in buys.items():
        offsets[code], left[code] = offsets.get(code, 0) + qty, left.get(code, 0) + qty
    for code in set(sells) | set(buys):
        marks[code], since[code] = anchors.get(code), now.isoformat()
        if not offsets[code]:
            drop(code)
    left = {c: min(n, offsets[c]) for c, n in left.items() if offsets.get(c, 0) > 0}
    save_manual(root, record, {'offsets': offsets, 'anchors': marks, 'since': since, 'left': left, 'amounts': amounts,
                               'amount_days': amount_days,
                               'claims': requests['ids']}, now)
    release_requests(root, key)
    for code, offset in offsets.items():
        target[code] = max(0, target.get(code, 0) + offset)
    return {'offsets': offsets, 'left': left, 'amounts': amounts, 'refused': refused}


def spend_hand_buy(root, record, code, qty, price=None):
    """Count `qty` hand-bought shares of `code` as ordered (a negative `qty` puts shares proven unsent back)."""
    state = manual_state(root, record)
    state['left'] = {c: n for c, n in {**state['left'], code: state['left'].get(code, 0) - qty}.items() if n > 0}
    if code in state['amounts']:
        if type(price) is not int or price <= 0:
            raise ValueError('manual amount price unavailable')
        state['amounts'][code] -= qty * price
        if state['amounts'][code] < 0:
            raise ValueError('manual amount exceeded')
    save_manual(root, record, state, datetime.now(KST))


def fold(last, event, now):
    """(write?, state): dry-run lines that repeat the previous plan are folded (11/17 live 2 wrote the same unfilled BUY
    144 times because a dry run never fills). A changed plan, any execute-mode line, or DRY_REPEAT_SECONDS of repeats is
    written; the written line carries `folded` = how many identical lines were skipped before it."""
    keys = ('action', 'reason', 'symbol', 'qty', 'execution', 'execution_reason', 'target', 'owned', 'held', 'halted',
            'error_type', 'pending', 'warning', 'manual_refused')
    signature = json.dumps({k: event.get(k) for k in keys}, sort_keys=True, default=str)
    if (event.get('mode') == 'dry_run' and last and last['signature'] == signature
            and (now - last['written']).total_seconds() < DRY_REPEAT_SECONDS):
        return False, {**last, 'skipped': last['skipped'] + 1}
    if last and last.get('skipped'):
        event['folded'] = last['skipped']
    return True, {'signature': signature, 'written': now, 'skipped': 0}


def mac_notify(title, message):
    """Best-effort macOS notification; never raises (an alert must not stop the mirror)."""
    import subprocess
    try:
        subprocess.run(['osascript', '-e', f'display notification {json.dumps(message)} with title {json.dumps(title)}'],
                       timeout=5, check=False, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        pass


def mirror_lag(root, key, event, now, notify=mac_notify):
    """Alert when a live account in execute mode keeps holding more than its mirror target (the paper sold or cut and the
    live sell did not follow) for MIRROR_LAG_ALERT_MIN minutes. 11/17 12:11~12:36 the live 1 stop lagged the paper by 24
    minutes with no alarm. Episode state and the open alert live in live-follow/lag-<key>.json and lag-alert-<key>.json;
    the alert is cleared once owned <= target again. Under-buying (a chase-limited buy) is not a lag."""
    if event.get('mode') != 'execute':
        return None
    folder = Path(root) / 'live-follow'
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path, alert = folder / f'lag-{key}.json', folder / f'lag-alert-{key}.json'
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
        if str(state.get('since', ''))[:10] != now.date().isoformat():
            state = {}  # yesterday's episode does not carry over
    except (OSError, ValueError, AttributeError):
        state = {}
    owned, target = event.get('owned'), event.get('target')
    if isinstance(owned, dict) and isinstance(target, dict):
        excess = {c: int(n) - int(target.get(c) or 0) for c, n in owned.items() if int(n) > int(target.get(c) or 0)}
        if not excess:
            path.unlink(missing_ok=True)
            alert.unlink(missing_ok=True)
            return None
        state = state or {'since': now.isoformat(), 'notified': False}
        state['excess'] = excess
    elif not state:
        return None  # a BLOCK or RECONCILE line says nothing new; an open episode keeps counting through it
    minutes = (now - datetime.fromisoformat(state['since'])).total_seconds() / 60
    result = None
    if minutes >= MIRROR_LAG_ALERT_MIN:
        result = {'minutes': round(minutes, 1), 'since': state['since'], 'excess': state.get('excess'),
                  'last_action': event.get('action'), 'last_reason': event.get('reason')}
        write_request_file(alert, result)
        if not state.get('notified'):
            notify('실계좌 미러 지연', f"{key}: 모의보다 {state.get('excess')} 더 보유한 채 {round(minutes)}분째 매도를 못 따라감")
            state['notified'] = True
    write_request_file(path, state)
    return result


def paper_stamp(source):
    """What tells a new fill in the paper ledger: None when it cannot be read (the mirror then keeps its minute)."""
    try:
        with ro_connect(source, 1) as db:
            return db.execute('SELECT COUNT(*), MAX(rowid) FROM fills').fetchone()
    except (sqlite3.Error, OSError, TypeError, AttributeError):
        return None


def next_wait(result):
    """Seconds to the next cycle when the paper ledger does not change first."""
    if result.get('execution') == 'ACKNOWLEDGED_NOT_FILLED':
        return ORDER_CHECK_SECONDS
    return {'RECONCILED': LEDGER_POLL_SECONDS, 'RECONCILE': OPEN_CHECK_SECONDS, 'CANCEL_PENDING': OPEN_CHECK_SECONDS,
            'BLOCK': BLOCK_CHECK_SECONDS}.get(result.get('action'), CHECK_INTERVAL_SECONDS)


def wait_for_fill(stamp, seen, seconds, *, sleep=time.sleep, clock=time.monotonic):
    """Wait `seconds`, or until the paper ledger's stamp differs from `seen`: True when it did."""
    end = clock() + seconds
    while True:
        current = stamp()
        if current is not None and current != seen:
            return True
        left = end - clock()
        if left <= 0:
            return False
        sleep(min(LEDGER_POLL_SECONDS, left))


def configured_accounts(values):
    """Live account numbers whose orders .env authorizes: ACCOUNT_NUMBER, then ACCOUNT_NUMBER_2, _3, ... (digits only)."""
    keys = ['ACCOUNT_NUMBER'] + sorted((k for k in values if k.startswith('ACCOUNT_NUMBER_') and k[15:].isdigit()), key=lambda k: int(k[15:]))
    numbers = [''.join(c for c in (values.get(k) or '') if c.isdigit()) for k in keys]
    return [n for n in numbers if len(n) == 11]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', required=True, help='accounts.json key of a live account')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', default=True, help='default: plan only, no orders')
    mode.add_argument('--execute', action='store_true', help='order through nh_guarded_live (separate user approval)')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    from dotenv import dotenv_values
    from accounts import load, resolve
    from nh_guarded_live import ENV
    from nh_live_broker import linked_accounts
    from src.config.loader import load_agent_config
    from src.config.paths import get_runtime_root
    from src.tools.mcp import MCPServerAdapter

    root = get_runtime_root() / 'nhplug'
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.umask(0o077)
    def current():
        record = load().get(args.account)
        # a long-term account is run by longterm_live.py, never mirrored (user 2026-11-17)
        return record if record and record.get('kind') == 'live' and not record.get('invalid') and record.get('engine') == 'jev' else None

    record = current()
    if not record:
        raise SystemExit('live account record unavailable, invalid or not on Jev trading')
    reader = MCPServerAdapter('nhplug_readonly', load_agent_config().mcp_servers['nhplug_readonly'])
    rows = linked_accounts(reader, lambda: args.once or session_over(datetime.now(KST)), ACCOUNT_LIST_WAIT)
    if not rows:
        raise SystemExit('account list unavailable')
    account = resolve({args.account: record}, rows).get(args.account)
    if not account:
        raise SystemExit('live account not linked at the broker')
    values = dotenv_values(ENV, interpolate=False)
    configured = configured_accounts(values)
    if args.execute:
        if account not in configured or not values.get('APP_KEY') or not values.get('APP_SECRET'):
            raise SystemExit('--execute needs an account listed in .env (ACCOUNT_NUMBER, ACCOUNT_NUMBER_2, ...) and the app key')
    # the first account's execute shares live-auto's lock so two live order loops never order it at once; every other
    # account (and every dry-run) has its own lock, so accounts run side by side
    primary = configured[0] if configured else None
    name = ('live-auto.lock' if account == primary else f'live-follow-exec-{args.account}.lock') if args.execute else f'live-follow-dry-{args.account}.lock'
    with (root / name).open('a', encoding='utf-8') as lock:
        file_lock.lock(lock, blocking=False)
        logged = None  # fold(): the last written plan of this account
        fast = 0
        while True:
            try:
                source = paper_source(root, (record or {}).get('engine'))
            except Exception:  # unreadable settings cost only the early wake; step() judges them itself
                source = None
            seen = paper_stamp(source)  # a paper fill during the cycle starts the next one at once
            try:  # settings are re-read every cycle; a disabled or invalid record stops ordering at once
                record = current()
                if record is None:
                    raise SystemExit('live account record became unavailable or invalid')
                # live orders need all three: the service armed with --execute, the account's automation switch, and the
                # dashboard's live-order switch (accounts.json live_orders, user request 2026-11-13; default off)
                live_now = bool(args.execute and record['auto'] is True and record.get('live_orders') is True)
                if record['auto'] is not True:  # automation off: the mirror rests (re-read every cycle, no restart needed)
                    result = {'action': 'WAIT', 'reason': 'automation off'}
                else:
                    result = step(reader, account, values, record, execute_orders=live_now, root=root, key=args.account,
                                  may_cancel=args.execute)
            except Exception as exc:  # broker/guard refusals are logged; an unknown outcome stays an uncertain intent
                from src.tools.nhplug_live_guard import LiveOrderBlocked
                result = {'action': 'BLOCK', 'error_type': type(exc).__name__,
                          **({'reason': str(exc)} if isinstance(exc, LiveOrderBlocked) else {})}
            live_now = bool(args.execute and record and record.get('auto') is True and record.get('live_orders') is True)
            event = {'time': datetime.now(KST).isoformat(), 'account': args.account,
                     'mode': 'execute' if live_now else 'dry_run', 'armed': bool(args.execute),
                     'manual_buy_amount_supported': True, 'manual_sell_amount_supported': True, **result}
            try:
                lag = mirror_lag(root, args.account, event, datetime.now(KST))
            except Exception as exc:  # the alarm is advisory; it must never stop the mirror
                lag = {'error_type': type(exc).__name__}
            if lag:
                event['lag_alert'] = lag
            write, logged = fold(logged, event, datetime.now(KST))
            if write:
                with (root / LOG).open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(event, ensure_ascii=False, default=str) + '\n')
                print(json.dumps(event, ensure_ascii=False, default=str), flush=True)
            if args.once or result.get('action') == 'STOP':
                break
            quick = next_wait(result)
            woken = wait_for_fill(lambda: paper_stamp(source), seen, quick if fast < FAST_CYCLES else CHECK_INTERVAL_SECONDS)
            fast = 0 if woken or quick >= CHECK_INTERVAL_SECONDS else fast + 1


if __name__ == '__main__':
    main()
