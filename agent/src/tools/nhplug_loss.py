"""Conservative account-loss latch; broker cost and carryover can be counted twice."""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from src.config.paths import get_runtime_root
from src.tools.private_db import private_file

LIMIT = 300_000
_MESSAGES = {'조회가 완료되었습니다', '모의투자 조회가 완료되었습니다', '모의투자 마지막 자료입니다'}


def check_loss(snapshot: dict, now: datetime, scope: str) -> dict:
    """Fail closed on missing data; latch conservative losses by account and KST day.

    This is not exact daily P&L: unrealized loss can predate today and expenses
    may already be included in the broker's realized figure.
    """
    try:  # the dashboard-set daily loss of the shared paper account (order guard bounds), else LIMIT
        from src.tools.nhplug_order_guard import paper_limits
        limit = paper_limits()['daily_loss_krw']
    except Exception:
        limit = LIMIT
    result = {'allowed': False, 'limit': limit, 'loss': None, 'day': None,
              'latched': False, 'reason': 'invalid broker loss data'}
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None or not isinstance(scope, str) or not scope:
        return result
    day = now.astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d')
    result['day'] = day
    if not isinstance(snapshot, dict):
        return result
    try:
        data = snapshot.get('data')
        if snapshot.get('status') != 'ok' or not isinstance(data, dict):
            return result
        if str(data.get('rsp_cd')) not in {'00000', 'XA102'} or str(data.get('rsp_msg', '')).strip().rstrip('.') not in _MESSAGES:
            return result
        output = data.get('Output_0')
        if not isinstance(output, dict):
            return result
        values = []
        for key in ('sby_pls_amt', 'sdr_xps', 'eal_pls_amt'):
            raw = output[key]
            if isinstance(raw, bool) or not isinstance(raw, (str, int, float, Decimal)):
                return result
            value = Decimal(str(raw))
            if not value.is_finite():
                return result
            values.append(value)
        realized, fees, unrealized = values
        if fees < 0:
            return result
        loss = max(-realized, Decimal(0)) + fees + max(-unrealized, Decimal(0))
        result['loss'] = int(loss.to_integral_value(rounding='ROUND_CEILING'))
        root = get_runtime_root() / 'nhplug'
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = root / 'loss.sqlite3'
        private_file(path)  # src.tools.private_db: never os.open an existing database
        with closing(sqlite3.connect(path, timeout=5)) as db, db:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('CREATE TABLE IF NOT EXISTS loss_halts (scope TEXT NOT NULL, day TEXT NOT NULL, PRIMARY KEY(scope,day))')
            db.execute('BEGIN IMMEDIATE')
            if loss >= limit:
                db.execute('INSERT OR IGNORE INTO loss_halts VALUES (?,?)', (scope, day))
            latched = db.execute('SELECT 1 FROM loss_halts WHERE scope=? AND day=?', (scope, day)).fetchone() is not None
        result.update(allowed=not latched, latched=latched, reason='daily loss limit reached' if latched else 'ok')
    except (KeyError, InvalidOperation, TypeError, ValueError, OSError, sqlite3.Error):
        result['reason'] = 'loss validation or state storage failed'
    return result
