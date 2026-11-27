"""Compare unambiguous same-day paper and live round trips for research."""
from collections import defaultdict
from datetime import datetime
import math


def compare(books):
    paper = (books.get('paper') or {}).get('fills') or []
    by_book = {'paper': paper}
    by_book.update({key: [row for row in (book.get('fills') or []) if row.get('path') == '미러']
                    for key, book in books.items() if key.startswith('live-')})
    grouped = {}
    for key, fills in by_book.items():
        symbols = defaultdict(list)
        for fill in fills:
            if isinstance(fill, dict) and isinstance(fill.get('symbol'), str):
                symbols[fill['symbol']].append(fill)
        grouped[key] = symbols

    def round_trip(rows):
        if len(rows) != 2:
            return None
        buys = [r for r in rows if r.get('side') == 'BUY']
        sells = [r for r in rows if r.get('side') == 'SELL']
        if len(buys) != 1 or len(sells) != 1:
            return None
        buy, sell = buys[0], sells[0]
        if type(buy.get('qty')) is not int or buy['qty'] <= 0 or sell.get('qty') != buy['qty']:
            return None
        if any(type(row.get('price')) not in (int, float) or not math.isfinite(row['price']) or row['price'] <= 0
               for row in (buy, sell)):
            return None
        pnl = sell.get('pnl')
        calculated = (sell['price'] - buy['price']) * buy['qty']
        if type(pnl) not in (int, float) or not math.isfinite(pnl) or abs(pnl - calculated) > 1:
            return None
        try:
            buy_at, sell_at = datetime.fromisoformat(buy['time']), datetime.fromisoformat(sell['time'])
            if not buy_at.tzinfo or not sell_at.tzinfo or buy_at.date() != sell_at.date() or buy_at >= sell_at:
                return None
        except (KeyError, TypeError, ValueError):
            return None
        return {'buy_time': buy['time'], 'sell_time': sell['time'], 'qty': buy['qty'],
                'buy_price': buy['price'], 'sell_price': sell['price'],
                'gross_pnl': round((sell['price'] - buy['price']) * buy['qty'], 2)}

    result = []
    for key in sorted(k for k in grouped if k != 'paper'):
        for symbol in sorted(grouped['paper'].keys() & grouped[key].keys()):
            paper_trip = round_trip(grouped['paper'][symbol])
            live_trip = round_trip(grouped[key][symbol])
            if not paper_trip or not live_trip or paper_trip['qty'] != live_trip['qty']:
                continue
            result.append({'symbol': symbol, 'account': key, 'paper': paper_trip, 'live': live_trip,
                           'buy_price_difference': round(live_trip['buy_price'] - paper_trip['buy_price'], 2),
                           'sell_price_difference': round(live_trip['sell_price'] - paper_trip['sell_price'], 2),
                           'gross_pnl_difference': round(live_trip['gross_pnl'] - paper_trip['gross_pnl'], 2),
                           'live_time_note': 'live time may be submission time; actual fill time may be unavailable',
                           'costs': 'excluded', 'interpretation': 'observed prices; causality and repeatability unverified'})
    return result
