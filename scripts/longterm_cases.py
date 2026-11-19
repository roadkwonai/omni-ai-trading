"""Past cases behind the long-term level map (docs/longterm-strategy-plan.md 3): for each asset and level kind, what
happened 20/60/120/250 trading days after the level was first touched, next to a baseline of ordinary days.

Prices are the US ETF proxies x USDKRW (vibe_longterm_replay), 10 years. A signal that fires again within 60 days counts
once. Stored in nhplug/vibe-longterm/cases.sqlite3 (stats, events); `--refresh` rebuilds it. Past results are not
probabilities: the report shows them with their case counts.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path

HORIZONS = (20, 60, 120, 250)
GAP = 60  # a signal again within this many days is the same case
# kind: (label, test(px, i, ctx)); ctx has the 200/50-day averages and the 252-day high
KINDS = {
    'dd10': ('252일 고점 대비 −10% 첫 도달', lambda c, i: c['px'][i] <= c['hi'][i] * 0.90 < c['px'][i - 1]),
    'dd15': ('252일 고점 대비 −15% 첫 도달', lambda c, i: c['px'][i] <= c['hi'][i] * 0.85 < c['px'][i - 1]),  # 투매 + 막스: tiers
    'dd20': ('252일 고점 대비 −20% 첫 도달', lambda c, i: c['px'][i] <= c['hi'][i] * 0.80 < c['px'][i - 1]),
    'dd25': ('252일 고점 대비 −25% 첫 도달', lambda c, i: c['px'][i] <= c['hi'][i] * 0.75 < c['px'][i - 1]),
    'dd30': ('252일 고점 대비 −30% 첫 도달', lambda c, i: c['px'][i] <= c['hi'][i] * 0.70 < c['px'][i - 1]),
    'ma200break': ('200일선 −2% 아래로 첫 이탈', lambda c, i: c['px'][i] < c['ma200'][i] * 0.98 <= c['px'][i - 1]),
    'overheat125': ('200일선 × 1.25 과열', lambda c, i: c['px'][i] >= c['ma200'][i] * 1.25),
    'pullback50': ('상승 추세 중 50일선 눌림', lambda c, i: c['ma50'][i] > c['ma200'][i] and c['px'][i] <= c['ma50'][i] < c['px'][i - 1]),
}
INDEX_KINDS = {  # read on the NASDAQ-100 with the VIX, applied to every asset's own forward returns
    'crash3': ('나스닥100 하루 −3% · VIX ≥ 25', lambda c, i: c['ndx'][i] / c['ndx'][i - 1] - 1 <= -0.03 and c['vix'][i] >= 25),
    'overheat115': ('나스닥100 200일선 +15% · VIX ≤ 14', lambda c, i: c['ndx'][i] >= c['ndx_ma200'][i] * 1.15 and c['vix'][i] <= 14),
}
ROOT = Path.home() / '.vibe-trading/nhplug/vibe-longterm'


def rolling(px, n, fn):
    return [fn(px[i - n + 1:i + 1]) if i >= n - 1 else None for i in range(len(px))]


def signals(ctx, test, start=252):
    out, last = [], -10 ** 9
    for i in range(max(start, 1), len(ctx['px'])):
        try:
            hit = test(ctx, i)
        except TypeError:  # an average not yet defined
            hit = False
        if hit and i - last >= GAP:
            out.append(i); last = i
    return out


def forward(px, idx, h):
    """(median return, share up, median worst fall inside the window) over the cases with h days ahead."""
    ok = [i for i in idx if i + h < len(px)]
    if not ok:
        return None
    rets = [px[i + h] / px[i] - 1 for i in ok]
    worst = [min(px[i:i + h + 1]) / px[i] - 1 for i in ok]
    return {'n': len(ok), 'median': statistics.median(rets), 'up': sum(r > 0 for r in rets) / len(rets),
            'worst': statistics.median(worst)}


def context(px, ndx=None, vix=None):
    ctx = {'px': px, 'hi': rolling(px, 252, max), 'ma200': rolling(px, 200, lambda w: sum(w) / 200),
           'ma50': rolling(px, 50, lambda w: sum(w) / 50)}
    if ndx is not None:
        ctx.update(ndx=ndx, vix=vix, ndx_ma200=rolling(ndx, 200, lambda w: sum(w) / 200))
    return ctx


def study(px, days, ndx=None, vix=None):
    """{kind: {'label', 'events': [day], 'h': {h: forward}}} plus 'baseline' (every 20th day)."""
    ctx = context(px, ndx, vix)
    out = {}
    for kind, (label, test) in {**KINDS, **(INDEX_KINDS if ndx is not None else {})}.items():
        idx = signals(ctx, test)
        out[kind] = {'label': label, 'events': [days[i] for i in idx], 'h': {h: forward(px, idx, h) for h in HORIZONS}}
    base = list(range(252, len(px), 20))
    out['baseline'] = {'label': '아무 날(20일마다)', 'events': [], 'h': {h: forward(px, base, h) for h in HORIZONS}}
    return out


def build(root=ROOT, fetch=None):
    """Rebuild the case database from the proxies; returns the number of rows written."""
    import vibe_longterm as L
    from vibe_longterm_replay import fetch as yahoo
    fetch = fetch or (lambda s: yahoo(s, 10))
    fx = fetch('KRW=X'); fxd = sorted(fx)
    ndx_raw, vix_raw = fetch('^NDX'), fetch('^VIX')

    def krw(raw):
        out, j, last = [], 0, None
        for d in sorted(raw):
            while j < len(fxd) and fxd[j] <= d:
                last = fx[fxd[j]]; j += 1
            if last and d in ndx_raw and d in vix_raw:
                out.append((d, raw[d] * last))
        return out
    rows, events = [], []
    for key, asset in L.ASSETS.items():
        if not asset[4]:
            continue
        series = krw(fetch(asset[4]))
        days, px = [d for d, _ in series], [p for _, p in series]
        result = study(px, [d.isoformat() for d in days], [ndx_raw[d] for d in days], [vix_raw[d] for d in days])
        for kind, item in result.items():
            for h, f in item['h'].items():
                if f:
                    rows.append((key, kind, item['label'], h, f['n'], f['median'], f['up'], f['worst'],
                                 f"{asset[4]}×원/달러 {days[0]}~{days[-1]}"))
            events += [(key, kind, d) for d in item['events']]
    Path(root).mkdir(parents=True, exist_ok=True, mode=0o700)
    with sqlite3.connect(Path(root) / 'cases.sqlite3') as db:
        db.execute('DROP TABLE IF EXISTS stats'); db.execute('DROP TABLE IF EXISTS events')
        db.execute('CREATE TABLE stats (asset TEXT, kind TEXT, label TEXT, h INTEGER, n INTEGER, median REAL, up REAL, worst REAL, source TEXT)')
        db.execute('CREATE TABLE events (asset TEXT, kind TEXT, day TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
        db.executemany('INSERT INTO stats VALUES (?,?,?,?,?,?,?,?,?)', rows)
        db.executemany('INSERT INTO events VALUES (?,?,?)', events)
        db.execute("INSERT OR REPLACE INTO meta VALUES ('updated', ?)", (datetime.now().astimezone().isoformat(timespec='seconds'),))
    return len(rows)


def evidence(root=ROOT):
    """{(asset, kind): {'label', 'source', h: {n, median, up, worst}}} including 'baseline'; {} before the first build."""
    path = Path(root) / 'cases.sqlite3'
    if not path.exists():
        return {}
    from sqlite_ro import ro_connect
    out = {}
    with ro_connect(path) as db:
        for asset, kind, label, h, n, median, up, worst, source in db.execute('SELECT * FROM stats'):
            item = out.setdefault((asset, kind), {'label': label, 'source': source})
            item[h] = {'n': n, 'median': median, 'up': up, 'worst': worst}
    return out


def events(asset, kind, root=ROOT):
    path = Path(root) / 'cases.sqlite3'
    if not path.exists():
        return []
    from sqlite_ro import ro_connect
    with ro_connect(path) as db:
        return [r[0] for r in db.execute('SELECT day FROM events WHERE asset=? AND kind=? ORDER BY day', (asset, kind))]


def describe(item, baseline=None, h=120):
    """One Korean line for a level's evidence, e.g. '과거 7건 · 120일 뒤 중앙 +34.2%(상승 100%) · 그사이 최악 −2.3% · 평소 +12.4%'."""
    if not item or h not in item:
        return '과거 사례 없음'
    f, base = item[h], (baseline or {}).get(h)
    text = f"과거 {f['n']}건 · {h}일 뒤 중앙 {f['median'] * 100:+.1f}%(상승 {f['up'] * 100:.0f}%) · 그사이 최악 {f['worst'] * 100:.1f}%"
    return text + (f" · 평소 {base['median'] * 100:+.1f}%" if base else '')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh', action='store_true')
    args = parser.parse_args()
    if args.refresh:
        print(json.dumps({'rows': build()}))
    else:
        print(json.dumps({f'{a}:{k}': describe(v) for (a, k), v in evidence().items()}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
