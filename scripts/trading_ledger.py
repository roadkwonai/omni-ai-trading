"""Import a sanitized paper-trading event log into local SQLite; never submits orders."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from etf_universe import SYMBOLS as ETF_SYMBOLS
from etf_universe import CODE_PATTERN
from src.tools.private_db import private_file

ROOT = Path.home() / '.vibe-trading/nhplug/etf-experiment'
EVENTS = ROOT / 'events.jsonl'
DATABASE = ROOT / 'trading-ledger.sqlite3'
SYMBOLS = set(ETF_SYMBOLS.values()) | {'005930', '035420', '035720'}
CHOICES = {'WAIT', 'HOLD', 'SELL', 'SAMSUNG', 'NAVER', 'KAKAO'} | set(ETF_SYMBOLS)
EVENT_TYPES = {'started', 'stopped', 'decision', 'market_context', 'jev_shadow', 'jev_error', 'jev_holding_decision', 'error', 'submitted', 'sell_submitted', 'order_status', 'buy_unfilled', 'roundtrip_verified', 'order_limit', 'v2_started', 'v2_stopped', 'v2_error', 'v2_submitted', 'v2_cancel_submitted', 'v2_order_status', 'v2_holding_decision', 'v2_holding_decision_error', 'v2_entry_gate', 'v2_risk_exit', 'v2_risk_exit_error', 'v2_execution_gate', 'v2_news_error', 'manual_exit', 'split_add', 'split_take_profit', 'strategy_settings', 'vibe_exit', 'exit_plan', 'overnight_hold', 'watchlist_strength', 'new_listing', 'direction_switch', 'oversold_scan', 'oversold_signal', 'oversold_v2_gate', 'oversold_v2_signal', 'strength_shadow',
               'oversold_research_failure', 'oversold_v2_research_failure', 'vibe_decision_intent', 'oversold_v2_gate_error', 'careful_reentry_review', 'pick_cooldown'}  # every journal_event name: an unknown one stops the runner (11/17 11:46 vibe)

REASONS = frozenset(('careful_reentry_chase', 'careful_reentry_weak', 'careful_reentry_no_session', 'careful_reentry_jev_wait',
    'careful_reentry_review_error', 'decision_wait', 'invalid_decision', 'confidence_below_threshold', 'invalid_bars', 'entry_conditions_met', 'trend_not_rising', 'jev_error', 'jev_model_mismatch', 'jev_invalid', 'jev_wait', 'jev_low_confidence', 'model_disagreement', 'order_limit', 'position_limit', 'learning_filter', 'insufficient_buying_power', 'stale_model_signal', 'stale_market_bars', 'signal_or_session_invalid', 'ai_settings_unavailable', 'models_disabled', 'opposite_direction_holding', 'order_guard_refused', 'single_order_limit', 'split_plan_holding', 'split_limit', 'split_history_unavailable', 'market_bars_unavailable', 'auto_off', 'vibe_no_buy', 'entry_window_closed', 'downtrend_no_averaging_down', 'stop_inside_spread', 'take_profit_all', 'stop_loss', 'breakeven_stop', 'session_flatten', 'ipo_time_stop', 'averaging_down_unknown', 'quote_not_fresh', 'trailing_stop', 'trend_upgrade', 'not_trend_profile', 'regime_not_rising', 'not_in_profit', 'overnight_limit', 'market_closed_next_day', 'trend_rising_in_profit', 'flows_not_supporting', 'volume_not_high', 'jev_not_supportive', 'closing_trade_supported', 'breadth_not_broad', 'not_first_target', 'weak_close', 'not_uptrend', 'vibe_negative', 'market_unconfirmed', 'direction_not_confirmed', 'volume_not_rising', 'losing_streak_cooldown', 'swing_continued', 'swing_days_exceeded', 'jev_selling', 'transient_read_failure', 'weak_market_targets', 'quantity_or_cash_limit',
    'add_count_limit', 'add_not_better', 'scale_signal_disabled', 'projected_exit', 'engine_unavailable', 'manual_after_flatten',
    'market_not_falling', 'opening_observation_only', 'quote_unavailable', 'regime_reversal', 'take_profit_history_unavailable',
    'oversold_hold_days',
    'inverse_regime_not_falling', 'inverse_index_not_falling', 'inverse_index_rebounding', 'inverse_flows_not_selling',
    'inverse_us_not_falling', 'inverse_market_unknown', 'add_cutoff', 'reentry_disabled', 'reentry_market_unconfirmed',
    'losing_streak_history_unavailable', 'oversold_limit', 'ipo_limit', 'direction_switch'))  # every code the runner journals (test: runner_codes)


def sanitize(event):
    if not isinstance(event, dict) or event.get('event') not in EVENT_TYPES or not isinstance(event.get('time'), str):
        raise ValueError('invalid event')
    timestamp = datetime.fromisoformat(event['time'])
    if timestamp.tzinfo is None or timestamp.utcoffset() is None: raise ValueError('event time missing timezone')
    result = {'time': timestamp.isoformat(), 'event': event['event']}
    response_time = event.get('model_response_time')
    if isinstance(response_time, str):
        try:
            parsed = datetime.fromisoformat(response_time)
            if parsed.tzinfo is not None and parsed.utcoffset() is not None and parsed <= timestamp:
                result['model_response_time'] = parsed.isoformat()
        except ValueError:
            pass
    if event.get('review_type') in ('preclose', 'regular'):
        result['review_type'] = event['review_type']
    if event.get('universe') == 'etf7':
        result['universe'] = 'etf7'
    if event.get('context') in ('entry', 'holding'):
        result['context'] = event['context']
    cycle_id = event.get('cycle_id')
    if isinstance(cycle_id, str) and re.fullmatch(r'[0-9a-f]{32}', cycle_id):
        result['cycle_id'] = cycle_id
    if event['event'] == 'v2_news_error' and event.get('error_type') in ('TimeoutError', 'OSError', 'ValueError', 'ParseError'):
        result['error_type'] = event['error_type']
    if event['event'] == 'v2_holding_decision_error' and event.get('error_type') in ('SignalOrSessionInvalid', 'StaleModelDecision', 'ValueError', 'TimeoutError', 'OSError', 'KeyError', 'TypeError'):
        result['error_type'] = event['error_type']
    if event.get('source') in ('decision', 'jev', 'consensus', 'watchlist_strength', 'new_listing', 'split', 'vibe-trading', 'oversold', 'oversold_v2', 'oversold_v2_add', 'oversold_v2_reentry', 'manual'):
        result['source'] = event['source']
    for key in ('choice', 'openai_choice'):
        if ((event.get(key) in CHOICES or (key == 'choice' and isinstance(event.get(key), str) and re.fullmatch(CODE_PATTERN, event[key]))) and
                (event['event'] != 'jev_holding_decision' or key != 'choice' or event[key] in ('HOLD', 'SELL'))):
            result[key] = event[key]
    if event.get('symbol') in SYMBOLS or (isinstance(event.get('symbol'), str) and re.fullmatch(CODE_PATTERN, event['symbol'])):
        result['symbol'] = event['symbol']
    if event.get('side') in ('Buy', 'Sell'): result['side'] = event['side']
    if event.get('reason') in REASONS:
        result['reason'] = event['reason']
    if type(event.get('permitted')) is bool: result['permitted'] = event['permitted']
    # 2026-11-12 audit: plan profile, split tranche, stop reason and internal error text were being dropped
    if event.get('profile') in ('defensive', 'trend', 'ipo', 'inverse_quick', 'oversold'): result['profile'] = event['profile']
    for key in ('weak_caps', 'reconciliation_required'):
        if type(event.get(key)) is bool: result[key] = event[key]
    if type(event.get('tranche')) is int and 1 <= event['tranche'] <= 10: result['tranche'] = event['tranche']
    if event['event'] == 'stopped' and event.get('reason') == 'session or STOP': result['reason'] = event['reason']
    if isinstance(event.get('detail'), str) and re.fullmatch(r'[a-z ]{3,60}', event['detail']):
        result['detail'] = event['detail']  # internal lowercase messages only; broker text (digits, Korean) never passes
    if event['event'] == 'v2_execution_gate' and isinstance(event.get('guard_detail'), str) and re.fullmatch(r'[A-Za-z][A-Za-z ;,.]{2,119}', event['guard_detail']):
        result['guard_detail'] = event['guard_detail']  # the local order guard's English refusal text, no numbers
    if event.get('trigger') in ('risk', 'take_profit', 'manual', 'vibe', 'model'): result['trigger'] = event['trigger']
    if event.get('strength_reason') in ('no_strength_candidate', 'strength_change_out_of_range', 'trend_not_rising', 'reentry_below_last_exit', 'one_share_over_buy', 'pick_cooldown'):
        result['strength_reason'] = event['strength_reason']
    if event['event'] == 'strength_shadow':  # observation only: what the strength path would pick when Jev's pick is blocked
        if event.get('blocked') in REASONS:  # the refusal that kept Jev's pick, the same codes as reason
            result['blocked'] = event['blocked']
        if type(event.get('opposite_held')) is bool: result['opposite_held'] = event['opposite_held']
        if event.get('strength_reason') == 'no_movers': result['strength_reason'] = 'no_movers'
        if isinstance(event.get('error_type'), str) and re.fullmatch(r'[A-Za-z]{3,40}', event['error_type']): result['error_type'] = event['error_type']
    if event['event'] == 'watchlist_strength' and type(event.get('slot')) is bool:
        result['slot'] = event['slot']  # bought through the strategy slot beside Jev's pick (11/17)
    if event['event'] in ('watchlist_strength', 'new_listing', 'strength_shadow') and type(event.get('change_pct')) in (int, float) and -60 <= event['change_pct'] <= 300:
        result['change_pct'] = event['change_pct']  # a listing day may move -40%..+300% from the offer price
    if event['event'] == 'new_listing':
        if type(event.get('turnover_pct')) in (int, float) and 0 <= event['turnover_pct'] < 10_000:
            result['turnover_pct'] = event['turnover_pct']
        if type(event.get('sessions')) is int and 0 <= event['sessions'] <= 10:
            result['sessions'] = event['sessions']
    if event.get('ipo_reason') in ('ipo_daily_limit', 'ipo_off', 'ipo_too_early', 'ipo_already_held', 'no_new_listing', 'listing_unconfirmed', 'ipo_quote_unavailable',
                                   'ipo_bars_unavailable', 'below_base', 'below_open', 'off_high', 'trend_not_rising', 'volume_not_rising', 'pick_cooldown'):
        result['ipo_reason'] = event['ipo_reason']
    if event.get('bar_problem') in ('quote_error', 'bar_response_invalid', 'bars_missing', 'insufficient_bars', 'stale_bars', 'other'):
        result['bar_problem'] = event['bar_problem']
    if isinstance(event.get('input_digest'), str) and re.fullmatch(r'[0-9a-f]{64}', event['input_digest']):
        result['input_digest'] = event['input_digest']
    if event['event'] in ('oversold_scan', 'oversold_signal', 'oversold_v2_signal'):  # 낙폭과대: the measured fall and the answer
        for key in ('drop_pct', 'atr_multiple', 'bounce_pct', 'target_pct', 'stop_pct', 'confidence', 'score', 'market_low_pct', 'rel_drop'):
            if type(event.get(key)) in (int, float) and -100 <= event[key] <= 100:
                result[key] = event[key]
        for key in ('stabilized', 'permitted'):
            if isinstance(event.get(key), bool):
                result[key] = event[key]
    if event['event'] == 'oversold_v2_signal' and event.get('research') in ('veto', 'boost', 'normal'):
        result['research'] = event['research']  # the main engine's verdict on the candidate
    if event['event'] == 'oversold_v2_gate':  # 선별 v2: the market facts at the time and whether they passed
        if isinstance(event.get('passed'), bool):
            result['passed'] = event['passed']
        if type(event.get('nasdaq_prev')) in (int, float) and -100 <= event['nasdaq_prev'] <= 100:
            result['nasdaq_prev'] = event['nasdaq_prev']
        for name in ('kospi', 'kosdaq'):
            facts = event.get(name) if isinstance(event.get(name), dict) else {}
            for key in ('low_pct', 'prior5_pct'):
                if type(facts.get(key)) in (int, float) and -100 <= facts[key] <= 100:
                    result[f'{name}_{key}'] = facts[key]
        if event.get('engine') in ('jev', 'vibe'):
            result['engine'] = event['engine']
        if event.get('choice') in ('BUY', 'WAIT'):
            result['choice'] = event['choice']
    if event['event'] == 'submitted' and event.get('add_reason') in ('first', 'rule_off', 'confidence_up', 'cheaper_step', 'profit_step', 'price_step'):
        result['add_reason'] = event['add_reason']  # why an add passed (improvement report 11/17-3)
    for key in ('high_gap_pct', 'open_gap_pct', 'vi_up_gap_pct', 'vi_down_gap_pct'):  # buy price vs today's high/open (11/13
        value = event.get(key)                                                        # review #3) and the VI triggers (11/17)
        if event['event'] == 'submitted' and type(value) in (int, float) and -100 <= value <= 1000:
            result[key] = value
    for key, allowed in (('overheat_code', '012345'), ('alarm_code', '012345'), ('vi_halt', '12')):  # NH warnings (11/17)
        value = event.get(key)
        if event['event'] == 'submitted' and isinstance(value, str) and len(value) == 1 and value in allowed:
            result[key] = value
    for key in ('confidence', 'filled', 'remaining', 'cancelled', 'limit', 'qty', 'elapsed_ms', 'openai_estimated_usd', 'stop_pct', 'half_pct', 'all_pct', 'trend_half_pct', 'trend_trail_pct'):
        value = event.get(key)
        if (type(value) in (int, float) and 0 <= value < 1e12 and
                (event['event'] != 'jev_holding_decision' or key != 'confidence' or value <= 1)):
            result[key] = value
    ranges = event.get('recent_range_pct')
    if isinstance(ranges, dict):
        result['recent_range_pct'] = {k:v for k,v in ranges.items() if k in CHOICES and type(v) in (int,float) and 0 <= v < 1e6}
    if event.get('model') in {'gpt-6-luna', 'jev-1.14.0'}:
        result['model'] = event['model']
    if event.get('market_source') == 'NH KRX 1-minute completed bars':
        result['market_source'] = event['market_source']
    bars = event.get('market_bars')
    if bars is not None:
        names = {'K200', 'INV', 'LEV', 'SAMSUNG', 'NAVER', 'KAKAO'}
        # The 7 ETFs may be joined by watchlist stocks under 6-digit codes (2026-11-11); a cycle whose read of some
        # ETFs failed records only the ones it has (4th review: a partial set stopped every later ingest).
        etf_plus_stocks = (isinstance(bars, dict) and bool(set(ETF_SYMBOLS) & set(bars))
                           and all(n in ETF_SYMBOLS or (isinstance(n, str) and re.fullmatch(CODE_PATTERN, n)) for n in bars))
        if not isinstance(bars, dict) or (set(bars) not in (names, {'K200', 'INV'}, set(ETF_SYMBOLS)) and not etf_plus_stocks):
            raise ValueError('invalid market bar symbols')
        result['market_bars'] = {}
        for name, rows in bars.items():
            if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
                raise ValueError('invalid market bars')
            safe = []
            for row in rows:
                keys = ('stck_oprc','stck_hgpr','stck_lwpr','stck_prpr','vol')
                if not isinstance(row, dict) or not re.fullmatch(r'\d{6}', str(row.get('bsop_time',''))):
                    raise ValueError('invalid bar time')
                values = [str(row.get(k, '')) for k in keys]
                if any(not re.fullmatch(r'\d{1,15}', v) for v in values):
                    raise ValueError('invalid bar value')
                op, high, low, close, _ = map(int, values)
                if low <= 0 or not (low <= op <= high and low <= close <= high):
                    raise ValueError('inconsistent OHLC')
                safe.append({'bsop_time': str(row['bsop_time']), **dict(zip(keys, values))})
            result['market_bars'][name] = safe
    news = event.get('news')
    if isinstance(news, list):
        clean = []
        for item in news[:20]:
            if not isinstance(item, dict) or not isinstance(item.get('title'), str) or not isinstance(item.get('published'), str):
                continue
            try:
                published = datetime.fromisoformat(item['published'])
                if published.tzinfo is None or published.utcoffset() is None: continue
            except ValueError:
                continue
            title = item['title'][:250]
            title = re.sub(r'(?i)\b(?:sk|jv|api)[-_][A-Za-z0-9_-]{8,}\b', '[redacted]', title)
            title = re.sub(r'(?<!\d)\d(?:[\d -]{7,}\d)(?!\d)', '[redacted]', title)
            safe_news = {'title': title, 'published': published.isoformat()}
            if item.get('source') in ('google_news_rss', 'bing_news_rss', 'naver_finance_news'):
                safe_news['source'] = item['source']
                publisher = item.get('publisher')
                if (isinstance(publisher, str) and 1 <= len(publisher) <= 80 and publisher.isprintable()
                        and not any(c in publisher for c in '<>@')
                        and not re.search(r'(?<!\d)\d(?:[\d -]{7,}\d)(?!\d)', publisher)
                        and not re.search(r'(?i)\b(?:sk|jv|api)[-_][A-Za-z0-9_-]{8,}\b', publisher)):
                    safe_news['publisher'] = publisher
            if item.get('topic') in ('market', 'kosdaq150', 'semiconductor'):
                safe_news['topic'] = item['topic']
            clean.append(safe_news)
        result['news'] = clean
    numeric = lambda v: type(v) in (int, float) and abs(v) < 1e15
    flows = event.get('investor_flows')
    if isinstance(flows, dict):  # per-symbol daily investor net quantities given to Jev
        result['investor_flows'] = {
            name: {day: {k: v for k, v in row.items() if k in ('date', 'foreign', 'institution', 'individual')
                         and (numeric(v) or (k == 'date' and isinstance(v, str) and re.fullmatch(r'\d{8}', v)))}
                   for day, row in entry.items() if day in ('last_close_day', 'today_provisional') and isinstance(row, dict)}
            for name, entry in flows.items() if isinstance(entry, dict) and (name in ETF_SYMBOLS or re.fullmatch(CODE_PATTERN, str(name)))}
    candidates = event.get('candidates')
    if isinstance(candidates, dict):  # watchlist stocks offered to Jev this cycle
        result['candidates'] = {
            code: {k: d[k] for k in ('change_pct', 'value_mil') if numeric(d.get(k))}
                  | {'reasons': [r for r in d.get('reasons') or [] if r in ('거래대금 상위', '거래량 상위', '급상승', '급하락')]}
            for code, d in candidates.items() if re.fullmatch(CODE_PATTERN, str(code)) and isinstance(d, dict)}
    settings = event.get('settings')
    if event['event'] == 'strategy_settings' and isinstance(settings, dict):
        result['settings'] = {k: v for k, v in settings.items() if isinstance(k, str) and numeric(v)}
        if event.get('status') in ('default', 'custom', 'invalid'):
            result['status'] = event['status']
    jev = event.get('jev')
    if isinstance(jev, dict) and isinstance(jev.get('candidate'), dict):
        response_time = jev.get('response_time')
        if isinstance(response_time, str):
            try:
                parsed = datetime.fromisoformat(response_time)
                if parsed.tzinfo is not None and parsed.utcoffset() is not None and parsed <= timestamp:
                    result['jev_response_time'] = parsed.isoformat()
            except ValueError:
                pass
        if jev.get('model') == 'jev-1.14.0': result['jev_model'] = jev['model']
        if isinstance(jev.get('input_digest'), str) and re.fullmatch(r'[0-9a-f]{64}', jev['input_digest']):
            result['jev_input_digest'] = jev['input_digest']
        candidate = jev['candidate']; choice = candidate.get('choice'); confidence = candidate.get('confidence')
        if choice in CHOICES or (isinstance(choice, str) and re.fullmatch(CODE_PATTERN, choice)): result['jev_choice'] = choice
        probabilities = candidate.get('probabilities')
        if isinstance(probabilities, dict) and len(probabilities) <= 40:
            kept = {k: v for k, v in probabilities.items() if isinstance(k, str) and (k in CHOICES or re.fullmatch(CODE_PATTERN, k))
                    and type(v) in (int, float) and 0 <= v <= 1}
            if kept: result['jev_probabilities'] = kept
        if type(confidence) in (int,float) and 0 <= confidence <= 1: result['jev_confidence'] = confidence
        for key in ('input_tokens','estimated_usd','elapsed_ms'):
            value = jev.get(key)
            if type(value) in (int,float) and 0 <= value < 1e12: result['jev_' + key] = value
    return result


SCHEMA_VERSION = 2  # events_v2 rows; the first table (events, the same rows with fewer fields) was dropped on 2026-11-18


def source_identity(db, f, path, inode):
    """The ledger's id for this file. APFS gives the volume a new st_dev at every boot, so a file is known by path and
    inode. Ids from before (hashed with st_dev, one more full copy each boot until 11/18) are matched by content once: the
    import that starts with this file's first line and reached furthest; the resume check below then confirms the rest."""
    known = db.execute('SELECT source FROM sources WHERE path=? AND inode=?', (path, inode)).fetchone()
    if known:
        return known[0]
    first = hashlib.sha256(f.readline()).hexdigest(); f.seek(0)
    identity = next((source for (source,) in db.execute('SELECT source FROM events_v2 GROUP BY source ORDER BY max(offset) DESC')
                     if db.execute('SELECT 1 FROM events_v2 WHERE source=? AND offset=0 AND digest=?', (source, first)).fetchone()),
                    hashlib.sha256(f'{path}:{inode}'.encode()).hexdigest())
    db.execute('INSERT INTO sources VALUES (?,?,?)', (path, inode, identity))
    return identity


def ingest(source, database):
    """Append the file's new complete lines to events_v2; returns how many.

    An ingest resumes at the last imported line (journal_event calls this after every line) and scans from the start
    when that line is gone or changed. A new line the sanitizer rejects stops the import (fail closed), and a line that
    differs from what was imported at its offset raises.
    """
    source, database = Path(source), Path(database)
    database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_file(database)  # never os.open an existing ledger: that corrupted it on 11-19 10:36 (src.tools.private_db)
    inode = source.stat().st_ino
    inserted = 0
    with sqlite3.connect(database, timeout=30) as db, source.open('rb') as f:
        db.execute('PRAGMA journal_mode=WAL')  # the runner writes while the learning watch and reports read: neither waits
        # the runner and the learning watch ingest the same file; without the write lock both insert one offset
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS events_v2 (source TEXT NOT NULL, offset INTEGER NOT NULL, digest TEXT NOT NULL, time TEXT NOT NULL, event TEXT NOT NULL, data TEXT NOT NULL, schema_version INTEGER NOT NULL, PRIMARY KEY(source,offset))')
        db.execute('CREATE INDEX IF NOT EXISTS events_v2_time ON events_v2(time)')
        db.execute('CREATE INDEX IF NOT EXISTS events_v2_event_time ON events_v2(event, time)')
        db.execute('CREATE TABLE IF NOT EXISTS sources (path TEXT NOT NULL, inode INTEGER NOT NULL, source TEXT NOT NULL, PRIMARY KEY(path,inode))')
        identity = source_identity(db, f, str(source.resolve()), inode)
        digest_at = lambda offset: db.execute('SELECT digest FROM events_v2 WHERE source=? AND offset=?', (identity, offset)).fetchone()
        last = db.execute('SELECT max(offset) FROM events_v2 WHERE source=?', (identity,)).fetchone()[0]
        if last is not None:
            f.seek(last); line = f.readline()
            kept = digest_at(last)
            if not (line.endswith(b'\n') and kept and kept[0] == hashlib.sha256(line).hexdigest()):
                f.seek(0)  # truncated or rewritten: the full scan below finds where it differs
        for line in iter(f.readline, b''):
            offset = f.tell() - len(line)
            if not line.endswith(b'\n'): break
            digest = hashlib.sha256(line).hexdigest()
            existing = digest_at(offset)
            if existing:
                if existing[0] != digest: raise ValueError('source changed at an imported offset')
                continue
            event = sanitize(json.loads(line))
            db.execute('INSERT INTO events_v2 VALUES (?,?,?,?,?,?,?)', (identity, offset, digest, event['time'], event['event'],
                       json.dumps(event, ensure_ascii=False, allow_nan=False), SCHEMA_VERSION))
            inserted += 1
    return inserted


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('action', choices=('import','summary'))
    parser.add_argument('--events', type=Path, default=EVENTS); parser.add_argument('--database', type=Path, default=DATABASE)
    args=parser.parse_args()
    if args.action=='import': print(json.dumps({'imported':ingest(args.events,args.database)}))
    else:
        if not args.database.exists(): raise SystemExit('ledger not initialized')
        with sqlite3.connect(f'file:{args.database}?mode=ro',uri=True) as db:
            print(json.dumps(dict(db.execute('SELECT event,count(*) FROM events_v2 GROUP BY event')),ensure_ascii=False))

if __name__=='__main__': main()
