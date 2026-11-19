"""Read-only NH live loss preflight; does not enable or submit orders."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from zoneinfo import ZoneInfo

from dotenv import dotenv_values
from src.config.loader import load_agent_config
from src.config.paths import get_runtime_root
from src.tools.mcp import MCPServerAdapter

LIMIT = 20_000
KST = ZoneInfo('Asia/Seoul')
from local_env import ENV  # <repo>/.env (see .env.example)
from src.tools.private_db import private_file


def evaluate(snapshot, now, scope, root):
    """Conservatively latch negative P&L plus fees; not a complete daily equity calculation."""
    if now.tzinfo is None or not scope or not isinstance(snapshot, dict):
        raise ValueError('invalid preflight input')
    if snapshot.get('rsp_cd') not in ('00166', '00000') or snapshot.get('rsp_msg', '').strip().rstrip('.') != '조회가 완료되었습니다':
        raise ValueError('broker loss response unavailable')
    data = snapshot.get('Output_0')
    if not isinstance(data, dict):
        raise ValueError('broker loss fields unavailable')
    try:
        raw = [data[k] for k in ('sby_pls_amt', 'sdr_xps', 'eal_pls_amt')]
        if any(isinstance(v, bool) or not isinstance(v, (str, int, float, Decimal)) for v in raw):
            raise ValueError('invalid loss value')
        realized, fees, unrealized = [Decimal(str(v)) for v in raw]
        if not all(v.is_finite() for v in (realized, fees, unrealized)) or fees < 0:
            raise ValueError('invalid loss value')
        loss = int((max(-realized, 0) + fees + max(-unrealized, 0)).to_integral_value(rounding=ROUND_CEILING))
    except (KeyError, InvalidOperation, TypeError) as exc:
        raise ValueError('invalid loss values') from exc
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / 'live-loss-preflight.sqlite3'
    private_file(path)  # src.tools.private_db: never os.open an existing database
    day = now.astimezone(KST).strftime('%Y%m%d')
    with closing(sqlite3.connect(path, timeout=5)) as db, db:
        db.execute('PRAGMA synchronous=FULL')
        db.execute('CREATE TABLE IF NOT EXISTS halts (scope TEXT NOT NULL, day TEXT NOT NULL, PRIMARY KEY(scope,day))')
        db.execute('BEGIN IMMEDIATE')
        if loss >= LIMIT:
            db.execute('INSERT OR IGNORE INTO halts VALUES (?,?)', (scope, day))
        latched = db.execute('SELECT 1 FROM halts WHERE scope=? AND day=?', (scope, day)).fetchone() is not None
    return {'loss_estimate_krw': loss, 'limit_krw': LIMIT, 'halt_latched': latched, 'date': day,
            'live_orders_enabled': False, 'estimate': 'conservative; not exact daily equity loss'}


def summarize_orders(snapshot):
    """Summarize only broker responses with proven complete pagination."""
    if not isinstance(snapshot, dict):
        raise ValueError('broker order response invalid')
    code = str(snapshot.get('rsp_cd', ''))
    message = snapshot.get('rsp_msg', '').strip().rstrip('.')
    rows = snapshot.get('Output_0')
    if code == '11512' and message == '데이터가 존재하지 않습니다' and (rows is None or rows == []):
        rows = []
    elif code not in ('00166', '00000') or message != '조회가 완료되었습니다' or not isinstance(rows, list):
        raise ValueError('broker order response unavailable')
    metadata = snapshot.get('_nh_pagination')
    if not isinstance(metadata, dict) or metadata.get('complete') is not True:
        raise ValueError('order pagination not confirmed complete')
    pending = []
    filled_total = cancelled_total = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('broker order state invalid')
        counts = [row.get(k) for k in ('orr_qty', 'tot_cns_qty', 'ny_cns_qty', 'can_qty')]
        if any(type(v) is not int or v < 0 for v in counts):
            raise ValueError('broker order quantities invalid')
        ordered, filled, remaining, cancelled = counts
        if filled + remaining + cancelled != ordered:
            raise ValueError('broker order quantities do not reconcile')
        filled_total += filled
        cancelled_total += cancelled
        if row['ny_cns_qty']:
            pending.append({'symbol': row.get('iem_cd'), 'remaining': row['ny_cns_qty']})
    return {'returned_orders': len(rows), 'open_orders': pending, 'filled_shares': filled_total,
            'cancelled_shares': cancelled_total, 'pagination_complete': True}


def main():
    values = dotenv_values(ENV, interpolate=False)
    account = ''.join(c for c in (values.get('ACCOUNT_NUMBER') or '') if c.isdigit())
    config = load_agent_config().mcp_servers['nhplug_readonly']
    if len(account) != 11 or config.args.count('--mode') != 1 or config.args[config.args.index('--mode') + 1] != 'readonly':
        raise ValueError('live account or readonly profile invalid')
    adapter = MCPServerAdapter('nhplug_readonly', config)
    accounts = adapter.call_tool('list_accounts', {})
    entries = accounts.get('data', {}).get('Output_0', [])
    matching = [v for v in entries if isinstance(v, dict) and v.get('acct_no') == account and v.get('acct_type') in ('01', '02')]
    if accounts.get('status') != 'ok' or len(matching) != 1:
        raise ValueError('live account identity not verified')
    result = adapter.call_tool('call_api', {'operationId': 'krstockInquiryRealizedPnl', 'input': {
        'act_no': account, 'iqr_dit_cd1': '0', 'fee_dit_cd': '1', 'qut_dit_cd': 'KRX', 'aly_qut_cd': '1'}})
    if result.get('status') != 'ok' or not isinstance(result.get('data'), dict):
        raise ValueError('broker loss query failed')
    scope = hashlib.sha256(('nh-live:' + account).encode()).hexdigest()
    now = datetime.now(KST)
    risk = evaluate(result['data'], now, scope, get_runtime_root() / 'nhplug')
    orders = adapter.call_tool('call_api', {'operationId': 'krstockInquiryDailyOrderExecution', 'input': {
        'act_no': account, 'orr_dt': now.strftime('%Y%m%d'), 'orr_mkt_cd': '00', 'ost_cns_dit': '0'}, 'want_pagination': True})
    if orders.get('status') != 'ok' or not isinstance(orders.get('data'), dict):
        raise ValueError('broker order query failed')
    print(json.dumps({'risk': risk, 'orders': summarize_orders(orders['data'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
