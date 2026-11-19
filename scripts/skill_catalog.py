"""The project's skills and the scheduled jobs that run them (dashboard 'API 연동 → 스킬·자동 작업', 2026-11-17).

Hand-kept like api_catalog: what each skill does, when it runs (Claude scheduled tasks, Codex automations, a request),
what it writes and which screen shows it. `outputs` are globs under the runtime root (~/.vibe-trading) so the screen
can show the newest result; a Codex automation's live state is read from its automation.toml.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REVIEWS = 'nhplug/shared-etf-paper/reviews'
DAY = '20[0-9][0-9]-[01][0-9]-[0-3][0-9]'

S = lambda key, name, kind, role, when, outputs, screen, inputs, rule='읽기 전용(주문·설정·서비스를 바꾸지 않음)': {  # noqa: E731
    'key': key, 'name': name, 'kind': kind, 'role': role, 'when': when, 'outputs': outputs, 'screen': screen, 'inputs': inputs, 'rule': rule}

SKILLS = [
    S('trading-journal-writer', '매매일지 작성', '서비스 스킬',
      '그날 매매일지와 월·분기·반기·연간 기간 매매일지를 표준 템플릿(scripts/journal_template.py)으로 쓰고 템플릿 검사를 통과시킨 뒤 일지 DB에 병합합니다.',
      ['예약 작업 "매매일지 작성" 평일 16:40', '예약 작업 "기간 매매일지 작성" 매월 1일 17:10', '요청: "매매일지 써줘", "이번 달 매매 결산"'],
      [('하루 매매관점 리포트', f'{REVIEWS}/journal-{DAY}.md'), ('하루 관점 JSON', f'{REVIEWS}/journal-{DAY}.json'),
       ('월간 매매일지', f'{REVIEWS}/journal-20??-[01][0-9].md'), ('분기·반기 매매일지', f'{REVIEWS}/journal-20??-[QH]?.md'),
       ('연간 매매일지', f'{REVIEWS}/journal-20??.md')],
      '매매일지 → 매매관점 리포트 · 기간 매매일지 리포트',
      '일지 레코드(trading_journal.py: 체결·손익·시장·수급·키움/토스 자료·뉴스·애널리스트·차트), 그날 장 마감 개선안'),
    S('premarket-analysis', '장전 상세 분석', '서비스 스킬',
      '장전 자료(premarket-brief.json)를 공통 템플릿(scripts/premarket_template.py)대로 상세 분석문으로 쓰고 검사합니다. 자료 수집 자체는 launchd 장전 수집(07:40~09:10)이 합니다.',
      ['예약 작업 "장전 상세 분석 작성" 평일 08:31', '요청: "장전 분석", "오늘 장 전망"'],
      [('장전 상세 분석', f'{REVIEWS}/premarket-{DAY}.md')],
      '시장 상황 → 장전 화면',
      '장전 브리프(해외 지수·선물·NXT·KRX/NXT 괴리율·상장 예정·뉴스·공시·KRX 통계)'),
    S('intraday-audit', '장중 매매·차단 감사', '서비스 스킬',
      '장중 Jev 공유 모의·실계좌 미러의 실행·시세·체결·진입 차단·이벤트를 점검해 같은 날 파일에 중복 없이 누적합니다(수집기 scripts/intraday_audit.py).',
      ['예약 작업 "장중 매매·차단 감사" 평일 09:20~15:20 매시 20분', '요청: "장중 감사", "왜 안 샀는지 감사"'],
      [('장중 감사 기록', f'{REVIEWS}/operations-audit-{DAY}.md')],
      '전용 화면 없음(파일로 확인)',
      '실행기 이벤트·주문 원장·미러 기록·서비스 상태'),
    S('longterm-strategy-writer', '장기투자 전략 리포트', '서비스 스킬',
      '장기투자(vibe) 가상 계좌의 일일·주간·월간 전략 리포트를 표준 템플릿(scripts/longterm_template.py)으로 씁니다. 시황, 차트 위치, 과거 사례, 거장 관점, 분할 매수·매도 타점, 시나리오별 대응을 담습니다.',
      ['예약 작업 "장기투자 일일 전략 리포트" 평일 08:50(장기투자 운영 꺼짐이면 쓰지 않음)', '예약 작업 "장기투자 주간 전략 리포트" 금 16:50',
       '예약 작업 "장기투자 월간 전략 리포트" 매월 1일 17:20', '요청: "장기투자 리포트", "오늘 장기 전략"'],
      [('일일·주간·월간 전략 리포트', 'nhplug/vibe-longterm/reports/strategy-*.md'), ('리포트 데이터', 'nhplug/vibe-longterm/reports/strategy-*.json')],
      '장기투자 → 리포트·감사 → 전략 리포트',
      '장기 엔진의 아침 판단·국면·모듈·가상 계좌, 과거 사례 DB, 시황'),
    S('report-html-publisher', '리포트 HTML 발행', '서비스 스킬',
      '위 리포트(.md·.json)를 DESIGN.md 토큰·Pretendard 글꼴의 같은 틀 HTML로 만들어 GitHub Pages에 올리고 Discord에 제목·요약·링크를 보냅니다. '
      '생성·게시·발송 상태와 실패 사유를 기록하고 다시 생성·게시·발송합니다(scripts/report_pages.py, 틀 scripts/report_templates/).',
      ['launchd 디스코드 알림 서비스가 30초마다 새·바뀐 리포트를 자동 처리', '요청: "리포트 HTML 다시 만들어", "리포트 링크 다시 보내", "Pages 배포"'],
      [('리포트 사이트 목록', 'report-pages/site/reports/index.html'), ('리포트 HTML', 'report-pages/site/reports/*/*.html')],
      'API 연동 → 리포트 발행', '각 작성 스킬이 쓴 리포트 .md·.json',
      'Pages 푸시는 REPORT_PAGES_PUSH=on일 때만(사용자 승인). 원본 리포트 내용은 고치지 않음'),
    S('vibe-trading', 'vibe-trading 운영·리서치', '서비스 스킬',
      '이 저장소 전용 운영 규칙입니다. 진행 상황·결과 보고, 시황·차트·수급·뉴스 분석, 분할 매수·매도 판단 지원, 청산 요청, 백테스트·팩터 리서치 도구를 안내합니다. 아래 예약 작업들이 이 규칙을 읽고 씁니다.',
      ['요청: "/vibe-trading", "진행 상황 알려줘"', '예약 작업 "장중 모의매매 진행 보고" 평일 11:30·13:30·14:30', '예약 작업 "장 마감 후 보고" 평일 15:45',
       '예약 작업 "장 마감 개선안 연구" 평일 16:15', '예약 작업 "낙폭과대 전략 주간 감사" 금 16:45'],
      [('장 마감 개선안', f'{REVIEWS}/improvement-{DAY}.md'), ('낙폭과대 주간 감사', f'{REVIEWS}/oversold-weekly-{DAY}.md')],
      '전략 구조 → 장 마감 개선안 · 보고는 대화(예약 작업 실행 기록)',
      '실행기 이벤트·원장·시황·설정·장 마감 개선안 데이터(scripts/daily_improvement.py)'),
    S('automation-guideline-review', '자동화 지침 검수', '서비스 스킬',
      'Codex 자동화·예약 작업 지침을 리뷰·검수·테스트합니다. 상시 규칙은 스킬로, 반복 수집은 읽기 전용 스크립트로, 일회성 점검은 날짜별 확인 목록으로 나눈 짧은 개정 지침을 만듭니다.',
      ['요청할 때만: "자동화 지침 리뷰", "예약 작업 프롬프트 점검"'],
      [], '전용 화면 없음(대화로 결과·개정 지침)', '자동화·예약 작업 지침과 참조 문서',
      '지침 반영(scripts/apply_prompt.py)은 사용자 승인 뒤에만'),
    S('developing-with-streamlit', 'Streamlit 개발 지침', '개발 보조 스킬',
      '이 대시보드를 고칠 때 Streamlit 위젯·레이아웃·캐시·테스트(AppTest) 모범 사례를 안내합니다.',
      ['대시보드 코드를 고칠 때 개발 세션이 씀'], [], '대시보드 전체', 'streamlit_namuh/ 코드', '자동 구동 없음'),
    S('archify', 'Archify 도식', '도식·차트 스킬',
      '아키텍처·워크플로·상태 전이 도식을 외부 글꼴·CDN 없는 단일 HTML(SVG)로 그립니다. 대시보드가 지금 설정·기록으로 도식 명세를 만들어 이 CLI로 그리고, 내용이 같으면 캐시를 씁니다.',
      ['대시보드 전략 구조를 열 때(Jev 전략 도식 · 장기투자 아키텍처·하루 알고리즘·국면 전이)', '설정·국면이 바뀌어 명세가 달라질 때만 다시 그림',
       '요청: "도식 그려줘", "아키텍처 다이어그램"'],
      [('Jev 전략 도식', 'nhplug/shared-etf-paper/strategy-map/strategy-map-*.html'), ('장기투자 도식', 'nhplug/vibe-longterm/diagrams/longterm-*.html')],
      '전략 구조 (Jev 트레이딩 · 장기투자)', 'scripts/strategy_map.spec · streamlit_namuh/longterm_flow.py 명세',
      '읽기 전용(도식 파일만 만듦). 실패하면 graphviz 기본 도식으로 표시'),
    S('design-taste-frontend', 'taste-skill 디자인 검수', '도식·차트 스킬',
      '회사·제품 웹페이지를 만들거나 다듬을 때 쓰는 외부 디자인 스킬(taste-skill v2.5.1)입니다. 한 체계·평이한 문구·가장자리 정렬·실제 브라우저 검사(1440·1024·768·390px)를 강제합니다. '
      '리포트 HTML 규칙은 이 스킬에서 데이터 리포트에 맞는 것만 옮겨 썼습니다(report-html-publisher references/design.md).',
      ['요청할 때만: "랜딩 페이지 만들어줘", "페이지가 AI가 만든 것 같아"'], [], '전용 화면 없음(만든 페이지)', '페이지 요구사항',
      '자동 구동 없음. 프로젝트 범위 설치, git 제외(36MB)'),
    S('diagram', '다이어그램 초안(Excalidraw)', '도식·차트 스킬',
      '말이나 Mermaid로 설명한 구조를 편집 가능한 Excalidraw 파일과 SVG·PNG로 만듭니다(gstack). 문서·기획용이며 대시보드는 쓰지 않습니다.',
      ['요청할 때만: "다이어그램 만들어줘"'], [], '전용 화면 없음(파일)', '설명 문장·Mermaid', '자동 구동 없음'),
    S('ui-ux-pro-max', 'UI/UX 설계 검색', '개발 보조 스킬',
      '화면 설계 때 스타일·색·차트·접근성 지침을 로컬 DB에서 찾아 줍니다(API 연동 화면의 상태 페이지 패턴, 장기투자 화면의 불릿·선 차트 기준을 여기서 골랐습니다).',
      ['화면을 새로 설계할 때 개발 세션이 씀'], [], '대시보드 전체', '설계 질의', '자동 구동 없음'),
]
BY_KEY = {s['key']: s for s in SKILLS}

J = lambda key, name, runner, when, skill, result: {'key': key, 'name': name, 'runner': runner, 'when': when, 'skill': skill, 'result': result}  # noqa: E731
KNOWN = [  # what each task does (skill, result) and a readable time; the list itself comes from the backup manifest (jobs())
    J('trading-preflight', '장전 사전 점검·보완', 'Claude 예약 작업', '평일 08:25', 'vibe-trading', '고친 내용·남은 문제 대화 보고'),
    J('premarket-analysis-daily', '장전 상세 분석 작성', 'Claude 예약 작업', '평일 08:31', 'premarket-analysis', '장전 상세 분석 .md'),
    J('longterm-strategy-daily', '장기투자 일일 전략 리포트', 'Claude 예약 작업', '평일 08:50', 'longterm-strategy-writer', '전략 리포트 .md·.json'),
    J('intraday-audit-hourly', '장중 매매·차단 감사', 'Claude 예약 작업', '평일 09:20~15:20 매시 20분', 'intraday-audit', '장중 감사 기록 .md'),
    J('trading-intraday-report', '장중 모의매매 진행 보고', 'Claude 예약 작업', '평일 11:30·13:30·14:30', 'vibe-trading', '대화 보고'),
    J('trading-close-report', '장 마감 후 모의·실계좌 보고', 'Claude 예약 작업', '평일 15:45', 'vibe-trading', '대화 보고'),
    J('trading-daily-improvement', '장 마감 개선안 연구', 'Claude 예약 작업', '평일 16:15', 'vibe-trading', '개선안 보고서 .md'),
    J('longterm-review-daily', '장기투자 일일 복기·감사', 'Claude 예약 작업', '평일 16:45', 'vibe-trading', '복기·감사 보고서 .md (longterm_audit)'),
    J('trading-journal', '매매일지 작성', 'Claude 예약 작업', '평일 16:40', 'trading-journal-writer', '매매일지 .md·.json, 일지 DB 병합'),
    J('oversold-weekly-audit', '낙폭과대 전략 주간 감사', 'Claude 예약 작업', '금 16:45', 'vibe-trading', '주간 감사 보고서 .md'),
    J('longterm-strategy-weekly', '장기투자 주간 전략 리포트', 'Claude 예약 작업', '금 16:50', 'longterm-strategy-writer', '주간 전략 리포트'),
    J('longterm-audit-weekly', '장기투자 주간 감사', 'Claude 예약 작업', '금 17:40', 'vibe-trading', '주간 감사 보고서 .md (longterm_audit)'),
    J('trading-journal-period', '기간 매매일지 작성', 'Claude 예약 작업', '매월 1일 17:10', 'trading-journal-writer', '월·분기·반기·년 매매일지'),
    J('longterm-strategy-monthly', '장기투자 월간 전략 리포트', 'Claude 예약 작업', '매월 1일 17:20', 'longterm-strategy-writer', '월간 전략 리포트'),
    J('longterm-audit-monthly', '장기투자 월간 감사', 'Claude 예약 작업', '매월 1일 17:45', 'vibe-trading', '감사·개선안 보고서 .md (longterm_audit)'),
    J('strength-shadow-10day-review', '관심종목 강도 경로 10거래일 결과', 'Claude 예약 작업', '11/30 15:40 한 번', 'vibe-trading', '대화 보고'),
]
KNOWN = {j['key']: j for j in KNOWN}
MANIFEST = REPO / 'ops' / 'scheduled-tasks' / 'manifest.json'
DAYS = {'1-5': '평일', '1': '월', '2': '화', '3': '수', '4': '목', '5': '금', '*': '매일'}


def cron_text(cron):
    """'40 16 * * 1-5' -> '평일 16:40'; a cron it cannot word stays as it is."""
    parts = (cron or '').split()
    if len(parts) != 5 or not parts[0].isdigit():
        return cron or ''
    minute, hour, dom, month, dow = parts
    when = f'{int(hour):02d}:{int(minute):02d}' if hour.isdigit() else f'{hour.replace("-", "~").replace(",", "·")}시 {int(minute)}분'
    if dom == '*' and month == '*' and dow in DAYS:
        return f'{DAYS[dow]} {when}'
    if dom.isdigit() and month == '*' and dow == '*':
        return f'매월 {int(dom)}일 {when}'
    if dom.isdigit() and month.isdigit() and dow == '*':
        return f'{int(month)}/{int(dom)} {when}'
    return cron


def jobs(manifest=None):
    """Every Claude scheduled task in the backup manifest (ops/scheduled-tasks, refreshed by state_sync tasks-export from the
    Claude app's own store), with KNOWN's skill and result where it has them (user 2026-11-19: the screen lists them all)."""
    try:
        entries = json.loads(Path(manifest or MANIFEST).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        entries = [{'taskId': k, 'title': j['name']} for k, j in KNOWN.items()]
    out = []
    for e in entries:
        known = KNOWN.get(e.get('taskId'), {})
        when = cron_text(e.get('cron')) if e.get('cron') else (f"{e['schedule'][5:7].lstrip('0')}/{e['schedule'][8:10]} {e['schedule'][11:16]} 한 번"
                                                                 if isinstance(e.get('schedule'), str) and e['schedule'][:4].isdigit() else known.get('when', ''))
        out.append({'key': e['taskId'], 'name': e.get('title') or known.get('name') or e.get('description', ''),
                    'runner': 'Claude 예약 작업', 'when': when or known.get('when', ''), 'skill': known.get('skill', ''),
                    'result': known.get('result') or e.get('description', ''), 'enabled': e.get('enabled', True) is not False,
                    'cron': e.get('cron'), 'schedule': e.get('schedule'), 'permission_mode': e.get('permission_mode')})
    return sorted(out, key=clock_key)


def clock_key(job):
    """(time of day, key) for the day's order: a cron's first hour and minute, a one-time task's time."""
    parts = (job.get('cron') or '').split()
    if len(parts) == 5 and parts[0].isdigit():
        hour = re.match(r'\d+', parts[1])
        return (int(hour.group()) if hour else 99, int(parts[0]), job['key'])
    at = job.get('schedule') or ''
    return (int(at[11:13]), int(at[14:16]), job['key']) if re.match(r'\d{4}-\d\d-\d\dT\d\d:\d\d', at) else (99, 0, job['key'])


JOBS = jobs()


def skill_file(key):
    """The skill's SKILL.md in the repo (.claude/skills, the Codex copy under .agents/skills) or the user's skills, or None."""
    return next((p for p in (REPO / '.claude/skills' / key / 'SKILL.md', REPO / '.agents/skills' / key / 'SKILL.md',
                             Path.home() / '.claude/skills' / key / 'SKILL.md') if p.is_file()), None)


def shown_path(path):
    """Where a skill file is, for the screen: repo-relative, or ~/… for a user-level skill."""
    if path is None:
        return '파일 없음'
    for base, prefix in ((REPO, ''), (Path.home(), '~/')):
        try:
            return prefix + str(path.relative_to(base))
        except ValueError:
            continue
    return str(path)


def job_state(job, home=None):
    """'실행 중'/'일시 정지' of a Codex automation (automation.toml), '등록됨'/'없음' of a Claude scheduled task."""
    home = Path(home or Path.home())
    if job['runner'] == 'Codex 자동화':
        try:
            status = re.search(r'^status\s*=\s*"([A-Z]+)"', (home / '.codex/automations' / job['key'] / 'automation.toml').read_text(), re.M)
        except OSError:
            return '없음'
        return {'ACTIVE': '실행 중', 'PAUSED': '일시 정지'}.get(status.group(1) if status else '', '확인 불가')
    if job.get('enabled') is False:
        return '꺼짐'
    return '등록됨' if (home / '.claude/scheduled-tasks' / job['key'] / 'SKILL.md').is_file() else '없음'


def latest(pattern, runtime):
    """(newest file, its mtime) matching the glob under the runtime root, or (None, None)."""
    files = [p for p in Path(runtime).glob(pattern) if p.is_file()]
    if not files:
        return None, None
    newest = max(files, key=lambda p: p.stat().st_mtime)
    return newest, newest.stat().st_mtime
