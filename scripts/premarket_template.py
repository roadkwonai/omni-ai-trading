"""장전 분석 템플릿 — 유일한 원본 (2026-11-17).

절 제목·순서와 각 절이 참조하는 브리프 자료(sections 키)를 여기서만 정한다. 자동 틀(premarket.report_md,
`premarket.py --report`), 브리프의 report_template(build), 작성된 분석 검사(check, `--check`), 대시보드 경고,
스킬 references/template.md가 모두 이 정의를 쓴다. 매매일지의 journal_template.py와 같은 방식이다.

    python scripts/premarket_template.py --check ~/.vibe-trading/nhplug/shared-etf-paper/reviews/premarket-2026-11-17.md
"""
from __future__ import annotations

import argparse
import re
from datetime import date

# (key, heading, brief sections the section draws on, what it holds)
SECTIONS = (
    ('status', '1. 수집 상태', ('market', 'news', 'futures', 'night_futures', 'nxt_quotes', 'krx', 'dart'),
     '거래일, 작성·수집 시각(KST), 자료별 상태(정상·부분·빈 결과·종료 세션·실패), 직전 세션과 휴장 여부'),
    ('overnight', '2. 밤사이 시장', ('market', 'futures', 'night_futures'),
     '미국 종가, 지수 선물·야간선물의 기준 시각과 지연, 환율·원자재, 국내 전 거래일 지수·수급·테마'),
    ('issues', '3. 핵심 이슈', ('news', 'overnight_news'),
     '규칙 기반 별점(premarket.rank_news)과 이유, 기사 제목·매체·발행 시각(KST)·링크, 이슈 해석, 장 마감 뒤 호재·악재 종목과 해외 재료'),
    ('impacts', '4. 시장·섹터·종목 영향', ('market', 'news', 'dart', 'krx', 'nxt_quotes'),
     '시장·섹터·관심 종목(보유·ETF·수동·스캐너·대형주)별 근거, 가능한 영향과 반대 시나리오, 개장 후 확인할 가격·수급'),
    ('strategy', '5. 오늘의 대응과 전략', ('market', 'news', 'night_futures'),
     '상승·하락·혼조/자료 부족별 대응, 기존 승인 규칙과 실제 설정, 조건 충족 전 대기 기준'),
    ('sources', '6. 출처와 한계', ('market', 'news', 'futures', 'night_futures', 'nxt_quotes', 'krx', 'dart'),
     '기사·공시·시세 링크, 시각과 지연, 빠진 자료와 확인하지 못한 항목'),
)
SOURCES = {'market': '시황(지수·수급·해외 일봉)', 'news': '뉴스', 'futures': '해외 선물', 'night_futures': '코스피200 야간선물',
           'nxt_quotes': 'NXT 장전 호가', 'krx': 'KRX 일별 통계', 'dart': '공시', 'overnight_news': '장 마감 뒤 재료'}
STATUS = {'ok': '수집됨', 'partial': '일부 수집', 'empty': '빈 결과(미공개·대상 없음)', 'closed': '종료 세션 마감값(지연)',
          'unavailable': '조회 불가', 'error': '오류', 'unknown': '확인 불가'}
TITLE = '# 장전 분석 · '
WRITTEN = '작성: '  # the line under the summary: when it was written (before the open or after) and the brief's age
WEEKDAYS = '월화수목금토일'
PLACEHOLDER = '_작성 전 — '
WRITE = PLACEHOLDER + '장전 분석 스킬(premarket-analysis)이 이 자리에 분석을 씁니다._'
SUMMARY_PENDING = PLACEHOLDER + '한 줄 요약을 씁니다._'


def headings():
    return [heading for _, heading, _, _ in SECTIONS]


def title(day):
    """'# 장전 분석 · 2026-11-17 (화)'."""
    return f'{TITLE}{day} ({WEEKDAYS[date.fromisoformat(day).weekday()]})'


def build(brief):
    """The brief's report_template: the sections with the status and observation time of every source they use."""
    sources = brief.get('sections') or {}
    return {
        'version': 2,
        'trading_day': brief['trading_day'],
        'generated_at': brief['time'],
        'title': title(brief['trading_day']),
        'sections': [
            {'key': key, 'title': heading, 'guide': guide,
             'sources': [{'key': name, 'status': (sources.get(name) or {}).get('status', 'unavailable'),
                          'observed_at': (sources.get(name) or {}).get('observed_at')}
                         for name in names]}
            for key, heading, names, guide in SECTIONS
        ],
    }


def check(text, written=False, day=None):
    """Problems of a premarket analysis Markdown against the template: the title (and its day), the one-line summary,
    every section in order, links in 출처와 한계, and — for a written analysis — no '작성 전' placeholders left.
    An empty list means the analysis follows the template."""
    lines = text.splitlines()
    problems = []
    if not lines or not lines[0].startswith(TITLE):
        problems.append(f"첫 줄이 '{TITLE}YYYY-MM-DD (요일)' 제목이 아닙니다")
    elif day and not lines[0].startswith(TITLE + day):
        problems.append(f'제목의 날짜가 {day}가 아닙니다')
    if not any(line.startswith('> ') for line in lines[:6]):
        problems.append("제목 아래 '> 한 줄 요약'이 없습니다")
    stamp = next((line for line in lines[:10] if line.startswith(WRITTEN)), None)
    if not stamp:
        problems.append(f"제목 아래 '{WRITTEN}MM/DD HH:MM KST (개장 전|개장 후 사후 작성)' 줄이 없습니다")
    else:
        at = re.search(r' (\d{2}):(\d{2}) ', stamp)
        if at and at.group(1) + at.group(2) >= '0900' and '사후 작성' not in stamp:
            problems.append('09:00 이후 작성인데 작성 줄에 사후 작성 표기가 없습니다')
    found = [line[3:].strip() for line in lines if line.startswith('## ')]
    at = -1
    for heading in headings():
        if heading not in found:
            problems.append(f"'## {heading}' 절이 없습니다")
            continue
        index = found.index(heading)
        if index < at:
            problems.append(f"'## {heading}' 절의 순서가 템플릿과 다릅니다")
        at = max(at, index)
    last = '## ' + SECTIONS[-1][1]
    if last in text and not re.search(r'\]\(https?://', text.split(last, 1)[1]):
        problems.append(f"'{last}'에 링크가 없습니다")
    if written and PLACEHOLDER in text:
        problems.append(f"자동 틀의 '작성 전' 자리표시가 {text.count(PLACEHOLDER)}곳 남아 있습니다")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--check', required=True, help='a written analysis (premarket-YYYY-MM-DD.md)')
    args = parser.parse_args()
    from pathlib import Path
    path = Path(args.check)
    day = path.stem.removeprefix('premarket-')
    problems = check(path.read_text(encoding='utf-8'), written=True,
                     day=day if re.fullmatch(r'\d{4}-\d{2}-\d{2}', day) else None)
    print('\n'.join(f'- {p}' for p in problems) or '템플릿과 일치합니다.')
    raise SystemExit(1 if problems else 0)


if __name__ == '__main__':
    main()
