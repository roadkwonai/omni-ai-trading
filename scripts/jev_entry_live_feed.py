"""Read a live NH quote and an independent in-memory virtual account snapshot."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from etf_universe import is_code
from jev_entry_virtual_observation import virtual_checkpoint
from kr_swing_v5_forward import fresh_quote


KST = ZoneInfo('Asia/Seoul')


class IsolatedFeed:
    """Refresh one candidate without placing orders or caching a prior quote."""

    def __init__(self, broker, reader):
        self.broker = broker
        self.reader = reader

    def check(self, frame):
        fields = ('qty', 'rules_permit', 'first_reconciled_at')
        supplied = frame.get('check')
        if not isinstance(supplied, dict):
            raise ValueError('isolated candidate check is unavailable')
        event = {
            **{name: frame[name] for name in ('scope', 'day', 'cycle_id', 'symbol', 'input_digest')},
            **{name: supplied[name] for name in fields if name in supplied},
            'kind': 'check',
        }
        return virtual_checkpoint(self.broker, event)

    def refresh(self, frame):
        symbol = frame['symbol']
        if not is_code(symbol):
            raise ValueError('invalid quote symbol')
        self.check(frame)
        response = self.reader.call_tool('get_stock_price', {'stock_code': symbol})
        if not isinstance(response, dict) or response.get('status') != 'ok':
            raise ValueError('quote unavailable')
        data = response.get('data')
        if not isinstance(data, dict) or not isinstance(data.get('Output_0'), dict):
            raise ValueError('quote unavailable')
        original = data['Output_0']
        clock_text = original.get('hoga_bsop_hour')
        if isinstance(clock_text, str) and len(clock_text) == 6 and clock_text.isascii() and clock_text.isdecimal():
            clock_text = f'{clock_text[:2]}:{clock_text[2:4]}:{clock_text[4:]}'
        if not isinstance(clock_text, str):
            raise ValueError('quote time unavailable')
        observed_now = self.broker.clock()
        if observed_now.utcoffset() is None or observed_now.astimezone(KST).date().isoformat() != frame.get('day'):
            raise ValueError('quote date mismatch')
        payload = {**data, 'Output_0': {**original, 'hoga_bsop_hour': clock_text}}
        quote = fresh_quote(payload, symbol, observed_now)
        observed = datetime.strptime(frame['day'] + clock_text, '%Y-%m-%d%H:%M:%S').replace(tzinfo=KST)
        bid, ask = quote.get('bidp'), quote.get('askp')
        if type(bid) is not int or type(ask) is not int or not 0 < bid <= ask:
            raise ValueError('invalid bid or ask')
        check = self.check(frame)
        checked = datetime.fromisoformat(check['at'])
        if not timedelta(0) <= checked - observed <= timedelta(seconds=5):
            raise ValueError('quote is stale or in the future')
        return {**frame, 'quote': {'at': observed.isoformat(), 'bid': bid, 'ask': ask}, 'check': check}


class _ReadOnly:
    def __init__(self, adapter):
        self.adapter = adapter

    def call_tool(self, name, arguments):
        if name != 'get_stock_price' or set(arguments) != {'stock_code'}:
            raise ValueError('only quote reads are permitted')
        return self.adapter.call_tool(name, arguments)


def nh_readonly_reader():
    from src.config.loader import load_agent_config
    from src.tools.mcp import MCPServerAdapter

    config = load_agent_config().mcp_servers['nhplug_readonly']
    return _ReadOnly(MCPServerAdapter('nhplug_readonly', config))
