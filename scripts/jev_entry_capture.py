"""Collect isolated paper observations as evidence for jev_entry_replay.

Input is JSONL from --events FILE or --events - for stdin. Each event must have
scope=isolated_paper, day, cycle_id, symbol, input_digest, kind and an aware at.
Use a judgment event with verdict, judgment_source and signal_expires_at, a
quote event with bid and ask, and a check event with qty, available_cash,
held_limit, held_exposure, reserved_buy, rules_permit and pending. Sequential
checks also carry first_reconciled_at. Optional exit_bid is a scenario value.
The evidence array goes to stdout. Collection counts and omissions go to stderr.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, timedelta
import json
from pathlib import Path
import re
import sys

from jev_entry_replay import timestamp


def identity(event):
    if event.get('scope') != 'isolated_paper':
        raise ValueError('event scope must be isolated_paper')
    day, cycle, symbol, digest = (event.get(k) for k in ('day', 'cycle_id', 'symbol', 'input_digest'))
    if (not isinstance(day, str) or date.fromisoformat(day).isoformat() != day
            or not isinstance(cycle, str) or not re.fullmatch(r'[0-9a-f]{32}', cycle)
            or not isinstance(symbol, str) or not re.fullmatch(r'[0-9A-Za-z]{6}', symbol)
            or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
        raise ValueError('invalid event identity')
    at = timestamp(event.get('at'), day)
    if at is None:
        raise ValueError('invalid event time')
    return (day, cycle, symbol, digest), at


def capture(stream):
    states = defaultdict(lambda: {'judgment': None, 'quote': None, 'check': None})
    counts = Counter()
    for line in stream:
        if not line.strip():
            continue
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError('event must be an object')
        key, at = identity(event)
        kind = event.get('kind')
        state = states[key]
        counts['events'] += 1
        if kind in ('judgment', 'quote'):
            if state['check'] is not None:
                counts['observations_after_check'] += 1
                continue
            previous = state[kind]
            if previous and previous[0] == at and previous[1] != event:
                raise ValueError('conflicting observations at the same time')
            if previous is None or previous[0] < at:
                state[kind] = (at, event)
        elif kind == 'check':
            if state['check'] is not None and state['check'][1] != event:
                raise ValueError('conflicting checks for one candidate')
            state['check'] = (at, event)
        else:
            raise ValueError('unknown event kind')

    rows = []
    for key, state in sorted(states.items()):
        check = state['check']
        if check is None:
            counts['check_missing'] += 1
            continue
        checked, data = check
        decision_event = state['judgment']
        quote_event = state['quote']
        if decision_event is None or decision_event[0] > checked:
            counts['judgment_missing'] += 1
            continue
        decided, decision = decision_event
        expiry = timestamp(decision.get('signal_expires_at'), key[0])
        source = decision.get('judgment_source')
        if (decision.get('verdict') not in ('BUY', 'WAIT') or not isinstance(source, str) or not source.strip()
                or expiry is None or not decided <= checked < expiry):
            counts['judgment_invalid_or_stale'] += 1
            continue
        if quote_event is None or quote_event[0] > checked:
            counts['quote_missing'] += 1
            continue
        quoted, quote = quote_event
        reconciled = data.get('first_reconciled_at')
        parsed_reconciled = timestamp(reconciled, key[0]) if reconciled is not None else None
        if (checked - quoted > timedelta(seconds=5)
                or (reconciled is not None and (parsed_reconciled is None or parsed_reconciled > quoted))):
            counts['quote_invalid_or_stale'] += 1
            continue
        numbers = ('qty', 'available_cash', 'held_limit', 'held_exposure', 'reserved_buy')
        if (any(type(data.get(k)) is not int for k in numbers)
                or data['qty'] <= 0 or any(data[k] < 0 for k in numbers[1:])
                or type(data.get('pending')) is not bool or type(data.get('rules_permit')) is not bool
                or type(quote.get('ask')) is not int or type(quote.get('bid')) is not int
                or not 0 < quote['bid'] <= quote['ask']
                or (data.get('exit_bid') is not None
                    and (type(data['exit_bid']) is not int or data['exit_bid'] <= 0))):
            counts['check_or_quote_invalid'] += 1
            continue
        day, cycle, symbol, digest = key
        row = {
            'day': day, 'cycle_id': cycle, 'symbol': symbol, 'input_digest': digest,
            'verdict': decision['verdict'], 'judgment_source': source, 'decided_at': decided.isoformat(),
            'signal_expires_at': expiry.isoformat(), 'quote_at': quoted.isoformat(),
            'ask': quote['ask'], 'bid': quote['bid'], 'checked_at': checked.isoformat(),
            'qty': data['qty'], 'available_cash': data['available_cash'],
            'remaining_room': max(0, data['held_limit'] - data['held_exposure'] - data['reserved_buy']),
            'pending': data['pending'], 'rules_permit': data['rules_permit'],
        }
        for field in ('first_reconciled_at', 'exit_bid'):
            if field in data:
                row[field] = data[field]
        for field in ('judgment_model', 'confidence', 'probabilities', 'judgment_input_digest'):
            if field in decision:
                row[field] = decision[field]
        rows.append(row)
    counts['evidence'] = len(rows)
    return rows, dict(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--events', required=True, help='isolated paper JSONL file or - for stdin')
    args = parser.parse_args()
    try:
        if args.events == '-':
            rows, counts = capture(sys.stdin)
        else:
            with Path(args.events).open(encoding='utf-8') as stream:
                rows, counts = capture(stream)
        print(json.dumps(rows, ensure_ascii=False, sort_keys=True, allow_nan=False))
        print(json.dumps(counts, ensure_ascii=False, sort_keys=True), file=sys.stderr)
    except (OSError, TypeError, ValueError) as exc:
        parser.exit(1, f'수집 실패: {exc}\n')


if __name__ == '__main__':
    main()
