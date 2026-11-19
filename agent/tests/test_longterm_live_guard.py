"""A long-term live account's daily loss latch counts only today's realized loss and fees (user 2026-11-17 "장기 전용
보호"): accounts.json engine 'longterm' → accounts.live_loss_realized_only → nh_guarded_live.execute → LiveOrderGuard
observe_loss and reserve. Puts scripts/ on the path itself (nh_guarded_live, accounts), so a plain agent run collects it."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import accounts  # noqa: E402
import nh_guarded_live
from src.tools.nhplug_live_guard import LiveOrderBlocked, LiveOrderGuard
import vibe_longterm
from tests.test_nhplug_live_multisymbol import A, ACCOUNTS, HISTORY, NOW, PNL, balance, filled, order, quote, reserve

PAPER_LOSS = {**PNL, 'Output_0': {'sby_pls_amt': 0, 'sdr_xps': 100, 'eal_pls_amt': -50_000}}  # holdings down, nothing sold
REALIZED_LOSS = {**PNL, 'Output_0': {'sby_pls_amt': -19_950, 'sdr_xps': 100, 'eal_pls_amt': 0}}


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_HOME', str(tmp_path / 'runtime'))


def buy(tmp_path, monkeypatch, engine, pnl, guard=None, code='069500'):
    """One guarded buy for an account run by `engine`; returns the orders that reached the (fake) broker transport."""
    record = {'kind': 'live', 'engine': engine, 'hash': accounts.digest(A),
              'budget': {'capital_krw': 1_000_000, 'order_price_max_krw': 2_000_000, 'daily_loss_krw': 20_000}}
    monkeypatch.setattr(accounts, 'load', lambda *a, **k: {'live-2': record})
    snapshots = {'accounts': ACCOUNTS, 'history': HISTORY, 'balance': balance(), 'pnl': pnl,
                 'previous_quote': quote(code, '11:00:07', 9_000), 'quote': quote(code, '11:00:09', 9_000)}
    monkeypatch.setattr(nh_guarded_live, 'collect_snapshots', lambda *a, **k: snapshots)
    monkeypatch.setattr(nh_guarded_live, 'datetime', type('Clock', (), {'now': staticmethod(lambda tz: NOW)}))
    sent = []
    ack = {'rsp_cd': '00048', 'rsp_msg': '매수주문이 완료되었습니다', 'Output_0': {'mkt_orr_no': 42}}
    monkeypatch.setattr(nh_guarded_live, '_transport', lambda credentials, op, fields, **k: sent.append(fields) or ack)
    nh_guarded_live.execute(None, guard or LiveOrderGuard(A, tmp_path / 'guard', symbols={code}), {}, 'krstockOrderCashBuy',
                            order(code, 9_000))
    return sent


def test_a_long_term_accounts_paper_loss_does_not_stop_its_buys(tmp_path, monkeypatch):
    assert len(buy(tmp_path, monkeypatch, 'longterm', PAPER_LOSS)) == 1


def test_a_trading_accounts_paper_loss_latches_its_buys(tmp_path, monkeypatch):
    with pytest.raises(LiveOrderBlocked, match='daily loss latch'):
        buy(tmp_path, monkeypatch, 'jev', PAPER_LOSS)


def test_a_long_term_accounts_realized_loss_and_fees_still_latch(tmp_path, monkeypatch):
    with pytest.raises(LiveOrderBlocked, match='daily loss latch'):
        buy(tmp_path, monkeypatch, 'longterm', REALIZED_LOSS)


# as many codes as the engine has ETFs; numeric stand-ins because check_session refuses 0023A0/0183J0 quotes
ENGINE_ETFS = ['379810', '379800', '381180', '449170', '069500', '122630', '229200'][:len(vibe_longterm.ASSETS)]


@pytest.mark.parametrize('engine', ['longterm', 'jev'])
def test_only_a_long_term_account_buys_every_engine_etf_on_its_first_day(tmp_path, monkeypatch, engine):
    # user 2026-11-18: the long-term engine's first purchase is not split over two days by the 5-new-symbol cap
    guard = LiveOrderGuard(A, tmp_path / 'guard', symbols=set(ENGINE_ETFS))
    for number, code in enumerate(ENGINE_ETFS[:-1], 1):
        filled(guard, reserve(guard, 'krstockOrderCashBuy', order(code, 9_000)), number, code)
    if engine == 'longterm':
        assert len(buy(tmp_path, monkeypatch, engine, PNL, guard, ENGINE_ETFS[-1])) == 1
    else:
        with pytest.raises(LiveOrderBlocked, match='new symbol'):
            buy(tmp_path, monkeypatch, engine, PNL, guard, ENGINE_ETFS[-1])
