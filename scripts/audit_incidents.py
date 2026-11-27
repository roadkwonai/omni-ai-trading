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
    'entry.repeat_block': {
        'title': '같은 종목이 같은 사유로 매 분 주문 단계에서 거절됨', 'since': '2026-11-19',
        'cause': '강도 후보 선정이 1주 가능 여부를 보지 않아 009150(847,000원)을 매 분 골랐고 주문 단계가 0주로 거절, 슬롯이 그 회차를 차지해 Jev 판단이 밀림(13:44~14:46)',
        'fix': 'nh_paper_isolated.strength_shares·strength_candidate(sized=), cool_refused_picks(거절된 강도·신규주 후보 SLOT_COOLDOWN_MIN 쉬기), intraday_audit.repeat_blocks',
        'guard': 'test_the_same_pick_refused_minutes_in_a_row_fires'},  # also test_a_pick_the_order_path_refuses_rests_instead_of_repeating_every_minute
    'entry.jev_stopped': {
        'title': 'Jev 진입 판단이 여러 회차 연속 실패함', 'since': '2026-11-24',
        'cause': '13:12~13:19 60분 안 시황 기사가 없어 Jev 요청 전에 멈춤(7회차). Google 검색(when:1d)이 새 기사를 놓침',
        'fix': 'etf_news(90분, when:2h, 네이버 속보, no_fresh_news), nh_etf_auto_v2._jev7_question, nh_paper_isolated(v2_news_error 뒤 계속)',
        'guard': 'test_no_market_news_still_asks_jev_for_entries_and_holdings'},  # also test_jev_rechecks_market_headlines_at_dispatch...
    'nh.same_query_limit': {
        'title': 'NH 같은 조회 제한(IGW42903)이 반복됨', 'since': '2026-11-19',
        'cause': '모의 잔고가 오늘 매매 종목 0주 행까지 12행(쪽당 10행)이 되어 둘째 쪽을 바로 요청 → NH가 같은 조회를 약 1초에 1번만 받아 거절, 재시도로 회차마다 호출 3번·약 1초 지연(11:00~15:10 167건)',
        'fix': 'nh_live_broker.PAGE_GAP·rate_limited, nh_paper_isolated.paper_pages, nh_live_position_watch.order_pages, intraday_audit.nh_limits',
        'guard': 'test_nh_same_query_limit_hits_in_the_window_fire'},  # also test_a_second_page_waits_for_nhs_one_request_a_second
    'nh.app_rate_limit': {
        'title': 'NH 앱 키 초당 한도(IGW42902)를 한 프로그램이 반복해 넘김', 'since': '2026-11-19',
        'cause': '순위 계산기가 5분마다 60종목 현재가를 쉬지 않고 조회(약 2.6회/초)해 같은 순간 실행기 분봉과 합쳐 앱 키 초당 5회를 넘김: 자기 조회 157건 실패(순위에서 그 종목이 빠짐), 실행기·실계좌 미러·대시보드 호출 110건도 그 3초 안에 거절',
        'fix': 'market_movers.SCAN_GAP(조회 시작 간격 0.5초, 초당 2회)·RATE_LIMIT_WAIT(거절되면 1초 뒤 한 번 더), intraday_audit.nh_limits',
        'guard': 'test_nh_app_rate_limit_hits_in_the_window_fire'},  # also test_the_scan_paces_its_quotes_and_asks_again_after_a_rate_limit
    'live.zero_total': {
        'title': '실계좌 잔고 합계가 잘못됨(총자산 0원 등)', 'since': '2026-11-23',
        'cause': 'NH는 총자산·총매수·총평가를 잔고 마지막 쪽에만 주는데 첫 쪽 합계(0)를 씀: 매매한 종목의 0주 행까지 2쪽이 된 14:42부터 총자산 0, 같은 값을 읽는 실계좌 가드는 새 매수를 capital limit exceeded로 막음',
        'fix': 'nh_live_broker._pages·BALANCE_TOTALS(합계는 마지막 쪽, 없으면 미확인), live_fills.plausible(기록·읽기에서 거름)·balance-rejected.jsonl, intraday_audit.zero_totals',
        'guard': 'test_a_rejected_live_balance_fires'},  # also test_a_paged_balance_takes_its_totals_from_the_last_page, test_an_impossible_balance_is_rejected_and_never_read
    'cli.mismatch': {
        'title': 'omni CLI의 답이 원본 기록과 다름', 'since': '2026-11-25',
        'cause': 'live status가 신규 매수 중단 파일만 보고 하루 손실 정지(live_halts)를 읽지 않아, 실계좌 1이 정지된 11:09~11:25에 새 매수 가능이라고 답함',
        'fix': 'streamlit_namuh/overview.py loss_halts(주의 항목·omni live status), intraday_audit.cli_health',
        'guard': 'test_a_cli_answer_that_disagrees_with_the_records_fires'},  # also test_a_daily_loss_halt_is_told_per_account
    'cli.failed': {
        'title': 'omni CLI의 조회 명령 실패', 'since': '2026-11-25',
        'cause': 'CLI를 점검하는 감사가 없어 실패를 사용자가 먼저 만남(11-25 compare check가 장중에 거짓 실패)',
        'fix': 'scripts/strategy_compare.py check(표를 만든 시각까지 비교), intraday_audit.cli_health',
        'guard': 'test_the_cli_check_reaches_the_findings_and_its_absence_resolves_nothing'},
    'learning.error': {
        'title': '학습 관찰기가 실행 중인데 매 회차 실패', 'since': '2026-11-19',
        'cause': '오래 돈 프로세스가 다른 프로세스가 지운 원장 WAL 파일 핸들을 붙잡아 매 회차 sqlite3.DatabaseError(09:03~10:26, 새 프로세스는 정상)',
        'fix': 'scripts/isolated_paper_learning_watch.py DB_ERROR_RESTART(3회 연속이면 스스로 새 프로세스), intraday_audit.learning_rounds',
        'guard': 'test_a_learning_watcher_failing_every_round_fires'},  # also test_repeated_sqlite_errors_replace_the_process
    'shadow.silent': {
        'title': '이슈·테마 관찰 후보 기록이 멈춤', 'since': '2026-11-18',
        'cause': '1단계 기록 전용 후보(issue_candidates)가 돌지 않으면 매수 허용 여부를 판단할 표본이 쌓이지 않음',
        'fix': 'scripts/market_movers.py shadow_round (shadow-status.json)', 'guard': 'test_shadow_candidate_rounds_that_stop_or_fail_fire'},
    'shadow.source_error': {
        'title': '관찰 후보 출처(랭킹·DART·시세) 조회 실패', 'since': '2026-11-18',
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
