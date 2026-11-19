# 미국 장기 지표 · 종합 현황

시장 상황 shows the long-term engine's US view (장기 국면 card with pending days, 나스닥100 drawdown, 200-day gap, VIX, macro background). 종합 현황 lists the long-term virtual account as an account row, and its alerts include long-term items (skipped decision, broken theme story, missing macro data).

## Sub-features

- `mo-us`: the 시장 상황 section `미국 장기 지표 · 장기투자 엔진`, or its empty-state caption before the first decision.
- `mo-account`: the 종합 현황 row `장기투자 가상 계좌`.
- `mo-alerts`: long-term alerts in the 주의 list when today's record has them.

## How to get to it (user POV)

- The top tab `시장 상황`, scrolled below 해외 지표.
- The top tab `종합 현황`.

## Driving it with drive.py / browser

Preconditions:

- `drive.py --doctor` passes. A long-term decision is recorded.

- **US section.** Run `drive.py --tab "시장 상황" --expect "미국 장기 지표" --id mo-us`. Exit 0.
- **Account row.** Run `drive.py --tab "종합 현황" --id mo-account`. Exit 0. The account table is a dataframe AppTest does not expose as text, so confirm the row in the browser: `document.body.innerText.includes('장기투자 가상 계좌')`.

## Gotchas

- 시장 상황 has a `시장 자료 갱신` button that calls Naver/Yahoo and writes `market-context`. Do not press it.
- The macro card appears only when the day's engine plan carries macro data (from 08:45).
