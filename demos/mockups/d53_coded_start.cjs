/* D53: "Coded records only" reads no clinical note in the trajectory page's own search either (decision log, D53).

   Since D53, under "Coded records only" (`freeText === 'off'`), the page's own search and its index periods read each item
   as its coded records alone make it (shared/core.js, `codedOf` and `searchable`): no step or period takes a time, a
   concept or a label from a letter mention, and a medicine course whose only medication record is a stop has no start
   there. The other two settings are unchanged. This tool loads the page's own code (shared/core.js, trajectory/app.js,
   trajectory/store_request.js, option_sweep.js) into node:vm contexts, main's from git at --base (default 34af272) and
   this tree's, and compares them.

     node demos/mockups/d53_coded_start.cjs census [--output REPORT.json]
       Rules 2 and 3, on the page's cohort: the page's own search (matchPatient), the index periods and the store requests,
       for the five templates and every option-sweep definition, each under the three settings. Under stated and hedged
       findings, all byte-identical to main's. Under coded only, each equals main's on the cohort with every letter
       mention removed (the meaning by removal), and every difference from main's traces to an item whose main form
       takes a field a search reads from a note.
     node demos/mockups/d53_coded_start.cjs usefulness [--output REPORT.json]
       Rule 5: the five templates' own search over fresh cohorts (seeds 5301-5330, extended in order to 5400), the
       owner's counting rule, here and main; the same identity and removal checks per seed.

   Synthetic data only (generator patient ids, as the parity test pins them). Needs git for main's page. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const d47 = require('./d47_page.cjs');
const {withoutMentions} = require('./d49_flip.cjs');

const here = __dirname, root = path.resolve(here, '../..');
const python = process.env.PYTHON || 'python';
const COHORT = path.join(here, 'data/synthetic-paediatric-rare.json');
const RUN_TIMEOUT_MS = 60 * 60 * 1000;
// As test_store_parity.cjs (D44): a child's stdin is a regular file, never a pipe.
const run = (args, input, opts = {}) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'd53-stdin-'));
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
const J = JSON.stringify;
const BASE = arg('--base', '34af272');  // main after D51 (#174)
// A smoke run (D53_SMOKE=1): the first 20 sweep definitions, one undeclared seed (4601, D46's probe seed), never into verification/.
const SMOKE = process.env.D53_SMOKE === '1';
const FREE_TEXT = ['present', 'off', 'possible'];
const SEEDS = Array.from({length: 30}, (_, i) => 5301 + i);
const EXTEND = Array.from({length: 70}, (_, i) => 5331 + i);

const FILES = {core: 'demos/mockups/shared/core.js', app: 'demos/mockups/trajectory/app.js',
               translator: 'demos/mockups/trajectory/store_request.js', sweep: 'demos/mockups/option_sweep.js'};
const baseText = rel => run(['show', `${BASE}:${rel}`], undefined, {command: 'git'});
const hereText = rel => fs.readFileSync(path.join(root, rel), 'utf8');

// What a step or an index period reads of an item under coded only (core.js's CODED_FIELDS, less `textConcept`, which
// `conceptFor` reads only under the other settings, and less the label and the recorded and available days, reported apart:
// the trajectory page's search never reads the last two, and the label only names a candidate).
const SEARCH_FIELDS = ['kind', 'concept', 'time', 'med', 'gene', 'procedure', 'freeTextOnly', 'possibleOnly'];
const EXTRA = `
  var SEARCH_FIELDS = ${J(SEARCH_FIELDS)};
  var differs = (a, b, keys) => keys.filter(k => JSON.stringify(a[k]) !== JSON.stringify(b[k]));
  // This tree only: the items whose form under coded only (codedOf) differs from the item as every record and mention make
  // it, in a field a search reads (so main, which searched the latter, read a note there), or that main searched and this
  // tree does not. With what differs; and, apart, the items whose label or recorded or available day alone differs.
  var notedItems = () => {
    const out = {search: [], label_or_days_only: 0};
    for (const pid of state.patients.keys()) for (const i of evidenceFor(pid).items) {
      if (i.freeTextOnly) continue;
      const c = codedOf(i);
      const d = c ? differs(i, c, SEARCH_FIELDS) : ['not searched'];
      if (d.length) out.search.push({patient_id: pid, sig: sigOf(i), kind: i.kind, concept: i.concept, fields: d,
                                     records: i.recs.map(r => r.kind + (r.status ? ':' + r.status : '')).sort()});
      else if (differs(i, c, ['display', 'recordedDay', 'availableDay']).length) out.label_or_days_only += 1;
    }
    return out;
  };
  var windowAt = (pid, window, freeText) => {
    const w = windowFor(evidenceFor(pid), {freeText, knownByIndex: false, window});
    return w.reason ? {reason: w.reason} : {start: toGrid(w.start), end: toGrid(w.end)};
  };
`;

function pageContext(cohortText, which) {
  const text = Object.fromEntries(Object.entries(FILES).map(([k, f]) => [k, which === 'main' ? baseText(f) : hereText(f)]));
  const stub = {addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; }};
  const context = vm.createContext({document: stub, addEventListener() {}, console, structuredClone, performance, DATA: cohortText,
                                    POST: () => { throw new Error('D53 never calls the page engine'); }});
  vm.runInContext(text.core + '\n' + text.app.replace(/\nmain\(\);\s*$/, '\n') + '\n' + text.translator + `
    state.data = JSON.parse(DATA); state.concepts = state.data.concepts;
    for (const [cid, c] of Object.entries(state.concepts)) for (const coding of c.codings) state.codeIndex.set(coding.system + '|' + coding.code, cid);
    for (const p of state.data.patients) state.patients.set(p.patient_id, p);
    state.freeText = 'present';
  ` + d47.DEFINITIONS + EXTRA, context);
  vm.runInContext(text.sweep, context);
  assert.equal(vm.runInContext('typeof codedOf', context), which === 'main' ? 'undefined' : 'function',
               `${which}: the page's codedOf is ${which === 'main' ? 'not ' : ''}expected`);
  return context;
}
const inContext = (context, code) => JSON.parse(vm.runInContext(`JSON.stringify(${code})`, context));
const bareText = text => JSON.stringify(withoutMentions(JSON.parse(text)).cohort);

/* Pure parts (checked in test_engine_parity.cjs). */

// Two store requests (or refusals): the same; or differing only in the windows of the named patients; or otherwise.
function requestDiff(a, b) {
  if (same(a, b)) return {same: true, windows_only: true, patients: []};
  const strip = r => { const {windows, ...rest} = r; return rest; };
  if (!a.windows || !b.windows || !same(strip(a), strip(b))) return {same: false, windows_only: false, patients: []};
  const pids = [...new Set([...Object.keys(a.windows), ...Object.keys(b.windows)])].filter(pid => !same(a.windows[pid], b.windows[pid])).sort();
  return {same: false, windows_only: true, patients: pids};
}
// One case's step candidates on the two sides (d47's stepCands), and the signatures of the patient's noted items: traced if
// they differ, and every candidate on one side only, before the occurrence or count picks ('all' lists, or the list itself
// for a step with none), is held by a noted item; a picked list may then differ by a reranking. A window placed differently
// (a null side) is traced by the caller.
function traced(mainCands, hereCands, notedSigs) {
  if (!mainCands || !hereCands || same(mainCands, hereCands)) return false;
  const noted = new Set(notedSigs.map(s => J(s)));
  const keys = new Set([...Object.keys(mainCands), ...Object.keys(hereCands)]);
  for (const key of keys) {
    if (key.startsWith('all ')) continue;
    const base = keys.has('all ' + key) ? 'all ' + key : key;
    const m = (mainCands[base] || []).map(J), h = (hereCands[base] || []).map(J);
    const only = [...m.filter(x => !h.includes(x)), ...h.filter(x => !m.includes(x))];
    if (only.some(x => !noted.has(J(JSON.parse(x).sig)))) return false;
    if (!only.length && !same(mainCands[key], hereCands[key])) return false;
  }
  return true;
}
// The census's verdict from its tallies.
function censusMet(t) {
  return {other_settings_identical: t.other.results === 0 && t.other.windows === 0 && t.other.requests === 0,
          removal: t.off.results_unlike_removal === 0 && t.off.windows_unlike_removal === 0,
          traced: t.off.untraced_results === 0 && t.off.untraced_windows === 0 && t.off.requests_not_windows_only === 0
                  && t.off.request_windows_untraced === 0,
          not_vacuous: t.noted_items > 0};
}

/* The census (rules 2 and 3). */

function cases(ctx) {
  const out = [], seen = new Set();
  const add = (name, def, f) => { const k = J(def) + '|' + f; if (!seen.has(k)) { seen.add(k); out.push({name: `${name} | ${f}`, def, freeText: f}); } };
  for (const [name, def] of Object.entries(inContext(ctx, 'TEMPLATE_DEFS'))) for (const f of FREE_TEXT) add(`template: ${name}`, def, f);
  const sweep = inContext(ctx, 'sweepCases()');
  for (const c of SMOKE ? sweep.slice(0, 20) : sweep) for (const f of FREE_TEXT) add(`sweep: ${c.name}`, c.def, f);
  return out;
}

function census(output) {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'd53-census-'));
  try {
    const cohortPath = SMOKE ? path.join(work, 'cohort-4601.json') : COHORT;
    if (SMOKE) run(['demos/mockups/synthetic/generate.py', '--seed', '4601', '--output', cohortPath]);
    const text = fs.readFileSync(cohortPath, 'utf8'), bare = withoutMentions(JSON.parse(text));
    const ctx = {main: pageContext(text, 'main'), here: pageContext(text, 'this'),
                 main_bare: pageContext(J(bare.cohort), 'main'), here_bare: pageContext(J(bare.cohort), 'this')};
    const q = (which, code) => inContext(ctx[which], code);
    const pids = q('here', '[...state.patients.keys()]');
    const noted = q('here', 'notedItems()');
    const notedBy = {};
    for (const n of noted.search) (notedBy[n.patient_id] = notedBy[n.patient_id] || []).push(n.sig);
    const all = cases(ctx.here);
    const t = {other: {cases: 0, results: 0, windows: 0, requests: 0},
               off: {cases: 0, results_compared: 0, results_changed: 0, results_unlike_removal: 0, untraced_results: 0,
                     windows_compared: 0, windows_changed: 0, windows_unlike_removal: 0, untraced_windows: 0,
                     requests_changed: 0, requests_not_windows_only: 0, request_windows_untraced: 0},
               noted_items: noted.search.length};
    const changes = [], misses = [];
    const t0 = Date.now();
    for (const c of all) {
      const d = J(c.def), f = J(c.freeText);
      const rm = q('main', `translateOne(${d}, ${f}, {})`), rh = q('here', `translateOne(${d}, ${f}, {})`);
      const om = q('main', `ownResults(${d}, ${f})`), oh = q('here', `ownResults(${d}, ${f})`);
      const wm = q('main', `windowsOf(${d}, ${f})`), wh = q('here', `windowsOf(${d}, ${f})`);
      if (c.freeText !== 'off') {
        t.other.cases += 1;
        const r = pids.filter(pid => !same(om[pid], oh[pid])).length, w = pids.filter(pid => !same(wm[pid], wh[pid])).length;
        t.other.results += r; t.other.windows += w; t.other.requests += same(rm, rh) ? 0 : 1;
        if (r || w || !same(rm, rh)) misses.push({case: c.name, results: r, windows: w, request_same: same(rm, rh)});
        continue;
      }
      t.off.cases += 1;
      // The meaning by removal: this tree on the cohort equals main on the cohort without letter mentions.
      const ob = q('main_bare', `ownResults(${d}, ${f})`), wb = q('main_bare', `windowsOf(${d}, ${f})`);
      const movedWindows = new Set();
      for (const pid of pids) {
        t.off.windows_compared += 1;
        if (!same(wh[pid], wb[pid])) { t.off.windows_unlike_removal += 1; misses.push({case: c.name, patient_id: pid, what: 'window unlike removal', here: wh[pid], main_bare: wb[pid]}); }
        if (!same(wm[pid], wh[pid])) {
          t.off.windows_changed += 1; movedWindows.add(pid);
          const ok = !!notedBy[pid] && same(wh[pid], wb[pid]);
          if (!ok) t.off.untraced_windows += 1;
          changes.push({case: c.name, patient_id: pid, what: 'window', main: wm[pid], here: wh[pid], traced: ok});
        }
        t.off.results_compared += 1;
        if (!same(oh[pid], ob[pid])) { t.off.results_unlike_removal += 1; misses.push({case: c.name, patient_id: pid, what: 'result unlike removal', here: oh[pid], main_bare: ob[pid]}); }
        if (!same(om[pid], oh[pid])) {
          t.off.results_changed += 1;
          const mc = q('main', `stepCands(${J(pid)}, ${d}, ${f})`), hc = q('here', `stepCands(${J(pid)}, ${d}, ${f})`);
          const ok = same(oh[pid], ob[pid]) && !!notedBy[pid] && (movedWindows.has(pid) || traced(mc, hc, notedBy[pid]));
          if (!ok) t.off.untraced_results += 1;
          changes.push({case: c.name, patient_id: pid, what: 'result', main: om[pid], here: oh[pid], traced: ok});
        }
      }
      const rd = requestDiff(rm, rh);
      if (!rd.same) {
        t.off.requests_changed += 1;
        if (!rd.windows_only) t.off.requests_not_windows_only += 1;
        // The request carries the index periods on the store's grid: each window it changes is a traced window change.
        t.off.request_windows_untraced += rd.patients.filter(pid => !movedWindows.has(pid) || !notedBy[pid]).length;
        changes.push({case: c.name, what: 'store request', windows_only: rd.windows_only, patients: rd.patients});
      }
    }
    const elapsed = (Date.now() - t0) / 1000;
    // Not vacuous: windows anchored on each noted item's concept (first, last), under coded only, move as removal says.
    const probes = [];
    for (const n of noted.search) for (const which of ['first', 'last']) {
      const window = {start: {kind: 'event', event: n.concept, which, n: 1, offset: 0, unit: 'month'}, end: {kind: 'span', amount: 6, unit: 'month'}};
      const at = w => q(w, `windowAt(${J(n.patient_id)}, ${J(window)}, 'off')`);
      probes.push({patient_id: n.patient_id, anchor: `${which} ${n.concept}`, main: at('main'), here: at('here'), main_bare: at('main_bare'),
                   here_bare: at('here_bare'), present_here: q('here', `windowAt(${J(n.patient_id)}, ${J(window)}, 'present')`),
                   present_main: q('main', `windowAt(${J(n.patient_id)}, ${J(window)}, 'present')`)});
    }
    const probesMet = probes.every(p => same(p.here, p.main_bare) && same(p.here, p.here_bare) && same(p.present_here, p.present_main));
    const met = {...censusMet(t), probes: probesMet};
    write(output, {
      profile: 'd53-coded-start-census-1.0',
      rule: ('D53 rules 2 and 3: on the page\'s cohort, the page\'s own search (matchPatient: status, certainty, relaxations, '
             + 'cost), the index periods and the store requests (no options), for the five templates and every option-sweep '
             + 'definition under each of the three settings, main\'s page against this tree\'s. Under stated and hedged findings, '
             + 'all byte-identical. Under coded only, every result and window equals main\'s on the cohort with every letter '
             + 'mention removed, and every difference from main\'s (a result, a window, a store request\'s windows) is on a patient '
             + 'with an item whose main form takes a field a search reads from a note, a result traced through its step '
             + 'candidates. At least one such item exists, and windows anchored on each one move as removal says'),
      base: BASE, cohort_sha256: shaText(text), mentions_removed: bare.removed, patients: pids.length, cases: all.length,
      tallies: t, noted_items: noted.search, label_or_days_only_items: noted.label_or_days_only, changes, first_misses: misses.slice(0, 20),
      probes, elapsed_s: elapsed, met, all_met: Object.values(met).every(Boolean),
      artifacts: artifacts(), contains_patient_level_data: false,
      synthetic_patient_ids: 'generator ids only (as the parity test pins them)', ...(SMOKE ? {smoke: true} : {}),
    });
    console.error(`census: ${Object.entries(met).map(([k, v]) => `${k} ${v ? 'met' : 'MISSED'}`).join(', ')}; under coded only `
                  + `${t.off.results_changed} results, ${t.off.windows_changed} windows, ${t.off.requests_changed} store requests changed; `
                  + `${noted.search.length} noted items; ${elapsed.toFixed(0)} s`);
  } finally {
    fs.rmSync(work, {recursive: true, force: true});
  }
}

/* Usefulness (rule 5): the owner's counting rule on the templates' own search, here and main, per fresh seed. */

function usefulness(output) {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'd53-usefulness-'));
  const perSeed = {}, used = [], checks = {other_settings_differing: 0, off_unlike_removal: 0, off_changed: 0};
  try {
    let names = null;
    const measure = seed => {
      const cohortPath = path.join(work, `cohort-${seed}.json`);
      run(['demos/mockups/synthetic/generate.py', '--seed', String(seed), '--output', cohortPath]);
      const text = fs.readFileSync(cohortPath, 'utf8');
      const ctx = {main: pageContext(text, 'main'), here: pageContext(text, 'this'), main_bare: pageContext(bareText(text), 'main')};
      const templates = inContext(ctx.here, 'TEMPLATE_DEFS'), out = {};
      for (const [tname, def] of Object.entries(templates)) for (const [f, label] of Object.entries(d47.SETTINGS)) {
        const entry = out[`${tname}, ${label}`] = {};
        const results = {};
        for (const which of ['here', 'main']) {
          results[which] = inContext(ctx[which], `ownResults(${J(def)}, ${J(f)})`);
          const cand = Object.keys(results[which]).filter(pid => {
            const c = inContext(ctx[which], `stepCands(${J(pid)}, ${J(def)}, ${J(f)})`);
            return c && def.nodes.every(n => (c['step:' + n.id] || []).length > 0);
          });
          entry[which] = {candidates: Object.fromEntries(cand.map(pid => [pid, results[which][pid].status])),
                          status: Object.fromEntries(Object.entries(results[which]).map(([pid, r]) => [pid, r.status]))};
        }
        const pids = Object.keys(results.here);
        if (f !== 'off') checks.other_settings_differing += pids.filter(pid => !same(results.here[pid], results.main[pid])).length;
        else {
          const bare = inContext(ctx.main_bare, `ownResults(${J(def)}, ${J(f)})`);
          checks.off_unlike_removal += pids.filter(pid => !same(results.here[pid], bare[pid])).length;
          checks.off_changed += pids.filter(pid => !same(results.here[pid], results.main[pid])).length;
        }
      }
      names = names || Object.keys(out);
      perSeed[seed] = {cohort_sha256: shaText(text), keys: d47.planKeys(seed), templates: out,
                       noted_items: inContext(ctx.here, 'notedItems()').search.length};
      used.push(seed);
      console.error(`seed ${seed}: ${perSeed[seed].noted_items} noted items; ` + names.filter(n => n.endsWith('coded only'))
        .map(n => `${n} ${Object.keys(out[n].here.candidates).length}`).join('; '));
    };
    for (const seed of SMOKE ? [4601] : SEEDS) measure(seed);
    const pooled = n => used.reduce((k, s) => k + Object.keys(perSeed[s].templates[n].here.candidates).length, 0);
    for (const seed of SMOKE ? [] : EXTEND) { if (!names.filter(n => n.endsWith('coded only')).some(n => pooled(n) < d47.MINIMUM)) break; measure(seed); }
    const templates = Object.fromEntries(names.map(n => [n, d47.pooledReadings(perSeed, used, n)]));
    const owner = Object.fromEntries(names.map(n => {
      const h = templates[n].here, m = templates[n].main;
      return [n, {here: {candidates: h.candidates, without_C001: h.without_C001.candidates, types: h.distinct_child_types.types,
                         types_unresolved: h.distinct_child_types.types_unresolved, types_decided: h.distinct_child_types.types_decided,
                         statuses: h.statuses},
                  main: {candidates: m.candidates, without_C001: m.without_C001.candidates, types: m.distinct_child_types.types,
                         types_unresolved: m.distinct_child_types.types_unresolved, types_decided: m.distinct_child_types.types_decided,
                         statuses: m.statuses},
                  owner_bar: h.candidates >= d47.MINIMUM && h.distinct_child_types.types > 0
                    && h.distinct_child_types.types_unresolved <= d47.SHARE * h.distinct_child_types.types}];
    }));
    const met = {other_settings_identical: checks.other_settings_differing === 0, removal: checks.off_unlike_removal === 0};
    write(output, {
      profile: 'd53-coded-start-usefulness-1.0',
      rule: ('D53 rule 5: the page\'s own search (matchPatient) for each of the five templates under the three settings, over '
             + 'fresh seeds 5301 to 5330, extended in order to 5400 while a template has fewer than 30 candidates pooled under '
             + 'coded only (patients with a window and a candidate for every step). Ruled, per seed: under stated and hedged '
             + 'findings every result equals main\'s; under coded only every result equals main\'s on the cohort without letter '
             + 'mentions. Reported: the owner\'s counting rule (distinct child types, PLAN keys, C001 the reference; unresolved '
             + 'at most 30%), all candidates and the count without C001, here and main'),
      base: BASE, declared_seeds: SEEDS, extension_seeds: EXTEND, seeds_used: used, checks, met, all_met: Object.values(met).every(Boolean),
      owner_rule_reported: owner, templates,
      per_seed: Object.fromEntries(used.map(s => [s, {cohort_sha256: perSeed[s].cohort_sha256, noted_items: perSeed[s].noted_items}])),
      artifacts: artifacts(), contains_patient_level_data: false, ...(SMOKE ? {smoke: true} : {}),
    });
    console.error(`usefulness: ${Object.entries(met).map(([k, v]) => `${k} ${v ? 'met' : 'MISSED'}`).join(', ')}; ${checks.off_changed} coded-only results changed over ${used.length} seeds`);
  } finally {
    fs.rmSync(work, {recursive: true, force: true});
  }
}

const ARTIFACTS = ['demos/mockups/d53_coded_start.cjs', 'demos/mockups/d47_page.cjs', 'demos/mockups/d49_flip.cjs',
  'demos/mockups/option_sweep.js', 'demos/mockups/trajectory/store_request.js', 'demos/mockups/trajectory/app.js',
  'demos/mockups/shared/core.js', 'demos/mockups/synthetic/generate.py'];
const artifacts = () => Object.fromEntries(ARTIFACTS.map(f => [f, sha(f)]));
const write = (output, report) => {
  if (SMOKE && output && path.resolve(output).startsWith(path.join(root, 'verification'))) throw new Error('a smoke run is never written into verification/');
  const text = JSON.stringify(report, null, 1) + '\n';
  if (output) fs.writeFileSync(output, text); else process.stdout.write(text);
};

module.exports = {requestDiff, traced, censusMet, SEARCH_FIELDS, SEEDS, EXTEND};

if (require.main === module) {
  const what = process.argv[2], output = arg('--output', null);
  if (what === 'census') census(output);
  else if (what === 'usefulness') usefulness(output);
  else { console.error('usage: node demos/mockups/d53_coded_start.cjs census|usefulness [--output FILE]'); process.exit(2); }
}
