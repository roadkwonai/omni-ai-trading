import json
import tempfile
import unittest
from pathlib import Path

import engine_roles as roles
import nh_paper_isolated as p


def accounts(nhplug, engine):
    (nhplug / 'accounts.json').write_text(json.dumps({'version': 1, 'accounts': {'paper-shared': {
        'kind': 'paper', 'engine': engine, 'auto': True, 'budget': {'first_buy_krw': 1_000_000, 'per_symbol_krw': 3_000_000}}}}))


class EngineRoles(unittest.TestCase):
    def test_main_trades_shared_and_secondary_trades_virtually(self):
        with tempfile.TemporaryDirectory() as d:
            nhplug = Path(d)
            self.assertEqual((roles.ledger_root(nhplug, 'jev').name, roles.ledger_root(nhplug, 'vibe').name),
                             ('shared-etf-paper', 'vibe-paper'))  # default: Jev main
            accounts(nhplug, 'vibe')  # no longer a valid main (user 2026-11-17): the record falls back to Jev
            self.assertEqual((roles.ledger_root(nhplug, 'jev').name, roles.ledger_root(nhplug, 'vibe').name),
                             ('shared-etf-paper', 'vibe-paper'))
            board = roles.scoreboard(nhplug)
            self.assertEqual((board['jev']['role'], board['vibe']['fills']), ('main', 'virtual'))
            self.assertIsNone(roles.ledger_root(nhplug, 'other'))

    def test_recorded_bars_reuse_the_shared_runner_minute(self):
        from datetime import datetime
        with tempfile.TemporaryDirectory() as d:
            nhplug = Path(d)
            shared, virtual = nhplug / 'shared-etf-paper', nhplug / 'jev-virtual'
            shared.mkdir(); virtual.mkdir()
            now = datetime.now(p.KST)
            rows = [{'bsop_time': '100000', 'stck_prpr': '1'}]
            (shared / 'events.jsonl').write_text(json.dumps({'time': now.isoformat(), 'event': 'market_context',
                                                             'market_bars': {'K200': rows, '005930': rows}}) + '\n')

            class NH:
                calls = []
                def call_tool(self, tool, params, **kwargs):
                    self.calls.append(params['input'].get('iem_cd'))
                    return {'status': 'ok', 'data': {}}
            nh = NH()
            reader = p.RecordedBars(nh, shared)
            bars = lambda code: reader.call_tool('call_api', {'operationId': 'krstockQuotePeriod', 'input': {
                'iem_cd': code, 'gubun': '5', 'xtick': '1'}})
            self.assertEqual(bars('069500')['source'], 'shared_runner_record')
            self.assertEqual(bars('005930')['data']['Output_1'][0]['bsop_date'], now.strftime('%Y%m%d'))
            bars('122630')  # not recorded: NH
            self.assertEqual(nh.calls, ['122630'])


if __name__ == '__main__':
    unittest.main()
