"""Run one isolated Jev trial from a separately supplied live paper frame.

The frame is read before and after the model request. A producer must update its
quote and account check timestamps after a slow request. The producer supplies
the isolated paper scope and read-only market observations. Results contain
evidence and the existing stage-three comparison and are written to stdout.
Set JEV_ISOLATED_API_KEY in .env or in the isolated process environment.
An independently allocated provider quota is required.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from decimal import Decimal
import hmac
import hashlib
import io
import json
import os
from pathlib import Path
from time import sleep
import urllib.request
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

from jev_entry_capture import capture, identity
from jev_entry_replay import analyze, case_mode, timestamp
from jev_request import jev_json_safe, validate_choice
from local_env import ENV
from paper_ai_modes import JEV_MODEL

KST = ZoneInfo('Asia/Seoul')
QUESTION = 'candidate'
CRITERIA = {'WAIT': 'Wait if evidence is insufficient or conflicting',
            'BUY': 'A paper-only entry is supported by the supplied evidence'}
IDENTITY = ('scope', 'day', 'cycle_id', 'symbol', 'input_digest', 'response_time')


class IsolationUnavailable(ValueError):
    """The required provider isolation was not established."""


def post_request(payload, key, timeout):
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(body) > 32 * 1024 * 1024:
        raise ValueError('request exceeds provider size limit')
    request = urllib.request.Request(
        'https://api.typesafe.ai/v1/systemone',
        data=body,
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def read_frame(path):
    frame = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(frame, dict):
        raise ValueError('invalid frame')
    check = frame.get('check')
    if not isinstance(check, dict):
        raise ValueError('missing check')
    identity({**frame, 'at': check.get('at')})
    if not isinstance(frame.get('state'), dict):
        raise ValueError('missing model state')
    if frame['state'].get('symbol') != frame.get('symbol'):
        raise ValueError('model state symbol mismatch')
    return frame


def matching_cycle(frame, reports):
    path = Path(reports) / (frame['day'] + '.json')
    report = json.loads(path.read_text(encoding='utf-8'))
    if report.get('day') != frame['day']:
        raise ValueError('report day mismatch')
    cycles = [row for row in report.get('cycles', []) if row.get('cycle_id') == frame['cycle_id']]
    if len(cycles) != 1:
        raise ValueError('cycle unavailable or ambiguous')
    cycle = cycles[0]
    candidates = [row for row in report.get('candidates', [])
                  if row.get('cycle_id') == frame['cycle_id'] and row.get('symbol') == frame['symbol']
                  and row.get('model_rank') == 2 and row.get('input_digest') == frame['input_digest']]
    buys = any(row.get('side') == 'BUY' for row in cycle.get('fills') or [])
    mode, _ = case_mode(cycle, frame['symbol'], buys)
    if (len(candidates) != 1 or not cycle.get('input_linked')
            or cycle.get('input_digest') != frame['input_digest']
            or cycle.get('response_time') != frame.get('response_time')
            or cycle.get('model_second_symbol') != frame['symbol'] or mode is None):
        raise ValueError('candidate not eligible for isolated comparison')
    return cycle


def observed_pair(current, frame, decided, expiry, now):
    if any(current.get(field) != frame.get(field) for field in IDENTITY):
        raise ValueError('frame identity changed')
    if current['state'] != frame['state']:
        raise ValueError('frame model state changed')
    quote, check = current.get('quote'), current['check']
    checked = timestamp(check.get('at'), frame['day'])
    quoted = timestamp(quote.get('at'), frame['day']) if isinstance(quote, dict) else None
    if (checked is not None and quoted is not None and now.utcoffset() is not None
            and decided <= checked <= now < expiry and timedelta(0) <= checked - quoted <= timedelta(seconds=5)
            and now - checked <= timedelta(seconds=5)):
        return quote, check
    return None


def refreshed_observations(path, frame, decided, expiry, clock, pause):
    path = Path(path)
    revision = None
    for attempt in range(21):
        now = clock()
        if now.utcoffset() is None or not decided <= now < expiry:
            break
        info = path.stat()
        current_revision = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        if current_revision != revision:
            current = read_frame(path)
            revision = current_revision
            if pair := observed_pair(current, frame, decided, expiry, now):
                return pair
        if attempt < 20:
            pause(min(0.1, (expiry - now).total_seconds()))
    raise ValueError('no fresh isolated observations after judgment')


def collect(frame_path, reports_dir, key, production_key, independent_quota, *, post=post_request, clock=None,
            pause=sleep, feed=None):
    if not key:
        raise IsolationUnavailable('.env에 JEV_ISOLATED_API_KEY를 설정해 주세요')
    if not independent_quota or not production_key or hmac.compare_digest(key, production_key):
        raise IsolationUnavailable('운영 키와 다른 인증키 및 독립 호출 한도 확인이 필요합니다')
    clock = clock or (lambda: datetime.now(KST))
    frame = read_frame(frame_path)
    matching_cycle(frame, reports_dir)
    start = clock()
    baseline = timestamp(frame['response_time'], frame['day'])
    if baseline is None or start.utcoffset() is None or not timedelta(0) <= start - baseline < timedelta(seconds=60):
        raise ValueError('baseline response is stale')
    expiry = baseline + timedelta(seconds=60)
    state = jev_json_safe(frame['state'])
    bars = state.get('completed_bars')
    if not isinstance(bars, list) or not 1 <= len(bars) <= 100:
        raise ValueError('completed bars are required')
    bar_times = [timestamp(row.get('time'), frame['day']) if isinstance(row, dict) else None for row in bars]
    if (any(stamp is None or stamp + timedelta(minutes=1) > baseline for stamp in bar_times)
            or baseline - max(bar_times) > timedelta(minutes=3)):
        raise ValueError('completed bars are stale or unavailable')
    precheck = frame['check'] if feed is None else feed.check(frame)
    if precheck.get('pending') is not False:
        raise ValueError('isolated book has pending or unknown orders')
    payload = {'model': JEV_MODEL, 'state': state, 'questions': {
        QUESTION: {'type': 'choice', 'instructions': 'Evaluate this candidate independently for an isolated '
                   'paper-trading entry. Choose WAIT if uncertain or information is insufficient.',
                   'criteria': CRITERIA}}}
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(encoded) > 32 * 1024 * 1024:
        raise ValueError('request exceeds provider size limit')
    response = post(payload, key, min(25.0, (expiry - start).total_seconds()))
    answer = validate_choice(response, QUESTION, CRITERIA, JEV_MODEL)
    decided = clock()
    if decided.utcoffset() is None or not start <= decided < expiry:
        raise ValueError('model response is stale')
    if feed is None:
        quote, check = refreshed_observations(frame_path, frame, decided, expiry, clock, pause)
    else:
        refreshed = feed.refresh(frame)
        pair = observed_pair(refreshed, frame, decided, expiry, clock())
        if pair is None:
            raise ValueError('no fresh isolated observations after judgment')
        quote, check = pair
    common = {name: frame[name] for name in ('scope', 'day', 'cycle_id', 'symbol', 'input_digest')}
    quote_fields = {name: quote[name] for name in ('at', 'bid', 'ask') if name in quote}
    check_fields = {name: check[name] for name in ('at', 'qty', 'available_cash', 'held_limit',
                    'held_exposure', 'reserved_buy', 'rules_permit', 'pending', 'first_reconciled_at')
                    if name in check}
    events = [
        {**common, 'kind': 'judgment', 'at': decided.isoformat(), 'verdict': answer['choice'],
         'judgment_model': response['model'], 'confidence': answer['confidence'],
         'probabilities': answer.get('probabilities'), 'judgment_input_digest': hashlib.sha256(encoded).hexdigest(),
         'judgment_source': 'jev_isolated', 'signal_expires_at': expiry.isoformat()},
        {**quote_fields, **common, 'kind': 'quote'},
        {**check_fields, **common, 'kind': 'check'},
    ]
    stream = io.StringIO(''.join(json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n' for event in events))
    evidence, counts = capture(stream)
    if not evidence:
        raise ValueError('isolated evidence incomplete: ' + ', '.join(sorted(counts)))
    row = evidence[0]
    key_tuple = (frame['day'], frame['cycle_id'], frame['symbol'], frame['input_digest'])
    comparison = analyze(Path(reports_dir), {key_tuple: row}, Decimal('15'))
    return {'evidence': evidence, 'comparison': comparison}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--frame', required=True, type=Path)
    parser.add_argument('--reports', required=True, type=Path)
    parser.add_argument('--independent-quota', action='store_true',
                        help='attest this key uses a quota independent from the operating Jev service')
    args = parser.parse_args()
    try:
        credentials = dotenv_values(ENV, interpolate=False)
        isolated_key = os.environ.get('JEV_ISOLATED_API_KEY') or credentials.get('JEV_ISOLATED_API_KEY') or ''
        result = collect(args.frame, args.reports, isolated_key,
                         credentials.get('JEV_API_KEY') or os.environ.get('JEV_API_KEY', ''),
                         args.independent_quota)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True))
    except IsolationUnavailable as exc:
        parser.exit(1, f'격리 실험 중단: {exc}\n')
    except (ValueError, OSError, TypeError, KeyError) as exc:
        parser.exit(1, f'격리 실험 실패: {type(exc).__name__}\n')


if __name__ == '__main__':
    main()
