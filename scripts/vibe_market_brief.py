"""Read-only market and runner brief for the 7 paper ETFs (used by the /vibe-trading Claude skill).

Prints JSON: per-ETF quote, recent completed 1-minute closes, prior-day investor flows, news headlines,
and the shared paper runner's state (service, holdings in its journal, today's fills, latest Jev views).
Never orders; never prints account numbers or credentials.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import os_services
from etf_universe import SYMBOLS, NAMES
from sqlite_ro import ro_connect

KST = ZoneInfo('Asia/Seoul')
ROOT = Path.home() / '.vibe-trading/nhplug/shared-etf-paper'


def ok(response):
    data = response.get('data') if isinstance(response, dict) else None
    if response.get('status') != 'ok' or not isinstance(data, dict) or data.get('rsp_cd') != '00000':
        raise ValueError('NH read failed')
    return data


class _FromRecord(Exception):
    """This part came from the shared runner's record; no NH read."""


def market(read, now, recorded=None):
    """Per-ETF brief. `recorded` (the shared runner's latest market_context, already fresh) supplies the 1-minute
    closes and investor flows it read, so only the quotes are asked from NH again."""
    from nh_etf_paper_runner import valid_bars
    result = {}
    bars_seen = (recorded or {}).get('market_bars') or {}
    flows_seen = (recorded or {}).get('investor_flows') or {}
    for name, code in SYMBOLS.items():
        row = {'name': NAMES[name]}
        try:
            quote = ok(read.call_tool('get_stock_price', {'stock_code': code}))['Output_0']
            row['quote'] = {k: quote.get(k) for k in ('stck_prpr', 'askp', 'bidp', 'hoga_bsop_hour', 'acml_vol')}
            price, previous = quote.get('stck_prpr'), quote.get('stck_prdy_clpr')
            # NH's prdy_ctrt is unsigned (an inverse ETF that fell shows +); derive the signed change from closes.
            if type(price) in (int, float) and type(previous) in (int, float) and previous > 0:
                row['quote']['change_pct'] = round((price / previous - 1) * 100, 2)
        except (ValueError, KeyError, TypeError) as exc:
            row['quote_error'] = type(exc).__name__
        if bars_seen.get(name):
            row['closes_1m_newest_first'] = [(b['bsop_time'], int(b['stck_prpr'])) for b in bars_seen[name][:10]]
        if flows_seen.get(name):
            row['investor_daily_net_qty'] = flows_seen[name]
        try:
            if 'closes_1m_newest_first' in row:
                raise _FromRecord  # already from the record
            bars = valid_bars(read.call_tool('call_api', {'operationId': 'krstockQuotePeriod', 'input': {
                'market_cd': 'KRX', 'iem_cd': code, 'gubun': '5', 'xtick': '1', 'today_cls_code': '1',
                'view_main_yn': 'Y', 'array_cnt': '30'}}).get('data', {}), code, now)
            row['closes_1m_newest_first'] = [(b['bsop_time'], int(b['stck_prpr'])) for b in bars[:10]]
        except _FromRecord:
            pass
        except (ValueError, KeyError, TypeError) as exc:
            row['bars_error'] = type(exc).__name__
        try:
            if 'investor_daily_net_qty' in row:
                raise _FromRecord
            flows = ok(read.call_tool('call_api', {'operationId': 'krstockQuoteCurrentInvestor', 'input': {
                'market_cd': 'KRX', 'iem_cd': code, 'array_cnt': '5'}})).get('Output_0') or []
            # Daily net quantities; today's row is provisional intraday (often a rounded foreign estimate) until the close.
            row['investor_daily_net_qty'] = [{k: r.get(k) for k in ('bsop_date1', 'frgn_ntby_qty', 'gigwan', 'person', 'program')}
                                             for r in flows if isinstance(r, dict)]
        except _FromRecord:
            pass
        except (ValueError, KeyError, TypeError) as exc:
            row['investor_error'] = type(exc).__name__
        result[name] = row
    return result


def service_state():
    try:
        return 'running' if os_services.state('shared-etf-paper')['running'] else 'not running'
    except os_services.Unknown:
        return 'unknown'


def runner(now):
    state = {}
    state['service'] = service_state()
    state['stop_requested'] = (ROOT / 'STOP').exists()
    day = now.strftime('%Y-%m-%d')
    with ro_connect(ROOT / "orders.sqlite3") as db:
        held = {}
        for symbol, side, qty in db.execute('SELECT symbol,side,quantity FROM fills ORDER BY rowid'):
            held[symbol] = held.get(symbol, 0) + (qty if side == 'BUY' else -qty)
        state['journal_holdings'] = {k: v for k, v in held.items() if v}
        state['fills_today'] = [dict(zip(('time', 'symbol', 'side', 'qty', 'price'), r)) for r in db.execute(
            'SELECT time,symbol,side,quantity,price FROM fills WHERE time LIKE ? ORDER BY rowid', (day + '%',))]
        state['unresolved_orders'] = db.execute("SELECT COUNT(*) FROM intents WHERE status!='terminal'").fetchone()[0]
    views = []
    with (ROOT / 'events.jsonl').open(encoding='utf-8') as stream:
        from collections import deque
        for line in deque(stream, maxlen=400):
            event = json.loads(line)
            if event.get('event') == 'jev_holding_decision':
                views.append({'time': event['time'][11:19], 'holding': event.get('symbol'),
                              'view': event.get('choice'), 'confidence': event.get('confidence')})
            elif event.get('event') == 'jev_shadow' and isinstance((event.get('jev') or {}).get('candidate'), dict):
                cand = event['jev']['candidate']
                views.append({'time': event['time'][11:19], 'entry_candidate': cand.get('choice'),
                              'confidence': cand.get('confidence')})
            elif event.get('event') in ('v2_entry_gate', 'v2_execution_gate', 'split_add', 'split_take_profit',
                                        'submitted', 'sell_submitted', 'v2_risk_exit', 'manual_exit'):
                views.append({'time': event['time'][11:19], 'event': event['event'],
                              **{k: event[k] for k in ('choice', 'symbol', 'reason', 'qty', 'limit', 'tranche') if k in event}})
    state['recent'] = views[-12:]
    return state


def main():
    from src.config.loader import load_agent_config
    from src.tools.mcp import MCPServerAdapter
    now = datetime.now(KST)
    read = MCPServerAdapter('nhplug_readonly', load_agent_config().mcp_servers['nhplug_readonly'])
    brief = {'time': now.isoformat(timespec='seconds'), 'market': market(read, now), 'runner': runner(now)}
    try:
        from etf_news import headlines7
        brief['news'] = headlines7(now)
    except Exception as exc:  # news is context only; its failure must not hide market data
        brief['news_error'] = type(exc).__name__
    print(json.dumps(brief, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
