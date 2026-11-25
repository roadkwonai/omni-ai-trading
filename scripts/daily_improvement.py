"""Facts for the after-close improvement review of the shared paper account (read-only; never orders or edits).

The daily scheduled task "장 마감 개선안 연구" runs this, reads the JSON and writes proposals; this script only measures:
every round trip of the day with its entry route, exit, best/worst point and the move after the exit; results by entry
route and exit reason; how often each restriction held a buy back; order-guard refusals with the day's buy total;
same-day re-entries; late entries; orders sized below the tranche; runner errors and stops; Jev call outcomes and
their hit rate by confidence band over the last N days (signal_calibration); every add's result and a size hint
(add_sizing); Jev's entry probabilities summed by direction (direction_mass); what blocked strength picks and untraded new listings did afterwards (blocked_outcomes); the
vibe virtual book; the last N trading days; and the settings in force.

  PYTHONPATH=agent:scripts .venv/bin/python scripts/daily_improvement.py [--day YYYY-MM-DD] [--days 10]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from sqlite_ro import day_rows, ro_connect

KST = ZoneInfo('Asia/Seoul')
RUNTIME = Path.home() / '.vibe-trading/nhplug'
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'streamlit_namuh'))  # engine_compare: fills -> decisions


def _stats(items, key):
    groups = defaultdict(list)
    for e in items:
        groups[e.get(key) or '기록 없음'].append(e)
    return {name: {'n': len(g), 'wins': sum(e['pnl'] > 0 for e in g), 'realized_krw': round(sum(e['pnl'] for e in g)),
                   'avg_return_pct': round(sum(e['return_pct'] or 0 for e in g) / len(g), 2)} for name, g in groups.items()}


def paths_today(root, day):
    """position_paths rows opened or closed on the day keyed by (symbol, opened minute): best/worst point while held, move after exit."""
    try:
        with ro_connect(Path(root) / 'market-research.sqlite3') as db:
            db.row_factory = sqlite3.Row
            return {(r['symbol'], r['opened'][:16]): dict(r) for r in db.execute(
                'SELECT * FROM position_paths WHERE opened LIKE ? OR closed LIKE ?', (day + '%', day + '%'))}
    except sqlite3.Error:
        return {}


def round_trips(rows, day, paths, events=()):
    import engine_compare as ec
    submitted = [e for e in events if e.get('event') == 'submitted' and ('high_gap_pct' in e or 'entry_timing' in e)]
    out = []
    for e in ec.episodes(rows):
        carried = not e['open'].startswith(day)
        if carried and not (e['close'] or '').startswith(day):
            continue
        path = paths.get((e['symbol'], e['open'][:16]), {})
        held = None
        if e['close']:
            held = round((datetime.fromisoformat(e['close']) - datetime.fromisoformat(e['open'])).total_seconds() / 60)
        buy = next((r for r in rows if r['time'] == e['open'] and r['symbol'] == e['symbol']), {})
        sent = [x for x in submitted if x.get('symbol') == e['symbol'] and x['time'] <= e['open']]  # the order behind the first fill
        context = sent[-1] if sent else {}
        out.append({'symbol': e['symbol'], 'open': f"{e['open'][5:10]} {e['open'][11:19]}" if carried else e['open'][11:19], 'close': (e['close'] or '')[11:19] or None, 'carried': carried,
                    'held_min': held, 'entry_route': e['entry'], 'entry_basis': buy.get('basis'), 'exit': e['exit'] or None,
                    'pnl': round(e['pnl']), 'return_pct': e['return_pct'], 'cost': round(e['cost']),
                    'entry_vs_day_high_pct': context.get('high_gap_pct'), 'entry_vs_open_pct': context.get('open_gap_pct'),
                    'entry_timing': context.get('entry_timing'),
                    'mfe_pct': path.get('mfe_pct'), 'mae_pct': path.get('mae_pct'), 'after_30m_pct': path.get('after_30m_pct'),
                    # how much of the best point while held was given back (only meaningful when the best was a gain)
                    'left_on_table_pct': (round(path['mfe_pct'] - e['return_pct'], 2)
                                          if (path.get('mfe_pct') or 0) > 0 and e['return_pct'] is not None else None)})
    return out


def reentries(trips):
    """Same symbol bought again the same day after a closed round trip: count and P&L of the later trips."""
    seen, later = set(), []
    for t in sorted(trips, key=lambda t: (not t.get('carried'), t['open'])):  # a carried lot came first
        if t['symbol'] in seen:
            later.append(t)
        if t['close']:
            seen.add(t['symbol'])
    return {'n': len(later), 'realized_krw': sum(t['pnl'] for t in later), 'trips': [f"{t['symbol']} {t['open']} {t['pnl']:+,}" for t in later]}


def guard_usage(day, events=()):
    """The shared order guard's buy total and new symbols for the day, the limits the runner applied over the day
    (strategy_settings records; one without limits means the defaults then) and when each refusal happened."""
    from accounts import DEFAULT_PAPER_LIMITS
    timeline, last = [], None
    for e in events:
        if e.get('event') == 'strategy_settings' and isinstance(e.get('settings'), dict):
            limits = {k: e['settings'].get(k, v) for k, v in DEFAULT_PAPER_LIMITS.items()}
            if limits != last:
                timeline.append({'from': e['time'][11:19], **limits})
                last = limits
    try:
        with ro_connect(RUNTIME / 'orders.sqlite3') as db:
            total, symbols = db.execute('SELECT COALESCE(SUM(notional),0), COUNT(DISTINCT symbol) FROM swing_buys WHERE day=?',
                                        (day.replace('-', ''),)).fetchone()
    except sqlite3.Error:
        total, symbols = None, None
    return {'buy_total_krw': total, 'new_symbols': symbols, 'limits_over_the_day': timeline,
            'refusal_times': [e['time'][11:19] for e in events if e.get('reason') == 'order_guard_refused']}


def runner_health(events, root, day):
    errors = Counter(f"{e['event']}:{e.get('reason') or e.get('error_type') or ''}" for e in events if str(e.get('event', '')).endswith('error'))
    stops = [e['time'][11:19] for e in events if e.get('event') == 'stopped']
    crashes = []
    try:
        lines = (Path(root) / 'com.vibetrading.shared-etf-paper.log').read_text(errors='replace', encoding='utf-8').splitlines()
    except OSError:
        lines = []
    last, held = '', []  # the last status time; crash lines since it
    for line in lines:
        if line.startswith('{"status"'):
            try:
                at = str(json.loads(line).get('time') or '')
            except (ValueError, AttributeError):
                continue
            # crashes since the last status belong to this status's day: one before a day's first status is that day's start
            if at[:10] == day:
                where = f'{last[11:19]} 이후' if last[:10] == day else f'{at[11:19]} 이전'
                crashes += [f'{where} · {c[:120]}' for c in held]
            last, held = at, []
        elif line.startswith(('ValueError', 'RuntimeError', 'TimeoutError', 'KeyError', 'TypeError', 'sqlite3.')):
            held.append(line)
    if last[:10] == day:  # after the day's last status (nothing logged since)
        crashes += [f'{last[11:19]} 이후 · {c[:120]}' for c in held]
    return {'error_events': dict(errors), 'stopped_at': stops, 'crashes': crashes[-10:]}


def undersized_buys(buys, tranche, events):
    """Buys under half a tranche that no approved rule sized down: the holdings/market sizing, an add (add_size_pct)
    and a careful re-entry shrink a buy on purpose, so a buy whose order record carries one is left out (review 4)."""
    sent = [e for e in events if e.get('event') == 'submitted']
    out = []
    for r in buys:
        if not tranche or r['amount'] >= tranche * 0.5 or r['path'] == '분할 추가 2차':
            continue
        order = ([e for e in sent if e.get('symbol') == r['symbol'] and str(e.get('time', '')) <= r['time']] or [{}])[-1]
        if order.get('sizing') or order.get('add_reason') not in (None, 'first') or order.get('careful_reentry'):
            continue
        out.append(f"{r['symbol']} {r['time'][11:19]} {round(r['amount']):,}원")
    return out


def jev_calls(root, day):
    try:
        with ro_connect(Path(root) / 'market-research.sqlite3') as db:
            from track_record import outcome_timing_filter
            timing = outcome_timing_filter(db)
            return {f'{engine} {kind}': {'n': n, 'hit_rate_30m': round(hit, 2), 'avg_vs_kodex200_30m_pct': round(alpha, 3)}
                    for engine, kind, n, hit, alpha in db.execute(
                        f"""SELECT engine, kind, COUNT(*), AVG(direction * r30 > 0), AVG(direction * (r30 - b30))
                           FROM decision_outcomes WHERE time LIKE ? AND status='settled' AND r30 IS NOT NULL AND b30 IS NOT NULL AND {timing} GROUP BY 1, 2""", (day + '%',))}
    except sqlite3.Error:
        return {}


BUCKETS = ((0.8, '0.8 이상'), (0.7, '0.7~0.8'), (0.6, '0.6~0.7'), (0.4, '0.4~0.6'), (0.2, '0.2~0.4'), (-9, '0.2 미만'))


def signal_calibration(root, day, days=10):
    """적중률 추적 (user 2026-11-17): every settled Jev pick / Jev holding view / vibe score by confidence band, for the
    day and the last `days` days with decisions: n, 30-minute hit rate (moved the way the call said), mean directional
    30-minute move and vs KODEX 200. `calibrated` says whether a higher band hit more often (None under 20 calls a band).
    11/17 morning: Jev BUY at 0.8+ did worse than at 0.6~0.8 and holding SELLs were followed by rises."""
    case = ' '.join(f"WHEN value >= {low} THEN '{name}'" for low, name in BUCKETS)
    try:
        with ro_connect(Path(root) / 'market-research.sqlite3') as db:
            from track_record import outcome_timing_filter
            timing = outcome_timing_filter(db)
            dates = [r[0] for r in db.execute("SELECT DISTINCT substr(time,1,10) FROM decision_outcomes WHERE substr(time,1,10) <= ? "
                                               "ORDER BY 1 DESC LIMIT ?", (day, days))]
            out = {}
            for label, since in (('today', day), (f'last_{len(dates)}_days', dates[-1] if dates else day)):
                rows = db.execute(f"""SELECT engine || ' ' || kind || CASE WHEN kind = 'holding' THEN ' ' || choice ELSE '' END,
                                             CASE {case} END, COUNT(*), AVG(direction * r30 > 0), AVG(direction * r30), AVG(direction * (r30 - b30))
                                      FROM decision_outcomes WHERE substr(time,1,10) BETWEEN ? AND ? AND status='settled'
                                      AND r30 IS NOT NULL AND b30 IS NOT NULL AND {timing} GROUP BY 1, 2""", (since, day)).fetchall()
                table = defaultdict(dict)
                for signal, band, n, hit, move, alpha in rows:
                    table[signal][band] = {'n': n, 'hit_rate_30m': round(hit, 2), 'avg_move_30m_pct': round(move, 3),
                                           'avg_vs_kodex200_30m_pct': round(alpha, 3)}
                out[label] = {signal: {'bands': bands, 'calibrated': _calibrated(bands)} for signal, bands in table.items()}
            return out
    except sqlite3.Error:
        return {}


def news_gap_outcomes(root, day, days=10):
    """뉴스 없는 판단 비교 (user 2026-11-24): Jev's entry and holding answers given with no fresh market headline
    (model_inputs state.news_status) beside the rest: how many, how many settled, 30-minute hit rate and mean directional
    move. Neighbouring answers overlap, so compare after 10 trading days. Observation only, no rule."""
    try:
        with ro_connect(Path(root) / 'market-research.sqlite3') as db:
            dates = [r[0] for r in db.execute("SELECT DISTINCT substr(time,1,10) FROM model_inputs WHERE substr(time,1,10) <= ? "
                                               "ORDER BY 1 DESC LIMIT ?", (day, days))]
            out = {}
            for label, since in (('today', day), (f'last_{len(dates)}_days', dates[-1] if dates else day)):
                table = defaultdict(dict)
                for question, missing, answers, settled, hit, move in db.execute(
                        "SELECT m.question, json_extract(m.inputs, '$.state.news_status') IS NOT NULL, COUNT(DISTINCT m.digest), COUNT(o.r30), "
                        "AVG(o.direction * o.r30 > 0), AVG(o.direction * o.r30) FROM model_inputs m LEFT JOIN decision_outcomes o "
                        "ON o.input_digest = m.digest AND o.engine = 'jev' AND o.status = 'settled' AND o.r30 IS NOT NULL "
                        "WHERE m.engine = 'jev' AND m.question IN ('candidate', 'holding') AND substr(m.time,1,10) BETWEEN ? AND ? "
                        "GROUP BY 1, 2", (since, day)):
                    table[question]['no_fresh_news' if missing else 'fresh'] = {
                        'answers': answers, 'settled': settled,
                        **({'hit_rate_30m': round(hit, 2), 'avg_move_30m_pct': round(move, 3)} if settled else {})}
                out[label] = dict(table)
            return out
    except sqlite3.Error:
        return {}


MASS_MIN, MASS_GAP = 0.5, timedelta(minutes=30)


def direction_mass_summary(root, day, days=10):
    """방향 합계 관찰 (user 2026-11-24): Jev's entry probabilities summed by direction (position_paths.direction_mass). For
    answers whose long or inverse sum reached MASS_MIN: how many, on how many days, how many had no single choice that
    high (`split`), and the KODEX 200 move 30 minutes later in that direction. Answers come every minute and their 30-minute windows
    overlap, so `separate` counts only answers at least 30 minutes apart; judge by that. Observation only, no rule."""
    try:
        with ro_connect(Path(root) / 'market-research.sqlite3') as db:
            dates = [r[0] for r in db.execute('SELECT DISTINCT substr(time,1,10) FROM direction_mass WHERE substr(time,1,10) <= ? ORDER BY 1 DESC LIMIT ?', (day, days))]
            rows = db.execute('SELECT time, "long", inverse, top_probability, k200_30 FROM direction_mass WHERE substr(time,1,10) BETWEEN ? AND ? ORDER BY time',
                              (dates[-1] if dates else day, day)).fetchall()
    except sqlite3.Error:
        return {}
    out = {}
    for label, part in (('today', [r for r in rows if r[0][:10] == day]), (f'last_{len(dates)}_days', rows)):
        out[label] = {'answers': len(part)}
        for side, index, sign in (('long', 1, 1), ('inverse', 2, -1)):
            picked, apart, last = [r for r in part if r[index] >= MASS_MIN], [], None
            settled = [r for r in picked if r[4] is not None]
            for r in settled:
                at = datetime.fromisoformat(r[0])
                if last is None or at - last >= MASS_GAP:
                    apart.append(r)
                    last = at
            stats = lambda found: {'n': len(found), **({'avg_move_30m_pct': round(sum(sign * r[4] for r in found) / len(found), 3),  # noqa: E731
                                                        'hit_rate_30m': round(sum(sign * r[4] > 0 for r in found) / len(found), 2)} if found else {})}
            out[label][side] = {'n': len(picked), 'days': len({r[0][:10] for r in picked}),
                                'split': sum(r[3] is None or r[3] < MASS_MIN for r in picked), 'settled': stats(settled), 'separate': stats(apart)}
    return out


def add_sizing(rows, day, days, settings, events, calibration):
    """추가 매수 감사 (user 2026-11-17): every add (a buy into a held position) of the last `days` trading days with
    where the position was sold after it (sell-weighted price after the add vs the add price), today's add gates and a
    rule-of-thumb hint for add_size_pct. Proposals only: the hint never changes a setting."""
    dates = sorted({r['time'][:10] for r in rows if r['time'][:10] <= day})[-days:]
    held, adds = defaultdict(int), []
    for r in sorted(rows, key=lambda r: r['time']):
        sym = r['symbol']
        if r['side'] == 'BUY':
            if held[sym] > 0 and r['time'][:10] in dates:
                adds.append({'day': r['time'][:10], 'time': r['time'][11:19], 'symbol': sym, 'price': r['price'],
                             'amount': round(r['amount']), 'sold': 0, 'sold_value': 0.0, 'open': True})
            held[sym] += r['qty']
        else:
            held[sym] -= r['qty']
            for a in adds:
                if a['symbol'] == sym and a['open']:
                    a['sold'] += r['qty']; a['sold_value'] += r['qty'] * r['price']
                    a['open'] = held[sym] > 0
    for a in adds:
        a['to_exit_pct'] = round((a['sold_value'] / a['sold'] / a['price'] - 1) * 100, 2) if a['sold'] and not a['open'] else None
        del a['sold_value']
    done = [a['to_exit_pct'] for a in adds if a['to_exit_pct'] is not None]
    avg = round(sum(done) / len(done), 2) if done else None
    win = round(sum(x > 0 for x in done) / len(done), 2) if done else None
    size = settings.get('add_size_pct', 50)
    entry = ((calibration or {}).get(next((k for k in (calibration or {}) if k.startswith('last_')), ''), {}) or {}).get('jev entry') or {}
    if len(done) < 10:
        hint = f'표본 부족({len(done)}건, 10건 이상 필요): add_size_pct {size:g}% 유지'
    elif avg < 0:
        hint = f'추가 매수가 평균 {avg:+.2f}% (승률 {win:.0%}): add_size_pct {size:g}% → {max(10, size - 20):g}% 하향 또는 추가 매수 횟수 축소 검토'
    elif avg >= 0.3 and win >= 0.6:
        hint = f'추가 매수가 평균 {avg:+.2f}% (승률 {win:.0%}): add_size_pct {size:g}% → {min(100, size + 20):g}% 상향 검토'
    else:
        hint = f'추가 매수 평균 {avg:+.2f}% (승률 {win:.0%}): 뚜렷한 차이 없음, {size:g}% 유지'
    if entry.get('calibrated') is True:
        hint += ' · Jev 진입 신뢰도가 높을수록 더 맞음: 신뢰도 비례 크기 재검토 가능'
    return {'settings': {k: settings.get(k) for k in ('add_size_pct', 'add_conf_sizing_on', 'scale_max_adds', 'partial_sell_conf', 'partial_sell_pct')},
            'today_adds': sum(a['day'] == day for a in adds),
            'today_gates': {k: v for k, v in Counter(e.get('reason') for e in events if e.get('event') == 'v2_execution_gate'
                                                      and e.get('reason') in ('add_not_better', 'add_count_limit', 'split_limit')).items()},
            'partial_sells_today': sum(e.get('event') == 'v2_holding_decision' and e.get('choice') == 'SELL_PART' for e in events),
            'adds': adds, 'settled': len(done), 'avg_to_exit_pct': avg, 'win_rate': win, 'hint': hint}


def blocked_outcomes(root, day, days=10):
    """사후 추적 (user 2026-11-17): what the blocked strength picks and untraded new listings did afterwards
    (blocked_outcomes.py, filled by the learning watcher after the close), by kind and block reason, for the day and
    the last `days` days: n, mean 30-minute and to-close move, share that closed up. A negative mean says the filter
    kept money; a clearly positive one says it may be too strict (judge only with 10+ rows)."""
    try:
        with ro_connect(Path(root) / 'market-research.sqlite3') as db:
            dates = [r[0] for r in db.execute('SELECT DISTINCT day FROM blocked_outcomes WHERE day <= ? ORDER BY 1 DESC LIMIT ?', (day, days))]
            out = {}
            for label, since in (('today', day), (f'last_{len(dates)}_days', dates[-1] if dates else day)):
                out[label] = {f'{kind} {blocked}': {'n': n, 'avg_r30_pct': None if r30 is None else round(r30, 2),
                                                   'avg_to_close_pct': None if last is None else round(last, 2),
                                                   'closed_up': None if up is None else round(up, 2)}
                              for kind, blocked, n, r30, last, up in db.execute(
                                  'SELECT kind, blocked, COUNT(*), AVG(r30), AVG(r_last), AVG(r_last > 0) FROM blocked_outcomes '
                                  'WHERE day BETWEEN ? AND ? GROUP BY 1, 2 ORDER BY 3 DESC', (since, day))}
            return out
    except sqlite3.Error:
        return {}


def mirror_gaps(days=10):
    """모의–실계좌 차이 (user 2026-11-25, mirror_gap.py): per mirrored account, before costs; + slips mean better than the paper."""
    from accounts import load
    import mirror_gap
    return {key: mirror_gap.summary(RUNTIME, key, days) for key, r in load(RUNTIME / 'accounts.json').items()
            if r.get('kind') == 'live' and r.get('engine') == 'jev'}


def overnight_summary(root, day, days=10):
    """전날 재료 그림자 (user 2026-11-25): the moves from 09:10 of stocks with overnight news, by direction and price reaction."""
    from overnight_catalysts import summary
    return summary(Path(root) / 'market-research.sqlite3', day, days)


def ipo_shadow_summary(root, day, days=10):
    """신규주 그림자 기록 (user 2026-11-19, ipo_shadow.py): per variant (지금 규칙 / A. VWAP 기준가 / C. A + 눌림 재매수) the
    listing-days, replayed trades, summed % and win rate over the last `days` recorded days. A replay on Yahoo 1-minute bars,
    not fills; compare the variants only after 10+ listing-days."""
    import ipo_shadow
    try:
        return ipo_shadow.summary(Path(root) / 'market-research.sqlite3', day, days)
    except sqlite3.Error:
        return {}


def fade_summary(root, day, days=10):
    """수익 중 약화 그림자 (user 2026-11-25, fade_shadow.py): selling at the first weak look in profit against the actual sale.
    Judge a rule only after five trading days and 20+ positions; the live account sells about a minute later (`late`)."""
    import fade_shadow
    try:
        return fade_shadow.summary(Path(root) / 'market-research.sqlite3', day, days)
    except sqlite3.Error:
        return {}


def _calibrated(bands):
    """True when the hit rate rises with the band (bands of 20+ calls only), False when it does not, None if too few."""
    order = [name for _, name in BUCKETS][::-1]
    hits = [bands[name]['hit_rate_30m'] for name in order if bands.get(name, {}).get('n', 0) >= 20]
    return None if len(hits) < 2 else all(a <= b for a, b in zip(hits, hits[1:]))


def unverified_calls(root, day):
    try:
        from track_record import unverified_vibe_outcomes
        with ro_connect(Path(root) / 'market-research.sqlite3') as db:
            return unverified_vibe_outcomes(db, day)
    except sqlite3.Error:
        return None


def vibe_entry_study(root, day):
    """Keep exploratory, read-only evidence separate from verified performance."""
    try:
        from vibe_entry_study import load_and_analyze
        return {'status': 'ok', **load_and_analyze(root, day)}
    except (ImportError, OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return {'status': 'unavailable', 'note': '강도 진입 연구 자료를 확인할 수 없습니다.'}


def history(rows, day, days):
    """Realized P&L and round trips by day (and by entry route) for the last `days` trading days up to `day`."""
    import engine_compare as ec
    by_day = defaultdict(lambda: {'trips': 0, 'wins': 0, 'realized_krw': 0, 'by_route': defaultdict(int)})
    for e in ec.episodes(rows):
        if not e['close'] or e['close'][:10] > day:
            continue
        d = by_day[e['close'][:10]]
        d['trips'] += 1
        d['wins'] += e['pnl'] > 0
        d['realized_krw'] += round(e['pnl'])
        d['by_route'][e['entry'] or '기록 없음'] += round(e['pnl'])
    keep = sorted(by_day)[-days:]
    return [{'day': k, **{**by_day[k], 'by_route': dict(by_day[k]['by_route'])}} for k in keep]


def oversold_section(day, events, settings=None):
    """낙폭과대 반등 for the review: today's triggers and the engines' answers, the outcome log of the day (filled in
    after the close by the learning watch) and the last 20 sessions pooled (oversold_outcomes.summary)."""
    from research_store import history
    out = {'scans': [{k: e.get(k) for k in ('time', 'symbol', 'drop_pct', 'atr_multiple', 'stabilized', 'bounce_pct')}
                     for e in events if e.get('event') == 'oversold_scan'],
           'signals': [{k: e.get(k) for k in ('time', 'symbol', 'engine', 'choice', 'confidence', 'score', 'permitted')}
                       for e in events if e.get('event') == 'oversold_signal'],
           # 선별 v2 (paper since 2026-11-16): the live gate, its candidates, and the samples kept apart from trading
           'v2_gate': [{k: e.get(k) for k in ('time', 'kospi', 'kosdaq', 'nasdaq_prev', 'passed')}
                       for e in events if e.get('event') == 'oversold_v2_gate'],
           'v2_signals': [{k: e.get(k) for k in ('time', 'symbol', 'drop_pct', 'market_low_pct', 'rel_drop', 'stop_pct', 'hold_days')}
                          for e in events if e.get('event') == 'oversold_v2_signal']}
    try:
        import oversold_cases
        import oversold_replay
        out['v2_samples'] = oversold_replay.v2_samples(oversold_cases.load(), settings=settings)[:20]
    except Exception as exc:
        out['v2_samples_error'] = type(exc).__name__
    try:
        today = next((r for r in history('oversold_outcome', 10, RUNTIME / 'shared-etf-paper' / 'market-research.sqlite3')
                      if r.get('day') == day), None)
        out['outcomes_today'] = (today or {}).get('rows')
        from oversold_outcomes import summary
        pooled = summary(20)
        out['last_20_sessions'] = {'events': pooled['events'], 'groups': pooled['groups']}
    except Exception as exc:  # the outcome log is optional context
        out['outcome_error'] = type(exc).__name__
    return out


def build(day, days=10):
    import engine_compare as ec
    from restrictions import counts
    from strategy_settings import read_settings
    root = RUNTIME / 'shared-etf-paper'
    events = day_rows(root / 'events.jsonl', day)[0] or []
    rows = ec.journal(root / 'orders.sqlite3', root / 'events.jsonl', 'jev')
    trips = round_trips(rows, day, paths_today(root, day), events)
    settings, status = read_settings(root)
    tranche = None
    try:
        from accounts import load
        tranche = load(RUNTIME / 'accounts.json')['paper-shared']['budget']['first_buy_krw']
    except (OSError, KeyError, ValueError):
        pass
    buys = [r for r in rows if r['time'].startswith(day) and r['side'] == 'BUY']
    vibe = []
    try:
        from engine_roles import ledger_root
        vibe_root = ledger_root(RUNTIME, 'vibe')
        vibe = [t for t in round_trips(ec.journal(vibe_root / 'orders.sqlite3', vibe_root / 'events.jsonl', 'vibe'), day, {})]
    except Exception:  # the vibe book is optional context
        vibe = []
    gates = Counter(e.get('reason') for e in events if e.get('event') == 'v2_entry_gate')
    review = None
    try:
        from research_store import history as research_history
        review = next((r for r in research_history('daily_review', 5) if r.get('day') == day), None)
    except Exception:
        review = None
    return {
        'day': day, 'generated': datetime.now(KST).isoformat(timespec='seconds'),
        'trading_day': bool(events),
        'summary': {'round_trips': len(trips), 'wins': sum(t['pnl'] > 0 for t in trips),
                    'realized_krw': sum(t['pnl'] for t in trips if t['close']), 'open_positions': sum(1 for t in trips if not t['close']),
                    'note': '체결가 기준, 수수료·세금 미반영'},
        'round_trips': trips,
        'by_entry_route': _stats([t for t in trips if t['close']], 'entry_route'),
        'by_exit': _stats([t for t in trips if t['close']], 'exit'),
        'reentries': reentries(trips),
        'late_entries_after_1400': [f"{t['symbol']} {t['open']} {t['pnl']:+,}" for t in trips if t['open'] >= '14:00:00'],
        'undersized_buys': undersized_buys(buys, tranche, events),
        'entry_gates': dict(gates.most_common()),
        'restriction_blocks': {k: v for k, v in counts(events, day).items() if v},
        'guard': guard_usage(day, events),
        'health': runner_health(events, root, day),
        'jev_calls_30m': jev_calls(root, day),
        'signal_calibration': (calibration := signal_calibration(root, day, days)),
        'add_sizing': add_sizing(rows, day, days, settings, events, calibration),
        'direction_mass': direction_mass_summary(root, day, days),
        'news_gap': news_gap_outcomes(root, day, days),
        'blocked_outcomes': blocked_outcomes(root, day, days),
        'ipo_shadow': ipo_shadow_summary(root, day, days),
        'fade_shadow': fade_summary(root, day, days),
        'overnight_catalysts': overnight_summary(root, day, days),
        'mirror_gap': mirror_gaps(days),
        'vibe_legacy_settled_excluded': unverified_calls(root, day),
        'vibe_settlements_note': '게시 시각 미확인 기존 vibe 정산 제외. 30분 방향성은 실제 체결 성과가 아닙니다.',
        'vibe_entry_study': vibe_entry_study(RUNTIME, day),
        'vibe_virtual': {'round_trips': len(vibe), 'realized_krw': sum(t['pnl'] for t in vibe if t['close'])},
        'settings': {'status': status, **settings, 'tranche_krw': tranche},
        'daily_review_lessons': (review or {}).get('lessons'),
        'history': history(rows, day, days),
        'oversold': oversold_section(day, events, settings),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--day', help='YYYY-MM-DD (Asia/Seoul); default today')
    parser.add_argument('--days', type=int, default=10, help='trading days of history (default 10)')
    args = parser.parse_args()
    print(json.dumps(build(args.day or datetime.now(KST).strftime('%Y-%m-%d'), args.days), ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__':
    main()
