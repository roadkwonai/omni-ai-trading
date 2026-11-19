"""장기투자 → 미국 종목 아이디어 (S3 of docs/longterm-master-dca-ideas-plan.md, user approval 2026-11-18): the masters' US stock
picks, ten-bagger candidates, the follow-up scorecard and three masters' 13F. A picked name opens its own daily/weekly chart
(user request 2026-11-18: pick a row, or the '차트로 볼 종목' box). Read-only: no order, no setting, no ledger."""
from __future__ import annotations

import streamlit as st

import re

import status_cards as sc
import theme as THEME
import us_ideas as U
import vibe_longterm as L

FLOW = """digraph G { rankdir=LR; bgcolor="transparent"; node [shape=box style="rounded,filled" fillcolor="#1E293B" color="#334155"
fontcolor="#E2E8F0" fontname="Pretendard" fontsize=11]; edge [color="#94A3B8" fontcolor="#94A3B8" fontname="Pretendard" fontsize=10];
src [label="SEC 공시·N-PORT·13F\\n(분기, 셋째 금 07:00)"]; px [label="Yahoo 시세\\n(주간, 금 07:30)"];
screen [label="코드 스크린\\n버핏·린치·피셔·텐베거\\n자료 이상은 제외"]; picks [label="상위 후보\\n(이 탭 · 이름만, 주문 없음)"];
lens [label="렌즈·공시 확인\\n주간 리포트(금 16:50)\\n반대 렌즈 포함" fillcolor="#0E1223" color="#C4B5FD" fontcolor="#C4B5FD"];
card [label="논리 카드\\n리포트 5절 · 반증 3·레드라인"]; score [label="추천 성적표\\n1·3·6·12개월 vs QQQ·SPY" fillcolor="#0E1223" color="#93C5FD" fontcolor="#93C5FD"];
src -> screen; px -> screen; screen -> picks; picks -> lens; lens -> card [label="통과만"]; picks -> score [style=dashed]; card -> score [style=dashed];
}"""

def flow_spec():
    import guide_ui as GU
    return GU.workflow('아이디어가 리포트까지 가는 길', [('data', '자료'), ('code', '코드 스크린 · 이 탭'), ('report', '주간 리포트'), ('score', '성적')],
        [('src', 'data', 0, 'SEC 공시·N-PORT·13F', '분기 · 셋째 금 07:00', 'external'), ('px', 'data', 1, 'Yahoo 시세', '주간 · 금 07:30', 'external'),
         ('screen', 'code', 1, '코드 스크린', '버핏·린치·피셔·텐베거 · 자료 이상 제외', 'security'),
         ('picks', 'code', 2, '상위 후보', '이름만 · 주문 없음', 'frontend'),
         ('lens', 'report', 3, '렌즈·공시 확인', '금 16:50 · 반대 렌즈 포함', 'cloud'), ('card', 'report', 4, '논리 카드', '리포트 5절 · 반증 3·레드라인'),
         ('scorecard', 'score', 5, '추천 성적표', '1·3·6·12개월 vs QQQ·SPY', 'database')],
        [('src', 'screen', None, 'main'), ('px', 'screen'), ('screen', 'picks', None, 'main'), ('picks', 'lens', None, 'main'),
         ('lens', 'card', '통과만', 'main'), ('picks', 'scorecard', None, 'async'), ('card', 'scorecard', None, 'async')],
        main=['src', 'screen', 'picks', 'lens', 'card'])


KRX = {'379800': 'KODEX 미국S&P500', '379810': 'KODEX 미국나스닥100', '381180': 'TIGER 미국필라델피아반도체나스닥',
       '0023A0': 'SOL 미국양자컴퓨팅TOP10', '0183J0': 'TIGER 미국우주테크'}


def etf_text(etfs):
    """Which of our KRX ETFs hold the name (through the US ETF they resemble) and at what weight."""
    parts = [f"{KRX.get(U.ETFS[e][1], U.ETFS[e][0])} {p:.2f}%" if p is not None else KRX.get(U.ETFS[e][1], U.ETFS[e][0])
             for e, p in etfs.items() if e in U.ETFS]
    return ' · '.join(parts) or '—'


def rows(picks, master):
    return [{'순위': p['rank'], '종목': p['ticker'] + (' 🆕' if p.get('new') else ''), '회사': p['name'],
             '근거(코드 계산)': ' · '.join(p['reasons']), '우리 ETF 안 비중': etf_text(p['etfs']),
             '국내 상장 ETF로 노출': ' · '.join(L.ASSETS[k][1] for k in U.domestic_etfs(p)) or '없음',  # 적립식이 살 수 있는 ETF
             '추천일 종가($)': round(p['price'], 2), '종가 날짜': p['price_date']} for p in picks if p['master'] == master]


# 13F change badges (user request 2026-11-18): the app's colours, a sign as well as a colour (red up, blue down)
ACTIONS = {'new': ('NEW 신규', '#a855f7'), 'increased': ('▲ 증가', '#ef4444'), 'reduced': ('▼ 감소', '#3b82f6'), 'closed': ('청산', '#94a3b8')}
PLAIN_CLASS = {'COM', 'COM NEW', 'COMMON', 'COMMON STOCK', 'SHS', 'ORD', 'ORD SHS'}
OPTION = {'Put': '풋 옵션', 'Call': '콜 옵션'}
SORTS = {'change': '변화 순', 'value': '보유 가치 순', 'weight': '비중 순'}

NONE = '선택 안 함'
MASTER_LABEL = {k: v.split(' · ')[0] for k, v in U.MASTERS.items()}


def show(table, empty, key=None):
    """A table; with `key`, picking a row opens that name's chart (the chart box above follows the pick)."""
    if not table:
        st.caption(empty)
        return
    if key is None:
        st.dataframe(table, hide_index=True, width='stretch')
        return

    def picked():
        rows = st.session_state[key].selection.rows
        if rows:  # open the chart on this name: its master's list, no search left over
            st.session_state['idea_chart_group'] = key.removeprefix('idea_rows_')
            st.session_state['idea_chart_query'] = ''
            st.session_state['idea_chart_ticker'] = table[rows[0]]['종목'].replace(' 🆕', '')
    st.dataframe(table, hide_index=True, width='stretch', key=key, on_select=picked, selection_mode='single-row')


MA_CHOICES = (3, 5, 20, 50, 60, 120, 200)
MA_DEFAULT = [5, 20, 60, 120]


def change_rows(m, universe, sort='change'):
    """One master's 13F changes as rows: a badge, the name with its share class / ETF product (from the positions, by CUSIP)
    or its CUSIP when two lines share an issuer, options marked, the size of the change, the value and weight after, and
    the ticker when the universe knows the company. Ordered new → increased (largest) → reduced (largest cut) → closed."""
    from collections import Counter
    pos = {(p.get('cusip'), p.get('put_call')): p for p in m.get('positions') or []}
    changes = m.get('changes') or []
    count = Counter(c.get('issuer') for c in changes)
    by_name = {U.norm(n): t for t, n in universe.items()}
    rows = []
    for c in changes:
        p = pos.get((c.get('cusip'), c.get('put_call'))) or {}
        cls, issuer = (p.get('title_of_class') or '').strip(), c.get('issuer') or c.get('cusip') or '—'
        name = f'{issuer} · {cls}' if cls and cls.upper() not in PLAIN_CLASS else f"{issuer} · CUSIP {c.get('cusip')}" if count[issuer] > 1 else issuer
        if c.get('put_call'):
            name += f" · {OPTION.get(c['put_call'], c['put_call'])}"
        action = c.get('action')
        pct = None if action == 'new' else c.get('shares_change_pct')
        rows.append({'action': action, '변화': ACTIONS.get(action, (action,))[0], '종목': name,
                     '주식 수 변화(%)': pct, '▲ 증가 크기': min(pct, 100.0) if pct and pct > 0 else None,
                     '▼ 감소 크기': min(-pct, 100.0) if pct and pct < 0 else None,
                     '보유 가치(달러)': c.get('value_usd_after'), '가치 변화(달러)': c.get('value_usd_change'), '비중(%)': p.get('weight_pct'),
                     'CUSIP': c.get('cusip'), '티커': '' if c.get('put_call') else by_name.get(U.norm(issuer), '')})
    order = list(ACTIONS)
    within = {'new': lambda r: -(r['보유 가치(달러)'] or 0), 'increased': lambda r: -(r['주식 수 변화(%)'] or 0),
              'reduced': lambda r: r['주식 수 변화(%)'] or 0, 'closed': lambda r: r['가치 변화(달러)'] or 0}
    keys = {'change': lambda r: (order.index(r['action']) if r['action'] in order else 9, within.get(r['action'], lambda r: 0)(r)),
            'value': lambda r: -(r['보유 가치(달러)'] or 0), 'weight': lambda r: -(r['비중(%)'] if r['비중(%)'] is not None else -1)}
    return sorted(rows, key=keys.get(sort, keys['change']))


NUMERIC = ('주식 수 변화(%)', '▲ 증가 크기', '▼ 감소 크기', '보유 가치(달러)', '가치 변화(달러)', '비중(%)')


def change_frame(rows):
    """change_rows as a table whose number columns are numbers (a missing value is a blank, never the word None)."""
    import pandas as pd
    frame = pd.DataFrame(rows)
    for col in NUMERIC:
        frame[col] = pd.to_numeric(frame[col], errors='coerce').astype(float)
    # the table shows text: Streamlit draws an empty number cell as a grey 'None', whatever the styler says
    frame['주식 수 변화'] = [('—' if pd.isna(v) else f'{v:+.1f}%'.replace('-', '−')) for v in frame['주식 수 변화(%)']]
    frame['비중'] = ['—' if pd.isna(v) else f'{v:.2f}%' for v in frame['비중(%)']]  # not in the stored positions: a dash
    return frame


def change_styler(frame, pick='all'):
    """Colours of the 13F table: the badge in its action colour, the change in red/blue, a dash for a new line, closed
    names struck through when the closed filter is on."""
    colour = {label: c for label, c in ACTIONS.values()}
    sign = lambda v: f"color: {'#ef4444' if v.startswith('+') else '#3b82f6' if v.startswith('−') else 'inherit'}; font-weight: 600"  # noqa: E731
    styled = (frame.style.map(lambda v: f'color: {colour.get(v, "inherit")}; font-weight: 700', subset=['변화'])
              .map(sign, subset=['주식 수 변화']))
    if pick == 'closed':
        styled = styled.map(lambda v: 'text-decoration: line-through; color: #94a3b8', subset=['종목'])
    return styled


def thirteen_f(m, universe, groups_):
    """One master's 13F changes: filter chips with counts, a sort, a coloured table; a row opens that name above."""
    key = m['manager']
    rows = change_rows(m, universe)
    counts = {a: sum(1 for r in rows if r['action'] == a) for a in ACTIONS}
    st.markdown(f"**{m['master']} · {m['firm']}** ({m.get('quarter', '—')}) — 신고 가치 {(m.get('portfolio_value_usd') or 0) / 1e9:,.0f}B 달러 · "
                f"보유 {len(m.get('positions') or [])}건")
    if not rows:
        st.caption('이 분기 변화 없음')
        return
    left, right = st.columns([3, 1], vertical_alignment='bottom')
    st.session_state.setdefault(f'13f_filter_{key}', 'all')
    pick = left.pills('변화', ['all', *ACTIONS], key=f'13f_filter_{key}', label_visibility='collapsed',
                      format_func=lambda a: f"전체 {len(rows)}" if a == 'all' else f"{ACTIONS[a][0]} {counts[a]}") or 'all'
    sort = right.selectbox('정렬', list(SORTS), format_func=SORTS.get, key=f'13f_sort_{key}', label_visibility='collapsed')
    table = [r for r in change_rows(m, universe, sort) if pick == 'all' or r['action'] == pick]
    if not table:
        st.caption('이 변화에 해당하는 종목이 없습니다.')
        return
    styled = change_styler(change_frame(table), pick)

    def picked():
        sel = st.session_state[f'13f_rows_{key}'].selection.rows
        if not sel:
            return
        row = table[sel[0]]
        group = f'13f:{key}'
        if row['티커'] and row['티커'] in groups_.get(group, ('', []))[1]:
            st.session_state.update({'idea_chart_group': group, 'idea_chart_query': '', 'idea_chart_ticker': row['티커'], '13f_note': ''})
        else:
            st.session_state['13f_note'] = f"{row['종목']}: 유니버스 밖(해외 상장·ETF·옵션 등)이라 차트·상세가 없습니다."
    st.dataframe(styled, hide_index=True, width='stretch', key=f'13f_rows_{key}', on_select=picked, selection_mode='single-row',
                 column_order=['변화', '종목', '주식 수 변화', '▲ 증가 크기', '▼ 감소 크기', '보유 가치(달러)', '가치 변화(달러)', '비중',
                               '티커', 'CUSIP'],
                 column_config={'▲ 증가 크기': st.column_config.ProgressColumn(min_value=0, max_value=100, format='%.0f%%', color='#ef4444',
                                                                           help='주식 수가 늘어난 정도(100% 이상은 가득 참)'),
                                '▼ 감소 크기': st.column_config.ProgressColumn(min_value=0, max_value=100, format='%.0f%%', color='#3b82f6',
                                                                           help='주식 수가 줄어든 정도(청산은 100%)'),
                                '보유 가치(달러)': st.column_config.NumberColumn(format='compact'),
                                '가치 변화(달러)': st.column_config.NumberColumn(format='compact')})


def groups(data, universe):
    """{group key: (label, [tickers])}: every name, each master's picks, each master's 13F holdings found in the universe
    by company name (13F lists issuers, not tickers; a name outside the universe cannot be charted)."""
    by_name = {U.norm(name): t for t, name in universe.items()}
    picks = {m: [p['ticker'] for p in data['picks'] if p['master'] == m] for m in U.MASTERS}
    out = {}
    for m in data.get('managers') or []:
        lines = (m.get('positions') or []) + [c for c in m.get('changes') or [] if not c.get('put_call')]  # a sold name opens too
        held = [by_name.get(U.norm(p.get('issuer'))) for p in lines]
        out[f"13f:{m['manager']}"] = (f"{m['master']} 13F · {m['firm']}", list(dict.fromkeys(t for t in held if t)))
    every = [t for m in U.MASTERS for t in picks[m]] + [t for _, held in out.values() for t in held]
    return {'all': ('전체', list(dict.fromkeys(every))),
            **{m: (U.MASTERS[m].split(' · ')[0], picks[m]) for m in U.MASTERS}, **out}


def chart_box(data, chart, universe=None):
    """The chart of one name: pick a master (or its 13F) and search by ticker or company name; daily or weekly candles with
    the chosen moving averages and volume in its own pane, the pick day and its close (reference only)."""
    import vibe_longterm_view as V
    picks = data['picks']
    universe = universe or {}
    names = {**universe, **{p['ticker']: p['name'] for p in picks}}
    found = groups(data, universe)
    g, q, t = st.columns([1.2, 1, 2], vertical_alignment='bottom')
    group = g.selectbox('거장', list(found), format_func=lambda k: f"{found[k][0]} ({len(found[k][1])})", key='idea_chart_group')
    query = (q.text_input('검색', key='idea_chart_query', placeholder='티커·회사명') or '').strip().lower()
    tickers = [x for x in found[group][1] if not query or query in x.lower() or query in names.get(x, '').lower()]
    if st.session_state.get('idea_chart_ticker', NONE) not in [NONE] + tickers:
        st.session_state['idea_chart_ticker'] = NONE  # the last pick is not in this list any more
    ticker = t.selectbox(f'차트로 볼 종목 · {len(tickers)}개 (표의 행을 골라도 열립니다)', [NONE] + tickers, key='idea_chart_ticker',
                         format_func=lambda x: x if x == NONE else f"{x} · {names.get(x, x)}")
    u, m = st.columns([1, 3], vertical_alignment='bottom')
    unit = u.segmented_control('기간', ['일', '주'], default='일', key='idea_chart_unit') or '일'
    periods = sorted(m.multiselect('이동평균', list(MA_CHOICES), default=MA_DEFAULT, key='idea_chart_ma',
                                   format_func=lambda n: f"{n}{'일' if unit == '일' else '주'}"))
    if ticker == NONE:
        return None, []
    bars = daily = V.yahoo_bars(ticker)
    if not bars:
        st.warning(f'{ticker} 일봉을 불러오지 못했습니다(Yahoo). 잠시 뒤 다시 열어 주세요.', icon=':material/cloud_off:')
        return ticker, []
    if unit == '주':
        bars = V.weekly(bars)
    mine = [p for p in picks if p['ticker'] == ticker]
    from datetime import date, datetime
    bucket = (lambda d: d.isocalendar()[:2]) if unit == '주' else (lambda d: d)  # noqa: E731
    bar_of = {bucket(datetime.fromtimestamp(b['time']).date()): b['time'] for b in bars}
    markers = [{'time': bar_of[k], 'position': 'belowBar', 'color': '#a78bfa', 'shape': 'circle', 'text': f"추천 · {U.MASTERS[p['master']].split(' · ')[0]}"}
               for p in mine if (k := bucket(date.fromisoformat(p['price_date']))) in bar_of]
    levels = [{'price': mine[0]['price'], 'color': '#a78bfa', 'style': 2, 'label': f"추천일 종가 {mine[0]['price_date']}"}] if mine else []
    high = max(b['high'] for b in bars[-(52 if unit == '주' else 252):])
    levels.append({'price': high, 'color': '#94a3b8', 'style': 1, 'label': '52주 고점'})
    if chart is None:
        st.caption('차트 컴포넌트가 없어 차트를 그릴 수 없습니다.')
    else:
        chart(bars, markers, None, averages={n: V.moving(bars, n) for n in periods}, intraday=False, volume_pane=True,
              visible=260 if unit == '일' else 104, levels=levels, alt=f'{ticker} 일봉')
    reasons = ' / '.join(f"{U.MASTERS[p['master']].split(' · ')[0]}: {' · '.join(p['reasons'])}" for p in mine) or '코드 스크린 추천 아님(13F 보유)'
    ma = '·'.join(map(str, periods)) + ('일' if unit == '일' else '주') if periods else '없음'
    st.caption(f"{ticker} · {names.get(ticker, ticker)} · Yahoo {'일봉' if unit == '일' else '주봉'}(달러, 수정주가) · 이동평균 {ma} · "
               f"아래 영역 = 거래량(빨강 상승 · 파랑 하락) · 보라 ● 추천일 · 코드 근거 — {reasons} · 참고 정보 · 주문 없음")
    return ticker, daily


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def sec_filings(cik):
    """The company's SEC industry (SIC) and its latest 10-K/10-Q/8-K with links (one submissions call, cached six hours)."""
    from backtest.loaders import sec_edgar_client as S
    sub = S.get_submissions(cik)
    recent = (sub.get('filings') or {}).get('recent') or {}
    rows = zip(recent.get('form') or [], recent.get('filingDate') or [], recent.get('accessionNumber') or [], recent.get('primaryDocument') or [])
    filings = [{'form': f, 'date': d, 'url': f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{a.replace('-', '')}/{doc}"}
               for f, d, a, doc in rows if f in ('10-K', '10-Q', '8-K')][:8]
    return {'sic': sub.get('sicDescription'), 'filings': filings}


@st.cache_data(ttl=1800, show_spinner=False)
def stock_news(ticker, name=''):
    """Recent headlines on the name (Yahoo search, the source vibe-trading's get_stock_news uses), cached 30 minutes. Yahoo tags
    big names on most market stories: keep an article whose title names the company or the ticker, or whose first tag (its
    subject) is the ticker."""
    from datetime import datetime, timezone
    from backtest.loaders import yahoo_client
    word = next((w for w in re.split(r'[^A-Za-z0-9&]+', name or '') if len(w) >= 3), '').lower()
    out = []
    for item in yahoo_client.search_news(ticker, 20):
        related = item.get('relatedTickers') or []
        title = (item.get('title') or '').lower()
        named = bool(word and re.search(rf'\b{re.escape(word)}\b', title)) or bool(re.search(rf'\b{re.escape(ticker.lower())}\b', title))
        if not named and related[:1] != [ticker]:
            continue
        when = item.get('providerPublishTime')
        out.append({'title': item.get('title') or '', 'url': item.get('link'), 'source': item.get('publisher') or '',
                    'published': datetime.fromtimestamp(when, timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M') if when else ''})
    return out[:10]


def clean_text(text):
    """An outside headline shown as plain text: no markdown links or line breaks of its own (data, not instructions)."""
    return ' '.join(str(text or '').replace('[', '(').replace(']', ')').split())


def info_rows(d, closes, price_day):
    """The screen's facts on the name with their basis and source; the price-based ones are marked as approximations."""
    m = U.metrics(d['facts'], closes[-1] if closes else None, closes or None)
    sec = f"SEC 공시(XBRL) · {d['facts_day'] or '수집 전'} 수집"
    px = f'Yahoo 종가 {price_day}' if price_day else 'Yahoo 시세 없음'
    val = lambda x, f: '—' if x is None else f(x)  # noqa: E731
    return [
        {'지표': '시가총액', '값': val(m.get('mcap'), lambda x: f'{x / 1e8:,.0f}억 달러'), '기준': '근사 · 종가 × 최근 공시 희석주식수', '출처': f'{px} · SEC'},
        {'지표': 'PER', '값': val(m.get('pe'), lambda x: f'{x:.1f}'), '기준': '근사 · 시가총액 ÷ 순이익(TTM)', '출처': f'{px} · SEC'},
        {'지표': 'PEG', '값': val(m.get('peg'), lambda x: f'{x:.2f}'), '기준': '근사 · PER ÷ 순이익 성장률(총액 기준)', '출처': f'{px} · SEC'},
        {'지표': 'FCF 수익률', '값': val(m.get('fcf_yield'), lambda x: f'{x * 100:.1f}%'), '기준': '근사 · 잉여현금흐름(최근 회계연도) ÷ 시가총액', '출처': f'{px} · SEC'},
        {'지표': '매출 성장(1년)', '값': val(m['rev_growth'], U.pct), '기준': 'TTM', '출처': sec},
        {'지표': '매출 3년 연평균', '값': val(m['rev_cagr3'], U.pct), '기준': 'TTM', '출처': sec},
        {'지표': '순이익 성장(1년)', '값': val(m['ni_growth'], U.pct), '기준': 'TTM · 총액', '출처': sec},
        {'지표': '총이익률', '값': val(m['gm'][-1], U.pct), '기준': 'TTM', '출처': sec},
        {'지표': '영업이익률', '값': val(m['om'][-1], U.pct), '기준': 'TTM', '출처': sec},
        {'지표': 'ROE', '값': val(m['roe'][-1], U.pct), '기준': 'TTM 순이익 ÷ 자본', '출처': sec},
        {'지표': '부채/자본', '값': val(m['de'], lambda x: f'{x:.2f}'), '기준': '최근 공시', '출처': sec},
        {'지표': '희석률(1년)', '값': '미확인(분할 추정)' if m['dilution'] is None else U.pct(m['dilution']), '기준': '희석주식수 변화', '출처': sec},
        {'지표': '가격 위치', '값': U.timing(m) or '—', '기준': '200일선 · 52주 범위', '출처': px},
    ], m


def detail(root, ticker, data, daily):
    """종목 정보 · 뉴스 · 거장 리포트 of one picked name (user request 2026-11-18). Reference only: no order, no price target."""
    from datetime import datetime
    from pathlib import Path
    import longterm_template as T
    d = U.stock_detail(root, ticker)
    closes = [b['close'] for b in daily]
    price_day = datetime.fromtimestamp(daily[-1]['time']).date().isoformat() if daily else None
    rows, m = info_rows(d, closes, price_day)
    st.markdown(f"#### {ticker} · {d['name']}")
    tabs = st.tabs(['종목 정보', '뉴스', '거장 리포트'])
    with tabs[0]:
        try:
            sec = sec_filings(d['cik']) if d['cik'] else {'sic': None, 'filings': []}
        except Exception:
            sec = None
        st.markdown(f"업종(SEC SIC): **{(sec or {}).get('sic') or '—'}** · 우리 ETF 안 비중: {etf_text(d['etfs'])}")
        st.dataframe(rows, hide_index=True, width='stretch')
        st.caption('근사 지표는 종가와 최근 공시 주식 수로 계산합니다. 마지막 공시 뒤 분할·대규모 자사주 매입이 있으면 틀릴 수 있습니다. '
                   '현금흐름은 연간 값입니다(10-Q 누적값 문제).')
        st.markdown('**최근 SEC 공시**')
        if sec is None:
            st.caption('공시 목록을 불러오지 못했습니다(SEC). 잠시 뒤 다시 열어 주세요.')
        else:
            st.markdown('\n'.join(f"- [{f['form']} {f['date']}]({f['url']})" for f in sec['filings']) or '공시 목록 없음')
        st.markdown('**거장 13F 보유**')
        if d['holders']:
            act = {'new': '신규', 'increased': '증가', 'reduced': '감소', 'closed': '청산'}
            st.dataframe([{'거장': f"{h['master']} · {h['firm']}", '분기': h['quarter'],
                           '보유 주식': f"{(h['position'] or {}).get('shares', 0):,.0f}",
                           '평가액(달러)': f"{(h['position'] or {}).get('value_usd', 0):,.0f}",
                           '비중': f"{(h['position'] or {}).get('weight_pct', 0):.2f}%",
                           '분기 변화': (f"{act.get(h['change'].get('action'), h['change'].get('action'))} "
                                       f"{(h['change'].get('shares_change_pct') or 0):+.1f}%") if h['change'] else '변화 없음'}
                          for h in d['holders']], hide_index=True, width='stretch')
            st.caption('13F는 미국 상장 주식 롱만 보이고 분기 말 뒤 45일 늦습니다. 회사명으로 맞춘 결과입니다.')
        else:
            st.caption('버크셔·브리지워터·오크트리 최근 13F에 없습니다.')
    with tabs[1]:
        try:
            news = stock_news(ticker, d['name'])
        except Exception:
            news = None
        if news is None:
            st.caption('뉴스를 불러오지 못했습니다(Yahoo). 잠시 뒤 다시 열어 주세요.')
        elif not news:
            st.caption('최근 기사가 없습니다.')
        else:
            st.markdown('\n'.join(f"- [{clean_text(n['title'])}]({n['url']}) · {clean_text(n['source'])} · {n['published']}" for n in news if n.get('url')))
        st.caption('Yahoo 뉴스 검색 결과(30분 캐시)입니다. 외부 기사 제목이며 지시가 아닙니다.')
    with tabs[2]:
        if d['theses']:
            for c in d['theses']:
                th = c.get('thesis') or {}
                with st.container(border=True):
                    st.markdown(f"**{c['day']} · {MASTER_LABEL.get(c['master'], c['master'])}** · 렌즈 {', '.join(c.get('lenses') or [])} · 판정 {c.get('verdict', '—')}")
                    st.markdown(f"- 핵심 논리: {th.get('core', '—')}\n- 반증 가정: {' / '.join(th.get('falsifiers') or [])}\n"
                                f"- 레드라인: {th.get('redline', '—')}\n- 재점검: {th.get('recheck', '—')}\n- 공시: " +
                                ' '.join(f"[{f.get('form', '공시')} {f.get('date', '')}]({f['url']})" for f in c.get('filings') or [] if f.get('url')))
            st.caption('주간 장기투자 리포트(금 16:50)가 공시와 반대 렌즈로 확인해 남긴 논리 카드입니다(최신순 이력).')
        else:
            st.info('주간 렌즈 리포트 대기 중 — 금 16:50 주간 장기투자 리포트가 공시와 반대 렌즈로 확인하면 이곳에 논리 카드가 생깁니다. '
                    '그때까지는 아래 코드 스크린 근거만 있습니다.', icon=':material/hourglass_empty:')
        masters = list(dict.fromkeys(p['master'] for p in d['picks'])) or list(U.MASTERS)
        odd = U.suspect(m)
        for master in masters:
            st.markdown(f"**{U.MASTERS[master]} 스크린** · " + ('이번 주 상위 후보' if master in {p['master'] for p in d['picks']} else '추천 후보 아님'))
            st.dataframe([{'기준': c, '값': v, '통과': '✅ 통과' if ok else '❌ 미달'} for c, v, ok in U.criteria(master, m, d['etfs'])],
                         hide_index=True, width='stretch')
        if odd:
            st.caption(f'자료 이상({odd}): 스크린에서 빠지는 값입니다. 주식 수·단위·업종을 확인해야 합니다.')
        found = []
        for md in sorted(Path(root, 'reports').glob('strategy-*.md'), reverse=True):
            key = md.stem.removeprefix('strategy-')
            try:
                if T.kind_of(key) in ('week', 'month') and re.search(rf'\b{re.escape(ticker)}\b', text := md.read_text(encoding='utf-8')):
                    found.append((key, T.section_body(text, '미국 종목 아이디어') or ''))
            except (OSError, ValueError):
                continue
        st.markdown('**이 종목이 나온 장기투자 리포트**')
        if not found:
            st.caption('아직 주간·월간 리포트에 나온 적이 없습니다.')
        for key, body in found[:6]:
            with st.expander(f"{key} · 장기투자 {'주간' if T.kind_of(key) == 'week' else '월간'} 리포트 · 5절 미국 종목 아이디어"):
                st.markdown(body)
    st.caption('참고 정보 · 주문 없음. 가격 목표나 매수 금액은 다루지 않습니다. 숫자는 코드 스크린 값이고, 글은 공시를 확인한 주간 리포트의 것입니다.')


def render(root, chart=None):
    st.caption('**참고 정보 · 주문 없음.** 장기 엔진은 KRX 상장 ETF 6종만 운용합니다. 숫자는 SEC 공시·Yahoo 시세로 코드가 계산했고, '
               '선별 기준은 가설이라 추천 성적표로 검증합니다. 투자 권유가 아닙니다. 설계 docs/longterm-master-dca-ideas-plan.md 4장')
    data = U.status(root)
    if not data.get('ready'):
        st.info('아직 주간 스크린 결과가 없습니다. 분기 유니버스 갱신(`us_ideas.py --universe`) 뒤 주간 실행(`--weekly`)이 만듭니다.',
                icon=':material/hourglass_empty:')
        return
    runs = data.get('runs') or {}
    uni, week = runs.get('universe') or {}, runs.get('weekly') or {}
    passed = week.get('passed') or {}
    sc.row([{'label': '주간 스크린', 'value': data['day'], 'icon': 'filter_alt', 'tone': 'off',
             'sub': f"가격 확인 {week.get('prefiltered', '—')}종목 · 자료 이상 제외 {week.get('suspect', 0)}"},
            {'label': '유니버스', 'value': f"{uni.get('tickers', '—')}종목", 'icon': 'public', 'tone': 'off', 'sub': f"{uni.get('day', '—')} 갱신(분기)"}])
    sc.row([{'label': U.MASTERS[k].split(' · ')[0], 'value': f'{passed.get(k, 0)}개 통과', 'icon': 'person', 'tone': 'off',
             'sub': f"상위 {min(passed.get(k, 0), U.TOP)}개 표시 · 새로 든 것 {sum(1 for p in data['picks'] if p['master'] == k and p.get('new'))}"}
            for k in U.MASTERS])  # two rows: six cards in one row broke the date into one character per line
    with st.expander('아이디어가 리포트까지 가는 길', icon=':material/account_tree:'):
        import guide_ui as GU
        GU.flow('us-ideas', flow_spec(), FLOW)
        st.caption('이 탭의 이름은 코드 스크린 결과일 뿐입니다. 리포트에는 주간 리포트가 공시와 반대 렌즈로 확인한 종목만 논리 카드로 실립니다.')
    universe = U.universe_names(root)
    ticker, daily = chart_box(data, chart, universe)
    if ticker:
        detail(root, ticker, data, daily)
    tabs = st.tabs(['거장 추천', '텐베거 후보', '추천 성적표', '거장 13F'])
    with tabs[0]:
        st.caption('달리오·보글·여운봉은 개별주를 추천하지 않습니다(지수·배분 관점). 막스는 종목 대신 시장 온도로, 윌리엄스는 타이밍 표시로 참여합니다.')
        for master in ('buffett', 'lynch', 'fisher'):
            st.markdown(f"**{U.MASTERS[master]}**")
            show(rows(data['picks'], master), '이번 주 통과 종목 없음', key=f'idea_rows_{master}')
    with tabs[1]:
        st.caption('10배가 될 종목이 아니라, 10배가 되려면 필요한 조건(작은 시가총액·빠른 매출 성장·높은 총이익률·낮은 희석·현금흐름)을 지금 갖춘 종목입니다. '
                   '그런 종목이 실제로 10배가 되는 비율은 낮습니다.')
        show(rows(data['picks'], 'tenbagger'), '이번 주 통과 종목 없음', key='idea_rows_tenbagger')
    with tabs[2]:
        score = data.get('scorecard') or []
        if not score:
            st.caption('첫 추천 뒤 1개월이 지나면 성적이 쌓이기 시작합니다.')
        else:
            st.dataframe([{'거장': U.MASTERS.get(r['master'], r['master']), '기간': f"{r['months']}개월", '건수': r['n'],
                           '평균 수익': f"{r['ret'] * 100:+.1f}%", 'QQQ': f"{(r['qqq'] or 0) * 100:+.1f}%",
                           'SPY': f"{(r['spy'] or 0) * 100:+.1f}%", 'QQQ를 이긴 비율': f"{(r['beat'] or 0) * 100:.0f}%"} for r in score],
                         hide_index=True, width='stretch')
    with tabs[3]:
        st.caption('13F는 미국 상장 주식 롱 포지션(옵션 포함)만 보이고 분기 말 뒤 45일 늦습니다. 그 거장의 전체 관점이 아닙니다. '
                   '빨강 ▲ 증가 · 파랑 ▼ 감소 · 보라 NEW 신규 · 회색 청산. 행을 고르면 위에서 그 종목 차트·상세가 열립니다.')
        if note := st.session_state.get('13f_note'):
            st.info(note, icon=':material/info:')
        found = groups(data, universe)
        for m in data.get('managers') or []:
            thirteen_f(m, universe, found)
