import unittest
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


def test_mover_agreement_compares_the_same_moment_and_the_rankings(monkeypatch):
    import api_bench as b
    import market_movers as m
    universe = {f'00000{i}': {'name': f'n{i}', 'index': 'KOSPI200'} for i in range(4)}
    row = lambda c, chg: {'code': c, 'name': 'x', 'index': 'KOSPI200', 'price': 100, 'change_pct': chg, 'volume': 10, 'value_mil': 5}  # noqa: E731
    monkeypatch.setattr(m, 'scan', lambda read, one, **k: [row(c, 1.0) for c in one])
    monkeypatch.setattr(m, 'kiwoom_scan', lambda one: [row(c, 1.0) for c in one])
    out = b.mover_agreement(None, universe, list(universe), sleep=lambda s: None)
    assert out['compared'] == 4 and out['fields']['price_equal'] == 1.0 and out['candidates_same']
    assert all(v['same'] for v in out['top10'].values()) and out['whole_answer']['kiwoom_calls'] == 1


class BarAgreementTest(unittest.TestCase):
    def test_finds_the_minute_offset_and_skips_nh_forming_bar(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        import api_bench
        kst = ZoneInfo('Asia/Seoul')
        row = lambda t, c: {'bsop_date': '20261120', 'bsop_time': t, 'stck_oprc': c, 'stck_hgpr': c, 'stck_lwpr': c, 'stck_prpr': c, 'vol': 10}  # noqa: E731
        nh = [row('130000', 100), row('130100', 101), row('130200', 102)]  # 13:02 still forming
        kw = [(datetime(2026, 11, 20, 13, 1, tzinfo=kst), 100, 100, 100, 100, 10),
              (datetime(2026, 11, 20, 13, 2, tzinfo=kst), 101, 101, 101, 101, 10)]  # stamped by end minute
        got = api_bench.bar_agreement(nh, kw, '20261120')
        self.assertEqual((got['offset_min'], got['bars'], got['ohlc_equal']), (1, 2, 1.0))
