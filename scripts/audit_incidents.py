"""Persistent history of the problem classes the intraday audit detects (user 2026-11-18).

Each class found and fixed on 2026-11-18 is a REGISTRY entry (what it is, why it happened, where it was fixed, which test
guards the fix). `record()` is called by every audit run (intraday_audit.collect) with that run's finding keys and keeps
one row per key in audit-incidents.sqlite3: new -> open (seen again) -> resolved (absent from a later run) -> recurred
(seen after resolved) -> resolved ... so a class that comes back on a later day is reported as a recurrence, not as new.
A key is `prefix` or `prefix:qualifier`; the title comes from the prefix. Only this module writes the database.

    PYTHONPATH=agent:scripts .venv/bin/python scripts/audit_incidents.py            # every incident, newest first
    PYTHONPATH=agent:scripts .venv/bin/python scripts/audit_incidents.py --since 2026-11-18
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from src.config.paths import get_runtime_root  # VIBE_TRADING_HOME, else ~/.vibe-trading (one rule for every tool)

KST = ZoneInfo('Asia/Seoul')
DB = get_runtime_root() / 'nhplug' / 'audit-incidents.sqlite3'
SPACING = timedelta(minutes=10)  # one occurrence row per key per 10 minutes keeps the trail small
SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (key TEXT PRIMARY KEY, prefix TEXT NOT NULL, first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL, count INTEGER NOT NULL, recurrences INTEGER NOT NULL, status TEXT NOT NULL,
    last_detail TEXT, resolved_at TEXT);
CREATE TABLE IF NOT EXISTS occurrences (time TEXT NOT NULL, key TEXT NOT NULL, detail TEXT);
CREATE INDEX IF NOT EXISTS occurrences_key_time ON occurrences(key, time);
"""
_INPUTS_TEST = 'test_jev_inputs_report_presence_freshness_and_evidence'
REGISTRY = {
    'jev.news_old': {
        'title': 'Jev 시장 헤드라인에 90분 넘은 기사', 'since': '2026-11-18',
        'cause': 'Bing 24시간 창의 2~10시간 전 기사가 Jev 판단 입력에 들어감',
        'fix': 'scripts/etf_news.py RECENT·headlines7', 'guard': 'test_old_market_or_stock_headlines_fire_from_a_single_call'},
    'jev.stock_news_old': {
        'title': 'Jev 종목 헤드라인이 시각 없음 또는 90분 초과', 'since': '2026-11-18',
        'cause': '관심종목이 하루치 종목 뉴스를 시각 없는 문자열로 넘겨 Jev가 오래된 기사를 구별하지 못함',
        'fix': 'scripts/nh_paper_isolated.py recent_stock_headlines',
        'guard': 'test_old_market_or_stock_headlines_fire_from_a_single_call'},
    'jev.market_intraday_missing': {
        'title': 'Jev 시황 요약에 장중 지수 흐름 없음', 'since': '2026-11-18',
        'cause': '장중 수집(지수 1분봉·프로그램·외국계 창구·투자자 랭킹)이 Jev 시황 요약에 들어가지 않음',
        'fix': 'scripts/market_context.py intraday_view',
        'guard': 'test_session_calls_without_intraday_market_sections_fire'},
    'jev.candidate_figures_stale': {
        'title': 'Jev 후보 종목 등락·거래대금이 최신 순위 값이 아님', 'since': '2026-11-18',
        'cause': '관심종목(30분 주기) 값이 최대 45분 묵은 채 후보 설명에 들어감',
        'fix': 'scripts/nh_paper_isolated.py with_latest_figures·latest_mover_rows',
        'guard': 'test_candidate_figures_older_than_the_ranking_window_fire'},
    'jev.evidence_missing': {
        'title': 'Jev 판단 근거 답변 누락', 'since': '2026-11-18',
        'cause': '근거 질문(jev_context.EVIDENCE)이 빠지면 판단 이유를 복기할 수 없음',
        'fix': 'scripts/jev_context.py EVIDENCE·scripts/research_store.py record_evidence',
        'guard': 'test_missing_evidence_after_it_started_fires'},
    'jev.global_intraday_missing': {
        'title': 'Jev 시황 요약에 장중 해외 선물·원자재 없음', 'since': '2026-11-18',
        'cause': '해외 지수·금·원유가 전일 종가 일봉뿐이라 장중 미국 선물·원자재 흐름을 Jev가 못 봄',
        'fix': 'scripts/market_context.py global_intraday (Yahoo spark 5분봉, 30분 한도)',
        'guard': 'test_session_calls_without_intraday_overseas_futures_fire'},
    'db.corrupt': {
        'title': '기록 DB 무결성 검사 실패', 'since': '2026-11-19',
        'cause': '기존 DB 파일을 SQLite 밖에서 os.open·close해 프로세스 잠금이 풀리고 다른 프로세스가 사용 중인 WAL을 지움(11-19 10:36 원장 색인 손상, quick_check로는 안 보임)',
        'fix': 'agent/src/tools/private_db.private_file(11곳 교체), intraday_audit.database_health(integrity_check)',
        'guard': 'test_a_corrupted_database_fires_even_when_quick_check_would_pass'},
    'learning.error': {
        'title': '학습 관찰기가 실행 중인데 매 회차 실패', 'since': '2026-11-19',
        'cause': '오래 돈 프로세스가 다른 프로세스가 지운 원장 WAL 파일 핸들을 붙잡아 매 회차 sqlite3.DatabaseError(09:03~10:26, 새 프로세스는 정상)',
        'fix': 'scripts/isolated_paper_learning_watch.py DB_ERROR_RESTART(3회 연속이면 스스로 새 프로세스), intraday_audit.learning_rounds',
        'guard': 'test_a_learning_watcher_failing_every_round_fires'},  # also test_repeated_sqlite_errors_replace_the_process
    'shadow.silent': {
        'title': '이슈·테마 그림자 후보 기록이 멈춤', 'since': '2026-11-18',
        'cause': '1단계 기록 전용 후보(issue_candidates)가 돌지 않으면 매수 허용 여부를 판단할 표본이 쌓이지 않음',
        'fix': 'scripts/market_movers.py shadow_round (shadow-status.json)', 'guard': 'test_shadow_candidate_rounds_that_stop_or_fail_fire'},
    'shadow.source_error': {
        'title': '그림자 후보 출처(랭킹·DART·시세) 조회 실패', 'since': '2026-11-18',
        'cause': '키움 랭킹·OpenDART·NH 시세 중 하나가 실패하면 그 출처의 후보가 빠짐',
        'fix': 'scripts/issue_candidates.py observe', 'guard': 'test_shadow_candidate_rounds_that_stop_or_fail_fire'},
    'source.lagging': {
        'title': '장중 시황 출처 구간의 시각이 수집 시각보다 늦음', 'since': '2026-11-18',
        'cause': '토스 기관 랭킹이 11:05에 머문 채 13:08까지 쓰임(11/18), 엔진은 그 구간을 받지 못함',
        'fix': 'scripts/market_context.py intraday_view INTRADAY_MAX_LAG', 'guard': 'test_a_lagging_market_source_section_fires'},
    'ledger.ingest_error': {
        'title': '거래 원장 SQLite 반영 실패', 'since': '2026-11-18',
        'cause': '등록되지 않은 이벤트 이름으로 trading_ledger.ingest가 예외(11/17 11:46 실행기 정지)',
        'fix': 'scripts/nh_paper_isolated.py journal_event (ledger-ingest-error.json)',
        'guard': 'test_ledger_ingest_error_marker_fires'},
    'runner.restart_unrecorded': {
        'title': 'restarts.jsonl 기록 없는 실행기 재시작', 'since': '2026-11-18',
        'cause': '다른 세션이 STOP 파일로 재시작해 이유·중단 시간이 남지 않음(11/18 12:20)',
        'fix': 'scripts/restart_runner.py restart', 'guard': 'test_a_restart_without_a_restart_record_fires'},
    'live.fill_time_unknown': {
        'title': '실계좌 체결 시각 복구 실패(T00:00:00)', 'since': '2026-11-18',
        'cause': 'NH 실계좌 주문 목록에 주문 시각이 없음',
        'fix': 'scripts/live_fills.py rows·order_times', 'guard': 'test_live_balance_gaps_and_unrecovered_fill_times_fire'},
    'live.balance_missing': {
        'title': '실계좌 잔고 기록 없음 또는 멈춤', 'since': '2026-11-18',
        'cause': '실계좌 실행기가 잔고(총자산)를 남기지 않아 Discord·대시보드 손익이 비어 있음',
        'fix': 'scripts/live_fills.py record_balance (nh_live_follow·longterm_live)',
        'guard': 'test_live_balance_gaps_and_unrecovered_fill_times_fire'},
    'discord.pnl_import': {
        'title': 'Discord 알림이 손익 계산 모듈을 못 읽음', 'since': '2026-11-18',
        'cause': "launchd PYTHONPATH(agent:scripts)에 streamlit_namuh가 없어 engine_compare import가 조용히 실패",
        'fix': 'scripts/discord_notify.py sys.path', 'guard': 'test_launchd_pythonpath_can_import_engine_compare'},
    'jev.input_missing': {
        'title': 'Jev 입력 키 포함률 90% 미만', 'since': '2026-11-18', 'cause': '판단 입력의 일부가 빠진 채 호출됨',
        'fix': 'scripts/intraday_audit.py jev_inputs (감시만)', 'guard': _INPUTS_TEST},
    'jev.news_stale': {
        'title': 'Jev 최신 헤드라인 경과 중앙값 120분 초과', 'since': '2026-11-18', 'cause': '뉴스 수집이 막혀 오래된 기사만 남음',
        'fix': 'scripts/etf_news.py headlines7', 'guard': 'test_old_market_or_stock_headlines_fire_from_a_single_call'},
    'jev.market_stale': {
        'title': 'Jev 시황 요약 15분 초과 경과', 'since': '2026-11-18', 'cause': '시황 수집 주기가 멈추거나 늦음',
        'fix': 'scripts/market_context.py summary MAX_AGE', 'guard': _INPUTS_TEST},
    'jev.data_insufficient': {
        'title': 'Jev 자료 충분 응답 40% 미만', 'since': '2026-11-18', 'cause': 'Jev가 입력이 부족하거나 서로 어긋난다고 답함',
        'fix': 'scripts/jev_context.py EVIDENCE why_data (감시만)', 'guard': _INPUTS_TEST},
    'jev.reentry_never_buy': {
        'title': '신중 재진입 재검토가 모두 WAIT', 'since': '2026-11-18', 'cause': '재진입 재검토가 사실상 금지로 동작',
        'fix': 'scripts/nh_paper_isolated.py reentry (감시만)', 'guard': _INPUTS_TEST},
}


def title(key):
    return (REGISTRY.get(key.split(':', 1)[0]) or {}).get('title')


def _item(key, count, first_seen, recurrences):
    return {'key': key, 'title': title(key), 'count': count, 'first_seen': first_seen, 'recurrences': recurrences}


def skipped(key, unchecked):
    """True when this run did not evaluate `key`: its prefix or the whole key is in `unchecked`."""
    return key in unchecked or key.split(':', 1)[0] in unchecked


def record(findings, now, path=DB, unchecked=()):
    """Fold one audit run's {key: detail} into the history and return {'new', 'recurred', 'resolved', 'open'} lists of
    {key, title, count, first_seen, recurrences}. `unchecked`: prefixes or keys this run did not evaluate (a session
    check after 15:20, on a holiday); their open incidents stay open instead of resolving, so the next sighting is not a
    false recurrence. The same call twice (same findings, same `now`) changes nothing and returns the same lists.
    Never raises: a database failure returns {'error': <exception type>}."""
    stamp = now.isoformat(timespec='seconds')
    out = {'new': [], 'recurred': [], 'resolved': [], 'open': []}
    try:
        with sqlite3.connect(path, timeout=5) as db:
            db.executescript(SCHEMA)
            known = {row[0]: row[1:] for row in db.execute(
                'SELECT key, status, count, first_seen, recurrences, last_seen, resolved_at FROM incidents')}
            for key, detail in sorted(findings.items()):
                detail = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False, default=str)
                status, count, first, recurrences, last, _ = known.get(key) or (None, 0, stamp, 0, None, None)
                if last != stamp:  # a repeated call at the same time is not a new sighting
                    status = {None: 'new', 'resolved': 'recurred'}.get(status, 'open')
                    count, recurrences = count + 1, recurrences + (status == 'recurred')
                    db.execute('INSERT OR REPLACE INTO incidents VALUES (?,?,?,?,?,?,?,?,NULL)',
                               (key, key.split(':', 1)[0], first, stamp, count, recurrences, status, detail))
                    recent = db.execute('SELECT 1 FROM occurrences WHERE key=? AND time > ?',
                                        (key, (now - SPACING).isoformat(timespec='seconds'))).fetchone()
                    if not recent:
                        db.execute('INSERT INTO occurrences VALUES (?,?,?)', (stamp, key, detail))
                out[status].append(_item(key, count, first, recurrences))
            for key, (status, count, first, recurrences, _, resolved_at) in sorted(known.items()):
                if key in findings or (status == 'resolved' and resolved_at != stamp) or (status != 'resolved' and skipped(key, unchecked)):
                    continue
                if status != 'resolved':
                    db.execute("UPDATE incidents SET status='resolved', resolved_at=? WHERE key=?", (stamp, key))
                out['resolved'].append(_item(key, count, first, recurrences))
        Path(path).chmod(0o600)
        return out
    except Exception as exc:  # the audit must still print its facts
        return {'error': type(exc).__name__}


def history(path=DB, since=None):
    """Every incident row (newest sighting first) with its registry title; since = ISO time on last_seen. [] when none."""
    from sqlite_ro import ro_connect
    if not Path(path).is_file():
        return []
    try:
        with ro_connect(path, timeout=2) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute('SELECT * FROM incidents WHERE last_seen >= ? ORDER BY last_seen DESC, key',
                              (since or '',)).fetchall()
    except sqlite3.Error:
        return []
    return [{**dict(row), 'title': title(row['key'])} for row in rows]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--since', help='ISO date or time on last_seen')
    parser.add_argument('--db', default=str(DB))
    args = parser.parse_args()
    print(json.dumps(history(args.db, args.since), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
