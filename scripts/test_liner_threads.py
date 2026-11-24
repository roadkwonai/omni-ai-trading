"""Past talks and searches with their answers (scripts/liner_history.py: threads, turns)."""
import sqlite3
from contextlib import closing

import pytest

import liner_history as h


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(h, 'path', lambda: tmp_path / 'nhplug' / 'ai-search-history.sqlite3')


def turn(question='ETF가 뭐예요?', answer='상장지수펀드입니다.', **more):
    return {'question': question, 'answer': answer, 'sources': [], 'guides': [], 'local': False, 'local_query': None, **more}


def test_a_talk_comes_back_whole_and_keeps_growing():
    sources = [{'제목': '기사', '원문 링크': 'https://a.kr', '발행일': '', '요약': ''}]
    guides = [{'문서': 'docs/x.md', '항목': '화면', '본문': '본문'}]
    subject = {'주제': '나스닥 급락', '기사 링크': 'https://a.kr'}
    first = h.save('대화', '나스닥 급락 영향', turn(sources=sources, guides=guides), subject=subject)
    query = {'kind': 'fills', 'period': '오늘', 'account': 'paper', 'compare_kodex': False}
    assert h.save('대화', '무시되는 제목', turn('오늘 체결은?', '기록', local=True, local_query=query), thread_id=first) == first
    talk = h.load(first)
    assert (talk['id'], talk['kind'], talk['title'], talk['subject']) == (first, '대화', '나스닥 급락 영향', subject)
    assert [t['question'] for t in talk['turns']] == ['ETF가 뭐예요?', '오늘 체결은?']
    assert talk['turns'][0]['sources'] == sources and talk['turns'][0]['guides'] == guides and talk['turns'][0]['time']
    assert talk['turns'][1]['local'] is True and talk['turns'][1]['local_query'] == query


def test_a_search_keeps_its_results():
    results = [{'제목': '기사', '원문 링크': 'https://a.kr', '발행일': '2026-11-24', '요약': '요약'}]
    made = h.save('검색', '반도체', turn('반도체', '', sources=results), subject={'주제': '반도체'})
    assert h.load(made)['kind'] == '검색' and h.load(made)['turns'][0]['sources'] == results


def test_the_list_is_newest_first_and_can_be_narrowed():
    a = h.save('대화', '첫 대화', turn('질문 하나', '100% 확실'))
    b = h.save('검색', '반도체 실적', turn('반도체 실적', ''))
    c = h.save('대화', '셋째', turn('질문 셋', "따옴표 ' 와 _밑줄"))
    h.save('대화', '', turn('첫 대화에 이어서', '답'), thread_id=a)
    rows = h.threads()
    assert [r['id'] for r in rows] == [a, c, b] and rows[0]['turns'] == 2 and rows[0]['title'] == '첫 대화'
    assert [r['id'] for r in h.threads(find='이어서')] == [a] and [r['id'] for r in h.threads(find='실적')] == [b]
    assert [r['id'] for r in h.threads(find='100%')] == [a] and h.threads(find='100_') == []  # % and _ are plain characters
    assert [r['id'] for r in h.threads(find="' 와 _밑")] == [c]
    assert [r['id'] for r in h.threads(limit=1)] == [a]


def test_deleting_removes_the_answers_and_never_reuses_the_number():
    a = h.save('대화', '가', turn())
    h.delete(a)
    assert h.load(a) is None and h.threads() == []
    b = h.save('대화', '나', turn('이어서', '답'), thread_id=a)  # a screen still showing the deleted talk
    assert b != a and h.load(b)['turns'][0]['question'] == '이어서'
    h.delete(999)
    h.record('질문', '남는 다시 요청')
    h.clear()
    assert h.threads() == [] and h.recent() == []
    with closing(sqlite3.connect(h.path())) as db:
        assert db.execute('SELECT COUNT(*) FROM turns').fetchone() == (0,)


def test_empty_missing_and_broken_files():
    assert h.threads() == [] and h.load(1) is None
    h.record('질문', '예전 이력만 있는 DB')
    assert h.threads() == [] and h.load(1) is None  # written before the talks were kept
    h.save('대화', '가', turn())
    with closing(sqlite3.connect(h.path())) as db, db:
        db.execute("UPDATE turns SET sources = '{깨짐', guides = NULL, local_query = '[]'")
        db.execute("UPDATE threads SET subject = 'x'")
    talk = h.load(1)
    assert talk['subject'] is None and talk['turns'][0]['sources'] == [] and talk['turns'][0]['answer']
    h.path().write_bytes(b'broken' * 500)
    with pytest.raises(sqlite3.Error):
        h.threads()


def test_parallel_saves_are_whole():
    from concurrent.futures import ThreadPoolExecutor
    first = h.save('대화', '같은 대화', turn())
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda i: h.save('대화', f't{i}', turn(f'q{i}', 'x' * 5000), thread_id=first if i % 2 else None), range(40)))
    assert len(h.load(first)['turns']) == 21 and len(h.threads(limit=100)) == 21


def test_a_long_title_is_cut_and_the_file_stays_private():
    made = h.save('대화', '제목 ' * 200, turn(answer='가' * 200_000))
    assert len(h.load(made)['title']) <= 120 and len(h.load(made)['turns'][0]['answer']) == 200_000
    assert h.path().stat().st_mode & 0o777 == 0o600


def test_archived_talks_leave_the_list_and_survive_a_clear():
    a, b, c = (h.save('대화' if i else '검색', f't{i}', turn(f'q{i}', '찾을 말')) for i in range(3))
    h.archive(a, True)
    h.archive(b, True)
    assert [r['id'] for r in h.threads()] == [c] and [r['id'] for r in h.threads(archived=True)] == [b, a]
    assert [r['id'] for r in h.threads(find='찾을 말', archived=True)] == [b, a]
    assert h.load(a)['archived'] is True and h.load(c)['archived'] is False
    h.archive(a, False)
    assert [r['id'] for r in h.threads()] == [c, a]
    h.save('대화', '', turn('이어서', '답'), thread_id=b)  # an archived talk stays archived when it goes on
    assert [r['id'] for r in h.threads(archived=True)] == [b]
    h.clear()
    assert h.threads() == [] and [r['id'] for r in h.threads(archived=True)] == [b] and len(h.load(b)['turns']) == 2
    h.delete(b)
    assert h.threads(archived=True) == []


def test_reading_and_writing_at_the_same_time_never_locks():
    import threading
    for i in range(300):
        h.save('대화', f't{i}', turn(f'q{i}', 'x' * 3000))
    stop, errors = threading.Event(), []

    def read():
        while not stop.is_set():
            try:
                h.threads(find='없는 말')
            except sqlite3.Error as exc:
                errors.append(exc)
    readers = [threading.Thread(target=read) for _ in range(3)]
    for reader in readers:
        reader.start()
    try:
        for i in range(60):
            h.save('대화', f'w{i}', turn())
    finally:
        stop.set()
        for reader in readers:
            reader.join()
    assert errors == [] and len(h.threads(limit=1000)) == 360
    with closing(sqlite3.connect(h.path())) as db:
        assert db.execute('PRAGMA journal_mode').fetchone() == ('wal',)
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in h.path().parent.glob('ai-search-history*'))


def test_cleared_answers_leave_the_file():
    for i in range(20):
        h.save('대화', f't{i}', turn(answer=f'지워질 말 {i} ' * 2000))
    with closing(sqlite3.connect(h.path())) as db:
        before = db.execute('PRAGMA page_count').fetchone()[0]
    h.clear()
    with closing(sqlite3.connect(h.path())) as db:
        assert db.execute('PRAGMA page_count').fetchone()[0] < before / 5 and db.execute('PRAGMA freelist_count').fetchone() == (0,)


def test_the_edges_of_the_lists(monkeypatch):
    a = h.save('대화', '가', turn(answer=r'C:\Users\x'))
    h.save('대화', '나', turn())
    assert [r['id'] for r in h.threads(find='\\')] == [a]
    monkeypatch.setattr(h, '_now', lambda: '2026-11-24T10:00:00.000000+09:00')
    same = [h.save('대화', str(i), turn()) for i in range(3)]
    assert [r['id'] for r in h.threads() if r['id'] in same] == same[::-1]  # the same moment: the last made first
    for i in range(12):
        h.record('질문', f'q{i}')
    assert h.recent() == [('질문', f'q{i}') for i in range(11, 3, -1)]
