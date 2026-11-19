import { useEffect, useState } from 'react'

const query = () => matchMedia('(prefers-reduced-motion: reduce)')
const reduced = () => query().matches

// Groups ([data-reveal]) and diagrams ([data-play]) show once as they enter; children stagger by index (--r).
// Reveal is explanation on a marketing page, never replayed on scroll back.
export function useReveal() {
  useEffect(() => {
    const items = [...document.querySelectorAll<HTMLElement>('[data-reveal], [data-play]')]
    document.querySelectorAll<HTMLElement>('[data-reveal]').forEach((g) =>
      [...g.children].forEach((c, i) => (c as HTMLElement).style.setProperty('--r', String(i))))
    const showAll = () => items.forEach((el) => el.setAttribute('data-shown', ''))
    if (reduced() || !('IntersectionObserver' in window)) { showAll(); return }
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) continue
        e.target.setAttribute('data-shown', '')
        io.unobserve(e.target)
      }
    }, { rootMargin: '0px 0px -10% 0px' })
    items.forEach((el) => io.observe(el))
    // turning reduced motion on mid-visit shows everything at once
    const mq = query()
    const onChange = () => { if (mq.matches) { io.disconnect(); showAll() } }
    mq.addEventListener('change', onChange)
    return () => { io.disconnect(); mq.removeEventListener('change', onChange) }
  }, [])
}

// Nav: shadow after scrolling, and which section is on screen (aria-current on its link).
export function useNavState(ids: string[]) {
  const [scrolled, setScrolled] = useState(false)
  const [current, setCurrent] = useState('')
  useEffect(() => {
    const onScroll = () => setScrolled(scrollY > 8)
    onScroll()
    addEventListener('scroll', onScroll, { passive: true })
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting) setCurrent(e.target.id)
    }, { rootMargin: '-45% 0px -50% 0px' })
    ids.forEach((id) => { const el = document.getElementById(id); if (el) io.observe(el) })
    return () => { removeEventListener('scroll', onScroll); io.disconnect() }
  }, [ids])
  return { scrolled, current }
}

// Tagline words light up in reading order as they cross the middle of the screen (one rAF per scroll burst).
export function useTaglineWords(id: string) {
  useEffect(() => {
    const root = document.getElementById(id)
    if (!root) return
    const words = [...root.querySelectorAll<HTMLElement>('[data-word]')]
    if (reduced()) {
      words.forEach((w) => w.setAttribute('data-lit', ''))
      return
    }
    let frame = 0
    const update = () => {
      frame = 0
      const box = root.getBoundingClientRect()
      const progress = (innerHeight * 0.75 - box.top) / (box.height + innerHeight * 0.25)
      const lit = Math.round(Math.min(1, Math.max(0, progress)) * words.length)
      words.forEach((w, i) => w.toggleAttribute('data-lit', i < lit))
    }
    const onScroll = () => { if (!frame) frame = requestAnimationFrame(update) }
    update()
    addEventListener('scroll', onScroll, { passive: true })
    addEventListener('resize', onScroll)
    return () => {
      removeEventListener('scroll', onScroll)
      removeEventListener('resize', onScroll)
      cancelAnimationFrame(frame)
    }
  }, [id])
}
