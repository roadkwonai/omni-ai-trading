"""API 연동 tab (2026-11-17): every external API's status, call volume, failures and where its data is used.

Reads scripts/api_calls.py (call log, 10-minute slots + failure rows) and scripts/api_catalog.py (hand-kept use map).
Optional provider data is fetched only through explicit controls in the detail view.
"""
from __future__ import annotations

import json
import logging
import random
import re
import sqlite3
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitAPIException

import api_calls
import api_catalog as cat
import api_load
import liner_history
import liner_jobs
import status_cards as sc

KST = api_calls.KST
logger = logging.getLogger(__name__)
PERIODS = {'오늘': 0, '최근 7일': 6, '최근 30일': 29}
KINDS = {'network': '연결 실패', 'http': 'HTTP 오류', 'response': '응답 내용 오류'}
STATES = {'정상': 'ok', '주의': 'warn', '지연': 'alert', '실패': 'alert', '키 없음': 'warn', '호출 없음': 'off', '미사용': 'off'}
SESSION = ('09:15', '15:00')  # the cadence check runs inside the session, away from the open and close edges


@st.cache_data(ttl=60, show_spinner=False)
def credentials():
    """{.env key: set?} — presence only."""
    try:
        from dotenv import dotenv_values
        import local_env
        values = dotenv_values(local_env.ENV, interpolate=False)
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
        return ('미사용' if provider.get('optional') else '키 없음'), f"{', '.join(missing)} 미설정"
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


def when(stamp: str | None) -> str:
    """'오늘 12:54', '11-23 09:10' — the year only when it is not this one."""
    try:
        time = datetime.fromisoformat(stamp).astimezone(KST)
    except (TypeError, ValueError):
        return '시각 없음'
    now = datetime.now(KST)
    return f'오늘 {time:%H:%M}' if time.date() == now.date() else f'{time:%m-%d %H:%M}' if time.year == now.year else f'{time:%Y-%m-%d}'


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


@st.cache_data(ttl=300, show_spinner=False)
def liner_issues():
    import trading_journal

    today = datetime.now(KST).date()
    records = trading_journal.days((today - timedelta(days=3)).isoformat(), today.isoformat())
    if not records:
        return None, []
    record = records[-1]
    headlines, _ = trading_journal.rank_issues(record)
    return record['day'], headlines[:5]


def close_liner_search() -> None:
    st.session_state.pop('quick_liner_open', None)


@st.dialog('AI 검색', width=1440, icon=':material/search:', on_dismiss=close_liner_search)
def open_liner_search() -> None:
    import dialogs

    dialogs.wide()  # the pop-ups' shared frame and width
    # The panel fills the screen. Wide and tall enough: the chat takes the height that is left, the side column scrolls by
    # itself and both end at the same line. Narrow or low: one column, the pop-up scrolls.
    st.html("""
    <style>
    [data-testid="stDialog"] [role="dialog"]:has(.st-key-quick_liner_panel) {
        display: flex;
        flex-direction: column;
        height: calc(100dvh - 3.6rem);  /* Streamlit leaves 29px above a dialog: the same below */
        max-height: calc(100dvh - 3.6rem);
        min-height: 0 !important;
        overflow: hidden;
    }
    [data-testid="stDialog"] [role="dialog"] > div:has(.st-key-quick_liner_panel) {
        flex: 1 1 auto;
        min-height: 0;
        overflow-y: auto !important;
        scrollbar-gutter: stable;
    }
    .st-key-quick_liner_chat_history {
        overflow-y: auto !important;
    }
    .st-key-quick_liner_side button,
    .st-key-quick_liner_side button > div {
        justify-content: flex-start;
        text-align: left;
    }
    .st-key-quick_liner_side, .st-key-quick_liner_main, .st-key-quick_liner_talk_list, .st-key-quick_liner_chat_history { scrollbar-width: thin; }
    /* the style blocks themselves take no row, and every card's title sits at the same height */
    [data-testid="stDialog"] [role="dialog"]:has(.st-key-quick_liner_panel) [data-testid="stElementContainer"]:has(style) { display: none; }
    .st-key-quick_liner_panel [data-testid="stHeading"] h3 { padding-top: 0; }
    @media (min-width: 961px) and (min-height: 720px) {
        [data-testid="stDialog"] [role="dialog"] > div:has(.st-key-quick_liner_body) {
            overflow-y: hidden !important;
            scrollbar-gutter: auto;
        }
        /* every wrapper down to the two columns passes the height on */
        [data-testid="stDialog"] [role="dialog"] > div :has(.st-key-quick_liner_body),
        [data-testid="stDialog"] [role="dialog"] > div:has(.st-key-quick_liner_body),
        .st-key-quick_liner_body,
        .st-key-quick_liner_body > [data-testid="stLayoutWrapper"] {
            display: flex;
            flex-direction: column;
            flex: 1 1 auto;
            min-height: 0;
            height: auto;
        }
        .st-key-quick_liner_body > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] {
            flex: 1 1 auto;
            min-height: 0;
            flex-wrap: nowrap;  /* a wrapping row takes its columns' own height */
            align-items: stretch;
        }
        .st-key-quick_liner_body > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
            display: flex;
            flex-direction: column;
            min-height: 0;
        }
        [data-testid="stColumn"] > [data-testid="stVerticalBlock"]:has(> * > .st-key-quick_liner_nav),
        [data-testid="stLayoutWrapper"]:has(> .st-key-quick_liner_nav),
        [data-testid="stColumn"] > [data-testid="stVerticalBlock"]:has(> * > .st-key-quick_liner_main),
        [data-testid="stColumn"] > [data-testid="stVerticalBlock"]:has(> * > .st-key-quick_liner_side),
        [data-testid="stLayoutWrapper"]:has(> .st-key-quick_liner_main),
        [data-testid="stLayoutWrapper"]:has(> .st-key-quick_liner_side) {
            flex: 1 1 auto;
            min-height: 0;
        }
        .st-key-quick_liner_main, .st-key-quick_liner_side {
            height: 100%;
            overflow-y: auto;
        }
        .st-key-quick_liner_main > *, .st-key-quick_liner_side > *, .st-key-quick_liner_nav > * { flex: 0 0 auto; }
        /* the list scrolls, what is above and below it stays */
        .st-key-quick_liner_nav { height: 100%; flex-wrap: nowrap; }
        .st-key-quick_liner_nav > [data-testid="stLayoutWrapper"]:has(> .st-key-quick_liner_talk_list) { flex: 0 1 auto; min-height: 6rem; }
        .st-key-quick_liner_talk_list { height: 100%; overflow-y: auto; flex-wrap: nowrap; }
        .st-key-quick_liner_talk_list > * { flex: 0 0 auto; }
        .st-key-quick_liner_main > [data-testid="stLayoutWrapper"]:has(> .st-key-quick_liner_chat_card) {
            flex: 1 0 26rem;
        }
        .st-key-quick_liner_chat_card > [data-testid="stLayoutWrapper"]:has(> .st-key-quick_liner_chat_history) {
            flex: 1 1 0;
            height: auto;
            min-height: 10rem;
        }
        .st-key-quick_liner_chat_history {
            height: 100% !important;
            max-height: none !important;
        }
    }
    @media (max-width: 960px), (max-height: 719px) {
        .st-key-quick_liner_talk_list { max-height: 11rem; overflow-y: auto; flex-wrap: nowrap; }
        .st-key-quick_liner_talk_list > * { flex: 0 0 auto; }
    }
    @media (max-width: 960px) {
        .st-key-quick_liner_body > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] { flex-direction: column; }
        .st-key-quick_liner_body > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
            width: 100%;
            flex: 0 0 auto;
        }
    }
    @media (max-width: 960px), (max-height: 719px) {
        [data-testid="stLayoutWrapper"]:has(> .st-key-quick_liner_chat_history),
        .st-key-quick_liner_chat_history {
            height: clamp(12rem, 45dvh, 30rem) !important;
            max-height: clamp(12rem, 45dvh, 30rem) !important;
            flex: 0 0 auto;
        }
        .st-key-quick_liner_chat_history:has(.st-key-quick_liner_empty),
        [data-testid="stLayoutWrapper"]:has(> .st-key-quick_liner_chat_history):has(.st-key-quick_liner_empty) {
            height: 9rem !important;
            max-height: 9rem !important;
        }
    }
    </style>
    """)
    with st.container(key='quick_liner_panel'):
        liner_search(True, prefix='quick_liner')


def markdown_label(text: str) -> str:
    return markdown_escape(' '.join(text.split()))


def markdown_escape(text: str) -> str:
    return re.sub(r'([\\\x60*_\[\]{}<>&!~:$|#])', r'\\\1', text)


def normalize_bold(text: str) -> str:
    parts = re.split(r'(?<![\\*])\*\*(?!\*)([^\n*]+?)\*\*(?!\*)', text)
    for index in range(0, len(parts), 2):
        parts[index] = re.sub(r'(?<!\*)\\?\*\*(?!\*)', '', re.sub(r'(?<=\s)\*\*(?=\s)', r'\\*\\*', parts[index]).replace('\\*\\*', '\ue002'))
        parts[index] = parts[index].replace('\ue002', '\\*\\*')
    for index in range(1, len(parts), 2):
        phrase = parts[index].strip()
        if not phrase:
            parts[index] = ''
            continue
        before, after = parts[index - 1][-1:], parts[index + 1][:1]
        # Korean particles next to punctuation can prevent a CommonMark delimiter from closing.
        left = '\u200a' if before.isalnum() and not phrase[0].isalnum() else ''
        right = '\u200a' if after.isalnum() and not phrase[-1].isalnum() else ''
        parts[index] = f'{left}**{phrase}**{right}'
    return ''.join(parts)


def highlight_excerpt(excerpt: str, query: str) -> str:
    text = ' '.join(excerpt.split())
    terms = dict.fromkeys(re.findall(r'[가-힣A-Za-z][가-힣A-Za-z0-9]{1,}', query))
    figures = r'(?<!\d)(?<!\d[.,])[+−-]?\d+(?:[.,]\d+)*\s*(?:%p|%|bp|[조억만]\s?원)'
    pattern = re.compile('|'.join([figures, *(re.escape(term) for term in sorted(terms, key=len, reverse=True)[:12])]), re.IGNORECASE)
    parts, end = [], 0
    for index, match in enumerate(pattern.finditer(text)):
        if index == 4:
            break
        parts.extend((markdown_escape(text[end:match.start()]), f':yellow-background[{markdown_escape(match.group())}]'))
        end = match.end()
    return ''.join(parts) + markdown_escape(text[end:])


# Streamlit reads $…$ as a formula and ~…~ as struck text. Kept: addresses, what is already escaped, $$…$$ and a one-line
# formula (an operator and no Hangul, or a LaTeX command; pandoc's rule for the closing $).
AS_WRITTEN = re.compile(
    r'https?://[^\s)]+|\\[$~]|~~+|\$\$[^$]+\$\$'
    r'|\$(?!\s)(?:[^$\n가-힣]{0,200}?[\\^_={}][^$\n가-힣]{0,200}|[^$\n]{0,200}?\\[a-z]+[^$\n]{0,200})(?<!\s)\$(?!\w)|[$~]')
CODE = re.compile(r'(?ms)^[ \t]*(\x60{3}|~~~).*?(?:^[ \t]*\1[^\n]*|\Z)|(?<!\x60)(\x60+)(?!\x60)[^\n]+?(?<!\x60)\2(?!\x60)')
LATEX_BLOCK = re.compile(r'\\\[([^\[\]\n]*\\[a-z]+[^\[\]\n]*)\\\]')


def plain_symbols(text: str) -> str:
    """Prices ($180에서 $195) and ranges (3~5%) shown as written."""
    return AS_WRITTEN.sub(lambda m: f'\\{m.group()}' if m.group() in ('$', '~') else m.group(), text)


def answer_markdown(answer: str, *, highlight: bool) -> str:
    codes = []

    def keep(match):
        codes.append(match.group())
        return f'\ue000{len(codes) - 1}\ue001'  # private-use characters: marks around a code piece still pair up

    text = CODE.sub(keep, re.sub('[\ue000\ue001]', '', answer)).replace('![', '[')
    if highlight:
        text = plain_symbols(LATEX_BLOCK.sub(lambda m: f'$${m[1]}$$', text))
    text = re.sub(r'!?\(\((\d+)\)\)(?=\()', r'\\[\1\\]', text)  # not an image or a link of its own
    text = re.sub(r'\(\((\d+)\)\)', r'\\[\1\\]' if highlight else r'[\1]', text)
    marked = re.split(r'(?<![A-Za-z0-9=])==(?!\s)((?:[^\n=]|=(?!=)){1,240}?)(?<!\s)==(?![A-Za-z0-9=])', text)
    for field, part in enumerate(marked):
        if field % 2:
            phrase = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', part.replace('**', ''))
            phrase = markdown_label(re.sub(r'\\([$~\[\]])', r'\1', phrase))
            gap = '\u200a' if marked[field - 1].endswith(':') else ''  # "::" would not be read as a highlight
            marked[field] = f'{gap}:yellow-background[{phrase}]' if highlight else f'**{phrase}**'
        else:
            marked[field] = normalize_bold(part)
    return re.sub('\ue000(\\d+)\ue001', lambda m: codes[int(m[1])], ''.join(marked))


def liner_search_results(results: list[dict[str, str]], prefix: str, query: str) -> None:
    for index, row in enumerate(results):
        with st.container(border=True, gap='xsmall'):
            st.markdown(f"**{markdown_label(row['제목'])}**")
            if row.get('발행일'):
                st.caption(f"발행일 · {markdown_label(row['발행일'])}",
                           help='검색 결과에서 제공한 원문의 발행일입니다. 기사에 표시된 시간은 원문에서 확인하세요.')
            if row.get('요약'):
                st.markdown(highlight_excerpt(row['요약'], query))
            st.link_button('원문 열기', row['원문 링크'], key=f'{prefix}_article_{index}', icon=':material/open_in_new:',
                           help='새 탭에서 원문을 엽니다. 공식 발표 시각, 문맥, 요약에 빠진 내용을 확인할 수 있습니다.')


def queue_liner_request(prefix, kind, query, operation, **details):
    def keep(result):
        if kind == '검색':
            return liner_history.save('검색', query, {'question': query, 'answer': '', 'sources': result}, subject=details['subject'])
        thread, local_query = details['thread'], details.get('local_query')
        answer, sources = result
        return liner_history.save('대화', query, {
            'question': details['question'], 'answer': answer, 'sources': sources, 'guides': details['guides'],
            'local': kind == '내부 조회', 'local_query': asdict(local_query) if local_query else None,
        }, thread_id=thread.get('id'), subject=thread['subject'])

    for field in (f'{prefix}_notice', f'{prefix}_arrived', 'ai_draft'):  # the draft was sent
        st.session_state.pop(field, None)
    st.session_state[f'{prefix}_pending'] = {
        'kind': kind, 'query': query, 'started': time.monotonic(),
        'future': liner_jobs.submit(kind, query, operation, keep), **details,
    }
    try:
        st.rerun(scope='fragment')  # the dialog or the floating panel alone
    except StreamlitAPIException:  # drawn as part of a full run
        st.rerun()


def leave(prefix: str, kind: str, opened: int | None = None) -> None:
    """Widget callback: the user goes to a new or another talk ('대화') or another search; an answer on its way must not take that away."""
    pending = st.session_state.get(f'{prefix}_pending')
    if pending and (pending['kind'] == '검색') == (kind == '검색'):
        pending['left'] = opened is None or opened != pending.get('thread', {}).get('id')


def finish_liner_request(prefix):
    pending = st.session_state.get(f'{prefix}_pending')
    if not pending or not pending['future'].done():
        return pending
    st.session_state.pop(f'{prefix}_pending')
    try:
        result, saved, talk = pending['future'].result()
    except ValueError as exc:
        st.session_state[f'{prefix}_notice'] = ('error', str(exc), pending)
    except Exception as exc:  # noqa: BLE001 - shown as a failed request
        logger.warning('AI 검색 요청 오류 · %s · %s', pending['kind'], type(exc).__name__)
        st.session_state[f'{prefix}_notice'] = ('error', '요청을 처리하는 중 오류가 발생했습니다.', pending)
    else:
        if pending.get('left') and talk:
            st.session_state[f'{prefix}_arrived'] = {'id': talk, 'query': pending['query']}
            return None
        st.session_state[f'{prefix}_latest'] = pending['kind']
        if pending['kind'] == '검색':
            st.session_state[f'{prefix}_subject'] = pending['subject']
            st.session_state[f'{prefix}_results'] = result
            st.session_state[f'{prefix}_found'] = talk
        else:
            answer, sources = result
            thread = pending['thread']
            thread['id'] = talk or thread.get('id')
            thread['turns'].append({'question': pending['question'], 'answer': answer,
                                    'sources': sources, 'guides': pending['guides'],
                                    'local': pending['kind'] == '내부 조회', 'local_query': pending.get('local_query'),
                                    'time': datetime.now(KST).isoformat()})
            st.session_state[f'{prefix}_chat'] = thread
        if not saved:
            st.session_state[f'{prefix}_notice'] = ('warning', '이 요청을 지난 대화에 저장하지 못했습니다.', pending)
    return None


def liner_request_text(query: str) -> None:
    with st.container(height=110 if len(query) > 150 else 'content', border=False, width='stretch'):
        st.text(query)


@st.fragment(run_every=1)
def liner_progress(pending):
    if pending['future'].done():
        st.rerun()
    elapsed = int(time.monotonic() - pending['started'])
    action = {'검색': '웹 자료를 검색하는 중', '질문': '출처를 찾아 AI 답변을 작성하는 중',
              '내부 조회': '대시보드 기록을 확인하는 중'}[pending['kind']]
    with st.status(f'{action} · {elapsed}초 경과', expanded=True).container():  # leaving st.status itself would mark it done
        liner_request_text(pending['query'])
        st.caption('기다리는 동안 다른 대화나 사용 가이드를 읽을 수 있습니다.',
                   help='표시된 시간은 요청을 시작한 뒤 지난 시간입니다. 진행 중인 요청이 끝나면 검색과 질문을 다시 보낼 수 있습니다.')


def fold(prefix: str, name: str) -> None:
    """Widget callback: fold or unfold a panel ('nav', 'side') or a card of the wide window."""
    st.session_state[f'{prefix}_fold_{name}'] = not folded(prefix, name)


def folded(prefix: str, name: str) -> bool:
    return st.session_state.get(f'{prefix}_fold_{name}', False)


def card(prefix: str, name: str, title: str, icon: str, opened: bool = True):
    """A card that folds by its title. What is in a folded one is not read; Streamlit forgets a widget that was not drawn, so it is kept here."""
    key = f'{prefix}_card_{name}'
    box = st.expander(f'{icon} {title}', key=key, expanded=st.session_state.get(f'{key}_open', opened), on_change='rerun')
    st.session_state[f'{key}_open'] = box.open
    return box


def liner_suggestions(prefix: str, busy: bool) -> None:
    from liner_topics import groups

    questions = groups()
    pick_key = f'{prefix}_suggestions'
    action_key = f'{prefix}_repeat'
    queue = st.session_state.__setitem__
    with st.container(horizontal=True):
        shuffle = st.button('다른 질문', icon=':material/shuffle:', key=f'{prefix}_shuffle', type='tertiary',
                            help='지금 보이는 추천을 다른 질문으로 바꿉니다. 추천을 누르면 AI 채팅에 바로 전송됩니다.')
        every = st.popover(f'전체 {sum(map(len, questions.values()))}개', icon=':material/list:', type='tertiary')
    st.caption('누르면 바로 보냅니다(요금 적용).')
    if shuffle or pick_key not in st.session_state:
        previous = st.session_state.get(pick_key, {})
        st.session_state[pick_key] = {
            name: random.choice([q for q in items if q != previous.get(name)] or items)
            for name, items in questions.items() if items
        }
    with st.container(horizontal=True, gap='xsmall'):
        for index, question in enumerate(st.session_state[pick_key].values()):
            st.button(markdown_label(question), key=f'{prefix}_suggestion_{index}', wrap=True, disabled=busy,
                      help='이 문장을 AI 채팅에 바로 보냅니다. 채팅 요청 요금이 적용됩니다.',
                      on_click=queue, args=(action_key, ('질문', question)))
    with every:
        group = st.selectbox('분류', [name for name, items in questions.items() if items], key=f'{prefix}_topic_group',
                             help='서비스 사용법, Jev·투자 성과, 투자 거장 등 관심 분야를 골라 질문 목록을 좁힙니다.')
        question = st.selectbox('질문', questions[group], index=None, key=f'{prefix}_topic_question', placeholder='질문을 고르세요',
                                help='선택한 문장을 확인한 뒤 아래 버튼으로 AI 채팅에 보냅니다.')
        st.button('선택한 질문 보내기', key=f'{prefix}_topic_send', disabled=busy or not question,
                  help='고른 질문으로 AI 답변을 요청합니다. 채팅 요청 요금이 적용됩니다.',
                  on_click=queue, args=(action_key, ('질문', question)))


def liner_recent(prefix: str, busy: bool) -> None:
    with card(prefix, 'history', '이력 · 다시 요청하기', ':material/history:', opened=False) as box:
        if not box.open:
            return
        try:
            recent = liner_history.recent()
        except (OSError, sqlite3.Error):
            st.warning('다시 요청하기 목록을 불러오지 못했습니다.')
            return
        st.caption('항목을 누르면 새로 요청합니다(요금 적용).' if recent else '검색이나 채팅을 사용하면 이곳에 기록됩니다.',
                   help='최근에 보낸 웹 검색어와 질문을 최대 8개 보여 줍니다. 받은 답은 지난 대화에서 봅니다.')
        with st.container(horizontal=True, gap='xsmall'):
            for index, (kind, text) in enumerate(recent):
                label = text if len(text) <= 55 else f'{text[:54]}…'
                icon = ':material/search:' if kind == '검색' else ':material/chat_bubble:'
                st.button(f'{kind} · {markdown_label(label)}', key=f'{prefix}_history_{index}', wrap=True, disabled=busy,
                          icon=icon,
                          help=f'**{kind} 다시 요청** · {markdown_label(text)}\n\n새 API 요청 요금이 적용됩니다.',
                          on_click=st.session_state.__setitem__,
                          args=(f'{prefix}_repeat', (kind, text)))


def show_liner_guides(guides: list[dict[str, str]]) -> None:
    if guides:
        with st.expander(f'서비스 가이드 {len(guides)}개'):
            st.caption('답변에 참고한 프로젝트 문서', help='화면 안내와 프로젝트 가이드에서 찾은 항목의 출처와 발췌입니다.')
            for guide in guides:
                st.markdown(f"**{markdown_label(guide['항목'])}** · \x60{guide['문서']}\x60")
                body = re.sub(r'!?\[([^\]]*)\]\((?!https?:)[^)]*\)', r'\1', guide['본문'])  # the dashboard does not serve these files
                st.markdown(plain_symbols(body.replace('![', '[')), unsafe_allow_html=False)


def render_liner_usage_guide() -> None:
    guide = Path(__file__).resolve().parents[1] / 'docs' / 'ai-search.md'
    title, _, body = guide.read_text(encoding='utf-8').partition('\n')
    intro, *sections = re.split(r'(?m)^## (.+)\n', body)
    st.subheader(title.removeprefix('# '), icon=':material/menu_book:', divider='blue',
                 help='빠른 시작에서 기본 흐름을 보고, 기능별 사용법과 이력·연동에서 필요한 설명을 찾아보세요.')
    st.markdown(intro.strip())
    for tab, text in zip(st.tabs(sections[::2]), sections[1::2]):
        with tab:
            st.markdown(text.strip())


def retry_liner_request(prefix: str, failed: dict) -> None:
    st.session_state.pop(f'{prefix}_guide', None)  # the guide view sends nothing
    if failed['kind'] == '검색':
        st.session_state[f'{prefix}_repeat'] = ('검색', failed['query'], failed['subject'])
        return
    st.session_state[f'{prefix}_chat'] = failed['thread']  # the talk it was asked in, whatever is open now
    st.session_state[f'{prefix}_repeat'] = ('질문', failed['question'])


def new_liner_chat(prefix: str) -> None:
    leave(prefix, '대화')
    for field in ('chat', 'notice'):
        st.session_state.pop(f'{prefix}_{field}', None)
    forget_search(prefix)
    st.session_state.update({f'{prefix}_latest': '질문', f'{prefix}_box': False})


def liner_notice(prefix: str, busy: bool, available: bool = True) -> None:
    """The running request and what the last one left to say."""
    if pending := st.session_state.get(f'{prefix}_pending'):
        liner_progress(pending)
    if arrived := st.session_state.get(f'{prefix}_arrived'):
        import ai_history
        with st.container(horizontal=True, vertical_alignment='center'):
            st.success(f"답이 도착했습니다 · {markdown_label(arrived['query'][:60])}", width='stretch')
            st.button('열기', key=f'{prefix}_arrived_open', on_click=ai_history.open_talk, args=(prefix, arrived['id']))
    if notice := st.session_state.get(f'{prefix}_notice'):
        kind, message, failed = notice
        if kind == 'error':
            st.error(message)
            with st.container(horizontal=True, vertical_alignment='center'):
                liner_request_text(failed['query'])
                st.button('다시 요청', icon=':material/refresh:', key=f'{prefix}_retry', disabled=busy or not available,
                          help='실패한 검색어나 질문을 다시 보냅니다. 웹 요청이면 새 API 요청 요금이 적용됩니다.',
                          on_click=retry_liner_request, args=(prefix, failed))
            show_liner_guides(failed.get('guides', []))
        else:
            st.warning(message)


def liner_form(prefix: str, busy: bool) -> tuple[str, bool]:
    with st.form(f'{prefix}_search', border=False):
        with st.container(horizontal=True, vertical_alignment='center'):
            query = st.text_input('종목·시장 이슈 검색', key=f'{prefix}_query', max_chars=300, icon=':material/travel_explore:', width='stretch',
                                  label_visibility='collapsed', placeholder='웹에서 찾기: 종목명, 종목코드, 시장 이슈')
            submitted = st.form_submit_button('검색', key=f'{prefix}_submit', icon=':material/search:', type='primary',
                                              disabled=busy, help='검색어로 웹에서 제목, 발행일, 요약과 원문 링크를 최대 10개 찾습니다. 검색 요청 요금이 적용됩니다.')
    st.caption('웹 검색과 웹·가이드 질문에는 Liner API 요금이 적용됩니다.',
               help='웹 요청은 Liner에 전송되며 완료한 요청은 로컬 이력에 저장됩니다. 채팅의 내부 데이터 조회는 대시보드에서 처리합니다.')
    return query, submitted


def liner_news(prefix: str, busy: bool) -> dict | None:
    """The journal's headlines; the subject of the one that was pressed."""
    with card(prefix, 'news', '최근 주요 뉴스', ':material/newspaper:') as box:
        if not box.open:
            return None
        try:
            day, issues = liner_issues()
        except Exception as exc:  # noqa: BLE001 - the news card must not take the search down
            logger.warning('최근 주요 뉴스 오류 · %s', type(exc).__name__)
            day, issues = None, []
            st.warning('최근 주요 뉴스를 불러오지 못했습니다.')
        st.caption(f'{day} 매매일지 · 제목을 누르면 웹 검색합니다(요금 적용).' if issues else '최근 3일의 매매일지에 순위를 매길 뉴스가 없습니다.')
        picked = None
        for index, issue in enumerate(issues):
            stars = issue.get('stars', 1)
            color = ('gray', 'blue', 'yellow', 'orange', 'red')[stars - 1]
            rating = f":{color}-badge[{'★' * stars} {stars}/5]"
            label = f"{rating}  **{markdown_label(issue['title'])}**"
            if st.button(label, key=f'{prefix}_issue_{index}', width='stretch', wrap=True, disabled=busy, type='tertiary',
                         help=f'매매일지 중요도 {stars}/5. 제목으로 웹 검색을 실행합니다(요금 적용). 검색 뒤 시장 영향 분석을 요청할 수 있습니다.'):
                picked = {'주제': issue['title'].strip(), '매매일지 기준일': day, '기사 링크': issue.get('link') or ''}
    return picked


def queue_liner_search(prefix: str, subject: dict) -> None:
    from liner_search import search

    query = subject['주제'].strip()
    if query:
        queue_liner_request(prefix, '검색', query, lambda: search(query), subject={**subject, '주제': query})
    st.info('검색어를 입력하세요.')


def analyze_liner(prefix: str) -> None:
    """Widget callback: a new talk about the searched topic."""
    st.session_state[f'{prefix}_chat'] = {'subject': st.session_state[f'{prefix}_subject'], 'turns': []}
    st.session_state[f'{prefix}_repeat'] = ('질문', MARKET_QUESTION)


def liner_results(prefix: str, busy: bool) -> None:
    subject, results = st.session_state.get(f'{prefix}_subject'), st.session_state.get(f'{prefix}_results')
    if not subject:
        return
    with st.container(horizontal=True, horizontal_alignment='distribute', vertical_alignment='center'):
        st.markdown(f"**:material/search: {markdown_label(subject['주제'])}**", width='content')
        st.button('오늘 주식시장 영향 분석', icon=':material/auto_awesome:', key=f'{prefix}_analyze', on_click=analyze_liner, args=(prefix,),
                  disabled=busy or st.session_state.get(f'{prefix}_kept_mode') == '내부 데이터',
                  help='자동 모드에서 이 주제로 새 웹 분석을 요청합니다(요금 적용). 관련 지수·업종과 확인할 지표를 최신 출처와 함께 찾아봅니다.')
    if results:
        with st.expander(f'검색 결과 {len(results)}건', expanded=st.session_state.get(f'{prefix}_latest') == '검색'):
            st.caption('형광펜은 검색어와 요약의 주요 숫자입니다.')
            liner_search_results(results, prefix, subject['주제'])
    else:
        st.info('표시할 검색 결과가 없습니다.')


def forget_search(prefix: str) -> None:
    for field in ('results', 'subject', 'found'):
        st.session_state.pop(f'{prefix}_{field}', None)


def panel_button(prefix: str, name: str, title: str, side: str) -> None:
    hidden = folded(prefix, name)
    st.button(title, icon=f":material/{side}_panel_{'open' if hidden else 'close'}:", key=f'{prefix}_fold_{name}_button', type='tertiary',
              on_click=fold, args=(prefix, name), help=f"{title} 목록을 {'펼칩니다' if hidden else '접습니다'}.")


def turn_guide(prefix: str) -> None:
    st.session_state[f'{prefix}_guide'] = not st.session_state.get(f'{prefix}_guide')


def liner_search(available: bool, prefix: str = 'api_liner') -> None:
    import ai_history

    busy = finish_liner_request(prefix) is not None
    guide = st.session_state.get(f'{prefix}_guide', False)
    with st.container(horizontal=True, horizontal_alignment='distribute', vertical_alignment='center', key=f'{prefix}_bar'):
        with st.container(horizontal=True, width='content'):
            if not guide:
                panel_button(prefix, 'nav', '지난 대화', 'left')
            st.button('검색·채팅' if guide else '사용 가이드', icon=':material/arrow_back:' if guide else ':material/menu_book:', key=f'{prefix}_guide_button',
                      type='tertiary', on_click=turn_guide, args=(prefix,))
        with st.container(horizontal=True, width='content'):
            if prefix == 'quick_liner' and st.button('작은 창', icon=':material/picture_in_picture_alt:', key=f'{prefix}_to_float', type='tertiary',
                                                     help='화면 오른쪽 아래 채팅창으로 옮깁니다. 대시보드를 보면서 이어서 물어볼 수 있습니다.'):
                close_liner_search()
                st.session_state['ai_float_open'] = True
                st.rerun()
            if available and not guide:
                panel_button(prefix, 'side', '뉴스·추천', 'right')
    liner_notice(prefix, busy, available)
    if guide:
        render_liner_usage_guide()
        return
    if not available:  # the talks that were kept can still be read
        busy = True
        st.info('웹 검색을 사용하려면 프로젝트 .env에 LINER_API_KEY를 설정하세요.')

    action, repeated, *again = st.session_state.pop(f'{prefix}_repeat', (None, None)) if available else (None, None)
    query, submitted, picked = '', False, None
    nav, side = not folded(prefix, 'nav'), available and not folded(prefix, 'side')
    with st.container(key=f'{prefix}_body'):
        columns = st.columns([1.1] * nav + [2.6] + [1.3] * side, gap='medium')
    st.html(f'<style>{ai_history.CSS}</style>')
    if nav:
        with columns[0].container(key=f'{prefix}_nav'):
            st.button('새 대화', icon=':material/add_comment:', key=f'{prefix}_new_chat', width='stretch', on_click=new_liner_chat, args=(prefix,),
                      help='대화와 분석 주제를 비우고 새로 질문합니다. 지금 대화는 이 목록에서 다시 열 수 있습니다.')
            ai_history.talk_list(prefix, busy)
    if side:
        with columns[-1].container(key=f'{prefix}_side'):
            picked = liner_news(prefix, busy)
            with card(prefix, 'suggestions', '추천 질문', ':material/lightbulb:') as box:
                if box.open:
                    liner_suggestions(prefix, busy)
            liner_recent(prefix, busy)

    with columns[int(nav)].container(key=f'{prefix}_main'):
        if available:
            query, submitted = liner_form(prefix, busy)
        wanted = (again[0] if again else {'주제': repeated}) if action == '검색' else picked or ({'주제': query} if submitted else None)
        if wanted and not busy:
            queue_liner_search(prefix, wanted)
        found_first = st.session_state.get(f'{prefix}_latest') == '검색'  # what the user has just looked for
        if found_first:
            liner_results(prefix, busy)
        with st.container(border=True, key=f'{prefix}_chat_card'):
            liner_chat(prefix, repeated if action == '질문' else None, busy=busy, available=available, new_button=not nav)
        if not found_first:
            liner_results(prefix, busy)


def liner_turn(turn: dict, key: str) -> None:
    if turn.get('time'):
        st.caption(when(turn['time']))
    st.chat_message('user').text(turn['question'])
    with st.chat_message('assistant'):
        if turn.get('local'):
            st.caption('대시보드 내부 조회')
        st.markdown(answer_markdown(turn['answer'], highlight=True), unsafe_allow_html=False)
        if turn['sources']:
            with st.expander(f"출처 {len(turn['sources'])}개"):
                st.caption('AI 답변에 참고한 웹 자료', help='원문에서 발행일과 인용된 내용의 전체 문맥을 확인할 수 있습니다.')
                for index, source in enumerate(turn['sources']):
                    st.link_button(markdown_label(source['제목']), source['원문 링크'], key=f'{key}_{index}', icon=':material/open_in_new:',
                                   wrap=True, help='새 탭에서 이 출처의 원문을 엽니다.')
        show_liner_guides(turn.get('guides', []))


def turn_markdown(turn: dict) -> str:
    markdown = answer_markdown(turn['answer'], highlight=False)
    if turn['sources']:
        lines = []
        for source in turn['sources']:
            url = source['원문 링크'].replace('<', '%3C').replace('>', '%3E')
            lines.append(f"- [{markdown_label(source['제목'])}](<{url}>)")
        markdown += '\n\n### 출처\n\n' + '\n'.join(lines)
    if turn.get('guides'):
        markdown += '\n\n### 서비스 가이드\n\n' + '\n'.join(f"- {markdown_label(guide['항목'])} ({guide['문서']})" for guide in turn['guides'])
    return markdown


MARKET_QUESTION = '이 이슈가 오늘 국내 주식시장에 어떤 영향을 미칠 수 있나요?'


def liner_chat(prefix: str, suggested: str | None = None, *, busy: bool = False, available: bool = True, compact: bool = False,
               new_button: bool = True) -> None:
    from markdown_clipboard import copy_markdown

    chat_key = f'{prefix}_chat'
    thread = st.session_state.get(chat_key)

    if not thread:
        thread = {'subject': None, 'turns': []}
    turns = thread['turns']
    topic = thread['subject']
    mode_key = f'{prefix}_chat_mode'
    if not st.session_state.get(mode_key):  # Streamlit forgets a widget that was not drawn (guide view, closed dialog)
        st.session_state[mode_key] = st.session_state.get(f'{prefix}_kept_mode', '자동')
    with st.container(horizontal=True, vertical_alignment='center'):
        mode = st.segmented_control('채팅 조회 범위', ('자동', '내부 데이터'), required=True, key=mode_key, label_visibility='collapsed',
                                    help='자동은 지원하는 운용 기록 질문을 대시보드에서 조회합니다. 내부 데이터에서는 모든 질문을 로컬에서 처리합니다.')
        if turns:
            copy_markdown(key=f'{prefix}_copy', data={'markdown': turn_markdown(turns[-1])}, width='content')
            if len(turns) > 1 and not compact:
                copy_markdown(key=f'{prefix}_copy_all', width='content', data={'label': '대화 전체 복사', 'markdown': '\n\n---\n\n'.join(
                    f"## {' '.join(turn['question'].split())}\n\n{turn_markdown(turn)}" for turn in turns)})
            if new_button:
                st.button('새 대화', icon=':material/add_comment:', key=f'{prefix}_new_chat',
                          help='대화와 분석 주제를 비우고 새로 질문합니다. 이전 대화는 지난 대화에서 다시 열 수 있습니다.',
                          on_click=new_liner_chat, args=(prefix,))
    st.session_state[f'{prefix}_kept_mode'] = mode
    if topic:
        st.caption(f"분석 주제 · {markdown_label(topic['주제'])}")
    with st.container(key=f'{prefix}_chat_history', height=360, border=False, autoscroll=len(turns) > 1):
        if not turns:
            with st.container(key=f'{prefix}_empty', width='content', horizontal_alignment='center', gap='xsmall'):
                st.markdown(':material/forum: **무엇이든 물어보세요**', width='content')
                st.caption('첫 질문 예시 · Jev의 최대 낙폭은 어느 화면에서 확인할 수 있나요?', width='content')
                if compact:
                    import ask_ai
                    ask_ai.idea_buttons()
        for turn_index, turn in enumerate(turns):
            liner_turn(turn, f'{prefix}_source_{turn_index}')
    box = f'{prefix}_follow_up'
    sent = st.session_state.pop(f'{prefix}_sent', None)
    if sent and busy:
        st.session_state[box] = sent
        st.toast('지금 요청이 끝나면 보낼 수 있습니다. 입력한 글은 그대로 두었습니다.')
    elif text := st.session_state.pop(f'{prefix}_fill', None):
        st.session_state[box] = text
    st.chat_input('주식·ETF·서비스 사용법을 질문하세요', key=box, max_chars=1000, disabled=not available, on_submit=send, args=(prefix,))
    question = suggested or sent
    if not busy and question and question.strip():
        ask_liner(prefix, thread, question.strip(), internal=mode == '내부 데이터')


def send(prefix: str) -> None:
    """Widget callback: the user pressed send. Never read from the box itself, which may hold a text it was only filled with."""
    st.session_state[f'{prefix}_sent'] = st.session_state[f'{prefix}_follow_up']


def ask_liner(prefix: str, thread: dict, question: str, *, internal: bool) -> None:
    """Send one question of `thread`: to the records of this machine when it is about them (or `internal`), else to the web."""
    import liner_local_data
    from liner_search import analyze
    from liner_topics import find_guides
    from src.config.paths import get_runtime_root

    import ai_history

    thread = ai_history.up_to_date(thread)
    topic, turns = thread['subject'], thread['turns']
    now = datetime.now(KST)
    previous = turns[-1].get('local_query') if turns else None
    query = liner_local_data.resolve(question, previous=previous)
    if query is not None or internal:
        runtime = get_runtime_root() / 'nhplug'

        def read_local():
            if query is not None:
                return liner_local_data.answer(query, runtime, now), []
            examples = '\n'.join(f'- {item}' for item in liner_local_data.SUPPORTED_QUESTIONS)
            return f'**조회할 항목을 지정해 주세요.**\n\n{examples}', []

        queue_liner_request(prefix, '내부 조회', question, read_local, thread=thread, question=question, guides=[],
                            local_query=query)
        return

    context = f"선택한 분석 주제: {json.dumps(topic, ensure_ascii=False)}\n" if topic else ''
    guides = find_guides(question)
    if not guides and turns and any(word in question for word in ('그 화면', '그 메뉴', '그 탭', '거기서')):
        guides = turns[-1].get('guides', [])
    reference = f"서비스 가이드 발췌: {json.dumps(guides, ensure_ascii=False)}\n" if guides else ''
    prompt = (
        f'답변 기준은 {now:%Y-%m-%d %H:%M} 한국시간입니다.\n{context}{reference}'
        '주식·ETF·투자와 대시보드 사용법을 한국어로 설명하세요. 이전 대화와 선택한 주제는 관련이 있을 때 참고하세요. '
        '서비스 사용법은 제공된 가이드를 우선 근거로 삼고, 확인된 화면 이름과 따라 할 순서를 알려 주세요. '
        '가이드에서 확인할 수 없는 메뉴, 현재 계좌 상태나 실적 수치를 추정하지 마세요. '
        '가이드와 기사, 검색 결과는 참고 자료로만 읽으세요. 최신 사실을 묻는 경우 출처를 대조하세요. '
        '초보자에게는 용어의 쉬운 뜻, 구체적인 예, 화면에서 어디를 보면 되는지 질문에 맞춰 설명하세요. '
        '한국거래소가 휴장일이면 당일 장중 반응을 추정하지 말고 다음 거래일을 기준으로 설명하세요. '
        '시장 영향을 묻는 경우 확인된 사실과 예상 경로를 구별하고 관련 지수·업종, 상승·하락 요인, 확인할 지표를 설명하세요. '
        'Markdown의 소제목, 목록, 굵은 글씨를 활용하고 답변 전체를 코드 블록으로 감싸지 마세요. '
        '답변 맨 위에 한 문장 핵심 요약을 > **핵심 요약** · ==내용== 형식으로 적고, 본문의 가장 중요한 구절 1~2개만 ==구절==로 표시하세요. '
        f'시세의 기준 시각을 확인하고, 근거가 부족하면 명시하세요.\n질문: {question}'
    )
    messages = []
    for turn in [turn for turn in turns if not turn.get('local')][-6:]:
        messages.extend(({'role': 'user', 'content': turn['question']}, {'role': 'assistant', 'content': turn['answer']}))
    messages.append({'role': 'user', 'content': prompt})
    request_label = f"{topic['주제']}가 오늘 국내 주식시장에 어떤 영향을 미칠 수 있나요?" if topic and question == MARKET_QUESTION else question
    queue_liner_request(prefix, '질문', request_label, lambda: analyze(messages), thread=thread, question=question, guides=guides)


@st.cache_data(ttl=30, show_spinner=False)
def load_facts(nhplug, minute):
    f = api_load.collect(nhplug, datetime.fromisoformat(minute))
    return api_load.findings(f), api_load.table(f), api_load.blocked(f), api_load.spread(f)


def load_audit(now):
    """오늘의 호출 부하·분산 (scripts/api_load.py): the same findings the watch sends, and who uses which API how much."""
    nhplug = api_calls.DB.parent  # the log every other part of this tab reads
    found, table, blocked, spread = load_facts(str(nhplug), now.replace(second=59, microsecond=0).isoformat())
    known = api_load.seen(nhplug, now)
    st.markdown('##### 오늘 부하·분산 감사')
    for x in found:
        st.warning(f"{x['detail']}  \n→ {x['advice']}", icon=':material/warning:')
    if not found:
        st.success(f'최근 {api_load.WINDOW_MIN}분에 과한 호출, 거절, 지연이 없습니다.' if table else '오늘 호출 기록이 없습니다.', icon=':material/check_circle:')
    if past := [v for k, v in known.items() if k not in {x['key'] for x in found}]:
        st.caption('오늘 지나간 문제: ' + ' / '.join(f"{v['first'][11:16]}~{v['last'][11:16]} {v['detail']}" for v in past))
    if not table:
        return
    st.dataframe(table, hide_index=True, width='stretch', alt='API별 호출 부하', column_config={
        '호출': st.column_config.NumberColumn(format='%,d'), '평균 응답(ms)': st.column_config.NumberColumn(format='%,.0f'),
        '평소(ms)': st.column_config.NumberColumn(format='%,.0f'), '호출 프로그램': st.column_config.TextColumn(width='large')})
    st.caption('거절 = 제공처의 호출 제한(HTTP 429·NH IGW4290x). 로컬 시작 실패 = NH에 닿기 전 MCP 프로세스가 30초 안에 뜨지 못함. '
               '평소 = 앞선 5일 중앙값. 초당 최대는 호출이 시작된 초를 세며, 새 코드로 시작한 프로그램만 들어갑니다(일부 = 호출의 90% 미만을 잼). '
               '같은 감사가 평일 09:05~15:40 매분 돌며 새 문제를 Discord 실시간 채널로 보내고 매시 장중 감사에도 들어갑니다.')
    with st.expander('막힌 요청과 NH 호출을 많이 쓰는 조회'):
        st.dataframe(blocked or [{'막힌 요청': '오늘 없음'}], hide_index=True, width='stretch', alt='오늘 막힌 요청')
        st.dataframe(spread or [{'NH 조회': '오늘 없음'}], hide_index=True, width='stretch', alt='NH 호출을 많이 쓰는 조회')


def render(runtime):
    now = datetime.now(KST)
    # 스킬 · 리포트 발행 · 자동 작업 are their own 운영 screens since 2026-11-19 (skills_view, report_pages_view, schedule_view)
    head, pick, refresh = st.columns([3, 2, 1], vertical_alignment='bottom')
    head.subheader('API 연동')
    period = pick.segmented_control('기간', list(PERIODS), default='오늘', key='api_period', label_visibility='collapsed') or '오늘'
    if refresh.button('새로고침', icon=':material/refresh:', width='stretch', key='api_refresh'):
        credentials.clear()
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
    st.caption('상태: 미사용(선택 키 미설정) · 키 없음(필요한 .env 키 미설정) → 실패(최근 30분 절반 이상 실패) → 지연(장중 09:15~15:00에 예상 주기의 3배, 최소 5분 넘게 성공 없음; '
               '오늘 아무 API도 호출되지 않은 날은 판단하지 않음) → 주의(1시간 안 실패) → 정상(오늘 호출 있음) → 호출 없음 순으로 판단합니다. '
               '"호출 없음"은 장 시간 밖이거나 그 API를 쓰는 서비스가 아직 새 코드로 재시작하지 않았다는 뜻일 수 있습니다.')

    load_audit(now)

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
    if p['key'] == 'liner':
        names_.append('웹 검색')
    uses_tab, endpoint_tab, caller_tab, fail_tab, output_tab, *data_tab = st.tabs(names_)
    if data_tab:
        with data_tab[0]:
            if p['key'] == 'liner':
                liner_search(keys.get('LINER_API_KEY', False))
            else:
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
