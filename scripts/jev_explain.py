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
import json
from datetime import datetime
from pathlib import Path

from sqlite_ro import ro_connect

ROOT = Path.home() / '.vibe-trading/nhplug/shared-etf-paper'
LABEL = {'why_driver': '결정 요인', 'why_chart': '차트·당일 흐름', 'why_news': '뉴스', 'why_market': '시황',
         'why_flows': '수급', 'why_history': '자기 성적·일지', 'why_data': '자료 충분성'}


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
    parser.add_argument('--question', help='candidate / holding / oversold / watchlist')
    parser.add_argument('--root', default=str(ROOT))
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
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
