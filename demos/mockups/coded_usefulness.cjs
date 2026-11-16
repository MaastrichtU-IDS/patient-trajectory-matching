/* D39: the usefulness test for "coded only" in the store (letter mentions left out of every step, profile 1.3), over
   many synthetic cohorts.

   For each declared generator seed, a distinct cohort of 48 children (demos/mockups/synthetic/generate.py --seed) is
   built into an execution store by the converter emitting series, with the diary's summaries labelled as period
   readings (store_parity.py build). Every page template is translated by the page's own translator
   (trajectory/store_request.js) under the page's "coded only" setting, with `codedOnly: 'store'`, with the page's own
   window and scope, as the parity test translates them, and matched in the store (store_parity.py match, which calls
   store_conditions_coded.match). Tree and Graph run without their gene step, which the store refuses by name. For each
   template it counts the patients with a store candidate and an index window, and the unresolved results among them
   (with their reasons), per seed and pooled. The same templates under the page's default setting (stated findings) are
   matched beside them and reported, not ruled (Event frequency is refused there by name).

     node demos/mockups/coded_usefulness.cjs [--output REPORT.json]

   The protocol is fixed here, as D39 declared it before measuring: seeds 1001 to 1030, then 1031, 1032, ... in order
   while some template has fewer than 30 candidates (counted before any status is read). No other seed is accepted.

   The bar (decision log, D39) is counted by distinct child types (the owner's rule of 2026-11-16): a child type is the
   generator's PLAN key (C001 is the key `reference`); a type is a candidate when any of its children is one in some
   seed, and unresolved when any of its candidates is unresolved; per template, at most 30% of the candidate types may be
   unresolved. The share over all candidates and with C001 left out is reported beside it. Synthetic data only; the
   report holds counts. Needs DuckDB (patterns/requirements-store.lock.txt). */
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
const MINIMUM = 30, SHARE = 0.3;
const SEEDS = Array.from({length: 30}, (_, i) => 1001 + i);  // declared in D39 before any measurement
const EXTEND = Array.from({length: 70}, (_, i) => 1031 + i);  // the extension rule's order
if (process.argv.length > 2 && !(process.argv.length === 4 && process.argv[2] === '--output')) {
  console.error('usage: node demos/mockups/coded_usefulness.cjs [--output FILE]  (the seeds are fixed by D39)'); process.exit(2);
}
const planKeys = seed => JSON.parse(run(['-c', 'import json, sys; sys.path.insert(0, "demos/mockups/synthetic"); '
  + 'import generate as g; print(json.dumps({c.pid: c.key for c in g.build(int(sys.argv[1]))}))', String(seed)]));

// The templates (ruled, under "coded only") and the comparison (stated findings, reported), declared in D39.
const TEMPLATE_NAMES = ['sequence', 'tree without its gene step', 'graph without its gene step', 'event frequency', 'blank'];
const STATED = name => name + ', stated findings';
const DEFINITIONS = `
  var pageWindow = defaultDef().window;
  var shapes = {
    'sequence': () => ({nodes: clone(TEMPLATES.sequence.nodes), edges: clone(TEMPLATES.sequence.edges)}),
    'tree without its gene step': () => withoutNodes({nodes: clone(TEMPLATES.tree.nodes), edges: clone(TEMPLATES.tree.edges)}, new Set(['B'])),
    'graph without its gene step': () => withoutNodes({nodes: clone(TEMPLATES.graph.nodes), edges: clone(TEMPLATES.graph.edges)}, new Set(['E'])),
    'event frequency': () => ({nodes: clone(TEMPLATES.frequency.nodes), edges: clone(TEMPLATES.frequency.edges)}),
    'blank': () => ({nodes: clone(BLANK.nodes), edges: clone(BLANK.edges)}),
  };
  var CASES = {};
  for (const [name, shape] of Object.entries(shapes)) {
    CASES[name] = {scope: 'from_start', window: pageWindow, freeText: 'off', ...shape()};
    CASES[name + ', stated findings'] = {scope: 'from_start', window: pageWindow, freeText: 'present', ...shape()};
  }
  // Rule 3's coverage: the whole Tree and Graph templates under "coded only" are refused for their gene step only.
  var WHOLE = {'tree': {scope: 'from_start', window: pageWindow, ...clone(TEMPLATES.tree)},
               'graph': {scope: 'from_start', window: pageWindow, ...clone(TEMPLATES.graph)}};
`;

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
  ` + DEFINITIONS, context);
  // Each case with its own free-text setting, which also places the page's index windows.
  return vm.runInContext(`(() => {
    const pids = [...state.patients.keys()];
    const one = (def, freeText) => {
      const before = state.freeText;
      state.freeText = freeText;
      try { return storeRequest({...defaultDef(), ...def}, freeText, pids, {rises: true, codedOnly: 'store'}); }
      catch (e) { if (e instanceof StoreRefusal) return {refused: e.code}; throw e; }
      finally { state.freeText = before; }
    };
    const cases = Object.entries(CASES).map(([name, def]) => ({name, ...one(def, def.freeText)}));
    const whole = Object.fromEntries(Object.entries(WHOLE).map(([name, def]) => [name, one(def, 'off').refused || null]));
    return {cases, whole};
  })()`, context);
}

function tally(results, c) {
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
  return {candidates: candidates.length, unresolved, statuses, reasons, profile: (c.conditions || {}).profile || null,
          candidate_ids: candidates, unresolved_ids: candidates.filter(pid => results[c.name][pid].status === 'unresolved')};
}

const strip = t => Object.fromEntries(Object.entries(t).filter(([k]) => !k.endsWith('_ids')));
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(path.join(root, file))).digest('hex');
const ARTIFACTS = ['demos/mockups/coded_usefulness.cjs', 'demos/mockups/store_parity.py', 'demos/mockups/trajectory/store_request.js',
  'demos/mockups/trajectory/app.js', 'demos/mockups/shared/core.js', 'demos/mockups/synthetic/generate.py',
  'patterns/store_conditions_coded.py', 'patterns/store_conditions_relative.py', 'patterns/execution_store.py',
  'patterns/synthetic_ingest.py', 'patterns/trajectory_graph.py', 'patterns/trajectory_times.py', 'patterns/temporal_stn.py'];

const work = fs.mkdtempSync(path.join(os.tmpdir(), 'coded-usefulness-'));
const perSeed = {};
let names = null, wholeRefusals = null;
try {
  const used = [];
  const measure = seed => {
    const cohortPath = path.join(work, `cohort-${seed}.json`);
    run(['demos/mockups/synthetic/generate.py', '--seed', String(seed), '--output', cohortPath]);
    const cohortText = fs.readFileSync(cohortPath, 'utf8');
    const {cases, whole} = translate(cohortText);
    names = names || cases.map(c => c.name);
    wholeRefusals = wholeRefusals || whole;
    const answered = cases.filter(c => !c.refused);
    const snapshot = run(['demos/mockups/store_parity.py', 'build', cohortPath, path.join(work, `store-${seed}`)]).trim();
    const results = JSON.parse(run(['demos/mockups/store_parity.py', 'match', snapshot], JSON.stringify({cohort: cohortPath, cases: answered})));
    fs.rmSync(path.join(work, `store-${seed}`), {recursive: true, force: true});
    perSeed[seed] = {children: JSON.parse(cohortText).patients.length, snapshot_id: path.basename(snapshot), keys: planKeys(seed),
                     cohort_sha256: crypto.createHash('sha256').update(cohortText).digest('hex'),
                     templates: Object.fromEntries(cases.map(c => [c.name, c.refused ? {refused: c.refused} : tally(results, c)]))};
    used.push(seed);
    const line = TEMPLATE_NAMES.map(n => `${n} ${perSeed[seed].templates[n].unresolved}/${perSeed[seed].templates[n].candidates}`).join('; ');
    console.error(`seed ${seed}: ${line}`);
  };
  for (const seed of SEEDS) measure(seed);
  const pooledCandidates = n => used.reduce((k, s) => k + (perSeed[s].templates[n].candidates || 0), 0);
  for (const seed of EXTEND) { if (!TEMPLATE_NAMES.some(n => pooledCandidates(n) < MINIMUM)) break; measure(seed); }
  const pooled = {};
  for (const name of names) {
    const refused = perSeed[used[0]].templates[name].refused;
    if (refused) { pooled[name] = {refused}; continue; }
    const p = pooled[name] = {candidates: 0, unresolved: 0, statuses: {}, reasons: {}};
    for (const s of used) {
      const t = perSeed[s].templates[name];
      p.candidates += t.candidates; p.unresolved += t.unresolved;
      for (const [k, v] of Object.entries(t.statuses)) p.statuses[k] = (p.statuses[k] || 0) + v;
      for (const [k, v] of Object.entries(t.reasons)) p.reasons[k] = (p.reasons[k] || 0) + v;
    }
    p.unresolved_share = p.candidates ? p.unresolved / p.candidates : null;
  }
  // Per PLAN key, and the shares three ways.
  const byKey = {}, readings = {};
  for (const n of names) {
    if (pooled[n].refused) continue;
    const k = byKey[n] = {};
    for (const s of used) {
      const t = perSeed[s].templates[n], keys = perSeed[s].keys;
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
    const d = readings[n].distinct_child_types;
    return [n, {types: d.types, types_unresolved: d.types_unresolved, share_of_types: d.types ? d.types_unresolved / d.types : null,
                candidates: pooled[n].candidates, unresolved: pooled[n].unresolved, enough_candidates: pooled[n].candidates >= MINIMUM,
                met: pooled[n].candidates >= MINIMUM && d.types > 0 && d.types_unresolved <= SHARE * d.types}];
  }));
  const coverage = {answered_under_coded_only: TEMPLATE_NAMES.filter(n => !pooled[n].refused),
                    whole_templates_refused: wholeRefusals,
                    met: TEMPLATE_NAMES.every(n => !pooled[n].refused)
                      && Object.values(wholeRefusals).every(code => code === 'GENE_CLASSIFICATION_UNSUPPORTED')};
  const report = {
    profile: 'd39-coded-usefulness-1.0',
    rule: ('per template under "coded only" (sequence; tree and graph without their gene step; event frequency; blank), '
           + 'pooled over the declared seeds: at least 30 patients with a store candidate and an index window, and at most '
           + '30% of the distinct child types (PLAN keys) with a candidate unresolved; the whole tree and graph templates '
           + 'refused for their gene step only; the share over all candidates and without C001, and stated findings, reported'),
    declared_seeds: SEEDS, extension_seeds: EXTEND, seeds_used: used,
    setting: {window: 'from the second seizure, 24 months (the page\'s own)', scope: 'from_start',
              free_text: 'off (coded records only); the comparison: present (stated findings)',
              translator: 'trajectory/store_request.js with codedOnly "store" and rises asked for',
              store: 'patterns/store_conditions_coded.py (profile execution-store-conditions-1.3, exclude_kinds ["mention"])',
              converter: 'patterns/synthetic_ingest.py emitting series, diary summaries labelled as period readings'},
    bar, coverage,
    templates: Object.fromEntries(TEMPLATE_NAMES.map(n => [n, pooled[n]])),
    stated_findings: Object.fromEntries(TEMPLATE_NAMES.map(n => [n, pooled[STATED(n)]])),
    independence: ('not independent children (D36 review, M1): C001 is the scripted reference child in every seed, and '
                   + 'every other child takes archetype, sex, birth year and overrides from the generator\'s fixed PLAN; '
                   + 'only the draws vary with the seed'),
    shares_three_ways: readings,
    by_plan_key: byKey,
    per_seed: Object.fromEntries(used.map(s => [s, {children: perSeed[s].children, cohort_sha256: perSeed[s].cohort_sha256,
      snapshot_id: perSeed[s].snapshot_id,
      templates: Object.fromEntries(names.map(n => [n, strip(perSeed[s].templates[n])]))}])),
    artifacts: Object.fromEntries(ARTIFACTS.map(f => [f, sha(f)])),
  };
  const text = JSON.stringify(report, null, 1) + '\n';
  if (OUTPUT) fs.writeFileSync(OUTPUT, text); else process.stdout.write(text);
  console.error('bar: ' + TEMPLATE_NAMES.map(n => `${n} ${bar[n].types_unresolved}/${bar[n].types} types, ${bar[n].unresolved}/${bar[n].candidates} candidates (${bar[n].met ? 'met' : 'missed'})`).join('; ')
                + `; coverage ${coverage.met ? 'met' : 'missed'}`);
} finally {
  fs.rmSync(work, {recursive: true, force: true});
}
