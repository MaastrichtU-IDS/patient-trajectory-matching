/* D37 rule 1: the option sweep's grid. A fixed grid of page definitions over every step option, relation, anchor,
   scope, index-period rule and free-text setting the page offers, each valid on the page (no `analyse` error).

   Moved here from test_store_parity.cjs in D45, unchanged, so that D45's readiness tool (coded_default.cjs) sweeps the
   same grid under a translator with "coded only" on by default. It is plain script text evaluated in a page context
   (shared/core.js, trajectory/app.js and a translator loaded into a node:vm context with the cohort in `state`):
   - optionSweepDefinitions() lists {name, def, freeText} in the grid's order;
   - optionSweepCases(options) translates each with the context's own `storeRequest` (and `options`, none by default),
     as {name, query, conditions, windows}, or {name, refused: code} for a refusal by name ('UNNAMED: ...' otherwise). */
function optionSweepDefinitions() {
  const subjects = {
    'any seizure': {concept: 'seizure', occ: 'any'}, 'first seizure': {concept: 'seizure', occ: 'first'},
    'last seizure': {concept: 'seizure', occ: 'last'}, '1st seizure': {concept: 'seizure', occ: 'nth', n: 1},
    '2nd seizure': {concept: 'seizure', occ: 'nth', n: 2}, '1st-to-last seizure': {concept: 'seizure', occ: 'nth_last', n: 1},
    '2nd-to-last seizure': {concept: 'seizure', occ: 'nth_last', n: 2},
    '1 seizure within a month': {concept: 'seizure', occ: 'count', n: 1, within: {amount: 1, unit: 'month'}},
    '3 seizures within 6 months': {concept: 'seizure', occ: 'count', n: 3, within: {amount: 6, unit: 'month'}},
    '2 febrile seizures in the period': {concept: 'febrile_seizure', occ: 'count', n: 2, within: 'period'},
    'first febrile seizure': {concept: 'febrile_seizure', occ: 'first'}, 'first Dravet diagnosis': {concept: 'dravet', occ: 'first'},
    'any blocker start': {concept: 'sodium_channel_blocker', occ: 'any'}, 'first blocker start': {concept: 'sodium_channel_blocker', occ: 'first'},
    'last blocker start': {concept: 'sodium_channel_blocker', occ: 'last'}, '2nd blocker start': {concept: 'sodium_channel_blocker', occ: 'nth', n: 2},
    'first carbamazepine start': {concept: 'carbamazepine', occ: 'first'}, '2nd-to-last valproate start': {concept: 'valproate', occ: 'nth_last', n: 2},
    'SCN1A result': {concept: 'gene:SCN1A'},
    'diary rises x2': {concept: 'seizure_frequency', cond: 'rise', param: 2}, 'diary falls /2': {concept: 'seizure_frequency', cond: 'fall', param: 2},
    'diary rises x1': {concept: 'seizure_frequency', cond: 'rise', param: 1}, 'diary rises x1.2': {concept: 'seizure_frequency', cond: 'rise', param: 1.2},
    'diary at least 8': {concept: 'seizure_frequency', cond: 'above', param: 8}, 'diary at most 2': {concept: 'seizure_frequency', cond: 'below', param: 2},
    'weight at least 10': {concept: 'weight', cond: 'above', param: 10}, 'weight at most 9': {concept: 'weight', cond: 'below', param: 9},
    'height at least 80': {concept: 'height', cond: 'above', param: 80}, 'head circumference at most 40': {concept: 'head_circ', cond: 'below', param: 40},
    'weight at most -1': {concept: 'weight', cond: 'below', param: -1}, 'weight at least 0': {concept: 'weight', cond: 'above', param: 0},
  };
  const relations = {
    'alone': () => ({nodes: [], edges: []}),
    'then a febrile seizure within 12 months': () => ({nodes: [{id: 'B', concept: 'febrile_seizure', occ: 'any'}], edges: [{from: 'A', to: 'B', rel: 'after', min: 0, max: 12, unit: 'month'}]}),
    'then a febrile seizure at least a day later': () => ({nodes: [{id: 'B', concept: 'febrile_seizure', occ: 'any'}], edges: [{from: 'A', to: 'B', rel: 'after', min: 1, max: null, unit: 'day'}]}),
    'and a febrile seizure up to 2 years either side': () => ({nodes: [{id: 'B', concept: 'febrile_seizure', occ: 'any'}], edges: [{from: 'A', to: 'B', rel: 'after', min: -24, max: 24, unit: 'month'}]}),
    'and a seizure while taking it, or 30 days after': () => ({nodes: [{id: 'B', concept: 'seizure', occ: 'any'}], edges: [{from: 'A', to: 'B', rel: 'during', grace: 30, unit: 'day'}]}),
    'within 6 months of the period start': () => ({nodes: [], edges: [{from: 'START', to: 'A', rel: 'after', min: 0, max: 6, unit: 'month'}]}),
    'within a year either side of the period end': () => ({nodes: [], edges: [{from: 'END', to: 'A', rel: 'after', min: -12, max: 12, unit: 'month'}]}),
    'after the first blocker, within 60 days': () => ({nodes: [{id: 'B', concept: 'sodium_channel_blocker', occ: 'first'}], edges: [{from: 'B', to: 'A', rel: 'after', min: 0, max: 60, unit: 'day'}]}),
  };
  const windows = {
    'the first seizure for 24 months': defaultDef().window,
    'birth to 6 years': {start: {kind: 'birth'}, end: {kind: 'age', amount: 6, unit: 'year'}},
    'from 2020-01-01 for 3 years': {start: {kind: 'date', date: '2020-01-01'}, end: {kind: 'span', amount: 3, unit: 'year'}},
    '12 months up to the last seizure': {start: {kind: 'span', amount: 12, unit: 'month'}, end: {kind: 'event', event: 'seizure', which: 'last', n: 1, offset: 0, unit: 'month'}},
    'a month before the 2nd-to-last febrile seizure to 10 years': {start: {kind: 'event', event: 'febrile_seizure', which: 'nth_last', n: 2, offset: -1, unit: 'month'}, end: {kind: 'age', amount: 10, unit: 'year'}},
    'the first diagnosis for 2 years': {start: {kind: 'event', event: '@any_diagnosis', which: 'first', n: 1, offset: 0, unit: 'month'}, end: {kind: 'span', amount: 2, unit: 'year'}},
    'the first genetic result for 2 years': {start: {kind: 'event', event: '@any_genetic', which: 'first', n: 1, offset: 0, unit: 'month'}, end: {kind: 'span', amount: 2, unit: 'year'}},
  };
  const out = [];
  const add = (name, def, freeText) => { if (!analyse(def).errors.length) out.push({name, def, freeText}); };
  for (const [s, subject] of Object.entries(subjects)) for (const [r, rel] of Object.entries(relations)) for (const freeText of ['present', 'off']) {
    const extra = rel();
    add([s, r, freeText].join(' / '), {...defaultDef(), nodes: [{id: 'A', ...subject}, ...extra.nodes], edges: extra.edges}, freeText);
  }
  for (const s of Object.keys(subjects)) add([s, 'alone', 'possible'].join(' / '), {...defaultDef(), nodes: [{id: 'A', ...subjects[s]}], edges: []}, 'possible');
  const probes = {'first seizure then a febrile seizure': ['first seizure', 'then a febrile seizure within 12 months'],
                  'a blocker then the diary rising': ['diary rises x2', 'after the first blocker, within 60 days'],
                  '2 febrile seizures in the period': ['2 febrile seizures in the period', 'alone']};
  for (const [p, [s, r]] of Object.entries(probes)) for (const scope of Object.keys(SCOPES)) for (const [w, window] of Object.entries(windows)) {
    const extra = relations[r]();
    add([p, scope, w].join(' / '), {window: clone(window), scope, nodes: [{id: 'A', ...subjects[s]}, ...extra.nodes], edges: extra.edges},
        s.includes('in the period') ? 'off' : 'present');
  }
  return out;
}

function optionSweepCases(options) {
  const pids = [...state.patients.keys()];
  return optionSweepDefinitions().map(({name, def, freeText}) => {
    const before = state.freeText;
    state.freeText = freeText;
    try {
      const {query, conditions, windows} = storeRequest(def, freeText, pids, options);
      return {name, query, conditions, windows};
    } catch (e) {
      return {name, refused: e instanceof StoreRefusal ? e.code : 'UNNAMED: ' + e.message};
    } finally { state.freeText = before; }
  });
}
