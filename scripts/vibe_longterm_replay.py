"""P2 replay for the long-term vibe engine (docs/vibe-longterm-plan.md, user approval 2026-11-17). Read-only research.

Daily closes of US proxies in KRW (adjusted close x USDKRW, like the unhedged KRX-listed ETFs) replay the rule layer:
core NDX/SPX, satellites (semis, space, quantum), a cash reservoir with drawdown tiers, band rebalancing, the 200-day
satellite check, the VIX pendulum and 2% risk sizing. Signals use day t's close, trades fill at day t+1's close with
fee + slippage; realized gains pay 15.4% (KRX-listed overseas ETF), and the after-tax figure also taxes the final
liquidation. Proxies are not the KRX ETFs (fees, tracking, distributions, trading hours differ); `--check` compares them.
"""
from __future__ import annotations

import argparse
import json
import math
import urllib.request
from datetime import date
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')

PROXY = {'NDX': 'QQQ', 'SPX': 'SPY', 'SEMI': 'SOXX', 'SPACE': 'UFO', 'QUANT': 'QTUM'}
KRX = {'NDX': '379810.KS', 'SPX': '379800.KS', 'SEMI': '381180.KS', 'SPACE': '0183J0.KS', 'QUANT': '0023A0.KS'}
CORE, SATS = ('NDX', 'SPX'), ('SEMI', 'SPACE', 'QUANT')
FEE, SLIP, TAX, CASH_RATE = 0.00015, 0.0005, 0.154, 0.025


def fetch(symbol, years=10):
    req = urllib.request.Request(f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={years}y&interval=1d',
                                 headers={'User-Agent': 'Mozilla/5.0'})
    r = json.load(urllib.request.urlopen(req, timeout=30))['chart']['result'][0]
    ind = r['indicators']
    close = (ind.get('adjclose') or [{}])[0].get('adjclose') or ind['quote'][0]['close']
    return {date.fromtimestamp(t): x for t, x in zip(r['timestamp'], close) if x}


def krw_panel(start=None):
    """{day: {name: KRW price}} on US trading days where every proxy, VIX and USDKRW (forward-filled) exist."""
    fx, vix = fetch('KRW=X'), fetch('^VIX')
    raw = {n: fetch(s) for n, s in PROXY.items()}
    days = sorted(set.intersection(*(set(v) for v in raw.values())))
    out, last_fx = {}, None
    fx_days = sorted(fx)
    i = 0
    for d in days:
        while i < len(fx_days) and fx_days[i] <= d:
            last_fx = fx[fx_days[i]]; i += 1
        if last_fx and d in vix and (start is None or d >= start):
            out[d] = {**{n: raw[n][d] * last_fx for n in PROXY}, 'VIX': vix[d], 'NDX_USD': raw['NDX'][d]}
    return out


class Book:
    """Cash and holdings with average cost, fees and realized-gain tax."""
    def __init__(self, cash, tax=TAX):
        self.cash, self.qty, self.cost, self.tax, self.turnover, self.tax_rate = cash, {}, {}, 0.0, 0.0, tax

    def value(self, px):
        return self.cash + sum(q * px[n] for n, q in self.qty.items())

    def trade(self, name, krw, price):
        if abs(krw) < 1:
            return
        self.turnover += abs(krw)
        if krw > 0:
            krw = min(krw, self.cash / (1 + FEE + SLIP))
            q = krw / (price * (1 + SLIP))
            self.cash -= krw * (1 + FEE)
            self.cost[name] = self.cost.get(name, 0) + krw
            self.qty[name] = self.qty.get(name, 0) + q
        else:
            q = min(self.qty.get(name, 0), -krw / price)
            if q <= 0:
                return
            avg = self.cost[name] / self.qty[name]
            proceeds = q * price * (1 - SLIP)
            gain = proceeds - q * avg
            tax = max(0.0, gain) * self.tax_rate
            self.tax += tax
            self.cash += proceeds * (1 - FEE) - tax
            self.qty[name] -= q
            self.cost[name] -= q * avg

    def after_tax(self, px):
        return self.value(px) - sum(max(0.0, q * px[n] - self.cost[n]) * self.tax_rate for n, q in self.qty.items())


def sma(seq, n):
    return sum(seq[-n:]) / n if len(seq) >= n else None


def replay(panel, *, reservoir=0.15, sats=0.25, tiers=((-0.10, 0.05), (-0.20, 0.05), (-0.30, 0.05)), band=0.05,
           sat_trend=True, pendulum=True, risk_size=False, rebalance=True, satellites=SATS, start_weights=None,
           capital=100_000_000, refill=True, confirm=2):
    """Run the rule layer; returns daily values plus counts. start_weights replaces the target model (plain buy-hold)."""
    days = sorted(panel)
    core_w = 1 - reservoir - sats
    base = start_weights or {**{n: core_w / 2 for n in CORE}, **{n: sats / len(satellites) for n in satellites}}
    book = Book(capital)
    hist = {n: [] for n in list(PROXY) + ['NDX_USD']}
    used, below, cut, values, after = set(), {n: 0 for n in satellites}, {}, [], []
    trades, pending, target_res = 0, None, reservoir
    for k, d in enumerate(days):
        px = panel[d]
        book.cash *= 1 + CASH_RATE / 252
        if pending:  # yesterday's decision fills at today's close
            tot = book.value(px)
            for n, w in sorted(pending.items(), key=lambda item: item[1] - book.qty.get(item[0], 0) * px[item[0]] / tot):
                diff = w * tot - book.qty.get(n, 0) * px[n]
                if abs(diff) > tot * 0.002:
                    book.trade(n, diff, px[n]); trades += 1
            pending = None
        for n in hist:
            hist[n].append(px[n])
        values.append(book.value(px)); after.append(book.after_tax(px))
        if k == 0:
            pending = dict(base); continue
        if start_weights:
            continue
        tot = book.value(px)
        weights = {n: book.qty.get(n, 0) * px[n] / tot for n in PROXY}
        hi = max(hist['NDX_USD'][-252:])
        dd = px['NDX_USD'] / hi - 1
        target = {n: weights.get(n, 0) for n in PROXY}
        changed = False
        # 1. pendulum: fear lifts the first tier, greed raises the reservoir target
        fear = pendulum and px['VIX'] >= 30
        ma200 = sma(hist['NDX_USD'], 200)
        greed = pendulum and px['VIX'] <= 14 and ma200 and px['NDX_USD'] > ma200 * 1.15
        new_res = reservoir + (0.05 if greed else 0)
        # 2. reservoir tiers on the NDX drawdown, refill at a new high
        for t, frac in tiers:
            level = t + 0.03 if fear and t == tiers[0][0] else t
            if dd <= level and t not in used and book.cash > tot * 0.01:
                move = min(frac * tot, book.cash)
                for n in CORE:
                    target[n] += move / tot / 2
                used.add(t); changed = True
        if dd >= -0.001 and used:  # a new high re-arms the tiers; refill sells the core back to the targets at once,
            used.clear()           # otherwise the monthly band rebalance refills the reservoir over time
            if refill:
                target_res = new_res; changed = True
                for n in CORE:
                    target[n] = (1 - target_res - sats) / 2
        # 3. satellites: 200-day check (half, then all after 20 days below), back above +2%
        for n in satellites:
            ma = sma(hist[n], 200)
            if not sat_trend or ma is None:
                continue
            p = px[n]
            below[n] = below[n] + 1 if p < ma * 0.98 else 0
            if below[n] == confirm and cut.get(n) is None:
                target[n] = weights[n] / 2; cut[n] = 'half'; changed = True
            elif below[n] == confirm + 20 and cut.get(n) == 'half':
                target[n] = 0; cut[n] = 'all'; changed = True
            elif cut.get(n) and p > ma * 1.02:
                target[n] = base[n]; cut.pop(n); changed = True
        # 4. 2% risk sizing on re-entries / rebalances of satellites
        if risk_size:
            for n in satellites:
                ma = sma(hist[n], 200)
                if ma and not cut.get(n) and px[n] > ma * 0.98:
                    dist = 1 - ma * 0.98 / px[n]
                    target[n] = min(target[n], 0.10, 0.02 / max(dist, 0.02))
        # 5. monthly band check against the targets
        if rebalance and (k % 21 == 0):
            want = {**{n: (1 - target_res - sats) / 2 for n in CORE},
                    **{n: (0 if cut.get(n) == 'all' else base[n] / (2 if cut.get(n) == 'half' else 1)) for n in satellites}}
            if not used and any(abs(weights.get(n, 0) - w) > band for n, w in want.items()):
                target.update(want); changed = True
            elif greed and target_res > reservoir and sum(weights[n] for n in CORE) > 1 - target_res - sats + band:
                target.update({n: want[n] for n in CORE}); changed = True
            if not greed and not used:
                target_res = reservoir
        if changed:
            total_sat = sum(target[n] for n in satellites)
            if total_sat > sats:
                for n in satellites:
                    target[n] *= sats / total_sat
            pending = {n: w for n, w in target.items() if n in PROXY}
    return {'values': values, 'after_tax': after, 'trades': trades, 'turnover': book.turnover / capital,
            'tax': book.tax, 'days': days}


def max_drawdown(values):
    """Deepest fall from a running peak, as a negative share (0.0 when it never fell)."""
    peak, worst = None, 0.0
    for v in values:
        peak = v if peak is None else max(peak, v)
        worst = min(worst, v / peak - 1)
    return worst


def stats(values, days):
    years = (days[-1] - days[0]).days / 365.25
    mdd = max_drawdown(values)
    rets = [b / a - 1 for a, b in zip(values, values[1:])]
    mean = sum(rets) / len(rets)
    vol = math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets)) * math.sqrt(252)
    cagr = (values[-1] / values[0]) ** (1 / years) - 1
    return {'cagr': cagr, 'mdd': mdd, 'vol': vol, 'calmar': cagr / -mdd if mdd else None}


def window(result, a, b):
    idx = [i for i, d in enumerate(result['days']) if a <= d <= b]
    vals = [result['values'][i] for i in idx]
    return vals[-1] / vals[0] - 1, max_drawdown(vals)


def check():
    """Daily return fit of each proxy (KRW) to the KRX ETF it stands for, on common days with the US close one day earlier."""
    fx = fetch('KRW=X'); fxd = sorted(fx)
    rows = {}
    for n in PROXY:
        us, kr = fetch(PROXY[n]), fetch(KRX[n], 5)
        usd = sorted(us)
        krw = {}
        j, last = 0, None
        for d in usd:
            while j < len(fxd) and fxd[j] <= d:
                last = fx[fxd[j]]; j += 1
            if last:
                krw[d] = us[d] * last
        pairs = []
        kd = sorted(kr)
        for a, b in zip(kd, kd[1:]):
            prev_us = [d for d in usd if d < a][-1:] ; cur_us = [d for d in usd if d < b][-1:]
            if prev_us and cur_us and prev_us[0] in krw and cur_us[0] in krw and prev_us != cur_us:
                pairs.append((kr[b] / kr[a] - 1, krw[cur_us[0]] / krw[prev_us[0]] - 1))
        if len(pairs) < 20:
            continue
        x, y = [p[1] for p in pairs], [p[0] for p in pairs]
        mx, my = sum(x) / len(x), sum(y) / len(y)
        cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
        corr = cov / math.sqrt(sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y))
        years = (kd[-1] - kd[0]).days / 365.25
        kr_cagr = (kr[kd[-1]] / kr[kd[0]]) ** (1 / years) - 1
        prod = 1.0
        for _, r in pairs:
            prod *= 1 + r
        rows[n] = {'krx': KRX[n], 'since': kd[0].isoformat(), 'days': len(pairs), 'corr': round(corr, 3),
                   'krx_cagr': round(kr_cagr * 100, 1), 'proxy_cagr': round((prod ** (1 / years) - 1) * 100, 1)}
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='compare proxies with the KRX ETFs')
    parser.add_argument('--start', default='2019-04-12')
    args = parser.parse_args()
    if args.check:
        print(json.dumps(check(), ensure_ascii=False, indent=1)); return
    panel = krw_panel(date.fromisoformat(args.start))
    days = sorted(panel)
    print(f'{days[0]} ~ {days[-1]} ({len(days)}일, KRW 환산)')
    variants = {
        'A 코어 50:50 보유': dict(start_weights={'NDX': 0.5, 'SPX': 0.5}),
        'B 60/25/15 보유(리밸런싱 없음)': dict(start_weights={'NDX': 0.30, 'SPX': 0.30, 'SEMI': 0.25 / 3, 'SPACE': 0.25 / 3, 'QUANT': 0.25 / 3}),
        'C B + 월 밴드 리밸런싱': dict(tiers=(), sat_trend=False, pendulum=False),
        'D C + 저수지 단계 투입': dict(sat_trend=False, pendulum=False),
        'E D + 위성 200일선': dict(pendulum=False),
        'F E + 시계추(VIX)': dict(),
        'G F + 2% 리스크 크기': dict(risk_size=True),
        'H F, 저수지 0%': dict(reservoir=0.0, tiers=()),
        'I F, 저수지 30%': dict(reservoir=0.30, tiers=((-0.10, 0.10), (-0.20, 0.10), (-0.30, 0.10))),
        'J F, 위성 15%': dict(sats=0.15),
    }
    results = {}
    for name, kw in variants.items():
        r = replay(panel, **kw)
        s = stats(r['values'], r['days']); at = stats(r['after_tax'], r['days'])
        c20 = window(r, date(2020, 2, 19), date(2020, 12, 31)); c22 = window(r, date(2021, 11, 19), date(2022, 12, 31))
        results[name] = {**{k: round(v * 100, 1) if k != 'calmar' else round(v, 2) for k, v in s.items()},
                         'cagr_after_tax': round(at['cagr'] * 100, 1), 'trades': r['trades'],
                         'turnover_x': round(r['turnover'], 1), 'mdd_2020': round(c20[1] * 100, 1),
                         'mdd_2022': round(c22[1] * 100, 1), 'ret_2022': round(c22[0] * 100, 1)}
        row = results[name]
        print(f"{name:28} CAGR {row['cagr']:5.1f}% 세후 {row['cagr_after_tax']:5.1f}% MDD {row['mdd']:6.1f}% "
              f"변동성 {row['vol']:4.1f}% 칼마 {row['calmar']:4.2f} 거래 {row['trades']:3} 회전 {row['turnover_x']:4.1f}배 "
              f"2020 MDD {row['mdd_2020']:6.1f}% 2022 MDD {row['mdd_2022']:6.1f}%")
    return results


if __name__ == '__main__':
    main()
