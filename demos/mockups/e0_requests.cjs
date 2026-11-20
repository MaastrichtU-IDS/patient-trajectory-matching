/* E0: the trajectory page's store requests for its five templates, for E0's usefulness arms (decision log, E0).

     node demos/mockups/e0_requests.cjs --cohort FILE

   Loads the page's own code (shared/core.js, trajectory/app.js, trajectory/store_request.js) into a node:vm context, as
   D46's and D47's tools do, and prints {cases: [{name, query, conditions, windows}], refusals: {name: code}}: each
   template (Sequence, Tree, Graph, Event frequency, Blank) with the page's own window and scope, under stated findings
   (the page's default: no options) and under coded only (the parity test's options: profile 1.3, the joint profile for
   Tree and Graph). Times are the page's minutes from birth, as store_parity.py reads them. The request does not depend
   on how letter mentions are timed in the store: the windows are the page's (reconcile_v1), the same for every arm.
   Synthetic data only. */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const here = __dirname;

const DEFINITIONS = `
  var pageWindow = defaultDef().window;
  var shape = t => ({scope: 'from_start', window: clone(pageWindow), nodes: clone(t.nodes), edges: clone(t.edges)});
  var TEMPLATE_DEFS = {'sequence': shape(TEMPLATES.sequence), 'tree': shape(TEMPLATES.tree), 'graph': shape(TEMPLATES.graph),
                       'event frequency': shape(TEMPLATES.frequency), 'blank': shape(BLANK)};
  var translateOne = (def, freeText, options) => {
    const before = state.freeText;
    state.freeText = freeText;
    try { return storeRequest({...defaultDef(), ...def}, freeText, [...state.patients.keys()], options); }
    catch (e) { if (e instanceof StoreRefusal) return {refused: e.code}; throw e; }
    finally { state.freeText = before; }
  };
`;

function pageContext(cohortText) {
  const stub = {addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; }};
  const context = vm.createContext({document: stub, addEventListener() {}, console, structuredClone, performance, DATA: cohortText,
                                    POST: () => { throw new Error('E0 never calls the page engine'); }});
  const app = fs.readFileSync(path.join(here, 'trajectory/app.js'), 'utf8').replace(/\nmain\(\);\s*$/, '\n');
  vm.runInContext(fs.readFileSync(path.join(here, 'shared/core.js'), 'utf8') + '\n' + app + '\n'
                  + fs.readFileSync(path.join(here, 'trajectory/store_request.js'), 'utf8') + `
    state.data = JSON.parse(DATA); state.concepts = state.data.concepts;
    for (const [cid, c] of Object.entries(state.concepts)) for (const coding of c.codings) state.codeIndex.set(coding.system + '|' + coding.code, cid);
    for (const p of state.data.patients) state.patients.set(p.patient_id, p);
    state.freeText = 'present';
  ` + DEFINITIONS, context);
  return context;
}
const inContext = (context, code) => JSON.parse(vm.runInContext(`JSON.stringify(${code})`, context));

function requests(cohortText) {
  const context = pageContext(cohortText);
  const cases = [], refusals = {};
  for (const [t, def] of Object.entries(inContext(context, 'TEMPLATE_DEFS'))) {
    const joint = t === 'tree' || t === 'graph';
    for (const [setting, freeText, options] of [['stated findings', 'present', {}],
      [`coded only (${joint ? 'joint' : '1.3'})`, 'off', joint ? {codedOnly: 'store', geneCodedOnly: true} : {codedOnly: 'store'}]]) {
      const name = `${t}, ${setting}`;
      const q = inContext(context, `translateOne(${JSON.stringify(def)}, ${JSON.stringify(freeText)}, ${JSON.stringify(options)})`);
      if (q.refused) { refusals[name] = q.refused; continue; }
      cases.push({name, query: q.query, conditions: q.conditions, windows: q.windows});
    }
  }
  return {cases, refusals};
}

module.exports = {requests};

if (require.main === module) {
  const i = process.argv.indexOf('--cohort');
  if (i < 0) { console.error('usage: node demos/mockups/e0_requests.cjs --cohort FILE'); process.exit(2); }
  process.stdout.write(JSON.stringify(requests(fs.readFileSync(process.argv[i + 1], 'utf8'))) + '\n');
}
