"""Prequential paper baseline; historical price direction, never order execution."""
from __future__ import annotations
import argparse
import hashlib
import heapq
import json
import math
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo
from paper_learning import OUTPUT, ROOT
from etf_universe import SYMBOLS as ETF_SYMBOLS
from src.tools.private_db import private_file

MODEL_DB = ROOT / 'paper-model.sqlite3'
SYMBOLS = set(ETF_SYMBOLS) | {'SAMSUNG', 'NAVER', 'KAKAO'}
VERSION = 'beta-direction-prequential-v2'
JEV_VERSION = 'jev-direction-prequential-v3'
KST = ZoneInfo('Asia/Seoul')


def parse_time(value):
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError('time requires timezone')
        return result.astimezone(KST)
    except (TypeError, ValueError) as exc:
        raise ValueError('invalid time') from exc


def label(row, decision_at):
    """Only accept a finite label with a provably matured, later observation."""
    if row['status'] != 'observed':
        return None
    try:
        baseline = parse_time(row['baseline_time'])
        future = parse_time(row['future_time'])
        seen = parse_time(row['future_seen_at'])
        start = Decimal(row['baseline_close'])
        end = Decimal(row['future_close'])
        if not start.is_finite() or not end.is_finite() or start <= 0 or end <= 0:
            return None
        if baseline > decision_at - timedelta(minutes=2) or decision_at - baseline > timedelta(minutes=4):
            return None
        if (future < decision_at + timedelta(minutes=5) or future > decision_at + timedelta(minutes=7)
                or future.date() != decision_at.date() or seen < future + timedelta(minutes=2)):
            return None
        if end == start:  # A flat future is neither a directional success nor a failure.
            return None
        return seen, int(end > start)
    except (TypeError, ValueError, InvalidOperation):
        return None


def train(source, output):
    source, output = Path(source), Path(output)
    if not source.is_file():
        raise FileNotFoundError('paper observations not initialized')
    if source.resolve() == output.resolve():
        raise ValueError('model database must differ from observation database')
    with sqlite3.connect(f'{source.resolve().as_uri()}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        columns = {r[1] for r in db.execute('PRAGMA table_info(price_observations)')}
        universe_column = "COALESCE(universe,'legacy_unknown')" if 'universe' in columns else "'legacy_unknown'"
        rows = [dict(r) for r in db.execute('''SELECT source,offset,symbol,decision_time,choice,
            baseline_time,baseline_close,future_time,future_seen_at,future_close,status,''' + universe_column + ''' AS universe
            FROM price_observations ORDER BY source,offset,symbol''')]
    canonical = json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    digest = hashlib.sha256((VERSION + '\n' + canonical).encode()).hexdigest()
    # A single choice is a categorical observation, not an actual trade or outcome.
    decisions = [row for row in rows if row['universe'] != 'etf7_jev_only'
                 and row['symbol'] in SYMBOLS and row['choice'] == row['symbol']]
    decisions.sort(key=lambda row: (parse_time(row['decision_time']), row['source'], row['offset'], row['symbol']))
    posterior = {}
    mature = []
    predictions = []
    for row in decisions:
        at = parse_time(row['decision_time'])
        while mature and mature[0][0] < at:
            _, _, universe, symbol, success = heapq.heappop(mature)
            posterior.setdefault((universe, symbol), [1, 1])[0 if success else 1] += 1
        alpha, beta = posterior.setdefault((row['universe'], row['symbol']), [1, 1])
        predictions.append((digest, row['source'], row['offset'], row['symbol'], at.isoformat(),
                            alpha, beta, alpha / (alpha + beta), row['universe']))
        outcome = label(row, at)
        if outcome:
            seen, success = outcome
            heapq.heappush(mature, (seen, len(predictions), row['universe'], row['symbol'], success))
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_file(output)  # src.tools.private_db: never os.open an existing database
    with sqlite3.connect(output) as db:
        db.execute('CREATE TABLE IF NOT EXISTS runs (digest TEXT PRIMARY KEY, version TEXT NOT NULL, source_rows INTEGER NOT NULL, predictions INTEGER NOT NULL)')
        db.execute('''CREATE TABLE IF NOT EXISTS predictions (run_digest TEXT NOT NULL, source TEXT NOT NULL,
            offset INTEGER NOT NULL, symbol TEXT NOT NULL, decision_time TEXT NOT NULL,
            alpha INTEGER NOT NULL, beta INTEGER NOT NULL, probability REAL NOT NULL,
            PRIMARY KEY(run_digest,source,offset,symbol))''')
        if 'universe' not in {r[1] for r in db.execute('PRAGMA table_info(predictions)')}:
            db.execute('ALTER TABLE predictions ADD COLUMN universe TEXT')
        db.execute('INSERT OR IGNORE INTO runs VALUES (?,?,?,?)', (digest, VERSION, len(rows), len(predictions)))
        db.executemany('''INSERT OR IGNORE INTO predictions
            (run_digest,source,offset,symbol,decision_time,alpha,beta,probability,universe)
            VALUES (?,?,?,?,?,?,?,?,?)''', predictions)
    return {'run_digest': digest, 'source_rows': len(rows), 'predictions': len(predictions),
            'matured_at_last_decision': sum(a+b-2 for a,b in posterior.values()),
            'status': 'research_only_not_trading_or_profit'}


def train_jev(ledger, source, output):
    """Record prequential Jev price-direction research, separate from trading predictions."""
    ledger, source, output = map(Path, (ledger, source, output))
    if not ledger.is_file() or not source.is_file():
        raise FileNotFoundError('paper research inputs unavailable')
    if len({p.resolve() for p in (ledger, source, output)}) != 3:
        raise ValueError('research inputs and output must differ')
    with sqlite3.connect(f'{ledger.resolve().as_uri()}?mode=ro', uri=True) as db:
        events = db.execute('SELECT source,offset,time,event,data FROM events_v2 ORDER BY source,offset').fetchall()
    cycles = Counter()
    for source_id, _, _, kind, raw in events:
        if kind in ('decision', 'market_context'):
            try:
                event = json.loads(raw)
                cycle = event.get('cycle_id') if isinstance(event, dict) else None
                if isinstance(cycle, str) and re.fullmatch(r'[0-9a-f]{32}', cycle):
                    cycles[(source_id, cycle)] += 1
            except (ValueError, TypeError):
                continue
    with sqlite3.connect(f'{source.resolve().as_uri()}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        columns = {r[1] for r in db.execute('PRAGMA table_info(price_observations)')}
        universe_column = "COALESCE(universe,'legacy_unknown')" if 'universe' in columns else "'legacy_unknown'"
        observations = {(r['source'], r['offset'], r['symbol']): dict(r) for r in db.execute(
            '''SELECT source,offset,symbol,decision_time,choice,baseline_time,baseline_close,
                      future_time,future_seen_at,future_close,status,''' + universe_column + ' AS universe FROM price_observations')}
    samples = []
    for first, second in zip(events, events[1:]):
        source_id, offset, stamp, kind, raw = first
        if kind not in ('decision', 'market_context') or second[0] != source_id or second[3] != 'jev_shadow':
            continue
        try:
            decision, jev = json.loads(raw), json.loads(second[4])
            at, jev_at = parse_time(stamp), parse_time(second[2])
            first_cycle, second_cycle = decision.get('cycle_id'), jev.get('cycle_id')
            symbol, confidence = jev.get('jev_choice'), jev.get('jev_confidence')
            if kind == 'market_context':
                if (decision.get('event') != kind or decision.get('context') != 'entry'
                        or decision.get('universe') != 'etf7' or decision.get('choice') is not None
                        or not isinstance(first_cycle, str) or not re.fullmatch(r'[0-9a-f]{32}', first_cycle)
                        or cycles[(source_id, first_cycle)] != 1
                        or second_cycle != first_cycle or jev.get('openai_choice') is not None
                        or jev.get('event') != 'jev_shadow' or symbol not in ETF_SYMBOLS
                        or parse_time(decision.get('time')) != at or parse_time(jev.get('time')) != jev_at
                        or not at <= parse_time(jev.get('jev_response_time')) <= jev_at):
                    continue
            if ((first_cycle is not None or second_cycle is not None) and
                    (not first_cycle or first_cycle != second_cycle)):
                continue
            if (not 0 <= (jev_at - at).total_seconds() <= 60 or at.date() != jev_at.date()
                    or (kind == 'decision' and (decision.get('choice') not in SYMBOLS | {'WAIT'}
                        or jev.get('openai_choice') != decision.get('choice')))
                    or jev.get('jev_model') != 'jev-1.14.0' or symbol not in SYMBOLS
                    or type(confidence) not in (int, float) or not math.isfinite(confidence)
                    or not 0 <= confidence <= 1):
                continue
            row = observations.get((source_id, offset, symbol))
            if (not row or parse_time(row['decision_time']) != at
                    or (kind == 'decision' and row['choice'] != decision['choice'])
                    or (kind == 'market_context' and (row['choice'] is not None or row['universe'] != 'etf7_jev_only'))):
                continue
            match_kind = 'cycle_id' if first_cycle else 'inferred_adjacent'
            samples.append((source_id, offset, at.isoformat(), jev_at.isoformat(), match_kind, row))
        except (ValueError, TypeError, AttributeError, KeyError):
            continue
    samples.sort(key=lambda item: (item[3], item[0], item[1]))
    canonical = json.dumps(samples, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    digest = hashlib.sha256((JEV_VERSION + '\n' + canonical).encode()).hexdigest()
    posterior = {}
    mature, predictions = [], []
    for source_id, offset, at_text, jev_text, match_kind, row in samples:
        at, jev_at = parse_time(at_text), parse_time(jev_text)
        while mature and mature[0][0] < jev_at:
            _, _, universe, symbol, success = heapq.heappop(mature)
            posterior.setdefault((universe, symbol), [1, 1])[0 if success else 1] += 1
        alpha, beta = posterior.setdefault((row['universe'], row['symbol']), [1, 1])
        predictions.append((digest, source_id, offset, row['symbol'], jev_text, match_kind, alpha, beta, alpha / (alpha + beta), row['universe']))
        outcome = label(row, at)
        if outcome and parse_time(row['future_time']) >= jev_at + timedelta(minutes=5):
            heapq.heappush(mature, (outcome[0], len(predictions), row['universe'], row['symbol'], outcome[1]))
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_file(output)  # src.tools.private_db: never os.open an existing database
    with sqlite3.connect(output) as db:
        db.execute('CREATE TABLE IF NOT EXISTS jev_runs (digest TEXT PRIMARY KEY, version TEXT NOT NULL, samples INTEGER NOT NULL)')
        # Each run recomputes every sample from the whole ledger, so only the latest run is kept: a copy per minute grew
        # jev_predictions (still kept as it was on 11/18, no longer written) to 1.3M rows for 2.6k targets.
        db.execute('''CREATE TABLE IF NOT EXISTS jev_predictions_latest (run_digest TEXT NOT NULL, source TEXT NOT NULL,
            offset INTEGER NOT NULL, symbol TEXT NOT NULL, jev_time TEXT NOT NULL, match_kind TEXT NOT NULL, alpha INTEGER NOT NULL,
            beta INTEGER NOT NULL, probability REAL NOT NULL, universe TEXT, PRIMARY KEY(source,offset,symbol))''')
        db.execute('INSERT OR IGNORE INTO jev_runs VALUES (?, ?, ?)', (digest, JEV_VERSION, len(samples)))
        db.execute('DELETE FROM jev_predictions_latest')
        db.executemany('''INSERT OR IGNORE INTO jev_predictions_latest
            (run_digest,source,offset,symbol,jev_time,match_kind,alpha,beta,probability,universe)
            VALUES (?,?,?,?,?,?,?,?,?,?)''', predictions)
    return {'run_digest': digest, 'predictions': len(predictions),
            'matured_at_last_jev': sum(a+b-2 for a,b in posterior.values()),
            'status': 'research_only_price_direction_not_trade_or_profit'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('train', 'summary'))
    args = parser.parse_args()
    if args.action == 'train':
        print(json.dumps(train(OUTPUT, MODEL_DB), ensure_ascii=False))
    else:
        if not MODEL_DB.is_file():
            raise SystemExit('paper model is not initialized')
        with sqlite3.connect(f'{MODEL_DB.resolve().as_uri()}?mode=ro', uri=True) as db:
            print(json.dumps({'runs': db.execute('SELECT COUNT(*) FROM runs').fetchone()[0],
                              'predictions': db.execute('SELECT COUNT(*) FROM predictions').fetchone()[0]}, ensure_ascii=False))

if __name__ == '__main__':
    main()
