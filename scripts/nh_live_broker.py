"""Read-only NH snapshots for live guard experiments; no order transport."""
from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from src.tools.nhplug_live_guard import LiveOrderBlocked, SYMBOL

KST = ZoneInfo('Asia/Seoul')
BALANCE_FIELDS = {'bnc_bse_cd': '5', 'ltg_aot_dit_cd': '9', 'aet_bse': '2', 'qut_dit_cd': 'UNT', 'aly_qut_cd': '1'}


RATE_LIMIT_WAITS = (0.5, 1.0, 2.0)  # NH's per-app call limit (IGW42902) is retryable; every service shares one app key
# NH answers one request of a kind about once a second (11-19, measured on the paper balance: a continuation page asked
# 0.5 s after the previous one's start was refused with IGW42903, 1.0 s passed); a continuation page waits this long.
PAGE_GAP = 1.1
# NH gives these balance totals only once every holding is read, so a paged balance carries 0 until its last page
BALANCE_TOTALS = ('nas_amt', 'tot_aet_amt', 'tot_byn_amt', 'tot_eal_amt', 'tot_eal_pls', 'pft_rt')


def linked_accounts(reader, until, wait=60):
    """The broker's linked accounts ([] when none came by `until()` turning True). A service starts once a day, so a call
    that fails there (the NH process missed its 8 s start, 2026-11-25) is asked again, never taken as "no account"."""
    while True:
        answer = reader.call_tool('list_accounts', {})
        data = answer.get('data') if isinstance(answer, dict) and answer.get('status') == 'ok' else None
        if rows := (data or {}).get('Output_0'):
            return rows
        if until():
            return []
        print('account list unavailable; asked again', flush=True)
        time.sleep(wait)


def orderable_cash(summary):
    """The cash a live account may plan buys with: orr_pbl_amt4 (100% orderable, a sale that settles in two days counted) within
    the D+2 deposit, else orr_pbl_amt1 (settled only). 2026-11-24: 005930's sale never came back in orr_pbl_amt1. The guard
    still asks NH for the very order (nh_guarded_live._buyable)."""
    summary = summary if isinstance(summary, dict) else {}
    settled, full, d2 = (summary.get(k) for k in ('orr_pbl_amt1', 'orr_pbl_amt4', 'nxt2_dd_dca'))
    if type(full) is int and type(d2) is int and full > 0 and d2 > 0:
        return min(full, d2)
    return settled


def _request(adapter, tool, params, sleep=time.sleep):
    """One read-only call; NH's rate-limit refusal is retried after short waits (reads only, so a retry is safe)."""
    for wait in (*RATE_LIMIT_WAITS, None):
        result = adapter.call_tool(tool, params)
        if isinstance(result, dict) and result.get('status') == 'ok' and isinstance(result.get('data'), dict):
            return result
        if wait is None or not rate_limited(result):
            break
        sleep(wait)
    raise LiveOrderBlocked('broker rate limit' if rate_limited(result) else 'broker request failed')


def rate_limited(result):
    """NH's per-app limit (IGW42902) or its same-query limit (IGW42903, ~1 request a second, 11-19); both retryable."""
    error = str((result or {}).get('error', ''))
    return 'IGW42902' in error or 'IGW42903' in error


def _pages(adapter, operation, fields, block, clock=time.monotonic, sleep=time.sleep):
    cursor, flag, seen, rows, first = None, None, set(), [], None
    started = 0.0
    for _ in range(20):
        args = {'operationId': operation, 'input': fields, 'want_pagination': True}
        if cursor is not None:
            args.update(cts=cursor, cts_flag=flag)
            sleep(max(0.0, started + PAGE_GAP - clock()))
        started = clock()
        data = _request(adapter, 'call_api', args, sleep=sleep)['data']
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
            if cursor is not None and block == 'Output_1' and isinstance(first.get('Output_0'), dict):
                last = data.get('Output_0')
                last = last if isinstance(last, dict) else {}
                first['Output_0'] = {**{k: v for k, v in first['Output_0'].items() if k not in BALANCE_TOTALS},
                                     **{k: last[k] for k in BALANCE_TOTALS if k in last}}
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


def require_readonly(adapter):
    args: list[str] = list(getattr(getattr(adapter, 'server_config', None), 'args', ()))
    if (getattr(adapter, 'server_name', None) != 'nhplug_readonly' or args.count('--mode') != 1
            or args[args.index('--mode') + 1:args.index('--mode') + 2] != ['readonly']):
        raise LiveOrderBlocked('only configured NH readonly transport permitted')


def read_balance(adapter, account):
    data = _pages(adapter, 'krstockInquiryBalance', {'act_no': account, **BALANCE_FIELDS}, 'Output_1')
    if (data.get('_pagination_complete') is not True or not isinstance(data.get('Output_0'), dict)
            or not isinstance(data.get('Output_1'), list)):
        raise ValueError('실계좌 잔고 확인 불가')
    return data


def read_linked_balance(adapter, account_key, record):
    from accounts import resolve

    require_readonly(adapter)
    linked = _request(adapter, 'list_accounts', {})['data'].get('Output_0')
    if not isinstance(linked, list):
        raise ValueError('실계좌 연결 확인 불가')
    account = resolve({account_key: record}, linked).get(account_key)
    if not isinstance(account, str) or len(account) != 11 or not account.isascii() or not account.isdigit():
        raise ValueError('실계좌 연결 확인 불가')
    result = read_balance(adapter, account)
    return result, datetime.now(KST)


def quote_before_advance(reader, symbol, limit=20, step=2, sleep=time.sleep):
    """The quote of `symbol` read just before its clock moved on, for collect_snapshots(previous_quote=); None when it did not
    move within `limit` seconds or could not be read. The guard buys only on a quote that moved between its two reads, 2 s
    apart; a thinly traded ETF's moves every 6~20 s (357870, refused from 09:41 to 09:48 on 2026-11-25)."""
    stamp = lambda quote: str((quote['data'].get('Output_0') or {}).get('hoga_bsop_hour') or '')  # noqa: E731
    try:
        first = _request(reader, 'get_stock_price', {'stock_code': symbol}, sleep=sleep)
        for _ in range(limit // step):
            sleep(step)
            if stamp(first) < stamp(_request(reader, 'get_stock_price', {'stock_code': symbol}, sleep=sleep)):
                return first
    except LiveOrderBlocked:
        pass
    return None


def collect_snapshots(adapter, account, now, sleep=time.sleep, symbol=SYMBOL, previous_quote=None):
    """Fetch guard inputs (quotes for `symbol`) from the configured read-only broker; never place an order.
    `symbol=None` skips the two quotes and their 2-second gap and the account list (the live mirror's per-minute read,
    which quotes its own symbols and uses neither; its orders re-read everything through nh_guarded_live).
    `previous_quote` (quote_before_advance): read moments ago, so only the current one is read here; the guard checks both."""
    if not isinstance(account, str) or len(account) != 11 or not account.isascii() or not account.isdigit():
        raise LiveOrderBlocked('invalid account')
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise LiveOrderBlocked('clock unavailable')
    require_readonly(adapter)
    day = now.astimezone(KST).strftime('%Y%m%d')
    accounts = _request(adapter, 'list_accounts', {})['data'] if symbol is not None else None
    history = _pages(adapter, 'krstockInquiryDailyOrderExecution', {'act_no':account, 'orr_dt':day, 'orr_mkt_cd':'00', 'ost_cns_dit':'0'}, 'Output_0', sleep=sleep)
    history.update(_account=account, _day=day)
    balance = _pages(adapter, 'krstockInquiryBalance', {'act_no': account, **BALANCE_FIELDS}, 'Output_1', sleep=sleep)
    balance['_account'] = account
    pnl = _request(adapter, 'call_api', {'operationId':'krstockInquiryRealizedPnl', 'input':{'act_no':account,
        'iqr_dit_cd1':'0', 'fee_dit_cd':'1', 'qut_dit_cd':'KRX', 'aly_qut_cd':'1'}})['data']
    if symbol is None:
        return {'history':history, 'balance':balance, 'pnl':pnl}
    if previous_quote is None:
        previous_quote = _request(adapter, 'get_stock_price', {'stock_code':symbol})
        sleep(2)
    quote = _request(adapter, 'get_stock_price', {'stock_code':symbol})
    return {'accounts':accounts, 'history':history, 'balance':balance, 'pnl':pnl,
            'previous_quote':previous_quote, 'quote':quote}
