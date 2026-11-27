"""Questions users can try in the AI search dashboard."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
USAGE = (
    '이 서비스가 처음인데 대시보드에서 어떤 순서로 보면 되나요?',
    '가이드 센터와 화면 안내는 어디에서 열 수 있나요?',
    'Jev가 왜 매수하거나 대기했는지 어디에서 확인하나요?',
    '매매일지에서 하루의 매수·매도 차트는 어떻게 열어요?',
    '성과 비교에서 기간과 KODEX 200 비교는 어떻게 확인하나요?',
    '시장 상황에서 오늘 장전 분석과 외국인 수급은 어디에 있나요?',
    '이 서비스에서 장기투자 거장 모듈과 목표 비중은 어디에서 보나요?',
    '운영 화면에서 API 오류와 실행기 상태를 어떻게 확인하나요?',
    '모의계좌와 실계좌를 화면에서 어떻게 구별하나요?',
    'AI 검색에서 추천 질문, 검색 이력, 마크다운 복사는 어떻게 쓰나요?',
)
INVESTING = (
    'ETF와 일반 펀드는 어떻게 다른가요?',
    '레버리지 ETF와 인버스 ETF를 며칠 보유하면 어떤 일이 생기나요?',
    '복리와 정기 적립식 투자의 차이를 예로 설명해 주세요.',
    '분산 투자와 리밸런싱은 각각 어떤 역할을 하나요?',
    'PER·PBR·ROE는 어떤 상황에서 유용하나요?',
    '배당수익률과 배당성향은 어떤 차이가 있나요?',
    '실현손익과 평가손익은 어떻게 구별하나요?',
    '수익률이 같아도 최대 낙폭이 다르면 어떤 차이가 있나요?',
)
INVESTORS = (
    '워런 버핏은 경제적 해자와 자본배분을 어떻게 평가하나요?',
    '피터 린치는 종목을 유형별로 나눠 어떻게 살펴봤나요?',
    '벤저민 그레이엄의 안전마진은 무엇인가요?',
    '레이 달리오의 올웨더 전략은 어떤 경제 국면을 고려하나요?',
    '존 보글은 왜 저비용 인덱스 펀드를 강조했나요?',
    '하워드 막스가 말한 투자 사이클과 2차적 사고는 무엇인가요?',
    '제시 리버모어와 윌리엄 오닐의 추세 매매에는 어떤 공통점이 있나요?',
    '조지 소로스의 재귀성과 시장 심리는 어떤 관계인가요?',
)
MARKET = (
    '장전 분석에서 나스닥·환율·유가를 각각 왜 확인하나요?',
    '외국인 순매수와 프로그램매매는 어떻게 해석하나요?',
    '금리 변화가 성장주와 금융주에 전달되는 경로는 무엇인가요?',
    '실적 발표에서 가이던스와 컨센서스는 어떻게 비교하나요?',
    'VWAP과 거래량 배율은 어떤 정보를 주나요?',
    '코스피200 선물과 현물 지수의 괴리는 무엇인가요?',
    'OpenDART 공시에서 유상증자와 공개매수는 어떻게 확인하나요?',
    '장중 반등과 추세 전환은 어떤 지표로 구별하나요?',
)
JEV = (
    '이 서비스에서 Jev의 투자 성과를 어디에서 보고 어떻게 해석하나요?',
    '이 서비스에서 Jev의 수익률·최대 낙폭·이긴 날은 어떻게 읽나요?',
    '이 서비스의 Jev 성과를 KODEX 200과 비교할 때 무엇을 확인해야 하나요?',
    'Jev의 모의 성과와 실계좌 성과를 비교하려면 어떤 자료가 필요한가요?',
    'Jev의 매수 판단과 주문·체결 기록은 어디에서 확인하나요?',
    'Jev 트레이딩과 장기투자 성과를 비교할 때 투자 기간과 기준을 왜 맞춰야 하나요?',
)
STRATEGY = (
    '이 서비스에서 낙폭과대 반등과 선별 v2는 어떻게 다른가요?',
    '종가매매와 장중 분할 진입은 어떤 차이가 있나요?',
    '손절·분할 익절·추적 손절은 각각 언제 쓰이나요?',
    '백테스트와 모의 체결, 실계좌 체결의 차이는 무엇인가요?',
    '실계좌 미러에서 가격 추격과 미체결 차단은 어떤 역할을 하나요?',
    '소수의 급등일이 전략 수익률에 미친 영향은 어떻게 확인하나요?',
    '장기 포트폴리오에서 코어·위성·저수지는 각각 어떤 역할인가요?',
    '매매일지에서 진입 근거와 청산 결과를 어떻게 복기하나요?',
)

# Money that is hard to get back: check an offer before sending, and split a retirement payout by when it is needed.
OFFER = (
    '원금을 보장하고 월 5% 수익을 준다는 투자 권유를 받았습니다. 보내기 전에 확인할 점과 공식 확인처를 알려 주세요.',
    '유명인이 추천하는 투자 대화방이 사칭인지 어디에서 어떻게 확인하나요?',
    '금융회사나 투자자문업체가 정식으로 등록된 곳인지 어디에서 확인하나요?',
    '투자 앱을 설치하거나 원격 제어를 허용해 달라고 하면 무엇을 의심해야 하나요?',
    '이미 투자금을 보냈는데 사기가 의심됩니다. 가장 먼저 무엇을 해야 하나요?',
    '퇴직금을 한꺼번에 받을 때와 연금으로 받을 때 무엇을 비교해야 하나요?',
    '퇴직 후 곧 쓸 돈과 오래 둘 돈은 어떤 기준으로 나누나요?',
)
# Outside trading: general questions before starting a business, recorded with scripts/startup_template.py.
STARTUP = (
    '퇴직 후 음식점을 창업하기 전에 확인할 공공 자료는 무엇인가요?',
    '가맹사업 정보공개서에서 먼저 볼 항목은 무엇인가요?',
    '가게를 열려는 동네의 상권 정보는 어디에서 어떻게 확인하나요?',
    '업종별 생존율과 폐업 통계는 어디에서 보고 어떻게 읽나요?',
    '월세와 인건비가 있는 가게의 손익분기점을 수식 기호 없이 예를 들어 계산해 주세요.',
    '창업 자금과 생활비, 비상금을 나눠 두는 방법을 예로 설명해 주세요.',
    '작은 가게를 언제 정리할지 월 적자와 날짜를 기준으로 정하는 예를 수식 기호 없이 보여 주세요.',
    '폐업할 때 드는 비용과 확인할 공공 지원은 무엇인가요?',
)

def glossary() -> dict[str, str]:
    from guides import GUIDES

    terms = {}
    for guide in GUIDES.values():
        if isinstance(definitions := guide.get('terms'), dict):
            terms.update(definitions)
    return terms


def groups() -> dict[str, tuple[str, ...]]:
    return {
        '서비스 사용법': USAGE,
        '서비스 용어': tuple(f'이 서비스에서 ‘{term}’의 뜻은 무엇인가요?' for term in glossary()),
        'Jev·투자 성과': JEV,
        '투자 거장': INVESTORS,
        '투자 기초': INVESTING,
        '시장·데이터': MARKET,
        '전략·위험 관리': STRATEGY,
        '노후 자금·투자 권유': OFFER,
        '창업 준비': STARTUP,
    }


GUIDE_SECTIONS = {
    'docs/ai-search.md': {
        '빠른 시작': ('AI 검색', 'AI 검색 사용법', 'AI 검색 가이드', 'AI 검색 처음'),
        '웹 검색과 최근 주요 뉴스': ('AI 검색 결과', '뉴스 별점', '시장 영향 분석', '형광펜'),
        '투자 AI 채팅과 출처': ('AI 채팅', '마크다운 복사', 'AI 답변 출처'),
        '내부 데이터 읽기': ('내부 조회', '내부 데이터', '원장 조회'),
        '추천 질문': ('다른 질문', '추천 태그'),
        '투자 공부 순서': ('투자 공부', '투자 초보'),
        '투자 권유 확인': ('투자 권유', '리딩방', '투자 사기'),
        '창업 준비 점검': ('창업 준비', '창업 점검', '폐업'),
        '작은 창으로 묻기': ('작은 창', '플로팅', 'AI에게 묻기', '채팅창'),
        '화면에서 바로 묻기': ('드래그', '화면에서 묻기', '용어 묻기', '카드 묻기'),
        '화면 배치와 스크롤': ('AI 검색 화면', '채팅 스크롤', '모달 스크롤', '패널 접기', '카드 접기'),
        '지난 대화': ('대화 이력', '이전 대화', '지난 답변', '대화 기록'),
        '다시 요청하기': ('검색 이력', '채팅 이력'),
        '진행 상태와 오류': ('AI 검색 로딩', '경과 시간', 'AI 검색 오류'),
        '키 설정과 요금': ('AI 검색 요금', 'Liner 키'),
    },
    'docs/dashboard.md': {
        '화면 구성': ('대시보드 처음', '대시보드 사용법', '대시보드 메뉴'),
        '탭 한눈에 보기': ('탭 목록', '대시보드 기능'),
        '가이드 팝업과 도식': ('가이드 센터', '화면 안내', '가이드 팝업'),
        '시장 상황': ('장전 분석', '투자자 수급', '외국인 수급', '프로그램매매'),
        '트레이딩 현황': ('체결 기록', '매수 판단', '진입 차단', '판단 근거', '대기했는지', '모의계좌'),
        '매매일지': ('매매 복기', '매수·매도 차트', '일별 거래 내역'),
        '성과 비교': ('엔진 비교', 'Jev 수익률', 'Jev 성과', '최대 낙폭', '투자 성능', 'CSV로 받을', '일별 기록', 'CSV 내려받기', 'CSV로 내려받'),
        '장기투자': ('목표 비중', '가상 장부', '장기투자 설정'),
        '실계좌': ('실계좌 미러', '보호 한도'),
        '운영': ('실행기 상태', 'API 오류', 'API 연동', '자동 작업', 'AI·실행 기록'),
        '화면 설정': ('자동 갱신', '글자 크기', '화면 테마'),
    },
    'docs/strategy.md': {
        'Jev가 받는 자료': ('Jev 판단 근거', 'Jev 신뢰도', 'Jev 입력'),
        '진입 경로': ('매수 조건', '매수 진입', '관심종목 강도'),
        '낙폭과대 반등 (0번 경로)': ('낙폭과대', '선별 v2', '낙주매매'),
        '청산 프로필': ('추적 손절', '분할 익절', '방어형', '추세형'),
        '장 마감과 이월·스윙': ('이월 보유', '이월 조건', '종가매매', '마감 정리'),
        '제한 규칙': ('재진입 제한', '연속 손실', '물타기'),
    },
    'docs/longterm.md': {
        '거장 모듈': ('거장 카드', '달리오 모듈', '린치 모듈', '저수지'),
        '국면 판정': ('확정 국면', '장기 국면'),
        '장부와 비교': ('비교 장부', '비교 전략', '그림자 장부', '그림자 비교'),
        '대시보드 장기투자 탭': ('장기투자 사용법', '장기투자 화면'),
    },
    'docs/nxt.md': {
        '원칙': ('NXT 가상', 'NXT 종가매매'),
        '하루 흐름': ('NXT 야간 거래', 'NXT 사용법', 'NXT 프리마켓'),
        '다음 날 청산': ('NXT 청산',),
    },
    'docs/integrations.md': {
        'Liner': ('AI 검색', '투자 AI 채팅', '추천 질문', '검색 이력', '마크다운 복사'),
        'TypeSafe Jev': ('Jev 연동', 'Jev API'),
    },
}


def normalized(text: str) -> str:
    return re.sub(r'[^a-z0-9가-힣]', '', text.casefold())


@lru_cache(maxsize=12)
def read_sections(relative: str, _mtime: int) -> dict[str, str]:
    try:
        text = (REPO / relative).read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return {}
    text = re.sub(r'(?ms)^\x60{3}.*?^\x60{3}[ \t]*$', '', text)
    parts = re.split(r'(?m)^#{1,3}[ \t]+(.+)$', text)
    return {
        title.strip(): '\n'.join(line for line in body.splitlines() if not line.lstrip().startswith(('![', '<'))).strip()
        for title, body in zip(parts[1::2], parts[2::2])
    }


def find_guides(question: str) -> list[dict[str, str]]:
    """Return only relevant excerpts from the dashboard guides and selected documentation sections."""
    query = normalized(question)
    hits = []
    for term, explanation in glossary().items():
        keyword = normalized(term)
        if keyword in query and (len(keyword) > 2 or f'‘{term}’' in question):  # '상관' is also in '상관없이'
            hits.append((len(keyword) + 12, {'문서': 'streamlit_namuh/guides.py', '항목': term, '본문': explanation}))

    jev_performance = 'jev' in query and any(word in query for word in ('성과', '성능', '수익', '낙폭', '승률'))
    intro = ('사용법' in query and len(query) < 13) or (
        any(word in query for word in ('이서비스', '대시보드', '이앱'))
        and any(word in query for word in ('처음', '사용법', '어떤순서', '시작', '메뉴'))
    )
    for relative, allowed in GUIDE_SECTIONS.items():
        candidates = []
        for title, aliases in allowed.items():
            keywords = [term for alias in (title, *aliases) if len(term := normalized(alias)) > 2 and term in query]
            score = max(map(len, keywords), default=0)
            if title == '성과 비교' and jev_performance:
                score += 15
            if intro and title in ('화면 구성', '가이드 팝업과 도식'):
                score += 4
            if score:
                candidates.append((score, title))
        if not candidates:
            continue
        try:
            sections = read_sections(relative, (REPO / relative).stat().st_mtime_ns)
        except OSError:
            continue
        for score, title in candidates:
            if body := sections.get(title):
                excerpt = body[:1500]
                if len(body) > len(excerpt) and '\n' in excerpt:
                    excerpt = excerpt.rsplit('\n', 1)[0]
                hits.append((score, {'문서': relative, '항목': title, '본문': excerpt}))
    hits.sort(key=lambda hit: hit[0], reverse=True)
    return [entry for _, entry in hits[:3]]
