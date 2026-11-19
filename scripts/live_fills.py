"""Confirmed live fills per live account, kept locally so realized P&L and trade history survive past today's broker view.

The live mirror (nh_live_follow) already reads the broker's daily order/execution list every minute; `record` stores each
order with a filled quantity (order number, symbol, side, filled quantity, average fill price, order time) under the
accounts.json key (never the account number). A later partial fill of the same order replaces its row. Read-only for
everything else: the dashboard and comparisons read `rows`.

    PYTHONPATH=agent:scripts .venv/bin/python scripts/live_fills.py --account live-1     # print the recorded fills
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from sqlite_ro import ro_connect

KST = ZoneInfo('Asia/Seoul')
NAME = 'live-fills.sqlite3'
SCHEMA = ('CREATE TABLE IF NOT EXISTS fills (account TEXT NOT NULL, day TEXT NOT NULL, order_no INTEGER NOT NULL, '
          'symbol TEXT NOT NULL, side TEXT NOT NULL, quantity INTEGER NOT NULL, price REAL NOT NULL, time TEXT NOT NULL, '
          'recorded TEXT NOT NULL, PRIMARY KEY (account, day, order_no))')


def _number(value):
    try:
        number = float(str(value).replace(',', ''))
    except (TypeError, ValueError):
        return None
    return number if number == number and number > 0 else None


def parse(history_rows, day):
    """Filled orders from krstockInquiryDailyOrderExecution rows of `day` (YYYYMMDD): [(order_no, symbol, side, qty,
    price, iso time)]. Rows without an order number, a filled quantity or an average price are skipped."""
    fills = []
    for row in history_rows or []:
        if not isinstance(row, dict):
            continue
        order_no, qty, price = row.get('itg_orr_no'), _number(row.get('tot_cns_qty')), _number(row.get('cns_avg_uit_pr'))
        symbol, side_name = row.get('iem_cd'), str(row.get('sby_dit_cd_nm') or '')
        raw = str(row.get('orr_tmd') or '').replace(':', '')
        if type(order_no) is not int or not qty or not price or not symbol or ('매수' not in side_name and '매도' not in side_name):
            continue
        clock = f'{raw[:2]}:{raw[2:4]}:{raw[4:6]}' if raw[:6].isdigit() else '00:00:00'
        stamp = f'{day[:4]}-{day[4:6]}-{day[6:8]}T{clock}+09:00'
        fills.append((order_no, symbol, 'BUY' if '매수' in side_name else 'SELL', int(qty), price, stamp))
    return fills


def record(root, account_key, history, now=None):
    """Store the filled orders of one broker history snapshot (the mirror's collect_snapshots()['history']).
    Returns how many rows were written; never raises (a failure must not stop the mirror)."""
    try:
        day = history.get('_day') or (now or datetime.now(KST)).strftime('%Y%m%d')
        fills = parse(history.get('Output_0'), day)
        if not fills:
            return 0
        stamp = datetime.now(KST).isoformat()
        with sqlite3.connect(Path(root) / NAME, timeout=5) as db:
            db.execute(SCHEMA)
            db.executemany('INSERT OR REPLACE INTO fills VALUES (?,?,?,?,?,?,?,?,?)',
                           [(account_key, day, *fill, stamp) for fill in fills])
        return len(fills)
    except Exception:
        return 0


SENT_LOGS = ('live-follow.jsonl', 'longterm-live.jsonl')  # the two live executors' records (time + guard intent)


def order_times(root, account_key, day):
    """{broker order number: ISO time} of one live account's orders sent on `day` (YYYY-MM-DD). NH's live order list has
    no time (orr_tmd is missing), so the executors' own submission records are joined to the guard's intents
    (fingerprint -> order number). Submission time, not fill time: the mirror's limit orders fill within the minute."""
    return days_order_times(root, account_key, {day}).get(day, {})


def days_order_times(root, account_key, days):
    """{day: order_times of that day} for every day in `days`, with one pass over each executor log (review 3 M7)."""
    if not days:
        return {}
    sent = {}  # (day, fingerprint) -> first submission time
    for name in SENT_LOGS:
        try:
            lines = (Path(root) / name).read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            if account_key not in line or '"intent"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            day = str(e.get('time', ''))[:10]
            if e.get('account') == account_key and isinstance(e.get('intent'), str) and day in days:
                sent.setdefault((day, e['intent']), e['time'])
    if not sent:
        return {}
    try:
        with ro_connect(Path(root) / 'live-orders.sqlite3', 2) as db:
            found = db.execute('SELECT day, fingerprint, order_number FROM live_intents WHERE order_number IS NOT NULL').fetchall()
    except sqlite3.Error:
        return {}
    out = {}
    for stamp, fingerprint, number in found:
        day = f'{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}' if isinstance(stamp, str) else None
        try:
            if (day, fingerprint) in sent:
                out.setdefault(day, {})[int(number)] = sent[(day, fingerprint)]
        except (TypeError, ValueError):
            continue
    return out


def rows(root, account_key):
    """[{time, symbol, side, qty, price, order_no}] of one live account, oldest first; [] when nothing was recorded.
    A fill stored without a clock (T00:00:00, NH gives none) takes its order's submission time when the executors
    recorded it."""
    path = Path(root) / NAME
    if not path.exists():
        return []
    try:
        with ro_connect(path, 5) as db:
            found = db.execute('SELECT time,symbol,side,quantity,price,order_no FROM fills WHERE account=? ORDER BY time, order_no',
                               (account_key,)).fetchall()
    except sqlite3.Error:
        return []
    times = days_order_times(root, account_key, {t[:10] for t, *_ in found if 'T00:00:00' in t})
    out = [{'time': times.get(t[:10], {}).get(n, t) if 'T00:00:00' in t else t, 'symbol': s, 'side': side, 'qty': q,
            'price': p, 'order_no': n} for t, s, side, q, p, n in found]
    return sorted(out, key=lambda r: (r['time'][:10], r['order_no']))  # order numbers rise through the day


BALANCE_MINUTES = 5


def balance_file(root, account_key):
    return Path(root) / f'live-balance-{account_key}.jsonl'


def record_balance(root, account_key, balance, now, every=BALANCE_MINUTES):
    """Append the account totals (cash, total assets, invested, evaluation) to live-balance-<key>.jsonl: the first read
    of the day, then every `every` minutes. Keyed by the accounts.json key, never the number. Total assets falls back
    to cash + evaluation when the broker leaves it out; a page without either is skipped rather than recorded wrong.
    Never raises: a logging failure must not stop the order cycle."""
    def num(value):  # 0 is a real balance here (all cash, or nothing held)
        try:
            out = float(str(value).replace(',', ''))
        except (TypeError, ValueError):
            return None
        return out if out == out and out >= 0 else None
    try:
        summary = (balance or {}).get('Output_0') or {}
        if isinstance(summary, list):
            summary = summary[0] if summary else {}
        row = {'time': now.isoformat(), 'cash': num(summary.get('orr_pbl_amt1')), 'total_assets': num(summary.get('tot_aet_amt')),
               'invested': num(summary.get('tot_byn_amt')), 'evaluation': num(summary.get('tot_eal_amt'))}
        if row['total_assets'] is None and row['cash'] is not None and row['evaluation'] is not None:
            row['total_assets'] = row['cash'] + row['evaluation']
        if row['total_assets'] is None:
            return False
        path = balance_file(root, account_key)
        try:
            last = json.loads(path.read_text().strip().splitlines()[-1])['time']
            if last[:10] == now.date().isoformat() and (now - datetime.fromisoformat(last)).total_seconds() < every * 60:
                return False
        except (OSError, ValueError, IndexError, KeyError, TypeError):
            pass
        with path.open('a') as stream:
            stream.write(json.dumps(row) + '\n')
        path.chmod(0o600)
        return True
    except Exception:
        return False


def balances(root, account_key, day):
    """Today's balance snapshots of one live account, oldest first ([] when none)."""
    try:
        lines = balance_file(root, account_key).read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if str(row.get('time', '')).startswith(day):
            out.append(row)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--account', required=True, help='accounts.json key, e.g. live-1')
    args = parser.parse_args()
    from src.config.paths import get_runtime_root
    print(json.dumps(rows(get_runtime_root() / 'nhplug', args.account), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
