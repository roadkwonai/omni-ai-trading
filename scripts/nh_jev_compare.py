"""Jev chart analysis for the NH paper experiment; never submits orders."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import time
import urllib.request

from dotenv import dotenv_values
import matplotlib
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from local_env import ENV  # <repo>/.env (see .env.example)


def openai_cost(usage):
    details = usage['input_tokens_details']
    cached = details.get('cached_tokens', 0)
    writes = details.get('cache_write_tokens', 0)
    ttl = details.get('cache_write_tokens_details', {})
    short = ttl.get('ttl_30m_tokens', 0)
    long = ttl.get('ttl_12h_tokens', 0)
    if short + long != writes or cached + writes > usage['input_tokens']:
        raise ValueError('unsupported token breakdown')
    return ((usage['input_tokens'] - cached - writes) * 0.10 + cached * 0.01
            + short * 0.125 + long * 0.25) / 1_000_000


def chart_png(bars):
    fig, axes = plt.subplots(3, 2, figsize=(12, 9), layout='constrained')
    try:
        for ax, name in zip(axes.flat, ('K200', 'INV', 'LEV', 'SAMSUNG', 'NAVER', 'KAKAO')):
            rows = list(reversed(bars[name]))
            closes = [int(row['stck_prpr']) for row in rows]
            ax.plot(range(len(rows)), closes, color='#1565c0', linewidth=1.5)
            ax.set_title(name + ' 1-min closes (oldest to newest)', fontsize=10)
            ax.grid(alpha=0.2)
            ax.set_xticks([0, len(rows) - 1], [rows[0]['bsop_time'], rows[-1]['bsop_time']])
        out = io.BytesIO()
        fig.savefig(out, format='png', dpi=100)
        return out.getvalue()
    finally:
        plt.close(fig)


def jev(bars, news):
    key = dotenv_values(ENV, interpolate=False).get('JEV_API_KEY')
    if not key:
        raise ValueError('JEV_API_KEY missing')
    image = chart_png(bars)
    ref = 'chart-' + hashlib.sha256(image).hexdigest()[:20]
    payload = {'model': 'jev-1.14.0',
        'state': {'chart': ref, 'chart_description': 'K200, INV, LEV, SAMSUNG, NAVER and KAKAO one minute closes, oldest to newest',
                  'recent_news_headlines': news},
        'assets': {ref: {'type': 'image', 'format': 'png', 'data_base64': base64.b64encode(image).decode('ascii')}},
        'questions': {
            'direction': {'type': 'choice', 'instructions': 'What is the visible recent direction of K200 in `chart`?',
                'criteria': {'up': 'overall rising', 'down': 'overall falling', 'unclear': 'sideways or unclear'}},
            'candidate': {'type': 'choice', 'instructions': 'Which paper-trading candidate is best supported by the chart and news?',
                'criteria': {'WAIT': 'insufficient evidence or wait', 'K200': 'KODEX 200',
                             'INV': 'KODEX inverse', 'LEV': 'KODEX leverage', 'SAMSUNG': 'Samsung Electronics 005930',
                             'NAVER': 'NAVER 035420', 'KAKAO': 'Kakao 035720'}}}}
    req = urllib.request.Request('https://api.typesafe.ai/v1/systemone',
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                 'TypeSafe-Danger-Zone-Images': 'true'})
    start = time.monotonic()
    from jev_activity import regular_request
    with regular_request():
        with urllib.request.urlopen(req, timeout=25) as response:
            result = json.load(response)
    elapsed = round((time.monotonic() - start) * 1000)
    answers = result.get('answers')
    if not isinstance(answers, dict) or not all(isinstance(answers.get(x), dict) for x in ('candidate', 'direction')):
        raise ValueError('invalid Jev response')
    if answers['candidate'].get('choice') not in ('WAIT', 'K200', 'INV', 'LEV', 'SAMSUNG', 'NAVER', 'KAKAO'):
        raise ValueError('invalid Jev candidate')
    tokens = result.get('usage', {}).get('input_tokens')
    if type(tokens) is not int or tokens < 0:
        raise ValueError('invalid Jev usage')
    return {'model': result.get('model'), 'candidate': answers['candidate'], 'direction': answers['direction'],
            'input_tokens': tokens, 'estimated_usd': tokens * 0.042 / 1_000_000, 'elapsed_ms': elapsed,
            'chart_sha256': hashlib.sha256(image).hexdigest()}
