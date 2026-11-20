"""Per-account automation settings (accounts.json): main engine, auto ON/OFF and budget.

Account numbers are never stored here, only a SHA-256 hash and a masked label. A live account orders only through the
guarded paths (nh_live_follow for Jev accounts, longterm_live for long-term ones) and only with its automation and
live-order switches on; its budget may only lower the approved live limits. A missing or invalid file falls back to
safe defaults (live auto OFF).
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

FILE = Path.home() / '.vibe-trading/nhplug/accounts.json'
# How an account trades (user 2026-11-17): Jev trading (the shared paper runner; live accounts mirror its fills) or the
# long-term engine (vibe_longterm; live accounts only, executed by scripts/longterm_live.py). The paper account is Jev.
ENGINES = ('jev', 'longterm')
ENGINE_LABELS = {'jev': 'Jev 트레이딩', 'longterm': '장기투자'}
# User decision 2026-11-12: each live account sets its own 1-share price cap, at most 2,000,000 KRW (was a fixed 10,000).
# The account's capital still bounds what one share can cost.
LIVE_PRICE_HARD_CAP = 2_000_000
# User decision 2026-11-17: live capital is set per account in the dashboard within the money in that account; this is only
# a typo ceiling and equals nhplug_live_guard.CAPITAL_MAX (test_accounts checks it).
LIVE_CAPITAL_MAX = 1_000_000_000
BUDGET_LIMITS = {
    # first buy <= the per-order limit and per symbol <= the held-amount limit (checked below); the outer bounds follow
    # PAPER_LIMITS (raised 2026-11-13 so they never stop a value the order limits allow)
    'paper': {'first_buy_krw': (100_000, 100_000_000), 'per_symbol_krw': (100_000, 5_000_000_000)},
    # Approved live caps (AGENTS.md): capital within the account's money (typo ceiling LIVE_CAPITAL_MAX), the account's
    # 1-share price cap up to 2,000,000 KRW, 20,000 KRW daily loss per account.
    'live': {'capital_krw': (10_000, LIVE_CAPITAL_MAX), 'order_price_max_krw': (1_000, LIVE_PRICE_HARD_CAP), 'daily_loss_krw': (1_000, 20_000)},
}
# Exit rules for a live account's virtual run (user decision 2026-11-12); defaults mirror the paper runner's fixed values.
# The stop may only tighten the approved 2%.
LIVE_RULE_LIMITS = {'take_profit_pct': (0.5, 10.0), 'stop_loss_pct': (0.5, 2.0), 'sell_confidence': (0.5, 0.95),
                    # live mirror sizing and entry (user decision 2026-11-12): share = one share per paper tranche,
                    # amount = tranche_krw of shares per paper tranche; buy orders may carry up to max_order_qty shares
                    'tranche_krw': (10_000, LIVE_CAPITAL_MAX), 'max_order_qty': (1, 1_000), 'chase_pct': (0.1, 5.0),
                    # adds after the first buy the live account follows per position (user 2026-11-17); 100 = all the engine's
                    'max_adds': (0, 100)}
LIVE_SIZING = ('share', 'amount')
DEFAULT_LIVE_RULES = {'take_profit_pct': 2.0, 'stop_loss_pct': 2.0, 'sell_confidence': 0.7, 'flatten': True, 'regime_exits': True,
                      'overnight': True, 'follow_paper': True,
                      'sizing': 'share', 'tranche_krw': 100_000, 'max_order_qty': 100, 'chase_pct': 1.0, 'evidence_chase': True,
                      'max_adds': 100, 'follow_pct': None}
# 금액 기준 모의 대비 비율 (user 2026-11-20): None = 자동 (운용금 ÷ 모의 하루 계획), else 1~100% of the paper fill's KRW
FOLLOW_PCT = (1.0, 100.0)
# Order-guard limits of the shared paper account, set from the dashboard (user request 2026-11-13). The order guard
# (agent/src/tools/nhplug_order_guard.py PAPER_LIMITS) and the loss stop read the same keys with the same bounds and fall
# back to these defaults on any invalid value. held_max_krw caps the cost of open positions (user decision 2026-11-13).
# Upper bounds raised 2026-11-13 at the user's request (the paper account holds about 5 billion KRW of virtual cash).
PAPER_LIMITS = {'order_max_krw': (100_000, 100_000_000), 'daily_buy_krw': (1_000_000, 1_000_000_000),
                'held_max_krw': (1_000_000, 5_000_000_000), 'daily_new_symbols': (1, 50), 'daily_loss_krw': (10_000, 100_000_000)}
DEFAULT_PAPER_LIMITS = {'order_max_krw': 3_000_000, 'daily_buy_krw': 6_000_000, 'held_max_krw': 21_000_000,
                        'daily_new_symbols': 5, 'daily_loss_krw': 300_000}
# Per-account live order limits (user request 2026-11-13). They can only tighten the approved live caps (one order is
# never more than the capital, at most 5 new symbols a day); LiveOrderGuard and the live mirror read them.
LIVE_LIMITS = {'order_max_krw': (10_000, LIVE_CAPITAL_MAX), 'daily_new_symbols': (1, 5)}
DEFAULT_LIVE_LIMITS = {'order_max_krw': 1_000_000, 'daily_new_symbols': 5}
DEFAULT_BUDGET = {'paper': {'first_buy_krw': 1_000_000, 'per_symbol_krw': 3_000_000},
                  'live': {'capital_krw': 1_000_000, 'order_price_max_krw': 10_000, 'daily_loss_krw': 20_000}}
DEFAULTS = {
    'version': 1,
    'accounts': {
        'paper-shared': {'label': '모의계좌 (공유)', 'kind': 'paper', 'engine': 'jev', 'auto': True,
                         'budget': DEFAULT_BUDGET['paper']},
    },
}


def problems(record):
    issues = []
    if record.get('kind') not in BUDGET_LIMITS:
        return ['계좌 종류가 올바르지 않습니다.']
    if record.get('engine') not in ENGINES or (record.get('kind') == 'paper' and record.get('engine') != 'jev'):
        issues.append('운용 방식은 Jev 트레이딩 또는 장기투자입니다(공유 모의계좌는 Jev 트레이딩 고정).')
    if type(record.get('auto')) is not bool:
        issues.append('자동매매 ON/OFF 값이 올바르지 않습니다.')
    budget = record.get('budget') if isinstance(record.get('budget'), dict) else {}
    for key, (low, high) in BUDGET_LIMITS[record['kind']].items():
        value = budget.get(key)
        if type(value) is not int or not low <= value <= high:  # whole KRW: quantities are derived from it
            issues.append(f'{key}는 {low:,}~{high:,} 사이여야 합니다.')
    if record['kind'] == 'paper' and not issues and budget['first_buy_krw'] > budget['per_symbol_krw']:
        issues.append('1차 매수 금액은 종목당 한도보다 클 수 없습니다.')
    if record['kind'] == 'paper':
        limits = record.get('limits') if isinstance(record.get('limits'), dict) else {}
        merged = {**DEFAULT_PAPER_LIMITS, **limits}
        for key, (low, high) in PAPER_LIMITS.items():
            if type(merged.get(key)) is not int or not low <= merged[key] <= high:
                issues.append(f'{key}는 {low:,}~{high:,} 사이여야 합니다.')
        if not issues:
            if merged['order_max_krw'] > merged['daily_buy_krw']:
                issues.append('회당 매수 한도는 하루 매수 한도보다 클 수 없습니다.')
            if budget.get('first_buy_krw', 0) > merged['order_max_krw']:
                issues.append('1차 매수 금액은 회당 매수 한도보다 클 수 없습니다.')
            if budget.get('per_symbol_krw', 0) > merged['held_max_krw']:
                issues.append('종목당 최대는 보유 금액 한도보다 클 수 없습니다.')
    if record['kind'] == 'live' and type(record.get('live_orders', False)) is not bool:
        issues.append('실주문 스위치 값이 올바르지 않습니다.')
    if record['kind'] == 'live' and 'limits' in record:
        merged = {**DEFAULT_LIVE_LIMITS, **(record['limits'] if isinstance(record['limits'], dict) else {})}
        for key, (low, high) in LIVE_LIMITS.items():
            if type(merged.get(key)) is not int or not low <= merged[key] <= high:
                issues.append(f'{key}는 {low:,}~{high:,} 사이여야 합니다.')
        capital = (record.get('budget') or {}).get('capital_krw')
        if type(merged.get('order_max_krw')) is int and type(capital) is int and merged['order_max_krw'] > capital \
                and isinstance(record.get('limits'), dict) and 'order_max_krw' in record['limits']:
            issues.append('회당 매수 한도는 운용금보다 클 수 없습니다.')
    if record['kind'] == 'live' and 'rules' in record:
        rules = {**DEFAULT_LIVE_RULES, **(record['rules'] if isinstance(record['rules'], dict) else {})}  # older records lack the newer keys
        for key, (low, high) in LIVE_RULE_LIMITS.items():
            value = rules.get(key)
            if key in ('tranche_krw', 'max_order_qty', 'max_adds') and type(value) is not int:
                value = None  # whole KRW and whole shares
            if type(value) not in (int, float) or not low <= value <= high:
                issues.append(f'{key}는 {low:g}~{high:g} 사이여야 합니다.')
        if type(rules.get('flatten')) is not bool:
            issues.append('장 마감 정리 값이 올바르지 않습니다.')
        for switch in ('regime_exits', 'overnight', 'follow_paper', 'evidence_chase'):  # rules saved before a switch existed default to ON
            if type(rules.get(switch, True)) is not bool:
                issues.append(f'{switch} 값이 올바르지 않습니다.')
        follow = rules.get('follow_pct')
        if follow is not None and (type(follow) not in (int, float) or not FOLLOW_PCT[0] <= follow <= FOLLOW_PCT[1]):
            issues.append(f'모의 대비 비율은 {FOLLOW_PCT[0]:g}~{FOLLOW_PCT[1]:g}% 또는 자동이어야 합니다.')
        if rules.get('sizing') not in LIVE_SIZING:
            issues.append('매수 크기 기준은 share 또는 amount여야 합니다.')
        if type(rules.get('tranche_krw')) is int and record.get('budget', {}).get('capital_krw') and rules['tranche_krw'] > record['budget']['capital_krw']:
            issues.append('분할 1회 금액은 운용금보다 클 수 없습니다.')
    return issues


def live_rules(record):
    """A live account's exit rules; a record saved before rules existed gets the defaults."""
    return {**DEFAULT_LIVE_RULES, **(record.get('rules') or {})}


def live_record(number, path=FILE):
    """The valid live account record of this account number, or None."""
    for record in load(path).values():
        if record.get('kind') == 'live' and not record.get('invalid') and record.get('hash') == digest(number):
            return record
    return None


def live_limits(number=None, path=FILE, record=None):
    """Per-account live order limits within LIVE_LIMITS (defaults for an unconfigured or invalid account)."""
    record = record if record is not None else (live_record(number, path) if number else None)
    saved = (record or {}).get('limits') if isinstance((record or {}).get('limits'), dict) else {}
    out = dict(DEFAULT_LIVE_LIMITS)
    for key, (low, high) in LIVE_LIMITS.items():
        if type(saved.get(key)) is int and low <= saved[key] <= high:
            out[key] = saved[key]
    return out


def live_loss_realized_only(number=None, path=FILE, record=None):
    """A long-term account's daily loss latch counts only today's realized loss and fees, not the holdings' paper loss
    (user 2026-11-17: a long-term book must keep buying in a drawdown). Unknown accounts keep the stricter basis."""
    record = record if record is not None else (live_record(number, path) if number else None)
    return bool(record) and record.get('engine') == 'longterm'


def live_price_cap(number, path=FILE):
    """The 1-share price cap the live order path enforces for this account number; an account not configured here
    (or with an invalid record) keeps the old 10,000 KRW cap."""
    for record in load(path).values():
        if record.get('kind') == 'live' and not record.get('invalid') and record.get('hash') == digest(number):
            return min(record['budget']['order_price_max_krw'], LIVE_PRICE_HARD_CAP)
    return DEFAULT_BUDGET['live']['order_price_max_krw']


def load(path=FILE):
    """All accounts with validated settings; an invalid record falls back to its kind's safe defaults (auto OFF)."""
    try:
        data = json.loads(Path(path).read_text())
        stored = data.get('accounts') if isinstance(data, dict) else None
    except (OSError, ValueError):
        stored = None
    accounts = deepcopy(DEFAULTS['accounts'])
    for key, record in (stored if isinstance(stored, dict) else {}).items():
        if not isinstance(record, dict):
            continue
        if problems(record):
            kind = 'paper' if key == 'paper-shared' else record.get('kind') if record.get('kind') in BUDGET_LIMITS else 'live'
            record = {**record, 'kind': kind, 'engine': 'jev', 'auto': False, 'budget': DEFAULT_BUDGET[kind],
                      'invalid': True}
        accounts[key] = record
    return accounts


def longterm_accounts(records):
    """{key: record} of the valid live accounts run by the long-term engine (scripts/longterm_live.py)."""
    return {k: r for k, r in records.items() if r.get('kind') == 'live' and r.get('engine') == 'longterm' and not r.get('invalid')}


def stored(path=FILE):
    """The accounts exactly as written on disk (no defaults, no invalid fallback); {} when unreadable."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    accounts = data.get('accounts') if isinstance(data, dict) else None
    return {k: v for k, v in accounts.items() if isinstance(v, dict)} if isinstance(accounts, dict) else {}


def save_one(key, changes, path=FILE, required=False):
    """Change one account only: read the file as it is now, apply `changes` to that record, validate that record and
    write. Other accounts stay byte-for-byte what is on disk (an invalid one is not replaced by defaults), and keys the
    caller did not pass (live_orders, hash, ...) keep their current value, so a stale screen cannot undo a newer change."""
    accounts = stored(path)
    base = accounts.get(key) or (load(path).get(key) or {})
    record = {**{k: v for k, v in base.items() if k not in ('invalid', 'mode')}, **changes}
    match = record.get('match_live') if isinstance(record.get('match_live'), dict) else None
    if key == 'paper-shared' and match and 'budget' in changes and 'match_live' not in changes:
        live = accounts.get(match.get('account'))
        auto = match_values(record, live, match['positions'])['budget'] if live and not problems(live) else {}
        manual = {k: record['budget'][k] for k in ('first_buy_krw', 'per_symbol_krw') if record['budget'].get(k) != auto.get(k)}
        record['match_live'] = {**match, 'manual': manual or None}  # 직접 정한 값 (user 2026-11-20): kept when the live side changes
    issues = problems(record)
    if issues:
        raise ValueError(f'{record.get("label", key)}: ' + ' '.join(issues))
    accounts[key] = record
    _write(accounts, path, required)
    if key != 'paper-shared' and ((accounts.get('paper-shared') or {}).get('match_live') or {}).get('account') == key:
        resync_match(path)  # the paper follows this live account's capital and limits
    return record


# 모의 운용금을 실계좌에 맞춤 (user 2026-11-20 '분할 금액까지 맞추기'): paper-shared `match_live` =
# {'account': live key, 'positions': 동시 보유 종목 수, 'before': {'budget', 'limits'} saved when switched on}. While on, the
# paper's budget and order-guard limits are written from that live account, so the runner, the order guard and the loss
# stop (which read accounts.json as before) trade the paper at the live account's scale.
MATCH_POSITIONS = (1, 5)
MATCH_SPLITS = 3  # nh_paper_isolated.SPLIT_TRANCHES (first buy + two adds)


def match_values(paper, live, positions):
    """{'budget', 'limits'} of the paper account sized to `live`: 분할 1회 = 운용금 ÷ (3 × 동시 보유 종목), 종목당 =
    운용금 ÷ 동시 보유 종목, 보유 금액 한도 = 운용금, 회당 매수·하루 새 종목·하루 손실 = the live account's (each within the
    paper bounds)."""
    capital = int((live.get('budget') or {})['capital_krw'])
    live_limits_ = live_limits(record=live)
    clamp = lambda v, bounds: max(bounds[0], min(bounds[1], int(v)))  # noqa: E731
    first = clamp(capital // (MATCH_SPLITS * positions), BUDGET_LIMITS['paper']['first_buy_krw'])
    limits = {**paper_limits(paper),
              'order_max_krw': clamp(min(live_limits_['order_max_krw'], capital), PAPER_LIMITS['order_max_krw']),
              'held_max_krw': clamp(capital, PAPER_LIMITS['held_max_krw']),
              'daily_new_symbols': clamp(live_limits_['daily_new_symbols'], PAPER_LIMITS['daily_new_symbols']),
              'daily_loss_krw': clamp((live.get('budget') or {}).get('daily_loss_krw') or 20_000, PAPER_LIMITS['daily_loss_krw'])}
    first = min(first, limits['order_max_krw'])
    per_symbol = max(first, min(clamp(capital // positions, BUDGET_LIMITS['paper']['per_symbol_krw']), limits['held_max_krw']))
    return {'budget': {**(paper.get('budget') or {}), 'first_buy_krw': first, 'per_symbol_krw': per_symbol}, 'limits': limits}


def set_match(account, positions=2, on=True, path=FILE):
    """Switch the paper account's match to a live account on (values written now, the previous ones kept) or off
    (the previous budget and limits restored). Returns the paper record written."""
    accounts = stored(path)
    paper = accounts.get('paper-shared') or load(path)['paper-shared']
    current = paper.get('match_live') if isinstance(paper.get('match_live'), dict) else None
    if not on:
        if not current:
            return paper
        before = current.get('before') or {}
        return save_one('paper-shared', {'budget': before.get('budget') or paper['budget'], 'limits': before.get('limits') or paper.get('limits'),
                                         'match_live': None}, path)
    live = accounts.get(account)
    if not live or live.get('kind') != 'live' or problems(live):
        raise ValueError('맞출 실계좌를 찾지 못했습니다.')
    if type(positions) is not int or not MATCH_POSITIONS[0] <= positions <= MATCH_POSITIONS[1]:
        raise ValueError(f'동시 보유 종목 수는 {MATCH_POSITIONS[0]}~{MATCH_POSITIONS[1]}개입니다.')
    before = current['before'] if current else {'budget': paper.get('budget'), 'limits': paper.get('limits')}
    return save_one('paper-shared', {**match_values(paper, live, positions),
                                     'match_live': {'account': account, 'positions': positions, 'before': before}}, path)


def resync_match(path=FILE):
    """Rewrite the matched paper values after the live account's capital or limits changed; None when off or unchanged."""
    accounts = stored(path)
    paper = accounts.get('paper-shared') or {}
    match = paper.get('match_live') if isinstance(paper.get('match_live'), dict) else None
    live = accounts.get((match or {}).get('account'))
    if not match or not live or problems(live):
        return None
    values = match_values(paper, live, match['positions'])
    manual = match.get('manual') if isinstance(match.get('manual'), dict) else {}
    values['budget'] = {**values['budget'], **{k: v for k, v in manual.items() if k in ('first_buy_krw', 'per_symbol_krw')}}
    values['budget']['first_buy_krw'] = min(values['budget']['first_buy_krw'], values['limits']['order_max_krw'])
    values['budget']['per_symbol_krw'] = max(values['budget']['first_buy_krw'], min(values['budget']['per_symbol_krw'], values['limits']['held_max_krw']))
    if values['budget'] == paper.get('budget') and values['limits'] == paper.get('limits'):
        return None
    return save_one('paper-shared', values, path)


def _write(accounts, path, required=False):
    """Write accounts.json (config_store) and record what changed in the settings history; `required`: the history
    must be written (the live-order switch audit), else a history failure does not block the save."""
    import config_store
    before = stored(path)
    clean = {key: {k: v for k, v in record.items() if k not in ('invalid', 'mode')} for key, record in accounts.items()}
    if required:  # the audit comes first: a switch that could not be recorded must not take effect
        config_store.record('accounts', before, clean, required=True, saved=path)
    config_store.write_json(path, {'version': 1, 'updated': datetime.now(ZoneInfo('Asia/Seoul')).isoformat(), 'accounts': clean})
    if not required:
        config_store.record('accounts', before, clean, saved=path)


def save(accounts, path=FILE):
    for key, record in accounts.items():
        issues = problems(record)
        if issues:
            raise ValueError(f'{record.get("label", key)}: ' + ' '.join(issues))
    _write(accounts, path)


def mask(number):
    digits = ''.join(ch for ch in str(number) if ch.isdigit())
    return f'{digits[:3]}-{digits[3:5]}-****{digits[-2:]}' if len(digits) == 11 else '***'


def digest(number):
    """The stored identity of an account: SHA-256 of its digits (the number itself is never stored)."""
    return hashlib.sha256(''.join(ch for ch in str(number) if ch.isdigit()).encode()).hexdigest()


def resolve(records, linked):
    """{key: account number} for live records whose hash matches a broker-linked account; kept in memory only."""
    numbers = {digest(row.get('acct_no')): row['acct_no'] for row in linked
               if isinstance(row, dict) and row.get('acct_type') in ('01', '02') and row.get('acct_no')}
    return {key: numbers[r['hash']] for key, r in records.items() if r.get('kind') == 'live' and r.get('hash') in numbers}


def paper_limits(record=None):
    """The shared paper account's order-guard limits: its saved values within PAPER_LIMITS, defaults otherwise."""
    limits = (record or {}).get('limits') if isinstance((record or {}).get('limits'), dict) else {}
    out = dict(DEFAULT_PAPER_LIMITS)
    for key, (low, high) in PAPER_LIMITS.items():
        value = limits.get(key)
        if type(value) is int and low <= value <= high:
            out[key] = value
    return out


LIVE_ORDER_LOG = 'live-order-switch.jsonl'


def set_live_orders(key, on, path=FILE, log_root=None):
    """Turn one live account's live-order switch on or off. The change (time, account key, off -> on; never the
    number) is recorded in the settings history (config_store) and must be, or the call fails. The old
    live-order-switch.jsonl audit is moved into that history the first time."""
    import config_store
    record = load(path).get(key)
    if not record or record.get('kind') != 'live' or record.get('invalid'):
        raise ValueError('live account unavailable or invalid')
    config_store.import_jsonl(Path(log_root or Path(path).parent) / LIVE_ORDER_LOG, 'accounts',
                              lambda e: (e['time'], f"{e['account']}.live_orders", None, bool(e['live_orders'])), saved=path)
    return save_one(key, {'live_orders': bool(on)}, path, required=True)
