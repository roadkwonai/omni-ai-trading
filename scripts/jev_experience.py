"""Build and read bounded, time-qualified Jev research summaries."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from config_store import write_json, write_text

MAX_LESSONS = 3
MAX_BYTES = 2048
MIN_SYMBOL_DAYS = 5
PURPOSES = {'watchlist', 'candidate', 'holding'}
MARKETS = {'KOSPI', 'KOSDAQ'}
LABELS = {'paper_fill', 'virtual_fill', 'price_observation'}
COSTS = {'included', 'excluded', 'unknown'}
TOKEN = re.compile(r'[A-Za-z0-9_.:-]{1,100}\Z')
VERSION = re.compile(r'[0-9a-f]{64}\Z')
KST = ZoneInfo('Asia/Seoul')
LESSON_FIELDS = {'purpose', 'market', 'route', 'label_type', 'cost_basis', 'symbol_days', 'positions', 'positive',
                 'negative', 'mean_return_pct', 'positive_examples', 'negative_examples', 'period_start',
                 'period_end', 'evidence_available_at'}


def _time(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('timestamp must include timezone')
    return value.astimezone(timezone.utc)


def _token(value):
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise ValueError('invalid identifier')
    return value


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def build(records, *, published_at, eligible_from, expires_at):
    published, eligible, expires = map(_time, (published_at, eligible_from, expires_at))
    if not published <= eligible < expires:
        raise ValueError('invalid publication window')
    groups = defaultdict(dict)
    identities = {}
    for row in records:
        if not isinstance(row, dict):
            raise ValueError('invalid sample')
        sample = _token(row.get('sample_id'))
        symbol = row.get('symbol')
        if not isinstance(symbol, str) or not re.fullmatch(r'[A-Za-z0-9]{6}', symbol):
            raise ValueError('invalid symbol')
        day = date.fromisoformat(row.get('day'))
        available = _time(row.get('outcome_available_at'))
        purpose, market, label, cost = (row.get(k) for k in ('purpose', 'market', 'label_type', 'cost_basis'))
        route = _token(row.get('route'))
        if purpose not in PURPOSES or market not in MARKETS or label not in LABELS or cost not in COSTS:
            raise ValueError('invalid sample classification')
        value = row.get('return_pct')
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('invalid return')
        position = row.get('position_id')
        if position is not None:
            _token(position)
        if day > available.astimezone(KST).date() or day >= eligible.astimezone(KST).date():
            raise ValueError('invalid sample day')
        identity = (symbol, day.isoformat(), purpose, market, route, label, cost, float(value), available.isoformat(), position)
        if sample in identities:
            if identities[sample] != identity:
                raise ValueError('conflicting sample identity')
            continue
        identities[sample] = identity
        if available > published:
            continue
        key = (purpose, market, route, label, cost)
        group = groups[key]
        sample_day = (symbol, day.isoformat())
        # One symbol-day contributes once, even if it was observed repeatedly.
        group.setdefault(sample_day, []).append((sample, float(value), position, available.isoformat()))
    lessons = []
    for (purpose, market, route, label, cost), days in sorted(groups.items()):
        # Use an equal weight per symbol-day, independent of the number of observations.
        averages = []
        for key, rows in sorted(days.items()):
            ordered = sorted(rows, key=lambda row: row[0])
            averages.append((key, math.fsum(row[1] for row in ordered) / len(ordered), ordered))
        positive = [item for item in averages if item[1] > 0]
        negative = [item for item in averages if item[1] < 0]
        if len(averages) < MIN_SYMBOL_DAYS or not positive or not negative:
            continue
        positions = {item[2] for _, _, rows in averages for item in rows if item[2] is not None}
        days_used = [item[0][1] for item in averages]
        evidence_at = max(item[3] for _, _, rows in averages for item in rows)
        lessons.append({'purpose': purpose, 'market': market, 'route': route, 'label_type': label,
                        'cost_basis': cost, 'symbol_days': len(averages), 'positions': len(positions),
                        'positive': len(positive), 'negative': len(negative),
                        'mean_return_pct': round(sum(item[1] for item in averages) / len(averages), 4),
                        'positive_examples': [next(row[0] for row in item[2] if row[1] > 0) for item in positive[:2]],
                        'negative_examples': [next(row[0] for row in item[2] if row[1] < 0) for item in negative[:2]],
                        'period_start': min(days_used), 'period_end': max(days_used), 'evidence_available_at': evidence_at})
    return {'schema': 1, 'research_only': True, 'published_at': published.isoformat(),
            'eligible_from': eligible.isoformat(), 'expires_at': expires.isoformat(), 'lessons': lessons}


def _directory(directory):
    directory = Path(directory).expanduser().resolve()
    roots = [Path.home() / '.vibe-trading']
    if os.environ.get('VIBE_TRADING_HOME'):
        roots.append(Path(os.environ['VIBE_TRADING_HOME']).expanduser())
    for root in roots:
        runtime = root.resolve()
        if directory == runtime or runtime in directory.parents:
            raise ValueError('research output must be outside runtime')
    return directory


def publish(artifact, directory):
    directory = _directory(directory)
    if not isinstance(artifact, dict) or artifact.get('research_only') is not True or artifact.get('schema') != 1:
        raise ValueError('invalid research artifact')
    content = _encoded(artifact)
    version = hashlib.sha256(content).hexdigest()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / f'{version}.json'
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError('immutable artifact mismatch')
    else:
        write_text(path, content.decode('utf-8'))
    write_json(directory / 'current.json', {'version': version}, indent=None)
    return path


@lru_cache(maxsize=32)
def _load(path, version, inode, size, modified_ns, changed_ns):
    del inode, size, modified_ns, changed_ns
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != version:
        raise ValueError('invalid artifact digest')
    return json.loads(data)


def read(directory, *, at, purpose, market, route):
    try:
        at = _time(at)
        if purpose not in PURPOSES or market not in MARKETS:
            return {}
        _token(route)
        directory = _directory(directory)
        pointer = json.loads((directory / 'current.json').read_text(encoding='utf-8'))
        version = pointer['version']
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            return {}
        path = directory / f'{version}.json'
        info = path.stat()
        if info.st_size > 1_048_576:
            return {}
        artifact = _load(str(path), version, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if artifact.get('schema') != 1 or artifact.get('research_only') is not True:
            return {}
        if not _time(artifact['published_at']) <= _time(artifact['eligible_from']) <= at < _time(artifact['expires_at']):
            return {}
        result = {'version': version, 'research_only': True, 'lessons': []}
        for lesson in artifact['lessons']:
            if not isinstance(lesson, dict) or lesson.keys() != LESSON_FIELDS:
                return {}
            if (lesson['purpose'], lesson['market'], lesson['route']) != (purpose, market, route):
                continue
            if (lesson['label_type'] not in LABELS or lesson['cost_basis'] not in COSTS
                    or type(lesson['symbol_days']) is not int or lesson['symbol_days'] < MIN_SYMBOL_DAYS
                    or type(lesson['positions']) is not int or lesson['positions'] < 0
                    or type(lesson['positive']) is not int or type(lesson['negative']) is not int
                    or min(lesson['positive'], lesson['negative']) < 1
                    or lesson['positive'] + lesson['negative'] > lesson['symbol_days']
                    or type(lesson['mean_return_pct']) not in (int, float)
                    or not math.isfinite(lesson['mean_return_pct'])
                    or any(not isinstance(lesson[key], list) or not 1 <= len(lesson[key]) <= 2
                           for key in ('positive_examples', 'negative_examples'))):
                return {}
            for example in lesson['positive_examples'] + lesson['negative_examples']:
                _token(example)
            start = date.fromisoformat(lesson['period_start'])
            end = date.fromisoformat(lesson['period_end'])
            if start > end or end >= _time(artifact['eligible_from']).astimezone(KST).date():
                return {}
            if _time(lesson['evidence_available_at']) > _time(artifact['published_at']):
                return {}
            result['lessons'].append(deepcopy(lesson))
            if len(result['lessons']) >= MAX_LESSONS:
                break
        while result['lessons'] and len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > MAX_BYTES:
            result['lessons'].pop()
        return result if result['lessons'] else {}
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return {}
