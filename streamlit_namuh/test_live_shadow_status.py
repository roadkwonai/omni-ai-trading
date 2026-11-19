from unittest.mock import patch

import app


def test_shadow_status_distinguishes_holdings_and_estimated_sale_results():
    result = {'open': {'AAA': {'qty': 5, 'last': None}, 'BBB': {'qty': 2, 'last': 1000}},
              'trades': [
                  {'symbol': 'AAA', 'side': 'SELL', 'qty': 2, 'time': '2026-11-16T09:20:00+09:00', 'pnl': 10, 'reason': '손절'},
                  {'symbol': 'AAA', 'side': 'SELL', 'qty': 1, 'time': '2026-11-16T09:10:00+09:00', 'pnl': -10, 'reason': '손절'},
                  {'symbol': 'BBB', 'side': 'BUY', 'qty': 2, 'time': '2026-11-16T09:15:00+09:00'},
                  {'symbol': 'CCC', 'side': 'SELL', 'qty': 3, 'time': '2026-11-16T09:30:00+09:00', 'pnl': -20, 'reason': '실계좌 손절 −2%'},
                  {'symbol': 'DDD', 'side': 'SELL', 'qty': 1, 'time': 'invalid', 'pnl': -10, 'reason': '모의 원장 매도'},
                  {'symbol': 'EEE', 'side': 'SELL', 'qty': 1, 'time': '2026-11-16T09:40:00+09:00', 'pnl': None}]}
    rows = {row['종목'].split(' ')[-1]: row for row in app.shadow_position_rows(result, {'AAA': '종목A'})}
    assert rows['(AAA)']['가상 보유'] == '보유 중'
    assert rows['(AAA)']['기준 종가'] == '미확인'
    assert rows['(AAA)']['최근 가상 매도'] == '익절 2주 · 09:20 외 1건'
    assert rows['(AAA)']['오늘 가상 매도 요약'] == '익절 1건 · 손절 1건'
    assert rows['(BBB)']['기준 종가'] == '1,000원'
    assert rows['(BBB)']['최근 가상 매도'] == '없음'
    assert rows['(CCC)']['가상 보유'] == '미보유'
    assert rows['(CCC)']['최근 가상 매도'] == '손절 3주 · 09:30'
    assert rows['(DDD)']['최근 가상 매도'] == '손실 매도 1주 · 시각 미확인'
    assert rows['(EEE)']['최근 가상 매도'] == '손익 미확인 1주 · 09:40'


def test_shadow_status_empty():
    assert app.shadow_position_rows({'open': {}, 'trades': []}, {}) == []


def test_shadow_view_does_not_turn_missing_price_into_zero_profit():
    result = {'open': {'AAA': {'qty': 5, 'unrealized': None}}, 'trades': [], 'realized': 0,
              'skipped_entries': {}, 'basis': 'engine ledger'}
    with patch.object(app, 'stock_names', return_value={}), patch.object(app.sc, 'row') as cards, \
         patch.object(app.st, 'caption'):
        app.shadow_view(result)
    valuation = next(card for card in cards.call_args.args[0] if card['label'] == '가상 평가손익')
    assert valuation['value'] == '확인 불가'
