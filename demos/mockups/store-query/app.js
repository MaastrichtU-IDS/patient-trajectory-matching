'use strict';
/* Store query: build a trajectory query on the MIMIC-IV demo store's item classes and run it in the
   execution store on the server (../store_query.py). The server answers with aggregate counts only;
   this page never sees a patient identifier, time or value. Thresholds are kept and sent as the
   strings typed, never as numbers, so they never pass through a float. */

const API = '../api/store/';
const STATUS_ORDER = ['exact', 'possible', 'relaxed', 'unresolved', 'none', 'not_comparable', 'excluded'];
const STATUS = {
  exact: ['Exact', 'Some choice of the patient\'s events meets every step and constraint, for every time the records allow.'],
  possible: ['Possible', 'A match holds for some times inside the recorded ranges but not all. Rare here: MIMIC times are exact.'],
  relaxed: ['Relaxed', 'No match as asked, but a declared relaxation gives one, at the cost shown.'],
  unresolved: ['Unresolved', 'Not decided. Here usually because an excluded row could have given an unrelaxed match, so a relaxed match is only noted beside it; or a search stopped at its budget.'],
  none: ['No recorded match', 'Every search finished, nothing matched, and no excluded row or gap in coverage could have changed that.'],
  not_comparable: ['Not comparable', 'The store cannot say "no match": rows it left out could hold one, or no source covering the period records the step.'],
  excluded: ['No index period', 'The period could not be placed for this patient.'],
};
const REASONS = {
  ADMISSION_EXCLUDED_RECORDS: 'rows the store left out (excluded records) could have matched or moved a first or last step (D18)',
  NO_SOURCE_COVERAGE: 'no source that records the step covers the patient\'s period',
  NO_SOURCE_RECORDS_CONCEPT: 'no source records the step at all, for example in the unit asked for',
  SEARCH_BUDGET_EXHAUSTED: 'the search stopped at its budget before deciding',
  EXCLUDED_RECORDS_MAY_MAKE_EXACT: 'excluded rows could make the match exact',
  EXACT_SEARCH_STOPPED: 'the search for an exact match stopped at its budget',
  CHEAPER_RELAXATION_SEARCH_STOPPED: 'a cheaper relaxation may exist; its search stopped',
  RELAXATION_SEARCH_STOPPED: 'no match, but a relaxed search stopped at its budget before deciding',
  OCCURRENCE_ORDER_UNCERTAIN: 'the records leave it uncertain which event is the first or last, and the match depends on it (D30)',
  RELAXATION_ORDER_UNCERTAIN: 'no unrelaxed match; a relaxed match was left undecided because the first or last event is uncertain (D30)',
  CHEAPER_RELAXATION_ORDER_UNCERTAIN: 'a cheaper relaxation was left undecided because the first or last event is uncertain (D30)',
};
// Refusals a valid-looking query can still meet, in plain words; anything else is shown by its code.
const REFUSALS = {
  VALUE_RELAXATION_MUST_WIDEN: 'A relaxed threshold must admit every value the original threshold admits: lower a lower bound, raise an upper one.',
  UNIT_NOT_RECORDED_FOR_THIS_ITEM: 'That unit is not recorded for this item in the store.',
  DURING_NEEDS_AN_INTERVAL_STEP: '"Happens while" needs an infusion item as its first step.',
  EXTEND_MAX_MUST_WIDEN: 'The relaxed latest gap must be larger than the latest gap.',
  THRESHOLD_NOT_A_PLAIN_DECIMAL: 'Thresholds must be plain decimals such as 65 or 37.5.',
};
const OPS = {lower: [['', 'no lower bound'], ['ge', 'at least (≥)'], ['gt', 'above (>)']],
             upper: [['', 'no upper bound'], ['le', 'at most (≤)'], ['lt', 'below (<)']]};
const SYMBOL = {ge: '≥', gt: '>', le: '≤', lt: '<'};
const TIME_UNITS = ['minute', 'hour', 'day'];
const DECIMAL = /^-?(0|[1-9][0-9]*)(\.[0-9]+)?$/;
const AMOUNT = /^-?[0-9]+(\.[0-9]+)?$/;
const IDS = 'ABCDEF'.split('');
const I = 'https://example.org/trajectory/mimic-record-item/';
const G = 'https://example.org/trajectory/mimic-record-item-category/';

const state = {status: null, catalogue: null, byConcept: new Map(), def: null, result: null, pending: false, error: null, filters: {}};
const $ = sel => document.querySelector(sel);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const fmt = n => Number(n).toLocaleString('en-GB');
const plural = (n, word) => `${fmt(n)} ${word}${n === 1 ? '' : 's'}`;

/* Examples: item classes named by itemid; each is checked against the catalogue before it is offered. */
const cond = (unit, lower, upper) => ({unit, lowerOp: lower ? lower[0] : '', lowerValue: lower ? lower[1] : '',
  upperOp: upper ? upper[0] : '', upperValue: upper ? upper[1] : ''});
const EXAMPLES = [
  {title: 'Low blood pressure, then norepinephrine within 6 hours',
   text: 'Non-invasive mean blood pressure below 65 mmHg, then a norepinephrine infusion starting 0 to 6 hours later, in the first ICU stay. Relaxations: the threshold to below 70, or any item under Medications instead of norepinephrine.',
   def: {window: 'first_stay', scope: 'within', budget: '1',
     steps: [{id: 'A', concept: I + '220181', occurrence: 'any', cond: cond('mmHg', null, ['lt', '65']), widenValue: {on: true, lowerValue: '', upperValue: '70', cost: '0.5'}},
             {id: 'B', concept: I + '221906', occurrence: 'any', widenConcept: {on: true, cost: '0.5'}}],
     relations: [{from: 'A', to: 'B', kind: 'after', min: '0', max: '6', unit: 'hour'}]}},
  {title: 'Heart rate above 120 while on norepinephrine',
   text: 'A heart rate above 120 bpm while the first norepinephrine infusion of the first ICU stay is running. Relaxation: the threshold to above 110.',
   def: {window: 'first_stay', scope: 'within', budget: '1',
     steps: [{id: 'A', concept: I + '221906', occurrence: 'first'},
             {id: 'B', concept: I + '220045', occurrence: 'any', cond: cond('bpm', ['gt', '120'], null), widenValue: {on: true, lowerValue: '110', upperValue: '', cost: '0.5'}}],
     relations: [{from: 'A', to: 'B', kind: 'during', grace: '0', unit: 'hour'}]}},
  {title: 'First fever, then fast breathing within 4 hours',
   text: 'The first temperature of at least 100.4 °F in the first 24 hours of the first ICU stay, then a respiratory rate above 24 per minute 0 to 4 hours later. Relaxation: up to 8 hours.',
   def: {window: 'first_stay_24h', scope: 'within', budget: '1',
     steps: [{id: 'A', concept: I + '223761', occurrence: 'first', cond: cond('°F', ['ge', '100.4'], null)},
             {id: 'B', concept: I + '220210', occurrence: 'any', cond: cond('insp/min', ['gt', '24'], null)}],
     relations: [{from: 'A', to: 'B', kind: 'after', min: '0', max: '4', unit: 'hour', extend: {on: true, value: '8', cost: '0.25'}}]}},
];

/* The definition the builder edits; every field is a string as typed. */
function freshStep(id, concept) {
  const e = concept ? state.byConcept.get(concept) : null;
  return {id, concept: concept || '', occurrence: 'any', cond: cond(e?.units[0]?.unit || '', null, null),
          widenValue: {on: false, lowerValue: '', upperValue: '', cost: '0.5'}, widenConcept: {on: false, cost: '0.5'}};
}
function normalise(def) {
  const out = JSON.parse(JSON.stringify(def));
  out.steps = out.steps.map(s => {
    const base = freshStep(s.id, s.concept);
    return {...base, ...s, cond: {...base.cond, ...(s.cond || {})}, widenValue: {...base.widenValue, ...(s.widenValue || {})},
            widenConcept: {...base.widenConcept, ...(s.widenConcept || {})}};
  });
  out.relations = out.relations.map(r => ({min: '0', max: '', grace: '0', unit: 'hour', extend: {on: false, value: '', cost: '0.25'}, ...r,
    extend: {on: false, value: '', cost: '0.25', ...(r.extend || {})}}));
  return out;
}

/* Reading and checking */

const entry = concept => state.byConcept.get(concept);
const labelOf = concept => {
  const e = entry(concept);
  if (!e) return 'no item chosen';
  return e.kind === 'grouping' ? `any item in ${e.label}` : `${e.label} (${e.itemid})`;
};
function condText(c) {
  const parts = [];
  if (c.lowerOp) parts.push(`${SYMBOL[c.lowerOp]} ${c.lowerValue}`);
  if (c.upperOp) parts.push(`${SYMBOL[c.upperOp]} ${c.upperValue}`);
  return parts.length ? `${parts.join(' and ')} ${c.unit}` : '';
}
const hasCond = s => !!(s.cond.lowerOp || s.cond.upperOp);
function stepText(s) {
  const occ = s.occurrence === 'any' ? '' : `the ${s.occurrence === 'first' ? 'first' : 'last recorded'} `;
  const c = hasCond(s) ? ` ${condText(s.cond)}` : '';
  return `${occ}${labelOf(s.concept)}${c}`;
}
function relationText(r) {
  if (r.kind === 'during') return `${r.to} while ${r.from} is running${+r.grace ? ` or up to ${r.grace} ${r.unit}s after it stops` : ''}`;
  const lo = r.min !== '', hi = r.max !== '';
  const span = lo && hi ? `${r.min}–${r.max} ${r.unit}s` : lo ? `at least ${r.min} ${r.unit}s` : hi ? `at most ${r.max} ${r.unit}s` : 'any time';
  return `${r.to} starts ${span} after ${r.from}`;
}
const SCOPE_TEXT = {within: 'inside the index period', from_start: 'from the start of the index period onward',
  after: 'after the index period ends', any: 'anywhere in the record'};

function problems(def) {
  const out = [], ids = def.steps.map(s => s.id);
  def.steps.forEach(s => {
    const e = entry(s.concept);
    if (!e) { out.push(`Step ${s.id}: choose an item class or category.`); return; }
    if (hasCond(s)) {
      if (!s.cond.unit) out.push(`Step ${s.id}: choose a unit.`);
      if (s.cond.lowerOp && !DECIMAL.test(s.cond.lowerValue)) out.push(`Step ${s.id}: the lower threshold must be a plain decimal such as 65 or 37.5.`);
      if (s.cond.upperOp && !DECIMAL.test(s.cond.upperValue)) out.push(`Step ${s.id}: the upper threshold must be a plain decimal such as 65 or 37.5.`);
      if (s.widenValue.on) {
        if (s.cond.lowerOp && !DECIMAL.test(s.widenValue.lowerValue)) out.push(`Step ${s.id}: the relaxed lower threshold must be a plain decimal.`);
        if (s.cond.upperOp && !DECIMAL.test(s.widenValue.upperValue)) out.push(`Step ${s.id}: the relaxed upper threshold must be a plain decimal.`);
      }
    }
  });
  def.relations.forEach((r, k) => {
    const n = `Constraint ${k + 1}`;
    if (!ids.includes(r.from) || !ids.includes(r.to)) out.push(`${n} names a step that no longer exists.`);
    if (r.from === r.to) out.push(`${n} relates a step to itself.`);
    if (r.kind === 'after') {
      if (r.min !== '' && !AMOUNT.test(r.min)) out.push(`${n}: the earliest gap must be a number.`);
      if (r.max !== '' && !AMOUNT.test(r.max)) out.push(`${n}: the latest gap must be a number.`);
      if (r.min !== '' && r.max !== '' && +r.max < +r.min) out.push(`${n}: the latest gap is smaller than the earliest.`);
      if (r.extend.on && (r.max === '' || !AMOUNT.test(r.extend.value) || +r.extend.value <= +r.max)) out.push(`${n}: the relaxed latest gap must be a number larger than the latest gap.`);
    } else {
      const from = def.steps.find(s => s.id === r.from);
      if (from && entry(from.concept) && !entry(from.concept).interval) out.push(`${n}: "happens while" needs an infusion item (an input event) as its first step, because only an infusion has an end.`);
      if (!AMOUNT.test(r.grace) || +r.grace < 0) out.push(`${n}: the grace must be a number, zero or more.`);
      if (r.extend.on && (!AMOUNT.test(r.extend.value) || +r.extend.value <= +r.grace)) out.push(`${n}: the relaxed grace must be larger than the grace.`);
    }
  });
  return out;
}

/* The request: the server's query form. Thresholds go as strings. */
function request(def) {
  const bound = (op, value) => op ? {op, value} : undefined;
  const condition = (c, lower, upper) => {
    const out = {unit: c.unit};
    if (c.lowerOp) out.lower = bound(c.lowerOp, lower);
    if (c.upperOp) out.upper = bound(c.upperOp, upper);
    return out;
  };
  const steps = def.steps.map(s => {
    const e = entry(s.concept), out = {id: s.id, concept: s.concept, occurrence: s.occurrence};
    if (hasCond(s)) {
      out.condition = condition(s.cond, s.cond.lowerValue, s.cond.upperValue);
      if (s.widenValue.on) out.widen_value = {condition: condition(s.cond, s.widenValue.lowerValue, s.widenValue.upperValue), cost: s.widenValue.cost};
    }
    if (s.widenConcept.on && e?.grouping) out.widen_concept = {concept: e.grouping, cost: s.widenConcept.cost};
    return out;
  });
  const relations = def.relations.map(r => {
    const amount = v => ({amount: v, unit: r.unit});
    if (r.kind === 'during') {
      const out = {kind: 'during', from: r.from, to: r.to, grace: amount(r.grace)};
      if (r.extend.on) out.extend_grace = {grace: amount(r.extend.value), cost: r.extend.cost};
      return out;
    }
    const out = {kind: 'after', from: r.from, to: r.to};
    if (r.min !== '') out.min = amount(r.min);
    if (r.max !== '') out.max = amount(r.max);
    if (r.extend.on && r.max !== '') out.extend_max = {max: amount(r.extend.value), cost: r.extend.cost};
    return out;
  });
  return {window: def.window, scope: def.scope, budget: def.budget, steps, relations};
}

/* The labels of the relaxations the page declared, by the identifiers the server gives them. */
function relaxationText(def, id) {
  const [kind, target] = id.split(':');
  if (kind === 'value') {
    const s = def.steps.find(x => x.id === target);
    return s ? `${s.id}: threshold relaxed to ${condText({...s.cond, lowerValue: s.widenValue.lowerValue, upperValue: s.widenValue.upperValue})}` : id;
  }
  if (kind === 'concept') {
    const s = def.steps.find(x => x.id === target), e = s && entry(s.concept);
    return e ? `${s.id}: any item in ${e.category}` : id;
  }
  const r = def.relations[+target.slice(1) - 1];
  if (!r) return id;
  return r.kind === 'during' ? `${r.from}→${r.to}: up to ${r.extend.value} ${r.unit}s after it stops` : `${r.from}→${r.to}: latest gap up to ${r.extend.value} ${r.unit}s`;
}

/* Rendering */

function renderStore() {
  const s = state.status, box = $('#store');
  if (!s) {
    box.innerHTML = `<div class="bad"><b>No server is answering.</b> Open this page through <code>demos/mockups/server.py</code>, not as a file or from static hosting: the query runs in the execution store on the server. ${state.error ? `(${esc(state.error)})` : ''}</div>`;
    return;
  }
  if (!s.available) {
    const why = {STORE_NOT_CONFIGURED: 'The server was started without a store.',
      SNAPSHOT_NOT_OPEN_MIMIC_DEMO: 'The configured snapshot\'s manifest does not name the open MIMIC-IV demo, so it is refused.',
      SNAPSHOT_CONTENT_NOT_OPEN_MIMIC_DEMO: 'The configured snapshot does not hold the open MIMIC-IV demo\'s 100 patients, whatever its label says, so it is refused. This page serves only the open demo: counts alone are not safe for credentialed data.',
      SNAPSHOT_NOT_FOUND: 'No snapshot was found at the configured path.', SEVERAL_SNAPSHOTS_UNDER_PATH: 'The configured path holds several snapshots; name one.',
      STORE_SUPPORT_NOT_INSTALLED: 'The server\'s Python has no DuckDB; install the store requirements.',
      SNAPSHOT_UNREADABLE: 'The snapshot could not be read or failed its file check.'}[s.error] || s.error;
    box.innerHTML = `<div class="bad"><b>No store to query.</b> ${esc(why)} <span class="muted">(${esc(s.error)})</span></div>
      <h3>Build one locally</h3>
      <p class="hint">The store is built on your machine from the verified demo files in <code>data/local-mimic/</code> (fetched and checked as in decision D11) and is never committed. The build takes about two minutes.</p>
      <pre>${s.how_to_build.map(esc).join('\n')}</pre>
      <p class="hint">The path can also come from the <code>PTM_MIMIC_STORE</code> environment variable, and the dictionary from <code>PTM_MIMIC_D_ITEMS</code>. Only a snapshot labelled <code>${esc(s.open_datasets.join(', '))}</code> that also holds the open demo's own 100 patients, checked by a pinned digest of their identifiers, is served.</p>`;
    return;
  }
  const t = s.tables;
  box.innerHTML = `<div class="facts">
      <div class="fact"><b>${fmt(s.patients)}</b>patients in the open MIMIC-IV demo</div>
      <div class="fact"><b>${fmt(t.events)}</b>events: infusion segments and chart measurements</div>
      <div class="fact"><b>${fmt(t.series)}</b>measured values, each with its unit</div>
      <div class="fact"><b>${fmt(t.excluded_records)}</b>excluded records: rows kept by item and time, without a value</div>
    </div>
    <p class="hint">Snapshot <code>${esc(s.snapshot_id.slice(0, 16))}…</code> · ${esc(s.store_profile)} · dataset <code>${esc(s.dataset_id)}</code> · ${s.labels.clinical_mapping_verified === false ? 'clinical mapping not verified' : ''}${s.dictionary_labels ? ' · item labels from the demo\'s <code>d_items</code>' : ' · no <code>d_items</code> file given, so items are shown by number (start the server with <code>--mimic-d-items</code> for labels)'}</p>`;
}

function itemOptions(selected, filter) {
  const f = (filter || '').trim().toLowerCase();
  const groups = new Map();
  for (const e of state.catalogue.entries) {
    const hay = `${e.label} ${e.itemid || ''} ${e.category || ''}`.toLowerCase();
    if (f && !hay.includes(f) && e.concept !== selected) continue;
    const key = e.category || 'No category';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(e);
  }
  const out = [`<option value="" ${selected ? '' : 'selected'}>Choose an item class or category…</option>`];
  for (const [cat, list] of [...groups].sort((a, b) => a[0].localeCompare(b[0]))) {
    list.sort((a, b) => (a.kind === 'grouping' ? -1 : b.kind === 'grouping' ? 1 : 0) || a.label.localeCompare(b.label));
    out.push(`<optgroup label="${esc(cat)}">${list.map(e => `<option value="${esc(e.concept)}" ${e.concept === selected ? 'selected' : ''}>${esc(e.kind === 'grouping' ? `Any item in ${e.label}` : `${e.label} · ${e.itemid}`)} · ${plural(e.patients, 'patient')}${e.interval ? ' · infusion' : ''}</option>`).join('')}</optgroup>`);
  }
  return out.join('');
}

function opSelect(side, value, k) {
  return `<select data-step="${k}" data-f="cond.${side}Op" aria-label="${side === 'lower' ? 'Lower bound' : 'Upper bound'}">${OPS[side].map(([v, t]) => `<option value="${v}" ${v === value ? 'selected' : ''}>${t}</option>`).join('')}</select>`;
}

function renderSteps() {
  const def = state.def;
  $('#steps').innerHTML = def.steps.map((s, k) => {
    const e = entry(s.concept), units = e ? e.units : [];
    const valued = units.length > 0;
    const cid = `item-${k}`;
    const condition = !e ? '<p class="hint">Choose an item first.</p>' : !valued
      ? `<p class="hint">No recorded values: ${e.interval ? 'infusion segments carry a start and an end only' : 'this item has no value in the store'}.</p>`
      : `<div class="row">
          <label for="unit-${k}">Unit</label>
          <select id="unit-${k}" data-step="${k}" data-f="cond.unit">${units.map(u => `<option value="${esc(u.unit)}" ${u.unit === s.cond.unit ? 'selected' : ''}>${esc(u.unit)} · ${fmt(u.rows)} values</option>`).join('')}</select>
        </div>
        <div class="row" style="margin-top:6px">
          ${opSelect('lower', s.cond.lowerOp, k)}
          ${s.cond.lowerOp ? `<input class="num" type="text" inputmode="decimal" value="${esc(s.cond.lowerValue)}" data-step="${k}" data-f="cond.lowerValue" aria-label="Lower threshold" aria-invalid="${!DECIMAL.test(s.cond.lowerValue)}">` : ''}
          ${opSelect('upper', s.cond.upperOp, k)}
          ${s.cond.upperOp ? `<input class="num" type="text" inputmode="decimal" value="${esc(s.cond.upperValue)}" data-step="${k}" data-f="cond.upperValue" aria-label="Upper threshold" aria-invalid="${!DECIMAL.test(s.cond.upperValue)}">` : ''}
        </div>
        <p class="hint">Units listed are those recorded for this item in the store. Thresholds are exact decimals, sent as typed.</p>`;
    const relaxValue = e && valued && hasCond(s) ? `<label><input type="checkbox" data-step="${k}" data-f="widenValue.on" ${s.widenValue.on ? 'checked' : ''}> or relax the threshold to</label>
        ${s.widenValue.on ? `<span class="row" style="display:inline-flex;gap:6px">${s.cond.lowerOp ? `${SYMBOL[s.cond.lowerOp]} <input class="num" type="text" inputmode="decimal" value="${esc(s.widenValue.lowerValue)}" data-step="${k}" data-f="widenValue.lowerValue" aria-label="Relaxed lower threshold" aria-invalid="${!DECIMAL.test(s.widenValue.lowerValue)}">` : ''}
          ${s.cond.upperOp ? `${SYMBOL[s.cond.upperOp]} <input class="num" type="text" inputmode="decimal" value="${esc(s.widenValue.upperValue)}" data-step="${k}" data-f="widenValue.upperValue" aria-label="Relaxed upper threshold" aria-invalid="${!DECIMAL.test(s.widenValue.upperValue)}">` : ''}
          ${esc(s.cond.unit)} at cost ${costSelect(s.widenValue.cost, `data-step="${k}" data-f="widenValue.cost"`)}</span>` : ''}` : '';
    const relaxConcept = e && e.kind === 'item' && e.grouping ? `<label><input type="checkbox" data-step="${k}" data-f="widenConcept.on" ${s.widenConcept.on ? 'checked' : ''}> or accept any item in <i>${esc(e.category)}</i></label>
        ${s.widenConcept.on ? ` at cost ${costSelect(s.widenConcept.cost, `data-step="${k}" data-f="widenConcept.cost"`)}` : ''}` : '';
    return `<div class="step-card">
      <div class="step-head"><div><span class="sid">${esc(s.id)}</span><strong>${esc(stepText(s))}</strong></div>
        <button class="btn" type="button" data-action="remove-step" data-k="${k}" aria-label="Remove step ${esc(s.id)}" ${def.steps.length < 2 ? 'disabled' : ''}>Remove</button></div>
      <div class="grid2">
        <div>
          <label for="filter-${k}">Item class or category</label>
          <input type="search" id="filter-${k}" placeholder="Filter by name, item number or category, e.g. pressure, 220045, Labs" value="${esc(state.filters[k] || '')}" data-filter="${k}" aria-controls="${cid}">
          <select class="item" id="${cid}" data-step="${k}" data-f="concept" aria-label="Item class or category for step ${esc(s.id)}" style="margin-top:6px">${itemOptions(s.concept, state.filters[k])}</select>
          ${e ? `<p class="hint">${e.kind === 'grouping' ? 'A dictionary category: every item class under it.' : `Category: ${esc(e.category || 'none')}.`} ${plural(e.patients, 'patient')}, ${plural(e.events, 'event')} in the store${e.interval ? '; infusion segments' : ''}.</p>` : ''}
          <div class="row" style="margin-top:6px"><label for="occ-${k}">Which</label>
            <select id="occ-${k}" data-step="${k}" data-f="occurrence">${[['any', 'any occurrence'], ['first', 'the first'], ['last', 'the last recorded']].map(([v, t]) => `<option value="${v}" ${s.occurrence === v ? 'selected' : ''}>${t}</option>`).join('')}</select></div>
          ${s.occurrence !== 'any' && hasCond(s) ? '<p class="hint">First and last pick among the events that meet the value condition: "the first heart rate above 120", not "the first heart rate, if above 120".</p>' : ''}
        </div>
        <fieldset><legend>Value condition (optional)</legend>${condition}</fieldset>
      </div>
      ${relaxValue || relaxConcept ? `<fieldset style="margin-top:10px"><legend>Relaxations (optional)</legend><div>${relaxValue}</div><div>${relaxConcept}</div></fieldset>` : ''}
    </div>`;
  }).join('');
  $('#add-step').disabled = def.steps.length >= IDS.length;
}

function costSelect(value, attrs) {
  return `<select ${attrs} aria-label="Cost">${['0.25', '0.5', '0.75', '1'].map(c => `<option ${c === value ? 'selected' : ''}>${c}</option>`).join('')}</select>`;
}

function renderRelations() {
  const def = state.def, ids = def.steps.map(s => s.id);
  const sel = (value, k, f, label) => `<select data-rel="${k}" data-f="${f}" aria-label="${label}">${ids.map(id => `<option ${id === value ? 'selected' : ''}>${id}</option>`).join('')}</select>`;
  const units = (value, k) => `<select data-rel="${k}" data-f="unit" aria-label="Unit">${TIME_UNITS.map(u => `<option value="${u}" ${u === value ? 'selected' : ''}>${u}s</option>`).join('')}</select>`;
  const num = (value, k, f, label, placeholder = '') => `<input class="num" type="text" inputmode="decimal" value="${esc(value)}" placeholder="${placeholder}" data-rel="${k}" data-f="${f}" aria-label="${label}">`;
  $('#relations').innerHTML = def.relations.length ? `<div class="scroll"><table><thead><tr><th scope="col">Later step</th><th scope="col">Relation</th><th scope="col">Earlier step</th><th scope="col">Gap</th><th scope="col">Relaxation</th><th></th></tr></thead><tbody>${def.relations.map((r, k) => `<tr>
      <td>${sel(r.to, k, 'to', 'Later step')}</td>
      <td><select data-rel="${k}" data-f="kind" aria-label="Relation"><option value="after" ${r.kind === 'after' ? 'selected' : ''}>starts between … after</option><option value="during" ${r.kind === 'during' ? 'selected' : ''}>happens while</option></select></td>
      <td>${sel(r.from, k, 'from', 'Earlier step')}</td>
      <td>${r.kind === 'after' ? `${num(r.min, k, 'min', 'Earliest gap')} – ${num(r.max, k, 'max', 'Latest gap', '∞')} ${units(r.unit, k)}`
        : `or up to ${num(r.grace, k, 'grace', 'Grace after it stops')} ${units(r.unit, k)} after it stops`}</td>
      <td><label><input type="checkbox" data-rel="${k}" data-f="extend.on" ${r.extend.on ? 'checked' : ''} ${r.kind === 'after' && r.max === '' ? 'disabled' : ''}> ${r.kind === 'after' ? 'latest up to' : 'grace up to'}</label>
        ${r.extend.on ? `${num(r.extend.value, k, 'extend.value', 'Relaxed limit')} at cost ${costSelect(r.extend.cost, `data-rel="${k}" data-f="extend.cost"`)}` : ''}</td>
      <td><button class="btn" type="button" data-action="remove-relation" data-k="${k}" aria-label="Remove constraint ${k + 1}">Remove</button></td></tr>`).join('')}</tbody></table></div>`
    : '<p class="hint">No constraints: each step matches on its own, anywhere the scope allows.</p>';
  $('#add-relation').disabled = def.steps.length < 2;
}

function renderReading() {
  const def = state.def;
  const steps = def.steps.map(s => `${s.id}: ${stepText(s)}`).join('; ');
  const rels = def.relations.map(relationText).join('; ');
  $('#reading').innerHTML = `<strong>Reads as:</strong> ${esc(steps)}${rels ? `; ${esc(rels)}` : ''}. Every step falls ${esc(SCOPE_TEXT[def.scope])}, where the index period is ${esc($('#window').selectedOptions[0]?.textContent || def.window)}.`;
  ['window', 'scope', 'budget'].forEach(f => { $(`#${f}`).value = def[f]; });
  const p = problems(def);
  $('#problems').innerHTML = p.length ? `<ul>${p.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '';
  $('#run').disabled = p.length > 0 || state.pending;
}

function renderResults() {
  const box = $('#results');
  if (state.pending) { box.innerHTML = '<p class="muted">Running in the store…</p>'; return; }
  if (state.error && state.status?.available) {
    const why = REFUSALS[state.error];
    const internal = {AGGREGATE_RULE_VIOLATION: 'The server built an answer that was not aggregate counts only, so it sent nothing. This is a fault in the server, not in the query.',
      INTERNAL_ERROR: 'The server failed while answering. This is a fault in the server, not in the query.'}[state.error];
    box.innerHTML = internal ? `<div class="bad"><b>No answer.</b> ${esc(internal)} <span class="muted">(${esc(state.error)})</span></div>`
      : `<div class="bad"><b>The store refused the query.</b> ${why ? esc(why) + ' ' : ''}<span class="muted">(${esc(state.error)})</span></div>`;
    return;
  }
  const r = state.result;
  if (!r) { box.innerHTML = '<p class="muted">Run a query or load an example.</p>'; return; }
  const def = state.ran;
  const total = r.patients;
  const rows = STATUS_ORDER.map(s => `<tr><td><span class="status s-${s}">${esc(STATUS[s][0])}</span></td><td class="n">${fmt(r.counts[s])}</td>
      <td class="n">${total ? Math.round(100 * r.counts[s] / total) : 0}%</td><td>${esc(STATUS[s][1])}</td></tr>`).join('');
  const table = (title, head, rows) => rows.length ? `<h3>${title}</h3><table><thead><tr>${head.map(h => `<th scope="col"${/^(Patients|Relaxed matches|Noted beside unresolved)$/.test(h) ? ' class="n"' : ''}>${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>` : '';
  const reasonRows = map => Object.entries(map).map(([k, n]) => `<tr><td><code>${esc(k)}</code></td><td class="n">${fmt(n)}</td><td>${esc(REASONS[k] || '')}</td></tr>`);
  const relaxRows = r.relaxations.map(x => `<tr><td>${esc(relaxationText(def, x.id))}</td><td class="n">${fmt(x.relaxed)}</td><td class="n">${fmt(x.beside_unresolved)}</td></tr>`);
  box.innerHTML = `<div class="card">
      <p style="margin-top:0"><b>${plural(total, 'patient')}</b> · the index period was placed for ${fmt(r.windows.placed)}. Counts only: the server sends no patient identifiers, times or values.</p>
      <table><thead><tr><th scope="col">Result</th><th scope="col" class="n">Patients</th><th scope="col" class="n">Share</th><th scope="col">Meaning</th></tr></thead><tbody>${rows}</tbody></table>
      ${r.none_incomplete ? `<p class="hint">Of the no-match results, <b>${fmt(r.none_incomplete)}</b> are not complete: some relaxed search was left undecided (see the notes below), so a relaxed match may exist.</p>` : ''}
      ${table('Why not comparable', ['Reason', 'Patients', 'Meaning'], reasonRows(r.not_comparable_reasons))}
      ${table('Why unresolved', ['Reason', 'Patients', 'Meaning'], reasonRows(r.unresolved_reasons))}
      ${table('Notes on other results', ['Note', 'Patients', 'Meaning'], reasonRows(r.notes))}
      ${table('Relaxations', ['Relaxation', 'Relaxed matches', 'Noted beside unresolved'], relaxRows)}
      ${Object.keys(r.relaxed_certainty).length ? `<p class="hint">Relaxed matches: ${Object.entries(r.relaxed_certainty).map(([k, n]) => `${fmt(n)} ${k}`).join(', ')}.</p>` : ''}
      <h3>How the store got there</h3>
      <p class="hint">The candidate filter kept <b>${fmt(r.candidates)}</b> patients: each step's item (or a declared widening) is recorded for them at least once (${r.plan.map(p => `step ${esc(p.step)}: ${fmt(p.patients)}`).join(', ')}). ${r.pruned === null ? 'Time pruning is switched off on this server, so the engine ran on every candidate with a period.' : `Time pruning then settled <b>${fmt(r.pruned)}</b> of those with a period as no match from the stored time summaries, without loading their events (D25), and the matching engine ran on the rest.`} The <b>${fmt(r.filtered)}</b> patients outside the candidate set are no match without running the engine, unless coverage or excluded rows make them not comparable. Matching took ${r.timing.match_seconds.toFixed(3)} s on the server (${r.timing.total_seconds.toFixed(3)} s in all, including a second pass to count pruned candidates).</p>
    </div>`;
}

function render() {
  renderStore();
  if (!state.catalogue) { $('#builder').hidden = true; return; }
  $('#builder').hidden = false;
  renderSteps();
  renderRelations();
  renderReading();
  renderResults();
}

function renderExamples() {
  $('#examples').innerHTML = EXAMPLES.map((x, k) => {
    const missing = x.def.steps.filter(s => !entry(s.concept)).map(s => s.concept.slice(s.concept.lastIndexOf('/') + 1));
    return `<button class="example" type="button" data-action="example" data-k="${k}" ${missing.length ? 'disabled' : ''}>
      <strong>${esc(x.title)}</strong><span>${esc(x.text)}${missing.length ? ` Not available: item${missing.length > 1 ? 's' : ''} ${esc(missing.join(', '))} not in this store.` : ''}</span></button>`;
  }).join('');
}

/* Interaction */

function setPath(obj, path, value) {
  const keys = path.split('.');
  let o = obj;
  keys.slice(0, -1).forEach(k => { o = o[k]; });
  o[keys[keys.length - 1]] = value;
}

function onChange(el) {
  const def = state.def;
  if (el.dataset.field) { def[el.dataset.field] = el.value; return renderReading(); }
  if (el.dataset.step != null) {
    const s = def.steps[+el.dataset.step], f = el.dataset.f;
    const value = el.type === 'checkbox' ? el.checked : el.value;
    if (f === 'concept') {
      const next = freshStep(s.id, value);
      next.occurrence = s.occurrence;
      def.steps[+el.dataset.step] = next;
      state.filters[+el.dataset.step] = '';
    } else {
      setPath(s, f, value);
      // With no bound left there is no threshold to relax.
      if ((f === 'cond.lowerOp' || f === 'cond.upperOp') && !hasCond(s)) s.widenValue.on = false;
    }
    if (el.tagName === 'INPUT' && el.type === 'text') { el.setAttribute('aria-invalid', String(!DECIMAL.test(el.value))); return renderReading(); }
    return render();
  }
  if (el.dataset.rel != null) {
    const r = def.relations[+el.dataset.rel], f = el.dataset.f;
    setPath(r, f, el.type === 'checkbox' ? el.checked : el.value);
    if (f === 'kind') r.extend.on = false;
    if (el.tagName === 'INPUT' && el.type === 'text') return renderReading();
    return render();
  }
}

async function run() {
  const def = state.def;
  if (problems(def).length) return;
  state.pending = true; state.error = null;
  renderReading(); renderResults();
  try {
    const response = await fetch(API + 'match', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(request(def))});
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || `HTTP ${response.status}`);
    state.result = value; state.ran = JSON.parse(JSON.stringify(def));
  } catch (error) {
    state.result = null; state.error = error.message;
  }
  state.pending = false;
  renderReading(); renderResults();
  $('#h-results').scrollIntoView({behavior: 'smooth', block: 'start'});
}

function onClick(el) {
  const def = state.def, k = +el.dataset.k;
  switch (el.dataset.action) {
    case 'example':
      state.def = normalise(EXAMPLES[k].def); state.filters = {};
      render();
      return run();
    case 'add-step': {
      const id = IDS.find(x => !def.steps.some(s => s.id === x));
      if (id) def.steps.push(freshStep(id, ''));
      break;
    }
    case 'remove-step': {
      const id = def.steps[k].id;
      def.steps.splice(k, 1);
      def.relations = def.relations.filter(r => r.from !== id && r.to !== id);
      state.filters = {};
      break;
    }
    case 'add-relation': {
      const n = def.steps.length;
      def.relations.push(normalise({steps: [], relations: [{from: def.steps[n - 2].id, to: def.steps[n - 1].id, kind: 'after', min: '0', max: '6', unit: 'hour'}]}).relations[0]);
      break;
    }
    case 'remove-relation': def.relations.splice(k, 1); break;
    case 'run': return run();
    default: return;
  }
  render();
}

function wire() {
  document.addEventListener('change', e => { const el = e.target.closest('[data-field],[data-step],[data-rel]'); if (el) onChange(el); });
  document.addEventListener('input', e => {
    const el = e.target;
    if (el.dataset.filter != null) {
      state.filters[+el.dataset.filter] = el.value;
      const s = state.def.steps[+el.dataset.filter];
      $(`#item-${el.dataset.filter}`).innerHTML = itemOptions(s.concept, el.value);
      return;
    }
    if (el.matches('input[type=text][data-step], input[type=text][data-rel]')) onChange(el);
  });
  document.addEventListener('click', e => { const el = e.target.closest('button[data-action]'); if (el && !el.disabled) onClick(el); });
}

async function main() {
  wire();
  try {
    const response = await fetch(API + 'status');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.status = await response.json();
  } catch (error) {
    state.error = error.message;
    return render();
  }
  if (state.status.available) {
    try {
      const response = await fetch(API + 'catalogue');
      const value = await response.json();
      if (!response.ok) throw new Error(value.error || `HTTP ${response.status}`);
      state.catalogue = value;
      value.entries.forEach(e => state.byConcept.set(e.concept, e));
      state.def = normalise(EXAMPLES[0].def);
      renderExamples();
    } catch (error) {
      state.status = {...state.status, available: false, error: error.message, how_to_build: [], open_datasets: []};
    }
  }
  render();
  // The admission figures follow the committed demo report (via results.json); the static text is the fallback.
  fetch('../results/results.json').then(r => r.json()).then(d => {
    const X = d.mimic_demo, set = (k, a, b) => document.querySelectorAll(`[data-k="${k}"]`).forEach(e => { e.textContent = `${fmt(a)} of ${fmt(b)}`; });
    set('chart', X.chartevents.admitted, X.chartevents.rows);
    set('input', X.inputevents.admitted, X.inputevents.rows);
  }).catch(() => {});
}

main();
