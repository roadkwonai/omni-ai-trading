from datetime import datetime
import json

import pytest

import test_jev_entry_live_trial
from test_jev_entry_live_trial import at
from virtual_paper_broker import VirtualBroker

trial = test_jev_entry_live_trial.trial


class QuoteReader:
    def __init__(self):
        self.calls = []
        self.quote_time = '10:00:29'
        self.override_symbol = None

    def call_tool(self, tool, params):
        self.calls.append((tool, params))
        return {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': {
            'iem_cd': self.override_symbol or params['stock_code'], 'market_status': '1',
            'hoga_bsop_hour': self.quote_time, 'bidp': 9990, 'askp': 10000,
        }}}


def test_refresh_uses_one_quote_and_a_memory_book(trial):
    from jev_entry_live_feed import IsolatedFeed

    frame_path, _ = trial
    frame = json.loads(frame_path.read_text())
    reader = QuoteReader()
    broker = VirtualBroker(':memory:', reader, cash=25000, limits={'held_krw': 20000},
                           clock=lambda: datetime.fromisoformat(at('10:00:30')))
    try:
        source = IsolatedFeed(broker, reader)
        assert source.check(frame)['available_cash'] == 25000
        assert reader.calls == []
        refreshed = source.refresh(frame)
        assert refreshed['quote'] == {'at': at('10:00:29'), 'bid': 9990, 'ask': 10000}
        assert refreshed['check']['held_limit'] == 20000
        assert refreshed['cycle_id'] == frame['cycle_id']
        assert reader.calls == [('get_stock_price', {'stock_code': frame['symbol']})]
        assert broker.db.execute('SELECT count(*) FROM orders').fetchone() == (0,)
    finally:
        broker.db.close()


@pytest.mark.parametrize(('quote_time', 'symbol'), [
    ('10:00:24', None), ('10:00:31', None), ('10:00:29', '000660'),
])
def test_feed_rejects_stale_future_or_wrong_symbol_quote(trial, quote_time, symbol):
    from jev_entry_live_feed import IsolatedFeed

    frame_path, _ = trial
    reader = QuoteReader()
    reader.quote_time, reader.override_symbol = quote_time, symbol
    broker = VirtualBroker(':memory:', reader, cash=25000, limits={'held_krw': 20000},
                           clock=lambda: datetime.fromisoformat(at('10:00:30')))
    try:
        with pytest.raises(ValueError):
            IsolatedFeed(broker, reader).refresh(json.loads(frame_path.read_text()))
        assert len(reader.calls) == 1
    finally:
        broker.db.close()


def test_trial_collects_after_jev_without_a_second_quote_or_model_call(trial):
    from jev_entry_live_feed import IsolatedFeed
    from jev_entry_live_trial import collect

    reader = QuoteReader()
    now = datetime.fromisoformat(at('10:00:25'))
    broker = VirtualBroker(':memory:', reader, cash=25000, limits={'held_krw': 20000}, clock=lambda: now)
    calls = []

    def post(payload, key, timeout):
        nonlocal now
        assert reader.calls == []
        calls.append(key)
        now = datetime.fromisoformat(at('10:00:30'))
        return {'model': 'jev-1.14.0', 'answers': {'candidate': {
            'type': 'choice', 'choice': 'BUY', 'confidence': 0.9,
            'probabilities': {'BUY': 0.95, 'WAIT': 0.05},
        }}}

    try:
        result = collect(*trial, 'research-key', 'production-key', True, post=post, clock=lambda: now,
                         feed=IsolatedFeed(broker, reader))
        assert result['comparison']['counts']['modeled'] == 1
        assert result['evidence'][0]['remaining_room'] == 20000
        assert calls == ['research-key']
        assert len(reader.calls) == 1
    finally:
        broker.db.close()
