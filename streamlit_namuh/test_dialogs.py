"""A pop-up stays until the user closes it: timers redraw the whole app (st.rerun) while it is open (2026-11-24)."""
import re
from concurrent.futures import Future
from pathlib import Path

from streamlit.proto.WidgetStates_pb2 import WidgetState, WidgetStates
from streamlit.testing.v1 import AppTest

import dialogs


def page():
    import streamlit as st

    import dialogs
    dialogs.keep_open()
    if st.button('첫째', key='first'):
        dialogs.open_dialog('첫째', st.write, '첫째 내용')
    if st.button('둘째', key='second'):
        dialogs.trades_dialog([], {})
    st.button('다시', key='other')


def shown(at):
    assert not at.exception
    return [m.value for m in at.markdown if '내용' in m.value] + [i.value for i in at.info]


def dismiss(at):
    """What the browser sends when the user closes the pop-up (X, Esc or a click outside)."""
    block = next(b for b in at._tree[2].children.values() if b.proto is not None and b.proto.dialog.is_open)
    return at._run(WidgetStates(widgets=[WidgetState(id=block.proto.dialog.id, trigger_value=True)]))


def test_a_popup_stays_through_full_reruns_until_the_user_closes_it():
    at = AppTest.from_function(page).run()
    assert shown(at) == []
    assert shown(at.button(key='first').click().run()) == ['첫째 내용']
    for _ in range(3):  # the launchctl read, the broker read, an AI answer …
        assert shown(at.run()) == ['첫째 내용']
    assert shown(dismiss(at)) == []
    assert shown(at.run()) == []


def test_the_last_opened_popup_is_the_one_kept():
    at = AppTest.from_function(page).run()
    at.button(key='first').click().run()
    assert shown(at.button(key='second').click().run()) == ['오늘 거래가 없습니다.']
    assert shown(at.run()) == ['오늘 거래가 없습니다.']


def body_page():
    import streamlit as st
    from streamlit.runtime.scriptrunner import RerunData, RerunException

    import dialogs

    def body():
        st.write('본문 내용')
        if st.button('저장', key='save'):
            st.rerun()
        if st.button('중단', key='interrupted'):  # another request stops the body: raised by Streamlit, not by the body
            raise RerunException(RerunData())
    dialogs.keep_open()
    if st.button('열기', key='first'):
        dialogs.open_dialog('본문', body)


def test_st_rerun_called_by_the_body_closes_its_popup():
    at = AppTest.from_function(body_page).run()
    at.button(key='first').click().run()
    assert shown(at.run()) == ['본문 내용']
    assert shown(at.button(key='save').click().run()) == []
    assert shown(at.run()) == []


def test_a_rerun_that_only_interrupts_the_body_keeps_its_popup():
    at = AppTest.from_function(body_page).run()
    at.button(key='first').click().run()
    assert shown(at.button(key='interrupted').click().run()) == ['본문 내용']


def test_every_popup_is_opened_through_dialogs_and_the_app_keeps_it():
    """st.dialog alone closes at the next full rerun. 'AI 검색' keeps its own open state (quick_liner_open)."""
    folder = Path(dialogs.__file__).parent
    raw = {p.name: len(re.findall(r'\bst\.dialog\(', p.read_text(encoding='utf-8')))
           for p in folder.glob('*.py') if not p.name.startswith('test_')}
    assert {k: n for k, n in raw.items() if n} == {'dialogs.py': 1, 'api_status_view.py': 1}
    main = (folder / 'app.py').read_text(encoding='utf-8').split('def main():')[1]
    assert main.index('open_liner_search()') < main.index('dialogs.keep_open()') < main.index('load_agent_config()')


def test_a_finished_background_read_waits_for_the_popup_to_close(monkeypatch):
    """Drawing again hides the pop-up for a moment and loses its scroll, tab and focus; closing it redraws the app anyway."""
    import app
    ticks, reruns, state = [], [], {dialogs.OPEN: lambda: None}
    read = Future()
    read.set_result(None)
    monkeypatch.setattr(app.st, 'fragment', lambda **_: lambda fn: ticks.append(fn) or fn)
    monkeypatch.setattr(app.st, 'rerun', lambda: reruns.append(True))
    monkeypatch.setattr(app.st, 'session_state', state)
    app.rerun_when_read_completes(lambda: read)
    ticks[0]()
    assert not reruns
    state.clear()
    ticks[0]()
    assert reruns


def test_a_popup_of_executor_states_opens_with_the_states_read(monkeypatch):
    """It is not drawn again while open, so it must not be opened with '확인 중'."""
    import app
    import automation_status
    read = {name: '실행 중' for name in automation_status.LABELS}
    monkeypatch.setattr(automation_status, 'peek_processes', lambda: None)
    monkeypatch.setattr(app, 'processes', lambda: read)
    monkeypatch.setattr(app, 'rerun_when_read_completes', lambda start: None)
    monkeypatch.setattr(app.st, 'session_state', {'ov_d_trades': True})
    assert set(app.display_process_states().values()) == {'확인 중'}
    source = ''.join(p.read_text(encoding='utf-8') for p in Path(dialogs.__file__).parent.glob('*.py') if not p.name.startswith('test_'))
    for key in app.STATE_POPUPS:
        monkeypatch.setattr(app.st, 'session_state', {key: True})
        assert app.display_process_states() == read
        assert source.count(f"'{key}'") == 2  # the button and STATE_POPUPS
