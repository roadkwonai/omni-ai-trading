"""What Jev is shown beyond the last ten minutes, and why it answered as it did (user 2026-11-18).

1. Session context: every Jev call merges its 1-minute bars into today's per-chart history (backfilled once a day from
   the runner's logged `market_bars`), so the chart can show the last hour and `intraday_summary` can give the session
   so far (open, high, low, VWAP, where the price sits in today's range, 30/60-minute change). Before this Jev saw only
   ten 1-minute closes and could not tell a fresh move from one at the day's high.
2. Evidence: Jev answers choice questions only (a text or number question is refused), so the reasons are asked as
   extra choice questions in the same request: what drove the answer, which way each input points, and whether the
   data was enough. They are recorded next to the model input (research_store.record_evidence) and in the runner's
   events; they never change the trading answer, and a missing or malformed one is simply left out.
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
EVENTS = Path.home() / '.vibe-trading/nhplug/shared-etf-paper/events.jsonl'
CHART_MINUTES = 60
FIELDS = ('bsop_time', 'stck_oprc', 'stck_hgpr', 'stck_lwpr', 'stck_prpr', 'vol')
_DAY = {'day': None, 'bars': {}, 'quotes': {}}
QUOTE_TTL = 120  # seconds NH's session figures for a chart stand in the summary (the careful tape, then its Jev review)

NOTE = ('intraday_summary is each chart\'s session so far from completed 1-minute bars: open, high, low, last, % from '
        'the open/high/low, VWAP, where the price sits in today\'s range (0 = low, 100 = high) and the 30/60-minute '
        'change; the charts show up to the last 60 minutes. Judge whether a move is fresh or already extended.')

DIRECTION = {'UP': 'the Korean market (KOSPI 200) rising', 'DOWN': 'the Korean market falling (inverse ETFs gain)',
             'MIXED': 'mixed or flat', 'NONE': 'missing, stale or not relevant'}
EVIDENCE = {
    'why_driver': ('Which single input decided your answer to the main question most?', {
        'CHART': '1-minute charts', 'INTRADAY': 'intraday_summary (session range, VWAP, change)',
        'NEWS': 'recent_news_headlines', 'REGIME': 'market_regime_themes_global regime (20/60-day trend)',
        'TODAY': "market_regime_themes_global today's tone, index flows and themes",
        'GLOBAL': 'overseas markets (global_last_daily_change_pct)', 'FLOWS': 'investor_daily_net_qty per ETF',
        'PREMARKET': 'premarket_context', 'HISTORY': 'own_track_record and trading_journal',
        'STOCK': 'watchlist_details or stock facts', 'POSITION': 'position (return, stop, target, peak)',
        'PLAYBOOK': 'market_playbook checks',
        'NOTHING': 'nothing was convincing'}),
    # 11/18 12:21: 'charts' was read as the picked inverse's own chart (INV picked with chart UP); name the market chart
    'why_chart': ('Where do the K200 (KODEX 200) chart and its intraday_summary point for the Korean market over the next '
                  '30 minutes? Judge the market, not an inverse ETF\'s own chart.', DIRECTION),
    'why_news': ('Where do recent_news_headlines point for Korean stocks today?', DIRECTION),
    'why_market': ("Where do market_regime_themes_global (today's tone, flows, themes, overseas) point today?", DIRECTION),
    'why_flows': ('Where do the investor flows point?', DIRECTION),
    'why_history': ('What do own_track_record and trading_journal suggest for this decision?', {
        'SUPPORT': 'they support the answer', 'AGAINST': 'they argue against it', 'NONE': 'no bearing'}),
    'why_data': ('Was the data enough for a confident decision?', {
        'ENOUGH': 'enough and consistent', 'CONFLICT': 'inputs conflict', 'CHART_SHORT': 'the chart window is too short',
        'NEWS_STALE': 'news is stale or generic', 'MISSING': 'key data missing (flows, market summary, stock facts)'}),
}


def _day(now):
    return (now or datetime.now(KST)).astimezone(KST).strftime('%Y%m%d')


def _backfill(day, path):
    """Today's bars from the runner's logged market_bars (one pass a day per process); a failure leaves it empty."""
    iso = f'{day[:4]}-{day[4:6]}-{day[6:]}'
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                if '"market_bars"' not in line or f'"time": "{iso}' not in line:
                    continue
                try:
                    bars = json.loads(line).get('market_bars')
                except ValueError:
                    continue
                if isinstance(bars, dict):
                    _merge(bars)
    except OSError:
        pass


def _merge(bars):
    for name, rows in bars.items():
        if not isinstance(rows, list):
            continue
        kept = _DAY['bars'].setdefault(name, {})
        for row in rows:
            if isinstance(row, dict) and (row.get('bsop_date') not in (None, _DAY['day']) or row.get('market') == 'NXT'):
                continue  # the opening review's previous-session rows (11-19) are charted, never kept as today's
            if isinstance(row, dict) and isinstance(row.get('bsop_time'), str) and all(k in row for k in FIELDS):
                kept[row['bsop_time']] = {k: row[k] for k in FIELDS}


def remember(bars, now=None, events=None):
    """Merge one call's bars (newest first, valid_bars rows) into today's history."""
    day = _day(now)
    if _DAY['day'] != day:
        _DAY.update(day=day, bars={}, quotes={})
        _backfill(day, events or EVENTS)
    if isinstance(bars, dict):
        _merge(bars)


def note_quote(name, quote):
    """NH's session figures for one chart (get_stock_price 시가 stck_oprc, 고가 stck_hgpr, 저가 stck_lwpr, 가중가
    wghn_avrg_prc) for the next QUOTE_TTL seconds; None (or a quote without them) forgets them. The bars a process saw
    can start late in the session (a stock that joined the watchlist at 10:00), so they alone miss an early run (r3 E3)."""
    figures = {k: _num((quote or {}).get(f)) for k, f in (('open', 'stck_oprc'), ('high', 'stck_hgpr'), ('low', 'stck_lwpr'),
                                                           ('vwap', 'wghn_avrg_prc'))}
    if all(figures[k] and figures[k] > 0 for k in ('open', 'high', 'low')):
        _DAY.setdefault('quotes', {})[name] = (time.monotonic(), figures)
    else:
        _DAY.setdefault('quotes', {}).pop(name, None)


def chart_rows(name, minutes=CHART_MINUTES):
    """The last `minutes` bars of one chart, newest first (valid_bars order); [] when unknown."""
    kept = _DAY['bars'].get(name) or {}
    return [kept[t] for t in sorted(kept, reverse=True)[:minutes]]


def _num(value):
    try:
        out = float(str(value).replace(',', ''))
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def summary(name):
    """The session so far of one chart; None with under 10 bars (e.g. a stock first seen a few minutes ago)."""
    kept = _DAY['bars'].get(name) or {}
    rows = [kept[t] for t in sorted(kept)]
    bars = [(t, _num(r['stck_oprc']), _num(r['stck_hgpr']), _num(r['stck_lwpr']), _num(r['stck_prpr']), _num(r['vol']) or 0)
            for t, r in ((r['bsop_time'], r) for r in rows)]
    bars = [b for b in bars if None not in b[1:5] and b[4] > 0]
    if len(bars) < 10:
        return None
    first, last = bars[0], bars[-1]
    opened, high, low, price = first[1], max(b[2] for b in bars), min(b[3] for b in bars), last[4]
    noted = (_DAY.get('quotes') or {}).get(name)
    session = noted[1] if noted and time.monotonic() - noted[0] < QUOTE_TTL else None
    if session:  # NH's whole session; a bar newer than the quote can still extend the range
        opened, high, low = session['open'], max(high, session['high']), min(low, session['low'])
    volume = sum(b[5] for b in bars)
    pct = lambda a, b: round((a / b - 1) * 100, 2) if b else None  # noqa: E731
    out = {'from': first[0][:4], 'to': last[0][:4], 'bars': len(bars), 'open': opened, 'high': high, 'low': low, 'last': price,
           'vs_open_pct': pct(price, opened), 'from_high_pct': pct(price, high), 'from_low_pct': pct(price, low),
           'range_position_pct': round((price - low) / (high - low) * 100) if high > low else 50}
    vwap = session['vwap'] if session and session['vwap'] else sum(b[4] * b[5] for b in bars) / volume if volume else None
    if vwap:
        out.update(vwap=round(vwap, 2), vs_vwap_pct=pct(price, vwap))
    if session:
        out['session_source'] = 'nh_quote'
        if not session['vwap']:
            out['vwap_partial'] = True  # NH sent no 가중가: VWAP of the bars seen only
    for minutes in (30, 60):
        if len(bars) > minutes:
            out[f'change_{minutes}m_pct'] = pct(price, bars[-1 - minutes][4])
    return out


def context(bars, now=None, events=None):
    """(chart bars for plotting, state additions) for one Jev call."""
    remember(bars, now, events)
    # rows from another day (the opening review: today's few bars, then the previous session's) are charted as given
    chart = {name: rows if any(isinstance(r, dict) and (r.get('bsop_date') not in (None, _DAY['day']) or r.get('market') == 'NXT')
                               for r in rows or [])
             else chart_rows(name) or rows
             for name, rows in bars.items()}
    summaries = {name: s for name in bars if (s := summary(name))}
    return chart, ({'intraday_summary': summaries, 'intraday_note': NOTE} if summaries else {})


def questions():
    return {key: {'type': 'choice', 'instructions': text, 'criteria': dict(choices)} for key, (text, choices) in EVIDENCE.items()}


def parse(answers):
    """{question: {choice, confidence, probabilities}} of the valid evidence answers; {} when none."""
    out = {}
    for key, (_, choices) in EVIDENCE.items():
        answer = answers.get(key) if isinstance(answers, dict) else None
        if not isinstance(answer, dict) or answer.get('choice') not in choices:
            continue
        confidence = answer.get('confidence')
        if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            continue
        item = {'choice': answer['choice'], 'confidence': round(confidence, 3)}
        probabilities = answer.get('probabilities')
        if isinstance(probabilities, dict):
            item['probabilities'] = {k: round(v, 3) for k, v in probabilities.items() if k in choices and type(v) in (int, float)}
        out[key] = item
    return out
