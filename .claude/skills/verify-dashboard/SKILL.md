---
name: verify-dashboard
description: "Prove a change to the Omni AI Trading Streamlit dashboard (streamlit_namuh/app.py: 종합 현황, 시장 상황, 전략 구조, 장기투자, 운영 상태, API 연동 …) works the way a user sees it — render the real app per tab headlessly with AppTest and save JSON evidence, or open an isolated instance on port 8599 in the browser for visual checks (archify diagrams, layout). Use after editing anything under streamlit_namuh/ or a scripts/ module a tab reads, before saying a dashboard change is done. Not for unit tests (pytest) or broker/order paths."
---

# Verify the dashboard

The dashboard is a Streamlit app (`streamlit_namuh/app.py`). The user's own copy runs as the launchd service
`com.vibetrading.shared-etf-dashboard` on `http://127.0.0.1:8501`. **Never drive 8501**: it is the user's session, and its
buttons write real settings. Verify with one of two drivers that this run starts itself.

All commands run from the repo root (`/Users/freelife/ai/trading/vibe-trading`) with
`PYTHONPATH=agent:scripts:streamlit_namuh`. Evidence goes to `~/.vibe-trading/verify/<run>/` and is never deleted by cleanup.

## Safety (read first)

- Rendering a tab reads live `~/.vibe-trading` data and makes the same read-only NH quote calls a person opening the tab
  would (`[nhplug-mcp] … trading=OFF` in the log is expected). It never orders. NH allows 5 calls/s per app key, shared with
  the live executors: run one drive at a time, never in a loop.
- Do not press buttons or change widgets that save or send: 저장, 실주문 스위치, 가상 체결 스위치, 즉시 매도, 수동 매수·매도,
  정리 요청, 운영 엔진, 설정 변경 이력 되돌리기. The '지금 확인' button (API 연동 → FRED) makes 6 external calls: only
  when the feature you verify is that button.
- Rendering must not write files. `drive.py` lists every file under `~/.vibe-trading` changed during the render. During
  market hours (09:00–15:40) the running services also write (`shared-etf-paper/events.jsonl`, `orders.sqlite3`,
  `live-follow*.jsonl` …); check a listed file against `launchctl list | grep vibetrading` before calling it a finding.

## Launch

**Headless (default, no server):** nothing to launch. `drive.py` loads `app.py` in-process.

**Browser (visual checks only: archify iframes, layout, colours):** start a private instance on 8599.

```bash
RUN=~/.vibe-trading/verify/$(date +%Y%m%d-%H%M%S); mkdir -p "$RUN"
(nohup .venv/bin/streamlit run streamlit_namuh/app.py --server.address 127.0.0.1 --server.port 8599 --server.headless true > "$RUN/streamlit-8599.log" 2>&1 & echo $! > "$RUN/streamlit-8599.pid")
```

Ready when `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8599/` prints `200` (about 5–8 s) and the page's tabs
appear in the browser. Code changes under `streamlit_namuh/` reload on the next rerun. A change in a `scripts/` module needs
a new instance (kill and relaunch). If 8599 is taken by a process you did not start, pick another port. Do not kill it.

## Doctor

```bash
PYTHONPATH=agent:scripts:streamlit_namuh .venv/bin/python .claude/skills/verify-dashboard/drive.py --doctor
```

Requires `app_exists` and `runtime_exists` true. `env_file_present` false means quote calls fail and tabs show
"조회 실패" messages. For the browser instance, also require `lsof -nP -iTCP:8599 -sTCP:LISTEN -t` to equal the PID in
`$RUN/streamlit-8599.pid`.

## Drive

**Headless.** One top tab per call. Choose sub-tabs through their widget keys with `--state`, and name the texts a user
must see with `--expect`:

```bash
PYTHONPATH=agent:scripts:streamlit_namuh .venv/bin/python .claude/skills/verify-dashboard/drive.py \
  --tab 장기투자 --state lt_tab=시황·국면 --expect "국면 기준" --id longterm-market --out "$RUN"
```

- Exit `0` means no exception, every `--expect` found, and no file changed. Exit `1` means one of those failed.
- The JSON at `"evidence"` holds the tabs, captions, infos, warnings and errors.
- Tab labels and widget keys are in [features/README.md](features/README.md).

**Browser (user rule 2026-11-18: look at the screen with ego-browser, carefully).** Every screen check runs in
ego-browser (`ego-browser nodejs`, guide: `ego-browser skill`) on 8599, one TaskSpace per goal, and ends with
`task.finish({ keep: [] })`. Never open or act on 8501. A check is not done until you have **looked** at screenshots
of every part the change touches (scroll the whole section, open each tab, popover and expander it adds) and read
the texts with a snapshot: say what each screenshot shows, not that the page loaded. Stable handles:

- Top tabs and sub-tabs are `[role="tab"]` with the label as text.
- Segmented controls are buttons inside `[data-testid="stButtonGroup"]`.
- Errors are `[data-testid="stException"]`.
- archify diagrams are `iframe` elements.
- The graphviz fallback says `Archify를 쓸 수 없어`.

```bash
ego-browser nodejs <<'JS'
const task = await taskSpace("verify dashboard change");
const page = task.page("p1");
await page.goto("http://127.0.0.1:8599/", { timeout: 60000 });
await page.waitForSelector('[role="tab"]', { timeout: 60000 });
const go = async (label) => { await page.evaluate((n) => [...document.querySelectorAll('[role="tab"]')]
  .find((x) => x.innerText.trim() === n).click(), label); await page.waitForTimeout(8000); };
await go("장기투자"); await go("시장");
await page.evaluate(() => [...document.querySelectorAll('[data-testid="stButtonGroup"] button')]
  .find((b) => b.innerText.trim() === "차트").click());
await page.waitForTimeout(8000);
console.log(await page.evaluate(() => ({ errors: [...document.querySelectorAll('[data-testid="stException"]')]
  .map((e) => e.innerText.slice(0, 200)), iframes: document.querySelectorAll("iframe").length })));
console.log({ shot: await page.screenshot({ path: "/ABSOLUTE/RUN/chart.png" }), taskSpaceId: task.spaceId });
JS
```

Open each saved screenshot with the Read tool and describe it. Scroll with `page.mouse.wheel()` (move over the area
first) for parts below the fold and take another screenshot; take a `page.snapshot()` when you need the exact texts.

Keep each script under about 60 s of waits; split longer walks across calls (the TaskSpace and its pages persist).

## Evidence

- **Headless:** the JSON file per drive in `$RUN` (`drive.py` writes it).
- **Browser:** save the object the script printed to `$RUN/<feature-id>.browser.json` and the screenshots to `$RUN/`; read
  each one and describe what it shows in your report.
- **Proof standard:**
  - Drive the user's path (tab → sub-tab), not an internal function.
  - Record the action (tab/state) and the resulting texts.
  - Check side effects through `files_changed`.
  - An `--expect` you chose to make it pass is not proof. Take the text from the feature file or the requirement.

## Cleanup

```bash
kill "$(cat "$RUN/streamlit-8599.pid")"; sleep 1; lsof -nP -iTCP:8599 -sTCP:LISTEN -t || echo "8599 free"
```

- Kill only the PID this run wrote. End the ego-browser TaskSpace with `task.finish({ keep: [] })`.
- Never touch `com.vibetrading.shared-etf-dashboard` or port 8501.
- `$RUN` stays: it is the evidence.

## Helpers

- `.claude/skills/verify-dashboard/drive.py` (executable): `--doctor`, or `--tab … [--state key=value …] [--expect text …] [--id name] [--out dir]`. Its docstring lists the behaviour.
