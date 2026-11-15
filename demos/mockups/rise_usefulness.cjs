/* D36: the usefulness test for rises and falls (the store's relative conditions), over many synthetic cohorts.

   For each declared generator seed, a distinct cohort of 48 children (demos/mockups/synthetic/generate.py --seed) is
   built into an execution store by the converter emitting series, with the diary's summaries labelled as period
   readings (store_parity.py build). The trajectory page's three rise templates (Sequence; Tree and Graph without their
   gene step, which the store refuses by name) and the declared variants are translated by the page's own translator
   (trajectory/store_request.js, rises asked for) with the page's own window, scope and free-text setting, exactly as
   the parity test translates them, and matched in the store (store_parity.py match), once with D35's target split on
   and once with it off. For each template it counts the patients with a store candidate and an index window, and the
   unresolved results among them (with their reasons), per seed and pooled.

     node demos/mockups/rise_usefulness.cjs [--output REPORT.json]

   The protocol is fixed here, as D36 declared it before measuring: seeds 1001 to 1030, then 1031, 1032, ... in order
   while some template has fewer than 30 candidates (counted before any status is read). No other seed is accepted.
   The bar (decision log, D36) is judged on the templates, pooled over those seeds.

   The cohorts are not independent children (the D36 review, M1): every seed scripts the reference child C001 with a
   fixed timeline, and every other child takes its archetype, sex, birth year and overrides from the generator's fixed
   PLAN; only the draws vary with the seed. So the report also counts candidates and unresolved results per PLAN key
   (C001 is the key `reference`), and gives the shares three ways: over all candidates, with C001 left out, and over
   distinct PLAN keys (a key unresolved if any of its candidates is). Synthetic data only; the report holds counts.
   Needs DuckDB (patterns/requirements-store.lock.txt). */
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
const MINIMUM = 30;
const SEEDS = Array.from({length: 30}, (_, i) => 1001 + i);  // declared in D36 before any measurement
const EXTEND = Array.from({length: 70}, (_, i) => 1031 + i);  // the extension rule's order (never needed past 1100)
if (process.argv.length > 2 && !(process.argv.length === 4 && process.argv[2] === '--output')) {
  console.error('usage: node demos/mockups/rise_usefulness.cjs [--output FILE]  (the seeds are fixed by D36)'); process.exit(2);
}
// Each child's PLAN key (the generator's own mapping of patient ids to plan slots, for this seed).
const planKeys = seed => JSON.parse(run(['-c', 'import json, sys; sys.path.insert(0, "demos/mockups/synthetic"); '
  + 'import generate as g; print(json.dumps({c.pid: c.key for c in g.build(int(sys.argv[1]))}))', String(seed)]));

// The templates (ruled) and the variants (reported), declared in the decision log before any measurement.
const TEMPLATE_NAMES = ['sequence', 'tree without its gene step', 'graph without its gene step'];
const DEFINITIONS = `
  var pageWindow = {start: {kind: 'event', event: 'seizure', which: 'first', n: 2, offset: 0, unit: 'month'}, end: {kind: 'span', amount: 24, unit: 'month'}};
  var firstWindow = {start: {kind: 'event', event: 'seizure', which: 'first', n: 1, offset: 0, unit: 'month'}, end: {kind: 'span', amount: 36, unit: 'month'}};
  var shapes = {
    'sequence': () => ({nodes: clone(TEMPLATES.sequence.nodes), edges: clone(TEMPLATES.sequence.edges)}),
    'tree without its gene step': () => withoutNodes({nodes: clone(TEMPLATES.tree.nodes), edges: clone(TEMPLATES.tree.edges)}, new Set(['B'])),
    'graph without its gene step': () => withoutNodes({nodes: clone(TEMPLATES.graph.nodes), edges: clone(TEMPLATES.graph.edges)}, new Set(['E'])),
  };
  var withFactor = (shape, factor) => ({...shape, nodes: shape.nodes.map(n => n.cond === 'rise' ? {...n, param: factor} : n)});
  var CASES = {};
  for (const [name, shape] of Object.entries(shapes)) {
    CASES[name] = {scope: 'from_start', window: pageWindow, ...shape()};
    CASES[name + ', factor 1.5'] = {scope: 'from_start', window: pageWindow, ...withFactor(shape(), 1.5)};
    CASES[name + ', from the first seizure for 36 months'] = {scope: 'from_start', window: firstWindow, ...shape()};
  }
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
    state.freeText = 'present';  // the page's own setting: coded records and findings that letters state
  ` + DEFINITIONS, context);
  return vm.runInContext(`Object.entries(CASES).map(([name, def]) => {
    const {query, conditions, windows} = storeRequest({...defaultDef(), ...def}, state.freeText, [...state.patients.keys()], {rises: true});
    return {name, query, conditions, windows};
  })`, context);
}

function tally(results, cases) {
  const out = {};
  for (const c of cases) {
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
const ARTIFACTS = ['demos/mockups/rise_usefulness.cjs', 'demos/mockups/store_parity.py', 'demos/mockups/trajectory/store_request.js',
  'demos/mockups/trajectory/app.js', 'demos/mockups/shared/core.js', 'demos/mockups/synthetic/generate.py',
  'patterns/store_conditions_relative.py', 'patterns/execution_store.py', 'patterns/synthetic_ingest.py',
  'patterns/trajectory_graph.py', 'patterns/trajectory_times.py', 'patterns/temporal_stn.py'];

const work = fs.mkdtempSync(path.join(os.tmpdir(), 'rise-usefulness-'));
const perSeed = {};
let names = null;
const pooledOf = seeds => {
  const pooled = {};
  for (const name of names) {
    const p = pooled[name] = {candidates: 0, unresolved: 0, statuses: {}, reasons: {}, unresolved_split_off: 0, reasons_split_off: {}};
    for (const s of seeds) {
      const on = perSeed[s].split_on[name], off = perSeed[s].split_off[name];
      p.candidates += on.candidates; p.unresolved += on.unresolved; p.unresolved_split_off += off.unresolved;
      for (const [k, v] of Object.entries(on.statuses)) p.statuses[k] = (p.statuses[k] || 0) + v;
      for (const [k, v] of Object.entries(on.reasons)) p.reasons[k] = (p.reasons[k] || 0) + v;
      for (const [k, v] of Object.entries(off.reasons)) p.reasons_split_off[k] = (p.reasons_split_off[k] || 0) + v;
    }
    p.unresolved_share = p.candidates ? p.unresolved / p.candidates : null;
  }
  return pooled;
};
try {
  const used = [];
  const measure = seed => {
    const cohortPath = path.join(work, `cohort-${seed}.json`);
    run(['demos/mockups/synthetic/generate.py', '--seed', String(seed), '--output', cohortPath]);
    const cohortText = fs.readFileSync(cohortPath, 'utf8');
    const cases = translate(cohortText);
    names = names || cases.map(c => c.name);
    const snapshot = run(['demos/mockups/store_parity.py', 'build', cohortPath, path.join(work, `store-${seed}`)]).trim();
    const on = JSON.parse(run(['demos/mockups/store_parity.py', 'match', snapshot], JSON.stringify({cohort: cohortPath, cases})));
    const off = JSON.parse(run(['demos/mockups/store_parity.py', 'match', snapshot],
                               JSON.stringify({cohort: cohortPath, cases, split: false})));
    perSeed[seed] = {children: JSON.parse(cohortText).patients.length, snapshot_id: path.basename(snapshot), keys: planKeys(seed),
                     cohort_sha256: crypto.createHash('sha256').update(cohortText).digest('hex'),
                     split_on: tally(on, cases), split_off: tally(off, cases)};
    used.push(seed);
    const line = TEMPLATE_NAMES.map(n => `${n} ${perSeed[seed].split_on[n].unresolved}/${perSeed[seed].split_on[n].candidates}`).join('; ');
    console.error(`seed ${seed}: ${line}`);
  };
  for (const seed of SEEDS) measure(seed);
  // The extension rule: further seeds, in order, while some template has fewer than MINIMUM candidates.
  const short = () => TEMPLATE_NAMES.some(n => used.reduce((k, s) => k + perSeed[s].split_on[n].candidates, 0) < MINIMUM);
  for (const seed of EXTEND) { if (!short()) break; measure(seed); }
  const pooled = pooledOf(used);
  const templates = Object.fromEntries(TEMPLATE_NAMES.map(n => [n, pooled[n]]));
  const variants = Object.fromEntries(names.filter(n => !TEMPLATE_NAMES.includes(n)).map(n => [n, pooled[n]]));
  // M1: per PLAN key, and the shares three ways.
  const byKey = {}, readings = {};
  for (const n of names) {
    const k = byKey[n] = {};
    for (const s of used) {
      const on = perSeed[s].split_on[n], keys = perSeed[s].keys;
      for (const pid of on.candidate_ids) {
        const e = k[keys[pid]] = k[keys[pid]] || {candidates: 0, unresolved: 0, cohorts: 0};
        e.candidates += 1; e.cohorts += 1;
        if (on.unresolved_ids.includes(pid)) e.unresolved += 1;
      }
    }
    const entries = Object.entries(k), noRef = entries.filter(([key]) => key !== 'reference');
    const sum = (es, f) => es.reduce((a, [, e]) => a + f(e), 0);
    readings[n] = {
      all_candidates: {candidates: sum(entries, e => e.candidates), unresolved: sum(entries, e => e.unresolved)},
      without_C001: {candidates: sum(noRef, e => e.candidates), unresolved: sum(noRef, e => e.unresolved)},
      distinct_plan_keys: {keys: entries.length, keys_unresolved: entries.filter(([, e]) => e.unresolved > 0).length},
      C001: k.reference || {candidates: 0, unresolved: 0, cohorts: 0},
    };
  }
  const bar = Object.fromEntries(TEMPLATE_NAMES.map(n => [n, {
    candidates: pooled[n].candidates, unresolved: pooled[n].unresolved, share: pooled[n].unresolved_share,
    enough_candidates: pooled[n].candidates >= MINIMUM, met: pooled[n].candidates >= MINIMUM && pooled[n].unresolved <= 0.3 * pooled[n].candidates}]));
  const report = {
    profile: 'd36-rise-usefulness-1.0',
    rule: ('per template (sequence; tree and graph without their gene step), pooled over the declared seeds: at least 30 '
           + 'patients with a store candidate and an index window, and at most 30% of them unresolved; variants reported'),
    declared_seeds: SEEDS, extension_seeds: EXTEND, seeds_used: used,
    setting: {window: 'from the second seizure, 24 months (the page\'s own); variants as named', scope: 'from_start',
              free_text: 'present (the page\'s own)', translator: 'trajectory/store_request.js with rises asked for',
              converter: 'patterns/synthetic_ingest.py emitting series, diary summaries labelled as period readings'},
    bar, templates, variants,
    independence: ('not independent children (D36 review, M1): C001 is the scripted reference child in every seed, and '
                   + 'every other child takes archetype, sex, birth year and overrides from the generator\'s fixed PLAN; '
                   + 'only the draws vary with the seed'),
    shares_three_ways: readings,
    by_plan_key: byKey,
    per_seed: Object.fromEntries(used.map(s => [s, {children: perSeed[s].children, cohort_sha256: perSeed[s].cohort_sha256,
      snapshot_id: perSeed[s].snapshot_id,
      templates: Object.fromEntries(names.map(n => [n, {...strip(perSeed[s].split_on[n]), unresolved_split_off: perSeed[s].split_off[n].unresolved,
                                                        reasons_split_off: perSeed[s].split_off[n].reasons}]))}])),
    split_contribution: Object.fromEntries(names.map(n => [n, {unresolved_split_on: pooled[n].unresolved,
                                                               unresolved_split_off: pooled[n].unresolved_split_off,
                                                               reasons_split_off: pooled[n].reasons_split_off}])),
    artifacts: Object.fromEntries(ARTIFACTS.map(f => [f, sha(f)])),
  };
  const text = JSON.stringify(report, null, 1) + '\n';
  if (OUTPUT) fs.writeFileSync(OUTPUT, text); else process.stdout.write(text);
  console.error('bar: ' + TEMPLATE_NAMES.map(n => `${n} ${bar[n].unresolved}/${bar[n].candidates} (${bar[n].met ? 'met' : 'missed'})`).join('; '));
} finally {
  fs.rmSync(work, {recursive: true, force: true});
}
