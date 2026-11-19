"""여운봉 저수지 매매법 as a book strategy (user request 2026-11-18, docs/yeo-reservoir-method-plan.md; 0절 overrides 4-4).

The book starts with everything in the reservoir (cash, or KOFR 449170 waiting) and holds nothing. When the index falls from
its all-time high (^NDX: −20/−30/−40%), it spends 40/40/20% of the reservoir it had when step 1 came, split risk : safe
= 70 : 30 (379810 KODEX 미국나스닥100 : 0261H0 KODEX 미국나스닥100미국채혼합50). A new all-time high re-arms the steps; what was
bought is never sold to refill the reservoir. Once something is held it is put back to 70 : 30 at every month's and every
quarter's first check (user 2026-11-19: both, not a choice) and at once when the risk weight is 5%p or more off. Every number is a setting (clean()). The
markets / indices other than the US Nasdaq-100 are choices kept off for now (KOSPI ^KS11 −30/−45/−55% → 30/40/30%).
plan() is pure: the engine (virtual book) and longterm_live (a live account) send its orders through their usual paths."""
from __future__ import annotations

from datetime import date

# asset keys outside the engine's six ETFs (vibe_longterm.ASSETS): key -> (KRX code, name)
EXTRA = {'NDXMIX': ('0261H0', 'KODEX 미국나스닥100미국채혼합50'), 'SPXMIX': ('438080', 'ACE 미국S&P500미국채혼합50액티브'),
         'K200': ('069500', 'KODEX 200'), 'K200MIX': ('183700', 'RISE 200채권혼합50')}
# index -> (risk key, safe key, Yahoo symbol for the all-time high, label)
INDEX = {'ndx': ('NDX', 'NDXMIX', '^NDX', '나스닥100'), 'spx': ('SPX', 'SPXMIX', '^GSPC', 'S&P500'), 'kospi': ('K200', 'K200MIX', '^KS11', '코스피')}
MARKETS = {'us': '미국 지수만', 'kospi': '코스피만', 'mix': '혼합'}
US_INDEX = {'ndx': '나스닥100만', 'spx': 'S&P500만', 'both': '둘 다'}
RESERVOIR = 'RES'
DEFAULT = {'market': 'us', 'us_index': 'ndx', 'mix_us': 0.70, 'mix_ndx': 0.50, 'risk': 0.70, 'band': 0.05,
           'steps': [[-0.20, 0.40], [-0.30, 0.40], [-0.40, 0.20]], 'kospi_steps': [[-0.30, 0.30], [-0.45, 0.40], [-0.55, 0.30]]}
RANGES = {'mix_us': (0.0, 1.0), 'mix_ndx': (0.0, 1.0), 'risk': (0.50, 0.90), 'band': (0.02, 0.10)}
STEP_DD, STEP_MAX, MIN_REBALANCE = (-0.60, -0.05), 3, 0.005
# scripts/yeo_method_replay.py (2026-11-18): the rule from all cash vs 70 : 30 bought on day one vs the index held (연수익 · MDD).
# KRX = 379810 closes with 0261H0 approximated (50% 379810 + 50% US T-bills in KRW) and the KOFR proxy 2.5%; fees, no tax.
RESEARCH = ('KRX 2021-04~ 연 13.5%·MDD −20.0% (70:30 즉시 15.6%·−25.7%, 보유 16.8%·−31.0%) · '
            '2021-11 고점에서 시작 15.0%·−20.0% (12.7%·−25.7%, 13.4%·−31.0%) · '
            '^NDX 2015~ 10.8%·−25.6% (14.9%·−30.6%, 16.9%·−35.6%) · ^NDX 2000~ 7.7%·−71.5% (7.2%·−76.2%, 7.6%·−82.9%). '
            '큰 하락이 든 구간은 낙폭이 가장 작았고, 상승이 이어진 구간은 현금 대기로 수익이 낮았다.')


def clean(cfg):
    """A complete setting kept to its ranges (out of range: the nearest edge; unknown: the default)."""
    cfg = cfg if isinstance(cfg, dict) else {}
    num = lambda v: type(v) in (int, float) and v == v  # noqa: E731
    out = {k: (list(map(list, v)) if isinstance(v, list) else v) for k, v in DEFAULT.items()}
    for key, choices in (('market', MARKETS), ('us_index', US_INDEX)):
        if cfg.get(key) in choices:
            out[key] = cfg[key]
    for key, (lo, hi) in RANGES.items():
        if num(cfg.get(key)):
            out[key] = round(min(hi, max(lo, float(cfg[key]))), 4)
    for key in ('steps', 'kospi_steps'):
        if isinstance(cfg.get(key), list):
            rows = {}
            for row in cfg[key]:
                if len(rows) < STEP_MAX and isinstance(row, (list, tuple)) and len(row) == 2 and num(row[0]) and num(row[1]):
                    rows.setdefault(round(min(STEP_DD[1], max(STEP_DD[0], float(row[0]))), 4), round(min(1.0, max(0.0, float(row[1]))), 4))
            out[key] = [[dd, s] for dd, s in sorted(rows.items(), reverse=True)]
    return out


def pairs(cfg):
    """[(index, risk key, safe key, symbol, steps, share of the reservoir)] the setting trades."""
    cfg = clean(cfg)
    us = {'ndx': [('ndx', 1.0)], 'spx': [('spx', 1.0)], 'both': [('ndx', cfg['mix_ndx']), ('spx', 1 - cfg['mix_ndx'])]}[cfg['us_index']]
    chosen = (us if cfg['market'] == 'us' else [('kospi', 1.0)] if cfg['market'] == 'kospi'
              else [(i, s * cfg['mix_us']) for i, s in us] + [('kospi', 1 - cfg['mix_us'])])
    return [(i, INDEX[i][0], INDEX[i][1], INDEX[i][2], cfg['kospi_steps'] if i == 'kospi' else cfg['steps'], share) for i, share in chosen if share > 0]


def keys(cfg):
    """The asset keys the setting may hold (its risk and safe ETFs)."""
    return [k for _, risk, safe, *_ in pairs(cfg) for k in (risk, safe)]


def period(day):
    """The rebalance period: every month (a quarter's first month is also that quarter's first check)."""
    return date.fromisoformat(day).strftime('%Y-%m')


def periodic_label(day):
    return '분기·매월' if date.fromisoformat(day).month in (1, 4, 7, 10) else '매월'


def pct(v):
    return f'{v * 100:+.1f}%'.replace('-', '−').replace('+', '')


def step_buys(state, day):
    """{asset key: KRW} the day's steps planned (plan() keeps it per index): what an unfilled buy may be owed for."""
    out = {}
    for s in (state or {}).values():
        if isinstance(s, dict) and (got := s.get('step_buy') or {}).get('day') == day:
            for k, krw in got.items():
                if k != 'day':
                    out[k] = out.get(k, 0) + krw
    return out


def unbought(rows, fee=0.00015, steps=None):
    """{asset key: KRW, fee included} of buy orders that did not fill ([{key, side, qty, filled, price}]): a step's leg
    that was refused, cut or cancelled unfilled, bought first the next day (owed). Only up to that day's step buys
    (step_buys()): a rebalance leg is paid by its own sale, so an unfilled one is not bought from the reservoir later."""
    out = {}
    for o in rows or []:
        if o.get('side') == 'BUY' and (left := o.get('qty', 0) - (o.get('filled') or 0)) > 0:
            out[o['key']] = out.get(o['key'], 0) + left * o['price'] * (1 + fee)
    return {k: min(v, steps.get(k, 0)) for k, v in out.items() if steps.get(k, 0) >= 1} if steps is not None else out


def switch_note(cfg):
    """What switching a book that already holds ETFs to the method does at its next check (review5: the screen must warn)."""
    cfg = clean(cfg)
    legs = [(INDEX[i][3], EXTRA.get(safe, (safe, safe))[1]) for i, risk, safe, *_ in pairs(cfg)]
    return ('이 계좌가 이미 ' + ', '.join(f'{name} ETF나 {mix}' for name, mix in legs) + ' 중 하나라도 갖고 있으면, 다음 주문 계획(평일 09:40)에서 '
            f"위험:안전 {cfg['risk'] * 100:.0f} : {(1 - cfg['risk']) * 100:.0f}으로 맞추려고 한쪽을 팔고 다른 쪽을 삽니다. "
            '이 방법이 다루지 않는 다른 ETF는 팔지 않고 그대로 둡니다.')


def plan(cfg, state, ath, values, cash, day, owed=None):
    """(planned {asset key: signed KRW}, new state, Korean notes) for one book on one day.
    ath: {index: {'dd': drawdown from the all-time high, 'peak'}}; values: KRW held per asset key (the reservoir RES too);
    cash: money the book may spend. State per index: used steps, the step-1 reservoir (base), the last rebalance period.
    owed: {asset key: KRW} an earlier step did not buy (unbought()), bought first from the reservoir and counted as held."""
    cfg = clean(cfg)
    state = {i: dict(v) for i, v in (state or {}).items() if isinstance(v, dict)}
    planned, notes = {}, []
    add = lambda k, v: planned.__setitem__(k, planned.get(k, 0) + v)  # noqa: E731
    reservoir = max(0.0, cash) + values.get(RESERVOIR, 0)
    bought = {}  # per index: each index spends at most its own share of the reservoir
    values = dict(values)
    for k, krw in (owed or {}).items():  # a step's leg that did not fill: the step stays used, its money goes in now
        if (krw := min(krw, reservoir - sum(bought.values()))) >= 1:
            add(k, krw)
            bought['owed'] = bought.get('owed', 0.0) + krw
            values[k] = values.get(k, 0) + krw  # the rebalance below sees it as held
            notes.append(f"지난 단계에서 사지 못한 몫 다시 삼: {EXTRA[k][1] if k in EXTRA else k} {krw:,.0f}원")
    for index, risk, safe, symbol, steps, share in pairs(cfg):
        s = state.setdefault(index, {})
        s.setdefault('used', [])
        s.setdefault('base', None)
        dd = ((ath or {}).get(index) or {}).get('dd')
        if type(dd) not in (int, float):
            notes.append(f'{symbol} 전고점 자료가 없어 오늘 단계 판단을 하지 않음')
            continue
        if dd >= -0.001 and (s['used'] or s['base'] is not None):
            s['used'], s['base'] = [], None
            notes.append(f'{symbol} 전고점 회복: 단계 재설정(산 몫은 팔지 않음, 남은 저수지가 다음 하락의 기준)')
        stepped = False
        for n, (level, use) in enumerate(steps, 1):
            if dd <= level and level not in s['used']:
                if s['base'] is None:
                    s['base'] = reservoir * share
                krw = min(s['base'] * use, max(0.0, reservoir * share - bought.get(index, 0.0)))
                s['used'].append(level)
                stepped = True
                if krw >= 1:
                    add(risk, krw * cfg['risk'])
                    add(safe, krw * (1 - cfg['risk']))
                    bought[index] = bought.get(index, 0.0) + krw
                    sb = s['step_buy'] if (s.get('step_buy') or {}).get('day') == day else {'day': day}
                    sb[risk] = sb.get(risk, 0) + krw * cfg['risk']
                    sb[safe] = sb.get(safe, 0) + krw * (1 - cfg['risk'])
                    s['step_buy'] = sb
                notes.append(f"여운봉 단계 {n}: {symbol} 전고점 대비 {pct(dd)}, 저수지 {use:.0%} 사용({krw:,.0f}원), "
                             f"{cfg['risk'] * 100:.0f}:{(1 - cfg['risk']) * 100:.0f}")
        held = values.get(risk, 0) + values.get(safe, 0)
        now = period(day)
        if stepped:
            s['rebalanced'] = now  # the step bought at the target split
        elif held > 0:
            weight = values.get(risk, 0) / held
            off = abs(weight - cfg['risk']) >= cfg['band'] - 1e-9
            due = s.get('rebalanced') != now
            if off or due:
                diff = cfg['risk'] * held - values.get(risk, 0)
                s['rebalanced'] = now
                if abs(diff) >= MIN_REBALANCE * held:
                    add(risk, diff)
                    add(safe, -diff)
                    notes.append(f"리밸런싱: {'즉시(' + format(cfg['band'] * 100, '.0f') + '%p 이상 이탈)' if off else '정기(' + periodic_label(day) + ')'}, "
                                 f"위험 {weight:.0%} → {cfg['risk']:.0%}")
        nxt = next((level for level, _ in steps if level not in s['used']), None)
        if not stepped and nxt is not None and dd > nxt:
            notes.append(f'{symbol} 전고점 대비 {pct(dd)} · 다음 단계 {pct(nxt)}까지 {(dd - nxt) * 100:.1f}%p 남음'
                         + ('(그 전에는 사지 않음)' if not held else ''))
    if (total := sum(bought.values())) > max(0.0, cash):  # the cash runs short: the KOFR reservoir pays the rest
        add(RESERVOIR, -(total - max(0.0, cash)))
    return {k: v for k, v in planned.items() if abs(v) >= 1}, state, notes


def share_plan(cfg, reservoir, prices, fee=0.00015):
    """[{level, use, krw, risk key: {krw, qty}, safe key: {krw, qty}}] per step of the first index: what each step would buy
    with this reservoir at these prices (whole shares, the fee on top)."""
    cfg = clean(cfg)
    index, risk, safe, symbol, steps, share = pairs(cfg)[0]
    out = []
    for level, use in steps:
        krw = round(reservoir * share * use)
        row = {'level': level, 'use': use, 'krw': krw}
        for key, part in ((risk, cfg['risk']), (safe, 1 - cfg['risk'])):
            price = (prices or {}).get(key)
            row[key] = {'krw': round(krw * part), 'qty': int(krw * part // (price * (1 + fee))) if price else None, 'price': price}
        out.append(row)
    return out
