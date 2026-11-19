"""One-button dashboard theme: 시스템 → 다크 → 라이트 (display only).

Streamlit draws its widgets, tables and Altair charts from the [theme.light]/[theme.dark] pair in .streamlit/config.toml and
picks one from the browser's localStorage; Archify diagrams read their own `archify-theme` key. `script()` writes the saved
choice to both and reloads the page once when Streamlit's value changed. `css()` gives the app's own HTML cards the light
tokens (the CSS falls back to the dark values)."""
from __future__ import annotations

import json

import streamlit as st

MODES = ('system', 'dark', 'light')
LABELS = {'system': ('시스템 테마', 'brightness_auto'), 'dark': ('다크 테마', 'dark_mode'), 'light': ('라이트 테마', 'light_mode')}
STREAMLIT = {'system': 'System', 'dark': 'Dark', 'light': 'Light'}  # the values Streamlit's frontend stores
LIGHT = (':root{--om-ink:#0f172a;--om-ink-2:#334155;--om-muted:#475569;--om-card-end:rgba(241,245,249,.92)}'
         # Graphviz labels drawn on the page itself (clusters, edges); filled nodes keep their own dark fill and light text
         '.stGraphVizChart g.cluster>text,.stGraphVizChart g.edge text{fill:#334155}'
         # accent-coloured card text (amber, green …) is too pale on a light card: darken it, keep the hue
         '.sc-value{color:color-mix(in srgb,var(--a) 72%,#000)}.sv-title,.sv-head,.sv-chip{color:color-mix(in srgb,var(--c) 72%,#000)}')


def next_mode(mode):
    return MODES[(MODES.index(mode) + 1) % len(MODES)] if mode in MODES else MODES[0]


def css(mode):
    if mode == 'light':
        return LIGHT
    return '@media (prefers-color-scheme: light){' + LIGHT + '}' if mode == 'system' else ''


def script(mode):
    want = json.dumps({'st': STREAMLIT[mode], 'archify': None if mode == 'system' else mode})
    return """<script>(() => {
  const want = %s, win = window.parent || window, ls = win.localStorage;
  const key = `stActiveTheme-${win.location.pathname}-v2`, value = JSON.stringify(want.st);
  try {
    if (want.archify) ls.setItem('archify-theme', want.archify); else ls.removeItem('archify-theme');
    if (ls.getItem(key) === value) return;
    ls.setItem(key, value);
    if (ls.getItem(key) !== value) return;  // storage blocked: never reload in a loop
    // Streamlit reads its theme only at page load; keep the tab the user is on across the reload
    const tab = win.document.querySelector('.st-key-main_tab [role="tablist"] > [role="tab"][aria-selected="true"]');
    const url = new URL(win.location.href);
    if (tab) url.searchParams.set('tab', tab.innerText.trim());
    win.location.replace(url);
  } catch (e) {}
})();</script>""" % want


def is_light():
    """The theme the page is drawn with now (system mode resolves to the OS setting)."""
    return (st.context.theme or {}).get('type') == 'light'


def ink(dark='#e2e8f0'):
    """Text/line colour for Altair marks drawn on the page background."""
    return '#334155' if is_light() else dark


CHART = {'dark': {'background': '#101827', 'text': '#d4dbea', 'grid': '#253043', 'line': '#e2e8f0'},
         'light': {'background': '#ffffff', 'text': '#334155', 'grid': '#e2e8f0', 'line': '#334155'}}


def chart_colors():
    """TradingView chart background, text, grid and the cost-basis line for the active theme."""
    return CHART['light' if is_light() else 'dark']


# Graphviz diagrams are drawn with dark node fills and pale text; on a light page they become light cards.
DOT_LIGHT = {'#0E1223': '#FFFFFF', '#1E293B': '#FFFFFF', '#E2E8F0': '#0F172A', '#94A3B8': '#475569',
             '#C4B5FD': '#6D28D9', '#93C5FD': '#1D4ED8'}


def dot(src):
    """The Graphviz source recoloured for the active theme (unchanged in dark)."""
    if not is_light():
        return src
    import re
    return re.sub(r'#[0-9A-Fa-f]{6}', lambda m: DOT_LIGHT.get(m.group(0).upper(), m.group(0)), src)


def graphviz(src, **kwargs):
    """st.graphviz_chart in the active theme's colours."""
    return st.graphviz_chart(dot(src), **kwargs)
