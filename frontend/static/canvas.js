// Cohort Canvas: build a cohort by dragging blocks onto a pipeline and see every step's grain and count as you go.
//
// The canvas is the page. Everything else floats above it only while it's in use: the block library (Add), the
// right panel (Inspect a card, the Cohort, the Code), the slides sheet and the words box. The steps are the same list
// the Build page and the AI use; the server runs them with the same engine (POST /api/canvas/evaluate), so every
// number on a card is the engine's.
import { $, $$, h, ic, paintIcons, api, fmt, rich, store, session, toast, warnToast, errorToast, initChrome,
  showModelStatus, loadMeta, verdictLabel } from './common.js';
import { createGraph } from './canvas-graph.js';
import { OPS, LAYERS, SLIDE_KINDS, FROZEN, TABLE_ICON, describe, controls, stepFor } from './canvas-controls.js';
import { CHARS, mixBar, compositionPanel, codeView, verifyBadge, galleryPanel, openXray, groupLabel } from './canvas-views.js';

const S = {
  meta: null, cat: null, ai: false,
  steps: [], ev: null, evSeq: 0, evPending: false, code: null, codeSeq: 0, codeStale: true, impacts: {}, impSeq: 0,
  gallery: null, galSeq: 0, galStale: true, galKinds: 'auto',
  sel: null, panel: null, inspFor: undefined, codeLang: 'sql', library: false, addSlot: null, sheet: false, ask: false,
  layout: store.get('cv-layout', 'flow'), undo: [], redo: [], lastRecord: { t: 0, key: '' }, searchId: null, askMode: 'new',
  flash: new Set(), hoverCid: null, recenter: null,
};
let cidSeq = 1;
let evTimer = null, codeTimer = null, impTimer = null;
const clone = x => JSON.parse(JSON.stringify(x));
const strip = s => Object.fromEntries(Object.entries(s).filter(([k]) => !k.startsWith('_')));
const withCid = s => ({ ...clone(strip(s)), _cid: cidSeq++ });
const forServer = () => S.steps.map(s => ({ ...strip(s), _cid: s._cid, ...(s._off ? { _off: true } : {}) }));
const stepByCid = cid => S.steps.find(s => s._cid === cid);
const evFor = cid => (S.ev ? S.ev.steps.find(x => x.cid === cid) : null);
const evIndex = cid => (S.ev ? S.ev.steps.findIndex(x => x.cid === cid) : -1);
const problemFor = cid => (S.ev ? (S.ev.problems || []).find(p => p.cid === cid) : null);
const errorFor = (st, e) => (e && e.error) || (problemFor(st._cid) || {}).message || '';
const shiftWarn = e => (e && S.ev ? (e.shift || []).find(x => !x.expected && x.tvd >= (S.ev.shift_threshold || 0.1)) : null);
function flash(cid) { S.flash.add(cid); setTimeout(() => S.flash.delete(cid), 1400); }
const iconBtn = (icon, label, onclick, cls = '') => h('button', { type: 'button', class: `icon-btn ${cls}`, title: label, 'aria-label': label,
  onclick: e => { e.stopPropagation(); onclick(e); } }, ic(icon));
const seg = (options, current, set, cls = '') => h('div', { class: `seg ${cls}`, role: 'group' }, options.map(([v, label, disabled]) =>
  h('button', { type: 'button', 'aria-pressed': String(current === v), disabled: disabled || null, onclick: () => set(v) }, label)));

// ---------------------------------------------------------------- history and local saving
function snapshot() { return JSON.stringify(S.steps); }
function record(key = '') {
  const now = Date.now();
  if (key && key === S.lastRecord.key && now - S.lastRecord.t < 900) { S.lastRecord.t = now; return; }
  S.undo.push(snapshot());
  if (S.undo.length > 80) S.undo.shift();
  S.redo = [];
  S.lastRecord = { t: now, key };
  updateChrome();
}
function restore(snap) {
  S.steps = JSON.parse(snap);
  cidSeq = Math.max(cidSeq, ...S.steps.map(s => s._cid + 1), 1);
  if (typeof S.sel === 'number' && !stepByCid(S.sel)) S.sel = null;
  S.inspFor = undefined;
  changed();
  renderPanel();
}
function undo() { if (!S.undo.length) return; S.redo.push(snapshot()); restore(S.undo.pop()); toast('Undone', 'undo-2'); }
function redo() { if (!S.redo.length) return; S.undo.push(snapshot()); restore(S.redo.pop()); toast('Redone', 'redo-2'); }
function persist() { store.set('cv-state', JSON.stringify({ steps: S.steps, searchId: S.searchId })); }

// ---------------------------------------------------------------- edits
function changed({ soon = false } = {}) {
  persist();
  S.codeStale = true; S.galStale = true; S.evPending = true;
  clearTimeout(evTimer);
  evTimer = setTimeout(evaluate, soon ? 30 : 260);
  renderGraph();
  updateChrome();
  renderEmpty();
}
function edit(fn, key) { record(key); fn(); changed(); }
function insertStep(step, slot) {
  const st = withCid(step);
  const at = Math.max(0, Math.min(S.steps.length, slot ?? S.steps.length));
  record();
  S.steps.splice(at, 0, st);
  flash(st._cid);
  S.recenter = st._cid;
  changed({ soon: true });
  select(st._cid);
  return st;
}
function addBlock(block, slot) {
  if (block.kind === 'template') { loadTemplate(block.template); return; }
  if (block.kind === 'sample_type') {                    // sample types merge into the sample step there is
    const existing = S.steps.find(s => s.op === 'sample_type' && !s._off);
    if (existing) {
      if (!(existing.values || []).includes(block.value)) edit(() => { existing.values = [...(existing.values || []), block.value]; });
      select(existing._cid);
      return;
    }
    slot = 0;
  }
  const step = stepFor(S.meta, block);
  if (step) insertStep(step, slot ?? S.addSlot ?? S.steps.length);
}
function removeStep(cid) {
  const i = S.steps.findIndex(s => s._cid === cid);
  if (i < 0) return;
  const st = S.steps[i];
  if (S.sel === cid) S.sel = (S.steps[i + 1] || S.steps[i - 1] || {})._cid ?? null;
  if (S.sel === null && S.panel === 'inspect') S.panel = null;
  S.inspFor = undefined;
  edit(() => { S.steps.splice(i, 1); });
  renderPanel();
  toast(`Removed “${describe(S.meta, st)}”. Ctrl+Z brings it back.`, 'trash-2');
}
function duplicateStep(cid) { const i = S.steps.findIndex(s => s._cid === cid); if (i >= 0) insertStep(S.steps[i], i + 1); }
function toggleOff(cid) { const st = stepByCid(cid); if (st) { edit(() => { st._off = !st._off; }); S.inspFor = undefined; renderPanel(); } }
function moveStep(cid, slot) {
  const i = S.steps.findIndex(s => s._cid === cid);
  const to = Math.max(0, Math.min(S.steps.length - 1, slot));
  if (i < 0 || to === i) return;
  edit(() => { const [st] = S.steps.splice(i, 1); S.steps.splice(to, 0, st); });
  flash(cid);
  S.inspFor = undefined;
  renderPanel();
}
function applyFix(cid) {
  const e = evFor(cid), st = stepByCid(cid);
  if (!e || !st) return;
  record();
  if (e.fix_step) Object.assign(st, strip(e.fix_step));
  else S.steps.splice(S.steps.indexOf(st) + 1, 0, withCid({ op: 'dedupe' }));
  flash(cid);
  S.inspFor = undefined;
  changed({ soon: true });
  renderPanel();
  toast('Fixed: every sample counts once', 'wand-sparkles');
}
function loadSteps(steps, note = '') {
  record();
  S.steps = (steps || []).map(withCid);
  S.steps.forEach(s => flash(s._cid));
  S.sel = null; S.inspFor = undefined;
  if (S.panel === 'inspect') S.panel = null;
  changed({ soon: true });
  renderPanel();
  setTimeout(() => graph.fitRead(), 420);
  if (note) toast(note, 'workflow');
}
const loadTemplate = t => loadSteps(t.steps, `Loaded “${t.label}”. Ctrl+Z brings back what you had.`);

// ---------------------------------------------------------------- the engine
async function evaluate() {
  const seq = ++S.evSeq;
  let res;
  try { res = await api('/api/canvas/evaluate', { method: 'POST', body: { steps: forServer() } }); } catch (e) {
    if (seq === S.evSeq) { S.evPending = false; errorToast(`Couldn't run the steps: ${e.message}`); renderGraph(); }
    return;
  }
  if (seq !== S.evSeq) return;
  S.ev = res;
  S.evPending = false;
  adoptServerSteps(res);
  (res.fixes || []).slice(0, 1).forEach(f => toast(`Tidied “${f.from}” to “${f.to}”`, 'sparkles'));
  renderGraph();
  renderPanelLive();
  updateChrome();
  if (S.recenter !== null) { const id = S.recenter; S.recenter = null; setTimeout(() => graph.centerOn(id), 420); }
  clearTimeout(codeTimer);
  codeTimer = setTimeout(() => { if (S.panel === 'code') fetchCode(); }, 120);
  clearTimeout(impTimer);
  impTimer = setTimeout(fetchImpacts, 900);
  if (S.sheet) fetchGallery();
}
/** A join the server added (a filter reads that table) becomes a real card on the canvas. */
function adoptServerSteps(res) {
  const run = res.run_steps || [];
  let added = false;
  const typing = document.activeElement && document.activeElement.closest && document.activeElement.closest('.cv-panel input, .cv-panel textarea');
  run.forEach((rs, j) => {
    if (rs._cid !== null && rs._cid !== undefined) {
      const local = stepByCid(rs._cid);
      if (local && !typing && JSON.stringify(strip(rs)) !== JSON.stringify(strip(local))) Object.assign(local, strip(rs));
      return;
    }
    const next = run.slice(j + 1).find(x => x._cid !== null && x._cid !== undefined);
    const at = next ? S.steps.findIndex(s => s._cid === next._cid) : S.steps.length;
    const st = withCid(rs);
    st._auto = true;
    S.steps.splice(Math.max(0, at), 0, st);
    flash(st._cid);
    added = true;
    toast(`Added “${describe(S.meta, st)}”: the next step reads that table`, 'link-2');
  });
  if (added) changed(); else persist();
}
async function fetchCode() {
  const seq = ++S.codeSeq;
  S.codeStale = false;
  renderPanel();
  try {
    const res = await api('/api/canvas/code', { method: 'POST', body: { steps: forServer(), verify: true } });
    if (seq === S.codeSeq) S.code = res;
  } catch (e) {
    if (seq === S.codeSeq) S.code = { sql: `-- ${e.message}`, python: `# ${e.message}`, json: '{}', lines: {}, verified: { ok: null, reason: e.message } };
  }
  if (S.panel === 'code') renderPanel();
}
async function fetchImpacts() {
  const seq = ++S.impSeq;
  if (!S.steps.length) return;
  try {
    const res = await api('/api/preview', { method: 'POST', body: { steps: forServer(), impacts: true } });
    if (seq === S.impSeq) { S.impacts = res.impacts || {}; renderPanelLive(); }
  } catch { /* a nice-to-have */ }
}
async function fetchGallery() {
  if (!S.galStale && S.gallery) { renderSheet(); return; }
  const seq = ++S.galSeq;
  S.galStale = false; S.gallery = null;
  renderSheet();
  const kinds = { auto: null, all: SLIDE_KINDS.map(k => k[0]), dx: ['diagnostic (FFPE)'], frozen: FROZEN }[S.galKinds];
  try {
    const res = await api('/api/canvas/gallery', { method: 'POST', body: { steps: forServer(), limit: 18, kinds } });
    if (seq === S.galSeq) S.gallery = res;
  } catch { if (seq === S.galSeq) S.gallery = { total: 0, samples_with_slides: 0, cohort_samples: 0, items: [] }; }
  renderSheet();
}

// ---------------------------------------------------------------- cards (kept short: the details live in the panel)
const graph = createGraph($('#cv-stage'), {
  onSelect: id => select(id),
  onOpen: id => { select(id); showPanel(id === 'end' ? 'cohort' : 'inspect'); },
  onDrop: (block, slot) => addBlock(block, slot),
  onMove: (cid, slot) => moveStep(cid, slot),
  onAddAt: slot => openLibrary(slot),
  insets: () => ({ left: S.library ? 336 : 0, right: S.panel ? $('#cv-panel').offsetWidth + 24 : 0, bottom: S.sheet ? $('#cv-sheet').offsetHeight + 8 : 0 }),
  overPanel: e => !!(e.target && e.target.closest && e.target.closest('.cv-float')),
});

function statusLine(st, e) {
  const err = errorFor(st, e);
  if (err) return h('div', { class: 'cvn-line bad', title: err }, ic('circle-alert'), h('span', {}, err));
  if (!e || st._off) return null;
  if (e.inflated) return h('div', { class: 'cvn-line infl' }, ic('copy'), h('span', {}, `${(e.rows / Math.max(1, e.samples)).toFixed(2)} rows per sample`));
  if (st._auto) return h('div', { class: 'cvn-line auto' }, ic('link-2'), h('span', {}, 'Added for the next step'));
  const sh = shiftWarn(e);
  if (sh) return h('div', { class: 'cvn-line warn', title: `${groupLabel(sh.group)}: ${sh.before_pct}% → ${sh.after_pct}%` }, ic('move-horizontal'),
    h('span', {}, `${sh.characteristic.split(' (')[0]} mix moved (${sh.tvd.toFixed(2)})`));
  if (e.missing_fraction >= 0.2) return h('div', { class: 'cvn-line warn' }, ic('circle-help'), h('span', {}, `${Math.round(e.missing_fraction * 100)}% had no value`));
  return null;
}
function stepBody(st, e, i) {
  const [label, , icon] = OPS[st.op] || [st.op, '', 'circle'];
  const err = errorFor(st, e);
  let foot;
  if (st._off) foot = h('div', { class: 'cvn-foot' }, h('span', { class: 'cvn-sub' }, 'Switched off · the cohort runs without it'));
  else if (err) foot = null;
  else if (!e) foot = h('div', { class: 'cvn-skel' });
  else foot = h('div', { class: 'cvn-foot' }, h('b', { class: 'cvn-n' }, fmt(e.samples)), h('span', { class: 'cvn-u' }, 'samples'),
    e.rows > e.samples ? h('span', { class: 'cvn-rows' }, `${fmt(e.rows)} rows`) : h('span', { class: 'cvn-sub' }, `· ${fmt(e.patients)} patients`));
  return h('div', { class: `cvn${S.evPending && e ? ' pending' : ''}` },
    h('div', { class: 'cvn-top' }, h('span', { class: 'cvn-op' }, ic(icon), label), h('span', { class: 'cvn-idx' }, String(i + 1)),
      h('span', { class: 'cvn-acts' },
        iconBtn(st._off ? 'eye-off' : 'eye', st._off ? 'Switch on' : 'Switch off', () => toggleOff(st._cid)),
        iconBtn('trash-2', 'Delete', () => removeStep(st._cid)))),
    h('div', { class: 'cvn-title' }, describe(S.meta, st)),
    foot, statusLine(st, e));
}
function tablePart(st, e) {
  if (st.op !== 'join') return null;
  const t = S.cat.tables.find(x => x.id === st.table);
  if (!t) return null;
  const byPatient = st.key === 'patient_id';
  const fan = st.table === 'slides' ? t.per_sample : byPatient ? t.per_patient : (t.per_sample || t.per_patient);
  const unit = { slides: 'slides per sample', normals: 'per patient' }[st.table] || (byPatient ? 'per patient' : 'per sample');
  const many = st.mode !== 'exists' && fan && fan.max > 1;
  const body = h('div', {}, h('div', { class: 'cvt-name' }, ic(TABLE_ICON[st.table] || 'table'), t.label),
    h('div', { class: 'cvt-fan' }, st.mode === 'exists' ? 'only needs one match' : fan ? h('span', {}, h('b', {}, String(fan.mean)), ` ${unit}${fan.max > 1 ? ` · max ${fan.max}` : ''}`) : ''));
  return { body, sig: JSON.stringify([st.table, st.key, st.mode, !!(e && e.inflated)]), many, inflating: !!(e && e.inflated),
    classes: `${e && e.inflated ? 'infl' : ''}${st._off ? ' off' : ''}`, title: `${t.label}: ${fmt(t.rows)} rows. ${t.about}` };
}
function pillPart(st, e) {
  if (!e || st._off || e.error || !e.inflated) return null;
  const frag = document.createDocumentFragment();
  frag.append(ic('triangle-alert'), `${fmt(e.rows - e.samples)} extra rows`,
    h('button', { type: 'button', class: 'btn small eosin', onclick: ev => { ev.stopPropagation(); applyFix(st._cid); } }, ic('wand-sparkles'), 'Fix'));
  return { body: frag, sig: `${e.rows}-${e.samples}` };
}
function dropPart(st, e) {
  if (!e || st._off || e.error || !e.removed) return null;
  return { body: document.createTextNode(`−${fmt(e.removed)}`), sig: String(e.removed), title: `${fmt(e.removed)} samples removed: ${e.removed_reason}` };
}
function startCard() {
  const st = S.ev ? S.ev.start : { samples: S.meta.release.samples, patients: S.meta.release.patients, rows: S.meta.release.samples, grain: 'sample' };
  return { ...st, out: { samples: st.samples, rows: st.rows }, sig: JSON.stringify(st), aria: `All samples: ${fmt(st.samples)} samples, ${fmt(st.patients)} patients`,
    body: h('div', { class: 'cvn' },
      h('div', { class: 'cvn-top' }, h('span', { class: 'cvn-op' }, ic('database'), 'Release')),
      h('div', { class: 'cvn-title' }, 'All samples in TCGA-BRCA'),
      h('div', { class: 'cvn-foot' }, h('b', { class: 'cvn-n' }, fmt(st.samples)), h('span', { class: 'cvn-u' }, 'samples'), h('span', { class: 'cvn-sub' }, `· ${fmt(st.patients)} patients`)),
      h('div', { class: 'cvn-line' }, ic('images'), h('span', {}, `${fmt(S.meta.release.slides)} slides · frozen event release`))) };
}
function endCard(prevGrain) {
  const ev = S.ev, f = ev && !ev.empty ? ev.final : null, v = ev && ev.verdict;
  const multi = f && f.rows > f.samples;
  return { grain: f ? f.grain : prevGrain, classes: multi ? 'locked' : '', sig: JSON.stringify([f, v && v.verdict, ev && ev.exportable, S.evPending]),
    aria: f ? `Cohort: ${fmt(f.samples)} samples, ${fmt(f.patients)} patients.${v ? ` Verdict ${v.verdict}.` : ''}` : 'Cohort',
    body: h('div', { class: `cvn${S.evPending ? ' pending' : ''}` },
      h('div', { class: 'cvn-top' }, h('span', { class: 'cvn-op' }, ic('flag'), 'Cohort')),
      h('div', { class: 'cvn-title' }, multi ? 'Counts some samples twice' : 'Your cohort'),
      f ? h('div', { class: 'cvn-foot' }, h('b', { class: 'cvn-n' }, fmt(f.samples)), h('span', { class: 'cvn-u' }, 'samples'), h('span', { class: 'cvn-sub' }, `· ${fmt(f.patients)} patients`))
        : h('div', { class: 'cvn-skel' }),
      h('div', { class: 'cvn-end-acts' }, v ? verdictLabel(v.verdict) : null,
        h('button', { type: 'button', class: 'btn small ink', disabled: !(ev && ev.exportable), title: ev && !ev.exportable ? ev.export_block_reason : 'Download cohort_samples.csv',
          onclick: e => { e.stopPropagation(); exportCsv(); } }, ic(multi ? 'lock' : 'download'), 'Export'))) };
}
function buildModel() {
  const ev = S.ev;
  const start = startCard();
  let prev = { samples: start.samples, rows: start.rows };
  let grain = start.grain;
  const steps = S.steps.map((st, i) => {
    const k = evIndex(st._cid);
    const e = k >= 0 ? ev.steps[k] : null;
    const err = errorFor(st, e);
    let out;
    if (st._off || !e || err) out = { ...prev, pending: !st._off && !e && !err, error: !!err };
    else { out = { samples: e.samples, rows: e.rows }; prev = out; }
    if (e && !err && !st._off) grain = e.grain;
    const sig = JSON.stringify([st, e && [e.rows, e.samples, e.patients, e.inflated, e.error, e.grain, e.missing_fraction, (shiftWarn(e) || {}).tvd], err, S.evPending]);
    const cls = [OPS[st.op] ? OPS[st.op][1].replace('op-', 'n-') : '', st._off ? 'off' : '', err ? 'error' : '', e && e.inflated ? 'infl' : '',
      S.flash.has(st._cid) ? 'flash' : '', S.hoverCid === String(st._cid) ? 'linked' : ''].filter(Boolean).join(' ');
    return { id: st._cid, grain: e && !err && !st._off ? e.grain : null, out, off: !!st._off, removed: e && !st._off && !err ? e.removed : 0,
      table: tablePart(st, e), pill: pillPart(st, e), drop: dropPart(st, e), body: stepBody(st, e, i), sig, classes: cls,
      aria: `Step ${i + 1}: ${describe(S.meta, st)}.${e && !err ? ` ${fmt(e.samples)} samples, ${fmt(e.patients)} patients${e.rows > e.samples ? `, ${fmt(e.rows)} rows` : ''}.` : ''}${err ? ` Problem: ${err}` : ''}` };
  });
  return { start, steps, end: endCard(grain), selected: S.sel };
}
function renderGraph() { if (S.meta) graph.render(buildModel()); }

// ---------------------------------------------------------------- selection and the right panel
function select(id) {
  if (S.sel === id) return;
  S.sel = id;
  S.inspFor = undefined;
  if (id === null) { if (S.panel === 'inspect') S.panel = null; }
  else if (S.panel !== 'code') S.panel = id === 'end' ? 'cohort' : 'inspect';
  renderGraph();
  renderPanel();
  if (id !== null) setTimeout(() => graph.centerOn(id), 30);
}
/** Show a panel (tabs, links, the status pill): never closes it. */
function showPanel(mode) {
  S.panel = mode;
  S.inspFor = undefined;
  renderPanel();
  if (S.panel === 'code' && (S.codeStale || !S.code)) fetchCode();
}
/** The toolbar's buttons: a second press closes their panel. */
function openPanel(mode) {
  if (S.panel === mode && mode !== 'inspect') { S.panel = null; S.inspFor = undefined; renderPanel(); } else showPanel(mode);
}
function closePanel() {
  S.panel = null;
  if (S.sel !== null) { S.sel = null; renderGraph(); }
  renderPanel();
}
const PANEL_TABS = [['inspect', 'Inspect'], ['cohort', 'Cohort'], ['code', 'Code']];
function renderPanel() {
  const host = $('#cv-panel');
  const was = !host.hidden;
  host.hidden = !S.panel;
  $('#cv-app').classList.toggle('panel-open', !!S.panel);
  if (S.panel) {
    if (!was) { host.classList.remove('cv-in-right'); void host.offsetWidth; host.classList.add('cv-in-right'); }
    const head = h('div', { class: 'cv-panel-head' },
      S.panel === 'help' ? h('b', { style: 'flex:1;padding-left:4px' }, 'Reading the canvas') : seg(PANEL_TABS, S.panel, v => showPanel(v)),
      iconBtn('x', 'Close the panel (Esc)', closePanel));
    const body = h('div', { class: 'cv-panel-body', id: 'cv-panel-body' });
    body.append(S.panel === 'inspect' ? inspector() : S.panel === 'cohort' ? cohortPanel() : S.panel === 'code' ? codePanel() : helpPanel());
    host.replaceChildren(head, body);
    S.inspFor = S.sel;
    if (S.panel === 'inspect') renderInsights();
    paintIcons();
  }
  updateChrome();
}
/** After each run: refresh what the numbers changed, without rebuilding the controls someone may be typing in. */
function renderPanelLive() {
  if (!S.panel || S.panel === 'code' || S.panel === 'help') return;   // the code refreshes itself after each run
  if (S.panel === 'inspect' && S.inspFor === S.sel && $('#cv-insights')) { renderInsights(); return; }
  renderPanel();
}

function inspector() {
  const sel = S.sel;
  if (sel === null || sel === undefined) {
    return h('div', { class: 'cv-pad' }, h('p', { class: 'cv-sec-title' }, 'Nothing selected'),
      h('p', { class: 'cv-hint' }, 'Click a card on the canvas to see what it does and change it.'));
  }
  if (sel === 'start') return h('div', { class: 'cv-pad' }, h('div', { class: 'cv-ins-head' }, h('div', { class: 'cv-ins-op' }, ic('database'), 'Release'),
    h('h3', {}, 'All samples in TCGA-BRCA')), h('div', { id: 'cv-insights' }));
  const st = stepByCid(sel);
  if (!st) return h('div', { class: 'cv-pad' }, h('p', { class: 'cv-hint' }, 'That step is gone.'));
  const i = S.steps.indexOf(st);
  const [label, cls, icon] = OPS[st.op] || [st.op, 'op-sample', 'circle'];
  const accent = { 'op-sample': 'var(--grey)', 'op-layer': 'var(--blue)', 'op-exclude': 'var(--eosin)', 'op-join': 'var(--teal)', 'op-filter': 'var(--haem)',
    'op-mutation': 'var(--amber)', 'op-patient': 'var(--teal)' }[cls];
  const ctrls = h('div');
  const drawCtrls = () => { ctrls.replaceChildren(controls(st, { meta: S.meta, cat: S.cat, steps: S.steps, changed: () => { record(`edit-${st._cid}`); changed(); },
    redraw: () => { drawCtrls(); renderGraph(); } })); paintIcons(); };
  drawCtrls();
  return h('div', { class: 'cv-pad' },
    h('div', { class: 'cv-ins-head', style: `--accent:${accent}` },
      h('div', { class: 'cv-ins-op' }, ic(icon), label, h('span', { class: 'muted' }, `Step ${i + 1} of ${S.steps.length}`)),
      h('h3', { id: 'cv-insp-name' }, describe(S.meta, st)),
      h('div', { class: 'cv-ins-acts' },
        h('button', { type: 'button', class: 'btn small ghost', onclick: () => toggleOff(st._cid) }, ic(st._off ? 'eye' : 'eye-off'), st._off ? 'Switch on' : 'Switch off'),
        h('button', { type: 'button', class: 'btn small ghost', onclick: () => duplicateStep(st._cid) }, ic('copy-plus'), 'Duplicate'),
        iconBtn('arrow-left', 'Move earlier', () => moveStep(st._cid, i - 1)), iconBtn('arrow-right', 'Move later', () => moveStep(st._cid, i + 1)),
        iconBtn('trash-2', 'Delete', () => removeStep(st._cid)))),
    h('div', { id: 'cv-insights-top' }),
    h('section', { class: 'cv-sec' }, h('p', { class: 'cv-sec-title' }, 'Settings'), ctrls),
    h('div', { id: 'cv-insights' }));
}
const nums = items => h('div', { class: 'cv-nums' }, items.map(([v, l, cls]) => h('div', { class: cls || '' }, h('b', {}, v), h('small', {}, l))));
const details = (title, kids, open = false) => h('details', { class: 'cv-sec', open: open || null }, h('summary', {}, h('span', { class: 'cv-sec-title' }, title)), ...kids);
/** The parts of the inspector that change with every run; the controls stay put. */
function renderInsights() {
  const host = $('#cv-insights'), top = $('#cv-insights-top');
  if (!host) return;
  if (S.sel === 'start') { host.replaceChildren(startInsights()); paintIcons(); return; }
  const st = stepByCid(S.sel);
  if (!st) return;
  const nm = $('#cv-insp-name');
  if (nm) nm.textContent = describe(S.meta, st);
  const e = evFor(S.sel);
  const err = errorFor(st, e);
  const t = [];
  if (err) t.push(h('div', { class: 'cv-banner bad' }, h('b', {}, 'This step can’t run'), h('span', {}, rich(err))));
  else if (st._off) t.push(h('div', { class: 'cv-banner info' }, h('span', {}, 'Switched off: the cohort is shown without it.')));
  else if (e) {
    t.push(nums([[fmt(e.samples), 'samples'], [fmt(e.patients), 'patients'], [fmt(e.rows), 'rows', e.rows > e.samples ? 'hot' : '']]));
    if (e.inflated) {
      const ex = (e.duplicate_examples || [])[0];
      t.push(h('div', { class: 'cv-banner infl' }, h('b', {}, `${fmt(e.rows - e.samples)} extra rows count samples more than once`),
        h('span', {}, rich(e.fix || 'Keep one row per sample.')),
        h('div', { class: 'cv-banner-acts' }, h('button', { type: 'button', class: 'btn small eosin', onclick: () => applyFix(st._cid) }, ic('wand-sparkles'), 'Apply the fix'),
          ex ? h('button', { type: 'button', class: 'btn small', onclick: () => xray(ex.sample_id.slice(0, 12), st._cid) }, ic('scan-search'), 'See why') : null)));
    }
  }
  if (top) top.replaceChildren(...t);
  const out = [];
  if (e && !err && !st._off) {
    const imp = S.impacts[String(S.sel)];
    if (e.removed || imp !== undefined) {
      const by = e.removed_by || {};
      out.push(details(`Who it removed · ${fmt(e.removed)}`, [
        h('p', {}, rich(e.removed_reason || 'Nothing removed.')),
        imp !== undefined ? h('p', { class: 'cv-hint' }, `Without this step the cohort would have ${imp >= 0 ? `${fmt(imp)} more` : `${fmt(-imp)} fewer`} samples.`) : null,
        ...CHARS.filter(ch => by[ch.key]).map(ch => h('div', { class: 'cv-byrow' }, h('small', {}, ch.name), mixBar(by[ch.key], ch, { cls: 'cv-mix wide' })))]));
    }
    if (e.inflated && (e.duplicate_examples || []).length) {
      out.push(details('Samples counted more than once', [h('div', { class: 'cv-dups' }, e.duplicate_examples.map(x => h('button', { type: 'button', class: 'cv-dup',
        onclick: () => xray(x.sample_id.slice(0, 12), S.sel) }, h('code', {}, x.sample_id), h('b', {}, `${x.rows} rows`), ic('scan-search'))))]));
    }
    if ((e.shift || []).length) {
      out.push(details('Did it change who is in?', [h('ul', { class: 'cv-shifts' }, e.shift.map(x => h('li', { class: x.expected ? 'exp' : x.tvd >= (S.ev.shift_threshold || 0.1) ? 'warn' : '' },
        h('span', {}, x.characteristic.split(' (')[0]), h('span', { class: 'cv-shift-bar' }, h('i', { style: `width:${Math.min(100, x.tvd * 300)}%` })), h('b', {}, x.tvd.toFixed(2)),
        h('small', {}, x.expected ? `Expected: ${x.expected}` : `${groupLabel(x.group)} ${x.before_pct}% → ${x.after_pct}%`)))),
      h('p', { class: 'cv-hint' }, 'TVD: 0 = same mix, 1 = no overlap. 0.10 or more is flagged unless it follows from what you filtered on.')], !!shiftWarn(e)));
    }
    if (e.missing_fraction >= 0.01) {
      out.push(details(`Not recorded · ${Math.round(e.missing_fraction * 100)}%`, [h('div', { class: 'cv-meter' }, h('i', { style: `width:${Math.round(e.missing_fraction * 100)}%` })),
        h('p', { class: 'cv-hint' }, `${Math.round(e.missing_fraction * 100)}% of the samples reaching this step have no value for ${e.column || 'it'}; a missing value never passes.`)]));
    }
    if (e.selection && e.selection.examples) {
      out.push(details(`Chosen for ${fmt(e.selection.patients)} patients`, [h('ul', { class: 'cv-sel' }, e.selection.examples.map(x => h('li', {}, h('code', {}, x.patient_id), ' kept ', h('code', {}, x.kept), h('small', {}, x.reason))))]));
    }
    out.push(h('div', { class: 'cv-sec' }, h('button', { type: 'button', class: 'btn small ghost', onclick: () => showPanel('code') }, ic('code-xml'), 'See this step as SQL')));
  }
  host.replaceChildren(...out);
  paintIcons();
}
function startInsights() {
  const ev = S.ev;
  return h('div', {},
    nums([[fmt(S.meta.release.samples), 'samples'], [fmt(S.meta.release.patients), 'patients'], [fmt(S.meta.release.slides), 'slides']]),
    details('Tables in the release', [h('ul', { class: 'cv-list' }, S.cat.tables.map(t => h('li', {}, h('b', {}, t.label), ` · ${fmt(t.rows)} rows. `, h('span', { class: 'muted' }, t.about))))], true),
    ev && ev.composition ? details('Who is in at the start', CHARS.filter(ch => ev.composition[ch.key]).map(ch => h('div', { class: 'cv-byrow' }, h('small', {}, ch.name),
      mixBar(ev.composition[ch.key].start, ch, { cls: 'cv-mix wide' })))) : null);
}
function checkList(v) {
  const icon = { pass: 'circle-check', warn: 'triangle-alert', fail: 'circle-x', unknown: 'circle-help' };
  return h('ul', { class: 'cv-checks' }, v.checks.map(c => h('li', { class: c.status }, ic(icon[c.status] || 'circle'),
    h('div', {}, h('b', {}, c.name), h('p', {}, rich(c.detail)), c.review && c.status !== 'pass' ? h('p', { class: 'cv-review' }, ic('user-check'), rich(c.review)) : null))));
}
function cohortPanel() {
  const ev = S.ev;
  if (!ev || ev.empty || !ev.verdict) return h('div', { class: 'cv-pad' }, h('p', { class: 'cv-hint' }, 'Add a step to see the cohort, its verdict and its checks.'));
  const v = ev.verdict, f = ev.final;
  const open = v.checks.filter(c => c.status !== 'pass').length;
  return h('div', { class: 'cv-pad' },
    h('div', { class: 'cv-verdict-line' }, verdictLabel(v.verdict), h('span', {}, rich(v.reason))),
    nums([[fmt(f.samples), 'samples'], [fmt(f.patients), 'patients'], [fmt(f.rows), 'rows', f.rows > f.samples ? 'hot' : '']]),
    h('div', { class: 'cv-actions' },
      h('button', { type: 'button', class: 'btn small ink', disabled: !ev.exportable, onclick: exportCsv }, ic('download'), 'Export cohort_samples.csv'),
      h('button', { type: 'button', class: 'btn small', onclick: saveAndOpen }, ic('save'), 'Save & open'),
      !ev.exportable ? h('span', { class: 'muted' }, ev.export_block_reason) : null),
    details(`Checks · ${open ? `${open} to look at` : 'all passed'}`, [checkList(v)], true),
    details('Who is in', [compositionPanel(ev)]),
    (v.human_review || []).length ? details('A person should review', [h('ul', { class: 'cv-list' }, v.human_review.map(x => h('li', {}, rich(x))))]) : null,
    (v.limitations || []).length ? details('What this can’t tell you', [h('ul', { class: 'cv-list' }, v.limitations.map(x => h('li', {}, rich(x))))]) : null);
}
function codePanel() {
  const lang = S.codeLang, c = S.code;
  const text = !c ? '' : lang === 'sql' ? c.sql : lang === 'python' ? c.python : c.json;
  const loading = !c || S.codeStale;
  const bar = h('div', { class: 'cv-code-bar' },
    seg([['sql', 'SQL'], ['python', 'Python'], ['json', 'JSON']], lang, v => { S.codeLang = v; renderPanel(); }),
    h('span', { class: 'spacer' }),
    lang === 'json' ? iconBtn('pencil', 'Edit the steps as JSON', editJson) : null,
    iconBtn('copy', 'Copy', () => copyText(text)),
    iconBtn('download', 'Download', () => download(text, lang === 'sql' ? 'cohort.sql' : lang === 'python' ? 'reproduce_cohort.py' : 'cohort_spec.json')));
  const meta = h('div', { class: 'cv-code-meta' }, lang === 'sql' ? verifyBadge(loading ? null : c && c.verified, S.cat.sql.available) : null,
    h('span', {}, lang === 'sql' ? 'DuckDB · one CTE per step · run from the repository root' : lang === 'python' ? 'pandas · the same logic the app uses' : 'The steps exactly as the engine runs them'));
  const body = !c ? h('div', { class: 'cv-wait' }, h('i', { class: 'cv-spin' }), 'Writing the code…')
    : codeView(text, { lang, lines: lang === 'sql' ? c.lines : {}, selected: typeof S.sel === 'number' ? S.sel : null,
      onHover: cid => { if (S.hoverCid !== cid) { S.hoverCid = cid; renderGraph(); } } });
  return h('div', { class: `cv-codewrap${loading ? ' loading' : ''}` }, bar, meta, body);
}
function helpPanel() {
  return h('div', { class: 'cv-pad' },
    h('p', { class: 'cv-sec-title' }, 'On the canvas'),
    h('ul', { class: 'cv-help-list' },
      h('li', {}, h('i', { class: 'cv-lg' }), 'Samples flowing on (the width is to scale)'),
      h('li', {}, h('i', { class: 'cv-lg infl' }), 'Extra rows a join added'),
      h('li', {}, h('i', { class: 'cv-lg drop' }), 'Samples a step removed'),
      h('li', {}, h('i', { class: 'glyph g-patient' }), 'One row per patient'),
      h('li', {}, h('i', { class: 'glyph g-sample' }), 'One row per sample'),
      h('li', {}, h('i', { class: 'glyph g-multi' }), 'Several rows per sample')),
    h('section', { class: 'cv-sec' }, h('p', { class: 'cv-sec-title' }, 'Doing things'),
      h('ul', { class: 'cv-list' },
        h('li', {}, h('b', {}, 'Add'), ' opens the blocks: drag one onto a wire, or click it.'),
        h('li', {}, 'The number on a wire is the samples flowing on. Click it to add a step there.'),
        h('li', {}, 'Click a card to change it; drag a card to move it.'),
        h('li', {}, h('b', {}, 'Grain lanes'), ' puts each card in the lane of what one row stands for, so a join that counts samples twice drops a lane.'))),
    h('section', { class: 'cv-sec' }, h('p', { class: 'cv-sec-title' }, 'Keys'),
      h('ul', { class: 'cv-help-list' }, [['A', 'add a block'], ['/', 'describe in words'], ['← →', 'move between cards'], ['Alt + ← →', 'move the card'],
        ['Delete', 'remove the step'], ['Ctrl + Z', 'undo'], ['F', 'fit to view'], ['Esc', 'close what’s open']].map(([k, v]) => h('li', {}, h('kbd', {}, k), v)))));
}
function editJson() {
  const ta = h('textarea', { class: 'cv-jsonarea', spellcheck: 'false', 'aria-label': 'Steps as JSON' });
  ta.value = JSON.stringify(S.steps.map(strip), null, 2);
  const err = h('p', { class: 'cv-hint', style: 'padding:0 14px' });
  const apply = () => {
    try {
      const steps = JSON.parse(ta.value);
      if (!Array.isArray(steps)) throw new Error('Expected a list of steps');
      loadSteps(steps, 'Steps replaced from JSON');
      showPanel('code');                            // back to the code; it refreshes after the run
    }
    catch (e) { err.textContent = e.message; }
  };
  $('#cv-panel-body').replaceChildren(h('div', { class: 'cv-codewrap' }, h('div', { class: 'cv-code-bar' }, h('b', {}, 'Edit the steps'), h('span', { class: 'spacer' }),
    h('button', { type: 'button', class: 'btn small ghost', onclick: () => renderPanel() }, 'Cancel'), h('button', { type: 'button', class: 'btn small ink', onclick: apply }, ic('check'), 'Apply')), ta, err));
  paintIcons();
  ta.focus();
}
async function copyText(text) { try { await navigator.clipboard.writeText(text); toast('Copied', 'copy'); } catch { errorToast('Couldn’t copy: select the text and press Ctrl+C.'); } }
function download(text, name) {
  const a = h('a', { href: URL.createObjectURL(new Blob([text], { type: 'text/plain' })), download: name });
  document.body.append(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}

// ---------------------------------------------------------------- top status, toolbar, empty state, slides sheet
function renderStatus() {
  const ev = S.ev, f = ev && !ev.empty ? ev.final : null;
  const issues = ev ? ev.steps.filter(x => x.inflated || x.error || shiftWarn(x)).length : 0;
  $('#cv-status').replaceChildren(h('button', { type: 'button', class: `cv-stat-pill${S.evPending ? ' pending' : ''}`, title: 'The cohort: verdict, checks and who is in',
    onclick: () => showPanel('cohort') },
  h('span', { class: 'cv-stat-n' }, h('b', {}, fmt(f ? f.samples : S.meta.release.samples)), h('span', { class: 'cv-unit' }, ' samples')),
  h('span', { class: 'muted' }, `${fmt(f ? f.patients : S.meta.release.patients)} patients`),
  ev && ev.verdict ? verdictLabel(ev.verdict.verdict) : null,
  issues ? h('span', { class: 'cv-issues', title: `${issues} step${issues === 1 ? '' : 's'} to look at` }, ic('triangle-alert'), String(issues)) : null));
}
function renderToolbar() {
  const tb = (icon, label, on, pressed, cls = '') => h('button', { type: 'button', class: `tb ${cls}`, 'aria-pressed': pressed === undefined ? null : String(!!pressed), title: label, onclick: on },
    ic(icon), h('span', { class: 'tb-label' }, label));
  const openChecks = S.ev && S.ev.verdict ? S.ev.verdict.checks.filter(c => c.status !== 'pass').length : 0;
  const cohort = tb('list-checks', 'Cohort', () => openPanel('cohort'), S.panel === 'cohort');
  if (openChecks) cohort.append(h('span', { class: 'tb-n' }, String(openChecks)));
  $('#cv-toolbar').replaceChildren(
    tb('plus', 'Add', () => (S.library ? closeLibrary() : openLibrary(null)), S.library, 'primary'),
    tb('sparkles', 'Ask', () => toggleAsk(), S.ask),
    h('span', { class: 'tb-sep' }),
    tb('workflow', 'Flow', () => setLayout('flow'), S.layout === 'flow'),
    tb('rows-3', 'Grain lanes', () => setLayout('lanes'), S.layout === 'lanes'),
    h('span', { class: 'tb-sep' }),
    cohort,
    tb('code-xml', 'Code', () => openPanel('code'), S.panel === 'code'),
    tb('images', 'Slides', () => toggleSheet(), S.sheet),
    h('span', { class: 'tb-sep' }),
    tb('circle-help', 'Help', () => openPanel('help'), S.panel === 'help'));
  paintIcons();
}
function updateChrome() {
  if (!S.meta) return;
  $('#cv-undo').disabled = !S.undo.length;
  $('#cv-redo').disabled = !S.redo.length;
  const ev = S.ev, exp = $('#cv-export');
  exp.classList.toggle('locked', !!(ev && !ev.empty && !ev.exportable));
  exp.title = ev && !ev.exportable ? (ev.export_block_reason || 'Export is paused') : 'Save the cohort and download cohort_samples.csv';
  renderStatus();
  renderToolbar();
  renderAskMode();
}
function setLayout(l) { S.layout = l; store.set('cv-layout', l); graph.setLayout(l); renderToolbar(); }
function renderEmpty() {
  const host = $('#cv-empty');
  if (!S.meta || S.steps.length) { host.hidden = true; return; }
  host.hidden = false;
  if (host.dataset.ready) return;
  host.dataset.ready = '1';
  host.replaceChildren(h('div', { class: 'cv-empty-card' },
    h('p', { class: 'label' }, 'Canvas'),
    h('h2', {}, 'Start a cohort'),
    h('p', {}, 'Pick a starting point, press Add to drag in blocks, or Ask to describe it in words.'),
    h('div', { class: 'cv-empty-grid' },
      h('button', { type: 'button', class: 'cv-empty-opt', onclick: () => addBlock({ kind: 'sample_type', value: 'Primary Tumor' }) },
        h('span', { class: 'cv-tag' }, 'Blank'), h('b', {}, 'Primary tumours'), h('small', {}, `${fmt(S.cat.sample_types[0].count)} samples, one per patient`)),
      ...S.cat.templates.slice(0, 5).map(t => h('button', { type: 'button', class: 'cv-empty-opt', onclick: () => loadTemplate(t) },
        h('span', { class: 'cv-tag' }, t.tag), h('b', {}, t.label), h('small', {}, t.description))))));
  paintIcons();
}
function toggleSheet(force) {
  S.sheet = force ?? !S.sheet;
  renderSheet();
  renderToolbar();
  if (S.sheet) fetchGallery();
}
function renderSheet() {
  const host = $('#cv-sheet');
  host.hidden = !S.sheet;
  if (!S.sheet) return;
  const g = S.gallery;
  host.replaceChildren(
    h('div', { class: 'cv-sheet-head' }, h('h2', {}, 'Slides in this cohort'),
      h('p', {}, g ? `${fmt(g.total)} slides from ${fmt(g.samples_with_slides)} of ${fmt(g.cohort_samples)} samples${g.items.length < g.total ? ` · showing ${g.items.length}` : ''}` : 'Finding them…'),
      seg([['auto', 'As joined'], ['all', 'All kinds'], ['dx', 'Diagnostic'], ['frozen', 'Frozen']], S.galKinds, v => { S.galKinds = v; S.galStale = true; fetchGallery(); }),
      iconBtn('x', 'Close', () => toggleSheet(false))),
    h('div', { class: 'cv-sheet-body' }, galleryPanel(g, { onXray: pid => xray(pid, null) })));
  paintIcons();
}

// ---------------------------------------------------------------- the block library (Add)
function block(kind, attrs, { label, detail, icon, cls, meta, search }) {
  const el = h('button', { type: 'button', class: 'cv-block', 'data-search': (search || `${label} ${detail || ''}`).toLowerCase(), title: 'Drag onto a wire, or click to add' },
    h('span', { class: `cv-block-ico ${cls}` }, ic(icon)),
    h('span', { class: 'cv-block-txt' }, h('b', {}, label), detail ? h('small', {}, detail) : null),
    meta !== undefined ? h('span', { class: 'cv-block-meta' }, meta) : null);
  const payload = { kind, ...attrs };
  el.addEventListener('pointerdown', e => {
    if (e.button !== 0) return;
    e.preventDefault();                      // no text selection; a drag or a click decides
    let dragged = false;
    const sx = e.clientX, sy = e.clientY;
    const cleanup = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
    const move = ev => {
      if (Math.abs(ev.clientX - sx) + Math.abs(ev.clientY - sy) <= 6) return;
      cleanup();
      dragged = true;
      graph.startDrag(ev, payload, label, icon);
    };
    const up = () => { cleanup(); if (!dragged) { addBlock(payload); closeLibrary(); } };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  });
  el.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); addBlock(payload); closeLibrary(); } });
  return el;
}
function section(title, items, { open = false, note = '' } = {}) {
  return h('details', { class: 'cv-psec', open: open || null }, h('summary', {}, h('span', {}, title), h('small', {}, String(items.length))),
    note ? h('p', { class: 'cv-psec-note' }, note) : null, h('div', { class: 'cv-blocks' }, items));
}
function buildLibrary() {
  const cat = S.cat, meta = S.meta;
  const slides = cat.tables.find(t => t.id === 'slides');
  const normals = cat.tables.find(t => t.id === 'normals');
  const field = (f, detail) => block('field', { table: f.table, field: f.field }, { label: f.label.split(' (')[0], detail, cls: 'op-filter',
    icon: { time: 'timer', event: 'activity', numeric: 'sigma', stage: 'signal-high' }[f.kind] || 'tag', search: `${f.label} ${f.field} ${(f.values || []).map(v => v.value).join(' ')}` });
  const fieldDetail = f => (f.values || []).filter(v => !v.flag).slice(0, 3).map(v => `${v.value.split(' (')[0]} ${fmt(v.count)}`).join(' · ') || (f.range ? `${f.range[0]}–${f.range[1]}` : f.kind);
  const byName = name => meta.fields.find(f => f.table === 'clinical' && f.field === name);
  const common = [
    block('sample_type', { value: 'Primary Tumor' }, { label: 'Primary tumours', detail: 'Start from tumours, one per patient', icon: 'flask-conical', cls: 'op-sample' }),
    block('layer', { value: 'rna' }, { label: 'Has RNA data', detail: 'RNA expression', icon: 'layers', cls: 'op-layer' }),
    block('layer', { value: 'protein' }, { label: 'Has protein data', detail: 'RPPA', icon: 'layers', cls: 'op-layer' }),
    ...['er_status', 'pam50', 'stage'].map(byName).filter(Boolean).map(f => field(f, fieldDetail(f))),
    block('slides', { kinds: ['diagnostic (FFPE)'], mode: 'rows' }, { label: 'Diagnostic slides', detail: `${slides.per_sample.mean} per sample, one row each`, icon: 'image', cls: 'op-join' }),
    block('gene', { value: 'TP53' }, { label: 'Gene mutation', detail: 'TP53; change it after adding', icon: 'dna', cls: 'op-mutation' }),
    block('one_per_patient', {}, { label: 'One sample per patient', detail: 'Primary tumour first', icon: 'user-round', cls: 'op-patient' }),
  ];
  const groups = {};
  meta.fields.forEach(f => { (groups[f.group] = groups[f.group] || []).push(f); });
  const sections = [
    section('Common', common, { open: true }),
    section('Start from', cat.sample_types.map(t => block('sample_type', { value: t.value }, { label: groupLabel(t.value), detail: 'Sample type', icon: 'flask-conical', cls: 'op-sample', meta: fmt(t.count) }))),
    section('Tables', [
      block('slides', { kinds: ['diagnostic (FFPE)'], mode: 'rows' }, { label: 'Diagnostic slides', detail: 'One row per slide', icon: 'image', cls: 'op-join', meta: fmt((slides.kinds.find(k => k.short === 'DX') || {}).count) }),
      block('slides', { kinds: FROZEN, mode: 'rows' }, { label: 'Frozen-section slides', detail: 'Top, middle and bottom', icon: 'snowflake', cls: 'op-join' }),
      block('slides', { kinds: [], mode: 'exists' }, { label: 'Has any slide', detail: 'Once per sample: adds no rows', icon: 'images', cls: 'op-join' }),
      block('clinical', {}, { label: 'Clinical records', detail: 'One per sample', icon: 'clipboard-list', cls: 'op-join' }),
      block('survival', {}, { label: 'Curated survival', detail: 'OS, PFI, DSS, DFI', icon: 'activity', cls: 'op-join' }),
      block('normals', { pairs: false }, { label: 'Has matched normal', detail: 'Patient also has normal tissue', icon: 'copy-plus', cls: 'op-join', meta: fmt(normals.rows) }),
      block('normals', { pairs: true }, { label: 'Tumour–normal pairs', detail: 'Adds each normal to the cohort', icon: 'git-merge', cls: 'op-join' })]),
    section('Data layers', cat.layers.map(l => block('layer', { value: l.value }, { label: LAYERS[l.value] || l.label, detail: `${Math.round(l.share * 100)}% of samples`, icon: 'layers', cls: 'op-layer', meta: fmt(l.count) }))),
    ...Object.entries(groups).map(([g, fs]) => section(g, fs.map(f => field(f, fieldDetail(f))))),
    section('Genes', cat.genes.slice(0, 40).map(g => block('gene', { value: g.value }, { label: g.value, detail: 'mutated in primary tumours', icon: 'dna', cls: 'op-mutation', meta: fmt(g.count) }))),
    section('Rules', [
      block('one_per_patient', {}, { label: 'One sample per patient', detail: 'By type, or earliest / latest', icon: 'user-round', cls: 'op-patient' }),
      block('dedupe', {}, { label: 'One row per sample', detail: 'Undo a join’s extra rows', icon: 'copy-minus', cls: 'op-sample' }),
      block('any_of', {}, { label: 'Either / or', detail: 'Any of several conditions', icon: 'split', cls: 'op-filter' }),
      block('where', {}, { label: 'Combined condition', detail: 'AND / OR / NOT', icon: 'git-branch', cls: 'op-filter' }),
      block('exclude_layer', { value: 'protein' }, { label: 'Exclude a data layer', detail: 'Keep samples without it', icon: 'ban', cls: 'op-exclude' })]),
    section('Templates', cat.templates.map(t => block('template', { template: t }, { label: t.label, detail: t.description, icon: 'layout-template', cls: 'op-sample' }))),
  ];
  const gaps = h('details', { class: 'cv-psec gaps' }, h('summary', {}, h('span', {}, 'Not in this release'), h('small', {}, String(cat.not_in_release.length))),
    h('p', { class: 'cv-psec-note' }, 'No file records these, so nothing can filter on them.'),
    h('ul', { class: 'cv-gaps' }, cat.not_in_release.map(x => h('li', {}, ic('circle-slash'), x))));
  const typedGene = h('div', { class: 'cv-blocks', hidden: true });
  const shownGenes = new Set(['TP53', ...cat.genes.slice(0, 40).map(g => g.value)]);
  const geneBlock = (value, count) => block('gene', { value }, { label: value, detail: 'Gene mutation', icon: 'dna', cls: 'op-mutation',
    meta: count === undefined || count === null ? undefined : fmt(count) });
  let geneSeq = 0, geneTimer = null;
  const noHit = h('p', { class: 'muted cv-pnohit', hidden: true }, 'Nothing matches. Try a field, a value or a gene symbol.');
  const search = h('input', { type: 'search', class: 'cv-lib-search', placeholder: 'Search fields, values, genes…', 'aria-label': 'Search blocks' });
  const scroll = h('div', { class: 'cv-lib-scroll' }, h('p', { class: 'cv-lib-hint' }, 'Drag a block onto a wire, or click it to add it.'), typedGene, ...sections, gaps, noHit);
  $('#cv-library').replaceChildren(
    h('div', { class: 'cv-lib-head' }, h('h2', {}, 'Add a block'), iconBtn('x', 'Close (Esc)', closeLibrary)),
    search, h('div', { id: 'cv-lib-at' }), scroll);
  search.addEventListener('input', () => {
    const q = search.value.trim().toLowerCase();
    let any = false;
    $$('.cv-psec', scroll).forEach(sec => {
      if (sec.classList.contains('gaps')) { sec.hidden = !!q && !sec.textContent.toLowerCase().includes(q); return; }
      let hits = 0;
      $$('.cv-block', sec).forEach(b => { const on = !q || b.dataset.search.includes(q); b.hidden = !on; if (on) hits += 1; });
      sec.hidden = !!q && !hits;
      sec.open = q ? hits > 0 : sec === sections[0];
      if (hits) any = true;
    });
    // a gene symbol not in the list above: ask the server whether the mutation file has it (and what was meant if not)
    const up = q.toUpperCase();
    const geneLike = /^[A-Z][A-Z0-9-]{1,19}$/.test(up) && !shownGenes.has(up);
    const seq = ++geneSeq;
    clearTimeout(geneTimer);
    typedGene.hidden = true;
    noHit.hidden = any || geneLike;
    if (geneLike) {
      geneTimer = setTimeout(async () => {
        let r = null;
        try { r = await api(`/api/canvas/genes?q=${encodeURIComponent(up)}`); } catch { /* offline: the sections above still work */ }
        if (seq !== geneSeq) return;
        const hits = r ? r.matches.filter(m => !shownGenes.has(m.value) && (!any || m.value === up)) : [];
        const near = r && !hits.length && !any ? r.closest : [];
        typedGene.replaceChildren(...hits.map(g => geneBlock(g.value, g.count)),
          ...(near.length ? [h('p', { class: 'cv-psec-note' }, `No gene called ${up} in the mutation file. Did you mean:`), ...near.map(g => geneBlock(g))] : []));
        typedGene.hidden = !hits.length && !near.length;
        noHit.hidden = any || !typedGene.hidden;
        paintIcons();
      }, 160);
    }
    paintIcons();
  });
  paintIcons();
}
function openLibrary(slot) {
  S.library = true;
  S.addSlot = slot;
  const host = $('#cv-library');
  const was = !host.hidden;
  host.hidden = false;
  $('#cv-app').classList.add('library-open');
  if (!was) {
    host.classList.remove('cv-in-left'); void host.offsetWidth; host.classList.add('cv-in-left');
    const search = $('.cv-lib-search');                  // each opening starts from the full list, not the last search
    if (search && search.value) { search.value = ''; search.dispatchEvent(new Event('input')); }
  }
  $('#cv-lib-at').replaceChildren(...(slot === null || slot === undefined ? [] : [h('div', { class: 'cv-lib-at' }, ic('map-pin'),
    slot === 0 ? 'Adding a first step' : `Adding after step ${slot}`, h('button', { type: 'button', onclick: () => openLibrary(null) }, 'add at the end'))]));
  renderToolbar();
  paintIcons();
  setTimeout(() => $('.cv-lib-search').focus({ preventScroll: true }), 30);
}
function closeLibrary() { S.library = false; S.addSlot = null; $('#cv-library').hidden = true; $('#cv-app').classList.remove('library-open'); renderToolbar(); }

// ---------------------------------------------------------------- words
function toggleAsk(force) {
  S.ask = force ?? !S.ask;
  const f = $('#cv-ask');
  f.hidden = !S.ask;
  if (S.ask) { f.classList.remove('cv-in-up'); void f.offsetWidth; f.classList.add('cv-in-up'); setTimeout(() => $('#cv-q').focus(), 20); }
  else { const pop = $('.cv-askpop'); if (pop) pop.remove(); }
  renderToolbar();
}
function renderAskMode() {
  const can = S.ai && S.steps.length;
  if (!can && S.askMode === 'change') S.askMode = 'new';
  $('#cv-ask-mode').replaceChildren(...[['new', 'New'], ['change', 'Change this']].map(([v, l]) => h('button', { type: 'button', 'aria-pressed': String(S.askMode === v),
    disabled: (v === 'change' && !can) || null, title: v === 'change' && !S.ai ? 'Changing a cohort in words needs the AI model' : '',
    onclick: () => { S.askMode = v; renderAskMode(); $('#cv-q').placeholder = v === 'change' ? 'e.g. drop the slide requirement' : 'Describe a cohort…'; $('#cv-q').focus(); } }, l)));
}
async function ask(text) {
  const q = text.trim();
  if (!q) return;
  const change = S.askMode === 'change' && S.steps.length;
  const btn = $('#cv-ask-go');
  btn.disabled = true; btn.classList.add('busy');
  try {
    const body = { query: q, use_ai: S.ai };
    if (change) Object.assign(body, { current_steps: forServer(), current_request: summarySentence() });
    const d = await api('/api/draft', { method: 'POST', body });
    (d.unsupported || []).slice(0, 2).forEach(u => warnToast(`Not in this release: ${u.phrase || u.what || ''}`));
    let spec = d.spec;
    if (d.clarifications && d.clarifications.length) spec = await askQuestions(d);
    if (!spec) return;
    loadSteps(spec.steps, change ? 'Changed in words. Ctrl+Z goes back.' : 'Drawn from your words. Every card can be changed.');
    $('#cv-q').value = '';
    toggleAsk(false);
  } catch (e) { errorToast(e.message); } finally { btn.disabled = false; btn.classList.remove('busy'); }
}
function askQuestions(d) {
  return new Promise(resolve => {
    const answers = {};
    const pop = h('div', { class: 'cv-askpop', role: 'dialog', 'aria-label': 'A choice to make' });
    const done = spec => { pop.remove(); resolve(spec); };
    pop.append(h('div', { class: 'cv-askpop-head' }, h('b', {}, d.clarifications.length === 1 ? 'One choice changes who is in' : `${d.clarifications.length} choices change who is in`),
      iconBtn('x', 'Cancel', () => done(null))),
    ...d.clarifications.map(q => {
      const max = Math.max(1, ...q.options.map(o => o.n_samples || 0));
      return h('div', { class: 'cv-q' }, h('p', { class: 'cv-q-t' }, rich(q.question)), q.why ? h('p', { class: 'muted' }, rich(q.why)) : null,
        h('div', { class: 'cv-q-opts' }, q.options.map((o, i) => h('button', { type: 'button', class: 'cv-q-opt', 'aria-pressed': 'false', onclick: async ev => {
          answers[q.id] = i;
          ev.currentTarget.parentElement.querySelectorAll('.cv-q-opt').forEach(b => b.setAttribute('aria-pressed', String(b === ev.currentTarget)));
          if (Object.keys(answers).length < d.clarifications.length) return;
          try { const r = await api(`/api/drafts/${d.draft_id}/resolve`, { method: 'POST', body: { answers } }); done(r.spec); } catch (e) { errorToast(e.message); done(null); }
        } }, i === (q.recommended ?? 0) ? h('span', { class: 'cv-tag' }, 'Standard') : null, h('span', {}, o.label),
        h('b', {}, o.n_samples === null || o.n_samples === undefined ? '—' : fmt(o.n_samples)),
        h('span', { class: 'cv-q-bar' }, h('i', { style: `width:${(100 * (o.n_samples || 0)) / max}%` }))))));
    }));
    $('#cv-ask').append(pop);
    paintIcons();
  });
}
const summarySentence = () => S.steps.filter(s => !s._off).map(s => describe(S.meta, s)).join('; ') || 'All samples';

// ---------------------------------------------------------------- saving and exporting
async function saveCohort() {
  const steps = forServer().filter(s => !s._off);
  if (!steps.length) { warnToast('Add a step first.'); return null; }
  let res;
  if (S.searchId) { try { res = await api(`/api/searches/${S.searchId}/versions`, { method: 'POST', body: { steps, note: 'Edited on the canvas', source: 'edit' } }); } catch { S.searchId = null; } }
  if (!res) res = await api('/api/fork', { method: 'POST', body: { steps, query: summarySentence(), note: 'Built on the canvas' } });
  S.searchId = res.search_id;
  persist();
  return res;
}
async function saveAndOpen() { try { const res = await saveCohort(); if (res) location.href = `/?open=${res.search_id}`; } catch (e) { errorToast(e.message); } }
/** The top bar's Save: keep working on the canvas; the cohort appears in the Build page's history. */
async function saveHere() {
  try {
    const res = await saveCohort();
    if (!res) return;
    const n = (res.versions || []).length;
    toast(`Saved${n > 1 ? ` as version ${n}` : ''}. It's in your history on the Build page.`, 'save');
  } catch (e) { errorToast(e.message); }
}
async function exportCsv() {
  const ev = S.ev;
  if (!ev || ev.empty || !ev.exportable) { warnToast(ev && !ev.empty ? ev.export_block_reason || 'Export is paused.' : 'Add a step first.'); showPanel('cohort'); return; }
  try {
    const res = await saveCohort();
    if (!res) return;
    const a = h('a', { href: `/api/searches/${res.search_id}/files/cohort_samples.csv?user=${encodeURIComponent(session.user)}`, download: 'cohort_samples.csv' });
    document.body.append(a); a.click(); a.remove();
    toast(`Saved and exported ${fmt(ev.final.samples)} samples`, 'download');
  } catch (e) { errorToast(e.message); }
}
function xray(patientId, cid) {
  const st = cid !== null && cid !== undefined ? stepByCid(cid) : null;
  openXray($('#cv-xray'), patientId, { step: st, stepIndex: st ? S.steps.indexOf(st) : 0, inCohort: S.ev ? new Set(S.ev.cohort_ids || []) : null });
}

// ---------------------------------------------------------------- keys
function initKeys() {
  const stage = $('#cv-stage');
  stage.addEventListener('keydown', e => {
    if (e.target.closest('input, textarea, select')) return;
    const ids = ['start', ...S.steps.map(s => s._cid), 'end'];
    const i = ids.indexOf(S.sel);
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault();
      const d = e.key === 'ArrowRight' ? 1 : -1;
      if (e.altKey && typeof S.sel === 'number') { moveStep(S.sel, S.steps.findIndex(s => s._cid === S.sel) + d); return; }
      select(ids[Math.max(0, Math.min(ids.length - 1, (i < 0 ? 0 : i) + d))]);
      const el = stage.querySelector(`.cv-node[data-id="${S.sel}"]`);
      if (el) el.focus({ preventScroll: true });
    } else if ((e.key === 'Delete' || e.key === 'Backspace') && typeof S.sel === 'number') { e.preventDefault(); removeStep(S.sel); }
  });
  document.addEventListener('keydown', e => {
    const typing = e.target.closest && e.target.closest('input, textarea, select, [contenteditable]');
    const mod = e.ctrlKey || e.metaKey;
    if (mod && !typing && e.key.toLowerCase() === 'z') { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return; }
    if (mod && !typing && e.key.toLowerCase() === 'y') { e.preventDefault(); redo(); return; }
    if (e.key === 'Escape') {
      if ($('#cv-xray').open) return;
      if (S.ask) { toggleAsk(false); return; }
      if (S.library) { closeLibrary(); return; }
      if (S.sheet) { toggleSheet(false); return; }
      if (S.panel) { closePanel(); return; }
      select(null);
      return;
    }
    if (typing || mod || e.altKey) return;
    if (e.key === '/') { e.preventDefault(); toggleAsk(true); }
    else if (e.key === 'a' || e.key === 'A') { e.preventDefault(); if (S.library) closeLibrary(); else openLibrary(null); }
    else if (e.key === 'f' || e.key === 'F') graph.fit();
    else if (e.key === '+' || e.key === '=') graph.zoomIn();
    else if (e.key === '-') graph.zoomOut();
  });
}

// ---------------------------------------------------------------- start
async function init() {
  initChrome({ onTheme: () => renderGraph() });
  let meta, cat;
  try { [meta, cat] = await Promise.all([loadMeta(), api('/api/canvas/catalog')]); } catch (e) {
    $('#cv-stage').append(h('div', { class: 'cv-empty' }, h('div', { class: 'cv-empty-card' }, h('h2', {}, 'The server didn’t answer'), h('p', {}, e.message))));
    return;
  }
  S.meta = meta; S.cat = cat; S.ai = !!meta.ai.available;
  showModelStatus(meta.ai);
  buildLibrary();
  initKeys();
  graph.setLayout(S.layout);
  $('#cv-undo').addEventListener('click', undo);
  $('#cv-redo').addEventListener('click', redo);
  $('#cv-fit').addEventListener('click', () => graph.fit());
  $('#cv-zoom-in').addEventListener('click', () => graph.zoomIn());
  $('#cv-zoom-out').addEventListener('click', () => graph.zoomOut());
  $('#cv-zoom-n').addEventListener('click', () => graph.zoomReset());
  $('#cv-save').addEventListener('click', saveHere);
  $('#cv-export').addEventListener('click', exportCsv);
  $('#cv-ask').addEventListener('submit', e => { e.preventDefault(); ask($('#cv-q').value); });
  if (!S.ai) $('#cv-q').placeholder = 'Describe a cohort (the built-in parser drafts it)';

  const params = new URLSearchParams(location.search);
  let loaded = false;
  if (params.get('open')) {
    try {
      const r = await api(`/api/searches/${Number(params.get('open'))}`);
      S.searchId = r.search_id; S.steps = (r.spec.steps || []).map(withCid); loaded = true;
      toast(`Opened “${r.query}”`, 'folder-open');
    } catch (e) { errorToast(e.message); }
  } else if (params.get('template') && cat.templates[Number(params.get('template'))]) {
    S.steps = cat.templates[Number(params.get('template'))].steps.map(withCid); loaded = true;
  }
  if (!loaded) {
    try {
      const saved = JSON.parse(store.get('cv-state', 'null'));
      if (saved && Array.isArray(saved.steps)) {
        S.steps = saved.steps.filter(s => s && s.op).map(s => ({ ...s }));
        S.searchId = saved.searchId || null;
        cidSeq = Math.max(1, ...S.steps.map(s => (s._cid || 0) + 1));
        S.steps.forEach(s => { if (!s._cid) s._cid = cidSeq++; });
      }
    } catch { /* a broken saved canvas starts empty */ }
  }
  renderGraph();
  renderEmpty();
  updateChrome();
  if (params.get('q')) { $('#cv-q').value = params.get('q'); toggleAsk(true); ask(params.get('q')); }
  evaluate();
}
init();
