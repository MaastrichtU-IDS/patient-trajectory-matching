/* The trajectory page matched by the engine agrees with the page's own browser search.

   The page script runs in a stub DOM against the generated synthetic cohort. For every
   template and three heavier trajectories, each patient is matched twice: by the page's
   in-browser search, and through the page's engine path, whose requests are answered by
   patterns/trajectory_graph.py. Status, certainty and relaxation set must agree.
   D46: the index periods both paths share are placed without the generator's truth, checked at the end; D47: so is the
   page's whole view.
   Run after demos/mockups/synthetic/generate.py. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');

const here = __dirname, root = path.resolve(here, '../..');
const python = process.env.PYTHON || 'python';
const post = body => JSON.parse(execFileSync(python, ['-c',
  "import json,sys; from patterns import trajectory_graph as t; d=json.load(sys.stdin); print(json.dumps(t.run(d['query'], d['patients'])))"],
  {cwd: root, input: JSON.stringify(body), encoding: 'utf8', maxBuffer: 1 << 28}));

const stub = {addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; }};
const context = vm.createContext({document: stub, addEventListener() {}, console, structuredClone, POST: post,
                                  DATA: fs.readFileSync(path.join(here, 'data/synthetic-paediatric-rare.json'), 'utf8')});
const page = fs.readFileSync(path.join(here, 'trajectory/app.js'), 'utf8').replace(/\nmain\(\);\s*$/, '\n');
vm.runInContext(fs.readFileSync(path.join(here, 'shared/core.js'), 'utf8') + '\n' + page + `
  state.data = JSON.parse(DATA); state.concepts = state.data.concepts;
  for (const [cid, c] of Object.entries(state.concepts)) for (const coding of c.codings) state.codeIndex.set(coding.system + '|' + coding.code, cid);
  for (const p of state.data.patients) state.patients.set(p.patient_id, p);
  var HEAVY = {
    'two seizures then a same-day SCN1A result': {nodes: [{id: 'A', concept: 'seizure', occ: 'any'}, {id: 'B', concept: 'seizure', occ: 'any'}, {id: 'C', concept: 'gene:SCN1A', occ: 'any'}],
      edges: [{from: 'A', to: 'B', rel: 'after', min: 0, max: 24, unit: 'month'}, {from: 'B', to: 'C', rel: 'after', min: 0, max: 1, unit: 'day'}]},
    'three seizures months apart': {nodes: [{id: 'A', concept: 'seizure', occ: 'any'}, {id: 'B', concept: 'seizure', occ: 'any'}, {id: 'C', concept: 'seizure', occ: 'any'}],
      edges: [{from: 'A', to: 'B', rel: 'after', min: 1, max: 24, unit: 'month'}, {from: 'B', to: 'C', rel: 'after', min: 1, max: 24, unit: 'month'}]},
    'two seizures then a febrile seizure': {nodes: [{id: 'A', concept: 'seizure', occ: 'any'}, {id: 'B', concept: 'seizure', occ: 'any'}, {id: 'C', concept: 'febrile_seizure', occ: 'any'}],
      edges: [{from: 'A', to: 'B', rel: 'after', min: 1, max: 24, unit: 'month'}, {from: 'B', to: 'C', rel: 'after', min: 1, max: 24, unit: 'month'}]},
  };
  var parity = function () {
    const out = [];
    const defs = [...Object.entries(TEMPLATES), ...Object.entries(HEAVY)].map(([name, t]) => [name, {...defaultDef(), ...clone(t)}]);
    const key = x => x.status + (x.certainty ? '/' + x.certainty : '') + (x.relaxKeys ? ' ' + [...x.relaxKeys].sort().join('+') : '');
    for (const [name, def] of defs) {
      const a = analyse(def);
      const steps = engineSteps(def);
      let step = steps.next();
      while (!step.done) step = steps.next(POST(step.value));
      const tally = {}, diffs = [];
      for (const r of step.value) {
        const b = matchPatient(r.pid, def, a);
        tally[key(r)] = (tally[key(r)] || 0) + 1;
        if (key(r) !== key(b)) diffs.push(r.pid + ': engine ' + key(r) + ', page ' + key(b));
      }
      out.push({name, tally, diffs});
    }
    return out;
  };`, context);

const results = vm.runInContext('parity()', context);
let matched = 0, patients = 0;
for (const {name, tally, diffs} of results) {
  // The arrays come from the sandbox's realm, so compare their contents, not their prototypes.
  assert.equal(diffs.length, 0, `${name}: the engine and the page disagree:\n${[...diffs].join('\n')}`);
  patients += Object.values(tally).reduce((a, b) => a + b, 0);
  matched += Object.entries(tally).filter(([k]) => /^(exact|possible|relaxed)/.test(k)).reduce((a, [, v]) => a + v, 0);
}
assert.ok(matched > 0, 'no trajectory matched anyone: the comparison would be vacuous');
console.log(`Trajectory page: engine and in-page search agree on ${patients} patient results over ${results.length} trajectories (${matched} matches).`);

/* D46: index periods are placed over the index view (shared/core.js, rule reconcile_v1), which reads no truth. Every
   window under the twelve period rules and three free-text settings is the same with every truth field removed from the
   cohort, and again with the stripped cohort's records shuffled or its record ids permuted (the review's S2), and equals
   the reference's (index_reference.py) to the minute. */
const os = require('node:os');
const d46 = require('./d46_windows.cjs');
const cohortText = fs.readFileSync(path.join(here, 'data/synthetic-paediatric-rare.json'), 'utf8');
const placed = d46.windowsOf(cohortText);
assert.deepEqual(d46.windowsOf(d46.stripped(cohortText)), placed, 'an index period moved when the truth was removed');
for (const [how, text] of Object.entries(d46.perturbed(d46.stripped(cohortText))))
  assert.deepEqual(d46.windowsOf(text), placed, `an index period moved with the truth removed and ${how.replace(/_/g, ' ')}`);
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'index-windows-'));
try {
  fs.writeFileSync(path.join(scratch, 'windows.json'), JSON.stringify(placed));
  let out;
  try {
    out = execFileSync(python, [path.join(here, 'index_reference.py'), 'compare', path.join(here, 'data/synthetic-paediatric-rare.json'),
                                path.join(scratch, 'windows.json')], {cwd: root, encoding: 'utf8'});
  } catch (e) { out = e.stdout; }
  const summary = JSON.parse(out);
  assert.equal(summary.mismatches, 0, `index periods differ from the reference:\n${JSON.stringify(summary.first_mismatches, null, 1)}`);
  assert.ok(summary.placed > 0, 'no index period placed: the comparison would be vacuous');
  console.log(`Index periods: ${summary.compared} windows (${summary.placed} placed) equal the reference's and do not read the truth.`);
} finally {
  fs.rmSync(scratch, {recursive: true, force: true});
}

/* D46 rule 4's verdict (the review's M1 and S3), on constructed inputs: a moved window without a class, or with an
   unexplained bound, fails the census, and so does a changed result on it; "another occurrence ranks" needs the
   occurrences up to an anchor to differ between the two sides. */
{
  const anchor = (at, times, records) => ({at, times, from_end: false, records, mentions: []});
  const none = () => ({mainHereCount: 1, mineMainCount: 1});
  assert.equal(d46.classify(anchor(10, [10, 20], ['a:condition']), anchor(10, [10, 20], ['b:condition']), none), 'same anchor');
  assert.equal(d46.classify(anchor(20, [10, 20], ['a:condition']), anchor(15, [10, 15], ['b:condition']), none),
               'another happening: another occurrence ranks');
  assert.equal(d46.classify(anchor(20, [20], ['a:condition']), anchor(15, [20], ['b:condition']), none), 'unexplained');
  assert.equal(d46.classify(anchor(20, [20], ['a:condition', 'b:registry_entry']), anchor(15, [15], ['a:condition', 'b:registry_entry']), none),
               'unexplained');
  const windows = cls => ({r: {present: {patients: {C001: {class: cls}}}}});
  assert.equal(d46.censusMet(windows('start: another happening: another occurrence ranks'), 0).met, true);
  assert.equal(d46.censusMet(windows('start: another happening: another occurrence ranks'), 1).met, false);
  assert.equal(d46.censusMet(windows('unexplained'), 0).met, false);
  assert.equal(d46.censusMet(windows('start: anchored only here; end: unexplained'), 0).met, false);
  assert.equal(d46.explained('unexplained'), false);
  assert.equal(d46.explained(undefined), false);
  assert.equal(d46.explained('end: a mention-only happening anchors here'), true);
  console.log('Index-period census verdict: a moved window or a changed result without an explanation fails it.');
}

/* D47: the page's whole view (shared/core.js, evidenceFor over rule reconcile_v1: items, series readings, letters and
   their data-quality cases, attached per record) reads no truth. It is the same with every truth link removed from the
   cohort (the cases kept), with the stripped cohort's records shuffled and with its record ids permuted, and equals the
   reference's (index_reference.py compare-view); every record is held exactly once; the page's own search gives the same
   results with the truth removed; and only the truth lens's display code names the truth. */
{
  const d47 = require('./d47_page.cjs');
  const view = d47.viewsOf(cohortText), free = d47.stripped(cohortText);
  assert.equal(d47.differingPatients(view, d47.viewsOf(free)), 0, 'the page\'s view moved when the truth was removed');
  for (const [how, {text, back}] of Object.entries(d47.perturbed(free)))
    assert.equal(d47.differingPatients(view, d47.mapBack(d47.viewsOf(text), back)), 0, `the page's view moved with ${how.replace(/_/g, ' ')}`);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'page-view-'));
  try {
    fs.writeFileSync(path.join(dir, 'view.json'), JSON.stringify(view));
    let out;
    try {
      out = execFileSync(python, [path.join(here, 'index_reference.py'), 'compare-view', path.join(here, 'data/synthetic-paediatric-rare.json'),
                                  path.join(dir, 'view.json')], {cwd: root, encoding: 'utf8'});
    } catch (e) { out = e.stdout; }
    const summary = JSON.parse(out);
    assert.equal(summary.mismatches, 0, `the page's view differs from the reference:\n${JSON.stringify(summary.first_mismatches, null, 1)}`);
    assert.ok(summary.items > 0, 'no item: the comparison would be vacuous');
    console.log(`Page view: ${summary.items} events over ${Object.keys(view).length} patients equal the reference's and do not read the truth.`);
  } finally {
    fs.rmSync(dir, {recursive: true, force: true});
  }
  // Only the truth lens's display code reads the truth (rule 2, static; widened after the review, S1): in the raw text of
  // every page script, each line naming a truth link (\`truth_event_id\`, \`.truth\`, \`['truth\`, \`["truth\`) is one of
  // these, the lens's own lines (core.js's truthItems; the "truth:" labels of two result lists).
  const LENS_LINES = new Set([
    "const holders = id => view.items.filter(i => i.recs.some(r => r.truth_event_id === id) || i.mentions.some(x => x.m.truth_event_id === id));",
    "return p.truth.events.filter(e => lane[e.kind]).map(e => ({",
    "${chip(r.status, r.cost)}${state.lens === 'truth' ? ` <span class=\"hint\">truth: ${esc(GROUP_LABEL[p.truth.condition_group])}</span>` : ''}",
    "const truth = state.lens === 'truth' ? ` · <strong>truth: ${esc(GROUP_LABEL[view.p.truth.condition_group])}</strong>` : '';",
    "out.push(`<details class=\"excluded\"><summary>${res.excluded.length} not eligible, and why</summary><ul>${res.excluded.map(e => `<li><strong>${e.pid}</strong>: ${esc(e.reason)}${state.lens === 'truth' ? ` <em>(truth: ${esc(GROUP_LABEL[state.patients.get(e.pid).truth.condition_group])})</em>` : ''}</li>`).join('')}</ul></details>`);",
  ]);
  const TRUTH = /truth_event_id|\.truth\b|\[\s*['"]truth/;
  const flagged = [];
  for (const f of ['shared/core.js', 'trajectory/app.js', 'trajectory/store_request.js', 'patient-first/app.js', 'store-query/app.js'])
    fs.readFileSync(path.join(here, f), 'utf8').split('\n').forEach((line, k) => {
      if (TRUTH.test(line) && !LENS_LINES.has(line.trim()))
        flagged.push(`${f}:${k + 1}: ${line.trim().slice(0, 120)}`);
    });
  assert.deepEqual(flagged, [], 'a page script reads the truth outside the truth lens');
  // Every record is held exactly once: by an item, a series reading, a letter or the list of records the rule could not place.
  const pc = d47.pageContext(cohortText, 'this');
  const complete = d47.inContext(pc, 'completeness()');
  assert.ok(complete.met, `records held by none or by several: ${JSON.stringify(complete)}`);
  // The page's own search reads no truth (S1): the templates and the first 200 sweep definitions, three settings, give the
  // same results on the stripped cohort.
  const own = d47.inContext(pc, 'ownAll(200)'), ownFree = d47.inContext(d47.pageContext(free, 'this'), 'ownAll(200)');
  let ownCompared = 0;
  for (const k of Object.keys(own)) for (const pid of Object.keys(own[k])) { ownCompared += 1; assert.deepEqual(ownFree[k][pid], own[k][pid], `${k} ${pid}: the page's own search moved with the truth removed`); }
  console.log(`Page view: every one of ${complete.records} records held once; ${ownCompared} own-search results the same with the truth removed.`);
  // The census's classes (D47 rule 4), on constructed candidates.
  const c = (sig, t = [1, 1, 1, 1]) => ({t, sig});
  const held = map => (side, sig) => map[side + ':' + sig.join(',')] ?? [['other']];
  assert.equal(d47.classify({'step:A': [c(['r1'])]}, {'step:A': [c(['r1'])]}, held({})), 'unexplained');
  assert.equal(d47.classify({'step:A': [c(['r1', 'r2'])]}, {'step:A': [c(['r1'])]}, held({'here:r1,r2': [['r1'], ['r2']], 'main:r1': [['r1', 'r2']]})), 'regrouped + split');
  assert.equal(d47.classify({'step:A': [c(['r1'])]}, {'step:A': [c(['n1#0'])]}, held({})), 'mention-only + regrouped');
  assert.equal(d47.classify({'step:A': [c(['r1'], [1, 1, 1, 1])]}, {'step:A': [c(['r1'], [2, 2, 2, 2])]}, held({})), 'reordered');
  // S2: a holder with the candidate's own records and mentions explains nothing; nor do readings of an unchanged series;
  // nor a window placed on one side only.
  assert.equal(d47.classify({'step:A': [c(['r1'])]}, {'step:A': [c(['r2'])]}, held({'here:r1': [['r1']]})), 'unexplained');
  assert.equal(d47.classify({'step:B': [c(['measure:m1'])]}, {'step:B': []}, held({}), () => true), 'unexplained');
  assert.equal(d47.classify({'step:B': [c(['measure:m1'])]}, {'step:B': []}, held({}), () => false), 'series');
  assert.equal(d47.classify(null, {'step:A': []}, held({})), 'unexplained');
  // The same event picked on one side only (first, last, Nth, a count reached) is a reranking only if the step's events
  // before the pick differ.
  assert.equal(d47.classify({'step:A': [], 'all step:A': [c(['r1'])]}, {'step:A': [c(['r1'])], 'all step:A': [c(['r1'])]}, held({'main:r1': [['r1']]})), 'unexplained');
  assert.equal(d47.classify({'step:A': [], 'all step:A': [c(['r1']), c(['r2', 'r3'])]}, {'step:A': [c(['r1'])], 'all step:A': [c(['r1']), c(['r2']), c(['r3'])]},
                            held({'main:r1': [['r1']], 'here:r2,r3': [['r2'], ['r3']], 'main:r2': [['r2', 'r3']], 'main:r3': [['r2', 'r3']]})),
               'regrouped + reranked + split');
  console.log('Page-view census classes: a changed result whose candidates are unchanged is unexplained.');
}

/* D49: the census's pure parts (d49_flip.cjs), on constructed requests and results. */
{
  const d49 = require('./d49_flip.cjs');
  const q = {steps: []}, w = {C001: {start: 0, end: 1}};
  const coded = (steps, profile = 'execution-store-conditions-1.3') => ({query: q, windows: w, conditions: {profile, steps, exclude_kinds: ['mention']}});
  // The request without the exclusion: 1.3 to 1.1, 1.2 with a rise, no conditions without a conditioned step; joint to gene.
  assert.equal(d49.withoutExclusion(coded({A: {count: {n: 2}}})).conditions.profile, 'execution-store-conditions-1.1');
  assert.equal(d49.withoutExclusion(coded({A: {relative: {kind: 'rise'}}})).conditions.profile, 'execution-store-conditions-1.2');
  assert.equal(d49.withoutExclusion(coded({})).conditions, null);
  assert.equal(d49.withoutExclusion(coded({B: {gene: true}}, 'execution-store-conditions-gene-coded-1.0')).conditions.profile, 'execution-store-conditions-gene-1.0');
  assert.ok(!('exclude_kinds' in d49.withoutExclusion(coded({A: {count: {n: 2}}})).conditions));
  assert.throws(() => d49.withoutExclusion({query: q, windows: w, conditions: {profile: 'execution-store-conditions-1.1', steps: {}}}));
  // The classes of a changed result under a profile move; anything else is named by its transition.
  assert.equal(d49.moveClass({status: 'none'}, {status: 'not_comparable', reason: 'NO_SOURCE_COVERAGE'}), 'coverage');
  assert.equal(d49.moveClass({status: 'not_comparable', reason: 'NO_SOURCE_COVERAGE'}, {status: 'not_comparable', reason: 'NO_SOURCE_RECORDS_CONCEPT'}), 'other: not_comparable -> not_comparable');
  assert.equal(d49.moveClass({status: 'none'}, {status: 'none', reason: 'FILTERED'}), 'filtered');
  assert.equal(d49.moveClass({status: 'none', relaxations: ['x']}, {status: 'none', reason: 'FILTERED'}), 'other: none -> none');
  assert.equal(d49.moveClass({status: 'exact', certainty: 'exact'}, {status: 'possible', certainty: 'possible'}), 'other: exact/exact -> possible/possible');
  // The cohort without letter mentions keeps every record; the declarations are checked exactly once.
  const bare = d49.withoutMentions({patients: [{records: [{kind: 'letter', mentions: [{}, {}]}, {kind: 'diagnosis'}]}]});
  assert.equal(bare.removed, 2);
  assert.equal(bare.cohort.patients[0].records.length, 2);
  assert.throws(() => d49.declared('const STORE_CODED_ONLY = true;', 'true'));
  d49.declared('const STORE_CODED_ONLY = true;\nconst STORE_GENE_CODED_ONLY = true;', 'true');
  // The page's translator as committed: both constants on (D49).
  d49.declared(fs.readFileSync(path.join(here, 'trajectory/store_request.js'), 'utf8'), 'true');
  console.log('D49 census parts: the request without the exclusion, the move classes and the flipped declarations.');
}

/* D53: "Coded records only" reads no clinical note for any step, in the page's own search and its index periods too
   (D49's help text; D49 pinned one exception, C026's medicine, a course with only a stop order, started from a letter).
   Generic, over the whole cohort: every item as that setting searches it (core.js's codedOf) equals, in every field a
   search reads and its label, the item the same records make on the cohort with every letter mention removed; an item
   with no coded record is not searched. The index periods under that setting (D46's twelve rules, and windows anchored on
   every concept an item holds, first and last) and the page's own search (the templates and the first 200 sweep
   definitions) are the same on both cohorts. Not vacuous: some item's full form takes a searched field from a note, and
   under stated findings some anchored window still reads one. */
{
  const d49 = require('./d49_flip.cjs'), d53 = require('./d53_coded_start.cjs'), d47 = require('./d47_page.cjs');
  const bare = d49.withoutMentions(JSON.parse(cohortText)), bareText = JSON.stringify(bare.cohort);
  assert.ok(bare.removed > 0, 'no letter mention removed: the check would be vacuous');
  const FIELDS = JSON.stringify(['kind', 'concept', 'textConcept', 'display', 'time', 'med', 'gene', 'procedure', 'freeTextOnly',
                                 'possibleOnly', 'recordedDay', 'availableDay']);
  const READ = `(() => {
    const F = ${FIELDS}, form = i => JSON.stringify(F.map(k => i[k] === undefined ? null : i[k]));
    const out = {searched: {}, noted: [], concepts: {}};
    for (const pid of state.patients.keys()) for (const i of evidenceFor(pid).items) {
      const c = codedOf(i), key = pid + ' ' + i.recs.map(r => r.record_id).sort().join(',');
      if (c) out.searched[key] = form(c);
      if (c && i.mentions.length && ${JSON.stringify(d53.SEARCH_FIELDS)}.some(k => JSON.stringify(c[k]) !== JSON.stringify(i[k])))
        out.noted.push(pid + ' ' + i.kind + ' ' + i.concept + ' ' + i.recs.map(r => r.kind + ':' + r.status).join(','));
      if (i.concept) (out.concepts[pid] = out.concepts[pid] || new Set()).add(i.concept);
    }
    for (const pid of Object.keys(out.concepts)) out.concepts[pid] = [...out.concepts[pid]].sort();
    return out;
  })()`;
  const full = d47.pageContext(cohortText, 'this'), stripped = d47.pageContext(bareText, 'this');
  const a = d47.inContext(full, READ), b = d47.inContext(stripped, READ);
  assert.deepEqual(Object.keys(a.searched).sort(), Object.keys(b.searched).sort(), 'coded only searches other items than the records make without mentions');
  const unlike = Object.keys(a.searched).filter(k => a.searched[k] !== b.searched[k]);
  assert.deepEqual(unlike, [], 'an item under coded only takes a time, a concept or a label from a note');
  assert.equal(b.noted.length, 0);
  assert.ok(a.noted.length > 0, 'no item reads a note in a searched field: the check would be vacuous');
  // The index periods: D46's rules, and windows anchored on every concept an item holds (first and last, for 6 months).
  const placedBare = d46.windowsOf(bareText);
  for (const rule of Object.keys(placed.windows))
    assert.deepEqual(placedBare.windows[rule].off, placed.windows[rule].off, `${rule}: an index period under coded only reads a note`);
  const ANCHORED = setting => `(() => {
    const out = {};
    for (const [pid, cs] of Object.entries(${JSON.stringify(a.concepts)})) for (const c of cs) for (const which of ['first', 'last']) {
      const w = windowFor(evidenceFor(pid), {freeText: '${setting}', knownByIndex: false,
        window: {start: {kind: 'event', event: c, which, n: 1, offset: 0, unit: 'month'}, end: {kind: 'span', amount: 6, unit: 'month'}}});
      out[pid + ' ' + which + ' ' + c] = w.reason ? 'unplaced' : [w.start, w.end];
    }
    return out;
  })()`;
  const anchoredFull = d47.inContext(full, ANCHORED('off')), anchoredBare = d47.inContext(stripped, ANCHORED('off'));
  assert.deepEqual(anchoredFull, anchoredBare, 'a window anchored on an item under coded only reads a note');
  const statedFull = d47.inContext(full, ANCHORED('present')), statedBare = d47.inContext(stripped, ANCHORED('present'));
  const stated = Object.keys(statedFull).filter(k => JSON.stringify(statedFull[k]) !== JSON.stringify(statedBare[k]));
  assert.ok(stated.length > 0, 'under stated findings no anchored window reads a note: the check would be vacuous');
  // A step's candidates under coded only (trajectory/app.js's candidates: times, course ends and labels), one step on
  // every concept an item holds, over the whole record: the same on both cohorts.
  const CANDS = `(() => {
    const before = state.freeText, out = {}, all = {start: -Infinity, end: Infinity};
    state.freeText = 'off';
    try {
      for (const [pid, cs] of Object.entries(${JSON.stringify(a.concepts)})) for (const c of cs) {
        const n = {id: 'A', concept: c, occ: 'any'};
        if (stepKind(n) && stepKind(n) !== 'measure') out[pid + ' ' + c] = candidates(evidenceFor(pid), n, new Set(), all).map(x => [x.e, x.l, x.fe, x.fl, x.what]);
      }
    } finally { state.freeText = before; }
    return out;
  })()`;
  const candsFull = d47.inContext(full, CANDS), candsBare = d47.inContext(stripped, CANDS);
  assert.ok(Object.keys(candsFull).length > 0);
  assert.deepEqual(candsFull, candsBare, 'a step\'s candidates under coded only read a note');
  // The page's own search under coded only: the same on both cohorts.
  const OWN =`(() => { const out = {};
    const defs = [...Object.entries(TEMPLATE_DEFS).map(([n, def]) => ['template: ' + n, def]), ...sweepCases().slice(0, 200).map(c => ['sweep: ' + c.name, c.def])];
    for (const [name, def] of defs) out[name] = ownResults(def, 'off');
    return out; })()`;
  const ownFull = d47.inContext(full, OWN), ownBare = d47.inContext(stripped, OWN);
  let compared = 0;
  for (const k of Object.keys(ownFull)) for (const pid of Object.keys(ownFull[k])) {
    compared += 1;
    assert.deepEqual(ownBare[k][pid], ownFull[k][pid], `${k} ${pid}: the page's own search under coded only reads a note`);
  }
  // The census's pure parts (d53_coded_start.cjs).
  const req = w => ({query: {steps: []}, conditions: null, windows: w});
  assert.deepEqual(d53.requestDiff(req({C1: {start: 1, end: 2}}), req({C1: {start: 1, end: 2}})), {same: true, windows_only: true, patients: []});
  assert.deepEqual(d53.requestDiff(req({C1: {start: 1, end: 2}, C2: {start: 0, end: 3}}), req({C1: {start: 1, end: 9}, C2: {start: 0, end: 3}})),
                   {same: false, windows_only: true, patients: ['C1']});
  assert.equal(d53.requestDiff({...req({}), conditions: {profile: 'x'}}, req({})).windows_only, false);
  assert.equal(d53.requestDiff({refused: 'A'}, {refused: 'B'}).windows_only, false);
  const cand = (sig, t = [1, 1, 1, 1]) => ({t, sig});
  assert.equal(d53.traced({'step:A': [cand(['r1'])]}, {'step:A': [cand(['r1'])]}, [['r1']]), false, 'identical candidates trace nothing');
  assert.equal(d53.traced({'step:A': [cand(['r1'], [5, 5, 5, 5])]}, {'step:A': []}, [['r1']]), true);
  assert.equal(d53.traced({'step:A': [cand(['r1'], [5, 5, 5, 5])]}, {'step:A': []}, [['r2']]), false, 'a candidate of an item that reads no note');
  assert.equal(d53.traced({'step:A': [cand(['r2'])], 'all step:A': [cand(['r1']), cand(['r2'])]},
                          {'step:A': [cand(['r3'])], 'all step:A': [cand(['r2']), cand(['r3'])]}, [['r1'], ['r3']]), true, 'reranked by noted items');
  assert.equal(d53.traced({'step:A': [cand(['r2'])], 'all step:A': [cand(['r2'])]}, {'step:A': [], 'all step:A': [cand(['r2'])]}, [['r2']]), false,
               'a pick that moves over the same events is not traced');
  const tallies = (over = {}) => ({other: {results: 0, windows: 0, requests: 0}, noted_items: 1,
    off: {results_unlike_removal: 0, windows_unlike_removal: 0, untraced_results: 0, untraced_windows: 0, requests_not_windows_only: 0,
          request_windows_untraced: 0, ...over}});
  assert.ok(Object.values(d53.censusMet(tallies())).every(Boolean));
  assert.equal(d53.censusMet(tallies({untraced_results: 1})).traced, false);
  assert.equal(d53.censusMet(tallies({results_unlike_removal: 1})).removal, false);
  assert.equal(d53.censusMet({...tallies(), noted_items: 0}).not_vacuous, false);
  console.log(`D53 coded only reads no note: ${Object.keys(a.searched).length} searched items equal their records' own on the cohort without `
              + `${bare.removed} letter mentions (before D53, ${a.noted.length} read one in a searched field: ${a.noted.slice(0, 3).join('; ')}); `
              + `${Object.keys(placed.windows).length} period rules, ${Object.keys(anchoredFull).length} anchored windows, ${Object.keys(candsFull).length} steps' candidates and ${compared} `
              + `own-search results under coded only are the same without them; under stated findings ${stated.length} anchored windows read one.`);
}

/* D53 review (S1): the patient-first page under "Coded records only" reads no note either, its similarity weights
   (icTable) included. Its comparison (evaluate), for references C001 and C026, each similarity measure and "known by the
   end of the window" on and off, gives the same rows (scores, per-feature similarities, coverage, feature values) and
   exclusions on the cohort and on the cohort without letter mentions. Not vacuous: under stated findings some row differs. */
{
  const d49 = require('./d49_flip.cjs');
  const bareText = JSON.stringify(d49.withoutMentions(JSON.parse(cohortText)).cohort);
  const pfApp = fs.readFileSync(path.join(here, 'patient-first/app.js'), 'utf8').replace(/\nmain\(\);\s*$/, '\n');
  const pfRun = (text, freeText) => {
    const ctx = vm.createContext({document: stub, addEventListener() {}, console, structuredClone, DATA: text});
    vm.runInContext(fs.readFileSync(path.join(here, 'shared/core.js'), 'utf8') + '\n' + pfApp + `
      state.data = JSON.parse(DATA); state.concepts = state.data.concepts;
      for (const [cid, c] of Object.entries(state.concepts)) for (const coding of c.codings) state.codeIndex.set(coding.system + '|' + coding.code, cid);
      for (const p of state.data.patients) state.patients.set(p.patient_id, p);`, ctx);
    return JSON.parse(vm.runInContext(`JSON.stringify((() => {
      const out = {};
      for (const ref of ['C001', 'C026']) for (const m of Object.keys(MEASURES)) for (const k of [true, false]) {
        state.referenceId = ref;
        const q = {...defaultQuery(), freeText: '${freeText}', knownByIndex: k, conceptMeasure: m}, r = evaluate(q);
        const shown = values => Object.fromEntries(FEATURES.map(f => [f.key, values[f.key] ? f.show(values[f.key].value) : null]));
        out[ref + ' ' + m + (k ? ' known' : '')] = {ref: shown(r.ref.values), excluded: r.excluded,
          rows: r.rows.map(x => ({pid: x.pid, score: x.score, coverage: x.coverage, sims: x.sims, values: shown(x.values)}))};
      }
      return out;
    })())`, ctx));
  };
  const offFull = pfRun(cohortText, 'off'), offBare = pfRun(bareText, 'off');
  let rows = 0;
  for (const k of Object.keys(offFull)) {
    rows += offFull[k].rows.length;
    assert.deepEqual(offBare[k], offFull[k], `patient-first, ${k}: the comparison under coded only reads a note`);
  }
  assert.ok(rows > 0);
  const statedFull = pfRun(cohortText, 'present'), statedBare = pfRun(bareText, 'present');
  const statedDiffer = Object.keys(statedFull).filter(k => JSON.stringify(statedFull[k]) !== JSON.stringify(statedBare[k])).length;
  assert.ok(statedDiffer > 0, 'under stated findings no patient-first comparison reads a note: the check would be vacuous');
  console.log(`D53 patient-first under coded only reads no note: ${Object.keys(offFull).length} comparisons (${rows} rows; 3 measures, 2 references) `
              + `the same without letter mentions; under stated findings ${statedDiffer} differ.`);
}

/* D52: the index anchor as a hidden step. The translator's rewrite on the page's cohort, the page's reading of an
   indexed answer, and the census's pure parts (d52_anchor.cjs), on constructed results. */
{
  const d52 = require('./d52_anchor.cjs');
  const ctx = d52.pageContext(cohortText, 'here');
  const read = code => JSON.parse(vm.runInContext(`JSON.stringify(${code})`, ctx));
  // The page's default period (the first seizure, 24 months), Blank, coded only: one hidden step, the scope `any` with
  // its relations, eligibility from the hidden step; D49's request on request; a constant period is not rewritten.
  const r = read(`(() => { state.freeText = 'off'; const before = INDEX_REWRITES.calls;
    const mine = storeRequest({...defaultDef(), ...clone(BLANK)}, 'off', [...state.patients.keys()]);
    const pin = storeRequest({...defaultDef(), ...clone(BLANK)}, 'off', [...state.patients.keys()], {indexAnchor: false});
    const constant = {...defaultDef(), ...clone(BLANK), window: {start: {kind: 'birth'}, end: {kind: 'age', amount: 6, unit: 'year'}}};
    const mid = INDEX_REWRITES.calls;
    const c1 = storeRequest(constant, 'off', [...state.patients.keys()]), c2 = storeRequest(constant, 'off', [...state.patients.keys()], {indexAnchor: false});
    return {mine, pin, calls: [mid - before, INDEX_REWRITES.calls - mid], constantSame: JSON.stringify(c1) === JSON.stringify(c2)}; })()`);
  assert.deepEqual(r.calls, [1, 0]);
  assert.ok(r.constantSame);
  assert.deepEqual(r.mine.query.steps.map(s => s.id), ['A', '@index_start']);
  assert.equal(r.mine.query.scope, 'any');
  assert.deepEqual(r.mine.index, {rule: 'index_anchor_v1', start: {step: '@index_start', offset: 0}, end: {step: '@index_start', offset: 1052006}});
  assert.deepEqual(r.mine.query.relations.map(x => [x.id, x.from, x.to, x.min, x.max]),
    [['index:scope:A', 'START', 'A', 0, undefined], ['index:scope-start:A', '@index_start', 'A', 0, undefined],
     ['index:followed', '@index_start', 'END', 1052006, undefined], ['index:born', 'START', '@index_start', -1052006, undefined]].map(a => a.map(v => v === undefined ? undefined : v)));
  assert.equal(r.pin.index, undefined);
  assert.equal(r.pin.query.scope, 'from_start');
  // Every base window starts at birth and ends at last contact; the page's anchor plays no part.
  const C001 = read(`state.patients.get('C001').follow_up.last_contact`);
  assert.deepEqual(r.mine.windows.C001, {start: 0, end: read(`toGrid(dayOf(state.patients.get('C001'), ${JSON.stringify(C001)}))`)});
  // The page reads an indexed answer: the hidden step is kept out of the steps, the period placed from its witness.
  const page = read(`(() => {
    state.def = {...defaultDef(), ...clone(BLANK)};
    const answer = {conditions_profile: 'execution-store-conditions-1.3', index: {rule: 'index_hull_v1', reasons: {C002: 'INDEX_ANCHOR_NOT_RECORDED'},
      windows: {C001: [1440, 1440 * 800], C002: null}}, results: [
      {patient_id: 'C001', status: 'exact', binding: {A: {kind: 'condition', start: [14400, 14400]}, '@index_start': {kind: 'condition', start: [14400, 14400]}},
       witness: {A: 14400, '@index_start': 14400}},
      {patient_id: 'C002', status: 'excluded', reason: 'NO_INDEX_WINDOW'}]};
    return fromStore(answer, state.def).map(x => ({status: x.status, win: x.win, period: x.period, steps: x.best ? [...x.best.bind.keys()] : null,
                                                   anchor: x.anchor ? [...x.anchor.keys()] : null, reason: x.reason})); })()`);
  assert.deepEqual(page[0].steps, ['A']);
  assert.deepEqual(page[0].anchor, ['@index_start']);
  assert.deepEqual(page[0].period, {start: 10, end: 10 + 1052006 / 1440});
  assert.deepEqual(page[0].win, {start: 1, end: 800});
  assert.equal(page[1].status, 'excluded');
  assert.ok(/holds no record of the period's event/.test(page[1].reason));
  // The census's classes through the pinned request (D52 review, M2), on constructed results.
  const ir = {ranges: {'@index_start': {P: [2, 100, 100], Q: [2, 100, 160]}}, windows: {P: {start: 100, end: 900}, Q: {start: 100, end: 960}},
              base_windows: {P: {start: 0, end: 2000}, Q: {start: 0, end: 2000}}, reasons: {}};
  const index = {start: {step: '@index_start', offset: 0}, end: {step: '@index_start', offset: 800}};
  const same3 = {main: {admitted: []}, pinned: {admitted: []}, hull: {admitted: []}};
  const one = (pid, m, p, h, at, extra = {}) => d52.classesOf({m, p, h, pid, ir, index, facts: same3,
    page: {reason: null, anchors: {start: at}, last: 2000}, hasStartMax: false, ...extra});
  const E = {status: 'exact'}, P = {status: 'possible'};
  // A certain anchor at the page's point explains nothing: the change is unexplained.
  assert.deepEqual(one('P', P, P, E, 100), {classes: [], unexplained: ['pinned -> D52']});
  assert.deepEqual(one('Q', P, P, E, 100), {classes: ['anchor_uncertain'], unexplained: []});
  assert.deepEqual(one('P', P, P, E, 90), {classes: ['anchor_moved'], unexplained: []});
  // Both sides `none` before the coverage rule: the change is that rule's verdict over the point window against the hull,
  // never the anchor; a change of the engine's own answer is the anchor's.
  const NC = {status: 'not_comparable', reason: 'NO_SOURCE_COVERAGE'};
  assert.deepEqual(one('Q', {status: 'none'}, {status: 'none'}, NC, 100), {classes: ['coverage_hull'], unexplained: []});
  assert.deepEqual(one('P', NC, NC, {status: 'none', reason: 'FILTERED'}, 100), {classes: ['coverage_hull'], unexplained: []});
  assert.deepEqual(one('Q', NC, NC, P, 100), {classes: ['anchor_uncertain'], unexplained: []});
  // Main to the pinned request: only D44's admission scope, verified on its facts.
  const ADM = {status: 'not_comparable', reason: 'ADMISSION_EXCLUDED_RECORDS'};
  assert.deepEqual(one('Q', P, ADM, ADM, 100), {classes: [], unexplained: ['main -> pinned']});
  assert.deepEqual(one('Q', P, ADM, ADM, 100, {facts: {...same3, pinned: {admitted: [['A', 1, 2]]}, hull: {admitted: [['A', 1, 2]]}}}),
                   {classes: ['admission_scope'], unexplained: []});
  assert.deepEqual(one('Q', E, E, {status: 'unresolved', reason: 'OCCURRENCE_ORDER_UNCERTAIN'}, 100, {related: true}),
                   {classes: ['anchor_uncertain', 'coupled'], unexplained: []});
  assert.deepEqual(d52.classesOf({m: {status: 'excluded'}, p: {status: 'excluded'}, h: P, pid: 'P', ir, index, hasStartMax: false, facts: same3,
    page: {reason: 'not followed to the end of the window (last contact at 2y)', anchors: {start: 100}, last: 2000}}), {classes: ['eligibility_gained'], unexplained: []});
  assert.deepEqual(one('Q', E, E, P, 100, {page: {reason: null, anchors: {start: 100}, last: 850}}),
                   {classes: ['anchor_uncertain', 'eligibility_partial'], unexplained: []});
  // The pinned request: main's, its scope written as relations, the same windows.
  const pin = d52.pinned({query: {scope: 'within', relations: [{id: 'edge:0'}], steps: []}, conditions: null, windows: {P: {start: 1, end: 2}}}, {nodes: [{id: 'A'}]});
  assert.deepEqual(pin.query.relations.map(x => [x.id, x.from, x.to]), [['edge:0', undefined, undefined], ['index:scope:A', 'START', 'A'], ['index:scope-end:A', 'A', 'END']]);
  assert.equal(pin.query.scope, 'any');
  // D52's point reasons fit their shapes only (review, S2), and a point period is D49's request, marked.
  const w = (start, end) => ({window: {start, end}, nodes: []});
  const ev = (event, which = 'first', n = 1) => ({kind: 'event', event, which, n, offset: 0, unit: 'month'});
  assert.ok(d52.pointFits('INDEX_ANCHOR_ANY_UNSUPPORTED', w(ev('@any_diagnosis'), {kind: 'span', amount: 1, unit: 'year'}), 'off', {}));
  assert.ok(!d52.pointFits('INDEX_ANCHOR_ANY_UNSUPPORTED', w(ev('seizure'), {kind: 'span', amount: 1, unit: 'year'}), 'off', {}));
  assert.ok(d52.pointFits('FREE_TEXT_COUNT_UNSUPPORTED', w(ev('seizure', 'nth', 3), {kind: 'span', amount: 1, unit: 'year'}), 'present', {}));
  assert.ok(!d52.pointFits('FREE_TEXT_COUNT_UNSUPPORTED', w(ev('seizure', 'nth', 3), {kind: 'span', amount: 1, unit: 'year'}), 'off', {}));
  const pt = read(`(() => { state.freeText = 'off'; const def = {...defaultDef(), ...clone(BLANK), window: {start: {kind: 'event', event: '@any_diagnosis', which: 'first', n: 1, offset: 0, unit: 'month'}, end: {kind: 'span', amount: 2, unit: 'year'}}};
    const mine = storeRequest(def, 'off', [...state.patients.keys()]), pin = storeRequest(def, 'off', [...state.patients.keys()], {indexAnchor: false});
    const {index_anchor, ...rest} = mine; return {index_anchor, same: JSON.stringify(rest) === JSON.stringify(pin)}; })()`);
  assert.deepEqual(pt, {index_anchor: {mode: 'point', reason: 'INDEX_ANCHOR_ANY_UNSUPPORTED'}, same: true});
  console.log('D52: the rewrite of the default period (one hidden step, scope any, eligibility from the step), D49\'s request on '
              + 'request, a constant period untouched, a point period marked; the page\'s reading of an indexed answer; the census\'s '
              + 'classes through the pinned request and the point reasons\' shapes.');
}
