"""The floating AI chat (user 2026-11-24): a phone-sized panel at the bottom right of every screen, opened by a button there.

It shows the same talk, results and history as the 'AI 검색' dialog (session keys quick_liner_*), one page at a time, and the
two never draw together. A fragment of its own: asking a question does not redraw the screen behind it.
"""
from __future__ import annotations

import streamlit as st

import ai_history
import api_status_view as view
import ask_ai

PREFIX = 'quick_liner'
PAGES = ('채팅', '검색', '추천', '이력')
# Below Streamlit's header, sidebar, pop-ups and drop-down lists. --om-panel is the light theme's token (theme.py).
CSS = f"<style>{ai_history.CSS}</style>" + """<style>
[data-testid="stLayoutWrapper"]:has(> .st-key-ai_float), [data-testid="stLayoutWrapper"]:has(> .st-key-ai_float_button) { position: absolute; }
.st-key-ai_float_button { position: fixed; right: 20px; bottom: 20px; width: auto !important; z-index: 99980; }
.st-key-ai_float_button button { border-radius: 999px; min-height: 48px; padding: 0 18px; box-shadow: 0 6px 20px rgba(2, 6, 23, .28); }
.st-key-ai_float {
    position: fixed; right: 20px; bottom: 20px; z-index: 99980;
    width: min(400px, calc(100vw - 24px)) !important; height: min(720px, calc(100dvh - 84px)) !important;
    display: flex; flex-direction: column; flex-wrap: nowrap; gap: .5rem; overflow: hidden;
    padding: 12px 14px; border-radius: 16px; border: 1px solid rgba(148, 163, 184, .35);
    background: var(--om-panel, #0b1120); box-shadow: 0 16px 48px rgba(2, 6, 23, .38);
}
.st-key-ai_float > * { flex: 0 0 auto; min-height: 0; }
/* the page takes the height that is left; the chat history fills it, the other pages scroll */
.st-key-ai_float > [data-testid="stLayoutWrapper"]:has(> .st-key-ai_float_body) { flex: 1 1 0; display: flex; flex-direction: column; }
.st-key-ai_float_body { flex: 1 1 0; min-height: 0; height: 100%; flex-wrap: nowrap; gap: .5rem; overflow-y: auto; scrollbar-width: thin; }
.st-key-ai_float_body > * { flex: 0 0 auto; }
.st-key-ai_float_body > [data-testid="stLayoutWrapper"]:has(> .st-key-quick_liner_chat_history) { flex: 1 1 8rem; min-height: 8rem; height: auto; }
.st-key-ai_float .st-key-quick_liner_chat_history { height: 100% !important; max-height: none !important; overflow-y: auto; scrollbar-width: thin; }
.st-key-ai_float [data-testid="stChatMessage"] { padding: .5rem; gap: .5rem; }
.st-key-ai_float [data-testid="stChatMessage"] h1, .st-key-ai_float [data-testid="stChatMessage"] h2,
.st-key-ai_float [data-testid="stChatMessage"] h3 { font-size: 1.05rem; padding: .4rem 0 .2rem; }
.st-key-ai_float [data-testid="stHeading"] h3 { font-size: 1.05rem; padding-top: 0; }
.st-key-ai_float_body button, .st-key-ai_float_body button > div { text-align: left; }
@media (max-width: 640px) {
    .st-key-ai_float { right: 12px; left: 12px; bottom: 12px; width: auto !important; height: calc(100dvh - 72px) !important; }
    .st-key-ai_float_button { right: 12px; bottom: 12px; }
}
[class*="st-key-ask_row_"] button { min-height: 1.6rem; padding: 0 .4rem; text-decoration: underline dotted; text-underline-offset: 3px; }
</style>"""


def _open() -> None:
    st.session_state['ai_float_open'] = True
    if st.session_state.pop('ai_float_news', False):  # what came while it was closed
        st.session_state['ai_float_page'] = '검색' if st.session_state.get(f'{PREFIX}_latest') == '검색' else '채팅'


def _close() -> None:
    if page := st.session_state.get('ai_float_page'):  # a widget that is not drawn falls back to an older page
        st.session_state['ai_float_page'] = page
    for key in ('ai_float_open', 'ai_draft'):
        st.session_state.pop(key, None)


@st.fragment(key='ai_float')
def render() -> None:
    if st.session_state.get(f'{PREFIX}_open'):  # the dialog draws the same widgets
        st.session_state.pop('ai_float_news', None)
        return
    if st.session_state.pop('ai_close_dialog', False):  # asked from a pop-up: redraw the app without it
        st.rerun()
    st.html(CSS)
    if st.session_state.get('prefs', {}).get('ask_selection', True):
        ask_ai.selection_button()
    if not st.session_state.get('ai_float_open'):
        waiting = st.session_state.get(f'{PREFIX}_pending')
        if waiting and not (pending := view.finish_liner_request(PREFIX)):
            failed = (st.session_state.get(f'{PREFIX}_notice') or ('',))[0] == 'error'
            waiting, st.session_state['ai_float_news'] = None, 'AI 요청 실패' if failed else 'AI 답 확인'
        if waiting:
            wait(pending)
        with st.container(key='ai_float_button', width='content'):
            label = '답변 작성 중' if waiting else st.session_state.get('ai_float_news', 'AI에게 묻기')
            st.button(label, icon=':material/forum:', key='ai_float_launch', type='primary', on_click=_open,
                      help='화면을 보면서 물어볼 수 있는 작은 채팅창을 엽니다. 대화는 위쪽 AI 검색 창과 이어집니다.')
        return
    with st.container(key='ai_float'):
        panel()


@st.fragment(run_every=2)
def wait(pending) -> None:
    if pending['future'].done():
        st.rerun()  # a fragment key is only taken from a widget callback


def panel() -> None:
    busy = view.finish_liner_request(PREFIX) is not None
    repeat = st.session_state.get(f'{PREFIX}_repeat')
    if repeat:  # asked from another page or a retry: show where the answer will come
        st.session_state['ai_float_page'] = '검색' if repeat[0] == '검색' else '채팅'
    with st.container(horizontal=True, horizontal_alignment='distribute', vertical_alignment='center'):
        st.markdown(':material/forum: **AI 검색**', width='content')
        with st.container(horizontal=True, width='content', gap='small'):
            if st.button('크게', icon=':material/open_in_full:', key='ai_float_expand', type='tertiary',
                         help='넓은 AI 검색 창으로 옮깁니다. 검색 결과, 뉴스와 사용 가이드를 함께 봅니다.'):
                _close()
                st.session_state[f'{PREFIX}_open'] = True
                st.rerun()
            st.button('닫기', icon=':material/close:', key='ai_float_close', type='tertiary', on_click=_close,
                      help='채팅창을 접습니다. 대화는 남아 있어 다시 열면 이어집니다.')
    page = st.segmented_control('작은 창 화면', PAGES, default=None if 'ai_float_page' in st.session_state else '채팅',
                                required=True, key='ai_float_page', label_visibility='collapsed', width='stretch')
    view.liner_notice(PREFIX, busy)
    action, repeated, *again = st.session_state.pop(f'{PREFIX}_repeat', (None, None))
    with st.container(key='ai_float_body'):
        if page == '채팅':
            ask_ai.note()
            view.liner_chat(PREFIX, repeated if action == '질문' else None, busy=busy, compact=True)
        elif page == '검색':
            query, submitted = view.liner_form(PREFIX, busy)
            found = st.container()
            picked = view.liner_news(PREFIX, busy)
            wanted = (again[0] if again else {'주제': repeated}) if action == '검색' else picked or ({'주제': query} if submitted else None)
            with found:
                if wanted and not busy:
                    view.queue_liner_search(PREFIX, wanted)
                view.liner_results(PREFIX, busy)
        elif page == '추천':
            view.liner_suggestions(PREFIX, busy)
        else:
            ai_history.talk_list(PREFIX, busy)
            view.liner_recent(PREFIX, busy)
