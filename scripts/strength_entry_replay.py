"""Replay recorded strength entries using an isolated review rule; never places orders."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from sqlite_ro import ro_connect

KST = ZoneInfo('Asia/Seoul')
ROOT = Path.home() / '.vibe-trading/nhplug/shared-etf-paper'
MODEL_MATCH_WINDOW = timedelta(seconds=15)


def replay(root, day):
    """Return one row per submitted strength order and its temporally nearest recorded candidate."""
    from strength_entry_review import review

    root = Path(root)
    day = date.fromisoformat(day)
    start = datetime.combine(day, datetime.min.time(), KST)
    end = start + timedelta(days=1)
    with ro_connect(root / 'trading-ledger.sqlite3') as events:
        submissions = events.execute(
            "SELECT time,data FROM events_v2 WHERE event='submitted' AND time>=? AND time<? "
            "AND json_extract(data,'$.source')='watchlist_strength' ORDER BY time LIMIT 1000",
            (start.isoformat(), end.isoformat())).fetchall()
        contexts = {json.loads(raw).get('cycle_id'): json.loads(raw) for (raw,) in events.execute(
            "SELECT data FROM events_v2 WHERE event='market_context' AND time>=? AND time<? "
            "AND json_extract(data,'$.context')='entry' ORDER BY time LIMIT 10000",
            (start.isoformat(), end.isoformat()))}
    results = []
    with ro_connect(root / 'market-research.sqlite3') as research, ro_connect(root / 'orders.sqlite3') as orders:
        for stamp, raw in submissions:
            event = json.loads(raw)
            timing = {**(event.get('entry_timing') or {}), 'open_gap_pct': event.get('open_gap_pct'),
                      'high_gap_pct': event.get('high_gap_pct')}
            gate = datetime.fromisoformat(timing.get('gate_time') or stamp)
            row = research.execute(
                "SELECT time,inputs,response FROM model_inputs WHERE engine='jev' AND question='candidate' "
                "AND time>=? AND time<=? ORDER BY time DESC LIMIT 1",
                ((gate - MODEL_MATCH_WINDOW).isoformat(), gate.isoformat())).fetchone()
            state, response, model_time, inputs = {}, {}, None, {}
            if row:
                model_time = datetime.fromisoformat(row[0])
                inputs = json.loads(row[1])
                state = inputs.get('state') or {}
                response = json.loads(row[2])
            code = event['symbol']
            details = ((contexts.get(event.get('cycle_id')) or {}).get('candidates') or {}).get(code) or {}
            raw_index = str(details.get('index') or '').upper()
            if not raw_index:
                description = (inputs.get('criteria') or {}).get(code)
                match = re.search(r'개별주식\((KOSPI(?:200)?|KOSDAQ(?:150)?)\)', description or '')
                raw_index = match.group(1) if match else ''
            index = '코스닥' if raw_index.startswith('KOSDAQ') else '코스피' if raw_index.startswith('KOSPI') else None
            market = state.get('market_regime_themes_global') or {}
            stock = (state.get('watchlist_details') or {}).get(code) or {}
            reasons = review(symbol=code, model_choice=response.get('choice'), model_fresh=bool(row), market=market,
                             index=index, market_news=state.get('recent_news_headlines') or [], stock=stock,
                             timing=timing, now=gate)
            fills = orders.execute(
                'SELECT f.side,f.quantity,f.price,f.time FROM fills f JOIN decision_order_links l ON l.order_id=f.order_id '
                'WHERE l.cycle_id=? AND f.symbol=? ORDER BY f.time LIMIT 50', (event.get('cycle_id'), code)).fetchall()
            results.append({'symbol': code, 'submitted_at': stamp, 'gate_at': gate.isoformat(),
                            'index': index, 'model_at': model_time.isoformat() if model_time else None,
                            'model_choice': response.get('choice'), 'model_link': 'time_proximity_only' if row else 'missing',
                            'blocked': bool(reasons), 'reasons': reasons,
                            'entry_fills': [{'side': side, 'quantity': qty, 'price': price, 'time': when}
                                            for side, qty, price, when in fills]})
    return {'day': day.isoformat(), 'results': results, 'scope': 'recorded submitted entries only; no replacement trades or fees'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--day', required=True, help='KRX date YYYY-MM-DD')
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps(replay(args.root, args.day), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
