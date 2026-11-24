"""Pre-startup check sheet: the report-template way of working used outside trading (docs/ai-search.md 창업 준비 점검).

Usage: scripts/startup_template.py --blank [YYYY-MM-DD]   print an empty sheet
       scripts/startup_template.py --check FILE           list what is missing (exit 1 when anything is)

It checks that the sheet is complete and consistent. It does not judge whether the business will work, calls no API,
saves nothing and reads no trading data."""
from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path

from longterm_template import PLACEHOLDER, check_sections, section_body

HEAD = '# 창업 준비 점검 · '
SECTIONS = ('지금 상황', '들은 말과 계획', '확인한 자료', '돈 나누기', '작게 시험하기', '멈출 기준', '아직 모르는 것', '사람의 결정')
RESULTS = ('확인됨', '다름', '모름')  # 모름 is a valid answer; the other two need a source
DECISIONS = ('진행', '보류', '중단')
HINTS = {
    '확인한 자료': f'| 주장 | 확인 결과 | 출처 |\n| --- | --- | --- |\n| {PLACEHOLDER} | 확인됨·다름·모름 중 하나 | 링크 |',
    '돈 나누기': f'| 항목 | 금액(만 원) |\n| --- | --- |\n| 생활비 | {PLACEHOLDER} |\n| 비상금 | {PLACEHOLDER} |\n| 창업 자금 | {PLACEHOLDER} |\n| 합계 | {PLACEHOLDER} |',
    '멈출 기준': f'{PLACEHOLDER} 날짜(YYYY-MM-DD)와 숫자를 함께 적으세요.',
    '사람의 결정': f'결정: {PLACEHOLDER} 진행·보류·중단 중 하나를 사람이 적으세요.',
}


def blank(day):
    return f'{HEAD}{day}\n\n' + '\n'.join(f'## {i}. {s}\n{HINTS.get(s, PLACEHOLDER)}\n' for i, s in enumerate(SECTIONS, 1))


def _rows(body):
    """Table rows as cell lists, without the header and the --- line."""
    rows = [[c.strip() for c in line.strip().strip('|').split('|')] for line in body.splitlines() if line.strip().startswith('|')]
    return [r for r in rows[1:] if not all(re.fullmatch(r':?-+:?', c) for c in r)]


def check(text):
    """Korean problems with a sheet ([] when it is complete)."""
    lines = text.splitlines()
    head = lines[0].strip() if lines else ''
    if not re.fullmatch(re.escape(HEAD) + r'\d{4}-\d{2}-\d{2}', head):
        head = HEAD + 'YYYY-MM-DD'
    problems = check_sections(text, head, SECTIONS, min_body=5)
    part = {s: section_body(text, s) or '' for s in SECTIONS}

    claims = _rows(part['확인한 자료'])
    if len(claims) < 3:
        problems.append('확인한 자료는 3줄 이상 적으세요.')
    for row in claims:
        claim, result, source = (row + ['', '', ''])[:3]
        if result not in RESULTS:
            problems.append(f'확인 결과는 {"·".join(RESULTS)} 중 하나로 적으세요: {claim}')
        elif result != '모름' and not re.search(r'https?://\S+', source):
            problems.append(f'출처 링크가 없습니다: {claim}')

    money = {}
    for row in _rows(part['돈 나누기']):
        amount = (row + [''])[1].replace(',', '')
        if not amount.isdecimal():
            problems.append(f'금액은 숫자로 적으세요: {row[0]}')
        money[row[0]] = int(amount) if amount.isdecimal() else 0
    for need in ('생활비', '비상금'):
        if not any(need in name and value > 0 for name, value in money.items()):
            problems.append(f'{need} 금액을 창업 자금과 따로 적으세요.')
    if money.get('합계') != sum(v for k, v in money.items() if k != '합계'):
        problems.append('돈 나누기의 합계가 항목을 더한 값과 다릅니다.')

    stop = part['멈출 기준']
    if not re.search(r'\d{4}-\d{2}-\d{2}', stop) or not re.search(r'\d', re.sub(r'\d{4}-\d{2}-\d{2}', '', stop)):
        problems.append('멈출 기준에 날짜와 숫자를 함께 적으세요.')
    if not re.search(rf'^결정: ({"|".join(DECISIONS)})(?=$|\s|\()', part['사람의 결정'], re.M):
        problems.append(f'사람의 결정은 "결정: {"·".join(DECISIONS)}" 중 하나로 적으세요.')
    if re.search(r'\b01\d-?\d{3,4}-?\d{4}\b|\b\d{6}-?[1-4]\d{6}\b|\b\d{3,6}-\d{2,6}-\d{4,}\b', text):
        problems.append('전화번호나 계좌번호 같은 개인정보를 지우세요.')
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--blank', nargs='?', const=date.today().isoformat(), metavar='YYYY-MM-DD')
    group.add_argument('--check', type=Path, metavar='FILE')
    args = parser.parse_args()
    if args.blank:
        print(blank(args.blank))
        return 0
    problems = check(args.check.read_text(encoding='utf-8'))
    print('\n'.join(f'- {p}' for p in problems) or '빠진 것이 없습니다. 사업이 잘된다는 뜻은 아닙니다.')
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
