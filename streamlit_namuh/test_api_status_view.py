import pytest
from streamlit.testing.v1 import AppTest


import api_status_view


@pytest.fixture(autouse=True)
def isolate_liner_history(tmp_path, monkeypatch):
    monkeypatch.setattr('liner_history.path', lambda: tmp_path / 'ai-search-history.sqlite3')


def _app():
    import api_status_view
    from pathlib import Path
    api_status_view.render(Path('/nonexistent-runtime'))


def finish_liner(at, prefix='quick_liner'):
    key = f'{prefix}_pending'
    if key in at.session_state:
        try:
            at.session_state[key]['future'].result(timeout=5)
        except ValueError:
            pass  # the app renders provider errors on the next pass
        at.run()


def test_liner_search_can_render_twice_without_widget_conflicts(monkeypatch):
    import liner_search
    import api_status_view

    monkeypatch.setattr(liner_search, 'search', lambda query: [{'제목': query, '원문 링크': 'https://example.org'}])
    monkeypatch.setattr(api_status_view, 'liner_issues', lambda: ('2026-11-20', [
        {'title': '금리 발표', 'stars': 5}, {'title': '반도체 실적', 'stars': 1},
    ]))

    def app():
        import api_status_view
        api_status_view.liner_search(True)
        api_status_view.liner_search(True, prefix='quick_liner')

    at = AppTest.from_function(app).run()
    assert not at.exception
    assert ':red-badge[★★★★★ 5/5]' in at.button(key='quick_liner_issue_0').label
    assert ':gray-badge[★ 1/5]' in at.button(key='quick_liner_issue_1').label
    at.text_input(key='quick_liner_query').set_value('반도체')
    at.button(key='quick_liner_submit').click().run()
    finish_liner(at)
    assert not at.exception
    assert any(item.value == '**반도체**' for item in at.markdown)
    assert at.text_input(key='api_liner_query').value == ''
    at.button(key='quick_liner_issue_0').click().run()
    finish_liner(at)
    assert any(item.value == '**금리 발표**' for item in at.markdown)


def test_liner_market_analysis_keeps_news_context_and_sources(tmp_path, monkeypatch):
    from unittest.mock import Mock

    import api_status_view as view
    import liner_search

    env = tmp_path / '.env'
    env.write_text('LINER_API_KEY=test-key\n')
    monkeypatch.setattr(liner_search.local_env, 'ENV', env)
    monkeypatch.setattr(view, 'liner_issues', lambda: ('2026-11-20', [
        {'title': '금리 발표', 'link': 'https://example.org/news'},
    ]))
    monkeypatch.setattr(liner_search, 'search', lambda query: [])
    response = Mock(status_code=200)
    answer = '### 확인된 사실\n\n- **금리 경로**를 살펴보세요. ((1))'
    response.json.return_value = {'answer': answer, 'references': [
        {'title': '중앙은행 발표', 'url': 'https://example.org/release', 'date': '2026-11-20'},
        {'url': 'javascript:alert(1)'},
    ]}
    post = Mock(return_value=response)
    monkeypatch.setattr(liner_search.requests, 'post', post)

    def app():
        import api_status_view
        api_status_view.liner_search(True, prefix='quick_liner')

    at = AppTest.from_function(app).run()
    at.button(key='quick_liner_issue_0').click().run()
    finish_liner(at)
    post.assert_not_called()
    at.button(key='quick_liner_analyze').click().run()
    finish_liner(at)
    assert not at.exception
    assert post.call_args.args == (liner_search.ANALYSIS_ENDPOINT,)
    assert post.call_args.kwargs['headers'] == {'x-api-key': 'test-key'}
    body = post.call_args.kwargs['json']
    assert body['lang'] == 'ko' and body['stream'] is False
    assert '금리 발표' in body['messages'][-1]['content'] and 'https://example.org/news' in body['messages'][-1]['content']
    assert any(item.value == '### 확인된 사실\n\n- **금리 경로**를 살펴보세요. \\[1\\]' for item in at.markdown)
    assert [link.url for link in at.link_button] == ['https://example.org/release']

    at.chat_input(key='quick_liner_follow_up').set_value('은행주는 어떤가요?').run()
    finish_liner(at)
    assert not at.exception
    messages = post.call_args.kwargs['json']['messages']
    assert [message['role'] for message in messages] == ['user', 'assistant', 'user']
    assert messages[1]['content'] == answer
    assert '은행주는 어떤가요?' in messages[-1]['content'] and '금리 발표' in messages[-1]['content']
    at.run()
    assert post.call_count == 2


def test_liner_chat_accepts_a_question_without_a_search_or_news(monkeypatch):
    from unittest.mock import Mock

    import api_status_view as view
    import liner_search

    monkeypatch.setattr(view, 'liner_issues', lambda: (None, []))
    analyze = Mock(return_value=('ETF는 거래소에서 매매할 수 있는 펀드입니다.', []))
    monkeypatch.setattr(liner_search, 'analyze', analyze)
    search = Mock()
    monkeypatch.setattr(liner_search, 'search', search)

    def app():
        import api_status_view
        api_status_view.liner_search(True, prefix='quick_liner')

    at = AppTest.from_function(app).run()
    assert not at.exception
    analyze.assert_not_called()
    at.chat_input(key='quick_liner_follow_up').set_value('ETF가 뭐예요?').run()
    finish_liner(at)
    assert not at.exception
    first = analyze.call_args.args[0]
    assert len(first) == 1 and 'ETF가 뭐예요?' in first[0]['content']
    assert '선택한 분석 주제:' not in first[0]['content']
    assert any('거래소에서 매매할 수 있는 펀드' in item.value for item in at.markdown)
    at.chat_input(key='quick_liner_follow_up').set_value('일반 펀드와 어떻게 달라요?').run()
    finish_liner(at)
    assert not at.exception
    messages = analyze.call_args.args[0]
    assert [message['role'] for message in messages] == ['user', 'assistant', 'user']
    assert '일반 펀드와 어떻게 달라요?' in messages[-1]['content']
    search.assert_not_called()


def test_liner_chat_keeps_internal_answers_out_of_liner_and_search_history(monkeypatch):
    from unittest.mock import Mock

    import api_status_view as view
    import liner_history
    import liner_local_data as local
    import liner_search

    question = '이번 주 Jev 수익률이 얼마야?'
    result = 'Jev 내부 조회 결과 12.34%'
    answer = Mock(return_value=result)
    analyze = Mock(return_value=('공개 투자 설명', []))
    monkeypatch.setattr(local, 'answer', answer)
    monkeypatch.setattr(liner_search, 'analyze', analyze)
    monkeypatch.setattr(view, 'liner_issues', lambda: (None, []))

    def app():
        import api_status_view
        api_status_view.liner_search(True, prefix='quick_liner')

    at = AppTest.from_function(app).run()
    at.chat_input(key='quick_liner_follow_up').set_value(question).run()
    finish_liner(at)
    assert not at.exception
    answer.assert_called_once()
    assert any(item.value == result for item in at.markdown)
    analyze.assert_not_called()
    assert liner_history.recent() == []

    at.segmented_control(key='quick_liner_chat_mode').set_value('내부 데이터').run()
    at.chat_input(key='quick_liner_follow_up').set_value('설명해 줘').run()
    finish_liner(at)
    assert any('조회할 항목을 지정해 주세요' in item.value for item in at.markdown)
    analyze.assert_not_called()

    at.segmented_control(key='quick_liner_chat_mode').set_value('자동').run()
    at.chat_input(key='quick_liner_follow_up').set_value('ETF란 무엇인가요?').run()
    finish_liner(at)
    analyze.assert_called_once()
    messages = analyze.call_args.args[0]
    assert len(messages) == 1 and 'ETF란 무엇인가요?' in messages[0]['content']
    assert question not in messages[0]['content'] and result not in messages[0]['content']
    assert liner_history.recent() == [('질문', 'ETF란 무엇인가요?')]


def test_liner_chat_reads_live_balance_and_keeps_it_out_of_liner(tmp_path, monkeypatch):
    import json
    from datetime import datetime
    from types import SimpleNamespace
    from unittest.mock import Mock

    from accounts import DEFAULT_BUDGET, digest
    import api_status_view as view
    import liner_history
    import liner_search

    number = '12345678901'
    name = '비공개테스트종목'
    query = '실계좌 1의 현재 보유 종목과 잔고는?'
    stamp = datetime(2026, 11, 21, 10, 54, 32, tzinfo=view.KST)
    runtime = tmp_path / 'nhplug'
    runtime.mkdir()
    (runtime / 'accounts.json').write_text(json.dumps({'accounts': {'live-1': {
        'kind': 'live', 'engine': 'jev', 'auto': False, 'budget': DEFAULT_BUDGET['live'], 'hash': digest(number),
    }}}))
    monkeypatch.setenv('VIBE_TRADING_HOME', str(tmp_path))
    monkeypatch.setattr(view, 'liner_issues', lambda: (None, []))
    monkeypatch.setattr('nh_live_broker.datetime', SimpleNamespace(now=lambda _tz: stamp))
    config = SimpleNamespace(args=['--mode', 'readonly'])
    monkeypatch.setattr('src.config.loader.load_agent_config',
                        lambda: SimpleNamespace(mcp_servers={'nhplug_readonly': config}))
    calls = []
    complete = True

    def call_tool(tool, args):
        calls.append((tool, args))
        if tool == 'list_accounts':
            return {'status': 'ok', 'data': {'Output_0': [{'acct_type': '01', 'acct_no': number}]}}
        assert tool == 'call_api' and args['operationId'] == 'krstockInquiryBalance'
        assert args['input']['act_no'] == number
        return {'status': 'ok', 'data': {
            'rsp_cd': '00166', 'rsp_msg': '조회가 완료되었습니다.', '_nh_pagination': {'complete': complete},
            'Output_0': {'act_no': number, 'dca': 0, 'orr_pbl_amt1': 1234567, 'tot_aet_amt': 9876543},
            'Output_1': [{'iem_cd': '005930', 'iem_nm': name, 'rsdl_qty': 17, 'act_no': number}],
        }}

    def adapter(server_name, server_config, **_kwargs):
        assert server_name == 'nhplug_readonly' and server_config is config
        return SimpleNamespace(server_name=server_name, server_config=server_config, call_tool=call_tool)

    monkeypatch.setattr('src.tools.mcp.MCPServerAdapter', adapter)
    analyze = Mock(return_value=('공개 투자 설명', []))
    search = Mock()
    monkeypatch.setattr(liner_search, 'analyze', analyze)
    monkeypatch.setattr(liner_search, 'search', search)

    def app():
        import api_status_view
        api_status_view.liner_search(True, prefix='quick_liner')

    at = AppTest.from_function(app).run()
    at.chat_input(key='quick_liner_follow_up').set_value(query).run()
    finish_liner(at)

    assert not at.exception
    reply = next(m.value for m in at.markdown if '### 실계좌 1 잔고와 보유 종목' in m.value)
    assert '조회 완료 2026-11-21 10:54:32 한국시간' in reply
    assert f'{name} (005930) · 17주' in reply and '예수금 0원' in reply and '총자산 9,876,543원' in reply
    assert number not in reply
    assert [tool for tool, _ in calls] == ['list_accounts', 'call_api']
    assert liner_history.recent() == []
    analyze.assert_not_called()
    search.assert_not_called()

    complete = None
    at.chat_input(key='quick_liner_follow_up').set_value('다시 조회해줘').run()
    finish_liner(at)
    assert any('NH에서 실계좌 잔고와 보유 종목을 확인하지 못했습니다' in m.value for m in at.markdown)
    assert not any('조회된 보유 종목이 없습니다' in m.value for m in at.markdown)
    analyze.assert_not_called()

    at.chat_input(key='quick_liner_follow_up').set_value('ETF란 무엇인가요?').run()
    finish_liner(at)
    analyze.assert_called_once()
    payload = str(analyze.call_args.args[0])
    assert all(value not in payload for value in (number, name, '9,876,543', query, '다시 조회해줘'))
    assert liner_history.recent() == [('질문', 'ETF란 무엇인가요?')]
    search.assert_not_called()


def test_liner_chat_grounds_service_questions_in_local_guides(monkeypatch):
    from unittest.mock import Mock

    import api_status_view as view
    import liner_search
    from liner_topics import find_guides

    assert any(guide['문서'] == 'streamlit_namuh/guides.py' for guide in find_guides('이 서비스에서 Jev 신뢰도가 뭐예요?'))
    assert any(guide['항목'] == '가이드 팝업과 도식' for guide in find_guides('대시보드를 처음 쓰는데 사용법을 알려줘'))
    assert any(guide['항목'] == '성과 비교' for guide in find_guides('누적 거래를 CSV로 받을 수 있어?'))
    assert any(guide['문서'] == 'docs/nxt.md' for guide in find_guides('NXT 야간 거래 사용법은?'))
    assert not find_guides('주식과 채권의 차이는 무엇인가요?')

    monkeypatch.setattr(view, 'liner_issues', lambda: (None, []))
    analyze = Mock(return_value=('**성과 비교** 탭에서 기간을 고르세요.', []))
    monkeypatch.setattr(liner_search, 'analyze', analyze)

    def app():
        import api_status_view
        api_status_view.liner_search(True, prefix='quick_liner')

    at = AppTest.from_function(app).run()
    composer = at.chat_input(key='quick_liner_follow_up')
    composer.set_value('이 서비스에서 JEV 투자 성능을 어디서 확인해요?').run()
    finish_liner(at)
    assert not at.exception
    prompt = analyze.call_args.args[0][-1]['content']
    assert '서비스 가이드 발췌:' in prompt and 'docs/dashboard.md' in prompt
    assert '최대 낙폭' in prompt and '성과 비교' in prompt
    assert any(item.label.startswith('서비스 가이드') for item in at.expander)
    at.chat_input(key='quick_liner_follow_up').set_value('거기서 기간을 어떻게 고르나요?').run()
    finish_liner(at)
    assert 'docs/dashboard.md' in analyze.call_args.args[0][-1]['content']


def test_liner_suggestions_and_history_replay_search_and_chat(monkeypatch):
    from unittest.mock import Mock

    import api_status_view as view
    import liner_history
    import liner_search

    monkeypatch.setattr(view, 'liner_issues', lambda: (None, []))
    analyze = Mock(return_value=('> **핵심 요약** · ==수급을 확인하세요==', []))
    search = Mock(return_value=[{'제목': '반도체', '요약': '반도체가 3.5% 상승했습니다.', '원문 링크': 'https://example.org'}])
    monkeypatch.setattr(liner_search, 'analyze', analyze)
    monkeypatch.setattr(liner_search, 'search', search)

    def app():
        import api_status_view
        api_status_view.liner_search(True, prefix='quick_liner')

    at = AppTest.from_function(app).run()
    assert not at.exception
    assert not liner_history.path().exists()
    old = at.button(key='quick_liner_suggestion_0').label
    at.button(key='quick_liner_shuffle').click().run()
    assert at.button(key='quick_liner_suggestion_0').label != old
    analyze.assert_not_called()
    at.button(key='quick_liner_suggestion_0').click().run()
    finish_liner(at)
    assert not at.exception
    assert any(':yellow-background[수급을 확인하세요]' in item.value for item in at.markdown)
    kind, question = liner_history.recent()[0]
    assert kind == '질문' and question in analyze.call_args.args[0][-1]['content']

    at.text_input(key='quick_liner_query').set_value('반도체')
    at.button(key='quick_liner_submit').click().run()
    finish_liner(at)
    assert [kind for kind, _ in liner_history.recent()] == ['검색', '질문']
    assert any(':yellow-background[반도체]가 :yellow-background[3.5%]' in item.value for item in at.markdown)

    reopened = AppTest.from_function(app).run()
    reopened.session_state['quick_liner_card_history'] = True
    reopened.run()
    assert reopened.button(key='quick_liner_history_0').label == '검색 · 반도체'
    assert reopened.button(key='quick_liner_history_0').proto.icon == ':material/search:'
    assert reopened.button(key='quick_liner_history_1').proto.icon == ':material/chat_bubble:'
    reopened.button(key='quick_liner_history_0').click().run()
    finish_liner(reopened)
    search.assert_called_with('반도체')
    assert len(liner_history.recent()) == 2
    reopened.button(key='quick_liner_history_1').click().run()
    finish_liner(reopened)
    assert question in analyze.call_args.args[0][-1]['content']


def test_liner_is_optional_and_only_searches_on_submit(tmp_path, monkeypatch):
    from unittest.mock import Mock

    import api_status_view as view
    import liner_search

    env = tmp_path / '.env'
    env.write_text('LINER_API_KEY=\n')
    monkeypatch.setattr(liner_search.local_env, 'ENV', env)
    monkeypatch.setattr(view, 'load', Mock(return_value=([], [], {})))
    response = Mock(status_code=200)
    response.json.return_value = {'results': [
        {'title': '실적 보도', 'url': 'https://example.org/news', 'date': '2026-11-21', 'description': '분기 실적 발표'},
        {'title': '잘못된 링크', 'url': 'javascript:alert(1)'},
    ]}
    post = Mock(return_value=response)
    monkeypatch.setattr(liner_search.requests, 'post', post)

    try:
        view.credentials.clear()
        at = AppTest.from_function(_app, default_timeout=60).run()
        at.selectbox(key='api_pick').set_value('Liner').run()
        assert not at.exception
        status = at.dataframe[0].value
        assert status.loc[status['API'] == 'Liner', '상태'].item() == '미사용'
        assert [item.key for item in at.text_input] == ['api_liner_talk_find'] and all(item.disabled for item in at.chat_input)
        assert any('LINER_API_KEY' in item.value for item in at.info)
        post.assert_not_called()

        env.write_text('LINER_API_KEY=test-key\n')
        view.credentials.clear()
        at.run()
        post.assert_not_called()
        at.text_input(key='api_liner_query').set_value(' 삼성전자 실적 ')
        at.button(key='api_liner_submit').click().run()
        finish_liner(at, 'api_liner')
        assert not at.exception
        post.assert_called_once_with(
            liner_search.ENDPOINT, headers={'x-api-key': 'test-key'},
            json={'query': '삼성전자 실적', 'country_code': 'kr', 'lang': 'ko', 'max_results': 10, 'include_knowledge': False},
            timeout=10, allow_redirects=False,
        )
        assert [link.url for link in at.link_button] == ['https://example.org/news']
        at.run()
        assert post.call_count == 1
        response.status_code = 429
        at.button(key='api_liner_submit').click().run()
        finish_liner(at, 'api_liner')
        assert any('HTTP 429' in item.value for item in at.error)
        assert [link.url for link in at.link_button] == ['https://example.org/news']
    finally:
        view.credentials.clear()


def test_liner_guide_remains_available_while_waiting_for_an_answer(monkeypatch):
    from threading import Event

    import api_status_view as view
    import liner_search

    release = Event()

    def analyze(messages):
        assert release.wait(timeout=10)
        return '**다음 일지 ▶**를 열고 **‘매매·차트’**를 선택하세요.', []

    monkeypatch.setattr(view, 'liner_issues', lambda: (None, []))
    monkeypatch.setattr(liner_search, 'analyze', analyze)

    def app():
        import api_status_view
        api_status_view.liner_search(True, prefix='quick_liner')

    at = AppTest.from_function(app).run()
    try:
        assert '종목코드' in at.text_input(key='quick_liner_query').placeholder
        assert '최대 10개' in at.button(key='quick_liner_submit').help
        assert '바로 보냅니다' in at.button(key='quick_liner_suggestion_0').help
        at.chat_input(key='quick_liner_follow_up').set_value('거래 기록을 어떻게 봐요?').run()
        assert at.button(key='quick_liner_submit').disabled
        assert not at.button(key='quick_liner_shuffle').disabled
        at.button(key='quick_liner_guide_button').click().run()
        assert not at.exception
        assert [tab.label for tab in at.tabs] == ['빠른 시작', '기능별 사용법', '이력·연동']
        assert not at.expander
        assert any('### 검색·채팅 흐름도' in item.value and 'flowchart' in item.value for item in at.markdown)
        assert any('마크다운 복사' in item.value and '웹 출처' in item.value for item in at.markdown)
    finally:
        release.set()
        finish_liner(at)
    at.button(key='quick_liner_guide_button').click().run()
    assert any('**다음 일지 ▶**\u200a를' in item.value and '**‘매매·차트’**\u200a를' in item.value for item in at.markdown)


def test_answers_show_prices_and_ranges_as_written_and_keep_formulas():
    shown = lambda text: api_status_view.answer_markdown(text, highlight=True)
    assert shown('엔비디아는 $180에서 $195로') == r'엔비디아는 \$180에서 \$195로'
    assert shown('US$ 210 to US$ 240, $1→$2') == r'US\$ 210 to US\$ 240, \$1→\$2'
    assert shown('3~5%였고 7~10%') == r'3\~5%였고 7\~10%'
    for kept in ('복리 $A = P(1+r)^n$ 계산', '$$\n\\frac{1}{2}\n$$', '`$1 ~ $2`', '```\n$1 ~a~ $2\n```', r'이미 \$5', '~~취소~~',
                 '[홈](https://a.kr/~u?x=$1)', 'https://a.kr/~u 와'):
        assert shown(kept) == kept, kept
    assert shown('**[제목](https://a.kr/~u)** 3~5%') == r'**[제목](https://a.kr/~u)** 3\~5%'
    assert api_status_view.answer_markdown('$1에서 $2, 3~5%', highlight=False) == '$1에서 $2, 3~5%'  # the copied text stays as written


def test_number_highlight_is_linear_and_marks_whole_numbers():
    import time
    start = time.perf_counter()
    api_status_view.highlight_excerpt('1,' * 4000 + '2' * 5000, '반도체')
    assert time.perf_counter() - start < 0.1
    assert api_status_view.highlight_excerpt('매출,3.5% 이익 1,200억 원 -2%p', '') == (
        '매출,:yellow-background[3.5%] 이익 :yellow-background[1,200억 원] :yellow-background[-2%p]')


def test_guide_excerpts_drop_links_to_files_and_keep_web_links(monkeypatch):
    from streamlit.testing.v1 import AppTest

    def app():
        import api_status_view
        api_status_view.show_liner_guides([{'문서': 'docs/x.md', '항목': '화면', '본문':
            '[장전](premarket.md#a)과 [웹](https://a.kr), 09:00~15:30, 1~5개 ![그림](a.png)'}])
    texts = [m.value for m in AppTest.from_function(app).run().markdown]
    assert r'장전과 [웹](https://a.kr), 09:00\~15:30, 1\~5개 그림' in texts


def liner_app(prefix='quick_liner'):
    from streamlit.testing.v1 import AppTest

    def app():
        import api_status_view
        api_status_view.liner_search(True, prefix='quick_liner')
    return AppTest.from_function(app).run(timeout=30)


def test_a_failed_question_can_be_sent_again_without_retyping(monkeypatch):
    import liner_search

    monkeypatch.setattr(api_status_view, 'liner_issues', lambda: (None, []))
    asked = []

    def analyze(messages):
        asked.append(messages[-1]['content'])
        if len(asked) == 1:
            raise ValueError('Liner 요청에 실패했습니다(HTTP 429).')
        return '답', []
    monkeypatch.setattr(liner_search, 'analyze', analyze)
    at = liner_app()
    question = '공들여 쓴 긴 질문입니다'
    at.chat_input(key='quick_liner_follow_up').set_value(question).run()
    finish_liner(at)
    assert at.error[0].value == 'Liner 요청에 실패했습니다(HTTP 429).'
    assert any(t.value == question for t in at.text)
    at.button(key='quick_liner_retry').click().run()
    finish_liner(at)
    assert not at.error and not at.exception
    assert len(asked) == 2 and all(a.endswith(f'질문: {question}') for a in asked)
    assert at.session_state['quick_liner_chat']['turns'][0]['question'] == question


def test_a_failed_search_can_be_sent_again(monkeypatch):
    import liner_search

    monkeypatch.setattr(api_status_view, 'liner_issues', lambda: (None, []))
    replies = [ValueError('Liner에 연결하지 못했습니다.'), [{'제목': '기사', '원문 링크': 'https://example.org'}]]

    def search(query):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(liner_search, 'search', search)
    at = liner_app()
    at.text_input(key='quick_liner_query').set_value('반도체')
    at.button(key='quick_liner_submit').click().run()
    finish_liner(at)
    at.button(key='quick_liner_retry').click().run()
    finish_liner(at)
    assert at.session_state['quick_liner_results'] and at.session_state['quick_liner_subject'] == {'주제': '반도체'}


def test_new_chat_drops_the_old_topic_and_turns(monkeypatch):
    import liner_search

    monkeypatch.setattr(api_status_view, 'liner_issues', lambda: (None, []))
    sent = []
    monkeypatch.setattr(liner_search, 'search', lambda q: [])
    monkeypatch.setattr(liner_search, 'analyze', lambda messages: sent.append(messages) or ('답', []))
    at = liner_app()
    at.text_input(key='quick_liner_query').set_value('반도체')
    at.button(key='quick_liner_submit').click().run()
    finish_liner(at)
    at.button(key='quick_liner_analyze').click().run()
    finish_liner(at)
    at.button(key='quick_liner_new_chat').click().run()
    assert not at.exception and 'quick_liner_chat' not in at.session_state
    at.chat_input(key='quick_liner_follow_up').set_value('ETF가 뭐예요?').run()
    finish_liner(at)
    assert len(sent[1]) == 1 and '선택한 분석 주제' not in sent[1][0]['content']


def test_new_search_results_open_even_after_a_chat(monkeypatch):
    import liner_search

    monkeypatch.setattr(api_status_view, 'liner_issues', lambda: (None, []))
    monkeypatch.setattr(liner_search, 'search', lambda q: [{'제목': '기사', '원문 링크': 'https://example.org'}])
    monkeypatch.setattr(liner_search, 'analyze', lambda messages: ('답', []))
    at = liner_app()
    results = lambda: next(e for e in at.expander if e.label.startswith('검색 결과'))
    at.chat_input(key='quick_liner_follow_up').set_value('ETF가 뭐예요?').run()
    finish_liner(at)
    at.text_input(key='quick_liner_query').set_value('반도체')
    at.button(key='quick_liner_submit').click().run()
    finish_liner(at)
    assert results().proto.expanded
    at.chat_input(key='quick_liner_follow_up').set_value('더 쉽게').run()
    finish_liner(at)
    assert not results().proto.expanded


def test_a_broken_journal_does_not_take_the_search_down(monkeypatch):
    def broken():
        raise KeyError('change_pct')
    monkeypatch.setattr(api_status_view, 'liner_issues', broken)
    at = liner_app()
    assert not at.exception and at.chat_input(key='quick_liner_follow_up')
    assert any('최근 주요 뉴스를 불러오지 못했습니다' in w.value for w in at.warning)


def test_every_guide_shows_its_own_text():
    def app():
        import api_status_view
        api_status_view.show_liner_guides([{'문서': 'a.md', '항목': 'A', '본문': '첫째 본문'}, {'문서': 'b.md', '항목': 'B', '본문': '둘째 본문'}])
    assert [m.value for m in AppTest.from_function(app).run().markdown] == ['**A** · `a.md`', '첫째 본문', '**B** · `b.md`', '둘째 본문']


def test_answer_marks_survive_colons_code_links_and_formulas():
    shown = lambda text: api_status_view.answer_markdown(text, highlight=True)
    assert shown('결론:==금리 인하==') == '결론: :yellow-background[금리 인하]'
    assert shown('==PER=12배==') == ':yellow-background[PER=12배]'
    assert shown('값==핵심==') == '값:yellow-background[핵심]'
    assert shown('==형광 [링크](https://a.com) 끝==') == ':yellow-background[형광 링크 끝]'
    assert shown('a == b 이고 c == d') == 'a == b 이고 c == d'
    assert '**`PER`**' in shown('**`PER`** 는')
    for kept in ('- 예\n  ```\n  $5 ~ ==a==\n  ```', '~~~\n$1 ~a~ $2\n~~~', '``a ` $1 ~b~ $2``', r'$\text{수익률} = \frac{a}{b}$'):
        assert shown(kept) == kept, kept
    assert shown(r'\[ R = \frac{a}{b} \]') == r'$$ R = \frac{a}{b} $$'
    assert shown('$TSLA=+5%,$NVDA=-3%') == r'\$TSLA=+5%,\$NVDA=-3%'
    assert shown('올랐다!((1)) 끝((2))(참고)') == r'올랐다!\[1\] 끝\[2\](참고)'
    assert api_status_view.answer_markdown('올랐다((1))', highlight=False) == '올랐다[1]'
    assert shown('![그림](https://a.kr/x.png)') == '[그림](https://a.kr/x.png)'


def test_internal_mode_survives_the_guide_and_stays_off_the_web(monkeypatch):
    import liner_search

    monkeypatch.setattr(api_status_view, 'liner_issues', lambda: (None, []))
    calls = []
    monkeypatch.setattr(liner_search, 'analyze', lambda messages: calls.append(1) or ('답', []))
    at = liner_app()
    at.segmented_control(key='quick_liner_chat_mode').set_value('내부 데이터').run()
    at.button(key='quick_liner_guide_button').click().run()
    at.button(key='quick_liner_guide_button').click().run()
    assert at.segmented_control(key='quick_liner_chat_mode').value == '내부 데이터'
    at.chat_input(key='quick_liner_follow_up').set_value('코스피 전망은?').run()
    finish_liner(at)
    assert calls == [] and not at.exception


def test_retry_from_the_guide_is_sent_at_once_and_only_once(monkeypatch):
    import liner_search

    monkeypatch.setattr(api_status_view, 'liner_issues', lambda: (None, []))
    calls = []

    def analyze(messages):
        calls.append(messages)
        if len(calls) == 1:
            raise ValueError('실패')
        return '답', []
    monkeypatch.setattr(liner_search, 'analyze', analyze)
    at = liner_app()
    at.chat_input(key='quick_liner_follow_up').set_value('ETF가 뭐예요?').run()
    finish_liner(at)
    at.button(key='quick_liner_guide_button').click().run()
    at.button(key='quick_liner_retry').click().run()
    finish_liner(at)
    at.run()
    assert len(calls) == 2 and at.chat_input(key='quick_liner_follow_up')


def test_retrying_a_failed_analysis_keeps_its_topic(monkeypatch):
    import liner_history
    import liner_search

    monkeypatch.setattr(api_status_view, 'liner_issues', lambda: ('2026-11-20', [{'title': '나스닥 급락', 'stars': 5, 'link': 'https://a.kr'}]))
    monkeypatch.setattr(liner_search, 'search', lambda q: [])
    sent = []

    def analyze(messages):
        sent.append(messages)
        if len(sent) == 2:
            raise ValueError('실패')
        return '답', []
    monkeypatch.setattr(liner_search, 'analyze', analyze)
    at = liner_app()
    at.chat_input(key='quick_liner_follow_up').set_value('ETF가 뭐예요?').run()
    finish_liner(at)
    at.button(key='quick_liner_issue_0').click().run()
    finish_liner(at)
    at.button(key='quick_liner_analyze').click().run()
    finish_liner(at)
    at.button(key='quick_liner_retry').click().run()
    finish_liner(at)
    assert sent[2] == sent[1] and len(sent[2]) == 1 and '나스닥 급락' in sent[2][0]['content'] and 'https://a.kr' in sent[2][0]['content']
    assert liner_history.recent()[0] == ('질문', '나스닥 급락가 오늘 국내 주식시장에 어떤 영향을 미칠 수 있나요?')


def test_retrying_a_failed_news_search_keeps_the_article(monkeypatch):
    import liner_search

    monkeypatch.setattr(api_status_view, 'liner_issues', lambda: ('2026-11-20', [{'title': '나스닥 급락', 'stars': 5, 'link': 'https://a.kr'}]))
    replies = [ValueError('실패'), []]

    def search(q):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(liner_search, 'search', search)
    at = liner_app()
    at.button(key='quick_liner_issue_0').click().run()
    finish_liner(at)
    at.button(key='quick_liner_retry').click().run()
    finish_liner(at)
    assert at.session_state['quick_liner_subject'] == {'주제': '나스닥 급락', '매매일지 기준일': '2026-11-20', '기사 링크': 'https://a.kr'}


def test_internal_turns_do_not_push_the_web_talk_out_of_the_context(monkeypatch):
    import liner_local_data
    import liner_search

    monkeypatch.setattr(api_status_view, 'liner_issues', lambda: (None, []))
    monkeypatch.setattr(liner_local_data, 'answer', lambda *a: '기록')
    sent = []
    monkeypatch.setattr(liner_search, 'analyze', lambda messages: sent.append(messages) or ('답', []))
    at = liner_app()
    for question in ('ETF가 뭐예요?', *['오늘 모의 체결 내역은?'] * 6, '더 쉽게 예를 들어 주세요'):
        at.chat_input(key='quick_liner_follow_up').set_value(question).run()
        finish_liner(at)
    assert [m['content'] for m in sent[1][:2]] == ['ETF가 뭐예요?', '답']


def test_new_chat_clears_the_failed_notice(monkeypatch):
    import liner_search

    monkeypatch.setattr(api_status_view, 'liner_issues', lambda: (None, []))
    replies = [('답', []), ValueError('실패')]

    def analyze(messages):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(liner_search, 'analyze', analyze)
    at = liner_app()
    for question in ('ETF가 뭐예요?', '펀드는요?'):
        at.chat_input(key='quick_liner_follow_up').set_value(question).run()
        finish_liner(at)
    at.button(key='quick_liner_new_chat').click().run()
    assert not at.error


def test_guides_ignore_short_words_inside_other_words():
    import liner_topics

    for question in ('버핏의 투자 원칙은?', '이월드 주가 전망', '이 월급으로 투자해도 될까?', '회사 운영 비용이란?', '나이에 상관없이 할 수 있나요?'):
        assert liner_topics.find_guides(question) == [], question
    assert liner_topics.find_guides('이 서비스에서 ‘국면’은 무슨 뜻인가요?')
    assert liner_topics.find_guides('이월 보유 조건은?')
    for question in liner_topics.USAGE + liner_topics.JEV:
        assert liner_topics.find_guides(question), question


def test_guides_cover_every_section_they_name_and_general_ai_search_questions():
    import re
    import liner_topics

    for relative, allowed in liner_topics.GUIDE_SECTIONS.items():
        sections = liner_topics.read_sections(relative, 0)
        assert [t for t in allowed if not sections.get(t)] == [], relative
    written = re.findall(r'(?m)^### (.+)$', (liner_topics.REPO / 'docs/ai-search.md').read_text(encoding='utf-8'))
    assert set(written) - set(liner_topics.GUIDE_SECTIONS['docs/ai-search.md']) == {'검색·채팅 흐름도'}  # a drawing, no text
    assert ('docs/ai-search.md', '투자 공부 순서') in [(g['문서'], g['항목']) for g in liner_topics.find_guides('투자 공부 순서 알려줘')]
    assert 'docs/ai-search.md' in [g['문서'] for g in liner_topics.find_guides('AI 검색은 어떻게 써?')]


def test_api_tab_renders_status_usage_and_failures(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    import api_calls
    monkeypatch.setattr(api_calls, 'DB', tmp_path / 'api-calls.sqlite3')
    monkeypatch.setattr(api_calls, '_conn', {})
    monkeypatch.setattr(api_calls, 'ENABLED', True)
    now = datetime.now(api_calls.KST)
    for _ in range(2):
        api_calls.mcp_call('nhplug_paper', 'get_stock_price', {}, 0, None, RuntimeError('Failed to initialize server session'))
    api_calls.record('kiwoom', 'api.kiwoom.com/api/dostk/chart#ka10080', True, 120, status=200, size=2048, now=now, who='market_movers')
    api_calls.record('google_news', 'news.google.com/rss/search', False, 900, status=429, error='HTTP 429', now=now, who='nh_paper_isolated')
    api_calls.record('nh', 'mcp:get_stock_price', True, 80, now=now - timedelta(days=2), who='dashboard')
    monkeypatch.setattr(api_calls, 'ENABLED', False)
    at = AppTest.from_function(_app, default_timeout=60).run()
    assert not at.exception
    heads = [m.value for m in at.markdown if m.value.startswith('#####')]
    assert heads[:4] == ['##### API별 연동 상태', '##### 오늘 부하·분산 감사', '##### 뉴스 수집 상태', '##### 오늘 호출 흐름'] and '##### 화면별 데이터 출처' in heads
    assert any('로컬 연결 시작 실패 2건' in w.value for w in at.warning)
    load = at.dataframe[1].value
    assert int(load.loc[load['API'] == 'Google 뉴스 RSS', '거절'].iloc[0]) == 1
    status = at.dataframe[0].value
    assert set(status.loc[status['API'] == 'Google 뉴스 RSS', '상태']) == {'실패'}
    assert int(status.loc[status['API'] == '키움 REST', '호출'].iloc[0]) == 1
    assert [t.label for t in at.tabs] == ['데이터·용도', '엔드포인트별 호출', '호출한 프로그램', '실패 이력', '산출물']
    assert any('HTTP 오류' in str(df.value.to_dict()) for df in at.dataframe)


def test_ago_handles_missing_stamps():
    from datetime import datetime
    import api_status_view as v
    now = datetime.now(v.KST)
    assert v.ago(float('nan'), now) == '—' and v.ago(None, now) == '—'
    assert v.ago(now.isoformat(timespec='seconds'), now).endswith('(0분 전)')


def test_recent_window_is_the_last_30_minutes_across_midnight():
    from datetime import datetime
    import api_status_view as v
    row = lambda day, slot, fail: {'day': day, 'slot': slot, 'provider': 'kiwoom', 'n': 2, 'fail': fail, 'bad': 0, 'ms_sum': 2.0,  # noqa: E731
                                   'ms_max': 1.0, 'bytes': 0, 'last_ok': None, 'last_fail': None}
    rows = [row('2026-11-17', '23:50', 2), row('2026-11-18', '00:00', 0), row('2026-11-17', '23:20', 2)]
    got = v.recent_totals(rows, datetime(2026, 11, 18, 0, 5, tzinfo=v.KST))['kiwoom']
    assert (got['n'], got['fail']) == (4, 2)  # 23:50 and 00:00 slots; 23:20 is older than 30 minutes
    late = v.recent_totals([row('2026-11-17', '10:00', 2), row('2026-11-17', '10:10', 0)], datetime(2026, 11, 17, 10, 39, tzinfo=v.KST))
    assert late['kiwoom']['fail'] == 0  # the 10:00 slot started 39 minutes ago


def _row(provider, when, n=1, fail=0):
    return {'day': when.strftime('%Y-%m-%d'), 'slot': f'{when:%H}:{when.minute // 10 * 10:02d}', 'provider': provider, 'endpoint': 'e',
            'caller': 'c', 'n': n, 'fail': fail, 'bad': 0, 'ms_sum': 1.0, 'ms_max': 1.0, 'bytes': 0,
            'last_ok': when.isoformat(timespec='seconds') if n > fail else None, 'last_fail': when.isoformat(timespec='seconds') if fail else None}


def test_an_api_that_stopped_answering_in_session_is_delayed():
    from datetime import datetime, timedelta
    import api_status_view as v
    keys = {k: True for p in v.cat.CATALOG for k in p['keys']}
    now = datetime(2026, 11, 18, 10, 30, tzinfo=v.KST)  # a Wednesday in session
    calls = [_row('nh', now - timedelta(minutes=20)), _row('naver', now - timedelta(minutes=4))]
    states = v.provider_states(calls, keys, now)
    assert states['nh'][0] == '지연' and '10:10' in states['nh'][1]  # 1-minute API silent for 20 minutes
    assert states['naver'][0] == '정상'
    assert states['jev'][0] == '지연' and '오늘 성공 없음' in states['jev'][1]  # services are up, Jev never answered today
    assert states['openai'][0] == '호출 없음'  # no expected cadence: only used in the OpenAI mode
    after = v.provider_states(calls, keys, datetime(2026, 11, 18, 16, 0, tzinfo=v.KST))
    assert after['nh'][0] == '정상'  # after the session silence is normal
    assert v.provider_states([], keys, now)['nh'][0] == '호출 없음'  # nothing called today (holiday, services down): no alarm


def test_alert_lines_name_failing_and_delayed_apis_only():
    import api_status_view as v
    states = {'nh': ('지연', '마지막 성공 10:10'), 'google_news': ('실패', '최근 30분 4건 중 4건 실패'), 'naver': ('정상', '오늘 3건'),
              'krx': ('키 없음', 'KRX_API_KEY 미설정')}
    assert v.alert_lines(states) == ['API NH 나무 PLUG 지연: 마지막 성공 10:10', 'API Google 뉴스 RSS 실패: 최근 30분 4건 중 4건 실패']


def test_endpoint_totals_handle_slots_without_a_success():
    from datetime import datetime, timedelta
    import pandas as pd
    import api_status_view as v
    now = datetime(2026, 11, 18, 10, 30, tzinfo=v.KST)
    frame = pd.DataFrame([_row('kiwoom', now - timedelta(minutes=20)), _row('kiwoom', now, n=1, fail=1)])
    got = v.totals(frame, by='endpoint')['e']
    assert (got['n'], got['fail'], got['last_ok'][11:16], got['last_fail'][11:16]) == (2, 1, '10:10', '10:30')


def test_token_expiry_reads_only_the_expiry_and_flags_expired(tmp_path):
    import json
    from datetime import datetime, timedelta
    import api_status_view as v
    now = datetime(2026, 11, 18, 10, 0, tzinfo=v.KST)
    (tmp_path / 'kiwoom').mkdir()
    (tmp_path / 'kiwoom' / 'token.json').write_text(json.dumps({'token': 'SECRET', 'expires_at': (now + timedelta(hours=20)).isoformat()}))
    (tmp_path / 'nhplug' / 'tokens').mkdir(parents=True)
    (tmp_path / 'nhplug' / 'tokens' / 'a.json').write_text(json.dumps({'token': 'SECRET', 'expiresAt': int((now - timedelta(minutes=1)).timestamp() * 1000)}))
    assert v.token_status(v.cat.BY_KEY['kiwoom'], tmp_path, now) == '토큰 11-19 06:00까지'
    assert v.token_status(v.cat.BY_KEY['nh'], tmp_path, now) == '토큰 만료 11-18 09:59 (다음 호출 때 재발급)'
    assert v.token_status(v.cat.BY_KEY['toss'], tmp_path, now) == '토큰 없음 (첫 호출 때 발급)'
    assert v.token_status(v.cat.BY_KEY['naver'], tmp_path, now) is None


def test_skills_section_lists_skill_results(tmp_path):
    import os
    os.environ['SKILLS_VIEW_RUNTIME'] = str(tmp_path)
    reviews = tmp_path / 'nhplug/shared-etf-paper/reviews'
    reviews.mkdir(parents=True)
    (reviews / 'journal-2026-11-17.md').write_text('x')

    def app():
        import os
        from pathlib import Path
        import skills_view
        skills_view.render(Path(os.environ['SKILLS_VIEW_RUNTIME']))
    at = AppTest.from_function(app, default_timeout=60).run()
    assert not at.exception
    skills = at.dataframe[0].value  # the schedules moved to 운영 → 자동 작업 (2026-11-19)
    assert skills.loc[skills['스킬'].str.contains('trading-journal-writer'), '최근 결과'].iloc[0].startswith('journal-2026-11-17.md')
    detail = at.dataframe[1].value  # the first skill (trading-journal-writer) is selected
    assert detail.loc[detail['결과'] == '하루 매매관점 리포트', '최근 파일'].iloc[0] == 'journal-2026-11-17.md'


def test_token_status_follows_the_token_in_use_not_an_old_file(tmp_path):
    import json
    from datetime import datetime, timedelta
    import api_status_view as v
    now = datetime(2026, 11, 18, 10, 0, tzinfo=v.KST)
    folder = tmp_path / 'nhplug' / 'tokens'
    folder.mkdir(parents=True)
    (folder / 'old.json').write_text(json.dumps({'expiresAt': int((now - timedelta(days=10)).timestamp() * 1000)}))
    (folder / 'used.json').write_text(json.dumps({'expiresAt': int((now + timedelta(hours=3)).timestamp() * 1000)}))
    assert v.token_status(v.cat.BY_KEY['nh'], tmp_path, now) == '토큰 11-18 13:00까지'


def _macro_app():
    import os
    from pathlib import Path
    import streamlit as st
    import api_status_view
    st.session_state.setdefault('api_pick', 'FRED (세인트루이스 연준)')
    api_status_view.render(Path(os.environ['API_VIEW_RUNTIME']))


def test_fred_detail_shows_the_values_the_engine_received(tmp_path, monkeypatch):
    import api_calls
    import vibe_longterm as L
    monkeypatch.setattr(api_calls, 'DB', tmp_path / 'api.sqlite3')
    monkeypatch.setattr(api_calls, '_conn', {})
    monkeypatch.setenv('API_VIEW_RUNTIME', str(tmp_path))
    L.put_state(L.connect(tmp_path / 'nhplug/vibe-longterm'), plan={'time': '2026-11-18T08:45:00+09:00', 'macro': {
        'as_of': '2026-11-18', 'errors': ['DFII10: TimeoutError'], 'events': [{'date': '2026-12-09', 'label': 'FOMC'}],
        'series': {'BAMLH0A0HYM2': {'label': '하이일드 스프레드', 'unit': '%p', 'date': '2026-11-16', 'value': 3.23, 'chg_1m': 0.0,
                                    'low_1y': 2.6, 'high_1y': 3.48, 'stale': False}}}})
    at = AppTest.from_function(_macro_app, default_timeout=60).run()
    assert not at.exception
    assert [t.label for t in at.tabs][-1] == '최근 데이터'
    tables = [df.value for df in at.dataframe]
    assert any('하이일드 스프레드' in set(t.get('지표', [])) for t in tables)
    assert any('FOMC' in set(t.get('발표', [])) for t in tables)
    assert any('빠진 자료: DFII10: TimeoutError' in c.value for c in at.caption)


def test_fred_check_now_button_shows_a_fresh_result(tmp_path, monkeypatch):
    import api_calls
    import longterm_macro
    monkeypatch.setattr(api_calls, 'DB', tmp_path / 'api.sqlite3')
    monkeypatch.setattr(api_calls, '_conn', {})
    monkeypatch.setenv('API_VIEW_RUNTIME', str(tmp_path))
    monkeypatch.setattr(longterm_macro, 'collect', lambda day: {'as_of': str(day), 'errors': [], 'events': [{'date': '2026-12-09', 'label': 'FOMC'}],
                                                                  'series': {'DGS10': {'label': '미 10년 국채금리', 'unit': '%', 'date': '2026-11-16',
                                                                                       'value': 5.15, 'chg_1m': -0.04, 'low_1y': 3.97, 'high_1y': 5.31, 'stale': False}}})
    at = AppTest.from_function(_macro_app, default_timeout=60).run()
    at.button(key='api_macro_check').click().run()
    assert not at.exception
    assert any('이 화면에서 지금 확인' in c.value for c in at.caption)
    assert any('미 10년 국채금리' in set(df.value.get('지표', [])) for df in at.dataframe)


def test_fred_check_now_gives_way_to_a_newer_engine_decision(tmp_path, monkeypatch):
    import api_calls
    import longterm_macro
    import vibe_longterm as L
    monkeypatch.setattr(api_calls, 'DB', tmp_path / 'api.sqlite3')
    monkeypatch.setattr(api_calls, '_conn', {})
    monkeypatch.setenv('API_VIEW_RUNTIME', str(tmp_path))
    monkeypatch.setattr(longterm_macro, 'collect', lambda day: {'as_of': str(day), 'errors': [], 'events': [], 'series': {}})
    root = tmp_path / 'nhplug/vibe-longterm'
    L.put_state(L.connect(root), plan={'time': '2026-01-02T08:45:00+09:00', 'macro': {'as_of': '2026-01-02', 'errors': [], 'events': [], 'series': {}}})
    at = AppTest.from_function(_macro_app, default_timeout=60).run()
    at.button(key='api_macro_check').click().run()
    assert any('이 화면에서 지금 확인' in c.value for c in at.caption)  # newer than the engine's morning data
    L.put_state(L.connect(root), plan={'time': '2099-01-02T08:45:00+09:00', 'macro': {'as_of': '2099-01-02', 'errors': [], 'events': [], 'series': {}}})
    at.run()
    captions = ' '.join(c.value for c in at.caption)
    assert '이 화면에서 지금 확인' not in captions and '장기 엔진 아침 판단 01-02 08:45' in captions


def test_a_weekday_krx_holiday_is_not_late(monkeypatch):
    # review4 D-4: the 08:40 long-term engine and discord-notify still call out on a KRX holiday; the cadence check took
    # any call today for a trading day and flagged every 1-minute API as 지연
    from datetime import date, datetime
    import nh_paper_isolated
    import api_status_view as v
    monkeypatch.setattr(nh_paper_isolated, '_HOLIDAYS', {2026: (frozenset({date(2026, 12, 31)}), 0.0)})
    keys = {k: True for p in v.cat.CATALOG for k in p['keys']}
    morning = lambda day: [{'day': day, 'slot': '08:40', 'provider': 'fred', 'endpoint': 'x', 'caller': 'vibe_longterm', 'n': 5,  # noqa: E731
                            'fail': 0, 'bad': 0, 'ms_sum': 500.0, 'ms_max': 200.0, 'bytes': 100,
                            'last_ok': f'{day}T08:40:10+09:00', 'last_fail': None}]
    holiday = v.provider_states(morning('2026-12-31'), keys, datetime(2026, 12, 31, 10, 30, tzinfo=v.KST))  # 연말 휴장일, a Thursday
    assert not [line for line in v.alert_lines(holiday) if '지연' in line]
    assert v.provider_states(morning('2026-12-30'), keys, datetime(2026, 12, 30, 10, 30, tzinfo=v.KST))['nh'][0] == '지연'  # it trades


def test_answers_with_marks_that_are_not_marks_are_shown_as_written():
    shown = lambda text: api_status_view.answer_markdown(text, highlight=True)
    assert shown('글꼴 \ue0003\ue001') == '글꼴 3'  # a stored answer must never break its talk
    assert shown('PER==10, PBR==1 기준') == 'PER==10, PBR==1 기준' and 'background' in shown('결론==핵심==입니다')
    assert shown('2 ** 10 = 1024') == r'2 \*\* 10 = 1024'
    assert shown('시작\n```python\na ** b  # $5 ~ $6') == '시작\n```python\na ** b  # $5 ~ $6'  # a fence the answer did not close
    copied = api_status_view.answer_markdown('!((1))(http://x/b.png)', highlight=False)
    assert '![' not in copied.replace('\\![', '')


def test_the_screen_shows_the_news_cache_and_a_resting_source(tmp_path, monkeypatch):
    """User 2026-11-25: which news source rests, and how new the cached stories are (local files, no request)."""
    import json
    from datetime import datetime, timedelta
    monkeypatch.setenv('VIBE_TRADING_NEWS_CACHE', str(tmp_path / 'news-cache.json'))
    now = datetime.now(api_status_view.KST)
    story = {'title': '코스피 상승', 'topic': 'market', 'source': 'naver_finance_news', 'publisher': 'P', 'published': (now - timedelta(minutes=7)).isoformat()}
    (tmp_path / 'news-cache.json').write_text(json.dumps({'fetched': now.isoformat(), 'rows': [story], 'topped': now.isoformat()}))
    (tmp_path / 'news-sources.json').write_text(json.dumps({'google_news_rss': (now + timedelta(minutes=5)).isoformat()}))

    def page():
        from datetime import datetime
        import api_status_view
        api_status_view.load_news(datetime.now(api_status_view.KST))
    at = AppTest.from_function(page).run()
    assert not at.exception
    topics, sources = at.dataframe[0].value, at.dataframe[1].value
    assert list(topics['가장 새 기사']) == ['7분 전', '—', '—'] and topics['제목'][0] == '코스피 상승'
    assert sources['상태'][0].startswith('쉬는 중') and sources['상태'][1] == '사용 가능'
    assert '코스닥 · 반도체' in at.warning[0].value


def test_a_typed_in_script_does_not_mark_an_api_as_failing():
    from datetime import datetime
    now = datetime(2026, 11, 25, 7, 50, tzinfo=api_status_view.KST)
    row = lambda who: {'day': '2026-11-25', 'slot': '07:40', 'provider': 'naver', 'endpoint': 'e', 'caller': who, 'n': 12, 'fail': 12, 'bad': 0,  # noqa: E731
                       'ms_sum': 1.0, 'ms_max': 1.0, 'bytes': 0, 'last_ok': None, 'last_fail': '2026-11-25T07:48:06+09:00'}
    state = lambda who: api_status_view.provider_states([row(who)], {}, now)['naver'][0]  # noqa: E731
    assert (state('market_movers'), state('-')) == ('실패', '호출 없음')
    assert api_status_view.cat.CALLERS['-'].startswith('임시')
