"""Explain Jev decisions from what was recorded (read-only, user 2026-11-18).

    PYTHONPATH=agent:scripts .venv/bin/python scripts/jev_explain.py --at 2026-11-18T11:21        # one call
    PYTHONPATH=agent:scripts .venv/bin/python scripts/jev_explain.py --since 2026-11-18T09:00     # aggregate

One call: the answer, the evidence answers (what drove it, which way each input pointed, whether the data was enough),
the inputs it was given with their size and age, the session summary of the chosen chart and the headlines.
Aggregate: intraday_audit.jev_inputs (input presence, freshness, evidence distribution). Evidence is the model's own
account of its answer, not a measured cause: check a doubtful one with a replay that removes that input.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from math import ceil
from statistics import median
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlite_ro import ro_connect

ROOT = Path.home() / '.vibe-trading/nhplug/shared-etf-paper'
LABEL = {'why_driver': '결정 요인', 'why_chart': '차트·당일 흐름', 'why_news': '뉴스', 'why_market': '시황',
         'why_flows': '수급', 'why_history': '자기 성적·일지', 'why_data': '자료 충분성'}


def json_object(raw):
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def request_metrics(root, since, *, question=None):
    kst = ZoneInfo('Asia/Seoul')
    start = datetime.fromisoformat(since)
    since = (start.astimezone(kst) if start.tzinfo is not None else start.replace(tzinfo=kst)).isoformat()
    groups = defaultdict(lambda: {'success': [], 'success_unmeasured': 0, 'http_errors_measured': 0,
                                  'http_errors_unmeasured': 0, 'preflight_rejected': 0, 'response_rejected': 0,
                                  'transport_errors': 0, 'invalid_rows': 0})
    with ro_connect(Path(root) / 'market-research.sqlite3') as db:
        for name, raw in db.execute("SELECT question,response FROM model_inputs WHERE engine='jev' AND time>=?", (since,)):
            if question is not None and name != question:
                continue
            group = groups[name]
            response = json_object(raw)
            if response is None:
                group['invalid_rows'] += 1
                continue
            metrics = response.get('request_metrics')
            if isinstance(metrics, dict) and metrics:
                group['success'].append(metrics)
            elif metrics is not None and not isinstance(metrics, dict):
                group['invalid_rows'] += 1
            else:
                group['success_unmeasured'] += 1
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='snapshots'").fetchone():
            for kind, raw in db.execute("SELECT kind,payload FROM snapshots WHERE kind IN ('jev_http_error','jev_request_rejected','jev_response_rejected','jev_transport_error') AND time>=?", (since,)):
                details = json_object(raw)
                name = details.get('question') if details is not None else None
                if not isinstance(name, str) or not name:
                    if question is None:
                        groups['_unattributed']['invalid_rows'] += 1
                    continue
                if question is not None and name != question:
                    continue
                group = groups[name]
                key = ('preflight_rejected' if kind == 'jev_request_rejected' else
                       'response_rejected' if kind == 'jev_response_rejected' else
                       'transport_errors' if kind == 'jev_transport_error' else
                       'http_errors_measured' if isinstance(details.get('request_metrics'), dict)
                       and details['request_metrics'] else 'http_errors_unmeasured')
                group[key] += 1
    for group in groups.values():
        samples = group.pop('success')
        measured = group['success_measured'] = len(samples)
        errors = group['http_errors_measured']
        total = measured + errors + group['response_rejected'] + group['transport_errors']
        group['http_error_share_recorded'] = round(errors / total, 4) if total else None
        for key in ('input_tokens', 'chart_ms', 'api_ms', 'total_ms', 'request_bytes'):
            values = sorted(value for m in samples if type(value := m.get(key)) in (int, float)
                            and 0 <= value <= 2**63 - 1)
            group[key] = {'samples': len(values), 'p50': median(values) if values else None,
                          'p95': values[ceil(len(values) * .95) - 1] if values else None}
    return dict(groups)


def one(root, at, question=None):
    with ro_connect(Path(root) / 'market-research.sqlite3') as db:
        sql = "SELECT digest, time, question, inputs, response FROM model_inputs WHERE engine='jev' AND time >= ?"
        args = [at]
        if question:
            sql += ' AND question = ?'
            args.append(question)
        row = db.execute(sql + ' ORDER BY time LIMIT 1', args).fetchone()
        if not row:
            return None
        digest, stamp, question, raw, response = row
        try:
            found = db.execute('SELECT evidence FROM model_evidence WHERE digest=?', (digest,)).fetchone()
        except Exception:
            found = None
    inputs, answer = json.loads(raw), json.loads(response)
    state, when = inputs['state'], datetime.fromisoformat(stamp)
    sizes = {k: len(json.dumps(v, ensure_ascii=False)) for k, v in state.items()}
    news = [n for n in state.get('recent_news_headlines') or [] if isinstance(n, dict)]
    ages = []
    for n in news:
        try:
            ages.append(round((when - datetime.fromisoformat(n['published'].replace('Z', '+00:00'))).total_seconds() / 60))
        except (KeyError, ValueError):
            ages.append(None)
    chosen = answer.get('choice')
    return {'time': stamp, 'question': question, 'digest': digest, 'answer': answer,
            'evidence': json.loads(found[0]) if found else None, 'input_chars': sizes,
            'market_as_of': (state.get('market_regime_themes_global') or {}).get('as_of'),
            'session_summary': (state.get('intraday_summary') or {}).get(chosen) or state.get('intraday_summary', {}).get('K200'),
            'headlines': [{'age_min': a, 'title': n.get('title')} for a, n in zip(ages, news)],
            'instructions': inputs.get('instructions')}


def show(item):
    print(f"[{item['time'][11:19]}] {item['question']} → {item['answer'].get('choice')} {item['answer'].get('confidence')}")
    print('  확률:', item['answer'].get('probabilities'))
    if item['evidence']:
        for key, v in item['evidence'].items():
            print(f"  근거 {LABEL.get(key, key)}: {v['choice']} ({v['confidence']})")
    else:
        print('  근거: 기록 없음(2026-11-18 이전 요청이거나 응답에 없음)')
    print('  시황 기준 시각:', item['market_as_of'], '| 입력 크기(자):', item['input_chars'])
    print('  당일 요약:', item['session_summary'])
    for h in item['headlines']:
        print(f"  뉴스 {h['age_min']}분 전: {h['title']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--at', help='first Jev call at or after this ISO time (Asia/Seoul)')
    parser.add_argument('--since', help='aggregate every Jev call since this ISO time')
    parser.add_argument('--metrics', action='store_true', help='show recorded request metrics since --since')
    parser.add_argument('--question', help='candidate / holding / oversold / watchlist')
    parser.add_argument('--root', default=str(ROOT))
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    if args.metrics:
        if not args.since:
            parser.error('--metrics requires --since')
        since = datetime.fromisoformat(args.since).isoformat()
        print(json.dumps(request_metrics(args.root, since, question=args.question), ensure_ascii=False, indent=1))
        return
    if args.since:
        from intraday_audit import jev_inputs
        since = datetime.fromisoformat(args.since)
        print(json.dumps(jev_inputs(args.root, since.date().isoformat(), since), ensure_ascii=False, indent=1))
        return
    item = one(args.root, args.at or datetime.now().isoformat()[:10], args.question)
    if item is None:
        raise SystemExit('그 시각 뒤의 Jev 요청 기록이 없습니다.')
    print(json.dumps(item, ensure_ascii=False, indent=1)) if args.json else show(item)


if __name__ == '__main__':
    main()
