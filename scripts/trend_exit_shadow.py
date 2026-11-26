"""Review the remainder of a trend position in the isolated audit."""
from datetime import timedelta
from math import isfinite

from fade_shadow import tape as chart_signals
from jev_program_paper import NET_SHARE_FLOOR

PROFIT_FLOOR = 0.8
SURGE_PROFIT = 5.0
SURGE_MOVE = 2.0
SURGE_GIVEBACK = 0.5
CONTINUATION_RATIO = 0.9


def number(value):
    return type(value) in (int, float) and isfinite(value)


def review(position, market, at, program, book=None, news=None):
    if position.get('exit_profile') != 'trend' or position.get('half_already_sold') is not True:
        return {'action': 'not_applicable'}
    avg, bid, peak = (position.get(key) for key in ('entry_average', 'bid', 'peak_bid'))
    if not all(number(x) and x > 0 for x in (avg, bid, peak)) or peak < bid:
        return {'action': 'inputs_missing'}
    end = at.replace(second=0, microsecond=0)
    minute = timedelta(minutes=1)
    rows = [market.bars.get(end - i * minute) for i in range(10, 0, -1)]
    if not all(rows):
        return {'action': 'inputs_missing'}
    chart = chart_signals(rows, end)
    prev = sum(r[5] for r in rows[:5])
    recent = sum(r[5] for r in rows[5:])
    turnover_before = sum(r[4] * r[5] for r in rows[:5])
    turnover_after = sum(r[4] * r[5] for r in rows[5:])
    if not prev or not recent or chart.get('pressure') is None:
        return {'action': 'inputs_missing'}
    volume_ratio = recent / prev
    turnover_ratio = turnover_after / turnover_before
    move = (rows[-1][4] - rows[4][4]) / rows[4][4] * 100
    profit = (bid - avg) / avg * 100
    giveback = (peak - bid) / peak * 100
    weak = chart['ma5_gap_pct'] < 0 and chart['pressure'] < 0
    flow = program.get('direction') if program.get('status') == 'ready' else None
    net_share = program.get('recent_net_volume_pct') if flow is not None else None
    program_sell = flow == 'sell_pressure' or (number(net_share) and net_share <= -100 * NET_SHARE_FLOOR)
    bid_size, ask_size = (book or {}).get('bid_qty'), (book or {}).get('ask_qty')
    ratio = bid_size / ask_size if number(bid_size) and number(ask_size) and bid_size >= 0 and ask_size > 0 else None
    corroborators = [name for name, flag in {
        'program_sell': program_sell, 'ask_heavy_book': ratio is not None and ratio < 0.8,
        'market_news_down': news == 'DOWN', 'active_selling': volume_ratio >= 1 and turnover_ratio >= 1,
    }.items() if flag]
    strong = (chart['ma5_gap_pct'] > 0 and chart['pressure'] > 0 and move > 0
              and volume_ratio >= CONTINUATION_RATIO and turnover_ratio >= CONTINUATION_RATIO and not program_sell
              and (ratio is None or ratio >= 0.8) and news != 'DOWN')
    action = 'keep_trailing'
    selling = program_sell or (ratio is not None and ratio < 0.8) or weak
    if profit >= SURGE_PROFIT and (giveback >= SURGE_GIVEBACK or (move >= SURGE_MOVE and selling)) and not strong:
        action = 'review_exit_surge'
    elif profit >= PROFIT_FLOOR and weak and corroborators:
        action = 'review_exit_weakening'
    return {'action': action, 'profit_pct': round(profit, 3), 'giveback_pct': round(giveback, 3),
            'move_5m_pct': round(move, 3), 'volume_ratio': round(volume_ratio, 3),
            'estimated_turnover_ratio': round(turnover_ratio, 3), 'chart': chart, 'program_direction': flow, 'program_net_volume_pct': net_share,
            'book_ratio': None if ratio is None else round(ratio, 3), 'market_news': news,
            'strong': strong, 'corroborators': corroborators}
