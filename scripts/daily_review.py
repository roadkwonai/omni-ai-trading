"""After-close review of the shared paper account (research record; never orders).

Rule-based, no model call: the day's closed positions (return, best/worst point while held, price after the exit),
settled Jev/vibe calls against KODEX 200, why entries waited, and the market regime are turned into a few plain
lessons. Stored in market-research.sqlite3 (kind 'daily_review'); the next day's track record shows the latest ones
to Jev and vibe-trading, in the spirit of TradingAgents' reflect-and-reuse memory.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from sqlite_ro import ro_connect

KST = ZoneInfo('Asia/Seoul')
ROOT = Path.home() / '.vibe-trading/nhplug/shared-etf-paper'


def _mean(values):
    values = [v for v in values if type(v) in (int, float)]
    return round(sum(values) / len(values), 3) if values else None


def facts(root, day):
    """The day's measurable outcomes from the research DB and the ledger."""
    research = sqlite3.connect(f"file:{Path(root) / 'market-research.sqlite3'}?mode=ro", uri=True)
    research.row_factory = sqlite3.Row
    has = lambda table: research.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone()
    closed = [dict(r) for r in research.execute("SELECT * FROM position_paths WHERE closed LIKE ?", (day + '%',))] if has('position_paths') else []
    calls = {}
    unverified = 0
    if has('decision_outcomes'):
        from track_record import outcome_timing_filter, unverified_vibe_outcomes
        timing = outcome_timing_filter(research)
        unverified = unverified_vibe_outcomes(research, day)
        for key, n, hit, ret, alpha in research.execute(
                f"""SELECT engine || ' ' || kind, COUNT(*), AVG(direction * r30 > 0), AVG(direction * r30), AVG(direction * (r30 - b30))
                   FROM decision_outcomes WHERE time LIKE ? AND status = 'settled' AND r30 IS NOT NULL AND b30 IS NOT NULL AND {timing} GROUP BY 1""", (day + '%',)):
            calls[key] = {'n': n, 'hit_rate_30m': round(hit, 2), 'avg_return_30m_pct': round(ret, 3), 'avg_vs_kodex200_30m_pct': round(alpha, 3)}
    research.close()
    gates, exits = Counter(), Counter()
    ledger = Path(root) / 'trading-ledger.sqlite3'
    if ledger.exists():
        with ro_connect(ledger) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='events_v2'").fetchone():
                end = (datetime.fromisoformat(day) + timedelta(days=1)).strftime('%Y-%m-%d')
                for event, data in db.execute("SELECT event, data FROM events_v2 WHERE time >= ? AND time < ? AND event IN "
                                              "('v2_entry_gate', 'v2_risk_exit', 'split_take_profit')", (day, end)):
                    reason = json.loads(data).get('reason')
                    if event == 'v2_entry_gate' and reason != 'entry_conditions_met':
                        gates[reason or 'unknown'] += 1
                    elif event != 'v2_entry_gate':
                        exits[reason or event] += 1
    return {'closed': len(closed), 'wins': sum((r.get('realized_krw') or 0) > 0 for r in closed),
            'realized_krw': sum(r.get('realized_krw') or 0 for r in closed),
            'avg_return_pct': _mean([r.get('return_pct') for r in closed]), 'avg_mfe_pct': _mean([r.get('mfe_pct') for r in closed]),
            'avg_mae_pct': _mean([r.get('mae_pct') for r in closed]), 'avg_after_exit_30m_pct': _mean([r.get('after_30m_pct') for r in closed]),
            'settled_calls': calls, 'vibe_legacy_settled_excluded': unverified,
            'vibe_settlements_note': '게시 시각 미확인 기존 vibe 정산 제외; 30분 방향성은 실제 체결 성과가 아닙니다.',
            'entry_waits': dict(gates.most_common(4)), 'exit_reasons': dict(exits)}


def lessons(f, regime=None):
    """Two to four short lessons from the facts; each names the number it rests on."""
    out = []
    entry = f['settled_calls'].get('jev entry')
    if entry and entry['n'] >= 10:
        if entry['avg_vs_kodex200_30m_pct'] < 0:
            out.append(f"Jev 진입 판단 {entry['n']}건의 30분 뒤 적중률 {entry['hit_rate_30m']:.0%}, KODEX 200 대비 "
                       f"{entry['avg_vs_kodex200_30m_pct']:+.2f}%p로 지수보다 못했다. 지수와 같은 방향만 고르는 판단은 걸러야 한다.")
        else:
            out.append(f"Jev 진입 판단 {entry['n']}건이 30분 뒤 KODEX 200보다 {entry['avg_vs_kodex200_30m_pct']:+.2f}%p 나았다(적중률 {entry['hit_rate_30m']:.0%}).")
    if f['closed']:
        after = f['avg_after_exit_30m_pct']
        if after is not None and after >= 0.3:
            out.append(f"청산 {f['closed']}건 뒤 30분간 평균 {after:+.2f}% 더 움직여 매도가 이른 편이었다. 같은 국면이면 목표를 조금 넓혀도 된다.")
        elif after is not None and after <= -0.3:
            out.append(f"청산 뒤 30분간 평균 {after:+.2f}%로 내려가 매도 시점이 맞았다.")
        if f['avg_mae_pct'] is not None and f['avg_mae_pct'] <= -1.5:
            out.append(f"보유 중 평균 최대 손실이 {f['avg_mae_pct']:+.2f}%로 손절선(−2%)에 가까웠다. 추세 확인이 약한 진입이었다.")
    waits = f['entry_waits']
    if waits:
        top, count = next(iter(waits.items()))
        out.append(f"진입을 막은 가장 큰 이유는 '{top}' {count}회였다" + (f"(국면 {regime})." if regime else '.'))
    return out[:4] or ['체결과 정산 데이터가 부족해 오늘은 교훈을 만들지 않았다.']


def build(root=ROOT, day=None):
    day = day or datetime.now(KST).strftime('%Y-%m-%d')
    f = facts(root, day)
    regime = None
    try:
        import market_context
        indices = (market_context.load(Path(root) / 'market-context.json') or {}).get('indices') or {}
        regime = ', '.join(f"{v['name']} {v['regime']['label']}·{v['flow']}" for v in indices.values()) or None
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {'time': datetime.now(KST).isoformat(timespec='seconds'), 'day': day, 'regime': regime, 'facts': f, 'lessons': lessons(f, regime)}


def store(review, root=ROOT):
    import research_store
    research_store.record('daily_review', review, Path(root) / 'market-research.sqlite3')
    return review


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--day', help='YYYY-MM-DD (Asia/Seoul); default today')
    args = parser.parse_args()
    print(json.dumps(store(build(day=args.day)), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
