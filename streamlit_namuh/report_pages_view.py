"""API 연동 → 리포트 발행 (user request 2026-11-17): which reports become HTML pages and go to Discord, when, and what
happened to each one: browse every generated page (search, kind, year/month/day), open it, and regenerate / republish /
resend a failed one with the stored reason. Reads scripts/report_pages.py records; buttons call its retry()."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

import report_pages as rp
import status_cards as sc

ACTIONS = {'generate': 'HTML 생성', 'publish': 'Pages 게시', 'send': 'Discord 발송', 'send_test': '발송 테스트',
           'requeue': '자동 재시도 예약', 'repo': '저장소·Pages 설정'}


@st.cache_data(ttl=20, show_spinner=False)
def load():
    return rp.records(), rp.history(limit=3000)


@st.cache_data(ttl=60, show_spinner=False)
def pages_settings():
    return rp.public(rp.settings())


def status(value):
    return rp.STATUS_KO.get(value, value or '—')


def reason(row):
    """The first failure reason of the three steps, for the table."""
    for step, key in (('생성', 'gen_error'), ('게시', 'publish_error'), ('발송', 'send_error')):
        if row.get(key):
            return f'{step}: {row[key]}'
    return ''


def table(rows):
    return pd.DataFrame([{'id': r['id'], '날짜': rp.sort_date(r['key']), '기간': rp.period_label(r['kind'], r['key']),
                          '종류': rp.BY_KEY[r['kind']]['name'] if r['kind'] in rp.BY_KEY else r['kind'], '제목': r['title'] or r['id'],
                          'HTML 생성': status(r['gen_status']), 'Pages 게시': status(r['publish_status']), 'Discord 발송': status(r['send_status']),
                          '채널': 'LONGTERM' if r['channel'] == 'longterm' else 'REALTIME', '링크': r['url'] or None, '사유': reason(r),
                          '검색': f"{r['title'] or ''} {r['summary'] or ''} {r['text'] or ''}".lower()} for r in rows])


def filtered(frame, query, kinds, year, month, day):
    if frame.empty:
        return frame
    out = frame
    if query:
        out = out[out['검색'].str.contains(query.strip().lower(), regex=False)]
    if kinds:
        out = out[out['종류'].isin(kinds)]
    if year != '전체':
        out = out[out['날짜'].str.startswith(year)]
    if month != '전체':
        out = out[out['날짜'].str.slice(5, 7) == f'{int(month):02d}']
    if day:
        out = out[out['날짜'] == day.isoformat()]
    return out.sort_values(['날짜', '종류'], ascending=[False, True])


def preview(row):
    """The local page with its stylesheet inlined (the iframe cannot follow ../../assets)."""
    path = rp.SITE / (row['html_path'] or '')
    try:
        page = path.read_text(encoding='utf-8')
        css = (rp.SITE / 'assets' / 'report.css').read_text(encoding='utf-8')
    except OSError:
        return None
    import re
    return re.sub(r'<link rel="stylesheet" href="[^"]*report\.css[^"]*">', lambda _: f'<style>{css}</style>', page)


def detail(row, settings):
    with st.container(border=True):
        st.markdown(f"**{row['title'] or row['id']}** · {rp.period_label(row['kind'], row['key'])}")
        st.caption(f"원본 {row['source']} · HTML {rp.SITE / (row['html_path'] or '')}")
        sc.row([{'label': 'HTML 생성', 'value': status(row['gen_status']), 'icon': 'description',
                 'tone': 'ok' if row['gen_status'] == 'ok' else 'alert', 'sub': (row['generated_at'] or '')[5:16].replace('T', ' ')},
                {'label': 'Pages 게시', 'value': status(row['publish_status']), 'icon': 'cloud_upload',
                 'tone': {'ok': 'ok', 'failed': 'alert'}.get(row['publish_status'], 'warn'), 'sub': (row['published_at'] or '')[5:16].replace('T', ' ') or None},
                {'label': 'Discord 발송', 'value': status(row['send_status']), 'icon': 'send',
                 'tone': {'sent': 'ok', 'sent_text': 'ok', 'failed': 'alert'}.get(row['send_status'], 'warn'),
                 'sub': ' · '.join(x for x in ((row['sent_at'] or '')[5:16].replace('T', ' '), f"시도 {row['send_attempts'] or 0}회") if x)}])
        for label, key in (('생성 실패', 'gen_error'), ('게시 상태', 'publish_error'), ('발송 실패', 'send_error')):
            if row.get(key):
                st.warning(f'{label}: {row[key]}', icon=':material/error:')
        a, b, c, d = st.columns(4)
        if a.button('다시 생성', icon=':material/refresh:', key=f"rp_gen_{row['id']}", width='stretch',
                    help='원본 .md·.json에서 HTML을 다시 만들고 목록 페이지를 갱신합니다.'):
            st.toast(f"다시 생성: {rp.retry('generate', row['id'])}")
            load.clear()
            st.rerun()
        if b.button('다시 게시', icon=':material/cloud_upload:', key=f"rp_pub_{row['id']}", width='stretch',
                    disabled=bool(settings['missing']), help='사이트를 커밋하고 REPORT_PAGES_PUSH=on이면 GitHub Pages로 푸시합니다.'):
            st.toast(f"다시 게시: {rp.retry('publish', row['id'])}")
            load.clear()
            st.rerun()
        if c.button('다시 발송', icon=':material/send:', key=f"rp_send_{row['id']}", width='stretch',
                    disabled=settings['ready'] and row['publish_status'] != 'ok' or row['gen_status'] != 'ok',
                    help='Pages가 켜져 있으면 게시된 페이지 링크를, 꺼져 있으면 리포트 본문을 Discord에 다시 보냅니다.'):
            st.toast(f"다시 발송: {rp.retry('send', row['id'])}")
            load.clear()
            st.rerun()
        if row['url'] and row['publish_status'] == 'ok':
            d.link_button('Pages에서 열기', row['url'], icon=':material/open_in_new:', width='stretch')
        else:
            d.button('Pages에서 열기', icon=':material/open_in_new:', disabled=True, key=f"rp_open_{row['id']}", width='stretch',
                     help='아직 게시되지 않았습니다. 아래 미리보기로 확인하세요.')
        if row['gen_status'] == 'ok' and (page := preview(row)):
            with st.expander('HTML 미리보기', icon=':material/visibility:'):
                st.components.v1.html(page, height=820, scrolling=True)


def schedule_rows(records):
    last = {}
    for r in records:
        cur = last.setdefault(r['kind'], {'gen': '', 'sent': ''})
        cur['gen'] = max(cur['gen'], r['generated_at'] or '')
        cur['sent'] = max(cur['sent'], r['sent_at'] or '')
    return [{'리포트': s['name'], 'HTML·발송': '대상', 'Discord 채널': rp.CHANNELS[s['channel']], '생성 일정': s['when'], '만드는 작업': s['job'],
             '원본 파일': f"{s['folder'].removeprefix('nhplug/')}/{s['pattern'].pattern.replace('(?P<key>', '(')}", 'HTML 템플릿': s['template'],
             '마지막 생성': (last.get(s['key'], {}).get('gen') or '—')[5:16].replace('T', ' '),
             '마지막 링크 발송': (last.get(s['key'], {}).get('sent') or '—')[5:16].replace('T', ' ')} for s in rp.REPORTS]


def render(runtime):
    settings = pages_settings()
    records, events = load()
    head, refresh = st.columns([5, 1], vertical_alignment='bottom')
    head.subheader('리포트 발행')
    if refresh.button('새로고침', icon=':material/refresh:', width='stretch', key='rp_refresh'):
        load.clear()
        pages_settings.clear()
    st.caption('리포트(.md)가 생기거나 바뀌면 디스코드 알림 서비스(30초 주기)가 HTML을 만들고 → GitHub Pages에 올린 뒤 → 페이지가 열리는 것을 확인하고 '
               'Discord에 제목·요약·링크를 보냅니다. 체결·주문 계획·NAV 같은 짧은 매매 알림은 지금처럼 텍스트로 바로 갑니다.')
    failures = [r for r in records if 'failed' in (r['gen_status'], r['publish_status'], r['send_status'])]
    push = '푸시 켜짐' if settings['push'] else '준비만 됨(푸시 꺼짐)'
    sc.row([{'label': 'HTML 대상 리포트', 'value': f'{len(rp.REPORTS)}종', 'icon': 'article', 'tone': 'off', 'sub': '아래 "발행 대상·일정"'},
            {'label': '만든 HTML', 'value': f"{sum(r['gen_status'] == 'ok' for r in records)}개", 'icon': 'description', 'tone': 'off',
             'sub': f"게시 {sum(r['publish_status'] == 'ok' for r in records)} · 링크 발송 {sum(r['send_status'] == 'sent' for r in records)}"},
            {'label': '실패', 'value': f'{len(failures)}개', 'icon': 'error', 'tone': 'alert' if failures else 'ok', 'sub': '생성·게시·발송 중 하나라도'},
            {'label': 'GitHub Pages', 'value': settings['repo'] or '저장소 미설정', 'icon': 'cloud',
             'tone': 'ok' if settings['ready'] else 'warn', 'sub': f"{push} · 토큰 {'설정됨' if settings['token_set'] else '없음'}"}])
    if settings['missing'] or not settings['push']:
        st.info(('설정이 필요합니다: ' + ', '.join(settings['missing']) + '. ' if settings['missing'] else '')
                + '.env에 REPORT_PAGES_REPO(owner/name)와 REPORT_PAGES_PUSH=on을 넣으면 .envrc의 GH_TOKEN으로 커밋·푸시합니다. '
                  '그 전에는 HTML을 로컬에서만 만들고 아래 미리보기로 봅니다. GitHub Pages는 공개 주소라 링크를 아는 사람은 볼 수 있습니다(검색 노출은 막음).',
                icon=':material/info:')

    browse, history_tab, plan_tab = st.tabs(['리포트 목록', '발송·작업 이력', '발행 대상·일정'])
    with browse:
        frame = table(records)
        years = sorted({d[:4] for d in frame['날짜']}, reverse=True) if not frame.empty else []
        q, k = st.columns([2, 2])
        query = q.text_input('검색어', placeholder='제목·요약·본문', key='rp_q')
        kinds = k.multiselect('종류', [s['name'] for s in rp.REPORTS], key='rp_kinds', placeholder='모든 종류')
        y, m, d = st.columns(3)
        year = y.selectbox('연도', ['전체', *years], key='rp_year')
        month = m.selectbox('월', ['전체', *[str(i) for i in range(1, 13)]], key='rp_month', format_func=lambda v: v if v == '전체' else f'{v}월')
        day = d.date_input('날짜', value=None, key='rp_day', min_value=date(2026, 1, 1), format='YYYY-MM-DD')
        shown = filtered(frame, query, kinds, year, month, day)
        st.caption(f'{len(shown)}개 / 전체 {len(frame)}개 · 행을 고르면 아래에 상태·실패 사유·다시 생성/게시/발송·미리보기가 나옵니다.')
        if shown.empty:
            st.caption('조건에 맞는 리포트가 없습니다.')
            return_row = None
        else:
            picked = st.dataframe(shown.drop(columns=['검색', 'id']), hide_index=True, width='stretch', key='rp_table', on_select='rerun',
                                  selection_mode='single-row', alt='HTML 리포트 목록',
                                  column_config={'링크': st.column_config.LinkColumn('링크', display_text='열기'),
                                                 '제목': st.column_config.TextColumn(width='large'), '사유': st.column_config.TextColumn(width='medium')})
            rows = picked.selection.rows if picked and picked.selection else []
            return_row = shown.iloc[rows[0]]['id'] if rows else None
        if return_row:
            detail(next(r for r in records if r['id'] == return_row), settings)

    with history_tab:
        kind = st.segmented_control('보기', ['Discord 발송', '실패만', '전체 작업'], default='Discord 발송', key='rp_hist') or 'Discord 발송'
        rows = [e for e in events if (kind == '전체 작업') or (kind == '실패만' and not e['ok'])
                or (kind == 'Discord 발송' and e['action'] in ('send', 'send_test'))]
        if rows:
            st.dataframe([{'시각': e['time'][5:19].replace('T', ' '), '리포트': e['title'] or e['report_id'], '작업': ACTIONS.get(e['action'], e['action']),
                           '결과': '성공' if e['ok'] else '실패', '내용': e['detail']} for e in rows],
                         hide_index=True, width='stretch', alt='리포트 발송·작업 이력',
                         column_config={'내용': st.column_config.TextColumn(width='large')})
            st.caption('최근 3,000건. 실패한 리포트는 "리포트 목록"에서 골라 다시 생성·게시·발송할 수 있습니다. 내용에는 토큰·웹훅 주소·긴 숫자를 남기지 않습니다.')
        else:
            st.caption('기록이 없습니다.')

    with plan_tab:
        st.markdown('##### HTML로 만들어 링크를 보내는 리포트')
        st.dataframe(schedule_rows(records), hide_index=True, width='stretch', alt='HTML 발행 대상과 일정')
        st.markdown('##### HTML로 만들지 않는 것')
        st.dataframe([{'항목': n['name'], 'Discord': n['channel'], '처리': n['how'], '이유': n['why']} for n in rp.NOT_PUBLISHED],
                     hide_index=True, width='stretch', alt='HTML 대상이 아닌 알림')
        st.caption(f"Pages: {settings['url'] or '주소 미정(REPORT_PAGES_REPO 설정 뒤 자동)'} · 브랜치 {settings['branch']} · 사이트 폴더 {rp.SITE} · "
                   f"기록 {rp.DB} · 페이지가 {rp.LIVE_WAIT_MIN}분 넘게 열리지 않으면 발송 실패로 남깁니다.")
