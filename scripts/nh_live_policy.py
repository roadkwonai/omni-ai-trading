"""Deterministic live policy decisions and audit; no order transport."""
import json
import sqlite3
import os
from pathlib import Path
from src.tools.private_db import private_file

POLICY = {'version':'inverse-capital-v2','symbol':'114800','capital_krw':1_000_000,
          'daily_loss_krw':20_000,'order_shares':1,'max_order_krw':10_000,
          'order_type':'limit','end_time':'15:00','poll_seconds':60}


def decide(observation, *, halted, transport_ready=False):
    risk = observation.get('risk')
    shares = observation.get('shares')
    opened = observation.get('open_order_rows')
    if (observation.get('symbol') != POLICY['symbol'] or type(shares) not in (int,float)
            or shares < 0 or shares != int(shares) or type(opened) is not int or opened < 0
            or not isinstance(risk, dict) or type(risk.get('loss_estimate_krw')) is not int
            or risk['loss_estimate_krw'] < 0 or type(risk.get('halt_latched')) is not bool):
        return {'desired':'WAIT','execution':'BLOCKED','reason':'invalid observation'}
    if risk['loss_estimate_krw'] >= POLICY['daily_loss_krw'] or risk['halt_latched']:
        desired = 'RECONCILE_THEN_SELL' if opened else 'SELL' if shares else 'STOP_BUYING'
        reason = 'daily loss stop'
    elif opened:
        desired, reason = 'RECONCILE', 'open orders block new entries'
    elif shares:
        desired, reason = 'HOLD', 'position within daily risk limit'
    else:
        desired, reason = 'WAIT', 'no validated entry signal'
    if halted:
        execution, reason = 'BLOCKED', reason + '; global live halt'
    elif not transport_ready:
        execution, reason = 'BLOCKED', reason + '; guarded live transport unavailable'
    else:
        execution = 'READY'
    return {'desired':desired,'execution':execution,'reason':reason,'policy_version':POLICY['version']}


def record(path, observation, decision):
    path = Path(path); path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    private_file(path)  # src.tools.private_db
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS policy (version TEXT PRIMARY KEY, data TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS decisions (time TEXT NOT NULL, version TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(time,version))')
        db.execute('INSERT OR IGNORE INTO policy VALUES (?,?)',(POLICY['version'],json.dumps(POLICY)))
        db.execute('INSERT OR IGNORE INTO decisions VALUES (?,?,?)',(observation['time'],POLICY['version'],json.dumps(decision)))
