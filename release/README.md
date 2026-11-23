# 릴리스

Omni AI Trading의 버전과 릴리스 노트를 두는 곳입니다. 공개 저장소의 [GitHub Releases](https://github.com/roadkwonai/omni-ai-trading/releases)에도 같은 노트가 올라갑니다.

| 릴리스 | 날짜 | 노트 |
| --- | --- | --- |
| v0.1.5 · 장기투자 현황과 대시보드 개선 | 2026-11-23 | [2026-11-23-v0.1.5-longterm-dashboard.md](2026-11-23-v0.1.5-longterm-dashboard.md) |
| v0.1.4 · 기획서와 서비스 소개 보완 | 2026-11-21 | [2026-11-21-v0.1.4-planning-and-landing.md](2026-11-21-v0.1.4-planning-and-landing.md) |
| v0.1.3 · 개장 즉시 진입과 인버스 규칙 강화 | 2026-11-20 | [2026-11-20-v0.1.3-opening-and-inverse.md](2026-11-20-v0.1.3-opening-and-inverse.md) |
| v0.1.2 · 주도 테마 동반주와 수능일 시각 보정 | 2026-11-20 | [2026-11-20-v0.1.2-theme-companions.md](2026-11-20-v0.1.2-theme-companions.md) |
| v0.1.1 · 투자 철학 소개와 알림 설정 안내 | 2026-11-19 | [2026-11-19-v0.1.1-investment-story.md](2026-11-19-v0.1.1-investment-story.md) |
| v0.1.0 · 첫 공개 릴리스 | 2026-11-19 | [2026-11-19-v0.1.0-first-public-release.md](2026-11-19-v0.1.0-first-public-release.md) |

## 규칙

| 항목 | 규칙 |
| --- | --- |
| 버전 번호 | [Semantic Versioning](https://semver.org/lang/ko/) `주.부.수`. 저장소 루트 [`VERSION`](../VERSION) 한 곳이 기준입니다 |
| 어떤 번호를 올릴지 | **사용자가 고릅니다.** AI는 변경 내용을 보고 추천 번호와 이유를 함께 보여 주되, 사용자가 고르기 전에는 `VERSION`을 확정하거나 발행하지 않습니다. 매매 규칙이 바뀌었다고 부 번호를 자동으로 올리지 않습니다(사용자 결정 2026-11-20) |
| 노트 파일 | `release/YYYY-MM-DD-v<버전>-<제목>.md`. 날짜는 릴리스한 날(Asia/Seoul), 제목은 영문 소문자와 하이픈입니다 |
| 노트 첫 줄 | `# Omni AI Trading v<버전> — <한 줄 제목>`. 이 줄이 GitHub Release 이름이 되고, 나머지가 본문이 됩니다 |
| 태그 | `v<버전>`. 공개 저장소 `main`의 릴리스 커밋에 annotated tag로 붙습니다 |
| 함께 맞출 곳 | README 릴리스 배지(`release-v<버전>`)와 랜딩 `landing/src/content.ts`의 `VERSION` |

노트에는 사용자에게 보이는 변화만 씁니다. 매매 규칙이 바뀌었으면 승인 날짜와 이전 값, 실계좌에 미치는 영향을 함께 적습니다. 키·계좌번호·웹훅·내부 설계 문서 경로는 쓰지 않습니다.

## 절차

로컬 저장소는 원본 Vibe-Trading의 얕은 복제이고 운영 파일도 함께 들고 있습니다. 그래서 로컬 브랜치를 그대로 올리지 않고, `scripts/release.py`가 HEAD에서 공개용 스냅숏을 만들어 공개 저장소 `main` 위에 커밋합니다. 작업 트리와 로컬 브랜치는 건드리지 않습니다.

1. 추천 번호와 이유를 사용자에게 보여 주고, 사용자가 고른 번호로 `VERSION`을 바꿉니다. README 배지와 `landing/src/content.ts`의 `VERSION`도 맞춥니다.
2. `release/YYYY-MM-DD-v<버전>-<제목>.md`를 쓰고 위 표에 한 줄을 더합니다.
3. 점검합니다.

   ```bash
   PYTHONPATH=agent:scripts .venv/bin/python scripts/release.py --check
   ```

4. 로컬에서 커밋합니다. `.gitignore`가 키(`.env*`, `.envrc`), 런타임 원장(`~/.vibe-trading`), 내부 문서(`docs/` 공개 가이드 밖)를 막고, `tools/ci_private_files_gate.py`가 색인을 한 번 더 검사합니다.
5. 공개 스냅숏을 미리 봅니다. 뺀 파일 수만 나오고 아무것도 올리지 않습니다.

   ```bash
   PYTHONPATH=agent:scripts .venv/bin/python scripts/release.py --dry-run
   ```

6. 사용자가 버전과 발행을 확인한 뒤 발행합니다. 공개 `main` 푸시 → `v<버전>` 태그 → GitHub Release 만들기(이미 있으면 노트 갱신) 순서입니다.

   ```bash
   PYTHONPATH=agent:scripts .venv/bin/python scripts/release.py --publish
   ```

7. 랜딩이 바뀌었으면 다시 배포합니다. 빌드한 랜딩을 리포트 사이트(`~/.vibe-trading/report-pages/site/`) 루트에 덮어쓰고 `gh-pages`로 푸시합니다. `reports/`는 건드리지 않습니다.

   ```bash
   zsh landing/scripts/deploy.sh
   ```

## 공개 스냅숏에서 빠지는 것

| 경로 | 이유 |
| --- | --- |
| `state/` | 운영 설정 정리본(계좌번호·해시는 이미 지웠지만 운영 값이라 공개하지 않음) |
| `ops/` | 예약 작업 지시문 |
| `.github/` | 원본 Vibe-Trading 전용 CI·Dependabot. 공개 저장소에서 돌면 원본의 빌드·배포를 시도합니다 |
| `docs/`의 공개 가이드 밖 문서 | 로컬 `.gitignore`가 막아 애초에 커밋되지 않습니다. 비공개 문서 검사가 스냅숏에서 다시 확인합니다 |

토큰은 `.env`나 상위 `.envrc`의 `GH_TOKEN`을 `report_pages.settings()`가 읽습니다. git에는 `GIT_CONFIG_*` 환경변수로만, GitHub API에는 요청 헤더로만 넘기며 화면이나 기록에 남기지 않습니다.
