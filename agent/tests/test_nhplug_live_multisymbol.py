"""LiveOrderGuard with several symbols: per-symbol ledgers, account-wide protections unchanged."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.tools.nhplug_live_guard import LiveOrderBlocked, LiveOrderGuard

A = '12345678901'
NOW = datetime(2026, 11, 6, 11, 0, 10, tzinfo=ZoneInfo('Asia/Seoul'))
SYMBOLS = ('114800', '005930', '069500')
ACCOUNTS = {'rsp_cd': '00000', 'Output_0': [{'acct_no': A, 'acct_type': '01'}]}
PNL = {'rsp_cd': '00166', 'rsp_msg': '조회가 완료되었습니다.', 'Output_0': {'sby_pls_amt': 0, 'sdr_xps': 0, 'eal_pls_amt': 0}}
HISTORY = {'_pagination_complete': True, '_account': A, '_day': '20261106', 'rsp_cd': '00166',
           'rsp_msg': '조회가 완료되었습니다.', 'Output_0': []}
SUMMARY = {'orr_pbl_amt1': 1_000_000, 'tot_aet_amt': 1_000_000, 'tot_byn_amt': 0}


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_HOME', str(tmp_path / 'runtime'))


def order(symbol, price):
    return {'act_no': A, 'iem_cd': symbol, 'orr_qty': 1, 'orr_pr': price, 'nmn_pr_tp_cd': '01', 'rmt_mkt_cd': 'KRX',
            'sor_mkt_sli_yn': 'N', 'orr_cnd_dit_cd': '00', 'ssl_nmn_pr_dit_cd': '00'}


def balance(holdings=(), **summary):
    return {'_pagination_complete': True, '_account': A, 'rsp_cd': '00166', 'rsp_msg': '조회가 완료되었습니다.',
            'Output_0': {**SUMMARY, **summary}, 'Output_1': [{'iem_cd': c, 'rsdl_qty': q} for c, q in holdings]}


def quote(symbol, clock, ask, bid=None):
    return {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': {'iem_cd': symbol, 'market_status': '1',
                                                                      'hoga_bsop_hour': clock, 'askp': ask, 'bidp': bid or ask}}}


def sellable(symbol, qty=1):
    return {'_account': A, '_symbol': symbol, 'rsp_cd': '00000', 'sll_pbl_qty': qty}


def reserve(g, op, fields, **kwargs):
    symbol = fields['iem_cd']
    options = {'now': NOW, 'accounts': ACCOUNTS, 'history': HISTORY, 'pnl': PNL, 'balance': balance(),
               'previous_quote': quote(symbol, '11:00:07', fields.get('orr_pr', 1)),
               'quote': quote(symbol, '11:00:09', fields.get('orr_pr', 1))}
    options.update(kwargs)
    return g.reserve(op, fields, **options)


def filled(g, fingerprint, number, symbol, side='현금매수', qty=1, history=HISTORY):
    word = '매수' if side == '현금매수' else '매도'
    g.acknowledge(fingerprint, {'rsp_cd': '00048' if word == '매수' else '00047', 'rsp_msg': f'{word}주문이 완료되었습니다',
                                'Output_0': {'mkt_orr_no': number}})
    row = {'itg_orr_no': number, 'iem_cd': symbol, 'orr_qty': 1, 'tot_cns_qty': qty, 'ny_cns_qty': 0,
           'can_qty': 1 - qty, 'sby_dit_cd_nm': side}
    return g.reconcile(fingerprint, {**history, 'Output_0': [row]})


def test_default_guard_still_only_orders_114800(tmp_path):
    with pytest.raises(LiveOrderBlocked, match='symbol'):
        reserve(LiveOrderGuard(A, tmp_path), 'krstockOrderCashBuy', order('005930', 50_000))
    for bad in ('005930', (), ('5930',), ('005930', None)):
        with pytest.raises(LiveOrderBlocked, match='symbols'):
            LiveOrderGuard(A, tmp_path, symbols=bad)
    with pytest.raises(LiveOrderBlocked, match='symbol'):
        reserve(LiveOrderGuard(A, tmp_path, symbols=SYMBOLS), 'krstockOrderCashBuy', order('035420', 9_000))


def test_ownership_and_broker_balance_are_reconciled_per_symbol(tmp_path):
    g = LiveOrderGuard(A, tmp_path, symbols=SYMBOLS)
    buy = reserve(g, 'krstockOrderCashBuy', order('005930', 50_000))
    assert filled(g, buy, 42, '005930') == {'filled': 1, 'cancelled': 0}
    assert (g.owned('005930'), g.owned('069500'), g.owned('114800')) == (1, 0, 0)
    held = balance([('005930', 1), ('069500', 3)])
    # The broker holds 069500 (the user's own shares) but this ledger never bought it: no sell.
    with pytest.raises(LiveOrderBlocked, match='owned'):
        reserve(g, 'krstockOrderCashSell', order('069500', 30_000), balance=held, sellable=sellable('069500', 3))
    # Sellable quantity read for another symbol is not proof for this one.
    with pytest.raises(LiveOrderBlocked, match='owned'):
        reserve(g, 'krstockOrderCashSell', order('005930', 50_000), balance=held, sellable=sellable('069500', 3))
    # The ledger owns 005930 but the broker balance shows none: no sell.
    with pytest.raises(LiveOrderBlocked, match='owned'):
        reserve(g, 'krstockOrderCashSell', order('005930', 50_000), balance=balance([('069500', 3)]), sellable=sellable('005930'))
    sell = reserve(g, 'krstockOrderCashSell', order('005930', 50_000), balance=held, sellable=sellable('005930'))
    filled(g, sell, 43, '005930', '현금매도')
    assert g.owned('005930') == 0


def test_adoption_is_per_symbol(tmp_path):
    g = LiveOrderGuard(A, tmp_path, symbols=SYMBOLS)
    held = balance([('005930', 2), ('114800', 1)])
    with pytest.raises(LiveOrderBlocked, match='match'):
        g.adopt_existing(now=NOW, balance=held, sellable=sellable('005930', 2), quantity=1, symbol='005930')
    with pytest.raises(LiveOrderBlocked, match='sellable'):
        g.adopt_existing(now=NOW, balance=held, sellable=sellable('114800', 1), quantity=2, symbol='005930')
    with pytest.raises(LiveOrderBlocked, match='symbol'):
        g.adopt_existing(now=NOW, balance=balance([('035420', 1)]), sellable=sellable('035420'), quantity=1, symbol='035420')
    g.adopt_existing(now=NOW, balance=held, sellable=sellable('005930', 2), quantity=2, symbol='005930')
    assert (g.owned('005930'), g.owned('114800')) == (2, 0)
    g.adopt_existing(now=NOW, balance=held, sellable=sellable('114800'), quantity=1)  # the old default symbol
    assert (g.owned('005930'), g.owned('114800')) == (2, 1)


def test_adopted_outstanding_buy_keeps_its_symbol_and_blocks_other_symbols(tmp_path):
    g = LiveOrderGuard(A, tmp_path, symbols=SYMBOLS)
    row = {'itg_orr_no': 77, 'iem_cd': '069500', 'orr_qty': 1, 'tot_cns_qty': 0, 'ny_cns_qty': 1, 'can_qty': 0,
           'sby_dit_cd_nm': '현금매수', 'orr_rjt_rsn_cd_nm': '정상'}
    history = {**HISTORY, 'Output_0': [row]}
    intent = g.adopt_order(now=NOW, history=history, order_number=77)
    # Unresolved order on 069500 blocks a submission on any symbol.
    g2 = LiveOrderGuard(A, tmp_path, symbols=SYMBOLS)
    with pytest.raises(LiveOrderBlocked, match='outstanding|unresolved'):
        reserve(g2, 'krstockOrderCashBuy', order('005930', 50_000), history=history)
    with pytest.raises(LiveOrderBlocked, match='unresolved'):
        reserve(g2, 'krstockOrderCashBuy', order('005930', 50_000))
    # A cancellation must name the pending order's own symbol.
    cancel = {'act_no': A, 'iem_cd': '005930', 'org_mkt_orr_no': 77, 'all_pat_dit_cd': '1'}
    with pytest.raises(LiveOrderBlocked, match='pending'):
        reserve(g2, 'krstockOrderCancel', cancel, history=history)
    cid = reserve(g2, 'krstockOrderCancel', {**cancel, 'iem_cd': '069500'}, history=history)
    g2.acknowledge(cid, {'rsp_cd': '00192', 'rsp_msg': '취소주문이 완료되었습니다', 'Output_0': {'mkt_orr_no': 78}})
    done = {**HISTORY, 'Output_0': [{**row, 'ny_cns_qty': 0, 'can_qty': 1}]}
    assert g2.reconcile(intent, done) == {'filled': 0, 'cancelled': 1}
    assert g2.owned('069500') == 0


def test_daily_loss_latch_blocks_buys_of_every_symbol_but_not_sells(tmp_path):
    g = LiveOrderGuard(A, tmp_path, symbols=SYMBOLS)
    g.adopt_existing(now=NOW, balance=balance([('005930', 1)]), sellable=sellable('005930'), symbol='005930')
    loss = {**PNL, 'Output_0': {'sby_pls_amt': -15_000, 'sdr_xps': 100, 'eal_pls_amt': -4_900}}
    with pytest.raises(LiveOrderBlocked, match='latch'):
        reserve(g, 'krstockOrderCashBuy', order('069500', 30_000), pnl=loss)
    for symbol in SYMBOLS:  # the latch holds for the rest of the day, even once losses recover
        with pytest.raises(LiveOrderBlocked, match='latch'):
            reserve(LiveOrderGuard(A, tmp_path, symbols=SYMBOLS), 'krstockOrderCashBuy', order(symbol, 9_000))
    assert reserve(g, 'krstockOrderCashSell', order('005930', 50_000), pnl=loss,
                   balance=balance([('005930', 1)]), sellable=sellable('005930'))


def test_price_and_capital_limits_hold_for_any_symbol(tmp_path):
    g = LiveOrderGuard(A, tmp_path, symbols=SYMBOLS)
    with pytest.raises(LiveOrderBlocked, match='price'):
        reserve(g, 'krstockOrderCashBuy', order('005930', 2_000_001),
                balance=balance(orr_pbl_amt1=3_000_000, tot_aet_amt=3_000_000))
    with pytest.raises(LiveOrderBlocked, match='capital'):  # one share above the 1,000,000 KRW capital
        reserve(g, 'krstockOrderCashBuy', order('005930', 1_000_001),
                balance=balance(orr_pbl_amt1=3_000_000, tot_aet_amt=3_000_000))
    with pytest.raises(LiveOrderBlocked, match='capital'):  # other holdings already use the capital
        reserve(g, 'krstockOrderCashBuy', order('005930', 60_000), balance=balance([('069500', 1)], tot_byn_amt=950_000))
    with pytest.raises(LiveOrderBlocked, match='ORDER_QTY_MAX'):  # beyond the hard share ceiling
        reserve(g, 'krstockOrderCashBuy', {**order('005930', 60_000), 'orr_qty': 1_001})
    with pytest.raises(LiveOrderBlocked, match='capital'):  # price x quantity counts against the capital
        reserve(g, 'krstockOrderCashBuy', {**order('005930', 60_000), 'orr_qty': 17}, balance=balance(orr_pbl_amt1=3_000_000, tot_aet_amt=3_000_000))
    with pytest.raises(LiveOrderBlocked, match='capital'):  # the account's own capital (500,000) is tighter
        reserve(g, 'krstockOrderCashBuy', {**order('005930', 60_000), 'orr_qty': 9}, capital=500_000,
                balance=balance(orr_pbl_amt1=3_000_000, tot_aet_amt=3_000_000))
    with pytest.raises(LiveOrderBlocked, match='quote symbol'):  # a quote of another symbol cannot price this order
        reserve(g, 'krstockOrderCashBuy', order('005930', 60_000), quote=quote('069500', '11:00:09', 60_000))
    assert reserve(g, 'krstockOrderCashBuy', {**order('005930', 60_000), 'orr_qty': 2}, capital=500_000)  # several shares within limits


def test_no_daily_buy_count_but_at_most_five_new_symbols_a_day(tmp_path):
    codes = ('005930', '069500', '122630', '229200', '251340', '000660')
    g = LiveOrderGuard(A, tmp_path, symbols=codes)
    for number, code in enumerate(codes[:5], 1):
        filled(g, reserve(g, 'krstockOrderCashBuy', order(code, 9_000)), number, code)
    with pytest.raises(LiveOrderBlocked, match='new symbol'):
        reserve(g, 'krstockOrderCashBuy', order('000660', 9_000))
    # buying a symbol already bought today again is not a new symbol, however many buys there were
    filled(g, reserve(g, 'krstockOrderCashBuy', order('005930', 9_010)), 6, '005930')
    filled(g, reserve(g, 'krstockOrderCashBuy', order('005930', 9_020)), 7, '005930')
    assert g.owned('005930') == 3
    # a new day starts the count again
    tomorrow = NOW.replace(day=9)
    history = {**HISTORY, '_day': '20261109'}
    assert reserve(g, 'krstockOrderCashBuy', order('000660', 9_000), now=tomorrow, history=history,
                   previous_quote=quote('000660', '11:00:07', 9_000), quote=quote('000660', '11:00:09', 9_000))


def test_sell_needs_its_own_bid_and_a_limit_near_it(tmp_path):
    g = LiveOrderGuard(A, tmp_path, symbols=SYMBOLS)
    held = balance([('005930', 1)])
    g.adopt_existing(now=NOW, balance=held, sellable=sellable('005930'), symbol='005930')
    near = quote('005930', '11:00:09', 50_100, 50_000)
    with pytest.raises(LiveOrderBlocked, match='too far below'):  # 48,400 < 50,000 x 97%
        reserve(g, 'krstockOrderCashSell', order('005930', 48_400), balance=held, sellable=sellable('005930'), quote=near)
    with pytest.raises(LiveOrderBlocked, match='bid unavailable'):
        reserve(g, 'krstockOrderCashSell', order('005930', 50_000), balance=held, sellable=sellable('005930'),
                quote=quote('069500', '11:00:09', 50_100, 50_000))
    # above the account's buy cap is fine for a sell: a higher limit only sells dearer
    assert reserve(g, 'krstockOrderCashSell', order('005930', 48_500), balance=held, sellable=sellable('005930'), quote=near)


@pytest.mark.parametrize('stamp,allowed', [
    ('2026-11-06T08:59:59+09:00', False), ('2026-11-06T09:00:00+09:00', True),
    ('2026-11-06T09:05:00+09:00', True), ('2026-11-06T09:10:00+09:00', True), ('2026-11-06T15:00:00+09:00', True),
    ('2026-11-06T15:05:00+09:00', True), ('2026-11-06T15:17:59+09:00', True),
    ('2026-11-06T15:18:00+09:00', False), ('2026-11-07T11:00:00+09:00', False),
])
def test_live_sell_session_respects_flatten_window(tmp_path, stamp, allowed):
    at = datetime.fromisoformat(stamp)
    g = LiveOrderGuard(A, tmp_path, symbols=SYMBOLS)
    held = balance([('005930', 1)])
    g.adopt_existing(now=at, balance=held, sellable=sellable('005930'), symbol='005930')
    current = quote('005930', at.strftime('%H:%M:%S'), 50_000)
    options = dict(now=at, history={**HISTORY, '_day': at.strftime('%Y%m%d')}, balance=held,
                   sellable=sellable('005930'), quote=current, previous_quote=current)
    if allowed:
        assert reserve(g, 'krstockOrderCashSell', order('005930', 50_000), **options)
    else:
        with pytest.raises(LiveOrderBlocked, match='market data stale or market closed'):
            reserve(g, 'krstockOrderCashSell', order('005930', 50_000), **options)


@pytest.mark.parametrize('kind', ['closed', 'old', 'future', 'date', 'previous_date', 'different_symbol', 'reversed'])
def test_live_sell_never_uses_unverified_quotes(tmp_path, kind):
    g = LiveOrderGuard(A, tmp_path, symbols=SYMBOLS)
    held = balance([('005930', 1)])
    g.adopt_existing(now=NOW, balance=held, sellable=sellable('005930'), symbol='005930')
    previous = quote('005930', '11:00:07', 50_000)
    current = quote('005930', '11:00:09', 50_000)
    q = current['data']['Output_0']
    if kind == 'closed': q['market_status'] = '0'
    if kind == 'old': q['hoga_bsop_hour'] = '10:58:09'
    if kind == 'future': q['hoga_bsop_hour'] = '11:00:16'  # beyond the 5 s clock-skew tolerance (1~2 s ahead is normal NH)
    if kind == 'date': q['bsop_date'] = '20261105'
    if kind == 'previous_date': previous['data']['Output_0']['bsop_date'] = '20261105'
    if kind == 'different_symbol': q['iem_cd'] = '069500'
    if kind == 'reversed': previous['data']['Output_0']['hoga_bsop_hour'] = '11:00:10'
    with pytest.raises(LiveOrderBlocked):
        reserve(g, 'krstockOrderCashSell', order('005930', 50_000), balance=held,
                sellable=sellable('005930'), quote=current, previous_quote=previous)
    with g._db() as db:
        assert db.execute('SELECT count(*) FROM live_intents').fetchone()[0] == 0


def test_uncertain_order_on_one_symbol_blocks_all_symbols(tmp_path):
    g = LiveOrderGuard(A, tmp_path, symbols=SYMBOLS)
    g.adopt_existing(now=NOW, balance=balance([('069500', 1)]), sellable=sellable('069500'), symbol='069500')
    buy = reserve(g, 'krstockOrderCashBuy', order('005930', 50_000))
    with pytest.raises(LiveOrderBlocked, match='uncertain'):
        g.acknowledge(buy, {})  # transport outcome unknown: stays uncertain
    with pytest.raises(LiveOrderBlocked, match='unresolved'):
        reserve(LiveOrderGuard(A, tmp_path, symbols=SYMBOLS), 'krstockOrderCashSell', order('069500', 30_000),
                balance=balance([('069500', 1)]), sellable=sellable('069500'))


def test_sell_replacement_is_scoped_to_its_symbol(tmp_path):
    g = LiveOrderGuard(A, tmp_path, symbols=SYMBOLS)
    held = balance([('005930', 1), ('069500', 1)])
    for symbol in ('005930', '069500'):
        g.adopt_existing(now=NOW, balance=held, sellable=sellable(symbol), symbol=symbol)
    sell = reserve(g, 'krstockOrderCashSell', order('005930', 50_000), balance=held, sellable=sellable('005930'))
    assert filled(g, sell, 50, '005930', '현금매도', qty=0) == {'filled': 0, 'cancelled': 1}
    # A cancelled 005930 sell does not demand a replacement for 069500 ...
    other = reserve(g, 'krstockOrderCashSell', order('069500', 30_000), balance=held, sellable=sellable('069500'))
    filled(g, other, 51, '069500', '현금매도')
    # ... still demands one for 005930, and cannot be used as a 069500 replacement.
    with pytest.raises(LiveOrderBlocked, match='replacement'):
        reserve(g, 'krstockOrderCashSell', order('005930', 49_900), balance=held, sellable=sellable('005930'))
    with pytest.raises(LiveOrderBlocked, match='replacement|owned'):
        reserve(g, 'krstockOrderCashSell', order('069500', 30_000), balance=held, sellable=sellable('069500'), replacement_of=sell)
    assert reserve(g, 'krstockOrderCashSell', order('005930', 49_900), balance=held, sellable=sellable('005930'), replacement_of=sell)


def test_multi_share_buy_sell_and_partial_cancel(tmp_path):
    g = LiveOrderGuard(A, tmp_path, symbols=SYMBOLS)
    buy = reserve(g, 'krstockOrderCashBuy', {**order('069500', 80_000), 'orr_qty': 3})
    g.acknowledge(buy, {'rsp_cd': '00048', 'rsp_msg': '매수주문이 완료되었습니다', 'Output_0': {'mkt_orr_no': 91}})
    assert g.reconcile(buy, {**HISTORY, 'Output_0': [{'itg_orr_no': 91, 'iem_cd': '069500', 'orr_qty': 3, 'tot_cns_qty': 3,
                                                      'ny_cns_qty': 0, 'can_qty': 0, 'sby_dit_cd_nm': '현금매수'}]}) == {'filled': 3, 'cancelled': 0}
    assert g.owned('069500') == 3
    held = balance([('069500', 3)])
    with pytest.raises(LiveOrderBlocked, match='sellable'):  # more than this ledger owns
        reserve(g, 'krstockOrderCashSell', {**order('069500', 80_000), 'orr_qty': 4}, balance=held, sellable=sellable('069500', 3))
    sell = reserve(g, 'krstockOrderCashSell', {**order('069500', 80_000), 'orr_qty': 2}, balance=held, sellable=sellable('069500', 3))
    g.acknowledge(sell, {'rsp_cd': '00047', 'rsp_msg': '매도주문이 완료되었습니다', 'Output_0': {'mkt_orr_no': 92}})
    pending = {**HISTORY, 'Output_0': [{'itg_orr_no': 92, 'iem_cd': '069500', 'orr_qty': 2, 'tot_cns_qty': 1, 'ny_cns_qty': 1,
                                        'can_qty': 0, 'sby_dit_cd_nm': '현금매도'}]}
    cancel = reserve(g, 'krstockOrderCancel', {'act_no': A, 'iem_cd': '069500', 'org_mkt_orr_no': 92, 'all_pat_dit_cd': '1'},
                     history=pending)  # a partly filled order's remainder can be cancelled
    assert cancel
    done = {**HISTORY, 'Output_0': [{'itg_orr_no': 92, 'iem_cd': '069500', 'orr_qty': 2, 'tot_cns_qty': 1, 'ny_cns_qty': 0,
                                     'can_qty': 1, 'sby_dit_cd_nm': '현금매도'}]}
    assert g.reconcile(sell, done) == {'filled': 1, 'cancelled': 1}
    assert g.owned('069500') == 2


def test_sell_above_buy_quantity_ceiling_still_requires_all_ownership_checks(tmp_path):
    g = LiveOrderGuard(A, tmp_path, symbols=SYMBOLS)
    with g._db() as db:
        db.execute('INSERT INTO live_adoptions VALUES (?,?,?,?)', (g.scope, '20261106', '069500', 1500))
    fields = {**order('069500', 1000), 'orr_qty': 1500}
    for broker_qty, sellable_qty, requested in ((1500, 1500, 1501), (1499, 1500, 1500), (1500, 1499, 1500)):
        with pytest.raises(LiveOrderBlocked, match='owned and sellable'):
            reserve(g, 'krstockOrderCashSell', {**fields, 'orr_qty': requested},
                    balance=balance([('069500', broker_qty)]), sellable=sellable('069500', sellable_qty))
    assert reserve(g, 'krstockOrderCashSell', fields, balance=balance([('069500', 1500)]),
                   sellable=sellable('069500', 1500))


def test_account_order_and_new_symbol_limits_only_tighten(tmp_path):
    codes = ('005930', '069500', '122630')
    g = LiveOrderGuard(A, tmp_path, symbols=codes)
    rich = balance(orr_pbl_amt1=3_000_000, tot_aet_amt=3_000_000)
    with pytest.raises(LiveOrderBlocked, match='single order'):  # 3 x 60,000 = 180,000 > the account's 150,000 per order
        reserve(g, 'krstockOrderCashBuy', {**order('005930', 60_000), 'orr_qty': 3}, order_max=150_000, balance=rich)
    assert reserve(g, 'krstockOrderCashBuy', {**order('005930', 60_000), 'orr_qty': 2}, order_max=150_000, balance=rich)
    g = LiveOrderGuard(A, tmp_path / 'fresh', symbols=codes)
    with pytest.raises(LiveOrderBlocked, match='capital'):  # a per-order cap above the capital cannot loosen it
        reserve(g, 'krstockOrderCashBuy', {**order('005930', 60_000), 'orr_qty': 17}, order_max=5_000_000, balance=rich)
    filled(g, reserve(g, 'krstockOrderCashBuy', order('005930', 60_000), order_max=150_000, balance=rich, new_symbols=2), 1, '005930')
    filled(g, reserve(g, 'krstockOrderCashBuy', order('069500', 9_000), new_symbols=2), 2, '069500')
    with pytest.raises(LiveOrderBlocked, match='new symbol'):  # the account allows 2 new symbols a day
        reserve(g, 'krstockOrderCashBuy', order('122630', 9_000), new_symbols=2)
    with pytest.raises(LiveOrderBlocked, match='new symbol'):  # 9 cannot raise the approved 5 (3rd symbol is fine, but cap 0 is not)
        reserve(g, 'krstockOrderCashBuy', order('122630', 9_000), new_symbols=0)


def test_daily_loss_latch_uses_the_account_limit_but_never_above_the_approved_one(tmp_path):
    loss = lambda krw: {**PNL, 'Output_0': {'sby_pls_amt': -krw, 'sdr_xps': 0, 'eal_pls_amt': 0}}
    g = LiveOrderGuard(A, tmp_path / 'tight', symbols=SYMBOLS)
    assert g.observe_loss(loss(9_000), NOW, 10_000)['halted'] is False
    assert g.observe_loss(loss(12_000), NOW, 10_000)['halted'] is True  # the account's 10,000 KRW limit
    g = LiveOrderGuard(A, tmp_path / 'wide', symbols=SYMBOLS)
    assert g.observe_loss(loss(19_000), NOW, 50_000)['halted'] is False
    assert g.observe_loss(loss(21_000), NOW, 50_000)['halted'] is True  # 50,000 cannot loosen the approved 20,000
    g = LiveOrderGuard(A, tmp_path / 'buy', symbols=SYMBOLS)
    with pytest.raises(LiveOrderBlocked, match='loss'):
        reserve(g, 'krstockOrderCashBuy', order('005930', 9_000), pnl=loss(6_000), loss_limit=5_000)


def test_a_long_term_account_counts_only_realized_loss_and_fees(tmp_path):
    # user 2026-11-17 "장기 전용 보호": a long-term book's paper loss must not stop its drawdown buying
    g = LiveOrderGuard(A, tmp_path, symbols=SYMBOLS)
    paper_loss = {**PNL, 'Output_0': {'sby_pls_amt': 0, 'sdr_xps': 100, 'eal_pls_amt': -50_000}}
    assert g.observe_loss(paper_loss, NOW, 20_000, realized_only=True)['halted'] is False
    realized = {**PNL, 'Output_0': {'sby_pls_amt': -19_950, 'sdr_xps': 100, 'eal_pls_amt': 0}}
    assert g.observe_loss(realized, NOW, 20_000, realized_only=True)['halted'] is True
    assert LiveOrderGuard(A, tmp_path / 'other', symbols=SYMBOLS).observe_loss(paper_loss, NOW, 20_000)['halted'] is True  # default basis unchanged


# user 2026-11-18: a long-term account (realized_only, set from accounts engine 'longterm') may buy all six engine ETFs on
# its first investment day; the 7th code is one more symbol the guard may order
LONG_TERM = ('379810', '379800', '381180', '449170', '069500', '122630', '005930')
TOMORROW = NOW.replace(day=9)
TOMORROW_HISTORY = {**HISTORY, '_day': '20261109'}


def buy_each(g, codes, start=1, **kwargs):
    """Buy one share of each code and reconcile each buy as filled."""
    history = kwargs.get('history', HISTORY)
    for number, code in enumerate(codes, start):
        filled(g, reserve(g, 'krstockOrderCashBuy', order(code, 9_000), **kwargs), number, code, history=history)


@pytest.mark.parametrize('realized_only, allowed', [(True, 6), (False, 5)])
def test_only_a_long_term_accounts_first_day_may_open_six_new_symbols(tmp_path, realized_only, allowed):
    g = LiveOrderGuard(A, tmp_path, symbols=LONG_TERM)
    buy_each(g, LONG_TERM[:allowed], realized_only=realized_only)
    with pytest.raises(LiveOrderBlocked, match='new symbol'):
        reserve(g, 'krstockOrderCashBuy', order(LONG_TERM[allowed], 9_000), realized_only=realized_only)


def test_any_earlier_buy_ends_a_long_term_accounts_first_day(tmp_path):
    g = LiveOrderGuard(A, tmp_path, symbols=LONG_TERM)
    buy_each(g, LONG_TERM[:1], realized_only=True)  # day one: a single buy
    day_two = {'now': TOMORROW, 'history': TOMORROW_HISTORY, 'realized_only': True}
    buy_each(g, LONG_TERM[1:6], start=2, **day_two)
    with pytest.raises(LiveOrderBlocked, match='new symbol'):
        reserve(g, 'krstockOrderCashBuy', order(LONG_TERM[6], 9_000), **day_two)
    # an earlier buy that never filled still counts: the account already tried to invest
    g = LiveOrderGuard(A, tmp_path / 'unfilled', symbols=LONG_TERM)
    filled(g, reserve(g, 'krstockOrderCashBuy', order(LONG_TERM[0], 9_000), realized_only=True), 1, LONG_TERM[0], qty=0)
    buy_each(g, LONG_TERM[1:6], start=2, **day_two)
    with pytest.raises(LiveOrderBlocked, match='new symbol'):
        reserve(g, 'krstockOrderCashBuy', order(LONG_TERM[6], 9_000), **day_two)


def test_holdings_adopted_on_an_earlier_day_end_a_long_term_accounts_first_day(tmp_path):
    g = LiveOrderGuard(A, tmp_path, symbols=LONG_TERM)
    g.adopt_existing(now=NOW, balance=balance([(LONG_TERM[0], 1)]), sellable=sellable(LONG_TERM[0]), symbol=LONG_TERM[0])
    day_two = {'now': TOMORROW, 'history': TOMORROW_HISTORY, 'realized_only': True}
    buy_each(g, LONG_TERM[1:6], **day_two)
    with pytest.raises(LiveOrderBlocked, match='new symbol'):
        reserve(g, 'krstockOrderCashBuy', order(LONG_TERM[6], 9_000), **day_two)


def test_a_tighter_account_setting_still_binds_a_long_term_accounts_first_day(tmp_path):
    g = LiveOrderGuard(A, tmp_path, symbols=LONG_TERM)
    buy_each(g, LONG_TERM[:3], realized_only=True, new_symbols=3)
    with pytest.raises(LiveOrderBlocked, match='new symbol'):
        reserve(g, 'krstockOrderCashBuy', order(LONG_TERM[3], 9_000), realized_only=True, new_symbols=3)


def test_a_buy_aborted_before_sending_on_an_earlier_day_keeps_the_long_term_first_day(tmp_path):
    """A buy proven never sent (abort_before_send) is no investment, as for today's new-symbol count (review 3 M10)."""
    g = LiveOrderGuard(A, tmp_path, symbols=LONG_TERM)
    g.abort_before_send(reserve(g, 'krstockOrderCashBuy', order(LONG_TERM[0], 9_000), realized_only=True))
    day_two = {'now': TOMORROW, 'history': TOMORROW_HISTORY, 'realized_only': True}
    buy_each(g, LONG_TERM[1:7], start=2, **day_two)  # all six engine ETFs on the real first investment day
    with pytest.raises(LiveOrderBlocked, match='new symbol'):
        reserve(g, 'krstockOrderCashBuy', order(LONG_TERM[0], 9_000), **day_two)  # a 7th is still refused


def test_the_same_sell_again_after_a_filled_round_trip_is_a_new_order(tmp_path):
    """4th review F1 (user approved 2026-11-19): a finished order is not a resend. A stop with the bid back at an
    earlier sale's price was refused as 'duplicate intent' until the bid moved."""
    g = LiveOrderGuard(A, tmp_path, symbols=SYMBOLS)
    held = balance([('005930', 1)])
    filled(g, reserve(g, 'krstockOrderCashBuy', order('005930', 50_000)), 1, '005930')
    filled(g, reserve(g, 'krstockOrderCashSell', order('005930', 50_000), balance=held, sellable=sellable('005930')),
           2, '005930', '현금매도')
    filled(g, reserve(g, 'krstockOrderCashBuy', order('005930', 50_100)), 3, '005930')
    second = reserve(g, 'krstockOrderCashSell', order('005930', 50_000), balance=held, sellable=sellable('005930'))
    assert isinstance(second, str)


def test_the_same_buy_again_after_a_cancelled_unfilled_buy_is_a_new_order(tmp_path):
    g = LiveOrderGuard(A, tmp_path, symbols=SYMBOLS)
    fp = reserve(g, 'krstockOrderCashBuy', order('114800', 1_340))
    g.acknowledge(fp, {'rsp_cd': '00048', 'rsp_msg': '매수주문이 완료되었습니다', 'Output_0': {'mkt_orr_no': 7}})
    row = {'itg_orr_no': 7, 'iem_cd': '114800', 'orr_qty': 1, 'tot_cns_qty': 0, 'ny_cns_qty': 0, 'can_qty': 1,
           'sby_dit_cd_nm': '현금매수'}
    g.reconcile(fp, {**HISTORY, 'Output_0': [row]})
    assert reserve(g, 'krstockOrderCashBuy', order('114800', 1_340)) != fp


def test_the_same_order_while_the_first_is_unresolved_is_still_refused(tmp_path):
    g = LiveOrderGuard(A, tmp_path, symbols=SYMBOLS)
    fp = reserve(g, 'krstockOrderCashBuy', order('114800', 1_340))
    g.acknowledge(fp, {'rsp_cd': '00048', 'rsp_msg': '매수주문이 완료되었습니다', 'Output_0': {'mkt_orr_no': 7}})
    with pytest.raises(LiveOrderBlocked, match='duplicate intent|unresolved'):
        reserve(g, 'krstockOrderCashBuy', order('114800', 1_340))


def test_the_same_buy_after_a_pre_send_abort_is_still_refused(tmp_path):
    """review5: the 'finished order is new' rule (user 2026-11-19) covers filled or cancelled orders only. A buy proven
    never sent stays a duplicate, as before; a sell may still follow its own pre-send abort."""
    g = LiveOrderGuard(A, tmp_path, symbols=SYMBOLS)
    g.abort_before_send(reserve(g, 'krstockOrderCashBuy', order('114800', 1_340)))
    with pytest.raises(LiveOrderBlocked, match='duplicate intent'):
        reserve(g, 'krstockOrderCashBuy', order('114800', 1_340))
