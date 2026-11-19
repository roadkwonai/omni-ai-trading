"""Isolated order-boundary prototype. No live transport or network access exists here.

The caller's snapshots are untrusted; this module must never be used for a real
account until a trusted broker-owned boundary constructs and validates them.
"""
from __future__ import annotations

from copy import deepcopy

from src.live.halt import halt_flag_set
from src.tools.nhplug_live_guard import LiveOrderBlocked, LiveOrderGuard


class SimulatedTransport:
    """In-memory fixture; it has no external transport and cannot submit orders."""

    def __init__(self, snapshots, response, *, disconnect=False):
        self.snapshots = deepcopy(snapshots)
        self.response = deepcopy(response)
        self.disconnect = disconnect
        self.calls = []

    def read(self):
        return deepcopy(self.snapshots)

    def send(self, operation, fields):
        self.calls.append((operation, deepcopy(fields)))
        if self.disconnect:
            raise TimeoutError('simulated transport failure')
        return deepcopy(self.response)


class SimulatedGuardedGateway:
    """Exercise guard -> simulated send -> acknowledgement without real writes."""

    def __init__(self, account, root, transport):
        if type(transport) is not SimulatedTransport:
            raise LiveOrderBlocked('only the in-memory simulated transport is supported')
        self.guard = LiveOrderGuard(account, root)
        self._transport = transport

    def submit(self, operation, fields, now):
        snapshots = self._transport.read()
        intent = self.guard.reserve(operation, fields, now=now, **snapshots)
        # A halt after reservation must not send, and the uncertain intent stays latched.
        if halt_flag_set('nhplug'):
            raise LiveOrderBlocked('live kill switch active before simulated send')
        response = self._transport.send(operation, fields)
        self.guard.acknowledge(intent, response)
        return intent

    def reconcile(self, intent):
        return self.guard.reconcile(intent, self._transport.read()['history'])
