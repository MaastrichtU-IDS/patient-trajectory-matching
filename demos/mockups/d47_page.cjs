/* D47: the trajectory page's whole view without the generator's truth. The page side of D47's rules (decision log, D47).

   Since D47 the page's view (shared/core.js, `evidenceFor`) groups records by rule reconcile_v1 (`reconcile`, D46's rule,
   until then used for index periods only) instead of by `truth_event_id`, and attaches data-quality cases per record.
   The store's matching is unchanged, and so are the index periods (they were already placed over reconcile_v1's items).
   What moves is the page's own search (its fallback, and what the store does not answer, such as hedged findings), its
   timeline and its inspector. This tool loads the page's own code (shared/core.js, trajectory/app.js,
   trajectory/store_request.js) into node:vm contexts and compares this tree with main's page (core.js and app.js read
   from git at --base, default a4d2010).

     node demos/mockups/d47_page.cjs view --cohort FILE --output FILE [--defect NAME]
       The page's view of every patient in a canonical form (the input of index_reference.py compare-view).
     node demos/mockups/d47_page.cjs reference [--output REPORT.json]
       Rules 2 and 3: the view against index_reference.py on the page's cohort and seeds 4711-4720; the same view with the
       truth removed, the records shuffled, the record ids permuted; the planted defects.
     node demos/mockups/d47_page.cjs census [--output REPORT.json]
       Rule 4: on the page's cohort, the page's own search under main's view and this tree's, every changed result
       classified by the regrouping that explains it; the windows and the store requests the same on both sides.
     node demos/mockups/d47_page.cjs usefulness [--output REPORT.json]
       Rule 5: the page's own search over fresh cohorts (seeds 4721-4750, extended in order to 4800), the owner's rule.

   Synthetic data only (generator patient ids, as the parity test pins them). Needs git for main's page. */
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
const run = (args, input, opts = {}) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'd47-stdin-'));
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
const BASE = arg('--base', 'a4d2010');
// A smoke run (D47_SMOKE=1): one undeclared seed (4601, D46's probe seed), never a ruled one, never into verification/.
const SMOKE = process.env.D47_SMOKE === '1';

// Planted defects (each substitution must occur exactly once in core.js).
const DEFECTS = {
  // Reading the truth, three ways: a coded record joins its truth event's happening first; a mention joins the happening
  // that holds a record of its truth event; a data-quality case attaches through its truth event too.
  truth: ['const h = nearest(coded.filter(k => compatible(k.concept, c) && meets(k.head, range)), range);',
          'const h = coded.find(k => r.truth_event_id && k.recs[0].truth_event_id === r.truth_event_id) || nearest(coded.filter(k => compatible(k.concept, c) && meets(k.head, range)), range);'],
  mention_truth: ['    const h = nearest(targets.filter(k => compatible(k.concept, c) && meets(k.range, y.range)), y.range);',
                  '    const h = targets.find(k => y.x.m.truth_event_id && k.recs.some(r => r.truth_event_id === y.x.m.truth_event_id)) || nearest(targets.filter(k => compatible(k.concept, c) && meets(k.range, y.range)), y.range);'],
  case_truth: ['  const casesOf = recs => uniq(recs.flatMap(r => byRecord.get(r.record_id) || []));',
               '  const casesOf = recs => uniq([...recs.flatMap(r => byRecord.get(r.record_id) || []), ...p.quality.filter(q => q.truth_event_id && recs.some(r => r.truth_event_id === q.truth_event_id))]);'],
  // Departures from the rule, which the reference must catch (D46's ruled defects).
  tau30: ['const meets = (a, b) => a.e <= b.l && a.l >= b.e;', 'const meets = (a, b) => a.e <= b.l + 30 && a.l >= b.e - 30;'],
  reach400: ['(k.lastDisp.attributes?.supply_days || 30) + 45 : Infinity', '(k.lastDisp.attributes?.supply_days || 30) + 400 : Infinity'],
  stop_ignored: ['} else if (open) { open.recs.push(r); open = null; }', '} else if (open) { open.recs.push(r); }'],
  no_precision: ['findings.sort((a, b) => PRECISION[a.occurred.precision] - PRECISION[b.occurred.precision] || ', 'findings.sort((a, b) => '],
  // A departure in the view itself: the provisional split forgotten (every hedged mention stays with its happening).
  // The records the rule could not place, dropped from the view (D47 review, M1): caught by the completeness check.
  unplaced_dropped: ['  const unplaced = p.records.filter(r => !held.has(r.record_id))', '  const unplaced = [].filter(r => !held.has(r.record_id))'],
  no_provisional: ["const early = firstCoded == null ? [] : ms.filter(x => x.m.assertion === 'possible' && day(x.note.occurred.earliest) < firstCoded);\n    if (early.length) {\n      const provisional = makeItem(p, `${id}:provisional`",
                   "const early = [];\n    if (early.length) {\n      const provisional = makeItem(p, `${id}:provisional`"],
};
const TRUTH_DEFECTS = ['truth', 'mention_truth', 'case_truth'];  // caught by the truth-free check
const COMPLETENESS_DEFECTS = ['unplaced_dropped'];  // caught by the completeness check
// no_provisional is reported only: it moved nothing on the development cohorts (seeds 4601, 990011, 990012). Under
// reconcile_v1 a hedged mention joins a happening only if its wording interval meets the happening's records, and almost
// every interval ends at its letter, so a mention written before the first coded record seldom joins it: it stands as
// its own mention-only, hedged-only event, which is what the split makes of it.
const RULED_DEFECTS = Object.keys(DEFECTS).filter(k => k !== 'no_provisional');
const CORE = path.join(here, 'shared/core.js'), APP = path.join(here, 'trajectory/app.js');
function coreText(defect) {
  const text = fs.readFileSync(CORE, 'utf8');
  if (!defect) return text;
  const [from, to] = DEFECTS[defect];
  assert.equal(text.split(from).length - 1, 1, `defect ${defect}: its text must occur once in core.js`);
  return text.replace(from, to);
}
const baseText = file => run(['show', `${BASE}:${path.relative(root, file)}`], undefined, {command: 'git'});

const DEFINITIONS = `
  var pageWindow = defaultDef().window;
  var shape = t => ({scope: 'from_start', window: clone(pageWindow), nodes: clone(t.nodes), edges: clone(t.edges)});
  var TEMPLATE_DEFS = {'sequence': shape(TEMPLATES.sequence), 'tree': shape(TEMPLATES.tree), 'graph': shape(TEMPLATES.graph),
                       'event frequency': shape(TEMPLATES.frequency), 'blank': shape(BLANK)};
  var mentionKey = x => x.note.record_id + '#' + x.note.mentions.indexOf(x.m);
  var sigOf = i => [...i.recs.map(r => r.record_id), ...i.mentions.map(mentionKey)].sort();
  var g = d => d == null ? null : Number.isFinite(d) ? toGrid(d) : (d < 0 ? '-inf' : 'inf');
  // The page's view of one patient, canonical (D47 rule 3; index_reference.py's Patient.view).
  var viewOf = pid => {
    const v = evidenceFor(pid), ids = list => list.map(q => q.case_id).sort();
    const items = v.items.map(i => ({records: i.recs.map(r => r.record_id).sort(), mentions: i.mentions.map(mentionKey).sort(),
      kind: i.kind, concept: i.concept, text_concept: i.kind === 'genetic' || i.kind === 'medication' ? null : (i.textConcept || null),
      provisional: !!i.provisional, free_text_only: i.freeTextOnly, possible_only: i.possibleOnly,
      best: i.kind === 'genetic' || i.kind === 'medication' || !i.time.best ? null : [g(i.time.best.e), g(i.time.best.l)],
      start: i.kind === 'medication' && i.med.start ? [g(i.med.start.e), g(i.med.start.l)] : null,
      history: i.kind === 'genetic' ? i.gene.history.map(h => g(h.recorded)) : null, cases: ids(i.cases)}));
    items.sort((a, b) => { const x = JSON.stringify(sortKeys(a)), y = JSON.stringify(sortKeys(b)); return x < y ? -1 : x > y ? 1 : 0; });
    return {items, series: Object.fromEntries(Object.entries(v.series).map(([k, s]) => [k, s.map(x => x.record.record_id).sort()])),
            letters: Object.fromEntries(v.letters.map(l => [l.record.record_id, ids(l.cases)])),
            unplaced: Object.fromEntries(v.unplaced.map(u => [u.record.record_id, ids(u.cases)])), unattached: ids(v.unattached)};
  };
  // Rule 2b (D47 review, M1): every record is held by exactly one of an item, a series reading, a letter or the list of
  // records the rule could not place.
  var completeness = () => {
    let records = 0, unheld = 0, multiple = 0;
    for (const pid of state.patients.keys()) {
      const v = evidenceFor(pid), count = new Map();
      const hold = r => count.set(r.record_id, (count.get(r.record_id) || 0) + 1);
      v.items.forEach(i => i.recs.forEach(hold));
      Object.values(v.series).flat().forEach(s => hold(s.record));
      v.letters.forEach(l => hold(l.record));
      v.unplaced.forEach(u => hold(u.record));
      for (const r of v.p.records) { records += 1; const n = count.get(r.record_id) || 0; if (n === 0) unheld += 1; if (n > 1) multiple += 1; }
    }
    return {records, held_by_none: unheld, held_by_several: multiple, met: unheld === 0 && multiple === 0};
  };
  var sortKeys = o => Array.isArray(o) ? o.map(sortKeys) : o && typeof o === 'object' ? Object.fromEntries(Object.keys(o).sort().map(k => [k, sortKeys(o[k])])) : o;
  var viewAll = () => Object.fromEntries([...state.patients.keys()].map(pid => [pid, viewOf(pid)]));
  // The page's own search for one definition (matchPatient, as the page runs it in the browser).
  var resultKey = r => ({status: r.status, certainty: r.certainty || null, relaxations: r.relaxKeys ? [...r.relaxKeys].sort() : [],
                         cost: r.cost || 0});
  var ownResults = (def, freeText) => {
    const before = state.freeText;
    state.freeText = freeText;
    try {
      const d = {...defaultDef(), ...def}, a = analyse(d);
      return Object.fromEntries([...state.patients.keys()].map(pid => [pid, resultKey(matchPatient(pid, d, a))]));
    } finally { state.freeText = before; }
  };
  var windowsOf = (def, freeText) => {
    const before = state.freeText;
    state.freeText = freeText;
    try {
      return Object.fromEntries([...state.patients.keys()].map(pid => {
        const w = windowFor(evidenceFor(pid), {freeText, knownByIndex: false, window: def.window});
        return [pid, w.reason ? {reason: w.reason} : {start: toGrid(w.start), end: toGrid(w.end)}];
      }));
    } finally { state.freeText = before; }
  };
  // Each step's candidates, and each relaxed alternative's, as the page's search resolves them, with what holds them.
  var stepCands = (pid, def, freeText) => {
    const before = state.freeText;
    state.freeText = freeText;
    try {
      const d = {...defaultDef(), ...def}, view = evidenceFor(pid), byId = nodeById(d);
      const win = windowFor(view, {freeText, knownByIndex: false, window: d.window});
      if (win.reason) return null;
      const out = {};
      const list = cs => cs.map(c => ({t: [g(c.e), g(c.l), g(c.point ? c.e : c.fe), g(c.point ? c.l : c.fl)],
        sig: c.item ? sigOf(c.item) : ['measure:' + (c.record_id || (c.record && c.record.record_id) || '?')]}));
      const add = (key, n, keys) => {
        out[key] = list(candidates(view, n, keys, win));
        // Before the occurrence or the count picks one (first, last, Nth, the count reached): every event of the step.
        if (n.occ && n.occ !== 'any') out['all ' + key] = list(candidates(view, {...n, occ: 'any'}, keys, win));
      };
      // Every set of a step's own relaxations the search can try (a parent concept, one fewer, both).
      const own = relaxOptions(d).filter(o => o.key.startsWith('node:') || o.key.startsWith('count:'));
      for (const n of d.nodes) {
        const mine = own.filter(o => o.key.endsWith(':' + n.id)).map(o => o.key);
        const sets = [[]];
        for (const k of mine) for (const s0 of [...sets]) sets.push([...s0, k]);
        for (const set of sets) add(set.length ? set.join('+') : 'step:' + n.id, n, new Set(set));
      }
      return out;
    } finally { state.freeText = before; }
  };
  // The view's items that hold any of these records or mentions: their own records and mentions.
  var holders = (pid, keys) => {
    const want = new Set(keys);
    return evidenceFor(pid).items.filter(i => sigOf(i).some(k => want.has(k))).map(sigOf);
  };
  // A series as the page reads it: each reading's record, time and value.
  var seriesOf = (pid, concept) => (evidenceFor(pid).series[concept] || []).map(x => [x.record.record_id, g(x.day), g(x.end), x.value]);
  var translateOne = (def, freeText, options) => {
    const before = state.freeText;
    state.freeText = freeText;
    // D52: D49's translation pinned (the page point windows, indexAnchor false), so this report keeps recording what it measured.
    try { return storeRequest({...defaultDef(), ...def}, freeText, [...state.patients.keys()], {indexAnchor: false, ...options}); }
    catch (e) { if (e instanceof StoreRefusal) return {refused: e.code}; throw e; }
    finally { state.freeText = before; }
  };
  var sweepCases = () => optionSweepDefinitions().map(({name, def, freeText}) => ({name, def, freeText}));
  // Rule 2c (D47 review, S1): the page's own search for the templates and the first \`limit\` sweep definitions (all
  // when null), each under the three settings.
  var ownAll = limit => {
    const out = {};
    const defs = [...Object.entries(TEMPLATE_DEFS).map(([n, def]) => ({name: 'template: ' + n, def})),
                  ...sweepCases().slice(0, limit == null ? undefined : limit).map(c => ({name: 'sweep: ' + c.name, def: c.def}))];
    for (const {name, def} of defs) for (const f of ['present', 'off', 'possible']) out[name + ' | ' + f] = ownResults(def, f);
    return out;
  };
  // Rule 7 (reported): building every patient's view from a cold cache.
  var viewCost = repeats => {
    const runs = [];
    for (let k = 0; k < repeats; k++) {
      const t0 = performance.now();
      for (const pid of state.patients.keys()) { state.evidence.delete(pid); if (state.indexViews) state.indexViews.delete(pid); evidenceFor(pid); }
      runs.push(performance.now() - t0);
    }
    runs.sort((a, b) => a - b);
    return {repeats, median_ms_all_patients: runs[Math.floor(repeats / 2)], patients: state.patients.size};
  };
`;

function pageContext(cohortText, which, defect) {
  const stub = {addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; }};
  const context = vm.createContext({document: stub, addEventListener() {}, console, structuredClone, performance, DATA: cohortText,
                                    POST: () => { throw new Error('D47 never calls the page engine'); }});
  const core = which === 'main' ? baseText(CORE) : coreText(defect);
  const app = (which === 'main' ? baseText(APP) : fs.readFileSync(APP, 'utf8')).replace(/\nmain\(\);\s*$/, '\n');
  vm.runInContext(core + '\n' + app + '\n' + fs.readFileSync(path.join(here, 'trajectory/store_request.js'), 'utf8') + `
    state.data = JSON.parse(DATA); state.concepts = state.data.concepts;
    for (const [cid, c] of Object.entries(state.concepts)) for (const coding of c.codings) state.codeIndex.set(coding.system + '|' + coding.code, cid);
    for (const p of state.data.patients) state.patients.set(p.patient_id, p);
    state.freeText = 'present';
  ` + DEFINITIONS, context);
  vm.runInContext(fs.readFileSync(path.join(here, 'option_sweep.js'), 'utf8'), context);
  assert.equal(vm.runInContext('typeof reconcile', context), which === 'main' ? 'undefined' : 'function',
               `${which}: the page's reconcile is ${which === 'main' ? 'not ' : ''}expected`);
  return context;
}
const inContext = (context, code) => JSON.parse(vm.runInContext(`JSON.stringify(${code})`, context));

const ARTIFACTS = ['demos/mockups/d47_page.cjs', 'demos/mockups/index_reference.py', 'demos/mockups/option_sweep.js',
  'demos/mockups/trajectory/store_request.js', 'demos/mockups/trajectory/app.js', 'demos/mockups/shared/core.js',
  'demos/mockups/synthetic/generate.py'];
const artifacts = () => Object.fromEntries(ARTIFACTS.map(f => [f, sha(f)]));
const write = (output, report) => {
  if (SMOKE && output && path.resolve(output).startsWith(path.join(root, 'verification'))) throw new Error('a smoke run is never written into verification/');
  const text = JSON.stringify(report, null, 1) + '\n';
  if (output) fs.writeFileSync(output, text); else process.stdout.write(text);
};
const generate = (seed, file) => run(['demos/mockups/synthetic/generate.py', '--seed', String(seed), '--output', file]);

/* The view with the truth removed (D47 rule 2): no `truth`, no `truth_event_id` on records, mentions or data-quality
   cases. The cases themselves stay: the page shows them, attached per record. */
function stripped(text) {
  const d = JSON.parse(text);
  for (const p of d.patients) {
    delete p.truth;
    for (const q of p.quality) delete q.truth_event_id;
    for (const r of p.records) { delete r.truth_event_id; for (const m of r.mentions || []) delete m.truth_event_id; }
  }
  return JSON.stringify(d);
}
/* D46's perturbations on the stripped cohort: records shuffled; record ids permuted (in records, mention keys and
   cases alike), with the map back so the views compare under the original ids. */
function perturbed(text) {
  let seed = 12345;
  const rand = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
  const shuffle = a => { for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(rand() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };
  const shuffled = JSON.parse(text);
  for (const p of shuffled.patients) shuffle(p.records);
  const renamed = JSON.parse(text), back = {};
  for (const p of renamed.patients) {
    const ids = p.records.map(r => r.record_id), perm = shuffle([...ids]);
    const map = Object.fromEntries(ids.map((id, i) => [id, perm[i]]));
    for (const [a, b] of Object.entries(map)) back[b] = a;
    for (const r of p.records) r.record_id = map[r.record_id];
    for (const q of p.quality) q.record_ids = q.record_ids.map(id => map[id] || id);
  }
  return {records_shuffled: {text: JSON.stringify(shuffled), back: null}, record_ids_permuted: {text: JSON.stringify(renamed), back}};
}
/* A view under permuted ids, mapped back to the original ids. */
function mapBack(view, back) {
  if (!back) return view;
  const id = x => back[x] || x, key = k => { const [n, i] = k.split('#'); return `${id(n)}#${i}`; };
  const sortKeys = o => Array.isArray(o) ? o.map(sortKeys) : o && typeof o === 'object' ? Object.fromEntries(Object.keys(o).sort().map(k => [k, sortKeys(o[k])])) : o;
  const out = {};
  for (const [pid, v] of Object.entries(view)) {
    const items = v.items.map(i => ({...i, records: i.records.map(id).sort(), mentions: i.mentions.map(key).sort()}));
    items.sort((a, b) => { const x = JSON.stringify(sortKeys(a)), y = JSON.stringify(sortKeys(b)); return x < y ? -1 : x > y ? 1 : 0; });
    out[pid] = {items, series: Object.fromEntries(Object.entries(v.series).map(([k, s]) => [k, s.map(id).sort()])),
                letters: Object.fromEntries(Object.entries(v.letters).map(([k, c]) => [id(k), c])),
                unplaced: Object.fromEntries(Object.entries(v.unplaced).map(([k, c]) => [id(k), c])), unattached: v.unattached};
  }
  return out;
}
const viewsOf = (text, defect) => inContext(pageContext(text, 'this', defect), 'viewAll()');
function differingPatients(a, b) {
  return Object.keys(a).filter(pid => !same(sortAll(a[pid]), sortAll(b[pid]))).length;
}
const sortObj = o => Object.fromEntries(Object.keys(o).sort().map(k => [k, o[k]]));
const sortAll = v => ({items: v.items, series: v.series, letters: sortObj(v.letters), unplaced: sortObj(v.unplaced), unattached: v.unattached});

const REFERENCE_SEEDS = Array.from({length: 10}, (_, i) => 4711 + i);
function reference(output) {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'd47-reference-'));
  try {
    const cohorts = SMOKE ? [['seed 4601', 4601]] : [['the page\'s cohort', null], ...REFERENCE_SEEDS.map(s => [`seed ${s}`, s])];
    const rows = {}, defects = Object.fromEntries(Object.keys(DEFECTS).map(k => [k, TRUTH_DEFECTS.includes(k) ? {patients_moved_by_removing_truth: 0}
      : COMPLETENESS_DEFECTS.includes(k) ? {records_held_by_none: 0} : {mismatches: 0}]));
    const truthFree = {patients: 0, stripped: 0, records_shuffled: 0, record_ids_permuted: 0, own_search_results: 0, own_search_differing: 0};
    const complete = {records: 0, held_by_none: 0, held_by_several: 0};
    for (const [name, seed] of cohorts) {
      const cohortPath = seed == null ? COHORT : path.join(work, `cohort-${seed}.json`);
      if (seed != null) generate(seed, cohortPath);
      const text = fs.readFileSync(cohortPath, 'utf8');
      const compareWith = (v, tag) => {
        const file = path.join(work, `view-${seed}-${tag}.json`);
        fs.writeFileSync(file, JSON.stringify(v));
        let out;
        try { out = run(['demos/mockups/index_reference.py', 'compare-view', cohortPath, file]); }
        catch (e) { if (e.status !== 1) throw e; out = e.stdout; }
        return JSON.parse(out);
      };
      const v = viewsOf(text), r = compareWith(v, 'page');
      rows[name] = {cohort_sha256: shaText(text), patients: Object.keys(v).length, items: r.items, compared: r.compared,
                    mismatches: r.mismatches, first_mismatches: r.first_mismatches};
      const free = stripped(text);
      truthFree.patients += Object.keys(v).length;
      truthFree.stripped += differingPatients(v, viewsOf(free));
      for (const [k, {text: t, back}] of Object.entries(perturbed(free))) truthFree[k] += differingPatients(v, mapBack(viewsOf(t), back));
      // The page's own search on the cohort and on the stripped cohort: every template and sweep definition, three settings.
      const own = inContext(pageContext(text, 'this'), 'ownAll(null)'), ownFree = inContext(pageContext(free, 'this'), 'ownAll(null)');
      for (const k of Object.keys(own)) for (const pid of Object.keys(own[k])) {
        truthFree.own_search_results += 1;
        if (!same(own[k][pid], ownFree[k][pid])) truthFree.own_search_differing += 1;
      }
      const c = inContext(pageContext(text, 'this'), 'completeness()');
      for (const k of ['records', 'held_by_none', 'held_by_several']) complete[k] += c[k];
      for (const k of Object.keys(DEFECTS)) {
        if (TRUTH_DEFECTS.includes(k)) defects[k].patients_moved_by_removing_truth += differingPatients(viewsOf(text, k), viewsOf(free, k));
        else if (COMPLETENESS_DEFECTS.includes(k)) defects[k].records_held_by_none += inContext(pageContext(text, 'this', k), 'completeness()').held_by_none;
        else defects[k].mismatches += compareWith(viewsOf(text, k), k).mismatches;
      }
      console.error(`${name}: ${r.items} items, ${r.compared} parts compared, ${r.mismatches} mismatches`);
    }
    const mismatches = Object.values(rows).reduce((a, r) => a + r.mismatches, 0);
    for (const [k, d] of Object.entries(defects)) d.caught = (TRUTH_DEFECTS.includes(k) ? d.patients_moved_by_removing_truth
      : COMPLETENESS_DEFECTS.includes(k) ? d.records_held_by_none : d.mismatches) > 0;
    truthFree.met = truthFree.stripped === 0 && truthFree.records_shuffled === 0 && truthFree.record_ids_permuted === 0
      && truthFree.own_search_differing === 0 && truthFree.own_search_results > 0;
    complete.met = complete.held_by_none === 0 && complete.held_by_several === 0;
    write(output, {
      profile: 'd47-page-reference-1.0',
      rule: ('D47 rules 2 and 3: the page\'s whole view (items with their records, mentions, kind, concept, times and '
             + 'data-quality cases; series readings; letters; records the rule could not place; unattached cases) equals '
             + 'index_reference.py\'s on the page\'s cohort and seeds 4711-4720, and is the same with the truth removed (cases '
             + 'kept, their truth links removed), with the records shuffled and with the record ids permuted; the page\'s own '
             + 'search gives the same results on the stripped cohort (five templates and every sweep definition, three '
             + 'settings); every record is held exactly once; each ruled planted defect is caught'),
      declared_seeds: REFERENCE_SEEDS, cohorts: rows, mismatches, truth_free: truthFree, completeness: complete, defects,
      ruled_defects: RULED_DEFECTS,
      met: {reference: mismatches === 0, truth_free: truthFree.met, completeness: complete.met, defects: RULED_DEFECTS.every(k => defects[k].caught)},
      artifacts: artifacts(), contains_patient_level_data: false, ...(SMOKE ? {smoke: true} : {}),
    });
  } finally {
    fs.rmSync(work, {recursive: true, force: true});
  }
}

/* A changed result's class (D47 rule 4), from each step's candidates (and each set of its own relaxations) under main's
   view and this tree's: the regroupings that make them differ, each verified on the views, joined by '+'. 'unexplained'
   when the candidates are the same, or when a difference has no verified cause (D47 review, S2). For a candidate event
   on one side only, by what the other side's view does with its records and mentions (CLASSES). */
const CLASSES = {merged: 'an event here holds records or mentions of several of main\'s events',
  split: 'records or mentions of one of main\'s events are in several events here',
  regrouped: 'held by one event on each side, with another set of records or mentions',
  'mention-only': 'an event of letter mentions only, held by one event on the other side with another set',
  ungrouped: 'the other side has no event holding these records or mentions (a record it could not place; a mention it does not take)',
  series: 'a measurement step\'s readings differ, and so does the series it reads',
  reranked: 'the same event on both sides, picked (first, last, Nth, the count reached) on one only, the step\'s events before the pick differing',
  reordered: 'the same events, in another order or with other times'};
function classify(mainCands, hereCands, held, seriesSame = () => false) {
  // A window placed on one side only is ruled out by rule 1 (windows identical); were one to occur, it is unexplained.
  if (!mainCands || !hereCands) return 'unexplained';
  const types = new Set();
  const keys = [...new Set([...Object.keys(mainCands), ...Object.keys(hereCands)])];
  for (const key of keys) {
    const a = mainCands[key] || [], b = hereCands[key] || [];
    if (same(a, b)) continue;
    const sigA = new Set(a.map(c => JSON.stringify(c.sig))), sigB = new Set(b.map(c => JSON.stringify(c.sig)));
    const onlyA = a.filter(c => !sigB.has(JSON.stringify(c.sig))), onlyB = b.filter(c => !sigA.has(JSON.stringify(c.sig)));
    if (!onlyA.length && !onlyB.length) { types.add('reordered'); continue; }
    for (const [cands, other] of [[onlyB, 'main'], [onlyA, 'here']]) for (const c of cands) {
      if (c.sig[0].startsWith('measure:')) { types.add(seriesSame(key) ? 'unexplained' : 'series'); continue; }
      const hs = held(other, c.sig);
      if (hs.length > 1) types.add(other === 'main' ? 'merged' : 'split');
      else if (hs.length === 0) types.add('ungrouped');
      // The same event on the other side, not picked there: verified only if the step's events before the pick differ.
      else if (same([...hs[0]].sort(), [...c.sig].sort())) types.add(!key.startsWith('all ') && !same(mainCands['all ' + key], hereCands['all ' + key]) ? 'reranked' : 'unexplained');
      else if (!c.sig.some(k => !k.includes('#'))) types.add('mention-only');
      else types.add('regrouped');
    }
  }
  return !types.size || types.has('unexplained') ? 'unexplained' : [...types].sort().join(' + ');
}

function census(output) {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'd47-census-'));
  try {
    const cohortPath = SMOKE ? path.join(work, 'cohort-4601.json') : COHORT;
    if (SMOKE) generate(4601, cohortPath);
    const text = fs.readFileSync(cohortPath, 'utf8');
    const ctx = {main: pageContext(text, 'main'), here: pageContext(text, 'this')};
    const pids = inContext(ctx.here, '[...state.patients.keys()]');
    const defs = [];
    for (const [name, def] of Object.entries(inContext(ctx.here, 'TEMPLATE_DEFS')))
      for (const f of ['present', 'off', 'possible']) defs.push({name: `template: ${name}, ${f}`, def, freeText: f});
    for (const c of inContext(ctx.here, 'sweepCases()')) {
      defs.push({name: `sweep: ${c.name}`, def: c.def, freeText: c.freeText});
      if (c.freeText !== 'possible') defs.push({name: `sweep: ${c.name} / possible`, def: c.def, freeText: 'possible'});
    }
    const q = (which, code) => inContext(ctx[which], code);
    const J = JSON.stringify;
    let windowsDiffer = 0, requestsDiffer = 0, compared = 0, changed = 0;
    const byClass = {}, byTransition = {}, byCase = {}, unexplained = [];
    const t0 = Date.now();
    for (const d of defs) {
      // The windows and the store's requests: the same on both sides (only the page's own search may move).
      if (!same(q('main', `windowsOf(${J(d.def)}, ${J(d.freeText)})`), q('here', `windowsOf(${J(d.def)}, ${J(d.freeText)})`))) windowsDiffer += 1;
      for (const options of d.name.startsWith('template') && d.freeText === 'off' ? [{}, {codedOnly: 'store', geneCodedOnly: true}] : [{}])
        if (!same(q('main', `translateOne(${J(d.def)}, ${J(d.freeText)}, ${J(options)})`), q('here', `translateOne(${J(d.def)}, ${J(d.freeText)}, ${J(options)})`))) requestsDiffer += 1;
      const a = q('main', `ownResults(${J(d.def)}, ${J(d.freeText)})`), b = q('here', `ownResults(${J(d.def)}, ${J(d.freeText)})`);
      for (const pid of pids) {
        compared += 1;
        if (same(a[pid], b[pid])) continue;
        changed += 1;
        const mc = q('main', `stepCands(${J(pid)}, ${J(d.def)}, ${J(d.freeText)})`);
        const hc = q('here', `stepCands(${J(pid)}, ${J(d.def)}, ${J(d.freeText)})`);
        const conceptOfKey = key => { const id = key.split('+')[0].split(':')[1]; return (d.def.nodes.find(n => n.id === id) || {}).concept; };
        const cls = classify(mc, hc, (side, sig) => q(side, `holders(${J(pid)}, ${J(sig)})`),
                             key => same(q('main', `seriesOf(${J(pid)}, ${J(conceptOfKey(key))})`), q('here', `seriesOf(${J(pid)}, ${J(conceptOfKey(key))})`)));
        const st = r => r.certainty ? `${r.status}/${r.certainty}` : r.status;
        const tr = `${st(a[pid])} -> ${st(b[pid])}`;
        if (cls === 'unexplained') unexplained.push({case: d.name, patient_id: pid, main: a[pid], here: b[pid]});
        byClass[cls] = (byClass[cls] || 0) + 1;
        byTransition[tr] = (byTransition[tr] || 0) + 1;
        const c = byCase[d.name] = byCase[d.name] || {changed: 0, patients: {}};
        c.changed += 1; c.patients[pid] = {class: cls, transition: tr};
      }
    }
    const elapsed = (Date.now() - t0) / 1000;
    const perSetting = {};
    for (const [name, c] of Object.entries(byCase)) {
      const f = name.endsWith('/ possible') || name.endsWith(', possible') ? 'possible' : defs.find(d => d.name === name).freeText;
      perSetting[f] = (perSetting[f] || 0) + c.changed;
    }
    const report = {
      profile: 'd47-page-census-1.0',
      rule: ('D47 rule 4: on the page\'s cohort, the page\'s own search (matchPatient) under main\'s view (grouped by the '
             + 'generator\'s truth) and this tree\'s (reconcile_v1), for the five templates under stated findings, coded only '
             + 'and hedged findings, and every option-sweep definition under its own setting and under hedged findings. Each '
             + 'changed result is classified by the regroupings that make its step candidates differ, each verified on the two '
             + 'views. Met only if no changed result is unexplained, and the windows and the store requests are the same on '
             + 'both sides for every case'),
      base: BASE, cohort_sha256: shaText(text), patients: pids.length, cases: defs.length, compared, changed,
      changed_by_setting: perSetting, classes: CLASSES, by_class: byClass, by_transition: byTransition, by_case: byCase,
      windows_differing_cases: windowsDiffer, store_requests_differing: requestsDiffer,
      unexplained: unexplained.length, first_unexplained: unexplained.slice(0, 20), elapsed_s: elapsed,
      view_cost: {rule: 'reported, not ruled (D47 rule 7): node, one process, after the census',
                  here: q('here', 'viewCost(21)'), main: q('main', 'viewCost(21)')},
      met: unexplained.length === 0 && windowsDiffer === 0 && requestsDiffer === 0,
      artifacts: artifacts(), contains_patient_level_data: false,
      synthetic_patient_ids: 'generator ids only (as the parity test pins them)', ...(SMOKE ? {smoke: true} : {}),
    };
    write(output, report);
    console.error(`census: ${report.met ? 'met' : 'MISSED'}; ${changed} of ${compared} own-search results changed, ${unexplained.length} unexplained; windows differ in ${windowsDiffer} cases, store requests in ${requestsDiffer}; ${elapsed.toFixed(0)} s`);
  } finally {
    fs.rmSync(work, {recursive: true, force: true});
  }
}

/* Rule 5: the page's own search over fresh cohorts. A candidate is a patient with a window and at least one candidate for
   every step.
   - Ruled (D47 review, M2): for each template and setting, the share of the distinct child types with a candidate that
     have a decided candidate (exact or possible) must not fall by more than 5 percentage points below main's on the same
     seeds.
   - Reported: the owner's unresolved bar. The page's own search is unresolved only when its search budget runs out, which
     happens on neither side, so that bar cannot fail here. Also the share of child types with a candidate whose status
     moves to none or not comparable (here against main, patient by patient). */
const SEEDS = Array.from({length: 30}, (_, i) => 4721 + i);
const EXTEND = Array.from({length: 50}, (_, i) => 4751 + i);
const MINIMUM = 30, SHARE = 0.3, DROP = 0.05;
const SETTINGS = {present: 'stated findings', off: 'coded only', possible: 'hedged findings'};
const DECIDED = new Set(['exact', 'possible']), WORSE = new Set(['none', 'incomparable']);
const planKeys = seed => JSON.parse(run(['-c', 'import json, sys; sys.path.insert(0, "demos/mockups/synthetic"); '
  + 'import generate as g; print(json.dumps({c.pid: c.key for c in g.build(int(sys.argv[1]))}))', String(seed)]));

// Pooled readings of one template and setting over the seeds: per side, candidates, statuses, child types.
function pooledReadings(perSeed, used, name) {
  const side = which => {
    const types = {}, statuses = {};
    let candidates = 0, unresolved = 0, noRef = 0, noRefUnresolved = 0;
    for (const s of used) {
      const t = perSeed[s].templates[name][which];
      for (const [pid, st] of Object.entries(t.candidates)) {
        const k = perSeed[s].keys[pid], e = types[k] = types[k] || {candidates: 0, unresolved: 0, decided: 0};
        candidates += 1; e.candidates += 1; statuses[st] = (statuses[st] || 0) + 1;
        if (DECIDED.has(st)) e.decided += 1;
        if (st === 'unresolved') { unresolved += 1; e.unresolved += 1; }
        if (k !== 'reference') { noRef += 1; if (st === 'unresolved') noRefUnresolved += 1; }
      }
    }
    const tk = Object.values(types);
    return {candidates, unresolved, statuses, by_plan_key: types,
            distinct_child_types: {types: tk.length, types_unresolved: tk.filter(e => e.unresolved > 0).length, types_decided: tk.filter(e => e.decided > 0).length,
                                   share_decided: tk.length ? tk.filter(e => e.decided > 0).length / tk.length : null},
            without_C001: {candidates: noRef, unresolved: noRefUnresolved}};
  };
  const here = side('here'), main = side('main');
  // Child types whose status moves to none or not comparable, patient by patient (a candidate on either side).
  const all = new Set(), moved = new Set();
  for (const s of used) {
    const t = perSeed[s].templates[name];
    for (const pid of new Set([...Object.keys(t.here.candidates), ...Object.keys(t.main.candidates)])) {
      const k = perSeed[s].keys[pid];
      all.add(k);
      if (WORSE.has(t.here.status[pid]) && !WORSE.has(t.main.status[pid])) moved.add(k);
    }
  }
  return {here, main, moved_to_none_or_incomparable: {types: all.size, types_moved: moved.size, share: all.size ? moved.size / all.size : null}};
}

function usefulness(output) {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'd47-usefulness-'));
  const perSeed = {}, used = [];
  try {
    let names = null;
    const measure = seed => {
      const cohortPath = path.join(work, `cohort-${seed}.json`);
      generate(seed, cohortPath);
      const text = fs.readFileSync(cohortPath, 'utf8');
      const ctx = {main: pageContext(text, 'main'), here: pageContext(text, 'this')};
      const templates = inContext(ctx.here, 'TEMPLATE_DEFS'), out = {};
      for (const [t, def] of Object.entries(templates)) for (const [f, label] of Object.entries(SETTINGS)) {
        const entry = out[`${t}, ${label}`] = {};
        for (const which of ['here', 'main']) {
          const results = inContext(ctx[which], `ownResults(${JSON.stringify(def)}, ${JSON.stringify(f)})`);
          const cand = Object.keys(results).filter(pid => {
            const c = inContext(ctx[which], `stepCands(${JSON.stringify(pid)}, ${JSON.stringify(def)}, ${JSON.stringify(f)})`);
            return c && def.nodes.every(n => (c['step:' + n.id] || []).length > 0);
          });
          entry[which] = {candidates: Object.fromEntries(cand.map(pid => [pid, results[pid].status])),
                          status: Object.fromEntries(Object.entries(results).map(([pid, r]) => [pid, r.status]))};
        }
      }
      names = names || Object.keys(out);
      perSeed[seed] = {cohort_sha256: shaText(text), keys: planKeys(seed), templates: out};
      used.push(seed);
      console.error(`seed ${seed}: ` + names.map(n => `${n} ${Object.values(out[n].here.candidates).filter(x => DECIDED.has(x)).length}/${Object.keys(out[n].here.candidates).length}`).join('; '));
    };
    for (const seed of SMOKE ? [4601] : SEEDS) measure(seed);
    const pooled = n => used.reduce((k, s) => k + Object.keys(perSeed[s].templates[n].here.candidates).length, 0);
    for (const seed of SMOKE ? [] : EXTEND) { if (!names.some(n => pooled(n) < MINIMUM)) break; measure(seed); }
    const templates = Object.fromEntries(names.map(n => [n, pooledReadings(perSeed, used, n)]));
    const rule = Object.fromEntries(names.map(n => {
      const h = templates[n].here.distinct_child_types, m = templates[n].main.distinct_child_types;
      return [n, {types: h.types, types_decided: h.types_decided, share_decided: h.share_decided,
                  main_types: m.types, main_types_decided: m.types_decided, main_share_decided: m.share_decided,
                  candidates: templates[n].here.candidates, enough_candidates: templates[n].here.candidates >= MINIMUM,
                  met: templates[n].here.candidates >= MINIMUM && h.share_decided != null && m.share_decided != null && h.share_decided >= m.share_decided - DROP}];
    }));
    const ownerBar = Object.fromEntries(names.map(n => {
      const t = templates[n].here, d = t.distinct_child_types;
      return [n, {types: d.types, types_unresolved: d.types_unresolved, candidates: t.candidates, unresolved: t.unresolved,
                  met: t.candidates >= MINIMUM && d.types > 0 && d.types_unresolved <= SHARE * d.types}];
    }));
    write(output, {
      profile: 'd47-page-usefulness-1.1',
      rule: ('D47 rule 5, as amended after the review (M2): the page\'s own search (matchPatient) for each of the five templates '
             + 'under stated findings, coded only and hedged findings, pooled over the declared seeds, with at least 30 candidates '
             + '(patients with a window and a candidate for every step). Ruled: the share of the distinct child types (PLAN keys; '
             + 'C001 is reference) with a candidate that have a decided candidate (exact or possible) is at least main\'s on the '
             + 'same seeds less 5 percentage points. Reported: the owner\'s unresolved bar (it cannot fail for the page\'s own '
             + 'search, whose only unresolved is an exhausted search budget), all candidates and the count without C001, and the '
             + 'share of child types whose status moves to none or not comparable'),
      declared_seeds: SEEDS, extension_seeds: EXTEND, seeds_used: used, rule_decided: rule, owner_bar_reported: ownerBar,
      met: Object.values(rule).every(b => b.met), templates,
      per_seed: Object.fromEntries(used.map(s => [s, {cohort_sha256: perSeed[s].cohort_sha256, templates: Object.fromEntries(
        Object.entries(perSeed[s].templates).map(([n, t]) => [n, Object.fromEntries(['here', 'main'].map(w => {
          const st = {};
          for (const x of Object.values(t[w].candidates)) st[x] = (st[x] || 0) + 1;
          return [w, {candidates: Object.keys(t[w].candidates).length, statuses: st}];
        }))]))}])),
      artifacts: artifacts(), contains_patient_level_data: false, ...(SMOKE ? {smoke: true} : {}),
    });
    console.error('rule: ' + Object.entries(rule).map(([n, b]) => `${n} ${b.types_decided}/${b.types} against main ${b.main_types_decided}/${b.main_types} (${b.met ? 'met' : 'missed'})`).join('; '));
  } finally {
    fs.rmSync(work, {recursive: true, force: true});
  }
}

module.exports = {DEFECTS, RULED_DEFECTS, TRUTH_DEFECTS, COMPLETENESS_DEFECTS, pageContext, inContext, REFERENCE_SEEDS, SEEDS, EXTEND, viewsOf, stripped, perturbed, mapBack,
                  differingPatients, classify,
                  // Shared with D53's tool (d53_coded_start.cjs): the page-side definitions and the owner's counting rule.
                  DEFINITIONS, pooledReadings, planKeys, MINIMUM, SHARE, SETTINGS};

if (require.main === module) {
  const what = process.argv[2], output = arg('--output', null);
  if (what === 'view') write(output, viewsOf(fs.readFileSync(arg('--cohort', COHORT), 'utf8'), arg('--defect', null)));
  else if (what === 'reference') reference(output);
  else if (what === 'census') census(output);
  else if (what === 'usefulness') usefulness(output);
  else { console.error('usage: node demos/mockups/d47_page.cjs view|reference|census|usefulness [--output FILE]'); process.exit(2); }
}
