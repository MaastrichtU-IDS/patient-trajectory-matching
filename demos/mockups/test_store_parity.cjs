/* The trajectory page and the execution store agree on queries the store can express.

   Stage 1 of moving the mockups onto the store (decision D29). For trajectories the page can
   express in the store's language (concept steps with any, first or last; after and during;
   the page's index windows; absolute value conditions; the page's relaxations), each patient
   is matched twice:
   - page: the page's own candidate resolution (core.js and trajectory/app.js, in a stub DOM),
     with the trajectory-graph engine deciding timing and relaxations, as the page does when
     served by server.py;
   - store: the synthetic cohort built into an execution store (store_parity.py), queried with
     execution_store.match over the same windows.
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
vm.runInContext(fs.readFileSync(path.join(here, 'shared/core.js'), 'utf8') + '\n' + page + `
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
  };
  const UNIT = {seizure_frequency: '/month', weight: 'kg', height: 'cm', head_circ: 'cm'};
  const decimal = x => { const s = String(x); if (!/^-?[0-9]+(\\.[0-9]+)?$/.test(s)) throw new Error('not a plain decimal: ' + s); return s; };
  // The page's query in the store's language. The relations and relaxation ids are the page's own engine request's;
  // each relaxed alternative becomes what it stands for: a parent concept, a drug class, or a wider value condition.
  var storeCase = function (name, def) {
    const pids = [...state.patients.keys()];
    const req = engineRequest(def, pids, true), q = req.body.query, byId = nodeById(def);
    const steps = def.nodes.map(n => ({id: n.id, concept: n.concept, occurrence: stepKind(n) === 'measure' ? 'any' : (n.occ || 'any')}));
    const conditions = {profile: 'execution-store-conditions-1.0', steps: {}, relaxations: []};
    const relaxations = [];
    for (const x of q.relaxations) {
      if (x.kind !== 'widen_concept') { relaxations.push(x); continue; }
      const n = byId[x.step], kind = stepKind(n);
      if (kind === 'occurrence') relaxations.push({...x, concept: state.concepts[n.concept].parents[0]});
      else if (kind === 'medicine') relaxations.push({...x, concept: classesOf(n.concept).find(k => k !== 'antiseizure')});
      else if (kind === 'measure') conditions.relaxations.push({id: x.id, kind: 'widen_value', step: n.id, cost: x.cost,
        condition: {unit: UNIT[n.concept], [n.cond === 'above' ? 'ge' : 'le']: decimal(relaxedParam(n))}});
      else throw new Error(name + ': no store form for ' + x.id);
    }
    for (const n of def.nodes) if (stepKind(n) === 'measure') {
      if (relative(n.cond)) throw new Error(name + ': rises and falls are not expressible in the store');
      conditions.steps[n.id] = {unit: UNIT[n.concept], [n.cond === 'above' ? 'ge' : 'le']: decimal(n.param)};
    }
    const windows = {};
    for (const pid of pids) { const w = req.windows.get(pid); windows[pid] = w.reason ? null : {start: toGrid(w.start), end: toGrid(w.end)}; }
    // What each step, and each relaxed alternative of a step, binds by in the store: for explaining differences.
    const wanted = {};
    for (const n of def.nodes) wanted['step:' + n.id] = {concept: n.concept, ...(conditions.steps[n.id] ? {condition: conditions.steps[n.id]} : {})};
    for (const x of relaxations) if (x.kind === 'widen_concept') wanted[x.id] = {concept: x.concept};
    for (const x of conditions.relaxations) wanted[x.id] = {concept: byId[x.step].concept, condition: x.condition};
    return {name, query: {...q, steps, relaxations}, conditions: Object.keys(conditions.steps).length ? conditions : null, windows, candidates: wanted};
  };
  // Each step's candidates as the page resolves them, and as the store holds them, unrelaxed and per relaxed
  // alternative: [start lo, start hi, end lo, end hi] in minutes from birth (a point ends when it starts; an open
  // end is null), with what produced each. The engine sees exactly these, so where they agree it cannot differ.
  var pageCandidates = function (name, pid) {
    const def = {...defaultDef(), ...CASES[name]}, view = evidenceFor(pid), byId = nodeById(def);
    const win = windowFor(view, {freeText: state.freeText, knownByIndex: false, window: def.window});
    const out = {};
    if (win.reason) return out;
    const grid = d => Number.isFinite(d) ? toGrid(d) : null;
    const add = (key, n, keys) => {
      out[key] = candidates(view, n, keys, win).map(c => ({t: [grid(c.e), grid(c.l), grid(c.point ? c.e : c.fe), grid(c.point ? c.l : c.fl)],
        mention: !!(c.item && !c.item.recs.length), what: c.what}));
    };
    for (const n of def.nodes) add('step:' + n.id, n, new Set());
    for (const o of relaxOptions(def)) if (o.key.startsWith('node:')) add(o.key, byId[o.key.slice(5)], new Set([o.key]));
    return out;
  };
  var occurrenceOf = function (name, key) {
    const def = {...defaultDef(), ...CASES[name]}, n = nodeById(def)[key.split(':')[1]];
    return {kind: stepKind(n), occ: stepKind(n) === 'measure' ? 'any' : (n.occ || 'any')};
  };
  var pageResults = function (def) {
    const steps = engineSteps(def);
    let step = steps.next();
    while (!step.done) step = steps.next(POST(step.value));
    return step.value;
  };`, context);

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
     its reconciled records date, without that test. */
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
function causes(name, pid, pageStatus, storeResult, storeCands) {
  if (COVERAGE.includes(storeResult.reason) && pageStatus === 'none') return ['coverage'];
  const page = vm.runInContext(`pageCandidates(${JSON.stringify(name)}, ${JSON.stringify(pid)})`, context);
  const out = new Set();
  // D30: the store leaves a first or last step undecided when its target is uncertain; the page does not test that.
  if (storeResult.reason === 'OCCURRENCE_ORDER_UNCERTAIN') out.add('order-uncertain');
  for (const [key, mine] of Object.entries(page)) {
    const {kind, occ} = vm.runInContext(`occurrenceOf(${JSON.stringify(name)}, ${JSON.stringify(key)})`, context);
    const theirs = storeList((storeCands[pid] || {})[key], occ);  // a relaxed alternative keeps the step's occurrence
    const sig = x => JSON.stringify(x.t);
    const a = mine.map(sig).sort(), b = theirs.map(sig).sort();
    if (JSON.stringify(a) === JSON.stringify(b)) continue;
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
const storeKey = r => [r.status, r.certainty || '', r.relaxations ? [...r.relaxations].sort().join('+') : ''].join(' ').trim();

const found = {}, summary = {};
let patients = 0, agree = 0, matched = 0;
const replayMismatches = [];
for (const name of names) {
  const page = vm.runInContext(`pageResults({...defaultDef(), ...CASES[${JSON.stringify(name)}]})`, context);
  found[name] = {};
  for (const r of page) {
    const a = pageKey(r), b = storeKey(store[name][r.pid]);
    patients += 1;
    if (/^(exact|possible|relaxed)/.test(b)) matched += 1;
    // The store's result is the engine's on the store's candidates, up to the store's comparability rule.
    const replayed = store[name + '#replayed'][r.pid], own = store[name][r.pid].status;
    if (!(replayed === own || (replayed === 'none' && own === 'not_comparable'))) replayMismatches.push(`${name} ${r.pid}: replayed ${replayed}, store ${own}`);
    if (a === b) { agree += 1; continue; }
    const why = causes(name, r.pid, STATUS[r.status] || r.status, store[name][r.pid], store[name + '#candidates']);
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
  "weight at most 9 kg inside the period": {},
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
console.log(`Trajectory page and execution store: ${agree} of ${patients} patient results agree over ${names.length} trajectories `
  + `(${matched} store matches); ${patients - agree} differ, each by a named cause (${Object.entries(summary).sort().map(([k, v]) => `${k} ${v}`).join(', ')}); `
  + 'the store matched exactly the candidates the explanation reads.');
