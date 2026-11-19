# 전략 구조 → 장기투자

The strategy tab's 장기투자 view explains the long-term engine. It shows status cards, three detail pop-ups (운용 기준 전체, 국면별 대응, 참고 데이터), five archify diagrams (아키텍처, 하루 알고리즘, 국면 전이, 거장 모듈 지도, 오늘 운영 상태), a regime card row and six 매매 경로 cards, all read from the saved settings and today's record.

## Sub-features

- `sl-switch`: the `Jev 트레이딩 / 장기투자` segmented control swaps the whole view.
- `sl-status`: the cards 장기투자 운영, 모드, 실행 방식, 다음 단계, 장기 국면.
- `sl-diagrams`: each `lt_flow_tab` sub-tab draws an archify iframe. The graphviz fallback appears only when archify fails.
- `sl-routes`: the 매매 경로 cards marked 켜짐/꺼짐 by the active modules.

## How to get to it (user POV)

- The top tab `전략 구조`, then the segmented control `장기투자`.

## Driving it with drive.py / browser

Preconditions:

- `drive.py --doctor` passes. For the browser path, the run's instance on 8599 is ready.

- **Headless switch.** Run `drive.py --tab "전략 구조" --state strategy_engine=장기투자 --expect "매매 경로" --expect "국면별 대응" --id sl-switch`. Exit 0.
- **Each diagram tab.** Add `--state lt_flow_tab=<value>` for 하루 알고리즘, 국면 전이, 거장 모듈, 구성 요소 · 오늘 운영 상태. Exit 0 each.
- **Diagram drawn (browser).** In the background tab on 8599, click `전략 구조`, then `장기투자` (SKILL.md Drive snippet). The result has `errors: []`, `iframes ≥ 1` and `fallback: false`. Screenshot the diagram.

## Gotchas

- archify renders on first view and caches by content under `nhplug/vibe-longterm/diagrams/`. A first render can add a cache file to `files_changed`. That is the intended cache, not a settings write.
- AppTest does not run the iframe's JavaScript. Only the browser path proves a diagram is visible.
