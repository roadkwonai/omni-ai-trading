"""The long-term strategy report template (docs/longterm-strategy-plan.md 5-2): section headings and order for the daily,
weekly and monthly reports, and the checks every written report must pass. Change the template here (with the
user's approval), not in a single report."""
from __future__ import annotations

import re

# 2026-11-18 (user approval, docs/longterm-master-dca-ideas-plan.md 5-1): 미국 종목 아이디어 and 익절·리밸런싱 의견 added
SECTIONS = ('오늘의 결론', '거시 시황', '거장별 시각', '종목별 분석', '미국 종목 아이디어', '타점 지도', '계좌 계획',
            '익절·리밸런싱 의견', '시나리오 대응', '지난 계획 점검', '위험·주의', '출처')
PLACEHOLDER = '(작성)'  # left in the blank report where analysis goes; a finished report has none
KINDS = {'day': r'\d{4}-\d{2}-\d{2}', 'week': r'\d{4}-W\d{2}', 'month': r'\d{4}-\d{2}'}
LABEL = {'day': '일일', 'week': '주간', 'month': '월간'}


def kind_of(key):
    for kind, pattern in KINDS.items():
        if re.fullmatch(pattern, key):
            return kind
    raise ValueError(f'알 수 없는 리포트 키입니다: {key}')


def title(key):
    return f'# 장기투자 {LABEL[kind_of(key)]} 전략 리포트 · {key}'


def check(text, key):
    """Korean problems with a written report ([] when it passes): title, every section in order, no blank left."""
    return check_sections(text, title(key), SECTIONS, min_body=20)


def check_sections(text, head, sections, min_body):
    """The checks every long-term document shares (the strategy report here, the review and audit in longterm_audit): the
    title line, every section in order (numbered or not), no placeholder left and no section shorter than min_body."""
    lines = text.splitlines()
    problems = [] if lines and lines[0].strip() == head else [f'첫 줄이 제목 "{head}"이 아닙니다.']
    found = [re.sub(r'^\d+\.\s*', '', line[3:].strip()) for line in lines if line.startswith('## ')]
    if missing := [s for s in sections if s not in found]:
        problems.append('빠진 절: ' + ', '.join(missing))
    elif [s for s in found if s in sections] != list(sections):
        problems.append('절 순서가 템플릿과 다릅니다.')
    if PLACEHOLDER in text:
        problems.append(f'채우지 않은 자리 {text.count(PLACEHOLDER)}곳이 남았습니다.')
    for section in sections:
        body = section_body(text, section)
        if body is not None and len(body.strip()) < min_body:
            problems.append(f'"{section}" 절이 비었습니다.')
    return problems


def section_body(text, section):
    """The text under one section heading (None when the heading is absent)."""
    match = re.search(rf'^## (?:\d+\.\s*)?{re.escape(section)}\s*$(.*?)(?=^## |\Z)', text, re.M | re.S)
    return match.group(1) if match else None
