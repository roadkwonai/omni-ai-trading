"""Fail-closed current KRX session check against a fresh NH market snapshot."""
from datetime import datetime, time
import re
from zoneinfo import ZoneInfo


def check_session(now: datetime, snapshot: dict, previous_snapshot: dict | None = None, *,
                  require_advance: bool = True, session_end: time = time(15),
                  future_tolerance_seconds: int = 5) -> dict:
    """Allow an order only when current KRX quotes confirm its permitted session.

    NH's quote time has no date; the caller must fetch both snapshots directly
    and immediately before checking them. This checks the present session, not future holidays.
    """
    def deny(reason):
        return {'allowed': False, 'reason': reason}

    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        return deny('timezone-aware clock required')
    now = now.astimezone(ZoneInfo('Asia/Seoul'))
    if (type(require_advance) is not bool or not isinstance(session_end, time)
            or session_end.tzinfo is not None or not time(9, 10) < session_end <= time(15, 18)
            or type(future_tolerance_seconds) is not int or not 0 <= future_tolerance_seconds <= 5):
        return deny('invalid session policy')
    if now.weekday() >= 5 or not time(9) <= now.time().replace(tzinfo=None) < session_end:
        return deny('outside permitted KRX session')
    if not isinstance(snapshot, dict) or snapshot.get('status') != 'ok':
        return deny('market snapshot unavailable')
    data = snapshot.get('data')
    if not isinstance(data, dict) or data.get('rsp_cd') != '00000':
        return deny('market snapshot invalid')
    quote = data.get('Output_0')
    # KRX short codes are a digit then five digits or capital letters (0023A0, 0183J0 since 2024), as etf_universe.CODE_PATTERN
    if not isinstance(quote, dict) or not re.fullmatch(r'[0-9][0-9A-Z]{5}', str(quote.get('iem_cd') or '')):
        return deny('quote symbol invalid')
    if quote.get('market_status') != '1':
        return deny('KRX regular session not confirmed')
    for key in ('bsop_date', 'stck_bsop_date'):
        if key in quote and quote[key] != now.strftime('%Y%m%d'):
            return deny('quote trading date mismatch')
    try:
        stamp = quote['hoga_bsop_hour']
        if not isinstance(stamp, str) or not re.fullmatch(r'\d{2}:\d{2}:\d{2}', stamp):
            raise ValueError
        observed = datetime.combine(now.date(), time.fromisoformat(stamp), now.tzinfo)
        age = (now - observed).total_seconds()
        if not -future_tolerance_seconds <= age <= 120:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return deny('quote timestamp unavailable or stale')
    if not isinstance(previous_snapshot, dict) or previous_snapshot.get('status') != 'ok':
        return deny('previous market snapshot unavailable')
    previous_data = previous_snapshot.get('data')
    previous = previous_data.get('Output_0') if isinstance(previous_data, dict) else None
    if (not isinstance(previous, dict) or previous_data.get('rsp_cd') != '00000'
            or previous.get('iem_cd') != quote['iem_cd'] or previous.get('market_status') != '1'):
        return deny('previous market snapshot invalid')
    for key in ('bsop_date', 'stck_bsop_date'):
        if key in previous and previous[key] != now.strftime('%Y%m%d'):
            return deny('previous quote trading date mismatch')
    try:
        prior_stamp = previous['hoga_bsop_hour']
        if not isinstance(prior_stamp, str) or not re.fullmatch(r'\d{2}:\d{2}:\d{2}', prior_stamp):
            raise ValueError
        prior = datetime.combine(now.date(), time.fromisoformat(prior_stamp), now.tzinfo)
        if (not -future_tolerance_seconds <= (now - prior).total_seconds() <= 120 or prior > observed
                or (require_advance and prior == observed)):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return deny('market quote did not advance')
    return {'allowed': True, 'reason': 'fresh KRX regular session confirmed'}
