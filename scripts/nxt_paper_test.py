"""One-time NXT paper order test (user approval 2026-11-11; the 08:05 run was missed in a reboot, so re-approved
2026-11-12 15:2x for the NXT after-market the same day, 15:35-19:30, after the runner has stopped for the day).

Question: does the NH paper account accept an order routed to NXT outside KRX hours (after-market here)?
Steps, all through the guarded paper path the runner uses:
  1. NXT quote for 005930; a 1-share limit buy about 25% under it (cannot fill; still inside the daily price band).
  2. A one-use token lets the guard's date-locked NXT exception accept exactly this order (see NXT_TEST in
     agent/src/tools/nhplug_order_guard.py); the token is removed whatever happens.
  3. Accepted: cancel the whole order at once, then read today's executions until the broker shows it cancelled
     (that read also closes the guard's records). Rejected: confirm the broker has no such order and close the
     guard's record, so the runner's orders are not blocked for the day.
  4. If it ever filled, write STOP so the next 09:10 start does not run with a position the runner does not know.
Result: shared-etf-paper/nxt-test-result.json. Never deletes records.
"""
from __future__ import annotations

import json
import math
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
SYMBOL, DAY, START, LAST_START = '005930', '20261112', '15:35', '19:20'
DISCOUNT = 0.75


def limit_price(price, previous_close):
    """About 25% under the quote on the KRX tick grid, never below the -30% daily limit; None when no room."""
    from backtest.engines.korea_equity import krx_tick_size
    target = price * DISCOUNT
    tick = int(krx_tick_size(target))
    limit = int(math.floor(target / tick) * tick)
    floor = previous_close * 0.7
    return limit if limit > floor + tick else None


def main():
    from src.config.loader import load_agent_config
    from src.config.paths import get_runtime_root
    from src.tools.mcp import MCPServerAdapter
    from src.tools.nhplug_order_guard import NHOrderGuard, NXT_TEST
    from nh_paper_isolated import paper_account, paper_pages, shared_default_config

    now = datetime.now(KST)
    runtime = get_runtime_root()
    root = runtime / 'nhplug' / 'shared-etf-paper'
    result = {'time': now.isoformat(timespec='seconds'), 'symbol': SYMBOL, 'market': 'NXT'}

    def finish(**fields):
        result.update(fields)
        path = root / 'nxt-test-result.json'
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
        os.chmod(path, 0o600)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return result

    if now.strftime('%Y%m%d') != DAY or not START <= now.strftime('%H:%M') < LAST_START:
        return finish(outcome='skipped', reason='outside the approved test window')
    if (root / 'STOP').exists():
        return finish(outcome='skipped', reason='STOP file present')

    configs = load_agent_config().mcp_servers
    config = shared_default_config(configs, runtime, MCPServerAdapter)
    account = paper_account(config)
    broker_config = config.model_copy(update={'args': [*config.args, '--guarded-paper']})
    broker = MCPServerAdapter('nhplug_paper_isolated', broker_config)
    read = MCPServerAdapter('nhplug_readonly', configs['nhplug_readonly'])

    def history():
        data = paper_pages(broker, 'krstockInquiryDailyOrderExecution', {'act_no': account, 'orr_dt': DAY,
                           'orr_mkt_cd': '00', 'ost_cns_dit': '0'}, 'Output_0')
        rows = data.get('Output_0')
        return rows if isinstance(rows, list) else None

    quote = read.call_tool('call_api', {'operationId': 'krstockQuoteCurrentPrice', 'input': {'market_cd': 'NXT', 'iem_cd': SYMBOL}})
    output = ((quote.get('data') or {}).get('Output_0') or {}) if quote.get('status') == 'ok' else {}
    price, previous = output.get('stck_prpr'), output.get('stck_prdy_clpr')
    if type(price) not in (int, float) or type(previous) not in (int, float) or price <= 0 or previous <= 0:
        return finish(outcome='skipped', reason='NXT quote unavailable', quote_rsp=(quote.get('data') or {}).get('rsp_msg'))
    limit = limit_price(price, previous)
    if limit is None:
        return finish(outcome='skipped', reason='no safe price inside the daily band', price=price, previous_close=previous)
    result.update(nxt_price=price, previous_close=previous, limit=limit)

    token = runtime / 'nhplug' / NXT_TEST['token']
    token.write_text(json.dumps({'day': DAY, 'symbol': SYMBOL, 'max_price': limit}), encoding='utf-8')
    os.chmod(token, 0o600)
    fields = {'act_no': account, 'iem_cd': SYMBOL, 'orr_qty': 1, 'orr_pr': limit, 'nmn_pr_tp_cd': '01',
              'orr_cnd_dit_cd': '00', 'ssl_nmn_pr_dit_cd': '00', 'rmt_mkt_cd': 'NXT', 'sor_mkt_sli_yn': 'N'}
    try:
        response = broker.call_tool('call_api', {'operationId': 'krstockOrderCashBuy', 'input': fields},
                                    order_intent_id=uuid.uuid4().hex, pre_reserve_check=lambda: True,
                                    pre_send_check=lambda: True,
                                    order_guard_deadline_ms=int(time.time() * 1000) + 60_000)
    finally:
        for leftover in (token, token.with_suffix('.used')):
            leftover.unlink(missing_ok=True)  # the exception is single-use whatever happened
    data = response.get('data') if isinstance(response.get('data'), dict) else {}
    number = (data.get('Output_0') or {}).get('mkt_orr_no') if isinstance(data.get('Output_0'), dict) else None
    result['order'] = {'status': response.get('status'), 'rsp_cd': data.get('rsp_cd'), 'rsp_msg': data.get('rsp_msg'),
                       'error_type': response.get('error_type'), 'error': str(response.get('error') or '')[:200],
                       'accepted': type(number) is int and number > 0}

    if response.get('error_type') == 'NHOrderBlocked' and 'order_safety' not in response:
        return finish(outcome='blocked_by_guard', reason='the guard refused before sending (nothing reached the broker)')

    if type(number) is int and number > 0:  # accepted: cancel at once, then confirm from the broker
        time.sleep(2)
        rows = history() or []
        row = next((r for r in rows if r.get('itg_orr_no') == number), {})
        if (row.get('tot_cns_qty') or 0) > 0:
            (root / 'STOP').write_text('nxt-paper-test: the test order filled; reconcile before trading\n', encoding='utf-8')
            return finish(outcome='filled_unexpectedly', order_row=row)
        cancel = broker.call_tool('call_api', {'operationId': 'krstockOrderCancel', 'input': {
            'act_no': account, 'iem_cd': SYMBOL, 'org_mkt_orr_no': number, 'all_pat_dit_cd': '1'}}, order_intent_id=uuid.uuid4().hex)
        cdata = cancel.get('data') if isinstance(cancel.get('data'), dict) else {}
        result['cancel'] = {'status': cancel.get('status'), 'rsp_cd': cdata.get('rsp_cd'), 'rsp_msg': cdata.get('rsp_msg')}
        for _ in range(6):
            time.sleep(3)
            row = next((r for r in history() or [] if r.get('itg_orr_no') == number), {})
            if row.get('can_qty') == 1 and row.get('ny_cns_qty') == 0:
                break
        if (row.get('tot_cns_qty') or 0) > 0:
            (root / 'STOP').write_text('nxt-paper-test: the test order filled; reconcile before trading\n', encoding='utf-8')
            return finish(outcome='filled_unexpectedly', order_row=row)
        done = row.get('can_qty') == 1 and row.get('ny_cns_qty') == 0
        return finish(outcome='accepted_and_cancelled' if done else 'accepted_cancel_unconfirmed',
                      order_row={k: row.get(k) for k in ('orr_qty', 'tot_cns_qty', 'can_qty', 'ny_cns_qty', 'orr_rjt_rsn_cd_nm')})

    # Not accepted: the broker answered without an order number. Close the guard's record only if no such order exists.
    rows = history()
    guard = NHOrderGuard(broker_config.args)
    import sqlite3
    with sqlite3.connect(guard.path) as db:
        pending = [r[0] for r in db.execute("SELECT fingerprint FROM intents WHERE day=? AND symbol=? AND state!='terminal' "
                                            "AND order_number IS NULL AND operation='krstockOrderCashBuy'", (DAY, SYMBOL))]
    released = []
    for fingerprint in pending:
        try:
            guard.release_rejected_nxt_test(fingerprint, rows)
            released.append(fingerprint[:8])
        except Exception as exc:
            result['release_error'] = f'{type(exc).__name__}: {exc}'
    return finish(outcome='rejected_by_broker', guard_records_closed=len(released),
                  broker_rows_for_symbol=sum(1 for r in rows or [] if r.get('iem_cd') == SYMBOL))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:  # leave a record; an order left open makes the 09:10 runner stop on its own
        out = Path.home() / '.vibe-trading/nhplug/shared-etf-paper/nxt-test-result.json'
        out.write_text(json.dumps({'time': datetime.now(KST).isoformat(timespec='seconds'), 'outcome': 'error',
                                   'error': f'{type(exc).__name__}: {exc}'[:300]}, ensure_ascii=False), encoding='utf-8')
        os.chmod(out, 0o600)
        raise
