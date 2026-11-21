'use strict';
/* Define a trajectory: the index period first, then a graph of steps and timing constraints.

   A sequence is a chain, a tree branches from one step into follow-ups with no order between
   them, and a graph lets a step depend on several earlier ones. All three are the same
   structure: steps bound to a patient's events, and constraints on the time between them.

   Each patient is matched as a simple temporal network. A step's time is the time of the
   event it is bound to, a variable bounded by the event's recorded range; a medicine also has
   an end. Steps bound to the same event share its time. Constraints are differences between
   variables. A binding is *possible* when some choice of times inside the recorded ranges
   satisfies every constraint jointly (checked exactly with shortest paths), and *exact* when
   every choice does. */

const ORDER = ['exact', 'possible', 'relaxed', 'incomparable', 'unresolved', 'none', 'excluded'];
const HIDDEN_BY_DEFAULT = ['none', 'excluded'];
const DEFAULT_FILTER = ORDER.filter(s => !HIDDEN_BY_DEFAULT.includes(s));
Object.assign(state, {lens: 'recorded', selected: null, def: null, results: [], filter: new Set(DEFAULT_FILTER), freeText: 'present',
  matcher: 'browser', engine: null, pending: false, engineError: null,
  // D37: the execution store under the page (its status, or why there is none), the last answer's identity, and
  // why this query fell back to the page's own search.
  store: null, storeError: null, storeMeta: null, fallback: null, pageAnswers: new Map()});

const ANCHORS = {START: 'Index start', END: 'Index end'};
const STEP_KINDS = ['phenotype', 'diagnosis', 'medication', 'drug_class', 'gene', 'measurement'];
const MEASURE_UNIT = {seizure_frequency: 'per month', weight: 'kg', height: 'cm', head_circ: 'cm'};
const stepKind = n => ({phenotype: 'occurrence', diagnosis: 'occurrence', medication: 'medicine', drug_class: 'medicine',
  gene: 'gene', measurement: 'measure'})[state.concepts[n.concept]?.kind];
const SCOPES = {any: 'anywhere in the record', from_start: 'from the start of the index period onward',
  within: 'inside the index period', after: 'after the index period ends'};
const STATUS = {
  exact: ['■', 'Exact match'], possible: ['◧', 'Possible · times uncertain'], relaxed: ['▲', 'Relaxed'],
  incomparable: ['?', 'Not comparable'], unresolved: ['◌', 'Unresolved · search stopped'], none: ['○', 'No recorded match'],
  excluded: ['–', 'No index period'],
};
const SEARCH_BUDGET = 40000;
const STOPPED = `the search stopped after ${SEARCH_BUDGET.toLocaleString('en')} attempts without finding or ruling out a match`;
// What a search that stopped early leaves open, for results it did not decide.
const STOPPED_NOTE = {possible: 'an exact binding may exist', relaxed: 'a cheaper or more certain relaxed match may exist',
  none: 'a relaxed match may exist'};

const TEMPLATES = {
  sequence: {
    label: 'Sequence', note: 'A, then B, then C: each step after the one before it.',
    nodes: [{id: 'A', concept: 'febrile_seizure', occ: 'first'}, {id: 'B', concept: 'sodium_channel_blocker', occ: 'first'},
            {id: 'C', concept: 'seizure_frequency', cond: 'rise', param: 2}],
    edges: [{from: 'A', to: 'B', rel: 'after', min: 0, max: 12, unit: 'month'}, {from: 'B', to: 'C', rel: 'after', min: 0, max: 60, unit: 'day'}],
  },
  tree: {
    label: 'Tree', note: 'The first seizure branches into a genetic result and a sodium-channel blocker, in either order; the rise follows the blocker.',
    nodes: [{id: 'A', concept: 'seizure', occ: 'first'}, {id: 'B', concept: 'gene:SCN1A'}, {id: 'C', concept: 'sodium_channel_blocker', occ: 'first'},
            {id: 'D', concept: 'seizure_frequency', cond: 'rise', param: 2}],
    edges: [{from: 'A', to: 'B', rel: 'after', min: 0, max: 2, unit: 'year'}, {from: 'A', to: 'C', rel: 'after', min: 0, max: 12, unit: 'month'},
            {from: 'C', to: 'D', rel: 'after', min: 0, max: 60, unit: 'day'}],
  },
  graph: {
    label: 'Graph', note: 'Stiripentol depends on two earlier steps: the rise while on a blocker, and the SCN1A result.',
    nodes: [{id: 'A', concept: 'seizure', occ: 'first'}, {id: 'B', concept: 'sodium_channel_blocker', occ: 'first'},
            {id: 'C', concept: 'seizure_frequency', cond: 'rise', param: 2}, {id: 'E', concept: 'gene:SCN1A'}, {id: 'D', concept: 'stiripentol', occ: 'any'}],
    edges: [{from: 'A', to: 'B', rel: 'after', min: 0, max: 12, unit: 'month'}, {from: 'B', to: 'C', rel: 'during', grace: 30, unit: 'day'},
            {from: 'C', to: 'D', rel: 'after', min: 0, max: 18, unit: 'month'}, {from: 'E', to: 'D', rel: 'after', min: 0, max: 18, unit: 'month'}],
  },
  frequency: {
    label: 'Event frequency', note: 'At least 3 recorded seizures within 6 months, then a sodium-channel blocker within 6 months of the third.',
    nodes: [{id: 'A', concept: 'seizure', occ: 'count', n: 3, within: {amount: 6, unit: 'month'}}, {id: 'B', concept: 'sodium_channel_blocker', occ: 'first'}],
    edges: [{from: 'A', to: 'B', rel: 'after', min: 0, max: 6, unit: 'month'}],
  },
};

// The Blank button's starting point.
const BLANK = {nodes: [{id: 'A', concept: 'seizure', occ: 'first'}], edges: []};

function defaultDef() {
  const t = clone(TEMPLATES.sequence);
  return {window: {start: {kind: 'event', event: 'seizure', which: 'first', n: 2, offset: 0, unit: 'month'}, end: {kind: 'span', amount: 24, unit: 'month'}},
          scope: 'from_start', nodes: t.nodes, edges: t.edges};
}

/* Structure */

const nodeById = def => Object.fromEntries(def.nodes.map(n => [n.id, n]));
const isAnchor = id => id in ANCHORS;
const finite = v => v !== '' && v != null && Number.isFinite(+v);

function analyse(def) {
  const errors = [], warnings = [];
  const ids = def.nodes.map(n => n.id), byId = nodeById(def);
  const inner = def.edges.filter(e => !isAnchor(e.from));
  def.nodes.forEach(n => { if (!stepKind(n)) errors.push(`Step ${n.id} has no event yet: search for one by name or code.`); });
  def.edges.forEach((e, k) => {
    const name = `Constraint ${k + 1}`;
    if (!(e.from in byId) && !isAnchor(e.from)) errors.push(`${name} starts from a step that no longer exists.`);
    if (!(e.to in byId)) errors.push(`${name} points to a step that no longer exists.`);
    if (e.from === e.to) errors.push(`${name} relates ${e.to} to itself.`);
    if (e.rel === 'during' && (isAnchor(e.from) || stepKind(byId[e.from] || {}) !== 'medicine')) errors.push(`${name}: "while taking" needs a medicine as its first step, because only a medicine has an end.`);
    if (e.rel === 'after' && finite(e.max) && finite(e.min) && +e.max < +e.min) errors.push(`${name}: the latest gap is smaller than the earliest.`);
  });
  const indeg = Object.fromEntries(ids.map(id => [id, 0])), outdeg = {...indeg}, adj = Object.fromEntries(ids.map(id => [id, []]));
  for (const e of inner) if (e.from in adj && e.to in adj && e.from !== e.to) { adj[e.from].push(e.to); indeg[e.to] += 1; outdeg[e.from] += 1; }
  const deg = {...indeg}, queue = ids.filter(id => !deg[id]), order = [];
  while (queue.length) { const id = queue.shift(); order.push(id); for (const t of adj[id]) if (--deg[t] === 0) queue.push(t); }
  if (order.length < ids.length) errors.push(`The constraints form a cycle (${ids.filter(id => !order.includes(id)).join(', ')}): a step cannot come after itself.`);
  const parent = Object.fromEntries(ids.map(id => [id, id]));
  const find = id => parent[id] === id ? id : (parent[id] = find(parent[id]));
  for (const e of inner) if (e.from in parent && e.to in parent) parent[find(e.from)] = find(e.to);
  const parts = new Set(ids.map(find)).size;
  const loose = ids.filter(id => !indeg[id] && !outdeg[id] && !def.edges.some(e => e.to === id));
  if (ids.length > 1 && loose.length) warnings.push(`${loose.join(', ')} ${loose.length > 1 ? 'are' : 'is'} not connected to any other step, so ${loose.length > 1 ? 'they match' : 'it matches'} independently.`);
  let shape = 'empty';
  if (errors.some(e => e.includes('cycle'))) shape = 'cycle';
  else if (ids.length === 1) shape = 'single step';
  else if (ids.length && parts === 1 && ids.every(id => indeg[id] <= 1 && outdeg[id] <= 1)) shape = 'sequence';
  else if (ids.length && parts === 1 && ids.every(id => indeg[id] <= 1)) shape = 'tree';
  else if (ids.length) shape = parts > 1 ? `graph in ${parts} separate parts` : 'graph';
  return {errors, warnings, shape, order, indeg, adj};
}

function nodeText(n) {
  const kind = stepKind(n), name = n.concept ? label(n.concept).toLowerCase() : 'unset';
  if (kind === 'occurrence') {
    if (n.occ === 'count') return `${n.n || 2}× ${name} ${n.within === 'period' ? 'in the index period' : `within ${amountText(n.within.amount, n.within.unit)}`}`;
    return `${n.occ && n.occ !== 'any' ? occurrenceText(n.occ, n.n) + ' ' : ''}${name}`;
  }
  if (kind === 'medicine') {
    const what = state.concepts[n.concept].kind === 'drug_class' ? `any ${n.concept === 'sodium_channel_blocker' ? 'sodium-channel blocker' : name}` : name;
    return n.occ && n.occ !== 'any' ? `${occurrenceText(n.occ, n.n)} start of ${what}` : `${what} started`;
  }
  if (kind === 'gene') return `${label(n.concept)} variant reported`;
  if (kind === 'measure') return `${name} ${CONDITIONS[n.cond]}${relative(n.cond) ? '' : ' '}${n.param}${relative(n.cond) ? '' : ' ' + (MEASURE_UNIT[n.concept] || '')}`;
  return name;
}

const gapText = (e, v) => amountText(v, e.unit || 'day');

function edgeText(e) {
  if (e.rel === 'during') return `while taking ${e.from}${+e.grace ? ` (or within ${gapText(e, e.grace)} after it stops)` : ''}`;
  const lo = finite(e.min) ? +e.min : null, hi = finite(e.max) ? +e.max : null, unit = `${e.unit || 'day'}s`;
  const ref = isAnchor(e.from) ? `the ${ANCHORS[e.from].toLowerCase()}` : e.from;
  if (lo != null && hi != null) return `${lo}–${hi} ${unit} after ${ref}`;
  if (lo != null) return `at least ${gapText(e, lo)} after ${ref}`;
  if (hi != null) return `at most ${gapText(e, hi)} after ${ref}`;
  return `any time after ${ref}`;
}

function reading(def, a) {
  if (a.errors.length || !def.nodes.length) return '';
  const byId = nodeById(def);
  let text;
  if (a.shape === 'sequence' && !def.edges.some(e => isAnchor(e.from))) {
    let id = a.order[0];
    const parts = [`${id} (${nodeText(byId[id])})`];
    for (;;) {
      const e = def.edges.find(x => x.from === id);
      if (!e) break;
      parts.push(e.rel === 'during' ? `then, while taking ${id}, ${e.to} (${nodeText(byId[e.to])})`
        : `then ${edgeText(e).replace(/ after \w+$/, ' later')} ${e.to} (${nodeText(byId[e.to])})`);
      id = e.to;
    }
    text = parts.join(', ');
  } else {
    text = a.order.map(id => {
      const ins = def.edges.filter(e => e.to === id);
      return `${id}, ${nodeText(byId[id])}${ins.length ? ': ' + ins.map(edgeText).join(' and ') : ''}`;
    }).join('; ');
  }
  return `${text}. Every step falls ${SCOPES[def.scope]}.`;
}

/* Candidates: the events a step can be bound to in one patient */

const point = (e, l, what, item) => ({e, l, fe: e, fl: l, what, item, point: true});
const relaxedParam = n => relative(n.cond) ? Math.max(1.1, Math.round(n.param * 7.5) / 10) : +(n.param * (n.cond === 'above' ? 0.9 : 1.1)).toFixed(2);

function candidates(view, n, relax, win) {
  const q = {freeText: state.freeText};
  // Under "Coded records only" each item as its coded records alone make it (D53, core.js's searchable).
  const items = searchable(view, q);
  const kind = stepKind(n);
  let out = [];
  if (kind === 'occurrence') {
    const target = relax.has(`node:${n.id}`) ? (state.concepts[n.concept].parents[0] || n.concept) : n.concept;
    const evs = items.filter(i => (i.kind === 'phenotype' || i.kind === 'diagnosis') && i.time.best && isA(conceptFor(i, q), target))
      .sort((a, b) => a.time.best.e - b.time.best.e);
    if (n.occ === 'count') {
      // The step happens when the count is reached: at the Nth event of a run inside the span.
      const need = Math.max(1, (+n.n || 2) - (relax.has(`count:${n.id}`) ? 1 : 0));
      const pool = n.within === 'period' ? evs.filter(i => i.time.best.l >= win.start && i.time.best.e <= win.end) : evs;
      const span = n.within === 'period' ? Infinity : toDays(n.within.amount, n.within.unit);
      const seen = new Set();
      for (let a = 0; a + need - 1 < pool.length; a++) {
        const first = pool[a], last = pool[a + need - 1];
        if (last.time.best.e - first.time.best.l > span || seen.has(last.id)) continue;
        seen.add(last.id);
        out.push(point(last.time.best.e, last.time.best.l, `${need} × ${last.display.toLowerCase()}, ${ageText(first.time.best.e)}–${ageText(last.time.best.e)}`, last));
      }
    } else out = evs.map(i => point(i.time.best.e, i.time.best.l, i.display, i));
  } else if (kind === 'medicine') {
    const isClass = state.concepts[n.concept].kind === 'drug_class';
    const cls = isClass ? n.concept : relax.has(`node:${n.id}`) ? classesOf(n.concept).find(k => k !== 'antiseizure') : null;
    out = items.filter(i => i.kind === 'medication' && i.med.start && (cls ? classesOf(i.concept).includes(cls) : i.concept === n.concept))
      .map(i => {
        const m = i.med;
        return {e: m.start.e, l: m.start.l, fe: m.end ? m.end.e : view.lastContact, fl: m.end ? m.end.l : Infinity, item: i, point: false,
                what: `${i.display}, ${ageText(m.start.e)}${m.end ? `–${ageText(m.end.e)}` : ' onward'}`};
      });
  } else if (kind === 'gene') {
    out = items.filter(i => i.kind === 'genetic' && i.concept === n.concept).flatMap(i => {
      const h = i.gene.history.find(x => x.classification !== 'no relevant variant');
      return h ? [point(h.recorded, h.recorded, `${i.gene.gene} ${h.classification}`, i)] : [];
    });
  } else if (kind === 'measure') {
    const param = relax.has(`node:${n.id}`) ? relaxedParam(n) : +n.param;
    out = measureEvents(view.series[n.concept] || [], n.cond, param).map(r => ({...r, fe: r.e, fl: r.l, point: true, measure: true}));
  }
  out.sort((a, b) => a.e - b.e);
  // "Last" counts back from the latest possible start, then the earliest, as the engine does (D30): an event known
  // only to lie somewhere in a long range may well be the latest.
  if (n.occ === 'last' || n.occ === 'nth_last') out.sort((a, b) => a.l - b.l || a.e - b.e);
  return n.occ === 'count' ? out : pickOccurrence(out, n.occ || 'any', n.n);
}

/* Temporal network: variables are the bound events' starts and ends; constraints bound x_j − x_i.

   Variables belong to events, not steps: one event bound to two steps has one time. Each
   step's candidates are rebuilt for that step, so an event is recognised by what it is --
   the recorded item (or measurement record), as a point or a span -- not by object
   identity. The same key names the event's variables in engine requests. If two steps ever
   see one event with different ranges, its time lies in both, as in the engine. */

const eventKey = c => `${c.item ? `item:${c.item.id}` : `measure:${c.record_id}`}|${c.point ? 'point' : 'span'}`;

function compile(def, win, bind, relax) {
  const vars = [], at = {}, cons = [], term = {};
  const add = (key, lo, hi) => {
    if (key in at) { const v = vars[at[key]]; v.lo = Math.max(v.lo, lo); v.hi = Math.min(v.hi, hi); }
    else { at[key] = vars.length; vars.push({lo, hi}); }
  };
  add('START', win.start, win.start);
  add('END', win.end, win.end);
  for (const [id, c] of bind) {
    const k = eventKey(c);
    add(`${k}.s`, c.e, c.l);
    if (!c.point) add(`${k}.f`, c.fe, c.fl);
    term[id] = {s: `${k}.s`, f: c.point ? `${k}.s` : `${k}.f`};  // a point ends when it starts
  }
  const ref = (id, part) => isAnchor(id) ? id : term[id][part];
  const C = (i, j, lo, hi, tag) => cons.push({i: at[i], j: at[j], lo, hi, tag});
  for (const [id, c] of bind) {
    if (!c.point) C(term[id].s, term[id].f, 0, Infinity, 'self');
    if (def.scope === 'from_start' || def.scope === 'within') C('START', term[id].s, 0, Infinity, 'scope');
    if (def.scope === 'within') C(term[id].s, 'END', 0, Infinity, 'scope');
    if (def.scope === 'after') C('END', term[id].s, 0, Infinity, 'scope');
  }
  def.edges.forEach((e, k) => {
    const ok = id => isAnchor(id) || bind.has(id);
    if (!ok(e.from) || !ok(e.to)) return;
    if (e.rel === 'after') {
      const hi = finite(e.max) ? toDays(e.max, e.unit) * (relax.has(`edge:${k}`) ? 1.5 : 1) : Infinity;
      C(ref(e.from, 's'), ref(e.to, 's'), finite(e.min) ? toDays(e.min, e.unit) : -Infinity, hi, k);
    } else {
      C(ref(e.from, 's'), ref(e.to, 's'), 0, Infinity, k);
      C(ref(e.from, 'f'), ref(e.to, 's'), -Infinity, toDays(+e.grace || 0, e.unit) + (relax.has(`edge:${k}`) ? 30 : 0), k);
    }
  });
  return {vars, cons};
}

// Shortest paths with variable k at index k + 1 and time zero at 0: d[i + 1][j + 1] is the
// largest x_j − x_i the constraints allow.
function distances(vars, cons) {
  const n = vars.length + 1;
  const d = Array.from({length: n}, (_, i) => Array.from({length: n}, (_, j) => i === j ? 0 : Infinity));
  vars.forEach((v, k) => { d[0][k + 1] = Math.min(d[0][k + 1], v.hi); d[k + 1][0] = Math.min(d[k + 1][0], -v.lo); });
  for (const c of cons) {
    d[c.i + 1][c.j + 1] = Math.min(d[c.i + 1][c.j + 1], c.hi);
    d[c.j + 1][c.i + 1] = Math.min(d[c.j + 1][c.i + 1], -c.lo);
  }
  for (let k = 0; k < n; k++) for (let i = 0; i < n; i++) {
    if (d[i][k] === Infinity) continue;
    for (let j = 0; j < n; j++) if (d[i][k] + d[k][j] < d[i][j]) d[i][j] = d[i][k] + d[k][j];
  }
  return d;
}

const consistent = ({vars, cons}) => distances(vars, cons).every((row, k) => row[k] >= -1e-9);

// A constraint is certain when every choice of times holds it: the recorded ranges, each
// event ending at or after it starts, alone bound x_j − x_i inside [lo, hi]. Two terms on
// one variable differ by exactly zero.
function certainCon(net, c) {
  net.facts ||= distances(net.vars, net.cons.filter(x => x.tag === 'self'));
  return -net.facts[c.j + 1][c.i + 1] >= c.lo && net.facts[c.i + 1][c.j + 1] <= c.hi;
}
const certainAll = net => net.cons.filter(c => c.tag !== 'self').every(c => certainCon(net, c));

function search(view, def, a, win, relax) {
  const byId = nodeById(def);
  const order = a.order;
  const cands = order.map(id => candidates(view, byId[id], relax, win));
  const bind = new Map(), blind = [];
  let best = null, calls = 0;
  const walk = k => {
    if (best?.status === 'exact' || calls > SEARCH_BUDGET) return;
    if (k === order.length) {
      const net = compile(def, win, bind, relax);
      const exact = certainAll(net);
      if (!best || exact) best = {status: exact ? 'exact' : 'possible', bind: new Map(bind), net};
      return;
    }
    const id = order[k];
    if (stepKind(byId[id]) === 'measure') {
      for (const e of def.edges.filter(x => x.to === id && bind.has(x.from))) {
        const p = bind.get(e.from);
        const within = e.rel === 'after' ? (finite(e.max) ? toDays(e.max, e.unit) : 365) : (Number.isFinite(p.fl) ? p.fl - p.e : 365) + toDays(+e.grace || 0, e.unit);
        if (!measureObservable(view.series[byId[id].concept] || [], byId[id].cond, p, within)) { blind.push({id, after: e.from, p}); return; }
      }
    }
    for (const c of cands[k]) {
      bind.set(id, c);
      calls += 1;
      if (consistent(compile(def, win, bind, relax))) walk(k + 1);
      bind.delete(id);
      if (best?.status === 'exact' || calls > SEARCH_BUDGET) return;
    }
  };
  walk(0);
  return {best, blind, truncated: calls > SEARCH_BUDGET};
}

function relaxOptions(def) {
  const out = [];
  def.nodes.forEach(n => {
    const kind = stepKind(n), c = state.concepts[n.concept];
    if (kind === 'occurrence' && c.parents.length) out.push({key: `node:${n.id}`, cost: 0.5, text: `${n.id} as any ${label(c.parents[0]).toLowerCase()}`});
    if (kind === 'occurrence' && n.occ === 'count' && (+n.n || 2) > 1) out.push({key: `count:${n.id}`, cost: 0.5, text: `${n.id} ${(+n.n || 2) - 1} times instead of ${n.n}`});
    if (kind === 'medicine' && c.kind === 'medication' && classesOf(n.concept).some(k => k !== 'antiseizure')) out.push({key: `node:${n.id}`, cost: 0.25, text: `${n.id} as any drug in the same class`});
    if (kind === 'measure') out.push({key: `node:${n.id}`, cost: 0.5, text: `${n.id} ${CONDITIONS[n.cond]}${relative(n.cond) ? '' : ' '}${relaxedParam(n)}`});
  });
  def.edges.forEach((e, k) => {
    if (e.rel === 'after' && finite(e.max)) out.push({key: `edge:${k}`, cost: 0.25, text: `${e.from}→${e.to} up to ${gapText(e, +(+e.max * 1.5).toFixed(1))}`});
    if (e.rel === 'during') out.push({key: `edge:${k}`, cost: 0.25, text: `${e.from}→${e.to} up to 30 more days after stopping`});
  });
  return out;
}

function matchPatient(pid, def, a) {
  const view = evidenceFor(pid);
  const win = windowFor(view, {freeText: state.freeText, knownByIndex: false, window: def.window});
  if (win.reason) return {pid, status: 'excluded', reason: win.reason};
  const riseNodes = def.nodes.filter(n => stepKind(n) === 'measure' && !(view.series[n.concept] || []).some(s => typeof s.value === 'number'));
  if (riseNodes.length) {
    // Not comparable only if everything that can be measured matches; otherwise it is simply no match.
    const ids = new Set(riseNodes.map(n => n.id));
    const rest = {...def, nodes: def.nodes.filter(n => !ids.has(n.id)), edges: def.edges.filter(e => !ids.has(e.from) && !ids.has(e.to))};
    const partial = rest.nodes.length ? search(view, rest, analyse(rest), win, new Set()) : {best: true};
    if (partial.best) return {pid, win, status: 'incomparable', reason: `the other steps match, but there are no ${riseNodes.map(n => label(n.concept).toLowerCase()).join(' or ')} measurements for ${[...ids].join(', ')}`};
    return partial.truncated ? {pid, win, status: 'unresolved', reason: `${STOPPED} on the steps that can be measured`}
      : {pid, win, status: 'none', reason: 'the steps that can be measured do not match'};
  }
  const first = search(view, def, a, win, new Set());
  if (first.best) return {pid, win, status: first.best.status, best: first.best, truncated: first.truncated, cost: 0, relaxations: []};
  const opts = relaxOptions(def);
  const subsets = [];
  opts.forEach((o, i) => { subsets.push([o]); opts.slice(i + 1).forEach(p => subsets.push([o, p])); });
  const ranked = subsets.map(s => ({s, cost: s.reduce((t, o) => t + o.cost, 0)})).filter(x => x.cost <= 1 && new Set(x.s.map(o => o.key)).size === x.s.length)
    .sort((x, y) => x.cost - y.cost || x.s.length - y.s.length);
  // A relaxed match counts only once the unrelaxed search has ruled out a match; a stopped
  // search ruled nothing out, so the patient is unresolved, not relaxed and not a non-match.
  let relaxed = null, skipped = false;
  for (const {s, cost} of ranked) {
    const r = search(view, def, a, win, new Set(s.map(o => o.key)));
    if (r.best) {
      relaxed = {certainty: r.best.status, best: r.best, cost, relaxations: s.map(o => o.text), relaxKeys: new Set(s.map(o => o.key)), truncated: skipped || r.truncated};
      break;
    }
    skipped ||= r.truncated;
  }
  if (first.truncated) {
    return relaxed ? {pid, win, status: 'unresolved', best: relaxed.best, relaxKeys: relaxed.relaxKeys,
      reason: `${STOPPED}; relaxing ${relaxed.relaxations.join('; ')} gives one at cost ${relaxed.cost}`}
      : {pid, win, status: 'unresolved', reason: STOPPED};
  }
  if (relaxed) return {pid, win, status: 'relaxed', ...relaxed};
  if (first.blind.length) {
    const b = first.blind[0];
    return {pid, win, status: 'incomparable', reason: `the measurements do not cover the time around ${b.after} (${b.p.what}, ${ageText(b.p.e)}), so ${b.id} cannot be judged`};
  }
  return {pid, win, status: 'none', truncated: skipped};
}

/* Engine matching. The page resolves each step's candidate events -- index window, free text,
   counts, measurement conditions and each relaxed alternative -- exactly as above, and the
   trajectory-graph engine (patterns/trajectory_graph.py, served by ../server.py) decides the
   temporal matching, relaxation and search budget. Each step's candidates become events
   tagged with that step, so concept matching stays here. Times go to the engine in minutes,
   its integer grid. Two statuses need the page's own data and are settled as above, with
   the engine deciding the steps that can be measured: no measurement series at all, and a
   measurement that cannot be observed after the event it follows. */
const ENGINE_URL = '../api/trajectory-graph/match';
const GRID = 1440;
const toGrid = d => Number.isFinite(d) ? Math.round(d * GRID) : (d < 0 ? -1e15 : 1e15);
const withoutNodes = (def, ids) => ({...def, nodes: def.nodes.filter(n => !ids.has(n.id)),
  edges: def.edges.filter(e => !ids.has(e.from) && !ids.has(e.to))});

function engineRequest(def, pids, relax = true) {
  const opts = relax ? relaxOptions(def) : [];
  const lookup = new Map(), windows = new Map();
  const relations = def.edges.map((e, k) => e.rel === 'after'
    ? {id: `edge:${k}`, kind: 'after', from: e.from, to: e.to,
       ...(finite(e.min) ? {min: toGrid(toDays(e.min, e.unit))} : {}), ...(finite(e.max) ? {max: toGrid(toDays(e.max, e.unit))} : {})}
    : {id: `edge:${k}`, kind: 'during', from: e.from, to: e.to, grace: toGrid(toDays(+e.grace || 0, e.unit))});
  const relaxations = opts.map(o => {
    const [kind, target] = o.key.split(':'), cost = String(o.cost);
    if (kind !== 'edge') return {id: o.key, kind: 'widen_concept', cost, step: target, concept: `${target}~${kind}`};
    const e = def.edges[+target];
    return e.rel === 'after'
      ? {id: o.key, kind: 'extend_max', cost, relation: `edge:${target}`, max: toGrid(toDays(e.max, e.unit) * 1.5)}
      : {id: o.key, kind: 'extend_grace', cost, relation: `edge:${target}`, grace: toGrid(toDays(+e.grace || 0, e.unit) + 30)};
  });
  const patients = pids.map(pid => {
    const view = evidenceFor(pid);
    const win = windowFor(view, {freeText: state.freeText, knownByIndex: false, window: def.window});
    windows.set(pid, win);
    if (win.reason) return {patient_id: pid, window: null, events: []};
    const events = [];
    // One engine event per step candidate; the same underlying event, emitted for several
    // steps, names the same time variables, so the engine gives it one time.
    const add = (n, keys, concept) => candidates(view, n, keys, win).forEach((c, i) => {
      const id = `${concept}#${i}`, key = eventKey(c);
      lookup.set(`${pid}/${id}`, c);
      events.push({id, concepts: [concept], start: [toGrid(c.e), toGrid(c.l)], start_var: `${key}.s`,
        ...(c.point ? {} : {end: [Math.max(toGrid(c.fe), toGrid(c.e)), Number.isFinite(c.fl) ? toGrid(c.fl) : null], end_var: `${key}.f`})});
    });
    for (const n of def.nodes) {
      add(n, new Set(), n.id);
      for (const o of opts.filter(o => !o.key.startsWith('edge:') && o.key.endsWith(`:${n.id}`))) add(n, new Set([o.key]), `${n.id}~${o.key.split(':')[0]}`);
    }
    return {patient_id: pid, window: {start: toGrid(win.start), end: toGrid(win.end)}, events};
  });
  const query = {profile: 'trajectory-graph-1.0', scope: def.scope || 'any', steps: def.nodes.map(n => ({id: n.id, concept: n.id})),
                 relations, relaxations, budget: relax ? '1' : '0', search_budget: SEARCH_BUDGET};
  return {body: {query, patients}, lookup, windows, opts};
}

function fromEngine(r, def, req) {
  const pid = r.patient_id, win = req.windows.get(pid);
  const bindOf = ids => new Map(Object.entries(ids).map(([s, id]) => [s, req.lookup.get(`${pid}/${id}`)]));
  const textOf = key => req.opts.find(o => o.key === key).text;
  const best = (ids, certainty, keys) => { const bind = bindOf(ids); return {status: certainty, bind, net: compile(def, win, bind, keys)}; };
  if (r.status === 'excluded') return {pid, status: 'excluded', reason: win.reason};
  if (r.status === 'exact' || r.status === 'possible')
    return {pid, win, status: r.status, best: best(r.binding, r.status, new Set()), truncated: r.note === 'EXACT_SEARCH_STOPPED', cost: 0, relaxations: []};
  if (r.status === 'relaxed') {
    const keys = new Set(r.relaxations);
    return {pid, win, status: 'relaxed', certainty: r.certainty, best: best(r.binding, r.certainty, keys), cost: +r.cost,
            relaxations: r.relaxations.map(textOf), relaxKeys: keys, truncated: !!r.note};
  }
  if (r.status === 'unresolved') {
    const m = r.relaxed_match;
    if (!m) return {pid, win, status: 'unresolved', reason: STOPPED};
    const keys = new Set(m.relaxations);
    return {pid, win, status: 'unresolved', best: best(m.binding, m.certainty, keys), relaxKeys: keys,
            reason: `${STOPPED}; relaxing ${m.relaxations.map(textOf).join('; ')} gives one at cost ${+m.cost}`};
  }
  return {pid, win, status: 'none', truncated: !!r.note};
}

// A generator: it yields each request body and resumes with the engine's answer, so the page
// drives it with fetch and a test can drive it synchronously.
function* engineSteps(def) {
  const pids = [...state.patients.keys()], out = new Map();
  function* ask(d, ids, relax) {
    const req = engineRequest(d, ids, relax);
    const answer = yield req.body;
    return answer.results.map(r => [r, req]);
  }
  // Patients with no measurement series at all for a measured step: the other steps decide.
  const groups = new Map();
  for (const pid of pids) {
    const view = evidenceFor(pid);
    const rise = def.nodes.filter(n => stepKind(n) === 'measure' && !(view.series[n.concept] || []).some(s => typeof s.value === 'number'));
    const key = rise.map(n => n.id).join(',');
    if (!groups.has(key)) groups.set(key, {rise, pids: []});
    groups.get(key).pids.push(pid);
  }
  for (const [key, {rise, pids: ids}] of groups) {
    if (!key) {
      for (const [r, req] of yield* ask(def, ids, true)) out.set(r.patient_id, fromEngine(r, def, req));
      continue;
    }
    const gone = new Set(rise.map(n => n.id)), rest = withoutNodes(def, gone);
    const answers = rest.nodes.length ? yield* ask(rest, ids, false) : ids.map(pid => [{patient_id: pid, status: 'exact'}, null]);
    for (const [r] of answers) {
      const win = windowFor(evidenceFor(r.patient_id), {freeText: state.freeText, knownByIndex: false, window: def.window});
      out.set(r.patient_id, r.status === 'excluded' || win.reason ? {pid: r.patient_id, status: 'excluded', reason: win.reason}
        : ['exact', 'possible'].includes(r.status) ? {pid: r.patient_id, win, status: 'incomparable', reason: `the other steps match, but there are no ${rise.map(n => label(n.concept).toLowerCase()).join(' or ')} measurements for ${[...gone].join(', ')}`}
        : r.status === 'unresolved' ? {pid: r.patient_id, win, status: 'unresolved', reason: `${STOPPED} on the steps that can be measured`}
        : {pid: r.patient_id, win, status: 'none', reason: 'the steps that can be measured do not match'});
    }
  }
  // A measurement that follows a step must be observable after the event bound to that step.
  // For patients with no match, the other steps go to the engine; if they match, the
  // measurement is checked after the bound event, as the browser search checks it.
  const measured = def.nodes.filter(n => stepKind(n) === 'measure' && def.edges.some(e => e.to === n.id && !isAnchor(e.from)));
  const unmatched = [...out.values()].filter(r => r.status === 'none' && !r.reason).map(r => r.pid);
  if (measured.length && unmatched.length) {
    const rest = withoutNodes(def, new Set(measured.map(n => n.id)));
    if (rest.nodes.length) for (const [r, req] of yield* ask(rest, unmatched, false)) {
      if (!['exact', 'possible'].includes(r.status)) continue;
      const view = evidenceFor(r.patient_id), bind = new Map(Object.entries(r.binding).map(([s, id]) => [s, req.lookup.get(`${r.patient_id}/${id}`)]));
      for (const n of measured) for (const e of def.edges.filter(x => x.to === n.id && bind.has(x.from))) {
        const p = bind.get(e.from);
        const within = e.rel === 'after' ? (finite(e.max) ? toDays(e.max, e.unit) : 365) : (Number.isFinite(p.fl) ? p.fl - p.e : 365) + toDays(+e.grace || 0, e.unit);
        if (!measureObservable(view.series[n.concept] || [], n.cond, p, within) && out.get(r.patient_id).status === 'none')
          out.set(r.patient_id, {pid: r.patient_id, win: req.windows.get(r.patient_id), status: 'incomparable',
            reason: `the measurements do not cover the time around ${e.from} (${p.what}, ${ageText(p.e)}), so ${n.id} cannot be judged`});
      }
    }
  }
  return pids.map(pid => out.get(pid));
}

async function matchWithEngine(def, post) {
  const steps = engineSteps(def);
  let step = steps.next();
  while (!step.done) step = steps.next(await post(step.value));
  return step.value;
}

async function postEngine(body) {
  const response = await fetch(ENGINE_URL, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || `HTTP ${response.status}`);
  return value;
}

const sortResults = results => results.sort((x, y) => ORDER.indexOf(x.status) - ORDER.indexOf(y.status) || (x.cost || 0) - (y.cost || 0) || x.pid.localeCompare(y.pid));
let runToken = 0;

function run() {
  const a = analyse(state.def);
  state.analysis = a;
  const token = ++runToken;
  state.fallback = null;
  state.storeMeta = null;
  state.pageAnswers = new Map();
  if (a.errors.length || !state.def.nodes.length) { state.results = []; state.pending = false; return; }
  if (state.matcher === 'store') return runStore(token);
  runPage(token, state.matcher === 'engine');
}

// The page's own candidates, matched by the engine (when reachable) or by the page's search.
function runPage(token, viaEngine) {
  if (viaEngine) {
    state.pending = true;
    state.engineError = null;
    matchWithEngine(state.def, postEngine).then(results => {
      if (token !== runToken) return;
      state.results = sortResults(results);
      state.pending = false;
      render();
    }).catch(error => {
      if (token !== runToken) return;
      // The engine refused or is unreachable: say so, and match in the browser instead.
      state.engineError = error.message;
      if (state.matcher === 'engine') state.matcher = 'browser';
      runPage(token, false);
      render();
    });
    return;
  }
  state.pending = false;
  state.results = sortResults([...state.patients.keys()].map(pid => matchPatient(pid, state.def, state.analysis)));
}

/* The execution store under the page (D29 stage 2, D37). The page's translator (store_request.js) writes the query in
   the store's language, with the index windows this page places; the server answers from a store of this cohort built
   by the converter (store_trajectory.py). What the translator or the store refuses by name is matched by the page's
   own search instead, and labelled "in-browser fallback: <code>". The answer shown is the store's. */
const STORE_URL = '../api/trajectory-store/match';
const STORE_REFUSAL = {
  GENE_CLASSIFICATION_UNSUPPORTED: 'the store has no form for a gene step (the first report that is not negative, with reclassification)',
  FREE_TEXT_HEDGED_UNSUPPORTED: 'the store holds no uncertain findings from clinical notes (such as "possible" or "suspected")',
  FREE_TEXT_COUNT_UNSUPPORTED: 'the store ranks and counts coded records only; an Nth occurrence or a count needs "Coded records only"',
  // Reachable only when a caller asks for it (`codedOnly: false` or null); the page itself never does since D49.
  FREE_TEXT_CODED_ONLY_UNSUPPORTED: 'the store was asked to answer "Coded records only" without leaving clinical notes out, which it can do only when every step is an Nth occurrence, a count or a measurement',
  // D49: unreachable from the page since the flip (gene steps under "Coded records only" are the joint profile); kept for a caller that asks for the refusal.
  GENE_WITH_CODED_ONLY_UNSUPPORTED: 'a gene step under "Coded records only" needs the store\'s own coded-only reading, which this query did not use',
  NTH_STEP_HAS_NO_END: '"while taking" reads a course\'s end, and an Nth start has none in the store',
  RELATIVE_CONDITION_UNSUPPORTED: 'rises and falls were not sent to the store',
  VALUE_NOT_A_PLAIN_DECIMAL: 'a threshold or factor is not a plain decimal number',
  STORE_UNREACHABLE: 'the store could not be reached',
  // Refused by the store itself (the option sweep in test_store_parity.cjs finds these two among the page's options).
  INVALID_FACTOR: 'the store takes a rise or fall only by a factor above 1',
  VALUE_RELAXATION_MUST_WIDEN: 'the relaxed threshold would narrow the condition instead of widening it (a negative "at most")',
  // The server's own checks, before the store sees the query.
  TIME_OUT_OF_RANGE: 'a time or gap lies beyond what the server accepts (about 1,900 years)',
  TIME_MUST_BE_WHOLE_MINUTES: 'the server takes times in whole minutes',
};
// Why the store is not offered here (the server's status), shown so the fallback to the engine is not silent.
const STORE_UNAVAILABLE = {
  STORE_NOT_CONFIGURED: 'the server was started without it (--no-trajectory-store)',
  STORE_DEPENDENCY_MISSING: 'DuckDB is not installed on the server (pip install -r patterns/requirements-store.lock.txt)',
  STORE_BUILD_FAILED: 'building it from the synthetic cohort failed on the server',
  NO_SYNTHETIC_COHORT: 'the synthetic cohort has not been generated on the server (python demos/mockups/synthetic/generate.py)',
  NOT_THE_SYNTHETIC_COHORT: 'the cohort file on the server is not the synthetic cohort',
  STORE_NOT_THE_SERVED_COHORT: 'the built store does not hold the served cohort\'s patients',
  STORE_ROOT_INSIDE_REPOSITORY: 'its build directory was set inside a git working tree',
  STORE_BUILDING: 'it was still building',
  STORE_UNREACHABLE: 'its status could not be read',
};
const STORE_REASON = {
  FILTERED: 'no candidate events in the store',
  NO_SOURCE_COVERAGE: 'no source that records {steps} covers this patient\'s index period, so a non-match cannot be told from a gap in recording',
  NO_SOURCE_RECORDS_CONCEPT: 'no source covering the period records {steps} at all',
  ADMISSION_EXCLUDED_RECORDS: 'a record the converter left out (a measurement without a unit) could have matched {steps}',
  STEP_CONDITION_UNCERTAIN: 'whether {steps} holds (a count, an Nth occurrence or a rise) depends on record times that are uncertain',
  OCCURRENCE_TIME_COUPLED: 'the Nth occurrence of {steps} can share events with another step, so a possible match is left undecided',
  OCCURRENCE_ORDER_UNCERTAIN: 'the records leave it uncertain which event is the first or last, and the match depends on it',
  SEARCH_BUDGET_EXHAUSTED: 'the search stopped at its budget before deciding',
};
// D49: FILTERED under "Coded records only": the patient holds no coded record for some step (D39's candidate refinement).
const CODED_ONLY_PROFILES = ['execution-store-conditions-1.3', 'execution-store-conditions-gene-coded-1.0'];
const STORE_REASON_CODED_ONLY = {FILTERED: 'no coded record for some step'};
const STORE_NOTE = {
  EXACT_SEARCH_STOPPED: 'the search for an exact match stopped at its budget',
  CHEAPER_RELAXATION_SEARCH_STOPPED: 'a cheaper relaxation may exist; its search stopped',
  CHEAPER_RELAXATION_ORDER_UNCERTAIN: 'a cheaper relaxation was left undecided because the first or last event is uncertain',
  CHEAPER_RELAXATION_OCCURRENCE_COUPLED: 'a cheaper relaxation was left undecided because an Nth occurrence shares events with another step',
  RELAXATION_SEARCH_STOPPED: 'a relaxed search stopped at its budget before deciding',
  RELAXATION_ORDER_UNCERTAIN: 'a relaxed match was left undecided because the first or last event is uncertain',
  RELAXATION_OCCURRENCE_COUPLED: 'a relaxed match was left undecided because an Nth occurrence shares events with another step',
  EXCLUDED_RECORDS_MAY_MAKE_EXACT: 'excluded records could make the match exact',
};
const STORE_STATUS = {not_comparable: 'incomparable'};
const storeSteps = r => Object.entries(r).filter(([k, v]) => k.endsWith('_steps') && Array.isArray(v)).flatMap(([, v]) => v);
const reasonText = (dict, code, steps) => `${(dict[code] || '').replace('{steps}', steps.length ? `step ${steps.join(', ')}` : 'a step') || code} (${code})`;

function runStore(token) {
  const def = state.def;
  let req;
  try {
    req = storeRequest(def, state.freeText, [...state.patients.keys()]);
  } catch (error) {
    if (!(error instanceof StoreRefusal)) throw error;
    const where = error.message.startsWith(error.code + ': ') ? error.message.slice(error.code.length + 2) : '';
    state.fallback = {code: error.code, by: 'the translator', detail: !where ? '' : /^[A-Z]$/.test(where) ? `step ${where}` : where};
    return runPage(token, !!state.engine);
  }
  state.pending = true;
  postStore(req).then(answer => {
    if (token !== runToken) return;
    state.results = sortResults(fromStore(answer, def));
    state.storeMeta = {snapshot: answer.snapshot_id, context: answer.context_id, conditions: answer.conditions_profile,
                       store: answer.store_profile, ms: answer.elapsed_ms};
    state.pending = false;
    render();
  }).catch(error => {
    if (token !== runToken) return;
    state.fallback = error.code ? {code: error.code, by: error.by === 'store' ? 'the store' : 'the server', detail: error.message}
      : {code: 'STORE_UNREACHABLE', by: 'the network', detail: error.message};
    runPage(token, !!state.engine);
    render();
  });
}

async function postStore(body) {
  const response = await fetch(STORE_URL, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const value = await response.json().catch(() => ({}));
  if (!response.ok) throw Object.assign(new Error(value.error || `HTTP ${response.status}`), {code: value.error, status: response.status, by: value.by});
  return value;
}

// A store result as a page result: the store's status, certainty, relaxations, cost and reason, its bound records,
// and the page's own index period (which the page placed and sent).
function fromStore(answer, def) {
  const opts = relaxOptions(def);
  const textOf = key => key.split('+').map(k => (opts.find(o => o.key === k) || {text: k}).text);
  // D49: under "Coded records only" (profile 1.3 or the joint profile) a reason reads over coded records.
  const reasonDict = CODED_ONLY_PROFILES.includes(answer.conditions_profile) ? {...STORE_REASON, ...STORE_REASON_CODED_ONLY} : STORE_REASON;
  return answer.results.map(r => {
    const pid = r.patient_id, view = evidenceFor(pid);
    const win = windowFor(view, {freeText: state.freeText, knownByIndex: false, window: def.window});
    const base = {pid, win, backend: 'store', store: r, truncated: false};
    if (r.status === 'excluded') return {...base, status: 'excluded', reason: win.reason || reasonText(reasonDict, r.reason, [])};
    const m = r.relaxed_match, u = r.unverified_match, keys = r.relaxations || (m && m.relaxations) || [];
    const reasons = [];
    if (r.reason) reasons.push(reasonText(reasonDict, r.reason, storeSteps(r)));
    if (u) reasons.push(`an unrelaxed ${u.certainty} match was found but is not verified`);
    if (m) reasons.push(`relaxing ${keys.flatMap(textOf).join('; ')} gives ${m.certainty === 'exact' ? 'an exact' : 'a possible'} match at cost ${+m.cost}, but the unrelaxed query is undecided`);
    for (const note of (r.note || '').split('; ').filter(Boolean)) reasons.push(reasonText(STORE_NOTE, note, []));
    return {...base, status: STORE_STATUS[r.status] || r.status, certainty: r.certainty, cost: r.cost === undefined ? 0 : +r.cost,
            relaxations: r.status === 'relaxed' ? keys.flatMap(textOf) : [], relaxKeys: keys.length ? new Set(keys.flatMap(k => k.split('+'))) : null,
            reason: reasons.join('; '), unverified: !!(u && !r.binding),
            best: storeBest(view, r.binding || (u && u.binding) || (m && m.binding), r.witness || (u && u.witness) || (m && m.witness),
                            r.certainty || (u && u.certainty) || (m && m.certainty) || r.status)};
  });
}

// The store's binding as the page's {step: candidate}: each bound record's recorded range, in days of age, and what it is.
function storeBest(view, binding, witness, status) {
  if (!binding) return null;
  const bind = new Map();
  for (const [step, d] of Object.entries(binding)) {
    const at = witness && witness[step] != null ? witness[step] / GRID : null;
    if (d.derived && d.gene_report) {  // D38: a gene step's point, at its result's first report that is not negative
      const g = d.gene_report, t = g.finding / GRID;
      const item = view.items.find(i => i.recs.some(x => x.record_id === g.record_id));
      bind.set(step, {e: t, l: t, fe: t, fl: t, point: true, item, store: d, at, measure: !item,
                      what: `${SOURCE_SHORT[g.source] || g.source} · ${g.display || g.kind} · ${g.record_id}: its first report that is not negative, at its recording`});
      continue;
    }
    if (d.derived) {
      bind.set(step, {e: NaN, l: NaN, what: 'the Nth occurrence: a point the store derives from the coded records', store: d, at, measure: true, derived: true});
      continue;
    }
    const e = d.start[0] / GRID, l = d.start[1] / GRID;
    const source = SOURCE_SHORT[d.source] || d.source;
    const value = d.value !== undefined ? ` = ${d.value}${d.unit && d.unit !== '/month' ? ' ' + d.unit : d.unit ? ' per month' : ''}` : '';
    const written = d.kind === 'mention' ? `, stated in a letter dated ${d.as_written}` : d.as_written ? `, written "${d.as_written}"` : '';
    const what = `${source} · ${d.display || d.kind}${value}${written} · ${d.record_id}`;
    const item = view.items.find(i => d.kind === 'mention'
      ? i.mentions.some(x => x.note.record_id === d.record_id && x.m.text === d.display)
      : i.recs.some(x => x.record_id === d.record_id));
    bind.set(step, {e, l, fe: d.end ? d.end[0] / GRID : e, fl: d.end ? (d.end[1] == null ? Infinity : d.end[1] / GRID) : l,
                    point: !d.end, what, item, store: d, at, measure: !item});
  }
  return {status, bind};
}

// For the drawer: the page's own answer for one patient, and the declared differences that can explain a difference.
function pageAnswerFor(pid) {
  if (!state.pageAnswers.has(pid)) state.pageAnswers.set(pid, matchPatient(pid, state.def, state.analysis));
  return state.pageAnswers.get(pid);
}
function declaredCauses(r, page) {
  const out = new Map(), def = state.def, reason = r.store?.reason;
  const add = (cause, text) => { if (!out.has(cause)) out.set(cause, text || cause); };
  if (['NO_SOURCE_COVERAGE', 'NO_SOURCE_RECORDS_CONCEPT'].includes(reason)) add('coverage', 'coverage (the store\'s comparability rule)');
  if (reason === 'ADMISSION_EXCLUDED_RECORDS') add('measurement records', 'measurement records (unitless values are excluded records in the store)');
  if (reason === 'OCCURRENCE_ORDER_UNCERTAIN') add('order uncertain', 'order uncertain (the store decides first and last over every time the records allow)');
  if (page.status === 'incomparable' && /do not cover the time around/.test(page.reason || '')) add('measurement observability', 'measurement observability (a page-only check)');
  for (const n of def.nodes) {
    const kind = stepKind(n);
    const mentions = state.freeText !== 'off';  // with coded records only, the page reads no letter mention
    if (kind === 'occurrence') { if (mentions) add('letter mentions'); add('record reconciliation'); }
    if (kind === 'medicine') { if (mentions) add('letter mentions'); add('medication records'); }
    if (kind === 'measure') add('measurement records');
    if (kind === 'measure' && relative(n.cond)) add('relative baseline');
    if (n.occ === 'count') add('count timing');
    if (derivedOcc(n)) add('occurrence envelope');
  }
  return [...out.values()];
}

/* Rendering */

function chip(status, cost, own = false) {
  const [shape, text] = own ? STATUS[status] : statusLabel(status);
  return `<span class="status ${status}">${shape} ${esc(status === 'relaxed' ? `${text} · cost ${cost}` : text)}</span>`;
}

// The store's unresolved is undecided for a named reason, not only a stopped search (D37).
const statusLabel = st => st === 'unresolved' && state.matcher === 'store' && state.storeMeta ? ['◌', 'Unresolved · undecided'] : STATUS[st];

function render() {
  renderContext();
  renderPeriod();
  renderTrajectory();
  renderMatches();
  renderDetail();
}

function renderContext() {
  const a = state.analysis, counts = count();
  const items = [['Dataset', `${state.data.dataset_id} · ${state.patients.size} patients`],
    ['Index period', windowText(state.def.window)],
    ['Trajectory', a.errors.length ? 'not valid yet' : `${a.shape} · ${state.def.nodes.length} steps · ${state.def.edges.length} constraints`],
    ['Matches', a.errors.length ? '—' : `${counts.exact || 0} exact · ${counts.possible || 0} possible · ${counts.relaxed || 0} relaxed`]];
  $('#context').innerHTML = items.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd title="${esc(v)}">${esc(v)}</dd></div>`).join('');
  document.querySelectorAll('[data-action="lens"]').forEach(b => b.setAttribute('aria-checked', String(b.dataset.lens === state.lens)));
}

function renderPeriod() {
  const w = state.def.window;
  const ref = state.results.filter(r => r.win && !r.win.reason).length;
  $('#period').innerHTML = `<div class="index-box">Every patient gets their own period, placed by the same rule. It anchors the trajectory: steps can be required to fall inside it or after it, and constraints can start from its start or end.
    ${boundEditor('start', w.start, w.end)}${boundEditor('end', w.end, w.start)}
    <div class="plain" style="margin-top:8px"><strong>Reads as:</strong> ${esc(windowText(w))}. Placed for ${ref} of ${state.patients.size} patients.</div>
    <div class="row" style="margin-top:8px"><span class="bound-label">Steps</span><select data-action="scope" aria-label="Where steps may fall">${Object.entries(SCOPES).map(([k, t]) => `<option value="${k}" ${state.def.scope === k ? 'selected' : ''}>must fall ${esc(t)}</option>`).join('')}</select></div></div>`;
}

/* The evidence options (D49: renamed by the owner; the values are unchanged). */
const EVIDENCE_OPTIONS = {
  off: 'Coded records only',
  present: 'Coded records + findings stated in clinical notes',
  possible: 'Coded records + uncertain findings in clinical notes (e.g. \'possible\', \'suspected\')',
};
const EVIDENCE_HINT = '"Coded records only" does not read clinical notes for any step: every step (first, last, Nth and counts included) '
  + 'is matched on coded records alone. Findings in letters and other clinical notes are read only under the other two options.';

function renderTrajectory() {
  const def = state.def, a = state.analysis;
  const shapeClass = a.shape === 'cycle' || a.errors.length ? 'bad' : a.shape.split(' ')[0];
  $('#shape').innerHTML = `<span class="shape-badge ${shapeClass}">${esc(a.errors.length ? 'not valid' : a.shape)}</span>`;
  $('#templates').innerHTML = 'Start from: ' + Object.entries(TEMPLATES).map(([k, t]) => `<button class="btn small" data-action="template" data-t="${k}" title="${esc(t.note)}">${esc(t.label)}</button>`).join('')
    + '<button class="btn small" data-action="template" data-t="blank">Blank</button>';
  $('#diagram').innerHTML = diagram(def, a);
  const msgs = [...a.errors.map(e => `<li class="err">${esc(e)}</li>`), ...a.warnings.map(w => `<li class="warn">${esc(w)}</li>`)];
  $('#reading').innerHTML = (msgs.length ? `<ul class="tj-msgs">${msgs.join('')}</ul>` : '')
    + (reading(def, a) ? `<div class="plain"><strong>Reads as:</strong> ${esc(reading(def, a))}</div>` : '')
    + `<p class="hint">${esc(({sequence: 'A sequence: every step has at most one step before and one after.', tree: 'A tree: steps branch from one root; branches need no order between them.',
        'single step': 'One step: add steps and constraints to make a sequence, tree or graph.', cycle: 'A cycle cannot be matched.'})[a.shape]
        || (a.shape.startsWith('graph') ? 'A graph: at least one step must come after several others, so it is matched jointly, not branch by branch.' : ''))}</p>`;
  $('#nodes').innerHTML = `<table class="tj-table tj-steps"><thead><tr><th>Step</th><th>Event</th><th>Condition</th><th></th></tr></thead><tbody>${def.nodes.map((n, i) =>
    `<tr><td><span class="tj-id">${esc(n.id)}</span></td>
      <td>${combo({value: n.concept, kinds: STEP_KINDS, action: 'node-concept', data: {i}, placeholder: 'Search a finding, diagnosis, medicine, gene or measurement'})}${comboCodes(n.concept, 3)}</td>
      <td>${conditionEditor(n, i)}</td>
      <td><button class="btn small" data-action="remove-node" data-i="${i}" aria-label="Remove step ${esc(n.id)}">Remove</button></td></tr>`).join('')}</tbody></table>
    <div class="actions"><button class="btn small" data-action="add-node">Add step</button></div>
    <p class="hint">Search by name or by code in any terminology the table holds, for example <code>G40</code>, <code>84757009</code>, <code>HP:0001250</code> or <code>N03AF01</code>. A concept also matches its narrower concepts, through every coding.</p>`;
  const ends = [...def.nodes.map(n => [n.id, `${n.id} · ${nodeText(n)}`])];
  const froms = [...Object.entries(ANCHORS), ...ends];
  const sel = (list, value, i, f) => `<select data-action="edge-field" data-i="${i}" data-f="${f}">${list.map(([k, t]) => `<option value="${k}" ${k === value ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select>`;
  $('#edges').innerHTML = `<table class="tj-table"><thead><tr><th>Later step</th><th>Relation</th><th>Earlier step</th><th>Gap</th><th></th></tr></thead><tbody>${def.edges.map((e, i) => `<tr>
      <td>${sel(ends, e.to, i, 'to')}</td>
      <td><select data-action="edge-field" data-i="${i}" data-f="rel"><option value="after" ${e.rel === 'after' ? 'selected' : ''}>starts after</option><option value="during" ${e.rel === 'during' ? 'selected' : ''}>happens while taking</option></select></td>
      <td>${sel(froms, e.from, i, 'from')}</td>
      <td>${e.rel === 'after' ? `<span class="inline"><input type="number" step="any" value="${e.min ?? ''}" data-action="edge-field" data-i="${i}" data-f="min" aria-label="Earliest gap">–<input type="number" step="any" value="${e.max ?? ''}" placeholder="∞" data-action="edge-field" data-i="${i}" data-f="max" aria-label="Latest gap">${unitSelect(e.unit || 'day', `data-action="edge-field" data-i="${i}" data-f="unit"`)}</span>`
        : `<span class="inline">or up to <input type="number" step="any" min="0" value="${e.grace ?? 0}" data-action="edge-field" data-i="${i}" data-f="grace" aria-label="Time after stopping">${unitSelect(e.unit || 'day', `data-action="edge-field" data-i="${i}" data-f="unit"`)} after stopping</span>`}</td>
      <td><button class="btn small" data-action="remove-edge" data-i="${i}" aria-label="Remove constraint ${i + 1}">Remove</button></td></tr>`).join('')}</tbody></table>
    <div class="actions"><button class="btn small" data-action="add-edge" ${def.nodes.length ? '' : 'disabled'}>Add constraint</button></div>
    <p class="hint">Leave the latest gap empty for no upper limit. A negative earliest gap allows the later step to start up to that many days before. Constraints from the index start or end tie a step to each patient's own period.</p>`;
  $('#evidence-options').innerHTML = `<div class="options">${Object.entries(EVIDENCE_OPTIONS).map(([v, t]) =>
    `<label><input type="radio" name="freetext" value="${v}" data-action="freetext" ${state.freeText === v ? 'checked' : ''}><span>${esc(t)}</span></label>`).join('')}</div>
    <p class="hint">${esc(EVIDENCE_HINT)}</p>`;
}

/* Any, first, last, Nth or Nth from last; N is shared with "at least N times". */
function occurrenceSelect(n, i, extra, noun = 'occurrence') {
  const options = [['any', `any ${noun}`], ['first', `first ${noun}`], ['last', `last recorded ${noun}`], ['nth', `Nth ${noun}`], ['nth_last', `Nth-to-last ${noun}`], ...extra];
  const which = n.occ || 'any';
  return `<select data-action="node-field" data-i="${i}" data-f="occ" aria-label="Which ${noun}">${options.map(([k, t]) => `<option value="${k}" ${which === k ? 'selected' : ''}>${t}</option>`).join('')}</select>`
    + (['nth', 'nth_last', 'count'].includes(which) ? ` <span class="inline">N = <input type="number" min="1" step="1" value="${esc(n.n || 2)}" data-action="node-field" data-i="${i}" data-f="n" aria-label="N"></span>` : '')
    + (which === 'last' || which === 'nth_last' ? '<div class="hint">Last recorded moves as follow-up grows; a patient seen for longer can match differently.</div>' : '');
}

function conditionEditor(n, i) {
  const kind = stepKind(n);
  const field = (f, value, attrs = '') => `<input type="number" step="any" value="${esc(value)}" data-action="node-field" data-i="${i}" data-f="${f}" ${attrs}>`;
  if (kind === 'occurrence') {
    const occ = occurrenceSelect(n, i, [['count', 'at least N times']]);
    if (n.occ !== 'count') return occ;
    const w = n.within === 'period' ? 'period' : 'span';
    return `${occ}
      <span class="inline"><select data-action="node-field" data-i="${i}" data-f="within-kind" aria-label="Counted within"><option value="span" ${w === 'span' ? 'selected' : ''}>within</option><option value="period" ${w === 'period' ? 'selected' : ''}>in the index period</option></select>
      ${w === 'span' ? `${field('within-amount', n.within.amount, 'aria-label="Span"')}${unitSelect(n.within.unit, `data-action="node-field" data-i="${i}" data-f="within-unit"`)}` : ''}</span>
      <div class="hint">The step happens when the count is reached, at the Nth event.</div>`;
  }
  if (kind === 'medicine') return occurrenceSelect(n, i, [], 'start');
  if (kind === 'gene') return '<span class="hint">first report that is not negative</span>';
  if (kind === 'measure') {
    return `<select data-action="node-field" data-i="${i}" data-f="cond" aria-label="Condition">${Object.entries(CONDITIONS).map(([k, t]) => `<option value="${k}" ${n.cond === k ? 'selected' : ''}>${esc(t.replace(/[×÷]$/, '').trim())}${relative(k) ? (k === 'rise' ? ' × previous' : ' ÷ previous') : ''}</option>`).join('')}</select>
      ${field('param', n.param, 'aria-label="Value"')} <span class="hint">${relative(n.cond) ? 'times the mean of the one or two measurements before' : esc(MEASURE_UNIT[n.concept] || '')}</span>`;
  }
  return '';
}

function diagram(def, a) {
  if (!def.nodes.length) return '<p class="hint">No steps yet.</p>';
  const byId = nodeById(def), depth = {};
  const order = a.errors.length ? def.nodes.map(n => n.id) : a.order;
  for (const id of order) {
    const parents = def.edges.filter(e => e.to === id && !isAnchor(e.from) && e.from in byId && e.from !== id).map(e => depth[e.from]).filter(d => d != null);
    depth[id] = parents.length ? Math.max(...parents) + 1 : 0;
  }
  const anchors = Object.keys(ANCHORS).filter(k => def.edges.some(e => e.from === k));
  const shift = anchors.length ? 1 : 0;
  const W = 134, H = 46, GX = 58, GY = 16, P = 8;
  const cols = {};
  const pos = {};
  anchors.forEach((k, i) => { pos[k] = {x: P, y: P + i * (H + GY)}; });
  for (const id of order) {
    const c = depth[id] + shift;
    cols[c] = (cols[c] || 0) + 1;
    pos[id] = {x: P + c * (W + GX), y: P + (cols[c] - 1) * (H + GY)};
  }
  const width = Math.max(...Object.values(pos).map(p => p.x)) + W + P, height = Math.max(...Object.values(pos).map(p => p.y)) + H + P;
  const out = [`<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="Trajectory diagram"><defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#475569"/></marker></defs>`];
  def.edges.forEach(e => {
    const s = pos[e.from], t = pos[e.to];
    if (!s || !t || e.from === e.to) return;
    const x1 = s.x + W, y1 = s.y + H / 2, x2 = t.x, y2 = t.y + H / 2, mx = (x1 + x2) / 2;
    const u = unitShort(e.unit || 'day');
    const text = e.rel === 'during' ? `during${+e.grace ? ` +${e.grace} ${u}` : ''}` : `${finite(e.min) ? e.min : '−∞'}…${finite(e.max) ? e.max : '∞'} ${u}`;
    const lw = text.length * 5.4 + 12;
    out.push(`<path d="M${x1} ${y1}C${mx} ${y1} ${mx} ${y2} ${x2 - 2} ${y2}" fill="none" stroke="${e.rel === 'during' ? '#126B5D' : '#475569'}" stroke-width="1.6" ${e.rel === 'during' ? 'stroke-dasharray="5 3"' : ''} marker-end="url(#arrow)"/>`,
             `<rect x="${mx - lw / 2}" y="${(y1 + y2) / 2 - 9}" width="${lw}" height="16" rx="8" fill="#fff" stroke="#D9E1E8"/><text x="${mx}" y="${(y1 + y2) / 2 + 3}" font-size="10" text-anchor="middle" fill="#334155">${esc(text)}</text>`);
  });
  for (const [id, p] of Object.entries(pos)) {
    const anchor = isAnchor(id);
    const n = byId[id];
    const title = anchor ? ANCHORS[id] : nodeText(n);
    const sub = anchor ? 'each patient\'s period' : n.concept ? KIND_LABEL[state.concepts[n.concept].kind] : 'no event yet';
    const color = anchor ? '#174EA6' : {occurrence: '#174EA6', medicine: '#126B5D', gene: '#68449A', measure: '#C2410C'}[stepKind(n)] || '#8592A0';
    out.push(`<g><rect x="${p.x}" y="${p.y}" width="${W}" height="${H}" rx="8" fill="${anchor ? '#E8EFFB' : '#fff'}" stroke="${color}" stroke-width="1.6" ${anchor ? 'stroke-dasharray="4 3"' : ''}/>`,
             anchor ? '' : `<rect x="${p.x}" y="${p.y}" width="22" height="${H}" rx="8" fill="${color}"/><rect x="${p.x + 14}" y="${p.y}" width="8" height="${H}" fill="${color}"/><text x="${p.x + 11}" y="${p.y + H / 2 + 4}" font-size="12" font-weight="700" fill="#fff" text-anchor="middle">${esc(id)}</text>`,
             `<text x="${p.x + (anchor ? 10 : 30)}" y="${p.y + 19}" font-size="11.5" font-weight="650" fill="#172B3A">${esc(title.length > 19 ? title.slice(0, 18) + '…' : title)}</text>`,
             `<text x="${p.x + (anchor ? 10 : 30)}" y="${p.y + 34}" font-size="10" fill="#6B7A89">${esc(sub.length > 22 ? sub.slice(0, 21) + '…' : sub)}</text>`,
             `<title>${esc(`${anchor ? '' : id + ': '}${title}`)}</title></g>`);
  }
  out.push('</svg>');
  return out.join('');
}

function count() {
  const c = {};
  state.results.forEach(r => { c[r.status] = (c[r.status] || 0) + 1; });
  return c;
}

function bindingChips(r) {
  if (!r.best) return '';
  return `<div class="tj-binding">${[...r.best.bind].map(([id, c]) => `<span class="pair-chip"><strong>${esc(id)}</strong> ${timeText(c)}</span>`).join('')}</div>`;
}

// A bound event's recorded range as ages; a store-derived point by its witness time, if any.
function timeText(c) {
  if (c.derived) return c.at != null ? `${esc(ageText(c.at))} (witness)` : '— (a derived point)';
  if (!Number.isFinite(c.e)) return '—';
  return esc(ageText(c.e)) + (c.l - c.e > 2 ? '–' + esc(ageText(c.l)) : '');
}

// Which backend answered this query, and what that means (D37).
function backendBanner() {
  const pageBy = state.engine && state.matcher !== 'browser' && !state.engineError ? 'the engine on this page\'s candidates' : 'this page\'s own search';
  if (state.matcher === 'store' && state.fallback) {
    const f = state.fallback;
    return `<div class="backend fallback" role="status"><strong>In-browser fallback: ${esc(f.code)}</strong>
      <span>${esc(STORE_REFUSAL[f.code] || (f.by === 'the store' ? 'the store refused the query by name' : `${f.by} could not answer`))}${f.detail && f.detail !== f.code ? ` (${esc(f.detail)})` : ''}. Matched by ${esc(pageBy)} instead.</span></div>`;
  }
  if (state.matcher === 'store' && state.storeMeta) {
    const s = state.storeMeta;
    return `<div class="backend store" role="status"><strong>Answered by the execution store</strong>
      <span>snapshot <code title="${esc(s.snapshot)}">${esc(s.snapshot.slice(0, 12))}…</code> · ${esc(s.store)}${s.conditions ? ` · ${esc(s.conditions.replace('execution-store-', ''))}` : ''} · ${s.ms} ms</span>
      <span class="hint">${state.freeText === 'off' ? 'Under "Coded records only" the store reads no clinical note: it reads every coded record as its own event' : 'The store reads every record and letter mention as its own event'}, counts recorded events, and says <em>not comparable</em> where no source covers a step. This page's own search merges records by a stated rule that reads no synthetic truth (same concept, overlapping times; D47), builds medication courses and times letter mentions from their wording, so the two can differ; each difference has a declared cause (README). Index periods are placed by this page over the same grouping, and sent to the store as exact times: the store's answer holds for that period, though the event that places it may be known only within a range.</span></div>`;
  }
  return `<div class="backend page" role="status"><strong>Matched by ${esc(state.matcher === 'browser' ? 'this page\'s own search' : pageBy)}</strong></div>`;
}

function renderMatches() {
  const a = state.analysis;
  if (a.errors.length || !state.def.nodes.length) {
    $('#summary').innerHTML = '<p class="hint">Fix the trajectory on the left to see matches.</p>';
    $('#list').innerHTML = '';
    return;
  }
  const c = count();
  const choices = [...(state.store ? [['store', 'the execution store']] : []), ...(state.engine ? [['engine', 'the engine on this page\'s candidates']] : []), ['browser', 'this page alone']];
  const matcher = state.engine || state.store
    ? `<div class="tj-filters" role="group" aria-label="Match with"><span class="hint">Match with</span>${choices.map(([m, t]) =>
        `<button class="tj-filter" aria-pressed="${state.matcher === m}" data-action="matcher" data-m="${m}">${esc(t)}</button>`).join('')}</div>`
    : '<p class="hint">Matched in this page: no server is reachable here. Serve the mockups with <code>demos/mockups/server.py</code> to match in the execution store.</p>';
  const notice = (state.engineError ? `<p class="hint">The engine could not answer (${esc(state.engineError)}); matched in this page instead.</p>` : '')
    + (state.engine && !state.store && state.storeError ? `<p class="hint">The execution store is not available here: ${esc(STORE_UNAVAILABLE[state.storeError] || 'the server said so')} (${esc(state.storeError)}). Matching with the engine on this page's candidates or with this page alone.</p>` : '');
  if (state.pending) {
    $('#summary').innerHTML = `${matcher}<p class="hint">Matching every patient ${state.matcher === 'store' && !state.fallback ? 'in the execution store' : 'with the engine'}…</p>`;
    $('#list').innerHTML = '';
    return;
  }
  $('#summary').innerHTML = `${matcher}${backendBanner()}${notice}<p class="hint">Every patient is searched; there is no reference patient here. Each step is bound to one of the patient's events, and the constraints are checked jointly over the recorded time ranges.</p>
    <div class="tj-filters" role="group" aria-label="Show results">${ORDER.map(st => `<button class="tj-filter${HIDDEN_BY_DEFAULT.includes(st) ? ' tj-filter-none' : ''}" aria-pressed="${state.filter.has(st)}" data-action="filter" data-s="${st}">${statusLabel(st)[0]} ${statusLabel(st)[1]} · ${c[st] || 0}</button>`).join('')}</div>
    <p class="hint">Select several result types to see them together. <em>No recorded match</em> and <em>No index period</em> are hidden until selected; <em>No recorded match</em> is shown only on its own. ${state.matcher === 'store' && state.storeMeta ? '<em>Unresolved</em> patients are undecided in the store, for the reason given (a stopped search, record times that leave a count or condition uncertain, or excluded records), and may still match.' : '<em>Unresolved</em> patients were not fully searched and may still match.'}</p>`;
  const rows = state.results.filter(r => state.filter.has(r.status));
  $('#list').innerHTML = rows.map(r => {
    const p = state.patients.get(r.pid);
    return `<div class="cand${state.selected === r.pid ? ' active' : ''}" tabindex="0" role="button" data-action="open" data-pid="${r.pid}">
      <div class="rank">${statusLabel(r.status)[0]}</div>
      <div><div class="who">${r.pid} <small>· ${p.sex === 'F' ? 'female' : 'male'}${r.win && !r.win.reason ? ` · period ${esc(spanText(r.win))}` : ''}</small></div>
        ${chip(r.status, r.cost)}${state.lens === 'truth' ? ` <span class="hint">truth: ${esc(GROUP_LABEL[p.truth.condition_group])}</span>` : ''}
        ${r.relaxations?.length ? `<div class="hint">relaxed: ${esc(r.relaxations.join('; '))}</div>` : ''}${r.reason ? `<div class="hint">${esc(r.reason)}</div>` : ''}${r.truncated ? `<div class="hint">search stopped early; ${STOPPED_NOTE[r.status]}</div>` : ''}
        ${bindingChips(r)}</div>
      <div class="right"></div></div>`;
  }).join('') || `<p class="hint">${state.filter.size ? 'No patients with the selected results.' : 'Select at least one result type.'}</p>`;
}

function renderDetail() {
  const drawer = $('#detail');
  const r = state.results.find(x => x.pid === state.selected);
  if (!r) { drawer.hidden = true; return; }
  drawer.hidden = false;
  const view = evidenceFor(r.pid), def = state.def;
  $('#h-detail').textContent = `${r.pid} · ${statusLabel(r.status)[1]}`;
  const highlight = new Map(), markers = [];
  if (r.best) for (const [id, c] of r.best.bind) {
    if (c.measure) { const e = c.at ?? c.e, l = c.at ?? c.l; if (Number.isFinite(e)) markers.push({e, l, tag: id}); } else highlight.set(c.item.id, id);
  }
  const width = $('#detail-body').clientWidth || drawer.clientWidth - 36;
  const out = [timelineSVG(r.pid, {width, minDay: 0, maxDay: Math.max(view.snapshot, 400), window: r.win && !r.win.reason ? r.win : null,
                                   lens: state.lens, highlight, markers, title: `Timeline for ${r.pid}`})];
  out.push(`<p>${chip(r.status, r.cost)} ${r.status === 'relaxed' ? esc(`by ${r.relaxations.join('; ')}${r.certainty === 'possible' ? '; times still uncertain' : ''}`) : esc(r.backend === 'store' ? '' : r.reason || '')}</p>`);
  if (r.backend === 'store') out.push(storeDetail(r));
  else if (r.best) {
    const {bind, net} = r.best;
    out.push('<table class="tj-table"><thead><tr><th>Step</th><th>Bound to</th><th>When</th></tr></thead><tbody>'
      + def.nodes.map(n => { const c = bind.get(n.id); return `<tr><td><span class="tj-id">${esc(n.id)}</span> ${esc(nodeText(n))}</td><td>${esc(c?.what || '—')}</td><td>${c ? esc(ageText(c.e)) + (c.l - c.e > 2 ? '–' + esc(ageText(c.l)) : '') : ''}</td></tr>`; }).join('')
      + '</tbody></table>');
    const verdict = tag => {
      const cs = net.cons.filter(c => c.tag === tag);
      return cs.length && cs.every(c => certainCon(net, c)) ? '<span class="status exact">■ holds for every recorded time</span>' : '<span class="status possible">◧ holds only for some times in the recorded ranges</span>';
    };
    out.push('<table class="tj-table"><thead><tr><th>Constraint</th><th>Verdict</th></tr></thead><tbody>'
      + def.edges.map((e, k) => `<tr><td><span class="tj-id">${esc(e.to)}</span> ${esc(edgeText(e))}${r.relaxKeys?.has(`edge:${k}`) ? ' <em>(relaxed)</em>' : ''}</td><td>${verdict(k)}</td></tr>`).join('')
      + (def.scope !== 'any' ? `<tr><td>Every step falls ${esc(SCOPES[def.scope])}</td><td>${verdict('scope')}</td></tr>` : '')
      + '</tbody></table>');
  }
  out.push(`<div class="actions"><a class="btn" href="../patient-first/">Open the record view</a></div>`);
  $('#detail-body').innerHTML = out.join('');
}

// The drawer for a store result: the store's status and reason as given, the records it bound and its witness times,
// and this page's own answer beside it when they differ, with the declared causes that can explain it (D37).
function storeDetail(r) {
  const sentence = text => text ? text[0].toUpperCase() + text.slice(1) : '';
  const out = [`<p class="hint">Answered by the execution store: <strong>${esc(r.store.status)}</strong>${r.store.certainty ? ` (${esc(r.store.certainty)})` : ''}.${r.reason ? ` ${esc(sentence(r.reason))}.` : ''}</p>`];
  if (r.best) {
    const {bind} = r.best;
    const which = r.status !== 'unresolved' ? '' : r.unverified ? ' (unverified match)' : ' (relaxed match)';
    out.push(`<table class="tj-table"><thead><tr><th>Step</th><th>Store record${which}</th><th>Recorded</th><th>Witness</th></tr></thead><tbody>`
      + state.def.nodes.map(n => {
        const c = bind.get(n.id);
        const recorded = !c ? '' : c.derived ? '— (no record holds this point)' : timeText(c);
        return `<tr><td><span class="tj-id">${esc(n.id)}</span> ${esc(nodeText(n))}</td><td>${esc(c?.what || '—')}</td><td>${recorded}</td><td>${c && c.at != null ? esc(ageText(c.at)) : ''}</td></tr>`;
      }).join('')
      + '</tbody></table>');
    out.push(`<p class="hint">Recorded is each record's own time range in the store (a letter mention ranges from birth to its letter); an Nth occurrence is a point the store derives, which no record holds, so it has only a witness; a gene step is the time its result's first report that is not negative was recorded. The witness is one choice of times that satisfies every constraint. The store decides certainty over every choice of times; this page does not re-check it. On the timeline, a bound record is marked on this page\'s own event that holds it, at this page\'s time for that event, which can differ from the store\'s range.${r.unverified ? ' An unverified match is a binding the search found whose standing the store could not settle (which event is first or last, or an Nth occurrence shared with another step, is uncertain), so the status stays undecided.' : ''}</p>`);
  }
  const page = pageAnswerFor(r.pid);
  const key = x => [x.status, x.certainty || '', x.relaxKeys ? [...x.relaxKeys].sort().join('+') : ''].join(' ');
  if (key(page) !== key(r)) {
    const certainty = page.certainty && page.certainty !== page.status ? ` <span class="hint">(${esc(page.certainty)})</span>` : '';
    out.push(`<div class="backend note"><strong>This page's own search gives ${chip(page.status, page.cost, true)}${certainty}${page.status === 'relaxed' && page.relaxations?.length ? ` <span class="hint">by ${esc(page.relaxations.join('; '))}</span>` : ''}</strong>
      <span class="hint">The store's answer is the one shown. Declared differences that can explain this: ${esc(declaredCauses(r, page).join('; '))}.</span></div>`);
  }
  return out.join('');
}

/* Interaction */

function nextId(def) {
  const used = new Set(def.nodes.map(n => n.id));
  return 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('').find(c => !used.has(c));
}

function freshNode(concept, id, previous) {
  const n = {id, concept};
  const kind = stepKind(n);
  if (previous && stepKind(previous) === kind) return {...previous, id, concept};
  if (kind === 'occurrence') Object.assign(n, {occ: 'first', n: 3, within: {amount: 1, unit: 'month'}});
  if (kind === 'medicine') n.occ = 'first';
  if (kind === 'measure') Object.assign(n, concept === 'seizure_frequency' ? {cond: 'rise', param: 2} : {cond: 'above', param: 10});
  return n;
}

function handle(action, el) {
  const def = state.def;
  const i = +el.dataset.i;
  switch (action) {
    case 'lens': state.lens = el.dataset.lens; return render();
    case 'filter': {
      const st = el.dataset.s, f = state.filter;
      if (st === 'none') state.filter = f.has('none') && f.size === 1 ? new Set(DEFAULT_FILTER) : new Set(['none']);
      else if (f.has('none')) state.filter = new Set([st]);
      else f.has(st) ? f.delete(st) : f.add(st);
      return render();
    }
    case 'open': state.selected = el.dataset.pid; return render();
    case 'close-detail': state.selected = null; return render();
    case 'template': {
      const t = el.dataset.t === 'blank' ? clone(BLANK) : clone(TEMPLATES[el.dataset.t]);
      def.nodes = t.nodes; def.edges = t.edges;
      break;
    }
    case 'add-node': if (nextId(def)) def.nodes.push({id: nextId(def), concept: null}); break;
    case 'remove-node': {
      const id = def.nodes[i].id;
      def.nodes.splice(i, 1);
      def.edges = def.edges.filter(e => e.from !== id && e.to !== id);
      break;
    }
    case 'node-concept': def.nodes[i] = freshNode(el.value, def.nodes[i].id, def.nodes[i]); break;
    case 'node-field': {
      const n = def.nodes[i], f = el.dataset.f;
      if (f === 'within-kind') n.within = el.value === 'period' ? 'period' : {amount: 1, unit: 'month'};
      else if (f === 'within-amount') n.within.amount = Math.max(0, +el.value || 0);
      else if (f === 'within-unit') n.within.unit = el.value;
      else if (f === 'occ') { n.occ = el.value; n.n = n.n || 2; if (n.occ === 'count' && !n.within) n.within = {amount: 1, unit: 'month'}; }
      else if (f === 'n') n.n = Math.max(1, Math.round(+el.value || 1));
      else if (f === 'cond') { n.cond = el.value; n.param = relative(n.cond) ? 2 : (n.param || 10); }
      else n[f] = +el.value || 0;
      break;
    }
    case 'add-edge': {
      const last = def.nodes[def.nodes.length - 1], prev = def.nodes[def.nodes.length - 2];
      def.edges.push({from: prev ? prev.id : 'START', to: last.id, rel: 'after', min: 0, max: 12, unit: 'month'});
      break;
    }
    case 'remove-edge': def.edges.splice(i, 1); break;
    case 'edge-field': {
      const e = def.edges[i], f = el.dataset.f;
      if (f === 'rel') { e.rel = el.value; if (e.rel === 'during') { e.grace = e.grace ?? 0; } else { e.min = e.min ?? 0; e.max = e.max ?? 12; } }
      else if (f === 'min' || f === 'max') e[f] = el.value === '' ? null : +el.value;
      else if (f === 'grace') e.grace = Math.max(0, +el.value || 0);
      else if (f === 'unit') e.unit = el.value;
      else e[f] = el.value;
      break;
    }
    case 'bound-kind': {
      const which = el.dataset.which, other = which === 'start' ? 'end' : 'start';
      def.window[which] = freshBound(el.value, which);
      if (el.value === 'span' && def.window[other].kind === 'span') def.window[other] = which === 'start' ? freshBound('age', 'end') : freshBound('birth', 'start');
      break;
    }
    case 'bound-field': {
      const b = def.window[el.dataset.which], f = el.dataset.field;
      b[f] = ['date', 'event', 'unit', 'which'].includes(f) ? el.value : f === 'n' ? Math.max(1, Math.round(+el.value || 1)) : (+el.value || 0);
      break;
    }
    case 'scope': def.scope = el.value; break;
    case 'matcher': state.matcher = el.dataset.m; state.engineError = null; break;
    case 'freetext': state.freeText = el.value; break;
    default: return;
  }
  run();
  render();
}

function wire() {
  document.addEventListener('click', e => {
    const el = e.target.closest('[data-action]');
    if (el && !['SELECT', 'INPUT'].includes(el.tagName)) handle(el.dataset.action, el);
  });
  document.addEventListener('change', e => {
    const el = e.target.closest('[data-action]');
    if (el && ['SELECT', 'INPUT'].includes(el.tagName)) handle(el.dataset.action, el);
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && state.selected) { handle('close-detail', e.target); return; }
    if ((e.key === 'Enter' || e.key === ' ') && e.target.closest('.cand')) { e.preventDefault(); e.target.closest('.cand').click(); }
  });
  wireCombos((action, el) => handle(action, el));
  const tip = $('#tooltip');
  document.addEventListener('mouseover', e => {
    const t = e.target.closest('[data-tip]');
    if (!t) { tip.hidden = true; return; }
    tip.textContent = t.dataset.tip;
    tip.hidden = false;
  });
  document.addEventListener('mousemove', e => {
    if (tip.hidden) return;
    const below = e.clientY + 16 + tip.offsetHeight < innerHeight - 8;
    tip.style.left = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8) + 'px';
    tip.style.top = (below ? e.clientY + 16 : e.clientY - tip.offsetHeight - 12) + 'px';
  });
  let timer;
  addEventListener('resize', () => { clearTimeout(timer); timer = setTimeout(render, 150); });
}

async function main() {
  try {
    await loadData();
  } catch (error) {
    $('#workspace').innerHTML = `<p class="loading">Could not load the synthetic dataset (${esc(error.message)}). Serve the <code>demos/mockups/</code> directory over HTTP.</p>`;
    return;
  }
  try {
    const health = await fetch('../api/health');
    if (health.ok) { state.engine = (await health.json()).profile; state.matcher = 'engine'; }
  } catch (error) { /* static hosting: match in the browser */ }
  if (state.engine) {
    // D37: with the store, the page matches there by default.
    try {
      const status = await fetch('../api/trajectory-store/status');
      const value = status.ok ? await status.json() : {error: `HTTP ${status.status}`};
      if (value.available) { state.store = value; state.matcher = 'store'; } else state.storeError = value.error;
    } catch (error) { state.storeError = 'STORE_UNREACHABLE'; }
  }
  state.def = defaultDef();
  run();
  wire();
  render();
}

main();
