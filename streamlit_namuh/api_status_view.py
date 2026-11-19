"""API 연동 tab (2026-11-17): every external API's status, call volume, failures and where its data is used.

Reads scripts/api_calls.py (call log, 10-minute slots + failure rows) and scripts/api_catalog.py (hand-kept use map).
Calls no external API itself; .env is checked for key presence only, never values.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import api_calls
import api_catalog as cat
import status_cards as sc

KST = api_calls.KST
PERIODS = {'오늘': 0, '최근 7일': 6, '최근 30일': 29}
KINDS = {'network': '연결 실패', 'http': 'HTTP 오류', 'response': '응답 내용 오류'}
STATES = {'정상': 'ok', '주의': 'warn', '지연': 'alert', '실패': 'alert', '키 없음': 'warn', '호출 없음': 'off'}
SESSION = ('09:15', '15:00')  # the cadence check runs inside the session, away from the open and close edges


@st.cache_data(ttl=60, show_spinner=False)
def credentials():
    """{.env key: set?} — presence only."""
    try:
        from dotenv import dotenv_values
        import local_env
        values = dotenv_values(local_env.ENV)
    except Exception:  # noqa: BLE001 - a missing file just means no keys
        values = {}
    return {k: bool(str(v or '').strip()) for k, v in values.items()}


@st.cache_data(ttl=30, show_spinner=False)
def load(day_from, day_to):
    return api_calls.rows(day_from, day_to), api_calls.failures(day_from), api_calls.last_seen()


@st.cache_data(ttl=30, show_spinner=False)
def provider_failures(key, since):
    return api_calls.failures(since, provider=key)


def recent_totals(rows, now, minutes=30):
    """totals() of the slots that started within the last `minutes` (yesterday's late slots count after midnight)."""
    since = now - timedelta(minutes=minutes)
    picked = [r for r in rows if datetime.fromisoformat(f"{r['day']}T{r['slot']}:00").replace(tzinfo=KST) >= since]
    return totals(pd.DataFrame(picked))


def totals(frame, by='provider'):
    """Per `by` value: calls, failures, response errors, ms, bytes, last ok/fail (None when a group never had one)."""
    if frame.empty:
        return {}
    out = {}
    for key, g in frame.groupby(by):
        n = int(g['n'].sum())
        out[key] = {'n': n, 'fail': int(g['fail'].sum()), 'bad': int(g['bad'].sum()), 'ms_avg': g['ms_sum'].sum() / n if n else None,
                    'ms_max': float(g['ms_max'].max()), 'bytes': int(g['bytes'].sum()),
                    'last_ok': g['last_ok'].dropna().max() if g['last_ok'].notna().any() else None,
                    'last_fail': g['last_fail'].dropna().max() if g['last_fail'].notna().any() else None}
    return out


def state(provider, today, recent, keys, now, active=False):
    """(label, reason) from keys, the last 30 minutes, the expected cadence and today's failures. `active`: some API was
    called today with services up, so on a KRX trading day a silent API with an expected cadence is late, not idle."""
    from nh_paper_isolated import is_trading_day  # the runners' KRX holiday table: discord-notify and FRED run on holidays
    missing = [k for k in provider['keys'] if not keys.get(k)]
    if missing:
        return '키 없음', f"{', '.join(missing)} 미설정"
    if recent and recent['n'] and (recent['fail'] + recent['bad']) / recent['n'] >= 0.5:
        return '실패', f"최근 30분 {recent['n']}건 중 {recent['fail'] + recent['bad']}건 실패"
    expect, hm = provider.get('expect_min'), now.strftime('%H:%M')
    if expect and active and SESSION[0] <= hm < SESSION[1] and is_trading_day(now.date()):
        last_ok = today.get('last_ok') if today else None
        if not last_ok:
            return '지연', f'오늘 성공 없음 (장중 {expect}분 주기 예상)'
        late = (now - datetime.fromisoformat(last_ok)).total_seconds() / 60
        if late > max(3 * expect, 5):
            return '지연', f'마지막 성공 {last_ok[11:16]} · {late:,.0f}분 전 (장중 {expect}분 주기 예상)'
    last_fail = today.get('last_fail') if today else None
    if last_fail and now - datetime.fromisoformat(last_fail) <= timedelta(hours=1):
        return '주의', f"마지막 실패 {last_fail[11:16]}"
    if today and today['n']:
        return '정상', f"오늘 {today['n']:,}건"
    return '호출 없음', '오늘 기록 없음'


def provider_states(calls, keys, now):
    """{api key: (label, reason)} for every catalog API from call rows covering yesterday and today."""
    today = now.strftime('%Y-%m-%d')
    today_totals = totals(pd.DataFrame([r for r in calls if r['day'] == today]))
    recent = recent_totals(calls, now)
    return {p['key']: state(p, today_totals.get(p['key']), recent.get(p['key']), keys, now, bool(today_totals)) for p in cat.CATALOG}


def alert_lines(states):
    """Lines for the sidebar / overview 주의 항목: failing or late APIs only."""
    return [f"API {cat.name_of(key)} {label}: {why}" for key, (label, why) in states.items() if label in ('실패', '지연')]


def current_alerts(now=None):
    """alert_lines() of the live call log (cached reads; never raises into the page that shows them)."""
    try:
        now = now or datetime.now(KST)
        calls = load((now - timedelta(days=1)).strftime('%Y-%m-%d'), now.strftime('%Y-%m-%d'))[0]
        return alert_lines(provider_states(calls, credentials(), now))
    except Exception:  # noqa: BLE001 - an alert list must not break the sidebar
        return []


def token_status(provider, runtime, now):
    """'토큰 MM-DD HH:MM까지' for the newest cached token of the API, or why there is none; None for keyless APIs.
    Only the expiry field is read (ISO text or epoch milliseconds); the token itself is never shown."""
    if not provider.get('tokens'):
        return None
    expiries = []
    for pattern, field in provider['tokens']:
        for path in Path(runtime).glob(pattern):
            try:
                value = json.loads(path.read_text()).get(field)
                expiries.append(datetime.fromtimestamp(value / 1000, KST) if type(value) in (int, float)
                                else datetime.fromisoformat(value).astimezone(KST))
            except (OSError, ValueError, TypeError, AttributeError):
                continue
    if not expiries:
        return '토큰 없음 (첫 호출 때 발급)'
    last = max(expiries)  # the token in use is the newest; an old file left behind must not read as expired
    return f'토큰 {last:%m-%d %H:%M}까지' if last > now else f'토큰 만료 {last:%m-%d %H:%M} (다음 호출 때 재발급)'


def ago(stamp, now):
    if not isinstance(stamp, str) or not stamp:
        return '—'
    minutes = (now - datetime.fromisoformat(stamp)).total_seconds() / 60
    when = datetime.fromisoformat(stamp)
    text = f'{when:%H:%M}' if when.date() == now.date() else f'{when:%m/%d %H:%M}'
    return f"{text} ({minutes:,.0f}분 전)" if minutes < 120 else f"{text} ({minutes / 60:,.0f}시간 전)" if minutes < 2880 else text


def size(n):
    return '—' if not n else f'{n / 1e6:,.1f} MB' if n >= 1e6 else f'{n / 1e3:,.0f} KB'


def modified(runtime, rel):
    try:
        return datetime.fromtimestamp((Path(runtime) / rel).stat().st_mtime, KST).isoformat(timespec='seconds')
    except OSError:
        return None


def output_rows(provider, runtime, now):
    return [{'산출물': label, '파일': rel, '마지막 갱신': ago(modified(runtime, rel), now) if modified(runtime, rel) else '파일 없음'}
            for label, rel in provider['outputs']]


def screens_of(provider):
    """Top-level screens a provider feeds, in catalog order: '시장 상황 → 장중 지수 흐름' -> '시장 상황'."""
    names = (part.split('→')[0].split('(')[0].strip() for u in provider['uses'] for part in u['screen'].split(' · '))
    return ' · '.join(dict.fromkeys(n for n in names if n))


def latest_output(provider, runtime, now):
    return ago(max((m for _, rel in provider['outputs'] if (m := modified(runtime, rel))), default=None), now)


MACRO_PROVIDERS = ('fred', 'fed')  # their latest values are kept in the long-term engine's plan (longterm_macro)


def macro_rows(macro):
    """(series rows, release rows) of a longterm_macro.collect() result for the tables."""
    series = [{'지표': s['label'], '값': f"{s['value']:.2f}{s['unit']}", '기준일': s['date'],
               '1개월 변화': f"{s['chg_1m']:+.2f}" if s.get('chg_1m') is not None else '—',
               '1년 범위': f"{s['low_1y']:.2f} ~ {s['high_1y']:.2f}", '상태': '오래됨' if s.get('stale') else '정상', 'FRED 코드': sid}
              for sid, s in (macro.get('series') or {}).items()]
    events = [{'날짜': e['date'], '발표': e['label'], '출처': '연준 일정' if e['label'] == 'FOMC' else 'FRED'} for e in macro.get('events') or []]
    return series, events


def macro_data(runtime, now):
    """FRED·연준: what the long-term engine received at its last decision, and an on-demand check from this screen."""
    import vibe_longterm as LT
    plan = (LT.read_state(Path(runtime) / 'nhplug' / 'vibe-longterm', ('plan',)).get('plan') or {})
    macro, source = plan.get('macro'), f"장기 엔진 아침 판단 {(plan.get('time') or '')[5:16].replace('T', ' ')}"
    left, right = st.columns([4, 1], vertical_alignment='center')
    if right.button('지금 확인', icon=':material/sync:', key='api_macro_check', width='stretch',
                    help='FRED 시계열 4개·발표 일정과 연준 FOMC 일정을 지금 한 번 조회합니다(호출 6회). 엔진 기록에는 저장하지 않습니다.'):
        import longterm_macro
        st.session_state['api_macro_result'] = (longterm_macro.collect(now.date()), now.isoformat())
    checked = st.session_state.get('api_macro_result')
    if checked and checked[1] > (plan.get('time') or ''):  # until the engine's next decision brings newer data
        macro, source = checked[0], f'이 화면에서 지금 확인 {checked[1][11:19]}'
    if not macro:
        left.caption('엔진이 아직 거시 자료를 받은 적이 없습니다(평일 08:45 판단부터). "지금 확인"으로 바로 조회할 수 있습니다.')
        return
    errors = macro.get('errors') or []
    left.caption(f"{source} · 기준 {macro.get('as_of', '—')} · " + (f"빠진 자료: {', '.join(errors)}" if errors else '빠진 자료 없음')
                 + ' · 장기 엔진 참고 자료이며 국면·주문 규칙은 쓰지 않습니다.')
    series, events = macro_rows(macro)
    if series:
        st.dataframe(series, hide_index=True, width='stretch', alt='FRED 금리·신용 최근 값')
    if events:
        st.dataframe(events, hide_index=True, width='stretch', alt='다가오는 미국 발표')


def render(runtime):
    now = datetime.now(KST)
    section = st.segmented_control('보기', ['외부 API', '스킬·자동 작업', '리포트 발행'], default='외부 API', key='api_section',
                                   label_visibility='collapsed') or '외부 API'
    if section == '리포트 발행':
        import report_pages_view
        report_pages_view.render(runtime)
        return
    if section == '스킬·자동 작업':
        import skills_view
        st.subheader('스킬·자동 작업')
        skills_view.render(runtime)
        return
    head, pick, refresh = st.columns([3, 2, 1], vertical_alignment='bottom')
    head.subheader('API 연동')
    period = pick.segmented_control('기간', list(PERIODS), default='오늘', key='api_period', label_visibility='collapsed') or '오늘'
    if refresh.button('새로고침', icon=':material/refresh:', width='stretch', key='api_refresh'):
        load.clear()
        provider_failures.clear()
    day_to = now.strftime('%Y-%m-%d')
    day_from = (now - timedelta(days=PERIODS[period])).strftime('%Y-%m-%d')
    calls, fails, seen = load(min(day_from, (now - timedelta(days=1)).strftime('%Y-%m-%d')), day_to)  # yesterday too: 30-minute window
    frame = pd.DataFrame([r for r in calls if r['day'] >= day_from])
    fails = [f for f in fails if f['time'] >= day_from]
    period_totals, keys = totals(frame), credentials()
    st.caption(f"확인 {now:%H:%M:%S} · 실행기·수집기·대시보드가 보낸 외부 호출을 10분 단위로 셉니다. 요청·응답 내용, 쿼리 문자열, 키 값은 남기지 않습니다. "
               "기록은 각 서비스가 이 기능이 든 코드로 다시 시작한 뒤부터 쌓입니다.")
    if frame.empty:
        st.info('아직 호출 기록이 없습니다. 서비스가 새 코드로 다시 시작하면 쌓이기 시작합니다. 아래 표의 상태는 인증 키와 산출물 파일만으로 판단합니다.',
                icon=':material/info:')

    states = provider_states(calls, keys, now)
    counts = {label: sum(1 for s, _ in states.values() if s == label) for label in STATES}
    sc.row([{'label': '연동 API', 'value': f'{len(cat.CATALOG)}개', 'icon': 'hub', 'tone': 'off',
             'sub': f"정상 {counts['정상']} · 호출 없음 {counts['호출 없음']}"},
            {'label': '실패·지연·주의', 'value': f"{counts['실패']} · {counts['지연']} · {counts['주의']}", 'icon': 'error',
             'tone': 'alert' if counts['실패'] or counts['지연'] else 'warn' if counts['주의'] else 'ok',
             'sub': '30분 절반 이상 실패 · 장중 주기 3배 무응답 · 1시간 안 실패'},
            {'label': f'{period} 호출', 'value': f"{int(frame['n'].sum()):,}건" if not frame.empty else '0건', 'icon': 'swap_vert', 'tone': 'off',
             'sub': f"받은 양 {size(int(frame['bytes'].sum()))}" if not frame.empty else None},
            {'label': f'{period} 실패', 'value': f"{int(frame['fail'].sum() + frame['bad'].sum()):,}건" if not frame.empty else '0건',
             'icon': 'report', 'tone': 'alert' if not frame.empty and frame['fail'].sum() + frame['bad'].sum() else 'off',
             'sub': '연결·HTTP 실패 + 응답 내용 오류'},
            {'label': '키 없음', 'value': f"{counts['키 없음']}개", 'icon': 'key_off', 'tone': 'warn' if counts['키 없음'] else 'ok',
             'sub': '.env 키 이름만 확인'}])

    st.markdown('##### API별 연동 상태')
    table = []
    for p in cat.CATALOG:
        t, label = period_totals.get(p['key']) or {}, states[p['key']][0]
        last_ok, last_fail = seen.get(p['key'], (None, None))
        table.append({'상태': label, 'API': p['name'], '구분': p['kind'], '사유': states[p['key']][1],
                      '인증': ('필요 없음' if not p['keys'] else '설정됨' if all(keys.get(k) for k in p['keys']) else '미설정')
                              + (f" · {token}" if (token := token_status(p, runtime, now)) else ''),
                      '호출': t.get('n', 0), '실패': t.get('fail', 0) + t.get('bad', 0),
                      '실패율': (t.get('fail', 0) + t.get('bad', 0)) / t['n'] * 100 if t.get('n') else None,
                      '평균 응답(ms)': t.get('ms_avg'), '받은 양': size(t.get('bytes')), '마지막 성공': ago(last_ok, now),
                      '마지막 실패': ago(last_fail, now), '산출물 갱신': latest_output(p, runtime, now), '쓰는 화면': screens_of(p)})
    tone = lambda v: f"color:{sc.TONES[STATES[v]][0]};font-weight:700" if v in STATES else ''  # noqa: E731 - same colours as the cards
    st.dataframe(pd.DataFrame(table).style.map(tone, subset=['상태']), hide_index=True, width='stretch',
                 alt='API별 연동 상태', column_config={
                     '호출': st.column_config.NumberColumn(format='%,d'), '실패': st.column_config.NumberColumn(format='%,d'),
                     '실패율': st.column_config.ProgressColumn(format='%.1f%%', min_value=0.0, max_value=100.0),
                     '평균 응답(ms)': st.column_config.NumberColumn(format='%,.0f'),
                     '쓰는 화면': st.column_config.TextColumn(width='large')})
    st.caption('상태: 키 없음(필요한 .env 키 미설정) → 실패(최근 30분 절반 이상 실패) → 지연(장중 09:15~15:00에 예상 주기의 3배, 최소 5분 넘게 성공 없음; '
               '오늘 아무 API도 호출되지 않은 날은 판단하지 않음) → 주의(1시간 안 실패) → 정상(오늘 호출 있음) → 호출 없음 순으로 판단합니다. '
               '"호출 없음"은 장 시간 밖이거나 그 API를 쓰는 서비스가 아직 새 코드로 재시작하지 않았다는 뜻일 수 있습니다.')

    if not frame.empty:
        st.markdown(f'##### {period} 호출 흐름')
        by = 'slot' if period == '오늘' else 'day'
        flow = frame.groupby([by, 'provider'], as_index=False)[['n', 'fail', 'bad']].sum()
        flow['API'] = flow['provider'].map(cat.name_of)
        flow['실패'] = flow['fail'] + flow['bad']
        x = alt.X(f'{by}:O', title=None, axis=alt.Axis(labelAngle=0, labelOverlap=True))
        bars = alt.Chart(flow).mark_bar().encode(x=x, y=alt.Y('sum(n):Q', title='호출 수'), color=alt.Color('API:N', legend=alt.Legend(orient='top', title=None)),
                                                 tooltip=[alt.Tooltip(f'{by}:O', title='시각' if by == 'slot' else '날짜'), 'API', alt.Tooltip('n:Q', title='호출', format=','),
                                                          alt.Tooltip('실패:Q', format=',')])
        failed = flow[flow['실패'] > 0]
        marks = alt.Chart(failed).mark_tick(color='#f43f5e', thickness=3, size=14).encode(
            x=x, y=alt.Y('실패:Q'), tooltip=[alt.Tooltip(f'{by}:O', title='시각'), 'API', alt.Tooltip('실패:Q', format=',')])
        st.altair_chart((bars + marks).properties(height=240), width='stretch')
        st.caption('막대: API별 호출 수(10분 단위, 7·30일은 하루 단위) · 빨간 눈금: 그 구간의 실패 수.')

    st.markdown('##### API 상세')
    names = {p['name']: p['key'] for p in cat.CATALOG}
    chosen = st.selectbox('API', list(names), key='api_pick', format_func=lambda n: f"{n} · {states[names[n]][0]}")
    p = cat.BY_KEY[names[chosen]]
    with st.container(border=True):
        st.markdown(f":material/{p['icon']}: **{p['name']}** · {p['kind']} · [{p['site']}]({p['site']})")
        st.caption(f"인증: {p['auth']} · 키: {', '.join(f'{k} ' + ('✓' if keys.get(k) else '✗') for k in p['keys']) or '없음'}"
                   + (f" · {token}" if (token := token_status(p, runtime, now)) else ''))
        st.caption(p['note'])
    names_ = ['데이터·용도', '엔드포인트별 호출', '호출한 프로그램', '실패 이력', '산출물'] + (['최근 데이터'] if p['key'] in MACRO_PROVIDERS else [])
    uses_tab, endpoint_tab, caller_tab, fail_tab, output_tab, *data_tab = st.tabs(names_)
    if data_tab:
        with data_tab[0]:
            macro_data(runtime, now)
    mine = frame[frame['provider'] == p['key']] if not frame.empty else frame
    with uses_tab:
        st.dataframe([{'데이터': u['data'], '엔드포인트': u['endpoint'], '언제': u['when'], '보이는 화면': u['screen'],
                       '쓰는 기능': u['feature'], '용도': u['purpose']} for u in p['uses']],
                     hide_index=True, width='stretch', alt=f"{p['name']} 데이터와 용도")
    with endpoint_tab:
        if mine.empty:
            st.caption(f'{period} 호출 기록이 없습니다.')
        else:
            st.dataframe(pd.DataFrame([{'엔드포인트': e, '호출': t['n'], '실패': t['fail'], '응답 오류': t['bad'], '평균 응답(ms)': t['ms_avg'],
                                        '최대 응답(ms)': t['ms_max'], '받은 양': size(t['bytes']), '마지막 성공': ago(t['last_ok'], now),
                                        '마지막 실패': ago(t['last_fail'], now)} for e, t in totals(mine, by='endpoint').items()]).sort_values('호출', ascending=False),
                         hide_index=True, width='stretch', alt=f"{p['name']} 엔드포인트별 호출",
                         column_config={'호출': st.column_config.NumberColumn(format='%,d'), '평균 응답(ms)': st.column_config.NumberColumn(format='%,.0f'),
                                        '최대 응답(ms)': st.column_config.NumberColumn(format='%,.0f'), '엔드포인트': st.column_config.TextColumn(width='large')})
            st.caption('엔드포인트는 호스트+경로이며 종목 코드 등 숫자가 든 부분은 {code}로 묶었습니다. 키움은 #뒤가 TR(api-id), NH는 mcp:뒤가 MCP 도구·operationId입니다.')
    with caller_tab:
        if mine.empty:
            st.caption(f'{period} 호출 기록이 없습니다.')
        else:
            g = mine.groupby('caller', as_index=False)[['n', 'fail', 'bad']].sum().sort_values('n', ascending=False)
            st.dataframe([{'프로그램': cat.CALLERS.get(r.caller, r.caller), '스크립트': r.caller, '호출': int(r.n), '실패': int(r.fail + r.bad)}
                          for r in g.itertuples()], hide_index=True, width='stretch', alt=f"{p['name']}를 호출한 프로그램")
    with fail_tab:
        rows = provider_failures(p['key'], day_from)  # its own query: another API's failure storm cannot crowd it out
        if rows:
            st.dataframe([{'시각': f['time'][5:19].replace('T', ' '), '엔드포인트': f['endpoint'], '프로그램': cat.CALLERS.get(f['caller'], f['caller']),
                           '종류': KINDS.get(f['kind'], f['kind']), '상태 코드': f['status'], '내용': f['detail']} for f in rows],
                         hide_index=True, width='stretch', alt=f"{p['name']} 실패 이력")
        else:
            st.caption(f'{period} 실패 기록이 없습니다.')
    with output_tab:
        st.dataframe(output_rows(p, runtime, now), hide_index=True, width='stretch', alt=f"{p['name']} 산출물 갱신 시각")
        st.caption('호출 기록이 없어도 산출물 파일이 최근에 바뀌었으면 그 API를 쓰는 작업이 돌았다는 뜻입니다.')

    st.markdown('##### 화면별 데이터 출처')
    usage = pd.DataFrame(cat.screen_map())
    screens = sorted(usage['화면'].unique())
    picked = st.multiselect('화면', screens, key='api_screens', placeholder='모든 화면', label_visibility='collapsed')
    shown = usage[usage['화면'].isin(picked)] if picked else usage
    st.dataframe(shown.drop(columns=['화면']).sort_values(['위치', 'API']), hide_index=True, width='stretch', alt='화면별 데이터 출처',
                 column_config={'위치': st.column_config.TextColumn('화면 · 위치', width='medium')})

    st.markdown(f'##### {period} 실패 이력 (전체 API)')
    if fails:
        st.dataframe([{'시각': f['time'][5:19].replace('T', ' '), 'API': cat.name_of(f['provider']), '엔드포인트': f['endpoint'],
                       '프로그램': cat.CALLERS.get(f['caller'], f['caller']), '종류': KINDS.get(f['kind'], f['kind']), '상태 코드': f['status'],
                       '내용': f['detail']} for f in fails], hide_index=True, width='stretch', alt='전체 API 실패 이력')
        st.caption('최근 1,000건까지 보여 줍니다. 내용에는 오류 종류만 남기고 6자리 이상 숫자는 가립니다.')
    else:
        st.caption('실패 기록이 없습니다.')
