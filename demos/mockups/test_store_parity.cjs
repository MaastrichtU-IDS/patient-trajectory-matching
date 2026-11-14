/* The trajectory page and the execution store agree on queries the store can express.

   Stage 1 of moving the mockups onto the store (decision D29), extended in D32. For trajectories
   the page can express in the store's language (concept steps with any, first, last, Nth or
   Nth-from-last; "at least N times within a span or the index period"; after and during; the
   page's index windows; absolute value conditions; the page's relaxations), translated by
   trajectory/store_request.js, which refuses every other feature by name, each patient is
   matched twice:
   - page: the page's own candidate resolution (core.js and trajectory/app.js, in a stub DOM),
     with the trajectory-graph engine deciding timing and relaxations, as the page does when
     served by server.py;
   - store: the synthetic cohort built into an execution store by the converter emitting series
     (store_parity.py), queried with execution_store.match over the same windows.
   The two runs share the engine, so every difference comes from candidate selection or from
   the store's comparability rule. Each one must be explained by a named cause in EXPECTED,
   per trajectory and patient; an unexplained difference, or an explained one that no longer
   occurs, fails. Run after demos/mockups/synthetic/generate.py, with DuckDB installed
   (patterns/requirements-store.lock.txt). */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');

const here = __dirname, root = path.resolve(here, '../..');
const python = process.env.PYTHON || 'python';
const cohort = path.join(here, 'data/synthetic-paediatric-rare.json');
const run = (args, input) => execFileSync(python, args, {cwd: root, input, encoding: 'utf8', maxBuffer: 1 << 28});
const post = body => JSON.parse(run(['-c',
  "import json,sys; from patterns import trajectory_graph as t; d=json.load(sys.stdin); print(json.dumps(t.run(d['query'], d['patients'])))"],
  JSON.stringify(body)));

const stub = {addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; }};
const context = vm.createContext({document: stub, addEventListener() {}, console, structuredClone, POST: post,
                                  DATA: fs.readFileSync(cohort, 'utf8')});
const page = fs.readFileSync(path.join(here, 'trajectory/app.js'), 'utf8').replace(/\nmain\(\);\s*$/, '\n');
const translator = fs.readFileSync(path.join(here, 'trajectory/store_request.js'), 'utf8');
vm.runInContext(fs.readFileSync(path.join(here, 'shared/core.js'), 'utf8') + '\n' + page + '\n' + translator + `
  state.data = JSON.parse(DATA); state.concepts = state.data.concepts;
  for (const [cid, c] of Object.entries(state.concepts)) for (const coding of c.codings) state.codeIndex.set(coding.system + '|' + coding.code, cid);
  for (const p of state.data.patients) state.patients.set(p.patient_id, p);
  // The page's own setting: coded records and findings that letters state about the patient.
  state.freeText = 'present';
  const window = {start: {kind: 'event', event: 'seizure', which: 'first', n: 2, offset: 0, unit: 'month'}, end: {kind: 'span', amount: 24, unit: 'month'}};
  var CASES = {
    'febrile seizure, then a sodium-channel blocker': {scope: 'from_start', window,
      nodes: [{id: 'A', concept: 'febrile_seizure', occ: 'first'}, {id: 'B', concept: 'sodium_channel_blocker', occ: 'first'}],
      edges: [{from: 'A', to: 'B', rel: 'after', min: 0, max: 12, unit: 'month'}]},
    'first seizure branching to a blocker and to stiripentol': {scope: 'from_start', window,
      nodes: [{id: 'A', concept: 'seizure', occ: 'first'}, {id: 'B', concept: 'sodium_channel_blocker', occ: 'first'}, {id: 'C', concept: 'stiripentol', occ: 'any'}],
      edges: [{from: 'A', to: 'B', rel: 'after', min: 0, max: 12, unit: 'month'}, {from: 'A', to: 'C', rel: 'after', min: 0, max: 18, unit: 'month'}]},
    'a seizure while taking the first blocker': {scope: 'any', window,
      nodes: [{id: 'A', concept: 'sodium_channel_blocker', occ: 'first'}, {id: 'B', concept: 'seizure', occ: 'any'}],
      edges: [{from: 'A', to: 'B', rel: 'during', grace: 30, unit: 'day'}]},
    'Dravet syndrome, then valproate': {scope: 'any', window: {start: {kind: 'birth'}, end: {kind: 'age', amount: 6, unit: 'year'}},
      nodes: [{id: 'A', concept: 'dravet', occ: 'first'}, {id: 'B', concept: 'valproate', occ: 'first'}],
      edges: [{from: 'A', to: 'B', rel: 'after', min: -24, max: 24, unit: 'month'}]},
    'levetiracetam, then the last recorded seizure': {scope: 'any', window,
      nodes: [{id: 'A', concept: 'levetiracetam', occ: 'first'}, {id: 'B', concept: 'seizure', occ: 'last'}],
      edges: [{from: 'A', to: 'B', rel: 'after', min: 0, max: 36, unit: 'month'}]},
    'status epilepticus within 6 months of the period start': {scope: 'within', window,
      nodes: [{id: 'A', concept: 'status_epilepticus', occ: 'any'}],
      edges: [{from: 'START', to: 'A', rel: 'after', min: 0, max: 6, unit: 'month'}]},
    'two seizures then a febrile seizure': {scope: 'from_start', window,
      nodes: [{id: 'A', concept: 'seizure', occ: 'any'}, {id: 'B', concept: 'seizure', occ: 'any'}, {id: 'C', concept: 'febrile_seizure', occ: 'any'}],
      edges: [{from: 'A', to: 'B', rel: 'after', min: 1, max: 24, unit: 'month'}, {from: 'B', to: 'C', rel: 'after', min: 1, max: 24, unit: 'month'}]},
    'a blocker, then at least 8 seizures a month within 60 days': {scope: 'from_start', window,
      nodes: [{id: 'A', concept: 'sodium_channel_blocker', occ: 'first'}, {id: 'B', concept: 'seizure_frequency', cond: 'above', param: 8}],
      edges: [{from: 'A', to: 'B', rel: 'after', min: 0, max: 60, unit: 'day'}]},
    // A threshold equal to recorded values (whole monthly counts), so reading at most as below changes many results.
    'at most 2 seizures a month inside the period': {scope: 'within', window,
      nodes: [{id: 'A', concept: 'seizure_frequency', cond: 'below', param: 2}], edges: []},
    'weight at most 9 kg inside the period': {scope: 'within', window,
      nodes: [{id: 'A', concept: 'weight', cond: 'below', param: 9}], edges: []},
    // D32: Nth occurrences and counts, over coded records only (the page's "coded only" setting; the store refuses
    // them over stated free text).
    'the event-frequency template': {scope: 'from_start', window, freeText: 'off',
      nodes: TEMPLATES.frequency.nodes, edges: TEMPLATES.frequency.edges},
    'the second seizure, then a sodium-channel blocker': {scope: 'from_start', window, freeText: 'off',
      nodes: [{id: 'A', concept: 'seizure', occ: 'nth', n: 2}, {id: 'B', concept: 'sodium_channel_blocker', occ: 'first'}],
      edges: [{from: 'A', to: 'B', rel: 'after', min: 0, max: 12, unit: 'month'}]},
    'the second-to-last seizure, then levetiracetam': {scope: 'any', window, freeText: 'off',
      nodes: [{id: 'A', concept: 'seizure', occ: 'nth_last', n: 2}, {id: 'B', concept: 'levetiracetam', occ: 'any'}],
      edges: [{from: 'A', to: 'B', rel: 'after', min: -12, max: 12, unit: 'month'}]},
    'at least 2 febrile seizures in the index period': {scope: 'within', window, freeText: 'off',
      nodes: [{id: 'A', concept: 'febrile_seizure', occ: 'count', n: 2, within: 'period'}], edges: []},
    // D33: the page's three rise templates, with the page's own window, scope and free-text setting. The tree and graph
    // templates carry a gene step, which the store refuses by name (asserted below), so they run here without that step
    // and its relations.
    'the sequence template (a rise)': {scope: 'from_start', window,
      nodes: TEMPLATES.sequence.nodes, edges: TEMPLATES.sequence.edges},
    'the tree template without its gene step': {scope: 'from_start', window,
      ...withoutNodes({nodes: TEMPLATES.tree.nodes, edges: TEMPLATES.tree.edges}, new Set(['B']))},
    'the graph template without its gene step': {scope: 'from_start', window,
      ...withoutNodes({nodes: TEMPLATES.graph.nodes, edges: TEMPLATES.graph.edges}, new Set(['E']))},
  };
  // Each case's free-text setting: the page's own default (stated findings) unless the case says otherwise.
  var withFreeText = function (name, fn) {
    const before = state.freeText;
    state.freeText = CASES[name].freeText || 'present';
    try { return fn(); } finally { state.freeText = before; }
  };
  // The page's query in the store's language (trajectory/store_request.js); what each step, and each relaxed alternative
  // of a step, binds by in the store, for explaining differences.
  var storeCase = function (name, def) {
    return withFreeText(name, () => {
      const pids = [...state.patients.keys()], byId = nodeById(def);
      const {query, conditions, windows} = storeRequest(def, state.freeText, pids);
      const own = (conditions || {steps: {}}).steps;
      const wanted = {};
      for (const n of def.nodes) wanted['step:' + n.id] = {concept: n.concept, ...(own[n.id] && own[n.id].unit ? {condition: own[n.id]} : {}),
        ...(own[n.id] && own[n.id].relative ? {relative: own[n.id].relative} : {})};
      for (const x of query.relaxations) if (x.kind === 'widen_concept') wanted[x.id] = {concept: x.concept};
      for (const x of (conditions || {}).relaxations || []) {
        if (x.kind === 'widen_value') wanted[x.id] = {concept: byId[x.step].concept, condition: x.condition};
        else if (x.kind === 'widen_factor') wanted[x.id] = {concept: byId[x.step].concept, relative: {...own[x.step].relative, factor: x.factor}};
        else if (x.concept) wanted[x.id] = {concept: x.concept};
      }
      return {name, query, conditions, windows, candidates: wanted};
    });
  };
  var refusalOf = function (def, freeText) {
    try { storeRequest(def, freeText, [...state.patients.keys()].slice(0, 1)); return null; } catch (e) { if (e.code) return e.code; throw e; }
  };
  // Each step's candidates as the page resolves them, and as the store holds them, unrelaxed and per relaxed
  // alternative: [start lo, start hi, end lo, end hi] in minutes from birth (a point ends when it starts; an open
  // end is null), with what produced each. The engine sees exactly these, so where they agree it cannot differ.
  var pageCandidates = function (name, pid, family) { return withFreeText(name, () => {
    const def = {...defaultDef(), ...CASES[name]}, view = evidenceFor(pid), byId = nodeById(def);
    const win = windowFor(view, {freeText: state.freeText, knownByIndex: false, window: def.window});
    const out = {};
    if (win.reason) return out;
    const grid = d => Number.isFinite(d) ? toGrid(d) : null;
    const add = (key, n, keys) => {
      out[key] = candidates(view, n, keys, win).map(c => ({t: [grid(c.e), grid(c.l), grid(c.point ? c.e : c.fe), grid(c.point ? c.l : c.fl)],
        mention: !!(c.item && !c.item.recs.length), what: c.what}));
    };
    // With "family", a derived step (Nth with N > 1, or a count) lists every event of its concept instead of the
    // picked one or the count's runs: what the store ranks or counts, before the rule that differs.
    const plain = n => family && (n.occ === 'count' || derivedOcc(n)) ? {...n, occ: 'any'} : n;
    for (const n of def.nodes) add('step:' + n.id, plain(n), new Set());
    for (const o of relaxOptions(def)) if (o.key.startsWith('node:')) add(o.key, plain(byId[o.key.slice(5)]), new Set([o.key]));
    return out;
  }); };
  var occurrenceOf = function (name, key) {
    const def = {...defaultDef(), ...CASES[name]}, n = nodeById(def)[key.split(':')[1]];
    const derived = n.occ === 'count' ? 'count' : derivedOcc(n) ? 'nth' : stepKind(n) === 'measure' && relative(n.cond) ? 'relative' : null;
    const occ = stepKind(n) === 'measure' || derived ? 'any' : n.occ === 'nth' ? 'first' : n.occ === 'nth_last' ? 'last' : (n.occ || 'any');
    return {kind: stepKind(n), occ, derived};
  };
  var pageResults = function (name) { return withFreeText(name, () => {
    const steps = engineSteps({...defaultDef(), ...CASES[name]});
    let step = steps.next();
    while (!step.done) step = steps.next(POST(step.value));
    return step.value;
  }); };`, context);

/* Why a patient's results differ, named from the candidates. Coverage first: the store reports not comparable when
   no source recording some step covers the period, which the page has no notion of except for measurements. Then,
   step by step, what the store holds that the page does not, or the reverse:
   - letter-mentions: the store holds each finding a letter states as its own event, at or before the letter (from
     birth to the letter, D30); the page merges a mention into the event it describes, or times it from the letter's
     own wording ("about 3 weeks ago");
   - record-reconciliation: the store holds every record as its own event; the page merges the records of one
     happening (grouped by the generator's ground truth, a shortcut the README declares) and times it by its most
     precise record, so the first, the last, and whether a time is certain can differ;
   - medication-records: the store's exposures run from each order to its stop, ongoing ones with an open end, and
     dispensings are separate points; the page builds one exposure per course from the order, else the first
     dispensing, ending at the stop or when the supply ran out, an ongoing one at last contact or later (a letter's
     mention of a medicine counts as letter-mentions);
   - measurement-records: the store reads every measured record with a unit; the page reads one value per happening;
   - order-uncertain (D30): a first or last step whose target the records leave uncertain is unresolved in the store
     when its end is read, another step can bind its events, or no sound refutation exists; the page binds the event
     its reconciled records date, without that test;
   - occurrence-envelope (D32): for an Nth step (N > 1) the store's time is the Nth occurrence's, ranging over every
     time the records allow (an envelope), unresolved when another step can bind the ranked events; the page binds the
     reconciled happening its most precise record ranks Nth, which is not sound under uncertain times (plan, 1.2);
   - count-timing (D32): for a count, the store binds the record at which the count is reached, certainly or only
     possibly over the records' ranges, with the two-pass rule (unresolved when it cannot decide); the page takes each
     run of N consecutive happenings, by their best dates, that may fit the span;
   - relative-baseline (D33): for a rise or fall, the store tags the recorded readings whose baseline (up to k = 2
     earlier readings of the item class within 75 days, by period start, strictly earlier, in the condition's unit,
     excluded readings undetermined) meets the condition, certainly or only possibly, with the two-pass rule; the page
     compares each reconciled happening's value with the one or two happenings before it by earliest day (same-day ones
     included, units ignored); the readings each side would bind are compared, and a difference is named so;
   - measurement-observability (D33): the page answers not comparable when its own, binding-specific check finds no
     measurement around the earlier step's bound event; the store has no such rule (declared page-only, plan 3.2).
   For a derived step, its family (every event of its concept the store counts, coded records only) is compared too;
   a difference there is named by the record-level causes above, and the derived rule is named whenever the results
   differ. */
const COVERAGE = ['NO_SOURCE_COVERAGE', 'NO_SOURCE_RECORDS_CONCEPT'];
function storeList(events, occ) {
  const t = e => [e[0], e[1], e[2] === null ? e[0] : e[2], e[2] === null ? e[1] : e[3]];
  const sorted = [...(events || [])].sort((a, b) => a[0] - b[0] || a[1] - b[1] || t(a)[2] - t(b)[2]
    || (t(a)[3] ?? Infinity) - (t(b)[3] ?? Infinity) || (a[4] < b[4] ? -1 : 1));
  // The engine's last is the greatest by latest start, then earliest start (D30).
  const latest = [...sorted].sort((a, b) => a[1] - b[1] || a[0] - b[0] || t(a)[2] - t(b)[2]
    || (t(a)[3] ?? Infinity) - (t(b)[3] ?? Infinity) || (a[4] < b[4] ? -1 : 1));
  const picked = occ === 'first' ? sorted.slice(0, 1) : occ === 'last' ? latest.slice(-1) : sorted;
  return picked.map(e => ({t: t(e), kind: e[5], id: e[4]}));
}
function causes(name, pid, pageStatus, storeResult, storeCands, pageReason) {
  if (COVERAGE.includes(storeResult.reason) && pageStatus === 'none') return ['coverage'];
  const page = vm.runInContext(`pageCandidates(${JSON.stringify(name)}, ${JSON.stringify(pid)}, true)`, context);
  const out = new Set();
  // D33: the page's own observability check for a measurement after an earlier step (binding-specific, page-only).
  if (pageStatus === 'not_comparable' && /do not cover the time around/.test(pageReason || '')) out.add('measurement-observability');
  // D30: the store leaves a first or last step undecided when its target is uncertain; the page does not test that.
  if (storeResult.reason === 'OCCURRENCE_ORDER_UNCERTAIN') out.add('order-uncertain');
  // D32: the converter's excluded records are its unitless measurements, which the page reads by value alone.
  if (storeResult.reason === 'ADMISSION_EXCLUDED_RECORDS') out.add('measurement-records');
  for (const [key, mine] of Object.entries(page)) {
    const {kind, occ, derived} = vm.runInContext(`occurrenceOf(${JSON.stringify(name)}, ${JSON.stringify(key)})`, context);
    if (derived && derived !== 'relative') out.add(derived === 'nth' ? 'occurrence-envelope' : 'count-timing');
    let rows = (storeCands[pid] || {})[key];
    if (derived) rows = (rows || []).filter(e => e[5] !== 'mention');  // the store ranks and counts coded records only
    const theirs = storeList(rows, occ);  // a relaxed alternative keeps the step's occurrence
    const sig = x => JSON.stringify(x.t);
    const a = mine.map(sig).sort(), b = theirs.map(sig).sort();
    if (JSON.stringify(a) === JSON.stringify(b)) continue;
    if (derived === 'relative') { out.add('relative-baseline'); continue; }
    if (kind === 'measure') { out.add('measurement-records'); continue; }
    const onlyStore = theirs.filter(x => !a.includes(sig(x))), onlyPage = mine.filter(x => !b.includes(sig(x)));
    if (onlyStore.some(x => x.kind === 'mention') || onlyPage.some(x => x.mention)) out.add('letter-mentions');
    const records = onlyStore.some(x => x.kind !== 'mention') || onlyPage.some(x => !x.mention);
    if (records) out.add(kind === 'medicine' ? 'medication-records' : 'record-reconciliation');
  }
  return out.size ? [...out].sort() : ['UNEXPLAINED'];
}

// The store: built once into a scratch directory.
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'store-parity-'));
const snapshot = run([path.join(here, 'store_parity.py'), 'build', cohort, path.join(scratch, 'store')]).trim();
const names = vm.runInContext('Object.keys(CASES)', context);
const cases = names.map(name => vm.runInContext(`storeCase(${JSON.stringify(name)}, {...defaultDef(), ...CASES[${JSON.stringify(name)}]})`, context));
if (process.env.STORE_PARITY_CASES) fs.writeFileSync(process.env.STORE_PARITY_CASES, JSON.stringify({cohort, cases}));  // diagnostics
const store = JSON.parse(run([path.join(here, 'store_parity.py'), 'match', snapshot], JSON.stringify({cohort, cases})));
fs.rmSync(scratch, {recursive: true, force: true});

const STATUS = {incomparable: 'not_comparable'};
const pageKey = r => [STATUS[r.status] || r.status, r.certainty || '', r.relaxKeys ? [...r.relaxKeys].sort().join('+') : ''].join(' ').trim();
// A combined relaxation of the store (`node:A+count:A`, D32) names the page's pair of relaxations.
const storeKey = r => [r.status, r.certainty || '', r.relaxations ? r.relaxations.flatMap(x => x.split('+')).sort().join('+') : ''].join(' ').trim();

const found = {}, summary = {}, unresolvedShare = {};
let patients = 0, agree = 0, matched = 0;
const replayMismatches = [];
// The store's excluded-records rule (D18) applies after the engine: it can turn none into not comparable and relaxed
// into unresolved, which the replay (the engine alone) does not see.
const EXCLUSION = {none: ['not_comparable'], relaxed: ['unresolved', 'not_comparable'], exact: ['not_comparable'],
                   possible: ['possible', 'not_comparable'], unresolved: ['not_comparable']};
for (const name of names) {
  const page = vm.runInContext(`pageResults(${JSON.stringify(name)})`, context);
  const tally = unresolvedShare[name] = {results: 0, unresolved: 0, reasons: {}, candidates: store[name + '#with_candidate'].length};
  found[name] = {};
  for (const r of page) {
    const a = pageKey(r), b = storeKey(store[name][r.pid]);
    patients += 1;
    if (/^(exact|possible|relaxed)/.test(b)) matched += 1;
    // The store's result is the engine's on the store's candidates, up to the store's comparability rule.
    const replayed = store[name + '#replayed'][r.pid], own = store[name][r.pid].status, reason = store[name][r.pid].reason;
    const excluded = reason === 'ADMISSION_EXCLUDED_RECORDS' && (EXCLUSION[replayed] || []).includes(own);
    if (!(replayed === own || (replayed === 'none' && own === 'not_comparable') || excluded)) replayMismatches.push(`${name} ${r.pid}: replayed ${replayed}, store ${own}`);
    if (store[name][r.pid].status !== 'excluded') tally.results += 1;
    if (own === 'unresolved') {
      tally.unresolved += 1; tally.reasons[reason] = (tally.reasons[reason] || 0) + 1;
      assert.ok(store[name + '#with_candidate'].includes(r.pid), `${name} ${r.pid}: unresolved without a candidate`);
    }
    if (a === b) { agree += 1; continue; }
    const why = causes(name, r.pid, STATUS[r.status] || r.status, store[name][r.pid], store[name + '#candidates'], r.reason);
    found[name][r.pid] = `${why.join('+')}: page ${a}, store ${b}`;
    for (const w of why) summary[w] = (summary[w] || 0) + 1;
  }
}
if (process.env.STORE_PARITY_PRINT) console.log(JSON.stringify(found, null, 1));

/* Every difference on the committed seed, with its cause. A new difference, a vanished one or a changed cause fails:
   regenerate the cohort, read the new causes, and update this table and the README together. */
const EXPECTED = {
  "febrile seizure, then a sodium-channel blocker": {
    C001: "letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C004: "letter-mentions+medication-records+record-reconciliation: page exact, store none",
    C008: "letter-mentions+medication-records+record-reconciliation: page relaxed exact node:A, store relaxed possible node:A",
    C015: "letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C021: "letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C023: "letter-mentions+medication-records: page exact, store possible",
    C036: "letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C038: "letter-mentions+medication-records+record-reconciliation: page relaxed exact edge:0+node:A, store relaxed possible node:A",
    C044: "letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C045: "letter-mentions+medication-records+record-reconciliation: page relaxed exact node:A, store relaxed possible node:A",
  },
  "first seizure branching to a blocker and to stiripentol": {
    C001: "letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C004: "letter-mentions+medication-records+record-reconciliation: page exact, store none",
    C015: "letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C021: "letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C023: "letter-mentions+medication-records: page exact, store possible",
    C036: "letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C044: "letter-mentions+medication-records+record-reconciliation: page exact, store possible",
  },
  "a seizure while taking the first blocker": {
    C001: "letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C004: "letter-mentions+medication-records+order-uncertain+record-reconciliation: page possible, store unresolved",
    C008: "letter-mentions+medication-records+order-uncertain+record-reconciliation: page none, store unresolved",
    C015: "letter-mentions+medication-records+order-uncertain+record-reconciliation: page possible, store unresolved",
    C021: "letter-mentions+medication-records+order-uncertain: page exact, store unresolved",
    C023: "letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C036: "letter-mentions+medication-records+order-uncertain+record-reconciliation: page none, store unresolved",
    C038: "letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C044: "letter-mentions+medication-records+order-uncertain+record-reconciliation: page none, store unresolved",
    C045: "letter-mentions+medication-records+order-uncertain: page exact, store unresolved",
  },
  "Dravet syndrome, then valproate": {
    C001: "letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C029: "letter-mentions+medication-records: page exact, store possible",
  },
  "levetiracetam, then the last recorded seizure": {
    C003: "letter-mentions+medication-records+record-reconciliation: page none, store possible",
    C008: "letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C010: "letter-mentions+medication-records+record-reconciliation: page none, store possible",
    C026: "letter-mentions+medication-records: page exact, store possible",
    C030: "letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C033: "letter-mentions+medication-records+record-reconciliation: page relaxed exact edge:0, store relaxed possible edge:0",
    C038: "letter-mentions+medication-records+record-reconciliation: page relaxed exact edge:0, store relaxed possible edge:0",
    C045: "letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C048: "letter-mentions+medication-records: page relaxed exact edge:0, store relaxed possible edge:0",
  },
  "status epilepticus within 6 months of the period start": {
    C002: "letter-mentions: page relaxed exact node:A, store relaxed possible node:A",
    C009: "letter-mentions: page relaxed exact node:A, store relaxed possible node:A",
    C023: "letter-mentions+record-reconciliation: page relaxed exact edge:0, store possible",
  },
  "two seizures then a febrile seizure": {
    C001: "letter-mentions+record-reconciliation: page relaxed exact node:C, store possible",
    C004: "letter-mentions+record-reconciliation: page relaxed possible node:C, store possible",
    C005: "letter-mentions+record-reconciliation: page none, store relaxed possible node:C",
    C008: "letter-mentions+record-reconciliation: page none, store relaxed possible node:C",
    C017: "letter-mentions+record-reconciliation: page relaxed exact node:C, store possible",
    C021: "letter-mentions: page relaxed exact node:C, store possible",
    C023: "letter-mentions+record-reconciliation: page relaxed exact node:C, store possible",
    C026: "letter-mentions: page none, store relaxed possible node:C",
    C027: "letter-mentions+record-reconciliation: page exact, store possible",
    C030: "letter-mentions: page none, store relaxed possible node:C",
    C031: "letter-mentions+record-reconciliation: page none, store possible",
    C036: "letter-mentions+record-reconciliation: page relaxed exact node:C, store possible",
    C044: "letter-mentions+record-reconciliation: page relaxed exact node:C, store possible",
    C045: "letter-mentions: page none, store relaxed possible node:C",
  },
  "a blocker, then at least 8 seizures a month within 60 days": {
    C001: "letter-mentions+medication-records: page exact, store possible",
    C002: "coverage: page none, store not_comparable",
    C003: "coverage: page none, store not_comparable",
    C005: "coverage: page none, store not_comparable",
    C006: "coverage: page none, store not_comparable",
    C010: "coverage: page none, store not_comparable",
    C015: "letter-mentions+medication-records: page exact, store possible",
    C019: "coverage: page none, store not_comparable",
    C020: "coverage: page none, store not_comparable",
    C023: "letter-mentions+medication-records: page exact, store possible",
    C025: "coverage: page none, store not_comparable",
    C026: "coverage: page none, store not_comparable",
    C028: "coverage: page none, store not_comparable",
    C030: "coverage: page none, store not_comparable",
    C031: "coverage: page none, store not_comparable",
    C033: "coverage: page none, store not_comparable",
    C035: "coverage: page none, store not_comparable",
    C036: "letter-mentions+medication-records: page exact, store possible",
    C040: "coverage: page none, store not_comparable",
    C041: "coverage: page none, store not_comparable",
    C042: "coverage: page none, store not_comparable",
    C043: "coverage: page none, store not_comparable",
    C045: "letter-mentions+medication-records: page exact, store possible",
    C048: "coverage: page none, store not_comparable",
  },
  "at most 2 seizures a month inside the period": {},
  "weight at most 9 kg inside the period": {
    C006: "measurement-records: page none, store not_comparable",
    C019: "measurement-records: page relaxed exact node:A, store unresolved  node:A",
    C022: "measurement-records: page none, store not_comparable",
    C028: "measurement-records: page none, store not_comparable",
  },
  "the event-frequency template": {
    C001: "count-timing+letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C004: "count-timing+letter-mentions+medication-records+record-reconciliation: page relaxed possible count:A, store possible",
    C008: "count-timing+letter-mentions+medication-records+record-reconciliation: page relaxed exact count:A, store possible",
    C015: "count-timing+letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C021: "count-timing+letter-mentions+medication-records: page relaxed exact count:A, store relaxed possible count:A",
    C023: "count-timing+letter-mentions+medication-records+record-reconciliation: page relaxed exact count:A, store unresolved  count:A",
    C036: "count-timing+letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C038: "count-timing+letter-mentions+medication-records+record-reconciliation: page relaxed exact count:A, store relaxed possible count:A",
    C044: "count-timing+letter-mentions+medication-records+record-reconciliation: page exact, store possible",
  },
  "the second seizure, then a sodium-channel blocker": {
    C001: "letter-mentions+medication-records+occurrence-envelope+record-reconciliation: page exact, store possible",
    C004: "letter-mentions+medication-records+occurrence-envelope+record-reconciliation: page possible, store none",
    C008: "letter-mentions+medication-records+occurrence-envelope+record-reconciliation: page exact, store possible",
    C015: "letter-mentions+medication-records+occurrence-envelope+record-reconciliation: page exact, store possible",
    C021: "letter-mentions+medication-records+occurrence-envelope: page exact, store possible",
    C023: "letter-mentions+medication-records+occurrence-envelope+record-reconciliation: page exact, store possible",
    C036: "letter-mentions+medication-records+occurrence-envelope+record-reconciliation: page exact, store possible",
    C038: "letter-mentions+medication-records+occurrence-envelope+record-reconciliation: page exact, store possible",
    C044: "letter-mentions+medication-records+occurrence-envelope+record-reconciliation: page exact, store possible",
  },
  "the second-to-last seizure, then levetiracetam": {
    C022: "letter-mentions+medication-records+occurrence-envelope+record-reconciliation: page none, store exact",
    C032: "letter-mentions+medication-records+occurrence-envelope+record-reconciliation: page none, store exact",
    C033: "letter-mentions+medication-records+occurrence-envelope: page none, store exact",
    C038: "letter-mentions+medication-records+occurrence-envelope+record-reconciliation: page none, store exact",
    C048: "letter-mentions+medication-records+occurrence-envelope: page none, store exact",
  },
  "at least 2 febrile seizures in the index period": {
    C005: "count-timing+record-reconciliation: page none, store unresolved  count:A+node:A",
    C010: "count-timing: page none, store relaxed exact count:A+node:A",
    C018: "count-timing+record-reconciliation: page none, store unresolved  count:A+node:A",
    C023: "count-timing+record-reconciliation: page relaxed exact count:A, store unresolved  count:A+node:A",
    C026: "count-timing: page none, store relaxed exact count:A+node:A",
    C031: "count-timing+record-reconciliation: page relaxed exact count:A, store unresolved  count:A",
    C034: "count-timing+record-reconciliation: page none, store unresolved  count:A+node:A",
    C035: "count-timing: page none, store relaxed exact count:A+node:A",
    C039: "count-timing+record-reconciliation: page none, store unresolved  count:A+node:A",
    C041: "count-timing+record-reconciliation: page none, store unresolved  count:A+node:A",
    C043: "count-timing+record-reconciliation: page none, store unresolved  count:A+node:A",
  },  "the sequence template (a rise)": {
    C002: "coverage: page none, store not_comparable",
    C003: "coverage: page none, store not_comparable",
    C005: "coverage: page none, store not_comparable",
    C006: "coverage: page none, store not_comparable",
    C008: "coverage: page none, store not_comparable",
    C010: "coverage: page none, store not_comparable",
    C015: "letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C019: "coverage: page none, store not_comparable",
    C020: "coverage: page none, store not_comparable",
    C025: "coverage: page none, store not_comparable",
    C026: "coverage: page none, store not_comparable",
    C028: "coverage: page none, store not_comparable",
    C030: "coverage: page none, store not_comparable",
    C031: "coverage: page none, store not_comparable",
    C033: "coverage: page none, store not_comparable",
    C035: "coverage: page none, store not_comparable",
    C036: "letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C038: "letter-mentions+medication-records+record-reconciliation: page none, store relaxed possible node:A+node:C",
    C040: "coverage: page none, store not_comparable",
    C041: "coverage: page none, store not_comparable",
    C042: "coverage: page none, store not_comparable",
    C043: "coverage: page none, store not_comparable",
    C048: "coverage: page none, store not_comparable",
  },
  "the tree template without its gene step": {
    C002: "coverage: page none, store not_comparable",
    C003: "coverage: page none, store not_comparable",
    C005: "coverage: page none, store not_comparable",
    C006: "coverage: page none, store not_comparable",
    C010: "coverage: page none, store not_comparable",
    C015: "letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C019: "coverage: page none, store not_comparable",
    C020: "coverage: page none, store not_comparable",
    C025: "coverage: page none, store not_comparable",
    C026: "coverage: page none, store not_comparable",
    C028: "coverage: page none, store not_comparable",
    C030: "coverage: page none, store not_comparable",
    C031: "coverage: page none, store not_comparable",
    C033: "coverage: page none, store not_comparable",
    C035: "coverage: page none, store not_comparable",
    C036: "letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C038: "letter-mentions+medication-records+record-reconciliation: page none, store relaxed possible node:D",
    C040: "coverage: page none, store not_comparable",
    C041: "coverage: page none, store not_comparable",
    C042: "coverage: page none, store not_comparable",
    C043: "coverage: page none, store not_comparable",
    C048: "coverage: page none, store not_comparable",
  },
  "the graph template without its gene step": {
    C002: "coverage: page none, store not_comparable",
    C003: "coverage: page none, store not_comparable",
    C005: "coverage: page none, store not_comparable",
    C006: "coverage: page none, store not_comparable",
    C008: "coverage: page none, store not_comparable",
    C010: "coverage: page none, store not_comparable",
    C015: "letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C019: "coverage: page none, store not_comparable",
    C020: "coverage: page none, store not_comparable",
    C023: "letter-mentions+medication-records+order-uncertain: page possible, store unresolved",
    C025: "coverage: page none, store not_comparable",
    C026: "coverage: page none, store not_comparable",
    C028: "coverage: page none, store not_comparable",
    C030: "coverage: page none, store not_comparable",
    C031: "coverage: page none, store not_comparable",
    C033: "coverage: page none, store not_comparable",
    C035: "coverage: page none, store not_comparable",
    C036: "letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C040: "coverage: page none, store not_comparable",
    C041: "coverage: page none, store not_comparable",
    C042: "coverage: page none, store not_comparable",
    C043: "coverage: page none, store not_comparable",
    C048: "coverage: page none, store not_comparable",
  },
};


assert.deepEqual(replayMismatches, [], 'the replayed candidates are not what the store matched on:\n' + replayMismatches.join('\n'));
for (const name of names) {
  const unexplained = Object.entries(found[name]).filter(([, why]) => why.startsWith('UNEXPLAINED'));
  assert.deepEqual(unexplained, [], `${name}: differences with no named cause`);
  // The tables come from the sandbox's realm, so compare their contents.
  assert.deepEqual(JSON.parse(JSON.stringify(found[name])), EXPECTED[name], `${name}: the differences changed`);
}
assert.deepEqual(Object.keys(EXPECTED).sort(), [...names].sort());
assert.ok(matched > 0, 'nothing matched in the store: the comparison would be vacuous');
// Every cause comes from the closed list the D32 rules fix.
const CAUSES = ['coverage', 'letter-mentions', 'record-reconciliation', 'medication-records', 'measurement-records',
                'order-uncertain', 'occurrence-envelope', 'count-timing', 'relative-baseline', 'measurement-observability'];
assert.deepEqual(Object.keys(summary).filter(c => !CAUSES.includes(c)), [], 'a cause outside the closed list');
// Every page feature the store does not express is refused by its code, never translated approximately.
const REFUSALS = vm.runInContext(`(() => {
  const d = name => ({...defaultDef(), ...clone(TEMPLATES[name])});
  const nthDuring = {...defaultDef(), nodes: [{id: 'A', concept: 'sodium_channel_blocker', occ: 'nth', n: 2}, {id: 'B', concept: 'seizure'}],
                     edges: [{from: 'A', to: 'B', rel: 'during', grace: 0, unit: 'day'}]};
  return {
    'rise (sequence template)': refusalOf(d('sequence'), 'off'),
    'rise (sequence template), stated free text': refusalOf(d('sequence'), 'present'),
    'gene step (tree template)': refusalOf(d('tree'), 'off'),
    'gene step and rise (graph template)': refusalOf(d('graph'), 'off'),
    'count over stated free text (frequency template)': refusalOf(d('frequency'), 'present'),
    'Nth over stated free text': refusalOf({...defaultDef(), nodes: [{id: 'A', concept: 'seizure', occ: 'nth', n: 2}], edges: []}, 'present'),
    'hedged free text': refusalOf(d('frequency'), 'possible'),
    'while taking the 2nd blocker': refusalOf(nthDuring, 'off'),
    'frequency template, coded only': refusalOf(d('frequency'), 'off'),
  };
})()`, context);
assert.deepEqual(JSON.parse(JSON.stringify(REFUSALS)), {
  'rise (sequence template)': null,  // D33: rises are translated
  'rise (sequence template), stated free text': null,
  'gene step (tree template)': 'GENE_CLASSIFICATION_UNSUPPORTED',
  'gene step and rise (graph template)': 'GENE_CLASSIFICATION_UNSUPPORTED',  // the rise is translated; the gene step is not
  'count over stated free text (frequency template)': 'FREE_TEXT_COUNT_UNSUPPORTED',
  'Nth over stated free text': 'FREE_TEXT_COUNT_UNSUPPORTED',
  'hedged free text': 'FREE_TEXT_HEDGED_UNSUPPORTED',
  'while taking the 2nd blocker': 'NTH_STEP_HAS_NO_END',
  'frequency template, coded only': null,
});
if (process.env.STORE_PARITY_PRINT) console.log(JSON.stringify(unresolvedShare, null, 1));
const share = names.map(n => `${n} ${unresolvedShare[n].unresolved}/${unresolvedShare[n].results}`
  + ` (${unresolvedShare[n].candidates} with a candidate)`).join('; ');
console.log(`Unresolved in the store, per trajectory: ${share}.`);
console.log(`Trajectory page and execution store: ${agree} of ${patients} patient results agree over ${names.length} trajectories `
  + `(${matched} store matches); ${patients - agree} differ, each by a named cause (${Object.entries(summary).sort().map(([k, v]) => `${k} ${v}`).join(', ')}); `
  + 'the store matched exactly the candidates the explanation reads.');
