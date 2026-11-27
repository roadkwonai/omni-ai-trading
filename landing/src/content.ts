export const REPO = 'https://github.com/roadkwonai/omni-ai-trading'
export const VERSION = '0.1.10' // the repository VERSION file; scripts/release.py --check compares them
export const doc = (name: string) => `${REPO}/blob/main/docs/${name}`
export const START = `${REPO}#빠른-시작`

// Same wording as streamlit_namuh/disclaimer.py and README '투자 유의사항 및 면책 고지' (test_disclaimer checks).
export const DISCLAIMER_SHORT = "모든 정보는 투자 판단을 위한 참고 자료이며 투자 권유가 아닙니다. 투자의 최종 판단과 손익(원금 손실 포함)의 책임은 투자자 본인에게 있습니다."
export const DISCLAIMER_FULL = [
  "이 소프트웨어와 대시보드·리포트·알림이 제공하는 데이터, 분석, 차트, AI 판단은 정보 제공과 개인 학습을 위한 것이며, 특정 금융상품의 매수·매도를 권유하지 않습니다. 투자자문·투자일임 서비스가 아닙니다.",
  "정보는 공개 자료와 증권사 API를 바탕으로 만들지만 정확성·완전성·적시성을 보장하지 않습니다. 데이터 지연·오류, 프로그램 결함, 통신 장애로 주문이 늦거나 잘못될 수 있습니다.",
  "AI 판단(Jev·OpenAI·Claude 등), 모의 성과, 가상 체결, 과거 재현 결과는 과거 데이터와 알고리즘에 기반한 것으로 미래의 수익을 보장하지 않습니다.",
  "금융투자상품은 가격 변동에 따라 투자 원금의 손실(0~100%)이 생길 수 있고, 레버리지·인버스 ETF는 손실이 더 커질 수 있습니다. 그 손실은 투자자에게 귀속됩니다.",
  "실계좌 자동매매를 켜는 것은 사용자 본인의 결정입니다. 투자의 최종 판단과 그로 인한 모든 손익의 책임은 투자자 본인에게 있으며, 제작자와 기여자는 이 소프트웨어의 사용으로 생긴 직·간접 손해에 법적 책임을 지지 않습니다(MIT 라이선스의 무보증 조항).",
  "NH투자증권 등 증권사·데이터 제공사와 제휴·후원 관계가 없는 비공식 오픈소스 프로젝트입니다.",
]
export const AI_PAID = "이 서비스는 AI 도구와 함께 쓰는 AI 트레이딩 시스템입니다. 설치·운영·리포트 작성에 Claude Code나 Codex 같은 AI 코딩 에이전트가 필요하고, 이를 쓰려면 해당 서비스의 유료 요금제(Claude Pro·Max 또는 ChatGPT Plus·Pro 같은 구독, 또는 API 사용료)가 필요합니다. 매매 판단에 쓰는 Jev·OpenAI API도 각 서비스의 이용 조건과 요금을 따릅니다."

// Service intro videos (YouTube, 권로드의 AI실험실 channel). Chapters match the video descriptions.
export const VIDEOS = [
  {
    id: 'JjL6to4mxN4',
    url: 'https://youtu.be/JjL6to4mxN4',
    length: '4분 4초',
    title: '노후·장기투자 편',
    text: '투자 권유를 받았을 때 AI 검색으로 근거부터 확인하고, 쓸 시점에 따라 돈을 나눠 정한 규칙으로 길게 운용하는 과정을 보여 줍니다. 첫 장면의 문자는 가상의 예시이며 화면은 모의·가상 계좌입니다.',
    thumb: './img/video-thumb-longterm.webp',
    alt: '영상 썸네일. 내 노후는 내가 지킨다. AI 검색으로 먼저 확인하고 장기투자는 정한 규칙대로 합니다.',
    chapters: [
      { t: 0, label: '도착한 문자' },
      { t: 56, label: '보내기 전에 묻기' },
      { t: 88, label: '모르는 말 바로 묻기' },
      { t: 108, label: '돈부터 나누기' },
      { t: 131, label: '정한 규칙으로 길게' },
      { t: 171, label: '폭락 뉴스가 뜬 날' },
    ],
  },
  {
    id: 'c4FZw6AYRAg',
    url: 'https://youtu.be/c4FZw6AYRAg',
    length: '3분 33초',
    title: 'Jev 트레이딩 편',
    text: '장중 매매와 장기투자가 어떻게 다른지, 주문을 막는 조건과 장 마감 뒤 남는 기록을 실제 대시보드로 보여 줍니다. 실계좌 금액은 가렸습니다.',
    thumb: './img/video-thumb.webp',
    alt: '영상 썸네일. Jev로 하는 단기 매매와 투자 거장의 원칙에 따른 장기투자를 소개합니다.',
    chapters: [
      { t: 0, label: '왜 망설이면 지는가' },
      { t: 36, label: '두 엔진 · AI는 의견, 규칙이 주문' },
      { t: 84, label: 'Jev의 실시간 판단과 매수 조건' },
      { t: 109, label: '여러 겹의 안전장치' },
      { t: 138, label: '장기투자 · 여운봉 저수지 매매법' },
      { t: 165, label: '복기와 리포트' },
    ],
  },
]

export const NAV = [
  { href: '#why', label: '만든 이유' },
  { href: '#ai-search', label: 'AI 검색' },
  { href: '#video', label: '소개 영상' },
  { href: '#ai', label: 'AI로 운영' },
  { href: '#cli', label: 'CLI' },
  { href: '#art', label: '투자 기법' },
  { href: '#jev', label: 'Jev' },
  { href: '#longterm', label: '장기투자' },
  { href: '#styles', label: '내 스타일' },
  { href: '#daily', label: '리포트' },
  { href: '#stack', label: '기반 기술' },
  { href: '#safety', label: '안전' },
  { href: '#start', label: '시작' },
]

export const PILLARS = [
  { href: '#jev', icon: 'Lightning', label: 'Jev 실시간 AI 트레이딩', body: '1분마다 차트와 뉴스로 판단' },
  { href: '#longterm', icon: 'Compass', label: '거장의 장기투자', body: '시장 국면에 맞춘 전략' },
  { href: '#dca', icon: 'PiggyBank', label: '적립식 투자', body: '새 적립금으로 ETF를 꾸준히 매수' },
  { href: '#ai', icon: 'ChatsCircle', label: 'AI 에이전트 운영', body: '채팅으로 묻고 맡기기' },
  { href: '#daily', icon: 'ClipboardText', label: '자동 점검과 리포트', body: '장전부터 마감까지 자동 점검' },
  { href: '#ai-search', icon: 'MagnifyingGlassPlus', label: 'AI 검색', body: '보던 화면에서 바로 묻기' },
]

export const RETIREMENT_POVERTY = {
  source: 'https://www.oecd.org/en/publications/pensions-at-a-glance-2025_e40274c1-en/full-report/old-age-income-poverty_3cd946d7.html',
  scale: [0, 30, 60],
  groups: [
    { label: '66세 이상 전체', korea: 39.7, oecd: 14.8 },
    { label: '66~75세', korea: 29.8, oecd: 13.1 },
    { label: '76세 이상', korea: 54.0, oecd: 17.2 },
  ],
}

// Three decisions around retirement that are hard to undo. `not` says what Omni does not do.
export const DECISIONS = [
  { kind: '투자 권유를 받았을 때', title: '믿고 보내도 될까요?', num: '6,581억 원', body: '2025년 불법 투자리딩방 사기의 피해액입니다(6,853건). 2024년의 7,104억 원보다 줄었지만 여전히 큽니다.', source: '경찰청 자료 · 파이낸셜뉴스', href: 'https://v.daum.net/v/WPn7aTKpAv',
    does: 'AI 검색으로 근거와 공식 확인처를 찾습니다.', not: '사기인지 판정하지는 않습니다.' },
  { kind: '퇴직 후 가게를 준비할 때', title: '이 돈을 넣어도 될까요?', num: '35%', body: '60대에 처음 창업한 사람 가운데 한 해 영업이익이 1천만 원에 못 미친 비율입니다(2022년 조사).', source: '한국은행 이슈노트 2025-12호', href: 'https://www.bok.or.kr/portal/bbs/P0002353/view.do?nttId=10091405&menuNo=200433',
    does: '공공 자료를 찾고 생활비와 멈출 기준을 점검표에 정리합니다.', not: '성공을 예측하지는 않습니다.' },
  { kind: '퇴직금을 받을 때', title: '언제 쓸 돈인가요?', num: '83.5%', body: '2025년 퇴직연금을 받기 시작한 계좌 가운데 일시금을 택한 비율입니다. 금액으로는 61.6%가 연금이었습니다.', source: '퇴직연금 통계 · 브라보마이라이프', href: 'https://bravo.etoday.co.kr/view/atc_view/20517',
    does: '오래 둘 돈만 따로 정해 적립하고, 비중과 판단 이유를 기록합니다.', not: '세금과 수령 방식은 조언하지 않습니다.' },
]
export const FIRST_BUSINESS = [
  { num: '9개월', label: '평균 창업 준비 기간입니다. 다른 연령대보다 짧습니다.' },
  { num: '3천만 원', label: '1인당 한 해 매출의 중앙값입니다.' },
  { num: '31%', label: '5년 안에 자영업을 그만둔 비율입니다. 20~50대와 큰 차이가 없습니다.' },
]

export const WHY_STATS = [
  { num: '42.5점', title: '장기 재무목표 설정', label: '한국은행·금융감독원의 2024년 성인 금융이해력 조사에서 장기 재무목표 설정 항목이 받은 점수입니다.', source: '금융위원회 조사 참고표', href: 'https://www.fsc.go.kr/comm/getFile?fileNo=8&fileTy=ATTACH&srvcId=BBSTY1&upperNo=87218' },
  { num: '60%', title: '조사 대상 신규 투자자 중 손실을 본 비율', label: '자본시장연구원이 4개 증권사의 2020년 3~10월 거래(1곳은 6월까지)를 분석했을 때, 조사 대상 신규 투자자 10명 중 6명이 거래 비용을 반영한 결과 손실을 봤습니다.', source: '자본시장연구원 연구', href: 'https://www.kcmi.re.kr/report/report_view?report_no=1243' },
]

export const WHY_CASES = [
  { kind: '금융위원회 공개 상담 사례', title: '수입이 불규칙하면 비상자금부터', body: '수입이 일정하지 않은 20대 프리랜서의 상담에서는 3개월 평균 수입을 바탕으로 재무 계획을 세우고 비상자금을 마련하는 일부터 다뤘습니다. Omni를 쓸 때도 먼저 운용할 수 있는 자금을 정하고 그 범위에서 적립과 비중 관리 기준을 세웁니다.', href: 'https://www.fsc.go.kr/no010101/87218', label: '상담 사례 보기' },
]

// Shown with the daily reports: one problem found in the records and how it was changed.
export const FIX_CASE = { kind: 'Omni v0.1.3 개선 사례', title: '개장 직후 판단이 늦었던 이유', body: '당일 1분봉이 쌓일 때까지 기다리느라 판단이 늦었습니다. 이제 08:55에 실행기를 준비합니다. 개장 직후 1분봉이 부족하면 전 거래일 마감 자료나 확인 가능한 당일 NXT 프리마켓 자료를 함께 씁니다.', href: `${REPO}/blob/v0.1.3/release/2026-11-20-v0.1.3-opening-and-inverse.md#개장-즉시-진입`, label: '릴리스에서 개선 과정 보기' }

export const TRAPS = [
  { trap: '이미 오른 종목을 뒤늦게 따라 삽니다', human: '차트와 뉴스, 수급을 따로 찾는 사이 가격이 바뀝니다.', fix: 'Jev가 자료를 함께 읽습니다. 실행기는 신규 매수 조건을 확인하고, 이미 많이 오른 가격을 따라 사지 않도록 막는 기준도 확인합니다. 주문을 보류한 이유도 남깁니다.' },
  { trap: '손절을 미루고 비중을 더 키웁니다', human: '매수 전에 손절과 추가 매수의 기준을 정하지 않으면 가격이 빠질 때 판단이 흔들립니다.', fix: '실행기는 1분마다 손절·익절과 주문 가능 여부를 확인합니다. 접수와 체결은 매매일지에서 따로 봅니다.' },
  { trap: '매일 시세를 들여다볼 시간이 없습니다', human: '장기투자를 하더라도 급락장이나 과열장에서 적립을 이어 갈지 고민됩니다.', fix: '장기 엔진은 정해 둔 적립과 비중 조정 기준으로 계획을 세웁니다. 전략별 결과는 가상 장부에서 비교할 수 있습니다.' },
]

// 매수는 기술, 매도는 예술 (#art). Every line maps to a rule in docs/strategy.md.
export const BUY_CRAFT = [
  { title: '시장 국면을 먼저 확인합니다', body: '코스피·코스닥을 상승장·횡보장·하락장으로 판정하고, 매수 방향에 불리한 시장일수록 주문 금액을 **0.75배, 0.5배**로 줄입니다.' },
  { title: '돈이 몰리는 곳을 찾습니다', body: '거래대금·거래량·상승률 순위, 테마 대장주, 투자자·프로그램 순매수로 ==오늘의 주도주 후보==를 추립니다.' },
  { title: 'AI가 1분마다 고릅니다', body: 'Jev는 차트, 최근 90분 뉴스, 시황과 수급을 보고 **매수 후보 한 종목이나 대기**를 고릅니다. 신뢰도도 함께 남깁니다.' },
  { title: '규칙이 한 번 더 거릅니다', body: '지수 방향과 최근 분봉·거래량을 확인합니다. 지수와 반대 방향으로 움직이는 인버스 ETF는 지수가 계속 내릴 때 살펴보고, 이미 많이 오른 ETF를 뒤늦게 따라 사는 것은 아닌지도 점검합니다.' },
  { title: '나눠 삽니다', body: '한 번에 다 사지 않고, **직전보다 나은 조건일 때만** 더 삽니다. 장 마감 30분 전부터는 추가 매수를 하지 않습니다.' },
]
export const SELL_ART = [
  { title: '절반은 먼저 챙깁니다', body: '방어형은 **+1%에 절반, +2%에 나머지**를 팝니다. 추세가 강하면 **+2%에 절반**을 팝니다.' },
  { title: '나머지는 추세를 따라갑니다', body: '남은 수량은 ==고점에서 가격이 1.5% 하락하면== 팝니다. 2배 ETF는 이 하락 폭을 1.5배로 넓힙니다.' },
  { title: '평균 매수가로 돌아오면 정리합니다', body: '절반을 팔아 수익을 확정한 뒤 가격이 평균 매수가로 돌아오면 **남은 수량도 매도**합니다.' },
  { title: '불리하면 손실을 짧게 끊습니다', body: '손절 기준은 기본 ==−2%==이며, 보유 종목에 불리한 시장에서는 ==−1%==로 좁힙니다. 계좌 전체에는 별도의 **하루 손실 한도**를 적용합니다.' },
  { title: 'AI가 보유 종목을 다시 살핍니다', body: '보유 종목을 매분 확인합니다. AI가 매도를 제안할 때 신뢰도 기준에 따라 **전량** 또는 **33%**를 팝니다. 매수 후 시장이 보유 방향과 반대로 바뀌면 **절반을 팝니다.**' },
  { title: '조건을 충족하면 다음 날까지 보유합니다', body: '==조건 11개를 통과한 추세형 종목만== 다음 날로 넘깁니다. 이후 보유 조건 6개를 매일 확인해 **최대 5거래일**까지 보유합니다.' },
]
// One position from entry to exit (#art diagram). `exits` can end it at any point.
export const LIFECYCLE = [
  { step: '진입', body: '1회 분할 매수 · 방어형 또는 추세형' },
  { step: '1차 익절', body: '+1~2%에서 남은 수량의 절반' },
  { step: '고점 대비 하락 시 매도', body: '고점 대비 −1.5%에 매도' },
  { step: '이월', body: '추세형 · 조건 11개 모두 통과' },
  { step: '스윙', body: '조건 6개 매일 확인 · 최대 5거래일' },
]
export const LIFECYCLE_EXITS = ['손절 −2% · 불리한 국면 −1%', '익절 뒤 본전 정리', 'Jev 판단에 따라 33% 또는 전량 매도', '보유 방향에 불리한 국면으로 바뀌면 절반 매도', '14:40 장 마감 정리', '계좌 하루 손실 한도']

// Jev by market regime (docs/strategy.md 시황별 대응).
export const REGIME_PLAYS = [
  { name: '상승장', tone: 'up', body: '지수 상승에 투자하는 종목을 주로 삽니다. ==추세형 종목은 더 길게 보유==하고, 조건을 충족하면 **다음 거래일로 넘깁니다.** 인버스는 매수 조건을 충족하지 못합니다.' },
  { name: '횡보장', tone: 'wait', body: '매수 금액은 **0.75배**로 줄이고 손절 기준은 **−1%**로 좁힙니다. 같은 날 다시 살 때는 추격 매수를 막고 AI에게 한 번 더 확인합니다. ==대부분 그날 정리==합니다.' },
  { name: '하락장', tone: 'down', body: '지수 상승에 투자하는 종목은 매수 금액을 줄이고 손절 폭을 좁힙니다. 인버스 ETF는 전용 매수 조건을 확인하고, 살 때의 지수 낙폭에 따라 수익을 확정할 가격을 정합니다.' },
]

// 내 투자 스타일 (#styles). `how` is the real setting path.
export const STYLES = [
  { name: '장기형', tag: '적립과 비중 관리', who: '매일 차트를 보지 않고 **오래 모으고 싶은 분**', run: '장기투자 + 적립식', how: '운영 엔진 → 장기투자만' },
  { name: '균형형', tag: '장기투자에 장중 매매를 병행', who: '장기투자를 중심으로 **장중 매매도 해 보고 싶은 분**', run: '장기투자 + Jev 트레이딩', how: '운영 엔진 → 둘 다 운영. 실계좌는 계좌마다 엔진 선택' },
  { name: '공격형', tag: '주도주 중심의 장중 매매', who: '주도주와 시장 흐름을 **적극적으로 따라가고 싶은 분**', run: 'Jev 트레이딩만', how: '운영 엔진 → Jev만. 진입 경로 빠른 선택 → 적극적' },
  { name: '실험형', tag: '먼저 지켜보기', who: '아직 확신이 없어 **결과부터 보고 싶은 분**', run: '모의계좌·가상 계좌로만', how: '기본값 그대로(실주문 스위치 꺼짐)' },
]
export const GROW = [
  { title: '진입 조건 고르기', body: '진입 경로마다 보수적·기본·적극적·끄기 중에서 고릅니다.' },
  { title: '매일 복기', body: '장 마감 개선안과 신호 적중률을 봅니다. 후보로 꼽았다가 사지 않은 종목의 가격 흐름도 확인합니다.' },
  { title: '승인한 것만 반영', body: '개선안을 받아 보고, **내가 승인한 설정만** 바꿉니다. 변경 이력은 기록에 남습니다.' },
]

// 설정은 JSON, 경험은 SQLite (#data).
export const DATA_JSON = [
  { file: 'accounts.json', body: '계좌, 운용금, 주문 보호 한도, 실주문 스위치' },
  { file: 'strategy-settings.json', body: '진입·익절·손절·이월 기준과 제한 규칙 스위치' },
  { file: 'tunables.json', body: '장 시간, 조회 주기 같은 운영 조정값' },
  { file: 'vibe-longterm/settings.json', body: '장기투자 거장 모듈, 비중, 적립식' },
]
export const DATA_SQLITE = [
  { file: 'trading-ledger', body: '판단·주문·체결 이벤트 기록' },
  { file: 'market-research', body: 'Jev 입력과 판단 근거, 신호 적중률, 실제로 매수하지 않은 비교용 후보' },
  { file: 'trading-journal', body: '일·월·분기·연 매매일지' },
  { file: 'oversold-cases', body: '10년 일봉으로 만든 낙폭 사례집' },
  { file: 'longterm', body: '장기투자 결정, 순자산, 주문 근거' },
  { file: 'settings-history', body: '모든 설정 변경의 이력' },
]

// First week of signal calibration (shared paper account, 11/11~11/17): higher confidence was not more accurate.
export const CALIBRATION = [
  { band: '0.6~0.7', hit: 72 },
  { band: '0.7~0.8', hit: 56 },
  { band: '0.8 이상', hit: 38 },
]

// AI 도구에서 쓰는 법 (#ai). Times are the scheduled tasks and launchd jobs in docs/automation.md.
export const USE_STEPS = [
  { icon: 'Download', title: 'AI 에이전트가 설치를 돕습니다', body: 'Claude Code나 Codex를 열고 설치 프롬프트를 붙여 넣으면, 에이전트가 **저장소를 내려받고 Python 가상환경, NH MCP와 프로젝트 스킬을 설정**합니다. 키를 넣거나 주문과 관련된 단계에서는 멈추고 확인을 받습니다.' },
  { icon: 'ChatsCircle', title: '운영은 대화로 합니다', body: '"오늘 왜 안 샀어?", "손절을 1.5%로 바꾸면 뭐가 달라져?"처럼 물으면 **기록을 읽고 근거와 함께** 답합니다. 설정은 사용자가 승인한 것만 바뀝니다.' },
  { icon: 'CalendarCheck', title: '반복 업무는 예약 작업이 맡습니다', body: '사람이 자리를 비워도 **예약 작업 15개**가 장전 점검과 분석, 장중 감사를 하고, 장 마감 개선안과 매매일지, 장기투자 리포트를 작성합니다.' },
]
export const SCHEDULE_CHIPS = ['08:25 장전 점검', '08:31 장전 상세 분석', '08:50 장기투자 전략 리포트', '매시 20분 장중 감사', '16:15 장 마감 개선안', '16:40 매매일지', '16:45 장기투자 복기']
export const AI_SKILLS = ['장전 분석', '장중 감사', '매매일지 작성', '장기투자 전략 리포트', 'HTML 리포트 발행', '대시보드 화면 확인', '다른 Mac으로 옮기기', '시황 트레이딩 점검', '운영 규칙·시황 조회', 'omni CLI 조회·설정']

// 기반 기술 (#stack). What Omni takes from each, in plain words (docs/architecture.md 원본 Vibe-Trading과 이 확장의 경계).
export const UPSTREAM = {
  name: 'HKUDS Vibe-Trading',
  href: 'https://github.com/HKUDS/Vibe-Trading',
  license: 'MIT 오픈소스',
  about: '홍콩대학교 데이터 지능 연구실(HKUDS)이 만든 개인 트레이딩 에이전트입니다. 금융 질문을 받아 **리서치 리포트, 백테스트, 모의 포트폴리오**를 만들어 주며, 시장·공시 리서치, 알파 벤치마크, 멀티 에이전트 리서치 팀, MCP 서버 기능을 갖췄습니다.',
  uses: [
    { title: 'MCP 연결 구조', body: 'NH 증권 서버와 리서치 도구를 같은 방식으로 연결해 호출합니다.' },
    { title: 'HALT 긴급 정지', body: '실계좌 거래를 한 번에 멈추는 원본의 안전장치를 그대로 씁니다.' },
    { title: '리서치 에이전트', body: '장기투자 테마 점검과 미국 종목 아이디어 조사에 읽기 전용으로 씁니다.' },
    { title: '기록·설정 폴더 규칙', body: '기록·설정 폴더(~/.vibe-trading) 구조와 테스트 체계를 이어받았습니다.' },
  ],
}
export const STACK = [
  { icon: 'Robot', name: 'Claude Code · Codex', role: 'AI 코딩 에이전트', body: '설치와 운영, 분석, 리포트 작성을 맡습니다. 프로젝트 스킬과 예약 작업으로 매일 정해진 일을 합니다.', href: 'ai-agents.md' },
  { icon: 'PlugsConnected', name: 'MCP', role: 'AI 도구 연결 표준', body: 'NH 공식 nhplug-mcp(MIT, 패치 적용)와 vibe-trading MCP로 시세·주문·리서치 도구를 연결합니다.', href: 'architecture.md' },
  { icon: 'Bank', name: 'NH 나무 PLUG OpenAPI', role: '증권사', body: '시세 조회, 잔고 확인, 모의·실계좌 주문을 맡습니다. 키움·토스 Open API는 순위와 시황을 보조로 가져올 때만 씁니다.', href: 'integrations.md' },
  { icon: 'Lightning', name: 'TypeSafe Jev · OpenAI', role: '판단 모델', body: 'Jev는 1분 차트와 뉴스를 보고 매매 후보와 신뢰도를 내고, OpenAI는 빠른 판단과 관찰을 맡습니다.', href: 'integrations.md' },
  { icon: 'Monitor', name: 'Python · Streamlit', role: '실행기와 대시보드', body: '1분마다 돌아가는 실행기와 로컬 대시보드(10개 탭)를 만듭니다. 자동 테스트 1,998개로 동작을 검증합니다.', href: 'dashboard.md' },
  { icon: 'Stack', name: 'SQLite · JSON', role: '기록과 설정', body: '서버 없이 내 컴퓨터의 파일에 저장합니다. 판단·체결·매매일지는 SQLite에, 관점과 설정은 JSON에 둡니다.', href: 'architecture.md' },
  { icon: 'ArrowsClockwise', name: 'macOS launchd', role: '자동 실행', body: '장중 실행기, 장전 자료 수집, 알림, 백업을 정해진 시각에 실행하고, 멈추면 다시 실행합니다.', href: 'automation.md' },
  { icon: 'BellRinging', name: 'GitHub Pages · 메신저', role: '리포트와 알림', body: '리포트는 HTML 페이지로 게시하고, 체결과 링크는 Discord·Telegram·Slack으로 보냅니다.', href: 'notifications.md' },
]

export const JEV_INPUTS = [
  { label: '1분 차트 이미지', detail: 'ETF와 관심종목의 최근 60분 캔들을 그림으로 읽습니다' },
  { label: '당일 요약', detail: '시가·고가·저가, 하루 범위 안 위치, VWAP, 30·60분 변화' },
  { label: '최근 90분 뉴스', detail: '오래된 기사는 빼고 지금 시장을 움직이는 헤드라인만' },
  { label: '시황과 수급', detail: '코스피·코스닥 국면, 테마와 대장주, 투자자·프로그램 순매수' },
  { label: '해외와 장전', detail: '나스닥·S&P500 선물, 금·유가·환율, 장전 브리프와 NXT 프리마켓' },
  { label: '최근 판단 결과와 매매일지', detail: '최근 판단 결과와 지난 3거래일 매매일지 요약을 참고합니다' },
]

export const JEV_USES = [
  { title: '매수 후보', body: '1분마다 매수 후보 한 종목이나 대기를 고릅니다.' },
  { title: '보유 판단', body: '종목별로 계속 보유할지 매도할지 판단합니다. 매도 판단은 신뢰도 기준에 따라 일부 매도로 이어질 수 있습니다.' },
  { title: '관심종목 선정', body: '30분마다 순위에 오른 종목의 재무·수급·뉴스를 살펴 관심종목 3개를 고릅니다.' },
  { title: '급락 종목 분석', body: '시장이 크게 하락한 날에는 후보 종목의 차트와 수급, 애널리스트 의견, 과거 사례를 함께 봅니다.' },
  { title: '같은 날 재매수 검토', body: '불리한 시장에서 당일에 사고판 종목을 다시 사기 전 Jev에 한 번 더 확인합니다.' },
  { title: '전날 보유분 재검토', body: '전 거래일부터 보유한 종목을 개장 직후 다시 살핍니다.' },
]

export const JEV_TRUST = [
  { title: '선택형으로 답합니다', body: '정해진 선택지에서 종목이나 대기를 고르고 신뢰도를 알려 줍니다. 실행기는 그 응답을 주문 규칙에 맞춰 확인합니다.' },
  { title: '근거를 함께 남깁니다', body: '결정 요인과 차트·뉴스·시황·수급의 방향 등을 묻는 질문 7개에 Jev가 답합니다. 답변은 판단 근거로 기록합니다.' },
  { title: '적중률을 매일 확인합니다', body: '신뢰도 구간별로 5·15·30분 뒤 결과를 모아, 신뢰도가 높을수록 판단이 더 자주 맞았는지 확인합니다.' },
  { title: '주문은 규칙이 정합니다', body: '신뢰도는 수익 확률이 아닙니다. 진입 조건과 주문 보호 조건을 통과해야 주문이 실행됩니다.' },
]

// Risk order, highest first. `masters` are MASTERS ids recommended in that regime.
export const REGIMES = [
  { name: '투매', rule: 'VIX 35 이상 · 낙폭 −15% 이하', masters: ['base', 'dalio', 'reservoir', 'marks', 'williams-bounce', 'lynch', 'buffett'] },
  { name: '약세', rule: '낙폭 −20% 이하 또는 200일선 아래 하락', masters: ['base', 'dalio', 'reservoir', 'marks', 'williams-trend', 'lynch', 'buffett'] },
  { name: '조정', rule: '낙폭 −10% 이하 또는 200일선 아래', masters: ['base', 'dalio', 'reservoir', 'lynch', 'buffett'] },
  { name: '회복', rule: '하락 국면을 벗어나는 중', masters: ['base', 'dalio', 'lynch', 'buffett'] },
  { name: '과열', rule: '200일선 +15% 이상 · VIX 14 이하', masters: ['base', 'dalio', 'marks', 'lynch', 'buffett'] },
  { name: '강세 안정', rule: '그 밖의 상승장', masters: ['base', 'dalio', 'lynch', 'buffett'] },
]

export const MASTERS = [
  { id: 'base', name: '기준 비중', idea: '흔들리지 않는 바탕', does: '기준 비중은 코어(미국 지수 ETF) 60%, 위성(테마 ETF) 25%, 저수지(하락 때 살 자금) 15%입니다.', on: true },
  { id: 'dalio', name: '레이 달리오', idea: '벗어나면 되돌린다', does: '매월 비중을 확인하고 목표에서 ±5%p 넘게 벗어나면 전체 비중을 원래 목표에 맞춥니다.', on: true },
  { id: 'lynch', name: '피터 린치', idea: '스토리가 살아 있는가', does: 'AI가 테마별 투자 근거가 유효한지 살펴 ‘유지·주의·훼손’으로 표시하고 테마 ETF의 비중을 조정합니다.', on: true },
  { id: 'buffett', name: '워런 버핏·필립 피셔', idea: '팔 이유가 없으면 들고 간다', does: '손절 규칙을 적용하지 않습니다. 한 달 매도 금액을 순자산의 10%로 제한해 매매를 줄입니다.', on: true },
  { id: 'marks', name: '하워드 막스', idea: '시장의 시계추를 거슬러', does: '시장이 과열되면 코어 ETF 일부를 팔아 저수지로 옮깁니다. 투매 국면에서는 단계별 매수 시점을 앞당깁니다.', on: false },
  { id: 'reservoir', name: '저수지 분할', idea: '떨어질 때를 기다린 돈', does: '나스닥100이 고점 대비 −20%·−30%에 닿을 때마다 저수지 자금으로 나눠 삽니다.', on: false },
  { id: 'williams-trend', name: '래리 윌리엄스 · 추세', idea: '추세가 꺾이면 줄인다', does: '테마 지수가 200일선 아래에 5일 머물면 해당 테마 ETF의 비중을 절반으로 줄입니다.', on: false },
  { id: 'williams-bounce', name: '래리 윌리엄스 · 반등', idea: '급락 뒤 짧은 반등', does: '나스닥100의 하루 등락률이 −3% 이하이고 VIX가 25 이상이면 저수지 자금 일부로 나스닥100 ETF를 잠시 보유합니다.', on: false },
]

export const LONGTERM_POINTS = [
  { title: '거장별 관점 리포트', body: '매일 아침 거장 7명의 원칙을 바탕으로 매수, 대기, 보유 유지, 비중 축소 의견을 정리합니다. 장기투자 엔진의 판단과 어떤 차이가 있는지도 보여 줍니다.' },
  { title: '시장 심리와 거시 지표', body: 'VIX·200일선·낙폭·하이일드 스프레드로 공포와 탐욕을 0~100으로 보여 주고, 금리차·실질금리·FOMC 일정을 함께 봅니다.' },
  { title: '여운봉 저수지 매매법', body: '처음에는 운용금 전부를 저수지에 둡니다. 나스닥100이 고점 대비 −20·−30·−40%에 닿으면 단계별로 나눠 사고, 나스닥100 ETF와 채권 혼합 ETF의 비중을 70:30으로 맞춥니다.' },
  { title: '전략 조합 비교와 주문 근거', body: '가상 1억 원 계좌와 전략 조합별 가상 장부의 결과를 비교합니다. 주문마다 판단 근거를 남깁니다.' },
]

// Split money by when it is needed. The years are an example; only the last row is what the long-term engine handles.
export const HORIZONS = [
  { when: '1~2년 안에 쓸 돈', where: '예금처럼 바로 꺼낼 수 있는 곳', omni: 'Omni에서는 다루지 않습니다. **투자할 돈에서 먼저 뺍니다.**', on: false },
  { when: '3~7년 뒤에 쓸 돈', where: '가격 변동이 작은 자산', omni: '장기투자 엔진이 맡는 돈이 아닙니다.', on: false },
  { when: '8년 넘게 둘 돈', where: '장기투자 자금', omni: '적립과 비중 조정을 계획하고 **왜 사고팔았는지 기록합니다.**', on: true },
]
// S&P 500 total return (USD), month ends 1988-01 to 2026-10, overlapping windows; own calculation from Yahoo ^SP500TR.
export const HOLDING_LOSS = [
  { label: '1년 보유', value: 16.3 }, { label: '3년', value: 13.7 }, { label: '5년', value: 12.1 },
  { label: '10년', value: 6.9 }, { label: '15년', value: 0 }, { label: '20년', value: 0 },
]
// 300M KRW, 15M withdrawn at the start of each year for 20 years: -15, -10, -10% in the first or the last three years, 7% otherwise.
export const SEQUENCE = [
  { label: '하락이 먼저 온 경우', value: 4380, text: '4,380만 원' },
  { label: '하락이 나중에 온 경우', value: 27570, text: '2억 7,570만 원' },
]

// Using the same way of working outside trading (docs/ai-search.md 창업 준비 점검).
export const EXTEND_STEPS = [
  { title: '기준 적기', body: '설정과 스킬에 내 기준을 적습니다.' },
  { title: '근거 찾기', body: 'AI 검색으로 자료와 출처를 찾습니다.' },
  { title: '승인하기', body: '제안은 사람이 승인한 뒤 적용합니다.' },
  { title: '기록하기', body: '바꾼 값과 이유를 남깁니다.' },
  { title: '복기하기', body: '결과를 보고 기준을 다시 고칩니다.' },
]
export const EXTEND_MOVE = [
  { title: '가져다 쓰는 것', items: ['일하는 순서와 승인 절차', '질문 묶음, 점검표와 검사 도구', '변경 이력과 재발 확인'] },
  { title: '가져다 쓰지 않는 것', items: ['계좌, 체결, 시세 데이터', '종목 판단과 매매 규칙'] },
]
export const EXTEND_NEXT = [
  { title: '부모님 자금을 챙기는 가족', body: '각자 설치하고 자기 계좌와 기준에 맞춰 씁니다.' },
  { title: '금융교육과 소비자 상담', body: '기관의 답변 기준과 승인이 필요합니다.' },
  { title: '구매, 재무, IT 운영', body: '업무 규칙과 시스템 연결을 새로 만들어야 합니다.' },
]
export const EXTEND_CHECK = `$ startup_template.py --check 점검표.md
- 출처 링크가 없습니다: 하루 30그릇이면 남는다
- 비상금 금액을 창업 자금과 따로 적으세요.
- 멈출 기준에 날짜와 숫자를 함께 적으세요.`

export const LONGTERM_FLOW = [
  { title: '적립 계획 세우기', body: '장기투자 자금을 언제 얼마나 넣을지 정합니다.' },
  { title: '현재 비중과 목표 비교', body: '시장 국면과 선택한 전략을 바탕으로 비중 차이를 살핍니다.' },
  { title: '매수·대기·비중 조정', body: '적립식은 새로 넣을 자금의 매수 계획을 세우고, 계좌 전략은 기존 자산의 비중 조정을 계획합니다.' },
  { title: '가상 장부에서 돌아보기', body: '전략별 비용·낙폭·비중 변화를 비교합니다.' },
]

export const DCA_AXES = [
  { axis: '매수일', master: '매 거래일 시장 상황을 확인해 매수일을 고릅니다. 정한 주기마다 최대 한 번 삽니다.', me: '정한 날 또는 하락일 우선' },
  { axis: '금액·수량', master: '기본 매수 금액에 시장 국면별로 0.5~1.5배를 적용합니다. 못 사고 남은 몫도 조금씩 더합니다.', me: '정한 금액 또는 종목별 수량(N주)' },
  { axis: '매수 종목', master: '계좌 전략이 정한 ETF 비중대로', me: '고른 ETF만' },
  { axis: '매수 시각', master: '09:40부터 전일 종가 이하인 첫 시점에 삽니다. 그 시점이 없으면 14:20에 확인합니다.', me: '정한 시각' },
]

export const DCA_PROFILES = ['존 보글·워런 버핏 · 시황을 보지 않고 꾸준히', '레이 달리오 · 균형 유지, 과열일 때만 조금 덜', '하워드 막스 · 비쌀 때 덜, 쌀 때 더', '여운봉 · 하락할수록 조금 더', '피터 린치 · 테마의 투자 근거 반영', '래리 윌리엄스 · 급락일 우선(비권장)', '시황 자동 · 시장 국면에 맞는 거장 원칙(비교용 가상 장부)']

export const DAILY = [
  { phase: '장전', time: '08:20', title: '장전 사전 점검', body: '서비스·계좌·미확인 주문·설정을 점검하고, 문제가 있으면 바로 알립니다.' },
  { phase: '장전', time: '08:31', title: '장전 상세 분석', body: '해외 시장, 야간선물, NXT 프리마켓, 공시로 오늘 시장을 정리합니다.' },
  { phase: '장전', time: '08:50', title: '장기투자 전략 리포트', body: '시장 국면과 거장 7명의 원칙을 바탕으로 한 의견, 오늘의 주문 계획을 정리합니다.' },
  { phase: '장중', time: '09:00', title: '개장 직후 주문 확인', body: '전 거래일부터 보유한 종목을 다시 살핍니다. 신규 매수는 매수 조건과 주문 보호 조건을 통과해야 진행합니다.' },
  { phase: '장중', time: '매시 20분', title: '장중 감사', body: '판단 근거와 주문 차단 사유, 체결 내역을 점검하고 반복된 문제를 바로 알립니다.' },
  { phase: '마감', time: '15:40', title: '마감 요약', body: '계좌별 손익과 잔고를 알림 채널로 보냅니다.' },
  { phase: '마감', time: '16:15', title: '장 마감 개선안', body: '그날 거래와 신호 적중률로 개선안을 제안합니다. 적용하려면 사용자가 승인해야 합니다.' },
  { phase: '마감', time: '16:40', title: '매매일지', body: '거래를 돌아보고 시장 흐름과 수급, 핵심 이슈를 정해진 양식으로 기록합니다.' },
  { phase: '마감', time: '16:45', title: '장기투자 복기·감사', body: '아침 계획과 실제 결과, 거장별 관점을 비교합니다.' },
  { phase: '주간·월간', time: '금요일 · 월초', title: '주간·월간 감사', body: '낙폭과대·장기투자 주간 감사와 월·분기·반기·연 매매일지를 씁니다.' },
]

export const INTEGRATIONS = [
  { group: '증권', items: [
    { name: 'NH 나무 PLUG', role: '시세·잔고·주문 (필수)', anchor: 'nh-나무-plug' },
    { name: '키움 REST', role: '순위 선별·대체 시황 자료', anchor: '키움-rest' },
    { name: '토스증권 Open API', role: '신규상장·NXT 프리마켓', anchor: '토스증권-open-api' },
  ] },
  { group: 'AI', items: [
    { name: 'TypeSafe Jev', role: '차트·뉴스 판단', anchor: 'typesafe-jev' },
    { name: 'OpenAI', role: '빠른 판단·관찰', anchor: 'openai' },
    { name: 'Claude Code · Codex', role: '채팅 운영·리포트 작성', doc: 'ai-agents.md' },
  ] },
  { group: '데이터', items: [
    { name: 'KRX 정보데이터', role: '일별 시세', anchor: 'krx-정보데이터' },
    { name: 'OpenDART', role: '공시·재무', anchor: 'opendart' },
    { name: 'FRED', role: '금리·거시 지표', anchor: 'fred' },
    { name: '네이버 · Yahoo · 뉴스 RSS', role: '수급·해외 시세·뉴스 제목', anchor: '키-없이-쓰는-출처' },
  ] },
  { group: '알림·게시', items: [
    { name: 'Discord', role: '알림·리포트 링크', anchor: 'discord' },
    { name: 'Telegram', role: '알림·리포트 링크', anchor: 'telegram' },
    { name: 'Slack', role: '알림·리포트 링크', anchor: 'slack' },
    { name: 'GitHub Pages', role: 'HTML 리포트 게시', anchor: '리포트-게시-github-pages' },
  ] },
]

export const HIGHLIGHTS = [
  { icon: 'Scales', title: 'AI가 판단하고 규칙이 주문을 정합니다', body: 'Jev와 OpenAI가 매매 의견과 신뢰도를 제시합니다. 주문을 보내려면 승인된 매수·매도 규칙과 주문 보호 조건을 모두 충족해야 합니다.', proof: '신뢰도는 수익 확률이 아닙니다' },
  { icon: 'Stack', title: '장중 매매와 장기투자를 따로 운용합니다', body: '장중 매매는 국내 ETF와 주식을 1분마다 살핍니다. 장기투자는 국내 상장 미국 지수·테마 ETF를 꾸준히 모읍니다. 두 기능은 따로 켜고 끌 수 있습니다.', proof: '엔진별 기록과 실행 설정' },
  { icon: 'ShieldCheck', title: '모의·가상으로 먼저 연습합니다', body: '먼저 주문 계획을 보거나 가상 체결로 결과를 확인합니다. 실제 주문에는 계좌별 승인과 한도가 적용됩니다.', proof: '실주문 확인과 계좌별 주문 보호' },
  { icon: 'ArrowsClockwise', title: '기록하고 매일 돌아봅니다', body: '판단 근거, 주문, 체결을 기록합니다. 장 마감 뒤 매매일지와 개선안을 작성하고 신호 적중률을 확인합니다. 개선안은 사용자가 승인해야 반영됩니다.', proof: '모든 설정 변경을 기록' },
  { icon: 'ChatsCircle', title: 'AI와 채팅으로 운영합니다', body: 'Claude Code나 Codex에 채팅으로 분석, 운영 점검, 리포트 작성을 요청할 수 있습니다. 프로젝트 스킬과 예약 작업은 정해진 절차를 따릅니다. 셸을 쓰는 다른 AI 에이전트도 omni CLI로 연결할 수 있습니다.', proof: '스킬 10개 · 예약 작업 15개 · omni CLI' },
  { icon: 'LockKey', title: '내 컴퓨터에서 내 키로 실행합니다', body: '프로그램은 내 Mac에서 실행합니다. 키와 계좌번호는 .env 파일에만 둡니다. 화면, 리포트, 알림은 계좌번호를 가립니다.', proof: '저장소에 키와 거래 기록 없음' },
]

export const CHAT = [
  { who: 'me', text: '오늘 장전 상세 분석 써 줘. 야간선물, NXT 프리마켓, 미국 시장을 근거로.' },
  { who: 'ai', text: '장전 자료를 읽고 템플릿 검사까지 마쳤습니다. 리포트 링크를 알림 채널로 보냈어요.' },
  { who: 'me', text: '10시 이후 진입이 막힌 이유를 종목별로 정리해 줘.' },
  { who: 'ai', text: '3종목은 인버스 매수 조건, 2종목은 추격 매수 금지 기준에 막혔습니다. 같은 이유로 3분 넘게 막힌 종목은 없습니다.' },
  { who: 'me', text: '신규 매수 마감을 14:00으로 바꾸면 뭐가 달라져?' },
  { who: 'ai', text: '폭락일 반등 매매 시간은 그대로이고, 일반 신규 매수만 30분 일찍 멈춥니다. 승인하시면 저장하고 변경 이력을 남길게요.' },
]

export const AI_LOOP = [
  { title: '묻고 맡기기', body: '상태 조회, 분석, 리포트를 채팅으로 요청합니다.' },
  { title: '프로젝트 스킬로 처리', body: '프로젝트 스킬이 정한 절차에 따라 장전 분석, 장중 감사, 매매일지를 작성합니다.' },
  { title: '사용자가 승인', body: '설정을 바꾸거나 실주문을 켜려면 사용자 승인이 필요합니다.' },
  { title: '실행기가 주문', body: '주문 보호 조건을 충족하면 실행기가 주문을 보냅니다.' },
]

export const TAGLINE = ['AI는 의견을 내고,', '규칙이 주문을 정하고,', '안전장치가 계좌를 지킵니다.']

export const FEATURES = [
  { icon: 'ChatsCircle', title: 'AI 검색', body: '기사 원문을 찾고, 투자 질문에 답하고, 실계좌 잔고와 보유 종목을 조회합니다.', href: doc('ai-search.md') },
  { icon: 'TrendDown', title: '폭락일 반등 매매', body: '지수가 크게 빠진 날 14:30부터 시장과 같이 빠진 종목을 나눠 사고, 오르면 나눠 팝니다.', href: doc('oversold.md') },
  { icon: 'Bank', title: '실계좌 주문 보호', body: '실주문 스위치, 하루 손실 차단 기준, 1주 가격 상한을 계좌마다 적용합니다. 미체결이나 미확인 주문이 있으면 새 주문을 막습니다.', href: doc('live-accounts.md') },
  { icon: 'Plant', title: '적립식과 여운봉 매매법', body: '새 적립금은 적립식 매수에 쓰고, 계좌에 있는 운용금은 별도의 계좌 전략으로 관리합니다.', href: doc('longterm-dca.md') },
  { icon: 'Notebook', title: '매매일지와 리포트', body: '체결 내역, 투자자별 매매 동향, 중요도 별점을 매긴 뉴스, 종목 차트를 모아 일·월·분기·연 단위 일지와 HTML 리포트를 만듭니다.', href: doc('journal.md') },
  { icon: 'BellRinging', title: 'Discord·Telegram·Slack 알림', body: '체결과 손익은 바로 알립니다. 리포트는 게시한 뒤 링크를 보내며, 같은 알림은 한 번만 보냅니다.', href: doc('integrations.md') },
  { icon: 'TerminalWindow', title: 'omni CLI', body: '셸을 쓰는 AI 에이전트라면 같은 명령으로 기록을 조회하고 설정 변경을 계획합니다. 실계좌에 영향을 주는 변경은 사용자의 승인 코드가 있어야 적용됩니다.', href: doc('omni-cli.md') },
  { icon: 'ListChecks', title: '장전 점검과 장중 감사', body: '개장 전에 자동 실행 서비스와 계좌를 점검하고, 장중에는 매시간 판단과 주문이 막힌 이유를 살펴 문제 기록을 남깁니다.', href: doc('automation.md') },
]

// A beginner's path through the AI search. Every question is one of the dashboard's own suggestions (streamlit_namuh/liner_topics.py).
export const LEARN_STEPS = [
  { title: '용어부터 물어보기', body: 'ETF, 분산 투자, 최대 낙폭처럼 **처음 보는 말의 쉬운 뜻과 예**를 묻습니다.', question: 'ETF와 일반 펀드는 어떻게 다른가요?' },
  { title: '화면의 숫자 이해하기', body: '차트나 시장 화면에서 본 지표가 **무엇을 알려 주는지** 확인합니다.', question: 'VWAP과 거래량 배율은 어떤 정보를 주나요?' },
  { title: '투자 원칙 배우기', body: '투자 거장이 **왜 그렇게 생각했는지** 묻고, 답변에 달린 출처를 읽어 봅니다.', question: '존 보글은 왜 저비용 인덱스 펀드를 강조했나요?' },
  { title: '내 기록으로 복기하기', body: '배운 내용을 **매매일지의 진입 근거와 청산 결과**에 비춰 봅니다.', question: '매매일지에서 진입 근거와 청산 결과를 어떻게 복기하나요?' },
]

// Asking from the screen you are looking at (docs/ai-search.md 작은 창으로 묻기 · 화면에서 바로 묻기).
export const ASK_SCENES = [
  {
    id: 'drag', label: '1. 드래그', screen: 'ai-ask-1', height: 900,
    title: '모르는 글자를 드래그합니다', question: '추세형이 무슨 뜻이지?',
    body: '드래그한 글자 옆에 **AI에게 묻기** 버튼이 뜹니다. 카드의 숫자를 드래그하면 그 카드 이름으로 묻습니다.',
    detail: '데이터 표와 도식 안의 글자는 드래그할 수 없습니다.',
    note: '실제 대시보드 화면 구성에 예시 값을 넣었습니다.',
    alt: '전략 구조 화면에서 추세형이라는 글자를 드래그하자 AI에게 묻기 버튼이 나타난 화면',
  },
  {
    id: 'draft', label: '2. 뜻 보기', screen: 'ai-ask-2', height: 900,
    title: '질문을 채우고 뜻을 먼저 보여 줍니다', question: '‘추세형’의 뜻을 쉽게 알려 주세요. (전략 구조 화면)',
    body: '작은 채팅창이 열리고 질문이 입력칸에 채워집니다. 서비스에 적어 둔 설명이 있으면 **요청을 보내지 않고 먼저** 보여 줍니다.',
    detail: '설명으로 충분하면 여기서 끝입니다. 더 궁금하면 질문을 고쳐 보내세요.',
    note: '실제 대시보드 화면 구성에 예시 값을 넣었습니다.',
    alt: '작은 채팅창에 추세형의 서비스 설명이 보이고 질문이 입력칸에 채워진 화면',
  },
  {
    id: 'answer', label: '3. 답변', screen: 'ai-ask-3', height: 900,
    title: '출처와 함께 답변을 받습니다', question: '‘추세형’의 뜻을 쉽게 알려 주세요. (전략 구조 화면)',
    body: '서비스 가이드와 웹 출처를 바탕으로 답합니다. **답변을 기다리는 동안 보던 화면은 그대로**이고, 이어서 더 물을 수 있습니다.',
    detail: '이 답변에는 웹 출처 9개와 서비스 가이드 2개가 함께 표시됐습니다.',
    note: '2026년 11월 24일에 받은 실제 답변입니다. 답변은 물을 때마다 달라질 수 있습니다.',
    alt: '작은 채팅창에서 추세형을 설명하는 답변을 보는 화면. 뒤에는 보던 전략 구조 화면이 그대로 있음',
  },
]
export const ASK_WAYS = [
  { title: '글자 드래그', body: '화면의 글자를 선택하면 옆에 버튼이 뜹니다.' },
  { title: '카드 이름', body: '종합 현황, 트레이딩 현황, 장기투자의 상태 카드 아래에서 이름을 누릅니다.' },
  { title: '화면 안내', body: '각 탭의 화면 안내에서 화면 전체나 용어를 골라 묻습니다.' },
  { title: '화면별 추천 질문', body: '작은 창을 열면 지금 화면에 맞는 질문 3개가 보입니다.' },
]
export const ASK_RULES = [
  { title: '보내기를 눌러야 전송됩니다', body: '어느 방법으로 묻든 질문을 **채우기만** 합니다. 보내기를 누르기 전에는 전송되지 않고 요금도 들지 않습니다.' },
  { title: '드래그한 금액은 가립니다', body: '드래그한 글의 **금액, 수량, 비율과 6자리 이상 숫자**는 ○로 바꿔 채웁니다. 직접 적은 내용은 그대로 전송되니 확인하고 보내세요.' },
  { title: '작은 창과 넓은 창이 이어집니다', body: '**크게**를 누르면 넓은 AI 검색 창으로 옮겨 갑니다. 대화, 검색 결과, 진행 중인 요청을 두 창이 함께 씁니다.' },
]
// What each step of the AI search does, and who does it.
export const SEARCH_STEPS = [
  { who: 'Liner Search', liner: true, title: '원문 찾기', body: '기사와 공시의 제목, 발행일, **원문 링크**를 찾습니다.' },
  { who: 'Liner Search Agent', liner: true, title: '뜻과 영향 묻기', body: '**출처가 달린 답변**을 받고 궁금한 점을 이어서 묻습니다.' },
  { who: '사용자', liner: false, title: '출처 열어 보기', body: '중요한 내용은 **원문에서 직접** 확인합니다.' },
  { who: '내 컴퓨터', liner: false, title: '내 기록과 비교하기', body: '내부 데이터를 선택해 물으면 **Liner로 보내지 않고** 대시보드에서 답합니다.' },
]
export const AI_SEARCH_EXAMPLES = [
  {
    id: 'offer', label: '권유 확인', screen: 'ai-search-offer',
    question: '원금을 보장하고 월 5% 수익을 준다는 투자 권유를 받았습니다. 보내기 전에 확인할 점과 공식 확인처를 알려 주세요.',
    title: '돈을 보내기 전에 근거부터',
    body: '들은 말을 그대로 입력하면 확인할 점과 공식 확인처를 출처와 함께 보여 줍니다. 추천 질문의 노후 자금·투자 권유 분류에서 바로 고를 수도 있습니다.',
    detail: '이름, 연락처, 계좌번호는 넣지 마세요. AI 검색은 사기인지 판정하지 않습니다.',
    note: '2026년 11월 24일에 받은 실제 답변입니다. 뉴스 목록은 예시이고, 답변은 물을 때마다 달라질 수 있습니다.',
    alt: '원금 보장과 월 5% 수익을 약속하는 투자 권유를 두고 확인할 점과 공식 확인처를 안내하는 AI 채팅 화면',
  },
  {
    id: 'research', label: '웹 검색·분석', screen: 'ai-search-research',
    question: '반도체 실적 뉴스가 코스피에 미치는 영향은?',
    title: '뉴스의 영향을 출처와 함께',
    body: '기사와 공시 원문을 찾고, 선택한 이슈의 시장 영향이나 투자 용어를 물어보세요. 답변의 출처에서 어떤 자료를 참고했는지 확인할 수 있습니다.',
    detail: '사용법을 물으면 관련 프로젝트 가이드를 보여 줍니다.',
    alt: '투자 AI 채팅에서 반도체 실적 뉴스의 시장 영향과 참고 출처를 확인하는 예시',
  },
  {
    id: 'account', label: '실계좌 조회', screen: 'ai-search',
    question: '실계좌 1의 현재 보유 종목과 잔고는?',
    title: '내 잔고와 조회 시각을 한 화면에',
    body: '내부 데이터에서 등록한 계좌를 물으면 NH 나무 PLUG의 읽기 전용 조회로 예수금, 총자산과 종목별 보유 수량을 확인할 수 있습니다.',
    detail: '조회 완료 시각은 한국 시간으로 표시됩니다. 결과는 대시보드 안에서 확인합니다.',
    alt: '내부 데이터 모드에서 예시 계좌의 조회 완료 시각, 잔고와 보유 수량을 확인하는 화면',
  },
]

export const SAFETY_STAGES = [
  { step: '1', title: '관찰', body: '실계좌는 계획만 기록하고 주문하지 않습니다.' },
  { step: '2', title: '가상 실행', body: '실시간 호가로 매수·매도 체결을 가정해 기록하고 전략의 결과를 확인합니다.' },
  { step: '3', title: '보호된 실주문', body: '실행기에 실주문 옵션을 지정하고 계좌별 자동매매·실주문 스위치를 켜야 합니다. 실행기는 긴급 중단 해제 여부와 주문별 보호 조건을 확인합니다.' },
]

export const LIMITS = [
  ['모의계좌', '1회 매수 금액, 하루 매수 금액, 보유 금액, 하루 새 종목 수, 하루 손실 한도를 대시보드에서 허용 범위 안의 값으로 정합니다.'],
  ['실계좌', '계좌별 하루 손실 차단 기준 상한 2만 원, 1주 가격 상한, 하루 새 종목 최대 5개(장기투자 첫 투입일 최대 6개), 미체결·미확인 주문이 있으면 새 주문을 막는 규칙을 적용합니다.'],
  ['공통', '계좌와 거래소 장 시간, 주문 기록과 잔고를 확인합니다. 공유 모의 STOP은 미확인 주문을 대조한 뒤 실행기를 종료하고, 실계좌 HALT는 실주문 경로를 차단합니다.'],
]

export const SCREENS = [
  { id: 'overview', label: '종합 현황', caption: '모든 계좌의 오늘 손익, 잔고, 실행기 상태를 한 화면에서 봅니다.' },
  { id: 'strategy-flow', label: '전략 구조', caption: '매수 판단부터 주문 한도 검사까지의 과정에 오늘 설정을 반영한 흐름도입니다.' },
  { id: 'trading', label: '트레이딩 현황', caption: '계좌를 골라 보유 종목과 체결 내역을 보고 수동 매수·매도를 요청합니다.' },
  { id: 'journal', label: '매매일지', caption: '계좌별 성과, 시장과 투자자별 매매 동향, 핵심 뉴스 별점을 카드로 모읍니다.' },
  { id: 'compare', label: '성과 비교', caption: 'Jev 트레이딩과 장기투자의 손익, 수익률, 낙폭을 같은 기간에 맞춰 비교합니다.' },
  { id: 'longterm', label: '장기투자', caption: '시장 국면과 오늘의 판단을 보고, 가상 계좌와 비교용 가상 장부의 자산을 확인합니다.' },
  { id: 'longterm-chart', label: '장기 차트', caption: '이동평균선과 사고팔 가격대, 적립식·여운봉 매수 단계를 그립니다.' },
  { id: 'accounts', label: '계좌·자동매매', caption: '켤 엔진과 계좌별 운용 방식, 주문 한도를 정합니다.' },
  { id: 'live', label: '실계좌', caption: '실계좌별 실행기 상태와 보호 한도, 장기투자 계좌의 오늘 계획을 봅니다.' },
  { id: 'api', label: '운영', caption: '운영 → API 연동에서 외부 API 호출 수, 실패 수, 응답 지연을 10분 단위로 확인합니다.' },
]

export const PROMPT = `이 저장소는 Omni AI Trading(NH 나무 PLUG 모의·실계좌 자동매매)이다.
docs/getting-started.md와 docs/ai-agents.md를 읽고 그 순서대로 설치를 도와줘.
- 답변은 한국어, 시각은 Asia/Seoul로 쓴다.
- .env 값과 계좌번호는 출력하지 말고, 키는 내가 직접 넣게 키 이름만 안내해.
- Python 가상환경, NH 공식 MCP 설치·패치·빌드, agent.json 등록, vibe_market_brief.py 연결 확인까지 해 줘.
- docs/ai-agents.md의 템플릿으로 AGENTS.md와 CLAUDE.md를 만들고, 프로젝트 스킬과 vibe-trading MCP를 연결하고,
  .claude/settings.local.json에 읽기 전용 권한 4개를 넣어 줘.
- launchd 서비스는 plist를 만들어 보여 주기만 하고, 등록은 대시보드·읽기 전용 서비스부터 내가 승인한 것만 해.
- shared-etf-paper(모의 주문), 실계좌 서비스, 실주문 스위치, 장기 엔진 --execute는 켜지 마.
- 끝나면 있는 것과 없는 것(키 이름, 서비스, 스킬, MCP, 실패한 점검)을 표로 보고해.`

export const CLI_INSTALL = `.venv/bin/python -m pip install --no-deps -e agent-harness
.venv/bin/omni status`

export const CLI_PROMPT = `이 저장소의 docs/omni-cli.md를 읽고 omni CLI를 설치해서 쓸 수 있게 해 줘.
- 답변은 한국어, 시각은 Asia/Seoul로 쓴다. 계좌번호와 키는 찾거나 출력하지 마.
- .venv에 agent-harness를 설치하고(.venv/bin/python -m pip install --no-deps -e agent-harness) omni status --json이 답하는지 확인해.
- 프로젝트 스킬 omni-cli를 쓸 수 있게 해. Codex면 .agents/skills에 링크해.
- 권한은 docs/ai-agents.md의 예시대로 조회만 자동 허용하고, 값을 바꾸는 명령은 매번 묻게 해. 설정 파일은 보여 준 뒤 내가 승인하면 저장해.
- 지금은 조회 명령만 실행해. plan approve, plan apply, live pause, 유료 질문(ai ask·search)은 하지 마.
- 끝나면 설치 위치, 상태 조회 결과, 바로 쓸 명령 5개를 표로 보고해.`

// omni CLI (#cli). Commands are real (test_docs parses them); the answers are made-up examples.
export const CLI_SCENES = [
  { id: 'blocks', kind: '묻기', ask: '오늘 왜 안 샀나요?', lines: ['$ omni jev blocks', 'blocked_cycles: 96', 'reasons:', '  - label Jev 대기 의견 · cycles 61', '  - label 최근 분봉 상승 아님 · cycles 35', '자료: events.jsonl 2분 전'],
    reply: 'Jev가 대기를 고른 회차가 61번으로 가장 많았고, 최근 분봉이 오르지 않은 경우가 35번이었습니다.' },
  { id: 'alerts', kind: '묻기', ask: '지금 확인할 일이 있나요?', lines: ['$ omni status alerts', 'alerts: -', '자료: events.jsonl 2분 전'],
    reply: '주의 항목은 없습니다. 2분 전 기록을 기준으로 확인했습니다.' },
  { id: 'local', kind: '묻기', ask: '오늘 모의 손익은 얼마인가요?', lines: ['$ omni ai local "오늘 모의 손익"', 'answer:', '  공유 모의계좌 · 2026-11-24', '  실현손익 +12,300원 · 매도 3건'],
    reply: '오늘 모의계좌의 실현손익은 +12,300원입니다. 매도는 3건이었습니다.' },
  { id: 'fills', kind: '묻기', ask: '실계좌에서 무엇이 체결됐나요?', lines: ['$ omni live fills live-1', 'fills:', '  - symbol 069500 · side BUY · qty 2 · price 41,250', '자료: live-fills.sqlite3 1분 전'],
    reply: '실계좌 1에서 KODEX 200 2주를 41,250원에 샀습니다.' },
  { id: 'lt', kind: '묻기', ask: '장기투자 엔진은 오늘 어떻게 판단했나요?', lines: ['$ omni lt today', 'mode: manual', 'regime:', '  regime: correction', '  since: 2026-11-16'],
    reply: '시장 국면은 조정이며 11월 16일부터 이어지고 있습니다.' },
  { id: 'change', kind: '바꾸기', ask: '진입 신뢰도를 0.6으로 바꿔 주세요', lines: ['$ omni settings plan strategy.entry_confidence=0.6 --reason "신중하게"', 'plan: 1a2b3c4d · status: open · level: H', '- setting strategy.entry_confidence · before 0.5 · after 0.6', 'affects: live-1'],
    reply: '아직 바꾸지 않았습니다. 0.5에서 0.6으로 바꾸면 실계좌 1에도 영향이 있어 승인이 필요합니다.' },
  { id: 'approve', kind: '승인하기', ask: '코드를 받았어요. 적용해 주세요', lines: ['$ omni plan approve 1a2b3c4d', 'sent: 1 · expires_minutes: 10', '$ omni plan apply 1a2b3c4d --code ********', 'status: applied · takes_effect: 다음 회차'],
    reply: '적용했습니다. 다음 회차부터 반영되며, 변경 이력에서 되돌릴 수 있습니다.' },
]
export const CLI_FLOW = [
  { icon: 'ClipboardText', title: '계획 만들기', body: '설정 변경이나 매매 요청을 계획으로 만듭니다. 이때는 아무것도 바뀌지 않습니다.' },
  { icon: 'MagnifyingGlassPlus', title: '영향 확인하기', body: '이전 값과 새 값, 실계좌에 주는 영향, 반영 시점을 먼저 보여 줍니다.' },
  { icon: 'LockKey', title: '사용자가 승인하기', body: '알림 채널로만 보내는 **일회용 승인 코드**를 사용자가 알려 줘야 합니다.', tag: '실계좌 영향 · 매매 요청' },
  { icon: 'ArrowsClockwise', title: '적용하고 기록하기', body: '누가 왜 바꿨는지 이력에 남깁니다. 설정은 이력 번호로 되돌릴 수 있습니다.' },
]
// Which agents connect and what stays the same (docs/omni-cli.md 1~3, docs/ai-agents.md omni 명령).
export const CLI_FROM = [
  { icon: 'Robot', name: 'Claude Code · Codex', role: '설치와 권한 예시 제공' },
  { icon: 'ChatsCircle', name: '셸을 쓰는 다른 AI 에이전트', role: '가이드를 읽고 같은 명령 사용' },
  { icon: 'ArrowsClockwise', name: '내 스크립트·자동화', role: 'JSON 답을 받아 다음 작업에 활용' },
]
export const CLI_HUB = ['같은 명령', '같은 JSON 형식', '같은 승인 절차']
export const CLI_TO = [
  { icon: 'Notebook', name: '기록과 리포트', role: '잔고, 매매, 장기투자, 매매일지' },
  { icon: 'ListChecks', name: '설정 변경 계획', role: '바꾸기 전에 영향 확인' },
  { icon: 'ShieldCheck', name: '승인과 주문 보호', role: '사용자 승인 뒤 실행기가 처리' },
]
export const CLI_OPEN = [
  { icon: 'PlugsConnected', title: '연동을 다시 만들지 않습니다', body: '셸 명령을 실행할 수 있는 에이전트라면 전용 플러그인 없이 연결됩니다. **AI 도구를 바꿔도 배운 명령을 그대로** 씁니다.' },
  { icon: 'ClipboardText', title: 'AI가 읽기 쉬운 답을 줍니다', body: '모든 명령에 --json을 붙일 수 있습니다. 답과 함께 **자료의 시각과 주의 사항**을 정해진 형식으로 알려 줍니다.' },
  { icon: 'MagnifyingGlassPlus', title: '명령을 스스로 찾습니다', body: '에이전트가 omni guide로 **필요한 명령을 검색**하고, 종료 코드로 성공과 거절, 승인 대기를 구분합니다.' },
  { icon: 'ShieldCheck', title: '안전 규칙은 그대로입니다', body: '어느 에이전트가 omni로 요청하든 **같은 승인 절차와 주문 보호 조건**을 거칩니다. 변경 이력에는 에이전트 이름을 남길 수 있습니다.' },
]
export const CLI_START = [
  { id: 'ai', label: 'AI에게 맡기기', copy: '프롬프트', code: CLI_PROMPT, steps: [
    { title: '프롬프트 복사하기', body: '프롬프트 복사 버튼을 누릅니다.' },
    { title: 'AI 에이전트에 붙여 넣기', body: '저장소를 연 Claude Code나 Codex에 붙여 넣습니다.' },
    { title: '결과 확인하기', body: '설치 위치와 상태 조회 결과, 바로 쓸 명령 5개를 표로 받습니다.' },
  ] },
  { id: 'self', label: '직접 설치하기', copy: '명령어', code: CLI_INSTALL, steps: [
    { title: '.venv 준비하기', body: '아래 시작 안내에 따라 Python 가상환경을 만듭니다.' },
    { title: '명령어 실행하기', body: '저장소 폴더에서 명령어를 차례로 실행합니다.' },
    { title: '상태 확인하기', body: '엔진과 서비스 상태가 나오면 준비가 끝났습니다.' },
  ] },
]

export const STEPS = [
  { title: '저장소 받기', body: 'Python 3.11, Node.js 22 이상, NH 나무 PLUG 앱 키(모의투자 계좌 포함), 유료 요금제의 Claude Code 또는 Codex를 준비합니다.', code: 'git clone https://github.com/roadkwonai/omni-ai-trading.git' },
  { title: '키와 계좌번호 입력', body: '.env.example을 .env로 복사하고 권한을 600으로 둡니다. 키와 계좌번호는 이 파일에만 둡니다.', code: 'cp .env.example .env && chmod 600 .env' },
  { title: 'AI에게 맡기기', body: 'Claude Code나 Codex에 아래 프롬프트를 주면 설치와 연결 확인을 진행하고, 주문 서비스는 승인을 기다립니다.', code: null },
]

export const FAQ = [
  { q: 'AI가 판단하면 손실이 안 나나요?', a: 'AI 판단에도 오류가 있고 원금 손실이 생길 수 있습니다. 실행기는 계좌와 전략에 따른 주문 보호 조건을 적용합니다. 시세 급변이나 미체결 때문에 실제 손실이 계좌의 손실 차단 기준을 넘을 수 있습니다.' },
  { q: '트레이딩과 장기투자 중 무엇부터 해야 하나요?', a: '처음에는 모의계좌와 가상 계좌에서 둘 다 켜 두고, 성과 비교 탭과 매매일지로 몇 주간 비교한 뒤 정하는 것을 권합니다. ‘운영 엔진’에서 ‘둘 다 운영’, ‘Jev만’, ‘장기투자만’ 중 언제든 다시 고를 수 있습니다.' },
  { q: '수익이 나나요?', a: '보장하지 않습니다. AI 판단, 모의 성과, 가상 체결, 과거 재현 결과는 미래 수익을 보장하지 않고, 금융투자상품은 원금 손실(0~100%)이 생길 수 있습니다. ' + DISCLAIMER_SHORT },
  { q: 'AI 도구 비용이 드나요?', a: AI_PAID + ' 요금과 이용 조건은 각 서비스의 최신 안내를 확인하세요.' },
  { q: '설치하면 실계좌로 바로 주문하나요?', a: '기본은 모의계좌입니다. 실계좌로 주문하려면 실행기에 실주문 옵션을 지정하고 계좌별 자동매매·실주문 스위치를 켜야 합니다. 실행기는 긴급 중단 해제 여부와 주문별 보호 조건을 확인합니다. 실주문 스위치를 켤 때는 확인 문구를 입력합니다.' },
  { q: '어떤 증권사를 쓰나요?', a: 'NH투자증권 나무 PLUG OpenAPI와 공식 MCP를 씁니다. 키움과 토스 Open API는 종목 순위와 시장 정보를 보조로 받아 오는 데만 씁니다. NH투자증권과 제휴 관계가 없는 비공식 프로젝트입니다.' },
  { q: 'CLI로 주문할 수 있나요?', a: 'omni에는 증권사에 직접 주문하는 명령이 없습니다. 보유 종목의 매도나 추가 매수를 요청하면, 알림으로 받은 일회용 승인 코드로 사용자가 승인한 뒤 실행기와 주문 가드가 처리합니다. 새 종목을 사는 명령도 없습니다.' },
  { q: 'Claude Code나 Codex가 아닌 AI 에이전트도 쓸 수 있나요?', a: '셸 명령을 실행할 수 있는 에이전트라면 omni CLI 가이드를 읽게 한 뒤 같은 명령을 쓸 수 있습니다. 설치와 권한 예시는 Claude Code와 Codex를 기준으로 안내합니다. 실제 시험은 Claude 에이전트로 했으므로, 다른 에이전트는 조회 명령부터 확인해 보세요. 프로젝트 스킬과 예약 작업은 Claude Code와 Codex를 기준으로 합니다.' },
  { q: '키와 계좌번호는 어디에 저장되나요?', a: '내 컴퓨터의 .env 파일에만 둡니다. 화면, 기록, 리포트, Discord 알림은 계좌번호를 가리고, 저장소에는 키가 들어가지 않습니다.' },
  { q: 'AI 검색에 내 계좌 정보가 전송되나요?', a: '지원하는 계좌·체결·성과 질문은 대시보드가 기록과 NH 조회로 직접 답하고 Liner로 보내지 않습니다. 그 밖의 계좌 질문은 채팅에서 내부 데이터를 고르고 물어보세요. 화면에서 드래그한 글은 금액과 비율을 ○로 가려 채우고, 보내기를 눌러야 전송됩니다. 직접 입력한 내용은 그대로 전송되니 이름, 연락처, 계좌번호는 넣지 마세요.' },
  { q: 'Jev는 무엇인가요?', a: '차트 이미지를 읽고 정해진 선택지에서 답을 고르는 TypeSafe 모델입니다. 1분 차트 이미지와 최근 90분 뉴스, 시황·수급·해외 선물·장전 요약, 최근 판단 결과와 매매일지를 참고합니다. 매수 후보 한 종목이나 대기를 고르고, 신뢰도도 함께 알려 줍니다. 판단마다 근거 질문 7개의 답을 기록하고 신뢰도 구간별 적중률을 매일 확인합니다. 신뢰도는 수익 확률이 아니며, 주문은 승인된 규칙과 주문 보호 조건을 통과해야 실행됩니다.' },
  { q: '장기투자는 무엇을 사나요?', a: '기준 구성은 국내 상장 ETF 6종입니다. 미국 지수(나스닥100, S&P500)와 테마(반도체, 양자, 우주) ETF를 사고, 매수에 쓸 대기 자금은 저수지 ETF(예를 들어 KOFR)에 둡니다. 여운봉 매매법은 나스닥100과 채권 혼합 ETF를 씁니다.' },
  { q: 'macOS가 아니어도 되나요?', a: '자동 실행 등록은 macOS launchd 기준입니다. 실행기와 대시보드는 Python이라 다른 환경에서도 돌릴 수 있지만 일정 등록은 직접 바꿔야 합니다. omni CLI로 Windows·Linux용 서비스 정의 파일을 만들 수 있지만 등록은 직접 해야 하고, 실제 Windows 실행은 아직 확인하지 않았습니다.' },
  { q: '원본 Vibe-Trading과 어떤 관계인가요?', a: 'HKUDS의 Vibe-Trading(MIT) 위에 국내 시장 자동매매와 장기투자를 더한 확장입니다. 원본의 라이선스와 NOTICE를 그대로 지킵니다.' },
]

// Stack diagram (연동 섹션). Data flows in from the left, alerts out to the right; the guard row is the one focal layer.
export const ARCH_LAYERS = [
  { label: '사람', items: [
    { name: '나', role: '채팅으로 묻고, 바꿀 것만 승인' },
    { name: 'Claude Code · Codex', role: '설치·감사·리포트 작성', href: doc('ai-agents.md') },
    { name: 'omni CLI', role: '여러 AI 에이전트를 잇는 공통 명령', href: doc('omni-cli.md') },
  ] },
  { label: '판단', items: [
    { name: 'TypeSafe Jev', role: '1분 차트·뉴스 판단', href: `${doc('integrations.md')}#typesafe-jev` },
    { name: 'OpenAI', role: '빠른 판단·관찰', href: `${doc('integrations.md')}#openai` },
    { name: '스킬 · 예약 작업', role: '장전 분석·장중 감사·매매일지', href: doc('automation.md') },
  ] },
  { label: '엔진', items: [
    { name: 'Jev 실시간 트레이딩', role: '국내 ETF·주식, 1분마다', href: doc('strategy.md') },
    { name: '장기투자', role: '미국 지수·테마 ETF, 하루 한 번', href: doc('longterm.md') },
    { name: '적립식', role: '새 적립금으로 ETF 매수', href: doc('longterm-dca.md') },
  ] },
  { label: '보호', focal: true, items: [
    { name: '주문 가드', role: '한도·거래 시간·중복 주문 확인', href: doc('safety.md') },
    { name: '실계좌 가드', role: '계좌별 손실 차단 기준·실주문 스위치', href: doc('live-accounts.md') },
    { name: 'STOP · HALT', role: '모의 실행기 종료·실주문 차단', href: doc('safety.md') },
  ] },
  { label: '증권사', items: [
    { name: 'NH 나무 PLUG', role: '시세·잔고·주문 (필수)', href: `${doc('integrations.md')}#nh-나무-plug` },
    { name: '키움 REST', role: '순위 선별·대체 시황 자료', href: `${doc('integrations.md')}#키움-rest` },
    { name: '토스증권 Open API', role: '신규상장·NXT 프리마켓', href: `${doc('integrations.md')}#토스증권-open-api` },
  ] },
]
