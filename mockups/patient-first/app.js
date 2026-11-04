'use strict';
/* Start from a patient: the record and similarity view. Trajectories are defined on their
   own page (../trajectory/); this page compares patients over an index window. */

const MODE_LABEL = {must: 'Must match', prefer: 'Prefer similar', ignore: 'Ignore'};
Object.assign(state, {
  referenceId: 'C001', lens: 'recorded', selected: null, compareId: null, align: 'age',
  revisions: [], current: 0, draft: null, onlyWithCases: false,
  hiddenConcepts: new Set(), hiddenFeatures: new Set(),
});

function defaultQuery() {
  return {
    window: {start: {kind: 'birth'}, end: {kind: 'age', amount: 24, unit: 'month'}},
    knownByIndex: true,
    freeText: 'present',
    features: {
      phenotype: {mode: 'prefer', required: [], excluded: []},
      onset: {mode: 'prefer', tolerance: 6},
      gene: {mode: 'prefer', pathogenicOnly: false},
      diagnosis: {mode: 'prefer'},
      medication: {mode: 'prefer', required: []},
      sex: {mode: 'ignore'},
    },
  };
}

const hedged = v => v && v.items.length > 0 && v.items.every(i => i.possibleOnly);
/* Hide from view: one rule for every surface on the page, never applied to the computation. */
const FEATURE_OF_KIND = {phenotype: 'phenotype', diagnosis: 'diagnosis', genetic: 'gene', gene: 'gene', medication: 'medication'};
const hiddenConcept = c => state.hiddenConcepts.has(c) || state.hiddenFeatures.has(FEATURE_OF_KIND[state.concepts[c]?.kind]);
const hiddenItem = i => hiddenConcept(i.concept) || (!!i.textConcept && hiddenConcept(i.textConcept)) || state.hiddenFeatures.has(FEATURE_OF_KIND[i.kind]);
hooks.hidden = i => hiddenItem(i);
const sexText = (sex, form) => state.hiddenFeatures.has('sex') ? (form === 'noun' ? 'Patient' : 'sex hidden') : form === 'noun' ? (sex === 'F' ? 'Female' : 'Male') : (sex === 'F' ? 'female' : 'male');
const hiddenNote = n => n ? `${n} hidden from view` : '';
function valueText(f, v) {
  if (!v) return null;
  if (state.hiddenFeatures.has(f.key)) return 'hidden from view';
  let text;
  if (f.key === 'phenotype' || f.key === 'medication') {
    const all = [...v.value], shown = all.filter(c => !hiddenConcept(c));
    text = [shown.map(label).join(', '), hiddenNote(all.length - shown.length)].filter(Boolean).join(' · ');
  } else if ((f.key === 'diagnosis' && hiddenConcept(v.value)) || (f.key === 'gene' && hiddenConcept(`gene:${v.value.gene}`))) {
    text = 'hidden from view';
  } else text = f.show(v.value);
  return text + (hedged(v) ? ' (hedged in a letter)' : '');
}

const jaccard = (a, b) => {
  const inter = [...a].filter(x => b.has(x)).length;
  return inter / (new Set([...a, ...b]).size || 1);
};
const expand = set => new Set([...set].flatMap(c => [...ancestors(c)]));

const FEATURES = [
  {
    key: 'phenotype', label: 'Clinical findings',
    meaning: 'Findings that occurred inside the window. A finding counts towards its broader class, so a febrile seizure also counts as a seizure.',
    extract(view, q, win) {
      const items = usable(view, q, win).filter(i => i.kind === 'phenotype' && !q.features.phenotype.excluded.includes(conceptFor(i, q)));
      return items.length ? {value: new Set(items.map(i => conceptFor(i, q))), items} : null;
    },
    sim: (a, b) => jaccard(expand(a), expand(b)),
    must(a, b, f) { const need = f.required.length ? f.required : [...a]; return need.every(r => [...b].some(c => isA(c, r))); },
    fail(a, b, f) { const need = f.required.length ? f.required : [...a]; return 'lacks ' + need.filter(r => ![...b].some(c => isA(c, r))).map(label).join(', '); },
    show: v => [...v].map(label).join(', '),
  },
  {
    key: 'onset', label: 'Age at first seizure',
    meaning: 'Earliest seizure of any type recorded by the end of the window, even if before it starts. Within a year scores partially; the same month scores 1.',
    extract(view, q, win) {
      const items = usable(view, q, {start: -Infinity, end: win.end}).filter(i => i.kind === 'phenotype' && isA(conceptFor(i, q), 'seizure'));
      if (!items.length) return null;
      const first = items.reduce((a, b) => a.time.best.e <= b.time.best.e ? a : b);
      return {value: first.time.best.e, items: [first]};
    },
    sim: (a, b) => Math.max(0, 1 - Math.abs(a - b) / 365),
    must: (a, b, f) => Math.abs(a - b) <= f.tolerance * MONTH,
    fail: (a, b, f) => `first seizure at ${ageText(b)}, outside ±${f.tolerance} months of ${ageText(a)}`,
    show: v => ageText(v),
  },
  {
    key: 'gene', label: 'Genetic finding',
    meaning: 'The classification the laboratory had reported by the end of the window, not what it says today.',
    extract(view, q, win) {
      const results = usable(view, q, win).filter(i => i.kind === 'genetic').map(i => ({item: i, r: knownResult(i, win.end, q)}));
      if (!results.length) return null;
      const rank = r => r.classification === 'pathogenic' || r.classification === 'homozygous deletion' ? 0 : r.classification === 'uncertain significance' ? 1 : 2;
      const best = results.sort((a, b) => rank(a.r) - rank(b.r))[0];
      return {value: {gene: best.item.gene.gene, classification: best.r.classification, hgvs: best.r.hgvs}, items: [best.item]};
    },
    sim(a, b) {
      if (b.classification === 'no relevant variant' || a.gene !== b.gene) return 0;
      return a.classification === b.classification ? 1 : 0.6;
    },
    must(a, b, f) {
      if (a.gene !== b.gene || b.classification === 'no relevant variant') return false;
      return !f.pathogenicOnly || b.classification === 'pathogenic' || b.classification === 'homozygous deletion';
    },
    fail: (a, b) => b.classification === 'no relevant variant' ? `${b.gene} tested, no relevant variant` : `${b.gene} ${b.classification}`,
    show: v => v.classification === 'no relevant variant' ? `${v.gene}: no relevant variant` : `${v.gene} · ${v.classification}`,
  },
  {
    key: 'diagnosis', label: 'Diagnosis',
    meaning: 'Most specific diagnosis established by the end of the window, through any terminology. A narrower diagnosis satisfies a broader one.',
    extract(view, q, win) {
      const items = usable(view, q, win).filter(i => i.kind === 'diagnosis');
      if (!items.length) return null;
      const concept = mostSpecific(items.map(i => conceptFor(i, q)));
      return {value: concept, items: items.filter(i => conceptFor(i, q) === concept)};
    },
    sim(a, b) {
      if (a === b) return 1;
      if (isA(a, b) || isA(b, a)) return 0.6;
      if (isA(a, 'dee') && isA(b, 'dee')) return 0.4;
      return isA(a, 'epilepsy') && isA(b, 'epilepsy') ? 0.15 : 0;
    },
    must: (a, b) => isA(b, a),
    fail: (a, b) => `recorded as ${label(b)}`,
    show: v => label(v),
  },
  {
    key: 'medication', label: 'Medication exposures',
    meaning: 'Medicines taken at any time during the window. Sodium-channel blockers also count as a class.',
    extract(view, q, win) {
      const items = usable(view, q, win).filter(i => i.kind === 'medication' && i.concept !== 'midazolam');
      return items.length ? {value: new Set(items.map(i => i.concept)), items} : null;
    },
    sim(a, b) {
      const widen = s => new Set([...s, ...[...s].flatMap(c => classesOf(c).filter(k => k !== 'antiseizure'))]);
      return jaccard(widen(a), widen(b));
    },
    must(a, b, f) {
      const need = f.required.length ? f.required : [...a];
      return need.every(r => [...b].some(c => c === r || classesOf(c).includes(r)));
    },
    fail(a, b, f) { const need = f.required.length ? f.required : [...a]; return 'not exposed to ' + need.filter(r => ![...b].some(c => c === r || classesOf(c).includes(r))).map(label).join(', '); },
    show: v => [...v].map(label).join(', '),
  },
  {
    key: 'sex', label: 'Sex',
    meaning: 'Recorded sex. Relevant for X-linked conditions such as PCDH19, CDKL5 and Rett syndrome.',
    extract: view => ({value: view.p.sex, items: []}),
    sim: (a, b) => a === b ? 1 : 0, must: (a, b) => a === b, fail: (a, b) => `recorded ${b}`,
    show: v => ({F: 'Female', M: 'Male'})[v] || v,
  },
];
const FEATURE = Object.fromEntries(FEATURES.map(f => [f.key, f]));

function extractAll(view, q, win) {
  return Object.fromEntries(FEATURES.map(f => [f.key, f.extract(view, q, win)]));
}

/* Pairwise comparison: what two patients share exactly, and what only one of them has */

function compareKey(item, q) {
  return item.kind === 'phenotype' || item.kind === 'diagnosis' ? conceptFor(item, q) : item.concept;
}

function relation(a, b) {
  if (!a || !b || a === b) return null;
  if (isA(b, a)) return 'narrower';
  if (isA(a, b)) return 'broader';
  const shared = classesOf(a).filter(k => k !== 'antiseizure' && classesOf(b).includes(k));
  return shared.length ? `same class: ${({sodium_channel_blocker: 'sodium-channel blocker'})[shared[0]] || label(shared[0]).toLowerCase()}` : null;
}

const SAME = {
  onset: (a, b) => Math.round(a / MONTH) === Math.round(b / MONTH),
  gene: (a, b) => a.gene === b.gene && a.classification === b.classification,
};

function partition(f, rv, cv, q, refPid, pid) {
  const ids = (who, v, keep = () => true) => v ? v.items.filter(keep).map(i => `${who}|${i.id}`) : [];
  const out = {shared: [], ref: [], cand: []};
  if (f.key === 'phenotype' || f.key === 'medication') {
    const A = rv ? [...rv.value] : [], B = cv ? [...cv.value] : [];
    const of = c => i => compareKey(i, q) === c;
    const note = (c, others, who) => {
      const o = others.find(x => relation(c, x) && !hiddenConcept(x));
      return o ? `≈ ${who} has ${label(o)} (${relation(c, o)})` : '';
    };
    for (const c of A) {
      if (B.includes(c)) out.shared.push({concept: c, text: label(c), ids: [...ids(refPid, rv, of(c)), ...ids(pid, cv, of(c))]});
      else out.ref.push({concept: c, text: label(c), ids: ids(refPid, rv, of(c)), note: note(c, B.filter(x => !A.includes(x)), pid)});
    }
    for (const c of B.filter(x => !A.includes(x))) {
      out.cand.push({concept: c, text: label(c), ids: ids(pid, cv, of(c)), note: note(c, A.filter(x => !B.includes(x)), refPid)});
    }
  } else if (rv && cv && (SAME[f.key] || ((a, b) => a === b))(rv.value, cv.value)) {
    out.shared.push({text: valueText(f, rv), ids: [...ids(refPid, rv), ...ids(pid, cv)]});
  } else {
    let note = '';
    if (rv && cv && f.key === 'onset') {
      const apart = Math.abs(Math.round((rv.value - cv.value) / MONTH));
      note = `${apart} month${apart === 1 ? '' : 's'} apart`;
    }
    if (rv && cv && f.key === 'gene' && rv.value.gene === cv.value.gene) note = 'same gene, different classification';
    if (rv && cv && f.key === 'diagnosis' && relation(rv.value, cv.value)) note = `≈ ${pid}'s is ${relation(rv.value, cv.value)}`;
    if (rv) out.ref.push({text: valueText(f, rv), ids: ids(refPid, rv), note});
    if (cv) out.cand.push({text: valueText(f, cv), ids: ids(pid, cv), note: rv ? '' : ''});
  }
  for (const col of ['shared', 'ref', 'cand']) {
    const hidden = out[col].filter(c => c.concept && hiddenConcept(c.concept));
    if (hidden.length) out[col] = [...out[col].filter(c => !hidden.includes(c)), {text: hiddenNote(hidden.length), placeholder: true}];
  }
  if (!rv) out.ref.push({text: 'not recorded', missing: true});
  if (!cv) out.cand.push({text: 'not recorded', missing: true});
  return out;
}

function qualityByEvent(v, f) {
  if (!v) return '';
  const withCases = v.items.filter(i => i.cases.length);
  if (state.hiddenFeatures.has(f.key)) {
    const n = withCases.reduce((t, i) => t + i.cases.length, 0);
    return n ? `<span class="pair-hidden">${n} case${n > 1 ? 's' : ''} hidden from view</span>` : '';
  }
  const hiddenCases = withCases.filter(hiddenItem).reduce((t, i) => t + i.cases.length, 0);
  return withCases.filter(i => !hiddenItem(i)).map(i => {
    const when = i.kind === 'medication' ? i.med.start?.e : i.time.best?.e;
    return `<div class="qrow"><strong>${esc(i.display)}</strong>${when != null ? ` <span class="hint">${esc(ageText(when))}</span>` : ''}<ul>${i.cases.map(c => {
      const kind = state.data.quality_kinds[c.kind];
      return `<li><span class="tag ${kind.category}">${esc(CATEGORY[kind.category])}</span> ${esc(c.kind.replace(/-/g, ' '))}: ${esc(c.detail || kind.description)}</li>`;
    }).join('')}</ul></div>`;
  }).join('') + (hiddenCases ? `<span class="pair-hidden">${hiddenCases} case${hiddenCases > 1 ? 's' : ''} on events hidden from view</span>` : '');
}

function pairChips(list) {
  return list.map(c => c.missing ? '<span class="pair-missing">not recorded in the window</span>'
    : c.placeholder ? `<span class="pair-hidden">${esc(c.text)}</span>`
    : `<span class="pair-chip" data-link="${esc((c.ids || []).join(' '))}">${esc(c.text)}${c.note ? `<small>${esc(c.note)}</small>` : ''}</span>`).join('');
}

/* What one event looks like from the other patient's side, for hovering in the drawer */
function compareTip(pid, id) {
  const q = currentRev().query, res = currentRev().result;
  const other = pid === state.referenceId ? state.compareId : state.referenceId;
  const view = evidenceFor(pid), oview = evidenceFor(other);
  const it = view.byId.get(id);
  if (!it) return null;
  const winOf = who => who === state.referenceId ? res.ref.win : (res.rows.find(r => r.pid === who)?.win ?? windowFor(evidenceFor(who), q));
  const win = winOf(pid), owin = winOf(other);
  const key = compareKey(it, q);
  const when = (i, at) => {
    const d = i.kind === 'medication' ? i.med.start.e : i.time.best.e;
    return `${ageText(d)}${at?.end == null ? '' : d < at.start ? ' (before the window)' : d > at.end ? ' (after the window)' : ' (in the window)'}`;
  };
  const same = oview.items.filter(o => compareKey(o, q) === key && (o.kind === 'medication' ? o.med.start : o.time.best));
  const nearAll = same.length ? [] : oview.items.filter(o => relation(key, compareKey(o, q)) && (o.kind === 'medication' ? o.med.start : o.time.best));
  const near = nearAll.filter(o => !hiddenItem(o));
  const nearHidden = nearAll.length - near.length;
  const used = win.end != null && usable(view, q, win).includes(it);
  const lines = [`<strong>${esc(it.display)}</strong> · ${esc(pid)} at ${esc(when(it, win))}`];
  if (same.length) {
    lines.push(`<span class="tip-shared">■ Shared exactly</span> — ${esc(other)} has it at ${esc(same.slice(0, 4).map(o => when(o, owin)).join(', '))}${same.length > 4 ? ` and ${same.length - 4} more` : ''}`);
  } else if (near.length || nearHidden) {
    lines.push(`<span class="tip-near">≈ Only in ${esc(pid)}</span> — ${esc(other)} has related: ${esc([...uniq(near.map(o => `${o.display} (${relation(key, compareKey(o, q))})`)).slice(0, 3), hiddenNote(nearHidden)].filter(Boolean).join('; '))}`);
  } else {
    lines.push(`<span class="tip-unique">○ Only in ${esc(pid)}</span> — nothing equivalent recorded for ${esc(other)}`);
  }
  const at = it.kind === 'medication' ? it.med.start.e : it.time.best.e;
  lines.push(`<span class="tip-muted">${used ? 'Used in the comparison' : win.end == null ? `No window for ${esc(pid)}: ${esc(win.reason)}` :
    it.kind === 'procedure' ? 'Tests are not compared' : at > win.end ? 'After the window: not compared' :
    !admissible(it, q) ? 'From letters only; excluded by the free-text setting' :
    it.availableDay > win.end && q.knownByIndex ? 'Not yet available by the end of the window, so not compared' : 'Before the window starts: not compared'}</span>`);
  return {html: lines.join('<br>'), links: [...same, ...near].map(o => `${other}|${o.id}`), exact: same.length > 0};
}

function clearLinks() {
  document.querySelectorAll('.linked-exact, .linked-near, .pair-chip.hot').forEach(el => el.classList.remove('linked-exact', 'linked-near', 'hot'));
}

function linkEvents(links, exact) {
  for (const link of links) {
    const [pid, id] = link.split('|');
    document.querySelectorAll(`#compare .ev[data-pid="${CSS.escape(pid)}"][data-id="${CSS.escape(id)}"]`)
      .forEach(el => el.classList.add(exact ? 'linked-exact' : 'linked-near'));
  }
}

function evaluate(q) {
  const refView = evidenceFor(state.referenceId);
  const refWin = windowFor(refView, q);
  const ref = {win: refWin, values: refWin.end == null ? {} : extractAll(refView, q, refWin)};
  const rows = [], excluded = [];
  for (const pid of state.patients.keys()) {
    if (pid === state.referenceId) continue;
    const view = evidenceFor(pid);
    const win = windowFor(view, q);
    if (win.reason) { excluded.push({pid, reason: win.reason}); continue; }
    const values = extractAll(view, q, win);
    let failed = null, total = 0, n = 0, observed = 0;
    const sims = {};
    for (const f of FEATURES) {
      const fq = q.features[f.key], rv = ref.values[f.key], cv = values[f.key];
      if (fq.mode === 'must' && rv) {
        if (!cv) failed = failed || `${f.label}: nothing recorded in the window`;
        else if (!f.must(rv.value, cv.value, fq)) failed = failed || `${f.label}: ${f.fail(rv.value, cv.value, fq)}`;
      } else if (fq.mode === 'prefer' && rv) {
        n += 1;
        sims[f.key] = cv ? f.sim(rv.value, cv.value) : null;
        if (cv) { observed += 1; total += sims[f.key]; }
      }
    }
    if (failed) { excluded.push({pid, reason: failed}); continue; }
    const used = uniq(Object.values(values).filter(Boolean).flatMap(v => v.items));
    rows.push({pid, win, values, sims, n, observed, score: n ? total / n : 0, coverage: n ? observed / n : 0,
               cases: uniq(used.flatMap(i => i.cases))});
  }
  rows.sort((a, b) => b.score - a.score || b.coverage - a.coverage || a.pid.localeCompare(b.pid));
  return {ref, rows, excluded};
}

/* Revisions */

const currentRev = () => state.revisions[state.current];

function describeChange(a, b) {
  const out = [];
  if (JSON.stringify(a.window) !== JSON.stringify(b.window)) out.push(`window ${windowText(b.window)}`);
  if (a.knownByIndex !== b.knownByIndex) out.push(b.knownByIndex ? 'only what was available by the window\'s end' : 'everything that had happened by the window\'s end');
  if (a.freeText !== b.freeText) out.push(`free text: ${({off: 'ignored', present: 'stated findings', possible: 'stated and hedged'})[b.freeText]}`);
  for (const f of FEATURES) {
    const x = a.features[f.key], y = b.features[f.key];
    if (x.mode !== y.mode) out.push(`${f.label}: ${MODE_LABEL[y.mode]}`);
    else if (JSON.stringify(x) !== JSON.stringify(y)) out.push(`${f.label} settings`);
  }
  return out.join('; ') || 'no change';
}

function membershipDelta(before, after) {
  const a = new Set(before.rows.map(r => r.pid)), b = new Set(after.rows.map(r => r.pid));
  const added = [...b].filter(x => !a.has(x)), removed = [...a].filter(x => !b.has(x));
  const topA = before.rows.slice(0, 10).map(r => r.pid).join(), topB = after.rows.slice(0, 10).map(r => r.pid).join();
  return {added, removed, reranked: topA !== topB};
}
const deltaText = d => d ? `${d.added.length ? '+' + d.added.length : '±0'} / ${d.removed.length ? '−' + d.removed.length : '−0'} eligible${d.reranked ? ' · order changed' : ''}` : 'start';

function resetRevisions(labelText) {
  const q = state.revisions.length ? clone(currentRev().query) : defaultQuery();
  state.revisions = [{id: 0, parent: null, query: q, label: labelText, result: evaluate(q), delta: null}];
  state.current = 0;
  state.draft = clone(q);
}

function applyDraft() {
  const cur = currentRev();
  const result = evaluate(state.draft);
  state.revisions.push({id: state.revisions.length, parent: cur.id, query: clone(state.draft),
                        label: describeChange(cur.query, state.draft), result, delta: membershipDelta(cur.result, result)});
  state.current = state.revisions.length - 1;
  render();
}

const dirty = () => JSON.stringify(state.draft) !== JSON.stringify(currentRev().query);


/* Rendering */

function render() {
  renderContext();
  renderHistory();
  renderCriteria();
  renderResults();
  renderRevisions();
  if (state.compareId) renderCompare();
}

function renderContext() {
  const q = currentRev().query;
  const items = [
    ['Dataset', `${state.data.dataset_id} · ${state.patients.size} patients`],
    ['Reference', state.referenceId],
    ['Index window', windowText(q.window)],
    ['Revision', `r${state.current}${dirty() ? ' · draft has changes' : ''}`],
    ['Result type', 'Ranked candidates, not a cohort'],
  ];
  $('#context').innerHTML = items.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd title="${esc(v)}">${esc(v)}</dd></div>`).join('');
  document.querySelectorAll('[data-action="lens"]').forEach(b => b.setAttribute('aria-checked', String(b.dataset.lens === state.lens)));
}

function renderHistory() {
  const view = evidenceFor(state.referenceId), p = view.p, q = currentRev().query;
  const win = windowFor(view, q);
  $('#reference').innerHTML = state.data.patients.map(c => `<option value="${c.patient_id}" ${c.patient_id === state.referenceId ? 'selected' : ''}>${c.patient_id} · ${sexText(c.sex)} · born ${c.birth_date.slice(0, 4)}${c.patient_id === state.data.reference_patient_id ? ' · suggested' : ''}</option>`).join('');
  $('#reference-summary').innerHTML = `${sexText(p.sex, 'noun')}, born ${p.birth_date}${p.gestational_age_weeks < 37 ? ` at ${p.gestational_age_weeks} weeks` : ''} · `
    + `${p.records.length} records from ${uniq(p.records.map(r => r.source)).length} sources · followed to ${ageText(view.lastContact)} · `
    + `${p.quality.length} known data-quality cases`
    + (state.lens === 'truth' ? ` · <strong>Showing the generator's truth</strong>: dashed shapes happened but were never recorded; red dashes join a truth event to where the records put it.` : '');
  const width = $('#timeline').clientWidth || 640;
  $('#timeline').innerHTML = timelineSVG(state.referenceId, {width, minDay: 0, maxDay: Math.max(view.snapshot, 400), window: win,
                                                              lens: state.lens, selectable: true, selected: state.selected, title: `Timeline for ${p.patient_id}`});
  const listed = view.items.filter(i => i.kind === 'medication' ? i.med.start : i.time.best);
  const rows = listed.filter(i => !hiddenItem(i))
    .sort((a, b) => (a.kind === 'medication' ? a.med.start.e : a.time.best.e) - (b.kind === 'medication' ? b.med.start.e : b.time.best.e));
  $('#event-list').innerHTML = `<table><thead><tr><th>Age</th><th>Event</th><th>Sources</th><th>Quality</th></tr></thead><tbody>${rows.map(i =>
    `<tr><td>${esc(timeTip(i).replace(/^started /, ''))}</td><td><button class="linklike" data-action="select" data-id="${esc(i.id)}">${esc(i.display)}</button>${i.freeTextOnly ? ' <small>(letters only)</small>' : ''}</td>`
    + `<td>${esc(uniq([...i.recs.map(r => SOURCE_SHORT[r.source]), ...i.mentions.map(x => SOURCE_SHORT[x.note.source])]).join(', '))}</td><td>${i.cases.length || ''}</td></tr>`).join('')}</tbody></table>`
    + (listed.length > rows.length ? `<p class="hint">${hiddenNote(listed.length - rows.length)}. Hidden events still count in the comparison.</p>` : '');
  renderInspector(view, win, q);
}

function renderInspector(view, win, q) {
  const box = $('#inspector');
  const id = state.selected;
  if (!id) { box.innerHTML = ''; return; }
  if (id === '__frequency') return void (box.innerHTML = frequencyInspector(view));
  if (id === '__growth') return void (box.innerHTML = growthInspector(view));
  if (id.startsWith('n:')) return void (box.innerHTML = letterInspector(view, id.slice(2)));
  const it = view.byId.get(id);
  if (!it) { box.innerHTML = ''; return; }
  if (hiddenItem(it)) {
    box.innerHTML = `<p class="hint">This event is hidden from view. It still counts in the comparison.</p><div class="actions"><button class="btn" data-action="ev-hide" data-id="${esc(it.id)}">Show in view</button></div>`;
    return;
  }
  const sources = uniq([...it.recs.map(r => r.source), ...it.mentions.map(x => x.note.source)]);
  const end = win.end ?? Infinity;
  const known = it.kind === 'genetic' ? !!knownResult(it, end, q) : it.availableDay <= end;
  const happened = it.kind === 'medication' ? it.med.start : it.time.best;
  const parts = [`<div class="insp-title"><strong>${esc(it.display)}</strong><span class="hint">${esc(it.kind)} · ${it.recs.length + it.mentions.length} record${it.recs.length + it.mentions.length === 1 ? '' : 's'} from ${sources.length} source${sources.length === 1 ? '' : 's'}</span></div>`];
  if (it.provisional) parts.push(`<p class="hint">A hedged statement made before anything was coded. It counts only when hedged findings are allowed.</p>`);
  if (it.textConcept) parts.push(`<p class="hint">Coded only as <strong>${esc(label(it.concept))}</strong>; a letter names it more specifically. Free-text settings decide which one the comparison uses.</p>`);
  parts.push('<dl class="kv">',
    `<dt>Happened</dt><dd>${esc(happened.as)} <span class="hint">(${esc(happened.precision)}${happened.l - happened.e > 1 ? `, between ${ageText(happened.e)} and ${ageText(happened.l)}` : `, at ${ageText(happened.e)}`}; from ${esc(SOURCE_SHORT[happened.source])})</span></dd>`);
  if (it.kind === 'medication') {
    const m = it.med;
    parts.push(`<dt>Stopped</dt><dd>${m.end ? `${esc(m.end.as)} <span class="hint">(${ageText(m.end.e)}–${ageText(m.end.l)}, ${esc(SOURCE_SHORT[m.end.source] || m.end.source)})</span>` : 'ongoing at last contact'}</dd>`,
               `<dt>Dispensings</dt><dd>${m.dispensings}${m.start.fromDispensing ? ' · <strong>no prescription in this hospital</strong>; start is the first dispensing' : ''}</dd>`);
  }
  if (it.time.conflicting) parts.push(`<dt>Conflict</dt><dd>Sources disagree: ${it.time.spans.map(s => `${esc(SOURCE_SHORT[s.source])} says ${esc(s.as)}`).join('; ')}</dd>`);
  parts.push(`<dt>Written down</dt><dd>${ageText(it.recordedDay)}${happened ? ` <span class="hint">(${Math.max(0, it.recordedDay - happened.e)} days after it began)</span>` : ''}</dd>`,
             `<dt>Available</dt><dd>${ageText(it.availableDay)} · ${win.end == null ? esc(win.reason) : known ? '<strong>known by the end of the window</strong>' : '<strong>not yet known at the end of the window</strong>'} (${esc(spanText(win.end == null ? null : win))})</dd>`, '</dl>');
  parts.push(timebar(happened, it.recordedDay, it.availableDay, win));
  if (it.kind === 'genetic') {
    const now = knownResult(it, end, q);
    parts.push('<h3>Laboratory history</h3><table class="mini"><tbody>' + it.gene.history.map(h =>
      `<tr><td>${ageText(h.recorded)}</td><td>${esc(h.classification)}${h.hgvs ? ` · <code>${esc(h.hgvs)}</code>` : ''}</td><td class="hint">${esc(h.status)}, filed at ${ageText(h.available)}</td></tr>`).join('')
      + `</tbody></table><p class="hint">At the end of the window the comparison uses: <strong>${esc(now ? now.classification : 'no result yet')}</strong>.</p>`);
  }
  if (it.procedure) parts.push(`<p class="plain">${esc(it.procedure.result || 'Ordered; no report was filed.')}</p>`);
  if (it.codings.length) {
    parts.push('<h3>Codes</h3><div class="chips">' + it.codings.map(c => {
      const cc = conceptOf(c);
      const broader = cc && cc !== it.concept && isA(it.textConcept || it.concept, cc) ? ' · broader' : '';
      return `<span class="chip"><code>${esc(codeText(c))}</code> ${esc(c.display)} <span class="src">· ${esc(SOURCE_SHORT[c.source])}${broader}</span></span>`;
    }).join('') + '</div>');
  }
  if (it.mentions.length) {
    parts.push('<h3>In letters</h3>' + it.mentions.slice(0, 4).map(x => snippet(x.note, x.m)).join('')
      + (it.mentions.length > 4 ? `<p class="hint">and ${it.mentions.length - 4} more mentions</p>` : ''));
  }
  if (it.cases.length) parts.push('<h3>Data quality</h3>' + it.cases.map(caseRow).join(''));
  parts.push('<h3>Use this event</h3>', eventActions(it));
  parts.push('<p class="provenance-note">These records are grouped into one event using the synthetic generator\'s ground truth. A real system would have to infer the grouping, and could get it wrong.</p>');
  box.innerHTML = parts.join('');
}

function timebar(happened, recorded, available, win) {
  if (!happened) return '';
  const idx = win.end ?? recorded;
  const lo = Math.min(happened.e, idx, recorded) - 20, hi = Math.max(happened.l, idx, available, recorded) + 20;
  const pct = d => ((d - lo) / (hi - lo) * 100).toFixed(1) + '%';
  return `<div class="timebar" aria-hidden="true">
    <div class="span" style="left:${pct(happened.e)};width:max(3px,calc(${pct(happened.l)} - ${pct(happened.e)}))" title="happened"></div>
    <div class="tick" style="left:${pct(recorded)};background:#68449A"></div><div class="lbl" style="left:${pct(recorded)}">written</div>
    <div class="tick" style="left:${pct(available)};background:#126B5D;top:8px"></div><div class="lbl" style="left:${pct(available)};top:34px">available</div>
    <div class="tick" style="left:${pct(idx)};background:#174EA6;width:3px"></div><div class="lbl" style="left:${pct(idx)};top:-12px;color:#174EA6">window end</div>
  </div>`;
}

function snippet(note, m) {
  const text = note.text, [s, e] = m.span;
  const from = Math.max(0, s - 90), to = Math.min(text.length, e + 90);
  const tags = [m.assertion !== 'present' ? m.assertion : '', m.experiencer !== 'patient' ? `about ${m.experiencer}` : '', m.historical ? 'historical' : '',
                note.language && note.language !== 'en' ? `in ${({nl: 'Dutch', fr: 'French'})[note.language]}` : ''].filter(Boolean);
  const mask = str => (note.mentions || []).filter(x => hiddenConcept(x.concept)).reduce((t, x) => t.split(x.text).join('[hidden]'), str);
  return `<div class="snippet"><span class="meta">${esc(SOURCE_SHORT[note.source])} · ${dateOf(note.occurred.earliest)}${tags.length ? ' · ' + esc(tags.join(' · ')) : ''}</span>`
    + `${from ? '…' : ''}${esc(mask(text.slice(from, s)))}<mark>${esc(text.slice(s, e))}</mark>${esc(mask(text.slice(e, to)))}${to < text.length ? '…' : ''}</div>`;
}

function caseRow(q) {
  const kind = state.data.quality_kinds[q.kind];
  return `<div class="case"><span><span class="tag ${kind.category}">${esc(CATEGORY[kind.category])}</span></span><span><strong>${esc(q.kind.replace(/-/g, ' '))}</strong> — ${esc(q.detail || kind.description)}</span></div>`;
}

function eventActions(it) {
  const b = (action, text, extra = '', cls = 'btn') => `<button class="${cls}" data-action="${action}" data-id="${esc(it.id)}" ${extra}>${esc(text)}</button>`;
  const out = [];
  if (it.kind === 'phenotype') {
    out.push(b('ev-must', 'Must match: require this finding', '', 'btn must'), b('ev-prefer', 'Prefer similar'), b('ev-ignore', 'Ignore this finding'));
  } else if (it.kind === 'diagnosis') {
    out.push(b('ev-must', `Must match: ${label(it.textConcept || it.concept)}`, '', 'btn must'), b('ev-prefer', 'Prefer similar'), b('ev-ignore', 'Ignore diagnosis'));
  } else if (it.kind === 'genetic') {
    out.push(b('ev-must', `Must match: ${it.gene.gene}`, '', 'btn must'), b('ev-prefer', 'Prefer similar'), b('ev-ignore', 'Ignore genetics'));
  } else if (it.kind === 'medication') {
    out.push(b('ev-must', `Must match: exposed to ${label(it.concept).toLowerCase()}`, '', 'btn must'));
    if (classesOf(it.concept).includes('sodium_channel_blocker')) out.push(b('ev-must-class', 'Must match: any sodium-channel blocker', '', 'btn must'));
    out.push(b('ev-prefer', 'Prefer similar'), b('ev-ignore', 'Ignore medication'));
  }
  out.push(b('ev-hide', state.hiddenConcepts.has(it.concept) ? 'Show in view' : 'Hide from view'));
  return `<div class="actions">${out.join('')}</div><p class="hint">Changes go into the draft in Comparison; nothing is recomputed until you apply them. Hide from view changes only this page.</p>`;
}

function frequencyInspector(view) {
  const fs = view.series.seizure_frequency;
  const missing = view.unattached.filter(q => q.kind === 'missing-measurement' && /diary/.test(q.detail)).length;
  return `<div class="insp-title"><strong>Seizure diary</strong><span class="hint">parent-reported · ${fs.length} months</span></div>
    <p class="hint">Monthly counts entered by parents in a diary app; uploaded at clinic visits, so they become available weeks after the month they describe.${missing ? ` ${missing} months have no entries.` : ''}</p>
    <p class="hint">Trajectories over this diary, such as a rise after an exposure, are defined on the <a href="../trajectory/">trajectory page</a>.</p>`;
}

function growthInspector(view) {
  const flagged = [...view.series.weight, ...view.series.height, ...view.series.head_circ].filter(s => s.cases.length);
  return `<div class="insp-title"><strong>Growth</strong><span class="hint">${view.series.weight.length} weights · ${view.series.height.length} heights · ${view.series.head_circ.length} head circumferences</span></div>
    ${flagged.length ? '<h3>Known entry problems</h3>' + flagged.map(s => `<div class="case"><span>${ageText(s.day)}</span><span>${esc(s.record.label_as_recorded)}: <code>${esc(s.value)} ${esc(s.unit ?? '(no unit)')}</code> — ${s.cases.map(q => esc(q.detail)).join('; ')}</span></div>`).join('') : '<p class="hint">No known entry problems.</p>'}
    <p class="hint">A comparison that used growth would have to exclude or correct these, and say which it did.</p>`;
}

function letterInspector(view, rid) {
  const l = view.letters.find(x => x.record.record_id === rid);
  if (!l) return '';
  let html = esc(l.record.text);
  const marks = [...(l.record.mentions || [])].sort((a, b) => b.span[0] - a.span[0]);
  let text = l.record.text;
  for (const m of marks) {
    const tag = [m.assertion !== 'present' ? m.assertion : '', m.experiencer !== 'patient' ? m.experiencer : ''].filter(Boolean).join(', ');
    text = text.slice(0, m.span[0]) + `\u0001${m.assertion}|${tag}\u0002` + (hiddenConcept(m.concept) ? '[hidden]' : text.slice(m.span[0], m.span[1])) + '\u0003' + text.slice(m.span[1]);
  }
  html = esc(text).replace(/\u0001([^|]*)\|([^\u0002]*)\u0002/g, (_, a, t) => `<mark title="${t}" style="${a === 'present' ? '' : 'background:#EDE7F6'}">`).replace(/\u0003/g, '</mark>');
  return `<div class="insp-title"><strong>${esc(l.record.label_as_recorded)}</strong><span class="hint">${esc(SOURCE_SHORT[l.record.source])} · ${dateOf(l.record.occurred.earliest)} · signed and available ${ageText(l.availableDay)}</span></div>
    <div class="snippet">${html}</div>
    <p class="hint">Yellow: stated findings. Lilac: negated, hedged, hypothetical or about a relative; none of those become evidence about the patient.</p>
    ${l.cases.length ? '<h3>Data quality</h3>' + l.cases.map(caseRow).join('') : ''}`;
}

function segmented(key, mode, refMissing) {
  return `<div class="segmented" role="group" aria-label="${esc(FEATURE[key].label)}">${Object.entries(MODE_LABEL).map(([m, t]) =>
    `<button aria-pressed="${mode === m}" data-action="mode" data-key="${key}" data-mode="${m}" ${m === 'must' && refMissing ? 'disabled title="The reference has nothing recorded before the index"' : ''}>${t}</button>`).join('')}</div>`;
}

function expansionFor(key, refValue, q) {
  if (key === 'diagnosis' && refValue) {
    const target = refValue.value;
    const narrower = Object.keys(state.concepts).filter(c => c !== target && isA(c, target));
    const yes = [target, ...narrower].flatMap(c => state.concepts[c].codings.map(codeText));
    const no = (state.concepts[target].parents || []).flatMap(c => state.concepts[c].codings.map(codeText));
    return `<div class="expansion">Counts: ${esc(yes.join(' · ') || label(target))}${q.freeText !== 'off' ? ' · letters naming it' : ''}${no.length ? `<br><span class="no">Does not count: ${esc(no.join(' · '))}</span> (broader); a letter can refine one.` : ''}</div>`;
  }
  if (key === 'gene' && refValue) return `<div class="expansion">Reference at the end of the window: ${esc(FEATURE.gene.show(refValue.value))}${refValue.value.hgvs ? ` (<code>${esc(refValue.value.hgvs)}</code>)` : ''}</div>`;
  return '';
}

function renderCriteria() {
  const q = state.draft, applied = currentRev();
  const refView = evidenceFor(state.referenceId);
  const draftResult = evaluate(q);
  const ref = draftResult.ref;
  const out = [];
  out.push(`<div class="index-box"><strong>Index window</strong> — each patient is compared over their own window, placed by the same rule.
    ${boundEditor('start', q.window.start, q.window.end)}${boundEditor('end', q.window.end, q.window.start)}
    <div class="plain" style="margin-top:8px"><strong>Reads as:</strong> ${esc(windowText(q.window))}. Reference: ${esc(ref.win.end == null ? ref.win.reason : spanText(ref.win))}.</div>
    <div class="options" style="margin-top:8px"><label><input type="checkbox" data-action="known" ${q.knownByIndex ? 'checked' : ''}><span>Use only what was <em>available</em> by the end of the window, not just what had happened. A letter signed later, or a result filed later, is left out.</span></label></div>
    <span class="hint">Findings count if they occurred inside the window. Diagnoses and genetic results count if established by its end. Medicines count if taken at any time during it.</span></div>`);
  out.push('<h3>Features</h3>');
  for (const f of FEATURES) {
    const fq = q.features[f.key], rv = ref.values[f.key];
    const hiddenView = state.hiddenFeatures.has(f.key);
    out.push(`<div class="feature"><div class="feature-name"><span>${esc(f.label)}</span><span class="ref">${hiddenView ? '<em>hidden from view</em>' : esc(valueText(f, rv) || 'not recorded before the index')}</span></div>
      <div class="meaning">${esc(f.meaning)}</div>
      <div class="feature-controls">${segmented(f.key, fq.mode, !rv)}
        <label class="hide-toggle"><input type="checkbox" data-action="hide-feature" data-key="${f.key}" ${hiddenView ? 'checked' : ''}> Hide from view</label></div>
      ${featureSub(f.key, fq, rv)}${expansionFor(f.key, rv, q)}</div>`);
  }
  out.push(`<h3>Letters and free text</h3><div class="options">${[['off', 'Ignore letters: coded records only'], ['present', 'Use findings letters state about the patient'], ['possible', 'Also use hedged findings ("possible", "?")']].map(([v, t]) =>
    `<label><input type="radio" name="freetext" value="${v}" data-action="freetext" ${q.freeText === v ? 'checked' : ''}><span>${esc(t)}</span></label>`).join('')}</div>
    <p class="hint">Negated findings, plans and family history never count, whatever this is set to.</p>`);
  out.push('<p class="hint">Trajectories are defined on their own page: <a href="../trajectory/">index period, then trajectory</a>.</p>');
  const cases = uniq(draftResult.rows.flatMap(r => r.cases));
  const byCat = {};
  cases.forEach(c => { const k = state.data.quality_kinds[c.kind].category; byCat[k] = (byCat[k] || 0) + 1; });
  out.push(`<div class="quality-box"><strong>Data quality in the evidence being compared</strong><ul>${Object.entries(byCat).sort((a, b) => b[1] - a[1]).map(([k, n]) =>
    `<li><span><span class="tag ${k}">${esc(CATEGORY[k])}</span></span><span>${n} case${n > 1 ? 's' : ''}</span></li>`).join('') || '<li>None known</li>'}</ul>
    <div class="options" style="margin-top:6px"><label><input type="checkbox" data-action="only-cases" ${state.onlyWithCases ? 'checked' : ''}><span>Show only patients whose compared evidence has known cases</span></label></div>
    <p class="hint">Known because the data are synthetic and every defect was injected deliberately. With real data these would be detections, and some would be missed.</p></div>`);
  if (dirty()) {
    const d = membershipDelta(applied.result, draftResult);
    out.push(`<div class="preview"><strong>Applying will change:</strong> ${esc(describeChange(applied.query, q))}.<br>Eligible ${applied.result.rows.length} → ${draftResult.rows.length} (${esc(deltaText(d))}).
      <div class="actions"><button class="btn primary" data-action="apply">Apply as r${state.revisions.length}</button><button class="btn" data-action="discard">Discard draft</button></div></div>`);
  } else {
    out.push(`<div class="preview none">No unapplied changes. Results show revision r${state.current}.</div>`);
  }
  $('#criteria-body').innerHTML = out.join('');
}

function featureSub(key, fq, rv) {
  if (key === 'phenotype' && rv) {
    const list = [...rv.value];
    if (fq.mode === 'must') {
      return `<div class="sub">Require: ${list.map(c => `<label><input type="checkbox" data-action="require" data-key="phenotype" data-c="${esc(c)}" ${!fq.required.length || fq.required.includes(c) ? 'checked' : ''}> ${esc(label(c))}</label>`).join('')}</div>`;
    }
    const excl = fq.excluded.length ? `<div class="sub">Ignored: ${fq.excluded.map(c => `<button class="btn small" data-action="unexclude" data-c="${esc(c)}">${esc(label(c))} ✕</button>`).join('')}</div>` : '';
    return excl;
  }
  if (key === 'medication' && rv && fq.mode === 'must') {
    const opts = [...rv.value, ...([...rv.value].some(c => classesOf(c).includes('sodium_channel_blocker')) ? ['sodium_channel_blocker'] : [])];
    return `<div class="sub">Require: ${opts.map(c => `<label><input type="checkbox" data-action="require" data-key="medication" data-c="${esc(c)}" ${fq.required.includes(c) || (!fq.required.length && c !== 'sodium_channel_blocker') ? 'checked' : ''}> ${esc(label(c))}</label>`).join('')}</div>`;
  }
  if (key === 'onset' && fq.mode === 'must') return `<div class="sub"><label>Within ± <input type="number" min="0" max="60" value="${fq.tolerance}" data-action="tolerance"> months</label></div>`;
  if (key === 'gene' && fq.mode === 'must') return `<div class="sub"><label><input type="checkbox" data-action="pathogenic" ${fq.pathogenicOnly ? 'checked' : ''}> Pathogenic only (exclude variants of uncertain significance)</label></div>`;
  return '';
}

function renderResults() {
  const rev = currentRev(), q = rev.query, res = rev.result;
  const rows = res.rows.filter(r => !state.onlyWithCases || r.cases.length);
  const shown = rows.slice(0, 20);
  const prefer = FEATURES.filter(f => q.features[f.key].mode === 'prefer' && res.ref.values[f.key]);
  const out = [`<div class="results-head"><strong>${shown.length} shown</strong> of ${res.rows.length} eligible · ${state.patients.size - 1} other patients in the dataset.
    Order comes from <em>Prefer similar</em>; eligibility only from <em>Must match</em>.${rev.delta ? `<span class="delta">r${rev.id}: ${esc(deltaText(rev.delta))}</span>` : ''}</div>`,
    `<div class="legend"><span><span class="coverage" style="display:inline-flex"><i></i></span> compared</span><span><span class="coverage" style="display:inline-flex"><i class="missing"></i></span> not recorded: counts as zero, never as a match</span></div>`];
  for (const [i, r] of shown.entries()) {
    const view = evidenceFor(r.pid);
    const dx = r.values.diagnosis ? valueText(FEATURE.diagnosis, r.values.diagnosis) : 'no diagnosis by the end of the window';
    const truth = state.lens === 'truth' ? ` · <strong>truth: ${esc(GROUP_LABEL[view.p.truth.condition_group])}</strong>` : '';
    out.push(`<div class="cand${state.compareId === r.pid ? ' active' : ''}" tabindex="0" role="button" data-action="compare" data-pid="${r.pid}" aria-label="Rank ${i + 1}, ${r.pid}, score ${Math.round(r.score * 100)}">
      <div class="rank">${i + 1}</div>
      <div><div class="who">${r.pid} <small>· ${sexText(view.p.sex)} · window ${esc(spanText(r.win))}</small></div>
        <div class="dx">${state.hiddenFeatures.has('diagnosis') ? '<em>diagnosis hidden from view</em>' : esc(dx)}${state.lens === 'truth' && !state.hiddenFeatures.has('diagnosis') ? truth : ''}</div>
</div>
      <div class="right"><strong>${Math.round(r.score * 100)}</strong><div class="scorebar"><span style="width:${(r.score * 100).toFixed(0)}%"></span></div>
        <div class="coverage" title="${esc(prefer.map(f => `${f.label}: ${r.sims[f.key] == null ? 'not recorded' : Math.round(r.sims[f.key] * 100)}`).join('\n'))}">${prefer.map(f => `<i class="${r.sims[f.key] == null ? 'missing' : ''}"></i>`).join('')}</div>
        <div class="hint">${r.observed}/${r.n} compared${r.cases.length ? `<span class="qcount">⚑ ${r.cases.length}</span>` : ''}</div></div></div>`);
  }
  if (!shown.length) out.push('<p class="hint">No patient satisfies every Must match criterion. The list below says which criterion excluded each one.</p>');
  if (res.excluded.length) {
    out.push(`<details class="excluded"><summary>${res.excluded.length} not eligible, and why</summary><ul>${res.excluded.map(e => `<li><strong>${e.pid}</strong>: ${esc(e.reason)}${state.lens === 'truth' ? ` <em>(truth: ${esc(GROUP_LABEL[state.patients.get(e.pid).truth.condition_group])})</em>` : ''}</li>`).join('')}</ul></details>`);
  }
  $('#results-body').innerHTML = out.join('');
}

function renderRevisions() {
  const out = ['<span class="label">Revisions</span>'];
  state.revisions.forEach((r, i) => {
    if (i) out.push('<span class="rev-arrow">→</span>');
    out.push(`<button class="rev" data-action="goto" data-rev="${r.id}" aria-current="${r.id === state.current}"><strong>r${r.id}</strong> ${esc(r.label.length > 60 ? r.label.slice(0, 58) + '…' : r.label)}<small>${esc(deltaText(r.delta))} · ${r.result.rows.length} eligible</small></button>`);
  });
  const parent = currentRev().parent;
  if (parent != null) out.push(`<button class="secondary" data-action="goto" data-rev="${parent}">Return to parent r${parent}</button>`);
  $('#revisions').innerHTML = out.join('');
}

function renderCompare() {
  const drawer = $('#compare');
  const pid = state.compareId;
  if (!pid) { drawer.hidden = true; return; }
  drawer.hidden = false;
  const rev = currentRev(), q = rev.query, res = rev.result;
  const row = res.rows.find(r => r.pid === pid);
  const refView = evidenceFor(state.referenceId), view = evidenceFor(pid);
  const refWin = res.ref.win, win = row ? row.win : windowFor(view, q);
  const width = $('#compare-body').clientWidth || drawer.clientWidth - 36;
  let tl;
  const anchorOf = w => w.end == null ? null : state.align === 'start' ? w.start : w.end;
  if (state.align !== 'age' && anchorOf(refWin) != null && anchorOf(win) != null) {
    const a = anchorOf(refWin), b = anchorOf(win);
    const lo = -Math.max(a, b), hi = Math.max(refView.snapshot - a, view.snapshot - b);
    tl = [timelineSVG(state.referenceId, {width, minDay: lo, maxDay: hi, offset: a, window: refWin, anchor: `window ${state.align}`, lens: state.lens, title: 'Reference'}),
          timelineSVG(pid, {width, minDay: lo, maxDay: hi, offset: b, window: win, anchor: `window ${state.align}`, lens: state.lens, title: pid})];
  } else {
    const hi = Math.max(refView.snapshot, view.snapshot);
    tl = [timelineSVG(state.referenceId, {width, minDay: 0, maxDay: hi, window: refWin, lens: state.lens, title: 'Reference'}),
          timelineSVG(pid, {width, minDay: 0, maxDay: hi, window: win, lens: state.lens, title: pid})];
  }
  $('#h-compare').textContent = `${state.referenceId} compared with ${pid}`;
  const out = [`<p class="tl-title">${state.referenceId} · reference</p>${tl[0]}<p class="tl-title">${pid} · ${row ? `rank ${res.rows.indexOf(row) + 1}, score ${Math.round(row.score * 100)}` : `not eligible under this revision${win.reason ? `: ${esc(win.reason)}` : ''}`}</p>${tl[1]}`];
  const values = row ? row.values : win.end == null ? {} : extractAll(view, q, win);
  out.push(`<table class="pair"><thead><tr><th>Feature</th><th>Setting</th><th class="col-shared">■ Shared exactly</th><th class="col-ref">Only in ${state.referenceId} (reference)</th><th class="col-cand">Only in ${pid}</th><th>Similarity</th><th>Data quality in ${pid}'s evidence</th></tr></thead><tbody>`);
  for (const f of FEATURES) {
    const rv = res.ref.values[f.key], cv = values[f.key], mode = q.features[f.key].mode;
    const sim = mode === 'prefer' && rv ? (cv ? Math.round(f.sim(rv.value, cv.value) * 100) : 'not recorded') : mode === 'must' && rv ? (cv && f.must(rv.value, cv.value, q.features[f.key]) ? 'meets' : 'fails') : '—';
    const cells = state.hiddenFeatures.has(f.key) ? ['<em>hidden from view</em>', '', '']
      : (({shared, ref, cand}) => [pairChips(shared), pairChips(ref), pairChips(cand)])(partition(f, rv, cv, q, state.referenceId, pid));
    out.push(`<tr><td>${esc(f.label)}</td><td>${esc(MODE_LABEL[mode])}</td><td class="col-shared">${cells[0]}</td><td class="col-ref">${cells[1]}</td><td class="col-cand">${cells[2]}</td><td>${esc(sim)}</td><td class="col-quality">${qualityByEvent(cv, f)}</td></tr>`);
  }
  out.push(`</tbody></table><p class="hint">The last column lists, finding by finding, what is uncertain or wrong in the records behind ${pid}'s values: approximate times, late recording, broad or local codes, entry errors. None of it removes the finding; it says how far to trust its timing and coding. Exact means the same concept, medicine or value (age to the month). ≈ marks something related but different: broader, narrower or the same drug class. Each patient's values are those in their own window. Hover a value or an event to see it on both timelines.</p>`);
  out.push(`<div class="actions"><button class="btn" data-action="use-reference" data-pid="${pid}">Use ${pid} as the reference (new branch)</button></div>`);
  $('#compare-body').innerHTML = out.join('');
}

/* Interaction */

function itemFor(id) { return evidenceFor(state.referenceId).byId.get(id); }

function setMode(key, mode) { state.draft.features[key].mode = mode; }

function handle(action, el) {
  const q = state.draft;
  const it = el.dataset.id ? itemFor(el.dataset.id) : null;
  switch (action) {
    case 'lens': state.lens = el.dataset.lens; break;
    case 'select': state.selected = el.dataset.id; break;
    case 'mode': setMode(el.dataset.key, el.dataset.mode); break;
    case 'bound-kind': {
      const which = el.dataset.which, other = which === 'start' ? 'end' : 'start';
      q.window[which] = freshBound(el.value, which);
      if (el.value === 'span' && q.window[other].kind === 'span') q.window[other] = which === 'start' ? freshBound('age', 'end') : freshBound('birth', 'start');
      break;
    }
    case 'bound-field': {
      const b = q.window[el.dataset.which], f = el.dataset.field;
      b[f] = ['date', 'event', 'unit', 'which'].includes(f) ? el.value : f === 'n' ? Math.max(1, Math.round(+el.value || 1)) : (+el.value || 0);
      break;
    }
    case 'known': q.knownByIndex = el.checked; break;
    case 'freetext': q.freeText = el.value; break;
    case 'tolerance': q.features.onset.tolerance = Math.max(0, +el.value || 0); break;
    case 'pathogenic': q.features.gene.pathogenicOnly = el.checked; break;
    case 'hide-feature': el.checked ? state.hiddenFeatures.add(el.dataset.key) : state.hiddenFeatures.delete(el.dataset.key); break;
    case 'only-cases': state.onlyWithCases = el.checked; break;
    case 'require': {
      const f = q.features[el.dataset.key];
      const rv = evaluate(q).ref.values[el.dataset.key];
      if (!f.required.length) f.required = [...rv.value];
      f.required = el.checked ? uniq([...f.required, el.dataset.c]) : f.required.filter(c => c !== el.dataset.c);
      break;
    }
    case 'unexclude': q.features.phenotype.excluded = q.features.phenotype.excluded.filter(c => c !== el.dataset.c); break;
    case 'ev-must':
      if (it.kind === 'phenotype') { setMode('phenotype', 'must'); q.features.phenotype.required = uniq([...q.features.phenotype.required, it.concept]); }
      if (it.kind === 'diagnosis') setMode('diagnosis', 'must');
      if (it.kind === 'genetic') setMode('gene', 'must');
      if (it.kind === 'medication') { setMode('medication', 'must'); q.features.medication.required = uniq([...q.features.medication.required, it.concept]); }
      break;
    case 'ev-must-class': setMode('medication', 'must'); q.features.medication.required = uniq([...q.features.medication.required.filter(c => c !== it.concept), 'sodium_channel_blocker']); break;
    case 'ev-prefer': {
      const key = {phenotype: 'phenotype', diagnosis: 'diagnosis', genetic: 'gene', medication: 'medication'}[it.kind];
      setMode(key, 'prefer');
      if (key === 'phenotype') q.features.phenotype.excluded = q.features.phenotype.excluded.filter(c => c !== it.concept);
      break;
    }
    case 'ev-ignore':
      if (it.kind === 'phenotype') q.features.phenotype.excluded = uniq([...q.features.phenotype.excluded, it.concept]);
      else setMode({diagnosis: 'diagnosis', genetic: 'gene', medication: 'medication'}[it.kind], 'ignore');
      break;
    case 'ev-hide': state.hiddenConcepts.has(it.concept) ? state.hiddenConcepts.delete(it.concept) : state.hiddenConcepts.add(it.concept); break;
    case 'apply': return applyDraft();
    case 'discard': state.draft = clone(currentRev().query); break;
    case 'goto': state.current = +el.dataset.rev; state.draft = clone(currentRev().query); break;
    case 'compare': state.compareId = el.dataset.pid; break;
    case 'close-compare': state.compareId = null; $('#compare').hidden = true; break;
    case 'align': state.align = el.value; break;
    case 'use-reference': return setReference(el.dataset.pid, `new reference ${el.dataset.pid}`);
    default: return;
  }
  render();
}

function setReference(pid, why) {
  state.referenceId = pid;
  state.selected = null;
  state.compareId = null;
  $('#compare').hidden = true;
  $('#reference').value = pid;
  resetRevisions(why);
  render();
}

function wire() {
  document.addEventListener('click', e => {
    const ev = e.target.closest('.ev[data-id]');
    if (ev && ev.dataset.pid === state.referenceId && ev.closest('#timeline')) { state.selected = ev.dataset.id; render(); return; }
    const el = e.target.closest('[data-action]');
    if (el && !['SELECT', 'INPUT'].includes(el.tagName)) handle(el.dataset.action, el);
  });
  document.addEventListener('change', e => {
    const el = e.target.closest('[data-action]');
    if (el && ['SELECT', 'INPUT'].includes(el.tagName)) handle(el.dataset.action, el);
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && state.compareId) { handle('close-compare', e.target); return; }
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const target = e.target.closest('.ev[data-id], .cand');
    if (!target) return;
    e.preventDefault();
    target.click();
  });
  const tip = $('#tooltip');
  document.addEventListener('mouseover', e => {
    clearLinks();
    const ev = e.target.closest('#compare .ev[data-id]');
    const chip = e.target.closest('#compare .pair-chip[data-link]');
    if (ev && state.compareId && !ev.dataset.id.startsWith('__') && !ev.dataset.id.startsWith('n:')) {
      const info = compareTip(ev.dataset.pid, ev.dataset.id);
      if (info) {
        tip.innerHTML = info.html;
        tip.hidden = false;
        ev.classList.add('linked-exact');
        linkEvents(info.links, info.exact);
        return;
      }
    }
    if (chip) {
      chip.classList.add('hot');
      linkEvents(chip.dataset.link.split(' ').filter(Boolean), true);
    }
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
  wireCombos((action, el) => handle(action, el));
  $('#reference').addEventListener('change', e => setReference(e.target.value, `reference ${e.target.value}`));
  let timer;
  addEventListener('resize', () => { clearTimeout(timer); timer = setTimeout(render, 150); });
}

async function main() {
  try {
    await loadData();
  } catch (error) {
    $('#workspace').innerHTML = `<p class="loading">Could not load the synthetic dataset (${esc(error.message)}). Serve the <code>mockups/</code> directory over HTTP; opening the file directly will not work.</p>`;
    return;
  }
  state.referenceId = state.data.reference_patient_id;
  resetRevisions('initial similarity');
  wire();
  render();
}

main();
