import pytest

import liner_topics


@pytest.mark.parametrize('name,count', [('노후 자금·투자 권유', 7), ('창업 준비', 8)])
def test_question_groups_for_decisions_that_are_hard_to_undo(name, count):
    questions = liner_topics.groups()[name]
    assert len(questions) == len(set(questions)) == count
    assert all(q.endswith(('?', '.')) and len(q) <= 80 for q in questions)


def test_every_guide_section_exists_in_its_document():
    from pathlib import Path
    root = Path(liner_topics.__file__).parent.parent
    for path, sections in liner_topics.GUIDE_SECTIONS.items():
        text = (root / path).read_text(encoding='utf-8')
        assert [s for s in sections if f'# {s}\n' not in text] == [], path


def test_guide_excerpts_are_few_and_short(monkeypatch):
    import liner_topics
    monkeypatch.setattr(liner_topics, 'read_sections', lambda *a: {title: ('긴 줄입니다.' * 20 + '\n') * 60 for titles in liner_topics.GUIDE_SECTIONS.values() for title in titles})
    found = liner_topics.find_guides('AI 검색 요금, 거장 카드, 매수 조건, 장전 분석, NXT 청산, Jev API')
    assert len(found) == 3 and all(len(guide['본문']) <= 1500 for guide in found)
