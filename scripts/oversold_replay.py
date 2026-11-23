"""낙주매매 재현 (dip-buy replay): what several ways of buying a sharp fall would have earned, case by case.

User request 2026-11-13: replay and record the past falls (KRX stocks and ETFs, KOSPI, Nasdaq, S&P 500, oil, crypto)
so the current rule can be compared with the variants the case study points to. RESEARCH ONLY: no orders.

Every strategy buys on the day of the fall (case day i) from daily bars [(dt, o, h, l, c, ...)] and is checked bar by
bar after that: the stop before the target within a day (conservative), fills at the stop or target price unless the
day opened beyond it (then at the open). Costs are a round trip per kind. The current intraday rule needs intraday
bars and is replayed by oversold_cases (case['intraday']['rule']); it is added here as 'now_rule' when present.
"""
from __future__ import annotations

import statistics
from collections import defaultdict

COST = {'stock': 0.25, 'etf': 0.1, 'lev': 0.1, 'overseas_etf': 0.1, 'kr_index': 0.1, 'us_index': 0.1, 'oil': 0.1, 'crypto': 0.2}
# name: (label, entry 'close' | 'next_open', max holding days, target, stop) — target 'gap' = back to the previous
# close; stop 'low5' = 5% under the day low, 'atr' = one 20-day ATR under the day low.
STRATEGIES = {
    'close_1d': ('종가 매수 → 다음 날 종가', 'close', 1, None, None),
    'close_3d': ('종가 매수 → 3일 뒤 종가', 'close', 3, None, None),
    'close_5d': ('종가 매수 → 5일 뒤 종가', 'close', 5, None, None),
    'close_5d_atr': ('종가 매수 · 저가−ATR 손절 · 5일', 'close', 5, None, 'atr'),
    'close_5d_low5': ('종가 매수 · 저가−5% 손절 · 5일', 'close', 5, None, 'low5'),
    'close_5d_tp8_low5': ('종가 매수 · +8% 익절 · 저가−5% 손절 · 5일', 'close', 5, 8.0, 'low5'),
    'close_gap_low5': ('종가 매수 · 전일 종가 회복 익절 · 저가−5% 손절 · 5일', 'close', 5, 'gap', 'low5'),
    'next_open_5d': ('다음 날 시가 매수 → 5일 뒤 종가', 'next_open', 5, None, None),
}
LABELS = {'now_rule': '지금 규칙 (장중 하락 멈춤 · +1.5% · 저가−0.5% · 1일)', **{k: v[0] for k, v in STRATEGIES.items()},
          'v2_plan': '선별 v2 낙주매매 (절반 → 다음 날 2차 · +8% 절반 · +15% · 3일 · 평균가−2% 손절)'}


V2_RULE_VERSION = 'v2-avg-stop-close-replay-2026-11-23'


def run_v2(daily, i, kind, settings=None):
    """The paper runners' 선별 v2 plan on daily bars: half at the close of day i, the other half at the next close when
    it closed lower and above the stop; half sold at +half_tp of the average, the rest at +tp, otherwise at the close
    of session i + hold; a stop oversold_v2_stop_pct under the average buy price (checked first each day; user
    2026-11-17, was a disaster stop under day i's low). The live re-buy after a stop (v2_reentry) needs intraday bars
    and is not replayed here. % on the capital used."""
    from strategy_settings import DEFAULTS
    v = {**DEFAULTS, **(settings or {})}
    hold, stop_pct = int(v['oversold_v2_hold_days']), v['oversold_v2_stop_pct']
    if i + hold >= len(daily):
        return {'pending': True, 'rule_version': V2_RULE_VERSION, 'settings': {k: v[k] for k in v if k.startswith('oversold_v2_')}}
    first = daily[i][4]
    stop = first * (1 - stop_pct / 100)
    lots = [(first, 0.5 if v['oversold_v2_add_on'] else 1.0)]
    nxt = daily[i + 1]
    if v['oversold_v2_add_on'] and nxt[3] > stop and nxt[4] < first:
        lots.append((nxt[4], 0.5))
    qty = sum(q for _, q in lots)
    average = sum(px * q for px, q in lots) / qty
    stop = average * (1 - stop_pct / 100)  # the stop follows the average after the second half
    half_px, all_px = average * (1 + v['oversold_v2_half_tp_pct'] / 100), average * (1 + v['oversold_v2_tp_pct'] / 100)
    sold, value, why, worst, best = 0.0, 0.0, 'time', average, average
    for j in range(i + 1, i + hold + 1):
        _, o, h, lo, c = daily[j][:5]
        worst, best = min(worst, lo), max(best, h)
        if lo <= stop:
            value, sold, why = value + (qty - sold) * min(o, stop), qty, 'stop'
            break
        if sold == 0 and h >= half_px:
            value, sold = value + qty / 2 * max(o, half_px), qty / 2
        if sold and h >= all_px:
            value, sold, why = value + (qty - sold) * max(o, all_px), qty, 'target'
            break
    else:
        j = i + hold
    if sold < qty:
        value += (qty - sold) * daily[j][4]
    return {**_result(value / qty, average, why, j - i, worst, best, COST.get(kind, 0.2)), 'added': len(lots) == 2,
            'rule_version': V2_RULE_VERSION, 'settings': {k: v[k] for k in v if k.startswith('oversold_v2_')}}


def run(daily, i, kind, atr_pct=None, name='close_5d'):
    """One strategy on one case; {'ret', 'exit', 'days', 'mae', 'mfe'} in %, {'pending': True} without enough bars.
    Every strategy is out by the close of day i + hold (a next-open entry buys at the open of day i + 1)."""
    _, entry_at, hold, target, stop = STRATEGIES[name]
    last = i + hold
    if last >= len(daily):
        return {'pending': True}
    entry = daily[i + 1][1] if entry_at == 'next_open' else daily[i][4]
    low, prev = daily[i][3], daily[i - 1][4]
    stop_px = low * 0.95 if stop == 'low5' else low * (1 - atr_pct / 100) if stop == 'atr' and atr_pct else None
    tp_px = prev if target == 'gap' else entry * (1 + target / 100) if target else None
    if tp_px is not None and tp_px <= entry:
        tp_px = None  # already above the target at the entry: held for time
    worst = best = entry
    cost = COST.get(kind, 0.2)
    for j in range(i + 1, last + 1):
        _, o, h, lo, c = daily[j][:5]
        worst, best = min(worst, lo), max(best, h)
        if stop_px and lo <= stop_px:
            return _result(min(o, stop_px), entry, 'stop', j - i, worst, best, cost)
        if tp_px and h >= tp_px:
            return _result(max(o, tp_px), entry, 'target', j - i, worst, best, cost)
    return _result(daily[last][4], entry, 'time', hold, worst, best, cost)


def _result(price, entry, why, days, worst, best, cost):
    return {'ret': round((price / entry - 1) * 100 - cost, 2), 'exit': why, 'days': days,
            'mae': round((worst / entry - 1) * 100, 2), 'mfe': round((best / entry - 1) * 100, 2)}


def replay(daily, i, kind, atr_pct=None):
    """Every strategy on case day i, and the paper runners' 선별 v2 plan."""
    return {**{name: run(daily, i, kind, atr_pct, name) for name in STRATEGIES}, 'v2_plan': run_v2(daily, i, kind)}


KR_KINDS = ('stock', 'etf', 'lev', 'kr_index')


INDEX_MARKET = {'^KS11': 'kospi', '^KQ11': 'kosdaq'}


def market_days(cases):
    """{day: {'kospi': case, 'kosdaq': case}} of the index falls — the market each KRX case is judged against."""
    out = {}
    for c in cases:
        if c['kind'] == 'kr_index' and c['code'] in INDEX_MARKET:
            out.setdefault(c['day'], {})[INDEX_MARKET[c['code']]] = c
    return out


def market_crash(market, settings=None):
    """선별 v2's market condition on an index case (the live gate's oversold.v2_gate rule, oversold_v2_* settings)."""
    from strategy_settings import DEFAULTS
    v = {**DEFAULTS, **(settings or {})}
    drop, prior = market['drop_pct'], market.get('prior_5d')
    crash = drop <= -v['oversold_v2_index_drop_pct'] or (
        drop <= -v['oversold_v2_capit_drop_pct'] and prior is not None and prior <= -v['oversold_v2_prior5_pct'])
    return crash and market.get('nasdaq_prev') is not None and market['nasdaq_prev'] <= 0


def gate_v2(case, markets, settings=None):
    """선별 v2 (hypothesis from the 2026-05~11 replay, held in the 10 years before too; NOT a trading rule): a KRX
    fall worth buying is a market crash — the index down 8% or more, or 5% or more after already falling 5% over the
    previous 5 sessions — that did not follow a rising Nasdaq night, and the case did not fall 4%p more than the market.
    None for non-KRX cases and days without a market case."""
    if case['kind'] not in KR_KINDS:
        return None
    import oversold_cases
    name = INDEX_MARKET.get(case['code']) if case['kind'] == 'kr_index' else oversold_cases.market_of_code(case['code'])
    market = (markets.get(case['day']) or {}).get(name)
    if market is None:
        return False
    if case['kind'] == 'kr_index':
        return bool(market_crash(market, settings))
    from strategy_settings import DEFAULTS
    rel = case['drop_pct'] - market['drop_pct'] * (2 if case['kind'] == 'lev' else 1)
    return bool(market_crash(market, settings) and rel > -{**DEFAULTS, **(settings or {})}['oversold_v2_rel_max_pct'])


SAMPLES_FROM = '2026-11-14'  # 선별 v2 went into paper trading after 2026-11-13; new samples count from here


def v2_samples(cases, since=SAMPLES_FROM, settings=None):
    """One row per KRX market fall day since `since` (newest first), kept apart from trading (user 2026-11-13):
    the market facts, whether 선별 v2 passed, and what followed (the index and the large caps that passed, close to
    5 days later; None while the 5 days are not over)."""
    import oversold_cases
    markets = market_days(cases)
    stocks = defaultdict(list)
    for c in cases:
        if c['kind'] == 'stock' and c['day'] >= since and gate_v2(c, markets, settings):
            value = ((c.get('replay') or {}).get('close_5d') or {}).get('ret')
            if value is not None:
                stocks[(c['day'], oversold_cases.market_of_code(c['code']))].append(value)
    rows = []
    for day, both in sorted(markets.items(), reverse=True):
        if day < since:
            continue
        for name, m in sorted(both.items()):
            got = stocks.get((day, name), [])
            rows.append({'day': day, 'market': m['name'], 'drop_pct': m['drop_pct'], 'prior_5d': m.get('prior_5d'),
                         'nasdaq_prev': m.get('nasdaq_prev'), 'passed': bool(market_crash(m, settings)),
                         'index_5d': ((m.get('replay') or {}).get('close_5d') or {}).get('ret'),
                         'stocks_5d': round(statistics.mean(got), 2) if got else None, 'stocks': len(got)})
    return rows


def table(cases, by_day=True):
    """Per strategy over `cases` (with 'replay'): n, per-day mean (cases of one day averaged first, so a crash day
    with 60 stocks counts once), mean, median, win %, worst day, mean worst point while held."""
    rows = []
    for name in ['now_rule', *STRATEGIES, 'v2_plan']:
        found = []
        for c in cases:
            r = ((c.get('intraday') or {}).get('rule') if name == 'now_rule' else (c.get('replay') or {}).get(name)) or {}
            value = r.get('return_pct', r.get('ret'))
            if value is not None and not r.get('pending'):
                found.append((c['day'], value, r.get('mae', r.get('adverse_pct'))))
        if not found:
            continue
        days = defaultdict(list)
        for day, value, _ in found:
            days[day].append(value)
        daily = [statistics.mean(v) for v in days.values()]
        values = [v for _, v, _ in found]
        maes = [m for _, _, m in found if m is not None]
        rows.append({'strategy': name, 'label': LABELS[name], 'n': len(values), 'days': len(days),
                     'day_mean': round(statistics.mean(daily), 2), 'day_win': round(sum(d > 0 for d in daily) / len(daily) * 100),
                     'mean': round(statistics.mean(values), 2), 'median': round(statistics.median(values), 2),
                     'win': round(sum(v > 0 for v in values) / len(values) * 100), 'worst_day': round(min(daily), 2),
                     'mae': round(statistics.mean(maes), 2) if maes else None})
    return rows
