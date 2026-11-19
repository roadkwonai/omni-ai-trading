"""API 연동 → 스킬·자동 작업: what each project skill does, when it runs, what it writes and where to read it."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import streamlit as st

import skill_catalog as cat
import status_cards as sc
from api_status_view import KST, ago

STATE_TONE = {'실행 중': 'ok', '등록됨': 'ok', '일시 정지': 'warn', '꺼짐': 'warn', '없음': 'off', '확인 불가': 'warn'}


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
    jobs = [{**j, 'state': cat.job_state(j)} for j in cat.jobs()]  # every backed-up Claude task (ops/scheduled-tasks)
    service = [s for s in cat.SKILLS if s['kind'] == '서비스 스킬']
    codex = [j for j in jobs if j['runner'] == 'Codex 자동화']
    st.caption('스킬은 Claude·Codex가 따르는 작업 절차 문서입니다. 예약 작업(Claude)과 자동화(Codex)가 정해진 시각에 스킬을 불러 보고서·일지를 만들고, '
               '같은 스킬을 대화에서 직접 부를 수도 있습니다. 모든 스킬은 읽기 전용이라 주문·설정·서비스를 바꾸지 않습니다.')
    sc.row([{'label': '서비스 스킬', 'value': f'{len(service)}개', 'icon': 'auto_stories', 'tone': 'off',
             'sub': ' · '.join(f"{k.replace(' 스킬', '')} {n}개" for k in ('도식·차트 스킬', '개발 보조 스킬')
                               if (n := sum(s['kind'] == k for s in cat.SKILLS))) + ' 별도'},
            {'label': 'Claude 예약 작업', 'value': f"{sum(j['state'] == '등록됨' for j in jobs if j['runner'] == 'Claude 예약 작업')}개 등록",
             'icon': 'schedule', 'tone': 'ok' if all(j['state'] == '등록됨' for j in jobs) else 'warn',
             'sub': f"백업 {len(jobs)}개(ops/scheduled-tasks) · 켜짐·다음 실행은 Claude 앱 예약 작업에서"},
            {'label': 'Codex 자동화', 'value': f"{sum(j['state'] == '실행 중' for j in codex)}/{len(codex)} 실행 중", 'icon': 'smart_toy',
             'tone': 'warn' if any(j['state'] != '실행 중' for j in codex) else 'ok', 'sub': ' · '.join(f"{j['name']} {j['state']}" for j in codex)}])

    st.markdown('##### 하루 일정 (예약 작업·자동화가 스킬을 부르는 시각)')
    tone = lambda v: f"color:{sc.TONES[STATE_TONE.get(v, 'off')][0]};font-weight:700"  # noqa: E731
    import pandas as pd
    st.dataframe(pd.DataFrame([{'시각': j['when'], '작업': j['name'], '작업 ID': j['key'], '상태': j['state'],
                                '쓰는 스킬': cat.BY_KEY[j['skill']]['name'] if j['skill'] in cat.BY_KEY else '—',
                                '결과': j['result'], '일정(cron·한 번)': j['cron'] or j['schedule'] or '—'} for j in jobs]).style.map(tone, subset=['상태']),
                 hide_index=True, width='stretch', alt='예약 작업·자동화 일정')
    st.caption('목록은 백업(ops/scheduled-tasks/manifest.json)에서 그립니다. 백업은 평일 16:55 state-sync가 Claude 앱의 예약 작업(지시문·cron·한 번 시각·켜짐)을 '
               '그대로 옮겨 쓰고, 다른 Claude 계정에서는 ops-bootstrap 스킬이 이 목록으로 같은 일정을 다시 등록합니다.')
    st.caption('일시 정지된 Codex 자동화는 그 스킬을 대화에서 요청하면 같은 결과를 만듭니다. 장전 자료 수집(07:40~09:10)과 장기 엔진(08:40)은 스킬이 아니라 '
               'launchd 서비스이며 "운영 상태" 탭에서 봅니다.')

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
