export const REPO = 'https://github.com/roadkwonai/omni-ai-trading'
export const VERSION = '0.1.3' // the repository VERSION file; scripts/release.py --check compares them
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

// Service intro video (YouTube, 권로드의 AI실험실 channel). Chapters match the video description.
export const VIDEO = {
  id: 'c4FZw6AYRAg',
  url: 'https://youtu.be/c4FZw6AYRAg',
  length: '3분 33초',
  chapters: [
    { t: 0, label: '왜 망설이면 지는가' },
    { t: 36, label: '두 엔진 · AI는 의견, 규칙이 주문' },
    { t: 84, label: 'Jev 실시간 판단과 진입 관문' },
    { t: 109, label: '여러 겹의 안전장치' },
    { t: 138, label: '장기투자 · 여운봉 저수지 매매법' },
    { t: 165, label: '복기와 리포트' },
  ],
}

export const NAV = [
  { href: '#why', label: '만든 이유' },
  { href: '#video', label: '소개 영상' },
  { href: '#ai', label: 'AI로 운영' },
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
  { href: '#longterm', icon: 'Compass', label: '거장의 장기투자', body: '시장 계절에 맞춘 거장 원칙' },
  { href: '#dca', icon: 'PiggyBank', label: '적립식 투자', body: '들어오는 돈을 꾸준히 모으기' },
  { href: '#ai', icon: 'ChatsCircle', label: 'AI 에이전트 운영', body: '채팅으로 묻고 맡기기' },
  { href: '#daily', icon: 'ClipboardText', label: '매일의 감사·리포트', body: '장전부터 마감까지 자동 점검' },
  { href: '#integrations', icon: 'PlugsConnected', label: '연동 생태계', body: '증권사·AI·데이터·알림' },
]

export const RETIREMENT_POVERTY = {
  source: 'https://www.oecd.org/en/publications/pensions-at-a-glance-2025_e40274c1-en/full-report/old-age-income-poverty_3cd946d7.html',
  scale: [0, 25, 50],
  rows: [
    { label: '한국', rate: 39.7 },
    { label: 'OECD 평균', rate: 14.8 },
  ],
}

export const WHY_STATS = [
  { num: '42.5점', title: '장기 재무목표 설정', label: '한국은행·금융감독원의 2024년 성인 금융이해력 조사에서 장기 재무목표 설정 항목이 받은 점수입니다.', source: '금융위원회 조사 참고표', href: 'https://www.fsc.go.kr/comm/getFile?fileNo=8&fileTy=ATTACH&srvcId=BBSTY1&upperNo=87218' },
  { num: '60%', title: '국내 신규 투자자 표본의 손실 비율', label: '자본시장연구원이 4개 증권사의 2020년 3~10월 거래를 분석했을 때, 표본 속 신규 투자자 10명 중 6명이 거래비용을 반영한 뒤 손실을 봤습니다.', source: '자본시장연구원 연구', href: 'https://www.kcmi.re.kr/report/report_view?report_no=1243' },
]

export const WHY_CASES = [
  { kind: '금융위원회 공개 상담 사례', title: '수입이 불규칙하면 비상자금부터', body: '수입이 일정하지 않은 20대 프리랜서의 상담에서는 3개월 평균을 바탕으로 재무계획을 세우고 비상자금을 마련하는 일부터 다뤘습니다. Omni를 쓸 때도 먼저 운용할 수 있는 자금을 정하고 그 범위에서 적립과 비중 관리 기준을 세웁니다.', href: 'https://www.fsc.go.kr/no010101/87218', label: '상담 사례 보기' },
  { kind: 'Omni v0.1.3 개선 사례', title: '개장 직후 판단이 늦었던 이유', body: '당일 완료 분봉을 기다리느라 판단이 늦었습니다. 실행기를 08:55에 준비하고, 개장 직후 부족한 분봉은 전 거래일 마감 자료나 확인 가능한 당일 NXT 프리마켓 자료로 보완하도록 개선했습니다.', href: `${REPO}/blob/v0.1.3/release/2026-11-20-v0.1.3-opening-and-inverse.md#개장-즉시-진입`, label: '릴리스에서 개선 과정 보기' },
]

export const TRAPS = [
  { trap: '이미 오른 종목을 뒤늦게 따라 삽니다', human: '차트와 뉴스, 수급을 따로 찾는 사이 가격이 바뀝니다.', fix: 'Jev가 자료를 함께 읽고, 실행기는 진입·추격 조건을 확인합니다. 기다린 이유도 기록합니다.' },
  { trap: '손절을 미루고 비중을 더 키웁니다', human: '매수 전에 손절과 추가 매수의 기준을 정하지 않으면 가격이 빠질 때 판단이 흔들립니다.', fix: '실행기는 회차마다 손절·익절과 주문 가능 여부를 확인합니다. 접수와 체결은 매매일지에서 따로 봅니다.' },
  { trap: '매일 시세를 들여다볼 시간이 없습니다', human: '오래 모으기로 해도 급락장이나 과열장에서 적립을 이어 갈지 고민됩니다.', fix: '장기 엔진은 정해 둔 적립·리밸런싱 기준으로 계획을 세웁니다. 전략별 결과는 가상 장부에서 비교할 수 있습니다.' },
]

// 매수는 기술, 매도는 예술 (#art). Every line maps to a rule in docs/strategy.md.
export const BUY_CRAFT = [
  { title: '국면을 먼저 봅니다', body: '코스피·코스닥을 상승장·횡보장·하락장으로 판정하고, 시장이 매수 방향과 다를수록 크기를 **0.75배, 0.5배**로 줄입니다.' },
  { title: '돈이 몰리는 곳을 찾습니다', body: '거래대금·거래량·상승률 순위, 테마 대장주, 투자자·프로그램 순매수로 ==오늘의 주도주 후보==를 추립니다.' },
  { title: 'AI가 1분마다 고릅니다', body: 'Jev가 차트와 90분 뉴스, 시황과 수급을 보고 **종목 하나와 신뢰도**를 냅니다.' },
  { title: '규칙이 한 번 더 거릅니다', body: '지수 방향과 최근 분봉·거래량을 확인합니다. 인버스는 지수 하락이 이어지는지와 ETF 가격을 추격하는지도 봅니다.' },
  { title: '나눠 삽니다', body: '한 번에 다 사지 않고, **직전보다 나은 조건일 때만** 더 삽니다. 장 마감 30분 전부터는 추가 매수를 하지 않습니다.' },
]
export const SELL_ART = [
  { title: '절반은 먼저 챙깁니다', body: '방어형은 **+1%에 절반, +2%에 나머지**를 팝니다. 추세가 강하면 **+2%에 절반**을 팝니다.' },
  { title: '나머지는 추세를 따라갑니다', body: '남은 수량은 ==고점에서 1.5% 밀릴 때까지== 들고 갑니다. 2배 ETF는 폭을 1.5배로 넓힙니다.' },
  { title: '번 돈은 지킵니다', body: '절반을 익절한 뒤 평균가로 돌아오면 **나머지를 본전에 정리**합니다.' },
  { title: '불리하면 손실을 짧게 끊습니다', body: '손절은 기본 ==−2%==, 국면이 포지션에 불리하면 ==−1%==. 계좌 전체 손실은 **하루 한도**로 따로 막습니다.' },
  { title: 'AI가 보유를 다시 봅니다', body: '매분 보유 종목마다 AI 판단을 받아, **확신이 크면 전량, 애매하면 33%**만 팝니다. 산 뒤 하락장으로 바뀌면 **절반을 줄입니다.**' },
  { title: '큰 추세는 다음 날까지 가져갑니다', body: '==조건 11개를 통과한 추세형만== 이월하고, 스윙 조건 6개를 매일 다시 확인해 **최대 5거래일** 들고 갑니다.' },
]
// One position from entry to exit (#art diagram). `exits` can end it at any point.
export const LIFECYCLE = [
  { step: '진입', body: '분할 1회 · 방어형 또는 추세형' },
  { step: '1차 익절', body: '+1~2%에서 남은 수량의 절반' },
  { step: '추적 청산', body: '고점 대비 −1.5%까지 따라감' },
  { step: '이월', body: '추세형 · 조건 11개 모두 통과' },
  { step: '스윙', body: '조건 6개 매일 확인 · 최대 5거래일' },
]
export const LIFECYCLE_EXITS = ['손절 −2% · 불리한 국면 −1%', '익절 뒤 본전 정리', 'Jev 매도 33%·전량', '하락장 전환 시 절반', '14:40 장 마감 정리', '계좌 하루 손실 한도']

// Jev by market regime (docs/strategy.md 시황별 대응).
export const REGIME_PLAYS = [
  { name: '상승장', tone: 'up', body: '롱을 중심으로 사고 ==추세형 포지션은 더 길게== 보유하며, 조건을 통과하면 **이월·스윙**까지 가져갑니다. 인버스는 관문에서 막힙니다.' },
  { name: '횡보장', tone: 'wait', body: '매수 크기 **0.75배**, 손절 **−1%**. 같은 날 다시 살 때는 추격 매수를 막고 AI에게 한 번 더 확인합니다. ==대부분 그날 정리==합니다.' },
  { name: '하락장', tone: 'down', body: '일반 롱은 매수 크기를 줄이고 손절 폭을 좁힙니다. 인버스는 별도 진입 관문을 거치며, 진입할 때 지수 낙폭에 따라 익절 목표가 달라집니다.' },
]

// 내 투자 스타일 (#styles). `how` is the real setting path.
export const STYLES = [
  { name: '장기형', tag: '적립과 비중 관리', who: '매일 차트를 보지 않고 **오래 모으고 싶은 분**', run: '장기투자 + 적립식', how: '운영 엔진 → 장기투자만' },
  { name: '균형형', tag: '중심은 지키며 일부로 대응', who: '자산의 중심은 지키며 **시장 흐름에도 올라타고 싶은 분**', run: '장기투자 + Jev 트레이딩', how: '운영 엔진 → 둘 다 운영. 실계좌는 계좌마다 엔진 선택' },
  { name: '공격형', tag: '흐름을 적극적으로', who: '주도주와 시장 흐름을 **적극적으로 따라가고 싶은 분**', run: 'Jev 트레이딩만', how: '운영 엔진 → Jev만. 진입 경로 빠른 선택 → 적극적' },
  { name: '실험형', tag: '먼저 지켜보기', who: '아직 확신이 없어 **결과부터 보고 싶은 분**', run: '모의계좌·가상 계좌로만', how: '기본값 그대로(실주문 스위치 꺼짐)' },
]
export const GROW = [
  { title: '프리셋으로 시작', body: '진입 경로마다 보수적·기본·적극적·끄기 중에서 고릅니다.' },
  { title: '매일 복기', body: '장 마감 개선안, 신호 적중률, 사지 않은 후보의 사후 결과를 봅니다.' },
  { title: '승인한 것만 반영', body: '개선안은 제안으로만 오고, **내가 승인한 설정만** 바뀌며 변경 이력이 남습니다.' },
]

// 설정은 JSON, 경험은 SQLite (#data).
export const DATA_JSON = [
  { file: 'accounts.json', body: '계좌, 운용금, 주문 보호 한도, 실주문 스위치' },
  { file: 'strategy-settings.json', body: '진입·익절·손절·이월 기준과 제한 규칙 스위치' },
  { file: 'tunables.json', body: '장 시간, 조회 주기 같은 운영 조정값' },
  { file: 'vibe-longterm/settings.json', body: '장기투자 거장 모듈, 비중, 적립식' },
]
export const DATA_SQLITE = [
  { file: 'trading-ledger', body: '판단·주문·체결 이벤트 원장' },
  { file: 'market-research', body: 'Jev 입력과 판단 근거, 신호 적중률, 그림자 후보' },
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
  { icon: 'Download', title: '설치는 AI 에이전트가 해 줍니다', body: 'Claude Code나 Codex를 열고 설치 프롬프트를 붙여 넣으면, 에이전트가 **저장소·가상환경·NH MCP·스킬 연결까지** 알아서 설치합니다. 키를 넣거나 주문과 관련된 단계에서는 멈추고 확인을 받습니다.' },
  { icon: 'ChatsCircle', title: '운영은 대화로 합니다', body: '"오늘 왜 안 샀어?", "손절을 1.5%로 바꾸면 뭐가 달라져?"처럼 물으면 **기록을 읽고 근거와 함께** 답합니다. 설정은 사용자가 승인한 것만 바뀝니다.' },
  { icon: 'CalendarCheck', title: '반복 업무는 예약 작업이 맡습니다', body: '사람이 자리를 비워도 **예약 작업 15개**가 장전 점검과 분석, 장중 감사, 장 마감 개선안, 매매일지와 장기투자 리포트를 씁니다.' },
]
export const SCHEDULE_CHIPS = ['08:25 장전 점검', '08:31 장전 상세 분석', '08:50 장기투자 전략 리포트', '매시 20분 장중 감사', '16:15 장 마감 개선안', '16:40 매매일지', '16:45 장기투자 복기']
export const AI_SKILLS = ['장전 분석', '장중 감사', '매매일지 작성', '장기투자 전략 리포트', 'HTML 리포트 발행', '대시보드 화면 확인', '다른 Mac으로 옮기기', '시황 트레이딩 점검', '운영 규칙·시황 조회']

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
  { label: '자기 성적과 매매일지', detail: '최근 판단 성적과 지난 3거래일 매매일지 요약을 스스로 돌아봅니다' },
]

export const JEV_USES = [
  { title: '진입 후보', body: '매분 지금 살 만한 종목 하나 또는 대기를 고릅니다.' },
  { title: '보유 판단', body: '보유 종목마다 유지·매도를 묻고, 애매하면 일부만 팝니다.' },
  { title: '관심종목 선정', body: '30분마다 순위 후보의 재무·수급·뉴스를 보고 3종목을 고릅니다.' },
  { title: '낙폭과대 리서치', body: '시장 폭락일 후보를 차트·수급·애널리스트·과거 사례로 검토합니다.' },
  { title: '재진입 재검토', body: '그날 사고판 종목을 불리한 시장에서 다시 살 때 한 번 더 묻습니다.' },
  { title: '개장 이월 판단', body: '전날 들고 온 종목을 개장 직후 다시 판단합니다.' },
]

export const JEV_TRUST = [
  { title: '선택형으로 답합니다', body: '자유 문장이 아니라 종목 하나와 신뢰도로 답해, 규칙으로 걸러 쓰기 쉽습니다.' },
  { title: '근거를 함께 남깁니다', body: '판단마다 결정 요인과 차트·뉴스·시황·수급 방향을 묻는 근거 질문 7개를 기록합니다.' },
  { title: '적중률을 매일 확인합니다', body: '신뢰도 구간별로 5·15·30분 뒤 결과를 모아, 높은 신뢰도가 정말 더 맞는지 확인합니다.' },
  { title: '주문은 규칙이 정합니다', body: '신뢰도는 수익 확률이 아닙니다. 진입 조건과 주문 가드를 통과해야 주문이 실행됩니다.' },
]

// Risk order, highest first. `masters` are MASTERS ids recommended in that season.
export const REGIMES = [
  { name: '투매', rule: 'VIX 35 이상 · 낙폭 −15% 이하', masters: ['base', 'dalio', 'reservoir', 'marks', 'williams-bounce', 'lynch', 'buffett'] },
  { name: '약세', rule: '낙폭 −20% 이하 또는 200일선 아래 하락', masters: ['base', 'dalio', 'reservoir', 'marks', 'williams-trend', 'lynch', 'buffett'] },
  { name: '조정', rule: '낙폭 −10% 이하 또는 200일선 아래', masters: ['base', 'dalio', 'reservoir', 'lynch', 'buffett'] },
  { name: '회복', rule: '하락 국면을 벗어나는 중', masters: ['base', 'dalio', 'lynch', 'buffett'] },
  { name: '과열', rule: '200일선 +15% 이상 · VIX 14 이하', masters: ['base', 'dalio', 'marks', 'lynch', 'buffett'] },
  { name: '강세 안정', rule: '그 밖의 상승장', masters: ['base', 'dalio', 'lynch', 'buffett'] },
]

export const MASTERS = [
  { id: 'base', name: '기준 비중', idea: '흔들리지 않는 바탕', does: '코어 60 · 위성 25 · 저수지 15의 기준 비중을 들고 갑니다.', on: true },
  { id: 'dalio', name: '레이 달리오', idea: '벗어나면 되돌린다', does: '매월 목표 비중에서 ±5%p를 벗어나면 전체를 목표로 되돌립니다.', on: true },
  { id: 'lynch', name: '피터 린치', idea: '스토리가 살아 있는가', does: 'AI가 테마마다 유지·주의·훼손을 판정하고 위성 비중을 조정합니다.', on: true },
  { id: 'buffett', name: '워런 버핏·필립 피셔', idea: '팔 이유가 없으면 들고 간다', does: '손절하지 않고, 한 달 매도를 순자산의 10%로 묶어 회전을 낮춥니다.', on: true },
  { id: 'marks', name: '하워드 막스', idea: '시장의 시계추를 거슬러', does: '과열이면 코어를 덜어 저수지로, 투매면 매수 단계를 앞당깁니다.', on: false },
  { id: 'reservoir', name: '저수지 분할', idea: '떨어질 때를 기다린 돈', does: '나스닥100이 고점 대비 −20%·−30%일 때 저수지를 나눠 투입합니다.', on: false },
  { id: 'williams-trend', name: '래리 윌리엄스 · 추세', idea: '추세가 꺾이면 줄인다', does: '테마 지수가 200일선 아래 5일 머물면 그 테마를 절반으로 줄입니다.', on: false },
  { id: 'williams-bounce', name: '래리 윌리엄스 · 반등', idea: '급락 뒤 짧은 반등', does: '하루 −3%·VIX 25 이상이면 저수지 일부를 나스닥100에 잠시 옮깁니다.', on: false },
]

export const LONGTERM_POINTS = [
  { title: '거장별 관점 리포트', body: '매일 아침 거장 7명의 원칙으로 지금 시장을 읽어 사기·기다리기·유지·덜기 의견을 내고, 엔진 판단과 무엇이 다른지 씁니다.' },
  { title: '시장 온도와 거시 배경', body: 'VIX·200일선·낙폭·하이일드 스프레드로 공포와 탐욕을 0~100으로 보여 주고, 금리차·실질금리·FOMC 일정을 함께 봅니다.' },
  { title: '여운봉 저수지 매매법', body: '전부 저수지에서 시작해 나스닥100이 고점 대비 −20·−30·−40%일 때 나눠 사고 70:30으로 맞춥니다.' },
  { title: '레시피 비교와 근거', body: '가상 1억 원 계좌와 전략 조합별 가상 장부로 먼저 비교하고, 주문마다 왜 샀는지 근거를 남깁니다.' },
]

export const LONGTERM_FLOW = [
  { title: '적립 계획 세우기', body: '오래 운용할 자금의 투입 시기와 금액을 정합니다.' },
  { title: '현재 비중과 목표 비교', body: '시장 국면과 선택한 전략을 바탕으로 비중 차이를 살핍니다.' },
  { title: '매수·대기·비중 조정', body: '적립식은 새 자금의 매수를, 계좌 전략은 기존 자산의 비중 조정을 계획합니다.' },
  { title: '가상 장부에서 돌아보기', body: '전략별 비용·낙폭·비중 변화를 비교합니다.' },
]

export const DCA_AXES = [
  { axis: '언제', master: '매 거래일 확인하고, 국면과 하락일에 맞춰 매수 빈도마다 한 번', me: '정한 날 또는 하락일 우선' },
  { axis: '얼마나', master: '적립금 속도 × 국면 배수(0.5~1.5), 밀린 몫은 조금씩 따라잡기', me: '정한 금액 또는 종목별 N주' },
  { axis: '무엇을', master: '계좌 전략이 정한 ETF 비중대로', me: '고른 ETF만' },
  { axis: '몇 시', master: '09:40부터 전일 종가 아래로 내려온 첫 순간, 없으면 14:20', me: '정한 시각' },
]

export const DCA_PROFILES = ['존 보글·워런 버핏 · 시황 보지 않고 꾸준히', '레이 달리오 · 균형, 과열만 조금 덜', '하워드 막스 · 비쌀 때 덜, 쌀 때 더', '여운봉 · 하락할수록 조금 더', '피터 린치 · 테마 스토리 반영', '래리 윌리엄스 · 급락일 우선(비권장)', '시황 자동 · 국면에 맞는 거장(비교용 가상 장부)']

export const DAILY = [
  { phase: '장전', time: '08:20', title: '장전 사전 점검', body: '서비스·계좌·미확인 주문·설정을 점검하고, 실패는 바로 알립니다.' },
  { phase: '장전', time: '08:31', title: '장전 상세 분석', body: '해외 시장, 야간선물, NXT 프리마켓, 공시로 오늘 시장을 정리합니다.' },
  { phase: '장전', time: '08:50', title: '장기투자 전략 리포트', body: '국면과 거장 7명의 관점 의견, 오늘 주문 계획을 씁니다.' },
  { phase: '장중', time: '09:00', title: '개장과 동시에', body: '이월 포지션을 다시 판단하고, 새 진입은 규칙과 가드를 거쳐 주문합니다.' },
  { phase: '장중', time: '매시 20분', title: '장중 감사', body: '판단·차단·체결을 점검하고, 다시 생긴 문제는 바로 알립니다.' },
  { phase: '마감', time: '15:40', title: '마감 요약', body: '계좌별 손익과 잔고를 알림 채널로 보냅니다.' },
  { phase: '마감', time: '16:15', title: '장 마감 개선안', body: '그날 거래와 신호 적중률로 개선안을 제안합니다. 적용하려면 사람이 승인해야 합니다.' },
  { phase: '마감', time: '16:40', title: '매매일지', body: '복기, 시장, 수급, 핵심 이슈를 같은 템플릿으로 남깁니다.' },
  { phase: '마감', time: '16:45', title: '장기투자 복기·감사', body: '아침 계획과 실제 결과, 거장별 관점을 비교합니다.' },
  { phase: '주간·월간', time: '금 · 월초', title: '주간·월간 감사', body: '낙폭과대·장기투자 주간 감사와 월·분기·반기·연 매매일지를 씁니다.' },
]

export const INTEGRATIONS = [
  { group: '증권', items: [
    { name: 'NH 나무 PLUG', role: '시세·잔고·주문 (필수)', anchor: 'nh-나무-plug' },
    { name: '키움 REST', role: '순위 선별·시황 대체', anchor: '키움-rest' },
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
    { name: '네이버 · Yahoo · 뉴스 RSS', role: '수급·해외·헤드라인', anchor: '키-없이-쓰는-출처' },
  ] },
  { group: '알림·게시', items: [
    { name: 'Discord', role: '알림·리포트 링크', anchor: 'discord' },
    { name: 'Telegram', role: '알림·리포트 링크', anchor: 'telegram' },
    { name: 'Slack', role: '알림·리포트 링크', anchor: 'slack' },
    { name: 'GitHub Pages', role: 'HTML 리포트 게시', anchor: '리포트-게시-github-pages' },
  ] },
]

export const HIGHLIGHTS = [
  { icon: 'Scales', title: 'AI는 의견, 규칙이 주문', body: 'Jev와 OpenAI가 의견과 신뢰도를 내고, 주문은 승인된 진입·청산 규칙과 주문 가드를 모두 통과해야 실행됩니다.', proof: '신뢰도는 수익 확률이 아닙니다' },
  { icon: 'Stack', title: '성격이 다른 두 엔진', body: '국내 ETF·주식을 1분마다 판단하는 실시간 트레이딩과 미국 지수·테마 ETF를 천천히 모으는 장기투자를 따로 켜고 끕니다.', proof: '엔진별 장부와 운영 스위치' },
  { icon: 'ShieldCheck', title: '모의 먼저, 실계좌는 단계적으로', body: '관찰과 가상 실행으로 전략을 살펴볼 수 있습니다. 실제 주문에는 계좌별 승인과 한도가 적용됩니다.', proof: '실주문 확인과 계좌별 주문 보호' },
  { icon: 'ArrowsClockwise', title: '기록하고 매일 돌아봅니다', body: '판단 근거, 주문, 체결이 남고 장 마감 뒤 매매일지와 개선안, 신호 적중률이 쌓입니다. 개선안은 사람이 승인해야 반영됩니다.', proof: '모든 설정 변경에 이력' },
  { icon: 'ChatsCircle', title: 'AI와 채팅으로 운영', body: 'Claude Code나 Codex에 말로 묻고 맡깁니다. 프로젝트 스킬과 예약 작업이 분석, 감사, 리포트를 씁니다.', proof: '스킬 9개 · 예약 작업 15개' },
  { icon: 'LockKey', title: '내 컴퓨터에서, 내 키로', body: 'Mac에서 로컬로 돌고 키와 계좌번호는 .env에만 둡니다. 화면, 리포트, 알림은 계좌번호를 가립니다.', proof: '저장소에 키·원장 없음' },
]

export const CHAT = [
  { who: 'me', text: '오늘 장전 상세 분석 써 줘. 야간선물, NXT 프리마켓, 미국 시장을 근거로.' },
  { who: 'ai', text: '장전 자료를 읽고 템플릿 검사까지 마쳤습니다. 리포트 링크를 알림 채널로 보냈어요.' },
  { who: 'me', text: '10시 이후 진입이 막힌 이유를 종목별로 정리해 줘.' },
  { who: 'ai', text: '3종목이 인버스 관문, 2종목이 추격 금지에 막혔습니다. 같은 이유로 3분 넘게 막힌 종목은 없습니다.' },
  { who: 'me', text: '신규 매수 마감을 14:00으로 바꾸면 뭐가 달라져?' },
  { who: 'ai', text: '선별 v2 매수 창은 그대로이고 일반 진입만 30분 일찍 멈춥니다. 승인하시면 변경 이력을 남기고 저장할게요.' },
]

export const AI_LOOP = [
  { title: '묻고 맡기기', body: '상태 조회, 분석, 리포트를 채팅으로 요청합니다.' },
  { title: '스킬이 처리', body: '장전 분석, 장중 감사, 매매일지 스킬이 정해진 템플릿대로 씁니다.' },
  { title: '사람이 승인', body: '설정 변경과 실주문은 승인한 것만 반영됩니다.' },
  { title: '실행기가 주문', body: '주문은 실행기와 주문 가드만 냅니다. 채팅은 주문하지 않습니다.' },
]

export const TAGLINE = ['AI는 의견을 내고,', '규칙이 주문을 정하고,', '안전장치가 계좌를 지킵니다.']

export const FEATURES = [
  { icon: 'TrendDown', title: '폭락일 반등 매매', body: '지수가 크게 빠진 날 14:30부터 시장과 같이 빠진 종목을 나눠 사고, 오르면 나눠 팝니다.', href: doc('oversold.md') },
  { icon: 'Bank', title: '보호된 실계좌', body: '실주문 스위치, 하루 손실 차단 기준, 1주 가격 상한을 계좌마다 적용합니다. 미체결이나 미확인 주문이 있으면 새 주문을 막습니다.', href: doc('live-accounts.md') },
  { icon: 'Plant', title: '적립식과 여운봉 매매법', body: '새로 넣는 적립금은 적립식이, 계좌에 있는 운용금은 계좌 전략이 맡아 서로 섞이지 않습니다.', href: doc('longterm-dca.md') },
  { icon: 'Notebook', title: '매매일지와 리포트', body: '체결 내역, 투자자별 매매 동향, 중요도 별점을 매긴 뉴스, 종목 차트를 모아 일·월·분기·연 단위 일지와 HTML 리포트를 만듭니다.', href: doc('journal.md') },
  { icon: 'BellRinging', title: 'Discord·Telegram·Slack 알림', body: '체결과 손익은 바로, 리포트는 페이지가 열린 뒤 링크로 보냅니다. 같은 알림은 한 번만 갑니다.', href: doc('integrations.md') },
  { icon: 'ListChecks', title: '장전 점검과 장중 감사', body: '개장 전에 자동 실행 프로그램과 계좌를 점검하고, 장중에는 매시간 판단과 주문이 막힌 이유를 살펴 문제 기록을 남깁니다.', href: doc('automation.md') },
]

export const SAFETY_STAGES = [
  { step: '1', title: '관찰', body: '실계좌는 계획만 기록하고 주문하지 않습니다.' },
  { step: '2', title: '가상 실행', body: '실시간 호가로 사고판 것처럼 가상 기록을 만들어 결과를 쌓습니다.' },
  { step: '3', title: '보호된 실주문', body: '실행 인자, 계좌 자동매매·실주문 스위치, 중단 해제와 주문별 보호 조건을 확인합니다.' },
]

export const LIMITS = [
  ['모의계좌', '1회 매수 금액, 하루 매수 금액, 보유 금액, 하루 새 종목 수, 하루 손실 한도를 대시보드에서 정한 범위 안으로 설정'],
  ['실계좌', '계좌별 하루 손실 차단 기준 상한 2만 원, 1주 가격 상한, 하루 새 종목 최대 5개(장기투자 첫 투입일 최대 6개), 미체결·미확인 주문이 있으면 새 주문 차단'],
  ['공통', '계좌와 거래소 장 시간, 주문 기록과 잔고를 확인합니다. 공유 모의 STOP은 미확인 주문을 대조한 뒤 실행기를 종료하고, 실계좌 HALT는 실주문 경로를 차단합니다.'],
]

export const SCREENS = [
  { id: 'overview', label: '종합 현황', caption: '모든 계좌의 오늘 손익, 잔고, 매매 프로그램 상태를 한 화면에서 봅니다.' },
  { id: 'strategy-flow', label: '전략 구조', caption: '매수 판단부터 주문 한도 검사까지 오늘 설정이 반영된 흐름도입니다.' },
  { id: 'trading', label: '트레이딩 현황', caption: '계좌를 골라 보유, 체결, 수동 매수와 매도 요청을 다룹니다.' },
  { id: 'journal', label: '매매일지', caption: '계좌별 성과, 시장과 투자자별 매매 동향, 핵심 뉴스 별점을 카드로 모읍니다.' },
  { id: 'compare', label: '성과 비교', caption: 'Jev 트레이딩과 장기투자의 손익, 수익률, 낙폭을 같은 날짜 위에 놓고 비교합니다.' },
  { id: 'longterm', label: '장기투자', caption: '시장 상태, 가상 계좌 자산, 비교용 가상 계좌와 오늘의 판단을 봅니다.' },
  { id: 'longterm-chart', label: '장기 차트', caption: '이동평균선과 사고팔 가격대, 적립식·여운봉 매수 단계를 그립니다.' },
  { id: 'accounts', label: '계좌·자동매매', caption: '켤 엔진과 계좌별 운용 방식, 주문 한도를 정합니다.' },
  { id: 'live', label: '실계좌', caption: '실계좌별 실행기 상태와 보호 한도, 장기투자 계좌의 오늘 계획을 봅니다.' },
  { id: 'api', label: '운영', caption: '운영 → API 연동에서 외부 API 호출과 실패, 지연을 10분 단위로 셉니다.' },
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

export const STEPS = [
  { title: '저장소 받기', body: 'Python 3.11, Node.js 22 이상, NH 나무 PLUG 앱 키(모의투자 계좌 포함), 유료 요금제의 Claude Code 또는 Codex를 준비합니다.', code: 'git clone https://github.com/roadkwonai/omni-ai-trading.git' },
  { title: '키 넣기', body: '.env.example을 .env로 복사하고 권한을 600으로 둡니다. 키와 계좌번호는 이 파일에만 둡니다.', code: 'cp .env.example .env && chmod 600 .env' },
  { title: 'AI에게 맡기기', body: 'Claude Code나 Codex에 아래 프롬프트를 주면 설치와 연결 확인을 진행하고, 주문 서비스는 승인을 기다립니다.', code: null },
]

export const FAQ = [
  { q: 'AI가 판단하면 손실이 안 나나요?', a: 'AI 판단에도 오류가 있고 원금 손실이 생길 수 있습니다. 실행기는 계좌와 전략에 따른 보호 조건을 적용합니다. 시세 급변이나 미체결로 실제 손실은 계좌의 손실 차단 기준을 넘을 수 있습니다.' },
  { q: '트레이딩과 장기투자 중 무엇부터 해야 하나요?', a: '처음에는 모의계좌와 가상 계좌에서 둘 다 켜 두고, 성과 비교 탭과 매매일지로 몇 주 비교해 본 뒤 정하기를 권합니다. 운영 엔진에서 둘 다, Jev만, 장기투자만을 언제든 바꿀 수 있습니다.' },
  { q: '수익이 나나요?', a: '보장하지 않습니다. AI 판단, 모의 성과, 가상 체결, 과거 재현 결과는 미래 수익을 보장하지 않고, 금융투자상품은 원금 손실(0~100%)이 생길 수 있습니다. ' + DISCLAIMER_SHORT },
  { q: 'AI 도구 비용이 드나요?', a: AI_PAID + ' 요금과 이용 조건은 각 서비스의 최신 안내를 확인하세요.' },
  { q: '설치하면 실계좌로 바로 주문하나요?', a: '기본은 모의계좌입니다. 실계좌 주문에는 실행 인자, 계좌별 자동매매·실주문 스위치, 중단 해제와 주문별 보호 조건이 필요합니다. 실주문 스위치를 켤 때는 확인 문구를 입력합니다.' },
  { q: '어떤 증권사를 쓰나요?', a: 'NH투자증권 나무 PLUG OpenAPI와 공식 MCP를 씁니다. 키움과 토스 Open API는 종목 순위와 시장 정보를 보조로 받아 오는 데만 씁니다. NH투자증권과 제휴 관계가 없는 비공식 프로젝트입니다.' },
  { q: '키와 계좌번호는 어디에 저장되나요?', a: '내 컴퓨터의 .env 파일에만 둡니다. 화면, 기록, 리포트, Discord 알림은 계좌번호를 가리고, 저장소에는 키가 들어가지 않습니다.' },
  { q: 'Jev는 무엇인가요?', a: 'TypeSafe의 이미지·선택형 판단 모델로, 이 시스템의 실시간 AI 트레이더입니다. 1분 차트 이미지와 최근 90분 뉴스, 시황·수급·해외 선물·장전 브리프, 자기 성적과 매매일지를 함께 보고 종목 하나(또는 대기)와 신뢰도를 고릅니다. 판단마다 근거 질문 7개를 기록하고 신뢰도 구간별 적중률을 매일 추적합니다. 신뢰도는 수익 확률이 아니며, 주문은 승인된 규칙과 주문 가드가 정합니다.' },
  { q: '장기투자는 무엇을 사나요?', a: '국내에 상장된 미국 지수와 테마 ETF 6종(나스닥100, S&P500, 반도체, 양자, 우주, 대기 자금 역할의 KOFR)입니다. 여운봉 매매법은 나스닥100과 채권 혼합 ETF를 씁니다.' },
  { q: 'macOS가 아니어도 되나요?', a: '자동 실행 등록은 macOS launchd 기준입니다. 매매 프로그램과 대시보드는 Python이라 다른 환경에서도 돌릴 수 있지만 일정 등록은 직접 바꿔야 합니다.' },
  { q: '원본 Vibe-Trading과 어떤 관계인가요?', a: 'HKUDS의 Vibe-Trading(MIT) 위에 국내 시장 자동매매와 장기투자를 더한 확장입니다. 원본의 라이선스와 NOTICE를 그대로 지킵니다.' },
]

// Stack diagram (연동 섹션). Data flows in from the left, alerts out to the right; the guard row is the one focal layer.
export const ARCH_LAYERS = [
  { label: '사람', items: [
    { name: '나', role: '채팅으로 묻고, 바꿀 것만 승인' },
    { name: 'Claude Code · Codex', role: '설치·감사·리포트 작성', href: doc('ai-agents.md') },
  ] },
  { label: '판단', items: [
    { name: 'TypeSafe Jev', role: '1분 차트·뉴스 판단', href: `${doc('integrations.md')}#typesafe-jev` },
    { name: 'OpenAI', role: '빠른 판단·관찰', href: `${doc('integrations.md')}#openai` },
    { name: '스킬 · 예약 작업', role: '장전 분석·장중 감사·매매일지', href: doc('automation.md') },
  ] },
  { label: '엔진', items: [
    { name: 'Jev 실시간 트레이딩', role: '국내 ETF·주식, 1분마다', href: doc('strategy.md') },
    { name: '장기투자', role: '미국 지수·테마 ETF, 하루 한 번', href: doc('longterm.md') },
    { name: '적립식', role: '들어오는 적립금만', href: doc('longterm-dca.md') },
  ] },
  { label: '보호', focal: true, items: [
    { name: '주문 가드', role: '한도·장 시간·중복 주문', href: doc('safety.md') },
    { name: '실계좌 가드', role: '계좌별 손실 차단 기준·실주문 스위치', href: doc('live-accounts.md') },
    { name: 'STOP · HALT', role: '모의 실행기 종료·실주문 차단', href: doc('safety.md') },
  ] },
  { label: '증권사', items: [
    { name: 'NH 나무 PLUG', role: '시세·잔고·주문 (필수)', href: `${doc('integrations.md')}#nh-나무-plug` },
    { name: '키움 REST', role: '순위 선별·시황 대체', href: `${doc('integrations.md')}#키움-rest` },
    { name: '토스증권 Open API', role: '신규상장·NXT 프리마켓', href: `${doc('integrations.md')}#토스증권-open-api` },
  ] },
]
