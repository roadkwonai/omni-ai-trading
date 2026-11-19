"""실전 시황 트레이딩 lens for Jev (user 2026-11-18): the questions the book asks before an entry and while holding,
in our own short words, sent with every Jev entry and holding decision as reference context, not a trading rule.

The full knowledge lives in the skill .agents/skills/danswer-market-trading (Claude reads it for premarket analysis,
the journal and reviews). Jev, an external model, cannot read a skill, so it gets this summary. No figures from the book:
they belong to its own setups and accounts (its cheatsheet says so). The approved entry, stop and limit rules decide orders.
"""
from __future__ import annotations

ROLE = '실전 시황 트레이딩 관점 요약(참고 맥락, 매매 규칙 아님)'
COMMON = (
    '시황부터: 오늘 지수 방향과 돈이 몰리는 섹터·테마를 먼저 읽고 그 흐름과 같은 쪽을 본다',
    '근거 확인: 뉴스는 국내 영향·지속성과 분봉의 실제 반응이 함께 있을 때만 근거로 삼는다',
    '수급 해석: 프로그램 순매수 하나로 주체를 단정하지 않고 외국인·기관·시간대·가격 반응을 함께 본다',
)
ENTRY = (
    '주도 판단: 상승률과 거래대금이 함께 클 때 주도로 보고 하나만 크면 약한 후보로 본다',
    '테마는 대장 기준: 대장의 속도가 멈추면 뒤따르는 종목도 조심한다',
    '돌파 후보는 돈이 다른 종목·섹터로 옮겨 가면 철회한다',
    '호가가 얇으면 크게 들어가지 않는다(나올 때 밀린다)',
    '인버스는 지수 하락이 시황·뉴스·수급·미국 시장에서 함께 확인되는 폭락 흐름일 때만 고른다(애매한 하락·횡보에서는 손실이 잦았다)',
    '지수가 횡보하거나 약하게 빠질 때는 인버스보다 신규 상장주·거래대금 상위 주도주로 돈이 옮겨 가는지 먼저 본다(모든 신규주가 지수와 반대로 움직이지는 않는다)',
)
HOLDING = (
    '매수 근거(시황·돈의 흐름·대장)가 깨지면 보유 기간을 늘리지 말고 정리 쪽으로 본다',
    '단가를 낮추는 것만이 이유인 추가 매수는 피한다',
    '폭락일이 아닌 날의 인버스는 작은 수익이라도 나면 지키는 쪽으로 본다(반등이 오면 수익이 빨리 사라진다)',
    '기법마다 확인할 것이 다르다: 종가는 다음 날 갭, 돌파는 돈의 이동, 테마는 대장, 수급은 주체의 지속성',
)


def brief(kind):
    """The lens for one Jev question: kind 'entry' (pick a candidate) or 'holding' (keep or sell a held position)."""
    return {'role': ROLE, 'checks': list(COMMON) + list(ENTRY if kind == 'entry' else HOLDING)}
