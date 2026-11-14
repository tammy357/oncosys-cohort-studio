// Cohort Studio — Compare page. Every number comes from /api/compare (the cohort engine); AI insights are optional.
import { $, $$, h, s, ic, paintIcons, api, fmt, timeAgo, modeLabel, verdictLabel, rich, countTo, toast, warnToast, errorToast, tipped, loadMeta, initChrome, showModelStatus, G, reduceMotion, did } from './common.js';
import { initTour, coachBlocked } from './tour.js';

const params = new URLSearchParams(location.search);
const state = { a: params.get('a'), b: params.get('b'), searches: [], favorites: [], ai: null, data: null, tab: 'recent', picking: 'a', km: 'pfi',
  versions: {} };   // saved cohort id -> its versions (oldest first), loaded when a slot needs them
const SOURCE_WORDS = { fix: 'Fix it', edit: 'Edited', draft: 'First run', restore: 'Restored' };
const STATUS = { pass: 'PASS', warn: 'WARN', fail: 'FAIL', unknown: 'OPEN' };

const richify = parts => parts.map(p => (typeof p === 'string' ? rich(p) : p));
const note = (kind, icon, title, ...body) => h('div', { class: `note ${kind}` }, ic(icon), h('div', {}, title ? h('b', {}, title, ' ') : null, ...richify(body)));
// A page section: the title, with what it shows in a tooltip (not a paragraph). `fold` sections start collapsed.
const infoDot = sub => (sub ? tipped(h('span', { class: 'info-dot', tabindex: '0' }, ic('info')), sub) : null);
const section = (id, icon, title, sub, ...kids) => h('section', { class: 'c-sec', id: `c-${id}` },
  h('header', { class: 'c-head' }, h('h2', {}, ic(icon), title, infoDot(sub))), ...kids);
const foldSection = (id, icon, title, sub, ...kids) => h('details', { class: 'c-sec c-fold', id: `c-${id}` },
  h('summary', { class: 'c-head' }, h('h2', {}, ic(icon), title, infoDot(sub)), h('span', { class: 'c-more' }, ic('chevron-down'))), h('div', { class: 'c-fold-body' }, ...kids));

// ---------------------------------------------------------------- choosing cohorts
function itemsFor(tab) {
  if (tab === 'fav') {
    return state.favorites.filter(f => f.has_spec).map(f => ({ ref: `f:${f.id}`, label: f.label, kind: 'Favourite', when: f.created_at, search: state.searches.find(x => x.id === f.search_id) }))
      .map(x => ({ ...x, verdict: x.search && x.search.verdict, n: x.search && x.search.n_samples, patients: x.search && x.search.n_patients }));
  }
  return state.searches.map(x => ({ ref: `s:${x.id}`, label: x.query, kind: 'Saved cohort', when: x.updated_at, verdict: x.verdict, n: x.n_samples, patients: x.n_patients, mode: x.mode, id: x.id }));
}
function info(ref) {
  if (!ref) return null;
  const ver = parseVersion(ref);
  if (ver) {   // 'v:<saved cohort id>:<version>': one saved state of a cohort
    const sr = state.searches.find(x => x.id === ver.id);
    const v = (state.versions[ver.id] || []).find(x => x.n === ver.n);
    return { ref, id: ver.id, version: ver.n, label: sr ? sr.query : `Saved cohort ${ver.id}`, kind: `Version ${ver.n}`,
      verdict: v && v.verdict, n: v && v.n_samples, patients: v && v.n_patients, when: v && v.created_at };
  }
  return [...itemsFor('recent'), ...itemsFor('fav')].find(x => x.ref === ref) || { ref, label: ref, kind: ref.startsWith('f:') ? 'Favourite' : 'Saved cohort' };
}
const parseVersion = ref => {
  const m = /^v:(\d+):(\d+)$/.exec(ref || '');
  return m ? { id: Number(m[1]), n: Number(m[2]) } : null;
};
/** The saved cohort behind a reference ('s:12' or 'v:12:3' -> 12), or null (favourites). */
const savedId = ref => {
  const ver = parseVersion(ref);
  if (ver) return ver.id;
  const m = /^s:(\d+)$/.exec(ref || '');
  return m ? Number(m[1]) : null;
};
/** Load a saved cohort's versions once, then redraw the slots (they show a version menu and "Compare its versions"). */
async function ensureVersions(id) {
  if (!id || state.versions[id]) return;
  state.versions[id] = [];
  try { state.versions[id] = (await api(`/api/searches/${id}/versions`)).versions || []; } catch { state.versions[id] = []; }
  renderSlots();
}
/** A = the version before the cohort's last change, B = the latest (for example before and after Fix it). */
function compareVersions(id) {
  const vs = state.versions[id] || [];
  if (vs.length < 2) return;
  state.a = `v:${id}:${vs[vs.length - 2].n}`;
  state.b = `v:${id}:${vs[vs.length - 1].n}`;
  sync();
}

function renderSlots() {
  const slot = side => {
    const letter = side.toUpperCase();
    const ref = state[side];
    if (!ref) {
      return h('button', { type: 'button', class: `slot ${side} empty-slot`, onclick: () => openPicker(side) },
        h('div', { class: 'slot-top' }, h('span', { class: 'slot-letter' }, letter), h('span', { class: 'slot-kind' }, `Cohort ${letter}`)),
        h('p', { class: 'slot-q' }, `Choose cohort ${letter}`), h('span', { class: 'muted' }, 'From your saved cohorts or favourites'));
    }
    const x = info(ref);
    const live = state.data && state.data[side] && state.data[side].ref === ref ? state.data[side] : null;
    const id = savedId(ref);
    const vs = id ? state.versions[id] : null;
    if (id && !vs) ensureVersions(id);
    // a version: a menu of that cohort's versions; a saved cohort with history: a button to compare its last two
    const kind = x.version && vs && vs.length
      ? h('select', { class: 'slot-ver', 'aria-label': `Version of cohort ${letter}`, onchange: e => { state[side] = `v:${id}:${e.target.value}`; sync(); } },
        vs.map(v => h('option', { value: v.n, selected: v.n === x.version || null, title: v.note || '' },
          `Version ${v.n} · ${SOURCE_WORDS[v.source] || v.source || 'saved'}`)))
      : h('span', { class: 'slot-kind' }, x.kind);
    const versionsBtn = !x.version && vs && vs.length > 1
      ? tipped(h('button', { type: 'button', class: 'btn small slot-vbtn', onclick: () => compareVersions(id) }, ic('history'), 'Compare its versions'),
        `This cohort has ${vs.length} versions. Compare version ${vs[vs.length - 2].n} (before its last change) with version ${vs[vs.length - 1].n}.`)
      : null;
    return h('div', { class: `slot ${side}` },
      h('div', { class: 'slot-top' }, h('span', { class: 'slot-letter' }, letter), kind,
        h('div', { class: 'slot-actions' },
          x.id ? h('a', { class: 'icon-btn', href: `/?open=${x.id}`, title: 'Open on the Build page', 'aria-label': 'Open on the Build page' }, ic('arrow-up-right')) : null,
          h('button', { type: 'button', class: 'icon-btn', title: 'Choose another', 'aria-label': `Choose another cohort ${letter}`, onclick: () => openPicker(side) }, ic('pencil')))),
      h('p', { class: 'slot-q' }, x.label),
      h('div', { class: 'slot-meta' }, verdictLabel(live ? live.verdict : x.verdict),
        `${fmt(live ? live.n_samples : x.n)} samples · ${fmt(live ? live.n_patients : x.patients)} patients`,
        x.when ? h('span', { class: 'muted' }, timeAgo(x.when)) : null),
      versionsBtn);
  };
  $('#slots').replaceChildren(slot('a'),
    h('button', { type: 'button', class: 'swap', title: 'Swap A and B', 'aria-label': 'Swap A and B', onclick: swap }, ic('arrow-left-right')), slot('b'));
  paintIcons();
}

function swap() {
  [state.a, state.b] = [state.b, state.a];
  if (state.data) state.data = null;
  const g = G();
  if (g) g.fromTo('.slot', { y: 8, autoAlpha: 0.4 }, { y: 0, autoAlpha: 1, duration: 0.45, stagger: 0.06, ease: 'power3.out' });
  sync();
}

function openPicker(side) {
  state.picking = side;
  $('#pk-letter').textContent = side.toUpperCase();
  $('#pk-letter').style.background = side === 'a' ? 'var(--haem)' : 'var(--eosin)';
  $('#pk-text').textContent = `Choose cohort ${side.toUpperCase()}`;
  $('#pk-filter').value = '';
  renderPicker();
  $('#picker').showModal();
  setTimeout(() => $('#pk-filter').focus(), 30);
}
function renderPicker() {
  const f = $('#pk-filter').value.trim().toLowerCase();
  const other = state[state.picking === 'a' ? 'b' : 'a'];
  $('#pk-tab-recent').setAttribute('aria-pressed', String(state.tab === 'recent'));
  $('#pk-tab-fav').setAttribute('aria-pressed', String(state.tab === 'fav'));
  const items = itemsFor(state.tab).filter(x => !f || `${x.label} ${x.verdict || ''} ${x.mode || ''}`.toLowerCase().includes(f));
  const host = $('#pk-list');
  if (!items.length) {
    host.replaceChildren(h('div', { class: 'empty', style: 'grid-template-columns:1fr;padding:22px 12px' }, h('div', {},
      h('h3', {}, f ? 'Nothing matches that filter' : state.tab === 'fav' ? 'No favourites with saved steps yet' : 'No saved cohorts yet'),
      h('p', {}, 'Run a cohort on the ', h('a', { href: '/' }, 'Build page'), ' and it appears here.'))));
    return;
  }
  host.replaceChildren(...items.map(x => h('button', { type: 'button', class: 'pk-item', disabled: x.ref === other,
    title: x.ref === other ? 'Already chosen on the other side' : '', onclick: () => pick(x.ref) },
  h('span', { class: 'pq' }, x.label),
  h('span', { class: 'pm' }, x.verdict ? verdictLabel(x.verdict) : null, x.mode ? modeLabel(x.mode) : x.kind, timeAgo(x.when), x.patients ? `${fmt(x.patients)} patients` : null),
  h('span', { class: 'pn' }, x.n === undefined || x.n === null ? '' : fmt(x.n)))));
}
function pick(ref) {
  state[state.picking] = ref;
  $('#picker').close();
  state.data = null;
  sync();
}
$('#pk-close').addEventListener('click', () => $('#picker').close());
$('#pk-filter').addEventListener('input', renderPicker);
$('#pk-tab-recent').addEventListener('click', () => { state.tab = 'recent'; renderPicker(); });
$('#pk-tab-fav').addEventListener('click', () => { state.tab = 'fav'; renderPicker(); });

function sync() {
  const q = new URLSearchParams();
  if (state.a) q.set('a', state.a);
  if (state.b) q.set('b', state.b);
  history.replaceState(null, '', `/compare${q.toString() ? `?${q}` : ''}`);
  renderSlots();
  if (state.a && state.b) runCompare();
  else renderEmpty();
}
function renderEmpty() {
  $('#c-results').hidden = true;
  const host = $('#c-empty');
  host.hidden = false;
  const none = !state.searches.length;
  host.replaceChildren(note('info', 'git-compare', none ? 'Nothing to compare yet.' : 'Choose two cohorts to compare.',
    none ? h('span', {}, 'Run a couple of cohorts on the ', h('a', { href: '/' }, 'Build page'), ' first; every run is saved here.')
      : 'Pick them from your saved cohorts or favourites. The comparison runs both protocols on the frozen files.'));
  paintIcons();
}

// ---------------------------------------------------------------- running the comparison
async function runCompare() {
  const host = $('#c-results');
  $('#c-empty').hidden = true;
  host.hidden = false;
  host.replaceChildren(h('div', { class: 'c-sec' }, h('div', { class: 'skeleton-block' })), h('div', { class: 'c-sec' }, h('div', { class: 'skeleton-block', style: 'height:160px' })));
  try {
    const d = await api(`/api/compare?a=${encodeURIComponent(state.a)}&b=${encodeURIComponent(state.b)}`);
    state.data = d;
    renderSlots();
    renderResults(d);
    did('compare');
  } catch (e) {
    host.replaceChildren(note('bad', 'circle-alert', "Couldn't compare them.", e.message));
    paintIcons();
  }
}

function lens(r1, r2, d) {
  if (d >= r1 + r2) return 0;
  if (d <= Math.abs(r1 - r2)) return Math.PI * Math.min(r1, r2) ** 2;
  const a = r1 * r1 * Math.acos((d * d + r1 * r1 - r2 * r2) / (2 * d * r1));
  const b = r2 * r2 * Math.acos((d * d + r2 * r2 - r1 * r1) / (2 * d * r2));
  const c = 0.5 * Math.sqrt((-d + r1 + r2) * (d + r1 - r2) * (d - r1 + r2) * (d + r1 + r2));
  return a + b - c;
}
/** What the overlap counts: rows for two versions of one cohort (Fix it changes rows, not samples), samples for two
 *  different cohorts. Same shape either way, so the diagram, tiles and bar don't need to know which. */
function overlapView(d) {
  const r = d.overlap_rows;
  if (r) return { unit: 'rows', one: 'row', nA: r.a, nB: r.b, shared: r.shared, only_a: r.only_a, only_b: r.only_b, union: r.union,
    jaccard: r.jaccard, coef: r.overlap_coefficient, extra: ['samples in both', r.samples_in_both] };
  const o = d.overlap;
  return { unit: 'samples', one: 'sample', nA: d.a.n_samples, nB: d.b.n_samples, shared: o.shared, only_a: o.only_a, only_b: o.only_b, union: o.union,
    jaccard: o.jaccard, coef: o.overlap_coefficient, extra: ['shared patients', o.shared_patients] };
}
function venn(d) {
  const W = 460;
  const H = 260;
  const v = overlapView(d);
  const nA = v.nA;
  const nB = v.nB;
  const sh = v.shared;
  const R = 100;
  const k = (Math.PI * R * R) / Math.max(nA, nB, 1);
  const rA = Math.max(6, Math.sqrt((k * nA) / Math.PI));
  const rB = Math.max(6, Math.sqrt((k * nB) / Math.PI));
  let dist;
  if (sh <= 0) dist = rA + rB + 16;
  else if (sh >= Math.min(nA, nB)) dist = Math.abs(rA - rB);
  else {
    let lo = Math.abs(rA - rB); let hi = rA + rB;
    for (let i = 0; i < 60; i++) { const mid = (lo + hi) / 2; if (lens(rA, rB, mid) > k * sh) lo = mid; else hi = mid; }
    dist = (lo + hi) / 2;
  }
  const left = (W - (rA + dist + rB)) / 2;
  const cxA = left + rA;
  const cxB = cxA + dist;
  const cy = H / 2;
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, class: 'venn', role: 'img', 'aria-label': `A has ${nA} ${v.unit}, B has ${nB}, ${sh} shared` });
  svg.append(s('circle', { cx: cxA, cy, r: rA, class: 'ca' }), s('circle', { cx: cxB, cy, r: rB, class: 'cb' }));
  const label = (x, n, text) => {
    const t1 = s('text', { x, y: cy + 2, class: 'vn' }); t1.textContent = fmt(n);
    const t2 = s('text', { x, y: cy + 20, class: 'vl' }); t2.textContent = text;
    svg.append(t1, t2);
  };
  if (sh <= 0) { label(cxA, nA, 'only A'); label(cxB, nB, 'only B'); }
  else {
    const lensL = Math.max(cxA - rA, cxB - rB);
    const lensR = Math.min(cxA + rA, cxB + rB);
    label((lensL + lensR) / 2, sh, 'both');
    // a crescent too thin for its label (two nearly equal circles, e.g. 776 vs 731 rows): put the label just outside
    const thin = 46;
    if (v.only_a) label(lensL - (cxA - rA) < thin ? cxA - rA - 30 : (cxA - rA + lensL) / 2, v.only_a, 'only A');
    if (v.only_b) label((cxB + rB) - lensR < thin ? cxB + rB + 30 : (lensR + cxB + rB) / 2, v.only_b, 'only B');
  }
  return svg;
}
/** Under the row diagram: what the rows only in one version are ("45 rows only in A: extra rows for samples it counted
 *  more than once, 776 rows for 731 samples") and how many samples the two share. */
function rowCaption(d) {
  const r = d.overlap_rows;
  const side = (n, x, letter) => (n ? `${fmt(n)} ${n === 1 ? 'row' : 'rows'} only in ${letter}: extra rows for samples ${letter} counted more than once (${fmt(x.n_rows)} rows for ${fmt(x.n_samples)} samples). ` : '');
  const same = !d.overlap.only_a && !d.overlap.only_b;
  return h('p', { class: 'vd-note venn-cap' }, side(r.only_a, d.a, 'A'), side(r.only_b, d.b, 'B'),
    same ? `Both versions have the same ${fmt(r.samples_in_both)} samples.` : `${fmt(r.samples_in_both)} samples are in both versions.`);
}
function tiles(d) {
  const o = overlapView(d);
  const t = (cls, v, label, isNum = true) => h('div', { class: `tile ${cls}` }, h('div', { class: 'tv', ...(isNum ? { 'data-count': String(v) } : {}) }, isNum ? (G() ? '0' : fmt(v)) : v), h('div', { class: 'tl' }, rich(label)));
  return h('div', { class: 'tiles' },
    t('a', o.nA, `${o.unit} in A`), t('b', o.nB, `${o.unit} in B`), t('', o.shared, `shared ${o.unit}`), t('', o.jaccard.toFixed(2), 'Jaccard index', false),
    t('a', o.only_a, `${o.unit} only in A`), t('b', o.only_b, `${o.unit} only in B`), t('', o.extra[1], o.extra[0]), t('', `${Math.round(100 * o.coef)}%`, 'of the smaller one is shared', false));
}
function stack(d) {
  const o = overlapView(d);
  const u = Math.max(1, o.union);
  return h('div', { class: 'stack' },
    h('div', { class: 'stack-bar', role: 'img', 'aria-label': `Only A ${o.only_a}, both ${o.shared}, only B ${o.only_b}` },
      h('i', { class: 'sa', style: `width:${(100 * o.only_a) / u}%` }), h('i', { class: 'sab', style: `width:${(100 * o.shared) / u}%` }), h('i', { class: 'sb', style: `width:${(100 * o.only_b) / u}%` })),
    h('div', { class: 'stack-key' }, h('span', { style: '--k:var(--haem)' }, `Only A ${fmt(o.only_a)}`), h('span', { style: '--k:var(--ink)' }, `Both ${fmt(o.shared)}`),
      h('span', { style: '--k:var(--eosin)' }, `Only B ${fmt(o.only_b)}`), h('span', { class: 'muted' }, `${fmt(o.union)} ${o.unit} in either`)));
}

function compCard(c) {
  const max = Math.max(1, ...c.rows.flatMap(r => [r.a_pct, r.b_pct]));
  const rows = c.rows.flatMap(r => {
    const wa = (100 * r.a_pct) / max;
    const wb = (100 * r.b_pct) / max;
    // Wide bars carry their label inside; short bars put it just outside, so text never leaves the card.
    const lab = (w, side, v) => (w > 34
      ? h('span', { class: 'in', style: side === 'a' ? `right:calc(${w}% - 46px)` : `left:calc(${w}% - 46px)` }, `${v}%`)
      : h('span', { style: side === 'a' ? `right:calc(${w}% + 6px)` : `left:calc(${w}% + 6px)` }, `${v}%`));
    return [
      h('div', { class: 'fa', title: `A: ${r.a_n} samples` }, h('i', { style: `width:${wa}%` }), lab(wa, 'a', r.a_pct)),
      h('div', { class: 'g', title: r.group }, rich(r.group)),
      h('div', { class: 'fb', title: `B: ${r.b_n} samples` }, h('i', { style: `width:${wb}%` }), lab(wb, 'b', r.b_pct)),
    ];
  });
  return h('article', { class: 'comp-card' },
    h('header', {}, h('h3', {}, rich(c.name)), h('span', { class: `tvd${c.tvd >= 0.1 ? ' hi' : ''}`, title: 'Total variation distance between A and B' }, `TVD ${c.tvd.toFixed(2)}`)),
    h('div', { class: 'fly-head' }, h('span', { class: 'ha' }, 'A'), h('span', {}), h('span', { class: 'hb' }, 'B')),
    h('div', { class: 'fly' }, rows));
}
function coverage(d) {
  return h('div', { class: 'cov' }, d.coverage.map(r => h('div', { class: 'cov-row' }, h('div', {}, rich(r.label)),
    h('div', { class: 'cov-bars' },
      h('div', { class: 'cov-line a', title: `A: ${r.a_n} samples` }, h('span', { class: 'who' }, 'A'), h('div', { class: 'cov-bar' }, h('i', { style: `width:${r.a_pct}%` })), h('span', { class: 'pv' }, `${r.a_pct}%`)),
      h('div', { class: 'cov-line b', title: `B: ${r.b_n} samples` }, h('span', { class: 'who' }, 'B'), h('div', { class: 'cov-bar' }, h('i', { style: `width:${r.b_pct}%` })), h('span', { class: 'pv' }, `${r.b_pct}%`))))));
}

function kmChart(entry) {
  const W = 640; const H = 300;
  const m = { l: 46, r: 14, t: 14, b: 38 };
  const span = Math.max(entry.a ? entry.a.max_years : 0, entry.b ? entry.b.max_years : 0, 1);
  const xmax = Math.min(20, Math.ceil(span / 2) * 2);
  const x = t => m.l + ((W - m.l - m.r) * Math.min(t, xmax)) / xmax;
  const y = v => m.t + (H - m.t - m.b) * (1 - v);
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, class: 'km', role: 'img', 'aria-label': `Kaplan-Meier curves for ${entry.label}` });
  [0, 0.25, 0.5, 0.75, 1].forEach(v => {
    svg.append(s('line', { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), class: v === 0.5 ? 'half' : 'grid' }));
    const t = s('text', { x: m.l - 8, y: y(v) + 4, class: 'axis', 'text-anchor': 'end' }); t.textContent = `${Math.round(v * 100)}%`; svg.append(t);
  });
  const step = xmax > 12 ? 5 : 2;
  for (let t = 0; t <= xmax; t += step) {
    const tx = s('text', { x: x(t), y: H - m.b + 18, class: 'axis', 'text-anchor': 'middle' }); tx.textContent = `${t}`; svg.append(tx);
  }
  const xl = s('text', { x: (m.l + W - m.r) / 2, y: H - 4, class: 'axis', 'text-anchor': 'middle' }); xl.textContent = 'years from diagnosis'; svg.append(xl);
  const path = (curve, cls) => {
    if (!curve) return;
    let dd = `M ${x(0)} ${y(1)}`;
    for (const [t, v] of curve.points.slice(1)) { if (t > xmax) break; dd += ` H ${x(t)} V ${y(v)}`; }
    const last = curve.points[curve.points.length - 1];
    dd += ` H ${x(Math.min(last[0], xmax))}`;
    svg.append(s('path', { d: dd, class: cls }));
  };
  path(entry.a, 'la');
  path(entry.b, 'lb');
  return svg;
}
function outcomes(d) {
  if (!d.outcomes || !Object.keys(d.outcomes).length) return note('info', 'info', 'No survival data.', 'The survival file is not downloaded, so outcomes can’t be compared.');
  const keys = Object.keys(d.outcomes);
  if (!d.outcomes[state.km]) state.km = keys[0];
  const host = h('div');
  const tabs = h('span', { class: 'seg', role: 'group', 'aria-label': 'Endpoint' });
  const draw = () => {
    const e = d.outcomes[state.km];
    $$('button', tabs).forEach(b => b.setAttribute('aria-pressed', String(b.dataset.k === state.km)));
    const row = (side, c) => c ? h('tr', {}, h('td', { class: side === 'a' ? 'ka' : 'kb' }, side.toUpperCase()), h('td', {}, fmt(c.n)),
      h('td', {}, `${fmt(c.events)} (${c.event_pct}%)`), h('td', {}, c.median_years === null ? 'not reached' : `${c.median_years} y`), h('td', {}, `${c.median_followup_years} y`))
      : h('tr', {}, h('td', {}, side.toUpperCase()), h('td', { colspan: 4, class: 'muted' }, 'no survival records'));
    const lr = e.logrank ? `Log-rank χ² ${e.logrank.chi2}, p = ${e.logrank.p < 0.001 ? '< 0.001' : e.logrank.p}. Descriptive and unadjusted: not a clinical comparison.`
      : d.overlap.shared_patients ? 'No log-rank test: the cohorts share patients, so the test would count them twice.' : 'Too few events to test.';
    const chart = kmChart(e);
    host.replaceChildren(h('div', { class: 'km-wrap' }, chart, h('div', {},
      h('table', { class: 'km-table' }, h('thead', {}, h('tr', {}, h('th', {}, ''), h('th', {}, 'Patients'), h('th', {}, 'Events'), h('th', {}, 'Median'), h('th', {}, 'Follow-up'))),
        h('tbody', {}, row('a', e.a), row('b', e.b))),
      h('p', { class: 'muted', style: 'font-size:13.5px;margin-top:12px' }, rich(lr)))));
    if (host.isConnected) drawKm(host);
  };
  keys.forEach(k2 => tabs.append(h('button', { type: 'button', 'data-k': k2, onclick: () => { state.km = k2; draw(); } }, k2 === 'pfi' ? 'Progression-free interval' : 'Overall survival')));
  draw();
  return h('div', {}, h('div', { style: 'margin-bottom:14px;display:flex;gap:14px;align-items:center;flex-wrap:wrap' }, tabs,
    h('span', { class: 'stack-key', style: 'margin:0' }, h('span', { style: '--k:var(--haem)' }, 'A'), h('span', { style: '--k:var(--eosin)' }, 'B'))), host);
}
function drawKm(root) {
  const g = G();
  if (!g) return;
  $$('.km .la, .km .lb', root).forEach((p, i) => {
    const len = p.getTotalLength ? p.getTotalLength() : 0;
    if (len) g.fromTo(p, { strokeDasharray: len, strokeDashoffset: len }, { strokeDashoffset: 0, duration: 1.4, delay: i * 0.15, ease: 'power2.inOut' });
  });
}
function protocols(d) {
  const rows = d.protocol.rows.map(r => h('tr', { class: r.in },
    h('td', { class: `ca${r.in === 'b' ? ' none' : ''}` }, r.in === 'b' ? '—' : rich(r.label)),
    h('td', { class: 'mid' }, r.in === 'both' ? ic('equal') : ''),
    h('td', { class: `cb${r.in === 'a' ? ' none' : ''}` }, r.in === 'a' ? '—' : rich(r.label))));
  return h('div', {}, h('table', { class: 'proto' },
    h('thead', {}, h('tr', {}, h('th', { class: 'label', style: 'text-align:left;padding:0 14px;color:var(--haem)' }, 'A'), h('th', {}), h('th', { class: 'label', style: 'text-align:left;padding:0 14px;color:var(--eosin)' }, 'B'))),
    h('tbody', {}, rows)),
  h('p', { class: 'muted', style: 'font-size:13.5px' }, `${d.protocol.shared} shared step${d.protocol.shared === 1 ? '' : 's'} · ${d.protocol.only_a} only in A · ${d.protocol.only_b} only in B`));
}
function verdicts(d) {
  const card = (side, x, other) => h('div', { class: `vcard ${side}` }, h('div', { class: 'slot-top' }, h('span', { class: 'slot-letter', style: `background:var(--${side === 'a' ? 'haem' : 'eosin'})` }, side.toUpperCase()), verdictLabel(x.verdict)),
    h('p', {}, rich(x.reason)),
    h('ul', { class: 'vchecks' }, x.checks.map(c => {
      const o = other.checks.find(q => q.name === c.name);
      return h('li', { class: o && o.status !== c.status ? 'diff' : '' }, h('span', { class: `st ${c.status}` }, STATUS[c.status] || c.status), h('span', {}, rich(c.name)));
    })));
  return h('div', { class: 'verdicts' }, card('a', d.a, d.b), card('b', d.b, d.a));
}

function insightsCard(d) {
  const card = h('div', { class: 'insights' });
  const intro = () => {
    const btn = state.ai && state.ai.available
      ? h('button', { class: 'btn ink', onclick: generate }, ic('lightbulb'), 'Generate insights')
      : h('span', { class: 'muted', style: 'font-size:14px' }, state.ai ? state.ai.reason : 'Checking the model…');
    card.replaceChildren(h('div', { class: 'insights-intro' }, h('p', {}, ...rich('The assistant reads only the aggregate metrics on this page (sizes, overlap, composition, coverage, outcomes, protocols and verdicts), never patient-level rows. Every number it writes is checked against those metrics.')), btn));
    paintIcons();
  };
  async function generate() {
    card.replaceChildren(h('div', { class: 'working' }, h('div', { class: 'working-line' }, h('span', { class: 'w-k' }, 'Thinking'), 'Reading the metrics for both cohorts'),
      h('div', { class: 'progress' }), h('div', { class: 'ghost-line', style: 'width:80%' }), h('div', { class: 'ghost-line', style: 'width:60%' }), h('div', { class: 'ghost-line', style: 'width:70%' })));
    try {
      const x = await api('/api/compare/insights', { method: 'POST', body: { a: state.a, b: state.b } });
      renderInsights(card, x, d);
    } catch (e) {
      errorToast(`Couldn't generate insights: ${e.message}`);
      intro();
    }
  }
  intro();
  if (params.get('insights') === '1' && state.ai && state.ai.available && !insightsCard.autoRan) { insightsCard.autoRan = true; generate(); }
  return card;
}
function renderInsights(card, x) {
  const items = list => h('div', { class: 'ins-grid' }, list.map(it => h('div', { class: 'ins-card' }, it.title ? h('b', {}, rich(it.title)) : null, h('p', {}, rich(it.detail)))));
  const u = x.usage || {};
  const model = tipped(h('span', { class: 'chip', tabindex: '0' }, x.model || 'model', x.cached ? ' · cached' : ''),
    x.cached ? 'Same comparison as before: served from the cache, no model call and no cost.'
      : u.input ? `${fmt(u.input)} tokens in, ${fmt(u.output)} out.` : '', 'Model');
  const checked = x.unverified_numbers.length
    ? tipped(h('span', { class: 'ins-check warn', tabindex: '0' }, ic('triangle-alert'), `${x.unverified_numbers.length} number${x.unverified_numbers.length > 1 ? 's' : ''} to check`),
      `These appear in the text but not in the computed metrics: ${x.unverified_numbers.join(', ')}.`)
    : tipped(h('span', { class: 'ins-check ok', tabindex: '0' }, ic('circle-check'), 'Numbers checked'), 'Every number in the text matches the computed metrics.');
  const more = [];
  if (x.similarities.length) more.push(h('h4', { class: 'sec-t' }, 'What they share'), items(x.similarities));
  if (x.cautions.length) more.push(h('h4', { class: 'sec-t' }, 'Before you rely on it'), h('div', { class: 'review' }, h('ul', {}, x.cautions.map(c => h('li', {}, rich(c))))));
  card.replaceChildren(
    h('div', { class: 'kicker' }, h('span', { class: 'k-main' }, 'AI insights'), model, checked),
    h('p', { class: 'ins-headline' }, rich(x.headline)),
    x.differences.length ? h('section', { class: 'sec' }, h('h4', { class: 'sec-t' }, 'Key differences'), items(x.differences.slice(0, 3))) : null,
    (x.applications.a || x.applications.b) ? h('section', { class: 'sec' }, h('h4', { class: 'sec-t' }, 'Which analysis each suits'), h('div', { class: 'ins-apps' },
      h('div', { class: 'ins-app a' }, h('span', { class: 'ins-who' }, 'Cohort A'), h('p', {}, rich(x.applications.a))),
      h('div', { class: 'ins-app b' }, h('span', { class: 'ins-who' }, 'Cohort B'), h('p', {}, rich(x.applications.b))))) : null,
    more.length ? h('details', { class: 'more' }, h('summary', {}, `Shared ground and cautions (${x.similarities.length + x.cautions.length})`), ...more) : null,
    h('p', { class: 'ins-foot' }, 'Research use only. The assistant reads the metrics on this page; it doesn’t add data.'));
  if (x.unverified_numbers.length) warnToast('Some numbers in the AI text are not in the metrics. Check them before quoting.');
  paintIcons();
  const g = G();
  if (g) g.from(card.children, { y: 10, autoAlpha: 0, duration: 0.5, stagger: 0.07, ease: 'power3.out', clearProps: 'transform,opacity,visibility' });
}

/** Two versions of one saved cohort: what happened between them, the steps that changed, and what that did to the counts. */
function versionDiff(d) {
  const V = d.versions;
  const head = side => `${side.toUpperCase()} · v${V[side]}`;
  const tr = (k, a, b) => h('tr', {}, h('th', { scope: 'row' }, k), h('td', {}, a), h('td', {}, b));
  const changed = d.protocol.changed || [];
  const same = d.overlap.only_a === 0 && d.overlap.only_b === 0;
  const twice = x => (x.n_rows > x.n_samples ? h('span', { class: 'vd-bad' }, ` (${fmt(x.n_rows - x.n_samples)} counted more than once)`) : null);
  return h('div', { class: 'vdiff' },
    h('ol', { class: 'vd-log' }, V.changes.map(c => h('li', {},
      h('b', {}, `Version ${c.n}`), h('span', { class: 'vd-src' }, SOURCE_WORDS[c.source] || c.source || 'saved'), c.note ? rich(c.note) : null))),
    // one table, so the steps that changed and the counts line up under the same A and B columns
    h('table', { class: 'fx-table vd-table' },
      h('thead', {}, h('tr', {}, h('th', {}, ''), h('th', { scope: 'col' }, head('a')), h('th', { scope: 'col' }, head('b')))),
      h('tbody', {},
        ...changed.map(c => tr(`Step ${c.step}`, rich(c.a), rich(c.b))),
        tr('Rows', h('span', {}, fmt(d.a.n_rows), twice(d.a)), h('span', {}, fmt(d.b.n_rows), twice(d.b))),
        tr('Samples', fmt(d.a.n_samples), fmt(d.b.n_samples)),
        tr('Patients', fmt(d.a.n_patients), fmt(d.b.n_patients)),
        tr('Verdict', verdictLabel(d.a.verdict), verdictLabel(d.b.verdict)))),
    h('p', { class: 'vd-note' }, same
      ? `Both versions select exactly the same ${fmt(d.overlap.shared)} samples, so who is in each, their data and their outcomes below are identical. The difference is in how the steps count them: the overlap below is counted in rows.`
      : `${fmt(d.overlap.only_a)} samples are only in ${head('a')} and ${fmt(d.overlap.only_b)} only in ${head('b')}: the sections below show who they are.`));
}

function renderResults(d) {
  const host = $('#c-results');
  host.replaceChildren(
    d.versions ? section('versions', 'history', 'What changed between versions',
      'A and B are two saved versions of the same cohort: what happened between them, which steps changed, and what that did to the counts.', versionDiff(d)) : '',
    d.overlap_rows
      ? section('overlap', 'layers', 'Overlap in rows', 'Two versions of one cohort, compared row by row: each sample\'s rows are matched, so a row only in one version is an extra row that version had (for example a sample counted twice before Fix it). Circle areas are proportional to row counts.',
        h('div', { class: 'overview' }, h('div', {}, venn(d), rowCaption(d)), h('div', {}, tiles(d), stack(d))))
      : section('overlap', 'layers', 'Overlap', 'Who is in both cohorts, and who is in only one. Circle areas are proportional to sample counts.',
        h('div', { class: 'overview' }, venn(d), h('div', {}, tiles(d), stack(d)))),
    section('summary', 'list-checks', 'What the numbers say', 'Computed directly from the metrics below; no model involved.',
      h('ul', { class: 'summary-list' }, d.summary.map(line => h('li', {}, ic('arrow-right'), h('span', {}, rich(line)))))),
    section('insights', 'lightbulb', 'AI insights', 'Differences, shared ground, which analysis each cohort suits, and what to check.', insightsCard(d)),
    section('composition', 'users-round', 'Who is in each', 'Share of each cohort in every group, ranked by how different the two mixes are (TVD).',
      h('div', { class: 'comp-grid' }, d.composition.map(compCard))),
    foldSection('coverage', 'chart-column', 'Data each one has', 'Share of samples with each data layer and a diagnostic slide.', coverage(d)),
    section('outcomes', 'activity', 'Outcomes', 'Kaplan–Meier estimates from the curated TCGA-CDR survival table, one record per patient.', outcomes(d)),
    foldSection('protocols', 'workflow', 'How each was built', 'Steps both protocols share, and the steps that belong to only one.', protocols(d)),
    foldSection('verdicts', 'scale', 'Verdicts', 'Checks that differ between the two are in bold.', verdicts(d)));
  paintIcons();
  $$('[data-count]', host).forEach(el => countTo(el, Number(el.dataset.count)));
  const g = G();
  if (!g) return;
  g.from($$('.c-sec', host), { y: 22, autoAlpha: 0, duration: 0.7, stagger: 0.08, ease: 'power3.out', clearProps: 'transform,opacity,visibility' });
  g.from($$('.venn circle', host), { attr: { r: 0 }, duration: 1, stagger: 0.12, ease: 'expo.out' });
  g.from($$('.venn text', host), { autoAlpha: 0, duration: 0.5, delay: 0.6, stagger: 0.05 });
  g.from($$('.stack-bar i', host), { scaleX: 0, duration: 0.9, stagger: 0.08, ease: 'power3.out', delay: 0.2 });
  $$('.fly .fa i', host).forEach(el => g.from(el, { scaleX: 0, duration: 0.8, ease: 'power3.out', delay: 0.3 }));
  $$('.fly .fb i', host).forEach(el => g.from(el, { scaleX: 0, duration: 0.8, ease: 'power3.out', delay: 0.3 }));
  g.from($$('.cov-bar i', host), { scaleX: 0, duration: 0.8, stagger: 0.03, ease: 'power3.out', delay: 0.3 });
  drawKm(host);
}

// ---------------------------------------------------------------- start-up
async function loadLists() {
  const [s1, f1] = await Promise.all([api('/api/searches?limit=100').catch(() => ({ searches: [] })), api('/api/favorites').catch(() => ({ favorites: [] }))]);
  state.searches = s1.searches || [];
  state.favorites = f1.favorites || [];
}
async function init() {
  initChrome({ onUser: async () => { state.a = null; state.b = null; state.data = null; await loadLists(); sync(); } });
  paintIcons();
  loadMeta().then(m => { state.ai = m.ai; showModelStatus(m.ai); if (state.data) { const c = $('#c-insights .insights'); if (c) c.replaceWith(insightsCard(state.data)); } })
    .catch(() => { state.ai = { available: false, reason: 'Model status unavailable.' }; });
  await loadLists();
  sync();
  const g = G();
  if (g) g.from(['.c-hero .eyebrow', '.c-hero h1', '.c-hero > p', '.slots'], { y: 16, autoAlpha: 0, duration: 0.7, stagger: 0.08, ease: 'power3.out', clearProps: 'transform,opacity,visibility' });
  if (state.a && !state.b && !reduceMotion) setTimeout(() => toast('Now choose cohort B to compare with', 'git-compare'), 600);
  // tours, practice and the help menu. Practice: compare the two most recent cohorts (or say how to get two).
  initTour({ page: 'compare', eligible: () => state.searches.length >= 2, setups: { compare: () => {
    if (state.searches.length < 2) { coachBlocked('Comparing needs two saved cohorts. Every run on the Build page is saved.', { href: '/', label: 'Go to Build' }); return false; }
    if (!state.a) state.a = `s:${state.searches[0].id}`;
    if (!state.b) state.b = `s:${state.searches.find(x => `s:${x.id}` !== state.a).id}`;
    sync();
  } } });
}
init();
