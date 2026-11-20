/* D45: is "coded only" ready to be the trajectory page's default? The page side of D45's rules (decision log, D45).

   The flip D45 assesses is two constants of trajectory/store_request.js: STORE_CODED_ONLY (D39) and
   STORE_GENE_CODED_ONLY (D42), both false. D45 does not change them. This tool loads the translator as it would be
   with them turned on, by substituting each constant's one declaration in the file's text before evaluating it
   (`translatorText`; the substitution fails unless each declaration occurs exactly once, as `= false`), beside the
   page's own code (shared/core.js, trajectory/app.js) in a node:vm context, as the parity and usefulness tests load
   the page. Three translators:
   - main: the file as it is (the page today);
   - coded: STORE_CODED_ONLY on (profile 1.3 for "coded only"; a gene step under it keeps its refusal);
   - joint: both on (a gene step under "coded only" as the joint profile).
   The page passes no options (`storeRequest(def, state.freeText, pids)`), so neither does this tool for a flipped
   translator.

     node demos/mockups/coded_default.cjs page [--output REPORT.json]
       Rule 3.1 (the flip changes only "coded only", into the request the tools measure) and rule 3.2 (the option sweep
       under each flipped translator, run in the store); reported: the refusal codes the flipped page can show without
       a message, and the translator's own time per template (rule 4.4).
     node demos/mockups/coded_default.cjs usefulness [--output REPORT.json]
       Rule 2: every page template under "coded only" with each flipped translator, over fresh cohorts.
     node demos/mockups/coded_default.cjs requests --output FILE
       The page's requests for rule 4 (tools/benchmark_coded_default.py): each template's coded-only request (the
       candidate) and the same request without the exclusion (the reference), the page's two controls, and the shapes
       timed at 300,000 patients. Not a report: synthetic patient ids and windows, written outside the repository.

   Synthetic data only; the reports hold counts. Needs DuckDB for `page` and `usefulness` (store_parity.py). */
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
const run = (args, input) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'coded-default-stdin-'));
  const file = path.join(dir, 'stdin');
  fs.writeFileSync(file, input === undefined ? '' : input);
  const fd = fs.openSync(file, 'r');
  try {
    return execFileSync(python, args, {cwd: root, stdio: [fd, 'pipe', 'inherit'], encoding: 'utf8', maxBuffer: 1 << 30,
                                       timeout: RUN_TIMEOUT_MS});
  } finally {
    fs.closeSync(fd);
    fs.rmSync(dir, {recursive: true, force: true});
  }
};
const arg = (name, fallback) => { const i = process.argv.indexOf(name); return i < 0 ? fallback : process.argv[i + 1]; };
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(path.join(root, file))).digest('hex');
const shaText = text => crypto.createHash('sha256').update(text).digest('hex');
// A smoke run to check the tool runs (CODED_DEFAULT_SMOKE=1): the first 10 sweep definitions, one undeclared seed (99);
// never written into verification/.
const SMOKE = process.env.CODED_DEFAULT_SMOKE === '1';

// The flip (decision log, D45): each constant's one declaration, false today, turned on.
const MODES = {main: {}, coded: {STORE_CODED_ONLY: true}, joint: {STORE_CODED_ONLY: true, STORE_GENE_CODED_ONLY: true}};
const TRANSLATOR = path.join(here, 'trajectory/store_request.js');
function translatorText(mode, text = fs.readFileSync(TRANSLATOR, 'utf8')) {
  for (const name of ['STORE_CODED_ONLY', 'STORE_GENE_CODED_ONLY']) {
    const declared = `const ${name} = false;`;
    const n = text.split(declared).length - 1;
    if (n !== 1) throw new Error(`store_request.js must declare "${declared}" exactly once (found ${n}); D45 keeps the default false`);
  }
  for (const [name, value] of Object.entries(MODES[mode])) text = text.replace(`const ${name} = false;`, `const ${name} = ${value};`);
  return text;
}

// The page templates (trajectory/app.js), each with the page's own window and scope; D39's two shapes without the gene
// step (reported, for continuity with D39); the shapes timed at 300,000 patients, whose kept build holds no series and
// no gene label (rule 4.2): Event frequency and Blank as the page writes them, Sequence, Tree and Graph without their
// rise and gene steps.
const DEFINITIONS = `
  var pageWindow = defaultDef().window;
  var shape = (t, gone) => {
    const def = {scope: 'from_start', window: clone(pageWindow), nodes: clone(t.nodes), edges: clone(t.edges)};
    return gone ? withoutNodes(def, new Set(gone)) : def;
  };
  var TEMPLATE_DEFS = {
    'sequence': shape(TEMPLATES.sequence), 'tree': shape(TEMPLATES.tree), 'graph': shape(TEMPLATES.graph),
    'event frequency': shape(TEMPLATES.frequency), 'blank': shape(BLANK),
  };
  var CONTINUITY_DEFS = {'tree without its gene step': shape(TEMPLATES.tree, ['B']),
                         'graph without its gene step': shape(TEMPLATES.graph, ['E'])};
  var SCALE_DEFS = {
    'event frequency': shape(TEMPLATES.frequency), 'blank': shape(BLANK),
    'sequence without its rise': shape(TEMPLATES.sequence, ['C']),
    'tree without its gene and rise steps': shape(TEMPLATES.tree, ['B', 'D']),
    'graph without its gene and rise steps': shape(TEMPLATES.graph, ['C', 'E']),
  };
  // The page-size controls (rule 4.1): two parity trajectories under stated findings that leave nothing out.
  var CONTROL_DEFS = {
    'febrile seizure, then a sodium-channel blocker': {scope: 'from_start', window: clone(pageWindow),
      nodes: [{id: 'A', concept: 'febrile_seizure', occ: 'first'}, {id: 'B', concept: 'sodium_channel_blocker', occ: 'first'}],
      edges: [{from: 'A', to: 'B', rel: 'after', min: 0, max: 12, unit: 'month'}]},
    'a blocker, then at least 8 seizures a month within 60 days': {scope: 'from_start', window: clone(pageWindow),
      nodes: [{id: 'A', concept: 'sodium_channel_blocker', occ: 'first'}, {id: 'B', concept: 'seizure_frequency', cond: 'above', param: 8}],
      edges: [{from: 'A', to: 'B', rel: 'after', min: 0, max: 60, unit: 'day'}]},
  };
  var translateOne = (def, freeText, options) => {
    const before = state.freeText;
    state.freeText = freeText;
    try { return storeRequest({...defaultDef(), ...def}, freeText, [...state.patients.keys()], options); }
    catch (e) { if (e instanceof StoreRefusal) return {refused: e.code}; throw e; }
    finally { state.freeText = before; }
  };
`;

function pageContext(cohortText, mode) {
  const stub = {addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; }};
  const context = vm.createContext({document: stub, addEventListener() {}, console, structuredClone, performance, DATA: cohortText,
                                    POST: () => { throw new Error('D45 never calls the page engine'); }});
  const page = fs.readFileSync(path.join(here, 'trajectory/app.js'), 'utf8').replace(/\nmain\(\);\s*$/, '\n');
  vm.runInContext(fs.readFileSync(path.join(here, 'shared/core.js'), 'utf8') + '\n' + page + '\n' + translatorText(mode) + `
    state.data = JSON.parse(DATA); state.concepts = state.data.concepts;
    for (const [cid, c] of Object.entries(state.concepts)) for (const coding of c.codings) state.codeIndex.set(coding.system + '|' + coding.code, cid);
    for (const p of state.data.patients) state.patients.set(p.patient_id, p);
    state.freeText = 'present';
  ` + DEFINITIONS, context);
  vm.runInContext(fs.readFileSync(path.join(here, 'option_sweep.js'), 'utf8'), context);
  return context;
}
const inContext = (context, code) => JSON.parse(vm.runInContext(`JSON.stringify(${code})`, context));
const translate = (context, def, freeText, options = {}) =>
  inContext(context, `translateOne(${JSON.stringify(def)}, ${JSON.stringify(freeText)}, ${JSON.stringify(options)})`);
const outcome = r => r.refused ? `refused ${r.refused}` : `profile ${(r.conditions || {}).profile || 'none'}`;
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// The request without the exclusion (rule 4's reference): the candidate with `exclude_kinds` removed and the profile
// that requires it replaced by the one the store uses without it (1.3: 1.2 with a rise, 1.1 with another step
// condition, no conditions without one; the joint profile: the gene profile). Query and windows unchanged.
function withoutExclusion(candidate) {
  const c = candidate.conditions;
  assert.ok(c && Array.isArray(c.exclude_kinds), 'a coded-only request carries exclude_kinds');
  const {exclude_kinds: _, ...rest} = c;
  let conditions;
  if (c.profile === 'execution-store-conditions-gene-coded-1.0') conditions = {...rest, profile: 'execution-store-conditions-gene-1.0'};
  else {
    assert.equal(c.profile, 'execution-store-conditions-1.3');
    const steps = Object.values(c.steps);
    conditions = !steps.length ? null : {...rest, profile: steps.some(s => s.relative) ? 'execution-store-conditions-1.2' : 'execution-store-conditions-1.1'};
  }
  return {query: candidate.query, conditions, windows: candidate.windows};
}

const COHORT_ARTIFACTS = ['demos/mockups/coded_default.cjs', 'demos/mockups/option_sweep.js', 'demos/mockups/store_parity.py',
  'demos/mockups/trajectory/store_request.js', 'demos/mockups/trajectory/app.js', 'demos/mockups/shared/core.js',
  'demos/mockups/synthetic/generate.py', 'patterns/store_conditions.py', 'patterns/store_conditions_coded.py',
  'patterns/store_conditions_gene.py', 'patterns/store_conditions_relative.py', 'patterns/store_conditions_counts.py',
  'patterns/execution_store.py', 'patterns/synthetic_ingest.py', 'patterns/trajectory_graph.py',
  'patterns/trajectory_times.py', 'patterns/temporal_stn.py'];
const artifacts = () => Object.fromEntries(COHORT_ARTIFACTS.map(f => [f, sha(f)]));
const write = (output, report) => {
  const text = JSON.stringify(report, null, 1) + '\n';
  if (output) fs.writeFileSync(output, text); else process.stdout.write(text);
};

/* Rule 3.1: the flip changes only "coded only", into the request the tools measure. For every page template, D39's two
   shapes and every option-sweep definition, under each free-text setting: the flipped translator's request with no
   options equals the page translator's with the option the tools and the parity test pass (`codedOnly: 'store'`, with
   `geneCodedOnly` false for the coded translator and true for the joint one), request for request or refusal for
   refusal; and with stated or hedged findings it equals the page translator's with no options (nothing else moves). */
function equivalence(contexts) {
  const defs = {...inContext(contexts.main, 'TEMPLATE_DEFS'), ...inContext(contexts.main, 'CONTINUITY_DEFS')};
  for (const {name, def} of inContext(contexts.main, 'optionSweepDefinitions()').slice(0, SMOKE ? 10 : undefined)) defs['sweep: ' + name] = def;
  const out = {definitions: Object.keys(defs).length, free_text: ['off', 'present', 'possible'], modes: {}};
  for (const mode of ['coded', 'joint']) {
    const row = out.modes[mode] = {compared: 0, equal_to_explicit_option: 0, unchanged_outside_coded_only: 0,
                                   changed_under_coded_only: 0, transitions: {}, failures: []};
    const options = {codedOnly: 'store', geneCodedOnly: mode === 'joint'};
    for (const [name, def] of Object.entries(defs)) for (const freeText of out.free_text) {
      const flipped = translate(contexts[mode], def, freeText);
      const explicit = translate(contexts.main, def, freeText, options);
      const today = translate(contexts.main, def, freeText);
      row.compared += 1;
      if (same(flipped, explicit)) row.equal_to_explicit_option += 1;
      else row.failures.push(`${name} / ${freeText}: differs from the explicit option`);
      if (freeText !== 'off') {
        if (same(flipped, today)) row.unchanged_outside_coded_only += 1;
        else row.failures.push(`${name} / ${freeText}: moved outside coded only`);
      } else if (!same(flipped, today)) {
        row.changed_under_coded_only += 1;
        const key = `${outcome(today)} -> ${outcome(flipped)}`;
        row.transitions[key] = (row.transitions[key] || 0) + 1;
      }
    }
    row.failures = row.failures.slice(0, 20);
    row.met = row.compared === row.equal_to_explicit_option
      && row.unchanged_outside_coded_only === Object.keys(defs).length * 2;
  }
  out.met = Object.values(out.modes).every(r => r.met);
  return out;
}

/* Rule 3.2: the option sweep (D37 rule 1; option_sweep.js) under each translator, run in the store on the page's
   cohort: every definition answered, with a result for every patient, or refused by a named code. */
function sweeps(contexts, snapshot, patients) {
  const out = {};
  for (const mode of ['main', 'coded', 'joint']) {
    const cases = inContext(contexts[mode], 'optionSweepCases()').slice(0, SMOKE ? 10 : undefined);
    const answered = cases.filter(c => !c.refused);
    const store = JSON.parse(run([path.join(here, 'store_parity.py'), 'sweep', snapshot], JSON.stringify({cohort: COHORT, cases: answered})));
    const tally = {answered: 0, translator: {}, store: {}}, unnamed = [], incomplete = [];
    for (const c of cases) {
      if (c.refused) {
        if (c.refused.startsWith('UNNAMED')) unnamed.push(`${c.name}: ${c.refused}`);
        tally.translator[c.refused] = (tally.translator[c.refused] || 0) + 1;
        continue;
      }
      const answer = store[c.name];
      if (answer.error === 'UNNAMED') unnamed.push(`${c.name}: ${answer.message}`);
      if (answer.error) { tally.store[answer.error] = (tally.store[answer.error] || 0) + 1; continue; }
      if (Object.values(answer.counts).reduce((a, b) => a + b, 0) !== patients) incomplete.push(c.name);
      tally.answered += 1;
    }
    const profiles = {};
    for (const c of answered) { const p = (c.conditions || {}).profile || 'none'; profiles[p] = (profiles[p] || 0) + 1; }
    out[mode] = {definitions: cases.length, ...tally, profiles_sent: profiles, unnamed: unnamed.slice(0, 20),
                 incomplete: incomplete.slice(0, 20), met: !unnamed.length && !incomplete.length};
  }
  return out;
}

// Reported: the refusal codes each flipped page can show (translator or store, over the templates and the sweep) that
// the page's STORE_REFUSAL has no message for (it then shows "the translator could not answer").
function messages(contexts, sweep) {
  const known = new Set(inContext(contexts.main, 'Object.keys(STORE_REFUSAL)'));
  const out = {};
  for (const mode of ['main', 'coded', 'joint']) {
    const codes = new Set([...Object.keys(sweep[mode].translator), ...Object.keys(sweep[mode].store)]);
    for (const def of Object.values(inContext(contexts[mode], 'TEMPLATE_DEFS'))) {
      const r = translate(contexts[mode], def, 'off');
      if (r.refused) codes.add(r.refused);
    }
    out[mode] = {codes: [...codes].sort(), without_a_message: [...codes].filter(c => !known.has(c)).sort()};
  }
  return out;
}

// Rule 4.4 (reported): the flipped translator's own time per template under "coded only" (the whole `storeRequest`,
// windows included), and the page translator's for the same template with stated findings, which the page runs today.
function translatorTimes(contexts, calls = 200, warm = 20) {
  vm.runInContext(`var timeOne = (def, freeText, n, w) => {
    for (let i = 0; i < w; i++) translateOne(def, freeText, {});
    const t = [];
    for (let i = 0; i < n; i++) { const s = performance.now(); translateOne(def, freeText, {}); t.push(performance.now() - s); }
    t.sort((a, b) => a - b);
    return {median_ms: t[Math.floor(t.length / 2)], max_ms: t[t.length - 1], calls: n};
  };`, contexts.main);
  const out = {};
  for (const mode of ['coded', 'joint']) {
    vm.runInContext('var timeOne = ' + vm.runInContext('timeOne.toString()', contexts.main), contexts[mode]);
    for (const [name, def] of Object.entries(inContext(contexts[mode], 'TEMPLATE_DEFS'))) {
      if (translate(contexts[mode], def, 'off').refused) continue;
      out[`${name} (${mode})`] = {
        coded_only: inContext(contexts[mode], `timeOne(TEMPLATE_DEFS[${JSON.stringify(name)}], 'off', ${calls}, ${warm})`),
        stated_findings_today: inContext(contexts.main, `timeOne(TEMPLATE_DEFS[${JSON.stringify(name)}], 'present', ${calls}, ${warm})`)};
    }
  }
  return out;
}

function page(output) {
  const cohortText = fs.readFileSync(COHORT, 'utf8');
  const contexts = Object.fromEntries(Object.keys(MODES).map(m => [m, pageContext(cohortText, m)]));
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'coded-default-page-'));
  try {
    console.error('rule 3.1: the flip\'s equivalence ...');
    const eq = equivalence(contexts);
    console.error('rule 3.2: the option sweep under each translator ...');
    const snapshot = run([path.join(here, 'store_parity.py'), 'build', COHORT, path.join(work, 'store')]).trim();
    const patients = JSON.parse(cohortText).patients.length;
    const sweep = sweeps(contexts, snapshot, patients);
    const report = {
      profile: 'd45-coded-default-page-1.0',
      rule: ('D45 rule 3: 3.1, under the coded and the joint translator, every page template, D39\'s two shapes and every '
             + 'option-sweep definition under each free-text setting translates as the page translator with the explicit '
             + 'option, and as today with stated or hedged findings; 3.2, the option sweep under each translator answered '
             + 'with a result per patient or refused by a named code. Reported: refusal codes without a page message; the '
             + 'translator\'s own time (rule 4.4)'),
      cohort: {children: patients, sha256: shaText(cohortText)},
      translators: Object.fromEntries(Object.keys(MODES).map(m => [m, {constants_on: Object.keys(MODES[m]), sha256: shaText(translatorText(m))}])),
      rule_3_1: eq, rule_3_2: sweep, messages: messages(contexts, sweep), translator_time: translatorTimes(contexts),
      met: {rule_3_1: eq.met, rule_3_2: Object.values(sweep).every(s => s.met)},
      artifacts: artifacts(), contains_patient_level_data: false, ...(SMOKE ? {smoke: true} : {}),
    };
    write(output, report);
    console.error(`rule 3.1 ${eq.met ? 'met' : 'MISSED'}; rule 3.2 ${report.met.rule_3_2 ? 'met' : 'MISSED'}; `
                  + Object.entries(sweep).map(([m, s]) => `${m}: ${s.answered} answered of ${s.definitions}`).join('; '));
  } finally {
    fs.rmSync(work, {recursive: true, force: true});
  }
}

/* Rule 2: usefulness. Fresh seeds, declared in D45 before any measurement: 4501 to 4530, then 4531, 4532, ... in order
   while some ruled template has fewer than 30 candidates pooled (the stop reads candidate counts only). */
const SEEDS = Array.from({length: 30}, (_, i) => 4501 + i);
const EXTEND = Array.from({length: 70}, (_, i) => 4531 + i);
const MINIMUM = 30, SHARE = 0.3;
// Ruled: the templates each flipped translator answers under "coded only"; and the whole Tree and Graph refused by the
// coded translator for their gene step only.
const RULED = {coded: ['sequence', 'event frequency', 'blank'], joint: ['tree', 'graph']};
const REFUSED_UNDER_CODED = {tree: 'GENE_WITH_CODED_ONLY_UNSUPPORTED', graph: 'GENE_WITH_CODED_ONLY_UNSUPPORTED'};
const planKeys = seed => JSON.parse(run(['-c', 'import json, sys; sys.path.insert(0, "demos/mockups/synthetic"); '
  + 'import generate as g; print(json.dumps({c.pid: c.key for c in g.build(int(sys.argv[1]))}))', String(seed)]));

function seedCases(cohortText) {
  const contexts = Object.fromEntries(Object.keys(MODES).map(m => [m, pageContext(cohortText, m)]));
  const templates = inContext(contexts.main, 'TEMPLATE_DEFS'), continuity = inContext(contexts.main, 'CONTINUITY_DEFS');
  const cases = [], refusals = {coded: {}, joint: {}, 'stated findings': {}};
  for (const mode of ['coded', 'joint'])
    for (const [name, def] of Object.entries(templates)) {
      const r = translate(contexts[mode], def, 'off');
      if (r.refused) refusals[mode][name] = r.refused;
      else cases.push({name: `${name}, coded only (${mode})`, ...r});
    }
  for (const [name, def] of Object.entries(continuity)) {
    const r = translate(contexts.coded, def, 'off');
    assert.ok(!r.refused, `${name}: refused (${r.refused})`);
    cases.push({name: `${name}, coded only (coded)`, ...r});
  }
  for (const [name, def] of Object.entries(templates)) {  // the page's default setting today, reported
    const r = translate(contexts.main, def, 'present');
    if (r.refused) refusals['stated findings'][name] = r.refused;
    else cases.push({name: `${name}, stated findings`, ...r});
  }
  // A template both flipped translators answer alike is matched once (the joint flip changes only gene steps).
  const byName = Object.fromEntries(cases.map(c => [c.name, c]));
  const body = c => { const {name: _, ...rest} = c; return rest; };
  for (const name of RULED.coded) assert.ok(same(body(byName[`${name}, coded only (coded)`]), body(byName[`${name}, coded only (joint)`])),
                                            `${name}: the two flipped translators differ`);
  return {cases: cases.filter(c => !(c.name.endsWith('(joint)') && RULED.coded.some(n => c.name.startsWith(n + ',')))), refusals};
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

const RULED_NAMES = [...RULED.coded.map(n => `${n}, coded only (coded)`), ...RULED.joint.map(n => `${n}, coded only (joint)`)];

function usefulness(output) {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'coded-default-usefulness-'));
  const perSeed = {}, used = [];
  let refusals = null;
  try {
    const measure = seed => {
      const cohortPath = path.join(work, `cohort-${seed}.json`);
      run(['demos/mockups/synthetic/generate.py', '--seed', String(seed), '--output', cohortPath]);
      const cohortText = fs.readFileSync(cohortPath, 'utf8');
      const {cases, refusals: r} = seedCases(cohortText);
      refusals = refusals || r;
      assert.deepEqual(r, refusals, `seed ${seed}: the refusals differ from the first seed's`);
      const snapshot = run(['demos/mockups/store_parity.py', 'build', cohortPath, path.join(work, `store-${seed}`)]).trim();
      const results = JSON.parse(run(['demos/mockups/store_parity.py', 'match', snapshot], JSON.stringify({cohort: cohortPath, cases})));
      fs.rmSync(path.join(work, `store-${seed}`), {recursive: true, force: true});
      perSeed[seed] = {children: JSON.parse(cohortText).patients.length, snapshot_id: path.basename(snapshot), keys: planKeys(seed),
                       cohort_sha256: shaText(cohortText), templates: Object.fromEntries(cases.map(c => [c.name, tally(results, c)]))};
      used.push(seed);
      console.error(`seed ${seed}: ` + RULED_NAMES.map(n => `${n} ${perSeed[seed].templates[n].unresolved}/${perSeed[seed].templates[n].candidates}`).join('; '));
    };
    for (const seed of SMOKE ? [99] : SEEDS) measure(seed);
    const pooledCandidates = n => used.reduce((k, s) => k + perSeed[s].templates[n].candidates, 0);
    for (const seed of SMOKE ? [] : EXTEND) { if (!RULED_NAMES.some(n => pooledCandidates(n) < MINIMUM)) break; measure(seed); }
    const names = Object.keys(perSeed[used[0]].templates);
    const pooled = {}, byKey = {}, readings = {};
    for (const name of names) {
      const p = pooled[name] = {candidates: 0, unresolved: 0, statuses: {}, reasons: {}, profile: perSeed[used[0]].templates[name].profile};
      const k = byKey[name] = {};
      for (const s of used) {
        const t = perSeed[s].templates[name], keys = perSeed[s].keys;
        p.candidates += t.candidates; p.unresolved += t.unresolved;
        for (const [x, v] of Object.entries(t.statuses)) p.statuses[x] = (p.statuses[x] || 0) + v;
        for (const [x, v] of Object.entries(t.reasons)) p.reasons[x] = (p.reasons[x] || 0) + v;
        for (const pid of t.candidate_ids) {
          const e = k[keys[pid]] = k[keys[pid]] || {candidates: 0, unresolved: 0};
          e.candidates += 1;
          if (t.unresolved_ids.includes(pid)) e.unresolved += 1;
        }
      }
      p.unresolved_share = p.candidates ? p.unresolved / p.candidates : null;
      const entries = Object.entries(k), noRef = entries.filter(([key]) => key !== 'reference');
      const sum = (es, f) => es.reduce((a, [, e]) => a + f(e), 0);
      readings[name] = {
        distinct_child_types: {types: entries.length, types_unresolved: entries.filter(([, e]) => e.unresolved > 0).length},
        all_candidates: {candidates: sum(entries, e => e.candidates), unresolved: sum(entries, e => e.unresolved)},
        without_C001: {candidates: sum(noRef, e => e.candidates), unresolved: sum(noRef, e => e.unresolved)},
        C001: k.reference || {candidates: 0, unresolved: 0},
      };
    }
    const bar = Object.fromEntries(RULED_NAMES.map(n => {
      const d = readings[n].distinct_child_types;
      return [n, {types: d.types, types_unresolved: d.types_unresolved, share_of_types: d.types ? d.types_unresolved / d.types : null,
                  candidates: pooled[n].candidates, unresolved: pooled[n].unresolved, enough_candidates: pooled[n].candidates >= MINIMUM,
                  met: pooled[n].candidates >= MINIMUM && d.types > 0 && d.types_unresolved <= SHARE * d.types}];
    }));
    const coverage = {refused_by_the_coded_translator: refusals.coded, refused_by_the_joint_translator: refusals.joint,
                      stated_findings_refusals: refusals['stated findings'],
                      met_coded: same(refusals.coded, REFUSED_UNDER_CODED), met_joint: same(refusals.joint, {})};
    const ok = names => names.every(n => bar[n].met);
    const report = {
      profile: 'd45-coded-default-usefulness-1.0',
      rule: ('D45 rule 2: per template under "coded only" through each flipped translator, pooled over the declared seeds: '
             + 'at least 30 patients with a store candidate and an index window, and at most 30% of the distinct child types '
             + '(PLAN keys; C001 is reference) with a candidate unresolved (a type is unresolved when any of its candidates '
             + 'is). Coded translator: sequence, event frequency and blank, the whole tree and graph refused for their gene '
             + 'step only; joint translator: tree and graph. All candidates and the count without C001, D39\'s two shapes '
             + 'without the gene step and the templates under stated findings reported'),
      declared_seeds: SEEDS, extension_seeds: EXTEND, seeds_used: used,
      setting: {window: 'from the first seizure, 24 months (the page\'s own)', scope: 'from_start',
                free_text: 'off (coded records only); reported beside it: present (stated findings, the page\'s default)',
                translator: 'trajectory/store_request.js with STORE_CODED_ONLY (coded) or both it and STORE_GENE_CODED_ONLY (joint) on, no options',
                store: 'store_parity.py build (the converter emitting series, through the gene wrapper) and match (store_conditions.match, the count post-pass on)'},
      bar, coverage,
      met: {coded: ok(RULED_NAMES.filter(n => n.endsWith('(coded)'))) && coverage.met_coded,
            joint: ok(RULED_NAMES) && coverage.met_joint},
      templates: pooled, shares_three_ways: readings, by_plan_key: byKey,
      independence: ('not independent children (D36 review, M1): C001 is the scripted reference child in every seed, and '
                     + 'every other child takes archetype, sex, birth year and overrides from the generator\'s fixed PLAN; '
                     + 'only the draws vary with the seed'),
      per_seed: Object.fromEntries(used.map(s => [s, {children: perSeed[s].children, cohort_sha256: perSeed[s].cohort_sha256,
        snapshot_id: perSeed[s].snapshot_id, templates: Object.fromEntries(names.map(n => [n, Object.fromEntries(
          Object.entries(perSeed[s].templates[n]).filter(([k]) => !k.endsWith('_ids')))]))}])),
      artifacts: artifacts(), contains_patient_level_data: false, ...(SMOKE ? {smoke: true} : {}),
    };
    write(output, report);
    console.error('bar: ' + RULED_NAMES.map(n => `${n} ${bar[n].types_unresolved}/${bar[n].types} types, ${bar[n].unresolved}/${bar[n].candidates} candidates (${bar[n].met ? 'met' : 'missed'})`).join('; ')
                  + `; coverage coded ${coverage.met_coded ? 'met' : 'missed'}, joint ${coverage.met_joint ? 'met' : 'missed'}`);
  } finally {
    fs.rmSync(work, {recursive: true, force: true});
  }
}

// The page's requests for rule 4, on the page's cohort.
function requests(output) {
  assert.ok(output, 'requests needs --output');
  const cohortText = fs.readFileSync(COHORT, 'utf8');
  const contexts = Object.fromEntries(Object.keys(MODES).map(m => [m, pageContext(cohortText, m)]));
  const templates = inContext(contexts.main, 'TEMPLATE_DEFS');
  const pageRequests = {};
  for (const [name, def] of Object.entries(templates)) {
    const mode = RULED.joint.includes(name) ? 'joint' : 'coded';
    const candidate = translate(contexts[mode], def, 'off');
    assert.ok(!candidate.refused, `${name}: refused (${candidate.refused})`);
    const reference = withoutExclusion(candidate);
    // The reference is the store's answer to the same template reading letter mentions: what the page translator writes
    // with D37's derived reading, or with stated findings for a gene template (whose windows the page places by the
    // free-text setting, so only its query and conditions are compared).
    const today = mode === 'joint' ? translate(contexts.main, def, 'present') : translate(contexts.main, def, 'off', {codedOnly: 'derived'});
    assert.ok(same(today.query, reference.query) && same(today.conditions, reference.conditions),
              `${name}: the reference is not the page translator's request without the exclusion`);
    if (mode !== 'joint') assert.ok(same(today.windows, reference.windows), `${name}: windows differ`);
    pageRequests[name] = {mode, candidate, reference};
  }
  const controls = Object.fromEntries(Object.entries(inContext(contexts.main, 'CONTROL_DEFS'))
    .map(([name, def]) => [name, translate(contexts.main, def, 'present')]));
  for (const [name, c] of Object.entries(controls)) assert.ok(!c.refused && !(c.conditions || {}).exclude_kinds, name);
  const scale = {};
  for (const [name, def] of Object.entries(inContext(contexts.main, 'SCALE_DEFS'))) {
    const candidate = translate(contexts.coded, def, 'off');
    assert.ok(!candidate.refused, `${name}: refused (${candidate.refused})`);
    const reference = withoutExclusion(candidate);
    scale[name] = {candidate: {query: candidate.query, conditions: candidate.conditions},
                   reference: {query: reference.query, conditions: reference.conditions}};
  }
  fs.writeFileSync(output, JSON.stringify({profile: 'd45-page-requests-1.0', cohort_sha256: shaText(cohortText),
    translators: Object.fromEntries(Object.keys(MODES).map(m => [m, shaText(translatorText(m))])),
    artifacts: artifacts(), page: pageRequests, page_controls: controls, scale}) + '\n');
  console.error(`wrote ${output}: ${Object.keys(pageRequests).length} page templates, ${Object.keys(controls).length} controls, ${Object.keys(scale).length} shapes at scale`);
}

module.exports = {translatorText, withoutExclusion, MODES, SEEDS, EXTEND, RULED};

if (require.main === module) {
  const what = process.argv[2], output = arg('--output', null);
  if (SMOKE && output && path.resolve(output).startsWith(path.join(root, 'verification'))) {
    console.error('a smoke run is never written into verification/'); process.exit(2);
  }
  if (what === 'page') page(output);
  else if (what === 'usefulness') usefulness(output);
  else if (what === 'requests') requests(output);
  else { console.error('usage: node demos/mockups/coded_default.cjs page|usefulness|requests [--output FILE]'); process.exit(2); }
}
