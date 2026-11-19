"""적립식(DCA) research for the long-term engine (user request 2026-11-17). Read-only replay, no orders.

A fixed monthly contribution lands on the first US trading day of each month. Variants decide what the money buys:
  regular      every contribution buys the target weights at once (plain 적립식)
  regular_res  85% buys at once, 15% stays in the reservoir (cash at the KOFR-like rate) for drawdown tiers
  dip          contributions wait in cash; buys only on a Nasdaq-100 drawdown step (−5/−10/−20%, deeper = more)
  yeo          여운봉 style: half buys every month, half fills the reservoir that the dip rule spends
  *_tp         adds take-profit: an asset +30% over its cost while the Nasdaq-100 is 15% over its 200-day line sells a
               third of it back to the reservoir (re-armed below +15%)
Prices are KRW proxies from vibe_longterm_replay.krw_panel (US ETF adjusted close × USDKRW); fills at the next close
with fee + slippage, realized gains taxed 15.4%. Results say what happened in the past ten years, not what will.
"""
from __future__ import annotations

import argparse
import json
from datetime import date


from vibe_longterm_replay import CASH_RATE, FEE, SLIP, TAX, Book, krw_panel, max_drawdown, sma

WEIGHTS = {'NDX': 0.35, 'SPX': 0.35, 'SEMI': 0.12, 'SPACE': 0.09, 'QUANT': 0.09}  # equity part of 60/25 (no reservoir)
DIP_STEPS = ((-0.05, 0.25), (-0.10, 0.5), (-0.20, 1.0))  # (Nasdaq-100 drawdown, share of the reservoir spent)


def _buy(krw, pending):
    for n, w in WEIGHTS.items():
        pending[n] = pending.get(n, 0) + krw * w


def run(panel, variant, monthly=1_000_000, tp=0.30, overheat=1.15):
    days = sorted(panel)
    book = Book(0.0)
    reservoir_share = {'regular': 0, 'regular_res': 0.15, 'dip': 1.0, 'yeo': 0.5}[variant.removesuffix('_tp')]
    take_profit = variant.endswith('_tp')
    hist, pending, contributed, flows = [], {}, 0.0, []
    month, steps_used, armed, values, ratios, cash_share = None, set(), {n: True for n in WEIGHTS}, [], [], []
    for d in days:
        px = panel[d]
        book.cash *= 1 + CASH_RATE / 252
        for n, krw in sorted(pending.items(), key=lambda kv: kv[1]):  # yesterday's decisions fill at today's close
            book.trade(n, krw, px[n])
        pending = {}
        hist.append(px['NDX_USD'])
        if (d.year, d.month) != month:  # the month's contribution arrives
            month = (d.year, d.month)
            book.cash += monthly
            contributed += monthly
            flows.append((d, -monthly))
            _buy(monthly * (1 - reservoir_share), pending)
        hi = max(hist[-252:])
        dd = hist[-1] / hi - 1
        if dd >= -0.001:
            steps_used.clear()
        reserve = book.cash - sum(v for v in pending.values() if v > 0)
        for level, share in DIP_STEPS:
            if reservoir_share and dd <= level and level not in steps_used and reserve > monthly * 0.1:
                _buy(reserve * share, pending)
                steps_used.add(level)
                reserve -= reserve * share
        if take_profit:
            ma = sma(hist, 200)
            hot = ma is not None and hist[-1] > ma * overheat
            for n in WEIGHTS:
                q = book.qty.get(n, 0)
                if q <= 0:
                    continue
                gain = q * px[n] / book.cost[n] - 1
                if armed[n] and hot and gain >= tp:
                    pending[n] = pending.get(n, 0) - q * px[n] / 3
                    armed[n] = False
                elif not armed[n] and gain < tp / 2:
                    armed[n] = True
        value = book.value(px)
        values.append(value)
        ratios.append(value / contributed)
        cash_share.append(book.cash / value if value else 1)
    flows.append((days[-1], book.after_tax(panel[days[-1]])))
    return {'variant': variant, 'contributed': contributed, 'final': values[-1], 'after_tax': flows[-1][1],
            'multiple': values[-1] / contributed, 'xirr': xirr(flows), 'worst_vs_paid': min(ratios) - 1,
            'mdd': max_drawdown(values), 'avg_cash': sum(cash_share) / len(cash_share), 'trades_tax': book.tax}


def xirr(flows):
    """Money-weighted yearly return of dated flows (negative = paid in), by bisection."""
    t0 = flows[0][0]
    f = lambda r: sum(v / (1 + r) ** ((d - t0).days / 365) for d, v in flows)
    lo, hi = -0.9, 2.0
    for _ in range(100):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) > 0 else (lo, mid)
    return (lo + hi) / 2


# ---------- master DCA profiles (docs/longterm-master-dca-ideas-plan.md 10; the real code since S2, 2026-11-18) ----------
# The real engine runs every day: vibe_longterm indicators -> raw_regime/confirm -> plan_orders with the profile's module
# bundle, then longterm_dca.plan with the profile (it never sells). Lynch's story check is an LLM call: here every story
# is "intact", so the Lynch profile shows the band alone. The waiting money sits as cash at CASH_RATE (as the other
# replays). Prices: KRW proxies, tax 15.4% on realized gains.
PROFILE_VARIANTS = (*__import__('longterm_dca').PROFILES, 'auto')


def run_engine(panel, variant, start=None, end=None, monthly=1_000_000, history=400, modules=None, initial=0.0, steady=None, tune=None,
               by=None):
    """One book from `start`: the engine pipeline every day plus a 적립 profile ('auto': the regime's profile with the
    engine's recommended modules); a master runs its own way (v3: 언제·얼마나 by the master). modules: a fixed engine module set
    (an account preset, --presets) with the 정기 적립(dalio) contribution; variant is then its name. initial: money already in
    the book on the first day (the engine deploys it; with `steady` the steady rule does). steady: the proposed rule
    (docs/longterm-dca-steady-plan.md, --steady) replacing longterm_dca.plan for this run only. tune: the book's 내 조정 (--pace);
    by: 'me' runs a master profile on my calendar and amount (the 바로 baseline)."""
    from datetime import datetime
    import longterm_dca as D
    import vibe_longterm as L
    days = sorted(d for d in panel if end is None or d <= end)
    weights = {k: a[3] for k, a in L.ASSETS.items()}
    by = by or ('me' if variant == 'auto' or modules else 'master')  # a preset's contribution: my calendar and amount (dalio)
    cfg = {**D.DEFAULT, 'on': True, 'profile': 'dalio' if variant not in D.PROFILES else variant, 'time': '09:40', 'when_by': by, 'size_by': by,
           'tune': tune or {}}
    book, pending, dca_state, eng, rs = Book(0.0, tax=TAX), {}, {}, {}, None
    res, hist = 1.0, {'ndx': [], 'vix': [], 'fx': [], **{k: [] for k in L.SATS}}
    month, contributed, flows, values_hist, ratios, cash_share, regimes = None, 0.0, [], [], [], [], {}
    buy_days, waiting_share = 0, []
    for d in days:
        res *= 1 + CASH_RATE / 252
        px = {**panel[d], 'RES': res}
        fx = px['NDX'] / px['NDX_USD']
        for key, v in (('ndx', px['NDX_USD']), ('vix', px['VIX']), ('fx', fx), *((k, px[k] / fx) for k in L.SATS)):
            hist[key] = (hist[key] + [(d, v)])[-history:]
        if start and d < start:
            continue
        if initial and not contributed:
            book.cash, contributed = initial, initial
            flows.append((d, -initial))
        book.cash *= 1 + CASH_RATE / 252
        for k, krw in sorted(pending.items(), key=lambda kv: kv[1]):  # yesterday's orders fill at today's close
            book.trade(k, krw, px[k])
        pending = {}
        try:
            ind = L.indicators(hist['ndx'], hist['vix'], hist['fx'], {k: hist[k] for k in L.SATS}, d)
        except ValueError:
            continue
        rs = L.confirm(rs, L.raw_regime(ind, (rs or {}).get('regime')), d.isoformat())
        regime = rs['regime']
        regimes[regime] = regimes.get(regime, 0) + 1
        today = (list(modules) if modules else ['bogle_core', *L.REGIMES[regime][2]] if variant == 'auto'
                 else list(D.PROFILES[variant]['modules']))
        # steady: the engine never spends uninvested cash; a pool rule (v3 on a module book) keeps only the contributions it owns
        waiting = (dca_state.get('pool', 0.0) if steady and steady.get('pool') else book.cash if steady else dca_state.get('waiting_krw', 0))
        values = {k: book.qty.get(k, 0) * px[k] for k in L.ASSETS}
        nav = book.value(px) - waiting
        plan = {'indicators': ind, 'regime': regime, 'weights': weights, 'tuning': L.DEFAULT_SETTINGS['tuning']}
        want = L.targets(ind, today, regime, eng, {}, d.isoformat(), weights)[0]
        if nav >= 1:
            want, _, _, _, planned, eng = L.plan_orders(plan, today, eng, {}, values, nav, book.cash - waiting, d.isoformat())
            pending.update(planned)
        arrived = 0.0
        if (d.year, d.month) != month:  # the month's contribution, after the engine's orders (dca runs after trade)
            month = (d.year, d.month)
            arrived = monthly
            book.cash += monthly
            contributed += monthly
            flows.append((d, -monthly))
        held = {k: v for k, v in values.items() if v > 0}
        if steady:
            if steady.get('pool'):
                dca_state['pool'] = dca_state.get('pool', 0.0) + arrived
            extra = (steady.get('fn') or steady_buy)(steady, dca_state, d, ind, regime, want, book.value(px), book.cash)
        else:
            extra, dca_state, _ = D.plan(cfg, dca_state, datetime(d.year, d.month, d.day, 14), ind, regime, want, held, book.cash,
                                         arrived, 'auto' if variant == 'auto' and not modules else None)
        buy_days += bool(extra)
        for k, krw in extra.items():
            pending[k] = pending.get(k, 0) + krw
        if contributed:
            value = book.value(px)
            values_hist.append(value)
            ratios.append(value / contributed)
            cash_share.append(book.cash / value if value else 1)
            waiting_share.append(dca_state.get('waiting_krw', 0) / value if value else 0)
    last = {**panel[days[-1]], 'RES': res}
    flows.append((days[-1], book.after_tax(last)))
    label = (steady['label'] if steady else L.BOOK_PRESETS[variant][0] if modules and variant in L.BOOK_PRESETS else D.PROFILES[variant]['label'] if variant in D.PROFILES
             else '시황 자동')
    return {'variant': variant, 'label': label, 'contributed': contributed, 'final': values_hist[-1], 'after_tax': flows[-1][1],
            'multiple': values_hist[-1] / contributed, 'xirr': xirr(flows), 'worst_vs_paid': min(ratios) - 1,
            'mdd': max_drawdown(values_hist), 'avg_cash': sum(cash_share) / len(cash_share), 'turnover': book.turnover / contributed,
            'tax': book.tax, 'regime_days': regimes, 'buy_days': buy_days, 'avg_waiting': sum(waiting_share) / len(waiting_share),
            'cost': book.turnover * (FEE + SLIP),  # cost: fee + slippage on every trade (engine's included), an upper estimate
            'buys': dca_state.get('log', [])}


PACES = (0.5, 0.7, 0.85, 1.0)


def pace_rows(panel, start=None, end=None):
    """Review 2026-11-18 (0.7 was never replayed): each master's size on a module book at every 적립금 속도, against my calendar
    and amount (all of it on the contribution day)."""
    import longterm_dca as D
    rows = [{**run_engine(panel, 'dalio', start, end, by='me'), 'label': '바로(내 날·내 금액)'}]
    for profile in ('bogle_buffett', 'dalio', 'marks', 'yeo'):
        rows += [{**run_engine(panel, profile, start, end, tune={'pace': pace}), 'label': f"{D.PROFILES[profile]['label']} · 속도 {pace:.0%}"}
                 for pace in PACES]
    return rows


def preset_rows(panel, start=None, end=None):
    """Each account preset (vibe_longterm.BOOK_PRESETS, 보글 없음 included) through the engine with 정기 적립."""
    import vibe_longterm as L
    return [run_engine(panel, key, start, end, modules=modules) for key, (_, modules) in L.BOOK_PRESETS.items()]


# 투자 시점 (거장 판단 PACE vs 정한 날, docs/longterm-dca-timing-plan.md 8) was replayed here until the v3 rule replaced
# PACE (2026-11-18); its results stay in that document.


# ---------- 적립식 재설계 D1: 운용자금 비율 기반 꾸준 매수 + 현금 비중 유지 (docs/longterm-dca-steady-plan.md, proposal) ----------
# Research only: longterm_dca is not changed. Every week (first trading day of an ISO week) the base amount B is the gap
# to the invested target ((1 - cash) x book value - invested) spread over the rounds left in the deploy period (at least
# a month's rounds afterwards); today's buy = B x the master's regime multiplier x the down/up-day factor + 1/8 of the
# catch-up budget (what slower days left), capped at 1.5 B and never below the cash target. Splits by the engine target.
_m = lambda *v: dict(zip(('bull', 'recovery', 'correction', 'bear', 'panic', 'overheat'), v))
STEADY = {  # name: (label, regime multipliers, cash target, the profile whose engine module bundle runs alongside)
    'steady_bogle': ('꾸준 · 보글·버핏(1.0 · 현금 10%)', _m(1, 1, 1, 1, 1, 1), 0.10, 'bogle_buffett'),
    'steady_dalio': ('꾸준 · 달리오(과열 0.9 · 현금 15%)', _m(1, 1, 1, 1, 1, 0.9), 0.15, 'dalio'),
    'steady_marks': ('꾸준 · 막스(시계추 · 현금 20%)', _m(0.9, 1, 1.2, 1.3, 1.4, 0.6), 0.20, 'marks'),
    'steady_yeo': ('꾸준 · 저수지형(현금 30%)', _m(0.8, 1, 1.2, 1.4, 1.5, 0.5), 0.30, 'yeo'),
}
WEEKS_PER_MONTH = 4.35


def steady_buy(cfg, state, day, ind, regime, want, value, cash):
    """One day of the proposed rule; returns {asset: KRW} (empty on a non-buy day)."""
    week = day.isocalendar()[:2]
    if state.get('week') == week:
        return {}
    state['week'] = week
    state['rounds'] = state.get('rounds', 0) + 1
    invested = value - cash
    gap = max(0.0, (1 - cfg['cash']) * value - invested)
    left = max(WEEKS_PER_MONTH, cfg['months'] * WEEKS_PER_MONTH - state['rounds'] + 1)
    base = gap / left
    pace = cfg['mult'].get(regime, 1.0) * ((1.2 if (ind.get('day_ret') or 0) < 0 else 0.8) if cfg.get('dip', True) else 1.0)
    today = base * pace
    catch = state.get('catch', 0.0)
    add = catch / 8 if today < 1.5 * base else 0.0
    spend = min(today + add, 1.5 * base, max(0.0, cash - cfg['cash'] * value))
    state['catch'] = max(0.0, catch - add + max(0.0, base - today))
    weights = {k: w for k, w in want.items() if w > 0 and k != 'RES'}
    total = sum(weights.values())
    return {k: spend * w / total for k, w in weights.items()} if spend >= 1000 and total else {}


def steady_rows(panel, start=None, end=None, initial=0.0, months=24):
    """The current way (달리오 정기 · 저수지 대기 투입) against the proposal's masters, same money and engine."""
    import longterm_dca as D
    rows = [run_engine(panel, v, start, end, initial=initial) for v in ('dalio', 'yeo')]
    runs = [(key, {'label': label, 'mult': mult, 'cash': cash, 'months': months}, profile)
            for key, (label, mult, cash, profile) in STEADY.items()]
    runs.append(('steady_marks_nodip', {'label': '꾸준 · 막스, 하락일 보정 없음', 'mult': STEADY['steady_marks'][1], 'cash': 0.20,
                                        'months': months, 'dip': False}, 'marks'))
    return rows + [run_engine(panel, key, start, end, initial=initial, steady=cfg, modules=D.PROFILES[profile]['modules'])
                   for key, cfg, profile in runs]


VARIANTS = ('regular', 'regular_res', 'dip', 'yeo', 'regular_tp', 'regular_res_tp', 'yeo_tp')
WINDOWS = {'10년': None, '2020 급락 전후': ('2019-07-01', '2021-06-30'), '2022 약세장': ('2021-11-01', '2023-06-30'),
           '2023~ 강세장': ('2023-01-01', None)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--profiles', action='store_true', help='master DCA profiles through the real engine pipeline (S1)')
    parser.add_argument('--presets', action='store_true', help='account strategy presets (기준 비중 없음 included) through the engine')
    parser.add_argument('--steady', type=float, metavar='INITIAL', help='D1: the steady redesign vs today, with this much in the book at start')
    parser.add_argument('--pace', action='store_true', help='the master size\'s 적립금 속도 (0.5~1.0) on a module book, vs buying it all at once')
    args = parser.parse_args()
    panel = krw_panel()
    out = {}
    for name, span in WINDOWS.items():
        # the indicators keep the history before the window; the book starts at it
        a, b = (date.fromisoformat(span[0]) if span else None), (date.fromisoformat(span[1]) if span and span[1] else None)
        if args.profiles:
            out[name] = [run_engine(panel, v, a, b) for v in PROFILE_VARIANTS]
            continue
        if args.presets:
            out[name] = preset_rows(panel, a, b)
            continue
        if args.steady is not None:
            out[name] = steady_rows(panel, a, b, initial=args.steady)
            continue
        if args.pace:
            out[name] = pace_rows(panel, a, b)
            continue
        part = {d: v for d, v in panel.items() if not span or (date.fromisoformat(span[0]) <= d and (span[1] is None or d <= date.fromisoformat(span[1])))}
        out[name] = [run(part, v) for v in VARIANTS]
    if args.json:
        print(json.dumps(out, ensure_ascii=False, default=str, indent=1))
        return
    if args.steady is not None or args.pace:
        for name, rows in out.items():
            print(f'\n## {name}')
            print('label                              xirr   worst    mdd  avg_cash  buy_days  turnover  final(만)')
            for r in rows:
                print(f"{r['label'][:32]:34} {r['xirr']*100:6.2f}% {r['worst_vs_paid']*100:6.1f}% {r['mdd']*100:6.1f}% {r['avg_cash']*100:7.1f}% "
                      f"{r['buy_days']:7}  {r['turnover']:7.2f}  {r['final']/1e4:8.0f}")
        return
    for name, rows in out.items():
        print(f'\n## {name}')
        print('variant          xirr    multiple  worst_vs_paid  mdd     avg_cash  turnover  tax')
        for r in rows:
            print(f"{r['variant']:15} {r['xirr']*100:6.1f}%  {r['multiple']:7.2f}  {r['worst_vs_paid']*100:9.1f}%  {r['mdd']*100:6.1f}%  "
                  f"{r['avg_cash']*100:6.1f}%  " + (f"{r['turnover']:6.2f}  {r['tax']:>10,.0f}" if 'turnover' in r else '     —           —'))


if __name__ == '__main__':
    main()
