import json
from datetime import datetime

import pytest

import longterm_report as R
import longterm_template as T
import vibe_longterm as L
from test_vibe_longterm import DAY, KST, fake_fetch


@pytest.fixture
def decided(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    answer = json.dumps({'market_view': '', 'stories': {k: {'status': 'intact', 'tilt': 0, 'reason': 'ok'} for k in L.SATS}})
    L.decide(tmp_path, datetime.combine(DAY, datetime.min.time(), KST).replace(hour=8, minute=45), fetch=fake_fetch, ask=lambda t: answer)
    return tmp_path


def good_json(nav=L.CAPITAL):
    return {'summary': '조정 국면, 오늘은 초기 투입 25%만 진행한다.', 'masters': {k: '관점' for k in L.MODULES},
            'scenarios': [{'name': n, 'trigger': 't', 'action': 'a'} for n in ('상승', '횡보', '급락')],
            'advice_levels': [{'asset': 'SEMI', 'horizon': '스윙', 'master': 'lynch_story', 'side': 'sell', 'price': 40000,
                               'size': {'weight': 0.03, 'krw': 3_000_000}, 'condition': 'c', 'kind': 'advice', 'status': 'waiting'}],
            'review': '첫 리포트', 'sources': [{'title': 'Yahoo', 'url': 'https://finance.yahoo.com', 'time': '2026-11-18'}],
            'ideas': []}


def test_blank_report_has_every_section_in_order_and_is_not_finished(decided):
    facts = R.data(DAY.isoformat(), root=decided, reports=decided / 'reports', fetch=fake_fetch, context=False)
    assert facts['plan_is_today'] and facts['levels'] and all('evidence' in x for x in facts['levels'])
    blank = R.report_md(facts)
    problems = T.check(blank, DAY.isoformat())
    assert not [p for p in problems if '빠진 절' in p or '순서' in p or '제목' in p]  # the skeleton is the template
    assert any('채우지 않은 자리' in p for p in problems)  # but not a finished report
    filled = blank.replace(T.PLACEHOLDER, '분석 내용을 채운 문장입니다. 근거와 함께 씁니다.')
    assert T.check(filled, DAY.isoformat()) == []


def test_template_rejects_missing_or_reordered_sections():
    body = '\n'.join([T.title('2026-11-18')] + [f'## {i + 1}. {s}\n본문 내용이 충분히 들어 있는 문장입니다.' for i, s in enumerate(T.SECTIONS)])
    assert T.check(body, '2026-11-18') == []
    assert any('빠진 절' in p for p in T.check(body.replace('## 9. 시나리오 대응', '## 9. 기타'), '2026-11-18'))
    swapped = body.replace('## 1. 오늘의 결론', '## X').replace('## 2. 거시 시황', '## 1. 오늘의 결론').replace('## X', '## 2. 거시 시황')
    assert any('순서' in p for p in T.check(swapped, '2026-11-18'))
    assert T.kind_of('2026-W47') == 'week' and T.title('2026-11').startswith('# 장기투자 월간')


def test_json_must_hold_masters_scenarios_advice_inside_the_account_and_sources():
    assert R.json_problems(good_json(), L.CAPITAL) == []
    rule = good_json()
    rule['advice_levels'][0]['kind'] = 'rule'
    assert any('제안(advice)' in p for p in R.json_problems(rule, L.CAPITAL))
    big = good_json()
    big['advice_levels'][0]['size'] = {'weight': 0.5, 'krw': 500_000_000}
    assert any('계좌 한도' in p for p in R.json_problems(big, L.CAPITAL))
    assert any('masters' in p for p in R.json_problems({**good_json(), 'masters': {'bogle_core': 'x'}}, L.CAPITAL))
    assert any('sources' in p for p in R.json_problems({**good_json(), 'sources': []}, L.CAPITAL))


def test_check_reads_both_files_and_previous_report_is_found(decided):
    reports = decided / 'reports'
    reports.mkdir()
    day = DAY.isoformat()
    md, js = R.paths(day, reports)
    md.write_text(R.report_md(R.data(day, root=decided, reports=reports, fetch=fake_fetch, context=False)).replace(T.PLACEHOLDER, '채운 분석 문장입니다. 근거가 있습니다.'), encoding='utf-8')
    js.write_text(json.dumps(good_json(), ensure_ascii=False), encoding='utf-8')
    assert R.check(day, root=decided, reports=reports) == []
    assert R.previous_report('2026-11-19', reports)['day'] == day
    (reports / f'strategy-{day}.md-E').write_text('backup')  # what `sed -i -E` leaves on macOS
    assert any('리포트가 아닌 파일' in p for p in R.check(day, root=decided, reports=reports))


def test_suggested_satellite_buys_must_fit_the_satellite_budget():
    many = good_json()
    many['advice_levels'] = [{**many['advice_levels'][0], 'asset': k, 'side': 'buy', 'size': {'weight': 0.10, 'krw': 10_000_000}}
                             for k in ('SEMI', 'QUANT', 'SPACE')]  # 30M against a 25M satellite target
    assert any('위성 목표' in p for p in R.json_problems(many, L.CAPITAL))
    assert not any('위성 목표' in p for p in R.json_problems(many, L.CAPITAL, {**L.DEFAULT_SETTINGS['weights'], 'SEMI': 0.15, 'RES': 0.10}))


def test_blank_report_shows_sleeve_budgets_and_the_etf_drawdown(decided):
    blank = R.report_md(R.data(DAY.isoformat(), root=decided, reports=decided / 'reports', fetch=fake_fetch, context=False))
    assert '슬리브 예산: 코어 60%' in blank and '위성 합계 25%(25,000,000원)' in blank
    assert '원화 기준 252일 고점 대비' in blank


def fill_doc():
    sections = {s: f'{s}에 대한 분석 문장입니다. 근거와 숫자를 함께 씁니다.' for s in R.FILLED}
    return {**good_json(), 'sections': sections}


def test_fill_writes_both_files_that_pass_the_check(decided):
    reports = decided / 'reports'
    day = DAY.isoformat()
    R.fill(day, fill_doc(), root=decided, reports=reports, fetch=fake_fetch)
    md, js = R.paths(day, reports)
    text = md.read_text(encoding='utf-8')
    assert T.PLACEHOLDER not in text and '[Yahoo](https://finance.yahoo.com)' in text
    assert '- 급락: 트리거 t → a' in text and '제안 타점 추가' in text
    assert all(f"{x['price']:,.0f}" in text for x in R.data(day, root=decided, reports=reports, fetch=fake_fetch, context=False)['levels'])
    assert json.loads(js.read_text(encoding='utf-8'))['sections']['위험·주의']  # a revision refills from the json
    assert R.check(day, root=decided, reports=reports) == []


def test_a_partial_fill_writes_nothing_so_no_draft_is_published(decided):
    # the report publisher sends any strategy-*.md that appears (report_pages); a draft must never land in the folder
    reports = decided / 'reports'
    day = DAY.isoformat()
    doc = fill_doc()
    del doc['sections']['위험·주의']
    problems = R.fill(day, doc, root=decided, reports=reports, fetch=fake_fetch)
    assert any('채우지 않은 자리' in p for p in problems)
    md, js = R.paths(day, reports)
    assert not md.exists() and not js.exists()
    assert R.fill(day, fill_doc(), root=decided, reports=reports, fetch=fake_fetch) == [] and md.exists()


def test_previous_report_is_the_latest_of_the_same_kind(tmp_path):
    for key in ('2026-10-29', '2026-10-30', '2026-10', '2026-W44', '2026-W45'):
        (tmp_path / f'strategy-{key}.json').write_text('{}')
    assert R.previous_report('2026-11-02', tmp_path)['day'] == '2026-10-30'  # not the monthly or a weekly file
    assert R.previous_report('2026-11', tmp_path)['day'] == '2026-10'
    assert R.previous_report('2026-W46', tmp_path)['day'] == '2026-W45'
    assert R.previous_report('2026-10-29', tmp_path) == {}


def test_rule_levels_of_switched_off_modules_are_not_called_engine_rules(decided):
    facts = R.data(DAY.isoformat(), root=decided, reports=decided / 'reports', fetch=fake_fetch, context=False)
    on, off = dict(facts['levels'][0], kind='rule', active=True), dict(facts['levels'][0], kind='rule', active=False, price=12345)
    text = R.report_md({**facts, 'levels': [on, off]}).split('## 6. 타점 지도')[1].split('## 7.')[0]
    rows = [line for line in text.splitlines() if line.startswith('| ') and '12,345' in line]
    assert len(rows) == 1 and '| 규칙(꺼짐) |' in rows[0]
    assert '| 규칙 |' in text


def test_a_morning_report_does_not_call_yesterdays_plan_today(decided):
    facts = R.data(DAY.isoformat(), root=decided, reports=decided / 'reports', fetch=fake_fetch, context=False)
    facts['account']['last_plan'] = {'day': '2026-11-17', 'why': 'monthly_band', 'orders': [{}], 'execute': False}
    text = R.report_md(facts)
    assert '오늘 계획' not in text and '오늘 주문 계획' not in text and '2026-11-17' in text.split('## 2.')[0]


def test_non_object_advice_items_are_reported_not_raised():
    bad = {**good_json(), 'advice_levels': ['NDX 사기']}
    assert any('advice_levels' in p for p in R.json_problems(bad, L.CAPITAL))


def test_monthly_check_does_not_flag_that_months_daily_reports(decided):
    reports = decided / 'reports'
    reports.mkdir()
    for name in ('strategy-2026-11.md', 'strategy-2026-11.json', 'strategy-2026-11-17.md', 'strategy-2026-11-17.json'):
        (reports / name).write_text('x')
    assert not any('리포트가 아닌 파일' in p for p in R.check('2026-11', root=decided, reports=reports))


def test_period_reports_carry_the_period_review_label(decided):
    week = R.report_md(R.data('2026-W47', root=decided, reports=decided / 'reports', fetch=fake_fetch, context=False))
    assert week.startswith('# 장기투자 주간 전략 리포트 · 2026-W47') and '**기간 점검**' in week
    day = R.report_md(R.data(DAY.isoformat(), root=decided, reports=decided / 'reports', fetch=fake_fetch, context=False))
    assert '**기간 점검**' not in day


def test_report_shows_rates_credit_and_releases_with_their_basis(tmp_path, monkeypatch):
    import longterm_macro as M
    from test_longterm_macro import fake_get, page
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    answer = json.dumps({'market_view': '', 'stories': {k: {'status': 'intact', 'tilt': 0, 'reason': 'ok'} for k in L.SATS}})
    facts = M.collect(DAY, get=fake_get(), key='k', page=page)
    L.decide(tmp_path, datetime.combine(DAY, datetime.min.time(), KST).replace(hour=8, minute=45), fetch=fake_fetch,
             ask=lambda t: answer, macro=lambda d: facts)
    md = R.report_md(R.data(DAY.isoformat(), root=tmp_path, reports=tmp_path / 'reports', fetch=fake_fetch, context=False))
    macro = md.split('## 2. 거시 시황')[1].split('## 3.')[0]
    assert '| 하이일드 스프레드 (FRED 2026-11-16) | 3.98%p · 1개월 +0.21 · 1년 1.00~3.98 |' in macro
    assert '다가오는 미국 발표(30일, FRED·연준 일정): 고용 2026-12-04, FOMC 2026-12-09, CPI 2026-12-10' in macro


def test_report_says_when_macro_is_missing(decided):
    md = R.report_md(R.data(DAY.isoformat(), root=decided, reports=decided / 'reports', fetch=fake_fetch, context=False))
    assert '거시 자료 일부 없음: fomc: OSError, FRED_API_KEY 없음' in md.split('## 3.')[0]


def test_story_is_korean_and_the_account_table_shows_the_engines_target(decided):
    facts = R.data(DAY.isoformat(), root=decided, reports=decided / 'reports', fetch=fake_fetch, context=False)
    facts['stories'] = {'stories': {'QUANT': {'status': 'watch'}}}
    facts['account']['last_plan'] = {'day': DAY.isoformat(), 'want': {'QUANT': 0.04}, 'weights': {}}
    text = R.report_md(facts)
    sec4, sec6 = text.split('## 4.')[1].split('## 5.')[0], text.split('## 7. 계좌 계획')[1].split('## 8.')[0]
    assert '| 주의 |' in sec4 and 'watch' not in sec4
    assert '설정 비중' in sec6 and '엔진 목표' in sec6
    row = next(line for line in sec6.splitlines() if line.startswith('| SOL 미국양자컴퓨팅TOP10'))
    assert '| 8.0% | 4.0% |' in row  # configured 8% halved by the 주의 story


def test_a_past_reports_sizes_are_checked_against_that_days_nav(decided):
    reports = decided / 'reports'
    reports.mkdir()
    day = DAY.isoformat()
    md, js = R.paths(day, reports)
    md.write_text(R.report_md(R.data(day, root=decided, reports=reports, fetch=fake_fetch, context=False)).replace(T.PLACEHOLDER, '채운 분석 문장입니다. 근거가 있습니다.'), encoding='utf-8')
    big = good_json()
    big['advice_levels'][0]['size'] = {'weight': 0.5, 'krw': 60_000_000}
    js.write_text(json.dumps(big, ensure_ascii=False), encoding='utf-8')
    db = L.connect(decided)
    with db:
        db.executemany('INSERT INTO nav VALUES (?,?,?)', [(day, 'actual', 50_000_000), ('2026-12-01', 'actual', 200_000_000)])
    L.put_state(db, nav_latest={'day': '2026-12-01', 'actual': 200_000_000})  # the account grew after that report
    assert any('계좌 한도' in p for p in R.check(day, root=decided, reports=reports))  # 60M against that day's 50M


# ---- S4 (docs/longterm-master-dca-ideas-plan.md 5-1, user approval 2026-11-18): US ideas and take-profit/rebalancing ----

def idea(**over):
    card = {'ticker': 'AAA', 'master': 'tenbagger', 'lenses': ['lynch-garp', 'chanos-forensic-short'], 'verdict': '관찰',
            'thesis': {'core': '구독 매출이 빠르게 늘고 총이익률이 높다.', 'falsifiers': ['성장 둔화', '희석 확대', '경쟁 심화'],
                       'redline': '2분기 연속 매출 감소', 'recheck': '2027-02-20'},
            'filings': [{'form': '10-Q', 'date': '2026-11-05', 'url': 'https://www.sec.gov/Archives/edgar/data/1/x.htm'}]}
    return {**card, **over}


@pytest.fixture
def picked(decided):
    import us_ideas as U
    db = U.connect(decided)
    with db:
        db.execute('INSERT INTO picks VALUES (?,?,?,?,?,?,?,?,?,?,?)', ('2026-11-13', 'tenbagger', 1, 'AAA', 'Alpha', 10.0, '2026-11-12',
                   1.2, '{}', json.dumps(['매출 성장 40%']), json.dumps({'IWO': 0.1})))
        db.execute('INSERT INTO runs VALUES (?,?,?,?)', ('2026-11-13', 'weekly', 'x', json.dumps({'passed': {'tenbagger': 1}})))
    return decided


def test_the_template_has_the_ideas_and_take_profit_sections_in_place():
    s = list(T.SECTIONS)
    assert len(s) == 12 and s.index('미국 종목 아이디어') == s.index('종목별 분석') + 1
    assert s.index('익절·리밸런싱 의견') == s.index('계좌 계획') + 1


def test_ideas_section_always_says_reference_only_and_names_only_checked_ideas(picked):
    facts = R.data(DAY.isoformat(), root=picked, reports=picked / 'reports', fetch=fake_fetch, context=False)
    assert facts['ideas']['passed'] == {'tenbagger': 1}
    sec = lambda md: md.split('## 5. 미국 종목 아이디어')[1].split('## 6.')[0]
    blank = sec(R.report_md(facts))
    assert '참고 정보 · 주문 없음' in blank and 'AAA' not in blank  # a screen name is not a report idea
    done = sec(R.report_md(facts, {**fill_doc(), 'ideas': [idea()]}))
    assert 'AAA' in done and '2분기 연속 매출 감소' in done and '[10-Q 2026-11-05](https://www.sec.gov/' in done and '매출 성장 40%' in done


def test_idea_cards_need_a_code_pick_falsifiers_a_redline_filings_and_no_prices():
    picks = {'AAA'}
    check = lambda card: R.json_problems({**good_json(), 'ideas': [card]}, L.CAPITAL, picks=picks)
    assert check(idea()) == []
    assert any('스크린' in p for p in check(idea(ticker='ZZZ')))
    assert any('반증' in p for p in check(idea(thesis={**idea()['thesis'], 'falsifiers': ['하나']})))
    assert any('공시' in p for p in check(idea(filings=[])))
    assert any('가격' in p for p in check(idea(thesis={**idea()['thesis'], 'core': '목표가 120달러까지 오른다.'})))
    assert any('ideas' in p for p in R.json_problems({k: v for k, v in good_json().items() if k != 'ideas'}, L.CAPITAL))


def test_take_profit_and_rebalancing_facts_show_gain_band_and_tax():
    rows = R.tp_rebalance({'NDX': (100, 20_000)}, {'NDX': 30_000, 'SPX': 10_000}, 10_000_000, {'NDX': 0.30, 'SPX': 0.30},
                          {'band': 0.05, 'monthly_sell_share': 0.10}, 200_000)
    ndx = rows['assets']['NDX']
    assert (ndx['gain'], ndx['weight'], ndx['band']) == (0.5, 0.3, [0.25, 0.35]) and not ndx['outside']
    assert ndx['tax_if_sold'] == round(1_000_000 * 0.154) and rows['sell_room'] == 800_000
    assert rows['assets']['SPX']['outside'] and rows['assets']['SPX']['weight'] == 0  # nothing held, target 30%


def test_fill_keeps_the_idea_cards_in_the_ideas_record(picked):
    import us_ideas as U
    day = DAY.isoformat()
    assert R.fill(day, {**fill_doc(), 'ideas': [idea()]}, root=picked, reports=picked / 'reports', fetch=fake_fetch) == []
    row = U.connect(picked).execute('SELECT * FROM theses').fetchone()
    assert (row['day'], row['ticker'], row['master']) == (day, 'AAA', 'tenbagger') and json.loads(row['card'])['verdict'] == '관찰'
    assert any('스크린' in p for p in R.fill(day, {**fill_doc(), 'ideas': [idea(ticker='ZZZ')]}, root=picked,
                                            reports=picked / 'reports', fetch=fake_fetch))
    U.connect(picked).execute('DELETE FROM theses').connection.commit()
    assert R.fill(day, {**fill_doc(), 'ideas': [idea()]}, root=picked, reports=picked / 'eval', fetch=fake_fetch) == []
    assert U.connect(picked).execute('SELECT COUNT(*) FROM theses').fetchone()[0] == 0  # an evaluation folder keeps out


def test_the_account_table_shows_the_watchlist_sleeve_inside_the_nav(decided):
    # user approval 2026-11-18 (docs/longterm-account-settings-plan.md 2-A): the sleeve is the 적립식's, not the engine's
    import longterm_watchlist as W
    from test_vibe_longterm import Read
    W.save(decided, {'005930': '삼성전자'})
    broker = L.broker_for(decided, Read())
    broker.call_tool('call_api', {'operationId': 'krstockOrderCashBuy', 'input': {'iem_cd': '005930', 'orr_qty': 30, 'orr_pr': 10_000}})
    L.put_state(L.connect(decided), nav_latest={'day': DAY.isoformat(), 'actual': 100_000_000, 'sleeve': 300_000, 'contributed': 0})
    facts = R.data(DAY.isoformat(), root=decided, reports=decided / 'reports', fetch=fake_fetch, context=False)
    assert facts['account']['sleeve'] == {'005930': [30, 10_000]}
    sec7 = R.report_md(facts).split('## 7. 계좌 계획')[1].split('## 8.')[0]
    row = next(line for line in sec7.splitlines() if '관심목록' in line and line.startswith('|'))
    assert '삼성전자' in row and '| 30 |' in row and '0.3%' in row
    assert '엔진이 리밸런싱·매도하지 않음' in sec7


# ---- 거장별 관점 의견 (user approval 2026-11-18, 보완안 3): each master's own principle, not only the engine module ----

def views(**over):
    out = {k: {'view': f'{k} 원칙으로 보면 지금은 조정 국면이고 금리차가 좁다.', 'action': '기다리기', 'vs_engine': '같음: 엔진도 단계 전이라 사지 않는다.'}
           for k in R.MASTER_VIEWS}
    return {**out, **over}


def test_every_master_has_a_principle_a_portfolio_and_a_source():
    assert set(R.MASTER_VIEWS) == {'bogle', 'dalio', 'buffett', 'marks', 'lynch', 'williams', 'yeo'}
    assert all(m['principle'] and m['portfolio'] and m['source'] for m in R.MASTER_VIEWS.values())
    assert '30/40/15/7.5/7.5' in R.MASTER_VIEWS['dalio']['portfolio'] and '90/10' in R.MASTER_VIEWS['buffett']['portfolio']
    assert '0261H0' in R.MASTER_VIEWS['yeo']['portfolio'] and '−30/−45/−55%' in R.MASTER_VIEWS['yeo']['portfolio']


def test_the_data_carries_the_principles_and_each_yeo_method_account(decided):
    L.save_settings(decided, {'books': {'live-2': {'strategy': 'yeo_method'}}})
    facts = R.data('2026-11-19', root=decided, reports=decided / 'reports', fetch=fake_fetch, context=False)
    assert set(facts['master_principles']) == set(R.MASTER_VIEWS)
    yeo = facts['yeo_accounts']['live-2']
    assert yeo['label'] == '실계좌 2' and yeo['next_step'] == -0.2 and yeo['dd'] is not None and '사지 않음' in yeo['next_action']


def test_section_three_shows_each_masters_view_and_the_check_requires_it_from_the_19th(decided):
    facts = R.data('2026-11-19', root=decided, reports=decided / 'reports', fetch=fake_fetch, context=False)
    text = R.report_md(facts, {**good_json(), 'master_views': views()})
    three = T.section_body(text, '거장별 시각')
    assert '### 거장별 관점 의견' in three and '참고 의견 · 주문 없음' in three
    assert all(f"#### {m['name']}" in three for m in R.MASTER_VIEWS.values()) and R.MASTER_VIEWS['dalio']['portfolio'] in three
    assert '기다리기' in three and '엔진·계좌와' in three
    nav = L.CAPITAL
    assert R.json_problems({**good_json()}, nav, day='2026-11-18') == []  # published before the section existed
    assert any('master_views' in p for p in R.json_problems(good_json(), nav, day='2026-11-19'))
    assert R.json_problems({**good_json(), 'master_views': views()}, nav, day='2026-11-19') == []
    bad = views(yeo={'view': '목표가 30,000원', 'action': '몰빵', 'vs_engine': ''})
    found = ' '.join(R.json_problems({**good_json(), 'master_views': bad}, nav, day='2026-11-19'))
    assert '가격' in found and 'action' in found and 'vs_engine' in found
