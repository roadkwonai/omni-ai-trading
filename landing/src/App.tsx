import { useEffect, useState } from 'react'
import { Tabs, TabsIndicator, TabsList, TabsPanel, TabsTab } from '@ui-sdk/react/tabs'
import { Dialog, DialogBackdrop, DialogClose, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from '@ui-sdk/react/dialog'
import { Accordion, AccordionHeader, AccordionItem, AccordionPanel, AccordionTrigger } from '@ui-sdk/react/accordion'
import {
  ArrowRight, ArrowUpRight, Bank, BellRinging, CaretDown, Check, Copy, GithubLogo, List, ListChecks, MagnifyingGlassPlus,
  Monitor, Moon, Notebook, Plant, ShieldCheck, Sun, TrendDown, X,
} from '@phosphor-icons/react'
import {
  DAY, doc, ENGINES, FAQ, FEATURES, LIMITS, NAV, NUMBERS, PROBLEMS, PROMPT, REPO, SAFETY_STAGES, SCREENS, START, STEPS, TAGLINE,
} from './content'
import { useReveal, useTaglineWords } from './motion'

const ICONS = { TrendDown, Bank, Plant, Notebook, BellRinging, ListChecks } as const
const img = (name: string) => `./img/${name}.webp`
const shot = (id: string) => `./img/screens/${id}.webp`

type Theme = 'system' | 'light' | 'dark'
const THEME_KEY = 'omni-report-theme' // shared with the report pages on the same site

function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => {
    try { return (localStorage.getItem(THEME_KEY) as Theme) || 'system' } catch { return 'system' }
  })
  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') root.removeAttribute('data-theme'); else root.setAttribute('data-theme', theme)
    try { theme === 'system' ? localStorage.removeItem(THEME_KEY) : localStorage.setItem(THEME_KEY, theme) } catch { /* private mode */ }
  }, [theme])
  const next = () => setTheme((t) => (t === 'system' ? 'light' : t === 'light' ? 'dark' : 'system'))
  return { theme, next }
}

function ThemeButton() {
  const { theme, next } = useTheme()
  const name = { system: '시스템', light: '라이트', dark: '다크' }[theme]
  const Icon = { system: Monitor, light: Sun, dark: Moon }[theme]
  return (
    <button type="button" className="icon-btn" onClick={next} aria-label={`화면 테마 ${name}, 눌러서 바꾸기`} title={`테마: ${name}`}>
      <Icon size={18} aria-hidden />
    </button>
  )
}

function Nav() {
  const [open, setOpen] = useState(false)
  useEffect(() => {
    document.body.toggleAttribute('data-menu-open', open)
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  }, [open])
  return (
    <header className="nav-wrap">
      <nav className="nav" aria-label="주 메뉴">
        <a className="brand title-4" href="#top" aria-label="Omni AI Trading 처음으로">
          <img src="./img/logo-64.png" alt="" width="24" height="24" />
          Omni AI Trading
        </a>
        <ul className="nav-links">
          {NAV.map((n) => <li key={n.href}><a className="body-5" href={n.href}>{n.label}</a></li>)}
        </ul>
        <div className="nav-tools">
          <ThemeButton />
          <a className="btn btn-primary btn-sm button-sm nav-cta" href={START}>GitHub에서 시작하기</a>
          <button type="button" className="icon-btn menu-btn" aria-expanded={open} aria-controls="mobile-menu"
            aria-label={open ? '메뉴 닫기' : '메뉴 열기'} onClick={() => setOpen((o) => !o)}>
            <span className="menu-icon" data-open={open || undefined} aria-hidden><List size={20} /><X size={20} /></span>
          </button>
        </div>
      </nav>
      <div id="mobile-menu" className="mobile-menu" data-open={open || undefined} hidden={!open}>
        <ul>
          {NAV.map((n, i) => (
            <li key={n.href} style={{ '--i': i } as React.CSSProperties}>
              <a className="heading-5" href={n.href} onClick={() => setOpen(false)}>{n.label}</a>
            </li>
          ))}
          <li style={{ '--i': NAV.length } as React.CSSProperties}>
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
        <p className="kicker caption-1 hero-step" style={{ '--d': 0 } as React.CSSProperties}>NH 나무 PLUG · Jev · vibe-trading</p>
        <h1 id="hero-title" className="heading-1 hero-title hero-step" style={{ '--d': 1 } as React.CSSProperties}>
          AI가 시황을 읽고<br />규칙 안에서 매매합니다
        </h1>
        <p className="body-3 hero-sub hero-step" style={{ '--d': 2 } as React.CSSProperties}>
          국내 ETF와 주식은 1분마다 차트와 뉴스로 판단하고, 미국 ETF는 거장 전략으로 천천히 모읍니다.
          모든 판단과 주문, 체결이 원장과 대시보드에 남습니다.
        </p>
        <div className="hero-cta hero-step" style={{ '--d': 3 } as React.CSSProperties}>
          <a className="btn btn-primary button-md" href={START}><GithubLogo size={18} aria-hidden />GitHub에서 시작하기</a>
          <a className="text-link button-md" href={doc('README.md')}>가이드 문서 보기 <ArrowRight size={16} aria-hidden /></a>
        </div>
        <p className="proof caption-2 hero-step" style={{ '--d': 4 } as React.CSSProperties}>
          <ShieldCheck size={16} aria-hidden /> 기본은 모의계좌 · 실계좌 하루 손실 2만 원 래치 · 자동 테스트 1,998개
        </p>
      </div>
      <div className="container hero-visual hero-step" style={{ '--d': 5 } as React.CSSProperties}>
        <figure className="browser">
          <div className="browser-bar" aria-hidden><span /><span /><span /></div>
          <img src={shot('overview')} width="1600" height="1000"
            alt="Omni AI Trading 대시보드 종합 현황 화면. 계좌별 오늘 실현손익, 잔고, 실행기 상태 카드가 보입니다." />
        </figure>
        <img className="hero-mascot" src={img('mascot-wave')} alt="" width="360" height="360" />
      </div>
    </section>
  )
}

function Problems() {
  return (
    <section className="section" aria-labelledby="problems-title" data-reveal>
      <div className="container">
        <p className="kicker caption-1">왜 만들었나</p>
        <h2 id="problems-title" className="heading-3 section-title">AI 자동매매가 불안한 세 가지 이유</h2>
        <ul className="grid-3">
          {PROBLEMS.map((p) => (
            <li key={p.worry} className="card">
              <h3 className="title-2">{p.worry}</h3>
              <p className="body-6 muted"><Check size={16} aria-hidden className="accent" /> {p.answer}</p>
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}

function Engines() {
  return (
    <section id="engines" className="section" aria-labelledby="engines-title" data-reveal>
      <div className="container">
        <p className="kicker caption-1">두 엔진</p>
        <h2 id="engines-title" className="heading-3 section-title">빠른 판단과 느린 축적을 따로 돌립니다</h2>
        <p className="body-3 muted section-lead">두 엔진은 운영 엔진 스위치로 각각 켜고 끄며, 서로의 장부와 주문에 섞이지 않습니다.</p>
        <Tabs defaultValue="jev" className="engines">
          <TabsList aria-label="엔진 고르기" className="seg">
            {ENGINES.map((e) => <TabsTab key={e.id} value={e.id} className="seg-tab button-sm">{e.tab}</TabsTab>)}
            <TabsIndicator className="seg-indicator" />
          </TabsList>
          <div className="stack">
            {ENGINES.map((e) => (
              <TabsPanel key={e.id} value={e.id} className="engine-panel">
                <div className="engine-copy">
                  <img src={img(e.mascot)} alt="" width="160" height="160" className="engine-mascot" loading="lazy" />
                  <h3 className="heading-5">{e.title}</h3>
                  <p className="body-3 muted">{e.lead}</p>
                  <ul className="checks">
                    {e.points.map((p) => <li key={p} className="body-6"><Check size={16} aria-hidden className="accent" />{p}</li>)}
                  </ul>
                  <a className="text-link button-sm" href={e.link.href}>{e.link.label} <ArrowUpRight size={14} aria-hidden /></a>
                </div>
                <figure className="browser engine-shot">
                  <div className="browser-bar" aria-hidden><span /><span /><span /></div>
                  <img src={shot(e.screen)} width="1600" height="1000" loading="lazy"
                    alt={`${e.tab} 화면: ${SCREENS.find((s) => s.id === e.screen)?.caption ?? ''}`} />
                </figure>
              </TabsPanel>
            ))}
          </div>
        </Tabs>
      </div>
    </section>
  )
}

function Tagline() {
  useTaglineWords('tagline')
  let n = 0
  return (
    <section className="section tagline" aria-label="핵심 원칙">
      <div className="container">
        <p id="tagline" className="heading-2 tagline-text">
          {TAGLINE.map((line) => (
            <span key={line} className="tagline-line">
              {line.split(' ').map((w) => <span key={`${w}-${n}`} data-word style={{ '--w': n++ } as React.CSSProperties}>{w} </span>)}
            </span>
          ))}
        </p>
      </div>
    </section>
  )
}

function Features() {
  return (
    <section id="features" className="section" aria-labelledby="features-title" data-reveal>
      <div className="container">
        <p className="kicker caption-1">기능</p>
        <h2 id="features-title" className="heading-3 section-title">매매 하나에 필요한 일을 다 묶었습니다</h2>
        <ul className="grid-3">
          {FEATURES.map((f) => {
            const Icon = ICONS[f.icon as keyof typeof ICONS]
            return (
              <li key={f.title}>
                <a className="card card-link" href={f.href}>
                  <span className="icon-chip" aria-hidden><Icon size={20} /></span>
                  <h3 className="title-2">{f.title}</h3>
                  <p className="body-6 muted">{f.body}</p>
                  <span className="caption-1 accent link-hint">가이드 <ArrowUpRight size={14} aria-hidden /></span>
                </a>
              </li>
            )
          })}
        </ul>
        <figure className="showcase">
          <img src={img('showcase-engines')} width="1600" height="900" loading="lazy"
            alt="왼쪽은 실시간 트레이딩 흐름도와 책상 앞의 부엉이, 오른쪽은 장기투자 차트와 나무에 물을 주는 부엉이를 방패 아이콘이 잇는 그림" />
        </figure>
      </div>
    </section>
  )
}

function Day() {
  return (
    <section id="day" className="section" aria-labelledby="day-title" data-reveal>
      <div className="container">
        <p className="kicker caption-1">하루 흐름</p>
        <h2 id="day-title" className="heading-3 section-title">개장 전 점검부터 장 마감 복기까지</h2>
        <p className="body-3 muted section-lead">평일 한국 시간 기준입니다. 정리 시각과 일부 일정은 설정과 휴장일, 시장 일정에 따라 달라집니다.</p>
        <ol className="timeline">
          {DAY.map((d) => (
            <li key={d.time}>
              <span className="time caption-3 tabular">{d.time}</span>
              <div>
                <h3 className="title-3">{d.title}</h3>
                <p className="body-6 muted">{d.body}</p>
              </div>
            </li>
          ))}
        </ol>
      </div>
    </section>
  )
}

function Safety() {
  return (
    <section id="safety" className="section" aria-labelledby="safety-title" data-reveal>
      <div className="container safety">
        <div>
          <p className="kicker caption-1">안전 장치</p>
          <h2 id="safety-title" className="heading-3 section-title">실계좌는 세 단계를 거쳐서 엽니다</h2>
          <ol className="stages">
            {SAFETY_STAGES.map((s) => (
              <li key={s.step} className="card">
                <span className="stage-no caption-3">{s.step}</span>
                <h3 className="title-3">{s.title}</h3>
                <p className="body-6 muted">{s.body}</p>
              </li>
            ))}
          </ol>
          <table className="limits body-6">
            <caption className="caption-2 muted">주문 한도 요약. 자세한 값은 안전 장치 가이드를 봅니다.</caption>
            <tbody>
              {LIMITS.map(([k, v]) => <tr key={k}><th scope="row" className="title-4">{k}</th><td>{v}</td></tr>)}
            </tbody>
          </table>
          <a className="text-link button-sm" href={doc('safety.md')}>안전 장치 가이드 <ArrowUpRight size={14} aria-hidden /></a>
        </div>
        <img className="safety-mascot" src={img('mascot-safety')} alt="" width="360" height="360" loading="lazy" />
      </div>
    </section>
  )
}

function Screens() {
  return (
    <section id="screens" className="section" aria-labelledby="screens-title" data-reveal>
      <div className="container">
        <p className="kicker caption-1">화면 둘러보기</p>
        <h2 id="screens-title" className="heading-3 section-title">로컬 대시보드 14개 탭에서 모두 확인합니다</h2>
        <p className="body-3 muted section-lead">Streamlit 대시보드는 내 컴퓨터에서만 열리며, 화면은 보기와 설정만 하고 실주문을 직접 내지 않습니다. 사진의 계좌번호는 가렸습니다.</p>
        <Tabs defaultValue={SCREENS[0].id} className="screens">
          <TabsList aria-label="화면 고르기" className="chips">
            {SCREENS.map((s) => <TabsTab key={s.id} value={s.id} className="chip button-xsm">{s.label}</TabsTab>)}
          </TabsList>
          <div className="stack">
            {SCREENS.map((s) => (
              <TabsPanel key={s.id} value={s.id} className="screen-panel">
                <Dialog>
                  <DialogTrigger className="zoom" aria-label={`${s.label} 화면 크게 보기`}>
                    <img src={shot(s.id)} width="1600" height="1000" loading="lazy" alt={`${s.label}: ${s.caption}`} />
                    <span className="zoom-hint caption-4"><MagnifyingGlassPlus size={14} aria-hidden /> 크게 보기</span>
                  </DialogTrigger>
                  <DialogBackdrop className="backdrop" />
                  <DialogContent className="lightbox">
                    <div className="lightbox-head">
                      <DialogTitle className="title-3">{s.label}</DialogTitle>
                      <DialogClose className="icon-btn" aria-label="닫기"><X size={18} aria-hidden /></DialogClose>
                    </div>
                    <DialogDescription className="caption-2 muted">{s.caption}</DialogDescription>
                    <img src={shot(s.id)} width="1600" height="1000" alt="" />
                  </DialogContent>
                </Dialog>
                <p className="caption-2 muted">{s.caption}</p>
              </TabsPanel>
            ))}
          </div>
        </Tabs>
      </div>
    </section>
  )
}

function Reports() {
  return (
    <section className="section" aria-labelledby="reports-title" data-reveal>
      <div className="container reports card">
        <img src={img('mascot-journal')} alt="" width="200" height="200" loading="lazy" />
        <div>
          <p className="kicker caption-1">리포트 보관함</p>
          <h2 id="reports-title" className="heading-4">매일 쓰는 일지와 리포트를 한곳에 모읍니다</h2>
          <p className="body-3 muted">장전 분석, 장 마감 개선안, 매매일지, 장기투자 전략 리포트를 날짜와 종류로 찾습니다. 리포트는 검색 엔진에 올리지 않습니다.</p>
          <a className="text-link button-md" href="./reports/">리포트 보관함 열기 <ArrowRight size={16} aria-hidden /></a>
        </div>
      </div>
    </section>
  )
}

function Numbers() {
  return (
    <section className="section" aria-labelledby="numbers-title" data-reveal>
      <div className="container">
        <h2 id="numbers-title" className="heading-5 section-title">숫자로 보는 Omni AI Trading</h2>
        <dl className="numbers">
          {NUMBERS.map((n) => (
            <div key={n.label} className="card">
              <dt className="caption-1 muted">{n.label}</dt>
              <dd className="heading-3 tabular">{n.value}</dd>
            </div>
          ))}
        </dl>
        <p className="caption-5 muted">2026년 11월 19일 저장소 코드에서 센 값입니다.</p>
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
    <button type="button" className="btn btn-ghost btn-sm button-sm" onClick={copy}>
      {state === 'done' ? <Check size={16} aria-hidden /> : <Copy size={16} aria-hidden />}
      {state === 'done' ? '복사했습니다' : state === 'fail' ? '복사하지 못했습니다. 직접 선택해 주세요' : '프롬프트 복사'}
      <span className="sr-only" aria-live="polite">{state === 'done' ? '프롬프트를 복사했습니다' : ''}</span>
    </button>
  )
}

function Start() {
  return (
    <section id="start" className="section" aria-labelledby="start-title" data-reveal>
      <div className="container">
        <p className="kicker caption-1">시작하기</p>
        <h2 id="start-title" className="heading-3 section-title">세 단계로 모의매매까지 준비합니다</h2>
        <ol className="steps">
          {STEPS.map((s, i) => (
            <li key={s.title} className="card">
              <span className="stage-no caption-3">{i + 1}</span>
              <h3 className="title-2">{s.title}</h3>
              <p className="body-6 muted">{s.body}</p>
              {s.code ? <pre className="code caption-5"><code>{s.code}</code></pre> : (
                <>
                  <pre className="code caption-5"><code>{PROMPT}</code></pre>
                  <CopyButton text={PROMPT} />
                </>
              )}
            </li>
          ))}
        </ol>
      </div>
    </section>
  )
}

function Faq() {
  return (
    <section id="faq" className="section" aria-labelledby="faq-title" data-reveal>
      <div className="container faq">
        <p className="kicker caption-1">자주 묻는 질문</p>
        <h2 id="faq-title" className="heading-3 section-title">시작하기 전에 확인하세요</h2>
        <Accordion className="accordion">
          {FAQ.map((f) => (
            <AccordionItem key={f.q} value={f.q} className="acc-item">
              <AccordionHeader className="acc-head">
                <AccordionTrigger className="acc-trigger title-3">{f.q}<CaretDown size={18} aria-hidden /></AccordionTrigger>
              </AccordionHeader>
              <AccordionPanel className="acc-panel body-6 muted">{f.a}</AccordionPanel>
            </AccordionItem>
          ))}
        </Accordion>
      </div>
    </section>
  )
}

function FinalCta() {
  return (
    <section className="section final" aria-labelledby="final-title" data-reveal>
      <div className="container final-inner">
        <h2 id="final-title" className="heading-3">모의계좌에서 먼저 돌려 보세요</h2>
        <p className="body-3 muted">설치는 AI 에이전트에게 맡기고, 주문 서비스는 내가 승인할 때만 켭니다.</p>
        <a className="btn btn-primary button-md" href={START}><GithubLogo size={18} aria-hidden />GitHub에서 시작하기</a>
      </div>
    </section>
  )
}

function Footer() {
  return (
    <footer className="footer">
      <div className="container footer-inner caption-2 muted">
        <p>
          Omni AI Trading은 <a href="https://github.com/HKUDS/Vibe-Trading">HKUDS Vibe-Trading</a>(MIT) 위에 국내 시장 자동매매를 더한 비공식 확장입니다.
          NH투자증권과 제휴·후원 관계가 없으며 투자 조언이 아닙니다. 모의 성과와 가상 체결은 실제 수익을 보장하지 않습니다.
        </p>
        <p className="footer-links">
          <a href={`${REPO}/blob/main/LICENSE`}>라이선스</a>
          <a href={`${REPO}/blob/main/NOTICE`}>NOTICE</a>
          <a href={doc('safety.md')}>안전 장치</a>
          <a href="./reports/">리포트</a>
          <span>이 페이지는 쿠키와 분석 도구를 쓰지 않습니다.</span>
        </p>
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
        <Problems />
        <Engines />
        <Tagline />
        <Features />
        <Day />
        <Safety />
        <Screens />
        <Reports />
        <Numbers />
        <Start />
        <Faq />
        <FinalCta />
      </main>
      <Footer />
    </>
  )
}
