import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import nxt_virtual as n

KST = n.KST
STRONG = {'futures': {'nasdaq': {'pct': 0.4}, 'sp500': {'pct': 0.2}, 'oil': {'pct': 0.5}, 'gold': {'pct': -0.1}},
          'night': {'available': True, 'pct': 0.3},
          'index': {'KOSPI': {'regime': '상승장', 'flow': '강세', 'foreign': 1200.0, 'institution': -300.0}}}


def krx(close=10_500, prev=10_000, high=10_550, low=10_000, vol_rate=220.0, value=50_000, d5=10_600):
    return {'stck_prpr': close, 'stck_prdy_clpr': prev, 'stck_hgpr': high, 'stck_lwpr': low, 'vol_rate': vol_rate,
            'acml_tr_pbmn': value, 'd5_hgpr': d5, 'd20_hgpr': 11_000}


def nxt(price=10_520, bid=10_510, ask=10_520):
    return {'stck_prpr': price, 'bidp': bid, 'askp': ask, 'acml_tr_pbmn': 900, 'acml_vol': 80_000, 'hoga_bsop_hour': '19:31:00'}


class Read:
    """Fake NH read-only adapter: quotes[(code, market)] -> output dict (None = not traded on that market)."""

    def __init__(self, quotes):
        self.quotes = quotes

    def call_tool(self, tool, params):
        q = self.quotes.get((params['input']['iem_cd'], params['input']['market_cd']))
        if q is None:
            return {'status': 'ok', 'data': {'rsp_cd': '00500', 'rsp_msg': 'NXT 거래불가종목 입니다.'}}
        return {'status': 'ok', 'data': {'Output_0': q}}


class GateTests(unittest.TestCase):
    def test_strong_day_passes_and_each_weakness_has_a_code(self):
        self.assertEqual(n.market_gate(STRONG, 'KOSPI'), [])
        weak = json.loads(json.dumps(STRONG))
        weak['index']['KOSPI'].update(regime='하락장', flow='혼조', foreign=-1.0)
        weak['futures']['nasdaq']['pct'] = -0.5
        weak['night']['pct'] = -0.2
        weak['futures']['oil']['pct'] = 4.0
        self.assertEqual(n.market_gate(weak, 'KOSPI'), ['regime_not_rising', 'today_not_strong', 'flows_not_supporting',
                                                         'us_futures_down', 'night_futures_down', 'oil_spike'])

    def test_missing_futures_block_but_missing_night_futures_do_not(self):
        m = {**STRONG, 'futures': {'nasdaq': {'pct': 1.0}}, 'night': {'available': False}}
        self.assertEqual(n.market_gate(m, 'KOSPI'), ['us_futures_unavailable'])

    def test_stock_gate(self):
        f = n.features(krx(), nxt())
        self.assertEqual(f['change_pct'], 5.0)
        self.assertEqual(n.stock_gate(f), [])
        weak = n.features(krx(close=10_100, high=10_600, vol_rate=90.0), nxt(price=10_050, bid=10_000, ask=10_100))
        self.assertEqual(n.stock_gate(weak), ['change_out_of_range', 'weak_close', 'volume_not_high', 'below_5d_high', 'nxt_below_close', 'spread_too_wide'])

    def test_night_futures_only_counts_in_the_night_session(self):
        now = datetime(2026, 11, 12, 19, 30, tzinfo=KST)
        stamp = lambda h, m, d=12: int(datetime(2026, 11, d, h, m, tzinfo=KST).timestamp())
        row = lambda opened, updated, pct: lambda: {'close': 830.5, 'change': pct, 'time': opened, 'last_bar_update_time': updated}
        night = n.night_futures(now, row(stamp(18, 0), stamp(19, 10), 0.4215))
        self.assertEqual((night['available'], night['session'], night['pct']), (True, 'night', 0.42))
        self.assertFalse(n.night_futures(now, row(stamp(8, 45), stamp(15, 35), -1.08))['available'])  # day bar
        self.assertFalse(n.night_futures(now, row(stamp(18, 0, 11), stamp(5, 59), 0.3))['available'])  # yesterday's night, stale
        self.assertFalse(n.night_futures(now, lambda: {})['available'])
        after_close = datetime(2026, 11, 13, 15, 35, tzinfo=KST)  # the day bar opened 11/12 18:00, updated at the close
        self.assertEqual(n.night_futures(after_close, row(stamp(18, 0, 12), stamp(15, 34, 13), -0.57))['session'], 'day')
        # 11/17 07:40: the night session that opened 11/16 18:00 has ended — labelled night, still not for the gate
        before_open = datetime(2026, 11, 13, 7, 40, tzinfo=KST)
        ended = n.night_futures(before_open, row(stamp(18, 0, 12), stamp(6, 20, 13), -1.76))
        self.assertEqual((ended['available'], ended['session'], ended['pct']), (False, 'night', -1.76))
        self.assertEqual(n.market_gate({'night': ended, 'futures': {}}, 'KOSPI'), n.market_gate({'futures': {}}, 'KOSPI'))


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.journal = n.Journal(self.root)
        self.quotes = {('111111', 'KRX'): krx(), ('111111', 'NXT'): nxt(), ('222222', 'KRX'): krx(), ('222222', 'NXT'): None}
        self.read = Read(self.quotes)
        patcher = mock.patch.object(n, 'candidates', return_value=[
            {'code': '111111', 'name': 'A', 'index': 'KOSPI200', 'change_pct': 5.0},
            {'code': '222222', 'name': 'B', 'index': 'KOSPI200', 'change_pct': 4.0}])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_evening_decision_buys_virtually_then_the_morning_sells_and_labels(self):
        evening = datetime(2026, 11, 12, 19, 31, tzinfo=KST)
        book = n.broker(self.read, self.root, clock=lambda: evening)
        n.decide(self.read, self.journal, book, evening, STRONG, next_open=lambda d: True)
        decisions = self.journal.rows('nxt_decision', '20261112')
        self.assertEqual([(d['symbol'], d['passed']) for d in decisions], [('111111', True), ('222222', False)])
        self.assertEqual(decisions[1]['reasons'], ['not_nxt_eligible'])
        self.assertEqual(book.holdings()['111111'][0], 1_000_000 // 10_520)

        clock = [datetime(2026, 11, 13, 8, 5, tzinfo=KST)]  # 08:05 in the NXT pre-market, then 10:00 after one sleep
        self.quotes[('111111', 'NXT')] = nxt(price=10_640, bid=10_630, ask=10_640)   # +1.0%: half
        self.quotes[('111111', 'KRX')] = {**krx(close=10_600), 'bidp': 10_590, 'askp': 10_600}
        n.morning(self.read, self.journal, lambda: clock[0], previous_day='20261112',
                  sleep=lambda s: clock.__setitem__(0, datetime(2026, 11, 13, 10, 0, tzinfo=KST)))
        exits = self.journal.rows('nxt_exit', '20261113')
        self.assertEqual([e['reason'] for e in exits], ['take_profit_half', 'deadline'])
        self.assertEqual(n.broker(self.read, self.root).holdings(), {})
        outcomes = self.journal.rows('nxt_outcome', '20261113')
        self.assertEqual({(o['symbol'], o['at']) for o in outcomes}, {('111111', '08:05'), ('111111', '09:05'), ('111111', '10:00')})
        self.assertEqual({o['at']: o.get('late_min') for o in outcomes}, {'08:05': None, '09:05': 55, '10:00': None})  # 09:05 taken at 10:00
        self.assertEqual([e['qty'] for e in exits], [47, 48])
        self.assertEqual(n.audit(self.root)['problems'], ['2026-11-12: decisions without a market snapshot'])  # decide() ran alone

    def test_an_unread_label_is_retried_once_and_never_duplicated(self):
        evening = datetime(2026, 11, 12, 19, 31, tzinfo=KST)
        for code in ('111111', '333333'):
            self.journal.write('nxt_decision', evening, symbol=code, passed=False, decision_price=10_000)
        self.quotes[('333333', 'KRX')] = None  # NH did not answer for this one at 09:05
        at905 = datetime(2026, 11, 13, 9, 5, tzinfo=KST)
        self.assertEqual(n.label(self.read, self.journal, at905, '20261112', '09:05'), 1)
        self.quotes[('333333', 'KRX')] = krx()
        self.assertEqual(n.label(self.read, self.journal, at905.replace(minute=8), '20261112', '09:05'), 0)
        self.assertEqual(n.label(self.read, self.journal, at905.replace(minute=9), '20261112', '09:05'), 0)  # a restart: no repeats
        outcomes = self.journal.rows('nxt_outcome', '20261113')
        self.assertEqual([(o['symbol'], o.get('late_min')) for o in outcomes], [('111111', None), ('333333', 3)])
        self.journal.write('nxt_started', at905.replace(hour=8), session='morning', decision_day='20261112')
        self.journal.write('nxt_stopped', at905.replace(hour=10, minute=0, second=30), session='morning')
        self.assertIn("20261112 08:05: no label for ['111111', '333333']", n.audit(self.root)['problems'])
        self.assertFalse(any('09:05' in p for p in n.audit(self.root)['problems']))

    def test_a_missing_last_label_is_retried_after_the_deadline_within_the_grace(self):
        self.journal.write('nxt_decision', datetime(2026, 11, 12, 19, 31, tzinfo=KST), symbol='111111', passed=False, decision_price=10_000)
        self.quotes[('111111', 'KRX')] = None  # NH drops the 10:00 read
        times = iter([datetime(2026, 11, 13, 10, 0, tzinfo=KST), datetime(2026, 11, 13, 10, 0, 30, tzinfo=KST),
                      datetime(2026, 11, 13, 10, 1, tzinfo=KST), datetime(2026, 11, 13, 10, 1, 30, tzinfo=KST)])
        clock = [next(times)]

        def sleep(_):
            clock[0] = next(times)
            if clock[0].minute == 1:
                self.quotes[('111111', 'KRX')] = krx()
        n.morning(self.read, self.journal, lambda: clock[0], previous_day='20261112', sleep=sleep)
        outcomes = [(o['at'], o.get('late_min')) for o in self.journal.rows('nxt_outcome', '20261113') if o['at'] == '10:00']
        self.assertEqual(outcomes, [('10:00', None)])  # taken at 10:01, inside the 2-minute lateness mark
        self.assertEqual(self.journal.rows('nxt_stopped', '20261113')[0]['time'][11:16], '10:01')

    def test_closed_next_day_or_a_weak_market_blocks_the_buy(self):
        now = datetime(2026, 11, 13, 19, 31, tzinfo=KST)
        book = n.broker(self.read, self.root, clock=lambda: now)
        n.decide(self.read, self.journal, book, now, STRONG, next_open=lambda d: False)
        self.assertIn('market_closed_next_day', self.journal.rows('nxt_decision', '20261113')[0]['reasons'])
        self.assertEqual(book.holdings(), {})

    def test_stop_loss(self):
        now = datetime(2026, 11, 12, 19, 31, tzinfo=KST)
        book = n.broker(self.read, self.root, clock=lambda: now)
        n.decide(self.read, self.journal, book, now, STRONG, next_open=lambda d: True)
        later = datetime(2026, 11, 13, 8, 10, tzinfo=KST)
        self.quotes[('111111', 'NXT')] = nxt(price=10_300, bid=10_290, ask=10_300)  # about -2.2%
        n.exits(n.broker(self.read, self.root, clock=lambda: later), self.journal, later)
        self.assertEqual(self.journal.rows('nxt_exit', '20261113')[0]['reason'], 'stop_loss')


class AuditTests(unittest.TestCase):
    def test_a_running_session_is_reported_not_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            journal = n.Journal(d)
            journal.write('nxt_started', datetime(2026, 11, 12, 15, 35, tzinfo=KST), session='evening')
            self.assertEqual(n.audit(d)['problems'], ['2026-11-12: a session did not stop cleanly'])
            n.mark_running(journal, 'evening', datetime(2026, 11, 12, 15, 35, tzinfo=KST))
            result = n.audit(d)
            self.assertEqual((result['problems'], result['days']['2026-11-12']['running']), ([], 'evening'))
            (Path(d) / 'session.json').write_text('{"pid": 999999999, "session": "evening"}')  # dead process
            self.assertEqual(n.audit(d)['problems'], ['2026-11-12: a session did not stop cleanly'])


class TerminateTests(unittest.TestCase):
    # 11/17 18:11 a SIGTERM restart left the 15:35 evening session without nxt_stopped and the audit flagged it
    def test_a_termination_signal_ends_the_session_with_a_stop_record(self):
        with tempfile.TemporaryDirectory() as d:
            journal = n.Journal(d)
            clock = [datetime(2026, 11, 17, 15, 36, tzinfo=KST)]
            def sleep(seconds):
                n.terminate(15, None)  # launchd's SIGTERM arrives during the pause
                clock[0] += timedelta(seconds=seconds)
            try:
                with mock.patch.object(n, 'macro', return_value={'futures': {}}):
                    n.evening(Read({}), journal, lambda: clock[0], sleep=sleep)
            finally:
                n.TERMINATED.clear()
            stopped = journal.rows('nxt_stopped', '20261117')
            self.assertEqual([(r['session'], r.get('reason')) for r in stopped], [('evening', 'terminated')])
            self.assertLess(clock[0], datetime(2026, 11, 17, 15, 37, tzinfo=KST))  # left at the first pause, not at 19:58
            journal.write('nxt_started', datetime(2026, 11, 17, 18, 11, tzinfo=KST), session='evening')
            journal.write('nxt_stopped', datetime(2026, 11, 17, 19, 58, tzinfo=KST), session='evening')
            result = n.audit(d)
            self.assertEqual((result['problems'], result['days']['2026-11-17']['terminated']), ([], 1))


if __name__ == '__main__':
    unittest.main()


class BootStartTests(unittest.TestCase):
    def test_an_auto_start_runs_only_inside_a_session_window(self):
        # 11/16 the Mac booted at 08:08 and the 07:55 run never came; launchd now also starts it at load
        seen = []
        for when, expected in ((datetime(2026, 11, 16, 8, 9, tzinfo=KST), 'morning'),
                               (datetime(2026, 11, 16, 13, 0, tzinfo=KST), None),
                               (datetime(2026, 11, 16, 16, 0, tzinfo=KST), 'evening'),
                               (datetime(2026, 11, 16, 21, 0, tzinfo=KST), None)):
            class Clock(datetime):
                @classmethod
                def now(cls, tz=None, _at=when):
                    return _at
            with mock.patch.object(n, 'datetime', Clock), mock.patch('sys.argv', ['nxt_virtual.py']), \
                 mock.patch.object(n, 'morning', side_effect=lambda *a, **k: seen.append('morning')), \
                 mock.patch.object(n, 'evening', side_effect=lambda *a, **k: seen.append('evening')), \
                 mock.patch.object(n, 'Journal'), \
                 mock.patch('src.tools.mcp.MCPServerAdapter'), mock.patch('src.config.loader.load_agent_config'):
                before = len(seen)
                n.main()
                self.assertEqual(seen[before:], [expected] if expected else [])
