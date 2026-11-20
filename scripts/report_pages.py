"""Omni AI Trading HTML report pages (user request 2026-11-17): the report documents become designed HTML pages (DESIGN.md tokens,
Pretendard), published to GitHub Pages, and Discord gets the title, a one-line summary and the link.

Simple trade messages (fills, plans, NAV, the 15:40 summary) stay text in discord_notify; this module owns documents.
Pipeline per report: source .md (+ .json) → generate (scripts/report_templates, site under ~/.vibe-trading/report-pages/site)
→ publish (git push of the site to the Pages repo) → send (Discord, only after the page answers 200).
Every step is recorded in nhplug/report-pages.sqlite3 (reports = current state, events = history with the failure reason)
so the dashboard (운영 → 리포트 발행) can list, search, regenerate, republish and resend.

Pages settings (.env or the legacy ../.envrc; names only, values are never printed or logged):
  REPORT_PAGES_REPO    owner/name of the Pages repository (<owner>/omni-ai-trading; created public on the first push if missing)
  REPORT_PAGES_URL     public base URL (default https://<owner>.github.io/<name>/)
  REPORT_PAGES_BRANCH  branch Pages serves (default gh-pages)
  REPORT_PAGES_PUSH    'on' to push; anything else builds locally only (prepared, not deployed: user 2026-11-17)
  GH_TOKEN             token git uses to push (given to git through GIT_CONFIG_* environment, never on the command line)

    PYTHONPATH=agent:scripts .venv/bin/python scripts/report_pages.py --scan            generate new/changed reports
    ... --build journal 2026-11-17 [--force]   one report        ... --publish          push the site
    ... --send journal:2026-11-17 [--no-live-check]              ... --cycle            scan + publish + send (the watcher)
    ... --list                                 records           ... --check FILE        validate a generated page
    ... --test-send                            one test message per channel with the newest report (link may not be live)
    ... --preview longterm-strategy sample.md [--out DIR]   render a sample page only (no records, publish or Discord)
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import filecmp
import hashlib
import html as htmllib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from config_store import write_json, write_text

KST = ZoneInfo('Asia/Seoul')
RUNTIME = Path(os.environ.get('VIBE_TRADING_HOME') or Path.home() / '.vibe-trading').expanduser()
HOME = RUNTIME / 'nhplug'
SITE = RUNTIME / 'report-pages' / 'site'
DB = HOME / 'report-pages.sqlite3'
LOCK = HOME / 'report-pages.lock'
TEMPLATES = Path(__file__).resolve().parent / 'report_templates'
REVIEWS = 'nhplug/shared-etf-paper/reviews'
DAY = r'\d{4}-\d{2}-\d{2}'
LIVE_WAIT_MIN = 15  # a published page that still does not answer after this long is a failed send

R = lambda key, name, icon, channel, folder, pattern, when, job, template='report.html', note='': {  # noqa: E731
    'key': key, 'name': name, 'icon': icon, 'channel': channel, 'folder': folder, 'pattern': re.compile(pattern), 'when': when,
    'job': job, 'template': template, 'note': note}
REPORTS = [  # the reports that become HTML pages and go to Discord as a link
    R('premarket', '장전 상세 분석', '🌅', 'realtime', REVIEWS, rf'premarket-(?P<key>{DAY})\.md', '평일 08:42', 'Codex 자동화 장전 상세 분석 작성'),
    R('longterm-strategy', '장기투자 전략 리포트', '📘', 'longterm', 'nhplug/vibe-longterm/reports', r'strategy-(?P<key>[0-9A-Za-z-]+)\.md',
      '평일 08:50 · 금 16:50(주간) · 매월 1일 17:20(월간)', '예약 작업 longterm-strategy-daily·weekly·monthly', 'longterm-strategy.html'),
    R('improvement', '장 마감 개선안', '🛠', 'realtime', REVIEWS, rf'improvement-(?P<key>{DAY})\.md', '평일 16:15', '예약 작업 trading-daily-improvement'),
    R('journal', '매매일지', '📓', 'realtime', REVIEWS, rf'journal-(?P<key>{DAY})\.md', '평일 16:40', '예약 작업 trading-journal', 'journal.html'),
    R('oversold-weekly', '낙폭과대 주간 감사', '📉', 'realtime', REVIEWS, rf'oversold-weekly-(?P<key>{DAY})\.md', '금 16:45', '예약 작업 oversold-weekly-audit'),
    R('journal-period', '기간 매매일지', '🗓', 'realtime', REVIEWS, r'journal-(?P<key>\d{4}(?:-\d{2}|-[QH]\d)?)\.md', '매월 1일 17:10',
      '예약 작업 trading-journal-period', 'journal.html'),
    R('longterm-audit', '장기투자 복기·감사', '🩺', 'longterm', 'nhplug/vibe-longterm/reviews', rf'audit-(?P<key>{DAY}|\d{{4}}-W\d{{2}}|\d{{4}}-\d{{2}})\.md',
      '평일 16:45(일일) · 금 17:40(주간) · 매월 1일 17:45(월간)', '예약 작업 longterm-review-daily · longterm-audit-weekly·monthly'),
]
BY_KEY = {r['key']: r for r in REPORTS}
NOT_PUBLISHED = [  # shown on the dashboard so it is clear what is not a page and why
    {'name': '체결·주문 계획·장기 NAV·15:40 마감 요약', 'channel': 'REALTIME·LONGTERM', 'how': '텍스트로 바로 발송(discord_notify)',
     'why': '짧은 매매 알림이라 링크보다 본문이 빠름'},
    {'name': '장중 매매·차단 감사 기록', 'channel': '—', 'how': '파일만(reviews/operations-audit-*.md)', 'why': '매시 갱신되는 내부 점검 기록'},
    {'name': '장중 진행 보고 · 장 마감 후 보고', 'channel': '—', 'how': '예약 작업 대화 보고', 'why': '파일로 남지 않음'},
]
CHANNELS = {'realtime': 'Discord REALTIME (Jev)', 'longterm': 'Discord LONGTERM (장기투자)'}
STATUS_KO = {'backoff': '잠시 뒤 재시도', 'draft': '작성 중(템플릿 검사 전)', 'ok': '완료', 'failed': '실패', 'pending': '대기', 'not_configured': '설정 없음', 'sent': '발송됨', 'skipped': '건너뜀',
             'sent_text': '텍스트로 발송됨', 'waiting_live': '게시 확인 중', 'off': '푸시 꺼짐'}
SECRET_TEXT = [(re.compile(r'(?<![0-9a-fA-F])[0-9a-f]{16,}(?![0-9a-fA-F])'), '••••'),  # account hashes (before the digit rule)
               (re.compile(r'(?<![\d/=%.#])\d{10,}(?!\d)'), '••••'),  # account-number-like digit runs, also next to Hangul
               (re.compile(r'(?<![\d-])\d{3,8}-\d{2,8}(?:-\d{1,8})?(?![\d-])'), None)]  # 123-45-678901: masked when ≥10 digits


def mask(text):
    """Account-like numbers and hashes in plain text replaced by ••••. Dates and short codes stay."""
    text = str(text or '')
    for pattern, sub in SECRET_TEXT:
        text = pattern.sub(sub or (lambda m: '••••' if sum(c.isdigit() for c in m.group()) >= 10 else m.group()), text)
    return text


LIVE_HIDDEN = '•••'
LIVE_WORD = '실계좌'
OTHER_BOOK = re.compile(r'\s*(?:[가-힣]{1,4}\s*[:—-]\s*)?(?:모의|Jev|vibe|가상|장기투자 \(vibe)')  # a sentence that starts with another book keeps its numbers
# money (원·만 원·억 원) and share counts keep their unit so Korean particles still read right; a percentage alone
# does not reveal the account's size and stays
LIVE_AMOUNT = re.compile(r'(?:[+−-]\s?)?\d[\d,]*(?:\.\d+)?\s*(?:조|억|만|천)?\s*(원)|\d[\d,]*\s*(주)(?!문|간|식|가|일|기|말|차|년)')
BLOCK = re.compile(r'(</?(?:p|li|tr|h[1-6]|ul|ol|blockquote|table|thead|tbody|div|figure|figcaption|section|details|summary|dt|dd)\b[^>]*>)')
SENTENCE = re.compile(r'(?<=[.!?])(\s+)')


def _hide(text):
    return LIVE_AMOUNT.sub(lambda m: LIVE_HIDDEN + (m.group(1) or m.group(2)), text)


def _hide_pieces(pieces):
    """Plain-text pieces of one block (text nodes in order): from the first 실계좌 to the end of the block every
    amount is hidden, except whole sentences that name another book (모의·Jev·vibe·가상) and not a 실계좌."""
    on, out = False, []
    for piece in pieces:
        parts = SENTENCE.split(piece)
        for i, s in enumerate(parts):
            if i % 2:
                continue
            if LIVE_WORD in s:
                at = s.index(LIVE_WORD) if not on else 0
                parts[i], on = s[:at] + _hide(s[at:]), True
            elif on and not OTHER_BOOK.match(s):
                parts[i] = _hide(s)
        out.append(''.join(parts))
    return out


def hide_live_text(text):
    """Public pages never show live-account money or share counts (user 2026-11-20). The local markdown and the
    dashboard keep the full numbers."""
    return _hide_pieces([str(text or '')])[0]


def _hide_live_columns(m):
    """A table whose header names a 실계좌 (the period journal: one column per account) hides that column's amounts."""
    table = m.group(0)
    heads = re.findall(r'<th\b[^>]*>(.*?)</th>', table.split('</thead>', 1)[0], flags=re.S)
    live = {i for i, h in enumerate(heads) if LIVE_WORD in htmllib.unescape(re.sub(r'<[^>]+>', '', h))}
    if not live or '</thead>' not in table:
        return table

    def row(r):
        cells = iter(range(10 ** 6))
        return re.sub(r'(<td\b[^>]*>)(.*?)(</td>)', lambda c: c.group(1) + (
            re.sub(r'(?<=>)[^<]+|^[^<]+', lambda t: htmllib.escape(_hide(htmllib.unescape(t.group())), quote=False), c.group(2))
            if next(cells) in live else c.group(2)) + c.group(3), r.group(0), flags=re.S)
    head, body = table.split('</thead>', 1)
    return head + '</thead>' + re.sub(r'<tr\b.*?</tr>', row, body, flags=re.S)


def hide_live(html_text):
    """hide_live_text for every block (paragraph, list item, table row, heading) of an HTML body.
    shortcut: a live sentence in a block that never names a 실계좌 is not caught (check() cannot see it either),
    so the writer skills keep naming the account."""
    html_text = re.sub(r'<table\b.*?</table>', _hide_live_columns, html_text, flags=re.S)
    parts = BLOCK.split(html_text)
    for i in range(0, len(parts), 2):
        if LIVE_WORD not in htmllib.unescape(parts[i]):
            continue
        nodes = re.split(r'(<[^>]+>)', parts[i])
        texts = _hide_pieces([htmllib.unescape(n) for n in nodes[0::2]])
        nodes[0::2] = [htmllib.escape(t, quote=False) if t != htmllib.unescape(n) else n for t, n in zip(texts, nodes[0::2])]
        parts[i] = ''.join(nodes)
    return ''.join(parts)


# ---------- records ----------
SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
  id TEXT PRIMARY KEY, kind TEXT, key TEXT, title TEXT, summary TEXT, channel TEXT, source TEXT, source_hash TEXT,
  html_path TEXT, url TEXT, text TEXT, created_at TEXT,
  generated_at TEXT, gen_status TEXT, gen_error TEXT,
  published_at TEXT, publish_status TEXT, publish_error TEXT,
  sent_at TEXT, send_status TEXT, send_error TEXT, send_attempts INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events (time TEXT, report_id TEXT, action TEXT, ok INTEGER, detail TEXT);
CREATE INDEX IF NOT EXISTS events_report ON events(report_id);
"""


def connect(db=None):
    path = Path(db or DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def now_iso():
    return datetime.now(KST).isoformat(timespec='seconds')


def event(con, report_id, action, ok, detail=''):
    con.execute('INSERT INTO events VALUES (?,?,?,?,?)', (now_iso(), report_id, action, int(bool(ok)), clean_error(detail)[:500]))


def update(con, report_id, **fields):
    con.execute(f"UPDATE reports SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), report_id))


def records(db=None):
    with connect(db) as con:
        return [dict(r) for r in con.execute('SELECT * FROM reports ORDER BY key DESC, kind')]


def history(db=None, action=None, limit=2000):
    with connect(db) as con:
        sql = 'SELECT e.rowid AS n, e.*, r.title, r.kind, r.key, r.channel FROM events e LEFT JOIN reports r ON r.id = e.report_id'
        args = ()
        if action:
            sql, args = sql + ' WHERE e.action=?', (action,)
        return [dict(r) for r in con.execute(sql + ' ORDER BY e.rowid DESC LIMIT ?', (*args, limit))]


@contextmanager
def locked():
    """One writer at a time (the watcher and dashboard buttons)."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK, 'w') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


# ---------- settings ----------
def settings(env=None):
    """Pages settings and why publishing cannot run ({'ready': bool, 'missing': [...], ...}); the token stays inside."""
    if env is None:
        from dotenv import dotenv_values
        import local_env
        env = {}
        for path in (local_env.REPO.parent / '.envrc', local_env.ENV):  # .env wins over the legacy .envrc
            if Path(path).is_file():
                env.update({k: v for k, v in dotenv_values(path, interpolate=False).items() if v})
    repo = (env.get('REPORT_PAGES_REPO') or '').strip().strip('/')
    owner, _, name = repo.partition('/')
    url = (env.get('REPORT_PAGES_URL') or (f'https://{owner}.github.io/{name}/' if owner and name else '')).strip()
    out = {'repo': repo, 'url': url.rstrip('/') + '/' if url else '', 'branch': (env.get('REPORT_PAGES_BRANCH') or 'gh-pages').strip(),
           'push': (env.get('REPORT_PAGES_PUSH') or '').strip().lower() in ('on', '1', 'true', 'yes'),
           'token': (env.get('GH_TOKEN') or env.get('GITHUB_TOKEN') or '').strip()}
    out['missing'] = [k for k, ok in (('REPORT_PAGES_REPO', owner and name), ('GH_TOKEN', out['token'])) if not ok]
    out['ready'] = not out['missing'] and out['push']
    return out


def public(cfg):
    """Settings without the token, for screens and logs."""
    return {k: v for k, v in cfg.items() if k != 'token'} | {'token_set': bool(cfg.get('token'))}


def clean_error(text):
    """Error text safe to store and show: no tokens, no URLs with credentials, no account-like numbers."""
    text = re.sub(r'(gh[pousr]_|github_pat_)[A-Za-z0-9_]+', '<token>', str(text))
    text = re.sub(r'https://[^@\s]+@', 'https://', text)
    text = re.sub(r'https://discord(app)?\.com/api/webhooks/\S+', '<webhook>', text)
    return mask(text).strip()


# ---------- sources ----------
def sources(runtime=None):
    """[(kind, key, path)] of every report file that exists."""
    runtime = Path(runtime or RUNTIME)
    out = []
    for spec in REPORTS:
        folder = runtime / spec['folder']
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            m = spec['pattern'].fullmatch(path.name)
            if m and path.is_file():
                out.append((spec['key'], m['key'], path))
    return out


def file_hash(path):
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()[:16]


def period_label(kind, key):
    if re.fullmatch(DAY, key):
        d = datetime.strptime(key, '%Y-%m-%d')
        return f"{d:%Y년 %-m월 %-d일} ({'월화수목금토일'[d.weekday()]})"
    if m := re.fullmatch(r'(\d{4})-(\d{2})', key):
        return f'{m[1]}년 {int(m[2])}월'
    if m := re.fullmatch(r'(\d{4})-([QH])(\d)', key):
        return f"{m[1]}년 {m[3]}{'분기' if m[2] == 'Q' else '반기'}"
    if m := re.fullmatch(r'(\d{4})-W(\d{2})', key):
        return f'{m[1]}년 {int(m[2])}주차'
    return f'{key}년' if re.fullmatch(r'\d{4}', key) else key


def sort_date(key):
    """YYYY-MM-DD used by the archive's year/month filters."""
    if re.fullmatch(DAY, key):
        return key
    if m := re.fullmatch(r'(\d{4})-W(\d{2})', key):
        return datetime.strptime(f'{m[1]}-W{m[2]}-5', '%G-W%V-%u').strftime('%Y-%m-%d')
    if m := re.fullmatch(r'(\d{4})-(\d{2})', key):
        return f'{m[1]}-{m[2]}-01'
    if m := re.fullmatch(r'(\d{4})-([QH])(\d)', key):
        return f"{m[1]}-{(int(m[3]) - 1) * (3 if m[2] == 'Q' else 6) + 1:02d}-01"
    return f'{key[:4]}-01-01' if re.fullmatch(r'\d{4}.*', key) else '1970-01-01'


# ---------- rendering ----------
def markdown(text):
    from markdown_it import MarkdownIt
    return MarkdownIt('commonmark', {'html': False, 'linkify': False, 'typographer': False}).enable(['table', 'strikethrough']).render(text)


def mask_text(html_text):
    """Mask account-like numbers and hashes in text nodes only (links keep their URLs)."""
    return re.sub(r'>([^<]+)<', lambda m: f'>{mask(m.group(1))}<', html_text)


SIGNED = re.compile(r'^\s*([+−-])\s?\d[\d,.]*\s*(%|원|%p|억|만|조)?')


def decorate(body):
    """Headings get ids for the table of contents, tables scroll on phones, external links open in a new tab,
    signed numbers in table cells get the Korean up(red)/down(blue) colour."""
    toc = []

    def heading(m):
        text = re.sub(r'<[^>]+>', '', m.group(2))
        ident = f's{len(toc) + 1}'
        toc.append({'id': ident, 'text': mask(htmllib.unescape(text))})
        return f'<h2 id="{ident}"{m.group(1)}>{m.group(2)}</h2>'
    body = re.sub(r'<h2([^>]*)>(.*?)</h2>', heading, body, flags=re.S)
    body = body.replace('<table>', '<div class="table-wrap"><table>').replace('</table>', '</table></div>')
    body = re.sub(r'<a href="(https?://[^"]+)"', r'<a href="\1" target="_blank" rel="noopener noreferrer"', body)

    def cell(m):
        inner = m.group(2)
        plain = htmllib.unescape(re.sub(r'<[^>]+>', '', inner))
        sign = SIGNED.match(plain)
        try:
            zero = float(re.search(r'\d[\d,]*(?:\.\d+)?', plain).group().replace(',', '')) == 0  # +0원 stays plain
        except (AttributeError, ValueError):
            zero = True
        if not sign or zero:
            return m.group(0)
        tone = 'up' if sign.group(1) == '+' else 'down'
        return f'<td{m.group(1)}><span class="{tone}">{inner}</span></td>'
    body = re.sub(r'<td([^>]*)>(.*?)</td>', cell, body, flags=re.S)
    return mask_text(body), toc


def split_title(text):
    """(title from the first '# ' line, markdown without it, lede = the first quote or paragraph)."""
    lines = text.splitlines()
    title, rest = '', lines
    for i, line in enumerate(lines):
        if line.startswith('# '):
            title, rest = line[2:].strip(), lines[:i] + lines[i + 1:]
            break
    lede = ''
    for block in '\n'.join(rest).split('\n\n'):
        block = block.strip()
        if block and not block.startswith(('#', '|', '-', '*', '1.')):
            lede = re.sub(r'^>\s?', '', block, flags=re.M)
            break
    lede = re.sub(r'^\[![^\]]+\][ \t]*', '', lede.strip())  # a callout as the opening quote: its text, not the marker
    lede = re.sub(r'[*_`]|\[([^\]]*)\]\([^)]*\)', lambda m: m.group(1) or '', lede).strip()
    lede = re.sub(r'==(?=\S)(.+?)(?<=\S)==', r'\1', lede)  # highlight marks are for the body; the summary is plain text
    body = '\n'.join(rest).lstrip('\n')
    if body.startswith('>'):  # the opening quote is the summary the hero already shows
        body = body.split('\n\n', 1)[1] if '\n\n' in body else ''
    return title, body, lede


def plain(body_html):
    return re.sub(r'\s+', ' ', htmllib.unescape(re.sub(r'<[^>]+>', ' ', body_html))).strip()


def environment():
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    return Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(['html']))


FENCE = re.compile(r'^```[ \t]*(chart|archify-(?:workflow|lifecycle|sequence|dataflow|architecture))[ \t]*\n(.*?)^```[ \t]*$', re.M | re.S)
ARCHIFY = Path(os.environ.get('ARCHIFY_BIN', Path.home() / '.claude/skills/archify/bin/archify.mjs'))


def archify(kind, source, out):
    """Render one archify spec (JSON) into `out` (a standalone HTML file). Returns None or the reason it failed."""
    node = shutil.which('node')
    if not node or not ARCHIFY.exists():
        return 'archify가 설치돼 있지 않음'
    spec = out.with_suffix('.json')
    try:
        data = json.loads(source)
        if isinstance(data.get('meta'), dict):
            data['meta']['animation'] = 'none'  # a report diagram holds still (taste loop-pause: no motion past 5 s without a pause)
        spec.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        done = subprocess.run([node, str(ARCHIFY), 'render', kind, str(spec), str(out), '--quality', 'standard'],
                              capture_output=True, text=True, timeout=90)
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        return f'{type(exc).__name__}'
    finally:
        spec.unlink(missing_ok=True)
    if done.returncode or not out.exists():
        return clean_error((done.stderr or done.stdout).strip().splitlines()[-1] if (done.stderr or done.stdout).strip() else 'archify 실패')[:200]
    return None


def blocks(md, key, out_dir):
    """Fenced ```chart and ```archify-<type> blocks → placeholders, and the HTML that replaces each."""
    import report_figures as F
    made = {}

    def one(m):
        n = len(made) + 1
        info, source = m.group(1), mask(m.group(2))
        token = f'@@FIGURE{n}@@'
        if info == 'chart':
            made[token] = F.chart_block(source)
        else:
            kind = info.split('-', 1)[1]
            try:
                title = str(json.loads(source).get('meta', {}).get('title') or '흐름도')
            except (ValueError, AttributeError):
                title = '흐름도'
            if out_dir is None:
                made[token] = f'<p class="label">흐름도: {htmllib.escape(title)} (HTML 생성 때 그림)</p>'
            else:
                name = f'{key}.d{n}.html'
                why = archify(kind, source, Path(out_dir) / name)
                made[token] = (f'<figure class="diagram"><div class="diagram-head"><span>{htmllib.escape(title)}</span>'
                               f'<a href="{name}" target="_blank" rel="noopener">크게 보기</a></div>'
                               f'<iframe data-diagram src="{name}" title="{htmllib.escape(title)}" loading="lazy"></iframe></figure>'
                               if why is None else
                               f'<aside class="callout warning" role="note"><p class="callout-title">도식을 그리지 못했습니다</p>'
                               f'<p>{htmllib.escape(title)}: {htmllib.escape(why)}</p></aside>')
        return f'\n{token}\n'
    return FENCE.sub(one, md), made


def render(kind, key, path, url='', out_dir=None):
    """(html, meta) of one report. meta: title, summary, text (plain, for search), toc. `out_dir` is where archify
    diagrams are written beside the page (None: diagrams are only named)."""
    import report_figures as F
    spec = BY_KEY[kind]
    text = Path(path).read_text(encoding='utf-8', errors='replace')
    title, rest, lede = split_title(text)
    rest, figures = blocks(rest, key, out_dir)
    extra = {}
    sibling = Path(path).with_suffix('.json')
    if sibling.exists():
        try:
            extra = json.loads(sibling.read_text(encoding='utf-8'))
        except ValueError:
            extra = {}
    extra = extra if isinstance(extra, dict) else {}
    summary = hide_live_text(mask(str(extra.get('summary') or lede or '').strip()))
    listed = lambda name: extra.get(name) if isinstance(extra.get(name), list) else []  # noqa: E731 - a string is not a list of items
    extra = {'top_issues': [{k: hide_live_text(mask(v)) for k, v in i.items()} for i in listed('top_issues') if isinstance(i, dict) and i.get('title')],
             'watch_next': [hide_live_text(mask(w)) for w in listed('watch_next') if isinstance(w, (str, int, float))],
             'scenarios': [{k: hide_live_text(mask(v)) for k, v in sc.items()} for sc in listed('scenarios') if isinstance(sc, dict) and sc.get('name')]}
    for issue in extra['top_issues']:
        try:
            issue['stars'] = max(0, min(5, int(float(issue.get('stars') or 0))))
        except ValueError:
            issue['stars'] = 0
    body, toc = decorate(markdown(rest))
    body = F.table_charts(F.marks(F.callouts(hide_live(body))))  # hidden before the bars are drawn from the table
    for token, html_figure in figures.items():
        body = body.replace(f'<p>{token}</p>', html_figure)
    title = hide_live_text(mask(title or f"{spec['name']} · {period_label(kind, key)}"))
    page = environment().get_template(spec['template']).render(
        title=title, description=summary or title, summary=summary, body=body, toc=toc, extra=extra, url=url,
        kind_name=spec['name'], period_label=period_label(kind, key), channel_label=CHANNELS[spec['channel']],
        source_name=Path(path).name, root='../../', css_version=css_version(), generated_at=datetime.now(KST).strftime('%Y-%m-%d %H:%M'))
    return page, {'title': title, 'summary': summary, 'text': plain(body)[:20000], 'toc': toc}


def css_version():
    return hashlib.sha1(b''.join((TEMPLATES / n).read_bytes() for n in ('report.css', 'index.js', 'theme.js'))).hexdigest()[:8]


def page_path(kind, key):
    return f'reports/{kind}/{key}.html'


def check(html_text):
    """Problems with a generated page ([] when it passes): head tags, structure, privacy."""
    issues = []
    for need, why in (('<html lang="ko">', 'lang 속성'), ('name="viewport"', 'viewport'), ('name="description"', 'description'),
                      ('<title>', 'title'), ('name="robots" content="noindex', 'noindex'), ('id="main"', '본문 랜드마크'),
                      ('skip-link', '본문 건너뛰기 링크'), ('<h1>', 'h1 제목')):
        if need not in html_text:
            issues.append(f'{why} 없음')
    text = re.findall(r'>([^<]+)<', html_text)
    if any(mask(htmllib.unescape(t)) != htmllib.unescape(t) for t in text):
        issues.append('계좌번호·계좌 해시처럼 보이는 값이 본문에 있음')
    if hide_live(html_text) != html_text:
        issues.append('실계좌 금액·수량이 공개 페이지에 있음')
    if re.search(r'(ghp_|github_pat_|discord(app)?\.com/api/webhooks)', html_text):
        issues.append('토큰·웹훅 주소가 들어 있음')
    if len(re.findall(r'<h1', html_text)) != 1:
        issues.append('h1은 하나여야 함')
    if re.search(r'<script(?![^>]*src=)', html_text) and 'index.js' not in html_text:
        issues.append('인라인 스크립트가 있음')
    return issues


# ---------- site ----------
def ensure_assets(site=None):
    """Copy the stylesheet, scripts and images. Pretendard comes from its official dynamic-subset CDN (only the glyphs a
    page uses); a 2 MB self-hosted copy was dropped after the perf check (2026-11-17), the system font is the fallback."""
    site = Path(site or SITE)
    assets = site / 'assets'
    assets.mkdir(parents=True, exist_ok=True)
    for name in ('report.css', 'index.js', 'theme.js', 'logo.png', 'mascot.png', 'favicon.png'):
        if not ((assets / name).exists() and filecmp.cmp(TEMPLATES / name, assets / name, shallow=False)):  # not every 30 s
            shutil.copyfile(TEMPLATES / name, assets / name)
    for name, body in (('.nojekyll', ''), ('robots.txt', 'User-agent: *\nDisallow: /reports/\n')):
        if not (site / name).exists():
            (site / name).write_text(body)


def build_index(con, site=None):
    """The archive page lives at reports/index.html: the Pages root is the landing page (user 2026-11-19)."""
    site = Path(site or SITE) / 'reports'
    site.mkdir(parents=True, exist_ok=True)  # every page may have failed: the index still exists
    rows = [dict(r) for r in con.execute("SELECT * FROM reports WHERE html_path IS NOT NULL AND html_path != '' ORDER BY key DESC, kind")]
    data = [{'kind': r['kind'], 'kind_name': BY_KEY[r['kind']]['name'] if r['kind'] in BY_KEY else r['kind'], 'key': r['key'],
             'period': period_label(r['kind'], r['key']), 'date': sort_date(r['key']), 'title': r['title'], 'summary': r['summary'] or '',
             'path': r['html_path'].removeprefix('reports/'), 'text': (r['text'] or '')[:4000]} for r in rows]
    data.sort(key=lambda r: (r['date'], r['kind']), reverse=True)
    write_json(site / 'reports.json', data, indent=None)  # read by the index page and pushed while a pass may be writing
    page = environment().get_template('index.html').render(
        title='전체 리포트', description='Omni AI Trading 매매일지·장전 분석·개선안·장기투자 전략 리포트 보관함', url='', root='../',
        css_version=css_version(), generated_at=datetime.now(KST).strftime('%Y-%m-%d %H:%M'),
        kinds=[{'key': s['key'], 'name': s['name']} for s in REPORTS], reports=data)
    write_text(site / 'index.html', page)


def generate(kind, key, path, con, cfg=None, site=None, force=False):
    """Write one page if it is new or its source changed (or `force`). Returns the record id."""
    site = Path(site or SITE)
    cfg = cfg or settings()
    report_id = f'{kind}:{key}'
    row = con.execute('SELECT * FROM reports WHERE id=?', (report_id,)).fetchone()
    digest = file_hash(path) + (file_hash(Path(path).with_suffix('.json')) if Path(path).with_suffix('.json').exists() else '')
    rel = page_path(kind, key)
    url = cfg['url'] + rel if cfg.get('url') else ''
    if row and row['source_hash'] == digest and (row['url'] or '') == url and not force:
        return report_id  # a failed source is not retried every pass: it waits for an edit or 'force'
    if not row:
        con.execute('INSERT INTO reports (id, kind, key, channel, source, created_at, send_status) VALUES (?,?,?,?,?,?,?)',
                    (report_id, kind, key, BY_KEY[kind]['channel'], str(path), now_iso(), 'pending'))
    try:
        page, meta = render(kind, key, path, url, out_dir=site / Path(rel).parent)
        issues = check(page)
        if issues:
            raise ValueError('검사 실패: ' + ', '.join(issues))
        out = site / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page, encoding='utf-8')
    except Exception as exc:  # noqa: BLE001 - the reason is shown on the dashboard
        update(con, report_id, gen_status='failed', gen_error=clean_error(f'{type(exc).__name__}: {exc}'), generated_at=now_iso(),
               source=str(path), source_hash=digest, url=url)
        event(con, report_id, 'generate', False, f'{type(exc).__name__}: {exc}')
        return report_id
    changed = bool(row and row['source_hash'] and row['source_hash'] != digest)
    update(con, report_id, title=meta['title'], summary=meta['summary'], text=meta['text'], html_path=rel, url=url, source=str(path),
           source_hash=digest, generated_at=now_iso(), gen_status='ok', gen_error=None, publish_status='pending', publish_error=None)
    if changed and row['send_status'] in ('failed', 'draft'):  # a draft is checked again before it is sent
        update(con, report_id, send_status='pending', send_error=None)
    event(con, report_id, 'generate', True, '원본이 바뀌어 다시 만듦' if changed else ('다시 만듦' if row else '새로 만듦'))
    return report_id


def scan(con, cfg=None, site=None, runtime=None):
    """Generate every new or changed report and rebuild the index; returns the ids that were (re)written."""
    cfg = cfg or settings()
    ensure_assets(site)
    before = {r['id']: (r['source_hash'], r['gen_status']) for r in con.execute('SELECT id, source_hash, gen_status FROM reports')}
    touched = []
    for kind, key, path in sources(runtime):
        report_id = generate(kind, key, path, con, cfg, site)
        after = con.execute('SELECT source_hash, gen_status FROM reports WHERE id=?', (report_id,)).fetchone()
        if before.get(report_id) != (after['source_hash'], after['gen_status']):
            touched.append(report_id)
    if touched or not (Path(site or SITE) / 'reports' / 'index.html').exists():  # an unchanged pass writes nothing (no empty commits)
        build_index(con, site)
    return touched


# ---------- publish ----------
def git(args, cwd, cfg=None, timeout=120):
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    if cfg and cfg.get('token'):  # credentials through the environment, not argv: ps never shows them
        auth = base64.b64encode(f"x-access-token:{cfg['token']}".encode()).decode()
        env |= {'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'http.https://github.com/.extraheader',
                'GIT_CONFIG_VALUE_0': f'AUTHORIZATION: basic {auth}'}
    done = subprocess.run(['git', *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if done.returncode:
        raise RuntimeError(clean_error((done.stderr or done.stdout).strip().splitlines()[-1] if (done.stderr or done.stdout).strip() else f'git {args[0]} 실패'))
    return done.stdout


def prepare_repo(cfg, site=None):
    """The site folder as a git repository on the Pages branch with the repo's remote (no push)."""
    site = Path(site or SITE)
    site.mkdir(parents=True, exist_ok=True)
    if not (site / '.git').exists():
        git(['init', '-q', '-b', cfg['branch']], site)
    if cfg.get('repo'):
        remote = cfg.get('remote') or f"https://github.com/{cfg['repo']}.git"  # 'remote': tests push to a local bare repo
        remotes = git(['remote'], site).split()
        git(['remote', 'set-url' if 'origin' in remotes else 'add', 'origin', remote], site)
    git(['config', 'user.name', 'Omni AI Trading reports'], site)
    git(['config', 'user.email', 'reports@users.noreply.github.com'], site)
    return site


PUBLISH_BACKOFF_MIN = 10
RETRY_SEND_MIN, RETRY_SEND_MAX = 10, 5  # transient Discord failures are tried again every 10 minutes, at most 5 tries


def publish(con, cfg=None, site=None, api=None, force=False):
    """Commit the site and push it (only when REPORT_PAGES_PUSH=on and the repo/token are set). Pending pages become
    'ok' after a push, 'off' while pushing is turned off, 'not_configured' without repo or token."""
    cfg = cfg or settings()
    site = Path(site or SITE)
    api = api or github
    # 'not_configured' waits for the settings, 'off' for REPORT_PAGES_PUSH=on: no git calls on every pass meanwhile
    waiting = ('pending', 'failed') + (('not_configured',) if not cfg['missing'] else ()) + (('off',) if cfg['push'] and not cfg['missing'] else ())
    pending = [r['id'] for r in con.execute(f"SELECT id FROM reports WHERE gen_status='ok' AND publish_status IN ({','.join('?' * len(waiting))})", waiting)]
    if not pending:
        return 'nothing'
    last = con.execute("SELECT ok, time FROM events WHERE action='publish' ORDER BY rowid DESC LIMIT 1").fetchone()
    if (not force and last and not last['ok']
            and datetime.now(KST) - datetime.fromisoformat(last['time']) < timedelta(minutes=PUBLISH_BACKOFF_MIN)):
        return 'backoff'
    if cfg['missing']:
        reason = f"{', '.join(cfg['missing'])} 설정 없음"
        for rid in pending:
            update(con, rid, publish_status='not_configured', publish_error=reason)
        return 'not_configured'
    try:
        prepare_repo(cfg, site)
        git(['add', '-A'], site)
        if git(['status', '--porcelain'], site).strip():
            git(['commit', '-q', '-m', f'reports {now_iso()}'], site)
    except Exception as exc:  # noqa: BLE001
        for rid in pending:
            update(con, rid, publish_status='failed', publish_error=clean_error(f'커밋 실패: {exc}'))
            event(con, rid, 'publish', False, f'커밋 실패: {exc}')
        return 'failed'
    if not cfg['push']:
        for rid in pending:
            update(con, rid, publish_status='off', publish_error='REPORT_PAGES_PUSH가 on이 아니라 로컬 커밋까지만 함')
        return 'off'
    try:
        made = ensure_repository(cfg, api)
        if made == 'created':
            event(con, None, 'repo', True, f"{cfg['repo']} 공개 저장소 만듦")
        git(['push', '-q', 'origin', f"HEAD:{cfg['branch']}"], site, cfg)
    except Exception as exc:  # noqa: BLE001
        for rid in pending:
            update(con, rid, publish_status='failed', publish_error=clean_error(str(exc)))
            event(con, rid, 'publish', False, str(exc))
        return 'failed'
    try:
        if ensure_pages(cfg, api) == 'enabled':
            event(con, None, 'repo', True, f"{cfg['repo']} Pages 켬({cfg['branch']} 브랜치)")
    except Exception as exc:  # noqa: BLE001 - the push landed; the send step reports a page that never opens
        event(con, None, 'repo', False, f'Pages 켜기 실패: {exc}')
    for rid in pending:
        update(con, rid, publish_status='ok', publish_error=None, published_at=now_iso())
        event(con, rid, 'publish', True, f"{cfg['repo']}@{cfg['branch']}")
    return 'ok'


def github(method, path, cfg, body=None, timeout=20):
    """(status, json) of one GitHub REST call with the push token; the token never leaves the header."""
    request = urllib.request.Request(f'https://api.github.com{path}', data=json.dumps(body).encode() if body is not None else None,
                                     method=method, headers={'Authorization': f"Bearer {cfg['token']}", 'Accept': 'application/vnd.github+json',
                                                             'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'omni-ai-trading-reports/1'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b'{}')
        except ValueError:
            return exc.code, {}


def ensure_repository(cfg, api=github):
    """'exists' or 'created': the Pages repository (public, so free-plan Pages works) is made on the first push."""
    owner, name = cfg['repo'].split('/', 1)
    status, _ = api('GET', f"/repos/{cfg['repo']}", cfg)
    if status == 200:
        return 'exists'
    if status != 404:
        raise RuntimeError(f'저장소 확인 실패(HTTP {status})')
    status, me = api('GET', '/user', cfg)
    path = '/user/repos' if status == 200 and me.get('login', '').lower() == owner.lower() else f'/orgs/{owner}/repos'
    status, out = api('POST', path, cfg, {'name': name, 'description': 'Omni AI Trading 리포트', 'private': False,
                                          'has_issues': False, 'has_wiki': False, 'has_projects': False, 'auto_init': False})
    if status not in (200, 201):
        raise RuntimeError(f"저장소 만들기 실패(HTTP {status}: {out.get('message', '')})")
    return 'created'


def ensure_pages(cfg, api=github):
    """'on' or 'enabled': Pages serves the branch root of the repository."""
    status, _ = api('GET', f"/repos/{cfg['repo']}/pages", cfg)
    if status == 200:
        return 'on'
    status, out = api('POST', f"/repos/{cfg['repo']}/pages", cfg, {'source': {'branch': cfg['branch'], 'path': '/'}})
    if status not in (200, 201):
        raise RuntimeError(f"HTTP {status}: {out.get('message', '')}")
    return 'enabled'


def page_live(url, timeout=10):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method='HEAD', headers={'User-Agent': 'omni-ai-trading-reports/1'}), timeout=timeout) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


# ---------- send ----------
def message(row, url=None, test=False):
    spec = BY_KEY.get(row['kind'], {'icon': '📄', 'name': row['kind']})
    summary = (row['summary'] or '').strip()
    if len(summary) > 280:
        summary = summary[:279].rstrip() + '…'
    lines = [f"{spec['icon']} **{spec['name']}** · {period_label(row['kind'], row['key'])}" + (' · 발송 테스트(배포 전)' if test else ''),
             f"{row['title']}"]
    if summary:
        lines.append(summary)
    lines.append(f"🔗 {url or row['url'] or '(Pages 주소 미설정)'}")
    return '\n'.join(lines)


def send(con, report_id, urls, sender=None, live=page_live, require_live=True, now=None):
    """Post the link of one published report. Returns 'sent', 'waiting' (page not answering yet) or 'failed'."""
    import discord_notify as dn
    sender = sender or dn.post
    now = now or datetime.now(KST)
    row = con.execute('SELECT * FROM reports WHERE id=?', (report_id,)).fetchone()
    if row is None:
        return 'failed'
    dests = dn.as_targets(urls.get(row['channel']))

    def fail(reason):
        keep = row['send_status'] in ('sent', 'sent_text')  # a manual resend that fails leaves a delivered report delivered
        update(con, report_id, send_status=row['send_status'] if keep else 'failed', send_error=reason,
               send_attempts=(row['send_attempts'] or 0) + 1)
        event(con, report_id, 'send', False, reason)
        return 'failed'
    if row['gen_status'] != 'ok':
        return fail('HTML 생성 실패 상태라 보낼 수 없음(다시 생성 필요)')
    if not dests:
        return fail(f"{CHANNELS[row['channel']]} 발송처 미설정({dn.WEBHOOK_KEYS[row['channel']]} 등)")
    if not row['url']:
        return fail('REPORT_PAGES_REPO 미설정이라 링크가 없음')
    if row['publish_status'] != 'ok':
        return fail(f"Pages 게시 전({STATUS_KO.get(row['publish_status'], row['publish_status'])}"
                    + (f": {row['publish_error']}" if row['publish_error'] else '') + ')')
    if require_live and not live(row['url']):
        published = datetime.fromisoformat(row['published_at']) if row['published_at'] else now
        late = now - published >= timedelta(minutes=LIVE_WAIT_MIN)  # user 2026-11-18: never the text instead, keep waiting for the link
        update(con, report_id, send_status='waiting_live',
               send_error=f'Pages 주소가 {LIVE_WAIT_MIN}분 넘게 열리지 않음(링크가 열리면 보냄)' if late else None)
        return 'waiting'
    # each destination once: one that already took this link (an earlier partial pass) is not sent again
    done = {d for (d,) in con.execute("SELECT detail FROM events WHERE report_id=? AND action='send' AND ok=1", (report_id,))}
    problems = []
    for kind, address in dests:
        label = f"{CHANNELS[row['channel']]} · {dn.DEST_KO[kind]}"
        if label in done or (kind == 'discord' and CHANNELS[row['channel']] in done):
            continue
        ok, permanent = dn.send_to(kind, address, message(row), sender if kind == 'discord' else None)
        if ok:
            event(con, report_id, 'send', True, label)
        else:
            problems.append(f"{dn.DEST_KO[kind]} " + ('거절(4xx: 주소·토큰 확인)' if permanent else '연결 실패 또는 429 계속'))
    if problems:
        return fail(', '.join(problems))
    update(con, report_id, send_status='sent', send_error=None, sent_at=now_iso(), send_attempts=(row['send_attempts'] or 0) + 1)
    return 'sent'


def test_send(urls, con, sender=None):
    """One test message per channel with the newest generated report of that channel (the link may not be live)."""
    import discord_notify as dn
    out = {}
    for channel in CHANNELS:
        row = con.execute("SELECT * FROM reports WHERE channel=? AND gen_status='ok' ORDER BY key DESC LIMIT 1", (channel,)).fetchone()
        if not dn.as_targets(urls.get(channel)):
            out[channel] = 'no destination'
            continue
        text = message(row, test=True) if row else f'🧪 리포트 HTML 발송 테스트 · {CHANNELS[channel]} · 아직 만든 리포트가 없습니다.'
        results = dn.send_everywhere(urls[channel], text, sender)
        ok = all(results.values())
        out[channel] = ', '.join(f"{dn.DEST_KO[k]} {'sent' if v else 'failed'}" for k, v in results.items())
        if row:
            event(con, row['id'], 'send_test', ok, CHANNELS[channel] + (' 테스트 발송' if ok else ' 테스트 실패'))
    return out


# ---------- the watcher's pass ----------
def unfinished(kind, key, source):
    """The writer's own template problems with a report's source ([] when it passes; reports without a template check
    are written once, after their writer's check). Review 4 (2026-11-19): the journal is filled in place, and a pass
    between the writer's edits linked the skeleton ('작성 전 — 자동 요약입니다…') to Discord."""
    if kind not in ('journal', 'journal-period', 'premarket'):
        return []
    try:
        text = Path(source).read_text(encoding='utf-8')
    except (OSError, TypeError):
        return ['원본 파일을 읽지 못함']
    if kind == 'premarket':
        import premarket_template
        return premarket_template.check(text, written=True, day=key)
    import journal_template
    return journal_template.check(text, 'day' if kind == 'journal' else 'period', written=True)


def cycle(urls, cfg=None, sender=None, live=page_live, db=None, site=None, runtime=None, api=None):
    """Scan → publish → send pending links. Called by discord_notify every pass; cheap when nothing changed."""
    cfg = cfg or settings()
    with locked(), connect(db) as con:
        requeue(con, urls)
        touched = scan(con, cfg, site, runtime)
        state = publish(con, cfg, site, api)
        results = {}
        # user 2026-11-18: no report goes to Discord as plain text. Until Pages is deployed (push off, repo or token
        # missing, a failing push) reports stay 'pending' and go out as links once their page is published and opens.
        for row in con.execute("SELECT id, kind, key, source FROM reports WHERE gen_status='ok' AND publish_status='ok' "
                               "AND send_status IN ('pending','waiting_live')").fetchall():
            problems = unfinished(row['kind'], row['key'], row['source'])
            if problems:  # a draft waits for the source to change (generate puts it back to 'pending')
                update(con, row['id'], send_status='draft', send_error=f'작성 중: {problems[0]}')
                continue
            results[row['id']] = send(con, row['id'], urls, sender, live)
            con.commit()
        return {'generated': touched, 'publish': state, 'sent': results}


def requeue(con, urls, now=None):
    """Failed sends whose cause can clear by itself go back to 'pending': a connection error or 429 (after 10 minutes,
    at most 5 tries) and a webhook that was missing but is set now. A 4xx rejection waits for a manual resend."""
    import discord_notify as dn
    now = now or datetime.now(KST)
    for row in con.execute("SELECT * FROM reports WHERE send_status='failed' AND gen_status='ok'").fetchall():
        error, tries = row['send_error'] or '', row['send_attempts'] or 0
        last = con.execute("SELECT max(time) FROM events WHERE report_id=? AND action='send'", (row['id'],)).fetchone()[0]
        quiet = not last or now - datetime.fromisoformat(last) >= timedelta(minutes=RETRY_SEND_MIN)
        webhook_back = '미설정' in error and dn.as_targets(urls.get(row['channel']))
        if webhook_back or ('연결 실패' in error and tries < RETRY_SEND_MAX and quiet):
            update(con, row['id'], send_status='pending', send_error=None)
            event(con, row['id'], 'requeue', True, '자동 재시도 대기열로 되돌림(' + ('웹훅 설정됨' if webhook_back else f'{tries}회 실패 뒤 {RETRY_SEND_MIN}분') + ')')


def retry(action, report_id, urls=None, cfg=None, db=None, site=None, sender=None, live=page_live):
    """Dashboard buttons: 'generate' (rebuild from the source), 'publish' (push again), 'send' (post the link again)."""
    cfg = cfg or settings()
    with locked(), connect(db) as con:
        row = con.execute('SELECT * FROM reports WHERE id=?', (report_id,)).fetchone()
        if row is None:
            return '기록 없음'
        if action == 'generate':
            if not Path(row['source']).exists():
                update(con, report_id, gen_status='failed', gen_error='원본 파일이 없음')
                event(con, report_id, 'generate', False, '원본 파일이 없음')
                return '원본 파일이 없음'
            ensure_assets(site)
            generate(row['kind'], row['key'], row['source'], con, cfg, site, force=True)
            build_index(con, site)
            return STATUS_KO.get(con.execute('SELECT gen_status FROM reports WHERE id=?', (report_id,)).fetchone()[0], '')
        if action == 'publish':
            update(con, report_id, publish_status='pending')
            return STATUS_KO.get(publish(con, cfg, site, force=True), '변경 없음')
        if action == 'send':
            from discord_notify import urls_from_env
            urls = urls or urls_from_env()
            if not cfg.get('ready'):  # Pages off: nothing goes out as text (user 2026-11-18)
                return 'Pages 배포 전이라 보내지 않음(링크가 준비되면 자동 발송)'
            return STATUS_KO.get(send(con, report_id, urls, sender, live), '')
    return '알 수 없는 작업'


def preview(kind, md, out=None):
    """Render any .md (+ sibling .json) as a page of `kind` into a throwaway folder: no records, no index, no publish, no
    Discord. Use it for samples and template changes; a file dropped into the real reports folder would be sent."""
    md = Path(md)
    m = BY_KEY[kind]['pattern'].fullmatch(md.name)
    key = m['key'] if m else md.stem
    site = Path(out or Path(os.environ.get('TMPDIR', '/tmp')) / 'report-preview')
    ensure_assets(site)
    target = site / page_path(kind, key)
    target.parent.mkdir(parents=True, exist_ok=True)
    page, _ = render(kind, key, md, out_dir=target.parent)
    target.write_text(page, encoding='utf-8')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--scan', action='store_true')
    parser.add_argument('--build', nargs=2, metavar=('KIND', 'KEY'))
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--send')
    parser.add_argument('--no-live-check', action='store_true')
    parser.add_argument('--cycle', action='store_true')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--check')
    parser.add_argument('--test-send', action='store_true')
    parser.add_argument('--settings', action='store_true')
    parser.add_argument('--preview', nargs=2, metavar=('KIND', 'MDFILE'))
    parser.add_argument('--out', default=None)
    args = parser.parse_args()
    if args.preview:
        kind, md = args.preview
        out = preview(kind, md, args.out)
        print(out)
        issues = check(Path(out).read_text(encoding='utf-8'))
        print('\n'.join(issues) or 'check ok')
        sys.exit(1 if issues else 0)
    if args.settings:
        print(json.dumps(public(settings()), ensure_ascii=False, indent=1))
    elif args.check:
        issues = check(Path(args.check).read_text(encoding='utf-8'))
        print('\n'.join(issues) or 'ok')
        sys.exit(1 if issues else 0)
    elif args.list:
        for r in records():
            print(f"{r['id']:34} 생성 {STATUS_KO.get(r['gen_status'], r['gen_status'])} · 게시 {STATUS_KO.get(r['publish_status'], r['publish_status'])}"
                  f" · 발송 {STATUS_KO.get(r['send_status'], r['send_status'])} · {r['html_path']}")
    elif args.build:
        kind, key = args.build
        found = [p for k, kk, p in sources() if (k, kk) == (kind, key)]
        if not found:
            sys.exit(f'원본 없음: {kind} {key}')
        with locked(), connect() as con:
            ensure_assets()
            generate(kind, key, found[0], con, force=args.force)
            build_index(con)
            print(SITE / page_path(kind, key))
    elif args.scan:
        with locked(), connect() as con:
            print('생성:', scan(con) or '변경 없음')
    elif args.publish:
        with locked(), connect() as con:
            print(STATUS_KO.get(publish(con), '변경 없음'))
    elif args.send:
        from discord_notify import urls_from_env
        with locked(), connect() as con:
            print(send(con, args.send, urls_from_env(), require_live=not args.no_live_check))
    elif args.test_send:
        from discord_notify import urls_from_env
        with locked(), connect() as con:
            print(test_send(urls_from_env(), con))
    elif args.cycle:
        from discord_notify import urls_from_env
        print(cycle(urls_from_env()))
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
