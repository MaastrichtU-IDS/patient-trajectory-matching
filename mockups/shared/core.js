'use strict';
/* Shared by every mockup page: the synthetic dataset, concepts, time, records grouped into
   events, the index window, the diary rise rule and the timeline renderer. Pages keep their
   own interface state in the same \`state\` object and set \`hooks.hidden\` if they hide
   anything from view. Records are grouped into events using the generator's ground truth,
   which a real system would have to infer. */

const state = {data: null, concepts: null, codeIndex: new Map(), patients: new Map(), evidence: new Map(), ancestry: new Map()};
const hooks = {hidden: () => false};

async function loadData() {
  const response = await fetch(DATA_URL);
  if (!response.ok) throw new Error(`${DATA_URL}: HTTP ${response.status}`);
  state.data = await response.json();
  state.concepts = state.data.concepts;
  for (const [cid, c] of Object.entries(state.concepts)) for (const coding of c.codings) state.codeIndex.set(`${coding.system}|${coding.code}`, cid);
  for (const p of state.data.patients) state.patients.set(p.patient_id, p);
}


const DATA_URL = '../data/synthetic-paediatric-rare.json';
const DAY_MS = 86400000;
const MONTH = 30.44;
const PRECISION = {minute: 0, day: 1, approximate: 2, month: 3};
const CATEGORY = {missing: 'Missing', incorrect: 'Incorrect', temporal: 'Timing', coding: 'Coding', free_text: 'Free text'};
const SOURCE_SHORT = {ehr: 'Hospital EHR', letters: 'Clinic letter', referral_nl: 'Referral letter (NL)',
  discharge_fr: 'Discharge summary (FR)', genetics: 'Genetics lab', pharmacy: 'Pharmacy', diary: 'Parent diary',
  registry: 'ERN registry', nbs: 'Newborn screening'};
const GROUP_LABEL = {dravet: 'Dravet syndrome', gefs_plus: 'GEFS+', febrile_only: 'Febrile seizures only',
  pcdh19: 'PCDH19 clustering epilepsy', cdkl5: 'CDKL5 deficiency disorder', tsc: 'Tuberous sclerosis complex',
  rett: 'Rett syndrome', sma: 'Spinal muscular atrophy'};
const LANES = [
  {key: 'phenotype', label: 'Seizures & findings'},
  {key: 'diagnosis', label: 'Diagnoses'},
  {key: 'tests', label: 'Tests & genetics'},
  {key: 'medication', label: 'Medication'},
  {key: 'frequency', label: 'Seizures / month', fixed: 48},
  {key: 'growth', label: 'Weight', fixed: 34},
  {key: 'letters', label: 'Letters', fixed: 24},
];
const COLOR = {phenotype: '#174EA6', diagnosis: '#172B3A', tests: '#68449A', medication: '#126B5D', letters: '#8592A0'};

const $ = (sel, root = document) => root.querySelector(sel);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const clone = value => JSON.parse(JSON.stringify(value));
const uniq = list => [...new Set(list)];
const min = list => list.length ? Math.min(...list) : null;
function push(map, key, value) { if (!map.has(key)) map.set(key, []); map.get(key).push(value); }

/* Concepts */

function ancestors(cid) {
  if (state.ancestry.has(cid)) return state.ancestry.get(cid);
  const out = new Set(), stack = [cid];
  while (stack.length) {
    const c = stack.pop();
    if (out.has(c)) continue;
    out.add(c);
    (state.concepts[c]?.parents || []).forEach(p => stack.push(p));
  }
  state.ancestry.set(cid, out);
  return out;
}
const isA = (cid, target) => !!cid && ancestors(cid).has(target);
const label = cid => state.concepts[cid]?.label || cid;
const conceptOf = coding => coding ? state.codeIndex.get(`${coding.system}|${coding.code}`) || null : null;
const mostSpecific = list => list.reduce((best, c) => (!best || ancestors(c).size > ancestors(best).size) ? c : best, null);
const classesOf = cid => state.concepts[cid]?.classes || [];
const codeText = c => c.code.startsWith(`${c.system}:`) ? c.code : `${c.system} ${c.code}`;
/* Time */

/* Days of age, fractional: a record written at 14:32 is 0.6 of a day after midnight, so
   hours and minutes keep their meaning when a window or gap is set in those units. */
function dayOf(p, iso) {
  const at = iso.length > 10 ? Date.parse(iso.slice(0, 16) + 'Z') : Date.parse(iso.slice(0, 10) + 'T00:00:00Z');
  return (at - Date.parse(p.birth_date + 'T00:00:00Z')) / DAY_MS;
}

const UNITS = {second: [1 / 86400, 's'], minute: [1 / 1440, 'min'], hour: [1 / 24, 'h'], day: [1, 'd'], week: [7, 'wk'], month: [MONTH, 'mo'], year: [365.25, 'y']};
const toDays = (amount, unit) => (+amount || 0) * (UNITS[unit] || UNITS.day)[0];
const fromDays = (days, unit) => days / (UNITS[unit] || UNITS.day)[0];
const amountText = (amount, unit) => `${+amount} ${unit}${Math.abs(+amount) === 1 ? '' : 's'}`;
const unitShort = unit => (UNITS[unit] || UNITS.day)[1];
function unitSelect(value, attrs) {
  return `<select ${attrs} aria-label="Unit">${Object.keys(UNITS).map(u => `<option value="${u}" ${u === value ? 'selected' : ''}>${u}s</option>`).join('')}</select>`;
}
function ageText(d) {
  if (d == null || Number.isNaN(d)) return '—';
  if (d < 0) return 'before birth';
  if (d < 1) return `${Math.round(d * 24)} h`;
  if (d < 60) return `${Math.round(d)} d`;
  if (d < 730) return `${Math.round(d / MONTH)} mo`;
  let y = Math.floor(d / 365.25), m = Math.round((d - y * 365.25) / MONTH);
  if (m === 12) { y += 1; m = 0; }
  return m ? `${y} y ${m} mo` : `${y} y`;
}
function relText(d, anchor) {
  if (Math.abs(d) < 15) return anchor;
  return (d < 0 ? '−' : '+') + ageText(Math.abs(d));
}
const dateOf = iso => iso.slice(0, 10);

/* Evidence: records grouped into events, plus series and letters */

function span(r, day) {
  return {e: day(r.occurred.earliest), l: day(r.occurred.latest), precision: r.occurred.precision,
          as: r.occurred.as_written, source: r.source};
}

function textSpan(x, day) {
  const nd = day(x.note.occurred.earliest), t = x.m.time_expression || '';
  const out = (e, l) => ({e, l, precision: 'approximate', as: t || `before the letter of ${dateOf(x.note.occurred.earliest)}`,
                          source: x.note.source});
  let m;
  if (/this week/.test(t)) return out(nd - 7, nd);
  if (/a week ago/.test(t)) return out(nd - 10, nd - 4);
  if ((m = t.match(/about (\d+) weeks ago/))) return out(nd - (+m[1] + 1) * 7, nd - Math.max(0, +m[1] - 1) * 7);
  if ((m = t.match(/(\d{2})-(\d{2})-(\d{4})/))) { const d = dayOf(x.note.p, `${m[3]}-${m[2]}-${m[1]}`); return {...out(d, d), precision: 'day'}; }
  if ((m = t.match(/(\d+) (days?|months?|years?)(?: (\d+) months?)?/))) {
    const n = +m[1], unit = m[2][0];
    const d = unit === 'd' ? n : unit === 'm' ? n * MONTH : n * 365.25 + (+m[3] || 0) * MONTH;
    const spread = unit === 'y' ? 45 : 30;
    return out(Math.round(d - spread), Math.round(d + spread));
  }
  return out(nd - 120, nd);
}

function evidenceFor(pid) {
  if (state.evidence.has(pid)) return state.evidence.get(pid);
  const p = state.patients.get(pid);
  const day = iso => dayOf(p, iso);
  const groups = new Map(), notes = [], loose = [];
  for (const r of p.records) {
    if (r.kind === 'note') notes.push(r);
    else if (r.truth_event_id) push(groups, r.truth_event_id, r);
    else loose.push(r);
  }
  const mentions = new Map();
  for (const n of notes) {
    n.p = p;
    for (const m of n.mentions || []) {
      if (m.truth_event_id && m.experiencer === 'patient' && (m.assertion === 'present' || m.assertion === 'possible')) {
        push(mentions, m.truth_event_id, {note: n, m});
      }
    }
  }
  const byRecord = new Map(), byTruth = new Map();
  for (const q of p.quality) {
    if (q.truth_event_id) push(byTruth, q.truth_event_id, q);
    q.record_ids.forEach(id => push(byRecord, id, q));
  }
  const items = [], series = {weight: [], height: [], head_circ: [], seizure_frequency: []};
  const attached = new Set();
  const lastContact = day(p.follow_up.last_contact);
  for (const id of uniq([...groups.keys(), ...mentions.keys()])) {
    const recs = groups.get(id) || [], ms = mentions.get(id) || [];
    const cases = uniq([...(byTruth.get(id) || []), ...recs.flatMap(r => byRecord.get(r.record_id) || [])]);
    cases.forEach(q => attached.add(q.case_id));
    const measured = recs.find(r => r.kind === 'measurement' || r.kind === 'diary_summary');
    if (measured) {
      const cid = conceptOf(measured.coding);
      if (series[cid]) {
        series[cid].push({day: day(measured.occurred.earliest), end: day(measured.occurred.latest), value: measured.value,
                          unit: measured.unit, record: measured, cases});
      }
      continue;
    }
    // A hedged mention written before anything was coded is an earlier, separate assertion
    // ("possible Dravet syndrome; genetics awaited"), not a copy of the later diagnosis.
    const firstCoded = min(recs.filter(r => r.coding && r.occurred).map(r => day(r.occurred.earliest)));
    const early = firstCoded == null ? [] : ms.filter(x => x.m.assertion === 'possible' && day(x.note.occurred.earliest) < firstCoded);
    if (early.length) {
      const noteIds = new Set(early.map(x => x.note.record_id));
      const provisional = makeItem(p, `${id}:provisional`, [], early, cases.filter(q => q.record_ids.some(r => noteIds.has(r))), day, lastContact);
      if (provisional) { provisional.provisional = true; items.push(provisional); }
    }
    const item = makeItem(p, id, recs, ms.filter(x => !early.includes(x)), cases, day, lastContact);
    if (item) items.push(item);
  }
  for (const r of loose) {
    const cases = byRecord.get(r.record_id) || [];
    cases.forEach(q => attached.add(q.case_id));
    const item = makeItem(p, r.record_id, [r], [], cases, day, lastContact);
    if (item) items.push(item);
  }
  const letters = notes.map(n => ({record: n, day: day(n.occurred.earliest), availableDay: day(n.available_at),
                                   cases: byRecord.get(n.record_id) || []}));
  letters.forEach(l => l.cases.forEach(q => attached.add(q.case_id)));
  Object.values(series).forEach(s => s.sort((a, b) => a.day - b.day));
  const view = {p, items, series, letters, lastContact, snapshot: day(p.follow_up.snapshot),
                unattached: p.quality.filter(q => !attached.has(q.case_id)), byId: new Map(items.map(i => [i.id, i]))};
  state.evidence.set(pid, view);
  return view;
}

function makeItem(p, id, recs, ms, cases, day, lastContact) {
  const kinds = new Set(recs.map(r => r.kind));
  const structured = recs.filter(r => r.coding);
  const coded = uniq(structured.map(r => conceptOf(r.coding)).filter(Boolean));
  const texted = uniq(ms.map(x => x.m.concept));
  const all = uniq([...coded, ...texted]);
  const kindOf = c => state.concepts[c]?.kind;
  let kind = 'phenotype';
  if (['test_order', 'genetic_result', 'screening_result'].some(k => kinds.has(k)) || (!recs.length && all.some(c => c.startsWith('gene:')))) kind = 'genetic';
  else if (kinds.has('medication_order') || kinds.has('dispensing') || all.some(c => kindOf(c) === 'medication')) kind = 'medication';
  else if (kinds.has('procedure')) kind = 'procedure';
  else if (all.some(c => kindOf(c) === 'diagnosis')) kind = 'diagnosis';

  const item = {id, kind, recs, mentions: ms, cases, freeTextOnly: !structured.length,
                possibleOnly: !structured.length && ms.length > 0 && ms.every(x => x.m.assertion === 'possible'),
                codings: uniq(structured.map(r => JSON.stringify({...r.coding, source: r.source}))).map(s => JSON.parse(s)),
                recordedDay: min([...recs.map(r => day(r.recorded_at)), ...ms.map(x => day(x.note.recorded_at))]),
                availableDay: min([...recs.map(r => day(r.available_at)), ...ms.map(x => day(x.note.available_at))])};

  if (kind === 'genetic') {
    const order = recs.find(r => r.kind === 'test_order');
    const results = recs.filter(r => r.kind === 'genetic_result').sort((a, b) => a.recorded_at.localeCompare(b.recorded_at));
    const gene = results[0]?.attributes.gene || order?.attributes?.gene_requested || (texted.find(c => c.startsWith('gene:')) || '').slice(5);
    item.concept = `gene:${gene}`;
    item.gene = {gene, method: results[0]?.attributes.method || order?.label_as_recorded,
                 history: results.map(r => ({recorded: day(r.recorded_at), available: day(r.available_at), status: r.status,
                                              classification: r.attributes.classification, hgvs: r.attributes.hgvs}))};
    const base = order || results[0];
    item.time = timeOf(base ? [span(base, day)] : ms.map(x => textSpan(x, day)));
    item.display = `${gene} test`;
    return item;
  }
  if (kind === 'medication') {
    const cid = mostSpecific(all.filter(c => kindOf(c) === 'medication'));
    if (!cid) return null;
    const orders = recs.filter(r => r.kind === 'medication_order');
    const active = orders.find(r => r.status === 'active'), stop = orders.find(r => r.status === 'stopped');
    const disp = recs.filter(r => r.kind === 'dispensing').sort((a, b) => a.occurred.earliest.localeCompare(b.occurred.earliest));
    const start = active ? span(active, day) : disp.length ? {...span(disp[0], day), fromDispensing: true}
      : ms.length ? textSpan(ms[0], day) : null;
    let end = null, ongoing = false;
    if (stop) end = span(stop, day);
    else if (disp.length) {
      const last = disp[disp.length - 1], runOut = day(last.occurred.earliest) + (last.attributes?.supply_days || 30);
      if (runOut + 45 < lastContact) end = {e: runOut, l: runOut + 45, precision: 'approximate', as: 'supply ran out', source: 'pharmacy'};
      else ongoing = true;
    } else ongoing = true;
    Object.assign(item, {concept: cid, display: label(cid), med: {start, end, ongoing, dispensings: disp.length},
                         time: timeOf(start ? [start] : [])});
    return item;
  }
  const pool = (coded.length ? coded : texted).filter(c => kindOf(c) === kind || (kind === 'procedure' && kindOf(c) === 'procedure'));
  item.concept = mostSpecific(pool);
  if (!item.concept) return null;
  item.textConcept = texted.find(c => c !== item.concept && isA(c, item.concept)) || null;
  item.display = label(item.textConcept || item.concept);
  const spans = recs.filter(r => r.occurred).map(r => span(r, day));
  item.time = timeOf(spans.length ? spans : ms.map(x => textSpan(x, day)));
  if (kind === 'procedure') {
    const proc = recs.find(r => r.kind === 'procedure');
    item.procedure = {result: proc?.text || null, status: proc?.status};
  }
  return item;
}

function timeOf(spans) {
  if (!spans.length) return {best: null, env: null, conflicting: false, spans};
  const best = [...spans].sort((a, b) => PRECISION[a.precision] - PRECISION[b.precision] || a.e - b.e)[0];
  const env = {e: Math.min(...spans.map(s => s.e)), l: Math.max(...spans.map(s => s.l))};
  const conflicting = spans.some(a => spans.some(b => a.l < b.e));
  return {best, env, conflicting, spans};
}

function truthItems(pid) {
  const p = state.patients.get(pid), view = evidenceFor(pid);
  const lane = {phenotype: 'phenotype', diagnosis: 'diagnosis', genetic: 'tests', procedure: 'tests', medication: 'medication'};
  return p.truth.events.filter(e => lane[e.kind]).map(e => ({
    id: e.event_id, truth: e, kind: e.kind, concept: e.concept, lane: lane[e.kind],
    display: e.kind === 'genetic' ? `${e.gene} test` : label(e.concept), recorded: view.byId.get(e.event_id) || null,
    day: e.day, end: e.end_day ?? null,
  }));
}

/* Index, availability and features */

const conceptFor = (item, q) => q.freeText !== 'off' && item.textConcept ? item.textConcept : item.concept;

function admissible(item, q) {
  if (item.freeTextOnly && q.freeText === 'off') return false;
  if (item.possibleOnly && q.freeText !== 'possible') return false;
  return true;
}

function knownResult(item, end, q) {
  const known = item.gene.history.filter(h => (q.knownByIndex ? h.available : h.recorded) <= end);
  return known.length ? known[known.length - 1] : null;
}

/* The index window. Findings count if they occurred inside it; diagnoses and genetic
   results are states, so they count if established by its end; medicines count if taken
   at any time during it. With knownByIndex, nothing counts that was not available by the end. */
function usable(view, q, win) {
  return view.items.filter(i => {
    if (!i.concept || !admissible(i, q)) return false;
    if (i.kind === 'genetic') return !!knownResult(i, win.end, q);
    if (q.knownByIndex && i.availableDay > win.end) return false;
    if (i.kind === 'medication') {
      const m = i.med;
      return !!m.start && m.start.e <= win.end && (m.ongoing || !m.end || m.end.l >= win.start);
    }
    const at = i.time.best?.e;
    if (at == null || at > win.end) return false;
    return i.kind === 'diagnosis' || at >= win.start;
  });
}

/* Index events are any concept, matched with its narrower concepts, at its first occurrence;
   two pseudo-concepts stand for any diagnosis and any genetic result. */
const SPECIAL = {'@any_diagnosis': 'any diagnosis', '@any_genetic': 'any genetic result'};
const eventLabel = id => SPECIAL[id] || label(id).toLowerCase();

/* Which occurrence: a variable, not a fixed "first". Occurrences are reconciled events, so a
   seizure entered twice by two sources counts once. "Last" means last recorded, and moves
   as follow-up grows. */
const OCCURRENCES = {first: 'first', last: 'last', nth: 'Nth', nth_last: 'Nth from last'};
const ordinal = n => `${n}${[11, 12, 13].includes(n % 100) ? 'th' : ({1: 'st', 2: 'nd', 3: 'rd'})[n % 10] || 'th'}`;
function occurrenceText(which, n) {
  n = Math.max(1, +n || 1);
  if (which === 'nth') return n === 1 ? 'first' : ordinal(n);
  if (which === 'nth_last') return n === 1 ? 'last recorded' : `${ordinal(n)}-to-last recorded`;
  return which === 'last' ? 'last recorded' : which === 'any' ? 'any' : 'first';
}
function pickOccurrence(sorted, which, n) {
  n = Math.max(1, +n || 1);
  if (which === 'first') return sorted.slice(0, 1);
  if (which === 'last') return sorted.slice(-1);
  if (which === 'nth') return sorted.slice(n - 1, n);
  if (which === 'nth_last') return sorted.length >= n ? [sorted[sorted.length - n]] : [];
  return sorted;
}

function eventTimes(view, q, id) {
  const items = usable(view, {...q, knownByIndex: false}, {start: -Infinity, end: Infinity});
  let times;
  if (id === '@any_diagnosis') times = items.filter(i => i.kind === 'diagnosis').map(i => i.time.best.e);
  else if (id === '@any_genetic') times = items.filter(i => i.kind === 'genetic' && i.gene.history.length).map(i => i.gene.history[0].recorded);
  else {
    const kind = state.concepts[id]?.kind;
    if (kind === 'medication' || kind === 'drug_class') times = items.filter(i => i.kind === 'medication' && (i.concept === id || classesOf(i.concept).includes(id))).map(i => i.med.start.e);
    else if (kind === 'gene') times = items.filter(i => i.kind === 'genetic' && i.concept === id && i.gene.history.length).map(i => i.gene.history[0].recorded);
    else times = items.filter(i => (i.kind === 'phenotype' || i.kind === 'diagnosis') && isA(conceptFor(i, q), id)).map(i => i.time.best.e);
  }
  return times.sort((a, b) => a - b);
}

function eventDay(view, q, id, which = 'first', n = 1) {
  return pickOccurrence(eventTimes(view, q, id), which, n)[0] ?? null;
}

/* Resolve a patient's window. Returns {start, end} in days of age, or {reason} when it cannot be placed. */
function windowFor(view, q) {
  const resolve = (b, which) => {
    if (b.kind === 'birth') return {day: 0};
    if (b.kind === 'age') return {day: toDays(b.amount, b.unit)};
    if (b.kind === 'date') return {day: dayOf(view.p, b.date)};
    if (b.kind === 'event') {
      const d = eventDay(view, q, b.event, b.which, b.n);
      return d == null ? {reason: `no ${occurrenceText(b.which, b.n)} ${eventLabel(b.event)} recorded, so the window cannot ${which}`} : {day: d + toDays(b.offset, b.unit)};
    }
    return null;
  };
  const {start: s, end: e} = q.window;
  let start = s.kind === 'span' ? null : resolve(s, 'start');
  let end = e.kind === 'span' ? null : resolve(e, 'end');
  if (start?.reason) return start;
  if (end?.reason) return end;
  if (s.kind === 'span') start = {day: end.day - toDays(s.amount, s.unit)};
  if (e.kind === 'span') end = {day: start.day + toDays(e.amount, e.unit)};
  if (end.day < 0) return {reason: 'born after the window ends'};
  if (end.day <= start.day) return {reason: 'the window ends before it starts'};
  if (end.day > view.lastContact) return {reason: `not followed to the end of the window (last contact at ${ageText(view.lastContact)})`};
  return {start: Math.max(0, start.day), end: end.day};
}

function boundText(b, which) {
  if (b.kind === 'birth') return 'birth';
  if (b.kind === 'age') return `${amountText(b.amount, b.unit)} of age`;
  if (b.kind === 'date') return b.date;
  if (b.kind === 'event') {
    const m = +b.offset || 0, what = `the ${occurrenceText(b.which, b.n)} ${eventLabel(b.event)}`;
    return m ? `${amountText(Math.abs(m), b.unit)} ${m < 0 ? 'before' : 'after'} ${what}` : what;
  }
  return `${amountText(b.amount, b.unit)} ${which === 'start' ? 'before the end' : 'after the start'}`;
}
const windowText = w => `from ${boundText(w.start, 'start')} to ${boundText(w.end, 'end')}`;
const spanText = win => win ? `${ageText(win.start)} – ${ageText(win.end)}` : '—';

/* Trajectory: ordered steps with bounded gaps over the whole record */

/* Measurement conditions turn a series (a diary count, a weight) into events: the
   measurements where the value rises or falls by a factor against the mean of the one or two
   before it, or crosses a threshold. A change needs a measurement before it to compare with,
   so whether the series can answer at all is checked separately for each binding of the
   earlier steps. Values without a number are skipped, not read as zero. */
const CONDITIONS = {rise: 'rises to at least ×', fall: 'falls to at most ÷', above: 'is at least', below: 'is at most'};
const relative = cond => cond === 'rise' || cond === 'fall';

function measureEvents(series, cond, param) {
  const out = [], pts = series.filter(s => typeof s.value === 'number');
  pts.forEach((c, i) => {
    const when = dateOf(c.record.occurred.earliest);
    const show = `${c.value}${c.unit && c.unit !== '/month' ? ' ' + c.unit : ''}`;
    if (relative(cond)) {
      const prior = pts.slice(Math.max(0, i - 2), i).filter(s => c.day - s.day <= 75);
      if (!prior.length) return;
      const base = Math.max(1e-9, prior.reduce((t, s) => t + s.value, 0) / prior.length);
      const hit = cond === 'rise' ? c.value >= param * Math.max(1, base) : c.value <= base / param;
      if (hit) out.push({e: c.day, l: c.end ?? c.day, what: `${show} on ${when} (before: ${prior.map(s => s.value).join(', ')})`});
    } else if (cond === 'above' ? c.value >= param : c.value <= param) {
      out.push({e: c.day, l: c.end ?? c.day, what: `${show} on ${when}`});
    }
  });
  return out;
}

function measureObservable(series, cond, prev, within) {
  const pts = series.filter(s => typeof s.value === 'number');
  const window = pts.some(s => (s.end ?? s.day) >= prev.e && s.day <= prev.l + within);
  return window && (!relative(cond) || pts.some(s => s.day < prev.l && (s.end ?? s.day) >= prev.e - 75));
}

/* Timeline */

function laneOf(kind) { return {phenotype: 'phenotype', diagnosis: 'diagnosis', genetic: 'tests', procedure: 'tests', medication: 'medication'}[kind]; }

function worstCategory(cases) {
  const cats = cases.map(q => state.data.quality_kinds[q.kind].category);
  return cats.includes('incorrect') ? 'incorrect' : cats.length ? cats[0] : null;
}

function glyphShape(kind, cx, cy, r, attrs) {
  if (kind === 'diagnosis') return `<path ${attrs} d="M${cx} ${cy - r - 1}L${cx + r + 1} ${cy}L${cx} ${cy + r + 1}L${cx - r - 1} ${cy}Z"/>`;
  if (kind === 'genetic') return `<rect ${attrs} x="${cx - r}" y="${cy - r}" width="${2 * r}" height="${2 * r}" rx="1.5"/>`;
  if (kind === 'procedure') return `<path ${attrs} d="M${cx} ${cy - r - 1}L${cx + r + 1} ${cy + r}L${cx - r - 1} ${cy + r}Z"/>`;
  return `<circle ${attrs} cx="${cx}" cy="${cy}" r="${r}"/>`;
}

function timelineSVG(pid, opts) {
  const view = evidenceFor(pid);
  const W = Math.max(opts.width, 420), G = 116, R = 16;
  const offset = opts.offset || 0;
  const lo = opts.minDay, hi = opts.maxDay;
  const x = d => G + (Math.min(Math.max(d - offset, lo), hi) - lo) / (hi - lo) * (W - G - R);
  const truth = opts.lens === 'truth';
  let hiddenCount = 0;
  const items = (truth ? truthItems(pid) : view.items).filter(i => {
    if (hooks.hidden(i)) { hiddenCount += 1; return false; }
    if (truth) return true;
    return i.kind === 'medication' ? !!i.med.start : !!i.time.best;
  });
  const lanes = {};
  for (const lane of LANES) lanes[lane.key] = {rows: [], marks: []};
  const posOf = it => truth ? it.day : (it.kind === 'medication' ? it.med.start.l : it.time.best.e);
  for (const it of [...items].sort((a, b) => posOf(a) - posOf(b))) {
    const key = truth ? it.lane : laneOf(it.kind);
    const lane = lanes[key];
    if (key === 'medication') {
      let row = lane.rows.findIndex(r => r.concept === it.concept);
      if (row < 0) { lane.rows.push({concept: it.concept, last: 0}); row = lane.rows.length - 1; }
      lane.marks.push({it, row});
    } else {
      const px = x(posOf(it));
      let row = lane.rows.findIndex(r => px - r.last >= 14);
      if (row < 0) { if (lane.rows.length < 4) { lane.rows.push({last: -1e9}); row = lane.rows.length - 1; } else row = lane.rows.length - 1; }
      lane.rows[row].last = px;
      lane.marks.push({it, row});
    }
  }
  const tops = {};
  let y = 40;
  for (const lane of LANES) {
    const rows = Math.max(1, lanes[lane.key].rows.length);
    const h = lane.fixed || (lane.key === 'medication' ? rows * 15 + 10 : rows * 15 + 12);
    tops[lane.key] = {y, h};
    y += h + 2;
  }
  const H = y + 4;
  const out = [`<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${esc(opts.title || 'Timeline')}">`,
    `<defs><pattern id="hatch-${pid}" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" stroke="#CBD5DF" stroke-width="2"/></pattern></defs>`];
  LANES.forEach((lane, i) => {
    const t = tops[lane.key];
    out.push(`<rect x="${G - 6}" y="${t.y}" width="${W - G + 6 - R + 8}" height="${t.h}" fill="${i % 2 ? '#FFFFFF' : '#F8FAFC'}" pointer-events="none"/>`,
             `<text class="lane-label" x="0" y="${t.y + 14}">${esc(lane.label)}</text>`);
  });
  const win = opts.window?.end != null ? opts.window : null;
  if (win) {
    out.push(`<rect x="${x(win.start)}" y="36" width="${Math.max(0, x(win.end) - x(win.start))}" height="${H - 40}" fill="#E8EFFB" opacity=".7" pointer-events="none"/>`);
  }
  const lc = view.lastContact;
  if (lc - offset < hi) {
    out.push(`<rect x="${x(lc)}" y="36" width="${Math.max(0, W - R - x(lc))}" height="${H - 40}" fill="url(#hatch-${pid})" opacity=".55" pointer-events="none"/>`,
             `<line x1="${x(lc)}" x2="${x(lc)}" y1="22" y2="${H}" stroke="#8592A0" stroke-dasharray="3 3"/>`,
             `<text class="region-label" x="${x(lc) + 3}" y="${H - 4}">last contact</text>`);
  }
  // axis
  const span = hi - lo, step = span <= 1100 ? 182.6 : span <= 2600 ? 365.25 : 730.5;
  out.push('<g class="axis">');
  for (let t = Math.ceil(lo / step) * step; t <= hi; t += step) {
    const px = G + (t - lo) / span * (W - G - R);
    out.push(`<line x1="${px}" x2="${px}" y1="22" y2="${H}" stroke-dasharray="1 4"/>`,
             `<text x="${px}" y="14" text-anchor="middle">${opts.anchor ? relText(t, '0') : ageText(t)}</text>`);
  }
  out.push('</g>');
  if (win) {
    // Labels spread outward from the window so a short window does not stack them.
    const xs = x(win.start), xe = x(win.end), room = 112;
    const endRight = xe + room < W;
    out.push(`<line x1="${xe}" x2="${xe}" y1="22" y2="${H}" stroke="#174EA6" stroke-width="2"/>`,
             `<text class="index-label" x="${endRight ? xe + 4 : xe - 4}" y="31" text-anchor="${endRight ? 'start' : 'end'}">window end · ${esc(ageText(win.end))}</text>`);
    if (win.start > 0) {
      const left = xs - G >= room, inside = !endRight ? false : xe - xs >= room;
      out.push(`<line x1="${xs}" x2="${xs}" y1="22" y2="${H}" stroke="#174EA6" stroke-width="2"/>`,
               `<text class="index-label" x="${left ? xs - 4 : xs + 4}" y="${left || inside ? 31 : 22}" text-anchor="${left ? 'end' : 'start'}">window start · ${esc(ageText(win.start))}</text>`);
    }
  }
  // marks
  for (const lane of ['phenotype', 'diagnosis', 'tests', 'medication']) {
    const t = tops[lane];
    for (const {it, row} of lanes[lane].marks) {
      const cy = t.y + 11 + row * 15;
      const color = COLOR[lane];
      const selected = opts.selected === it.id;
      if (truth) {
        const recorded = !!it.recorded;
        const px = x(it.day);
        const tip = `${it.display} · happened at ${ageText(it.day)}${recorded ? '' : ' · not in any record'}${it.day > lc ? ' · after last contact' : ''}`;
        let extra = '';
        if (recorded && it.recorded.time.best && Math.abs(it.recorded.time.best.e - it.day) > 10 && it.kind !== 'medication') {
          extra = `<line x1="${px}" x2="${x(it.recorded.time.best.e)}" y1="${cy}" y2="${cy}" stroke="#B42318" stroke-dasharray="2 2"/>`;
        }
        if (it.kind === 'medication') {
          const end = x(Math.min(it.end ?? view.snapshot, view.snapshot));
          out.push(`<g class="ev" data-tip="${esc(tip)}"><rect x="${px}" y="${cy - 4}" width="${Math.max(3, end - px)}" height="8" rx="3" fill="${recorded ? color : '#fff'}" stroke="${color}" ${recorded ? '' : 'stroke-dasharray="3 2"'} opacity=".85"/></g>`);
        } else {
          out.push(`<g class="ev" data-tip="${esc(tip)}">${extra}${glyphShape(it.kind, px, cy, 5, `class="glyph" fill="${recorded ? color : '#fff'}" stroke="${recorded ? color : '#8592A0'}" stroke-width="1.5" ${recorded ? '' : 'stroke-dasharray="2 2"'}`)}</g>`);
        }
        continue;
      }
      const cat = worstCategory(it.cases);
      const dot = cat ? `<circle cx="{DX}" cy="${cy - 6}" r="3.3" fill="${cat === 'incorrect' ? '#B42318' : '#C27C0E'}" stroke="#fff" stroke-width="1"/>` : '';
      const fill = it.freeTextOnly ? '#fff' : color;
      const dash = it.possibleOnly ? 'stroke-dasharray="2 2"' : '';
      const tip = `${it.display} · ${timeTip(it)}${it.cases.length ? ` · ${it.cases.length} data-quality case${it.cases.length > 1 ? 's' : ''}` : ''}`;
      const aria = `${it.display}, ${timeTip(it)}`;
      const g = [`<g class="ev${selected ? ' selected' : ''}" data-id="${esc(it.id)}" data-pid="${pid}" tabindex="${opts.selectable ? 0 : -1}" role="button" aria-label="${esc(aria)}" data-tip="${esc(tip)}">`];
      if (lane === 'medication') {
        const m = it.med, s0 = x(m.start.e), s1 = x(m.start.l);
        const endE = m.end ? x(m.end.e) : x(lc), endL = m.end ? x(m.end.l) : x(lc);
        if (s1 - s0 > 2) g.push(`<line x1="${s0}" x2="${s1}" y1="${cy}" y2="${cy}" stroke="${color}" stroke-width="3" stroke-dasharray="2 2"/>`);
        g.push(`<rect class="glyph" x="${s1}" y="${cy - 4.5}" width="${Math.max(3, endE - s1)}" height="9" rx="3" fill="${fill}" stroke="${color}" ${dash}/>`);
        if (m.end && endL - endE > 2) g.push(`<line x1="${endE}" x2="${endL}" y1="${cy}" y2="${cy}" stroke="${color}" stroke-width="3" stroke-dasharray="2 2"/>`);
        if (m.ongoing) g.push(`<path d="M${endE} ${cy - 5}L${endE + 6} ${cy}L${endE} ${cy + 5}Z" fill="${color}"/>`);
        g.push(`<rect class="focus-ring" x="${s0 - 3}" y="${cy - 7.5}" width="${Math.max(8, endE - s0 + 6)}" height="15" rx="5"/>`);
        const name = it.display.split(' (')[0];
        if (endE - s1 > name.length * 5.6 + 6) g.push(`<text x="${s1 + 4}" y="${cy + 3.5}" font-size="9.5" fill="#fff">${esc(name)}</text>`);
        else g.push(`<text x="${Math.max(endE, endL) + 8}" y="${cy + 3.5}" font-size="9.5" fill="${color}">${esc(name)}</text>`);
        g.push(dot.replace('{DX}', s1 + 2));
      } else {
        const px = x(it.time.best.e);
        if (it.time.env && x(it.time.env.l) - x(it.time.env.e) > 4) {
          g.push(`<line x1="${x(it.time.env.e)}" x2="${x(it.time.env.l)}" y1="${cy}" y2="${cy}" stroke="${it.time.conflicting ? '#B42318' : color}" stroke-width="2" opacity=".45" ${it.time.conflicting ? 'stroke-dasharray="3 2"' : ''}/>`);
        }
        g.push(glyphShape(it.kind, px, cy, 5, `class="glyph" fill="${fill}" stroke="${color}" stroke-width="1.6" ${dash}`));
        g.push(`<circle class="focus-ring" cx="${px}" cy="${cy}" r="9"/>`);
        g.push(dot.replace('{DX}', px + 6));
      }
      const tag = opts.highlight?.get(it.id);
      if (tag) {
        const hx = lane === 'medication' ? x(it.med.start.l) : x(it.time.best.e);
        g.push(`<circle cx="${hx}" cy="${cy}" r="10" fill="none" stroke="#C2410C" stroke-width="2.5"/>`,
               `<rect x="${hx - 8}" y="${cy - 25}" width="16" height="13" rx="3" fill="#C2410C"/><text x="${hx}" y="${cy - 15}" font-size="10" font-weight="700" fill="#fff" text-anchor="middle">${esc(tag)}</text>`);
      }
      g.push('</g>');
      out.push(g.join(''));
    }
  }
  // seizure frequency
  const f = tops.frequency, fs = view.series.seizure_frequency;
  if (fs.length) {
    const maxV = Math.max(4, ...fs.map(s => s.value));
    let path = '', prev = null;
    for (const s of fs) {
      const px = x(s.day + 15), py = f.y + f.h - 6 - (s.value / maxV) * (f.h - 14);
      path += (prev && s.day - prev.day < 45 ? 'L' : 'M') + `${px.toFixed(1)} ${py.toFixed(1)} `;
      prev = s;
    }
    out.push(`<g class="ev" data-id="__frequency" data-pid="${pid}" tabindex="${opts.selectable ? 0 : -1}" role="button" aria-label="Seizure diary, ${fs.length} months" data-tip="Parent diary: ${fs.length} months recorded, peak ${maxV} in a month">`,
             `<rect x="${G}" y="${f.y}" width="${W - G - R}" height="${f.h}" fill="transparent"/>`,
             `<path d="${path}" fill="none" stroke="#174EA6" stroke-width="1.6"/>`,
             `<text x="${W - R}" y="${f.y + 11}" font-size="9.5" fill="#6B7A89" text-anchor="end">max ${maxV}</text></g>`);
  } else {
    out.push(`<text x="${G}" y="${f.y + 18}" font-size="11" fill="#8592A0">No seizure diary</text>`);
  }
  for (const m of opts.markers || []) {
    const x0 = x(m.e), x1 = Math.max(x(m.l), x0 + 4);
    out.push(`<rect x="${x0}" y="${f.y + 2}" width="${x1 - x0}" height="${f.h - 4}" fill="none" stroke="#C2410C" stroke-width="2" rx="3"/>`,
             `<rect x="${x0}" y="${f.y - 11}" width="16" height="13" rx="3" fill="#C2410C"/><text x="${x0 + 8}" y="${f.y - 1}" font-size="10" font-weight="700" fill="#fff" text-anchor="middle">${esc(m.tag)}</text>`);
  }
  // weight
  const gt = tops.growth, ws = view.series.weight.filter(s => typeof s.value === 'number');
  if (ws.length) {
    const vals = ws.map(s => s.value), lo2 = Math.min(...vals), hi2 = Math.max(...vals);
    const py = v => gt.y + gt.h - 5 - (v - lo2) / ((hi2 - lo2) || 1) * (gt.h - 10);
    out.push(`<g class="ev" data-id="__growth" data-pid="${pid}" tabindex="${opts.selectable ? 0 : -1}" role="button" aria-label="Growth measurements" data-tip="Weight at clinic visits; red marks a known entry error">`,
             `<rect x="${G}" y="${gt.y}" width="${W - G - R}" height="${gt.h}" fill="transparent"/>`);
    for (const s of ws) {
      const bad = s.cases.some(q => state.data.quality_kinds[q.kind].category === 'incorrect');
      out.push(`<circle cx="${x(s.day)}" cy="${py(s.value)}" r="${bad ? 3.6 : 2.2}" fill="${bad ? '#B42318' : '#126B5D'}"/>`);
    }
    for (const s of [...view.series.height, ...view.series.head_circ].filter(s => s.cases.length)) {
      out.push(`<rect x="${x(s.day) - 1}" y="${gt.y + gt.h - 7}" width="2.5" height="7" fill="${worstCategory(s.cases) === 'incorrect' ? '#B42318' : '#C27C0E'}"/>`);
    }
    out.push('</g>');
  }
  // letters
  const lt = tops.letters;
  for (const l of view.letters) {
    const lang = l.record.language && l.record.language !== 'en' ? l.record.language.toUpperCase() : '';
    const cat = worstCategory(l.cases);
    const color = cat === 'incorrect' ? '#B42318' : lang || cat ? '#C27C0E' : COLOR.letters;
    out.push(`<g class="ev${opts.selected === 'n:' + l.record.record_id ? ' selected' : ''}" data-id="n:${esc(l.record.record_id)}" data-pid="${pid}" tabindex="${opts.selectable ? 0 : -1}" role="button" aria-label="${esc(l.record.label_as_recorded)} at ${ageText(l.day)}" data-tip="${esc(`${l.record.label_as_recorded} · ${dateOf(l.record.occurred.earliest)}${lang ? ' · ' + lang : ''}${l.cases.length ? ' · ' + l.cases.map(q => q.kind).join(', ') : ''}`)}">`,
             `<rect class="glyph" x="${x(l.day) - 1.5}" y="${lt.y + 5}" width="3" height="13" fill="${color}"/>`,
             `<rect class="focus-ring" x="${x(l.day) - 4}" y="${lt.y + 2}" width="8" height="19" rx="2"/>`,
             lang ? `<text x="${x(l.day) + 3}" y="${lt.y + 15}" font-size="8.5" font-weight="700" fill="#805400">${lang}</text>` : '', '</g>');
  }
  if (hiddenCount) out.push(`<text x="${W - R}" y="9" font-size="10.5" fill="#805400" text-anchor="end">${hiddenCount} event${hiddenCount > 1 ? 's' : ''} hidden from view</text>`);
  out.push('</svg>');
  return out.join('');
}

function timeTip(it) {
  if (it.kind === 'medication') {
    const m = it.med;
    return `started ${ageText(m.start.e)}${m.start.l - m.start.e > 2 ? '–' + ageText(m.start.l) : ''}${m.end ? `, stopped ~${ageText(m.end.e)}` : ', ongoing'}`;
  }
  const b = it.time.best;
  return `${ageText(b.e)}${b.l - b.e > 2 ? '–' + ageText(b.l) : ''}${it.time.conflicting ? ' (sources disagree)' : ''}`;
}

function freshBound(kind, which) {
  return {birth: {kind: 'birth'}, age: {kind: 'age', amount: which === 'start' ? 6 : 24, unit: 'month'}, date: {kind: 'date', date: '2021-01-01'},
          event: {kind: 'event', event: 'seizure', which: 'first', n: 2, offset: 0, unit: 'month'}, span: {kind: 'span', amount: 24, unit: 'month'}}[kind];
}

function boundEditor(which, b, other) {
  const kinds = [['birth', 'at birth'], ['age', 'at an age'], ['date', 'on a date'], ['event', 'at an event'],
                 ['span', which === 'start' ? 'a length of time before the end' : 'a length of time after the start']];
  const sel = `<select data-action="bound-kind" data-which="${which}" aria-label="Window ${which}">${kinds
    .filter(([k]) => !(k === 'span' && other.kind === 'span')).filter(([k]) => !(k === 'birth' && which === 'end'))
    .map(([k, t]) => `<option value="${k}" ${b.kind === k ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select>`;
  const num = (field, value, label) => `<input type="number" step="any" value="${value ?? 0}" data-action="bound-field" data-which="${which}" data-field="${field}" aria-label="${label}">`;
  const unit = unitSelect(b.unit, `data-action="bound-field" data-which="${which}" data-field="unit"`);
  let extra = '';
  if (b.kind === 'age' || b.kind === 'span') extra = `<span class="inline">${num('amount', b.amount, 'Amount')}${unit}</span>`;
  if (b.kind === 'date') extra = `<input type="date" value="${esc(b.date)}" data-action="bound-field" data-which="${which}" data-field="date" aria-label="Date">`;
  if (b.kind === 'event') {
    const occ = `<select data-action="bound-field" data-which="${which}" data-field="which" aria-label="Which occurrence">${Object.entries(OCCURRENCES)
      .map(([k, t]) => `<option value="${k}" ${(b.which || 'first') === k ? 'selected' : ''}>${t}</option>`).join('')}</select>`
      + (b.which === 'nth' || b.which === 'nth_last' ? `<span class="inline">N = ${num('n', b.n || 2, 'Which occurrence number')}</span>` : '');
    extra = `<span class="bound-event">${occ} ${combo({value: b.event, kinds: ['phenotype', 'diagnosis', 'medication', 'drug_class', 'gene', 'special'], action: 'bound-field', data: {which, field: 'event'}})}</span>`
      + `<span class="inline">offset ${num('offset', b.offset, 'Offset (negative is before)')}${unit}</span>`;
  }
  return `<div class="row bound"><span class="bound-label">${which === 'start' ? 'Starts' : 'Ends'}</span>${sel}${extra}</div>`;
}

/* Terminology search: concepts by name, synonym-like display text, or code in any system the
   table holds (ICD-10, ICD-10-CM, SNOMED CT, ORPHA, OMIM, HPO, ATC, LOINC, HGNC, local).
   Choosing a concept also matches its narrower concepts. */

const KIND_LABEL = {phenotype: 'Finding', diagnosis: 'Diagnosis', medication: 'Medicine', drug_class: 'Medicine class',
  gene: 'Gene', measurement: 'Measurement', special: 'Any'};
let SEARCH = null;

function searchIndex() {
  if (SEARCH) return SEARCH;
  SEARCH = Object.entries(state.concepts).filter(([, c]) => KIND_LABEL[c.kind]).map(([id, c]) => ({
    id, label: c.label, kind: c.kind, codings: c.codings,
    narrower: Object.keys(state.concepts).filter(x => x !== id && isA(x, id)),
  })).concat(Object.entries(SPECIAL).map(([id, t]) => ({id, label: t[0].toUpperCase() + t.slice(1), kind: 'special', codings: [], narrower: []})));
  return SEARCH;
}

function searchConcepts(query, kinds) {
  const q = query.trim().toLowerCase();
  const pool = searchIndex().filter(e => kinds.includes(e.kind));
  if (!q) return pool.slice().sort((a, b) => a.label.localeCompare(b.label)).slice(0, 12).map(e => ({e, hit: null}));
  const scored = [];
  for (const e of pool) {
    let score = 0, hit = null;
    for (const c of e.codings) {
      const code = c.code.toLowerCase(), bare = code.replace(/^[a-z]+:/, '');
      if (code === q || bare === q) { score = 100; hit = c; break; }
      if ((code.startsWith(q) || bare.startsWith(q)) && score < 80) { score = 80; hit = c; }
      if (c.display.toLowerCase().includes(q) && score < 40) { score = 40; hit = c; }
    }
    const name = e.label.toLowerCase();
    if (name.startsWith(q)) score = Math.max(score, 70);
    else if (name.split(/[\s-]+/).some(w => w.startsWith(q))) score = Math.max(score, 55);
    else if (name.includes(q)) score = Math.max(score, 35);
    if (score) scored.push({e, hit, score});
  }
  return scored.sort((a, b) => b.score - a.score || a.e.label.localeCompare(b.e.label)).slice(0, 10);
}

const entryFor = id => searchIndex().find(e => e.id === id);

function combo({value, kinds, action, data = {}, placeholder = 'Search by name or code'}) {
  const e = value ? entryFor(value) : null;
  const attrs = Object.entries(data).map(([k, v]) => `data-${k}="${esc(v)}"`).join(' ');
  return `<span class="combo"><input type="text" class="combo-input" role="combobox" aria-autocomplete="list" aria-expanded="false" autocomplete="off" spellcheck="false"
    value="${esc(e ? e.label : '')}" data-selected="${esc(value || '')}" placeholder="${esc(placeholder)}" data-combo-action="${action}" data-kinds="${kinds.join(',')}" ${attrs} aria-label="${esc(placeholder)}"><span class="combo-list" role="listbox" hidden></span></span>`;
}

function comboCodes(id, max = 4) {
  const e = entryFor(id);
  if (!e) return '';
  const codes = e.codings.slice(0, max).map(c => `<code>${esc(codeText(c))}</code>`).join(' ');
  const more = e.narrower.length ? ` · includes ${e.narrower.slice(0, 3).map(label).join(', ')}${e.narrower.length > 3 ? ` and ${e.narrower.length - 3} more` : ''}` : '';
  return `<span class="combo-codes">${esc(KIND_LABEL[e.kind])}${codes ? ' · ' + codes : ''}${esc(more)}</span>`;
}

function wireCombos(onSelect) {
  let active = -1;
  const listOf = input => input.parentElement.querySelector('.combo-list');
  const show = input => {
    const results = searchConcepts(input.value === (entryFor(input.dataset.selected)?.label || '') ? '' : input.value, input.dataset.kinds.split(','));
    const list = listOf(input);
    active = results.length ? 0 : -1;
    list.innerHTML = results.map(({e, hit}, i) => `<span class="combo-option${i === active ? ' active' : ''}" role="option" id="opt-${esc(e.id)}" data-id="${esc(e.id)}" aria-selected="${i === active}">
      <strong>${esc(e.label)}</strong> <small>${esc(KIND_LABEL[e.kind])}${hit ? ` · ${esc(codeText(hit))}` : e.codings[0] ? ` · ${esc(codeText(e.codings[0]))}` : ''}${e.narrower.length ? ` · +${e.narrower.length} narrower` : ''}</small></span>`).join('')
      || '<span class="combo-empty">No concept matches. Try another name or a code such as G40, HP:0001250 or N03AF01.</span>';
    list.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  };
  const hide = input => { listOf(input).hidden = true; input.setAttribute('aria-expanded', 'false'); };
  const choose = (input, id) => { hide(input); onSelect(input.dataset.comboAction, {dataset: {...input.dataset}, value: id}); };
  const move = (input, by) => {
    const opts = [...listOf(input).querySelectorAll('.combo-option')];
    if (!opts.length) return;
    active = (active + by + opts.length) % opts.length;
    opts.forEach((o, i) => { o.classList.toggle('active', i === active); o.setAttribute('aria-selected', String(i === active)); });
    input.setAttribute('aria-activedescendant', opts[active].id);
    opts[active].scrollIntoView({block: 'nearest'});
  };
  document.addEventListener('focusin', e => { if (e.target.matches('.combo-input')) { e.target.select(); show(e.target); } });
  document.addEventListener('input', e => { if (e.target.matches('.combo-input')) show(e.target); });
  document.addEventListener('keydown', e => {
    const input = e.target.closest?.('.combo-input');
    if (!input) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); move(input, e.key === 'ArrowDown' ? 1 : -1); }
    else if (e.key === 'Enter') {
      e.preventDefault();
      const opt = listOf(input).querySelectorAll('.combo-option')[active];
      if (opt) choose(input, opt.dataset.id);
    } else if (e.key === 'Escape') { e.stopPropagation(); input.value = entryFor(input.dataset.selected)?.label || ''; hide(input); }
  }, true);
  document.addEventListener('mousedown', e => {
    const opt = e.target.closest('.combo-option');
    if (opt) { e.preventDefault(); choose(opt.closest('.combo').querySelector('.combo-input'), opt.dataset.id); }
  });
  document.addEventListener('focusout', e => {
    if (!e.target.matches('.combo-input')) return;
    const input = e.target;
    setTimeout(() => { if (document.activeElement !== input) { input.value = entryFor(input.dataset.selected)?.label || ''; hide(input); } }, 120);
  });
}
