# Dashboard verification map

The maintained source for proving user-facing dashboard behaviour. Read this index, then use the matching feature file as the recipe.

## Baseline preconditions

- `drive.py --doctor` passes (`app_exists`, `runtime_exists`; `env_file_present` for quote calls).
- Headless drives need no server. Browser drives use the run's own instance on `http://127.0.0.1:8599` (see SKILL.md Launch), never the user's 8501.
- Data is the live `~/.vibe-trading` record. Features whose record does not exist yet (for example before the first 08:45 long-term decision of the day) show their empty state. That is a valid observation, not a failure.

## Driving conventions

- One top tab per `drive.py` call. Choose it with `--tab <label>`.
- Sub-tabs and switches go through `--state <widget key>=<value>`.
- Top tab labels: 종합 현황, 시장 상황, 전략 구조, 트레이딩 현황, 매매일지, 계좌·자동매매, 엔진 비교, 장기투자, 주문·정리, AI·실행 기록, 실계좌, 운영 상태, API 연동, 백업·이식.
- Widget keys:
  - `main_tab`: top tab, set by `--tab`.
  - `lt_tab`: 장기투자 tab. Values: 개요, 시장, 전략, 계좌·실행, 리포트·감사, 미국 종목 아이디어, 변경 이력 (2026-11-18 S4). The sub-view of a tab is `lt_sub_<tab>` (시장: 시황·국면·테마 스토리·차트; 전략: 거장 모듈·비중·적립식; 계좌·실행: 실행·포트폴리오·주문·기록; 리포트·감사: 전략 리포트·감사·개선안). `vibe_longterm_view.goto(state, name)` takes an old tab name.
  - `strategy_engine`: 전략 구조 engine. Values: Jev 트레이딩, 장기투자.
  - `lt_flow_tab`: 전략 구조 → 장기투자 sub-tab. Values: 아키텍처, 하루 알고리즘, 국면 전이, 거장 모듈, 구성 요소 · 오늘 운영 상태.
  - `api_section`: API 연동 view. Values: 외부 API, 스킬·자동 작업, 리포트 발행.
  - `api_pick`: the API detail. A label from scripts/api_catalog.py, for example `FRED (세인트루이스 연준)`.
  - `trading_account`: sidebar account.
- Never press save/send widgets (SKILL.md Safety).

## Proof and skip reporting

- Keep the `drive.py` JSON for every claim, and name the feature ID and entry point used.
- Visual claims (diagram drawn, layout) need the browser path: the returned JSON plus a screenshot described in the report.
- Report an unreachable entry point with the command and the unmet precondition. Do not prove it through another path.

## Feature entry contract

Each feature file has an H1, one paragraph, then `Sub-features`, `How to get to it (user POV)`, `Driving it with drive.py / browser` (starting with `Preconditions:`) and `Gotchas`.

## Features

- [장기투자 탭](./longterm-tab.md): the long-term engine's ten sub-tabs (overview, chart, reports, settings, 적립식, market, stories, portfolio, record, run settings).
- [전략 구조 → 장기투자](./strategy-longterm.md): engine switch, status cards, archify diagrams, regime and route cards.
- [미국 장기 지표 · 종합 현황](./market-overview.md): the long-term section of 시장 상황 and the long-term rows and alerts of 종합 현황.
- [API 연동 → FRED](./api-fred.md): FRED and Fed calendar status, the engine's last macro data and the on-demand check.
