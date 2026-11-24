import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import { Tabs, TabsIndicator, TabsList, TabsPanel, TabsTab } from '@ui-sdk/react/tabs'
import { Dialog, DialogBackdrop, DialogClose, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from '@ui-sdk/react/dialog'
import { Accordion, AccordionHeader, AccordionItem, AccordionPanel, AccordionTrigger } from '@ui-sdk/react/accordion'
import {
  ArrowDown, ArrowRight, ArrowUpRight, Bank, BellRinging, CaretDown, Check, Copy, GithubLogo, List, ListChecks, MagnifyingGlassPlus,
  ArrowsClockwise, CalendarCheck, ChatsCircle, Download, ClipboardText, Compass, Lightning, LockKey, Monitor, PiggyBank, Play, PlugsConnected, Moon, Notebook, Plant, Robot, Scales, ShieldCheck, Stack, Sun, TerminalWindow, TrendDown, UserCircle, Warning, X,
} from '@phosphor-icons/react'
import {
  doc, FAQ, FEATURES, LIMITS, NAV, PROMPT, REPO, SAFETY_STAGES, SCREENS, START, STEPS, TAGLINE, VERSION,
  AI_LOOP, AI_PAID, CLI_FLOW, CLI_FROM, CLI_HUB, CLI_OPEN, CLI_SCENES, CLI_START, CLI_TO, AI_SEARCH_EXAMPLES, ASK_RULES, ASK_SCENES, ASK_WAYS, DECISIONS, EXTEND_CHECK, EXTEND_MOVE, EXTEND_NEXT, EXTEND_STEPS, FIRST_BUSINESS, FIX_CASE, HOLDING_LOSS, HORIZONS, LEARN_STEPS, SEARCH_STEPS, SEQUENCE, ARCH_LAYERS, CHAT, DAILY, DCA_AXES, DCA_PROFILES, DISCLAIMER_FULL, DISCLAIMER_SHORT, HIGHLIGHTS, INTEGRATIONS,
  JEV_INPUTS, JEV_TRUST, JEV_USES, LONGTERM_FLOW, LONGTERM_POINTS, MASTERS, PILLARS, REGIMES, RETIREMENT_POVERTY, TRAPS, WHY_CASES, WHY_STATS, BUY_CRAFT, SELL_ART, LIFECYCLE, LIFECYCLE_EXITS, REGIME_PLAYS, STYLES, GROW, DATA_JSON, DATA_SQLITE, CALIBRATION, USE_STEPS, SCHEDULE_CHIPS, AI_SKILLS, UPSTREAM, STACK, VIDEOS,
} from './content'
import { useNavState, useReveal, useTaglineWords } from './motion'
// Charts load in their own chunk; the placeholder keeps the height so nothing jumps.
const ScheduleChart = lazy(() => import('./Charts').then((m) => ({ default: m.ScheduleChart })))
const chart = (el: React.ReactNode, h: number) => <Suspense fallback={<div className="chart-slot" style={{ height: h }} />}>{el}</Suspense>

const ICONS = { TerminalWindow, TrendDown, Bank, Plant, Notebook, BellRinging, ListChecks, Scales, Stack, ShieldCheck, ArrowsClockwise, ChatsCircle, LockKey,
  Lightning, Compass, PiggyBank, ClipboardText, PlugsConnected, Download, CalendarCheck, Robot, Monitor, MagnifyingGlassPlus } as const
const img = (name: string) => `./img/${name}.webp`
const shot = (id: string) => `./img/screens/${id}.webp`
const css = (vars: Record<string, string | number>) => vars as React.CSSProperties
// Inline emphasis in content strings: **bold** for key terms, ==highlight== for the one phrase that matters most.
const rich = (t: string) => t.split(/(\*\*[^*]+\*\*|==[^=]+==)/g).map((p, i) =>
  p.startsWith('**') ? <strong key={i} className="em">{p.slice(2, -2)}</strong>
    : p.startsWith('==') ? <mark key={i} className="hl">{p.slice(2, -2)}</mark> : p)

type Theme = 'system' | 'light' | 'dark'
const THEME_KEY = 'omni-report-theme' // shared with the report pages on the same site
const THEME_COLOR = { light: '#ffffff', dark: '#131209' }
const THEME_NAME: Record<Theme, string> = { system: '시스템', light: '라이트', dark: '다크' }
const THEME_TO: Record<Theme, string> = { system: '시스템으로', light: '라이트로', dark: '다크로' }
const THEME_NEXT: Record<Theme, Theme> = { system: 'light', light: 'dark', dark: 'system' }

function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => {
    try { return (localStorage.getItem(THEME_KEY) as Theme) || 'system' } catch { return 'system' }
  })
  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') root.removeAttribute('data-theme'); else root.setAttribute('data-theme', theme)
    try { theme === 'system' ? localStorage.removeItem(THEME_KEY) : localStorage.setItem(THEME_KEY, theme) } catch { /* private mode */ }
    // the browser bar follows the chosen theme; 'system' restores the per-scheme values from index.html
    document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]').forEach((m) => {
      m.content = theme === 'system' ? m.dataset.scheme ?? m.content : THEME_COLOR[theme]
    })
  }, [theme])
  const next = () => {
    const go = () => flushSync(() => setTheme((t) => THEME_NEXT[t]))
    // short crossfade where View Transitions exist (M10); reduced motion switches instantly
    if (document.startViewTransition && !matchMedia('(prefers-reduced-motion: reduce)').matches) document.startViewTransition(go)
    else go()
  }
  return { theme, next }
}

function ThemeButton() {
  const { theme, next } = useTheme()
  const label = `화면 테마: ${THEME_NAME[theme]}. 누르면 ${THEME_TO[THEME_NEXT[theme]]} 바뀝니다`
  return (
    <button type="button" className="theme-btn button-sm" onClick={next} aria-label={label} title={label}>
      <span className="theme-icon" aria-hidden>
        <Monitor size={16} data-on={theme === 'system' || undefined} />
        <Sun size={16} data-on={theme === 'light' || undefined} />
        <Moon size={16} data-on={theme === 'dark' || undefined} />
      </span>
      <span className="theme-label" aria-hidden>{THEME_NAME[theme]}</span>
    </button>
  )
}

const NAV_IDS = NAV.map((n) => n.href.slice(1))

function Nav() {
  const [open, setOpen] = useState(false)
  const { scrolled, current } = useNavState(NAV_IDS)
  const menuRef = useRef<HTMLDivElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    document.body.toggleAttribute('data-menu-open', open)
    if (!open) return
    const menu = menuRef.current!
    menu.querySelector<HTMLElement>('a')?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setOpen(false); btnRef.current?.focus(); return }
      if (e.key !== 'Tab') return
      // keep focus inside the open menu and its toggle button
      const stops = [btnRef.current!, ...menu.querySelectorAll<HTMLElement>('a')]
      const i = stops.indexOf(document.activeElement as HTMLElement)
      const to = e.shiftKey ? (i <= 0 ? stops.length - 1 : i - 1) : (i === stops.length - 1 ? 0 : i + 1)
      e.preventDefault(); stops[to].focus()
    }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  }, [open])
  return (
    <header className="nav-wrap">
      <nav className="nav" aria-label="주 메뉴" data-scrolled={scrolled || undefined}>
        <a className="brand" href="#top" aria-label="Omni AI Trading 처음으로">
          <img src="./img/logo-64.png" alt="" width="24" height="24" />
          Omni AI Trading
        </a>
        <ul className="nav-links">
          {NAV.map((n) => <li key={n.href}><a href={n.href} aria-current={current === n.href.slice(1) ? 'true' : undefined}>{n.label}</a></li>)}
        </ul>
        <div className="nav-tools">
          <ThemeButton />
          <a className="btn btn-primary btn-sm button-sm nav-cta" href={START}>GitHub에서 시작하기</a>
          <button ref={btnRef} type="button" className="icon-btn menu-btn" aria-expanded={open} aria-controls="mobile-menu"
            aria-label={open ? '메뉴 닫기' : '메뉴 열기'} onClick={() => setOpen((o) => !o)}>
            <span className="menu-icon" data-open={open || undefined} aria-hidden><List size={20} /><X size={20} /></span>
          </button>
        </div>
      </nav>
      <div id="mobile-menu" ref={menuRef} className="mobile-menu" data-open={open || undefined} inert={!open}
        role="dialog" aria-modal={open || undefined} aria-label="메뉴">
        <ul>
          {NAV.map((n, i) => (
            <li key={n.href} style={css({ '--i': i })}>
              <a href={n.href} onClick={() => setOpen(false)}>{n.label}</a>
            </li>
          ))}
          <li style={css({ '--i': NAV.length })}>
            <a className="btn btn-primary button-md" href={START}>GitHub에서 시작하기</a>
          </li>
        </ul>
      </div>
    </header>
  )
}

function Hero() {
  return (
    <section className="hero" aria-labelledby="hero-title">
      <div className="container hero-inner">
        <p className="kicker hero-step" style={css({ '--d': 0 })}>오픈소스 AI 투자 도구</p>
        <h1 id="hero-title" className="heading-1 hero-title hero-step" style={css({ '--d': 1 })}>
          실시간 매매부터 장기투자까지<br />AI와 함께 배우고 투자해 보세요
        </h1>
        <p className="lead hero-sub hero-step" style={css({ '--d': 2 })}>
          Jev는 차트·뉴스·수급을 보고 <strong className="em">매수할지, 지금은 기다릴지</strong> 판단합니다. 그 근거는 모의매매 기록에서 확인할 수 있습니다.
          {' '}장기투자에서는 거장의 원칙을 참고해 <strong className="em">ETF 적립과 비중 조정 계획</strong>을 세웁니다. 가상으로 체결한 결과를 장부에 기록해 전략별로 비교합니다.
        </p>
        <div className="hero-cta hero-step" style={css({ '--d': 3 })}>
          <a className="btn btn-primary button-md" href="#jev"><Lightning size={18} aria-hidden />장중 모의매매 살펴보기</a>
          <a className="btn btn-ghost button-md" href="#longterm"><Compass size={18} aria-hidden />장기투자 살펴보기</a>
        </div>
        <p className="proof small hero-step" style={css({ '--d': 3 })}>
          <ShieldCheck size={16} aria-hidden /><span>모의매매와 가상 체결로 먼저 확인</span><span>실주문에는 별도 승인과 주문 보호 조건 적용</span>
        </p>
        <p className="hero-notice hero-step" style={css({ '--d': 4 })}>
          <Warning size={16} aria-hidden />
          <span>
            투자 권유가 아니며 손익 책임은 투자자 본인에게 있습니다. Claude Code·Codex 같은 AI 도구의 유료 요금제가 필요합니다.{' '}
            <a href="#disclaimer">투자 유의사항</a>
          </span>
        </p>
      </div>
      <div className="container hero-visual hero-step" style={css({ '--d': 5 })}>
        <figure className="browser">
          <div className="browser-bar" aria-hidden><span /><span /><span /></div>
          <img src={shot('overview')} width="1600" height="1000"
            alt="Omni AI Trading 대시보드 종합 현황 화면. 오늘의 계좌별 실현손익, 잔고, 실행기 상태가 보입니다." />
        </figure>
        <img className="hero-mascot" src={img('mascot-wave')} alt="" width="360" height="360" />
      </div>
      <nav className="container pillars-wrap" aria-label="주요 기능">
        <p className="caption muted pillars-title">주요 기능</p>
        <ol className="pillars">
          {PILLARS.map((p, i) => {
            const Icon = ICONS[p.icon as keyof typeof ICONS]
            return (
              <li key={p.href}>
                <a className="pillar" href={p.href}>
                  <span className="pillar-icon"><Icon size={18} aria-hidden /></span>
                  <span className="pillar-no" aria-hidden>{String(i + 1).padStart(2, '0')}</span>
                  <strong className="title-3">{p.label}</strong>
                  <span className="caption muted">{p.body}</span>
                  <ArrowDown className="pillar-go" size={14} aria-hidden />
                </a>
              </li>
            )
          })}
        </ol>
      </nav>
    </section>
  )
}

const mmss = (t: number) => `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`

// Click-to-load: the YouTube iframe (and its cookies/scripts) loads only after the visitor presses play.
function Video() {
  const [playing, setPlaying] = useState<{ id: string; start: number } | null>(null) // one video at a time
  return (
    <section id="video" className="section video" aria-labelledby="video-title">
      <div className="container">
        <div className="head">
          <p className="kicker">서비스 소개 영상 {VIDEOS.length}편</p>
          <h2 id="video-title" className="heading-2">실제 화면으로 보는 <span className="accent-text">Omni AI Trading</span></h2>
          <p className="lead">노후를 위한 장기투자와 장중 트레이딩을 나눠 실제 대시보드로 보여 드립니다.</p>
        </div>
        <div className="video-grid">
          {VIDEOS.map((v) => (
            <article key={v.id} className="video-item" data-reveal>
              <div className="video-frame">
                {playing?.id === v.id ? (
                  <iframe src={`https://www.youtube-nocookie.com/embed/${v.id}?autoplay=1&rel=0&start=${playing.start}`}
                    title={`Omni AI Trading 소개 영상 · ${v.title}`} allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
                    allowFullScreen referrerPolicy="strict-origin-when-cross-origin" />
                ) : (
                  <button type="button" className="video-poster" onClick={() => setPlaying({ id: v.id, start: 0 })} aria-label={`${v.title} 영상 재생 (${v.length})`}>
                    <img src={v.thumb} width="1280" height="720" loading="lazy" alt={v.alt} />
                    <span className="video-play" aria-hidden><Play size={26} weight="fill" /></span>
                    <span className="video-len caption" aria-hidden>{v.length.replace(/(\d+)분 (\d+)초/, (_, m, s) => `${m}:${s.padStart(2, '0')}`)}</span>
                  </button>
                )}
              </div>
              <h3 className="video-name">{v.title} <span className="caption">{v.length}</span></h3>
              <p className="video-text">{v.text}</p>
              <ol className="video-chapters">
                {v.chapters.map((c) => (
                  <li key={c.t}>
                    <button type="button" className="chip-btn small" onClick={() => setPlaying({ id: v.id, start: c.t })}>
                      <span className="video-t">{mmss(c.t)}</span>{c.label}
                    </button>
                  </li>
                ))}
              </ol>
              <p className="more-row" style={{ marginTop: 20 }}>
                <a className="more button-md" href={v.url} target="_blank" rel="noopener noreferrer" aria-label={`${v.title} YouTube에서 보기`}>YouTube에서 보기 <ArrowUpRight size={16} aria-hidden /></a>
              </p>
            </article>
          ))}
        </div>
      </div>
    </section>
  )
}

// Horizontal bars as plain HTML: the value is always written next to the bar, so the bar itself is decoration.
function Bars({ rows, max }: { rows: { label: string; value: number; text: string; muted?: boolean }[]; max: number }) {
  return (
    <ul className="bars">
      {rows.map((r) => (
        <li key={r.label} data-muted={r.muted || undefined}>
          <span>{r.label}</span>
          <strong className="tabular">{r.text}</strong>
          <div className="poverty-track" aria-hidden="true"><span style={{ width: `${r.value / max * 100}%` }} /></div>
        </li>
      ))}
    </ul>
  )
}

function Why() {
  return (
    <section id="why" className="section band why" aria-labelledby="why-title">
      <div className="container">
        <div className="head">
          <h2 id="why-title" className="heading-2">투자가 처음이어도 노후 준비를 시작할 수 있도록</h2>
          <p className="lead">
            처음 투자할 때는 어떤 정보를 봐야 할지 막막하고, 본업을 하면서 시장을 꾸준히 살피기도 어렵습니다.
            Omni는 누구나 시장을 읽는 법을 익히고, 장기투자 자금을 적립하며 자산 비중을 관리해
            더 여유로운 노후를 준비하도록 돕습니다.
          </p>
        </div>
        <div className="decisions-head">
          <h3 className="heading-3">한 번 내리면 되돌리기 어려운 결정</h3>
          <p className="body muted">퇴직할 무렵에는 큰돈이 걸린 결정이 겹칩니다. 잘못 고른 사람을 탓하기에 앞서, <strong className="em">미리 확인하고 작게 시험하고 돈을 나눠 둘 시간과 도구</strong>가 있어야 합니다.</p>
        </div>
        <ul className="decisions">
          {DECISIONS.map((d) => (
            <li key={d.title} className="why-stat">
              <p className="kicker">{d.kind}</p>
              <h4 className="heading-4">{d.title}</h4>
              <strong className="why-num tabular">{d.num}</strong>
              <p className="small muted">{d.body}</p>
              <a className="caption why-src" href={d.href} target="_blank" rel="noopener noreferrer">{d.source} <ArrowUpRight size={12} aria-hidden /></a>
              <p className="small trap-fix"><ShieldCheck size={16} aria-hidden /><span><strong className="em">{d.does}</strong> {d.not}</span></p>
            </li>
          ))}
        </ul>
        <div className="retirement-grid">
          <img className="retirement-photo" src={img('pause-and-check')} alt="휴대전화를 내려놓고 노트를 보며 함께 확인하는 어머니와 딸" width="1120" height="840" loading="lazy" />
          <figure className="why-stat poverty-chart" aria-labelledby="business-title">
            <figcaption id="business-title" className="heading-3">60대의 첫 창업, 그만둔 뒤가 더 어렵습니다</figcaption>
            <dl className="facts">
              {FIRST_BUSINESS.map((f) => <div key={f.num}><dt className="why-num tabular">{f.num}</dt><dd className="small muted">{f.label}</dd></div>)}
            </dl>
            <p className="small">그만둔 뒤는 달랐습니다. 20~50대는 절반 넘게 상용직 일자리를 얻었지만 <strong className="em">60대는 상당수가 임시·일용직에 그치거나 일을 그만뒀습니다.</strong></p>
            <p className="caption muted">이 자료는 창업 자금이 퇴직금이었는지 조사하지 않았습니다. 앞의 두 값은 2022년 조사 결과이고, 31%와 이후 일자리는 한국노동패널조사 결과입니다.</p>
            <a className="caption why-src" href={DECISIONS[1].href} target="_blank" rel="noopener noreferrer">{DECISIONS[1].source} <ArrowUpRight size={12} aria-hidden /></a>
          </figure>
        </div>
        <div className="retirement-grid">
          <figure className="why-stat poverty-chart" aria-labelledby="poverty-title">
            <figcaption id="poverty-title" className="heading-3">고령층의 상대적 소득 빈곤율 <span className="small muted nowrap">2022년</span></figcaption>
            {RETIREMENT_POVERTY.groups.map((g) => (
              <div key={g.label} className="poverty-group">
                <p className="title-3">{g.label}</p>
                <Bars max={Math.max(...RETIREMENT_POVERTY.scale)} rows={[
                  { label: '한국', value: g.korea, text: `${g.korea.toFixed(1)}%` },
                  { label: 'OECD 평균', value: g.oecd, text: `${g.oecd.toFixed(1)}%`, muted: true },
                ]} />
              </div>
            ))}
            <div className="poverty-scale caption muted tabular" aria-hidden="true">
              {RETIREMENT_POVERTY.scale.map((tick) => <span key={tick}>{tick}%</span>)}
            </div>
            <p className="small muted">가구 규모를 반영한 가처분소득이 각 나라 중위소득의 절반보다 적은 사람의 비율입니다.</p>
            <a className="caption why-src" href={RETIREMENT_POVERTY.source} target="_blank" rel="noopener noreferrer">OECD 연금 지표 2025 · 표 7.2 <ArrowUpRight size={12} aria-hidden /></a>
          </figure>
          <img className="retirement-photo" src={img('retirement-planning')} alt="식탁에 앉아 노트에 함께 계획을 적는 두 사람" width="1120" height="840" loading="lazy" />
        </div>
        <ul className="why-stats">
          {WHY_STATS.map((s) => (
            <li key={s.title} className="why-stat">
              <strong className="why-num tabular">{s.num}</strong>
              <h3 className="title-1">{s.title}</h3>
              <p className="small muted">{s.label}</p>
              <a className="caption why-src" href={s.href} target="_blank" rel="noopener noreferrer">{s.source}<ArrowUpRight size={12} aria-hidden /></a>
            </li>
          ))}
        </ul>
        <h3 className="heading-3 traps-title">개인 투자자가 어려움을 겪는 순간</h3>
        <ul className="traps">
          {TRAPS.map((t) => (
            <li key={t.trap} className="trap">
              <h4 className="title-2">{t.trap}</h4>
              <p className="small muted">{t.human}</p>
              <p className="small trap-fix"><ShieldCheck size={16} aria-hidden /><span>{t.fix}</span></p>
            </li>
          ))}
        </ul>
        <ul className="why-cases single">
          {WHY_CASES.map((item) => (
            <li key={item.title} className="why-stat">
              <p className="kicker">{item.kind}</p>
              <h3 className="heading-4">{item.title}</h3>
              <p className="small muted">{item.body}</p>
              <a className="caption why-src" href={item.href} target="_blank" rel="noopener noreferrer">{item.label}<ArrowUpRight size={12} aria-hidden /></a>
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}

function Principle() {
  useTaglineWords('tagline')
  let n = 0
  return (
    <section id="highlights" className="section" aria-labelledby="highlights-title">
      <div className="container">
        <h2 id="highlights-title" className="sr-only">핵심 원칙과 특징</h2>
        <div className="principle-head">
          <div>
            <p className="kicker">핵심 원칙</p>
            <p id="tagline" className="heading-2 tagline-text" style={{ marginTop: 16 }}>
              {TAGLINE.map((line) => (
                <span key={line} className="tagline-line">
                  {line.split(' ').map((w) => <span key={`${w}-${n}`} data-word style={css({ '--w': n++ })}>{w} </span>)}
                </span>
              ))}
            </p>
          </div>
          <img className="art art-lg" src={img('mascot-principle')} alt="" width="360" height="360" loading="lazy" />
        </div>
        <ul className="features" data-reveal>
          {HIGHLIGHTS.map((h) => {
            const Icon = ICONS[h.icon as keyof typeof ICONS]
            return (
              <li key={h.title} className="feature">
                <span className="icon-chip" aria-hidden><Icon size={20} /></span>
                <h3 className="title-1">{h.title}</h3>
                <p className="small muted">{h.body}</p>
                <span className="tag">{h.proof}</span>
              </li>
            )
          })}
        </ul>
      </div>
    </section>
  )
}

// Decorative 1-minute candles for the pipeline's first stage (illustration, not market data).
const CANDLES = [[30, 22, 36, 18], [22, 26, 30, 20], [26, 20, 28, 16], [20, 28, 32, 18], [28, 34, 38, 26], [34, 30, 38, 28], [30, 38, 42, 28],
  [38, 44, 48, 36], [44, 40, 46, 36], [40, 48, 52, 38], [48, 54, 58, 46], [54, 50, 56, 46]]
function Candles() {
  return (
    <svg className="candles" viewBox="0 0 200 72" role="img" aria-label="1분 캔들 차트 예시 그림">
      {CANDLES.map(([o, c, h, l], i) => {
        const x = 12 + i * 15.5, y = (v: number) => 66 - (v - 14) * 1.35, up = c >= o
        return (
          <g key={i} stroke={up ? 'var(--up)' : 'var(--down)'} fill={up ? 'var(--up)' : 'var(--down)'}>
            <line x1={x} x2={x} y1={y(h)} y2={y(l)} strokeWidth="1" />
            <rect x={x - 3.5} width="7" y={y(Math.max(o, c))} height={Math.max(2, Math.abs(c - o) * 1.35)} rx="1" />
          </g>
        )
      })}
    </svg>
  )
}

function Art() {
  return (
    <section id="art" className="section" aria-labelledby="art-title">
      <div className="container">
        <div className="art-head">
          <p className="kicker">투자 기법 · 오래된 증시 격언</p>
          <h2 id="art-title" className="art-title">매수는 기술,<br /><span className="accent-text">매도는 예술</span></h2>
          <p className="lead">
            사는 이유는 차트와 수급으로 설명할 수 있지만, 파는 순간에는 욕심과 공포가 끼어듭니다. 고점은 아무도 모르니까요.
            Omni는 매도할 때 <mark className="hl">미리 정한 기준</mark>을 따릅니다.
          </p>
          <img className="art-visual" src={img('mascot-art')} alt="" width="1200" height="800" loading="lazy" />
        </div>
        <div className="craft" data-reveal>
          <div className="craft-col">
            <h3 className="heading-4">매수 · 기술</h3>
            <ol className="craft-list">
              {BUY_CRAFT.map((x, i) => (
                <li key={x.title}><span className="craft-no" aria-hidden>{i + 1}</span><div><strong className="title-2">{x.title}</strong><p className="small muted">{rich(x.body)}</p></div></li>
              ))}
            </ol>
          </div>
          <div className="craft-col craft-sell">
            <h3 className="heading-4">매도 · <span className="accent-text">팔 시점을 정하는 기준</span></h3>
            <ol className="craft-list">
              {SELL_ART.map((x, i) => (
                <li key={x.title}><span className="craft-no" aria-hidden>{i + 1}</span><div><strong className="title-2">{x.title}</strong><p className="small muted">{rich(x.body)}</p></div></li>
              ))}
            </ol>
          </div>
        </div>
        <figure className="life" data-reveal>
          <figcaption className="title-1">매수부터 보유·매도까지</figcaption>
          <ol className="life-steps" aria-label="첫 매수부터 여러 날 보유까지 다섯 단계">
            {LIFECYCLE.map((x, i) => (
              <li key={x.step} className="life-step" style={css({ '--i': i })}>
                <strong className="title-2">{x.step}</strong>
                <span className="caption muted">{x.body}</span>
              </li>
            ))}
          </ol>
          <div className="life-exits">
            <span className="caption muted">어느 단계에서든 아래 매도 조건이 충족되면 그 기준에 따라 팝니다.</span>
            <ul>{LIFECYCLE_EXITS.map((e) => <li key={e} className={/손절|손실 한도/.test(e) ? 'tag tag-warn' : 'tag'}>{e}</li>)}</ul>
          </div>
        </figure>
        <p className="why-close heading-4">조건을 확인해 나눠 사고, 손절가에 닿으면 팝니다.<br /><span className="accent-text">수익을 내고 있으면 고점에서 얼마나 떨어졌는지 확인해 팔 시점을 정합니다.</span></p>
        <p className="more-row"><a className="more button-sm" href={doc('strategy.md')}>매수·매도 규칙 전체 보기 <ArrowUpRight size={16} aria-hidden /></a></p>
      </div>
    </section>
  )
}

const TONE_ICON = { up: '▲', wait: '■', down: '▼' } as const

function Jev() {
  return (
    <section id="jev" className="section band" aria-labelledby="jev-title">
      <div className="container">
        <div className="head-split">
          <div className="head">
            <p className="kicker">Jev 실시간 AI 트레이더</p>
            <h2 id="jev-title" className="heading-2">수급과 시황으로 주도주를 찾고, 1분마다 판단합니다</h2>
            <p className="lead">
              Jev는 차트 이미지를 읽고 정해진 선택지에서 답을 고르는 TypeSafe 모델입니다. 장중 1분마다 <mark className="hl">거래가 몰리는 종목과 그 배경</mark>을 살펴 국내 ETF와 주식 중 매수 후보 한 종목이나 대기를 고릅니다. 판단의 신뢰도도 함께 알려 줍니다.
              보유 종목을 계속 들고 갈지, 당일에 판 종목을 다시 살지 판단합니다. 전 거래일부터 보유한 종목은 개장 직후 다시 살피고, 관심종목과 급락 종목도 검토합니다.
            </p>
          </div>
          <img className="art art-md" src={img('mascot-jev')} alt="" width="280" height="280" loading="lazy" />
        </div>
        <ol className="pipe" data-play aria-label="Jev 판단에서 주문까지 네 단계">
          <li className="pipe-stage" style={css({ '--s': 0 })}>
            <span className="pipe-no">01 · 입력</span>
            <h3 className="title-1">Jev가 보는 것</h3>
            <Candles />
            <ul className="inputs small">
              {JEV_INPUTS.map((x) => <li key={x.label} title={x.detail}>{x.label}</li>)}
            </ul>
          </li>
          <li className="pipe-stage" style={css({ '--s': 1 })}>
            <span className="pipe-no">02 · 판단</span>
            <h3 className="title-1">Jev가 답하는 것</h3>
            <div className="signals">
              <span className="signal up" style={css({ '--i': 0 })}>▲ 매수 · 종목 하나</span>
              <span className="signal wait" style={css({ '--i': 1 })}>■ 대기</span>
              <span className="signal down" style={css({ '--i': 2 })}>▼ 매도 · 일부 매도</span>
            </div>
            <p className="small muted">신뢰도와 함께 판단 근거를 기록합니다. Jev는 결정을 좌우한 요인과 자료가 충분했는지 등 7가지 질문에 답합니다.</p>
            <span className="tag">신뢰도는 수익 확률이 아닙니다</span>
          </li>
          <li className="pipe-stage focal" style={css({ '--s': 2 })}>
            <span className="pipe-no">03 · 규칙</span>
            <h3 className="title-1">규칙이 정하는 것</h3>
            <ul className="gate small">
              <li><Check size={14} aria-hidden />신규 매수 공통 조건</li>
              <li><Check size={14} aria-hidden />자료 갱신 여부, 분봉 상승, 지수 방향</li>
              <li><Check size={14} aria-hidden />재매수·인버스 조건</li>
              <li><Check size={14} aria-hidden />매수 금액·수량 계산</li>
              <li><Check size={14} aria-hidden />주문 보호 한도</li>
            </ul>
          </li>
          <li className="pipe-stage" style={css({ '--s': 3 })}>
            <span className="pipe-no">04 · 실행</span>
            <h3 className="title-1">주문과 기록</h3>
            <p className="small muted">실행기는 모든 주문 조건을 충족할 때 주문을 보냅니다. 판단 근거, 주문을 막은 이유, 체결 내역을 기록합니다.</p>
          </li>
        </ol>
        <div className="cols-2" data-reveal>
          <div>
            <h3 className="heading-3">여섯 가지 판단을 맡습니다</h3>
            <ul className="rows">
              {JEV_USES.map((u) => <li key={u.title}><strong className="title-2">{u.title}</strong><span className="small muted">{u.body}</span></li>)}
            </ul>
          </div>
          <div>
            <h3 className="heading-3">Jev의 판단을 확인하는 방법</h3>
            <ul className="rows">
              {JEV_TRUST.map((t) => <li key={t.title}><strong className="title-2">{t.title}</strong><span className="small muted">{t.body}</span></li>)}
            </ul>
          </div>
        </div>
        <h3 className="heading-3 regime-title">시황에 따라 다르게 움직입니다</h3>
        <ul className="regime-plays" data-reveal>
          {REGIME_PLAYS.map((r) => (
            <li key={r.name} className={`regime-play ${r.tone}`}>
              <strong className="title-1"><span aria-hidden>{TONE_ICON[r.tone as keyof typeof TONE_ICON]}</span> {r.name}</strong>
              <p className="small muted">{rich(r.body)}</p>
            </li>
          ))}
        </ul>
        <p className="small muted regime-note">Jev는 최근 90분 뉴스와 해외 선물·원자재·환율 자료를 보고 지정학 이슈와 해외 시장 상황을 판단에 반영합니다.</p>
        <p className="more-row">
          <a className="more button-sm" href={`${doc('getting-started.md')}#7-첫-모의-회차-확인`}>첫 모의매매 확인 가이드 <ArrowUpRight size={16} aria-hidden /></a>
          <a className="more button-sm" href={doc('strategy.md')}>매매 전략 가이드 <ArrowUpRight size={16} aria-hidden /></a>
        </p>
      </div>
    </section>
  )
}

const HEAT = [100, 80, 60, 40, 30, 10] // risk order of REGIMES, highest first
const masterName = Object.fromEntries(MASTERS.map((m) => [m.id, m.name]))

function Longterm() {
  const [pick, setPick] = useState(0)
  const regime = REGIMES[pick]
  return (
    <section id="longterm" className="section" aria-labelledby="longterm-title">
      <div className="container">
        <div className="head-split">
          <div className="head">
            <p className="kicker">거장의 안목, 장기투자</p>
            <h2 id="longterm-title" className="heading-2">시장에 맞춰 적립하고 목표 비중을 관리합니다</h2>
            <p className="lead">
              하락장에 얼마나 더 모을지, 오른 자산을 언제 줄일지 기준을 미리 정합니다. 달리오의 비중 관리와 여운봉 저수지 매매법처럼 공개된 투자 원칙을 규칙으로 옮겼습니다.
              여운봉 매매법에서 ‘저수지’는 시장이 하락할 때 나눠 사려고 미리 모아 둔 자금입니다. 장기투자 엔진은 나스닥100 낙폭, 200일선, 변동성 지표를 살피고 선택한 계좌 전략에 따라
              국내 상장 미국 ETF의 매수·대기·비중 조정 계획을 세웁니다.
            </p>
          </div>
          <img className="art art-md" src={img('mascot-masters')} alt="" width="280" height="280" loading="lazy" />
        </div>
        <figure className="longterm-flow" aria-labelledby="longterm-flow-title">
          <figcaption id="longterm-flow-title" className="heading-3">적립 계획부터 결과 확인까지</figcaption>
          <img src={img('longterm-workflow')} alt="" width="1200" height="383" loading="lazy" />
          <ol>
            {LONGTERM_FLOW.map((step) => (
              <li key={step.title}>
                <h3 className="title-1">{step.title}</h3>
                <p className="small muted">{step.body}</p>
              </li>
            ))}
          </ol>
        </figure>
        <div className="horizon">
          <img className="horizon-art" src={img('mascot-envelopes')} alt="" width="640" height="724" loading="lazy" />
          <div>
            <h3 className="heading-3">언제 쓸 돈인지부터 나눕니다</h3>
            <ol className="horizons">
              {HORIZONS.map((h) => (
                <li key={h.when} data-on={h.on || undefined}>
                  <strong className="title-1">{h.when}</strong>
                  <span className="small muted">{h.where}</span>
                  <span className="small">{rich(h.omni)}</span>
                </li>
              ))}
            </ol>
            <p className="caption muted">기간은 설명을 위한 예시이며 개인 사정에 따라 다릅니다.</p>
          </div>
        </div>
        <div className="risk-charts">
          <figure className="why-stat" aria-labelledby="holding-title">
            <figcaption id="holding-title" className="heading-4">오래 보유할수록 손실로 끝난 구간이 줄었습니다</figcaption>
            <Bars max={20} rows={HOLDING_LOSS.map((r) => ({ ...r, text: `${r.value}%`, muted: r.value === 0 }))} />
            <p className="small"><strong className="em">10년을 보유해도 손실이 난 구간이 있었습니다.</strong> 가장 나빴던 10년은 −29.5%, 월말 기준 최대 낙폭은 −50.9%였습니다.</p>
            <p className="caption muted">S&amp;P 500(배당 포함, 달러) 1988년 1월~2026년 10월 월말 값으로 직접 계산했습니다. 구간이 겹쳐 독립된 표본은 훨씬 적고, 환율·세금·물가는 반영하지 않았습니다. 앞으로의 수익을 보장하지 않습니다.</p>
          </figure>
          <figure className="why-stat" aria-labelledby="sequence-title">
            <figcaption id="sequence-title" className="heading-4">은퇴 직후에 하락하면 타격이 더 큽니다</figcaption>
            <Bars max={30000} rows={SEQUENCE.map((r, i) => ({ ...r, muted: i > 0 }))} />
            <p className="small">3억 원에서 해마다 1,500만 원을 꺼내 쓸 때 20년 뒤 남는 돈입니다. 두 경우의 <strong className="em">연평균 수익률은 3.96%로 같고 순서만 다릅니다.</strong> 그래서 곧 쓸 돈은 투자하지 않습니다.</p>
            <p className="small">Vanguard는 은퇴 초기에 수익률이 나빴던 사람이 생전에 자산을 다 쓸 가능성이 <strong className="em">31% 더 높다</strong>고 추정했습니다(2020년, 모형 추정치).</p>
            <a className="caption why-src" href="https://www.vanguard.co.uk/content/dam/intl/europe/documents/en/whitepapers/safeguarding-retirement-bear-market.pdf" target="_blank" rel="noopener noreferrer">Vanguard 보고서 <ArrowUpRight size={12} aria-hidden /></a>
            <p className="caption muted">실제 시장 자료가 아니라 설명을 위한 가정입니다. 하락한 3년은 −15·−10·−10%, 나머지 17년은 해마다 7%로 두었고, 돈은 연초에 꺼냅니다.</p>
          </figure>
        </div>
        <div className="regimes" role="group" aria-label="시장 국면 선택. 위험이 큰 순서입니다.">
          {REGIMES.map((r, i) => (
            <button key={r.name} type="button" className="regime" aria-pressed={i === pick} onClick={() => setPick(i)} style={css({ '--heat': HEAT[i] })}>
              <span className="heat" aria-hidden />
              <span className="title-1">{r.name}</span>
              <span className="caption muted">{r.rule}</span>
            </button>
          ))}
        </div>
        <p className="small muted strip-note" aria-live="polite">
          <strong style={{ color: 'var(--text)' }}>{regime.name}</strong>에 추천하는 전략:{' '}
          {regime.masters.map((id, i) => <span key={id}><span className="nowrap">{masterName[id]}</span>{i < regime.masters.length - 1 ? ', ' : ''}</span>)}
        </p>
        <ul className="masters" aria-label="거장 전략 8가지">
          {MASTERS.map((m) => {
            const on = regime.masters.includes(m.id)
            return (
              <li key={m.id} className="master" data-dim={!on || undefined}>
                <div className="master-top">
                  <h3 className="title-1">{m.name}</h3>
                  <span className="tag">{m.on ? '기본 켬' : '선택'}</span>
                </div>
                <p className="small idea">“{m.idea}”</p>
                <p className="small">{m.does}</p>
                <span className="sr-only">{on ? `${regime.name}에 추천` : `${regime.name}에는 추천하지 않음`}</span>
              </li>
            )
          })}
        </ul>
        <p className="caption muted strip-note">같은 판정이 10거래일 연속으로 나오면 새 시장 국면을 확정합니다(투매는 2거래일). 적용할 전략은 추천 목록을 보고 직접 고릅니다.</p>
        <ul className="points" data-reveal>
          {LONGTERM_POINTS.map((p) => <li key={p.title}><h3 className="title-2">{p.title}</h3><p className="small muted">{p.body}</p></li>)}
        </ul>
        <p className="more-row"><a className="more button-sm" href={doc('longterm.md')}>장기투자 가이드 <ArrowUpRight size={16} aria-hidden /></a></p>
      </div>
    </section>
  )
}

function Dca() {
  const [side, setSide] = useState<'master' | 'me'>('master')
  return (
    <section id="dca" className="section band" aria-labelledby="dca-title">
      <div className="container">
        <div className="head-split">
          <div className="head">
            <p className="kicker">적립식 투자</p>
            <h2 id="dca-title" className="heading-2">언제, 얼마나 적립할지 미리 정합니다</h2>
            <p className="lead">
              적립식은 새로 넣는 자금으로 ETF를 사는 기능입니다. 매수 날짜, 금액이나 수량, 종목, 시각은 항목별로 직접 정하거나 거장의 투자 원칙을 따르게 할 수 있습니다.
              기존 운용금의 매매와 보유 ETF의 비중 조정은 계좌 전략이 맡습니다.
            </p>
          </div>
          <img className="art art-sm" src={img('mascot-dca')} alt="" width="280" height="280" loading="lazy" />
        </div>
        <div className="dca">
          <div className="panel">
            <div className="seg" data-side={side} role="group" aria-label="적립식 매수 조건 설정 방식">
              <span className="seg-thumb" aria-hidden />
              <button type="button" aria-pressed={side === 'master'} onClick={() => setSide('master')}>거장 원칙 따르기</button>
              <button type="button" aria-pressed={side === 'me'} onClick={() => setSide('me')}>내가 정하기</button>
            </div>
            <dl className="axes" aria-live="polite">
              {DCA_AXES.map((a) => (
                <div key={a.axis}><dt className="title-2">{a.axis}</dt><dd className="small">{side === 'master' ? a.master : a.me}</dd></div>
              ))}
            </dl>
          </div>
          <div>
            <h3 className="heading-3">적립식 투자 방식 7가지</h3>
            <dl className="profiles" style={{ marginTop: 16 }}>
              {DCA_PROFILES.map((p) => {
                const [name, rest] = p.split(' · ')
                return <div key={p}><dt className="title-3">{name}</dt><dd className="small muted">{rest}</dd></div>
              })}
            </dl>
            <p className="caption muted" style={{ marginTop: 16 }}>장기투자 차트에서 다음 적립 매수 기준가와 여운봉 저수지 매매법의 단계별 매수 기준가를 볼 수 있습니다.</p>
          </div>
        </div>
        <p className="dca-funding small">
          <strong>입금형 적립식</strong>을 쓰려면 장기투자 실계좌에 입금한 뒤 대시보드에서 운용금을 올려 저장합니다.
          {' '}늘어난 운용금 중 지정한 몫을 여러 주에 나눠 매수할 계획을 세우고, 주문 전에 계좌 현금과 보호 한도를 확인합니다.
        </p>
        <p className="more-row"><a className="more button-sm" href={doc('longterm-dca.md')}>적립식 가이드 <ArrowUpRight size={16} aria-hidden /></a></p>
      </div>
    </section>
  )
}

function Styles() {
  return (
    <section id="styles" className="section" aria-labelledby="styles-title">
      <div className="container">
        <div className="head">
          <p className="kicker">내 투자 스타일</p>
          <h2 id="styles-title" className="heading-2">내 투자 성향에 맞는 방식을 고릅니다</h2>
          <p className="lead">장중 매매를 연습하거나 장기 적립 계획을 세울 수 있습니다. 모의계좌와 가상 장부에서 각각의 판단과 결과를 보고 나에게 맞는 방식을 고릅니다.</p>
        </div>
        <img className="art-visual styles-visual" src={img('mascot-styles')} alt="" width="1200" height="406" loading="lazy" />
        <ul className="styles" data-reveal>
          {STYLES.map((x) => (
            <li key={x.name} className="style-card">
              <span className="tag">{x.tag}</span>
              <h3 className="heading-4">{x.name}</h3>
              <p className="small muted">{rich(x.who)}</p>
              <dl className="small">
                <div><dt>운용</dt><dd>{x.run}</dd></div>
                <div><dt>켜는 곳</dt><dd>{x.how}</dd></div>
              </dl>
            </li>
          ))}
        </ul>
        <ol className="grow" data-reveal aria-label="투자 전략을 점검하는 세 단계">
          {GROW.map((g, i) => (
            <li key={g.title}><span className="craft-no" aria-hidden>{i + 1}</span><div><strong className="title-2">{g.title}</strong><p className="small muted">{rich(g.body)}</p></div></li>
          ))}
        </ol>
      </div>
    </section>
  )
}

function Data() {
  return (
    <section id="data" className="section band" aria-labelledby="data-title">
      <div className="container">
        <div className="head">
          <p className="kicker">단순한 구조</p>
          <h2 id="data-title" className="heading-2">설정은 JSON에,<br />매매 기록은 SQLite에 저장합니다</h2>
          <p className="lead">
            <strong className="em">별도 서버나 클라우드 데이터베이스 없이</strong> 내 컴퓨터에 저장합니다. 대시보드에서 바꾼 설정과 투자 관점은 JSON 파일에, 매일의 판단과 체결은 SQLite에 남깁니다.
            기록은 다음 판단에 참고하고, 백업 파일로 다른 Mac에 옮길 수 있습니다.
          </p>
        </div>
        <div className="data-flow" data-reveal>
          <div className="data-col">
            <h3 className="title-1"><span className="file-kind">JSON</span> 관점과 설정</h3>
            <ul>{DATA_JSON.map((d) => <li key={d.file}><code>{d.file}</code><span className="small muted">{d.body}</span></li>)}</ul>
          </div>
          <div className="data-arrow" aria-hidden><span>매분 판단</span><ArrowRight size={20} /><span>매일 복기</span></div>
          <div className="data-col">
            <h3 className="title-1"><span className="file-kind">SQLite</span> 판단과 매매 기록</h3>
            <ul>{DATA_SQLITE.map((d) => <li key={d.file}><code>{d.file}</code><span className="small muted">{d.body}</span></li>)}</ul>
          </div>
        </div>
      </div>
    </section>
  )
}

function AiOps() {
  return (
    <section id="ai" className="section band" aria-labelledby="ai-title">
      <div className="container">
        <div className="head-split">
          <div className="head">
            <p className="kicker">Claude Code · Codex로 운영</p>
            <h2 id="ai-title" className="heading-2">AI 도구로 설치하고,<br />대화와 예약 작업으로 운영합니다</h2>
            <p className="lead">
              Omni AI Trading은 <strong className="em">Claude Code나 Codex 같은 AI 코딩 에이전트와 함께 쓰는 투자 도구</strong>입니다.
              AI 에이전트에게 설치를 맡기고, 채팅으로 상태를 확인하거나 분석과 설정 변경을 요청할 수 있습니다. 반복 점검과 리포트 작성은 예약 작업이 맡습니다.
              {' '}<mark className="hl">실행기는 주문 보호 조건을 충족한 실주문만 보냅니다.</mark>
            </p>
          </div>
          <img className="art art-wide" src={img('mascot-agent')} alt="" width="1200" height="800" loading="lazy" />
        </div>
        <ol className="use-steps" data-reveal>
          {USE_STEPS.map((u, i) => {
            const Icon = ICONS[u.icon as keyof typeof ICONS]
            return (
              <li key={u.title} className="use-step">
                <span className="icon-chip" aria-hidden><Icon size={20} /></span>
                <span className="craft-no" aria-hidden>{i + 1}</span>
                <h3 className="heading-4">{u.title}</h3>
                <p className="small muted">{rich(u.body)}</p>
              </li>
            )
          })}
        </ol>
        <div className="ai-chips" data-reveal>
          <div><p className="title-3">예약 작업이 매일 하는 일</p><ul>{SCHEDULE_CHIPS.map((c) => <li key={c} className="tag">{c}</li>)}</ul></div>
          <div><p className="title-3">프로젝트 스킬 10개</p><ul>{AI_SKILLS.map((c) => <li key={c} className="tag">{c}</li>)}</ul></div>
        </div>
        <div className="ai-ops">
          <figure className="chat" aria-label="채팅 운영 예시">
            <div className="chat-head">
              <img src={img('mascot-ai-chat')} alt="" width="160" height="160" loading="lazy" />
              <div><p className="title-2">Omni 운영 채팅</p><p className="caption muted">Claude Code · Codex</p></div>
            </div>
            <ul className="chat-body">
              {CHAT.map((c, i) => (
                <li key={i} className={`turn ${c.who}`}>
                  <span className="avatar" aria-hidden>{c.who === 'me' ? <UserCircle size={16} /> : <Robot size={16} />}</span>
                  <p className="bubble small"><span className="sr-only">{c.who === 'me' ? '나: ' : 'AI 에이전트: '}</span>{c.text}</p>
                </li>
              ))}
            </ul>
            <figcaption className="caption">대화 예시입니다. 실제 답은 그날 기록에 따라 달라집니다.</figcaption>
          </figure>
          <div>
            <ol className="steps-v">
              {AI_LOOP.map((step, i) => (
                <li key={step.title}>
                  <span className={`num${i === 2 ? '' : ' quiet'}`}>{i + 1}</span>
                  <div><h3 className="title-1">{step.title}</h3><p className="small muted">{step.body}</p></div>
                </li>
              ))}
            </ol>
            <p className="small muted" style={{ marginTop: 20 }}>AI 도구를 쓰려면 유료 요금제가 필요합니다.</p>
            <p className="more-row" style={{ marginTop: 12 }}><a className="more button-sm" href={doc('ai-agents.md')}>AI와 함께 쓰기 가이드 <ArrowUpRight size={16} aria-hidden /></a></p>
          </div>
        </div>
      </div>
    </section>
  )
}

function CliNodes({ items }: { items: { icon: string; name: string; role: string }[] }) {
  return (
    <ul className="cli-nodes">
      {items.map((n) => {
        const Icon = ICONS[n.icon as keyof typeof ICONS]
        return <li key={n.name}><span className="icon-chip" aria-hidden><Icon size={20} /></span><div><p className="title-3">{n.name}</p><p className="caption muted">{n.role}</p></div></li>
      })}
    </ul>
  )
}

// Three cards meet at the hub; the dashes run toward it or away from it (still under reduced motion).
function CliWires({ out }: { out?: boolean }) {
  return (
    <>
      <svg className="cli-wires" viewBox="0 0 100 300" preserveAspectRatio="none" aria-hidden>
        {['base', 'run'].map((layer) => [45, 150, 255].map((y) => (
          <path key={layer + y} className={layer} d={out ? `M0 150 C50 150 50 ${y} 100 ${y}` : `M0 ${y} C50 ${y} 50 150 100 150`} vectorEffect="non-scaling-stroke" />
        )))}
      </svg>
      <ArrowDown className="cli-down" size={20} aria-hidden />
    </>
  )
}

// The command line tool: try it, how a change is approved, what connects to it, and how to install it.
function Cli() {
  return (
    <section id="cli" className="section" aria-labelledby="cli-title">
      <div className="container">
        <div className="head-split">
          <div className="head">
            <p className="kicker">omni CLI</p>
            <h2 id="cli-title" className="heading-2">명령 한 줄로 묻고,<br />중요한 변경은 직접 승인합니다</h2>
            <p className="lead">
              <strong className="em">여러 AI 에이전트와 터미널을 같은 명령으로 잇는 도구</strong>입니다. Claude Code와 Codex를 비롯해 셸 명령을 실행할 수 있는 에이전트라면 기록을 조회하고, 설정을 바꾸기 전에 어떤 영향이 있는지 확인할 수 있습니다.
            </p>
          </div>
          <img className="art art-wide" src={img('mascot-cli')} alt="" width="1200" height="800" loading="lazy" />
        </div>

        <div className="cli-demo" data-reveal>
          <Tabs defaultValue={CLI_SCENES[0].id} orientation="vertical" className="cli-tabs">
            <TabsList aria-label="omni에게 물어볼 말 고르기" className="cli-asks">
              {CLI_SCENES.map((s) => (
                <TabsTab key={s.id} value={s.id} className="cli-ask"><span className="cli-kind">{s.kind}</span><span>{s.ask}</span></TabsTab>
              ))}
            </TabsList>
            <div className="panels cli-screen">
              {CLI_SCENES.map((s) => (
                <TabsPanel key={s.id} value={s.id} className="cli-panel">
                  <div className="terminal">
                    <p className="terminal-bar"><TerminalWindow size={16} aria-hidden /> omni</p>
                    <pre><code>{s.lines.map((l, i) => <span key={i} className={l.startsWith('$') ? 'cmd' : 'out'}>{l}{'\n'}</span>)}</code></pre>
                  </div>
                  <div className="cli-reply">
                    <span className="avatar" aria-hidden><Robot size={16} /></span>
                    <div><p className="caption muted">AI 에이전트가 전하는 답</p><p className="body">{s.reply}</p></div>
                  </div>
                </TabsPanel>
              ))}
            </div>
          </Tabs>
          <p className="caption muted">실제로 쓰는 명령이며, 답에 나온 값은 예시입니다.</p>
        </div>

        <div className="cli-block">
          <h3 className="heading-3">바꾸는 일은 네 단계를 거칩니다</h3>
          <p className="muted">조회는 바로 답하고, <strong className="em">실계좌에 영향을 주는 변경과 매매 요청은 사용자가 승인해야</strong> 적용됩니다.</p>
          <ol className="loop cli-flow" data-reveal>
            {CLI_FLOW.map((f, i) => {
              const Icon = ICONS[f.icon as keyof typeof ICONS]
              return (
                <li key={f.title} className={f.tag ? 'focal' : undefined}>
                  <span className="cli-flow-head"><span className="icon-chip" aria-hidden><Icon size={20} /></span><span className="craft-no" aria-hidden>{i + 1}</span></span>
                  <h4 className="title-1">{f.title}</h4>
                  <p className="small muted">{rich(f.body)}</p>
                  {f.tag && <span className="tag tag-warn">{f.tag}</span>}
                </li>
              )
            })}
          </ol>
          <p className="cli-note small muted"><ShieldCheck size={16} aria-hidden /><span>직접 주문하는 명령은 없습니다. 매매 요청은 실행기와 주문 가드를 거칩니다. 실계좌의 신규 매수 중단만 승인 코드 없이 바로 적용됩니다.</span></p>
          <p className="more-row"><a className="more button-sm" href="./omni-cli-flow.html">흐름도 크게 보기 <ArrowUpRight size={16} aria-hidden /></a></p>
        </div>

        <div className="cli-block">
          <h3 className="heading-3">쓰던 AI 에이전트를 그대로 연결하세요</h3>
          <p className="muted">omni CLI는 특정 AI 도구에 묶이지 않습니다. 새 에이전트를 쓰거나 자동화를 더해도 <strong className="em">같은 명령으로 확장할 수 있습니다.</strong></p>
          <div className="cli-hub" role="group" aria-label="연결 구조. AI 에이전트와 스크립트가 omni CLI를 거쳐 기록, 설정 변경 계획, 승인으로 이어집니다.">
            <CliNodes items={CLI_FROM} />
            <CliWires />
            <div className="cli-core"><TerminalWindow size={32} aria-hidden /><p className="heading-4">omni CLI</p><ul>{CLI_HUB.map((h) => <li key={h}><Check size={14} aria-hidden />{h}</li>)}</ul></div>
            <CliWires out />
            <CliNodes items={CLI_TO} />
          </div>
          <ul className="cli-points" data-reveal>
            {CLI_OPEN.map((f) => {
              const Icon = ICONS[f.icon as keyof typeof ICONS]
              return <li key={f.title}><span className="icon-chip" aria-hidden><Icon size={20} /></span><h4 className="title-1">{f.title}</h4><p className="small muted">{rich(f.body)}</p></li>
            })}
          </ul>
        </div>

        <div className="cli-block cli-start">
          <Tabs defaultValue="ai">
            <div className="cli-start-head">
              <h3 className="heading-3">CLI 설치하기</h3>
              <TabsList aria-label="설치 방법 고르기" className="tab-chips">
                <TabsIndicator className="tab-thumb" />
                {CLI_START.map((s) => <TabsTab key={s.id} value={s.id} className="tab-chip">{s.label}</TabsTab>)}
              </TabsList>
            </div>
            <div className="panels">
              {CLI_START.map((s) => (
                <TabsPanel key={s.id} value={s.id} className="cli-start-panel">
                  <ol className="steps-v">
                    {s.steps.map((x, i) => <li key={x.title}><span className="num quiet">{i + 1}</span><div><h4 className="title-2">{x.title}</h4><p className="small muted">{x.body}</p></div></li>)}
                  </ol>
                  <div className="prompt">
                    <div className="prompt-head"><p className="title-3">{s.copy}</p><CopyButton text={s.code} label={s.copy} /></div>
                    <pre tabIndex={0} aria-label={`${s.label} ${s.copy}`}><code>{s.code}</code></pre>
                  </div>
                </TabsPanel>
              ))}
            </div>
          </Tabs>
          <p className="more-row">
            <a className="more button-sm" href={doc('omni-cli.md')}>omni CLI 가이드 <ArrowUpRight size={16} aria-hidden /></a>
            <a className="more button-sm" href={doc('omni-cli-reference.md')}>명령·설정 참조 <ArrowUpRight size={16} aria-hidden /></a>
          </p>
        </div>
      </div>
    </section>
  )
}

type Scene = { id: string; label: string; screen: string; height?: number; title: string; question: string; body: string; detail: string; note?: string; alt: string }

// Screens of the AI search in tabs; each opens larger.
function SearchDemo({ title, items }: { title: string; items: Scene[] }) {
  return (
    <div className="search-demo" data-reveal>
      <Tabs defaultValue={items[0].id}>
        <div className="search-demo-head">
          <h3 className="heading-3">{title}</h3>
          <TabsList aria-label={`${title} 고르기`} className="tab-chips">
            <TabsIndicator className="tab-thumb" />
            {items.map((item) => <TabsTab key={item.id} value={item.id} className="tab-chip">{item.label}</TabsTab>)}
          </TabsList>
        </div>
        <div className="panels search-panels">
          {items.map((item) => {
            const note = item.note ?? '실제 대시보드 화면에 예시 데이터를 넣었습니다.'
            return (
              <TabsPanel key={item.id} value={item.id} className="search-panel">
                <figure className="screen-panel">
                  <Dialog>
                    <DialogTrigger className="zoom" aria-label={item.title + ' 화면 크게 보기'}>
                      <img src={shot(item.screen)} width="1440" height={item.height ?? 1068} loading="lazy" alt={item.alt} />
                      <span className="zoom-hint"><MagnifyingGlassPlus size={14} aria-hidden /> 크게 보기</span>
                    </DialogTrigger>
                    <DialogBackdrop className="backdrop" />
                    <DialogContent className="lightbox">
                      <div className="lightbox-head">
                        <DialogTitle className="title-1">{item.title}</DialogTitle>
                        <DialogClose className="icon-btn" aria-label="닫기"><X size={18} aria-hidden /></DialogClose>
                      </div>
                      <DialogDescription className="small muted">{note}</DialogDescription>
                      <img src={shot(item.screen)} width="1440" height={item.height ?? 1068} alt={item.alt} />
                    </DialogContent>
                  </Dialog>
                  <figcaption className="caption muted">{note}</figcaption>
                </figure>
                <div className="search-example">
                  <h4 className="heading-4">{item.title}</h4>
                  <blockquote className="search-question title-2">
                    <ChatsCircle size={20} aria-hidden />
                    <p>{item.question}</p>
                  </blockquote>
                  <p className="body muted">{rich(item.body)}</p>
                  <p className="small muted">{item.detail}</p>
                </div>
              </TabsPanel>
            )
          })}
        </div>
      </Tabs>
    </div>
  )
}

function AiSearch() {
  return (
    <section id="ai-search" className="section search-section" aria-labelledby="ai-search-title">
      <div className="container">
        <div className="search-intro">
          <div className="head">
            <p className="kicker">AI 검색 · Liner API</p>
            <h2 id="ai-search-title" className="heading-2">궁금할 때 바로 묻고<br />투자 지식을 쌓아 가세요</h2>
            <p className="lead">투자가 처음이어도 괜찮습니다. 시세나 매매 기록을 보다가 모르는 말이 나오면 <strong className="em">글자를 드래그해 그 자리에서 물어보세요.</strong>{' '}오른쪽 아래 작은 채팅창이 열려 보던 화면을 떠나지 않아도 됩니다. 기사와 공시 원문, 뉴스의 시장 영향, 내 보유 종목도 같은 창에서 확인합니다.</p>
            <div className="search-actions">
              <a className="btn btn-primary button-md" href={doc('ai-search.md')}>
                AI 검색 사용 가이드 <ArrowUpRight size={16} aria-hidden />
              </a>
              <p className="small muted"><code>LINER_API_KEY</code>를 설정하면 상단의 AI 검색과 오른쪽 아래 AI에게 묻기 버튼이 표시됩니다.</p>
            </div>
          </div>
          <div className="search-art" aria-hidden>
            <img src={img('mascot-ai-search')} alt="" width="640" height="640" loading="lazy" />
          </div>
        </div>
        <SearchDemo title="보던 화면에서 바로 묻기" items={ASK_SCENES} />
        <h3 className="heading-4 ask-title">묻는 방법 네 가지</h3>
        <ul className="ask-ways">
          {ASK_WAYS.map((w) => <li key={w.title}><h4 className="title-2">{w.title}</h4><p className="small muted">{w.body}</p></li>)}
        </ul>
        <ul className="ask-rules">
          {ASK_RULES.map((x) => <li key={x.title}><ShieldCheck size={18} aria-hidden /><div><h4 className="title-2">{x.title}</h4><p className="small muted">{rich(x.body)}</p></div></li>)}
        </ul>
        <h3 className="heading-4 ask-title">자료를 찾고 확인하는 순서</h3>
        <ol className="search-steps" aria-label="AI 검색이 하는 일">
          {SEARCH_STEPS.map((s) => (
            <li key={s.title}>
              <span className="who caption" data-liner={s.liner || undefined}>{s.who}</span>
              <h3 className="title-1">{s.title}</h3>
              <p className="small muted">{rich(s.body)}</p>
            </li>
          ))}
        </ol>
        <div className="learn">
          <h3 className="heading-3">처음이라면 이런 순서로 물어보세요</h3>
          <p className="muted">추천 질문 약 100개를 서비스 사용법, 투자 기초, 투자 거장, 노후 자금·투자 권유 등 9개 분류로 나눠 두었습니다. 화면에 보이는 추천 태그를 누르면 바로 전송되고, 답변을 읽다가 궁금한 점을 이어서 물을 수 있습니다.</p>
          <ol className="use-steps learn-steps" data-reveal>
            {LEARN_STEPS.map((step, i) => (
              <li key={step.title} className="use-step">
                <span className="craft-no" aria-hidden>{i + 1}</span>
                <h4 className="title-1">{step.title}</h4>
                <p className="small muted">{rich(step.body)}</p>
                <p className="learn-question small"><ChatsCircle size={16} aria-hidden /><span>{step.question}</span></p>
              </li>
            ))}
          </ol>
          <p className="small muted">답변은 공부를 돕는 참고 자료입니다. 웹 답변은 출처를 함께 보고, 중요한 내용은 원문에서 확인하세요. 도움이 된 답변은 마크다운으로 복사해 나만의 공부 노트에 모을 수 있습니다.</p>
        </div>
        <SearchDemo title="넓은 창에서 하는 일" items={AI_SEARCH_EXAMPLES} />
      </div>
    </section>
  )
}

const PHASES = ['장전', '장중', '마감', '주간·월간'] as const

function Daily() {
  return (
    <section id="daily" className="section" aria-labelledby="daily-title">
      <div className="container">
        <div className="head">
          <p className="kicker">자동 점검과 리포트</p>
          <h2 id="daily-title" className="heading-2">매일 정해진 시간에 점검하고 보고합니다</h2>
          <p className="lead">
            일정은 평일 한국 시간 기준입니다. 리포트를 HTML 페이지로 게시하고, Discord·Telegram·Slack으로 링크를 보냅니다. 개선안은 사용자가 승인한 뒤 적용합니다.
          </p>
        </div>
        <div className="chart-wrap">{chart(<ScheduleChart />, 467)}</div>
        <div className="phases" data-reveal>
          {PHASES.map((ph) => (
            <section key={ph} className="phase" aria-label={ph}>
              <h3 className="title-1">{ph}</h3>
              <ol>
                {DAILY.filter((d) => d.phase === ph).map((d) => (
                  <li key={d.time + d.title}>
                    <time>{d.time}</time>
                    <strong className="title-2">{d.title}</strong>
                    <span className="small muted">{d.body}</span>
                  </li>
                ))}
              </ol>
            </section>
          ))}
        </div>
        <div className="why-stat fix-case">
          <p className="kicker">{FIX_CASE.kind}</p>
          <h3 className="heading-4">{FIX_CASE.title}</h3>
          <p className="small muted">{FIX_CASE.body}</p>
          <a className="caption why-src" href={FIX_CASE.href} target="_blank" rel="noopener noreferrer">{FIX_CASE.label} <ArrowUpRight size={12} aria-hidden /></a>
        </div>
        <div className="cta-line">
          <div>
            <img src={img('mascot-journal')} alt="" width="200" height="200" loading="lazy" />
            <p><strong className="title-1">리포트 보관함</strong><br /><span className="small muted">장전 분석, 장 마감 개선안, 매매일지, 장기투자 리포트를 날짜와 종류로 찾습니다.</span></p>
          </div>
          <a className="btn btn-ghost button-sm" href="./reports/">보관함 열기 <ArrowRight size={16} aria-hidden /></a>
        </div>
      </div>
    </section>
  )
}

function Extend() {
  return (
    <section id="extend" className="section extend" aria-labelledby="extend-title">
      <div className="container">
        <div className="head-split">
          <div className="head">
            <p className="kicker">다른 결정에 활용하기</p>
            <h2 id="extend-title" className="heading-2">투자에서 다듬은 방식을<br />다른 결정에도</h2>
            <p className="lead">내 투자 데이터는 그대로 두고 <strong className="em">기준을 적고, 근거를 찾고, 사람이 승인하고, 결과를 복기하는 방식</strong>만 가져다 씁니다.</p>
          </div>
          <img className="extend-art" src={img('mascot-extend')} alt="" width="840" height="417" loading="lazy" />
        </div>
        <ol className="loop" aria-label="다섯 단계를 거쳐 다시 기준으로 돌아옵니다">
          {EXTEND_STEPS.map((s, i) => (
            <li key={s.title}>
              <span className="craft-no" aria-hidden>{i + 1}</span>
              <h3 className="title-1">{s.title}</h3>
              <p className="small muted">{s.body}</p>
            </li>
          ))}
        </ol>
        <p className="loop-back small muted"><ArrowsClockwise size={16} aria-hidden /> 고친 기준으로 1단계부터 다시 시작합니다.</p>
        <div className="extend-grid">
          <div className="why-stat">
            <p className="kicker">실제로 만든 사례</p>
            <h3 className="heading-4">창업 준비 점검</h3>
            <p className="small muted">AI 검색 질문 8개로 공공 자료와 손익분기점을 알아보고, 8절로 된 점검표에 생활비와 멈출 기준을 적습니다. 주문과 매매 기록은 쓰지 않습니다.</p>
            <pre className="extend-term" tabIndex={0} aria-label="가상 점검표를 검사한 결과">{EXTEND_CHECK}</pre>
            <p className="caption muted">가상 사례에서 세 항목을 일부러 빼고 실제로 검사한 결과입니다. 사업이 잘될지는 판단하지 않습니다.</p>
            <a className="caption why-src" href={`${doc('ai-search.md')}#창업-준비-점검`}>창업 준비 점검 보기 <ArrowUpRight size={12} aria-hidden /></a>
          </div>
          <div className="extend-side">
            {EXTEND_MOVE.map((m) => (
              <div key={m.title} className="why-stat">
                <h3 className="title-1">{m.title}</h3>
                <ul className="extend-list">{m.items.map((x) => <li key={x} className="small muted">{x}</li>)}</ul>
              </div>
            ))}
          </div>
        </div>
        <h3 className="heading-4 extend-next-title">다음으로 넓힐 곳</h3>
        <ul className="traps">
          {EXTEND_NEXT.map((n) => <li key={n.title} className="trap"><h4 className="title-2">{n.title}</h4><p className="small muted">{n.body}</p></li>)}
        </ul>
        <p className="small muted">다른 조직에 도입한 사례는 아직 없고, 시간을 얼마나 아꼈는지도 측정하지 않았습니다. 같은 일을 직접 할 때와 비교해 측정할 계획입니다.</p>
      </div>
    </section>
  )
}

function StackSection() {
  return (
    <section id="stack" className="section band" aria-labelledby="stack-title">
      <div className="container">
        <div className="head-split">
          <div className="head">
            <p className="kicker">기반 기술</p>
            <h2 id="stack-title" className="heading-2">검증된 오픈소스 위에<br />국내 시장 매매와 장기투자를 더했습니다</h2>
            <p className="lead">
              Omni AI Trading은 <strong className="em">HKUDS Vibe-Trading</strong>을 바탕으로, NH투자증권 나무 PLUG를 연동해 국내 ETF와 주식을 자동으로 매매하고,
              투자 거장의 원칙에 따라 국내 상장 미국 ETF를 운용하도록 확장한 오픈소스 프로젝트입니다. 원본의 MIT 라이선스와 저작권 고지를 그대로 따릅니다.
            </p>
          </div>
          <img className="art art-lg" src={img('mascot-foundation')} alt="" width="640" height="640" loading="lazy" />
        </div>
        <article className="upstream" data-reveal>
          <div className="upstream-main">
            <span className="tag">{UPSTREAM.license} · Omni의 기반</span>
            <h3 className="heading-3">{UPSTREAM.name}</h3>
            <p className="small muted">{rich(UPSTREAM.about)}</p>
            <a className="more button-sm" href={UPSTREAM.href} target="_blank" rel="noopener noreferrer">GitHub에서 원본 보기 <ArrowUpRight size={16} aria-hidden /></a>
          </div>
          <div>
            <p className="title-3">Omni가 이어받아 쓰는 것</p>
            <ul className="rows">
              {UPSTREAM.uses.map((u) => <li key={u.title}><strong className="title-2">{u.title}</strong><span className="small muted">{u.body}</span></li>)}
            </ul>
          </div>
        </article>
        <ul className="stack-grid" data-reveal>
          {STACK.map((t) => {
            const Icon = ICONS[t.icon as keyof typeof ICONS]
            return (
              <li key={t.name}>
                <a className="stack-card" href={doc(t.href)}>
                  <span className="icon-chip" aria-hidden><Icon size={20} /></span>
                  <span className="caption muted">{t.role}</span>
                  <strong className="title-1">{t.name}</strong>
                  <span className="small muted">{t.body}</span>
                </a>
              </li>
            )
          })}
        </ul>
      </div>
    </section>
  )
}

function Integrations() {
  const data = INTEGRATIONS.find((g) => g.group === '데이터')!
  const alerts = INTEGRATIONS.find((g) => g.group === '알림·게시')!
  const link = (it: { anchor?: string; doc?: string }) => (it.doc ? doc(it.doc) : `${doc('integrations.md')}#${it.anchor}`)
  return (
    <section id="integrations" className="section" aria-labelledby="integrations-title">
      <div className="container">
        <div className="head-split">
          <div className="head">
            <p className="kicker">서비스 연동과 시스템 구조</p>
            <h2 id="integrations-title" className="heading-2">증권사, AI, 공공 데이터, 알림을 한 구조로 묶었습니다</h2>
            <p className="lead">
              증권사 연동에는 NH 나무 PLUG가 필요합니다. 다른 서비스는 해당 기능을 쓸 때 연동합니다. API 키는 내 컴퓨터의 .env 파일에만 저장합니다.
            </p>
          </div>
          <img className="art art-sm" src={img('mascot-integrations')} alt="" width="280" height="280" loading="lazy" />
        </div>
        <div className="stack" role="group" aria-label="시스템 구조도. 왼쪽은 데이터 출처, 가운데는 판단과 주문 보호 단계, 오른쪽은 알림과 리포트 채널입니다.">
          <div className="stack-side in">
            <p className="kicker">가져오는 데이터</p>
            {data.items.map((it) => <a key={it.name} className="svc small" href={link(it)}><strong>{it.name}</strong><span className="caption muted">{it.role}</span></a>)}
          </div>
          <ol className="stack-core">
            {ARCH_LAYERS.map((l) => (
              <li key={l.label} className={`layer${l.focal ? ' focal' : ''}`}>
                <span className="kicker">{l.label}</span>
                <div className="tiles">
                  {l.items.map((it) => {
                    const body = <><strong className="title-3">{it.name}</strong><span className="caption">{it.role}</span></>
                    return 'href' in it && it.href ? <a key={it.name} className="tile" href={it.href}>{body}</a> : <div key={it.name} className="tile">{body}</div>
                  })}
                </div>
              </li>
            ))}
          </ol>
          <div className="stack-side out">
            <p className="kicker">보내는 알림과 리포트</p>
            {alerts.items.map((it) => <a key={it.name} className="svc small" href={link(it)}><strong>{it.name}</strong><span className="caption muted">{it.role}</span></a>)}
          </div>
        </div>
        <p className="more-row">
          <a className="more button-sm" href="./architecture.html">구조도 크게 보기 <ArrowUpRight size={16} aria-hidden /></a>
          <a className="more button-sm" href={doc('integrations.md')}>연동 서비스 가이드 <ArrowUpRight size={16} aria-hidden /></a>
        </p>
      </div>
    </section>
  )
}

function Safety() {
  return (
    <section id="safety" className="section band" aria-labelledby="safety-title">
      <div className="container">
        <div className="head-split">
          <div className="head">
            <p className="kicker">안전장치와 대시보드</p>
            <h2 id="safety-title" className="heading-2">모의·가상으로 먼저 확인하고, 실주문은 직접 승인합니다</h2>
            <p className="lead">계획과 가상 체결을 먼저 살펴볼 수 있습니다. 실계좌 주문은 계좌별 승인과 보호 조건을 거칩니다. 시세 급변이나 미체결이 생기면 실제 손실이 손실 차단 기준을 넘을 수 있습니다.</p>
          </div>
          <img className="art art-sm" src={img('mascot-safety-v2')} alt="" width="360" height="360" loading="lazy" />
        </div>
        <ol className="stages" data-reveal>
          {SAFETY_STAGES.map((s) => (
            <li key={s.step}>
              <span className="num quiet">{s.step}</span>
              <h3 className="title-1">{s.title}</h3>
              <p className="small muted">{s.body}</p>
            </li>
          ))}
        </ol>
        <div className="calib" data-reveal>
          <div>
            <h3 className="heading-4">Jev의 매수 신호를 매일 검증합니다</h3>
            <p className="small muted">
              공유 모의계좌 첫 주(11/11~11/17)에 관찰한 Jev 진입 신호는 신뢰도가 높은 구간일수록 적중률이 낮았습니다.
              {' '}장 마감 뒤에는 매수 신호가 나오고 5·15·30분이 지났을 때 가격이 어떻게 움직였는지 확인합니다. 신뢰도는 Jev가 판단에 붙인 값입니다. 적중률과 수익률은 따로 집계합니다.
            </p>
          </div>
          <ul className="calib-bars" aria-label="신뢰도 구간별 적중률">
            {CALIBRATION.map((c) => (
              <li key={c.band}>
                <span className="caption muted">신뢰도 {c.band}</span>
                <span className="calib-bar"><span style={css({ '--w': `${c.hit}%` })} /></span>
                <strong className="title-3">{c.hit}%</strong>
              </li>
            ))}
          </ul>
        </div>
        <details className="limits">
          <summary>주문 한도 요약 보기 <CaretDown size={18} aria-hidden /></summary>
          <table className="small">
            <caption className="sr-only">주문 한도 요약. 자세한 값은 안전장치 가이드를 봅니다.</caption>
            <tbody>
              {LIMITS.map(([k, v]) => <tr key={k}><th scope="row" className="title-3">{k}</th><td>{v}</td></tr>)}
            </tbody>
          </table>
        </details>
        <div id="screens" className="screens-wrap">
          <h3 className="heading-3">대시보드 화면 10개를 살펴보세요</h3>
          <p className="small muted" style={{ marginTop: 8, maxWidth: 680 }}>
            이 Streamlit 대시보드는 내 컴퓨터에서 엽니다. 계좌 상태를 확인하고 설정을 바꿀 수 있습니다. 실제 주문은 실행기가 보냅니다. 화면의 계좌번호는 가렸습니다.
          </p>
          <Tabs defaultValue={SCREENS[0].id}>
            <TabsList aria-label="화면 고르기" className="tab-chips">
              <TabsIndicator className="tab-thumb" />
              {SCREENS.map((s) => <TabsTab key={s.id} value={s.id} className="tab-chip">{s.label}</TabsTab>)}
            </TabsList>
            <div className="panels">
              {SCREENS.map((s) => (
                <TabsPanel key={s.id} value={s.id} className="screen-panel">
                  <Dialog>
                    <DialogTrigger className="zoom" aria-label={`${s.label} 화면 크게 보기`}>
                      <img src={shot(`${s.id}-crop`)} width="1300" height="812" loading="lazy" alt={`${s.label}: ${s.caption}`} />
                      <span className="zoom-hint"><MagnifyingGlassPlus size={14} aria-hidden /> 크게 보기</span>
                    </DialogTrigger>
                    <DialogBackdrop className="backdrop" />
                    <DialogContent className="lightbox">
                      <div className="lightbox-head">
                        <DialogTitle className="title-1">{s.label}</DialogTitle>
                        <DialogClose className="icon-btn" aria-label="닫기"><X size={18} aria-hidden /></DialogClose>
                      </div>
                      <DialogDescription className="small muted">{s.caption}</DialogDescription>
                      <img src={shot(s.id)} width="1600" height="1000" alt="" />
                    </DialogContent>
                  </Dialog>
                  <p className="small muted">{s.caption}</p>
                </TabsPanel>
              ))}
            </div>
          </Tabs>
          <ul className="guides" aria-label="기능별 가이드">
            {FEATURES.map((f) => {
              const Icon = ICONS[f.icon as keyof typeof ICONS]
              return <li key={f.title}><a href={f.href} title={f.body}><Icon size={16} aria-hidden />{f.title}</a></li>
            })}
            <li><a href={doc('safety.md')}><ShieldCheck size={16} aria-hidden />안전장치</a></li>
          </ul>
        </div>
      </div>
    </section>
  )
}

function CopyButton({ text, label = '프롬프트' }: { text: string; label?: string }) {
  const [state, setState] = useState<'idle' | 'done' | 'fail'>('idle')
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); setState('done') } catch { setState('fail') }
    setTimeout(() => setState('idle'), 2000)
  }
  return (
    <>
      <button type="button" className="btn btn-ghost btn-sm button-sm copy-btn" onClick={copy} aria-describedby="copy-status">
        {state === 'done' ? <Check size={16} aria-hidden /> : <Copy size={16} aria-hidden />}
        {state === 'done' ? '복사했습니다' : `${label} 복사`}
      </button>
      <span id="copy-status" className="sr-only" role="status">
        {state === 'done' ? `${label}를 복사했습니다` : state === 'fail' ? '복사하지 못했습니다. 직접 선택해 주세요' : ''}
      </span>
    </>
  )
}

function Start() {
  return (
    <section id="start" className="section" aria-labelledby="start-title">
      <div className="container">
        <div className="head">
          <p className="kicker">시작하기</p>
          <h2 id="start-title" className="heading-2">설치하고 시세 연결부터 확인하세요</h2>
          <p className="small muted">
            공개 v0.1.3과 v0.1.4에는 Jev 모의 실행에 필요한 Python 모듈이 빠져 있습니다. 두 버전 중 하나를 쓰고 있다면 <a className="more" href={doc('getting-started.md')}>설치 가이드</a>의 시세 연결 확인부터 진행하세요.
          </p>
        </div>
        <div className="start">
          <ol className="steps-v code-steps">
            {STEPS.map((s, i) => (
              <li key={s.title}>
                <span className={`num${s.code ? ' quiet' : ''}`}>{i + 1}</span>
                <div>
                  <h3 className="title-1">{s.title}</h3>
                  <p className="small muted">{s.body}</p>
                  {s.code && <pre className="code"><code>{s.code}</code></pre>}
                </div>
              </li>
            ))}
          </ol>
          <div>
            <p className="notice-line">
              <Warning size={16} aria-hidden />
              <span><strong>시작 전에 확인하세요.</strong> {AI_PAID} {DISCLAIMER_SHORT} <a href="#disclaimer">전체 고지</a></span>
            </p>
            <div className="prompt">
              <div className="prompt-head">
                <p className="title-3">3단계 · AI에게 줄 설치 프롬프트</p>
                <CopyButton text={PROMPT} />
              </div>
              <pre><code>{PROMPT}</code></pre>
            </div>
          </div>
        </div>
        <div id="faq" className="faq">
          <div className="faq-head head">
            <p className="kicker">자주 묻는 질문</p>
            <h2 className="heading-3">설치와 이용 안내</h2>
            <p className="small muted">더 궁금한 점은 <a href={doc('faq.md')}>FAQ 문서</a>에 있습니다.</p>
          </div>
          <Accordion className="accordion">
            {FAQ.map((f) => (
              <AccordionItem key={f.q} value={f.q} className="acc-item">
                <AccordionHeader className="acc-head">
                  <AccordionTrigger className="acc-trigger">{f.q}<CaretDown size={18} aria-hidden /></AccordionTrigger>
                </AccordionHeader>
                <AccordionPanel className="acc-panel"><p className="small muted">{f.a}</p></AccordionPanel>
              </AccordionItem>
            ))}
          </Accordion>
        </div>
        <div className="final-inner">
          <img className="art art-sm" src={img('mascot-start')} alt="" width="360" height="360" loading="lazy" />
          <h2 className="heading-2">모의계좌에서 먼저 연습해 보세요</h2>
          <p className="lead">AI 에이전트에게 설치를 맡길 수 있습니다. 주문 서비스는 사용자가 승인한 뒤 켭니다.</p>
          <a className="btn btn-primary button-md" href={START}><GithubLogo size={18} aria-hidden />GitHub에서 시작하기</a>
          <p className="caption muted">{DISCLAIMER_SHORT}</p>
        </div>
      </div>
    </section>
  )
}

function Footer() {
  return (
    <footer className="footer">
      <div className="container">
        <section id="disclaimer" className="disclaimer" aria-labelledby="disclaimer-title">
          <h2 id="disclaimer-title" className="title-1"><Scales size={18} aria-hidden /> 투자 유의사항 및 면책 고지</h2>
          <ol>
            {DISCLAIMER_FULL.map((t) => <li key={t}>{t}</li>)}
          </ol>
          <p><strong>AI 도구 비용.</strong> {AI_PAID} 요금과 이용 조건은 각 서비스의 최신 안내를 확인하세요.</p>
        </section>
        <div className="footer-grid">
          <div>
            <p className="brand"><img src="./img/logo-64.png" alt="" width="20" height="20" /> Omni AI Trading</p>
            <p>
              <a href="https://github.com/HKUDS/Vibe-Trading">HKUDS Vibe-Trading</a>(MIT) 위에 국내 시장 자동매매를 더한 비공식 확장입니다.
              NH투자증권과 제휴·후원 관계가 없으며 투자 조언이 아닙니다. 모의 성과와 가상 체결은 실제 수익을 보장하지 않습니다.
            </p>
          </div>
          <div>
            <h3 className="title-3">문서</h3>
            <ul>
              <li><a href={doc('getting-started.md')}>시작하기</a></li>
              <li><a href={doc('safety.md')}>안전장치</a></li>
              <li><a href={doc('integrations.md')}>연동 서비스</a></li>
              <li><a href="./reports/">리포트 보관함</a></li>
            </ul>
          </div>
          <div>
            <h3 className="title-3">프로젝트</h3>
            <ul>
              <li><a href={REPO}>GitHub 저장소</a></li>
              <li><a href={`${REPO}/releases/tag/v${VERSION}`}>v{VERSION} 릴리스 노트</a></li>
              <li><a href={`${REPO}/blob/main/LICENSE`}>라이선스</a> · <a href={`${REPO}/blob/main/NOTICE`}>NOTICE</a></li>
              <li>이 페이지는 쿠키와 분석 도구를 쓰지 않습니다.</li>
            </ul>
          </div>
        </div>
      </div>
    </footer>
  )
}

export default function App() {
  useReveal()
  return (
    <>
      <a className="skip-link button-sm" href="#main">본문으로 건너뛰기</a>
      <Nav />
      <main id="main">
        <span id="top" />
        <Hero />
        <Why />
        <AiSearch />
        <Video />
        <Principle />
        <AiOps />
        <Cli />
        <Art />
        <Jev />
        <Longterm />
        <Dca />
        <Styles />
        <Data />
        <Daily />
        <Extend />
        <StackSection />
        <Integrations />
        <Safety />
        <Start />
      </main>
      <Footer />
    </>
  )
}
