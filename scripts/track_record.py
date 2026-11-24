"""Jev's own track record, summarised for Jev and the dashboard (reference only; never an order gate).

- trades: verified paper fills in orders.sqlite3 -> realized return of each sell vs the moving-average cost.
- direction: the learning service's prequential counts in paper-model.sqlite3 -> how often a Jev pick rose afterwards.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from sqlite_ro import ro_connect

_CACHE = {}


def outcome_timing_filter(db):
    """Exclude vibe outcomes whose publication time was not verified."""
    columns = {row[1] for row in db.execute('PRAGMA table_info(decision_outcomes)')}
    if 'key' not in columns:
        return "engine != 'vibe'"
    return "(engine != 'vibe' OR key GLOB 'vibe_available:*')"


def unverified_vibe_outcomes(db, day=None):
    """Count retained legacy results that must not enter performance summaries."""
    columns = {row[1] for row in db.execute('PRAGMA table_info(decision_outcomes)')}
    missing = "(key IS NULL OR key NOT GLOB 'vibe_available:*')" if 'key' in columns else '1'
    period = ' AND time LIKE ?' if day is not None else ''
    return db.execute(f"SELECT COUNT(*) FROM decision_outcomes WHERE engine='vibe' AND status='settled' AND {missing}{period}",
                      (day + '%',) if day is not None else ()).fetchone()[0]


def trade_results(orders_db):
    books, sells = {}, []
    with ro_connect(orders_db) as db:
        for stamp, symbol, side, qty, price in db.execute('SELECT time,symbol,side,quantity,price FROM fills ORDER BY rowid'):
            book = books.setdefault(symbol, [0, 0.0])
            if side == 'BUY':
                book[1] = (book[1] * book[0] + price * qty) / (book[0] + qty)
                book[0] += qty
            elif book[0] > 0:
                sells.append({'time': stamp[:16], 'symbol': symbol, 'qty': qty,
                              'return_pct': round((price / book[1] - 1) * 100, 2)})
                book[0] -= qty
    wins = sum(s['return_pct'] > 0 for s in sells)
    return {'closed_sells': len(sells), 'wins': wins, 'losses': sum(s['return_pct'] < 0 for s in sells),
            'avg_return_pct': round(sum(s['return_pct'] for s in sells) / len(sells), 2) if sells else None,
            'recent': sells[-5:]}


def direction_results(model_db):
    with ro_connect(model_db) as db:
        result = {}  # the learning service keeps only its latest run, in time order: the last row per symbol is its count
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='jev_predictions_latest'").fetchone():
            rows = db.execute('SELECT symbol, alpha, beta FROM jev_predictions_latest WHERE rowid IN '
                              '(SELECT MAX(rowid) FROM jev_predictions_latest GROUP BY symbol)')
        else:  # a model file written before 11/19 has only the old per-run table: read its last run
            run = db.execute('SELECT run_digest FROM jev_predictions ORDER BY rowid DESC LIMIT 1').fetchone() \
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='jev_predictions'").fetchone() else None
            if not run:
                return {}
            rows = db.execute('''SELECT symbol, alpha, beta FROM jev_predictions WHERE run_digest=? AND rowid IN
                   (SELECT MAX(rowid) FROM jev_predictions WHERE run_digest=? GROUP BY symbol)''', (run[0], run[0]))
        for symbol, alpha, beta in rows:
            if type(alpha) is int and type(beta) is int and alpha + beta > 2:
                result[symbol] = {'picks_rose_after': alpha - 1, 'picks_fell_after': beta - 1}
    return result


def settled_decisions(path):
    """{engine kind: {n, hit_rate_30m, avg_return_30m_pct, avg_vs_kodex200_30m_pct}} from position_paths.decision_outcomes."""
    if not Path(path).is_file():
        return {}
    with ro_connect(path) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='decision_outcomes'").fetchone():
            return {}
        timing = outcome_timing_filter(db)
        rows = db.execute(f"""SELECT engine || ' ' || kind, COUNT(*), AVG(direction * r30 > 0), AVG(direction * r30), AVG(direction * (r30 - b30))
                             FROM decision_outcomes WHERE status = 'settled' AND r30 IS NOT NULL AND b30 IS NOT NULL AND {timing} GROUP BY 1""")
        return {key: {'n': n, 'hit_rate_30m': round(hit, 2), 'avg_return_30m_pct': round(ret, 3), 'avg_vs_kodex200_30m_pct': round(alpha, 3)}
                for key, n, hit, ret, alpha in rows}


def summary(root, now=None, ttl=300):
    """Cached for ttl seconds; any unreadable source is simply left out."""
    now = now or datetime.now()
    cached = _CACHE.get(str(root))
    if cached and 0 <= (now - cached[0]).total_seconds() < ttl:
        return cached[1]
    root, result = Path(root), {}
    try:
        result['paper_trades'] = trade_results(root / 'orders.sqlite3')
    except (OSError, sqlite3.Error, TypeError, ZeroDivisionError):
        pass
    try:
        direction = direction_results(root / 'paper-model.sqlite3')
        if direction:
            result['pick_direction_research'] = direction
    except (OSError, sqlite3.Error):
        pass
    try:
        settled = settled_decisions(root / 'market-research.sqlite3')
        if settled:
            result['settled_calls'] = settled
        with ro_connect(root / 'market-research.sqlite3') as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='decision_outcomes'").fetchone():
                excluded = unverified_vibe_outcomes(db)
                if excluded:
                    result['vibe_legacy_settled_excluded'] = excluded
                    result['vibe_settlements_note'] = '게시 시각을 확인할 수 없는 기존 vibe 정산은 성과 집계에서 제외했습니다.'
    except (OSError, sqlite3.Error):
        pass
    try:
        from research_store import history
        reviews = history('daily_review', 3, root / 'market-research.sqlite3')
        if reviews:
            result['recent_daily_lessons'] = {r['day']: r['lessons'] for r in reviews if r.get('day')}
    except (OSError, sqlite3.Error, KeyError, TypeError):
        pass
    if result:
        result['note'] = ('Your own past paper results, how often past picks rose afterwards, after-close lessons, and settled calls '
                          '(30 minutes later, in the called direction, and versus KODEX 200). Small samples; context only, not a rule.')
    _CACHE[str(root)] = (now, result)
    return result


def engine_results(nhplug, engine, now):
    """{'its_role', 'its_paper_trades', 'its_holdings'} of `engine`'s current ledger (engine_roles), what is available."""
    import json
    from engine_roles import ledger_root, main_engine
    out = {}
    try:
        root = ledger_root(nhplug, engine)
        out['its_role'] = 'main' if main_engine(nhplug) == engine else 'secondary'
    except Exception:
        return out
    try:
        trades = trade_results(root / 'orders.sqlite3')
        if trades['closed_sells']:
            out['its_paper_trades'] = trades
    except (OSError, sqlite3.Error, TypeError, ZeroDivisionError):
        pass
    try:
        state = json.loads((root / 'account-state.json').read_text(encoding='utf-8'))
        if str(state.get('time', ''))[:10] == now.strftime('%Y-%m-%d') and state.get('holdings'):
            out['its_holdings'] = {c: {k: h.get(k) for k in ('rsdl_qty', 'phs_pr', 'pft_rt')} for c, h in state['holdings'].items()}
    except (OSError, ValueError, AttributeError):
        pass
    return out


JEV_STRATEGY = ('Jev (image model) picks one candidate a minute from 1-minute charts, news, investor flows and the market '
                'regime; a buy needs confidence >= entry_confidence plus index direction and rising volume (or a watchlist '
                'strength / new-listing entry). Split buys of about first_buy_krw at -add_step_pct steps, up to '
                'per-symbol budget. Exits: defensive (data target or take_profit_half/all) or trend (half + trailing) by '
                'regime, stop_loss_pct, Jev SELL >= sell_confidence, no new buys from 14:30, flatten 14:40 unless the '
                '12-condition closing-trade check holds it overnight.')


def jev_view(runtime=None, now=None, max_age_minutes=30):
    """What vibe-trading may consider from Jev (reference only): Jev's latest candidate view (<= 30 minutes), its trading
    results and holdings (role main = shared paper, secondary = jev-virtual), its strategy and current settings, and
    its settled 30-minute calls (the strategy part is always there)."""
    import json
    from collections import deque
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    from engine_roles import SHARED, ledger_root
    nhplug = Path(runtime or Path.home() / '.vibe-trading') / 'nhplug'
    now = now or datetime.now(ZoneInfo('Asia/Seoul'))
    view = engine_results(nhplug, 'jev', now)
    try:
        with (ledger_root(nhplug, 'jev') / 'events.jsonl').open(encoding='utf-8') as stream:
            for line in deque(stream, maxlen=300):
                event = json.loads(line)
                candidate = (event.get('jev') or {}).get('candidate') if event.get('event') == 'jev_shadow' else None
                at = datetime.fromisoformat(event['time']) if candidate else None
                if at and timedelta(0) <= now - at <= timedelta(minutes=max_age_minutes):
                    view['latest_view'] = {'time': event['time'][:19], 'choice': candidate.get('choice'),
                                           'confidence': candidate.get('confidence')}
                    # every symbol's probability in that pick (user request 2026-11-13: vibe weighs Jev per symbol)
                    from etf_universe import SYMBOLS
                    probabilities = {SYMBOLS.get(k, k): v for k, v in (candidate.get('probabilities') or {}).items() if k != 'WAIT'}
                    if probabilities:
                        view['latest_view']['probabilities_by_code'] = probabilities
                        view['latest_view']['wait_probability'] = (candidate.get('probabilities') or {}).get('WAIT')
                if event.get('event') == 'jev_holding_decision':
                    at = datetime.fromisoformat(event['time'])
                    if timedelta(0) <= now - at <= timedelta(minutes=max_age_minutes):
                        view.setdefault('holding_views', {})[event.get('symbol')] = {
                            'time': event['time'][11:19], 'choice': event.get('choice'), 'confidence': event.get('confidence')}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        from strategy_settings import read_settings
        view['strategy'] = {'rules': JEV_STRATEGY, 'settings': read_settings(nhplug / SHARED)[0]}
    except (OSError, ValueError, ImportError):
        pass
    try:
        calls = {k: v for k, v in settled_decisions(nhplug / SHARED / 'market-research.sqlite3').items() if k.startswith('jev ')}
        if calls:
            view['settled_calls'] = calls
    except (OSError, sqlite3.Error):
        pass
    view['note'] = ('Another analyst (Jev): its latest pick with every symbol\'s probability (probabilities_by_code; WAIT = '
                    'no trade), its HOLD/SELL view on each holding (holding_views), its own trading results and holdings, its '
                    'rules and settings, and how its calls did 30 minutes later. A second opinion, not an instruction.')
    return view
