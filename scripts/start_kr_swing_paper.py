"""Initialize a read-only forward observation record; never submits orders."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.config.loader import load_agent_config
from src.config.paths import get_runtime_root
from src.tools.mcp import MCPServerAdapter

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = ROOT / 'strategies/kr_swing_v1/protocol.json'
METADATA = ROOT / '.venv/kr-swing-data/metadata.json'
SUMMARY = ROOT / 'results/kr-swing-v1/validation_summary.json'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_snapshot(account, accounts, balance, orders):
    if accounts.get('rsp_cd') != '00000' or not any(
        str(row.get('acct_no')) == account and str(row.get('acct_type')) == '03'
        for row in accounts.get('Output_0', []) if isinstance(row, dict)
    ):
        raise ValueError('paper account identity unavailable')
    if balance.get('rsp_cd') != '00000' or not isinstance(balance.get('Output_0'), dict):
        raise ValueError('paper balance unavailable')
    rows = balance.get('Output_1')
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) or 'rsdl_qty' not in row or float(row['rsdl_qty']) != 0
        for row in rows
    ):
        raise ValueError('paper holdings are nonempty or incomplete')
    if orders.get('rsp_cd') not in ('00000', 'XA102') or not isinstance(orders.get('Output_0'), list):
        raise ValueError('paper order history unavailable')
    if any(not isinstance(row, dict) or 'ny_cns_qty' not in row or float(row['ny_cns_qty']) != 0
           for row in orders['Output_0']):
        raise ValueError('open orders exist or order history is incomplete')


def initialize(accounts, balance, orders, account, target, now):
    protocol_hash, metadata_hash = digest(PROTOCOL), digest(METADATA)
    if target.exists():
        old = json.loads(target.read_text())
        if (old.get('protocol_sha256') != protocol_hash or old.get('metadata_sha256') != metadata_hash
                or old.get('account_sha256') != hashlib.sha256(account.encode()).hexdigest()):
            raise ValueError('existing observation has different inputs; refusing overwrite')
        return old
    validate_snapshot(account, accounts, balance, orders)
    try:
        summary = json.loads(SUMMARY.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError('validation summary is unavailable or invalid') from exc
    if (not isinstance(summary, dict) or summary.get('protocol_sha256') != protocol_hash
            or summary.get('metadata_sha256') != metadata_hash
            or type(summary.get('promotion_passed')) is not bool):
        raise ValueError('validation summary does not match current strategy and data')
    passed = summary.get('promotion_passed') is True
    state = {'strategy_id': 'kr_swing_v1', 'protocol_sha256': protocol_hash,
             'metadata_sha256': metadata_hash, 'account_sha256': hashlib.sha256(account.encode()).hexdigest(),
             'initialized_at': now.isoformat(), 'initial_cash_krw': 30000000, 'positions': [],
             'earliest_signal_bar': 'first completed KRX daily bar after initialization',
             'earliest_execution': 'next trading session after that signal bar',
             'status': 'observation_only' if passed else 'research_rejected',
             'promotion_passed': passed, 'forward_performance_validated': False,
             'broker_snapshot_limitation': 'NH MCP does not expose pagination headers'}
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(state, stream, ensure_ascii=False, indent=2)
    return state


def response(adapter, tool, args):
    result = adapter.call_tool(tool, args)
    if result.get('status') != 'ok' or not isinstance(result.get('data'), dict):
        raise ValueError('broker read failed')
    return result['data']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--initialize', action='store_true')
    args = parser.parse_args()
    target = get_runtime_root() / 'nhplug/strategies/kr_swing_v1/forward.json'
    if not args.initialize:
        print(json.dumps({'status': 'initialized' if target.exists() else 'not_initialized',
                          'path': str(target)}, ensure_ascii=False))
        return
    configs = load_agent_config().mcp_servers
    paper = configs['nhplug_paper']
    options = paper.args
    assert options.count('--mode') == 1 and options[options.index('--mode') + 1] == 'paper'
    assert options.count('--account') == 1
    account = options[options.index('--account') + 1]
    read = MCPServerAdapter('nhplug_readonly', configs['nhplug_readonly'])
    broker = MCPServerAdapter('nhplug_paper', paper)
    now = datetime.now(ZoneInfo('Asia/Seoul'))
    accounts = response(read, 'list_accounts', {})
    balance = response(broker, 'call_api', {'operationId': 'krstockInquiryBalance', 'input': {
        'act_no': account, 'bnc_bse_cd': '5', 'ltg_aot_dit_cd': '9', 'aet_bse': '2',
        'qut_dit_cd': 'UNT', 'aly_qut_cd': '1'}})
    orders = response(broker, 'call_api', {'operationId': 'krstockInquiryDailyOrderExecution', 'input': {
        'act_no': account, 'orr_dt': now.strftime('%Y%m%d'), 'orr_mkt_cd': '00', 'ost_cns_dit': '0'}})
    state = initialize(accounts, balance, orders, account, target, now)
    print(json.dumps({'status': state['status'], 'path': str(target)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
