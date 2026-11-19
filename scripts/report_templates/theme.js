// Theme: one button cycles 시스템 → 라이트 → 다크 (saved per reader). Loaded in <head> so the page never flashes the wrong theme.
// Also keeps embedded archify diagrams on the same theme and marks the table-of-contents entry being read.
(() => {
  const KEY = 'omni-report-theme';
  const root = document.documentElement;
  const read = () => { try { return localStorage.getItem(KEY); } catch (e) { return null; } };
  const apply = (mode) => {
    if (mode === 'light' || mode === 'dark') root.setAttribute('data-theme', mode); else root.removeAttribute('data-theme');
    const dark = mode === 'dark' || (!mode && matchMedia('(prefers-color-scheme: dark)').matches);
    document.querySelectorAll('iframe[data-diagram]').forEach((f) => {
      try { f.contentDocument.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light'); } catch (e) { /* not same-origin */ }
    });
    const btn = document.querySelector('.theme-toggle');
    if (btn) {
      const name = { light: '라이트', dark: '다크' }[mode] || '시스템';
      btn.querySelector('.theme-name').textContent = name;
      btn.setAttribute('aria-label', `화면 테마 ${name}, 눌러서 바꾸기`);
    }
  };
  apply(read());
  addEventListener('DOMContentLoaded', () => {
    apply(read());
    document.querySelector('.theme-toggle')?.addEventListener('click', () => {
      const now = read();
      const next = now === 'light' ? 'dark' : now === 'dark' ? null : 'light';
      try { next ? localStorage.setItem(KEY, next) : localStorage.removeItem(KEY); } catch (e) { /* private mode */ }
      apply(next);
    });
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => apply(read()));
    document.querySelectorAll('iframe[data-diagram]').forEach((f) => f.addEventListener('load', () => apply(read())));
    const links = [...document.querySelectorAll('.toc a')];
    if (!links.length || !('IntersectionObserver' in window)) return;
    const byId = new Map(links.map((a) => [decodeURIComponent(a.hash.slice(1)), a]));
    const io = new IntersectionObserver((entries) => entries.forEach((e) => {
      if (!e.isIntersecting) return;
      links.forEach((a) => a.removeAttribute('aria-current'));
      byId.get(e.target.id)?.setAttribute('aria-current', 'true');
    }), { rootMargin: '-80px 0px -70% 0px' });
    byId.forEach((_, id) => { const h = document.getElementById(id); if (h) io.observe(h); });
  });
})();
