"""Read the AI models configured or observed inside launchd services."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from dotenv import dotenv_values

from paper_ai_modes import DECISIONS_MODEL, JEV_MODEL, read_modes
from sqlite_ro import ro_connect

REPO = Path(__file__).resolve().parents[1]
STORY_PROMPT_PREFIX = 'Long-term research task for a simulated (virtual) portfolio of KRX-listed US ETFs;'
PROVIDERS = {'openai-codex': 'OpenAI Codex', 'openai': 'OpenAI', 'anthropic': 'Anthropic', 'claude-cli': 'Claude'}
MODEL_ID = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9._/:-]{0,119}')


def _model(value):
    return value if isinstance(value, str) and MODEL_ID.fullmatch(value) else None


def _advisor_config(home, env):
    # Use the first file the CLI loads. Values supplied to the service take precedence.
    files = (home / '.vibe-trading/.env', REPO / 'agent/.env', home / '.vibe-trading-advisor/.env')
    try:
        path = next((p for p in files if p.is_file()), None)
        values = dotenv_values(path, interpolate=False) if path else {}
    except (OSError, UnicodeError):
        values = {}
    provider = _model(env.get('LANGCHAIN_PROVIDER', values.get('LANGCHAIN_PROVIDER'))) or 'openai'
    model = _model(env.get('LANGCHAIN_MODEL_NAME', values.get('LANGCHAIN_MODEL_NAME')))
    return PROVIDERS.get(provider, provider), model or '모델 설정 확인 불가'


def configured(name, root, home, env, *, known=True):
    """Configured display values and the role the model plays within a service."""
    model, provider, detail = '직접 호출 없음', '미사용', ''
    if name == 'shared-etf-paper':
        try:
            modes = read_modes(root / 'shared-etf-paper')
        except (OSError, ValueError):
            model, provider = '사용 설정 확인 불가', 'Jev · OpenAI'
        else:
            options = [(p, m) for enabled, p, m in ((modes['jev'], 'Jev', JEV_MODEL),
                       (modes['decision'], 'OpenAI', DECISIONS_MODEL)) if enabled]
            if options:
                provider = ' · '.join(p for p, _ in options)
                model = ' · '.join(m for _, m in options)
            else:
                model = 'AI 판단 꺼짐'
            model_states = [f"{identifier} {'켜짐' if modes[mode] else '꺼짐'}"
                            for mode, identifier in (('jev', JEV_MODEL), ('decision', DECISIONS_MODEL))]
            detail = '공유 모의 판단 설정 · ' + ' · '.join(model_states)
    elif name == 'market-movers':
        provider, model, detail = 'Jev', JEV_MODEL, '가격·수급 순위를 계산한 뒤 Jev가 관심종목 후보를 고릅니다.'
    elif name == 'live-follow-1':
        provider, model = 'Jev', f'{JEV_MODEL} (조건부)'
        detail = '평소에는 모의 체결을 미러합니다. 실계좌에만 남은 보유분을 별도로 검토할 때 Jev에 묻습니다.'
    elif name == 'vibe-longterm':
        provider, model = _advisor_config(home, env)
        detail = '장기투자 ETF의 테마 스토리 판단에 사용하는 모델입니다.'
    elif name == 'longterm-live-2':
        provider = '판단 참조'
        detail = '장기투자 실행기에 저장된 테마 스토리 판단과 계획을 읽습니다.'
    elif name == 'shared-etf-learning':
        from paper_online_model import JEV_VERSION, VERSION
        from isolated_paper_learning_watch import decision_mode

        provider, model = '통계 학습', JEV_VERSION
        if decision_mode(root / 'shared-etf-paper'):
            model = f'{model} · {VERSION}'
        detail = '가격 방향을 사후 관측하는 연구용 통계 모델입니다.'
    elif name == 'ai-schedule-runner':
        provider = model = '작업별 설정'
        detail = '전환한 예약 작업에 저장된 AI와 모델을 각각 사용합니다.'
    elif not known:
        model = provider = '확인 불가'
    return {'provider': provider, 'model': model, 'model_detail': detail}


def _query(path, sql, args=()):
    if not path.is_file():
        return None
    try:
        with ro_connect(path, timeout=0.5) as db:
            return db.execute(sql, args).fetchone()
    except (OSError, sqlite3.Error):
        return None


def _story_usage(home):
    advisor = home / '.vibe-trading-advisor/runs'
    try:
        paths = sorted(advisor.glob('*/llm_usage.json'), key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for path in paths:
        try:
            request = json.loads(path.with_name('req.json').read_text(encoding='utf-8'))
            if not isinstance(request, dict) or not str(request.get('prompt', '')).startswith(STORY_PROMPT_PREFIX):
                continue
            usage = json.loads(path.read_text(encoding='utf-8'))
            if not _model(usage.get('model')) or not usage.get('updated_at') or not usage['totals']['calls']:
                continue
        except (OSError, ValueError, AttributeError, TypeError, KeyError):
            continue
        return usage['model'], usage['updated_at'], '장기투자 스토리 판단 사용량 기록'
    return None


def last_model_use(name, root, home):
    """(model, time, evidence) from that service's own AI record, if retained."""
    if name == 'vibe-longterm':
        return _story_usage(home)
    if name == 'shared-etf-paper':
        row = _query(root / 'shared-etf-paper/trading-ledger.sqlite3', """
            SELECT time, CASE event
                WHEN 'jev_shadow' THEN coalesce(json_extract(data, '$.jev.model'), json_extract(data, '$.jev_model'))
                WHEN 'decision' THEN json_extract(data, '$.model')
                END AS model
            FROM events_v2
            WHERE event IN ('jev_shadow', 'decision') AND json_valid(data) AND model IS NOT NULL
            ORDER BY time DESC LIMIT 1
        """)
        return (row[1], row[0], '공유 모의 판단 원장') if row and _model(row[1]) else None
    if name == 'market-movers':
        path, question = root / 'shared-etf-paper/market-research.sqlite3', 'watchlist'
    elif name == 'live-follow-1':
        path, question = root / 'live-follow/jev-research/market-research.sqlite3', 'holding'
    else:
        return None
    # These stored responses have an engine and timestamp, without a per-response model ID.
    row = _query(path, "SELECT time FROM model_inputs WHERE engine='jev' AND question=? ORDER BY time DESC LIMIT 1", (question,))
    return (None, row[0], 'Jev 응답 원장 · 이전 기록에는 모델 ID 미기록') if row else None
