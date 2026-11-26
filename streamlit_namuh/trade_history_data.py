"""Read-only trade and asset history for the dashboard."""
from __future__ import annotations

from array import array
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from threading import Event, RLock
from uuid import uuid4
from weakref import finalize
from zoneinfo import ZoneInfo

from engine_compare import apply_fill
from live_fills import days_order_times, plausible
from sqlite_ro import ro_connect

ACCOUNTS = ('paper-shared', 'live-1', 'live-2')
KST = ZoneInfo('Asia/Seoul')
TRADE_STORES = ('shared-etf-paper/orders.sqlite3', 'live-fills.sqlite3', 'live-orders.sqlite3')
QUERY_LIMIT = 8
_query_lock = RLock()
_queries = OrderedDict()
_query_keys = {}
_query_leases = {}
_query_builds = {}
_load_lock = RLock()
_balance_lock = RLock()
_balance_cache = OrderedDict()


class QueryExpired(LookupError):
    """The stored result was evicted and must be explicitly refreshed."""


class QueryLease:
    """A session's lightweight claim on a shared query."""

    __slots__ = ('token', '_finalizer', '__weakref__')

    def __init__(self, token):
        self.token = token
        self._finalizer = finalize(self, _release_query, token)

    def release(self):
        self._finalizer()


def _trim_queries():
    idle = sum(token not in _query_leases for token in _queries)
    for token in tuple(_queries):
        if idle <= QUERY_LIMIT:
            break
        if token not in _query_leases:
            key, _ = _queries.pop(token)
            del _query_keys[key]
            idle -= 1


def _release_query(token):
    with _query_lock:
        remaining = _query_leases[token] - 1
        if remaining:
            _query_leases[token] = remaining
        else:
            del _query_leases[token]
            key, _ = _queries.pop(token)
            del _query_keys[key]
        _trim_queries()


def _take_lease(token):
    _query_leases[token] = _query_leases.get(token, 0) + 1
    return QueryLease(token)


@dataclass(frozen=True, slots=True)
class TradeQuery:
    rows: tuple
    indices: array

    def __len__(self):
        return len(self.indices)

    def __iter__(self):
        return (self.rows[index] for index in self.indices)

    def page(self, offset, limit):
        return tuple(self.rows[index] for index in self.indices[offset:offset + limit])


def _signature(path):
    path = Path(path)
    stamps = []
    for item in (path, Path(str(path) + '-wal')):
        try:
            stat = item.stat()
            stamps.append((stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            stamps.append(None)
    return tuple(stamps)


def _paper(root):
    path = root / 'shared-etf-paper' / 'orders.sqlite3'
    if not path.exists():
        return []
    with ro_connect(path) as db:
        rows = db.execute('SELECT order_id,symbol,side,quantity,price,time FROM fills ORDER BY rowid').fetchall()
    return [{'order_no': order, 'symbol': symbol, 'side': side, 'qty': quantity, 'price': price, 'time': stamp}
            for order, symbol, side, quantity, price, stamp in rows]


@lru_cache(maxsize=2)
def _live_records(root, account, signature):
    root = Path(root)
    path = root / 'live-fills.sqlite3'
    if not path.exists():
        return ()
    with ro_connect(path) as db:
        found = db.execute('SELECT time,symbol,side,quantity,price,order_no,recorded FROM fills '
                           'WHERE account=? ORDER BY day,order_no', (account,)).fetchall()
    return tuple(found)


def _live(root, account, signature):
    found = _live_records(str(root), account, signature)
    times = days_order_times(root, account, {stamp[:10] for stamp, *_ in found if 'T00:00:00' in stamp})
    result = []
    for stamp, symbol, side, qty, price, number, recorded in found:
        submitted = times.get(stamp[:10], {}).get(number) if 'T00:00:00' in stamp else None
        result.append({'time': submitted or stamp, 'symbol': symbol, 'side': side, 'qty': qty, 'price': price,
                       'order_no': number, 'time_basis': '제출 기록' if submitted else '주문 시각',
                       'recorded': recorded})
    return result


def _book(account, records, checkpoints=()):
    held, averages, uncertain, result = {}, {}, set(), []
    live = account != 'paper-shared'
    checkpoint_index, trusted, previous = 0, not live, None
    last_observed: dict[str, datetime] = {}
    for record in records:
        symbol, side, qty, price = (record[key] for key in ('symbol', 'side', 'qty', 'price'))
        stamp = str(record.get('time') or '')
        try:
            clock = datetime.fromisoformat(stamp)
            unknown_time = len(stamp) < 19 or (account != 'paper-shared' and stamp[11:19] == '00:00:00')
            clock = clock.astimezone(KST) if clock.tzinfo else clock.replace(tzinfo=KST)
            if not unknown_time:
                stamp = clock.isoformat()
        except ValueError:
            unknown_time = True
            clock = None
        if live and unknown_time:
            trusted = False
            held.clear()
            averages.clear()
            uncertain.clear()
            while checkpoint_index < len(checkpoints) and (clock is None or checkpoints[checkpoint_index].date() <= clock.date()):
                checkpoint_index += 1
        elif live and clock is not None:
            if previous is not None and clock < previous:
                trusted = False
                held.clear()
                averages.clear()
                uncertain.clear()
            while checkpoint_index < len(checkpoints) and checkpoints[checkpoint_index] < clock:
                checkpoint = checkpoints[checkpoint_index]
                checkpoint_index += 1
                if previous is None or checkpoint > previous:
                    trusted = True
                    held.clear()
                    averages.clear()
                    last_observed = {code: seen for code, seen in last_observed.items() if seen >= checkpoint}
                    uncertain = set(last_observed)
            previous = max(previous, clock) if previous is not None else clock
        if live:
            try:
                observed = datetime.fromisoformat(record.get('recorded') or '')
                observed = observed.astimezone(KST) if observed.tzinfo else observed.replace(tzinfo=KST)
            except ValueError:
                pass
            else:
                last_observed[symbol] = max(last_observed.get(symbol, observed), observed)
        before = held.get(symbol, 0)
        if side == 'SELL' and qty > before:
            uncertain.add(symbol)
        new_qty, average, cost, pnl, rate = apply_fill(before, averages.get(symbol, 0.0), side, qty, price)
        held[symbol], averages[symbol] = new_qty, average
        if not trusted or symbol in uncertain:
            cost = pnl = rate = None
        result.append({**record, 'time': stamp, 'account': account, 'id': f"{account}:{stamp[:10]}:{record['order_no']}",
                       'amount': qty * price, 'cost': cost, 'pnl': pnl, 'return_pct': rate,
                       'time_basis': '시각 미확인' if unknown_time else record.get('time_basis', '원장 기록'),
                       'time_known': not unknown_time})
    return result


@lru_cache(maxsize=1)
def _paper_book(root, signature):
    return tuple(_book('paper-shared', _paper(Path(root))))


@lru_cache(maxsize=2)
def _live_book(root, account, signatures, checkpoints):
    return tuple(_book(account, _live(Path(root), account, signatures[0]), checkpoints))


@lru_cache(maxsize=1)
def _load(root, signatures):
    live_keys = []
    for account in ACCOUNTS[1:]:
        checkpoints = []
        for snapshot in balances(root, account):
            if (type(snapshot.get('invested')) in (int, float) and snapshot['invested'] == 0
                    and type(snapshot.get('evaluation')) in (int, float) and snapshot['evaluation'] == 0):
                stamp = datetime.fromisoformat(snapshot['time'])
                checkpoints.append(stamp.astimezone(KST) if stamp.tzinfo else stamp.replace(tzinfo=KST))
        found = _live_records(root, account, signatures[1])
        time_sources = signatures[2:5] if any('T00:00:00' in row[0] for row in found) else ()
        live_keys.append((account, (signatures[1], *time_sources), tuple(sorted(checkpoints))))
    return _merged(root, signatures[0], tuple(live_keys))


@lru_cache(maxsize=1)
def _merged(root, paper_signature, live_keys):
    records = list(_paper_book(root, paper_signature))
    for account, signatures, checkpoints in live_keys:
        records.extend(_live_book(root, account, signatures, checkpoints))
    return tuple(sorted(records, key=lambda r: (str(r['time']), r['account'], str(r['order_no'])), reverse=True))


def revision(root):
    root = Path(root)
    related = ('live-follow.jsonl', 'longterm-live.jsonl', 'live-balance-live-1.jsonl', 'live-balance-live-2.jsonl')
    return freshness_revision(root) + tuple(_signature(root / name) for name in related)


def freshness_revision(root):
    return tuple(_signature(Path(root) / name) for name in TRADE_STORES)


def load(root):
    """Return the three accounts' recorded fills, newest first. Invalid databases raise errors."""
    with _load_lock:
        return _load(str(Path(root).resolve()), revision(root))


def _matching_indices(rows, accounts=ACCOUNTS, start=None, end=None, time_start=None, time_end=None, side=None):
    start, end = str(start or '0000-01-01'), str(end or '9999-12-31')
    start_clock = str(time_start)[:5] if time_start is not None else None
    end_clock = str(time_end)[:5] if time_end is not None else None
    if start > end or (start_clock and end_clock and start_clock > end_clock):
        raise ValueError('시작은 종료보다 늦을 수 없습니다')
    account_set = set(accounts)
    for index, row in enumerate(rows):
        stamp = str(row['time'])
        if row['account'] not in account_set or not start <= stamp[:10] <= end or (side and row['side'] != side):
            continue
        if start_clock or end_clock:
            if not row['time_known']:
                continue
            minute = stamp[11:16]
            if (start_clock and minute < start_clock) or (end_clock and minute > end_clock):
                continue
        yield index


def select(rows, accounts=ACCOUNTS, start=None, end=None, time_start=None, time_end=None, side=None):
    """Filter inclusive dates and minute window. Unknown times remain visible without a time filter."""
    return tuple(iter_selected(rows, accounts, start, end, time_start, time_end, side))


def iter_selected(rows, accounts=ACCOUNTS, start=None, end=None, time_start=None, time_end=None, side=None):
    return (rows[index] for index in _matching_indices(rows, accounts, start, end, time_start, time_end, side))


def _open_query(root, filters, leased):
    """Capture a bounded shared result and return its opaque handle and stable paged view."""
    root = str(Path(root).resolve())
    changes = 0
    while changes < 3:
        current_revision = revision(root)
        key = (root, filters, current_revision)
        with _query_lock:
            token = _query_keys.get(key)
            if token in _queries:
                _queries.move_to_end(token)
                return (_take_lease(token) if leased else token), _queries[token][1]
            pending = _query_builds.get(key)
            owner = pending is None
            if owner:
                pending = _query_builds[key] = Event()
        if not owner:
            pending.wait()
            continue
        try:
            rows = load(root)
            indices = array('Q', _matching_indices(rows, *filters))
            if current_revision != revision(root):
                changes += 1
                continue
            with _query_lock:
                token = uuid4().hex
                query = TradeQuery(rows, indices)
                _queries[token] = (key, query)
                _query_keys[key] = token
                handle = _take_lease(token) if leased else token
                _trim_queries()
                return handle, query
        finally:
            with _query_lock:
                del _query_builds[key]
                pending.set()
    raise OSError('원장이 조회 중 계속 변경되어 결과를 고정하지 못했습니다.')


def open_query(root, filters):
    return _open_query(root, filters, False)


def acquire_query(root, filters):
    """Pin a shared result for the lifetime of the returned session lease."""
    return _open_query(root, filters, True)


def get_query(token):
    """Return the captured result or signal expiry without switching the underlying records."""
    with _query_lock:
        try:
            query = _queries[token][1]
        except KeyError:
            raise QueryExpired('조회 결과가 만료됐습니다. 새 기록 확인을 눌러 주세요.') from None
        _queries.move_to_end(token)
        return query


def _summaries(rows, grouped):
    def empty():
        return {'buys': 0, 'sells': 0, 'buy_amount': 0, 'sell_amount': 0,
                'realized': None, 'return_pct': None, 'known_sells': 0,
                'wins': 0, 'losses': 0, 'flat': 0, '_cost': 0, '_profit': 0}

    totals = {} if grouped else {None: empty()}
    for row in rows:
        key = row['account'] if grouped else None
        if key not in totals:
            totals[key] = empty()
        out = totals[key]
        if row['side'] == 'BUY':
            out['buys'] += 1
            out['buy_amount'] += row['amount']
        elif row['side'] == 'SELL':
            out['sells'] += 1
            out['sell_amount'] += row['amount']
            if row['pnl'] is not None:
                out['known_sells'] += 1
                out['_cost'] += row['cost']
                out['_profit'] += row['pnl']
                out['wins' if row['pnl'] > 0 else 'losses' if row['pnl'] < 0 else 'flat'] += 1
    for out in totals.values():
        cost, profit = out.pop('_cost'), out.pop('_profit')
        if grouped:
            out['sold_cost'] = cost
        if out['known_sells']:
            out['realized'] = profit
            out['return_pct'] = profit / cost * 100 if cost else None
    return totals


def summary(rows):
    return _summaries(rows, False)[None]


def summary_by_account(rows):
    return _summaries(rows, True)


def _balances(path):
    import json
    values = []
    try:
        with Path(path).open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if plausible(row) and isinstance(row.get('time'), str):
                    try:
                        datetime.fromisoformat(row['time'])
                    except ValueError:
                        continue
                    values.append(row)
    except FileNotFoundError:
        return ()
    def in_kst(row):
        stamp = datetime.fromisoformat(row['time'])
        return stamp.astimezone(KST) if stamp.tzinfo else stamp.replace(tzinfo=KST)

    return tuple(sorted(values, key=in_kst))


def balances(root, account):
    root = Path(root)
    path = (root / 'shared-etf-paper' / 'account-snapshots.jsonl' if account == 'paper-shared'
            else root / f'live-balance-{account}.jsonl')
    if account not in ACCOUNTS:
        raise ValueError('지원하지 않는 계좌입니다')
    path = str(path.resolve())
    with _balance_lock:
        for _ in range(3):
            signature = _signature(path)
            cached = _balance_cache.get(path)
            if cached and cached[0] == signature:
                _balance_cache.move_to_end(path)
                return cached[1]
            values = _balances(path)
            if signature == _signature(path):
                _balance_cache[path] = (signature, values)
                _balance_cache.move_to_end(path)
                if len(_balance_cache) > 16:
                    _balance_cache.popitem(last=False)
                return values
        raise OSError('잔고 기록이 조회 중 계속 변경되어 결과를 고정하지 못했습니다.')
