"""Preserve the v5 baseline and publish validated, completed Yahoo daily bars."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backtest.loaders.yahoo_loader import DataLoader
from src import file_lock
from src.config.paths import get_runtime_root
from strategies.kr_swing_v5.signal_engine import SignalEngine

CODES = ('005930.KS', '000660.KS', '035420.KS', '005380.KS', '105560.KS')
COLUMNS = ('open', 'high', 'low', 'close', 'volume')
BASELINE = ROOT / '.venv/kr-swing-data'
KRX_ROOT = 'https://open.krx.co.kr'
KRX_PAGE = '/contents/MKD/01/0110/01100305/MKD01100305.jsp'
KRX_DATA = '/contents/OPN/99/OPN99000001.jspx'


def krx_holidays(years):
    """Fetch KRX's current holiday table; missing or malformed data never means open.

    This follows the official page's OTP-backed form, not a stable public API;
    any change to its response contract must stop the caller's update.
    """
    if not years or any(type(y) is not int or not 1900 <= y <= 2100 for y in years):
        raise ValueError('KRX holiday years invalid')
    session = requests.Session()
    session.trust_env = False
    session.headers.update({'User-Agent': 'Mozilla/5.0', 'Referer': KRX_ROOT + KRX_PAGE,
                            'X-Requested-With': 'XMLHttpRequest'})

    def request(method, path, **kwargs):
        response = getattr(session, method)(KRX_ROOT + path, timeout=15,
                                            allow_redirects=False, **kwargs)
        if response.status_code != 200:
            raise ValueError('KRX holiday service unavailable')
        return response

    def token(name, bld):
        value = request('get', '/contents/COM/GenerateOTP.jspx',
                        params={'name': name, 'bld': bld}).text.strip()
        if not re.fullmatch(r'[A-Za-z0-9+/=_-]{1,512}', value):
            raise ValueError('KRX holiday service returned invalid token')
        return value

    try:
        request('get', KRX_PAGE)
        code = token('selectbox', '/MKD/01/0110/01100305/mkd01100305_03')
        data = request('get', KRX_DATA, params={'code': code}).json()
        rows = data.get('block1') if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows or any(
                not isinstance(row, dict) or not re.fullmatch(r'\d{4}', str(row.get('bas_yy', '')))
                for row in rows):
            raise ValueError('KRX holiday supported years invalid')
        available = {int(row['bas_yy']) for row in rows}
        if not set(years).issubset(available):
            raise ValueError('KRX holiday year not available')
        dates = set()
        for year in sorted(years):
            code = token('form', '/MKD/01/0110/01100305/mkd01100305_01')
            data = request('post', KRX_DATA, data={'search_bas_yy': str(year),
                           'gridTp': 'KRX', 'pagePath': KRX_PAGE, 'code': code}).json()
            rows = data.get('block1') if isinstance(data, dict) else None
            if not isinstance(rows, list) or not rows:
                raise ValueError('KRX holiday year has no verifiable rows')
            for row in rows:
                if (not isinstance(row, dict) or not isinstance(row.get('calnd_dd'), str)
                        or not isinstance(row.get('holdy_nm'), str) or not row['holdy_nm'].strip()):
                    raise ValueError('KRX holiday row invalid')
                value = date.fromisoformat(row['calnd_dd'])
                if value.year != year or value in dates:
                    raise ValueError('KRX holiday date invalid')
                dates.add(value)
        return dates
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        raise ValueError('KRX holiday calendar unavailable or invalid') from exc
    finally:
        session.close()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _valid(frame):
    if not isinstance(frame, pd.DataFrame) or not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is not None:
        raise ValueError('invalid date index')
    if frame.empty or frame.index.has_duplicates or not frame.index.is_monotonic_increasing or not (frame.index == frame.index.normalize()).all():
        raise ValueError('duplicate, unsorted, or non-daily dates')
    if any(c not in frame for c in COLUMNS):
        raise ValueError('OHLCV fields missing')
    values = frame.loc[:, list(COLUMNS)].to_numpy(dtype=float)
    if (not np.isfinite(values).all() or (values[:, :4] <= 0).any() or (values[:, 4] < 0).any()
            or (values[:, 1] < values[:, [0, 2, 3]].max(axis=1)).any()
            or (values[:, 2] > values[:, [0, 1, 3]].min(axis=1)).any()):
        raise ValueError('invalid OHLCV values')


def _read(directory):
    frames = {c: pd.read_csv(directory / f'{c}.csv', parse_dates=['date']).set_index('date') for c in CODES}
    for frame in frames.values():
        _valid(frame)
    if any(not f.index.equals(frames[CODES[0]].index) for f in frames.values()):
        raise ValueError('mismatched trading calendars')
    return frames


def verify_snapshot(directory):
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if set(manifest['sha256']) != set(CODES):
        raise ValueError('manifest symbols mismatch')
    for code in CODES:
        if digest(directory / f'{code}.csv') != manifest['sha256'][code]:
            raise ValueError('snapshot hash mismatch')
    frames = _read(directory)
    if any(str(f.index[-1].date()) != manifest['last_session'] or len(f) != manifest['rows'] for f in frames.values()):
        raise ValueError('manifest calendar mismatch')
    return frames


def merge_frames(old, fetched, end, today, holidays=None):
    if end >= today:
        raise ValueError('only completed sessions may be published')
    if set(old) != set(CODES) or set(fetched) != set(CODES):
        raise ValueError('symbol mismatch')
    for frame in (*old.values(), *fetched.values()):
        _valid(frame)
    old_index = old[CODES[0]].index
    if any(not f.index.equals(old_index) for f in old.values()):
        raise ValueError('old calendar mismatch')
    last = old_index[-1].date()
    if end < last:
        raise ValueError('cannot move backwards')
    # An absent verified calendar deliberately treats every weekday as open.
    holidays = set() if holidays is None else set(holidays)
    if any(not isinstance(value, date) for value in holidays):
        raise ValueError('invalid KRX holiday dates')
    expected = pd.DatetimeIndex([d for d in pd.bdate_range(last + timedelta(days=1), end)
                                 if d.date() not in holidays])
    result = {}
    for code in CODES:
        fresh = fetched[code]
        if len(old[code]) < 120:
            raise ValueError('insufficient frozen history')
        overlap = old[code].tail(120)
        if not overlap.index.isin(fresh.index).all():
            raise ValueError('frozen history missing')
        candidate = fresh.loc[overlap.index, list(COLUMNS)].astype(float)
        original = overlap.loc[:, list(COLUMNS)].astype(float)
        if (not np.array_equal(candidate['volume'].to_numpy(), original['volume'].to_numpy())
                or not np.allclose(candidate.iloc[:, :4], original.iloc[:, :4], rtol=1e-6, atol=0)):
            raise ValueError('frozen history revised')
        comparison = old[code].copy()
        comparison.loc[overlap.index, list(COLUMNS)] = candidate
        original_signal = SignalEngine().generate({code: old[code]})[code].iloc[-1]
        revised_signal = SignalEngine().generate({code: comparison})[code].iloc[-1]
        if original_signal != revised_signal:
            raise ValueError('frozen signal revised')
        added = fresh.loc[(fresh.index > pd.Timestamp(last)) & (fresh.index <= pd.Timestamp(end)), list(COLUMNS)]
        if not added.index.equals(expected):
            raise ValueError('missing or mismatch future sessions; holiday requires verification')
        result[code] = pd.concat([old[code].loc[:, list(COLUMNS)], added])
    return result


def _write_csv(path, frame, previous):
    # Preserve every byte of the published prefix so comparison hashes stay stable.
    shutil.copyfile(previous, path)
    with path.open('a', encoding='utf-8') as stream:
        frame.to_csv(stream, header=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, 0o600)
    with path.open('rb') as f:
        os.fsync(f.fileno())


@contextmanager
def _lock(root):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    fd = os.open(root / '.lock', os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'r+', encoding='utf-8') as lock:
        file_lock.lock(lock, blocking=False)
        yield


def _publish(root, files, calendar=None):
    versions = root / 'versions'
    versions.mkdir(mode=0o700, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.stage-', dir=versions))
    try:
        for code, source in files.items():
            target = stage / f'{code}.csv'
            if isinstance(source, Path):
                shutil.copyfile(source, target)
                os.chmod(target, 0o600)
                with target.open('rb') as f:
                    os.fsync(f.fileno())
            else:
                _write_csv(target, source[1], source[0])
        frames = _read(stage)
        manifest = {'version':1, 'source':'Yahoo adjusted OHLCV; frozen baseline',
                    'sha256':{c:digest(stage/f'{c}.csv') for c in CODES},
                    'last_session':str(frames[CODES[0]].index[-1].date()), 'rows':len(frames[CODES[0]])}
        if calendar is not None:
            manifest['holiday_calendar'] = calendar
        manifest_path = stage/'manifest.json'
        fd = os.open(manifest_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(manifest, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        verify_snapshot(stage)
        fd = os.open(stage, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        final = versions / ('snapshot-' + hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()[:20])
        if final.exists():
            verify_snapshot(final)
            shutil.rmtree(stage)
        else:
            os.rename(stage, final)
        fd = os.open(versions, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        fd, name = tempfile.mkstemp(prefix='.current-', dir=root)
        os.close(fd)
        os.unlink(name)
        try:
            os.symlink(os.path.relpath(final, root), name)
            os.replace(name, root/'current')
            fd = os.open(root, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if os.path.lexists(name): os.unlink(name)
        return final
    finally:
        if stage.exists(): shutil.rmtree(stage)


def initialize(root, source):
    with _lock(root):
        if (root/'current').exists():
            raise ValueError('baseline already initialized')
        meta = json.loads((source/'metadata.json').read_text(encoding='utf-8'))
        for code in CODES:
            if digest(source/f'{code}.csv') != meta['symbols'][code]['clean_sha256']:
                raise ValueError('baseline hash mismatch')
        frames = _read(source)
        if any(len(f) != meta['common_rows'] or str(f.index[-1].date()) != meta['common_last'] for f in frames.values()):
            raise ValueError('baseline metadata mismatch')
        return _publish(root, {c:source/f'{c}.csv' for c in CODES})


def update(root, fetched, end, today, holidays=None, calendar=None):
    with _lock(root):
        current = (root/'current').resolve(strict=True)
        if current.parent != (root/'versions').resolve():
            raise ValueError('invalid current snapshot pointer')
        old = verify_snapshot(current)
        merged = merge_frames(old, fetched, end, today, holidays=holidays)
        if merged[CODES[0]].index[-1] == old[CODES[0]].index[-1]:
            return current
        return _publish(root, {c:(current/f'{c}.csv', merged[c].loc[merged[c].index > old[c].index[-1]]) for c in CODES}, calendar=calendar)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--initialize', action='store_true')
    parser.add_argument('--as-of', type=date.fromisoformat)
    args = parser.parse_args()
    root = get_runtime_root()/'nhplug/strategies/kr_swing_v5/data'
    if args.initialize:
        if args.as_of:
            parser.error('--as-of is for updates only')
        path = initialize(root, BASELINE)
    else:
        if not args.as_of:
            parser.error('--as-of is required for updates')
        today = datetime.now(ZoneInfo('Asia/Seoul')).date()
        if args.as_of >= today:
            parser.error('--as-of must be before today in Asia/Seoul')
        current = (root/'current').resolve(strict=True)
        old = verify_snapshot(current)
        last = old[CODES[0]].index[-1].date()
        if args.as_of < last:
            parser.error('--as-of cannot precede the last snapshot')
        years = set(range(last.year, args.as_of.year + 1))
        holidays = krx_holidays(years)
        checked_at = datetime.now(ZoneInfo('Asia/Seoul')).isoformat()
        start = old[CODES[0]].index[-120].date().isoformat()
        frames = DataLoader().fetch(list(CODES), start, args.as_of.isoformat(), interval='1D')
        calendar = {'source': KRX_ROOT + KRX_PAGE, 'years': sorted(years),
                    'retrieved_at': checked_at, 'holidays': sorted(d.isoformat() for d in holidays)}
        path = update(root, frames, args.as_of, today, holidays=holidays, calendar=calendar)
    print(json.dumps({'status':'published','snapshot':str(path),'last_session':json.loads((path/'manifest.json').read_text(encoding='utf-8'))['last_session']}))


if __name__ == '__main__':
    main()
