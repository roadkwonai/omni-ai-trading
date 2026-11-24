"""Isolated Jev holding opinions for a verified vibe event; never submits orders."""
from __future__ import annotations

import json
import hashlib
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from vibe_event_advisor import KST, _identity, _time, context_sufficient, current_position
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')

REPO = Path(__file__).resolve().parents[1]
MODEL = 'jev-1.14.0'
MAX_AGE = timedelta(seconds=60)
RESEARCH = 'jev-event-research'
_NOT_SENT_MARKER = 'JEV_REQUEST_NOT_SENT_LOCK'


class JevEventNotSent(ValueError):
    """The child confirmed that the Jev lock failed before HTTP transmission."""


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _same_number(a, b):
    try:
        return _number(a) and _number(b) and Decimal(str(a)) == Decimal(str(b))
    except InvalidOperation:
        return False


def _answer(answer, threshold):
    if (not isinstance(answer, dict) or answer.get('type') != 'choice'
            or answer.get('choice') not in ('HOLD', 'SELL')
            or type(answer.get('confidence')) not in (int, float)
            or not math.isfinite(answer['confidence']) or not 0 <= answer['confidence'] <= 1):
        raise ValueError('invalid_jev_answer')
    return ('hold' if answer['choice'] == 'HOLD' else
            'exit' if answer['confidence'] >= threshold else 'uncertain')


def _same_vibe_root(root):
    """Do not follow a default engine if the saved account role is missing or invalid."""
    from accounts import load, stored
    from engine_roles import ledger_root
    root = Path(root)
    path = root.parent / 'accounts.json'
    try:
        raw = stored(path).get('paper-shared') or {}
        actual = load(path).get('paper-shared') or {}
        engine = raw.get('engine')
        return (engine in ('jev', 'vibe') and not actual.get('invalid') and actual.get('engine') == engine
                and ledger_root(root.parent, 'vibe') == root)
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _result(stance, source, reason, *, confidence=None, digest=None, choice=None,
            response_time=None, chart_sha256=None, sell_confidence=None, strategy=None, strategy_observed_at=None,
            api_calls=0, elapsed_ms=None):
    return {'text': json.dumps({'stance': stance, 'reason': reason}, ensure_ascii=False),
            'source': source, 'usage': {'status': 'unavailable'}, 'api_cost': 'unknown',
            'confidence': confidence, 'input_digest': digest, 'choice': choice,
            'response_time': response_time, 'chart_sha256': chart_sha256,
            'sell_confidence': sell_confidence, 'strategy': strategy,
            'strategy_observed_at': strategy_observed_at, 'api_calls': api_calls, 'elapsed_ms': elapsed_ms}


def _recent_existing(root, row, context, now, strategy, position):
    """Reuse only if the original model input and same position generation are provable."""
    from etf_universe import SYMBOLS
    from vibe_fast_exit_sim import _position_at

    symbol, inputs = row['symbol'], row['event_trigger']['inputs']
    event_at, bars_through = _time(row['observed_at']), _time(inputs.get('bars_through'))
    shared = Path(root).parent / 'shared-etf-paper'
    try:
        with sqlite3.connect(f"file:{Path(root) / 'orders.sqlite3'}?mode=ro", uri=True, timeout=2) as db:
            raw = db.execute('SELECT order_id,symbol,side,quantity,price,time FROM fills ORDER BY rowid').fetchall()
        fills = [(oid, code, side, qty, price, _time(at)) for oid, code, side, qty, price, at in raw]
        if any(at is None for *_, at in fills):
            return None
        records = []
        with (Path(root) / 'events.jsonl').open(encoding='utf-8') as stream:
            for line in stream:
                if 'jev_holding_decision' not in line:
                    continue
                event = json.loads(line)
                at = _time(event.get('model_response_time')) if isinstance(event, dict) else None
                if (at is not None and event.get('symbol') == symbol and event.get('model') == MODEL
                        and timedelta(0) <= now - at <= MAX_AGE and at <= event_at):
                    records.append((at, event))
        for at, event in sorted(records, key=lambda item: item[0], reverse=True):
            digest = event.get('input_digest')
            if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
                continue
            pos = _position_at(fills, symbol, at)
            if (not pos or pos['position_id'] != row['position_id'] or pos['qty'] != row['qty']
                    or pos['latest_fill_id'] != row['latest_fill_id']
                    or not _same_number(pos['entry_price'], inputs.get('entry_price'))):
                continue
            with sqlite3.connect(f"file:{shared / 'market-research.sqlite3'}?mode=ro", uri=True, timeout=2) as db:
                record = db.execute("SELECT inputs,response FROM model_inputs WHERE digest=? AND engine='jev' AND question='holding'", (digest,)).fetchone()
            if not record:
                continue
            saved, answer = json.loads(record[0]), json.loads(record[1])
            state = saved.get('state') or {}
            holding = state.get('position') or {}
            if (holding.get('symbol') != symbol or holding.get('quantity') != row['qty']
                    or not _same_number(holding.get('entry_average'), inputs.get('entry_price'))
                    or not _same_number(holding.get('bid'), inputs.get('bid'))
                    or answer.get('choice') != event.get('choice')
                    or answer.get('confidence') != event.get('confidence')):
                continue
            if any(holding.get(k) != v for k, v in position.items() if k not in ('market_index', 'market_regime')):
                continue
            if state.get('vibe_strategy') != strategy:
                continue  # Older Jev evidence has no proof of the exact vibe rules it saw.
            if state.get('vibe_position_source') != {'engine': 'vibe', 'ledger': Path(root).name}:
                continue
            aliases = {v: k for k, v in SYMBOLS.items()}
            raw_clock = (state.get('chart_as_of') or {}).get(aliases.get(symbol, symbol))
            if not isinstance(raw_clock, str) or not re.fullmatch(r'\d{6}', raw_clock) or bars_through is None:
                continue
            chart_at = datetime.strptime(at.strftime('%Y%m%d') + raw_clock, '%Y%m%d%H%M%S').replace(tzinfo=KST)
            if chart_at < bars_through or chart_at > at:
                continue
            prior = (context.get('prior_analysis') or {}).get('observed_at')
            if not prior or (state.get('vibe_trading_view') or {}).get('as_of') != prior:
                continue
            market = (context.get('market') or {}).get('data')
            if not isinstance(market, dict) or state.get('market_regime_themes_global') != market:
                continue
            news = (context.get('news') or {}).get('items') or []
            saved_news = state.get('recent_news_headlines') or []
            if not news or not all(isinstance(n, dict) and any(isinstance(s, dict) and s.get('title') == n.get('title') and s.get('published') == n.get('published') for s in saved_news) for n in news):
                continue
            chart_sha256 = saved.get('chart_sha256')
            if not isinstance(chart_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', chart_sha256):
                continue
            return answer, digest, at.isoformat(), chart_sha256
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, json.JSONDecodeError):
        return None
    return None


def _strategy(root, now):
    """Snapshot only validated saved settings; a changed file invalidates the snapshot."""
    from strategy_settings import FILE, read_settings
    shared = Path(root).parent / 'shared-etf-paper'
    path = shared / FILE
    try:
        before = path.read_bytes()
        stamp = datetime.fromtimestamp(path.stat().st_mtime, KST)
        if stamp > now:
            raise ValueError('settings_future')
    except FileNotFoundError:
        before, stamp = None, None
    settings, status = read_settings(shared)
    if status == 'invalid':
        raise ValueError('settings_invalid')
    try:
        after = path.read_bytes()
    except FileNotFoundError:
        after = None
    if before != after or (status == 'default') != (before is None):
        raise ValueError('settings_changed')
    return {'values': settings, 'source': status, 'saved_at': stamp.isoformat() if stamp else None,
            'sha256': hashlib.sha256(before).hexdigest() if before is not None else None}


def _position_plan(root, row, context, strategy):
    """Use the engine's actual position history and known exit-plan rules, or refuse."""
    from nh_paper_isolated import (exit_plans, market_view, position_since_open,
                                   regime_stop_levels, stop_symbol_index, take_profit_target,
                                   verified_take_profit_sold)
    from session_times import times

    root = Path(root)
    if root.name not in ('vibe-paper', 'shared-etf-paper'):
        raise ValueError('position_source_unknown')
    shared = root.parent / 'shared-etf-paper'
    symbol, qty = row['symbol'], row['qty']
    values = strategy['values']
    index = stop_symbol_index(shared, symbol)
    observed_market = (context.get('market') or {}).get('data')
    current_market = market_view(shared)
    if (index is None or not isinstance(current_market, dict) or current_market != observed_market
            or (current_market.get(index) or {}).get('regime') not in ('상승장', '횡보장', '하락장')):
        raise ValueError('position_regime_unavailable')
    from engine_exits import defensive_plan
    plan = defensive_plan(values)
    if root.name == 'shared-etf-paper':
        plan_path = root / 'exit-plans.json'
        try:
            raw = json.loads(plan_path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            raw = {}
        if not isinstance(raw, dict):
            raise ValueError('exit_plan_unavailable')
        if symbol in raw:
            saved = exit_plans(root).get(symbol)
            if saved is None or saved != raw[symbol] or saved.get('profile') in ('oversold', 'ipo'):
                raise ValueError('exit_plan_unavailable')
            plan = saved
    levels = regime_stop_levels(plan, current_market, index, symbol)
    with sqlite3.connect(f"file:{root / 'orders.sqlite3'}?mode=ro", uri=True, timeout=2) as db:
        history = position_since_open(db, symbol, qty)
        profit_sold = verified_take_profit_sold(db, symbol, qty)
    if profit_sold is None:
        raise ValueError('take_profit_history_unavailable')
    inputs = row['event_trigger']['inputs']
    avg, bid = inputs['entry_price'], inputs['bid']
    if not _number(avg) or not _number(bid):
        raise ValueError('position_price_unavailable')
    pct = lambda level: None if level is None else round(float((level - 1) * 100), 2)
    half_sold = profit_sold >= take_profit_target(history)
    return {'quantity': qty, 'return_pct': round((bid / avg - 1) * 100, 2),
            'exit_profile': plan.get('profile', 'defensive'), 'stop_pct': pct(levels['stop']),
            'first_take_profit_pct': pct(levels['half']), 'final_take_profit_pct': pct(levels['all']),
            'trailing_pct': None if levels['trail'] is None else float(levels['trail'] * 100),
            'peak_bid': plan.get('peak'), 'half_already_sold': half_sold,
            'market_index': index, 'market_regime': current_market[index]['regime'],
            'same_day_exit_from': times(values)['flatten']}


def _fresh_inputs(root, row, context, now, strategy, position):
    """Read only already captured 1-minute bars; do not query the broker."""
    from etf_universe import SYMBOLS
    from vibe_fast_exit_watch import _recent_bars

    symbol = row['symbol']
    codes = set(SYMBOLS.values()) | {symbol}
    recent = _recent_bars(Path(root).parent / 'shared-etf-paper' / 'events.jsonl', codes, now)
    if set(recent) != codes:
        raise ValueError('jev_bars_unavailable')
    bars = {name: recent[code][0] for name, code in SYMBOLS.items()}
    if symbol not in SYMBOLS.values():
        bars[symbol] = recent[symbol][0]
    from nh_paper_isolated import fresh_etf7_bars
    if not fresh_etf7_bars(bars, now):
        raise ValueError('jev_bars_stale')
    news = (context.get('news') or {}).get('items')
    market = (context.get('market') or {}).get('data')
    if not isinstance(news, list) or not news or not isinstance(market, dict):
        raise ValueError('jev_context_unavailable')
    inputs = row['event_trigger']['inputs']
    return {'bars': bars, 'news': news, 'symbol': symbol, 'avg': inputs['entry_price'], 'bid': inputs['bid'],
            'position_extra': position,
            'extra_state': {'market_regime_themes_global': market,
                            'vibe_reference': context.get('prior_analysis'),
                            'vibe_strategy': strategy,
                            'strategy_observed_at': now.isoformat(),
                            'trigger_context': row['event_trigger']}}


_CHILD = '''import json, sys
from pathlib import Path
import research_store
research_store.ROOT = Path(sys.argv[1])
import jev_activity
# The vibe helper must never block Jev main (user 2026-11-17): Jev main's requests fail at once while the shared
# regular lock is held, so this child holds its own lock; it still steps aside when Jev main is busy (checked before).
jev_activity.LOCK = Path.home() / '.vibe-trading/nhplug/jev-event-request.lock'
from nh_etf_auto_v2 import jev_holding7
payload = json.load(sys.stdin)
try:
    result = jev_holding7(**payload)
except Exception as exc:
    from jev_activity import JevRequestBusy
    if isinstance(exc, JevRequestBusy):
        print('JEV_REQUEST_NOT_SENT_LOCK')
        sys.exit(73)
    raise
print(json.dumps(result, ensure_ascii=False))
'''


def _invoke(payload, directory):
    """Run the existing Jev helper in a child whose research store is isolated."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    env = {**os.environ, 'PYTHONPATH': f"{REPO / 'agent'}:{REPO / 'scripts'}"}
    child = subprocess.run([sys.executable, '-c', _CHILD, str(directory)], cwd=str(REPO), env=env,
                           input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True, timeout=35, encoding='utf-8')
    if child.returncode == 73 and child.stdout.strip() == _NOT_SENT_MARKER:
        raise JevEventNotSent('jev_request_not_sent_lock')
    if child.returncode:
        raise ValueError('jev_call_failed')
    return json.loads(child.stdout.strip().splitlines()[-1])


def _verify_stored_input(directory, digest, chart, answer, payload):
    """A digest is evidence only when the isolated store has the exact model input and answer."""
    try:
        path = Path(directory) / 'market-research.sqlite3'
        if not path.is_file():
            return False
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=2) as db:
            saved = db.execute("SELECT inputs,response FROM model_inputs WHERE digest=? AND engine='jev' AND question='holding'", (digest,)).fetchone()
        if not saved:
            return False
        recorded, recorded_answer = json.loads(saved[0]), json.loads(saved[1])
        state = recorded['state']
        position = state['position']
        expected = payload['extra_state']
        return (recorded.get('chart_sha256') == chart and recorded_answer == answer
                and position.get('symbol') == payload['symbol']
                and all(position.get(k) == v for k, v in payload['position_extra'].items())
                and _same_number(position.get('entry_average'), payload['avg']) and _same_number(position.get('bid'), payload['bid'])
                and all(state.get(k) == expected.get(k) for k in ('vibe_strategy', 'strategy_observed_at',
                                                                   'vibe_reference', 'trigger_context',
                                                                   'market_regime_themes_global')))
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, json.JSONDecodeError):
        return False


def review(row, context, root, *, clock=None):
    """Produce an isolated observation; the caller owns persistent rate limiting and order isolation."""
    clock = clock or (lambda: datetime.now(KST))
    now = clock().astimezone(KST)
    identity = _identity(row, now)
    if identity is None or not _same_vibe_root(root) or not current_position(root, identity, now):
        return _result('uncertain', 'unavailable', '이벤트 또는 보유 상태를 확인할 수 없습니다.')
    if not context_sufficient(context, now):
        return _result('uncertain', 'unavailable', '시황 또는 뉴스의 출처와 시각을 확인할 수 없습니다.')
    prior = context.get('prior_analysis')
    prior_at = _time(prior.get('observed_at')) if isinstance(prior, dict) else None
    if (not isinstance(prior, dict) or prior.get('status') != 'reference_only'
            or not isinstance(prior.get('technical'), dict) or prior_at is None
            or not timedelta(0) <= now - prior_at <= timedelta(minutes=20)):
        return _result('uncertain', 'unavailable', 'vibe 기술 판단의 출처와 시각을 확인할 수 없습니다.')
    api_calls, started = 0, None
    try:
        strategy = _strategy(root, now)
        settings = strategy['values']
        position = _position_plan(root, row, context, strategy)
        reused = _recent_existing(root, row, context, now, strategy, position)
        if reused:
            after = clock().astimezone(KST)
            if (_identity(row, after) != identity or not _same_vibe_root(root) or not current_position(root, identity, after)
                    or not context_sufficient(context, after) or _strategy(root, after) != strategy
                    or _position_plan(root, row, context, strategy) != position):
                raise ValueError('jev_reuse_stale')
            answer, digest, response_time, chart_sha256 = reused
            stance = _answer(answer, settings['sell_confidence'])
            return _result(stance, 'jev_existing', '동일 보유와 입력이 확인된 기존 Jev 보유 판단입니다.',
                           confidence=answer['confidence'], digest=digest, choice=answer['choice'],
                           response_time=response_time, chart_sha256=chart_sha256,
                           sell_confidence=settings['sell_confidence'], strategy=strategy,
                           strategy_observed_at=now.isoformat())
        payload = _fresh_inputs(root, row, context, now, strategy, position)
        from jev_activity import regular_request_busy
        if regular_request_busy():
            return _result('uncertain', 'unavailable', '정규 Jev 요청이 진행 중이거나 상태를 확인할 수 없습니다.')
        directory = Path(root).parent / 'vibe-event-analysis' / RESEARCH
        api_calls, started = None, time.monotonic()
        result = _invoke(payload, directory)
        api_calls = 1
        elapsed_ms = round((time.monotonic() - started) * 1000)
        after = clock().astimezone(KST)
        response_at = _time(result.get('response_time')) if isinstance(result, dict) else None
        if (_identity(row, after) != identity or not _same_vibe_root(root) or not current_position(root, identity, after)
                or not context_sufficient(context, after) or response_at is None
                or not timedelta(0) <= after - response_at <= MAX_AGE or result.get('model') != MODEL
                or _strategy(root, after) != strategy
                or _position_plan(root, row, context, strategy) != payload['position_extra']
                or not isinstance(result.get('chart_sha256'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', result['chart_sha256'])
                or not isinstance(result.get('input_digest'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', result['input_digest'])
                or not _verify_stored_input(directory, result['input_digest'], result['chart_sha256'],
                                            result.get('holding'), payload)):
            raise ValueError('jev_result_stale')
        answer = result.get('holding')
        stance = _answer(answer, settings['sell_confidence'])
        return _result(stance, 'jev_event', 'vibe 보유 상태와 이벤트에 대한 별도 Jev 판단입니다.',
                       confidence=answer['confidence'], digest=result.get('input_digest'), choice=answer['choice'],
                       response_time=response_at.isoformat(), chart_sha256=result['chart_sha256'],
                       sell_confidence=settings['sell_confidence'], strategy=strategy,
                       strategy_observed_at=now.isoformat(), api_calls=api_calls, elapsed_ms=elapsed_ms)
    except JevEventNotSent:
        elapsed = round((time.monotonic() - started) * 1000) if started is not None else None
        return _result('uncertain', 'unavailable', 'Jev 공통 잠금 경합 또는 잠금 오류로 전송 전 보류했습니다.',
                       api_calls=0, elapsed_ms=elapsed)
    except (OSError, ValueError, TypeError, KeyError, IndexError, sqlite3.Error, subprocess.SubprocessError):
        elapsed = round((time.monotonic() - started) * 1000) if started is not None else None
        return _result('uncertain', 'unavailable', 'Jev 판단 입력이나 응답을 확인할 수 없습니다.',
                       api_calls=api_calls, elapsed_ms=elapsed)
