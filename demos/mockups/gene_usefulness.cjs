/* D38: the usefulness test for gene steps in the store, over 30 synthetic cohorts, and the no-change differential.

   For each declared generator seed, a cohort of 48 children (demos/mockups/synthetic/generate.py --seed) is built into an
   execution store by store_parity.py build (the converter emitting series, through the converter wrapper of
   patterns/store_conditions_gene.py, which keeps each genetic result's reports and classifications in its evidence).
   The trajectory page's Tree and Graph templates, gene step included, are translated by the page's own translator
   (trajectory/store_request.js, rises and genes asked for) at the page's default setting (window from the second
   seizure for 24 months, scope from_start, stated findings) and matched in the store (store_parity.py match). For
   each template it counts the patients with a store candidate and an index window, and the unresolved results among
   them, with reasons, per seed and pooled; by distinct child type (the generator's PLAN keys; C001 is `reference`;
   a type is unresolved when any of its candidates is), over all candidates, and without C001.

   The same run is the no-change differential on these cohorts: D36's usefulness requests (its templates and variants,
   read from rise_usefulness.cjs, none with a gene step) are run on this snapshot and on main's build of the same cohort
   (gene_no_change.py), and must agree in every field but identifiers.

     node demos/mockups/gene_usefulness.cjs [--output REPORT.json]

   The protocol is fixed here, as D38 declared it before measuring: seeds 1001 to 1030, no extension. The bar (decision
   log, D38) is per template: answered by the store, at least one candidate, and at most 30% of the distinct child types
   with a candidate unresolved. Synthetic data only; the report holds counts and synthetic patient ids. Needs DuckDB. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');

const here = __dirname, root = path.resolve(here, '../..');
const python = process.env.PYTHON || 'python';
const run = (args, input) => execFileSync(python, args, {cwd: root, input, encoding: 'utf8', maxBuffer: 1 << 28});
const arg = (name, fallback) => { const i = process.argv.indexOf(name); return i < 0 ? fallback : process.argv[i + 1]; };
const OUTPUT = arg('--output', null);
const SEEDS = Array.from({length: 30}, (_, i) => 1001 + i);  // declared in D38 before any measurement
// A smoke run on one undeclared seed (the committed cohort's, 20261103), never written as the report.
const SMOKE = process.env.GENE_USEFULNESS_SMOKE_SEED ? [+process.env.GENE_USEFULNESS_SMOKE_SEED] : null;
if ((process.argv.length > 2 && !(process.argv.length === 4 && process.argv[2] === '--output')) || (SMOKE && OUTPUT)) {
  console.error('usage: node demos/mockups/gene_usefulness.cjs [--output FILE]  (the seeds are fixed by D38)'); process.exit(2);
}
const planKeys = seed => JSON.parse(run(['-c', 'import json, sys; sys.path.insert(0, "demos/mockups/synthetic"); '
  + 'import generate as g; print(json.dumps({c.pid: c.key for c in g.build(int(sys.argv[1]))}))', String(seed)]));

const TEMPLATE_NAMES = ['tree', 'graph'];
const DEFINITIONS = `
  var pageWindow = {start: {kind: 'event', event: 'seizure', which: 'first', n: 2, offset: 0, unit: 'month'}, end: {kind: 'span', amount: 24, unit: 'month'}};
  var firstWindow = {start: {kind: 'event', event: 'seizure', which: 'first', n: 1, offset: 0, unit: 'month'}, end: {kind: 'span', amount: 36, unit: 'month'}};
  var GENE_CASES = {};
  for (const name of ['tree', 'graph']) {
    GENE_CASES[name] = {scope: 'from_start', window: pageWindow, nodes: clone(TEMPLATES[name].nodes), edges: clone(TEMPLATES[name].edges)};
    GENE_CASES[name + ', from the first seizure for 36 months'] = {scope: 'from_start', window: firstWindow, nodes: clone(TEMPLATES[name].nodes), edges: clone(TEMPLATES[name].edges)};
    GENE_CASES[name + ', coded only'] = {scope: 'from_start', window: pageWindow, nodes: clone(TEMPLATES[name].nodes), edges: clone(TEMPLATES[name].edges), freeText: 'off'};
  }
`;
// D36's usefulness requests, read from its own tool so that they are exactly its (none carries a gene step).
const D36 = (() => {
  const source = fs.readFileSync(path.join(here, 'rise_usefulness.cjs'), 'utf8');
  const found = source.match(/const DEFINITIONS = `([\s\S]*?)`;/);
  assert.ok(found, 'rise_usefulness.cjs no longer declares its DEFINITIONS');
  return found[1];
})();

function translate(cohortText) {
  const stub = {addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; }};
  const context = vm.createContext({document: stub, addEventListener() {}, console, structuredClone,
                                    POST: () => { throw new Error('the usefulness test never calls the page engine'); },
                                    DATA: cohortText});
  const page = fs.readFileSync(path.join(here, 'trajectory/app.js'), 'utf8').replace(/\nmain\(\);\s*$/, '\n');
  const translator = fs.readFileSync(path.join(here, 'trajectory/store_request.js'), 'utf8');
  vm.runInContext(fs.readFileSync(path.join(here, 'shared/core.js'), 'utf8') + '\n' + page + '\n' + translator + `
    state.data = JSON.parse(DATA); state.concepts = state.data.concepts;
    for (const [cid, c] of Object.entries(state.concepts)) for (const coding of c.codings) state.codeIndex.set(coding.system + '|' + coding.code, cid);
    for (const p of state.data.patients) state.patients.set(p.patient_id, p);
  ` + DEFINITIONS + D36, context);
  return vm.runInContext(`(() => {
    const pids = [...state.patients.keys()];
    const gene = Object.entries(GENE_CASES).map(([name, def]) => {
      state.freeText = def.freeText || 'present';
      try {
        const {query, conditions, windows} = storeRequest({...defaultDef(), ...def}, state.freeText, pids, {rises: true, genes: true});
        return {name, query, conditions, windows};
      } catch (e) { if (!(e instanceof StoreRefusal)) throw e; return {name, refused: e.code}; }
    });
    state.freeText = 'present';
    const d36 = Object.entries(CASES).map(([name, def]) => {
      const {query, conditions, windows} = storeRequest({...defaultDef(), ...def}, state.freeText, pids, {rises: true});
      return {name: 'D36 ' + name, query, conditions, windows};
    });
    return {gene, d36};
  })()`, context);
}

function tally(results, cases) {
  const out = {};
  for (const c of cases) {
    if (c.refused) { out[c.name] = {refused: c.refused}; continue; }
    const candidates = results[c.name + '#with_candidate'];
    const statuses = {}, reasons = {};
    let unresolved = 0;
    for (const pid of candidates) {
      const r = results[c.name][pid];
      statuses[r.status] = (statuses[r.status] || 0) + 1;
      if (r.status === 'unresolved') { unresolved += 1; reasons[r.reason] = (reasons[r.reason] || 0) + 1; }
    }
    for (const [pid, r] of Object.entries(results[c.name]))
      if (r.status === 'unresolved' && !candidates.includes(pid)) throw new Error(`${c.name} ${pid}: unresolved without a candidate`);
    out[c.name] = {candidates: candidates.length, unresolved, statuses, reasons,
                   candidate_ids: candidates, unresolved_ids: candidates.filter(pid => results[c.name][pid].status === 'unresolved')};
  }
  return out;
}

const strip = t => Object.fromEntries(Object.entries(t).filter(([k]) => !k.endsWith('_ids')));
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(path.join(root, file))).digest('hex');
const ARTIFACTS = ['demos/mockups/gene_usefulness.cjs', 'demos/mockups/gene_no_change.py', 'demos/mockups/rise_usefulness.cjs',
  'demos/mockups/store_parity.py', 'demos/mockups/trajectory/store_request.js', 'demos/mockups/trajectory/app.js',
  'demos/mockups/shared/core.js', 'demos/mockups/synthetic/generate.py', 'patterns/store_conditions_gene.py',
  'patterns/store_conditions_relative.py', 'patterns/execution_store.py', 'patterns/synthetic_ingest.py',
  'patterns/trajectory_graph.py', 'patterns/trajectory_times.py', 'patterns/temporal_stn.py'];

const work = fs.mkdtempSync(path.join(os.tmpdir(), 'gene-usefulness-'));
const perSeed = {};
let names = null;
try {
  const used = SMOKE || SEEDS;
  const noChange = {cases: 0, results: 0, refused: 0, differences: []};
  for (const seed of used) {
    const cohortPath = path.join(work, `cohort-${seed}.json`);
    run(['demos/mockups/synthetic/generate.py', '--seed', String(seed), '--output', cohortPath]);
    const cohortText = fs.readFileSync(cohortPath, 'utf8');
    const {gene, d36} = translate(cohortText);
    names = names || gene.map(c => c.name);
    const snapshot = run(['demos/mockups/store_parity.py', 'build', cohortPath, path.join(work, `store-${seed}`)]).trim();
    const answered = gene.filter(c => !c.refused);
    const on = JSON.parse(run(['demos/mockups/store_parity.py', 'match', snapshot], JSON.stringify({cohort: cohortPath, cases: answered})));
    const main = run(['demos/mockups/gene_no_change.py', 'build-main', cohortPath, path.join(work, `main-${seed}`)]).trim();
    const same = JSON.parse(run(['demos/mockups/gene_no_change.py', 'compare', snapshot, main], JSON.stringify({cohort: cohortPath, cases: d36})));
    assert.equal(same.gene_cases_skipped, 0);
    noChange.cases += same.cases; noChange.results += same.results; noChange.refused += same.refused;
    noChange.differences.push(...same.differences.map(d => ({seed, ...d})));
    perSeed[seed] = {children: JSON.parse(cohortText).patients.length, snapshot_id: path.basename(snapshot), keys: planKeys(seed),
                     cohort_sha256: crypto.createHash('sha256').update(cohortText).digest('hex'), templates: tally(on, gene),
                     no_change: {cases: same.cases, results: same.results, refused: same.refused, differences: same.differences.length}};
    fs.rmSync(path.join(work, `main-${seed}`), {recursive: true, force: true});
    const line = TEMPLATE_NAMES.map(n => `${n} ${perSeed[seed].templates[n].unresolved}/${perSeed[seed].templates[n].candidates}`).join('; ');
    console.error(`seed ${seed}: ${line}; no change ${same.cases} requests, ${same.differences.length} differences`);
  }
  // Pooled, per PLAN key, and the shares three ways.
  const pooled = {}, byKey = {}, readings = {};
  for (const n of names) {
    const refused = used.map(s => perSeed[s].templates[n].refused).find(Boolean);
    if (refused) { pooled[n] = {refused}; continue; }
    const p = pooled[n] = {candidates: 0, unresolved: 0, statuses: {}, reasons: {}};
    const k = byKey[n] = {};
    for (const s of used) {
      const t = perSeed[s].templates[n], keys = perSeed[s].keys;
      p.candidates += t.candidates; p.unresolved += t.unresolved;
      for (const [x, v] of Object.entries(t.statuses)) p.statuses[x] = (p.statuses[x] || 0) + v;
      for (const [x, v] of Object.entries(t.reasons)) p.reasons[x] = (p.reasons[x] || 0) + v;
      for (const pid of t.candidate_ids) {
        const e = k[keys[pid]] = k[keys[pid]] || {candidates: 0, unresolved: 0, cohorts: 0};
        e.candidates += 1; e.cohorts += 1;
        if (t.unresolved_ids.includes(pid)) e.unresolved += 1;
      }
    }
    const entries = Object.entries(k), noRef = entries.filter(([key]) => key !== 'reference');
    const sum = (es, f) => es.reduce((a, [, e]) => a + f(e), 0);
    readings[n] = {
      distinct_child_types: {types: entries.length, types_unresolved: entries.filter(([, e]) => e.unresolved > 0).length},
      all_candidates: {candidates: sum(entries, e => e.candidates), unresolved: sum(entries, e => e.unresolved)},
      without_C001: {candidates: sum(noRef, e => e.candidates), unresolved: sum(noRef, e => e.unresolved)},
      C001: k.reference || {candidates: 0, unresolved: 0, cohorts: 0},
    };
  }
  const bar = Object.fromEntries(TEMPLATE_NAMES.map(n => {
    if (pooled[n].refused) return [n, {answered: false, refused: pooled[n].refused, met: false}];
    const d = readings[n].distinct_child_types;
    return [n, {answered: true, child_types: d.types, child_types_unresolved: d.types_unresolved,
                share: d.types ? d.types_unresolved / d.types : null, candidates: pooled[n].candidates,
                unresolved: pooled[n].unresolved,
                met: pooled[n].candidates >= 1 && d.types > 0 && d.types_unresolved <= 0.3 * d.types}];
  }));
  const report = {
    profile: 'd38-gene-usefulness-1.0',
    rule: ('per template (Tree and Graph with their gene step, the page\'s default setting), pooled over seeds 1001 to 1030: '
           + 'answered by the store, at least one patient with a store candidate and an index window, and at most 30% of the '
           + 'distinct child types (PLAN keys) with a candidate unresolved; all candidates and the count without C001 reported; '
           + 'variants reported'),
    declared_seeds: SEEDS, seeds_used: used, smoke: !!SMOKE,
    setting: {window: 'from the second seizure, 24 months (the page\'s own); variants as named', scope: 'from_start',
              free_text: 'present (the page\'s own); the coded-only variant as named',
              translator: 'trajectory/store_request.js with rises and genes asked for',
              converter: 'patterns/synthetic_ingest.py emitting series, through the store_conditions_gene wrapper'},
    bar,
    templates: Object.fromEntries(TEMPLATE_NAMES.map(n => [n, pooled[n]])),
    variants: Object.fromEntries(names.filter(n => !TEMPLATE_NAMES.includes(n)).map(n => [n, pooled[n]])),
    shares: readings,
    independence: ('not independent children (D36 review, M1): C001 is the scripted reference child in every seed, and every '
                   + 'other child takes archetype, sex, birth year and overrides from the generator\'s fixed PLAN; only the '
                   + 'draws vary with the seed; the bar counts distinct child types (owner rule, 2026-11-16)'),
    by_plan_key: byKey,
    no_change: {rule: ('D36\'s usefulness requests (no gene step) on the wrapper\'s build and on main\'s build of each cohort: '
                       + 'every output field equal except the snapshot and context identifiers and the labels\' copy'),
                ...noChange},
    per_seed: Object.fromEntries(used.map(s => [s, {children: perSeed[s].children, cohort_sha256: perSeed[s].cohort_sha256,
      snapshot_id: perSeed[s].snapshot_id, no_change: perSeed[s].no_change,
      templates: Object.fromEntries(names.map(n => [n, strip(perSeed[s].templates[n])]))}])),
    artifacts: Object.fromEntries(ARTIFACTS.map(f => [f, sha(f)])),
  };
  const text = JSON.stringify(report, null, 1) + '\n';
  if (OUTPUT) fs.writeFileSync(OUTPUT, text); else if (!SMOKE) process.stdout.write(text);
  console.error('bar: ' + TEMPLATE_NAMES.map(n => bar[n].answered
    ? `${n} ${bar[n].child_types_unresolved}/${bar[n].child_types} child types unresolved, ${bar[n].unresolved}/${bar[n].candidates} candidates (${bar[n].met ? 'met' : 'missed'})`
    : `${n} refused ${bar[n].refused}`).join('; ') + `; no change: ${noChange.cases} requests, ${noChange.differences.length} differences`);
} finally {
  fs.rmSync(work, {recursive: true, force: true});
}
