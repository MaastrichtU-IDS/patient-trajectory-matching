/* The trajectory page matched by the engine agrees with the page's own browser search.

   The page script runs in a stub DOM against the generated synthetic cohort. For every
   template and three heavier trajectories, each patient is matched twice: by the page's
   in-browser search, and through the page's engine path, whose requests are answered by
   patterns/trajectory_graph.py. Status, certainty and relaxation set must agree.
   D46: the index periods both paths share are placed without the generator's truth, checked at the end.
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
   window under the seven period rules and three free-text settings is the same with every truth field removed from the
   cohort, and equals the independent reference's (index_reference.py) to the minute. */
const os = require('node:os');
const d46 = require('./d46_windows.cjs');
const cohortText = fs.readFileSync(path.join(here, 'data/synthetic-paediatric-rare.json'), 'utf8');
const placed = d46.windowsOf(cohortText);
assert.deepEqual(d46.windowsOf(d46.stripped(cohortText)), placed, 'an index period moved when the truth was removed');
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
