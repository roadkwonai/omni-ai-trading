"""운영 → 자동 작업. 일정과 실행 기록, 작업별 AI 실행 설정을 살펴본다."""
from __future__ import annotations

import json
import hashlib
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

import status_cards as sc
from schedules import clock, times_text

KST = ZoneInfo('Asia/Seoul')
COLORS = {'launchd 서비스': '#22c55e', 'Claude 예약 작업': '#a855f7', 'Codex 자동화': '#64748b'}
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


def date_text(value):
    """Display a recorded instant in Seoul time without inventing missing dates."""
    if not value:
        return '기록 없음'
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(KST)
        return value.strftime('%Y-%m-%d %H:%M:%S')
    return str(value)


def job_rows(jobs):
    return [{'작업': j['name'], '구분': j['kind'], '상태': j['state'], '일정': j['when'],
             'AI': j.get('provider') or '시스템', '설정 모델': j.get('model') or '확인 불가',
             '최근 실행': date_text(j.get('last_run')) if j.get('last_run') else
                        f"관측 {date_text(j['last_observed'])}" if j.get('last_observed') else '기록 없음',
             '최근 실행 모델': j.get('last_model') or ('모델 ID 미기록' if j.get('last_model_at') else
                                                   '해당 없음' if j.get('model') == '직접 호출 없음' else '확인 불가'),
             '최근 결과': j.get('last_status') or '확인 불가',
             '다음 예정': date_text(j['next_run']) if j.get('next_run') else '—',
             'AI 전환': j.get('route_state') or '—'} for j in jobs]


def sorted_jobs(jobs, order):
    by_name = sorted(jobs, key=lambda job: (job['name'].casefold(), job['kind'], job['key']))
    if order == '이름순':
        return by_name

    def recency(job):
        started = job.get('last_run')
        recorded = started or job.get('last_observed')
        if recorded is None:
            return False, float('-inf')
        if recorded.tzinfo is None:
            recorded = recorded.replace(tzinfo=KST)
        return started is not None, recorded.timestamp()

    return sorted(by_name, key=recency, reverse=True)


def _route_form(job, runtime):
    import schedule_dispatch as D

    reason = D.eligibility(job)
    st.markdown('##### AI 실행 설정')
    if reason:
        st.caption(reason)
        return
    route = job.get('route') or {}
    if route.get('reason'):
        st.warning(route['reason'])
    providers = ['기존 실행 방식', *D.OPTIONS]
    current_provider = route.get('provider') or '기존 실행 방식'
    if current_provider not in providers:
        providers.append(current_provider)
    identity = f"{job['kind']}:{job['key']}"
    provider = st.selectbox('실행 AI', providers, index=providers.index(current_provider), key=f'sched_provider_{identity}')
    model = ''
    if provider != '기존 실행 방식':
        choices = list(D.OPTIONS.get(provider, ()))
        current_model = route.get('model', '') if provider == current_provider else ''
        if current_model and current_model not in choices:
            choices.insert(0, current_model)
        choices.append('직접 입력')
        selected = st.selectbox('모델', choices, index=choices.index(current_model) if current_model in choices else 0,
                                key=f'sched_model_{identity}_{provider}')
        model = st.text_input('모델 이름', value=current_model if selected == '직접 입력' else '',
                              key=f'sched_model_custom_{identity}_{provider}') if selected == '직접 입력' else selected
    st.caption('변경을 저장한 뒤 Claude 앱의 기존 예약을 일시 정지하면 다음 예정 시각부터 선택한 AI로 실행됩니다. '
               '기존 방식으로 돌아갈 때는 여기에 저장된 전환을 해제한 뒤 Claude 앱에서 예약을 다시 켜세요.')
    if st.button('AI 실행 설정 저장', key=f'sched_save_{identity}', type='primary'):
        try:
            D.set_route(runtime, job['key'], '' if provider == '기존 실행 방식' else provider, model.strip())
        except (ValueError, OSError) as exc:
            st.error(str(exc))
        else:
            st.toast('AI 실행 설정을 저장했습니다.')
            st.rerun()


def job_detail(job, runtime):
    import schedules as S

    a, b, c = st.columns(3)
    a.metric('등록 상태', job['state'])
    b.metric('최근 실행', date_text(job.get('last_run')))
    c.metric('최근 결과', job.get('last_status') or '확인 불가')
    st.write(f"**일정** {job['when']} · 한국 시간")
    if job.get('next_run'):
        st.write(f"**다음 예정** {date_text(job['next_run'])}")
    st.write(f"**실행 방식** {job.get('provider') or '시스템'} · **설정 모델** {job.get('model') or '확인 불가'}")
    if job.get('model_detail'):
        st.caption(job['model_detail'])
    if job.get('last_model_at'):
        used = job.get('last_model') or '응답 확인'
        st.write(f"**최근 모델 사용** {used} · {date_text(job['last_model_at'])}")
        st.caption(job['last_model_source'])
    if job.get('what'):
        st.caption(job['what'])
    st.caption(f"{job['kind']} · {job['key']}")
    _route_form(job, runtime)
    st.markdown('##### 실행 이력')
    history = S.job_history(job, runtime=runtime)
    if not history:
        st.info('확인 가능한 실행 이력이 없습니다.')
        return
    rows = [{'시작': date_text(row.get('started')), '종료·최근 기록': date_text(row.get('finished') or row.get('observed')),
             '상태': row.get('status') or '확인 불가', 'AI': row.get('provider') or '확인 불가',
             '실행 모델': row.get('model') or '확인 불가', '기록 출처': row.get('source') or '확인 불가'} for row in history]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch', alt='예약 작업 실행 이력')
    st.caption(f'보관된 기록 {len(rows)}건 · 시간은 한국 시간입니다.')
    reports = [row for row in history if row.get('result')]
    if reports:
        picked = st.selectbox('실행 결과 보기', range(len(reports)), key=f"sched_result_{job['key']}",
                              format_func=lambda index: date_text(reports[index]['started']))
        st.text(reports[picked]['result'])


def migration_dialog(runtime):
    """Prepare and inspect a portable schedule migration."""
    import schedule_bundle as bundle
    import schedule_migration as migration

    st.caption('예약 목록과 일정, 지시문, 모델 설정을 파일로 보관하고 대상 계정의 이전 계획을 확인합니다.')
    try:
        exported = bundle.encode_bundle(runtime)
    except (ValueError, OSError) as exc:
        st.error(f'예약 목록을 준비하지 못했습니다. {exc}')
    else:
        st.download_button('예약 목록 내려받기', exported, file_name='omni-schedules.json', mime='application/json',
                           key='sched_export', icon=':material/download:')

    upload = st.file_uploader('예약 목록 또는 서명된 인수 영수증', type=['json'], key='sched_import',
                              help='다른 기기에서 실행권을 인수하려면 원본의 서명된 영수증을 올리세요.')
    uploaded = None
    if upload:
        try:
            uploaded = json.loads(upload.getvalue())
        except (ValueError, UnicodeError):
            st.error('예약 JSON을 읽을 수 없습니다.')
            return
    receipt = isinstance(uploaded, dict) and 'transfer_id' in uploaded
    source_key = st.text_input('원본 기기에서 확인한 서명 지문', key='sched_receipt_signer').strip() if receipt else ''
    try:
        profiles = migration.profiles(runtime)
    except (ValueError, OSError) as exc:
        st.error(f'AI 실행 프로필을 읽지 못했습니다. {exc}')
        return
    if not profiles:
        st.info('대상 AI 실행 프로필을 먼저 등록해 주세요. `omni schedule profile-add <제공자> <별칭> --model <모델-ID>`')
        return
    options = {item['id']: item for item in profiles}
    target = st.selectbox('이전할 AI 계정', options, key='sched_migrate_target',
                          format_func=lambda key: f"{key} · {options[key].get('provider', '')} · {options[key].get('model', '')}",
                          required=True)
    if st.button('이전 계획 확인', key='sched_migrate_plan', type='primary'):
        try:
            if uploaded is not None and not receipt:
                bundle.validate_bundle(uploaded)
            plan = (migration.create_plan(runtime, target, receipt=uploaded, source_key=source_key)
                    if receipt else migration.create_plan(runtime, target, bundle=uploaded))
        except (ValueError, OSError, UnicodeError, sqlite3.Error) as exc:
            st.error(f'이전 계획을 만들지 못했습니다. {exc}')
        else:
            st.session_state['sched_migration_plan'] = plan
            st.session_state['sched_migration_inputs'] = (target, hashlib.sha256(upload.getvalue()).hexdigest() if upload else None, source_key)
            st.session_state.pop('sched_migration_result', None)

    plan = st.session_state.get('sched_migration_plan')
    if not plan:
        return
    current_inputs = (target, hashlib.sha256(upload.getvalue()).hexdigest() if upload else None, source_key)
    if current_inputs != st.session_state.get('sched_migration_inputs'):
        st.info('대상 계정 또는 파일이 바뀌었습니다. 이전 계획을 다시 확인해 주세요.')
        return
    st.markdown('##### 이전 계획')
    st.caption(f"계획 {plan['id']} · 대상 {plan.get('target', target)} · {plan.get('state', '')}")
    rows = plan.get('rows') or []
    if rows:
        visible = [{'작업': row.get('name'), '일정': row.get('when'), '현재 모델': row.get('source_model'),
                    '최근 사용 모델': row.get('source_last_model'), '대상 모델': row.get('target_model'),
                    '다음 예정': date_text(row.get('next_run')), '상태': row.get('status'), '사유': row.get('reason')}
                   for row in rows]
        st.dataframe(pd.DataFrame(visible), hide_index=True, width='stretch', alt='예약 작업 이전 계획')
    else:
        st.info('이전 계획에 포함된 작업이 없습니다.')
    st.caption('작업별 일정, 모델과 대기 사유를 확인한 뒤 적용할 수 있습니다.')
    if any(row.get('status') == '대체 모델 검증 필요' for row in rows):
        st.info('비식별 검증 자료로 대체 모델을 확인할 수 있습니다. `omni schedule validate-model` 실행 뒤 상태 확인을 누르세요.')
    a, b, c = st.columns(3)
    for clicked, operation, label in (
        (a.button('이전 적용', key='sched_migrate_apply'), migration.apply, '적용'),
        (b.button('상태 확인', key='sched_migrate_verify'), migration.verify, '확인'),
        (c.button('이전 되돌리기', key='sched_migrate_rollback'), migration.rollback, '되돌리기'),
    ):
        if clicked:
            try:
                st.session_state['sched_migration_result'] = operation(runtime, plan['id'])
            except (ValueError, OSError, sqlite3.Error) as exc:
                st.error(f'{label} 실패. {exc}')
    result = st.session_state.get('sched_migration_result')
    if result:
        st.markdown('##### 처리 결과')
        st.json(result)


def _timeline(jobs, day):
    import schedules as S
    plan = S.day_plan(jobs, day)
    st.markdown(f"##### {day:%m/%d}({DAY_NAMES[day.weekday()]}) 일정 · 시작 {len(plan)}번")
    if not plan:
        st.info('이날 시작하는 작업이 없습니다. 상시 서비스는 전체 목록에서 확인할 수 있습니다.', icon=':material/event_busy:')
        return
    dots = pd.DataFrame([{'시각': datetime.combine(day, datetime.min.time()).replace(hour=h, minute=m), '구분': j['kind'],
                          '작업': j['name'], '상태': j['state']} for h, m, j in plan])
    chart = alt.Chart(dots, mark=alt.MarkDef(type='tick', thickness=3, size=22))
    chart = chart.encode(
        x=alt.X('시각:T', title=None, axis=alt.Axis(format='%H:%M', tickCount=12)),
        y=alt.Y('구분:N', title=None, sort=list(S.KINDS)),
        color=alt.Color('구분:N', scale=alt.Scale(domain=list(COLORS), range=list(COLORS.values())), legend=None),
        tooltip=[alt.Tooltip('시각:T', format='%H:%M'), '구분', '작업', '상태'])
    chart = chart.properties(height=150)
    st.altair_chart(chart, width='stretch')
    st.dataframe(pd.DataFrame(day_rows(jobs, day)), hide_index=True, width='stretch', alt='그날 자동 작업 일정')


def render(runtime=None):
    import dialogs
    import schedules as S
    from automation_status import processes

    now = datetime.now(KST)
    jobs = S.all_jobs(processes(), runtime=runtime)
    st.subheader('자동 작업')
    st.caption('예약 작업과 서비스의 일정, 실행 모델, 최근 실행을 확인합니다. 모든 시각은 한국 시간입니다.')
    by = {kind: [job for job in jobs if job['kind'] == kind] for kind in S.KINDS}
    always = [j for j in by['launchd 서비스'] if j['always']]
    claude, codex = by['Claude 예약 작업'], by['Codex 자동화']
    nxt = S.next_start(jobs, now)
    sc.row([{'label': '상시 서비스', 'value': f"{sum(j['state'] == '실행 중' for j in always)}/{len(always)} 실행 중", 'icon': 'dns',
             'tone': 'ok' if all(j['state'] == '실행 중' for j in always) else 'warn', 'sub': 'launchd 서비스'},
            {'label': 'Claude 예약', 'value': f"{sum(j['state'] == '등록됨' for j in claude)}/{len(claude)} 켜짐", 'icon': 'smart_toy',
             'tone': 'ai'},
            {'label': 'Codex 자동화', 'value': f"{sum(j['state'] in ('실행 중', '등록됨') for j in codex)}/{len(codex)} 켜짐", 'icon': 'schedule',
             'tone': 'off'},
            {'label': '다음 시작', 'value': nxt[0].strftime('%H:%M') if nxt else '—', 'icon': 'alarm', 'tone': 'ai',
             'sub': (f"{nxt[0]:%m/%d}({DAY_NAMES[nxt[0].weekday()]}) · {nxt[1]['name']}" if nxt else '예정 확인 불가')}])

    st.markdown('##### 전체 목록')
    left, right, sort, active = st.columns([2, 1.2, 1.2, 1], vertical_alignment='bottom')
    kinds = left.segmented_control('구분', ['전체', *S.KINDS], default='전체', key='sched_kind') or '전체'
    query = right.text_input('작업 검색', key='sched_search', placeholder='작업 이름 또는 모델')
    order = sort.segmented_control('정렬', ['최근 실행순', '이름순'], default='최근 실행순', key='sched_sort',
                                   help='최근 실행순에서는 실제 실행 기록을 먼저 표시하고 관측 기록만 있는 작업을 뒤에 표시합니다.')
    active_only = active.checkbox('운용 중인 작업만', key='sched_active_only')
    visible = [j for j in jobs if kinds in ('전체', j['kind']) and
               (not active_only or j['state'] not in S.NON_ACTIVE) and
               query.casefold() in ' '.join(str(j.get(k) or '') for k in ('name', 'key', 'provider', 'model', 'last_model')).casefold()]
    if visible:
        visible = sorted_jobs(visible, order)
        st.dataframe(pd.DataFrame(job_rows(visible)), hide_index=True, width='stretch', alt='자동 작업 전체 목록')
        choices = {f"{job['kind']}:{job['key']}": job for job in visible}
        selected = st.selectbox('상세 이력을 볼 작업', choices,
                                format_func=lambda key: f"{choices[key]['name']} · {choices[key]['kind']}",
                                key='sched_detail_pick', required=True)
        if st.button('작업 상세 · 실행 이력', key='sched_detail', icon=':material/history:'):
            job = choices[selected]
            dialogs.open_dialog(job['name'], job_detail, job, runtime, icon=':material/event_note:')
    else:
        st.info('조건에 맞는 작업이 없습니다.')
    st.caption('최근 실행 모델은 AI 사용 기록을 기준으로 표시합니다. 서비스 실행과 모델 사용 시각은 작업 상세에서 확인할 수 있습니다.')

    if st.button('예약 작업 이전', key='sched_migrate', icon=':material/swap_horiz:'):
        dialogs.open_dialog('예약 작업 이전', migration_dialog, runtime, icon=':material/swap_horiz:')

    st.markdown('##### 날짜별 일정')
    day = st.date_input('날짜', now.date(), key='sched_day', help='그날 시작하는 작업을 시각 순으로 봅니다.')
    _timeline(jobs, day)
