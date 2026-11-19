---
name: vibe-trading
description: NH 나무 모의 ETF 분할 매수/매도 운용과 vibe-trading 리서치 도구. "/vibe-trading", 트레이딩 진행 상황·결과 보고, 시황·차트·투자자 동향·뉴스 분석, 분할 매수/매도 판단, 종목 정리(청산) 요청, 백테스트·팩터 리서치에 사용한다.
---

# vibe-trading (이 저장소 전용)

먼저 프로젝트 `AGENTS.md`의 안전 규칙을 따른다. 계좌번호·인증키를 출력하지 않는다. 시각은 Asia/Seoul로 쓴다.
모든 명령은 저장소 루트에서 `PYTHONPATH=agent:scripts .venv/bin/python ...`로 실행한다.

## 실행 구조 (자동 실행기가 주문, Claude는 분석·보고·요청)

- 주문은 공유 ETF 모의 실행기(`scripts/nh_paper_isolated.py --run --shared-default --execute`, 서비스 `com.vibetrading.shared-etf-paper`)만 낸다. 실행기는 매 주기 증권사 잔고·주문과 자체 원장을 대조하므로, **대시보드·직접 API·다른 앱으로 이 계좌에 주문하지 않는다**(원장 불일치로 실행기가 멈춘다).
- 대상 7 ETF: K200 069500, LEV 122630, INV 114800, KQ150 229200, KQ150LEV 233740, KQ150INV 251340, SEMILEV 494310.
- 순위 종목(2026-11-11 사용자 승인): `scripts/market_movers.py`(서비스 `com.vibetrading.market-movers`, 평일 08:58 시작·15:05 종료)가 5분마다 코스피200·코스닥150 구성종목을 NH 시세로 훑어 거래대금·거래량·급상승·급하락 상위를 `movers.json`에 저장한다. 각 순위 상위 3개(최대 10개)가 7 ETF와 함께 Jev 종목 후보가 되고, 같은 분할 규칙으로 모의 매매된다. 12분보다 오래된 스캔이나 분봉이 신선하지 않은 종목은 후보에서 빠진다. "인기"는 거래대금 상위로 대신하고, 지수 구성종목 밖 종목은 포함되지 않는다.
- 관심종목(2026-11-11 사용자 승인: Jev 선정 · 3개 · 30분마다): 순위 계산기가 09:05부터 30분마다 `scripts/watchlist.py`로 후보(1주 가격 100만 원 이하)마다 종목정보(PER·PBR·시총·업종·52주·외국인 보유율)·투자자 일별 순매수·최근 하루 종목 뉴스·1분봉과 시황(KODEX 200·코스닥150 등락, 증시 뉴스)을 모아 Jev에 묻고 확률 상위 3개를 `watchlist.json`에 저장한다. 실행기는 순위 원본이 아니라 이 관심종목(45분 이내 선정)만 7 ETF와 함께 매매 후보로 쓴다. 보고 시 `watchlist.json`의 selected·probabilities·stocks를 근거로 설명한다.
- 공유 계좌 5종목 한도는 2026-11-11부터 "하루 새로 산 종목 5개"다(이전: 누적).
- 사용자 승인 분할 전략(2026-11-11):
  - 1차 매수: Jev 종목 후보 신뢰도 0.5 이상 + 데이터 확인(2026-11-11 사용자 선택: 지수 방향 일치·최근 5분 거래량이 직전 5분의 1.2배 이상, 시장 요약 없으면 매수 안 함), 3연속 손실 뒤 30분 신규 진입 쉼, 최근 분봉 상승, 보유 종목과 반대 방향(롱↔인버스) 아님, 이미 보유한 종목 아님. 약 100만 원(1회 최대 100주).
  - 관심종목 강도 진입(2026-11-12 사용자 승인): Jev가 대기·저신뢰일 때 오늘 Jev 관심종목 선정 상위 3위 안(확률 0.15 이상, 이미 보유한 종목 제외), 전일 대비 +2~+25%, 1분봉 상승, 거래량 1.2배인 종목을 지수 방향 확인 없이 약 100만 원 매수한다. 청산 규칙은 같다.
  - 신규주 진입(2026-11-12): Jev 대기·관심종목 후보 없음일 때, KIND 신규상장 목록을 NH 상장일로 확인한 뒤 상장 후 2거래일 안 · 기준가·시가 위 · 고가 3% 안 · 분봉 상승·거래량인 종목을 1차 매수액의 50%로 산다. 신규주형 청산(손절 3% · +3% 절반 · 추적 2% · 당일 정리)이며 설정에서 바꾼다.
  - 2·3차 매수: 첫 매수가 대비 −0.7%, −1.4% 이하이고 직전 완료 분봉이 반등했을 때 각 약 100만 원. 종목당 300만 원 이하. 매도가 시작된 포지션에는 추가 매수 없음.
  - 매도: 매입평균가 +1%에서 그때 남은 수량의 절반(2026-11-18부터, 그 전에 판 몫 제외), +2%에서 나머지. 평균가 −2% 손절, Jev 보유 SELL 신뢰도 0.7 이상이면 전량.
  - 국면별 청산(2026-11-11 사용자 요청 "상승장엔 더 들고 가기"): 매수 시점 지수 국면이 상승장(약세일 제외)이거나 오늘 강세면 추세형(인버스는 하락장·약세일). 추세형은 +2% 절반, 나머지는 그 가격에 닿은 뒤 고점 대비 −1.5% 추적 손절(2배 ETF ×1.5), 고정 +2% 전량 매도 없음. 보유 중 강세로 바뀌면 추세형으로만 전환. 장 마감 때 추세형·상승장(인버스는 하락장)·수익 중(절반 익절 또는 +1%↑)·다음 날 거래일이면 300만 원(설정 `overnight_max_manwon`) 한도 안에서 보유, 나머지는 정리. 값은 대시보드 매매 기준 설정에서 바꾼다.
  - 방어형 데이터 목표·종가매매(2026-11-11 사용자 요청): 하락·횡보장 방어형은 종목별 목표에서 전량 익절(최근 20일 장중 상승폭 중앙값×국면 계수 하락 0.4·횡보 0.5·그 외 0.6, 0.8~3%, 데이터 없으면 1%/2% 분할). 장 마감 보유는 기본 금지(당일 정리). 예외는 `nh_paper_isolated.OVERNIGHT_RULES` 12개 조건(추세형, 상승장+강세일, 상승/하락 종목 2배, 지수 외국인·기관·프로그램 순매수, 1차 익절 달성, 거래량 1.5배, 종가 강도 상위 20%·시가 위, 현재가>5일>20일 평균, Jev HOLD 0.6↑, vibe 점수 있으면 +0.3↑, 한도, 다음 날 거래일)을 모두 충족할 때만. 결과·처음 걸린 조건은 `overnight_hold` 이벤트.
  - 안전 규칙(2026-11-11 사용자 승인 3-1): 절반 익절 뒤 나머지는 평균가(본전) 이하에서 전량(2026-11-17: 판 뒤 다시 사면 새 평균가의 일반 손절로 돌아가고 다시 팔면 재적용), 14:30부터 신규·추가 매수 없음(2026-11-17: 추가 매수는 장 마감 정리 30분 전부터 없음), 14:40부터 보유 전량 보호 매도(하루 넘겨 보유 안 함, 한 주기 1건 매도라 20분 확보; 11/11 리뷰로 14:40/14:50에서 앞당김), 코스피(코스닥 ETF는 코스닥) 하락장+오늘 약세면 2·3차 추가 매수 중단(인버스는 상승장+강세), 손절 폭이 호가 차이 2배보다 좁으면 진입 거부. 매도 기준은 매수 시점에 `exit-plans.json`으로 고정된다.
  - 공유 계좌 가드: 회당 300만 원, 일일 매수 600만 원, 누적 2,100만 원, 누적 5종목(지금까지 산 종목 수), 일일 손실 30만 원.
- 매매 기준값(매수·매도 신뢰도, 1·2차 익절 %, 손절 %, 추가 매수 간격 %)은 대시보드 "전략·AI 설정" 탭에서 바꾸며 `~/.vibe-trading/nhplug/shared-etf-paper/strategy-settings.json`에 저장된다(`scripts/strategy_settings.py`, 범위 검증). 실행기는 매 주기 다시 읽고, 바뀌면 `strategy_settings` 이벤트를 남긴다. 파일이 없거나 잘못되면 위 기본값을 쓴다. 사용자 요청 없이 이 값을 바꾸지 않는다.
- 엔진 역할(2026-11-12 사용자 결정, `scripts/engine_roles.py`): 메인 엔진(`accounts.json` paper-shared.engine, 지금 Jev)은 공유 모의계좌에서 실제 모의 체결로 매매한다. 다른 엔진은 **보조**로 같은 실행기 코드를 가상 체결(`scripts/virtual_paper_broker.py`: 실시간 호가에 닿는 지정가를 전량 체결, 수수료·부분 체결 없음, 공유 가드 한도 적용)로 계속 돌려 데이터를 쌓는다. Jev 보조 = `nh_paper_isolated.py --virtual-jev`(launchd `com.vibetrading.jev-virtual`, `nhplug/jev-virtual`, 공유 실행기 입력 파일은 심볼릭 링크로 함께 읽음, Jev가 메인이면 보유가 없을 때 Jev 호출 없이 대기). vibe 보조 = `vibe_runner.py --virtual`(launchd `com.vibetrading.vibe-paper`, `nhplug/vibe-paper`, 가상 1,000만 원, vibe가 메인이면 새로 사지 않음). AI 판단은 그 판단으로 매매하는 실행기 한 곳에서만 부른다(vibe 메인이면 공유 실행기는 Jev 보조가 돌 때 기록용 Jev·Decision 호출을 건너뛴다). Jev 보조와 vibe 판단기는 공유 실행기가 `events.jsonl`에 남긴 1분봉·투자자 동향을 먼저 재사용하고 없는 것만 NH에 묻는다. 메인 엔진을 바꾸면 역할이 자동으로 바뀌고, 두 엔진의 매매 결과와 판단 정산(`engine_roles.scoreboard`, 대시보드 "Jev vs vibe-trading")으로 메인을 고른다. 가상 체결은 실제 체결 검증이 아니다.
- vibe-trading 판단기(`scripts/vibe_advisor.py`, 서비스 `com.vibetrading.vibe-advisor`, 평일 09:08~14:50, 15분마다, 메인 엔진과 상관없이 항상): vibe-trading 에이전트를 `scripts/vibe_agent_safe.py`(읽기 전용 시장·분석 도구와 `load_skill`만 허용)와 별도 홈 `~/.vibe-trading-advisor`로 실행한다. 매 회차 vibe-trading 스킬을 쓴다: `technical-basic` 스킬 코드(`agent/src/skills/technical-basic/example_signal_engine.py`)로 NH 1분봉 80개의 추세·과열·거래량 판정(`technical_basic`)을 계산해 넣고, 프롬프트 절차(research-discipline 점검 → technical-basic 판정에서 시작 → 도구로 1회 이상 확인 → 필요하면 load_skill)를 따른다. 점수(−1~1)는 규칙으로 바꾼다: 매수 신호 = 이번·직전 회차(35분 이내) 평균(±1.0이 60% 이상인 회차는 절반), 도구로 확인하지 않은 회차(`grounded` false)·technical-basic 하락 판정 종목은 매수 없음, 같은 지수 반대 방향 동시 매수 금지, 신호 0.5 이상 × 300만 원, 보유 −0.3 이하 전량 매도. 기록: `vibe-paper/vibe-advice.json`(scores, signals, technical_basic, grounding, skills_used)과 `market-research.sqlite3`(kind vibe_advice).
- vibe-trading 실행기(`scripts/vibe_runner.py`): vibe가 보조일 때 `--virtual`로 가상 체결 운용(위 엔진 역할). 한도: 회당 300만·하루 600만·하루 신규 5종목·종목당 300만 원·하루 손실 30만 원, 익절은 공유 매매 기준 `take_profit_all_pct`, 손절 `stop_loss_pct`, 14:30 신규 매수 마감, 14:40 전량 정리, 일시 조회 실패 5회까지 계속. 분리 모의계좌(isolated-paper)는 국내주식(01)이 아니라 실주문 모드(`--virtual` 없이)는 쓰지 않는다.
- 계좌별 설정(`scripts/accounts.py`, `~/.vibe-trading/nhplug/accounts.json`, 계좌번호 미저장·해시와 가린 번호만): paper-shared(메인 엔진 jev|vibe, 자동 ON/OFF, 1차 매수액·종목당 한도), live-1(Jev)·live-2(vibe) 실계좌. 실계좌 실행기 `scripts/nh_live_follow.py`는 자기 엔진의 원장(메인이면 공유 모의, 보조면 가상)을 미러한다(모의 분할 1회 = 실계좌 1주). 기본 dry-run, `--execute`와 launchd 등록은 별도 승인이며 지금 꺼져 있다. 대시보드 가상 실행 카드(`nh_live_shadow.account_shadow`)는 관찰용 재계산이다. 두 엔진은 서로의 데이터를 참고한다: Jev는 `track_record.vibe_view`(점수·확정 신호·vibe 매매 결과·보유), vibe는 `track_record.jev_view`(최근 판단·매매 결과·보유·전략 규칙과 매매 기준·판단 정산)와 `engine_scoreboard`.
- 관심종목 직접 편집: `watchlist.edit_manual`(watchlist-manual.json) — 추가 종목은 Jev 선정과 별개로 후보, 제외 종목은 Jev가 골라도 제외(유지).
- 실계좌 한도(AGENTS.md): 운용금 100만 원, 하루 손실 2만 원 래치, 1회 1주, 하루 신규 5종목, 계좌별 1주 가격 상한(매수만, 최대 200만 원), 매도는 매수호가 대비 3% 하한, 미체결·미확인 차단. 실계좌 실주문을 켜거나 한도를 바꾸지 않는다. 설계·켜는 절차: `guides/nh-live-follow-plan.md`.

## 보고 ("진행 상황/결과 알려줘")

1. `scripts/vibe_market_brief.py` — 7 ETF 현재가·등락률·최근 1분 종가, 투자자별 일별 순매수(외국인·기관·개인; 당일 행은 장중 잠정치·주로 외국인 어림값이거나 0이며 장 마감 후 확정), 뉴스, 실행기 서비스 상태·원장 보유·오늘 체결·최근 Jev 의견과 주문 이벤트.
2. 증권사 기준 잔고·손익은 대시보드 http://127.0.0.1:8501/ 첫 탭(종합 현황·계좌 잔고·종목별 손익) 또는 `streamlit_namuh/app.py`의 `shared_etf_broker_snapshot`으로 확인한다.
3. 엔진 비교: `PYTHONPATH=agent:scripts .venv/bin/python -c "import engine_roles,json,pathlib;print(json.dumps(engine_roles.scoreboard(pathlib.Path.home()/'.vibe-trading/nhplug'),ensure_ascii=False,indent=1))"` — 역할·원장·매매 결과·판단 정산. 실계좌: 미러 목표는 대시보드 계좌 카드, 실행기 기록은 `~/.vibe-trading/nhplug/live-follow.jsonl`(증권사 조회가 필요한 `nh_live_follow.py --once`는 NH 호출 한도를 함께 쓰므로 장중에는 꼭 필요할 때만).
4. 보고에는 조회 시각, 증권사 확인 체결과 로컬 판단(Jev 의견)의 구분, 수수료·세금 미반영, 미확인 주문 수를 적는다. 체결·수익을 증거 없이 주장하지 않는다.

## 분석과 판단 지원

- Jev 입력(2026-11-11 14:00~): 7 ETF 최근 1분 종가 차트, 최근 하루 뉴스 헤드라인 최신순 9건, ETF별 투자자 일별 순매수(직전 확정일·오늘 잠정). 기록은 events.jsonl의 market_context(`news`, `investor_flows`)에 있다.
- 시황·차트·투자자·뉴스는 위 브리핑을 근거로 해석한다. ETF의 투자자 순매수에는 유동성공급자(기관 중 금융투자) 거래가 섞여 있어 방향 신호로 약하다는 점을 명시한다. Jev 신뢰도는 수익 확률이 아니다.
- 분석 결과로 전략 수치(분할 간격, 금액, 신뢰도 기준)를 바꾸려면 사용자 승인을 받은 뒤 `nh_paper_isolated.py`의 `SPLIT_*`·`ETF7_MAX_ORDER_QTY` 상수와 테스트를 고치고, 아래 재시작 절차를 따른다.

## 사용자가 요청할 때 할 수 있는 조치

- **종목 정리(전량 매도) 요청:** 공유 ETF 폴더 `~/.vibe-trading/nhplug/shared-etf-paper/exit-requests.json`에 종목코드 JSON 목록을 쓴다(예: `["114800"]`, 권한 600). 실행기가 다음 주기에 AI 판단 없이 보호매도 경로로 팔고, 보유가 0이 되면 요청을 지운다.
- **실행기 재시작(코드 변경 반영):** 미확인 주문 0건 확인 → `STOP` 파일 생성 → 프로세스 종료와 `stopped` 이벤트 확인 → `STOP` 삭제 → `launchctl kickstart gui/$(id -u)/com.vibetrading.shared-etf-paper` → 첫 주기 로그 확인. 전환 중에는 손절 감시가 멈춘다고 알린다. 이 절차는 `.venv/bin/python scripts/restart_runner.py --runner shared|vibe|jev-virtual --reason "<이유>"`가 한 번에 밟는다(미확인 주문·손절선 0.5%p 안 보유가 있으면 거부, `--force`로만 진행, 매번 `<실행기 폴더>/restarts.jsonl`에 이유·감시 중단 시간 기록). 장중 재시작은 손절 감시를 멈추므로 코드 반영은 가능하면 장 전·보유 없을 때 몰아서 한다(11/17 장중 7회).
- 원장·이벤트 파일을 지우거나 고치지 않는다. 오류·불확실 주문이 있으면 재전송하지 말고 증권사 조회로 대조한다.

## 리서치 도구 (vibe-trading 패키지)

백테스트·팩터·옵션·멀티에이전트 팀 등 일반 리서치는 `agent/SKILL.md`를 읽고 `.venv/bin/vibe-trading` CLI 또는 `vibe-trading` MCP 서버(`.venv/bin/vibe-trading-mcp`)를 사용한다. 한국 일별 데이터는 pykrx 소스를 쓸 수 있다. 리서치 결과는 실주문 근거나 수익성 검증으로 간주하지 않는다.

## 자동 일정 (2026-11-11부터, Codex 자동화 대신)

| 시각(평일) | 작업 | 방식 |
|---|---|---|
| 08:50 | 시장 상황·순위·관심종목 (`market_movers.py`, 장전 1회 후 09:00~15:05 5분마다) | launchd `com.vibetrading.market-movers` |
| 09:08 | vibe-trading 판단기 (`vibe_advisor.py`, 15분마다, 메인 엔진과 무관) | launchd `com.vibetrading.vibe-advisor` |
| 09:11 | 보조 엔진 가상 실행기 (vibe `vibe_runner.py --virtual`, Jev `nh_paper_isolated.py --virtual-jev`; 메인 엔진 쪽은 대기) | launchd `com.vibetrading.vibe-paper`, `com.vibetrading.jev-virtual` |
| 09:10 | 거래일 시작 점검 → 공유 모의 실행기·학습기·국면 관찰기 시작 (`trading_day_start.py`, 결과는 `shared-etf-paper/trading-day-start.jsonl`) | launchd `com.vibetrading.trading-day-start` |
| 11:30·13:30·14:30 | 장중 진행 보고 (읽기 전용) | Claude 앱 예약 작업 `trading-intraday-report` |
| 15:10 | 포지션 경로·판단 정산·장 마감 복기 | 학습기(shared-etf-learning) 종료 시 |
| 15:45 | 장 마감 후 모의·실계좌 보고 (읽기 전용) | Claude 앱 예약 작업 `trading-close-report` |
| 15:50 | 기록 백업 (`backup_trading_data.py` → `~/.vibe-trading/backups/날짜`, 14일 보관) | launchd `com.vibetrading.trading-backup` |
| 금 16:10 | 청산 규칙 재현 평가 (`exit_replay.py`) | launchd `com.vibetrading.exit-replay` |

## HTML 가독성 문법 (2026-11-17)
이 리포트는 HTML 페이지(GitHub Pages)로도 발행된다. 템플릿 절 제목·순서는 그대로 두고, 필요한 곳에만 다음을 쓴다(자세한 규칙: `.claude/skills/report-html-publisher/references/report-syntax.md`).
- 절 결론은 `> [!SUMMARY]`, 꼭 알아야 할 사실은 `> [!IMPORTANT]`, 조건부 위험은 `> [!WARNING]`, 손실·오류는 `> [!CAUTION]` 콜아웃으로(한 절에 하나까지).
- 꼭 기억할 한 구절은 `==형광펜==`(리포트 전체 2~4개), 핵심 숫자·종목은 `**굵게**`.
- 등락·손익 표는 `+`/`−` 부호와 단위를 붙이면 HTML에서 막대가 자동으로 붙는다.
- 장 마감 개선안·낙폭과대 주간 감사 보고서도 같은 문법을 쓴다. 개선안마다 근거는 표, 결론은 `> [!SUMMARY]`, 승인 필요 사항은 `> [!IMPORTANT]`.
