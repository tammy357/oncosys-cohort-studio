// Shared by the Build page (app.js) and the Compare page (compare.js).
// GSAP and Lucide are optional (pinned CDN builds): everything works without them.

export const $ = (sel, el = document) => el.querySelector(sel);
export const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
export const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
export const G = () => (!reduceMotion && window.gsap ? window.gsap : null);
const NS = 'http://www.w3.org/2000/svg';

export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
export function s(tag, attrs = {}) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}
export const ic = (name, cls = '') => h('i', { 'data-lucide': name, class: cls });
let iconQueued = false;
export function paintIcons() {
  if (iconQueued) return;
  iconQueued = true;
  requestAnimationFrame(() => {
    iconQueued = false;
    if (window.lucide) window.lucide.createIcons({ attrs: { 'stroke-width': 1.7 } });
  });
}

export const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};
export const session = { user: store.get('cs-user', 'researcher') };

export async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(path, { method, headers: { 'content-type': 'application/json', 'x-user': session.user },
    body: body === undefined ? undefined : JSON.stringify(body) });
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    const msg = data && (data.error || (Array.isArray(data.detail) ? data.detail.map(d => d.msg).join('; ') : data.detail));
    throw new Error(msg || `Request failed (${res.status})`);
  }
  return data;
}

/**
 * /api/meta, patiently. A free-tier server that went to sleep takes about a minute to wake, so after a short wait a
 * "waking up" note appears, and failed attempts (a 502/504 while it starts) are retried before giving up.
 */
export async function loadMeta() {
  const note = h('div', { class: 'waking', role: 'status', hidden: true }, h('span', { class: 'waking-dot' }),
    h('span', {}, h('b', {}, 'Waking up the server. '), 'Free hosting sleeps when nobody has used it for a while; this can take up to a minute.'));
  document.body.append(note);
  const slow = setTimeout(() => { note.hidden = false; }, 2500);
  try {
    for (let attempt = 1; ; attempt++) {
      try { return await api('/api/meta'); }
      catch (e) {
        if (attempt >= 5) throw e;
        note.hidden = false;
        await new Promise(r => setTimeout(r, 3000 * attempt));
      }
    }
  } finally { clearTimeout(slow); note.remove(); }
}

export const fmt = n => (n === null || n === undefined ? '–' : Number(n).toLocaleString());
export const pct = n => (n === null || n === undefined ? '–' : `${Number(n).toFixed(1).replace(/\.0$/, '')}%`);
export const clock = () => new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
export const modeLabel = m => ({ ai: 'AI assistant', rules: 'Built-in parser', advanced: 'Advanced search', favourite: 'Favourite' }[m] || m || '');
export const verdictLabel = v => h('span', { class: `verdict ${String(v || '').split(' ')[0]}` }, v || '–');
export function timeAgo(iso) {
  if (!iso) return '';
  const sec = (Date.now() - new Date(iso).getTime()) / 1000;
  if (sec < 60) return 'just now';
  if (sec < 3600) return `${Math.floor(sec / 60)} min ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

// ---------------------------------------------------------------- technical terms: bold, with a plain definition
const GLOSSARY = [
  // [pattern, case-sensitive?, definition]
  ['PAM50', true, 'An RNA-based test that sorts breast tumours into five intrinsic subtypes: Luminal A, Luminal B, HER2-enriched, Basal-like and Normal-like.'],
  ['Luminal A|LumA', false, 'PAM50 subtype: usually hormone-receptor positive, slow-growing.'],
  ['Luminal B|LumB', false, 'PAM50 subtype: hormone-receptor positive, faster-growing than Luminal A.'],
  ['Basal-like|Basal', false, 'PAM50 subtype: mostly triple-negative, often TP53-mutated.'],
  ['HER2-enriched', false, 'PAM50 subtype driven by HER2 (ERBB2) amplification.'],
  ['Normal-like', false, 'PAM50 subtype that resembles normal breast tissue.'],
  ['IHC', true, 'Immunohistochemistry: staining tissue with antibodies to see whether a protein such as ER, PR or HER2 is present.'],
  ['ER', true, 'Oestrogen receptor, measured by IHC in the clinical record.'],
  ['PR', true, 'Progesterone receptor, measured by IHC in the clinical record.'],
  ['HER2', true, 'A growth-factor receptor (gene ERBB2); clinical status from IHC or in situ hybridisation.'],
  ['hormone[- ]receptor[- ]positive|HR\\+', false, 'ER-positive or PR-positive (the usual definition).'],
  ['triple[- ]negative', false, 'ER-, PR- and HER2-negative.'],
  ['RPPA', true, 'Reverse-phase protein array: the protein data layer.'],
  ['RNA expression|RNA-seq', false, 'Gene expression measured by RNA sequencing.'],
  ['copy[- ]number', false, 'Gains and losses of DNA, here GISTIC2 thresholded calls per gene.'],
  ['MC3', true, 'The TCGA multi-centre mutation calling project: gene-level somatic mutation calls.'],
  ['somatic', false, 'Arising in the tumour, not inherited.'],
  ['germline', false, 'Inherited variants. Not available in this release.'],
  ['primary tumou?rs?', false, 'The original tumour sample (TCGA sample code 01).'],
  ['Solid Tissue Normal|normal[- ]tissue', false, 'Healthy tissue taken near the tumour (TCGA sample code 11).'],
  ['metastatic', false, 'A sample from a spread of the cancer (TCGA sample code 06).'],
  ['diagnostic \\(FFPE\\)|FFPE', false, 'Formalin-fixed, paraffin-embedded diagnostic slide: the standard for histology.'],
  ['frozen[- ]section', false, 'A slide cut from frozen tissue at surgery: quicker, lower quality than FFPE.'],
  ['sample_id', true, 'The 15-character TCGA sample barcode, for example TCGA-A7-A0CD-01.'],
  ['patient_id', true, 'The 12-character TCGA patient barcode.'],
  ['joins?', false, 'Combining two tables on a shared key. If the other table has several matches, one sample becomes several rows.'],
  ['counted twice|counts? samples more than once|double[- ]count(?:ing|ed|s)?', false, 'A join produced more rows than distinct samples, so some samples would be counted more than once.'],
  ['TVD|total variation distance', false, 'How much a group mix changed: 0 = same mix, 1 = no overlap. 0.10 or more is flagged.'],
  ['submitting site|tissue source site', false, 'The hospital that contributed the sample, read from the barcode.'],
  ['PFI|progression[- ]free interval', false, 'Progression-free interval (TCGA-CDR): time until the cancer progressed. The recommended endpoint for BRCA.'],
  ['OS|overall survival', false, 'Overall survival: time until death from any cause.'],
  ['DSS|disease[- ]specific survival', false, 'Time until death from the cancer.'],
  ['DFI|disease[- ]free interval', false, 'Time until the cancer returned, for patients who were disease-free after treatment.'],
  ['TCGA-CDR|Clinical Data Resource', false, 'The curated TCGA Pan-Cancer Clinical Data Resource (Liu et al., Cell 2018).'],
  ['pathologic stage|AJCC', false, 'Cancer stage from the pathology report, using the AJCC system.'],
  ['pathologic_N|lymph[- ]node', false, 'Whether the cancer reached the lymph nodes (AJCC N category).'],
  ['neoadjuvant', false, 'Treatment given before surgery.'],
  ['Jaccard', false, 'Shared samples divided by all samples in either cohort: 1 = identical, 0 = no overlap.'],
  ['Kaplan[- ]Meier', false, 'An estimate of the share of patients without the event over time. Descriptive and unadjusted here.'],
  ['log-rank', false, 'A test of whether two survival curves differ. Valid only when the cohorts share no patients.'],
  ['Insufficient Evidence', false, 'A fact the cohort depends on can\'t be confirmed, for example unused words or a missing file.'],
  ['frozen release|frozen files', false, 'The fixed snapshot of TCGA-BRCA used at the event; checksums prove the files have not changed.'],
  ['checksums?', false, 'A fingerprint of a file. If it matches, the file is the exact one the event froze.'],
];
const SEP = '(?<![A-Za-z0-9_+-])';
const END = '(?![A-Za-z0-9_])';
const CS = GLOSSARY.filter(g => g[1]);
const CI = GLOSSARY.filter(g => !g[1]);
const csRe = new RegExp(CS.map(g => `${SEP}(?:${g[0]})${END}`).join('|'), 'g');
const ciRe = new RegExp(CI.map(g => `${SEP}(?:${g[0]})${END}`).join('|'), 'gi');
const defOf = (word, list, flags) => {
  const hit = list.find(g => new RegExp(`^(?:${g[0]})$`, flags).test(word));
  return hit ? hit[2] : '';
};

function splitTerms(text, re, list, flags) {
  const out = [];
  let last = 0;
  re.lastIndex = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(h('b', { class: 'term', 'data-def': defOf(m[0], list, flags) }, m[0]));
    last = m.index + m[0].length;
    if (m[0].length === 0) re.lastIndex += 1;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** Text with technical terms in bold (each carries a short definition shown on hover). */
export function rich(text) {
  if (text === null || text === undefined) return '';
  const parts = splitTerms(String(text), csRe, CS, '');
  return parts.flatMap(p => (typeof p === 'string' ? splitTerms(p, ciRe, CI, 'i') : [p]));
}

/**
 * One tooltip for the whole page. Two kinds of element get one:
 *   .term[data-def]   a technical term: its name in bold, then the definition
 *   [data-tip]        anything else: the text (lines split on \n), with an optional bold data-tip-title
 * Shown on hover and on keyboard focus, so details can live here instead of on the page.
 */
const TIP_SEL = '.term, [data-tip]';
export function initTooltips() {
  const tip = h('div', { class: 'tip', role: 'tooltip', id: 'cs-tip', hidden: true });
  document.body.append(tip);
  let current = null;
  const show = el => {
    const def = el.dataset.def;
    const text = el.dataset.tip;
    if (!def && !text) return;
    current = el;
    if (def) tip.replaceChildren(h('b', {}, el.textContent), ' ', def);
    else {
      const lines = String(text).split('\n').filter(Boolean);
      tip.replaceChildren(...[el.dataset.tipTitle ? h('b', { class: 'tip-title' }, el.dataset.tipTitle) : null,
        ...lines.map(l => h('div', { class: 'tip-line' }, rich(l)))].filter(Boolean));
    }
    tip.hidden = false;
    const r = el.getBoundingClientRect();
    const tw = Math.min(320, window.innerWidth - 24);
    tip.style.width = `${tw}px`;
    const left = Math.max(12, Math.min(window.innerWidth - tw - 12, r.left + r.width / 2 - tw / 2));
    tip.style.left = `${left}px`;
    const th = tip.offsetHeight;
    const above = r.top - th - 10;
    tip.style.top = `${above > 8 ? above : r.bottom + 10}px`;
    tip.classList.toggle('below', above <= 8);
  };
  const hide = () => { tip.hidden = true; current = null; };
  document.addEventListener('pointerover', e => { const t = e.target.closest(TIP_SEL); if (t && t !== current) show(t); });
  document.addEventListener('pointerout', e => { const t = e.target.closest(TIP_SEL); if (t && !t.contains(e.relatedTarget)) hide(); });
  document.addEventListener('focusin', e => { const t = e.target.closest && e.target.closest('[data-tip]'); if (t) show(t); });
  document.addEventListener('focusout', hide);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') hide(); });
  window.addEventListener('scroll', hide, { passive: true, capture: true });
}
/** Give an element a tooltip (and an accessible description). */
export function tipped(el, text, title) {
  if (!text) return el;
  el.dataset.tip = text;
  if (title) el.dataset.tipTitle = title;
  if (!el.getAttribute('aria-label') && !el.textContent.trim()) el.setAttribute('aria-label', title || text.split('\n')[0]);
  return el;
}

// ---------------------------------------------------------------- motion helpers
export function countTo(el, target, duration = 1.1, format = fmt) {
  const g = G();
  if (!g || !Number.isFinite(target)) { el.textContent = format(target); return; }
  const o = { v: 0 };
  g.to(o, { v: target, duration, ease: 'power2.out', onUpdate: () => { el.textContent = format(target % 1 ? Math.round(o.v * 10) / 10 : Math.round(o.v)); } });
}
export function enter(el, delay = 0) {
  const g = G();
  if (g) g.from(el, { y: 16, autoAlpha: 0, duration: 0.6, delay, ease: 'power3.out', clearProps: 'transform,opacity,visibility' });
}
/**
 * A short message at the bottom of the screen. kind: 'ok' | 'warn' | 'bad'. Warnings and errors stay longer.
 * The same message is never stacked twice.
 */
export function toast(text, icon = 'check', kind = 'ok') {
  const host = $('#toasts');
  if (!host) return;
  if ($$('.toast', host).some(x => x.dataset.text === text)) return;
  const t = h('div', { class: `toast ${kind}`, role: kind === 'bad' ? 'alert' : 'status', 'data-text': text }, ic(icon), h('span', {}, text));
  host.append(t);
  while (host.children.length > 3) host.firstElementChild.remove();
  paintIcons();
  const g = G();
  if (g) g.from(t, { y: 14, autoAlpha: 0, duration: 0.35, ease: 'back.out(1.6)' });
  setTimeout(() => {
    if (g) g.to(t, { y: 8, autoAlpha: 0, duration: 0.25, onComplete: () => t.remove() });
    else t.remove();
  }, kind === 'ok' ? 2600 : 5200);
}
/** Remove toasts that no longer apply (for example a warning about a problem that was just fixed). */
export function clearToasts(kind = 'warn') {
  $$(`#toasts .toast.${kind}`).forEach(t => t.remove());
}
export const warnToast = text => toast(text, 'triangle-alert', 'warn');
export const errorToast = text => toast(text, 'circle-alert', 'bad');

// ---------------------------------------------------------------- shared chrome: theme, user, model status
export function initChrome({ onTheme, onUser } = {}) {
  const params = new URLSearchParams(location.search);
  const setTheme = theme => {
    document.documentElement.dataset.theme = theme;
    store.set('cs-theme', theme);
    const btn = $('#theme-toggle');
    btn.replaceChildren(ic(theme === 'dark' ? 'sun' : 'moon'));
    btn.title = theme === 'dark' ? 'Switch to the light theme' : 'Switch to the dark theme';
    paintIcons();
    if (onTheme) onTheme(theme);
  };
  $('#theme-toggle').addEventListener('click', () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));
  setTheme(params.get('theme') || document.documentElement.dataset.theme || 'light');
  const userInput = $('#user');
  userInput.value = session.user;
  userInput.addEventListener('change', () => {
    session.user = userInput.value.trim().replace(/[^A-Za-z0-9_.@-]/g, '') || 'researcher';
    userInput.value = session.user;
    store.set('cs-user', session.user);
    toast(`Showing ${session.user}'s cohorts`, 'user-round');
    if (onUser) onUser(session.user);
  });
  window.addEventListener('scroll', () => $('#topbar').classList.toggle('scrolled', window.scrollY > 8), { passive: true });
  initTooltips();
}
export function showModelStatus(ai) {
  const status = $('#ai-status');
  status.classList.add(ai.available ? 'on' : 'off');
  $('#ai-model').textContent = ai.available ? `${ai.model}${ai.where === 'local endpoint' ? ' · local' : ''}` : 'off';
  status.title = ai.available ? `Model calls this run: ${ai.calls} of ${ai.call_limit}` : ai.reason;
}
