"""Past talks: what was asked and answered comes back after a new chat, a reload or a closed window."""
import time

import pytest
from streamlit.testing.v1 import AppTest

import api_status_view as view
import liner_history

PREFIX = 'quick_liner'


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import liner_search
    monkeypatch.setattr('liner_history.path', lambda: tmp_path / 'ai-search-history.sqlite3')
    monkeypatch.setattr(view, 'liner_issues', lambda: ('2026-11-20', [{'title': '나스닥 급락', 'stars': 5, 'link': 'https://a.kr'}]))
    monkeypatch.setattr(liner_search, 'search', lambda q: [{'제목': f'{q} 기사', '원문 링크': 'https://example.org', '발행일': '', '요약': ''}])


@pytest.fixture
def asked(monkeypatch):
    import liner_search
    sent = []
    monkeypatch.setattr(liner_search, 'analyze', lambda messages: sent.append(messages) or (
        f'답변 {len(sent)}', [{'제목': '출처', '원문 링크': 'https://example.org', '발행일': '', '요약': ''}]))
    return sent


def wide():
    def app():
        import api_status_view
        api_status_view.liner_search(True, prefix='quick_liner')
    return AppTest.from_function(app).run(timeout=30)


def small():
    def app():
        import ai_float
        ai_float.render()
    at = AppTest.from_function(app).run(timeout=30)
    at.button(key='ai_float_launch').click().run()
    return at


def finish(at):
    if f'{PREFIX}_pending' in at.session_state:
        try:
            at.session_state[f'{PREFIX}_pending']['future'].result(timeout=5)
        except ValueError:
            pass
        at.run()
    assert not at.exception


def ask(at, question):
    at.chat_input(key=f'{PREFIX}_follow_up').set_value(question).run()
    finish(at)


def shown(at):
    return [m.value for m in at.markdown]


def of_kind(kind):
    return [row for row in liner_history.threads() if row['kind'] == kind]


def drop(at, thread_id):
    at.button(key=f'{PREFIX}_drop_{thread_id}').click().run()
    assert liner_history.load(thread_id)  # asked once more, by the same button
    at.button(key=f'{PREFIX}_drop_{thread_id}').click().run()


def talk_rows(at):
    return [int(b.key.rsplit('_', 1)[1]) for b in at.button if b.key.startswith(f'{PREFIX}_talk_') and b.key.rsplit('_', 1)[1].isdigit()]


def test_an_answer_is_listed_after_a_new_chat_and_a_reload_and_costs_nothing(asked):
    at = wide()
    ask(at, 'ETF가 뭐예요?')
    ask(at, '더 쉽게 알려 주세요')
    at.button(key=f'{PREFIX}_new_chat').click().run()
    assert '답변 1' not in shown(at)
    fresh = wide()  # another browser session
    row = fresh.button(key=f'{PREFIX}_talk_{talk_rows(fresh)[0]}')
    assert 'ETF가 뭐예요?' in row.label and '답변 2개' in row.label
    row.click().run()
    assert '답변 1' in shown(fresh) and '답변 2' in shown(fresh) and len(asked) == 2
    ask(fresh, '그럼 펀드는요?')  # goes on in the same talk, with its context
    assert len(liner_history.threads()) == 1 and liner_history.threads()[0]['turns'] == 3
    assert [m['content'] for m in asked[2][:4]] == ['ETF가 뭐예요?', '답변 1', '더 쉽게 알려 주세요', '답변 2']


def test_an_answer_that_arrives_after_the_screen_closed_is_kept(monkeypatch):
    import liner_search
    monkeypatch.setattr(liner_search, 'analyze', lambda messages: (time.sleep(0.4), ('늦은 답', []))[1])
    at = wide()
    at.chat_input(key=f'{PREFIX}_follow_up').set_value('ETF가 뭐예요?').run()
    future = at.session_state[f'{PREFIX}_pending']['future']
    del at
    future.result(timeout=5)
    assert liner_history.load(liner_history.threads()[0]['id'])['turns'][0]['answer'] == '늦은 답'


def test_a_failed_request_leaves_no_talk(monkeypatch):
    import liner_search
    monkeypatch.setattr(liner_search, 'analyze', lambda messages: (_ for _ in ()).throw(ValueError('실패')))
    at = wide()
    ask(at, 'ETF가 뭐예요?')
    assert at.error and liner_history.threads() == []


def test_a_search_comes_back_with_its_results_and_article(asked):
    at = wide()
    at.button(key=f'{PREFIX}_issue_0').click().run()
    finish(at)
    fresh = wide()
    fresh.button(key=f'{PREFIX}_talk_{talk_rows(fresh)[0]}').click().run()
    assert any('나스닥 급락 기사' in m for m in shown(fresh))
    assert fresh.session_state[f'{PREFIX}_subject'] == {'주제': '나스닥 급락', '매매일지 기준일': '2026-11-20', '기사 링크': 'https://a.kr'}
    fresh.button(key=f'{PREFIX}_analyze').click().run()  # the analysis still knows the article
    finish(fresh)
    assert 'https://a.kr' in asked[0][0]['content']
    talk = liner_history.load(of_kind('대화')[0]['id'])
    assert talk['subject']['주제'] == '나스닥 급락' and talk['title'].startswith('나스닥 급락')


def test_internal_answers_are_kept_here_but_never_sent_even_after_reopening(asked, monkeypatch):
    import liner_local_data
    monkeypatch.setattr(liner_local_data, 'answer', lambda query, *a: f'잔고 비밀 {query.period}')
    at = wide()
    ask(at, '오늘 모의 체결 내역은?')
    assert liner_history.recent() == []  # not among the requests to send again
    fresh = wide()
    fresh.button(key=f'{PREFIX}_talk_{talk_rows(fresh)[0]}').click().run()
    assert '잔고 비밀 오늘' in shown(fresh)
    ask(fresh, '어제는?')  # the reopened talk still knows which lookup it was
    assert '잔고 비밀 어제' in shown(fresh) and asked == []
    ask(fresh, 'ETF가 뭐예요?')
    assert len(asked[0]) == 1 and '잔고 비밀' not in asked[0][0]['content']



def test_the_list_grows_by_pages():
    for i in range(25):
        liner_history.save('대화', f'대화 {i}', {'question': f'q{i}', 'answer': 'a'})
    at = small()
    at.segmented_control(key='ai_float_page').set_value('이력').run()
    assert len(talk_rows(at)) == 20
    at.button(key=f'{PREFIX}_talk_more').click().run()
    assert len(talk_rows(at)) == 25 and f'{PREFIX}_talk_more' not in [b.key for b in at.button]


def test_the_small_window_opens_a_talk_and_a_search_where_they_belong(asked):
    at = small()
    ask(at, 'ETF가 뭐예요?')
    at.button(key=f'{PREFIX}_new_chat').click().run()
    at.segmented_control(key='ai_float_page').set_value('검색').run()
    at.text_input(key=f'{PREFIX}_query').set_value('반도체')
    at.button(key=f'{PREFIX}_submit').click().run()
    finish(at)
    talk, found = of_kind('대화')[0]['id'], of_kind('검색')[0]['id']
    at.segmented_control(key='ai_float_page').set_value('이력').run()
    at.button(key=f'{PREFIX}_talk_{talk}').click().run()
    assert at.segmented_control(key='ai_float_page').value == '채팅' and '답변 1' in shown(at)
    at.segmented_control(key='ai_float_page').set_value('이력').run()
    at.button(key=f'{PREFIX}_talk_{found}').click().run()
    assert at.segmented_control(key='ai_float_page').value == '검색' and any('반도체 기사' in m for m in shown(at))



def test_a_deleted_or_unreadable_talk_is_said(asked):
    at = wide()
    ask(at, 'ETF가 뭐예요?')
    row = liner_history.threads()[0]['id']
    liner_history.delete(row)
    at.button(key=f'{PREFIX}_talk_{row}').click().run()
    assert not at.exception and any('열지 못했습니다' in w.value for w in at.warning)
    liner_history.path().write_bytes(b'broken' * 500)
    at.run()
    assert not at.exception and any('불러오지 못했습니다' in w.value for w in at.warning)


def test_the_talk_is_copied_whole():
    talk = liner_history.load(liner_history.save('대화', '제목', {'question': '질문 1', 'answer': '==답== $1에서 $2'}))
    assert view.turn_markdown(talk['turns'][0]) == '**답** $1에서 $2'


def talks(n):
    return [liner_history.save('대화', f'대화 {i}', {'question': f'q{i}', 'answer': f'답변 {i}'}) for i in range(n)]



def test_the_left_list_opens_a_talk_and_it_goes_on_there(asked):
    at = wide()
    ask(at, 'ETF가 뭐예요?')
    at.button(key=f'{PREFIX}_new_chat').click().run()
    ask(at, '커버드콜은요?')
    newer, older = (r['id'] for r in liner_history.threads())
    before = liner_history.load(older)['updated']
    fresh = wide()  # another day, another session
    assert talk_rows(fresh) == [newer, older] and '답변 1' not in shown(fresh)
    fresh.button(key=f'{PREFIX}_talk_{older}').click().run()
    assert '답변 1' in shown(fresh) and '답변 2' not in shown(fresh)
    assert [b.key for b in fresh.button if b.proto.type == 'secondary' and b.key.startswith(f'{PREFIX}_talk_') and b.key[-1].isdigit()] == [f'{PREFIX}_talk_{older}']  # the open one stands out
    fresh.chat_input(key=f'{PREFIX}_follow_up').set_value('수수료는 어떻게 되나요?').run()
    finish(fresh)
    assert '답변 3' in shown(fresh) and [m['content'] for m in asked[2][:2]] == ['ETF가 뭐예요?', '답변 1']  # with the talk's own context
    talk = liner_history.load(older)
    assert [t['question'] for t in talk['turns']] == ['ETF가 뭐예요?', '수수료는 어떻게 되나요?']
    assert talk['updated'] > before and talk_rows(fresh) == [older, newer]  # its date is the last talk's
    fresh.button(key=f'{PREFIX}_talk_{newer}').click().run()
    assert '답변 2' in shown(fresh) and '답변 1' not in shown(fresh)
    fresh.button(key=f'{PREFIX}_new_chat').click().run()
    assert '답변 2' not in shown(fresh) and len(liner_history.threads()) == 2


def test_the_left_list_finds_and_narrows(asked):
    first, second = talks(2)
    at = wide()
    at.text_input(key=f'{PREFIX}_talk_find').set_value('답변 0').run()
    assert talk_rows(at) == [first]
    at.text_input(key=f'{PREFIX}_talk_find').set_value('없는 말').run()
    assert any('찾는 대화가 없습니다' in c.value for c in at.caption) and not at.exception
    at.text_input(key=f'{PREFIX}_talk_find').set_value('').run()
    assert talk_rows(at) == [second, first]


def folded(at, name):
    at.button(key=f'{PREFIX}_fold_{name}_button').click().run()
    assert not at.exception
    return at


def test_the_left_and_right_panels_fold_and_stay_folded(asked):
    made = talks(1)[0]
    at = wide()
    assert talk_rows(at) == [made] and f'{PREFIX}_issue_0' in [b.key for b in at.button]
    folded(at, 'nav')
    assert talk_rows(at) == [] and not at.text_input(key=f'{PREFIX}_query').disabled
    ask(at, 'ETF가 뭐예요?')
    at.button(key=f'{PREFIX}_new_chat').click().run()  # still within reach when the list is folded
    folded(at, 'side')
    assert not [b for b in at.button if b.key.startswith((f'{PREFIX}_issue_', f'{PREFIX}_suggestion_'))]
    at.button(key=f'{PREFIX}_guide_button').click().run()
    assert f'{PREFIX}_fold_nav_button' not in [b.key for b in at.button]
    at.button(key=f'{PREFIX}_guide_button').click().run()
    assert talk_rows(at) == [] and not [b for b in at.button if b.key.startswith(f'{PREFIX}_issue_')]
    folded(folded(at, 'nav'), 'side')
    assert len(talk_rows(at)) == 2 and f'{PREFIX}_issue_0' in [b.key for b in at.button]


def test_each_card_on_the_right_folds_by_itself_and_reads_nothing_then(asked, monkeypatch):
    calls = []
    monkeypatch.setattr(view, 'liner_issues', lambda: calls.append(1) or ('2026-11-20', [{'title': '나스닥 급락', 'stars': 5}]))
    at = wide()
    marks = {'news': f'{PREFIX}_issue_0', 'suggestions': f'{PREFIX}_suggestion_0', 'history': f'{PREFIX}_history_0'}
    liner_history.record('질문', 'ETF가 뭐예요?')
    has = lambda name: marks[name] in [b.key for b in at.button]
    assert has('news') and has('suggestions') and not has('history')  # 다시 요청하기 is paid: closed until it is opened

    def turn(name, opened):
        at.session_state[f'{PREFIX}_card_{name}'] = opened  # what a click on the card's title does
        at.run()
        assert not at.exception
    turn('news', False)
    read = len(calls)
    at.run()
    assert not has('news') and has('suggestions') and len(calls) == read
    turn('suggestions', False)
    turn('history', True)
    assert not has('suggestions') and has('history')
    folded(at, 'side')
    folded(at, 'side')
    at.button(key=f'{PREFIX}_guide_button').click().run()
    at.button(key=f'{PREFIX}_guide_button').click().run()
    assert not has('news') and not has('suggestions') and has('history')  # as they were left
    turn('news', True)
    assert has('news')


def slow(at, monkeypatch, question):
    """Ask `question` and stop before its answer; the returned call lets the answer arrive."""
    import threading

    import liner_search
    gate = threading.Event()
    monkeypatch.setattr(liner_search, 'analyze', lambda messages: (gate.wait(5), ('늦은 답', []))[1])
    at.chat_input(key=f'{PREFIX}_follow_up').set_value(question).run()

    def arrive():
        gate.set()
        finish(at)
    return arrive


@pytest.mark.parametrize('start', [wide, small])
def test_reading_another_talk_while_an_answer_is_on_its_way(asked, monkeypatch, start):
    other = talks(1)[0]
    at = start()
    arrive = slow(at, monkeypatch, '펀드는요?')
    if start is small:
        at.segmented_control(key='ai_float_page').set_value('이력').run()
    at.button(key=f'{PREFIX}_talk_{other}').click().run()
    assert '답변 0' in shown(at)
    arrive()
    assert '답변 0' in shown(at) and '늦은 답' not in shown(at)  # the screen is not taken away
    assert len(liner_history.load(other)['turns']) == 1  # nor the answer put into the talk being read
    at.button(key=f'{PREFIX}_arrived_open').click().run()
    assert '늦은 답' in shown(at) and '답변 0' not in shown(at) and not at.success


def test_an_answer_joins_its_talk_when_it_is_the_one_being_read(asked, monkeypatch):
    first, other = talks(2)
    at = wide()
    at.button(key=f'{PREFIX}_talk_{first}').click().run()
    arrive = slow(at, monkeypatch, '이어서')
    at.button(key=f'{PREFIX}_talk_{other}').click().run()
    at.button(key=f'{PREFIX}_talk_{first}').click().run()  # back again
    arrive()
    assert '답변 0' in shown(at) and '늦은 답' in shown(at) and not at.success
    assert len(liner_history.load(first)['turns']) == 2 and len(liner_history.load(other)['turns']) == 1


def test_a_new_chat_while_an_answer_is_on_its_way_stays_new(asked, monkeypatch):
    at = wide()
    arrive = slow(at, monkeypatch, '펀드는요?')
    at.button(key=f'{PREFIX}_new_chat').click().run()
    arrive()
    assert '늦은 답' not in shown(at) and at.success and liner_history.threads()[0]['turns'] == 1


def test_an_answer_that_could_not_be_kept_is_shown_wherever_the_user_is(asked, monkeypatch):
    other = talks(1)[0]
    at = wide()
    arrive = slow(at, monkeypatch, '펀드는요?')
    at.button(key=f'{PREFIX}_talk_{other}').click().run()
    monkeypatch.setattr(liner_history, 'save', lambda *a, **k: (_ for _ in ()).throw(OSError()))
    arrive()
    assert '늦은 답' in shown(at) and at.warning  # else it would be lost


def test_a_stored_search_opens_above_the_chat(asked):
    at = wide()
    at.text_input(key=f'{PREFIX}_query').set_value('반도체')
    at.button(key=f'{PREFIX}_submit').click().run()
    finish(at)
    ask(at, 'ETF가 뭐예요?')
    place = lambda: (next(i for i, m in enumerate(shown(at)) if '반도체 기사' in m), shown(at).index('답변 1'))
    assert place()[0] > place()[1]
    at.button(key=f'{PREFIX}_talk_{of_kind('검색')[0]["id"]}').click().run()
    assert place()[0] < place()[1]
    assert next(e for e in at.expander if e.label.startswith('검색 결과')).proto.expanded



def test_clearing_keeps_the_archive(asked):
    first, second = talks(2)
    liner_history.archive(first, True)
    at = wide()
    at.button(key=f'{PREFIX}_talk_{second}').click().run()
    at.button(key=f'{PREFIX}_talk_clear').click().run()
    assert liner_history.threads() == [] and [r['id'] for r in liner_history.threads(archived=True)] == [first]
    assert '답변 1' not in shown(at) and not at.exception


def test_clearing_does_not_close_an_archived_talk_that_is_open():
    first, second = talks(2)
    liner_history.archive(first, True)
    at = wide()
    at.button(key=f'{PREFIX}_box_button').click().run()
    at.button(key=f'{PREFIX}_talk_{first}').click().run()
    at.button(key=f'{PREFIX}_box_button').click().run()
    at.button(key=f'{PREFIX}_talk_clear').click().run()
    assert '답변 0' in shown(at)


def test_the_internal_mode_also_holds_for_a_past_talk(asked):
    made = talks(1)[0]
    at = wide()
    at.segmented_control(key=f'{PREFIX}_chat_mode').set_value('내부 데이터').run()
    at.button(key=f'{PREFIX}_talk_{made}').click().run()
    ask(at, '코스피 전망은?')
    assert asked == [] and len(liner_history.load(made)['turns']) == 2


def test_a_talk_deleted_elsewhere_goes_on_as_a_new_one(asked):
    made = talks(1)[0]
    at = wide()
    at.button(key=f'{PREFIX}_talk_{made}').click().run()
    liner_history.delete(made)
    ask(at, '이어서')
    assert [r['turns'] for r in liner_history.threads()] == [1] and talk_rows(at) != [made]


def test_a_failed_follow_up_can_be_retried_into_the_same_talk(monkeypatch):
    import liner_search
    first, other = talks(2)
    replies = [ValueError('실패'), ('다시 받은 답', [])]

    def analyze(messages):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(liner_search, 'analyze', analyze)
    at = wide()
    at.button(key=f'{PREFIX}_talk_{first}').click().run()
    ask(at, '이어서')
    assert at.error
    at.button(key=f'{PREFIX}_talk_{other}').click().run()  # reading another one meanwhile
    ask_again = [b for b in at.button if b.key == f'{PREFIX}_retry']
    assert ask_again
    ask_again[0].click().run()
    finish(at)
    assert [t['question'] for t in liner_history.load(first)['turns']] == ['q0', '이어서'] and len(liner_history.load(other)['turns']) == 1


def test_past_talks_are_readable_without_a_key_and_nothing_can_be_sent():
    made = talks(1)[0]

    def app():
        import api_status_view
        api_status_view.liner_search(False, prefix='quick_liner')
    at = AppTest.from_function(app).run(timeout=30)
    assert any('LINER_API_KEY' in i.value for i in at.info)
    at.button(key=f'{PREFIX}_talk_{made}').click().run()
    assert '답변 0' in shown(at) and at.chat_input(key=f'{PREFIX}_follow_up').disabled and not at.exception
    assert not [b for b in at.button if b.key.startswith((f'{PREFIX}_submit', f'{PREFIX}_suggestion_', f'{PREFIX}_issue_'))]


def test_every_turn_says_when_it_was_asked(asked):
    made = talks(1)[0]
    at = wide()
    at.button(key=f'{PREFIX}_talk_{made}').click().run()
    ask(at, '이어서')
    assert len([c for c in at.caption if c.value.startswith('오늘 ')]) == 2


@pytest.mark.parametrize('start', [wide, small])
def test_each_row_is_archived_put_back_or_deleted_by_its_own_icons(asked, start):
    first, second = talks(2)
    at = start()
    if start is small:
        at.segmented_control(key='ai_float_page').set_value('이력').run()
    assert not at.checkbox and not [t for t in at.toggle if 'talk' in t.key]
    at.button(key=f'{PREFIX}_archive_{first}').click().run()
    assert talk_rows(at) == [second] and [r['id'] for r in liner_history.threads(archived=True)] == [first]
    at.button(key=f'{PREFIX}_box_button').click().run()
    assert talk_rows(at) == [first] and at.button(key=f'{PREFIX}_archive_{first}').label == '보관 해제'
    at.button(key=f'{PREFIX}_archive_{first}').click().run()
    assert talk_rows(at) == [] and any('보관한 대화가 없습니다' in c.value for c in at.caption)
    at.button(key=f'{PREFIX}_box_button').click().run()
    drop(at, second)
    assert talk_rows(at) == [first] and liner_history.load(second) is None and not at.exception


def test_deleting_the_open_talk_empties_the_chat_and_archiving_does_not(asked):
    first, second = talks(2)
    at = wide()
    at.button(key=f'{PREFIX}_talk_{first}').click().run()
    at.button(key=f'{PREFIX}_archive_{first}').click().run()
    assert '답변 0' in shown(at)
    ask(at, '이어서')
    assert liner_history.load(first)['archived'] and len(liner_history.load(first)['turns']) == 2  # goes on in the archive
    drop(at, second)
    assert '답변 0' in shown(at)
    at.button(key=f'{PREFIX}_box_button').click().run()
    drop(at, first)
    assert '답변 0' not in shown(at) and f'{PREFIX}_chat' not in at.session_state and not at.exception


def test_a_talk_waiting_for_its_answer_cannot_be_deleted(asked, monkeypatch):
    first, other = talks(2)
    at = wide()
    at.button(key=f'{PREFIX}_talk_{first}').click().run()
    arrive = slow(at, monkeypatch, '이어서')
    assert at.button(key=f'{PREFIX}_drop_{first}').disabled and not at.button(key=f'{PREFIX}_drop_{other}').disabled
    arrive()
    assert len(liner_history.load(first)['turns']) == 2 and not at.button(key=f'{PREFIX}_drop_{first}').disabled


def test_deleting_can_be_called_off():
    made = talks(1)[0]
    at = wide()
    at.button(key=f'{PREFIX}_drop_{made}').click().run()
    at.button(key=f'{PREFIX}_keep_{made}').click().run()
    at.button(key=f'{PREFIX}_drop_{made}').click().run()  # asks again
    assert talk_rows(at) == [made] and liner_history.load(made)


def test_an_answer_that_arrived_is_opened_from_the_guide_too(asked, monkeypatch):
    at = wide()
    arrive = slow(at, monkeypatch, '펀드는요?')
    at.button(key=f'{PREFIX}_new_chat').click().run()
    at.button(key=f'{PREFIX}_guide_button').click().run()
    arrive()
    at.button(key=f'{PREFIX}_arrived_open').click().run()
    assert '늦은 답' in shown(at) and at.chat_input(key=f'{PREFIX}_follow_up')


def test_a_kept_answer_is_not_called_lost_when_only_the_request_list_fails(asked, monkeypatch):
    monkeypatch.setattr(liner_history, 'record', lambda *a: (_ for _ in ()).throw(OSError()))
    at = wide()
    ask(at, 'ETF가 뭐예요?')
    assert len(liner_history.threads()) == 1 and not at.warning


def test_the_arrived_notice_of_a_deleted_talk_goes_away(asked, monkeypatch):
    at = wide()
    arrive = slow(at, monkeypatch, '펀드는요?')
    at.button(key=f'{PREFIX}_new_chat').click().run()
    arrive()
    liner_history.delete(liner_history.threads()[0]['id'])  # in another window
    at.button(key=f'{PREFIX}_arrived_open').click().run()
    assert not at.success and any('열지 못했습니다' in w.value for w in at.warning)


def test_a_talk_that_grew_in_another_window_goes_on_with_all_of_it(asked):
    first = talks(1)[0]
    a, b = wide(), wide()
    for at in (a, b):
        at.button(key=f'{PREFIX}_talk_{first}').click().run()
    ask(a, '둘째')
    ask(b, '셋째')
    assert [m['content'] for m in asked[1][:-1]] == ['q0', '답변 0', '둘째', '답변 1']
    assert '답변 1' in shown(b) and '답변 2' in shown(b) and len(liner_history.load(first)['turns']) == 3


def test_the_open_search_stands_out_in_the_list(asked):
    made = talks(1)[0]
    at = wide()
    at.button(key=f'{PREFIX}_talk_{made}').click().run()
    at.text_input(key=f'{PREFIX}_query').set_value('반도체')
    at.button(key=f'{PREFIX}_submit').click().run()
    finish(at)
    marked = lambda: [b.key for b in at.button if b.proto.type == 'secondary' and b.key.startswith(f'{PREFIX}_talk_') and b.key[-1].isdigit()]
    found = of_kind('검색')[0]['id']
    assert marked() == [f'{PREFIX}_talk_{found}']
    at.button(key=f'{PREFIX}_talk_{made}').click().run()
    assert marked() == [f'{PREFIX}_talk_{made}']
    at.button(key=f'{PREFIX}_talk_{found}').click().run()
    assert marked() == [f'{PREFIX}_talk_{found}']


def test_the_internal_mode_never_turns_into_the_web_by_itself(asked):
    at = wide()
    at.segmented_control(key=f'{PREFIX}_chat_mode').set_value('내부 데이터').run()
    at.session_state[f'{PREFIX}_chat_mode'] = None  # two runs that overtook each other leave the widget empty
    at.run()
    ask(at, '코스피 전망은?')
    assert asked == [] and at.segmented_control(key=f'{PREFIX}_chat_mode').value == '내부 데이터'


def test_a_question_typed_while_waiting_is_not_lost(asked, monkeypatch):
    at = wide()
    arrive = slow(at, monkeypatch, '펀드는요?')
    at.chat_input(key=f'{PREFIX}_follow_up').set_value('다음 질문').run()
    assert at.toast and at.chat_input(key=f'{PREFIX}_follow_up').proto.value == '다음 질문'  # back in the box
    arrive()
    assert [t['question'] for t in liner_history.load(liner_history.threads()[0]['id'])['turns']] == ['펀드는요?']


def test_a_talk_or_a_new_chat_does_not_keep_the_last_search_beside_it(asked):
    made = talks(1)[0]
    at = wide()
    for leave in (f'{PREFIX}_talk_{made}', f'{PREFIX}_new_chat'):
        at.text_input(key=f'{PREFIX}_query').set_value('반도체')
        at.button(key=f'{PREFIX}_submit').click().run()
        finish(at)
        assert f'{PREFIX}_analyze' in [b.key for b in at.button]
        at.button(key=leave).click().run()
        assert f'{PREFIX}_analyze' not in [b.key for b in at.button] and not any('반도체 기사' in m for m in shown(at))


def test_deleting_the_open_search_takes_its_results_away(asked):
    at = wide()
    at.text_input(key=f'{PREFIX}_query').set_value('반도체')
    at.button(key=f'{PREFIX}_submit').click().run()
    finish(at)
    drop(at, of_kind('검색')[0]['id'])
    assert not any('반도체 기사' in m for m in shown(at)) and f'{PREFIX}_analyze' not in [b.key for b in at.button]


def test_a_new_chat_leaves_the_archive_box(asked):
    at = wide()
    at.button(key=f'{PREFIX}_box_button').click().run()
    at.button(key=f'{PREFIX}_new_chat').click().run()
    ask(at, 'ETF가 뭐예요?')
    assert len(talk_rows(at)) == 1


def test_paid_presses_say_so_on_the_screen(asked):
    at = wide()
    said = [c.value for c in at.caption]
    assert any('제목' in c and '요금' in c for c in said) and any('누르면 바로' in c and '요금' in c for c in said)
