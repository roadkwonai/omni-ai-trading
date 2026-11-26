"""Candidate-to-entry checks for the isolated watchlist-strength trial."""
from datetime import datetime, timedelta
import math
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
MARKET_AGE, NEWS_AGE, FLOW_AGE = 10, 30, 15
CHASE_LIMITS = {'open_gap_pct': 8.0, 'ask_vs_recent_low_pct': 1.5, 'ask_vs_decision_pct': 0.5}


def finite(value):
    return type(value) in (int, float) and -1e30 <= value <= 1e30


def fresh(stamp, now, minutes, *, market_time=False):
    try:
        if market_time and len(stamp) == 5:
            stamp = f'{now:%Y-%m-%d}T{stamp}'
        at = datetime.fromisoformat(stamp)
        if market_time and at.tzinfo is None:
            at = at.replace(tzinfo=KST)
        return timedelta(0) <= now - at <= timedelta(minutes=minutes)
    except (TypeError, ValueError):
        return False


def review(*, symbol, model_choice, model_fresh, market, index, market_news, stock, timing, now):
    now = now.astimezone(KST)
    reasons = []
    if not model_fresh or not isinstance(model_choice, str):
        reasons.append('strength_model_unavailable')
    elif model_choice == 'WAIT':
        reasons.append('strength_jev_wait')
    elif model_choice != symbol:
        reasons.append('strength_jev_other_symbol')

    market, stock, timing = market or {}, stock or {}, timing or {}
    own = (market.get('index_intraday') or {}).get(index) or {}
    from_open = own.get('from_open_pct')
    recent = own.get('change_30m_pct')
    if recent is None:
        recent = own.get('first_30m_from_open_pct')
    if (index not in ('코스피', '코스닥') or not fresh(market.get('as_of'), now, MARKET_AGE, market_time=True)
            or not fresh(own.get('as_of'), now, MARKET_AGE, market_time=True) or not all(map(finite, (from_open, recent)))):
        reasons.append('strength_market_unavailable')
    elif from_open < 0 or recent < 0:
        reasons.append('strength_market_weak')

    if not any(isinstance(n, dict) and fresh(n.get('published'), now, NEWS_AGE) for n in market_news or []):
        reasons.append('strength_market_news_unverified')
    headlines = stock.get('headlines') or []
    if not any(isinstance(n, dict) and (fresh(n.get('published'), now, NEWS_AGE)
               if 'published' in n else finite(n.get('age_min')) and 0 <= n['age_min'] < NEWS_AGE)
               for n in headlines):
        reasons.append('strength_stock_news_unverified')
    provisional = (stock.get('investor_daily_net_qty') or {}).get('today_provisional') or {}
    flows = [provisional[k] for k in ('foreign', 'institution') if finite(provisional.get(k))]
    if (str(provisional.get('date')) not in (now.strftime('%Y%m%d'), now.date().isoformat()) or not flows
            or not fresh(stock.get('investor_read_at'), now, FLOW_AGE)):
        reasons.append('strength_stock_flow_unverified')
    elif math.fsum(flows) < 0:
        reasons.append('strength_stock_flow_selling')

    if any(not finite(timing.get(k)) for k in CHASE_LIMITS):
        reasons.append('strength_chase_unverified')
    elif any(timing[k] > cap for k, cap in CHASE_LIMITS.items()):
        reasons.append('strength_chase')
    return reasons


def compare(*, baseline, pressure=None, **facts):
    """Compare candidate policies without placing orders or fetching additional data.

    ``baseline`` is the observed current entry gate result. Pressure is a supplied
    boolean signal with a source, matching symbol and two verified timestamps.
    """
    if baseline not in (True, False, None) or type(baseline) not in (bool, type(None)):
        raise ValueError('baseline must be true, false or unknown')
    reasons = review(**facts)
    current_status = 'permit' if baseline is True else 'block' if baseline is False else 'unknown'
    current = {'status': current_status, 'reasons': [] if baseline is True else ['baseline_blocked' if baseline is False else 'baseline_unknown']}
    strict_blocked = [reason for reason in reasons if not (reason.endswith('_unavailable') or reason.endswith('_unverified'))]
    strict_unknown = [reason for reason in reasons if reason not in strict_blocked]
    strict_status = ('block' if baseline is False or strict_blocked else
                     'unknown' if baseline is None or strict_unknown else 'permit')
    strict = {'status': strict_status, 'reasons': ([] if baseline is True else current['reasons']) + reasons}

    now = facts['now']
    valid_pressure = (isinstance(pressure, dict) and pressure.get('symbol') == facts['symbol']
                      and isinstance(pressure.get('source'), str) and bool(pressure['source'].strip())
                      and type(pressure.get('selling')) is bool
                      and fresh(pressure.get('source_at'), now, 5) and fresh(pressure.get('received_at'), now, 5))
    if valid_pressure:
        valid_pressure = datetime.fromisoformat(pressure['source_at']) <= datetime.fromisoformat(pressure['received_at'])
    pressure_value = pressure['selling'] if valid_pressure else None
    market_value = None if 'strength_market_unavailable' in reasons else 'strength_market_weak' in reasons
    model_value = (None if 'strength_model_unavailable' in reasons else
                   facts['model_choice'] == 'WAIT')
    timing = facts.get('timing') or {}
    exceeded = any(finite(timing.get(key)) and timing[key] > limit for key, limit in CHASE_LIMITS.items())
    chase_value = True if exceeded else None if 'strength_chase_unverified' in reasons else False
    weak = True if True in (pressure_value, market_value) else False if pressure_value is False and market_value is False else None
    tests = (model_value, chase_value, weak)
    conditional = False if False in tests else None if None in tests else True
    status = ('block' if baseline is False or conditional is True else
              'unknown' if baseline is None or conditional is None else 'permit')
    conditional_reasons = []
    if baseline is not True:
        conditional_reasons.extend(current['reasons'])
    if conditional is True:
        conditional_reasons.extend(('strength_jev_wait', 'strength_chase'))
        if pressure_value is True:
            conditional_reasons.append('strength_selling_pressure')
        if market_value is True:
            conditional_reasons.append('strength_market_weak')
    elif conditional is None:
        conditional_reasons.append('strength_conditional_unverified')
    return {'current': current, 'strict': strict, 'conditional': {'status': status, 'reasons': conditional_reasons}}
