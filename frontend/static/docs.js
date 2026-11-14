// Docs page: renders /docs.md (the same file AI agents read), with a table of contents, search and inline tour videos.
import { $, $$, h, ic, paintIcons, initChrome, toast } from './common.js';
import { initTour } from './tour.js';

const slug = text => text.toLowerCase().replace(/[`*_]/g, '').replace(/[^a-z0-9\s-]/g, '').trim().replace(/\s+/g, '-');

function waitFor(name, ms = 4000) {
  return new Promise(res => {
    const t0 = performance.now();
    const tick = () => (window[name] || performance.now() - t0 > ms ? res(window[name]) : setTimeout(tick, 40));
    tick();
  });
}

async function render() {
  const art = $('#docs-article');
  let md;
  try { md = await fetch('/docs.md', { cache: 'no-cache' }).then(r => { if (!r.ok) throw new Error(r.status); return r.text(); }); }
  catch { art.replaceChildren(h('p', {}, 'The docs could not be loaded. ', h('a', { href: '/docs.md' }, 'Open them as Markdown'), '.')); return; }
  window.__docsMd = md;
  const marked = await waitFor('marked');
  if (!marked) { art.replaceChildren(h('pre', { class: 'docs-raw' }, md)); return; }
  art.innerHTML = marked.parse(md);

  // headings: stable ids (the tours link to them) and a link icon
  $$('h2, h3', art).forEach(el => {
    el.dataset.title = el.textContent;
    el.id = slug(el.textContent);
    el.append(h('a', { class: 'docs-anchor', href: `#${el.id}`, 'aria-label': `Link to ${el.textContent}` }, '#'));
  });
  // ![Video: …](….mp4) becomes the tour's clip with its voice-over (a play button plays both together)
  const media = await fetch('/static/tour/media.json').then(r => (r.ok ? r.json() : {})).catch(() => ({}));
  const darkTheme = document.documentElement.dataset.theme === 'dark';
  $$('img', art).forEach(img => {
    const url = img.getAttribute('src') || '';
    if (!url.endsWith('.mp4')) return;
    const id = url.split('/').pop().replace(/\.mp4$/, '');
    const base = url.replace(/\.mp4$/, '') + (darkTheme && media[id] && media[id].dark ? '-dark' : '');
    const title = img.alt.replace(/^Video:\s*/, '');
    const video = h('video', { src: `${base}.mp4`, poster: `${base}.jpg`, muted: true, playsinline: true, loop: true, preload: 'none', 'aria-hidden': 'true' });
    video.muted = true;
    const audio = h('audio', { src: url.replace(/\.mp4$/, '.mp3'), preload: 'none' });
    const label = h('span', {}, 'Play with voice');
    const btn = h('button', { type: 'button', class: 'dv-play', 'aria-label': `Play “${title}” with its voice-over` }, ic('play'), label);
    const set = playing => {
      btn.classList.toggle('on', playing);
      label.textContent = playing ? 'Pause' : (audio.ended ? 'Play again' : 'Play with voice');
      btn.replaceChildren(ic(playing ? 'pause' : 'play'), label);
      paintIcons();
    };
    btn.addEventListener('click', async () => {
      if (!video.paused) { video.pause(); audio.pause(); set(false); return; }
      $$('.docs-video video').forEach(v => { if (v !== video && !v.paused) v.closest('figure').querySelector('.dv-play').click(); });
      if (audio.ended || audio.currentTime === 0) { video.currentTime = 0; audio.currentTime = 0; }
      try { await Promise.all([video.play(), audio.play()]); set(true); } catch { set(false); }
    });
    audio.addEventListener('ended', () => { setTimeout(() => { video.pause(); set(false); }, 900); });
    const fig = h('figure', { class: 'docs-video' }, h('div', { class: 'dv-frame' }, video, audio, btn),
      h('figcaption', {}, ic('clapperboard'), title));
    (img.closest('p') || img).replaceWith(fig);
  });
  $$('table', art).forEach(t => t.replaceWith(h('div', { class: 'docs-table' }, t.cloneNode(true))));
  $$('a[href^="http"]', art).forEach(a => { a.target = '_blank'; a.rel = 'noopener'; });
  buildToc(art);
  paintIcons();
  if (location.hash) {
    const el = document.getElementById(decodeURIComponent(location.hash.slice(1)));
    if (el) requestAnimationFrame(() => el.scrollIntoView({ block: 'start' }));
  }
}

function buildToc(art) {
  const toc = $('#docs-toc');
  const groups = [];
  $$('h2, h3', art).forEach(el => {
    if (el.tagName === 'H2') groups.push({ el, kids: [] });
    else if (groups.length) groups[groups.length - 1].kids.push(el);
  });
  const link = el => h('a', { href: `#${el.id}`, 'data-id': el.id }, el.dataset.title);
  toc.replaceChildren(...groups.map(g => h('div', { class: 'toc-g' }, link(g.el), g.kids.length ? h('div', { class: 'toc-kids' }, g.kids.map(link)) : null)));
  // highlight the section in view
  const links = new Map($$('a[data-id]', toc).map(a => [a.dataset.id, a]));
  const io = new IntersectionObserver(entries => {
    entries.forEach(e => { if (e.isIntersecting) { links.forEach(a => a.classList.remove('on')); const a = links.get(e.target.id); if (a) a.classList.add('on'); } });
  }, { rootMargin: '-70px 0px -70% 0px' });
  $$('h2, h3', art).forEach(el => io.observe(el));
  // search: filter the contents and the sections
  $('#docs-q').addEventListener('input', e => {
    const q = e.target.value.trim().toLowerCase();
    const sections = sectionsOf(art);
    let shown = 0;
    sections.forEach(s => {
      const hit = !q || s.text.includes(q);
      s.nodes.forEach(n => { n.hidden = !hit; });
      const a = links.get(s.id);
      if (a) a.hidden = !hit;
      if (hit) shown += 1;
    });
    $$('.toc-g', toc).forEach(g => { g.hidden = $$('a', g).every(a => a.hidden); });
    let none = $('#docs-none');
    if (!shown) { if (!none) art.prepend(h('p', { id: 'docs-none', class: 'muted' }, 'Nothing in the docs matches that.')); }
    else if (none) none.remove();
  });
}

/** Each h2/h3 with the nodes after it, until the next heading. */
function sectionsOf(art) {
  const out = [];
  let cur = null;
  [...art.children].forEach(n => {
    if (n.matches('h2, h3')) { cur = { id: n.id, nodes: [n], text: n.textContent.toLowerCase() }; out.push(cur); }
    else if (cur) { cur.nodes.push(n); cur.text += ` ${n.textContent.toLowerCase()}`; }
  });
  return out;
}

initChrome();
render();
$('#docs-copy').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(window.__docsMd || ''); toast('Docs copied as Markdown', 'copy'); }
  catch { location.href = '/docs.md'; }
});
initTour({ page: 'docs' });
