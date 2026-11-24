"""Read-only ^NDX regular-session monitoring against the saved daily-close peak."""
from __future__ import annotations

import json
import math
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import config_store
import vibe_longterm as L

from src import file_lock

ROOT = L.ROOT / 'ndx-intraday'
NY = ZoneInfo('America/New_York')
ALERT_THRESHOLD = -0.20


def fetch():
    req = Request('https://query1.finance.yahoo.com/v8/finance/chart/%5ENDX?interval=1m&range=1d',
                  headers={'User-Agent': 'Mozilla/5.0'})
    with urlopen(req, timeout=12) as response:
        return json.load(response)['chart']['result'][0]


def fetch_daily():
    req = Request('https://query1.finance.yahoo.com/v8/finance/chart/%5ENDX?interval=1d&range=5d',
                  headers={'User-Agent': 'Mozilla/5.0'})
    with urlopen(req, timeout=12) as response:
        return json.load(response)['chart']['result'][0]


def closing(data, session, end, peak, threshold, now):
    result = {'status': 'unavailable', 'source': 'Yahoo Finance ^NDX 일봉', 'session': session}
    if now.timestamp() < end + 900:
        return {**result, 'reason': '종가 안정화 대기'}
    meta = data.get('meta') or {}
    if meta.get('symbol') != '^NDX':
        return {**result, 'reason': '지수 코드 불일치'}
    last = meta.get('regularMarketTime')
    if (type(last) not in (int, float) or not end - 120 <= last <= now.timestamp() + 5
            or datetime.fromtimestamp(last, NY).date().isoformat() != session):
        return {**result, 'reason': '정규장 마감 시각과 종가 자료 불일치'}
    quotes = ((data.get('indicators') or {}).get('quote') or [{}])[0]
    matches = []
    for ts, value in zip(data.get('timestamp') or [], quotes.get('close') or []):
        if type(ts) not in (int, float) or type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            continue
        if datetime.fromtimestamp(ts, NY).date().isoformat() == session:
            matches.append((ts, value))
    if len(matches) != 1:
        return {**result, 'reason': '해당 정규장 일봉 종가 미확인'}
    close = matches[0][1]
    drawdown = close / max(peak, close) - 1
    return {**result, 'status': 'confirmed', 'close': close, 'drawdown': drawdown,
            'threshold': threshold, 'qualifies': drawdown <= threshold}


def latest_record(root=ROOT):
    """Read the most recent US-session record, with no provider calls."""
    for path in sorted(Path(root).glob('????-??-??.json'), reverse=True):
        try:
            row = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(row, dict) and row.get('session') == date.fromisoformat(path.stem).isoformat():
                return row
        except (OSError, ValueError):
            continue
    return None


def evaluate(data, peak, threshold, now):
    """Only completed and current same-session regular-minute lows can trigger."""
    result = {'status': 'unavailable', 'source': 'Yahoo Finance ^NDX 1분봉', 'threshold': threshold}
    if type(peak) not in (int, float) or not math.isfinite(peak) or peak <= 0:
        return {**result, 'reason': '기준 최고 종가 미확인'}
    meta = data.get('meta') or {}
    if meta.get('symbol') != '^NDX':
        return {**result, 'reason': '지수 코드 불일치'}
    period = (meta.get('currentTradingPeriod') or {}).get('regular') or {}
    try:
        start, end = int(period['start']), int(period['end'])
        start_dt = datetime.fromtimestamp(start, NY)
        end_dt = datetime.fromtimestamp(end, NY)
        now = now.astimezone(timezone.utc)
        session = start_dt.date().isoformat()
        if (start_dt.date() != end_dt.date() or start_dt.weekday() >= 5 or start_dt.strftime('%H:%M') != '09:30'
                or end_dt.strftime('%H:%M') not in ('13:00', '16:00')):
            raise ValueError
    except (KeyError, TypeError, ValueError, OSError, OverflowError):
        return {**result, 'reason': '정규장 시간 미확인'}
    result.update(session=session, peak=peak, regular_start=start_dt.isoformat(), regular_end=end_dt.isoformat())
    ended = now.timestamp() >= end
    if not (start <= now.timestamp() <= end + 1800) or now.astimezone(NY).date().isoformat() != session:
        return {**result, 'status': 'outside_session', 'reason': '미국 정규장 외'}
    quote = ((data.get('indicators') or {}).get('quote') or [{}])[0]
    bars = []
    for ts, low in zip(data.get('timestamp') or [], quote.get('low') or []):
        if type(ts) not in (int, float) or type(low) not in (int, float):
            continue
        if not math.isfinite(low) or low <= 0 or not (start <= ts < end) or ts + 60 > now.timestamp():
            continue
        if datetime.fromtimestamp(ts, NY).date().isoformat() == session:
            bars.append((ts, low))
    if not bars:
        return {**result, 'reason': '완료된 정규장 1분봉 없음'}
    ts, low = max(bars, key=lambda x: x[0])
    observed = datetime.fromtimestamp(ts, timezone.utc)
    freshness = min(now, datetime.fromtimestamp(end, timezone.utc))
    if freshness - (observed + timedelta(minutes=1)) > timedelta(minutes=5):
        return {**result, 'status': 'delayed', 'reason': '최근 완료봉 5분 초과', 'observed_at': observed.isoformat()}
    boundary = peak * (1 + threshold)
    first_hit = next(((t, v) for t, v in sorted(bars) if v <= boundary), None)
    result.update(status='crossed' if first_hit else 'finished' if ended else 'watching', observed_at=observed.isoformat(),
                  low=low, drawdown=low / peak - 1, crossed=bool(first_hit))
    if first_hit:
        result.update(crossed_at=datetime.fromtimestamp(first_hit[0], timezone.utc).isoformat(),
                      crossed_low=first_hit[1], crossed_drawdown=first_hit[1] / peak - 1)
    return result


def run(now=None, root=ROOT, provider=fetch, daily_provider=fetch_daily):
    now = now or datetime.now(timezone.utc)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / 'observer.lock').open('a', encoding='utf-8') as lock:
        file_lock.lock(lock)
        try:
            plan = (L.read_state(L.ROOT, ('plan',)).get('plan') or {})
            ath = ((plan.get('indicators') or {}).get('ath') or {}).get('ndx') or {}
            cfg = L.yeo_settings(L.load_settings(L.ROOT), 'live-2')
            if cfg is None:
                raise ValueError('account strategy unavailable')
            import yeo_method
            pair = next(p for p in yeo_method.pairs(cfg) if p[0] == 'ndx')
            local_day = now.astimezone(NY).date()
            basis_day = date.fromisoformat(ath.get('as_of') or '')
            if not 0 <= (local_day - basis_day).days <= 7:
                raise ValueError('기준 최고 종가 기록 만료')
            data = provider()
            # The provider can advertise the next session shortly after the close. Recover today's saved
            # regular hours only after that session actually ended, and still date-filter all bars.
            prior = latest_record(root)
            if prior and prior.get('session') == local_day.isoformat() and (prior_end := prior.get('regular_end')):
                end = datetime.fromisoformat(prior_end)
                if end <= now and (prior_start := prior.get('regular_start')):
                    meta = data.get('meta') or {}
                    data = {**data, 'meta': {**meta, 'currentTradingPeriod': {'regular': {
                        'start': datetime.fromisoformat(prior_start).timestamp(), 'end': end.timestamp()}}}}
            result = evaluate(data, ath.get('peak'), ALERT_THRESHOLD, now)
            result.update(peak_day=ath.get('peak_day'), peak_as_of=ath.get('as_of'), engine_plan_day=plan.get('day'),
                          plan_threshold=pair[4][0][0])
        except Exception as exc:
            result = {'status': 'unavailable', 'reason': f'관측 자료 확인 실패 ({type(exc).__name__})'}
        result['checked_at'] = now.isoformat()
        session = result.get('session')
        if session:
            path = root / f'{session}.json'
            try:
                old = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                old = {}
            # Preserve the crossing evidence after a temporary data failure.
            crossing = old.get('crossing')
            alert = result.get('status') == 'crossed' and crossing is None
            if alert:
                crossing = {k: result[k] for k in ('crossed_at', 'crossed_low', 'crossed_drawdown', 'peak', 'threshold')}
            result['alert'] = alert
            result['crossing'] = crossing
            close = old.get('close')
            end_text = result.get('regular_end')
            if (end_text and result.get('peak') and (not close or close.get('status') != 'confirmed')
                    and now.timestamp() >= datetime.fromisoformat(end_text).timestamp() + 900):
                try:
                    checked = closing(daily_provider(), session, datetime.fromisoformat(end_text).timestamp(),
                                      result['peak'], result['plan_threshold'], now)
                    if checked.get('status') == 'confirmed' or not close or close.get('status') != 'confirmed':
                        close = checked
                except Exception as exc:
                    if not close or close.get('status') != 'confirmed':
                        close = {'status': 'unavailable', 'reason': type(exc).__name__}
            result['close'] = close
            temp = root / f'.{session}.{os.getpid()}.tmp'
            temp.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
            config_store.replace(temp, path)
        return result


if __name__ == '__main__':
    now = datetime.now(timezone.utc)
    local = now.astimezone(NY)
    result = (run(now) if local.weekday() < 5 and '09:30' <= local.strftime('%H:%M') <= '17:30'
              else {'status': 'outside_session', 'alert': False, 'reason': '미국 관측 시간 외'})
    print(json.dumps(result, ensure_ascii=False))
