"""Display names by stock code, one map for the dashboard, Discord, the journal and the CLI.

A stock bought outside the watchlist (a new listing) or one that left today's scan had no name on any screen
(2026-11-25 엠비디, shown as 498150). `load` reads local files only; `learn` asks Toss's stock master once for traded
codes nobody named and keeps the answer in stock-names.json. Read-only: never orders.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from config_store import write_json

FILE = 'stock-names.json'
RETRY_S = 600
_asked = {}  # code -> when Toss was last asked


def _json(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load(nh):
    """{code: name}: learned names, the case library, new listings, the latest scan, the watchlist and the ETFs."""
    import vibe_longterm
    import yeo_method
    from etf_universe import NAMES, SYMBOLS
    from oversold_cases import NAMES as CASES
    shared = Path(nh) / 'shared-etf-paper'
    listed = _json(shared / 'new-listings.json').get('rows')
    found = [(c, n) for c, n in _json(shared / FILE).items()] + list(CASES.items())
    found += [(r.get('code'), r.get('name')) for r in (listed if isinstance(listed, list) else []) if isinstance(r, dict)]
    for file, part in (('movers.json', 'rows'), ('watchlist.json', 'stocks')):
        rows = _json(shared / file).get(part)
        found += [(c, r.get('name')) for c, r in (rows.items() if isinstance(rows, dict) else ()) if isinstance(r, dict)]
    names = {c: n.lstrip('*') for c, n in found if isinstance(c, str) and isinstance(n, str) and n.lstrip('*')}
    names |= {a[0]: a[1] for a in vibe_longterm.ASSETS.values()} | dict(yeo_method.EXTRA.values())
    return names | {code: NAMES[key] for key, code in SYMBOLS.items()}


def traded(nh):
    """Every code in the shared paper and live fill records."""
    from sqlite_ro import ro_connect
    codes = set()
    for path in (Path(nh) / 'shared-etf-paper/orders.sqlite3', Path(nh) / 'live-fills.sqlite3'):
        try:
            with ro_connect(path) as db:
                codes |= {row[0] for row in db.execute('SELECT DISTINCT symbol FROM fills')}
        except Exception:  # noqa: BLE001 - a missing or busy record only leaves its names for the next look
            continue
    return codes


def learn(nh, ask=None, clock=time.monotonic):
    """Name the traded codes nobody named (Toss, each code at most every 10 minutes); failures leave the code as it is."""
    missing = sorted(c for c in traded(nh) - set(load(nh)) if clock() - _asked.get(c, -RETRY_S) >= RETRY_S)
    if not missing:
        return {}
    _asked.update({c: clock() for c in missing})
    try:
        if ask is None:
            import toss_data
            if not toss_data.configured():
                return {}
            ask = lambda codes: toss_data.get('/api/v1/stocks', {'symbols': ','.join(codes)})
        new = {r['symbol']: r['name'] for r in ask(missing) or [] if r.get('symbol') in missing and r.get('name')}
        if new:
            path = Path(nh) / 'shared-etf-paper' / FILE
            write_json(path, _json(path) | new)
        return new
    except Exception:  # noqa: BLE001 - a name is display only
        return {}
