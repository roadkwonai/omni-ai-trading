import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from etf_regime import classify_regime

KST = ZoneInfo('Asia/Seoul')


def data():
    now = datetime(2026, 11, 11, 11, 0, 30, tzinfo=KST)
    minute = []
    for index in range(10):
        price = 100 + index * 0.05
        minute.append({'time': (now.replace(second=0) - timedelta(minutes=10-index)).isoformat(),
                       'open': price, 'high': price + 0.05, 'low': price - 0.05,
                       'close': price + 0.01, 'volume': 100})
    daily = []
    at = now.replace(hour=0, minute=0, second=0) - timedelta(days=1)
    while len(daily) < 20:
        if at.weekday() < 5:
            daily.append({'time': at.isoformat(), 'open': 100, 'high': 102, 'low': 99,
                          'close': 101 if len(daily) < 5 else 100, 'volume': 1000})
        at -= timedelta(days=1)
    return now, minute, list(reversed(daily))


class RegimeTests(unittest.TestCase):
    def test_intraday_and_swing_candidate_are_research_only(self):
        now, minute, daily = data()
        minute[-1]['high'] = 102
        result = classify_regime(now, minute, daily)
        self.assertEqual(result['mode'], 'intraday')
        self.assertEqual(result['purpose'], 'research_only')
        self.assertFalse(result['calibrated_for_leverage'])
        now, minute, daily = data()
        for row in minute:
            row.update(open=100, high=100.1, low=99.9, close=100.05)
        minute[-1]['close'] = 100.06
        self.assertEqual(classify_regime(now, minute, daily)['mode'], 'swing_candidate')

    def test_missing_future_stale_duplicate_and_invalid_data_wait(self):
        now, minute, daily = data()
        invalid = []
        invalid.append((now, minute[:-1], daily))
        later = [dict(x) for x in minute]; later[-1]['time'] = now.replace(second=0).isoformat()
        invalid.append((now, later, daily))
        stale = [dict(x) for x in minute]
        for row in stale:
            row['time'] = (datetime.fromisoformat(row['time']) - timedelta(minutes=10)).isoformat()
        invalid.append((now, stale, daily))
        duplicate = [dict(x) for x in minute]; duplicate[-1]['time'] = duplicate[-2]['time']
        invalid.append((now, duplicate, daily))
        zero = [dict(x) for x in minute]; zero[-1]['volume'] = 0
        invalid.append((now, zero, daily))
        gap = [dict(x) for x in minute]; del gap[4]
        invalid.append((now, gap, daily))
        current_day = [dict(x) for x in daily]; current_day[-1]['time'] = now.replace(hour=0, minute=0, second=0).isoformat()
        invalid.append((now, minute, current_day))
        invalid.append((now.replace(tzinfo=None), minute, daily))
        for current, minutes, days in invalid:
            with self.subTest(current=current, minutes=len(minutes)):
                self.assertEqual(classify_regime(current, minutes, days)['mode'], 'wait')

    def test_stopped_and_inconsistent_market_data_wait(self):
        now, minute, daily = data()
        minute[-1]['close'] = float('nan')
        self.assertEqual(classify_regime(now, minute, daily)['mode'], 'wait')
        now, minute, daily = data()
        for row in minute:
            row.update(open=100, high=100, low=100, close=100)
        self.assertEqual(classify_regime(now, minute, daily)['mode'], 'wait')
        now, minute, daily = data()
        daily[-1]['high'] = 90
        self.assertEqual(classify_regime(now, minute, daily)['mode'], 'wait')


if __name__ == '__main__':
    unittest.main()
