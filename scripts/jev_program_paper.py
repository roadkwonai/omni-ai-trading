"""Isolated Jev experiment for stock program flow. Reads quotes and simulates fills in memory."""
from __future__ import annotations

import argparse
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
import json
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from etf_universe import is_code
from session_times import shifted
from src.config.paths import get_runtime_root

KST = ZoneInfo('Asia/Seoul')
RESEARCH = get_runtime_root() / 'nhplug/jev-program-paper'
MINUTE = timedelta(minutes=1)
WINDOW_MINUTES = 5
WINDOW = WINDOW_MINUTES * MINUTE
MAX_TAPE_LAG = MINUTE
MAX_WINDOW_SKEW = timedelta(seconds=30)
ENTRY_CUTOFF, FLATTEN = '14:30', '15:00'
NET_SHARE_FLOOR = 0.10
VOLUME_RATIO_FLOOR = 1.2
PRICE_MOVE_FLOOR = 0.0015


@dataclass(frozen=True)
class ProgramPoint:
    at: datetime
    bought: int
    sold: int

    @property
    def net(self):
        return self.bought - self.sold


def clock(day, hhmm):
    return datetime.combine(day, time.fromisoformat(hhmm), KST)


def quote_capture(symbol, at, rows, bars):
    return {'symbol': symbol, 'day': at.date().isoformat(), 'captured_at': at.isoformat(), 'program': rows,
            'bars': [[stamp.isoformat(), *values] for stamp, *values in bars]}


@lru_cache(maxsize=16)
def session_bounds(day):
    return tuple(clock(day, shifted(hhmm, day)) for hhmm in ('09:00', '15:20'))


def program_points(rows, day):
    """Validate Kiwoom cumulative shares, keeping an unambiguous point per timestamp."""
    points = {}
    start, end = session_bounds(day)
    for row in rows:
        try:
            hhmmss = row['tm']
            if not isinstance(hhmmss, str) or len(hhmmss) != 6 or not hhmmss.isascii() or not hhmmss.isdecimal():
                continue
            at = datetime.strptime(hhmmss, '%H%M%S').time()
            at = datetime.combine(day, at, KST)
            bought, sold, net = (int(str(row[k]).replace(',', '')) for k in
                                 ('prm_buy_qty', 'prm_sell_qty', 'prm_netprps_qty'))
        except (KeyError, ValueError, TypeError):
            continue
        if (row.get('stex_tp') not in (None, '', 'KRX') or not start <= at < end
                or min(bought, sold) < 0 or bought - sold != net):
            continue
        point = ProgramPoint(at, bought, sold)
        if at not in points:
            points[at] = point
        elif points[at] != point:
            points[at] = None
    return tuple(p for _, p in sorted(points.items()) if p is not None)


def completed_bars(rows, day):
    """Normalize Kiwoom one-minute bars, whose timestamps are the starts of their minutes."""
    start, end = session_bounds(day)
    bars = {}
    for stamp, opened, high, low, close, volume in rows:
        if stamp.utcoffset() is None:
            continue
        stamp = stamp.astimezone(KST)
        if (start <= stamp < end and stamp.second == stamp.microsecond == 0
                and all(type(v) in (int, float) and math.isfinite(v) for v in (opened, high, low, close, volume))
                and 0 < low <= min(opened, close) <= max(opened, close) <= high and volume >= 0):
            bar = (stamp, opened, high, low, close, volume)
            if stamp not in bars:
                bars[stamp] = bar
            elif bars[stamp] != bar:
                bars[stamp] = None
    return {stamp: bar for stamp, bar in bars.items() if bar is not None}


class Tape:
    def __init__(self, day, rows, bars):
        self.day = day
        self.cutoff, self.flatten = (clock(day, shifted(hhmm, day)) for hhmm in (ENTRY_CUTOFF, FLATTEN))
        self.points = program_points(rows, day)
        self.stamps = tuple(p.at for p in self.points)
        self.reset_stamps = tuple(after.at for before, after in zip(self.points, self.points[1:])
                                  if after.bought < before.bought or after.sold < before.sold)
        self.bars = completed_bars(bars, day)

    def point_at(self, at):
        idx = bisect_right(self.stamps, at) - 1
        if idx >= 0 and at - self.stamps[idx] <= MAX_TAPE_LAG:
            return self.points[idx]
        return None

    def signal(self, at):
        """Use only completed bars and program rows observable at the decision boundary."""
        end = at.astimezone(KST).replace(second=0, microsecond=0)
        last, first = self.point_at(end), self.point_at(end - WINDOW)
        if last is None or first is None:
            return {'status': 'program_missing_or_stale'}
        result = {'program_as_of': last.at.isoformat(), 'program_from': first.at.isoformat(),
                  'buy_shares': last.bought, 'sell_shares': last.sold, 'net_shares': last.net,
                  'recent_net_shares': last.net - first.net}
        if abs(last.at - first.at - WINDOW) > MAX_WINDOW_SKEW:
            return {**result, 'status': 'program_window_misaligned'}
        if bisect_right(self.reset_stamps, first.at) != bisect_right(self.reset_stamps, last.at):
            return {**result, 'status': 'program_cumulative_reset'}
        rows = [self.bars.get(end - i * MINUTE) for i in range(2 * WINDOW_MINUTES, 0, -1)]
        if not all(rows):
            return {**result, 'status': 'chart_window_missing'}
        prior = sum(r[5] for r in rows[:WINDOW_MINUTES])
        recent = sum(r[5] for r in rows[WINDOW_MINUTES:])
        if not prior or not recent:
            return {**result, 'status': 'volume_missing'}
        buy = last.bought - first.bought
        sell = last.sold - first.sold
        if max(buy, sell) > recent:
            return {**result, 'status': 'program_volume_mismatch'}
        price, base = rows[-1][4], rows[WINDOW_MINUTES - 1][4]
        move = (price - base) / base
        local_vwap = sum(r[4] * r[5] for r in rows) / (prior + recent)
        share, volume_ratio = (buy - sell) / recent, recent / prior
        direction = 'neutral'
        if volume_ratio >= VOLUME_RATIO_FLOOR:
            if share >= NET_SHARE_FLOOR and move >= PRICE_MOVE_FLOOR and price > local_vwap:
                direction = 'buy_pressure'
            elif share <= -NET_SHARE_FLOOR and move <= -PRICE_MOVE_FLOOR and price < local_vwap:
                direction = 'sell_pressure'
        return {**result, 'status': 'ready', 'direction': direction, 'recent_buy_shares': buy,
                'recent_sell_shares': sell, 'recent_volume_shares': recent,
                'recent_net_volume_pct': round(share * 100, 2), 'volume_vs_previous_5m': round(volume_ratio, 3),
                'price_change_5m_pct': round(move * 100, 3), 'price_vs_10m_vwap_pct': round((price / local_vwap - 1) * 100, 3)}

    def chart(self, at):
        done = [self.bars[t] for t in sorted(self.bars, reverse=True) if t + MINUTE <= at][:60]
        return [dict(bsop_time=stamp.strftime('%H%M%S'), stck_oprc=opened, stck_hgpr=high, stck_lwpr=low, stck_prpr=close, vol=volume)
                for stamp, opened, high, low, close, volume in done]


def proposed_action(signal, held):
    if signal.get('direction') == 'sell_pressure':
        return 'review_exit' if held else 'defer_entry'
    if signal.get('direction') == 'buy_pressure' and not held:
        return 'review_entry'
    return 'hold' if held else 'wait'


def simulated_fill(tape, at, action, until):
    if action not in ('review_entry', 'review_exit', 'session_flatten'):
        return None
    if action == 'review_entry' and at >= tape.cutoff:
        return None
    if at > tape.flatten:
        return None
    fill_at = at + MINUTE
    bar = tape.bars.get(fill_at)
    if bar and fill_at + MINUTE <= until:
        return {'time': fill_at.isoformat(), 'signal_at': at.isoformat(), 'side': 'buy' if action == 'review_entry' else 'sell',
                'price': bar[1], 'reason': action}
    return None


def replay(tape, until, cost_bps):
    """Rule-only one-share simulation. The next observed minute's open prices a hypothetical fill."""
    start, close = session_bounds(tape.day)
    steps, fills, held = {}, [], False
    at = start + 2 * WINDOW
    last_review = min(until - 2 * MINUTE, close - 2 * MINUTE, tape.flatten)
    while at <= last_review:
        signal = tape.signal(at)
        status = signal.get('direction', signal['status'])
        steps[status] = steps.get(status, 0) + 1
        action = 'session_flatten' if held and at == tape.flatten else proposed_action(signal, held)
        if fill := simulated_fill(tape, at, action, until):
            fills.append(fill)
            held = not held
        at += MINUTE
    pairs = list(zip(fills[::2], fills[1::2]))
    fee = cost_bps / 10_000
    pnl = sum(s['price'] * (1 - fee) - b['price'] * (1 + fee) for b, s in pairs)
    return {'minutes': steps, 'fills': fills, 'closed_trades': len(pairs), 'open_shares': int(held),
            'realized_net_krw': round(pnl, 2) if pairs else None, 'assumed_cost_bps_per_side': cost_bps}


def ask_jev(symbol, tape, at, signal, held):
    """One separate Jev judgment, using the same chart transport as the runner."""
    from nh_etf_auto_v2 import _jev7_question

    if signal['status'] != 'ready':
        return {'status': 'insufficient_data'}
    question = 'program_holding' if held else 'program_entry'
    criteria = ({'HOLD': 'hold or need more evidence', 'SELL': 'review exiting the simulated holding'} if held else
                {'WAIT': 'wait or need more evidence', 'BUY': 'review entering a simulated position'})
    RESEARCH.mkdir(parents=True, exist_ok=True, mode=0o700)
    answer = _jev7_question({symbol: tape.chart(at)}, [], question, criteria,
                            instructions=('This is an isolated paper-trading review. Read the stock chart and symbol_program_flow. '
                                          'Use recent signed program flow, price and volume together. Look at the recent change '
                                          'separately from the cumulative net. A buy requires buy_pressure, and an early sale '
                                          'requires sell_pressure. If the setup conflicts or is uncertain, choose WAIT or HOLD.'),
                            extra_state={'symbol': symbol, 'analysis_at': at.isoformat(), 'simulated_holding': held,
                                         'symbol_program_flow': signal},
                            program_research_db=RESEARCH / 'judgments.sqlite3')
    response = answer[question]
    rule = proposed_action(signal, held)
    action = ({'BUY': 'review_entry', 'SELL': 'review_exit'}.get(response['choice']) or ('hold' if held else 'wait'))
    if action != rule:
        action = 'defer_entry' if rule == 'defer_entry' else 'hold' if held else 'wait'
    return {'status': 'reviewed', 'answer': response, 'action': action, 'input_digest': answer['input_digest']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('symbol', help='6-character KRX code')
    parser.add_argument('--input', type=Path, help='Replay an explicitly saved quote capture')
    parser.add_argument('--save', type=Path, help='Write the current quote capture to a new private research file')
    parser.add_argument('--at', help='Decision time in KST, HH:MM')
    parser.add_argument('--cost-bps', type=float, default=15, help='Simulation cost per side in basis points')
    parser.add_argument('--ask-jev', action='store_true', help='After the regular session, request one separate Jev judgment at --at')
    parser.add_argument('--held', action='store_true', help='Review a simulated holding instead of an entry')
    args = parser.parse_args()
    if not is_code(args.symbol) or not args.symbol.isdecimal() or not math.isfinite(args.cost_bps) or not 0 <= args.cost_bps < 10_000:
        parser.error('invalid symbol or cost')
    if args.input and args.save:
        parser.error('--save is for a new quote capture')
    if args.at and not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', args.at):
        parser.error('--at must use HH:MM in KST')
    now = datetime.now(KST)
    if args.ask_jev and (not args.at or now < session_bounds(now.date())[1]):
        parser.error('--ask-jev requires --at and runs after the regular session')
    if args.input:
        data = json.loads(args.input.read_text(encoding='utf-8'))
        if 'captures' in data:
            data = data['captures'].get(args.symbol) or {}
            if 'bars' not in data:
                parser.error('no usable quote capture for this symbol in the audit')
        if data['symbol'] != args.symbol:
            parser.error('capture belongs to another symbol')
        day = date.fromisoformat(data['day'])
        known = datetime.fromisoformat(data['captured_at'])
        if known.utcoffset() is None:
            parser.error('capture timestamp must contain a timezone')
        known = known.astimezone(KST)
        if known.date() != day or known > now:
            parser.error('invalid capture date')
        rows = data['program']
        bars = [(datetime.fromisoformat(t), *values) for t, *values in data['bars']]
    else:
        from kiwoom_data import day_bars, stock_program_rows

        day = now.date()
        if now < session_bounds(day)[0]:
            print(json.dumps({'symbol': args.symbol, 'day': day.isoformat(), 'status': 'before_regular_session'}))
            return
        # Use the request start as the replay cutoff. A still-forming candle may be present in the answer.
        known = now
        try:
            rows = stock_program_rows(args.symbol, day, now=now)
            bars = day_bars(args.symbol, day, now=now)['bars']
        except Exception as exc:  # provider messages may echo request or authentication fields
            print(json.dumps({'symbol': args.symbol, 'day': day.isoformat(), 'status': 'quote_read_failed',
                              'error_type': type(exc).__name__}))
            raise SystemExit(1) from None
        if args.save:
            import os

            with os.fdopen(os.open(args.save, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
                json.dump(quote_capture(args.symbol, known, rows, bars), stream)
    tape = Tape(day, rows, bars)
    start, end = session_bounds(day)
    latest = min(known, end)
    at = clock(day, args.at) if args.at else latest
    if not start <= at <= latest:
        parser.error('--at must be within the captured regular session')
    signal = tape.signal(at)
    result = {'symbol': args.symbol, 'day': day.isoformat(), 'analysis_at': at.isoformat(), 'replay_through': latest.isoformat(), 'signal': signal,
              'action_to_review': proposed_action(signal, args.held), 'replay': replay(tape, latest, args.cost_bps)}
    if args.ask_jev:
        from research_store import record

        try:
            result['jev'] = ask_jev(args.symbol, tape, at, signal, args.held)
        except Exception as exc:  # preserve the quote replay even if the optional model is unavailable
            result['jev'] = {'status': 'review_failed', 'error_type': type(exc).__name__}
        result['jev']['simulated_fill'] = simulated_fill(tape, at, result['jev'].get('action'), latest)
        RESEARCH.mkdir(parents=True, exist_ok=True, mode=0o700)
        result['jev']['audit_saved'] = record('program_review', {'time': now.isoformat(), 'symbol': args.symbol,
                                                                 'analysis_at': at.isoformat(), 'review': result['jev']},
                                               path=RESEARCH / 'judgments.sqlite3')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
