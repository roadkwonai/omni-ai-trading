"""Collect the intraday report's approved facts before calling the tool-free model."""
from __future__ import annotations

import json
from pathlib import Path

from schedule_antigravity import test_model
from sqlite_ro import day_rows

TARGETS = frozenset({'trading-intraday-report'})


def facts(root, slot):
    from intraday_audit import _json, _query, ledger, services

    root = Path(root) / 'nhplug/shared-etf-paper'
    day = slot.date().isoformat()
    starts, _ = day_rows(root / 'trading-day-start.jsonl', day)
    decisions = [item.get('decision') for item in starts or [] if isinstance(item, dict)]
    if slot.weekday() >= 5 or (decisions and decisions[-1] == 'skip_closed'):
        return {'trading_day': False, 'date': day}
    trading_day = True if decisions and decisions[-1] in ('start', 'already_running') else None
    summary = ledger(root, day, None)
    data = {k: summary.get(k) for k in ('fills', 'unfinished_intents', 'events', 'last_event', 'entry_blocks', 'errors')}
    holdings = _query(root / 'orders.sqlite3', """
        SELECT symbol, SUM(CASE side WHEN 'BUY' THEN quantity WHEN 'SELL' THEN -quantity END)
        FROM fills GROUP BY symbol HAVING SUM(CASE side WHEN 'BUY' THEN quantity WHEN 'SELL' THEN -quantity END) > 0
    """)
    data['holdings'] = holdings
    plans = _json(root / 'exit-plans.json') or {}
    data['exit_plans'] = {
        code: {k: plan[k] for k in ('profile', 'half_pct', 'all_pct', 'peak') if k in plan}
        for code, _ in holdings or [] if isinstance(plan := plans.get(code), dict)
    } if isinstance(plans, dict) else {}
    events, _ = day_rows(root / 'events.jsonl', day)
    jev = next((e['jev'] for e in reversed(events or [])
                if isinstance(e.get('jev'), dict) and isinstance(e['jev'].get('candidate'), dict)), {})
    candidate = jev.get('candidate') or {}
    data['last_jev'] = {k: candidate[k] for k in ('choice', 'confidence') if k in candidate}
    if jev.get('response_time'):
        data['last_jev']['time'] = jev['response_time']
    market = _json(root / 'market-context.json') or {}
    indexes = market.get('indices') if isinstance(market, dict) else None
    market = market if isinstance(market, dict) else {}
    data['market'] = {'time': market.get('time'), 'indexes': [
        {'name': i.get('name'), 'change_pct': i.get('change_pct'), 'flow': i.get('flow'),
         'regime': (i.get('regime') or {}).get('label')} for i in (indexes.values() if isinstance(indexes, dict) else [])
        if isinstance(i, dict) and isinstance(i.get('regime', {}), dict)]}
    service_names = ('shared-etf-paper', 'shared-etf-learning', 'market-movers', 'shared-etf-regime')
    data['services'] = {name: {k: row[k] for k in ('registered', 'running', 'unknown') if k in row}
                        for name, row in services().items() if name in service_names}
    return {'trading_day': trading_day, 'date': day, 'startup': decisions[-1] if decisions else None, **data}


def run(profile, binary, work, root, slot, prompt):
    data = facts(root, slot)
    if data['trading_day'] is False:
        return '휴장', None
    instructions = (
        '다음은 실행할 보고서의 원본 지시문과 로컬 조회기로 준비한 비식별 자료입니다. '
        '자료 조회 단계는 완료했습니다. 제공된 자료만 사용하고 도구를 호출하지 마세요. '
        '빠진 자료와 거래일 확인이 필요한 경우 그대로 표시하세요. 원본 보고 형식대로 작성하세요.\n\n'
        + prompt + '\n\n조회 자료\n' + json.dumps(data, ensure_ascii=False, default=str)
    )
    answer, used = test_model(profile, binary, instructions, work)
    if not answer or (used and used != profile['model']):
        return None, None
    if answer.strip().split(maxsplit=1)[0].strip('**:') not in ('정상', '주의', '문제'):
        return None, None
    return answer, used
