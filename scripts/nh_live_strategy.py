"""Experimental capital-limited inverse rules; computes intent only."""
from decimal import Decimal, InvalidOperation

VERSION = 'inverse-capital-v2'

def number(value):
    if isinstance(value,bool) or not isinstance(value,(str,int,float,Decimal)):
        raise ValueError('invalid market number')
    try: result=Decimal(str(value))
    except InvalidOperation as exc: raise ValueError('invalid market number') from exc
    if not result.is_finite(): raise ValueError('non-finite market number')
    return result

def intent(*, shares, pending, daily_loss, bid, ask, entry=None, closes=(), price_cap=10_000):
    if type(shares) is not int or shares<0 or type(pending) is not int or pending<0:
        return {'action':'BLOCK','reason':'position or orders require reconciliation'}
    loss=number(daily_loss);bid=number(bid);ask=number(ask)
    if loss<0 or bid<=0 or ask<bid: raise ValueError('invalid risk or spread')
    if loss>=20_000:
        return {'action':'CANCEL_THEN_SELL' if pending and shares else 'CANCEL_ORDERS' if pending else 'SELL' if shares else 'STOP', 'reason':'daily account loss limit'}
    if shares*bid>1_000_000:
        return {'action':'CANCEL_THEN_SELL' if pending else 'SELL','reason':'reduce exposure above capital limit'}
    if shares:
        cost=number(entry)
        if cost<=0:raise ValueError('entry unavailable')
        if bid<=cost*Decimal('.98'):
            return {'action':'CANCEL_THEN_SELL' if pending else 'SELL','reason':'position loss reached 2 percent'}
        if bid>=cost*Decimal('1.02'):
            return {'action':'CANCEL_THEN_SELL' if pending else 'SELL','reason':'position profit target'}
        return {'action':'RECONCILE' if pending else 'HOLD','reason':'manage existing position'}
    if pending:return {'action':'RECONCILE','reason':'outstanding orders block entry'}
    prices=[number(p) for p in closes]
    if len(prices)<10 or any(p<=0 for p in prices):return {'action':'WAIT','reason':'insufficient completed bars'}
    if ask>price_cap or (ask-bid)/ask>Decimal('.002'):return {'action':'WAIT','reason':'price cap or spread'}
    fast=sum(prices[-3:])/3;slow=sum(prices[-10:])/10
    if prices[-3]<prices[-2]<prices[-1] and fast>slow:
        return {'action':'BUY','reason':'experimental completed-bar momentum'}
    return {'action':'WAIT','reason':'entry conditions absent'}
