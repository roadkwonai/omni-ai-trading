---
name: premarket-analysis
description: 국내 장전 상세 분석(reviews/premarket-YYYY-MM-DD.md)을 공통 템플릿으로 쓰고 검사한다. "장전 분석", "오늘 장 전망", "개장 전 시황", "장전 시황 정리", "프리마켓 분석" 요청에 쓴다. 매매 주문, 전략·설정 변경, 코드 수정에는 쓰지 않는다.
---

저장소 루트 `.agents/skills/premarket-analysis/SKILL.md`를 읽고 따른다. Codex와 Claude가 같은 절차·템플릿을 쓰도록 그 문서와 `references/template.md`가 공통 원본이고, 절 구성의 원본은 `scripts/premarket_template.py`다.

## HTML 가독성 문법 (2026-11-17)
이 리포트는 HTML 페이지(GitHub Pages)로도 발행된다. 템플릿 절 제목·순서는 그대로 두고, 필요한 곳에만 다음을 쓴다(자세한 규칙: `.claude/skills/report-html-publisher/references/report-syntax.md`).
- 절 결론은 `> [!SUMMARY]`, 꼭 알아야 할 사실은 `> [!IMPORTANT]`, 조건부 위험은 `> [!WARNING]`, 손실·오류는 `> [!CAUTION]` 콜아웃으로(한 절에 하나까지).
- 꼭 기억할 한 구절은 `==형광펜==`(리포트 전체 2~4개), 핵심 숫자·종목은 `**굵게**`.
- 등락·손익 표는 `+`/`−` 부호와 단위를 붙이면 HTML에서 막대가 자동으로 붙는다.
- 밤사이 시장 표는 등락 열에 부호를 붙여 자동 막대가 나오게 하고, 오늘의 대응 분기는 필요할 때만 `archify-workflow`로 그린다.
