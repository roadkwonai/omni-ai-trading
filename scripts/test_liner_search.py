import os
import sqlite3
from unittest.mock import Mock

import pytest
import requests

import liner_history
import liner_search


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    env = tmp_path / '.env'
    env.write_text('LINER_API_KEY=test-key\n', encoding='utf-8')
    monkeypatch.setattr(liner_search.local_env, 'ENV', env)
    monkeypatch.setattr(liner_history, 'path', lambda: tmp_path / 'nhplug' / 'ai-search-history.sqlite3')


def reply(status=200, payload=None, error=None):
    response = Mock(status_code=status)
    response.json.side_effect = error
    response.json.return_value = payload
    return response


@pytest.mark.parametrize('status, said', [(401, 'LINER_API_KEY'), (402, '크레딧'), (403, '계정'), (429, '잠시 뒤'),
                                          (500, '잠시 뒤'), (502, '잠시 뒤'), (400, 'HTTP 400')])
def test_a_refused_request_says_what_to_do(monkeypatch, status, said):
    monkeypatch.setattr(liner_search.requests, 'post', lambda *a, **k: reply(status))
    with pytest.raises(ValueError, match=f'(?s){said}.*') as caught:
        liner_search.search('삼성전자')
    assert f'HTTP {status}' in str(caught.value) and 'test-key' not in str(caught.value)


def test_a_timeout_is_not_called_a_connection_failure(monkeypatch):
    def slow(*a, **k):
        raise requests.ReadTimeout('310')
    monkeypatch.setattr(liner_search.requests, 'post', slow)
    with pytest.raises(ValueError, match='시간 안에 응답하지'):
        liner_search.analyze([{'role': 'user', 'content': 'ETF'}])
    monkeypatch.setattr(liner_search.requests, 'post', Mock(side_effect=requests.ConnectionError('x')))
    with pytest.raises(ValueError, match='연결하지 못했습니다'):
        liner_search.search('ETF')


@pytest.mark.parametrize('payload, error', [(None, ValueError()), ([], None), ({}, None), ({'results': None}, None)])
def test_unreadable_search_replies_are_value_errors(monkeypatch, payload, error):
    monkeypatch.setattr(liner_search.requests, 'post', lambda *a, **k: reply(200, payload, error))
    with pytest.raises(ValueError, match='Liner'):
        liner_search.search('ETF')


def test_bad_rows_are_dropped_and_missing_fields_filled(monkeypatch):
    rows = liner_search.source_rows([None, {'title': 'x'}, {'url': 'javascript:alert(1)'}, {'url': 'https://u:p@a.kr'},
                                     {'url': 'https://a.kr/\nb'}, {'url': 'http://[bad'},
                                     {'url': 'https://a.kr/1', 'title': ' ', 'description': None, 'date': None}])
    assert rows == [{'제목': 'a.kr', '발행일': '', '요약': '', '원문 링크': 'https://a.kr/1'}]


def test_history_is_private_distinct_and_newest_first():
    for kind, query in (('검색', '가'), ('질문', '가'), ('검색', '나'), ('검색', '가')):
        liner_history.record(kind, query)
    assert liner_history.recent() == [('검색', '가'), ('검색', '나'), ('질문', '가')]
    path = liner_history.path()
    os.chmod(path, 0o644)  # e.g. restored from an archive
    liner_history.record('검색', '다')
    assert path.stat().st_mode & 0o777 == 0o600
    assert len(liner_history.recent()) == 4


def test_history_keeps_a_bounded_number_of_rows(monkeypatch):
    monkeypatch.setattr(liner_history, 'KEEP', 10)
    for i in range(15):
        liner_history.record('질문', f'질문 {i}')
    with sqlite3.connect(liner_history.path()) as db:
        assert db.execute('SELECT COUNT(*), MIN(query), MAX(id) FROM history').fetchone() == (10, '질문 10', 15)
    db.close()
    assert liner_history.recent()[0] == ('질문', '질문 14')


def test_parallel_records_are_all_kept():
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda i: liner_history.record('질문', f'q{i}'), range(40)))
    with sqlite3.connect(liner_history.path()) as db:
        assert db.execute('SELECT COUNT(DISTINCT query) FROM history').fetchone() == (40,)
    db.close()


def test_a_paid_answer_survives_a_history_that_cannot_be_written(monkeypatch):
    import sys
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'streamlit_namuh'))
    import liner_jobs

    def keep(result):
        return liner_history.save('대화', 'x', {'question': 'x', 'answer': result})
    assert liner_jobs.submit('질문', 'x', lambda: 'answer', keep).result(5) == ('answer', True, 1)
    path = liner_history.path()
    path.write_bytes(b'broken' * 500)
    assert liner_jobs.submit('질문', 'x', lambda: 'answer', keep).result(5) == ('answer', False, None)
    path.unlink()
    assert liner_jobs.submit('질문', 'x', lambda: '\ud800', keep).result(5) == ('\ud800', False, None)
    assert liner_jobs.submit('질문', '\ud800', lambda: 'answer', keep).result(5)[1]  # the talk is kept, its place in the request list is not
    assert liner_jobs.submit('내부 조회', 'x', lambda: 'answer').result(5) == ('answer', True, None)


def test_what_is_sent_to_liner(monkeypatch, tmp_path):
    from unittest.mock import Mock
    env = tmp_path / '.env'
    env.write_text('LINER_API_KEY=test-key\n')
    monkeypatch.setattr(liner_search.local_env, 'ENV', env)
    reply = Mock(status_code=200)
    post = Mock(return_value=reply)
    monkeypatch.setattr(liner_search.requests, 'post', post)
    reply.json.return_value = {'results': [{'title': str(i), 'url': f'https://a.kr/{i}'} for i in range(30)] + [{'url': 'https://a.kr\\@evil.kr'}]}
    assert len(liner_search.search(' 반도체 ')) == 10
    post.assert_called_with(liner_search.ENDPOINT, headers={'x-api-key': 'test-key'}, timeout=10, allow_redirects=False, json={
        'query': '반도체', 'country_code': 'kr', 'lang': 'ko', 'max_results': 10, 'include_knowledge': False})
    reply.json.return_value = {'answer': ' 답 ', 'references': [{'url': 'https://a.kr\\@evil.kr'}, {'url': 'https://b.kr'}]}
    assert liner_search.analyze([{'role': 'user', 'content': 'q'}]) == ('답', [{'제목': 'b.kr', '발행일': '', '요약': '', '원문 링크': 'https://b.kr'}])
    post.assert_called_with(liner_search.ANALYSIS_ENDPOINT, headers={'x-api-key': 'test-key'}, timeout=310, allow_redirects=False, json={
        'messages': [{'role': 'user', 'content': 'q'}], 'lang': 'ko', 'mode': 'general', 'stream': False})
    reply.status_code = 204
    with pytest.raises(ValueError):
        liner_search.search('반도체')
