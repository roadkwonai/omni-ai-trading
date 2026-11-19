"""API 선정 실측 (api_bench): a source that cannot answer the need ranks below one that can, whatever its points."""
from unittest.mock import patch

import api_bench as b


def test_the_rubric_qualifies_before_it_scores_and_records_what_each_source_returned():
    full = {'A': {'change_pct': 1.0, 'value_krw': 2.0, 'as_of': 't'}, 'B': {'change_pct': 1.0, 'value_krw': 2.0, 'as_of': 't'}}
    need = {'ask': 'x', 'fields': ('change_pct', 'value_krw', 'as_of'), 'target': 'g', 'not_candidates': {},
            'candidates': {'naver': lambda g, u: (full, 9),                        # every field, every member, unofficial
                           'kiwoom': lambda g, u: ({'A': full['A']}, 1),            # official and cheap but half the members
                           'toss': lambda g, u: (_ for _ in ()).throw(OSError())}}  # never answers
    with patch.dict(b.NEEDS, {'t': need}):
        out = b.bench('t', 2, {'A', 'B'}, sleep=lambda s: None)
    ranking = {r['source']: r for r in out['ranking']}
    assert [r['source'] for r in out['ranking']][0] == 'naver' and ranking['naver']['qualified']
    assert not ranking['kiwoom']['qualified'] and ranking['kiwoom']['coverage'] == 0.5
    assert ranking['toss']['ok'] == 0 and ranking['toss']['points']['stable'] == 0 and ranking['toss']['errors'] == ['OSError'] * 2
    assert ranking['naver']['points']['official'] == 0 and ranking['kiwoom']['points']['official'] == b.RUBRIC['official']
