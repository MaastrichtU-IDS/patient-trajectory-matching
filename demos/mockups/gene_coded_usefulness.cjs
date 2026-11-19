/* D42: the usefulness test for gene steps under "coded only" in the store (the joint profile), over 30 synthetic cohorts,
   and the differential against main's code on D36's, D38's and D39's usefulness requests.

   For each declared generator seed, a cohort of 48 children (demos/mockups/synthetic/generate.py --seed) is built into an
   execution store by store_parity.py build (the converter emitting series, through the converter wrapper of
   patterns/store_conditions_gene.py). The trajectory page's Tree and Graph templates, gene step included, are translated
   by the page's own translator (trajectory/store_request.js) under the page's "coded only" setting with the store's own
   exclusion of letter mentions and the joint profile asked for (`codedOnly: 'store'`, `geneCodedOnly: true`; profile
   execution-store-conditions-gene-coded-1.0), at the page's own window (from the first seizure for 24 months) and scope
   (from_start), and matched in the store (store_parity.py match, through the dispatcher store_conditions.match). The same
   templates with stated findings (D38's ruled setting) are matched beside them and reported, not ruled. For each it
   counts the patients with a store candidate and an index window, and the unresolved results among them, with reasons,
   per seed and pooled; by distinct child type (the generator's PLAN keys; C001 is `reference`; a type is unresolved when
   any of its candidates is), over all candidates, and without C001.

   The same run is D42's differential against main's code on these cohorts: D36's, D38's and D39's usefulness requests
   (each tool's own definitions, read from it, with its own options and free-text settings) are answered on this snapshot
   through this tree's dispatcher and main's (d42_no_change.py; main's patterns/ extracted from git at --main, default
   origin/main), and must agree in every field but the context identifier and the two edited modules' digests.

     node demos/mockups/gene_coded_usefulness.cjs [--main REF] [--output REPORT.json]

   The protocol is fixed here, as D42 declared it before measuring: seeds 1001 to 1030, no extension. The bar (decision
   log, D42, rule 3) is per template: answered by the store, at least one candidate, and at most 30% of the distinct child
   types with a candidate unresolved. Synthetic data only; the report holds counts and synthetic patient ids. Needs
   DuckDB. */
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
const OUTPUT = arg('--output', null), MAIN = arg('--main', 'origin/main');
const SEEDS = Array.from({length: 30}, (_, i) => 1001 + i);  // declared in D42 before any measurement
// A smoke run on one undeclared seed, never written as the report.
const SMOKE = process.env.GENE_CODED_USEFULNESS_SMOKE_SEED ? [+process.env.GENE_CODED_USEFULNESS_SMOKE_SEED] : null;
const extra = process.argv.slice(2).filter((a, i, all) => !(['--output', '--main'].includes(a) || ['--output', '--main'].includes(all[i - 1])));
if (extra.length || (SMOKE && OUTPUT)) {
  console.error('usage: node demos/mockups/gene_coded_usefulness.cjs [--main REF] [--output FILE]  (the seeds are fixed by D42)');
  process.exit(2);
}
const planKeys = seed => JSON.parse(run(['-c', 'import json, sys; sys.path.insert(0, "demos/mockups/synthetic"); '
  + 'import generate as g; print(json.dumps({c.pid: c.key for c in g.build(int(sys.argv[1]))}))', String(seed)]));

const TEMPLATE_NAMES = ['tree, coded only', 'graph, coded only'];
const DEFINITIONS = `
  var pageWindow = {start: {kind: 'event', event: 'seizure', which: 'first', n: 2, offset: 0, unit: 'month'}, end: {kind: 'span', amount: 24, unit: 'month'}};
  var CASES = {};
  for (const name of ['tree', 'graph']) {
    CASES[name + ', coded only'] = {scope: 'from_start', window: pageWindow, nodes: clone(TEMPLATES[name].nodes), edges: clone(TEMPLATES[name].edges), freeText: 'off'};
    CASES[name + ', stated findings'] = {scope: 'from_start', window: pageWindow, nodes: clone(TEMPLATES[name].nodes), edges: clone(TEMPLATES[name].edges), freeText: 'present'};
  }
`;
const OPTIONS = {rises: true, genes: true, codedOnly: 'store', geneCodedOnly: true};
// The usefulness requests of D36, D38 and D39, read from their own tools, each with its tool's options: the requests the
// differential against main's code runs (D42, rule 2).
const definitionsOf = tool => {
  const found = fs.readFileSync(path.join(here, tool), 'utf8').match(/const DEFINITIONS = `([\s\S]*?)`;/);
  assert.ok(found, `${tool} no longer declares its DEFINITIONS`);
  return found[1];
};
const EARLIER = [
  {label: 'D36', tool: 'rise_usefulness.cjs', cases: 'CASES', options: {rises: true}},
  {label: 'D38', tool: 'gene_usefulness.cjs', cases: 'GENE_CASES', options: {rises: true, genes: true}},
  {label: 'D39', tool: 'coded_usefulness.cjs', cases: 'CASES', options: {rises: true, codedOnly: 'store', geneCodedOnly: false}},
];

// Each definition translated with its own free-text setting (which also places the page's index windows).
function translate(cohortText, definitions, casesName, options, prefix) {
  const stub = {addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; }};
  const context = vm.createContext({document: stub, addEventListener() {}, console, structuredClone,
                                    POST: () => { throw new Error('the usefulness test never calls the page engine'); },
                                    DATA: cohortText, OPTIONS: options, PREFIX: prefix});
  const page = fs.readFileSync(path.join(here, 'trajectory/app.js'), 'utf8').replace(/\nmain\(\);\s*$/, '\n');
  const translator = fs.readFileSync(path.join(here, 'trajectory/store_request.js'), 'utf8');
  vm.runInContext(fs.readFileSync(path.join(here, 'shared/core.js'), 'utf8') + '\n' + page + '\n' + translator + `
    state.data = JSON.parse(DATA); state.concepts = state.data.concepts;
    for (const [cid, c] of Object.entries(state.concepts)) for (const coding of c.codings) state.codeIndex.set(coding.system + '|' + coding.code, cid);
    for (const p of state.data.patients) state.patients.set(p.patient_id, p);
  ` + definitions, context);
  return vm.runInContext(`(() => {
    const pids = [...state.patients.keys()];
    return Object.entries(${casesName}).map(([name, def]) => {
      state.freeText = def.freeText || 'present';
      try {
        const {query, conditions, windows} = storeRequest({...defaultDef(), ...def}, state.freeText, pids, OPTIONS);
        return {name: PREFIX + name, query, conditions, windows};
      } catch (e) { if (!(e instanceof StoreRefusal)) throw e; return {name: PREFIX + name, refused: e.code}; }
      finally { state.freeText = 'present'; }
    });
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
    out[c.name] = {candidates: candidates.length, unresolved, statuses, reasons, profile: (c.conditions || {}).profile || null,
                   candidate_ids: candidates, unresolved_ids: candidates.filter(pid => results[c.name][pid].status === 'unresolved')};
  }
  return out;
}

const strip = t => Object.fromEntries(Object.entries(t).filter(([k]) => !k.endsWith('_ids')));
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(path.join(root, file))).digest('hex');
const ARTIFACTS = ['demos/mockups/gene_coded_usefulness.cjs', 'demos/mockups/d42_no_change.py', 'demos/mockups/rise_usefulness.cjs',
  'demos/mockups/gene_usefulness.cjs', 'demos/mockups/coded_usefulness.cjs', 'demos/mockups/store_parity.py',
  'demos/mockups/trajectory/store_request.js', 'demos/mockups/trajectory/app.js', 'demos/mockups/shared/core.js',
  'demos/mockups/synthetic/generate.py', 'patterns/store_conditions.py', 'patterns/store_conditions_gene.py',
  'patterns/store_conditions_coded.py', 'patterns/store_conditions_relative.py', 'patterns/execution_store.py',
  'patterns/synthetic_ingest.py', 'patterns/trajectory_graph.py', 'patterns/trajectory_times.py', 'patterns/temporal_stn.py'];

const work = fs.mkdtempSync(path.join(os.tmpdir(), 'gene-coded-usefulness-'));
const perSeed = {};
let names = null;
try {
  const mainRoot = path.join(work, 'main');
  const mainCommit = run(['demos/mockups/d42_no_change.py', 'extract', MAIN, mainRoot]).trim();
  const used = SMOKE || SEEDS;
  const noChange = {cases: 0, results: 0, refused: 0, joint_cases_skipped: 0, by_profile: {}, differences: []};
  for (const seed of used) {
    const cohortPath = path.join(work, `cohort-${seed}.json`);
    run(['demos/mockups/synthetic/generate.py', '--seed', String(seed), '--output', cohortPath]);
    const cohortText = fs.readFileSync(cohortPath, 'utf8');
    const mine = translate(cohortText, DEFINITIONS, 'CASES', OPTIONS, '');
    names = names || mine.map(c => c.name);
    const earlier = EARLIER.flatMap(e => translate(cohortText, definitionsOf(e.tool), e.cases, e.options, e.label + ' '));
    const snapshot = run(['demos/mockups/store_parity.py', 'build', cohortPath, path.join(work, `store-${seed}`)]).trim();
    const on = JSON.parse(run(['demos/mockups/store_parity.py', 'match', snapshot], JSON.stringify({cohort: cohortPath, cases: mine.filter(c => !c.refused)})));
    const same = JSON.parse(run(['demos/mockups/d42_no_change.py', 'compare', snapshot, mainRoot],
                                JSON.stringify({cohort: cohortPath, cases: earlier.filter(c => !c.refused)})));
    for (const k of ['cases', 'results', 'refused', 'joint_cases_skipped']) noChange[k] += same[k];
    for (const [p, v] of Object.entries(same.by_profile)) noChange.by_profile[p] = (noChange.by_profile[p] || 0) + v;
    noChange.differences.push(...same.differences.map(d => ({seed, ...d})));
    perSeed[seed] = {children: JSON.parse(cohortText).patients.length, snapshot_id: path.basename(snapshot), keys: planKeys(seed),
                     cohort_sha256: crypto.createHash('sha256').update(cohortText).digest('hex'), templates: tally(on, mine),
                     no_change: {cases: same.cases, results: same.results, refused: same.refused,
                                 translator_refused: earlier.filter(c => c.refused).length, differences: same.differences.length}};
    fs.rmSync(path.join(work, `store-${seed}`), {recursive: true, force: true});
    const line = names.map(n => perSeed[seed].templates[n].refused ? `${n} refused ${perSeed[seed].templates[n].refused}`
      : `${n} ${perSeed[seed].templates[n].unresolved}/${perSeed[seed].templates[n].candidates}`).join('; ');
    console.error(`seed ${seed}: ${line}; no change ${same.cases} requests, ${same.differences.length} differences`);
  }
  // Pooled, per PLAN key, and the shares three ways.
  const pooled = {}, byKey = {}, readings = {};
  for (const n of names) {
    const refused = used.map(s => perSeed[s].templates[n].refused).find(Boolean);
    if (refused) { pooled[n] = {refused}; continue; }
    const p = pooled[n] = {candidates: 0, unresolved: 0, statuses: {}, reasons: {}, profile: perSeed[used[0]].templates[n].profile};
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
    return [n, {answered: true, profile: pooled[n].profile, child_types: d.types, child_types_unresolved: d.types_unresolved,
                share: d.types ? d.types_unresolved / d.types : null, candidates: pooled[n].candidates,
                unresolved: pooled[n].unresolved,
                met: pooled[n].candidates >= 1 && d.types > 0 && d.types_unresolved <= 0.3 * d.types}];
  }));
  const report = {
    profile: 'd42-gene-coded-usefulness-1.0',
    rule: ('per template (Tree and Graph with their gene step under "coded only", the joint profile, at the page\'s own window '
           + 'and scope), pooled over seeds 1001 to 1030: answered by the store, at least one patient with a store candidate and '
           + 'an index window, and at most 30% of the distinct child types (PLAN keys) with a candidate unresolved; all '
           + 'candidates and the count without C001 reported; the same templates with stated findings reported beside them'),
    declared_seeds: SEEDS, seeds_used: used, smoke: !!SMOKE,
    setting: {window: 'from the first seizure, 24 months (the page\'s own)', scope: 'from_start',
              free_text: 'off (coded records only) for the ruled templates; present (stated findings) beside them',
              translator: 'trajectory/store_request.js with rises, genes, codedOnly: store and geneCodedOnly asked for',
              converter: 'patterns/synthetic_ingest.py emitting series, through the store_conditions_gene wrapper'},
    bar,
    templates: Object.fromEntries(TEMPLATE_NAMES.map(n => [n, pooled[n]])),
    stated_findings: Object.fromEntries(names.filter(n => !TEMPLATE_NAMES.includes(n)).map(n => [n, pooled[n]])),
    shares: readings,
    independence: ('not independent children (D36 review, M1): C001 is the scripted reference child in every seed, and every '
                   + 'other child takes archetype, sex, birth year and overrides from the generator\'s fixed PLAN; only the '
                   + 'draws vary with the seed; the bar counts distinct child types (owner rule, 2026-11-16)'),
    by_plan_key: byKey,
    no_change: {rule: ('D36\'s, D38\'s and D39\'s usefulness requests, each tool\'s own, answered on each cohort\'s snapshot '
                       + 'through this tree\'s store_conditions and main\'s: every field equal except the context identifier '
                       + 'and the digests of the two modules D42 edits; candidates equal; refusals the same'),
                main: mainCommit, ...noChange},
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
