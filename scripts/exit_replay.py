"""Replay the runner's exit rules on past sessions (research only; never orders).

In the spirit of day-trader-skill's Time Machine history check: for each ETF and each of the last ~60 sessions,
buy at fixed times (10:00, 11:00, 13:00 bar opens) and walk 5-minute bars to 14:40 under each exit rule:
  split     +1% half / +2% rest, -2% stop
  target    full exit at the data target (20-day median rise from the open x regime factor, 0.8-3%), -2% stop
  trend     +2% half (2x ETFs x1.5), the rest on a trailing stop from the peak (1.5%, x1.5), -2% stop
A bar that touches both the stop and a target counts as the stop (conservative). Results are grouped by the KOSPI
regime known at that day's open. Like the runner's entry confirmation, a long is bought only outside a 하락장 and an
inverse only outside a 상승장, and only when the ETF trades above its previous close at the entry time. Yahoo Finance 5-minute data can lag or have gaps; costs are not modelled.
"""
from __future__ import annotations

import json
import statistics
from datetime import time

ETFS = {'K200': '069500', 'LEV': '122630', 'INV': '114800', 'KQ150LEV': '233740', 'SEMILEV': '494310'}
SHORT = {'114800'}
LEVERAGED = {'122630', '233740', '494310'}
FACTOR = {'하락장': 0.4, '횡보장': 0.5}
ENTRIES = (time(10, 0), time(11, 0), time(13, 0))
FLAT = time(14, 40)


def walk(bars, entry, rule, target=None, scale=1.0):
    """Return % from `entry` over bars [(high, low, close)] for one rule; the last close is the 14:40 exit."""
    stop, half_done, peak, realized = entry * 0.98, False, entry, 0.0
    half = entry * (1.01 if rule == 'split' else 1 + 0.02 * scale)
    full = entry * (1.02 if rule == 'split' else 1 + (target or 2.0) / 100)
    trail = 0.015 * scale
    for high, low, close in bars:
        if low <= (entry if half_done else stop):  # stop, or breakeven after the half sale
            exit_price = entry if half_done else stop
            return round(realized + (0.5 if half_done else 1.0) * (exit_price / entry - 1) * 100, 3)
        if rule == 'target':
            if high >= full:
                return round((full / entry - 1) * 100, 3)
            continue
        if not half_done and high >= half:
            half_done, realized = True, 0.5 * (half / entry - 1) * 100
        peak = max(peak, high)
        if rule == 'split' and half_done and high >= full:
            return round(realized + 0.5 * (full / entry - 1) * 100, 3)
        if rule == 'trend' and half_done and low <= peak * (1 - trail):
            return round(realized + 0.5 * (peak * (1 - trail) / entry - 1) * 100, 3)
    last = bars[-1][2] if bars else entry
    return round(realized + (0.5 if half_done else 1.0) * (last / entry - 1) * 100, 3)


def kospi_regimes(daily_close):
    """{date: regime known at that day's open} with the market_context rule (20/60-day averages, 20-day return)."""
    ma20, ma60 = daily_close.rolling(20).mean(), daily_close.rolling(60).mean()
    ret20 = daily_close / daily_close.shift(20) - 1
    score = ((daily_close > ma20).astype(int) + (ma20 > ma60).astype(int) + (ret20 > 0.03).astype(int)
             - (daily_close < ma20).astype(int) - (ma20 < ma60).astype(int) - (ret20 < -0.03).astype(int)).shift(1)
    labels = score.map(lambda s: '상승장' if s >= 2 else '하락장' if s <= -2 else '횡보장')
    return {d.date(): r for d, r in labels[ma60.shift(1).notna()].items()}


def run():
    import yfinance as yf
    tickers = [f'{code}.KS' for code in ETFS.values()]
    intraday = yf.download(tickers, period='60d', interval='5m', progress=False, auto_adjust=False)
    daily = yf.download(tickers + ['^KS11'], period='1y', interval='1d', progress=False, auto_adjust=False)
    regimes = kospi_regimes(daily['Close']['^KS11'].dropna())
    results = {}
    for name, code in ETFS.items():
        ticker, scale = f'{code}.KS', (1.5 if code in LEVERAGED else 1.0)
        frame = intraday.xs(ticker, axis=1, level=1).dropna()
        frame.index = frame.index.tz_convert('Asia/Seoul')
        day_frame = daily.xs(ticker, axis=1, level=1).dropna()
        ups = (day_frame['High'] / day_frame['Open'] - 1) * 100
        closes = day_frame['Close']
        for day, bars in frame.groupby(frame.index.date):
            regime = regimes.get(day)
            past = ups[ups.index.date < day].tail(20)
            if regime is None or len(past) < 10:
                continue
            target = min(max(past.median() * FACTOR.get(regime, 0.6), 0.8), 3.0)
            previous = closes[closes.index.date < day]
            if previous.empty or regime == ('상승장' if code in SHORT else '하락장'):
                continue  # the runner would not buy against the regime
            for start in ENTRIES:
                window = bars[(bars.index.time >= start) & (bars.index.time < FLAT)]
                if len(window) < 3:
                    continue
                entry = float(window['Open'].iloc[0])
                if entry < float(previous.iloc[-1]):
                    continue  # not moving its way yet today
                path = list(zip(window['High'], window['Low'], window['Close']))
                for rule in ('split', 'target', 'trend'):
                    results.setdefault((regime, rule), []).append(walk(path, entry, rule, target, scale))
    table = []
    for (regime, rule), returns in sorted(results.items()):
        table.append({'regime': regime, 'rule': rule, 'trades': len(returns), 'avg_return_pct': round(statistics.mean(returns), 3),
                      'win_rate': round(sum(r > 0 for r in returns) / len(returns), 2), 'worst_pct': min(returns),
                      'median_pct': round(statistics.median(returns), 3)})
    return table


def main():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import research_store
    table = run()
    research_store.record('exit_replay', {'time': datetime.now(ZoneInfo('Asia/Seoul')).isoformat(timespec='seconds'), 'table': table})
    print(json.dumps(table, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
