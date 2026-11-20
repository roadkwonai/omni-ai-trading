import { ChartCard, ThemeProvider, Timeline } from 'ztd-charts'

// ztd-charts reads its colors from --ztd-* variables; point them at the landing tokens so light, dark and the theme button follow.
const THEME = {
  bg: 'var(--bg)', panel: 'var(--bg)', ink: 'var(--text)', muted: 'var(--muted)', dim: 'var(--soft)',
  accent: 'var(--accent)', accentText: 'var(--accent-text)', onAccent: 'var(--on-accent)', onInk: 'var(--bg)',
  line: 'var(--line)', line2: 'var(--line-soft)', neutral: 'var(--chart-neutral)', neutralStrong: 'var(--muted)',
  onNeutralStrong: 'var(--bg)', neutralSoft: 'var(--surface-2)', lift: 'var(--surface)', tint: 'var(--accent-tint)',
  fontFamily: 'inherit', radius: 16, radiusMark: 6,
}

function Charts({ children }: { children: React.ReactNode }) {
  return <ThemeProvider theme={THEME} locale="ko-KR">{children}</ThemeProvider>
}

// Weekday schedule in Asia/Seoul (launchd services and scheduled tasks; default settings).
const at = (hm: string) => `2026-11-20T${hm}`
const point = (label: string, hm: string, group: string) => ({ label, start: at(hm), end: at(hm), group })
const SCHEDULE = [
  { label: '장전 자료 수집', start: at('08:00'), end: at('08:55'), group: '장전' },
  point('사전 점검', '08:20', '장전'), point('사전 점검', '08:53', '장전'),
  point('장전 상세 분석', '08:31', '장전'),
  point('장기 판단·리포트', '08:45', '장기투자'), point('장기 판단·리포트', '08:50', '장기투자'),
  point('Jev 실행기 준비', '08:55', '장전'),
  { label: 'Jev 실시간 트레이딩', start: at('09:00'), end: at('15:00'), group: '장중' },
  ...['09:20', '10:20', '11:20', '12:20', '13:20', '14:20', '15:20'].map((t) => point('장중 감사', t, '장중')),
  { label: '장기 계좌 주문 창', start: at('09:40'), end: at('14:30'), group: '장기투자' },
  point('마감 요약', '15:40', '마감'), point('개선안·매매일지', '16:15', '마감'), point('개선안·매매일지', '16:40', '마감'),
  point('장기 복기·감사', '16:45', '장기투자'),
]

export function ScheduleChart() {
  return (
    <Charts>
      <ChartCard title="평일 자동 일정" subtitle="한국 시간, 기본 설정 기준. 점은 한 번 도는 작업입니다" accent="Jev"
        takeaway="Jev는 개장부터 자료와 진입 조건을 확인합니다. 장중 감사는 매시 20분에 돌아갑니다.">
        <Timeline data={SCHEDULE} highlight="Jev 실시간 트레이딩" height={330} xType="date"
          referenceLines={[{ x: at('09:00'), label: '개장' }, { x: at('15:30'), label: '장 마감' }]}
          ariaLabel="평일 자동 일정 타임라인" />
      </ChartCard>
    </Charts>
  )
}
