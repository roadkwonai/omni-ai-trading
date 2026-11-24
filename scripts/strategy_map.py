"""The live trading algorithm as data: what each part reads, which rules apply now, and the route in use.

`state()` reads the same files the runner and services use (settings, accounts, AI modes, market context, watchlist,
the order journal, exit plans) and never calls a broker. `spec()` turns that state into an
Archify workflow diagram whose highlighted path is inferred from settings and time, not observed execution; `render()` draws it with the installed
Archify CLI and caches by content, so an unchanged state is not redrawn. `dot()` is a Graphviz fallback.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from sqlite_ro import ro_connect
from src.config.paths import get_runtime_root  # VIBE_TRADING_HOME, else ~/.vibe-trading (one rule for every tool)

KST = ZoneInfo('Asia/Seoul')
ROOT = get_runtime_root() / 'nhplug' / 'shared-etf-paper'
ARCHIFY = Path(os.environ.get('ARCHIFY_BIN', Path.home() / '.claude/skills/archify/bin/archify.mjs'))
# Defaults of nh_paper_isolated (a test keeps them in step); state() uses the saved settings (session_times).
ENTRY_CUTOFF, FLATTEN_AT, SESSION_CLOSE = '14:30', '14:40', '15:00'


def phase(now, times=None):
    """Where the trading day stands (weekends only; exchange holidays are checked by the runner itself). `times` is
    session_times.times() of the saved settings; the defaults otherwise."""
    times = times or {'cutoff': ENTRY_CUTOFF, 'flatten': FLATTEN_AT, 'close': SESSION_CLOSE}
    clock = now.astimezone(KST).strftime('%H:%M')
    if now.astimezone(KST).weekday() >= 5:
        return 'closed_day', '휴장(주말)'
    if clock < '09:00':
        return 'pre_open', '장 시작 전'
    from session_times import order_start
    if clock < order_start(now.astimezone(KST).date()):
        return 'observation', '개장 관찰 · 주문 없음'
    if clock < times['cutoff']:
        return 'entry', '진입 가능'
    if clock < times['flatten']:
        return 'no_new_buys', '신규 매수 마감'
    if clock < times['close']:
        return 'flatten', '장 마감 정리'
    return 'closed', '장 종료'


def _age(stamp, now):
    try:
        at = datetime.fromisoformat(stamp)
        return None if at.tzinfo is None else max(0, int((now - at).total_seconds() // 60))
    except (TypeError, ValueError):
        return None


def _holdings(root):
    try:
        with ro_connect(Path(root) / 'orders.sqlite3') as db:
            rows = db.execute("SELECT symbol, SUM(CASE side WHEN 'BUY' THEN quantity ELSE -quantity END) FROM fills GROUP BY symbol")
            return {code: qty for code, qty in rows if qty}
    except sqlite3.Error:
        return None


def _last_event(root):
    """The last whole journal line: the tail grows until it holds one (a market_context line runs to ~21 KB, a 4 KB tail
    held none 1.6% of the session) and a line still being written is skipped."""
    try:
        with (Path(root) / 'events.jsonl').open('rb') as f:
            end, size = f.seek(0, 2), 8192
            while True:
                start = max(0, end - size)
                f.seek(start)
                lines = f.read(end - start).split(b'\n')
                for line in reversed(lines if start == 0 else lines[1:]):  # lines[0] may start mid-line
                    try:
                        event = json.loads(line)
                    except ValueError:  # blank, or the line being written
                        continue
                    if isinstance(event, dict):
                        return event
                if start == 0:
                    return None
                size *= 4
    except OSError:
        return None


def state(root=ROOT, now=None, services=None):
    """Everything the diagram and tables need, read-only; a missing source is reported, never filled in."""
    import accounts
    import market_context
    from strategy_settings import read_settings
    root, now = Path(root), now or datetime.now(KST)
    records = accounts.load()
    record = records.get('paper-shared', {})
    settings, settings_status = read_settings(root)
    import session_times
    clock = session_times.times(settings)
    try:
        from paper_ai_modes import read_modes
        modes = read_modes(root)
    except (OSError, ValueError, TypeError):
        modes = None
    context = market_context.load(root / 'market-context.json')
    view = market_context.summary(context, now) if context else None
    indices = {}
    for item in (context or {}).get('indices', {}).values():
        indices[item['name']] = {'regime': item['regime']['label'], 'tone': item['flow'], 'change_pct': item['change_pct']}

    def stamp_of(path, key='time'):
        try:
            return json.loads((root / path).read_text(encoding='utf-8')).get(key)
        except (OSError, ValueError, AttributeError):
            return None

    from premarket import load as load_premarket, engine_brief
    try:
        brief = load_premarket(root, now)
        premarket = engine_brief(root, now) if brief else None
    except (OSError, ValueError, TypeError, KeyError):
        brief = premarket = None
    live_records = [r for r in records.values() if r.get('kind') == 'live']
    live_switches = [r.get('live_orders', False) if not r.get('invalid') else None for r in live_records]
    last = _last_event(root)
    try:
        plans = json.loads((root / 'exit-plans.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        plans = {}
    sources = [
        ('NH 시세·1분봉', last.get('time') if last else None, 3, 'Jev 차트·진입 추세·청산 가격'),
        ('시장 상황(국면·수급·테마·해외)', (context or {}).get('time'), 20, 'Jev 입력, 추가 매수 차단'),
        ('순위·관심종목', stamp_of('watchlist.json'), 45, 'Jev 후보 종목'),
    ]
    return {
        'time': now.isoformat(timespec='seconds'), 'phase': phase(now, clock), 'clock': clock,
        'engine': record.get('engine', 'jev'), 'auto': bool(record.get('auto')), 'budget': record.get('budget', {}),
        'limits': accounts.paper_limits(record), 'live_switches': live_switches,
        'premarket_time': brief.get('time') if brief else None, 'premarket_available': premarket is not None,
        'settings': settings, 'settings_status': settings_status, 'modes': modes, 'indices': indices,
        'market_fresh': view is not None, 'holdings': _holdings(root), 'exit_plans': sorted(plans) if isinstance(plans, dict) else [],
        'stop_file': (root / 'STOP').exists(), 'services': services or {},
        # outside the session the intraday sources are not refreshed (runner and selection run 09:00~): 'idle', not stale
        'sources': [{'name': n, 'time': t, 'age_min': _age(t, now), 'limit_min': lim, 'used_for': use,
                     'fresh': (_age(t, now) is not None and _age(t, now) <= lim),
                     'idle': phase(now, clock)[0] in ('pre_open', 'closed_day', 'closed')} for n, t, lim, use in sources],
    }


def averaging_down(st, index='코스피'):
    """nh_paper_isolated.averaging_down_blocked for a long on `index`; unknown when the market view is stale."""
    import nh_paper_isolated as runner
    from etf_universe import SYMBOLS
    item = st['indices'].get(index)
    if not st['market_fresh'] or not item:
        return 'unknown'
    view = {index: {'regime': item['regime'], 'today_tone': item['tone']}}
    return 'blocked' if runner.averaging_down_blocked(view, SYMBOLS['K200'], False, index) else 'allowed'


def active_route(st):
    """(node ids, description) for the settings-based expected path, not observed execution."""
    key = st['phase'][0]
    held = bool(st['holdings'])
    if key in ('closed', 'closed_day', 'pre_open'):
        return ['order', 'ledger'], '장외: 주문 없음, 기록·정산만 갱신'
    if st['stop_file']:
        return ['halt', 'entry'], '중단 요청(STOP): 실행기가 멈춰 신규 진입과 보유분 감시가 모두 멈춤'
    if not st['auto']:
        return ['halt', 'entry'], '자동매매 꺼짐: 신규 진입 차단 (보유분 손절·익절 감시는 계속)'
    if key == 'observation':
        return ['nh', 'jev'], '개장 관찰: 새 주문 없음 · 실제 실행은 별도 확인'
    if key in ('no_new_buys', 'flatten'):
        if held:
            return ['quote', 'exit', 'gate', 'order', 'ledger'], (f"{(st.get('clock') or {}).get('flatten', FLATTEN_AT)} 이후 당일 정리 (종가매매 조건 11개 충족 종목만 보유)" if key == 'flatten'
                                                               else '신규 매수 마감: 보유분 청산 규칙만 감시')
        return ['halt', 'entry'], '신규 매수 마감, 보유 없음: 대기'
    return ['nh', 'jev', 'entry', 'gate', 'order', 'ledger'], 'Jev 후보 판단 → 진입 게이트 → 주문 가드 → 모의 주문'


def spec(st, output='strategy-map.html'):
    """Archify workflow (schema v2) for the current state; tags carry the live values."""
    s, kospi = st['settings'], st['indices'].get('코스피')
    regime = (f"코스피 {kospi['regime']}·{kospi['tone']}" if kospi else '국면 미확인') + ('' if st['market_fresh'] else ' (오래됨)')
    fresh = {src['name']: src for src in st['sources']}
    # a state word, not 'N분 전': the content hash names the cached HTML, so a clock in it redrew ~770 KB every minute
    age = lambda name: ('기록 없음' if fresh[name]['time'] is None else '장외' if fresh[name].get('idle')
                        else '최신' if fresh[name]['fresh'] else '오래됨')
    jev_main, held = st['engine'] == 'jev', st['holdings'] or {}
    blocked = averaging_down(st)
    route, _ = active_route(st)
    limits = st.get('limits')
    money = lambda value: f'{value / 10_000:,.0f}만' if type(value) is int else '미확인'
    limit_text = (f"회당 {money(limits['order_max_krw'])}·일 {money(limits['daily_buy_krw'])}" if limits else '계좌 한도 미확인')
    limit_tag = (f"하루 {limits['daily_new_symbols']}종목·손실 {money(limits['daily_loss_krw'])}" if limits else '설정 확인 필요')
    switches = st.get('live_switches')
    live_tag = ('스위치 미확인' if switches is None else '실계좌 미설정' if not switches else
                f"스위치 ON {sum(x is True for x in switches)} · OFF {sum(x is False for x in switches)}" +
                (' · 미확인 있음' if any(x is None for x in switches) else ''))
    premarket_tag = (f"수집 {(st.get('premarket_time') or '')[11:16]} · 입력 별도 확인" if st.get('premarket_available')
                     else '당일 유효 자료 없음·입력 미확인')
    # Placement keeps each input directly above what reads it, so most edges are one straight drop.
    nodes = [
        {'id': 'nh', 'lane': 'data', 'col': 1, 'type': 'external', 'label': 'NH 분봉·뉴스', 'sublabel': '1분봉·호가·헤드라인', 'tag': age('NH 시세·1분봉')},
        {'id': 'mkt', 'lane': 'data', 'col': 2, 'type': 'cloud', 'label': '시장 상황', 'sublabel': '국면·수급·테마·해외', 'tag': regime},
        {'id': 'premarket', 'lane': 'data', 'col': 0, 'type': 'database', 'label': '오늘 장전 수집 자료',
         'sublabel': '뉴스·해외 지표·공시', 'tag': premarket_tag},
        {'id': 'quote', 'lane': 'data', 'col': 4, 'type': 'external', 'label': '보유 시세', 'sublabel': '매 주기 호가 확인',
         'tag': f"보유 {len(held)}종" if st['holdings'] is not None else '보유 미확인'},
        {'id': 'movers', 'lane': 'engine', 'col': 0, 'type': 'messagebus', 'label': '순위·관심종목', 'sublabel': 'Jev가 30분마다 3종목', 'tag': age('순위·관심종목')},
        {'id': 'jev', 'lane': 'engine', 'col': 1, 'type': 'backend', 'label': 'Jev', 'sublabel': '후보·보유 판단',
         'tag': (f"메인 · 진입 {s['entry_confidence']:.2f}↑" if jev_main else '참고 의견')},
        {'id': 'entry', 'lane': 'rules', 'col': 2, 'type': 'security', 'label': '진입 게이트', 'sublabel': '엔진·전략별 조건과 예외',
         'tag': '진입 시간 · 조건 별도 확인' if st['phase'][0] == 'entry' and st['auto'] else st['phase'][1]},
        {'id': 'exit', 'lane': 'rules', 'col': 4, 'type': 'backend', 'label': '청산 규칙',
         'sublabel': f"방어 데이터 목표 전량 · 추세 +{s['trend_half_pct']:g}%·추적",
         'tag': f"{(st.get('clock') or {}).get('flatten', FLATTEN_AT)} 정리 또는 보유"},
        {'id': 'add', 'lane': 'rules', 'col': 5, 'type': 'backend', 'label': '분할 추가 매수',
         'sublabel': '보유 종목 조건·한도 확인',
         'tag': {'blocked': '하락장+약세: 제한', 'allowed': '국면 확인: 조건별 검토', 'unknown': '국면 미확인'}[blocked]},
        {'id': 'halt', 'lane': 'guard', 'col': 2, 'type': 'security', 'label': '자동매매 스위치',
         'sublabel': 'ON/OFF·STOP 파일', 'tag': ('중단 요청됨' if st['stop_file'] else 'ON' if st['auto'] else 'OFF')},
        {'id': 'gate', 'lane': 'guard', 'col': 4, 'type': 'security', 'label': '공유 모의 주문 가드', 'sublabel': limit_text, 'tag': limit_tag},
        {'id': 'live', 'lane': 'exec', 'col': 1, 'type': 'external', 'label': '실계좌 미러', 'sublabel': '별도 실행기·보호 조건 적용', 'tag': live_tag},
        {'id': 'order', 'lane': 'exec', 'col': 4, 'type': 'messagebus', 'label': '모의 주문', 'sublabel': '보호 경로·체결 대조',
         'tag': f'보유 {len(held)}종' if st['holdings'] is not None else '보유 미확인'},
        {'id': 'ledger', 'lane': 'exec', 'col': 5, 'type': 'database', 'label': 'SQLite 기록', 'sublabel': '원장·AI 입력·정산', 'tag': 'events_v2·model_inputs'},
    ]
    for node in nodes:
        node['width'] = 188  # Korean tags with live values need room at the legible minimum
    # Sides are pinned: automatic ports let shared inputs detour around the canvas.
    down = {'fromSide': 'bottom', 'toSide': 'top'}
    edges = [
        {'from': 'nh', 'to': 'jev', 'label': '분봉·뉴스', **down},
        {'from': 'mkt', 'to': 'jev', 'label': '국면 요약'},
        {'from': 'premarket', 'to': 'jev', 'label': '장전 맥락', 'role': 'async'},
        {'from': 'movers', 'to': 'jev', 'label': '후보', 'fromSide': 'right', 'toSide': 'left'},
        {'from': 'jev', 'to': 'entry', 'label': f"신뢰도 {s['entry_confidence']:.2f}↑", 'fromSide': 'bottom', 'toSide': 'left'},
        {'from': 'jev', 'to': 'live', 'label': '판단 재현', 'role': 'async', 'fromSide': 'bottom'},
        {'from': 'halt', 'to': 'entry', 'label': '차단', 'role': 'error', 'fromSide': 'top', 'toSide': 'bottom'},
        {'from': 'entry', 'to': 'gate', 'label': '매수 의도', 'fromSide': 'right', 'toSide': 'left'},
        {'from': 'quote', 'to': 'exit', 'label': '매수가 대비', **down}, {'from': 'exit', 'to': 'gate', 'label': '보호 매도', **down},
        {'from': 'add', 'to': 'gate', 'role': 'branch', 'fromSide': 'bottom', 'toSide': 'right'},
        {'from': 'gate', 'to': 'order', 'label': '통과', **down},
        {'from': 'order', 'to': 'ledger', 'label': '체결·기록', 'fromSide': 'right', 'toSide': 'left'},
    ]
    for edge in edges:
        if edge['from'] in route and edge['to'] in route and route.index(edge['to']) == route.index(edge['from']) + 1:
            edge['role'] = 'main'
    return {
        'schema_version': 2, 'diagram_type': 'workflow',
        'meta': {'title': 'Omni AI Trading 알고리즘 · 예상 경로 · ' + st['phase'][1], 'output': output, 'quality_profile': 'standard',
                 'animation': 'trace'},
        'lanes': [{'id': 'data', 'label': '참고 데이터'}, {'id': 'engine', 'label': '판단 엔진'}, {'id': 'rules', 'label': '매매 규칙'},
                  {'id': 'guard', 'label': '보호 장치', 'variant': 'exception'}, {'id': 'exec', 'label': '실행·기록'}],
        'phases': [{'id': 'decide', 'label': '입력·판단', 'fromCol': 0, 'toCol': 2, 'variant': 'emphasis'},
                   {'id': 'act', 'label': '보유·실행·기록', 'fromCol': 4, 'toCol': 5, 'variant': 'dashed'}],
        'mainPath': route, 'nodes': nodes, 'edges': edges,
    }


def regime_rows(st):
    """How the implemented rules differ by market state, with the row in force now. The long exit styles are written
    out; adds, stops and the inverse exit come from the runner's own rules (nh_paper_isolated, read only)."""
    import nh_paper_isolated as runner
    from etf_universe import SYMBOLS
    s = st['settings']
    flat = (st.get('clock') or {}).get('flatten', FLATTEN_AT)
    defensive = f"방어형: 데이터 목표(20일 장중 상승폭×국면 계수)에서 전량·−{s['stop_loss_pct']:g}%"
    trend = f"추세형: +{s['trend_half_pct']:g}% 절반·고점 −{s['trend_trail_pct']:g}% 추적·−{s['stop_loss_pct']:g}%·본전"
    keep = f"종가매매 조건 11개를 모두 충족한 종목만 보유({s['overnight_max_manwon']:g}만원 한도), 그 외 정리"
    entry = f"Jev {s['entry_confidence']:.2f}↑ + 분봉 상승"
    weak = (f"약세장: 늦어도 +{s.get('weak_half_pct', 1.0):g}% 절반·+{s.get('weak_all_pct', 2.0):g}% 전량(데이터 목표가 낮으면 그 값)·"
            f"−{s['stop_loss_pct']:g}%·본전")
    entry = entry + ' · Jev 대기 시 관심종목 강도 → 신규주'  # the three entry paths, in order
    rows = []
    long, inverse, inv = SYMBOLS['K200'], SYMBOLS['INV'], runner.INVERSE
    # the long exit style per row mirrors nh_paper_isolated.trend_mode / weak_market (a test keeps them in step)
    for regime, tones, exits, close in (
            ('상승장', ('강세',), trend, keep + ' · 인버스는 정리'),
            ('상승장', ('혼조', '판단 불가'), trend, f'{flat} 정리 (강세일만 보유 검토)'),
            ('상승장', ('약세',), defensive + ' (이미 추세형이면 유지)', f'{flat} 정리'),
            ('횡보장', ('강세',), trend + ' (당일)', f'{flat} 정리'),
            ('횡보장', ('혼조', '약세', '판단 불가'), defensive, f'{flat} 정리'),
            ('하락장', ('강세',), trend + ' (약세장 반등, 당일)', f'{flat} 정리'),
            ('하락장', ('혼조', '판단 불가'), weak, f'{flat} 정리'),
            ('하락장', ('약세',), weak, f'{flat} 정리 · 인버스는 ' + keep)):
        views = [{'코스피': {'regime': regime, 'today_tone': tone}} for tone in tones]
        blocked = {runner.averaging_down_blocked(view, long, False, '코스피') for view in views}
        stop = lambda code: min(s['stop_loss_pct'], 1.0) if regime in runner.tightening_regimes(code) else s['stop_loss_pct']  # noqa: E731
        trending = [tone for tone, view in zip(tones, views) if runner.trend_mode(view, '코스피', True)]
        crash_style = ('추세형' if len(trending) == len(tones) else '방어형' if not trending
                       else f"{'·'.join(trending)}일 추세형, 그 외 방어형")
        kospi = st['indices'].get('코스피') or {}
        rows.append({'국면': regime, '오늘 흐름': '·'.join(tones), '진입': entry,
                     '추가 매수(롱)': '중단' if blocked == {True} else '허용' if blocked == {False} else '일부 중단',
                     # inverse_gate: only a 하락장 lets an inverse buy or add, and then only when the tape agrees
                     '추가 매수(인버스)': ('중단' if runner.inverse_gate({'코스피': {'regime': regime}}, '코스피') == 'inverse_regime_not_falling'
                                     else '조건부'),
                     '청산': exits.replace(f"−{s['stop_loss_pct']:g}%", f"−{stop(long):g}%"),
                     '인버스 청산': (f"폭락일(지수 −{inv['crash_pct']:g}% 이하)이 아니면 +{inv['quick_tp_pct']:g}% 전량 · "
                                 f"폭락일은 {crash_style} · 손절 −{stop(inverse):g}%"),
                     '장 마감': close,
                     '현재': st['market_fresh'] and kospi.get('regime') == regime and kospi.get('tone') in tones})
    return rows


def _mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:  # removed by another session's cleanup meanwhile
        return 0.0


# Archify sizes its page to the viewport (body and canvas min-height 100vh) and lists the nodes under the canvas. In a
# dashboard iframe whose height follows its content that grew without end (2026-11-19: 99,548px, an empty-looking canvas),
# so the page is as tall as the diagram itself: the frame then fits it at any width with no inner scroll.
# The diagram is also pinned to the frame width (height from its own aspect ratio): Archify's reader layout sized wide
# diagrams (ratio >= 1.55) from the window height, so they grew a few pixels at a time (2026-11-19, 신규주 · 장기투자 도식).
FRAME_CSS = ('<style id="omni-frame">html,body{min-height:0!important;height:auto!important}'
             '.container{max-width:none!important;width:auto!important}'
             '.diagram-container{min-height:0!important;height:auto!important}'
             '.diagram-container>svg{width:100%!important;height:auto!important;max-height:none!important;min-height:0!important}'
             '.reader-rail{display:none!important}</style>')


def render(diagram, out_dir, kind='workflow', prefix='strategy-map'):
    """Draw with Archify (`kind`: workflow, lifecycle, ...); returns the HTML path (cached by content) or raises when
    Archify is unavailable. Each `prefix` keeps its own last few files."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    text = json.dumps(diagram, ensure_ascii=False, sort_keys=True) + FRAME_CSS  # a new frame rule re-renders once
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]
    html = out_dir / f'{prefix}-{digest}.html'
    if html.exists():
        return html
    node = shutil.which('node')
    if not node or not ARCHIFY.exists():
        raise FileNotFoundError('node or Archify not installed')
    # A private name per render: Archify never publishes over a file another session just wrote (it refused, and an
    # interrupted replace left .archify-remove-* folders behind); the finished file is renamed into place.
    work = f'{prefix}-{digest}.{uuid.uuid4().hex[:8]}'
    candidate, drawn = out_dir / f'{work}.json', out_dir / f'{work}.html'
    try:
        candidate.write_text(json.dumps({**diagram, 'meta': {**diagram['meta'], 'output': drawn.name}}, ensure_ascii=False), encoding='utf-8')
        result = subprocess.run([node, str(ARCHIFY), 'render', kind, candidate.name, drawn.name, '--quality', 'standard'],
                                cwd=out_dir, capture_output=True, text=True, timeout=30, encoding='utf-8')
        if result.returncode != 0 or not drawn.exists():
            raise ValueError('Archify render failed: ' + (result.stderr or result.stdout)[-300:])
        drawn.write_text(drawn.read_text(encoding='utf-8').replace('</head>', FRAME_CSS + '</head>', 1), encoding='utf-8')
        candidate.replace(out_dir / f'{prefix}-{digest}.json')
        drawn.replace(html)
    finally:
        candidate.unlink(missing_ok=True)
        drawn.unlink(missing_ok=True)
    done = [p for p in out_dir.glob(f'{prefix}-*') if len(p.suffixes) == 1]  # not another session's work file
    for old in sorted(done, key=_mtime)[:-10]:  # keep the last few
        old.unlink(missing_ok=True)
    return html


def dot(diagram):
    """Graphviz fallback with the same nodes, edges and highlighted main path."""
    route = diagram['mainPath']
    lines = ['digraph G {', 'rankdir=LR; bgcolor="transparent"; node [shape=box style="rounded,filled" fontname="sans-serif" '
             'fontcolor="#e2e8f0" color="#475569" fillcolor="#1e293b"]; edge [color="#64748b" fontcolor="#94a3b8" fontsize=10];']
    for lane in diagram['lanes']:
        lines.append(f'subgraph cluster_{lane["id"]} {{ label="{lane["label"]}"; fontcolor="#94a3b8"; color="#334155";')
        for n in (n for n in diagram['nodes'] if n['lane'] == lane['id']):
            hot = ' color="#22c55e" penwidth=2.5' if n['id'] in route else ''
            lines.append(f'"{n["id"]}" [label="{n["label"]}\\n{n.get("sublabel", "")}\\n[{n.get("tag", "")}]"{hot}];')
        lines.append('}')
    for e in diagram['edges']:
        hot = ' color="#22c55e" penwidth=2.5' if e.get('role') == 'main' else ' style=dashed' if e.get('role') in ('async', 'error') else ''
        lines.append(f'"{e["from"]}" -> "{e["to"]}" [label="{e.get("label", "")}"{hot}];')
    return '\n'.join(lines + ['}'])
