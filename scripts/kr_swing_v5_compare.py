"""Compare same-close v5 paper NAV with its registered synthetic benchmark."""
from __future__ import annotations

import argparse
import hashlib
from datetime import date, datetime, time
import json
import math
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

CAPITAL = 30_000_000
BASELINE = '2026-11-05'
KST = ZoneInfo('Asia/Seoul')
STRATEGY_COST = 'estimated only: buy 0.20%, sell 0.40%; actual fees unavailable'
BENCHMARK_COST = '0.20% buy commission and 0.05% buy slippage; 0.20% sell tax + 0.20% sell commission if liquidation is later modeled'
CODES = frozenset({'005930.KS', '000660.KS', '035420.KS', '005380.KS', '105560.KS'})


def _date(value):
    try:
        parsed = date.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise ValueError('invalid observation date') from exc
    if parsed.isoformat() != value:
        raise ValueError('invalid observation date')
    return value


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'invalid {name}')
    return float(value)


def compare(journal: Path, benchmark: dict, expected_sessions=None):
    """Compare aligned end-of-session NAVs; require an external complete calendar.

    Signal dates and valuation dates differ. Only verified same-basis closing
    valuations may be compared to the benchmark's adjusted close.
    """
    if not isinstance(benchmark, dict) or benchmark.get('baseline') != BASELINE:
        raise ValueError('benchmark baseline mismatch')
    if benchmark.get('cost_note') != BENCHMARK_COST:
        raise ValueError('benchmark cost assumptions unknown')
    registered = _date(benchmark.get('registered_date_kst'))
    if registered < BASELINE:
        raise ValueError('benchmark registration precedes calibration')
    observations = benchmark.get('observations')
    if not isinstance(observations, list):
        raise ValueError('benchmark observations missing')
    try:
        bench_days = [_date(row['date']) for row in observations]
    except (KeyError, TypeError) as exc:
        raise ValueError('benchmark observations invalid') from exc
    if bench_days != sorted(set(bench_days)) or any(d <= BASELINE for d in bench_days):
        raise ValueError('benchmark dates must be strictly chronological after baseline')
    if any(d <= registered for d in bench_days):
        raise ValueError('benchmark observation cannot precede the session after registration')
    last = _date(benchmark.get('last_session'))
    if bench_days and bench_days[-1] != last:
        raise ValueError('benchmark last session mismatch')
    uri = journal.resolve().as_uri() + '?mode=ro'
    try:
        with sqlite3.connect(uri, uri=True) as db:
            db.execute('BEGIN')
            observation_days = [row[0] for row in db.execute('SELECT day FROM observations ORDER BY day')]
            rows = db.execute('SELECT m.day,m.observed_at,m.cash_gross,m.holdings_value,m.equity_gross,c.assumption,c.total,c.equity_estimated FROM marks m LEFT JOIN cost_estimates c ON c.day=m.day ORDER BY m.day').fetchall()
            pending = db.execute("SELECT COUNT(*) FROM intents WHERE status != 'terminal'").fetchone()[0]
            close_sources = {}
            if bench_days:
                try:
                    close_sources = {r[0]: r[1:] for r in db.execute('SELECT day,source,input_sha256,evidence FROM verified_closes')}
                except sqlite3.Error:
                    pass
    except sqlite3.Error as exc:
        raise ValueError('strategy journal unavailable or incomplete') from exc
    if pending:
        raise ValueError('unreconciled strategy order')
    if not observation_days or _date(observation_days[0]) != BASELINE:
        raise ValueError('strategy baseline observation missing')
    # Marks made on the registration day precede the first eligible full
    # comparison session. Keep the legacy flat baseline, ignore those marks.
    rows = [row for row in rows if row[0] == BASELINE or row[0] > registered]
    days = [_date(row[0]) for row in rows]
    if not days or days[0] != BASELINE or days != sorted(set(days)):
        raise ValueError('strategy baseline or dates invalid')
    if days[1:] != bench_days:
        raise ValueError('strategy and benchmark dates differ')
    if bench_days:
        if expected_sessions is None:
            raise ValueError('authoritative expected session dates required to detect gaps')
        expected = [_date(day) for day in expected_sessions]
        if expected != sorted(set(expected)) or expected != bench_days:
            raise ValueError('expected session dates differ or contain gaps')
    comparisons = []
    for i, (day, observed, cash, holdings, gross, assumption, costs, estimated) in enumerate(rows):
        if assumption is None or costs is None or estimated is None:
            raise ValueError('strategy cost estimate missing')
        if assumption != STRATEGY_COST:
            raise ValueError('strategy cost assumptions unknown')
        cash, holdings, gross, costs, estimated = (_number(v, n) for v, n in ((cash,'cash'),(holdings,'holdings'),(gross,'gross'),(costs,'cost'),(estimated,'estimated NAV')))
        if costs < 0 or abs(cash + holdings - gross) > .01 or abs(gross - costs - estimated) > .01:
            raise ValueError('strategy cost or NAV inconsistent')
        if i == 0:
            if holdings != 0 or cash != CAPITAL or estimated != CAPITAL:
                raise ValueError('strategy baseline must be flat and 30 million KRW')
            continue
        try:
            stamp = datetime.fromisoformat(observed)
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                raise ValueError
            local = stamp.astimezone(KST)
        except (ValueError, TypeError) as exc:
            raise ValueError('strategy close timestamp invalid') from exc
        if local < datetime.combine(date.fromisoformat(day), time(15, 30), KST):
            raise ValueError('strategy mark is earlier than the session close')
        entry = close_sources.get(day)
        if not entry or entry[0] != 'validated_yahoo_adjusted_close':
            raise ValueError('same-session close provenance missing')
        _, digest, evidence = entry
        if not isinstance(evidence, str) or not isinstance(digest, str) or hashlib.sha256(evidence.encode()).hexdigest() != digest:
            raise ValueError('close evidence hash mismatch')
        try:
            proof = json.loads(evidence)
        except (ValueError, TypeError) as exc:
            raise ValueError('invalid close evidence') from exc
        if (not isinstance(proof, dict) or proof.get('day') != day or proof.get('source') != entry[0]
                or proof.get('observed_at') != observed or proof.get('broker_snapshot_verified') is not True
                or proof.get('cash_gross_krw') != cash or proof.get('holdings_krw') != holdings
                or proof.get('estimated_cost_krw') != costs):
            raise ValueError('close evidence does not bind valuation')
        manifest = proof.get('snapshot_manifest_sha256')
        prices, positions = proof.get('prices'), proof.get('positions')
        if (not isinstance(manifest, str) or not re.fullmatch(r'[0-9a-f]{64}', manifest)
                or not isinstance(prices, dict) or set(prices) != CODES
                or not isinstance(positions, dict) or set(positions) != CODES):
            raise ValueError('close evidence has invalid snapshot or holdings')
        marked = 0.0
        for symbol in CODES:
            pair, shares = prices[symbol], positions[symbol]
            if not isinstance(pair, dict) or type(shares) is not int or shares < 0:
                raise ValueError('close evidence has invalid price or holdings')
            yahoo = _number(pair.get('yahoo_close'), 'Yahoo close')
            krx = _number(pair.get('krx_close'), 'KRX close')
            if yahoo <= 0 or krx <= 0 or abs(yahoo - krx) > 0.5:
                raise ValueError('closing prices do not share a proven price basis')
            marked += yahoo * shares
        if abs(marked - holdings) > .01:
            raise ValueError('close evidence holdings do not match valuation')
        benchmark_nav = _number(observations[i-1].get('nav'), 'benchmark NAV')
        if benchmark_nav <= 0 or estimated <= 0:
            raise ValueError('invalid nonpositive NAV')
        strategy_return = estimated / CAPITAL - 1
        benchmark_return = benchmark_nav / CAPITAL - 1
        comparisons.append({'date':day,'strategy_nav_estimated':estimated,'benchmark_nav_simulated':benchmark_nav,
                            'strategy_return':strategy_return,'benchmark_return':benchmark_return,
                            'difference':strategy_return-benchmark_return})
    return {'status':'observing' if comparisons else 'awaiting_future_sessions','baseline':BASELINE,
            'baseline_meaning':'historical calibration only', 'comparison_effective_start':registered,
            'first_eligible_session':'first exchange session after registration',
            'initial_capital_krw':CAPITAL,'comparisons':comparisons,'forward_performance_validated':False,
            'strategy_costs':'estimated; not actual broker fees','benchmark_costs':'simulated; not broker fills',
            'risk_note':'pre-start forecast matched; future realized volatility may differ'}


def main():
    from src.config.paths import get_runtime_root
    root = get_runtime_root() / 'nhplug/strategies/kr_swing_v5'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal', type=Path, default=root/'forward.sqlite3')
    parser.add_argument('--benchmark', type=Path, default=root/'benchmark.json')
    parser.add_argument('--sessions', type=Path, help='JSON array of verified completed session dates after baseline')
    args = parser.parse_args()
    sessions = json.loads(args.sessions.read_text()) if args.sessions else None
    print(json.dumps(compare(args.journal, json.loads(args.benchmark.read_text()), sessions), ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
