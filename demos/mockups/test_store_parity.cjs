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
// A development cohort (STORE_PARITY_COHORT, diagnostics only, D52): every rule runs, but the tables pinned to the
// committed cohort (the differences, the sweep tally) are printed instead of compared.
const DEV_COHORT = process.env.STORE_PARITY_COHORT;
const cohort = DEV_COHORT ? path.resolve(DEV_COHORT) : path.join(here, 'data/synthetic-paediatric-rare.json');
// D44: the child's stdin is a regular file holding `input` (empty without one), never a pipe. Once, the `sweep` child sat
// in its read of stdin for about 30 minutes and passed on a rerun. A read of a regular file ends at the file's end
// whatever any other process holds open, so no read can wait on a writer; and a call that still hangs fails after
// RUN_TIMEOUT_MS instead of blocking the test. stderr is the parent's, as execFileSync's default shows it.
const RUN_TIMEOUT_MS = 30 * 60 * 1000;
const run = (args, input) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'store-parity-stdin-'));
  const file = path.join(dir, 'stdin');
  fs.writeFileSync(file, input === undefined ? '' : input);
  const fd = fs.openSync(file, 'r');
  try {
    return execFileSync(python, args, {cwd: root, stdio: [fd, 'pipe', 'inherit'], encoding: 'utf8', maxBuffer: 1 << 28,
                                       timeout: RUN_TIMEOUT_MS});
  } finally {
    fs.closeSync(fd);
    fs.rmSync(dir, {recursive: true, force: true});
  }
};
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
    // them over stated free text). D37 kept three of them on D32's reading (coded only for the derived step, stated
    // findings elsewhere); since D39 every step reads coded records only, as the page does (codedOnly: 'store': the
    // store's profile 1.3, letter mentions left out of every step). Since D49 that is the page's default, so these cases,
    // like every case here, are translated exactly as the page translates them (no options).
    'the event-frequency template': {scope: 'from_start', window, freeText: 'off',
      nodes: TEMPLATES.frequency.nodes, edges: TEMPLATES.frequency.edges},
    'the second seizure, then a sodium-channel blocker': {scope: 'from_start', window, freeText: 'off',
      nodes: [{id: 'A', concept: 'seizure', occ: 'nth', n: 2}, {id: 'B', concept: 'sodium_channel_blocker', occ: 'first'}],
      edges: [{from: 'A', to: 'B', rel: 'after', min: 0, max: 12, unit: 'month'}]},
    'the second-to-last seizure, then levetiracetam': {scope: 'any', window, freeText: 'off',
      nodes: [{id: 'A', concept: 'seizure', occ: 'nth_last', n: 2}, {id: 'B', concept: 'levetiracetam', occ: 'any'}],
      edges: [{from: 'A', to: 'B', rel: 'after', min: -12, max: 12, unit: 'month'}]},
    // D52: a count "in the index period" keeps D49's point window (INDEX_PERIOD_COUNT_UNSUPPORTED, the point table below).
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
    // D37: the page's Blank template, as the page starts it.
    'the blank template (the first seizure)': {scope: 'from_start', window, nodes: BLANK.nodes, edges: BLANK.edges},
    // D38: the tree and graph templates as the page writes them, gene step included (the store's gene condition).
    'the tree template': {scope: 'from_start', window, nodes: TEMPLATES.tree.nodes, edges: TEMPLATES.tree.edges},
    'the graph template': {scope: 'from_start', window, nodes: TEMPLATES.graph.nodes, edges: TEMPLATES.graph.edges},
    // D39: the other templates under "coded only", every step over coded records (the store's profile 1.3).
    'the sequence template, coded only': {scope: 'from_start', window, freeText: 'off',
      nodes: TEMPLATES.sequence.nodes, edges: TEMPLATES.sequence.edges},
    'the tree template without its gene step, coded only': {scope: 'from_start', window, freeText: 'off',
      ...withoutNodes({nodes: TEMPLATES.tree.nodes, edges: TEMPLATES.tree.edges}, new Set(['B']))},
    'the graph template without its gene step, coded only': {scope: 'from_start', window, freeText: 'off',
      ...withoutNodes({nodes: TEMPLATES.graph.nodes, edges: TEMPLATES.graph.edges}, new Set(['E']))},
    'the blank template, coded only': {scope: 'from_start', window, freeText: 'off', nodes: BLANK.nodes,
      edges: BLANK.edges},
    // D42: the tree and graph templates under "coded only", gene step included (the joint profile: gene steps as D38,
    // every other step over coded records as D39).
    'the tree template, coded only': {scope: 'from_start', window, freeText: 'off',
      nodes: TEMPLATES.tree.nodes, edges: TEMPLATES.tree.edges},
    'the graph template, coded only': {scope: 'from_start', window, freeText: 'off',
      nodes: TEMPLATES.graph.nodes, edges: TEMPLATES.graph.edges},
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
      // The page's own call (D49): no options. Rises (D36), gene steps (D38), "coded only" as the store's profile 1.3
      // (D39) and gene steps under it as the joint profile (D42) are all the translator's defaults since D49.
      const {query, conditions, windows, index} = storeRequest(def, state.freeText, pids);
      const own = (conditions || {steps: {}}).steps;
      const wanted = {};
      for (const n of def.nodes) wanted['step:' + n.id] = {concept: n.concept, ...(own[n.id] && own[n.id].unit ? {condition: own[n.id]} : {}),
        ...(own[n.id] && own[n.id].relative ? {relative: own[n.id].relative} : {}), ...(own[n.id] && own[n.id].gene ? {gene: true} : {})};
      // D52: the hidden steps that place an event's period (the replay binds them as the store does).
      for (const st of query.steps) if (st.id.startsWith('@index_')) wanted['step:' + st.id] = {concept: st.concept};
      for (const x of query.relaxations) if (x.kind === 'widen_concept') wanted[x.id] = {concept: x.concept};
      for (const x of (conditions || {}).relaxations || []) {
        if (x.kind === 'widen_value') wanted[x.id] = {concept: byId[x.step].concept, condition: x.condition};
        else if (x.kind === 'widen_factor') wanted[x.id] = {concept: byId[x.step].concept, relative: {...own[x.step].relative, factor: x.factor}};
        else if (x.concept) wanted[x.id] = {concept: x.concept};
      }
      return {name, query, conditions, windows, ...(index ? {index} : {}), candidates: wanted};
    });
  };
  // D52 (review, S2): why a period placed by an event keeps D49's point window, or null when it is rewritten.
  var pointOf = function (def, freeText, options) {
    const r = storeRequest(def, freeText, [...state.patients.keys()].slice(0, 1), options);
    return r.index_anchor ? r.index_anchor.reason : r.index ? 'rewritten' : 'the D49 request';
  };
  var refusalOf = function (def, freeText, options) {
    try { storeRequest(def, freeText, [...state.patients.keys()].slice(0, 1), options); return null; } catch (e) { if (e.code) return e.code; throw e; }
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
  // D52: the page's own placement for a patient (its reason, its anchors and last contact, in grid minutes), for the
  // declared cause "index anchor".
  var pageIndex = function (name, pid) { return withFreeText(name, () => {
    const def = {...defaultDef(), ...CASES[name]}, q = {freeText: state.freeText, knownByIndex: false};
    const w = windowFor(evidenceFor(pid), {...q, window: def.window}), anchors = {};
    for (const side of ['start', 'end']) {
      const b = def.window[side];
      if (b.kind !== 'event') continue;
      const d = eventDay(indexViewFor(pid), q, b.event, b.which, b.n);
      anchors[side] = d == null ? null : toGrid(d);
    }
    const p = state.patients.get(pid);
    return {reason: w.reason || null, anchors, last: toGrid(dayOf(p, p.follow_up.last_contact))};
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
     when its end is read, another step can bind its events, or no sound refutation exists, and the target split
     (D35) cannot decide it; the page binds the event its reconciled records date, without that test;
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
/* D52: the store places an event's period from its own records (a hidden step), the page at one time. Declared for a
   patient only when that can differ: the page or the store (and not both) cannot place the period; a hidden step's
   possible range is not exactly the page's point; some worlds end the period after the last contact (or before birth,
   or before it starts); or the period's start may fall before birth (a gap from the start is measured from the event
   plus its offset, unclamped; D52, Q2). */
function indexAnchorDiffers(name, pid, storeResult, ir, index) {
  if (!ir) return false;
  const page = vm.runInContext(`pageIndex(${JSON.stringify(name)}, ${JSON.stringify(pid)})`, context);
  if (!!page.reason !== (storeResult.status === 'excluded')) return true;
  if (storeResult.status === 'excluded') return false;
  const range = step => (ir.ranges[step] || {})[pid] || [0, null, null];
  const bound = step => step === '@index_start' ? 'start' : 'end';
  for (const step of new Set(['start', 'end'].filter(x => index[x]).map(x => index[x].step))) {
    const [, lo, hi] = range(step), at = page.anchors[bound(step)];
    if (lo === null || at === null || at === undefined || lo !== hi || at !== lo) return true;
  }
  const base = ir.base_windows[pid], side = (x, c) => index[x] ? [range(index[x].step)[1] + index[x].offset, range(index[x].step)[2] + index[x].offset] : [c, c];
  const S = side('start', base.start), E = side('end', base.end);
  // Eligibility in some worlds only: the period may end after the last contact or before birth, or before it starts.
  const partial = (index.end && (E[1] > page.last || E[0] < 0)) || (!(index.start && index.end && index.start.step === index.end.step) && S[1] >= E[0]);
  // The clamp (D52, Q2): the period's start may fall before birth, where a gap from it is measured from the event plus
  // its offset (the store) and from birth (the page).
  const clamp = !!index.start && S[0] < 0;
  return partial || clamp;
}
function causes(name, pid, pageStatus, storeResult, storeCands, pageReason, ir, index) {
  const anchor = indexAnchorDiffers(name, pid, storeResult, ir, index);
  if (COVERAGE.includes(storeResult.reason) && pageStatus === 'none') return anchor ? ['coverage', 'index-anchor'] : ['coverage'];
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
    // D38: a gene step's points (the first report not negative, at its recording) are expected to be the page's own; a
    // difference has no declared cause, so it fails as such.
    if (kind === 'gene') { out.add('gene-points-differ'); continue; }
    if (kind === 'measure') { out.add('measurement-records'); continue; }
    const onlyStore = theirs.filter(x => !a.includes(sig(x))), onlyPage = mine.filter(x => !b.includes(sig(x)));
    if (onlyStore.some(x => x.kind === 'mention') || onlyPage.some(x => x.mention)) out.add('letter-mentions');
    const records = onlyStore.some(x => x.kind !== 'mention') || onlyPage.some(x => !x.mention);
    if (records) out.add(kind === 'medicine' ? 'medication-records' : 'record-reconciliation');
  }
  if (anchor) out.add('index-anchor');
  return out.size ? [...out].sort() : ['UNEXPLAINED'];
}

// The store: built once into a scratch directory.
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'store-parity-'));
const snapshot = run([path.join(here, 'store_parity.py'), 'build', cohort, path.join(scratch, 'store')]).trim();
const names = vm.runInContext('Object.keys(CASES)', context);
const cases = names.map(name => vm.runInContext(`storeCase(${JSON.stringify(name)}, {...defaultDef(), ...CASES[${JSON.stringify(name)}]})`, context));
if (process.env.STORE_PARITY_CASES) fs.writeFileSync(process.env.STORE_PARITY_CASES, JSON.stringify({cohort, cases}));  // diagnostics
// D52: each case whose period is a hidden step has its base windows narrowed by the server's rule first (index_hull.py).
const narrowed = JSON.parse(run([path.join(here, 'index_hull.py'), 'narrow', snapshot], JSON.stringify({cohort, cases}))).cases;
const indexOf = Object.fromEntries(narrowed.filter(c => c.index_result).map(c => [c.name, {ir: c.index_result, index: c.index}]));
const store = JSON.parse(run([path.join(here, 'store_parity.py'), 'match', snapshot], JSON.stringify({cohort, cases: narrowed})));
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
    const why = causes(name, r.pid, STATUS[r.status] || r.status, store[name][r.pid], store[name + '#candidates'], r.reason,
                       (indexOf[name] || {}).ir, (indexOf[name] || {}).index);
    found[name][r.pid] = `${why.join('+')}: page ${a}, store ${b}`;
    for (const w of why) summary[w] = (summary[w] || 0) + 1;
  }
}
if (process.env.STORE_PARITY_PRINT) console.log(JSON.stringify(found, null, 1));

/* Every difference on the committed seed, with its cause. A new difference, a vanished one or a changed cause fails:
   regenerate the cohort, read the new causes, and update this table and the README together. Re-pinned in D52 (rule 5)
   from the test's run under the bench lock: the store places an event's period from its own records (`index-anchor`). */
const EXPECTED = {
  "febrile seizure, then a sodium-channel blocker": {
    C001: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C004: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C008: "index-anchor+letter-mentions+medication-records+record-reconciliation: page relaxed exact node:A, store none",
    C015: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C016: "index-anchor: page excluded, store none",
    C021: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C023: "index-anchor+letter-mentions+medication-records+order-uncertain: page exact, store unresolved",
    C024: "index-anchor: page excluded, store none",
    C036: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C037: "index-anchor: page excluded, store none",
    C038: "index-anchor+letter-mentions+medication-records+record-reconciliation: page relaxed exact edge:0+node:A, store none",
    C044: "index-anchor+letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C045: "index-anchor+letter-mentions+medication-records+record-reconciliation: page relaxed exact node:A, store relaxed possible node:A",
  },
  "first seizure branching to a blocker and to stiripentol": {
    C001: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C004: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C015: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C016: "index-anchor: page excluded, store none",
    C021: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C023: "index-anchor+letter-mentions+medication-records+order-uncertain: page exact, store unresolved",
    C024: "index-anchor: page excluded, store none",
    C036: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C037: "index-anchor: page excluded, store none",
    C044: "index-anchor+letter-mentions+medication-records+record-reconciliation: page exact, store possible",
  },
  "a seizure while taking the first blocker": {
    C001: "index-anchor+letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C004: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page possible, store unresolved",
    C008: "index-anchor+letter-mentions+medication-records+record-reconciliation: page none, store possible",
    C015: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page possible, store unresolved",
    C016: "index-anchor: page excluded, store none",
    C021: "index-anchor+letter-mentions+medication-records: page exact, store possible",
    C023: "index-anchor+letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C024: "index-anchor: page excluded, store none",
    C036: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page none, store unresolved",
    C037: "index-anchor: page excluded, store none",
    C038: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C044: "index-anchor+letter-mentions+medication-records+record-reconciliation: page none, store possible",
    C045: "index-anchor+letter-mentions+medication-records: page exact, store possible",
  },
  "Dravet syndrome, then valproate": {
    C001: "letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C029: "letter-mentions+medication-records: page exact, store possible",
  },
  "levetiracetam, then the last recorded seizure": {
    C003: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page none, store unresolved",
    C008: "index-anchor+letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C010: "index-anchor+letter-mentions+medication-records+record-reconciliation: page none, store possible",
    C016: "index-anchor: page excluded, store none",
    C024: "index-anchor: page excluded, store none",
    C026: "index-anchor+letter-mentions+medication-records: page exact, store possible",
    C030: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C033: "index-anchor+letter-mentions+medication-records+record-reconciliation: page relaxed exact edge:0, store relaxed possible edge:0",
    C037: "index-anchor: page excluded, store none",
    C038: "index-anchor+letter-mentions+medication-records+record-reconciliation: page relaxed exact edge:0, store relaxed possible edge:0",
    C045: "index-anchor+letter-mentions+medication-records+record-reconciliation: page exact, store possible",
    C048: "index-anchor+letter-mentions+medication-records: page relaxed exact edge:0, store none",
  },
  "status epilepticus within 6 months of the period start": {
    C001: "index-anchor+letter-mentions+record-reconciliation: page exact, store possible",
    C002: "index-anchor+letter-mentions: page relaxed exact node:A, store relaxed possible node:A",
    C003: "index-anchor+letter-mentions+record-reconciliation: page relaxed exact node:A, store relaxed possible node:A",
    C004: "index-anchor+letter-mentions+record-reconciliation: page relaxed exact node:A, store relaxed possible node:A",
    C006: "index-anchor+letter-mentions+record-reconciliation: page relaxed exact node:A, store relaxed possible node:A",
    C014: "index-anchor+letter-mentions: page relaxed exact node:A, store relaxed possible node:A",
    C015: "index-anchor+letter-mentions+record-reconciliation: page relaxed exact node:A, store relaxed possible node:A",
    C016: "index-anchor: page excluded, store relaxed possible node:A",
    C017: "index-anchor+letter-mentions+record-reconciliation: page exact, store possible",
    C018: "index-anchor+letter-mentions+record-reconciliation: page relaxed exact node:A, store relaxed possible node:A",
    C019: "index-anchor+letter-mentions: page relaxed exact node:A, store relaxed possible node:A",
    C022: "index-anchor+letter-mentions+record-reconciliation: page relaxed exact node:A, store relaxed possible node:A",
    C023: "index-anchor+letter-mentions+record-reconciliation: page relaxed exact edge:0, store possible",
    C024: "index-anchor: page excluded, store relaxed possible node:A",
    C025: "index-anchor+letter-mentions: page relaxed exact node:A, store relaxed possible node:A",
    C027: "index-anchor+letter-mentions+record-reconciliation: page exact, store possible",
    C028: "index-anchor+letter-mentions+record-reconciliation: page relaxed exact node:A, store relaxed possible node:A",
    C029: "index-anchor+letter-mentions+record-reconciliation: page exact, store possible",
    C031: "index-anchor+letter-mentions+record-reconciliation: page relaxed exact node:A, store relaxed possible node:A",
    C032: "index-anchor+letter-mentions+record-reconciliation: page relaxed exact node:A, store relaxed possible node:A",
    C033: "index-anchor+letter-mentions: page relaxed exact node:A, store relaxed possible node:A",
    C034: "index-anchor+letter-mentions+record-reconciliation: page relaxed exact node:A, store relaxed possible node:A",
    C036: "index-anchor+letter-mentions+record-reconciliation: page exact, store possible",
    C037: "index-anchor: page excluded, store relaxed possible node:A",
    C038: "index-anchor+letter-mentions+record-reconciliation: page relaxed exact node:A, store relaxed possible node:A",
    C039: "index-anchor+letter-mentions+record-reconciliation: page relaxed exact node:A, store relaxed possible node:A",
    C040: "index-anchor+letter-mentions: page relaxed exact node:A, store relaxed possible node:A",
    C042: "index-anchor+letter-mentions+record-reconciliation: page relaxed exact node:A, store relaxed possible node:A",
    C044: "index-anchor+letter-mentions+record-reconciliation: page exact, store possible",
    C047: "index-anchor+letter-mentions+record-reconciliation: page exact, store possible",
    C048: "index-anchor+letter-mentions: page relaxed exact node:A, store relaxed possible node:A",
  },
  "two seizures then a febrile seizure": {
    C001: "index-anchor+letter-mentions+record-reconciliation: page relaxed exact node:C, store possible",
    C004: "index-anchor+letter-mentions+record-reconciliation: page relaxed exact node:C, store possible",
    C005: "index-anchor+letter-mentions+record-reconciliation: page none, store relaxed possible node:C",
    C008: "index-anchor+letter-mentions+record-reconciliation: page none, store relaxed possible node:C",
    C016: "index-anchor: page excluded, store possible",
    C017: "index-anchor+letter-mentions+record-reconciliation: page relaxed exact node:C, store possible",
    C021: "index-anchor+letter-mentions: page relaxed exact node:C, store possible",
    C023: "index-anchor+letter-mentions+record-reconciliation: page relaxed exact node:C, store possible",
    C024: "index-anchor: page excluded, store possible",
    C026: "index-anchor+letter-mentions: page none, store relaxed possible node:C",
    C027: "index-anchor+letter-mentions+record-reconciliation: page exact, store possible",
    C028: "index-anchor+letter-mentions+record-reconciliation: page none, store possible",
    C030: "index-anchor+letter-mentions: page none, store relaxed possible node:C",
    C031: "index-anchor+letter-mentions+record-reconciliation: page none, store possible",
    C036: "index-anchor+letter-mentions+record-reconciliation: page relaxed exact node:C, store possible",
    C037: "index-anchor: page excluded, store possible",
    C043: "index-anchor+letter-mentions+record-reconciliation: page none, store relaxed possible node:C",
    C044: "index-anchor+letter-mentions+record-reconciliation: page relaxed exact node:C, store possible",
    C045: "index-anchor+letter-mentions: page none, store relaxed possible node:C",
  },
  "a blocker, then at least 8 seizures a month within 60 days": {
    C001: "index-anchor+letter-mentions+medication-records: page exact, store possible",
    C002: "coverage+index-anchor: page none, store not_comparable",
    C003: "coverage+index-anchor: page none, store not_comparable",
    C005: "coverage+index-anchor: page none, store not_comparable",
    C006: "coverage+index-anchor: page none, store not_comparable",
    C010: "coverage+index-anchor: page none, store not_comparable",
    C014: "coverage+index-anchor: page none, store not_comparable",
    C015: "index-anchor+letter-mentions+medication-records: page exact, store possible",
    C016: "index-anchor: page excluded, store not_comparable",
    C019: "coverage+index-anchor: page none, store not_comparable",
    C020: "coverage+index-anchor: page none, store not_comparable",
    C023: "index-anchor+letter-mentions+medication-records: page exact, store possible",
    C024: "index-anchor: page excluded, store not_comparable",
    C025: "coverage+index-anchor: page none, store not_comparable",
    C026: "coverage+index-anchor: page none, store not_comparable",
    C028: "coverage+index-anchor: page none, store not_comparable",
    C030: "coverage+index-anchor: page none, store not_comparable",
    C031: "coverage+index-anchor: page none, store not_comparable",
    C033: "coverage+index-anchor: page none, store not_comparable",
    C035: "coverage+index-anchor: page none, store not_comparable",
    C036: "index-anchor+letter-mentions+medication-records: page exact, store possible",
    C037: "index-anchor: page excluded, store not_comparable",
    C040: "coverage+index-anchor: page none, store not_comparable",
    C041: "coverage+index-anchor: page none, store not_comparable",
    C042: "coverage+index-anchor: page none, store not_comparable",
    C043: "coverage+index-anchor: page none, store not_comparable",
    C045: "index-anchor+letter-mentions+medication-records: page exact, store possible",
    C048: "coverage+index-anchor: page none, store not_comparable",
  },
  "at most 2 seizures a month inside the period": {
    C016: "index-anchor: page excluded, store not_comparable",
    C017: "index-anchor: page exact, store possible",
    C018: "index-anchor: page exact, store possible",
    C022: "index-anchor: page exact, store possible",
    C023: "index-anchor: page exact, store possible",
    C024: "index-anchor: page excluded, store not_comparable",
    C032: "index-anchor: page exact, store possible",
    C034: "index-anchor: page exact, store possible",
    C037: "index-anchor: page excluded, store not_comparable",
    C039: "index-anchor: page exact, store possible",
  },
  "weight at most 9 kg inside the period": {
    C002: "index-anchor: page exact, store possible",
    C006: "index-anchor+measurement-records: page none, store not_comparable",
    C009: "index-anchor: page exact, store possible",
    C010: "index-anchor+measurement-records: page none, store not_comparable",
    C014: "index-anchor: page none, store relaxed possible node:A",
    C016: "index-anchor: page excluded, store none",
    C017: "index-anchor: page exact, store possible",
    C019: "index-anchor+measurement-records: page relaxed exact node:A, store unresolved  node:A",
    C022: "index-anchor+measurement-records: page none, store not_comparable",
    C024: "index-anchor: page excluded, store relaxed possible node:A",
    C028: "index-anchor+measurement-records: page none, store not_comparable",
    C029: "index-anchor: page exact, store possible",
    C037: "index-anchor: page excluded, store relaxed possible node:A",
    C044: "index-anchor: page exact, store possible",
  },
  "the event-frequency template": {
    C004: "count-timing+index-anchor+medication-records+record-reconciliation: page possible, store exact",
    C008: "count-timing+index-anchor+record-reconciliation: page relaxed exact count:A, store exact",
    C014: "index-anchor: page excluded, store none",
    C015: "count-timing+index-anchor+medication-records+record-reconciliation: page exact, store possible",
    C016: "index-anchor: page excluded, store none",
    C023: "count-timing+index-anchor+record-reconciliation: page none, store relaxed possible count:A",
    C024: "index-anchor: page excluded, store none",
    C037: "index-anchor: page excluded, store none",
  },
  "the second seizure, then a sodium-channel blocker": {
    C001: "index-anchor+medication-records+occurrence-envelope+record-reconciliation: page exact, store unresolved",
    C004: "index-anchor+medication-records+occurrence-envelope+record-reconciliation: page exact, store unresolved",
    C008: "index-anchor+occurrence-envelope+record-reconciliation: page exact, store unresolved",
    C014: "index-anchor: page excluded, store none",
    C016: "index-anchor: page excluded, store none",
    C023: "index-anchor+occurrence-envelope+record-reconciliation: page none, store unresolved",
    C024: "index-anchor: page excluded, store none",
    C037: "index-anchor: page excluded, store none",
  },
  "the second-to-last seizure, then levetiracetam": {
    C014: "index-anchor: page excluded, store none",
    C016: "index-anchor: page excluded, store none",
    C022: "medication-records+occurrence-envelope+record-reconciliation: page none, store exact",
    C024: "index-anchor: page excluded, store none",
    C032: "medication-records+occurrence-envelope+record-reconciliation: page none, store exact",
    C033: "medication-records+occurrence-envelope: page none, store exact",
    C037: "index-anchor: page excluded, store none",
    C038: "medication-records+occurrence-envelope+record-reconciliation: page none, store exact",
    C048: "medication-records+occurrence-envelope: page none, store exact",
  },
  "at least 2 febrile seizures in the index period": {
    C004: "count-timing+record-reconciliation: page exact, store possible",
    C005: "count-timing+record-reconciliation: page none, store relaxed possible node:A",
    C010: "count-timing: page none, store relaxed exact count:A+node:A",
    C018: "count-timing+record-reconciliation: page none, store relaxed possible node:A",
    C023: "count-timing+record-reconciliation: page relaxed exact count:A, store relaxed possible count:A",
    C026: "count-timing: page none, store relaxed exact count:A+node:A",
    C031: "count-timing+record-reconciliation: page relaxed exact count:A, store possible",
    C034: "count-timing+record-reconciliation: page none, store relaxed possible node:A",
    C035: "count-timing: page none, store relaxed exact count:A+node:A",
    C039: "count-timing+record-reconciliation: page none, store relaxed possible node:A",
    C041: "count-timing+record-reconciliation: page none, store relaxed possible node:A",
    C043: "count-timing+record-reconciliation: page none, store relaxed possible node:A",
  },
  "the sequence template (a rise)": {
    C001: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page possible, store unresolved",
    C002: "coverage+index-anchor: page none, store not_comparable",
    C003: "coverage+index-anchor: page none, store not_comparable",
    C005: "coverage+index-anchor: page none, store not_comparable",
    C006: "coverage+index-anchor: page none, store not_comparable",
    C008: "coverage+index-anchor: page none, store not_comparable",
    C010: "coverage+index-anchor: page none, store not_comparable",
    C014: "coverage+index-anchor: page none, store not_comparable",
    C015: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C016: "index-anchor: page excluded, store not_comparable",
    C019: "coverage+index-anchor: page none, store not_comparable",
    C020: "coverage+index-anchor: page none, store not_comparable",
    C023: "index-anchor+letter-mentions+medication-records+order-uncertain: page possible, store unresolved",
    C024: "index-anchor: page excluded, store not_comparable",
    C025: "coverage+index-anchor: page none, store not_comparable",
    C026: "coverage+index-anchor: page none, store not_comparable",
    C028: "coverage+index-anchor: page none, store not_comparable",
    C030: "coverage+index-anchor: page none, store not_comparable",
    C031: "coverage+index-anchor: page none, store not_comparable",
    C033: "coverage+index-anchor: page none, store not_comparable",
    C035: "coverage+index-anchor: page none, store not_comparable",
    C036: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C037: "index-anchor: page excluded, store not_comparable",
    C040: "coverage+index-anchor: page none, store not_comparable",
    C041: "coverage+index-anchor: page none, store not_comparable",
    C042: "coverage+index-anchor: page none, store not_comparable",
    C043: "coverage+index-anchor: page none, store not_comparable",
    C048: "coverage+index-anchor: page none, store not_comparable",
  },
  "the tree template without its gene step": {
    C001: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page possible, store unresolved",
    C002: "coverage+index-anchor: page none, store not_comparable",
    C003: "coverage+index-anchor: page none, store not_comparable",
    C005: "coverage+index-anchor: page none, store not_comparable",
    C006: "coverage+index-anchor: page none, store not_comparable",
    C010: "coverage+index-anchor: page none, store not_comparable",
    C014: "coverage+index-anchor: page none, store not_comparable",
    C015: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C016: "index-anchor: page excluded, store not_comparable",
    C019: "coverage+index-anchor: page none, store not_comparable",
    C020: "coverage+index-anchor: page none, store not_comparable",
    C023: "index-anchor+letter-mentions+medication-records+order-uncertain: page possible, store unresolved",
    C024: "index-anchor: page excluded, store not_comparable",
    C025: "coverage+index-anchor: page none, store not_comparable",
    C026: "coverage+index-anchor: page none, store not_comparable",
    C028: "coverage+index-anchor: page none, store not_comparable",
    C030: "coverage+index-anchor: page none, store not_comparable",
    C031: "coverage+index-anchor: page none, store not_comparable",
    C033: "coverage+index-anchor: page none, store not_comparable",
    C035: "coverage+index-anchor: page none, store not_comparable",
    C036: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C037: "index-anchor: page excluded, store not_comparable",
    C040: "coverage+index-anchor: page none, store not_comparable",
    C041: "coverage+index-anchor: page none, store not_comparable",
    C042: "coverage+index-anchor: page none, store not_comparable",
    C043: "coverage+index-anchor: page none, store not_comparable",
    C048: "coverage+index-anchor: page none, store not_comparable",
  },
  "the graph template without its gene step": {
    C001: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page possible, store unresolved",
    C002: "coverage+index-anchor: page none, store not_comparable",
    C003: "coverage+index-anchor: page none, store not_comparable",
    C005: "coverage+index-anchor: page none, store not_comparable",
    C006: "coverage+index-anchor: page none, store not_comparable",
    C008: "coverage+index-anchor: page none, store not_comparable",
    C010: "coverage+index-anchor: page none, store not_comparable",
    C014: "coverage+index-anchor: page none, store not_comparable",
    C015: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C016: "index-anchor: page excluded, store not_comparable",
    C019: "coverage+index-anchor: page none, store not_comparable",
    C020: "coverage+index-anchor: page none, store not_comparable",
    C023: "index-anchor+letter-mentions+medication-records+order-uncertain: page possible, store unresolved",
    C024: "index-anchor: page excluded, store not_comparable",
    C025: "coverage+index-anchor: page none, store not_comparable",
    C026: "coverage+index-anchor: page none, store not_comparable",
    C028: "coverage+index-anchor: page none, store not_comparable",
    C030: "coverage+index-anchor: page none, store not_comparable",
    C031: "coverage+index-anchor: page none, store not_comparable",
    C033: "coverage+index-anchor: page none, store not_comparable",
    C035: "coverage+index-anchor: page none, store not_comparable",
    C036: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C037: "index-anchor: page excluded, store not_comparable",
    C040: "coverage+index-anchor: page none, store not_comparable",
    C041: "coverage+index-anchor: page none, store not_comparable",
    C042: "coverage+index-anchor: page none, store not_comparable",
    C043: "coverage+index-anchor: page none, store not_comparable",
    C048: "coverage+index-anchor: page none, store not_comparable",
  },
  "the blank template (the first seizure)": {
    C014: "index-anchor+letter-mentions: page exact, store possible",
    C016: "index-anchor: page excluded, store possible",
    C018: "index-anchor+letter-mentions+record-reconciliation: page exact, store possible",
    C024: "index-anchor: page excluded, store possible",
    C034: "index-anchor+letter-mentions+record-reconciliation: page exact, store possible",
    C035: "index-anchor+letter-mentions+record-reconciliation: page exact, store possible",
    C037: "index-anchor: page excluded, store possible",
    C039: "index-anchor+letter-mentions+record-reconciliation: page exact, store possible",
  },
  "the tree template": {
    C001: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page possible, store unresolved",
    C002: "coverage+index-anchor: page none, store not_comparable",
    C003: "coverage+index-anchor: page none, store not_comparable",
    C005: "coverage+index-anchor: page none, store not_comparable",
    C006: "coverage+index-anchor: page none, store not_comparable",
    C008: "coverage+index-anchor: page none, store not_comparable",
    C010: "coverage+index-anchor: page none, store not_comparable",
    C014: "coverage+index-anchor: page none, store not_comparable",
    C015: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C016: "index-anchor: page excluded, store not_comparable",
    C019: "coverage+index-anchor: page none, store not_comparable",
    C020: "coverage+index-anchor: page none, store not_comparable",
    C023: "index-anchor+letter-mentions+medication-records+order-uncertain: page possible, store unresolved",
    C024: "index-anchor: page excluded, store not_comparable",
    C025: "coverage+index-anchor: page none, store not_comparable",
    C026: "coverage+index-anchor: page none, store not_comparable",
    C028: "coverage+index-anchor: page none, store not_comparable",
    C030: "coverage+index-anchor: page none, store not_comparable",
    C031: "coverage+index-anchor: page none, store not_comparable",
    C033: "coverage+index-anchor: page none, store not_comparable",
    C035: "coverage+index-anchor: page none, store not_comparable",
    C036: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C037: "index-anchor: page excluded, store not_comparable",
    C040: "coverage+index-anchor: page none, store not_comparable",
    C041: "coverage+index-anchor: page none, store not_comparable",
    C042: "coverage+index-anchor: page none, store not_comparable",
    C043: "coverage+index-anchor: page none, store not_comparable",
    C048: "coverage+index-anchor: page none, store not_comparable",
  },
  "the graph template": {
    C001: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page possible, store unresolved",
    C002: "coverage+index-anchor: page none, store not_comparable",
    C003: "coverage+index-anchor: page none, store not_comparable",
    C005: "coverage+index-anchor: page none, store not_comparable",
    C006: "coverage+index-anchor: page none, store not_comparable",
    C008: "coverage+index-anchor: page none, store not_comparable",
    C010: "coverage+index-anchor: page none, store not_comparable",
    C014: "coverage+index-anchor: page none, store not_comparable",
    C015: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C016: "index-anchor: page excluded, store not_comparable",
    C019: "coverage+index-anchor: page none, store not_comparable",
    C020: "coverage+index-anchor: page none, store not_comparable",
    C023: "index-anchor+letter-mentions+medication-records+order-uncertain: page possible, store unresolved",
    C024: "index-anchor: page excluded, store not_comparable",
    C025: "coverage+index-anchor: page none, store not_comparable",
    C026: "coverage+index-anchor: page none, store not_comparable",
    C028: "coverage+index-anchor: page none, store not_comparable",
    C030: "coverage+index-anchor: page none, store not_comparable",
    C031: "coverage+index-anchor: page none, store not_comparable",
    C033: "coverage+index-anchor: page none, store not_comparable",
    C035: "coverage+index-anchor: page none, store not_comparable",
    C036: "index-anchor+letter-mentions+medication-records+order-uncertain+record-reconciliation: page exact, store unresolved",
    C037: "index-anchor: page excluded, store not_comparable",
    C040: "coverage+index-anchor: page none, store not_comparable",
    C041: "coverage+index-anchor: page none, store not_comparable",
    C042: "coverage+index-anchor: page none, store not_comparable",
    C043: "coverage+index-anchor: page none, store not_comparable",
    C048: "coverage+index-anchor: page none, store not_comparable",
  },
  "the sequence template, coded only": {
    C002: "coverage+index-anchor: page none, store not_comparable",
    C003: "coverage+index-anchor: page none, store not_comparable",
    C005: "coverage+index-anchor: page none, store not_comparable",
    C006: "coverage+index-anchor: page none, store not_comparable",
    C008: "coverage+index-anchor: page none, store not_comparable",
    C010: "coverage+index-anchor: page none, store not_comparable",
    C014: "index-anchor: page excluded, store not_comparable",
    C016: "index-anchor: page excluded, store not_comparable",
    C019: "coverage: page none, store not_comparable",
    C020: "coverage: page none, store not_comparable",
    C024: "index-anchor: page excluded, store not_comparable",
    C025: "coverage: page none, store not_comparable",
    C026: "coverage: page none, store not_comparable",
    C028: "coverage+index-anchor: page none, store not_comparable",
    C030: "coverage: page none, store not_comparable",
    C031: "coverage+index-anchor: page none, store not_comparable",
    C033: "coverage: page none, store not_comparable",
    C035: "coverage+index-anchor: page none, store not_comparable",
    C037: "index-anchor: page excluded, store not_comparable",
    C040: "coverage+index-anchor: page none, store not_comparable",
    C041: "coverage+index-anchor: page none, store not_comparable",
    C042: "coverage+index-anchor: page none, store not_comparable",
    C043: "coverage+index-anchor: page none, store not_comparable",
    C048: "coverage: page none, store not_comparable",
  },
  "the tree template without its gene step, coded only": {
    C002: "coverage+index-anchor: page none, store not_comparable",
    C003: "coverage+index-anchor: page none, store not_comparable",
    C005: "coverage+index-anchor: page none, store not_comparable",
    C006: "coverage+index-anchor: page none, store not_comparable",
    C010: "coverage+index-anchor: page none, store not_comparable",
    C014: "index-anchor: page excluded, store not_comparable",
    C016: "index-anchor: page excluded, store not_comparable",
    C019: "coverage: page none, store not_comparable",
    C020: "coverage: page none, store not_comparable",
    C024: "index-anchor: page excluded, store not_comparable",
    C025: "coverage: page none, store not_comparable",
    C026: "coverage: page none, store not_comparable",
    C028: "coverage+index-anchor: page none, store not_comparable",
    C030: "coverage: page none, store not_comparable",
    C031: "coverage+index-anchor: page none, store not_comparable",
    C033: "coverage: page none, store not_comparable",
    C035: "coverage+index-anchor: page none, store not_comparable",
    C037: "index-anchor: page excluded, store not_comparable",
    C040: "coverage+index-anchor: page none, store not_comparable",
    C041: "coverage+index-anchor: page none, store not_comparable",
    C042: "coverage+index-anchor: page none, store not_comparable",
    C043: "coverage+index-anchor: page none, store not_comparable",
    C048: "coverage: page none, store not_comparable",
  },
  "the graph template without its gene step, coded only": {
    C002: "coverage+index-anchor: page none, store not_comparable",
    C003: "coverage+index-anchor: page none, store not_comparable",
    C005: "coverage+index-anchor: page none, store not_comparable",
    C006: "coverage+index-anchor: page none, store not_comparable",
    C008: "coverage+index-anchor: page none, store not_comparable",
    C010: "coverage+index-anchor: page none, store not_comparable",
    C014: "index-anchor: page excluded, store not_comparable",
    C015: "index-anchor+medication-records: page exact, store possible",
    C016: "index-anchor: page excluded, store not_comparable",
    C019: "coverage: page none, store not_comparable",
    C020: "coverage: page none, store not_comparable",
    C024: "index-anchor: page excluded, store not_comparable",
    C025: "coverage: page none, store not_comparable",
    C026: "coverage: page none, store not_comparable",
    C028: "coverage+index-anchor: page none, store not_comparable",
    C030: "coverage: page none, store not_comparable",
    C031: "coverage+index-anchor: page none, store not_comparable",
    C033: "coverage: page none, store not_comparable",
    C035: "coverage+index-anchor: page none, store not_comparable",
    C037: "index-anchor: page excluded, store not_comparable",
    C040: "coverage+index-anchor: page none, store not_comparable",
    C041: "coverage+index-anchor: page none, store not_comparable",
    C042: "coverage+index-anchor: page none, store not_comparable",
    C043: "coverage+index-anchor: page none, store not_comparable",
    C048: "coverage: page none, store not_comparable",
  },
  "the blank template, coded only": {
    C014: "index-anchor: page excluded, store none",
    C016: "index-anchor: page excluded, store none",
    C024: "index-anchor: page excluded, store none",
    C037: "index-anchor: page excluded, store none",
  },
  "the tree template, coded only": {
    C002: "coverage+index-anchor: page none, store not_comparable",
    C003: "coverage+index-anchor: page none, store not_comparable",
    C005: "coverage+index-anchor: page none, store not_comparable",
    C006: "coverage+index-anchor: page none, store not_comparable",
    C008: "coverage+index-anchor: page none, store not_comparable",
    C010: "coverage+index-anchor: page none, store not_comparable",
    C014: "index-anchor: page excluded, store not_comparable",
    C016: "index-anchor: page excluded, store not_comparable",
    C018: "coverage+index-anchor: page none, store not_comparable",
    C019: "coverage: page none, store not_comparable",
    C020: "coverage: page none, store not_comparable",
    C024: "index-anchor: page excluded, store not_comparable",
    C025: "coverage: page none, store not_comparable",
    C026: "coverage: page none, store not_comparable",
    C028: "coverage+index-anchor: page none, store not_comparable",
    C030: "coverage: page none, store not_comparable",
    C031: "coverage+index-anchor: page none, store not_comparable",
    C033: "coverage: page none, store not_comparable",
    C034: "coverage+index-anchor: page none, store not_comparable",
    C035: "coverage+index-anchor: page none, store not_comparable",
    C037: "index-anchor: page excluded, store not_comparable",
    C039: "coverage+index-anchor: page none, store not_comparable",
    C040: "coverage+index-anchor: page none, store not_comparable",
    C041: "coverage+index-anchor: page none, store not_comparable",
    C042: "coverage+index-anchor: page none, store not_comparable",
    C043: "coverage+index-anchor: page none, store not_comparable",
    C048: "coverage: page none, store not_comparable",
  },
  "the graph template, coded only": {
    C002: "coverage+index-anchor: page none, store not_comparable",
    C003: "coverage+index-anchor: page none, store not_comparable",
    C005: "coverage+index-anchor: page none, store not_comparable",
    C006: "coverage+index-anchor: page none, store not_comparable",
    C008: "coverage+index-anchor: page none, store not_comparable",
    C010: "coverage+index-anchor: page none, store not_comparable",
    C014: "index-anchor: page excluded, store not_comparable",
    C015: "index-anchor+medication-records: page exact, store possible",
    C016: "index-anchor: page excluded, store not_comparable",
    C018: "coverage+index-anchor: page none, store not_comparable",
    C019: "coverage: page none, store not_comparable",
    C020: "coverage: page none, store not_comparable",
    C024: "index-anchor: page excluded, store not_comparable",
    C025: "coverage: page none, store not_comparable",
    C026: "coverage: page none, store not_comparable",
    C028: "coverage+index-anchor: page none, store not_comparable",
    C030: "coverage: page none, store not_comparable",
    C031: "coverage+index-anchor: page none, store not_comparable",
    C033: "coverage: page none, store not_comparable",
    C034: "coverage+index-anchor: page none, store not_comparable",
    C035: "coverage+index-anchor: page none, store not_comparable",
    C037: "index-anchor: page excluded, store not_comparable",
    C039: "coverage+index-anchor: page none, store not_comparable",
    C040: "coverage+index-anchor: page none, store not_comparable",
    C041: "coverage+index-anchor: page none, store not_comparable",
    C042: "coverage+index-anchor: page none, store not_comparable",
    C043: "coverage+index-anchor: page none, store not_comparable",
    C048: "coverage: page none, store not_comparable",
  },
};


assert.deepEqual(replayMismatches, [], 'the replayed candidates are not what the store matched on:\n' + replayMismatches.join('\n'));
for (const name of names) {
  const unexplained = Object.entries(found[name]).filter(([, why]) => why.startsWith('UNEXPLAINED'));
  assert.deepEqual(unexplained, [], `${name}: differences with no named cause`);
  // The tables come from the sandbox's realm, so compare their contents.
  if (!DEV_COHORT) assert.deepEqual(JSON.parse(JSON.stringify(found[name])), EXPECTED[name], `${name}: the differences changed`);
}
if (!DEV_COHORT) assert.deepEqual(Object.keys(EXPECTED).sort(), [...names].sort());
assert.ok(matched > 0, 'nothing matched in the store: the comparison would be vacuous');
// Every cause comes from the closed list the D32 rules fix.
const CAUSES = ['coverage', 'letter-mentions', 'record-reconciliation', 'medication-records', 'measurement-records',
                'order-uncertain', 'occurrence-envelope', 'count-timing', 'relative-baseline', 'measurement-observability',
                'index-anchor'];  // D52: the store places an event's period from its own records
assert.deepEqual(Object.keys(summary).filter(c => !CAUSES.includes(c)), [], 'a cause outside the closed list');
// Every page feature the store does not express is refused by its code, never translated approximately.
const REFUSALS = vm.runInContext(`(() => {
  const d = name => ({...defaultDef(), ...clone(TEMPLATES[name])});
  const nthDuring = {...defaultDef(), nodes: [{id: 'A', concept: 'sodium_channel_blocker', occ: 'nth', n: 2}, {id: 'B', concept: 'seizure'}],
                     edges: [{from: 'A', to: 'B', rel: 'during', grace: 0, unit: 'day'}]};
  const huge = {...defaultDef(), nodes: [{id: 'A', concept: 'seizure_frequency', cond: 'above', param: 1e21}], edges: []};
  return {
    'rise (sequence template)': refusalOf(d('sequence'), 'present'),
    'rise (sequence template), coded only': refusalOf(d('sequence'), 'off'),
    'gene step (tree template)': refusalOf(d('tree'), 'off'),
    'gene step and rise (graph template)': refusalOf(d('graph'), 'off'),
    'rise (sequence template), rises refused on request': refusalOf(d('sequence'), 'off', {rises: false}),
    'gene step and rise (graph template), rises refused on request': refusalOf(d('graph'), 'off', {rises: false}),
    'count over stated free text (frequency template)': refusalOf(d('frequency'), 'present'),
    'Nth over stated free text': refusalOf({...defaultDef(), nodes: [{id: 'A', concept: 'seizure', occ: 'nth', n: 2}], edges: []}, 'present'),
    'hedged free text': refusalOf(d('frequency'), 'possible'),
    'while taking the 2nd blocker': refusalOf(nthDuring, 'off'),
    'frequency template, coded only': refusalOf(d('frequency'), 'off'),
    'frequency template, coded only for the count (D32)': refusalOf(d('frequency'), 'off', {codedOnly: 'derived'}),
    'a count alone, coded only': refusalOf({...defaultDef(), nodes: [{id: 'A', concept: 'febrile_seizure', occ: 'count', n: 2, within: 'period'}], edges: []}, 'off'),
    'frequency template, coded only in the store (D39)': refusalOf(d('frequency'), 'off', {codedOnly: 'store'}),
    'rise (sequence template), coded only in the store (D39)': refusalOf(d('sequence'), 'off', {codedOnly: 'store'}),
    'gene step (tree template), coded only in the store (D39)': refusalOf(d('tree'), 'off', {codedOnly: 'store'}),
    'frequency template, coded only refused on request (D39)': refusalOf(d('frequency'), 'off', {codedOnly: false}),
    'a threshold that is not a plain decimal': refusalOf(huge, 'present'),
    // D38: gene steps when asked for (the default follows STORE_GENES).
    'gene step (tree template), genes asked for': refusalOf(d('tree'), 'present', {genes: true}),
    'gene step (tree template), coded only, genes asked for': refusalOf(d('tree'), 'off', {genes: true}),
    'gene step and rise (graph template), genes refused on request': refusalOf(d('graph'), 'present', {genes: false}),
    'a gene step alone, coded only, genes asked for': refusalOf({...defaultDef(), nodes: [{id: 'A', concept: 'gene:SCN1A'}], edges: []}, 'off', {genes: true}),
    'a gene step with an occurrence, genes asked for': refusalOf({...defaultDef(), nodes: [{id: 'A', concept: 'gene:SCN1A', occ: 'first'}], edges: []}, 'present', {genes: true}),
    // D42: the joint profile (gene steps under the store's coded only) when asked for; the default follows STORE_GENE_CODED_ONLY.
    'gene step (tree template), coded only in the store, joint asked for (D42)': refusalOf(d('tree'), 'off', {codedOnly: 'store', geneCodedOnly: true}),
    'gene step and rise (graph template), coded only in the store, joint asked for (D42)': refusalOf(d('graph'), 'off', {codedOnly: 'store', geneCodedOnly: true}),
    'gene step (tree template), coded only for the count, joint asked for (D42)': refusalOf(d('tree'), 'off', {codedOnly: 'derived', geneCodedOnly: true}),
    'gene step (tree template), coded only in the store, joint refused on request (D42)': refusalOf(d('tree'), 'off', {codedOnly: 'store', geneCodedOnly: false}),
    'gene step (tree template), coded only, joint asked for (D42)': refusalOf(d('tree'), 'off', {geneCodedOnly: true}),
    // D49: the named refusals stay reachable for a caller that asks for them (the page never does).
    'blank template, coded only, the store option refused on request (D49)': refusalOf({...defaultDef(), ...clone(BLANK)}, 'off', {codedOnly: null}),
    'gene step (tree template), coded only, the store option refused on request (D49)': refusalOf(d('tree'), 'off', {codedOnly: false}),
    'a count alone, coded only, the store option refused on request (D49)': refusalOf({...defaultDef(), nodes: [{id: 'A', concept: 'febrile_seizure', occ: 'count', n: 2, within: 'period'}], edges: []}, 'off', {codedOnly: null}),
  };
})()`, context);
assert.deepEqual(JSON.parse(JSON.stringify(REFUSALS)), {
  // D36: rises are translated by default (STORE_RISES); the named refusal remains for a caller that asks for it.
  'rise (sequence template)': null,
  // D37 refused "coded only" with steps that would still read letter mentions in the store; since D49 the page writes it
  // as the store's profile 1.3 by default (STORE_CODED_ONLY).
  'rise (sequence template), coded only': null,
  // D38: gene steps are translated by default (STORE_GENES); under coded only, since D49, as the joint profile by default
  // (STORE_GENE_CODED_ONLY), refused before.
  'gene step (tree template)': null,
  'gene step and rise (graph template)': null,
  'rise (sequence template), rises refused on request': 'RELATIVE_CONDITION_UNSUPPORTED',
  'gene step and rise (graph template), rises refused on request': 'RELATIVE_CONDITION_UNSUPPORTED',  // first refused, in node order
  'count over stated free text (frequency template)': 'FREE_TEXT_COUNT_UNSUPPORTED',
  'Nth over stated free text': 'FREE_TEXT_COUNT_UNSUPPORTED',
  'hedged free text': 'FREE_TEXT_HEDGED_UNSUPPORTED',
  'while taking the 2nd blocker': 'NTH_STEP_HAS_NO_END',
  'frequency template, coded only': null,  // D49 (FREE_TEXT_CODED_ONLY_UNSUPPORTED before)
  'frequency template, coded only for the count (D32)': null,
  'a count alone, coded only': null,
  // D39: the store leaves letter mentions out of every step (profile 1.3); a caller may still ask for the refusal.
  'frequency template, coded only in the store (D39)': null,
  'rise (sequence template), coded only in the store (D39)': null,
  'gene step (tree template), coded only in the store (D39)': null,  // the joint profile by default since D49 (STORE_GENE_CODED_ONLY)
  'frequency template, coded only refused on request (D39)': 'FREE_TEXT_CODED_ONLY_UNSUPPORTED',
  'a threshold that is not a plain decimal': 'VALUE_NOT_A_PLAIN_DECIMAL',
  'gene step (tree template), genes asked for': null,
  // D38 review: gene steps under coded-only waited for a joint profile with the store's coded-only option (D42; default since D49).
  'gene step (tree template), coded only, genes asked for': null,  // D49: the joint profile by default
  'gene step and rise (graph template), genes refused on request': 'GENE_CLASSIFICATION_UNSUPPORTED',
  'a gene step alone, coded only, genes asked for': null,  // D49: the joint profile by default
  'a gene step with an occurrence, genes asked for': 'OCCURRENCE_ON_GENE_STEP',
  // D42: under the store's coded only, a gene step is the joint profile (the default since D49); under D32's reading of
  // coded only, or with the joint profile refused on request, D38's refusal stands.
  'gene step (tree template), coded only in the store, joint asked for (D42)': null,
  'gene step and rise (graph template), coded only in the store, joint asked for (D42)': null,
  'gene step (tree template), coded only for the count, joint asked for (D42)': 'GENE_WITH_CODED_ONLY_UNSUPPORTED',
  'gene step (tree template), coded only in the store, joint refused on request (D42)': 'GENE_WITH_CODED_ONLY_UNSUPPORTED',
  'gene step (tree template), coded only, joint asked for (D42)': null,  // D49: the store's coded only is the default
  'blank template, coded only, the store option refused on request (D49)': 'FREE_TEXT_CODED_ONLY_UNSUPPORTED',
  'gene step (tree template), coded only, the store option refused on request (D49)': 'GENE_WITH_CODED_ONLY_UNSUPPORTED',
  'a count alone, coded only, the store option refused on request (D49)': null,  // D32's reading: a count is coded only
});
/* D52: the index period as a hidden step (rewritten), or, for a shape the rewrite cannot write, D49's point window with
   its reason (review, S2; the page then says the anchor's uncertainty is not accounted for). */
const POINTS = vm.runInContext(`(() => {
  const d = name => ({...defaultDef(), ...clone(TEMPLATES[name])});
  return {
    'a period from the first diagnosis (D52)': pointOf({...defaultDef(), ...clone(BLANK), window: {start: {kind: 'event', event: '@any_diagnosis', which: 'first', n: 1, offset: 0, unit: 'month'}, end: {kind: 'span', amount: 2, unit: 'year'}}}, 'off'),
    'a period from the first SCN1A result (D52)': pointOf({...defaultDef(), ...clone(BLANK), window: {start: {kind: 'event', event: 'gene:SCN1A', which: 'first', n: 1, offset: 0, unit: 'month'}, end: {kind: 'span', amount: 1, unit: 'year'}}}, 'off'),
    'a period from the 3rd seizure, stated findings (D52)': pointOf({...defaultDef(), ...clone(BLANK), window: {start: {kind: 'event', event: 'seizure', which: 'nth', n: 3, offset: 0, unit: 'month'}, end: {kind: 'span', amount: 12, unit: 'month'}}}, 'present'),
    'a period from the 3rd seizure, coded only (D52)': pointOf({...defaultDef(), ...clone(BLANK), window: {start: {kind: 'event', event: 'seizure', which: 'nth', n: 3, offset: 0, unit: 'month'}, end: {kind: 'span', amount: 12, unit: 'month'}}}, 'off'),
    'a period from a date to the first seizure (D52)': pointOf({...defaultDef(), ...clone(BLANK), window: {start: {kind: 'date', date: '2020-01-01'}, end: {kind: 'event', event: 'seizure', which: 'first', n: 1, offset: 0, unit: 'month'}}}, 'off'),
    'a period from 1 year of age to the last seizure (D52)': pointOf({...defaultDef(), ...clone(BLANK), window: {start: {kind: 'age', amount: 1, unit: 'year'}, end: {kind: 'event', event: 'seizure', which: 'last', n: 1, offset: 0, unit: 'month'}}}, 'off'),
    'a period from the first seizure to the first antiseizure start (D52)': pointOf({...defaultDef(), ...clone(BLANK), window: {start: {kind: 'event', event: 'seizure', which: 'first', n: 1, offset: 0, unit: 'month'}, end: {kind: 'event', event: 'antiseizure', which: 'first', n: 1, offset: 0, unit: 'month'}}}, 'off'),
    'a count alone, coded only, point windows on request (D52)': pointOf({...defaultDef(), nodes: [{id: 'A', concept: 'febrile_seizure', occ: 'count', n: 2, within: 'period'}], edges: []}, 'off', {indexAnchor: false}),
    'the blank template, coded only, point windows on request (D52)': pointOf({...defaultDef(), ...clone(BLANK)}, 'off', {indexAnchor: false}),
    'a count in the index period, coded only (D52)': pointOf({...defaultDef(), nodes: [{id: 'A', concept: 'febrile_seizure', occ: 'count', n: 2, within: 'period'}], edges: []}, 'off'),
    'a count within 6 months, coded only, the store option refused on request (D52)': pointOf({...defaultDef(), nodes: [{id: 'A', concept: 'seizure', occ: 'count', n: 3, within: {amount: 6, unit: 'month'}}], edges: []}, 'off', {codedOnly: null}),
  };
})()`, context);
assert.deepEqual(JSON.parse(JSON.stringify(POINTS)), {
  'a period from the first diagnosis (D52)': 'INDEX_ANCHOR_ANY_UNSUPPORTED',
  'a period from the first SCN1A result (D52)': 'INDEX_ANCHOR_GENE_UNSUPPORTED',
  'a period from the 3rd seizure, stated findings (D52)': 'FREE_TEXT_COUNT_UNSUPPORTED',
  'a period from the 3rd seizure, coded only (D52)': 'rewritten',
  'a period from a date to the first seizure (D52)': 'INDEX_DATE_START_WITH_EVENT_END_UNSUPPORTED',
  'a period from 1 year of age to the last seizure (D52)': 'rewritten',
  'a period from the first seizure to the first antiseizure start (D52)': 'rewritten',
  'a count alone, coded only, point windows on request (D52)': 'the D49 request',
  'the blank template, coded only, point windows on request (D52)': 'the D49 request',
  'a count in the index period, coded only (D52)': 'INDEX_PERIOD_COUNT_UNSUPPORTED',
  'a count within 6 months, coded only, the store option refused on request (D52)': 'FREE_TEXT_CODED_ONLY_UNSUPPORTED',
});

/* D37, rule 1: every page template, under each free-text option, is answered by the store or refused by a named code.
   An answered one must be a parity trajectory above (same window, scope, steps, constraints and free-text setting), so
   its differences from the page are pinned in EXPECTED. */
const TEMPLATE_TABLE = JSON.parse(vm.runInContext(`JSON.stringify((() => {
  const out = {};
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  for (const name of [...Object.keys(TEMPLATES), 'blank']) for (const freeText of ['off', 'present', 'possible']) {
    const def = {...defaultDef(), ...clone(name === 'blank' ? BLANK : TEMPLATES[name])};
    const code = refusalOf(def, freeText);
    const parity = Object.keys(CASES).find(k => (CASES[k].freeText || 'present') === freeText
      && same([CASES[k].window, CASES[k].scope, CASES[k].nodes, CASES[k].edges], [def.window, def.scope, def.nodes, def.edges]));
    out[name + ', ' + freeText] = code || (parity ? 'store: ' + parity : 'ANSWERED BUT NOT A PARITY TRAJECTORY');
  }
  return out;
})())`, context));
assert.deepEqual(TEMPLATE_TABLE, {
  'sequence, off': 'store: the sequence template, coded only',  // D49 (FREE_TEXT_CODED_ONLY_UNSUPPORTED before)
  'sequence, present': 'store: the sequence template (a rise)',
  'sequence, possible': 'FREE_TEXT_HEDGED_UNSUPPORTED',
  'tree, off': 'store: the tree template, coded only',  // D49 (GENE_WITH_CODED_ONLY_UNSUPPORTED before)
  'tree, present': 'store: the tree template',
  'tree, possible': 'FREE_TEXT_HEDGED_UNSUPPORTED',
  'graph, off': 'store: the graph template, coded only',  // D49 (GENE_WITH_CODED_ONLY_UNSUPPORTED before)
  'graph, present': 'store: the graph template',
  'graph, possible': 'FREE_TEXT_HEDGED_UNSUPPORTED',
  'frequency, off': 'store: the event-frequency template',  // D49 (FREE_TEXT_CODED_ONLY_UNSUPPORTED before)
  'frequency, present': 'FREE_TEXT_COUNT_UNSUPPORTED',
  'frequency, possible': 'FREE_TEXT_HEDGED_UNSUPPORTED',
  'blank, off': 'store: the blank template, coded only',  // D49 (FREE_TEXT_CODED_ONLY_UNSUPPORTED before)
  'blank, present': 'store: the blank template (the first seizure)',
  'blank, possible': 'FREE_TEXT_HEDGED_UNSUPPORTED',
});

/* D37, rule 1: the option sweep. A fixed grid of page definitions over every step option, relation, anchor, scope,
   index-period rule and free-text setting the page offers, each valid on the page (no `analyse` error), is translated
   and, unless the translator refuses it by name, run in the store. Each must be answered, or refused by a named code;
   a thrown error or a store failure without a code breaks the rule. The tally by outcome is pinned. */
const SWEEP_CASES = (() => {
  // D45: the grid lives in option_sweep.js, shared with coded_default.cjs; translated here by the page's translator.
  vm.runInContext(fs.readFileSync(path.join(here, 'option_sweep.js'), 'utf8'), context);
  return JSON.parse(vm.runInContext('JSON.stringify(optionSweepCases())', context));
})();
const sweepScratch = fs.mkdtempSync(path.join(os.tmpdir(), 'store-sweep-'));
const sweepSnapshot = run([path.join(here, 'store_parity.py'), 'build', cohort, path.join(sweepScratch, 'store')]).trim();
// D52: a sweep case whose period is a hidden step is narrowed by the server's rule first, as the page's server does.
const sweepRun = JSON.parse(run([path.join(here, 'index_hull.py'), 'narrow', sweepSnapshot], JSON.stringify({cohort, cases: SWEEP_CASES.filter(c => !c.refused)}))).cases;
const sweepStore = JSON.parse(run([path.join(here, 'store_parity.py'), 'sweep', sweepSnapshot], JSON.stringify({cohort, cases: sweepRun})));
fs.rmSync(sweepScratch, {recursive: true, force: true});
const sweepTally = {answered: 0, translator: {}, store: {}}, unnamed = [];
const patientCount = JSON.parse(fs.readFileSync(cohort, 'utf8')).patients.length;
for (const c of SWEEP_CASES) {
  if (c.refused) {
    if (c.refused.startsWith('UNNAMED')) unnamed.push(`${c.name}: ${c.refused}`);
    sweepTally.translator[c.refused] = (sweepTally.translator[c.refused] || 0) + 1;
    continue;
  }
  const answer = sweepStore[c.name];
  if (answer.error === 'UNNAMED') unnamed.push(`${c.name}: ${answer.message}`);
  if (answer.error) { sweepTally.store[answer.error] = (sweepTally.store[answer.error] || 0) + 1; continue; }
  assert.equal(Object.values(answer.counts).reduce((a, b) => a + b, 0), patientCount, `${c.name}: every patient has a result`);
  sweepTally.answered += 1;
}
assert.deepEqual(unnamed, [], 'page options that are neither answered by the store nor refused by a named code');
if (process.env.STORE_PARITY_PRINT) console.log(JSON.stringify({sweepTally, cases: SWEEP_CASES.length, store: Object.fromEntries(sweepRun.filter(c => sweepStore[c.name].error).map(c => [c.name, sweepStore[c.name].error]))}, null, 1));
// The store's two refusals here are page options with no meaning in its language: a rise "to at least 1 times" the
// baseline (INVALID_FACTOR: a factor must exceed 1), and the page's relaxation of "at most -1" to "at most -1.1",
// which narrows instead of widening (VALUE_RELAXATION_MUST_WIDEN). The page shows each as a labelled fallback.
if (DEV_COHORT) console.log('sweep tally (development cohort): ' + JSON.stringify(sweepTally));
else assert.deepEqual(sweepTally, {
  // D49: "coded only" is the store's profile 1.3 by default, and a gene step under it the joint profile, so the 157
  // coded-only and the 7 gene refusals are answered or refused by the store (D45's joint sweep: 449 answered; before
  // D49, 293 answered, FREE_TEXT_CODED_ONLY_UNSUPPORTED 157, GENE_WITH_CODED_ONLY_UNSUPPORTED 7, INVALID_FACTOR 10,
  // VALUE_RELAXATION_MUST_WIDEN 10).
  answered: 449,
  translator: {FREE_TEXT_COUNT_UNSUPPORTED: 51, NTH_STEP_HAS_NO_END: 2, FREE_TEXT_HEDGED_UNSUPPORTED: 31},
  store: {INVALID_FACTOR: 14, VALUE_RELAXATION_MUST_WIDEN: 14},
}, 'the option sweep changed');
console.log(`Option sweep: ${SWEEP_CASES.length} page definitions; ${sweepTally.answered} answered by the store; refused by the translator `
  + `${Object.entries(sweepTally.translator).sort().map(([k, v]) => `${k} ${v}`).join(', ')}; refused by the store `
  + `${Object.entries(sweepTally.store).sort().map(([k, v]) => `${k} ${v}`).join(', ') || 'none'}; none without a code.`);
/* D38, no change without the new fields: every parity and sweep request without a gene step gives, on the snapshot
   store_parity.py builds now (the converter wrapper of store_conditions_gene), the same output as on main's build
   (no gene reports), every field equal except the snapshot's and the context's identifiers and the labels' copy. */
// D42: the parity and sweep requests, written for the differential against main's code (d42_no_change.py) when asked.
if (process.env.STORE_PARITY_DUMP_CASES)
  fs.writeFileSync(process.env.STORE_PARITY_DUMP_CASES, JSON.stringify({cohort, cases: [...narrowed, ...sweepRun]}));
const changeScratch = fs.mkdtempSync(path.join(os.tmpdir(), 'store-gene-no-change-'));
const noChange = (() => {
  try {
    const wrapper = run([path.join(here, 'store_parity.py'), 'build', cohort, path.join(changeScratch, 'wrapper')]).trim();
    const main = run([path.join(here, 'gene_no_change.py'), 'build-main', cohort, path.join(changeScratch, 'main')]).trim();
    return JSON.parse(run([path.join(here, 'gene_no_change.py'), 'compare', wrapper, main], JSON.stringify({cohort, cases: [...narrowed, ...sweepRun]})));
  } finally { fs.rmSync(changeScratch, {recursive: true, force: true}); }
})();
assert.deepEqual(noChange.differences, [], 'a request without a gene step answered differently on the wrapper\'s build');
assert.equal(noChange.cases + noChange.gene_cases_skipped, cases.length + sweepRun.length, 'every request was compared or is a gene request');
assert.ok(noChange.results > 10000, 'the no-change differential was vacuous');
console.log(`No change without gene steps (D38): ${noChange.cases} requests (${noChange.results} patient results, ${noChange.refused} refused alike) `
  + `equal on the wrapper's build and on main's, but for identifiers; ${noChange.gene_cases_skipped} gene requests left out.`);
if (process.env.STORE_PARITY_PRINT) console.log(JSON.stringify(unresolvedShare, null, 1));
const share = names.map(n => `${n} ${unresolvedShare[n].unresolved}/${unresolvedShare[n].results}`
  + ` (${unresolvedShare[n].candidates} with a candidate)`).join('; ');
console.log(`Unresolved in the store, per trajectory: ${share}.`);
console.log(`Trajectory page and execution store: ${agree} of ${patients} patient results agree over ${names.length} trajectories `
  + `(${matched} store matches); ${patients - agree} differ, each by a named cause (${Object.entries(summary).sort().map(([k, v]) => `${k} ${v}`).join(', ')}); `
  + 'the store matched exactly the candidates the explanation reads.');
