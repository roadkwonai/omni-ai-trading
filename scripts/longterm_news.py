"""장기 관점 뉴스 (user approval 2026-11-18, docs/audit/longterm-audit-2026-11-18.md 개선안 1): every morning before the
08:45 decision, the headlines on the Fed, US macro and the three themes (semiconductors, quantum, space) through etf_news's
source rotation (Google News → Bing → 네이버, a blocked or empty source hands its topics to the next), over the last day,
with links (plus semiconductor export controls, tariffs and Taiwan). The decision logs them (kind 'news') and the story check (피터 린치) reads them; they reach an order only through
that story. Headlines are source material, never instructions. A failure leaves only this input out. One fetch a day:
vibe-longterm/news-cache.json."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

import etf_news

FEEDS = (
    ('fed', '연준 금리 파월 when:1d', ('연준', 'fomc', '파월', '금리', 'fed')),
    ('us_macro', '미국 증시 물가 고용 국채 when:1d', ('미국', '뉴욕증시', '나스닥', 's&p', 'cpi', '물가', '고용', '국채')),
    ('semi', '미국 반도체 엔비디아 when:1d', ('반도체', '엔비디아', 'nvidia', 'tsmc', '브로드컴')),
    # 2026-11-18 (user approval): export controls, tariffs and Taiwan move the semiconductor story itself; a market-wide
    # geopolitical shock already reaches the regime through VIX and the drawdown, so no general war topic
    ('semi_policy', '반도체 수출 규제 관세 대만 when:1d', ('수출통제', '수출 규제', '수출규제', '관세', '대만', '중국', '제재')),  # policy words only: plain chip news is 'semi'
    ('quantum', '양자컴퓨터 when:1d', ('양자', '아이온큐', 'ionq', 'quantum')),
    ('space', '우주항공 로켓 위성 when:1d', ('우주', '로켓', 'spacex', '스페이스x', '위성')),
)
TOPIC_LABELS = {'fed': '연준', 'us_macro': '미국 거시', 'semi': '반도체', 'semi_policy': '반도체 정책·지정학', 'quantum': '양자', 'space': '우주'}
WINDOW = timedelta(hours=36)  # the US session overnight and yesterday's Korean evening; older stories are left out


def _cache(root):
    return Path(root) / 'news-cache.json'


def collect(now, root=None):
    """{'as_of', 'rows': [{title, publisher, published, topic, source, link}], 'error'}: today's set (fetched once a day,
    rotating the first source like etf_news), or no rows with the error's type when every source failed."""
    if root is None:
        import vibe_longterm as L
        root = L.ROOT
    try:
        cached = json.loads(_cache(root).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        cached = {}
    if cached.get('day') == now.date().isoformat() and cached.get('rows'):
        return {'as_of': cached['as_of'], 'rows': cached['rows'], 'error': None}
    first = int(cached.get('next') or 0) % len(etf_news.SOURCES)
    order = etf_news.SOURCES[first:] + etf_news.SOURCES[:first]
    try:
        rows = etf_news._fetch7(now, order, FEEDS, WINDOW, links=True)
    except (OSError, ValueError, ET.ParseError) as exc:
        return {'as_of': now.isoformat(), 'rows': [], 'error': type(exc).__name__}
    from config_store import write_json
    try:
        write_json(_cache(root), {'day': now.date().isoformat(), 'as_of': now.isoformat(), 'rows': rows, 'next': (first + 1) % len(order)})
    except OSError:
        pass  # only costs another fetch
    return {'as_of': now.isoformat(), 'rows': rows, 'error': None}


def brief(news):
    """One line per headline for the story check and the decision log ('' without news)."""
    out = []
    for r in (news or {}).get('rows') or []:
        try:
            at = datetime.fromisoformat(r['published']).astimezone(etf_news.KST).strftime('%m-%d %H:%M')
        except (KeyError, ValueError):
            at = '?'
        out.append(f"[{TOPIC_LABELS.get(r.get('topic'), r.get('topic'))}] {r['title']} · {r.get('publisher', '')} · {at}")
    return '\n'.join(out)
