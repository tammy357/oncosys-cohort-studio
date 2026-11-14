// Views the canvas shows beside the graph: who is in (composition bars), the code (SQL / Python / JSON with each
// step's lines linked to its card), one patient as a tree (the "X-ray"), and the cohort's real slide images.
import { h, ic, paintIcons, api, fmt, rich, tipped, errorToast } from './common.js';

// ---------------------------------------------------------------- who is in: categories, colours, bars
export const CHARS = [
  { key: 'PAM50 subtype', name: 'Subtype', css: 'pam', order: ['LumA', 'LumB', 'Basal', 'Her2', 'Normal'] },
  { key: 'ER status (IHC)', name: 'ER status', css: 'er', order: ['Positive', 'Negative'] },
  { key: 'Stage', name: 'Stage', css: 'stage', order: ['I', 'II', 'III', 'IV'] },
  { key: 'Age band', name: 'Age', css: 'age', order: ['under 50', '50-64', '65 and over'] },
  { key: 'Sample type', name: 'Sample type', css: 'type', order: ['Primary Tumor', 'Solid Tissue Normal', 'Metastatic'] },
];
export const NA = '(not recorded)';
const LABEL = { LumA: 'Luminal A', LumB: 'Luminal B', Her2: 'HER2-enriched', Basal: 'Basal-like', Normal: 'Normal-like', Positive: 'ER-positive',
  Negative: 'ER-negative', I: 'Stage I', II: 'Stage II', III: 'Stage III', IV: 'Stage IV', 'under 50': 'Under 50', '50-64': '50–64', '65 and over': '65 and over',
  'Primary Tumor': 'Primary tumour', 'Solid Tissue Normal': 'Normal tissue', Metastatic: 'Metastatic', [NA]: 'Not recorded' };
export const groupLabel = g => LABEL[g] || g;
const pctOf = (n, d) => (d ? `${Math.round((100 * n) / d)}%` : '–');
export function ordered(counts, ch) {
  const keys = Object.keys(counts || {});
  const extra = keys.filter(k => !ch.order.includes(k) && k !== NA).sort();
  return [...ch.order, ...extra, NA].filter(k => counts && counts[k]).map(k => [k, counts[k]]);
}
export const swatch = (ch, g) => (g === NA ? 'var(--fz-na)' : ch.order.includes(g) ? `var(--fz-${ch.css}-${ch.order.indexOf(g)})` : 'var(--fz-other)');
export function mixBar(counts, ch, { cls = 'cv-mix' } = {}) {
  const pairs = ordered(counts, ch);
  const total = pairs.reduce((a, [, n]) => a + n, 0);
  const tip = pairs.map(([g, n]) => `${groupLabel(g)}: ${fmt(n)} (${pctOf(n, total)})`).join('\n');
  return tipped(h('span', { class: cls, role: 'img', 'aria-label': `${ch.name}: ${pairs.map(([g, n]) => `${groupLabel(g)} ${pctOf(n, total)}`).join(', ')}` },
    pairs.map(([g, n]) => h('i', { style: `flex:${n} 1 0;background:${swatch(ch, g)}` }))), tip, `${ch.name} after this step`);
}

/** Start vs final mix for every characteristic, with the shift (TVD) the engine measured. */
export function compositionPanel(ev) {
  const comp = ev.composition || {};
  const shift = Object.fromEntries((ev.overall_shift || []).map(x => [x.characteristic, x]));
  const rows = CHARS.filter(ch => comp[ch.key]).map(ch => {
    const sh = shift[ch.key];
    const flag = sh ? (sh.expected ? 'expected' : sh.tvd >= (ev.shift_threshold || 0.1) ? 'warn' : '') : '';
    return h('div', { class: `cv-comp-row ${flag}` },
      h('span', { class: 'cv-comp-name' }, ch.name,
        sh ? tipped(h('span', { class: `cv-tvd ${flag}` }, `TVD ${sh.tvd.toFixed(2)}`),
          `${groupLabel(sh.group)}: ${sh.before_pct}% → ${sh.after_pct}%${sh.expected ? `\nExpected: ${sh.expected}` : ''}${sh.p_value !== undefined ? `\nRandom dropout moves it this much ${Math.round(sh.p_value * 100)}% of the time` : ''}`,
          'Shift from the starting population') : null),
      h('div', { class: 'cv-comp-bars' },
        h('div', { class: 'cv-comp-line' }, h('small', {}, 'start'), mixBar(comp[ch.key].start, ch, { cls: 'cv-mix wide' })),
        h('div', { class: 'cv-comp-line' }, h('small', {}, 'cohort'), mixBar(comp[ch.key].final, ch, { cls: 'cv-mix wide' }))));
  });
  const legend = h('ul', { class: 'cv-legend-list' }, ordered((comp['PAM50 subtype'] || {}).start, CHARS[0]).map(([g]) =>
    h('li', {}, h('i', { style: `background:${swatch(CHARS[0], g)}` }), groupLabel(g))));
  return h('div', { class: 'cv-comp' }, rows, legend);
}

// ---------------------------------------------------------------- code view (SQL highlighted, lines linked to steps)
const KW = new Set(('select from where join on and or not in as with create replace table macro case when then else end is null true false qualify over '
  + 'partition by order group distinct union all exclude unpivot columns into name value between like asc desc nulls last first left inner semi '
  + 'cast try_cast coalesce nullif count limit having').split(' '));
function highlightSql(line) {
  const out = [];
  const re = /(--.*$)|('(?:[^']|'')*')|("(?:[^"]|"")*")|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)(?=\s*\()|([A-Za-z_][A-Za-z0-9_]*)|(\s+)|(.)/g;
  let m;
  while ((m = re.exec(line))) {
    if (m[1]) out.push(h('span', { class: 't-com' }, m[1]));
    else if (m[2]) out.push(h('span', { class: 't-str' }, m[2]));
    else if (m[3]) out.push(h('span', { class: 't-id' }, m[3]));
    else if (m[4]) out.push(h('span', { class: 't-num' }, m[4]));
    else if (m[5]) out.push(h('span', { class: KW.has(m[5].toLowerCase()) ? 't-kw' : 't-fn' }, m[5]));
    else if (m[6]) out.push(KW.has(m[6].toLowerCase()) ? h('span', { class: 't-kw' }, m[6]) : m[6]);
    else out.push(m[7] || m[8]);
  }
  return out;
}
function highlightPy(line) {
  const out = [];
  const re = /(#.*$)|("""|'''|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|(\b(?:def|return|import|from|for|in|if|elif|else|not|and|or|None|True|False|with|as|class|lambda|try|except|raise|while|print)\b)|(\b\d+(?:\.\d+)?\b)|(.)/g;
  let m;
  while ((m = re.exec(line))) {
    if (m[1]) out.push(h('span', { class: 't-com' }, m[1]));
    else if (m[2]) out.push(h('span', { class: 't-str' }, m[2]));
    else if (m[3]) out.push(h('span', { class: 't-kw' }, m[3]));
    else if (m[4]) out.push(h('span', { class: 't-num' }, m[4]));
    else out.push(m[5]);
  }
  return out;
}
/** lines: { cid: [start, end] } (1-based); returns the element (it scrolls the selected step's lines into view). */
export function codeView(text, { lang = 'sql', lines = {}, selected = null, onHover = null } = {}) {
  const rows = String(text || '').split('\n');
  const owner = new Array(rows.length + 1).fill(null);
  Object.entries(lines).forEach(([cid, [a, b]]) => { for (let i = a; i <= b; i++) owner[i] = cid; });
  const hl = lang === 'sql' ? highlightSql : lang === 'python' ? highlightPy : l => [l];
  const pre = h('div', { class: `cv-code ${lang}`, role: 'region', 'aria-label': `${lang.toUpperCase()} code`, tabindex: '0' },
    rows.map((l, i) => {
      const n = i + 1;
      const cid = owner[n];
      return h('div', { class: `cv-line${cid && String(cid) === String(selected) ? ' sel' : ''}${cid ? ' step' : ''}`, 'data-cid': cid || null },
        h('span', { class: 'cv-ln', 'aria-hidden': 'true' }, String(n)), h('code', {}, hl(l)));
    }));
  if (onHover) {
    pre.addEventListener('mouseover', e => { const l = e.target.closest('.cv-line'); onHover(l && l.dataset.cid ? l.dataset.cid : null); });
    pre.addEventListener('mouseleave', () => onHover(null));
  }
  if (selected !== null && lines[selected]) {
    requestAnimationFrame(() => {
      const first = pre.querySelector('.cv-line.sel');
      if (first) pre.scrollTop = Math.max(0, first.offsetTop - 60);
    });
  }
  return pre;
}
export function verifyBadge(v, available) {
  if (!available) return tipped(h('span', { class: 'cv-verify off' }, ic('circle-dashed'), 'Not verified'), 'DuckDB isn’t installed on this server, so the SQL wasn’t run. pip install duckdb', 'SQL check');
  if (!v) return h('span', { class: 'cv-verify wait' }, h('i', { class: 'cv-spin' }), 'Checking the SQL…');
  if (v.ok === true) {
    return tipped(h('span', { class: 'cv-verify ok' }, ic('badge-check'), `Verified · same ${fmt(v.cohort.sql)} samples`),
      `DuckDB ran this SQL on the frozen files in ${fmt(v.ms)} ms.\nEvery step's rows, samples and patients match the engine, and so do the final sample IDs.`, 'SQL matches the engine');
  }
  if (v.ok === null) return tipped(h('span', { class: 'cv-verify off' }, ic('circle-dashed'), 'Not run'), v.reason || '', 'SQL check');
  const bad = (v.steps || []).find(s => !s.match);
  return tipped(h('span', { class: 'cv-verify bad' }, ic('circle-alert'), 'Differs from the engine'),
    `${v.reason || ''}${bad ? `\nStep ${bad.index + 1}: engine ${fmt(bad.engine.samples)} samples, SQL ${bad.sql ? fmt(bad.sql.samples) : '—'}` : ''}\nThe engine's numbers are the ones the app uses.`, 'SQL check');
}

// ---------------------------------------------------------------- slide images
export const thumbUrl = barcode => `/api/canvas/thumbs/${encodeURIComponent(barcode)}.jpg`;
export function slideImg(item, { size = '' } = {}) {
  const frame = h('span', { class: `cv-slide ${size}${item.image ? '' : ' noimg'}` });
  if (item.image) {
    const img = h('img', { src: thumbUrl(item.slide_barcode), alt: `Slide ${item.slide_barcode} (${item.slide_kind})`, loading: 'lazy', decoding: 'async' });
    img.addEventListener('load', () => frame.classList.add('loaded'));
    img.addEventListener('error', () => { frame.classList.add('failed'); img.remove(); });
    frame.append(img);
  }
  frame.append(h('span', { class: 'cv-slide-glass', 'aria-hidden': 'true' }));
  return frame;
}
/** The grid of the cohort's slides (the caller draws the header and the kind filter). */
export function galleryPanel(g, { onXray }) {
  if (!g) return h('div', { class: 'cv-wait' }, h('i', { class: 'cv-spin' }), 'Finding the cohort’s slides…');
  if (!g.items.length) return h('p', { class: 'muted' }, 'No slide of these kinds belongs to the cohort.');
  const grid = h('div', { class: 'cv-gal' }, g.items.map(it => h('figure', { class: 'cv-gal-item' },
    h('a', { href: it.viewer_url || '#', target: '_blank', rel: 'noopener', class: 'cv-gal-img', title: 'Open the full slide in the IDC viewer' }, slideImg(it)),
    h('figcaption', {},
      h('span', { class: `cv-kind k-${it.short}` }, it.short),
      h('code', { title: it.slide_barcode }, it.sample_id.slice(5)),
      it.pam50 && it.pam50 !== NA ? h('span', { class: 'cv-gal-pam', style: `--c:${swatch(CHARS[0], it.pam50)}` }, groupLabel(it.pam50)) : null,
      h('button', { type: 'button', class: 'icon-btn tiny', title: 'X-ray this patient', 'aria-label': `X-ray patient ${it.patient_id}`, onclick: () => onXray(it.patient_id) }, ic('scan-search'))))));
  return h('div', {}, grid,
    h('p', { class: 'cv-credit' }, 'Slide thumbnails: NCI Imaging Data Commons (CC BY 3.0), TCGA Research Network. Click one to open the full slide.'));
}

// ---------------------------------------------------------------- one patient as a tree (the X-ray)
const LAYER_SHORT = [['rna', 'RNA'], ['protein', 'Prot'], ['mutation', 'Mut'], ['copy_number', 'CNV'], ['survival', 'Surv'], ['clinical', 'Clin']];
const TYPE_CLASS = { 'Primary Tumor': 'pt', 'Solid Tissue Normal': 'nt', Metastatic: 'mt' };
/** ctx: { step, stepIndex, inCohort: Set } — the step whose rows to explain (a join), and who is in the cohort. */
export async function openXray(dialog, patientId, ctx = {}) {
  dialog.replaceChildren(h('div', { class: 'cv-xray-shell' }, h('div', { class: 'cv-gal-wait' }, h('i', { class: 'cv-spin' }), `Opening ${patientId}…`)));
  if (!dialog.open) dialog.showModal();
  let p;
  try { p = await api(`/api/canvas/patients/${encodeURIComponent(patientId)}`); } catch (e) { dialog.close(); errorToast(e.message); return; }
  const st = ctx.step;
  const kinds = st && st.op === 'join' && st.table === 'slides' ? (st.slide_kinds || []) : null;
  const matches = sm => (kinds ? sm.slides.filter(x => !kinds.length || kinds.includes(x.slide_kind)) : []);
  const rowsFor = sm => (kinds ? (st.mode === 'exists' ? (matches(sm).length ? 1 : 0) : matches(sm).length) : null);
  const samples = p.samples.map(sm => {
    const rows = rowsFor(sm);
    const inC = ctx.inCohort ? ctx.inCohort.has(sm.sample_id) : null;
    return h('div', { class: `cv-x-sample ${TYPE_CLASS[sm.sample_type] || ''}${inC === false ? ' out' : ''}` },
      h('div', { class: 'cv-x-shead' },
        h('span', { class: 'cv-x-type' }, groupLabel(sm.sample_type)),
        h('code', {}, sm.sample_id),
        inC === null ? null : h('span', { class: `cv-x-in ${inC ? 'yes' : 'no'}` }, inC ? 'in the cohort' : 'not in the cohort')),
      h('div', { class: 'cv-x-facts' },
        h('span', { class: 'cv-x-layers' }, LAYER_SHORT.map(([k, l]) => h('i', { class: sm.layers[k] ? 'on' : '', title: `${l}: ${sm.layers[k] ? 'yes' : 'no'}` }, l))),
        ...[['PAM50', groupLabel(sm.pam50 || NA)], ['ER', groupLabel(sm.er || NA)], ['Stage', sm.stage && sm.stage !== NA ? groupLabel(sm.stage) : '—'], ['Age', groupLabel(sm.age || NA)]]
          .map(([k, v]) => h('span', { class: 'cv-x-fact' }, h('small', {}, k), v))),
      rows === null ? null : h('p', { class: `cv-x-rows${rows > 1 ? ' many' : ''}` },
        h('b', {}, rows > 1 ? `Becomes ${rows} rows` : rows === 1 ? 'Stays one row' : 'Dropped'),
        ` at step ${ctx.stepIndex + 1}, which ${st.mode === 'exists' ? 'keeps a sample once if it has a' : 'adds a row for every'} `
        + `${kinds.length ? kinds.map(k => k.split(' (')[0]).join(' or ') : 'pathology'} slide`
        + `${rows === 0 ? ' (this sample has none)' : ''}.`),
      h('div', { class: 'cv-x-slides' }, sm.slides.length ? sm.slides.map(sl => {
        const hit = kinds ? matches(sm).includes(sl) : false;
        return h('a', { class: `cv-x-slide${kinds ? (hit ? ' hit' : ' miss') : ''}`, href: sl.viewer_url || '#', target: '_blank', rel: 'noopener', title: `${sl.slide_barcode} · ${sl.slide_kind}. Open in the IDC viewer.` },
          slideImg({ ...sl, image: sl.image }, { size: 'sm' }), h('span', { class: `cv-kind k-${sl.short}` }, sl.short), h('code', {}, sl.slide_barcode.slice(-7)));
      }) : h('p', { class: 'muted' }, 'No slides for this sample.')));
  });
  const close = h('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Close', onclick: () => dialog.close() }, ic('x'));
  dialog.replaceChildren(h('div', { class: 'cv-xray-shell' },
    h('header', { class: 'cv-xray-head' },
      h('div', {}, h('p', { class: 'label' }, 'Patient X-ray'), h('h2', { id: 'cv-xray-title' }, h('code', {}, p.patient_id)),
        h('p', { class: 'muted' }, ...rich(`Submitting site ${p.site} · ${p.samples.length} sample${p.samples.length === 1 ? '' : 's'} · ${p.samples.reduce((a, x) => a + x.slides.length, 0)} slides. `
          + 'One patient, every row they can become: this is what a join multiplies.'))),
      close),
    h('div', { class: 'cv-x-tree' },
      h('div', { class: 'cv-x-root' }, h('i', { class: 'glyph g-patient' }), h('b', {}, 'Patient'), h('small', {}, p.patient_id)),
      h('div', { class: 'cv-x-branches' }, samples)),
    h('p', { class: 'cv-credit' }, 'Slides: NCI Imaging Data Commons (CC BY 3.0). Pink outline: a slide the selected join turns into a row.')));
  paintIcons();
  dialog.addEventListener('click', e => { if (e.target === dialog) dialog.close(); }, { once: true });
}
