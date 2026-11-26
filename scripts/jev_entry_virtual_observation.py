"""Emit a check event from an in-memory paper book and caller-supplied candidate checks."""

from math import ceil, floor

from jev_entry_replay import timestamp
from virtual_paper_broker import VirtualBroker


def virtual_checkpoint(broker, event):
    """Read the paper book at the injected replay clock without refreshing quotes or placing orders."""
    if type(broker) is not VirtualBroker or any(row[2] for row in broker.db.execute('PRAGMA database_list')):
        raise ValueError('an in-memory VirtualBroker is required')
    if event.get('scope') != 'isolated_paper' or event.get('kind') != 'check':
        raise ValueError('an isolated check event is required')
    now = broker.clock()
    at = timestamp(event.get('at', now.isoformat()), event.get('day'))
    if at is None or at != now:
        raise ValueError('checkpoint must match the virtual clock')
    limits = broker.limits
    if type(limits) is not dict or type(limits.get('held_krw')) is not int or limits['held_krw'] < 0:
        raise ValueError('an explicit virtual holdings limit is required')
    pending_count, reserved = broker.db.execute("""
        SELECT COUNT(*), COALESCE(SUM(CASE WHEN side = 'BUY' THEN (qty - filled - cancelled)*price END), 0)
        FROM orders WHERE qty > filled + cancelled
    """).fetchone()
    exposure = sum(qty * average for qty, average in broker.holdings().values())
    return {
        **event, 'at': at.isoformat(), 'available_cash': max(0, floor(broker.cash()) - reserved),
        'held_limit': limits['held_krw'], 'held_exposure': ceil(exposure),
        'reserved_buy': reserved, 'pending': bool(pending_count),
    }
