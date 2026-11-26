"""Run a point-in-time Jev experience comparison in independent virtual books."""
from __future__ import annotations

import argparse
from bisect import bisect_right
from datetime import date, datetime, time, timedelta
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys
from time import monotonic
import urllib.request

from etf_universe import is_code
from jev_entry_study import atomic_write, immutable, output_folder, snapshot_folder
from jev_experience import build, publish, read
from jev_input_compare import with_experience
from jev_learning_replay import KST, normalized_time
from jev_learning_virtual import ARMS, replay
from jev_request import encode_request, usage_tokens, validate_choice
from position_paths import day_bars_from_connection
from sqlite_ro import ro_connect

ROUTE = 'watchlist_strength'
MODEL_QUESTION = 'candidate'
EVIDENCE_QUESTION = 'experience_use'
REASON_QUESTION = 'experience_reason'
EXPERIENCE_CRITERIA = {'USED': 'The validated_experience supplied relevant context that affected the recommendation.',
                       'UNUSED': 'The supplied experience did not affect the recommendation.',
                       'ABSENT': 'No validated_experience was supplied.'}
REASON_CRITERIA = {'ABSENT': 'The experience field was absent.', 'IRRELEVANT': 'It did not match the current situation.',
                   'SMALL_SAMPLE': 'The sample or statistical evidence was too weak.',
                   'CURRENT_SIGNALS': 'Current market, price, volume, or news dominated.',
                   'CONFLICT': 'Experience conflicted with the current evidence.',
                   'SUPPORTED': 'Experience supported the decision.', 'OTHER': 'Another reason.'}
START = time(9, 10)
END = time(15, 0)
FEE = 0.0015
CASH = 1_000_000
TRANCHE = 500_000
MIN_CONFIDENCE = 0.50
MAX_PAIRS = 10
LIMITS = {'order_krw': TRANCHE, 'daily_buy_krw': CASH, 'daily_new_symbols': 2, 'held_krw': TRANCHE}


def frames(ledger, research, day, symbols):
    start, end = [datetime.combine(day, boundary, KST).isoformat() for boundary in (START, END)]
    query = ("SELECT time,event,data FROM events_v2 WHERE time>=? AND time<? "
             "AND event IN ('holding_quote','jev_shadow','jev_holding_decision') ORDER BY time,rowid")
    archived, quotes = [], {symbol: [] for symbol in symbols}
    for stamp, kind, raw in ledger.execute(query, (start, end)):
        item = json.loads(raw)
        if kind == 'jev_shadow' and item.get('jev_input_digest'):
            archived.append((stamp, item['jev_input_digest'], 'candidate', item.get('jev_model'), None))
        if kind == 'jev_holding_decision' and item.get('input_digest') and item.get('symbol') in quotes:
            archived.append((stamp, item['input_digest'], 'holding', item.get('model'), item['symbol']))
        if kind == 'holding_quote' and item.get('symbol') in quotes:
            at = normalized_time(stamp)
            bid, ask = item.get('bid'), item.get('ask')
            try:
                source = datetime.combine(day, time.fromisoformat(item.get('quote_time', '')), KST)
            except (TypeError, ValueError):
                continue
            if type(bid) is int and type(ask) is int and 0 < bid <= ask and timedelta(0) <= at - source <= timedelta(seconds=5):
                quotes[item['symbol']].append({'id': stamp, 'symbol': item['symbol'], 'at': stamp, 'source_at': source.isoformat(),
                                               'received_at': stamp, 'bid': bid, 'ask': ask})
    archived_times = [row[0] for row in archived]
    bars = day_bars_from_connection(ledger, day.isoformat())
    answer, seen = [], set()
    for symbol, rows in quotes.items():
        for quote in rows[:-1]:
            index = bisect_right(archived_times, quote['at']) - 1
            quote_at = normalized_time(quote['at'])
            for cursor in range(index, -1, -1):
                stamp, digest, question, model, held_symbol = archived[cursor]
                decision_at = normalized_time(stamp)
                delta = quote_at - decision_at
                if delta > timedelta(seconds=90):
                    break
                if digest in seen or delta < timedelta(seconds=10) or held_symbol not in (None, symbol):
                    continue
                entry = research.execute("SELECT time,inputs FROM model_inputs WHERE digest=? AND engine='jev' AND question=?",
                                         (digest, question)).fetchone()
                if not entry or normalized_time(entry[0]) is None or normalized_time(entry[0]) > decision_at:
                    continue
                capture = json.loads(entry[1])
                criteria = capture.get('criteria') or {}
                if question == 'candidate' and (symbol not in criteria or 'KOSDAQ' not in str(criteria[symbol])):
                    continue
                model = capture.get('model') or model
                if not model:
                    continue
                series = {code: [(b[0], b[4]) for b in bars.get(code, ()) if b[5] is not None and b[5] <= decision_at][-45:]
                          for code in (symbol, '229200')}
                if any(len(values) < 3 for values in series.values()):
                    continue
                seen.add(digest)
                answer.append({'quote': quote, 'input_digest': digest, 'decision_at': stamp, 'capture': capture,
                               'capture_question': question, 'model': model, 'series': series})
                break
    return sorted(answer, key=lambda row: row['quote']['at'])[:MAX_PAIRS], quotes


def png_chart(series):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=(8, 4), dpi=100, layout='constrained')
    FigureCanvasAgg(figure)
    for ax, (code, values) in zip(figure.subplots(len(series), 1, squeeze=False).flat, series.items()):
        ax.plot([stamp for stamp, _ in values], [close for _, close in values])
        ax.set_title(f'{code} | recorded 1-minute closes')
    stream = BytesIO()
    figure.savefig(stream, format='png')
    return stream.getvalue()


def requests_for_frame(frame, experience):
    original = frame['capture']
    symbol = frame['quote']['symbol']
    image = png_chart(frame['series'])
    chart_id = 'chart-' + hashlib.sha256(image).hexdigest()[:20]
    state = {**original['state'], 'chart': chart_id,
             'chart_as_of': {code: values[-1][0].strftime('%H%M%S') for code, values in frame['series'].items()}}
    for key in ('position', 'opening_review', 'pre_close_review'):
        state.pop(key, None)
    criteria = {'WAIT': 'The stock is unsuitable or the evidence is insufficient.',
                symbol: f'The recorded chart, news and market evidence support a KOSDAQ paper entry in {symbol}.'}
    instructions = ('Evaluate only the given stock for a watchlist-strength paper entry. Use the stated market, '
                    'news, flows and completed charts. Treat historical performance and any supplied validated_experience '
                    'as reference. Choose WAIT if uncertain, the move is extended, or evidence is insufficient.')
    payload = {'model': frame['model'], 'state': state, 'assets': {chart_id: {}}, 'questions': {
        MODEL_QUESTION: {'type': 'choice', 'criteria': criteria, 'instructions': instructions},
        EVIDENCE_QUESTION: {'type': 'choice', 'criteria': EXPERIENCE_CRITERIA,
                            'instructions': 'Did the validated_experience field affect the candidate recommendation?'},
        REASON_QUESTION: {'type': 'choice', 'criteria': REASON_CRITERIA,
                          'instructions': 'What best explains the role of validated_experience in your decision?'}}}
    return {'baseline': payload, 'experience': with_experience(payload, experience)}, image


def ask(payload, image, key):
    body = encode_request(payload, image, payload['state']['chart'])
    query = urllib.request.Request('https://api.typesafe.ai/v1/systemone', data=body,
                                   headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                                            'TypeSafe-Danger-Zone-Images': 'true'})
    with urllib.request.urlopen(query, timeout=15) as response:
        result = json.load(response)
    criteria = payload['questions'][MODEL_QUESTION]['criteria']
    answer = validate_choice(result, MODEL_QUESTION, criteria, payload['model'])
    try:
        use = validate_choice(result, EVIDENCE_QUESTION, EXPERIENCE_CRITERIA, payload['model'])['choice']
    except ValueError:
        use = 'UNVERIFIED'
    try:
        reason = validate_choice(result, REASON_QUESTION, REASON_CRITERIA, payload['model'])['choice']
    except ValueError:
        reason = 'UNVERIFIED'
    return {'choice': answer['choice'], 'confidence': answer['confidence'], 'experience_use': use,
            'experience_reason': reason, 'input_tokens': usage_tokens(result), 'request_bytes': len(body)}


def actions(quotes, predictions):
    per_symbol = {symbol: {'positions': {}, 'closed': set()} for symbol in quotes}
    last = {symbol: rows[-1]['id'] for symbol, rows in quotes.items() if rows}
    steps = []
    for quote in sorted((q for rows in quotes.values() for q in rows), key=lambda row: row['at']):
        decisions = {}
        symbol, at = quote['symbol'], quote['at']
        state = per_symbol[symbol]
        for arm in ARMS:
            decision = {'action': 'WAIT', 'decided_at': at}
            position = state['positions'].get(arm)
            if position:
                basis, qty = position
                if quote['bid'] <= basis * .99 or quote['bid'] >= basis * 1.02 or quote['id'] == last[symbol]:
                    decision.update(action='SELL', qty=qty, limit=quote['bid'])
                    del state['positions'][arm]
                    state['closed'].add(arm)
            elif arm not in state['closed'] and quote['id'] in predictions and quote['id'] != last[symbol]:
                answer = predictions[quote['id']][arm]
                if answer['choice'] == symbol and answer['confidence'] >= MIN_CONFIDENCE:
                    qty = int(TRANCHE / (quote['ask'] * (1 + FEE)))
                    if qty:
                        decision.update(action='BUY', qty=qty, limit=quote['ask'])
                        state['positions'][arm] = (quote['ask'], qty)
            decisions[arm] = decision
        steps.append({**quote, 'decisions': decisions})
    return steps


def run(day, symbols, source, training, out, key, judge=ask, *, closed_backup=False):
    cutoff = datetime.combine(day, time.min, KST)
    basis = json.loads(training.read_text(encoding='utf-8'))
    if normalized_time(basis['as_of']) >= cutoff:
        raise ValueError('training cutoff must precede the evaluation day')
    artifact = build(basis['samples'], published_at=cutoff, eligible_from=cutoff, expires_at=cutoff + timedelta(days=1))
    publish(artifact, out / 'reconstructed_experience')
    experience = read(out / 'reconstructed_experience', at=cutoff, purpose='candidate', market='KOSDAQ', route=ROUTE)
    if not experience:
        raise ValueError('no eligible KOSDAQ experience')
    open_source = immutable if closed_backup else ro_connect
    with open_source(source / 'trading-ledger.sqlite3') as ledger, open_source(source / 'market-research.sqlite3') as research:
        study, quotes = frames(ledger, research, day, symbols)
    if len(study) < 2:
        raise ValueError('not enough linked Jev inputs and recorded quotes')
    answers, rows = {}, []
    created = datetime.now(KST)
    for index, frame in enumerate(study):
        payloads, image = requests_for_frame(frame, experience)
        paired = {}
        started = monotonic()
        # Alternate order across observations to balance the effect of request order.
        for arm in (ARMS if index % 2 == 0 else ARMS[::-1]):
            paired[arm] = judge(payloads[arm], image, key)
        quote = frame['quote']
        elapsed = monotonic() - started
        eligible = elapsed < (normalized_time(quote['at']) - normalized_time(frame['decision_at'])).total_seconds()
        if eligible:
            answers[quote['id']] = paired
        rows.append({'symbol': quote['symbol'], 'quote_at': quote['at'], 'decision_at': frame['decision_at'],
                     'input_digest': frame['input_digest'], 'experience_version': experience['version'],
                     'capture_question': frame['capture_question'], 'paired_elapsed_ms': round(elapsed * 1000),
                     'execution_eligible': eligible, 'chart_sha256': hashlib.sha256(image).hexdigest(), 'answers': paired})
        atomic_write(out / 'pairs.json', rows)
    steps = actions(quotes, answers)
    simulation = replay(steps, cash=CASH, limits=LIMITS, fee=FEE)
    result = {'day': day.isoformat(), 'symbols': symbols, 'training_as_of': basis['as_of'],
              'actually_created_at': created.isoformat(), 'simulated_experience_available_from': cutoff.isoformat(),
              'experience': 'reconstructed_point_in_time', 'experience_version': experience['version'],
              'chart': 'same_reconstructed_completed_bars_per_pair', 'scope': 'single_candidate_screen_and_shared_exit_rules',
              'fee_bps_per_side': FEE * 10_000, 'cash': CASH, 'tranche': TRANCHE, 'pairs': rows,
              'virtual_books': simulation}
    atomic_write(out / 'result.json', result)
    return result


def main():
    from dotenv import dotenv_values
    from hmac import compare_digest
    import os

    from local_env import ENV

    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--day', type=date.fromisoformat, required=True)
    cli.add_argument('--symbols', nargs='+', required=True)
    cli.add_argument('--source', type=Path, required=True)
    cli.add_argument('--closed-backup', action='store_true')
    cli.add_argument('--training', type=Path, required=True)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--independent-quota', action='store_true', required=True)
    args = cli.parse_args()
    if not 1 <= len(set(args.symbols)) == len(args.symbols) <= 2 or not all(map(is_code, args.symbols)):
        cli.error('provide one or two distinct KRX codes')
    credentials = dotenv_values(ENV, interpolate=False)
    key = os.getenv('JEV_ISOLATED_API_KEY') or credentials.get('JEV_ISOLATED_API_KEY')
    production = credentials.get('JEV_API_KEY') or os.getenv('JEV_API_KEY')
    if not key or not production or compare_digest(key, production):
        cli.error('distinct isolated key required')
    try:
        source = snapshot_folder(args.source) if args.closed_backup else args.source.resolve(strict=True)
        out = output_folder(args.output, source)
        out.mkdir(mode=0o700, parents=True, exist_ok=False)
        result = run(args.day, args.symbols, source, args.training, out, key, closed_backup=args.closed_backup)
        print(json.dumps({'pairs': len(result['pairs']), 'output': str(out / 'result.json')}, ensure_ascii=False))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f'isolated comparison failed: {type(exc).__name__}', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
