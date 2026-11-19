"""Exit levels of a held position, per runner (user 2026-11-17: Jev and vibe branch explicitly; the rest is shared).

The branch is the runner that keeps the ledger folder, not the engine's name: the shared account is always traded by
nh_paper_isolated, (Jev, the only main engine since 2026-11-17);
only vibe-paper is kept by vibe_runner (engine_roles.SECONDARY['vibe']).
  paper  the plan saved at entry (exit-plans.json: defensive / trend / ipo / oversold, with later trend or weak-market
         changes); a position without one uses the current settings' defensive plan.
  vibe   the current settings' defensive plan for every ordinary position (vibe_runner keeps no per-position plan);
         낙폭과대 positions follow their own absolute prices in oversold-plans.json.
Shared: the index-regime stop tightening by position direction (nh_paper_isolated.regime_stop_levels) and Decimal
price comparisons. vibe_runner and the fast-exit watcher read the levels here; the paper runner applies its plans in
nh_paper_isolated itself, and the live mirror takes only ORDINARY_SOURCES / runner_of from here: its stop percent
(nh_live_follow.stop_percent_for_positions, also the dashboard card) reuses nh_paper_isolated.tightening_regimes.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

# Buy routes whose positions the runner holds as ordinary strategy positions (defensive/trend levels, regime
# tightening); 낙폭과대 and 신규주 routes have their own plans. The paper runner records 'vibe' in vibe-main mode and
# tightens hand-added ('manual') shares like any other.
ORDINARY_SOURCES = {'paper': frozenset({'jev', 'vibe', 'watchlist_strength', 'split', 'manual'}),
                    'vibe': frozenset({'vibe', 'vibe-trading', 'jev_fast'})}


def runner_of(root):
    """'vibe' for the folder vibe_runner keeps, else 'paper' (nh_paper_isolated)."""
    from engine_roles import SECONDARY
    return 'vibe' if Path(root).name == SECONDARY['vibe'] else 'paper'


def defensive_plan(settings):
    """The defensive plan of the current strategy settings."""
    return {'profile': 'defensive', 'stop_pct': settings['stop_loss_pct'],
            'half_pct': settings['take_profit_half_pct'], 'all_pct': settings['take_profit_all_pct']}


def plan_for(root, symbol, settings):
    """(plan, special): the plan a position in the ledger folder `root` is held under, or for a vibe_runner 낙폭과대
    position (None, its oversold-plans.json entry with absolute target_price / stop_price)."""
    if runner_of(root) == 'vibe':
        from vibe_runner import oversold_plans
        special = oversold_plans(root).get(symbol)
        return (None, special) if special else (defensive_plan(settings), None)
    from nh_paper_isolated import exit_plans
    return exit_plans(root).get(symbol) or defensive_plan(settings), None


def levels(plan, view, index, symbol):
    """{'stop', 'half', 'all', 'trail'} price multiples of the average, with the regime tightening."""
    from nh_paper_isolated import regime_stop_levels
    return regime_stop_levels(plan, view, index, symbol)


def stop_hit(avg, bid, lv):
    """bid at or below avg × stop level (Decimal: a float edge must not flip the decision between processes)."""
    return Decimal(str(bid)) <= Decimal(str(avg)) * lv['stop']


def target_hit(avg, bid, lv, key):
    """bid at or above avg × the 'half' or 'all' level; a trend plan has no fixed final target (None never hits)."""
    return lv.get(key) is not None and Decimal(str(bid)) >= Decimal(str(avg)) * lv[key]
