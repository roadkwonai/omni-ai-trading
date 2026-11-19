"""운영 → 자동 작업 (user 2026-11-19): every scheduled job (launchd services, Claude scheduled tasks, Codex automations)
in one place: what runs on a chosen day and when, and the full list with each job's state. Read-only (scripts/schedules.py)."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

import status_cards as sc
from schedules import clock, times_text

KST = ZoneInfo('Asia/Seoul')
COLORS = {'launchd 서비스': '#22c55e', 'Claude 예약 작업': '#a855f7', 'Codex 자동화': '#64748b'}
STATE_TONE = {'실행 중': 'ok', '등록됨': 'ok', '종료': 'off', '꺼짐': 'warn', '일시 정지': 'warn', '없음': 'alert', '미등록': 'alert',
              '확인 불가': 'alert'}
DAY_NAMES = '월화수목금토일'


def day_rows(jobs, day):
    """One row per job running on `day`: its first start, every start of the day as text, count, state."""
    import schedules as S
    grouped = {}
    for h, m, job in S.day_plan(jobs, day):
        grouped.setdefault((job['kind'], job['key']), (job, []))[1].append((h, m))
    rows = [{'첫 시작': clock(*ts[0]), '구분': j['kind'], '작업': j['name'], '이날 시각': times_text(ts), '횟수': len(ts), '상태': j['state'],
             '하는 일': j['what']} for j, ts in grouped.values()]
    return sorted(rows, key=lambda r: (r['첫 시작'], r['작업']))


def render(runtime=None):
    import schedules as S
    from automation_status import processes
    now = datetime.now(KST)
    jobs = S.all_jobs(processes())
    head, pick = st.columns([3, 1], vertical_alignment='bottom')
    head.subheader('자동 작업 · 언제 무엇이 도나')
    day = pick.date_input('날짜', now.date(), key='sched_day', help='그날 시작하는 작업을 시각 순으로 봅니다. 상시 서비스는 아래 전체 목록에 있습니다.')
    by = {k: [j for j in jobs if j['kind'] == k] for k in S.KINDS}
    always = [j for j in by['launchd 서비스'] if j['always']]
    timed = [j for j in by['launchd 서비스'] if not j['always']]
    claude, codex = by['Claude 예약 작업'], by['Codex 자동화']
    nxt = S.next_start(jobs, now)
    sc.row([{'label': '상시 서비스', 'value': f"{sum(j['state'] == '실행 중' for j in always)}/{len(always)} 실행 중", 'icon': 'dns',
             'tone': 'ok' if all(j['state'] == '실행 중' for j in always) else 'warn', 'sub': '실행기·관찰기·대시보드·알림 (launchd)',
             'help': '로그인하면 시작해 계속 도는 서비스입니다. 장 밖에 실행기가 "종료"인 것은 정상일 수 있습니다.'},
            {'label': '시간 서비스', 'value': f'{len(timed)}개', 'icon': 'schedule', 'tone': 'off',
             'sub': 'launchd · 정한 시각에 한 번 돌고 끝남', 'help': '정한 시각에 시작해 일을 마치면 "종료"가 됩니다. 정상입니다.'},
            {'label': 'Claude 예약 작업', 'value': f"{sum(j['state'] == '등록됨' for j in claude)}개 켜짐", 'icon': 'smart_toy',
             'tone': 'ok', 'sub': f"꺼짐·끝난 한 번 작업 {sum(j['state'] != '등록됨' for j in claude)}개 · 앱이 열려 있을 때 실행",
             'help': 'Claude 앱의 예약 작업입니다. 앱이 닫혀 있으면 다음에 열 때 실행됩니다. 켜고 끄기·일정 변경은 앱의 예약 작업 화면에서 합니다.'},
            {'label': 'Codex 자동화', 'value': f"{sum(j['state'] == '실행 중' for j in codex)}/{len(codex)} 실행 중", 'icon': 'pause_circle',
             'tone': 'off', 'sub': ' · '.join(f"{j['name']} {j['state']}" for j in codex) or '없음',
             'help': '같은 일을 Claude 예약 작업으로 옮겨 일시 정지해 두었습니다. 둘 다 켜면 같은 작업이 두 번 돌아갑니다.'},
            {'label': '다음 시작', 'value': nxt[0].strftime('%H:%M') if nxt else '—', 'icon': 'alarm', 'tone': 'ai',
             'sub': (f"{nxt[0]:%m/%d}({DAY_NAMES[nxt[0].weekday()]}) · {nxt[1]['name']}" if nxt else '8일 안에 없음')}])

    plan = S.day_plan(jobs, day)
    st.markdown(f"##### {day:%m/%d}({DAY_NAMES[day.weekday()]}) 일정 · 시작 {len(plan)}번")
    if not plan:
        st.info('이날 시작하는 작업이 없습니다(주말·휴일). 상시 서비스는 계속 돌아갑니다.', icon=':material/event_busy:')
    else:
        dots = pd.DataFrame([{'시각': datetime.combine(day, datetime.min.time()).replace(hour=h, minute=m), '구분': j['kind'],
                              '작업': j['name'], '상태': j['state']} for h, m, j in plan])
        st.altair_chart(alt.Chart(dots).mark_tick(thickness=3, size=22).encode(
            x=alt.X('시각:T', title=None, axis=alt.Axis(format='%H:%M', tickCount=12)),
            y=alt.Y('구분:N', title=None, sort=list(S.KINDS)),
            color=alt.Color('구분:N', scale=alt.Scale(domain=list(COLORS), range=list(COLORS.values())), legend=None),
            tooltip=[alt.Tooltip('시각:T', format='%H:%M'), '구분', '작업', '상태']).properties(height=150), width='stretch')
        tone = lambda v: f"color:{sc.TONES[STATE_TONE.get(v, 'off')][0]};font-weight:700"  # noqa: E731
        st.dataframe(pd.DataFrame(day_rows(jobs, day)).style.map(tone, subset=['상태']), hide_index=True, width='stretch',
                     alt='그날 자동 작업 일정', column_config={'이날 시각': st.column_config.TextColumn(width='medium'),
                                                               '하는 일': st.column_config.TextColumn(width='large')})
        st.caption('세로 막대 하나가 작업 시작 한 번입니다. 시간 서비스·한 번 작업의 "종료"는 정상이고, 끝난 한 번 작업은 "꺼짐"으로 보입니다.')

    st.markdown('##### 전체 목록')
    kinds = st.segmented_control('구분', ['전체', *S.KINDS], default='전체', key='sched_kind', label_visibility='collapsed') or '전체'
    rows = [{'구분': j['kind'], '작업': j['name'], '언제': j['when'], '상태': j['state'], '하는 일': j['what'], 'ID': j['key'],
             '권한 모드': j['mode']} for j in jobs if kinds in ('전체', j['kind'])]
    tone = lambda v: f"color:{sc.TONES[STATE_TONE.get(v, 'off')][0]};font-weight:700"  # noqa: E731
    st.dataframe(pd.DataFrame(rows).style.map(tone, subset=['상태']), hide_index=True, width='stretch', alt='자동 작업 전체 목록',
                 column_config={'언제': st.column_config.TextColumn(width='medium'), '하는 일': st.column_config.TextColumn(width='large')})
    st.caption('바꾸는 곳 · launchd 서비스: scripts/launchd_services.py(등록은 사용자 승인) · Claude 예약 작업: Claude 앱의 예약 작업 '
               '(목록은 앱 저장소에서 바로 읽고, 평일 16:55 state-sync가 ops/scheduled-tasks에 백업) · Codex 자동화: Codex 앱. '
               '스킬별 역할과 결과 파일은 "운영 → 스킬"에 있습니다.')
