export const REPO = 'https://github.com/roadkwonai/omni-ai-trading'
export const doc = (name: string) => `${REPO}/blob/main/docs/${name}`
export const START = `${REPO}#빠른-시작`

export const NAV = [
  { href: '#engines', label: '두 엔진' },
  { href: '#features', label: '기능' },
  { href: '#day', label: '하루' },
  { href: '#safety', label: '안전' },
  { href: '#screens', label: '화면' },
  { href: './reports/', label: '리포트' },
]

export const PROBLEMS = [
  {
    worry: 'AI가 틀리면 돈을 잃습니다',
    answer: 'Jev가 고른 후보도 진입 경로, 분할 매수, 청산 규칙과 주문 가드를 모두 지나야 주문이 나갑니다. 모델의 신뢰도는 수익 확률이 아닙니다.',
  },
  {
    worry: '주문이 꼬이면 멈출 수 없습니다',
    answer: '회차마다 증권사 잔고와 원장을 먼저 맞춰 봅니다. 미확인 주문이나 잔고 불일치가 보이면 새 주문 없이 멈춥니다.',
  },
  {
    worry: '무엇을 왜 샀는지 남지 않습니다',
    answer: '판단 근거, 주문, 체결이 원장과 대시보드에 남고, 매일 장 마감 뒤 매매일지와 개선안이 자동으로 쌓입니다.',
  },
]

export const ENGINES = [
  {
    id: 'jev',
    tab: 'Jev 실시간 트레이딩',
    mascot: 'mascot-realtime',
    screen: 'strategy-flow',
    title: '국내 ETF와 주식을 1분마다 판단합니다',
    lead: '공유 모의계좌 실행기가 NH 시세와 1분봉을 읽고, Jev가 차트 이미지와 최근 90분 뉴스로 후보를 고릅니다.',
    points: [
      'Jev 후보, 관심종목 강도, 신규상장 세 가지 진입 경로와 병렬 전략 슬롯',
      '분할 매수와 추가 매수 크기를 보유량과 시황으로 조절',
      '상승장, 횡보장, 하락장마다 다른 손절과 익절, 인버스는 폭락 신호가 모두 모일 때만',
      '시장 폭락일의 낙폭과대 선별 v2 분할 진입과 분할 익절',
      '실계좌 1은 모의 체결을 비율대로 따라 하고, 계좌별 한도 안에서만 주문',
    ],
    link: { href: doc('strategy.md'), label: '매매 전략 가이드' },
  },
  {
    id: 'longterm',
    tab: '장기투자',
    mascot: 'mascot-longterm',
    screen: 'longterm-chart',
    title: '미국 지수와 테마 ETF를 천천히 모읍니다',
    lead: '매일 아침 나스닥100 낙폭, 200일선, VIX로 국면 6개 중 하나를 정하고, 거장 모듈 8개가 목표 비중을 조정합니다.',
    points: [
      '코어 60 · 위성 25 · 저수지 15 비중과 국내 상장 미국 ETF 6종',
      '보글, 달리오, 막스, 버핏, 린치, 윌리엄스, 여운봉 관점의 모듈을 켜고 끄기',
      '적립식은 언제와 얼마나를 각각 거장 자동 또는 내가 정하기',
      '여운봉 저수지 매매법: 전고점 대비 단계마다 나눠 사고 70:30으로 맞추기',
      '가상 1억 원 계좌와 레시피 비교 장부로 먼저 검증, 실계좌 2는 계획부터',
    ],
    link: { href: doc('longterm.md'), label: '장기투자 가이드' },
  },
]

export const TAGLINE = ['AI는 의견을 내고,', '규칙이 주문을 정하고,', '가드가 계좌를 지킵니다.']

export const FEATURES = [
  { icon: 'TrendDown', title: '낙폭과대 선별 v2', body: '지수가 크게 빠진 날 14:30부터 시장과 같이 빠진 종목을 나눠 사고, 오르면 나눠 팝니다.', href: doc('oversold.md') },
  { icon: 'Bank', title: '보호된 실계좌', body: '실주문 스위치, 하루 손실 2만 원 래치, 1주 가격 상한, 미체결 차단을 계좌마다 따로 겁니다.', href: doc('live-accounts.md') },
  { icon: 'Plant', title: '적립식과 여운봉 매매법', body: '들어오는 적립금은 적립식이, 운용금 매매는 계좌 전략이 맡아 서로 섞이지 않습니다.', href: doc('longterm-dca.md') },
  { icon: 'Notebook', title: '매매일지와 리포트', body: '체결, 수급, 이슈 별점, 종목 차트를 모아 하루·월·분기·년 단위 일지와 HTML 리포트를 만듭니다.', href: doc('journal.md') },
  { icon: 'BellRinging', title: 'Discord 알림', body: '체결과 손익은 바로, 리포트는 페이지가 열린 뒤 링크로 보냅니다. 같은 알림은 한 번만 갑니다.', href: doc('notifications.md') },
  { icon: 'ListChecks', title: '장전 점검과 장중 감사', body: '개장 전 서비스와 계좌를 점검하고, 장중에는 매시 판단과 차단 사유를 감사해 문제 이력을 남깁니다.', href: doc('automation.md') },
]

export const DAY = [
  { time: '08:20', title: '장전 사전 점검', body: '서비스, 계좌 목록, 미확인 주문, 설정 유효성을 확인합니다.' },
  { time: '08:31', title: '장전 상세 분석', body: '해외 시장, 야간선물, 공시를 모아 그날 판단 맥락을 만듭니다.' },
  { time: '08:45', title: '장기투자 판단', body: '국면을 정하고 거장 모듈로 그날 목표 비중과 주문 계획을 세웁니다.' },
  { time: '09:00', title: '개장과 동시에', body: '전날 든 포지션은 개장 판단을 받고, 새 진입은 완료 분봉 6개와 가드를 확인한 뒤 주문합니다.' },
  { time: '매시 20분', title: '장중 감사', body: '판단과 차단 사유, 체결 대조를 점검하고 재발한 문제를 바로 알립니다.' },
  { time: '14:40', title: '장 마감 정리', body: '정리 시각(기본값, 설정 가능)에 당일 포지션을 정리합니다. 선별 v2는 자기 규칙을 따릅니다.' },
  { time: '15:40', title: '마감 요약', body: '계좌별 손익과 잔고를 Discord로 보내고 장기 장부를 마감합니다.' },
  { time: '16:40', title: '매매일지', body: '그날 거래를 복기하고 개선안과 다음 날 엔진 참고 메모를 남깁니다.' },
]

export const SAFETY_STAGES = [
  { step: '1', title: '관찰', body: '실계좌는 계획만 기록하고 주문하지 않습니다.' },
  { step: '2', title: '가상 실행', body: '실시간 호가로 가상 체결 원장을 만들어 결과를 쌓습니다.' },
  { step: '3', title: '보호된 실주문', body: '실주문 스위치와 확인 문구, 실행 플래그가 모두 있어야 열립니다.' },
]

export const LIMITS = [
  ['공유 모의계좌', '회당 매수, 하루 매수, 보유 금액, 하루 새 종목, 하루 손실 한도를 대시보드에서 범위 안으로 설정'],
  ['실계좌', '운용금은 잔고 안에서, 하루 손실 계좌별 최대 2만 원, 1주 가격 상한, 하루 새 종목 5, 미체결·미확인 차단'],
  ['공통', '계좌 소유 확인, KRX 장 시간, 원장 대조 뒤에만 주문, STOP·HALT 파일로 즉시 중단'],
]

export const SCREENS = [
  { id: 'overview', label: '종합 현황', caption: '모든 계좌의 오늘 손익, 잔고, 실행기 상태를 한 화면에서 봅니다.' },
  { id: 'strategy-flow', label: '전략 구조', caption: '매수 판단부터 주문 가드까지 오늘 설정이 반영된 흐름도입니다.' },
  { id: 'trading', label: '트레이딩 현황', caption: '계좌를 골라 보유, 체결, 수동 매수와 매도 요청을 다룹니다.' },
  { id: 'journal', label: '매매일지', caption: '계좌별 성과, 시장과 수급, 핵심 이슈 별점을 카드로 모읍니다.' },
  { id: 'longterm', label: '장기투자', caption: '국면, 가상 계좌 순자산, 그림자 장부와 오늘의 판단을 봅니다.' },
  { id: 'longterm-chart', label: '장기 차트', caption: '이동평균선과 엔진 타점, 적립식·여운봉 매수 단계를 그립니다.' },
  { id: 'accounts', label: '계좌·자동매매', caption: '운영 엔진과 계좌별 운용 방식, 보호 한도를 정합니다.' },
  { id: 'api', label: 'API 연동', caption: '외부 API 호출과 실패, 지연을 10분 단위로 셉니다.' },
]

export const NUMBERS = [
  { value: '14', label: '대시보드 탭' },
  { value: '17', label: 'launchd 서비스' },
  { value: '8', label: '장기 거장 모듈' },
  { value: '6', label: '장기 국면' },
  { value: '1,998', label: '자동 테스트' },
]

export const PROMPT = `이 저장소의 docs/getting-started.md를 따라 Python 환경과 NH 공식 MCP를 설치하고,
.env.example을 복사해 .env를 만든 뒤 내가 넣을 키 항목을 알려줘. 키 값은 출력하지 마.
nhplug_readonly·nhplug_paper 연결과 ETF 시세 조회까지 확인하고, 서비스는 plist만 만들어 보여줘.
모의 주문 서비스 등록은 내가 승인한 뒤에 해줘.`

export const STEPS = [
  { title: '저장소 받기', body: 'Python 3.11, Node.js 22 이상과 NH 나무 PLUG 앱 키(모의투자 계좌 포함)를 준비합니다.', code: 'git clone https://github.com/roadkwonai/omni-ai-trading.git' },
  { title: '키 넣기', body: '.env.example을 .env로 복사하고 권한을 600으로 둡니다. 키와 계좌번호는 이 파일에만 둡니다.', code: 'cp .env.example .env && chmod 600 .env' },
  { title: 'AI에게 맡기기', body: 'Claude Code나 Codex에 아래 프롬프트를 주면 설치와 연결 확인을 진행하고, 주문 서비스는 승인을 기다립니다.', code: null },
]

export const FAQ = [
  { q: '수익이 나나요?', a: '보장하지 않습니다. 모의 성과와 가상 체결은 실제 체결이나 수익성 검증이 아니며, 이 프로젝트는 투자 조언이 아닙니다.' },
  { q: '설치하면 실계좌로 바로 주문하나요?', a: '아닙니다. 기본은 모의계좌입니다. 실계좌는 계좌별 실주문 스위치, 확인 문구, 실행 플래그가 모두 있어야 주문하며 그 전에는 계획만 기록합니다.' },
  { q: '어떤 증권사를 쓰나요?', a: 'NH투자증권 나무 PLUG OpenAPI와 공식 MCP를 씁니다. 키움과 토스 Open API는 순위와 시황의 대체 조회 출처로만 씁니다. NH투자증권과 제휴 관계가 없는 비공식 프로젝트입니다.' },
  { q: '키와 계좌번호는 어디에 저장되나요?', a: '내 컴퓨터의 .env 파일에만 둡니다. 화면, 기록, 리포트, Discord 알림은 계좌번호를 가리고, 저장소에는 키가 들어가지 않습니다.' },
  { q: 'Jev는 무엇인가요?', a: '차트 이미지와 뉴스로 매수, 대기, 매도 의견과 신뢰도를 내는 외부 판단 모델입니다. 의견은 참고 입력이고, 주문은 승인된 규칙과 가드가 정합니다.' },
  { q: '장기투자는 무엇을 사나요?', a: '국내에 상장된 미국 지수와 테마 ETF 6종(나스닥100, S&P500, 반도체, 양자, 우주, 저수지 역할의 KOFR)입니다. 여운봉 매매법은 나스닥100과 채권 혼합 ETF를 씁니다.' },
  { q: 'macOS가 아니어도 되나요?', a: '서비스 등록은 macOS launchd 기준입니다. 실행기와 대시보드는 Python이라 다른 환경에서도 돌릴 수 있지만 일정 등록은 직접 바꿔야 합니다.' },
  { q: '원본 Vibe-Trading과 어떤 관계인가요?', a: 'HKUDS의 Vibe-Trading(MIT) 위에 국내 시장 자동매매와 장기투자를 더한 확장입니다. 원본의 라이선스와 NOTICE를 그대로 지킵니다.' },
]
