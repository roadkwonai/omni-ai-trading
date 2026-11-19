"""Read-only NH snapshots for live guard experiments; no order transport."""
from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from src.tools.nhplug_live_guard import LiveOrderBlocked, SYMBOL

KST = ZoneInfo('Asia/Seoul')


RATE_LIMIT_WAITS = (0.5, 1.0, 2.0)  # NH's per-app call limit (IGW42902) is retryable; every service shares one app key


def _request(adapter, tool, params, sleep=time.sleep):
    """One read-only call; NH's rate-limit refusal is retried after short waits (reads only, so a retry is safe)."""
    for wait in (*RATE_LIMIT_WAITS, None):
        result = adapter.call_tool(tool, params)
        if isinstance(result, dict) and result.get('status') == 'ok' and isinstance(result.get('data'), dict):
            return result
        if wait is None or 'IGW42902' not in str((result or {}).get('error', '')):
            break
        sleep(wait)
    raise LiveOrderBlocked('broker rate limit' if 'IGW42902' in str((result or {}).get('error', '')) else 'broker request failed')


def _pages(adapter, operation, fields, block):
    cursor, flag, seen, rows, first = None, None, set(), [], None
    for _ in range(20):
        args = {'operationId': operation, 'input': fields, 'want_pagination': True}
        if cursor is not None:
            args.update(cts=cursor, cts_flag=flag)
        data = _request(adapter, 'call_api', args)['data']
        code, msg = data.get('rsp_cd'), data.get('rsp_msg')
        if not isinstance(msg, str):
            raise LiveOrderBlocked('broker page message missing')
        empty = code == '11512' and msg.strip().rstrip('.') == '데이터가 존재하지 않습니다'
        continued = code == '00218' and msg.strip().rstrip('.') == '계속 조회시 다음(연속조회) 버튼을 누르시기 바랍니다'
        if not empty and not continued and (code not in ('00000', '00166') or msg.strip().rstrip('.') != '조회가 완료되었습니다'):
            raise LiveOrderBlocked('broker page business failure')
        page, meta = data.get(block), data.get('_nh_pagination')
        if empty and page is None:
            page = []
        if not isinstance(page, list) or not isinstance(meta, dict):
            raise LiveOrderBlocked('broker page or continuation missing')
        if first is None:
            first = dict(data)
        rows.extend(page)
        if meta.get('complete') is True:
            first[block] = rows
            first['rsp_cd'], first['rsp_msg'] = code, msg
            first['_pagination_complete'] = True
            return first
        if meta.get('complete') is not False:
            raise LiveOrderBlocked('broker pagination completeness unknown')
        cursor, flag = meta.get('cts'), meta.get('cts_flag')
        if not isinstance(cursor, str) or not cursor or not isinstance(flag, str) or not flag or (cursor, flag) in seen:
            raise LiveOrderBlocked('broker pagination cursor invalid')
        seen.add((cursor, flag))
    raise LiveOrderBlocked('broker pagination page limit reached')


def collect_snapshots(adapter, account, now, sleep=time.sleep, symbol=SYMBOL):
    """Fetch guard inputs (quotes for `symbol`) from the configured read-only broker; never place an order.
    `symbol=None` skips the two quotes and their 2-second gap and the account list (the live mirror's per-minute read,
    which quotes its own symbols and uses neither; its orders re-read everything through nh_guarded_live)."""
    if not isinstance(account, str) or len(account) != 11 or not account.isascii() or not account.isdigit():
        raise LiveOrderBlocked('invalid account')
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise LiveOrderBlocked('clock unavailable')
    config = getattr(adapter, 'server_config', None)
    args = getattr(config, 'args', ())
    if getattr(adapter, 'server_name', None) != 'nhplug_readonly' or args.count('--mode') != 1 or args[args.index('--mode') + 1:args.index('--mode') + 2] != ['readonly']:
        raise LiveOrderBlocked('only configured NH readonly transport permitted')
    day = now.astimezone(KST).strftime('%Y%m%d')
    accounts = _request(adapter, 'list_accounts', {})['data'] if symbol is not None else None
    history = _pages(adapter, 'krstockInquiryDailyOrderExecution', {'act_no':account, 'orr_dt':day, 'orr_mkt_cd':'00', 'ost_cns_dit':'0'}, 'Output_0')
    history.update(_account=account, _day=day)
    balance = _pages(adapter, 'krstockInquiryBalance', {'act_no':account, 'bnc_bse_cd':'5', 'ltg_aot_dit_cd':'9', 'aet_bse':'2', 'qut_dit_cd':'UNT', 'aly_qut_cd':'1'}, 'Output_1')
    balance['_account'] = account
    pnl = _request(adapter, 'call_api', {'operationId':'krstockInquiryRealizedPnl', 'input':{'act_no':account,
        'iqr_dit_cd1':'0', 'fee_dit_cd':'1', 'qut_dit_cd':'KRX', 'aly_qut_cd':'1'}})['data']
    if symbol is None:
        return {'history':history, 'balance':balance, 'pnl':pnl}
    previous_quote = _request(adapter, 'get_stock_price', {'stock_code':symbol})
    sleep(2)
    quote = _request(adapter, 'get_stock_price', {'stock_code':symbol})
    return {'accounts':accounts, 'history':history, 'balance':balance, 'pnl':pnl,
            'previous_quote':previous_quote, 'quote':quote}
