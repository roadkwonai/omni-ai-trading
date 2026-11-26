"""Paired Jev judgments for an isolated virtual experiment."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import hmac
import json
import math
import re
from zoneinfo import ZoneInfo

from etf_universe import is_code
from jev_learning_forward import ask, requests_for_frame

KST = ZoneInfo('Asia/Seoul')
ARMS = ('baseline', 'experience')


def _time(value):
    try:
        stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(stamp, datetime) or stamp.utcoffset() is None:
            raise ValueError
        return stamp.astimezone(KST)
    except (TypeError, ValueError):
        raise ValueError('aware timestamp required') from None


def isolated_credentials(credentials, *, independent_quota):
    """Validate separately allocated keys without exposing either value."""
    isolated = credentials.get('JEV_ISOLATED_API_KEY')
    production = credentials.get('JEV_API_KEY')
    if (independent_quota is not True or not isinstance(isolated, str) or not isolated.strip()
            or not isinstance(production, str) or not production.strip() or hmac.compare_digest(isolated, production)):
        raise ValueError('separate isolated key and independent quota required')
    return isolated


def compare(frame, experience, *, isolated_key, production_key, independent_quota, reverse=False,
            judge=ask, clock=None):
    """Evaluate identical prepared inputs once per arm and preserve any partial result.

    The caller owns durable deduplication before invoking this function. The
    returned times are actual response times, never execution times.
    """
    isolated_credentials({'JEV_ISOLATED_API_KEY': isolated_key, 'JEV_API_KEY': production_key},
                         independent_quota=independent_quota)
    if type(reverse) is not bool or not isinstance(frame, dict):
        raise ValueError('invalid frame')
    symbol, market = frame.get('symbol'), frame.get('market')
    digest, model = frame.get('input_digest'), frame.get('model')
    decision_at = _time(frame.get('decision_at'))
    if (not is_code(symbol) or market not in ('KOSPI', 'KOSDAQ') or model != 'jev-1.14.0'
            or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
            or not isinstance(frame.get('capture'), dict) or not isinstance(frame['capture'].get('state'), dict)):
        raise ValueError('invalid source')
    if (not isinstance(experience, dict) or experience.get('research_only') is not True
            or not re.fullmatch(r'[0-9a-f]{64}', str(experience.get('version', '')))
            or not isinstance(experience.get('lessons'), list) or not experience['lessons']
            or any(not isinstance(lesson, dict) or lesson.get('market') != market for lesson in experience['lessons'])):
        raise ValueError('qualified experience unavailable')
    series = frame.get('series')
    if not isinstance(series, dict) or symbol not in series or not 1 <= len(series) <= 8:
        raise ValueError('chart data unavailable')
    for code, values in series.items():
        if not is_code(code) or not isinstance(values, list) or not 1 <= len(values) <= 100:
            raise ValueError('invalid chart')
        previous = None
        for value in values:
            if not isinstance(value, (tuple, list)) or len(value) != 2:
                raise ValueError('invalid chart point')
            at, price = _time(value[0]), value[1]
            if (at + timedelta(minutes=1) > decision_at or previous is not None and at <= previous
                    or type(price) not in (int, float) or not math.isfinite(price) or price <= 0):
                raise ValueError('chart point unavailable at decision')
            previous = at
    args = {'capture': frame['capture'], 'quote': {'symbol': symbol}, 'model': model, 'series': series}
    payloads, image = requests_for_frame(args, experience)
    for payload in payloads.values():
        payload['questions']['candidate']['criteria'][symbol] = (
            f'The recorded chart, news and market evidence support a {market} paper entry in {symbol}.')
    clock = clock or (lambda: datetime.now(KST))
    order = list(ARMS[::-1] if reverse else ARMS)
    result = {'source_input_digest': digest, 'source_decision_at': decision_at.isoformat(),
              'experience_version': experience['version'], 'chart_sha256': hashlib.sha256(image).hexdigest(),
              'track_record_source': 'shared_actual_paper_archive_at_source_decision',
              'order': order, 'arms': {}, 'failed_arm': None, 'error_type': None}
    for arm in order:
        try:
            payload = payloads[arm]
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')
            answered = judge(payload, image, isolated_key)
            decided_at = _time(clock())
            choice, confidence = answered.get('choice'), answered.get('confidence')
            if (choice not in ('WAIT', symbol) or type(confidence) not in (int, float)
                    or not math.isfinite(confidence) or not 0 <= confidence <= 1 or decided_at < decision_at):
                raise ValueError('invalid model response')
            result['arms'][arm] = {**answered, 'decided_at': decided_at.isoformat(),
                                   'input_sha256': hashlib.sha256(encoded).hexdigest()}
        except Exception as exc:
            result.update(failed_arm=arm, error_type=type(exc).__name__)
            break
    return result
