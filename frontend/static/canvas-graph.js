// The canvas stage: lays the pipeline out, draws the cards and wires, pans and zooms, and handles drag and drop.
//
//   Layout "flow"   the steps left to right on one spine; a table a join reads sits above its step.
//   Layout "lanes"  the same spine, but each card sits in the lane of its grain (one row per patient, per sample, or
//                   several per sample), so a join that inflates visibly drops a lane and its fix brings it back.
//
// Wires are to scale: the ink core is the samples flowing on, a pink halo the extra rows a join added. Under a card,
// a short branch ends in how many samples it removed, and an inflating join gets a pink "extra rows · Fix" pill.
// The graph knows geometry only; canvas.js builds each card's content and decides what clicks and drops mean.
import { h, s, ic, paintIcons, fmt, reduceMotion } from './common.js';

export const CARD = { w: 244, h: 148 };
const GAP = 84;
const TABLE = { w: 196, h: 58, gap: 42 };
const LANE = 220;
const TOP = 150;
const VGAP = 112;               // vertical layout (narrow screens): space between cards
const LANES = [['patient', 'One row per patient'], ['sample', 'One row per sample'], ['multi', 'Several rows per sample']];

const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const ease = t => 1 - Math.pow(1 - t, 3);

export function createGraph(stage, opts) {
  const world = stage.querySelector('.cv-world');
  const wires = stage.querySelector('.cv-wires');
  const nodesHost = stage.querySelector('.cv-nodes');
  const lanesHost = stage.querySelector('.cv-lanes');
  const zoomLabel = () => document.getElementById('cv-zoom-n');
  const cards = new Map();          // id -> card element
  const attach = new Map();         // key -> { el, of: card id, kind: 'table' | 'pill' | 'drop' }
  const pos = new Map();            // card id -> current {x, y} (animated)
  let target = new Map();
  let model = null;
  let layout = 'flow';
  let vertical = false;
  let view = { x: 80, y: 40, k: 1 };
  let anim = null;
  let bounds = { x: 0, y: 0, w: 1, h: 1 };
  let marker = null;
  let didFit = false;
  let laneLabels = null;
  const lanesOn = () => layout === 'lanes' && !vertical;
  const dragging = on => document.body.classList.toggle('cv-dragging', on);

  // ---------------------------------------------------------------- geometry
  const columnsOf = m => [{ id: 'start', kind: 'start', ...m.start }, ...m.steps.map(st => ({ ...st, kind: 'step' })), { id: 'end', kind: 'end', ...m.end }];
  const laneY = grain => TOP + Math.max(0, LANES.findIndex(l => l[0] === grain)) * LANE;
  function place(cols) {
    const out = new Map();
    let last = cols[0].grain || 'sample';
    cols.forEach((c, i) => {
      if (vertical) { out.set(c.id, { x: 0, y: i * (CARD.h + VGAP) }); return; }
      const grain = c.grain || last;
      if (c.grain) last = c.grain;
      out.set(c.id, { x: i * (CARD.w + GAP), y: lanesOn() ? laneY(grain) : TOP });
    });
    return out;
  }
  const tablePos = p => ({ x: p.x + (CARD.w - TABLE.w) / 2, y: p.y - TABLE.h - TABLE.gap });
  const pillPos = p => ({ x: p.x + CARD.w / 2, y: p.y + CARD.h + 12 });
  const dropPos = (p, withPill) => ({ x: p.x + 14, y: p.y + CARD.h + (withPill ? 54 : 20) });

  // ---------------------------------------------------------------- view (pan / zoom)
  function applyView() {
    world.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.k})`;
    stage.style.backgroundPosition = `${view.x}px ${view.y}px`;
    stage.style.backgroundSize = `${24 * view.k}px ${24 * view.k}px`;
    const zl = zoomLabel();
    if (zl) zl.textContent = `${Math.round(view.k * 100)}%`;
    drawLanes();
  }
  function zoomAt(k, cx, cy) {
    const r = stage.getBoundingClientRect();
    const px = cx ?? r.width / 2, py = cy ?? r.height / 2;
    const nk = clamp(k, 0.3, 1.6);
    view.x = px - ((px - view.x) * nk) / view.k;
    view.y = py - ((py - view.y) * nk) / view.k;
    view.k = nk;
    applyView();
  }
  /** mode 'read': as large as fits but never below readable (long pipelines start at the left and you scroll on);
   *  mode 'all': everything in view. `insets` keeps clear of floating panels: { left, right, bottom }. */
  function fit(animate = true, mode = 'all') {
    const r = stage.getBoundingClientRect();
    if (!r.width || !model) return;
    const ins = (opts.insets && opts.insets()) || {};
    const L = 48 + (ins.left || 0) + (lanesOn() ? 150 : 0), R = 48 + (ins.right || 0), T = 70, B = 96 + (ins.bottom || 0);
    const aw = Math.max(200, r.width - L - R), ah = Math.max(160, r.height - T - B);
    const kAll = Math.min(aw / bounds.w, ah / bounds.h);
    const k = mode === 'read' ? clamp(kAll, 0.72, 1) : clamp(kAll, 0.3, 1);
    const nx = bounds.w * k > aw ? L - bounds.x * k : L + (aw - bounds.w * k) / 2 - bounds.x * k;
    const ny = T + Math.max(0, (ah - bounds.h * k) / 2) - bounds.y * k;
    if (!animate || reduceMotion || !window.gsap) { view = { x: nx, y: ny, k }; applyView(); return; }
    window.gsap.to(view, { x: nx, y: ny, k, duration: 0.5, ease: 'power3.out', onUpdate: applyView });
  }
  function centerOn(id) {
    const p = target.get(id);
    if (!p) return;
    const r = stage.getBoundingClientRect();
    const ins = (opts.insets && opts.insets()) || {};
    const left = 40 + (ins.left || 0), right = r.width - 40 - (ins.right || 0);
    const sx = view.x + p.x * view.k, sy = view.y + p.y * view.k;
    if (sx > left && sx + CARD.w * view.k < right && sy > 60 && sy + CARD.h * view.k < r.height - 110) return;
    const nx = (left + right) / 2 - (p.x + CARD.w / 2) * view.k, ny = r.height / 2 - (p.y + CARD.h / 2) * view.k;
    if (reduceMotion || !window.gsap) { view.x = nx; view.y = ny; applyView(); return; }
    window.gsap.to(view, { x: nx, y: ny, duration: 0.45, ease: 'power2.out', onUpdate: applyView });
  }

  // pan by dragging the background (no text gets selected); scroll pans; Ctrl/⌘ + scroll or a pinch zooms
  let pan = null;
  stage.addEventListener('pointerdown', e => {
    if (e.button !== 0 && e.button !== 1) return;
    if (e.target.closest('.cv-node, .cv-tcard, .cv-pill, .cv-drop, .cv-wl, .cv-empty, button, input, a, select, textarea')) return;
    e.preventDefault();
    stage.focus({ preventScroll: true });
    pan = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y, moved: false };
    stage.setPointerCapture(e.pointerId);
    stage.classList.add('panning');
    dragging(true);
  });
  stage.addEventListener('pointermove', e => {
    if (!pan) return;
    const dx = e.clientX - pan.x, dy = e.clientY - pan.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) pan.moved = true;
    view.x = pan.vx + dx; view.y = pan.vy + dy;
    applyView();
  });
  const endPan = e => {
    if (!pan) return;
    if (!pan.moved && opts.onSelect) opts.onSelect(null);
    pan = null;
    stage.classList.remove('panning');
    dragging(false);
    try { stage.releasePointerCapture(e.pointerId); } catch { /* already released */ }
  };
  stage.addEventListener('pointerup', endPan);
  stage.addEventListener('pointercancel', endPan);
  stage.addEventListener('wheel', e => {
    if (e.target.closest('.cv-empty')) return;
    e.preventDefault();
    const r = stage.getBoundingClientRect();
    if (e.ctrlKey || e.metaKey) { zoomAt(view.k * Math.exp(-e.deltaY * 0.0022), e.clientX - r.left, e.clientY - r.top); return; }
    if (vertical) { view.y -= e.deltaY; view.x -= e.deltaX; }
    else if (e.deltaX) { view.x -= e.deltaX; view.y -= e.deltaY; }
    else if (e.shiftKey) view.y -= e.deltaY;
    else view.x -= e.deltaY;                  // a plain mouse wheel moves along the pipeline
    applyView();
  }, { passive: false });

  // ---------------------------------------------------------------- elements
  function cardEl(item) {
    let el = cards.get(item.id);
    if (!el) {
      el = h('div', { class: 'cv-node entering', 'data-id': item.id, role: 'button', tabindex: '0' });
      el.addEventListener('pointerdown', e => startNodeDrag(e, el));
      el.addEventListener('click', e => {
        if (el._dragged) { el._dragged = false; return; }
        if (e.target.closest('button, a, input, select, textarea')) return;
        const id = el.dataset.id;
        if (opts.onSelect) opts.onSelect(id === 'start' || id === 'end' ? id : Number(id));
      });
      el.addEventListener('dblclick', e => { if (!e.target.closest('button') && opts.onOpen) opts.onOpen(el.dataset.id === 'start' || el.dataset.id === 'end' ? el.dataset.id : Number(el.dataset.id)); });
      el.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); el.click(); } });
      nodesHost.append(el);
      cards.set(item.id, el);
      requestAnimationFrame(() => requestAnimationFrame(() => el.classList.remove('entering')));
    }
    if (el._sig !== item.sig) { el.replaceChildren(item.body); el._sig = item.sig; }
    el.className = `cv-node ${item.kind} ${item.classes || ''}${model.selected === item.id ? ' selected' : ''}`;
    el.setAttribute('aria-label', item.aria || '');
    el.setAttribute('aria-pressed', String(model.selected === item.id));
    return el;
  }
  function attachEl(key, of, kind, part, cls) {
    let a = attach.get(key);
    if (!a) {
      const el = h(kind === 'table' ? 'div' : 'div', { class: `${cls} entering`, 'data-of': of });
      if (kind === 'table' || kind === 'drop') el.addEventListener('click', () => opts.onSelect && opts.onSelect(of));
      nodesHost.append(el);
      a = { el, of, kind };
      attach.set(key, a);
      requestAnimationFrame(() => requestAnimationFrame(() => el.classList.remove('entering')));
    }
    if (a.el._sig !== part.sig) { a.el.replaceChildren(part.body); a.el._sig = part.sig; }
    a.el.className = `${cls} ${part.classes || ''}${model.selected === of ? ' selected' : ''}`;
    if (part.title) a.el.title = part.title; else a.el.removeAttribute('title');
    return a;
  }

  // ---------------------------------------------------------------- wires
  function wirePath(a, b) {
    if (vertical) {
      const ax = a.x + CARD.w / 2, ay = a.y + CARD.h, bx = b.x + CARD.w / 2, by = b.y, d = (by - ay) / 2;
      return `M${ax},${ay} C${ax},${ay + d} ${bx},${by - d} ${bx},${by}`;
    }
    const ax = a.x + CARD.w, ay = a.y + CARD.h / 2, bx = b.x, by = b.y + CARD.h / 2, d = Math.max(30, (bx - ax) / 2);
    return `M${ax},${ay} C${ax + d},${ay} ${bx - d},${by} ${bx},${by}`;
  }
  const widthOf = (n, total) => (n > 0 ? 2 + 12 * Math.sqrt(Math.min(1, n / Math.max(1, total))) : 1.2);
  const wl = [];                    // wire labels: reused buttons ("731 +")
  function drawWires() {
    if (!model) return;
    const total = model.start.samples || 1;
    const cols = columnsOf(model);
    const parts = [];
    const labels = [];
    for (let i = 0; i < cols.length - 1; i++) {
      const a = cols[i], b = cols[i + 1];
      const pa = pos.get(a.id), pb = pos.get(b.id);
      if (!pa || !pb) continue;
      const d = wirePath(pa, pb);
      const out = a.out || {};
      const samples = out.samples ?? 0, rows = out.rows ?? samples;
      const core = widthOf(samples, total);
      const extra = rows > samples ? widthOf(rows - samples, total) : 0;
      const st = a.off ? ' off' : out.error ? ' err' : out.pending ? ' pending' : '';
      if (extra) parts.push(s('path', { d, class: `w-halo${st}`, 'stroke-width': core + extra * 1.7 }));
      parts.push(s('path', { d, class: `w-core${st}`, 'stroke-width': core }));
      if (!reduceMotion && samples && !a.off && !out.pending) parts.push(s('path', { d, class: 'w-flow', 'stroke-width': Math.max(1, core * 0.3) }));
      labels.push({ x: vertical ? pa.x + CARD.w / 2 : (pa.x + CARD.w + pb.x) / 2, y: vertical ? (pa.y + CARD.h + pb.y) / 2 : (pa.y + pb.y) / 2 + CARD.h / 2,
        samples, rows, slot: i, off: a.off, pending: out.pending });
    }
    cols.forEach(c => {
      const p = pos.get(c.id);
      if (!p) return;
      if (c.table && !vertical) {
        const t = tablePos(p);
        const ax = t.x + TABLE.w / 2, ay = t.y + TABLE.h, bx = p.x + CARD.w / 2, by = p.y;
        const cls = `t-wire${c.table.inflating ? ' infl' : ''}${c.off ? ' off' : ''}`;
        parts.push(s('path', { d: `M${ax},${ay} L${bx},${by - 1}`, class: cls }));
        if (c.table.many) [-7, 0, 7].forEach(dx => parts.push(s('path', { d: `M${bx},${by - 13} L${bx + dx},${by - 1}`, class: `${cls} foot` })));
        else parts.push(s('path', { d: `M${bx - 6},${by - 9} L${bx + 6},${by - 9}`, class: `${cls} foot` }));
      }
      if (c.drop) {
        const dp = dropPos(p, !!c.pill);
        parts.push(s('path', { d: `M${dp.x + 8},${p.y + CARD.h} L${dp.x + 8},${dp.y + 2}`, class: 'w-drop', 'stroke-width': Math.max(1.4, widthOf(c.removed || 0, total) * 0.7) }));
      }
    });
    wires.replaceChildren(...parts);
    drawLabels(labels);
  }
  function drawLabels(labels) {
    while (wl.length < labels.length) {
      const n = h('span', { class: 'n' });
      const el = h('button', { type: 'button', class: 'cv-wl', title: 'Add a step here' }, n, h('span', { class: 'plus', 'aria-hidden': 'true' }, ic('plus')));
      el.addEventListener('click', () => opts.onAddAt && opts.onAddAt(Number(el.dataset.slot), el));
      el._n = n;
      nodesHost.append(el);
      wl.push(el);
      paintIcons();
    }
    wl.forEach((el, i) => {
      const l = labels[i];
      el.hidden = !l;
      if (!l) return;
      el.dataset.slot = l.slot;
      el.style.left = `${l.x}px`; el.style.top = `${l.y}px`;
      el.className = `cv-wl${l.rows > l.samples ? ' infl' : ''}${l.off ? ' off' : ''}${l.pending ? ' pending' : ''}`;
      const text = l.rows > l.samples ? `${fmt(l.rows)} rows` : fmt(l.samples);
      if (el._n.textContent !== text) el._n.textContent = text;
      el.setAttribute('aria-label', `${l.rows > l.samples ? `${fmt(l.rows)} rows for ${fmt(l.samples)} samples` : `${fmt(l.samples)} samples`}. Add a step here`);
    });
  }

  function drawLanes() {
    if (!lanesOn()) {
      lanesHost.replaceChildren(); lanesHost.hidden = true;
      if (laneLabels) { laneLabels.remove(); laneLabels = null; }
      return;
    }
    lanesHost.hidden = false;
    if (!lanesHost.children.length) lanesHost.replaceChildren(...LANES.map(([g]) => h('div', { class: `cv-lane g-${g}` })));
    if (!laneLabels) {
      laneLabels = h('div', { class: 'cv-lane-labels', 'aria-hidden': 'true' }, LANES.map(([g, label]) => h('span', { class: 'cv-lane-label' }, h('i', { class: `glyph g-${g}` }), label)));
      stage.append(laneLabels);
    }
    const y0 = TOP - (LANE - CARD.h) / 2;
    [...lanesHost.children].forEach((el, i) => { el.style.top = `${view.y + (y0 + i * LANE) * view.k}px`; el.style.height = `${LANE * view.k}px`; });
    [...laneLabels.children].forEach((el, i) => { el.style.top = `${view.y + (y0 + i * LANE) * view.k + 10}px`; });
  }

  // ---------------------------------------------------------------- render (animated)
  function frame() {
    pos.forEach((p, id) => { const el = cards.get(id); if (el) { el.style.left = `${p.x}px`; el.style.top = `${p.y}px`; } });
    attach.forEach(a => {
      const p = pos.get(a.of);
      if (!p) return;
      const card = model && (a.of === 'start' ? model.start : a.of === 'end' ? model.end : model.steps.find(x => x.id === a.of));
      const q = a.kind === 'table' ? tablePos(p) : a.kind === 'pill' ? pillPos(p) : dropPos(p, !!(card && card.pill));
      a.el.style.left = `${q.x}px`; a.el.style.top = `${q.y}px`;
    });
    drawWires();
  }
  function animateTo(next) {
    target = next;
    const from = new Map();
    next.forEach((p, id) => from.set(id, pos.get(id) || { x: p.x, y: p.y + 20 }));
    [...pos.keys()].forEach(id => { if (!next.has(id)) pos.delete(id); });
    if (anim) cancelAnimationFrame(anim);
    if (reduceMotion) { next.forEach((p, id) => pos.set(id, { ...p })); frame(); return; }
    const t0 = performance.now(), dur = 380;
    const tick = now => {
      const t = ease(Math.min(1, (now - t0) / dur));
      next.forEach((p, id) => { const f = from.get(id); pos.set(id, { x: f.x + (p.x - f.x) * t, y: f.y + (p.y - f.y) * t }); });
      frame();
      anim = t < 1 ? requestAnimationFrame(tick) : null;
    };
    anim = requestAnimationFrame(tick);
  }
  function computeBounds(cols, next) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    cols.forEach(c => {
      const p = next.get(c.id);
      x0 = Math.min(x0, p.x); x1 = Math.max(x1, p.x + CARD.w);
      y0 = Math.min(y0, c.table && !vertical ? tablePos(p).y : p.y);
      y1 = Math.max(y1, p.y + CARD.h + (c.pill ? 46 : 0) + (c.drop ? 36 : 0));
    });
    if (lanesOn()) { y0 = Math.min(y0, TOP - (LANE - CARD.h) / 2); y1 = Math.max(y1, TOP + 2 * LANE + CARD.h + 30); }
    bounds = { x: x0, y: y0, w: Math.max(1, x1 - x0), h: Math.max(1, y1 - y0) };
  }

  function render(m) {
    model = m;
    vertical = stage.clientWidth < 640;
    stage.classList.toggle('vertical', vertical);
    stage.classList.toggle('lanes', lanesOn());
    const cols = columnsOf(m);
    const next = place(cols);
    const liveCards = new Set(), liveAttach = new Set();
    cols.forEach(c => {
      cardEl(c); liveCards.add(c.id);
      if (c.table && !vertical) { attachEl(`table:${c.id}`, c.id, 'table', c.table, 'cv-tcard'); liveAttach.add(`table:${c.id}`); }
      if (c.pill) { attachEl(`pill:${c.id}`, c.id, 'pill', c.pill, 'cv-pill'); liveAttach.add(`pill:${c.id}`); }
      if (c.drop) { attachEl(`drop:${c.id}`, c.id, 'drop', c.drop, 'cv-drop'); liveAttach.add(`drop:${c.id}`); }
    });
    const gone = el => { el.classList.add('leaving'); setTimeout(() => el.remove(), 240); };
    cards.forEach((el, id) => { if (!liveCards.has(id)) { cards.delete(id); gone(el); } });
    attach.forEach((a, key) => { if (!liveAttach.has(key)) { attach.delete(key); gone(a.el); } });
    computeBounds(cols, next);
    animateTo(next);
    drawLanes();
    syncOrder(cols);
    paintIcons();
    if (!didFit && cols.length) { didFit = true; requestAnimationFrame(() => fit(false, 'read')); }
  }
  /** Keep the cards (and the wire after each) in pipeline order in the DOM, so Tab and screen readers follow the
   *  pipeline after moves and undos. Moves elements only when the order is off, and keeps focus where it was. */
  function syncOrder(cols) {
    const want = [];
    cols.forEach((c, i) => { const el = cards.get(c.id); if (el) want.push(el); if (wl[i] && !wl[i].hidden) want.push(wl[i]); });
    const set = new Set(want);
    const have = [...nodesHost.children].filter(el => set.has(el));
    if (have.length === want.length && have.every((el, i) => el === want[i])) return;
    const focused = nodesHost.contains(document.activeElement) ? document.activeElement : null;
    want.forEach(el => nodesHost.append(el));
    if (focused && document.activeElement !== focused) focused.focus({ preventScroll: true });
  }

  // ---------------------------------------------------------------- dragging: blocks in, cards around
  function worldPoint(e) {
    const r = stage.getBoundingClientRect();
    return { x: (e.clientX - r.left - view.x) / view.k, y: (e.clientY - r.top - view.y) / view.k,
      inside: e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom && !(opts.overPanel && opts.overPanel(e)) };
  }
  function slotAt(pt, skipId) {
    let slot = 0;
    (model ? model.steps.filter(st => st.id !== skipId) : []).forEach(st => {
      const p = target.get(st.id);
      if (p && (vertical ? pt.y > p.y + CARD.h / 2 : pt.x > p.x + CARD.w / 2)) slot += 1;
    });
    return slot;
  }
  function showMarker(slot, skipId) {
    const cols = columnsOf(model).filter(c => c.id !== skipId);
    const a = target.get(cols[slot].id), b = target.get((cols[slot + 1] || { id: 'end' }).id);
    if (!a) return;
    if (!marker) { marker = h('div', { class: 'cv-marker' }, h('span', {}, ic('plus'))); nodesHost.append(marker); paintIcons(); }
    marker.classList.toggle('h', vertical);
    if (vertical) { marker.style.left = `${a.x}px`; marker.style.top = `${(a.y + CARD.h + (b ? b.y : a.y + CARD.h + VGAP)) / 2}px`; marker.style.height = ''; return; }
    const y0 = Math.min(a.y, b ? b.y : a.y), y1 = Math.max(a.y, b ? b.y : a.y) + CARD.h;
    marker.style.left = `${(a.x + CARD.w + (b ? b.x : a.x + CARD.w + GAP)) / 2}px`;
    marker.style.top = `${y0}px`; marker.style.height = `${y1 - y0}px`;
  }
  function hideMarker() { if (marker) { marker.remove(); marker = null; } }

  /** Drag a palette block; on a drop over the canvas, opts.onDrop(payload, slot). */
  function startDrag(e, payload, label, icon) {
    const ghost = h('div', { class: 'cv-ghost' }, ic(icon || 'plus'), h('span', {}, label));
    document.body.append(ghost);
    paintIcons();
    dragging(true);
    stage.classList.add('dropping');
    const move = ev => {
      ghost.style.transform = `translate(${ev.clientX + 14}px, ${ev.clientY + 12}px)`;
      const pt = worldPoint(ev);
      if (pt.inside && model) { ghost._slot = slotAt(pt); showMarker(ghost._slot); ghost.classList.add('over'); }
      else { ghost._slot = null; hideMarker(); ghost.classList.remove('over'); }
    };
    const up = ev => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      stage.classList.remove('dropping');
      dragging(false);
      hideMarker();
      ghost.remove();
      if (worldPoint(ev).inside && ghost._slot !== null && ghost._slot !== undefined && opts.onDrop) opts.onDrop(payload, ghost._slot);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    move(e);
  }
  function startNodeDrag(e, el) {
    const id = el.dataset.id;
    if (e.button !== 0 || id === 'start' || id === 'end' || e.target.closest('button, a, input, select, textarea')) return;
    e.preventDefault();                          // no text selection while dragging a card
    el.focus({ preventScroll: true });
    const cid = Number(id), sx = e.clientX, sy = e.clientY;
    let moving = false, slot = null;
    const move = ev => {
      if (!moving && Math.abs(ev.clientX - sx) + Math.abs(ev.clientY - sy) < 7) return;
      if (!moving) { moving = true; el.classList.add('dragging'); stage.classList.add('dropping'); dragging(true); }
      el.style.translate = `${(ev.clientX - sx) / view.k}px ${(ev.clientY - sy) / view.k}px`;
      slot = slotAt(worldPoint(ev), cid);
      showMarker(slot, cid);
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      if (!moving) return;
      el._dragged = true;
      el.classList.remove('dragging');
      el.style.translate = '';
      stage.classList.remove('dropping');
      dragging(false);
      hideMarker();
      if (slot !== null && opts.onMove) opts.onMove(cid, slot);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  let resizeT = null;
  window.addEventListener('resize', () => { clearTimeout(resizeT); resizeT = setTimeout(() => { if (model) render(model); }, 120); });

  return {
    render, fit, centerOn, startDrag,
    fitRead: () => fit(true, 'read'),
    zoomIn: () => zoomAt(view.k * 1.2), zoomOut: () => zoomAt(view.k / 1.2), zoomReset: () => zoomAt(1),
    setLayout: l => { layout = l; if (model) { render(model); setTimeout(() => fit(true, 'read'), 40); } },
    get layout() { return layout; },
  };
}
