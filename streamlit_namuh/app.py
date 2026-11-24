"""Local NAMUH dashboard; all orders pass through the existing paper guard."""
from __future__ import annotations

import sys
import time
import math
import json
import hashlib
import re
import sqlite3
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import altair as alt
import pandas as pd
import streamlit as st

from report_figures import for_streamlit  # report callouts/highlights in st.markdown
from automation_status import processes, learning, recent_events, stop_shared_paper, stop_live
import dialogs
import prefs as PREFS
import theme as THEME
from guides import help_row, guide_dialog, LIVE_LIMITS, live_dot, values as guide_values
import status_cards as sc
import live_labels as LL
from settings_view import FOLLOW_HELP, LIVE_STOP_GUIDE, SIZING_HELP, TRANCHE_HELP, krw_words
from nh_live_buy_control import buy_allowed
from nh_paper_isolated import paper_pages
from etf_universe import SYMBOLS as ETF_CODES, NAMES as ETF_NAMES
from paper_ai_modes import read_modes, read_pending_modes, write_modes
from engine_compare import apply_fill, read_events, signed
from strategy_settings import read_settings, write_settings, DEFAULTS as STRATEGY_DEFAULTS, LIMITS as STRATEGY_LIMITS, LABELS as STRATEGY_LABELS
from nh_live_broker import BALANCE_FIELDS as BALANCE
from nh_live_broker import _pages as read_live_pages, orderable_cash, read_balance
from src.live.halt import halt_flag_set
from src.config.loader import load_agent_config
from src.config.paths import get_runtime_root
from src.tools.mcp import MCPServerAdapter
from sqlite_ro import ro_connect
from tunables import setting

KST = ZoneInfo('Asia/Seoul')
SYMBOLS = {ETF_NAMES[key]: code for key, code in ETF_CODES.items()}
ETF_CHARTS = {ETF_NAMES[key]: (key, ETF_CODES[key]) for key in
              ('INV', 'K200', 'LEV', 'KQ150', 'KQ150LEV', 'KQ150INV', 'SEMILEV')}


def configured_account(config, mode):
    args = list(config.args)
    if args.count('--mode') != 1 or args[args.index('--mode') + 1] != mode or args.count('--account') != 1:
        raise ValueError('계좌 설정을 확인할 수 없습니다.')
    return args[args.index('--account') + 1]


@st.cache_resource(show_spinner=False)
def _read_cache():
    return {}


@st.cache_resource(show_spinner=False)
def _nh_starts():
    from collections import deque
    return Lock(), deque(maxlen=NH_CALLS_PER_S)


@st.cache_resource(show_spinner=False)
def _dashboard_reads():
    return ThreadPoolExecutor(max_workers=2, thread_name_prefix='dashboard-read'), Lock(), {}


def start_dashboard_read(key, fetch):
    """Share an in-flight display read across sessions. Workers never access Streamlit session state."""
    pool, lock, running = _dashboard_reads()
    with lock:
        future = running.get(key)
        if future is not None and not future.done():
            return future
        future = running[key] = pool.submit(fetch)

    def forget(done):
        with lock:
            if running.get(key) is done:
                running.pop(key)

    future.add_done_callback(forget)
    return future


def rerun_when_read_completes(start):
    armed = False
    future = None

    @st.fragment(run_every='300ms')
    def check():
        nonlocal armed, future
        if not armed:
            armed = True
            return
        if future is None:
            future = start()
        if future.done() and dialogs.OPEN not in st.session_state:  # closing a pop-up redraws the app
            st.rerun()
    check()


STATE_POPUPS = ('side_d_services', 'side_d_alerts', 'ov_d_alerts', 'ops_d_services')  # buttons of pop-ups that show the states


def display_process_states():
    from automation_status import LABELS, PROCESS_CACHE_S, peek_processes
    if PROCESS_CACHE_S <= 0:
        return processes()
    states = peek_processes()
    if states is not None:
        return states
    if any(st.session_state.get(key) for key in STATE_POPUPS):
        return processes()  # an open pop-up is not drawn again when the read completes
    rerun_when_read_completes(lambda: start_dashboard_read('process-states', processes))
    return dict.fromkeys(LABELS, '확인 중')


QUOTE_CACHE_S = setting('quote_cache_s')  # display-only NH reads (운영 조정값)
CHART_CACHE_S = setting('chart_cache_s')
ACCOUNT_LIST_CACHE_S = setting('account_list_cache_s')
NH_CALLS_PER_S = 2  # NH allows 5 calls/s per app key, shared with the runners: display reads leave them 3 (11-18 429s)


def cached_tool(adapter, tool, args, ttl=QUOTE_CACHE_S):
    """adapter.call_tool for display-only reads through a short cache shared by all sessions: every NH call starts its
    own MCP server process (~0.3 s), and each rerun used to repeat the same quotes. Never for orders or their checks."""
    key = (getattr(adapter, 'server_name', None) or id(adapter), tool, json.dumps(args, sort_keys=True, default=str))
    hit = _read_cache().get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    lock, starts = _nh_starts()  # shared by every session and quote thread of this dashboard process
    with lock:  # no more than NH_CALLS_PER_S starts in any one second
        if len(starts) == starts.maxlen:
            time.sleep(max(0.0, starts[0] + 1.0 - time.monotonic()))
        starts.append(time.monotonic())
    result = adapter.call_tool(tool, args)
    if result.get('status') == 'ok':
        cache = _read_cache()
        if len(cache) > 500:  # quotes of a few dozen symbols; drop everything rather than track ages
            cache.clear()
        cache[key] = (time.monotonic(), result)
    return result


def call(adapter, tool, args, cache_s=0):
    result = cached_tool(adapter, tool, args, cache_s) if cache_s else adapter.call_tool(tool, args)
    if result.get('status') != 'ok' or not isinstance(result.get('data'), dict):
        raise ValueError('증권사 요청 실패 또는 차단: ' + str(result.get('error_type', '응답 오류')))
    return result['data']


def display_quotes(read, codes, ttl):
    """{code: Output_0 dict, or None when it failed} of display-only quotes, four calls in flight: a call costs ~0.27 s,
    mostly its own MCP process start, and the sidebar's ten serial quotes took ~2.7 s of every rerun. cached_tool keeps
    the starts to NH_CALLS_PER_S in any second."""
    from concurrent.futures import ThreadPoolExecutor
    from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
    ctx = get_script_run_ctx()

    def one(code):
        for wait in (0.5, 1.0, None):  # a burst of calls at page load is throttled; back off twice
            try:
                return call(read, 'get_stock_price', {'stock_code': code}, cache_s=ttl).get('Output_0') or {}
            except Exception:
                if wait is None:
                    return None
                time.sleep(wait)

    with ThreadPoolExecutor(max_workers=4, initializer=add_script_run_ctx, initargs=(None, ctx)) as pool:
        return dict(zip(codes, pool.map(one, codes)))


def display_prices(read, codes, ttl):
    """{code: (row with iem_cd, stck_prpr, stck_prdy_clpr, or None; 'kiwoom' | 'nh')} for display only (2026-11-20 NH call
    plan P1, docs/nh-call-reduction-plan.md): one Kiwoom ka10095 call for up to 100 codes, kept `ttl` seconds for every
    session of this dashboard; NH (display_quotes) only for the codes Kiwoom did not answer (no key, a failure, a code it
    does not know). Never for orders or their checks."""
    codes = list(dict.fromkeys(codes))
    key = ('kiwoom-prices', tuple(codes))
    hit = _read_cache().get(key)
    got = hit[1] if hit and time.monotonic() - hit[0] < max(ttl, 1) else None
    if got is None:
        got = {}
        try:
            import kiwoom_data
            for s in kiwoom_data.call('ka10095', {'stk_cd': '|'.join(codes[:100])}, 'atn_stk_infr', max_pages=1):
                code = str(s.get('stk_cd') or '').split('_')[0]  # '090430_AL' (KRX+NXT) -> '090430'
                price, base, opened, high, volume = (abs(kiwoom_data.num(s.get(k)) or 0)
                                                     for k in ('cur_prc', 'base_pric', 'open_pric', 'high_pric', 'trde_qty'))
                if code in codes and price > 0 and base > 0:
                    got[code] = {'iem_cd': code, 'stck_prpr': price, 'stck_prdy_clpr': base, 'stck_oprc': opened,
                                 'stck_hgpr': high, 'acml_vol': volume}
        except Exception:  # noqa: BLE001 - no key or Kiwoom down: NH answers below
            got = {}
        _read_cache()[key] = (time.monotonic(), got)
    out = {code: (row, 'kiwoom') for code, row in got.items()}
    missing = [code for code in codes if code not in out]
    if missing:
        out.update({code: (quote, 'nh') for code, quote in display_quotes(read, missing, ttl).items()})
    return out


LISTING_FIELDS_S = 6 * 3600  # NH listing date and listed shares change at most once a day


def listing_quotes(read, codes):
    """{code: quote} for the new-listing panel (2026-11-20 NH call audit): prices, open, high and volume from one Kiwoom
    call (display_prices, shared for 60 s); NH only for the listing date and listed shares the runner checks, kept
    LISTING_FIELDS_S for every session. Each tab used to read every listing's NH quote once a minute."""
    priced = display_prices(read, codes, 60)
    static = display_quotes(read, codes, LISTING_FIELDS_S)
    return {code: {**{k: v for k, v in (static.get(code) or {}).items() if k in ('stck_lstn_date', 'lstn_stcn_unit0', 'iem_nm')},
                   **((priced.get(code) or (None, None))[0] or {})} for code in codes}


def verify_account(read, account, account_types, cache_s=0):
    """cache_s only for screens that read balances (the account list is shared with resolve_accounts); never before an order."""
    data = call(read, 'list_accounts', {}, cache_s=cache_s)
    rows = data.get('Output_0')
    if not isinstance(rows, list) or not any(isinstance(row, dict) and row.get('acct_no') == account
                                               and row.get('acct_type') in account_types for row in rows):
        raise ValueError('계좌 유형을 확인할 수 없습니다.')


def live_balance(read, account):
    verify_account(read, account, {'01', '02'}, ACCOUNT_LIST_CACHE_S)
    return read_balance(read, account)


def live_order_rows(read, account, now):
    verify_account(read, account, {'01', '02'}, ACCOUNT_LIST_CACHE_S)
    data = read_live_pages(read, 'krstockInquiryDailyOrderExecution', {'act_no': account,
        'orr_dt': now.astimezone(KST).strftime('%Y%m%d'), 'orr_mkt_cd': '00', 'ost_cns_dit': '0'}, 'Output_0')
    rows = data.get('Output_0')
    if data.get('_pagination_complete') is not True or not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('실계좌 주문 전체 조회 실패')
    fields = [('iem_cd', '종목코드'), ('iem_nm', '종목'), ('sby_dit_cd_nm', '구분'),
              ('orr_qty', '주문수량'), ('tot_cns_qty', '체결수량'), ('ny_cns_qty', '미체결수량'),
              ('can_qty', '취소수량'), ('cns_avg_uit_pr', '평균체결가'),
              ('orr_rjt_rsn_cd_nm', '거부 사유'), ('orr_tmd', '주문 시각')]
    return display_rows(rows, fields)


def display_rows(rows, fields):
    return [{label: row.get(key) for key, label in fields} for row in rows if isinstance(row, dict)]


SNAPSHOT_MAX_AGE = 120  # seconds: the chart hides the cost-basis lines for an older broker snapshot


def snapshot_due(kept, now):
    """True when a kept broker snapshot must be fetched again: none yet, or older than the refresh interval or the
    chart's freshness limit, whichever is shorter (with auto refresh off it was kept until the chart called it stale)."""
    return kept is None or (now - kept['time']).total_seconds() > min(refresh_s(), SNAPSHOT_MAX_AGE)


SHARED_SNAPSHOT_S = 20  # seconds a broker snapshot one session fetched serves the others (2026-11-20 NH call audit)


def shared_snapshot(kind, account, fetch, fresh=False):
    """A display-only broker snapshot shared by every session of this dashboard: each open browser tab kept its own and
    re-read the same balance and orders on its own timer (two tabs doubled the dashboard's NH calls). `fresh` (a refresh
    button) always reads again. Never before an order."""
    key = ('broker-snapshot', kind, account)
    hit = _read_cache().get(key)
    if not fresh and hit and time.monotonic() - hit[0] < SHARED_SNAPSHOT_S:
        return hit[1]
    got = fetch()
    _read_cache()[key] = (time.monotonic(), got)
    return got


def shared_etf_broker_snapshot(read, paper, account, now):
    verify_account(read, account, {'03'}, ACCOUNT_LIST_CACHE_S)
    balance = paper_pages(paper, 'krstockInquiryBalance', {'act_no': account, **BALANCE}, 'Output_1')
    history = paper_pages(paper, 'krstockInquiryDailyOrderExecution', {'act_no': account,
        'orr_dt': now.astimezone(KST).strftime('%Y%m%d'), 'orr_mkt_cd': '00', 'ost_cns_dit': '0'}, 'Output_0')
    if (not isinstance(balance.get('Output_0'), dict) or not isinstance(balance.get('Output_1'), list)
            or not isinstance(history.get('Output_0'), list)):
        raise ValueError('증권사 전체 조회 결과를 확인할 수 없습니다.')
    return broker_snapshot(balance, history, now)


def live_broker_snapshot(read, account, now):
    """The same snapshot shape as the paper account, from a linked live stock account (read-only queries)."""
    verify_account(read, account, {'01', '02'}, ACCOUNT_LIST_CACHE_S)
    balance = read_live_pages(read, 'krstockInquiryBalance', {'act_no': account, **BALANCE}, 'Output_1')
    history = read_live_pages(read, 'krstockInquiryDailyOrderExecution', {'act_no': account,
        'orr_dt': now.astimezone(KST).strftime('%Y%m%d'), 'orr_mkt_cd': '00', 'ost_cns_dit': '0'}, 'Output_0')
    if (balance.get('_pagination_complete') is not True or history.get('_pagination_complete') is not True
            or not isinstance(balance.get('Output_0'), dict) or not isinstance(balance.get('Output_1'), list)
            or not isinstance(history.get('Output_0'), list)):
        raise ValueError('실계좌 전체 조회 결과를 확인할 수 없습니다.')
    return broker_snapshot(balance, history, now)


def order_fills(orders, day):
    """Filled broker orders as chart/overview fills (order time, average fill price); unparsable rows are skipped."""
    fills = []
    for row in orders:
        qty, price, raw = row.get('체결수량'), row.get('평균체결가'), str(row.get('주문 시각') or '').replace(':', '')
        if type(qty) not in (int, float) or qty <= 0 or not raw[:6].isdigit():
            continue
        at = datetime.combine(day, datetime.strptime(raw[:6], '%H%M%S').time(), KST)
        fills.append({'시각': at.isoformat(), '종목': row.get('종목코드'), '구분': 'BUY' if '매수' in str(row.get('구분')) else 'SELL',
                      '수량': int(qty), '체결가': price})
    return fills


def broker_snapshot(balance, history, now):
    holdings = []
    for row in balance['Output_1']:
        if not isinstance(row, dict):
            raise ValueError('보유 종목 형식이 올바르지 않습니다.')
        quantity = row.get('rsdl_qty')
        if type(quantity) not in (int, float) or not math.isfinite(quantity) or quantity < 0:
            raise ValueError('보유 수량을 확인할 수 없습니다.')
        if quantity:
            holdings.append(row)
    fields = [('iem_cd', '종목코드'), ('iem_nm', '종목'), ('rsdl_qty', '보유수량'),
              ('phs_pr', '평균매입가'), ('now_pr', '현재가'), ('eal_amt', '평가금액'),
              ('eal_pls_amt', '평가손익'), ('pft_rt', '수익률(%)')]
    order_fields = [('iem_cd', '종목코드'), ('iem_nm', '종목'), ('sby_dit_cd_nm', '구분'),
                    ('orr_qty', '주문수량'), ('tot_cns_qty', '체결수량'), ('ny_cns_qty', '미체결수량'),
                    ('can_qty', '취소수량'), ('cns_avg_uit_pr', '평균체결가'),
                    ('orr_rjt_rsn_cd_nm', '거부 사유'), ('orr_tmd', '주문 시각'), ('itg_orr_no', '주문번호')]
    if any(not isinstance(row, dict) for row in history['Output_0']):
        raise ValueError('주문 내역 형식이 올바르지 않습니다.')
    summary = {**balance['Output_0'], 'orr_pbl_amt1': orderable_cash(balance['Output_0'])}  # a sale that settles in two days counted
    values = [summary.get(key) for key in ('tot_eal_amt', 'tot_eal_pls', 'pft_rt')]
    def valid_number(value):
        return type(value) in (int, float) and math.isfinite(value)
    valid_prices = all(valid_number(row.get('now_pr')) and row['now_pr'] > 0
                       and valid_number(row.get('eal_amt')) and row['eal_amt'] >= 0 for row in holdings)
    aggregate = (dict(zip(('평가금액', '평가손익', '수익률(%)'), values))
                 if valid_prices and all(map(valid_number, values)) and values[0] >= 0 else None)
    return {'time': now.astimezone(KST), 'holdings': display_rows(holdings, fields),
            'orders': display_rows(history['Output_0'], order_fields), 'aggregate': aggregate,
            'account': {label: summary.get(key) if valid_number(summary.get(key)) and summary[key] >= 0 else None
                        for key, label in (('orr_pbl_amt1', '주문가능금액'), ('tot_byn_amt', '총매입금액'),
                                           ('tot_eal_amt', '총평가금액'), ('tot_aet_amt', '총자산'))}}


def etf_portfolio_rows(holdings):
    by_symbol = {}
    for row in holdings:
        symbol = row.get('종목코드')
        if symbol in ETF_CODES.values():
            if symbol in by_symbol:
                raise ValueError('동일 종목의 보유 내역이 여러 건입니다.')
            by_symbol[symbol] = row
    fields = ('종목코드', '종목', '보유수량', '평균매입가', '현재가', '평가금액', '평가손익', '수익률(%)')
    result = []
    for key, symbol in ETF_CODES.items():
        if symbol in by_symbol:
            row = dict(by_symbol[symbol])
            average, current = row.get('평균매입가'), row.get('현재가')
            if (type(average) in (int, float) and type(current) in (int, float)
                    and math.isfinite(average) and math.isfinite(current) and average > 0 and current > 0):
                change = (current / average - 1) * 100
                row['가격 구간'] = f"{'수익' if change > 0 else '손실' if change < 0 else '보합'} {change:+.2f}%"
            else:
                row['가격 구간'] = '확인 불가'
            result.append(row)
        else:
            row = dict.fromkeys(fields)
            row.update({'종목코드': symbol, '종목': ETF_NAMES[key], '보유수량': 0, '가격 구간': '미보유'})
            result.append(row)
    return result


def holding_chart_basis(snapshot, fills, symbol, now):
    """Use a recent broker position only when a local confirmed fill anchors its start."""
    if not isinstance(snapshot, dict) or not isinstance(fills, list):
        return None
    observed = snapshot.get('time')
    if not isinstance(observed, datetime) or observed.tzinfo is None or not 0 <= (now - observed).total_seconds() <= 120:
        return None
    rows = [row for row in snapshot.get('holdings', []) if row.get('종목코드') == symbol]
    if len(rows) != 1:
        return None
    quantity, average = rows[0].get('보유수량'), rows[0].get('평균매입가')
    if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in (quantity, average)):
        return None
    candidates = []
    for fill in fills:
        if not isinstance(fill, dict):
            return None
        if fill.get('종목') != symbol:
            continue
        at = event_time(fill.get('시각'))
        if at is None or at > observed:
            return None
        candidates.append((at, fill))
    # Walk back from the newest fill until the net position reaches zero; that buy opened the current holding.
    remaining, cost, bought = quantity, 0.0, 0
    for at, fill in sorted(candidates, key=lambda row: row[0], reverse=True):
        side, qty, price = str(fill.get('구분', '')).upper(), fill.get('수량'), fill.get('체결가')
        if (side not in ('BUY', 'SELL') or type(qty) is not int or qty <= 0
                or type(price) not in (int, float) or not math.isfinite(price) or price <= 0):
            return None
        if side == 'BUY':
            remaining, cost, bought = remaining - qty, cost + qty * price, bought + qty
        else:
            remaining += qty
        if remaining < 0:
            return None
        if remaining == 0:
            # Broker averages are moving averages, so they equal the quantity-weighted buys since opening.
            if abs(cost / bought - average) >= 0.5:
                return None
            return {'price': float(average), 'start': int(at.timestamp())}
    return None


def realized_by_symbol(db, day):
    """Realized P&L per symbol from verified local fills, using the broker's moving-average cost."""
    return books_from_rows(db.execute('SELECT time,symbol,side,quantity,price FROM fills ORDER BY rowid'), day)


def books_from_rows(rows, day):
    """realized_by_symbol over (time, symbol, side, qty, price) rows; a sale of shares bought before the rows began has
    no known cost, so only the part covered by recorded buys counts as realized."""
    books = {}
    for stamp, symbol, side, qty, price in rows:
        book = books.setdefault(symbol, {'qty': 0, 'avg': 0.0, 'realized': 0.0, 'today': 0.0, 'buys': 0, 'sells': 0, 'sales': []})
        book['qty'], book['avg'], cost, pnl, _ = apply_fill(book['qty'], book['avg'], side, qty, price)
        book['buys' if side == 'BUY' else 'sells'] += 1
        if cost is not None:  # a covered sale
            book['realized'] += pnl
            book['today'] += pnl if str(stamp).startswith(day) else 0
            book['sales'].append((str(stamp), pnl, cost))  # (time, P&L, cost of the shares sold)
    return books


def movers_snapshot():
    """Latest market_movers.py scan (rankings and Jev candidates); None when unavailable."""
    try:
        return json.loads((get_runtime_root() / 'nhplug' / 'shared-etf-paper' / 'movers.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def watchlist_snapshot():
    """Latest Jev watchlist selection (watchlist.py); None when unavailable."""
    try:
        return json.loads((get_runtime_root() / 'nhplug' / 'shared-etf-paper' / 'watchlist.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def stock_names():
    """Display names by code: the ETF universe, the latest scan (movers.json) and the watchlist."""
    rows = {**((movers_snapshot() or {}).get('rows') or {}), **((watchlist_snapshot() or {}).get('stocks') or {})}
    return {**{code: ETF_NAMES[key] for key, code in ETF_CODES.items()},
            **{code: row.get('name', code) for code, row in rows.items() if isinstance(row, dict)}}


HOLDING_STATES = {'보유': lambda row: (row.get('보유수량') or 0) > 0, '전체': lambda row: True,
                  '미보유': lambda row: not (row.get('보유수량') or 0) > 0}
PERIOD_UNITS = {'일': 10, '월': 7, '연': 4}  # prefix length of an ISO time: 2026-11-13 / 2026-11 / 2026


def pct(pnl, cost):
    """Return % of a P&L on its cost; None without a cost."""
    return round(pnl / cost * 100, 2) if cost else None


def realized_in(book, prefix):
    """(P&L, cost of the shares sold) of a book's sells whose time starts with prefix (None = all)."""
    sales = [(pnl, cost) for stamp, pnl, cost in (book or {}).get('sales', []) if prefix is None or stamp.startswith(prefix)]
    return sum(p for p, _ in sales), sum(c for _, c in sales)


def realized_card(books, today):
    """The desk's 실현손익 card (user 2026-11-18): today's realized P&L, with this month and since the records start."""
    if books is None:
        return {'label': '실현손익', 'value': '확인 불가', 'tone': 'alert', 'icon': 'error',
                'help': '로컬 체결 원장을 읽지 못해 실현손익을 계산하지 못했습니다.'}
    total = lambda prefix: round(sum(realized_in(book, prefix)[0] for book in books.values()))
    day, month, all_ = total(today.isoformat()), total(today.isoformat()[:7]), total(None)
    return {'label': '실현손익', 'value': f'{day:+,}원', 'tone': sc.signed(day),
            'icon': 'trending_up' if day > 0 else 'trending_down' if day < 0 else 'trending_flat',
            'sub': f'이번 달 {month:+,}원 · 누적 {all_:+,}원',
            'help': '판 주식의 손익(체결가 기준, 수수료·세금 미반영)입니다. 큰 값은 오늘, 아래는 이번 달과 이 계좌 기록 시작부터의 누적입니다. '
                    '전체 계좌 합은 종합 현황의 "오늘 실현손익 · 전체 계좌"에서 봅니다.'}


def realized_by_period(books, unit):
    """One row per day/month/year (newest first) with sells, realized P&L and its return on the cost sold."""
    size, periods = PERIOD_UNITS[unit], {}
    for book in books.values():
        for stamp, pnl, cost in book.get('sales', []):
            row = periods.setdefault(stamp[:size], [0, 0.0, 0.0])
            row[0], row[1], row[2] = row[0] + 1, row[1] + pnl, row[2] + cost
    return [{'기간': key, '매도 횟수': n, '실현손익': round(pnl), '수익률': pct(pnl, cost)}
            for key, (n, pnl, cost) in sorted(periods.items(), reverse=True)]


def symbol_pnl_rows(holdings, books, names=None, period=None):
    """One row per ETF or stock that is held or was traded: broker valuation plus locally realized P&L.

    period=(label, time prefix) picks the realized column, e.g. ('2026-11', '2026-11'); None = today."""
    held = {row.get('종목코드'): row for row in holdings}
    etf_by_code = {code: key for key, code in ETF_CODES.items()}
    stocks = sorted((set(held) | set(books)) - set(etf_by_code))
    rows = []
    for code in [*ETF_CODES.values(), *stocks]:
        position, book = held.get(code), books.get(code)
        if not position and not book:
            continue
        label = (ETF_NAMES[etf_by_code[code]] if code in etf_by_code
                 else (position or {}).get('종목') or (names or {}).get(code) or code)
        unrealized = position.get('평가손익') if position else 0
        unrealized = unrealized if type(unrealized) in (int, float) and math.isfinite(unrealized) else None
        realized = round(book['realized']) if book else 0
        label_p, prefix = period or ('오늘', None)
        if period:
            period_pnl, period_cost = realized_in(book, prefix)
        else:
            period_pnl, period_cost = (book['today'] if book else 0), None
        average, quantity = (position or {}).get('평균매입가'), (position or {}).get('보유수량')
        held_cost = average * quantity if type(average) in (int, float) and type(quantity) in (int, float) else 0
        rows.append({'종목': label, '보유수량': position.get('보유수량') if position else 0,
                     '평균매입가': position.get('평균매입가') if position else None,
                     '현재가': position.get('현재가') if position else None,
                     '평가손익': unrealized, '평가수익률': pct(unrealized, held_cost) if unrealized is not None else None,
                     f'실현손익({label_p})': round(period_pnl),
                     f'실현수익률({label_p})': pct(period_pnl, period_cost) if period else None,
                     '실현손익(누적)': realized, '누적수익률': pct(*realized_in(book, None)) if book else None,
                     '합계': None if unrealized is None else unrealized + realized,
                     '매수·매도 횟수': f"{book['buys']} · {book['sells']}" if book else '0 · 0'})
    return rows


def jev_evidence(events):
    """Latest Jev entry call with the exact bars and headlines recorded for the same cycle, plus the latest holding view."""
    inputs, latest, holding = {}, None, None
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get('event') in ('market_context', 'decision') and event.get('cycle_id') and isinstance(event.get('market_bars'), dict):
            inputs[event['cycle_id']] = event
        candidate = (event.get('jev') or {}).get('candidate') if event.get('event') == 'jev_shadow' else None
        if isinstance(candidate, dict) and candidate.get('choice') and event.get('cycle_id') in inputs:
            latest = (event, candidate)
        if event.get('event') == 'jev_holding_decision':
            holding = event
    if latest is None:
        return None
    event, candidate = latest
    source = inputs[event['cycle_id']]
    return {'time': event_time(event.get('time')), 'choice': candidate.get('choice'), 'confidence': candidate.get('confidence'),
            'probabilities': candidate.get('probabilities') or {}, 'bars': source['market_bars'],
            'news': source.get('news') or [], 'investor': source.get('investor_flows'), 'holding': holding,
            'track_record': source.get('track_record')}


def jev_chart_frame(bars):
    """Percent change from the first recorded close, per ETF, oldest first — the price picture Jev was shown."""
    series = {}
    for key, rows in bars.items():
        try:
            points = [(str(r['bsop_time']), int(r['stck_prpr'])) for r in reversed(rows)]
        except (KeyError, TypeError, ValueError):
            continue
        if points and points[0][1] > 0:
            series[ETF_NAMES.get(key, key)] = {f'{t[:2]}:{t[2:4]}': (v / points[0][1] - 1) * 100 for t, v in points}
    return pd.DataFrame(series).sort_index()


def jev_evidence_panel(events, read, refresh):
    evidence = jev_evidence(events)
    if evidence is None:
        st.caption('Jev 판단 기록이 아직 없습니다.')
        return
    st.caption(f"최근 Jev 진입 판단 {evidence['time']:%H:%M:%S} · Jev 입력: 7개 ETF 최근 1분 종가 차트, 뉴스 헤드라인(최신순), "
               'ETF별 투자자 일별 순매수(11/11 13:5x부터). 아래 "참고 시장 데이터"는 같은 내용을 지금 다시 조회한 것입니다.')
    result, picture = st.columns(2)
    with result:
        score = evidence['confidence']
        label_of = {**ETF_NAMES, **stock_names()}
        name = label_of.get(evidence['choice'], '대기' if evidence['choice'] == 'WAIT' else evidence['choice']).removeprefix('KODEX ')
        result.metric('Jev 종목 후보', name, f"신뢰도 {score:.2f} · 진입 기준 {strategy_settings()[0]['entry_confidence']:.2f}" if type(score) in (int, float) else None,
                      delta_color='violet', delta_arrow='off', border=True)
        if evidence['probabilities']:
            probs = pd.Series({('대기' if k == 'WAIT' else label_of.get(k, k)): v for k, v in evidence['probabilities'].items()},
                              name='확률').sort_values(ascending=False)
            st.bar_chart(probs, horizontal=True, height=240)
            st.caption('Jev가 각 선택지에 준 확률입니다. 수익 확률이 아닙니다.')
        holding = evidence['holding']
        if holding:
            st.caption(f"최근 보유분 의견: {ETF_NAMES.get(next((k for k, v in ETF_CODES.items() if v == holding.get('symbol')), ''), holding.get('symbol'))} "
                       f"{holding.get('choice')} {holding.get('confidence', 0):.2f} · {holding.get('time', '')[11:19]} (매도 기준 0.70)")
    with picture:
        frame = jev_chart_frame(evidence['bars'])
        st.write('Jev가 본 차트 (최근 1분 종가, 첫 분 대비 %)')
        if frame.empty:
            st.caption('기록된 분봉이 없습니다.')
        else:
            st.line_chart(frame, height=300)
    st.write('Jev가 본 투자자 동향 (순매수 주식 수)')
    investor = evidence.get('investor')
    if investor:
        rows = []
        for key, entry in investor.items():
            for label, field in (('직전 확정일', 'last_close_day'), ('오늘 장중 잠정', 'today_provisional')):
                if field in entry:
                    row = entry[field]
                    rows.append({'종목': ETF_NAMES.get(key, key), '구분': f"{label} {row.get('date', '')}",
                                 '외국인': row.get('foreign'), '기관': row.get('institution'), '개인': row.get('individual')})
        frame = pd.DataFrame([{'종목': r['종목'], '투자자': who, '순매수': r[who]} for r in rows if r['구분'].startswith('직전')
                              for who in ('외국인', '기관', '개인') if type(r.get(who)) in (int, float)])
        if not frame.empty:
            st.altair_chart(alt.Chart(frame).mark_bar().encode(
                y=alt.Y('종목:N', title=None), x=alt.X('순매수:Q', title='직전 확정일 순매수(주)'),
                color=alt.Color('투자자:N', scale=alt.Scale(domain=['외국인', '기관', '개인'], range=['#f59e0b', '#22c55e', '#94a3b8'])),
                yOffset='투자자:N', tooltip=['종목', '투자자', alt.Tooltip('순매수:Q', format=',')]).properties(height=60 * frame['종목'].nunique()),
                width='stretch')
        with st.expander('투자자 동향 표 (오늘 잠정 포함)'):
            st.dataframe(rows, hide_index=True)
    else:
        st.caption('이 판단에는 투자자 동향이 들어가지 않았습니다(도입 전 기록이거나 조회 실패).')
    record = evidence.get('track_record') or {}
    if record:
        st.write('Jev가 본 자기 성과 요약 (학습 데이터)')
        trades = record.get('paper_trades') or {}
        direction = record.get('pick_direction_research') or {}
        names = {**ETF_NAMES, **stock_names()}
        st.caption(f"모의 매도 {trades.get('closed_sells', 0)}건 · 이익 {trades.get('wins', 0)} · 손실 {trades.get('losses', 0)} · "
                   f"평균 {trades.get('avg_return_pct') if trades.get('avg_return_pct') is not None else '—'}% (평균 매입가 대비, 비용 미반영)")
        if direction:
            st.dataframe([{'종목': names.get(k, k), '고른 뒤 상승': v['picks_rose_after'], '고른 뒤 하락': v['picks_fell_after']}
                          for k, v in direction.items()], hide_index=True)
        st.caption('학습 관찰기가 쌓은 "Jev가 고른 종목이 이후 올랐는지" 집계와 실제 모의 매도 결과입니다. 표본이 작아 참고용이며 매수·매도 조건에는 쓰지 않습니다.')
    st.write('Jev가 본 뉴스')
    today = (evidence['time'] or datetime.now(KST)).date()
    for item in evidence['news']:
        stamp = event_time(item.get('published'))
        when = '시각 미상' if stamp is None else stamp.astimezone(KST).strftime('%m/%d %H:%M')
        old = ' · :orange[오늘 기사 아님]' if stamp is not None and stamp.astimezone(KST).date() != today else ''
        st.markdown(f"- {news_link(item)}  \n  :gray[{item.get('publisher') or item.get('source', '')} · {when}{old}]")
    if not evidence['news']:
        st.caption('기록된 뉴스가 없습니다.')

    st.write('참고 시장 데이터 (Jev 입력 아님)')
    cache = st.session_state.get('market_reference')
    stale = not isinstance(cache, dict) or (datetime.now(KST) - cache['time']).total_seconds() > 300
    if st.button('시세·투자자 동향 조회', key='market_reference_refresh') or (refresh and stale):
        try:
            from vibe_market_brief import market
            cache = {'time': datetime.now(KST), 'data': market(read, datetime.now(KST))}
            st.session_state.market_reference = cache
        except Exception:
            st.warning('참고 시장 데이터를 불러오지 못했습니다.')
    if isinstance(cache, dict):
        rows = []
        for key, row in cache['data'].items():
            quote = row.get('quote') or {}
            flows = [f for f in row.get('investor_daily_net_qty', []) if any(f.get(k) for k in ('frgn_ntby_qty', 'gigwan', 'person'))]
            last = flows[0] if flows else {}
            day = last.get('bsop_date1', '—')
            rows.append({'종목': row['name'], '현재가': quote.get('stck_prpr'), '등락률(%)': quote.get('change_pct'),
                         '투자자 기준일': f'{day} (오늘·장중 잠정)' if day == cache['time'].strftime('%Y%m%d') else day, '외국인 순매수(주)': last.get('frgn_ntby_qty'),
                         '기관 순매수(주)': last.get('gigwan'), '개인 순매수(주)': last.get('person')})
        st.dataframe(rows, hide_index=True)
        st.caption(f"조회 {cache['time']:%H:%M:%S} · 투자자 동향은 일별 순매수 수량이며, 오늘 행은 장중 잠정치(주로 외국인 어림값)로 장 마감 후 확정됩니다. "
                   'ETF 순매수에는 유동성공급자(기관) 거래가 섞여 방향 신호로 약합니다.')
    else:
        st.caption('버튼을 누르면 7개 ETF 시세와 투자자별 순매수를 조회합니다(자동 조회 중에는 5분마다).')


def news_link(item):
    """A headline as a markdown link. The feed's article URL is not recorded (and Jev's input stays unchanged), so the
    link searches Google News for the exact headline, which opens the article at the top."""
    from urllib.parse import quote
    title = str(item.get('title') or '제목 없음')
    text = title.replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]').replace('$', '\\$')  # $ would start LaTeX in Streamlit markdown
    return f"[{text}](https://news.google.com/search?q={quote(title, safe='')}&hl=ko&gl=KR&ceid=KR:ko)"


def new_listing_panel(now):
    """Recent new listings (KRX KIND, confirmed by NH's listing date) and where each stands against the entry rule."""
    import new_listings
    import nh_paper_isolated as runner
    rules = strategy_settings()[0]
    days, near = int(rules['ipo_days']), rules['ipo_near_high_pct']
    st.markdown('##### 신규주 (최근 상장 · 신규주 진입 후보)')
    if not days:
        st.caption('신규주 진입이 꺼져 있습니다. 전략·AI 설정의 "신규주 진입 · 빠른 선택"에서 켤 수 있습니다.')
    root = get_runtime_root() / 'nhplug' / 'shared-etf-paper'
    try:  # the runner's daily list as it saved it: this 30-second fragment never asks KIND or Toss itself
        cache = json.loads((root / new_listings.FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        cache = {}
    listings = sorted(cache.get('rows') or [], key=lambda r: r['listed'], reverse=True)[:6]
    if listings and not cache.get('ok'):  # ok False with rows: KIND failed and Toss stood in (2026-11-17)
        st.caption(f"KRX KIND 조회 실패({cache.get('error', '?')})로 토스증권 종목 정보의 상장일을 씁니다. 15분 뒤 KIND를 다시 시도합니다.")
    if listings and cache.get('day') != f'{now:%Y%m%d}':
        st.caption(f"실행기가 오늘 목록을 아직 받지 않아 {LL.day(cache.get('day'))} 목록입니다.")
    if not listings:
        st.caption('최근 14일 신규상장 목록이 아직 없거나 신규주가 없습니다(실행기가 하루 한 번 KRX KIND, 실패 시 토스증권에서 받습니다).')
        return
    cache = st.session_state.get('ipo_quotes')
    if not cache or (now - cache[0]).total_seconds() > 60:
        read, codes = st.session_state.get('_read_adapter'), [item['code'] for item in listings]
        cache = (now, listing_quotes(read, codes))
        st.session_state['ipo_quotes'] = cache
    rows = []
    for item in listings:
        q = cache[1].get(item['code']) or {}
        listed = datetime.strptime(item['listed'], '%Y%m%d').date()
        sessions = runner.sessions_held(listed, now.date())
        price, base, opened, high = (q.get(k) for k in ('stck_prpr', 'stck_prdy_clpr', 'stck_oprc', 'stck_hgpr'))
        ok = all(type(v) in (int, float) and v > 0 for v in (price, base, opened, high))
        checks = [('기간', sessions < days), ('NH 상장일 확인', q.get('stck_lstn_date') == item['listed']),
                  ('기준가 위', ok and price >= base), ('시가 위', ok and price >= opened), (f'고가 {near:g}% 안', ok and price >= high * (1 - near / 100))]
        failed = next((name for name, good in checks if not good), None)
        volume, shares = q.get('acml_vol'), q.get('lstn_stcn_unit0')
        rows.append({'종목': item['name'], '코드': item['code'], '상장일': f"{item['listed'][4:6]}/{item['listed'][6:]}",
                     '상장 후': '당일' if sessions == 0 else f'{sessions}거래일', '기준가': f'{base:,}' if ok else '—',
                     '현재가': f'{price:,}' if ok else '—', '기준가 대비': f'{(price / base - 1) * 100:+.1f}%' if ok else '—',
                     '회전율': f'{volume / shares * 100:.0f}%' if type(volume) in (int, float) and type(shares) in (int, float) and shares else '—',
                     '판단': '✓ 1차 조건 통과 (분봉 상승·거래량 확인 대기)' if not failed else f'✗ {failed}'})
    st.dataframe(rows, hide_index=True, width='stretch', column_config={
        '기준가': st.column_config.TextColumn('기준가', help='상장 당일은 공모가(기준가), 그 뒤는 전일 종가입니다.'),
        '회전율': st.column_config.TextColumn('회전율', help='오늘 거래량 ÷ 상장 주식 수. 높을수록 손바뀜이 활발합니다(참고 지표, 조건 아님).'),
        '판단': st.column_config.TextColumn('판단', help=f'상장 후 {days}거래일 안 · NH 상장일 확인 · 기준가 위 · 시가 위 · 고가 {near:g}% 안을 차례로 봅니다. '
                                                         '통과해도 실행기가 1분봉 상승과 거래량 증가를 다시 확인한 뒤에만 삽니다.', width='large')})
    with st.expander('신규주 진입은 어떻게 하나요? (도식)', icon=':material/schema:'):
        from guides import topic_view, ipo_dot, values as guide_values
        v = guide_values()
        topic_view('ipo', v)
        import guide_ui as GU
        from guides import ipo_flow
        GU.flow('jev-ipo', ipo_flow(v), ipo_dot(v))
    st.caption(f"조회 {cache[0]:%H:%M:%S} · 목록: KRX KIND 신규상장기업현황(스팩 제외) · 시세: NH · 1분마다 다시 조회")


def market_watch_panel(watch, movers, now):
    """What Jev weighs when picking today's stocks: market backdrop, the watchlist, every candidate and the rankings."""
    at = event_time((watch or {}).get('time'))
    if at is None or at.astimezone(KST).date() != now.astimezone(KST).date():
        watch, at = None, None
    market = (watch or {}).get('market') or {}
    k200, kq150, picked = st.columns(3)
    for column, key, label in ((k200, 'K200', '코스피200 (KODEX 200)'), (kq150, 'KQ150', '코스닥150 (KODEX 코스닥150)')):
        row = market.get(key) or {}
        change = row.get('change_pct')
        column.metric(label, f"{row['price']:,}원" if row.get('price') else '—',
                      f'{change:+.2f}%' if type(change) in (int, float) else None,
                      delta_color='red' if (change or 0) > 0 else 'blue' if (change or 0) < 0 else 'off', border=True)
    picked.metric('관심종목 선정', f'{at:%H:%M}' if at else '없음',
                  f"Jev 1순위 {stock_names().get((watch or {}).get('jev_choice'), (watch or {}).get('jev_choice') or '—')}"
                  if watch else '오늘 선정 대기', delta_color='violet', delta_arrow='off', border=True)
    if not watch:
        st.caption('완료 분봉이 확보되면 Jev가 순위 후보를 평가합니다. 선정 결과는 30분 간격으로 갱신합니다.')
    else:
        age = (now - at).total_seconds() / 60 if at else None
        if age is None or age > 45:
            st.warning('관심종목 선정이 45분보다 오래돼 실행기는 개별 종목을 매매 후보에서 뺍니다(7개 ETF만 판단).')
        stocks, probs = watch.get('stocks') or {}, watch.get('probabilities') or {}
        st.markdown('##### 오늘의 관심종목 (Jev 선정, 7개 ETF와 함께 매매 후보)')
        cards = st.columns(max(1, len(watch.get('selected') or [])))
        for column, code in zip(cards, watch.get('selected') or []):
            s = stocks.get(code) or {}
            facts, flows = s.get('facts') or {}, s.get('investor_daily_net_qty') or {}
            with column.container(border=True):
                change = s.get('change_pct')
                st.metric(f"{s.get('name', code)} ({code})", f"{s.get('price', 0):,}원",
                          f'{change:+.2f}%' if type(change) in (int, float) else None,
                          delta_color='red' if (change or 0) > 0 else 'blue' if (change or 0) < 0 else 'off')
                st.caption(' · '.join(s.get('reasons') or []) + f" · Jev 선정 확률 {probs.get(code, 0):.2f}",
                           help='Jev가 후보끼리 나눠 매긴 상대 점수(합계 1)입니다. 순위 상위 3위 안 + 0.15 이상이면 관심종목 강도 진입 후보가 됩니다.')
                low, high = facts.get('52주 최저'), facts.get('52주 최고')
                lines = [f"**거래대금** {s.get('value_mil', 0) / 100:,.0f}억 · **거래량** {s.get('volume', 0):,}주",
                         f"**PER** {facts.get('PER', '—')} · **PBR** {facts.get('PBR', '—')} · **시총** {signed(facts.get('시가총액(억)')).lstrip('+')}억",
                         f"**업종** {facts.get('업종', '—')} · **외국인 보유** {facts.get('외국인 보유율(%)', '—')}%"]
                if type(low) in (int, float) and type(high) in (int, float):
                    lines.append(f'**52주** {low:,} ~ {high:,}')
                st.markdown('  \n'.join(lines))
                for label, key in (('전일', 'last_close_day'), ('오늘 잠정', 'today_provisional')):
                    row = flows.get(key)
                    if row:
                        st.caption(f"투자자 {label} {row.get('date', '')}: 외국인 {signed(row.get('foreign'))} · "
                                   f"기관 {signed(row.get('institution'))} · 개인 {signed(row.get('individual'))}주")
                for item in s.get('news') or []:
                    stamp = event_time(item.get('published'))
                    st.markdown(f"- {news_link(item)}  \n  :gray[{item.get('publisher', '')} · {stamp:%H:%M}]" if stamp
                                else f"- {news_link(item)}")
                if not s.get('news'):
                    st.caption('오늘 종목 뉴스 없음')
        st.markdown('##### 후보 비교 (Jev가 함께 본 종목)')
        ranked = sorted((c for c, v in probs.items() if c != 'NONE' and type(v) in (int, float)), key=lambda c: probs[c], reverse=True)
        rules = strategy_settings()[0]  # the saved values the runner applies
        top, floor = int(rules['strength_top_n']), rules['strength_min_prob']
        table = []
        for code, s in sorted(stocks.items(), key=lambda kv: probs.get(kv[0], 0), reverse=True):
            facts, last = s.get('facts') or {}, (s.get('investor_daily_net_qty') or {}).get('last_close_day') or {}
            rank = ranked.index(code) + 1 if code in ranked else None
            eligible = rank is not None and rank <= top and probs.get(code, 0) >= floor
            table.append({'종목': s.get('name', code), '선정': '●' if code in (watch.get('selected') or []) else '',
                          '순위': rank, 'Jev 확률': f"{probs.get(code, 0):.2f}",
                          '강도 진입 후보': '✓ 후보' if eligible else ('확률 부족' if rank is not None and rank <= top else '—'),
                          '사유': ', '.join(s.get('reasons') or []),
                          '등락': f"{s.get('change_pct', 0):+.2f}%", '거래대금': f"{s.get('value_mil', 0) / 100:,.0f}억",
                          'PER': facts.get('PER'), 'PBR': facts.get('PBR'),
                          '외국인(전일)': signed(last.get('foreign')), '기관(전일)': signed(last.get('institution')),
                          '오늘 뉴스': len(s.get('news') or [])})
        st.dataframe(table, hide_index=True, column_config={
            '순위': st.column_config.NumberColumn('순위', help='Jev가 매긴 확률 순서입니다. 1위가 가장 유력한 후보입니다.'),
            'Jev 확률': st.column_config.TextColumn('Jev 확률', help='Jev가 후보끼리 나눠 매긴 상대 점수입니다. 모든 후보를 더하면 1이라, 후보가 많을수록 값이 작아집니다. 수익 확률이 아닙니다.'),
            '강도 진입 후보': st.column_config.TextColumn('강도 진입 후보', help=f'관심종목 강도 진입의 첫 관문입니다: 순위 상위 {top}위 안 + 확률 {floor:g} 이상. '
                                                                         '통과한 종목도 전일 대비 상승률·1분봉 상승·거래량을 다시 확인한 뒤에만 삽니다. 이미 보유한 종목은 건너뜁니다.'),
        })
        with st.expander(f'관심종목 강도 진입은 어떻게 고르나요? (상위 {top}위 + 확률 {floor:g} · 도식)', icon=':material/schema:'):
            from guides import topic_view, strength_dot, values as guide_values
            v = guide_values()
            topic_view('strength', v)
            if ranked:
                st.markdown('**지금 선정 기준으로 계산하면**')
                st.dataframe([{'순위': i, '종목': (stocks.get(c) or {}).get('name', c), '확률': f'{probs[c]:.2f}',
                               f'상위 {top}위': '✓' if i <= top else '✗', f'확률 ≥ {floor:g}': '✓' if probs[c] >= floor else '✗',
                               '첫 관문': '통과' if i <= top and probs[c] >= floor else '탈락'}
                              for i, c in enumerate(ranked, 1)], hide_index=True, width='stretch')
                st.caption(f"확률 합계 {sum(probs[c] for c in ranked):.2f} (NONE 포함 시 1). 첫 관문을 통과해도 상승률·분봉·거래량 확인을 거쳐야 매수합니다.")
            import guide_ui as GU
            from guides import strength_flow
            GU.flow('jev-strength', strength_flow(v), strength_dot(v))
        if watch.get('market_news'):
            with st.expander('Jev가 함께 본 증시 뉴스'):
                for item in watch['market_news']:
                    stamp = event_time(item.get('published'))
                    st.markdown(f"- {news_link(item)}  \n  :gray[{item.get('publisher', '')} · {stamp:%H:%M}]" if stamp
                                else f"- {news_link(item)}")
        st.caption('Jev 확률은 선정용 상대 비교이며 수익 확률이 아닙니다. 1주 가격이 1차 매수액(100만 원)보다 비싼 종목은 선정에서 제외됩니다. '
                   '투자자 순매수는 일별 주식 수(오늘 행은 장중 잠정), 뉴스는 최근 하루 종목명 언급 기사입니다.')
    st.caption('관심종목 추가·제외는 좌측 사이드바에서 계좌별로 합니다.')
    rows = (movers or {}).get('rows') or {}
    if rows:
        st.markdown('##### 급상승·급하락 상위')
        codes = [*((movers.get('rankings') or {}).get('rise') or [])[:7], *((movers.get('rankings') or {}).get('fall') or [])[:7]]
        signed_bar(pd.DataFrame([{'종목': rows[c]['name'], '등락률(%)': rows[c]['change_pct']} for c in dict.fromkeys(codes) if c in rows]),
                   '종목', '등락률(%)', '전일 대비 등락률(%)')
        st.markdown('##### 거래대금 상위 (억 원)')
        top = (movers.get('rankings') or {}).get('trading_value') or []
        frame = pd.DataFrame([{'종목': rows[c]['name'], '거래대금(억)': round(rows[c]['value_mil'] / 100)} for c in top[:10] if c in rows])
        if not frame.empty:
            st.altair_chart(alt.Chart(frame).mark_bar(color='#f59e0b').encode(
                y=alt.Y('종목:N', sort='-x', title=None), x=alt.X('거래대금(억):Q'), tooltip=['종목', alt.Tooltip('거래대금(억):Q', format=',')])
                .properties(height=24 * len(frame)), width='stretch')
    with st.expander('거래대금·거래량·급상승·급하락 순위 표 전체'):
        movers_panel(movers, now)


def watchlist_editor(account):
    """Add or remove this account's watchlist stocks; the shared paper runner trades its list from the next cycle."""
    from watchlist import manual, edit_manual, manual_path, account_watchlist
    path = manual_path(account)
    paper = account == 'paper-shared'
    with st.form(f'watch_add_{account}', clear_on_submit=True, border=False):
        code = st.text_input('추가할 종목코드', max_chars=6, placeholder='예: 005930').strip()
        if st.form_submit_button('관심종목 추가', width='stretch'):
            try:
                quote = call(st.session_state['_read_adapter'], 'get_stock_price', {'stock_code': code}).get('Output_0', {})
                if quote.get('iem_cd') != code:
                    raise ValueError('종목을 찾지 못했습니다.')
                name = str(quote.get('iem_nm') or code).lstrip('*')
                edit_manual(add=code, name=name, path=path)
                st.toast(f'{name} 추가' + (' · 다음 주기부터 매매 후보' if paper else ' · 관찰용'))
                st.rerun()
            except (KeyError, ValueError, OSError) as exc:
                st.error(f'추가하지 못했습니다: {exc}')
    rows = account_watchlist(account)
    names = {c: n for c, n, _ in rows}
    with st.form(f'watch_remove_{account}', border=False):
        target = st.selectbox('뺄 종목', list(names), format_func=lambda c: f'{names[c]} ({c})', index=None,
                              placeholder='관심종목 선택')
        if st.form_submit_button('관심종목에서 빼기', width='stretch') and target:
            edit_manual(remove=target, path=path)
            st.toast(f'{names[target]} 제외 · Jev가 다시 골라도 이 계좌에서는 빠집니다')
            st.rerun()
    removed = manual(path)['remove']
    if removed:
        st.caption('직접 제외: ' + ', '.join(removed))
    st.caption('모의계좌 목록은 실행기의 매매 후보입니다.' if paper else '실계좌는 관찰 모드라 이 목록으로 주문하지 않습니다.')


def movers_panel(movers, now):
    if not isinstance(movers, dict) or not isinstance(movers.get('rankings'), dict):
        st.caption('순위 스캔 기록이 없습니다. 평일 08:58에 순위 계산기(market_movers)가 시작되어 5분마다 갱신합니다.')
        return
    at = event_time(movers.get('time'))
    age = (now - at).total_seconds() / 60 if at else None
    scope = (f"코스피200·코스닥150 구성종목 {movers.get('universe', '?')}개 중 키움 순위 상위 {movers.get('scanned', '?')}개를 NH 시세로 조회"
             if movers.get('source') == 'kiwoom_preselect' else f"코스피200·코스닥150 구성종목 {movers.get('scanned', '?')}개 NH 전체 스캔")
    st.caption(f"스캔 {at:%m/%d %H:%M:%S} · {scope} · {movers.get('seconds', '?')}초 소요" if at else '스캔 시각 미상')
    if age is None or age > 12:
        st.warning('순위가 12분보다 오래돼 실행기는 개별 종목을 후보에서 뺍니다(7개 ETF만 판단).')
    rows = movers.get('rows') or {}
    candidates = movers.get('candidates') or {}
    labels = {'trading_value': '거래대금 상위(인기 대용)', 'volume': '거래량 상위', 'rise': '급상승', 'fall': '급하락'}
    columns = st.columns(2)
    for i, (key, label) in enumerate(labels.items()):
        with columns[i % 2]:
            st.markdown(f'**{label}**')
            table = [{'종목': rows.get(c, {}).get('name', c), '현재가': f"{rows.get(c, {}).get('price', 0):,}",
                      '등락': f"{rows.get(c, {}).get('change_pct', 0):+.2f}%",
                      '거래대금': f"{rows.get(c, {}).get('value_mil', 0) / 100:,.0f}억",
                      'Jev 후보': '●' if c in candidates else ''} for c in movers['rankings'].get(key, [])[:10]]
            st.dataframe(table, hide_index=True)
    picks = [p for p in movers.get('theme_picks') or [] if isinstance(p, dict) and p.get('code')]
    if picks:
        st.markdown('**주도 테마 동반주**')
        st.dataframe([{'종목': rows.get(p['code'], {}).get('name', p['code']), '테마 묶음': p.get('group') or '—',
                       '현재가': f"{rows.get(p['code'], {}).get('price', 0):,}", '등락': f"{rows.get(p['code'], {}).get('change_pct', 0):+.2f}%",
                       '거래대금': f"{rows.get(p['code'], {}).get('value_mil', 0) / 100:,.0f}억"} for p in picks], hide_index=True)
    st.caption('● 표시 종목(각 순위 상위 3개, 최대 10개)과 주도 테마 동반주(강한 테마 묶음마다 1종목, 최대 2개)가 '
               '7개 ETF와 함께 Jev 종목 후보로 들어갑니다. 동반주는 실계좌 미러가 따라 사지 않습니다. '
               '등락률은 전일 종가 대비 직접 계산, "인기"는 거래대금 상위로 대신합니다. 지수 구성종목 밖 종목은 포함되지 않습니다.')


def strategy_settings():
    return read_settings(get_runtime_root() / 'nhplug' / 'shared-etf-paper')


def split_policy_summary():
    """Describe the last policy recorded by the shared runner, not the imported code version."""
    from engine_compare import recent_events as journal_tail
    path = get_runtime_root() / 'nhplug' / 'shared-etf-paper' / 'events.jsonl'
    events = journal_tail(path, 20000)
    latest = next((e for e in reversed(events) if isinstance(e, dict) and e.get('event') == 'strategy_settings'), None)
    if not latest or not isinstance(latest.get('settings'), dict):
        return '실행기의 분할 매수 적용 기록을 확인하지 못했습니다. 주문·판단 기록을 확인하세요.'
    step = latest['settings'].get('add_step_pct')
    if type(step) not in (int, float) or not math.isfinite(step):
        return '실행기의 분할 매수 간격 기록을 확인하지 못했습니다. 주문·판단 기록을 확인하세요.'
    stamp = event_time(latest.get('time'))
    recorded = f"최근 실행 기록 {stamp:%m/%d %H:%M}" if stamp else '최근 실행 기록 · 시각 미확인'
    if latest['settings'].get('scale_policy_version') == 2:
        interval = latest['settings'].get('scale_interval_min', 5)
        if type(interval) not in (int, float) or not math.isfinite(interval):
            return '실행기의 분할 매수 간격 기록을 확인하지 못했습니다. 주문·판단 기록을 확인하세요.'
        return (f'{recorded}: 마지막 체결가 대비 ±{step:g}% 가격 단계와 완료 분봉 반등·{interval:g}분 간격을 확인합니다. '
                '익절 후에는 매도 사유가 확인된 경우만 검토하며, 새 Jev 매수 판단도 진입·보호 조건을 통과해야 합니다. '
                '종목·회당·하루·보유 금액과 손절 보호가 우선합니다.')
    return (f'{recorded}: 첫 매수가 대비 −{step:g}%·−{2 * step:g}% 반등 시 최대 3회 매수하며, '
            '매도가 시작된 포지션은 추가 매수를 중단합니다.')


def holding_targets(symbol, average, settings, engine=None):
    """(half, full) take-profit prices of the position's own plan, as the runner sells it (engine_exits.plan_for): a
    trend or new-listing plan has no fixed full target (None, a trailing stop instead), 선별 v2 sells at its own +8%/+15%.
    The settings' defensive plan when the ledger is unknown."""
    from engine_exits import defensive_plan, plan_for
    from engine_roles import ledger_root, main_engine
    from nh_paper_isolated import plan_levels
    nhplug = get_runtime_root() / 'nhplug'
    try:
        root = ledger_root(nhplug, engine or main_engine(nhplug))
        plan, special = plan_for(root, symbol, settings) if root else (defensive_plan(settings), None)
        if special:  # vibe_runner 낙폭과대: one absolute target
            return None, float(special['target_price'])
        levels = plan_levels(plan)
    except (OSError, ValueError, TypeError, KeyError, ArithmeticError):
        levels = plan_levels(defensive_plan(settings))
    return tuple(None if levels[key] is None else average * float(levels[key]) for key in ('half', 'all'))


def targets_text(half, full):
    """'절반 X · 전량 Y', '절반 X · 나머지 추적' (no fixed full target) or '익절 Y' (one exit)."""
    if full is None:
        return f'절반 {half:,.0f} · 나머지 추적 청산' if half else '익절 목표 없음'
    return f'절반 {half:,.0f} · 전량 {full:,.0f}' if half and half < full else f'익절 {full:,.0f}'


def with_levels(basis, symbol, engine=None, live_stop_pct=None):
    """Add the stop (holding_stop_display) and the position plan's half/full take-profit prices to a chart basis."""
    if not basis:
        return basis
    settings = strategy_settings()[0]
    stop, label = holding_stop_display(symbol, basis['price'], settings, engine, live_stop_pct)
    half, full = holding_targets(symbol, basis['price'], settings, engine)
    return {**basis, 'stop': stop, 'stop_label': f'손절({label})', 'half': half, 'all': full}


def holding_stop_display(symbol, average, settings, engine=None, live_stop_pct=None):
    """Reference stop price and provenance; never treat an unavailable market view as current."""
    from engine_roles import ledger_root, main_engine
    from nh_paper_isolated import exit_plans, market_view, plan_levels, regime_stop_levels, stop_symbol_index
    nhplug = get_runtime_root() / 'nhplug'
    try:
        engine = engine or main_engine(nhplug)
        root = ledger_root(nhplug, engine)
        if root is None:
            raise ValueError('engine unavailable')
        if live_stop_pct is not None:
            from nh_live_follow import stop_percent_for_positions
            info = stop_percent_for_positions(root / 'orders.sqlite3', nhplug, {symbol: 1}, live_stop_pct)[symbol]
            pct, regime = info['pct'], info.get('regime')
            live_market = nhplug / 'shared-etf-paper'
            index = stop_symbol_index(live_market, symbol)
            view = market_view(live_market)
            if ((view or {}).get(index) or {}).get('stale_reused_from'):
                return average * (1 - pct / 100), '미러 계좌 기준 · 시황 재사용/오래됨'
            if info.get('profile') in ('oversold', 'ipo'):
                label = f"모의 {'낙폭과대' if info['profile'] == 'oversold' else '신규주'} 계획 손절(모의와 같음)"
            elif info.get('profile') != 'ordinary':
                label = '계좌 설정 · 전략 확인 필요' + (' · 시황 미확인' if regime not in ('상승장', '횡보장', '하락장') else '')
            elif regime not in ('상승장', '횡보장', '하락장'):
                label = '계좌 설정 · 시황 미확인'
            else:
                label = f'미러 계좌·시황 기준({regime})'
            return average * (1 - pct / 100), label

        from engine_exits import plan_for, runner_of  # the runner keeping this ledger decides the plan (11/17 review)
        plan, special = plan_for(root, symbol, settings)
        if special:
            stop = special.get('stop_price')
            if type(stop) in (int, float) and math.isfinite(stop) and stop > 0:
                return float(stop), '낙폭과대 전용 손절'
            raise ValueError('special stop unavailable')
        if runner_of(root) == 'vibe':
            from nh_live_follow import ordinary_positions
            verified = ordinary_positions(root / 'orders.sqlite3').get(symbol) is True
            note = '설정 기준' if verified else '기본 설정 · 전략 미확인'
            market_root = nhplug / 'shared-etf-paper'
        else:
            note = '보유 계획' if symbol in exit_plans(root) else '기본 설정 · 보유 계획 미확인'
            market_root = root
        if plan.get('profile') in ('ipo', 'oversold'):
            return average * float(plan_levels(plan)['stop']), '전용 손절 계획'
        view = market_view(market_root)
        index = stop_symbol_index(market_root, symbol)
        item = ((view or {}).get(index) or {}) if index else {}
        regime = item.get('regime')
        if regime not in ('상승장', '횡보장', '하락장'):
            return average * float(plan_levels(plan)['stop']), f'{note} · 시황 미확인'
        stale = ' · 시황 재사용/오래됨' if item.get('stale_reused_from') else ''
        return average * float(regime_stop_levels(plan, view, index, symbol)['stop']), f'{note} · {index} {regime}{stale}'
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, ArithmeticError):
        pct = live_stop_pct if live_stop_pct is not None else settings.get('stop_loss_pct')
        if type(pct) in (int, float) and math.isfinite(pct) and 0 < pct < 100:
            return average * (1 - pct / 100), '기본 설정 · 적용 상태 미확인'
        return None, '손절 기준 미확인'


def account_switcher(records, picked):
    """Accounts as one row of buttons on the trading desk, in step with the sidebar choice (same session key)."""
    def follow():
        if st.session_state.get('trading_account_tab'):
            st.session_state.trading_account = st.session_state.trading_account_tab
    st.session_state.trading_account_tab = picked
    from accounts import ENGINE_LABELS
    engine = lambda r: ENGINE_LABELS.get(r.get('engine'), r.get('engine'))
    st.segmented_control(
        '계좌', list(records), key='trading_account_tab', on_change=follow,
        format_func=lambda k: f"{records[k].get('label', k)} · {engine(records[k])}",
        help='모의계좌와 실계좌를 여기서 바로 바꿉니다(왼쪽 사이드바 계좌 선택과 같습니다). 실계좌 화면은 조회 전용입니다.')


def compare_panel():
    """성과 비교 (2026-11-19, was 엔진 비교): Jev trading and the long-term engine over the same days."""
    import strategy_compare_view
    strategy_compare_view.render(get_runtime_root() / 'nhplug')


UP, DOWN = '#ef4444', '#3b82f6'  # Korean convention: red rise, blue fall


def signed_bar(frame, label, value, title, fmt='+.2f'):
    """Horizontal bars coloured by sign, with the value printed (colour is never the only cue)."""
    frame = frame.sort_values(value, ascending=False)
    frame['항목'] = [f'{n} {v:{fmt}}' for n, v in zip(frame[label], frame[value])]  # value in the label, not a second layer
    st.altair_chart(alt.Chart(frame).mark_bar().encode(
        y=alt.Y('항목:N', sort=list(frame['항목']), title=None, axis=alt.Axis(labelLimit=300)), x=alt.X(f'{value}:Q', title=title),
        color=alt.condition(alt.datum[value] >= 0, alt.value(UP), alt.value(DOWN)),
        tooltip=[label, alt.Tooltip(value, format=fmt)]).properties(height=70 + 28 * len(frame)), width='stretch')


def change_text(value, digits=2):
    """Korean colour convention with an arrow, so colour is never the only cue."""
    if value is None:
        return '—'
    return f':red[▲ {value:+.{digits}f}%]' if value > 0 else f':blue[▼ {value:+.{digits}f}%]' if value < 0 else f'■ {value:+.{digits}f}%'


def index_chart(history):
    frame = pd.DataFrame(history, columns=['날짜', '종가'])
    frame['날짜'] = pd.to_datetime(frame['날짜'])
    frame['20일선'] = frame['종가'].rolling(20).mean()
    frame['60일선'] = frame['종가'].rolling(60).mean()
    long = frame.melt('날짜', var_name='선', value_name='값').dropna()
    st.altair_chart(alt.Chart(long).mark_line().encode(
        x=alt.X('날짜:T', title=None, axis=alt.Axis(format='%m/%d')), y=alt.Y('값:Q', title=None, scale=alt.Scale(zero=False)),
        color=alt.Color('선:N', scale=alt.Scale(domain=['종가', '20일선', '60일선'], range=[THEME.ink(), '#f59e0b', '#a855f7']),
                        legend=alt.Legend(orient='top', title=None)),
        strokeDash=alt.condition(alt.datum['선'] == '종가', alt.value([1, 0]), alt.value([4, 3])),
        tooltip=[alt.Tooltip('날짜:T', format='%Y-%m-%d'), '선', alt.Tooltip('값:Q', format=',.2f')]).properties(height=220),
        width='stretch')


def strategy_panel():
    """The live algorithm: the route in use now (Archify diagram), how rules change by market state, and its data."""
    import strategy_map as smap
    head, toggle = st.columns([3, 1], vertical_alignment='center')
    head.subheader('전략 구조')
    auto = auto_on()  # the header's one switch (user 2026-11-17); this page keeps its 60 s pace
    toggle.caption('자동 갱신 켜짐 · 60초' if auto else '자동 갱신 꺼짐 · 상단에서 켭니다')
    engine = st.segmented_control('엔진', ['Jev 트레이딩', '장기투자'], default='Jev 트레이딩', key='strategy_engine',
                                  help='두 엔진은 판단 주기(1분 · 하루 1회)와 대상(국내 ETF·개별주 · 미국 지수·테마 ETF)이 달라 따로 봅니다.')
    if engine == '장기투자':
        longterm_algorithm_view()
        return

    @st.fragment(run_every='60s' if auto else None)
    def body():
        root = get_runtime_root() / 'nhplug' / 'shared-etf-paper'
        current = smap.state(root)
        route, description = smap.active_route(current)
        kospi, kosdaq = current['indices'].get('코스피'), current['indices'].get('코스닥')
        phase_key, phase_text = current['phase']
        phase_look = {'entry': ('ok', 'schedule', True), 'pre_open': ('warn', 'hourglass_top', True),
                      'no_new_buys': ('warn', 'block', False), 'flatten': ('warn', 'logout', True)}.get(phase_key, ('off', 'bedtime', False))
        auto_look = (('alert', 'stop_circle', '중단 요청') if current['stop_file'] else ('ok', 'bolt', 'ON') if current['auto']
                     else ('off', 'power_settings_new', 'OFF'))
        averaging = smap.averaging_down(current)

        def index_card(name, item):
            if not item:
                return {'label': name, 'value': '미확인', 'tone': 'off', 'icon': 'help'}
            tone, icon = sc.regime(item['regime'])
            change = item.get('change_pct')
            return {'label': name, 'value': item['regime'], 'tone': tone, 'icon': icon,
                    'sub': f"오늘 {item['tone']}" + (f" {change:+.2f}%" if type(change) in (int, float) else ''),
                    'help': '국면은 20·60일선과 20일 수익률 규칙으로, 오늘 흐름은 등락과 투자자 순매수로 정합니다. 국면이 청산 방식과 진입 확인을 바꿉니다.'}
        sc.row([{'label': '시간대', 'value': phase_text, 'tone': phase_look[0], 'icon': phase_look[1], 'pulse': phase_look[2],
                 'help': f"09:10~{current['clock']['cutoff']} 신규 매수 · {current['clock']['flatten']}부터 당일 정리 · {current['clock']['close']} 이후 장 종료. 시각은 제한 규칙 탭에서 정합니다."},
                {'label': '메인 엔진', 'value': 'Jev' if current['engine'] == 'jev' else 'vibe-trading', 'tone': 'ai', 'icon': 'smart_toy',
                 'help': '모의계좌의 매수·매도 판단을 맡는 엔진입니다. 2026-11-17부터 모든 계좌가 Jev로 고정이고, 장기투자는 "장기투자" 탭의 별도 가상 계좌입니다.'},
                {'label': '자동매매', 'value': auto_look[2], 'tone': auto_look[0], 'icon': auto_look[1], 'pulse': auto_look[0] == 'ok',
                 'help': 'ON이면 실행기가 1분마다 판단해 주문합니다. 중단 요청은 STOP 파일이 있다는 뜻입니다.'}])
        sc.row([index_card('코스피', kospi), index_card('코스닥', kosdaq),
                {'label': '추가 매수(롱)', 'value': {'blocked': '중단', 'allowed': '허용', 'unknown': '미확인'}[averaging],
                 'tone': {'blocked': 'down', 'allowed': 'ok', 'unknown': 'off'}[averaging],
                 'icon': {'blocked': 'block', 'allowed': 'add_circle', 'unknown': 'help'}[averaging],
                 'help': '하락장이면서 오늘 약세면 롱 종목의 하락 후 추가 매수를 제한합니다(물타기 방지).'}])
        import dialogs as dlg
        dlg.buttons([('자세히 · 매매 기준 전체', 'stg_d_rules', dlg.rules_dialog, (current['settings'], {k: v for k, v in STRATEGY_LABELS.items() if k not in RETIRED_SETTINGS}, STRATEGY_HELP)),
                     ('자세히 · 시황별 대응', 'stg_d_regime', dlg.table_dialog,
                      ('시황별 대응', [{'': '▶ 현재' if r.get('현재') else '', **{k: v for k, v in r.items() if k != '현재'}} for r in smap.regime_rows(current)],
                       '국면은 매수 시점에 청산 방식을 정합니다. ▶ 표시가 지금 국면입니다.')),
                     ('자세히 · 참고 데이터', 'stg_d_sources', dlg.table_dialog,
                      ('전략이 읽는 데이터', [{'데이터': src['name'], '쓰임': src['used_for'], '마지막 갱신': (src['time'] or '')[:16].replace('T', ' ') or '없음',
                                           '경과(분)': src['age_min'], '기준(분)': src['limit_min'], '상태': '정상' if src['fresh'] else phase_text if src.get('idle') and src['time'] else '오래됨·없음'}
                                          for src in current['sources']], '기준보다 오래된 데이터는 그 데이터를 쓰는 규칙이 판단을 보류합니다.'))],
                    columns=3)
        st.info(f'설정·시간 기준 예상 경로 · {description} · 실행 및 실제 판단 여부는 별도 확인', icon=':material/route:')
        if not current['market_fresh']:
            st.warning('시장 상황 요약이 20분보다 오래돼 국면별 규칙(추가 매수 중단)이 판단되지 않습니다. 실행기도 같은 기준을 씁니다.')
        diagram = smap.spec(current)
        try:
            html = smap.render(diagram, root / 'strategy-map')
            import guide_ui as GU
            GU.archify_frame(html, '전략 구조 도식')
            st.caption('Archify 도식 · 초록 굵은 경로는 설정·시간 기준 예상 흐름이며 실제 실행이나 주문을 뜻하지 않습니다. 주문은 09:00 개장부터 냅니다(장전 준비는 그 전에 끝남).')
            st.caption('데이터 갱신 · ' + ' · '.join(f"{src['name']} {src['age_min']}분 전" if src['age_min'] is not None
                                                  else f"{src['name']} 기록 없음" for src in current['sources']))
        except Exception as exc:
            THEME.graphviz(smap.dot(diagram), width='stretch')
            st.caption(f'Archify를 쓸 수 없어 기본 도식으로 표시합니다 ({type(exc).__name__}). 초록 경로는 설정·시간 기준 예상 흐름입니다.')

        import settings_view as sv
        import strategy_view as sview
        sv.inject()
        st.markdown(sview.CSS, unsafe_allow_html=True)
        rules = strategy_settings()[0]
        rows = smap.regime_rows(current)
        now_row = next((i for i, r in enumerate(rows) if r['현재']), None)
        last_row = None if now_row is not None else sview.matching(rows, (kospi or {}).get('regime'), (kospi or {}).get('tone'))

        st.markdown('##### 시황별 대응')
        if now_row is not None:
            r = rows[now_row]
            st.caption('코스피 현재 시황 기준 · 신규 진입에 적용할 청산 방식')
            st.markdown(sview.regime_card(r, current=True), unsafe_allow_html=True)
        else:
            st.warning('시장 자료가 오래됐거나 부족해 현재 시황을 확인할 수 없습니다.', icon=':material/history:')
            if last_row is not None:
                st.caption('마지막으로 확인된 시황 · 현재 적용 상태가 아닌 참고 기록')
                st.markdown(sview.regime_card(rows[last_row], last=True), unsafe_allow_html=True)
        st.caption('일반 전략의 신규·기존 보유분은 현재 시황이 포지션에 불리하면 손절을 최대 1%로 좁힙니다(롱은 횡보장·하락장, 인버스는 상승장·횡보장). '
                   '낙폭과대·신규주는 전용 손절을 따르고, 시황을 확인할 수 없으면 기존 손절 기준을 사용합니다. '
                   '코스닥 ETF·코스닥150 종목은 코스닥 시황을 사용합니다.')
        with st.expander('다른 시황의 대응과 청산 방식 보기'):
            st.markdown('###### 청산 방식')
            for col, (name, items, note) in zip(st.columns(3), sview.exit_cards(rules)):
                col.markdown(sv.card_html(name, items, *sview.EXITS[name], note=note),
                             unsafe_allow_html=True)
            st.markdown('###### 시황별 대응')
            for col, regime in zip(st.columns(3), sview.REGIMES):
                col.markdown(f"**{regime}**")
                for i, r in enumerate(rows):
                    if r['국면'] == regime:
                        col.markdown(sview.regime_card(r, i == now_row, i == last_row), unsafe_allow_html=True)
        st.markdown('##### 엔진별 진입 경로')
        st.caption(f'켜짐은 해당 규칙의 설정 상태이며 현재 판단·주문이 실행 중이라는 뜻은 아닙니다. {sview.route_order(rules)} 순으로 확인합니다.')
        routes = sview.route_cards(rules)
        cols = st.columns([1, 0.06] * (len(routes) - 1) + [1])
        for i, (title, items, color, icon, on) in enumerate(routes):
            cols[i * 2].markdown(sv.card_html(title, items, color if on else '#64748b', icon, '켜짐' if on else '꺼짐', on, wide=True),
                                 unsafe_allow_html=True)
            if i < len(routes) - 1:
                cols[i * 2 + 1].markdown('<div class="sv-arrow" aria-hidden="true">arrow_forward</div>', unsafe_allow_html=True)
        oversold_algorithm_view(root, rules)
        oversold_watch_view(root)
        st.caption(f"주문 가드(회당·하루 한도)는 각 경로에 적용됩니다. 일반 신규 매수는 {current['clock']['cutoff']} 이후 제한되며, 선별 v2는 별도 매수 창을 따르고 반대 방향 보유 차단을 적용하지 않습니다. "
                   + ('Jev가 반대 방향을 고르고 보유 종목이 손실 중이며 약세장이면 그 보유를 먼저 정리합니다(방향 전환). ' if rules['direction_switch'] else '')
                   + '도식은 오른쪽 위 "가이드 센터" → 매수 전략 · 관심종목 진입 · 신규주 진입.')

        from nh_paper_isolated import OVERNIGHT_RULES, SWING_RULES
        st.markdown('##### 다음 날까지 보유하는 조건')
        night, swing = st.tabs([f'종가매매 · 모두 충족해야 보유 ({len(OVERNIGHT_RULES)}개)',
                                f"스윙 지속 · 이미 넘어온 추세형 (최대 {int(rules['swing_max_days'])}거래일, {len(SWING_RULES)}개)"])
        with night:
            st.markdown(sview.checklist_html([text for _, text in OVERNIGHT_RULES], '#f97316'), unsafe_allow_html=True)
            st.caption('하나라도 빠지면 장 마감 정리 시각부터 팝니다. 종목마다 결과와 처음 걸린 조건을 하루 한 번 overnight_hold 기록으로 남깁니다.')
        with swing:
            st.markdown(sview.checklist_html([text for _, text in SWING_RULES], '#a855f7'), unsafe_allow_html=True)
            st.caption('첫날 밤은 종가매매 조건을 모두 통과해야 하고, 이틀째부터는 강세장이 이어지고 종목 추세가 살아 있으면 이 조건만으로 계속 보유합니다. '
                       '하나라도 빠지면 그날 장 마감 정리 때 팝니다. 보유 중 손절·추적 손절은 매분 그대로 작동합니다.')

        st.markdown('##### 참고 데이터')
        sc.row([{'label': src['name'], 'icon': 'database',
                 'value': '정상' if src['fresh'] else '없음' if src['time'] is None else phase_text if src.get('idle')
                 else f"오래됨 · {src['age_min']}분",
                 'tone': 'ok' if src['fresh'] else 'off' if src['time'] is None or src.get('idle') else 'warn',
                 'sub': f"{(src['time'] or '')[11:16] or '—'} 갱신 · 기준 {src['limit_min']}분",
                 'help': f"쓰임: {src['used_for']}. 기준보다 오래되면 이 데이터를 쓰는 규칙이 판단을 보류합니다."
                         + (' 장 밖에서는 장중 작업(09:00~)이 갱신하지 않아 마지막 값을 그대로 보여 줍니다.' if src.get('idle') else '')}
                for src in current['sources']])
        settings = current['settings']
        with st.expander('알고리즘 상세'):
            st.markdown(
                f"- **진입**: Jev 후보 신뢰도 {settings['entry_confidence']:.2f} 이상 + 최근 분봉 상승. "
                f"보유 종목 추가 진입은 실행기 분할 정책을 따르고, 반대 방향(롱↔인버스)은 차단합니다. 손절 폭이 호가 차이 2배보다 좁으면 거부, {current['clock']['cutoff']} 이후 신규 매수 없음.\n"
                f"- **분할 추가 매수**: {split_policy_summary()}\n"
                "- **청산(방어형: 하락·횡보장)**: 종목별 목표에서 **전량** 익절. 목표 = 최근 20일 장중 상승폭 중앙값×국면 계수(하락 0.4·횡보 0.5·그 외 0.6, 0.8~3%). "
                f"데이터가 없으면 +{settings['take_profit_half_pct']:g}% 절반·+{settings['take_profit_all_pct']:g}% 나머지. "
                f"−{settings['stop_loss_pct']:g}% 손절, Jev 매도 신뢰도 {settings['sell_confidence']:.2f} 이상 전량.\n"
                f"- **청산(추세형: 상승장이거나 지수 강세일)**: +{settings['trend_half_pct']:g}% 절반, 나머지는 그 가격에 닿은 뒤 고점 대비 "
                f"−{settings['trend_trail_pct']:g}% 추적 손절(2배 ETF는 ×1.5), 손절·본전 규칙은 같음. 보유 중 강세로 바뀌면 추세형으로 전환(되돌리지 않음).\n"
                f"- **장 마감**: 기본은 {current['clock']['flatten']}부터 당일 정리. 위 종가매매 조건을 모두 충족한 종목만 "
                f"{settings['overnight_max_manwon']:g}만원 한도 안에서 다음 날까지 보유. 익절·손절 비율은 매수 시점에 고정되고, "
                "보유 중에는 추세형으로의 전환만 일어납니다. 종가매매 조건과 한도는 그날의 데이터와 설정으로 판단합니다.\n"
                f"- **보호 장치**: {guard_text()}(공유 모의계좌 주문 가드), 미확인 주문이 있으면 정지.\n"
                "- **기록**: 원장(events·events_v2), AI 입력(model_inputs), 포지션 경로·판단 정산(position_paths·decision_outcomes).\n"
                "- **실계좌**: 실계좌 1은 미러 실행기가 Jev 모의 체결을 계좌 한도로 따라가며, 실계좌별 실주문 스위치가 꺼져 있으면 계획만 기록합니다. 장기투자 실계좌는 장기투자 실행기가 그날 장기 엔진 계획을 같은 보호 경로로 따릅니다.")
        from research_store import history
        replay = next(iter(history('exit_replay', 1, Path.home() / '.vibe-trading/nhplug/shared-etf-paper/market-research.sqlite3')), None)
        with st.expander('청산 규칙 재현 평가 (지난 60일 5분봉)'):
            if not replay:
                st.caption('아직 평가 기록이 없습니다. scripts/exit_replay.py를 실행하면 생깁니다.')
            else:
                names = {'split': '1%·2% 분할', 'target': '데이터 목표 전량', 'trend': '추세형(+2% 절반·추적)'}
                st.dataframe(pd.DataFrame([{'국면': r['regime'], '규칙': names.get(r['rule'], r['rule']), '건수': r['trades'],
                                            '평균 %': r['avg_return_pct'], '승률': f"{r['win_rate']:.0%}", '중앙 %': r['median_pct'],
                                            '최악 %': r['worst_pct']} for r in replay['table']]), hide_index=True, width='stretch')
                st.caption(f"평가 {replay['time'][:16]} · 매일 10·11·13시에 국면 방향과 맞는 ETF를 샀다고 가정하고 14:40까지 규칙대로 청산. "
                           'Yahoo Finance 5분봉, 비용 미반영, 손절과 목표가 한 봉에 같이 닿으면 손절로 계산. 실제 진입(Jev 선택)과 다르므로 규칙 비교용입니다.')
        reports = (sorted([*(root / 'reviews').glob('improvement-*.md'), *(root / 'reviews').glob('oversold-weekly-*.md')],
                          key=lambda f: (f.stem.rsplit('-', 3)[-3:], f.stem), reverse=True) if (root / 'reviews').is_dir() else [])
        with st.expander(f"장 마감 개선안 · 저장된 연구 및 감사 보고서 ({len(reports)}건)", icon=':material/lightbulb:', expanded=bool(reports)):
            if not reports:
                st.caption('아직 저장된 보고서가 없습니다. 개선안·주간 감사의 Codex 자동 작성 예약은 현재 없습니다. '
                           '보고서는 제안만 하고 설정은 바꾸지 않습니다.')
            else:
                pick = st.selectbox('보고서', reports, key='improvement_report',
                                    format_func=lambda f: (f"{f.stem.removeprefix('oversold-weekly-')} · 낙폭과대 주간 감사" if f.stem.startswith('oversold-weekly-')
                                                           else f"{f.stem.removeprefix('improvement-')} · 장 마감 개선안"))
                st.markdown(for_streamlit(pick.read_text(encoding='utf-8')))
                st.caption(f'원본: {pick.name} · 데이터는 scripts/daily_improvement.py가 모읍니다. 제안을 적용하려면 대화에서 승인해 주세요.')
        reviews = history('daily_review', 3, root / 'market-research.sqlite3')
        with st.expander(f"장 마감 복기 · 최근 {len(reviews)}일 (Jev에 다음 날 전달)"):
            if not reviews:
                st.caption('아직 복기 기록이 없습니다. 장 마감 뒤(15:10) 학습 관찰기가 만듭니다.')
            for review in reviews:
                facts = review.get('facts') or {}
                st.markdown(f"**{review.get('day')}** · {review.get('regime') or '국면 미확인'} · 청산 {facts.get('closed', 0)}건 "
                            f"(이익 {facts.get('wins', 0)}) · 실현 {facts.get('realized_krw', 0):+,}원")
                st.markdown('\n'.join(f'- {lesson}' for lesson in review.get('lessons') or []))
        st.caption(f"상태 읽은 시각 {current['time'][11:19]} · 휴장일은 실행기가 따로 확인합니다.")

    body()


def market_panel():
    import market_context
    from market_view import render_summary
    import premarket_view

    head, button = st.columns([4, 1], vertical_alignment='center')
    head.subheader('시장 상황')
    data = market_context.load()
    if button.button('시장 자료 갱신', width='stretch', icon=':material/refresh:',
                     help='네이버 금융과 Yahoo Finance의 시장 자료를 다시 조회합니다.'):
        with st.spinner('시장 자료를 조회하는 중'):
            data = market_context.collect()
            market_context.write(data)

    render_summary(data)
    if data:
        if data.get('errors'):
            st.warning('조회 실패 · ' + ' · '.join(data['errors']))
        market_indicators(data)
        import broker_data_view  # Kiwoom / Toss parts of the snapshot (2026-11-17)
        broker_data_view.market_extras(data)
    import vibe_longterm_view  # the long-term engine's US view sits next to the domestic one (L2, 2026-11-17)
    vibe_longterm_view.market_summary(get_runtime_root() / 'nhplug' / 'vibe-longterm')
    st.divider()
    premarket_view.render()


def market_indicators(data):
    st.markdown('### 국내 지수·수급')
    indices = list((data.get('indices') or {}).values())
    fallback = any(isinstance(item.get('fallback'), dict) for item in indices)
    st.caption(('네이버 금융(조회 실패한 지수는 키움·토스 대체 출처, 각 지수 아래 표시)' if fallback else '네이버 금융')
               + ' · 순매수와 거래대금은 억 원 · 장중 수급은 잠정치')
    if indices:
        for col, item in zip(st.columns(len(indices)), indices):
            with col, st.container(border=True):
                market_index_panel(item)
    else:
        st.info('국내 지수 자료가 없습니다.')

    st.markdown('### 테마 강세·약세')
    themes = data.get('themes') or {}
    if themes.get('groups'):
        st.caption(('키움 테마그룹(네이버 조회 실패 대체)' if str(themes.get('source') or 'naver').startswith('kiwoom') else '네이버 금융')
                   + ' · 테마 등락률을 구성 종목 수로 가중 평균한 값')
        signed_bar(pd.DataFrame(themes['groups']), 'group', 'change_pct', '평균 등락률 (%)')
        rows = [{'구분': label, '테마': t['name'], '등락률 (%)': t['change_pct'], '상승 / 하락': f"{t['rise']} / {t['fall']}"}
                for label, key in [('강세', 'top'), ('약세', 'bottom')] for t in themes.get(key, [])]
        if rows:
            st.dataframe(rows, hide_index=True, alt='강세·약세 테마',
                         column_config={'등락률 (%)': st.column_config.NumberColumn(format='%+.2f')})
    else:
        st.info('테마 자료가 없습니다.')

    st.markdown('### 해외 지표')
    prices = data.get('global') or {}
    st.caption('Yahoo Finance · 일봉 종가 · 전일 대비')
    if prices:
        from market_view import number

        cols = st.columns(5)
        for i, (name, item) in enumerate(prices.items()):
            with cols[i % len(cols)], st.container(border=True):
                st.caption(f"{name} · {item.get('date') or '기준일 미확인'}")
                st.markdown(f"**{number(item.get('price'), ',.2f')}**  \n{change_text(item.get('change_pct'))}")
    else:
        st.info('해외 지표가 없습니다.')


def market_index_panel(item):
    from market_view import breadth, index_display, number

    regime = item.get('regime') or {}
    day, close, delta = index_display(item)
    st.markdown(f"### {item['name']} {number(close, ',.2f')} {change_text(delta)}")
    flow = f" · 오늘 흐름 {item['flow']}" if day == datetime.now(KST).date().isoformat() and item.get('flow') else ''
    st.caption(f"기준일 {day or '미확인'} · 일봉 국면 {regime.get('label', '미확인')}{flow}")
    if item.get('reused_from'):
        st.caption(f"마지막 정상 조회 {item['reused_from']}")
    if isinstance(item.get('fallback'), dict):
        st.caption(f"대체 출처 · {item['fallback'].get('source')} · {item['fallback'].get('note', '')}")
    if regime.get('reasons'):
        st.caption(' · '.join(regime['reasons']))
    if item.get('history'):
        index_chart(item['history'])
    if day and day < str(item.get('traded_at') or '')[:10]:
        st.caption('장전 · 당일 거래대금·등락 종목 수·수급 집계 대기')
    else:
        up, down = breadth(item) or (None, None)
        for col, label, value in zip(st.columns(3), ('거래대금 (억 원)', '상승 종목', '하락 종목'),
                                     (item.get('value_100m'), up, down)):
            col.metric(label, number(value, ',.0f'))
        program = item.get('program_100m') or {}
        flows = [{'주체': k, '순매수': v} for k, v in {**(item.get('investors_100m') or {}),
                 '프로그램 전체': program.get('전체')}.items() if number(v, ',.0f') != '—']
        if flows:
            signed_bar(pd.DataFrame(flows), '주체', '순매수', '순매수 (억 원)', fmt='+,.0f')
        st.caption(f"프로그램 순매수 (억 원) · 차익 {number(program.get('차익'), '+,.0f')} · 비차익 {number(program.get('비차익'), '+,.0f')}")
    with st.expander('국면 계산값과 52주 범위'):
        rows = [{'항목': label, '값': number(value, ',.2f')} for label, value in (
            ('20일선', regime.get('ma20')), ('60일선', regime.get('ma60')),
            ('20일 수익률 (%)', regime.get('ret20_pct')), ('120일 고점 대비 (%)', regime.get('drawdown_pct')),
            ('52주 최고', item.get('high52')), ('52주 최저', item.get('low52')))]
        st.dataframe(rows, hide_index=True, alt='국면 계산값과 52주 범위')


def confidence_chart(decisions, settings):
    """Jev confidence over time against the buy and sell thresholds now in force."""
    rows = []
    for row in decisions[-180:]:
        if type(row.get('jev_confidence')) in (int, float):
            rows.append({'시각': row['time'], '신뢰도': row['jev_confidence'], '종류': '매수 후보'})
        if row.get('jev_holding') == 'SELL' and type(row.get('jev_holding_confidence')) in (int, float):
            rows.append({'시각': row['time'], '신뢰도': row['jev_holding_confidence'], '종류': '보유 매도의견'})
    if not rows:
        return
    frame = pd.DataFrame(rows)
    lines = alt.Chart(frame).mark_line(point=True).encode(
        x=alt.X('시각:T', title=None, axis=alt.Axis(format='%H:%M')), y=alt.Y('신뢰도:Q', scale=alt.Scale(domain=[0, 1])),
        color=alt.Color('종류:N', scale=alt.Scale(domain=['매수 후보', '보유 매도의견'], range=['#a855f7', '#e879f9'])),
        tooltip=[alt.Tooltip('시각:T', format='%H:%M:%S'), '종류', alt.Tooltip('신뢰도:Q', format='.2f')])
    rules = alt.Chart(pd.DataFrame([{'기준': f"매수 {settings['entry_confidence']:.2f}", 'y': settings['entry_confidence']},
                                    {'기준': f"매도 {settings['sell_confidence']:.2f}", 'y': settings['sell_confidence']}])).mark_rule(
        strokeDash=[4, 4], color='#94a3b8').encode(y='y:Q', tooltip=['기준'])
    st.altair_chart((lines + rules).properties(height=220), width='stretch')
    st.caption('점선은 지금 적용 중인 매수·매도 기준입니다. 점선 위로 올라온 판단만 매매로 이어질 수 있습니다.')


def accounts_panel(read):
    """Per-account main engine, auto switch and budget; live accounts are observation-only with their results."""
    from accounts import load
    records = load()
    st.subheader('계좌·자동매매')
    st.caption('계좌마다 자동매매와 금액을 정합니다. 공유 모의계좌는 Jev 트레이딩이고, 실계좌는 계좌마다 운용 방식(Jev 트레이딩 미러 · 장기투자)을 고릅니다. '
               '실계좌는 각 계좌 탭 맨 위 "실주문 스위치"(확인 문구 필요)를 켠 계좌만 실제 주문을 내고, 꺼진 계좌는 메인 엔진 체결을 그 계좌 한도로 계획만 기록합니다.')
    import engine_view  # 운영 엔진: Jev only, vibe-trading only, both or neither (2026-11-17)
    engine_view.render(get_runtime_root() / 'nhplug', records)
    events = []
    try:
        events = read_events(get_runtime_root() / 'nhplug' / 'shared-etf-paper' / 'events.jsonl', 20000)
    except (OSError, ValueError):
        pass
    import settings_view as sv
    from engine_compare import _follow_mode
    import longterm_account_view as LTV  # a long-term account runs no Jev mirror: its mode is its own (2026-11-18)
    modes = {k: LTV.mode_of(r) if r.get('engine') == 'longterm' else _follow_mode(get_runtime_root() / 'nhplug', k)
             for k, r in records.items() if r.get('kind') == 'live'}
    sv.inject()
    badge = lambda r: ':green-badge[장기투자]' if r.get('engine') == 'longterm' else ':violet-badge[Jev 트레이딩]'
    tabs = st.tabs(['한눈에 보기', *[f"{r.get('label', k)} {badge(r)}" for k, r in records.items()]])
    with tabs[0]:
        sv.overview(records, strategy_settings()[0], modes)
        import overview as ov
        import status_cards as sc
        st.markdown('**장기투자 (vibe · 별도 가상 계좌)**')
        card, go = st.columns([3, 1], vertical_alignment='center')
        sc.card(card, **ov.longterm_card(get_runtime_root() / 'nhplug'))
        go.button('장기투자 탭 열기', key='acc_open_longterm', icon=':material/savings:', width='stretch',
                  on_click=lambda: st.session_state.update(main_tab='장기투자'))
        with st.expander('Jev(단타)와 장기투자 차이', icon=':material/psychology:'):
            from guides import engines_view, values as guide_values
            engines_view(guide_values())
            st.caption('두 엔진은 서로 주문에 관여하지 않습니다. 장기투자는 Jev 자료를 하루 요약으로만 참고합니다. 기간 성과 비교는 "성과 비교" 탭, 장기투자 설정은 "장기투자" 탭에서 봅니다.')
        with st.expander('모의계좌와 실계좌 설정 비교', icon=':material/compare_arrows:'):
            paper_live_table(records)
    for tab, (key, record) in zip(tabs[1:], records.items()):
        live = record['kind'] == 'live'
        with tab:
            if record.get('invalid'):
                st.warning('저장된 설정이 허용 범위를 벗어나 안전한 기본값(자동 OFF)으로 표시합니다. 다시 저장하세요.')
            card, diagram = st.columns([3, 2])
            card.markdown(sv.account_card(key, record, modes.get(key)), unsafe_allow_html=True)
            with diagram:
                sv.flow(record, strategy_settings()[0], modes.get(key))
            longterm = live and record.get('engine') == 'longterm'
            if live:
                live_order_switch(key, record)
                set_tab, run_tab, limit_tab = st.tabs(['금액·포트폴리오·보호 한도', '오늘 주문 계획', '보호 한도 요약'] if longterm else
                                                      ['금액·한도·미러 규칙', '오늘 가상 실행', '제한 규칙'])
            else:
                set_tab, run_tab, limit_tab = st.tabs(['금액·주문 보호 한도', '매매 기준 (진입·익절·손절·빠른 선택)', '제한 규칙'])
            with set_tab:
                account_form(key, record, records)
            with limit_tab:
                restrictions_tab(key, record, events)
            with run_tab:
                if not live:
                    strategy_form(events, prefix='acct_')
                elif longterm:
                    LTV.status_card(key, record, get_runtime_root() / 'nhplug')
                elif not record['auto']:
                    st.caption('관찰 기록이 꺼져 있습니다.')
                else:
                    shadow_view(shadow_result(record, events), record)


def restrictions_tab(key, record, events):
    """제한 규칙: the paper runner's restrictions with switches, or a live account's protections (read-only)."""
    if record.get('engine') == 'longterm' and record['kind'] == 'live':  # 2026-11-18: 장기 전용 보호, no Jev mirror rule
        import longterm_account_view as LTV
        import vibe_longterm
        st.dataframe(LTV.protection_rows(record, record['budget']['capital_krw'], vibe_longterm.load_settings(vibe_longterm.ROOT)),
                     hide_index=True, width='stretch', column_config={'장기 계좌에서': st.column_config.TextColumn(width='large')})
        st.caption('실계좌 보호 한도는 끌 수 없고 "금액·포트폴리오·보호 한도"에서 줄이거나 넓힙니다. 월 매도 한도는 장기투자 → 계좌·실행의 엔진 값입니다.')
        return
    if record['kind'] == 'live':
        from restrictions import live_rules
        st.dataframe(live_rules(record), hide_index=True, width='stretch',
                     column_config={'내용': st.column_config.TextColumn(width='large')})
        st.caption('실계좌 보호 한도는 끌 수 없고, 승인된 최대치 안에서 "금액·한도·미러 규칙"에서 줄이거나 넓힙니다. '
                   '매수·매도 시점 자체는 엔진 체결 원장을 따르므로 모의계좌 제한 규칙(재진입 제한 등)이 미러에도 그대로 반영됩니다.')
        return
    import restrictions_view as rv
    from accounts import paper_limits
    root = get_runtime_root() / 'nhplug' / 'shared-etf-paper'
    settings = strategy_settings()[0]
    rv.render(settings, events, datetime.now(KST).strftime('%Y-%m-%d'),
              lambda switches: write_settings(root, {**settings, **switches}),
              paper_limits(record), record.get('budget'), prefix=f'{key}_')


def live_order_switch(key, record):
    """실주문 스위치: real orders only when the mirror service is armed (--execute), automation is on and this is on.
    Turning on needs the typed phrase; turning off is one click. Every change is audited (accounts.set_live_orders)."""
    from accounts import set_live_orders
    label = record.get('label', key)
    longterm = record.get('engine') == 'longterm'
    if longterm:  # 2026-11-18: the long-term executor is armed by --execute in its launchd registration (not the mirror state)
        import longterm_account_view as LTV
        armed = '--execute' in (LTV.registered_args(f'longterm-{key}') or [])
    else:
        armed = bool((follow_state(key) or {}).get('armed'))
    on, halted = record.get('live_orders') is True, halt_flag_set('nhplug')
    live = armed and on and record.get('auto') and not halted
    service = '장기투자 실행기' if longterm else '미러 서비스'
    with st.container(border=True):
        sc.row([{'label': '실주문 스위치', 'value': '켜짐' if on else '꺼짐', 'icon': 'bolt' if on else 'power_settings_new',
                 'tone': 'up' if on else 'off', 'pulse': on, 'help': '켜면 이 계좌에 실제 주문이 실행됩니다. 끄면 다음 회차(약 1분)부터 계획만 기록합니다.'},
                {'label': service, 'value': '실주문 대기(--execute)' if armed else '계획만 등록' if longterm else '관찰 전용', 'icon': 'dns',
                 'tone': 'ok' if armed else 'warn', 'help': '서비스가 실주문 가능 상태로 등록돼 있어야 스위치가 효과를 냅니다.'},
                {'label': '주문 보호(HALT)', 'value': '켜짐 · 주문 막힘' if halted else '꺼짐', 'icon': 'gpp_bad' if halted else 'verified_user',
                 'tone': 'alert' if halted else 'ok'},
                {'label': '지금 상태', 'value': '실주문 중' if live else '계획만 기록', 'icon': 'radio_button_checked' if live else 'visibility',
                 'tone': 'up' if live else 'warn', 'pulse': live,
                 'help': '실주문은 서비스 실주문 대기 + 자동매매 ON + 실주문 스위치 켜짐 + HALT 꺼짐일 때만 실행됩니다.'}])
        if on:
            st.warning(f'{label}은 실주문 스위치가 켜져 있습니다. 운용금·하루 손실·1주 가격 상한·회당 한도·새 종목 수·미체결 차단은 그대로 적용됩니다.',
                       icon=':material/warning:')
            if st.button('실주문 끄기 (다음 회차부터 계획만 기록)', key=f'live_orders_off_{key}', type='primary', icon=':material/stop_circle:'):
                set_live_orders(key, False)
                st.toast(f'{label} 실주문을 껐습니다.', icon=':material/stop_circle:')
                st.rerun()
        else:
            import disclaimer
            disclaimer.order_note(st)
            phrase = f'{label} 실주문'
            typed = st.text_input(f'실주문을 켜려면 "{phrase}"를 그대로 입력하세요', key=f'live_orders_phrase_{key}',
                                  placeholder=phrase, help='실수로 켜지지 않게 확인 문구를 받습니다. 켜고 끈 기록은 설정 변경 이력(운영 → 운영 상태)에 남습니다.')
            if st.button('실주문 켜기', key=f'live_orders_on_{key}', disabled=typed.strip() != phrase, icon=':material/bolt:'):
                set_live_orders(key, True)
                st.session_state.pop(f'live_orders_phrase_{key}', None)
                st.toast(f'{label} 실주문을 켰습니다. 다음 회차(약 1분)부터 적용됩니다.', icon=':material/bolt:')
                st.rerun()
        if not armed:
            st.caption('장기투자 실행기가 아직 계획만(--execute 없이) 등록돼 있어 스위치를 켜도 실주문은 실행되지 않습니다(재등록은 별도 승인).'
                       if longterm else '미러 서비스가 아직 관찰 전용으로 돌고 있어 스위치를 켜도 실주문은 실행되지 않습니다(서비스 재등록 필요).')


def paper_live_table(records):
    """How the shared paper account and the live accounts differ, with today's saved numbers."""
    import session_times
    _clock = session_times.load(get_runtime_root() / 'nhplug' / 'shared-etf-paper')
    from accounts import live_limits, paper_limits
    from nh_live_shadow import effective_rules
    from settings_view import won as words
    settings = strategy_settings()[0]
    paper = next((r for r in records.values() if r['kind'] != 'live'), {'budget': {}})
    limits = paper_limits(paper)
    from tunables import setting
    lives = [r for r in records.values() if r['kind'] == 'live']
    each = lambda f: ' / '.join(f(r) for r in lives) or '—'
    stop = effective_rules(lives[0])['stop_loss_pct'] if lives else 2.0
    st.dataframe([
        {'항목': '주문 경로', '모의계좌 (공유)': '자동 실행기가 실제 모의 주문', '실계좌': '미러 실행기(nh_live_follow) · 기본은 계획만 기록 (실주문 --execute는 별도 승인)'},
        {'항목': '매수 판단', '모의계좌 (공유)': f"Jev ≥ {settings['entry_confidence']:.2f} + 지수·거래량 확인, 또는 관심종목 강도·신규주",
         '실계좌': '엔진 원장이 산 날, 체결가 + 계좌의 추격 한도 이내(넘어도 엔진 근거가 있고 1차 익절가 아래면 매수)'},
        {'항목': '1회 매수 크기', '모의계좌 (공유)': f"분할 1회 매수 금액 {words(paper['budget'].get('first_buy_krw'))} · " + (f"공유 실행기 일반 주문당 최대 {setting('paper_max_order_qty'):,}주" if setting('paper_max_order_qty') else '공유 실행기 일반 주문 주수 제한 없음'),
         '실계좌': '계좌 설정: 분할 1회 = 1주 또는 정한 금액 (1회 매수 최대 수량 이내)'},
        {'항목': '회당 매수 한도', '모의계좌 (공유)': words(limits['order_max_krw']),
         '실계좌': each(lambda r: words(live_limits(record=r)['order_max_krw']))},
        {'항목': '1주 가격 상한', '모의계좌 (공유)': '없음', '실계좌': each(lambda r: words(r['budget']['order_price_max_krw'])) + ' (최대 200만 원)'},
        {'항목': '총 한도', '모의계좌 (공유)': f"종목당 {words(paper['budget'].get('per_symbol_krw'))} · 하루 매수 {words(limits['daily_buy_krw'])} · "
                                         f"보유 {words(limits['held_max_krw'])} · 하루 새 종목 {limits['daily_new_symbols']}개",
         '실계좌': '운용금 ' + each(lambda r: words(r['budget']['capital_krw'])) + ' · 하루 새 종목 '
                   + each(lambda r: f"{live_limits(record=r)['daily_new_symbols']}개")},
        {'항목': '추가 매수', '모의계좌 (공유)': split_policy_summary(),
         '실계좌': '모의가 분할 매수하면 같이 추가 (모의 규칙 그대로)'},
        {'항목': '익절', '모의계좌 (공유)': '방어형 데이터 목표 / 추세형 절반+추적 / 신규주형 / 약세 시장 +1%·+2%',
         '실계좌': '모의가 파는 만큼 같은 비율로 매도 (모의 청산 계획 그대로)'},
        {'항목': '손절', '모의계좌 (공유)': f"−{settings['stop_loss_pct']:g}% 전량", '실계좌': f"계좌 설정 −{stop:g}% (최대 2%) · 일반 전략 횡보·하락장 시 최대 1% · 낙폭과대 전용 계획 및 계좌 보호"},
        {'항목': 'Jev 매도 의견', '모의계좌 (공유)': f"신뢰도 ≥ {settings['sell_confidence']:.2f} 전량", '실계좌': '모의가 Jev 의견으로 팔면 같이 매도'},
        {'항목': '하루 손실 한도', '모의계좌 (공유)': f"{words(limits['daily_loss_krw'])} 도달 시 신규 매수 중단",
         '실계좌': each(lambda r: words(r['budget']['daily_loss_krw'])) + ' 도달 시 전량 정리 · 그날 매수 중단'},
        {'항목': '장 마감', '모의계좌 (공유)': f"{_clock['cutoff']} 신규 매수 끝 · {_clock['flatten']} 정리 (종가매매·스윙 예외)",
         '실계좌': f"모의의 {_clock['flatten']} 정리와 종가매매·스윙 이월을 그대로 따라감 (15:20까지 동기화)"},
        {'항목': '설정 위치', '모의계좌 (공유)': '이 화면의 "모의계좌 (공유)" 탭', '실계좌': '이 화면의 각 실계좌 탭'},
    ], hide_index=True, width='stretch', height=38 + 35 * 13)
    st.caption('실계좌는 모의(엔진) 원장을 계좌 설정 크기로 미러합니다. 실계좌만의 규칙은 매수 크기·추격 한도·1주 가격 상한·운용금·'
               '회당 매수·하루 새 종목·하루 손실·손절 %이고, 이 한도 때문에 결과 금액은 모의와 다릅니다.')
    st.caption(LIVE_STOP_GUIDE)


def shadow_result(record, events):
    """Today's virtual run of the account's main engine under its own (capped) budget."""
    from nh_live_shadow import account_shadow
    return account_shadow(record, events, datetime.now(KST).strftime('%Y-%m-%d'))


def follow_state(key):
    """The latest live-follow.jsonl record of one live account (nh_live_follow), or None."""
    from engine_compare import latest_follow
    return latest_follow(get_runtime_root() / 'nhplug').get(key)


def sizing_example_view(record, budget, rules, sizing, tranche, limits=None):
    """What this live account buys for one paper buy under each sizing basis, computed with the values on screen
    (1주 가격 상한, 한 번 최대 금액, 회당 매수 한도, 금액 비율), plus a calculator for any price and paper quantity."""
    import nh_live_follow as F
    shown = {**record, 'budget': budget, 'rules': {**rules, 'tranche_krw': tranche}, **({'limits': limits} if limits else {})}
    try:
        ratio, rows = F.sizing_example(shown)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        st.caption(f'예시를 계산하지 못했습니다({type(exc).__name__}).')
        return
    from accounts import live_limits
    order_max = live_limits(record=shown)['order_max_krw']
    cap = int(budget.get('order_price_max_krw', 0))
    follow = rules.get('follow_pct')
    if sizing == 'amount':
        st.metric('금액 비율 (모의가 쓴 돈의 몇 %를 따라 사나)', f'{float(ratio):.1%}',
                  '직접 정함' if follow is not None else '자동 계산: 운용금 ÷ 모의가 하루에 쓰려는 돈', delta_color='off', help=FOLLOW_HELP)
    else:
        st.caption('1주씩: 금액 비율은 쓰지 않습니다. 모의가 몇 주를 사든 실계좌는 1주를 삽니다.')
    st.markdown(f'**이 계좌는 이렇게 따라 삽니다** · 지금 화면의 한도로 계산했습니다: 1주 가격 상한 **{krw_words(cap)}**, '
                f'회당 매수 한도 **{krw_words(order_max)}**' + (f', 한 번에 따라 살 최대 금액 **{krw_words(tranche)}**, 금액 비율 **{float(ratio):.1%}**'
                                                              if sizing == 'amount' else '') + '.')
    now = {'amount': '금액 비율이면', 'share': '1주씩이면'}
    head = ' | '.join(now[k] + (' ◀ 지금 설정' if k == sizing else '') for k in ('amount', 'share'))
    lines = [f'| 종목 1주 가격 | 모의가 산 수량 | {head} |', '|---|---|---|---|']
    for r in rows:
        cell = lambda n, why, price=r['price']: (f'{n}주 (약 {krw_words(n * price)}) · {why}' if n else f'안 삼 · {why}')  # noqa: E731
        lines.append(f"| {krw_words(r['price'])} | {r['paper_qty']:,}주 ({krw_words(r['paper_krw'])}어치) | "
                     f"{cell(r['amount'], r['amount_why'])} | {cell(r['share'], r['share_why'])} |")
    st.markdown('\n'.join(lines))
    with st.container(border=True):
        st.markdown('**직접 계산해 보기** · 종목 1주 가격과 모의가 산 수량을 넣으면 이 설정으로 실계좌가 몇 주를 사는지 보여 줍니다.')
        c1, c2 = st.columns(2)
        price = int(c1.number_input('종목 1주 가격 (원)', 100, 10_000_000, 226_000, step=1_000, key=f"calc_price_{record.get('hash', '')[:8]}"))
        qty = int(c2.number_input('모의가 산 수량 (주)', 1, 100_000, 2, key=f"calc_qty_{record.get('hash', '')[:8]}"))
        quote = F.follow_quote(shown, price, qty)
        n, why = quote[sizing]
        st.markdown(f"→ 지금 설정({now[sizing][:-2]})이면 실계좌는 **{n}주**" + (f" (약 {krw_words(n * price)})" if n else '') + f" · {why}")
        other = 'share' if sizing == 'amount' else 'amount'
        st.caption(f"{now[other][:-2]}였다면 {quote[other][0]}주 · {quote[other][1]}")
    st.caption('· 1주가 안 되는 몫이어도 1주는 삽니다(1주 가격 상한·한 번 최대 금액 안일 때).  \n'
               '· 모의 수량만큼 사려면 금액 비율을 고르고 비율을 100%로 정하세요. 1주씩은 몇 주든 1주입니다.  \n'
               '· 운용금과 하루 손실 한도는 주문할 때 가드가 한 번 더 확인합니다.')


def follow_mode_view(key, prefix=''):
    """Live-order mode of the paper-following runner; switching it on is a separate user approval, so the toggle stays disabled."""
    event = follow_state(key)
    stamp = event_time(event.get('time')) if event else None
    executing = bool(event and event.get('mode') == 'execute' and stamp and (datetime.now(KST) - stamp).total_seconds() < 180)
    # status only: the switch itself is live_order_switch at the top of the account tab (a greyed toggle here read as "cannot turn on")
    st.caption(f"미러 실행기 최근 3분: {'실주문으로 실행 중' if executing else '계획만 기록'} · 켜고 끄기는 이 계좌 탭 맨 위 '실주문 스위치'에서 합니다.")
    try:
        from accounts import load as load_accounts
        from nh_live_follow import mirror_target, paper_book, paper_source
        engine = load_accounts().get(key, {}).get('engine')
        source = paper_source(get_runtime_root() / 'nhplug', engine)
        if source is None or not source.exists():
            st.caption('미러 목표: Jev 모의 실행기 원장이 없어 새로 사지 않습니다 (이미 산 종목은 손절·손실 한도로만 관리).')
        else:
            from nh_live_follow import tranche_shares
            book = paper_book(source, tranche_shares(load_accounts().get(key, {}), root=get_runtime_root() / 'nhplug', source=source))
            pairs = [f"{code} 모의 {book[code]['qty']}주 → 실계좌 {shares}주" for code, shares in mirror_target(book).items()]
            st.caption('미러 목표: ' + (' · '.join(pairs) if pairs else '모의 보유 없음 → 실계좌 0주'))
    except (ImportError, OSError, ValueError, KeyError, sqlite3.Error):
        st.caption('미러 목표: 모의 체결 원장을 읽지 못했습니다.')
    if not event:
        st.caption('추종 실행기 기록 없음 · 실주문 꺼짐')
        return
    mode = '실주문' if event.get('mode') == 'execute' else '계획만 기록(dry-run)'
    detail = ' · '.join(LL.reason(event[k]) if k == 'reason' else str(event[k])
                        for k in ('action', 'symbol', 'reason', 'execution') if event.get(k))
    st.caption(f"추종 실행기 마지막 기록 {stamp:%m/%d %H:%M} · {mode} · {detail}" if stamp else f'추종 실행기 · {mode} · {detail}')


def shadow_position_rows(result, names):
    """Show each estimated account position alongside its latest estimated sale today."""
    def sale_outcome(trade):
        pnl = trade.get('pnl')
        return ('손익 미확인' if type(pnl) not in (int, float) or not math.isfinite(pnl) else
                '익절' if pnl > 0 else '본전 매도' if pnl == 0 else
                '손절' if '손절' in str(trade.get('reason') or '') else '손실 매도')

    grouped = {code: {'position': position, 'sales': []} for code, position in result['open'].items()}
    for trade in result['trades']:
        if trade.get('side') != 'SELL' or not trade.get('symbol'):
            continue
        group = grouped.setdefault(trade['symbol'], {'position': None, 'sales': []})
        group['sales'].append(trade)
    rows = []
    for code, group in grouped.items():
        position = group['position']
        sales = group['sales']
        latest = max(sales, key=lambda t: event_time(t.get('time')) or datetime.min.replace(tzinfo=KST)) if sales else None
        status = '보유 중' if position else '미보유'
        sale_text = '없음'
        summary = '없음'
        if latest:
            label = sale_outcome(latest)
            stamp = event_time(latest.get('time'))
            at = stamp.astimezone(KST).strftime('%H:%M') if stamp else '시각 미확인'
            suffix = f' 외 {len(sales) - 1}건' if len(sales) > 1 else ''
            sale_text = f"{label} {latest.get('qty', 0):,}주 · {at}{suffix}"
            counts = {}
            for sale in sales:
                outcome = sale_outcome(sale)
                counts[outcome] = counts.get(outcome, 0) + 1
            summary = ' · '.join(f'{outcome} {count}건' for outcome, count in counts.items())
        last = position.get('last') if position else None
        rows.append({'종목': f'{names.get(code, code)} ({code})', '가상 보유': status,
                     '수량': f"{position['qty']:,}주" if position else '—',
                     '기준 종가': ('—' if not position else f'{last:,}원' if type(last) in (int, float)
                               and math.isfinite(last) and last > 0 else '미확인'),
                     '최근 가상 매도': sale_text, '오늘 가상 매도 요약': summary})
    return rows


def shadow_positions_view(result):
    st.markdown('##### 계좌별 가상 미러 현황')
    st.caption('엔진 원장을 계좌 설정으로 재현한 추정입니다. 실계좌 잔고·주문·체결이 아니며 수수료·세금은 반영하지 않습니다. '
               '기준 종가는 공유 실행기의 저장 분봉으로, 누락 시 가상 손절·평가손익을 확인할 수 없습니다. 모의가 다 팔면 실계좌도 다 팔고, 실계좌에만 있는 보유는 Jev 보유 판단을 받습니다. '
               '수동 매매·계획·시황별 손절·하루 손실 정지는 미러와 같은 계산이고, 추격 한도·회당 한도·하루 새 종목 수·개장 규칙은 재현하지 않습니다.')
    names = stock_names()
    rows = shadow_position_rows(result, names)
    if rows:
        st.dataframe(rows, hide_index=True, width='stretch')
    else:
        st.caption('가상 보유와 오늘 가상 매도 내역이 없습니다.')


def side_table(rows, formats, side='구분'):
    """A trade/order table like the overview's: amounts with commas, the 구분 cell 매수 red / 매도 blue, and only the signed
    P&L / % cells colored (profit red, loss blue)."""
    frame = pd.DataFrame(rows)
    for name, fmt in formats.items():  # formatted here: st.dataframe shows a Styler's missing values as "None"
        if name in frame:
            frame[name] = [fmt.format(v) if type(v) in (int, float) and math.isfinite(v) else '' for v in frame[name]]
    frame = frame.fillna('')
    signed = [name for name, fmt in formats.items() if name in frame and fmt.startswith('{:+')]
    style = frame.style.map(sc.cell_tone, subset=signed) if signed else frame.style
    st.dataframe(style.map(sc.side_tone, subset=[side]) if side in frame else style, hide_index=True, width='stretch')


def signed_pct(pnl, cost):
    return pnl / cost * 100 if cost else None


def live_order_times(key, day):
    """{broker order number: 'HH:MM:SS'} of one live account's orders sent today (live_fills.order_times: NH's live
    order list has no time, so the executors' submission records are joined to the guard's intents)."""
    from live_fills import order_times
    return {number: stamp[11:19] for number, stamp in order_times(get_runtime_root() / 'nhplug', key, day).items()}


def with_broker_fills(recorded, snapshot, day):
    """The mirror's recorded fills plus today's broker fills it has not stored yet: it records once a minute, so a sale
    shown in the broker list would have no P&L until its next cycle (11/17 15:02 sell, recorded 15:03). The broker's
    filled quantity wins for today's orders (a later partial fill)."""
    rows = {(r['time'][:10], r.get('order_no')): r for r in recorded}
    for o in (snapshot or {}).get('orders') or []:
        side = 'BUY' if '매수' in str(o.get('구분')) else 'SELL' if '매도' in str(o.get('구분')) else None
        n, price, number = o.get('체결수량'), o.get('평균체결가'), o.get('주문번호')
        if side and type(number) is int and type(n) in (int, float) and n > 0 and type(price) in (int, float) and o.get('종목코드'):
            old = rows.get((day, number))
            rows[(day, number)] = {'time': old['time'] if old else f'{day}T00:00:00+09:00', 'symbol': o['종목코드'],
                                   'side': side, 'qty': int(n), 'price': float(price), 'order_no': number}
    return sorted(rows.values(), key=lambda r: (r['time'], r.get('order_no') or 0))


def sell_results(filled, books=None, day=None):
    """{order number or id: (P&L, % of cost)} of today's filled sells. With `books` (the live mirror's recorded fills),
    each symbol's sales of `day` follow its sells in order-number order (NH's live list has no time); without them the
    day's own buys give a running average (a sale of carried shares beyond them stays unknown)."""
    out = {}
    order_key = lambda o: (str(o.get('주문 시각') or '').replace(':', ''), o.get('주문번호') or 0)
    ident = lambda o: o.get('주문번호') or id(o)
    for code in {o.get('종목코드') for o in filled}:
        rows = sorted((o for o in filled if o.get('종목코드') == code), key=order_key)
        sells = [o for o in rows if '매도' in str(o.get('구분'))]
        if books is not None and day:
            sales = [(pnl, cost) for t, pnl, cost in (books.get(code) or {}).get('sales', []) if t.startswith(day)]
            if len(sales) == len(sells):  # one sale per order; a ledger with partial-fill rows falls back below
                for order, (pnl, cost) in zip(sorted(sells, key=lambda o: o.get('주문번호') or 0), sales):
                    out[ident(order)] = (pnl, signed_pct(pnl, cost))
                continue
        qty = avg = 0.0
        for o in rows:
            n, price = o['체결수량'], o['평균체결가']
            if '매수' in str(o.get('구분')):
                avg, qty = (avg * qty + price * n) / (qty + n), qty + n
            elif qty >= n > 0:
                pnl = (price - avg) * n
                out[ident(o)] = (pnl, signed_pct(pnl, avg * n))
                qty -= n
            else:
                qty = 0.0  # carried shares: their cost is unknown here
    return out


def orders_view(snapshot, books=None, day=None):
    """Today's broker orders like the virtual run: totals, buy/sell amounts, realized (from recorded fills), evaluation and
    holdings, then the order table with amounts and buy/sell colors."""
    orders = snapshot['orders']
    filled = [o for o in orders if type(o.get('체결수량')) in (int, float) and o['체결수량'] > 0
              and type(o.get('평균체결가')) in (int, float)]
    amount = lambda word: sum(o['체결수량'] * o['평균체결가'] for o in filled if word in str(o.get('구분')))
    bought, sold = amount('매수'), amount('매도')
    cards = [{'label': '오늘 매매', 'value': f'{len(filled)}건', 'tone': 'ai' if filled else 'off', 'icon': 'receipt_long',
              'sub': f"매수 {sum('매수' in str(o.get('구분')) for o in filled)} · 매도 {sum('매도' in str(o.get('구분')) for o in filled)}"
                     f" · 주문 {len(orders)}건", 'help': '증권사에 조회된 오늘 주문 중 체결된 건수입니다(부분 체결 포함).'},
             {'label': '매수 · 매도 금액', 'value': f'{bought:,.0f}원', 'tone': 'up' if bought else 'off', 'icon': 'swap_vert',
              'sub': f'매도 {sold:,.0f}원', 'help': '체결 수량 × 평균 체결가의 합입니다. 수수료·세금은 빠져 있습니다.'}]
    if books is not None and day:
        realized = sum(b['today'] for b in books.values())
        cost = sum(c for b in books.values() for t, _, c in b['sales'] if t.startswith(day))
        pct = signed_pct(realized, cost)
        cards.append({'label': '오늘 실현손익', 'value': f'{realized:+,.0f}원', 'tone': sc.signed(realized),
                      'icon': 'trending_up' if realized > 0 else 'trending_down' if realized < 0 else 'trending_flat',
                      'sub': f'매입 대비 {pct:+.2f}%' if pct is not None else '오늘 매도 없음',
                      'help': '실행기가 기록한 이 계좌의 증권사 체결로 계산한 오늘 판 주식의 손익입니다. 수수료·세금은 빠져 있습니다.'})
    aggregate = snapshot.get('aggregate') or {}
    pnl, rate = aggregate.get('평가손익'), aggregate.get('수익률(%)')
    cards.append({'label': '평가손익', 'value': f'{pnl:+,.0f}원' if pnl is not None else '확인 불가',
                  'tone': sc.signed(pnl) if pnl is not None else 'alert',
                  'icon': ('trending_up' if pnl > 0 else 'trending_down' if pnl < 0 else 'trending_flat') if pnl is not None else 'error',
                  'sub': f'수익률 {rate:+.2f}%' if rate is not None else None, 'help': '아직 팔지 않은 보유의 증권사 평가손익입니다.'})
    holdings = snapshot.get('holdings') or []
    cards.append({'label': '보유', 'value': f'{len(holdings)}종', 'tone': 'up' if holdings else 'off', 'icon': 'inventory_2',
                  'sub': ', '.join(f"{h.get('종목') or h.get('종목코드')} {h.get('보유수량'):,}주" for h in holdings) or None,
                  'help': '증권사 잔고 기준 보유 종목입니다.'})
    sc.row(cards, ask='live')
    if not orders:
        st.caption('증권사에 조회된 당일 주문이 없습니다.')
        return
    timed = any(o.get('주문 시각') for o in orders)  # the live order query may not return a time
    clock = lambda o: str(o.get('주문 시각') or '').replace(':', '')
    orders = sorted(orders, key=lambda o: (clock(o), o.get('주문번호') or 0), reverse=True)  # the latest first
    shown = lambda o: (lambda t: f'{t[:2]}:{t[2:4]}:{t[4:6]}' if len(t) >= 6 and t[:6].isdigit() else t)(clock(o))
    results = sell_results(filled, books, day)
    result = lambda o: results.get(o.get('주문번호') or id(o), (None, None)) if o in filled else (None, None)
    side_table([{**({'시각': shown(o)} if timed else {}), '구분': o.get('구분'), '종목': o.get('종목') or o.get('종목코드'),
                 '주문수량': o.get('주문수량'), '체결수량': o.get('체결수량'), '미체결': o.get('미체결수량'),
                 '평균체결가': o.get('평균체결가'),
                 '체결금액': (o['체결수량'] * o['평균체결가']) if o in filled else None,
                 '수익률': result(o)[1], '실현손익': result(o)[0],
                 '취소': o.get('취소수량'), '거부 사유': o.get('거부 사유')} for o in orders],
               {'주문수량': '{:,.0f}', '체결수량': '{:,.0f}', '미체결': '{:,.0f}', '취소': '{:,.0f}',
                '평균체결가': '{:,.0f}', '체결금액': '{:,.0f}', '수익률': '{:+.2f}%', '실현손익': '{:+,.0f}원'})


def shadow_view(result, record=None):
    trades = result['trades']
    unrealized = (sum(o['unrealized'] for o in result['open'].values())
                  if all(o.get('unrealized') is not None for o in result['open'].values()) else None)
    held = ', '.join(f"{stock_names().get(c, c)} {o['qty']}주"
                     + (f" ({'추세형' if o.get('profile') == 'trend' else '방어형'})" if o.get('profile') else '')
                     for c, o in result['open'].items())
    sc.row([{'label': '오늘 가상 매매', 'value': f'{len(trades)}건', 'tone': 'ai' if trades else 'off', 'icon': 'science',
             'sub': f"매수 {sum(t['side'] == 'BUY' for t in trades)} · 매도 {sum(t['side'] == 'SELL' for t in trades)}",
             'help': '메인 엔진 판단을 이 계좌 한도로 실행했다고 가정한 매수·매도 건수입니다. 실주문이 아닙니다.'},
            {'label': '가상 실현손익', 'value': f"{result['realized']:+,}원", 'tone': sc.signed(result['realized']),
             'icon': 'trending_up' if result['realized'] > 0 else 'trending_down' if result['realized'] < 0 else 'trending_flat',
             'help': '판 종목의 손익입니다. 분봉 종가에 체결됐다고 보고 계산하며 호가·수수료·세금은 빠져 있습니다.'},
            {'label': '가상 평가손익', 'value': f'{unrealized:+,}원' if unrealized is not None else '확인 불가',
             'tone': sc.signed(unrealized) if unrealized is not None else 'alert',
             'icon': ('trending_up' if unrealized > 0 else 'trending_down' if unrealized < 0 else 'trending_flat')
                     if unrealized is not None else 'error',
             'help': '아직 팔지 않은 가상 보유의 마지막 분봉 기준 손익입니다.'},
            {'label': '가상 보유', 'value': f"{len(result['open'])}종", 'tone': 'up' if result['open'] else 'off', 'icon': 'inventory_2',
             'sub': held or None, 'help': '가상 매매에서 아직 팔지 않은 종목과 청산 방식(방어형·추세형)입니다.'}])
    buys = sum(t['qty'] * t['price'] for t in trades if t['side'] == 'BUY')
    sold = [t for t in trades if t['side'] == 'SELL' and t.get('pnl') is not None]
    sold_cost = sum(t['qty'] * t['price'] - t['pnl'] for t in sold)
    rate = signed_pct(sum(t['pnl'] for t in sold), sold_cost)
    if trades:
        sc.row([{'label': '가상 매수 금액', 'value': f'{buys:,.0f}원', 'tone': 'up' if buys else 'off', 'icon': 'shopping_cart',
                 'help': '가상 매수의 수량 × 가격 합입니다.'},
                {'label': '가상 매도 금액', 'value': f"{sum(t['qty'] * t['price'] for t in trades if t['side'] == 'SELL'):,.0f}원",
                 'tone': 'down' if any(t['side'] == 'SELL' for t in trades) else 'off', 'icon': 'sell',
                 'help': '가상 매도의 수량 × 가격 합입니다.'},
                {'label': '실현 수익률', 'value': f'{rate:+.2f}%' if rate is not None else '매도 없음',
                 'tone': sc.signed(rate) if rate is not None else 'off', 'icon': 'percent',
                 'sub': f'판 주식 매입 {sold_cost:,.0f}원 대비' if rate is not None else None,
                 'help': '가상 실현손익을 판 주식의 매입금액으로 나눈 값입니다. 수수료·세금은 빠져 있습니다.'}])
    sells = [t for t in trades if t['side'] == 'SELL']
    if sells:  # a line needs realized results; buys alone would draw a flat 0 line
        frame = pd.DataFrame(sells)
        frame['누적손익'] = frame['pnl'].fillna(0).cumsum()
        frame['시각'] = pd.to_datetime(frame['time'], format='ISO8601')
        st.altair_chart(alt.Chart(frame).mark_line(point=True, color='#a855f7').encode(
            x=alt.X('시각:T', title=None, axis=alt.Axis(format='%H:%M')),
            y=alt.Y('누적손익:Q', title='누적 가상 실현손익(원)', axis=alt.Axis(format=',.0f')),
            tooltip=[alt.Tooltip('시각:T', format='%H:%M'), 'symbol', 'price', 'reason', alt.Tooltip('누적손익:Q', format=',.0f')]
        ).properties(height=180), width='stretch')
    if trades:
        with st.expander(f'가상 매매 내역 ({len(trades)}건)', expanded=True):
            names = stock_names()
            side_table([{'시각': t['time'][11:16], '구분': '매수' if t['side'] == 'BUY' else '매도', '종목': names.get(t['symbol'], t['symbol']),
                         '수량': t['qty'], '가격': t['price'], '금액': t['qty'] * t['price'],
                         '손익': t.get('pnl'),
                         '수익률': signed_pct(t['pnl'], t['qty'] * t['price'] - t['pnl']) if t.get('pnl') is not None else None,
                         '사유': t['reason']} for t in sorted(trades, key=lambda t: t['time'], reverse=True)],  # the latest first
                       {'수량': '{:,.0f}', '가격': '{:,.0f}', '금액': '{:,.0f}', '손익': '{:+,.0f}', '수익률': '{:+.2f}%'})
    if result['skipped_entries']:
        st.caption('한도로 건너뛴 진입: ' + ', '.join(f'{k} {v}건' for k, v in result['skipped_entries'].items()))
    st.caption(('엔진 원장을 미러와 같은 손절(계획·시황별)·하루 손실 정지로 재현한 가상 추정입니다. 수수료·세금은 반영하지 않습니다.'
                if result['basis'].startswith('engine ledger') else '분봉 종가와 계좌 기본 손절을 바탕으로 한 가상 추정입니다. 시황별 1% 손절, 호가·수수료·세금은 반영하지 않습니다.')
               + ' 실주문 전환은 별도 승인과 보호 경로 검증이 필요합니다.')
    return trades


def virtual_markers(trades, bars, symbol, *, minute=True):
    """Account replay estimates, not broker fills, aligned to an available chart bar."""
    available = [bar['time'] for bar in bars]
    markers = []
    for trade in trades:
        stamp = event_time(trade.get('time'))
        side = trade.get('side')
        if not stamp or trade.get('symbol') != symbol or side not in ('BUY', 'SELL'):
            continue
        stamp_minute = int(stamp.replace(second=0, microsecond=0).timestamp())
        position = (min((t for t in available if t >= stamp_minute), default=None) if minute else
                    max((t for t in available if t <= stamp_minute), default=None))
        if position is not None:
            buy = side == 'BUY'
            suffix = f" ({stamp.astimezone(KST):%H:%M})" if minute and position != stamp_minute else ''
            markers.append({'time': position, 'position': 'belowBar' if buy else 'aboveBar',
                            'color': '#f59e0b' if buy else '#d946ef', 'shape': 'circle',
                            'text': f"가상 재현 {'매수' if buy else '매도'}{suffix}"})
    return markers


def shadow_chart_symbols(charts, shadow, names):
    """One code per option with account-replay holdings and most recent sale."""
    by_code = {}
    for label, (group, code) in charts.items():
        by_code.setdefault(code, (group, label))
    status = {}
    for code, row in (shadow.get('open') or {}).items():
        by_code.setdefault(code, (code, f'{names.get(code, code)} ({code})'))
        status.setdefault(code, {})['qty'] = row.get('qty')
    for row in shadow.get('trades') or []:
        code = row.get('symbol')
        if not code or row.get('side') not in ('BUY', 'SELL'):
            continue
        by_code.setdefault(code, (code, f'{names.get(code, code)} ({code})'))
        if row['side'] == 'SELL':
            stamp = event_time(row.get('time'))
            entry = status.setdefault(code, {})
            if stamp and (entry.get('time') is None or stamp > entry['time']):
                pnl = row.get('pnl')
                entry.update(time=stamp, outcome=('익절' if type(pnl) in (int, float) and pnl > 0 else
                            ('손절' if '손절' in str(row.get('reason') or '') else '손실 매도')
                            if type(pnl) in (int, float) and pnl < 0 else
                            '본전 매도' if pnl == 0 else '매도'))
    return by_code, status


def live_levels(record):
    """The live account's own stop % (nh_live_shadow uses the same); its take-profits follow the engine ledger's plans."""
    from nh_live_shadow import effective_rules
    return {'stop_loss_pct': effective_rules(record)['stop_loss_pct']}


def live_rules_card(record):
    """The live account's limits and how it follows its engine's ledger (the live mirror), in one glance."""
    from accounts import live_rules
    from nh_live_shadow import effective_rules
    budget, stop, rules = record['budget'], effective_rules(record)['stop_loss_pct'], live_rules(record)
    from accounts import ENGINE_LABELS
    engine = ENGINE_LABELS.get(record['engine'], record['engine'])
    size = ('**모의 분할 1회 = 1주**, 1회 최대 ' + f"{rules['max_order_qty']:,}주" if rules['sizing'] == 'share' else
            f"**모의 체결 금액 × 운용금/(모의 분할 1회 × 3분할 × 하루 새 종목), 체결 1건당 최대 {rules['tranche_krw']:,}원** (주수 제한 없음, 1주 미만이면 1주)")
    chase = f"체결가 +{rules['chase_pct']:g}% 이내" + (' (넘어도 엔진 근거 + 1차 익절가 아래면 매수)' if rules['evidence_chase'] else '')
    with st.container(border=True):
        buy, sell = st.columns(2)
        buy.markdown(f"**매수** ({engine} 원장 미러)  \n엔진 원장이 산 날 {chase} → {size}  \n"
                     f"1주 가격 **{budget['order_price_max_krw']:,}원 이하** · 운용금 **{budget['capital_krw']:,}원** 이내 · 하루 신규 5종목")
        sell.markdown(f"**매도**  \n엔진 원장이 파는 만큼 같은 비율로 (익절·청산 계획·장 마감 정리·종가매매 이월 모두 모의 그대로)  \n"
                      f":blue[▼ 계좌 손절 설정 **−{stop:g}%**] · 계좌 하루 손실 **{budget['daily_loss_krw']:,}원** 도달 시 전량·그날 매수 중단")
        st.caption(LIVE_STOP_GUIDE)
        st.caption('모의 전략·학습이 바뀌면 실계좌도 그대로 따라갑니다 · 손절은 하루 손실과 종목 손절 % 중 먼저 닿는 기준 · '
                   '지금은 관찰 모드라 계획만 기록합니다 · 금액과 손절 %는 아래 "계좌 설정" 탭')


def live_account_view(read, key, record, number, configured, events):
    """A live account on the same layout as the paper desk: status, rules, cards, P&L, chart and detail tabs. Read-only."""
    from accounts import ENGINE_LABELS
    engine = ENGINE_LABELS.get(record['engine'], record['engine'])
    heading, toggle = st.columns([3, 1], vertical_alignment='center')
    heading.subheader(f"{record.get('label', key)} {record.get('masked', '')}")
    auto = auto_on()  # the header's one switch (user 2026-11-17)
    toggle.caption(f'자동 갱신 켜짐 · {refresh_s()}초 (장중 09:00~15:30)' if auto else '자동 갱신 꺼짐 · 상단에서 켭니다',
                   help=f'{refresh_s()}초 간격 REST 조회입니다. 실시간 틱이나 체결 스트림이 아닙니다.')
    if number is None:
        st.error('증권사 연결 계좌 목록에서 이 계좌를 찾지 못했습니다. 잔고와 주문을 확인할 수 없습니다.')
        return

    @st.fragment(run_every=live_every())
    def desk():
        line, button = st.columns([4, 1], vertical_alignment='center')
        if record.get('engine') == 'longterm':  # user 2026-11-17: the long-term engine's day plan, not the Jev mirror
            import longterm_account_view
            with line:
                longterm_account_view.status_card(key, record, get_runtime_root() / 'nhplug')
        else:
            mirror = follow_state(key)
            executing = bool(mirror and mirror.get('mode') == 'execute')
            parts = [(':red[●] 미러 실주문' if executing else ':orange[●] 미러 관찰(dry-run, 실주문 없음)'), f'따라가는 엔진 {engine}',
                     f"미러 실행기 {processes().get(f'실계좌 {key[-1]} 미러 실행기', '확인 불가')}"]
            if mirror:
                parts.append(f"마지막 계획 {LL.clock(mirror.get('time'), f'{datetime.now(KST):%Y-%m-%d}')} {LL.ACTIONS.get(mirror.get('action'), mirror.get('action', ''))}"
                             + (f" {stock_names().get(mirror.get('symbol'), mirror.get('symbol'))}" if mirror.get('symbol') else ''))
            line.markdown(' · '.join(parts) + f' · 조회 {datetime.now(KST):%H:%M:%S}',
                          help='미러 실행기는 엔진 원장의 체결을 이 계좌 크기로 따라갑니다. dry-run이면 계획만 기록하고 주문하지 않습니다.')
        refresh = button.button('계좌 현황 새로고침', key=f'live_refresh_{key}', width='stretch',
                                help='증권사에서 이 계좌의 잔고와 당일 주문을 다시 조회합니다(조회 전용).')
        state = f'live_snapshot_{key}'
        kept = st.session_state.get(state)
        # with auto refresh off, a kept snapshot went stale after a sale (capital used still showed the sold position)
        if refresh or auto or snapshot_due(kept, datetime.now(KST)):
            try:
                st.session_state[state] = shared_snapshot('live', number, lambda: live_broker_snapshot(read, number, datetime.now(KST)), refresh)
            except Exception:
                st.session_state[state] = None
        snapshot = st.session_state[state]
        if snapshot is not None:  # NH's live order list has no time: fill it from the mirror's own submission record
            times = live_order_times(key, datetime.now(KST).strftime('%Y-%m-%d'))
            for order in snapshot['orders']:
                if not order.get('주문 시각') and order.get('주문번호') in times:
                    order['주문 시각'] = times[order['주문번호']]
        if snapshot is None:
            st.error('증권사 잔고 또는 당일 주문을 확인하지 못했습니다. 빈 계좌나 주문 없음으로 간주하지 마세요.')
        live_rules_card(record)
        now = datetime.now(KST)
        fills = order_fills(snapshot['orders'], now.date()) if snapshot else []
        if snapshot and snapshot.get('aggregate'):
            used, capital = snapshot['aggregate']['평가금액'], record['budget']['capital_krw']
            st.progress(min(1.0, used / capital), text=f'운용금 사용 {used:,.0f}원 / {capital:,}원 ({used / capital:.0%})')
        levels = live_levels(record)
        from live_fills import rows as live_fill_rows
        recorded = with_broker_fills(live_fill_rows(get_runtime_root() / 'nhplug', key), snapshot, now.strftime('%Y-%m-%d'))
        books = books_from_rows([(r['time'], r['symbol'], r['side'], r['qty'], r['price']) for r in recorded], now.strftime('%Y-%m-%d'))
        portfolio_overview(snapshot, events, fills, now, books=books, key_prefix=f'live_{key}_',
                           sell=lambda: live_quick_sell(number, key, record, f'live_top_{key}'),
                           live_stop_pct=levels['stop_loss_pct'], engine=record['engine'])
        st.caption('위 손절 가격은 계좌 설정과 확인된 시황·전략을 반영한 참고값입니다. 미러 관리 대상 여부와 본전·추적 청산은 별도 확인합니다.')
        st.caption(f"실현손익·수익률은 미러 실행기가 기록한 이 계좌의 증권사 체결(현재 {len(recorded)}건, 2026-11-13부터 기록) 기준입니다. "
                   '기록 전에 산 보유분을 팔면 그 부분은 매입가를 몰라 실현손익에서 뺍니다. 관찰(dry-run) 중에는 실체결이 없습니다.')
        from nh_live_follow import paper_source
        try:
            source = paper_source(get_runtime_root() / 'nhplug', record.get('engine'))
            result = shadow_result(record, events) if record['auto'] and source and source.is_file() else None
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
            result = None
        if result is not None:
            shadow_positions_view(result)
        elif record.get('engine') == 'longterm':
            st.caption('장기투자 계좌는 Jev 미러가 아니어서 가상 미러 보유를 표시하지 않습니다. 오늘 계획은 위 장기투자 카드에 있습니다.')
        elif record['auto']:
            st.warning('계좌별 가상 미러 원장을 확인하지 못했습니다. 가상 보유·매도 내역은 확인 불가입니다.')
        else:
            st.caption('계좌별 가상 미러 관찰이 꺼져 있어 가상 보유·매도를 표시하지 않습니다.')
        paper_trade_chart(read, snapshot, refresh, auto, fills=fills, key=f'live_{key}', levels=live_levels(record),
                          shadow=result, engine=record['engine'])
        st.caption('주황 원: 가상 매수 · 자주 원: 가상 매도(실주문 아님) · 초록/빨강 화살표: 증권사 체결(주문 시각 기준) · '
                   '자세한 읽는 법은 오른쪽 위 "화면 안내"')
        virtual, orders_tab, sell_tab, runner_tab, settings_tab = st.tabs(['메인 엔진 가상 실행', '당일 주문·체결', '즉시 매도',
                                                                            '실행기 기록', '계좌 설정'])
        with sell_tab:
            live_quick_sell(number, key, record, f'live_quick_{key}')
        with virtual:
            if result is not None:
                shadow_view(result, record)
            elif record['auto']:
                st.caption('엔진 원장을 읽지 못해 가상 미러 결과를 확인할 수 없습니다.')
            else:
                st.caption('관찰 기록이 꺼져 있습니다. "계좌 설정" 탭에서 켤 수 있습니다.')
        with orders_tab:
            if snapshot:
                orders_view(snapshot, books, now.strftime('%Y-%m-%d'))
        with runner_tab:
            st.caption('미러 실행기(nh_live_follow) 최근 기록입니다. 매분 엔진 원장과 증권사 잔고를 비교해 계획을 남깁니다.')
            from engine_compare import recent_events as journal_tail
            recent = [e for e in journal_tail(get_runtime_root() / 'nhplug/live-follow.jsonl', limit=400) if e.get('account') == key][-30:]
            if recent:
                st.dataframe([{k: str(e.get(k, '')) for k in ('time', 'mode', 'action', 'symbol', 'qty', 'price', 'reason', 'execution')}
                              for e in reversed(recent)], hide_index=True, width='stretch')
            else:
                st.caption('실행 기록이 없거나 읽을 수 없습니다.')
        with settings_tab:
            from accounts import load
            records = load()
            account_form(key, records[key], records, prefix='desk_')

    desk()


ACCOUNT_HELP = {'capital_krw': ' · 이 계좌가 주식 매입에 쓸 수 있는 최대 금액입니다. 실계좌 잔고(총자산) 안에서 정하며, 매수 직전 증권사 현금·총자산을 다시 확인합니다.',
                'order_price_max_krw': ' · 이 가격을 넘는 종목은 사지 않습니다. 실주문 경로의 매수에도 같은 값이 적용되며(이미 산 종목은 이 가격을 넘어도 팔 수 있습니다), 운용금 한도 때문에 실제로는 운용금보다 비싼 1주는 살 수 없습니다.'}
ACCOUNT_LABELS = {'first_buy_krw': '분할 1회 매수 금액(원)', 'per_symbol_krw': '종목당 최대(원)', 'capital_krw': '운용금(원)',
                  'order_price_max_krw': '1주 가격 상한(원)', 'daily_loss_krw': '하루 손실 한도(원)'}


MONEY_UNITS = {'원': 1, '만원': 10_000, '10만원': 100_000, '100만원': 1_000_000, '1000만원': 10_000_000, '억원': 100_000_000}


def unit_range(low, high, size):
    """Whole-number bounds of [low, high] KRW in a unit of `size` KRW, or None when no whole unit fits."""
    first, last = -(-low // size), high // size
    return (first, last) if first <= last else None


def synced_number(container, label, low, high, current, key, slider, step=1, fmt=None, help=None, on_value=None, show=None):
    """A number as a box, or a slider with a box under it for typing the exact value; both stay on one value.
    `current` is used only the first time; returns the value. The box has no min/max of its own (Streamlit would show
    an English range error): a typed value outside [low, high] is brought into range with a Korean note under it.
    `show(v)` words a value for that note (default: the number with commas)."""
    box, bar, note = f'{key}_n', f'{key}_s', f'{key}_note'
    for name in (box, bar):
        if name not in st.session_state:
            st.session_state[name] = current
    words = show or (lambda v: f'{v:,}' if isinstance(v, int) else f'{v:,.2f}'.rstrip('0').rstrip('.'))

    # A unit change pops this field's keys in its own callback; a box/slider callback of the old unit queued in the
    # same rerun then finds its key gone (KeyError 11/13). Skip it: the new unit's widgets start from the kept amount.
    def from_box():
        if box in st.session_state:
            typed = st.session_state[box]
            if typed is None:
                typed = st.session_state.get(bar, current)
            value = min(max(typed, low), high)
            if value != typed:
                st.session_state[note] = (f'{words(typed)}은(는) 허용 범위({words(low)} ~ {words(high)})를 벗어나 '
                                          f'{words(value)}(으)로 맞췄습니다.')
                st.session_state[box] = value
            # never write the box's own value back otherwise: the server value then replaced + clicks still on their
            # way and quick clicks bounced back (11-18: 43 → 44 → 43 after four clicks)
            st.session_state[bar] = value
            if on_value:
                on_value(value)

    def from_bar():
        if bar in st.session_state:
            st.session_state[box] = st.session_state[bar]
            if on_value:
                on_value(st.session_state[bar])
    extra = {'format': fmt} if fmt else {}
    if slider and low < high:  # one allowed value (e.g. 1만~100만 in 100만원 units): Streamlit refuses min == max
        container.slider(label, min_value=low, max_value=high, step=step, key=bar, on_change=from_bar, help=help, **extra)
        value = container.number_input('직접 입력', step=step, key=box, on_change=from_box, label_visibility='collapsed', **extra)
    else:
        value = container.number_input(label, step=step, key=box, on_change=from_box, help=help, **extra)
    if note in st.session_state:
        container.caption(f':orange[{st.session_state.pop(note)}]')
    return min(max(value, low), high) if value is not None else st.session_state.get(bar, current)


def money_input(container, label, low, high, value, key, unit, help='', slider=False):
    """A KRW amount set in whole units (원 ... 억원): picking a bigger unit snaps the amount to that unit; a smaller
    unit (or typing) sets it finer. `unit` is the default until this field's own choice changes it. The amount is kept
    in KRW across unit changes; returns whole KRW."""
    keep, pick = f'{key}_krw', f'{key}_unit'
    st.session_state.setdefault(keep, int(value))
    units = [name for name, size in MONEY_UNITS.items() if unit_range(low, high, size)]

    def changed():  # a new unit starts its widgets from the kept KRW amount, snapped to that unit
        for name in [n for n in st.session_state if n.startswith(f'{key}_') and n not in (keep, pick)]:
            st.session_state.pop(name, None)
    chosen = st.session_state.get(pick) or unit
    if chosen not in units:
        chosen = units[-1] if MONEY_UNITS[chosen] > MONEY_UNITS[units[-1]] else units[0]
    size = MONEY_UNITS[chosen]
    first, last = unit_range(low, high, size)
    current = min(max(round(st.session_state[keep] / size), first), last)

    def store(v):
        st.session_state[keep] = int(v) * size
    shown = synced_number(container, f'{label} ({chosen})', int(first), int(last), int(current), f'{key}_{chosen}_{int(high)}', slider,
                          help=(help + ' ' if help else '') + f'허용 {krw_words(low)} ~ {krw_words(high)}. 저장값 {value:,}원({krw_words(value)}). '
                               '더 잘게 정하려면 단위를 작게 바꾸세요.', on_value=store, show=lambda v: krw_words(int(v) * size))
    krw = int(shown) * size
    st.session_state[keep] = krw
    container.selectbox('단위', units, index=units.index(chosen), key=pick, on_change=changed,
                        label_visibility='collapsed', help='이 칸의 단위. 단위를 키우면 그 단위로 맞춰집니다.')
    container.caption(f'= {krw:,}원 · {krw_words(krw)}')
    if not low <= int(value) <= high:  # a related limit moved: say the saved value is being brought into range
        container.caption(f':orange[저장값 {krw_words(value)}이 지금 허용 범위({krw_words(low)}~{krw_words(high)})를 벗어나 맞췄습니다. 저장하면 이 값으로 바뀝니다.]')
    return krw


def num_input(container, label, low, high, value, key, step=1, fmt=None, help=None, slider=False):
    """A plain number as a box, or a slider with a box for typing the exact value."""
    kind = type(step)
    return synced_number(container, label, kind(low), kind(high), kind(value), key, slider, step=step, fmt=fmt, help=help)


def live_assets(key, records, refresh=False):
    """The money in a live account (총자산, else 주문가능금액 + 총평가금액) for the capital ceiling, from a broker read cached a
    minute; None when it cannot be read (the capital then cannot be raised above its saved value)."""
    state = f'live_assets_{key}'
    kept = st.session_state.get(state)
    if refresh or not kept or (datetime.now(KST) - kept[0]).total_seconds() > 60:
        read = st.session_state.get('_read_adapter')
        number = live_number(read, records, key) if read is not None else None
        try:
            account = shared_snapshot('live', number, lambda: live_broker_snapshot(read, number, datetime.now(KST)), refresh)['account'] if number else None
        except Exception:
            account = None
        total = None
        if account:
            total = account.get('총자산')
            if total is None and account.get('주문가능금액') is not None and account.get('총평가금액') is not None:
                total = account['주문가능금액'] + account['총평가금액']
        kept = (datetime.now(KST), int(total) if total else None, account)
        st.session_state[state] = kept
    return kept[1], kept[2]


def live_limit_inputs(key, record, budget, unit, prefix, slide, guide=None):
    """The live account's own order guard limits (both Jev trading and 장기투자 accounts). guide: {field: (label, who checks,
    long-term meaning)} of a 장기투자 account, whose section heading the caller draws (longterm_account_view.GUARD)."""
    from accounts import LIVE_LIMITS, live_limits
    saved_limits = live_limits(record=record)
    if guide is None:
        st.markdown('**주문 보호 한도 (이 계좌)** · 실계좌 가드가 매수마다 확인합니다. 승인된 한도(운용금 · 하루 새 종목 5개)보다 넓힐 수 없습니다.')
    l1, l2 = st.columns(2)
    capital_now = budget.get('capital_krw', record['budget']['capital_krw'])
    limits = {'order_max_krw': money_input(l1, '회당 매수 한도', int(LIVE_LIMITS['order_max_krw'][0]),
                                           int(min(LIVE_LIMITS['order_max_krw'][1], max(capital_now, LIVE_LIMITS['order_max_krw'][0]))),
                                           int(min(saved_limits['order_max_krw'], max(capital_now, LIVE_LIMITS['order_max_krw'][0]))),
                                           f'{prefix}{key}_order_max', unit,
                                           guide['order_max_krw'][2] if guide else
                                           '매수 주문 1건의 금액(가격 × 수량) 상한입니다. 운용금보다 크게 잡아도 운용금이 우선합니다.', slide),
              'daily_new_symbols': num_input(l2, '하루 새 종목 수', *LIVE_LIMITS['daily_new_symbols'], saved_limits['daily_new_symbols'],
                                             f'{prefix}{key}_new_symbols', slider=slide,
                                             help=guide['daily_new_symbols'][2] if guide else '그날 처음 사는 종목 수 상한입니다(1~5).')}
    for col, field in ((l1, 'order_max_krw'), (l2, 'daily_new_symbols')) if guide else ():
        col.caption(f':blue[{guide[field][1]}가 확인] · {guide[field][2]}')
    return limits


def match_live_panel(record, records, prefix=''):
    """모의 운용금을 실계좌에 맞춤 (user 2026-11-20): switch, live account, 동시 보유 종목 수, the values before/after; saved at once."""
    import accounts as A
    record = A.load().get('paper-shared') or record  # what is saved now: the form below may have just saved a new budget
    match = record.get('match_live') if isinstance(record.get('match_live'), dict) else None
    manual = (match or {}).get('manual') or {}
    lives = {k: r for k, r in records.items() if r.get('kind') == 'live' and r.get('engine') == 'jev' and not r.get('invalid')}
    with st.container(border=True):
        st.markdown('**실계좌 운용금에 맞춰 모의매매** ' + (':green[켜짐]' if match else ':gray[꺼짐]'),
                    help='켜면 모의계좌의 분할 1회 매수 금액·종목당 최대·주문 보호 한도를 고른 실계좌 운용금과 한도로 계산해 저장합니다. '
                         '모의가 실계좌와 같은 크기로 사고팔아, 실계좌(금액 비율 100%)가 모의 수량을 그대로 따라갈 수 있습니다. '
                         '실계좌 운용금·한도를 바꾸면 모의 값도 다시 계산됩니다. 끄면 켜기 전 값으로 돌아갑니다.')
        if not lives:
            st.caption('맞출 Jev 트레이딩 실계좌가 없습니다.')
            return
        c1, c2 = st.columns(2)
        keys = list(lives)
        live_key = c1.selectbox('맞출 실계좌', keys, index=keys.index(match['account']) if match and match.get('account') in keys else 0,
                                format_func=lambda k: lives[k].get('label', k), key=f'{prefix}match_account')
        positions = int(c2.number_input('동시 보유 종목 수', A.MATCH_POSITIONS[0], A.MATCH_POSITIONS[1],
                                        int((match or {}).get('positions') or 2), key=f'{prefix}match_positions',
                                        help='운용금을 몇 종목에 나눌지입니다. 분할 1회 = 운용금 ÷ (3번 나눠 사기 × 종목 수), 종목당 최대 = 운용금 ÷ 종목 수.'))
        after = A.match_values(record, lives[live_key], positions)
        now_limits, new_limits = A.paper_limits(record), after['limits']
        capital = int(lives[live_key]['budget']['capital_krw'])
        mine = ' · 지금은 아래에서 직접 정한 값 사용'
        rows = [('분할 1회 매수 금액', record['budget'].get('first_buy_krw'), after['budget']['first_buy_krw'],
                 f'운용금 {krw_words(capital)} ÷ (3번 나눠 사기 × {positions}종목)' + (mine if 'first_buy_krw' in manual else '')),
                ('종목당 최대', record['budget'].get('per_symbol_krw'), after['budget']['per_symbol_krw'],
                 f'운용금 ÷ {positions}종목' + (mine if 'per_symbol_krw' in manual else '')),
                ('보유 금액 한도', now_limits['held_max_krw'], new_limits['held_max_krw'], '실계좌 운용금'),
                ('회당 매수 한도', now_limits['order_max_krw'], new_limits['order_max_krw'], '실계좌 회당 매수 한도'),
                ('하루 손실 한도', now_limits['daily_loss_krw'], new_limits['daily_loss_krw'], '실계좌 하루 손실 한도')]
        st.dataframe([{'항목': n, '지금 (저장값)': krw_words(a), '계산값': krw_words(b), '계산 (바꾸는 곳)': how} for n, a, b, how in rows] +
                     [{'항목': '하루 새 종목', '지금 (저장값)': f"{now_limits['daily_new_symbols']}개", '계산값': f"{new_limits['daily_new_symbols']}개",
                       '계산 (바꾸는 곳)': '실계좌 하루 새 종목'}],
                     hide_index=True, width='stretch')
        st.caption('분할 1회·종목당 최대는 모의계좌에만 있는 값이라 실계좌 설정에는 없습니다. 실계좌 운용금(실계좌 탭)이나 위 동시 보유 종목 수를 '
                   '바꾸면 다시 계산됩니다. 실계좌는 모의가 산 수량을 금액 비율대로 따라 삽니다(실계좌 탭 → 금액·한도·미러 규칙).')
        sizing = strategy_settings()[0]
        share = sizing['size_market_against_pct'] if sizing['size_market_on'] else 100
        st.caption(f"하락장에는 분할 1회의 {share:g}%만 사므로(시황 축소, 매매 기준 → 비중 조절) 1주가 그보다 비싼 종목(계산값 기준 약 {krw_words(int(after['budget']['first_buy_krw'] * share / 100))} 넘는 종목)은 "
                   '첫 매수를 못 할 수 있습니다. 모의 하루 손실 한도도 실계좌와 같아져, 그만큼 잃으면 그날 새 매수를 멈춥니다.')
        confirm = st.checkbox('모의계좌 매수 크기와 한도가 바뀌는 것을 확인했습니다', key=f'{prefix}match_confirm')
        b1, b2 = st.columns(2)
        if b1.button('맞춤 켜기·다시 계산' if not match else '다시 계산해 저장', type='primary', disabled=not confirm, key=f'{prefix}match_on'):
            try:
                A.set_match(live_key, positions, True)
            except (OSError, ValueError) as exc:
                st.error(f'저장하지 못했습니다: {exc}')
            else:
                st.toast('모의계좌를 실계좌 운용금에 맞췄습니다. 실행기는 다음 회차부터 새 값을 씁니다.', icon=':material/check_circle:')
                st.rerun()
        if match and b2.button('맞춤 끄기 (켜기 전 값으로)', key=f'{prefix}match_off'):
            try:
                A.set_match(live_key, on=False)
            except (OSError, ValueError) as exc:
                st.error(f'되돌리지 못했습니다: {exc}')
            else:
                st.toast('켜기 전 값으로 되돌렸습니다.', icon=':material/undo:')
                st.rerun()
        if match:
            st.info('아래 분할 1회·종목당 최대는 직접 바꿔 "저장"하면 바로 적용되고, 실계좌 운용금이 바뀌어도 그 값을 유지합니다'
                    '(회당 매수·보유 금액 한도는 넘지 않게 줄임). 계산값으로 돌아가려면 "다시 계산해 저장"을 누르세요. '
                    '주문 보호 한도(회당 매수·보유 금액·하루 손실·하루 새 종목)는 늘 실계좌 값을 따릅니다.')


@st.fragment  # a +/- click or typed amount reruns this form only, so its slider follows at once (11-18: the whole
# page with balances, quotes and the sidebar reran on every click, the slider lagged and quick clicks bounced back)
def account_form(key, record, records, prefix=''):
    """Main engine, auto switch and budget for one account (prefix keeps widget keys unique per screen)."""
    from accounts import BUDGET_LIMITS
    fresh = st.session_state.get(f'{prefix}record_{key}')  # a fragment rerun keeps the page's old arguments: the saved record
    record = fresh if isinstance(fresh, dict) else record
    if (done := st.session_state.pop(f'{prefix}saved_{key}', None)):
        st.success(done)
    live = record['kind'] == 'live'
    labels = ACCOUNT_LABELS
    def all_units():  # a new default unit applies to every money field of this account
        for name in [n for n in st.session_state if n.startswith(f'{prefix}{key}_') and n.endswith('_unit')]:
            st.session_state.pop(name)
        for name in [n for n in st.session_state if n.startswith(f'{prefix}{key}_') and any(f'_{u}' in n for u in MONEY_UNITS)
                     and not n.endswith(('_krw', '_unit'))]:
            st.session_state.pop(name)
    unit = st.segmented_control('기본 금액 단위', list(MONEY_UNITS), default=st.session_state.get('prefs', PREFS.DEFAULTS)['money_unit'], key=f'{prefix}unit_{key}', on_change=all_units,
                                help='모든 금액 칸의 단위를 한 번에 바꿉니다. 칸마다 아래 단위 선택으로 따로 바꿀 수도 있습니다. 저장은 원 단위로 됩니다.') or '만원'
    slide = True  # user decision 2026-11-13: every setting is a slider with a box under it for typing the exact value
    with st.container(border=True):
        engine_col, auto_col = st.columns(2)
        from accounts import ENGINE_LABELS
        if live:  # user 2026-11-17: each live account runs Jev trading (mirror) or the long-term engine
            engine = engine_col.segmented_control('운용 방식', list(ENGINE_LABELS), format_func=ENGINE_LABELS.get,
                                                  default=record.get('engine', 'jev'), key=f'{prefix}engine_{key}',
                                                  help='Jev 트레이딩: 공유 모의계좌의 Jev 체결을 이 계좌 한도로 따라 삽니다(장중 1분). '
                                                       '장기투자: 장기투자 엔진의 하루 계획(미국 지수·테마 ETF, 거장 모듈)을 이 계좌 운용금으로 운용합니다.') or record.get('engine', 'jev')
            if engine != record.get('engine'):
                engine_col.caption(':orange[저장하면 실행기가 바뀝니다. 이 계좌의 이전 방식 실행기는 다음 확인 때 멈춥니다.]')
        else:
            engine = 'jev'
            engine_col.markdown('**운용 방식** · Jev 트레이딩 (공유 모의계좌 고정)',
                                help='공유 모의계좌는 Jev가 매매합니다. 장기투자는 "장기투자" 탭의 가상 계좌와 실계좌에서 운용합니다.')
        auto = auto_col.toggle('자동매매' + ((' (장기투자 실행)' if engine == 'longterm' else ' (미러 실행)') if live else ''), value=record['auto'], key=f'{prefix}auto_{key}', help=('끄면 이 계좌의 미러 실행기가 멈추고 실주문도 실행되지 않습니다. 실제 주문 여부는 위 실주문 스위치가 정합니다.' if live else None))
        budget = {}
        caps = {}
        if not live:  # the first buy fits in one order and a symbol's total in the held limit: follow those limits as edited
            match_live_panel(record, records, prefix)
            from accounts import paper_limits
            saved_caps = paper_limits(record)
            caps = {'first_buy_krw': st.session_state.get(f'{prefix}{key}_order_max_krw_krw', saved_caps['order_max_krw']),
                    'per_symbol_krw': st.session_state.get(f'{prefix}{key}_held_max_krw_krw', saved_caps['held_max_krw'])}
        if live:  # the capital is chosen within the money in the account (user decision 2026-11-17)
            assets, account = live_assets(key, records)
            caps['capital_krw'] = assets if assets else max(int(record['budget']['capital_krw']), 10_000)
        longterm = live and engine == 'longterm'  # 2026-11-18: its price cap and loss limit sit in 보호 한도 (장기 전용) below

        def budget_field(col, field, label=None, help=None):  # same bounds, widget key and saved field on every layout
            low, high = BUDGET_LIMITS[record['kind']][field]
            high = max(int(low), min(high, caps.get(field, high)))
            budget[field] = money_input(col, label or labels[field].replace('(원)', '').strip(), int(low), int(high), int(record['budget'][field]),
                                        f'{prefix}{key}_{field}', unit,
                                        help or (ACCOUNT_HELP.get(field, '승인된 실계좌 한도 이하로만 설정할 수 있습니다.') if live else
                                         {'first_buy_krw': '분할 매수 한 번에 사는 금액입니다. 첫 매수와 추가 매수에 적용되며 종목당 최대에 닿으면 더 사지 않습니다. Jev는 아래 1회 최대 수량과 회당 매수 한도도 따릅니다.',
                                          'per_symbol_krw': '한 종목에 보유할 수 있는 매입금액의 상한입니다. 상한은 아래 보유 금액 한도를 따릅니다.'}.get(field, '')), slide)
        fields = ['capital_krw'] if longterm else list(BUDGET_LIMITS[record['kind']])
        for col, field in zip(st.columns(2 if longterm else len(fields)), fields):
            budget_field(col, field)
        if live:
            if assets:
                st.caption(f"운용금은 이 실계좌 잔고 안에서 정합니다: 총자산 {assets:,}원 (주문가능 {account.get('주문가능금액') or 0:,}원 · "
                           f"보유 평가 {account.get('총평가금액') or 0:,}원, 1분 안 조회). 매수 직전에도 증권사 현금·총자산을 다시 확인합니다.")
            else:
                st.warning('실계좌 잔고를 조회하지 못해 운용금을 지금 저장값보다 올릴 수 없습니다. 잠시 뒤 다시 열거나 트레이딩 현황에서 잔고를 새로고침하세요.')
        rules = limits = None
        if not live:
            from accounts import PAPER_LIMITS, paper_limits
            current_limits = paper_limits(record)
            st.caption('분할 1회 금액은 Jev와 낙폭과대 경로에 적용되고, 아래 주문 보호 한도를 따릅니다.')
            import tunables
            st.markdown('**주식 수량 제한**')
            q1, q2 = st.columns(2)
            saved_order_qty = tunables.setting('paper_max_order_qty')
            saved_total_qty = tunables.setting('paper_max_total_qty')
            order_on = q1.toggle('1회 주문 수량 제한', value=bool(saved_order_qty), key=f'{prefix}{key}_order_qty_on',
                                 help='Jev 공유 모의 실행기의 일반 매수·일반 매도에 적용합니다. 끄면 매도는 보유·매도 가능 수량 이내에서 주수 제한 없이 처리합니다. '
                                      '보호 손절·수동 정리·장 마감 정리는 신속한 청산을 위해 이 제한과 별개로 처리합니다. 실계좌는 계좌별 1회 주문 수량 한도를 따릅니다.')
            total_on = q2.toggle('전체 보유 수량 제한', value=bool(saved_total_qty), key=f'{prefix}{key}_total_qty_on',
                                 help='끄면 전체 보유는 금액 한도만 따릅니다. 모의 실행기에 적용됩니다.')
            quantity = num_input(q1, '일반 주문 1회 최대 수량 (주)', 1, 1_000_000, saved_order_qty or 100,
                                 f'{prefix}{key}_paper_max_qty', slider=slide) if order_on else 0
            total_quantity = num_input(q2, '전체 보유 최대 수량 (주)', 1, 1_000_000, saved_total_qty or 1000,
                                       f'{prefix}{key}_paper_total_qty', slider=slide) if total_on else 0
            if st.button('수량 제한 저장', key=f'{prefix}{key}_save_paper_max_qty', type='primary'):
                try:
                    tunables.save({'paper_max_order_qty': int(quantity), 'paper_max_total_qty': int(total_quantity)})
                    st.success('수량 제한을 저장했습니다. 최신 실행기는 다음 주기부터 적용합니다.')
                except (OSError, ValueError) as exc:
                    st.error(f'최대 수량을 저장하지 못했습니다: {exc}')
            st.markdown('**주문 보호 한도** · 주문 가드가 모든 매수에 적용합니다(저장 즉시, 다음 주문부터)')
            guide = {'order_max_krw': ('회당 매수 한도 (원)', '한 번의 매수 주문 금액(지정가 × 수량) 상한입니다.'),
                     'daily_buy_krw': ('하루 매수 한도 (원)', '그날 낸 매수 주문 금액의 합 상한입니다. 판 금액은 빼지 않습니다.'),
                     'held_max_krw': ('보유 금액 한도 (원)', '지금 보유한 종목의 매입금액(미체결 매수 포함)에 새 매수를 더한 상한입니다.'),
                     'daily_new_symbols': ('하루 새 종목 수', '그날 처음 사는 종목 수 상한입니다. 그날 이미 산 종목의 추가 매수는 세지 않습니다.'),
                     'daily_loss_krw': ('하루 손실 한도 (원)', '그날 손실이 이만큼 나면 신규 매수를 멈춥니다(보유분 손절·익절은 계속).')}
            limits = {}
            for col, (field, (low, high)) in zip(st.columns(len(PAPER_LIMITS)), PAPER_LIMITS.items()):
                label, text = guide[field]
                if field == 'daily_new_symbols':
                    limits[field] = num_input(col, label, int(low), int(high), int(current_limits[field]), f'{prefix}{key}_{field}',
                                              help=f'{text} 허용 {low}~{high}개.', slider=slide)
                else:
                    limits[field] = money_input(col, label.replace(' (원)', ''), int(low), int(high), int(current_limits[field]),
                                                f'{prefix}{key}_{field}', unit, text, slide)
        if longterm:  # docs/longterm-account-settings-plan.md 1절: 운용 상태 · 포트폴리오 · 적립식 · 보호 한도 (장기 전용)
            import longterm_account_view as LTV
            LTV.settings_panel(key, record, budget['capital_krw'], prefix)
            limits = live_limit_inputs(key, record, budget, unit, prefix, slide, guide=LTV.GUARD)
            for col, field in zip(st.columns(2), ('order_price_max_krw', 'daily_loss_krw')):
                label, who, text = LTV.GUARD[field]
                budget_field(col, field, label, text)
                col.caption(f':blue[{who}가 확인] · {text}')
        elif live:
            from accounts import live_rules, LIVE_RULE_LIMITS
            current = live_rules(record)
            follow_mode_view(key, prefix)
            st.caption('실계좌는 엔진 원장을 아래 매수 크기로 미러합니다. 진입·추가 매수·익절·국면별 청산·장 마감 정리·종가매매 이월은 '
                       '모의 실행기를 그대로 따르고, 실계좌만의 규칙은 위 금액 한도와 아래 매수 크기·추격 한도·손절 %입니다.')
            st.info('매수 크기 기준·금액·1주 가격 상한을 바꾸면 **새로 생기는 모의 체결부터** 적용합니다. 이미 따라 산 몫은 고정돼 있어 '
                    '설정 변경만으로 기존 보유를 더 사거나 팔지 않습니다(2026-11-17). 기존 보유는 모의가 팔 때 비율대로 줄어듭니다.')
            s1, s2 = st.columns([3, 2])
            sizing = s1.radio('매수 크기 기준', ['share', 'amount'], index=['share', 'amount'].index(current['sizing']), horizontal=True,
                              format_func=lambda v: {'share': '1주씩 (모의가 몇 주를 사든 1주)', 'amount': '금액 비율 (모의가 쓴 돈의 정한 %만큼)'}[v],
                              key=f'{prefix}{key}_sizing', help=SIZING_HELP)
            follow = current.get('follow_pct')
            if sizing == 'amount':
                tranche = money_input(s2, '한 번에 따라 살 최대 금액 (금액 비율일 때)', *map(int, LIVE_RULE_LIMITS['tranche_krw']), int(current['tranche_krw']),
                                      f'{prefix}{key}_tranche', unit, TRANCHE_HELP, slide)
                f1, f2 = st.columns([2, 3])
                mode = f1.radio('금액 비율 정하는 법', ['auto', 'manual'], index=0 if follow is None else 1, horizontal=True,
                                format_func=lambda v: {'auto': '자동 (운용금에 맞춰 계산)', 'manual': '직접 정하기 (%)'}[v], key=f'{prefix}{key}_follow_mode',
                                help=FOLLOW_HELP)
                if mode == 'manual':
                    follow = float(num_input(f2, '모의가 쓴 돈의 몇 %를 살까 (100% = 모의 수량 그대로)', 1, 100, int(round(follow or 10)), f'{prefix}{key}_follow_pct',
                                             slider=slide, help=FOLLOW_HELP))
                else:
                    follow = None
            else:  # not used by the share basis: shown disabled, the saved value kept for 금액 기준
                tranche = int(current['tranche_krw'])
                s2.number_input('한 번에 따라 살 최대 금액 (금액 비율일 때) (원)', value=tranche, disabled=True, key=f'{prefix}{key}_tranche_off',
                                help='1주 기준에서는 쓰지 않습니다. 모의가 몇 주를 사든 실계좌는 1주를 삽니다. ' + TRANCHE_HELP)
                s2.caption(f'1주씩일 때는 쓰지 않습니다 (저장값 {krw_words(tranche)} 유지)')
            sizing_example_view(record, budget, {**current, 'follow_pct': follow}, sizing, int(tranche))  # limits below: saved values
            limits = live_limit_inputs(key, record, budget, unit, prefix, slide)
            r1, r2, r3 = st.columns(3)
            if sizing == 'amount':  # not used by amount sizing (2026-11-17): shown disabled, the saved value kept for 1주 기준
                max_qty = int(current['max_order_qty'])
                r1.number_input('1회 매수 최대 수량 (주)', value=max_qty, disabled=True, key=f'{prefix}{key}_maxqty_off',
                                help='금액 기준에서는 쓰지 않습니다(주수 제한 없음). 회당 매수 한도·운용금·하루 손실이 매수를 제한합니다. '
                                     '1주 기준으로 바꾸면 이 값을 다시 정할 수 있습니다.')
                r1.caption('금액 비율일 때: 주수 제한 없음')
            else:
                max_qty = num_input(r1, '1회 매수 최대 수량 (주)', *map(int, LIVE_RULE_LIMITS['max_order_qty']), int(current['max_order_qty']),
                                    f'{prefix}{key}_maxqty', slider=slide,
                                    help='1주 기준일 때 매수 주문 한 번의 최대 수량입니다(1~1,000주). '
                                         '매도는 이 한도 없이 보유량·매도가능량 이내로 처리합니다.')
            chase = num_input(r2, '가격 추격 한도 (%)', *map(float, LIVE_RULE_LIMITS['chase_pct']), float(current['chase_pct']),
                              f'{prefix}{key}_chase', step=0.1, fmt='%.1f', slider=slide,
                                    help='모의가 산 그날, 현재 매도호가가 모의 체결가보다 이 % 이내이면 따라 삽니다(0.1~5%).')
            stop = num_input(r3, '손절 % (실계좌 평균가 기준)', *map(float, LIVE_RULE_LIMITS['stop_loss_pct']),
                             float(current['stop_loss_pct']), f'{prefix}{key}_stop', step=0.1, fmt='%.1f', slider=slide,
                                   help='계좌 손절 설정입니다(0.5~2%). ' + LIVE_STOP_GUIDE)
            adds = num_input(st, '추가 매수 최대 횟수 (실계좌, 첫 매수 제외)', *map(int, LIVE_RULE_LIMITS['max_adds']), int(current['max_adds']),
                             f'{prefix}{key}_max_adds', slider=slide,
                             help='엔진이 한 종목을 여러 번 나눠 살 때 이 실계좌가 따라 사는 추가 매수 횟수입니다(0~100, 100이면 엔진을 그대로 따름). '
                                  '엔진의 추가 매수 최대 횟수보다 크게 잡아도 엔진 횟수를 넘지 않습니다. 바꾼 값은 새 모의 체결부터 적용되어, '
                                  '줄여도 이미 따라 산 몫을 팔지 않습니다.')
            evidence = st.toggle('추격 한도를 넘어도 근거가 있으면 매수', value=current['evidence_chase'], key=f'{prefix}{key}_evidence',
                                 help='추격 한도를 넘었어도 ① 엔진이 최근 15분 안에 그 종목을 다시 고르고(Jev 진입 통과 또는 진입 신뢰도 이상 후보) '
                                      '② 현재가가 모의 계획의 1차 익절가보다 낮으면 삽니다. 1차 익절가를 넘었다면 모의도 '
                                      '팔기 시작하는 구간이라 사지 않습니다.')
            rules = {**current, 'stop_loss_pct': stop, 'follow_paper': True, 'sizing': sizing, 'tranche_krw': int(tranche), 'follow_pct': follow,
                     'max_order_qty': int(max_qty), 'chase_pct': float(chase), 'evidence_chase': evidence, 'max_adds': int(adds)}
        if st.button('저장', type='primary', key=f'{prefix}save_{key}'):
            try:
                from accounts import save_one  # this account only, on top of what is on disk now (never a stale copy)
                st.session_state[f'{prefix}record_{key}'] = save_one(key, {'engine': engine, 'auto': auto, 'budget': budget, **({'rules': rules} if rules else {}),
                               **({'limits': {k: int(v) for k, v in limits.items()}} if limits else {})})
                st.session_state[f'{prefix}saved_{key}'] = '저장했습니다. 모의계좌는 실행기의 다음 주기(약 1분)부터 적용됩니다.'
            except (OSError, ValueError) as exc:
                text = str(exc)
                for raw, word in {**ACCOUNT_LABELS, 'order_max_krw': '회당 매수 한도', 'daily_buy_krw': '하루 매수 한도',
                                  'held_max_krw': '보유 금액 한도', 'daily_new_symbols': '하루 새 종목 수', 'daily_loss_krw': '하루 손실 한도',
                                  'tranche_krw': '분할 1회 금액'}.items():
                    text = text.replace(raw, word.replace('(원)', '').strip())
                st.error(f'저장하지 못했습니다: {text}')
            else:
                try:  # redraw the form (and the match table above it) with the saved values
                    st.rerun(scope='fragment')
                except st.errors.StreamlitAPIException:  # drawn outside a fragment run (a nested call, a test page)
                    st.rerun()
    saved = {ACCOUNT_LABELS.get(k, k).replace('(원)', '').strip(): v for k, v in record['budget'].items()}
    if record['kind'] != 'live':
        from accounts import paper_limits
        names = {'order_max_krw': '회당 매수', 'daily_buy_krw': '하루 매수', 'held_max_krw': '보유 금액', 'daily_loss_krw': '하루 손실'}
        saved.update({names[k]: v for k, v in paper_limits(record).items() if k in names})
    st.caption('저장된 값: ' + ' · '.join(f'{k} {v:,}원({krw_words(v)})' for k, v in saved.items() if type(v) is int))


ACTIVITY = (('Jev 판단', ('jev_shadow', 'jev_holding_decision')), ('주문', ('submitted', 'sell_submitted', 'split_add')),
            ('보호 매도', ('v2_risk_exit', 'manual_exit', 'vibe_exit', 'split_take_profit')),
            ('오류·중단', ('v2_news_error', 'jev_error', 'v2_holding_decision_error', 'v2_risk_exit_error', 'stopped')))


def activity_frames(events, day):
    """Today's events per 10-minute bucket by kind, and why entries waited."""
    kinds = {name: label for label, names in ACTIVITY for name in names}
    buckets, reasons = [], {}
    for event in events:
        stamp = event_time(event.get('time')) if isinstance(event, dict) else None
        if stamp is None or stamp.date() != day:
            continue
        kind = event.get('event')
        label = kinds.get(kind)
        if kind in ('v2_entry_gate', 'v2_execution_gate') and event.get('permitted') is False:
            label = '대기'
            reason = event.get('reason') or '기록 없음'
            reasons[reason] = reasons.get(reason, 0) + 1
        if label:
            buckets.append({'시각': stamp.replace(minute=stamp.minute - stamp.minute % 10, second=0, microsecond=0).replace(tzinfo=None),
                            '종류': label})
    frame = pd.DataFrame(buckets)
    counts = frame.groupby(['시각', '종류']).size().reset_index(name='건수') if not frame.empty else frame
    waits = pd.DataFrame([{'사유': execution_reason_label(r) if r in ('order_limit', 'position_limit', 'learning_filter',
                           'insufficient_buying_power', 'order_guard_refused', 'single_order_limit', 'split_limit')
                           else entry_reason_label(r), '건수': n} for r, n in reasons.items()])
    return counts, waits


def day_summary(events, day, fills=None):
    """One trading day of the shared runner in numbers: judgments, confidence, entries passed, orders, fills, top wait."""
    jev, permitted, orders, protective, errors, waits, last = [], 0, 0, 0, 0, {}, None
    for event in events:
        stamp = event_time(event.get('time')) if isinstance(event, dict) else None
        if stamp is None or stamp.date() != day:
            continue
        last = max(last, stamp) if last else stamp
        kind = event.get('event')
        if kind == 'jev_shadow':
            score = ((event.get('jev') or {}).get('candidate') or {}).get('confidence')
            jev.append(score if type(score) in (int, float) else None)
        elif kind in ('v2_entry_gate', 'v2_execution_gate'):
            if event.get('permitted') is True:
                permitted += 1
            elif event.get('permitted') is False:
                reason = event.get('reason') or '기록 없음'
                waits[reason] = waits.get(reason, 0) + 1
        elif kind in ('submitted', 'sell_submitted', 'split_add'):
            orders += 1
        elif kind in ('v2_risk_exit', 'manual_exit', 'vibe_exit', 'split_take_profit'):
            protective += 1
        elif kind in ('v2_news_error', 'jev_error', 'v2_holding_decision_error', 'v2_risk_exit_error', 'v2_error', 'error'):
            errors += 1
    scores = [x for x in jev if x is not None]
    day_fills = [f for f in fills or [] if (event_time(f.get('시각')) or datetime.min.replace(tzinfo=KST)).date() == day]
    top = max(waits.items(), key=lambda item: item[1]) if waits else None
    return {'judgments': len(jev), 'max_conf': max(scores) if scores else None, 'avg_conf': sum(scores) / len(scores) if scores else None,
            'permitted': permitted, 'orders': orders, 'protective': protective, 'errors': errors, 'waits': sum(waits.values()),
            'top_wait': (entry_reason_label(top[0]), top[1]) if top else None, 'last': last,
            'buys': sum(f.get('구분') == 'BUY' for f in day_fills), 'sells': sum(f.get('구분') == 'SELL' for f in day_fills)}


def day_kpis(summary, entry_min):
    """The day summary as one row of cards; each card says what it counts in its (?) tooltip."""
    best = summary['max_conf']
    met = best is not None and best >= entry_min
    buys, sells = summary['buys'], summary['sells']
    sc.row([{'label': 'Jev 판단', 'value': f"{summary['judgments']:,}회", 'tone': 'ai' if summary['judgments'] else 'off', 'icon': 'psychology',
             'sub': None if summary['avg_conf'] is None else f"평균 신뢰도 {summary['avg_conf']:.2f}",
             'help': '실행기가 Jev에게 진입 판단을 물은 횟수입니다. 장중 1분마다 한 번입니다.'},
            {'label': '최고 신뢰도', 'value': '—' if best is None else f'{best:.2f}', 'icon': 'speed',
             'tone': 'off' if best is None else 'ok' if met else 'warn',
             'sub': None if best is None else ('진입 기준 충족' if met else f'진입 기준 {entry_min:.2f} 미달'),
             'help': f'이 날 Jev 진입 판단 중 가장 높은 신뢰도입니다. 진입 기준은 {entry_min:.2f}이며 신뢰도는 수익 확률이 아닙니다.'},
            {'label': '진입 통과 · 주문', 'value': f"{summary['permitted']}회 · {summary['orders']}건", 'icon': 'shopping_cart_checkout',
             'tone': 'up' if summary['orders'] else 'ok' if summary['permitted'] else 'off', 'pulse': bool(summary['orders']),
             'help': '진입 조건을 모두 통과한 횟수와 실제로 낸 매수·매도·추가 매수 주문 수입니다.'},
            {'label': '체결 확인 (매수 / 매도)', 'value': f'{buys} / {sells}', 'icon': 'swap_vert',
             'tone': 'up' if buys > sells else 'down' if sells else 'off',
             'help': '실행기가 증권사 조회로 확인한 체결 건수입니다(최근 100건 원장 기준). 매수가 많으면 빨강, 매도가 있으면 파랑입니다.'}])
    top = summary['top_wait']
    if top:
        st.caption(f"가장 많은 대기 사유: **{top[0]}** {top[1]}회 (전체 대기 {summary['waits']}회) · 전체 사유는 아래 \"매매하지 않은 이유\"")
    if summary['errors']:
        st.warning(f"이 날 오류 기록 {summary['errors']}건이 있습니다. 아래 기록 표에서 확인하세요.")


def activity_charts(counts, waits):
    if counts.empty:
        st.caption('표시할 실행기 기록이 없습니다.')
        return
    palette = {'Jev 판단': '#a855f7', '대기': '#64748b', '주문': '#22c55e', '보호 매도': '#3b82f6', '오류·중단': '#ef4444'}
    st.altair_chart(alt.Chart(counts).mark_bar().encode(
        x=alt.X('시각:T', title=None, axis=alt.Axis(format='%H:%M')), y=alt.Y('건수:Q', stack=True),
        color=alt.Color('종류:N', scale=alt.Scale(domain=list(palette), range=list(palette.values()))),
        tooltip=[alt.Tooltip('시각:T', format='%H:%M'), '종류', '건수']).properties(height=220), width='stretch')
    if not waits.empty:
        st.markdown('##### 매매하지 않은 이유')
        # the count sits in the label: a separate text layer collapsed the rows into one band
        waits = waits.assign(항목=waits['사유'] + ' · ' + waits['건수'].astype(str) + '건').sort_values('건수', ascending=False)
        st.altair_chart(alt.Chart(waits).mark_bar(color='#64748b').encode(
            y=alt.Y('항목:N', sort=list(waits['항목']), title=None, axis=alt.Axis(labelLimit=360)), x=alt.X('건수:Q'),
            tooltip=['사유', '건수']).properties(height=70 + 40 * len(waits)), width='stretch')  # height includes the axis


def fill_rows(fills, names):
    """(rows, trading days in order) of verified fills for the chart: day label, minute since the 09:00 open (0~390), stock
    name, side, quantity, price and KRW; a fill without a readable time or price is left out."""
    rows = []
    for f in fills or []:
        at = event_time(f.get('시각'))
        if not at or type(f.get('체결가')) not in (int, float) or type(f.get('수량')) not in (int, float):
            continue
        rows.append({'날짜': f"{at:%m/%d}({'월화수목금토일'[at.weekday()]})", 'day': at.date().isoformat(), '시각': f'{at:%H:%M}',
                     '장중 분': min(390, max(0, at.hour * 60 + at.minute - 540)), '종목': names.get(f.get('종목'), f.get('종목')),
                     '구분': '매수' if str(f.get('구분')).upper() == 'BUY' else '매도', '수량': f['수량'], '체결가': f['체결가'],
                     '금액': round(f['수량'] * f['체결가'])})
    rows.sort(key=lambda r: (r['day'], r['장중 분']))
    return rows, list(dict.fromkeys(r['날짜'] for r in rows))


def fills_chart(fills, names):
    """Verified paper fills (2026-11-19 redesign): one line per stock, trading days side by side with the minute of the
    session inside each day (no night or weekend gaps, no shared price axis), ▲ buy red · ▼ sell blue sized by KRW; then the
    day's buy and sell totals."""
    rows, days = fill_rows(fills, names)
    if not rows:
        return
    frame = pd.DataFrame(rows)
    buys, sells = frame.loc[frame['구분'] == '매수', '금액'].sum(), frame.loc[frame['구분'] == '매도', '금액'].sum()
    st.caption(f"{rows[0]['day']} ~ {rows[-1]['day']} · 체결 {len(rows)}건 · {frame['종목'].nunique()}종목 · "
               f"매수 {buys:,.0f}원 · 매도 {sells:,.0f}원 (체결가 기준, 수수료·세금 전)")
    stocks = list(frame.sort_values('day', ascending=False)['종목'].drop_duplicates())  # the latest traded on top
    side = alt.Scale(domain=['매수', '매도'], range=[UP, DOWN])
    x = alt.X('날짜:O', sort=days, title=None, axis=alt.Axis(labelAngle=0, grid=True, bandPosition=0, orient='top'))
    st.altair_chart(alt.Chart(frame).mark_point(filled=True, opacity=0.85).encode(
        x=x, xOffset=alt.XOffset('장중 분:Q', scale=alt.Scale(domain=[0, 390])),
        y=alt.Y('종목:N', sort=stocks, title=None, axis=alt.Axis(labelLimit=200, grid=True)),
        color=alt.Color('구분:N', scale=side, legend=alt.Legend(orient='bottom', title=None)),
        shape=alt.Shape('구분:N', scale=alt.Scale(domain=['매수', '매도'], range=['triangle-up', 'triangle-down']), legend=None),
        size=alt.Size('금액:Q', scale=alt.Scale(range=[50, 420]), legend=None),
        tooltip=['날짜', '시각', '종목', '구분', alt.Tooltip('수량:Q', format=','), alt.Tooltip('체결가:Q', format=','),
                 alt.Tooltip('금액:Q', format=',', title='금액(원)')]).properties(height=alt.Step(30)), width='stretch')
    st.caption('줄 하나가 종목 하나, 칸 하나가 거래일입니다. 칸 안에서 왼쪽이 09:00 개장, 오른쪽이 15:30 마감이고, 점이 클수록 체결 금액이 큽니다. '
               '빨강 ▲ 매수 · 파랑 ▼ 매도. 점에 마우스를 올리면 시각·수량·체결가가 보입니다.')
    totals = frame.groupby(['날짜', '구분'], as_index=False).agg(금액=('금액', 'sum'), 건수=('금액', 'size'))
    totals['만원'] = totals['금액'] / 10_000
    bars = alt.Chart(totals).encode(
        x=alt.X('날짜:O', sort=days, title=None, axis=alt.Axis(labelAngle=0)), xOffset=alt.XOffset('구분:N', sort=['매수', '매도']),
        y=alt.Y('만원:Q', title='체결 금액(만원)'), color=alt.Color('구분:N', scale=side, legend=None),
        tooltip=['날짜', '구분', alt.Tooltip('금액:Q', format=',', title='금액(원)'), '건수'])
    st.altair_chart((bars.mark_bar() + bars.mark_text(dy=-6, fontSize=11).encode(text=alt.Text('건수:Q', format='d'))).properties(height=170),
                    width='stretch')
    st.caption('거래일별 매수(빨강)·매도(파랑) 체결 금액, 막대 위 숫자는 체결 건수입니다.')


def operations_panel(root):
    """Services, today's runner activity, why it waited, and how much learning data has accumulated."""
    head, refresh = st.columns([4, 1], vertical_alignment='center')
    head.subheader('운영 상태')
    if refresh.button('통합 현황 새로고침', width='stretch'):
        st.rerun()
    st.caption(f"확인 시각: {datetime.now(KST):%Y-%m-%d %H:%M:%S} · 프로세스 실행은 정상 주문이나 체결을 보증하지 않으며 장외에는 종료될 수 있습니다.")
    states = processes()
    halt = halt_flag_set('nhplug')
    paper = root / 'nhplug' / 'shared-etf-paper'
    events = []
    try:
        events = read_events((paper / 'events.jsonl'), 20000)
    except (OSError, ValueError):
        st.warning('공유 모의 실행기 기록을 읽을 수 없습니다.')
    try:
        with ro_connect(paper / "orders.sqlite3") as db:
            fills = paper_order_records(db)[0]
    except sqlite3.Error:
        fills = None
    day = datetime.now(KST).date()
    counts, waits = activity_frames(events, day)
    if counts.empty:  # outside market hours show the last trading day that has records
        last = max((event_time(e.get('time')) for e in events if isinstance(e, dict) and event_time(e.get('time'))), default=None)
        if last:
            day = last.date()
            counts, waits = activity_frames(events, day)
    summary = day_summary(events, day, fills)
    running = sum(v == '실행 중' for v in states.values())
    runner = states.get('공유 모의 ETF 실행기', '확인 불가')
    runner_tone, runner_icon, runner_pulse = sc.service(runner)
    age = (datetime.now(KST) - summary['last']).total_seconds() / 60 if summary['last'] else None
    import session_times
    close = session_times.load(paper)['close']
    in_session = session_times.order_start() <= datetime.now(KST).strftime('%H:%M') < close and datetime.now(KST).weekday() < 5
    sc.row([{'label': '자동 작업', 'value': f'{running}/{len(states)} 실행 중', 'icon': 'dns',
             # one-shot jobs end as 종료 by design: healthy means every service is registered and its state known
             'tone': 'ok' if all(v in ('실행 중', '종료') for v in states.values()) else 'warn' if running else 'alert',
             'help': '아래 "서비스 상세"의 자동 작업 중 지금 돌고 있는 수입니다. 시간에 맞춰 한 번 도는 작업(시장 수집·백업 등)은 끝나면 "종료"로 보입니다. '
                     '모두 등록돼 있으면 초록입니다(장중 실행기가 멈추면 종합 현황 주의 항목에 나옵니다).'},
            {'label': '모의 실행기', 'value': runner, 'tone': runner_tone if in_session or runner == '실행 중' else 'off', 'icon': runner_icon,
             'pulse': runner_pulse, 'sub': f'장중(09:10~{close})' if in_session else '장 시간 밖 · 종료가 정상',
             'help': '모의계좌에 주문하는 유일한 실행기입니다. 장중에 "실행 중"이 아니면 자동 주문과 손절·익절 감시가 멈춥니다.'},
            {'label': '마지막 실행기 기록', 'value': f"{summary['last']:%H:%M}" if summary['last'] else '없음', 'icon': 'history',
             'tone': 'off' if age is None or not in_session else 'ok' if age <= 3 else 'alert',
             'sub': f"{summary['last']:%m/%d} · {age:,.0f}분 전" if age is not None else None, 'pulse': age is not None and age <= 3 and in_session,
             'help': '실행기가 남긴 가장 최근 기록입니다. 장중에 3분 넘게 기록이 없으면 빨간 테두리로 깜박입니다.'},
            {'label': '실계좌 HALT', 'value': '설정됨' if halt else '해제됨', 'icon': 'gpp_bad' if halt else 'verified_user',
             'tone': 'alert' if halt else 'off',
             'help': 'NH 실계좌 주문 전역 중단 표시입니다. 설정되면 실계좌 실주문 경로가 막힙니다. 모의계좌와는 관계없습니다.'}])
    import dialogs as dlg
    dlg.buttons([('자세히 · 자동 작업', 'ops_d_services', dlg.services_dialog, (states,)),
                 ('자세히 · 실행기 기록', 'ops_d_events', dlg.events_dialog, ('모의 실행기 최근 기록', events)),
                 ('자세히 · 주문·체결', 'ops_d_orders', dlg.events_dialog,
                  ('주문·청산 기록', events, {'submitted', 'sell_submitted', 'split_add', 'v2_risk_exit', 'split_take_profit', 'manual_exit', 'v2_execution_gate'})),
                 ('자세히 · 대기 사유', 'ops_d_waits', dlg.table_dialog,
                  (f'{day:%m/%d} 매수하지 않은 이유', waits.sort_values('건수', ascending=False) if not waits.empty else [], '실행기가 진입을 보류한 회차의 사유별 건수입니다.'))])
    import vibe_longterm_view  # L2 (2026-11-17): the long-term engine runs once a day, so freshness, not activity
    st.markdown('##### 장기투자 엔진')
    sc.row(vibe_longterm_view.freshness_cards(root / 'nhplug' / 'vibe-longterm', states.get('장기투자 실행기', '확인 불가'), datetime.now(KST)))
    st.markdown(f'##### {day:%m/%d} 모의 실행기 하루 요약')
    day_kpis(summary, strategy_settings()[0]['entry_confidence'])
    with st.expander(f'서비스 상세 ({running}/{len(states)} 실행 중)', icon=':material/dns:'):
        tone = {'실행 중': 'green', '종료': 'gray', '미등록': 'orange'}
        cols = st.columns(4)
        for i, (name, state) in enumerate(states.items()):
            with cols[i % 4]:
                st.caption(name)
                st.badge(('● ' if state == '실행 중' else '○ ') + state, color=tone.get(state, 'red'))
        st.caption('미등록: 시스템에 등록되지 않은 작업입니다. 재부팅 뒤 자동으로 올라오도록 운영체제의 자동 실행에 등록돼 있어야 합니다.')
    st.markdown(f'##### 모의 실행기 활동 ({day:%m/%d}, 10분 단위)')
    activity_charts(counts, waits)

    st.markdown('##### 학습·기록 데이터 적재량')
    stats = learning(root, shared=True)
    from research_store import ROOT as RESEARCH_ROOT
    rows = []
    for label, key in [('거래 원장', 'ledger'), ('가격 관측', 'observations'), ('Jev 방향 연구', 'jev_model'), ('모의 주문 원장', 'orders')]:
        for item, value in (stats[key] or {}).items():
            rows.append({'항목': f'{label} · {item}', '건수': value, '묶음': label})
    try:
        with ro_connect(RESEARCH_ROOT / "market-research.sqlite3") as db:
            names = {'movers': '순위 스캔', 'watchlist': '관심종목 선정', 'vibe_advice': 'vibe 장중 분석(보관 · ~11-17)'}
            rows += [{'항목': f'시장 이력 · {names.get(k, k)}', '건수': n, '묶음': '시장 이력'}
                     for k, n in db.execute('SELECT kind, COUNT(*) FROM snapshots GROUP BY kind')]
    except sqlite3.Error:
        pass
    if rows:
        st.altair_chart(alt.Chart(pd.DataFrame(rows)).mark_bar().encode(
            y=alt.Y('항목:N', sort=None, title=None, axis=alt.Axis(labelLimit=320)), x=alt.X('건수:Q', scale=alt.Scale(type='symlog')),
            color=alt.Color('묶음:N', legend=None),
            tooltip=['항목', alt.Tooltip('건수:Q', format=',')]).properties(height=30 * len(rows)), width='stretch')
        st.caption('막대 길이는 로그 눈금입니다(건수 차이가 커서). Jev 연구는 가격 방향 관측이며 체결이나 수익률이 아닙니다.')
    else:
        st.caption('학습 데이터 파일이 아직 없습니다.')

    with st.expander('최근 기록 표 (모의 ETF 실행기)'):  # the live mirror's log is on 실계좌 · AI·실행 기록
        shared_events = [{k: e.get(k) for k in ('time', 'event', 'choice', 'action', 'reason', 'execution')}
                         for e in events[-20:][::-1] if isinstance(e, dict)]  # the journal read above, not a second 39 MB pass
        if shared_events:
            st.dataframe(shared_events, hide_index=True)
        else:
            st.caption('공유 ETF 기록이 없거나 읽을 수 없습니다.')
    with st.container(border=True):
        st.markdown('**모의 실행기 중단**')
        st.caption('STOP 파일을 만들어 실행기를 멈춥니다. 미체결과 기존 보유분은 확인이 필요하며, 멈춘 동안에는 손절·익절 감시도 없고 자동 청산을 보장하지 않습니다.')
        sure = st.checkbox('보유분 감시가 멈춘다는 것을 확인했습니다', key='ops_stop_confirm')
        if st.button('공유 ETF 실행 중단 요청', disabled=not sure, icon=':material/stop_circle:',
                     help='확인란을 체크해야 누를 수 있습니다. 다시 시작하려면 미확인 주문을 대조한 뒤 STOP 파일을 지우고 실행기를 시작합니다.'):
            try:
                stop_shared_paper(root)
                st.warning('공유 ETF 중단을 요청했습니다. 실행기 종료와 주문 상태를 확인하세요.')
            except OSError:
                st.error('공유 ETF 중단 요청을 저장하지 못했습니다.')
        if (paper / 'STOP').exists():
            st.warning('공유 ETF 실행 중단 요청이 설정되어 있습니다.')
    settings_files_panel()


HISTORY_KINDS = {'accounts': '계좌·자동매매', 'strategy': '매매 기준·제한 규칙', 'tunables': '운영 조정값', 'prefs': '화면 설정',
                 'ai_modes': 'AI 사용 설정'}


def settings_files_panel():
    """운영 조정값 (tunables.json) editor and one timeline of every settings change (settings-history.sqlite3)."""
    import config_store
    import tunables as T
    st.markdown('##### 운영 조정값')
    st.caption('비밀값도 매매 기준도 아닌 운영값입니다. 저장 위치는 런타임 폴더의 tunables.json이고, 범위를 벗어나면 가까운 경계값으로 맞춥니다. '
               '대시보드 값은 바로 반영되고, 실행기 값은 그 실행기를 다시 시작해야 반영됩니다. 공유 실행기 1회 최대 수량은 계좌·자동매매에서 설정합니다.')
    flash = st.session_state.pop('tunables_flash', None)
    if flash:
        st.success(flash, icon=':material/check_circle:')
    current, chosen = T.values(), {}
    for group in T.GROUPS:
        if group == 'vibe-trading':  # the intraday vibe runner and advisor were retired on 2026-11-17: values kept, not edited
            with st.expander('보관된 설정 · 장중 vibe (2026-11-17 종료, 읽기 전용)', icon=':material/inventory_2:'):
                st.caption('장중 vibe 판단기·실행기가 멈춰 아무 실행기도 이 값을 읽지 않습니다. 기록을 위해 값만 남겨 둡니다. 장기투자 설정은 "장기투자" 탭에 있습니다.')
                st.dataframe([{'설정': T.TUNABLES[k][4], '저장값': current[k]} for k, v in T.TUNABLES.items() if v[3] == group],
                             hide_index=True, width='stretch')
            continue
        with st.expander(f'{group} · {T.RESTART[group]}', icon=':material/tune:'):
            keys = [k for k, v in T.TUNABLES.items() if v[3] == group and k not in ('paper_max_order_qty', 'paper_max_total_qty')]
            for col, key in zip(st.columns(3) * 4, keys):
                default, low, high, _, label, tip = T.TUNABLES[key]
                help_text = f'{tip} 기본 {default} · 허용 {low}~{high}'.strip()
                if isinstance(default, str):
                    hour, minute = map(int, current[key].split(':'))
                    picked = col.time_input(label, value=datetime(2000, 1, 1, hour, minute).time(), step=60,
                                            key=f'tun_{key}', help=help_text)
                    chosen[key] = f'{picked.hour:02d}:{picked.minute:02d}' if picked else current[key]
                else:  # no min/max: a value out of range is brought to the edge with a Korean note on save
                    chosen[key] = col.number_input(label, value=current[key], step=1 if isinstance(default, int) else 0.05,
                                                   key=f'tun_{key}', help=help_text)
    changed = {k: v for k, v in chosen.items() if v != current[k]}
    if st.button('운영 조정값 저장', type='primary', icon=':material/save:', disabled=not changed, key='tun_save'):
        try:
            saved = T.save(changed)
            moved = [f"{T.TUNABLES[k][4]} {changed[k]}→{v}" for k, v in saved.items() if v != changed[k]]
            st.session_state['tunables_flash'] = ('저장했습니다. ' + (f"범위를 벗어난 값은 맞췄습니다: {', '.join(moved)}. " if moved else '')
                                                  + '실행기 값은 해당 실행기를 다시 시작해야 반영됩니다.')
            st.rerun()
        except (OSError, ValueError) as exc:
            st.error(f'저장하지 못했습니다: {exc}')
    st.markdown('##### 설정 변경 이력')
    rows = config_store.history(300)
    if not rows:
        st.caption('아직 기록된 변경이 없습니다. 이제부터 계좌·매매 기준·제한 규칙·운영 조정값·화면 설정·AI 설정·관심종목을 바꾸면 여기에 남습니다.')
        return
    kinds = sorted({r[1] for r in rows})
    label = lambda k: HISTORY_KINDS.get(k) or ('관심종목 편집' if k.startswith('watchlist') else k)
    picked = st.pills('종류', kinds, selection_mode='multi', default=kinds, format_func=label, key='history_kinds') or kinds
    show = lambda v: '—' if v is None else str(v).strip('"')
    table_frame = __import__('dialogs').table_frame
    table_frame([{'시각': r[0][5:16].replace('T', ' '), '종류': label(r[1]), '항목': r[2], '이전': show(r[3]), '이후': show(r[4]), '출처': r[5]}
                 for r in rows if r[1] in picked][:300])
    st.caption('settings-history.sqlite3 · 최신순 300건. 값 자체는 각 JSON 파일에 있고, 여기에는 무엇이 언제 바뀌었는지만 남습니다.')


def position_zone(average, current, stop=2.0, take=2.0):
    """Price change versus the broker average and its place between the stop (-stop%) and the final take-profit (+take%)."""
    change = (current / average - 1) * 100
    label = (f'▲ 수익 구간 {change:+.2f}%' if change > 0 else
             f'▼ 손실 구간 {change:+.2f}%' if change < 0 else '■ 보합 0.00%')
    return change, label, min(1.0, max(0.0, (change + stop) / (stop + take)))


RETIRED_SETTINGS = {'oversold_vibe_min', 'oversold_v2_vibe_veto'}  # read only when vibe was a main engine (gone 2026-11-17): kept, not shown
STRATEGY_HELP = {
    'entry_confidence': ('Jev가 "이 종목을 사도 좋다"고 고를 때 붙이는 확신 정도(0~1)입니다. 이 값 이상일 때만 1차 매수를 합니다.',
                         '높이면(예: 0.7) 확신이 강할 때만 사서 거래가 줄고, 낮추면(예: 0.5) 거래가 늘지만 근거가 약한 신호도 따릅니다. '
                         '11/11에는 Jev 진입 판단 93번 중 0.6 이상이 15번, 0.7 이상이 2번이었습니다. 수익 확률이 아닙니다.'),
    'sell_confidence': ('보유 종목에 대해 Jev가 "지금 팔자(SELL)"고 할 때의 확신 정도입니다. 이 값 이상이면 가격과 상관없이 전량 팝니다.',
                        '낮추면 Jev 의견으로 일찍 파는 일이 늘고, 높이면 주로 아래 익절·손절 가격 규칙으로만 팝니다. '
                        '11/11 보유 의견의 최고 신뢰도는 0.5였습니다.'),
    'take_profit_half_pct': ('평균 매입가보다 이만큼 오르면 그때 남은 수량의 절반을 팔아 수익을 먼저 확정합니다.',
                             '예: 평균 10,000원, 1.0%면 10,100원 이상에서 절반 매도. 2차 익절보다 작아야 합니다.'),
    'take_profit_all_pct': ('평균 매입가보다 이만큼 오르면 남은 수량을 모두 팝니다.',
                            '예: 평균 10,000원, 2.0%면 10,200원 이상에서 전량 매도. 높이면 더 큰 상승을 기다리지만 되밀릴 위험이 커집니다.'),
    'stop_loss_pct': ('평균 매입가보다 이만큼 내려가면 손실을 더 키우지 않도록 전량 팝니다.',
                      '예: 평균 10,000원, 2.0%면 9,800원 이하에서 전량 매도. 넓히면 흔들림에 덜 팔리지만 한 번에 잃는 금액이 커집니다. '
                      '보유 중에는 현재 국면과 적용 중인 손절 계획도 함께 확인합니다.'),
    'trend_half_pct': ('상승장이거나 지수가 강한 날(추세형)에 쓰는 1차 익절 기준입니다. 방어형의 데이터 목표 대신 이 값에서 절반을 팝니다.',
                       '예: 2.0이면 평균가 +2%에서 절반. 레버리지 2배 ETF는 1.5배(+3%)로 잡습니다. 기본 1차 익절보다 낮게 저장할 수 없습니다.'),
    'trend_trail_pct': ('추세형에서는 고정 전량 익절 대신, 1차 익절 가격에 한 번이라도 닿은 뒤 최고가에서 이만큼 내려오면 남은 수량을 팝니다.',
                        '예: 최고 10,300원, 1.5%면 10,145원 이하에서 매도. 넓히면 더 길게 들고 가지만 고점에서 더 많이 돌려줍니다. 레버리지 2배는 1.5배.'),
    'overnight_max_manwon': ('장 마감 전 정리 때 다음 날까지 들고 갈 수 있는 금액(매입가 기준, 만원)의 합계입니다. 0이면 항상 모두 정리합니다.',
                             '기본은 당일 정리입니다. 전략 구조 탭의 종가매매 조건 11개(상승장 강세일, 시장 폭, 외국인·기관·프로그램 순매수, '
                             '1차 익절 달성, 거래량, 종가 강도, 종목 추세, Jev 의견, 한도, 다음 날 거래일)를 모두 충족한 종목만 이 한도 안에서 보유합니다.'),
    'add_step_pct': ('분할 추가 매수의 가격 간격입니다.',
                     '적용 중인 실행기 정책과 완료 분봉, 보유 및 주문 보호 조건을 함께 확인합니다.'),
    'swing_max_days': ('종가매매로 넘긴 추세형 종목을 강세장에서 최대 며칠(거래일)까지 이어서 들고 갈지입니다.',
                       '이틀째부터는 스윙 지속 조건(지수 국면이 받쳐 줌 · 종목 상승 추세 · Jev 매도 의견 없음 · 한도)만 매일 확인합니다. '
                       '1이면 하룻밤만 보유합니다. 기본 5.'),
    'strength_top_n': ('Jev가 대기할 때 관심종목 중 Jev 선정 순위 몇 위까지를 강도 진입 후보로 볼지입니다. 0이면 관심종목 강도 진입을 끕니다.',
                       '높이면 후보가 많아져 거래가 늘고, 낮추면 Jev가 가장 유력하다고 본 종목만 봅니다. 기본 3.'),
    'strength_min_prob': ('후보로 인정할 Jev 선정 확률의 최소값입니다. 확률은 후보끼리 나눠 갖는 상대 점수(합계 1)라 후보가 많을수록 작아집니다.',
                          '순위 안에 들어도 이 값보다 낮으면 근거가 약한 것으로 보고 뺍니다. 0.6처럼 높게 잡으면 한 종목만 넘을 수 있습니다. 기본 0.15.'),
    'strength_change_min': ('오늘 전일 종가 대비 최소 이만큼은 올라 있어야 "스스로 강한 종목"으로 봅니다.',
                            '낮추면 덜 오른 종목도 사서 빨리 들어가지만 힘이 약한 종목이 섞입니다. 기본 2%.'),
    'strength_change_max': ('이보다 많이 오른 종목은 상한가(+30%) 근처라 사지 않습니다.',
                            '높이면 급등주를 더 따라가지만 고점 매수·변동성 위험이 커집니다. 최소 상승 %보다 커야 합니다. 기본 25%.'),
    'ipo_days': ('상장 후 며칠(거래일)까지를 신규주로 볼지입니다. 1이면 상장 당일만, 2면 당일과 다음 날입니다. 0이면 신규주 진입을 끕니다.',
                 '상장 직후 며칠은 거래량과 변동이 커서 짧게 크게 움직입니다. 늘리면 기회가 늘지만 관심이 식은 종목도 섞입니다. 기본 2.'),
    'ipo_size_pct': ('신규주는 변동이 커서 1차 매수액(약 100만 원)의 몇 %만 살지 정합니다.',
                     '예: 50이면 약 50만 원. 높이면 수익과 손실이 함께 커집니다. 기본 50.'),
    'ipo_near_high_pct': ('지금가가 오늘 고가에서 이 % 안에 있을 때만 삽니다(고가 근처 = 매수세가 살아 있음).',
                          '좁히면 가장 강한 순간에만, 넓히면 눌림목에서도 삽니다. 기본 3.'),
    'ipo_stop_pct': ('신규주 전용 손절 %입니다. 평균 매입가 대비 이만큼 내려가면 전량 매도합니다.',
                     '신규주는 1분에 몇 %씩 움직여 일반 손절(2%)보다 조금 넓게 둡니다. 기본 3.'),
    'ipo_half_pct': ('평균 매입가 대비 이만큼 오르면 절반을 먼저 팔아 이익을 확정합니다.',
                     '그 뒤 나머지는 본전 아래로 내려가거나 고점에서 추적 손절 %만큼 밀리면 팝니다. 기본 3.'),
    'ipo_trail_pct': ('절반 익절 뒤 남은 수량을, 보유 중 최고가에서 이만큼 내려오면 팝니다.',
                      '넓히면 큰 상승을 더 길게 타지만 고점에서 더 많이 돌려줍니다. 기본 2.'),
    'weak_half_pct': ('약세장(하락장이면서 오늘 강세가 아닌 날)에는 늦어도 이 수익에서 절반을 팝니다. 데이터 목표가 더 낮으면 그 값을 씁니다.',
                      '하락장에서는 오른 만큼을 빨리 확정하는 것이 중요합니다. 절반을 판 뒤 나머지는 본전 아래로 내려오면 팝니다. 기본 1.'),
    'weak_all_pct': ('약세장에서 남은 수량을 늦어도 이 수익에서 모두 팝니다.', '기본 2. 절반 익절보다 커야 합니다.'),
    'direction_switch': ('Jev가 반대 방향(예: 롱 보유 중 인버스)을 진입 기준 이상으로 고르고, 보유 종목이 평균가 아래이며 시장이 그 보유에 불리하면(약세장) 보유 종목을 정리합니다.',
                         '1이면 켬, 0이면 끔. 정리 뒤 다음 주기부터 새 방향 진입을 검토합니다. 이익 중인 보유는 정리하지 않습니다.'),
    'entry_volume_ratio': ('모든 진입 경로(Jev · 관심종목 강도 · 신규주)에서, 최근 5분 거래량이 그 전 5분의 몇 배 이상이어야 사는지입니다. 0이면 거래량을 보지 않습니다.',
                           '선택지: 엄격 1.5 · 기본 1.2 · 완화 1.0(거래량이 줄지만 않으면) · 끄기 0. 낮추면 거래가 늘지만, 거래량이 받쳐 주지 않는 상승(힘 빠진 반등)에도 들어갑니다. '
                           '오후처럼 거래가 한산한 시간에는 1.2를 넘기 어려워 진입이 거의 없습니다.'),
    'reentry_guard': ('그날 판 종목(익절 포함)은 관심종목 강도 경로로 다시 살 때 직전 매도가보다 비쌀 때만 삽니다.',
                      '11/13 SFA반도체 재진입 −31,100원, 원익QnC 익절 뒤 매도가 아래 재매수 시도에서 나왔습니다. 제한 규칙 탭에서 끕니다.'),
    'losing_streak_guard': ('오늘 왕복 거래가 3번 연속 손실이면 마지막 청산 뒤 30분 동안 새로 사지 않습니다.', '제한 규칙 탭에서 끕니다.'),
    'averaging_down_guard': ('하락장의 약세·혼조일(인버스는 상승장 강세일)에는 하락 후 추가 매수를 하지 않습니다.', '제한 규칙 탭에서 끕니다.'),
    'opposite_guard': ('롱을 보유 중이면 인버스를, 인버스를 보유 중이면 롱을 새로 사지 않습니다.', '끄면 방향 전환(손실 보유 정리)도 일어나지 않습니다. 제한 규칙 탭에서 끕니다.'),
    'direction_confirm': ('Jev가 고른 종목은 그 지수가 같은 방향일 때만 삽니다. 시장 요약이 없으면 사지 않습니다.', '끄면 거래량·분봉 확인만 남습니다. 제한 규칙 탭에서 끕니다.'),
    'rising_check': ('후보의 현재가가 5분 전보다 높을 때만 삽니다(모든 진입 경로).', '끄면 거래량·지수 조건만으로 삽니다. 제한 규칙 탭에서 끕니다.'),
    'ipo_add_guard': ('신규주는 떨어져도 추가 매수를 하지 않습니다.', '제한 규칙 탭에서 끕니다.'),
    'ipo_max_positions': ('신규주를 한 번에 이 수까지만 보유합니다.', '제한 규칙 탭에서 1~3으로 정합니다.'),
    'ipo_slot_on': ('켜면 Jev가 ETF를 고른 분에도 신규주 조건을 따로 보고, 인버스를 들고 있어도 신규주는 반대 방향 차단에서 뺍니다(2026-11-17).',
                    '끄면 예전처럼 Jev가 대기할 때만 신규주를 보고 반대 방향 차단도 적용합니다. 실계좌 미러는 신규주도 모의를 그대로 따라 삽니다(2026-11-19).'),
    'ipo_daily_max': ('하루에 신규주를 이 횟수까지만 삽니다.', '1~10회, 기본 2회.'),
    'ipo_time_stop_min': ('신규주를 산 뒤 이 시간(분)이 지나면 손익과 관계없이 팝니다(상장일 급변 대비).', '0이면 끔, 기본 60분.'),
    'strength_slot_on': ('켜면 Jev가 ETF를 고른 분에도 관심종목 강도 후보를 따로 보고 삽니다(병렬 슬롯, 2026-11-17). 인버스를 들고 있으면 반대 방향 차단 때문에 기다립니다.',
                         '기본 꺼짐. 2026-11-17 사용자가 켬, 11-30 strength_shadow 10거래일 결과로 유지 여부를 다시 봅니다.'),
    'strength_slot_max': ('강도 슬롯으로 산 종목을 한 번에 이 수까지만 보유합니다.', '1~5, 기본 2.'),
    'strength_slot_daily': ('강도 슬롯으로 하루에 이 횟수까지만 삽니다.', '1~10, 기본 3.'),
    'entry_cutoff_hhmm': ('이 시각부터 새로 사지 않습니다(분할 추가 매수 포함).', '제한 규칙 탭에서 10:00~15:00으로 정합니다.'),
    'flatten_hhmm': ('이 시각부터 보유를 팝니다(종가매매 조건 충족 종목만 예외).', '제한 규칙 탭에서 14:00~15:05로 정합니다. 실행기는 정리 뒤 20분(최대 15:18)까지 돌아갑니다.'),
    'oversold_stock_drop_pct': ('개별주가 전일 종가보다 오늘 저가 기준 이만큼 빠져야 낙폭과대로 봅니다.', '리서치: 6% 미만 하락은 반등 성적이 없었습니다(11/13, 2년 일봉).'),
    'oversold_etf_drop_pct': ('KODEX 200 같은 1배 ETF의 낙폭 기준입니다.', '지수 ETF는 −6% 이상이 드물어 기회가 적습니다.'),
    'oversold_lev_drop_pct': ('2배 ETF의 낙폭 기준입니다(2배 ETF 포함을 켰을 때만).', '리서치에서 2배 ETF는 어떤 설정에서도 이익이 나지 않아 기본은 꺼짐입니다.'),
    'oversold_atr_mult': ('낙폭이 평소 하루 변동폭(최근 20일 ATR)의 몇 배 이상이어야 하는지입니다.', '2026년은 변동성이 커서 ATR이 5.5~11%라 이 조건이 주로 결정합니다. 0이면 % 기준만 봅니다.'),
    'oversold_calm_min': ('하락이 멈췄다고 보려면 최근 몇 분 동안 저가를 새로 쓰지 않아야 하는지입니다.', '짧으면 떨어지는 칼을 잡기 쉽습니다(알테오젠 11/9 사례).'),
    'oversold_rebound_pct': ('저가에서 이만큼 올라와야 삽니다.', ''),
    'oversold_size_pct': ('한 번에 사는 금액(분할 1회 금액 대비 %)입니다.', '리서치상 최악 손실이 개별주 −4.5%, 2배 ETF −10%라 기본은 1배입니다. 회당·하루 매수 한도 안에서만 삽니다.'),
    'oversold_tp_pct': ('매수가 대비 이만큼 오르면 전량 익절합니다.', '낙폭 회복 기준과 둘 중 낮은 값이 목표가입니다.'),
    'oversold_recover_pct': ('전일 종가와 저가 사이 낙폭의 몇 %가 회복되면 익절할지입니다.', '하루 안에 회복되는 비율은 평균 14% 정도라 보통은 익절 %가 먼저 닿습니다.'),
    'oversold_stop_pct': ('당일 저가보다 이만큼 더 빠지면 손절합니다.', '하락이 이어지면 반등 가정이 틀린 것입니다.'),
    'oversold_hold_days': ('목표에 못 닿아도 이 거래일 수까지는 장 마감 정리에서 빼고 들고 갑니다.', '리서치: 반등은 대부분 1~3일에 걸쳐 나왔습니다. 0이면 당일 정리합니다.'),
    'oversold_jev_min': ('Jev가 시황·뉴스·수급·애널리스트 의견을 보고 BUY를 이 신뢰도 이상으로 골라야 삽니다.', ''),
    'oversold_v2_on': ('1이면 선별 v2 조건의 시장 폭락일에 모의계좌가 실제로 삽니다(엔진 확인 없이 규칙대로).', '사례 재현에서 나온 가설을 모의매매로 시험하는 경로입니다(2026-11-13).'),
    'oversold_v2_from_hhmm': ('이 시각부터 선별 v2 매수를 봅니다. 신규 매수 마감 뒤에도 이 경로만 삽니다.', '재현(폭락일 18일, 60분봉): 14~15시 매수가 3일 뒤 +2.02%로 가장 좋았고 9시 매수는 +0.47%였습니다.'),
    'oversold_v2_until_hhmm': ('이 시각까지 선별 v2 매수를 봅니다.', '실행기는 장 마감 정리 뒤 20분(최소 15:00)까지 돌아갑니다.'),
    'oversold_v2_add_on': ('1이면 첫날 절반만 사고, 다음 거래일 매수 창에 첫 매수가보다 싸고 손절가보다 위면 나머지 절반을 삽니다.', '재현: 다음 날 2차 매수가 모든 조합에서 성적을 올렸습니다.'),
    'oversold_v2_half_tp_pct': ('평균가 대비 이만큼 오르면 절반을 팝니다.', '나머지는 나머지 익절 %에서 팝니다.'),
    'oversold_v2_veto_conf': ('Jev가 모든 데이터(차트·뉴스·수급·시황·애널리스트·과거 사례)를 보고 WAIT를 이 신뢰도 이상으로 고르면 사지 않습니다.', '1.0이면 거부하지 않습니다. 종목 고유 악재·애널리스트 하향·위기 확산 같은 분명한 이유일 때 WAIT를 고르도록 묻습니다.'),
    'oversold_v2_boost_pct': ('리서치가 강하게 동의하면(Jev BUY 0.8 이상) 매수 크기를 이 %로 키웁니다.', '근거 데이터가 없어 기본은 100%(키우지 않음)입니다.'),
    'oversold_v2_index_drop_pct': ('코스피(KODEX 200)·코스닥(KODEX 코스닥150) 저가가 전일 대비 이만큼 빠지면 폭락일입니다.', '최근 6개월 −8% 이상 폭락일 14번 평균 +4.8%(5일).'),
    'oversold_v2_capit_drop_pct': ('직전 5일 하락 조건과 함께 볼 지수 낙폭입니다(투매 국면).', ''),
    'oversold_v2_prior5_pct': ('지수가 직전 5거래일 동안 이만큼 이미 빠진 뒤의 급락이면 투매로 봅니다.', '재현: 직전 5일 −8% 이상 뒤 급락 +2.6%, 첫 하락은 −2.2%.'),
    'oversold_v2_rel_max_pct': ('종목이 시장보다 이만큼(%p) 넘게 더 빠졌으면 종목 고유 악재로 보고 사지 않습니다.', '2배 ETF는 시장 낙폭의 2배와 비교합니다.'),
    'oversold_market_drop_pct': ('폭락일에만 매수: 그 종목의 시장(코스피는 KODEX 200, 코스닥은 KODEX 코스닥150)의 오늘 저가가 전일 종가보다 이만큼 이상 빠진 날에만 장중 반등을 삽니다. 시장은 멀쩡한데 종목만 빠진 날은 엔진에 묻지 않고 사지 않으며 기록(market_not_falling)만 남깁니다. 0이면 이 조건 없이 종목 낙폭만 봅니다.',
                                 '재현(최근 6개월): 지금 규칙은 모든 낙폭에서 하루 평균 −0.34%(오른 날 40%), 시장 폭락일에만 +0.53%(62%)였습니다. 종목 혼자 빠진 날은 어떤 방식도 +0.2%를 넘지 못했습니다. 사례집 제안값 3%.'),
    'oversold_v2_jev_sell_on': ('켜면 수익 중인 선별 v2를 Jev 보유 판단 매도(Jev 매도 신뢰도 이상)로 팝니다. 손실 중에는 손절 규칙만 따릅니다.', '끄면 Jev 판단은 기록만 하고 손절·익절·보유일로만 팝니다(2026-11-13 규칙).'),
    'oversold_v2_trail_at_pct': ('평균가보다 이만큼 오른 적이 있으면 고점 되밀림 매도를 시작합니다.', '0이면 쓰지 않습니다. 낮추면 작은 수익도 지키지만 일찍 팔릴 수 있습니다.'),
    'oversold_v2_trail_pct': ('최고 매수호가에서 이만큼 내려오면 전량 팝니다.', '좁히면 흔들림에 일찍 팔리고, 넓히면 수익을 더 돌려줍니다.'),
    'oversold_v2_stop_pct': ('평균 매입가보다 이만큼 빠지면 전량 손절하고, 저점이 확인되면 다시 삽니다(사용자 결정 2026-11-17).', '재현(일봉·5분봉)에서는 넓은 손절보다 평균이 낮았습니다. 운용 결과로 다시 봅니다.'),
    'oversold_v2_reentry_max': ('손절 뒤 저점에서 다시 사는 횟수(포지션당)입니다. 0이면 다시 사지 않습니다.', '재매수마다 Jev가 다시 판단해 거부하면 사지 않습니다.'),
    'oversold_v2_reentry_hold_min': ('손절 뒤 새 저점이 이 시간 동안 깨지지 않아야 다시 삽니다.', ''),
    'oversold_v2_reentry_bounce_pct': ('그 저점보다 이만큼 올라와 있어야 다시 삽니다.', ''),
    'oversold_v2_tp_pct': ('평균가 대비 이만큼 오르면 남은 물량을 모두 팝니다.', ''),
    'oversold_v2_hold_days': ('이 거래일 수가 지나면 장 마감 정리에서 팝니다(주말·휴장일은 세지 않음).', '재현: 3거래일 보유가 최악의 날을 −6% 안팎으로 묶었습니다.'),
    'oversold_v2_size_pct': ('한 번에 사는 금액(분할 1회 금액 대비 %)입니다.', '회당·하루 매수 한도와 보유 금액 한도 안에서만 삽니다.'),
    'oversold_v2_max_new': ('하루에 선별 v2로 새로 사는 종목 수입니다.', ''),
}


@dialogs.dialog('매매 기준 쉽게 보기')
def strategy_guide():
    import guide_ui as GU
    dialogs.wide()
    GU.style()
    settings = strategy_settings()[0]
    pct = lambda key: settings[key] / 100
    GU.lead('실행기는 **1분마다** 아래 순서로 판단합니다. 숫자는 지금 적용 중인 값입니다.')
    GU.steps([
        ('살지 정하기 (1차 매수)', f"Jev가 고른 종목의 신뢰도가 **{settings['entry_confidence']:.2f} 이상**이고 진입·보호 조건을 통과하면 계좌에 정한 "
                             '분할 1회 금액으로 삽니다. 보유 종목과 반대 방향(레버리지↔인버스)인 종목은 사지 않습니다. 보유 종목의 추가 매수는 별도 조건을 확인합니다.'),
        ('조건에 맞으면 나눠 사기', split_policy_summary()),
        ('팔기', f"평균 매입가보다 **+{settings['take_profit_half_pct']:g}%** → 산 수량의 **절반** 매도\n"
               f"**+{settings['take_profit_all_pct']:g}%** → **남은 전량** 매도\n"
               f"**−{settings['stop_loss_pct']:g}%** → **전량 손절**\n"
               f"Jev가 보유 종목에 **SELL**을 **{settings['sell_confidence']:.2f} 이상**으로 말하면 → 전량 매도")])
    price = 10_000
    GU.section('예시 — 10,000원에 첫 매수했다면', '추가 매수 가격에 따라 평균 매입가가 바뀌며, 익절·손절 가격도 그 평균을 기준으로 계산됩니다.')
    GU.table([('절반 익절 (평균 10,000원 기준)', f"{price * (1 + pct('take_profit_half_pct')):,.0f}원 이상"),
              ('전량 익절', f"{price * (1 + pct('take_profit_all_pct')):,.0f}원 이상"),
              ('전량 손절', f"{price * (1 - pct('stop_loss_pct')):,.0f}원 이하")], head=('상황', '가격'))
    GU.section('항목별 설명')
    GU.table([(STRATEGY_LABELS[key], f'{settings[key]:g}', f'{STRATEGY_DEFAULTS[key]:g}',
               f'{STRATEGY_LIMITS[key][0]:g}~{STRATEGY_LIMITS[key][1]:g}', what + (f'\n{detail}' if detail else ''))
              for key, (what, detail) in STRATEGY_HELP.items()], head=('항목', '지금', '기본', '허용', '설명'))
    GU.note('가격은 1분마다 확인하므로 실제 체결가가 정확히 기준가라는 보장은 없습니다. 수수료·세금은 반영하지 않습니다. '
            '저장한 값은 실행기의 다음 주기(약 1분 안)부터 적용되며 모의계좌에만 적용됩니다.', 'warn')


PRESET_NOTES = {
    '보수적': '가장 유력한 1종목만, 확률 0.30 이상, +3~20% 오른 경우. 거래는 드물지만 근거가 가장 강할 때만 삽니다.',
    '기본': '상위 3종목, 확률 0.15 이상, +2~25%. 여러 강한 종목을 차례로 살 수 있게 한 기본값입니다.',
    '적극적': '상위 5종목, 확률 0.10 이상, +1~29%. 기회를 넓게 잡지만 근거가 약하거나 급등 막바지인 종목도 섞일 수 있습니다.',
    '끄기': 'Jev가 7개 ETF 중 하나를 확신할 때만 삽니다. 관심종목 개별주는 Jev가 직접 고를 때만 매수합니다.',
}


def save_preset(settings, values, name):
    try:
        write_settings(get_runtime_root() / 'nhplug' / 'shared-etf-paper', {**settings, **values})
        st.toast(f'"{name}"를 저장했습니다. 실행기의 다음 주기(약 1분 안)부터 적용됩니다.', icon=':material/check_circle:')
        st.rerun()
    except (OSError, ValueError) as exc:
        st.error(f'저장하지 못했습니다: {exc}')


def strength_presets(settings, prefix=''):
    """Ready-made choices for the watchlist strength entry as colour cards, with what each one changes."""
    from strategy_settings import STRENGTH_PRESETS
    import settings_view as sv
    sv.preset_cards('관심종목 강도 진입 · 빠른 선택', '오늘 강하게 오르는 관심종목을 Jev 순위로 따라 사는 경로입니다. 고른 카드의 값 4개만 바꾸고 나머지 매매 기준은 그대로 둡니다.',
                    STRENGTH_PRESETS,
                    lambda v: [('Jev 상위', f"{int(v['strength_top_n'])}위까지" if v['strength_top_n'] else '끔'),
                               ('확률 최소', f"{v['strength_min_prob']:.2f}"),
                               ('오늘 등락', f"+{v['strength_change_min']:g}~{v['strength_change_max']:g}%")],
                    PRESET_NOTES, settings, lambda name: save_preset(settings, STRENGTH_PRESETS[name], name), f'{prefix}strength_')
    st.caption('판단 순서와 예시는 "시장·관심종목" 탭의 "관심종목 강도 진입은 어떻게 고르나요?" 또는 가이드 센터 → 관심종목 진입')


OVERSOLD_NOTES = {
    '보수적': '개별주 −8%·ETF −7%·평소 변동폭 2배 이상, 30분간 저가 갱신이 없을 때만. 당일 정리, Jev 0.6. 가장 깊은 투매만 줍습니다.',
    '기본': '개별주·KODEX 200 −6%와 변동폭 1.5배, 20분 안정 후 +1.5% 익절·저가 −0.5% 손절, 하루까지 보유. 리서치에서 그나마 성적이 난 값입니다.',
    '적극적': '−4%(ETF −3%)·변동폭 1배부터, 크기 2배, +3% 익절, 2배 ETF 포함. 기회는 늘지만 리서치상 승률이 떨어지는 구간입니다.',
    '끄기': '낙폭과대 반등 매매를 하지 않습니다(감시 기록도 남기지 않음).'}


def oversold_presets(settings, prefix=''):
    """낙폭과대 반등 choices as colour cards (user 2026-11-13)."""
    from strategy_settings import OVERSOLD_PRESETS
    import settings_view as sv
    sv.preset_cards('낙폭과대 반등 · 빠른 선택', '탄탄한 종목·지수 ETF가 평소 변동폭보다 훨씬 크게 빠지고 하락이 멈췄을 때 사서 낙폭 일부가 회복되면 바로 파는 경로입니다. '
                    'Jev가 시황·뉴스·수급·애널리스트 의견을 보고 확인해야 삽니다.',
                    OVERSOLD_PRESETS,
                    lambda v: [('발동', f"개별주 −{v['oversold_stock_drop_pct']:g}% · ETF −{v['oversold_etf_drop_pct']:g}% · 변동폭 {v['oversold_atr_mult']:g}배")
                               if v['oversold_on'] else ('발동', '끔'),
                               ('익절·손절', f"+{v['oversold_tp_pct']:g}% · 저가 −{v['oversold_stop_pct']:g}%"),
                               ('크기·보유', f"분할 1회의 {v['oversold_size_pct']:g}% · {int(v['oversold_hold_days'])}거래일")],
                    OVERSOLD_NOTES, settings, lambda name: save_preset(settings, OVERSOLD_PRESETS[name], name), f'{prefix}oversold_')


def oversold_watch_view(root):
    """Today's 낙폭과대 반등 watch: every code that fell far enough (oversold_scan), the engines' answers and the research."""
    import oversold
    from research_store import history
    names = oversold.universe(root)
    today = datetime.now(KST).strftime('%Y-%m-%d')
    from engine_compare import recent_events as journal_tail  # [] before the runner writes its first event
    events = [e for e in journal_tail(root / 'events.jsonl', 20000) if str(e.get('time', '')).startswith(today)
              and e.get('event') in ('oversold_scan', 'oversold_signal')]
    latest = {}
    for e in events:
        latest.setdefault(e.get('symbol'), {})[e['event']] = e
    from market_view import number  # a journaled number can be null (quote unknown): '—', never a TypeError
    with st.expander(f"낙폭과대 감시 · 오늘 발동 {len(latest)}종목 (감시 {len(names)}종목)", icon=':material/south_east:', expanded=bool(latest)):
        if latest:
            rows = []
            for code, item in latest.items():
                scan, signal = item.get('oversold_scan') or {}, item.get('oversold_signal') or {}
                analyst = scan.get('analyst') or {}
                rows.append({'시각': str((signal or scan).get('time', ''))[11:16], '종목': names.get(code, code),
                             '낙폭': f"{number(scan.get('drop_pct'), '+.2f')}%", '평소 변동폭 배수': scan.get('atr_multiple'),
                             '하락 멈춤': '예' if scan.get('stabilized') else '아니오', '저가 대비': f"+{number(scan.get('bounce_pct'), '.2f')}%",
                             '애널리스트': (f"의견 {analyst.get('recomm_mean')} · 목표 {number(analyst.get('target_mean'), ',.0f')}원"
                                         if analyst.get('target_mean') else '—'),
                             '엔진 판단': (f"Jev {signal.get('choice')} {number(signal.get('confidence'), '.2f')}" if signal.get('engine') == 'jev'
                                       else f"vibe {signal.get('score')}" if signal.get('engine') == 'vibe' else '—'),
                             '매수': '예' if signal.get('permitted') else '아니오'})
            st.dataframe(rows, hide_index=True, width='stretch')
        else:
            st.caption('오늘은 감시 종목 중 낙폭과대 기준까지 빠진 종목이 없습니다. 감시 종목과 기준은 계좌·자동매매 → 모의계좌 → 매매 기준에서 바꿉니다.')
        try:
            from oversold_outcomes import summary
            pooled = summary(60)
        except Exception:
            pooled = {'rows': []}
        if pooled['rows']:
            st.markdown(f"**사후 결과 · 최근 {len(pooled['sessions'])}거래일 {pooled['events']}건** (지금 규칙으로 다시 계산 · 비용 0.15% 반영 · Yahoo 5분봉)")
            kinds = {'stock': '개별주', 'etf': '1배 ETF', 'lev': '2배 ETF'}
            st.dataframe([{'날짜': r['day'], '종목': r.get('name', r['code']), '구분': kinds.get(r['kind'], r['kind']),
                           '상태': '발동' if r['status'] == 'triggered' else '근접', '낙폭': f"{r['drop_pct']:+.2f}%",
                           '변동폭 배수': r.get('atr_multiple'), '나스닥 전일': (f"{r['nasdaq_prev_pct']:+.2f}%" if r.get('nasdaq_prev_pct') is not None else '—'),
                           '규칙 결과': ('—' if not r.get('rule') else
                                      f"{r['rule']['return_pct']:+.2f}% ({ {'target': '익절', 'stop': '손절', 'close': '종가'}.get(r['rule']['exit'], r['rule']['exit']) })"
                                      + (' · 다음 날 대기' if r['rule'].get('pending_next_day') else '')),
                           '최악': (f"{r['rule']['adverse_pct']:+.2f}%" if r.get('rule') else '—'),
                           'Jev 매수': '예' if (r.get('jev') or {}).get('bought') else '', 'vibe 매수': '예' if (r.get('vibe') or {}).get('bought') else ''}
                          for r in reversed(pooled['rows'])], hide_index=True, width='stretch', height=320)
            groups = pooled['groups']
            st.caption(' · '.join(f"{kinds.get(k.split(':')[0])} {'발동' if k.endswith('triggered') else '근접'} {g['n']}건"
                                  + (f" (규칙 {g['simulated']}건 승 {g['wins']} · 평균 {g['mean_return_pct']:+.2f}% · 최악 {g['worst_pct']:+.2f}%)" if g['simulated'] else '')
                                  for k, g in groups.items()) + '. 장 마감 뒤 학습 관찰기가 매일 채웁니다(scripts/oversold_outcomes.py).')
        oversold_case_library(names)
        oversold_replay_view()
        research = next(iter(history('oversold_research', 1, root / 'market-research.sqlite3')), None)
        if research:
            st.caption(f"기준값 근거: scripts/oversold_research.py ({str(research.get('time', ''))[:10]}) · 2년 일봉과 60일 5분봉. "
                       '깊게 빠진 개별주·KODEX 200에서만 약한 반등 우위가 있었고 2배 ETF는 없었습니다. 표본이 작아 수익을 보장하지 않습니다.')


def longterm_algorithm_view():
    """장기투자 알고리즘 · 구조 (user 2026-11-17, docs/longterm-dashboard-plan.md L1): architecture, the day's algorithm and the
    regime state machine drawn by Archify from the saved settings and today's record (longterm_flow.py), read-only."""
    import longterm_flow as LF
    import strategy_map as smap
    import dialogs as dlg
    import settings_view as sv
    import strategy_view as sview
    import vibe_longterm_view as LV
    nhplug = get_runtime_root() / 'nhplug'
    root = nhplug / 'vibe-longterm'
    current, now = LF.state(root), datetime.now(KST)
    sc.row([*LF.status(current, now), LV.regime_card(current['regime'])])  # same places as the Jev view: status, details, regimes, routes
    dlg.buttons([('자세히 · 운용 기준 전체', 'lt_d_rules', dlg.table_dialog,
                  ('장기투자 운용 기준', LF.rule_rows(current), '저장된 설정과 엔진 고정값입니다. 설정은 장기투자 탭 → 전략·계좌·실행 탭에서 바꿉니다.')),
                 ('자세히 · 국면별 대응', 'lt_d_regimes', dlg.table_dialog,
                  ('국면별 대응', LF.regime_rows(current), '위에서부터 먼저 맞는 국면이 원시 국면이고, 확정 일수만큼 이어져야 확정 국면이 바뀝니다. ▶ 지금 · ◌ 확정 대기.')),
                 ('자세히 · 참고 데이터', 'lt_d_sources', dlg.table_dialog,
                  ('장기 엔진이 읽는 데이터', LF.sources(current, root, now), '자료가 기준보다 오래되거나 없으면 오른쪽 "기준" 열대로 처리합니다.'))],
                columns=3)
    st.info(LF.summary(current), icon=':material/savings:')
    names = ['아키텍처', '하루 알고리즘', '국면 전이', '거장 모듈', '구성 요소 · 오늘 운영 상태']
    tabs = st.tabs(names, key='lt_flow_tab', on_change='rerun')
    notes = {'architecture': '초록 굵은 경로는 오늘 자료가 계획·가상 계좌·마감까지 가는 흐름입니다. 태그는 마지막 기록 기준입니다.',
             'algorithm': '초록 굵은 경로는 오늘 판단이 실제로 지나간 단계입니다(스토리 모듈이 꺼져 있거나 주문이 없으면 그 단계를 건너뜀).',
             'regimes': '● 지금 국면, ◌ 확정 대기 국면입니다. 같은 새 국면이 확정 일수만큼 이어져야 국면이 바뀝니다.'}
    notes['module_map'] = '실선 = 켜진 모듈, 점선 = 꺼진 모듈. 화살표는 그 모듈이 주로 움직이는 자산 슬리브입니다.'
    notes['today_flow'] = '✓ 오늘 끝남 · △ 이전 판단 사용 · ✗ 빠짐 · … 아직 · — 계획만. 초록 경로는 오늘 끝난 단계입니다.'

    def draw(name):
        import guide_ui as GU
        diagram = getattr(LF, name)(current)
        GU.flow(f'longterm-{name}', diagram, LF.dot(diagram))
        st.caption('Archify 도식 · ' + notes[name] + ' 설정은 "장기투자" 탭 → 전략·계좌·실행 탭에서 바꿉니다.')

    for tab, name in zip(tabs[:3], ('architecture', 'algorithm', 'regimes')):
        if tab.open:
            with tab:
                draw(name)
    if tabs[3].open:
        with tabs[3]:
            draw('module_map')
            st.markdown(f'**{LF.module_summary(current)}**')
            rows = pd.DataFrame(LF.modules(current))
            on = lambda row: [('background-color: rgba(22,163,74,.12)' if row['운용'] == '켜짐' else 'color: #94a3b8')] * len(row)
            st.dataframe(rows.style.apply(on, axis=1)
                         .map(lambda v: 'color: #16a34a; font-weight: 700' if v == '켜짐' else '', subset=['운용'])
                         .map(lambda v: 'color: #d97706; font-weight: 700' if v else '', subset=['국면 추천']),
                         hide_index=True, width='stretch')
            st.caption('초록 행은 지금 켜진 모듈, 흐린 행은 꺼진 모듈, 주황 ★은 오늘 국면의 추천입니다. 수동 모드에서는 켜진 모듈만 쓰고, '
                       '자동 실제 적용일 때만 추천 조합을 씁니다.')
    if tabs[4].open:
        with tabs[4]:
            draw('today_flow')
            left, right = st.columns([3, 2])
            left.dataframe(LF.components(current), hide_index=True, width='stretch')
            right.dataframe(LF.today(current), hide_index=True, width='stretch')
            right.caption('조회 전용입니다. 주문·설정은 바꾸지 않습니다.')

    sv.inject()
    st.markdown(sview.CSS, unsafe_allow_html=True)
    st.markdown('##### 국면별 대응')
    reg, L_REGIMES = current['regime'], LF.L.REGIMES
    card = lambda k, badge, on: sv.card_html(  # noqa: E731
        L_REGIMES[k][0], [('조건', L_REGIMES[k][1]), ('확정', f"{LF.confirm_need(k, current['settings'])}거래일"),
                          ('추천 모듈', LF._masters(['bogle_core', *L_REGIMES[k][2]]))],
        LV.REGIME_COLOR[k], LV.REGIME_ICON[k], badge, on, wide=True)
    if reg.get('regime') in L_REGIMES:
        cols = st.columns(2)
        cols[0].markdown(card(reg['regime'], '지금 확정 국면', True), unsafe_allow_html=True)
        if reg.get('pending') in L_REGIMES:
            cols[1].markdown(card(reg['pending'], f"확정 대기 {reg.get('pending_days', 0)}일째", False), unsafe_allow_html=True)
    else:
        st.caption('첫 판단(평일 08:45) 뒤에 지금 국면이 표시됩니다.')
    with st.expander('다른 국면의 대응 보기'):
        for col, k in zip(st.columns(3) * 2, L_REGIMES):
            col.markdown(card(k, None, False), unsafe_allow_html=True)
    st.markdown('##### 매매 경로')
    st.caption('켜짐은 그 거장 모듈이 지금 설정에서 켜져 있다는 뜻이며, 오늘 주문이 실행된다는 뜻은 아닙니다. 조건 문장은 엔진 모듈 규칙 그대로입니다.')
    routes = LF.route_cards(current)
    for row in (routes[:3], routes[3:]):
        for col, (title, items, color, icon, on) in zip(st.columns(3), row):
            col.markdown(sv.card_html(title, items, color if on else '#64748b', icon, '켜짐' if on else '꺼짐', on, wide=True),
                         unsafe_allow_html=True)


def oversold_algorithm_view(root, rules):
    """낙폭과대 알고리즘 한눈에 (user 2026-11-13): the whole flow with the live settings, every component (what it does now,
    its data, settings and code) and today's operating state, from one definition (oversold_flow.py)."""
    import oversold_flow as of
    st.markdown('##### 낙폭과대 알고리즘 · FLOW')
    flow, parts, state = st.tabs(['FLOW 도식', '구성 요소', '오늘 운영 상태'])
    with flow:
        import guide_ui as GU
        GU.flow('oversold-v2', of.flow_spec(rules), of.flow_dot(rules))
        left, right = st.columns(2)
        with left:
            GU.flow('oversold-rebound', of.rebound_spec(rules), of.rebound_dot(rules))
        with right:
            GU.flow('oversold-loop', of.loop_spec(rules), of.loop_dot(rules))
        st.caption('Archify 도식 · 굵은 길이 기본 흐름, 아래 줄(대기·거부)은 사지 않거나 끝내는 길입니다. 상자 안 숫자는 지금 저장된 설정이라 '
                   '매매 기준을 바꾸면 도식도 같이 바뀝니다.')
    with parts:
        import settings_view as sv
        colors = {'① 감지': ('#f59e0b', 'radar'), '② 판단': ('#22c55e', 'psychology'), '③ 진입': ('#ef4444', 'shopping_cart'),
                  '④ 관리': ('#3b82f6', 'tune'), '⑤ 청산': ('#3b82f6', 'logout'), '⑥ 기록·학습': ('#a855f7', 'school')}
        cols = st.columns(3)
        for i, x in enumerate(of.stages(rules)):
            color, icon = colors.get(x['phase'], ('#64748b', 'circle'))
            cols[i % 3].markdown(sv.card_html(f"{x['phase']} · {x['name']}", [('하는 일', x['what']), ('데이터', x['data']),
                                                                             ('설정', x['keys']), ('코드', x['code'])], color, icon, wide=True),
                                 unsafe_allow_html=True)
        st.caption('값 바꾸기: 계좌·자동매매 → 모의계좌 → 매매 기준 → "낙폭과대 선별 v2"(17개)와 "낙폭과대 반등", 감시 종목. '
                   '근거: docs/oversold-case-study.md 2-1(선별 조건)·2-2(진입 시점·분할 매수·분할 익절·손절·보유일). '
                   '설정을 바꾸면 다음 주기부터 새 진입에 적용되고, 이미 산 포지션은 매수 때 정한 값을 지킵니다.')
    with state:
        from market_view import number  # a journaled number can be null (quote unknown): '—', never a TypeError
        from engine_compare import recent_events as journal_tail  # [] before the runner writes its first event
        gates, signals = of.today_events(root, read=lambda path: journal_tail(path, 20000))
        held = of.positions(root)
        last = gates[-1] if gates else {}
        lows = {n: (last.get(k) or {}).get('low_pct') for k, n in (('kospi', '코스피'), ('kosdaq', '코스닥'))}
        cols = st.columns(4)
        cols[0].metric('오늘 시장 판정', '통과' if last.get('passed') else ('통과 못 함' if last else '판정 전'),
                       ' · '.join(f'{n} {low:+.1f}%' for n, low in lows.items() if type(low) in (int, float))  # unknown: left out
                       or f"{of._hhmm(rules['oversold_v2_from_hhmm'])}부터 판정", delta_color='off')
        vetoed = sum(e.get('research') == 'veto' for e in signals)
        cols[1].metric('오늘 후보', f'{len(signals)}종목', f'리서치 거부 {vetoed}', delta_color='off')
        cols[2].metric('보유 중 선별 v2', f'{len(held)}종목', f"2차 매수 {sum('2차' in h['stage'] for h in held)}", delta_color='off')
        cols[3].metric('전날 밤 나스닥', f"{last['nasdaq_prev']:+.2f}%" if type(last.get('nasdaq_prev')) in (int, float) else '—')
        if held:
            st.dataframe([{'종목': h['code'], '1차 매수일': h['opened'], '보유 거래일': h['sessions'], '남은 거래일': h['left'], '단계': h['stage'],
                           '절반 익절': f"+{h['half_pct']:g}%", '나머지 익절': f"+{h['all_pct']:g}%", '손절 (평균가 대비)': f"−{h['stop_pct']:g}%"}
                          for h in held], hide_index=True, width='stretch')
        if signals:
            labels = {'veto': '거부', 'boost': '강한 동의', 'normal': '보통'}
            st.dataframe([{'시각': str(e.get('time', ''))[11:16], '종목': e.get('symbol'), '낙폭': f"{number(e.get('drop_pct'), '+.2f')}%",
                           '시장 저가': f"{number(e.get('market_low_pct'), '+.2f')}%", '리서치': labels.get(e.get('research'), '—'),
                           '엔진 판단': (f"Jev {e.get('choice')} {number(e.get('confidence'), '.2f')}" if e.get('engine') == 'jev'
                                     else f"vibe {e.get('score')}" if e.get('engine') == 'vibe' else '—')} for e in signals],
                         hide_index=True, width='stretch')
        if not held and not signals:
            st.caption('오늘은 선별 v2 후보와 보유가 없습니다. 판정은 매수 창이 열리면 매분 하고, 통과할 때만 후보·리서치·주문으로 넘어갑니다.')


def oversold_case_library(names):
    """낙폭과대 사례집: how past sharp falls went on, by kind and depth, and a what-if lookup for one code."""
    try:
        import oversold_cases as oc
        cases = oc.load()
    except Exception:
        cases = []
    if not cases:
        st.caption('과거 사례집이 아직 없습니다. `scripts/oversold_cases.py --collect`로 만들고, 장 마감 뒤 학습 관찰기가 최근 40일을 매일 갱신합니다.')
        return
    domestic = [c for c in cases if c['kind'] in ('stock', 'etf', 'lev')]
    st.markdown(f"**과거 사례집 · 국내 {len(domestic):,}건** :gray[(Yahoo 일봉 최대 10년 · 대형주·ETF {len({c['code'] for c in domestic})}종목 · "
                "전일 종가 대비 저가 개별주 −5%·1배 ETF −3%·2배 ETF −6% 이상인 날 · 종가 매수 기준, 비용 미반영 · "
                f"해외 지수·원유·코인 {len(cases) - len(domestic):,}건은 아래 재현 기록에서)]")
    labels = {'stock': '개별주', 'etf': '1배 ETF', 'lev': '2배 ETF'}
    fmt = lambda v, sign=True: '—' if v is None else (f'{v:+.2f}%' if sign else f'{v}%')  # noqa: E731
    for tab, kind in zip(st.tabs(list(labels.values())), labels):
        with tab:
            st.dataframe([{'저가 낙폭': label, '사례': row['n'], '다음 날': fmt(row.get('r1')), '3일': fmt(row.get('r3')),
                           '5일': fmt(row.get('r5')), '10일': fmt(row.get('r10')), '3일 오른 비율': fmt(row.get('win_r3'), False),
                           '반등 (5일 +3%)': fmt(row.get('rebound'), False), '추가 하락 (저가 −3%)': fmt(row.get('further_fall'), False),
                           '저가에서 3일 (최선)': fmt(row.get('r3_from_low'))}
                          for label, row in oc.breakdown(cases, kind) if row['n']], hide_index=True, width='stretch')
    st.caption('값은 사례의 중앙값입니다. 반등은 5일 안에 저가를 깨지 않고 5일 뒤 +3% 이상, 추가 하락은 5일 안에 그날 저가보다 3% 더 빠진 경우입니다.')
    codes = {**{c: n for c, n, _, _ in oc.UNIVERSE}, **names}
    left, middle, right = st.columns([2, 1, 1])
    code = left.selectbox('종목으로 비슷한 과거 사례 찾기', list(codes), format_func=lambda c: f'{codes[c]} ({c})', key='oversold_case_code')
    drop = middle.number_input('종목 저가 낙폭 %', value=-6.0, step=0.5, key='oversold_case_drop')
    market = right.number_input('시장 저가 낙폭 %', value=-1.0, step=0.5, key='oversold_case_market',
                                help='코스피(코스닥 종목은 코스닥)의 오늘 저가. 시장 전체가 같이 빠졌는지가 과거 결과를 가장 크게 가릅니다.')
    view = oc.similar(code, oc.live_features(code, min(drop, 0.0), mkt_low_pct=min(market, 0.0)))
    if not view.get('n'):
        st.caption('이 종류의 사례가 부족합니다.')
        return
    cols = st.columns(4)
    cols[0].metric('비슷한 사례', f"{view['n']}건", f"같은 종목 {view['same_code']}건", delta_color='off')
    cols[1].metric('3일 뒤 (중앙값)', fmt(view.get('r3_med')), f"오른 비율 {view['win_r3_pct']}%", delta_color='off')
    cols[2].metric('반등', f"{view['rebound_pct']}%", '5일 뒤 +3% 이상', delta_color='off')
    cols[3].metric('추가 하락', f"{view['further_fall_pct']}%", '5일 안에 저가 −3%', delta_color='off')
    st.caption(' · '.join(view.get('examples') or []) + ' — Jev도 판단마다 이 형식의 과거 사례를 참고합니다.')


REPLAY_GROUPS = {'국내 대형주': 'stock', '국내 1배 ETF': 'etf', '국내 2배 ETF': 'lev', '코스피·코스닥 지수': 'kr_index',
                 '미국 지수 (S&P500·나스닥·반도체)': 'us_index', '원유 (WTI·브렌트)': 'oil', '코인 (비트코인·이더리움·솔라나)': 'crypto',
                 '국내 상장 해외 ETF (나스닥100·S&P500·WTI)': 'overseas_etf'}


def oversold_replay_view():
    """낙주매매 재현 기록: every past fall replayed with the current rule and the variants, by market, period and the
    선별 v2 hypothesis (scripts/oversold_replay.py; the daily refresh keeps recent cases current)."""
    try:
        import oversold_cases as oc
        import oversold_replay as rp
        cases = oc.load()
    except Exception:
        cases = []
    if not any(c.get('replay') for c in cases):
        return
    settings = strategy_settings()[0]  # the same 선별 v2 thresholds the runner uses (4th review)
    samples = rp.v2_samples(cases, settings=settings)
    passed = [r for r in samples if r['passed']]
    done = [r['stocks_5d'] for r in passed if r['stocks_5d'] is not None]
    st.markdown(f"**선별 v2 표본** :gray[({rp.SAMPLES_FROM}부터 매매와 별도로 매일 쌓음 · 장 마감 뒤 사례집 갱신)]")
    cols = st.columns(4)
    cols[0].metric('지수 급락일', f'{len(samples)}일')
    cols[1].metric('선별 v2 통과', f'{len(passed)}일', '표본 비교에 활용', delta_color='off')
    cols[2].metric('통과일 대형주 5일 평균', f'{statistics.mean(done):+.2f}%' if done else '—', f'결과 나온 {len(done)}일', delta_color='off')
    cols[3].metric('통과 못 한 날', f'{len(samples) - len(passed)}일')
    if samples:
        fmt2 = lambda v: '—' if v is None else f'{v:+.2f}%'  # noqa: E731
        st.dataframe([{'날짜': r['day'], '시장': r['market'], '지수 저가': fmt2(r['drop_pct']), '직전 5일': fmt2(r['prior_5d']),
                       '전날 나스닥': fmt2(r['nasdaq_prev']), '선별 v2': '✅ 통과' if r['passed'] else '— 밖',
                       '지수 5일 뒤': fmt2(r['index_5d']), '통과 대형주 5일 평균': fmt2(r['stocks_5d'])} for r in samples],
                     hide_index=True, width='stretch')
    st.markdown('**낙주매매 재현 기록** :gray[(급락한 날 여러 방식으로 샀다면 · 비용 반영 · 하루 단위 평균은 같은 날 사례를 먼저 평균)]')
    left, middle, right = st.columns([2, 1, 1])
    group = left.selectbox('대상', list(REPLAY_GROUPS), key='replay_group')
    period = middle.selectbox('기간', ['최근 6개월', '최근 1년', '최근 3년', '10년 전체'], key='replay_period')
    kind = REPLAY_GROUPS[group]
    gate = right.selectbox('선별 v2 (가설)', ['전체', '통과', '통과 못 함'], key='replay_gate', disabled=kind not in rp.KR_KINDS,
                           help='지수 −8% 이상, 또는 직전 5일 −5% 뒤 −5% 이상 폭락이면서 전날 나스닥이 오르지 않았고, '
                                '종목이 시장보다 4%p 넘게 더 빠지지 않은 날. 2026-05~11 재현에서 나온 가설이며 매매 규칙이 아닙니다.')
    since = (datetime.now(KST) - timedelta(days={'최근 6개월': 183, '최근 1년': 365, '최근 3년': 1096}.get(period, 3700))).strftime('%Y-%m-%d')
    markets = rp.market_days(cases)
    rows = [c for c in cases if c['kind'] == kind and c['day'] >= since]
    if kind in rp.KR_KINDS and gate != '전체':
        rows = [c for c in rows if rp.gate_v2(c, markets, settings) is (gate == '통과')]
    table = rp.table(rows)
    if not table:
        st.caption('이 조건의 사례가 없습니다.')
        return
    fmt = lambda v: '—' if v is None else f'{v:+.2f}%'  # noqa: E731
    best = max(table, key=lambda r: r['day_mean'])['strategy']
    st.dataframe([{'방식': ('★ ' if r['strategy'] == best else '') + r['label'], '사례': r['n'], '날 수': r['days'],
                   '하루 평균': fmt(r['day_mean']), '오른 날': f"{r['day_win']}%", '사례 평균': fmt(r['mean']), '중앙값': fmt(r['median']),
                   '최악의 날': fmt(r['worst_day']), '보유 중 평균 최저': fmt(r['mae'])} for r in table],
                 hide_index=True, width='stretch')
    st.caption('★ 하루 평균이 가장 높은 방식입니다. 지금 규칙은 국내 종목의 장중 분봉(5분봉 60일·60분봉 2년)이 있는 사례만 재현됩니다. '
               '대형주는 지금 살아남은 종목이라 오래된 기간일수록 결과가 좋게 나옵니다.')
    recent = sorted(rows, key=lambda c: c['day'], reverse=True)[:80]
    ret = lambda c, k: ((c.get('replay') or {}).get(k) or {}).get('ret')  # noqa: E731
    with st.expander(f'사례 목록 · 최근 {len(recent)}건', icon=':material/list:'):
        st.dataframe([{'날짜': c['day'], '종목': c['name'], '저가': fmt(c['drop_pct']), '시장 저가': fmt(c.get('mkt_low_pct')),
                       '전날 나스닥': fmt(c.get('nasdaq_prev')),
                       '선별 v2': {True: '✅ 통과', False: '— 밖', None: ''}[rp.gate_v2(c, markets, settings)],
                       '지금 규칙': fmt(((c.get('intraday') or {}).get('rule') or {}).get('return_pct')),
                       '종가→5일': fmt(ret(c, 'close_5d')), '종가·저가−5% 손절': fmt(ret(c, 'close_5d_low5')),
                       '다음 날 시가→5일': fmt(ret(c, 'next_open_5d')),
                       '그 뒤': oc.AFTER.get(c.get('after'), c.get('after'))} for c in recent],
                     hide_index=True, width='stretch', height=360)


def oversold_universe_editor(prefix=''):
    """The watched codes for 낙폭과대 반등 (oversold-universe.json, change history recorded)."""
    import oversold
    root = get_runtime_root() / 'nhplug' / 'shared-etf-paper'
    codes = oversold.universe(root)
    st.markdown('**낙폭과대 감시 종목**  \n:gray[실적·애널리스트 평가가 탄탄해 크게 빠지면 되돌아올 가능성이 큰 종목·지수 ETF만 넣으세요.]')
    keep = st.multiselect('감시 종목', list(codes), default=list(codes), format_func=lambda c: f'{codes[c]} {c}',
                          key=f'{prefix}oversold_keep', label_visibility='collapsed')
    settings = strategy_settings()[0]
    on_col, lev_col = st.columns(2)
    on = on_col.toggle('낙폭과대 반등 매매', value=bool(settings['oversold_on']), key=f'{prefix}oversold_on',
                       help='끄면 감시·매수를 모두 하지 않습니다. 이미 산 종목은 정해 둔 목표가·손절가로 팝니다.')
    lev = lev_col.toggle('2배 ETF 포함', value=bool(settings['oversold_lev_on']), key=f'{prefix}oversold_lev_on',
                         help='리서치(11/13)에서 2배 ETF는 어떤 설정에서도 이익이 나지 않아 기본은 꺼짐입니다.')
    if (int(on), int(lev)) != (int(settings['oversold_on']), int(settings['oversold_lev_on'])):
        save_preset(settings, {'oversold_on': int(on), 'oversold_lev_on': int(lev)}, '낙폭과대 스위치')
    code_col, name_col, add_col = st.columns([1, 2, 1], vertical_alignment='bottom')
    new_code = code_col.text_input('종목코드', key=f'{prefix}oversold_new_code', placeholder='예: 005380')
    new_name = name_col.text_input('이름', key=f'{prefix}oversold_new_name', placeholder='예: 현대차')
    if add_col.button('감시 종목 저장', key=f'{prefix}oversold_save', type='primary', width='stretch', icon=':material/save:'):
        chosen = {c: codes[c] for c in keep}
        if new_code.strip():
            if not re.fullmatch(r'\d{6}', new_code.strip()):
                st.error('종목코드는 6자리 숫자입니다.')
                return
            chosen[new_code.strip()] = new_name.strip() or new_code.strip()
        try:
            oversold.save_universe(chosen, root)
            st.toast('낙폭과대 감시 종목을 저장했습니다. 실행기의 다음 주기부터 적용됩니다.', icon=':material/check_circle:')
            st.rerun()
        except (OSError, ValueError) as exc:
            st.error(f'저장하지 못했습니다: {exc}')


VOLUME_NOTES = {'엄격': '거래가 확실히 늘 때만(1.5배). 진입은 드물지만 힘이 실린 상승만 삽니다.',
                '기본': '1.2배. 지금까지 쓰던 값입니다.',
                '완화': '1.0배. 거래량이 줄지만 않으면 삽니다. 한산한 오후에도 진입이 생깁니다.',
                '끄기': '거래량을 보지 않습니다. 다른 조건(가격·분봉)만으로 삽니다.'}


def volume_presets(settings, prefix=''):
    """Quick choice for the entry volume check that every entry path shares, as colour cards."""
    from strategy_settings import VOLUME_PRESETS
    import settings_view as sv
    sv.preset_cards('진입 거래량 확인 · 빠른 선택', 'Jev · 관심종목 강도 · 신규주 모든 진입에 적용됩니다. 최근 5분 거래량 ÷ 그 전 5분 거래량의 기준입니다.',
                    VOLUME_PRESETS, lambda v: [('거래량 배수', f'{v:g}배 이상' if v else '보지 않음')], VOLUME_NOTES, settings,
                    lambda name: save_preset(settings, {'entry_volume_ratio': VOLUME_PRESETS[name]}, name), f'{prefix}volume_')


IPO_NOTES = {
    '보수적': '상장 당일만, 30% 크기, 고가 2% 안, 손절 2%·절반 익절 3%·추적 1.5%. 가장 강한 순간에 작게 들어가 빨리 나옵니다.',
    '기본': '상장 후 2거래일, 50% 크기, 고가 3% 안, 손절 3%·절반 익절 3%·추적 2%.',
    '적극적': '상장 후 3거래일, 70% 크기, 고가 5% 안, 손절 4%·절반 익절 5%·추적 3%. 큰 시세를 노리지만 한 번에 잃는 금액도 큽니다.',
    '끄기': '신규주 진입을 하지 않습니다.',
}


def ipo_presets(settings, prefix=''):
    """Ready-made choices for the new-listing entry as colour cards, with what each one changes."""
    from strategy_settings import IPO_PRESETS
    import settings_view as sv
    sv.preset_cards('신규주(새로 상장한 종목) 진입 · 빠른 선택', '상장 직후 고가 근처에서 강한 신규주를 작게 사는 경로입니다. 고른 카드의 값 6개만 바꿉니다.',
                    IPO_PRESETS,
                    lambda v: [('상장 후', f"{int(v['ipo_days'])}거래일" if v['ipo_days'] else '끔'), ('크기', f"분할 1회의 {v['ipo_size_pct']:g}%"),
                               ('고가 대비', f"{v['ipo_near_high_pct']:g}% 안"), ('손절', f"−{v['ipo_stop_pct']:g}%"),
                               ('절반 익절', f"+{v['ipo_half_pct']:g}%"), ('추적 손절', f"−{v['ipo_trail_pct']:g}%")],
                    IPO_NOTES, settings, lambda name: save_preset(settings, IPO_PRESETS[name], name), f'{prefix}ipo_')
    st.caption('판단 순서는 "시장·관심종목" 탭의 신규주 패널 또는 가이드 센터 → 신규주 진입')


SIZING_SETTINGS = (  # position_sizing (user 2026-11-17)
    ('size_holding_on', '보유 비중이 크면 덜 사기', '한 종목 보유가 종목당 한도의 절반을 넘으면 매수 크기를 (1 − 보유 비율)로 줄입니다(최소 25%). 한 종목 쏠림을 막습니다.'),
    ('size_market_on', '시황 방향에 맞춰 크기 조절', '지수 국면이 매수 방향과 같으면 100%, 횡보장이거나 반대 방향(하락장 일반 매수·상승장 인버스)이면 아래에서 정한 비율만 삽니다. 끄면 늘 100%입니다.'),
    ('regime_trim_on', '시황 역전 시 절반 매도', '산 뒤 지수 국면이 보유 방향과 반대로 바뀌면 포지션당 한 번 절반을 팝니다. 익절·손절·Jev 매도는 그대로입니다.'),
)
SCALE_SETTINGS = (
    ('scale_signal_add_on', '새 매수 판단으로 추가 매수', '새 Jev 매수 판단이 진입·보호 조건을 통과할 때 보유 종목을 추가 매수합니다.'),
    ('scale_step_add_on', '가격 단계 추가 매수', '가격 단계와 엔진 확인 조건을 만족할 때 추가 매수합니다. Jev의 새 판단이 필요합니다.'),
    ('scale_profit_rebuy_on', '일부 익절 후 재매수', '익절 이력이 확인된 보유 종목의 추가 매수를 허용합니다. 끄면 익절 후 두 추가 매수 경로가 모두 중단됩니다.'),
)


def market_size_line(values, tranche=None):
    """What one ordinary first buy comes to in each regime with these 시황 축소 values and the saved 분할 1회 금액."""
    if tranche is None:
        import accounts
        tranche = (accounts.load().get('paper-shared') or {}).get('budget', {}).get('first_buy_krw') or 0
    if not values.get('size_market_on'):
        return f'시황 축소가 꺼져 있어 국면과 관계없이 분할 1회 {tranche:,}원을 삽니다(수량은 내림).'
    flat, against = values['size_market_flat_pct'], values['size_market_against_pct']
    return (f'분할 1회 {tranche:,}원 기준 일반 종목 첫 매수: 상승장 {tranche:,}원 · 횡보장 {int(tranche * flat / 100):,}원 · '
            f'하락장 {int(tranche * against / 100):,}원까지(수량은 내림). 인버스는 하락장이 100%이고 인버스 전용 축소가 더해집니다.')


def scale_settings_status(settings, jev_applied):
    """Show saved and recorded runner settings separately; missing runner evidence is not treated as applied."""
    def describe(values):
        flags = ' · '.join(f"{label}: " + ('켜짐' if values.get(key) == 1 else '꺼짐' if values.get(key) == 0 else '미확인')
                           for key, label, _ in SCALE_SETTINGS)
        interval = values.get('scale_interval_min')
        duration = f'{int(interval)}분' if type(interval) in (int, float) and math.isfinite(interval) and 1 <= interval <= 60 else '미확인'
        return f'{flags} · 단계 추가 간격: {duration}'
    saved_values = {key: settings.get(key, 1) for key, _, _ in SCALE_SETTINGS}
    saved_values['scale_interval_min'] = settings.get('scale_interval_min', 5)
    saved = '저장된 설정: ' + describe(saved_values)
    def recorded(label, events):
        if not events or not isinstance(events[-1].get('settings'), dict):
            return f'{label} 실행기 적용 기록: 미확인'
        latest = events[-1]
        stamp = event_time(latest.get('time'))
        at = f'{stamp:%m/%d %H:%M:%S}' if stamp else '시각 미확인'
        return f'{label} 실행기 최근 기록 {at}: ' + describe(latest['settings'])
    return saved, recorded('Jev', jev_applied)


def strategy_form(events, prefix=''):
    """Edit the runner's thresholds; the runner applies them from its next cycle."""
    settings, status = strategy_settings()
    applied = [e for e in events if isinstance(e, dict) and e.get('event') == 'strategy_settings']
    head, guide = st.columns([3, 1], vertical_alignment='center')
    head.markdown('##### 매매 기준 설정')
    if guide.button('매매 기준 쉽게 보기', key=f'{prefix}strategy_guide', width='stretch'):
        strategy_guide()
    if applied:
        last = applied[-1]
        st.caption(f"실행기 적용 확인: {str(last.get('time', ''))[11:19]} · "
                   + ' · '.join(f"{STRATEGY_LABELS[k]} {v:g}" for k, v in (last.get('settings') or {}).items() if k in STRATEGY_LABELS))
    else:
        st.caption('실행기가 아직 설정 적용 기록을 남기지 않았습니다(다음 장중 주기에 기록).')
    strength_presets(settings, prefix)
    oversold_presets(settings, prefix)
    oversold_universe_editor(prefix)
    ipo_presets(settings, prefix)
    volume_presets(settings, prefix)
    steps = {'oversold_market_drop_pct': 0.5, 'entry_confidence': 0.05, 'sell_confidence': 0.05, 'overnight_max_manwon': 50.0, 'swing_max_days': 1.0,
             'strength_top_n': 1.0, 'strength_min_prob': 0.05, 'strength_change_min': 0.5, 'strength_change_max': 1.0,
             'ipo_days': 1.0, 'ipo_size_pct': 10.0, 'ipo_near_high_pct': 0.5, 'ipo_stop_pct': 0.5, 'ipo_half_pct': 0.5, 'ipo_trail_pct': 0.5,
             'weak_half_pct': 0.1, 'weak_all_pct': 0.1, 'direction_switch': 1.0, 'entry_volume_ratio': 0.1,
             'oversold_v2_on': 1.0, 'oversold_v2_from_hhmm': 5.0, 'oversold_v2_hold_days': 1.0, 'oversold_v2_size_pct': 10.0,
             'oversold_v2_max_new': 1.0, 'oversold_v2_stop_pct': 0.5, 'oversold_v2_tp_pct': 1.0, 'oversold_v2_until_hhmm': 5.0,
             'oversold_v2_jev_sell_on': 1.0, 'oversold_v2_trail_at_pct': 0.5, 'oversold_v2_trail_pct': 0.5,
             'oversold_v2_add_on': 1.0, 'oversold_v2_half_tp_pct': 0.5, 'oversold_v2_veto_conf': 0.05, 'oversold_v2_vibe_veto': 0.05,
             'oversold_v2_boost_pct': 10.0, 'oversold_v2_reentry_max': 1.0, 'oversold_v2_reentry_hold_min': 5.0,
             'oversold_v2_reentry_bounce_pct': 0.1}
    import settings_view as sv
    groups = [('진입 (매수 조건)', '#a855f7', 'login', ['entry_confidence', 'entry_volume_ratio', 'direction_switch']),
              ('방어형 익절 · 손절', '#ef4444', 'savings', ['take_profit_half_pct', 'take_profit_all_pct', 'stop_loss_pct']),
              ('분할 추가 매수', '#f59e0b', 'stacked_line_chart', ['add_step_pct']),
              ('추세형 · 약세장', '#3b82f6', 'trending_up', ['trend_half_pct', 'trend_trail_pct', 'weak_half_pct', 'weak_all_pct']),
              ('Jev 매도 · 이월 · 스윙', '#64748b', 'nightlight', ['sell_confidence', 'overnight_max_manwon', 'swing_max_days']),
              ('관심종목 강도 진입', '#22c55e', 'local_fire_department', ['strength_top_n', 'strength_min_prob', 'strength_change_min', 'strength_change_max']),
              ('신규주 진입', '#06b6d4', 'fiber_new', ['ipo_days', 'ipo_size_pct', 'ipo_near_high_pct', 'ipo_stop_pct', 'ipo_half_pct', 'ipo_trail_pct']),
              ('낙폭과대 반등', '#ec4899', 'south_east', ['oversold_stock_drop_pct', 'oversold_etf_drop_pct', 'oversold_lev_drop_pct',
                                                       'oversold_atr_mult', 'oversold_calm_min', 'oversold_rebound_pct', 'oversold_size_pct',
                                                       'oversold_tp_pct', 'oversold_recover_pct', 'oversold_stop_pct', 'oversold_hold_days', 'oversold_market_drop_pct',
                                                       'oversold_jev_min']),
              ('낙폭과대 선별 v2 (시장 폭락일 · 5일 보유)', '#be185d', 'crisis_alert',
               ['oversold_v2_on', 'oversold_v2_from_hhmm', 'oversold_v2_until_hhmm', 'oversold_v2_index_drop_pct',
                'oversold_v2_capit_drop_pct', 'oversold_v2_prior5_pct', 'oversold_v2_rel_max_pct', 'oversold_v2_add_on',
                'oversold_v2_half_tp_pct', 'oversold_v2_tp_pct', 'oversold_v2_hold_days', 'oversold_v2_stop_pct',
                'oversold_v2_jev_sell_on', 'oversold_v2_trail_at_pct', 'oversold_v2_trail_pct',
                'oversold_v2_reentry_max', 'oversold_v2_reentry_hold_min', 'oversold_v2_reentry_bounce_pct',
                'oversold_v2_size_pct', 'oversold_v2_max_new', 'oversold_v2_veto_conf', 'oversold_v2_boost_pct'])]
    from restrictions import EDITABLE
    scale_keys = {key for key, _, _ in SCALE_SETTINGS} | {'scale_interval_min', 'scale_max_adds', 'add_conf_sizing_on', 'add_size_pct', 'partial_sell_conf', 'partial_sell_pct'}
    scale_keys |= {key for key, _, _ in SIZING_SETTINGS} | {'size_market_flat_pct', 'size_market_against_pct'}
    covered = {k for *_, keys in groups for k in keys} | set(EDITABLE) | {'oversold_on', 'oversold_lev_on'} | scale_keys | RETIRED_SETTINGS
    leftover = [k for k in STRATEGY_DEFAULTS if k not in covered]
    if leftover:
        groups.append(('기타', '#94a3b8', 'tune', leftover))
    # Sliders with a box (user 2026-11-13: every setting is a slider), outside a form so they stay in step. Keys carry
    # the settings file's save time: after any save (here, a preset card or the 제한 규칙 tab) they start from the file.
    path = get_runtime_root() / 'nhplug' / 'shared-etf-paper' / 'strategy-settings.json'
    stamp = path.stat().st_mtime_ns if path.exists() else 0  # ns: two saves within one second still get new widgets
    values = {k: settings[k] for k in (*EDITABLE, 'oversold_on', 'oversold_lev_on', *RETIRED_SETTINGS)}  # set by their cards; reset restores them
    scale_ready = scale_keys <= set(STRATEGY_DEFAULTS)
    if scale_ready:
        with st.container(border=True):
            st.markdown('**분할 매수 경로**')
            st.caption('Jev 공유 모의 실행기에 적용합니다. 실계좌는 각 엔진의 체결을 미러하며 계좌별 금액·수량과 보호 한도를 따릅니다.')
            from engine_compare import recent_events as journal_tail
            from engine_roles import ledger_root
            nhplug = get_runtime_root() / 'nhplug'
            engine_events = {}
            for engine in ('jev',):
                ledger = ledger_root(nhplug, engine)
                source = applied if ledger == nhplug / 'shared-etf-paper' else journal_tail(ledger / 'events.jsonl', 20000)
                engine_events[engine] = [e for e in source if isinstance(e, dict) and e.get('event') == 'strategy_settings']
            for line in scale_settings_status(settings, engine_events['jev']):
                st.caption(line)
            cols = st.columns(len(SCALE_SETTINGS))
            for col, (key, label, help_text) in zip(cols, SCALE_SETTINGS):
                values[key] = float(col.toggle(label, value=bool(settings.get(key, 1)),
                                               key=f'{prefix}strategy_{key}_{stamp}', help=help_text))
            values['scale_interval_min'] = float(num_input(st, '가격 단계 추가 최소 간격 (분)', 1, 60,
                                                          int(settings.get('scale_interval_min', 5)),
                                                          f'{prefix}strategy_scale_interval_min_{stamp}', step=1, slider=True,
                                                          help='가격 단계 추가에만 적용합니다. 새로운 BUY 판단을 통한 추가 매수의 간격을 제한하지 않습니다.'))
            values['scale_max_adds'] = float(num_input(st, '종목당 추가 매수 최대 횟수 (첫 매수 제외)', 0, 100,
                                                      int(settings.get('scale_max_adds', 2)),
                                                      f'{prefix}strategy_scale_max_adds_{stamp}', step=1, slider=True,
                                                      help='새 판단 추가·가격 단계 추가·익절 후 재매수를 모두 합친 횟수입니다. 종목당 최대 금액(계좌·자동매매)에 먼저 닿으면 그 전에 멈춥니다. 0이면 추가 매수를 하지 않습니다.'))
            values['add_conf_sizing_on'] = float(st.toggle('추가 매수는 직전보다 나은 조건일 때만', value=bool(settings.get('add_conf_sizing_on', 1)),
                                                            key=f'{prefix}strategy_add_conf_sizing_on_{stamp}',
                                                            help='새 판단 추가 매수는 직전 매수보다 신뢰도가 0.02 이상 높거나, 평균가보다 한 단계 싸거나, 한 단계 수익 중일 때만 삽니다. 끄면 엔진이 다시 BUY할 때마다 삽니다.'))
            values['add_size_pct'] = float(num_input(st, '추가 매수 크기 (새 판단·가격 단계, 분할 1회 대비 %)', 10, 100, int(settings.get('add_size_pct', 50)),
                                                    f'{prefix}strategy_add_size_pct_{stamp}', step=5, slider=True,
                                                    help='100이면 분할 1회 금액 전부, 50이면 절반을 삽니다. 장 마감 개선안의 추가 매수 감사(add_sizing)가 조정 제안을 냅니다. 11/17 적중률 추적에서 높은 신뢰도가 덜 맞아 50%로 시작했습니다.'))
            c1, c2 = st.columns(2)
            values['partial_sell_conf'] = float(num_input(c1, '엔진 일부 매도 시작 신뢰도', 0.5, 0.95, float(settings.get('partial_sell_conf', 0.55)),
                                                         f'{prefix}strategy_partial_sell_conf_{stamp}', step=0.05, fmt='%.2f', slider=True,
                                                         help='Jev SELL 신뢰도가 이 값 이상이고 전량 매도 기준(Jev 매도 신뢰도) 미만이면 일부만 팝니다. 전량 기준 이상으로 두면 끕니다. vibe는 판단기가 정한 수량으로 팝니다.'))
            values['partial_sell_pct'] = float(num_input(c2, '엔진 일부 매도 비율 (%)', 10, 90, int(settings.get('partial_sell_pct', 33)),
                                                        f'{prefix}strategy_partial_sell_pct_{stamp}', step=1, slider=True,
                                                        help='포지션당 한 번, 마지막 체결이 매수일 때만 팝니다. 판 뒤에는 그 포지션에 추가 매수하지 않습니다.'))
            st.markdown('**비중 조절** · 한 번에 사는 크기를 보유 비중과 시황에 맞춰 분할 1회의 25~100%로 줄이고, 시황이 거꾸로 돌면 절반을 팝니다.')
            for col, (key, label, help_text) in zip(st.columns(len(SIZING_SETTINGS)), SIZING_SETTINGS):
                values[key] = float(col.toggle(label, value=bool(settings.get(key, 1)), key=f'{prefix}strategy_{key}_{stamp}', help=help_text))
            for col, key, label, text in zip(st.columns(2), ('size_market_flat_pct', 'size_market_against_pct'),
                                             ('횡보장 매수 크기 (분할 1회 대비 %)', '반대 방향 매수 크기 (분할 1회 대비 %)'),
                                             ('지수 국면이 횡보장일 때 일반 매수와 인버스 매수의 크기입니다.',
                                              '하락장에서 일반 종목을 살 때와 상승장에서 인버스를 살 때의 크기입니다. 키우면 하락장의 손절 손실도 같은 비율로 커집니다.')):
                values[key] = float(num_input(col, label, 25, 100, int(settings[key]), f'{prefix}strategy_{key}_{stamp}', step=5, slider=True,
                                              help=f'{text} 100이면 줄이지 않습니다(기본 {STRATEGY_DEFAULTS[key]:g}, 허용 25~100). 낙폭과대·선별 v2·신규주에는 적용하지 않습니다.'))
            st.caption(market_size_line(values))
            st.caption('저장 후 다음 거래 회차부터 반영됩니다. 실행 중인 매수 판단에서 설정이 바뀌면 주문 전 확인에 따라 다시 판단합니다. 이미 접수된 주문은 변경되지 않습니다.')
    else:
        st.warning('분할 매수 설정 모듈이 이전 버전입니다. 새 설정을 안전하게 저장하려면 대시보드를 다시 시작해야 합니다.')
    for name, color, icon, keys in groups:
        with st.container(border=True):
            st.markdown(sv.card_html(name, [], color, icon), unsafe_allow_html=True)
            for col, key in zip(st.columns(3) * math.ceil(len(keys) / 3), keys):  # every key, three a row
                low, high = STRATEGY_LIMITS[key]
                tip = f'{STRATEGY_HELP[key][0]} {STRATEGY_HELP[key][1]} (기본값 {STRATEGY_DEFAULTS[key]:g} · 허용 {low:g}~{high:g})'
                if key == 'overnight_max_manwon':  # an amount: 원 ~ 억원 units like the account limits
                    krw = money_input(col, STRATEGY_LABELS[key].replace(' (만원)', ''), int(low * 10_000), int(high * 10_000),
                                      int(settings[key] * 10_000), f'{prefix}strategy_{key}_{stamp}',
                                      st.session_state.get('prefs', PREFS.DEFAULTS)['money_unit'], help=tip + ' 0이면 항상 당일 정리합니다.',
                                      slider=True)
                    values[key] = krw / 10_000
                    continue
                whole = float(steps.get(key, 0.1)).is_integer() and float(low).is_integer() and float(high).is_integer()
                kind = int if whole else float
                values[key] = float(num_input(col, STRATEGY_LABELS[key], kind(low), kind(high), kind(settings[key]),
                                              f'{prefix}strategy_{key}_{stamp}', step=kind(steps.get(key, 0.1)),
                                              fmt=None if whole else '%.2f', help=tip, slider=True))
    with st.expander('항목별 설명'):
        for key in (k for k in STRATEGY_DEFAULTS if k not in EDITABLE and k in STRATEGY_HELP):  # plain-language lines, besides each field's (?) tooltip
            st.caption(f'**{STRATEGY_LABELS[key]}**: {STRATEGY_HELP[key][0]} {STRATEGY_HELP[key][1]}')
    save, reset = st.columns(2)
    saved = save.button('매매 기준 저장', type='primary', key=f'{prefix}strategy_save', width='stretch', disabled=not scale_ready)
    restored = reset.button('기본값으로 되돌리기', key=f'{prefix}strategy_reset', width='stretch', disabled=not scale_ready)
    flash = st.session_state.pop(f'{prefix}strategy_flash', None)
    if flash:
        st.success(flash, icon=':material/check_circle:')
    if saved or restored:
        try:
            write_settings(get_runtime_root() / 'nhplug' / 'shared-etf-paper', STRATEGY_DEFAULTS if restored else values)
            st.session_state[f'{prefix}strategy_flash'] = ('기본값으로 되돌렸습니다. ' if restored else '저장했습니다. ') + \
                '실행기의 다음 주기(약 1분 안)부터 적용됩니다. 이미 낸 주문에는 영향이 없습니다.'
            st.rerun()
        except (OSError, ValueError) as exc:
            st.error(f'저장하지 못했습니다: {exc}')
    st.caption('1차 익절은 2차 익절보다 낮아야 합니다. 손절과 가격 단계 추가 매수는 각각의 조건을 확인하며, 손절이 우선합니다. 실계좌는 계좌별 보호 한도를 따릅니다.')


def rules_card(settings, status):
    """The buy/sell rules the runner is applying, in one glance."""
    with st.container(border=True):
        buy, sell = st.columns(2)
        strength = (f"관심종목 · Jev가 대기할 때 **Jev 선정 상위 {int(settings['strength_top_n'])}위 안(확률 {settings['strength_min_prob']:g}↑) · "
                    f"전일 대비 +{settings['strength_change_min']:g}~+{settings['strength_change_max']:g}% · 분봉 상승 · 거래량 {settings['entry_volume_ratio']:g}배** → 계좌 분할 금액"
                    if settings['strength_top_n'] else '관심종목 강도 진입 **꺼짐** (Jev가 직접 고를 때만 개별주 매수)')
        ipo = (f"신규주 · 상장 후 **{int(settings['ipo_days'])}거래일** 안 · 기준가·시가 위 · 고가 {settings['ipo_near_high_pct']:g}% 안 · 분봉 상승 · 거래량 → "
               f"약 {settings['ipo_size_pct']:g}% 크기, 손절 −{settings['ipo_stop_pct']:g}% · 절반 익절 +{settings['ipo_half_pct']:g}% · "
               f"추적 −{settings['ipo_trail_pct']:g}% · 당일 정리" if settings['ipo_days'] else '신규주 진입 **꺼짐**')
        buy.markdown(f"**매수 기준**  \n"
                     f"1차 · Jev 매수 신뢰도 **{settings['entry_confidence']:.2f} 이상** + 진입·보호 조건 → 계좌 분할 금액  \n"
                     f"{strength}  \n{ipo}  \n"
                     f"{split_policy_summary()}")
        sell.markdown(f"**매도 기준** (평균 매입가 대비)  \n"
                      ":red[▲ **방어형**(추세형이 아닐 때): 종목별 데이터 목표에서 **전량** 익절] (최근 20일 장중 상승폭 중앙값×국면 계수, 0.8~3%)  \n"
                      f"목표 데이터가 없으면 +{settings['take_profit_half_pct']:g}% 절반 · +{settings['take_profit_all_pct']:g}% 나머지  \n"
                      f":blue[▼ **−{settings['stop_loss_pct']:g}%** 전량 손절] · Jev 매도 신뢰도 **{settings['sell_confidence']:.2f} 이상** 전량  \n"
                      f"**약세장**(하락장·강세 아닌 날): 늦어도 **+{settings['weak_half_pct']:g}% 절반 · +{settings['weak_all_pct']:g}% 전량**, 추가 매수 없음"
                      + (" · Jev가 반대 방향을 고르면 손실 중인 보유 정리" if settings['direction_switch'] else '') + "  \n"
                      "절반 익절 뒤 나머지는 **매입가(본전)** 아래로 내려가면 전량  \n"
                      f"**추세형**(상승장·지수 강세일): +{settings['trend_half_pct']:g}% 절반, 나머지는 고점 대비 "
                      f"−{settings['trend_trail_pct']:g}% 추적 손절(2배 ETF ×1.5) · 고정 +{settings['take_profit_all_pct']:g}% 매도 없음")
        import session_times
        _clock = session_times.load(get_runtime_root() / 'nhplug' / 'shared-etf-paper')
        ENTRY_CUTOFF, FLATTEN_AT = _clock['cutoff'], _clock['flatten']
        st.caption(f'하루 마감: {ENTRY_CUTOFF}부터 신규·추가 매수 없음 · {FLATTEN_AT}부터 당일 정리가 기본 · 다음 날 보유는 종가매매 조건 11개를 '
                   f"모두 충족한 종목만 {settings['overnight_max_manwon']:g}만원 한도 안에서, 강세가 이어지면 최대 {int(settings['swing_max_days'])}거래일까지 스윙(전략 구조 탭) · "
                   '방어형↔추세형은 시장 상황으로 정하며 보유 중 추세형으로만 바뀝니다 · '
                   '코스피(코스닥 ETF는 코스닥) 하락장이면서 오늘 약세면 하락 후 추가 매수 중단 · '
                   '매도 기준은 매수 시점 값으로 고정되어 이후 설정 변경은 새 매수부터 적용')
        note = {'default': '승인된 기본값', 'custom': '사용자 설정값', 'invalid': '설정 파일이 잘못돼 기본값 사용 중'}[status]
        st.caption(f'{note} · 바꾸려면 아래 "전략·AI 설정" 탭 · 가격은 1분마다 확인하므로 체결가가 정확히 기준가라는 보장은 없습니다')


def won_compact(value):
    """Compact KRW: 억 for large balances, full digits otherwise (settings_view.won spells amounts out in words)."""
    return '확인 불가' if value is None else f'{value / 1e8:,.2f}억원' if abs(value) >= 1e8 else f'{value:,.0f}원'


def portfolio_overview(snapshot, events, fills, now, books=None, live_stop_pct=None, engine=None, key_prefix='', sell=None):
    """First screen: four key cards, then the positions table with stop/target bars.

    live_stop_pct: a live account's own stop % (its targets follow the engine ledger it mirrors); books=None hides realized P&L."""
    observed = snapshot.get('time') if isinstance(snapshot, dict) else None
    if not (isinstance(observed, datetime) and observed.tzinfo is not None and 0 <= (now - observed).total_seconds() <= 120):
        st.caption('최근 2분 안의 증권사 잔고가 없어 손익 요약을 표시하지 않습니다. 새로고침을 누르세요.')
        return
    if engine is None:
        from engine_roles import main_engine
        engine = main_engine(get_runtime_root() / 'nhplug')
    holdings = [row for row in snapshot.get('holdings', []) if isinstance(row, dict)]  # ETFs and scanner stocks
    today = now.astimezone(KST).date()
    todays = [fill for fill in fills or [] if event_time(fill.get('시각')) is not None and event_time(fill.get('시각')).date() == today]
    buys = [f for f in todays if str(f.get('구분', '')).upper() == 'BUY']
    sells = [f for f in todays if str(f.get('구분', '')).upper() == 'SELL']
    shares = lambda rows: sum(r['수량'] for r in rows if type(r.get('수량')) is int)
    account = snapshot.get('account') or {}
    aggregate = snapshot.get('aggregate')
    cards = []
    if aggregate is not None:
        rate = aggregate['수익률(%)']
        cards.append({'label': '평가손익', 'value': f"{aggregate['평가손익']:+,.0f}원", 'sub': f'수익률 {rate:+.2f}%', 'tone': sc.signed(rate),
                      'icon': 'trending_up' if rate > 0 else 'trending_down' if rate < 0 else 'trending_flat',
                      'help': '증권사 응답의 평가손익입니다. 수익은 빨강, 손실은 파랑입니다. 수수료·세금은 빠져 있습니다.'})
    else:
        cards.append({'label': '평가손익', 'value': '확인 불가', 'tone': 'alert', 'icon': 'error', 'help': '증권사 합계가 없어 계산하지 못했습니다.'})
    cards.append(realized_card(books, today))
    cards.append({'label': '주문가능금액', 'value': won_compact(account.get('주문가능금액')), 'tone': 'ok', 'icon': 'account_balance_wallet',
                  'sub': f"평가 {won_compact(account.get('총평가금액'))} · 매입 {won_compact(account.get('총매입금액'))}",
                  'help': '증권사 잔고 응답 그대로. 주문가능금액은 미결제 대금 반영 방식이 확인되지 않아 총자산으로 합산하지 않습니다.'})
    cards.append({'label': '보유 · 오늘 체결', 'value': f"{len(holdings)}종 {sum(int(r.get('보유수량') or 0) for r in holdings):,}주",
                  'sub': f'오늘 매수 {shares(buys):,}주 · 매도 {shares(sells):,}주', 'icon': 'inventory_2',
                  'tone': 'up' if holdings else 'off', 'pulse': bool(todays),
                  'help': '증권사 기준 보유 종목과, 실행기가 오늘 확인한 체결입니다. 오늘 체결이 있으면 점이 깜박입니다.'})
    rows = comparison_data(events)
    if rows:
        latest = rows[-1]
        if latest.get('jev_holding'):
            label, text, score = '최근 Jev · 보유 의견', latest['jev_holding'], latest.get('jev_holding_confidence')
        else:
            label, text, score = '최근 Jev · 종목 후보', latest.get('jev') or '없음', latest.get('jev_confidence')
        detail = (f'신뢰도 {score:.2f} · ' if type(score) in (int, float) and math.isfinite(score) else '') + f"{latest['time']:%H:%M}"
        cards.append({'label': label, 'value': {'WAIT': '대기'}.get(text, text), 'sub': detail, 'icon': 'psychology',
                      'tone': 'down' if text == 'SELL' else 'off' if text in ('WAIT', '없음', 'HOLD') else 'ai',
                      'help': 'Jev의 가장 최근 의견입니다. 신뢰도는 수익 확률이 아니며, 매수는 진입 조건을 모두 통과해야 실행됩니다.'})
    else:
        cards.append({'label': '최근 Jev 의견', 'value': '기록 없음', 'tone': 'off', 'icon': 'psychology'})
    sc.row(cards, ask='etf')

    if books is None and live_stop_pct is None:
        st.warning('로컬 체결 원장을 읽지 못해 실현손익을 확인할 수 없습니다. 표의 실현손익 0은 미확인입니다.')
    period = None
    head, pick_state, pick_unit, pick_key = st.columns([3, 2, 2, 2], vertical_alignment='bottom')
    head.markdown('##### 종목별 손익 (보유·거래)')
    state = pick_state.segmented_control('상태', list(HOLDING_STATES), default='보유', key=f'{key_prefix}pnl_holding_state') or '보유'
    if books is not None:
        unit = pick_unit.segmented_control('기간', list(PERIOD_UNITS), default='일', key=f'{key_prefix}pnl_period_unit') or '일'
        size = PERIOD_UNITS[unit]
        current = now.astimezone(KST).isoformat()[:size]
        keys = sorted({stamp[:size] for book in books.values() for stamp, *_ in book.get('sales', [])} | {current}, reverse=True)
        key = pick_key.selectbox(f'{unit} 선택', keys, key=f'{key_prefix}pnl_period_{unit}') or current
        period = ('오늘' if unit == '일' and key == current else key, key)
    if sell is not None:  # sell-now buttons above the positions (user 2026-11-17)
        sell()
    pnl_rows = symbol_pnl_rows(holdings, books or {}, stock_names(), period)
    if books is None:  # no local ledger for this account: show broker valuation only, never a fake 0
        pnl_rows = [{k: v for k, v in row.items() if not k.startswith('실현') and k not in ('누적수익률', '합계', '매수·매도 횟수')}
                    for row in pnl_rows]
    pnl_rows = [row for row in pnl_rows if HOLDING_STATES[state](row)]
    if not pnl_rows:
        st.caption({'보유': '지금 보유한 종목이 없습니다.', '미보유': '다 팔았거나 거래만 한 종목이 없습니다.'}.get(state, '보유하거나 거래한 종목이 없습니다.'))
        return
    money = [k for k in pnl_rows[0] if k == '평가손익' or k.startswith('실현손익') or k == '합계']
    rates = [k for k in pnl_rows[0] if k.endswith('수익률') or '수익률(' in k]
    # Pre-formatted text: Streamlit ignores Styler number formats here; colour follows the sign character.
    shown = [{k: ('—' if v is None else f'{v:+,.0f}원' if k in money else f'{v:+.2f}%' if k in rates
                  else f'{v:,.0f}원' if k in ('평균매입가', '현재가') else v)
              for k, v in row.items()} for row in pnl_rows]
    tone = lambda v: ('color:#ef4444' if str(v).startswith('+') and v not in ('+0원', '+0.00%')
                      else 'color:#3b82f6' if str(v).startswith('-') else '')
    st.dataframe(pd.DataFrame(shown).style.map(tone, subset=money + rates), hide_index=True)
    if books is not None:
        st.caption('평가수익률은 보유분 매입금액 대비, 실현수익률은 판 수량의 매입금액 대비입니다. 수수료·세금은 반영하지 않았습니다.')
        history = realized_by_period(books, unit)
        if history:
            with st.expander(f'{unit}별 실현손익 ({len(history)}개 기간)', icon=':material/calendar_month:'):
                shown = [{**row, '실현손익': f"{row['실현손익']:+,.0f}원",
                          '수익률': '—' if row['수익률'] is None else f"{row['수익률']:+.2f}%"} for row in history]
                st.dataframe(pd.DataFrame(shown).style.map(tone, subset=['실현손익', '수익률']), hide_index=True)
    for row in holdings:
        average, current, quantity = row.get('평균매입가'), row.get('현재가'), row.get('보유수량')
        name = row.get('종목') or row.get('종목코드')
        if not all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in (average, current, quantity)):
            st.warning(f'{name}: 수량 또는 매입가를 확인할 수 없습니다.')
            continue
        level = with_levels({'price': average}, row.get('종목코드'), engine, live_stop_pct)
        stop_price, stop_label = level['stop'], level['stop_label']
        if '시황 재사용/오래됨' in stop_label:
            st.warning(f'{name}: 손절 기준 계산에 오래된 시황이 재사용됐습니다. 최신 시황 확인이 필요합니다.')
        if stop_price is None or stop_price >= average:
            st.caption(f'{name} {quantity:,.0f}주 · {stop_label}' + (f' {stop_price:,.2f}원' if stop_price else ''))
            continue
        top = level['all'] or level['half'] or average * (1 + strategy_settings()[0]['take_profit_all_pct'] / 100)
        change, label, gauge = position_zone(average, current, (1 - stop_price / average) * 100, (top / average - 1) * 100)
        st.progress(gauge, text=f"{label} · {name} {quantity:,.0f}주 · {stop_label} {stop_price:,.2f} ◀ 현재 {current:,.0f} ▶ "
                                + targets_text(level['half'], level['all']))
    st.caption(('평가손익은 증권사 응답' + (', 실현손익은 실행기 확인 체결(이동평균 매입가) 기준' if books is not None else '')
                + ' · 수수료·세금 미반영 · 막대의 손절은 시황·계획 기준 참고값이며 본전·추적 청산은 별도 확인'))


def experiment_row(event):
    jev = event.get('jev')
    candidate = jev.get('candidate') if isinstance(jev, dict) else None
    choice = candidate.get('choice') if isinstance(candidate, dict) else event.get('choice')
    return {'시각': event.get('time') or '—', '이벤트': event.get('event') or '—',
            '판단': choice or '—', '판단 출처': event.get('source') or '—', '사유': entry_reason_label(event.get('reason')),
            '오류 유형': event.get('error_type') or '—',
            '종목': event.get('symbol') or '—', '체결': str(event.get('filled', '—')),
            '잔량': json.dumps(event['remaining'], ensure_ascii=False) if isinstance(event.get('remaining'), (dict, list)) else str(event.get('remaining', '—'))}


def event_time(value):
    try:
        value = datetime.fromisoformat(value)
        return value.astimezone(KST) if value.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def latest_regime(events):
    rows = [(event_time(event.get('time')), event) for event in events
            if isinstance(event, dict) and event.get('event') == 'v2_regime']
    rows = [(stamp, event) for stamp, event in rows if stamp is not None]
    if not rows:
        return None
    stamp, event = max(rows, key=lambda item: item[0])
    mode = {'intraday': '장중 매매 후보', 'swing_candidate': '단기 스윙 후보',
            'wait': '대기'}.get(event.get('mode'), '확인 불가')
    return {'기록 시각': stamp.strftime('%Y-%m-%d %H:%M:%S'), '국면': mode,
            '사유': event.get('reason') or '—', '상태': '연구용 · 검증 중'}


def read_regime_observation(path, now):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ValueError('국면 관찰 기록을 읽을 수 없습니다.') from exc
    if not isinstance(data, dict) or data.get('status') not in ('ready', 'partial', 'unavailable'):
        raise ValueError('국면 관찰 상태가 올바르지 않습니다.')
    stamp = event_time(data.get('time'))
    symbols = data.get('symbols')
    if stamp is None or not isinstance(symbols, dict) or not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('국면 관찰 시각 또는 종목 정보가 올바르지 않습니다.')
    rows = []
    for key, item in symbols.items():
        if (key not in ETF_CODES or not isinstance(item, dict) or item.get('symbol') != ETF_CODES[key]
                or item.get('purpose') != 'research_only' or item.get('data_quality') not in ('valid', 'invalid')):
            raise ValueError('국면 관찰 종목 정보가 올바르지 않습니다.')
        mode = {'intraday': '장중 매매 후보', 'swing_candidate': '단기 스윙 후보', 'wait': '대기'}.get(item.get('mode'))
        if mode is None:
            raise ValueError('국면 관찰 구분이 올바르지 않습니다.')
        daily = event_time(item.get('daily_quote_time'))
        minute_data = event_time(item.get('minute_data_time'))
        minute_source = {'decision_event': '기존 판단 분봉', 'market_context': '시장자료 기록', 'nh_direct': 'NH 조회'}.get(item.get('minute_source'), '확인 불가')
        rows.append({'종목': ETF_NAMES[key], '국면': mode if item['data_quality'] == 'valid' else '데이터 부족',
                     '데이터 상태': item['data_quality'], '사유': item.get('reason') or '—',
                     '분봉 출처': minute_source, '분봉 기준 시각': minute_data.strftime('%Y-%m-%d %H:%M:%S') if minute_data else '확인 불가',
                     '일봉 조회 시각': daily.strftime('%Y-%m-%d %H:%M:%S') if daily else '확인 불가'})
    minute = event_time(data.get('minute_event_time'))
    age = (now.astimezone(KST) - stamp).total_seconds()
    return {'time': stamp, 'stale': not 0 <= age <= 120, 'status': data['status'],
            'source': data.get('source') or '확인 불가', 'minute_time': minute, 'rows': rows}


def comparison_data(events):
    """Match model events only when a unique recorded cycle ID proves their relationship."""
    rows = []
    by_cycle = {}
    for event in events:
        if not isinstance(event, dict) or event.get('event') not in ('decision', 'v2_holding_decision', 'market_context'):
            continue
        if event.get('event') == 'market_context' and event.get('context') not in ('entry', 'holding'):
            continue
        stamp = event_time(event.get('time'))
        if stamp is None:
            continue
        kind = ('market_' + event['context']) if event['event'] == 'market_context' else event['event']
        row = {'time': stamp, 'kind': kind, 'symbol': event.get('symbol'),
               'decision': event.get('choice'), 'decision_confidence': event.get('confidence'),
               'jev': None, 'jev_confidence': None, 'reason': None, 'permitted': None, 'source': None,
               'cycle_id': event.get('cycle_id')}
        rows.append(row)
        if isinstance(row['cycle_id'], str) and row['cycle_id']:
            by_cycle.setdefault(row['cycle_id'], []).append(row)
        else:
            row['cycle_id'] = None
    for event in events:
        if not isinstance(event, dict):
            continue
        kind = event.get('event')
        cycle = event.get('cycle_id')
        matching = by_cycle.get(cycle, []) if isinstance(cycle, str) else []
        target = matching[0] if len(matching) == 1 else None
        if kind == 'jev_shadow':
            jev = event.get('jev')
            candidate = jev.get('candidate') if isinstance(jev, dict) else None
            if not isinstance(candidate, dict):
                continue
            if target is not None and event.get('openai_choice') == target['decision'] and target['jev'] is None:
                target['jev'], target['jev_confidence'] = candidate.get('choice'), candidate.get('confidence')
            else:
                stamp = event_time(event.get('time'))
                if stamp is not None:
                    rows.append({'time': stamp, 'kind': 'jev_only', 'symbol': None, 'decision': None,
                        'decision_confidence': None, 'jev': candidate.get('choice'), 'jev_confidence': candidate.get('confidence'),
                        'reason': None, 'permitted': None, 'source': None, 'cycle_id': None})
        elif kind == 'v2_entry_gate' and target is not None and target['kind'] in ('decision', 'market_entry'):
            target.update(reason=event.get('reason'), permitted=event.get('permitted'), source=event.get('source'))
        elif (kind == 'jev_holding_decision' and target is not None and target['kind'] in ('v2_holding_decision', 'market_holding')
              and event.get('symbol') == target['symbol'] and event.get('choice') in ('HOLD', 'SELL')):
            confidence = event.get('confidence')
            if type(confidence) in (int, float) and math.isfinite(confidence) and 0 <= confidence <= 1:
                target.update(jev_holding=event['choice'], jev_holding_confidence=confidence)
        elif (kind == 'v2_execution_gate' and target is not None and target['kind'] in ('decision', 'market_entry')
              and event.get('permitted') is False):
            target['execution_reason'] = event.get('reason')
    for duplicates in by_cycle.values():
        if len(duplicates) > 1:
            for row in duplicates:
                row['cycle_id'] = None
    for event in events:
        if not isinstance(event, dict) or event.get('event') != 'v2_risk_exit':
            continue
        stamp = event_time(event.get('time'))
        if stamp is not None:
            cycle = event.get('cycle_id')
            rows.append({'time': stamp, 'kind': 'v2_risk_exit', 'symbol': event.get('symbol'),
                'decision': None, 'decision_confidence': None, 'jev': None, 'jev_confidence': None,
                'reason': '손절·익절 보호 조건', 'permitted': None, 'source': '보호 청산',
                'cycle_id': cycle if isinstance(cycle, str) and cycle else None})
    return sorted(rows, key=lambda row: row['time'])


def paper_order_records(db):
    """Read fills and cycle links from a single read-only journal snapshot."""
    db.execute('BEGIN')
    fills = [dict(zip(('시각', '종목', '구분', '수량', '체결가'), row)) for row in
             db.execute('SELECT time,symbol,side,quantity,price FROM fills ORDER BY rowid DESC LIMIT 100')]
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='decision_order_links'").fetchone():
        return fills, None
    records = {}
    for cycle, status, symbol, side, qty, filled, price, stamp in db.execute('''SELECT l.cycle_id, i.status, i.symbol, i.side, i.qty, f.quantity, f.price, f.time
            FROM decision_order_links l JOIN intents i ON i.id=l.order_id
            LEFT JOIN fills f ON f.order_id=i.id'''):
        if status == 'terminal' and filled is not None and 0 < filled <= qty:
            label = '체결 확인' if filled == qty else '부분 체결 확인·주문 종료'
        elif status == 'terminal' and filled is None:
            label = '주문 종료·체결 기록 없음'
        elif status == 'acknowledged':
            label = '주문 접수·체결 대조 중'
        else:
            label = '주문 결과 미확인'
        side_label = {'BUY': '매수', 'SELL': '매도'}.get(side, '구분 미확인')
        details = f'{symbol} {side_label} {qty}주 · {label}'
        if status == 'terminal' and filled is not None and 0 < filled <= qty:
            details += f' · 확인 {filled}주 / {price}원 / {stamp}'
        records.setdefault(cycle, []).append(details)
    return fills, records


def order_outcome(row, records):
    if not row.get('cycle_id'):
        return '연결 기록 없음'
    if records is None:
        return '주문 원장 확인 불가'
    if row['cycle_id'] not in records:
        return '연결된 주문 없음(원장 기준)'
    return ' / '.join(records[row['cycle_id']])


def execution_reason_label(reason):
    return {'order_limit': '일일 주문 한도', 'position_limit': '전체 보유 한도',
            'learning_filter': '학습 필터', 'insufficient_buying_power': '모의 주문 가능 금액 부족',
            'order_guard_refused': '공유 계좌 가드 거절(미전송)', 'single_order_limit': '회당 금액 한도',
            'split_limit': '분할 매수 금액·수량 한도',
            'projected_exit': '추가 매수 직후 손절·익절 조건 충돌',
            'scale_signal_disabled': '새 판단 추가 매수 설정 꺼짐'}.get(reason, reason or '기록 없음')


def entry_reason_label(reason):
    import entry_reasons
    if not isinstance(reason, str):
        return '—'
    return entry_reasons.ENTRY.get(reason, reason or '—')


def block_rows(events, day):
    """(latest, per_stock) of why buys waited on `day` (user 2026-11-20 '대시보드에서 차단 사유도 보이게'): the latest cycle's
    chain (Jev's pick and its refusal, each ranked strength stock's reason, the new-listing reason) and, per stock, how often
    and why it was passed over today (v2_entry_gate strength_reasons; Jev-picked symbols with their own refusal)."""
    cycles, per = {}, {}
    for event in events:
        if not isinstance(event, dict) or event.get('event') != 'v2_entry_gate' or event.get('permitted') is not False:
            continue
        stamp = event_time(event.get('time'))
        if stamp is None or stamp.date() != day:
            continue
        cycle = cycles.setdefault(event.get('cycle_id') or event.get('time'), {'time': stamp, 'steps': []})
        cycle['time'] = max(cycle['time'], stamp)
        cycle['steps'].append(event)
        found = dict(event.get('strength_reasons') or {})
        if event.get('choice') not in (None, 'WAIT'):
            found.setdefault(event['choice'], event.get('reason'))
        for code, why in found.items():
            if why == 'picked':
                continue
            row = per.setdefault(code, {'count': 0, 'reasons': {}, 'last': None, 'last_why': None})
            row['count'] += 1
            row['reasons'][why] = row['reasons'].get(why, 0) + 1
            if row['last'] is None or stamp >= row['last']:
                row['last'], row['last_why'] = stamp, why
    latest = max(cycles.values(), key=lambda c: c['time']) if cycles else None
    stocks = sorted(({'code': code, **row, 'top': max(row['reasons'].items(), key=lambda kv: kv[1])} for code, row in per.items()),
                    key=lambda r: r['last'], reverse=True)
    return latest, stocks


def step_line(step, steps, names):
    """One refusal of a cycle in words. A Jev wait right after a refused Jev pick in the same cycle is the hand-off to the
    strength and new-listing paths (11-20: an inverse the gate refused read as 'Jev 대기 의견')."""
    pick = step.get('choice')
    if pick not in (None, 'WAIT'):
        return f"**Jev 선택 {names.get(pick, pick)}** → {entry_reason_label(step.get('reason'))}"
    before = [s for s in steps[:steps.index(step)] if s.get('choice') not in (None, 'WAIT')]
    if step.get('reason') == 'jev_wait' and before:
        return f"**{names.get(before[-1]['choice'], before[-1]['choice'])} 막힘** → 강도·신규주 경로로 넘김"
    return f"**Jev 대기** → {entry_reason_label(step.get('reason'))}"


def block_reason_panel(events, now):
    """The 매수 차단 사유 tab of the paper desk: the last cycle's chain, then today's stocks and why each was not bought."""
    latest, stocks = block_rows(events, now.date())
    if latest is None:
        st.caption('오늘 매수를 보류한 회차 기록이 없습니다.')
        return
    names = {**stock_names(), **ETF_NAMES}  # Jev picks ETFs by key (INV), the strength path by code
    st.markdown(f"##### 최근 회차 · {latest['time']:%H:%M:%S}")
    for step in latest['steps']:
        st.markdown('- ' + step_line(step, latest['steps'], names))
        if step.get('strength_reasons'):
            st.markdown('  - 강도 후보: ' + ' · '.join(f'{names.get(c, c)} {entry_reason_label(w)}' for c, w in step['strength_reasons'].items()))
        elif step.get('strength_reason'):
            st.markdown(f"  - 강도 후보: {entry_reason_label(step['strength_reason'])}")
        if step.get('ipo_reason'):
            st.markdown(f"  - 신규주: {entry_reason_label(step['ipo_reason'])}")
    st.markdown('##### 오늘 종목별 차단 사유')
    st.dataframe(pd.DataFrame([{'종목': names.get(r['code'], r['code']), '코드': r['code'], '마지막': f"{r['last']:%H:%M}",
                                '마지막 사유': entry_reason_label(r['last_why']), '오늘 횟수': r['count'],
                                '가장 많은 사유': f"{entry_reason_label(r['top'][0])} ({r['top'][1]})"} for r in stocks]),
                 hide_index=True, width='stretch')
    st.caption('강도 후보는 Jev 관심 상위 종목마다 사지 않은 이유를 13:36부터 남깁니다. 인버스·지수 ETF는 Jev가 고른 회차의 사유입니다. 규칙대로 막힌 것이며 주문 오류가 아닙니다.')


def risk_exit_warning(events):
    failures = [(event_time(event.get('time')), event) for event in events
                if isinstance(event, dict) and event.get('event') == 'v2_risk_exit_error']
    failures = [(stamp, event) for stamp, event in failures if stamp is not None]
    if not failures:
        return None
    stamp, event = max(failures, key=lambda item: item[0])
    symbol = event.get('symbol')
    name = next((ETF_NAMES[key] for key, code in ETF_CODES.items() if code == symbol), '종목 확인 불가')
    return (f"보호매도 미전송 기록: {stamp:%Y-%m-%d %H:%M:%S} · {name} · "
            f"{entry_reason_label(event.get('reason'))}. 현재 보유와 주문 상태를 증권사에서 확인하세요.")


def chart_bars(events, group, day):
    bars = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        stamp = event_time(event.get('time'))
        groups = event.get('market_bars')
        if not stamp or stamp.date() != day or not isinstance(groups, dict) or not isinstance(groups.get(group), list):
            continue
        for row in groups[group]:
            try:
                clock = str(row['bsop_time'])
                if len(clock) != 6 or not clock.isascii() or not clock.isdigit():
                    continue
                bar_time = datetime.strptime(day.strftime('%Y%m%d') + clock, '%Y%m%d%H%M%S').replace(tzinfo=KST)
                o, h, l, c = (int(row[key]) for key in ('stck_oprc', 'stck_hgpr', 'stck_lwpr', 'stck_prpr'))
                if bar_time > stamp or l <= 0 or not (l <= o <= h and l <= c <= h):
                    continue
                bars[int(bar_time.timestamp())] = {'time': int(bar_time.timestamp()), 'open': o, 'high': h, 'low': l, 'close': c,
                                                   'volume': int(row.get('vol') or 0)}
            except (TypeError, KeyError, ValueError):
                continue
    return [bars[key] for key in sorted(bars)]


# NH krstockQuotePeriod: gubun 1 day 2 week 3 month 4 year 5 minute 7 tick; (gubun, xtick, rows to read, bars shown first)
TIMEFRAMES = {'틱': ('7', '1', 600, 150), '1분': ('5', '1', 600, 120), '5분': ('5', '5', 500, 78), '15분': ('5', '15', 400, 52),
              '일': ('1', None, 400, 120), '주': ('2', None, 300, 104), '월': ('3', None, 200, 60), '년': ('4', None, 40, 30)}
MA_PERIODS = (3, 5, 20, 60, 120)
MA_COLORS = {3: '#f472b6', 5: '#facc15', 20: '#22d3ee', 50: '#34d399', 60: '#a78bfa', 120: '#fb923c', 200: '#f8fafc'}


def bars_from_period(rows, timeframe):
    """NH period rows (newest first) as ascending chart bars; ticks in the same second are merged into one bar."""
    intraday = TIMEFRAMES[timeframe][1] is not None
    bars = {}
    for row in reversed([r for r in rows if isinstance(r, dict)]):  # oldest first, so later rows close the bar
        try:
            day = str(row['bsop_date'])
            day = day[:4] + '0101' if timeframe == '년' else day[:6] + '01' if timeframe == '월' else day[:8]
            clock = str(row.get('bsop_time') or '')[:6] if intraday else '000000'
            at = datetime.strptime(day + clock, '%Y%m%d%H%M%S').replace(tzinfo=KST)
            o, h, l, c = (float(row[k]) for k in ('stck_oprc', 'stck_hgpr', 'stck_lwpr', 'stck_prpr'))
            volume = int(float(row.get('vol') or 0))
        except (KeyError, TypeError, ValueError):
            continue
        if min(o, h, l, c) <= 0 or not (l <= min(o, c) and max(o, c) <= h):
            continue
        key = int(at.timestamp())
        if key in bars:
            bar = bars[key]
            bar.update(high=max(bar['high'], h), low=min(bar['low'], l), close=c, volume=bar['volume'] + volume)
        else:
            bars[key] = {'time': key, 'open': o, 'high': h, 'low': l, 'close': c, 'volume': volume}
    return [bars[k] for k in sorted(bars)]


KIWOOM_MINUTES = {'1분': 1, '5분': 5, '15분': 15}


def kiwoom_minute_bars(symbol, timeframe):
    """Chart bars for 1/5/15-minute charts from one Kiwoom ka10080 page (900 rows, KRX regular session), for display only
    (user 2026-11-20 '차트만 바꿔줘'; the runner's decision bars stay NH). Kiwoom stamps a bar by its first minute and
    NH by its last, so bars move `scope` minutes later to line up with the runner's records and fill markers."""
    import kiwoom_data
    scope = KIWOOM_MINUTES[timeframe]
    key = ('kiwoom-bars', symbol, scope)
    hit = _read_cache().get(key)
    if hit and time.monotonic() - hit[0] < CHART_CACHE_S:
        return hit[1]
    bars = []
    for r in kiwoom_data.call('ka10080', {'stk_cd': symbol, 'tic_scope': str(scope), 'upd_stkpc_tp': '0'},
                              'stk_min_pole_chart_qry', max_pages=1):
        t = str(r.get('cntr_tm') or '')
        o, h, l, c = (abs(kiwoom_data.num(r.get(f)) or 0) for f in ('open_pric', 'high_pric', 'low_pric', 'cur_prc'))
        if len(t) != 14 or not '090000' <= t[8:] < '153100' or min(o, h, l, c) <= 0 or not (l <= min(o, c) and max(o, c) <= h):
            continue  # NXT pre/after-market rows come in the same answer
        at = datetime.strptime(t, '%Y%m%d%H%M%S').replace(tzinfo=KST) + timedelta(minutes=scope)
        bars.append({'time': int(at.timestamp()), 'open': o, 'high': h, 'low': l, 'close': c,
                     'volume': int(abs(kiwoom_data.num(r.get('trde_qty')) or 0))})
    bars = sorted({b['time']: b for b in bars}.values(), key=lambda b: b['time'])
    _read_cache()[key] = (time.monotonic(), bars)
    return bars


def period_bars(read, symbol, timeframe):
    if timeframe in KIWOOM_MINUTES:
        try:
            if bars := kiwoom_minute_bars(symbol, timeframe):
                return bars
        except Exception:  # noqa: BLE001 - no key or Kiwoom down: NH as before
            pass
    gubun, xtick, count, _ = TIMEFRAMES[timeframe]
    fields = {'market_cd': 'KRX', 'iem_cd': symbol, 'gubun': gubun, 'array_cnt': str(count), 'view_main_yn': 'Y'}
    if xtick:
        fields.update(xtick=xtick, today_cls_code='1' if gubun == '7' else '0')
    result = cached_tool(read, 'call_api', {'operationId': 'krstockQuotePeriod', 'input': fields}, ttl=CHART_CACHE_S)
    data = result.get('data') if isinstance(result, dict) and result.get('status') == 'ok' else None
    rows = data.get('Output_1') if isinstance(data, dict) and data.get('rsp_cd') == '00000' else None
    if not isinstance(rows, list):
        raise ValueError('기간별 시세 조회 실패')
    bars = bars_from_period(rows, timeframe)
    if not bars:
        raise ValueError('유효한 봉 없음')
    return bars


def moving_averages(bars, periods):
    """{period: [{time, value}]} simple moving averages of closes; a period longer than the data is left out."""
    closes = [bar['close'] for bar in bars]
    lines = {}
    for period in periods:
        if len(closes) >= period:
            window = sum(closes[:period])
            values = [window / period]
            for i in range(period, len(closes)):
                window += closes[i] - closes[i - period]
                values.append(window / period)
            lines[period] = [{'time': bars[i + period - 1]['time'], 'value': round(v, 4)} for i, v in enumerate(values)]
    return lines


def containing_markers(fills, bars, symbol):
    """Broker-confirmed fills on the bar whose period contains them (for 5/15-minute, daily and longer bars)."""
    times = [bar['time'] for bar in bars]
    markers = []
    for fill in fills or []:
        stamp = event_time(fill.get('시각'))
        side = str(fill.get('구분', '')).upper()
        if not stamp or fill.get('종목') != symbol or side not in ('BUY', 'SELL'):
            continue
        earlier = [t for t in times if t <= stamp.timestamp()]
        if earlier:
            buy = side == 'BUY'
            markers.append({'time': earlier[-1], 'position': 'belowBar' if buy else 'aboveBar', 'color': '#16a34a' if buy else '#ef4444',
                            'shape': 'arrowUp' if buy else 'arrowDown',
                            'text': ('매수' if buy else '매도') + (f" {fill['수량']}주" if type(fill.get('수량')) is int else '')})
    return sorted(markers, key=lambda m: m['time'])


def chart_controls(key, default='1분'):
    """Timeframe, moving averages and volume switches shared by every candle chart."""
    frame, averages, volume = st.columns([3, 2, 1], vertical_alignment='bottom')
    timeframe = frame.segmented_control('봉 단위', list(TIMEFRAMES), default=default, key=f'{key}_timeframe') or default
    periods = averages.multiselect('이동평균선', MA_PERIODS, default=[5, 20], key=f'{key}_ma',
                                   format_func=lambda p: f"{p}{'일' if timeframe == '일' else '봉'}선")
    show_volume = volume.toggle('거래량', value=True, key=f'{key}_volume')
    return timeframe, sorted(periods), show_volume


def investor_rows(read, symbol, days=20):
    """Per-stock daily net quantities by investor type (NH current-investor; today's row is provisional)."""
    result = cached_tool(read, 'call_api', {'operationId': 'krstockQuoteCurrentInvestor',
                                         'input': {'market_cd': 'KRX', 'iem_cd': symbol, 'array_cnt': str(days)}})
    data = result.get('data') if isinstance(result, dict) and result.get('status') == 'ok' else None
    rows = data.get('Output_0') if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError('투자자 조회 실패')
    out = []
    for row in rows:
        try:
            day = datetime.strptime(str(row['bsop_date1']), '%Y%m%d').date()
        except (KeyError, ValueError):
            continue
        values = {label: row.get(key) for label, key in (('외국인', 'frgn_ntby_qty'), ('기관', 'gigwan'), ('개인', 'person'), ('프로그램', 'program'))}
        if all(type(v) in (int, float) for v in values.values()):
            out.append({'날짜': day, **values})
    return sorted(out, key=lambda r: r['날짜'])


def investor_panel(read, symbol, name):
    """Foreign, institutional, individual and program net buying for one symbol over recent days."""
    cache = st.session_state.setdefault('investor_cache', {})
    entry = cache.get(symbol)
    if not entry or (datetime.now(KST) - entry[0]).total_seconds() > 300:
        try:
            entry = (datetime.now(KST), investor_rows(read, symbol))
        except Exception:
            entry = (datetime.now(KST), None)
        cache[symbol] = entry
    rows = entry[1]
    st.markdown(f'##### 투자자별 순매수 · {name}')
    if not rows:
        st.caption('투자자 동향을 불러오지 못했습니다(장외이거나 조회 실패). 0으로 해석하지 마세요.')
        return
    frame = pd.DataFrame(rows)
    totals = frame[['외국인', '기관', '개인', '프로그램']].sum()
    cols = st.columns(4)
    for col, label in zip(cols, ('외국인', '기관', '개인', '프로그램')):
        value = totals[label]
        col.metric(f'{label} · {len(frame)}일 합계', f'{value / 1e4:+,.1f}만주' if abs(value) >= 1e4 else f'{value:+,.0f}주',
                   help=f'{value:+,.0f}주', border=True)
    long = frame.melt('날짜', var_name='주체', value_name='순매수(주)')
    st.altair_chart(alt.Chart(long).mark_bar().encode(
        x=alt.X('yearmonthdate(날짜):O', title=None, axis=alt.Axis(format='%m/%d', labelAngle=0)),
        xOffset='주체:N', y=alt.Y('순매수(주):Q', title='순매수(주)'),
        color=alt.Color('주체:N', scale=alt.Scale(domain=['외국인', '기관', '개인', '프로그램'],
                                                  range=['#38bdf8', '#a78bfa', '#f59e0b', '#22c55e']), legend=alt.Legend(orient='top', title=None)),
        tooltip=[alt.Tooltip('yearmonthdate(날짜):T', title='날짜', format='%Y-%m-%d'), '주체', alt.Tooltip('순매수(주):Q', format='+,.0f')]
    ).properties(height=240), width='stretch')
    today = rows[-1]['날짜']
    st.caption(f"NH 주식현재가 투자자 · 일별 순매수 수량(주) · {today:%m/%d} 행은 장중 잠정치(외국인은 장 마감 후 확정) · "
               f"조회 {entry[0]:%H:%M} · ETF는 유동성공급자(기관) 거래가 섞여 방향 신호로 약합니다.")


def merge_chart_bars(recorded, fresh):
    """Use the latest validated broker bars when recorded windows overlap."""
    combined = {row['time']: row for row in recorded}
    combined.update({row['time']: row for row in fresh})
    return [combined[key] for key in sorted(combined)]


def chart_markers(rows, fills, bars, group, symbol, *, show_jev=True):
    available = {bar['time'] for bar in bars}
    rules = strategy_settings()[0]  # confidence markers use the currently saved thresholds
    markers = []
    # Every opinion gets a mark. Text goes on a changed view, an actionable one, or every 5th minute, so a steady
    # opinion still shows its confidence without labels overlapping on adjacent bars.
    last = {}
    score = lambda value: f' {value:.2f}' if type(value) in (int, float) else ''
    for row in rows:
        stamp = row['time'].replace(second=0, microsecond=0)
        position = int(stamp.timestamp())
        states = []
        if row.get('decision') is not None:
            shown = row['decision'] == group or (row['decision'] == 'SELL' and row.get('symbol') == symbol)
            states.append(('decision', 'Decision: ' + row['decision'] if shown else None,
                           {'position': 'aboveBar', 'color': '#3b82f6', 'shape': 'circle'}))
        if show_jev and row.get('jev') is not None:
            states.append(('jev', 'Jev매수' if row['jev'] == group else None,
                           {'position': 'belowBar', 'color': '#a855f7', 'shape': 'arrowUp',
                            'detail': score(row.get('jev_confidence')), 'value': row.get('jev_confidence'), 'act': rules['entry_confidence']}))
        if show_jev and row.get('jev_holding') is not None and row.get('symbol') == symbol:
            states.append(('jev_holding', 'Jev매도' if row['jev_holding'] == 'SELL' else None,
                           {'position': 'aboveBar', 'color': '#e879f9', 'shape': 'arrowDown',
                            'detail': score(row.get('jev_holding_confidence')), 'value': row.get('jev_holding_confidence'), 'act': rules['sell_confidence']}))
        for key, state, style in states:
            if state and position in available:
                detail, value, act = style.pop('detail', ''), style.pop('value', None), style.pop('act', None)
                actionable = act is not None and type(value) in (int, float) and value >= act
                changed = state != last.get(key)
                newly_actionable = actionable and not last.get(key + ':act')
                gate = ''
                if key == 'jev' and row.get('source') == 'jev':
                    if row.get('permitted') is True:
                        why = row.get('execution_reason')
                        gate = ' · 진입 통과' + (f' · 주문 차단: {execution_reason_label(why)}' if why else '')
                    elif row.get('permitted') is False:
                        why = row.get('reason')
                        gate = ' · 진입 차단' + (f': {entry_reason_label(why)}' if why else '')
                gate_changed = gate != last.get(key + ':gate', '')
                text = (state + detail + (' 신뢰도 충족' if actionable else '') + gate if changed or newly_actionable or gate_changed
                        else detail.strip() if stamp.minute % 5 == 0 else '')
                markers.append({'time': position, **style, 'size': 2 if changed or actionable else 1, 'text': text})
                last[key + ':act'] = actionable
                last[key + ':gate'] = gate
            else:
                last[key + ':act'] = False
            last[key] = state
    for fill in fills:
        stamp = event_time(fill.get('시각'))
        if not stamp or fill.get('종목') != symbol or fill.get('구분', '').upper() not in ('BUY', 'SELL'):
            continue
        minute = int(stamp.replace(second=0, microsecond=0).timestamp())
        # A fill inside a gap in stored bars is shown on the next bar, labelled with its own confirmation time.
        position = min((t for t in available if t >= minute), default=None)
        if position is not None:
            buy = fill['구분'].upper() == 'BUY'
            markers.append({'time': position, 'position': 'belowBar' if buy else 'aboveBar',
                            'color': '#16a34a' if buy else '#ef4444', 'shape': 'arrowUp' if buy else 'arrowDown',
                            'text': '체결 확인: ' + ('매수' if buy else '매도')
                                    + (f" {fill['수량']}주" if type(fill.get('수량')) is int else '')
                                    + ('' if position == minute else f" ({stamp.astimezone(KST):%H:%M})")})
    return sorted(markers, key=lambda marker: marker['time'])


def tradingview_chart(bars, markers, basis=None, *, averages=None, volume=True, intraday=True, visible=120, levels=None, alt='분봉과 AI 의견 및 확인된 체결',
                      volume_pane=False):
    """Candles with markers, optional moving averages and a volume pane; opens on the latest `visible` bars. levels: horizontal
    price lines [{price, color, label, style (0 solid, 2 dashed)}] with their label on the price axis (the long-term level map).
    volume_pane: volume in its own pane under the candles with its own axis (US idea charts, 2026-11-18) instead of faint bars
    under the price."""
    lines = [{'period': p, 'color': MA_COLORS.get(p, '#e2e8f0'), 'data': data} for p, data in (averages or {}).items()]
    colors = THEME.chart_colors()
    payload = json.dumps({'bars': bars, 'markers': markers, 'basis': basis, 'lines': lines, 'volume': bool(volume), 'levels': levels or [],
                          'intraday': bool(intraday), 'visible': int(visible), 'volume_pane': bool(volume_pane)},
                         ensure_ascii=True, allow_nan=False).replace('<', '\\u003c')
    html = '''<div id="chart" style="height:460px"></div><div id="error" style="color:#dc2626">차트 라이브러리 불러오는 중입니다. 인터넷 연결 또는 차단 설정을 확인하세요.</div>
    <div style="font:12px sans-serif;margin-top:5px;color:#94a3b8"><a href="https://www.tradingview.com/" target="_blank" rel="noopener">TradingView</a> Lightweight Charts™</div>
    <script src="https://unpkg.com/lightweight-charts@5.0.8/dist/lightweight-charts.standalone.production.js"></script>
    <script>
    try {
      const data = PAYLOAD;
      const element = document.getElementById('chart');
      const parts = data.intraday ? {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false} : {year:'2-digit',month:'2-digit',day:'2-digit'};
      const format = t => new Intl.DateTimeFormat('ko-KR', {timeZone:'Asia/Seoul', ...parts}).format(new Date(t * 1000));
      const chart = LightweightCharts.createChart(element, {autoSize:true, height:460, layout:{background:{color:'BG'},textColor:'TEXT'}, grid:{vertLines:{color:'GRID'},horzLines:{color:'GRID'}}, timeScale:{timeVisible:data.intraday,secondsVisible:false,tickMarkFormatter:format}, localization:{timeFormatter:format}});
      if (data.basis) {
        const held = data.bars.filter(b => b.time >= data.basis.start);
        if (held.length) {
          const area = chart.addSeries(LightweightCharts.BaselineSeries, {baseValue:{type:'price',price:data.basis.price},topFillColor1:'rgba(239,68,68,.40)',topFillColor2:'rgba(239,68,68,.08)',bottomFillColor1:'rgba(59,130,246,.08)',bottomFillColor2:'rgba(59,130,246,.40)',topLineColor:'rgba(239,68,68,.7)',bottomLineColor:'rgba(59,130,246,.7)',priceLineVisible:false,lastValueVisible:false});
          area.setData(held.map(b => ({time:b.time,value:b.close})));
          for (const [price, color, label] of [[data.basis.price,'LINE','매입가 (위: 수익 · 아래: 손실)'],[data.basis.stop,'#60a5fa',data.basis.stop_label||'손절 기준'],[data.basis.half,'#fca5a5','절반 익절'],[data.basis.all,'#f87171','익절']].filter(line => line[0])) {
            const line = chart.addSeries(LightweightCharts.LineSeries, {color,lineWidth:1,lineStyle:2,priceLineVisible:false,lastValueVisible:false,title:label});
            line.setData(held.map(b => ({time:b.time,value:price})));
          }
        }
      }
      const series = chart.addSeries(LightweightCharts.CandlestickSeries, {upColor:'#ef4444',downColor:'#3b82f6',wickUpColor:'#ef4444',wickDownColor:'#3b82f6',borderVisible:false});
      series.setData(data.bars.map(({time, open, high, low, close}) => ({time, open, high, low, close})));
      LightweightCharts.createSeriesMarkers(series, data.markers);
      for (const level of data.levels) series.createPriceLine({price:level.price,color:level.color,lineWidth:1,lineStyle:level.style ?? 2,axisLabelVisible:true,title:level.label});
      for (const line of data.lines) {
        const ma = chart.addSeries(LightweightCharts.LineSeries, {color:line.color,lineWidth:1.5,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false,title:'MA'+line.period});
        ma.setData(line.data);
      }
      if (data.volume && data.volume_pane) {
        const vol = chart.addSeries(LightweightCharts.HistogramSeries, {priceFormat:{type:'volume'},priceLineVisible:false,lastValueVisible:true,title:'거래량'}, 1);
        vol.setData(data.bars.map(b => ({time:b.time,value:b.volume||0,color:b.close>=b.open?'rgba(239,68,68,.85)':'rgba(59,130,246,.85)'})));
        const panes = chart.panes();
        if (panes.length > 1) { panes[0].setHeight(340); panes[1].setHeight(120); }
      } else if (data.volume) {
        series.priceScale().applyOptions({scaleMargins:{top:0.06,bottom:0.26}});
        const vol = chart.addSeries(LightweightCharts.HistogramSeries, {priceScaleId:'volume',priceFormat:{type:'volume'},priceLineVisible:false,lastValueVisible:false});
        vol.priceScale().applyOptions({scaleMargins:{top:0.78,bottom:0}});
        vol.setData(data.bars.map(b => ({time:b.time,value:b.volume||0,color:b.close>=b.open?'rgba(239,68,68,.45)':'rgba(59,130,246,.45)'})));
      }
      // Open on the latest bars. A chart drawn inside a hidden tab has no width yet, so wait until it has one.
      const applyRange = () => {
        if (!element.clientWidth) return false;
        const n = data.bars.length;
        if (n > data.visible) chart.timeScale().setVisibleLogicalRange({from:n - data.visible, to:n + 2}); else chart.timeScale().fitContent();
        return true;
      };
      if (!applyRange()) { const watch = new ResizeObserver(() => { if (applyRange()) watch.disconnect(); }); watch.observe(element); }
      document.getElementById('error').style.display = 'none';
    } catch (e) { document.getElementById('error').textContent = '차트를 불러오지 못했습니다. 인터넷 연결 또는 차단 설정을 확인하세요.'; }
    </script>'''.replace('PAYLOAD', payload)
    for name, key in (('BG', 'background'), ('TEXT', 'text'), ('GRID', 'grid'), ('LINE', 'line')):
        html = html.replace(f"'{name}'", f"'{colors[key]}'")
    st.iframe(html, height=500, alt=alt)


def chart_universe(engine, holdings, known):
    """{label: (group, code)} the chart picker offers. A long-term account (user 2026-11-18) gets the long-term engine's
    ETFs only; the Jev desks get the ETF7, held scanner stocks and today's watchlist (trading candidates)."""
    if engine == 'longterm':
        import vibe_longterm as L
        import yeo_method as Y
        charts = {f'{a[1]} ({a[0]})': (k, a[0]) for k, a in L.ASSETS.items()}
        return charts | {f'{name} ({code})': (key, code) for key, (code, name) in Y.EXTRA.items() if key in ('NDXMIX', 'SPXMIX')}  # 여운봉 매매법
    charts = dict(ETF_CHARTS)
    for row in holdings:  # held scanner stocks
        code = row.get('종목코드') if isinstance(row, dict) else None
        if code and code not in ETF_CODES.values():
            charts[f"{row.get('종목') or known.get(code, code)} ({code})"] = (code, code)
    for code in ((watchlist_snapshot() or {}).get('selected') or []):  # today's watchlist (trading candidates)
        charts.setdefault(f'{known.get(code, code)} ({code})', (code, code))
    return charts


def paper_trade_chart(read, snapshot, refresh, auto, preferred=None, fills=None, key='paper_desk', levels=None, extra=None, engine=None, shadow=None):
    """Show market bars and verified paper evidence together on the first screen.

    fills/levels/extra let a live account reuse the chart: its broker fills, its limits and virtual-trade markers."""
    now = datetime.now(KST)
    holdings = snapshot.get('holdings', []) if isinstance(snapshot, dict) else []
    held_codes = {row.get('종목코드') for row in holdings if isinstance(row, dict)}
    known = stock_names()
    charts = chart_universe(engine, holdings, known)
    longterm = engine == 'longterm'
    if key == 'paper_desk':
        import trading_positions

        states = trading_positions.snapshot(get_runtime_root() / 'nhplug')
        labels = {code: ETF_NAMES[name] for name, code in ETF_CODES.items()}
        labels.update(known)
        by_code = {}
        for name, (group, code) in charts.items():
            by_code.setdefault(code, (group, name))
        status = {}
        for account_engine, info in states.items():
            if info.get('status') != 'ok':
                st.caption(f"{account_engine} 원장 확인 불가")
                continue
            for field in ('holdings', 'recent_stops'):
                for row in info.get(field, []):
                    code = row['symbol']
                    by_code.setdefault(code, (code, f"{labels.get(code, code)} ({code})"))
                    entry = status.setdefault(code, {'engines': set(), 'held': set(), 'latest': None})
                    entry['engines'].add(account_engine)
                    if field == 'holdings':
                        entry['held'].add(account_engine)
                    else:
                        try:
                            at = datetime.fromisoformat(row['time'])
                            if at.tzinfo and (entry['latest'] is None or at > entry['latest']):
                                entry['latest'] = at
                        except (KeyError, TypeError, ValueError):
                            pass
            if info.get('unknown_sells'):
                st.caption(f"{account_engine} 매도 사유 미확인 {info['unknown_sells']}건")
        pending = st.session_state.pop('pending_chart', None)
        old = st.session_state.get(f'{key}_chart_symbol')
        if pending:
            code = pending['code']
            by_code.setdefault(code, (code, f"{labels.get(code, code)} ({code})"))
            st.session_state[f'{key}_chart_symbol'] = code
            st.session_state['paper_chart_engine'] = pending['engine']
        elif old not in by_code:
            old_code = charts.get(old, (None, None))[1]
            old_code = old_code or next((code for code, (_, label) in by_code.items()
                                         if old == label or str(old).startswith(label + ' · ')), None)
            old_code = old_code or st.session_state.get('paper_chart_code')
            if old_code in by_code:
                st.session_state[f'{key}_chart_symbol'] = old_code
            else:
                st.session_state.pop(f'{key}_chart_symbol', None)

        def format_symbol(code):
            label = by_code[code][1]
            details = status.get(code) or {}
            tags = []
            if details.get('held'):
                tags.append('보유 ' + '·'.join('Jev' if e == 'jev' else 'vibe-trading' for e in sorted(details['held'])))
            if details.get('latest'):
                tags.append(f"최근 손절 {details['latest'].astimezone(KST):%m/%d %H:%M}")
            return label + (' · ' + ' · '.join(tags) if tags else '')

        codes = list(status) + [code for code in by_code if code not in status]
        formatted = {format_symbol(code): code for code in codes}
        selected_code = st.session_state.get(f'{key}_chart_symbol')
        if selected_code in by_code:
            st.session_state[f'{key}_chart_symbol'] = format_symbol(selected_code)
        initial = next((i for i, code in enumerate(codes) if code in held_codes),
                       next((i for i, code in enumerate(codes) if by_code[code][0] == preferred), 0))
        title, picker = st.columns([2, 3], vertical_alignment='bottom')
        title.markdown('##### 차트')
        name = picker.selectbox('보유·관심 종목 차트', list(formatted), index=initial,
                                key=f'{key}_chart_symbol', label_visibility='collapsed')
        symbol = formatted[name]
        group = by_code[symbol][0]
        engines = sorted((status.get(symbol) or {}).get('engines') or [])
        if pending:
            engine = pending['engine']
        else:
            engine = st.session_state.get('paper_chart_engine', engine)
        if len(engines) > 1:
            if pending and pending['engine'] in engines:
                st.session_state[f'{key}_engine_{symbol}'] = pending['engine']
            if engine not in engines:
                engine = engines[0]
            engine = st.radio('차트 기록 엔진', engines, index=engines.index(engine),
                              horizontal=True, key=f'{key}_engine_{symbol}')
        elif engines:
            engine = engines[0]
        st.session_state['paper_chart_engine'] = engine
        st.session_state['paper_chart_code'] = symbol
        st.caption('보유·최근 손절은 엔진 원장의 확인된 체결 기준 · 최근 7일 · 시각은 한국시간')
    else:
        by_code, shadow_status = shadow_chart_symbols(charts, shadow or {}, known)
        codes = list(shadow_status) + [code for code in by_code if code not in shadow_status]
        current = st.session_state.get(f'{key}_chart_symbol')
        if current not in by_code:
            old_code = charts.get(current, (None, None))[1]
            if old_code in by_code:
                st.session_state[f'{key}_chart_symbol'] = old_code
            else:
                st.session_state.pop(f'{key}_chart_symbol', None)
        initial = next((i for i, code in enumerate(codes) if shadow_status.get(code, {}).get('qty')),
                       next((i for i, code in enumerate(codes) if code in held_codes),
                            next((i for i, code in enumerate(codes) if by_code[code][0] == preferred), 0)))

        def live_symbol_label(code):
            details = shadow_status.get(code) or {}
            tags = []
            if details.get('qty'):
                tags.append(f"가상 재현 보유 {details['qty']:,}주")
            if details.get('time'):
                tags.append(f"가상 재현 {details['outcome']} {details['time'].astimezone(KST):%m/%d %H:%M}")
            return by_code[code][1] + (' · ' + ' · '.join(tags) if tags else '')
        title, picker = st.columns([2, 3], vertical_alignment='bottom')
        title.markdown('##### 차트')
        symbol = picker.selectbox('장기투자 종목 차트' if longterm else '보유·관심 종목 차트', codes, index=initial, format_func=live_symbol_label,
                                  key=f'{key}_chart_symbol', label_visibility='collapsed')
        group, name = by_code[symbol]
    timeframe, periods, show_volume = chart_controls(key, '일' if longterm else '1분')
    minute = timeframe == '1분'  # the runner's AI opinions are per completed 1-minute bar
    show_jev = not longterm  # Jev does not trade a long-term account
    if minute and show_jev:
        show_jev = st.toggle('Jev 판단 표식', value=True, key=f'{key}_show_jev',
                             help='차트의 Jev 의견만 표시하거나 숨깁니다. 체결과 가상 매매 표식은 유지됩니다.')
    observed = snapshot.get('time') if isinstance(snapshot, dict) else None
    if isinstance(snapshot, dict) and not (isinstance(observed, datetime) and observed.tzinfo is not None
                                           and 0 <= (now - observed).total_seconds() <= SNAPSHOT_MAX_AGE):
        st.warning('증권사 잔고 조회가 오래돼 매입가 기준선을 표시하지 않습니다.')

    root = get_runtime_root() / 'nhplug' / 'shared-etf-paper'
    if engine:
        from engine_roles import ledger_root, main_engine
        nhplug = get_runtime_root() / 'nhplug'
        root = ledger_root(nhplug, engine)
        virtual = root is not None and engine != main_engine(nhplug)
        if virtual and key == 'paper_desk':
            snapshot = None
        st.caption(f"차트 기록 · {engine} 엔진 · {'가상 체결 원장' if virtual else '공유 모의 체결 원장' if root else '엔진 원장 없음(증권사 체결만 표시)'}")
    events = []
    if root is not None:  # a long-term live account follows no Jev ledger: no AI markers, its own broker fills only
        try:
            events = read_events((root / 'events.jsonl'), 5000)
        except (OSError, ValueError):
            st.warning('로컬 AI 기록을 확인할 수 없어 판단 마커를 표시하지 못했습니다.')
    if fills is None and root is not None:
        try:
            with ro_connect(root / "orders.sqlite3") as db:
                fills, _ = paper_order_records(db)
        except sqlite3.Error:
            fills = None
            st.warning('체결 원장을 확인할 수 없어 체결 마커와 매입가 기준선을 표시하지 못했습니다.')

    recorded = chart_bars(events, group, now.date()) if minute else []
    cache = st.session_state.get(f'{key}_bars')
    if (refresh or auto or not isinstance(cache, dict) or cache.get('symbol') != symbol or cache.get('timeframe') != timeframe
            or (datetime.now(KST) - cache['time']).total_seconds() > 60):
        try:
            cache = {'symbol': symbol, 'timeframe': timeframe, 'time': datetime.now(KST), 'bars': period_bars(read, symbol, timeframe)}
        except (OSError, ValueError, TypeError, KeyError):
            cache = {'symbol': symbol, 'timeframe': timeframe, 'time': datetime.now(KST), 'bars': None}
        st.session_state[f'{key}_bars'] = cache
    fresh = cache.get('bars') if isinstance(cache, dict) else None
    bars = merge_chart_bars(recorded, fresh or [])
    rows = comparison_data(events)
    related = [row for row in rows if row.get('symbol') == symbol or row.get('jev') == group or row.get('decision') == group]
    status = []
    if related:
        latest = related[-1]
        opinion = latest.get('jev_holding') or latest.get('jev') or '확인 불가'
        confidence = latest.get('jev_holding_confidence') if latest.get('jev_holding') else latest.get('jev_confidence')
        score = f' {confidence:.2f}' if type(confidence) in (int, float) and math.isfinite(confidence) else ''
        status.append(f"최근 Jev {opinion}{score} ({latest['time']:%H:%M})")
    if fresh is None:
        st.warning('최신 NH 시세를 확인하지 못했습니다. 아래 차트가 보이면 저장된 과거 분봉입니다.')
    if bars:
        basis = holding_chart_basis(snapshot, fills, symbol, now)
        if basis is None and levels is not None:  # live: today's orders rarely reach back to the opening buy
            held = [r for r in (snapshot or {}).get('holdings', []) if r.get('종목코드') == symbol]
            average = held[0].get('평균매입가') if len(held) == 1 else None
            basis = {'price': float(average), 'start': bars[0]['time']} if type(average) in (int, float) and average > 0 else None
        if basis and not minute:  # a longer bar starts before the buy inside it
            basis['start'] = max([b['time'] for b in bars if b['time'] <= basis['start']], default=basis['start'])
        if basis and not any(bar['time'] >= basis['start'] for bar in bars):
            basis = None
        if basis:
            basis = with_levels(basis, symbol, engine, levels['stop_loss_pct'] if levels is not None else None)
            stop_text = f"{basis['stop_label']} {basis['stop']:,.2f}" if basis['stop'] is not None else '손절 기준 미확인'
            status.append(f"매입 {basis['price']:,.0f} · {stop_text} · {targets_text(basis['half'], basis['all'])}")
            if '시황 재사용/오래됨' in basis['stop_label']:
                st.warning('손절 기준 계산에 오래된 시황이 재사용됐습니다. 최신 시황 확인이 필요합니다.')
        last = datetime.fromtimestamp(bars[-1]['time'], KST)
        status.append(f"마지막 {timeframe}봉 {last:%m/%d %H:%M}" if TIMEFRAMES[timeframe][1] else f"마지막 {timeframe}봉 {last:%Y-%m-%d}")
        st.caption(' · '.join(status))
        if basis:
            st.caption('손절선은 표시된 계획과 시황 기준의 참고값입니다. 본전·추적 청산 및 주문·체결 상태는 별도로 확인합니다.')
        if minute:
            markers = chart_markers(rows, fills or [], bars, group, symbol, show_jev=show_jev) + virtual_markers(
                (shadow or {}).get('trades', extra or []), bars, symbol)
        else:
            markers = containing_markers(fills, bars, symbol) + virtual_markers(
                (shadow or {}).get('trades', extra or []), bars, symbol, minute=False)
        tradingview_chart(bars, sorted(markers, key=lambda m: m['time']), basis, averages=moving_averages(bars, periods),
                          volume=show_volume, intraday=TIMEFRAMES[timeframe][1] is not None, visible=TIMEFRAMES[timeframe][3])
        if not minute:
            st.caption('AI 의견 표식은 1분봉에서만 보입니다. 이 단위에서는 확인 체결과 가상 재현을 해당 봉에 표시합니다.')
        if minute and (now - last).total_seconds() > 240:
            st.warning('마지막 분봉이 4분보다 오래돼 현재 가격으로 사용할 수 없습니다.')
        with st.expander('차트 보는 법 · 가격 데이터'):
            st.caption('보라 ▲(아래) Jev 매수의견 · 분홍 ▼(위) 보유분 Jev 매도의견 · 파랑 ● Decision · 초록 ↑/빨강 ↓ 체결 확인 · 주황 ● 가상 재현 매수 / 자주 ● 가상 재현 매도(실체결 아님). '
                       f"기준(매수 {strategy_settings()[0]['entry_confidence']:.2f}·매도 {strategy_settings()[0]['sell_confidence']:.2f})을 넘은 의견은 '신뢰도 충족'으로 표시합니다. "
                       "'진입 통과·차단'은 같은 판단의 진입 확인 기록이며 주문·체결을 뜻하지 않습니다. 주문이 차단되면 별도 표시하고, 실제 거래는 체결 확인 표식으로 구분합니다. "
                       '그 밖의 의견은 5분마다 신뢰도 숫자를 표시합니다. '
                       f'처음에는 최근 {TIMEFRAMES[timeframe][3]}개 봉을 보여주며 좌우로 스크롤·확대할 수 있습니다. 아래 막대는 거래량, 색 선은 이동평균입니다. '
                       '매입가 위 빨강 음영은 수익, 아래 파랑 음영은 손실 구간(보유와 체결이 대조될 때만). '
                       f"NH 기간별 시세({timeframe}) 조회 {cache['time']:%H:%M:%S}, 화면 자동 갱신은 30초 REST. 체결 표식은 로컬 확인 시각이며 비용 미반영.")
            st.dataframe([{'시각': datetime.fromtimestamp(row['time'], KST).strftime('%Y-%m-%d %H:%M'),
                           '시가': row['open'], '고가': row['high'], '저가': row['low'], '종가': row['close'], '거래량': row.get('volume')}
                          for row in reversed(bars[-30:])], hide_index=True)
    else:
        st.info('선택한 종목의 봉 데이터가 없습니다. 조회 가능 시간과 연결 상태를 확인하세요.')
    investor_panel(read, symbol, name)


EXIT_REQUESTS, MANUAL_BUYS = 'exit-requests.json', 'manual-buys.json'  # the shared paper runner's request files


class _AllHeld:
    """`owned` for reading exit requests on screen: every code counts, so reading never prunes or writes the file (the
    runner drops a request itself once it no longer holds the symbol)."""
    def __contains__(self, code):
        return True


def paper_requests(name):
    """{code: shares or None (all)} pending in a shared paper runner request file, read by the runner's own rules (its
    code pattern keeps letter codes such as 0183J0, which the screen used to drop and the next click erased)."""
    from nh_paper_isolated import exit_requests, manual_buys
    root = get_runtime_root() / 'nhplug' / 'shared-etf-paper'
    return manual_buys(root) if name == MANUAL_BUYS else exit_requests(root, _AllHeld())


def request_paper(name, code, qty):
    """Add one request through the runner's own writer (config_store, 0600); the other pending requests stay."""
    from nh_paper_isolated import write_requests
    write_requests(get_runtime_root() / 'nhplug' / 'shared-etf-paper' / name, {**paper_requests(name), code: qty})


def request_live_exit(root, key, codes, qty=None):
    """Add sell requests for a live account: all shares (qty None) or `qty` shares of each code (nh_live_follow)."""
    from nh_live_follow import exit_request_path, exit_requests, write_request_file
    write_request_file(exit_request_path(root, key), {**exit_requests(root, key), **{c: qty for c in codes}})


def request_live_buy(root, key, code, qty):
    from nh_live_follow import manual_buy_path, manual_buy_requests, write_request_file
    write_request_file(manual_buy_path(root, key), {**manual_buy_requests(root, key), code: qty})


def quick_sell_panel(items, pending, sell, buy, key, note):
    """Hand trades by quantity (user 2026-11-17): per holding a quantity with 매도 / 추가 매수 / 전량 매도, and one 전체
    매도, each behind a confirm popover. `items` are (code, name, qty); `pending` {code: text}; `sell(code, qty or None)`
    and `buy(code, qty)` file the request the runner/mirror carries out next cycle through its guarded path."""
    with st.container(border=True):
        st.markdown('**수동 매수·매도**')
        st.caption(note)
        import disclaimer
        disclaimer.order_note(st)
        if pending:
            st.warning('대기 중인 요청: ' + ', '.join(f'{c} {t}' for c, t in pending.items()))
        if not items:
            st.caption('보유 종목이 없습니다.')
            return

        def filed(action):
            try:
                action()
                st.success('요청을 저장했습니다. 다음 회차(약 1분 안)에 처리합니다.')
                st.rerun()
            except OSError:
                st.error('요청을 저장하지 못했습니다.')
        # visible on the row (user 2026-11-17: the controls hidden in one popover looked missing); each order still
        # asks for a confirm in its own popover
        from vibe_longterm import ASSETS, RESERVOIRS
        from yeo_method import EXTRA

        known_names = {code: name for code, name, *_ in (*ASSETS.values(), *EXTRA.values())} | RESERVOIRS
        labeled = []
        for code, name, qty in items:
            if not name or name == code:
                name = known_names.get(code, code)
            if name != code and not name.endswith(f'({code})'):
                name = f'{name} ({code})'
            labeled.append((code, name, qty))
            label, size, sell_col, buy_col, all_col = st.columns([3, 2, 2, 2, 2], vertical_alignment='bottom')
            label.markdown(f'**{name}** · 보유 {qty:,}주')
            n = int(size.number_input('수량 (주)', min_value=1, max_value=max(qty, 1_000_000), value=min(qty, max(1, qty // 2)),
                                      step=1, key=f'{key}_qty_{code}', help=f'매도는 보유 {qty:,}주 안에서 팝니다.'))
            busy = code in pending
            with sell_col.popover(f'{min(n, qty):,}주 매도', icon=':material/remove_shopping_cart:', width='stretch', disabled=busy):
                st.write(f'{name} {min(n, qty):,}주를 매도합니다.')
                if st.button('매도 확인', key=f'{key}_sell_{code}', width='stretch', disabled=busy):
                    filed(lambda: sell(code, min(n, qty)))
            with buy_col.popover(f'{n:,}주 추가 매수', icon=':material/add_shopping_cart:', width='stretch', disabled=busy):
                st.write(f'{name} {n:,}주를 추가 매수합니다. 주문 가드의 한도가 그대로 적용됩니다.')
                if st.button('매수 확인', type='primary', key=f'{key}_buy_{code}', width='stretch', disabled=busy):
                    filed(lambda: buy(code, n))
            with all_col.popover('전량 매도', icon=':material/sell:', width='stretch', disabled=busy):
                st.write(f'{name} {qty:,}주를 모두 매도합니다.')
                if st.button('전량 매도 확인', key=f'{key}_sellall_{code}', width='stretch', disabled=busy):
                    filed(lambda: sell(code, None))
        with st.popover('전체 종목 매도', icon=':material/delete_sweep:'):
            st.write('보유 종목 ' + ', '.join(f'{n} {q:,}주' for _, n, q in labeled) + '를 모두 전량 매도합니다.')
            if st.button('전체 매도 확인', type='primary', key=f'{key}_sell_all', width='stretch'):
                filed(lambda: [sell(c, None) for c, _, _ in items])


def live_quick_sell(number, key, record, prefix):
    """Sell-now buttons for the live mirror's own holdings (the order tab's sell-all request, per symbol or all)."""
    from nh_live_follow import exit_requests, ledger_owned
    nh = get_runtime_root() / 'nhplug'
    try:
        _, owned = ledger_owned(number, nh)  # as the order tab: the mirror's own holdings by its ledger
    except Exception as exc:
        st.error(f'미러 실행기 보유를 확인하지 못했습니다({type(exc).__name__}).')
        return
    names = stock_names()
    from nh_live_follow import manual_buy_requests
    pending = {**{c: '전량 매도' if q is None else f'{q:,}주 매도' for c, q in exit_requests(nh, key).items()},
               **{c: f'{q:,}주 매수' for c, q in manual_buy_requests(nh, key).items()}}
    quick_sell_panel([(c, names.get(c, c), q) for c, q in sorted(owned.items())], pending,
                     lambda code, qty: request_live_exit(nh, key, [code], qty), lambda code, qty: request_live_buy(nh, key, code, qty), prefix,
                     '미러 실행기가 다음 회차(약 1분 안)에 실계좌 가드(운용금·하루 손실·1주 가격 상한·회당 한도·미체결 차단)를 거쳐 주문합니다. '
                     '전량 매도는 오늘 다시 사지 않고, 일부 매도분은 그 포지션 동안 되사지 않으며, 추가 매수분은 정리 시각(15:00)에 함께 팝니다. '
                     '미러가 산 수량만 대상입니다(직접 산 주식 제외). '
                     + ('실주문 스위치가 켜져 있어 실제로 팝니다.' if record.get('live_orders') is True else
                        '실주문 스위치가 꺼져 있어 매도 계획만 기록하고 주문하지 않습니다.'))


def paper_quick_sell(prefix):
    """Sell-now buttons for the shared paper runner's holdings (its exit requests, per symbol or all)."""
    from engine_compare import account_state
    state = account_state(get_runtime_root() / 'nhplug' / 'shared-etf-paper', datetime.now(KST), max_age=timedelta(minutes=10))
    if state is None:
        st.error('실행기의 최근 잔고(10분 안)를 확인하지 못했습니다. 실행기가 도는지 확인하세요.')
        return
    names = stock_names()
    items = [(c, names.get(c, h.get('iem_nm') or c), int(h['rsdl_qty'])) for c, h in sorted(state['holdings'].items())
             if type(h.get('rsdl_qty')) is int and h['rsdl_qty'] > 0]
    pending = {**{c: '전량 매도' if q is None else f'{q:,}주 매도' for c, q in paper_requests(EXIT_REQUESTS).items()},
               **{c: f'{q:,}주 매수' for c, q in paper_requests(MANUAL_BUYS).items()}}
    quick_sell_panel(items, pending, lambda code, qty: request_paper(EXIT_REQUESTS, code, qty),
                     lambda code, qty: request_paper(MANUAL_BUYS, code, qty), prefix,
                     f"공유 모의 실행기가 다음 회차(약 1분 안)에 AI 판단 없이 보호 경로·주문 가드로 매도하거나 추가 매수합니다(수동 매수는 출처 'manual'로 기록). "
                     f"기준 잔고: 실행기 기록 {LL.clock(state['time'], f'{datetime.now(KST):%Y-%m-%d}')}. 실계좌는 모의 매도를 비율대로 따라 팝니다.")


def account_scope(records, key, default=None):
    """Accounts for the order and record tabs (paper first), as one row of buttons; returns the chosen key."""
    from accounts import ENGINE_LABELS
    engine = lambda r: f" · {ENGINE_LABELS.get(r['engine'], r['engine'])}" if r.get('engine') else ''
    keys = list(records)
    first = default or keys[0]
    return st.segmented_control('계좌', keys, default=first, key=key,
                                format_func=lambda k: f"{records[k].get('label', k)}{engine(records[k])}") or first


def live_number(read, records, key):
    from accounts import resolve as resolve_accounts
    try:
        return resolve_accounts(records, call(read, 'list_accounts', {}, cache_s=ACCOUNT_LIST_CACHE_S).get('Output_0') or []).get(key)
    except Exception:
        return None


def live_order_panel(read, key, record, number):
    """A live account on the order tab: broker balance and orders, the mirror's own orders and recorded fills, and a
    sell-all request the mirror carries out through its guarded path (orders only in --execute)."""
    from engine_compare import _follow_mode
    from live_fills import rows as live_fill_rows
    from nh_live_follow import exit_request_path, exit_requests, ledger_owned
    root = get_runtime_root() / 'nhplug'
    head, button = st.columns([4, 1], vertical_alignment='center')
    head.subheader(f"{record.get('label', key)} 주문·정리")
    if number is None:
        st.error('증권사 연결 계좌 목록에서 이 계좌를 찾지 못했습니다.')
        return
    state = f'live_order_snapshot_{key}'
    if button.button('잔고·주문 조회', key=f'live_order_refresh_{key}', width='stretch', icon=':material/refresh:',
                     help='증권사에서 이 실계좌의 잔고와 오늘 주문을 조회합니다(조회 전용).') or state not in st.session_state:
        try:
            st.session_state[state] = live_broker_snapshot(read, number, datetime.now(KST))
        except Exception:
            st.session_state[state] = None
    snapshot = st.session_state[state]
    mode = _follow_mode(root, key)
    guard, owned = ledger_owned(number, root)
    with guard._db() as db:
        intents = db.execute('SELECT day, operation, symbol, qty, filled, state, order_number FROM live_intents WHERE scope=? '
                             'ORDER BY rowid DESC LIMIT 50', (guard.scope,)).fetchall()
    open_orders = [r for r in (snapshot or {}).get('orders') or [] if type(r.get('미체결수량')) in (int, float) and r['미체결수량'] > 0]
    held = (snapshot or {}).get('holdings') or []
    cash = ((snapshot or {}).get('account') or {}).get('주문가능금액')
    sc.row([{'label': '미러 모드', 'value': '실주문' if mode == '실주문' else '관찰(dry-run)', 'icon': 'bolt' if mode == '실주문' else 'visibility',
             'tone': 'up' if mode == '실주문' else 'warn', 'pulse': mode == '실주문',
             'help': '실주문이면 미러 실행기가 보호 경로로 주문합니다. 관찰이면 계획만 기록하고 주문하지 않습니다.'},
            {'label': '주문가능금액', 'value': won_compact(cash) if isinstance(cash, (int, float)) else '조회 불가', 'icon': 'account_balance_wallet',
             'tone': 'ok' if isinstance(cash, (int, float)) else 'alert'},
            {'label': '보유 · 미러 보유', 'value': f'{len(held)}종 · {len(owned)}종', 'icon': 'inventory_2', 'tone': 'up' if held else 'off',
             'help': '증권사 잔고 종목 수 · 그중 미러 실행기가 산(정리 요청 대상) 종목 수입니다.'},
            {'label': '미체결 주문', 'value': f'{len(open_orders)}건' if snapshot else '조회 불가', 'icon': 'pending_actions',
             'tone': 'alert' if open_orders else 'ok' if snapshot else 'off'}])
    if snapshot is None:
        st.error('증권사 잔고 또는 주문을 확인하지 못했습니다. 빈 계좌나 주문 없음으로 간주하지 마세요.')
    fills = live_fill_rows(root, key)
    names = stock_names()
    label = lambda code: f'{names.get(code, code)} ({code})'
    holdings_tab, orders_tab, mirror_tab, fills_tab = st.tabs(
        [f'보유 종목 {len(held)}', f"오늘의 주문 {len((snapshot or {}).get('orders') or [])}", f'미러 주문 기록 {len(intents)}',
         f'실체결 기록 {len(fills)}'])
    with holdings_tab:
        if held:
            st.dataframe([{'종목': r.get('종목') or names.get(r.get('종목코드'), r.get('종목코드')), '보유': f"{r.get('보유수량', 0):,}주",
                           '미러 보유': f"{owned.get(r.get('종목코드'), 0)}주", '평균매입가': won_compact(r.get('평균매입가')),
                           '현재가': won_compact(r.get('현재가')), '평가손익': signed(r.get('평가손익'), '원'),
                           '수익률': f"{r['수익률(%)']:+.2f}%" if type(r.get('수익률(%)')) in (int, float) else '—'} for r in held], hide_index=True, width='stretch')
        else:
            st.caption('보유 종목이 없습니다.')
    with orders_tab:
        rows = (snapshot or {}).get('orders') or []
        if rows:
            st.dataframe([{'주문 시각': str(r.get('주문 시각') or '')[:2] + ':' + str(r.get('주문 시각') or '')[2:4],
                           '종목': r.get('종목') or names.get(r.get('종목코드'), r.get('종목코드')), '구분': r.get('구분'),
                           '주문': f"{r.get('주문수량') or 0}주", '체결': f"{r.get('체결수량') or 0}주", '미체결': f"{r.get('미체결수량') or 0}주",
                           '평균 체결가': won_compact(r.get('평균체결가')) if r.get('평균체결가') else '—', '거부 사유': r.get('거부 사유') or ''}
                          for r in rows], hide_index=True, width='stretch')
        else:
            st.caption('오늘 주문이 없습니다.')
    with mirror_tab:
        if intents:
            st.dataframe([{'날짜': LL.day(d), '구분': LL.OPERATIONS.get(o, o), '종목': names.get(sym, sym), '주문 수량': f'{q}주',
                           '체결 수량': f'{f}주', '상태': LL.STATES.get(st_, st_), '주문번호': str(n or '—')}
                          for d, o, sym, q, f, st_, n in intents], hide_index=True, width='stretch')
            st.caption('미러 실행기(예전 인버스 실행기 포함)가 보호 경로로 낸 주문입니다. 완료 = 체결 또는 취소까지 증권사에서 확인함.')
        else:
            st.caption('미러 실행기가 이 계좌에 낸 주문이 없습니다' + (' (관찰 모드).' if mode != '실주문' else '.'))
    with fills_tab:
        if fills:
            st.dataframe([{'시각': f['time'][:16].replace('T', ' '), '종목': names.get(f['symbol'], f['symbol']),
                           '구분': '매수 ▲' if f['side'] == 'BUY' else '매도 ▼', '수량': f"{f['qty']}주", '평균 체결가': f'{f["price"]:,.0f}원',
                           '금액': f"{f['price'] * f['qty']:,.0f}원", '주문번호': str(f['order_no'])} for f in reversed(fills)],
                         hide_index=True, width='stretch')
        else:
            st.caption('기록된 실체결이 없습니다. 미러 실행기가 매분 증권사 체결을 기록합니다(2026-11-13부터).')
    with st.container(border=True):
        st.markdown('**보유 종목 정리 요청 (실계좌)**')
        st.caption('선택한 종목을 미러 실행기가 다음 주기(약 1분 안)에 보호 경로로 전량 매도하고 오늘은 다시 사지 않습니다. 미러 실행기가 산 '
                   '수량만 팔며(직접 산 주식은 대상 아님), 매도호가 하한·미체결 차단 등 가드는 그대로입니다. '
                   + ('지금은 실주문 모드라 실제로 매도합니다.' if mode == '실주문' else '지금은 관찰 모드라 매도 계획만 기록하고 주문하지 않습니다.'))
        requested = sorted(exit_requests(root, key))
        if requested:
            left, right = st.columns([4, 1], vertical_alignment='center')
            left.warning('대기 중인 정리 요청: ' + ', '.join(label(c) for c in requested))
            if right.button('요청 취소', key=f'live_exit_cancel_{key}', width='stretch'):
                exit_request_path(root, key).unlink(missing_ok=True)
                st.rerun()
        choices = [c for c in owned if c not in requested]
        if not choices:
            st.caption('미러 실행기가 보유한 종목이 없습니다.')
            return
        choice = st.multiselect('정리할 종목', choices, format_func=lambda c: f'{label(c)} {owned[c]}주', key=f'live_exit_pick_{key}')
        sure = st.checkbox('선택 종목을 시장 상황과 관계없이 전량 매도하는 데 동의합니다', key=f'live_exit_confirm_{key}')
        if st.button('정리 요청 보내기', type='primary', disabled=not (choice and sure), icon=':material/sell:', key=f'live_exit_send_{key}'):
            try:
                request_live_exit(root, key, choice)
                st.success('정리 요청을 저장했습니다. 미러 실행기 기록과 체결을 확인하세요.')
                st.rerun()
            except OSError:
                st.error('정리 요청을 저장하지 못했습니다.')


def live_record_panel(key, record):
    """A live account on the record tab: its trading decisions are its engine's, so this shows what the mirror did with
    them minute by minute (plans, executions, blocks) as changes only."""
    from engine_compare import recent_events as journal_tail
    from accounts import ENGINE_LABELS
    engine = ENGINE_LABELS.get(record.get('engine'), record.get('engine'))
    st.subheader(f"{record.get('label', key)} 실행 기록")
    st.info(f'실계좌는 자체 AI 판단이 없습니다. 매매 판단은 {engine} 엔진 기록과 같으니 위 "기록" 버튼에서 {engine}을 고르면 판단 근거와 차트를 봅니다. '
            '여기서는 그 판단을 미러 실행기가 이 계좌에서 어떻게 실행(또는 계획)했는지 봅니다.', icon=':material/info:')
    events = [e for e in journal_tail(get_runtime_root() / 'nhplug' / 'live-follow.jsonl', 8000) if e.get('account') == key]
    days = sorted({e['time'][:10] for e in events if e.get('time')}, reverse=True)
    if not days:
        st.caption('미러 실행기 기록이 없습니다.')
        return
    day = st.selectbox('기록 날짜 (한국시간)', days, key=f'live_record_day_{key}')
    today = [e for e in events if e.get('time', '').startswith(day)]
    count = lambda pred: sum(1 for e in today if pred(e))
    executed = count(lambda e: str(e.get('execution', '')).startswith(('ACKNOWLEDGED', 'CANCEL')))
    planned = count(lambda e: e.get('action') in ('BUY', 'SELL'))
    blocked = count(lambda e: e.get('action') == 'BLOCK' or e.get('execution') == 'BLOCKED')
    sc.row([{'label': '기록', 'value': f'{len(today)}회', 'icon': 'history', 'tone': 'ai' if today else 'off',
             'sub': f"{today[0]['time'][11:16]} ~ {today[-1]['time'][11:16]}"},
            {'label': '매매 계획', 'value': f'{planned}회', 'icon': 'swap_vert', 'tone': 'up' if planned else 'off',
             'sub': f"매수 {count(lambda e: e.get('action') == 'BUY')} · 매도 {count(lambda e: e.get('action') == 'SELL')} (매분 같은 계획 반복 포함)"},
            {'label': '실주문 접수', 'value': f'{executed}건', 'icon': 'bolt', 'tone': 'up' if executed else 'off'},
            {'label': '차단·오류', 'value': f'{blocked}회', 'icon': 'block', 'tone': 'alert' if blocked else 'ok'}])
    changes, previous = [], None
    for e in today:
        signature = (e.get('action'), e.get('symbol'), e.get('reason'), e.get('execution'), e.get('error_type'))
        if signature != previous:
            changes.append(e)
            previous = signature
    names = stock_names()
    st.markdown('##### 계획이 바뀐 시점')
    st.dataframe([{'시각': e['time'][11:19], '계획': LL.ACTIONS.get(e.get('action'), e.get('action') or ''),
                   '종목': names.get(e.get('symbol'), e.get('symbol') or '—') if e.get('symbol') else '—',
                   '수량': f"{e['qty']}주" if e.get('qty') else '—', '가격': f"{e['price']:,}원" if e.get('price') else '—',
                   '사유': LL.reason(e.get('reason')) or ('오류: ' + str(e.get('error_type')) if e.get('error_type') else ''),
                   '매수 근거': ', '.join(f'{names.get(c, c)}: {LL.reason(v)}' for c, v in (e.get('buy_basis') or {}).items()) or '—',
                   '실행': LL.EXECUTIONS.get(e.get('execution'), e.get('execution') or '—'),
                   '모드': '실주문' if e.get('mode') == 'execute' else '관찰'}
                  for e in reversed(changes)], hide_index=True, width='stretch')
    st.caption('같은 계획이 매분 반복되면 처음 한 번만 보입니다. "계획만 기록(관찰)"은 주문을 내지 않은 관찰 모드 계획입니다.')


def regime_observation_panel(path):
    """7 ETF regime observation (research only)."""
    st.caption('매매 신호가 아니며 실제 주문과 수익성을 뜻하지 않습니다.')
    try:
        observation = read_regime_observation(path.parent / 'regime-observation.json', datetime.now(KST))
        if observation is None:
            st.info('국면 관찰 기록이 아직 없습니다. 연구 기능 검증 중입니다.')
        else:
            stamp = observation['time'].strftime('%Y-%m-%d %H:%M:%S')
            minute = observation['minute_time']
            minute_text = minute.strftime('%Y-%m-%d %H:%M:%S') if minute else '확인 불가'
            st.caption(f"기록 시각: {stamp} · 분봉 입력 시각: {minute_text} · 출처: {observation['source']} · 상태: {observation['status']}")
            if observation['stale']:
                st.warning('국면 관찰이 오래됐거나 기록 시각이 미래입니다. 현재 상태로 사용하지 마세요.')
            if observation['status'] in ('partial', 'unavailable'):
                st.warning('일부 또는 전체 ETF의 국면 입력이 부족합니다. 표시된 데이터를 확인하세요.')
            if observation['rows']:
                chips = st.columns(4)
                for i, row in enumerate(observation['rows']):
                    mode = str(row.get('국면', ''))
                    color = 'gray' if row.get('데이터 상태') != 'valid' else 'red' if any(w in mode for w in ('상승', 'up', 'bull', 'trend')) \
                        else 'blue' if any(w in mode for w in ('하락', 'down', 'bear')) else 'violet'
                    with chips[i % 4]:
                        st.caption(row['종목'])
                        st.badge(mode, color=color)
                with st.expander('국면 관찰 표'):
                    st.dataframe(observation['rows'], hide_index=True)
            else:
                st.info('표시할 국면 관찰 종목이 없습니다.')
    except ValueError:
        st.warning('국면 관찰 기록을 확인할 수 없습니다.')


def live_linked_numbers(read, records):
    """{key: account number} of the live accounts linked at the broker (memory only, never shown)."""
    from accounts import resolve as resolve_accounts
    try:
        return resolve_accounts(records, call(read, 'list_accounts', {}, cache_s=ACCOUNT_LIST_CACHE_S).get('Output_0') or [])
    except Exception:
        return {}


def record_live_balances(read, records):
    """종합 현황 '실계좌 잔고 조회' (user 2026-11-18): read each linked live account's balance once (read-only) and append it
    to live-balance-<key>.jsonl, the log the mirror and long-term runners keep. Returns the problems, in words."""
    from live_fills import record_balance
    numbers, now, problems = live_linked_numbers(read, records), datetime.now(KST), []
    for key, record in records.items():
        if record.get('kind') != 'live':
            continue
        if not numbers.get(key):
            problems.append(f"{record.get('label', key)}: 증권사 연결 계좌를 찾지 못함")
            continue
        try:
            if not record_balance(get_runtime_root() / 'nhplug', key, live_balance(read, numbers[key]), now, every=0):
                problems.append(f"{record.get('label', key)}: 잔고 합계를 읽지 못함")
        except Exception:
            problems.append(f"{record.get('label', key)}: 잔고 조회 실패")
    return problems


def live_shared_events():
    """The shared runner's recent events (one-minute prices for the virtual run's unrealized column)."""
    try:
        return read_events((get_runtime_root() / 'nhplug' / 'shared-etf-paper' / 'events.jsonl'), 20000)
    except (OSError, ValueError):
        return []


def live_mirror_row(key, record, states, events):
    """One live account at a glance: executor, mode, ledger it mirrors, today's virtual run and its limits."""
    from engine_roles import main_engine
    from accounts import live_rules
    from nh_live_follow import mirror_target, paper_book, paper_source, tranche_shares
    from nh_live_shadow import effective_rules
    label = record.get('label', key)
    event = follow_state(key) or {}
    stamp = event_time(event.get('time')) if event else None
    from accounts import ENGINE_LABELS
    engine = ENGINE_LABELS.get(record['engine'], record['engine'])
    try:
        role = '메인 · 공유 모의 원장' if main_engine(get_runtime_root() / 'nhplug') == record['engine'] else '보조 · 가상 원장'
        source = paper_source(get_runtime_root() / 'nhplug', record['engine'])
        target = (mirror_target(paper_book(source, tranche_shares(record, root=get_runtime_root() / 'nhplug', source=source)))
                  if source and source.exists() else {})
    except (ImportError, OSError, ValueError, KeyError, sqlite3.Error):
        role, target = '확인 불가', None
    try:
        result = shadow_result(record, events)
    except Exception:  # an unreadable ledger leaves the virtual run empty, never the other accounts
        result = {'trades': [], 'realized': 0, 'open': {}}
    budget = record['budget']
    return {'key': key, 'label': label, 'engine': engine, 'role': role, 'state': states.get(f'{label} 미러 실행기', '확인 불가'),
            'mode': '실주문' if event.get('mode') == 'execute' else '계획만 기록', 'event': event, 'stamp': stamp,
            'target': target, 'result': result, 'stop': effective_rules(record)['stop_loss_pct'], 'budget': budget,
            'rules': live_rules(record)}


def live_balances_view(read, rows, records, numbers, scope):
    """Broker balance and today's orders of the given live accounts (read-only), one table each with an account column."""
    names = ', '.join(r['label'] for r in rows)
    if st.button(f'{names} 잔고·주문 조회', type='primary', key=f'live_balance_{scope}',
                 help='증권사에서 예수금·보유 종목과 당일 주문·체결·취소를 조회합니다(조회 전용).'):
        now, snapshot = datetime.now(KST), {'time': datetime.now(KST), 'cash': {}, 'holdings': [], 'orders': [], 'errors': []}
        for row in rows:
            number = numbers.get(row['key'])
            if not number:
                snapshot['errors'].append(f"{row['label']}: 증권사 연결 계좌를 찾지 못함")
                continue
            try:
                balance = live_balance(read, number)
                snapshot['cash'][row['label']] = balance['Output_0'].get('dca')
                snapshot['holdings'] += [{'계좌': row['label'], **r} for r in display_rows(balance['Output_1'], [
                    ('iem_nm', '종목'), ('iem_cd', '종목코드'), ('rsdl_qty', '잔량'), ('phs_pr', '평균단가'), ('now_pr', '현재가'),
                    ('eal_pls_amt', '평가손익'), ('pft_rt', '수익률(%)')])]
                snapshot['orders'] += [{'계좌': row['label'], **r} for r in live_order_rows(read, number, now)]
            except Exception:
                snapshot['errors'].append(f"{row['label']}: 잔고 또는 주문 전체 내역 확인 실패 — 비었다고 간주하지 마세요")
        st.session_state[f'live_balance_{scope}_data'] = snapshot
    snapshot = st.session_state.get(f'live_balance_{scope}_data')
    if not snapshot:
        st.caption('위 버튼으로 조회해 주세요. 조회 전에는 아무것도 표시하지 않습니다.')
        return
    st.caption(f"조회 {snapshot['time']:%Y-%m-%d %H:%M:%S} · 다시 조회하기 전까지 과거 상태입니다")
    for error in snapshot['errors']:
        st.error(error)
    cash = [v for v in snapshot['cash'].values() if isinstance(v, (int, float))]
    columns = st.columns(len(snapshot['cash']) + (1 if len(snapshot['cash']) > 1 else 0) or 1)
    for col, (label, value) in zip(columns, snapshot['cash'].items()):
        col.metric(f'{label} 예수금', f'{value:,.0f}원' if isinstance(value, (int, float)) else '확인 불가')
    if len(snapshot['cash']) > 1:
        columns[-1].metric('예수금 합계', f'{sum(cash):,.0f}원' if len(cash) == len(snapshot['cash']) else '일부 확인 불가')
    st.markdown('**보유 종목**')
    if snapshot['holdings']:
        st.dataframe(snapshot['holdings'], hide_index=True, width='stretch')
    else:
        st.caption('조회된 보유 종목이 없습니다.')
    st.markdown('**당일 주문·체결·취소**')
    if snapshot['orders']:
        st.dataframe(snapshot['orders'], hide_index=True, width='stretch')
    else:
        st.caption('조회된 당일 주문이 없습니다.')


def live_buy_controls(root, *, mirror=True):
    start, stop = st.columns(2) if mirror else (None, st)
    if stop.button('실계좌 신규 매수 중단', icon=':material/pause_circle:', width='stretch',
                   help='신규 매수 중단 표시를 저장합니다. 다시 허용하려면 실행 기록을 대조한 뒤 별도로 해제해야 합니다.'):
        try:
            if stop_live(root):
                st.warning('신규 매수 중단을 요청했습니다. 계좌별 실행 기록과 미체결 상태를 확인하세요.')
            else:
                st.error('신규 매수 중단 표시를 확인하지 못했습니다. 실행 상태와 증권사 주문을 확인하세요.')
        except Exception:
            st.error('중단 요청을 저장하지 못했습니다. 실행 상태를 확인하세요.')
    if start is not None:
        start.button('실계좌 자동매매 시작', icon=':material/play_circle:', disabled=True, width='stretch',
                     help='모의 검증과 별도 승인 전에는 이 화면에서 시작할 수 없습니다.')
        st.caption('Jev·Decision 확인과 AI 관찰기가 모두 확인돼야 실계좌 시작을 허용합니다. 모의 주문·체결 검증 후 설정을 연결합니다.')


def live_tab_panel(root, read, configs, all_records):
    """Live accounts: a combined view of every live account, every account's orders (the old 주문·정리 tab, 2026-11-19)
    and one view per account (executor, mirror, virtual run, limits, orders, balance), then the shared controls and the
    paper-to-live review."""
    st.subheader('실계좌')
    records = {k: r for k, r in all_records.items() if r['kind'] == 'live'}
    if not records:  # the controls below (buy pause) stay available even then
        st.info('등록된 실계좌가 없습니다. "계좌·자동매매" 탭에서 추가합니다.')
    views = ['전체 합산', PREFS.LIVE_ORDERS, *[r.get('label', k) for k, r in records.items()]]
    pick = st.segmented_control('보기', views, default=views[0], key='live_view',
                                help='전체 합산은 모든 실계좌를 한 화면에, 주문·정리 종합은 모든 계좌(모의 포함)의 보유·체결·정리 요청을, '
                                     '계좌 이름을 누르면 그 계좌만 자세히 봅니다.') or views[0]
    if pick == PREFS.LIVE_ORDERS:
        import overview
        overview.render_orders(root / 'nhplug', all_records, processes(), stock_names())
        return
    states, events = processes(), live_shared_events()
    allowed, halted = buy_allowed(root / 'nhplug'), halt_flag_set('nhplug')
    # user 2026-11-17: a live account runs Jev trading (mirror rows below) or 장기투자 (its own card)
    from accounts import longterm_accounts
    longterm = longterm_accounts(records)
    rows = [live_mirror_row(k, r, states, events) for k, r in records.items() if k not in longterm]
    control = [{'label': '신규 매수', 'value': '허용' if allowed else '중단 요청됨', 'tone': 'ok' if allowed else 'alert',
                'icon': 'play_circle' if allowed else 'pause_circle',
                'help': '"자동매매 제어" 탭의 중단 버튼으로 바뀝니다. 모든 실계좌 미러 실행기가 이 표시를 따릅니다. 중단해도 매도·손절은 계속합니다.'},
               {'label': '주문 보호(HALT)', 'value': '켜짐' if halted else '꺼짐', 'tone': 'alert' if halted else 'off',
                'icon': 'gpp_bad' if halted else 'verified_user',
                'help': '전역 중단 표시입니다. 켜져 있으면 실계좌 주문 경로가 막힙니다. 다른 증권사 HALT와는 별개입니다.'}]
    if pick == views[0]:
        import longterm_account_view as ltv
        nhplug = root / 'nhplug'
        longterm_rows = {k: ltv.status(k, record, nhplug) for k, record in longterm.items()}
        longterm_states = {k: states.get(f"{record.get('label', k)} 장기투자 실행기", '확인 불가')
                           for k, record in longterm.items()}
        executor_states = [r['state'] for r in rows] + list(longterm_states.values())
        running = executor_states.count('실행 중')
        realized = sum(int(r['result']['realized']) for r in rows)
        sc.row([{'label': '실계좌 실행기', 'value': f'{running}/{len(executor_states)} 실행 중',
                 'tone': 'off' if not running else 'ok' if running == len(executor_states) else 'warn',
                 'icon': 'sync_alt', 'pulse': bool(running),
                 'sub': f"미러 {sum(r['state'] == '실행 중' for r in rows)}/{len(rows)} · 장기투자 {list(longterm_states.values()).count('실행 중')}/{len(longterm)}",
                 'help': '미러와 장기투자 실계좌의 프로세스 상태입니다. 실주문 설정, 주문 계획과 보유 현황은 아래에서 확인합니다.'},
                {'label': '미러 오늘 가상 실현', 'value': f'{realized:+,}원' if rows else '—', 'tone': sc.signed(realized), 'icon': 'science',
                 'sub': f"가상 매매 {sum(len(r['result']['trades']) for r in rows)}건 · 수수료·세금 미반영",
                 'help': '각 계좌가 엔진 원장을 미러했을 때 오늘의 실현손익 합계입니다(실제 체결 아님).'},
                *control])
        import dialogs as dlg
        dlg.buttons([(f"자세히 · {r['label']}", f"live_d_{r['key']}", dlg.body_dialog,
                      (f"{r['label']} 규칙·오늘 가상 매매", lambda r=r: (live_rules_card(records[r['key']]), shadow_view(r['result'])),))
                     for r in rows]
                    + [('자세히 · 실행 기록', 'live_d_events', dlg.live_events_dialog,
                        ('실계좌 미러 실행 기록', recent_events(root / 'nhplug/live-follow.jsonl'), LL))], columns=4)
        st.markdown('##### 계좌별 한눈에 보기')
        import engine_compare as ec
        import live_fills
        names = ltv.asset_names_by_code() | stock_names()
        holdings = {}
        for key in records:
            fills = live_fills.rows(nhplug, key)
            positions = ec.positions(ec.real_journal(fills, []))
            holdings[key] = (' · '.join(f"{names.get(code, code)} ({code}) {p['qty']:,}주" for code, p in sorted(positions.items()))
                             or ('원장상 0주' if fills else '체결 기록 없음'))
        table = []
        for r in rows:
            target = r['target']
            table.append({'계좌': r['label'], '엔진': r['engine'], '따라가는 원장': r['role'], '실행기': r['state'], '모드': r['mode'],
                          '마지막 기록': (f"{r['stamp']:%m/%d %H:%M} {LL.ACTIONS.get(r['event'].get('action'), r['event'].get('action', ''))}"
                                           f" · {LL.reason(r['event'].get('reason'))}" if r['stamp'] else '없음'),
                          '체결 원장 보유': holdings[r['key']],
                          '미러 목표': ('확인 불가' if target is None else ' · '.join(f'{names.get(c, c)} {n}주' for c, n in target.items()) or '0주'),
                          '오늘 가상 매매': f"{len(r['result']['trades'])}건", '오늘 가상 실현': f"{int(r['result']['realized']):+,}원",
                          '매수 크기': '1주' if r['rules']['sizing'] == 'share' else f"{r['rules']['tranche_krw'] // 10_000:,}만 원",
                          '한도 (운용금 · 1주 상한 · 하루 손실 · 손절)': f"{r['budget']['capital_krw'] // 10_000:,}만 · {r['budget']['order_price_max_krw'] // 10_000:,}만 · "
                                                                f"{r['budget']['daily_loss_krw'] // 10_000:,}만 · −{r['stop']:g}%"})
        for key, data in longterm_rows.items():
            table.append({'계좌': records[key].get('label', key), '엔진': '장기투자', '따라가는 원장': '장기투자 엔진 계획',
                          '실행기': longterm_states[key], '모드': data['mode'], '마지막 기록': data['today'].get('day') or '없음',
                          '체결 원장 보유': holdings[key], '미러 목표': '—', '오늘 가상 매매': '—', '오늘 가상 실현': '—',
                          '매수 크기': '계좌 전략 기준', '한도 (운용금 · 1주 상한 · 하루 손실 · 손절)': '계좌 설정 참조'})
        if table:
            st.dataframe(table, hide_index=True, width='stretch')
            st.caption('체결 원장 보유는 기록된 실체결을 누적한 수량입니다. 증권사 최신 잔고는 아래 잔고·주문 조회에서 확인할 수 있습니다.')
        if longterm:
            st.markdown('##### 장기투자 실계좌')
            for k, record in longterm.items():
                ltv.status_card(k, record, nhplug, data=longterm_rows[k])
        selected = rows + [{'key': k, 'label': r.get('label', k)} for k, r in longterm.items()]
        st.caption('계좌 이름을 위에서 누르면 그 계좌의 규칙·오늘 가상 매매·잔고를 자세히 봅니다. 한도는 계좌·자동매매 탭에서 계좌마다 정합니다.')
    elif (chosen := next((k for k, r in longterm.items() if r.get('label', k) == pick), None)):
        import longterm_account_view
        label = longterm[chosen].get('label', chosen)
        selected = [{'key': chosen, 'label': label}]

        def broker_view():
            live_balances_view(read, selected, records, live_linked_numbers(read, records), chosen)
            st.caption('신규 매수 중단은 실계좌 전체에 적용됩니다.')
            live_buy_controls(root, mirror=False)

        def load_assets(refresh):
            number = live_number(read, records, chosen)
            if not number:
                raise ValueError('증권사 연결 계좌 확인 불가')

            def fetch():
                balance = live_balance(read, number)
                return broker_snapshot(balance, {'Output_0': []}, datetime.now(KST))

            return shared_snapshot('longterm-assets', number, fetch, refresh)

        longterm_account_view.desk(chosen, longterm[chosen], root / 'nhplug', control,
                                   states.get(f'{label} 장기투자 실행기', '확인 불가'), broker_view=broker_view,
                                   load_assets=load_assets, chart=tradingview_chart)
        return
    else:
        r = next(row for row in rows if row['label'] == pick)
        record = records[r['key']]
        tone, icon, pulse = sc.service(r['state'])
        sc.row([{'label': '미러 실행기', 'value': r['state'], 'tone': tone, 'icon': icon, 'pulse': pulse, 'sub': r['mode'],
                 'help': f"{r['label']}의 실행기(com.vibetrading.{'live-follow-' + r['key'][-1]})입니다. 평일 08:58 시작, 15:20 종료."},
                {'label': '따라가는 원장', 'value': r['engine'], 'tone': 'ai', 'icon': 'account_tree', 'sub': r['role'],
                 'help': '이 계좌의 엔진이 메인이면 공유 모의계좌 원장, 보조이면 그 엔진의 가상 원장을 따라갑니다.'},
                *control])
        live_rules_card(record)
        sc.row([{'label': '운용금', 'value': f"{r['budget']['capital_krw']:,}원", 'tone': 'warn', 'icon': 'account_balance_wallet',
                 'help': '이 계좌의 실계좌 매입 금액 최대치입니다(계좌 설정에서 실계좌 잔고 안으로 정함).'},
                {'label': '하루 손실 한도', 'value': f"{r['budget']['daily_loss_krw']:,}원", 'tone': 'down', 'icon': 'shield',
                 'help': '실주문 직전 손익을 다시 조회해 닿으면 그날 매수를 막고 정리합니다. 시세 급변·미체결로 초과 손실이 날 수 있습니다.'},
                {'label': '매수 크기', 'value': '1주 기준' if r['rules']['sizing'] == 'share' else f"{r['rules']['tranche_krw'] // 10_000:,}만 원 기준",
                 'tone': 'warn', 'icon': 'counter_1',
                 'sub': (f"1회 매수 최대 {r['rules']['max_order_qty']:,}주 · " if r['rules']['sizing'] == 'share' else '운용금 비율 · 주수 제한 없음 · ')
                        + f"1주 {r['budget']['order_price_max_krw']:,}원 이하 · 추격 {r['rules']['chase_pct']:g}%",
                 'help': '1주 기준: 모의 분할 1회마다 1주(1회 최대 수량 적용). 금액 기준: 모의 체결 금액의 운용금 비율만큼, 체결 1건당 정한 금액 이하. 매도는 보유·매도가능 수량 이내입니다.'},
                {'label': '계좌 손절 설정', 'value': f"−{r['stop']:g}%", 'tone': 'down', 'icon': 'trending_down',
                 'help': LIVE_STOP_GUIDE}])
        follow_mode_view(r['key'], 'panel_')
        st.markdown('##### 오늘 가상 매매 (미러 계산)')
        shadow_view(r['result'])
        selected = [r]
    with st.expander('실계좌 주문은 어떻게 나가나요? (도식)', icon=':material/schema:'):
        import guide_ui as GU
        from guides import live_flow
        GU.flow('jev-live', live_flow(guide_values()), live_dot(guide_values()))
        L = LIVE_LIMITS
        st.caption(f"점선은 별도 승인 후에만 열리는 경로입니다. 이 대시보드는 실주문을 내지 않습니다. 승인된 최대 한도: 운용금 {L['capital_krw']:,}원 · "
                   f"하루 손실 {L['daily_loss_krw']:,}원 · 1회 최대 {L['order_qty']:,}주 · 1주 가격 {L['order_price_max_krw']:,}원 · 종목 손절 −{L['stop_pct']:g}%")
    live_events = recent_events(root / 'nhplug/live-follow.jsonl')
    if pick != views[0]:  # one account: its orders and sell-all request (the old 주문·정리 tab) come first
        order_tab, balance_tab, control_tab, review_tab = st.tabs(['주문·정리', '잔고·주문', '자동매매 제어', '모의 → 실계좌 적용 검토'])
        with order_tab:
            key = selected[0]['key']
            live_order_panel(read, key, records[key], live_number(read, records, key))
    else:
        balance_tab, control_tab, review_tab = st.tabs(['잔고·주문', '자동매매 제어', '모의 → 실계좌 적용 검토'])
    with balance_tab:
        if selected:
            live_balances_view(read, selected, records, live_linked_numbers(read, records), 'all' if pick == views[0] else selected[0]['key'])
        else:
            st.caption('조회할 실계좌가 없습니다.')
    with control_tab:
        st.caption('손절 감시: 조회 완료 후 30초 간격 · 미체결 매수 취소 시도: 최초 확인 30초 후. 실제 주기는 조회 지연에 따라 길어질 수 있습니다.')
        st.warning('중단은 신규 매수 중단입니다. 실행기가 작동 중이면 미체결 매수를 취소·대조하고 보유분 손절·익절 관리는 계속합니다. '
                   '매도와 취소가 실제로 체결·완료된다는 보장은 아닙니다.')
        live_buy_controls(root)
        st.markdown('**최근 실행 기록**')
        if live_events:
            st.dataframe([{k: str(e.get(k, '')) for k in ('time', 'account', 'mode', 'action', 'symbol', 'reason', 'execution')}
                          for e in live_events[-30:]], hide_index=True, width='stretch')
        else:
            st.caption('실행 기록이 없거나 읽을 수 없습니다.')
    with review_tab:
        st.caption('모의 연구 자료가 실계좌에 써도 될 만큼 쌓였는지 보는 로컬 점검입니다. 실계좌 주문 권한이나 자동 전환을 주지 않습니다.')
        if st.button('모의 연구자료 검토', help='가격 관측·Jev 연구·모의 체결 기록이 기준을 채웠는지 확인합니다.'):
            try:
                from nh_live_research_gate import evaluate
                report = evaluate(get_runtime_root() / 'nhplug/shared-etf-paper')
                st.warning('실계좌 적용 검증 미완료' if report['status'] == 'NOT_READY' else '적용 상태를 별도 확인하세요.')
                st.write('가격 관측', report.get('price_observations', {}))
                st.write('Jev 연구', report.get('jev_research', {}))
                st.write('모의 체결 기록', report.get('paper_fills', {}))
                st.caption('증권사 독립 대조, 거래 비용 및 독립 전진 검증은 이 화면에서 완료되지 않습니다.')
            except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
                st.error('모의 연구자료를 확인하지 못했습니다. 실계좌 적용 여부는 미확인입니다.')


def guard_text():
    """The shared paper order guard's limits as saved on the dashboard (the text used to hard-code the defaults)."""
    from accounts import load, paper_limits
    try:
        L = paper_limits(load()['paper-shared'])
    except (OSError, KeyError, ValueError):
        from accounts import DEFAULT_PAPER_LIMITS as L
    return (f"회당 {krw_words(L['order_max_krw'])} · 하루 매수 {krw_words(L['daily_buy_krw'])} · 보유 {krw_words(L['held_max_krw'])} · "
            f"하루 새 종목 {L['daily_new_symbols']}개 · 하루 손실 {krw_words(L['daily_loss_krw'])}")


def auto_on():
    """The header's single auto-refresh switch (user 2026-11-17: one switch for every page instead of one per page)."""
    return bool(st.session_state.get('auto_all', (st.session_state.get('prefs') or {}).get('quotes_auto', False)))


def refresh_s():
    return st.session_state.get('prefs', PREFS.DEFAULTS)['refresh_s']


def live_every(now=None):
    """run_every of the fragments that poll NH quotes and balances: the refresh interval while KRX trades (09:00~15:30
    on a KRX trading day) with auto refresh on, else None. Polling every 30 s through evenings and holidays made the
    dashboard NH's top caller beside the runners' rate-limit errors."""
    from nh_paper_isolated import is_trading_day
    import session_times
    now = now or datetime.now(KST)
    trading = session_times.MARKET_OPEN <= f'{now:%H:%M}' < '15:30' and is_trading_day(now.date())
    return f'{refresh_s()}s' if auto_on() and trading else None


def prefs_path():
    return get_runtime_root() / 'nhplug' / 'dashboard-prefs.json'


def prefs_view():
    """Load saved display preferences once per session; status cards read status_blink from session state."""
    if 'prefs' not in st.session_state:
        st.session_state.prefs = PREFS.load(prefs_path())
    else:
        st.session_state.prefs = PREFS.load_from(st.session_state.prefs)
    st.session_state.status_blink = st.session_state.prefs['status_blink']


def apply_font_scale(scale):
    """Scale dashboard text and Graphviz diagrams with the display preference."""
    if type(scale) is not int or scale not in PREFS.FONT_SCALES:
        scale = PREFS.DEFAULTS['font_scale']
    style = '' if scale == 100 else f'html {{ font-size: {scale}% !important; }}'
    if scale < 100:
        style += f' .stGraphVizChart svg {{ max-width: {scale}% !important; height: auto !important; }}'
    st.html(f'<style>{style}</style>')


def apply_theme():
    """Page styles and the browser-side theme sync for the saved mode. Kept in the page body (pop-over content is not
    mounted while it is closed)."""
    mode = st.session_state.prefs['theme']
    st.html(f'<style>{THEME.css(mode)}</style>' + THEME.script(mode), unsafe_allow_javascript=True)


def theme_button():
    """One button cycling 시스템 → 다크 → 라이트 (saved in dashboard-prefs.json), the first control of 화면 설정."""
    def cycle():
        st.session_state.prefs = PREFS.save(prefs_path(), {**st.session_state.prefs,
                                                           'theme': THEME.next_mode(st.session_state.prefs['theme'])})
    label, icon = THEME.LABELS[st.session_state.prefs['theme']]
    st.button(label, icon=f':material/{icon}:', key='theme_toggle', on_click=cycle, width='stretch',
              help='누를 때마다 시스템(OS 설정) → 다크 → 라이트 순서로 바뀝니다. 바뀔 때 화면을 한 번 새로 엽니다.')


def prefs_form():
    """Top-right display settings, saved for the next visit. Display only: trading settings live in 계좌·자동매매."""
    current = st.session_state.prefs
    theme_button()  # a plain button: st.form only allows its submit button
    with st.form('prefs_form', border=False):
        font_scale = st.selectbox('화면 글씨 크기', PREFS.FONT_SCALES,
                                  index=PREFS.FONT_SCALES.index(current['font_scale']),
                                  format_func=lambda value: '기본 (100%)' if value == 100 else f'{value}%',
                                  help='대시보드 글씨와 여백, 도식 크기를 조절합니다. 차트와 일부 표의 글씨는 별도 크기를 사용합니다.')
        blink = st.toggle('상태 깜박임', value=current['status_blink'],
                          help='카드의 작은 점이 실행 중·주의 상태일 때 천천히 깜박입니다(시스템의 "동작 줄이기"에서도 멈춥니다).')
        start = st.selectbox('처음 여는 탭', PREFS.TABS, index=PREFS.TABS.index(current['start_tab']))
        refresh = st.segmented_control('자동 갱신 간격(초)', PREFS.REFRESH, default=current['refresh_s'],
                                       help='시세·트레이딩 현황·운영 상태의 자동 갱신 간격입니다. 짧을수록 증권사 조회가 늘어납니다.')
        quotes = st.toggle('자동 갱신 기본 켜기 (전체 화면)', value=current['quotes_auto'])
        selection = st.toggle('글자를 드래그해 AI에게 묻기', value=current['ask_selection'],
                              help='화면의 글자를 선택하면 옆에 AI에게 묻기 버튼이 뜹니다. 질문은 채팅창에 채우기만 하고, 보내기를 눌러야 전송됩니다.')
        unit = st.segmented_control('기본 금액 단위', PREFS.UNITS, default=current['money_unit'],
                                    help='계좌·자동매매 금액 칸이 처음 열릴 때의 단위입니다.')
        if st.form_submit_button('저장', type='primary', icon=':material/save:', width='stretch'):
            st.session_state.prefs = PREFS.save(prefs_path(), {**current, 'status_blink': blink, 'start_tab': start, 'refresh_s': refresh or current['refresh_s'],
                                                                'quotes_auto': quotes, 'ask_selection': selection, 'money_unit': unit or current['money_unit'],
                                                                'font_scale': font_scale})
            st.session_state.pop('quotes_auto', None)
            st.session_state.pop('auto_all', None)
            st.rerun()
    st.caption('화면 표시 설정만 바꿉니다. 매매·한도·주문은 바뀌지 않습니다. '
               f"저장 위치: 런타임 폴더의 dashboard-prefs.json · 지금 갱신 {current['refresh_s']}초")
    if st.button('실행기 상태 · 자동 작업', icon=':material/list_alt:', width='stretch', key='prefs_services'):
        import dialogs as dlg
        dlg.services_dialog(processes())


def main():
    logo = Path(__file__).with_name('assets') / 'logo.png'
    st.set_page_config(page_title='Omni AI Trading', page_icon=str(logo) if logo.is_file() else None, layout='wide',
                       menu_items={'About': 'Omni AI Trading · NH 나무 PLUG 모의·실계좌 자동매매 관찰 대시보드. '
                                            '이 화면은 실주문을 내지 않습니다(실계좌 실주문 스위치만 예외, 확인 문구 필요). '
                                            '모든 정보는 참고 자료이며 투자 권유가 아닙니다. 투자 판단과 손익 책임은 투자자 본인에게 있습니다.'})
    if logo.is_file():
        st.logo(str(logo), size='large')
    prefs_view()
    apply_font_scale(st.session_state.prefs['font_scale'])
    sc.inject()
    apply_theme()
    title, controls = st.columns([3, 2], vertical_alignment='bottom')
    controls = controls.container(horizontal=True, horizontal_alignment='right', vertical_alignment='center')  # side by side
    title.title('Omni AI Trading')
    controls.toggle(f'{refresh_s()}초 자동 갱신', value=st.session_state['prefs']['quotes_auto'], key='auto_all',
                    help='모든 화면(시세·트레이딩 현황·실계좌·전략 구조)의 자동 갱신을 한 번에 켜고 끕니다. 간격과 처음 켜짐 여부는 화면 설정에서 정합니다.')
    import api_status_view
    if api_status_view.credentials().get('LINER_API_KEY'):
        if controls.button('AI 검색', icon=':material/search:', key='quick_liner',
                           help='종목·시장 이슈를 찾고 투자 용어나 서비스 사용법을 질문하는 창을 엽니다. 창 안에서 사용 가이드를 볼 수 있습니다.'):
            st.session_state.pop('quick_liner_suggestions', None)
            st.session_state.pop('ai_float_open', None)
            st.session_state['quick_liner_open'] = True
        if st.session_state.get('quick_liner_open'):
            api_status_view.open_liner_search()
        import ai_float
        ai_float.render()  # 'AI에게 묻기', bottom right of every screen
    dialogs.keep_open()
    with controls.popover('화면 설정', icon=':material/settings:'):
        prefs_form()
    st.caption('모의·실계좌 구분 · 한국시간 · 자동매매는 별도 실행기가 하며 이 화면은 실주문을 내지 않습니다')
    import disclaimer
    disclaimer.top_line(st)  # investment disclaimer on every tab (user 2026-11-19)
    try:
        configs = load_agent_config().mcp_servers
        read = MCPServerAdapter('nhplug_readonly', configs['nhplug_readonly'])
        paper = MCPServerAdapter('nhplug_paper', configs['nhplug_paper'])
        account = configured_account(configs['nhplug_paper'], 'paper')
    except Exception:
        st.error('NH 연결 설정을 불러올 수 없습니다. Vibe의 MCP 설정을 확인하세요.')
        return
    st.session_state['_read_adapter'] = read
    from accounts import load as load_accounts
    from watchlist import account_watchlist
    records = load_accounts()
    with st.sidebar:
        import overview
        import sidebar
        states = display_process_states()
        if '확인 중' in states.values():
            st.caption('실행기 상태 확인 중')
        import engine_ops
        alerts = overview.alerts(get_runtime_root() / 'nhplug', states, overview.latest_follow(get_runtime_root() / 'nhplug'),
                                 datetime.now(KST).strftime('%Y-%m-%d'))
        sidebar.render(PREFS.TABS, states, records, alerts,
                       engine_ops.LABELS[engine_ops.mode(get_runtime_root() / 'nhplug')])
        @st.fragment(run_every=f'{refresh_s()}s')
        def sidebar_positions():
            sidebar.positions_panel(get_runtime_root() / 'nhplug', stock_names())

        sidebar_positions()
        if st.button('가이드 센터', icon=':material/menu_book:', width='stretch',
                     help='모든 화면의 설명과 매수·매도 전략, 안전장치, 데이터 흐름을 도식과 함께 봅니다.'):
            guide_dialog('etf')
        st.divider()
        picked = st.selectbox('계좌', list(records), key='trading_account',
                              format_func=lambda k: f"{records[k].get('label', k)} {records[k].get('masked', '')}".strip(),
                              help='좌측 관심종목과 "트레이딩 현황" 탭이 이 계좌를 따릅니다.')
        st.subheader('전체 시세')
        @st.fragment(run_every=live_every())
        def market():
            fresh = st.button('시세 새로고침')
            # 7 ETFs, then the selected account's watchlist (·Jev selected, ·추가 added by hand)
            targets = {**{name.removeprefix('KODEX '): code for name, code in SYMBOLS.items()},
                       **{f'{name} ·{source}': code for code, name, source in account_watchlist(picked)}}
            rows, failed = [], []
            # reused for one refresh interval (other tabs and sessions rerun in between); the button asks again
            quotes = display_prices(read, list(dict.fromkeys(targets.values())), 1 if fresh else refresh_s())
            for label, code in targets.items():
                try:
                    row = quotes[code][0]
                    if row is None:
                        raise ValueError('quote failed')
                    price, previous = row.get('stck_prpr'), row.get('stck_prdy_clpr')
                    if row.get('iem_cd') != code or type(price) not in (int, float) or price <= 0:
                        raise ValueError('invalid quote')
                    change = (price / previous - 1) * 100 if type(previous) in (int, float) and previous > 0 else None
                    mark = '' if change is None else '▲' if change > 0 else '▼' if change < 0 else '■'
                    rows.append({'종목': label, '현재가': f'{int(price):,}', '등락': f'{mark}{abs(change):.1f}%' if change is not None else '—'})
                except Exception:
                    failed.append(label)
            tone = lambda v: 'color:#ef4444' if str(v).startswith('▲') else 'color:#3b82f6' if str(v).startswith('▼') else ''
            if rows:
                st.dataframe(pd.DataFrame(rows).style.map(tone, subset=['등락']), hide_index=True, height=38 + 35 * len(rows))
            sources = ' · '.join(dict.fromkeys({'kiwoom': '키움', 'nh': 'NH 나무 PLUG'}[s] for _, s in quotes.values())) or 'NH 나무 PLUG'
            st.caption(f"조회 {datetime.now(KST):%H:%M:%S} · {sources} · 전일 종가 대비 · {refresh_s()}초 REST 조회(최대 {refresh_s()}초 묵은 값)이며 체결 스트림이 아닙니다")
            if failed:
                st.warning('시세를 불러오지 못한 종목: ' + ', '.join(failed) + '. 이전 값을 현재 시세로 사용하지 마세요.')
        quotes_slot = st.container()  # filled at the end of main(): the tabs no longer wait for a dozen quote calls
        with st.expander(f"관심종목 편집 · {records[picked].get('label', picked)}"):
            watchlist_editor(picked)
    linked = PREFS.place(st.query_params.pop('tab', None) or '', st.query_params.pop('view', None))  # ?tab=&view= deep links, old names included
    if linked:
        st.session_state['main_tab'] = linked[0]
        if linked[1]:
            st.session_state[linked[1]] = linked[2]
    summary_tab, market_tab, strategy_tab, etf_tab, journal_tab, compare_tab, accounts_tab, longterm_tab, live_tab, ops_tab = st.tabs(
        PREFS.TABS, key='main_tab', default=st.session_state['prefs']['start_tab'], on_change='rerun')  # keyed: a save or any rerun keeps the tab the user is on
    if summary_tab.open:  # only the tab on screen runs (on_change="rerun"); the others cost nothing
        with summary_tab:
            help_row('summary')
            import overview
            overview.render(get_runtime_root() / 'nhplug', records, states,
                            stock_names(), refresh_balances=lambda: record_live_balances(read, records))
    if journal_tab.open:
        with journal_tab:
            import journal_view
            journal_view.render()
    if longterm_tab.open:
        with longterm_tab:
            help_row('longterm')
            import vibe_longterm_view
            vibe_longterm_view.render(get_runtime_root() / 'nhplug' / 'vibe-longterm', chart=tradingview_chart)
    if market_tab.open:
        with market_tab:
            help_row('market')
            market_panel()
    if strategy_tab.open:
        with strategy_tab:
            help_row('strategy')
            strategy_panel()
    if accounts_tab.open:
        with accounts_tab:
            help_row('accounts')
            accounts_panel(read)
    if compare_tab.open:
        with compare_tab:
            help_row('compare')
            compare_panel()
    root = get_runtime_root()  # also used by the live tab below
    if ops_tab.open:  # 2026-11-19: the four admin screens under one tab
        if alerts:  # what needs a look shows before picking a screen (the same list as the sidebar)
            ops_tab.warning(f'주의 항목 {len(alerts)}건 · ' + ' · '.join(alerts[:3]) + (' …' if len(alerts) > 3 else ''), icon=':material/warning:')
        ops_view = ops_tab.segmented_control('운영 화면', PREFS.OPS_VIEWS, default=PREFS.OPS_VIEWS[0], key='ops_view',
                                             label_visibility='collapsed') or PREFS.OPS_VIEWS[0]
        if ops_view == '운영 상태':
            with ops_tab:
                help_row('ops')
                operations_panel(root)
        elif ops_view == '자동 작업':  # 2026-11-19: launchd services, Claude scheduled tasks and Codex automations together
            with ops_tab:
                help_row('schedule')
                import schedule_view
                schedule_view.render(root)
        elif ops_view == 'API 연동':
            with ops_tab:
                help_row('api')
                import api_status_view
                api_status_view.render(root)
        elif ops_view == '스킬':
            with ops_tab:
                import skills_view
                st.subheader('스킬')
                skills_view.render(root)
        elif ops_view == '리포트 발행':
            with ops_tab:
                import report_pages_view
                report_pages_view.render(root)
        elif ops_view == '백업·이식':  # 2026-11-17: backup and restore of the runtime data (state_sync)
            with ops_tab:
                import backup_view
                backup_view.render(root)
        else:
            ai_records(ops_tab, records, read)
    if etf_tab.open:
        with etf_tab:
            from accounts import resolve as resolve_accounts
            help_row('etf' if picked == 'paper-shared' else 'live_desk')
            account_switcher(records, picked)
            if picked != 'paper-shared':
                try:
                    linked = call(read, 'list_accounts', {}, cache_s=ACCOUNT_LIST_CACHE_S).get('Output_0') or []
                    number = resolve_accounts(records, linked).get(picked)
                except Exception:
                    number = None
                try:
                    configured = configured_account(configs['nhplug_readonly'], 'readonly')
                except ValueError:
                    configured = None
                shared_events = []
                try:
                    shared_events = read_events((get_runtime_root() / 'nhplug' / 'shared-etf-paper' / 'events.jsonl'), 20000)
                except (OSError, ValueError):
                    st.warning('엔진 판단 기록을 읽지 못해 가상 실행 결과가 비어 보일 수 있습니다.')
                live_account_view(read, picked, records[picked], number, configured, shared_events)
            else:
                heading, toggle = st.columns([3, 1], vertical_alignment='center')
                heading.subheader('모의 트레이딩')
                etf_auto = auto_on()  # the header's one switch (user 2026-11-17)
                toggle.caption(f'자동 갱신 켜짐 · {refresh_s()}초 (장중 09:00~15:30)' if etf_auto else '자동 갱신 꺼짐 · 상단에서 켭니다',
                               help=f'{refresh_s()}초 간격 REST 조회입니다. 실시간 틱이나 체결 스트림이 아니며 지연·실패가 있을 수 있습니다.')
                first_trading_render = True

                @st.fragment(run_every=live_every())
                def etf_status():
                    nonlocal first_trading_render
                    service_states = states if first_trading_render else display_process_states()
                    first_trading_render = False
                    runner_state = service_states.get('공유 모의 ETF 실행기', '확인 불가')
                    learning_state = service_states.get('공유 모의 ETF 데이터 관찰기', '확인 불가')
                    line, button = st.columns([4, 1], vertical_alignment='center')
                    mark = ':green[●]' if runner_state == '실행 중' else ':red[●]'
                    line.markdown(f'{mark} 실행기 {runner_state} · 학습 관찰기 {learning_state} · 조회 {datetime.now(KST):%H:%M:%S}')
                    refresh = button.button('ETF 현황 새로고침', key='etf_status_refresh', width='stretch')
                    if runner_state == '확인 중':
                        st.caption('실행기 상태 확인 중입니다.')
                    elif runner_state == '확인 불가':
                        st.warning('모의 실행기 상태를 확인할 수 없어 자동 주문과 손절·익절 감시를 보장할 수 없습니다.')
                    elif runner_state != '실행 중':
                        st.warning('모의 실행기가 작동하지 않아 자동 주문과 손절·익절 감시를 보장할 수 없습니다.')
                    identity = hashlib.sha256(str(account).encode()).hexdigest()
                    if st.session_state.get('etf_broker_identity') != identity:
                        st.session_state.pop('etf_broker_snapshot', None)
                        st.session_state.pop('etf_broker_error', None)
                        st.session_state.pop('etf_broker_attempt', None)
                        st.session_state.pop('etf_broker_future', None)
                        st.session_state.etf_broker_identity = identity

                    def fetch_snapshot(fresh):
                        now = datetime.now(KST)
                        try:
                            snapshot = shared_snapshot('paper', account, lambda: shared_etf_broker_snapshot(read, paper, account, now), fresh)
                        except Exception:
                            snapshot = None
                        return snapshot, time.monotonic()

                    def apply_snapshot(value):
                        snapshot, attempted_at = value
                        if snapshot is None:
                            st.session_state.pop('etf_broker_snapshot', None)
                        else:
                            st.session_state.etf_broker_snapshot = snapshot
                        st.session_state.etf_broker_error = snapshot is None
                        st.session_state.etf_broker_attempt = attempted_at

                    request = st.session_state.get('etf_broker_future')
                    if request is not None and request.done():
                        apply_snapshot(request.result())
                        st.session_state.pop('etf_broker_future', None)
                        request = None
                    if refresh and request is None:
                        apply_snapshot(fetch_snapshot(True))

                    kept = st.session_state.get('etf_broker_snapshot')
                    last_attempt = st.session_state.get('etf_broker_attempt')
                    due = snapshot_due(kept, datetime.now(KST))
                    can_retry = last_attempt is None or time.monotonic() - last_attempt >= refresh_s()
                    pending_refresh = request is not None or (
                        not refresh and can_retry and (etf_auto or (due and not st.session_state.get('etf_broker_error')))
                    )
                    pending_first = kept is None and pending_refresh
                    if pending_refresh:
                        st.caption('증권사 잔고·주문을 불러오는 중입니다. 실행기 원장부터 표시합니다.')
                        if kept is not None:
                            st.caption(f"기존 증권사 잔고 조회 시각 {kept['time']:%m/%d %H:%M:%S} KST")
                        def start_broker_read():
                            pending = st.session_state.get('etf_broker_future')
                            if pending is None:
                                key = ('paper-snapshot', identity, datetime.now(KST).date())
                                pending = start_dashboard_read(key, lambda: fetch_snapshot(False))
                                st.session_state.etf_broker_future = pending
                            return pending
                        rerun_when_read_completes(start_broker_read)
                    if st.session_state.get('etf_broker_error'):
                        st.error('증권사 잔고 또는 전체 주문을 확인하지 못했습니다. 빈 계좌로 간주하지 마세요.')
                    snapshot = st.session_state.get('etf_broker_snapshot')
                    evidence = get_runtime_root() / 'nhplug' / 'shared-etf-paper'
                    try:
                        recent = read_events((evidence / 'events.jsonl'), 5000)
                    except (OSError, ValueError):
                        recent = []  # paper_trade_chart below reports the same failure
                    try:
                        with ro_connect(evidence / "orders.sqlite3") as db:
                            recent_fills, _ = paper_order_records(db)
                            books = realized_by_symbol(db, datetime.now(KST).strftime('%Y-%m-%d'))
                    except sqlite3.Error:
                        recent_fills, books = None, None
                    if not pending_first:
                        portfolio_overview(snapshot, recent, recent_fills, datetime.now(KST), books,
                                           sell=lambda: paper_quick_sell('paper_top'))
                    import trading_positions
                    position_data = trading_positions.snapshot(get_runtime_root() / 'nhplug')
                    position_names = stock_names()
                    st.markdown('##### Jev 보유·오늘 매도')
                    for engine, label in (('jev', 'Jev'),):  # 장기투자는 "장기투자" 탭 · 사이드바에서 봅니다
                        info = position_data.get(engine) or {}
                        with st.container(border=True):
                            st.markdown(f'**{label}**')
                            if info.get('status') != 'ok':
                                st.warning('원장을 확인할 수 없습니다.')
                                continue
                            holding_count, sell_count = len(info.get('holdings') or []), len(info.get('sells') or [])
                            st.caption(f'보유 {holding_count}종목 · 오늘 매도 체결 {sell_count}건 · 수수료·세금 미반영')
                            with st.container(horizontal=True):
                                for group in trading_positions.grouped_symbols(info):
                                    code = group['symbol']
                                    import sidebar
                                    if st.button(trading_positions.group_label(group, position_names), key=f'desk_{engine}_{code}',
                                                 on_click=sidebar.open_trade_history, args=(engine, code),
                                                 help='이 엔진의 종목별 확인 체결 내역을 봅니다.'):
                                        st.rerun()
                            if info.get('unknown_sells'):
                                st.caption('사유를 확인할 수 없는 매도 체결이 있습니다.')

                    with st.expander('매매 기준 (지금 적용 중)', icon=':material/rule:'):
                        rules_card(*strategy_settings())
                    latest_views = comparison_data(recent)
                    paper_trade_chart(read, snapshot, refresh, etf_auto, latest_views[-1].get('jev') if latest_views else None)
                    movers_tab, blocks_tab, reasons, history, broker_tab, sell_tab, settings_tab = st.tabs(
                        ['시장·관심종목', '매수 차단 사유', 'Jev 판단 근거', '판단·주문 기록', '증권사 잔고·주문 원본', '즉시 매도', '전략·AI 설정'],
                        key='etf_desk_tab', on_change='rerun')  # only the open tab runs
                    if blocks_tab.open:
                        with blocks_tab:
                            block_reason_panel(recent, datetime.now(KST))
                    if sell_tab.open:
                        with sell_tab:
                            paper_quick_sell('paper_quick')
                    if movers_tab.open:
                        with movers_tab:
                            market_watch_panel(watchlist_snapshot(), movers_snapshot(), datetime.now(KST))
                            listings = st.expander('신규 상장 종목', key='paper_new_listings', on_change='rerun')
                            if listings.open:
                                with listings:
                                    new_listing_panel(datetime.now(KST))
                    if reasons.open:
                        with reasons:
                            jev_evidence_panel(recent, read, refresh or etf_auto)
                    if broker_tab.open:
                        with broker_tab:
                            if st.session_state.get('etf_broker_error'):
                                st.caption('증권사 조회 실패 — 위 오류를 확인하세요.')
                            elif snapshot:
                                age = (datetime.now(KST) - snapshot['time']).total_seconds()
                                st.caption(f"증권사 조회: {snapshot['time']:%Y-%m-%d %H:%M:%S} · 다시 조회하기 전까지 과거 상태")
                                if age < 0 or age > 120:
                                    st.warning('증권사 조회 결과가 2분보다 오래됐거나 시각을 확인할 수 없습니다.')
                                holdings = snapshot['holdings']
                                etfs = [row for row in holdings if row.get('종목코드') in ETF_CODES.values()]
                                # Stocks the runner bought itself are expected; anything else needs reconciliation.
                                other = [row for row in holdings if row.get('종목코드') not in ETF_CODES.values()
                                         and not ((books or {}).get(row.get('종목코드')) or {}).get('qty')]
                                if snapshot['aggregate'] is None:
                                    st.caption('가격 또는 증권사 합계가 없어 전체 평가금액·손익·수익률을 표시하지 않습니다.')
                                st.write('7 ETF 보유(증권사 응답)')
                                try:
                                    st.dataframe(etf_portfolio_rows(etfs), hide_index=True)
                                    if not etfs:
                                        st.caption('조회된 7 ETF 보유 종목이 없습니다.')
                                except ValueError:
                                    st.error('동일 ETF의 보유 내역이 여러 건이어서 합계를 표시하지 않습니다. 증권사에서 대조하세요.')
                                    st.dataframe(etfs, hide_index=True)
                                if other:
                                    st.warning('지정 ETF 외 보유 종목이 있어 계좌 대조가 필요합니다.')
                                    st.dataframe(other, hide_index=True)
                                st.caption('평가손익과 수익률은 증권사 응답 그대로이며 비용 포함 여부는 확인되지 않았습니다.')
                                st.write('당일 주문·체결·취소')
                                orders_view(snapshot, books, datetime.now(KST).strftime('%Y-%m-%d'))
                            else:
                                st.info('ETF 현황 새로고침을 누르면 증권사 잔고와 주문을 조회합니다.')

                    if history.open:
                        with history:
                            st.caption('Jev의 종목 후보와 보유분 HOLD·SELL 의견은 별도입니다. Decision·Jev 의견은 체결이 아닙니다.')
                            local_root = get_runtime_root() / 'nhplug' / 'shared-etf-paper'
                            events_path = local_root / 'events.jsonl'
                            journal = local_root / 'orders.sqlite3'
                            try:
                                events = read_events(events_path, 5000)
                                decisions = comparison_data(events)
                                risk_warning = risk_exit_warning(events)
                                if risk_warning:
                                    st.warning(risk_warning)
                                try:
                                    with ro_connect(journal) as db:
                                        _, local_orders = paper_order_records(db)
                                except sqlite3.Error:
                                    local_orders = None
                                if local_orders is None:
                                    st.warning('로컬 주문 원장을 확인하지 못해 판단과 주문을 연결할 수 없습니다.')
                                if decisions:
                                    latest = decisions[-1]['time']
                                    st.caption(f"최근 판단 기록: {latest:%Y-%m-%d %H:%M:%S} · 로컬 기록, 증권사 체결과 별도")
                                    age = (datetime.now(KST) - latest).total_seconds()
                                    if age < 0 or age > 120:
                                        st.warning('최근 AI 판단 기록이 2분보다 오래됐거나 시각을 확인할 수 없습니다.')
                                    rows = [{'판단 시각': row['time'].strftime('%Y-%m-%d %H:%M:%S'),
                                             'Decision': row.get('decision') or '—', 'Jev 종목 후보': row.get('jev') or '—',
                                             'Jev 신뢰도': (f"{row['jev_confidence']:.2f}" if type(row.get('jev_confidence')) in (int, float) else '—'),
                                             'Jev 보유 의견': (f"{row['jev_holding']} {row['jev_holding_confidence']:.2f}"
                                                             if row.get('jev_holding') and type(row.get('jev_holding_confidence')) in (int, float)
                                                             else row.get('jev_holding') or '—'),
                                             '판단 사유': entry_reason_label(row.get('reason')),
                                             '주문 제한 사유': execution_reason_label(row.get('execution_reason')),
                                             '로컬 주문 상태': order_outcome(row, local_orders)} for row in reversed(decisions[-10:])]
                                    confidence_chart(decisions, strategy_settings()[0])
                                    st.dataframe(rows, hide_index=True)
                                else:
                                    st.caption('로컬 AI 판단 기록이 없습니다.')
                                news_errors = [event_time(item.get('time')) for item in events
                                               if isinstance(item, dict) and item.get('event') == 'v2_news_error']
                                news_errors = [stamp for stamp in news_errors if stamp is not None]
                                if news_errors and (not decisions or max(news_errors) > decisions[-1]['time']):
                                    st.warning(f"최근 뉴스 조회 오류: {max(news_errors):%Y-%m-%d %H:%M:%S}. 후속 판단이 확인되기 전까지 현재 의견으로 간주하지 마세요.")
                            except FileNotFoundError:
                                st.info('공유 ETF의 로컬 판단 기록이 아직 없습니다.')
                            except (OSError, ValueError, TypeError):
                                st.error('로컬 AI 판단 기록을 확인할 수 없습니다.')
                    if settings_tab.open:
                        with settings_tab:
                            strategy_form(recent)
                            st.markdown(
                                '- **매수 크기:** 분할 1회 금액·1회 최대 수량·종목당 최대 금액은 계좌·자동매매에서 설정\n'
                                '- **제한:** 롱↔인버스 동시 보유 안 함 · 하루 최대 1,000회 · 보유 수량은 계좌에서 설정 · 60초 주기 · 시각은 제한 규칙 탭\n'
                                f'- **공유 계좌 보호:** {guard_text()} 중단')
                            st.caption('기존 종합 모의계좌를 공유합니다. 현재 실행 중인 전략 버전은 실행기 기동 후 별도로 확인해야 하며, 목록에 표시되는 것만으로 자동주문 대상임을 뜻하지 않습니다.')
                            st.caption('설정 요청은 안전한 주문 대조 후 주기 경계에서 적용됩니다. 적용 전 현재 주기의 주문은 기존 설정을 사용할 수 있습니다.')
                            st.caption('두 모델을 모두 끄면 AI 주문을 보류하며 실행기 작동 중 2% 보호 감시는 유지합니다.')
                            try:
                                modes_root = get_runtime_root() / 'nhplug/shared-etf-paper'
                                active_modes = read_modes(modes_root)
                                pending_modes = read_pending_modes(modes_root)
                                status_text = lambda modes: f"Jev {'켜짐' if modes['jev'] else '꺼짐'} · Decision {'켜짐' if modes['decision'] else '꺼짐'}"
                                st.write('설정 파일 활성값: ' + status_text(active_modes))
                                st.caption('실행 중인 프로세스의 설정 반영 여부는 실행기 상태와 기록에서 확인하세요.')
                                if pending_modes is not None:
                                    st.warning('다음 안전 주기 적용 대기: ' + status_text(pending_modes))
                                current = pending_modes or active_modes
                                with st.form('paper_ai_settings'):
                                    jev_enabled = st.checkbox('Jev 사용', value=current['jev'])
                                    decision_enabled = st.checkbox('Decision 사용', value=current['decision'])
                                    request_modes = st.form_submit_button('모의 AI 설정 요청')
                                if request_modes:
                                    write_modes(modes_root, jev=jev_enabled, decision=decision_enabled)
                                    st.success('설정을 요청했습니다. 다음 안전 주기의 적용 여부를 확인하세요.')
                            except (OSError, ValueError, TypeError, KeyError):
                                st.error('모의 AI 설정 미확인. 새 실행기는 AI 주문을 보류합니다. 현재 실행 중인 프로세스의 설정 반영 여부를 확인하세요.')
                etf_status()
    if live_tab.open:
        with live_tab:
            help_row('live')
            live_tab_panel(root, read, configs, records)
    with quotes_slot:
        market()


def selected_trade_history(root, engine):
    """Show every confirmed fill for the symbol selected from the engine position panel."""
    selected = st.session_state.get('trade_history')
    if not isinstance(selected, dict) or selected.get('engine') != engine:
        return
    code = selected.get('code')
    if not isinstance(code, str) or not re.fullmatch(r'[0-9][0-9A-Z]{5}', code):
        return
    names = stock_names()
    st.markdown(f"##### {names.get(code, code)} ({code}) 거래 내역")
    st.caption('선택한 엔진 원장의 확인 체결 전체 · 시각은 로컬 확인 시각(한국시간)입니다.')
    import sidebar
    st.button('이 종목 차트 보기', key=f'history_chart_{engine}_{code}',
              on_click=sidebar.open_trading_chart, args=(engine, code), icon=':material/candlestick_chart:')
    def confirmed_time(stamp):
        try:
            at = event_time(stamp)
            return at.strftime('%Y-%m-%d %H:%M:%S') if at else '시각 미확인'
        except OverflowError:
            return '시각 미확인'
    try:
        with ro_connect(root / 'orders.sqlite3') as db:
            rows = db.execute('SELECT time,side,quantity,price FROM fills WHERE symbol=? ORDER BY rowid DESC', (code,)).fetchall()
        if rows:
            st.dataframe([{'시각': confirmed_time(stamp), '구분': {'BUY': '매수', 'SELL': '매도'}.get(side, side),
                           '수량': qty, '체결가': price} for stamp, side, qty, price in rows], hide_index=True, width='stretch')
        else:
            st.info('이 종목의 확인 체결 기록이 없습니다.')
    except sqlite3.Error:
        st.warning('이 엔진의 체결 원장을 읽지 못했습니다.')


def ai_records(tab, records, read):
    """운영 → AI·실행 기록: the engines' and the live executors' records (the frozen vibe intraday book left 11-19)."""
    with tab:
        help_row('ai')
        record_source = st.segmented_control(
            '기록', ['overview', 'jev', 'longterm', *[k for k, r in records.items() if r.get('kind') == 'live']], default='overview',
            key='record_source',
            format_func=lambda k: {'overview': '종합', 'jev': 'Jev 엔진', 'longterm': '장기투자 결정'}.get(k) or f"{records[k].get('label', k)} 실행",
            help='Jev 엔진은 공유 모의계좌 원장의 판단·체결 기록을, 실계좌를 고르면 그 계좌 실행기 기록을 봅니다. 종합은 모두를 한 날짜로 모읍니다.') or 'overview'
    if record_source == 'overview':
        with tab:
            import overview
            overview.render_records(get_runtime_root() / 'nhplug', records, stock_names(), strategy_settings()[0], entry_reason_label)
    elif record_source == 'longterm':
        with tab:
            import vibe_longterm_view
            st.caption('장기투자 엔진의 국면·스토리·계획·주문 기록입니다. 전체 화면은 "장기투자" 탭에 있습니다.')
            vibe_longterm_view.record(vibe_longterm_view.load(get_runtime_root() / 'nhplug' / 'vibe-longterm')[1])
    elif record_source in records:
        with tab:
            live_record_panel(record_source, records[record_source])
    else:
        ai_record_tab(tab, record_source, read)


def ai_record_tab(ai_tab, engine, read):
    from engine_roles import ledger_root
    with ai_tab:
        title, reload = st.columns([4, 1], vertical_alignment='center')
        title.subheader(f"{'Jev' if engine == 'jev' else 'vibe-trading'} AI 기록")
        reloaded = reload.button('실험 기록 새로고침', width='stretch', icon=':material/refresh:',
                                 help='실행기 기록(events.jsonl)과 체결 원장을 다시 읽습니다.')
        source = '모의계좌 ETF'
        folder = ledger_root(get_runtime_root() / 'nhplug', engine).name
        path = get_runtime_root() / 'nhplug' / folder / 'events.jsonl'
        selected_trade_history(path.parent, engine)
        if reloaded or st.session_state.get('ai_loaded_source') != folder:
            try:
                events = read_events(path, 5000)
                st.session_state.ai_raw_events = events
                allowed = {'started', 'stopped', 'decision', 'market_context', 'jev_shadow', 'jev_error', 'jev_holding_decision', 'submitted', 'sell_submitted', 'order_status', 'roundtrip_verified', 'order_limit', 'error', 'v2_started', 'v2_submitted', 'v2_order_status', 'v2_stopped', 'v2_error', 'v2_holding_decision', 'v2_holding_decision_error', 'v2_entry_gate', 'v2_execution_gate', 'v2_risk_exit', 'v2_risk_exit_error', 'v2_news_error', 'v2_regime'}
                st.session_state.ai_events = [experiment_row(event) for event in events
                                              if isinstance(event, dict) and event.get('event') in allowed][-100:][::-1]
                journal = path.parent / 'orders.sqlite3'
                try:
                    with ro_connect(journal) as db:
                        st.session_state.ai_fills, st.session_state.ai_orders = paper_order_records(db)
                except sqlite3.Error:
                    st.session_state.ai_fills = None
                    st.session_state.ai_orders = None
                st.session_state.ai_checked = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')
                st.session_state.ai_loaded_source = folder
            except (OSError, ValueError, sqlite3.Error):
                st.session_state.pop('ai_events', None)
                st.session_state.pop('ai_raw_events', None)
                st.session_state.pop('ai_fills', None)
                st.session_state.pop('ai_orders', None)
                st.session_state.ai_loaded_source = folder
                if not path.exists():
                    st.info('선택한 실험의 기록이 아직 없습니다.')
                else:
                    st.error('실험 기록을 읽을 수 없습니다. 실행기 상태와 기록을 확인하세요.')
        if 'ai_events' in st.session_state:
            st.caption(f"기록 확인: {st.session_state.ai_checked} · 원본 최신 5,000개 중 이벤트 표는 100개 · 실행 중 여부는 확인하지 않습니다.")
            raw = st.session_state.ai_raw_events
            risk_warning = risk_exit_warning(raw)
            if risk_warning:
                st.warning(risk_warning)
            comparisons = comparison_data(raw)
            dates = sorted({row['time'].date() for row in comparisons}, reverse=True)
            if dates:
                chart_day = st.selectbox('기록 날짜 (한국시간)', dates, key='ai_chart_day', help='위 요약 카드와 아래 차트·표가 모두 이 날짜를 따릅니다.')
                day_kpis(day_summary(raw, chart_day, st.session_state.get('ai_fills')), strategy_settings()[0]['entry_confidence'])
            st.write('전략 국면 · 검증 중')
            st.caption('연구 기록이며 주문 실행이나 수익성을 뜻하지 않습니다. 표시 시각 이후의 상태는 확인되지 않았습니다.')
            regime = latest_regime(raw)
            if regime is None:
                st.caption('전략 국면 기록 없음 · 검증 중')
            else:
                st.dataframe([regime], hide_index=True)
            st.subheader('TradingView 판단·체결 차트')
            st.caption('선택한 실험에 저장된 NH 완료 1분봉입니다. 실시간 시세가 아니며 브라우저가 차트 라이브러리를 외부 CDN에서 불러옵니다.')
            st.caption('파랑 Decision · 보라 Jev 후보 · 초록 매수 체결 확인 · 빨강 매도 체결 확인. 해당 분봉이 없으면 표에만 표시합니다. 체결 시각은 증권사 체결 시각이 아닌 로컬 확인 시각입니다.')
            if dates:
                ai_charts = dict(ETF_CHARTS)
                names = stock_names()
                for code in sorted({k for e in raw if isinstance(e, dict) and isinstance(e.get('market_bars'), dict)
                                    for k in e['market_bars'] if k.isdigit()}):
                    ai_charts[f'{names.get(code, code)} ({code})'] = (code, code)
                chart_name = st.selectbox('차트 종목', list(ai_charts), key='ai_chart_symbol')
                group, chart_symbol = ai_charts[chart_name]
                ai_timeframe, ai_periods, ai_volume = chart_controls('ai')
                selected = [row for row in comparisons if row['time'].date() == chart_day]
                if ai_timeframe == '1분':
                    bars = chart_bars(raw, group, chart_day)
                else:  # other units come from NH (latest data, not the selected day); AI opinions are per 1-minute bar
                    cached = st.session_state.get('ai_period_bars')
                    if not cached or cached[0] != (chart_symbol, ai_timeframe) or (datetime.now(KST) - cached[1]).total_seconds() > 60:
                        try:
                            cached = ((chart_symbol, ai_timeframe), datetime.now(KST), period_bars(read, chart_symbol, ai_timeframe))
                        except Exception:
                            cached = ((chart_symbol, ai_timeframe), datetime.now(KST), [])
                        st.session_state['ai_period_bars'] = cached
                    bars = cached[2]
                    st.caption(f'{ai_timeframe}봉은 NH 최신 시세이며 선택한 기록 날짜와 무관합니다. AI 의견 표식은 1분봉에서만 보이고, 여기서는 체결 확인만 표시합니다.')
                if bars and ai_timeframe != '1분':
                    tradingview_chart(bars, containing_markers(st.session_state.get('ai_fills') or [], bars, chart_symbol), None,
                                      averages=moving_averages(bars, ai_periods), volume=ai_volume,
                                      intraday=TIMEFRAMES[ai_timeframe][1] is not None, visible=TIMEFRAMES[ai_timeframe][3])
                elif bars:
                    from engine_roles import main_engine
                    basis = (holding_chart_basis(st.session_state.get('etf_broker_snapshot'), st.session_state.get('ai_fills'), chart_symbol, datetime.now(KST))
                             if engine == main_engine(get_runtime_root() / 'nhplug') else None)
                    if basis and any(bar['time'] >= basis['start'] for bar in bars):
                        basis = with_levels(basis, chart_symbol, engine)
                        stop_text = f"{basis['stop_label']} {basis['stop']:,.2f}원" if basis['stop'] is not None else '손절 기준 미확인'
                        st.caption(f"현재 보유분 기준 · 매입가 {basis['price']:,.0f}원 · {stop_text} · {targets_text(basis['half'], basis['all'])}")
                        if '시황 재사용/오래됨' in basis['stop_label']:
                            st.warning('손절 기준 계산에 오래된 시황이 재사용됐습니다. 최신 시황 확인이 필요합니다.')
                        st.caption('매입가 위는 수익 구간, 아래는 손실 구간입니다. 손절선은 시황·계획 기준 참고값이며 본전·추적 청산은 별도입니다. 수수료·세금은 반영하지 않습니다.')
                    else:
                        basis = None
                        st.caption('최신 잔고와 보유 시작을 확인할 수 없어 매입가·손익 구간을 표시하지 않습니다.')
                    tradingview_chart(bars, chart_markers(selected, st.session_state.get('ai_fills') or [], bars, group, chart_symbol), basis,
                                      averages=moving_averages(bars, ai_periods), volume=ai_volume, visible=TIMEFRAMES['1분'][3])
                    last_bar = datetime.fromtimestamp(bars[-1]['time'], KST)
                    st.caption(f'마지막 기록 분봉: {last_bar:%Y-%m-%d %H:%M} · {len(bars)}개')
                else:
                    st.info('선택한 날짜·종목의 저장된 분봉이 없습니다. 과거 실행에서 수집하지 않은 종목은 표시되지 않습니다.')
                st.markdown('##### Jev 신뢰도 추이')
                confidence_chart(selected, strategy_settings()[0])
                st.markdown(f'##### 실행기 활동 ({chart_day:%m/%d}, 10분 단위)')
                activity_charts(*activity_frames(raw, chart_day))
                st.write('Jev·Decision 판단 비교')
                st.caption('Jev 종목 후보와 보유분 HOLD·SELL 의견은 별도입니다. Decision의 SELL도 보유 종목에 대한 의견이며, 모델 신뢰도는 수익 확률이 아닙니다. 판단과 주문은 기록된 동일 식별자로만 연결합니다.')
                compare_rows = [{'판단 시각': row['time'].strftime('%Y-%m-%d %H:%M:%S'),
                    '상황': {'decision': '진입 검토', 'market_entry': 'Jev 진입 검토', 'v2_holding_decision': '보유 검토', 'market_holding': 'Jev 보유 검토', 'jev_only': 'Jev 단독 기록', 'v2_risk_exit': '보호 청산 판단'}[row['kind']],
                    '보유 종목': row['symbol'] or '—', 'Decision': row['decision'] or '—',
                    'Decision 신뢰도': row['decision_confidence'], 'Jev 후보': row['jev'] or '—',
                    'Jev 후보 신뢰도': row['jev_confidence'], 'Jev 보유 의견': row.get('jev_holding') or '—',
                    'Jev 보유 의견 신뢰도': row.get('jev_holding_confidence'),
                    '진입 조건': '허용' if row['permitted'] is True else '불허' if row['permitted'] is False else '확인 불가',
                    '판단 출처': row['source'] or '—', '사유': entry_reason_label(row.get('reason')),
                    '주문 제한 사유': execution_reason_label(row.get('execution_reason')),
                    '연결된 주문·체결': order_outcome(row, st.session_state.get('ai_orders'))} for row in reversed(selected)]
                if compare_rows:
                    with st.expander(f'판단 비교 표 ({len(compare_rows)}건)'):
                        st.dataframe(compare_rows, hide_index=True)
                else:
                    st.caption('비교할 판단 기록이 없습니다.')
            else:
                st.info('차트에 사용할 판단 기록이 없습니다.')
            if st.session_state.ai_events:
                with st.expander(f'실험 이벤트 표 (최근 {len(st.session_state.ai_events)}건)'):
                    st.dataframe(st.session_state.ai_events, hide_index=True)
            else:
                st.caption('표시할 실험 이벤트가 없습니다.')
            st.write(f'{source} 체결 확인 기록')
            st.caption('시각은 로컬 확인 시각이며, 증권사 실제 체결 시각과 다를 수 있습니다. 최신 100건만 표시합니다.')
            if st.session_state.get('ai_fills') is None:
                st.warning('체결 원장을 읽을 수 없어 체결 여부를 확인하지 못했습니다.')
            elif st.session_state.ai_fills:
                fills_chart(st.session_state.ai_fills, stock_names())
                with st.expander('체결 확인 표'):
                    st.dataframe(st.session_state.ai_fills, hide_index=True)
            else:
                st.caption('이 계좌의 확인된 체결 기록이 없습니다.')
        with st.expander('7 ETF 국면 관찰 · 연구 전용', icon=':material/science:'):
            regime_observation_panel(path)


if __name__ == '__main__':
    main()
