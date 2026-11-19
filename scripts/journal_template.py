"""The 매매일지 template (user 2026-11-13): one definition of every journal's sections, for the daily journal and the
month / quarter / half / year journals. The automatic report (trading_journal.report_md / period_report_md), the
written analysis (16:40 daily task, period task, the trading-journal-writer skill) and the checker below all use it,
so every journal has the same sections in the same order and can be compared and read by the engines.

    python scripts/journal_template.py --check reviews/journal-2026-11-13.md        # day
    python scripts/journal_template.py --check reviews/journal-2026-Q4.md           # period (from the file name)
    python scripts/journal_template.py --due 2027-01-01                             # periods to write that day
"""
from __future__ import annotations

import argparse
import re
from datetime import date, timedelta

# (key, heading): the key names the perspective field that fills the section when there is one
DAY = (
    ('core_issues', '오늘의 핵심 이슈'),
    ('numbers', '1. 숫자로 보는 하루'),
    ('trades', '2. 매매 관점 — 무엇을 왜 사고 팔았나'),
    ('charts', '3. 종목별 차트로 본 매매'),
    ('market', '4. 시장과 수급'),
    ('news', '5. 이슈와 뉴스'),
    ('analysts', '6. 애널리스트'),
    ('system', '7. 시스템'),
    ('retrospective', '8. 복기·회고'),
    ('strategy_improvements', '9. 매매 전략 보완'),
    ('system_improvements', '10. 시스템 결함·개선'),
    ('engine_notes', '11. 다음 날 Jev·vibe 참고'),
    ('watch_next', '12. 다음 날 지켜볼 것'),
    ('sources', '출처'),
)
PERIOD = (
    ('numbers', '1. 숫자로 보는 기간'),
    ('flow', '2. 손익 흐름'),
    ('routes', '3. 진입 경로별 성적'),
    ('symbols', '4. 종목별 성적'),
    ('market', '5. 시장 흐름'),
    ('core_issues', '6. 기간의 핵심 이슈'),
    ('system', '7. 시스템'),
    ('retrospective', '8. 기간 복기·회고'),
    ('strategy_improvements', '9. 매매 전략 보완'),
    ('system_improvements', '10. 시스템 결함·개선'),
    ('engine_notes', '11. 다음 기간 Jev·vibe 참고'),
    ('watch_next', '12. 다음 기간 지켜볼 것'),
    ('sources', '출처'),
)
# the written perspective (reviews/journal-<day or period key>.json): the same keys for a day and a period
LISTS = ('retrospective', 'strategy_improvements', 'system_improvements', 'engine_notes', 'watch_next')
KINDS = {'month': '월', 'quarter': '분기', 'half': '반기', 'year': '연'}
WEEKDAYS = '월화수목금토일'
PLACEHOLDER = '_작성 전 — '
EMPTY_WRITTEN = PLACEHOLDER + '평일 16:40 매매일지 작성(기간은 기간 매매일지 작성)이 채웁니다._'
SUMMARY_PENDING = PLACEHOLDER + '자동 요약입니다. 분석 요약으로 바꿉니다._'  # the automatic '> ' line, also counted by check()


def headings(kind):
    return [h for _, h in (DAY if kind == 'day' else PERIOD)]


def title(kind, key):
    """'# 매매일지 · 2026-11-13 (금)' or '# 매매일지 · 2026년 4분기 (2026-10-01 ~ 2026-12-31)'."""
    if kind == 'day':
        return f"# 매매일지 · {key} ({WEEKDAYS[date.fromisoformat(key).weekday()]})"
    since, until = bounds(kind, key)
    return f'# 매매일지 · {label(kind, key)} ({since} ~ {until})'


# ── periods ─────────────────────────────────────────────────────────────────
def key_of(kind, day):
    """The period of `kind` holding `day`: 2026-11 / 2026-Q4 / 2026-H2 / 2026."""
    d = date.fromisoformat(str(day)[:10])
    return {'month': f'{d:%Y-%m}', 'quarter': f'{d.year}-Q{(d.month - 1) // 3 + 1}',
            'half': f"{d.year}-H{1 if d.month <= 6 else 2}", 'year': str(d.year)}[kind]


def kind_of(key):
    """The kind of a period key, or 'day' for YYYY-MM-DD."""
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', key):
        return 'day'
    for kind, pattern in (('month', r'\d{4}-\d{2}'), ('quarter', r'\d{4}-Q[1-4]'), ('half', r'\d{4}-H[12]'), ('year', r'\d{4}')):
        if re.fullmatch(pattern, key):
            return kind
    raise ValueError(f'not a journal key: {key}')


def bounds(kind, key):
    """(first day, last day) of the period, ISO dates."""
    year = int(key[:4])
    if kind == 'month':
        first_month, months = int(key[5:7]), 1
    elif kind == 'quarter':
        first_month, months = 3 * (int(key[-1]) - 1) + 1, 3
    elif kind == 'half':
        first_month, months = 6 * (int(key[-1]) - 1) + 1, 6
    else:
        first_month, months = 1, 12
    end_year, end_month = (year + 1, 1) if first_month + months > 12 else (year, first_month + months)
    return date(year, first_month, 1).isoformat(), (date(end_year, end_month, 1) - timedelta(days=1)).isoformat()


def label(kind, key):
    if kind == 'month':
        return f'{key[:4]}년 {int(key[5:7])}월'
    if kind == 'quarter':
        return f'{key[:4]}년 {key[-1]}분기'
    if kind == 'half':
        return f"{key[:4]}년 {'상' if key[-1] == '1' else '하'}반기"
    return f'{key}년'


def previous(kind, key):
    """The period just before `key` (for the period task: the one that has just closed)."""
    since, _ = bounds(kind, key)
    return key_of(kind, date.fromisoformat(since) - timedelta(days=1))


# ── the checker ─────────────────────────────────────────────────────────────
def check(text, kind, written=False):
    """Problems of a journal Markdown against the template: the title, the one-line summary, every section heading
    in order, and links in 출처. `written`: a finished analysis (reviews/journal-<key>.md), where the automatic
    skeleton's '작성 전' placeholders must all be filled. An empty list means the journal follows the template."""
    lines = text.splitlines()
    problems = []
    if not lines or not lines[0].startswith('# 매매일지 · '):
        problems.append("첫 줄이 '# 매매일지 · …' 제목이 아닙니다")
    if not any(line.startswith('> ') for line in lines[:6]):
        problems.append("제목 아래 '> 한 줄 요약'이 없습니다")
    found = [line[3:].strip() for line in lines if line.startswith('## ')]
    at = -1
    for heading in headings(kind):
        if heading not in found:
            problems.append(f"'## {heading}' 절이 없습니다")
            continue
        index = found.index(heading)
        if index < at:
            problems.append(f"'## {heading}' 절의 순서가 템플릿과 다릅니다")
        at = max(at, index)
    sources = text.split('## 출처', 1)[1] if '## 출처' in text else ''
    if sources and 'http' not in sources and (written or '작성 전' not in sources):
        problems.append("'## 출처'에 링크가 없습니다")
    if written and PLACEHOLDER in text:
        problems.append(f"자동 틀의 '작성 전' 자리표시가 {text.count(PLACEHOLDER)}곳 남아 있습니다")
    return problems


def due(day):
    """[(kind, key)] of the periods that ended the day before `day` (the 1st of a month: last month, and the quarter,
    half and year when they end then), shortest first: what the period task writes on that day."""
    d = date.fromisoformat(str(day)[:10])
    return [(kind, previous(kind, key_of(kind, d))) for kind in KINDS if bounds(kind, key_of(kind, d))[0] == d.isoformat()]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--check', help='a written journal Markdown file (journal-<day or period key>.md)')
    parser.add_argument('--due', nargs='?', const=date.today().isoformat(), help='the periods to write on this day (default today)')
    args = parser.parse_args()
    if args.due:
        print('\n'.join(f'{kind} {key}' for kind, key in due(args.due)) or '없음')
        return
    if not args.check:
        parser.error('--check or --due')
    from pathlib import Path
    path = Path(args.check)
    kind = kind_of(path.stem.removeprefix('journal-'))
    problems = check(path.read_text(encoding='utf-8'), 'day' if kind == 'day' else 'period', written=True)
    print('\n'.join(f'- {p}' for p in problems) or '템플릿과 일치합니다.')
    raise SystemExit(1 if problems else 0)


if __name__ == '__main__':
    main()
