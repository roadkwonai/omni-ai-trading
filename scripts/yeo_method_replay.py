"""여운봉 저수지 매매법 replay (docs/yeo-reservoir-method-plan.md 8장): the real rule (yeo_method.plan) every day on
  · KRX: KODEX 미국나스닥100 (379810.KS) closes from its listing; 0261H0 (listed 2026-11-10) approximated as 50% 379810 +
    50% US T-bills (^IRX, in KRW with USDKRW) rebalanced daily; the reservoir earns the KOFR proxy (2.5% a year);
  · a long proxy: ^NDX (USD) with the same 50 : 50 T-bill mix, from 2000 / 2007 / 2015;
starting all in the reservoir (nothing held), steps from ^NDX's all-time high (all history up to that day), orders at the
day's close with fee + slippage, no tax. Compared with 70 : 30 bought on day one (same rebalancing) and the index held.
    PYTHONPATH=agent:scripts .venv/bin/python scripts/yeo_method_replay.py [--json]
"""
from __future__ import annotations

import argparse
import json
from datetime import date

import yeo_method as Y
from vibe_longterm_replay import fetch, max_drawdown

FEE, SLIP, CASH, START = 0.00015, 0.0005, 0.025, 100_000_000  # a book of 1억 원 (the rule drops orders under 1 KRW)


def panel(krx=True):
    """{day: {'risk', 'safe', 'dd'}} with risk/safe as daily returns-compounded prices and dd from ^NDX's running all-time high."""
    ndx = fetch('^NDX', 40)
    irx = fetch('^IRX', 40)
    fx = fetch('KRW=X', 40) if krx else {}
    risk_px = fetch('379810.KS', 10) if krx else ndx
    days = sorted(d for d in risk_px if d in ndx or krx)
    out, peak, safe, prev, last = {}, 0.0, 1.0, None, {'ndx': None, 'irx': 5.0, 'fx': None}
    nd = sorted(ndx)
    i = 0
    for d in days:
        while i < len(nd) and nd[i] <= d:  # ^NDX and its running all-time high up to this day (US close before the KRX day)
            last['ndx'] = ndx[nd[i]]
            peak = max(peak, ndx[nd[i]])
            i += 1
        last['irx'] = irx.get(d, last['irx'])
        last['fx'] = fx.get(d, last['fx']) if krx else 1.0
        if last['ndx'] is None or last['fx'] is None:
            continue
        r = risk_px[d]
        if prev is not None:
            bill = (1 + last['irx'] / 100 / 252) * (last['fx'] / prev['fx'] if krx else 1.0) - 1
            safe *= 1 + 0.5 * (r / prev['r'] - 1) + 0.5 * bill
        prev = {'r': r, 'fx': last['fx']}
        out[d] = {'risk': r, 'safe': safe, 'dd': last['ndx'] / peak - 1, 'ndx': last['ndx']}
    return out


def run(data, start, way='method', cfg=None):
    """One book of 1 from `start`: way 'method' (all cash, the steps), 'split' (70 : 30 on day one, then the same rebalance),
    'hold' (the risk ETF on day one)."""
    cfg = Y.clean(cfg)
    days = [d for d in sorted(data) if d >= start]
    cash, qty, state, values, events = float(START), {'NDX': 0.0, 'NDXMIX': 0.0}, {}, [], []
    px = lambda d: {'NDX': data[d]['risk'], 'NDXMIX': data[d]['safe']}  # noqa: E731
    for n, d in enumerate(days):
        p = px(d)
        if n:
            cash *= 1 + CASH / 252
        if n == 0 and way in ('split', 'hold'):
            share = cfg['risk'] if way == 'split' else 1.0
            qty['NDX'] = START * share / (p['NDX'] * (1 + FEE + SLIP))
            qty['NDXMIX'] = START * (1 - share) / (p['NDXMIX'] * (1 + FEE + SLIP))
            cash = 0.0
        if way != 'hold':
            held = {k: qty[k] * p[k] for k in qty}
            plan, state, notes = Y.plan(cfg, state if way == 'method' else {**state, 'ndx': {**state.get('ndx', {}), 'used': [s for s, _ in cfg['steps']]}},
                                        {'ndx': {'dd': data[d]['dd']}}, held, cash, d.isoformat())
            for k, krw in sorted(plan.items(), key=lambda kv: kv[1]):
                if k == 'RES':
                    continue
                if krw < 0:
                    q = min(qty[k], -krw / p[k])
                    qty[k] -= q
                    cash += q * p[k] * (1 - FEE - SLIP)
                else:
                    krw = min(krw, cash)
                    qty[k] += krw / (p[k] * (1 + FEE + SLIP))
                    cash -= krw
            events += [f'{d} {t}' for t in notes if t.startswith(('여운봉 단계', '리밸런싱'))]
        values.append(cash + sum(qty[k] * p[k] for k in qty))
    years = (days[-1] - days[0]).days / 365.25
    return {'start': days[0].isoformat(), 'end': days[-1].isoformat(), 'way': way, 'cagr': (values[-1] / START) ** (1 / years) - 1,
            'mdd': max_drawdown(values), 'multiple': values[-1] / START, 'steps': [e for e in events if '단계' in e],
            'rebalances': sum('리밸런싱' in e for e in events)}


# ---------- 적립식 재설계 v2 (docs/longterm-dca-steady-plan.md 10장, proposal; research only) ----------
# The 여운봉 book with money coming in every month: 'alone' leaves new money in the reservoir for the steps; 'now' buys each
# contribution at 70 : 30 the day it lands (today's 적립식 when it follows the strategy); 'steady' buys rate x book value
# every week at 70 : 30 while the cash stays above floor x book value, the steps spending the rest; 'split' starts at
# 70 : 30 and buys each contribution at once (no reservoir). Money-weighted return (XIRR), MDD of the book value.

def run_dca(data, start, way='alone', rate=0.005, floor=0.4, monthly=1_000_000, cfg=None):
    from longterm_dca_replay import xirr
    cfg = Y.clean(cfg)
    days = [d for d in sorted(data) if d >= start]
    cash, qty, state, values, flows, month, week, ratios, cash_share = float(START), {'NDX': 0.0, 'NDXMIX': 0.0}, {}, [], [], None, None, [], []
    paid = float(START)
    flows.append((days[0], -START))
    px = lambda d: {'NDX': data[d]['risk'], 'NDXMIX': data[d]['safe']}  # noqa: E731

    def buy(k, krw, p):
        nonlocal cash
        krw = min(krw, cash)
        if krw >= 1:
            qty[k] += krw / (p[k] * (1 + FEE + SLIP))
            cash -= krw

    for n, d in enumerate(days):
        p = px(d)
        if n:
            cash *= 1 + CASH / 252
        if n == 0 and way == 'split':
            buy('NDX', START * cfg['risk'], p)
            buy('NDXMIX', START * (1 - cfg['risk']), p)
        if (d.year, d.month) != month:
            if month is not None:
                cash += monthly
                paid += monthly
                flows.append((d, -monthly))
                if way in ('now', 'split'):
                    buy('NDX', monthly * cfg['risk'], p)
                    buy('NDXMIX', monthly * (1 - cfg['risk']), p)
            month = (d.year, d.month)
        value = cash + sum(qty[k] * p[k] for k in qty)
        if way == 'steady' and d.isocalendar()[:2] != week:
            week = d.isocalendar()[:2]
            spend = min(rate * value, max(0.0, cash - floor * value))
            buy('NDX', spend * cfg['risk'], p)
            buy('NDXMIX', spend * (1 - cfg['risk']), p)
        held = {k: qty[k] * p[k] for k in qty}
        steps = {**state, 'ndx': {**state.get('ndx', {}), 'used': [s for s, _ in cfg['steps']]}} if way == 'split' else state
        plan, state, _ = Y.plan(cfg, steps, {'ndx': {'dd': data[d]['dd']}}, held, cash, d.isoformat())
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
    return {'start': days[0].isoformat(), 'way': way, 'rate': rate, 'floor': floor, 'xirr': xirr(flows), 'mdd': max_drawdown(values),
            'worst_vs_paid': min(ratios) - 1, 'avg_cash': sum(cash_share) / len(cash_share), 'paid': paid, 'final': values[-1]}


DCA_WAYS = (('alone', 0, 0), ('now', 0, 0), ('split', 0, 0), ('steady', 0.005, 0.4), ('steady', 0.005, 0.3), ('steady', 0.01, 0.4),
            ('steady', 0.01, 0.3), ('steady', 0.0025, 0.5))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--dca', action='store_true', help='적립식 재설계 v2: the 여운봉 book with 100만 원 a month (steady vs alone vs now)')
    args = parser.parse_args()
    out = {}
    if args.dca:
        for name, data, starts in (('KRX', panel(True), (date(2021, 4, 12), date(2021, 11, 22), date(2023, 1, 2))),
                                   ('^NDX', panel(False), (date(2000, 1, 3), date(2007, 1, 3), date(2015, 1, 2)))):
            for start in starts:
                print(f'## {name} {start} (1억 시작 + 매월 100만 원)')
                for way, rate, floor in DCA_WAYS:
                    r = run_dca(data, start, way, rate, floor)
                    tag = f'{way} 주 {rate:.2%} · 현금 하한 {floor:.0%}' if way == 'steady' else way
                    print(f"  {tag:28} XIRR {r['xirr'] * 100:6.2f}% · MDD {r['mdd'] * 100:6.1f}% · 넣은 돈 대비 최악 {r['worst_vs_paid'] * 100:6.1f}% · "
                          f"평균 현금 {r['avg_cash'] * 100:5.1f}%")
        return
    krx = panel(True)
    for start in (date(2021, 4, 12), date(2021, 11, 22), date(2023, 1, 2)):
        out[f'KRX {start}'] = [run(krx, start, w) for w in ('method', 'split', 'hold')]
    proxy = panel(False)
    for start in (date(2000, 1, 3), date(2007, 1, 3), date(2015, 1, 2)):
        out[f'^NDX {start}'] = [run(proxy, start, w) for w in ('method', 'split', 'hold')]
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    for name, rows in out.items():
        print(f'## {name}')
        for r in rows:
            print(f"  {r['way']:7} {r['cagr'] * 100:6.2f}% · MDD {r['mdd'] * 100:6.1f}% · x{r['multiple']:.2f} · 리밸런싱 {r['rebalances']}회 · "
                  + ('; '.join(r['steps'][:4]) or '단계 없음'))


if __name__ == '__main__':
    main()
