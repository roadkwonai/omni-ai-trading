"""선별 v2: after its fast stop, buy the code again at a held low (user decision 2026-11-17).

"낙폭과대라도 −2%에 손절하고 저점에 다시 매수": a v2 position stopped out goes on a watch list (v2-reentry.json in the
runner's folder). Each minute the runner tracks the lowest bar since the stop; when that low has held
oversold_v2_reentry_hold_min minutes and the price is oversold_v2_reentry_bounce_pct off it, the engine judges the code
again (Jev for the shared paper runner, vibe's score for vibe) and the runner buys back the stopped quantity with a new
−oversold_v2_stop_pct stop. At most oversold_v2_reentry_max re-buys per position, inside the position's own sessions,
09:10 ~ the v2 window close, not after 14:00 on its last session. Replay (daily and 5-minute) found this below the wide
stop; the user chose it, and the paper and virtual results decide what comes next.
"""
from __future__ import annotations

import json
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
FILE = 'v2-reentry.json'


def watch(root):
    try:
        data = json.loads((Path(root) / FILE).read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _save(root, data):
    path = Path(root) / FILE
    from config_store import write_json
    write_json(path, data, indent=None)


def stopped(root, code, *, opened, hold_days, qty, reentries, limit, facts=None, now=None):
    """A v2 position of `qty` was stopped out: watch it for a re-buy (unless its `limit` re-buys are used up)."""
    data = watch(root)
    if reentries >= int(limit) or qty < 1:
        data.pop(code, None)
    else:
        data[code] = {'stopped': (now or datetime.now(KST)).isoformat(), 'opened': opened, 'hold_days': int(hold_days),
                      'qty': int(qty), 'reentries': int(reentries), 'low': None, 'low_at': None, 'facts': facts or {}}
    try:
        _save(root, data)
    except OSError:
        pass  # no re-buy for it; the stop itself already went out


def prune(root, now, sessions_held):
    """The watches whose sessions are not over yet; the others (or unreadable ones) are dropped from the file. The runner
    calls it every cycle, so a watch that expires outside a v2 day does not stay (r3 X10)."""
    def alive(entry):
        try:
            return sessions_held(datetime.fromisoformat(entry['opened']).astimezone(KST).date(), now.date()) < int(entry['hold_days'])
        except (KeyError, TypeError, ValueError):
            return False
    data = watch(root)
    kept = {code: entry for code, entry in data.items() if alive(entry)}
    if kept != data:
        try:
            _save(root, kept)
        except OSError:
            pass  # tried again next cycle
    return kept


def clear(root, code):
    data = watch(root)
    if data.pop(code, None) is not None:
        try:
            _save(root, data)
        except OSError:
            pass


def open_for(entry, now, settings, sessions_held):
    """The watch may still buy now: inside the position's sessions, 09:10 ~ the v2 window close, and not after 14:00 on
    its last session."""
    from session_times import hhmm
    try:
        opened = datetime.fromisoformat(entry['opened']).astimezone(KST).date()
        held = sessions_held(opened, now.date())
    except (KeyError, TypeError, ValueError):
        return False
    clock = now.strftime('%H:%M')
    last = held >= int(entry['hold_days']) - 1
    from session_times import order_start, shifted
    day = now.date()
    return (held < int(entry['hold_days']) and order_start(day) <= clock < shifted(hhmm(settings['oversold_v2_until_hhmm']), day)
            and not (last and clock >= shifted('14:00', day)))


def bottom(root, code, rows, now, settings):
    """Track the low since the stop from the latest 1-minute rows (newest first; NH 'bsop_time', 'stck_lwpr',
    'stck_prpr') and say whether it has held long enough with the price off it. Saves the tracked low."""
    data = watch(root)
    entry = data.get(code)
    if not entry or not rows:
        return False
    since = datetime.fromisoformat(entry['stopped']).astimezone(KST)
    if since.date() != now.date():  # a stop from an earlier session: today's bars only
        since = now.replace(hour=9, minute=0, second=0, microsecond=0)
    low, low_at = entry.get('low'), entry.get('low_at')
    if low_at and datetime.fromisoformat(low_at).date() != now.date():
        low, low_at = None, None
    for row in reversed(rows):  # oldest first, so the earliest bar of an equal low is kept
        at = datetime.strptime(now.strftime('%Y%m%d') + row['bsop_time'], '%Y%m%d%H%M%S').replace(tzinfo=KST)
        bar_low = int(row['stck_lwpr'])
        if at >= since.replace(second=0, microsecond=0) and bar_low > 0 and (low is None or bar_low < low):
            low, low_at = bar_low, at.isoformat()
    if (low, low_at) != (entry.get('low'), entry.get('low_at')):
        data[code] = {**entry, 'low': low, 'low_at': low_at}
        try:
            _save(root, data)
        except OSError:
            pass
    if low is None:
        return False
    held = (now - datetime.fromisoformat(low_at)).total_seconds() / 60
    return held >= settings['oversold_v2_reentry_hold_min'] and int(rows[0]['stck_prpr']) >= low * (1 + settings['oversold_v2_reentry_bounce_pct'] / 100)
