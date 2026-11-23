from longterm_step_execution import read_step_intents, step_order_rows


def test_guard_lookup_reads_only_requested_fingerprint(tmp_path):
    import sqlite3

    with sqlite3.connect(tmp_path / 'live-orders.sqlite3') as db:
        db.execute('CREATE TABLE live_intents (fingerprint TEXT PRIMARY KEY, operation TEXT, symbol TEXT, '
                   'order_number INTEGER, day TEXT, qty INTEGER, state TEXT, filled INTEGER)')
        db.executemany('INSERT INTO live_intents VALUES (?,?,?,?,?,?,?,?)',
                       [('abc', 'krstockOrderCashBuy', '379810', 22, '20261123', 5, 'terminal', 3),
                        ('other', 'krstockOrderCashBuy', '379810', 23, '20261123', 5, 'terminal', 5)])
    rows = read_step_intents(tmp_path, [{'intent': 'abc'}, {'intent': None}])
    assert len(rows) == 1 and rows[0]['order_number'] == 22
    assert read_step_intents(tmp_path / 'missing', [{'intent': 'abc'}]) == []


def test_step_fill_requires_exact_guard_fingerprint_and_order_number():
    plan = {'day': '2026-11-23', 'info': {'notes': ['여운봉 단계 1: NDX']},
            'orders': [{'code': '379810', 'side': 'BUY', 'source': 'plan', 'qty': 5,
                        'status': 'sent', 'sent': {'qty': 5}}]}
    events = [{'time': '2026-11-23T09:41:00+09:00', 'action': 'BUY', 'symbol': '379810',
               'execution': 'ACKNOWLEDGED_NOT_FILLED', 'intent': 'abc'}]
    fills = [{'time': '2026-11-23T09:42:00+09:00', 'symbol': '379810', 'side': 'BUY', 'order_no': 22, 'qty': 3}]
    assert step_order_rows(plan, events, fills)[0]['상태'] == '주문 기록 · 체결 미확인'
    intents = [{'fingerprint': 'abc', 'symbol': '379810', 'operation': 'krstockOrderCashBuy', 'order_number': 22,
                'day': '20261123', 'qty': 5, 'state': 'acknowledged'}]
    row = step_order_rows(plan, events, fills, intents)[0]
    assert (row['상태'], row['확인 체결 수량']) == ('일부 체결 확인', 3)
    fills[0]['qty'] = 5
    assert step_order_rows(plan, events, fills, intents)[0]['상태'] == '전량 체결 확인'
    intents[0].update(state='terminal', filled=0)
    assert step_order_rows(plan, events, [], intents)[0]['상태'] == '종료 · 체결 없음'
    assert not step_order_rows({**plan, 'info': {'notes': []}}, events, fills, intents)


def test_ambiguous_duplicate_orders_and_dca_are_not_misattributed():
    order = {'code': '379810', 'side': 'BUY', 'source': 'plan+dca', 'qty': 3,
             'status': 'sent', 'sent': {'qty': 3}}
    plan = {'day': '2026-11-23', 'info': {'notes': ['여운봉 단계 1: NDX']}, 'orders': [order, order.copy()]}
    event = {'time': '2026-11-23T09:41:00+09:00', 'action': 'BUY', 'symbol': '379810',
             'execution': 'ACKNOWLEDGED_NOT_FILLED', 'intent': 'abc'}
    intent = {'fingerprint': 'abc', 'symbol': '379810', 'operation': 'krstockOrderCashBuy', 'order_number': 1,
              'day': '20261123', 'qty': 3, 'state': 'terminal', 'filled': 3}
    fill = {'time': event['time'], 'symbol': '379810', 'side': 'BUY', 'order_no': 1, 'qty': 3}
    rows = step_order_rows(plan, [event], [fill], [intent])
    assert all(r['상태'] == '주문 기록 · 체결 미확인' and r['확인 체결 수량'] is None for r in rows)
    assert rows[0]['구분'] == '단계·적립식 합산 주문'
