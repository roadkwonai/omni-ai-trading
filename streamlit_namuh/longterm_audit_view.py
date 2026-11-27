"""장기투자 → 리포트·감사 → 감사·개선안 (user request 2026-11-18): the weekly and monthly audit reports (reviews/audit-<key>.md, written by
the scheduled tasks longterm-audit-weekly·monthly from scripts/longterm_audit.py) and today's plain check of the decision
log. Read-only: an audit only proposes; changes need the user's approval."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import streamlit as st

import longterm_audit as A
import vibe_longterm as L
from report_figures import for_streamlit
from report_pages import sort_date


def coverage_rows(data, root=None):
    yes = lambda v: '—' if v is None else ('있음' if v else '없음')  # noqa: E731
    return [{'날짜': c['day'], '기록': ' · '.join(f"{L.DECISION_KINDS.get(k, '거래' if k == 'trade' else k)} {n}" for k, n in c['kinds'].items()) or '—',
             '빠짐': '시작 전' if c.get('before_start') else ', '.join(c['missing']) or '—',
             '스토리 판단': c['story_source'] or '—', '스토리 출처 수': c['story_sources'], '오류·놓침': c['errors'] + c['missed'],
             '아침 리포트': yes(c['report']), '저녁 복기': yes(A.path(root, c['day']).exists() if root else None),
             '일지 장기 관점': yes(c['journal_view']), '순자산': yes(c['nav'])} for c in data['coverage']]


def gaps(rows):
    """'11-19 아침 리포트·저녁 복기' for each day missing a morning report, an evening review or the close NAV (W47 C1)."""
    out = []
    for r in rows:
        if r['빠짐'] == '시작 전':
            continue
        miss = [k for k in ('아침 리포트', '저녁 복기', '순자산') if r[k] == '없음']
        if miss:
            out.append(f"{r['날짜'][5:]} {'·'.join(miss)}")
    return out


def render(root):
    root = Path(root)
    st.caption('장기 엔진이 무엇을 보고 어떻게 판단했는지, 기록·리포트가 빠짐없이 남는지를 감사해 개선·보완안을 제안합니다. '
               '감사는 제안만 하고, 매매 규칙·설정 변경은 사용자 승인 뒤에만 적용합니다.')
    data = A.collect(root, datetime.now(L.KST).date(), A.WEEK_DAYS)
    with st.container(border=True):
        st.markdown(f"**최근 {len(data['days'])}거래일 자동 점검** · 찾은 문제 {len(data['findings'])}건")
        for f in data['findings'][:12]:
            st.markdown(f'- {f}')
        if not data['findings']:
            st.caption('빠진 기록·오류·리포트 누락이 없습니다.')
        rows = coverage_rows(data, root)
        if missing := gaps(rows):
            st.markdown(':red[**빠진 날** · ' + ' / '.join(missing) + ']')
        with st.expander('날짜별 기록 범위', icon=':material/table_rows:'):
            st.dataframe(rows, hide_index=True, width='stretch')
    keys = sorted(A.status(root)['keys'], key=lambda k: (sort_date(k), not A.is_month(k)), reverse=True)
    if not keys:
        st.info('아직 복기·감사 보고서가 없습니다. 예약 작업이 평일 16:45에 일일 복기·감사(그날 관찰·매매 근거·거장·거시 관점·비교 전략 검토), '
                '금 17:40에 주간 감사, 매월 1일 17:45에 월간 감사를 씁니다. 아침 08:50 전략 리포트는 계획, 저녁 보고서는 복기입니다. '
                '지금 쓰려면 Claude에게 "오늘 장기투자 복기해줘"라고 요청하세요.', icon=':material/fact_check:')
        return
    key = st.selectbox('복기·감사 보고서', keys, key='lt_audit_key', format_func=lambda k: f"{k} · {A.LABEL[A.kind_of(k)]}")
    text = A.path(root, key).read_text(encoding='utf-8')
    if problems := A.check(text, key):
        st.warning('템플릿 검사: ' + ' / '.join(problems[:3]), icon=':material/rule:')
    st.download_button('보고서 .md 내려받기', text.encode('utf-8'), A.path(root, key).name, 'text/markdown', icon=':material/download:',
                       key='lt_audit_dl')
    st.markdown(for_streamlit(text))
