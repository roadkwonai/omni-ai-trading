---
name: trading-journal-writer
description: 이 저장소(Omni AI Trading)의 매매일지를 scripts/journal_template.py 표준 템플릿으로 같은 품질로 쓰는 절차 — 하루 매매일지(journal-YYYY-MM-DD)와 월·분기·반기·연간 기간 매매일지(데이터 수집, 관점 JSON, 매매관점 리포트 .md, 템플릿 검사, 일지 DB 병합). "매매일지 써줘", "오늘/어제 매매 복기", "이번 달·분기·반기·올해 매매 결산·회고", "매매관점 리포트 다시 써줘", 평일 16:40 매매일지·매월 1일 기간 매매일지 예약 작업에서는 템플릿을 말하지 않아도 반드시 쓴다. 주문·실매매, 백테스트, 전략·설정 변경, 매매일지 기능(trading_journal.py·대시보드) 코드 수정에는 쓰지 않는다.
---

# 매매일지 쓰기 (일 · 월 · 분기 · 반기 · 년)

먼저 저장소 루트의 `AGENTS.md`를 따른다. 글은 한국어, 시각은 Asia/Seoul이다.
- - 명령은 저장소 루트에서 읽기 전용 실행기 `scripts/ro`로 하나씩 실행한다(`&&`·`>` 없이, 파일 출력은 `scripts/ro --out .scratch/<파일> …`; 예약 작업이 승인 없이 돌도록 2026-11-18).
- 절 제목과 순서는 `scripts/journal_template.py`(`DAY`, `PERIOD`)가 정한다. 그래야 날짜·기간끼리 비교되고 엔진(`trading_journal.brief`)과 대시보드가 읽는다. 템플릿을 바꿀 일이 생기면 글에서 바꾸지 말고, 사용자 승인 뒤 그 파일과 테스트에서 바꾼다.

## 만드는 것

일지 하나마다 `~/.vibe-trading/nhplug/shared-etf-paper/reviews/`에 두 파일을 쓴다(아래 `R/`).
- `journal-<key>.json`: 관점 데이터. 엔진과 대시보드가 읽는다.
- `journal-<key>.md`: 매매관점 리포트. 자동 틀 위에 분석을 채운 글이다.

key 형식:

| 종류 | key 예 | 절차 | 참고 문서 |
|---|---|---|---|
| 하루 | `2026-11-13` | 아래 "하루 매매일지" | `references/day.md` |
| 월 | `2026-11` | 아래 "기간 매매일지" | `references/period.md` |
| 분기 | `2026-Q4` | 〃 | 〃 |
| 반기 | `2026-H2` | 〃 | 〃 |
| 년 | `2026` | 〃 | 〃 |

임시 파일을 따로 만들지 말고 자동 틀을 `R/journal-<key>.md`에 바로 받아 고친다. 그 파일 이름이라야 검사기가 하루인지 기간인지 안다.

## 하루 매매일지

1. **상태를 본다.** `scripts/ro trading_journal.py --day D --status`
   - `trading_day`가 `false`(주말·KRX 휴장)면 "휴장"만 보고하고 끝낸다.
   - `note`가 `holiday_table_unavailable…`이고 `fills`가 0이어도 휴장으로 보고 끝낸다.
2. **데이터를 확보한다.**
   - `collected`가 `false`: `scripts/ro trading_journal.py --day D`로 모은다.
   - 이미 모았는데 지난 날짜라 다시 모아야 하면 `--day D --rebuild`를 쓴다. 그날만 잡히는 프로그램 매매·시장 맥락·촘촘한 분봉을 지킨다.
   - 지난 날짜에 `--day D`를 다시 쓰지 않는다. 데이터 없이 `--merge`부터 하지 않는다.
3. **이미 쓴 일지인지 본다.** `written`이 `true`면 기존 json·md를 먼저 읽는다. 바뀐 데이터와 요청받은 부분만 고치고, 근거가 유효한 `sources`·`top_issues` 링크는 지킨다.
   새로 쓸 때는 틀을 받는다: `scripts/ro trading_journal.py --day D --report --blank > R/journal-D.md`
   `--blank`는 이미 병합된 분석을 빼고 자리표시만 있는 틀을 준다. "처음부터 다시 써줘"일 때도 이것을 쓴다.
4. **리서치하고 분석한다.** 레코드는 `trading_journal.load('D')`로 읽는다. 매매 복기(시장·진입·비중·청산의 의사결정)는 `danswer-market-trading` 스킬의 `cheatsheet.md`와 7장 복기 기준을 함께 본다(사용자 2026-11-18). 절별로 무엇을 더할지는 `references/day.md`를 따른다. 그날의 장 마감 개선안은 레코드의 `system.improvement_report`에 있다.
5. **금요일이면** `scripts/ro trading_journal.py --day D --week`로 이번 주 수치를 받는다. 그리고 `retrospective`에 이번 주 흐름 한 항목을 더한다.
   형식 예: "이번 주(11/09~11/13) 모의 +1,819원, 매매일 3·이익일 1, 최고 11/13 +9,996원·최저 11/12 −8,152원, 손실은 005930에 몰림"
6. `R/journal-D.json`을 쓴다(스키마는 `references/day.md`).
7. `R/journal-D.md`를 완성한다. 모든 `##` 절을 순서대로 두고, `_작성 전 — …_` 자리표시를 모두 실제 분석으로 바꾼다.
8. **검사한다.** `scripts/ro journal_template.py --check R/journal-D.md`
   "템플릿과 일치합니다."가 나올 때까지 고친다. 절 제목·순서·요약·출처 링크와 남은 자리표시를 함께 본다.
9. **병합한다.** `scripts/ro trading_journal.py --day D --merge`
   - 일지 DB, 그 날이 속한 월·분기·반기·년 일지, 대시보드, 엔진 참고 입력에 들어간다.
   - 두 파일 권한은 600으로 맞춰진다.
   - 여러 번 실행해도 안전하다.

## 기간 매매일지 (월 · 분기 · 반기 · 년)

1. **쓸 기간을 정한다.** 사용자가 기간을 말하면 그것을 쓴다.
   예약 작업(매월 1일)이면 `scripts/ro journal_template.py --due`가 출력하는 줄(`kind key`)을 순서대로 쓴다.
   여러 기간이면 월 → 분기 → 반기 → 년 순서로 쓴다. 긴 기간은 그 안의 짧은 기간 일지를 읽고 쓴다.
2. **기간 일지를 모은다.** `scripts/ro trading_journal.py --period <kind> --key <key>`
   출력의 `days`가 0이면 "기록 없음"으로 보고하고, 그 기간의 json·md는 쓰지 않는다.
3. **틀을 받는다.** 이미 쓴 일지면 하루 3단계처럼 기존 파일을 먼저 읽는다.
   새로 쓸 때는 `scripts/ro trading_journal.py --period <kind> --key <key> --report --blank > R/journal-<key>.md`
4. **리서치하고 분석한다.** 레코드는 `trading_journal.load_period('<key>')`로 읽는다. 절별 안내는 `references/period.md`를 따른다.
   하루 일지의 판단을 다시 하지 말고 기간에 걸쳐 반복된 패턴을 찾는다.
5. `R/journal-<key>.json`을 쓰고 `R/journal-<key>.md`를 완성한다. 자리표시는 남기지 않는다.
6. **검사한다.** `scripts/ro journal_template.py --check R/journal-<key>.md`
7. **병합한다.** `scripts/ro trading_journal.py --period <kind> --key <key> --merge`

## 쓰는 법

- 절마다 첫 문장에 결론을 쓰고, 근거는 짧은 글머리표로 단다. 문단은 3문장 이하, 표는 7열 이하로 한다.
- 수치는 레코드나 링크된 출처에서만 가져온다.
  - 출처 없는 수치는 쓰지 않는다.
  - 사실과 의견을 구분한다("~로 보인다", "가설").
  - 없는 데이터를 지어내지 않는다. 비어 있으면 비어 있다고 쓴다.
- 자동 틀의 `> ` 요약 줄과 `_작성 전 — …_` 자리표시는 모두 분석으로 바꾼다. 검사기는 남은 자리표시를 센다.
- 근거 기사·리포트는 `[제목](URL)`로 링크한다.
  - 레코드의 issues·engine_news·analysts link를 그대로 써도 된다. Google News 링크도 된다.
  - 웹 검색으로 원문을 찾았으면 원문 URL을 더 우선한다. 검색으로 못 찾으면 레코드 링크만으로 쓰고, 헤드라인만으로 사실을 단정하지 않는다("~라고 보도됐다").
  - 링크는 `http(s)://`만 쓴다.
- 별점 기준(JSON의 `stars`는 정수 1~5):
  - ★5: 시장 전체를 움직였거나 매매 결과를 바꾼 사건
  - ★4: 그날·그 기간에 큰 영향
  - ★3: 업종·종목 영향
  - ★1~2: 참고
- 금액은 `+12,345원`, 비율은 `+1.23%` 형식이다. "체결가 기준, 수수료·세금 미반영"을 한 번 밝힌다.

## 지켜야 할 것

- 읽기 전용이다. 주문, 설정 변경, 서비스 조작, 코드 수정, 원장·이벤트 수정과 삭제를 하지 않는다.
- 쓰는 파일은 `R/journal-<key>.json`·`.md`뿐이다. 위 명령으로 일지 DB를 갱신하는 것은 허용된다.
- 계좌번호와 인증키를 출력하지 않는다.
- `engine_notes`는 판단 참고용 맥락이다. 매매 규칙이나 숫자 지시를 쓰지 않는다.
  - 쓰지 않는 예: "X를 사라", "손절을 3%로"
  - 쓰는 예: "전쟁 확전 뉴스가 나온 날 지수 ETF 낙폭이 컸다"
- 전략 수치를 바꾸자는 제안은 `strategy_improvements`에 근거와 확인 방법을 붙여 적는다. 적용은 사용자가 승인할 때만 한다.

## 마지막 보고

짧게 쓴다.
- 한 줄 요약
- 계좌별 실현손익
- 보완 제안 수
- 검사 결과
- 파일 경로
- 대시보드에서 보는 곳: '매매일지' 탭의 일 보기. 기간 일지는 월·분기·반기·년 보기의 '기간 매매일지 리포트' 탭이다.

## HTML 가독성 문법 (2026-11-17)
이 리포트는 HTML 페이지(GitHub Pages)로도 발행된다. 템플릿 절 제목·순서는 그대로 두고, 필요한 곳에만 다음을 쓴다(자세한 규칙: `.claude/skills/report-html-publisher/references/report-syntax.md`).
- 절 결론은 `> [!SUMMARY]`, 꼭 알아야 할 사실은 `> [!IMPORTANT]`, 조건부 위험은 `> [!WARNING]`, 손실·오류는 `> [!CAUTION]` 콜아웃으로(한 절에 하나까지).
- 꼭 기억할 한 구절은 `==형광펜==`(리포트 전체 2~4개), 핵심 숫자·종목은 `**굵게**`.
- 등락·손익 표는 `+`/`−` 부호와 단위를 붙이면 HTML에서 막대가 자동으로 붙는다.
- 하루 매매 흐름(진입→추가→청산)이 핵심인 날은 `archify-workflow` 흐름도 하나, 기간 일지의 손익 추이는 ```chart 선 그래프 하나를 넣을 수 있다.
