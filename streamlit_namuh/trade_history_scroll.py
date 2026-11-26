"""Virtualized, incremental list of confirmed account trades."""
from __future__ import annotations

import streamlit as st

_COMPONENT = st.components.v2.component(
    'trade_history_scroll',
    html='''<div class="trade-list"><div class="header" role="row"><span>일시 · 기준</span><span>계좌</span><span>종목</span><span>구분</span><span>수량</span><span>평균체결가</span><span>체결금액</span><span>실현손익</span><span>수익률</span><span>상세</span></div><div class="viewport" role="region" aria-label="매매내역 목록" tabindex="0"><div class="space"><div class="window"></div></div></div><div class="footer"><span role="status"></span><button class="more" type="button">이전 거래 더 보기</button></div></div>''',
    css='''
.trade-list {font: 14px var(--st-font, sans-serif); color:var(--st-text-color); font-variant-numeric:tabular-nums;
  --trade-up: color-mix(in srgb, #ed454d 82%, var(--st-text-color)); --trade-down:color-mix(in srgb, #3b82f6 82%, var(--st-text-color))}
.header,.row {display:grid; grid-template-columns:160px 95px 110px 55px 55px 105px 110px 110px 80px 55px; align-items:center; gap:8px; min-width:1030px; padding:0 8px; box-sizing:border-box}
.header {height:38px; min-width:0; width:100%; background:var(--st-secondary-background-color); font-weight:600; overflow:hidden}
.viewport {height:440px; overflow:auto; border:1px solid var(--st-border-color); position:relative; scrollbar-gutter:stable}
.space {position:relative; min-width:1030px}
.window {position:absolute; top:0; left:0; right:0}
.row {height:46px; border-bottom:1px solid var(--st-border-color)}
.loading-row {height:46px; padding:12px 8px; box-sizing:border-box; color:var(--st-text-color); opacity:.7}
.row span {overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
.row .buy, .row .profit {color:var(--trade-up)}
.row .sell, .row .loss {color:var(--trade-down)}
.row .buy, .row .sell {justify-self:start; padding:2px 7px; border-radius:5px; font-weight:650;
  background:color-mix(in srgb, currentColor 10%, transparent)}
.row .profit, .row .loss {font-weight:650}
.row .stamp {display:grid; gap:1px; font-size:.9em; line-height:1.25}
.row .stamp small {color:var(--st-text-color); opacity:.72; font-size:.85em}
.row button,.footer button {font:inherit; color:inherit; background:var(--st-secondary-background-color); border:1px solid var(--st-border-color); border-radius:5px; padding:4px 6px; cursor:pointer}
.detail {height:90px; min-width:1030px; box-sizing:border-box; padding:8px 12px; background:var(--st-secondary-background-color); border-bottom:1px solid var(--st-border-color); display:flex; flex-wrap:wrap; gap:4px 22px; align-content:center}
.detail span {white-space:nowrap}
.footer {display:flex; justify-content:space-between; align-items:center; gap:8px; padding:8px 0; min-height:42px}
.footer span {color:var(--st-text-color)}
button[hidden] {display:none}
''',
    js=r'''
export default function ({data, parentElement, setTriggerValue}) {
    const root = parentElement.querySelector('.trade-list');
    const viewport = root.querySelector('.viewport');
    const header = root.querySelector('.header');
    const space = root.querySelector('.space');
    const windowNode = root.querySelector('.window');
    const status = root.querySelector('[role=status]');
    const more = root.querySelector('.more');
    let state = root._tradeState;
    if (!state || state.query !== data.query_id) {
        state = root._tradeState = {query:data.query_id, pages:new Map(), highWater:0, expanded:null, pending:null, version:0};
        viewport.scrollTop = 0;
        viewport.scrollLeft = 0;
        header.scrollLeft = 0;
    }
    state.total = Number(data.total);
    state.trigger = setTriggerValue;
    const pageSize = data.page_size;
    const firstPageSize = data.first_page_size ?? pageSize;
    const maxPages = pageSize > firstPageSize ? 2 : 6;
    const pageOffset = index => index < firstPageSize ? 0 :
        firstPageSize + Math.floor((index - firstPageSize) / pageSize) * pageSize;
    if (data.rows.length && (data.offset === state.highWater || data.offset < state.highWater)) {
        state.pages.set(data.offset, data.rows);
        state.highWater = Math.max(state.highWater, data.offset + data.rows.length);
        state.version++;
        if (state.pending && state.pending.offset === data.offset && state.pending.requestId === data.request_id) {
            state.pending = null;
            clearTimeout(state.retryTimer);
        }
        const current = Math.floor(viewport.scrollTop / 46);
        const distance = offset => {
            const end = offset + state.pages.get(offset).length - 1;
            return current < offset ? offset - current : current > end ? current - end : 0;
        };
        while (state.pages.size > maxPages) {
            const farthest = [...state.pages.keys()].reduce((a, b) =>
                distance(a) >= distance(b) ? a : b);
            state.pages.delete(farthest);
        }
    }
    const number = value => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('ko-KR', {maximumFractionDigits:2}) : '—';
    const signed = value => typeof value === 'number' && Number.isFinite(value) ? (value > 0 ? '+' : '') + number(value) : '—';
    const text = value => value === null || value === undefined || value === '' ? '—' : String(value);
    const resultTone = value => value > 0 ? 'profit' : value < 0 ? 'loss' : '';
    const yAt = index => index * 46 + (state.expanded !== null && state.expanded < index ? 90 : 0);
    function sendPending() {
        const pending = state.pending;
        if (!pending) return;
        pending.requestId = Date.now() + Math.random();
        pending.attempts++;
        pending.deadline = performance.now() + 4000;
        state.trigger('next_offset', {query_id:state.query, offset:pending.offset, request_id:pending.requestId});
        armRetry();
    }
    function armRetry() {
        clearTimeout(state.retryTimer);
        if (!state.pending) return;
        state.retryTimer = setTimeout(() => {
            if (!state.pending) return;
            if (state.pending.attempts < 3) {
                sendPending();
            } else {
                state.pending = null;
                status.textContent = '불러오지 못했습니다. 이전 거래 더 보기를 누르세요.';
            }
        }, Math.max(0, state.pending.deadline - performance.now()));
    }
    function request(offset) {
        if (state.pending !== null || offset < 0 || offset >= state.total || offset > state.highWater || state.pages.has(offset)) return;
        state.pending = {offset, requestId:null, attempts:0};
        status.textContent = '이전 거래를 불러오는 중…';
        sendPending();
    }
    function render() {
        const count = state.highWater;
        space.style.height = `${yAt(count)}px`;
        // An expanded row changes offsets; adjust the first visible index before adding overscan.
        let first = Math.floor(viewport.scrollTop / 46);
        if (state.expanded !== null && yAt(state.expanded) < viewport.scrollTop) first -= 2;
        first = Math.max(0, Math.min(count, first - 8));
        let last = first;
        while (last < count && yAt(last) < viewport.scrollTop + viewport.clientHeight + 400) last++;
        windowNode.style.transform = `translateY(${yAt(first)}px)`;
        const windowKey = `${first}:${last}:${state.expanded}:${state.version}`;
        if (state.windowKey !== windowKey) {
            paint(first, last);
            state.windowKey = windowKey;
        }
        const total = Number.isFinite(state.total) ? state.total : count;
        const progress = state.pending !== null ? '이전 거래를 불러오는 중…' :
            `${count.toLocaleString('ko-KR')} / ${total.toLocaleString('ko-KR')}건 불러옴` + (count < total ? '' : ' · 마지막 기록');
        if (status.textContent !== progress) status.textContent = progress;
        more.hidden = count >= total && missingPage() === null;
    }
    function missingPage() {
        let index = Math.max(0, Math.floor(viewport.scrollTop / 46) - 3);
        if (state.expanded !== null && yAt(state.expanded) < viewport.scrollTop) index = Math.max(0, index - 2);
        const end = Math.min(state.highWater, index + Math.ceil(viewport.clientHeight / 46) + 6);
        for (; index < end; index++) {
            const offset = pageOffset(index);
            if (!state.pages.has(offset)) return offset;
        }
        return null;
    }
    function requestVisible() {
        const missing = missingPage();
        if (missing !== null) request(missing);
        else if (viewport.scrollTop + viewport.clientHeight >= viewport.scrollHeight - 160) request(state.highWater);
    }
    function paint(first, last) {
        const frag = document.createDocumentFragment();
        for (let i = first; i < last; i++) {
            const offset = pageOffset(i);
            const r = state.pages.get(offset)?.[i - offset];
            if (!r) {
                const placeholder = document.createElement('div');
                placeholder.className = 'loading-row';
                if (state.expanded === i) placeholder.style.height = '136px';
                placeholder.textContent = '기록을 불러오는 중';
                frag.appendChild(placeholder);
                continue;
            }
            const row = document.createElement('div');
            row.className = 'row';
            row.setAttribute('role', 'row');
            const clock = text(r.time).replace('T',' ').slice(0, 19);
            const stamp = document.createElement('span');
            stamp.className = 'stamp';
            stamp.setAttribute('role', 'cell');
            stamp.title = clock + ' · ' + text(r.time_basis);
            stamp.textContent = clock;
            const basis = document.createElement('small');
            basis.textContent = text(r.time_basis);
            stamp.appendChild(basis);
            row.appendChild(stamp);
            const values = [
                [text(r.account), ''], [text(r.symbol), ''],
                r.side === 'BUY' ? ['매수', 'buy'] : r.side === 'SELL' ? ['매도', 'sell'] : [text(r.side), ''],
                [number(r.qty), ''], [number(r.price), ''], [number(r.amount), ''],
                [signed(r.pnl), resultTone(r.pnl)],
                [r.return_pct == null ? '—' : signed(r.return_pct) + '%', resultTone(r.return_pct)],
            ];
            for (const [value, tone] of values) {
                const span = document.createElement('span');
                span.textContent = value;
                span.title = value;
                if (tone) span.className = tone;
                span.setAttribute('role', 'cell');
                row.appendChild(span);
            }
            const button = document.createElement('button');
            button.type = 'button';
            button.textContent = state.expanded === i ? '접기' : '상세';
            button.setAttribute('aria-expanded', String(state.expanded === i));
            button.setAttribute('aria-label', `${text(r.symbol)} ${text(r.time)} 상세`);
            button.onclick = () => {state.expanded = state.expanded === i ? null : i; render(); windowNode.querySelector(`[data-index="${i}"]`)?.focus();};
            button.dataset.index = i;
            row.appendChild(button);
            frag.appendChild(row);
            if (state.expanded === i) {
                const detail = document.createElement('div');
                detail.className = 'detail';
                const facts = [['주문번호',r.order_no],['체결 수량',number(r.qty)],['평균체결가',number(r.price)],['매도분 취득원가',number(r.cost)],['시각 기준',r.time_basis],['수수료·세금','자료 없음']];
                for (const [label,value] of facts) {
                    const span = document.createElement('span');
                    span.textContent = `${label} ${text(value)}`;
                    detail.appendChild(span);
                }
                frag.appendChild(detail);
            }
        }
        windowNode.replaceChildren(frag);
    }
    let animation;
    viewport.onscroll = () => {
        if (animation !== undefined) return;
        animation = requestAnimationFrame(() => {
            animation = undefined;
            header.scrollLeft = viewport.scrollLeft;
            render();
            requestVisible();
        });
    };
    more.onclick = () => request(missingPage() ?? state.highWater);
    render();
    if (state.pending) armRetry();
    else if (!state.pages.has(0) && state.highWater === 0) request(0);
    else requestVisible();
    return () => {
        viewport.onscroll = null;
        more.onclick = null;
        cancelAnimationFrame(animation);
        clearTimeout(state.retryTimer);
    };
}
''',
)


def render(key, query_id, rows, offset, total, page_size, request_id=None, first_page_size=None):
    """Mount one page. `next_offset` carries the query ID and the requested starting offset."""
    return _COMPONENT(
        key=key,
        data={'query_id': query_id, 'rows': rows, 'offset': offset, 'total': total, 'page_size': page_size,
              'first_page_size': first_page_size, 'request_id': request_id},
        on_next_offset_change=lambda: None,
    )
