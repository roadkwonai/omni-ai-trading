import unittest

import settings_view as sv


class SettingsViewTests(unittest.TestCase):
    def test_current_preset_matches_all_values_or_reports_custom(self):
        presets = {'보수적': {'a': 1.0, 'b': 2.0}, '기본': {'a': 3.0, 'b': 4.0}}
        self.assertEqual(sv.current_preset({'a': 3.0, 'b': 4.0}, presets), '기본')
        self.assertEqual(sv.current_preset({'a': 3.0, 'b': 5.0}, presets), '직접 설정')
        self.assertEqual(sv.current_preset({'entry_volume_ratio': 1.2}, {'엄격': 1.5, '기본': 1.2}), '기본')

    def test_card_escapes_text_and_marks_the_active_card(self):
        html = sv.card_html('<b>x</b>', [('한도', '1억 원')], '#22c55e', 'shield', '적용 중', True, 'note & more')
        self.assertIn('&lt;b&gt;x&lt;/b&gt;', html)
        self.assertIn('sv-on', html)
        self.assertIn('note &amp; more', html)
        self.assertNotIn('sv-on', sv.card_html('y', [], badge='선택 가능'))

    def test_account_rows_and_limit_flow_show_the_saved_numbers(self):
        paper = {'kind': 'paper', 'budget': {'first_buy_krw': 1_000_000, 'per_symbol_krw': 3_000_000},
                 'limits': {'held_max_krw': 50_000_000}}
        rows = dict(sv.account_rows(paper))
        self.assertEqual((rows['분할 1회 매수'], rows['보유 금액 한도']), ('100만 원', '5,000만 원'))
        self.assertIn('보유 금액 한도\\n5,000만 원', sv.limits_dot(paper, {'entry_confidence': 0.5}))
        live = {'kind': 'live', 'budget': {'capital_krw': 500_000, 'order_price_max_krw': 300_000, 'daily_loss_krw': 20_000},
                'rules': {'sizing': 'amount', 'tranche_krw': 100_000}, 'limits': {'daily_new_symbols': 3}}
        rows = dict(sv.account_rows(live, '실주문'))
        self.assertEqual((rows['매수 크기(분할 1회)'], rows['하루 새 종목'], rows['미러 실행']), ('10만 원씩', '3개', '실주문'))
        self.assertIn('계좌 손절 설정', rows)
        for text in ('횡보장·하락장', '1% 중 더 좁은 값', '전략이나 시황이 미확인', '낙폭과대', '하루 손실 보호'):
            self.assertIn(text, sv.LIVE_STOP_GUIDE)
        self.assertIn('운용금\\n50만 원', sv.limits_dot(live))


if __name__ == '__main__':
    unittest.main()
