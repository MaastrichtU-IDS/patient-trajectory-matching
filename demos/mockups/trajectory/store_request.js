/* The trajectory page's query in the execution store's request language (D29 stage 1; D32; D33; D37).

   `storeRequest(def, freeText, pids, options)` translates a page definition into a store match request: the engine query, the
   store's `conditions` (D28 value conditions; D32 Nth occurrences and counts; D33 rises and falls) and the index
   windows the page placed,
   in whole minutes from each patient's birth (store_parity.py converts them to the store's clock). A page feature the
   store cannot express is refused by name (`StoreRefusal`, its `code`), never translated approximately:
   - GENE_CLASSIFICATION_UNSUPPORTED: gene steps (the first report that is not negative, timed by its recording), only
     when a caller asks for none (`options.genes: false`); since D38 (STORE_GENES) a
     gene step is the store's `gene` condition, profile execution-store-conditions-gene-1.0 (store_conditions_gene.py):
     each coded genetic result of the gene bound at its first report that is not negative, across reclassification, as
     a point at that report's recording time, never a letter's mention;
   - OCCURRENCE_ON_GENE_STEP (D38): a gene step with an occurrence other than any (the store refuses it too);
   - GENE_WITH_CODED_ONLY_UNSUPPORTED (D38 review): a gene step under "coded records only", unless the store's own
     coded-only option is in force (`options.codedOnly: 'store'`, the default since D49) and the joint profile is
     available (D42, STORE_GENE_CODED_ONLY, the default since D49, or `options.geneCodedOnly: true`): then the request is profile
     execution-store-conditions-gene-coded-1.0 (gene steps as D38, every other step over coded records only as D39);
   - RELATIVE_CONDITION_UNSUPPORTED: rises and falls, only when a caller asks for none (`options.rises: false`); since
     D36 they are translated by default (STORE_RISES), as the store's relative conditions (D33, shipped in D36);
   - FREE_TEXT_HEDGED_UNSUPPORTED: free text read as "possible" (hedged) findings, which the converter never emits;
   - FREE_TEXT_COUNT_UNSUPPORTED: an Nth occurrence (N > 1) or a count asked over stated free text. The store counts and
     ranks coded records only (a letter mention ranges from birth to its letter, D30, which would leave almost every
     count and rank uncertain), so these need the page's "coded only" setting;
   - NTH_STEP_HAS_NO_END: "while taking" from an Nth step (its time is a start; its end belongs to no known record);
   - FREE_TEXT_CODED_ONLY_UNSUPPORTED (D37): "coded records only" on a query with a step that is neither derived (an Nth
     with N > 1, a count) nor a measurement. The store reads the converter's letter mentions for every such step, so the
     option would silently become "also stated findings" there. `options.codedOnly: 'derived'` keeps D32's reading
     (coded only for derived steps, stated findings elsewhere). Since D39 the store can leave letter mentions out of
     every step (profile 1.3, `exclude_kinds: ["mention"]`, patterns/store_conditions_coded.py): `options.codedOnly:
     'store'` translates "coded only" so, and since D49 it is the default (STORE_CODED_ONLY, the owner's decision on D45's
     verdict); `options.codedOnly: false` (or null) asks for the named refusal;
   - VALUE_NOT_A_PLAIN_DECIMAL (D37): a threshold or factor that does not print as a plain decimal (such as 1e+21);
   - since D52, an index period placed by an event is written as a hidden step of the query (STORE_INDEX_ANCHOR; the
     block at the end of this file); a shape it cannot write keeps D49's point window, marked with its reason
     (INDEX_ANCHOR_ANY_UNSUPPORTED, INDEX_ANCHOR_GENE_UNSUPPORTED, INDEX_PERIOD_COUNT_UNSUPPORTED,
     INDEX_DATE_START_WITH_EVENT_END_UNSUPPORTED, FREE_TEXT_COUNT_UNSUPPORTED, FREE_TEXT_CODED_ONLY_UNSUPPORTED).
   Everything else is translated:
   - an Nth occurrence with N = 1 is the engine's first (Nth from last: last); with N > 1, the store's
     `occurrence: {nth | nth_last: N}`;
   - "at least N times within a span" or "in the index period" is the store's `count`; the page's one-fewer relaxation
     (`count:<step>`) is `widen_count` to N - 1, and the page's pair "parent concept and one fewer" (`node:<step>` with
     `count:<step>`), which the store's one-relaxation-per-step rule cannot combine, is one `widen_count` naming the
     parent concept, identified as `node:<step>+count:<step>`, at the pair's cost;
   - a rise or fall (D33) is the store's `relative` condition with the page's own rule: the mean of the one or two
     readings before (k = 2) within 75 days, a floor of 1 for a rise and 10^-9 for a fall (the page's max(1, base) and
     max(1e-9, base)), in the measurement's literal unit; the page's relaxed factor is `widen_factor` (left out when it
     is not smaller, since it would relax nothing). The store reads the diary's monthly counts as periods, ordered by
     their start, as the page does by their earliest day; it reads every record, not one per happening, and decides
     a rise as certain or only possible over the records' times (the two-pass rule);
   - the rest as before D32: concepts, a parent concept or a drug class as `widen_concept`, absolute value conditions
     with a literal unit and their relaxation as `widen_value`, the page's relations and their relaxations.
   On the store, counts and occurrences are over recorded events, not over the page's reconciled happenings; the page
   must say so when it shows the store's answer. Since D37 the page sends this request to the server
   (`POST /api/trajectory-store/match`, store_trajectory.py), which answers from the store; a refusal here is shown as
   the page's in-browser fallback, labelled with its code. */
const STORE_UNIT = {seizure_frequency: '/month', weight: 'kg', height: 'cm', head_circ: 'cm'};

class StoreRefusal extends Error {
  constructor(code, detail) { super(detail ? `${code}: ${detail}` : code); this.code = code; }
}

function storeDecimal(x) {
  const s = String(x);
  if (!/^-?[0-9]+(\.[0-9]+)?$/.test(s)) throw new StoreRefusal('VALUE_NOT_A_PLAIN_DECIMAL', s);
  return s;
}

// D36: a rise or fall is translated by default (the store's profile 1.2, `store_conditions_relative`), since D36's
// verdict (every rule met); a caller may still ask for the named refusal (`options.rises: false`).
const STORE_RISES = true;
// D38: a gene step is translated by default since D38's verdict (every rule met); a caller may still ask for the named
// refusal (`options.genes: false`, GENE_CLASSIFICATION_UNSUPPORTED).
const STORE_GENES = true;
const GENE_PROFILE = 'execution-store-conditions-gene-1.0';
const GENE_CONDITION = {report: 'first_not_negative', timed_by: 'recorded_at'};
// D39: "coded only" as the store's own exclusion of letter mentions from every step (profile 1.3). The default since D49:
// D45 re-met D39's rules ("ready"), and the owner decided the flip (2026-11-20), together with STORE_GENE_CODED_ONLY.
// A caller may still ask for D37's named refusal (`options.codedOnly: false` or null) or D32's reading ('derived').
const STORE_CODED_ONLY = true;
const codedOnlyMode = options => options.codedOnly === undefined ? (STORE_CODED_ONLY ? 'store' : null) : options.codedOnly;
// D42: a gene step under the store's own "coded only" (codedOnly: 'store') as the joint profile. The default since D49,
// flipped together with STORE_CODED_ONLY (D45: the joint profile is ready with it), so no page template under "coded
// only" is refused; a caller may still ask for D38's named refusal (`options.geneCodedOnly: false`).
const STORE_GENE_CODED_ONLY = true;
const GENE_CODED_PROFILE = 'execution-store-conditions-gene-coded-1.0';
const jointGenes = options => codedOnlyMode(options) === 'store'
  && (options.geneCodedOnly === undefined ? STORE_GENE_CODED_ONLY : options.geneCodedOnly);
const derivedOcc = n => (n.occ === 'nth' || n.occ === 'nth_last') && Math.max(1, +n.n || 1) > 1;
// D33: the page's rise and fall rule (shared/core.js, measureEvents) in the store's terms.
const RELATIVE_PREVIOUS = 2, RELATIVE_WITHIN_DAYS = 75;
const relativeFloor = cond => cond === 'rise' ? '1' : '0.000000001';
const relativeCondition = (n, factor) => ({kind: n.cond, factor: storeDecimal(factor), unit: STORE_UNIT[n.concept],
  floor: relativeFloor(n.cond), baseline: {previous: RELATIVE_PREVIOUS, within: toGrid(RELATIVE_WITHIN_DAYS)}});

function storeRefusals(def, freeText, options = {}) {
  if (freeText === 'possible') throw new StoreRefusal('FREE_TEXT_HEDGED_UNSUPPORTED');
  const rises = options.rises === undefined ? STORE_RISES : options.rises;
  const genes = options.genes === undefined ? STORE_GENES : options.genes;
  for (const n of def.nodes) {
    const kind = stepKind(n);
    if (kind === 'gene' && !genes) throw new StoreRefusal('GENE_CLASSIFICATION_UNSUPPORTED', n.id);
    if (kind === 'gene' && n.occ && n.occ !== 'any') throw new StoreRefusal('OCCURRENCE_ON_GENE_STEP', n.id);
    if (kind === 'gene' && freeText === 'off' && !jointGenes(options)) throw new StoreRefusal('GENE_WITH_CODED_ONLY_UNSUPPORTED', n.id);
    if (kind === 'measure' && relative(n.cond) && !rises) throw new StoreRefusal('RELATIVE_CONDITION_UNSUPPORTED', n.id);
    if ((n.occ === 'count' || derivedOcc(n)) && kind !== 'measure' && freeText !== 'off')
      throw new StoreRefusal('FREE_TEXT_COUNT_UNSUPPORTED', n.id);
    if (derivedOcc(n) && kind !== 'measure' && def.edges.some(e => e.rel === 'during' && e.from === n.id))
      throw new StoreRefusal('NTH_STEP_HAS_NO_END', n.id);
  }
  // D37: without D39's exclusion the store reads letter mentions for every step that is not derived, so "coded only"
  // cannot be honoured there.
  const mode = codedOnlyMode(options);
  if (freeText === 'off' && mode !== 'derived' && mode !== 'store')
    for (const n of def.nodes)
      if (stepKind(n) !== 'measure' && n.occ !== 'count' && !derivedOcc(n)) throw new StoreRefusal('FREE_TEXT_CODED_ONLY_UNSUPPORTED', n.id);
}

function storeRequest(def, freeText, pids, options = {}) {
  storeRefusals(def, freeText, options);
  if (indexAnchorOn(options) && hasEventBound(def.window)) {
    // D52 review, S2: a shape the rewrite cannot write keeps D49's point window, marked, so the store still answers it.
    const point = indexPointReason(def, freeText, options);
    if (point) return {...baseRequest(def, freeText, pids, options), index_anchor: {mode: 'point', reason: point}};
    INDEX_REWRITES.calls += 1;
    return indexRewrite(baseRequest(def, freeText, pids, options), def, pids, options);
  }
  return baseRequest(def, freeText, pids, options);
}

// D49's translation: the page's point windows (D46's rule), unchanged. D52 rewrites it when the period is event-placed.
function baseRequest(def, freeText, pids, options) {
  const req = engineRequest(def, pids, true), q = req.body.query, byId = nodeById(def);
  const steps = def.nodes.map(n => {
    const kind = stepKind(n);
    let occurrence = kind === 'measure' || n.occ === 'count' ? 'any' : (n.occ || 'any');
    if (occurrence === 'nth' || occurrence === 'nth_last') occurrence = derivedOcc(n) ? 'any' : (occurrence === 'nth' ? 'first' : 'last');
    return {id: n.id, concept: n.concept, occurrence};
  });
  const steps11 = {}, relax11 = [], relaxations = [], valueRelax = [];
  for (const n of def.nodes) {
    if (stepKind(n) === 'measure' && relative(n.cond)) {
      steps11[n.id] = {relative: relativeCondition(n, n.param)};
    } else if (stepKind(n) === 'measure') {
      steps11[n.id] = {unit: STORE_UNIT[n.concept], [n.cond === 'above' ? 'ge' : 'le']: storeDecimal(n.param)};
    } else if (n.occ === 'count') {
      const within = n.within === 'period' ? 'window' : {span: toGrid(toDays(n.within.amount, n.within.unit))};
      steps11[n.id] = {count: {at_least: Math.max(1, +n.n || 2), within}};
    } else if (derivedOcc(n)) {
      steps11[n.id] = {occurrence: {[n.occ]: Math.max(1, +n.n || 1)}};
    } else if (stepKind(n) === 'gene') {
      steps11[n.id] = {gene: {...GENE_CONDITION}};  // D38
    }
  }
  const parent = n => stepKind(n) === 'medicine' ? classesOf(n.concept).find(k => k !== 'antiseizure') : state.concepts[n.concept].parents[0];
  const ids = new Set(q.relaxations.map(x => x.id));
  for (const x of q.relaxations) {
    if (x.kind !== 'widen_concept') { relaxations.push(x); continue; }
    const n = byId[x.step], kind = stepKind(n), what = x.id.split(':')[0];
    if (kind === 'measure' && relative(n.cond)) {
      if (+relaxedParam(n) < +n.param)
        valueRelax.push({id: x.id, kind: 'widen_factor', step: n.id, cost: x.cost, factor: storeDecimal(relaxedParam(n))});
    } else if (kind === 'measure') {
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
  const coded = freeText === 'off' && codedOnlyMode(options) === 'store';
  if (Object.keys(steps11).length || coded) {
    // At least 1.1 (D32 review): a value condition on a snapshot built without series is then refused by name; 1.2
    // when a step rises or falls (D33); 1.3 with letter mentions left out of every step under "coded only" (D39); with a
    // gene step, the gene profile (D38), or under "coded only" the joint profile (D42; refused above unless available,
    // GENE_WITH_CODED_ONLY_UNSUPPORTED).
    const rises = Object.values(steps11).some(s => s.relative), gene = Object.values(steps11).some(s => s.gene);
    conditions = {profile: gene ? (coded ? GENE_CODED_PROFILE : GENE_PROFILE) : coded ? 'execution-store-conditions-1.3' : rises ? 'execution-store-conditions-1.2'
                    : 'execution-store-conditions-1.1',
                  steps: steps11};
    const all = [...valueRelax, ...relax11];
    if (all.length) conditions.relaxations = all;
    if (coded) conditions.exclude_kinds = ['mention'];
  }
  return {query: {...q, steps, relaxations}, conditions, windows};
}

/* D52: the index anchor as a hidden step of the query (D46's option (c), for P2).

   An index period placed by an event is not sent as the page's point window. Each event bound becomes a hidden step
   (`@index_start` for the start's event, `@index_end` for the end's when it is another), whose occurrence is the bound's: first and last
   natively, an Nth or Nth-from-last with N > 1 as D32's derived occurrence. The period's scope, its eligibility and
   every relation from START or END are written as relations from that step, so the engine places the period in each
   world, at the time the store's own records allow for the event there. Each patient gets a constant base window:
   birth (or the constant start) to last contact (or the constant end). The server narrows it to the hull of the
   periods the records allow (`index_hull_v1`, index_hull.py), which changes coverage, D44's admission scope and
   pruning, never what the request matches (decision log, D52, design §2).

   The rule `index_anchor_v1`, after spans are resolved (start = anchor + kP or a constant, end = anchor + kQ or a
   constant; offsets in grid minutes, each rounded once from its summed days):
   - followed to the end (the page's last-contact rule): relation anchor(end) -> END, min kQ, with END = last contact;
   - not born after the end: relation START -> anchor(end), min -kQ (START = 0); with a constant start C >= 0 instead,
     ends after it: START -> anchor(end), min 1 - kQ (START = C);
   - ends after it starts: for two hidden steps, anchor(start) -> anchor(end), min kP - kQ + 1; with a constant end C,
     anchor(start) -> END, min kP + 1 (END = C); for one step, checked on the page (a span of at most 0 places nothing);
   - scope: the query's scope becomes `any` (it binds every step, the hidden ones too), and each user step X gets
     relations: for `from_start` and `within`, START -> X, min 0 (the clamp at birth, or the constant start) and
     anchor(start) -> X, min kP; for `within` also anchor(end) -> X, max kQ (with a constant end, X -> END, min 0); for
     `after`, anchor(end) -> X, min kQ (with a constant end, END -> X, min 0);
   - a relation START -> X [lo, hi] becomes anchor(start) -> X [kP + lo, kP + hi], with its id and its relaxation's
     maximum shifted alike: measured from the anchor plus its offset, *not clamped at birth* (the clamp limits where steps
     fall, through the scope, but not relations from the period's start; D52, Q2); END -> X shifts by kQ.
   Written with D49's point window instead, marked `index_anchor: {mode: 'point', reason}` (the store's answer holds for
   the window sent, D46's P2; the page says so), when the rewrite cannot write the shape (D52 review, S2; the reason
   codes):
   - INDEX_ANCHOR_ANY_UNSUPPORTED: "any diagnosis" or "any genetic result" as the event (no store concept holds them);
   - INDEX_ANCHOR_GENE_UNSUPPORTED: a gene as the event (the page's anchor is its first report of any class, by its
     recording, which the store holds as no event);
   - FREE_TEXT_COUNT_UNSUPPORTED (detail `@index_start`): an Nth event (N > 1) over stated free text (D32: the store
     ranks coded records only);
   - INDEX_PERIOD_COUNT_UNSUPPORTED: a count "in the index period" with an event-placed period (a count's window is
     constant);
   - INDEX_DATE_START_WITH_EVENT_END_UNSUPPORTED: a date start (or an age below 0) with an event end (a start that may
     precede birth would need a bound per patient);
   - FREE_TEXT_CODED_ONLY_UNSUPPORTED: "coded only" refused on request (`codedOnly: null` or false) with a hidden step
     that is not derived, which would read letter mentions.
   `options.indexAnchor: false` writes D49's request (the page's point windows) exactly. */
const STORE_INDEX_ANCHOR = true;
const INDEX_RULE = 'index_anchor_v1';
const INDEX_STEPS = {start: '@index_start', end: '@index_end'};
const INDEX_REWRITES = {calls: 0};  // the census's structural check: 0 calls for a period with no event bound
const indexAnchorOn = options => options.indexAnchor === undefined ? STORE_INDEX_ANCHOR : !!options.indexAnchor;
const hasEventBound = w => !!w && (w.start.kind === 'event' || w.end.kind === 'event');
// An event bound's occurrence as the store writes it: first and last natively, N > 1 as a derived occurrence.
function anchorOccurrence(b) {
  const which = b.which || 'first', n = Math.max(1, +b.n || 1);
  if (which === 'first' || (which === 'nth' && n === 1)) return {occurrence: 'first'};
  if (which === 'last' || (which === 'nth_last' && n === 1)) return {occurrence: 'last'};
  return {occurrence: 'any', derived: {[which]: n}};
}
const anchorKey = b => JSON.stringify([b.event, anchorOccurrence(b)]);

// The reason a period placed by an event keeps D49's point window, or null when the rewrite can write it.
function indexPointReason(def, freeText, options) {
  const w = def.window, bounds = [w.start, w.end].filter(b => b.kind === 'event');
  if (bounds.some(b => b.event === '@any_diagnosis' || b.event === '@any_genetic')) return 'INDEX_ANCHOR_ANY_UNSUPPORTED';
  if (bounds.some(b => state.concepts[b.event]?.kind === 'gene')) return 'INDEX_ANCHOR_GENE_UNSUPPORTED';
  const steps = indexSteps(w);
  if (steps.some(s => s.derived) && freeText !== 'off') return 'FREE_TEXT_COUNT_UNSUPPORTED';
  if (def.nodes.some(n => n.occ === 'count' && n.within === 'period')) return 'INDEX_PERIOD_COUNT_UNSUPPORTED';
  if (w.end.kind === 'event' && (w.start.kind === 'date' || (w.start.kind === 'age' && toDays(w.start.amount, w.start.unit) < 0)))
    return 'INDEX_DATE_START_WITH_EVENT_END_UNSUPPORTED';
  const mode = codedOnlyMode(options);
  if (freeText === 'off' && mode !== 'derived' && mode !== 'store' && steps.some(s => !s.derived)) return 'FREE_TEXT_CODED_ONLY_UNSUPPORTED';
  return null;
}

// The hidden steps of a window: one per distinct event bound (the same event, occurrence and N share one).
function indexSteps(w) {
  const out = [];
  const startEvent = w.start.kind === 'event', endEvent = w.end.kind === 'event';
  if (startEvent) out.push({id: INDEX_STEPS.start, bound: w.start, side: 'start', ...anchorOccurrence(w.start)});
  if (endEvent && !(startEvent && anchorKey(w.start) === anchorKey(w.end)))
    out.push({id: INDEX_STEPS.end, bound: w.end, side: 'end', ...anchorOccurrence(w.end)});
  return out;
}

/* The window as sides: each {step, offset} (grid minutes from the hidden step) or {constant: bound}. Offsets are summed
   in days, as windowFor sums them, and rounded once. */
function indexSides(w) {
  const steps = indexSteps(w);
  // The end's step is its own, or the start's when the two bounds name one event.
  const stepOf = side => (steps.find(s => s.side === side) || steps[0]).id;
  const days = b => toDays(b.offset, b.unit);
  let start, end;
  if (w.start.kind === 'event') start = {step: stepOf('start'), days: days(w.start)};
  else if (w.start.kind !== 'span') start = {constant: w.start};
  if (w.end.kind === 'event') end = {step: stepOf('end'), days: days(w.end)};
  else if (w.end.kind !== 'span') end = {constant: w.end};
  if (w.start.kind === 'span') start = {step: end.step, days: end.days - toDays(w.start.amount, w.start.unit)};
  if (w.end.kind === 'span') end = {step: start.step, days: start.days + toDays(w.end.amount, w.end.unit)};
  for (const x of [start, end]) if (x.step) x.offset = toGrid(x.days);
  return {start, end, steps};
}

// A constant bound's day for a patient (windowFor's own resolution).
const constantDay = (b, p) => b.kind === 'birth' ? 0 : b.kind === 'age' ? toDays(b.amount, b.unit) : dayOf(p, b.date);

/* One patient's base window in grid minutes, or {reason} when no period can be placed whatever the event's time: the
   page's static checks (windowFor's, in its order), on values that read no event. */
function indexBaseWindow(sides, p) {
  const last = dayOf(p, p.follow_up.last_contact);
  const {start, end} = sides;
  if (end.constant) {
    const c = constantDay(end.constant, p);
    if (c < 0) return {reason: 'born after the window ends'};
    if (start.step === undefined) return null;  // both constant: no rewrite (not reached)
    if (c > last) return {reason: `not followed to the end of the window (last contact at ${ageText(last)})`};
    return {start: 0, end: toGrid(c)};
  }
  if (start.step !== undefined && start.step === end.step && end.offset <= start.offset) return {reason: 'the window ends before it starts'};
  return {start: start.constant ? toGrid(constantDay(start.constant, p)) : 0, end: toGrid(last)};
}

function indexRewrite(base, def, pids, options) {
  const sides = indexSides(def.window), {start, end, steps} = sides;
  const q = base.query, relations = [], shift = {};
  const startVar = start.step !== undefined, endVar = end.step !== undefined;
  const after = (id, from, to, min, max) => ({id, kind: 'after', from, to, ...(min !== undefined ? {min} : {}), ...(max !== undefined ? {max} : {})});
  // User relations: START and END read from the hidden steps, ids kept.
  for (const r of q.relations) {
    const k = r.from === 'START' && startVar ? start : r.from === 'END' && endVar ? end : null;
    if (!k) { relations.push(r); continue; }
    shift[r.id] = k.offset;
    relations.push({...r, from: k.step, ...('min' in r ? {min: r.min + k.offset} : {}), ...('max' in r ? {max: r.max + k.offset} : {})});
  }
  // The scope, per user step, as relations (the query's scope binds every step, the hidden ones included, so it is
  // `any`): the clamp at birth (or the constant start) from START, the period's start from its step, its end from its
  // step (or the constant END).
  const scope = 'any';
  for (const n of def.nodes) {
    const s = q.scope;
    if (s === 'from_start' || s === 'within') {
      relations.push(after(`index:scope:${n.id}`, 'START', n.id, 0));
      const lo = startVar ? start.offset : undefined;
      const hi = s === 'within' && endVar ? end.offset : undefined;
      if (startVar && hi !== undefined && start.step === end.step && lo <= hi) relations.push(after(`index:scope-start:${n.id}`, start.step, n.id, lo, hi));
      else {
        if (startVar) relations.push(after(`index:scope-start:${n.id}`, start.step, n.id, lo));
        if (hi !== undefined) relations.push(after(`index:scope-end:${n.id}`, end.step, n.id, undefined, hi));
      }
      if (s === 'within' && !endVar) relations.push(after(`index:scope-end:${n.id}`, n.id, 'END', 0));
    } else if (s === 'after') {
      relations.push(endVar ? after(`index:scope-end:${n.id}`, end.step, n.id, end.offset) : after(`index:scope-end:${n.id}`, 'END', n.id, 0));
    }
  }
  // Eligibility.
  if (endVar) {
    relations.push(after('index:followed', end.step, 'END', end.offset));
    relations.push(start.constant ? after('index:ordered', 'START', end.step, 1 - end.offset) : after('index:born', 'START', end.step, -end.offset));
  }
  if (startVar && endVar && start.step !== end.step) relations.push(after('index:ordered', start.step, end.step, start.offset - end.offset + 1));
  if (startVar && end.constant) relations.push(after('index:ordered', start.step, 'END', start.offset + 1));
  const relaxations = q.relaxations.map(x => x.kind === 'extend_max' && x.relation in shift ? {...x, max: x.max + shift[x.relation]} : x);
  const query = {...q, scope, steps: [...q.steps, ...steps.map(s => ({id: s.id, concept: s.bound.event, occurrence: s.occurrence}))],
                 relations, relaxations};
  let conditions = base.conditions;
  const derived = steps.filter(s => s.derived);
  if (derived.length) {
    conditions = conditions ? {...conditions, steps: {...conditions.steps}} : {profile: 'execution-store-conditions-1.1', steps: {}};
    for (const s of derived) conditions.steps[s.id] = {occurrence: s.derived};
  }
  const windows = {};
  for (const pid of pids) {
    const w = indexBaseWindow(sides, state.patients.get(pid));
    windows[pid] = w.reason ? null : w;
  }
  const side = x => x.step !== undefined ? {step: x.step, offset: x.offset} : null;
  return {query, conditions, windows, index: {rule: INDEX_RULE, start: side(start), end: side(end)}};
}

// The static reason the page gives a patient under the rewrite (for the page's display), or null.
function indexStaticReason(def, p) {
  const w = indexBaseWindow(indexSides(def.window), p);
  return w && w.reason ? w.reason : null;
}
