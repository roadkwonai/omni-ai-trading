"""Compare an archived Jev input with a smaller input in isolation."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import urllib.error
from zoneinfo import ZoneInfo

from jev_request import MAX_IMAGE_BYTES, encode_request, jev_json_safe, usage_tokens, validate_choice

KST = ZoneInfo('Asia/Seoul')


def condense(capture):
    """Preserve the decision and provenance; shorten only premarket and journal."""
    result = copy.deepcopy(capture)
    state = result['state']
    premarket = state.get('premarket_context')
    codes = set((state.get('watchlist_details') or {}).keys()) | set(result.get('criteria', {}).keys())
    position = state.get('position') or {}
    if isinstance(position, dict) and position.get('symbol'):
        codes.add(position['symbol'])
    if isinstance(premarket, dict):
        if isinstance(premarket.get('news'), list):
            premarket['news'] = premarket['news'][:5]
        if isinstance(premarket.get('disclosures'), list):
            premarket['disclosures'] = [item for item in premarket['disclosures']
                                        if not isinstance(item, dict) or not item.get('code') or item['code'] in codes]
        overnight = premarket.get('overnight_material')
        if isinstance(overnight, dict) and isinstance(overnight.get('stocks'), dict):
            overnight['stocks'] = {code: value for code, value in overnight['stocks'].items() if code in codes}
    journal = state.get('trading_journal')
    if isinstance(journal, dict) and isinstance(journal.get('sessions'), list):
        journal['sessions'] = journal['sessions'][:1]
    return result


def comparison(capture):
    compact = condense(capture)
    def size(data):
        return len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode())
    return {'original_bytes': size(capture), 'condensed_bytes': size(compact),
            'comparison': 'structured_input_bytes_only', 'model_calls': 0}


def with_experience(capture, experience):
    """Return an independent request with only the research experience added."""
    result = copy.deepcopy(capture)
    if 'validated_experience' in result['state']:
        raise ValueError('capture already contains experience')
    result['state']['validated_experience'] = copy.deepcopy(experience)
    return result


def validate_chart(capture, path, *, require_original=True):
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError('comparison image exceeds file size limit')
    image = path.read_bytes()
    if require_original and hashlib.sha256(image).hexdigest() != capture.get('chart_sha256'):
        raise ValueError('chart digest does not match archived request')
    return image


def check_market(now):
    from session_times import shifted

    local = now.astimezone(KST)
    if local.weekday() < 5 and shifted('08:00', local.date()) <= local.strftime('%H:%M') < shifted('15:40', local.date()):
        raise ValueError('comparison model calls are unavailable during market hours')


def request(payload, key, image):
    import urllib.request
    from jev_activity import regular_request

    ref = payload['state']['chart']
    body = encode_request(payload, image, ref)
    req = urllib.request.Request('https://api.typesafe.ai/v1/systemone', data=body,
                                 headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                                          'TypeSafe-Danger-Zone-Images': 'true'})
    with regular_request():
        with urllib.request.urlopen(req, timeout=25) as response:
            return json.load(response)


def answer(result, question, criteria, expected_model):
    item = validate_choice(result, question, criteria, expected_model)
    return {'model': result['model'], 'choice': item['choice'], 'confidence': item['confidence'], 'input_tokens': usage_tokens(result)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path, help='JSON model_inputs.inputs from a recorded Jev request')
    parser.add_argument('--chart', type=Path, help='Original chart image matching chart_sha256')
    parser.add_argument('--paired-chart', type=Path, help='Explicit replacement chart shared by both arms; not an original replay')
    parser.add_argument('--question', help='Recorded question ID; required for model calls with older captures')
    parser.add_argument('--model', help='Recorded model ID; required for model calls with older captures')
    parser.add_argument('--ask-jev', action='store_true', help='Make two separate Jev calls after market hours')
    parser.add_argument('--output', type=Path, help='Save comparison to a new file')
    parser.add_argument('--experience-dir', type=Path, help='Isolated research experience directory')
    parser.add_argument('--decision-at', help='Archived decision time with UTC offset')
    parser.add_argument('--market', choices=('KOSPI', 'KOSDAQ'), help='Candidate market')
    parser.add_argument('--route', help='Candidate entry route')
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error('output already exists')
    capture = json.loads(args.capture.read_text())
    if not isinstance(capture, dict) or not all(key in capture for key in ('state', 'criteria', 'instructions', 'chart_sha256')):
        parser.error('capture must include state, criteria, instructions and chart_sha256')
    if not isinstance(capture['state'], dict) or not isinstance(capture['criteria'], dict) or not capture['criteria']:
        parser.error('invalid state or criteria')
    if not isinstance(capture['instructions'], (str, dict, list)):
        parser.error('invalid instructions')
    for field in ('question', 'model'):
        stored, supplied = capture.get(field), getattr(args, field)
        if stored is not None and (not isinstance(stored, str) or not stored.strip()):
            parser.error(f'invalid recorded {field}')
        if stored is not None and supplied is not None and stored != supplied:
            parser.error(f'{field} differs from the recorded request')
        setattr(args, field, stored or supplied)
    if args.ask_jev and (not args.question or not args.model):
        parser.error('provide --question and --model when they are missing from the capture')
    if args.experience_dir:
        if not all((args.decision_at, args.market, args.route, args.question in ('watchlist', 'candidate', 'holding'))):
            parser.error('experience comparison requires --decision-at, --market, --route and a recorded question')
        try:
            decision_at = datetime.fromisoformat(args.decision_at)
            if decision_at.utcoffset() is None:
                raise ValueError('decision time must be timezone-aware')
        except ValueError:
            parser.error('decision time must be ISO formatted with a UTC offset')
        recorded_at = capture.get('decision_at')
        if recorded_at is not None:
            try:
                recorded = datetime.fromisoformat(recorded_at)
                if recorded.utcoffset() is None or recorded != decision_at:
                    raise ValueError('decision time differs')
            except (TypeError, ValueError):
                parser.error('decision time differs from the capture')
        if args.ask_jev and recorded_at is None:
            parser.error('model comparison requires a decision_at in the capture')
        if args.output:
            runtime = (Path.home() / '.vibe-trading').resolve()
            output = args.output.expanduser().resolve()
            if output == runtime or runtime in output.parents:
                parser.error('experience comparison output must be outside the runtime directory')
    elif args.decision_at or args.market or args.route:
        parser.error('experience context requires --experience-dir')
    chart_ref = capture['state'].get('chart')
    chart_path = args.chart or args.paired_chart
    if (args.chart and args.paired_chart) or (args.ask_jev and (not chart_path or not isinstance(chart_ref, str) or not chart_ref)):
        parser.error('provide one chart when requesting a model comparison')
    try:
        capture = jev_json_safe(capture)
        image = validate_chart(capture, chart_path, require_original=bool(args.chart)) if chart_path else None
        report = comparison(capture)
        report.update(question=args.question, model=args.model, chart_sha256=capture['chart_sha256'],
                      chart_verified=bool(args.chart), compared_chart_sha256=hashlib.sha256(image).hexdigest() if image else None,
                      chart_provenance='matched_original' if args.chart else 'replacement' if args.paired_chart else 'unavailable',
                      decision_scope='single recorded question; excludes other questions and their context or effects',
                      compared_context=['premarket_context', 'trading_journal'])
        arms = [('original', capture), ('condensed', condense(capture))]
        if args.experience_dir:
            from jev_experience import read as read_experience
            experience = read_experience(args.experience_dir, at=decision_at, purpose=args.question,
                                         market=args.market, route=args.route)
            variant = with_experience(capture, experience) if experience else None
            report.update(comparison='experience_structured_input_bytes_only', compared_context=['validated_experience'],
                          experience_status='available' if variant else 'unavailable',
                          experience_version=experience.get('version') if experience else None,
                          decision_at=decision_at.isoformat(), decision_time_provenance='capture' if recorded_at else 'caller')
            if variant:
                report['experience_bytes'] = len(json.dumps(variant, ensure_ascii=False, allow_nan=False).encode())
                arms = [('original', capture), ('experience', variant)]
            elif args.ask_jev:
                raise ValueError('experience unavailable for comparison')
        if args.ask_jev:
            check_market(datetime.now(KST))
            from dotenv import dotenv_values
            from local_env import ENV
            key = dotenv_values(ENV, interpolate=False).get('JEV_API_KEY')
            if not key:
                raise ValueError('JEV_API_KEY missing')
            answers = {}
            asset = {chart_ref: {}}
            failed = None
            attempts = 0
            for name, current in arms:
                payload = {'model': args.model, 'state': current['state'],
                           'assets': asset,
                           'questions': {args.question: {'type': 'choice', 'instructions': current['instructions'],
                                                        'criteria': current['criteria']}}}
                try:
                    check_market(datetime.now(KST))
                    attempts += 1
                    answers[name] = answer(request(payload, key, image), args.question, current['criteria'], args.model)
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    failed = {'arm': name, 'error_type': type(exc).__name__}
                    break
            second = arms[1][0]
            report.update(model_calls=attempts, answers=answers, failed_arm=failed, trials_per_arm=1,
                          choice_matches=None if failed else answers['original']['choice'] == answers[second]['choice'])
    except (ValueError, OSError, KeyError, TypeError, urllib.error.URLError) as exc:
        parser.error(f'comparison failed ({type(exc).__name__})')
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered, flush=True)
    if args.output:
        import os
        try:
            fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(rendered)
        except OSError:
            parser.exit(1, 'could not save comparison file; result is available on stdout\n')
    if report.get('failed_arm'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
