import { useEffect } from 'react'

const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches

// Sections rise in once as they enter (marketing page: explanation, never replayed on scroll back).
export function useReveal() {
  useEffect(() => {
    const items = [...document.querySelectorAll<HTMLElement>('[data-reveal]')]
    if (reduced() || !('IntersectionObserver' in window)) {
      items.forEach((el) => el.setAttribute('data-shown', ''))
      return
    }
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) continue
        e.target.setAttribute('data-shown', '')
        io.unobserve(e.target)
      }
    }, { rootMargin: '0px 0px -12% 0px' })
    items.forEach((el) => io.observe(el))
    return () => io.disconnect()
  }, [])
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
