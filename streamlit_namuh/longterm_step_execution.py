"""Read-only links between a long-term step plan, guarded orders and broker fills."""
from __future__ import annotations

from pathlib import Path
import sqlite3


def read_step_intents(nhplug, events):
    """Read only exact fingerprints supplied by the already account-filtered event stream."""
    from sqlite_ro import ro_connect

    fingerprints = {e['intent'] for e in events if isinstance(e.get('intent'), str) and e['intent']}
    if not fingerprints:
        return []
    try:
        with ro_connect(Path(nhplug) / 'live-orders.sqlite3', timeout=0.2) as db:
            out = []
            for fingerprint in fingerprints:
                row = db.execute('SELECT fingerprint,operation,symbol,order_number,day,qty,state,filled '
                                 'FROM live_intents WHERE fingerprint=?', (fingerprint,)).fetchone()
                if row:
                    out.append(dict(zip(('fingerprint', 'operation', 'symbol', 'order_number', 'day', 'qty', 'state', 'filled'), row)))
            return out
    except (OSError, sqlite3.Error):
        return []


def step_order_rows(plan, events, fills, intents=(), *, codes=None, index=None):
    """Return step-plan progress. Only an exact guard fingerprint and order number can link a fill."""
    day = plan.get('day')
    notes = (plan.get('info') or {}).get('notes') or []
    prefix = f'여운봉 단계 1: {index} ' if index else '여운봉 단계 1:'
    if not day or not any(str(note).startswith(prefix) for note in notes):
        return []
    orders = [o for o in plan.get('orders') or [] if o.get('side') == 'BUY'
              and 'plan' in str(o.get('source', '')).split('+') and (codes is None or o.get('code') in codes)]
    out = []
    for order in orders:
        code = order.get('code')
        sent = order.get('sent') or {}
        status = order.get('status')
        qty = sent.get('qty')
        candidates = [e for e in events if str(e.get('time', '')).startswith(day) and e.get('action') == 'BUY'
                      and e.get('symbol') == code and e.get('execution') == 'ACKNOWLEDGED_NOT_FILLED' and e.get('intent')]
        fingerprints = {e['intent'] for e in candidates}
        matching_intents = [i for i in intents if i.get('fingerprint') in fingerprints and i.get('symbol') == code
                            and i.get('operation') == 'krstockOrderCashBuy' and i.get('day') == day.replace('-', '')
                            and i.get('qty') == qty and i.get('order_number')]
        ids = {str(i['order_number']) for i in matching_intents}
        matched = [f for f in fills if str(f.get('time', '')).startswith(day) and f.get('symbol') == code
                   and f.get('side') == 'BUY' and str(f.get('order_no')) in ids]
        unique = (status == 'sent' and type(qty) is int and qty > 0 and
                  sum(other.get('code') == code for other in orders) == 1 and len(fingerprints) == len(matching_intents) == 1)
        broker_fill = matched[0] if unique and len(matched) == 1 and 0 < matched[0]['qty'] <= qty else None
        filled = broker_fill['qty'] if broker_fill else None
        terminal = matching_intents[0] if unique and matching_intents[0]['state'] == 'terminal' else {}
        if terminal and filled == terminal.get('filled') == qty:
            state = '전량 체결 확인'
        elif terminal and filled is not None and filled == terminal.get('filled'):
            state = '일부 체결 · 잔량 종료'
        elif terminal and terminal.get('filled') == 0 and not matched:
            filled, state = 0, '종료 · 체결 없음'
        elif filled is not None:
            state = '전량 체결 확인' if filled == qty else '일부 체결 확인'
        elif status == 'sent':
            state = '접수 확인 · 체결 미확인' if unique else '주문 기록 · 체결 미확인'
        else:
            state = {'planned': '주문 대기', 'blocked': '보호 차단', 'skipped': '주문 건너뜀',
                     'dry_run': '가상 계획'}.get(status, '상태 미확인')
        out.append({'종목코드': code, '계획 배정액': order.get('krw'), '계획 수량': order.get('qty'), '주문 수량': qty,
                    '확인 체결 수량': filled, '평균 체결가': (broker_fill or {}).get('price'), '상태': state,
                    '구분': '단계·적립식 합산 주문' if 'dca' in str(order.get('source', '')).split('+') else '단계 주문',
                    '사유': '' if status == 'sent' else (order.get('reason') or '')})
    return out
