from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

import kiwoom_data as k


def test_stock_program_rows_requests_only_current_day_krx():
    now = datetime(2026, 11, 26, 10, 0, tzinfo=k.KST)
    rows = [{'tm': '095900', 'prm_netprps_qty': '-200'}]
    with patch.object(k, 'call', return_value=rows) as call:
        assert k.stock_program_rows('005930', now.date(), now=now) == rows
    call.assert_called_once_with('ka90008', {'amt_qty_tp': '2', 'stk_cd': '005930', 'date': '20261126'},
                                 'stk_tm_prm_trde_trnsn', max_pages=1, timeout=2)


@pytest.mark.parametrize('code,offset', [('005930_NX', 0), ('00593A', 0), ('005930', -1)])
def test_stock_program_rows_rejects_other_venues_and_days(code, offset):
    now = datetime(2026, 11, 26, 10, 0, tzinfo=k.KST)
    with patch.object(k, 'call') as call, pytest.raises(ValueError):
        k.stock_program_rows(code, now.date() + timedelta(days=offset), now=now)
    call.assert_not_called()
