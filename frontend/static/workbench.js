// The cohort workbench: edit a built (or drafted) cohort step by step, or in words, with live counts.
//
//   Edit steps     every step is a card with real controls (fields, values with counts, numbers); switch a step
//                  off, reorder, delete or add steps; each card shows samples in -> out and what the cohort would
//                  be without it, recomputed on the frozen files as you edit.
//   Ask in words   a conversation about this cohort ("drop the slide requirement", "ER-negative instead"); each
//                  reply shows the exact changes and the new count, and can be applied to the steps.
//   Versions       every run of a saved cohort is a version; load any of them, run it again, or compare two.
import { $, $$, h, ic, paintIcons, api, fmt, rich, countTo, toast, warnToast, errorToast, tipped, G, reduceMotion } from './common.js';

const SAMPLE_TYPES = ['Primary Tumor', 'Solid Tissue Normal', 'Metastatic'];
const LAYERS = { rna: 'RNA expression', protein: 'Protein (RPPA)', mutation: 'Mutation calls (MC3)', copy_number: 'Copy number',
  survival: 'Curated survival', clinical: 'Clinical record' };
const SLIDE_KINDS = [['diagnostic (FFPE)', 'Diagnostic (FFPE)'], ['frozen, top section', 'Frozen · top'], ['frozen, middle section', 'Frozen · middle'],
  ['frozen, bottom section', 'Frozen · bottom']];
const OPS = { sample_type: ['Sample', 'op-sample'], layer: ['Has data', 'op-layer'], exclude_layer: ['No data', 'op-exclude'],
  join: ['Join', 'op-join'], filter: ['Filter', 'op-filter'], any_of: ['Either / or', 'op-filter'], mutation: ['Gene', 'op-mutation'],
  one_per_patient: ['Per patient', 'op-patient'], dedupe: ['Dedupe', 'op-sample'] };
const ADD = [
  ['layers', 'Require a data layer', 'Keep samples that have RNA, protein, mutation calls…', () => ({ op: 'layer', layer: 'rna' })],
  ['ban', 'Exclude a data layer', 'Keep samples that do not have a layer', () => ({ op: 'exclude_layer', layer: 'protein' })],
  ['filter', 'Clinical filter', 'Receptor status, subtype, stage, age, treatment…', () => ({ op: 'filter', table: 'clinical', field: 'er_status', cmp: 'in', value: ['Positive'] })],
  ['split', 'Either / or', 'Keep samples meeting any of several conditions', () => ({ op: 'any_of', table: 'clinical', conditions: [
    { field: 'er_status', cmp: 'in', value: ['Positive'] }, { field: 'pr_status', cmp: 'in', value: ['Positive'] }] })],
  ['activity', 'Outcome or follow-up', 'Survival endpoint recorded, an event, a minimum follow-up', () => ({ op: 'filter', table: 'survival', field: 'pfi', cmp: 'notnull', value: null })],
  ['dna', 'Gene mutation', 'Somatic MC3 call: mutated or not', () => ({ op: 'mutation', gene: 'TP53', mutated: true })],
  ['image', 'Pathology slide', 'Join diagnostic or frozen-section slides', () => ({ op: 'join', table: 'slides', key: 'sample_id', mode: 'rows', slide_kinds: ['diagnostic (FFPE)'] })],
  ['copy-plus', 'Matched normal tissue', 'The patient also has a normal-tissue sample', () => ({ op: 'join', table: 'normals', key: 'patient_id', mode: 'rows', add_to_cohort: false })],
  ['user-round', 'One sample per patient', 'By sample type, or the earliest / latest collected', () => ({ op: 'one_per_patient', rule: 'priority' })],
];
const SUGGESTIONS = ['Remove the slide requirement', 'Make it ER-negative instead', 'Add TP53 mutated', 'Only stage II and III',
  'Also require copy-number data', 'Keep one sample per patient', 'Patients aged 40 to 60'];

const wb = { el: null, meta: null, ai: false, onResult: null, mode: 'search', searchId: null, query: '', spec: null, draft: null,
  base: [], baseN: null, steps: [], versions: [], viewing: null, tab: 'steps', chat: [], history: [], preview: null,
  expanded: new Set(), seq: 0, timer: null, flash: new Set() };
let cidSeq = 1;
const clone = x => JSON.parse(JSON.stringify(x));
/** replaceChildren that skips null/false (the native one would print "null"). */
const put = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter(k => k !== null && k !== undefined && k !== false));
const withCid = s => ({ ...clone(s), _cid: cidSeq++ });
const strip = s => Object.fromEntries(Object.entries(s).filter(([k]) => !k.startsWith('_')));
const active = () => wb.steps.filter(s => !s._off).map(strip);
const fieldMeta = (table, field) => wb.meta.fields.find(f => f.table === table && f.field === field);
const sigOf = s => {
  if (s.op === 'filter') return `filter|${s.table}|${s.field}`;
  if (s.op === 'layer' || s.op === 'exclude_layer') return `layer|${s.layer}`;
  if (s.op === 'mutation') return `mutation|${String(s.gene || '').toUpperCase()}`;
  if (s.op === 'join') return `join|${s.table}`;
  if (s.op === 'any_of') return `any|${s.table}|${(s.conditions || []).map(c => c.field).sort().join(',')}`;
  return s.op;
};

// ---------------------------------------------------------------- plain-words summary (instant, before the server answers)
function valueText(v) { return Array.isArray(v) ? v.join(' or ') : v === null || v === undefined ? '' : String(v); }
function condText(table, c) {
  const f = fieldMeta(table, c.field);
  const name = f ? f.label : c.field;
  const words = { in: 'is', not_in: 'is not', in_raw: 'is exactly', stage_in: 'is stage', gte: 'is at least', lte: 'is at most',
    between: 'is between', notnull: 'is recorded', is_event: 'had the event', contains: 'contains' }[c.cmp] || c.cmp;
  const v = c.cmp === 'between' && Array.isArray(c.value) ? `${c.value[0]} and ${c.value[1]}` : valueText(c.value);
  return `${name} ${words}${v ? ` ${v}` : ''}`;
}
function describe(s) {
  switch (s.op) {
    case 'sample_type': return `${(s.values || []).join(' + ') || 'No'} samples`;
    case 'layer': return `Has ${LAYERS[s.layer] || s.layer} data`;
    case 'exclude_layer': return `Has no ${LAYERS[s.layer] || s.layer} data`;
    case 'mutation': return `${s.gene || '?'} ${s.mutated === false ? 'not mutated' : 'mutated'}`;
    case 'filter': return condText(s.table, s);
    case 'any_of': return (s.conditions || []).map(c => condText(s.table, c)).join(', or ');
    case 'one_per_patient': {
      if (s.rule === 'earliest' || s.rule === 'latest') return `One sample per patient: the ${s.rule} collected`;
      const first = (s.order || [])[0] || 'Primary Tumor';
      return `One sample per patient (${first === 'Primary Tumor' ? 'primary tumour' : first === 'Metastatic' ? 'metastasis' : 'normal tissue'} first)`;
    }
    case 'dedupe': return 'Keep one row per sample';
    case 'join':
      if (s.table === 'slides') return `Has a ${(s.slide_kinds || []).length ? s.slide_kinds.join(' / ') : 'pathology'} slide · ${s.mode === 'exists' ? 'once per sample' : 'one row per slide'}`;
      if (s.table === 'normals') return `${s.add_to_cohort ? 'Tumour–normal pairs' : 'Patient also has a normal sample'}${(s.require_layers || []).length ? ` (normal has ${s.require_layers.map(l => LAYERS[l]).join(', ')})` : ''}`;
      return `Join ${s.table} records on ${s.key === 'patient_id' ? 'the patient barcode' : 'the sample barcode'}`;
    default: return JSON.stringify(strip(s));
  }
}

// ---------------------------------------------------------------- small controls (each mutates a step and calls changed())
function chips(options, isOn, toggle, { small = false } = {}) {
  const wrap = h('div', { class: 'chips' });
  const render = () => wrap.replaceChildren(...options.map(o => h('button', { type: 'button', class: `chip-opt${small ? ' small' : ''}`,
    'aria-pressed': String(isOn(o.value)), onclick: () => { toggle(o.value); render(); changed(); } }, o.label, o.count !== undefined ? h('span', { class: 'n' }, fmt(o.count)) : null)));
  render();
  return wrap;
}
function seg(options, current, set) {
  const wrap = h('div', { class: 'seg', role: 'group' });
  const render = () => wrap.replaceChildren(...options.map(([v, label]) => h('button', { type: 'button', 'aria-pressed': String(current() === v),
    onclick: () => { set(v); render(); changed(); } }, label)));
  render();
  return wrap;
}
function num(value, set, attrs = {}) {
  const el = h('input', { type: 'number', class: 'wb-num', ...attrs });
  el.value = value ?? '';
  el.addEventListener('input', () => { set(el.value === '' ? '' : Number(el.value)); changed(); });
  return el;
}
const row = (label, ...ctl) => h('div', { class: 'wb-ctl' }, label ? h('span', { class: 'wb-l' }, label) : null, ...ctl);

function fieldPicker(table, field, onPick, { tables = ['clinical', 'survival'] } = {}) {
  const sel = h('select', { class: 'wb-select', 'aria-label': 'Field' });
  const groups = {};
  wb.meta.fields.filter(f => tables.includes(f.table)).forEach(f => { (groups[f.group] = groups[f.group] || []).push(f); });
  Object.entries(groups).forEach(([g, fs]) => sel.append(h('optgroup', { label: g }, fs.map(f => h('option', { value: `${f.table}|${f.field}` }, f.label)))));
  sel.value = `${table}|${field}`;
  sel.addEventListener('change', () => { const [t, f] = sel.value.split('|'); onPick(t, f); });
  return sel;
}
function defaultCondition(table, field) {
  const f = fieldMeta(table, field);
  if (!f) return { field, cmp: 'notnull', value: null };
  if (f.kind === 'event') return { field, cmp: 'notnull', value: null };
  if (f.kind === 'time') return { field, cmp: 'gte', value: 1826 };
  if (f.kind === 'numeric') return { field, cmp: 'gte', value: Math.round(((f.range || [0, 0])[0] + (f.range || [0, 0])[1]) / 2) };
  if (f.kind === 'stage') return { field, cmp: 'stage_in', value: ['II'] };
  const first = (f.values || []).find(v => !v.flag) || (f.values || [])[0];
  return { field, cmp: 'in', value: first ? [first.value] : [] };
}
function valueEditor(table, cond) {
  const f = fieldMeta(table, cond.field);
  if (!f) return h('p', { class: 'muted' }, 'This field is not in the release.');
  if (f.kind === 'event') return row('Condition', seg([['notnull', 'Is recorded'], ['is_event', 'Had the event']], () => cond.cmp, v => { cond.cmp = v; cond.value = null; }));
  if (f.kind === 'time') {
    let unit = 'years';
    const toUnit = d => (d === '' || d === null || d === undefined ? '' : unit === 'years' ? Math.round((d / 365.25) * 10) / 10 : d);
    const box = h('span');
    const draw = () => box.replaceChildren(num(toUnit(cond.value), v => { cond.value = v === '' ? '' : unit === 'years' ? Math.round(v * 365.25) : v; }, { min: 0, step: unit === 'years' ? 0.5 : 30 }));
    draw();
    const timeRow = row('Time', h('div', { class: 'wb-inline' }, box, seg([['years', 'years'], ['days', 'days']], () => unit, v => { unit = v; draw(); })));
    timeRow.hidden = cond.cmp === 'notnull';
    return h('div', {}, row('Condition', seg([['gte', 'At least'], ['lte', 'At most'], ['notnull', 'Is recorded']], () => cond.cmp, v => {
      cond.cmp = v;
      if (v === 'notnull') cond.value = null; else if (typeof cond.value !== 'number') { cond.value = 1826; draw(); }
      timeRow.hidden = v === 'notnull';
    })), timeRow);
  }
  if (f.kind === 'numeric') {
    const r = f.range || ['', ''];
    const box = h('div', { class: 'wb-inline' });
    const draw = () => {
      if (cond.cmp === 'notnull') {
        box.replaceChildren(h('span', { class: 'muted' }, `Any recorded value (range ${r[0]}–${r[1]})`));
      } else if (cond.cmp === 'between') {
        if (!Array.isArray(cond.value)) cond.value = [r[0], r[1]];
        box.replaceChildren(num(cond.value[0], v => { cond.value[0] = v; }), h('span', { class: 'muted' }, 'to'), num(cond.value[1], v => { cond.value[1] = v; }), h('span', { class: 'muted' }, `range ${r[0]}–${r[1]}`));
      } else {
        if (Array.isArray(cond.value)) cond.value = cond.value[0];
        if (cond.value === null || cond.value === undefined) cond.value = r[0];
        box.replaceChildren(num(cond.value, v => { cond.value = v; }), h('span', { class: 'muted' }, `range ${r[0]}–${r[1]}`));
      }
    };
    draw();
    return h('div', {}, row('Condition', seg([['gte', 'At least'], ['lte', 'At most'], ['between', 'Between'], ['notnull', 'Is recorded']], () => cond.cmp,
      v => { cond.cmp = v; if (v === 'notnull') cond.value = null; draw(); })), row('Value', box));
  }
  if (f.kind === 'stage') {
    const groups = ['I', 'II', 'III', 'IV'].map(g => ({ value: g, label: `Stage ${g}`, count: f.values.filter(v => new RegExp(`^Stage ${g}[ABC]?$`).test(v.value)).reduce((a, v) => a + v.count, 0) }));
    const exact = f.values.map(v => ({ value: v.value, label: v.value, count: v.count }));
    const box = h('div');
    const draw = () => box.replaceChildren(cond.cmp === 'stage_in'
      ? chips(groups, v => (cond.value || []).includes(v), v => { cond.value = (cond.value || []).includes(v) ? cond.value.filter(x => x !== v) : [...(cond.value || []), v]; })
      : chips(exact, v => (cond.value || []).includes(v), v => { cond.value = (cond.value || []).includes(v) ? cond.value.filter(x => x !== v) : [...(cond.value || []), v]; }, { small: true }));
    draw();
    return h('div', {}, row('Pick', seg([['stage_in', 'Whole stage groups'], ['in', 'Exact sub-stages']], () => cond.cmp, v => { cond.cmp = v; cond.value = v === 'stage_in' ? ['II'] : []; draw(); })), row('Stages', box));
  }
  if (cond.cmp === 'contains') {
    const t = h('input', { class: 'wb-text', value: valueText(cond.value), 'aria-label': 'Text the value contains' });
    t.addEventListener('input', () => { cond.value = t.value; changed(); });
    return h('div', {}, row('Condition', h('span', { class: 'muted' }, 'Contains the text')), row('Text', t));
  }
  if (['in', 'not_in', 'in_raw'].includes(cond.cmp) && !Array.isArray(cond.value)) cond.value = cond.value === null || cond.value === undefined ? [] : [cond.value];
  const opts = f.values.map(v => ({ value: v.value, label: v.value.length > 42 ? `${v.value.split(' (')[0]}` : v.value, count: v.count }));
  const values = row('Values', chips(opts, v => (cond.value || []).includes(v), v => {
    cond.value = (cond.value || []).includes(v) ? cond.value.filter(x => x !== v) : [...(cond.value || []), v];
  }, { small: opts.length > 8 }));
  values.hidden = cond.cmp === 'notnull';
  return h('div', {}, row('Condition', seg([['in', 'Is one of'], ['not_in', 'Is not'], ['notnull', 'Is recorded']], () => (cond.cmp === 'in_raw' ? 'in' : cond.cmp), v => {
    if (v === 'notnull') cond.value = null; else if (!Array.isArray(cond.value)) cond.value = [];
    cond.cmp = v;
    values.hidden = v === 'notnull';
  })), values);
}

function controls(st) {
  const counts = wb.meta.advanced;
  const layerOpts = (counts.layers || []).map(o => ({ value: o.value, label: LAYERS[o.value] || o.value, count: o.count }));
  switch (st.op) {
    case 'sample_type':
      return row('Sample types', chips(SAMPLE_TYPES.map(t => ({ value: t, label: t, count: (counts.sample_types.find(x => x.value === t) || {}).count })),
        v => (st.values || []).includes(v), v => { st.values = (st.values || []).includes(v) ? st.values.filter(x => x !== v) : [...(st.values || []), v]; }));
    case 'layer': case 'exclude_layer':
      return h('div', {}, row('Rule', seg([['layer', 'Must have'], ['exclude_layer', 'Must not have']], () => st.op, v => { st.op = v; })),
        row('Data layer', chips(layerOpts, v => st.layer === v, v => { st.layer = v; })));
    case 'mutation': {
      const box = h('input', { class: 'wb-text', list: 'wb-genes', value: st.gene || '', 'aria-label': 'Gene', spellcheck: 'false' });
      box.addEventListener('input', () => { st.gene = box.value.trim().toUpperCase(); changed(); });
      return h('div', {}, row('Gene', box, h('datalist', { id: 'wb-genes' }, (counts.genes || []).map(g => h('option', { value: g.value }, `${g.count} mutated`)))),
        row('Call', seg([[true, 'Mutated'], [false, 'Not mutated']], () => st.mutated !== false, v => { st.mutated = v; })));
    }
    case 'filter':
      return h('div', {}, row('Field', fieldPicker(st.table, st.field, (t, f) => { Object.assign(st, { table: t }, defaultCondition(t, f)); rerender(st._cid); changed(); })),
        valueEditor(st.table, st));
    case 'any_of': {
      const list = h('div', { class: 'wb-conds' });
      const draw = () => {
        list.replaceChildren(...(st.conditions || []).map((c, i) => h('div', { class: 'wb-cond' },
          h('div', { class: 'wb-cond-head' }, h('span', { class: 'label' }, i === 0 ? 'Keep samples where' : 'or'),
            st.conditions.length > 2 ? h('button', { type: 'button', class: 'icon-btn', title: 'Remove this condition', onclick: () => { st.conditions.splice(i, 1); draw(); changed(); } }, ic('x')) : null),
          row('Field', fieldPicker(st.table, c.field, (t, f) => { st.conditions[i] = defaultCondition(st.table, f); draw(); changed(); }, { tables: [st.table] })),
          valueEditor(st.table, c))));
        paintIcons();
      };
      draw();
      return h('div', {}, list, h('button', { type: 'button', class: 'btn small ghost', onclick: () => { st.conditions.push(defaultCondition(st.table, 'pr_status')); draw(); changed(); } }, ic('plus'), 'Add an “or” condition'));
    }
    case 'join':
      if (st.table === 'slides') {
        return h('div', {}, row('Slide kinds', chips([{ value: '*', label: 'Any kind' }, ...SLIDE_KINDS.map(([v, l]) => ({ value: v, label: l }))],
          v => (v === '*' ? !(st.slide_kinds || []).length : (st.slide_kinds || []).includes(v)),
          v => { st.slide_kinds = v === '*' ? [] : (st.slide_kinds || []).includes(v) ? st.slide_kinds.filter(x => x !== v) : [...(st.slide_kinds || []), v]; }, { small: true })),
        row('Rows', seg([['rows', 'One row per slide'], ['exists', 'Once per sample']], () => st.mode || 'rows', v => { st.mode = v; })),
        h('p', { class: 'wb-hint' }, ...rich('One row per slide shows the double counting (several slides per sample); once per sample is the fix.')));
      }
      if (st.table === 'normals') {
        return h('div', {}, row('Meaning', seg([[false, 'Patient also has one'], [true, 'Tumour–normal pairs']], () => !!st.add_to_cohort, v => { st.add_to_cohort = v; })),
          row('Normal sample must have', chips(layerOpts.map(o => ({ value: o.value, label: o.label })), v => (st.require_layers || []).includes(v),
            v => { st.require_layers = (st.require_layers || []).includes(v) ? st.require_layers.filter(x => x !== v) : [...(st.require_layers || []), v]; }, { small: true })),
          row('Rows', seg([['rows', 'One row per match'], ['exists', 'Once per sample']], () => st.mode || 'rows', v => { st.mode = v; })));
      }
      return h('div', {}, row('Match on', seg([['sample_id', 'Sample barcode'], ['patient_id', 'Patient barcode']], () => st.key || 'sample_id', v => { st.key = v; })),
        h('p', { class: 'wb-hint' }, ...rich('Joining on the patient barcode also matches the patient’s other samples, so samples can be counted twice.')));
    case 'one_per_patient': {
      // the engine's step: rule priority (by sample type, then lowest barcode) or earliest / latest collection day
      if (st.prefer && !st.order) { st.order = st.prefer; delete st.prefer; }   // older saved form
      if (!st.rule) st.rule = 'priority';
      const hasDays = !!fieldMeta('clinical', 'collection_day');
      const rules = [['priority', 'By sample type'], ...(hasDays ? [['earliest', 'Earliest collected'], ['latest', 'Latest collected']] : [])];
      const DEFAULT = ['Primary Tumor', 'Metastatic', 'Solid Tissue Normal'];
      const detail = h('div');
      const draw = () => {
        if (st.rule === 'priority') {
          detail.replaceChildren(row('Keep first', seg([['Primary Tumor', 'Primary tumour'], ['Metastatic', 'Metastasis'], ['Solid Tissue Normal', 'Normal tissue']],
            () => (st.order || DEFAULT)[0], v => { if (v === 'Primary Tumor') delete st.order; else st.order = [v, ...DEFAULT.filter(t => t !== v)]; })),
          h('p', { class: 'wb-hint' }, 'Ties go to the lowest barcode. Every choice is listed in sample_selection.csv.'));
        } else {
          delete st.order;
          detail.replaceChildren(h('p', { class: 'wb-hint' }, ...rich('Uses the sample collection day (days_to_collection). In TCGA it may record when tissue was '
            + 'procured for the project, not when surgery happened. Patients with no day are chosen by sample type.')));
        }
      };
      draw();
      return h('div', {}, row('Choose', seg(rules, () => st.rule, v => { st.rule = v; draw(); })), detail);
    }
    default:
      return h('p', { class: 'muted' }, 'Nothing to set for this step.');
  }
}

// ---------------------------------------------------------------- the steps pane
function flagFor(st) {
  const baseByCid = wb.baseMap.get(st._cid);
  const key = JSON.stringify(strip(st));
  if (baseByCid !== undefined) return baseByCid === key ? '' : 'changed';
  const same = wb.base.find(b => sigOf(b) === sigOf(st));
  if (!same) return 'added';
  return JSON.stringify(strip(same)) === key ? '' : 'changed';
}
function nodeEl(st, i) {
  const [opLabel, opClass] = OPS[st.op] || [st.op, 'op-sample'];
  const open = wb.expanded.has(st._cid);
  const flag = st._off ? 'off' : flagFor(st);
  const head = h('div', { class: 'node-head', role: 'button', tabindex: '0', 'aria-expanded': String(open),
    onclick: e => { if (!e.target.closest('.node-actions')) toggle(st._cid); }, onkeydown: e => { if (e.key === 'Enter' && e.target === head) toggle(st._cid); } },
  h('span', { class: `p-op ${opClass}` }, opLabel),
  h('span', { class: 'node-title', 'data-slot': 'title' }, rich(describe(st))),
  flag && flag !== 'off' ? h('span', { class: `node-flag ${flag}` }, flag === 'added' ? 'new' : 'edited') : null,
  h('span', { class: 'node-count', 'data-slot': 'count' }, st._off ? 'off' : '…'),
  h('span', { class: 'node-actions' },
    h('button', { type: 'button', class: 'icon-btn', title: st._off ? 'Switch this step on' : 'Switch this step off (see the cohort without it)', 'aria-label': st._off ? 'Switch on' : 'Switch off',
      onclick: () => { st._off = !st._off; renderSteps(); changed(); } }, ic(st._off ? 'eye-off' : 'eye')),
    h('button', { type: 'button', class: 'icon-btn', title: 'Move up', 'aria-label': 'Move up', disabled: i === 0, onclick: () => move(i, -1) }, ic('arrow-up')),
    h('button', { type: 'button', class: 'icon-btn', title: 'Move down', 'aria-label': 'Move down', disabled: i === wb.steps.length - 1, onclick: () => move(i, 1) }, ic('arrow-down')),
    h('button', { type: 'button', class: 'icon-btn', title: 'Delete this step', 'aria-label': 'Delete', onclick: () => remove(i) }, ic('trash-2'))),
  h('span', { class: 'node-chev' }, ic('chevron-down')));
  return h('div', { class: `node ${flag}${open ? ' open' : ''}${wb.flash.has(st._cid) ? ' flash' : ''}`, 'data-cid': st._cid },
    h('div', { class: 'node-rail' }, h('span', { class: `node-dot ${opClass}` })),
    h('div', { class: 'node-card' }, head, open ? h('div', { class: 'node-body' }, controls(st)) : null,
      h('div', { class: 'node-foot', 'data-slot': 'foot' })));
}
function addBtn(at, last = false) {
  if (last) {
    return h('div', { class: 'node-add last' }, h('button', { type: 'button', class: 'add-dot wide', onclick: e => openAddMenu(e.currentTarget, at) }, ic('plus'), 'Add a step'));
  }
  return h('div', { class: 'node-add' }, h('button', { type: 'button', class: 'add-dot', title: 'Add a step here', 'aria-label': 'Add a step here', onclick: e => openAddMenu(e.currentTarget, at) }, ic('plus')));
}
function renderSteps() {
  const host = $('#wb-steps');
  const parts = [h('div', { class: 'node fixed' }, h('div', { class: 'node-rail' }, h('span', { class: 'node-dot start' })),
    h('div', { class: 'node-card plain' }, h('div', { class: 'node-head static' }, h('span', { class: 'node-title' }, 'All samples in the release'), h('span', { class: 'node-count' }, h('b', {}, fmt(wb.meta.release.samples))))))];
  wb.steps.forEach((st, i) => { parts.push(addBtn(i)); parts.push(nodeEl(st, i)); });
  parts.push(addBtn(wb.steps.length, true));
  parts.push(h('div', { class: 'node fixed end' }, h('div', { class: 'node-rail' }, h('span', { class: 'node-dot end' })),
    h('div', { class: 'node-card ink' }, h('div', { class: 'node-head static' }, h('span', { class: 'node-title' }, 'Cohort'), h('span', { class: 'node-count', 'data-slot': 'final' }, '…')))));
  host.replaceChildren(h('div', { class: 'pipe' }, parts),
    h('div', { class: 'wb-tools' },
      h('button', { type: 'button', class: 'btn small ghost', onclick: toggleJson }, ic('braces'), 'Edit as JSON'),
      h('span', { class: 'muted' }, 'Click a step to change it. The eye switches a step off so you can see the cohort without it.')),
    h('div', { id: 'wb-json' }));
  paintIcons();
  paintPreview();
  wb.flash.clear();
}
function rerender(cid) {
  const i = wb.steps.findIndex(s => s._cid === cid);
  const old = $(`.node[data-cid="${cid}"]`, $('#wb-steps'));
  if (i < 0 || !old) { renderSteps(); return; }
  old.replaceWith(nodeEl(wb.steps[i], i));
  paintIcons();
  paintPreview();
}
function toggle(cid) {
  if (wb.expanded.has(cid)) wb.expanded.delete(cid); else wb.expanded.add(cid);
  rerender(cid);
}
function move(i, d) {
  const j = i + d;
  if (j < 0 || j >= wb.steps.length) return;
  [wb.steps[i], wb.steps[j]] = [wb.steps[j], wb.steps[i]];
  renderSteps();
  changed();
}
function remove(i) {
  const [gone] = wb.steps.splice(i, 1);
  renderSteps();
  changed();
  const undo = h('button', { type: 'button', class: 'btn small', onclick: () => { wb.steps.splice(i, 0, gone); renderSteps(); changed(); bar.remove(); } }, ic('undo-2'), 'Undo');
  const bar = h('div', { class: 'wb-undo' }, `Removed “${describe(gone)}”`, undo);
  $('#wb-steps').prepend(bar);
  paintIcons();
  setTimeout(() => bar.remove(), 6000);
}
function openAddMenu(anchor, at) {
  closeMenus();
  const menu = h('div', { class: 'wb-menu', role: 'menu' }, ADD.map(([icon, title, sub, make]) => h('button', { type: 'button', role: 'menuitem', onclick: () => {
    const st = withCid(make());
    wb.steps.splice(at, 0, st);
    wb.expanded.add(st._cid);
    wb.flash.add(st._cid);
    closeMenus();
    renderSteps();
    changed();
  } }, ic(icon), h('span', {}, h('b', {}, title), h('small', {}, sub)))));
  anchor.closest('.node-add').append(menu);
  paintIcons();
  const g = G();
  if (g) g.from(menu, { y: -6, autoAlpha: 0, duration: 0.18, ease: 'power2.out' });
  setTimeout(() => document.addEventListener('click', closeMenus, { once: true }), 0);
}
function closeMenus() { $$('.wb-menu').forEach(m => m.remove()); }
function toggleJson() {
  const host = $('#wb-json');
  if (host.firstChild) { host.replaceChildren(); return; }
  const ta = h('textarea', { class: 'wb-jsonarea', spellcheck: 'false', 'aria-label': 'All steps as JSON' });
  ta.value = JSON.stringify(wb.steps.filter(s => !s._off).map(strip), null, 1);
  const err = h('span', { class: 'muted' });
  host.replaceChildren(ta, h('div', { class: 'wb-tools' }, h('button', { type: 'button', class: 'btn small ink', onclick: () => {
    try {
      const steps = JSON.parse(ta.value);
      if (!Array.isArray(steps)) throw new Error('expected a list of steps');
      wb.steps = steps.map(withCid);
      host.replaceChildren();
      renderSteps();
      changed();
    } catch (e) { err.textContent = `Not valid: ${e.message}`; }
  } }, 'Use these steps'), err));
}

// ---------------------------------------------------------------- live preview
function changed() {
  clearTimeout(wb.timer);
  $$('[data-slot="count"]', $('#wb-steps')).forEach(el => el.classList.add('stale'));
  wb.timer = setTimeout(runPreview, 260);
}
async function runPreview() {
  const mine = ++wb.seq;
  try {
    const p = await api('/api/preview', { method: 'POST', body: { steps: wb.steps, base_steps: wb.base, impacts: true } });
    if (mine !== wb.seq) return;
    wb.preview = p;
    paintPreview();
    // a problem that an edit just introduced also gets a toast (once; the card shows the details)
    const issues = [...(p.problems || []).map(x => x.message),
      ...(p.steps || []).filter(x => x.inflated).map(x => `${x.label}: ${fmt(x.rows - x.samples)} rows counted twice`)];
    const key = issues.join('|');
    if (key && key !== wb.alertKey && !wb.fresh) warnToast(issues[0]);
    wb.alertKey = key;
    wb.fresh = false;
  } catch (e) { /* keep the last preview */ }
}
function paintPreview() {
  const p = wb.preview;
  const host = $('#wb-steps');
  if (!p || !host) return;
  const byCid = new Map((p.steps || []).filter(s => s.cid !== null && s.cid !== undefined).map(s => [s.cid, s]));
  const problems = new Map();
  (p.problems || []).forEach(x => { if (x.cid !== null && x.cid !== undefined) problems.set(x.cid, [...(problems.get(x.cid) || []), x.message]); });
  for (const st of wb.steps) {
    const el = $(`.node[data-cid="${st._cid}"]`, host);
    if (!el) continue;
    const s = byCid.get(st._cid);
    const count = $('[data-slot="count"]', el);
    count.classList.remove('stale');
    if (st._off) count.replaceChildren('off');
    else if (s && s.skipped) count.replaceChildren('not run');
    else if (s) put(count, fmt(s.samples_in), ' → ', h('b', {}, fmt(s.samples)), s.removed ? h('span', { class: 'neg' }, ` −${fmt(s.removed)}`) : null);
    else count.replaceChildren('—');
    const foot = $('[data-slot="foot"]', el);
    const parts = [];
    if (s && s.label && !st._off) $('[data-slot="title"]', el).replaceChildren(...rich(s.label));
    const impact = p.impacts && p.impacts[String(st._cid)];
    if (!st._off && impact !== undefined) {
      parts.push(h('span', { class: 'impact' }, impact === 0 ? 'Without this step: no change' : `Without this step: ${fmt(p.final.samples + impact)} samples (${impact > 0 ? '+' : ''}${fmt(impact)})`));
    }
    if (s && s.inflated) parts.push(h('span', { class: 'warn-t' }, `${fmt(s.rows - s.samples)} rows counted twice: choose “Once per sample” to fix`));
    if (s && s.error && !s.skipped) parts.push(h('span', { class: 'err-t' }, s.error));
    (problems.get(st._cid) || []).forEach(m => parts.push(h('span', { class: 'err-t' }, m)));
    foot.replaceChildren(...parts);
  }
  const fin = $('[data-slot="final"]', host);
  if (fin && p.final) fin.replaceChildren(h('b', {}, fmt(p.final.samples)), ` samples · ${fmt(p.final.patients)} patients`);
  // header + footer
  if (p.final) {
    countTo($('#wb-n'), p.final.samples, 0.5);
    $('#wb-sub').replaceChildren('samples · ', h('b', {}, fmt(p.final.patients)), ' patients');
    const d = wb.baseN === null ? null : p.final.samples - wb.baseN;
    put($('#wb-delta'), d === null || d === 0 ? '' : h('span', { class: d > 0 ? 'up' : 'down' }, `${d > 0 ? '+' : '−'}${fmt(Math.abs(d))} vs ${wb.mode === 'search' && wb.versions.length ? `v${wb.versions[wb.versions.length - 1].n}` : 'the draft'}`));
  }
  renderChanges(p.changes || [], p.fixes || []);
}
function renderChanges(changes, fixes) {
  const host = $('#wb-changes');
  const icon = { added: 'plus', removed: 'trash-2', changed: 'pen-line' };
  if (!changes.length) {
    host.replaceChildren(h('span', { class: 'muted' }, wb.mode === 'draft' ? 'Edit any step, then run.' : 'No changes yet.'));
  } else {
    put(host, h('span', { class: 'label' }, `${changes.length} change${changes.length > 1 ? 's' : ''}`),
      h('ul', { class: 'wb-chlist' }, changes.slice(0, 4).map(c => h('li', { class: c.kind }, ic(icon[c.kind]),
        c.kind === 'changed' ? h('span', {}, h('s', {}, c.before), ' → ', rich(c.after)) : h('span', {}, rich(c.after || c.before))))),
      changes.length > 4 ? h('span', { class: 'muted' }, `and ${changes.length - 4} more`) : null);
  }
  if (fixes.length) host.append(h('span', { class: 'muted', title: fixes.map(f => `${f.from} → ${f.to}`).join('\n') }, `${fixes.length} value${fixes.length > 1 ? 's' : ''} tidied`));
  $('#wb-run').disabled = wb.mode === 'search' && !changes.length;
  paintIcons();
}

// ---------------------------------------------------------------- versions
function renderVersions() {
  const host = $('#wb-versions');
  if (wb.mode !== 'search' || !wb.versions.length) { host.replaceChildren(wb.mode === 'draft' ? h('span', { class: 'muted' }, 'Draft · not run yet') : ''); return; }
  const latest = wb.versions[wb.versions.length - 1].n;
  put(host, h('span', { class: 'label' }, 'Versions'), ...wb.versions.map(v => h('button', { type: 'button',
    class: `ver${(wb.viewing || latest) === v.n ? ' on' : ''}`, title: `${v.note || v.source} · ${fmt(v.n_samples)} samples · ${v.verdict || ''}`,
    onclick: () => loadVersion(v.n) }, `v${v.n}`, h('small', {}, fmt(v.n_samples)))),
  wb.viewing && wb.viewing !== latest ? h('a', { class: 'text-link', href: `/compare?a=v:${wb.searchId}:${wb.viewing}&b=v:${wb.searchId}:${latest}` }, `Compare v${wb.viewing} with v${latest}`, ic('arrow-right')) : null);
  paintIcons();
}
async function loadVersion(n) {
  const latest = wb.versions[wb.versions.length - 1].n;
  try {
    const v = await api(`/api/searches/${wb.searchId}/versions/${n}`);
    wb.viewing = n === latest ? null : n;
    wb.steps = v.spec.steps.map(withCid);
    renderVersions();
    renderSteps();
    changed();
    if (n !== latest) toast(`Loaded v${n}. Run it to make it the current version.`, 'history');
  } catch (e) { errorToast(e.message); }
}

// ---------------------------------------------------------------- ask in words
function renderChat() {
  const host = $('#wb-chat');
  const thread = h('div', { class: 'wb-thread', id: 'wb-thread' });
  if (!wb.chat.length) {
    thread.append(h('div', { class: 'wb-intro' }, h('h3', {}, 'Change it in your own words'),
      h('p', {}, 'Ask to add, remove or change anything: a filter, a value, a data layer, a slide requirement. Every reply shows the exact changes and the new count before anything is applied.')));
  }
  wb.chat.forEach(m => thread.append(m.el));
  const box = h('textarea', { class: 'wb-input', rows: 2, placeholder: wb.ai ? 'e.g. drop the slide requirement and make it ER-negative' : 'Editing in words needs the AI model; use Edit steps instead.',
    disabled: !wb.ai, 'aria-label': 'Describe a change' });
  const send = () => { const t = box.value.trim(); if (t) { box.value = ''; askChange(t); } };
  box.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });
  const dynamic = wb.steps.filter(s => !s._off && !(s.op === 'join' && ['clinical', 'survival'].includes(s.table)) && s.op !== 'sample_type').slice(0, 3)
    .map(s => `Remove “${describe(s)}”`);
  host.replaceChildren(thread,
    h('div', { class: 'wb-compose' },
      h('div', { class: 'chips' }, [...dynamic, ...SUGGESTIONS].slice(0, 8).map(t => h('button', { type: 'button', class: 'chip-opt small', disabled: !wb.ai, onclick: () => askChange(t) }, t))),
      h('div', { class: 'wb-send' }, box, h('button', { type: 'button', class: 'btn ink', disabled: !wb.ai, onclick: send, 'aria-label': 'Send' }, ic('send')))));
  paintIcons();
  thread.scrollTop = thread.scrollHeight;
}
function changeList(diff) {
  const icon = { added: 'plus', removed: 'trash-2', changed: 'pen-line' };
  if (!diff.length) return h('p', { class: 'muted' }, 'No change to the steps.');
  return h('ul', { class: 'wb-chlist roomy' }, diff.map(c => h('li', { class: c.kind }, ic(icon[c.kind]),
    c.kind === 'changed' ? h('span', {}, h('s', {}, c.before), ' → ', rich(c.after)) : h('span', {}, rich(c.after || c.before)))));
}
async function askChange(text) {
  const userEl = h('div', { class: 'wb-msg user' }, text);
  const reply = h('div', { class: 'wb-msg bot' }, h('div', { class: 'working' }, h('div', { class: 'working-line' }, h('span', { class: 'w-k' }, 'Working'), 'Reading your change against this cohort'), h('div', { class: 'progress' })));
  wb.chat.push({ el: userEl }, { el: reply });
  renderChat();
  const current = active();
  const before = wb.preview && wb.preview.final ? wb.preview.final.samples : null;
  try {
    const d = await api('/api/draft', { method: 'POST', body: { query: text, use_ai: true, current_steps: current, current_request: wb.query, history: wb.history } });
    reply.replaceChildren(proposal(d, text, before));
  } catch (e) {
    reply.replaceChildren(h('div', { class: 'note bad' }, ic('circle-alert'), h('div', {}, h('b', {}, "Couldn't work that out. "), e.message)));
  }
  paintIcons();
  const t = $('#wb-thread');
  if (t) t.scrollTop = t.scrollHeight;
}
function proposal(d, text, before) {
  const box = h('div', { class: 'wb-prop' });
  box.append(h('p', { class: 'wb-prop-t' }, rich(d.interpretation || 'Here is the updated cohort.')));
  for (const u of d.unsupported || []) box.append(h('div', { class: 'note warn' }, ic('circle-off'), h('div', {}, h('b', {}, `Not in this release: “${u.phrase}”. `), rich(u.reason || ''))));
  for (const w of d.warnings || []) box.append(h('div', { class: 'note warn' }, ic('triangle-alert'), h('div', {}, rich(w))));
  const finish = (steps, diff, after) => {
    box.append(changeList(diff),
      h('div', { class: 'wb-prop-n' }, before === null ? '' : `${fmt(before)} → `, h('b', {}, fmt(after)), ' samples'),
      h('div', { class: 'wb-prop-actions' },
        h('button', { type: 'button', class: 'btn small', disabled: !diff.length, onclick: () => applyProposal(steps, text, false) }, ic('pen-line'), 'Apply to steps'),
        h('button', { type: 'button', class: 'btn small ink', disabled: !diff.length, onclick: () => applyProposal(steps, text, true) }, ic('play'), wb.mode === 'search' ? 'Apply and run' : 'Apply and run')));
    paintIcons();
  };
  if (d.clarifications && d.clarifications.length) {
    const answers = {};
    d.clarifications.forEach(q => {
      const opts = h('div', { class: 'options compact' }, q.options.map((o, i) => h('button', { type: 'button', class: 'option', 'aria-pressed': 'false', onclick: async e => {
        answers[q.id] = i;
        e.currentTarget.parentElement.querySelectorAll('.option').forEach(b => b.setAttribute('aria-pressed', String(b === e.currentTarget)));
        if (Object.keys(answers).length < d.clarifications.length) return;
        const r = await api(`/api/drafts/${d.draft_id}/resolve`, { method: 'POST', body: { answers } });
        const p = await api('/api/preview', { method: 'POST', body: { steps: r.spec.steps, base_steps: active(), impacts: false } });
        finish(r.spec.steps, p.changes, p.final ? p.final.samples : null);
      } }, h('span', { class: 'o-label' }, o.label), h('span', { class: 'o-n' }, fmt(o.n_samples), h('small', {}, 'samples')))));
      box.append(h('div', { class: 'question' }, h('div', { class: 'q-head' }, h('h3', { class: 'q-title' }, rich(q.question))), q.why ? h('p', { class: 'q-why' }, rich(q.why)) : null, opts));
    });
  } else if (d.spec) {
    finish(d.spec.steps, d.diff || [], d.preview ? d.preview.n_samples : null);
  }
  return box;
}
function applyProposal(steps, text, run) {
  const prev = new Map(wb.steps.map(s => [sigOf(s), s._cid]));
  wb.steps = steps.map(s => {
    const st = withCid(s);
    const old = prev.get(sigOf(s));
    if (old !== undefined && wb.baseMap.has(old)) { wb.baseMap.set(st._cid, wb.baseMap.get(old)); }
    return st;
  });
  wb.steps.forEach(s => { if (flagFor(s)) wb.flash.add(s._cid); });
  wb.history.push(text);
  if (run) { save(text, 'chat'); return; }
  setTab('steps');
  changed();
  toast('Applied. Check the steps, then run.', 'pen-line');
}

// ---------------------------------------------------------------- run / save
async function save(note = '', source = 'edit') {
  const btn = $('#wb-run');
  btn.disabled = true;
  const changes = (wb.preview && wb.preview.changes) || [];
  const autoNote = note || (wb.viewing ? `Restored v${wb.viewing}` : changes.slice(0, 3).map(c => (c.kind === 'removed' ? `Removed ${c.before}` : c.kind === 'added' ? `Added ${c.after}` : `Changed to ${c.after}`)).join('; '));
  try {
    let res;
    if (wb.mode === 'search') {
      res = await api(`/api/searches/${wb.searchId}/versions`, { method: 'POST', body: { steps: wb.steps, note: autoNote, source: wb.viewing ? 'restore' : source } });
    } else {
      const spec = { ...wb.spec, steps: active() };
      res = await api('/api/run', { method: 'POST', body: { spec, query: wb.query, mode: wb.draft.mode || 'ai', draft_id: wb.draft.draftId, edited: true } });
    }
    close(true);
    if (wb.onResult) wb.onResult(res, autoNote);
  } catch (e) {
    errorToast(e.message);
    btn.disabled = false;
  }
}
async function fork() {
  const host = $('#wb-forkbox');
  if (host.firstChild) { host.replaceChildren(); return; }
  const name = h('input', { class: 'wb-text', value: `${wb.query} (edited)`, 'aria-label': 'Name for the new cohort' });
  host.replaceChildren(name, h('button', { type: 'button', class: 'btn small ink', onclick: async () => {
    try {
      const res = await api('/api/fork', { method: 'POST', body: { steps: wb.steps, query: name.value.trim() || wb.query, base_search_id: wb.mode === 'search' ? wb.searchId : null } });
      close(true);
      if (wb.onResult) wb.onResult(res, 'Saved as a new cohort');
    } catch (e) { errorToast(e.message); }
  } }, 'Save'));
  name.focus();
  name.select();
}

// ---------------------------------------------------------------- shell
function ensureDom() {
  if (wb.el) return;
  const scrim = h('div', { class: 'wb-scrim', hidden: true, onclick: () => close() });
  const el = h('aside', { class: 'wb', hidden: true, role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'wb-title' },
    h('header', { class: 'wb-head' },
      h('div', { class: 'wb-top' }, h('div', { class: 'wb-titles' }, h('span', { class: 'label', id: 'wb-kind' }, 'Editing'), h('h2', { id: 'wb-title' })),
        h('div', { class: 'wb-live' }, h('div', { class: 'wb-n', id: 'wb-n' }, '–'), h('div', { class: 'wb-sub', id: 'wb-sub' }, 'samples'), h('div', { class: 'wb-delta', id: 'wb-delta' })),
        h('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Close the editor', onclick: () => close() }, ic('x'))),
      h('div', { class: 'wb-versions', id: 'wb-versions' }),
      h('nav', { class: 'wb-tabs', role: 'tablist' },
        h('button', { type: 'button', role: 'tab', 'data-tab': 'steps', onclick: () => setTab('steps') }, ic('sliders-horizontal'), 'Edit steps'),
        h('button', { type: 'button', role: 'tab', 'data-tab': 'chat', onclick: () => setTab('chat') }, ic('message-square'), 'Ask in words'))),
    h('div', { class: 'wb-body' }, h('div', { class: 'wb-pane', id: 'wb-steps' }), h('div', { class: 'wb-pane', id: 'wb-chat', hidden: true })),
    h('footer', { class: 'wb-foot' },
      h('div', { class: 'wb-changes', id: 'wb-changes' }),
      h('div', { class: 'wb-forkbox', id: 'wb-forkbox' }),
      h('div', { class: 'wb-actions' },
        h('button', { type: 'button', class: 'btn ghost', onclick: discard }, 'Discard'),
        h('button', { type: 'button', class: 'btn', id: 'wb-fork', onclick: fork }, ic('copy-plus'), 'Save as new cohort'),
        h('button', { type: 'button', class: 'btn ink', id: 'wb-run', onclick: () => save() }, ic('play'), h('span', { id: 'wb-run-t' }, 'Run')))));
  document.body.append(scrim, el);
  wb.el = el;
  wb.scrim = scrim;
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !wb.el.hidden && !document.querySelector('dialog[open]')) close(); });
}
function setTab(tab) {
  wb.tab = tab;
  $$('.wb-tabs button', wb.el).forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
  $('#wb-steps').hidden = tab !== 'steps';
  $('#wb-chat').hidden = tab !== 'chat';
  if (tab === 'chat') renderChat(); else renderSteps();
}
function dirty() { return !!(wb.preview && wb.preview.changes && wb.preview.changes.length); }
function discard() {
  if (dirty() && !confirm('Discard your changes?')) return;
  wb.steps = wb.base.map(withCid);
  wb.baseMap = new Map(wb.steps.map(s => [s._cid, JSON.stringify(strip(s))]));
  wb.viewing = null;
  renderVersions();
  renderSteps();
  changed();
}
function close(force = false) {
  if (!force && dirty() && !confirm('Close the editor and lose your changes?')) return;
  const g = G();
  const done = () => { wb.el.hidden = true; wb.scrim.hidden = true; document.body.classList.remove('wb-open'); };
  if (g) { g.to(wb.el, { xPercent: 100, duration: 0.3, ease: 'power3.in', onComplete: done }); g.to(wb.scrim, { autoAlpha: 0, duration: 0.3 }); }
  else done();
}

/**
 * Open the editor.
 *   search mode: { searchId, query, spec, versions, nSamples }   -> runs save a new version
 *   draft mode:  { draft: { draftId, mode }, query, spec }       -> runs create the cohort
 */
export function openWorkbench(opts) {
  ensureDom();
  Object.assign(wb, { meta: opts.meta, ai: !!opts.ai, onResult: opts.onResult, mode: opts.searchId ? 'search' : 'draft', searchId: opts.searchId || null,
    query: opts.query || (opts.spec && opts.spec.request) || 'Cohort', spec: clone(opts.spec), draft: opts.draft || null, versions: opts.versions || [],
    viewing: null, chat: [], history: [], preview: null, baseN: opts.nSamples ?? null });
  wb.expanded = new Set();
  wb.fresh = true;
  wb.alertKey = '';
  wb.steps = opts.spec.steps.map(withCid);
  wb.base = opts.spec.steps.map(clone);
  wb.baseMap = new Map(wb.steps.map(s => [s._cid, JSON.stringify(strip(s))]));
  $('#wb-title').textContent = wb.query;
  $('#wb-kind').textContent = wb.mode === 'search' ? 'Editing a saved cohort' : 'Editing a draft';
  $('#wb-run-t').textContent = wb.mode === 'search' ? `Run as v${(wb.versions.length ? wb.versions[wb.versions.length - 1].n : 1) + 1}` : 'Run cohort';
  $('#wb-fork').hidden = wb.mode !== 'search';
  $('#wb-forkbox').replaceChildren();
  $('#wb-n').textContent = fmt(opts.nSamples);
  $('#wb-sub').textContent = 'samples';
  $('#wb-delta').replaceChildren();
  $('#wb-changes').replaceChildren();
  const focusStep = Number.isInteger(opts.focus) ? wb.steps[opts.focus] : null;
  if (focusStep) { wb.expanded.add(focusStep._cid); wb.flash.add(focusStep._cid); }
  renderVersions();
  setTab(opts.tab || 'steps');
  if (focusStep) setTimeout(() => { const n = $(`.node[data-cid="${focusStep._cid}"]`, wb.el); if (n) n.scrollIntoView({ block: 'center', behavior: reduceMotion ? 'auto' : 'smooth' }); }, 420);
  if (opts.addAt === 'end') setTimeout(() => { const b = $$('.add-dot', wb.el).pop(); if (b) { b.scrollIntoView({ block: 'center' }); openAddMenu(b, wb.steps.length); } }, 420);
  wb.el.hidden = false;
  wb.scrim.hidden = false;
  document.body.classList.add('wb-open');
  const g = G();
  if (g) {
    g.fromTo(wb.el, { xPercent: 100 }, { xPercent: 0, duration: 0.45, ease: 'power3.out' });
    g.fromTo(wb.scrim, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.3 });
    g.from($$('.node', wb.el), { x: 18, autoAlpha: 0, duration: 0.4, stagger: 0.04, delay: 0.15, ease: 'power3.out', clearProps: 'transform,opacity,visibility' });
  }
  runPreview();
  if (opts.tab === 'chat') setTimeout(() => { const b = $('.wb-input', wb.el); if (b) b.focus(); }, 350);
}
