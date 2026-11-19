import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import oversold_flow as of
from strategy_settings import DEFAULTS


class FlowTests(unittest.TestCase):
    def test_every_stage_shows_the_live_values(self):
        names = [s['name'] for s in of.stages(DEFAULTS)]
        self.assertEqual(names[0], '시장 폭락 판정')
        self.assertEqual(len(names), 9)
        changed = {**DEFAULTS, 'oversold_v2_half_tp_pct': 6.0, 'oversold_v2_add_on': 0}
        rows = {s['name']: s['what'] for s in of.stages(changed)}
        self.assertIn('+6%', rows['분할 익절'])
        self.assertEqual(rows['2차 매수 (다음 거래일)'], '꺼짐 (한 번에 전량)')

    def test_flow_marks_switched_off_routes(self):
        dot = of.flow_dot(DEFAULTS)
        self.assertTrue(dot.startswith('digraph'))
        self.assertIn('14:30~14:55', dot)
        self.assertNotIn('선별 v2 꺼짐', dot)
        self.assertIn('선별 v2 꺼짐', of.flow_dot({**DEFAULTS, 'oversold_v2_on': 0}))
        self.assertIn('낙폭과대 반등 꺼짐', of.rebound_dot({**DEFAULTS, 'oversold_on': 0}))
        # the reports are written on a schedule (ops/scheduled-tasks): the loop must say when, from the manifest
        tasks = {t['taskId']: t for t in json.loads((Path(of.__file__).parents[1] / 'ops/scheduled-tasks/manifest.json').read_text())}
        for task, label in (('trading-daily-improvement', '평일'), ('oversold-weekly-audit', '금')):
            minute, hour = tasks[task]['cron'].split()[:2]
            self.assertTrue(tasks[task]['enabled'])
            for text in (of.loop_dot(DEFAULTS), json.dumps(of.loop_spec(DEFAULTS), ensure_ascii=False), of.stages(DEFAULTS)[-1]['what']):
                self.assertIn(f'{label} {int(hour):02d}:{int(minute):02d}', text)
                self.assertNotIn('예약 없음', text)

    def test_positions_and_today_events(self):
        kst = ZoneInfo('Asia/Seoul')
        with tempfile.TemporaryDirectory() as d:
            plan = {'opened': datetime(2026, 11, 16, 14, 35, tzinfo=kst).isoformat(), 'profile': 'oversold', 'stop_pct': 15.2,
                    'half_pct': 8.0, 'all_pct': 15.0, 'hold_days': 3, 'target_basis': {'variant': 'v2', 'added': True}}
            (Path(d) / 'exit-plans.json').write_text(json.dumps({'005930': plan, '069500': {**plan, 'target_basis': {}}}))
            held = of.positions(d, datetime(2026, 11, 17, 10, 0, tzinfo=kst))
            self.assertEqual([(h['code'], h['sessions'], h['left'], h['stage']) for h in held], [('005930', 1, 2, '2차 매수 완료')])
            events = [{'time': '2026-11-16T14:30:05+09:00', 'event': 'oversold_v2_gate', 'passed': True},
                      {'time': '2026-11-16T14:31:00+09:00', 'event': 'oversold_v2_signal', 'symbol': '005930', 'research': 'normal'},
                      {'time': '2026-11-13T14:31:00+09:00', 'event': 'oversold_v2_signal', 'symbol': '000660'}]
            (Path(d) / 'events.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
            gates, signals = of.today_events(d, '2026-11-16')
            self.assertEqual((len(gates), [s['symbol'] for s in signals]), (1, ['005930']))


if __name__ == '__main__':
    unittest.main()
