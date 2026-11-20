/* D49: "coded only" is the trajectory page's default. The page side of D49's rules (decision log, D49).

   D49 turns on both constants of trajectory/store_request.js, STORE_CODED_ONLY (D39) and STORE_GENE_CODED_ONLY (D42),
   on D45's verdict ("ready" for both) and the owner's decision (2026-11-20). The store, the engine and the converter do
   not change; what changes is the request the page sends under "Coded records only". This tool loads the page's own code
   (shared/core.js, trajectory/app.js, trajectory/store_request.js) into node:vm contexts, as the parity and usefulness
   tools do, for this tree and for main's page (all three read from git at --base, default b575ba8), whose constants
   are false.

     node demos/mockups/d49_flip.cjs census [--output REPORT.json] [--cohort FILE]
       Rules 1 and 2, on the page's cohort: every page template, D39's two shapes and every option-sweep definition,
       under each free-text setting, translated as the page translates them (no options) on main and here; the sweep as
       the page offers it; every request either side sends, answered in the store; the meaning by removal for every
       coded-only request this tree sends.
     node demos/mockups/d49_flip.cjs usefulness [--output REPORT.json]
       Rule 3: the five page templates under "Coded records only" as this tree's page writes them, over fresh cohorts
       (seeds 4911-4940, extended in order to 5010), the owner's counting rule.

   Synthetic data only (generator patient ids); the reports hold counts and the names of sweep definitions. Needs DuckDB
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
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'd49-stdin-'));
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
const BASE = arg('--base', 'b575ba8');
// A smoke run (D49_SMOKE=1): the first 10 sweep definitions, one undeclared seed (4901), never into verification/.
const SMOKE = process.env.D49_SMOKE === '1';

const FILES = {core: 'demos/mockups/shared/core.js', app: 'demos/mockups/trajectory/app.js',
               translator: 'demos/mockups/trajectory/store_request.js', sweep: 'demos/mockups/option_sweep.js'};
const baseText = rel => run(['show', `${BASE}:${rel}`], undefined, {command: 'git'});
const hereText = rel => fs.readFileSync(path.join(root, rel), 'utf8');
const CONSTANTS = ['STORE_CODED_ONLY', 'STORE_GENE_CODED_ONLY'];
// Each constant's one declaration, as main (false) and this tree (true) must have it.
function declared(text, value) {
  for (const name of CONSTANTS) {
    const n = text.split(`const ${name} = ${value};`).length - 1;
    if (n !== 1) throw new Error(`store_request.js must declare "const ${name} = ${value};" exactly once (found ${n})`);
  }
}

// The page templates, each with the page's own window and scope (as coded_default.cjs and d47_page.cjs have them);
// D39's two shapes without the gene step (as D45 reported them).
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
  var translateOne = (def, freeText, options) => {
    const before = state.freeText;
    state.freeText = freeText;
    try { return storeRequest({...defaultDef(), ...def}, freeText, [...state.patients.keys()], options); }
    catch (e) { if (e instanceof StoreRefusal) return {refused: e.code}; return {refused: 'UNNAMED: ' + e.message}; }
    finally { state.freeText = before; }
  };
`;

function pageContext(cohortText, which) {
  const text = Object.fromEntries(Object.entries(FILES).map(([k, f]) => [k, which === 'main' ? baseText(f) : hereText(f)]));
  declared(text.translator, which === 'main' ? 'false' : 'true');
  const stub = {addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; }};
  const context = vm.createContext({document: stub, addEventListener() {}, console, structuredClone, performance, DATA: cohortText,
                                    POST: () => { throw new Error('D49 never calls the page engine'); }});
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
const outcome = r => r.refused ? `refused ${r.refused}` : `profile ${(r.conditions || {}).profile || 'none'}`;
const FREE_TEXT = ['off', 'present', 'possible'];
// The option that, on main's translator, writes what this tree's writes by default (D45 rule 3.1's explicit option).
const FLIPPED = {codedOnly: 'store', geneCodedOnly: true};

/* The request without the exclusion (D45's `withoutExclusion`, rule 4's reference): `exclude_kinds` removed and the
   profile that requires it replaced by the one the store uses without it (1.3: 1.2 with a rise, 1.1 with another step
   condition, no conditions without one; the joint profile: the gene profile). Query and windows unchanged. */
function withoutExclusion(request) {
  const c = request.conditions;
  assert.ok(c && Array.isArray(c.exclude_kinds), 'a coded-only request carries exclude_kinds');
  const {exclude_kinds: _, ...rest} = c;
  let conditions;
  if (c.profile === 'execution-store-conditions-gene-coded-1.0') conditions = {...rest, profile: 'execution-store-conditions-gene-1.0'};
  else {
    assert.equal(c.profile, 'execution-store-conditions-1.3');
    const steps = Object.values(c.steps);
    conditions = !steps.length ? null : {...rest, profile: steps.some(s => s.relative) ? 'execution-store-conditions-1.2' : 'execution-store-conditions-1.1'};
  }
  return {query: request.query, conditions, windows: request.windows};
}
const isCodedOnly = r => !r.refused && !!(r.conditions && r.conditions.exclude_kinds);

/* A changed result of a request whose profile the flip moved (main's D32 reading, 1.1 or 1.2, to 1.3), by the reason
   it can change. `coverage`: D39's coverage rule, a source that records the step only through letter mentions no longer
   covers it, so a result becomes `not_comparable` for a coverage reason. `filtered`: D39's candidate refinement. Anything
   else is named by its transition. Every class is reported; rule 2.2 rules the meaning by removal, not the classes. */
const COVERAGE = ['NO_SOURCE_COVERAGE', 'NO_SOURCE_RECORDS_CONCEPT'];
const statusText = r => r ? (r.certainty ? `${r.status}/${r.certainty}` : r.status) : '-';
function moveClass(before, after) {
  if (after && after.status === 'not_comparable' && COVERAGE.includes(after.reason)
      && !(before && before.status === 'not_comparable' && COVERAGE.includes(before.reason))) return 'coverage';
  // D39's candidate refinement: a patient holding no kept event for some step is no longer a candidate, so its `none`
  // carries the reason FILTERED.
  if (before && after && before.status === 'none' && after.status === 'none' && after.reason === 'FILTERED' && before.reason !== 'FILTERED'
      && same(before.relaxations, after.relaxations)) return 'filtered';
  return `other: ${statusText(before)} -> ${statusText(after)}`;
}

/* The cohort with every letter mention removed: the snapshot built from it is D39's "snapshot without the left-out
   events" for the page's cohort (the converter emits an event only for a mention; the letters, their sources and the
   coverage periods stay). */
function withoutMentions(cohort) {
  const out = JSON.parse(JSON.stringify(cohort));
  let removed = 0;
  for (const p of out.patients) for (const r of p.records) if (Array.isArray(r.mentions) && r.mentions.length) { removed += r.mentions.length; r.mentions = []; }
  return {cohort: out, removed};
}

const ARTIFACTS = ['demos/mockups/d49_flip.cjs', 'demos/mockups/option_sweep.js', 'demos/mockups/store_parity.py',
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
const bump = (o, k, n = 1) => { o[k] = (o[k] || 0) + n; };

/* Rules 1 and 2 (the census). */
function census(output) {
  const cohortPath = path.resolve(arg('--cohort', COHORT));
  const cohortText = fs.readFileSync(cohortPath, 'utf8'), cohort = JSON.parse(cohortText);
  const ctx = {main: pageContext(cohortText, 'main'), here: pageContext(cohortText, 'here')};
  const sweepDefs = inContext(ctx.here, 'optionSweepDefinitions()').slice(0, SMOKE ? 10 : undefined);
  assert.ok(same(sweepDefs, inContext(ctx.main, 'optionSweepDefinitions()').slice(0, SMOKE ? 10 : undefined)), 'the sweep grid differs from main\'s');
  const groups = {template: inContext(ctx.here, 'TEMPLATE_DEFS'), continuity: inContext(ctx.here, 'CONTINUITY_DEFS'),
                  sweep: Object.fromEntries(sweepDefs.map(d => [d.name, d.def]))};
  const t0 = Date.now();

  // Rule 1.1 and 1.2: every definition under each setting, translated with no options on both sides.
  const rows = [];  // {group, name, def, freeText, main, here}
  const r11 = {compared: 0, unchanged_outside_coded_only: 0, equal_to_explicit_option: 0, changed_under_coded_only: 0,
               transitions: {}, by_group: {}, failures: []};
  for (const [group, defs] of Object.entries(groups)) for (const [name, def] of Object.entries(defs)) for (const freeText of FREE_TEXT) {
    const m = translate(ctx.main, def, freeText), h = translate(ctx.here, def, freeText);
    rows.push({group, name, def, freeText, main: m, here: h});
    r11.compared += 1;
    if (freeText !== 'off') {
      if (same(m, h)) r11.unchanged_outside_coded_only += 1; else r11.failures.push(`${name} / ${freeText}: moved outside coded only`);
      continue;
    }
    if (same(h, translate(ctx.main, def, freeText, FLIPPED))) r11.equal_to_explicit_option += 1;
    else r11.failures.push(`${name} / off: differs from main's translator with the explicit option`);
    if (!same(m, h)) {
      r11.changed_under_coded_only += 1;
      const key = `${outcome(m)} -> ${outcome(h)}`;
      bump(r11.transitions, key);
      const g = r11.by_group[group] = r11.by_group[group] || {};
      bump(g, key);
    }
  }
  const definitions = Object.values(groups).reduce((k, d) => k + Object.keys(d).length, 0);
  r11.definitions = definitions;
  r11.met = !r11.failures.length && r11.unchanged_outside_coded_only === 2 * definitions && r11.equal_to_explicit_option === definitions;
  r11.failures = r11.failures.slice(0, 20);
  // The profile moves (main answered, here answers differently): each must be main's request plus the exclusion only.
  const moved = rows.filter(r => r.freeText === 'off' && !r.main.refused && !r.here.refused && !same(r.main, r.here));
  const movedNotExclusionOnly = moved.filter(r => !isCodedOnly(r.here) || !same(withoutExclusion(r.here), r.main)).map(r => r.name);

  const sweepChanged = {};
  for (const d of sweepDefs) {
    const row = rows.find(x => x.group === 'sweep' && x.name === d.name && x.freeText === d.freeText);
    if (!same(row.main, row.here)) bump(sweepChanged, `${outcome(row.main)} -> ${outcome(row.here)}`);
  }

  // Rule 2: every distinct request either side sends, in the store; the coded-only ones also without the exclusion on
  // the snapshot without letter mentions (the meaning by removal).
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'd49-census-'));
  try {
    const requests = new Map();  // JSON -> name
    const nameOf = (req, prefix) => {
      const key = JSON.stringify({query: req.query, conditions: req.conditions, windows: req.windows});
      if (!requests.has(key)) requests.set(key, `${prefix}${requests.size}`);
      return requests.get(key);
    };
    for (const r of rows) for (const side of ['main', 'here']) if (!r[side].refused) r[side + 'Name'] = nameOf(r[side], 'q');
    const full = run([path.join(here, 'store_parity.py'), 'build', cohortPath, path.join(work, 'full')]).trim();
    const caseList = [...requests.entries()].map(([key, name]) => ({name, ...JSON.parse(key)}));
    const sweptFull = JSON.parse(run([path.join(here, 'store_parity.py'), 'sweep', full], JSON.stringify({cohort: cohortPath, cases: caseList})));
    const unnamed = Object.entries(sweptFull).filter(([, a]) => a.error === 'UNNAMED').map(([n, a]) => `${n}: ${a.message}`);
    const answeredCases = caseList.filter(c => !sweptFull[c.name].error);
    const resultsFull = JSON.parse(run([path.join(here, 'store_parity.py'), 'match', full], JSON.stringify({cohort: cohortPath, cases: answeredCases})));
    // The stripped snapshot: the same windows (the page's, placed on the full cohort), the request without the exclusion.
    const {cohort: bare, removed} = withoutMentions(cohort);
    const barePath = path.join(work, 'cohort-without-mentions.json');
    fs.writeFileSync(barePath, JSON.stringify(bare));
    const stripped = run([path.join(here, 'store_parity.py'), 'build', barePath, path.join(work, 'stripped')]).trim();
    const removal = [];
    const seen = new Set();
    for (const r of rows) if (isCodedOnly(r.here) && !seen.has(r.hereName)) {
      seen.add(r.hereName);
      removal.push({name: r.hereName + '#bare', ...withoutExclusion(r.here)});
    }
    const sweptBare = JSON.parse(run([path.join(here, 'store_parity.py'), 'sweep', stripped], JSON.stringify({cohort: barePath, cases: removal})));
    const resultsBare = JSON.parse(run([path.join(here, 'store_parity.py'), 'match', stripped],
                                       JSON.stringify({cohort: barePath, cases: removal.filter(c => !sweptBare[c.name].error)})));
    const pids = cohort.patients.map(p => p.patient_id);
    const answer = (name, pid) => sweptFull[name].error ? {error: sweptFull[name].error} : resultsFull[name][pid];
    const key = a => a ? JSON.stringify({status: a.status, certainty: a.certainty, relaxations: a.relaxations, reason: a.reason, error: a.error}) : 'none';

    // The sweep as the page offers it (each definition under its own setting), on both sides, tallied as the parity test
    // tallies it (answered by the store, refused by the translator, refused by the store).
    const sweepAsOffered = {};
    for (const side of ['main', 'here']) {
      const t = sweepAsOffered[side] = {answered: 0, translator: {}, store: {}, profiles: {}};
      for (const d of sweepDefs) {
        const row = rows.find(x => x.group === 'sweep' && x.name === d.name && x.freeText === d.freeText), r = row[side];
        if (r.refused) { bump(t.translator, r.refused); continue; }
        const error = sweptFull[row[side + 'Name']].error;
        if (error) { bump(t.store, error); continue; }
        t.answered += 1; bump(t.profiles, (r.conditions || {}).profile || 'none');
      }
    }

    // 2.1: unchanged requests. Every request main answers outside the profile moves is byte-identical here, and no file
    // that answers a request differs from the base (so its answer is main's: the same request to the same code).
    const unchanged = rows.filter(r => !r.main.refused && same(r.main, r.here));
    const mainAnswered = rows.filter(r => !r.main.refused);
    const storePaths = ['patterns', 'demos/mockups/store_parity.py', 'demos/mockups/store_trajectory.py', 'demos/mockups/synthetic'];
    const storeDiff = run(['diff', '--name-only', BASE, '--', ...storePaths], undefined, {command: 'git'}).trim();
    const r21 = {main_answered: mainAnswered.length, identical_here: unchanged.length, profile_moves: moved.length,
                 other: mainAnswered.length - unchanged.length - moved.length,
                 store_refused_alike: unchanged.filter(r => sweptFull[r.mainName].error).length,
                 results_of_identical_requests: unchanged.filter(r => !sweptFull[r.mainName].error).length * pids.length,
                 answering_code_changed_since_base: storeDiff ? storeDiff.split('\n') : [], answering_code: storePaths};
    r21.met = r21.other === 0 && !storeDiff;

    // 2.2: the profile moves. Main's request plus the exclusion only; every changed result explained by removal.
    const r22 = {definitions: moved.length, by_group: {}, by_transition: {}, not_exclusion_only: movedNotExclusionOnly.slice(0, 20),
                 results_compared: 0, results_changed: 0, changed_by_class: {}, changed_by_transition: {},
                 changed_not_explained_by_removal: 0, definitions_with_a_change: 0, examples: [], store_refused: {}, names: moved.map(r => `${r.group}: ${r.name}`)};
    for (const r of moved) {
      bump(r22.by_group, r.group); bump(r22.by_transition, `${outcome(r.main)} -> ${outcome(r.here)}`);
      if (sweptFull[r.hereName].error || sweptFull[r.mainName].error) {
        bump(r22.store_refused, `${sweptFull[r.mainName].error || 'answered'} -> ${sweptFull[r.hereName].error || 'answered'}`);
        continue;
      }
      let any = false;
      for (const pid of pids) {
        r22.results_compared += 1;
        const before = answer(r.mainName, pid), after = answer(r.hereName, pid);
        if (key(before) === key(after)) continue;
        any = true;
        r22.results_changed += 1;
        bump(r22.changed_by_class, moveClass(before, after));
        bump(r22.changed_by_transition, `${statusText(before)} -> ${statusText(after)}`);
        if (r22.examples.length < 10) r22.examples.push({definition: r.name, before, after});
        const bareName = r.hereName + '#bare';
        if (sweptBare[bareName].error || key(resultsBare[bareName][pid]) !== key(after)) r22.changed_not_explained_by_removal += 1;
      }
      if (any) r22.definitions_with_a_change += 1;
    }
    r22.met = movedNotExclusionOnly.length === 0 && r22.changed_not_explained_by_removal === 0;

    // 2.3: the meaning by removal, for every coded-only request this tree sends (D39's store-against-store check on the
    // page's cohort): the same answer, patient by patient, as the request without the exclusion on the snapshot without
    // letter mentions; a refusal by the same code on both.
    const r23 = {requests: removal.length, results_compared: 0, results_differing: 0, refused_alike: 0, refused_differently: 0,
                 letter_mentions_removed: removed, profiles: {}, examples: []};
    for (const c of removal) {
      const name = c.name.replace(/#bare$/, '');
      const profile = rows.find(r => r.hereName === name).here.conditions.profile;
      bump(r23.profiles, profile);
      const a = sweptFull[name].error, b = sweptBare[c.name].error;
      if (a || b) { if (a === b) r23.refused_alike += 1; else r23.refused_differently += 1; continue; }
      for (const pid of pids) {
        r23.results_compared += 1;
        if (key(resultsFull[name][pid]) !== key(resultsBare[c.name][pid])) {
          r23.results_differing += 1;
          if (r23.examples.length < 10) r23.examples.push({request: name, patient_id: pid, full: resultsFull[name][pid], stripped: resultsBare[c.name][pid]});
        }
      }
    }
    r23.met = r23.results_differing === 0 && r23.refused_differently === 0 && r23.results_compared > 0;

    // 2.4 (reported): the requests the flip newly answers (refused on main), their store outcome and status mix.
    const fresh = rows.filter(r => r.main.refused && !r.here.refused);
    const r24 = {definitions: fresh.length, by_transition: {}, store_refused: {}, statuses: {}, reasons: {}, results: 0};
    for (const r of fresh) {
      bump(r24.by_transition, `${outcome(r.main)} -> ${outcome(r.here)}`);
      if (sweptFull[r.hereName].error) { bump(r24.store_refused, sweptFull[r.hereName].error); continue; }
      for (const pid of pids) {
        const a = resultsFull[r.hereName][pid];
        r24.results += 1; bump(r24.statuses, a.status);
        if (a.reason) bump(r24.reasons, a.reason);
      }
    }
    // Every answer has a result for every patient (D37 rule 1).
    const incomplete = answeredCases.filter(c => Object.keys(resultsFull[c.name]).length !== pids.length).map(c => c.name);

    // Reported: the refusal codes this tree's page can show (translator or store, over every row) without a page message.
    const known = new Set(inContext(ctx.here, 'Object.keys(STORE_REFUSAL)'));
    const codes = new Set();
    for (const r of rows) {
      if (r.here.refused) codes.add(r.here.refused);
      else if (sweptFull[r.hereName].error) codes.add(sweptFull[r.hereName].error);
    }
    const storeRefusals = {};
    for (const a of Object.values(sweptFull)) if (a.error) bump(storeRefusals, a.error);
    const report = {
      profile: 'd49-flip-census-1.0',
      rule: ('D49 rules 1 and 2 on the page\'s cohort, main\'s page (constants false) read from git at the base against this '
             + 'tree\'s (both true), every page template, D39\'s two shapes and every option-sweep definition under each '
             + 'free-text setting, translated with no options as the page does. 1.1: nothing moves outside "Coded records '
             + 'only", and under it this tree\'s request equals main\'s translator with the explicit option (codedOnly store, '
             + 'geneCodedOnly true); 1.2 reported: the transitions. 2.1: every request main answers outside the profile '
             + 'moves is byte-identical here, and no answering code differs from the base; 2.2: every profile move is main\'s request plus the '
             + 'exclusion only, and every changed result equals the old request\'s on the snapshot without letter mentions; '
             + '2.3: every coded-only request this tree sends answers as the request without the exclusion on that '
             + 'snapshot; 2.4 reported: the newly answered requests'),
      base: BASE, cohort: {children: pids.length, sha256: shaText(cohortText), page_cohort: cohortPath === COHORT},
      translators: {main: shaText(baseText(FILES.translator)), here: sha(FILES.translator)},
      rule_1_1: r11, sweep_as_offered: {definitions: sweepDefs.length, main: sweepAsOffered.main, here: sweepAsOffered.here, changed: sweepChanged},
      rule_2_1: r21, rule_2_2: r22, rule_2_3: r23, rule_2_4: r24,
      distinct_requests: caseList.length, store_refusals: storeRefusals,
      unnamed: unnamed.slice(0, 20), incomplete: incomplete.slice(0, 20),
      messages: {codes: [...codes].sort(), without_a_message: [...codes].filter(c => !known.has(c)).sort()},
      met: {rule_1_1: r11.met, rule_2_1: r21.met, rule_2_2: r22.met, rule_2_3: r23.met, named: !unnamed.length && !incomplete.length},
      seconds: Math.round((Date.now() - t0) / 1000),
      artifacts: artifacts(), contains_patient_level_data: false, ...(SMOKE ? {smoke: true} : {}),
    };
    write(output, report);
    console.error(`rule 1.1 ${r11.met ? 'met' : 'MISSED'} (${r11.changed_under_coded_only} changed under coded only of ${definitions}); `
                  + `2.1 ${r21.met ? 'met' : 'MISSED'} (${r21.identical_here} identical, ${r21.other} other); `
                  + `2.2 ${r22.met ? 'met' : 'MISSED'} (${r22.definitions} moves, ${r22.results_changed} results changed: ${JSON.stringify(r22.changed_by_class)}); `
                  + `2.3 ${r23.met ? 'met' : 'MISSED'} (${r23.requests} requests, ${r23.results_differing} differing); `
                  + `sweep as offered: main ${sweepAsOffered.main.answered}, here ${sweepAsOffered.here.answered} answered of ${sweepDefs.length}`);
  } finally {
    fs.rmSync(work, {recursive: true, force: true});
  }
}

/* Rule 3: usefulness. Fresh seeds, declared in D49 before any measurement: 4911 to 4940, then 4941, 4942, ... in order
   to 5010 while some template has fewer than 30 candidates pooled (the stop reads candidate counts only). */
const SEEDS = Array.from({length: 30}, (_, i) => 4911 + i);
const EXTEND = Array.from({length: 70}, (_, i) => 4941 + i);
const MINIMUM = 30, SHARE = 0.3;
const RULED = ['sequence', 'tree', 'graph', 'event frequency', 'blank'];
const planKeys = seed => JSON.parse(run(['-c', 'import json, sys; sys.path.insert(0, "demos/mockups/synthetic"); '
  + 'import generate as g; print(json.dumps({c.pid: c.key for c in g.build(int(sys.argv[1]))}))', String(seed)]));

function tally(results, c) {
  const candidates = results[c.name + '#with_candidate'];
  const statuses = {}, reasons = {};
  let unresolved = 0, exact = 0;
  for (const pid of candidates) {
    const r = results[c.name][pid];
    bump(statuses, r.status);
    if (r.certainty === 'exact' && r.status === 'exact') exact += 1;
    if (r.status === 'unresolved') { unresolved += 1; bump(reasons, r.reason); }
  }
  for (const [pid, r] of Object.entries(results[c.name]))
    if (r.status === 'unresolved' && !candidates.includes(pid)) throw new Error(`${c.name} ${pid}: unresolved without a candidate`);
  return {candidates: candidates.length, unresolved, statuses, reasons, profile: (c.conditions || {}).profile || null,
          candidate_ids: candidates, unresolved_ids: candidates.filter(pid => results[c.name][pid].status === 'unresolved')};
}
const RULED_NAMES = RULED.map(n => `${n}, coded only`);

function usefulness(output) {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'd49-usefulness-'));
  const perSeed = {}, used = [];
  let refusals = null;
  try {
    const measure = seed => {
      const cohortPath = path.join(work, `cohort-${seed}.json`);
      run(['demos/mockups/synthetic/generate.py', '--seed', String(seed), '--output', cohortPath]);
      const cohortText = fs.readFileSync(cohortPath, 'utf8');
      const context = pageContext(cohortText, 'here');
      const cases = [], r = {};
      for (const [name, def] of Object.entries(inContext(context, 'TEMPLATE_DEFS'))) for (const [label, f] of [['coded only', 'off'], ['stated findings', 'present']]) {
        const t = translate(context, def, f);  // the page's own call: no options
        if (t.refused) r[`${name}, ${label}`] = t.refused; else cases.push({name: `${name}, ${label}`, ...t});
      }
      refusals = refusals || r;
      assert.deepEqual(r, refusals, `seed ${seed}: the refusals differ from the first seed's`);
      const snapshot = run(['demos/mockups/store_parity.py', 'build', cohortPath, path.join(work, `store-${seed}`)]).trim();
      const results = JSON.parse(run(['demos/mockups/store_parity.py', 'match', snapshot], JSON.stringify({cohort: cohortPath, cases})));
      fs.rmSync(path.join(work, `store-${seed}`), {recursive: true, force: true});
      perSeed[seed] = {children: JSON.parse(cohortText).patients.length, snapshot_id: path.basename(snapshot), keys: planKeys(seed),
                       cohort_sha256: shaText(cohortText), templates: Object.fromEntries(cases.map(c => [c.name, tally(results, c)]))};
      used.push(seed);
      console.error(`seed ${seed}: ` + RULED_NAMES.filter(n => perSeed[seed].templates[n]).map(n => `${n} ${perSeed[seed].templates[n].unresolved}/${perSeed[seed].templates[n].candidates}`).join('; '));
    };
    for (const seed of SMOKE ? [4901] : SEEDS) measure(seed);
    const pooledCandidates = n => used.reduce((k, s) => k + (perSeed[s].templates[n] ? perSeed[s].templates[n].candidates : 0), 0);
    for (const seed of SMOKE ? [] : EXTEND) { if (!RULED_NAMES.some(n => pooledCandidates(n) < MINIMUM)) break; measure(seed); }
    const names = Object.keys(perSeed[used[0]].templates);
    const pooled = {}, byKey = {}, readings = {};
    for (const name of names) {
      const p = pooled[name] = {candidates: 0, unresolved: 0, statuses: {}, reasons: {}, profile: perSeed[used[0]].templates[name].profile};
      const k = byKey[name] = {};
      for (const s of used) {
        const t = perSeed[s].templates[name], keys = perSeed[s].keys;
        p.candidates += t.candidates; p.unresolved += t.unresolved;
        for (const [x, v] of Object.entries(t.statuses)) bump(p.statuses, x, v);
        for (const [x, v] of Object.entries(t.reasons)) bump(p.reasons, x, v);
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
      if (!readings[n]) return [n, {refused: refusals[n], met: false}];
      const d = readings[n].distinct_child_types;
      return [n, {types: d.types, types_unresolved: d.types_unresolved, share_of_types: d.types ? d.types_unresolved / d.types : null,
                  candidates: pooled[n].candidates, unresolved: pooled[n].unresolved,
                  without_C001: readings[n].without_C001, enough_candidates: pooled[n].candidates >= MINIMUM,
                  met: pooled[n].candidates >= MINIMUM && d.types > 0 && d.types_unresolved <= SHARE * d.types}];
    }));
    const coverage = {refused_under_coded_only: Object.fromEntries(Object.entries(refusals).filter(([k]) => k.endsWith(', coded only'))),
                      refused_under_stated_findings: Object.fromEntries(Object.entries(refusals).filter(([k]) => k.endsWith(', stated findings')))};
    coverage.met = !Object.keys(coverage.refused_under_coded_only).length;
    const report = {
      profile: 'd49-flip-usefulness-1.0',
      rule: ('D49 rule 3 (the owner\'s counting rule): each page template under "Coded records only", translated by this '
             + 'tree\'s page translator with no options (profile 1.3; the joint profile for Tree and Graph), pooled over the '
             + 'declared seeds: at least 30 patients with a store candidate and an index window, and at most 30% of the '
             + 'distinct child types (PLAN keys; C001 is reference) with a candidate unresolved (a type is unresolved when '
             + 'any of its candidates is); none of the five refused. All candidates and the count without C001 reported; the '
             + 'templates under stated findings reported beside'),
      declared_seeds: SEEDS, extension_seeds: EXTEND, seeds_used: used,
      setting: {window: 'from the first seizure, 24 months (the page\'s own)', scope: 'from_start',
                free_text: 'off (Coded records only); reported beside it: present (stated findings)',
                translator: 'trajectory/store_request.js as committed (STORE_CODED_ONLY and STORE_GENE_CODED_ONLY true), no options',
                store: 'store_parity.py build (the converter emitting series, through the gene wrapper) and match (store_conditions.match, the count post-pass on)'},
      bar, coverage, met: RULED_NAMES.every(n => bar[n].met) && coverage.met,
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
    console.error('bar: ' + RULED_NAMES.map(n => bar[n].refused ? `${n} refused ${bar[n].refused}` : `${n} ${bar[n].types_unresolved}/${bar[n].types} types, ${bar[n].unresolved}/${bar[n].candidates} candidates (${bar[n].met ? 'met' : 'missed'})`).join('; ')
                  + `; coverage ${coverage.met ? 'met' : 'missed'}`);
  } finally {
    fs.rmSync(work, {recursive: true, force: true});
  }
}

module.exports = {withoutExclusion, moveClass, withoutMentions, declared, SEEDS, EXTEND};

if (require.main === module) {
  const command = process.argv[2];
  const output = arg('--output');
  if (command === 'census') census(output);
  else if (command === 'usefulness') usefulness(output);
  else { console.error('usage: d49_flip.cjs census|usefulness [--output REPORT.json]'); process.exit(2); }
}
