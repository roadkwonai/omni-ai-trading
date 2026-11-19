---
name: intraday-audit
description: 평일 장중 Jev 공유 모의·vibe 가상·실계좌 미러의 실행·시세·체결·진입 차단·이벤트 분석을 읽기 전용으로 감사해 reviews/operations-audit-YYYY-MM-DD.md에 중복 없이 누적한다. "장중 감사", "실행기 상태 점검", "왜 안 샀는지 감사" 요청에 쓴다. 주문·설정·스위치·서비스 변경에는 쓰지 않는다.
---

저장소 루트 `.agents/skills/intraday-audit/SKILL.md`를 읽고 따른다. Codex 자동화 "장중 매매·차단 감사"와 Claude가 같은 절차·기록 형식·수집기(`scripts/intraday_audit.py`)를 쓰도록 그 문서와 `references/checks.md`가 공통 원본이다.
