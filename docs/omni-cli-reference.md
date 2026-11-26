# omni 명령·설정 참조

`agent-harness/build_reference.py`가 코드에서 만든 문서입니다. 직접 고치지 않습니다. 사용법은 [omni CLI 가이드](omni-cli.md)에 있습니다.

명령 179개. 모든 명령에 `--json`을 붙일 수 있습니다. `--json`은 명령 앞뒤 어디에 써도 됩니다.

**성격**: 조회는 기록만 읽습니다. 외부 조회는 증권사·시세 서버를 부릅니다. 계획은 `~/.vibe-trading/cli`에 저장만 하고 설정·주문을 바꾸지 않습니다. 승인 코드는 사용자가 알림에서 받아 알려 주는 값입니다.

**답**은 `data`의 첫 단계 항목과 목록 한 줄의 항목 이름입니다. 기록에 따라 빠지거나 더 있을 수 있고, 계좌 키(`live-1` 등)는 적지 않았습니다.

## 차례

- [account](#account)
- [ai](#ai)
- [backup](#backup)
- [compare](#compare)
- [jev](#jev)
- [journal](#journal)
- [live](#live)
- [lt](#lt)
- [market](#market)
- [ops](#ops)
- [plan](#plan)
- [premarket](#premarket)
- [report](#report)
- [research](#research)
- [schedule](#schedule)
- [settings](#settings)
- [status](#status)
- [template](#template)
- [trade](#trade)
- [기타](#기타)
- [설정](#설정)

## account

계좌 설정 보기와 묶음 변경 계획

### omni account checklist

실주문이 나가기 위한 조건 4가지

- 성격: 조회
- 인자: `<key>` (필수): 계좌 키나 기간 키
- 예: `omni account checklist live-1`
- 답: `service` · `conditions`(registered, registered_for_real_orders, auto, live_orders, halt_off)

### omni account list

모든 계좌(계좌번호 제외)

- 성격: 조회
- 인자: 없음
- 예: `omni account list`
- 답: `accounts`[] (account, label, kind, engine, auto, live_orders)

### omni account match

모의 금액·한도를 실계좌에 맞추는 계획

- 성격: 계획(실계좌에 영향을 주면 승인 코드)
- 인자: `<live>` (필수): 실계좌 키 · `--positions <positions>`: 동시 보유 종목 수 (1~5) · 기본 2 · `--off`: 맞춤 끄기 · `--reason <reason>` (필수): 사용자가 말한 이유(기록에 남음)
- 예: `omni account match live-1 --positions 2 --reason "모의를 실계좌에 맞춤"`

### omni account quote

모의가 그 가격에 그 수량을 사면 이 실계좌가 살 수량(계산만)

- 성격: 조회
- 인자: `<key>` (필수): 계좌 키나 기간 키 · `<price>` (필수): 1주 가격(원) (1~) · `<qty>` (필수): 주식 수 (1~)
- 예: `omni account quote live-1 50000 20`
- 답: `amount`(shares, why) · `share`(shares, why) · `now_uses`

### omni account shadow

실계좌 가상 실행(미러 계산)

- 성격: 조회
- 인자: `--day <day>`: YYYY-MM-DD(기본 오늘)
- 예: `omni account shadow`

### omni account show

계좌 하나의 설정

- 성격: 조회
- 인자: `<key>` (필수): 계좌 키나 기간 키
- 예: `omni account show live-1`

## ai

AI 검색: 로컬 기록·내부 데이터(무료), 웹 검색·질문(유료)

### omni ai ask

투자·서비스 질문(유료, Liner로 전송; 내부 데이터 질문은 `ai local`)

- 성격: **유료 · 외부 전송** · 씀: 대화 이력
- 인자: `<question>` (필수): 질문 · `--talk <talk>`: 이어서 물을 대화 번호 · `--issue <issue>`: `ai issues`의 번호(시장 영향 분석) (1~5) · `--yes`: 요금과 외부 전송을 확인함
- 예: `omni ai ask "금리가 오르면 성장주는?" --yes`

### omni ai history

지난 AI 검색 대화 목록

- 성격: 조회
- 인자: `--find <find>`: 검색어 · `--archived`: 보관한 대화도 포함 · `--limit <limit>`: 최대 개수 (1~200) · 기본 20
- 예: `omni ai history --find 금리`
- 답: `talks`[]

### omni ai issues

최근 매매일지의 주요 이슈 5개(요금 없음)

- 성격: 조회
- 인자: 없음
- 예: `omni ai issues`
- 답: `day` · `issues`[] (number, stars, title, link)

### omni ai local

내부 데이터 질문(성과·체결·보유·상태, 실계좌 보유는 증권사 조회)

- 성격: 조회 · 외부 조회: 실계좌 보유를 물으면 NH
- 인자: `<question>` (필수): 질문
- 예: `omni ai local "오늘 모의 손익"`

### omni ai search

웹 검색(유료, Liner로 전송)

- 성격: **유료 · 외부 전송** · 씀: 대화 이력
- 인자: `<query>` (필수): 검색어 · `--yes`: 요금과 외부 전송을 확인함
- 예: `omni ai search "FOMC 일정" --yes`

### omni ai talk

지난 대화 하나

- 성격: 조회
- 인자: `<number>` (필수): 대화 번호
- 예: `omni ai talk 1`

### omni ai topics

추천 질문과 서비스 가이드 찾기

- 성격: 조회
- 인자: `<word>`: 검색어
- 예: `omni ai topics 적립식`
- 답: `guides`[] · `questions`[]

## backup

백업

### omni backup create

지금 백업(저장소 state/backups, 최근 14개만 보관)

- 성격: 씀: 백업 파일
- 인자: 없음
- 예: `omni backup create`

### omni backup import

백업 가져오기 계획(서비스가 모두 꺼져 있을 때, 승인 코드와 확인 문구)

- 성격: **계획 · 승인 코드 + 확인 문구 `백업 가져오기`**
- 인자: `<name>` (필수): 이름 · `--reason <reason>` (필수): 요청 이유(기록에 남음)
- 예: `omni backup import vibe-trading-state-20261124-1655.tar.gz --reason "새 맥으로 옮김"`

### omni backup list

백업 목록

- 성격: 조회
- 인자: 없음
- 예: `omni backup list`
- 답: `backups`[] (name, mb, time)

### omni backup tasks

예약 작업 정의 백업(저장소 ops/scheduled-tasks)

- 성격: 씀: ops/scheduled-tasks
- 인자: 없음
- 예: `omni backup tasks`

### omni backup verify

백업 하나를 검사

- 성격: 조회
- 인자: `<name>` (필수): 이름
- 예: `omni backup verify vibe-trading-state-20261124-1655.tar.gz`
- 답: `name` · `ok` · `problems`[]

## compare

성과 비교

### omni compare check

성과 자료 점검

- 성격: 조회
- 인자: 없음
- 예: `omni compare check`
- 답: `ledger` · `table` · `ok`

### omni compare export

성과 표를 CSV로 내보내기

- 성격: 씀: 지정한 새 CSV
- 인자: `--out <out>` (필수): 새 CSV 파일 · `--start <start>`: 시작 날짜(YYYY-MM-DD) · `--end <end>`: 끝 날짜(YYYY-MM-DD)
- 예: `omni compare export --out ./compare.csv`

### omni compare show

날짜 × 장부 성과

- 성격: 조회
- 인자: `--start <start>`: 시작 날짜(YYYY-MM-DD) · `--end <end>`: 끝 날짜(YYYY-MM-DD) · `--book <book>`: 장부 이름
- 예: `omni compare show`
- 답: `rows`[] (day, book, nav_index, contributed, pnl_day, pnl_cum, realized_day, unrealized, deployed, ret_day, drawdown, trades, buys, sells)

## jev

Jev 트레이딩 조회, 실행기 중단·재시작(승인 코드)

### omni jev blocks

매수가 막힌 이유(차단 사유)별 회차 수, 많은 순

- 성격: 조회
- 인자: `--day <day>`: YYYY-MM-DD(기본 오늘)
- 예: `omni jev blocks`
- 답: `day` · `blocked_cycles` · `reasons`[] (reason, label, cycles) · `strength_candidates`[] (reason, label, cycles)

### omni jev chart

그날 분봉과 체결(실행기 기록, 없으면 키움·Yahoo 조회)

- 성격: 조회 · 외부 조회: 기록이 없으면 키움·Yahoo, 전일 종가는 Yahoo
- 인자: `<code>` (필수): 6자리 종목코드 · `--day <day>`: YYYY-MM-DD(기본 오늘)
- 예: `omni jev chart 069500`

### omni jev costs

그날 거래 비용

- 성격: 조회
- 인자: `--day <day>`: YYYY-MM-DD(기본 오늘)
- 예: `omni jev costs`
- 답: `day` · `status` · `window`[] · `fills` · `buys` · `sells` · `stock_sells` · `gross_pnl` · `paper_cost` · `paper_cost_pct_of_turnover` · `real_cost_estimate` · `real_cost_pct_of_turnover` · `net_pnl_paper` · `net_pnl_real_estimate` · `rates`(commission_pct, sell_tax_pct, etf_sell_tax)

### omni jev events

실행기 이벤트(주문·체결·매도 사유·오류, 최신순)

- 성격: **계획 · 승인 코드**
- 인자: `--day <day>`: YYYY-MM-DD(기본 오늘) · `--event <event>`: 이벤트 이름 · `--reason <reason>`: 사용자가 말한 이유(기록에 남음) · `--symbol <symbol>`: 6자리 종목코드 · `--limit <limit>`: 최대 개수 (1~500) · 기본 20
- 예: `omni jev events --symbol 069500 --limit 20`

### omni jev explain

Jev 판단 설명(기록)

- 성격: 조회
- 인자: `--at <at>`: 이 시각 이후 첫 판단: HH:MM(오늘) 또는 YYYY-MM-DDTHH:MM · `--since <since>`: YYYY-MM-DD
- 예: `omni jev explain --at 10:30`
- 답: `time` · `question` · `digest` · `answer`(choice, confidence, probabilities, type) · `evidence`(why_chart, why_data, why_driver, why_flows, why_history, why_market, why_news) · `input_chars`(chart, chart_as_of, intraday_note, intraday_summary, investor_daily_net_qty, investor_note, market_playbook, market_regime_themes_global, news_checked_at, news_max_age_minutes, news_priority_minutes, own_track_record, premarket_context, recent_news_headlines) · `market_as_of` · `session_summary`(bars, change_30m_pct, change_60m_pct, from, from_high_pct, from_low_pct, high, last, low, open, range_position_pct, to, vs_open_pct, vs_vwap_pct) · `headlines`[] (age_min, title) · `instructions`

### omni jev oversold

낙폭과대 감시 종목과 오늘 기록, 선별 v2 재매수 감시

- 성격: 조회
- 인자: 없음
- 예: `omni jev oversold`
- 답: `universe` · `today`[] (time, event, passed, kospi, kosdaq, nasdaq_prev) · `v2_reentry`

### omni jev positions

모의 보유와 청산 계획

- 성격: 조회
- 인자: 없음
- 예: `omni jev positions`
- 답: `time` · `cash` · `holdings` · `exit_plans`

### omni jev record

Jev 성적 요약

- 성격: 조회
- 인자: 없음
- 예: `omni jev record`
- 답: `paper_trades`(closed_sells, wins, losses, avg_return_pct, recent) · `pick_direction_research` · `settled_calls` · `vibe_legacy_settled_excluded` · `vibe_settlements_note` · `recent_daily_lessons` · `note`

### omni jev requests

대기 중인 수동 매매 요청(조회만)

- 성격: 조회
- 인자: 없음
- 예: `omni jev requests`
- 답: `exit-requests` · `manual-buys`

### omni jev restart

모의 실행기 재시작 계획(미확인 주문·손절 근접이면 거절)

- 성격: **계획 · 승인 코드**
- 인자: `--reason <reason>` (필수): 요청 이유(기록에 남음)
- 예: `omni jev restart --reason "사용자가 말한 이유"`

### omni jev restrictions

제한 규칙과 켜짐 여부

- 성격: 조회
- 인자: 없음
- 예: `omni jev restrictions`
- 답: `rules`[] (rule, group, name, kind, setting, now)

### omni jev stop

모의 실행기 중단 계획(멈춘 동안 손절·익절 감시도 없음)

- 성격: **계획 · 승인 코드**
- 인자: `--reason <reason>` (필수): 요청 이유(기록에 남음)
- 예: `omni jev stop --reason "사용자가 말한 이유"`

### omni jev strategy

지금 시각·시황에서 예상되는 경로와 국면별 규칙(실제 판단 아님)

- 성격: 조회
- 인자: 없음
- 예: `omni jev strategy`
- 답: `route`[] · `description` · `regimes`[]

### omni jev unstop

모의 실행기 중단 해제 계획(미확인 주문 0건일 때, 시작은 하지 않음)

- 성격: **계획 · 승인 코드**
- 인자: `--reason <reason>` (필수): 요청 이유(기록에 남음)
- 예: `omni jev unstop --reason "사용자가 말한 이유"`

### omni jev v2-samples

낙폭과대 선별 v2 표본(사례집 재현, 체결 아님)

- 성격: 조회
- 인자: 없음
- 예: `omni jev v2-samples`
- 답: `samples`[] (day, market, drop_pct, prior_5d, nasdaq_prev, passed, index_5d, stocks_5d, stocks)

## journal

매매일지

### omni journal check

분석 일지 템플릿 검사(날짜 또는 기간 키)

- 성격: 조회
- 인자: `<key>` (필수): 계좌 키나 기간 키
- 예: `omni journal check 2026-11-24`
- 답: `text`

### omni journal due

그날 작성할 기간 일지

- 성격: 조회
- 인자: `--day <day>`: YYYY-MM-DD(기본 오늘)
- 예: `omni journal due`
- 답: `text`

### omni journal improvement

장 마감 개선안 자료

- 성격: 조회
- 인자: `--day <day>`: YYYY-MM-DD(기본 오늘)
- 예: `omni journal improvement`
- 답: `day` · `generated` · `trading_day` · `summary`(round_trips, wins, realized_krw, open_positions, note) · `round_trips`[] (symbol, open, close, held_min, entry_route, entry_basis, exit, pnl, return_pct, cost, entry_vs_day_high_pct, entry_vs_open_pct, entry_timing, mfe_pct) · `by_entry_route` · `by_exit` · `reentries`(n, realized_krw, trips) · `late_entries_after_1400`[] · `undersized_buys`[] · `entry_gates`(jev_wait, jev_low_confidence, entry_window_closed, jev_error, entry_conditions_met) · `restriction_blocks`(careful_reentry, volume, rising, cutoff, order_guard) · `guard`(buy_total_krw, new_symbols, limits_over_the_day, refusal_times) · `health`(error_events, stopped_at, crashes) · …

### omni journal list

저장된 일지 날짜

- 성격: 조회
- 인자: 없음
- 예: `omni journal list`
- 답: `days`[]

### omni journal merge

작성된 분석을 저장된 일지에 붙임(일지 DB에 씀)

- 성격: 외부 조회: 저장된 날이 아니면 여러 곳 · 씀: 일지 DB
- 인자: `--day <day>` (필수): YYYY-MM-DD(기본 오늘)
- 예: `omni journal merge --day 2026-11-24`

### omni journal period

기간 일지(2026-11 · 2026-Q4 · 2026-H2 · 2026)

- 성격: 조회
- 인자: `<key>` (필수): 계좌 키나 기간 키 · `--parts <parts>`: 쉼표로 고른 항목
- 예: `omni journal period 2026-11`
- 답: `kind` · `key` · `label` · `since` · `until` · `built` · `days` · `first` · `last` · `books`(paper, longterm, vibe) · `routes`(new_listing) · `monthly` · `market` · `issues`[] (day, title, link, category, stars, publisher) · `issue_categories` · `system`(errors, entry_gates) · `lessons`(retrospective, strategy_improvements, system_improvements, engine_notes, watch_next, summaries) · `perspective`(period, summary, views, retrospective, strategy_improvements, system_improvements, engine_notes, watch_next, top_issues, sources)

### omni journal rebuild

저장된 하루 일지의 자료를 다시 모음(외부 조회, 일지 DB에 씀)

- 성격: 외부 조회: 여러 곳 · 씀: 일지 DB
- 인자: `--day <day>` (필수): YYYY-MM-DD(기본 오늘)
- 예: `omni journal rebuild --day 2026-11-24`

### omni journal report

자동 리포트(저장된 일지)

- 성격: 조회
- 인자: `--day <day>`: YYYY-MM-DD(기본 오늘)
- 예: `omni journal report`
- 답: `text`

### omni journal review

장 마감 복기(최근 5회, 저장된 것)

- 성격: 조회
- 인자: 없음
- 예: `omni journal review`
- 답: `reviews`[] (time, day, regime, facts, lessons)

### omni journal show

하루 일지

- 성격: 조회
- 인자: `--day <day>`: YYYY-MM-DD(기본 오늘) · `--parts <parts>`: 쉼표로 고른 항목(기본: 요약)
- 예: `omni journal show`
- 답: `day` · `built` · `books`(paper, longterm) · `system`(trading_day, health, entry_gates, guard, restriction_blocks, errors, by_entry_route, by_exit, daily_review_lessons, improvement_report) · `flows`(market, stocks, pressure, investor_rankings) · `market` · `issues` · `analysts` · `market_context`(time, indices, themes) · `perspective`(day, summary, views, retrospective, strategy_improvements, system_improvements, engine_notes, watch_next, top_issues, sources)

### omni journal status

일지 상태

- 성격: 조회 · 외부 조회: KRX 휴장일
- 인자: `--day <day>`: YYYY-MM-DD(기본 오늘)
- 예: `omni journal status`
- 답: `day` · `trading_day` · `note` · `collected` · `fills` · `written`(json, md) · `merged`

### omni journal week

이번 주 장부별 성적

- 성격: 조회
- 인자: `--day <day>`: YYYY-MM-DD(기본 오늘)
- 예: `omni journal week`
- 답: `paper`(label, realized_krw, win_rate, trading_days, best_day, worst_day, by_symbol) · `longterm`(label, realized_krw, win_rate, trading_days, best_day, worst_day, by_symbol)

## live

실계좌 조회, 매수 중단·재개(승인 코드)

### omni live balance

실계좌 잔고와 보유(외부 조회, 주문 없음)

- 성격: 조회 · 외부 조회: NH
- 인자: `<key>` (필수): 계좌 키나 기간 키
- 예: `omni live balance live-1`

### omni live fills

확인된 실계좌 체결(날짜·주문 번호순, 마지막이 최신; 시각은 주문을 보낸 때)

- 성격: 조회
- 인자: `<key>` (필수): 계좌 키나 기간 키 · `--day <day>`: YYYY-MM-DD(기본 전체) · `--limit <limit>`: 최대 개수 (1~1,000) · 기본 50
- 예: `omni live fills live-1`
- 답: `fills`[] (time, symbol, side, qty, price, order_no)

### omni live halt

HALT 걸기 계획(승인 필요: 손절 주문도 멈춤)

- 성격: **계획 · 승인 코드**
- 인자: `--reason <reason>` (필수): 사용자가 말한 이유(기록에 남음)
- 예: `omni live halt --reason "사용자가 말한 이유"`

### omni live log

실계좌 실행기 회차 기록

- 성격: 조회
- 인자: `<key>` (필수): 계좌 키나 기간 키 · `--day <day>`: YYYY-MM-DD(기본 오늘) · `--limit <limit>`: 최대 개수 (1~200) · 기본 20
- 예: `omni live log live-1`
- 답: `rows`[] (time, account, mode, armed, action, reason)

### omni live longterm

장기투자 실계좌의 오늘 계획·가상 운용

- 성격: 조회
- 인자: `<key>` (필수): 계좌 키나 기간 키
- 예: `omni live longterm live-2`
- 답: `day` · `orders`[] · `notes`[] · `virtual_nav`[] (day, value, capital)

### omni live off

실주문 끄기 계획(승인 필요: 손절 주문도 멈춤, 다시 켜기는 `live on`)

- 성격: **계획 · 승인 코드**
- 인자: `<key>` (필수): 계좌 키나 기간 키 · `--reason <reason>` (필수): 사용자가 말한 이유(기록에 남음)
- 예: `omni live off live-1 --reason "잠시 중단"`

### omni live on

실주문 켜기 계획(승인 코드와 확인 문구)

- 성격: **계획 · 승인 코드 + 확인 문구 `<계좌 이름> 실주문`**
- 인자: `<account>` (필수): 실계좌 키(`live-1` 등) · `--reason <reason>` (필수): 요청 이유(기록에 남음)
- 예: `omni live on live-1 --reason "다시 시작"`

### omni live pause

모든 실계좌의 신규 매수 중단(바로 적용, 보유 손절은 계속)

- 성격: 씀: 모든 실계좌 신규 매수 중단(바로 적용)·알림 발송
- 인자: `--reason <reason>` (필수): 사용자가 말한 이유(기록에 남음)
- 예: `omni live pause --reason "사용자가 말한 이유"`

### omni live resume

실계좌 신규 매수 재개 계획(미확인 주문·오늘 손실 정지가 있으면 거절)

- 성격: **계획 · 승인 코드**
- 인자: `--reason <reason>` (필수): 요청 이유(기록에 남음)
- 예: `omni live resume --reason "사용자가 말한 이유"`

### omni live review

모의 → 실계좌 적용 검토

- 성격: 조회
- 인자: 없음
- 예: `omni live review`
- 답: `status` · `as_of` · `account_scope` · `costs`(available, net_return_pct, reason) · `promotion_criteria`(available, reason) · `live_guards`(evaluated) · `limitations`[] · `price_observations`(available, observed, insufficient, not_recorded, latest, record_dates, independent_samples) · `jev_research`(available, unique_predictions, latest, independent_samples) · `paper_fills`(available, local_reconciled_fills, closed_units, open_units, unresolved_orders, latest_confirmation, broker_independently_checked)

### omni live status

실주문 스위치·HALT·신규 매수 중단·하루 손실 정지

- 성격: 조회
- 인자: 없음
- 예: `omni live status`
- 답: `live_accounts` · `halt` · `buy_paused`

### omni live unhalt

NH 주문 HALT 해제 계획(승인 코드와 확인 문구, 다른 증권사 정지는 유지)

- 성격: **계획 · 승인 코드 + 확인 문구 `HALT 해제`**
- 인자: `--reason <reason>` (필수): 요청 이유(기록에 남음)
- 예: `omni live unhalt --reason "사용자가 말한 이유"`

## lt

장기투자 조회, 엔진 중지(승인 코드)

### omni lt audit-blank

복기·감사 틀

- 성격: 조회
- 인자: `--key <key>`: 날짜 또는 기간 키(2026-11-24 · 2026-W48 · 2026-11)
- 예: `omni lt audit-blank`
- 답: `text`

### omni lt audit-check

복기·감사 검사

- 성격: 조회
- 인자: `--key <key>`: 날짜 또는 기간 키(2026-11-24 · 2026-W48 · 2026-11)
- 예: `omni lt audit-check`
- 답: `ok` · `problems`[]

### omni lt audit-data

복기·감사 자료

- 성격: 조회 · 외부 조회: KRX 휴장일
- 인자: `--key <key>`: 날짜 또는 기간 키(2026-11-24 · 2026-W48 · 2026-11)
- 예: `omni lt audit-data`
- 답: `key` · `kind` · `days`[] · `started` · `coverage`[] (day, kinds, missing, story_source, story_sources, errors, missed, skips, report, journal_view, nav) · `findings`[] · `settings_changes`[] (time, key, old, new, who) · `regimes`[] · `regime_changes` · `trades`(rows, executed, orders, notes_by_module, reasons) · `dca`[] (day, orders, reason, seen) · `nav`(actual, auto, base, contributed, dca_auto) · `live` · `ideas`(latest_screen, picks, theses) · `evidence`(count, unfilled, reconstructed, used_parts, summaries) · `settings`(mode, execute, enabled, modules, dca_on) · `reports`[] · `previous_audits`[] · `masters`(principles, morning, yeo_accounts) · `fills`(virtual, live, recovered, owed) · …

### omni lt audit-status

감사 상태

- 성격: 조회
- 인자: 없음
- 예: `omni lt audit-status`
- 답: `keys`[] · `latest`

### omni lt cases-refresh

장기 과거 사례 다시 만들기(외부 시세 조회, 사례 DB에 씀)

- 성격: 외부 조회: 시세 서버 · 씀: 장기 사례 DB
- 인자: 없음
- 예: `omni lt cases-refresh`

### omni lt chart

엔진 ETF 일봉 종가와 타점(외부 조회 1회)

- 성격: 조회 · 외부 조회: 시세 서버
- 인자: `<key>` (필수): 계좌 키나 기간 키 · `--days <days>`: 최근 일수 (5~500) · 기본 120
- 예: `omni lt chart NDX --days 60`

### omni lt dca-preview

적립식 적용값과 이번 회차 예시(amount_krw=200000 …, 저장·주문 안 함)

- 성격: 조회
- 인자: `<book>`: 장부(`actual`, `live-2`) · `<pairs>…`: `주소=값` 여러 개
- 예: `omni lt dca-preview actual amount_krw=300000`
- 답: `book` · `on` · `regime` · `prices_from` · `fields`[] (label, value) · `example`[] (name, weight, price, krw, qty, carry, excluded) · `notes`[] · `next_day`

### omni lt ideas

미국 종목 아이디어 상태(참고, 주문 없음)

- 성격: 조회
- 인자: 없음
- 예: `omni lt ideas`
- 답: `ready` · `day` · `previous` · `picks`[] (day, master, rank, ticker, name, price, price_date, score, metrics, reasons, etfs, new) · `scorecard`[] · `quarter_13f` · `managers`[] (manager, quarter, master, firm, cik, filing, portfolio_value_usd, positions, changes) · `runs`(universe, weekly)

### omni lt levels

타점 지도

- 성격: 조회
- 인자: 없음
- 예: `omni lt levels`
- 답: `day` · `levels`[] (asset, horizon, master, side, price, size, condition, kind, case, active, distance, status)

### omni lt log

판단 기록(regime·story·plan·order·dca·news …)

- 성격: 조회
- 인자: `--kind <kind>`: 기록 종류 · `--detail`: 지표·테마 판정·목표도 표시 · `--limit <limit>`: 최대 개수 (1~500) · 기본 20
- 예: `omni lt log --kind story --detail`
- 답: `rows`[] (time, day, kind, regime, mode, orders, reason, indicators, targets)

### omni lt nav

장부별 순자산

- 성격: 조회
- 인자: `--days <days>`: 최근 일수 (1~400) · 기본 10
- 예: `omni lt nav`
- 답: `nav`[] (day, book, value)

### omni lt orders

가상·계획 주문과 근거(--day로 하루만)

- 성격: 조회
- 인자: `--why`: 근거 요약 포함 · `--day <day>`: YYYY-MM-DD(기본 전체) · `--limit <limit>`: 최대 개수 (1~200) · 기본 10
- 예: `omni lt orders --why --day 2026-11-23`
- 답: `orders`[] (time, day, book, kind, key, code, side, qty, price, filled, fill_price, why, masters, summary)

### omni lt preview

오늘 목표 비중: 저장값과 바꾼 값(lt.modules.x=on lt.weights.NDX=0.25 …, 저장 안 함)

- 성격: 조회
- 인자: `<pairs>…`: `주소=값` 여러 개
- 예: `omni lt preview lt.weights.NDX=0.25 lt.weights.SPX=0.35`
- 답: `day` · `strategy` · `targets`[]

### omni lt recipes

레시피 비교 가상 장부(1억 원, 시작일·지금·손익)

- 성격: 조회
- 인자: 없음
- 예: `omni lt recipes`
- 답: `recipes`[] (account, start, last, days, value, capital, pnl_pct)

### omni lt recommend

지금 국면의 거장 추천(바꾸지 않음)

- 성격: 조회
- 인자: 없음
- 예: `omni lt recommend`
- 답: `regime` · `recommended`[] · `modules`[] (module, name, grade, why, evidence, on) · `dca_auto_profile`

### omni lt report-blank

전략 리포트 틀(사실 표)

- 성격: 조회 · 외부 조회: 시세 서버·KRX
- 인자: `--day <day>`: YYYY-MM-DD(기본 오늘)
- 예: `omni lt report-blank`
- 답: `text`

### omni lt report-check

전략 리포트 검사

- 성격: 조회 · 외부 조회: KRX 휴장일
- 인자: `--day <day>`: YYYY-MM-DD(기본 오늘)
- 예: `omni lt report-check`
- 답: `ok` · `problems`[]

### omni lt report-data

전략 리포트 자료

- 성격: 조회 · 외부 조회: 시세 서버·KRX
- 인자: `--day <day>`: YYYY-MM-DD(기본 오늘)
- 예: `omni lt report-data`
- 답: `day` · `plan_day` · `plan_is_today` · `regime`(now, raw, day, regime, pending, pending_days, since) · `indicators`(as_of, ndx, dd, gap, slope, day_ret, vix, usdkrw, season_month_mean, sats, ath, krx_close) · `macro`(as_of, source, series, events, errors) · `mode` · `active`[] · `recommended`[] · `impacts`(dalio_rebalance, yeo_reservoir, marks_pendulum, williams_trend, williams_rebound, lynch_story, buffett_lowturn) · `weights` · `levels`[] (asset, horizon, master, side, price, size, condition, kind, case, active, distance, status, evidence) · `positions` · `stories`(time, stories, market_view, source) · `account`(holdings, nav, sleeve, reservoir, yeo_book, yeo, sleeve_names, last_plan, …

### omni lt report-status

전략 리포트 상태

- 성격: 조회 · 외부 조회: KRX 휴장일
- 인자: `--day <day>`: YYYY-MM-DD(기본 오늘)
- 예: `omni lt report-status`
- 답: `day` · `plan_day` · `plan_is_today` · `trading_day` · `written` · `report` · `json`

### omni lt stop

장기 엔진 중지 요청 계획

- 성격: **계획 · 승인 코드**
- 인자: `--reason <reason>` (필수): 요청 이유(기록에 남음)
- 예: `omni lt stop --reason "사용자가 말한 이유"`

### omni lt today

오늘 판단·국면·계획

- 성격: 조회
- 인자: `--full`: 긴 판단 이유도 모두 표시
- 예: `omni lt today`
- 답: `mode` · `books`(actual) · `execute` · `enabled` · `regime`(day, regime, pending, pending_days, since) · `plan`(day, regime, active, mode, recommended, weights) · `recent`[] (time, day, kind, regime, mode, reason)

### omni lt unstop

장기 엔진 중지 요청 해제 계획

- 성격: **계획 · 승인 코드**
- 인자: `--reason <reason>` (필수): 요청 이유(기록에 남음)
- 예: `omni lt unstop --reason "사용자가 말한 이유"`

### omni lt watch-state

상시 감시 상태

- 성격: 조회
- 인자: 없음
- 예: `omni lt watch-state`
- 답: `day` · `time` · `found`

### omni lt yeo

여운봉 매매법 장부의 낙폭·다음 단계·할 일

- 성격: 조회
- 인자: 없음
- 예: `omni lt yeo`
- 답: `actual`(label, index, dd, used, next_step, next_action)

## market

시장 자료

### omni market bars

NH 1분봉 30개(외부 조회 1회)

- 성격: 조회 · 외부 조회: NH
- 인자: `<code>` (필수): 6자리 종목코드
- 예: `omni market bars 005930`

### omni market brief

ETF 7종 시세와 실행기 요약(외부 조회)

- 성격: 외부 조회: NH·뉴스 · 씀: 공유 뉴스 캐시
- 인자: 없음
- 예: `omni market brief`

### omni market context

시황(시장 국면·수급·테마·해외 지표)

- 성격: 조회
- 인자: `--keys <keys>`: 쉼표로 고른 항목만
- 예: `omni market context --keys indices,themes`
- 답: `indices` · `themes`(source, groups, top, bottom, total)

### omni market investors

종목의 외국인·기관·개인 순매수(직전 확정일·오늘 잠정, 외부 조회 1회)

- 성격: 조회 · 외부 조회: NH
- 인자: `<code>` (필수): 6자리 종목코드
- 예: `omni market investors 005930`

### omni market listings

신규상장 목록

- 성격: 조회
- 인자: `--keys <keys>`: 쉼표로 고른 항목만
- 예: `omni market listings`
- 답: `day` · `checked` · `ok` · `rows`[] (name, code, listed)

### omni market longterm

미국 장기 지표·거시 배경(장기 엔진의 아침 판단)

- 성격: 조회
- 인자: 없음
- 예: `omni market longterm`
- 답: `day` · `regime`(day, regime, pending, pending_days, since) · `indicators`(as_of, ndx, dd, gap, slope, day_ret, vix, usdkrw, season_month_mean, sats, ath, krx_close) · `temperature`(score, label, parts) · `macro`(as_of, source, series, events, errors)

### omni market movers

순위 후보

- 성격: 조회
- 인자: `--keys <keys>`: 쉼표로 고른 항목만
- 예: `omni market movers`
- 답: `time` · `scanned` · `source` · `quote_source` · `universe` · `seconds` · `rankings`(trading_value, volume, rise, fall) · `candidates` · `theme_picks`[] · `rows`

### omni market ndx

나스닥100 장중 관측(저장된 것)

- 성격: 조회
- 인자: `--day <day>`: YYYY-MM-DD(기본 최신)
- 예: `omni market ndx`
- 답: `status` · `source` · `threshold` · `session` · `peak` · `regular_start` · `regular_end` · `reason` · `peak_day` · `peak_as_of` · `engine_plan_day` · `plan_threshold` · `checked_at` · `alert` · `crossing` · `close`(status, source, session, close, drawdown, threshold, qualifies)

### omni market news

모든 실행기가 함께 쓰는 최근 시장 뉴스(저장된 것)

- 성격: 조회
- 인자: 없음
- 예: `omni market news`
- 답: `fetched` · `rows`[] (title, published, source, topic, publisher) · `error`

### omni market premarket

장전 브리프

- 성격: 조회
- 인자: `--keys <keys>`: 쉼표로 고른 항목만
- 예: `omni market premarket`
- 답: `version` · `time` · `trading_day` · `watch_codes`[] · `watch_roles` · `sections`(market, futures, night_futures, news, nxt_quotes, krx, dart, listings, nxt_gap, nxt_premarket) · `summary`[] · `strategy`[] · `report_template`(version, trading_day, generated_at, title, sections)

### omni market quote

NH 현재가·호가(외부 조회 1회)

- 성격: 조회 · 외부 조회: NH
- 인자: `<code>` (필수): 6자리 종목코드
- 예: `omni market quote 005930`

### omni market refresh

시장 자료를 다시 모아 저장(화면의 갱신 버튼과 같음, 외부 조회 여러 번)

- 성격: 외부 조회: 여러 곳 · 씀: 시장 자료 파일
- 인자: 없음
- 예: `omni market refresh`

### omni market regime

국면 관측

- 성격: 조회
- 인자: `--keys <keys>`: 쉼표로 고른 항목만
- 예: `omni market regime`
- 답: `time` · `status` · `minute_event_time` · `source` · `symbols`

### omni market watchlist

Jev 관심종목

- 성격: 조회
- 인자: `--keys <keys>`: 쉼표로 고른 항목만
- 예: `omni market watchlist`
- 답: `time` · `selected`[] · `jev_choice` · `jev_confidence` · `probabilities` · `stocks` · `market` · `market_news`[] (title, published, source, topic, publisher) · `chart_sha256`

## ops

운영

### omni ops api

API 호출 부하와 거절

- 성격: 조회
- 인자: 없음
- 예: `omni ops api`
- 답: `findings`[] · `providers`[] · `blocked`[]

### omni ops api-bench

저장된 API 실측 결과(새로 재지 않음)

- 성격: 조회
- 인자: `<need>`: 실측 이름
- 예: `omni ops api-bench`
- 답: `results`[]

### omni ops audit

장중 감사 자료(문제 이력 DB에 기록)

- 성격: 외부 조회: KRX 휴장일 · 씀: 문제 이력 DB
- 인자: `--since <since>`: HH:MM
- 예: `omni ops audit`

### omni ops day-start

거래일 시작 계획(모의 실행기·학습·국면 관찰기)

- 성격: **계획 · 승인 코드** · 외부 조회: KRX 휴장일
- 인자: `--reason <reason>` (필수): 요청 이유(기록에 남음)
- 예: `omni ops day-start --reason "사용자가 말한 이유"`

### omni ops doctor

새 기기·계정 점검

- 성격: 조회
- 인자: 없음
- 예: `omni ops doctor`
- 답: `runtime_root` · `runtime_exists` · `env_present`[] · `env_empty`[] · `env_missing_vs_example`[] · `services`(shared-etf-paper, shared-etf-learning, shared-etf-regime, shared-etf-dashboard, market-movers, premarket, trading-day-start, preflight, vibe-longterm, longterm-watch, api-watch, us-ideas, trading-backup, state-sync) · `running_trading`[] · `tasks_missing`[] · `tasks_installed`[] · `tasks_without_schedule`[] · `tasks_not_in_manifest`[] · `latest_backup` · `latest_backup_age_h`

### omni ops incidents

문제 재발 이력

- 성격: 조회
- 인자: `--since <since>`: 이 날짜부터(YYYY-MM-DD)
- 예: `omni ops incidents`
- 답: `rows`[] (key, prefix, first_seen, last_seen, count, recurrences, status, last_detail, resolved_at, title)

### omni ops logs

서비스 로그 끝부분

- 성격: 조회
- 인자: `<service>` (필수): 서비스 이름(`ops services`) · `--lines <lines>`: 줄 수 (1~500) · 기본 40
- 예: `omni ops logs preflight --lines 40`
- 답: `lines`[]

### omni ops notify-preview

보내지 않은 알림 미리보기(발송 없음)

- 성격: 조회
- 인자: 없음
- 예: `omni ops notify-preview`
- 답: `text`

### omni ops notify-test

알림 채널 시험 발송 계획

- 성격: **계획 · 승인 코드**
- 인자: `--reason <reason>` (필수): 요청 이유(기록에 남음)
- 예: `omni ops notify-test --reason "사용자가 말한 이유"`

### omni ops schedule

그날 자동 작업(서비스·예약)

- 성격: 조회
- 인자: `--day <day>`: YYYY-MM-DD(기본 오늘)
- 예: `omni ops schedule`
- 답: `jobs`[]

### omni ops service-files

자동 실행 정의 파일 만들기(등록·시작은 하지 않음)

- 성격: 씀: 지정한 새 폴더
- 인자: `--system <system>` (필수): 운영체제 `macos` \| `windows` \| `linux` · `--out <out>` (필수): 새로 만들 폴더
- 예: `omni ops service-files --system linux --out ./services`

### omni ops services

서비스 일정과 상태(macOS·윈도우·리눅스)

- 성격: 조회
- 인자: 없음
- 예: `omni ops services`
- 답: `system` · `services`[] (service, name, registered, running, pid, last_exit, schedule)

### omni ops skills

프로젝트 스킬

- 성격: 조회
- 인자: 없음
- 예: `omni ops skills`
- 답: `skills`[]

### omni ops start

등록된 서비스 시작 계획(주문 서비스는 거래일 08:00~15:00, 미확인 주문 0건일 때)

- 성격: **계획 · 승인 코드** · 외부 조회: KRX 휴장일
- 인자: `<service>` (필수): 서비스 이름(`ops services`) · `--reason <reason>` (필수): 요청 이유(기록에 남음)
- 예: `omni ops start market-movers --reason "멈춰 있어서"`

## plan

계획 보기·승인 코드·적용(설정과 요청 공통)

### omni plan apply

계획 적용(실계좌에 영향을 주면 승인 코드 필요)

- 성격: 외부 조회: 계획에 따라 · 씀: 계획한 변경·요청·발송
- 인자: `<plan>` (필수): 계획 번호 · `--code <code>`: 알림으로 받은 승인 코드 · `--phrase <phrase>`: 확인 문구(계획에 나온 그대로) · `--during-market`: 장중(08:55~15:20)에도 적용
- 예: `omni plan apply 1a2b3c4d --code ABCD2345`

### omni plan approve

승인 코드를 알림 채널로 보냄(적용하지 않음)

- 성격: 씀: 승인 코드 발송
- 인자: `<plan>` (필수): 계획 번호
- 예: `omni plan approve 1a2b3c4d`

### omni plan drop

아직 적용하지 않은 계획 버리기(승인 필요 없음)

- 성격: 씀: 계획 상태
- 인자: `<plan_id>` (필수): 계획 번호
- 예: `omni plan drop 1a2b3c4d`

### omni plan list

최근 계획

- 성격: 조회
- 인자: 없음
- 예: `omni plan list`
- 답: `plans`[] (plan, status, level, affects, reason, how, title, created, expires, changes)

### omni plan show

계획 보기

- 성격: 조회
- 인자: `<plan>` (필수): 계획 번호
- 예: `omni plan show 1a2b3c4d`

## premarket

장전

### omni premarket check

장전 분석 템플릿 검사

- 성격: 조회
- 인자: `--day <day>`: YYYY-MM-DD(기본 오늘)
- 예: `omni premarket check`
- 답: `text`

### omni premarket preflight

장전 사전 점검(발송 없음, 외부 조회, 점검 기록 한 줄이 남음)

- 성격: 외부 조회: NH · 씀: 점검 기록 한 줄
- 인자: 없음
- 예: `omni premarket preflight`

### omni premarket read

장전 상세 분석

- 성격: 조회
- 인자: `--day <day>`: YYYY-MM-DD(기본 오늘)
- 예: `omni premarket read`
- 답: `text`

### omni premarket status

장전 브리프 상태

- 성격: 조회 · 외부 조회: KRX 휴장일
- 인자: 없음
- 예: `omni premarket status`
- 답: `trading_day`(date, open, note) · `time` · `age_minutes` · `sections`(market, futures, night_futures, news, nxt_quotes, krx, dart, listings, nxt_gap, nxt_premarket) · `watch` · `report` · `written` · `stale` · `checked`[]

### omni premarket template

장전 분석 틀

- 성격: 조회
- 인자: 없음
- 예: `omni premarket template`
- 답: `text`

## report

리포트

### omni report check-page

만든 HTML 페이지 검사

- 성격: 조회
- 인자: `<page>` (필수): HTML 파일
- 예: `omni report check-page ./page.html`

### omni report list

리포트 파일 목록

- 성격: 조회
- 인자: 없음
- 예: `omni report list`
- 답: `reports`[] (kind, key, file)

### omni report pages

발행 기록

- 성격: 조회
- 인자: 없음
- 예: `omni report pages`
- 답: `text`

### omni report preview

리포트 HTML 미리보기(발행·발송·기록 없음)

- 성격: 씀: 지정한 새 폴더
- 인자: `<kind>` (필수): 리포트 종류(`report list`) · `<markdown>` (필수): .md 파일 · `--out <out>` (필수): 새로 만들 폴더
- 예: `omni report preview journal ./journal.md --out ./preview`

### omni report publish

리포트 다시 게시 계획(공개 페이지)

- 성격: **계획 · 승인 코드**
- 인자: `<kind>` (필수): 리포트 종류(`report list`) · `<key>` (필수): 계좌 키나 기간 키 · `--reason <reason>` (필수): 요청 이유(기록에 남음)
- 예: `omni report publish journal 2026-11-24 --reason "오타 고침"`

### omni report read

리포트 본문

- 성격: 조회
- 인자: `<kind>` (필수): 리포트 종류(`report list`) · `<key>` (필수): 계좌 키나 기간 키 · `--lines <lines>`: 앞부분만(요약은 대개 첫 20줄) (1~) · `--links`: 링크 주소도 표시
- 예: `omni report read journal 2026-11-24 --lines 20`
- 답: `text` · `lines`

### omni report send

리포트 링크 다시 발송 계획

- 성격: **계획 · 승인 코드**
- 인자: `<kind>` (필수): 리포트 종류(`report list`) · `<key>` (필수): 계좌 키나 기간 키 · `--reason <reason>` (필수): 요청 이유(기록에 남음)
- 예: `omni report send journal 2026-11-24 --reason "다시 보냄"`

### omni report settings

발행 설정(값은 표시하지 않음)

- 성격: 조회
- 인자: 없음
- 예: `omni report settings`
- 답: `repo` · `url` · `branch` · `push` · `missing`[] · `ready` · `token_set`

## research

연구 기록

### omni research cases

비슷한 과거 급락 사례(사례집)

- 성격: 조회
- 인자: `<code>` (필수): 6자리 종목코드 · `<drop>` (필수): 하락률 %(−6처럼 써도 됨)
- 예: `omni research cases 005930 -6`
- 답: `n` · `same_code` · `r1_med` · `r3_med` · `r5_med` · `r3_from_low_med` · `win_r3_pct` · `further_fall_pct` · `rebound_pct` · `max_dn_5_med` · `examples`[]

### omni research ipo

신규주 그림자 성적

- 성격: 조회
- 인자: 없음
- 예: `omni research ipo`
- 답: `current`(label, days, listing_days, trades, sum_pct, win_rate) · `vwap_base`(label, days, listing_days, trades, sum_pct, win_rate) · `vwap_dip`(label, days, listing_days, trades, sum_pct, win_rate)

### omni research issues

이슈 그림자 후보 성적

- 성격: 조회
- 인자: 없음
- 예: `omni research issues`
- 답: `theme`(n, avg_r30, avg_r_close, up_share, would_buy)

### omni research ledger

거래 원장 이벤트 수

- 성격: 조회
- 인자: 없음
- 예: `omni research ledger`
- 답: `events`[] (event, n, last)

### omni research nxt

NXT 가상 매매 감사

- 성격: 조회
- 인자: 없음
- 예: `omni research nxt`
- 답: `ok` · `problems`[] · `days`

### omni research oversold

낙폭과대 사후 결과

- 성격: 조회
- 인자: `--days <days>`: 최근 일수 · 기본 30
- 예: `omni research oversold`
- 답: `sessions`[] · `events` · `groups` · `rows`[] (day, code, kind, status, drop_pct, need_pct, atr_pct, atr_multiple, nasdaq_prev_pct, close_vs_low_pct, gap_recovered_pct, next_close_pct, rule, no_bars)

### omni research replay

장기 전략 과거 재현(외부 시세 조회, 몇 분 걸림, 기록하지 않음)

- 성격: 조회 · 외부 조회: 시세 서버
- 인자: `<name>` (필수): 이름 `dca` \| `dca-masters` \| `dca-presets` \| `dca-pace` \| `dca-v3` \| `yeo` \| `yeo-dca` \| `engine`
- 예: `omni research replay yeo`

## schedule

예약 작업 목록·백업·AI 실행 프로필 이전

### omni schedule device

이전용 기기 ID와 서명 지문 확인

- 성격: 씀: 이전용 기기 신원
- 인자: 없음
- 예: `omni schedule device`

### omni schedule export

비공개 파일에 예약 정의 내보내기

- 성격: 씀: 비공개 예약 번들
- 인자: `--out <out>` (필수): 새 번들 파일 경로
- 예: `omni schedule export --out schedules.json`

### omni schedule handoff

원본 실행권을 해제하고 지정 기기용 인수 영수증 발급

- 성격: 씀: 원본 실행권 해제 및 인수 영수증
- 인자: `--to-device <to_device>` (필수): 인수할 기기의 ID · `--job <job>` (필수): 원본 실행권을 해제할 작업 ID · `--out <out>` (필수): 서명된 인수 영수증 파일
- 예: `omni schedule handoff --to-device device-1234567890abcdef1234567890abcdef --job "Claude 예약 작업:trading-intraday-report" --out receipt.json`

### omni schedule list

예약 작업의 일정·모델·프롬프트 보존 상태 조회

- 성격: 조회
- 인자: 없음
- 예: `omni schedule list`

### omni schedule plan

전체 또는 선택한 예약 작업의 이전 계획 작성

- 성격: 씀: 예약 이전 계획
- 인자: `--to <target>` (필수): 대상 프로필 ID · `--from <bundle_path>`: 내보낸 예약 번들 · `--job <job>`: 이전할 작업 ID(반복 가능) · `--all`: 전체 작업을 계획에 포함 · `--receipt <receipt>`: 원본에서 받은 인수 영수증 · `--source-key <source_key>`: 원본 기기에서 확인한 서명 지문
- 예: `omni schedule plan --to codex:work --all`

### omni schedule probe

검증용 입력으로 지정 모델 호출 확인(사용량 발생)

- 성격: **유료 · 외부 전송** · 씀: 모델 호출 확인
- 인자: `<profile>` (필수)
- 예: `omni schedule probe codex:work`

### omni schedule profile-add

현재 로그인 계정을 작업 실행 프로필로 등록

- 성격: 씀: AI 실행 프로필
- 인자: `<provider>` (필수): `claude` \| `codex` \| `antigravity` · `<alias>` (필수) · `--model <model>` (필수): 실행할 정확한 모델 ID · `--config-dir <config_dir>`: 선택한 CLI 인증 설정 폴더 · `--credential-file <credential_file>`: Antigravity 전용 Gemini API 키 파일
- 예: `omni schedule profile-add codex work --model <모델-ID>`

### omni schedule profiles

등록된 AI 실행 프로필 조회

- 성격: 조회
- 인자: 없음
- 예: `omni schedule profiles`

### omni schedule receipt-export

기발급 영수증을 별도 파일로 다시 저장

- 성격: 씀: 기발급 인수 영수증 파일
- 인자: `<transfer_id>` (필수) · `--out <out>` (필수): 새 인수 영수증 파일 경로
- 예: `omni schedule receipt-export transfer-1234567890abcdef1234567890abcdef --out copy.json`

### omni schedule rollback

이전에서 바꾼 Omni 실행 경로 되돌리기

- 성격: 씀: 예약 실행 경로 복원
- 인자: `<plan_id>` (필수): 계획 번호
- 예: `omni schedule rollback schedule-123456abcdef`

### omni schedule validate-model

비식별 입력과 기대 결과로 대체 모델 비교(사용량 발생)

- 성격: **유료 · 외부 전송** · 씀: 작업별 검증 결과
- 인자: `<plan_id>` (필수): 계획 번호 · `<job_id>` (필수) · `--fixture <fixture>` (필수): input과 expected를 담은 비식별 JSON 자료
- 예: `omni schedule validate-model schedule-123456abcdef "Claude 예약 작업:intraday-audit-hourly" --fixture fixture.json`

### omni schedule verify

이전 상태와 첫 회차 기록 조회

- 성격: 조회
- 인자: `<plan_id>` (필수): 계획 번호
- 예: `omni schedule verify schedule-123456abcdef`

## settings

설정 조회·계획·적용

### omni settings apply

계획 적용(실계좌에 영향을 주면 승인 코드 필요)

- 성격: 외부 조회: 계획에 따라 · 씀: 계획한 변경·요청·발송
- 인자: `<plan>` (필수): 계획 번호 · `--code <code>`: 알림으로 받은 승인 코드 · `--phrase <phrase>`: 확인 문구(계획에 나온 그대로) · `--during-market`: 장중(08:55~15:20)에도 적용
- 예: `omni settings apply 1a2b3c4d --code ABCD2345`

### omni settings approve

승인 코드를 알림 채널로 보냄(적용하지 않음)

- 성격: 씀: 승인 코드 발송
- 인자: `<plan>` (필수): 계획 번호
- 예: `omni settings approve 1a2b3c4d`

### omni settings explain

범위·실계좌 영향·최근 변경

- 성격: 조회
- 인자: `<address>` (필수): 설정 주소
- 예: `omni settings explain strategy.entry_confidence`
- 답: `setting` · `now` · `default` · `label` · `range` · `takes_effect` · `level` · `affects`[] · `how`[] · `files`[] · `recent`[]

### omni settings export

설정 값 내보내기(계좌번호 제외)

- 성격: 조회
- 인자: `--group <name>`: 설정 묶음(strategy · accounts · lt · book · dca · tunables · ai · prefs) `strategy` \| `accounts` \| `lt` \| `book` \| `dca` \| `tunables` \| `ai` \| `prefs`
- 예: `omni settings export`
- 답: `strategy`(entry_confidence, sell_confidence, take_profit_half_pct, take_profit_all_pct, stop_loss_pct, add_step_pct, trend_half_pct, trend_trail_pct, overnight_max_manwon, swing_max_days, strength_top_n, strength_min_prob, strength_change_min, strength_change_max) · `accounts` · `lt`(enabled, mode, auto_live, execute) · `book` · `dca` · `tunables`(session_open, last_order, flatten_lead_min, paper_max_order_qty, paper_max_total_qty, read_failure_limit, losing_streak, losing_cooldown_min, ipo_start, ipo_max_scan, vibe_interval_min, vibe_buy_score, vibe_sell_score, vibe_full_score_krw) · `ai`(jev, decision) · `prefs`(status_blink, start_tab, refresh_s, quotes_auto, money_unit, font_scale, …

### omni settings get

설정 값

- 성격: 조회
- 인자: `<addresses>…` (필수): 설정 주소 여러 개
- 예: `omni settings get strategy.entry_confidence strategy.scale_max_adds`
- 답: `settings`[] (setting, now, default, label, range, takes_effect)

### omni settings history

변경 이력(번호로 되돌리기)

- 성격: 조회
- 인자: `--group <kind>`: 설정 묶음(strategy · accounts · lt · book · dca · tunables · ai · prefs) · `--since <since>`: YYYY-MM-DD · `--limit <limit>`: 최대 개수 (1~1,000) · 기본 30
- 예: `omni settings history --group strategy --limit 10`
- 답: `changes`[] (id, time, kind, old, new, who, setting)

### omni settings list

설정과 기본값·범위

- 성격: 조회
- 인자: `--group <name>`: 설정 묶음(strategy · accounts · lt · book · dca · tunables · ai · prefs) `strategy` \| `accounts` \| `lt` \| `book` \| `dca` \| `tunables` \| `ai` \| `prefs` · `--changed`: 기본값과 다른 것만 · `--find <word>`: 주소·이름 검색어(여러 개면 모두 포함한 것) · `<more>…`: 낱말
- 예: `omni settings list --find "분할 금액"`
- 답: `settings`[] (setting, now, default, label, range, takes_effect)

### omni settings list-edit

관심종목·감시 종목·적립식 관심목록 편집 계획

- 성격: 계획(실계좌에 영향을 주면 승인 코드)
- 인자: `<which>` (필수): 목록 `watch` \| `universe` \| `dcalist` · `<op>` (필수): 추가·삭제 `add` \| `remove` · `<code>` (필수): 6자리 종목코드 · `--name <name>`: 표시 이름 · `--account <key>`: 계좌 키 · 기본 paper-shared · `--reason <reason>` (필수): 사용자가 말한 이유(기록에 남음)
- 예: `omni settings list-edit watch add 005930 --reason "관심종목에 더함"`

### omni settings plan

`주소=값`을 검사해 계획으로 저장(설정은 바꾸지 않음)

- 성격: 계획(실계좌에 영향을 주면 승인 코드)
- 인자: `<pairs>…` (필수): `주소=값` 여러 개 · `--reason <reason>` (필수): 변경 이유(이력에 남음)
- 예: `omni settings plan strategy.scale_max_adds=5 --reason "추가 매수 줄임"`

### omni settings plans

최근 계획

- 성격: 조회
- 인자: 없음
- 예: `omni settings plans`
- 답: `plans`[] (plan, status, level, affects, reason, how, title, created, expires, changes)

### omni settings preset

빠른 선택으로 계획 만들기

- 성격: 계획(실계좌에 영향을 주면 승인 코드)
- 인자: `<name>` (필수): 이름 `strength` \| `ipo` \| `volume` \| `oversold` · `<choice>` (필수): 선택 이름 · `--reason <reason>` (필수): 사용자가 말한 이유(기록에 남음)
- 예: `omni settings preset volume 엄격 --reason "거래량 확인 강화"`

### omni settings revert

이력 하나를 되돌리는 계획

- 성격: 계획(실계좌에 영향을 주면 승인 코드)
- 인자: `<change>` (필수): 이력 번호 · `--reason <reason>` (필수): 사용자가 말한 이유(기록에 남음)
- 예: `omni settings revert 203 --reason "되돌림"`

### omni settings show-plan

계획 보기

- 성격: 조회
- 인자: `<plan>` (필수): 계획 번호
- 예: `omni settings show-plan 1a2b3c4d`

## status

전체 상태(엔진·서비스·실주문 스위치·HALT)

### omni status alerts

주의 항목

- 성격: 조회
- 인자: 없음
- 예: `omni status alerts`
- 답: `alerts`[]

### omni status all

엔진·서비스·실주문 스위치·HALT

- 성격: 조회
- 인자: 없음
- 예: `omni status all`
- 답: `engines`(jev, vibe) · `live_accounts` · `halt` · `buy_paused` · `stop_files`[] · `services`

### omni status balance

계좌별·전체 잔고와 원금 대비 손익(실행기 기록)

- 성격: 조회
- 인자: 없음
- 예: `omni status balance`
- 답: `unit` · `balances`[]

### omni status realized

오늘 실현손익

- 성격: 조회
- 인자: 없음
- 예: `omni status realized`
- 답: `realized`[]

### omni status refresh

실계좌 잔고를 한 번 조회해 기록(외부 조회, 주문 없음)

- 성격: 외부 조회: NH · 씀: 실계좌 잔고 기록 한 줄
- 인자: 없음
- 예: `omni status refresh`

### omni status timeline

그날 판단·실행 타임라인

- 성격: 조회
- 인자: `--day <day>`: YYYY-MM-DD(기본 오늘) · `--limit <limit>`: 최대 개수 (1~500) · 기본 50
- 예: `omni status timeline`
- 답: `rows`[] (time) · `waits`

### omni status trades

그날 모든 계좌의 거래

- 성격: 조회
- 인자: `--day <day>`: YYYY-MM-DD(기본 오늘)
- 예: `omni status trades`
- 답: `trades`[] (account, time, side, symbol, qty, price, pnl)

## template

점검표

### omni template startup

창업 준비 점검표

- 성격: 조회
- 인자: `--blank`: 빈 점검표 · `--check <path>`: 검사할 .md
- 예: `omni template startup --blank`

## trade

매매 요청 계획(승인 코드 필요, 실행기·미러가 가드를 거쳐 처리)

### omni trade cancel

대기 중인 매매 요청 취소 계획(where: paper 또는 실계좌 키)

- 성격: **계획 · 승인 코드**
- 인자: `<where>` (필수): `paper` 또는 실계좌 키 · `<code>` (필수): 6자리 종목코드 · `--reason <reason>` (필수): 요청 이유(기록에 남음)
- 예: `omni trade cancel paper 069500 --reason "취소"`

### omni trade live-buy

실계좌 추가 매수 요청 계획(미러 보유 종목만, 외부 조회 1회)

- 성격: **계획 · 승인 코드** · 외부 조회: NH
- 인자: `<account>` (필수): 실계좌 키(`live-1` 등) · `<code>` (필수): 6자리 종목코드 · `--qty <qty>` (필수): 주식 수 (1~1,000,000) · `--reason <reason>` (필수): 요청 이유(기록에 남음)
- 예: `omni trade live-buy live-1 069500 --qty 2 --reason "추가 매수"`

### omni trade live-sell

실계좌 매도 요청 계획(미러가 산 수량만, 외부 조회 1회)

- 성격: **계획 · 승인 코드** · 외부 조회: NH
- 인자: `<account>` (필수): 실계좌 키(`live-1` 등) · `<code>` (필수): 6자리 종목코드 · `--qty <qty>`: 주식 수 (1~1,000,000) · `--all`: 전량(오늘 다시 사지 않음) · `--reason <reason>` (필수): 요청 이유(기록에 남음)
- 예: `omni trade live-sell live-1 069500 --all --reason "정리"`

### omni trade paper-buy

모의 보유 종목 추가 매수 요청 계획(실계좌 미러도 따라 매수)

- 성격: **계획 · 승인 코드**
- 인자: `<code>` (필수): 6자리 종목코드 · `--qty <qty>` (필수): 주식 수 (1~1,000,000) · `--reason <reason>` (필수): 요청 이유(기록에 남음)
- 예: `omni trade paper-buy 069500 --qty 2 --reason "추가 매수"`

### omni trade paper-sell

모의 보유 종목 매도 요청 계획(실계좌 미러도 따라 매도)

- 성격: **계획 · 승인 코드**
- 인자: `<code>` (필수): 6자리 종목코드 · `--qty <qty>`: 주식 수 (1~1,000,000) · `--all`: 전량 · `--reason <reason>` (필수): 요청 이유(기록에 남음)
- 예: `omni trade paper-sell 069500 --qty 2 --reason "비중 축소"`

## 기타

묶음 없는 명령

### omni engine

운영 엔진 변경 계획(두 파일이라 계획이 나뉠 수 있음)

- 성격: 계획(실계좌에 영향을 주면 승인 코드)
- 인자: `<mode>` (필수): 엔진 모드 `both` \| `jev` \| `vibe` \| `off` · `--reason <reason>` (필수): 사용자가 말한 이유(기록에 남음)
- 예: `omni engine jev --reason "장기투자 중지"`

### omni guide

모든 명령 보기와 검색

- 성격: 조회
- 인자: `<words>…`: 검색어
- 예: `omni guide`
- 답: `commands`[] (command, group, help, options) · `rules`[]

### omni query

nhplug 아래 DB를 읽기 전용 SELECT 한 문장으로 조회

- 성격: 조회
- 인자: `<database>` (필수): nhplug 아래 DB 이름 · `<sql>` (필수): SELECT 한 문장 · `--limit <limit>`: 최대 개수 (1~5,000) · 기본 200
- 예: `omni query shared-etf-paper/orders.sqlite3 "SELECT count(*) FROM fills"`
- 답: `rows`[]

### omni repl

명령을 이어서 입력하는 대화형 모드(exit로 종료)

- 성격: 조회
- 인자: 없음
- 예: `omni repl`

## 설정

주소는 `묶음.키`입니다. 지금 값은 `omni settings get <주소>`, 실계좌 영향은 `omni settings explain <주소>`로 봅니다. `<실계좌>`·`<장부>` 자리에는 실제 키를 씁니다. 범위는 저장할 때 다시 검사하며, 다른 값에 따라 더 좁을 수 있습니다(예: 운용금은 계좌 잔고까지).

### 설정 strategy

매매 기준(Jev 공유 모의 실행기). 실주문이 켜진 Jev 실계좌가 있으면 승인 코드가 필요합니다.

| 주소 | 이름 | 값 | 기본 | 반영 |
| --- | --- | --- | --- | --- |
| `strategy.entry_confidence` | Jev 매수 신뢰도 | 0.3~0.95 | `0.5` | 다음 회차 |
| `strategy.sell_confidence` | Jev 매도 신뢰도 | 0.3~0.95 | `0.7` | 다음 회차 |
| `strategy.take_profit_half_pct` | 방어형 1차 익절 % (데이터 목표 없을 때·절반) | 0.2~10.0 | `1.0` | 다음 회차 |
| `strategy.take_profit_all_pct` | 방어형 2차 익절 % (데이터 목표 없을 때·나머지) | 0.3~20.0 | `2.0` | 다음 회차 |
| `strategy.stop_loss_pct` | 손절 % | 0.5~10.0 | `2.0` | 다음 회차 |
| `strategy.add_step_pct` | 추가 매수 간격 % | 0.2~5.0 | `0.7` | 다음 회차 |
| `strategy.trend_half_pct` | 추세형 1차 익절 % | 0.5~15.0 | `2.0` | 다음 회차 |
| `strategy.trend_trail_pct` | 추세형 고점 대비 추적 손절 % | 0.5~10.0 | `1.5` | 다음 회차 |
| `strategy.overnight_max_manwon` | 장 마감 보유 한도 (만원) | 0~500000 | `300` | 다음 회차 |
| `strategy.swing_max_days` | 스윙 최대 보유일 (거래일) | 1~10 | `5` | 다음 회차 |
| `strategy.strength_top_n` | 관심종목 강도 진입: Jev 순위 상위 N위 (0=끔) | 0~10 | `3` | 다음 회차 |
| `strategy.strength_min_prob` | 관심종목 강도 진입: Jev 확률 최소 | 0.05~0.6 | `0.15` | 다음 회차 |
| `strategy.strength_change_min` | 관심종목 강도 진입: 전일 대비 최소 상승 % | 0.0~10.0 | `2.0` | 다음 회차 |
| `strategy.strength_change_max` | 관심종목 강도 진입: 전일 대비 최대 상승 % | 5.0~29.0 | `25.0` | 다음 회차 |
| `strategy.ipo_days` | 신규주: 상장 후 거래일 수 (0=끔) | 0~5 | `2` | 다음 회차 |
| `strategy.ipo_size_pct` | 신규주: 분할 1회 매수 금액 대비 % | 10~100 | `50` | 다음 회차 |
| `strategy.ipo_near_high_pct` | 신규주: 당일 고가 대비 허용 % 이내 | 0.5~15.0 | `3.0` | 다음 회차 |
| `strategy.ipo_stop_pct` | 신규주: 손절 % | 1.0~10.0 | `3.0` | 다음 회차 |
| `strategy.ipo_half_pct` | 신규주: 절반 익절 % | 1.0~30.0 | `3.0` | 다음 회차 |
| `strategy.ipo_trail_pct` | 신규주: 고점 대비 추적 손절 % | 0.5~15.0 | `2.0` | 다음 회차 |
| `strategy.weak_half_pct` | 약세장: 절반 익절 % (늦어도) | 0.3~5.0 | `1.0` | 다음 회차 |
| `strategy.weak_all_pct` | 약세장: 전량 익절 % (늦어도) | 0.5~10.0 | `2.0` | 다음 회차 |
| `strategy.fade_exit_pct` | 하락·횡보장 강도 종목: 매수세가 꺾이면 전량 매도하는 수익 % (0=끔) | 0.0~5.0 | `0.8` | 다음 회차 |
| `strategy.entry_volume_ratio` | 진입 거래량 배수 (최근 5분 ÷ 그 전 5분, 0=확인 안 함) | 0.0~3.0 | `1.2` | 다음 회차 |
| `strategy.direction_switch` | 방향 전환 (1=켬, 0=끔) | `0.0` \| `1.0` | `1` | 다음 회차 |
| `strategy.reentry_guard` | 같은 날 재진입 제한 | `0.0` \| `1.0` | `1` | 다음 회차 |
| `strategy.reentry_allow` | 같은 날 재진입 허용 | `0.0` \| `1.0` | `1` | 다음 회차 |
| `strategy.careful_reentry_on` | 신중 재진입 (불리한 국면의 같은 날 재진입) | `0.0` \| `1.0` | `1` | 다음 회차 |
| `strategy.careful_reentry_vwap_pct` | 신중 재진입: VWAP 대비 최대 % | 0.5~10.0 | `2.0` | 다음 회차 |
| `strategy.careful_reentry_range_pct` | 신중 재진입: 오늘 범위 위치 상한 % | 50~100 | `85` | 다음 회차 |
| `strategy.careful_reentry_conf` | 신중 재진입: Jev 재검토 매수 신뢰도 | 0.3~0.95 | `0.6` | 다음 회차 |
| `strategy.careful_reentry_size_pct` | 신중 재진입: 매수 크기 (평소 대비 %) | 10~100 | `50` | 다음 회차 |
| `strategy.losing_streak_guard` | 연속 손실 쉬기 | `0.0` \| `1.0` | `1` | 다음 회차 |
| `strategy.averaging_down_guard` | 약세장 물타기 금지 | `0.0` \| `1.0` | `1` | 다음 회차 |
| `strategy.opposite_guard` | 반대 방향 동시 보유 차단 | `0.0` \| `1.0` | `1` | 다음 회차 |
| `strategy.direction_confirm` | 지수 방향 확인 (Jev 경로) | `0.0` \| `1.0` | `1` | 다음 회차 |
| `strategy.rising_check` | 최근 분봉 상승 확인 | `0.0` \| `1.0` | `1` | 다음 회차 |
| `strategy.ipo_add_guard` | 신규주 추가 매수 금지 | `0.0` \| `1.0` | `1` | 다음 회차 |
| `strategy.ipo_max_positions` | 신규주 동시 보유 종목 수 | 1~3 | `1` | 다음 회차 |
| `strategy.ipo_slot_on` | 신규주 독립 슬롯 (Jev 선택·인버스 보유와 별도) | `0.0` \| `1.0` | `1` | 다음 회차 |
| `strategy.ipo_daily_max` | 신규주 하루 매수 횟수 | 1~10 | `2` | 다음 회차 |
| `strategy.ipo_time_stop_min` | 신규주 시간 손절 분 (0=끔) | 0~390 | `60` | 다음 회차 |
| `strategy.strength_slot_on` | 관심종목 강도 독립 슬롯 (Jev 선택과 별도) | `0.0` \| `1.0` | `0` | 다음 회차 |
| `strategy.strength_slot_max` | 강도 슬롯 동시 보유 종목 수 | 1~5 | `2` | 다음 회차 |
| `strategy.strength_slot_daily` | 강도 슬롯 하루 매수 횟수 | 1~10 | `3` | 다음 회차 |
| `strategy.scale_signal_add_on` | 새 매수 판단으로 보유 종목 추가 매수 | `0.0` \| `1.0` | `1` | 다음 회차 |
| `strategy.scale_step_add_on` | 가격 단계 도달 시 보유 종목 추가 매수 | `0.0` \| `1.0` | `1` | 다음 회차 |
| `strategy.scale_profit_rebuy_on` | 일부 익절 후 재매수 | `0.0` \| `1.0` | `1` | 다음 회차 |
| `strategy.scale_interval_min` | 가격 단계 추가 매수 최소 간격(분) | 1~60 | `5` | 다음 회차 |
| `strategy.scale_max_adds` | 종목당 추가 매수 최대 횟수(첫 매수 제외) | 0~100 | `2` | 다음 회차 |
| `strategy.size_holding_on` | 매수 비중: 보유가 종목당 한도 절반을 넘으면 줄이기 (1=켬) | `0.0` \| `1.0` | `1` | 다음 회차 |
| `strategy.size_market_on` | 매수 비중: 시황 방향 배율 (같은 방향은 100%, 1=켬) | `0.0` \| `1.0` | `1` | 다음 회차 |
| `strategy.size_market_flat_pct` | 매수 비중: 횡보장 크기 % | 25~100 | `75` | 다음 회차 |
| `strategy.size_market_against_pct` | 매수 비중: 반대 방향 크기 % (하락장 일반 매수·상승장 인버스) | 25~100 | `50` | 다음 회차 |
| `strategy.regime_trim_on` | 시황 역전 시 보유 절반 매도 (포지션당 1번, 1=켬) | `0.0` \| `1.0` | `1` | 다음 회차 |
| `strategy.add_conf_sizing_on` | 추가 매수는 직전보다 나은 조건일 때만 (1=켬) | `0.0` \| `1.0` | `1` | 다음 회차 |
| `strategy.add_size_pct` | 추가 매수 크기 (새 판단·가격 단계, 분할 1회 대비 %) | 10~100 | `50` | 다음 회차 |
| `strategy.partial_sell_conf` | 엔진 일부 매도 시작 신뢰도 | 0.5~0.95 | `0.55` | 다음 회차 |
| `strategy.partial_sell_pct` | 엔진 일부 매도 비율 % | 10~90 | `33` | 다음 회차 |
| `strategy.oversold_on` | 낙폭과대 반등 매매 | `0.0` \| `1.0` | `1` | 다음 회차 |
| `strategy.oversold_stock_drop_pct` | 낙폭과대: 개별주 낙폭 % (전일 대비 저가) | 1.0~30.0 | `6.0` | 다음 회차 |
| `strategy.oversold_etf_drop_pct` | 낙폭과대: ETF 낙폭 % | 0.5~15.0 | `6.0` | 다음 회차 |
| `strategy.oversold_lev_on` | 낙폭과대: 2배 ETF 포함 | `0.0` \| `1.0` | `0` | 다음 회차 |
| `strategy.oversold_lev_drop_pct` | 낙폭과대: 2배 ETF 낙폭 % | 1.0~30.0 | `8.0` | 다음 회차 |
| `strategy.oversold_atr_mult` | 낙폭과대: 평소 변동폭(ATR) 배수 | 0.0~5.0 | `1.5` | 다음 회차 |
| `strategy.oversold_calm_min` | 낙폭과대: 저가 갱신 없는 분 | 1~25 | `20` | 다음 회차 |
| `strategy.oversold_rebound_pct` | 낙폭과대: 저가 대비 반등 % | 0.0~5.0 | `0.3` | 다음 회차 |
| `strategy.oversold_size_pct` | 낙폭과대: 매수 크기 (분할 1회 대비 %) | 50~1000 | `100` | 다음 회차 |
| `strategy.oversold_tp_pct` | 낙폭과대: 익절 % | 0.3~15.0 | `1.5` | 다음 회차 |
| `strategy.oversold_recover_pct` | 낙폭과대: 익절 (낙폭 회복 %, 둘 중 낮은 값) | 20~100 | `100` | 다음 회차 |
| `strategy.oversold_stop_pct` | 낙폭과대: 손절 (당일 저가 아래 %) | 0.1~5.0 | `0.5` | 다음 회차 |
| `strategy.oversold_market_drop_pct` | 낙폭과대: 시장 저가 하락 % 이상일 때만 (0=조건 없음) | 0.0~15.0 | `3.0` | 다음 회차 |
| `strategy.oversold_hold_days` | 낙폭과대: 최대 보유 거래일 (0=당일) | 0~3 | `1` | 다음 회차 |
| `strategy.oversold_jev_min` | 낙폭과대: Jev 매수 신뢰도 | 0.0~0.95 | `0.5` | 다음 회차 |
| `strategy.oversold_vibe_min` | 낙폭과대: vibe 점수 | -1.0~1.0 | `0.2` | 다음 회차 |
| `strategy.oversold_v2_on` | 낙폭과대 선별 v2 매매 | `0.0` \| `1.0` | `1` | 다음 회차 |
| `strategy.oversold_v2_from_hhmm` | 선별 v2: 매수 시작 시각 | 900~1510 | `1430` | 다음 회차 |
| `strategy.oversold_v2_until_hhmm` | 선별 v2: 매수 마감 시각 | 930~1515 | `1455` | 다음 회차 |
| `strategy.oversold_v2_index_drop_pct` | 선별 v2: 지수 폭락 % (저가) | 3.0~20.0 | `8.0` | 다음 회차 |
| `strategy.oversold_v2_capit_drop_pct` | 선별 v2: 투매 지수 낙폭 % | 2.0~15.0 | `5.0` | 다음 회차 |
| `strategy.oversold_v2_prior5_pct` | 선별 v2: 투매 직전 5일 지수 하락 % | 0.0~20.0 | `5.0` | 다음 회차 |
| `strategy.oversold_v2_rel_max_pct` | 선별 v2: 시장보다 더 빠진 한도 %p | 0.5~15.0 | `4.0` | 다음 회차 |
| `strategy.oversold_v2_add_on` | 선별 v2: 다음 날 2차 매수 (1=켬) | 0~1 | `1` | 다음 회차 |
| `strategy.oversold_v2_half_tp_pct` | 선별 v2: 절반 익절 % | 2.0~30.0 | `8.0` | 다음 회차 |
| `strategy.oversold_v2_tp_pct` | 선별 v2: 나머지 익절 % | 3.0~45.0 | `15.0` | 다음 회차 |
| `strategy.oversold_v2_hold_days` | 선별 v2: 보유 거래일 | 1~10 | `3` | 다음 회차 |
| `strategy.oversold_v2_stop_pct` | 선별 v2: 손절 (평균 매입가 대비 %) | 1.0~25.0 | `2.0` | 다음 회차 |
| `strategy.oversold_v2_jev_sell_on` | 선별 v2: 수익 중 Jev 매도 판단으로 팔기 (1=켬) | `0.0` \| `1.0` | `0` | 다음 회차 |
| `strategy.oversold_v2_trail_at_pct` | 선별 v2: 고점 되밀림 매도 시작 % (평균가 대비, 0=끔) | 0.0~30.0 | `0.0` | 다음 회차 |
| `strategy.oversold_v2_trail_pct` | 선별 v2: 고점에서 되밀림 % | 0.5~15.0 | `2.0` | 다음 회차 |
| `strategy.oversold_v2_reentry_max` | 선별 v2: 손절 뒤 저점 재매수 횟수 (0=끔) | 0~3 | `2` | 다음 회차 |
| `strategy.oversold_v2_reentry_hold_min` | 선별 v2: 재매수 전 저점 유지 분 | 5~60 | `20` | 다음 회차 |
| `strategy.oversold_v2_reentry_bounce_pct` | 선별 v2: 재매수 저점 반등 % | 0.2~3.0 | `1.0` | 다음 회차 |
| `strategy.oversold_v2_size_pct` | 선별 v2: 매수 크기 (1·2차 합계, 분할 1회 대비 %) | 10~1000 | `100` | 다음 회차 |
| `strategy.oversold_v2_veto_conf` | 선별 v2: 리서치 거부 (Jev WAIT 신뢰도) | 0.5~1.0 | `0.7` | 다음 회차 |
| `strategy.oversold_v2_vibe_veto` | 선별 v2: 리서치 거부 (vibe 점수 이하) | -1.0~0.0 | `-0.3` | 다음 회차 |
| `strategy.oversold_v2_boost_pct` | 선별 v2: 리서치 강한 동의 시 크기 % | 100~200 | `100` | 다음 회차 |
| `strategy.oversold_v2_max_new` | 선별 v2: 하루 매수 종목 수 | 1~6 | `3` | 다음 회차 |
| `strategy.entry_cutoff_hhmm` | 신규 매수 마감 시각 | 1000~1500 | `1430` | 다음 회차 |
| `strategy.flatten_hhmm` | 장 마감 정리 시각 | 1400~1505 | `1440` | 다음 회차 |

### 설정 accounts

계좌별 자동매매·금액·한도. 모의는 `paper-shared`, 실계좌는 `live-1` 같은 키입니다.

| 주소 | 이름 | 값 | 기본 | 반영 |
| --- | --- | --- | --- | --- |
| `accounts.paper-shared.auto` | 자동매매 | `true` \| `false` | `false` | 다음 회차 |
| `accounts.paper-shared.principal_krw` | 원금(화면 손익 기준) | 1~1000000000000 | — | 바로 |
| `accounts.paper-shared.budget.first_buy_krw` | 분할 1회 매수 금액(원) | 100000~100000000 | `1000000` | 다음 회차 |
| `accounts.paper-shared.budget.per_symbol_krw` | 종목당 최대 매수 금액(원) | 100000~5000000000 | `3000000` | 다음 회차 |
| `accounts.paper-shared.limits.order_max_krw` | 회당 매수 한도(원) | 100000~100000000 | `3000000` | 다음 회차 |
| `accounts.paper-shared.limits.daily_buy_krw` | 하루 매수 한도(원) | 1000000~1000000000 | `6000000` | 다음 회차 |
| `accounts.paper-shared.limits.held_max_krw` | 보유 금액 한도(원) | 1000000~5000000000 | `21000000` | 다음 회차 |
| `accounts.paper-shared.limits.daily_new_symbols` | 하루 새 종목 수 | 1~50 | `5` | 다음 회차 |
| `accounts.paper-shared.limits.daily_loss_krw` | 하루 손실 한도(원) | 10000~100000000 | `300000` | 다음 회차 |
| `accounts.<실계좌>.auto` | 자동매매 | `true` \| `false` | `false` | 다음 회차 |
| `accounts.<실계좌>.principal_krw` | 원금(화면 손익 기준) | 1~1000000000000 | — | 바로 |
| `accounts.<실계좌>.engine` | 운용 방식 | `jev` \| `longterm` | `"jev"` | 다음 회차 |
| `accounts.<실계좌>.budget.capital_krw` | 운용금(원) | 10000~1000000000 | `1000000` | 다음 회차 |
| `accounts.<실계좌>.budget.order_price_max_krw` | 1주 가격 상한(원) | 1000~2000000 | `10000` | 다음 회차 |
| `accounts.<실계좌>.budget.daily_loss_krw` | 하루 손실 한도(원) | 1000~30000 | `20000` | 다음 회차 |
| `accounts.<실계좌>.limits.order_max_krw` | 회당 매수 한도(원) | 10000~1000000000 | `1000000` | 다음 회차 |
| `accounts.<실계좌>.limits.daily_new_symbols` | 하루 새 종목 수 | 1~5 | `5` | 다음 회차 |
| `accounts.<실계좌>.rules.take_profit_pct` | 익절 %(모의를 따르는 미러 계좌에서는 쓰지 않음) | 0.5~10.0 | `2.0` | 다음 회차 |
| `accounts.<실계좌>.rules.stop_loss_pct` | 손절 % | 0.5~2.0 | `2.0` | 다음 회차 |
| `accounts.<실계좌>.rules.sell_confidence` | 매도 신뢰도 기준(모의를 따르는 미러 계좌에서는 쓰지 않음) | 0.5~0.95 | `0.7` | 다음 회차 |
| `accounts.<실계좌>.rules.flatten` | 장 마감 정리(모의를 따르는 미러 계좌에서는 쓰지 않음) | `true` \| `false` | `true` | 다음 회차 |
| `accounts.<실계좌>.rules.regime_exits` | 국면별 청산(모의를 따르는 미러 계좌에서는 쓰지 않음) | `true` \| `false` | `true` | 다음 회차 |
| `accounts.<실계좌>.rules.overnight` | 이월(모의를 따르는 미러 계좌에서는 쓰지 않음) | `true` \| `false` | `true` | 다음 회차 |
| `accounts.<실계좌>.rules.follow_paper` | 모의 체결 따라가기 | `true` \| `false` | `true` | 다음 회차 |
| `accounts.<실계좌>.rules.sizing` | 매수 크기 기준(1주씩·금액 비율) | `share` \| `amount` | `"share"` | 새 모의 체결부터 |
| `accounts.<실계좌>.rules.tranche_krw` | 한 번에 따라 살 최대 금액(원) | 10000~1000000000 | `100000` | 새 모의 체결부터 |
| `accounts.<실계좌>.rules.max_order_qty` | 1회 매수 최대 수량(주) | 1~1000 | `100` | 다음 회차 |
| `accounts.<실계좌>.rules.chase_pct` | 가격 추격 한도 % | 0.1~5.0 | `1.0` | 다음 회차 |
| `accounts.<실계좌>.rules.evidence_chase` | 추격 한도를 넘어도 근거가 있으면 매수 | `true` \| `false` | `true` | 다음 회차 |
| `accounts.<실계좌>.rules.max_adds` | 추가 매수 최대 횟수 | 0~100 | `100` | 새 모의 체결부터 |
| `accounts.<실계좌>.rules.follow_pct` | 모의 대비 금액 비율 %(비우면 자동) | 1.0~100.0 | — | 새 모의 체결부터 |

### 설정 lt

장기 엔진(가상 계좌)의 운용 방식·거장 모듈·비중·수치.

| 주소 | 이름 | 값 | 기본 | 반영 |
| --- | --- | --- | --- | --- |
| `lt.enabled` | 장기 엔진 운영 | `true` \| `false` | `true` | 다음 확인 |
| `lt.mode` | 운용 모드 | `manual` \| `recommend` \| `auto` | `"manual"` | 다음 08:45 |
| `lt.auto_live` | 자동 조합을 실제 적용 | `true` \| `false` | `false` | 다음 08:45 |
| `lt.execute` | 가상 체결 | `true` \| `false` | `false` | 다음 09:40 |
| `lt.weights.NDX` | KODEX 미국나스닥100 | 0.0~0.6 | `0.3` | 다음 08:45 |
| `lt.weights.SPX` | KODEX 미국S&P500 | 0.0~0.6 | `0.3` | 다음 08:45 |
| `lt.weights.SEMI` | TIGER 미국필라델피아반도체나스닥 | 0.0~0.6 | `0.1` | 다음 08:45 |
| `lt.weights.QUANT` | SOL 미국양자컴퓨팅TOP10 | 0.0~0.6 | `0.08` | 다음 08:45 |
| `lt.weights.SPACE` | TIGER 미국우주테크 | 0.0~0.6 | `0.07` | 다음 08:45 |
| `lt.weights.RES` | TIGER KOFR금리액티브(합성) | 0.0~0.6 | `0.15` | 다음 08:45 |
| `lt.tuning.band` | 리밸런싱 밴드(목표 대비 ±) | 0.03~0.1 | `0.05` | 다음 08:45 |
| `lt.tuning.confirm_days` | 국면 확정 일수(투매 제외) | 5~20 | `10` | 다음 08:45 |
| `lt.tuning.monthly_sell_share` | 저수지 뺀 월 매도 상한(순자산 대비) | 0.05~0.2 | `0.1` | 다음 08:45 |
| `lt.tuning.daily_buy_share` | 하루 매수 상한(순자산 대비) | 0.1~0.5 | `0.25` | 다음 08:45 |
| `lt.tuning.deploy_weeks` | 초기 투입 주 수(0이면 하루 상한) | 0~26 | `0` | 다음 08:45 |
| `lt.modules.bogle_core` | 기준 비중 · 코어·위성 유지 | `true` \| `false` | `true` | 다음 08:45 |
| `lt.modules.dalio_rebalance` | 레이 달리오 · 월 밴드 리밸런싱 | `true` \| `false` | `true` | 다음 08:45 |
| `lt.modules.yeo_reservoir` | 저수지 분할(일반) · 하락 단계 투입 | `true` \| `false` | `false` | 다음 08:45 |
| `lt.modules.marks_pendulum` | 하워드 막스 · 시계추 | `true` \| `false` | `false` | 다음 08:45 |
| `lt.modules.williams_trend` | 래리 윌리엄스 · 위성 추세 점검 | `true` \| `false` | `false` | 다음 08:45 |
| `lt.modules.williams_rebound` | 래리 윌리엄스 · 급락 단기 반등 | `true` \| `false` | `false` | 다음 08:45 |
| `lt.modules.lynch_story` | 피터 린치 · 테마 스토리 점검 | `true` \| `false` | `true` | 다음 08:45 |
| `lt.modules.buffett_lowturn` | 워런 버핏·필립 피셔 · 저회전·손절 없음 | `true` \| `false` | `true` | 다음 08:45 |
| `lt.params.lynch_story.watch` | 주의일 때 남기는 비율 | 0.0~1.0 | `0.5` | 다음 08:45 |
| `lt.params.lynch_story.tilt` | 기울기 한 단계 크기 | 0.0~0.5 | `0.25` | 다음 08:45 |
| `lt.params.williams_trend.days` | 200일선 −2% 아래 머문 거래일 | 2~20 | `5` | 다음 08:45 |
| `lt.params.williams_trend.recover` | 되돌림: 200일선 위 회복 폭 | 0.0~0.1 | `0.02` | 다음 08:45 |
| `lt.params.williams_trend.cut` | 줄일 때 남기는 비율 | 0.0~1.0 | `0.5` | 다음 08:45 |
| `lt.params.marks_pendulum.trim` | 과열 때 코어에서 저수지로 | 0.0~0.15 | `0.05` | 다음 08:45 |
| `lt.params.marks_pendulum.panic_1` | 투매 때 저수지 1단계 낙폭 | -0.4~-0.05 | `-0.15` | 다음 08:45 |
| `lt.params.marks_pendulum.panic_2` | 투매 때 저수지 2단계 낙폭 | -0.5~-0.1 | `-0.25` | 다음 08:45 |
| `lt.params.yeo_reservoir.step_1` | 저수지 1단계 낙폭 | -0.4~-0.05 | `-0.2` | 다음 08:45 |
| `lt.params.yeo_reservoir.step_2` | 저수지 2단계 낙폭 | -0.6~-0.1 | `-0.3` | 다음 08:45 |
| `lt.params.yeo_reservoir.per_step` | 단계마다 코어로 옮길 몫 | 0.0~0.15 | `0.075` | 다음 08:45 |
| `lt.params.williams_rebound.drop` | 나스닥100 하루 하락 | -0.1~-0.01 | `-0.03` | 다음 08:45 |
| `lt.params.williams_rebound.vix` | VIX 이상 | 15~50 | `25` | 다음 08:45 |
| `lt.params.williams_rebound.move` | 나스닥100으로 옮길 몫 | 0.0~0.15 | `0.05` | 다음 08:45 |
| `lt.params.williams_rebound.hold` | 보유 거래일 | 2~30 | `10` | 다음 08:45 |

### 설정 book

장부별 전략(가상 계좌 `actual`, 장기투자 실계좌 키).

| 주소 | 이름 | 값 | 기본 | 반영 |
| --- | --- | --- | --- | --- |
| `book.<장부>.strategy` | 전략 | `engine` \| `custom` \| `yeo_method` | `"engine"` | 다음 계획 |
| `book.<장부>.reservoir` | 저수지 ETF | `auto` \| `449170` \| `357870` \| `272580` | `"auto"` | 다음 계획 |
| `book.<장부>.modules.bogle_core` | 기준 비중 | `true` \| `false` | — | 다음 계획 |
| `book.<장부>.modules.dalio_rebalance` | 레이 달리오 | `true` \| `false` | — | 다음 계획 |
| `book.<장부>.modules.yeo_reservoir` | 저수지 분할(일반) | `true` \| `false` | — | 다음 계획 |
| `book.<장부>.modules.marks_pendulum` | 하워드 막스 | `true` \| `false` | — | 다음 계획 |
| `book.<장부>.modules.williams_trend` | 래리 윌리엄스 | `true` \| `false` | — | 다음 계획 |
| `book.<장부>.modules.williams_rebound` | 래리 윌리엄스 | `true` \| `false` | — | 다음 계획 |
| `book.<장부>.modules.lynch_story` | 피터 린치 | `true` \| `false` | — | 다음 계획 |
| `book.<장부>.modules.buffett_lowturn` | 워런 버핏·필립 피셔 | `true` \| `false` | — | 다음 계획 |
| `book.<장부>.yeo.market` | 여운봉: 시장 | `us` \| `kospi` \| `mix` | `"us"` | 다음 계획 |
| `book.<장부>.yeo.us_index` | 여운봉: 미국 지수 | `ndx` \| `spx` \| `both` | `"ndx"` | 다음 계획 |
| `book.<장부>.yeo.mix_us` | 여운봉: 미국·한국을 섞을 때 미국 몫 | 0.0~1.0 | `0.7` | 다음 계획 |
| `book.<장부>.yeo.mix_ndx` | 여운봉: 나스닥100·S&P500을 섞을 때 나스닥100 몫 | 0.0~1.0 | `0.5` | 다음 계획 |
| `book.<장부>.yeo.risk` | 여운봉: 위험자산 비중 | 0.5~0.9 | `0.7` | 다음 계획 |
| `book.<장부>.yeo.band` | 여운봉: 리밸런싱 이탈 폭 | 0.02~0.1 | `0.05` | 다음 계획 |
| `book.<장부>.yeo.steps` | 여운봉: 미국 하락 단계와 투입 비율 | JSON | `[[-0.2, 0.4], [-0.3, 0.4], [-0.4, 0.2]]` | 다음 계획 |
| `book.<장부>.yeo.kospi_steps` | 여운봉: 코스피 하락 단계와 투입 비율 | JSON | `[[-0.3, 0.3], [-0.45, 0.4], [-0.55, 0.3]]` | 다음 계획 |
| `book.<장부>.yeo.park` | 여운봉: 대기 현금을 저수지 ETF로 | `true` \| `false` | `false` | 다음 계획 |

### 설정 dca

장부별 적립식.

| 주소 | 이름 | 값 | 기본 | 반영 |
| --- | --- | --- | --- | --- |
| `dca.<장부>.on` | 적립식: 적립식 켜기 | `true` \| `false` | `false` | 다음 확인 |
| `dca.<장부>.profile` | 적립식: 거장 | `dalio` \| `bogle_buffett` \| `marks` \| `yeo` \| `lynch` \| `williams` | `"dalio"` | 다음 확인 |
| `dca.<장부>.when_by` | 적립식: 언제 정하기 | `master` \| `me` | `"me"` | 다음 확인 |
| `dca.<장부>.size_by` | 적립식: 얼마나 정하기 | `master` \| `me` | `"me"` | 다음 확인 |
| `dca.<장부>.me_when` | 적립식: 정한 날 방식 | `cycle` \| `dip` | `"cycle"` | 다음 확인 |
| `dca.<장부>.buy_every` | 적립식: 매수 빈도 | `day` \| `week` \| `month` | `"week"` | 다음 확인 |
| `dca.<장부>.amount_krw` | 적립식: 1회 적립 금액 | 10000~1000000000 | `1000000` | 다음 확인 |
| `dca.<장부>.frequency` | 적립식: 주기 | `monthly` \| `weekly` \| `daily` \| `deposit` | `"monthly"` | 다음 확인 |
| `dca.<장부>.day` | 적립식: 매월 며칠 | 1~28 | `1` | 다음 확인 |
| `dca.<장부>.weekday` | 적립식: 요일 | 0~4 | `0` | 다음 확인 |
| `dca.<장부>.time` | 적립식: 시각 | 09:40~14:20 | `"10:00"` | 다음 확인 |
| `dca.<장부>.pick_on` | 적립식: 적립 종목 선택 | `true` \| `false` | `false` | 다음 확인 |
| `dca.<장부>.picks` | 적립식: 적립할 종목 | JSON | `[]` | 다음 확인 |
| `dca.<장부>.max_symbols_on` | 적립식: 종목 수 제한 | `true` \| `false` | `false` | 다음 확인 |
| `dca.<장부>.max_symbols` | 적립식: 종목 수 제한 | 1~26 | `3` | 다음 확인 |
| `dca.<장부>.max_per_symbol_on` | 적립식: 종목당 주식 수 제한 | `true` \| `false` | `false` | 다음 확인 |
| `dca.<장부>.max_per_symbol` | 적립식: 종목당 주식 수 제한 | 1~100000 | `100` | 다음 확인 |
| `dca.<장부>.max_total_on` | 적립식: 전체 주식 수 제한 | `true` \| `false` | `false` | 다음 확인 |
| `dca.<장부>.max_total` | 적립식: 전체 주식 수 제한 | 1~1000000 | `500` | 다음 확인 |
| `dca.<장부>.size` | 적립식: 적립 크기 | `amount` \| `shares` | `"amount"` | 다음 확인 |
| `dca.<장부>.shares_n` | 적립식: 종목별 주식 수 | 1~1000 | `1` | 다음 확인 |
| `dca.<장부>.time_mode` | 적립식: 매수 시각 | `fixed` \| `auto` | `"fixed"` | 다음 확인 |
| `dca.<장부>.deposit_pct` | 적립식: 입금 중 적립식 비율 | 10~100 | `100` | 다음 확인 |
| `dca.<장부>.spread_weeks` | 적립식: 입금 몫 나눠 사는 기간 | 1~26 | `4` | 다음 확인 |
| `dca.<장부>.alert` | 적립식: 꺼져도 적립 알림 | `true` \| `false` | `false` | 다음 확인 |
| `dca.<장부>.style` | 적립식: 투자 방식 | `master` \| `manual` | `"master"` | 다음 확인 |
| `dca.<장부>.tune.rate` | 거장 값 조정 rate | 0.001~0.01 | — | 다음 확인 |
| `dca.<장부>.tune.pace` | 거장 값 조정 pace | 0.3~1.0 | — | 다음 확인 |
| `dca.<장부>.tune.mult.bull` | 거장 값 조정 mult.bull | 0.5~1.5 | — | 다음 확인 |
| `dca.<장부>.tune.mult.recovery` | 거장 값 조정 mult.recovery | 0.5~1.5 | — | 다음 확인 |
| `dca.<장부>.tune.mult.correction` | 거장 값 조정 mult.correction | 0.5~1.5 | — | 다음 확인 |
| `dca.<장부>.tune.mult.bear` | 거장 값 조정 mult.bear | 0.5~1.5 | — | 다음 확인 |
| `dca.<장부>.tune.mult.panic` | 거장 값 조정 mult.panic | 0.5~1.5 | — | 다음 확인 |
| `dca.<장부>.tune.mult.overheat` | 거장 값 조정 mult.overheat | 0.5~1.5 | — | 다음 확인 |
| `dca.<장부>.tune.wait` | 거장 값 조정 wait | 1~10 | — | 다음 확인 |
| `dca.<장부>.tune.floor` | 거장 값 조정 floor | 0.05~0.5 | — | 다음 확인 |

### 설정 tunables

운영 조정값.

| 주소 | 이름 | 값 | 기본 | 반영 |
| --- | --- | --- | --- | --- |
| `tunables.session_open` | 실행기 장 시작 | 09:00~10:00 | `"09:00"` | 공유 모의 실행기와 학습 관찰기를 다시 시작해야 반영됩니다 |
| `tunables.last_order` | 마지막 주문 상한 | 15:00~15:18 | `"15:18"` | 공유 모의 실행기와 학습 관찰기를 다시 시작해야 반영됩니다 |
| `tunables.flatten_lead_min` | 정리 뒤 더 도는 분 | 10~40 | `20` | 공유 모의 실행기와 학습 관찰기를 다시 시작해야 반영됩니다 |
| `tunables.paper_max_order_qty` | 1회 주문 최대 수량(주) | 0~1000000 | `100` | 공유 모의 실행기를 STOP 절차로 다시 시작해야 반영됩니다 |
| `tunables.paper_max_total_qty` | 총 보유 최대 수량(주) | 0~1000000 | `1000` | 공유 모의 실행기를 STOP 절차로 다시 시작해야 반영됩니다 |
| `tunables.read_failure_limit` | 조회 실패 알림 횟수 | 1~20 | `5` | 공유 모의 실행기를 STOP 절차로 다시 시작해야 반영됩니다 |
| `tunables.losing_streak` | 연속 손실 쉬기: 횟수 | 2~10 | `3` | 공유 모의 실행기를 STOP 절차로 다시 시작해야 반영됩니다 |
| `tunables.losing_cooldown_min` | 연속 손실 쉬기: 분 | 5~240 | `30` | 공유 모의 실행기를 STOP 절차로 다시 시작해야 반영됩니다 |
| `tunables.ipo_start` | 신규주 보기 시작 | 09:10~14:00 | `"09:20"` | 공유 모의 실행기를 STOP 절차로 다시 시작해야 반영됩니다 |
| `tunables.ipo_max_scan` | 신규주 시세 조회 수 | 1~10 | `3` | 공유 모의 실행기를 STOP 절차로 다시 시작해야 반영됩니다 |
| `tunables.vibe_interval_min` | 판단 주기(분) | 5~60 | `15` | vibe 판단기·실행기를 다시 시작해야 반영됩니다 |
| `tunables.vibe_buy_score` | 매수 점수 | 0.1~1.0 | `0.5` | vibe 판단기·실행기를 다시 시작해야 반영됩니다 |
| `tunables.vibe_sell_score` | 매도 점수 | -1.0~0.0 | `-0.3` | vibe 판단기·실행기를 다시 시작해야 반영됩니다 |
| `tunables.vibe_full_score_krw` | 점수 1.0 매수 금액(원) | 100000~100000000 | `3000000` | vibe 판단기·실행기를 다시 시작해야 반영됩니다 |
| `tunables.vibe_strength_score` | 강도 경로 최소 점수 | 0.0~1.0 | `0.2` | vibe 판단기·실행기를 다시 시작해야 반영됩니다 |
| `tunables.vibe_strength_krw` | 강도 경로 매수 금액(원) | 100000~100000000 | `1000000` | vibe 판단기·실행기를 다시 시작해야 반영됩니다 |
| `tunables.vibe_chase_vwap_pct` | 추격 필터: VWAP 위 상한(%) | 0.5~10.0 | `2.0` | vibe 판단기·실행기를 다시 시작해야 반영됩니다 |
| `tunables.vibe_chase_range_pct` | 추격 필터: 구간 위치 상한(%) | 50~100 | `85` | vibe 판단기·실행기를 다시 시작해야 반영됩니다 |
| `tunables.live_session_end` | 미러 종료 시각 | 15:00~15:20 | `"15:20"` | 실계좌 미러 실행기를 다시 시작해야 반영됩니다 |
| `tunables.quote_cache_s` | 시세 재사용(초) | 0~120 | `15` | 바로 반영됩니다 |
| `tunables.chart_cache_s` | 차트 재사용(초) | 0~300 | `30` | 바로 반영됩니다 |
| `tunables.account_list_cache_s` | 계좌 목록 재사용(초) | 0~600 | `600` | 바로 반영됩니다 |
| `tunables.process_cache_s` | 실행기 상태 재사용(초) | 0~60 | `5` | 바로 반영됩니다 |

### 설정 ai

Jev·OpenAI 판단 사용.

| 주소 | 이름 | 값 | 기본 | 반영 |
| --- | --- | --- | --- | --- |
| `ai.jev` | Jev 판단 사용 | `true` \| `false` | `true` | 주문 없는 회차 |
| `ai.decision` | OpenAI Decisions 사용 | `true` \| `false` | `true` | 주문 없는 회차 |

### 설정 prefs

대시보드 화면 설정(장중에도 바로 적용, 승인 없음).

| 주소 | 이름 | 값 | 기본 | 반영 |
| --- | --- | --- | --- | --- |
| `prefs.status_blink` | 화면: 상태 점 깜빡임 | `True` \| `False` | `true` | 바로 |
| `prefs.start_tab` | 화면: 시작 탭 | `종합 현황` \| `시장 상황` \| `전략 구조` \| `트레이딩 현황` \| `매매일지` \| `성과 비교` \| `계좌·자동매매` \| `장기투자` \| `실계좌` \| `운영` | `"종합 현황"` | 바로 |
| `prefs.refresh_s` | 화면: 자동 갱신 간격(초) | `15` \| `30` \| `60` \| `120` | `30` | 바로 |
| `prefs.quotes_auto` | 화면: 시세 자동 갱신 | `True` \| `False` | `false` | 바로 |
| `prefs.money_unit` | 화면: 금액 단위 | `원` \| `만원` \| `10만원` \| `100만원` \| `1000만원` \| `억원` | `"만원"` | 바로 |
| `prefs.font_scale` | 화면: 글자 크기 % | `70` \| `75` \| `80` \| `85` \| `90` \| `100` \| `110` \| `120` | `100` | 바로 |
| `prefs.theme` | 화면: 화면 색(밝게·어둡게) | `system` \| `dark` \| `light` | `"system"` | 바로 |
| `prefs.ask_selection` | 화면: 고른 글로 AI에게 묻기 | `True` \| `False` | `true` | 바로 |
