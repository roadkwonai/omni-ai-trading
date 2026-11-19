import json
from datetime import date, datetime, timedelta

import pytest

import us_ideas as U


def facts(**over):
    base = {'revenue': [100, 120, 150, 190, 250], 'gross_profit': [50, 60, 75, 95, 125], 'operating_income': [5, 8, 12, 20, 30],
            'net_income': [4, 6, 9, 15, 18], 'cfo': [3, 5, 8, 12, 20], 'capex': [1, 1, 2, 2, 3],
            'shares_diluted': [10, 10, 10, 10.2, 10.3], 'total_debt': [10, 10, 10, 10, 11], 'total_equity': [20, 25, 30, 40, 50],
            'cash': [5, 6, 7, 9, 12]}
    return {**base, **over}


def test_names_match_across_sec_and_nport_spellings():
    assert U.norm('C. H. Robinson Worldwide, Inc.') == U.norm('C.H. ROBINSON WORLDWIDE INC /DE/')
    assert U.norm('Valero Energy Corp.') == U.norm('VALERO ENERGY CORP/TX')
    assert U.norm('Exxon Mobil Corp.') == U.norm('ExxonMobil Holdings Corp')
    assert U.norm('Williams Cos., Inc. (The)') == U.norm('WILLIAMS COMPANIES, INC.')
    payload = {'0': {'cik_str': 1, 'ticker': 'AAA', 'title': 'Alpha Inc'}, '1': {'cik_str': 2, 'ticker': 'BBB', 'title': 'Beta Corp'}}
    rows, missed = U.match([{'asset_category': 'EC', 'name': 'Alpha, Inc.', 'pct_of_net_assets': 1.5},
                            {'asset_category': 'EC', 'name': 'x', 'ticker': 'BBB'},
                            {'asset_category': 'EC', 'name': 'Gamma plc'}, {'asset_category': 'STIV', 'name': 'cash'}],
                           U.ticker_index(payload), {'AAA': '0000000001', 'BBB': '0000000002'})
    assert [r[0] for r in rows] == ['AAA', 'BBB'] and rows[0][1] == '0000000001' and missed == ['Gamma plc']


def test_metrics_guard_splits_and_compute_valuation():
    m = U.metrics(facts(), price=20.0, closes=[float(i) for i in range(1, 301)])
    assert round(m['rev_growth'], 4) == round(250 / 190 - 1, 4) and round(m['rev_cagr3'], 4) == round((250 / 120) ** (1 / 3) - 1, 4)
    assert m['mcap'] == 20.0 * 10.3 and m['pe'] == 20.0 * 10.3 / 18 and m['fcf'] == 17
    assert round(m['dilution'], 4) == round(10.3 / 10.2 - 1, 4) and m['above200'] and m['pos52'] == 1.0
    assert U.metrics(facts(shares_diluted=[1, 1, 1, 1, 10]))['dilution'] is None  # a 10:1 split is not dilution
    empty = U.metrics({})
    assert empty['rev_growth'] is None and empty['roe'] == [None] * 5


def test_screens_pass_and_fail_on_the_documented_hypotheses():
    m = U.metrics(facts(), price=1e8, closes=None)  # market cap 1.03e9
    ok, score, reasons = U.screen('tenbagger', m, {'IWO': 0.1})
    assert ok and score > 0 and any('매출 성장' in r for r in reasons)
    assert not U.screen('tenbagger', U.metrics(facts(), price=1e10), {})[0]  # too large to be a ten-bagger
    assert not U.screen('buffett', m, {'IWO': 0.1})[0]  # outside the S&P 500
    good = facts(net_income=[10, 11, 12, 13, 15.5], total_equity=[50, 55, 60, 65, 70])
    ok, _, reasons = U.screen('buffett', U.metrics(good, price=10.0), {'IVV': 0.2})
    assert ok and reasons[0].startswith('ROE 5년')
    lynch = U.metrics(facts(net_income=[4, 6, 9, 15, 18]), price=17.5)  # +20% growth, PE 10, PEG 0.5
    assert U.screen('lynch', lynch, {'QQQM': 1})[0] and not U.screen('lynch', lynch, {'IWO': 1})[0]
    assert U.screen('fisher', m, {'IVV': 1})[0]
    odd = U.metrics(facts(), price=0.01)  # PER far below 3: a share-class or unit mismatch, not a bargain
    ok, score, reasons = U.screen('lynch', odd, {'QQQM': 1})
    assert not ok and score is None and reasons[0].startswith('자료 이상(')
    with pytest.raises(ValueError):
        U.screen('nobody', m, {})


def test_outcome_waits_until_the_horizon_is_reached():
    start = date(2026, 1, 2)
    series = {start + timedelta(days=i): 100 + i for i in range(120)}
    assert U.outcome(series, start, 1) == pytest.approx(130 / 100 - 1)
    assert U.outcome(series, start, 6) is None


def test_weekly_run_records_picks_status_and_scorecard(tmp_path):
    db = U.connect(tmp_path)
    old = (date.today() - timedelta(days=400)).isoformat()
    with db:
        db.execute('INSERT INTO universe VALUES (?,?,?,?,?,?)', (old, 'AAA', '1', 'Alpha', 'IWO', 0.5))
        db.execute('INSERT INTO facts VALUES (?,?,?)', (old, 'AAA', json.dumps(facts())))
    closes = {date.today() - timedelta(days=500 - i): 1e8 * (1 + i / 1000) for i in range(500)}
    info = U.run_weekly(tmp_path, date.today(), fetch=lambda t, years=2: closes, pause=0)
    assert info['passed']['tenbagger'] == 1 and info['prefiltered'] == 1
    st = U.status(tmp_path)
    assert st['ready'] and st['picks'][0]['ticker'] == 'AAA' and st['picks'][0]['new'] is False
    assert U.run_weekly(tmp_path, date.today(), fetch=lambda t, years=2: closes, pause=0)['passed']['tenbagger'] == 1  # rerun replaces
    assert len(U.status(tmp_path)['picks']) == 1
    with db:  # 13F filers report different quarters: each shows its latest
        db.execute('INSERT INTO managers13f VALUES (?,?,?)', ('2026Q2', 'B', json.dumps({'master': 'm', 'firm': 'b'})))
        db.execute('INSERT INTO managers13f VALUES (?,?,?)', ('2026Q3', 'A', json.dumps({'master': 'm', 'firm': 'a'})))
        db.execute('INSERT INTO managers13f VALUES (?,?,?)', ('2026Q1', 'B', json.dumps({'master': 'm', 'firm': 'old'})))
    st = U.status(tmp_path)
    assert [(m['manager'], m['quarter']) for m in st['managers']] == [('A', '2026Q3'), ('B', '2026Q2')] and st['quarter_13f'] == '2026Q3'


def test_scheduled_runs_the_universe_on_the_quarters_third_friday_and_the_week_once(tmp_path, monkeypatch):
    done = []
    monkeypatch.setattr(U, 'run_universe', lambda root, today: done.append(('universe', today)) or {})
    monkeypatch.setattr(U, 'run_weekly', lambda root, today: done.append(('weekly', today)) or {})
    at = lambda m, d, h, mi: datetime(2026, m, d, h, mi, tzinfo=U.KST)
    assert U.third_friday(date(2026, 11, 20)) and not U.third_friday(date(2026, 11, 13)) and not U.third_friday(date(2026, 12, 18))
    assert U.scheduled(tmp_path, at(11, 13, 7, 0)) == 'not_due' and done == []  # a Friday, not the third
    assert U.scheduled(tmp_path, at(11, 20, 7, 0)) == 'universe+weekly' and [k for k, _ in done] == ['universe', 'weekly']
    db = U.connect(tmp_path)
    with db:
        db.execute("INSERT INTO runs VALUES ('2026-11-20', 'weekly', 'x', '{}')")
    assert U.scheduled(tmp_path, at(11, 20, 7, 30)) == 'already'  # the 07:00 run did this week's screen
    assert U.scheduled(tmp_path, at(11, 13, 7, 30)) == 'weekly' and done[-1] == ('weekly', date(2026, 11, 13))


def test_criteria_explain_each_screen_and_decide_it():
    # user request 2026-11-18: why a name passed or failed, criterion by criterion, from the code values
    m = U.metrics(facts(), price=1e8)
    rows = U.criteria('tenbagger', m, {'IWO': 0.1})
    assert [r[0] for r in rows][:3] == ['시가총액 3억~100억 달러', '매출 성장(1년) 25% 이상', '매출 3년 연평균 20% 이상']
    assert all(r[2] for r in rows) and U.screen('tenbagger', m, {'IWO': 0.1})[0]
    big = U.criteria('tenbagger', U.metrics(facts(), price=1e10), {})
    assert big[0][2] is False and '1,030억' in big[0][1]  # the failing criterion carries its value
    for master in U.MASTERS:  # screen() decides by these rows: one place for every threshold
        for etfs in ({'IVV': 1}, {'QQQM': 1}, {'IWO': 1}):
            for price in (1.0, 17.5, 1e8, 1e10):
                mm = U.metrics(facts(), price=price)
                if U.suspect(mm) is None and U.screen(master, mm, etfs)[1] is not None:
                    assert U.screen(master, mm, etfs)[0] == all(r[2] for r in U.criteria(master, mm, etfs))
    missing = U.criteria('buffett', U.metrics({}), {'IVV': 1})
    assert any(r[1] == '자료 없음' and r[2] is False for r in missing)


def test_13f_collection_pages_through_every_position(tmp_path):
    class Tool:  # 2 pages of positions, changes on the first
        def __init__(self):
            self.calls = []

        def execute(self, **kw):
            self.calls.append(kw['offset'])
            page = {0: ([{'issuer': 'A', 'cusip': '1'}], 1), 1: ([{'issuer': 'B', 'cusip': '2'}], None)}[kw['offset']]
            return json.dumps({'data': {'cik': '1', 'filing': {'quarter': '2026Q3'}, 'positions': page[0],
                                        'changes': {'items': [{'action': 'new', 'issuer': 'B'}]}}, 'paging': {'next_offset': page[1]}})
    tool, notes = Tool(), {}
    db = U.connect(tmp_path)
    U.collect_13f(db, notes, tool)
    stored = json.loads(db.execute("SELECT data FROM managers13f WHERE manager='Berkshire Hathaway'").fetchone()[0])
    assert [p['issuer'] for p in stored['positions']] == ['A', 'B'] and stored['changes'] == [{'action': 'new', 'issuer': 'B'}]
    assert set(notes['13f'].values()) == {'2026Q3'}


def test_us_etfs_map_to_the_engines_domestic_etfs_by_proxy_and_alias():
    # user approval 2026-11-18 (docs/longterm-account-settings-plan.md 3-A)
    pick = {'etfs': {'IVV': 6.4, 'VOO': 1.0, 'QQQM': 2.0, 'SMH': 0.5, 'QTUM': None, 'IWO': 0.1}}
    assert U.domestic_etfs(pick) == {'SPX': 6.4, 'NDX': 2.0, 'SEMI': 0.5, 'QUANT': None}
    assert list(U.domestic_etfs(pick))[:2] == ['SPX', 'NDX']  # largest weight first
    assert U.domestic_etfs({'etfs': {'IWO': 0.1}}) == {} and U.domestic_etfs({}) == {}
