"""'AI에게 묻기' from any screen: every path fills the chat box and shows what is already written down; nothing is sent by itself."""
import pytest
from streamlit.testing.v1 import AppTest

import api_status_view
import ask_ai

PREFIX = 'quick_liner'


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr('liner_history.path', lambda: tmp_path / 'ai-search-history.sqlite3')
    monkeypatch.setattr(api_status_view, 'liner_issues', lambda: (None, []))
    monkeypatch.setattr(api_status_view, 'credentials', lambda: {'LINER_API_KEY': True})
    isolated.redraw = ask_ai._redraw_panel
    monkeypatch.setattr(ask_ai, '_redraw_panel', lambda: None)  # AppTest keeps only the fragment's elements after a keyed rerun


@pytest.fixture
def asked(monkeypatch):
    import liner_search
    sent = []
    monkeypatch.setattr(liner_search, 'analyze', lambda messages: sent.append(messages) or ('답변입니다', []))
    return sent


def page():
    def app():
        import streamlit as st

        import ai_float
        import ask_ai
        import guides
        import status_cards as sc
        st.session_state.setdefault('main_tab', '장기투자')
        ai_float.render()
        ask_ai.guide_questions('longterm')
        sc.row([{'label': '주문가능금액', 'value': '1,234,567원', 'help': '지금 주문에 쓸 수 있는 돈입니다.'}, {'label': '보유', 'value': '3종'}], ask='demo')
    return AppTest.from_function(app).run(timeout=30)


def draft(at):
    return at.chat_input(key=f'{PREFIX}_follow_up').value


def test_numbers_that_look_like_money_or_accounts_are_hidden():
    for text, masked in {
        '총자산 1,509,321원': '총자산 ○원', '+19,350원 실현': '○원 실현', '수익률 -1.28%': '수익률 ○%', '005930 35주': '○ ○주',
        '계좌 12345678901': '계좌 ○', '약 3억 원': '약 ○억 원', '50.00억원': '○억원',
        'KODEX 200': 'KODEX 200', '60분 VWAP': '60분 VWAP', '선별 v2': '선별 v2', '09:20 시작': '09:20 시작', '2026-11-24': '2026-11-24',
    }.items():
        assert ask_ai.hide_numbers(text) == masked, text


def test_questions_fit_the_length_and_kind_of_what_was_picked():
    assert ask_ai.question('관심종목 강도 진입', '트레이딩 현황') == '‘관심종목 강도 진입’의 뜻을 쉽게 알려 주세요. (트레이딩 현황 화면)'
    sentence = '국면이 바뀌면 손절 기준을 1%로 좁힙니다.'
    assert ask_ai.question(sentence, '') == '이 내용을 쉽게 설명해 주세요: “국면이 바뀌면 손절 기준을 ○%로 좁힙니다.”'
    assert '\n' not in ask_ai.question('두 줄\n  글', '') and len(ask_ai.question('가' * 500, '')) < 200
    assert ask_ai.question('‘따옴표’ “글”', '') == '‘따옴표 글’의 뜻을 쉽게 알려 주세요.'
    assert ask_ai.question('   ', '') is None and ask_ai.question('', '') is None


def test_every_drafted_question_is_an_explanation_not_an_account_lookup():
    import liner_local_data
    for text in ('Jev 수익률', '체결 내역', '실계좌 1 잔고', '보유 종목', '주문가능금액', '오늘 손익', '실행기 상태'):
        assert liner_local_data.resolve(ask_ai.question(text, '실계좌')) is None, text


def test_what_is_already_written_down_is_found():
    assert ask_ai.known('저수지')[0] == '저수지'
    assert ask_ai.known('  관심종목  강도 진입 ')[0] == '관심종목 강도 진입'
    assert ask_ai.known('여기서 확정 국면이 바뀌면')[0] == '확정 국면'
    assert ask_ai.known('아무 말') is None and ask_ai.known('상관없이') is None


def test_a_term_fills_the_box_shows_its_meaning_and_sends_nothing(asked):
    at = page()
    at.button(key='ask_terms_longterm_저수지').click().run()
    assert not at.exception and draft(at) == '‘저수지’의 뜻을 쉽게 알려 주세요. (장기투자 화면)'
    import guides
    assert [i.value for i in at.info] == [guides.GUIDES['longterm']['terms']['저수지']] and asked == []
    at.button(key='ask_terms_longterm_저수지').click().run()  # the same term can be picked again
    assert draft(at)
    at.chat_input(key=f'{PREFIX}_follow_up').set_value('저수지가 뭐예요?').run()
    at.session_state[f'{PREFIX}_pending']['future'].result(timeout=5)
    at.run()
    assert len(asked) == 1 and not at.info  # the hint goes once it is asked


def test_the_screen_button_and_a_card_fill_the_box(asked):
    at = page()
    at.button(key='ask_screen_longterm').click().run()
    assert draft(at) == '장기투자 화면은 어떤 순서로 보면 되나요?'
    at.button(key='ask_cards_demo_주문가능금액').click().run()
    assert draft(at) == '‘주문가능금액’의 뜻을 쉽게 알려 주세요. (장기투자 화면)'
    assert any('지금 주문에 쓸 수 있는 돈' in m.value for m in at.info)  # the card's own help, shown without a request
    assert asked == []


def test_the_empty_chat_offers_questions_of_the_current_screen(asked):
    at = page()
    at.button(key='ai_float_launch').click().run()
    labels = [b.label for b in at.button if b.key.startswith('ask_idea_')]
    assert len(labels) == 3 and any('저수지' in x or '비교 전략' in x or '확정 국면' in x for x in labels)
    at.button(key='ask_idea_0').click().run()
    assert draft(at) and asked == []


def test_ideas_exist_for_every_tab():
    import prefs
    for tab in prefs.TABS:
        ideas = ask_ai.ideas(tab)
        assert len(ideas) == 3 and len(set(ideas)) == 3, tab


def test_a_picked_text_opens_the_panel_on_the_chat_page(asked):
    at = page()
    at.button(key='ai_float_launch').click().run()
    at.segmented_control(key='ai_float_page').set_value('이력').run()
    at.button(key='ask_screen_longterm').click().run()
    assert at.segmented_control(key='ai_float_page').value == '채팅' and draft(at)


def test_the_selection_handler_reads_the_component_and_ignores_rubbish(monkeypatch):
    filled = []
    monkeypatch.setattr(ask_ai, 'fill', lambda q, known=None: filled.append(q))
    for text, card, expected in (('총자산 1,509,321원', '', '총자산 ○원'), ('+19,350원', '오늘 실현 · 모의계좌', '오늘 실현 · 모의계좌'),
                                 ('국면', '확정 국면', '국면'), ('조정', '확정 국면', '확정 국면: 조정')):  # a bare number is asked about by its card's name
        monkeypatch.setattr(ask_ai.st, 'session_state', {'ask_selection': {'picked': {'text': text, 'card': card}}, 'main_tab': '종합 현황'})
        ask_ai.picked()
        assert filled.pop() == f'‘{expected}’의 뜻을 쉽게 알려 주세요. (종합 현황 화면)'
    state = {'ask_selection': {'picked': {'text': '국면', 'card': '', 'dialog': True}}, 'dialog_open': object()}
    monkeypatch.setattr(ask_ai.st, 'session_state', state)
    ask_ai.picked()
    assert filled.pop() and 'dialog_open' not in state and state['ai_close_dialog']  # the pop-up gives way to the chat
    for value in (None, {}, {'picked': None}, {'picked': 5}, {'picked': {}}, {'picked': {'text': 5, 'card': None}}, {'picked': {'text': '  ', 'card': ''}},
                  {'picked': {'text': '+19,350원', 'card': ''}}, {'picked': {'text': '12:03', 'card': None}}):
        monkeypatch.setattr(ask_ai.st, 'session_state', {'ask_selection': value})
        ask_ai.picked()
    assert filled == []


def test_nothing_is_offered_without_a_key(monkeypatch):
    monkeypatch.setattr(api_status_view, 'credentials', lambda: {})

    def app():
        import ask_ai
        import status_cards as sc
        ask_ai.guide_questions('longterm')
        sc.row([{'label': '보유', 'value': '3종'}], ask='demo')
    at = AppTest.from_function(app).run()
    assert not at.button and not at.caption and not at.exception


def test_the_drag_button_can_be_switched_off(tmp_path):
    import prefs
    assert prefs.load(tmp_path / 'none.json')['ask_selection'] is True
    assert prefs.save(tmp_path / 'p.json', {**prefs.DEFAULTS, 'ask_selection': False})['ask_selection'] is False


@pytest.mark.parametrize('text, masked', [
    ('총자산 1,509,321', '총자산 ○'), ('손익 -90,802', '손익 ○'), ('평균가 189,050 수량 3주', '평균가 ○ 수량 ○주'),
    ('계좌 123-45-678901 잔고', '계좌 ○ 잔고'), ('2026-11-24 기준', '2026-11-24 기준'), ('KODEX 200 주가', 'KODEX 200 주가'),
    ('코스피 200 주식 원인', '코스피 200 주식 원인'), ('매수 3 주', '매수 ○ 주'), ('5주년', '5주년'),
])
def test_numbers_without_a_unit_and_account_numbers_are_hidden_too(text, masked):
    assert ask_ai.hide_numbers(text) == masked


@pytest.mark.parametrize('button', ['ask_idea_0', 'ask_cards_demo_보유'])
def test_asking_while_the_panel_is_open_fills_and_never_sends(asked, monkeypatch, button):
    monkeypatch.setattr(ask_ai, '_redraw_panel', isolated.redraw)
    at = page()
    at.button(key='ai_float_launch').click().run()
    at.button(key=button).click().run()
    for _ in range(2):
        assert f'{PREFIX}_pending' not in at.session_state and not asked
        at.run()
    assert draft(at) is None or asked == []
