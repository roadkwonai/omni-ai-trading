"""The floating AI chat (bottom right): the same talk as the 'AI 검색' dialog, over any screen."""
import pytest
from streamlit.testing.v1 import AppTest

import api_status_view

PREFIX = 'quick_liner'


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import liner_search
    monkeypatch.setattr('liner_history.path', lambda: tmp_path / 'ai-search-history.sqlite3')
    monkeypatch.setattr(api_status_view, 'liner_issues', lambda: ('2026-11-20', [{'title': '나스닥 급락', 'stars': 5, 'link': 'https://a.kr'}]))
    monkeypatch.setattr(liner_search, 'search', lambda q: [{'제목': f'{q} 기사', '원문 링크': 'https://example.org'}])


@pytest.fixture
def asked(monkeypatch):
    import liner_search
    sent = []
    monkeypatch.setattr(liner_search, 'analyze', lambda messages: sent.append(messages) or ('답변입니다', []))
    return sent


def panel():
    def app():
        import ai_float
        ai_float.render()
    return AppTest.from_function(app).run(timeout=30)


def finish(at):
    if f'{PREFIX}_pending' in at.session_state:
        try:
            at.session_state[f'{PREFIX}_pending']['future'].result(timeout=5)
        except ValueError:
            pass
        at.run()


def keys(at):
    return {b.key for b in at.button}


def opened(at):
    at.button(key='ai_float_launch').click().run()
    assert not at.exception
    return at


def test_only_the_launcher_shows_until_it_is_pressed():
    at = panel()
    assert keys(at) == {'ai_float_launch'} and not at.chat_input
    opened(at)
    assert 'ai_float_launch' not in keys(at) and at.chat_input(key=f'{PREFIX}_follow_up')


def test_a_question_is_answered_in_the_panel_and_kept_for_the_dialog(asked):
    at = opened(panel())
    at.chat_input(key=f'{PREFIX}_follow_up').set_value('ETF가 뭐예요?').run()
    finish(at)
    assert any(m.value == '답변입니다' for m in at.markdown) and len(asked) == 1
    at.button(key='ai_float_close').click().run()
    assert keys(at) == {'ai_float_launch'}
    assert at.session_state[f'{PREFIX}_chat']['turns'][0]['question'] == 'ETF가 뭐예요?'
    opened(at)
    assert any(m.value == '답변입니다' for m in at.markdown)


def test_the_panel_moves_to_the_dialog_and_back(asked):
    at = opened(panel())
    at.button(key='ai_float_expand').click().run()
    assert at.session_state[f'{PREFIX}_open'] is True and 'ai_float_open' not in at.session_state
    assert not at.button and not at.chat_input  # the dialog draws the same widgets

    def dialog():
        import api_status_view
        api_status_view.liner_search(True, prefix='quick_liner')
    big = AppTest.from_function(dialog).run(timeout=30)
    big.button(key=f'{PREFIX}_to_float').click().run()
    assert big.session_state['ai_float_open'] is True and f'{PREFIX}_open' not in big.session_state


def test_a_suggestion_is_asked_once_and_shows_the_chat(asked):
    at = opened(panel())
    at.segmented_control(key='ai_float_page').set_value('추천').run()
    assert not at.chat_input
    at.button(key=f'{PREFIX}_suggestion_0').click().run()
    finish(at)
    at.run()
    assert len(asked) == 1 and at.segmented_control(key='ai_float_page').value == '채팅'
    assert any(m.value == '답변입니다' for m in at.markdown)


def test_search_news_and_history_show_results_on_the_search_page(asked):
    at = opened(panel())
    at.segmented_control(key='ai_float_page').set_value('검색').run()
    at.text_input(key=f'{PREFIX}_query').set_value('반도체')
    at.button(key=f'{PREFIX}_submit').click().run()
    finish(at)
    assert any('반도체 기사' in m.value for m in at.markdown)
    at.button(key=f'{PREFIX}_issue_0').click().run()
    finish(at)
    assert at.session_state[f'{PREFIX}_subject']['기사 링크'] == 'https://a.kr'
    at.segmented_control(key='ai_float_page').set_value('이력').run()
    at.session_state[f'{PREFIX}_card_history'] = True
    at.run()
    at.button(key=f'{PREFIX}_history_1').click().run()  # '검색 · 반도체'
    finish(at)
    assert at.segmented_control(key='ai_float_page').value == '검색'
    assert at.session_state[f'{PREFIX}_subject'] == {'주제': '반도체'} and not asked


def test_the_searched_topic_is_analysed_from_its_results_in_the_chat(asked):
    at = opened(panel())
    at.segmented_control(key='ai_float_page').set_value('검색').run()
    at.button(key=f'{PREFIX}_issue_0').click().run()
    finish(at)
    at.button(key=f'{PREFIX}_analyze').click().run()
    finish(at)
    assert '나스닥 급락' in asked[0][0]['content'] and at.segmented_control(key='ai_float_page').value == '채팅'


def test_a_failure_is_shown_and_retried_from_any_page(monkeypatch):
    import liner_search
    calls = []

    def analyze(messages):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError('Liner 요청에 실패했습니다(HTTP 429).')
        return '답변입니다', []
    monkeypatch.setattr(liner_search, 'analyze', analyze)
    at = opened(panel())
    at.chat_input(key=f'{PREFIX}_follow_up').set_value('ETF가 뭐예요?').run()
    finish(at)
    at.segmented_control(key='ai_float_page').set_value('검색').run()
    assert at.error
    at.button(key=f'{PREFIX}_retry').click().run()
    finish(at)
    assert len(calls) == 2 and not at.error and at.segmented_control(key='ai_float_page').value == '채팅'


def test_internal_mode_is_kept_when_the_panel_closes(asked):
    at = opened(panel())
    at.segmented_control(key=f'{PREFIX}_chat_mode').set_value('내부 데이터').run()
    at.button(key='ai_float_close').click().run()
    opened(at)
    at.chat_input(key=f'{PREFIX}_follow_up').set_value('코스피 전망은?').run()
    finish(at)
    assert not asked


def test_a_closed_panel_says_when_the_answer_came(monkeypatch):
    import threading

    import liner_search
    gate = threading.Event()
    monkeypatch.setattr(liner_search, 'analyze', lambda messages: (gate.wait(5), ('늦은 답', []))[1])
    at = opened(panel())
    at.chat_input(key=f'{PREFIX}_follow_up').set_value('ETF가 뭐예요?').run()
    at.button(key='ai_float_close').click().run()
    assert at.button(key='ai_float_launch').label == '답변 작성 중'
    gate.set()
    finish(at)
    assert at.button(key='ai_float_launch').label == 'AI 답 확인'
    opened(at)
    assert any(m.value == '늦은 답' for m in at.markdown)
    at.button(key='ai_float_close').click().run()
    assert at.button(key='ai_float_launch').label == 'AI에게 묻기'


def test_a_term_asked_while_waiting_still_shows_what_the_service_says(monkeypatch):
    import threading

    import liner_search
    gate = threading.Event()
    monkeypatch.setattr(liner_search, 'analyze', lambda messages: (gate.wait(5), ('늦은 답', []))[1])
    at = opened(panel())
    at.chat_input(key=f'{PREFIX}_follow_up').set_value('ETF가 뭐예요?').run()
    at.session_state['ai_draft'] = ('보호 한도', '서비스 설명')  # what a term button sets
    at.run()
    assert any('서비스 설명' in i.value for i in at.info)
    gate.set()
    finish(at)
    assert any('서비스 설명' in i.value for i in at.info)  # until a question is sent
    at.chat_input(key=f'{PREFIX}_follow_up').set_value('보호 한도는?').run()
    finish(at)
    assert not any('서비스 설명' in i.value for i in at.info)


def test_an_answer_that_came_while_closed_is_where_the_panel_opens(monkeypatch):
    import threading

    import liner_search
    gate = threading.Event()
    monkeypatch.setattr(liner_search, 'analyze', lambda messages: (gate.wait(5), ('늦은 답', []))[1])
    at = opened(panel())
    at.chat_input(key=f'{PREFIX}_follow_up').set_value('ETF가 뭐예요?').run()
    at.segmented_control(key='ai_float_page').set_value('추천').run()
    at.button(key='ai_float_close').click().run()
    gate.set()
    finish(at)
    opened(at)
    assert at.segmented_control(key='ai_float_page').value == '채팅' and any(m.value == '늦은 답' for m in at.markdown)
    at.segmented_control(key='ai_float_page').set_value('추천').run()
    at.button(key='ai_float_close').click().run()
    opened(at)
    assert at.segmented_control(key='ai_float_page').value == '추천'  # else where it was left


def test_a_failure_while_closed_is_not_called_an_answer_and_the_wide_window_clears_it(monkeypatch):
    import threading

    import liner_search
    gate = threading.Event()

    def analyze(messages):
        gate.wait(5)
        raise ValueError('실패')
    monkeypatch.setattr(liner_search, 'analyze', analyze)
    at = opened(panel())
    at.chat_input(key=f'{PREFIX}_follow_up').set_value('ETF가 뭐예요?').run()
    at.button(key='ai_float_close').click().run()
    gate.set()
    finish(at)
    assert at.button(key='ai_float_launch').label == 'AI 요청 실패'
    at.session_state[f'{PREFIX}_open'] = True  # read in the wide window instead
    at.run()
    del at.session_state[f'{PREFIX}_open']
    at.run()
    assert at.button(key='ai_float_launch').label == 'AI에게 묻기'
