// Report archive: reads reports.json (written with every build) and filters by text, kind, year and month.
(async () => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  let rows = [];
  try { rows = await (await fetch('reports.json', { cache: 'no-store' })).json(); } catch (e) { rows = []; }
  const years = [...new Set(rows.map((r) => r.date.slice(0, 4)))].sort().reverse();
  $('year').insertAdjacentHTML('beforeend', years.map((y) => `<option>${y}</option>`).join(''));
  $('month').insertAdjacentHTML('beforeend', Array.from({ length: 12 }, (_, i) => `<option value="${String(i + 1).padStart(2, '0')}">${i + 1}월</option>`).join(''));
  const params = new URLSearchParams(location.search);
  for (const id of ['q', 'kind', 'year', 'month', 'day']) if (params.get(id)) $(id).value = params.get(id);
  const draw = () => {
    const q = $('q').value.trim().toLowerCase();
    const shown = rows.filter((r) => (!$('kind').value || r.kind === $('kind').value)
      && (!$('year').value || r.date.startsWith($('year').value))
      && (!$('month').value || r.date.slice(5, 7) === $('month').value)
      && (!$('day').value || r.date === $('day').value)
      && (!q || `${r.title} ${r.summary} ${r.kind_name} ${r.key} ${r.text}`.toLowerCase().includes(q)));
    $('count').textContent = `${shown.length}개 리포트`;
    if (!shown.length) { $('list').innerHTML = '<p class="empty">조건에 맞는 리포트가 없습니다.</p>'; return; }
    const groups = {};
    for (const r of shown) (groups[r.date.slice(0, 7)] ||= []).push(r);
    $('list').innerHTML = Object.entries(groups).map(([m, items]) => `<section class="month"><h2>${m.slice(0, 4)}년 ${Number(m.slice(5))}월</h2><ul class="list">${
      items.map((r) => `<li class="item"><a href="${esc(r.path)}"><span class="row"><span class="chip">${esc(r.kind_name)}</span><span>${esc(r.period)}</span></span><strong>${esc(r.title)}</strong><p>${esc(r.summary)}</p></a></li>`).join('')
    }</ul></section>`).join('');
  };
  for (const id of ['q', 'kind', 'year', 'month', 'day']) $(id).addEventListener('input', draw);
  draw();
})();
