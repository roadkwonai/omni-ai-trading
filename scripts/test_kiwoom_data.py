import unittest
from datetime import date, datetime
from unittest.mock import patch

import kiwoom_data as k

KST = k.KST


def row(t, close, prev_diff='+6500'):
    return {'cntr_tm': t, 'open_pric': f'+{close}', 'high_pric': f'+{close}', 'low_pric': f'-{close}',
            'cur_prc': f'+{close}', 'trde_qty': '100', 'pred_pre': prev_diff}


RAW = [row('20261116195900', 195000, '-11000'),   # NXT after-market: dropped, but still gives the previous close
       row('20261116153000', 196800, '-9200'),
       row('20261116090000', 206500, '+500'),
       row('20261116080000', 207000, '+1000'),    # NXT pre-market: dropped
       row('20261113153000', 206000, '0')]        # previous day in the same page: dropped


class DayBarsTests(unittest.TestCase):
    def test_regular_session_oldest_first_and_previous_close(self):
        with patch.object(k, 'call', return_value=RAW) as call:
            got = k.day_bars('005930', date(2026, 11, 16), now=datetime(2026, 11, 17, 9, tzinfo=KST))
        self.assertEqual([b[0].strftime('%H:%M') for b in got['bars']], ['09:00', '15:30'])
        self.assertEqual(got['bars'][0][1:], (206500, 206500, 206500, 206500, 100))  # direction signs stripped
        self.assertEqual(got['prev_close'], 206000)
        self.assertEqual(call.call_args.args[1]['upd_stkpc_tp'], '0')

    def test_finished_days_are_cached_and_today_is_not(self):
        day = date(2026, 11, 16)
        with patch.object(k, 'call', return_value=RAW) as call:
            k.day_bars('005930', day, now=datetime(2026, 11, 16, 11, tzinfo=KST))  # session still open
            k.day_bars('005930', day, now=datetime(2026, 11, 17, 9, tzinfo=KST))
            again = k.day_bars('005930', day, now=datetime(2026, 11, 17, 9, tzinfo=KST))
        self.assertEqual(call.call_count, 2)
        self.assertEqual((len(again['bars']), again['prev_close']), (2, 206000))
        self.assertEqual(again['bars'][0][0], datetime(2026, 11, 16, 9, tzinfo=KST))

    def test_empty_answer_is_not_cached(self):
        with patch.object(k, 'call', return_value=[]) as call:
            for _ in range(2):
                self.assertEqual(k.day_bars('005930', date(2024, 1, 2))['bars'], [])
        self.assertEqual(call.call_count, 2)


if __name__ == '__main__':
    unittest.main()


class CallTests(unittest.TestCase):
    def setUp(self):
        self.tokens = iter(['cached', 'renewed'])
        patcher = patch.object(k, '_token', side_effect=lambda stale=None: next(self.tokens))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_rejected_token_is_renewed_and_the_same_page_retried(self):
        answers = [({}, {'return_code': 3, 'return_msg': 'Token 무효'}), ({}, {'return_code': 0, 'rows': [1, 2]})]
        with patch.object(k, '_post', side_effect=answers) as post:
            self.assertEqual(k.call('ka10037', {}, 'rows', max_pages=1), [1, 2])
        self.assertEqual([c.args[2]['authorization'] for c in post.call_args_list], ['Bearer cached', 'Bearer renewed'])

    def test_other_errors_raise_without_renewing_the_shared_token(self):
        with patch.object(k, '_post', return_value=({}, {'return_code': 5, 'return_msg': 'x'})), self.assertRaises(k.KiwoomError):
            k.call('ka10037', {}, 'rows')
        self.assertEqual(next(self.tokens), 'renewed')  # only the cached token was taken

    def test_pages_follow_continuation_keys(self):
        answers = [({'cont-yn': 'Y', 'next-key': 'n1'}, {'return_code': 0, 'rows': [1]}), ({}, {'return_code': 0, 'rows': [2]})]
        with patch.object(k, '_post', side_effect=answers) as post:
            self.assertEqual(k.call('ka10037', {}, 'rows'), [1, 2])
        self.assertEqual(post.call_args_list[1].args[2]['next-key'], 'n1')

    def test_order_trs_are_refused_before_any_request(self):
        with patch.object(k, '_post') as post, self.assertRaises(k.KiwoomError):
            k.call('kt10000', {}, None)
        post.assert_not_called()
