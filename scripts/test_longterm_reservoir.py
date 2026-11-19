"""저수지(대기 현금) ETF per book (user 2026-11-19): KOFR, CD금리 or 단기채권, 'auto' by the book's money, one at a time."""
import longterm_live as LL
import vibe_longterm as L
from test_longterm_live import CAPITAL, DAY, PLAN, QUOTES

KOFR, CD, SHORT = '449170', '357870', '272580'


def test_auto_picks_the_cheaper_share_for_a_small_book_and_a_choice_is_kept():
    settings = {'books': {}}
    assert L.reservoir_code(settings, 'actual', L.CAPITAL) == KOFR  # the 1억 virtual book: unchanged
    assert L.reservoir_code(settings, 'live-2', 500_000) == CD  # 50만: half the share price of KOFR
    assert L.reservoir_code({'books': {'live-2': {'reservoir': SHORT}}}, 'live-2', 500_000) == SHORT
    assert L.reservoir_code({'books': {'actual': {'reservoir': CD}}}, 'actual', L.CAPITAL) == CD


def test_a_book_holding_one_reservoir_etf_keeps_it_until_sold_out():
    settings = {'books': {'actual': {'reservoir': CD}}}
    assert L.reservoir_code(settings, 'actual', L.CAPITAL, {KOFR: (43, 112_000)}) == KOFR
    assert L.reservoir_code(settings, 'actual', L.CAPITAL, {KOFR: (0, 112_000)}) == CD  # sold out: the choice applies
    assert L.reservoir_code({'books': {}}, 'live-2', 500_000, {KOFR: 2}) == KOFR  # live holdings are {code: qty}


def test_every_reservoir_etf_is_the_reservoir_and_orders_go_to_the_books_code():
    assert all(L.CODE_KEY[c] == 'RES' for c in (KOFR, CD, SHORT))
    assert L.code_of('RES', CD) == CD and L.code_of('RES') == KOFR and L.code_of('NDX', CD) == L.ASSETS['NDX'][0]
    assert L.clean_book({'reservoir': CD})['reservoir'] == CD and L.clean_book({'reservoir': '005930'})['reservoir'] == 'auto'


def test_send_orders_buy_and_sell_the_books_reservoir_code():
    class Broker:
        def cash(self):
            return 1_000_000
    quotes = {'RES': (58_400, 58_410)}
    sent, error = L.send_orders(Broker(), {'RES': 120_000}, quotes, {}, False, res=CD)
    assert error is None and sent == [{'key': 'RES', 'code': CD, 'side': 'BUY', 'qty': 2, 'price': 58_410}]
    sent, _ = L.send_orders(Broker(), {'RES': -120_000}, quotes, {CD: (1, 58_000)}, False, res=CD)
    assert sent[0]['code'] == CD and sent[0]['qty'] == 1  # never more than held of that code


def test_a_live_account_values_and_orders_its_reservoir_etf():
    held = {CD: 4}
    quotes = {**QUOTES, 'RES': (58_400, 58_410)}
    assert LL.values_of(held, quotes, CD)['RES'] == 4 * 58_400
    orders, info, _ = LL.day_orders(PLAN, {}, {}, held, quotes, CAPITAL, CAPITAL, DAY, res=CD)
    assert info['invested'] >= 4 * 58_400
    assert all(o['code'] == CD for o in orders if o['key'] == 'RES')


def _panel(root, capital):
    import longterm_account_view as A
    A.reservoir_panel('live-2', 't_', root, capital)


def test_the_panel_shows_the_reservoir_in_use_and_saves_a_choice(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import longterm_account_view as A
    monkeypatch.setattr(A, 'nh_price', lambda code: {KOFR: 112_485, CD: 58_410, SHORT: 57_490}[code])
    page = AppTest.from_function(_panel, args=(tmp_path, 500_000), default_timeout=30).run()
    assert not page.exception, page.exception
    table = page.dataframe[0].value
    assert list(table.loc[table[''] == '▶ 지금', '코드']) == [CD]  # 50만 on auto: the CD-rate ETF
    page.radio(key='t_lt_res_live-2').set_value(SHORT).run()
    page.button(key='t_lt_res_save_live-2').click().run()
    assert L.load_settings(tmp_path)['books']['live-2']['reservoir'] == SHORT
    assert L.load_settings(tmp_path)['books']['live-2']['strategy'] == 'engine'  # only the reservoir changed


def test_the_virtual_book_moves_its_kofr_into_the_chosen_reservoir_etf_at_the_next_trade(tmp_path, monkeypatch):
    """User 2026-11-19: '가상 계좌도 CD금리로' while it holds KOFR: the next trade sells the KOFR first and keeps the money
    in the CD-rate ETF; a dry run only says it will."""
    from test_vibe_longterm import Read, decided, krx_calendar  # noqa: F401
    now = decided(tmp_path, monkeypatch).replace(hour=9, minute=40)
    broker = L.broker_for(tmp_path, Read())
    broker.call_tool('call_api', {'operationId': 'krstockOrderCashBuy', 'input': {'iem_cd': KOFR, 'orr_qty': 500, 'orr_pr': 10_000}})
    L.save_settings(tmp_path, {'books': {'actual': {'reservoir': CD}}})
    assert L.trade(tmp_path, Read(), now) == 'planned'
    assert KOFR in L.broker_for(tmp_path, Read()).holdings()  # dry run: nothing sold
    assert any('저수지 ETF 전환' in text and CD in text for _, text in L.read_state(tmp_path)['last_plan']['notes'])
    L.put_state(L.connect(tmp_path), planned_day=None)
    L.save_settings(tmp_path, {'execute': True})
    assert L.trade(tmp_path, Read(), now) == 'traded'
    held = L.broker_for(tmp_path, Read()).holdings()
    assert KOFR not in held and held[CD][0] > 0
    assert L.reservoir_code(L.load_settings(tmp_path), 'actual', L.CAPITAL, held) == CD
