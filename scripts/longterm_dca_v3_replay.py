"""적립식 v3 V1 재현 (docs/longterm-dca-v3-plan.md 5장, research only: longterm_dca is not changed).

The proposed rule (3-2·3-3): every week one buy at most; the master picks the day (each period's first trading day, or
the first down day with a maximum wait, or a crash day) and the size: base = rate x the book's value, times the master's
regime multiplier (0.5~1.5, a crash day x1.5 for 윌리엄스), plus 1/8 of the catch-up budget, at most 1.5 x base.
  · a module book (the engine pipeline, longterm_dca_replay.run_engine): the 적립식 spends only the contributions it holds
    (pool); the engine deploys the starting money and keeps its own reservoir (KOFR), so no cash floor here.
  · a 여운봉 book (yeo_method_replay data): it spends the cash down to the master's floor x the book's value; the method's
    −20/−30/−40% steps spend the rest; the regime comes from ^NDX and ^VIX with the engine's raw_regime + confirm.
Money: 1억 at the start + 100만 원 a month. Results are one past (mostly a bull market), not a forecast.
    PYTHONPATH=agent:scripts .venv/bin/python scripts/longterm_dca_v3_replay.py [--json]
"""
from __future__ import annotations

import argparse
import json
from datetime import date

REGIMES = ('bull', 'recovery', 'correction', 'bear', 'panic', 'overheat')
_m = lambda *v: dict(zip(REGIMES, v))  # noqa: E731
# docs/longterm-dca-v3-plan.md 3-3: (label, multipliers, when, max wait days, cash floor, crash boost)
MASTERS = {
    'bogle': ('존 보글·워런 버핏', _m(1, 1, 1, 1, 1, 1), 'cycle', 0, 0.10, False),
    'dalio': ('레이 달리오', _m(1, 1, 1, 1, 1, 0.9), 'cycle', 0, 0.15, False),
    'marks': ('하워드 막스', _m(0.9, 1, 1.2, 1.3, 1.4, 0.6), 'dip', 5, 0.20, False),
    'yeo': ('여운봉(저수지 관점)', _m(0.8, 1, 1.2, 1.4, 1.5, 0.5), 'dip', 7, 0.30, False),
    'williams': ('래리 윌리엄스', _m(1, 1, 1.1, 1.1, 1.2, 0.9), 'dip', 5, 0.20, True),
}
RATES = (0.001, 0.0025, 0.005)  # base weekly share of the book's value
WEAK = ('correction', 'bear', 'panic')


def v3_spend(cfg, state, day, day_ret, vix, regime, value, avail):
    """KRW the rule spends today (0 on a day it waits), recording each buy in state['log']."""
    week = day.isocalendar()[:2]
    if state.get('week') == week:
        return 0.0
    state.setdefault('since', day)  # the wait for a down day runs from the day after the last buy, across weeks
    crash = cfg['crash'] and day_ret is not None and day_ret <= -0.03 and (vix or 0) >= 25
    down = day_ret is not None and day_ret < 0
    waited = (day - state['since']).days >= cfg['wait']
    if cfg['when'] == 'dip' and not (down or crash or waited):
        return 0.0
    base = cfg['rate'] * value
    mult = cfg['mult'].get(regime, 1.0) * (1.5 if crash else 1.0)
    today = base * mult
    catch = state.get('catch', 0.0)
    add = catch / 8 if today < 1.5 * base else 0.0
    spend = max(0.0, min(today + add, 1.5 * base, avail))
    state['catch'] = max(0.0, catch - add + max(0.0, base - today))
    state['week'] = week
    state.pop('since', None)
    if spend >= 1_000:
        state.setdefault('log', []).append((day.isoformat(), regime, round(spend), round(mult, 2)))
    return spend if spend >= 1_000 else 0.0


def module_fn(cfg, state, day, ind, regime, want, value, cash):
    """run_engine's steady hook: spend from the 적립식's pool at the engine's targets without KOFR."""
    spend = v3_spend(cfg, state, day, ind.get('day_ret'), ind.get('vix'), regime, value, min(state.get('pool', 0.0), cash))
    weights = {k: w for k, w in want.items() if k != 'RES' and w > 0}
    total = sum(weights.values())
    if not spend or not total:
        return {}
    state['pool'] -= spend
    return {k: spend * w / total for k, w in weights.items()}


def behaviour(buys, contributed):
    """How the master bought: number, mean and spread of a buy, the share of all bought in a weak regime."""
    if not buys:
        return {'n': 0, 'avg': 0, 'min': 0, 'max': 0, 'weak_share': 0.0, 'avg_mult': 0.0}
    amounts = [b[2] for b in buys]
    return {'n': len(buys), 'avg': sum(amounts) / len(amounts), 'min': min(amounts), 'max': max(amounts),
            'weak_share': sum(b[2] for b in buys if b[1] in WEAK) / sum(amounts), 'avg_mult': sum(b[3] for b in buys) / len(buys)}


def module_rows(panel, start=None, end=None):
    import longterm_dca_replay as R
    flat = {'label': '바로(비교: 적립금 전액)', 'mult': dict.fromkeys(REGIMES, 1.0), 'when': 'cycle', 'wait': 0, 'crash': False, 'rate': 1.0,
            'pool': True, 'fn': module_fn}  # every week the whole pool, KOFR left out as v2 A: the v2 바로 on this book
    rows = [{**R.run_engine(panel, 'v3_now', start, end, initial=1e8, steady=flat, modules=('bogle_core', 'dalio_rebalance', 'buffett_lowturn')),
             'label': '지금(v2) · 바로', 'rate': None, 'master': 'now'}]
    for rate in RATES:
        for key, (label, mult, when, wait, _, crash) in MASTERS.items():
            cfg = {'label': label, 'mult': mult, 'when': when, 'wait': wait, 'crash': crash, 'rate': rate, 'pool': True, 'fn': module_fn}
            r = R.run_engine(panel, f'v3_{key}', start, end, initial=1e8, steady=cfg,
                             modules=('bogle_core', 'dalio_rebalance', 'buffett_lowturn'))
            rows.append({**r, 'label': label, 'rate': rate, 'master': key})
    return rows


def with_regimes(data):
    """data[d]['regime'], ['day_ret'], ['vix'] from the full ^NDX and ^VIX history (252-day high, 200-day line and its
    20-day slope; the engine's raw_regime + confirm), the last US close on or before each day of `data`."""
    import vibe_longterm as L
    from vibe_longterm_replay import fetch
    ndx, vix = fetch('^NDX', 40), fetch('^VIX', 40)
    us = sorted(ndx)
    closes, rs, last_vix, i = [], None, 20.0, 0
    for d in sorted(data):
        while i < len(us) and us[i] <= d:
            closes.append(ndx[us[i]])
            last_vix = vix.get(us[i], last_vix)
            if len(closes) >= 221:
                sma, before = sum(closes[-200:]) / 200, sum(closes[-220:-20]) / 200
                ind = {'dd': closes[-1] / max(closes[-252:]) - 1, 'gap': closes[-1] / sma - 1, 'slope': sma / before - 1, 'vix': last_vix}
                rs = L.confirm(rs, L.raw_regime(ind, (rs or {}).get('regime')), us[i].isoformat())
            i += 1
        data[d]['regime'] = (rs or {}).get('regime', 'bull')
        data[d]['day_ret'] = closes[-1] / closes[-2] - 1 if len(closes) > 1 else None
        data[d]['vix'] = last_vix
    return data


def yeo_run(data, start, key, rate, monthly=1_000_000):
    """One 여운봉 book with the v3 적립식 of master `key`: weekly buys at 70 : 30 down to the master's cash floor."""
    import yeo_method as Y
    from longterm_dca_replay import xirr
    from vibe_longterm_replay import max_drawdown
    from yeo_method_replay import CASH, FEE, SLIP, START
    label, mult, when, wait, floor, crash = MASTERS[key]
    cfg_y, cfg = Y.clean(None), {'mult': mult, 'when': when, 'wait': wait, 'crash': crash, 'rate': rate}
    days = [d for d in sorted(data) if d >= start]
    cash, qty, ystate, state = float(START), {'NDX': 0.0, 'NDXMIX': 0.0}, {}, {}
    values, flows, ratios, cash_share, paid, month = [], [(days[0], -START)], [], [], float(START), None

    def buy(k, krw, p):
        nonlocal cash
        krw = min(krw, cash)
        if krw >= 1:
            qty[k] += krw / (p[k] * (1 + FEE + SLIP))
            cash -= krw
    for n, d in enumerate(days):
        p = {'NDX': data[d]['risk'], 'NDXMIX': data[d]['safe']}
        if n:
            cash *= 1 + CASH / 252
        if (d.year, d.month) != month:
            if month is not None:
                cash += monthly
                paid += monthly
                flows.append((d, -monthly))
            month = (d.year, d.month)
        value = cash + sum(qty[k] * p[k] for k in qty)
        spend = v3_spend(cfg, state, d, data[d].get('day_ret'), data[d].get('vix'), data[d].get('regime', 'bull'), value,
                         cash - floor * value)
        buy('NDX', spend * cfg_y['risk'], p)
        buy('NDXMIX', spend * (1 - cfg_y['risk']), p)
        held = {k: qty[k] * p[k] for k in qty}
        plan, ystate, _ = Y.plan(cfg_y, ystate, {'ndx': {'dd': data[d]['dd']}}, held, cash, d.isoformat())
        for k, krw in sorted(plan.items(), key=lambda kv: kv[1]):
            if k == 'RES':
                continue
            if krw < 0:
                q = min(qty[k], -krw / p[k])
                qty[k] -= q
                cash += q * p[k] * (1 - FEE - SLIP)
            else:
                buy(k, krw, p)
        value = cash + sum(qty[k] * p[k] for k in qty)
        values.append(value)
        ratios.append(value / paid)
        cash_share.append(cash / value if value else 1)
    flows.append((days[-1], values[-1]))
    return {'label': label, 'master': key, 'rate': rate, 'xirr': xirr(flows), 'worst_vs_paid': min(ratios) - 1, 'mdd': max_drawdown(values),
            'avg_cash': sum(cash_share) / len(cash_share), 'buys': state.get('log', []), 'contributed': paid}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    from longterm_dca_replay import krw_panel
    import yeo_method_replay as YR
    out = {'module': {}, 'yeo': {}}
    panel = krw_panel()
    for name, span in {'2020-02~': (date(2020, 2, 3), None), '2022 약세장': (date(2021, 11, 1), date(2023, 6, 30))}.items():
        out['module'][name] = module_rows(panel, *span)
    for name, data, starts in (('KRX', YR.panel(True), (date(2021, 4, 12), date(2021, 11, 22))), ('^NDX', YR.panel(False), (date(2015, 1, 2),))):
        data = with_regimes(data)
        for start in starts:
            rows = [{**YR.run_dca(data, start, 'alone'), 'label': '지금(v2) · 적립식 꺼짐(저수지에 쌓기)', 'master': 'alone', 'rate': None, 'buys': []},
                    {**YR.run_dca(data, start, 'now'), 'label': '지금(v2) · 바로', 'master': 'now', 'rate': None, 'buys': []}]
            rows += [yeo_run(data, start, key, rate) for rate in RATES for key in MASTERS]
            out['yeo'][f'{name} {start}'] = rows
    for book in out.values():
        for rows in book.values():
            for r in rows:
                r['behaviour'] = behaviour(r.pop('buys', []), r.get('contributed') or r.get('paid'))
                for k in list(r):
                    if k not in ('label', 'master', 'rate', 'xirr', 'worst_vs_paid', 'mdd', 'avg_cash', 'behaviour'):
                        r.pop(k)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
        return
    for book, windows in out.items():
        for name, rows in windows.items():
            print(f"\n## {'거장 모듈 계좌' if book == 'module' else '여운봉 계좌'} · {name} (1억 시작 + 매월 100만 원)")
            print('거장                      주비율   XIRR    최악     MDD   평균현금  매수  평균1회(만)  최소~최대(만)  약세장몫  평균배수')
            for r in rows:
                b = r['behaviour']
                rate = '—' if r['rate'] is None else f"{r['rate']:.2%}"
                print(f"{r['label'][:22]:24} {rate:>6} {r['xirr'] * 100:6.2f}% {r['worst_vs_paid'] * 100:6.1f}% {r['mdd'] * 100:6.1f}% "
                      f"{r['avg_cash'] * 100:6.1f}% {b['n']:5} {b['avg'] / 1e4:9.1f} {b['min'] / 1e4:7.1f}~{b['max'] / 1e4:<7.1f} "
                      f"{b['weak_share'] * 100:6.1f}% {b['avg_mult']:7.2f}")


if __name__ == '__main__':
    main()
