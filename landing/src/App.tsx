import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import { Tabs, TabsIndicator, TabsList, TabsPanel, TabsTab } from '@ui-sdk/react/tabs'
import { Dialog, DialogBackdrop, DialogClose, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from '@ui-sdk/react/dialog'
import { Accordion, AccordionHeader, AccordionItem, AccordionPanel, AccordionTrigger } from '@ui-sdk/react/accordion'
import {
  ArrowDown, ArrowRight, ArrowUpRight, Bank, BellRinging, CaretDown, Check, Copy, GithubLogo, List, ListChecks, MagnifyingGlassPlus,
  ArrowsClockwise, ChatsCircle, ClipboardText, Compass, Lightning, LockKey, Monitor, PiggyBank, PlugsConnected, Moon, Notebook, Plant, Robot, Scales, ShieldCheck, Stack, Sun, TrendDown, UserCircle, Warning, X,
} from '@phosphor-icons/react'
import {
  doc, FAQ, FEATURES, LIMITS, NAV, PROMPT, REPO, SAFETY_STAGES, SCREENS, START, STEPS, TAGLINE, VERSION,
  AI_LOOP, AI_PAID, ARCH_LAYERS, CHAT, DAILY, DCA_AXES, DCA_PROFILES, DISCLAIMER_FULL, DISCLAIMER_SHORT, HIGHLIGHTS, INTEGRATIONS,
  JEV_INPUTS, JEV_TRUST, JEV_USES, LONGTERM_POINTS, MASTERS, PILLARS, REGIMES, TRAPS, WHY_STATS, BUY_CRAFT, SELL_ART, LIFECYCLE, LIFECYCLE_EXITS, REGIME_PLAYS, STYLES, GROW, DATA_JSON, DATA_SQLITE, CALIBRATION,
} from './content'
import { useNavState, useReveal, useTaglineWords } from './motion'
// Charts load in their own chunk; the placeholder keeps the height so nothing jumps.
const ScheduleChart = lazy(() => import('./Charts').then((m) => ({ default: m.ScheduleChart })))
const chart = (el: React.ReactNode, h: number) => <Suspense fallback={<div className="chart-slot" style={{ height: h }} />}>{el}</Suspense>

const ICONS = { TrendDown, Bank, Plant, Notebook, BellRinging, ListChecks, Scales, Stack, ShieldCheck, ArrowsClockwise, ChatsCircle, LockKey,
  Lightning, Compass, PiggyBank, ClipboardText, PlugsConnected } as const
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
        <p className="kicker hero-step" style={css({ '--d': 0 })}>국내 ETF 실시간 매매 · 미국 ETF 장기투자 · 오픈소스</p>
        <h1 id="hero-title" className="heading-1 hero-title hero-step" style={css({ '--d': 1 })}>
          손절을 망설이는 순간에도<br />AI는 원칙대로 움직입니다
        </h1>
        <p className="lead hero-sub hero-step" style={css({ '--d': 2 })}>
          투자에서 지는 이유는 정보가 아니라 <mark className="hl">망설임</mark>입니다. AI 트레이더 Jev는 <strong className="em">수급과 시황으로 주도주를 찾아 1분마다 판단</strong>하고,
          <strong className="em">거장의 원칙은 장기 자산을 천천히 키웁니다.</strong> 모든 판단과 주문은 기록으로 남아 매일 복기됩니다.
        </p>
        <div className="hero-cta hero-step" style={css({ '--d': 3 })}>
          <a className="btn btn-primary button-md" href={START}><GithubLogo size={18} aria-hidden />GitHub에서 시작하기</a>
          <a className="more button-md" href={doc('README.md')}>가이드 문서 보기 <ArrowRight size={16} aria-hidden /></a>
        </div>
        <p className="proof small hero-step" style={css({ '--d': 3 })}>
          <ShieldCheck size={16} aria-hidden /><span>기본은 모의계좌</span><span>실계좌 하루 손실 한도 2만 원</span><span>자동 테스트 1,998개</span>
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
            alt="Omni AI Trading 대시보드 종합 현황 화면. 계좌별 오늘 실현손익, 잔고, 매매 프로그램 상태 카드가 보입니다." />
        </figure>
        <img className="hero-mascot" src={img('mascot-wave')} alt="" width="360" height="360" />
      </div>
      <nav className="container pillars-wrap" aria-label="이 페이지에서 볼 여섯 가지">
        <p className="caption muted pillars-title">이 페이지에서 볼 여섯 가지</p>
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

function Why() {
  return (
    <section id="why" className="section band why" aria-labelledby="why-title">
      <div className="container">
        <div className="head-split">
        <div className="head">
          <p className="kicker">왜 AI로 투자해야 할까요</p>
          <h2 id="why-title" className="heading-2">대부분의 손실은 분석이 아니라<br />망설임에서 시작됩니다</h2>
          <p className="lead">
            차트를 볼 줄 몰라서가 아닙니다. <strong className="em">손실 앞에서 머뭇거리고 이익 앞에서 조급해지는 건</strong>, 사람이라서 그렇습니다.
            연구 결과도 이런 실수가 수익을 깎아 먹는다는 것을 보여 줍니다.
          </p>
        </div>
        <img className="art art-md" src={img('mascot-why')} alt="" width="320" height="320" loading="lazy" />
        </div>
        <ul className="why-stats" data-reveal>
          {WHY_STATS.map((s) => (
            <li key={s.num} className="why-stat">
              <strong className="why-num">{s.num}</strong>
              <span className="small">{rich(s.label)}</span>
              <a className="caption muted why-src" href={s.href} target="_blank" rel="noopener noreferrer">{s.source}<ArrowUpRight size={12} aria-hidden /></a>
            </li>
          ))}
        </ul>
        <h3 className="title-1 traps-title">사람이 빠지는 여섯 가지 함정, Omni는 이렇게 막습니다</h3>
        <ol className="traps" data-reveal>
          {TRAPS.map((t, i) => (
            <li key={t.trap} className="trap">
              <span className="trap-no" aria-hidden>{String(i + 1).padStart(2, '0')}</span>
              <h4 className="title-2">{t.trap}</h4>
              <p className="small muted trap-human">{rich(t.human)}</p>
              <p className="small trap-fix"><ShieldCheck size={16} aria-hidden /><span>{rich(t.fix)}</span></p>
            </li>
          ))}
        </ol>
        <p className="why-close heading-4">
          사람이 약해서가 아니라, 사람이라서 그렇습니다.<br />
          <span className="accent-text">그래서 원칙을 지키는 일을 AI에게 맡겼습니다.</span>
        </p>
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
    <section id="art" className="section band" aria-labelledby="art-title">
      <div className="container">
        <div className="art-head">
          <p className="kicker">투자 기법 · 오래된 증시 격언</p>
          <h2 id="art-title" className="art-title">매수는 기술,<br /><span className="accent-text">매도는 예술</span></h2>
          <p className="lead">
            사는 이유는 차트와 수급으로 설명할 수 있지만, 파는 순간에는 욕심과 공포가 끼어듭니다. 고점은 아무도 모르니까요.
            Omni는 그 예술을 <mark className="hl">감정이 끼어들 틈 없는 규칙</mark>으로 바꿨습니다.
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
            <h3 className="heading-4">매도 · <span className="accent-text">예술을 규칙으로</span></h3>
            <ol className="craft-list">
              {SELL_ART.map((x, i) => (
                <li key={x.title}><span className="craft-no" aria-hidden>{i + 1}</span><div><strong className="title-2">{x.title}</strong><p className="small muted">{rich(x.body)}</p></div></li>
              ))}
            </ol>
          </div>
        </div>
        <figure className="life" data-reveal>
          <figcaption className="title-1">한 포지션의 일생</figcaption>
          <ol className="life-steps" aria-label="진입부터 스윙까지 다섯 단계">
            {LIFECYCLE.map((x, i) => (
              <li key={x.step} className="life-step" style={css({ '--i': i })}>
                <strong className="title-2">{x.step}</strong>
                <span className="caption muted">{x.body}</span>
              </li>
            ))}
          </ol>
          <div className="life-exits">
            <span className="caption muted">어느 단계에서든 아래 조건에 먼저 닿으면 정리합니다</span>
            <ul>{LIFECYCLE_EXITS.map((e) => <li key={e} className={/손절|손실 한도/.test(e) ? 'tag tag-warn' : 'tag'}>{e}</li>)}</ul>
          </div>
        </figure>
        <p className="why-close heading-4">물타기 대신 분할, 버티기 대신 손절,<br /><span className="accent-text">조급한 익절 대신 추적 청산.</span></p>
        <p className="more-row"><a className="more button-sm" href={doc('strategy.md')}>진입·청산 규칙 전체 보기 <ArrowUpRight size={16} aria-hidden /></a></p>
      </div>
    </section>
  )
}

const TONE_ICON = { up: '▲', wait: '■', down: '▼' } as const

function Jev() {
  return (
    <section id="jev" className="section" aria-labelledby="jev-title">
      <div className="container">
        <div className="head-split">
          <div className="head">
            <p className="kicker">Jev 실시간 AI 트레이더</p>
            <h2 id="jev-title" className="heading-2">수급과 시황으로 주도주를 찾고, 1분마다 판단합니다</h2>
            <p className="lead">
              Jev 트레이딩의 핵심은 <mark className="hl">오늘 시장의 돈이 어디로 가는지 읽고, 그 중심에 있는 종목을 공략</mark>하는 것입니다.
              Jev는 TypeSafe의 이미지·선택형 판단 모델입니다. 국내 ETF와 주식을 장중 1분마다 보고 종목 하나(또는 대기)와 신뢰도를 고릅니다.
              진입, 보유, 관심종목, 낙폭과대, 재진입, 개장 이월까지 실시간 매매의 판단을 맡습니다.
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
            <p className="small muted">답마다 신뢰도와 근거 질문 7개(결정 요인, 차트·뉴스·시황·수급 방향, 자료 충분성)를 받습니다.</p>
            <span className="tag">신뢰도는 수익 확률이 아닙니다</span>
          </li>
          <li className="pipe-stage focal" style={css({ '--s': 2 })}>
            <span className="pipe-no">03 · 규칙</span>
            <h3 className="title-1">규칙이 정하는 것</h3>
            <ul className="gate small">
              <li><Check size={14} aria-hidden />진입 공통 확인</li>
              <li><Check size={14} aria-hidden />신선도·분봉 상승·지수 방향</li>
              <li><Check size={14} aria-hidden />재진입·인버스 관문</li>
              <li><Check size={14} aria-hidden />매수 크기 계산</li>
              <li><Check size={14} aria-hidden />주문 가드 한도</li>
            </ul>
          </li>
          <li className="pipe-stage" style={css({ '--s': 3 })}>
            <span className="pipe-no">04 · 실행</span>
            <h3 className="title-1">주문과 기록</h3>
            <p className="small muted">모두 통과한 판단만 주문이 됩니다. 판단, 근거, 차단 사유, 체결이 기록에 남습니다.</p>
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
            <h3 className="heading-3">믿고 쓸 수 있게 만든 장치</h3>
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
        <p className="small muted regime-note">지정학·해외 이슈는 별도 규칙이 아니라 90분 뉴스, 해외 선물·원자재·환율 입력으로 Jev 판단에 들어갑니다.</p>
        <p className="more-row"><a className="more button-sm" href={doc('strategy.md')}>매매 전략 가이드 <ArrowUpRight size={16} aria-hidden /></a></p>
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
    <section id="longterm" className="section band" aria-labelledby="longterm-title">
      <div className="container">
        <div className="head-split">
          <div className="head">
            <p className="kicker">거장의 안목, 장기투자</p>
            <h2 id="longterm-title" className="heading-2">시장의 계절을 읽고, 그 계절의 거장 원칙으로 운용합니다</h2>
            <p className="lead">
              <mark className="hl">빠르지는 않지만, 천천히 부자가 되는 길</mark>입니다. 시장이 무너질 때 겁먹지 않고 과열될 때 들뜨지 않도록 거장들의 원칙을 미리 정해 둡니다.
              국내 상장 미국 지수·테마 ETF 6종을 코어 60 · 위성 25 · 저수지 15로 담습니다. 매일 아침 나스닥100 낙폭, 200일선, VIX로
              시장의 계절을 정하고, 켜 둔 거장 전략이 목표 비중을 조정합니다. 거장이 직접 운용하는 것이 아니라 공개된 원칙을 본뜬 규칙입니다.
            </p>
          </div>
          <img className="art art-md" src={img('mascot-masters')} alt="" width="280" height="280" loading="lazy" />
        </div>
        <div className="regimes" role="group" aria-label="시장의 계절 고르기. 위험이 큰 순서입니다">
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
        <p className="caption muted strip-note">같은 판정이 10거래일(투매는 2거래일) 이어져야 계절이 바뀝니다. 추천은 보여 주기만 하고, 고르는 것은 사람입니다.</p>
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
    <section id="dca" className="section" aria-labelledby="dca-title">
      <div className="container">
        <div className="head-split">
          <div className="head">
            <p className="kicker">적립식 투자</p>
            <h2 id="dca-title" className="heading-2">매달 넣는 돈을 거장의 리듬으로 꾸준히</h2>
            <p className="lead">
              새로 넣는 적립금은 적립식이, 계좌에 있는 운용금은 계좌 전략이 맡습니다. 언제, 얼마나, 무엇을, 몇 시에 살지를 항목마다
              거장에게 맡기거나 직접 정합니다. 적립식은 사기만 하고, 파는 일은 거장 전략의 몫입니다.
            </p>
          </div>
          <img className="art art-sm" src={img('mascot-dca')} alt="" width="280" height="280" loading="lazy" />
        </div>
        <div className="dca">
          <div className="panel">
            <div className="seg" data-side={side} role="group" aria-label="적립식 네 가지 선택을 누가 정할지">
              <span className="seg-thumb" aria-hidden />
              <button type="button" aria-pressed={side === 'master'} onClick={() => setSide('master')}>거장에게 맡기기</button>
              <button type="button" aria-pressed={side === 'me'} onClick={() => setSide('me')}>내가 정하기</button>
            </div>
            <dl className="axes" aria-live="polite">
              {DCA_AXES.map((a) => (
                <div key={a.axis}><dt className="title-2">{a.axis}</dt><dd className="small">{side === 'master' ? a.master : a.me}</dd></div>
              ))}
            </dl>
          </div>
          <div>
            <h3 className="heading-3">거장 리듬 7가지</h3>
            <dl className="profiles" style={{ marginTop: 16 }}>
              {DCA_PROFILES.map((p) => {
                const [name, rest] = p.split(' · ')
                return <div key={p}><dt className="title-3">{name}</dt><dd className="small muted">{rest}</dd></div>
              })}
            </dl>
            <p className="caption muted" style={{ marginTop: 16 }}>장기투자 차트에는 다음 적립 매수선과 여운봉 매매법 단계가 함께 그려집니다.</p>
          </div>
        </div>
        <p className="more-row"><a className="more button-sm" href={doc('longterm-dca.md')}>적립식 가이드 <ArrowUpRight size={16} aria-hidden /></a></p>
      </div>
    </section>
  )
}

function Styles() {
  return (
    <section id="styles" className="section band" aria-labelledby="styles-title">
      <div className="container">
        <div className="head">
          <p className="kicker">내 투자 스타일</p>
          <h2 id="styles-title" className="heading-2">투자 성향에 맞춰 고르고, 기록으로 키웁니다</h2>
          <p className="lead">빠른 트레이딩, 천천히 부자가 되는 장기투자, 또는 둘 다. <strong className="em">시간이 지날수록 내 기록과 선택이 쌓여 나에게 맞는 시스템</strong>이 됩니다.</p>
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
        <ol className="grow" data-reveal aria-label="키우는 법 세 단계">
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
    <section id="data" className="section" aria-labelledby="data-title">
      <div className="container">
        <div className="head">
          <p className="kicker">단순한 구조</p>
          <h2 id="data-title" className="heading-2">관점은 JSON에,<br />경험은 SQLite에 쌓입니다</h2>
          <p className="lead">
            <strong className="em">서버도 클라우드 DB도 없이 내 컴퓨터의 파일로</strong> 움직입니다. 대시보드에서 바꾼 관점과 설정은 JSON으로, 매일의 판단과 체결은 SQLite로 남아
            다음 판단의 근거가 됩니다. 백업 한 번이면 다른 Mac으로 그대로 옮길 수 있습니다.
          </p>
        </div>
        <div className="data-flow" data-reveal>
          <div className="data-col">
            <h3 className="title-1"><span className="file-kind">JSON</span> 관점과 설정</h3>
            <ul>{DATA_JSON.map((d) => <li key={d.file}><code>{d.file}</code><span className="small muted">{d.body}</span></li>)}</ul>
          </div>
          <div className="data-arrow" aria-hidden><span>매분 판단</span><ArrowRight size={20} /><span>매일 복기</span></div>
          <div className="data-col">
            <h3 className="title-1"><span className="file-kind">SQLite</span> 경험과 학습 데이터</h3>
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
        <div className="head">
          <p className="kicker">AI 운영</p>
          <h2 id="ai-title" className="heading-2">Claude Code·Codex와 채팅하며 운영합니다</h2>
          <p className="lead">
            설치부터 매일의 점검, 분석, 리포트까지 AI 코딩 에이전트에게 말로 맡깁니다. 프로젝트 스킬 9개와 예약 작업 15개가
            사람이 없을 때도 장전 분석, 장중 감사, 매매일지를 씁니다. 주문은 채팅이 아니라 실행기와 주문 가드가 냅니다.
          </p>
        </div>
        <div className="ai-ops">
          <figure className="chat" aria-label="채팅 운영 예시">
            <div className="chat-head">
              <img src={img('mascot-ai-chat')} alt="" width="160" height="160" loading="lazy" />
              <div><p className="title-2">Omni 운영 채팅</p><p className="caption muted">Claude Code · Codex</p></div>
              <span className="dot" aria-hidden />
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
            <p className="small muted" style={{ marginTop: 20 }}>AI 도구는 유료 요금제가 필요합니다.</p>
            <p className="more-row" style={{ marginTop: 12 }}><a className="more button-sm" href={doc('ai-agents.md')}>AI와 함께 쓰기 가이드 <ArrowUpRight size={16} aria-hidden /></a></p>
          </div>
        </div>
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
          <p className="kicker">매일의 감사와 리포트</p>
          <h2 id="daily-title" className="heading-2">사람이 자리를 비워도, 매일 점검하고 보고합니다</h2>
          <p className="lead">
            평일 한국 시간 기준입니다. 리포트는 HTML 페이지로 게시되고 Discord·Telegram·Slack으로 링크가 갑니다. 개선안은 제안만 하고, 반영은 사람이 승인합니다.
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

function Integrations() {
  const data = INTEGRATIONS.find((g) => g.group === '데이터')!
  const alerts = INTEGRATIONS.find((g) => g.group === '알림·게시')!
  const link = (it: { anchor?: string; doc?: string }) => (it.doc ? doc(it.doc) : `${doc('integrations.md')}#${it.anchor}`)
  return (
    <section id="integrations" className="section band" aria-labelledby="integrations-title">
      <div className="container">
        <div className="head-split">
          <div className="head">
            <p className="kicker">연동 생태계와 구조</p>
            <h2 id="integrations-title" className="heading-2">증권사, AI, 공공 데이터, 알림을 한 구조로 묶었습니다</h2>
            <p className="lead">
              필수는 NH 나무 PLUG 하나입니다. 나머지는 키를 넣는 만큼 기능이 늘고, 비어 있으면 그 기능만 빠집니다. 키는 내 컴퓨터의 .env에만 둡니다.
            </p>
          </div>
          <img className="art art-sm" src={img('mascot-integrations')} alt="" width="280" height="280" loading="lazy" />
        </div>
        <div className="stack" role="group" aria-label="시스템 구조도. 왼쪽에서 데이터가 들어와 가운데 층을 위에서 아래로 거쳐 주문이 되고, 오른쪽으로 알림이 발송됩니다">
          <div className="stack-side in">
            <p className="kicker">데이터 · 들어옴</p>
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
            <p className="kicker">알림 · 나감</p>
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
    <section id="safety" className="section" aria-labelledby="safety-title">
      <div className="container">
        <div className="head-split">
          <div className="head">
            <p className="kicker">안전장치와 대시보드</p>
            <h2 id="safety-title" className="heading-2">실계좌 주문은 세 단계를 거쳐야 실행됩니다</h2>
            <p className="lead">처음에는 계획만 기록하고, 가상으로 사고팔아 본 뒤, 계좌마다 스위치와 확인 문구를 거쳐야 실제 주문이 실행됩니다.</p>
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
            <h3 className="heading-4">AI도 틀립니다. 그래서 매일 검증합니다</h3>
            <p className="small muted">
              첫 주(11/11~11/17, 공유 모의계좌) Jev 진입 신호를 신뢰도 구간별로 5·15·30분 뒤 결과와 비교해 보니, <mark className="hl">신뢰도가 높을수록 오히려 덜 맞았습니다.</mark>
              그래서 <strong className="em">신뢰도를 수익 확률로 쓰지 않고</strong>, 손절과 하루 손실 한도를 늘 함께 두고, 장 마감마다 구간별 적중률을 다시 확인합니다.
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
          <h3 className="heading-3">로컬 대시보드 10개 탭에서 모두 확인합니다</h3>
          <p className="small muted" style={{ marginTop: 8, maxWidth: 680 }}>
            Streamlit 대시보드는 내 컴퓨터에서만 열리며, 보기와 설정만 하고 실제 주문을 직접 내지 않습니다. 사진의 계좌번호는 가렸습니다.
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

function CopyButton({ text }: { text: string }) {
  const [state, setState] = useState<'idle' | 'done' | 'fail'>('idle')
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); setState('done') } catch { setState('fail') }
    setTimeout(() => setState('idle'), 2000)
  }
  return (
    <>
      <button type="button" className="btn btn-ghost btn-sm button-sm copy-btn" onClick={copy} aria-describedby="copy-status">
        {state === 'done' ? <Check size={16} aria-hidden /> : <Copy size={16} aria-hidden />}
        {state === 'done' ? '복사했습니다' : '프롬프트 복사'}
      </button>
      <span id="copy-status" className="sr-only" role="status">
        {state === 'done' ? '프롬프트를 복사했습니다' : state === 'fail' ? '복사하지 못했습니다. 직접 선택해 주세요' : ''}
      </span>
    </>
  )
}

function Start() {
  return (
    <section id="start" className="section band" aria-labelledby="start-title">
      <div className="container">
        <div className="head">
          <p className="kicker">시작하기</p>
          <h2 id="start-title" className="heading-2">세 단계로 모의매매까지 준비합니다</h2>
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
            <h2 className="heading-3">시작하기 전에 많이 묻는 것</h2>
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
          <h2 className="heading-2">모의계좌에서 먼저 돌려 보세요</h2>
          <p className="lead">설치는 AI 에이전트에게 맡기고, 주문 서비스는 내가 승인할 때만 켭니다.</p>
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
        <Principle />
        <Art />
        <Jev />
        <Longterm />
        <Dca />
        <Styles />
        <Data />
        <AiOps />
        <Daily />
        <Integrations />
        <Safety />
        <Start />
      </main>
      <Footer />
    </>
  )
}
