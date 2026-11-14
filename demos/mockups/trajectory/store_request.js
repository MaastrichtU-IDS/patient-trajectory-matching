/* The trajectory page's query in the execution store's request language (D29 stage 1; D32).

   `storeRequest(def, freeText, pids)` translates a page definition into a store match request: the engine query, the
   store's `conditions` (D28 value conditions; D32 Nth occurrences and counts) and the index windows the page placed,
   in whole minutes from each patient's birth (store_parity.py converts them to the store's clock). A page feature the
   store cannot express is refused by name (`StoreRefusal`, its `code`), never translated approximately:
   - GENE_CLASSIFICATION_UNSUPPORTED: gene steps (the first report that is not negative, timed by its recording);
   - RELATIVE_CONDITION_UNSUPPORTED: rises and falls against earlier values (planned as D33);
   - FREE_TEXT_HEDGED_UNSUPPORTED: free text read as "possible" (hedged) findings, which the converter never emits;
   - FREE_TEXT_COUNT_UNSUPPORTED: an Nth occurrence (N > 1) or a count asked over stated free text. The store counts and
     ranks coded records only (a letter mention ranges from birth to its letter, D30, which would leave almost every
     count and rank uncertain), so these need the page's "coded only" setting;
   - NTH_STEP_HAS_NO_END: "while taking" from an Nth step (its time is a start; its end belongs to no known record).
   Everything else is translated:
   - an Nth occurrence with N = 1 is the engine's first (Nth from last: last); with N > 1, the store's
     `occurrence: {nth | nth_last: N}`;
   - "at least N times within a span" or "in the index period" is the store's `count`; the page's one-fewer relaxation
     (`count:<step>`) is `widen_count` to N - 1, and the page's pair "parent concept and one fewer" (`node:<step>` with
     `count:<step>`), which the store's one-relaxation-per-step rule cannot combine, is one `widen_count` naming the
     parent concept, identified as `node:<step>+count:<step>`, at the pair's cost;
   - the rest as before D32: concepts, a parent concept or a drug class as `widen_concept`, absolute value conditions
     with a literal unit and their relaxation as `widen_value`, the page's relations and their relaxations.
   On the store, counts and occurrences are over recorded events, not over the page's reconciled happenings; the page
   must say so when it shows the store's answer. */
const STORE_UNIT = {seizure_frequency: '/month', weight: 'kg', height: 'cm', head_circ: 'cm'};

class StoreRefusal extends Error {
  constructor(code, detail) { super(detail ? `${code}: ${detail}` : code); this.code = code; }
}

function storeDecimal(x) {
  const s = String(x);
  if (!/^-?[0-9]+(\.[0-9]+)?$/.test(s)) throw new Error('not a plain decimal: ' + s);
  return s;
}

const derivedOcc = n => (n.occ === 'nth' || n.occ === 'nth_last') && Math.max(1, +n.n || 1) > 1;

function storeRefusals(def, freeText) {
  if (freeText === 'possible') throw new StoreRefusal('FREE_TEXT_HEDGED_UNSUPPORTED');
  for (const n of def.nodes) {
    const kind = stepKind(n);
    if (kind === 'gene') throw new StoreRefusal('GENE_CLASSIFICATION_UNSUPPORTED', n.id);
    if (kind === 'measure' && relative(n.cond)) throw new StoreRefusal('RELATIVE_CONDITION_UNSUPPORTED', n.id);
    if ((n.occ === 'count' || derivedOcc(n)) && kind !== 'measure' && freeText !== 'off')
      throw new StoreRefusal('FREE_TEXT_COUNT_UNSUPPORTED', n.id);
    if (derivedOcc(n) && kind !== 'measure' && def.edges.some(e => e.rel === 'during' && e.from === n.id))
      throw new StoreRefusal('NTH_STEP_HAS_NO_END', n.id);
  }
}

function storeRequest(def, freeText, pids) {
  storeRefusals(def, freeText);
  const req = engineRequest(def, pids, true), q = req.body.query, byId = nodeById(def);
  const steps = def.nodes.map(n => {
    const kind = stepKind(n);
    let occurrence = kind === 'measure' || n.occ === 'count' ? 'any' : (n.occ || 'any');
    if (occurrence === 'nth' || occurrence === 'nth_last') occurrence = derivedOcc(n) ? 'any' : (occurrence === 'nth' ? 'first' : 'last');
    return {id: n.id, concept: n.concept, occurrence};
  });
  const steps11 = {}, relax11 = [], relaxations = [], valueRelax = [];
  for (const n of def.nodes) {
    if (stepKind(n) === 'measure') {
      steps11[n.id] = {unit: STORE_UNIT[n.concept], [n.cond === 'above' ? 'ge' : 'le']: storeDecimal(n.param)};
    } else if (n.occ === 'count') {
      const within = n.within === 'period' ? 'window' : {span: toGrid(toDays(n.within.amount, n.within.unit))};
      steps11[n.id] = {count: {at_least: Math.max(1, +n.n || 2), within}};
    } else if (derivedOcc(n)) {
      steps11[n.id] = {occurrence: {[n.occ]: Math.max(1, +n.n || 1)}};
    }
  }
  const parent = n => stepKind(n) === 'medicine' ? classesOf(n.concept).find(k => k !== 'antiseizure') : state.concepts[n.concept].parents[0];
  const ids = new Set(q.relaxations.map(x => x.id));
  for (const x of q.relaxations) {
    if (x.kind !== 'widen_concept') { relaxations.push(x); continue; }
    const n = byId[x.step], kind = stepKind(n), what = x.id.split(':')[0];
    if (kind === 'measure') {
      valueRelax.push({id: x.id, kind: 'widen_value', step: n.id, cost: x.cost,
        condition: {unit: STORE_UNIT[n.concept], [n.cond === 'above' ? 'ge' : 'le']: storeDecimal(relaxedParam(n))}});
    } else if (what === 'count') {
      relax11.push({id: x.id, kind: 'widen_count', step: n.id, cost: x.cost, at_least: Math.max(1, +n.n || 2) - 1});
      const node = `node:${n.id}`;
      if (ids.has(node)) {
        const cost = String(+q.relaxations.find(y => y.id === node).cost + +x.cost);
        if (+cost <= +q.budget) relax11.push({id: `${node}+${x.id}`, kind: 'widen_count', step: n.id, cost,
          at_least: Math.max(1, +n.n || 2) - 1, concept: parent(n)});
      }
    } else if (what === 'node') {
      relaxations.push({...x, concept: parent(n)});
    } else throw new Error('no store form for ' + x.id);
  }
  const windows = {};
  for (const pid of pids) { const w = req.windows.get(pid); windows[pid] = w.reason ? null : {start: toGrid(w.start), end: toGrid(w.end)}; }
  let conditions = null;
  if (Object.keys(steps11).length) {
    // Always 1.1 (D32 review): a value condition on a snapshot built without series is then refused by name.
    conditions = {profile: 'execution-store-conditions-1.1', steps: steps11};
    const all = [...valueRelax, ...relax11];
    if (all.length) conditions.relaxations = all;
  }
  return {query: {...q, steps, relaxations}, conditions, windows};
}
