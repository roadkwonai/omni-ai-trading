"""Left sidebar: system status at a glance (each opens its detail pop-up) and one-click tab navigation."""
from __future__ import annotations

from html import escape

ICONS = {'장기투자': 'savings', '종합 현황': 'dashboard', '시장 상황': 'monitoring', '전략 구조': 'account_tree', '트레이딩 현황': 'candlestick_chart', '매매내역': 'receipt_long', '매매일지': 'menu_book',
         '계좌·자동매매': 'tune', '성과 비교': 'compare_arrows', '실계좌': 'account_balance', '운영': 'settings_heart'}

CSS = """<style>
.nav-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin:2px 0 6px}
.nav-group{font-size:.74rem;opacity:.7;margin:8px 0 2px;letter-spacing:.02em}
.nav-btn{display:flex;align-items:center;gap:6px;min-height:40px;padding:6px 10px;border-radius:10px;cursor:pointer;
  border:1px solid rgba(128,128,128,.28);background:transparent;color:inherit;font:inherit;font-size:.86rem;text-align:left;
  transition:background .15s ease,border-color .15s ease}
.nav-btn:hover{background:rgba(128,128,128,.12)}
.nav-btn:focus-visible{outline:2px solid #22c55e;outline-offset:2px}
.nav-btn[aria-current="page"]{border-color:#22c55e;background:rgba(34,197,94,.14);font-weight:600}
.nav-btn .ms{font-family:'Material Symbols Rounded';font-size:18px;line-height:1;opacity:.85}
.side-stat{display:flex;align-items:center;gap:8px;padding:6px 2px;font-size:.9rem}
.side-stat .dot{width:9px;height:9px;border-radius:50%;flex:none}
.side-stat .v{margin-left:auto;font-weight:600}
</style>"""

# Clicks a main tab in the page (no app rerun, so switching stays instant) and marks the active nav button.
# Handlers live on window.parent and are redefined each run; the one document listener just calls them.
SCRIPT = """<script>
const win = window.parent, doc = win.document;
// the first tablist inside the main tabs is their own; nested tabs in the content come after it
const tabs = () => { const list = doc.querySelector('.st-key-main_tab [role="tablist"]'); return list ? [...list.querySelectorAll(':scope > [role="tab"]')] : []; };
win.__navSync = () => {
  const active = tabs().find(t => t.getAttribute('aria-selected') === 'true');
  const label = active ? active.innerText.trim() : '';
  doc.querySelectorAll('.nav-btn').forEach(b => b.setAttribute('aria-current', b.dataset.tab === label ? 'page' : 'false'));
};
win.__navClick = e => {
  const btn = e.target.closest('.nav-btn');
  if (btn) {
    const tab = tabs().find(t => t.innerText.trim() === btn.dataset.tab);
    if (tab) { tab.click(); win.scrollTo({top: 0, behavior: 'smooth'}); }
  }
  setTimeout(win.__navSync, 80);
};
if (!win.__navInstalled) { win.__navInstalled = true; doc.addEventListener('click', e => win.__navClick(e), true); }
setTimeout(win.__navSync, 300);
</script>"""


def nav_html(tabs, groups=None):
    """Shortcut buttons, one titled block per tab group (매일 · 계좌 · 관리) when groups are given."""
    button = lambda t: (f'<button type="button" class="nav-btn" data-tab="{escape(t)}"><span class="ms" aria-hidden="true">'
                        f'{ICONS.get(t, "tab")}</span>{escape(t)}</button>')
    blocks = [(name, [t for t in group if t in tabs]) for name, group in groups] if groups else [(None, tabs)]
    return CSS + '<nav aria-label="화면 바로가기">' + ''.join(
        (f'<div class="nav-group">{escape(name)}</div>' if name else '') + '<div class="nav-grid">' + ''.join(map(button, items)) + '</div>'
        for name, items in blocks if items) + '</nav>'


def tab_groups_css(groups):
    """A thin divider and gap before the first tab of each later group in the main tab bar (a tab bar has no group titles)."""
    starts, n = [], 0
    for _, tabs in groups:
        if n:
            starts.append(n + 1)
        n += len(tabs)
    sel = lambda i: f'.st-key-main_tab > div > [role="tablist"] > [role="tab"]:nth-of-type({i})'  # not the tabs nested in a tab
    return ('<style>' + ','.join(sel(i) for i in starts) + '{margin-left:22px;position:relative}'
            + ','.join(sel(i) + '::before' for i in starts)
            + "{content:'';position:absolute;left:-12px;top:22%;height:56%;border-left:1px solid rgba(128,128,128,.45)}</style>")


def stat_html(label, value, color):
    return (f'<div class="side-stat"><span class="dot" style="background:{color}" aria-hidden="true"></span>'
            f'{escape(label)}<span class="v">{escape(value)}</span></div>')


def render(tabs, states, records, alerts, engines=None):
    """Status block (each line has a detail pop-up) and the navigation grid."""
    import streamlit as st
    import dialogs as dlg
    green, gray, orange, red = '#22c55e', '#64748b', '#f59e0b', '#ef4444'
    runner = states.get('공유 모의 ETF 실행기', '확인 불가')
    live = {k: r for k, r in records.items() if r.get('kind') == 'live'}
    # a long-term account has its own executor (user 2026-11-17); the line counts both kinds
    mirrors = [states.get(f"실계좌 {k[-1]} {'장기투자' if r.get('engine') == 'longterm' else '미러'} 실행기") for k, r in live.items()]
    running = sum(1 for s in mirrors if s == '실행 중')
    armed = sum(1 for r in live.values() if r.get('live_orders') is True)
    st.markdown(CSS + (stat_html('운영 엔진', engines, gray if engines == '모두 끔' else green) if engines else '')
                + stat_html('모의 실행기', runner, green if runner == '실행 중' else gray if runner == '확인 중' else red  # 종료 is normal unless alerts() flags it
                            if runner != '종료' or any(a.startswith('공유 모의 ETF 실행기:') for a in alerts) else gray)
                + stat_html('실계좌 실행기', '확인 중' if '확인 중' in mirrors else f'{running}/{len(live)} 실행',
                            green if live and running == len(live) else gray if not running else orange)
                + stat_html('실주문 스위치', f'{armed}개 켜짐' if armed else '모두 꺼짐', orange if armed else gray)
                + stat_html('주의 항목', f'{len(alerts)}건', red if alerts else green), unsafe_allow_html=True)
    a, b = st.columns(2)
    if a.button('자동 작업', key='side_d_services', icon=':material/list_alt:', width='stretch',
                help='모든 자동 실행기의 상태와 일정을 봅니다.'):
        dlg.services_dialog(states)
    if b.button('주의 항목', key='side_d_alerts', icon=':material/warning:', width='stretch',
                help='종합 현황의 주의 항목과 같은 목록입니다.'):
        dlg.table_dialog('주의 항목', [{'내용': text} for text in alerts], icon=':material/warning:', empty='지금 주의할 항목이 없습니다.')
    st.markdown('**바로가기**')
    import prefs
    st.markdown(nav_html(tabs, prefs.GROUPS) + tab_groups_css(prefs.GROUPS), unsafe_allow_html=True)
    st.iframe(SCRIPT, height=1)  # no visible content (0 is refused, 'content' takes 150px); components.html is deprecated



def open_trading_chart(engine, symbol, *, show_evidence=False):
    """Open the shared trading view and request the selected engine's chart."""
    import streamlit as st

    st.session_state['trading_account'] = 'paper-shared'
    st.session_state['trading_account_tab'] = 'paper-shared'
    st.session_state['main_tab'] = '트레이딩 현황'
    st.session_state['pending_chart'] = {'engine': engine, 'code': symbol}
    if show_evidence:
        st.session_state['etf_desk_tab'] = 'Jev 판단 근거'
        st.session_state['paper_desk_detail_reset'] = symbol


def open_trade_history(engine, symbol):
    """Open this engine's confirmed fills for the selected symbol."""
    import streamlit as st

    st.session_state['main_tab'] = '운영'
    st.session_state['ops_view'] = 'AI·실행 기록'
    st.session_state['record_source'] = engine
    st.session_state['trade_history'] = {'engine': engine, 'code': symbol}


def positions_panel(runtime, names):
    import streamlit as st
    import trading_positions

    data = trading_positions.snapshot(runtime)
    st.markdown('**엔진별 종목 현황**')
    st.caption('엔진 원장 기준 · 오늘 확인된 매도 · 수수료·세금 미반영')
    for engine, label in (('jev', 'Jev'),):  # the intraday vibe book is frozen (2026-11-17): long-term summary below
        item = data[engine]
        st.markdown(f"**{label}**")
        if item['status'] != 'ok':
            st.warning('원장을 확인할 수 없습니다.')
            continue
        if not item.get('holdings'):
            st.caption('보유 없음')
        if not item.get('sells', item.get('stops')):
            st.caption('오늘 매도 없음')
        for group in trading_positions.grouped_symbols(item):
            code = group['symbol']
            if st.button(trading_positions.group_label(group, names),
                         key=f'side_position_{engine}_{code}', width='stretch',
                         on_click=open_trade_history, args=(engine, code), icon=':material/receipt_long:',
                         help='이 엔진의 종목별 확인 체결 내역을 봅니다.'):
                st.rerun()
        if item.get('unknown_sells'):
            st.caption(f"매도 사유 미확인 {item['unknown_sells']}건")
    longterm_panel(runtime)


def open_longterm():
    import streamlit as st
    st.session_state['main_tab'] = '장기투자'


def longterm_side_lines(state):
    """Two short lines: the virtual account against the base shadow (기준 비중+달리오), and theme stories that are not intact."""
    import vibe_longterm as LT
    nav, lines = state.get('nav_latest') or {}, []
    if nav.get('actual') and nav.get('base'):
        gap = nav['actual'] - nav['base']  # the shadows receive the same 적립식 money, so the gap is like for like
        paid = f" · 적립 누계 {nav[LT.CONTRIBUTED] / 1e4:,.0f}만" if nav.get(LT.CONTRIBUTED) else ''
        lines.append(f"순자산 {nav['actual'] / 1e8:,.3f}억{paid} · 기본 비교 전략 대비 {gap:+,.0f}원 ({nav.get('day', '')})")
    stories = (state.get('story') or {}).get('stories') or {}
    warn = [f"{LT.ASSETS[k][1].split(' ', 1)[-1]} {LT.STORY_LABELS[v['status']]}" for k, v in stories.items()
            if k in LT.ASSETS and v.get('status') in ('watch', 'broken')]
    if warn:
        lines.append('스토리 ⚠ ' + ' · '.join(warn))
    return lines


def longterm_panel(runtime):
    """장기투자 (vibe): today's regime and plan with the three largest target weights against the current ones."""
    import streamlit as st
    from pathlib import Path
    st.markdown('**장기투자**')
    import vibe_longterm as LT
    try:
        state = LT.read_state(Path(runtime) / 'vibe-longterm', ('plan', 'last_plan', 'nav_latest', 'story'))
    except Exception:  # a dashboard panel: an unreadable record must not break the sidebar
        state = {}
    if not state:
        st.caption('기록 전 · 평일 08:45 첫 판단')
        return
    plan, last = state.get('plan') or {}, state.get('last_plan') or {}
    st.caption(f"{LT.REGIMES.get(plan.get('regime'), ('판정 전',))[0]} 국면 · {plan.get('day', '')}" + (f" · {LT.why_label(last.get('why'))}" if last.get('why') else ''))
    want, now = last.get('want') or {}, last.get('weights') or {}
    try:  # the reservoir ETF the virtual book holds (CD금리 since 11-20), not always KOFR
        held = {r['symbol']: r['after'] for r in LT.journal_rows(Path(runtime) / 'vibe-longterm')}
    except Exception:
        held = {}
    res = next((c for c, q in held.items() if c in LT.RESERVOIRS and q > 0), None)
    for key in sorted(want, key=lambda k: -want[k])[:3]:
        st.caption(f"{LT.asset_name(key, res)} · 현재 {now.get(key, 0) * 100:.0f}% / 목표 {want[key] * 100:.0f}%")  # NDXMIX: 여운봉 매매법
    for line in longterm_side_lines(state):
        st.caption(line)
    import vibe_longterm_view as LV
    if near := LV.nearest(plan.get('levels') or []):
        st.caption(f"가장 가까운 타점 · {LT.asset_name(near['asset'])} {LV.SIDE[near['side']]} {near['price']:,.0f} ({near['distance'] * 100:+.1f}%)")
    st.button('장기투자 탭 열기', key='side_longterm', on_click=open_longterm, icon=':material/savings:', width='stretch')
