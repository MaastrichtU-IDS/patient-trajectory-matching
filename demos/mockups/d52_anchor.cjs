/* D52: the index anchor as a hidden step of the query. The page side of D52's rules (decision log, D52).

   D52 writes an index period placed by an event as a hidden step of the store request (trajectory/store_request.js,
   `index_anchor_v1`), and the server narrows each base window to the hull of the periods the store's records allow
   (index_hull.py, `index_hull_v1`). This tool loads the page's own code (shared/core.js, trajectory/app.js,
   trajectory/store_request.js) into node:vm contexts, for this tree and for main's page (read from git at --base).

   D52b (the narrowing's cache) runs the same rules on fresh seeds: 5711-5720 (reference), 5721-5750 extended to 5820
   (usefulness), 5701 for development; the census and the reference also rule that the narrowing with the cache equals it
   without.

   D52c (index_simplify_v1, the server's simplification after the narrowing) runs them again on fresh seeds: 5911-5920
   (reference), 5921-5950 extended in order to 6020 (usefulness), 5901 for development. Every narrowed request is
   simplified (index_simplify.py simplify) before it is matched, as the server does. The reference also rules the
   simplification against index_anchor_reference.py's; the census rules, on every patient of every rewritten definition,
   the answer with the simplification against the answer without it (index_simplify.py compare: equal except in the
   declared classes, each verified on its facts), explains each changed result through main -> pinned -> D52b's form ->
   D52c's, and checks that every template's hidden-step key is one the server fills its cache with at start.

     node demos/mockups/d52_anchor.cjs reference [--output REPORT.json]
       Rule 2: on the page's cohort and seeds 5711-5720, every census definition under its settings, translated here,
       against index_anchor_reference.py (requests byte for byte, or one refusal code), and index_hull.py's narrowed
       windows against the reference's narrowing without SQL (tools/d52_measure.py reference-check).
     node demos/mockups/d52_anchor.cjs census [--output REPORT.json] [--cohort FILE]
       Rule 3, on the page's cohort: the templates, the option sweep and the Blank template under the period rules,
       main's translator against this tree's; structural checks; every changed result with a verified class; identity
       where no class applies.
     node demos/mockups/d52_anchor.cjs usefulness [--output REPORT.json]
       Rule 4: the five page templates under "Coded records only" (ruled) and four under stated findings (reported), over
       fresh cohorts (seeds 5721-5750, extended in order to 5820), the owner's counting rule; main's translation
       (`indexAnchor: false`) beside.

   D52_SMOKE=1 runs a development smoke: the undeclared seed 5701 (usefulness and reference) or the first 10 sweep
   definitions (census); never into verification/. Synthetic data only (generator patient ids); the reports hold counts,
   definition names and, for the census's changed results, generator patient ids of synthetic children. Needs DuckDB
   (store_parity.py) and git for main's page. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');

const here = __dirname, root = path.resolve(here, '../..');
const python = process.env.PYTHON || 'python';
const COHORT = path.join(here, 'data/synthetic-paediatric-rare.json');
const RUN_TIMEOUT_MS = 60 * 60 * 1000;
// As test_store_parity.cjs (D44): a child's stdin is a regular file, never a pipe.
const run = (args, input, opts = {}) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'd52-stdin-'));
  const file = path.join(dir, 'stdin');
  fs.writeFileSync(file, input === undefined ? '' : input);
  const fd = fs.openSync(file, 'r');
  try {
    return execFileSync(opts.command || python, args, {cwd: root, stdio: [fd, 'pipe', 'inherit'], encoding: 'utf8', maxBuffer: 1 << 30,
                                                      timeout: RUN_TIMEOUT_MS});
  } finally {
    fs.closeSync(fd);
    fs.rmSync(dir, {recursive: true, force: true});
  }
};
const arg = (name, fallback) => { const i = process.argv.indexOf(name); return i < 0 ? fallback : process.argv[i + 1]; };
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(path.join(root, file))).digest('hex');
const shaText = text => crypto.createHash('sha256').update(text).digest('hex');
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const bump = (o, k, n = 1) => { o[k] = (o[k] || 0) + n; };
const BASE = arg('--base', 'c977303');  // main before D52c (D52's census used 5f9fffb, D52b's 9b50317; recorded in the log)
const SMOKE = process.env.D52_SMOKE === '1';
const DEV_SEED = 5901;  // D52c (5501 for D52, 5701 for D52b)

const FILES = {core: 'demos/mockups/shared/core.js', app: 'demos/mockups/trajectory/app.js',
               translator: 'demos/mockups/trajectory/store_request.js', sweep: 'demos/mockups/option_sweep.js'};
const baseText = rel => run(['show', `${BASE}:${rel}`], undefined, {command: 'git'});
const hereText = rel => fs.readFileSync(path.join(root, rel), 'utf8');

/* The definitions: the page templates with the page's own window and scope; the Blank template under D46's twelve
   period rules and D52's four added shapes (two periods bounded by two events, two by a constant start and an event
   end). */
const DEFINITIONS = `
  var pageWindow = defaultDef().window;
  var shape = t => ({scope: 'from_start', window: clone(pageWindow), nodes: clone(t.nodes), edges: clone(t.edges)});
  var TEMPLATE_DEFS = {'sequence': shape(TEMPLATES.sequence), 'tree': shape(TEMPLATES.tree), 'graph': shape(TEMPLATES.graph),
                       'event frequency': shape(TEMPLATES.frequency), 'blank': shape(BLANK)};
  var ev = (event, which, n, offset) => ({kind: 'event', event, which, n, offset, unit: 'month'});
  var PERIOD_RULES = {
    'the first seizure for 24 months (the page\\'s default)': defaultDef().window,
    'birth to 6 years': {start: {kind: 'birth'}, end: {kind: 'age', amount: 6, unit: 'year'}},
    'from 2020-01-01 for 3 years': {start: {kind: 'date', date: '2020-01-01'}, end: {kind: 'span', amount: 3, unit: 'year'}},
    '12 months up to the last seizure': {start: {kind: 'span', amount: 12, unit: 'month'}, end: ev('seizure', 'last', 1, 0)},
    'a month before the 2nd-to-last febrile seizure to 10 years': {start: ev('febrile_seizure', 'nth_last', 2, -1), end: {kind: 'age', amount: 10, unit: 'year'}},
    'the first diagnosis for 2 years': {start: ev('@any_diagnosis', 'first', 1, 0), end: {kind: 'span', amount: 2, unit: 'year'}},
    'the first genetic result for 2 years': {start: ev('@any_genetic', 'first', 1, 0), end: {kind: 'span', amount: 2, unit: 'year'}},
    'the 2nd antiseizure start for 12 months': {start: ev('antiseizure', 'nth', 2, 0), end: {kind: 'span', amount: 12, unit: 'month'}},
    'the last valproate start, 6 months before it to it': {start: ev('valproate', 'last', 1, -6), end: {kind: 'span', amount: 6, unit: 'month'}},
    'the 3rd-from-last antiseizure start for 6 months': {start: ev('antiseizure', 'nth_last', 3, 0), end: {kind: 'span', amount: 6, unit: 'month'}},
    'the first SCN1A result for 1 year': {start: ev('gene:SCN1A', 'first', 1, 0), end: {kind: 'span', amount: 1, unit: 'year'}},
    'the 3rd seizure for 12 months': {start: ev('seizure', 'nth', 3, 0), end: {kind: 'span', amount: 12, unit: 'month'}},
    // D52's four added shapes.
    'from the first seizure to the first antiseizure start': {start: ev('seizure', 'first', 1, 0), end: ev('antiseizure', 'first', 1, 0)},
    'from the first seizure to the 3rd seizure': {start: ev('seizure', 'first', 1, 0), end: ev('seizure', 'nth', 3, 0)},
    'from birth to the first antiseizure start': {start: {kind: 'birth'}, end: ev('antiseizure', 'first', 1, 0)},
    'from 1 year of age to the last seizure': {start: {kind: 'age', amount: 1, unit: 'year'}, end: ev('seizure', 'last', 1, 0)},
  };
  // D52 review, S1: the rewritten shapes crossed with the four scopes and the relations from START (with a maximum, so
  // with its extend_max relaxation) and from END, so the translator's every branch meets the reference.
  var CROSS_RULES = ['from the first seizure to the first antiseizure start', 'from the first seizure to the 3rd seizure',
    'from birth to the first antiseizure start', 'from 1 year of age to the last seizure', '12 months up to the last seizure',
    'a month before the 2nd-to-last febrile seizure to 10 years', 'the last valproate start, 6 months before it to it'];
  var CROSS_PROBES = {'alone': [], 'within 6 months of the period start': [{from: 'START', to: 'A', rel: 'after', min: 0, max: 6, unit: 'month'}],
    'within a year either side of the period end': [{from: 'END', to: 'A', rel: 'after', min: -12, max: 12, unit: 'month'}]};
  var CROSS_DEFS = {};
  for (const rule of CROSS_RULES) for (const scope of Object.keys(SCOPES)) for (const [probe, edges] of Object.entries(CROSS_PROBES))
    CROSS_DEFS[[rule, scope, probe].join(' / ')] = {scope, window: clone(PERIOD_RULES[rule]), nodes: [{id: 'A', concept: 'seizure', occ: 'any'}], edges: clone(edges)};
  var BLANK_DEFS = Object.fromEntries(Object.entries(PERIOD_RULES).map(([k, w]) => [k, {scope: 'from_start', window: clone(w), nodes: clone(BLANK.nodes), edges: []}]));
  var translateOne = (def, freeText, options) => {
    const before = state.freeText;
    state.freeText = freeText;
    try { return storeRequest({...defaultDef(), ...def}, freeText, [...state.patients.keys()], options); }
    catch (e) { if (e instanceof StoreRefusal) return {refused: e.code}; return {refused: 'UNNAMED: ' + e.message}; }
    finally { state.freeText = before; }
  };
  // The store's closure (parents and classes, synthetic_ingest's ancestors), for the census's coupled class.
  var storeClosure = cid => { const out = new Set(), stack = [cid];
    while (stack.length) { const c = stack.pop(); if (out.has(c)) continue; out.add(c);
      for (const p of [...(state.concepts[c]?.parents || []), ...(state.concepts[c]?.classes || [])]) stack.push(p); }
    return out; };
  var related = (a, b) => storeClosure(a).has(b) || storeClosure(b).has(a);
  var rewriteCalls = () => typeof INDEX_REWRITES === 'undefined' ? null : INDEX_REWRITES.calls;
  // The page's own placement for each patient (main's rule, D46/D47): its reason, its window in grid minutes, and each
  // event bound's anchor (the page's point, the earliest time of the anchor event's most precise record) in grid minutes.
  var pageFacts = (window, freeText) => {
    const out = {}, q = {freeText, knownByIndex: false};
    for (const pid of state.patients.keys()) {
      const w = windowFor(evidenceFor(pid), {...q, window});
      const anchors = {};
      for (const side of ['start', 'end']) {
        const b = window[side];
        if (b.kind !== 'event') continue;
        const d = eventDay(indexViewFor(pid), q, b.event, b.which, b.n);
        anchors[side] = d == null ? null : toGrid(d);
      }
      out[pid] = {reason: w.reason || null, window: w.reason ? null : {start: toGrid(w.start), end: toGrid(w.end)}, anchors,
                  last: toGrid(dayOf(state.patients.get(pid), state.patients.get(pid).follow_up.last_contact))};
    }
    return out;
  };
  // The rewritten request with the page's own view perturbed (rule 3, structural): every event anchor at the latest
  // time its most precise record allows (D46's rule 8), or the index view emptied.
  var translatePerturbed = (def, freeText, how) => {
    const saved = new Map(state.indexViews);
    try {
      for (const pid of state.patients.keys()) {
        const view = indexViewFor(pid);
        const items = how === 'empty' ? [] : view.items.map(i => ({...i,
          time: i.time && i.time.best ? {...i.time, best: {...i.time.best, e: i.time.best.l}} : i.time,
          med: i.med && i.med.start ? {...i.med, start: {...i.med.start, e: i.med.start.l}} : i.med}));
        state.indexViews.set(pid, {...view, items});
      }
      return translateOne(def, freeText, {});
    } finally { state.indexViews = saved; }
  };
`;

function pageContext(cohortText, which) {
  const text = Object.fromEntries(Object.entries(FILES).map(([k, f]) => [k, which === 'main' ? baseText(f) : hereText(f)]));
  const stub = {addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; }};
  const context = vm.createContext({document: stub, addEventListener() {}, console, structuredClone, performance, DATA: cohortText,
                                    POST: () => { throw new Error('D52 never calls the page engine'); }});
  vm.runInContext(text.core + '\n' + text.app.replace(/\nmain\(\);\s*$/, '\n') + '\n' + text.translator + `
    state.data = JSON.parse(DATA); state.concepts = state.data.concepts;
    for (const [cid, c] of Object.entries(state.concepts)) for (const coding of c.codings) state.codeIndex.set(coding.system + '|' + coding.code, cid);
    for (const p of state.data.patients) state.patients.set(p.patient_id, p);
    state.freeText = 'present';
  ` + DEFINITIONS, context);
  vm.runInContext(text.sweep, context);
  return context;
}
const inContext = (context, code) => JSON.parse(vm.runInContext(`JSON.stringify(${code})`, context));
const translate = (context, def, freeText, options = {}) =>
  inContext(context, `translateOne(${JSON.stringify(def)}, ${JSON.stringify(freeText)}, ${JSON.stringify(options)})`);
const hasEvent = def => def.window.start.kind === 'event' || def.window.end.kind === 'event';
const outcome = r => r.refused ? `refused ${r.refused}` : r.index ? 'rewritten' : `profile ${(r.conditions || {}).profile || 'none'}`;
const SETTINGS = [['coded only', 'off'], ['stated findings', 'present']];

const ARTIFACTS = ['demos/mockups/d52_anchor.cjs', 'demos/mockups/option_sweep.js', 'demos/mockups/store_parity.py',
  'demos/mockups/index_hull.py', 'demos/mockups/index_simplify.py', 'demos/mockups/index_anchor_reference.py', 'tools/d52_measure.py',
  'demos/mockups/trajectory/store_request.js', 'demos/mockups/trajectory/app.js', 'demos/mockups/shared/core.js',
  'demos/mockups/synthetic/generate.py', 'patterns/store_conditions.py', 'patterns/store_conditions_coded.py',
  'patterns/store_conditions_gene.py', 'patterns/store_conditions_relative.py', 'patterns/store_conditions_counts.py',
  'patterns/execution_store.py', 'patterns/synthetic_ingest.py', 'patterns/trajectory_graph.py',
  'patterns/trajectory_times.py', 'patterns/temporal_stn.py'];
const artifacts = () => Object.fromEntries(ARTIFACTS.map(f => [f, sha(f)]));
const write = (output, report) => {
  if (SMOKE && output && path.resolve(output).startsWith(path.join(root, 'verification'))) throw new Error('a smoke run is never written into verification/');
  const text = JSON.stringify(report, null, 1) + '\n';
  if (output) fs.writeFileSync(output, text); else process.stdout.write(text);
};
const generate = (seed, file) => run(['demos/mockups/synthetic/generate.py', '--seed', String(seed), '--output', file]);
const build = (cohortPath, dir) => run([path.join(here, 'store_parity.py'), 'build', cohortPath, dir]).trim();

/* The store's answers to a list of cases ({name, query, conditions, windows, index?}): each case with an index block
   narrowed first (index_hull.py narrow) and simplified (index_simplify.py simplify, D52c), refusals by code
   (store_parity.py sweep), then the answered ones matched. With `compare`, each simplified case is also matched without
   the simplification and compared per patient (index_simplify.py compare). */
function answer(snapshot, cohortPath, cases, compare = false) {
  const narrowedOnly = JSON.parse(run([path.join(here, 'index_hull.py'), 'narrow', snapshot], JSON.stringify({cohort: cohortPath, cases}))).cases;
  const narrowed = JSON.parse(run([path.join(here, 'index_simplify.py'), 'simplify', snapshot], JSON.stringify({cohort: cohortPath, cases: narrowedOnly}))).cases;
  const swept = JSON.parse(run([path.join(here, 'store_parity.py'), 'sweep', snapshot], JSON.stringify({cohort: cohortPath, cases: narrowed})));
  const answered = narrowed.filter(c => !swept[c.name].error);
  const results = answered.length ? JSON.parse(run([path.join(here, 'store_parity.py'), 'match', snapshot], JSON.stringify({cohort: cohortPath, cases: answered}))) : {};
  const simplified = answered.filter(c => c.simplify_result);
  const compared = compare && simplified.length
    ? JSON.parse(run([path.join(here, 'index_simplify.py'), 'compare', snapshot], JSON.stringify({cohort: cohortPath, cases: simplified}))) : {};
  return {narrowed: Object.fromEntries(narrowed.map(c => [c.name, c])), swept, results, compared};
}

/* Rule 4: usefulness. Fresh seeds, declared in D52c before any measurement: 5921 to 5950, then 5951, 5952, ... in order
   to 6020 while a ruled template has fewer than 30 candidates pooled (the stop reads candidate counts only). */
// D52c's fresh seeds (D52's were 5511 to 5620, D52b's 5711 to 5820; D53's 5301 to 5400).
const SEEDS = Array.from({length: 30}, (_, i) => 5921 + i);
const EXTEND = Array.from({length: 70}, (_, i) => 5951 + i);
const REFERENCE_SEEDS = Array.from({length: 10}, (_, i) => 5911 + i);
const MINIMUM = 30, SHARE = 0.3;
const RULED = ['sequence', 'tree', 'graph', 'event frequency', 'blank'];
const RULED_NAMES = RULED.map(n => `${n}, coded only`);
const NTH_PROBE = 'the 3rd seizure for 12 months';
const planKeys = seed => JSON.parse(run(['-c', 'import json, sys; sys.path.insert(0, "demos/mockups/synthetic"); '
  + 'import generate as g; print(json.dumps({c.pid: c.key for c in g.build(int(sys.argv[1]))}))', String(seed)]));

function tally(results, name, conditions) {
  const candidates = results[name + '#with_candidate'];
  const statuses = {}, reasons = {};
  let unresolved = 0, exact = 0;
  for (const pid of candidates) {
    const r = results[name][pid];
    bump(statuses, r.status);
    if (r.status === 'exact') exact += 1;
    if (r.status === 'unresolved') { unresolved += 1; bump(reasons, r.reason); }
  }
  for (const [pid, r] of Object.entries(results[name]))
    if (r.status === 'unresolved' && !candidates.includes(pid)) throw new Error(`${name} ${pid}: unresolved without a candidate`);
  return {candidates: candidates.length, unresolved, exact, statuses, reasons, profile: (conditions || {}).profile || null,
          candidate_ids: candidates, unresolved_ids: candidates.filter(pid => results[name][pid].status === 'unresolved')};
}

function usefulness(output) {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'd52-usefulness-'));
  const perSeed = {}, used = [];
  let refusals = null;
  const t0 = Date.now();
  try {
    const measure = seed => {
      const cohortPath = path.join(work, `cohort-${seed}.json`);
      generate(seed, cohortPath);
      const cohortText = fs.readFileSync(cohortPath, 'utf8');
      const context = pageContext(cohortText, 'here');
      const defs = inContext(context, 'TEMPLATE_DEFS');
      defs[`event frequency under ${NTH_PROBE}`] = {...defs['event frequency'], window: inContext(context, `PERIOD_RULES[${JSON.stringify(NTH_PROBE)}]`)};
      const cases = [], r = {};
      for (const [name, def] of Object.entries(defs)) for (const [label, f] of SETTINGS) for (const [arm, options] of [['', {}], [' @main', {indexAnchor: false}]]) {
        const t = translate(context, def, f, options);  // the page's own call (no options), and D49's translation beside
        const key = `${name}, ${label}${arm}`;
        if (t.refused) r[key] = t.refused; else cases.push({name: key, ...t});
      }
      refusals = refusals || r;
      assert.deepEqual(r, refusals, `seed ${seed}: the refusals differ from the first seed's`);
      const snapshot = build(cohortPath, path.join(work, `store-${seed}`));
      const a = answer(snapshot, cohortPath, cases);
      fs.rmSync(path.join(work, `store-${seed}`), {recursive: true, force: true});
      const excluded = {};
      for (const c of cases) for (const why of Object.values((a.narrowed[c.name].index_result || {}).reasons || {})) bump(excluded[c.name] = excluded[c.name] || {}, why);
      perSeed[seed] = {children: JSON.parse(cohortText).patients.length, snapshot_id: path.basename(snapshot), keys: planKeys(seed),
                       cohort_sha256: shaText(cohortText),
                       store_refused: Object.fromEntries(cases.filter(c => a.swept[c.name].error).map(c => [c.name, a.swept[c.name].error])),
                       excluded_by_the_server: excluded,
                       templates: Object.fromEntries(cases.filter(c => !a.swept[c.name].error).map(c => [c.name, tally(a.results, c.name, c.conditions)]))};
      used.push(seed);
      console.error(`seed ${seed}: ` + RULED_NAMES.map(n => perSeed[seed].templates[n] ? `${n} ${perSeed[seed].templates[n].unresolved}/${perSeed[seed].templates[n].candidates}` : `${n} -`).join('; '));
    };
    for (const seed of SMOKE ? [DEV_SEED] : SEEDS) measure(seed);
    const pooledCandidates = n => used.reduce((k, s) => k + (perSeed[s].templates[n] ? perSeed[s].templates[n].candidates : 0), 0);
    for (const seed of SMOKE ? [] : EXTEND) { if (!RULED_NAMES.some(n => pooledCandidates(n) < MINIMUM)) break; measure(seed); }
    const names = [...new Set(used.flatMap(s => Object.keys(perSeed[s].templates)))];
    const pooled = {}, byKey = {}, readings = {};
    for (const name of names) {
      const p = pooled[name] = {candidates: 0, unresolved: 0, exact: 0, statuses: {}, reasons: {}};
      const k = byKey[name] = {};
      for (const s of used) {
        const t = perSeed[s].templates[name], keys = perSeed[s].keys;
        if (!t) continue;
        p.profile = t.profile;
        p.candidates += t.candidates; p.unresolved += t.unresolved; p.exact += t.exact;
        for (const [x, v] of Object.entries(t.statuses)) bump(p.statuses, x, v);
        for (const [x, v] of Object.entries(t.reasons)) bump(p.reasons, x, v);
        for (const pid of t.candidate_ids) {
          const e = k[keys[pid]] = k[keys[pid]] || {candidates: 0, unresolved: 0};
          e.candidates += 1;
          if (t.unresolved_ids.includes(pid)) e.unresolved += 1;
        }
      }
      const entries = Object.entries(k), noRef = entries.filter(([key]) => key !== 'reference');
      const sum = (es, f) => es.reduce((a, [, e]) => a + f(e), 0);
      readings[name] = {
        distinct_child_types: {types: entries.length, types_unresolved: entries.filter(([, e]) => e.unresolved > 0).length},
        all_candidates: {candidates: sum(entries, e => e.candidates), unresolved: sum(entries, e => e.unresolved)},
        without_C001: {candidates: sum(noRef, e => e.candidates), unresolved: sum(noRef, e => e.unresolved)},
        C001: k.reference || {candidates: 0, unresolved: 0},
      };
    }
    const barOf = n => {
      if (!readings[n]) return {refused: refusals[n] || 'refused by the store', met: false};
      const d = readings[n].distinct_child_types;
      return {types: d.types, types_unresolved: d.types_unresolved, share_of_types: d.types ? d.types_unresolved / d.types : null,
              candidates: pooled[n].candidates, unresolved: pooled[n].unresolved, exact: pooled[n].exact,
              without_C001: readings[n].without_C001, enough_candidates: pooled[n].candidates >= MINIMUM,
              met: pooled[n].candidates >= MINIMUM && d.types > 0 && d.types_unresolved <= SHARE * d.types};
    };
    const bar = Object.fromEntries(RULED_NAMES.map(n => [n, barOf(n)]));
    const stated = Object.fromEntries(RULED.map(n => `${n}, stated findings`).map(n => [n, barOf(n)]));
    const beside = Object.fromEntries([...RULED_NAMES, ...RULED.map(n => `${n}, stated findings`)].map(n => [n, barOf(n + ' @main')]));
    const coverage = {refused_under_coded_only: Object.fromEntries(Object.entries(refusals).filter(([k]) => k.endsWith(', coded only'))),
                      refused_under_stated_findings: Object.fromEntries(Object.entries(refusals).filter(([k]) => k.endsWith(', stated findings')))};
    coverage.met = RULED_NAMES.every(n => !(n in refusals));
    const report = {
      profile: 'd52c-anchor-usefulness-1.0',
      rule: ('D52c rule 4, D52\'s rule 4 (the owner\'s counting rule): each page template under "Coded records only", translated by this '
             + 'tree\'s page translator with no options (the index anchor as a hidden step; profile 1.3, the joint profile for '
             + 'Tree and Graph), narrowed by index_hull.py and simplified by index_simplify.py, pooled over the declared seeds: at least 30 patients with a '
             + 'window sent and a store candidate, and at most 30% of the distinct child types (PLAN keys; C001 is reference) '
             + 'with a candidate unresolved (a type is unresolved when any of its candidates is); none of the five refused. '
             + 'Reported: all candidates and the count without C001; the templates under stated findings with the same bar; '
             + 'D49\'s translation (indexAnchor false) on the same seeds; the Event frequency template under the 3rd seizure for 12 months'),
      declared_seeds: SEEDS, extension_seeds: EXTEND, seeds_used: used,
      setting: {window: 'from the first seizure, 24 months (the page\'s own)', scope: 'from_start',
                free_text: 'off (Coded records only), ruled; present (stated findings), reported',
                translator: 'trajectory/store_request.js as committed (STORE_INDEX_ANCHOR true), no options; beside it the same with indexAnchor false (D49\'s request)',
                store: 'store_parity.py build; index_hull.py narrow; index_simplify.py simplify; store_parity.py match (store_conditions.match)'},
      bar, coverage, met: RULED_NAMES.every(n => bar[n].met) && coverage.met,
      stated_findings_reported: stated, d49_translation_beside: beside,
      templates: pooled, shares_three_ways: readings, by_plan_key: byKey,
      independence: ('not independent children (D36 review, M1): C001 is the scripted reference child in every seed, and '
                     + 'every other child takes archetype, sex, birth year and overrides from the generator\'s fixed PLAN; '
                     + 'only the draws vary with the seed'),
      per_seed: Object.fromEntries(used.map(s => [s, {children: perSeed[s].children, cohort_sha256: perSeed[s].cohort_sha256,
        snapshot_id: perSeed[s].snapshot_id, store_refused: perSeed[s].store_refused, excluded_by_the_server: perSeed[s].excluded_by_the_server,
        templates: Object.fromEntries(Object.entries(perSeed[s].templates).map(([n, t]) => [n, Object.fromEntries(
          Object.entries(t).filter(([k]) => !k.endsWith('_ids')))]))}])),
      seconds: Math.round((Date.now() - t0) / 1000),
      artifacts: artifacts(), contains_patient_level_data: false, ...(SMOKE ? {smoke: true, seed: DEV_SEED} : {}),
    };
    write(output, report);
    const line = (n, b) => b.refused ? `${n} refused ${b.refused}` : `${n} ${b.types_unresolved}/${b.types} types, ${b.unresolved}/${b.candidates} (exact ${b.exact})${b.met ? '' : ' MISSED'}`;
    console.error('bar: ' + RULED_NAMES.map(n => line(n, bar[n])).join('; ') + `; coverage ${coverage.met ? 'met' : 'missed'}`);
    console.error('stated (reported): ' + Object.entries(stated).map(([n, b]) => line(n, b)).join('; '));
    console.error('D49 beside: ' + Object.entries(beside).map(([n, b]) => line(n, b)).join('; '));
  } finally {
    fs.rmSync(work, {recursive: true, force: true});
  }
}

/* The definitions every rule reads: templates and Blank-under-rules under both settings, and the option sweep under each
   definition's own setting. */
function definitionsOf(context, limit) {
  const out = [];
  for (const [name, def] of Object.entries(inContext(context, 'TEMPLATE_DEFS'))) for (const [label, f] of SETTINGS)
    out.push({group: 'template', name: `${name}, ${label}`, def, freeText: f});
  for (const [name, def] of Object.entries(inContext(context, 'BLANK_DEFS'))) for (const [label, f] of SETTINGS)
    out.push({group: 'blank', name: `blank, ${name}, ${label}`, def, freeText: f});
  for (const [name, def] of Object.entries(inContext(context, 'CROSS_DEFS'))) for (const [label, f] of SETTINGS)
    out.push({group: 'cross', name: `cross: ${name}, ${label}`, def, freeText: f});
  const sweep = inContext(context, 'optionSweepDefinitions()');
  for (const d of sweep.slice(0, limit)) out.push({group: 'sweep', name: `sweep: ${d.name}`, def: d.def, freeText: d.freeText});
  return out;
}

/* Rule 2: the reference differential. */
function reference(output) {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'd52-reference-'));
  const t0 = Date.now();
  try {
    const cohorts = SMOKE ? [['seed ' + DEV_SEED, DEV_SEED]] : [['page', null], ...REFERENCE_SEEDS.map(s => ['seed ' + s, s])];
    const per = {};
    for (const [label, seed] of cohorts) {
      const cohortPath = seed == null ? COHORT : path.join(work, `cohort-${seed}.json`);
      if (seed != null) generate(seed, cohortPath);
      const context = pageContext(fs.readFileSync(cohortPath, 'utf8'), 'here');
      const rows = [];
      for (const d of definitionsOf(context)) {
        if (!hasEvent(d.def)) continue;
        const base = translate(context, d.def, d.freeText, {indexAnchor: false});
        if (base.refused) continue;  // refused before D52's rewrite is reached: not the rewrite's to mirror
        const mine = translate(context, d.def, d.freeText);
        rows.push({name: d.name, window: d.def.window, nodes: d.def.nodes, free_text: d.freeText, coded_mode: 'store',
                   base: {query: base.query, conditions: base.conditions, windows: base.windows}, here: mine});
      }
      const snapshot = build(cohortPath, path.join(work, `store-${label.replace(' ', '-')}`));
      const file = path.join(work, `rows-${label.replace(' ', '-')}.json`);
      fs.writeFileSync(file, JSON.stringify({cohort: cohortPath, rows}));
      per[label] = JSON.parse(run(['tools/d52_measure.py', 'reference-check', snapshot, file]));
      console.error(`${label}: ${per[label].requests} requests, ${per[label].request_mismatches} request mismatches, `
                    + `${per[label].windows} windows, ${per[label].window_mismatches} window mismatches, `
                    + `${per[label].simplify_requests} simplified (${per[label].simplify_applied} applied), ${per[label].simplify_mismatches} mismatches`);
    }
    const total = k => Object.values(per).reduce((a, x) => a + x[k], 0);
    const report = {
      profile: 'd52c-anchor-reference-1.0',
      rule: ('D52c rule 2, D52\'s rule 2: on the page\'s cohort and seeds 5911 to 5920, every census definition with an event bound that '
             + 'D49\'s translation answers, under its settings: this tree\'s request equals index_anchor_reference.py\'s byte '
             + 'for byte, or both refuse with one code; index_hull.py\'s narrowed windows and reasons equal the '
             + 'reference\'s narrowing over the converted ingest (no SQL); the narrowing with the cache equals it without (D52b); '
             + 'and index_simplify.py\'s simplification (query, record, weak patients) equals the reference\'s (D52c). 0 mismatches in each'),
      cohorts: per, requests: total('requests'), request_mismatches: total('request_mismatches'),
      windows: total('windows'), window_mismatches: total('window_mismatches'),
      cache_identity: {requests: total('cache_requests'), not_identical: total('cache_not_identical')},
      simplification: {requests: total('simplify_requests'), mismatches: total('simplify_mismatches'), applied: total('simplify_applied'),
                       by_rule: Object.fromEntries(['c1', 'c2', 'c3'].map(k => [k, Object.values(per).reduce((a, x) => a + x.simplify_by_rule[k], 0)]))},
      met: total('request_mismatches') === 0 && total('window_mismatches') === 0 && total('requests') > 0
           && total('cache_not_identical') === 0 && total('cache_requests') > 0
           && total('simplify_mismatches') === 0 && total('simplify_applied') > 0,
      seconds: Math.round((Date.now() - t0) / 1000),
      artifacts: artifacts(), contains_patient_level_data: false, ...(SMOKE ? {smoke: true, seed: DEV_SEED} : {}),
    };
    write(output, report);
    console.error(`reference: ${report.requests} requests, ${report.request_mismatches} mismatches; ${report.windows} windows, ${report.window_mismatches} mismatches: ${report.met ? 'met' : 'MISSED'}`);
  } finally {
    fs.rmSync(work, {recursive: true, force: true});
  }
}

/* Rule 3: the movement census, on the page's cohort. */
const D52_CODES = ['INDEX_ANCHOR_ANY_UNSUPPORTED', 'INDEX_ANCHOR_GENE_UNSUPPORTED', 'INDEX_PERIOD_COUNT_UNSUPPORTED',
                   'INDEX_DATE_START_WITH_EVENT_END_UNSUPPORTED', 'FREE_TEXT_COUNT_UNSUPPORTED', 'FREE_TEXT_CODED_ONLY_UNSUPPORTED'];
// Whether a point-window reason fits the definition's shape (the translator's indexPointReason; D52 review, S2).
function pointFits(code, def, freeText, concepts) {
  const w = def.window, events = [w.start, w.end].filter(b => b.kind === 'event');
  const derived = b => (b.which === 'nth' || b.which === 'nth_last') && Math.max(1, +b.n || 1) > 1;
  switch (code) {
    case 'INDEX_ANCHOR_ANY_UNSUPPORTED': return events.some(b => b.event.startsWith('@any_'));
    case 'INDEX_ANCHOR_GENE_UNSUPPORTED': return events.some(b => (concepts[b.event] || {}).kind === 'gene');
    case 'FREE_TEXT_COUNT_UNSUPPORTED': return freeText !== 'off' && events.some(derived);
    case 'INDEX_PERIOD_COUNT_UNSUPPORTED': return def.nodes.some(n => n.occ === 'count' && n.within === 'period');
    case 'INDEX_DATE_START_WITH_EVENT_END_UNSUPPORTED': return w.end.kind === 'event' && (w.start.kind === 'date' || w.start.kind === 'age');
    case 'FREE_TEXT_CODED_ONLY_UNSUPPORTED': return freeText === 'off' && events.some(b => !derived(b));
    default: return false;
  }
}
const COUPLED = ['OCCURRENCE_TIME_COUPLED', 'OCCURRENCE_ORDER_UNCERTAIN', 'STEP_CONDITION_UNCERTAIN'];
const COVERAGE_REASONS = ['NO_SOURCE_COVERAGE', 'NO_SOURCE_RECORDS_CONCEPT'];
const ADMISSION = 'ADMISSION_EXCLUDED_RECORDS';
const resultKey = r => r ? JSON.stringify({status: r.status, certainty: r.certainty, relaxations: r.relaxations, reason: r.reason}) : 'none';
const statusText = r => r ? (r.certainty ? `${r.status}/${r.certainty}` : r.status) : '-';

/* The pinned request (D52 review, M2): main's request with only its scope written as D52 writes it (scope `any`, the
   scope as relations from START and to or from END), over main's point windows (the page's anchor). It differs from main
   only where D44's admission scope reads the scope; from D52's request only in the anchor (the page's point against the
   hidden step in every world) and the window-reading rules (the point window against the hull). */
function pinned(main, def) {
  const scope = main.query.scope, relations = [...main.query.relations];
  for (const n of def.nodes) {
    if (scope === 'from_start' || scope === 'within') relations.push({id: `index:scope:${n.id}`, kind: 'after', from: 'START', to: n.id, min: 0});
    if (scope === 'within') relations.push({id: `index:scope-end:${n.id}`, kind: 'after', from: n.id, to: 'END', min: 0});
    if (scope === 'after') relations.push({id: `index:scope-end:${n.id}`, kind: 'after', from: 'END', to: n.id, min: 0});
  }
  return {query: {...main.query, scope: 'any', relations}, conditions: main.conditions, windows: main.windows};
}

/* The classes of one changed result (main M against D52's H), through the pinned request P (D52 review, M2). M to P
   needs `admission_scope` (the excluded records main's scope admits over the point window differ from those `any`
   admits); P to H needs classes verified on their facts: the patient's eligibility, the window-reading rules over the
   point window against the hull (coverage, where both sides are `none` before the coverage rule; admission, where the
   excluded records each admits differ), the clamp of a relation from the period's start, a coupled hidden step, a stopped
   search; and only where the change is not the coverage rule's alone and neither side is excluded, the anchor itself: `anchor_moved`
   (the page's point outside the store's range) or `anchor_uncertain` (the range wider than a point), with
   `eligibility_partial` beside when some worlds end the period after the last contact. Each step that changed needs a
   class, or the result is unexplained. */
function classesOf(c) {
  const {m, p, h, page, ir, index, pid, hasStartMax, facts} = c;
  const out = [], unexplained = [];
  const range = step => (ir.ranges[step] || {})[pid] || [0, null, null];
  const bound = step => step === '@index_start' ? 'start' : 'end';
  const F = facts || {};
  const differ = (a, b, k) => F[a] && F[b] && !same(F[a][k], F[b][k]);
  if (resultKey(m) !== resultKey(p)) {
    if (differ('main', 'pinned', 'admitted') && [m.reason, p.reason].includes(ADMISSION)) out.push('admission_scope');
    else if (m.reason === 'SEARCH_BUDGET_EXHAUSTED' || p.reason === 'SEARCH_BUDGET_EXHAUSTED') out.push('search');
    else unexplained.push('main -> pinned');
  }
  if (resultKey(p) !== resultKey(h)) {
    const mine = [];
    if (p.status === 'excluded' && h.status !== 'excluded') {
      if (page.reason && /^no /.test(page.reason)) mine.push('no_anchor_on_page');
      else if (page.reason && ir.windows[pid]) mine.push('eligibility_gained');
    }
    if (h.status === 'excluded' && p.status !== 'excluded') {
      const why = ir.reasons[pid], sides = ['start', 'end'].filter(x => index[x]);
      if (why === 'INDEX_ANCHOR_NOT_RECORDED' && sides.some(x => range(index[x].step)[1] === null)) mine.push('excluded_here');
      if (why === 'INDEX_NO_POSSIBLE_PERIOD') {
        const base = ir.base_windows[pid];
        const s0 = index.start ? Math.max(base.start, range(index.start.step)[1] + index.start.offset) : base.start;
        const e0 = index.end ? Math.min(base.end, range(index.end.step)[2] + index.end.offset) : base.end;
        if (s0 > e0) mine.push('excluded_here');
      }
    }
    // Coverage: a `not_comparable` for coverage is the store's `none` turned by its coverage rule (D18), so where both
    // sides are `none` before that rule, the change is the rule's verdict over the point window against the hull alone.
    const preNone = r => r.status === 'none' || (r.status === 'not_comparable' && COVERAGE_REASONS.includes(r.reason));
    const coverageOnly = preNone(p) && preNone(h);
    if (coverageOnly) mine.push('coverage_hull');
    if (differ('pinned', 'hull', 'admitted') && [p.reason, h.reason].includes(ADMISSION)) mine.push('admission_hull');
    if (hasStartMax && index.start) {
      const [, lo] = range(index.start.step), at = page.anchors[bound(index.start.step)];
      if ((lo !== null && lo + index.start.offset < 0) || (at !== null && at !== undefined && at + index.start.offset < 0)) mine.push('clamp');
    }
    if (h.status === 'unresolved' && COUPLED.includes(h.reason) && c.related) mine.push('coupled');
    if (p.reason === 'SEARCH_BUDGET_EXHAUSTED' || h.reason === 'SEARCH_BUDGET_EXHAUSTED') mine.push('search');
    if (!coverageOnly && h.status !== 'excluded' && p.status !== 'excluded') {
      const steps = [...new Set(['start', 'end'].filter(x => index[x]).map(x => index[x].step))];
      for (const step of steps) {
        const [, lo, hi] = range(step), at = page.anchors[bound(step)];
        if (lo === null) continue;
        if (at === null || at === undefined || at < lo || at > hi) mine.push('anchor_moved');
        else if (hi > lo) mine.push('anchor_uncertain');
      }
      if (mine.some(x => x.startsWith('anchor_')) && ir.windows[pid]) {
        const base = ir.base_windows[pid];
        const side = (x, k) => index[x] ? (([, lo, hi]) => lo === null ? null : [lo + index[x].offset, hi + index[x].offset])(range(index[x].step)) : [k, k];
        const S = side('start', base.start), E = side('end', base.end);
        const one = index.start && index.end && index.start.step === index.end.step;
        if (S && E && ((index.end && (E[1] > page.last || E[0] < 0)) || (!one && S[1] >= E[0]))) mine.push('eligibility_partial');
      }
    }
    if (!mine.filter(x => x !== 'eligibility_partial').length) unexplained.push('pinned -> D52');
    out.push(...mine);
  }
  return {classes: [...new Set(out)].sort(), unexplained};
}

// Each step's concepts as the coverage rule reads them: its own, and those its relaxations widen it to.
function stepConcepts(req) {
  const out = {};
  for (const s of req.query.steps) out[s.id] = [s.concept];
  for (const x of [...req.query.relaxations, ...((req.conditions || {}).relaxations || [])])
    if (x.concept && out[x.step]) out[x.step].push(x.concept);
  return out;
}

function census(output) {
  const cohortPath = path.resolve(arg('--cohort', COHORT));
  const cohortText = fs.readFileSync(cohortPath, 'utf8'), cohort = JSON.parse(cohortText);
  const ctx = {main: pageContext(cohortText, 'main'), here: pageContext(cohortText, 'here')};
  const concepts = cohort.concepts;
  const t0 = Date.now();
  const defs = definitionsOf(ctx.here, SMOKE ? 10 : undefined);
  assert.ok(same(defs.map(d => d.name), definitionsOf(ctx.main, SMOKE ? 10 : undefined).map(d => d.name)), 'the definitions differ from main\'s');
  const rows = [];
  const structural = {pin_differs_from_main: [], no_event_bound_changed: [], no_event_bound_rewrite_calls: 0,
                      refused_here_answered_on_main: [], refused_on_main_changed: [], point_not_main_request: [],
                      point_reason_not_fitting: [], perturbed_differs: [], perturbations_checked: 0};
  const point = {definitions: 0, sweep_definitions: 0, by_reason: {}, by_group: {}};
  for (const d of defs) {
    const main = translate(ctx.main, d.def, d.freeText);
    const pin = translate(ctx.here, d.def, d.freeText, {indexAnchor: false});
    const before = inContext(ctx.here, 'rewriteCalls()');
    const mine = translate(ctx.here, d.def, d.freeText);
    const calls = inContext(ctx.here, 'rewriteCalls()') - before;
    if (!same(pin, main)) structural.pin_differs_from_main.push(d.name);
    if (!hasEvent(d.def)) {
      if (!same(mine, main)) structural.no_event_bound_changed.push(d.name);
      structural.no_event_bound_rewrite_calls += calls;
    }
    if (main.refused && !same(mine, main)) structural.refused_on_main_changed.push(d.name);
    if (!main.refused && mine.refused) structural.refused_here_answered_on_main.push(`${d.name}: ${mine.refused}`);
    if (mine.index_anchor) {
      // D52 review, S2: a point period is D49's request, unchanged, with a reason that fits its shape.
      const {index_anchor: marker, ...request} = mine;
      if (!same(request, main)) structural.point_not_main_request.push(d.name);
      if (!(D52_CODES.includes(marker.reason) && pointFits(marker.reason, d.def, d.freeText, concepts))) structural.point_reason_not_fitting.push(`${d.name}: ${marker.reason}`);
      point.definitions += 1; bump(point.by_reason, marker.reason); bump(point.by_group, d.group);
      if (d.group === 'sweep') point.sweep_definitions += 1;
    }
    if (mine.index) {
      structural.perturbations_checked += 1;
      for (const how of ['late', 'empty'])
        if (!same(inContext(ctx.here, `translatePerturbed(${JSON.stringify(d.def)}, ${JSON.stringify(d.freeText)}, ${JSON.stringify(how)})`), mine))
          structural.perturbed_differs.push(`${d.name} (${how})`);
    }
    rows.push({...d, main, mine});
  }
  structural.met = ['pin_differs_from_main', 'no_event_bound_changed', 'refused_here_answered_on_main', 'refused_on_main_changed',
                    'point_not_main_request', 'point_reason_not_fitting', 'perturbed_differs'].every(k => !structural[k].length)
                   && structural.no_event_bound_rewrite_calls === 0 && structural.perturbations_checked > 0;

  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'd52-census-'));
  try {
    const snapshot = build(cohortPath, path.join(work, 'store'));
    const requests = new Map();
    const nameOf = req => {
      const {index_anchor: _, ...body} = req;
      const key = JSON.stringify(body);
      if (!requests.has(key)) requests.set(key, `q${requests.size}`);
      return requests.get(key);
    };
    for (const r of rows) {
      for (const side of ['main', 'mine']) if (!r[side].refused) r[side + 'Name'] = nameOf(r[side]);
      if (r.mine.index && !r.main.refused) r.pinnedName = nameOf(pinned(r.main, r.def));
    }
    const cases = [...requests.entries()].map(([key, name]) => ({name, ...JSON.parse(key)}));
    const a = answer(snapshot, cohortPath, cases, true);
    const unnamed = Object.entries(a.swept).filter(([, x]) => x.error === 'UNNAMED').map(([n, x]) => `${n}: ${x.message}`);
    const pids = cohort.patients.map(p => p.patient_id);
    // D52b: every narrowed request, with the cache and without it, identical (ruled); how many reused a cached range.
    const cached = Object.values(a.narrowed).filter(c => c.index_result).map(c => c.index_result.cache);
    const cacheIdentity = {requests: cached.length, hits: cached.filter(c => c.hit).length,
                           not_identical: cached.filter(c => !c.identical_without_cache).length};
    cacheIdentity.met = cacheIdentity.requests > 0 && cacheIdentity.hits > 0 && cacheIdentity.not_identical === 0;
    // D52c (rule 3b, ruled): on every patient of every simplified request, the answer with index_simplify_v1 equals the
    // answer without it but in the declared classes, each verified on its facts (index_simplify.py compare).
    const simplification = {requests: 0, applied: 0, by_rule: {c1: 0, c2: 0, c3: 0}, results: 0, changed: 0, by_class: {},
                            unexplained: [], unexplained_count: 0, bindings_differ: 0, refused: []};
    for (const [name, x] of Object.entries(a.compared)) {
      if (x.error) { simplification.refused.push(`${name}: ${x.error}`); continue; }
      simplification.requests += 1;
      const rec = x.record;
      if (rec.dropped.length || Object.keys(rec.merged).length || rec.existential) simplification.applied += 1;
      if (rec.dropped.length) simplification.by_rule.c1 += 1;
      if (Object.keys(rec.merged).length) simplification.by_rule.c2 += 1;
      if (rec.existential) simplification.by_rule.c3 += 1;
      simplification.results += x.results; simplification.changed += x.changed; simplification.bindings_differ += x.bindings_differ;
      for (const [k, v] of Object.entries(x.by_class)) bump(simplification.by_class, k, v);
      simplification.unexplained_count += x.unexplained.length;
      for (const u of x.unexplained.slice(0, 5)) if (simplification.unexplained.length < 30) simplification.unexplained.push({request: name, ...u});
    }
    simplification.met = simplification.requests > 0 && simplification.applied > 0 && simplification.unexplained_count === 0
                         && !simplification.refused.length;
    // D52c (pre-registered pre-fill): every template's hidden-step key, under both settings, is one the server fills its
    // narrowing's cache with when its store opens (index_hull.PREFILL).
    const prefill = JSON.parse(run(['-c', 'import json, sys; sys.path.insert(0, "demos/mockups"); import index_hull; print(json.dumps(index_hull.PREFILL))']));
    const keyOf = (req, sid) => {
      const st = req.query.steps.find(s => s.id === sid), cond = ((req.conditions || {}).steps || {})[sid];
      const kinds = new Set((req.conditions || {}).exclude_kinds || []);
      if (st.occurrence === 'first' || st.occurrence === 'last') return [st.concept, 1, st.occurrence === 'last', [...kinds].sort()];
      const [[which, k]] = Object.entries(cond.occurrence);
      kinds.add('mention');
      return [st.concept, k, which === 'nth_last', [...kinds].sort()];
    };
    const prefillCheck = {template_keys: [], not_prefilled: []};
    for (const r of rows.filter(x => x.group === 'template' && x.mine.index)) {
      for (const sid of new Set(['start', 'end'].map(s => r.mine.index[s] && r.mine.index[s].step).filter(Boolean))) {
        const key = keyOf(r.mine, sid);
        prefillCheck.template_keys.push(`${r.name}: ${JSON.stringify(key)}`);
        if (!prefill.some(k => same(k, key))) prefillCheck.not_prefilled.push(`${r.name}: ${JSON.stringify(key)}`);
      }
    }
    prefillCheck.met = prefillCheck.template_keys.length > 0 && !prefillCheck.not_prefilled.length;
    const facts = new Map();  // window+setting -> page facts (main's page)
    const pageOf = (def, freeText) => {
      const key = JSON.stringify([def.window, freeText]);
      if (!facts.has(key)) facts.set(key, inContext(ctx.main, `pageFacts(${JSON.stringify(def.window)}, ${JSON.stringify(freeText)})`));
      return facts.get(key);
    };
    // The changed results of rewritten definitions answered on both sides (a point period's request is main's).
    const changed = [];
    let compared = 0, eventCases = 0, refusedAlike = 0;
    for (const r of rows) {
      if (!r.mine.index || r.main.refused) continue;
      const errs = [r.mainName, r.mineName, r.pinnedName].map(n => a.swept[n].error || null);
      if (errs.some(Boolean)) {
        if (errs.every(e => e === errs[0])) refusedAlike += 1;
        else changed.push({row: r, pid: null, store_refusal: errs});
        continue;
      }
      eventCases += 1;
      const page = pageOf(r.def, r.freeText), ir = a.narrowed[r.mineName].index_result;
      const hasStartMax = r.main.query.relations.some(x => x.from === 'START' && 'max' in x);
      const hidden = r.mine.query.steps.filter(x => x.id.startsWith('@index_')).map(x => x.concept);
      const users = r.def.nodes.map(n => n.concept);
      const related = inContext(ctx.here, `${JSON.stringify(hidden)}.some(h => ${JSON.stringify(users)}.some(u => related(u, h)))`);
      const cmp = a.compared[r.mineName] || {};
      for (const pid of pids) {
        compared += 1;
        // D52c: `hs` is this tree's answer (simplified), `h` the same request without the simplification (D52b's form).
        const m = a.results[r.mainName][pid], hs = a.results[r.mineName][pid], p = a.results[r.pinnedName][pid];
        const h = cmp.without ? cmp.without[pid] : hs;
        if (resultKey(m) === resultKey(hs) && resultKey(h) === resultKey(hs)) continue;
        changed.push({row: r, pid, m, p, h, hs, simplifiedClasses: (cmp.classes || {})[pid] || [], page: page[pid], ir,
                      index: r.mine.index, hasStartMax, related});
      }
    }
    // Admission for the changed results (tools/d52_measure.py facts): main's scope over the point window, `any` over the
    // point window (the pinned request), `any` over the hull (D52).
    const items = changed.filter(c => c.pid).map((c, i) => ({key: String(i), patient_id: c.pid, steps: stepConcepts(c.row.mine), sides: {
      main: {scope: c.row.main.query.scope, window: c.row.main.windows[c.pid]}, pinned: {scope: 'any', window: c.row.main.windows[c.pid]},
      hull: {scope: c.row.mine.query.scope, window: c.ir.windows[c.pid]}}}));
    const factsOf = items.length ? JSON.parse(run(['tools/d52_measure.py', 'facts', snapshot], JSON.stringify({cohort: cohortPath, items}))) : {};
    // The option sweep as the page offers it, tallied as the parity test tallies it (rule 5's pin is taken from here).
    const sweepAsOffered = {};
    for (const side of ['main', 'mine']) {
      const t = sweepAsOffered[side === 'mine' ? 'here' : 'main'] = {answered: 0, translator: {}, store: {}};
      for (const r of rows.filter(x => x.group === 'sweep')) {
        if (r[side].refused) { bump(t.translator, r[side].refused); continue; }
        const error = a.swept[r[side + 'Name']].error;
        if (error) bump(t.store, error); else t.answered += 1;
      }
    }
    const byClass = {}, byTransition = {}, bySetting = {}, byStep = {}, unexplained = [];
    let k = 0;
    for (const c of changed) {
      if (!c.pid) { unexplained.push({definition: c.row.name, store_refusal: c.store_refusal}); continue; }
      const f = factsOf[String(k++)];
      const {classes, unexplained: gaps} = classesOf({...c, facts: f});
      // D52c: the last step of the chain, D52b's form -> this tree's, by index_simplify.py compare's verified classes.
      if (resultKey(c.h) !== resultKey(c.hs)) {
        if (c.simplifiedClasses.length) classes.push(...c.simplifiedClasses.map(x => `simplified_${x}`));
        else gaps.push('D52b form -> D52c');
      }
      bump(byTransition, `${statusText(c.m)} -> ${statusText(c.hs)}`);
      bump(bySetting, c.row.freeText);
      bump(byStep, (resultKey(c.m) === resultKey(c.p) ? 'pinned equals main' : resultKey(c.p) === resultKey(c.h) ? 'pinned equals D52' : 'differs from both')
                   + (resultKey(c.h) === resultKey(c.hs) ? '' : '; simplified'));
      // Identity: a step of the chain that changed with no verified class is unexplained.
      if (gaps.length) unexplained.push({definition: c.row.name, patient_id: c.pid, steps: gaps, classes, main: c.m, pinned: c.p, d52b_form: c.h, here: c.hs, facts: f,
                                         windows: {main: c.row.main.windows[c.pid], hull: c.ir.windows[c.pid]}});
      bump(byClass, [...new Set(classes)].sort().join(' + ') || 'none');
    }
    const report = {
      profile: 'd52c-anchor-census-1.0',
      rule: ('D52c rule 3, D52b\'s rule 3 (D52\'s with the cache\'s identity) with the simplification, on the page\'s cohort: the templates and the Blank template under D46\'s '
             + 'twelve period rules and D52\'s four added shapes, the cross grid of the added shapes with the probes (both settings), '
             + 'and every option-sweep definition (its own setting); main\'s translator (from git at the base) against this '
             + 'tree\'s. Structural: indexAnchor false reproduces main\'s request; a definition with no event bound has a '
             + 'byte-identical request and 0 rewrite calls; nothing answered on main is refused, nothing refused on main changes; '
             + 'a point period is main\'s request with a reason fitting its shape; the rewritten request is byte-identical with '
             + 'the page\'s anchors at their latest and with the index view emptied. Every changed result of a rewritten '
             + 'definition answered on both sides is explained through the pinned request (main\'s request with D52\'s scope '
             + 'writing, over main\'s point windows): each step of main -> pinned -> D52b\'s form -> D52c\'s (simplified) that changed '
             + 'needs a class verified on its facts, or the result is unexplained. D52c, ruled: the narrowing with the cache equals it '
             + 'without; on every patient of every simplified request the answer with index_simplify_v1 equals the answer without it '
             + 'but in the declared classes (decided, undecided_both, admission, claim_withdrawn), each verified on its facts; '
             + 'every template\'s hidden-step key under both settings is in index_hull.PREFILL'),
      base: BASE, cohort: {children: pids.length, sha256: shaText(cohortText), page_cohort: cohortPath === COHORT},
      definitions: rows.length, rewritten_answered_on_both: eventCases, results_compared: compared,
      results_changed: changed.filter(c => c.pid).length, store_refused_alike: refusedAlike,
      structural, point_window: point, sweep_as_offered: sweepAsOffered, cache_identity: cacheIdentity,
      simplification, prefill: prefillCheck,
      changed_by_class: byClass, changed_by_transition: byTransition, changed_by_setting: bySetting, changed_by_chain: byStep,
      unexplained: unexplained.slice(0, 50), unexplained_count: unexplained.length,
      p2_comparison: {d46_uncertainty_census: '397 of 13,824 event-anchored results changed with each anchor at its latest',
                      here: `${changed.filter(c => c.pid).length} of ${compared} results of rewritten definitions changed against main's point windows`},
      unnamed: unnamed.slice(0, 20),
      met: structural.met && !unexplained.length && !unnamed.length && cacheIdentity.met && simplification.met && prefillCheck.met,
      seconds: Math.round((Date.now() - t0) / 1000),
      artifacts: artifacts(), contains_patient_level_data: false, ...(SMOKE ? {smoke: true} : {}),
    };
    write(output, report);
    console.error(`census: ${report.results_changed} of ${compared} changed; unexplained ${unexplained.length}; point periods ${point.definitions} `
                  + `(${point.sweep_definitions} in the sweep); structural ${structural.met ? 'met' : 'MISSED'}; chain ${JSON.stringify(byStep)}; classes ${JSON.stringify(byClass)}; `
                  + `simplification ${simplification.changed} of ${simplification.results} changed, ${simplification.unexplained_count} unexplained ${JSON.stringify(simplification.by_class)} `
                  + `(rules ${JSON.stringify(simplification.by_rule)}); prefill ${prefillCheck.met ? 'met' : 'MISSED'}`);
  } finally {
    fs.rmSync(work, {recursive: true, force: true});
  }
}

/* Rule 6: the requests the latency tool times (tools/benchmark_index_anchor.py), on the page's cohort. Each template
   under "Coded records only" as the page sends it (the candidate, with its index block) and with D49's point windows
   (the reference, `indexAnchor: false`); two controls D52 leaves byte-identical (constant periods), with the rewrite's
   call count for each (0); and the shapes timed at 300,000 (the kept build holds no series and no gene label), whose
   windows the tool computes there. */
function requests(output) {
  const cohortText = fs.readFileSync(COHORT, 'utf8');
  const context = pageContext(cohortText, 'here');
  const t = inContext(context, 'TEMPLATE_DEFS'), rules = inContext(context, 'PERIOD_RULES');
  const nth = {...t['event frequency'], window: rules[NTH_PROBE]};
  const page = {}, scale = {}, controls = {}, calls = {};
  const pair = def => ({candidate: translate(context, def, 'off'), reference: translate(context, def, 'off', {indexAnchor: false})});
  for (const [name, def] of [...Object.entries(t), [`event frequency under ${NTH_PROBE}`, nth]]) {
    page[name] = pair(def);
    assert.ok(page[name].candidate.index && !page[name].reference.index, `${name}: not a rewritten request`);
  }
  for (const name of ['sequence', 'blank']) {
    const def = {...t[name], window: rules['birth to 6 years']};
    const before = inContext(context, 'rewriteCalls()');
    controls[`${name} from birth to 6 years`] = translate(context, def, 'off');
    calls[`${name} from birth to 6 years`] = inContext(context, 'rewriteCalls()') - before;
    assert.ok(same(controls[`${name} from birth to 6 years`], translate(context, def, 'off', {indexAnchor: false})), 'a control moved');
  }
  const strip = (def, gone) => inContext(context, `withoutNodes(${JSON.stringify(def)}, new Set(${JSON.stringify(gone)}))`);
  const shapes = {'event frequency': t['event frequency'], 'blank': t.blank, 'sequence without its rise': strip(t.sequence, ['C']),
                  'tree without its gene and rise steps': strip(t.tree, ['B', 'D']), 'graph without its gene and rise steps': strip(t.graph, ['C', 'E']),
                  [`event frequency under ${NTH_PROBE}`]: nth};
  for (const [name, def] of Object.entries(shapes)) {
    const p = pair(def);
    scale[name] = {candidate: {query: p.candidate.query, conditions: p.candidate.conditions, index: p.candidate.index},
                   reference: {query: p.reference.query, conditions: p.reference.conditions}};
  }
  const report = {profile: 'd52c-anchor-requests-1.0', cohort_sha256: shaText(cohortText), translator_sha256: sha(FILES.translator),
                  page, page_controls: controls, control_rewrite_calls: calls, scale, contains_patient_level_data: false};
  write(output, report);
}

module.exports = {SEEDS, EXTEND, REFERENCE_SEEDS, DEV_SEED, answer, definitionsOf, pageContext, inContext, translate,
                  classesOf, pointFits, pinned, stepConcepts, D52_CODES};

if (require.main === module) {
  const command = process.argv[2];
  const output = arg('--output');
  if (command === 'usefulness') usefulness(output);
  else if (command === 'reference') reference(output);
  else if (command === 'census') census(output);
  else if (command === 'requests') requests(output);
  else { console.error('usage: node demos/mockups/d52_anchor.cjs reference|census|usefulness [--output FILE]'); process.exit(2); }
}
