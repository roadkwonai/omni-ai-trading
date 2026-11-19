---
name: report-html-publisher
description: >-
  Make, fix, preview, publish and resend the Omni AI Trading report HTML pages (매매일지, 기간 매매일지, 장전 분석, 장 마감 개선안,
  낙폭과대 주간 감사, 장기투자 전략 리포트) that go to GitHub Pages and to Discord as a link. Use this whenever someone asks
  to turn a report into HTML, regenerate or resend a report, add a new report kind or HTML template, change how the report
  pages look, check why a report link was not sent, set up or switch on GitHub Pages publishing, or says "리포트 HTML",
  "리포트 다시 보내", "디스코드 링크", "Pages 배포", "리포트 디자인", "리포트가 넓은 화면에서 답답해", "모바일에서 리포트가
  잘려", "다크 모드·테마 버튼", "콜아웃·차트·흐름도 넣기" — even when they do not name this skill.
---

# 리포트 HTML 발행

리포트 본문(.md, 일부는 .json)은 각 작성 스킬이 쓰고, 이 스킬은 그것을 **같은 틀의 HTML 페이지**로 만들어
GitHub Pages에 올리고 Discord에 링크를 보내는 일을 맡는다. 코드가 틀을 강제하므로, 일관된 품질은 손으로 HTML을
쓰는 데서가 아니라 `scripts/report_pages.py`와 `scripts/report_templates/`를 거치는 데서 나온다. HTML을 직접
손으로 고치지 않는다(다음 생성 때 덮어써지고 다른 리포트와 어긋난다).

## 구조 한눈에

| 단계 | 하는 일 | 어디 |
|---|---|---|
| 원본 | 작성 스킬이 .md(+.json)를 씀 | `~/.vibe-trading/nhplug/shared-etf-paper/reviews/`, `.../vibe-longterm/reports/` |
| 생성 | 원본이 새로 생기거나 바뀌면 HTML·목록 갱신 | `report_pages.scan` → `~/.vibe-trading/report-pages/site/` |
| 게시 | 사이트 커밋, `REPORT_PAGES_PUSH=on`이면 푸시 | `report_pages.publish` (토큰: .env 또는 ../.envrc의 `GH_TOKEN`) |
| 발송 | 페이지가 200으로 열린 뒤 제목·요약·링크 | `report_pages.send` (15분 넘게 안 열리면 실패 기록) |
| 기록 | 상태와 실패 사유, 이력 | `~/.vibe-trading/nhplug/report-pages.sqlite3` |

디스코드 알림 서비스(launchd `discord-notify`, 30초)가 매 회차 `report_pages.cycle`을 부른다. 그래서 보통은
아무것도 안 해도 된다. Pages가 꺼져 있으면(`REPORT_PAGES_PUSH`≠on 또는 저장소 미설정) 리포트는 예전처럼
Discord에는 아무것도 보내지 않고(사용자 결정 2026-11-18: 본문 텍스트 발송 금지) HTML은 로컬에만 생긴다. 배포되면 쌓인 리포트가 링크로 나간다.

## 자주 하는 일

모든 명령은 저장소 루트에서 `PYTHONPATH=agent:scripts .venv/bin/python scripts/report_pages.py ...`로 실행한다.

- **상태 보기**: `--list` (리포트별 생성·게시·발송 상태) · `--settings` (Pages 설정, 토큰은 설정 여부만)
- **샘플·템플릿 미리보기**: `--preview <종류> <md 파일> [--out 폴더]` — 기록·게시·발송 없이 그 폴더에만 그리고 검사까지 한다.
  샘플 리포트를 실제 원본 폴더(`~/.vibe-trading/nhplug/.../reviews`, `.../reports`)에 두면 안 된다. 30초 안에 알림 서비스가
  그것을 진짜 리포트로 만들어 Discord에 보낸다. 샘플은 언제나 임시 폴더에 쓰고 `--preview`로 본다.
- **한 리포트 다시 만들기**: `--build journal 2026-11-17 --force`
- **전부 훑기**: `--scan` (바뀐 것만 다시 씀) · **게시**: `--publish` · **한 번에**: `--cycle`
- **다시 보내기**: `--send journal:2026-11-17` (페이지 확인 생략은 `--no-live-check`). Discord로 실제 메시지가 나가므로
  사용자가 다시 보내 달라고 했을 때만 실행한다.
- **발송 테스트**: `--test-send` — 채널마다 최신 리포트로 테스트 메시지 하나(배포 전이면 링크가 열리지 않는다)
- **검사**: `--check <html 파일>` — lang·viewport·description·noindex·h1 하나·건너뛰기 링크·계좌번호/해시/토큰 없음
- **화면 확인**: `.claude/launch.json`의 `report-pages` 서버(포트 8765, 사이트 폴더)를 열고 1920·1440·768·390px(넓은 화면 작업이면 2560도)와 다크 모드를 본다. 테마 버튼으로 라이트·다크를 바꿔 본다.
  미리보기 폴더는 `.venv/bin/python -m http.server 8766 --directory <폴더>`로 열면 된다(상대 경로 CSS 때문에 파일 단독으로 열면 스타일이 빠진다).
- 대시보드 **운영 → 리포트 발행**에서 같은 일(목록 검색·연/월/일 필터·미리보기·다시 생성/게시/발송·발송 이력)을 한다.

## "링크가 안 왔다"를 진단하는 순서

1. `--list`에서 그 리포트의 세 단계(생성·게시·발송) 중 어디가 멈췄는지 본다.
2. 기록의 실패 사유(`gen_error`·`publish_error`·`send_error`)와 이력(`report_pages.history()` 또는 대시보드 '발송·작업 이력')을 읽는다.
   사유는 기록에 그대로 남으므로 추측하지 말고 그대로 전한다.
3. 자동으로 풀리는 것과 사람이 할 일을 나눠 말한다.

| 상태·사유 | 뜻 | 자동 처리 | 사람이 할 일 |
|---|---|---|---|
| 생성 `검사 실패: …`·예외 | 원본이나 틀 문제 | 원본이 바뀔 때만 다시 시도(30초마다 반복하지 않음) | 원본·틀을 고치고 `--build … --force` |
| 게시 `설정 없음` | `REPORT_PAGES_REPO`·토큰 없음 | 설정되면 다음 회차에 게시 | .env 설정(기본 omni-ai-trading) |
| 게시 `푸시 꺼짐` | 준비만 된 상태, 발송 대기(텍스트로 보내지 않음) | `REPORT_PAGES_PUSH=on`이 되면 푸시 | 사용자가 허락하면 on |
| 게시 `실패`·`잠시 뒤 재시도` | 푸시 거절·토큰·네트워크 | 10분 간격 재시도, 텍스트로 대신 보내지 않음 | 사유의 HTTP 코드·git 메시지 확인 |
| `저장소 만들기 실패(HTTP 403…)` | 토큰에 저장소 생성 권한 없음 | — | 토큰 권한(classic repo 또는 Administration 쓰기) |
| 발송 `게시 확인 중` | 페이지가 아직 안 열림 | 열릴 때까지 기다림(15분 넘으면 사유에 표시, 텍스트로 보내지 않음) | Pages 소스가 gh-pages인지 |
| 발송 `Discord 연결 실패 또는 429` | 일시 오류 | 10분 뒤 자동 재시도(최대 5회) | — |
| 발송 `…WEBHOOK… 미설정` | 웹훅 없음 | 웹훅이 생기면 자동 재시도 | .env 웹훅 설정 |
| 발송 `Discord 거절(4xx…)` | 웹훅 주소가 틀렸거나 지워짐 | 없음 | 웹훅 고친 뒤 `discord_notify.py --test`, 그다음 `--send` |

이미 보낸 리포트(`발송됨`·`텍스트로 발송됨`)는 수동 재발송이 실패해도 상태가 그대로다. 그러니 "다시 보내 줘"에 실패하면
"이미 한 번 갔다"는 사실과 이번 실패 사유를 함께 말한다.

## 새 리포트 종류 더하기

1. `scripts/report_pages.py`의 `REPORTS`에 한 줄: 키, 이름, 아이콘, 채널(realtime|longterm), 폴더, 파일명 정규식
   (`(?P<key>…)` 필수), 일정, 만드는 작업, 템플릿.
2. 특별한 머리 영역이 필요하면 `scripts/report_templates/<key>.html`을 `report.html`에서 확장해 `highlights` 블록만
   채운다(예: `journal.html`의 핵심 이슈, `longterm-strategy.html`의 시나리오). 본문은 언제나 .md 렌더링이다.
3. `scripts/test_report_pages.py`에 원본 하나로 생성·검사 테스트를 더한다(`runtime()`·`setup()` 도우미가 임시 폴더를 만든다).
   샘플을 `--preview`로 그려 1920·1440·768·390px와 다크 모드를 브라우저로 확인한다(가로 스크롤 없음, 표는 표 안에서만 스크롤).
4. 그 리포트를 쓰는 예약 작업이 없으면 사용자에게 알린다. 원본 파일이 생겨야 페이지와 링크가 생긴다.
5. 대시보드 '발행 대상·일정' 표와 Discord 발송은 `REPORTS`에서 바로 읽으므로 따로 고칠 것이 없다.

## 디자인을 바꿀 때

템플릿·CSS 디자인 작업은 **`design-taste-frontend` 스킬**을 불러 그 규칙으로 하고, `references/design.md`의 검수 명령과
면제 표대로 확인한다. 리포트 원본에 쓰는 콜아웃·형광펜·차트·흐름도 문법은 `references/report-syntax.md`.

틀이 어디서 무엇을 하는지(고칠 곳을 바로 찾기 위한 지도):

| 파일 | 맡는 것 |
|---|---|
| `scripts/report_templates/_base.html` | head(메타·noindex·Pretendard CDN·favicon), 건너뛰기 링크, 고정 헤더(로고·전체 리포트·테마 버튼), 바닥 |
| `report.html` | 머리(꼬리표·h1·날짜·요약), 넓은 화면 왼쪽 목차, 1023px 이하 펼침 목차, 하이라이트 블록, 본문 카드 |
| `journal.html` · `longterm-strategy.html` | 종류별 하이라이트(핵심 이슈·시나리오·다음에 지켜볼 것) |
| `index.html` · `index.js` | 보관함 `reports/index.html`(검색·종류·연·월·날짜, 마스코트). 사이트 루트는 랜딩페이지(`landing/`) |
| `report.css` | 토큰·라이트/다크, 반응형 폭·글자, 콜아웃·표·막대·선 그래프·흐름도 |
| `theme.js` | 테마 버튼(시스템→라이트→다크, 기기별 기억), 흐름도 iframe 테마 맞춤, 목차 현재 위치 |
| `scripts/report_figures.py` | 콜아웃·형광펜·자동 등락 막대·```chart(HTML 막대, 반응형 선)·대시보드용 `for_streamlit` |
| `scripts/report_pages.py` `blocks`·`archify` | ```archify-<종류> 흐름도를 옆 파일로 그려 iframe으로 넣음(애니메이션은 끔) |

요점(자세한 값은 `references/design.md`):
- 색은 DESIGN.md의 **역할 토큰**만 쓴다(브랜드 주황은 꼬리표·형광펜·초점에만). 국내 관례대로 **이익·상승 빨강 / 손실·하락 파랑**.
- 글꼴은 **Pretendard 동적 서브셋 CDN**이다. 2MB 자체 호스팅은 성능 검사로 뺐다.
- **폭은 화면을 따라간다**(최대 2040px, 여백 16~96px). 본문 글자는 16→19px로 커지지만 문단은 약 56자에서 끊는다. 표·차트는 넓어진 폭을 쓴다.
- **휴대폰**에서는 펼침 목차를 쓰고, 표는 표 안에서 스크롤(그림자 신호)한다. 막대·선 그래프는 HTML 글자라 잘리지 않는다. 가로 스크롤은 없어야 한다.
- 카드·버튼·반경은 한 벌이다. CSS는 `report.css` 한 곳에서 고친다.
- 고치면 `--preview`로 그려 1920·1440·768·390px(필요하면 2560)과 라이트·다크를 본 뒤 `--build … --force`(또는 대시보드 '다시 생성')로 기존 페이지를 다시 만든다.
- **디자인 변경은 `consistency.mjs`(1920·1440·768·390)를 실제로 돌려 결과를 보고해야 끝난다.** 스크린샷만으로는 안 된다. 스크린샷은 눈에 띈 곳만 보여 주고, 검사는 넘침·잘림·제목 단계·탭 크기·대비를 폭마다 잰다(3~4분 걸린다). `references/design.md`의 면제 표에 없는 FAIL은 고치고, 남긴 것은 이유와 함께 적는다.
- 브라우저가 옛 CSS를 붙들고 있으면 주소에 `?r=2`처럼 붙여 다시 연다. 페이지는 `report.css?v=<내용 해시>`로 바뀐 CSS를 알린다.

## 지키는 것

- 계좌번호·계좌 해시·토큰·웹훅 주소를 페이지·기록·화면에 남기지 않는다. 생성기가 제목·목차·본문·JSON 필드의
  10자리 이상 숫자(하이픈 형식 포함)와 16자 이상 hex를 가리고, 검사가 남은 것을 막는다. 원본에 그런 값이 있으면 원본을 고친다.
- Pages는 공개 주소다. 저장소를 새로 만들거나 `REPORT_PAGES_PUSH=on`으로 바꾸는 일, 처음 푸시는 사용자가
  허락했을 때만 한다. Discord 재발송도 사용자가 요청했을 때만 한다.
- 원본 리포트(.md/.json)는 작성 스킬의 산출물이다. 이 스킬은 그 내용을 고치지 않고 모양만 맡는다.
