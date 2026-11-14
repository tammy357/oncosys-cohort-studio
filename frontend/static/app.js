// Cohort Studio — Build page. Shared helpers live in common.js; three.js, GSAP and Lucide are optional.
import {
  $, $$, h, s, ic, paintIcons, store, session, api, fmt, clock, modeLabel, verdictLabel, timeAgo, rich,
  countTo, enter, toast, warnToast, errorToast, clearToasts, tipped, loadMeta, initChrome, showModelStatus, G, reduceMotion, did,
} from './common.js';
import { openWorkbench } from './workbench.js';
import { initTour } from './tour.js';

const state = { meta: null, aiAvailable: false, history: [], specimen: null, q: 0 };
const STATUS = { pass: 'PASS', warn: 'WARN', fail: 'FAIL', unknown: 'OPEN' };
const TAG_SWATCH = { 'Join check': 'haem', 'Asks first': 'eosin', Pairs: 'teal', Negation: 'amber', Outcomes: 'blue',
  Clinical: 'haem2', 'Per patient': 'teal', 'Honest gaps': 'grey' };
const LEGEND_PALETTE = {
  light: ['#3d348b', '#7b70d0', '#cf3f68', '#c98a1c', '#23806f', '#b3aa9b', '#2f6db3', '#18161d'],
  dark: ['#a49bff', '#cbc3ff', '#ff7ea3', '#f0b45e', '#4fc8ad', '#8a8494', '#7fb2f0', '#f0ebe2'],
};
const OPS = { sample_type: ['Sample', 'op-sample'], layer: ['Layer', 'op-layer'], exclude_layer: ['Exclude', 'op-exclude'],
  join: ['Join', 'op-join'], filter: ['Filter', 'op-filter'], any_of: ['Any of', 'op-filter'], mutation: ['Gene', 'op-mutation'],
  one_per_patient: ['Per patient', 'op-patient'], dedupe: ['Dedupe', 'op-sample'], where: ['Condition', 'op-filter'] };
const PAM_LABEL = { LumA: 'Luminal A', LumB: 'Luminal B', Her2: 'HER2-enriched', Basal: 'Basal-like', Normal: 'Normal-like' };
const LAYER_LABEL = { clinical: 'Clinical record', survival: 'Curated survival', mutation: 'Mutation calls (MC3)', copy_number: 'Copy number',
  rna: 'RNA expression', protein: 'Protein (RPPA)' };
const short = text => String(text).split(' (')[0];
// a step's label for a chip: drop the bracketed asides but keep the values ("ER status (clinical IHC) is one of Negative" -> "ER status is one of Negative").
// A join that keeps each sample once (what "Fix it" switches to) is tagged, so the chip shows the fix changed it.
const chipLabel = text => String(text).replace(/\s*\(keep each sample once if it has at least one match\)/, ' · each sample once')
  .replace(/\s*\([^)]*\)/g, '').replace(/^Keep samples where /, '').replace(/\s+/g, ' ').trim();
// The funnel's "colour by" characteristics. Categories keep a fixed order (and so a fixed colour) whatever the cohort;
// the colours are CSS variables (--fz-*) so each theme has its own validated steps. Ordinal ones use one hue, light to dark.
const FUNNEL_BY = [
  { key: 'PAM50 subtype', name: 'Subtype', css: 'pam', order: ['LumA', 'LumB', 'Basal', 'Her2', 'Normal'] },
  { key: 'ER status (IHC)', name: 'ER status', css: 'er', order: ['Positive', 'Negative'] },
  { key: 'Stage', name: 'Stage', css: 'stage', order: ['I', 'II', 'III', 'IV'] },
  { key: 'Age band', name: 'Age', css: 'age', order: ['under 50', '50-64', '65 and over'] },
  { key: 'Sample type', name: 'Sample type', css: 'type', order: ['Primary Tumor', 'Solid Tissue Normal', 'Metastatic'] },
];
const NA = '(not recorded)';
const GROUP_LABEL = { ...PAM_LABEL, Positive: 'ER-positive', Negative: 'ER-negative', I: 'Stage I', II: 'Stage II', III: 'Stage III', IV: 'Stage IV',
  'under 50': 'Under 50', '50-64': '50–64', '65 and over': '65 and over', 'Primary Tumor': 'Primary tumour', 'Solid Tissue Normal': 'Normal tissue',
  Metastatic: 'Metastatic', [NA]: 'Not recorded' };
const groupLabel = g => GROUP_LABEL[g] || g;
const pctTxt = (n, d) => (d ? `${Math.round((100 * n) / d)}%` : '–');

// ---------------------------------------------------------------- small builders
const richify = parts => parts.map(p => (typeof p === 'string' ? rich(p) : p));
const note = (kind, icon, title, ...body) => h('div', { class: `note ${kind}` }, ic(icon), h('div', {}, title ? h('b', {}, title, ' ') : null, ...richify(body)));
const sec = (title, ...kids) => h('section', { class: 'sec' }, h('h4', { class: 'sec-t' }, title), ...kids);
const details = (summary, ...kids) => h('details', { class: 'more' }, h('summary', {}, summary), ...kids);

function animatePanel(root) {
  paintIcons();
  $$('[data-count]', root).forEach(el => countTo(el, Number(el.dataset.count)));
  const g = G();
  if (!g) return;
  const funnel = $('.funnel:not([hidden])', root);
  if (funnel) animateFunnel(funnel);
  const flow = $('.flow:not([hidden])', root);
  if (flow) animateFlow(flow);
  const bars = $$('.o-bar i', root);
  if (bars.length) g.from(bars, { scaleX: 0, duration: 0.9, ease: 'power3.out', stagger: 0.06 });
  $$('.db', root).forEach((row, i) => {
    const span = $('.span', row);
    const after = $('.after', row);
    if (!span || !after) return;
    const x1 = span.getAttribute('x1');
    g.from(span, { attr: { x2: x1 }, duration: 0.9, delay: 0.2 + i * 0.08, ease: 'power3.out' });
    g.from(after, { attr: { cx: x1 }, duration: 0.9, delay: 0.2 + i * 0.08, ease: 'power3.out' });
  });
  const rows = $$('.protocol li, .checks tr, .read-as > *, .fixes li', root);
  if (rows.length) g.from(rows, { y: 6, autoAlpha: 0, duration: 0.4, stagger: 0.03, ease: 'power2.out', clearProps: 'transform,opacity,visibility' });
}
function animateFunnel(el) {
  const g = G();
  if (!g) return;
  const tl = g.timeline({ defaults: { ease: 'power3.out' } });
  tl.from($$('.f-label, .f-num', el), { autoAlpha: 0, x: i => (i % 2 ? 8 : -8), duration: 0.45, stagger: 0.04, clearProps: 'all' }, 0);
  tl.from($$('.fz-bar', el), { scaleX: 0, duration: 0.75, stagger: 0.08, clearProps: 'transform' }, 0.05);
  tl.from($$('.fz-rib', el), { autoAlpha: 0, duration: 0.6, stagger: 0.08, clearProps: 'opacity,visibility' }, 0.25);
  tl.from($$('.f-drop', el), { autoAlpha: 0, y: 4, duration: 0.45, stagger: 0.08, clearProps: 'all' }, 0.3);
  tl.from($$('.f-ghost', el), { autoAlpha: 0, scaleX: 0.7, duration: 0.6 }, '-=0.3');
}
function animateFlow(el) {
  const g = G();
  if (!g) return;
  const tl = g.timeline({ defaults: { ease: 'power3.out' } });
  $$(':scope > *', el).forEach(child => {
    if (child.classList.contains('flow-stem')) tl.fromTo(child, { '--grow': 0 }, { '--grow': 1, duration: 0.22 }, '>-0.05');
    else tl.from(child, { y: 6, autoAlpha: 0, duration: 0.32, clearProps: 'transform,opacity,visibility' }, '<0.05');
  });
}

// ---------------------------------------------------------------- hero
function renderFigures() {
  const r = state.meta.release;
  const items = [[r.samples, 'Samples'], [r.patients, 'Patients'], [r.slides, 'Pathology slides'], [r.genes, 'Genes with calls']];
  $('#figures').replaceChildren(...items.map(([n, label]) => h('div', {}, h('dd', { 'data-count': String(n) }, G() ? '0' : fmt(n)), h('dt', {}, label))));
}
function renderLegend() {
  const sp = state.meta.specimen;
  const pal = LEGEND_PALETTE[document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'];
  $('#legend').replaceChildren(
    h('figcaption', {}, h('b', {}, 'Fig. 1  '), `Each dot is one of the release's ${fmt(sp.samples.length)} samples, grouped by `,
      ...rich('PAM50 subtype. The layout is illustrative. Point at a group to isolate it.')),
    h('ul', {}, sp.groups.map((g, i) => h('li', {
      onmouseenter: () => state.specimen && state.specimen.focusGroup(i), onmouseleave: () => state.specimen && state.specimen.focusGroup(null) },
    h('i', { style: `background:${pal[i]}` }), h('span', {}, g.label), h('span', {}, fmt(g.count))))));
}
function intro() {
  const g = G();
  const root = document.documentElement;
  if (!g) { root.classList.remove('preload'); $$('[data-count]', $('#figures')).forEach(el => { el.textContent = fmt(Number(el.dataset.count)); }); return; }
  g.set(['.eyebrow', '.lead', '.search', '.search-meta', '.figures', '.legend'], { autoAlpha: 0, y: 16 });
  g.set('.headline .line > span', { yPercent: 115 });
  root.classList.remove('preload');
  g.timeline({ defaults: { ease: 'power3.out' } })
    .to('.eyebrow', { autoAlpha: 1, y: 0, duration: 0.6 }, 0.1)
    .to('.headline .line > span', { yPercent: 0, duration: 1.1, stagger: 0.13, ease: 'expo.out' }, 0.2)
    .to('.lead', { autoAlpha: 1, y: 0, duration: 0.8 }, 0.65)
    .to('.search', { autoAlpha: 1, y: 0, duration: 0.8 }, 0.78)
    .to('.search-meta', { autoAlpha: 1, y: 0, duration: 0.7 }, 0.9)
    .to('.figures', { autoAlpha: 1, y: 0, duration: 0.8, onStart: () => $$('[data-count]', $('#figures')).forEach(el => countTo(el, Number(el.dataset.count), 1.6)) }, 1.0)
    .to('.legend', { autoAlpha: 1, y: 0, duration: 0.8 }, 1.15);
}
function scrollReveals() {
  const g = G();
  if (!g || !window.ScrollTrigger) return;
  g.registerPlugin(window.ScrollTrigger);
  $$('.method-col, .block-head').forEach(el => g.from(el, { y: 22, autoAlpha: 0, duration: 0.9, ease: 'power3.out',
    clearProps: 'transform,opacity,visibility', scrollTrigger: { trigger: el, start: 'top 90%', once: true } }));
}

// ---------------------------------------------------------------- ready-made cohorts
function curveSvg(curve) {
  const W = 240; const H = 58; const pad = 4;
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, class: 'curve', 'aria-hidden': 'true' });
  if (!curve || curve.length < 2) { svg.append(s('line', { x1: pad, x2: W - pad, y1: H / 2, y2: H / 2, class: 'base' })); return svg; }
  const max = curve[0] || 1;
  const x = i => pad + ((W - 2 * pad) * i) / (curve.length - 1);
  const y = v => pad + (H - 2 * pad) * (1 - v / max);
  let d = `M ${x(0)} ${y(curve[0])}`;
  for (let i = 1; i < curve.length; i++) d += ` H ${x(i)} V ${y(curve[i])}`;
  svg.append(s('line', { x1: pad, x2: W - pad, y1: y(max), y2: y(max), class: 'base' }), s('path', { d: `${d} V ${H} H ${x(0)} Z`, class: 'area' }), s('path', { d, class: 'line' }));
  curve.forEach((v, i) => svg.append(s('circle', { cx: x(i), cy: y(v), r: i === curve.length - 1 ? 3.4 : 2.2, class: i === curve.length - 1 ? 'end' : 'dot' })));
  return svg;
}
function renderSpecimens() {
  const host = $('#specimens');
  host.replaceChildren(...state.meta.presets.map((p, i) => h('button', { type: 'button', class: 'spec', title: p.query,
    onclick: () => { $('#q').value = p.query; startSearch(p.query); } },
  h('div', { class: 'spec-top' }, h('span', { class: `swatch s-${TAG_SWATCH[p.tag] || 'grey'}` }), p.tag, h('span', { class: 'idx' }, String(i + 1).padStart(2, '0'))),
  h('h3', { class: 'spec-title' }, p.label),
  h('p', { class: 'spec-desc' }, rich(p.description)),
  curveSvg(p.curve),
  h('div', { class: 'spec-foot' }, p.curve ? `${fmt(p.curve[0])} → ${fmt(p.final)} samples` : 'Not in this release', ic('arrow-up-right')))));
  paintIcons();
  const g = G();
  if (g && window.ScrollTrigger) {
    g.from($$('.spec', host), { y: 24, autoAlpha: 0, duration: 0.8, stagger: 0.07, ease: 'power3.out', clearProps: 'transform,opacity,visibility',
      scrollTrigger: { trigger: host, start: 'top 88%', once: true } });
    $$('.curve .line', host).forEach(path => {
      const len = path.getTotalLength ? path.getTotalLength() : 0;
      if (len) g.fromTo(path, { strokeDasharray: len, strokeDashoffset: len }, { strokeDashoffset: 0, duration: 1.4, ease: 'power2.inOut', scrollTrigger: { trigger: path, start: 'top 95%', once: true } });
    });
  }
}

// ---------------------------------------------------------------- notebook
const thread = $('#thread');
function startNotebook() {
  if (document.body.classList.contains('chatting')) return;
  document.body.classList.add('chatting');
  syncField();
  $('#notebook').hidden = false;
  $('#session-date').textContent = new Date().toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long' });
  if (window.ScrollTrigger) window.ScrollTrigger.refresh();
}
function addQuery(text) {
  startNotebook();
  state.q += 1;
  const el = h('article', { class: 'entry q' }, h('div', { class: 'entry-meta' }, h('b', {}, `Q${String(state.q).padStart(2, '0')}`), clock()), h('p', { class: 'q-text' }, `“${text}”`));
  thread.append(el);
  enter(el);
  el.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' });
  return el;
}
function addPanel(content) {
  const el = h('article', { class: 'entry panel' }, content);
  thread.append(el);
  enter(el, 0.05);
  animatePanel(el);
  return el;
}
function setPanel(panel, content) { panel.replaceChildren(content); animatePanel(panel); }
function working(lines) {
  const label = h('span', {}, lines[0]);
  const el = h('div', { class: 'working' }, h('div', { class: 'working-line' }, h('span', { class: 'w-k' }, 'Working'), label),
    h('div', { class: 'progress' }), h('div', { class: 'ghost-line', style: 'width:72%' }), h('div', { class: 'ghost-line', style: 'width:46%' }));
  let i = 0;
  const timer = setInterval(() => { i = (i + 1) % lines.length; label.textContent = lines[i]; }, 1500);
  if (state.specimen) state.specimen.busy(true);
  el.stop = () => { clearInterval(timer); if (state.specimen) state.specimen.busy(false); };
  return el;
}

async function startSearch(query) {
  query = (query || '').trim();
  if (!query) return;
  closeSuggest();
  addQuery(query);
  const useAi = $('#use-ai').checked && state.aiAvailable;
  const w = working(useAi
    ? ["Reading this release's fields and values", 'Drafting the protocol', 'Checking every value against the data', 'Counting what each choice would give']
    : ['Reading the request with the built-in parser']);
  const panel = addPanel(w);
  try {
    const d = await api('/api/draft', { method: 'POST', body: { query, use_ai: useAi } });
    w.stop();
    setPanel(panel, renderDraft(d));
  } catch (e) {
    w.stop();
    setPanel(panel, h('div', {}, note('bad', 'circle-alert', useAi ? "The assistant couldn't draft this." : "Couldn't draft this.", e.message),
      h('div', { class: 'run-row' },
        useAi ? h('button', { class: 'btn small', onclick: () => { $('#use-ai').checked = false; startSearch(query); } }, 'Try the built-in parser') : null,
        h('button', { class: 'btn small', onclick: openAdvanced }, 'Use advanced search'))));
  }
}

// ---------------------------------------------------------------- drafts and questions
/** A small counted fact whose details live in a tooltip ("3 small fixes"). */
const fact = (icon, text, tip, title) => tipped(h('span', { class: 'fact', tabindex: '0' }, ic(icon), text), tip, title);
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

function renderDraft(d) {
  const u = d.usage || {};
  const box = h('div');
  const model = d.mode === 'ai'
    ? tipped(h('span', { class: 'chip', tabindex: '0' }, `AI · ${d.model || ''}`, d.cached ? ' · cached' : ''),
      d.cached ? 'Same request as before: served from the cache, no model call and no cost.'
        : u.input ? `${fmt(u.input)} tokens in${u.cached_input ? ` (${fmt(u.cached_input)} cached)` : ''}, ${fmt(u.output)} out.` : '', 'Model')
    : h('span', { class: 'chip' }, modeLabel(d.mode));
  box.append(h('div', { class: 'kicker' }, h('span', { class: 'k-main' }, 'Draft'), model, d.refines ? h('span', { class: 'chip' }, 'edit') : null));
  if (d.interpretation) box.append(h('p', { class: 'reading' }, rich(d.interpretation)));
  if (d.mappings && d.mappings.length) {
    box.append(h('div', { class: 'read-as' }, tipped(h('span', { class: 'label', tabindex: '0' }, 'Read as'), 'Point at a phrase to see how it was read.'),
      d.mappings.map(m => tipped(h('span', { class: 'phrase', tabindex: '0' }, m.phrase), m.meaning, `“${m.phrase}”`))));
  }
  // small facts, details on hover
  const facts = [];
  if (d.corrections && d.corrections.length) {
    facts.push(fact('check-check', plural(d.corrections.length, 'small fix', 'small fixes'),
      d.corrections.map(c => `${c.from} → ${c.to}: ${c.why}`).join('\n'), 'Checked against the data'));
  }
  if (d.auto_resolved && d.auto_resolved.length) {
    facts.push(fact('circle-check', plural(d.auto_resolved.length, 'choice settled for you', 'choices settled for you'),
      d.auto_resolved.map(a => `${a.question} ${a.reason}`).join('\n'), 'Settled without asking'));
  }
  if (d.assumptions && d.assumptions.length) {
    facts.push(fact('lightbulb', plural(d.assumptions.length, 'assumption', 'assumptions'), d.assumptions.join('\n'), 'Assumptions'));
  }
  if (facts.length) box.append(h('div', { class: 'facts' }, facts));
  // problems: one line each (details on hover), and a toast
  const alerts = h('div', { class: 'alerts' });
  if (d.mode === 'rules') {
    alerts.append(alertBar('warn', 'triangle-alert', 'Built-in parser, no AI: check every step.',
      'It matches keywords, so it can miss words such as “not”, “or” and “without”.'));
  }
  for (const x of d.unsupported || []) {
    alerts.append(alertBar(d.status === 'cannot_answer' ? 'bad' : 'warn', 'circle-off', `Not in this release: “${x.phrase}”.`,
      `${x.reason || ''}${x.alternative ? `\nClosest available: ${x.alternative}.` : ''}`));
  }
  for (const w of d.warnings || []) alerts.append(alertBar('warn', 'triangle-alert', w));
  if (d.status === 'cannot_answer') {
    alerts.append(alertBar('bad', 'circle-off', "This release can't answer the main part of the request.",
      "You can still run what's left, rephrase it, or use advanced search."));
  }
  if (alerts.childElementCount) box.append(alerts);
  if ((d.unsupported || []).length) warnToast(`Not in this release: ${d.unsupported.map(x => x.phrase).join(', ')}`);
  else if ((d.warnings || []).length) warnToast('The draft has something to check before you run it.');
  const host = h('div');
  if (d.clarifications && d.clarifications.length) box.append(renderQuestions(d, host));
  else if (d.spec) host.append(protocol(d.spec, d.spec_labels, { draft: d, mode: d.mode, query: d.typed || d.request }));
  box.append(host);
  return box;
}

function renderQuestions(d, host) {
  const answers = {};
  const n = d.clarifications.length;
  const wrap = sec(n === 1 ? 'A choice to make' : `${n} choices to make`);
  d.clarifications.forEach((q, qi) => {
    const max = Math.max(1, ...q.options.map(o => o.n_samples || 0));
    const buttons = q.options.map((o, i) => {
      const b = h('button', { type: 'button', class: 'option', 'aria-pressed': 'false', onclick: () => choose(q, i, b) },
        i === (q.recommended ?? 0) ? h('span', { class: 'o-tag' }, 'Standard') : null,
        h('span', { class: 'o-label' }, o.label),
        h('span', { class: 'o-n' }, o.n_samples === null || o.n_samples === undefined ? '—' : fmt(o.n_samples),
          h('small', {}, o.n_samples === null || o.n_samples === undefined ? "can't run" : `samples · ${fmt(o.n_patients)} patients`)),
        h('span', { class: 'o-bar' }, h('i', { style: `width:${(100 * (o.n_samples || 0)) / max}%` })),
        o.labels && o.labels.length ? h('span', { class: 'o-steps' }, rich(o.labels.join(' · '))) : null);
      return b;
    });
    const other = h('input', { placeholder: 'Something else? Say it in your own words', 'aria-label': `Other answer to: ${q.question}` });
    const ask = () => { if (other.value.trim()) startSearch(`${d.typed || d.request}. ${q.question} ${other.value.trim()}`); };
    other.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); ask(); } });
    wrap.append(h('div', { class: 'question' },
      h('div', { class: 'q-head' }, h('span', { class: 'q-n' }, `Q${qi + 1}`), h('h3', { class: 'q-title' }, rich(q.question))),
      q.why ? h('p', { class: 'q-why' }, rich(q.why)) : null,
      h('div', { class: 'options', role: 'group', 'aria-label': q.question }, buttons),
      h('div', { class: 'other' }, other, h('button', { type: 'button', class: 'btn small', onclick: ask }, 'Ask again'))));
  });
  async function choose(q, i, btn) {
    answers[q.id] = i;
    btn.parentElement.querySelectorAll('.option').forEach(o => o.setAttribute('aria-pressed', String(o === btn)));
    if (Object.keys(answers).length < n) return;
    const w = working(['Applying your answers']);
    host.replaceChildren(w);
    try {
      const r = await api(`/api/drafts/${d.draft_id}/resolve`, { method: 'POST', body: { answers } });
      w.stop();
      host.replaceChildren(protocol(r.spec, r.spec_labels, { draft: d, mode: r.mode, query: d.typed || d.request, answered: true }));
      animatePanel(host);
      did('clarify');
    } catch (e) {
      w.stop();
      host.replaceChildren(note('bad', 'circle-alert', "Couldn't apply the answers.", e.message));
      paintIcons();
    }
  }
  return wrap;
}

// ---------------------------------------------------------------- protocol (the draft's steps, before it runs)
function protocol(spec, labels, ctx) {
  const p = ctx.draft && ctx.draft.preview;
  const known = p && p.n_samples !== null && p.n_samples !== undefined && !ctx.answered;
  const list = h('ol', { class: 'protocol' }, spec.steps.map((st, i) => {
    const [opLabel, opClass] = OPS[st.op] || [st.op, 'op-sample'];
    return h('li', { class: 'p-click', title: 'Click to change this step', onclick: () => edit(i) }, h('span', { class: 'p-n' }, `S${i + 1}`),
      h('span', { class: `p-op ${opClass}` }, opLabel), h('div', { class: 'p-text' }, labels[i] ? rich(labels[i]) : JSON.stringify(st)),
      h('span', { class: 'p-go' }, ic('pen-line')));
  }));
  const preview = h('span', { class: 'preview' }, known
    ? ['Preview ', h('b', {}, fmt(p.n_samples)), ' samples · ', h('b', {}, fmt(p.n_patients)), ' patients']
    : 'Run to see where every sample goes');
  const edit = focus => openWorkbench({ meta: state.meta, ai: state.aiAvailable, query: ctx.query, spec, focus,
    draft: { draftId: ctx.draft && ctx.draft.draft_id, mode: ctx.mode }, nSamples: known ? p.n_samples : null, onResult: edited });
  const runBtn = h('button', { type: 'button', class: 'btn ink', onclick: run }, ic('play'), 'Run protocol');
  async function run() {
    runBtn.disabled = true;
    const w = working(['Running every step on the frozen files', 'Checking each join for double counting', 'Measuring who each step removed']);
    const panel = addPanel(w);
    try {
      const res = await api('/api/run', { method: 'POST', body: { spec, query: ctx.query, mode: ctx.mode || 'ai', draft_id: ctx.draft && ctx.draft.draft_id } });
      w.stop();
      showResult(panel, res);
      refreshHistory();
    } catch (e) {
      w.stop();
      setPanel(panel, note('bad', 'circle-alert', "The protocol didn't run.", e.message));
    } finally { runBtn.disabled = false; }
  }
  return sec('Protocol', list, h('div', { class: 'run-row' }, runBtn,
    h('button', { type: 'button', class: 'btn', onclick: () => edit() }, ic('sliders-horizontal'), 'Edit steps'), preview));
}

// ---------------------------------------------------------------- the editor (workbench) and what happens after it runs
function edited(res, why) {
  addQuery(`Edited · ${why || res.query}`);
  const panel = addPanel(h('div'));
  showResult(panel, res);
  refreshHistory();
}
function openEditor(res, opts = {}) {
  openWorkbench({ meta: state.meta, ai: state.aiAvailable, searchId: res.search_id, query: res.query, spec: res.spec,
    versions: res.versions || [], nSamples: res.final.samples, onResult: edited, ...opts });
}

// ---------------------------------------------------------------- results: flow diagram, funnel, the rest
const byKey = key => FUNNEL_BY.find(c => c.key === key) || FUNNEL_BY[0];
/** Ordered [group, count] pairs for one characteristic (fixed order first, then anything else, "not recorded" last). */
function ordered(counts, ch) {
  const keys = Object.keys(counts || {});
  const extra = keys.filter(k => !ch.order.includes(k) && k !== NA).sort();
  return [...ch.order, ...extra, NA].filter(k => counts && counts[k]).map(k => [k, counts[k]]);
}
const swatchVar = (ch, g) => (g === NA ? 'var(--fz-na)' : ch.order.includes(g) ? `var(--fz-${ch.css}-${ch.order.indexOf(g)})` : 'var(--fz-other)');
/** A thin one-whole bar (who was removed / who is in). */
function miniBar(counts, ch, title) {
  const pairs = ordered(counts, ch);
  const total = pairs.reduce((a, [, n]) => a + n, 0);
  return h('span', { class: 'mini-bar', role: 'img', 'aria-label': `${title}: ${pairs.map(([g, n]) => `${groupLabel(g)} ${pctTxt(n, total)}`).join(', ')}` },
    pairs.map(([g, n]) => h('i', { style: `flex:${n} 1 0;background:${swatchVar(ch, g)}`, title: `${groupLabel(g)} · ${fmt(n)} (${pctTxt(n, total)})` })));
}

function funnel(res) {
  const comp = res.composition || {};
  const chars = FUNNEL_BY.filter(c => comp[c.key]);
  let ch = byKey(store.get('cs-funnel-by', 'PAM50 subtype'));
  if (!comp[ch.key]) ch = chars[0] || ch;
  const wrap = h('div', { class: 'funnel', role: 'group', 'aria-label': 'Funnel: samples left after each step, split by a characteristic' });
  const head = h('div', { class: 'fz-head' });
  const grid = h('div', { class: 'fz-grid' });
  wrap.append(head, grid);

  const start = res.start.samples || 1;
  const W = n => Math.max(1.2, Math.min(100, (100 * n) / start));
  // one entry per bar; its counts are looked up for whichever characteristic is chosen
  const rows = [{ label: 'All samples in the release', n: res.start.samples, at: 'start', kind: 'start' }];
  res.steps.forEach((st, i) => rows.push({ label: st.label, n: st.skipped ? rows[rows.length - 1].n : st.samples, at: i, st, kind: st.error ? 'error' : '' }));
  rows.push({ label: 'Cohort', n: res.final.samples, at: 'final', kind: 'end', patients: res.final.patients });
  const countsOf = row => {
    const c = comp[ch.key] || {};
    return (row.at === 'start' ? c.start : row.at === 'final' ? c.final : (c.steps || [])[row.at]) || {};
  };

  function segs(row) {
    const pairs = ordered(countsOf(row), ch);
    const total = pairs.reduce((a, [, n]) => a + n, 0) || 1;
    let cum = 0;
    return pairs.map(([g, n]) => { const x0 = cum / total; cum += n; return { g, n, share: n / total, x0, x1: cum / total }; });
  }
  function draw() {
    head.replaceChildren(
      h('div', { class: 'fz-by' }, h('span', { class: 'label' }, 'Colour by'),
        h('span', { class: 'seg', role: 'group', 'aria-label': 'Colour the funnel by' }, chars.map(c => h('button', { type: 'button', 'aria-pressed': String(c === ch),
          onclick: () => { ch = c; store.set('cs-funnel-by', c.key); rebuild(true); } }, c.name)))),
      h('ul', { class: 'fz-legend' }, ordered((comp[ch.key] || {}).start, ch).map(([g]) => h('li', {},
        h('button', { type: 'button', 'data-g': g, onmouseenter: () => focus(g), onmouseleave: () => focus(null), onfocus: () => focus(g), onblur: () => focus(null) },
          h('i', { style: `background:${swatchVar(ch, g)}`, class: g === NA ? 'na' : '' }), groupLabel(g))))));
    const parts = [];
    let prevSegs = null;
    let prevW = null;
    rows.forEach((row, i) => {
      const w = W(row.n);
      const ss = segs(row);
      if (i > 0) {
        // ribbons: each group's slice of the previous bar flows into its slice of this one
        const svg = s('svg', { viewBox: '0 0 100 30', preserveAspectRatio: 'none', class: 'fz-rib', 'aria-hidden': 'true' });
        const L0 = 50 - prevW / 2;
        const L1 = 50 - w / 2;
        for (const a of prevSegs) {
          const b = ss.find(x => x.g === a.g);
          const bx0 = b ? L1 + b.x0 * w : L1 + a.x0 * w;
          const bx1 = b ? L1 + b.x1 * w : bx0;
          svg.append(s('path', { 'data-g': a.g, style: `fill:${a.g === NA ? 'var(--fz-na-solid)' : swatchVar(ch, a.g)}`,
            d: `M${L0 + a.x0 * prevW},0 C${L0 + a.x0 * prevW},15 ${bx0},15 ${bx0},30 L${bx1},30 C${bx1},15 ${L0 + a.x1 * prevW},15 ${L0 + a.x1 * prevW},0 Z` }));
        }
        const st = row.st;
        const prevCounts = countsOf(rows[i - 1]);
        const curCounts = countsOf(row);
        const lost = {};
        for (const [g, n] of Object.entries(prevCounts)) { const d = n - (curCounts[g] || 0); if (d > 0) lost[g] = d; }
        const lostN = Object.values(lost).reduce((a, n) => a + n, 0);
        const drop = h('div', { class: 'f-drop' });
        if (st && st.removed) {
          drop.append(h('div', {}, h('b', {}, `−${fmt(st.removed)}`), ' ', h('span', {}, rich(st.removed_reason))));
          if (lostN) drop.append(miniBar(lost, ch, 'Who this step removed'));
        } else if (row.kind === 'end') drop.append(h('span', {}, 'final cohort'));
        else drop.append(h('span', {}, st && st.skipped ? 'not run' : 'none removed'));
        parts.push(h('div', { class: 'f-gap' }), svg, drop);
      }
      const bar = h('div', { class: `fz-bar ${row.kind}`, style: `--w:${w}%` }, ss.map(x => h('i', { class: `fz-seg${x.g === NA ? ' na' : ''}`, 'data-g': x.g,
        style: `flex:${x.n} 1 0;background:${swatchVar(ch, x.g)}`, title: `${groupLabel(x.g)} · ${fmt(x.n)} samples (${Math.round(100 * x.share)}%)`,
        onmouseenter: () => focus(x.g), onmouseleave: () => focus(null) })));
      const st = row.st;
      parts.push(h('div', { class: `f-label${row.kind === 'end' ? ' end' : ''}` }, row.kind === 'end' ? h('b', {}, 'Cohort') : rich(row.label)),
        h('div', { class: 'f-track' }, st && st.inflated ? h('div', { class: 'f-ghost', style: `--w:${W(st.rows)}%`, title: `${fmt(st.rows)} rows` }) : null, bar),
        h('div', { class: 'f-num' }, st && st.skipped ? h('span', { class: 'muted' }, 'not run') : [h('b', {}, fmt(row.n)), ' samples',
          h('small', { class: 'fz-share', 'data-row': String(i) },
            row.kind === 'end' ? `${fmt(row.patients)} patients` : st ? `${fmt(st.patients)} patients${st.rows !== st.samples ? ` · ${fmt(st.rows)} rows` : ''}` : `${fmt(state.meta.release.patients)} patients`),
          st && st.inflated ? h('span', { class: 'f-flag' }, `${fmt(st.rows - st.samples)} rows counted twice`) : null]));
      prevSegs = ss;
      prevW = w;
    });
    grid.replaceChildren(...parts);
    wrap.rowsData = rows.map(r => segs(r));
  }
  function focus(g) {
    if (g === null) { delete wrap.dataset.focus; } else wrap.dataset.focus = g;
    $$('[data-g]', wrap).forEach(el => el.classList.toggle('on', g !== null && el.dataset.g === g));
    // the share of the focused group in every bar, written next to the count
    $$('.fz-share', grid).forEach(el => {
      if (!el.dataset.base) el.dataset.base = el.textContent;
      const seg = (wrap.rowsData[Number(el.dataset.row)] || []).find(x => x.g === g);
      el.textContent = g === null ? el.dataset.base : `${groupLabel(g)}: ${seg ? `${fmt(seg.n)} · ${Math.round(100 * seg.share)}%` : 'none'}`;
      el.classList.toggle('hot', g !== null);
    });
  }
  function rebuild(animate) {
    draw();
    paintIcons();
    const gs = G();
    if (animate && gs) {
      gs.from($$('.fz-seg', grid), { autoAlpha: 0, duration: 0.35, stagger: { each: 0.004, from: 'start' }, ease: 'power2.out', clearProps: 'opacity,visibility' });
      gs.from($$('.fz-rib', grid), { autoAlpha: 0, duration: 0.5, ease: 'power2.out', clearProps: 'opacity,visibility' });
    }
  }
  draw();
  return wrap;
}

function flowDiagram(res, onEdit) {
  const wrap = h('div', { class: 'flow' });
  const chars = FUNNEL_BY.filter(c => ['PAM50 subtype', 'ER status (IHC)', 'Sample type'].includes(c.key));
  const why = st => {
    const box = h('div', { class: 'flow-why' });
    for (const ch of chars) {
      const counts = (st.removed_by || {})[ch.key];
      if (!counts || !Object.keys(counts).length) continue;
      const pairs = ordered(counts, ch);
      const total = pairs.reduce((a, [, n]) => a + n, 0);
      box.append(h('div', { class: 'fw-row' }, h('span', { class: 'fw-k' }, ch.name), miniBar(counts, ch, `Removed by ${ch.name}`),
        h('span', { class: 'fw-v' }, [...pairs].sort((x, y) => y[1] - x[1]).slice(0, 3).map(([g, n]) => `${groupLabel(g)} ${pctTxt(n, total)}`).join(' · '))));
    }
    if ((st.removed_examples || []).length) box.append(h('div', { class: 'fw-ex' }, 'e.g. ', st.removed_examples.slice(0, 4).map(x => h('code', {}, x))));
    return box;
  };
  wrap.append(h('div', { class: 'flow-box flow-main start' }, 'All samples in the release', h('span', { class: 'fb-n' }, `n = ${fmt(res.start.samples)}`)));
  for (const st of res.steps) {
    wrap.append(h('div', { class: 'flow-stem' }, st.removed ? h('span', { class: 'branch' }) : null));
    let excl = null;
    if (st.removed) {
      const detail = why(st);
      detail.hidden = true;
      excl = h('button', { type: 'button', class: 'flow-box flow-excl', 'aria-expanded': 'false', title: 'Who was excluded',
        onclick: e => { const open = detail.hidden; detail.hidden = !open; e.currentTarget.setAttribute('aria-expanded', String(open));
          const gs = G(); if (open && gs) gs.from(detail, { height: 0, autoAlpha: 0, duration: 0.3, ease: 'power2.out', clearProps: 'all' }); } },
      h('span', { class: 'fb-n' }, `Excluded n = ${fmt(st.removed)}`, h('span', { class: 'fb-more' }, ic('chevron-down'))), h('span', {}, rich(st.removed_reason)), detail);
    }
    wrap.append(h('div', { class: 'flow-side' }, excl));
    wrap.append(h('div', { class: `flow-box flow-main${st.inflated ? ' inflated' : ''}${st.error ? ' error' : ''}` }, rich(st.label),
      h('span', { class: 'fb-n' }, st.skipped ? 'not run' : `n = ${fmt(st.samples)}`,
        st.skipped ? null : h('small', {}, ` · ${fmt(st.patients)} patients${st.rows !== st.samples ? ` · ${fmt(st.rows)} rows` : ''}`)),
      st.inflated ? h('span', { class: 'fb-badge' }, `${fmt(st.rows - st.samples)} rows counted twice`) : null,
      st.error ? h('div', { class: 'muted' }, st.error) : null,
      onEdit ? h('button', { type: 'button', class: 'fb-edit', title: 'Change this step', 'aria-label': `Change step ${st.index + 1}`, onclick: () => onEdit(st.index) }, ic('pen-line')) : null));
  }
  wrap.append(h('div', { class: 'flow-stem' }), h('div', { class: 'flow-side' }),
    h('div', { class: 'flow-box flow-main end' }, 'Cohort', h('span', { class: 'fb-n' }, `n = ${fmt(res.final.samples)}`, h('small', {}, ` · ${fmt(res.final.patients)} patients`))));
  return wrap;
}

function attrition(res, onEdit) {
  const flow = flowDiagram(res, onEdit);
  const f = funnel(res);
  f.hidden = true;
  const bD = h('button', { type: 'button', 'aria-pressed': 'true' }, 'Flow diagram');
  const bF = h('button', { type: 'button', 'aria-pressed': 'false' }, 'Funnel');
  const show = which => {
    bF.setAttribute('aria-pressed', String(which === 'funnel'));
    bD.setAttribute('aria-pressed', String(which === 'flow'));
    f.hidden = which !== 'funnel';
    flow.hidden = which !== 'flow';
    paintIcons();
    if (which === 'funnel') animateFunnel(f); else animateFlow(flow);
  };
  bF.addEventListener('click', () => show('funnel'));
  bD.addEventListener('click', () => show('flow'));
  return h('section', { class: 'sec' }, h('h4', { class: 'sec-t' }, 'Where every sample went', h('span', { class: 'seg', role: 'group', 'aria-label': 'View' }, bD, bF)), flow, f);
}

// ---------------------------------------------------------------- the result: the number first, details on demand
const FILE_INFO = {
  'cohort_samples.csv': 'One row per sample in the cohort. This is the file to hand on.',
  'reproduce_cohort.py': 'A standalone script that rebuilds this cohort exactly from the frozen files.',
  'cohort_spec.json': 'The steps as JSON: rerun or edit them later.',
  'findings.json': 'Every check and finding, in the event’s findings format.',
  'verdict.json': 'The verdict and every check behind it.',
  'ledger.csv': 'Rows, samples and patients after every step.',
  'exclusions.csv': 'Every excluded sample, with the step and the reason.',
  'sample_selection.csv': 'For “one sample per patient”: which sample each patient kept, and why.',
  'coverage.csv': 'Every clinical and survival variable, with the share of cohort samples that have a value.',
};
const CHECK_SHORT = {
  'Every step runs on this release': 'Steps run', "Files match the event's frozen checksums": 'Frozen files',
  'The whole request was turned into steps': 'Whole request', 'No sample is counted twice': 'No double counting',
  'One sample per patient': 'Per patient', 'Sample collection days make sense': 'Collection days',
  'Enough patients to work with': 'Enough patients', 'The cohort still looks like the population it started from': 'Representative',
  'Filter fields are recorded for most samples': 'Fields recorded', 'Slides are of one kind': 'Slide kinds',
  'Survival endpoint suits breast cancer': 'Survival endpoint',
};
const CHECK_ICON = { pass: 'check', warn: 'triangle-alert', fail: 'x', unknown: 'circle-help' };
const CHECK_ORDER = { fail: 0, warn: 1, unknown: 2, pass: 3 };

/** A one-line alert. The long explanation lives in the tooltip (hover or focus the text). */
function alertBar(kind, icon, text, tip, ...actions) {
  const t = h('span', { class: 'alert-t', tabindex: tip ? '0' : null }, rich(text), tip ? ic('info', 'alert-i') : null);
  if (tip) tipped(t, tip);
  return h('div', { class: `alert ${kind}`, role: kind === 'bad' ? 'alert' : null }, ic(icon), t, actions.length ? h('span', { class: 'alert-actions' }, actions) : null);
}

function checkPills(v) {
  const checks = [...v.checks].sort((a, b) => (CHECK_ORDER[a.status] ?? 9) - (CHECK_ORDER[b.status] ?? 9));
  return h('div', { class: 'checks-strip', role: 'list', 'aria-label': 'Checks behind the verdict' }, checks.map(c => tipped(
    h('span', { class: `ck ${c.status}`, role: 'listitem', tabindex: '0' }, ic(CHECK_ICON[c.status] || 'circle'), CHECK_SHORT[c.name] || c.name),
    `${c.detail}${c.review ? `\nTo check: ${c.review}` : ''}`, `${STATUS[c.status] || c.status} · ${c.name}`)));
}

function dumbbell(title, subtitle, item) {
  const W = 300;
  const hi = Math.max(item.before_pct, item.after_pct, 1) * 1.2;
  const x = v => 6 + ((W - 12) * v) / hi;
  const svg = s('svg', { viewBox: `0 0 ${W} 18`, 'aria-hidden': 'true' });
  svg.append(s('line', { x1: 6, x2: W - 6, y1: 9, y2: 9, class: 'track' }), s('line', { x1: x(item.before_pct), x2: x(item.after_pct), y1: 9, y2: 9, class: 'span' }),
    s('circle', { cx: x(item.before_pct), cy: 9, r: 4.5, class: 'before' }), s('circle', { cx: x(item.after_pct), cy: 9, r: 4.5, class: 'after' }));
  const row = h('div', { class: 'db', tabindex: '0' }, h('div', { class: 'db-label' }, h('b', {}, rich(title)), h('span', {}, subtitle)), svg,
    h('span', { class: 'db-v' }, `${item.before_pct}% → ${item.after_pct}%`));
  return tipped(row, `${item.group}: ${item.before_pct}% of samples before, ${item.after_pct}% after.\nTVD ${item.tvd.toFixed(2)} (0 = no change, 1 = completely different).\n`
    + 'This may be a bias worth reporting if the group matters for your question.', title);
}

/** Who the steps removed: only shifts that may be a bias are drawn; expected ones are one quiet line. */
function biasSection(res) {
  const th = res.shift_threshold;
  const found = [];
  const expected = [];
  for (const st of res.steps) {
    for (const x of st.shift || []) {
      if (x.tvd < th) continue;
      (x.expected ? expected : found).push({ ...x, where: `step ${st.index + 1}` });
    }
  }
  for (const x of res.overall_shift || []) if (x.tvd >= th) (x.expected ? expected : found).push({ ...x, where: 'overall' });
  found.sort((a, b) => b.tvd - a.tvd);
  const kids = [];
  if (found.length) {
    const rows = found.map(x => dumbbell(x.characteristic, `${x.where} · ${x.group}`, x));
    kids.push(h('div', { class: 'dumbbells' }, rows.slice(0, 3)));
    if (rows.length > 3) kids.push(details(`${rows.length - 3} more`, h('div', { class: 'dumbbells' }, rows.slice(3))));
  } else {
    kids.push(h('p', { class: 'quiet' }, ic('check'), 'No step changed who is in the cohort enough to suggest a bias.'));
  }
  if (expected.length) {
    kids.push(tipped(h('p', { class: 'quiet', tabindex: '0' }, ic('info'),
      `${expected.length} expected change${expected.length > 1 ? 's' : ''}, because of what you filtered on`),
    expected.slice(0, 4).map(x => `${x.characteristic} (${x.where}): ${x.group} ${x.before_pct}% → ${x.after_pct}%. ${x.expected}.`).join('\n'),
    'Expected, not a bias'));
  }
  return sec('Who the steps removed', ...kids);
}

function exportSection(res) {
  if (!res.exportable) return sec('Export', alertBar('bad', 'lock', 'Export is paused.', res.export_block_reason));
  const url = f => `/api/searches/${res.search_id}/files/${encodeURIComponent(f)}?user=${encodeURIComponent(session.user)}`;
  const others = res.files.filter(f => f !== 'cohort_samples.csv');
  const menu = h('details', { class: 'files' }, h('summary', { class: 'btn small' }, ic('folder-down'), `Other files (${others.length})`),
    h('div', { class: 'files-menu' }, others.map(f => h('a', { download: f, href: url(f) }, ic('download'), h('span', {}, h('code', {}, f), h('small', {}, FILE_INFO[f] || ''))))));
  return sec('Export', h('div', { class: 'exports' },
    tipped(h('a', { class: 'btn small ink', download: 'cohort_samples.csv', href: url('cohort_samples.csv') }, ic('download'), 'Download cohort_samples.csv'), FILE_INFO['cohort_samples.csv']),
    menu));
}

function renderResult(res) {
  const v = res.verdict;
  const box = h('div', { class: 'result' });
  const vers = res.versions || [];
  const cur = vers.length ? vers[vers.length - 1] : null;
  box.append(h('div', { class: 'kicker' }, h('span', { class: 'k-main' }, 'Result'), h('span', { class: 'chip' }, modeLabel(res.mode)),
    cur && vers.length > 1 ? tipped(h('button', { type: 'button', class: 'chip ver-chip', onclick: () => openEditor(res) }, ic('history'), `v${cur.n} of ${vers.length}`),
      `${cur.note || 'Edited'}\nClick to see every version.`, `Version ${cur.n}`) : null,
    clock()));
  box.append(h('div', { class: 'result-head' },
    h('div', {}, h('div', { class: 'big', 'data-count': String(res.final.samples) }, G() ? '0' : fmt(res.final.samples)), h('div', { class: 'big-sub' }, `samples · ${fmt(res.final.patients)} patients`)),
    h('div', { class: 'rh-side' }, tipped(verdictLabel(v.verdict), v.decision_rule || '', 'How the verdict is decided'),
      tipped(h('p', { class: 'reason clamp', tabindex: '0' }, rich(v.reason)), v.reason, v.verdict),
      checkPills(v))));
  // the filters behind the number, always in view; each one opens the editor on that step
  const recipe = (res.spec.steps || []).map((st, i) => ({ st, i, label: (res.spec_labels || [])[i] || st.op }))
    .filter(x => !(x.st.op === 'join' && ['clinical', 'survival'].includes(x.st.table) && (x.st.mode || 'rows') === 'rows' && (x.st.key || 'sample_id') === 'sample_id'));
  box.append(h('div', { class: 'recipe' },
    h('span', { class: 'label' }, 'Filters'),
    h('div', { class: 'recipe-chips' }, recipe.map(x => tipped(h('button', { type: 'button', class: `rc ${(OPS[x.st.op] || ['', 'op-sample'])[1]}`,
      onclick: () => openEditor(res, { focus: x.i }) }, h('span', { class: 'rc-dot' }), rich(chipLabel(x.label))), `${x.label}\nClick to change this step.`)),
    tipped(h('button', { type: 'button', class: 'rc add', onclick: () => openEditor(res, { addAt: 'end' }) }, ic('plus'), 'Add'), 'Add a step to this cohort')),
    h('div', { class: 'recipe-actions' },
      h('button', { type: 'button', class: 'btn small ink', onclick: () => openEditor(res) }, ic('sliders-horizontal'), 'Edit steps'),
      tipped(h('button', { type: 'button', class: 'btn small', onclick: () => openEditor(res, { tab: 'chat' }) }, ic('message-square'), 'Change in words'),
        state.aiAvailable ? 'Describe a change, for example “drop the slide requirement”.' : 'Needs the AI model. Edit steps works without it.'))));

  // things that need attention: one line each, details in the tooltip
  const alerts = h('div', { class: 'alerts' });
  if (res.rechecked && res.rechecked.length) {
    alerts.append(alertBar('info', 'check-check', `Your edits were checked against the data: ${res.rechecked.length} value${res.rechecked.length > 1 ? 's' : ''} tidied.`,
      res.rechecked.map(c => `${c.from} → ${c.to}: ${c.why}`).join('\n')));
  }
  const confirmButton = label => h('button', { class: 'btn small', onclick: async e => {
    e.currentTarget.disabled = true;
    try { const r = await api(`/api/searches/${res.search_id}/confirm`, { method: 'POST' }); replaceResult(box, r); toast('Confirmed'); refreshHistory(); }
    catch (err) { errorToast(err.message); }
  } }, label);
  const unparsed = (res.unparsed || []), unconfirmed = (res.unconfirmed || []);
  if (unparsed.length && !res.unparsed_confirmed) {
    alerts.append(alertBar('warn', 'message-circle-question', `Not turned into steps: “${unparsed.join('”, “')}”.`,
      'These words of the request were not turned into steps. Check they don’t change who should be in the cohort, or edit the steps.',
      confirmButton('They don’t change it')));
  }
  if (unconfirmed.length && !res.unparsed_confirmed) {
    alerts.append(alertBar('warn', 'message-circle-question', `Not confirmed: ${unconfirmed.join('; ')}.`,
      'These parts of the request could be read more than one way, and nobody has confirmed the reading the steps use. Edit the steps if you meant another.',
      ...(unparsed.length && !res.unparsed_confirmed ? [] : [confirmButton('That’s what I meant')])));
  }
  for (const st of res.steps.filter(x => x.inflated)) {
    const ex = (st.duplicate_examples || []).slice(0, 3).map(e => `${e.sample_id} appears ${e.rows} times`);
    const fix = h('button', { class: 'btn small eosin', onclick: async e => {
      e.currentTarget.disabled = true;
      try {
        const r = await api(`/api/searches/${res.search_id}/fix`, { method: 'POST', body: { step_index: st.index } });
        // say what the fix changed, not just that it happened ("Keep each sample once if it has at least one matching slide, ...")
        replaceResult(box, r, `Step ${st.index + 1} fixed: ${st.fix || 'every sample is now counted once.'}`);
        toast('Double count fixed');
        did('fix');
        refreshHistory();
      } catch (err) { errorToast(err.message); }
    } }, ic('wrench'), 'Fix it');
    alerts.append(alertBar('bad', 'copy', `Step ${st.index + 1} counts ${fmt(st.rows - st.samples)} rows twice.`,
      [st.inflation_note, ...ex, st.fix ? `Fix: ${st.fix}` : ''].filter(Boolean).join('\n'), fix));
  }
  for (const st of res.steps.filter(x => x.selection)) {
    const exs = st.selection.examples.map(x => `${x.patient_id}: kept ${x.kept} (${x.kept_type}), dropped ${x.dropped.join(', ')}`);
    alerts.append(alertBar('info', 'user-round', `Step ${st.index + 1} chose one sample for each of ${fmt(st.selection.patients)} patients with more than one.`,
      [st.selection.examples[0] ? `Rule: ${st.selection.examples[0].reason}` : '', ...exs, 'Every choice is in sample_selection.csv.'].filter(Boolean).join('\n')));
  }
  if (alerts.childElementCount) box.append(alerts);

  box.append(attrition(res, i => openEditor(res, { focus: i })));
  box.append(biasSection(res));
  box.append(exportSection(res));

  // everything else, folded
  const fails = v.checks.filter(c => c.status !== 'pass').length;
  box.append(h('div', { class: 'folds' },
    details(`Before anyone uses it (${v.human_review.length})`, h('ul', {}, v.human_review.map(x => h('li', {}, rich(x))))),
    details(`All checks${fails ? ` · ${fails} to look at` : ''}`, h('table', { class: 'checks' }, h('tbody', {}, v.checks.map(c => h('tr', {},
      h('td', {}, h('span', { class: `st ${c.status}` }, STATUS[c.status] || c.status)), h('td', {}, h('b', {}, c.name), ' — ', rich(c.detail))))))),
    details(`Limitations (${v.limitations.length})`, h('ul', {}, v.limitations.map(x => h('li', {}, rich(x)))), h('p', { class: 'muted' }, v.decision_rule)),
    res.notes && res.notes.length ? details('How the request was read', h('ul', {}, res.notes.map(x => h('li', {}, rich(x))))) : null));

  const copy = (text, msg) => navigator.clipboard && navigator.clipboard.writeText(text).then(() => toast(msg), () => errorToast('Copy failed'));
  const fav = h('button', { class: 'btn small', onclick: async () => {
    try { await api('/api/favorites', { method: 'POST', body: { search_id: res.search_id } }); fav.disabled = true; fav.replaceChildren(ic('star'), 'Saved'); paintIcons(); toast('Saved to favourites', 'star'); refreshFavorites(); }
    catch (e) { errorToast(e.message); }
  } }, ic('star'), 'Save as favourite');
  box.append(h('div', { class: 'actions' }, fav,
    h('a', { class: 'btn small', href: `/compare?a=s:${res.search_id}` }, ic('git-compare'), 'Compare with another'),
    h('a', { class: 'btn small', href: `/canvas?open=${res.search_id}` }, ic('workflow'), 'Open in canvas'),
    h('span', { class: 'spacer' }),
    tipped(h('button', { class: 'icon-btn', onclick: () => copy(JSON.stringify(res.spec, null, 2), 'Steps copied as JSON') }, ic('braces')), 'Copy the steps as JSON'),
    tipped(h('button', { class: 'icon-btn', onclick: () => copy(`${location.origin}/?open=${res.search_id}`, 'Link copied') }, ic('link-2')), 'Copy a link to this cohort')));
  return box;
}
/** Problems also get a toast, once, when a result appears. */
function resultToasts(res) {
  const infl = res.steps.filter(s => s.inflated);
  if (infl.length) warnToast(`Step ${infl[0].index + 1} counts samples more than once. Export is paused until it's fixed.`);
  else if (res.verdict.verdict === 'Blocked') errorToast(`Blocked: ${(res.verdict.checks.find(c => c.status === 'fail') || {}).name || 'see the checks'}.`);
  else if (res.unparsed && res.unparsed.length && !res.unparsed_confirmed) warnToast('Part of the request wasn’t turned into steps. Please check it.');
  else if (res.unconfirmed && res.unconfirmed.length && !res.unparsed_confirmed) warnToast('Part of the request could be read more than one way. Please confirm the reading.');
}
function showResult(panel, res) {
  setPanel(panel, renderResult(res));
  resultToasts(res);
  if (state.specimen) { state.specimen.highlight(res.cohort_ids); pulseField(); }
}
function replaceResult(box, res, okNote) {
  const fresh = renderResult(res);
  if (okNote) fresh.insertBefore(alertBar('ok', 'circle-check', okNote), fresh.children[2] || null);
  const panel = box.parentElement;
  panel.replaceChildren(fresh);
  animatePanel(panel);
  clearToasts('warn');
  resultToasts(res);
  if (state.specimen) { state.specimen.highlight(res.cohort_ids); pulseField(); }
}

// ---------------------------------------------------------------- autocomplete
const input = $('#q');
const list = $('#suggest-list');
let suggestions = [];
let active = -1;
let seq = 0;
let timer = null;
function closeSuggest() { list.hidden = true; input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant'); active = -1; }
async function fetchSuggest() {
  const q = input.value;
  const mine = ++seq;
  try {
    const data = await api(`/api/suggest?q=${encodeURIComponent(q)}`);
    if (mine !== seq) return;
    suggestions = data.suggestions || [];
    renderSuggest(q);
  } catch { closeSuggest(); }
}
function marked(text, frag) {
  const i = frag ? text.toLowerCase().indexOf(frag) : -1;
  if (i < 0) return [text];
  return [text.slice(0, i), h('mark', {}, text.slice(i, i + frag.length)), text.slice(i + frag.length)];
}
function renderSuggest(q) {
  if (!suggestions.length || document.activeElement !== input) { closeSuggest(); return; }
  active = -1;
  const items = [];
  let last = null;
  suggestions.forEach((sg, i) => {
    const recent = sg.category === 'recent search';
    const group = recent ? 'Recent' : 'In this release';
    if (group !== last) { items.push(h('li', { class: 's-head', role: 'presentation' }, group)); last = group; }
    items.push(h('li', { id: `sug-${i}`, class: 's-item', role: 'option', 'aria-selected': 'false', onmousedown: e => { e.preventDefault(); apply(sg); } },
      recent ? ic('history', 's-recent') : null,
      h('span', { class: 's-text' }, marked(sg.text, recent ? q.trim().toLowerCase() : q.slice(sg.start).trim().toLowerCase())),
      recent ? null : h('span', { class: 's-cat' }, sg.category),
      h('span', { class: 's-count' }, sg.count ? fmt(sg.count) : '')));
  });
  list.replaceChildren(...items);
  list.hidden = false;
  input.setAttribute('aria-expanded', 'true');
  paintIcons();
}
function move(delta) {
  if (!suggestions.length) return;
  active = (active + delta + suggestions.length) % suggestions.length;
  $$('.s-item', list).forEach((li, i) => li.setAttribute('aria-selected', String(i === active)));
  const el = $(`#sug-${active}`);
  if (el) { el.scrollIntoView({ block: 'nearest' }); input.setAttribute('aria-activedescendant', el.id); }
}
function apply(sg) {
  const q = input.value;
  input.value = sg.category === 'recent search' ? sg.text : `${q.slice(0, sg.start)}${sg.text} `;
  closeSuggest();
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
}
input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(fetchSuggest, 110); });
input.addEventListener('focus', () => { if (!input.value.trim()) fetchSuggest(); });
input.addEventListener('blur', () => setTimeout(closeSuggest, 120));
input.addEventListener('keydown', e => {
  if (list.hidden) return;
  if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
  else if ((e.key === 'Enter' || e.key === 'Tab') && active >= 0) { e.preventDefault(); apply(suggestions[active]); }
  else if (e.key === 'Escape') { e.preventDefault(); closeSuggest(); }
});
$('#search-form').addEventListener('submit', e => { e.preventDefault(); startSearch(input.value); });
document.addEventListener('keydown', e => {
  const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName);
  if (e.key === '/' && !typing) { e.preventDefault(); window.scrollTo({ top: 0, behavior: reduceMotion ? 'auto' : 'smooth' }); input.focus(); }
});

// ---------------------------------------------------------------- archive and favourites
const EMPTY_ART = '<svg viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="29" fill="none" stroke="currentColor" stroke-width="1.2"/><circle cx="27" cy="34" r="11" fill="#3d348b" opacity=".85"/><circle cx="41" cy="23" r="4.5" fill="#cf3f68"/><circle cx="40" cy="41" r="2.5" fill="currentColor" opacity=".35"/></svg>';
async function refreshHistory() {
  try { state.history = (await api('/api/searches?limit=60')).searches; } catch { state.history = []; }
  renderHistory();
}
function renderHistory() {
  const f = $('#history-filter').value.trim().toLowerCase();
  const items = state.history.filter(x => !f || `${x.query} ${x.verdict} ${x.mode}`.toLowerCase().includes(f));
  const host = $('#history');
  if (!items.length) {
    const art = h('div');
    art.innerHTML = EMPTY_ART;
    host.replaceChildren(h('div', { class: 'empty' }, art, h('div', {}, h('h3', {}, state.history.length ? 'Nothing matches that filter' : 'Nothing here yet'),
      h('p', {}, state.history.length ? 'Try another word, or a verdict such as “ready”.'
        : 'Every cohort you run is kept here: reopen it, refine it in words, compare it with another, or save it as a favourite.'))));
    return;
  }
  host.replaceChildren(...items.map(x => h('div', { class: `row v-${String(x.verdict || '').split(' ')[0]}` },
    h('div', { class: 'row-meta' }, `№ ${String(x.id).padStart(3, '0')}`, h('br'), timeAgo(x.updated_at)),
    h('div', {}, h('p', { class: 'row-q', onclick: () => openSearch(x.id, x.query) }, x.query),
      h('div', { class: 'row-sub' }, verdictLabel(x.verdict), `${fmt(x.n_samples)} samples · ${fmt(x.n_patients)} patients`, h('span', { class: 'muted' }, modeLabel(x.mode)))),
    h('div', { class: 'row-actions' },
      h('button', { class: 'icon-btn', title: 'Open', 'aria-label': 'Open this cohort', onclick: () => openSearch(x.id, x.query) }, ic('arrow-up-right')),
      h('a', { class: 'icon-btn', title: 'Compare with another cohort', 'aria-label': 'Compare this cohort', href: `/compare?a=s:${x.id}` }, ic('git-compare')),
      h('button', { class: 'icon-btn', title: 'Edit its steps', 'aria-label': 'Edit this cohort', onclick: () => openSearch(x.id, x.query).then(res => res && openEditor(res)) }, ic('sliders-horizontal')),
      h('button', { class: 'icon-btn', title: 'Save as favourite', 'aria-label': 'Save as favourite', onclick: async () => {
        await api('/api/favorites', { method: 'POST', body: { search_id: x.id } }); toast('Saved to favourites', 'star'); refreshFavorites(); } }, ic('star')),
      h('button', { class: 'icon-btn', title: 'Delete', 'aria-label': 'Delete this cohort', onclick: async () => {
        if (!confirm('Delete this cohort from your history?')) return;
        await api(`/api/searches/${x.id}`, { method: 'DELETE' }); toast('Deleted', 'trash-2'); refreshHistory(); } }, ic('trash-2'))))));
  paintIcons();
}
$('#history-filter').addEventListener('input', renderHistory);
async function openSearch(id, query) {
  addQuery(`Open: ${query || `cohort ${id}`}`);
  const w = working(['Rerunning the saved protocol on the frozen files']);
  const panel = addPanel(w);
  try { const res = await api(`/api/searches/${id}`); w.stop(); showResult(panel, res); return res; }
  catch (e) { w.stop(); setPanel(panel, note('bad', 'circle-alert', "Couldn't open it.", e.message)); return null; }
}
async function refreshFavorites() {
  let favs = [];
  try { favs = (await api('/api/favorites')).favorites; } catch { /* none */ }
  $('#favorites-block').hidden = !favs.length;
  $('#favorites').replaceChildren(...favs.map(f => h('span', { class: 'fav', role: 'button', tabindex: '0', title: f.has_spec ? 'Reruns the saved steps, no model call' : f.query,
    onclick: e => { if (!e.target.closest('.x')) runFavorite(f); }, onkeydown: e => { if (e.key === 'Enter') runFavorite(f); } },
  ic('star'), f.label.length > 52 ? `${f.label.slice(0, 52)}…` : f.label,
  h('button', { class: 'x', 'aria-label': `Remove favourite ${f.label}`, onclick: async e => {
    e.stopPropagation(); await api(`/api/favorites/${f.id}`, { method: 'DELETE' }); toast('Removed from favourites', 'star-off'); refreshFavorites(); } }, ic('x')))));
  paintIcons();
}
async function runFavorite(f) {
  addQuery(f.label);
  const w = working([f.has_spec ? 'Rerunning the saved protocol' : 'Drafting']);
  const panel = addPanel(w);
  try {
    const r = await api(`/api/favorites/${f.id}/run`, { method: 'POST' });
    w.stop();
    if (r.kind === 'result') showResult(panel, r.result); else setPanel(panel, renderDraft(r.draft));
    refreshHistory();
  } catch (e) { w.stop(); setPanel(panel, note('bad', 'circle-alert', "Couldn't run the favourite.", e.message)); }
}

// ---------------------------------------------------------------- advanced search
const dialog = $('#advanced');
const freshForm = () => ({ sample_types: ['Primary Tumor'], one_per_patient: false, normals: '', normal_layers: [], require_layers: [], exclude_layers: [],
  er: 'any', pr: 'any', her2: 'any', hr_positive: false, pam50: [], stage_groups: [], stages: [], age_min: '', age_max: '', menopause: [], gender: 'any',
  histology: [], radiation: 'any', neoadjuvant: 'any', lymph_nodes: 'any', genes_mutated: [], genes_not_mutated: [], survival_endpoint: '',
  survival_event_only: false, min_followup_years: '', slides: '', slides_once: false, extra_field: '', extra_value: '', extra_min: '' });
let adv = freshForm();
let previewTimer = null;
let previewSeq = 0;

function chipsCtl(key, options, { small = false } = {}) {
  const wrap = h('div', { class: 'chips' });
  const render = () => wrap.replaceChildren(...options.map(o => {
    const on = adv[key].includes(o.value);
    return h('button', { type: 'button', class: `chip-opt${small ? ' small' : ''}`, 'aria-pressed': String(on), onclick: () => {
      adv[key] = on ? adv[key].filter(v => v !== o.value) : [...adv[key], o.value]; render(); changed();
    } }, o.label, o.count !== undefined ? h('span', { class: 'n' }, fmt(o.count)) : null);
  }));
  render();
  return wrap;
}
function segCtl(key, options) {
  const wrap = h('div', { class: 'seg', role: 'group' });
  const render = () => wrap.replaceChildren(...options.map(([v, label]) => h('button', { type: 'button', 'aria-pressed': String(adv[key] === v),
    onclick: () => { adv[key] = v; render(); changed(); } }, label)));
  render();
  return wrap;
}
function switchCtl(key, label) {
  const box = h('input', { type: 'checkbox' });
  box.checked = !!adv[key];
  box.addEventListener('change', () => { adv[key] = box.checked; changed(); });
  return h('label', { class: 'toggle' }, box, h('span', { class: 'toggle-track' }), h('span', {}, label));
}
function numInput(key, attrs = {}) {
  const el = h('input', { type: 'number', ...attrs });
  el.value = adv[key];
  el.addEventListener('input', () => { adv[key] = el.value; changed(); });
  return el;
}
function field(label, ...ctl) { return h('div', { class: 'adv-field' }, label ? h('span', { class: 'adv-l' }, label) : null, ...ctl); }
function genesCtl(key, label, quick = []) {
  const chosen = h('div', { class: 'adv-genes' });
  const box = h('input', { class: 'adv-text', list: 'gene-list', placeholder: 'Type a gene such as TP53, then press Enter', 'aria-label': label });
  const add = g => { g = g.trim().toUpperCase(); if (g && !adv[key].includes(g)) { adv[key] = [...adv[key], g]; renderChosen(); changed(); } };
  box.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add(box.value); box.value = ''; } });
  box.addEventListener('change', () => { if (box.value) { add(box.value); box.value = ''; } });
  const renderChosen = () => {
    chosen.replaceChildren(...adv[key].map(g => h('button', { type: 'button', class: 'chip-opt small', 'aria-pressed': 'true', title: `Remove ${g}`,
      onclick: () => { adv[key] = adv[key].filter(x => x !== g); renderChosen(); changed(); } }, g, ic('x', 'x'))));
    paintIcons();
  };
  renderChosen();
  const quickRow = quick.length ? h('div', { class: 'adv-genes' }, h('span', { class: 'muted', style: 'font-size:13px' }, 'Most often mutated:'),
    quick.map(g => h('button', { type: 'button', class: 'chip-opt small', onclick: () => add(g.value) }, g.value, h('span', { class: 'n' }, fmt(g.count))))) : null;
  return field(label, box, chosen, quickRow);
}

function advSections() {
  const a = state.meta.advanced;
  const layers = a.layers.map(o => ({ value: o.value, label: LAYER_LABEL[o.value] || o.value, count: o.count }));
  const extraField = h('select', { class: 'adv-select', 'aria-label': 'Clinical field' }, h('option', { value: '' }, 'None'), a.extra_fields.map(f => h('option', { value: f.field }, f.label)));
  const extraHost = h('div', { style: 'margin-top:10px' });
  extraField.value = adv.extra_field;
  const renderExtra = () => {
    const f = a.extra_fields.find(x => x.field === adv.extra_field);
    if (!f) { extraHost.replaceChildren(); return; }
    if (f.numeric) {
      extraHost.replaceChildren(h('label', { class: 'adv-num' }, 'At least', numInput('extra_min', { min: f.numeric[0], max: f.numeric[1] }), `(${f.numeric[0]} to ${f.numeric[1]})`));
    } else {
      const sel = h('select', { class: 'adv-select', 'aria-label': 'Value' }, h('option', { value: '' }, 'Choose a value'), f.values.map(v => h('option', { value: v.value }, `${v.value} (${fmt(v.count)})`)));
      sel.value = adv.extra_value;
      sel.addEventListener('change', () => { adv.extra_value = sel.value; changed(); });
      extraHost.replaceChildren(sel);
    }
  };
  extraField.addEventListener('change', () => { adv.extra_field = extraField.value; adv.extra_value = ''; adv.extra_min = ''; renderExtra(); changed(); });
  renderExtra();
  const receptor = (key, label) => field(label, segCtl(key, [['any', 'Any'], ...a.receptors[key].map(v => [v.value, v.value])]));
  return [
    ['samples', 'Samples', 'Which kind of tissue sample, and whether each patient should count once.', [
      field('Sample type', chipsCtl('sample_types', a.sample_types.map(o => ({ value: o.value, label: o.value, count: o.count })))),
      field(null, switchCtl('one_per_patient', 'Keep one sample per patient (the primary tumour first)')),
      field('Matched normal tissue', segCtl('normals', [['', 'Not needed'], ['has_normal', 'Patient also has one'], ['pairs', 'Tumour–normal pairs']])),
      field('If so, the normal sample must have', chipsCtl('normal_layers', layers.map(l => ({ value: l.value, label: l.label })), { small: true }))]],
    ['data', 'Data available', 'Require a data layer, or exclude samples that have one.', [
      field('Must have', chipsCtl('require_layers', layers)),
      field('Must not have', chipsCtl('exclude_layers', layers.map(l => ({ value: l.value, label: l.label })), { small: true }))]],
    ['receptors', 'Receptors', 'Clinical IHC status from the hospital record.', [
      h('div', { class: 'adv-row' }, receptor('er', 'ER'), receptor('pr', 'PR')), receptor('her2', 'HER2'),
      field(null, switchCtl('hr_positive', 'Hormone-receptor positive (ER+ or PR+)'))]],
    ['subtype', 'PAM50 subtype', 'The RNA-based intrinsic subtype call.', [
      chipsCtl('pam50', a.pam50.map(o => ({ value: o.value, label: PAM_LABEL[o.value] || o.value, count: o.count })))]],
    ['stage', 'Stage', 'Pathologic stage. Pick whole groups, or exact sub-stages.', [
      field('Stage group (all sub-stages)', chipsCtl('stage_groups', a.stage_groups.map(o => ({ value: o.value, label: `Stage ${o.value}`, count: o.count })))),
      field('Exact sub-stages', chipsCtl('stages', a.stages.map(o => ({ value: o.value, label: o.value, count: o.count })), { small: true }))]],
    ['histology', 'Histology', 'Histological type from the pathology report.', [
      chipsCtl('histology', a.histology.map(o => ({ value: o.value, label: short(o.value), count: o.count })))]],
    ['patients', 'Patients', 'Age at diagnosis, recorded gender and menopause status.', [
      h('div', { class: 'adv-row' },
        field('Age at diagnosis', h('div', { class: 'adv-num' }, 'From', numInput('age_min', { min: 0, max: 120, placeholder: (a.age || [])[0] }), 'to', numInput('age_max', { min: 0, max: 120, placeholder: (a.age || [])[1] }), 'years')),
        field('Recorded gender', segCtl('gender', [['any', 'Any'], ...a.gender.map(v => [v.value, v.value.charAt(0) + v.value.slice(1).toLowerCase()])]))),
      field('Menopause', chipsCtl('menopause', a.menopause.map(o => ({ value: o.value, label: short(o.value), count: o.count }))))]],
    ['treatment', 'Treatment and lymph nodes', 'Yes/no treatment flags and the pathologic N category.', [
      h('div', { class: 'adv-row' },
        field('Radiation therapy', segCtl('radiation', [['any', 'Any'], ['YES', 'Received'], ['NO', 'Not received']])),
        field('Neoadjuvant treatment', segCtl('neoadjuvant', [['any', 'Any'], ['Yes', 'Yes'], ['No', 'No']]))),
      field('Lymph nodes', segCtl('lymph_nodes', [['any', 'Any'], ['positive', 'Positive (N1–N3)'], ['negative', 'Negative (N0)']]))]],
    ['genes', 'Genes', 'Somatic MC3 mutation calls. Samples without mutation data are left out.', [
      h('datalist', { id: 'gene-list' }, a.genes.map(g => h('option', { value: g.value }))),
      genesCtl('genes_mutated', 'Mutated', a.genes.slice(0, 8)), genesCtl('genes_not_mutated', 'Not mutated (wild-type)')]],
    ['outcome', 'Outcome', 'Curated survival from the TCGA-CDR. PFI is the recommended endpoint for breast cancer.', [
      field('Endpoint must be recorded', segCtl('survival_endpoint', [['', 'Not needed'], ['pfi', 'PFI'], ['os', 'Overall survival'], ['dss', 'DSS'], ['dfi', 'DFI']])),
      field(null, switchCtl('survival_event_only', 'Only patients who had the event')),
      field('Minimum follow-up', h('label', { class: 'adv-num' }, numInput('min_followup_years', { min: 0, step: '0.5' }), 'years'))]],
    ['slides', 'Pathology slides', 'Diagnostic (FFPE) or frozen-section slides from NCI Imaging Data Commons.', [
      field('Slide', segCtl('slides', [['', 'Not needed'], ['diagnostic', 'Diagnostic (FFPE)'], ['frozen', 'Frozen section'], ['any', 'Any kind']])),
      field(null, switchCtl('slides_once', 'Keep each sample once (skips the double-count check)'))]],
    ['other', 'Any other clinical field', 'Every other column this release has, with its real values.', [field('Field', extraField, extraHost)]],
  ];
}

function buildAdvanced() {
  const sections = advSections();
  const main = $('#adv-main');
  main.replaceChildren(...sections.map(([id, title, hint, controls]) => h('section', { class: 'adv-sec', id: `adv-${id}` },
    h('h3', {}, title), h('p', {}, rich(hint)), ...controls)));
  const nav = $('#adv-nav');
  nav.replaceChildren(...sections.map(([id, title], i) => h('a', { href: `#adv-${id}`, class: i === 0 ? 'active' : '',
    onclick: e => { e.preventDefault(); $(`#adv-${id}`).scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' }); } }, title)));
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver(entries => entries.forEach(en => {
      if (en.isIntersecting) $$('a', nav).forEach(a => a.classList.toggle('active', a.getAttribute('href') === `#${en.target.id}`));
    }), { root: main, rootMargin: '0px 0px -70% 0px' });
    $$('.adv-sec', main).forEach(sct => io.observe(sct));
  }
  changed(true);
  paintIcons();
}

function advPayload() {
  const out = { ...adv, extra_filters: [] };
  const f = state.meta.advanced.extra_fields.find(x => x.field === adv.extra_field);
  if (f && f.numeric && adv.extra_min !== '') out.extra_filters.push({ field: f.field, cmp: 'gte', value: Number(adv.extra_min) });
  else if (f && !f.numeric && adv.extra_value) out.extra_filters.push({ field: f.field, cmp: 'in', value: [adv.extra_value] });
  delete out.extra_field; delete out.extra_value; delete out.extra_min;
  return out;
}
function criteria() {
  const c = [];
  const join = xs => xs.join(', ');
  c.push(`${join(adv.sample_types.length ? adv.sample_types : ['Primary Tumor'])} samples`);
  if (adv.one_per_patient) c.push('One sample per patient');
  if (adv.normals) c.push(adv.normals === 'pairs' ? 'Tumour–normal pairs' : 'Patient also has a normal-tissue sample');
  if (adv.normals && adv.normal_layers.length) c.push(`Normal sample has ${join(adv.normal_layers.map(l => LAYER_LABEL[l]))}`);
  if (adv.require_layers.length) c.push(`Has ${join(adv.require_layers.map(l => LAYER_LABEL[l]))}`);
  if (adv.exclude_layers.length) c.push(`Has no ${join(adv.exclude_layers.map(l => LAYER_LABEL[l]))}`);
  for (const k of ['er', 'pr', 'her2']) if (adv[k] !== 'any') c.push(`${k.toUpperCase()} ${adv[k]}`);
  if (adv.hr_positive) c.push('Hormone-receptor positive (ER+ or PR+)');
  if (adv.pam50.length) c.push(`PAM50: ${join(adv.pam50.map(p => PAM_LABEL[p] || p))}`);
  if (adv.stage_groups.length || adv.stages.length) c.push(`Stage ${join([...adv.stage_groups, ...adv.stages.map(x => x.replace('Stage ', ''))])}`);
  if (adv.histology.length) c.push(join(adv.histology.map(short)));
  if (adv.age_min !== '' || adv.age_max !== '') c.push(`Age ${adv.age_min || '…'}–${adv.age_max || '…'}`);
  if (adv.gender !== 'any') c.push(`Gender ${adv.gender.toLowerCase()}`);
  if (adv.menopause.length) c.push(`${join(adv.menopause.map(short))}-menopausal`);
  if (adv.radiation !== 'any') c.push(adv.radiation === 'YES' ? 'Received radiation therapy' : 'No radiation therapy');
  if (adv.neoadjuvant !== 'any') c.push(adv.neoadjuvant === 'Yes' ? 'Had neoadjuvant treatment' : 'No neoadjuvant treatment');
  if (adv.lymph_nodes !== 'any') c.push(`Lymph node ${adv.lymph_nodes}`);
  if (adv.genes_mutated.length) c.push(`${join(adv.genes_mutated)} mutated`);
  if (adv.genes_not_mutated.length) c.push(`${join(adv.genes_not_mutated)} not mutated`);
  if (adv.survival_endpoint) c.push(`${adv.survival_endpoint.toUpperCase()} ${adv.survival_event_only ? 'event' : 'recorded'}`);
  if (adv.min_followup_years) c.push(`At least ${adv.min_followup_years} years of follow-up`);
  if (adv.slides) c.push(`${adv.slides === 'diagnostic' ? 'Diagnostic (FFPE)' : adv.slides === 'frozen' ? 'Frozen-section' : 'Any'} slide`);
  const f = state.meta.advanced.extra_fields.find(x => x.field === adv.extra_field);
  if (f && (adv.extra_value || adv.extra_min !== '')) c.push(`${f.label}: ${adv.extra_value || `at least ${adv.extra_min}`}`);
  return c;
}
function changed(now = false) {
  $('#adv-crit').replaceChildren(...criteria().map(x => h('li', {}, h('span', {}, rich(x)))));
  clearTimeout(previewTimer);
  previewTimer = setTimeout(async () => {
    const mine = ++previewSeq;
    try {
      const p = await api('/api/advanced/preview', { method: 'POST', body: { form: advPayload() } });
      if (mine !== previewSeq) return;
      $('#adv-err').hidden = p.ok;
      if (p.ok) {
        countTo($('#adv-n'), p.n_samples, 0.5);
        $('#adv-sub').replaceChildren('samples · ', h('b', {}, fmt(p.n_patients)), ' patients');
      } else { $('#adv-n').textContent = '–'; $('#adv-err').textContent = p.error; }
    } catch (e) { $('#adv-err').hidden = false; $('#adv-err').textContent = e.message; }
  }, now ? 0 : 260);
}
function openAdvanced() {
  buildAdvanced();
  dialog.showModal();
  const main = $('#adv-main');
  main.setAttribute('tabindex', '-1');
  main.style.outline = 'none';
  main.focus({ preventScroll: true });       // start in the form, not on the close button
}
$('#open-advanced').addEventListener('click', openAdvanced);
$('#adv-close').addEventListener('click', () => dialog.close());
$('#adv-reset').addEventListener('click', () => { adv = freshForm(); buildAdvanced(); });
$('#adv-build').addEventListener('click', async () => {
  const form = advPayload();
  dialog.close();
  addQuery('Advanced search');
  const w = working(['Building an exact protocol from your choices']);
  const panel = addPanel(w);
  try { const d = await api('/api/advanced', { method: 'POST', body: { form } }); w.stop(); setPanel(panel, renderDraft(d)); }
  catch (err) { w.stop(); setPanel(panel, note('bad', 'circle-alert', "Couldn't build that protocol.", err.message)); }
});

// ---------------------------------------------------------------- the background field
const canvas = $('#specimen');
let pulseUntil = 0;
function fieldOpacity() {
  if (Date.now() < pulseUntil) return 0.62;
  if (document.body.classList.contains('chatting')) return 0.15;
  return Math.max(0.15, 1 - window.scrollY / 650);
}
function syncField() { canvas.style.opacity = String(fieldOpacity()); }
function pulseField() { pulseUntil = Date.now() + 2600; syncField(); setTimeout(syncField, 2700); }
window.addEventListener('scroll', syncField, { passive: true });
async function startSpecimen() {
  try {
    const { createSpecimen } = await import('./specimen.js');
    state.specimen = createSpecimen(canvas, state.meta.specimen, { reduceMotion, theme: document.documentElement.dataset.theme, gsap: G() });
    if (!reduceMotion) {
      canvas.style.transition = 'opacity 2.4s cubic-bezier(.2,.7,.1,1)';
      canvas.style.opacity = '0';
      requestAnimationFrame(() => requestAnimationFrame(() => { syncField(); setTimeout(() => { canvas.style.transition = ''; }, 2500); }));
    } else syncField();
  } catch (e) {
    canvas.hidden = true;
  }
}

// ---------------------------------------------------------------- start-up
async function init() {
  const params = new URLSearchParams(location.search);
  initChrome({
    onTheme: theme => { if (state.specimen) state.specimen.setTheme(theme); if (state.meta) renderLegend(); },
    onUser: () => { refreshHistory(); refreshFavorites(); },
  });
  try { state.meta = await loadMeta(); }
  catch (e) { document.documentElement.classList.remove('preload'); $('#hero').append(note('bad', 'circle-alert', "Couldn't reach the server.", e.message)); paintIcons(); return; }
  const ai = state.meta.ai;
  state.aiAvailable = !!ai.available;
  showModelStatus(ai);
  const toggle = $('#use-ai');
  toggle.checked = ai.available && store.get('cs-use-ai', '1') === '1';
  toggle.disabled = !ai.available;
  toggle.addEventListener('change', () => store.set('cs-use-ai', toggle.checked ? '1' : '0'));
  $('#ai-note').textContent = ai.available ? '' : `${ai.reason} The built-in parser and advanced search still work.`;
  renderFigures();
  renderLegend();
  paintIcons();
  intro();
  startSpecimen();
  renderSpecimens();
  scrollReveals();
  await Promise.all([refreshHistory(), refreshFavorites()]);
  if (params.get('q')) { input.value = params.get('q'); startSearch(params.get('q')); }
  else if (params.get('open')) openSearch(Number(params.get('open')));
  else if (params.get('advanced')) openAdvanced();
  else if (params.get('prefill')) { input.value = params.get('prefill'); input.focus(); fetchSuggest(); }
  // tours, practice and the help menu (first visit only; Help reopens them)
  const HR = 'hormone receptor positive, HER2 negative tumors with protein data';
  initTour({ page: 'build', setups: { clarify: () => { input.value = HR; window.scrollTo({ top: 0, behavior: 'smooth' }); startSearch(HR); } } });
}
init();
