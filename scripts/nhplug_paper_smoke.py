"""One bounded NH paper round trip through the configured MCP; never live orders."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastmcp.client import Client
from fastmcp.client.transports import StdioTransport

KST = ZoneInfo('Asia/Seoul')
SYMBOL = '005930'


def paper_config(servers):
    cfg = servers['nhplug_paper']
    args = cfg['args']
    if args.count('--mode') != 1 or args[args.index('--mode') + 1] != 'paper':
        raise ValueError('paper mode is required')
    return cfg, args[args.index('--account') + 1]


def order_row(response, number):
    rows = response.get('Output_0')
    if not isinstance(rows, list):
        raise ValueError('order list missing; reconcile before any further order')
    matches = [r for r in rows if int(r['itg_orr_no']) == number and r['iem_cd'] == SYMBOL]
    if len(matches) != 1:
        raise ValueError('exact order not found; reconcile manually, do not resubmit')
    return matches[0]


def quantity(balance):
    rows = balance.get('Output_1', [])
    if not isinstance(balance.get('Output_0'), dict) or not isinstance(rows, list):
        raise ValueError('balance blocks missing')
    # An empty initial portfolio avoids acting on truncated multi-page holdings.
    if any(float(r['rsdl_qty']) != 0 for r in rows):
        raise ValueError('smoke test requires an empty paper portfolio')
    return 0


def check_previous_runs(directory):
    for path in directory.glob('nh-smoke-*.json'):
        previous = json.loads(path.read_text())
        if any(s['step'].startswith('intent_') for s in previous.get('steps', [])) and previous.get('status') not in {
            'roundtrip_verified', 'buy_unfilled_cancel_verified',
        }:
            raise ValueError('previous submission needs reconciliation; no new order allowed')


async def run(execute):
    if execute:
        check_previous_runs(Path('.venv'))
    settings = json.loads((Path.home() / '.vibe-trading/agent.json').read_text())
    servers = settings['mcpServers']
    cfg, account = paper_config(servers)
    readonly = servers['nhplug_readonly']
    if readonly['args'][readonly['args'].index('--mode') + 1] != 'readonly':
        raise ValueError('readonly mode is required')
    report = {'time': datetime.now(KST).isoformat(), 'symbol': SYMBOL, 'quantity': 1,
              'execute': execute, 'steps': [], 'status': 'incomplete'}
    out = Path('.venv') / ('nh-smoke-' + datetime.now(KST).strftime('%Y%m%d-%H%M%S') + '.json')

    def save(step, **values):
        report['steps'].append({'step': step, **values})
        fd = os.open(out, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(step, json.dumps(values, ensure_ascii=False), flush=True)

    async def call(client, tool, params):
        result = await client.call_tool(tool, params, timeout=30)
        data = result.structured_content or json.loads(result.content[0].text)
        if not isinstance(data, dict) or 'error' in data:
            raise ValueError('MCP response invalid')
        return data

    def client(config):
        return Client(StdioTransport(command=config['command'], args=config['args'],
                                     env=os.environ.copy(), keep_alive=True))

    async with client(readonly) as read, client(cfg) as paper:
        async def api(op, fields):
            return await call(paper, 'call_api', {'operationId': op, 'input': {'act_no': account, **fields}})

        async def balance():
            return await api('krstockInquiryBalance', {'bnc_bse_cd': '5', 'ltg_aot_dit_cd': '9',
                             'aet_bse': '2', 'qut_dit_cd': 'UNT', 'aly_qut_cd': '1'})

        async def orders():
            return await api('krstockInquiryDailyOrderExecution', {'orr_dt': datetime.now(KST).strftime('%Y%m%d'),
                             'orr_mkt_cd': '00', 'ost_cns_dit': '0'})

        accounts = await call(read, 'list_accounts', {})
        if not any(r.get('acct_no') == account and r.get('acct_type') == '03' for r in accounts.get('Output_0', [])):
            raise ValueError('selected account is not verified as paper type 03')
        locked = await call(read, 'list_apis', {'domain': 'krstock', 'category': '주문'})
        if locked.get('trading_enabled') is not False or locked.get('count') != 0:
            raise ValueError('live order lock not verified')
        before = await balance()
        quantity(before)
        initial_cash = before['Output_0']['dca']
        if initial_cash < 300000:
            raise ValueError('insufficient cash for bounded smoke test')
        existing = await orders()
        if not isinstance(existing.get('Output_0'), list) or any(int(r['ny_cns_qty']) for r in existing['Output_0']):
            raise ValueError('existing open orders or missing order list')
        quote = await call(read, 'get_stock_price', {'stock_code': SYMBOL})
        q = quote.get('Output_0', {})
        price = q.get('stck_prpr')
        if q.get('iem_cd') != SYMBOL or not isinstance(price, (int, float)) or not 0 < price <= 300000:
            raise ValueError('quote invalid or above fixed 300000 KRW cap')
        save('preflight', paper_verified=True, live_locked=True, initial_cash=initial_cash, price=price)
        if not execute:
            report['status'] = 'preflight_only'
            save('finished')
            return
        now = datetime.now(KST)
        if now.weekday() >= 5 or not '09:05' <= now.strftime('%H:%M') <= '15:00':
            raise ValueError('outside bounded test session')
        for op in ('krstockOrderCashBuy', 'krstockOrderCashSell', 'krstockOrderCancel'):
            schema = await call(paper, 'describe_api', {'operationId': op})
            if schema.get('operationId') != op or not schema.get('input_fields'):
                raise ValueError('order schema unavailable')
        fields = {'iem_cd': SYMBOL, 'orr_qty': 1, 'orr_pr': price, 'nmn_pr_tp_cd': '01',
                  'orr_cnd_dit_cd': '00', 'ssl_nmn_pr_dit_cd': '00', 'rmt_mkt_cd': 'KRX', 'sor_mkt_sli_yn': 'N'}
        # Persist intent before submission; ambiguous responses are never retried.
        for side in ('Buy', 'Sell'):
            save('intent_' + side, price=price)
            response = await api('krstockOrderCash' + side, fields)
            number = response.get('Output_0', {}).get('mkt_orr_no')
            save('submitted_' + side, order_number=number, message=response.get('rsp_msg'))
            if not isinstance(number, int) or number <= 0:
                raise ValueError('ambiguous submission: reconcile order history, do not retry')
            try:
                for _ in range(6):
                    await asyncio.sleep(5)
                    row = order_row(await orders(), number)
                    save('state_' + side, order_number=number, filled=row['tot_cns_qty'],
                         remaining=row['ny_cns_qty'], cancelled=row['can_qty'], rejection=row.get('orr_rjt_rsn_cd_nm'))
                    if int(row['tot_cns_qty']) == 1 or int(row['can_qty']) == 1:
                        break
            finally:
                row = order_row(await orders(), number)
                if int(row['ny_cns_qty']) > 0:
                    cancel = await api('krstockOrderCancel', {'org_mkt_orr_no': number,
                                       'all_pat_dit_cd': '1', 'iem_cd': SYMBOL})
                    save('cancel_' + side, message=cancel.get('rsp_msg'))
                    await asyncio.sleep(2)
                row = order_row(await orders(), number)
                save('final_' + side, filled=row['tot_cns_qty'], remaining=row['ny_cns_qty'], cancelled=row['can_qty'])
                if int(row['ny_cns_qty']) != 0:
                    raise ValueError('open order remains; cleanup not verified')
            after = await balance()
            holdings = after.get('Output_1', [])
            held = sum(float(r['rsdl_qty']) for r in holdings if r.get('iem_cd') == SYMBOL)
            save('balance_' + side, cash=after['Output_0']['dca'], shares=held)
            if side == 'Buy' and (int(row['tot_cns_qty']) != 1 or held != 1):
                report['status'] = 'buy_unfilled_cancel_verified' if int(row['can_qty']) == 1 and held == 0 else 'reconciliation_required'
                save('finished')
                return
            if side == 'Sell' and (int(row['tot_cns_qty']) != 1 or held != 0):
                report['status'] = 'sell_incomplete_paper_position_may_remain'
                save('finished')
                return
        report['status'] = 'roundtrip_verified'
        save('finished')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='One paper buy/sell, max 1 share and KRW 300000')
    # Hold a process lock across preflight and submission to exclude duplicate CLI runs.
    import fcntl
    with Path('.venv/nh-smoke.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(run(parser.parse_args().execute))
