/* D46: index periods placed without the generator's truth. The page side of D46's rules (decision log, D46).

   Since D46 the trajectory page places each event bound of an index period over its index view (shared/core.js,
   `indexViewFor`, rule reconcile_v1), not over evidenceFor's grouping by `truth_event_id`. The store's matching is
   unchanged; only the windows the page sends move. This tool loads the page's own code (shared/core.js,
   trajectory/app.js, trajectory/store_request.js) into node:vm contexts, as the parity and usefulness tools do, and
   compares this tree with main's page (core.js and app.js read from git at --base, default 28a3cbf; the translator is
   unchanged), whose windows are grouped by truth.

     node demos/mockups/d46_windows.cjs windows --cohort FILE --output FILE [--defect NAME]
       Every patient's window under every period rule and free-text setting, in the store request's minutes (the input
       of index_reference.py compare).
     node demos/mockups/d46_windows.cjs reference [--output REPORT.json]
       Rule 3: the page's windows against index_reference.py on the page's cohort and seeds 4611-4620; rule 2's
       perturbations (records shuffled, record ids permuted) and planted defects (`truth` must move a window when the
       cohort's truth fields are removed; each other ruled defect must give a mismatch).
     node demos/mockups/d46_windows.cjs census [--output REPORT.json]
       Rule 4: on the page's cohort, every moved window and every changed store result against main's windows, each
       classified; rule 8 (reported): the store's results again with each event anchor at its latest time.
     node demos/mockups/d46_windows.cjs usefulness [--output REPORT.json]
       Rule 5: the page templates over fresh cohorts (seeds 4621-4650, extended in order to 4700), the owner's counting
       rule, with main's windows on the same seeds reported beside.

   Synthetic data only (generator patient ids, as the parity test pins them). Needs DuckDB for `census` and `usefulness`
   (store_parity.py), and git for main's page. */
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
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'd46-stdin-'));
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
const BASE = arg('--base', '28a3cbf');
// A smoke run to check the tool runs (D46_SMOKE=1): one undeclared seed (4601, the design probe's), never a ruled seed,
// and never written into verification/.
const SMOKE = process.env.D46_SMOKE === '1';

// Planted defects for rule 2 (each substitution must occur exactly once in core.js).
const DEFECTS = {
  // Reconciliation 30 days wide: ranges that miss by up to 30 days still meet.
  tau30: ['const meets = (a, b) => a.e <= b.l && a.l >= b.e;', 'const meets = (a, b) => a.e <= b.l + 30 && a.l >= b.e - 30;'],
  // Reading the truth: a coded record joins the happening of its truth event first.
  truth: ['const h = nearest(coded.filter(k => compatible(k.concept, c) && meets(k.head, range)), range);',
          'const h = coded.find(k => r.truth_event_id && k.recs[0].truth_event_id === r.truth_event_id) || nearest(coded.filter(k => compatible(k.concept, c) && meets(k.head, range)), range);'],
  // The review's mutants (D46 review, S1), each a departure from one clause of reconcile_v1:
  reach400: ['(k.lastDisp.attributes?.supply_days || 30) + 45 : Infinity', '(k.lastDisp.attributes?.supply_days || 30) + 400 : Infinity'],
  stop_ignored: ['} else if (open) { open.recs.push(r); open = null; }', '} else if (open) { open.recs.push(r); }'],
  diary_any: ["coded.filter(k => isA(k.concept, 'seizure') && meets(k.head, range))", 'coded.filter(k => meets(k.head, range))'],
  tie_last: ['Math.abs(mid(h.range) - mid(x)) < Math.abs(mid(best.range) - mid(x))', 'Math.abs(mid(h.range) - mid(x)) <= Math.abs(mid(best.range) - mid(x))'],
  no_precision: ['findings.sort((a, b) => PRECISION[a.occurred.precision] - PRECISION[b.occurred.precision] || ', 'findings.sort((a, b) => '],
  mention_head: ['meets(k.range, y.range)), y.range);\n    if (h) { h.ms.push', 'meets(k.head || k.range, y.range)), y.range);\n    if (h) { h.ms.push'],
  gene_any: ['tests.filter(k => k.gene === g && meets(k.head, range))', 'tests.filter(k => k.gene === g)'],
  join_whole_range: ['coded.filter(k => compatible(k.concept, c) && meets(k.head, range))', 'coded.filter(k => compatible(k.concept, c) && meets(k.range, range))'],
};
// The defects rules 2 and 3 rule: each must be caught. `truth` by the truth-free check (a window moves when the truth is
// removed); every other by the reference (at least one mismatch over rule 3's cohorts).
// Ruled: those that moved a window on at least two of the three development cohorts (seeds 4601, 990011, 990012; disclosed
// in the log). Reported only: diary_any, tie_last, mention_head and join_whole_range (each moved windows on one of the
// three), and gene_any (on none: a period anchored on a gene or on any genetic result reads the earliest report, which
// grouping cannot change).
const RULED_DEFECTS = ['truth', 'tau30', 'reach400', 'stop_ignored', 'no_precision'];
const CORE = path.join(here, 'shared/core.js'), APP = path.join(here, 'trajectory/app.js');
function coreText(defect) {
  let text = fs.readFileSync(CORE, 'utf8');
  if (!defect) return text;
  const [from, to] = DEFECTS[defect];
  assert.equal(text.split(from).length - 1, 1, `defect ${defect}: its text must occur once in core.js`);
  return text.replace(from, to);
}
const baseText = file => run(['show', `${BASE}:${path.relative(root, file)}`], undefined, {command: 'git'});

// The seven index-period rules of the option sweep (option_sweep.js; the parity trajectories use the first two), and
// the page templates with the page's own window and scope (as coded_default.cjs defines them).
const DEFINITIONS = `
  var PERIOD_RULES = {
    'the first seizure for 24 months (the page\\'s default)': defaultDef().window,
    'birth to 6 years': {start: {kind: 'birth'}, end: {kind: 'age', amount: 6, unit: 'year'}},
    'from 2020-01-01 for 3 years': {start: {kind: 'date', date: '2020-01-01'}, end: {kind: 'span', amount: 3, unit: 'year'}},
    '12 months up to the last seizure': {start: {kind: 'span', amount: 12, unit: 'month'}, end: {kind: 'event', event: 'seizure', which: 'last', n: 1, offset: 0, unit: 'month'}},
    'a month before the 2nd-to-last febrile seizure to 10 years': {start: {kind: 'event', event: 'febrile_seizure', which: 'nth_last', n: 2, offset: -1, unit: 'month'}, end: {kind: 'age', amount: 10, unit: 'year'}},
    'the first diagnosis for 2 years': {start: {kind: 'event', event: '@any_diagnosis', which: 'first', n: 1, offset: 0, unit: 'month'}, end: {kind: 'span', amount: 2, unit: 'year'}},
    'the first genetic result for 2 years': {start: {kind: 'event', event: '@any_genetic', which: 'first', n: 1, offset: 0, unit: 'month'}, end: {kind: 'span', amount: 2, unit: 'year'}},
  };
  // Five more (D46 review, S1), so that a ruled period anchors on a medicine, a drug class, a gene and an Nth > 1: the
  // medication, genetic and tie-break paths of reconcile_v1 are then reachable by rules 2 to 4.
  var EXTENDED_RULES = {
    'the 2nd antiseizure start for 12 months': {start: {kind: 'event', event: 'antiseizure', which: 'nth', n: 2, offset: 0, unit: 'month'}, end: {kind: 'span', amount: 12, unit: 'month'}},
    'the last valproate start, 6 months before it to it': {start: {kind: 'event', event: 'valproate', which: 'last', n: 1, offset: -6, unit: 'month'}, end: {kind: 'span', amount: 6, unit: 'month'}},
    'the 3rd-from-last antiseizure start for 6 months': {start: {kind: 'event', event: 'antiseizure', which: 'nth_last', n: 3, offset: 0, unit: 'month'}, end: {kind: 'span', amount: 6, unit: 'month'}},
    'the first SCN1A result for 1 year': {start: {kind: 'event', event: 'gene:SCN1A', which: 'first', n: 1, offset: 0, unit: 'month'}, end: {kind: 'span', amount: 1, unit: 'year'}},
    'the 3rd seizure for 12 months': {start: {kind: 'event', event: 'seizure', which: 'nth', n: 3, offset: 0, unit: 'month'}, end: {kind: 'span', amount: 12, unit: 'month'}},
  };
  var SWEEP_RULE_NAMES = Object.keys(PERIOD_RULES);
  Object.assign(PERIOD_RULES, EXTENDED_RULES);
  var pageWindow = defaultDef().window;
  var shape = t => ({scope: 'from_start', window: clone(pageWindow), nodes: clone(t.nodes), edges: clone(t.edges)});
  var TEMPLATE_DEFS = {'sequence': shape(TEMPLATES.sequence), 'tree': shape(TEMPLATES.tree), 'graph': shape(TEMPLATES.graph),
                       'event frequency': shape(TEMPLATES.frequency), 'blank': shape(BLANK)};
  var anchorView = pid => typeof indexViewFor === 'function' ? indexViewFor(pid) : evidenceFor(pid);
  var placeOne = (pid, window, freeText) => {
    const w = windowFor(evidenceFor(pid), {freeText, knownByIndex: false, window});
    return w.reason ? {reason: w.reason} : {start: toGrid(w.start), end: toGrid(w.end)};
  };
  var placeAll = (window, freeText) => Object.fromEntries([...state.patients.keys()].map(pid => [pid, placeOne(pid, window, freeText)]));
  // The happening an event bound is placed on, and what it holds: record ids and letter mentions (letter#position).
  var anchorOf = (pid, b, freeText) => {
    if (b.kind !== 'event') return null;
    const view = anchorView(pid), q = {freeText, knownByIndex: false};
    const items = usable(view, q, {start: -Infinity, end: Infinity});
    const id = b.event, kind = state.concepts[id]?.kind;
    let timed;
    if (id === '@any_diagnosis') timed = items.filter(i => i.kind === 'diagnosis').map(i => [i, i.time.best.e]);
    else if (id === '@any_genetic') timed = items.filter(i => i.kind === 'genetic' && i.gene.history.length).map(i => [i, i.gene.history[0].recorded]);
    else if (kind === 'medication' || kind === 'drug_class') timed = items.filter(i => i.kind === 'medication' && (i.concept === id || classesOf(i.concept).includes(id))).map(i => [i, i.med.start.e]);
    else if (kind === 'gene') timed = items.filter(i => i.kind === 'genetic' && i.concept === id && i.gene.history.length).map(i => [i, i.gene.history[0].recorded]);
    else timed = items.filter(i => (i.kind === 'phenotype' || i.kind === 'diagnosis') && isA(conceptFor(i, q), id)).map(i => [i, i.time.best.e]);
    timed.sort((x, y) => x[1] - y[1]);
    const picked = pickOccurrence(timed, b.which, b.n)[0];
    if (!picked) return null;
    const [item, at] = picked;
    return {at: toGrid(at), times: timed.map(x => toGrid(x[1])), from_end: b.which === 'last' || b.which === 'nth_last', records: item.recs.map(r => r.record_id + ':' + r.kind),
            mentions: item.mentions.map(x => x.note.record_id + '#' + x.note.mentions.indexOf(x.m))};
  };
  // How many of the anchor view's happenings hold any of these records or mentions (for classifying a moved window).
  var holders = (pid, keys) => {
    const want = new Set(keys);
    return anchorView(pid).items.filter(i => i.recs.some(r => want.has(r.record_id + ':' + r.kind))
      || i.mentions.some(x => want.has(x.note.record_id + '#' + x.note.mentions.indexOf(x.m)))).length;
  };
  // Rule 8 (reported): the window with every event anchor at the latest time its most precise record allows (a
  // medicine at its start's latest), placed by the same rule over a copy of the anchor view.
  var lateOne = (pid, window, freeText) => {
    const view = anchorView(pid);
    const late = {...view, items: view.items.map(i => ({...i,
      time: i.time && i.time.best ? {...i.time, best: {...i.time.best, e: i.time.best.l}} : i.time,
      med: i.med && i.med.start ? {...i.med, start: {...i.med.start, e: i.med.start.l}} : i.med}))};
    let w;
    if (typeof indexViewFor === 'function') {
      state.indexViews.set(pid, late);
      try { w = windowFor(evidenceFor(pid), {freeText, knownByIndex: false, window}); } finally { state.indexViews.set(pid, view); }
    } else w = windowFor(late, {freeText, knownByIndex: false, window});
    return w.reason ? null : {start: toGrid(w.start), end: toGrid(w.end)};
  };
  var translateOne = (def, freeText, options, late) => {
    const before = state.freeText;
    state.freeText = freeText;
    try {
      const r = storeRequest({...defaultDef(), ...def}, freeText, [...state.patients.keys()], options);
      if (late) r.windows = Object.fromEntries(Object.keys(r.windows).map(pid => [pid, lateOne(pid, def.window, freeText)]));
      return r;
    } catch (e) { if (e instanceof StoreRefusal) return {refused: e.code}; throw e; }
    finally { state.freeText = before; }
  };
  // Rule 7 (reported): the page-side cost of the view an index period is placed over, every patient from a cold cache.
  var viewCost = repeats => {
    const runs = [];
    for (let k = 0; k < repeats; k++) {
      const t0 = performance.now();
      for (const pid of state.patients.keys()) {
        if (typeof indexViewFor === 'function') { state.indexViews.delete(pid); state.evidence.delete(pid); indexViewFor(pid); }  // D47: the page's view
        else { state.evidence.delete(pid); evidenceFor(pid); }
      }
      runs.push(performance.now() - t0);
    }
    runs.sort((a, b) => a - b);
    return {repeats, median_ms_all_patients: runs[Math.floor(repeats / 2)], patients: state.patients.size};
  };
  var sweepCases = () => optionSweepDefinitions().map(({name, def, freeText}) => ({name, def, freeText}));
`;

function pageContext(cohortText, which, defect) {
  const stub = {addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; }};
  const context = vm.createContext({document: stub, addEventListener() {}, console, structuredClone, performance, DATA: cohortText,
                                    POST: () => { throw new Error('D46 never calls the page engine'); }});
  const core = which === 'main' ? baseText(CORE) : coreText(defect);
  const app = (which === 'main' ? baseText(APP) : fs.readFileSync(APP, 'utf8')).replace(/\nmain\(\);\s*$/, '\n');
  vm.runInContext(core + '\n' + app + '\n' + fs.readFileSync(path.join(here, 'trajectory/store_request.js'), 'utf8') + `
    state.data = JSON.parse(DATA); state.concepts = state.data.concepts;
    for (const [cid, c] of Object.entries(state.concepts)) for (const coding of c.codings) state.codeIndex.set(coding.system + '|' + coding.code, cid);
    for (const p of state.data.patients) state.patients.set(p.patient_id, p);
    state.freeText = 'present';
  ` + DEFINITIONS, context);
  vm.runInContext(fs.readFileSync(path.join(here, 'option_sweep.js'), 'utf8'), context);
  assert.equal(vm.runInContext('typeof indexViewFor', context), which === 'main' ? 'undefined' : 'function',
               `${which}: the page's index view is ${which === 'main' ? 'not ' : ''}expected`);
  return context;
}
const inContext = (context, code) => JSON.parse(vm.runInContext(`JSON.stringify(${code})`, context));

const ARTIFACTS = ['demos/mockups/d46_windows.cjs', 'demos/mockups/index_reference.py', 'demos/mockups/option_sweep.js',
  'demos/mockups/store_parity.py', 'demos/mockups/trajectory/store_request.js', 'demos/mockups/trajectory/app.js',
  'demos/mockups/shared/core.js', 'demos/mockups/synthetic/generate.py', 'patterns/store_conditions.py',
  'patterns/store_conditions_coded.py', 'patterns/store_conditions_gene.py', 'patterns/store_conditions_relative.py',
  'patterns/store_conditions_counts.py', 'patterns/execution_store.py', 'patterns/synthetic_ingest.py',
  'patterns/trajectory_graph.py', 'patterns/trajectory_times.py', 'patterns/temporal_stn.py'];
const artifacts = () => Object.fromEntries(ARTIFACTS.map(f => [f, sha(f)]));
const write = (output, report) => {
  if (SMOKE && output && path.resolve(output).startsWith(path.join(root, 'verification'))) throw new Error('a smoke run is never written into verification/');
  const text = JSON.stringify(report, null, 1) + '\n';
  if (output) fs.writeFileSync(output, text); else process.stdout.write(text);
};
const FREE_TEXT = ['present', 'off', 'possible'];
// D49: the translator's options that reproduce its default before D49 (STORE_CODED_ONLY and STORE_GENE_CODED_ONLY false).
const PRE_FLIP = {codedOnly: null};
const generate = (seed, file) => run(['demos/mockups/synthetic/generate.py', '--seed', String(seed), '--output', file]);

/* Windows for index_reference.py: every rule, every free-text setting, every patient. */
function windowsOf(cohortText, defect) {
  const context = pageContext(cohortText, 'this', defect);
  const rules = inContext(context, 'PERIOD_RULES');
  const out = {};
  for (const name of Object.keys(rules)) {
    out[name] = {};
    for (const f of FREE_TEXT) out[name][f] = inContext(context, `placeAll(PERIOD_RULES[${JSON.stringify(name)}], ${JSON.stringify(f)})`);
  }
  return {rules, windows: out};
}

/* Rule 2's truth-free reading: the same windows with every truth field removed from the cohort. */
function stripped(cohortText) {
  const d = JSON.parse(cohortText);
  for (const p of d.patients) {
    delete p.truth; p.quality = [];
    for (const r of p.records) { delete r.truth_event_id; for (const m of r.mentions || []) delete m.truth_event_id; }
  }
  return JSON.stringify(d);
}

/* The review's perturbations (S2): each patient's records shuffled, and record ids permuted (they are sequential in
   truth-event order and are the rule's last tie-break), with a fixed generator so the run repeats. */
function perturbed(text) {
  let seed = 12345;
  const rand = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
  const shuffle = a => { for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(rand() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };
  const shuffled = JSON.parse(text);
  for (const p of shuffled.patients) shuffle(p.records);
  const renamed = JSON.parse(text);
  for (const p of renamed.patients) {
    const ids = p.records.map(r => r.record_id), perm = shuffle([...ids]);
    const map = Object.fromEntries(ids.map((id, i) => [id, perm[i]]));
    for (const r of p.records) r.record_id = map[r.record_id];
  }
  return {records_shuffled: JSON.stringify(shuffled), record_ids_permuted: JSON.stringify(renamed)};
}

const REFERENCE_SEEDS = Array.from({length: 10}, (_, i) => 4611 + i);
function reference(output) {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'd46-reference-'));
  try {
    const cohorts = SMOKE ? [['seed 4601', 4601]] : [['the page\'s cohort', null], ...REFERENCE_SEEDS.map(s => [`seed ${s}`, s])];
    const rows = {}, defects = Object.fromEntries(Object.keys(DEFECTS).map(k => [k, k === 'truth' ? {moved_by_removing_truth: 0} : {mismatches: 0}]));
    let truthFree = {compared: 0, differing: 0, perturbed: {records_shuffled: 0, record_ids_permuted: 0}};
    for (const [name, seed] of cohorts) {
      const cohortPath = seed == null ? COHORT : path.join(work, `cohort-${seed}.json`);
      if (seed != null) generate(seed, cohortPath);
      const text = fs.readFileSync(cohortPath, 'utf8');
      const compareWith = (w, tag) => {
        const file = path.join(work, `windows-${seed}-${tag}.json`);
        fs.writeFileSync(file, JSON.stringify(w));
        let out;
        try { out = run(['demos/mockups/index_reference.py', 'compare', cohortPath, file]); }
        catch (e) { if (e.status !== 1) throw e; out = e.stdout; }
        return JSON.parse(out);
      };
      const w = windowsOf(text);
      const r = compareWith(w, 'page');
      const placed = Object.fromEntries(Object.entries(w.windows).map(([k, per]) => [k, Object.fromEntries(Object.entries(per)
        .map(([f, by]) => [f, Object.values(by).filter(x => !x.reason).length]))]));
      rows[name] = {cohort_sha256: shaText(text), compared: r.compared, placed: r.placed, mismatches: r.mismatches,
                    first_mismatches: r.first_mismatches, placed_by_rule: placed};
      // Rule 2: the windows do not move when every truth field is removed.
      const free = windowsOf(stripped(text));
      for (const k of Object.keys(w.windows)) for (const f of FREE_TEXT) for (const pid of Object.keys(w.windows[k][f])) {
        truthFree.compared += 1;
        if (!same(w.windows[k][f][pid], free.windows[k][f][pid])) truthFree.differing += 1;
      }
      // The review's perturbations (S2), on the stripped cohort: records shuffled, record ids permuted.
      for (const [k, t] of Object.entries(perturbed(stripped(text)))) {
        const pw = windowsOf(t);
        for (const r of Object.keys(w.windows)) for (const f of FREE_TEXT) for (const pid of Object.keys(w.windows[r][f]))
          if (!same(w.windows[r][f][pid], pw.windows[r][f][pid])) truthFree.perturbed[k] += 1;
      }
      // Planted defects.
      for (const k of Object.keys(DEFECTS)) if (k !== 'truth') defects[k].mismatches += compareWith(windowsOf(text, k), k).mismatches;
      const wt = windowsOf(text, 'truth'), ft = windowsOf(stripped(text), 'truth');
      for (const k of Object.keys(wt.windows)) for (const f of FREE_TEXT) for (const pid of Object.keys(wt.windows[k][f]))
        if (!same(wt.windows[k][f][pid], ft.windows[k][f][pid])) defects.truth.moved_by_removing_truth += 1;
      console.error(`${name}: ${r.compared} windows compared, ${r.placed} placed, ${r.mismatches} mismatches`);
    }
    const mismatches = Object.values(rows).reduce((a, r) => a + r.mismatches, 0);
    for (const [k, v] of Object.entries(defects)) { v.caught = (k === 'truth' ? v.moved_by_removing_truth : v.mismatches) > 0; v.ruled = RULED_DEFECTS.includes(k); }
    truthFree.met = truthFree.differing === 0 && Object.values(truthFree.perturbed).every(n => n === 0);
    write(output, {
      profile: 'd46-window-reference-1.0',
      rule: ('D46 rules 2 and 3: every window the page places (seven period rules, three free-text settings, every patient) '
             + 'equals index_reference.py\'s, to the minute, on the page\'s cohort and seeds 4611-4620; the windows are the '
             + 'same with every truth field removed from the cohort; a 30-day reconciliation defect gives a mismatch and a '
             + 'defect reading truth_event_id moves a window when the truth is removed'),
      declared_seeds: REFERENCE_SEEDS, cohorts: rows, mismatches, truth_free: truthFree, defects,
      ruled_defects: RULED_DEFECTS,
      met: {reference: mismatches === 0, truth_free: truthFree.met, defects: RULED_DEFECTS.every(k => defects[k].caught)},
      artifacts: artifacts(), contains_patient_level_data: false,
      synthetic_patient_ids: 'generator ids only, in first_mismatches', ...(SMOKE ? {smoke: true} : {}),
    });
  } finally {
    fs.rmSync(work, {recursive: true, force: true});
  }
}

/* The store's results for a set of cases (names unique), on one built snapshot. */
function storeResults(cohortPath, snapshot, cases) {
  return JSON.parse(run(['demos/mockups/store_parity.py', 'match', snapshot], JSON.stringify({cohort: cohortPath, cases})));
}
const resultKey = r => r ? JSON.stringify({status: r.status, certainty: r.certainty, relaxations: r.relaxations, reason: r.reason}) : 'none';
const statusText = r => r ? (r.certainty ? `${r.status}/${r.certainty}` : r.status) : '-';

/* A moved window's class, from the anchors that place it under main's grouping and this tree's. */
function classify(main, mine, held) {
  const kindsOf = keys => [...new Set(keys.map(k => k.includes('#') ? 'mention' : k.split(':').pop()))].sort();
  const keys = a => a ? [...a.records, ...a.mentions] : [];
  if (!main && !mine) return 'no event anchor';
  if (!main) return 'anchored only here';
  if (!mine) return 'anchored only on main';
  // A bound whose anchor has the same time did not move, whatever its happening holds.
  if (main.at === mine.at) return 'same anchor';
  const m = new Set(keys(main)), t = new Set(keys(mine));
  const shared = [...m].some(k => t.has(k));
  if (!mine.records.length && main.records.length) return 'a mention-only happening anchors here';
  if (!main.records.length && mine.records.length) return 'a mention-only happening anchored on main';
  if (shared) {
    const diff = [...m].filter(k => !t.has(k)).concat([...t].filter(k => !m.has(k)));
    return diff.length ? `the same happening, other records: ${kindsOf(diff).join(', ')} (timed differently)` : 'unexplained';
  }
  // Another happening: main's anchor split here, or this tree's anchor merged from several of main's, or neither (a
  // different occurrence ranks first, last or Nth because a happening elsewhere was split or merged).
  const {mainHereCount, mineMainCount} = held();
  if (mainHereCount > 1) return 'another happening: main\'s anchor is split here';
  if (mineMainCount > 1) return 'another happening: merged here from several of main\'s';
  // Another occurrence ranks only if the occurrences up to an anchor differ (D46 review, S3): at main's anchor time or
  // this tree's, the number of occurrences counted from the occurrence's end (the start for first and Nth, the end for
  // last and Nth from last) up to and including that time differs between the two sides.
  const upTo = (a, t) => a.times.filter(x => a.from_end ? x >= t : x <= t).length;
  if ([main.at, mine.at].some(t => upTo(main, t) !== upTo(mine, t))) return 'another happening: another occurrence ranks';
  return 'unexplained';
}

// A moved window is explained only if it has a class none of whose bounds is unexplained (D46 review, M1).
const explained = cls => !!cls && !cls.split('; ').some(part => part === 'unexplained' || part.endsWith(': unexplained'));
// Rule 4's verdict: no changed result without an explained moved window, and no moved window unexplained.
function censusMet(windows, unexplainedResults) {
  const movedUnexplained = [];
  for (const [rule, per] of Object.entries(windows))
    for (const [f, w] of Object.entries(per))
      for (const [pid, x] of Object.entries(w.patients)) if (!explained(x.class)) movedUnexplained.push({rule, free_text: f, patient_id: pid, class: x.class});
  return {met: unexplainedResults === 0 && movedUnexplained.length === 0, moved_unexplained: movedUnexplained};
}

function census(output) {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'd46-census-'));
  try {
    const cohortPath = SMOKE ? path.join(work, 'cohort-4601.json') : COHORT;
    if (SMOKE) generate(4601, cohortPath);
    const text = fs.readFileSync(cohortPath, 'utf8');
    const ctx = {main: pageContext(text, 'main'), mine: pageContext(text, 'this')};
    const pids = inContext(ctx.mine, '[...state.patients.keys()]');
    const rules = inContext(ctx.mine, 'PERIOD_RULES');
    assert.ok(same(rules, inContext(ctx.main, 'PERIOD_RULES')));
    // Every sweep definition's window is one of the seven rules.
    const sweepRules = inContext(ctx.mine, 'SWEEP_RULE_NAMES');
    for (const c of inContext(ctx.mine, 'sweepCases()')) assert.ok(sweepRules.some(k => same(rules[k], c.def.window)), c.name);
    // Windows, per rule and the two settings the store is sent (stated findings, coded only).
    const windows = {}, movedBy = {};
    for (const [name, w] of Object.entries(rules)) {
      windows[name] = {};
      for (const f of ['present', 'off']) {
        const a = inContext(ctx.main, `placeAll(PERIOD_RULES[${JSON.stringify(name)}], ${JSON.stringify(f)})`);
        const b = inContext(ctx.mine, `placeAll(PERIOD_RULES[${JSON.stringify(name)}], ${JSON.stringify(f)})`);
        const counts = {placed_both: 0, unplaced_both: 0, unchanged: 0, moved: 0, placed_only_on_main: 0, placed_only_here: 0};
        const classes = {}, shifts = [], patients = {};
        for (const pid of pids) {
          const x = a[pid], y = b[pid];
          if (x.reason && y.reason) { counts.unplaced_both += 1; continue; }
          if (!x.reason && !y.reason) counts.placed_both += 1;
          if (!x.reason && !y.reason && same(x, y)) { counts.unchanged += 1; continue; }
          const which = x.reason ? 'placed_only_here' : y.reason ? 'placed_only_on_main' : 'moved';
          counts[which] += 1;
          const bounds = {};
          for (const side of ['start', 'end']) {
            const bound = w[side];
            if (bound.kind !== 'event') continue;
            const am = inContext(ctx.main, `anchorOf(${JSON.stringify(pid)}, ${JSON.stringify(bound)}, ${JSON.stringify(f)})`);
            const at = inContext(ctx.mine, `anchorOf(${JSON.stringify(pid)}, ${JSON.stringify(bound)}, ${JSON.stringify(f)})`);
            const keysOf = a => a ? [...a.records, ...a.mentions] : [];
            const cls = classify(am, at, () => ({
              mainHereCount: inContext(ctx.mine, `holders(${JSON.stringify(pid)}, ${JSON.stringify(keysOf(am))})`),
              mineMainCount: inContext(ctx.main, `holders(${JSON.stringify(pid)}, ${JSON.stringify(keysOf(at))})`)}));
            if (cls !== 'same anchor') bounds[side] = {class: cls, main: am, here: at};
          }
          const cls = Object.entries(bounds).map(([s, v]) => `${s}: ${v.class}`).join('; ') || 'unexplained';
          classes[cls] = (classes[cls] || 0) + 1;
          const shift = which === 'moved' ? {start_days: (y.start - x.start) / 1440, end_days: (y.end - x.end) / 1440} : null;
          if (shift) shifts.push(shift.start_days !== 0 ? shift.start_days : shift.end_days);
          patients[pid] = {change: which, class: cls, main: x, here: y, shift, bounds};
        }
        shifts.sort((p, q) => p - q);
        windows[name][f] = {counts, classes, shift_days: shifts.length ? {n: shifts.length, min: shifts[0], median: shifts[Math.floor(shifts.length / 2)],
                                                                            max: shifts[shifts.length - 1]} : null, patients};
        movedBy[`${name}|${f}`] = patients;
      }
    }
    // Store results: the page templates (stated findings with no options; "coded only" with the parity test's options),
    // and every option-sweep definition the store answers, under main's windows and this tree's; rule 8's late windows.
    const ruleOf = w => Object.keys(rules).find(k => same(rules[k], w));
    const defs = [];
    const templates = inContext(ctx.mine, 'TEMPLATE_DEFS');
    for (const [name, def] of Object.entries(templates)) {
      defs.push({name: `template: ${name}, stated findings`, def, freeText: 'present', options: {}});
      const joint = name === 'tree' || name === 'graph';
      defs.push({name: `template: ${name}, coded only (${joint ? 'joint' : '1.3'})`, def, freeText: 'off',
                 options: joint ? {codedOnly: 'store', geneCodedOnly: true} : {codedOnly: 'store'}});
    }
    // D49: the sweep as D46 measured it, before "coded only" became the store's profile 1.3 by default: `codedOnly: null`
    // is the translator's pre-D49 default exactly (D37's refusal, D32's reading for derived-only definitions, D38's gene
    // refusal), so this census keeps recording D46's window moves on D46's requests. D49's own census covers the flip.
    for (const c of inContext(ctx.mine, 'sweepCases()')) defs.push({name: `sweep: ${c.name}`, def: c.def, freeText: c.freeText, options: PRE_FLIP});
    // The Blank template under each of the five added period rules (D46 review, S1), stated findings and coded only.
    for (const [rule, window] of Object.entries(inContext(ctx.mine, 'EXTENDED_RULES'))) {
      const def = {...templates.blank, window};
      defs.push({name: `blank, ${rule}, stated findings`, def, freeText: 'present', options: {}});
      defs.push({name: `blank, ${rule}, coded only (1.3)`, def, freeText: 'off', options: {codedOnly: 'store'}});
    }
    const cases = [], refused = {}, eventAnchored = new Set();
    const tr = (which, d, late) => inContext(ctx[which], `translateOne(${JSON.stringify(d.def)}, ${JSON.stringify(d.freeText)}, ${JSON.stringify(d.options)}, ${late})`);
    for (const d of defs) {
      const a = tr('main', d, false), b = tr('mine', d, false);
      if (a.refused || b.refused) { assert.equal(a.refused, b.refused, `${d.name}: refused differently`); refused[d.name] = a.refused; continue; }
      assert.ok(same(a.query, b.query) && same(a.conditions, b.conditions), `${d.name}: only the windows may differ`);
      cases.push({name: d.name + ' @main', query: a.query, conditions: a.conditions, windows: a.windows});
      cases.push({name: d.name + ' @here', query: b.query, conditions: b.conditions, windows: b.windows});
      if (d.def.window.start.kind === 'event' || d.def.window.end.kind === 'event') {
        eventAnchored.add(d.name);
        for (const which of ['main', 'mine']) {
          const l = tr(which, d, true);
          cases.push({name: `${d.name} @late-${which === 'main' ? 'main' : 'here'}`, query: l.query, conditions: l.conditions, windows: l.windows});
        }
      }
    }
    const snapshot = run(['demos/mockups/store_parity.py', 'build', cohortPath, path.join(work, 'store')]).trim();
    // What the store refuses by name (a rise by a factor of 1, a relaxation that narrows) it refuses under any window.
    const swept = JSON.parse(run(['demos/mockups/store_parity.py', 'sweep', snapshot], JSON.stringify({cohort: cohortPath, cases})));
    for (const d of defs) {
      if (refused[d.name]) continue;
      const a = swept[d.name + ' @main'], b = swept[d.name + ' @here'];
      assert.ok(a.error !== 'UNNAMED' && b.error !== 'UNNAMED', `${d.name}: a failure without a code`);
      assert.equal(a.error, b.error, `${d.name}: the store refuses it under one window set only`);
      if (a.error) refused[d.name] = `store: ${a.error}`;
    }
    const results = storeResults(cohortPath, snapshot, cases.filter(c => !refused[c.name.replace(/ @(main|here|late-main|late-here)$/, '')]));
    const changes = {}, byCase = {}, unexplained = [];
    let compared = 0, changed = 0;
    const late = {here: {compared: 0, changed: 0, transitions: {}}, main: {compared: 0, changed: 0, transitions: {}}};
    for (const d of defs) {
      if (refused[d.name]) continue;
      const rows = results[d.name + ' @main'], rowsHere = results[d.name + ' @here'];
      const rule = ruleOf(d.def.window), moved = movedBy[`${rule}|${d.freeText}`];
      const mine = {changed: 0, by_class: {}};
      for (const pid of pids) {
        compared += 1;
        const x = rows[pid], y = rowsHere[pid];
        if (resultKey(x) === resultKey(y)) continue;
        changed += 1; mine.changed += 1;
        const cls = moved && moved[pid] ? moved[pid].class : null;
        if (!explained(cls)) { unexplained.push({case: d.name, patient_id: pid, main: x, here: y, window_class: cls}); continue; }
        const key = `${cls} | ${statusText(x)} -> ${statusText(y)}`;
        mine.by_class[key] = (mine.by_class[key] || 0) + 1;
        changes[key] = (changes[key] || 0) + 1;
      }
      if (mine.changed) byCase[d.name] = mine;
      if (eventAnchored.has(d.name)) for (const [tag, base] of [['here', rowsHere], ['main', rows]]) {
        const lr = results[`${d.name} @late-${tag}`];
        for (const pid of pids) {
          late[tag].compared += 1;
          if (resultKey(base[pid]) === resultKey(lr[pid])) continue;
          late[tag].changed += 1;
          const key = `${statusText(base[pid])} -> ${statusText(lr[pid])}`;
          late[tag].transitions[key] = (late[tag].transitions[key] || 0) + 1;
        }
      }
    }
    const report = {
      profile: 'd46-window-census-1.0',
      rule: ('D46 rule 4: on the page\'s cohort, each patient\'s window under main\'s page (grouped by truth) and this tree\'s '
             + '(rule reconcile_v1), for the seven period rules under stated findings and coded only, each moved window '
             + 'classified by the anchors that place it; the store\'s results for the page templates and every answered '
             + 'option-sweep definition under both windows, each changed result classified by its window\'s class and its '
             + 'transition. Met only if every changed result has a moved window (unexplained 0). Rule 8, reported: the '
             + 'store\'s results with every event anchor at the latest time its most precise record allows'),
      base: BASE, cohort_sha256: shaText(text), patients: pids.length,
      windows, results: {cases: defs.length - Object.keys(refused).length, refused, compared, changed, by_class: changes, by_case: byCase,
                         unexplained: unexplained.length, first_unexplained: unexplained.slice(0, 20)},
      view_cost: {rule: 'reported, not ruled (D46 rule 7): node, one process, after the census',
                  index_view_here: inContext(ctx.mine, 'viewCost(21)'), evidence_view_on_main: inContext(ctx.main, 'viewCost(21)')},
      uncertainty: {rule: 'reported, not ruled (D46 rule 8)', cases: eventAnchored.size, this_tree: late.here, main: late.main},
      ...(() => { const v = censusMet(windows, unexplained.length); return {moved_windows_unexplained: v.moved_unexplained, met: v.met}; })(),
      artifacts: artifacts(), contains_patient_level_data: false,
      synthetic_patient_ids: 'generator ids only (as the parity test pins them)', ...(SMOKE ? {smoke: true} : {}),
    };
    write(output, report);
    console.error(`census: ${report.met ? 'met' : 'MISSED'}; ${report.moved_windows_unexplained.length} moved windows unexplained; ${changed} of ${compared} results changed, ${unexplained.length} unexplained; late anchors change ${late.here.changed} of ${late.here.compared} here, ${late.main.changed} of ${late.main.compared} on main`);
  } finally {
    fs.rmSync(work, {recursive: true, force: true});
  }
}

/* Rule 5: usefulness, the owner's counting rule (D45's), over fresh cohorts. */
const SEEDS = Array.from({length: 30}, (_, i) => 4621 + i);
const EXTEND = Array.from({length: 50}, (_, i) => 4651 + i);
const MINIMUM = 30, SHARE = 0.3;
const RULED = ['sequence, stated findings', 'tree, stated findings', 'graph, stated findings', 'blank, stated findings',
               'sequence, coded only (1.3)', 'event frequency, coded only (1.3)', 'blank, coded only (1.3)',
               'tree, coded only (joint)', 'graph, coded only (joint)'];
const EXPECTED_REFUSALS = {'event frequency, stated findings': 'FREE_TEXT_COUNT_UNSUPPORTED'};
const planKeys = seed => JSON.parse(run(['-c', 'import json, sys; sys.path.insert(0, "demos/mockups/synthetic"); '
  + 'import generate as g; print(json.dumps({c.pid: c.key for c in g.build(int(sys.argv[1]))}))', String(seed)]));

function tally(results, name) {
  const candidates = results[name + '#with_candidate'];
  const statuses = {}, reasons = {};
  let unresolved = 0;
  for (const pid of candidates) {
    const r = results[name][pid];
    statuses[r.status] = (statuses[r.status] || 0) + 1;
    if (r.status === 'unresolved') { unresolved += 1; reasons[r.reason] = (reasons[r.reason] || 0) + 1; }
  }
  return {candidates: candidates.length, unresolved, statuses, reasons, candidate_ids: candidates,
          unresolved_ids: candidates.filter(pid => results[name][pid].status === 'unresolved')};
}

function usefulness(output) {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'd46-usefulness-'));
  const perSeed = {}, used = [];
  let refusals = null;
  try {
    const measure = seed => {
      const cohortPath = path.join(work, `cohort-${seed}.json`);
      generate(seed, cohortPath);
      const text = fs.readFileSync(cohortPath, 'utf8');
      const ctx = {main: pageContext(text, 'main'), here: pageContext(text, 'this')};
      const templates = inContext(ctx.here, 'TEMPLATE_DEFS');
      const cases = [], r = {};
      for (const [t, def] of Object.entries(templates)) {
        const joint = t === 'tree' || t === 'graph';
        for (const [setting, freeText, options] of [['stated findings', 'present', {}],
          [`coded only (${joint ? 'joint' : '1.3'})`, 'off', joint ? {codedOnly: 'store', geneCodedOnly: true} : {codedOnly: 'store'}]]) {
          const name = `${t}, ${setting}`;
          for (const which of ['here', 'main']) {
            const q = inContext(ctx[which], `translateOne(${JSON.stringify(def)}, ${JSON.stringify(freeText)}, ${JSON.stringify(options)}, false)`);
            if (q.refused) { r[name] = q.refused; continue; }
            cases.push({name: `${name} @${which}`, query: q.query, conditions: q.conditions, windows: q.windows});
          }
        }
      }
      refusals = refusals || r;
      assert.deepEqual(r, refusals, `seed ${seed}: the refusals differ from the first seed's`);
      const snapshot = run(['demos/mockups/store_parity.py', 'build', cohortPath, path.join(work, `store-${seed}`)]).trim();
      const results = storeResults(cohortPath, snapshot, cases);
      fs.rmSync(path.join(work, `store-${seed}`), {recursive: true, force: true});
      perSeed[seed] = {children: JSON.parse(text).patients.length, keys: planKeys(seed), cohort_sha256: shaText(text),
                       templates: Object.fromEntries(cases.map(c => [c.name, tally(results, c.name)]))};
      used.push(seed);
      console.error(`seed ${seed}: ` + RULED.map(n => `${n} ${perSeed[seed].templates[n + ' @here'].unresolved}/${perSeed[seed].templates[n + ' @here'].candidates}`).join('; '));
    };
    for (const seed of SMOKE ? [4601] : SEEDS) measure(seed);
    const pooledCandidates = n => used.reduce((k, s) => k + perSeed[s].templates[n + ' @here'].candidates, 0);
    for (const seed of SMOKE ? [] : EXTEND) { if (!RULED.some(n => pooledCandidates(n) < MINIMUM)) break; measure(seed); }
    const names = Object.keys(perSeed[used[0]].templates);
    const pooled = {}, readings = {}, byKey = {};
    for (const name of names) {
      const p = pooled[name] = {candidates: 0, unresolved: 0, statuses: {}, reasons: {}};
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
      const entries = Object.entries(k), noRef = entries.filter(([key]) => key !== 'reference');
      const sum = (es, f) => es.reduce((a, [, e]) => a + f(e), 0);
      readings[name] = {
        distinct_child_types: {types: entries.length, types_unresolved: entries.filter(([, e]) => e.unresolved > 0).length},
        all_candidates: {candidates: sum(entries, e => e.candidates), unresolved: sum(entries, e => e.unresolved)},
        without_C001: {candidates: sum(noRef, e => e.candidates), unresolved: sum(noRef, e => e.unresolved)},
      };
    }
    const bar = Object.fromEntries(RULED.map(n => {
      const d = readings[n + ' @here'].distinct_child_types, c = pooled[n + ' @here'].candidates;
      return [n, {types: d.types, types_unresolved: d.types_unresolved, candidates: c, unresolved: pooled[n + ' @here'].unresolved,
                  enough_candidates: c >= MINIMUM, met: c >= MINIMUM && d.types > 0 && d.types_unresolved <= SHARE * d.types}];
    }));
    const coverage = {refusals, expected: EXPECTED_REFUSALS, met: same(refusals, EXPECTED_REFUSALS)};
    write(output, {
      profile: 'd46-window-usefulness-1.0',
      rule: ('D46 rule 5 (the owner\'s counting rule, D45\'s): per page template the store answers, under stated findings '
             + '(no options, the page\'s default) and under "coded only" (the parity test\'s options: profile 1.3, the joint '
             + 'profile for Tree and Graph), with this tree\'s windows, pooled over the declared seeds: at least 30 patients '
             + 'with a store candidate and an index window, and at most 30% of the distinct child types (PLAN keys; C001 is '
             + 'reference) with a candidate unresolved. All candidates and the count without C001 reported; main\'s windows '
             + '(grouped by truth) on the same seeds reported beside (@main)'),
      declared_seeds: SEEDS, extension_seeds: EXTEND, seeds_used: used,
      setting: {window: 'from the first seizure, 24 months (the page\'s own)', scope: 'from_start',
                translator: 'trajectory/store_request.js; coded only with the explicit options above (the defaults since D49), stated findings with none',
                store: 'store_parity.py build and match (store_conditions.match)'},
      bar, coverage, met: RULED.every(n => bar[n].met) && coverage.met,
      templates: pooled, shares_three_ways: readings, by_plan_key: byKey,
      independence: ('not independent children (D36 review, M1): C001 is the scripted reference child in every seed, and '
                     + 'every other child takes archetype, sex, birth year and overrides from the generator\'s fixed PLAN'),
      per_seed: Object.fromEntries(used.map(s => [s, {children: perSeed[s].children, cohort_sha256: perSeed[s].cohort_sha256,
        templates: Object.fromEntries(names.map(n => [n, Object.fromEntries(Object.entries(perSeed[s].templates[n]).filter(([k]) => !k.endsWith('_ids')))]))}])),
      artifacts: artifacts(), contains_patient_level_data: false, ...(SMOKE ? {smoke: true} : {}),
    });
    console.error('bar: ' + RULED.map(n => `${n} ${bar[n].types_unresolved}/${bar[n].types} types, ${bar[n].unresolved}/${bar[n].candidates} (${bar[n].met ? 'met' : 'missed'})`).join('; ')
                  + `; coverage ${coverage.met ? 'met' : 'missed'}`);
  } finally {
    fs.rmSync(work, {recursive: true, force: true});
  }
}

module.exports = {DEFECTS, RULED_DEFECTS, REFERENCE_SEEDS, SEEDS, EXTEND, RULED, windowsOf, stripped, perturbed, classify, explained, censusMet};

if (require.main === module) {
  const what = process.argv[2], output = arg('--output', null);
  if (what === 'windows') {
    const w = windowsOf(fs.readFileSync(arg('--cohort', COHORT), 'utf8'), arg('--defect', null));
    write(output, w);
  } else if (what === 'reference') reference(output);
  else if (what === 'census') census(output);
  else if (what === 'usefulness') usefulness(output);
  else { console.error('usage: node demos/mockups/d46_windows.cjs windows|reference|census|usefulness [--output FILE]'); process.exit(2); }
}
