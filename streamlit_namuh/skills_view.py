"""운영 → 스킬: what each project skill does, what it writes and where to read it (when it runs: 운영 → 자동 작업)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import streamlit as st

import skill_catalog as cat
import status_cards as sc
from api_status_view import KST, ago



def results(skill, runtime, now):
    """Rows of the skill's result kinds with the newest file of each."""
    rows = []
    for label, pattern in skill['outputs']:
        path, mtime = cat.latest(pattern, runtime)
        rows.append({'결과': label, '최근 파일': path.name if path else '아직 없음',
                     '만든 시각': ago(datetime.fromtimestamp(mtime, KST).isoformat(timespec='seconds'), now) if mtime else '—',
                     '위치': str(Path('~/.vibe-trading') / pattern)})
    return rows


def newest(skill, runtime, now):
    found = [cat.latest(pattern, runtime) for _, pattern in skill['outputs']]
    found = [f for f in found if f[0]]
    if not found:
        return '—'
    path, mtime = max(found, key=lambda f: f[1])
    return f"{path.name} · {ago(datetime.fromtimestamp(mtime, KST).isoformat(timespec='seconds'), now)}"


def render(runtime):
    now = datetime.now(KST)
    service = [s for s in cat.SKILLS if s['kind'] == '서비스 스킬']
    st.caption('스킬은 Claude·Codex가 따르는 작업 절차 문서입니다. 예약 작업(Claude)과 자동화(Codex)가 정해진 시각에 스킬을 불러 보고서·일지를 만들고, '
               '같은 스킬을 대화에서 직접 부를 수도 있습니다. omni 명령 스킬을 뺀 모든 스킬은 읽기 전용이고, omni는 설정과 요청을 계획한 뒤 사람의 승인으로만 적용합니다. '
               '언제 도는지는 "운영 → 자동 작업"에서 봅니다.')
    sc.row([{'label': '서비스 스킬', 'value': f'{len(service)}개', 'icon': 'auto_stories', 'tone': 'off',
             'sub': ' · '.join(f"{k.replace(' 스킬', '')} {n}개" for k in ('도식·차트 스킬', '개발 보조 스킬')
                               if (n := sum(s['kind'] == k for s in cat.SKILLS))) + ' 별도'}])

    st.markdown('##### 스킬 한눈에 보기')
    st.dataframe([{'스킬': f"{s['name']} ({s['key']})", '구분': s['kind'], '역할': s['role'], '언제': ' / '.join(s['when']),
                   '최근 결과': newest(s, runtime, now), '보는 화면': s['screen']} for s in cat.SKILLS],
                 hide_index=True, width='stretch', alt='스킬 목록', column_config={'역할': st.column_config.TextColumn(width='large')})

    st.markdown('##### 스킬 상세')
    pick = st.selectbox('스킬', [s['key'] for s in cat.SKILLS], key='skill_pick', format_func=lambda k: f"{cat.BY_KEY[k]['name']} · {k}")
    skill, path = cat.BY_KEY[pick], cat.skill_file(pick)
    with st.container(border=True):
        st.markdown(f"**{skill['name']}** · {skill['kind']} · `{cat.shown_path(path)}`")
        st.markdown(skill['role'])
        st.markdown('**언제 구동되나**\n' + '\n'.join(f'- {w}' for w in skill['when']))
        st.markdown(f"**쓰는 자료** {skill['inputs']}  \n**보는 화면** {skill['screen']}  \n**규칙** {skill['rule']}")
    if skill['outputs']:
        st.dataframe(results(skill, runtime, now), hide_index=True, width='stretch', alt=f"{skill['name']} 결과 파일")
    else:
        st.caption('파일로 남기는 결과가 없는 스킬입니다.')
