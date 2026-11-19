# API 연동 → FRED

The API tab's FRED (and 연준 FOMC 일정) detail shows the key status, call counts, and a `최근 데이터` sub-tab with the values the long-term engine received at its last decision. A `지금 확인` button fetches them once on demand (6 calls, not saved).

## Sub-features

- `af-detail`: choosing `FRED (세인트루이스 연준)` shows its key status `FRED_API_KEY ✓`.
- `af-data`: the `최근 데이터` sub-tab shows the engine's last macro values, or the empty-state caption.
- `af-check`: `지금 확인` replaces the view with a fresh on-demand result while it is newer than the engine's.

## How to get to it (user POV)

- The top tab `API 연동`, view `외부 API`, then the `API` select under API 상세.

## Driving it with drive.py / browser

Preconditions:

- `drive.py --doctor` passes with `env_file_present: true`.

- **Detail.** Run `drive.py --tab "API 연동" --state "api_pick=FRED (세인트루이스 연준)" --expect "FRED_API_KEY ✓" --id af-detail`. Exit 0.
- **On-demand check (browser, only when that button is the change under test).** In the 8599 tab choose FRED, open `최근 데이터`, press `지금 확인`. The caption changes to `이 화면에서 지금 확인 HH:MM:SS` and no `stException` appears.

## Gotchas

- `지금 확인` makes real external calls (FRED 5, Fed page 1). Press it at most once per run.
- The status column counts calls per process. A fresh 8599 instance starts with no calls of its own.
