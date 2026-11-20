"""Publish research-only ETF regime observations from read-only NH data."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import fcntl
import time as clocktime
from collections import deque
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from etf_regime import classify_regime
from etf_universe import SYMBOLS
from session_times import until_open
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')

KST = ZoneInfo('Asia/Seoul')
SOURCE = 'NH KRX completed recorded bars or read-only minute quote + daily quote'


def _timestamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('timezone required')
    return value.astimezone(KST)


def _empty(symbol, reason):
    return {'symbol': symbol, 'mode': 'wait', 'reason': reason, 'data_quality': 'invalid',
            'purpose': 'research_only', 'calibrated_for_leverage': False,
            'range_10m_pct': None, 'daily_sma_gap_pct': None, 'daily_quote_time': None,
            'minute_source': None, 'minute_data_time': None}


def _latest_decision(path):
    with Path(path).open(encoding='utf-8') as stream:
        rows = [json.loads(line) for line in deque(stream, maxlen=200) if line.strip()]
    return next((row for row in reversed(rows) if isinstance(row, dict) and
                 (row.get('event') == 'decision' or
                  (row.get('event') == 'v2_holding_decision' and isinstance(row.get('market_bars'), dict)
                   and set(SYMBOLS) <= set(row['market_bars'])) or
                  (row.get('event') == 'market_context' and row.get('universe') == 'etf7'
                   and row.get('context') in ('entry', 'holding') and isinstance(row.get('market_bars'), dict)
                   # the runner logs watchlist stock charts beside ETF7 (11-20: an exact match never matched)
                   and set(SYMBOLS) <= set(row['market_bars'])))), None)


def _minute_bars(rows, event_time):
    if not isinstance(rows, list):
        raise ValueError('minute bars unavailable')
    converted = []
    for row in rows:
        raw_time = row['bsop_time']
        if not isinstance(raw_time, str) or len(raw_time) != 6 or not raw_time.isascii() or not raw_time.isdigit():
            raise ValueError('minute time invalid')
        stamp = datetime.strptime(event_time.strftime('%Y%m%d') + raw_time, '%Y%m%d%H%M%S').replace(tzinfo=KST)
        if stamp + timedelta(minutes=2) > event_time:
            raise ValueError('minute not demonstrably complete')
        converted.append({'time': stamp.isoformat(), 'open': row['stck_oprc'], 'high': row['stck_hgpr'],
                          'low': row['stck_lwpr'], 'close': row['stck_prpr'], 'volume': row['vol']})
    return sorted(converted, key=lambda row: row['time'])


def _daily_bars(response, code, now):
    if not isinstance(response, dict) or response.get('status') != 'ok':
        raise ValueError('daily response unavailable')
    data = response.get('data')
    if not isinstance(data, dict) or data.get('rsp_cd') != '00000':
        raise ValueError('daily response rejected')
    header, rows = data.get('Output_0'), data.get('Output_1')
    if not isinstance(header, dict) or header.get('iem_cd') != code or not isinstance(rows, list):
        raise ValueError('daily response identity unavailable')
    day_text, time_text = header['qry_date'], header['qry_time']
    if (not isinstance(day_text, str) or len(day_text) != 8 or not day_text.isascii() or not day_text.isdigit()
            or not isinstance(time_text, str) or len(time_text) != 6 or not time_text.isascii() or not time_text.isdigit()):
        raise ValueError('daily response time invalid')
    stamp = datetime.strptime(day_text + time_text, '%Y%m%d%H%M%S').replace(tzinfo=KST)
    if not timedelta(0) <= now - stamp <= timedelta(seconds=120) or stamp.date() != now.date():
        raise ValueError('daily response stale')
    converted = []
    for row in rows:
        raw_day = row['bsop_date']
        if not isinstance(raw_day, str) or len(raw_day) != 8 or not raw_day.isascii() or not raw_day.isdigit():
            raise ValueError('daily date invalid')
        day = datetime.strptime(raw_day, '%Y%m%d').date()
        if day > now.date():
            raise ValueError('daily response future')
        if day == now.date():
            continue
        converted.append({'time': datetime.combine(day, time.min, KST).isoformat(),
                          'open': row['stck_oprc'], 'high': row['stck_hgpr'], 'low': row['stck_lwpr'],
                          'close': row['stck_prpr'], 'volume': row['vol']})
    return sorted(converted, key=lambda row: row['time']), stamp


def observe(reader, event_path, clock=lambda: datetime.now(KST), sleeper=lambda _: None, daily_cache=None):
    """Observe latest recorded market state; unknown data produces WAIT, never orders. `daily_cache` ({(code, day):
    (rows, stamp)}, kept by run()): the daily history drops today's row, so it is read from NH once a day (11-20)."""
    result = {'time': None, 'status': 'unavailable', 'minute_event_time': None,
              'source': SOURCE, 'symbols': {name: _empty(code, 'minute_data_unavailable')
                                             for name, code in SYMBOLS.items()}}
    try:
        now = _timestamp(clock())
        result['time'] = now.isoformat()
    except (TypeError, ValueError, OverflowError):
        return result
    if now.weekday() >= 5 or not '09:10' <= now.strftime('%H:%M') < '15:00':
        return result
    config = getattr(reader, 'server_config', None)
    args = getattr(config, 'args', ())
    if (not isinstance(args, (list, tuple)) or getattr(reader, 'server_name', None) != 'nhplug_readonly' or args.count('--mode') != 1
            or args[args.index('--mode') + 1:args.index('--mode') + 2] != ['readonly']):
        return result
    event, event_time = None, None
    try:
        candidate = _latest_decision(event_path)
        if isinstance(candidate, dict) and isinstance(candidate.get('market_bars'), dict):
            event_time = _timestamp(candidate['time'])
            result['minute_event_time'] = event_time.isoformat()
            if event_time.date() == now.date() and timedelta(0) <= now - event_time <= timedelta(seconds=120):
                event = candidate
    except (OSError, ValueError, TypeError, KeyError):
        pass
    for name, code in SYMBOLS.items():
        try:
            minute_rows = event['market_bars'].get(name) if event is not None else None
            if isinstance(minute_rows, list):
                minute_source = 'market_context' if event.get('event') == 'market_context' else 'decision_event'
                minute, minute_stamp = _minute_bars(minute_rows, event_time), event_time
            else:
                from nh_etf_paper_runner import valid_bars
                sleeper(1)
                minute_reply = reader.call_tool('call_api', {'operationId': 'krstockQuotePeriod', 'input': {
                    'market_cd': 'KRX', 'iem_cd': code, 'gubun': '5', 'xtick': '1',
                    'today_cls_code': '1', 'view_main_yn': 'Y', 'array_cnt': '30'}})
                minute_stamp = _timestamp(clock())
                if not isinstance(minute_reply, dict) or minute_reply.get('status') != 'ok':
                    raise ValueError('minute response unavailable')
                minute = _minute_bars(valid_bars(minute_reply.get('data', {}), code, minute_stamp), minute_stamp)
                minute_source = 'nh_direct'
            cached = daily_cache.get((code, now.date())) if daily_cache is not None else None
            if cached is None:
                sleeper(1)
                reply = reader.call_tool('call_api', {'operationId': 'krstockQuotePeriod', 'input': {
                    'market_cd': 'KRX', 'iem_cd': code, 'gubun': '1', 'today_cls_code': '0',
                    'view_main_yn': 'Y', 'array_cnt': '30'}})
            as_of = _timestamp(clock())
            if not timedelta(0) <= as_of - minute_stamp <= timedelta(seconds=120):
                raise ValueError('minute source stale')
            daily, daily_stamp = cached if cached is not None else _daily_bars(reply, code, as_of)
            if daily_cache is not None and cached is None:
                for old in [k for k in daily_cache if k[1] != now.date()]:
                    del daily_cache[old]
                daily_cache[(code, now.date())] = (daily, daily_stamp)
            result['symbols'][name] = {'symbol': code, **classify_regime(as_of, minute, daily),
                                       'daily_quote_time': daily_stamp.isoformat(), 'daily_history_cached': cached is not None,
                                       'minute_source': minute_source, 'minute_data_time': minute_stamp.isoformat()}
        except Exception:
            result['symbols'][name] = _empty(code, 'market_data_unavailable')
    final = _timestamp(clock())
    result['time'] = final.isoformat()
    for name, code in SYMBOLS.items():
        row = result['symbols'][name]
        stamp = row.get('daily_quote_time')
        minute_stamp = row.get('minute_data_time')
        if stamp and ((not row.get('daily_history_cached') and not timedelta(0) <= final - _timestamp(stamp) <= timedelta(seconds=120))
                      or not minute_stamp or not timedelta(0) <= final - _timestamp(minute_stamp) <= timedelta(seconds=120)):
            result['symbols'][name] = _empty(code, 'market_data_stale')
    valid = sum(row['data_quality'] == 'valid' for row in result['symbols'].values())
    result['status'] = 'ready' if valid == len(SYMBOLS) else 'partial' if valid else 'unavailable'
    return result


def publish(path, result):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.regime-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def claim_attempt(path, now):
    """Call under the observer lock so an interrupted run remains rate-limited."""
    path = Path(path)
    now = _timestamp(now)
    if path.exists():
        last = _timestamp(json.loads(path.read_text())['time'])
        if now < last or now - last < timedelta(seconds=120):
            return False
    publish(path, {'time': now.isoformat()})
    return True


def _unavailable(now, reason):
    return {'time': now.isoformat(), 'status': 'unavailable', 'minute_event_time': None,
            'source': SOURCE, 'symbols': {name: _empty(code, reason) for name, code in SYMBOLS.items()}}


def run(root, reader, *, watch=False, clock=lambda: datetime.now(KST), sleeper=clocktime.sleep):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / 'regime-observer.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 'already_running'
        waiting_day, daily_cache = None, {}
        while True:
            try:
                now = _timestamp(clock())
            except (TypeError, ValueError, OverflowError):
                return 'clock_unavailable'
            if watch:
                if waiting_day is not None and (now.date() != waiting_day or (root / 'STOP').exists()):
                    return 'outside_session' if now.date() != waiting_day else 'stopped'
                if wait := until_open(now):  # started pre-open: wait for the bell instead of quitting
                    sleeper(min(30, wait))
                    continue
                if now.weekday() >= 5 or not '09:00' <= now.strftime('%H:%M') < '15:00':
                    return 'outside_session'
                if now.strftime('%H:%M') < '09:10':
                    if (root / 'STOP').exists():
                        return 'stopped'
                    waiting_day = now.date()
                    opening = now.replace(hour=9, minute=10, second=0, microsecond=0)
                    sleeper(min(30, (opening - now).total_seconds()))
                    continue
                # STOP is the paper runner's file in this folder: after the open a runner restart (STOP, then delete)
                # must not end the observer (11/17 09:36 it did and the regime view went stale for hours)
                waiting_day = None
            try:
                due = claim_attempt(root / 'regime-last-attempt.json', now)
            except (OSError, ValueError, TypeError, KeyError):
                due = False
                status = 'rate_limit_unavailable'
                try:
                    publish(root / 'regime-observation.json', _unavailable(now, status))
                except (OSError, ValueError, TypeError):
                    status = 'publish_failed'
            else:
                status = 'rate_limited'
            if due:
                try:
                    result = observe(reader, root / 'events.jsonl', clock, sleeper, daily_cache)
                except Exception:
                    result = _unavailable(now, 'observer_error')
                try:
                    publish(root / 'regime-observation.json', result)
                    status = result['status']
                except (OSError, ValueError, TypeError, KeyError):
                    status = 'publish_failed'
            print(json.dumps({'time': now.isoformat(), 'status': status}, ensure_ascii=False), flush=True)
            if not watch:
                return status
            sleeper(120)


def main(argv=None):
    from src.config.loader import load_agent_config
    from src.config.paths import get_runtime_root
    from src.tools.mcp import MCPServerAdapter
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--watch', action='store_true', help='Observe at least 120 seconds apart during the session')
    args = parser.parse_args(argv)
    root = get_runtime_root() / 'nhplug/shared-etf-paper'
    config = load_agent_config().mcp_servers['nhplug_readonly']
    run(root, MCPServerAdapter('nhplug_readonly', config), watch=args.watch)


if __name__ == '__main__':
    main()
