// Product tours, practice tasks and the help menu, shared by every page.
//
// - First visit to a page in this browser: a small card offers the tour in a corner (never a blocking modal), and
//   shrinks to a pill as soon as you start working. It is shown once; a reload never brings it back.
// - The tour player: real footage of the feature (light or dark, to match the page), a voice-over (Gradium),
//   captions, and a spotlight on the real control when it is on screen. With a spotlight the player is a compact card
//   beside the control, never on top of it. Back / Next, skip any time, Esc closes, focus returns where it was.
// - Practice: a checklist of real tasks that tick themselves off when the page reports them (common.js `did`), with a
//   small coach card and a beacon on the control while a task is under way.
// Content lives in /static/tour/tours.json; footage, voice and captions in /static/tour/ (frontend/tour/ builds them).
import { $, $$, h, ic, paintIcons, store, toast, reduceMotion, G } from './common.js';

const PARAMS = new URLSearchParams(location.search);      // read now: some pages rewrite their own URL on load
const PAGES = { build: '/', canvas: '/canvas', compare: '/compare' };
const NAMES = { build: 'Build', canvas: 'Canvas', compare: 'Compare' };
const MEDIA = '/static/tour/';
const KEY = 'cs-tour';

let cfg = null, media = {}, page = null, setups = {}, opts = {};
const S = loadState();
let P = null;                    // the open player
let menu = null, pop = null, unlock = null, coach = null;
let userPlayed = false;          // with reduced motion, one press of play means "play the rest too"

function loadState() {
  let s = {};
  try { s = JSON.parse(store.get(KEY, '{}')) || {}; } catch { s = {}; }
  return { seen: {}, finished: {}, done: {}, practice: false, voice: true, cc: false, ...s };
}
const save = () => store.set(KEY, JSON.stringify(S));
const tasks = () => (cfg ? cfg.practice.tasks : []);
const doneCount = () => tasks().filter(t => S.done[t.id]).length;
const dark = () => document.documentElement.dataset.theme === 'dark';
const src = (id, ext) => `${MEDIA}${id}${ext !== 'mp3' && dark() && media[id] && media[id].dark ? '-dark' : ''}.${ext}`;
const visible = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth; };
const findTarget = sel => (sel ? $$(sel).find(visible) : null);
function focusHome() { const el = $('#cs-practice') || $('#cs-help'); if (el) el.focus({ preventScroll: true }); }

async function loadCfg() {
  if (cfg) return cfg;
  const [c, m] = await Promise.all([
    fetch(`${MEDIA}tours.json`).then(r => r.json()),
    fetch(`${MEDIA}media.json`).then(r => (r.ok ? r.json() : {})).catch(() => ({})),
  ]);
  cfg = c; media = m;
  return cfg;
}
function stepById(id) {
  for (const [key, tour] of Object.entries(cfg.tours)) {
    const i = tour.steps.findIndex(s => s.id === id);
    if (i >= 0) return { key, i, step: tour.steps[i] };
  }
  return null;
}

/**
 * Call once per page, after the page's own start-up.
 * options: { page, ready: Promise, setups: { taskId: fn }, shortcuts: fn, eligible: () => bool }
 * `eligible` says whether the first-visit card makes sense yet (Compare waits until there are two cohorts).
 */
export async function initTour(options) {
  opts = options; page = options.page; setups = options.setups || {};
  try { await loadCfg(); } catch { return; }            // the page works exactly the same without tours
  mountHelp();
  renderPracticeButton();
  document.addEventListener('cs:did', e => complete(e.detail && e.detail.what));
  try { await options.ready; } catch { /* the page shows its own error */ }
  const task = PARAMS.get('practice');
  if (task && tasks().some(t => t.id === task)) {
    S.practice = true; save(); renderPracticeButton();
    setTimeout(() => startTask(tasks().find(t => t.id === task)), 450);
  }
  if (PARAMS.get('tour') && cfg.tours[page]) { markSeen(); setTimeout(() => openTour(page), 500); return; }
  if (!cfg.tours[page] || S.seen[page] || task) return;
  if (navigator.webdriver) return;                      // automated browsers and agents read /docs instead
  setTimeout(() => {
    if (S.seen[page] || P) return;
    if (options.eligible && !options.eligible()) return; // not yet: it waits for a visit where it makes sense
    showUnlock();
  }, 1500);
}
function markSeen() { S.seen[page] = Date.now(); save(); }

// ---------------------------------------------------------------- the unlock card (first visit)
function showUnlock() {
  const tour = cfg.tours[page];
  markSeen();                                           // once per browser: a reload never shows it again
  const id = tour.preview || tour.steps[0].id;
  let timer = 0;
  const close = (andToast = true) => {
    if (!unlock) return;
    const el = unlock; unlock = null;
    const hadFocus = el.contains(document.activeElement);
    clearTimeout(timer);
    document.removeEventListener('pointerdown', onPage, true);
    document.removeEventListener('keydown', onKey, true);
    el.classList.add('leaving');
    setTimeout(() => el.remove(), 260);
    if (hadFocus) focusHome();
    if (andToast) toast('The tour is under Help (?) whenever you want it', 'circle-help');
  };
  // once someone starts working, the card gets out of the way: it becomes a small pill, then leaves on its own
  const mini = () => {
    if (!unlock || unlock.classList.contains('mini')) return;
    unlock.classList.add('mini');
    const v = unlock.querySelector('video');
    if (v) v.pause();
    timer = setTimeout(() => close(false), 30000);
  };
  const onPage = e => { if (unlock && !unlock.contains(e.target)) mini(); };
  const onKey = e => { if (unlock && !unlock.contains(e.target)) mini(); };
  const start = h('button', { type: 'button', class: 'btn small ink tu-go', onclick: () => { close(false); openTour(page); } },
    ic('play'), h('span', { class: 'tu-go-t' }, 'Take the tour'), h('span', { class: 'tu-time' }, `${tour.minutes} min`));
  unlock = h('aside', { class: 'tour-unlock', role: 'dialog', 'aria-labelledby': 'tu-title', 'aria-describedby': 'tu-blurb',
    onkeydown: e => { e.stopPropagation(); if (e.key === 'Escape') close(); } },
  h('button', { type: 'button', class: 'icon-btn tu-x', 'aria-label': 'Not now', title: 'Not now', onclick: () => close() }, ic('x')),
  h('div', { class: 'tu-media' }, video(id, { autoplay: !reduceMotion, cls: 'tu-video' })),
  h('div', { class: 'tu-body' },
    h('p', { class: 'tu-eyebrow' }, h('span', { class: 'tu-spark', 'aria-hidden': 'true' }), `${tour.eyebrow} · ${NAMES[page]}`),
    h('h2', { id: 'tu-title' }, tour.title),
    h('p', { id: 'tu-blurb' }, tour.blurb),
    h('div', { class: 'tu-acts' }, start, h('button', { type: 'button', class: 'btn small ghost tu-later', onclick: () => close() }, 'Not now'))));
  document.body.append(unlock);
  document.addEventListener('pointerdown', onPage, true);
  document.addEventListener('keydown', onKey, true);
  paintIcons();
}
function closeUnlock() { if (unlock) { unlock.remove(); unlock = null; } }

function video(id, { autoplay = false, cls = '', preload } = {}) {
  const v = h('video', { class: cls, src: src(id, 'mp4'), poster: src(id, 'jpg'), muted: true, loop: true, playsinline: true,
    preload: preload || (autoplay ? 'auto' : 'metadata'), 'aria-hidden': 'true', disablepictureinpicture: true });
  v.muted = true;
  if (autoplay) v.autoplay = true;
  return v;
}

// ---------------------------------------------------------------- the player
export async function openTour(key = page, start = 0) {
  await loadCfg();
  closeUnlock(); closeMenu(); closePop(false); closeCoach();
  const tour = cfg.tours[key];
  if (!tour) return;
  openPlayer({ key, steps: tour.steps, i: start, single: false });
}
/** One step on its own (practice "Show me"). */
export async function showStep(id, task) {
  await loadCfg();
  const f = stepById(id);
  if (!f) return;
  closeMenu(); closePop(false);
  openPlayer({ key: f.key, steps: [f.step], i: 0, single: true, task });
}

function openPlayer({ key, steps, i, single, task }) {
  if (P) P.close(true, { restore: false });
  const back = document.activeElement && document.activeElement !== document.body ? document.activeElement : null;
  const dialog = h('dialog', { class: 'tour-player', 'aria-labelledby': 'tp-title' });
  const spot = h('div', { class: 'tp-spot', 'aria-hidden': 'true', hidden: true });
  const card = h('div', { class: 'tp-card' });
  dialog.append(spot, card);
  document.body.append(dialog);
  P = { key, steps, i, single, task, dialog, card, spot, video: null, audio: null, raf: 0, ended: false, target: null, wide: false, close: null };
  P.close = (silent = false, { restore = true } = {}) => {
    if (!P || P.dialog !== dialog) return;
    stopMedia();
    const wasEnded = P.ended, wasSingle = P.single;
    P = null;
    if (dialog.open) dialog.close();                    // at once: nothing later can take focus back
    dialog.remove();
    if (restore) { if (back && back.isConnected) back.focus({ preventScroll: true }); else focusHome(); }
    if (!silent && !wasEnded && !wasSingle) toast('Tour closed. Pick it up any time from Help (?)', 'circle-help');
  };
  dialog.addEventListener('cancel', e => { e.preventDefault(); P && P.close(); });
  dialog.addEventListener('keydown', e => {
    e.stopPropagation();                                  // the page's own shortcuts stay quiet while the tour is open
    if (e.target.closest('input, textarea')) return;
    if (e.key === 'ArrowRight' && !P.ended) { e.preventDefault(); next(); }
    if (e.key === 'ArrowLeft' && !P.ended && P.i > 0) { e.preventDefault(); back_(); }
  });
  dialog.addEventListener('click', e => {
    if (e.target !== dialog || !P) return;
    // the spotlit control is a real button: clicking it tries it for real (and the tour steps aside)
    const r = !P.spot.hidden && P.target && P.target.getBoundingClientRect();
    if (r && e.clientX >= r.left - 6 && e.clientX <= r.right + 6 && e.clientY >= r.top - 6 && e.clientY <= r.bottom + 6) {
      const el = P.target;
      P.close(true, { restore: false });
      el.click();
      toast('Trying it for real. The tour is under Help (?)', 'circle-help');
      return;
    }
    P.close();
  });
  dialog.showModal();
  renderStep();
}
addEventListener('resize', () => { if (P && !P.ended) place(); });
document.addEventListener('visibilitychange', () => {
  if (!P || !P.video || P.ended) return;
  if (document.hidden) {
    P.resume = !P.video.paused;
    P.video.pause(); P.audio.pause();
  } else if (P.resume) {
    P.resume = false;
    Promise.all([P.video.play(), P.audio.ended ? null : P.audio.play()]).catch(() => { if (P) P.playBtn.hidden = false; });
  }
});
const next = () => { if (P.i < P.steps.length - 1) { P.i += 1; renderStep(); } else if (P.single) P.close(true); else renderEnd(); };
const back_ = () => { P.i -= 1; renderStep(); };

function renderStep() {
  stopMedia();
  const { steps, i, key, single } = P;
  const st = steps[i];
  const m = media[st.id] || {};
  const v = video(st.id, { cls: 'tp-video', preload: 'auto' });
  const audio = h('audio', { src: src(st.id, 'mp3'), preload: 'auto' });
  P.video = v; P.audio = audio; P.cues = m.captions || []; P.st = st;
  const cc = h('div', { class: 'tp-cc empty', hidden: !S.cc, 'aria-hidden': 'true' });
  const playBtn = h('button', { type: 'button', class: 'tp-play', hidden: true, 'aria-label': 'Play', onclick: () => { userPlayed = true; playStep(true); } }, ic('play'));
  const ctl = (icon, label, on, pressed) => h('button', { type: 'button', class: 'tp-ctl', 'aria-label': label, title: label,
    'aria-pressed': pressed === undefined ? null : String(pressed), onclick: on }, ic(icon));
  const voiceBtn = ctl(S.voice ? 'volume-2' : 'volume-x', S.voice ? 'Mute the voice' : 'Turn the voice on', () => {
    S.voice = !S.voice; save(); audio.muted = !S.voice;
    voiceBtn.setAttribute('aria-label', S.voice ? 'Mute the voice' : 'Turn the voice on'); voiceBtn.title = voiceBtn.getAttribute('aria-label');
    voiceBtn.replaceChildren(ic(S.voice ? 'volume-2' : 'volume-x')); paintIcons();
    if (S.voice && audio.paused && !audio.ended) playStep(true);
  });
  const ccBtn = ctl('captions', 'Captions', () => { S.cc = !S.cc; save(); cc.hidden = !S.cc; ccBtn.setAttribute('aria-pressed', String(S.cc)); }, S.cc);
  const wideBtn = ctl('maximize-2', 'Bigger video', () => {
    if (innerWidth < 700) {                               // phones: the clip itself goes full screen
      const fs = v.requestFullscreen || v.webkitRequestFullscreen || v.webkitEnterFullscreen;
      if (fs) { fs.call(v); return; }
    }
    P.wide = !P.wide;
    P.card.classList.toggle('wide', P.wide);
    wideBtn.setAttribute('aria-label', P.wide ? 'Smaller video' : 'Bigger video'); wideBtn.title = wideBtn.getAttribute('aria-label');
    wideBtn.replaceChildren(ic(P.wide ? 'minimize-2' : 'maximize-2')); paintIcons();
    place();
  });
  const progress = h('div', { class: 'tp-progress', 'aria-hidden': 'true' },
    steps.map((_, k) => h('span', { class: `tp-seg${k < i ? ' done' : ''}${k === i ? ' now' : ''}` }, h('i', {}))));
  const find = st.find && st.find.label ? h('span', { class: 'tp-find' }, ic('locate-fixed'), st.find.label) : null;
  const docs = st.docs ? h('a', { class: 'tp-docs', href: `/docs#${st.docs}`, target: '_blank', rel: 'noopener' }, 'Docs', ic('arrow-up-right')) : null;
  const backBtn = !single && i > 0 ? h('button', { type: 'button', class: 'btn small ghost', onclick: back_ }, ic('arrow-left'), 'Back') : null;
  const last = i === steps.length - 1;
  const nextBtn = single
    ? (P.task ? h('button', { type: 'button', class: 'btn small ink tp-next', onclick: () => { const t = P.task; P.close(true, { restore: false }); startTask(t); } }, 'Try it', ic('arrow-right'))
      : h('button', { type: 'button', class: 'btn small ink tp-next', onclick: () => P.close(true) }, 'Got it'))
    : h('button', { type: 'button', class: 'btn small ink tp-next', onclick: next }, last ? 'Finish' : 'Next', ic(last ? 'check' : 'arrow-right'));
  const skip = single ? h('button', { type: 'button', class: 'text-link tp-skip', onclick: () => P.close(true) }, 'Close')
    : h('button', { type: 'button', class: 'text-link tp-skip', onclick: () => P.close() }, 'Skip tour');
  audio.addEventListener('ended', () => nextBtn.classList.add('nudge'));
  P.card.classList.remove('wide');
  P.wide = false;
  P.card.replaceChildren(
    h('div', { class: 'tp-top' }, progress,
      h('div', { class: 'tp-ctls' }, ctl('rotate-ccw', 'Play again', () => { userPlayed = true; playStep(true); }), ccBtn, voiceBtn, wideBtn),
      h('button', { type: 'button', class: 'icon-btn tp-x', 'aria-label': 'Close the tour', title: 'Close (Esc)', onclick: () => P.close() }, ic('x'))),
    h('div', { class: 'tp-media' }, v, cc, playBtn),
    h('div', { class: 'tp-text' },
      h('p', { class: 'tp-kicker' }, single ? `${NAMES[key]} · ${st.title}` : `${i + 1} of ${steps.length} · ${NAMES[key]}`),
      h('h2', { id: 'tp-title' }, st.title),
      h('p', { class: 'tp-body' }, st.body),
      find || docs ? h('p', { class: 'tp-meta' }, find, docs) : null),
    h('footer', { class: 'tp-foot' }, skip, h('span', { class: 'spacer' }), backBtn, nextBtn));
  P.card.append(audio);
  P.playBtn = playBtn; P.cc = cc; P.seg = progress.children[i].firstElementChild;
  paintIcons();
  const g = G();
  if (g) g.from(P.card.querySelector('.tp-text'), { y: 8, autoAlpha: 0, duration: 0.35, ease: 'power3.out', clearProps: 'all' });
  P.target = findTarget(st.find && st.find.selector);
  place();
  watchTarget(st);
  requestAnimationFrame(() => requestAnimationFrame(place));   // again once the card has its real size
  nextBtn.focus({ preventScroll: true });
  playStep(false);
}

function ready(el, ms = 5000) {
  return new Promise(res => {
    if (el.readyState >= 3) return res();
    const done = () => { el.removeEventListener('canplay', done); res(); };
    el.addEventListener('canplay', done);
    setTimeout(done, ms);
  });
}
async function playStep(user) {
  if (!P) return;
  const { video: v, audio: a } = P;
  P.playBtn.hidden = true;
  if (reduceMotion && !user && !userPlayed) { P.playBtn.hidden = false; return; }
  a.muted = !S.voice;
  P.card.classList.add('loading');
  await Promise.all([ready(v), ready(a)]);
  if (!P || P.video !== v) return;
  P.card.classList.remove('loading');
  v.currentTime = 0; a.currentTime = 0;
  try { await Promise.all([v.play(), a.play()]); }
  catch { if (P && P.video === v) { v.pause(); a.pause(); P.playBtn.hidden = false; } return; }
  cancelAnimationFrame(P.raf);
  const tick = () => {
    if (!P || P.audio !== a) return;
    const d = a.duration || 1;
    const k = a.ended ? 1 : Math.min(1, a.currentTime / d);
    if (P.seg) P.seg.style.transform = `scaleX(${k})`;
    const cue = P.cues.find(c => a.currentTime >= c.s && a.currentTime < c.e + 0.25);
    const text = cue && !a.ended ? cue.t : '';
    if (P.cc.textContent !== text) P.cc.textContent = text;
    P.cc.classList.toggle('empty', !text);
    if (!a.ended) P.raf = requestAnimationFrame(tick);
  };
  P.raf = requestAnimationFrame(tick);
}
function stopMedia() {
  if (!P) return;
  clearInterval(P.watch);
  cancelAnimationFrame(P.raf);
  [P.video, P.audio].forEach(m => { if (m) { m.pause(); m.removeAttribute('src'); m.load(); } });
}

/** Pages animate and scroll under the tour: if the control moves (or appears), place the card and spotlight again. */
function watchTarget(st) {
  clearInterval(P.watch);
  let last = '';
  const me = P;
  P.watch = setInterval(() => {
    if (P !== me || P.ended) { clearInterval(me.watch); return; }
    if (!P.target || !P.target.isConnected) P.target = findTarget(st.find && st.find.selector);
    const r = P.target && P.target.getBoundingClientRect();
    const key = r ? `${Math.round(r.left)},${Math.round(r.top)},${Math.round(r.width)},${Math.round(r.height)}` : '';
    if (key !== last) { last = key; place(); }
  }, 200);
}

/**
 * Where the card goes. No control on screen: a centred card. A control on screen: spotlight it, and put the card
 * beside it (compact if it must be) so it never covers the control or its own buttons. Always inside the window.
 */
function place() {
  if (!P || P.ended) return;
  const { spot, card, dialog } = P;
  const el = P.target && P.target.isConnected && visible(P.target) ? P.target : null;
  const r = el && el.getBoundingClientRect();
  const big = r && r.width * r.height > 0.45 * innerWidth * innerHeight;
  const centred = () => {
    card.classList.remove('placed', 'compact'); card.style.left = ''; card.style.top = '';
    spot.hidden = true; dialog.classList.remove('spotting');
  };
  if (!r || big || P.wide) { centred(); return; }
  const pad = 6, gap = 16, m = 12;
  const fit = ({ width: w, height: hh }) => {
    const clampX = x => Math.max(m, Math.min(innerWidth - w - m, x));
    const clampY = y => Math.max(m, Math.min(innerHeight - hh - m, y));
    const cx = r.left + r.width / 2 - w / 2, cy = r.top + r.height / 2 - hh / 2;
    return [
      [clampX(cx), r.bottom + pad + gap], [clampX(cx), r.top - pad - gap - hh],
      [r.right + pad + gap, clampY(cy)], [r.left - pad - gap - w, clampY(cy)],
    ].find(([x, y]) => x >= m && y >= m && x + w <= innerWidth - m && y + hh <= innerHeight - m);
  };
  // both sizes, read from layout (getBoundingClientRect lays out synchronously), before anything is decided
  card.classList.add('placed');
  card.classList.remove('compact');
  const full = card.getBoundingClientRect();
  card.classList.add('compact');
  const small = card.getBoundingClientRect();
  const atFull = fit(full);
  const at = atFull || fit(small);
  card.classList.toggle('compact', !atFull);
  if (!at) { centred(); return; }                        // no room beside it: centred, no spotlight (the label says where)
  card.style.left = `${at[0]}px`; card.style.top = `${at[1]}px`;
  Object.assign(spot.style, { left: `${r.left - pad}px`, top: `${r.top - pad}px`, width: `${r.width + 2 * pad}px`, height: `${r.height + 2 * pad}px` });
  spot.hidden = false;
  dialog.classList.add('spotting');
}

function renderEnd() {
  stopMedia();
  P.ended = true;
  S.finished[P.key] = Date.now(); save();
  P.spot.hidden = true; P.dialog.classList.remove('spotting');
  P.card.classList.remove('placed', 'compact', 'wide'); P.card.style.left = ''; P.card.style.top = '';
  const n = doneCount(), all = n === tasks().length;
  const title = { build: 'You are ready to build', canvas: 'The canvas is yours', compare: 'Ready to compare' }[P.key] || 'Tour complete';
  const burst = h('canvas', { class: 'tp-burst', 'aria-hidden': 'true' });
  P.card.replaceChildren(
    h('div', { class: 'tp-top' }, h('div', { class: 'tp-progress done-all', 'aria-hidden': 'true' }, P.steps.map(() => h('span', { class: 'tp-seg done' }, h('i', {})))),
      h('button', { type: 'button', class: 'icon-btn tp-x', 'aria-label': 'Close', onclick: () => P.close(true) }, ic('x'))),
    h('div', { class: 'tp-end' }, burst, h('div', { class: 'tp-badge' }, ic('check')),
      h('p', { class: 'tp-kicker' }, `${NAMES[P.key]} tour complete`),
      h('h2', { id: 'tp-title' }, title),
      h('p', { class: 'tp-body' }, all ? 'You have done every practice task too. The docs cover everything else.'
        : `Now try it for real: ${tasks().length - n} short task${tasks().length - n === 1 ? '' : 's'} across Build, Canvas and Compare, each ticking itself off as you go.`)),
    h('footer', { class: 'tp-foot' },
      h('a', { class: 'text-link', href: '/docs', target: '_blank', rel: 'noopener' }, ic('book-open'), 'Read the docs'),
      h('span', { class: 'spacer' }),
      all ? null : h('button', { type: 'button', class: 'btn small ghost', onclick: () => P.close(true) }, 'Later'),
      all ? h('button', { type: 'button', class: 'btn small ink', onclick: () => P.close(true) }, 'Done')
        : h('button', { type: 'button', class: 'btn small ink', onclick: () => { P.close(true, { restore: false }); openPractice(); } }, ic('list-checks'), `Start practice · ${n}/${tasks().length}`)));
  paintIcons();
  P.card.querySelector('.tp-foot .btn.ink').focus({ preventScroll: true });
  confetti(burst);
}

function confetti(canvas) {
  if (reduceMotion || !canvas.getContext) return;
  const r = canvas.getBoundingClientRect(), dpr = Math.min(2, devicePixelRatio || 1);
  canvas.width = r.width * dpr; canvas.height = r.height * dpr;
  const ctx = canvas.getContext('2d');
  const css = getComputedStyle(document.documentElement);
  const colours = ['--haem', '--eosin', '--teal', '--amber'].map(v => css.getPropertyValue(v).trim() || '#c93a63');
  const oy = canvas.classList.contains('tp-burst') ? 66 : r.height * 0.42;      // from the badge
  const dots = Array.from({ length: 70 }, () => ({ x: r.width / 2, y: oy, vx: (Math.random() - 0.5) * 7, vy: -Math.random() * 6 - 2,
    s: 2 + Math.random() * 3, c: colours[Math.floor(Math.random() * colours.length)], a: 1 }));
  const t0 = performance.now();
  const frame = now => {
    const t = (now - t0) / 1000;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, r.width, r.height);
    dots.forEach(d => { d.vy += 0.18; d.x += d.vx; d.y += d.vy; d.a = Math.max(0, 1 - t / 1.6); ctx.globalAlpha = d.a; ctx.fillStyle = d.c; ctx.beginPath(); ctx.arc(d.x, d.y, d.s, 0, 7); ctx.fill(); });
    if (t < 1.7 && canvas.isConnected) requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

// ---------------------------------------------------------------- practice
export async function openPractice() {
  await loadCfg();
  closeMenu();
  S.practice = true; save();
  renderPracticeButton();
  openPop();
}
function complete(what) {
  if (!what || !cfg) return;
  const task = tasks().find(t => t.id === what);
  if (!task || S.done[what]) return;
  S.done[what] = Date.now(); save();
  renderPracticeButton(true);
  if (pop) renderPop();
  if (coach && coach.task.id === what) coachDone();
  if (!S.practice) return;
  const n = doneCount();
  if (n === tasks().length) celebrate();
  else if (!coach) toast(`Task done: ${task.title} · ${n} of ${tasks().length}`, 'circle-check');
}
/** Do it: on this page, set the task up and coach it; elsewhere, go there and do the same. */
function startTask(t) {
  closePop(false);
  if (t.page !== page) { location.href = `${PAGES[t.page]}?practice=${t.id}`; return; }
  const ok = setups[t.id] ? setups[t.id]() : true;
  if (ok === false) return;                             // the set-up said why it can't start (and offered a way)
  showCoach(t);
}
function renderPracticeButton(bump = false) {
  const host = helpHost();
  let btn = $('#cs-practice');
  const total = tasks().length, n = doneCount();
  if (!S.practice || !host || !total) { if (btn) btn.remove(); return; }
  if (!btn) {
    btn = h('button', { type: 'button', class: 'cs-practice', id: 'cs-practice', 'aria-haspopup': 'dialog', onclick: () => (pop ? closePop() : openPop()) });
    host.insertBefore(btn, $('#cs-help', host) || host.firstChild);
  }
  const C = 2 * Math.PI * 7;
  btn.innerHTML = `<svg viewBox="0 0 18 18" aria-hidden="true"><circle cx="9" cy="9" r="7" class="track"/><circle cx="9" cy="9" r="7" class="fill" stroke-dasharray="${C}" stroke-dashoffset="${C * (1 - n / total)}"/></svg><span>${n === total ? 'Done' : `${n}/${total}`}</span>`;
  btn.title = `Practice: ${n} of ${total} tasks done`;
  btn.setAttribute('aria-label', btn.title);
  btn.classList.toggle('complete', n === total);
  if (bump) { btn.classList.remove('bump'); void btn.offsetWidth; btn.classList.add('bump'); }
}
function openPop() {
  closePop(false); closeMenu();
  pop = h('div', { class: 'cs-pop', role: 'dialog', 'aria-label': 'Practice tasks', tabindex: '-1',
    onkeydown: e => { e.stopPropagation(); if (e.key === 'Escape') closePop(); } });
  document.body.append(pop);
  renderPop();
  position(pop, $('#cs-practice') || $('#cs-help'));
  pop.focus({ preventScroll: true });
  setTimeout(() => document.addEventListener('pointerdown', outsidePop), 0);
}
let openTask = null;
function renderPop() {
  if (!pop) return;
  const total = tasks().length, n = doneCount();
  const firstOpen = tasks().find(t => !S.done[t.id] && t.page === page) || tasks().find(t => !S.done[t.id]);   // what you can do here first
  if (!openTask || S.done[openTask]) openTask = firstOpen && firstOpen.id;
  pop.replaceChildren(
    h('header', { class: 'cs-pop-head' }, h('div', {}, h('h3', {}, cfg.practice.title), h('p', {}, cfg.practice.blurb)),
      h('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Close', onclick: () => closePop() }, ic('x'))),
    h('div', { class: 'cs-meter', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': String(total), 'aria-valuenow': String(n),
      'aria-label': `${n} of ${total} done` }, h('i', { style: `width:${(100 * n) / total}%` }), h('span', {}, `${n} of ${total}`)),
    h('ol', { class: 'cs-tasks' }, tasks().map(t => {
      const done = !!S.done[t.id], open = t.id === openTask && !done;
      return h('li', { class: `cs-task${done ? ' done' : ''}${open ? ' open' : ''}` },
        h('button', { type: 'button', class: 'cs-task-head', 'aria-expanded': done ? null : String(open), disabled: done || null,
          onclick: () => { openTask = t.id; renderPop(); } },
        h('span', { class: 'cs-check' }, done ? ic('check') : null), h('span', { class: 'cs-task-t' }, t.title),
        h('span', { class: 'cs-task-where' }, NAMES[t.page])),
        open ? h('div', { class: 'cs-task-body' }, h('p', {}, t.hint), h('div', { class: 'cs-task-acts' },
          t.show ? h('button', { type: 'button', class: 'btn small ghost', onclick: () => showStep(t.show, t) }, ic('play'), 'Show me') : null,
          h('button', { type: 'button', class: 'btn small ink', onclick: () => startTask(t) }, t.page === page ? 'Do it' : `Go to ${NAMES[t.page]}`, ic('arrow-right')))) : null);
    })),
    h('footer', { class: 'cs-pop-foot' }, n === total ? h('span', { class: 'cs-all' }, ic('party-popper'), 'All done. Nicely played.') : null,
      h('span', { class: 'spacer' }),
      h('button', { type: 'button', class: 'text-link', onclick: () => { S.practice = false; save(); closePop(false); closeCoach(); renderPracticeButton(); focusHome(); toast('Practice hidden. It is under Help (?)', 'circle-help'); } }, 'Hide practice')));
  paintIcons();
}
function outsidePop(e) { if (pop && !pop.contains(e.target) && !e.target.closest('#cs-practice')) closePop(false); }
function closePop(restore = true) {
  if (!pop) return;
  const hadFocus = pop.contains(document.activeElement);
  pop.remove(); pop = null;
  document.removeEventListener('pointerdown', outsidePop);
  if (restore && hadFocus) focusHome();
}
function celebrate() {
  toast('Practice complete. You know Cohort Studio.', 'party-popper');
  const c = h('canvas', { class: 'cs-burst', 'aria-hidden': 'true' });
  document.body.append(c);
  confetti(c);
  setTimeout(() => c.remove(), 2000);
}

// ---------------------------------------------------------------- the coach: the current task, and where to do it
function showCoach(t) {
  closeCoach();
  const n = doneCount(), total = tasks().length;
  const beacon = h('div', { class: 'cs-beacon', 'aria-hidden': 'true', hidden: true });
  const card = h('aside', { class: `cs-coach${t.coachAt === 'right' ? ' right' : ''}`, role: 'status', 'aria-live': 'polite' },
    h('div', { class: 'cs-coach-top' }, h('span', { class: 'cs-coach-k' }, `Practice · ${n + 1} of ${total}`),
      h('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Stop coaching this task', onclick: closeCoach }, ic('x'))),
    h('p', { class: 'cs-coach-t' }, t.title),
    h('p', { class: 'cs-coach-h' }, t.coach || t.hint),
    h('div', { class: 'cs-coach-acts' }, t.show ? h('button', { type: 'button', class: 'btn small ghost', onclick: () => showStep(t.show) }, ic('play'), 'Show me') : null));
  document.body.append(beacon, card);
  paintIcons();
  coach = { task: t, card, beacon, raf: 0, until: Date.now() + 60000, scrolled: false };
  const follow = () => {
    if (!coach || coach.card !== card) return;
    const el = findTarget(t.target) || (t.target ? $$(t.target)[0] : null);
    if (el && !visible(el) && !coach.scrolled && t.scroll) { coach.scrolled = true; el.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
    if (el && visible(el)) {
      const r = el.getBoundingClientRect();
      Object.assign(beacon.style, { left: `${r.left - 6}px`, top: `${r.top - 6}px`, width: `${r.width + 12}px`, height: `${r.height + 12}px` });
      beacon.hidden = false;
    } else beacon.hidden = true;
    if (Date.now() < coach.until) coach.raf = requestAnimationFrame(follow);
    else beacon.hidden = true;
  };
  coach.raf = requestAnimationFrame(follow);
}
function coachDone() {
  const { card, beacon, task } = coach;
  beacon.hidden = true;
  cancelAnimationFrame(coach.raf);
  const nextTask = tasks().find(x => !S.done[x.id]);
  card.classList.add('done');
  card.replaceChildren(
    h('div', { class: 'cs-coach-top' }, h('span', { class: 'cs-coach-k' }, ic('circle-check'), `Done · ${doneCount()} of ${tasks().length}`),
      h('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Close', onclick: closeCoach }, ic('x'))),
    h('p', { class: 'cs-coach-t' }, task.title),
    nextTask ? h('div', { class: 'cs-coach-acts' }, h('button', { type: 'button', class: 'btn small ink', onclick: () => { closeCoach(); startTask(nextTask); } },
      `Next: ${nextTask.title}`, ic('arrow-right'))) : null);
  paintIcons();
  coach.until = 0;
  const mine = coach;
  setTimeout(() => { if (coach === mine && !card.matches(':hover, :focus-within')) closeCoach(); }, 7000);
}
function closeCoach() {
  if (!coach) return;
  cancelAnimationFrame(coach.raf);
  coach.card.remove(); coach.beacon.remove();
  coach = null;
}
/** For a page's practice set-up that can't start: say why, with the way forward (shown in the coach card). */
export function coachBlocked(text, action) {
  closeCoach();
  const card = h('aside', { class: 'cs-coach', role: 'status', 'aria-live': 'polite' },
    h('div', { class: 'cs-coach-top' }, h('span', { class: 'cs-coach-k' }, 'Practice'),
      h('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Close', onclick: closeCoach }, ic('x'))),
    h('p', { class: 'cs-coach-h' }, text),
    action ? h('div', { class: 'cs-coach-acts' }, h('a', { class: 'btn small ink', href: action.href }, action.label, ic('arrow-right'))) : null);
  document.body.append(card);
  paintIcons();
  coach = { task: { id: null }, card, beacon: h('div'), raf: 0, until: 0 };
}

// ---------------------------------------------------------------- help menu (?)
function helpHost() { return $('.cv-top-right') || $('.top-right'); }
function mountHelp() {
  const host = helpHost();
  if (!host || $('#cs-help')) return;
  const btn = h('button', { type: 'button', class: 'icon-btn', id: 'cs-help', 'aria-haspopup': 'menu', 'aria-expanded': 'false',
    'aria-label': 'Help: tours, practice and docs', title: 'Help: tours, practice and docs', onclick: () => (menu ? closeMenu() : openMenu()) }, ic('circle-help'));
  const theme = $('#theme-toggle', host);
  host.insertBefore(btn, theme || host.firstChild);
  paintIcons();
}
function openMenu() {
  closeMenu(); closePop(false);
  const btn = $('#cs-help');
  const tour = cfg.tours[page];
  const item = (icon, label, sub, on) => h('button', { type: 'button', role: 'menuitem', class: 'cs-mi', onclick: () => { closeMenu(); on(); } },
    h('span', { class: 'cs-mi-ico' }, ic(icon)), h('span', { class: 'cs-mi-t' }, h('b', {}, label), sub ? h('small', {}, sub) : null));
  const others = Object.keys(cfg.tours).filter(k => k !== page);
  menu = h('div', { class: 'cs-menu', role: 'menu', 'aria-label': 'Help', onkeydown: menuKeys },
    tour ? item('play-circle', 'Take the tour', `${tour.steps.length} steps · ${tour.minutes} min${S.finished[page] ? ' · watched' : ''}`, () => openTour(page)) : null,
    item('list-checks', 'Practice tasks', `${doneCount()} of ${tasks().length} done`, openPractice),
    page === 'docs' ? null : item('book-open', 'Docs', 'Every feature, for people and AI agents', () => { location.href = '/docs'; }),
    opts.shortcuts ? item('keyboard', 'Keyboard shortcuts', 'And how to read the canvas', opts.shortcuts) : null,
    h('div', { class: 'cs-mi-sep', role: 'separator' }),
    h('p', { class: 'cs-mi-label' }, page === 'docs' ? 'Tours' : 'Other tours'),
    h('div', { class: 'cs-mi-row' }, others.map(k => h('a', { role: 'menuitem', class: 'cs-mi-link', href: `${PAGES[k]}?tour=1` }, NAMES[k]))));
  document.body.append(menu);
  paintIcons();
  position(menu, btn);
  btn.setAttribute('aria-expanded', 'true');
  const first = menu.querySelector('[role=menuitem]');
  if (first) first.focus({ preventScroll: true });
  setTimeout(() => document.addEventListener('pointerdown', outsideMenu), 0);
}
function menuKeys(e) {
  e.stopPropagation();
  const items = $$('[role=menuitem]', menu);
  const i = items.indexOf(document.activeElement);
  if (e.key === 'Escape') { closeMenu(); $('#cs-help').focus(); }
  else if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length].focus(); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
}
function outsideMenu(e) { if (menu && !menu.contains(e.target) && !e.target.closest('#cs-help')) closeMenu(); }
function closeMenu() {
  if (!menu) return;
  menu.remove(); menu = null;
  document.removeEventListener('pointerdown', outsideMenu);
  const btn = $('#cs-help');
  if (btn) btn.setAttribute('aria-expanded', 'false');
}
function position(el, anchor) {
  if (!anchor) return;
  const r = anchor.getBoundingClientRect();
  const w = el.offsetWidth;
  el.style.top = `${r.bottom + 8}px`;
  el.style.left = `${Math.max(8, Math.min(innerWidth - w - 8, r.right - w))}px`;
}
