"""Position sizing by holdings and market direction (user decision 2026-11-17).

A buy is not always the full tranche up to the per-symbol limit: it is the tranche times
  holding factor  1.0 while the symbol holds under half of its per-symbol limit, then (1 - held share), at least 0.25
  market factor   1.0 when the index regime matches the buy's direction, size_market_flat_pct (75%) in a 횡보장,
                  size_market_against_pct (50%) against it (long in a 하락장, inverse in a 상승장); 1.0 when the
                  regime is unknown. Both shares are settings (user 2026-11-24).
clamped to 25%..100% of the tranche. A held position whose index regime turns against it after it was bought sells
half once (regime_trim). Each part is a strategy setting switch (size_holding_on, size_market_on, regime_trim_on).
Routes with their own sizing (낙폭과대, 선별 v2, new listings) are not resized here.
"""
from __future__ import annotations

import json
from pathlib import Path

MIN_FACTOR = 0.25
TRIMS = 'regime-trims.json'


def holding_factor(held_cost, cap):
    if not cap or cap <= 0 or not held_cost or held_cost <= 0:
        return 1.0
    share = held_cost / cap
    return 1.0 if share < 0.5 else max(MIN_FACTOR, 1.0 - share)


def regime_of(view, index):
    return ((view or {}).get(index) or {}).get('regime')


def against(regime, short):
    return regime == ('상승장' if short else '하락장')


def market_factor(view, index, short, settings=None):
    settings = settings or {}
    regime = regime_of(view, index)
    if regime is None:
        return 1.0
    if regime == '횡보장':
        return settings.get('size_market_flat_pct', 75) / 100
    return settings.get('size_market_against_pct', 50) / 100 if against(regime, short) else 1.0


def buy_factor(settings, view, index, short, held_cost, cap):
    """(factor, parts) for one buy; parts are recorded with the order for later review."""
    parts = {}
    if settings.get('size_holding_on', 1):
        parts['holding'] = holding_factor(held_cost, cap)
    if settings.get('size_market_on', 1):
        parts['market'] = market_factor(view, index, short, settings)
    factor = 1.0
    for value in parts.values():
        factor *= value
    return max(MIN_FACTOR, min(1.0, factor)), parts


def position_key(db, symbol, quantity):
    """The rowid of the first buy of the position now held (walking fills back to flat); None when unknown."""
    left = quantity
    first = None
    for rowid, side, qty in db.execute('SELECT rowid, side, quantity FROM fills WHERE symbol=? ORDER BY rowid DESC', (symbol,)):
        if side == 'BUY':
            left -= qty
            first = rowid
        else:
            left += qty
        if left == 0:
            return first
        if left < 0:
            return None
    return None


def _load(root):
    try:
        data = json.loads((Path(root) / TRIMS).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(root, data):
    path = Path(root) / TRIMS
    from config_store import write_json
    write_json(path, data, indent=None)


def regime_trim(root, db, symbol, quantity, view, index, short, settings):
    """Shares to sell now because the regime turned against the position after it was bought: half, once per position.
    The regime first seen while holding is remembered as its entry regime; an unknown regime never trims."""
    if not settings.get('regime_trim_on', 1) or quantity < 2:
        return 0
    key = position_key(db, symbol, quantity)
    regime = regime_of(view, index)
    if key is None or regime is None:
        return 0
    data = _load(root)
    entry = data.get(symbol)
    if not isinstance(entry, dict) or entry.get('position') != key:
        entry = {'position': key, 'entry_regime': regime, 'trimmed': False}
        data[symbol] = entry
        _save(root, {s: e for s, e in data.items() if s == symbol or isinstance(e, dict)})
    if entry['trimmed'] or against(entry['entry_regime'], short) or not against(regime, short):
        return 0
    return quantity // 2


def prune(root, held):
    """Drop the entries of symbols no longer held (a new position starts its own entry anyway); the file kept every
    symbol ever seen (r3 X10)."""
    data = _load(root)
    kept = {s: e for s, e in data.items() if s in held}
    if kept != data:
        _save(root, kept)


def mark_trimmed(root, symbol):
    data = _load(root)
    if isinstance(data.get(symbol), dict):
        data[symbol]['trimmed'] = True
        _save(root, data)
