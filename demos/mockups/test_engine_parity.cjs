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
  // The help text (D49): "Coded records only" reads no clinical note for any step. On the page's own search (its
  // fallback), every item admissible under "off" takes its time from coded records, with one known exception: a medicine
  // whose only medication record is a stopped order takes its start from a letter (core.js's makeItem), pinned here.
  const fallback = JSON.parse(vm.runInContext(`JSON.stringify((() => {
    const out = {items: 0, from_a_note: []};
    for (const pid of state.patients.keys()) for (const i of evidenceFor(pid).items) {
      if (!admissible(i, {freeText: 'off'}) || !i.mentions.length) continue;
      out.items += 1;
      const coded = i.kind === 'genetic' ? i.recs.some(r => r.kind === 'test_order' || r.kind === 'genetic_result')
        : i.kind === 'medication' ? i.recs.some(r => (r.kind === 'medication_order' && r.status === 'active') || r.kind === 'dispensing')
        : i.recs.some(r => r.occurred);
      if (!coded) out.from_a_note.push(pid + ' ' + i.kind + ' ' + i.recs.map(r => r.kind + ':' + r.status).join(','));
    }
    return out;
  })())`, require('./d47_page.cjs').pageContext(cohortText, 'this')));
  assert.deepEqual(fallback.from_a_note, ['C026 medication medication_order:stopped'], 'the page\'s own search times an item from a note under coded only');
  console.log(`D49 census parts: the request without the exclusion, the move classes and the flipped declarations; the fallback times `
              + `${fallback.items - fallback.from_a_note.length} of ${fallback.items} mixed items from coded records under "off" (1 known exception).`);
}
