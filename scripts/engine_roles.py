"""Which engine is main and where each engine's trading ledger lives (user decision 2026-11-12).

The main engine (accounts.json paper-shared.engine) trades the shared NH paper account (shared-etf-paper). The other
engine keeps trading as the secondary on a virtual broker (virtual_paper_broker), with the same runner code, so its
data keeps accumulating and it can take over as main later:
  jev  secondary -> jev-virtual  (runner retired 2026-11-17; folder moved to ~/.vibe-trading/archive/2026-11-vibe-intraday on 11-19)
  vibe secondary -> vibe-paper   (intraday vibe stopped 2026-11-17; folder moved to the same archive on 11-19)
Readers (live mirror, the other engine's reference view, dashboards) ask here instead of hard-coding a folder.
"""
from __future__ import annotations

from pathlib import Path

ENGINES = ('jev', 'vibe')
SHARED = 'shared-etf-paper'
SECONDARY = {'jev': 'jev-virtual', 'vibe': 'vibe-paper'}


def main_engine(nhplug):
    from accounts import load
    return load(Path(nhplug) / 'accounts.json')['paper-shared']['engine']


def ledger_root(nhplug, engine):
    """Folder whose orders.sqlite3/account-state.json are `engine`'s trades now (None for an unknown engine)."""
    if engine not in ENGINES:
        return None
    return Path(nhplug) / (SHARED if engine == main_engine(nhplug) else SECONDARY[engine])


def scoreboard(nhplug):
    """Per engine: role, ledger and closed-trade results; plus settled 30-minute calls per engine (shared research DB).
    Evidence for choosing the main engine; small samples and virtual fills (secondary) are not proof of profitability."""
    from track_record import settled_decisions, trade_results
    nhplug, main = Path(nhplug), main_engine(nhplug)
    board = {}
    for engine in ENGINES:
        root = ledger_root(nhplug, engine)
        row = {'role': 'main' if engine == main else 'secondary', 'ledger': root.name,
               'fills': 'paper' if engine == main else 'virtual'}
        try:
            row.update(trade_results(root / 'orders.sqlite3'))
        except Exception:  # a missing or unreadable ledger leaves the row without results
            pass
        board[engine] = row
    try:
        calls = settled_decisions(nhplug / SHARED / 'market-research.sqlite3')
        for engine in ENGINES:
            board[engine]['settled_calls'] = {k: v for k, v in calls.items() if k.split(' ')[0] == engine}
    except Exception:
        pass
    return board
