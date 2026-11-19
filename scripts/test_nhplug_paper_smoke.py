"""Offline lifecycle checks: fake MCP only, no credentials or broker traffic."""
import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from datetime import datetime
from zoneinfo import ZoneInfo

from nhplug_paper_smoke import order_row, paper_config, quantity, check_previous_runs, run


class SmokeTests(unittest.TestCase):
    def test_restart_blocks_unresolved_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'nh-smoke-test.json'
            p.write_text(json.dumps({'status': 'incomplete', 'steps': [{'step': 'intent_Buy'}]}))
            with self.assertRaises(ValueError):
                check_previous_runs(Path(directory))
            p.write_text(json.dumps({'status': 'buy_unfilled_cancel_verified', 'steps': [{'step': 'intent_Buy'}]}))
            check_previous_runs(Path(directory))

    def test_live_mode_is_refused(self):
        for args in (['--mode', 'live', '--account', 'fake'],
                     ['--mode', 'paper', '--mode', 'readonly', '--account', 'fake']):
            with self.assertRaises(ValueError):
                paper_config({'nhplug_paper': {'args': args}})

    def test_zero_quantity_rows_are_not_positions(self):
        balance = {'Output_0': {}, 'Output_1': [{'iem_cd': '005930', 'itg_bnc_qty': 0, 'rsdl_qty': 0}, {'iem_cd': '', 'itg_bnc_qty': 0, 'rsdl_qty': 0}]}
        self.assertEqual(quantity(balance), 0)
        balance['Output_1'][0]['rsdl_qty'] = 1
        with self.assertRaises(ValueError):
            quantity(balance)

    def test_missing_remaining_quantity_fails_closed(self):
        with self.assertRaises((ValueError, KeyError)):
            quantity({'Output_0': {}, 'Output_1': [{'iem_cd': '005930', 'itg_bnc_qty': 0}]})

    def test_exact_order_required(self):
        with self.assertRaises(ValueError):
            order_row({'Output_0': [{'itg_orr_no': 7, 'iem_cd': 'other'}]}, 7)
        with self.assertRaises(ValueError):
            order_row({}, 7)

    def exercise(self, filled=False, ambiguous=False):
        calls = []
        state = {'number': 0, 'cancelled': False}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def call_tool(self, tool, params, **kwargs):
                calls.append((tool, params))
                if tool == 'list_accounts':
                    d = {'Output_0': [{'acct_no': 'fake', 'acct_type': '03'}]}
                elif tool == 'list_apis':
                    d = {'count': 0, 'trading_enabled': False}
                elif tool == 'get_stock_price':
                    d = {'Output_0': {'iem_cd': '005930', 'stck_prpr': 180000}}
                elif tool == 'describe_api':
                    d = {'operationId': params['operationId'], 'input_fields': ['fake']}
                else:
                    op = params['operationId']
                    if op == 'krstockInquiryBalance':
                        held = filled and state['number'] == 1
                        d = {'Output_0': {'dca': 5000000}, 'Output_1':
                             [{'iem_cd': '005930', 'itg_bnc_qty': 0, 'ny_stl_qty': 1, 'rsdl_qty': 1}] if held else []}
                    elif op == 'krstockInquiryDailyOrderExecution':
                        d = {'Output_0': [] if not state['number'] else [{
                            'itg_orr_no': state['number'], 'iem_cd': '005930',
                            'tot_cns_qty': int(filled), 'ny_cns_qty': int(not filled and not state['cancelled']),
                            'can_qty': int(state['cancelled'])}]}
                    elif op == 'krstockOrderCancel':
                        state['cancelled'] = True
                        d = {'Output_0': {'mkt_orr_no': 99}}
                    else:
                        state['number'] += 1
                        d = {'Output_0': {} if ambiguous else {'mkt_orr_no': state['number']}}
                return SimpleNamespace(structured_content=d)

        with tempfile.TemporaryDirectory() as directory:
            old = Path.cwd()
            home = Path(directory)
            (home / '.vibe-trading').mkdir()
            (home / '.venv').mkdir()
            servers = {f'nhplug_{mode}': {'command': 'fake', 'args':
                       ['--mode', mode, '--account', 'fake']} for mode in ('readonly', 'paper')}
            (home / '.vibe-trading/agent.json').write_text(json.dumps({'mcpServers': servers}))
            try:
                os.chdir(home)
                with patch('nhplug_paper_smoke.Path.home', return_value=home), \
                     patch('nhplug_paper_smoke.Client', FakeClient), \
                     patch('nhplug_paper_smoke.datetime') as clock, \
                     patch('nhplug_paper_smoke.asyncio.sleep', new=AsyncMock()), \
                     contextlib.redirect_stdout(io.StringIO()):
                    clock.now.return_value = datetime(2026, 11, 6, 10, 0, tzinfo=ZoneInfo('Asia/Seoul'))
                    if ambiguous:
                        with self.assertRaisesRegex(ValueError, 'ambiguous'):
                            asyncio.run(run(True))
                    else:
                        asyncio.run(run(True))
                report = json.loads(next((home / '.venv').glob('nh-smoke-*')).read_text())
                self.assertNotIn('fake', json.dumps(report))
                return calls, report
            finally:
                os.chdir(old)

    def test_unfilled_buy_cancelled_without_sell(self):
        calls, report = self.exercise()
        ops = [p.get('operationId') for t, p in calls if t == 'call_api']
        self.assertEqual(ops.count('krstockOrderCashBuy'), 1)
        self.assertEqual(ops.count('krstockOrderCancel'), 1)
        self.assertNotIn('krstockOrderCashSell', [p.get('operationId') for t, p in calls if t == 'call_api'])
        self.assertEqual(report['status'], 'buy_unfilled_cancel_verified')

    def test_filled_roundtrip(self):
        _, report = self.exercise(filled=True)
        self.assertEqual(report['status'], 'roundtrip_verified')

    def test_ambiguous_submission_never_retried(self):
        calls, report = self.exercise(ambiguous=True)
        buys = [p for t, p in calls if t == 'call_api' and p['operationId'] == 'krstockOrderCashBuy']
        self.assertEqual(len(buys), 1)
        self.assertEqual(report['status'], 'incomplete')


if __name__ == '__main__':
    unittest.main()
