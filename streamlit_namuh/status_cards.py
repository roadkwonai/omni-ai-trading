"""Status cards for the top of each tab: colour, icon and a pulsing dot say the state before the text is read.

Tones follow the Korean market convention (up red, down blue) and add green for "working as it should", amber for
"needs attention", rose with a blinking dot for "stopped or failing", violet for AI opinions and gray for "off".
Every card also carries the state in words and an icon, so colour is never the only signal. The dot pulses slowly
(1.6 s, well under 3 flashes per second); the sidebar switch and the OS "reduce motion" setting both stop it.
"""
from __future__ import annotations

from html import escape
from math import isfinite

import streamlit as st

TONES = {  # name: (accent colour, card tint)
    'up': ('#ef4444', 'rgba(239,68,68,0.10)'),       # 상승 · 강세 · 수익 · 매수
    'down': ('#3b82f6', 'rgba(59,130,246,0.10)'),     # 하락 · 약세 · 손실 · 매도
    'ok': ('#22c55e', 'rgba(34,197,94,0.09)'),        # 정상 · 실행 중 · 허용
    'warn': ('#f59e0b', 'rgba(245,158,11,0.10)'),     # 주의 · 혼조 · 기준 미달
    'alert': ('#f43f5e', 'rgba(244,63,94,0.14)'),     # 중단 · 오류 · 확인 불가
    'ai': ('#a855f7', 'rgba(168,85,247,0.10)'),       # Jev · vibe 의견
    'off': ('#64748b', 'rgba(100,116,139,0.08)'),     # 꺼짐 · 없음 · 기록 없음
}

CSS = """
<style>
.sc{position:relative;border:1px solid rgba(148,163,184,.22);border-left:4px solid var(--a);border-radius:10px;
    background:linear-gradient(135deg,var(--t),var(--om-card-end,rgba(14,18,35,.6)));padding:12px 14px 10px;min-height:96px;margin-bottom:8px}
.sc-head{display:flex;align-items:center;gap:6px;color:var(--om-ink-2,#cbd5e1);font-size:.82rem;font-weight:600}
.sc-help{margin-left:auto;font-size:16px;color:#64748b;cursor:help}
.sc-value{display:flex;align-items:center;gap:8px;color:var(--a);font-size:1.55rem;font-weight:700;line-height:1.25;
          margin-top:4px;word-break:keep-all;overflow-wrap:anywhere}
.sc-value .ms{font-size:1.6rem}
.sc-sub{color:var(--om-muted,#94a3b8);font-size:.8rem;margin-top:4px;word-break:keep-all}
.sc-sub.sc-signed{color:var(--a);font-weight:600}
.ms{font-family:'Material Symbols Rounded';font-weight:normal;font-style:normal;line-height:1;letter-spacing:normal;
    text-transform:none;white-space:nowrap;-webkit-font-feature-settings:'liga';font-feature-settings:'liga'}
.sc-dot{width:9px;height:9px;border-radius:50%;background:var(--a);flex:none;box-shadow:0 0 0 0 var(--a)}
.sc-blink .sc-dot.sc-pulse{animation:sc-pulse 1.6s ease-in-out infinite}
.sc-blink .sc-alert-ring{animation:sc-ring 1.6s ease-in-out infinite}
@keyframes sc-pulse{0%{opacity:1;box-shadow:0 0 0 0 var(--a)}60%{opacity:.35;box-shadow:0 0 0 6px transparent}100%{opacity:1}}
@keyframes sc-ring{0%,100%{border-color:rgba(148,163,184,.22)}50%{border-color:var(--a)}}
@media (prefers-reduced-motion: reduce){.sc-blink .sc-dot.sc-pulse,.sc-blink .sc-alert-ring{animation:none}}
</style>
"""


def inject():
    """Once per run (Streamlit drops elements from earlier runs, so each run adds its own copy)."""
    st.markdown(CSS, unsafe_allow_html=True)


def html(label, value, tone='off', icon=None, sub=None, help=None, pulse=False, signed_sub=False):
    accent, tint = TONES.get(tone, TONES['off'])
    blink = st.session_state.get('status_blink', True)
    pulse = pulse or tone == 'alert'  # a stopped or failing state always draws the eye
    ring = ' sc-alert-ring' if tone == 'alert' else ''
    tip = f'<span class="ms sc-help" title="{escape(help)}" aria-label="{escape(help)}">help</span>' if help else ''
    mark = f'<span class="ms" aria-hidden="true">{escape(icon)}</span>' if icon else ''
    return (f'<div class="{"sc-blink" if blink else ""}"><div class="sc{ring}" style="--a:{accent};--t:{tint}" role="group" '
            f'aria-label="{escape(label)}: {escape(str(value))}{(" · " + escape(sub)) if sub else ""}">'
            f'<div class="sc-head"><span class="sc-dot{" sc-pulse" if pulse else ""}"></span>{escape(label)}{tip}</div>'
            f'<div class="sc-value">{mark}<span>{escape(str(value))}</span></div>'
            + (f'<div class="sc-sub{" sc-signed" if signed_sub else ""}">{escape(sub)}</div>' if sub else '') + '</div></div>')


def card(container, label, value, tone='off', icon=None, sub=None, help=None, pulse=False, signed_sub=False):
    """signed_sub: the sub line is a profit/loss and takes the tone's colour (up red, down blue)."""
    container.markdown(html(label, value, tone, icon, sub, help, pulse, signed_sub), unsafe_allow_html=True)


def row(items, columns=None, ask=None):
    """items: dicts of card() arguments; one row of equal columns. ask: a key that adds 'AI에게 묻기' chips below the row."""
    for col, item in zip(st.columns(columns or len(items)), items):
        card(col, **item)
    if ask:
        import ask_ai
        ask_ai.card_questions(items, ask)


# ── meaning → tone/icon ─────────────────────────────────────────────────────
def regime(label):
    return {'상승장': ('up', 'trending_up'), '하락장': ('down', 'trending_down'), '횡보장': ('warn', 'trending_flat')}.get(label, ('off', 'help'))


def service(state):
    """(tone, icon, pulse) for a launchd state."""
    return {'실행 중': ('ok', 'play_circle', True), '종료': ('off', 'stop_circle', False),
            '미등록': ('warn', 'help', False)}.get(state, ('alert', 'error', True))


def signed(value):
    return 'up' if value > 0 else 'down' if value < 0 else 'off'


def signed_percent(value):
    """Format small balance changes without rounding a nonzero move to signed zero."""
    if value is None or not isfinite(value):
        return '—'
    if value == 0:
        return '0.00%'
    magnitude = abs(value)
    if magnitude >= .01:
        return f'{value:+.2f}%'
    if magnitude >= .0001:
        return f'{value:+.4f}%'
    if magnitude >= .00000001:
        return f'{value:+.8f}%'
    return f'{value:+.2g}%'


UP_COLOR, DOWN_COLOR = '#ef4444', '#3b82f6'  # 국내 관례: 상승·매수 빨강, 하락·매도 파랑


def cell_tone(text):
    """Table cell CSS for a signed P&L/% string: profit red, loss blue, zero/blank plain."""
    text = str(text).strip()
    if text.startswith('+') and text.lstrip('+0.,원%'):
        return f'color:{UP_COLOR}'
    return f'color:{DOWN_COLOR}' if text.startswith(('-', '−')) else ''


def side_tone(text):
    """Table cell CSS for a buy/sell label: 매수 red, 매도 blue."""
    text = str(text)
    return f'color:{UP_COLOR}' if '매수' in text else f'color:{DOWN_COLOR}' if '매도' in text else ''
