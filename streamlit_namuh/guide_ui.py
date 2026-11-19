"""One template for every guide pop-up (user request 2026-11-18): a summary card on top, section titles, full-width tables
that wrap, numbered step cards, diagrams in a captioned frame and tone notes, so every guide reads the same way in light
and dark themes. The pop-up frame itself (title, tabs, width) is dialogs.CSS, shared by every pop-up. Text may carry **bold** and `code`; everything else is escaped (no HTML from the content)."""
from __future__ import annotations

import html
import re

import streamlit as st

CSS = """<style>
.gd-lead{border:1px solid rgba(139,92,246,.35);border-left:5px solid #8b5cf6;background:rgba(139,92,246,.08);border-radius:12px;
  padding:14px 18px;margin:4px 0 16px;font-size:15px;line-height:1.65}
.gd-lead .gd-k{display:block;font-size:12px;font-weight:700;letter-spacing:.04em;color:#8b5cf6;margin-bottom:4px}
.gd-sec{margin:18px 0 8px}.gd-sec h4{margin:0;font-size:16px;font-weight:700}.gd-sec p{margin:2px 0 0;font-size:13px;color:var(--om-muted,#64748b)}
.gd-table{width:100%;border-collapse:separate;border-spacing:0;font-size:14px;line-height:1.55;margin:6px 0 14px;
  border:1px solid rgba(148,163,184,.30);border-radius:10px;overflow:hidden}
.gd-table th{background:rgba(148,163,184,.16);font-weight:700;text-align:left;padding:9px 12px;white-space:nowrap;
  border-bottom:1px solid rgba(148,163,184,.30)}
.gd-table td{padding:9px 12px;vertical-align:top;border-bottom:1px solid rgba(148,163,184,.16)}
.gd-table tr:last-child td{border-bottom:none}.gd-table tbody tr:nth-child(even) td{background:rgba(148,163,184,.06)}
.gd-table td:first-child{font-weight:600;white-space:nowrap}
.gd-table code,.gd-lead code,.gd-step code,.gd-note code{font-size:12.5px;padding:1px 5px;border-radius:5px;background:rgba(148,163,184,.18)}
.gd-steps{display:grid;gap:8px;margin:6px 0 14px}
.gd-step{display:flex;gap:12px;align-items:flex-start;border:1px solid rgba(148,163,184,.28);border-radius:10px;padding:10px 14px}
.gd-step .gd-n{flex:0 0 26px;height:26px;border-radius:50%;background:#8b5cf6;color:#fff;font-weight:700;font-size:13px;
  display:flex;align-items:center;justify-content:center}
.gd-step .gd-t{font-weight:700;margin-bottom:2px}.gd-step .gd-d{font-size:14px;line-height:1.55;opacity:.9}
.gd-note{border-radius:10px;padding:10px 14px;margin:6px 0 12px;font-size:14px;line-height:1.55;border:1px solid}
.gd-note.info{background:rgba(59,130,246,.08);border-color:rgba(59,130,246,.35)}
.gd-note.warn{background:rgba(245,158,11,.10);border-color:rgba(245,158,11,.45)}
.gd-note.ok{background:rgba(34,197,94,.08);border-color:rgba(34,197,94,.38)}
.gd-cap{font-size:12.5px;color:var(--om-muted,#64748b);margin:-4px 0 12px}
</style>"""


def _inline(text):
    """Escape, then **bold** → <b>, `code` → <code>, newlines → <br>."""
    out = html.escape(str(text))
    out = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', out)
    out = re.sub(r'`([^`]+)`', r'<code>\1</code>', out)
    return out.replace('\n', '<br>')


def _html(markup):
    st.markdown(markup, unsafe_allow_html=True)


def style():
    _html(CSS)


def lead(text, label='한눈에'):
    """The summary card a guide opens with: what this pop-up answers, in two or three sentences."""
    _html(f'<div class="gd-lead"><span class="gd-k">{html.escape(label)}</span>{_inline(text)}</div>')


def section(title, caption=None):
    _html(f'<div class="gd-sec"><h4>{_inline(title)}</h4>' + (f'<p>{_inline(caption)}</p>' if caption else '') + '</div>')


def table(rows, head=None):
    """A full-width table that wraps long text: rows are dicts (head = their keys) or sequences with `head`."""
    rows = list(rows)
    if not rows:
        return
    if head is None:
        head = list(rows[0])
    cells = [[r[h] for h in head] if isinstance(r, dict) else list(r) for r in rows]
    _html('<table class="gd-table"><thead><tr>' + ''.join(f'<th>{_inline(h)}</th>' for h in head) + '</tr></thead><tbody>'
          + ''.join('<tr>' + ''.join(f'<td>{_inline(c)}</td>' for c in row) + '</tr>' for row in cells) + '</tbody></table>')


def steps(items):
    """Numbered step cards: items are (title, text) or plain text."""
    def card(item):
        t, d = item if isinstance(item, (tuple, list)) else (None, item)
        return (f'<div class="gd-t">{_inline(t)}</div>' if t else '') + f'<div class="gd-d">{_inline(d)}</div>'
    _html('<div class="gd-steps">' + ''.join(f'<div class="gd-step"><div class="gd-n">{i}</div><div>{card(item)}</div></div>'
                                             for i, item in enumerate(items, 1)) + '</div>')


def note(text, tone='info'):
    _html(f'<div class="gd-note {tone if tone in ("info", "warn", "ok") else "info"}">{_inline(text)}</div>')


def caption(text):
    _html(f'<div class="gd-cap">{_inline(text)}</div>')


def diagram(dot, text=None):
    """A Graphviz diagram (through the theme) in a bordered frame with its caption under it."""
    import theme as THEME
    with st.container(border=True):
        THEME.graphviz(dot, width='stretch')
    if text:
        caption(text)


def open(title, parts, icon=':material/menu_book:', focus=None):
    """A guide pop-up: tabs from parts [(label, render)], the one whose label contains `focus` opened first."""
    import dialogs

    def body():
        style()
        names = [p[0] for p in parts]
        default = next((n for n in names if focus and focus in n), None)
        for tab, (_, fn) in zip(st.tabs(names, default=default), parts):
            with tab:
                fn()
    dialogs.open_dialog(title, body, icon=icon)


# ---------- Archify flows (user rule 2026-11-18: a process a reader follows step by step is drawn with Archify) ----------

LEGEND = {'backend': '처리·규칙', 'security': '판단·관문', 'database': '기록·자료', 'cloud': 'AI·분석', 'external': '외부·대기',
          'messagebus': '주기·주문', 'frontend': '설정'}  # node type → the Korean legend word


def workflow(title, lanes, nodes, edges, main=None, phases=()):
    """A compact Archify workflow (schema v2): lanes (id, label[, 'exception']); nodes (id, lane, col 0-5, label, sublabel, type,
    tag); edges (from, to, label, role) with role main · branch · async · return · error. `main` is the highlighted path."""
    def node(row):
        nid, lane, col, label, *rest = row
        sub, kind, tag = (list(rest) + ['', 'backend', ''][len(rest):])[:3]
        return {'id': nid, 'lane': lane, 'col': col, 'type': kind or 'backend', 'label': label,
                **({'sublabel': sub} if sub else {}), **({'tag': tag} if tag else {})}

    def edge(row):
        a, b, label, role = (*row, None, None)[:4]
        return {'from': a, 'to': b, **({'label': label} if label else {}), **({'role': role} if role else {})}
    built = [node(n) for n in nodes]
    legend = {t: {'label': LEGEND[t]} for t in dict.fromkeys(n['type'] for n in built) if t in LEGEND}
    spec = {'schema_version': 2, 'diagram_type': 'workflow', 'meta': {'title': title, 'output': 'flow.html', 'quality_profile': 'standard',
                                                                       'locale': 'ko-KR', 'animation': 'trace', 'legend': {'entries': legend}},
            'lanes': [{'id': l[0], 'label': l[1], **({'variant': l[2]} if len(l) > 2 else {})} for l in lanes],
            'nodes': built, 'edges': [edge(e) for e in edges]}
    if main:
        spec['mainPath'] = list(main)
    if phases:
        spec['phases'] = [{'id': f'p{i}', 'label': label, 'fromCol': a, 'toCol': b, 'variant': 'emphasis' if i == 0 else 'dashed'}
                          for i, (label, a, b) in enumerate(phases)]
    return spec


def flow(key, spec, fallback=None, text=None):
    """Draw an Archify diagram (cached by content under nhplug/diagrams); when node or Archify is missing, or the render fails,
    show `fallback` (Graphviz dot) instead so the screen never breaks."""
    import strategy_map as smap
    from src.config.paths import get_runtime_root
    try:
        html = smap.render(spec, get_runtime_root() / 'nhplug' / 'diagrams', kind=spec['diagram_type'], prefix=f'guide-{key}')
        st.iframe(html, alt=spec['meta']['title'])  # height 'content': the whole diagram, no inner scroll
    except Exception as exc:  # noqa: BLE001 - any render failure falls back to the plain diagram
        if fallback is None:
            raise
        diagram(fallback)
        caption(f'Archify를 쓸 수 없어 기본 도식으로 표시합니다({type(exc).__name__}).')
    if text:
        caption(text)
