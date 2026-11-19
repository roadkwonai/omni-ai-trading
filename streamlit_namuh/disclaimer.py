"""Investment disclaimer and the paid-AI-tool notice shown on the dashboard (user 2026-11-19).

The same wording is used in README.md (투자 유의사항 및 면책 고지), the landing page footer and the report pages;
change them together.
"""
from __future__ import annotations

SHORT = ('모든 정보는 투자 판단을 위한 참고 자료이며 투자 권유가 아닙니다. '
         '투자의 최종 판단과 손익(원금 손실 포함)의 책임은 투자자 본인에게 있습니다.')

ORDER = ('주문 전에 확인하세요: AI 판단과 모의·가상 결과는 수익을 보장하지 않으며, '
         '이 주문의 판단과 손익(원금 손실 포함) 책임은 본인에게 있습니다.')

FULL = (
    '이 소프트웨어와 대시보드·리포트·알림이 제공하는 데이터, 분석, 차트, AI 판단은 정보 제공과 개인 학습을 위한 것이며, '
    '특정 금융상품의 매수·매도를 권유하지 않습니다. 투자자문·투자일임 서비스가 아닙니다.',
    '정보는 공개 자료와 증권사 API를 바탕으로 만들지만 정확성·완전성·적시성을 보장하지 않습니다. '
    '데이터 지연·오류, 프로그램 결함, 통신 장애로 주문이 늦거나 잘못될 수 있습니다.',
    'AI 판단(Jev·OpenAI·Claude 등), 모의 성과, 가상 체결, 과거 재현 결과는 과거 데이터와 알고리즘에 기반한 것으로 '
    '미래의 수익을 보장하지 않습니다.',
    '금융투자상품은 가격 변동에 따라 투자 원금의 손실(0~100%)이 생길 수 있고, 레버리지·인버스 ETF는 손실이 더 커질 수 있습니다. '
    '그 손실은 투자자에게 귀속됩니다.',
    '실계좌 자동매매를 켜는 것은 사용자 본인의 결정입니다. 투자의 최종 판단과 그로 인한 모든 손익의 책임은 투자자 본인에게 있으며, '
    '제작자와 기여자는 이 소프트웨어의 사용으로 생긴 직·간접 손해에 법적 책임을 지지 않습니다(MIT 라이선스의 무보증 조항).',
    'NH투자증권 등 증권사·데이터 제공사와 제휴·후원 관계가 없는 비공식 오픈소스 프로젝트입니다.',
)

AI_PAID = ('이 서비스는 AI 도구와 함께 쓰는 AI 트레이딩 시스템입니다. 설치·운영·리포트 작성에 Claude Code나 Codex 같은 '
           'AI 코딩 에이전트가 필요하고, 이를 쓰려면 해당 서비스의 유료 요금제(Claude Pro·Max 또는 ChatGPT Plus·Pro 같은 구독, '
           '또는 API 사용료)가 필요합니다. 매매 판단에 쓰는 Jev·OpenAI API도 각 서비스의 이용 조건과 요금을 따릅니다.')


def full_markdown():
    """The numbered full notice plus the paid-AI notice, as Markdown."""
    items = '\n'.join(f'{i}. {text}' for i, text in enumerate(FULL, 1))
    return f'**투자 유의사항 및 면책 고지**\n\n{items}\n\n**AI 도구 비용**\n\n{AI_PAID}'


def top_line(st):
    """One visible line under the page title on every tab, with the full notice in a popover."""
    note, more = st.columns([7, 1], vertical_alignment='center')
    note.warning(f'**투자 유의사항** · {SHORT} · AI 도구(Claude Code·Codex) 유료 이용 필요', icon=':material/gavel:')
    with more.popover('투자 유의사항', icon=':material/info:'):
        st.markdown(full_markdown())


def order_note(st):
    """A short reminder right before an order control (manual trades, the live-order switch)."""
    st.caption(f':material/gavel: {ORDER}')
