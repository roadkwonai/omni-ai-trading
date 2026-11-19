"""Readable report markup for the HTML pages (report_pages): callouts, highlights and data figures drawn as plain SVG.

Writers (journal, premarket, improvement, long-term strategy skills) keep writing markdown; these extras need no tool:
  > [!NOTE] / [!TIP] / [!IMPORTANT] / [!WARNING] / [!CAUTION] / [!SUMMARY]   callout boxes (Korean aliases 참고·팁·중요·주의·위험·요약)
  ==핵심 문장==                                                                   highlighted text
  ```chart  {"type": "bar"|"line", "title": ..., "unit": "%", "data": [...] }   a chart (spec in chart_block)
  a table with a 등락·수익률·손익·변화 column of signed numbers                    gets a diverging bar chart under it automatically
Flow diagrams (```archify-workflow etc.) are drawn by report_pages with the archify CLI. The idea of a JSON chart spec
follows ztd-charts; the drawing is a few lines of SVG here so the pages stay static, theme-aware and dependency-free.
"""
from __future__ import annotations

import html as htmllib
import json
import re

CALLOUTS = {'NOTE': ('note', '참고'), 'TIP': ('tip', '팁'), 'IMPORTANT': ('important', '중요'), 'WARNING': ('warning', '주의'),
            'CAUTION': ('caution', '위험'), 'SUMMARY': ('summary', '요약'),
            '참고': ('note', '참고'), '팁': ('tip', '팁'), '중요': ('important', '중요'), '주의': ('warning', '주의'),
            '위험': ('caution', '위험'), '요약': ('summary', '요약')}
ICONS = {  # 24px stroke icons, one per callout kind
    'note': '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    'tip': '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3Z"/>',
    'important': '<path d="M12 3l2.6 5.6 6.1.7-4.5 4.2 1.2 6L12 16.6 6.6 19.5l1.2-6L3.3 9.3l6.1-.7Z"/>',
    'summary': '<path d="M5 6h14M5 12h14M5 18h9"/>',
    'warning': '<path d="M12 3 2 20h20L12 3Z"/><path d="M12 10v4M12 17h.01"/>',
    'caution': '<circle cx="12" cy="12" r="9"/><path d="M8 8l8 8M16 8l-8 8"/>',
}
CHANGE_HEADER = re.compile(r'등락|수익률|손익|변화|증감|매수 대비|대비|change', re.I)
NUMBER = re.compile(r'([+−-])?\s?(\d[\d,]*(?:\.\d+)?)')


def callouts(body):
    """<blockquote><p>[!NOTE] title?…</p>…</blockquote> → an aside with an icon and a Korean label."""
    def one(m):
        key, rest = m.group(1).strip(), m.group(2)
        kind, label = CALLOUTS.get(key.upper()) or CALLOUTS.get(key) or ('note', '참고')
        first, _, after = rest.partition('\n')
        title = first.strip() or label
        icon = f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">{ICONS[kind]}</svg>'
        inner = f'<p>{after.lstrip()}' if after.strip() else ''
        return (f'<aside class="callout {kind}" role="note" aria-label="{label}"><p class="callout-title">{icon}{title}</p>'
                f'{inner}{m.group(3)}</aside>')
    pattern = r'<blockquote>\s*<p>\[!([A-Za-z]+|참고|팁|중요|주의|위험|요약)\]([^\n<]*(?:\n[^<]*)?)(.*?)</blockquote>'
    return re.sub(pattern, one, body, flags=re.S)


def marks(body):
    """==text== → <mark>text</mark>, outside <pre>/<code>."""
    parts = re.split(r'(<pre.*?</pre>|<code>.*?</code>)', body, flags=re.S)
    return ''.join(p if p.startswith(('<pre', '<code')) else re.sub(r'==(?=\S)(.+?)(?<=\S)==', r'<mark>\1</mark>', p) for p in parts)


def signed(text):
    """The first signed number in a cell (+1.2% → 1.2, −3,000원 → -3000), or None."""
    m = NUMBER.search(htmllib.unescape(re.sub(r'<[^>]+>', '', text or '')).replace('−', '-'))
    if not m:
        return None
    try:
        value = float(m.group(2).replace(',', ''))
    except ValueError:
        return None
    return -value if m.group(1) in ('-', '−') else value


def esc(text):
    return htmllib.escape(str(text), quote=True)


def bar_svg(rows, unit='', title=''):
    """Diverging bars (Korean colours: up red, down blue) for [(label, value)], drawn as HTML rows so they fit any
    width: label | track with a zero line | value. (Name kept from the SVG version; callers do not care.)"""
    rows = [(str(k), float(v)) for k, v in rows][:24]
    peak = max(abs(v) for _, v in rows) or 1
    has_neg, has_pos = any(v < 0 for _, v in rows), any(v > 0 for _, v in rows)
    zero = 50.0 if has_neg and has_pos else (100.0 if has_neg else 0.0)  # the track keeps side room for the value labels (CSS)
    span = 50.0 if has_neg and has_pos else 100.0
    fmt = lambda v: (f"{v:+,.2f}".rstrip('0').rstrip('.') if abs(v) < 1000 else f"{v:+,.0f}") + unit  # noqa: E731
    out = [f'<div class="bars" role="img" aria-label="{esc(title or "막대 그래프")}" style="--zero:{zero:.1f}%">']
    for label, v in rows:
        tone = 'up' if v > 0 else 'down' if v < 0 else 'flat'
        w = abs(v) / peak * span
        left = zero - w if v < 0 else zero
        out.append(f'<div class="bar-row"><span class="bar-label" title="{esc(label)}">{esc(label)}</span>'
                   f'<span class="bar-track"><i class="bar {tone}" style="left:{left:.2f}%;width:{max(w, 0.6):.2f}%"></i>'
                   f'<span class="bar-val {tone}" style="left:{(left if v < 0 else left + max(w, 0.6)):.2f}%">{esc(fmt(v))}</span></span></div>')
    out.append('</div>')
    return ''.join(out)


def line_svg(series, unit='', title=''):
    """Lines for [{"name", "points": [[x_label, y], ...]}] sharing one x axis (labels from the first series).
    Fully responsive: the lines are an SVG stretched to the box (strokes keep their width), the axis labels and end
    dots are HTML, so text stays its normal size on a phone and on a 4K screen."""
    import math
    series = [s for s in series if isinstance(s, dict) and s.get('points')][:4]
    xs = [str(p[0]) for p in series[0]['points']]
    ys = [float(p[1]) for s in series for p in s['points']]
    lo, hi = min(ys), max(ys)
    if hi == lo:
        hi, lo = hi + 1, lo - 1
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad
    X = lambda i, n: 100 * i / max(n - 1, 1)  # noqa: E731
    Y = lambda v: 100 * (1 - (v - lo) / (hi - lo))  # noqa: E731
    digits = max(0, min(3, 1 - math.floor(math.log10((hi - lo) / 4))))
    ticks = ''.join(f'<span style="top:{Y(lo + (hi - lo) * k / 4):.2f}%">{lo + (hi - lo) * k / 4:,.{digits}f}{esc(unit)}</span>' for k in range(5))
    grid = ''.join(f'<line class="gridline" x1="0" x2="100" y1="{25 * k}" y2="{25 * k}" vector-effect="non-scaling-stroke"/>' for k in range(5))
    lines, dots = [], []
    for n, s in enumerate(series):
        pts = s['points']
        lines.append(f'<polyline class="line s{n}" vector-effect="non-scaling-stroke" points="'
                     + ' '.join(f'{X(i, len(pts)):.2f},{Y(float(p[1])):.2f}' for i, p in enumerate(pts)) + f'"><title>{esc(s.get("name", ""))}</title></polyline>')
        last = pts[-1]
        dots.append(f'<i class="lc-dot d{n}" style="left:{X(len(pts) - 1, len(pts)):.2f}%;top:{Y(float(last[1])):.2f}%" '
                    f'title="{esc(s.get("name", ""))} {esc(last[0])} {float(last[1]):,.{digits}f}{esc(unit)}"></i>')
    step = max(1, math.ceil(len(xs) / 6))
    xlabels = ''.join(f'<span style="left:{X(i, len(xs)):.2f}%">{esc(xs[i])}</span>' for i in range(0, len(xs), step))
    colours = ('brand', 'info', 'ok', 'warn')
    legend = ''.join(f'<li><i style="background:var(--{colours[n]})"></i>{esc(s.get("name", ""))}</li>' for n, s in enumerate(series))
    return (f'<div class="linechart" role="img" aria-label="{esc(title or "선 그래프")}">'
            f'<div class="lc-y">{ticks}</div>'
            f'<div class="lc-plot"><svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">{grid}{"".join(lines)}</svg>{"".join(dots)}</div>'
            f'<div class="lc-x">{xlabels}</div></div>'
            + (f'<ul class="legend">{legend}</ul>' if len(series) > 1 else ''))


def figure(svg, title):
    return f'<figure class="figure">' + (f'<p class="figure-title">{esc(title)}</p>' if title else '') + f'{svg}</figure>'


def chart_block(source):
    """```chart JSON → a figure. {"type": "bar", "title", "unit", "data": [{"label", "value"}]} or
    {"type": "line", "title", "unit", "series": [{"name", "points": [["11-13", 1.2], ...]}]}. Bad JSON → a short note."""
    try:
        spec = json.loads(source)
        title, unit = str(spec.get('title') or ''), str(spec.get('unit') or '')
        if spec.get('type') == 'line':
            return figure(line_svg(spec['series'], unit, title), title)
        rows = [(d['label'], float(d['value'])) for d in spec['data']]
        return figure(bar_svg(rows, unit, title), title) if rows else ''
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        return f'<aside class="callout warning" role="note"><p class="callout-title">차트를 그리지 못했습니다</p><p>{esc(type(exc).__name__)}: chart 블록 형식을 확인하세요.</p></aside>'


def table_charts(body, limit=3):
    """After a table whose header has a change/return/P&L column of signed numbers (3+ rows), a diverging bar chart."""
    made = 0

    def one(m):
        nonlocal made
        table = m.group(0)
        heads = [htmllib.unescape(re.sub(r'<[^>]+>', '', h)).strip() for h in re.findall(r'<th[^>]*>(.*?)</th>', table, flags=re.S)]
        col = next((i for i, h in enumerate(heads) if i and CHANGE_HEADER.search(h)), None)
        if col is None or made >= limit:
            return table
        rows, texts = [], []
        for tr in re.findall(r'<tr>(.*?)</tr>', table.split('</thead>', 1)[-1], flags=re.S):
            cells = re.findall(r'<td[^>]*>(.*?)</td>', tr, flags=re.S)
            if len(cells) > col:
                value = signed(cells[col])
                label = htmllib.unescape(re.sub(r'<[^>]+>', '', cells[0])).strip()
                if value is not None and label:
                    rows.append((label, value))
                    texts.append(re.sub(r'<[^>]+>', '', cells[col]))
        if len(rows) < 3 or not any(v for _, v in rows):
            return table
        unit = '%' if all('%' in t for t in texts) else '원' if all('원' in t for t in texts) else ''
        made += 1
        title = f'{heads[col]} 한눈에 보기'
        return table + figure(bar_svg(rows, unit, title), title)
    return re.sub(r'<div class="table-wrap"><table>.*?</table></div>', one, body, flags=re.S)


STREAMLIT_TONE = {'note': 'blue', 'tip': 'green', 'important': 'orange', 'summary': 'orange', 'warning': 'orange', 'caution': 'red'}


def for_streamlit(text):
    """The same markdown for st.markdown on the dashboard: callouts become a coloured label line inside the quote,
    ==mark== a Streamlit background highlight, chart/archify blocks a one-line pointer to the HTML page."""
    def callout(m):
        key = m.group(1).strip()
        kind, label = CALLOUTS.get(key.upper()) or CALLOUTS.get(key) or ('note', '참고')
        title = m.group(2).strip()
        return f"> **:{STREAMLIT_TONE[kind]}[{label}{' · ' + title if title else ''}]**  "
    text = re.sub(r'^>\s*\[!([A-Za-z]+|참고|팁|중요|주의|위험|요약)\]([^\n]*)$', callout, str(text or ''), flags=re.M)
    text = re.sub(r'```[ \t]*(chart|archify-[a-z]+)[ \t]*\n.*?```', lambda m: '> :gray[도식·차트는 HTML 리포트에서 그려집니다.]', text, flags=re.S)
    return re.sub(r'==(?=\S)(.+?)(?<=\S)==', r':orange-background[\1]', text)
