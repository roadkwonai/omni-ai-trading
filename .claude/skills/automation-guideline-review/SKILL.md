---
name: automation-guideline-review
description: Codex 자동화·예약 작업 지침(prompt)을 리뷰·검수·테스트해 참조 검증표·리뷰 결과를 내고, 상시 규칙은 프로젝트 스킬로, 반복 수집은 읽기 전용 스크립트로, 일회성 점검은 날짜별 확인 목록으로 나눈 뒤 붙여 넣을 짧은 개정 지침을 만든다. "자동화 지침 리뷰", "예약 작업 프롬프트 점검", "감사 지침 보완", "automation.toml 지침 고쳐줘" 요청이나 길어진 자동화 지침을 정리할 때 쓴다. 자동화 일정·상태 변경, 주문·설정 변경, 자동화가 하는 일 자체에는 쓰지 않는다.
---

저장소 루트 `.agents/skills/automation-guideline-review/SKILL.md`를 읽고 따른다. Codex와 Claude가 같은 절차·점검 목록·보고 템플릿·개정 지침 틀(`references/`)과 반영 스크립트(`scripts/apply_prompt.py`)를 쓰도록 그 폴더가 공통 원본이다.
