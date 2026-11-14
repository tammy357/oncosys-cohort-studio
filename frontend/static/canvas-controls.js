// Step vocabulary for the canvas: what each step is called, how it reads in plain words, the blocks the palette
// offers, and the controls that edit a step in the inspector (adapted from the step editor in workbench.js, plus a
// visual builder for combined AND / OR / NOT conditions instead of JSON).
import { h, ic, paintIcons, fmt, rich } from './common.js';

export const SAMPLE_TYPES = ['Primary Tumor', 'Solid Tissue Normal', 'Metastatic'];
export const LAYERS = { rna: 'RNA expression', protein: 'Protein (RPPA)', mutation: 'Mutation calls (MC3)', copy_number: 'Copy number',
  survival: 'Curated survival', clinical: 'Clinical record' };
export const SLIDE_KINDS = [['diagnostic (FFPE)', 'Diagnostic (FFPE)', 'DX'], ['frozen, top section', 'Frozen · top', 'TS'],
  ['frozen, middle section', 'Frozen · middle', 'MS'], ['frozen, bottom section', 'Frozen · bottom', 'BS']];
export const FROZEN = SLIDE_KINDS.filter(k => k[0].startsWith('frozen')).map(k => k[0]);
// op -> [label, colour class, icon]
export const OPS = { sample_type: ['Samples', 'op-sample', 'flask-conical'], layer: ['Has data', 'op-layer', 'layers'],
  exclude_layer: ['No data', 'op-exclude', 'ban'], join: ['Join', 'op-join', 'link-2'], filter: ['Filter', 'op-filter', 'filter'],
  any_of: ['Either / or', 'op-filter', 'split'], mutation: ['Gene', 'op-mutation', 'dna'], one_per_patient: ['Per patient', 'op-patient', 'user-round'],
  dedupe: ['Dedupe', 'op-sample', 'copy-minus'], where: ['Condition', 'op-filter', 'git-branch'] };
export const TABLE_ICON = { slides: 'image', clinical: 'clipboard-list', survival: 'activity', normals: 'copy-plus' };

const put = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter(k => k !== null && k !== undefined && k !== false));
const valueText = v => (Array.isArray(v) ? v.join(' or ') : v === null || v === undefined ? '' : String(v));
const toggleIn = (list, v) => ((list || []).includes(v) ? list.filter(x => x !== v) : [...(list || []), v]);

// ---------------------------------------------------------------- plain words
export function fieldOf(meta, table, field) { return meta.fields.find(f => f.table === table && f.field === field); }
export function condText(meta, table, c) {
  const f = fieldOf(meta, table, c.field);
  const name = f ? f.label : c.field;
  const words = { in: 'is', not_in: 'is not', in_raw: 'is exactly', stage_in: 'is stage', gte: 'is at least', lte: 'is at most',
    between: 'is between', notnull: 'is recorded', is_event: 'had the event', contains: 'contains' }[c.cmp] || c.cmp;
  let v = c.cmp === 'between' && Array.isArray(c.value) ? `${c.value[0]} and ${c.value[1]}` : valueText(c.value);
  if (f && f.kind === 'time' && typeof c.value === 'number') v = `${Math.round((c.value / 365.25) * 10) / 10} years`;
  return `${name} ${words}${v ? ` ${v}` : ''}`;
}
function testText(meta, t) {
  if (t.all) return t.all.map(x => testText(meta, x)).join(' and ');
  if (t.any) return `(${t.any.map(x => testText(meta, x)).join(' or ')})`;
  if (t.not) return `not ${testText(meta, t.not)}`;
  if (t.layer) return `has ${LAYERS[t.layer] || t.layer}`;
  if (t.sample_type) return `is ${[].concat(t.sample_type).join(' / ')}`;
  if (t.gene) return `${t.gene} ${t.mutated === false ? 'not mutated' : 'mutated'}`;
  if (t.slide_kinds) return t.slide_kinds.length ? `has a ${t.slide_kinds.join(' / ')} slide` : 'has a slide';
  if (t.table) return condText(meta, t.table, t);
  return '?';
}
export function describe(meta, s) {
  switch (s.op) {
    case 'sample_type': return (s.values || []).map(v => ({ 'Primary Tumor': 'Primary tumours', 'Solid Tissue Normal': 'Normal tissue', Metastatic: 'Metastases' }[v] || v)).join(' + ') || 'No sample type chosen';
    case 'layer': return `Has ${LAYERS[s.layer] || s.layer} data`;
    case 'exclude_layer': return `Has no ${LAYERS[s.layer] || s.layer} data`;
    case 'mutation': return `${s.gene || '?'} ${s.mutated === false ? 'not mutated' : 'mutated'}`;
    case 'filter': return condText(meta, s.table, s);
    case 'any_of': return (s.conditions || []).map(c => condText(meta, s.table, c)).join(', or ');
    case 'one_per_patient':
      if (s.rule === 'earliest' || s.rule === 'latest') return `One sample per patient: the ${s.rule} collected`;
      return `One sample per patient (${({ Metastatic: 'metastasis', 'Solid Tissue Normal': 'normal tissue' }[(s.order || [])[0]] || 'primary tumour')} first)`;
    case 'dedupe': return 'Keep one row per sample';
    case 'where': return s.condition ? `Keep samples where ${testText(meta, s.condition)}` : 'Combined condition';
    case 'join':
      if (s.table === 'slides') {
        const ks = s.slide_kinds || [];
        const name = !ks.length ? '' : FROZEN.every(k => ks.includes(k)) && ks.length === FROZEN.length ? 'frozen-section '
          : `${ks.map(k => ({ 'diagnostic (FFPE)': 'diagnostic', 'frozen, top section': 'frozen top', 'frozen, middle section': 'frozen middle',
            'frozen, bottom section': 'frozen bottom' }[k] || k)).join(' or ')} `;
        return s.mode === 'exists' ? `Has a ${name}slide` : `Every ${name}slide, one row each`;
      }
      if (s.table === 'normals') return `${s.add_to_cohort ? 'Tumour–normal pairs' : 'Patient also has normal tissue'}${(s.require_layers || []).length ? ` (normal has ${s.require_layers.map(l => LAYERS[l]).join(', ')})` : ''}`;
      return `Join ${s.table === 'survival' ? 'curated survival' : 'clinical records'} by ${s.key === 'patient_id' ? 'patient' : 'sample'}${s.mode === 'exists' ? ' (once per sample)' : ''}`;
    default: return s.op;
  }
}
/** The table a step reads from the canvas's point of view (a satellite card above it), or null. */
export function tableOf(s) {
  if (s.op === 'join') return s.table;
  return null;
}

// ---------------------------------------------------------------- palette blocks -> new steps
export function defaultCondition(meta, table, field) {
  const f = fieldOf(meta, table, field);
  if (!f) return { field, cmp: 'notnull', value: null };
  if (f.kind === 'event') return { field, cmp: 'notnull', value: null };
  if (f.kind === 'time') return { field, cmp: 'gte', value: 1826 };
  if (f.kind === 'numeric') return { field, cmp: 'gte', value: Math.round(((f.range || [0, 0])[0] + (f.range || [0, 0])[1]) / 2) };
  if (f.kind === 'stage') return { field, cmp: 'stage_in', value: ['II'] };
  const first = (f.values || []).find(v => !v.flag) || (f.values || [])[0];
  return { field, cmp: 'in', value: first ? [first.value] : [] };
}
export function stepFor(meta, block) {
  switch (block.kind) {
    case 'sample_type': return { op: 'sample_type', values: [block.value] };
    case 'layer': return { op: 'layer', layer: block.value };
    case 'exclude_layer': return { op: 'exclude_layer', layer: block.value || 'protein' };
    case 'slides': return { op: 'join', table: 'slides', key: 'sample_id', mode: block.mode || 'rows', slide_kinds: block.kinds || ['diagnostic (FFPE)'] };
    case 'clinical': return { op: 'join', table: 'clinical', key: 'sample_id', mode: 'rows' };
    case 'survival': return { op: 'join', table: 'survival', key: 'sample_id', mode: 'rows' };
    case 'normals': return { op: 'join', table: 'normals', key: 'patient_id', mode: 'rows', add_to_cohort: !!block.pairs };
    case 'field': return { op: 'filter', table: block.table, ...defaultCondition(meta, block.table, block.field) };
    case 'gene': return { op: 'mutation', gene: block.value, mutated: block.mutated !== false };
    case 'one_per_patient': return { op: 'one_per_patient', rule: 'priority' };
    case 'dedupe': return { op: 'dedupe' };
    case 'any_of': return { op: 'any_of', table: 'clinical', conditions: [{ field: 'er_status', cmp: 'in', value: ['Positive'] }, { field: 'pr_status', cmp: 'in', value: ['Positive'] }] };
    case 'where': return { op: 'where', condition: { any: [{ layer: 'rna' }, { layer: 'protein' }] } };
    default: return null;
  }
}

// ---------------------------------------------------------------- inspector controls
/** ctx: { meta, cat, changed(), redraw(), steps } — every control mutates the step and calls changed(). */
export function controls(st, ctx) {
  const { meta } = ctx;
  const changed = () => ctx.changed();
  const chips = (options, isOn, toggle, { small = false } = {}) => {
    const wrap = h('div', { class: 'chips' });
    const draw = () => put(wrap, options.map(o => h('button', { type: 'button', class: `chip-opt${small ? ' small' : ''}`, 'aria-pressed': String(isOn(o.value)),
      onclick: () => { toggle(o.value); draw(); changed(); } }, o.label, o.count !== undefined ? h('span', { class: 'n' }, fmt(o.count)) : null)));
    draw();
    return wrap;
  };
  const seg = (options, current, set) => {
    const wrap = h('div', { class: 'seg', role: 'group' });
    const draw = () => put(wrap, options.map(([v, label]) => h('button', { type: 'button', 'aria-pressed': String(current() === v),
      onclick: () => { set(v); draw(); changed(); } }, label)));
    draw();
    return wrap;
  };
  const num = (value, set, attrs = {}) => {
    const el = h('input', { type: 'number', class: 'wb-num', ...attrs });
    el.value = value ?? '';
    el.addEventListener('input', () => { set(el.value === '' ? '' : Number(el.value)); changed(); });
    return el;
  };
  const row = (label, ...ctl) => h('div', { class: 'cv-ctl' }, label ? h('span', { class: 'cv-l' }, label) : null, h('div', { class: 'cv-c' }, ...ctl));
  const fieldPicker = (table, field, onPick, tables = ['clinical', 'survival']) => {
    const sel = h('select', { class: 'cv-select', 'aria-label': 'Field' });
    const groups = {};
    meta.fields.filter(f => tables.includes(f.table)).forEach(f => { (groups[f.group] = groups[f.group] || []).push(f); });
    Object.entries(groups).forEach(([g, fs]) => sel.append(h('optgroup', { label: g }, fs.map(f => h('option', { value: `${f.table}|${f.field}` }, f.label)))));
    sel.value = `${table}|${field}`;
    sel.addEventListener('change', () => { const [t, f] = sel.value.split('|'); onPick(t, f); });
    return sel;
  };
  const layerOpts = (meta.advanced.layers || []).map(o => ({ value: o.value, label: LAYERS[o.value] || o.value, count: o.count }));

  function valueEditor(table, cond) {
    const f = fieldOf(meta, table, cond.field);
    if (!f) return h('p', { class: 'muted' }, 'This field is not in the release.');
    if (f.kind === 'event') return row('Condition', seg([['notnull', 'Is recorded'], ['is_event', 'Had the event']], () => cond.cmp, v => { cond.cmp = v; cond.value = null; }));
    if (f.kind === 'time') {
      let unit = 'years';
      const toUnit = d => (d === '' || d === null || d === undefined ? '' : unit === 'years' ? Math.round((d / 365.25) * 10) / 10 : d);
      const box = h('span');
      const draw = () => box.replaceChildren(num(toUnit(cond.value), v => { cond.value = v === '' ? '' : unit === 'years' ? Math.round(v * 365.25) : v; }, { min: 0, step: unit === 'years' ? 0.5 : 30 }));
      draw();
      const timeRow = row('Time', h('div', { class: 'cv-inline' }, box, seg([['years', 'years'], ['days', 'days']], () => unit, v => { unit = v; draw(); })));
      timeRow.hidden = cond.cmp === 'notnull';
      return h('div', {}, row('Condition', seg([['gte', 'At least'], ['lte', 'At most'], ['notnull', 'Is recorded']], () => cond.cmp, v => {
        cond.cmp = v;
        if (v === 'notnull') cond.value = null; else if (typeof cond.value !== 'number') { cond.value = 1826; draw(); }
        timeRow.hidden = v === 'notnull';
      })), timeRow);
    }
    if (f.kind === 'numeric') {
      const r = f.range || ['', ''];
      const box = h('div', { class: 'cv-inline' });
      const draw = () => {
        if (cond.cmp === 'notnull') box.replaceChildren(h('span', { class: 'muted' }, `Any recorded value (${r[0]}–${r[1]})`));
        else if (cond.cmp === 'between') {
          if (!Array.isArray(cond.value)) cond.value = [r[0], r[1]];
          box.replaceChildren(num(cond.value[0], v => { cond.value[0] = v; }), h('span', { class: 'muted' }, 'to'), num(cond.value[1], v => { cond.value[1] = v; }));
        } else {
          if (Array.isArray(cond.value)) cond.value = cond.value[0];
          if (cond.value === null || cond.value === undefined) cond.value = r[0];
          box.replaceChildren(num(cond.value, v => { cond.value = v; }), h('span', { class: 'muted' }, `range ${r[0]}–${r[1]}`));
        }
      };
      draw();
      return h('div', {}, row('Condition', seg([['gte', 'At least'], ['lte', 'At most'], ['between', 'Between'], ['notnull', 'Recorded']], () => cond.cmp,
        v => { cond.cmp = v; if (v === 'notnull') cond.value = null; draw(); })), row('Value', box));
    }
    if (f.kind === 'stage') {
      const groups = ['I', 'II', 'III', 'IV'].map(g => ({ value: g, label: `Stage ${g}`, count: f.values.filter(v => new RegExp(`^Stage ${g}[ABC]?$`).test(v.value)).reduce((a, v) => a + v.count, 0) }));
      const exact = f.values.map(v => ({ value: v.value, label: v.value, count: v.count }));
      const box = h('div');
      const draw = () => box.replaceChildren(cond.cmp === 'stage_in'
        ? chips(groups, v => (cond.value || []).includes(v), v => { cond.value = toggleIn(cond.value, v); })
        : chips(exact, v => (cond.value || []).includes(v), v => { cond.value = toggleIn(cond.value, v); }, { small: true }));
      draw();
      return h('div', {}, row('Pick', seg([['stage_in', 'Stage groups'], ['in', 'Exact sub-stages']], () => cond.cmp, v => { cond.cmp = v; cond.value = v === 'stage_in' ? ['II'] : []; draw(); })), row('Stages', box));
    }
    if (cond.cmp === 'contains') {
      const t = h('input', { class: 'cv-text', value: valueText(cond.value), 'aria-label': 'Text the value contains' });
      t.addEventListener('input', () => { cond.value = t.value; changed(); });
      return row('Contains', t);
    }
    if (['in', 'not_in', 'in_raw'].includes(cond.cmp) && !Array.isArray(cond.value)) cond.value = cond.value === null || cond.value === undefined ? [] : [cond.value];
    const opts = f.values.map(v => ({ value: v.value, label: v.value.length > 42 ? `${v.value.split(' (')[0]}` : v.value, count: v.count }));
    const values = row('Values', chips(opts, v => (cond.value || []).includes(v), v => { cond.value = toggleIn(cond.value, v); }, { small: opts.length > 8 }));
    values.hidden = cond.cmp === 'notnull';
    return h('div', {}, row('Condition', seg([['in', 'Is one of'], ['not_in', 'Is not'], ['notnull', 'Is recorded']], () => (cond.cmp === 'in_raw' ? 'in' : cond.cmp), v => {
      if (v === 'notnull') cond.value = null; else if (!Array.isArray(cond.value)) cond.value = [];
      cond.cmp = v;
      values.hidden = v === 'notnull';
    })), values);
  }

  switch (st.op) {
    case 'sample_type':
      return row('Sample types', chips(SAMPLE_TYPES.map(t => ({ value: t, label: t, count: (meta.advanced.sample_types.find(x => x.value === t) || {}).count })),
        v => (st.values || []).includes(v), v => { st.values = toggleIn(st.values, v); }));
    case 'layer': case 'exclude_layer':
      return h('div', {}, row('Rule', seg([['layer', 'Must have'], ['exclude_layer', 'Must not have']], () => st.op, v => { st.op = v; ctx.redraw(); })),
        row('Data layer', chips(layerOpts, v => st.layer === v, v => { st.layer = v; })));
    case 'mutation': {
      const box = h('input', { class: 'cv-text', list: 'cv-genes', value: st.gene || '', 'aria-label': 'Gene', spellcheck: 'false' });
      box.addEventListener('input', () => { st.gene = box.value.trim().toUpperCase(); changed(); });
      return h('div', {}, row('Gene', box, h('datalist', { id: 'cv-genes' }, (meta.advanced.genes || []).map(g => h('option', { value: g.value }, `${g.count} mutated`)))),
        row('Call', seg([[true, 'Mutated'], [false, 'Not mutated']], () => st.mutated !== false, v => { st.mutated = v; })),
        h('p', { class: 'cv-hint' }, ...rich('Somatic MC3 calls. Samples without mutation data are dropped either way.')));
    }
    case 'filter':
      return h('div', {}, row('Field', fieldPicker(st.table, st.field, (t, f) => { Object.assign(st, { table: t }, defaultCondition(meta, t, f)); ctx.redraw(); changed(); })),
        valueEditor(st.table, st));
    case 'any_of': {
      const list = h('div', { class: 'cv-conds' });
      const draw = () => {
        list.replaceChildren(...(st.conditions || []).map((c, i) => h('div', { class: 'cv-cond' },
          h('div', { class: 'cv-cond-head' }, h('span', { class: 'label' }, i === 0 ? 'Keep samples where' : 'or'),
            st.conditions.length > 2 ? h('button', { type: 'button', class: 'icon-btn', title: 'Remove this condition', onclick: () => { st.conditions.splice(i, 1); draw(); changed(); } }, ic('x')) : null),
          row('Field', fieldPicker(st.table, c.field, (t, f) => { st.conditions[i] = defaultCondition(meta, st.table, f); draw(); changed(); }, [st.table])),
          valueEditor(st.table, c))));
        paintIcons();
      };
      draw();
      return h('div', {}, list, h('button', { type: 'button', class: 'btn small ghost', onclick: () => { st.conditions.push(defaultCondition(meta, st.table, 'pr_status')); draw(); changed(); } }, ic('plus'), 'Add an “or” condition'));
    }
    case 'join':
      if (st.table === 'slides') {
        return h('div', {},
          row('Slide kinds', chips([{ value: '*', label: 'Any kind' }, ...SLIDE_KINDS.map(([v, l]) => ({ value: v, label: l, count: kindCount(ctx.cat, v) }))],
            v => (v === '*' ? !(st.slide_kinds || []).length : (st.slide_kinds || []).includes(v)),
            v => { st.slide_kinds = v === '*' ? [] : toggleIn(st.slide_kinds, v); }, { small: true })),
          row('Rows', seg([['rows', 'One row per slide'], ['exists', 'Once per sample']], () => st.mode || 'rows', v => { st.mode = v; })),
          h('p', { class: 'cv-hint' }, ...rich('One row per slide counts a sample once for every slide it has (the double counting); once per sample keeps each sample a single time.')));
      }
      if (st.table === 'normals') {
        return h('div', {}, row('Meaning', seg([[false, 'Patient also has one'], [true, 'Tumour–normal pairs']], () => !!st.add_to_cohort, v => { st.add_to_cohort = v; })),
          row('Normal must have', chips(layerOpts.map(o => ({ value: o.value, label: o.label })), v => (st.require_layers || []).includes(v),
            v => { st.require_layers = toggleIn(st.require_layers, v); }, { small: true })),
          row('Rows', seg([['rows', 'One row per match'], ['exists', 'Once per sample']], () => st.mode || 'rows', v => { st.mode = v; })));
      }
      return h('div', {}, row('Match on', seg([['sample_id', 'Sample barcode'], ['patient_id', 'Patient barcode']], () => st.key || 'sample_id', v => { st.key = v; })),
        row('Rows', seg([['rows', 'Bring the record'], ['exists', 'Only require one']], () => st.mode || 'rows', v => { st.mode = v; })),
        h('p', { class: 'cv-hint' }, ...rich('Matching on the patient barcode also matches the patient’s other samples (for example normal tissue), so samples can be counted twice.')));
    case 'one_per_patient': {
      if (!st.rule) st.rule = 'priority';
      const hasDays = !!fieldOf(meta, 'clinical', 'collection_day');
      const DEFAULT = ['Primary Tumor', 'Metastatic', 'Solid Tissue Normal'];
      const detail = h('div');
      const draw = () => {
        if (st.rule === 'priority') {
          detail.replaceChildren(row('Keep first', seg([['Primary Tumor', 'Primary tumour'], ['Metastatic', 'Metastasis'], ['Solid Tissue Normal', 'Normal tissue']],
            () => (st.order || DEFAULT)[0], v => { if (v === 'Primary Tumor') delete st.order; else st.order = [v, ...DEFAULT.filter(t => t !== v)]; })),
          h('p', { class: 'cv-hint' }, 'Ties go to the lowest barcode. Every choice is listed in sample_selection.csv.'));
        } else {
          delete st.order;
          detail.replaceChildren(h('p', { class: 'cv-hint' }, ...rich('Uses the sample collection day (days_to_collection). Patients with no day are chosen by sample type.')));
        }
      };
      draw();
      return h('div', {}, row('Choose', seg([['priority', 'By sample type'], ...(hasDays ? [['earliest', 'Earliest'], ['latest', 'Latest']] : [])], () => st.rule, v => { st.rule = v; draw(); })), detail);
    }
    case 'where':
      return h('div', {}, condBuilder(st, ctx, { chips, seg, row, valueEditor, fieldPicker, layerOpts }),
        ctx.steps.some(s => s.op === 'join' && s.table === 'normals' && s.add_to_cohort && !s._off) || st.apply_to
          ? row('Check it on', seg([['sample', 'Each sample'], ['both', 'Both of a pair'], ['added_normals', 'The added normal']],
            () => st.apply_to || 'sample', v => { if (v === 'sample') delete st.apply_to; else st.apply_to = v; })) : null);
    case 'dedupe':
      return h('p', { class: 'cv-hint' }, ...rich('Keeps one row for each sample, so a join that added a row per slide or per match no longer counts samples twice.'));
    default:
      return h('p', { class: 'muted' }, 'Nothing to set for this step.');
  }
}
function kindCount(cat, kind) {
  const t = cat && cat.tables.find(x => x.id === 'slides');
  const k = t && t.kinds.find(x => x.value === kind);
  return k ? k.count : undefined;
}

// ---------------------------------------------------------------- the combined-condition builder (AND / OR / NOT)
const TEST_KINDS = [['layer', 'Has a data layer'], ['sample_type', 'Sample type'], ['gene', 'Gene mutation'], ['slide_kinds', 'Has a slide'],
  ['clinical', 'Clinical field'], ['survival', 'Survival field']];
function kindOf(t) {
  if (t.layer) return 'layer';
  if (t.sample_type) return 'sample_type';
  if (t.gene) return 'gene';
  if (t.slide_kinds) return 'slide_kinds';
  if (t.table) return t.table;
  return 'layer';
}
function newTest(meta, kind) {
  switch (kind) {
    case 'layer': return { layer: 'rna' };
    case 'sample_type': return { sample_type: ['Primary Tumor'] };
    case 'gene': return { gene: 'TP53', mutated: true };
    case 'slide_kinds': return { slide_kinds: ['diagnostic (FFPE)'] };
    case 'survival': return { table: 'survival', ...defaultCondition(meta, 'survival', 'pfi') };
    default: return { table: 'clinical', ...defaultCondition(meta, 'clinical', 'er_status') };
  }
}
function condBuilder(st, ctx, ui) {
  const { meta } = ctx;
  const host = h('div', { class: 'cv-cb' });
  if (!st.condition || !(st.condition.all || st.condition.any)) st.condition = { all: [st.condition || { layer: 'rna' }] };
  const draw = () => { put(host, group(st.condition, null, 0)); paintIcons(); };
  const changed = () => { ctx.changed(); };
  function group(node, remove, depth) {
    const key = node.all ? 'all' : 'any';
    const kids = node[key];
    const head = h('div', { class: 'cv-cb-head' },
      h('div', { class: 'seg' }, [['all', 'All of (AND)'], ['any', 'Any of (OR)']].map(([k, label]) => h('button', { type: 'button', 'aria-pressed': String(k === key),
        onclick: () => { if (k !== key) { node[k] = node[key]; delete node[key]; draw(); changed(); } } }, label))),
      remove ? h('button', { type: 'button', class: 'icon-btn', title: 'Remove this group', 'aria-label': 'Remove this group',
        onclick: () => { remove(); draw(); changed(); } }, ic('x')) : null);
    const list = h('div', { class: 'cv-cb-list' }, kids.map((k, i) => item(k, kids, i, depth)));
    const add = h('div', { class: 'cv-cb-add' },
      h('button', { type: 'button', class: 'btn small ghost', onclick: () => { kids.push(newTest(meta, 'layer')); draw(); changed(); } }, ic('plus'), 'Test'),
      depth < 3 ? h('button', { type: 'button', class: 'btn small ghost', onclick: () => { kids.push({ any: [newTest(meta, 'layer')] }); draw(); changed(); } }, ic('brackets'), 'Group') : null);
    return h('div', { class: `cv-cb-group d${depth} ${key}` }, head, list, add);
  }
  function item(node, list, i, depth) {
    const neg = !!node.not;
    const inner = neg ? node.not : node;
    const set = v => { list[i] = neg ? { not: v } : v; };
    const notBtn = h('button', { type: 'button', class: `cv-cb-not${neg ? ' on' : ''}`, 'aria-pressed': String(neg), title: 'NOT: keep samples where this is false',
      onclick: () => { list[i] = neg ? node.not : { not: node }; draw(); changed(); } }, 'NOT');
    if (inner.all || inner.any) return h('div', { class: 'cv-cb-item' }, notBtn, group(inner, () => list.splice(i, 1), depth + 1));
    const kind = kindOf(inner);
    const kindSel = h('select', { class: 'cv-select small', 'aria-label': 'What to test' }, TEST_KINDS.map(([v, l]) => h('option', { value: v }, l)));
    kindSel.value = kind;
    kindSel.addEventListener('change', () => { set(newTest(meta, kindSel.value)); draw(); changed(); });
    let body;
    if (kind === 'layer') body = ui.chips(ui.layerOpts, v => inner.layer === v, v => { inner.layer = v; }, { small: true });
    else if (kind === 'sample_type') body = ui.chips(SAMPLE_TYPES.map(t => ({ value: t, label: t })), v => [].concat(inner.sample_type).includes(v), v => { inner.sample_type = toggleIn([].concat(inner.sample_type), v); }, { small: true });
    else if (kind === 'gene') {
      const g = h('input', { class: 'cv-text', list: 'cv-genes', value: inner.gene || '', 'aria-label': 'Gene', spellcheck: 'false' });
      g.addEventListener('input', () => { inner.gene = g.value.trim().toUpperCase(); changed(); });
      body = h('div', { class: 'cv-inline' }, g, ui.seg([[true, 'Mutated'], [false, 'Not']], () => inner.mutated !== false, v => { inner.mutated = v; }));
    } else if (kind === 'slide_kinds') {
      body = ui.chips([{ value: '*', label: 'Any' }, ...SLIDE_KINDS.map(([v, , s]) => ({ value: v, label: s }))],
        v => (v === '*' ? !(inner.slide_kinds || []).length : (inner.slide_kinds || []).includes(v)),
        v => { inner.slide_kinds = v === '*' ? [] : toggleIn(inner.slide_kinds, v); }, { small: true });
    } else {
      body = h('div', {}, ui.fieldPicker(inner.table, inner.field, (t, f) => { set({ table: t, ...defaultCondition(meta, t, f) }); draw(); changed(); }, [inner.table]),
        ui.valueEditor(inner.table, inner));
    }
    return h('div', { class: 'cv-cb-item' }, notBtn, h('div', { class: 'cv-cb-test' },
      h('div', { class: 'cv-cb-test-head' }, kindSel,
        h('button', { type: 'button', class: 'icon-btn', title: 'Remove this test', 'aria-label': 'Remove this test', disabled: list.length < 2 && depth === 0,
          onclick: () => { list.splice(i, 1); draw(); changed(); } }, ic('x'))),
      body));
  }
  draw();
  return h('div', {}, h('p', { class: 'cv-hint' }, ...rich('A value that is not recorded never passes. NOT flips a test; groups combine tests with AND or OR.')), host);
}
