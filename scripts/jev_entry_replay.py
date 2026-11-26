"""Compare archived Jev entries with separately supplied, hypothetical candidate evidence.

Evidence is a JSON array keyed by day, cycle_id, symbol and input_digest. Each row
supplies verdict (BUY or WAIT), decided_at, checked_at, signal_expires_at,
quote_at, ask, bid, qty, available_cash, remaining_room, rules_permit and pending.
BUY also requires judgment_model, confidence and both choice probabilities.
Sequential entries additionally require first_reconciled_at. Optional exit_bid
is a scenario price. It does not establish an observed exit or portfolio return.
Fees are charged on both entry and exit, at 15 basis points per side by default.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from etf_universe import SYMBOLS
from jev_request import JevResponseRejected, validate_choice
from paper_ai_modes import JEV_MODEL


KST = ZoneInfo('Asia/Seoul')
SECOND = timedelta(seconds=1)
RESEARCH_MIN_BUY_CONFIDENCE = 0.6
REJECTION_REASONS = {
    'trend_not_rising', 'volume_not_rising', 'inverse_low_confidence',
    'inverse_too_late', 'inverse_market_unknown', 'inverse_index_not_falling_now',
    'inverse_flows_not_selling', 'careful_reentry_chase', 'careful_reentry_weak',
    'reentry_disabled', 'averaging_down_unknown', 'opposite_direction_holding',
    'split_plan_holding', 'scale_signal_disabled', 'add_count_limit',
    'projected_exit', 'stop_inside_spread', 'inverse_market_rebound',
}


def timestamp(value, day):
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.utcoffset() is None or stamp.astimezone(KST).date().isoformat() != day:
            return None
        return stamp.astimezone(KST)
    except (ValueError, TypeError, OverflowError):
        return None


def evidence_index(path):
    if path is None:
        return {}
    rows = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(rows, list):
        raise ValueError('evidence must be a JSON array')
    index = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('invalid evidence')
        key = tuple(row.get(k) for k in ('day', 'cycle_id', 'symbol', 'input_digest'))
        if (not isinstance(key[0], str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', key[0])
                or not isinstance(key[1], str) or not re.fullmatch(r'[0-9a-f]{32}', key[1])
                or not isinstance(key[2], str) or not re.fullmatch(r'[0-9A-Za-z]{6}', key[2])
                or not isinstance(key[3], str) or not re.fullmatch(r'[0-9a-f]{64}', key[3])):
            raise ValueError('invalid evidence identity')
        if key in index:
            raise ValueError('duplicate evidence')
        index[key] = row
    return index


def first_choice_rejections(cycle):
    return [gate for gate in cycle.get('gates') or []
            if gate.get('permitted') is False
            and SYMBOLS.get(gate.get('choice'), gate.get('choice')) == cycle.get('model_choice')
            and gate.get('reason') in REJECTION_REASONS]


def case_mode(cycle, second, buy_fills):
    submissions = cycle.get('submissions') or []
    intents = cycle.get('intents') or []
    fills = cycle.get('fills') or []
    if any(item.get('symbol') == second for item in submissions + intents + fills):
        return None, 'already_selected'
    if any(item.get('status') != 'terminal' for item in intents):
        return None, 'pending_order'
    execution = cycle.get('execution_status')
    if execution == 'blocked' and not (submissions or intents or fills):
        if first_choice_rejections(cycle):
            return 'fallback', None
    if execution in ('filled', 'partial_fill') and buy_fills and intents and submissions:
        return 'sequential', None
    return None, 'no_eligible_baseline'


def evaluate(cycle, evidence, day, mode, fee):
    if evidence is None or not cycle.get('input_linked'):
        return 'unknown', 'evidence_missing', None
    if evidence.get('verdict') == 'WAIT':
        return 'blocked', 'independent_wait', None
    if evidence.get('verdict') != 'BUY':
        return 'unknown', 'verdict_missing', None
    try:
        judgment = validate_choice({
            'model': evidence.get('judgment_model'),
            'answers': {'candidate': {'type': 'choice', 'choice': 'BUY',
                                      'confidence': evidence.get('confidence'),
                                      'probabilities': evidence.get('probabilities')}},
        }, 'candidate', ('BUY', 'WAIT'), JEV_MODEL)
    except JevResponseRejected:
        return 'unknown', 'judgment_confidence_unverified', None
    probabilities = judgment.get('probabilities')
    if probabilities is None or abs(judgment['confidence'] - (2 * probabilities['BUY'] - 1)) > 0.015:
        return 'unknown', 'judgment_confidence_unverified', None
    if judgment['confidence'] < RESEARCH_MIN_BUY_CONFIDENCE or probabilities['BUY'] < 0.8:
        return 'blocked', 'low_confidence', None
    if evidence.get('pending') is True:
        return 'blocked', 'pending_order', None
    if evidence.get('pending') is not False:
        return 'unknown', 'pending_unknown', None
    if evidence.get('rules_permit') is False:
        return 'blocked', 'rules_refused', None
    if evidence.get('rules_permit') is not True:
        return 'unknown', 'rules_unknown', None

    response = timestamp(cycle.get('response_time'), day)
    decision = timestamp(evidence.get('decided_at'), day)
    checked = timestamp(evidence.get('checked_at'), day)
    expiry = timestamp(evidence.get('signal_expires_at'), day)
    quote = timestamp(evidence.get('quote_at'), day)
    if any(item is None for item in (response, decision, checked, expiry, quote)):
        return 'unknown', 'timestamp_missing_or_invalid', None
    if not response <= decision <= checked < expiry or checked - response > 60 * SECOND:
        return 'blocked', 'stale_signal', None
    if not quote <= checked or checked - quote > 5 * SECOND:
        return 'blocked', 'stale_quote', None
    if mode == 'sequential':
        reconciled = timestamp(evidence.get('first_reconciled_at'), day)
        fill_times = [timestamp(item.get('time'), day) for item in cycle.get('fills') or []]
        if reconciled is None or not fill_times or any(item is None for item in fill_times):
            return 'unknown', 'reconciliation_unknown', None
        if not max(fill_times) <= reconciled <= quote <= checked:
            return 'blocked', 'quote_before_reconciliation', None
    else:
        rejected = [timestamp(gate.get('time'), day) for gate in first_choice_rejections(cycle)]
        if not rejected or any(item is None for item in rejected):
            return 'unknown', 'rejection_time_unknown', None
        if quote < max(rejected):
            return 'blocked', 'quote_before_rejection', None

    fields = ('ask', 'bid', 'qty', 'available_cash', 'remaining_room')
    if any(type(evidence.get(key)) is not int for key in fields):
        return 'unknown', 'price_or_capacity_unknown', None
    ask, bid, qty, cash, room = (evidence[key] for key in fields)
    if ask <= 0 or bid <= 0 or bid > ask or qty <= 0 or cash < 0 or room < 0:
        return 'blocked', 'invalid_price_or_capacity', None
    notional = ask * qty
    entry_fee = Decimal(notional) * fee / 10000
    if notional + entry_fee > cash or notional > room:
        return 'blocked', 'insufficient_capital', None
    pnl = None
    exit_bid = evidence.get('exit_bid')
    if exit_bid is not None:
        if type(exit_bid) is not int or exit_bid <= 0:
            return 'unknown', 'exit_price_invalid', None
        exit_ = Decimal(exit_bid * qty)
        pnl = (exit_ - notional - entry_fee - exit_ * fee / 10000).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP)
    return 'modeled', 'scenario_only', float(pnl) if pnl is not None else None


def analyze(directory, evidence, fee):
    counts = Counter({'modeled': 0, 'unknown': 0, 'blocked': 0, 'skipped': 0,
                      'baseline_buy_cycles': 0})
    cases = []
    paths = sorted(directory.glob('????-??-??.json'))
    if not paths:
        raise ValueError('no daily reports found')
    for path in paths:
        report = json.loads(path.read_text(encoding='utf-8'))
        day = report.get('day')
        if day != path.stem or date.fromisoformat(day).isoformat() != day:
            raise ValueError('report day does not match filename')
        by_cycle = defaultdict(dict)
        for candidate in report.get('candidates', []):
            if candidate.get('model_rank') == 2:
                by_cycle[candidate.get('cycle_id')][candidate.get('symbol')] = candidate
        for cycle in report.get('cycles', []):
            buy_fills = any(fill.get('side') == 'BUY' for fill in cycle.get('fills') or [])
            counts['baseline_buy_cycles'] += buy_fills
            cid, symbol, digest = cycle.get('cycle_id'), cycle.get('model_second_symbol'), cycle.get('input_digest')
            candidate = by_cycle[cid].get(symbol)
            mode, reason = case_mode(cycle, symbol, buy_fills)
            if mode is None:
                status, pnl = ('blocked' if reason == 'pending_order' else 'skipped'), None
            elif candidate is None or candidate.get('input_digest') != digest:
                status, reason, pnl = 'unknown', 'candidate_missing_or_mismatched', None
            else:
                status, reason, pnl = evaluate(cycle, evidence.get((day, cid, symbol, digest)), day, mode, fee)
            case = {'day': day, 'cycle_id': cid, 'symbol': symbol, 'input_digest': digest, 'mode': mode, 'status': status,
                    'baseline_choice': cycle.get('model_choice'), 'baseline_buy': buy_fills,
                    'reason': reason, 'next_buy_fill_time': candidate.get('next_buy_fill_time') if candidate else None,
                    'net_pnl_krw': pnl}
            cases.append(case)
            counts[status] += 1
    return {'counts': dict(counts), 'fee_bps_each_side': float(fee),
            'result_scope': 'independent hypothetical cases, not portfolio performance', 'cases': cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--reports', type=Path, required=True)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--fee-bps', type=Decimal, default=Decimal('15'))
    args = parser.parse_args()
    try:
        if not args.fee_bps.is_finite() or not Decimal(0) <= args.fee_bps <= Decimal(10000):
            raise ValueError('fee-bps must be finite and between 0 and 10000')
        output = analyze(args.reports, evidence_index(args.evidence), args.fee_bps)
        print(json.dumps(output, ensure_ascii=False, sort_keys=True, allow_nan=False))
    except (ValueError, OSError, InvalidOperation, TypeError, AttributeError) as exc:
        parser.exit(1, f'분석 실패: {exc}\n')


if __name__ == '__main__':
    main()
