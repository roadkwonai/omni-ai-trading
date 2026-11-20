import plistlib
import unittest
from pathlib import Path

import launchd_services as L


class RenderTest(unittest.TestCase):
    def test_paths_follow_checkout_and_home(self):
        for name in L.SERVICES:
            plist = L.render(name, repo=Path('/r'), home=Path('/h'))
            text = plistlib.dumps(plist).decode()
            self.assertNotIn('/Users/', text)
            self.assertEqual(plist['WorkingDirectory'], '/r')
            self.assertTrue(plist['StandardOutPath'].startswith('/h/.vibe-trading/nhplug/'))
            self.assertFalse(plist['KeepAlive'])

    def test_schedules(self):
        self.assertNotIn('StartCalendarInterval', L.render('shared-etf-paper'))
        self.assertEqual([d['Weekday'] for d in L.render('exit-replay')['StartCalendarInterval']], [5])
        self.assertEqual(L.schedule_text('market-movers'), '평일 08:50')
        self.assertEqual(L.schedule_text('trading-day-start'), '평일 08:55')
        live2 = L.render('longterm-live-2')  # 실계좌 2 long-term: armed (user 2026-11-20)
        self.assertEqual((L.schedule_text('longterm-live-2'), live2['ProgramArguments'][-3:]), ('평일 09:35', ['--execute', '--account', 'live-2']))
        retired = {'vibe-advisor', 'vibe-paper', 'vibe-fast-exit-observer', 'vibe-event-advisor', 'vibe-event-replay', 'live-follow-2', 'jev-virtual'}
        self.assertFalse(retired & set(L.SERVICES))  # user 2026-11-17: vibe moves to a long-term engine
        longterm = L.render('vibe-longterm')
        self.assertEqual((L.schedule_text('vibe-longterm'), longterm['ProgramArguments'][-1]), ('평일 08:40', '--run'))  # dry run
        ideas = L.render('us-ideas')  # user 2026-11-18: reference-only data, Fridays
        self.assertEqual((L.schedule_text('us-ideas'), ideas['ProgramArguments'][-1]), ('금 07:00 · 금 07:30', '--scheduled'))
        self.assertEqual([(i['Weekday'], i['Hour'], i['Minute']) for i in ideas['StartCalendarInterval']], [(5, 7, 0), (5, 7, 30)])
        premarket = L.render('premarket')
        self.assertEqual(premarket['ProgramArguments'][-1], '--once')
        self.assertEqual({(d['Hour'], d['Minute']) for d in premarket['StartCalendarInterval']},
                         {(7, 40), *((8, m) for m in range(0, 60, 5)), (9, 0), (9, 10)})  # NXT pre-market every 5 minutes (2026-11-19)
        self.assertEqual(L.schedule_text('nxt-virtual'), '평일 07:55 · 평일 15:35')
        self.assertEqual(len(L.render('nxt-virtual')['StartCalendarInterval']), 10)
        live = L.render('live-follow-1')['ProgramArguments']
        self.assertEqual(live[-2:], ['--account', 'live-1'])
        self.assertIn('--execute', live)  # armed; orders only while the dashboard live-order switch is on (default off)
        dashboard = L.render('shared-etf-dashboard', repo=Path('/r'))
        self.assertEqual(dashboard['ProgramArguments'][:3], ['/r/.venv/bin/streamlit', 'run', '/r/streamlit_namuh/app.py'])


if __name__ == '__main__':
    unittest.main()


def test_the_dashboard_gets_the_pythonpath_so_streamlit_reloads_changed_scripts_modules():
    # 11-18 twice (ledger_owned, order_times): a session added a function to a scripts/ module and the running dashboard
    # kept the old module. Streamlit re-imports a changed module only when it sits in the app folder or in PYTHONPATH.
    import launchd_services as L
    from pathlib import Path
    env = L.render('shared-etf-dashboard', repo=Path('/repo'), home=Path('/home'))['EnvironmentVariables']
    assert env['PYTHONPATH'].split(':') == ['/repo/agent', '/repo/scripts']
