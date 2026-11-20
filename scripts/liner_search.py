"""On-demand Liner web search for public research queries."""
from __future__ import annotations

from urllib.parse import urlsplit

import requests
from dotenv import dotenv_values

import local_env

ENDPOINT = 'https://platform.liner.com/api/v1/tools/search/web'
ANALYSIS_ENDPOINT = 'https://platform.liner.com/api/v1/agents/search'


def _request_json(endpoint: str, body: dict, timeout: int) -> dict:
    key = (dotenv_values(local_env.ENV, interpolate=False).get('LINER_API_KEY') or '').strip()
    if not key:
        raise ValueError('LINER_API_KEY가 미설정 상태입니다.')
    try:
        response = requests.post(
            endpoint, headers={'x-api-key': key}, json=body, timeout=timeout, allow_redirects=False,
        )
    except requests.RequestException:
        raise ValueError('Liner에 연결하지 못했습니다.') from None
    if response.status_code != 200:
        raise ValueError(f'Liner 요청에 실패했습니다(HTTP {response.status_code}).')
    try:
        payload = response.json()
    except ValueError:
        raise ValueError('Liner 응답 형식을 읽을 수 없습니다.') from None
    if not isinstance(payload, dict):
        raise ValueError('Liner 응답 형식을 읽을 수 없습니다.')
    return payload


def source_rows(items: list) -> list[dict[str, str]]:
    results = []
    for result in items:
        if not isinstance(result, dict):
            continue
        title, url = result.get('title'), result.get('url')
        if not isinstance(url, str):
            continue
        if '\\' in url or any(c.isspace() or ord(c) < 32 for c in url):
            continue
        try:
            parts = urlsplit(url)
            if parts.scheme not in ('https', 'http') or not parts.hostname or parts.username is not None:
                continue
        except ValueError:
            continue
        description, date = result.get('description'), result.get('date')
        results.append({'제목': title.strip() if isinstance(title, str) and title.strip() else parts.hostname,
                        '발행일': date if isinstance(date, str) else '',
                        '요약': description if isinstance(description, str) else '', '원문 링크': url})
    return results


def search(query: str) -> list[dict[str, str]]:
    query = query.strip()
    if not query:
        raise ValueError('검색어를 입력하세요.')
    payload = _request_json(ENDPOINT, {
        'query': query, 'country_code': 'kr', 'lang': 'ko', 'max_results': 10, 'include_knowledge': False,
    }, 10)
    items = payload.get('results')
    if not isinstance(items, list):
        raise ValueError('Liner 응답에 검색 결과 목록이 없습니다.')
    return source_rows(items[:10])


def analyze(messages: list[dict[str, str]]) -> tuple[str, list[dict[str, str]]]:
    payload = _request_json(ANALYSIS_ENDPOINT, {
        'messages': messages, 'lang': 'ko', 'mode': 'general', 'stream': False,
    }, 310)
    answer = payload.get('answer')
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError('Liner가 분석 답변을 반환하지 않았습니다. 다시 요청할 수 있습니다.')
    references = payload.get('references')
    return answer.strip(), source_rows(references) if isinstance(references, list) else []
