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
  matcher: 'browser', engine: null, pending: false, engineError: null});

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
  if (kind === 'measure') return `${name} ${CONDITIONS[n.cond]}${n.param}${relative(n.cond) ? '' : ' ' + (MEASURE_UNIT[n.concept] || '')}`;
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
  const items = view.items.filter(i => admissible(i, q));
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
  return n.occ === 'count' ? out : pickOccurrence(out, n.occ || 'any', n.n);
}

/* Temporal network: variables are the bound events' starts and ends; constraints bound x_j − x_i.

   Variables belong to events, not steps: one event bound to two steps has one time. Each
   step's candidates are rebuilt for that step, so an event is recognised by what it is --
   the recorded item (or measurement record), as a point or a span, with its recorded range
   -- not by object identity. The same key names the event's variables in engine requests. */

const eventKey = c => [c.item ? `item:${c.item.id}` : `measure:${c.source}`, c.point ? 'point' : 'span', c.e, c.l, c.fe, c.fl].join('|');

function compile(def, win, bind, relax) {
  const vars = [], at = {}, cons = [], term = {};
  const add = (key, lo, hi) => { if (!(key in at)) { at[key] = vars.length; vars.push({lo, hi}); } };
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
    if (kind === 'measure') out.push({key: `node:${n.id}`, cost: 0.5, text: `${n.id} ${CONDITIONS[n.cond]}${relaxedParam(n)}`});
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
  if (a.errors.length || !state.def.nodes.length) { state.results = []; state.pending = false; return; }
  if (state.matcher === 'engine') {
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
      state.matcher = 'browser';
      run();
      render();
    });
    return;
  }
  state.pending = false;
  state.results = sortResults([...state.patients.keys()].map(pid => matchPatient(pid, state.def, a)));
}

/* Rendering */

function chip(status, cost) {
  const [shape, text] = STATUS[status];
  return `<span class="status ${status}">${shape} ${esc(status === 'relaxed' ? `${text} · cost ${cost}` : text)}</span>`;
}

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
  $('#evidence-options').innerHTML = `<div class="options">${[['off', 'Coded records only'], ['present', 'Also findings that letters state about the patient'], ['possible', 'Also hedged findings in letters']].map(([v, t]) =>
    `<label><input type="radio" name="freetext" value="${v}" data-action="freetext" ${state.freeText === v ? 'checked' : ''}><span>${esc(t)}</span></label>`).join('')}</div>`;
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
  return `<div class="tj-binding">${[...r.best.bind].map(([id, c]) => `<span class="pair-chip"><strong>${esc(id)}</strong> ${esc(ageText(c.e))}${c.l - c.e > 2 ? '–' + esc(ageText(c.l)) : ''}</span>`).join('')}</div>`;
}

function renderMatches() {
  const a = state.analysis;
  if (a.errors.length || !state.def.nodes.length) {
    $('#summary').innerHTML = '<p class="hint">Fix the trajectory on the left to see matches.</p>';
    $('#list').innerHTML = '';
    return;
  }
  const c = count();
  const matcher = state.engine
    ? `<div class="tj-filters" role="group" aria-label="Matched by"><span class="hint">Matched by</span>${[['engine', `the engine (${state.engine})`], ['browser', 'this page']].map(([m, t]) =>
        `<button class="tj-filter" aria-pressed="${state.matcher === m}" data-action="matcher" data-m="${m}">${esc(t)}</button>`).join('')}</div>`
    : '<p class="hint">Matched in this page: no engine is reachable here. Serve the mockups with <code>demos/mockups/server.py</code> to match with the engine.</p>';
  const notice = state.engineError ? `<p class="hint">The engine could not answer (${esc(state.engineError)}); matched in this page instead.</p>` : '';
  if (state.pending) {
    $('#summary').innerHTML = `${matcher}<p class="hint">Matching every patient with the engine…</p>`;
    $('#list').innerHTML = '';
    return;
  }
  $('#summary').innerHTML = `${matcher}${notice}<p class="hint">Every patient is searched; there is no reference patient here. Each step is bound to one of the patient's events, and the constraints are checked jointly over the recorded time ranges.</p>
    <div class="tj-filters" role="group" aria-label="Show results">${ORDER.map(st => `<button class="tj-filter${HIDDEN_BY_DEFAULT.includes(st) ? ' tj-filter-none' : ''}" aria-pressed="${state.filter.has(st)}" data-action="filter" data-s="${st}">${STATUS[st][0]} ${STATUS[st][1]} · ${c[st] || 0}</button>`).join('')}</div>
    <p class="hint">Select several result types to see them together. <em>No recorded match</em> and <em>No index period</em> are hidden until selected; <em>No recorded match</em> is shown only on its own. <em>Unresolved</em> patients were not fully searched and may still match.</p>`;
  const rows = state.results.filter(r => state.filter.has(r.status));
  $('#list').innerHTML = rows.map(r => {
    const p = state.patients.get(r.pid);
    return `<div class="cand${state.selected === r.pid ? ' active' : ''}" tabindex="0" role="button" data-action="open" data-pid="${r.pid}">
      <div class="rank">${STATUS[r.status][0]}</div>
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
  $('#h-detail').textContent = `${r.pid} · ${STATUS[r.status][1]}`;
  const highlight = new Map(), markers = [];
  if (r.best) for (const [id, c] of r.best.bind) c.measure ? markers.push({e: c.e, l: c.l, tag: id}) : highlight.set(c.item.id, id);
  const width = $('#detail-body').clientWidth || drawer.clientWidth - 36;
  const out = [timelineSVG(r.pid, {width, minDay: 0, maxDay: Math.max(view.snapshot, 400), window: r.win && !r.win.reason ? r.win : null,
                                   lens: state.lens, highlight, markers, title: `Timeline for ${r.pid}`})];
  out.push(`<p>${chip(r.status, r.cost)} ${r.status === 'relaxed' ? esc(`by ${r.relaxations.join('; ')}${r.certainty === 'possible' ? '; times still uncertain' : ''}`) : esc(r.reason || '')}</p>`);
  if (r.best) {
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
      const t = el.dataset.t === 'blank' ? {nodes: [{id: 'A', concept: 'seizure', occ: 'first'}], edges: []} : clone(TEMPLATES[el.dataset.t]);
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
  state.def = defaultDef();
  run();
  wire();
  render();
}

main();
