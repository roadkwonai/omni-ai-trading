"""Past AI talks and searches with the answers that were received (docs/ai-search-history-plan.md). Opening one sends nothing."""
from __future__ import annotations

import sqlite3

import streamlit as st

import api_status_view as view
import liner_history

PAGE = 20
# A row is its title and time; its icons show on hover (always on a touch screen).
CSS = """
[class*="_liner_chat_history"] > [data-testid="stLayoutWrapper"]:has(> [class*="_liner_empty"]) { margin: auto; }
[class*="_liner_empty"] { text-align: center; }
[class*="_liner_issue_"] button, [class*="_liner_issue_"] button > div { justify-content: flex-start; text-align: left; }
[class*="_liner_row_"] { flex-wrap: nowrap; border-radius: 8px; position: relative; }
[class*="_liner_row_"]:hover { background: rgba(148, 163, 184, .16); }
[class*="_liner_row_"]:has([class*="_liner_talk_"] [data-testid="stBaseButton-secondary"]) { background: color-mix(in srgb, var(--primary-color, #16a34a) 16%, transparent); }
[class*="_liner_row_"] > :first-child { flex: 1 1 0; min-width: 0; }
[class*="_liner_row_"] > :not(:first-child) { flex: 0 0 auto; }
[class*="_liner_row_"] [class*="_liner_talk_"] button { border: 0; background: transparent; padding: .25rem .5rem; }
[class*="_liner_row_"] [class*="_liner_talk_"] :is(button, button > div) { justify-content: flex-start; text-align: left; min-width: 0; }
[class*="_liner_row_"] [class*="_liner_talk_"] button p { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
[class*="_liner_row_"] > :is([class*="_liner_archive_"], :has(> [class*="_liner_archive_"])) { order: 1; }
[class*="_liner_row_"] > :is([class*="_liner_drop_"], :has(> [class*="_liner_drop_"])) { order: 2; }
[class*="_liner_row_"] > :is([class*="_liner_keep_"], :has(> [class*="_liner_keep_"])) { order: 3; }
[class*="_liner_row_"] :is([class*="_liner_archive_"], [class*="_liner_drop_"]) button { padding: 0; width: 1.9rem; min-width: 1.9rem; justify-content: center; }
[class*="_liner_row_"] :is([class*="_liner_archive_"], [class*="_liner_drop_"]) button p { position: absolute; width: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
[class*="_liner_row_"]:has([class*="_liner_keep_"]) [class*="_liner_drop_"] button { width: auto; padding: 0 .6rem; background: #dc2626; color: #fff; }
[class*="_liner_row_"]:has([class*="_liner_keep_"]) [class*="_liner_drop_"] button p { position: static; width: auto; clip-path: none; }
@media (hover: hover) {
    [class*="_liner_row_"]:not(:hover, :focus-within):not(:has([class*="_liner_keep_"])) :is([class*="_liner_archive_"], [class*="_liner_drop_"]) { position: absolute; right: 0; opacity: 0; }
}
"""


def _read(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except (OSError, sqlite3.Error):
        st.warning('지난 대화를 불러오지 못했습니다.')
        return None


def as_chat(talk: dict) -> dict:
    """A stored talk as the chat's own thread: asking on continues it."""
    from liner_local_data import InternalQuery

    def query(saved):
        try:
            return InternalQuery(**saved) if saved else None
        except TypeError:  # written by another version
            return None
    return {'id': talk['id'], 'subject': talk['subject'], 'turns': [{**turn, 'local_query': query(turn['local_query'])} for turn in talk['turns']]}


def up_to_date(thread: dict) -> dict:
    """The talk as it is stored now: another window may have gone on with it."""
    try:
        talk = liner_history.load(thread['id']) if thread.get('id') else None
    except (OSError, sqlite3.Error):
        talk = None
    return as_chat(talk) if talk else thread


def _seen(prefix: str, thread_id: int) -> None:
    if st.session_state.get(f'{prefix}_arrived', {}).get('id') == thread_id:
        st.session_state.pop(f'{prefix}_arrived')


def open_talk(prefix: str, thread_id: int) -> None:
    """Widget callback: put a stored talk back into the chat, or a stored search back into the results."""
    try:
        talk = liner_history.load(thread_id)
    except (OSError, sqlite3.Error):
        talk = None
    if not talk or not talk['turns']:
        st.session_state[f'{prefix}_notice'] = ('warning', '이 대화를 열지 못했습니다. 지워졌거나 기록을 읽을 수 없습니다.', None)
        _seen(prefix, thread_id)
        return
    view.leave(prefix, talk['kind'], thread_id)
    st.session_state.pop(f'{prefix}_guide', None)
    if st.session_state.get(f'{prefix}_notice', ('',))[0] == 'warning':  # an error keeps its question and its 다시 요청
        st.session_state.pop(f'{prefix}_notice')
    _seen(prefix, thread_id)
    if talk['kind'] == '검색':
        st.session_state[f'{prefix}_subject'] = talk['subject'] or {'주제': talk['title']}
        st.session_state[f'{prefix}_results'] = talk['turns'][-1]['sources']
        st.session_state[f'{prefix}_found'] = thread_id
        st.session_state[f'{prefix}_latest'] = '검색'
    else:
        view.forget_search(prefix)
        st.session_state[f'{prefix}_chat'] = as_chat(talk)
        st.session_state[f'{prefix}_latest'] = '질문'
    if prefix == 'quick_liner':  # the small window's page
        st.session_state['ai_float_page'] = '검색' if talk['kind'] == '검색' else '채팅'


def _rows(prefix: str, rows: list[dict], archived: bool) -> None:
    searched = st.session_state.get(f'{prefix}_latest') == '검색'
    current = st.session_state.get(f'{prefix}_found') if searched else (st.session_state.get(f'{prefix}_chat') or {}).get('id')
    asked = (st.session_state.get(f'{prefix}_pending') or {}).get('thread', {}).get('id')
    for row in rows:
        thread_id = row['id']
        info = view.when(row['updated']) + (f" · 답변 {row['turns']}개" if row['kind'] == '대화' else ' · 검색')
        with st.container(horizontal=True, vertical_alignment='center', gap=None, key=f'{prefix}_row_{thread_id}'):
            st.button(f"{view.markdown_label(row['title'])}  \n:gray[:small[{info}]]", key=f'{prefix}_talk_{thread_id}', width='stretch', wrap=True,
                      on_click=open_talk, args=(prefix, thread_id), type='secondary' if thread_id == current else 'tertiary')
            dropping = st.session_state.get(f'{prefix}_dropping') == thread_id
            st.button('지우기', icon=':material/delete:', key=f'{prefix}_drop_{thread_id}', type='tertiary',  # the same place and kind: the keyboard stays on it
                      disabled=thread_id == asked, on_click=_drop, args=(prefix, thread_id),
                      help='답을 기다리는 대화는 지울 수 없습니다.' if thread_id == asked else '한 번 더 누르면 받은 답까지 지웁니다. 되돌릴 수 없습니다.')
            if dropping:
                st.button('취소', key=f'{prefix}_keep_{thread_id}', on_click=st.session_state.pop, args=(f'{prefix}_dropping', None))
            else:
                st.button('보관 해제' if archived else '보관', icon=':material/unarchive:' if archived else ':material/archive:',
                          key=f'{prefix}_archive_{thread_id}', type='tertiary', on_click=_change, args=(prefix, liner_history.archive, thread_id, not archived),
                          help='다시 목록에 보입니다.' if archived else '보관함으로 옮깁니다. 모두 지우기에도 지워지지 않습니다.')


def _drop(prefix: str, thread_id: int) -> None:
    if st.session_state.get(f'{prefix}_dropping') == thread_id:
        _change(prefix, liner_history.delete, thread_id)
    else:
        st.session_state[f'{prefix}_dropping'] = thread_id


def _change(prefix: str, change, thread_id: int, *args) -> None:
    """Widget callback: archive, put back or delete one."""
    try:
        change(thread_id, *args)
        _forget_deleted(prefix)
    except (OSError, sqlite3.Error):
        st.session_state[f'{prefix}_notice'] = ('warning', '지난 대화를 바꾸지 못했습니다. 잠시 뒤 다시 시도하세요.', None)
    st.session_state.pop(f'{prefix}_dropping', None)


def _forget_deleted(prefix: str) -> None:
    """Empty the chat and the results when they are no longer there."""
    chat, found = st.session_state.get(f'{prefix}_chat'), st.session_state.get(f'{prefix}_found')
    if chat and chat.get('id') and not liner_history.load(chat['id']):
        st.session_state.pop(f'{prefix}_chat')
    if found and not liner_history.load(found):
        view.forget_search(prefix)


def _turn_box(prefix: str) -> None:
    st.session_state[f'{prefix}_box'] = not st.session_state.get(f'{prefix}_box')


def talk_list(prefix: str, busy: bool) -> None:
    """The left side of the wide window and the small window's 이력 page: a row opens in the chat (or in the search results)."""
    archived = st.session_state.get(f'{prefix}_box', False)
    find = st.text_input('지난 대화 찾기', key=f'{prefix}_talk_find', max_chars=100, placeholder='보관함에서 찾기' if archived else '지난 대화에서 찾기',
                         icon=':material/search:', label_visibility='collapsed')
    limit = st.session_state.get(f'{prefix}_talk_limit', PAGE)
    rows = _read(liner_history.threads, find=find, limit=limit + 1, archived=archived)
    with st.container(border=False, gap='xxsmall', key=f'{prefix}_talk_list'):
        if rows:
            _rows(prefix, rows[:limit], archived)
            if len(rows) > limit:
                st.button('더 보기', icon=':material/expand_more:', key=f'{prefix}_talk_more', width='stretch', type='tertiary',
                          on_click=st.session_state.__setitem__, args=(f'{prefix}_talk_limit', limit + PAGE))
        elif rows is not None:
            st.caption('찾는 대화가 없습니다.' if find.strip() else '보관한 대화가 없습니다.' if archived
                       else '아직 저장된 대화가 없습니다. 묻거나 검색하면 답과 함께 여기에 남습니다.')
    with st.container(horizontal=True, horizontal_alignment='distribute', key=f'{prefix}_talk_foot'):
        st.button('지난 대화' if archived else '보관함', icon=':material/arrow_back:' if archived else ':material/archive:', key=f'{prefix}_box_button',
                  type='tertiary', on_click=_turn_box, args=(prefix,))
        if rows and not archived:
            with st.popover('모두 지우기', icon=':material/delete_sweep:', type='tertiary'):
                st.write('보관하지 않은 지난 대화와 다시 요청하기 목록을 모두 지웁니다. 되돌릴 수 없습니다. 보관함은 남습니다.')
                st.button('모두 지우기', key=f'{prefix}_talk_clear', type='primary', disabled=busy, on_click=_clear, args=(prefix,))


def _clear(prefix: str) -> None:
    try:
        liner_history.clear()
        _forget_deleted(prefix)
    except (OSError, sqlite3.Error):
        st.session_state[f'{prefix}_notice'] = ('warning', '지난 대화를 지우지 못했습니다.', None)
        return
    st.session_state.pop(f'{prefix}_arrived', None)
