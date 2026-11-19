"""Record a reconciled v5 close only when KRX raw and frozen Yahoo closes agree."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from kr_swing_v5_daily_data import CODES, verify_snapshot
from kr_swing_v5_forward import CAPITAL, KST, read_paginated, read_response
from src.config.loader import load_agent_config
from src.config.paths import get_runtime_root
from src.tools.krx_daily_tool import KrxDailyTool
from src.tools.mcp import MCPServerAdapter

SOURCE = 'validated_yahoo_adjusted_close'
COST = 'estimated only: buy 0.20%, sell 0.40%; actual fees unavailable'


def _number(value):
    if isinstance(value, bool):
        raise ValueError('invalid numeric value')
    try:
        value = float(str(value).replace(',', ''))
    except (TypeError, ValueError) as exc:
        raise ValueError('invalid numeric value') from exc
    if not math.isfinite(value):
        raise ValueError('invalid numeric value')
    return value


def record_close(db, snapshot: Path, day: date, now: datetime, krx: dict, balance: dict, orders: dict):
    """Store a completed-day estimate after prices and current positions reconcile.

    KRX supplies unadjusted prices; require equality within half a won for every
    basket name before a Yahoo-adjusted value can be used with real NH fills.
    """
    if now.tzinfo is None or now.utcoffset() is None or day >= now.astimezone(KST).date():
        raise ValueError('only prior completed sessions can be evaluated')
    if not isinstance(balance, dict) or balance.get('rsp_cd') != '00000' or balance.get('_pagination_complete') is not True or not isinstance(balance.get('Output_1'), list):
        raise ValueError('complete broker balance unavailable')
    frames = verify_snapshot(snapshot)
    day_str = day.isoformat()
    if set(krx) != set(CODES):
        raise ValueError('KRX prices missing')
    prices = {}
    for code in CODES:
        frame = frames[code]
        if day_str not in frame.index.strftime('%Y-%m-%d'):
            raise ValueError('snapshot does not contain requested session')
        adj = _number(frame.loc[day_str, 'close'])
        payload = krx[code]
        if (not isinstance(payload, dict) or payload.get('ok') is not True
                or payload.get('source') != 'KRX Open API' or payload.get('date') != day_str
                or payload.get('symbol') != code or payload.get('adjustment') != 'raw'
                or payload.get('currency') != 'KRW'):
            raise ValueError('KRX source/date mismatch')
        rows = payload.get('records')
        if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
                or str(rows[0].get('ISU_CD')) != code[:6]
                or str(rows[0].get('BAS_DD')) != day.strftime('%Y%m%d')):
            raise ValueError('KRX symbol/close unavailable')
        raw = _number(rows[0].get('TDD_CLSPRC'))
        if raw <= 0 or adj <= 0 or abs(raw - adj) > 0.5:
            raise ValueError('raw and adjusted closing prices differ')
        prices[code] = {'yahoo_close': adj, 'krx_close': raw}
    try:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS verified_closes (day TEXT PRIMARY KEY, source TEXT NOT NULL, input_sha256 TEXT NOT NULL, evidence TEXT NOT NULL)')
        old = db.execute('SELECT source,input_sha256,evidence FROM verified_closes WHERE day=?', (day_str,)).fetchone()
        if old:
            # A prior close is immutable, including after later trades.
            if old[0] != SOURCE or hashlib.sha256(old[2].encode()).hexdigest() != old[1]:
                raise ValueError('stored close certificate corrupt')
            proof = json.loads(old[2])
            if proof.get('day') != day_str or proof.get('source') != SOURCE or proof.get('prices') != prices or proof.get('broker_snapshot_verified') is not True:
                raise ValueError('stored close certificate differs')
            cash_old = _number(proof.get('cash_gross_krw'))
            held_old = _number(proof.get('holdings_krw'))
            cost_old = _number(proof.get('estimated_cost_krw'))
            db.rollback()
            return {'status':'verified', 'day':day_str, 'equity_estimated':cash_old+held_old-cost_old}
        pending = db.execute("SELECT count(*) FROM intents WHERE status != 'terminal'").fetchone()[0]
        if pending:
            raise ValueError('unreconciled order exists')
        fills = db.execute('SELECT day,symbol,side,quantity,price,order_number FROM fills ORDER BY day,order_number').fetchall()
        intents = db.execute('SELECT day,symbol,side,qty,status,order_number FROM intents').fetchall()
        if set(orders) != {r[0] for r in intents}:
            raise ValueError('broker order history missing')
        for iday, symbol, side, qty, state, number in intents:
            history = orders[iday]
            if not isinstance(history, dict) or history.get('_pagination_complete') is not True or history.get('rsp_cd') not in {'00000','XA102'} or not isinstance(history.get('Output_0'), list):
                raise ValueError('broker order history incomplete')
            matches = [r for r in history['Output_0'] if r.get('itg_orr_no') == number and r.get('iem_cd') == symbol[:6]]
            if len(matches) != 1 or side not in {'BUY','SELL'} or state != 'terminal':
                raise ValueError('broker order identity mismatch')
            row = matches[0]
            if row.get('sby_dit_cd_nm') != ('현금매수' if side == 'BUY' else '현금매도'):
                raise ValueError('broker order side mismatch')
            counts = [row.get(k) for k in ('orr_qty','tot_cns_qty','ny_cns_qty','can_qty')]
            if any(type(n) is not int or n < 0 for n in counts) or counts[0] != qty or counts[2] != 0 or counts[1] + counts[3] != qty:
                raise ValueError('broker order is not terminal')
            own = [f for f in fills if f[0] == iday and f[1] == symbol and f[2] == side and f[5] == number]
            if counts[1] == 0 and own or counts[1] > 0 and (len(own) != 1 or own[0][3] != counts[1] or _number(own[0][4]) != _number(row.get('cns_avg_uit_pr'))):
                raise ValueError('broker fill and journal differ')
        positions = {code: 0 for code in CODES}
        cash = float(CAPITAL)
        costs = 0.0
        for fday, symbol, side, qty, price, number in fills:
            if (not isinstance(fday, str) or len(fday) != 8 or not fday.isdigit()
                    or fday > day.strftime('%Y%m%d') or symbol not in positions or side not in {'BUY', 'SELL'}
                    or type(qty) is not int or qty <= 0 or type(number) is not int or number <= 0):
                raise ValueError('invalid or later fill')
            price = _number(price)
            if price <= 0:
                raise ValueError('invalid fill price')
            matches = [r for r in intents if r[0] == fday and r[1] == symbol and r[2] == side and r[4] == 'terminal' and r[5] == number and type(r[3]) is int and r[3] >= qty]
            if len(matches) != 1:
                raise ValueError('fill has no unique terminal intent')
            positions[symbol] += qty if side == 'BUY' else -qty
            if positions[symbol] < 0:
                raise ValueError('negative strategy position')
            cash += qty * price * (1 if side == 'SELL' else -1)
            costs += qty * price * (.002 if side == 'BUY' else .004)
        if cash < 0:
            raise ValueError('negative strategy cash')
        actual = {code: 0 for code in CODES}
        for row in balance['Output_1']:
            if not isinstance(row, dict) or 'rsdl_qty' not in row:
                raise ValueError('broker position incomplete')
            qty = _number(row['rsdl_qty'])
            if qty < 0 or not qty.is_integer():
                raise ValueError('broker quantity invalid')
            if qty:
                matches = [code for code in CODES if code[:6] == str(row.get('iem_cd', ''))]
                if len(matches) != 1:
                    raise ValueError('unowned broker holding')
                actual[matches[0]] += int(qty)
        if actual != positions:
            raise ValueError('broker and strategy holdings differ')
        holding_value = sum(positions[c] * prices[c]['yahoo_close'] for c in CODES)
        observed = now.astimezone(KST).isoformat()
        evidence = json.dumps({'day':day_str, 'source':SOURCE,
            'snapshot_manifest_sha256':hashlib.sha256((snapshot/'manifest.json').read_bytes()).hexdigest(),
            'prices':prices, 'positions':positions, 'fills':[list(f) for f in fills],
            'estimated_cost_krw':costs, 'cost_assumption':COST,
            'cash_gross_krw':cash, 'holdings_krw':holding_value,
            'broker_snapshot_verified':True, 'broker_order_days':sorted(orders), 'observed_at':observed},
            ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        evidence_hash = hashlib.sha256(evidence.encode()).hexdigest()
        db.execute('INSERT OR REPLACE INTO marks VALUES (?,?,?,?,?)', (day_str,observed,cash,holding_value,cash+holding_value))
        db.execute('INSERT OR REPLACE INTO cost_estimates VALUES (?,?,?,?)', (day_str,COST,costs,cash+holding_value-costs))
        db.execute('INSERT INTO verified_closes VALUES (?,?,?,?)', (day_str,SOURCE,evidence_hash,evidence))
        db.commit()
        return {'status':'verified', 'day':day_str, 'equity_estimated':cash+holding_value-costs}
    except Exception:
        db.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', type=date.fromisoformat, required=True)
    parser.add_argument('--snapshot', type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(Path.home()/'.vibe-trading/.env', override=False)
    now = datetime.now(ZoneInfo('Asia/Seoul'))
    if args.date >= now.date():
        parser.error('date must be a prior completed session')
    cfg = load_agent_config().mcp_servers
    paper = cfg['nhplug_paper']; options = paper.args
    if options.count('--mode') != 1 or options[options.index('--mode') + 1] != 'paper' or options.count('--account') != 1:
        raise ValueError('paper configuration invalid')
    account = options[options.index('--account') + 1]
    readonly = MCPServerAdapter('nhplug_readonly', cfg['nhplug_readonly'])
    broker = MCPServerAdapter('nhplug_paper', paper)
    dbpath = get_runtime_root()/'nhplug/strategies/kr_swing_v5/forward.sqlite3'
    if not dbpath.is_file():
        raise ValueError('strategy journal unavailable')
    lock = dbpath.parent/'runner.lock'
    with lock.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        accounts = read_response(readonly, 'list_accounts', {})
        if accounts.get('rsp_cd') != '00000' or not any(str(row.get('acct_no')) == account and str(row.get('acct_type')) == '03' for row in accounts.get('Output_0', []) if isinstance(row, dict)):
            raise ValueError('paper account identity unavailable')
        balance = read_paginated(broker, 'krstockInquiryBalance', {'act_no':account, 'bnc_bse_cd':'5', 'ltg_aot_dit_cd':'9', 'aet_bse':'2', 'qut_dit_cd':'UNT', 'aly_qut_cd':'1'}, 'Output_1')
        api = KrxDailyTool()
        krx = {code: json.loads(api.execute(date=args.date.isoformat(), symbol=code)) for code in CODES}
        with sqlite3.connect(dbpath) as db:
            account_hash = db.execute("SELECT value FROM meta WHERE name='account_hash'").fetchone()
            if not account_hash or account_hash[0] != hashlib.sha256(account.encode()).hexdigest():
                raise ValueError('journal account mismatch')
            days = [row[0] for row in db.execute('SELECT DISTINCT day FROM intents')]
            orders = {day:read_paginated(broker, 'krstockInquiryDailyOrderExecution',
                {'act_no':account,'orr_dt':day,'orr_mkt_cd':'00','ost_cns_dit':'0'}, 'Output_0') for day in days}
            print(json.dumps(record_close(db,args.snapshot,args.date,now,krx,balance,orders),ensure_ascii=False))


if __name__ == '__main__':
    main()
