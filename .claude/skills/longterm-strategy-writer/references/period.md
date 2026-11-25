# 주간 · 월간 리포트

같은 템플릿(12개 절)을 쓴다. 다른 점만 적는다.

| 항목 | 주간 | 월간 |
|---|---|---|
| key | `2026-W47` (ISO 주) | `2026-11` |
| 시점 | 금요일 장 마감 뒤 | 매월 1일(지난달) |
| 제목 | `# 장기투자 주간 전략 리포트 · 2026-W47` (`longterm_template.title`) | `# 장기투자 월간 전략 리포트 · 2026-11` |

## 절차

1. 자료를 받는다: `scripts/ro longterm_report.py --day <key> --data`. 사실 표(보유·비중·순자산·타점)는 기간 끝이 아니라 **작성하는 지금**의 기록이다. 기간 성과는 아래 3단계처럼 `nav`·`decisions` 기록에서 직접 읽어 쓴다.
2. 리포트는 key로 만든다. 분석 파일을 쓰고 `scripts/ro longterm_report.py --day <key> --fill .scratch/lt-fill-<key>.json`을 실행한다. 제목이 key의 종류(주간·월간)로 나온다. 사실 표 미리보기는 `--day <key> --report --blank`다.
3. 기간 숫자는 기록에서 직접 읽는다(읽기 전용).
   ```bash
   scripts/ro sql ~/.vibe-trading/nhplug/vibe-longterm/longterm.sqlite3 "SELECT day, book, value FROM nav WHERE day BETWEEN '<시작>' AND '<끝>' ORDER BY day"
   scripts/ro sql ~/.vibe-trading/nhplug/vibe-longterm/longterm.sqlite3 "SELECT time, kind, regime, reason FROM decisions WHERE day BETWEEN '<시작>' AND '<끝>' ORDER BY time"
   ```
   그 기간의 일일 리포트(`R/strategy-YYYY-MM-DD.json`)를 모두 읽는다.
4. 리서치는 일일 절차 4단계와 같이 한다. 그 기간의 지수 흐름, 다음 주(다음 달) 일정, 테마 뉴스를 각각 한 번씩 본다. 기간 리포트도 외부 근거와 출처 링크가 있어야 한다. 예약 실행에서는 웹 검색 없이 자료 `news`와 그 기간 일일 리포트의 출처 링크를 쓴다.
5. 절별로 일일 리포트와 다르게 쓰는 것:
   - 1절: 기간 성과(실제·기본 그림자·자동 그림자 순자산 변화)와 다음 기간 방향
   - 2절: 기간 동안 국면이 어떻게 바뀌었고 왜 그랬는지
   - 3절: 기간 동안 각 거장 모듈이 실제로 무엇을 했는지(결정 기록), 다음 기간에 켜거나 끌 만한 모듈(제안)
   - 5절(미국 종목 아이디어): **주간**은 `references/ideas.md`의 렌즈 절차로 이번 주 코드 스크린 상위 후보 중 최대 10종목을 공시까지 확인해 논리 카드(`ideas`)로 싣는다. 지난 카드의 재점검일이 지났으면 다시 본다. **월간**은 `references/ideas.md`의 추천 성적표(1·3·6·12개월, QQQ·SPY 대비)를 쓰고 새 카드는 주간 리포트에서 가져온다
   - 6절: 기간 중 닿은 타점과 그때 일어난 일
   - 8절(익절·리밸런싱 의견): 기간 동안 엔진이 실제로 판 것(결정 기록)과 이유, 적립 프로필 그림자(`dca_auto`)와 실제 비교
   - 10절: **기간 점검**. 일일 리포트들의 시나리오·제안 타점이 맞았는지 집계한다(맞음·틀림·미발생 건수와 대표 사례)
6. 분석 파일 형식과 검사는 일일 리포트와 같다(SKILL.md 7·8단계). `--fill`이 검사까지 하고, 따로 볼 때는 `scripts/ro longterm_report.py --day <key> --check`를 쓴다.
