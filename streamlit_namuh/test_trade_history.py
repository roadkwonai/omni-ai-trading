import sqlite3
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import app


def _history():
    import os
    from pathlib import Path
    import streamlit as st
    import app

    st.session_state['trade_history'] = {'engine': 'vibe', 'code': '005930'}
    app.selected_trade_history(Path(os.environ['TEST_TRADE_HISTORY']), 'vibe')


def test_history_shows_all_matching_fills_and_keeps_chart_link(tmp_path, monkeypatch):
    with sqlite3.connect(tmp_path / 'orders.sqlite3') as db:
        db.execute('CREATE TABLE fills(time TEXT,symbol TEXT,side TEXT,quantity INT,price REAL)')
        db.executemany('INSERT INTO fills VALUES (?,?,?,?,?)', [
            ('2026-11-16T09:00:00+09:00', '005930', 'BUY', 3, 100),
            ('2026-11-16T01:00:00+00:00', '005930', 'SELL', 2, 105),
            ('2026-11-16T10:02:00', '005930', 'BUY', 1, 101),
            ('invalid', '005930', 'SELL', 1, 102),
            ('2026-11-16T10:01:00+09:00', '000660', 'SELL', 5, 200)])
    monkeypatch.setenv('TEST_TRADE_HISTORY', str(tmp_path))
    with patch.object(app, 'stock_names', return_value={'005930': '삼성전자'}):
        ui = AppTest.from_function(_history).run()
        assert not ui.exception
        assert '삼성전자 (005930) 거래 내역' in ui.markdown[0].value
        assert list(ui.dataframe[0].value['구분']) == ['매도', '매수', '매도', '매수']
        assert list(ui.dataframe[0].value['시각']) == ['시각 미확인', '시각 미확인',
                                                  '2026-11-16 10:00:00', '2026-11-16 09:00:00']
        ui.button[0].click().run()
        assert ui.session_state['pending_chart'] == {'engine': 'vibe', 'code': '005930'}
