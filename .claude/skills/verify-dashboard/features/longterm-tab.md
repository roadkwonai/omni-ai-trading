# 장기투자 탭

The long-term engine's own tab: regime and plan status, NAV against the shadows, the level chart, strategy reports, settings (modules, 적립식, run settings), market and theme stories, portfolio and decision record. Sub-tabs render only when opened.

## Sub-features

- `lt-status`: the status line shows the 운영·모드·실행 방식 cards (운영 꺼짐 when the engine is switched off).
- `lt-overview`: the NAV cards (actual and shadows), 적립 누계 when 적립식 paid in, and the bullet chart of target vs current weights after the 09:40 plan.
- `lt-market`: the regime basis table (`국면 기준`), the regime history band and the macro background card.
- `lt-record`: the decision record with every engine decision kind, 적립식 included.
- `lt-dca`: the 적립식 settings per book.
- `lt-reports`: the strategy report list, newest first, daily above weekly for the same date.

## How to get to it (user POV)

- The top tab `장기투자`, then a sub-tab.
- The sidebar button `장기투자 탭 열기`.

## Driving it with drive.py / browser

Preconditions:

- `drive.py --doctor` passes.
- `~/.vibe-trading/nhplug/vibe-longterm/longterm.sqlite3` exists (any decision recorded).

- **Market.** Open 시황·국면. Run `drive.py --tab 장기투자 --state lt_tab=시황·국면 --expect "국면 기준" --id lt-market`. Exit 0, `expect` all true, no `files_changed`.
- **Overview.** Open 개요. Run `drive.py --tab 장기투자 --state lt_tab=개요 --expect "목표 대비 현재 비중" --id lt-overview`. Before today's 09:40 plan the caption `첫 주문 계획(09:40) 뒤에 보입니다` is the expected empty state.
- **Record.** Open 기록. Run `drive.py --tab 장기투자 --state lt_tab=기록 --id lt-record`. Exit 0. The JSON tabs list contains all ten sub-tab labels.
- **Every sub-tab renders.** Repeat with each `lt_tab` value from the index. Every run returns `exceptions: []`.

## Gotchas

- `lt_tab` values must match the label exactly (`시황·국면` uses a middle dot).
- The overview's bullet chart, the 적립 누계 card and the macro card depend on today's records. Report the empty state instead of forcing data.
- Pressing 저장 in 전략(거장 모듈·비중, 적립식)·시장(확정 일수)·계좌·실행(가상 체결) writes `vibe-longterm/settings.json` and the settings history. Never do it in a verification run.
