"""매매일지 tab (user 2026-11-13): every trading day's record (scripts/trading_journal.py) by day, month, year or any
range. A day reads at a glance — cards for every book and for the market, flows, issues, system and retrospective
(each opens its details in a pop-up), the day's flow diagram, the realized-P&L curve and the top issues — and the
details sit in lazy tabs below (only the open tab renders)."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

from report_figures import for_streamlit  # report callouts/highlights in st.markdown

import dialogs
import journal_template as T
import status_cards as sc
import theme as THEME
import trading_journal as tj
from guides import BUY, GUARD, INFO, SELL, WAIT, _dot, _node
from trading_journal import _pct as pct, _won as won

KST = ZoneInfo('Asia/Seoul')
VIEW_LABELS = {'summary': '한 줄 요약', 'market': '시황·수급', 'global': '해외 증시·원자재·코인', 'issues': '이슈(전쟁·정치·지정학)',
               'earnings': '실적·혁신 발표', 'analysts': '애널리스트', 'stocks': '종목'}
BOOK_ICONS = {'paper': 'science', 'vibe': 'auto_awesome', 'longterm': 'savings'}  # live books: account_balance
GOTO = 'journal_goto'  # a day to open on the next run (set by a card, a table row or a period's best/worst day)


def _win_rate(p):
    return f"{p['win_rate']}%" if p.get('win_rate') is not None else '—'


def detail(card, title, fn, *args):
    """A KPI item whose button opens fn(*args) in a pop-up."""
    return card, f'자세히 · {title}', dialogs.open_dialog, (title, fn, *args)


def kpi_row(items, key):
    """One row of status cards with their buttons right under them (detail() / jump() items)."""
    sc.row([card for card, *_ in items])
    dialogs.buttons([(label, f'{key}_{i}', callback, args) for i, (_, label, callback, args) in enumerate(items)])


def goto(day):
    st.session_state[GOTO] = day
    st.rerun()


def jump(card, day):
    """A KPI item whose button opens that day's journal."""
    return card, f'{day} 일지 열기', goto, (day,)


def day_dot(record):
    """The day at a glance: market -> issues -> the engines' routes -> result -> retrospective -> tomorrow."""
    m = record.get('market') or {}
    mk = ' · '.join(f"{n} {m[n]['change_pct']:+.1f}%" for n in ('코스피', '나스닥', '원/달러') if n in m) or '시장 자료 없음'
    issues = [k for k, v in (record.get('issues') or {}).items() if v][:5]
    paper = (record.get('books') or {}).get('paper', {}).get('performance', {})
    routes = (record.get('system') or {}).get('by_entry_route') or {}
    route_text = '\\n'.join(f"{r} {v['n']}건 {v['realized_krw']:+,}원" for r, v in list(routes.items())[:4]) or '진입 없음'
    view = record.get('perspective') or {}
    lessons = len(view.get('retrospective') or []) + len(view.get('strategy_improvements') or []) + len(view.get('system_improvements') or [])
    notes = len(view.get('engine_notes') or [])
    body = ' '.join([
        _node('mkt', f'시장\\n{mk}', GUARD),
        _node('iss', '이슈\\n' + ('\\n'.join(issues) if issues else '수집 전'), INFO),
        _node('route', f'진입 경로 (모의)\\n{route_text}', BUY),
        _node('res', f"결과\\n실현 {won(paper.get('realized_krw'))}\\n승률 {paper.get('win_rate') if paper.get('win_rate') is not None else '—'}%",
              SELL),
        _node('retro', f"회고·보완\\n{lessons}건" if view else '회고\\n작성 전 (16:40 예약 작업)', WAIT),
        _node('next', f"다음 날 엔진 참고\\n메모 {notes}건", INFO),
        'mkt -> iss; iss -> route; route -> res; res -> retro; retro -> next;'])
    return _dot(body, 'LR')


def curve_chart(books):
    """Cumulative realized P&L through the day, one line per book: books = [(label, fills)]."""
    rows = []
    for label, fills in books:
        run = 0
        for f in sorted((f for f in fills if f['side'] == 'SELL' and f.get('pnl') is not None), key=lambda f: f['time']):
            run += f['pnl']
            rows.append({'시각': pd.to_datetime(f['time']), '누적 실현손익': run, '종목': f['symbol'], '이번 매도': f['pnl'], '계좌': label})
    if not rows:
        return None
    line = alt.Chart(pd.DataFrame(rows)).mark_line(point=True, interpolate='step-after').encode(
        x=alt.X('시각:T', title=None, axis=alt.Axis(format='%H:%M')), y=alt.Y('누적 실현손익:Q', title='누적 실현손익 (원)'),
        color=alt.Color('계좌:N', legend=alt.Legend(orient='top', title=None)),
        tooltip=[alt.Tooltip('시각:T', format='%H:%M'), '계좌:N', '종목:N', alt.Tooltip('이번 매도:Q', format='+,'),
                 alt.Tooltip('누적 실현손익:Q', format='+,')])
    zero = alt.Chart(pd.DataFrame({'y': [0]})).mark_rule(strokeDash=[4, 4], color='#64748b').encode(y='y:Q')
    return (line + zero).properties(height=240)


@st.cache_data(ttl=3600, show_spinner=False)
def live_chart_data(day, code):
    """chart_data() for a day whose stored record has no charts (built before 2026-11-13 evening)."""
    return tj.chart_data(day, code)


def bar_frame(day, data):
    frame = pd.DataFrame(data.get('bars') or [], columns=['t', 'o', 'h', 'l', 'c', 'v'])
    if frame.empty:
        return frame
    frame['time'] = pd.to_datetime(day + ' ' + frame['t'])
    typical = (frame['h'] + frame['l'] + frame['c']) / 3
    volume = frame['v'].clip(lower=0)
    frame['vwap'] = (typical * volume).cumsum() / volume.cumsum().where(volume.cumsum() > 0)
    return frame


def fill_frame(fills):
    rows = []
    for book, f in fills:
        t = pd.to_datetime(f['time'])
        t = (t.tz_convert('Asia/Seoul').tz_localize(None) if t.tzinfo else t).floor('min')
        buy = f['side'] == 'BUY'
        rows.append({'time': t, 'price': float(f['price']), '구분': '매수' if buy else '매도', '계좌': book, '수량': f['qty'],
                     '실현': f.get('pnl'), '수익률': f.get('return_pct'), '경로': f.get('path') or '',
                     'label': f"{'매수' if buy else '매도'} {f['qty']}" + (f" {f['pnl']:+,.0f}" if f.get('pnl') is not None else '')})
    return pd.DataFrame(rows)


def trade_chart(day, code, data, trades, market, fills=(), market_bars=None):
    """Candles, VWAP, the previous close and every buy (▲) / sell (▼) of the day, volume below, the market beside."""
    frame = bar_frame(day, data)
    if frame.empty:
        st.caption(f'{code}: 이날 분봉을 찾지 못했습니다(Yahoo 1분봉 7일·5분봉 60일·60분봉 2년 보관).')
        return
    width = {'1m': 1.6, '5m': 5, '60m': 24}.get(data.get('interval'), 2)
    x = alt.X('time:T', axis=alt.Axis(format='%H:%M', title=None))
    color = alt.condition('datum.c >= datum.o', alt.value(BUY), alt.value(SELL))  # Korean convention: up red, down blue
    base = alt.Chart(frame).encode(x=x)
    tip = [alt.Tooltip('time:T', format='%H:%M', title='시각'), alt.Tooltip('o:Q', title='시가', format=','),
           alt.Tooltip('h:Q', title='고가', format=','), alt.Tooltip('l:Q', title='저가', format=','),
           alt.Tooltip('c:Q', title='종가', format=','), alt.Tooltip('vwap:Q', title='VWAP', format=',.0f'),
           alt.Tooltip('v:Q', title='거래량', format=',')]
    layers = [base.mark_rule(opacity=0.45).encode(y=alt.Y('l:Q', scale=alt.Scale(zero=False), title='가격'), y2='h:Q', color=color),
              base.mark_bar(size=width, opacity=0.45).encode(y='o:Q', y2='c:Q', color=color, tooltip=tip),  # dimmed: the trades stand out
              base.mark_line(color='#f59e0b', strokeWidth=1.6).encode(y='vwap:Q')]
    if data.get('prev_close'):
        layers.append(alt.Chart(pd.DataFrame({'p': [data['prev_close']]})).mark_rule(strokeDash=[5, 4], color='#94a3b8').encode(y='p:Q'))
    if not trades.empty:
        side = alt.condition("datum['구분'] == '매수'", alt.value(BUY), alt.value(SELL))
        mark_tip = [alt.Tooltip('time:T', format='%H:%M', title='시각'), '계좌:N', '구분:N', '수량:Q', alt.Tooltip('price:Q', format=',', title='가격'),
                    alt.Tooltip('실현:Q', format='+,'), alt.Tooltip('수익률:Q', format='+.2f'), '경로:N']
        points = alt.Chart(trades).encode(x='time:T', y='price:Q')
        layers += [alt.Chart(trades).mark_rule(strokeDash=[3, 3], strokeWidth=1.2, opacity=0.7).encode(x='time:T', color=side),
                   points.mark_point(filled=True, size=320, stroke='white', strokeWidth=2, opacity=1).encode(
                       shape=alt.Shape('구분:N', scale=alt.Scale(domain=['매수', '매도'], range=['triangle-up', 'triangle-down']), legend=None),
                       color=side, tooltip=mark_tip),
                   points.mark_text(dy=-20, fontSize=12, fontWeight='bold', stroke='#0b1220', strokeWidth=3).encode(text='label:N'),
                   points.mark_text(dy=-20, fontSize=12, fontWeight='bold').encode(text='label:N', color=side)]
    price = alt.layer(*layers).properties(height=340)
    volume = base.mark_bar(size=width, opacity=0.6).encode(y=alt.Y('v:Q', title='거래량'), color=color).properties(height=90)
    st.altair_chart(alt.vconcat(price, volume).resolve_scale(x='shared'), width='stretch')
    st.caption(f"▲ 매수 · ▼ 매도(세로 점선은 체결 시각, 라벨: 수량 · 실현손익) · 주황 선 VWAP · 회색 점선 전일 종가 · "
               f"흐린 빨강 양봉 · 흐린 파랑 음봉 · 분봉 {data.get('source')}")
    for line in tj.trade_lines(data.get('bars'), data.get('prev_close'), list(fills), market_bars):
        st.markdown(f'- {line}')
    if market is not None and not market.empty and code != tj.MARKET_PROXY:
        compare = pd.concat([pd.DataFrame({'time': frame['time'], '등락': (frame['c'] / frame['o'].iloc[0] - 1) * 100, '종목': code}),
                             pd.DataFrame({'time': market['time'], '등락': (market['c'] / market['o'].iloc[0] - 1) * 100, '종목': 'KODEX 200'})])
        line = alt.Chart(compare).mark_line().encode(x=x, y=alt.Y('등락:Q', title='시가 대비 %'),
                                                      color=alt.Color('종목:N', scale=alt.Scale(range=['#a855f7', '#64748b']),
                                                                      legend=alt.Legend(orient='top', title=None)),
                                                      tooltip=[alt.Tooltip('time:T', format='%H:%M'), '종목:N', alt.Tooltip('등락:Q', format='+.2f')])
        st.altair_chart(line.properties(height=200), width='stretch')
        st.caption('보라 = 이 종목, 회색 = KODEX 200(시장). 둘 다 그날 시가 대비 %입니다.')


def charts_section(record):
    """Every traded code of the day on its own intraday chart with every book's buys and sells."""
    books = record.get('books') or {}
    fills = [(b['label'], f) for b in books.values() for f in b['fills']]
    codes = list(dict.fromkeys(f['symbol'] for _, f in fills))
    if not codes:
        return
    stored = record.get('charts') or {}
    get = lambda code: stored.get(code) if stored.get(code, {}).get('bars') else live_chart_data(record['day'], code)  # noqa: E731
    st.markdown('**종목 차트 · 매수/매도 분석**')
    names = {c: f"{c} · 매수 {sum(1 for _, f in fills if f['symbol'] == c and f['side'] == 'BUY')} · 매도 "
                f"{sum(1 for _, f in fills if f['symbol'] == c and f['side'] == 'SELL')}" for c in codes}
    chosen = st.radio('종목', codes, format_func=names.get, horizontal=True, key=f"journal_chart_{record['day']}")
    market_data = get(tj.MARKET_PROXY)
    mine = [(b, f) for b, f in fills if f['symbol'] == chosen]
    trade_chart(record['day'], chosen, get(chosen), fill_frame(mine), bar_frame(record['day'], market_data),
                fills=[f for _, f in mine], market_bars=market_data.get('bars'))


STAR_COLORS = {5: '#ef4444', 4: '#f97316', 3: '#eab308', 2: '#64748b', 1: '#475569'}
ISSUE_CSS = """<style>
.iss{border:1px solid rgba(148,163,184,.25);border-left:5px solid var(--c);border-radius:12px;padding:10px 12px;margin:0 0 10px;
  background:linear-gradient(90deg,color-mix(in srgb,var(--c) 14%,transparent),transparent 70%)}
.iss.hot{box-shadow:0 0 0 1px var(--c),0 0 14px color-mix(in srgb,var(--c) 45%,transparent)}
.iss .rank{font-weight:800;font-size:1.05rem;margin-right:6px}
.iss .stars{color:var(--c);letter-spacing:1px;font-size:1.05rem}
.iss .cat{font-weight:700;margin-left:6px}
.iss ul{margin:6px 0 4px 1rem;padding:0}.iss li{margin:2px 0;font-size:.9rem}
.iss li.big a{font-weight:700}
.iss .why{font-size:.75rem;opacity:.75;margin-top:4px}
.chip{display:inline-block;border:1px solid rgba(148,163,184,.35);border-radius:999px;padding:0 7px;margin:0 4px 2px 0;font-size:.72rem}
</style>"""


def _issue_card(rank, stars, title, extra, items, why=''):
    from html import escape
    return (f'<div class="iss{" hot" if stars >= 4 else ""}" style="--c:{STAR_COLORS[stars]}"><span class="rank">{rank}</span>'
            f'<span class="stars">{tj.star_text(stars)}</span><span class="cat">{escape(title)}</span>'
            f'<span style="opacity:.6;font-size:.8rem">{escape(extra)}</span><ul>{items}</ul>'
            + (f'<div class="why">{why}</div>' if why else '') + '</div>')


def _written_cards(written):
    from html import escape
    out = []
    for i, item in enumerate(written, 1):
        links = ''.join(f'<li><a href="{escape(u)}" target="_blank">{escape(str(t or u))}</a></li>'
                        for t, u in ((x.get('title'), tj.safe_url(x.get('url'))) for x in item.get('links') or [] if isinstance(x, dict)) if u)
        n = tj.star_count(item.get('stars'))
        out.append(_issue_card(i, n, str(item.get('title', '')), '', links, escape(str(item.get('why', '')))))
    return out


def _ranked_cards(issues):
    from html import escape
    out = []
    for i, g in enumerate(issues, 1):
        items = ''.join(f'<li class="{"big" if h["stars"] >= 4 else ""}"><span style="color:{STAR_COLORS[h["stars"]]}">{"★" * h["stars"]}</span> '
                        f'<a href="{escape(h.get("link") or tj.news_search_link(h["title"]))}" target="_blank">{escape(h["title"])}</a>'
                        f' <span style="opacity:.6">· {escape(str(h.get("publisher") or ""))}</span></li>' for h in g['top'])
        why = ''.join(f'<span class="chip">{escape(w)}</span>' for w in g['top'][0]['why'][1:])
        out.append(_issue_card(i, g['stars'], g['category'], f" · {g['count']}건 · 점수 {g['score']}", items, why))
    return out


def _cards(cards, columns):
    st.markdown(ISSUE_CSS, unsafe_allow_html=True)
    cols = st.columns(columns)
    for i, card in enumerate(cards):
        cols[i % columns].markdown(card, unsafe_allow_html=True)


def top_issues(record, issues, limit=3):
    """오늘의 핵심 이슈 TOP n: the 16:40 analysis's ranking when it wrote one, else the data ranking."""
    written = (record.get('perspective') or {}).get('top_issues') or []
    cards = _written_cards(written[:limit]) if written else _ranked_cards(issues[:limit])
    st.markdown('##### 오늘의 핵심 이슈 ' + (':gray[· 16:40 분석 순위]' if written else ':gray[· 데이터 순위]'))
    if not cards:
        st.caption('이슈를 아직 모으지 않았습니다.')
        return
    _cards(cards, len(cards))  # TOP 3 side by side under the P&L curve


def issues_detail(record):
    """Every issue: the written ranking, the data ranking by category, every headline by stars, what the engines read,
    the analysts and the written views."""
    heads, issues = tj.rank_issues(record)
    view = (record.get('perspective') or {}).get('views') or {}
    for key in ('issues', 'earnings', 'analysts', 'stocks'):
        if view.get(key):
            st.markdown(f"**{VIEW_LABELS[key]}** — {tj._md_links_safe(view[key])}")
    written = (record.get('perspective') or {}).get('top_issues') or []
    if written:
        st.markdown('##### 16:40 분석이 고른 순위')
        _cards(_written_cards(written), 2)
    if issues:
        st.markdown('##### 분야별 중요도 순 (데이터)')
        _cards(_ranked_cards(issues), 3)
    if heads:
        with st.expander(f'모든 헤드라인 중요도 순 ({len(heads)}건)', expanded=not issues):
            st.caption('점수 = 분야 가중치(전쟁·지정학·금리·미국 증시 3, 반도체 2.5 …) + 핵심어(폭락·전쟁·관세·쇼크 …) + 그 분야 지표의 큰 움직임 '
                       '+ 매매 종목 언급 + 엔진 입력 시간 + 비슷한 보도 수. ★5 7점 이상 · ★4 5.5 · ★3 4 · ★2 2.5.')
            st.markdown('\n'.join(f"- {'**' if h['stars'] >= 4 else ''}<span style='color:{STAR_COLORS[h['stars']]}'>{tj.star_text(h['stars'])}</span> "
                                   f"[{tj._md(h['title'])}]({h.get('link') or tj.news_search_link(h['title'])}){'**' if h['stars'] >= 4 else ''} "
                                   f":gray[· {h['category']} · {h.get('publisher') or ''} · {h['score']}]" for h in heads), unsafe_allow_html=True)
    if record.get('engine_news'):
        st.markdown('##### 엔진이 그날 판단에 받은 뉴스 :gray[· 입력에 들어간 시간 순]')
        st.markdown('\n'.join(f"- [{tj._md(n.get('title'))}]({n.get('link') or tj.news_search_link(n.get('title') or '')}) · "
                               f":gray[{n.get('publisher') or ''} · {n.get('minutes', 0)}분]" for n in record['engine_news']))
    if record.get('analysts'):
        st.markdown('##### 매매 종목 애널리스트 의견 :gray[· 네이버 컨센서스·최근 리포트]')
        for c, a in record['analysts'].items():
            reports = [r if isinstance(r, dict) else {'text': r} for r in a.get('reports') or []]
            st.markdown(f"- [{c}]({a.get('link') or tj.stock_link(c)}) · 평균 의견 {a.get('recomm_mean') or '—'} · 목표가 "
                        + (f"{a['target_mean']:,.0f}원" if a.get('target_mean') else '—') + ' · 어조 '
                        + {'up': '상향', 'down': '하향', 'neutral': '중립'}.get(a.get('report_tone'), '—') + ' · '
                        + ' / '.join(f"[{tj._md(r['text'])}]({r['link']})" if r.get('link') else tj._md(r['text']) for r in reports))
    if not (heads or written or record.get('engine_news') or record.get('analysts')):
        st.info('이날 모은 이슈·뉴스가 없습니다.')


def diverging(values, title, height=None):
    """Horizontal net-buy bars: red buying, blue selling (억 원)."""
    frame = pd.DataFrame([{'항목': k, '순매수': v} for k, v in values.items() if v is not None])
    if frame.empty:
        return None
    bars = alt.Chart(frame).mark_bar(cornerRadius=3).encode(
        y=alt.Y('항목:N', sort=list(values), title=None, axis=alt.Axis(labelLimit=140, labelOverlap=False)),
        x=alt.X('순매수:Q', title='순매수 (억 원)'),
        color=alt.condition('datum["순매수"] >= 0', alt.value(BUY), alt.value(SELL)),
        tooltip=['항목:N', alt.Tooltip('순매수:Q', format='+,')])
    text = bars.mark_text(align='left', dx=4, fontSize=11).encode(text=alt.Text('순매수:Q', format='+,'), color=alt.value(THEME.ink('#cbd5e1')))
    return (bars + text).properties(height=height or 34 * len(frame) + 20, title=title)


def flows_section(record):
    """투자자·프로그램 수급: KOSPI / KOSDAQ side by side (net buying by investor and program, 5-day trend, breadth),
    then the traded codes' own flows."""
    flows = record.get('flows') or {}
    market, ctx = flows.get('market') or {}, (record.get('market_context') or {}).get('indices') or {}
    if not market and not ctx:
        st.caption('수급 자료가 없습니다(이 날 일지를 다시 만들면 채워집니다).')
        return
    st.markdown('##### 투자자·프로그램 수급')
    cols = st.columns(2)
    for col, code, name in ((cols[0], 'KOSPI', '코스피'), (cols[1], 'KOSDAQ', '코스닥')):
        m = market.get(code) or {}
        snap = ctx.get(code) or {}
        index = (record.get('market') or {}).get(name) or {}
        with col:
            with st.container(border=True):
                head = f"**{name}**"
                if index:
                    head += f" {index['close']:,.2f} ({pct(index['change_pct'])})"
                st.markdown(head)
                breadth = m.get('breadth') or snap.get('breadth') or {}
                chips = []
                if breadth.get('상승') is not None:
                    chips.append(f"상승 {breadth['상승']:,.0f} · 하락 {breadth.get('하락', 0):,.0f}")
                if snap.get('value_100m'):
                    chips.append(f"거래대금 {snap['value_100m']:,.0f}억")
                if (snap.get('regime') or {}).get('label'):
                    chips.append(f"국면 {snap['regime']['label']} · 흐름 {snap.get('flow', '—')}")
                if chips:
                    st.caption(' · '.join(chips))
                values = {k: m.get(k) for k in ('개인', '외국인', '기관')}
                values |= {f'프로그램 {k}': v for k, v in (m.get('program') or {}).items()}
                chart = diverging(values, '투자자·프로그램 순매수')
                if chart is not None:
                    st.altair_chart(chart, width='stretch')
                if not m.get('program'):
                    st.caption('프로그램 매매는 장 마감 당일에 만든 일지에만 있습니다.')
                hist = m.get('history') or []
                if hist:
                    frame = pd.DataFrame([{'날짜': h[0][5:], '투자자': who, '순매수': v} for h in hist
                                          for who, v in zip(('개인', '외국인', '기관'), h[1:]) if v is not None])
                    trend = alt.Chart(frame).mark_bar().encode(
                        x=alt.X('날짜:N', title=None, axis=alt.Axis(labelAngle=0)), xOffset=alt.XOffset('투자자:N', sort=['개인', '외국인', '기관']),
                        y=alt.Y('순매수:Q', title='억 원', axis=alt.Axis(format='~s')),
                        color=alt.Color('투자자:N', scale=alt.Scale(domain=['개인', '외국인', '기관'], range=['#eab308', '#a855f7', '#22c55e']),
                                        legend=alt.Legend(orient='top', title=None)),
                        tooltip=['날짜:N', '투자자:N', alt.Tooltip('순매수:Q', format='+,')])
                    zero = alt.Chart(pd.DataFrame({'y': [0]})).mark_rule(color='#64748b').encode(y='y:Q')
                    st.altair_chart((trend + zero).properties(height=230, title='최근 5거래일 투자자별 순매수'), width='stretch')
    details = {name: (flows.get('market') or {}).get(code, {}).get('detail') for code, name in (('KOSPI', '코스피'), ('KOSDAQ', '코스닥'))}
    details = {name: d for name, d in details.items() if isinstance(d, dict)}
    if details:
        import toss_data
        import broker_data_view as broker_view
        st.markdown('**기관 세부·기타법인** (토스증권, 순매수 억 원)')
        st.dataframe([{'시장': name, **{k: (d.get('institution_detail') or {}).get(k) for k in toss_data.INSTITUTION_KINDS.values()},
                       '기관 계': d.get('기관'), '기타법인': d.get('기타법인'), '기준 시각': broker_view.stamp(d.get('updated_at'))}
                      for name, d in details.items()], hide_index=True, width='stretch', alt='기관 세부·기타법인 순매수',
                     column_config={k: st.column_config.NumberColumn(format='%+,.0f') for k in [*toss_data.INSTITUTION_KINDS.values(), '기관 계', '기타법인']})
    stocks = flows.get('stocks') or {}
    if stocks:
        names = tj.stock_names(set(stocks))
        st.markdown('**매매 종목 수급** (그날 외국인·기관·개인 순매수)')
        rows, frame = [], []
        for code, f in stocks.items():
            row = {'종목': f"{names.get(code, code)} ({code})"}
            for who in ('외국인', '기관', '개인'):
                item = f.get(who) or {}
                row[f'{who} (주)'] = f"{item.get('shares'):+,.0f}" if item.get('shares') is not None else '—'
                row[f'{who} (억)'] = f"{item.get('value_100m'):+,.1f}" if item.get('value_100m') is not None else '—'
                if item.get('value_100m') is not None:
                    frame.append({'종목': names.get(code, code), '투자자': who, '순매수': item['value_100m']})
            row['비고'] = '잠정' if f.get('provisional') else ''
            rows.append(row)
        st.dataframe(rows, hide_index=True, width='stretch')
        if frame:
            chart = alt.Chart(pd.DataFrame(frame)).mark_bar().encode(
                y=alt.Y('종목:N', title=None, axis=alt.Axis(labelLimit=160, labelOverlap=False)), yOffset='투자자:N',
                x=alt.X('순매수:Q', title='순매수 (억 원)'),
                color=alt.Color('투자자:N', scale=alt.Scale(domain=['외국인', '기관', '개인'], range=['#a855f7', '#22c55e', '#eab308']),
                                legend=alt.Legend(orient='top', title=None)),
                tooltip=['종목:N', '투자자:N', alt.Tooltip('순매수:Q', format='+,.1f')])
            st.altair_chart(chart.properties(height=40 + 66 * len(stocks)), width='stretch')
    import broker_data_view  # Toss short selling / credit / lending and the investor leaders (2026-11-17)
    broker_data_view.journal_extras(flows, tj.stock_names(set(flows.get('pressure') or {})) if flows.get('pressure') else {})
    st.caption('출처: 네이버 증권(코스피·코스닥 투자자별 순매수는 날짜별, 프로그램·상승/하락은 그날 마감 스냅숏), 토스증권(기관 세부·기타법인, '
               '미등록 외국인 포함이라 네이버 합계와 다를 수 있음). 당일 종목 수급은 다음 날 확정 전까지 잠정치입니다.')


def report_view(key, auto, kind='day'):
    """A journal's report (day or period): the written analysis (reviews/journal-<key>.md) when there is one, checked
    against the template, and the report assembled from the record."""
    written = tj.REVIEWS / f'journal-{key}.md'
    text = written.read_text(encoding='utf-8') if written.exists() else None
    author = '평일 16:40 "매매일지 작성"' if kind == 'day' else '"기간 매매일지 작성"(분기 첫날 17:10 또는 요청 시)'
    left, right = st.columns(2)
    left.download_button('데이터 리포트 내려받기 (.md)', auto, file_name=f'trading-journal-{key}.md', mime='text/markdown',
                         key=f'journal_dl_auto_{key}', width='stretch')
    if not text:
        right.caption(f'분석 리포트는 {author}이 씁니다. 아래는 기록으로 만든 데이터 리포트입니다(같은 템플릿).')
        with st.container(border=True):
            st.markdown(auto)
        return
    right.download_button('분석 리포트 내려받기 (.md)', text, file_name=f'trading-journal-analysis-{key}.md',
                          mime='text/markdown', key=f'journal_dl_written_{key}', width='stretch')
    problems = T.check(text, 'day' if kind == 'day' else 'period', written=True)
    if problems:
        st.warning('분석 리포트가 템플릿과 다릅니다: ' + ' · '.join(problems), icon=':material/rule:')
    written_tab, auto_tab = st.tabs(['분석 리포트', '데이터 리포트 (자동)'])
    with written_tab:
        with st.container(border=True):
            st.markdown(for_streamlit(tj._md_links_safe(text)))
    with auto_tab:
        with st.container(border=True):
            st.markdown(auto)


def book_detail(book):
    """One book's day: the numbers, the P&L curve, every fill and every round trip."""
    p = book['performance']
    sc.row([{'label': '실현손익', 'value': won(p['realized_krw']), 'tone': sc.signed(p['realized_krw'] or 0), 'sub': f"매수 대비 {pct(p['return_on_buys_pct'])}"},
            {'label': '승률', 'value': _win_rate(p), 'tone': 'off', 'sub': f"매도 {p['sells']}건"},
            {'label': '평균 이익 / 손실', 'value': f"{pct(p['avg_win_pct'])} / {pct(p['avg_loss_pct'])}", 'tone': 'off',
             'sub': f"손익비 {p['profit_factor'] or '—'}"},
            {'label': '가장 수익이 컸던 시점', 'value': str(p['peak']['time'])[11:16] if p.get('peak') else '—', 'tone': 'up' if p.get('peak') else 'off',
             'sub': won(p['peak']['realized_krw']) if p.get('peak') else '이익 구간 없음'}])
    chart = curve_chart([(book['label'], book['fills'])])
    if chart is not None:
        st.altair_chart(chart, width='stretch')
    if p['by_symbol']:
        st.caption('종목별 실현: ' + ' · '.join(f'{k} {v:+,}원' for k, v in p['by_symbol'].items()))
    if not book['fills']:
        st.info('이날 이 계좌의 체결은 없습니다.')
        return
    dialogs.table_frame(pd.DataFrame([{'시각': str(f['time'])[11:19], '종목': f['symbol'], '구분': '매수 ▲' if f['side'] == 'BUY' else '매도 ▼',
                                       '수량': f['qty'], '가격': f"{f['price']:,.0f}", '금액': f"{f['amount']:,.0f}", '실현': won(f.get('pnl')),
                                       '수익률': pct(f.get('return_pct')), '경로': f.get('path') or ''} for f in book['fills']])
                        .style.map(dialogs._tone, subset=['실현', '수익률']))
    if book.get('round_trips'):
        st.markdown(f"**왕복 매매 {len(book['round_trips'])}건** :gray[· 진입 경로 · 청산 이유 · 보유 시간 · 보유 중 최고/최저]")
        dialogs.table_frame(pd.DataFrame([{'종목': t['symbol'], '진입': t['open'], '청산': t['close'] or '보유', '경로': t['entry_route'],
                                           '청산 이유': t['exit'], '보유(분)': t['held_min'], '손익': won(t['pnl']), '수익률': pct(t['return_pct']),
                                           '보유 중 최고': pct(t.get('mfe_pct')), '보유 중 최저': pct(t.get('mae_pct'))} for t in book['round_trips']])
                            .style.map(dialogs._tone, subset=['손익', '수익률', '보유 중 최고', '보유 중 최저']))


def market_detail(record):
    m = record.get('market') or {}
    if m:
        dialogs.table_frame(pd.DataFrame([{'지표': n, '종가': f"{v['close']:,.2f}", '등락': pct(v['change_pct']), '기준일': v['on'],
                                           '링크': tj.QUOTE_LINKS.get(n)} for n, v in m.items()]).style.map(dialogs._tone, subset=['등락']),
                            column_config={'링크': st.column_config.LinkColumn('링크', display_text='시세 보기')})
        st.caption('미국 지수·원자재는 한국 장 전날 밤 마감 기준입니다(Yahoo).')
    themes = ((record.get('market_context') or {}).get('themes') or {})
    if themes.get('top'):
        st.caption('강한 테마: ' + ' · '.join(str(t.get('name', t)) for t in themes['top'][:6]))
    view = (record.get('perspective') or {}).get('views') or {}
    for key in ('summary', 'market', 'global'):
        if view.get(key):
            st.markdown(f"**{VIEW_LABELS[key]}** — {tj._md_links_safe(view[key])}")
    if not m:
        st.info('이날 지표를 모으지 않았습니다.')


def system_detail(record):
    s = record.get('system') or {}
    if s.get('errors'):
        st.markdown('**오류·결함 기록**')
        dialogs.table_frame([{'종류': k, '횟수': v} for k, v in s['errors'].items()])
    if s.get('entry_gates'):
        st.markdown('**진입하지 않은 이유** :gray[· 상위]')
        dialogs.table_frame([{'이유': k, '횟수': v} for k, v in list(s['entry_gates'].items())[:12]])
    if s.get('restriction_blocks'):
        st.caption('제한 규칙이 막은 횟수: ' + ' · '.join(f'{k} {v}' for k, v in s['restriction_blocks'].items()))
    fixes = (record.get('perspective') or {}).get('system_improvements') or []
    if fixes:
        st.markdown('**시스템 결함·보완 사항**\n' + '\n'.join(f'- {tj._md_links_safe(x)}' for x in fixes))
    if s.get('health'):
        with st.expander('실행기 상태 원본'):
            st.json(s['health'], expanded=False)
    if s.get('improvement_report'):
        with st.expander('장 마감 개선안 보고서 (16:15)'):
            st.markdown(for_streamlit(s['improvement_report']))


RETRO = (('retrospective', '복기·회고'), ('strategy_improvements', '매매 전략 보완'), ('system_improvements', '시스템 개선'),
         ('engine_notes', '다음 날 엔진 참고 메모'), ('watch_next', '다음 날 지켜볼 것'))


def retro_detail(record):
    view = record.get('perspective') or {}
    if not view:
        st.info('이날의 회고·관점 정리는 아직 없습니다. 평일 16:40 예약 작업 "매매일지 작성"이 씁니다.')
        return
    if view.get('summary'):
        st.info(tj._md_links_safe(view['summary']), icon=':material/edit_note:')
    for key, title in RETRO:
        if view.get(key):
            st.markdown(f'**{title}**\n' + '\n'.join(f'- {tj._md_links_safe(x)}' for x in view[key]))


def _index_card(record, name, label):
    v = (record.get('market') or {}).get(name)
    if not v:
        return {'label': label, 'value': '—', 'tone': 'off', 'icon': 'show_chart', 'sub': '자료 없음'}
    return {'label': label, 'value': pct(v['change_pct']), 'tone': sc.signed(v['change_pct']), 'icon': 'show_chart',
            'sub': f"{v['close']:,.2f} · {v['on']}"}


def context_items(record, issues):
    """시장 · 수급 · 이슈 · 시스템 · 회고 cards, each opening its details."""
    kospi = ((record.get('flows') or {}).get('market') or {}).get('KOSPI') or {}
    foreign = kospi.get('외국인')
    flow = {'label': '외국인 순매수 (코스피)', 'icon': 'groups', 'tone': sc.signed(foreign or 0) if foreign is not None else 'off',
            'value': f'{foreign:+,.0f}억' if foreign is not None else '—',
            'sub': ' · '.join(f"{k} {kospi[k]:+,.0f}" for k in ('기관', '개인') if kospi.get(k) is not None) or '수급 자료 없음'}
    written = (record.get('perspective') or {}).get('top_issues') or []
    if written:
        first = written[0]
        stars, title, sub = tj.star_count(first.get('stars')), str(first.get('title', '')), str(first.get('why', ''))
    elif issues:
        stars, title, sub = issues[0]['stars'], issues[0]['category'], issues[0]['top'][0]['title']
    else:
        stars, title, sub = 0, '—', '이슈 수집 전'
    issue = {'label': '핵심 이슈 1위', 'icon': 'local_fire_department', 'tone': 'alert' if stars >= 5 else 'warn' if stars >= 4 else 'off',
             'value': f"{tj.star_text(stars)} {title}" if stars else title, 'sub': sub[:60]}
    s = record.get('system') or {}
    errors = sum((s.get('errors') or {}).values())
    gates = list((s.get('entry_gates') or {}).items())
    system = {'label': '시스템', 'icon': 'monitor_heart', 'tone': 'alert' if errors else 'ok',
              'value': f"오류 {errors}회" if errors else '오류 없음',
              'sub': f"진입 차단 1위 {gates[0][0]} {gates[0][1]}회" if gates else '진입 차단 기록 없음'}
    view = record.get('perspective') or {}
    lessons = sum(len(view.get(k) or []) for k, _ in RETRO)
    retro = {'label': '회고·보완', 'icon': 'edit_note', 'tone': 'ai' if view else 'off',
             'value': f'{lessons}건' if view else '작성 전', 'sub': (view.get('summary') or '')[:60] if view else '평일 16:40 예약 작업이 씁니다'}
    return [detail(_index_card(record, '코스피', '코스피'), '지수·해외·원자재', market_detail, record),
            detail(_index_card(record, '나스닥', '나스닥 (전날 밤)'), '지수·해외·원자재', market_detail, record),
            detail(flow, '투자자·프로그램 수급', flows_section, record),
            detail(issue, '이슈·뉴스·애널리스트', issues_detail, record),
            detail(system, '시스템', system_detail, record),
            detail(retro, '회고·개선', retro_detail, record)]


def day_view(record):
    sc.inject()
    books = record.get('books') or {}
    heads, issues = tj.rank_issues(record)
    if books:
        kpi_row([detail({'label': b['label'], 'value': won(b['performance']['realized_krw']), 'icon': BOOK_ICONS.get(k, 'account_balance'),
                         'tone': sc.signed(b['performance']['realized_krw'] or 0),
                         'sub': f"매수 대비 {pct(b['performance']['return_on_buys_pct'])} · 매도 {b['performance']['sells']}건 · "
                                f"승률 {_win_rate(b['performance'])}"}, f"{b['label']} 매매", book_detail, b)
                 for k, b in books.items()], 'jr_book')
    kpi_row(context_items(record, issues), 'jr_ctx')
    THEME.graphviz(day_dot(record), width='stretch')
    st.markdown('##### 실현손익 흐름 :gray[· 계좌별 누적]')
    chart = curve_chart([(b['label'], b['fills']) for b in books.values()])
    if chart is not None:
        st.altair_chart(chart, width='stretch')
    else:
        st.caption('이날 매도 체결이 없어 실현손익 흐름이 없습니다.')
    top_issues(record, issues)
    tabs = st.tabs(['매매관점 리포트', '매매·차트', '시장·수급', '이슈·뉴스', '시스템·회고', '장기투자'], key='journal_day_tab', on_change='rerun')
    if tabs[0].open:
        with tabs[0]:
            report_view(record['day'], tj.report_md(record))
    if tabs[1].open:
        with tabs[1]:
            charts_section(record)
            traded = [b for b in books.values() if b['fills']]
            for i, book in enumerate(traded):
                with st.expander(f"{book['label']} · 체결 {len(book['fills'])}건 · 실현 {won(book['performance']['realized_krw'])}", expanded=i == 0):
                    book_detail(book)
            if not traded:
                st.caption('이날은 체결이 없습니다.')
    if tabs[2].open:
        with tabs[2]:
            flows_section(record)
            st.markdown('##### 지수·해외·원자재·코인')
            market_detail(record)
    if tabs[3].open:
        with tabs[3]:
            issues_detail(record)
    if tabs[4].open:
        with tabs[4]:
            left, right = st.columns(2, gap='medium')
            with left:
                st.markdown('##### 시스템')
                system_detail(record)
            with right:
                st.markdown('##### 회고·개선')
                retro_detail(record)
    if tabs[5].open:  # 2026-11-17: the masters' macro view, the long-term journal and the day's strategy report
        with tabs[5]:
            import longterm_journal_view
            longterm_journal_view.render_day(record)


def daily_table(book):
    """The book's days, newest first."""
    return pd.DataFrame([{'날짜': day, '실현손익': won(pnl), '매수 금액': f'{bought:,.0f}원'} for day, pnl, bought in reversed(book['daily'])])


def drawdown_chart(book):
    cum = pd.DataFrame([{'날짜': pd.to_datetime(day), '누적': v} for day, v in book['cumulative']])
    if cum.empty:
        st.info('기록이 없습니다.')
        return
    cum['고점'] = cum['누적'].cummax()
    cum['낙폭'] = cum['누적'] - cum['고점']
    line = alt.Chart(cum).mark_line(color='#a855f7').encode(x=alt.X('날짜:T', title=None), y=alt.Y('누적:Q', title='누적 실현 (원)'),
                                                            tooltip=['날짜:T', alt.Tooltip('누적:Q', format='+,'), alt.Tooltip('낙폭:Q', format='+,')])
    peak = alt.Chart(cum).mark_line(strokeDash=[4, 4], color='#64748b').encode(x='날짜:T', y='고점:Q')
    area = alt.Chart(cum).mark_area(color=SELL, opacity=0.35).encode(x='날짜:T', y=alt.Y('낙폭:Q', title='고점 대비 (원)'))
    st.altair_chart(alt.vconcat((line + peak).properties(height=220), area.properties(height=120)).resolve_scale(x='shared'),
                    width='stretch')
    st.caption('위: 누적 실현손익(보라)과 그때까지의 고점(회색 점선) · 아래: 고점 대비 낙폭.')


def trades_detail(p):
    rows = [{'구분': name, '시각': str(t['time'])[:16], '종목': t['symbol'], '실현': won(t['pnl']), '수익률': pct(t.get('return_pct'))}
            for name, t in (('가장 큰 이익', p.get('best_trade')), ('가장 큰 손실', p.get('worst_trade'))) if t]
    dialogs.table_frame(pd.DataFrame(rows).style.map(dialogs._tone, subset=['실현', '수익률']) if rows else rows)
    ranked = list(p['by_symbol'].items())
    st.markdown('**종목별 실현손익** :gray[· 이익 큰 순]')
    dialogs.table_frame(pd.DataFrame([{'종목': k, '실현': won(v)} for k, v in ranked]).style.map(dialogs._tone, subset=['실현']) if ranked else [])


def period_book_detail(book):
    p = book['performance']
    st.caption(f"매매일 {book['trading_days']} · 이익일 {book['up_days']} · 승률 {_win_rate(p)} · 손익비 {p['profit_factor'] or '—'}")
    dialogs.table_frame(daily_table(book).style.map(dialogs._tone, subset=['실현손익']))
    trades_detail(p)


def period_view(records, title, period=None):
    """월·분기·반기·년: the glance and the period journal in the template (stored after the close, else built now)."""
    if not period:
        return period_glance(records, title)
    kind, key = period
    tabs = st.tabs(['한눈에 보기', '기간 매매일지 리포트', '장기투자'], key='journal_period_tab', on_change='rerun')
    if tabs[0].open:
        with tabs[0]:
            period_glance(records, title)
    if tabs[1].open:
        with tabs[1]:
            record = tj.load_period(key) or tj.build_period(kind, key)
            st.caption(f"{record['label']} · 일지 {record['days']}일 · 모은 시각 {str(record.get('built', ''))[:16].replace('T', ' ')} · "
                       f"기간 회고 {'작성됨' if record.get('perspective') else '작성 전'} · 일지가 쌓일 때마다 장 마감 뒤 다시 모읍니다")
            report_view(key, tj.period_report_md(record), kind)
    if tabs[2].open:
        with tabs[2]:
            import longterm_journal_view
            if records:
                longterm_journal_view.render_period(records[0]['day'], records[-1]['day'])
            else:
                st.caption('이 기간에 저장된 일지가 없어 장기투자 기간 기록을 모을 날짜가 없습니다.')


def period_glance(records, title):
    if not records:
        st.caption('이 기간에 저장된 일지가 없습니다.')
        return
    sc.inject()
    pooled = tj.period(records)
    if not pooled:
        st.caption('이 기간 일지에 계좌 기록이 없습니다.')
        return
    st.markdown(f'**{title}** :gray[· {records[0]["day"]} ~ {records[-1]["day"]} · 일지 {len(records)}일 · 체결가 기준, 수수료·세금 미반영]')
    kpi_row([detail({'label': b['label'], 'value': won(b['performance']['realized_krw']), 'icon': BOOK_ICONS.get(k, 'account_balance'),
                     'tone': sc.signed(b['performance']['realized_krw'] or 0),
                     'sub': f"매매일 {b['trading_days']} · 이익일 {b['up_days']} · 승률 {_win_rate(b['performance'])}"},
                    f"{b['label']} 기간 성적", period_book_detail, b) for k, b in pooled.items()], 'jr_pbook')
    key = st.segmented_control('계좌', list(pooled), format_func=lambda k: pooled[k]['label'], default=next(iter(pooled)),
                               key='journal_period_book')
    key = key if key in pooled else next(iter(pooled))  # deselected, or a book this period does not have
    book = pooled[key]
    p = book['performance']
    items = [detail({'label': '매수 대비 수익률', 'value': pct(p['return_on_buys_pct']), 'tone': sc.signed(p['return_on_buys_pct'] or 0),
                     'icon': 'percent', 'sub': f"평균 이익 {pct(p['avg_win_pct'])} · 손실 {pct(p['avg_loss_pct'])}"},
                    '일별 실현손익', period_book_detail, book),
             detail({'label': '최대 낙폭 (누적)', 'value': won(book['max_drawdown_krw']), 'tone': 'down' if book['max_drawdown_krw'] else 'off',
                     'icon': 'trending_down', 'sub': f"손익비 {p['profit_factor'] or '—'}"}, '누적 손익과 낙폭', drawdown_chart, book),
             detail({'label': '가장 큰 이익 · 손실 매매', 'icon': 'swap_vert', 'tone': 'off',
                     'value': f"{won((p.get('best_trade') or {}).get('pnl'))} / {won((p.get('worst_trade') or {}).get('pnl'))}",
                     'sub': ' · '.join(f"{t['symbol']} {str(t['time'])[5:10]}" for t in (p.get('best_trade'), p.get('worst_trade')) if t) or '—'},
                    '매매·종목 순위', trades_detail, p)]
    for name, d, tone in (('가장 수익이 큰 날', book['best_day'], 'up'), ('손실이 큰 날', book['worst_day'], 'down')):
        card = {'label': name, 'icon': 'event', 'tone': tone if d else 'off', 'value': won(d[1]) if d else '—', 'sub': d[0] if d else '—'}
        items.append(jump(card, d[0]) if d else detail(card, name, st.info, '해당하는 날이 없습니다.'))
    kpi_row(items, 'jr_pkpi')
    frame = pd.DataFrame([{'날짜': day[5:], '실현손익': pnl, '누적': cum} for (day, pnl, _), (_, cum) in zip(book['daily'], book['cumulative'])])
    if not frame.empty:  # one band per stored day (no weekend gaps, no clock ticks)
        x = alt.X('날짜:O', title=None, axis=alt.Axis(labelAngle=0))
        bars = alt.Chart(frame).mark_bar(cornerRadius=2).encode(x=x, y=alt.Y('실현손익:Q', title='일별 실현손익 (원)'),
                                                                color=alt.condition('datum.실현손익 >= 0', alt.value(BUY), alt.value(SELL)),
                                                                tooltip=['날짜:O', alt.Tooltip('실현손익:Q', format='+,'), alt.Tooltip('누적:Q', format='+,')])
        line = alt.Chart(frame).mark_line(color='#a855f7', point=True).encode(x=x, y=alt.Y('누적:Q', title='누적 (원)'))
        st.altair_chart(alt.layer(bars, line).resolve_scale(y='independent').properties(height=260), width='stretch')
        st.caption('막대 = 하루 실현손익(빨강 이익 · 파랑 손실), 보라 선 = 누적 실현손익.')
    left, right = st.columns(2, gap='medium')
    with left:
        routes = tj.route_stats(records)
        st.markdown('**진입 경로별 성적** :gray[· 모의 · Jev]')
        dialogs.table_frame(pd.DataFrame([{'경로': k, '왕복': v['n'], '이익': v['wins'], '승률': f"{v['wins'] / v['n'] * 100:.0f}%" if v['n'] else '—',
                                           '실현': won(v['realized_krw'])} for k, v in sorted(routes.items(), key=lambda kv: -kv[1]['realized_krw'])])
                            .style.map(dialogs._tone, subset=['실현']) if routes else [])
    with right:
        ranked = list(p['by_symbol'].items())
        st.markdown(f"**종목별 실현손익** :gray[· {book['label']} · 이익 큰 5 · 손실 큰 5]")
        top = ranked[:5] + [x for x in ranked[-5:] if x not in ranked[:5]]
        dialogs.table_frame(pd.DataFrame([{'종목': k, '실현': won(v)} for k, v in top]).style.map(dialogs._tone, subset=['실현']) if top else [])
    st.markdown('**일별 일지** :gray[· 행을 고르면 그날 일지가 열립니다]')
    newest = list(reversed(records))
    rows = pd.DataFrame([{'날짜': r['day'], **{b['label']: won(b['performance']['realized_krw']) for b in (r.get('books') or {}).values()},
                          '코스피': pct(((r.get('market') or {}).get('코스피') or {}).get('change_pct')),
                          '나스닥(전날 밤)': pct(((r.get('market') or {}).get('나스닥') or {}).get('change_pct')),
                          '요약': ((r.get('perspective') or {}).get('summary') or '')[:120]} for r in newest])

    def opened():
        picked = st.session_state['journal_period_days'].selection.rows
        if picked:
            st.session_state[GOTO] = newest[picked[0]]['day']
    dialogs.table_frame(rows.style.map(dialogs._tone, subset=[c for c in rows.columns if c not in ('날짜', '요약')]),
                        key='journal_period_days', on_select=opened, selection_mode='single-row')
    lessons = [(r['day'], x) for r in records for x in ((r.get('perspective') or {}).get('strategy_improvements') or [])]
    fixes = [(r['day'], x) for r in records for x in ((r.get('perspective') or {}).get('system_improvements') or [])]
    if lessons or fixes:
        with st.expander(f'기간의 전략 보완 {len(lessons)}건 · 시스템 개선 {len(fixes)}건'):
            st.markdown('\n'.join(f'- {d} · {tj._md_links_safe(x)}' for d, x in lessons + fixes))


def _step(stored, delta):
    """◀ / ▶: the previous or next stored journal day."""
    current = st.session_state['journal_day'].isoformat()
    near = [d for d in stored if (d < current if delta < 0 else d > current)]
    if near:
        st.session_state['journal_day'] = date.fromisoformat(near[-1] if delta < 0 else near[0])


KEEP = ('journal_day', 'journal_month', 'journal_quarter', 'journal_half', 'journal_year', 'journal_range')
UNIT_KINDS = {'월': 'month', '분기': 'quarter', '반기': 'half', '연': 'year'}


def render():
    stored = tj.day_list()
    today = datetime.now(KST).date()
    for key in KEEP:  # Streamlit drops a widget's value on runs that do not draw it: keep each mode's choice
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]
    if goto_day := st.session_state.pop(GOTO, None):  # before the widgets exist, so their state can still change
        st.session_state['journal_unit'] = '일'
        st.session_state['journal_day'] = date.fromisoformat(goto_day)
        st.session_state.pop('journal_period_days', None)  # a kept row selection would hide the next click on that row
    st.session_state.setdefault('journal_day', date.fromisoformat(stored[-1]) if stored else today)
    st.session_state.setdefault('journal_unit', '일')
    head, back, pick, forward, rebuild = st.columns([3.2, 0.45, 1.5, 0.45, 1.6], vertical_alignment='bottom')
    unit = head.segmented_control('보기', ['일', *UNIT_KINDS, '기간'], key='journal_unit') or '일'
    if unit == '일':
        current = st.session_state['journal_day'].isoformat()
        back.button('', icon=':material/chevron_left:', key='journal_prev', help='이전 일지', width='stretch',
                    on_click=_step, args=(stored, -1), disabled=not any(d < current for d in stored))
        day = pick.date_input('날짜', key='journal_day', format='YYYY-MM-DD').isoformat()
        forward.button('', icon=':material/chevron_right:', key='journal_next', help='다음 일지', width='stretch',
                       on_click=_step, args=(stored, 1), disabled=not any(d > current for d in stored))
        chosen = st.session_state['journal_day']
        if rebuild.button('이날 일지 다시 만들기', key='journal_rebuild', icon=':material/refresh:', width='stretch',
                          disabled=chosen.weekday() >= 5 or chosen > today,
                          help='체결·시장·뉴스·시스템 기록을 다시 모읍니다(약 15초). 그날만 잡히는 프로그램 매매·시장 맥락과 더 촘촘한 분봉은 '
                               '기존 일지 것을 지킵니다. 회고·관점 정리는 예약 작업이 씁니다. 주말·미래 날짜는 만들지 않습니다.'):
            with st.spinner('일지를 모으는 중'):
                tj.rebuild(day)
        record = tj.load(day)
        if record is None:
            st.info(f'{day} 일지가 아직 없습니다. "이날 일지 다시 만들기"를 누르거나 장 마감 뒤 자동 작성을 기다리세요.', icon=':material/event_busy:')
            return
        st.caption(f"{day} ({tj.WEEKDAYS[date.fromisoformat(day).weekday()]}) · 모은 시각 {str(record.get('built', ''))[:16].replace('T', ' ')} · 회고 "
                   f"{'작성됨' if record.get('perspective') else '작성 전'} · 체결가 기준, 수수료·세금 미반영 · 카드 아래 '자세히'로 상세를 엽니다")
        day_view(record)
        return
    if unit in UNIT_KINDS:
        kind = UNIT_KINDS[unit]
        keys = sorted({T.key_of(kind, d) for d in stored}) or [T.key_of(kind, today)]
        state = f'journal_{kind}'
        if st.session_state.get(state) not in keys:
            st.session_state[state] = keys[-1]
        key = pick.selectbox(unit, keys, key=state, format_func=lambda k: T.label(kind, k))
        since, until = T.bounds(kind, key)
        period_view(tj.days(since, until), f'{T.label(kind, key)} 매매일지', (kind, key))
    else:
        st.session_state.setdefault('journal_range', (today - timedelta(days=30), today))
        picked = pick.date_input('기간', key='journal_range', format='YYYY-MM-DD')
        if not isinstance(picked, (tuple, list)) or len(picked) != 2:
            st.caption('끝 날짜까지 고르세요.')
            return
        period_view(tj.days(picked[0].isoformat(), picked[1].isoformat()), '기간 매매일지')
