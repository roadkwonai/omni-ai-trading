import unittest

from streamlit.testing.v1 import AppTest


def page():
    import streamlit as st
    import app
    st.write('BOX', app.num_input(st, '보유일', 1, 10, 5, 'days', slider=True))
    st.write('KRW', app.money_input(st, '장 마감 보유 한도', 0, 5_000_000_000, 3_000_000, 'night', '만원', slider=True))


class NumberRangeTests(unittest.TestCase):
    def test_out_of_range_input_is_clamped_with_a_korean_note(self):
        at = AppTest.from_function(page, default_timeout=60).run()
        at.number_input(key='days_n').set_value(99).run()
        self.assertFalse(at.exception)
        self.assertIn('BOX `10`', [m.value for m in at.markdown])
        self.assertTrue(any('허용 범위' in c.value for c in at.caption))
        at.selectbox(key='night_unit').set_value('억원').run()
        at.number_input(key='night_억원_5000000000_n').set_value(1).run()
        self.assertFalse(at.exception)
        self.assertIn('KRW `100000000`', [m.value for m in at.markdown])
        at.number_input(key='night_억원_5000000000_n').set_value(80).run()   # 80억 > 50억
        self.assertIn('KRW `5000000000`', [m.value for m in at.markdown])
        self.assertTrue(any('50억' in c.value and '맞췄습니다' in c.value for c in at.caption))

    def test_a_unit_with_one_allowed_value_shows_only_the_box(self):
        def one_value():
            import streamlit as st
            import app
            st.write('KRW', app.money_input(st, '회당 매수 한도', 10_000, 1_000_000, 1_000_000, 'order', '100만원', slider=True))
        at = AppTest.from_function(one_value, default_timeout=60).run()
        self.assertFalse(at.exception)  # 1만~100만 in 100만원 is just 1: no min == max slider
        self.assertEqual(len(at.slider), 0)
        self.assertIn('KRW `1000000`', [m.value for m in at.markdown])


if __name__ == '__main__':
    unittest.main()
