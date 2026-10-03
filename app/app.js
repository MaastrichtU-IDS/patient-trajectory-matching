'use strict';
const $ = id => document.getElementById(id);
let current = null, busy = false, lastEvidence = null;
// The four dispositions UI-002 asks for. The first three decide what a feature does to the
// result and are mutually exclusive; hiding is orthogonal to all of them, because a hidden
// feature must go on doing exactly what it was doing. Keeping it out of the query is what
// makes that true by construction rather than by care.
const LABELS = {age_band:'Age band', baseline_creatinine:'Baseline creatinine', clinical_concepts:'Clinical concepts'};
const MODES = {must:'Must match', prefer:'Prefer similar', ignore:'Ignore'};
const EFFECT = {must:'eligibility', prefer:'ranking only', ignore:'neither'};
let components = [], baseWeights = {};
const modes = {}, hidden = {};
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'The request could not be completed.');
  return value;
}
function controls() {
  $('start').disabled = busy;
  for (const id of ['apply','compare']) $(id).disabled = busy || !current;
  $('undo').disabled = busy || !current?.parent_revision_id;
}
async function action(work) {
  if (busy) return;
  busy = true; controls(); $('status').className = ''; $('status').textContent = 'Evaluating the authored records…';
  try { await work(); $('status').textContent = 'Evaluation complete. Results refer to the revision shown below.'; }
  catch (error) { $('status').className = 'error'; $('status').textContent = error.message; }
  finally { busy = false; controls(); }
}
function clearComparison() {
  $('cohorts').innerHTML = '';
  $('trajectory-summary').textContent = 'Run the trajectory on this revision’s entire eligible population, including patients outside the displayed ranking.';
  $('export').className = 'button hidden'; $('export').removeAttribute('href');
  $('evidence').textContent = 'No patient selected.'; lastEvidence = null;
}
function featureEffect(component) {
  return `Changes ${EFFECT[modes[component]]}${hidden[component] ? '; hidden from view' : ''}`;
}
function renderComponents() {
  $('components').innerHTML = components.map(component => {
    const name = LABELS[component] || component;
    const options = Object.keys(MODES).map(mode =>
      `<option value="${mode}"${modes[component] === mode ? ' selected' : ''}>${esc(MODES[mode])}</option>`).join('');
    return `<div class="feature-row"><span class="feature-name">${esc(name)}</span>`
      + `<select id="mode-${esc(component)}" data-component="${esc(component)}" data-kind="mode" aria-label="${esc(name)} role">${options}</select>`
      + `<label class="feature-hide"><input type="checkbox" id="hide-${esc(component)}" data-component="${esc(component)}" data-kind="hide"${hidden[component] ? ' checked' : ''}> Hide from view</label>`
      + `<span class="feature-effect" id="effect-${esc(component)}"></span></div>`;
  }).join('');
  refreshEffects();
}
function refreshEffects() {
  for (const component of components) {
    const effect = $('effect-' + component);
    if (effect) effect.textContent = featureEffect(component);
  }
}
// Hiding never reaches the server: it re-renders what is already in hand. A request here
// would create a revision, and a revision is exactly what must not change.
function renderHidden() {
  refreshEffects();
  if (current) renderProfile(current);
  if (lastEvidence) renderEvidence(lastEvidence.patient, lastEvidence.data);
}
function renderProfile(revision) {
  const shown = components.filter(component => !hidden[component]);
  const weights = shown.map(component => `${LABELS[component] || component} ${revision.query.weights[component]}`).join(', ');
  const withheld = components.length - shown.length;
  $('profile').textContent = 'Authored defaults: 14-day pre-index history; minimum creatinine in the preceding 48 hours;'
    + ` occurrence and authored availability strictly before index. Current weights: ${weights || 'none shown'}.`
    + (withheld ? ` ${withheld} feature${withheld > 1 ? 's are' : ' is'} hidden from this view; the analysis used all ${components.length}.` : '')
    + ' This availability overlay is constructed, not a historical clinical replay.';
}
function requirement(component) {
  if (component === 'age_band') return {component, operator:'eq', value:$('band').value};
  if (component === 'baseline_creatinine') return {component, operator:'between', value:{min:'0', max:$('maximum').value}};
  if (component === 'clinical_concepts') return {component, operator:'contains', value:$('concept').value};
  return null;
}
function plan() {
  const weights = {};
  let preferred = 0;
  for (const component of components) {
    const ranked = modes[component] === 'prefer';
    if (ranked) preferred += 1;
    const declared = baseWeights[component];
    weights[component] = ranked ? (declared && declared !== '0' ? declared : '1') : '0';
  }
  if (!preferred) return null;
  // Weights first: a feature set to Must match is given weight zero, so eligibility and
  // ranking stay separable and the effects reported below are not one effect counted twice.
  const operations = [{type:'set_weights', weights}];
  for (const component of components) {
    if (modes[component] !== 'must') continue;
    const predicate = requirement(component);
    if (predicate) operations.push({type:'add_filter', predicate});
  }
  return operations;
}
function describeEffects(before, revision) {
  const after = revision.results.eligible_patient_ids;
  const previous = new Set(before.eligible), eligible = new Set(after);
  const added = after.filter(id => !previous.has(id)).length;
  const removed = before.eligible.filter(id => !eligible.has(id)).length;
  const order = revision.results.displayed.map(row => row.patient_id);
  const was = before.order.filter(id => order.includes(id));
  const now = order.filter(id => before.order.includes(id));
  const moved = was.filter((id, position) => now[position] !== id).length;
  return `Eligibility: ${added} added, ${removed} removed. Ranking: `
    + (moved ? `${moved} of ${was.length} still-displayed patients changed position` : 'order unchanged')
    + '. Hiding a feature from view changes neither of these.';
}
function renderRevision(revision) {
  current = revision; clearComparison();
  $('revision').textContent = revision.parent_revision_id ? 'Refined revision' : 'Initial revision';
  const r = revision.results;
  $('scope').textContent = `Reference ${revision.reference_patient_id} · ${r.eligible_patient_ids.length} eligible patients · showing ${r.displayed.length}. Ranking uses only pre-index history; the trajectory uses the entire eligible pool.`;
  renderProfile(revision);
  const changes = revision.changes || {};
  $('changes').textContent = revision.parent_revision_id ? `Eligibility: added ${(changes.added || []).join(', ') || 'none'}; removed ${(changes.removed || []).join(', ') || 'none'}. Ranking may change without changing eligibility.` : 'Defaults are explicit and authored for this demonstration; they are not clinically validated.';
  $('rankings').innerHTML = r.displayed.map(row => `<tr><td>${esc(row.patient_id)}</td><td>${esc(row.ranking_distance)}</td><td>${esc(row.coverage)}</td><td><button class="inspect" data-patient="${esc(row.patient_id)}">Inspect</button></td></tr>`).join('') || '<tr><td colspan="4">No rankable candidates under this revision.</td></tr>';
  $('missing').textContent = `Unresolved eligibility: ${(r.unresolved || []).map(x=>x.patient_id).join(', ') || 'none'}. Insufficient ranking evidence: ${(r.unrankable || []).map(x=>x.patient_id).join(', ') || 'none'}. Missing observations are not treated as a perfect match.`;
  controls();
}
function cohortGroup(title, ids, results) {
  const byId = new Map(results.map(row => [row.patient_id, row]));
  return `<div class="cohort-group"><h3>${esc(title)} · ${ids.length}</h3>${ids.map(id => `<button class="inspect" data-patient="${esc(id)}">${esc(id)} · inspect</button><p class="hint">${esc((byId.get(id)?.reason_codes || []).join(', '))}</p>`).join('') || '<p class="hint">No patients in this group.</p>'}</div>`;
}
function renderComparison(value) {
  $('trajectory-summary').textContent = `${value.eligible_patient_ids.length} eligible patients evaluated. ${value.exact.membership.included.length} exact matches; ${value.added.length} additional permitted near matches. Unresolved trajectory evidence: ${value.relaxed.membership.unresolved.join(', ') || 'none'}. This is an authored exposure-associated pattern, not a causal finding.`;
  $('cohorts').innerHTML = '<div class="cohort-grid">' + cohortGroup('Exact', value.exact.membership.included, value.exact.results) + cohortGroup('Added by declared relaxation', value.added, value.relaxed.results) + '</div>';
  $('export').href = '/api/export/' + encodeURIComponent(value.comparison_id);
  $('export').className = 'button';
}
// A filtered copy, never a mutation: lastEvidence keeps the whole payload, so unhiding
// restores the withheld rows without another request. A hidden feature is withheld
// wherever the evidence names it -- the weighted comparison row and both sides' feature
// maps -- because a reader who hid it should not meet it again two panels down.
function withhold(similarity) {
  if (!similarity || !components.some(component => hidden[component])) return similarity;
  const copy = {...similarity};
  if (similarity.comparison && Array.isArray(similarity.comparison.components)) {
    copy.comparison = {...similarity.comparison,
      components: similarity.comparison.components.filter(row => !hidden[row.component])};
  }
  for (const side of ['candidate', 'reference']) {
    if (!similarity[side] || !similarity[side].features) continue;
    const features = {};
    for (const key of Object.keys(similarity[side].features)) {
      if (!hidden[key]) features[key] = similarity[side].features[key];
    }
    copy[side] = {...similarity[side], features};
  }
  return copy;
}
async function inspect(patient) {
  const data = await api('/api/evidence?revision_id=' + encodeURIComponent(current.revision_id) + '&patient_id=' + encodeURIComponent(patient));
  renderEvidence(patient, data);
}
function renderEvidence(patient, data) {
  lastEvidence = {patient, data};
  const withheld = components.filter(component => hidden[component]);
  const similarity = withhold(data.similarity);
  const notice = withheld.length ? `<p class="hint">Hidden from this view: ${esc(withheld.map(c => LABELS[c] || c).join(', '))}. The scores below were computed with every feature.</p>` : '';
  $('evidence').innerHTML = `<h3>${esc(patient)}</h3><p class="hint">${esc(data.notice)}</p>${notice}<h4>Original authored records</h4><div class="table-scroll"><table><thead><tr><th>Record</th><th>Concept</th><th>Recorded time</th><th>Original value</th></tr></thead><tbody>${data.source_rows.map(row=>`<tr><td>${esc(row.record_id)}</td><td>${esc(row.source_code)}</td><td>${esc(row.datetime)}</td><td>${esc(row.value)} ${esc(row.unit)}</td></tr>`).join('')}</tbody></table></div><details open><summary>Similarity components and eligibility</summary><pre>${esc(JSON.stringify(similarity,null,2))}</pre></details><details><summary>PRO / SOLID source bindings</summary><pre>${esc(JSON.stringify(data.pro_solid,null,2))}</pre></details>`;
}
$('start').onclick = () => action(async()=>renderRevision(await api('/api/initial',{patient_id:$('patient').value,top_k:5})));
$('components').onchange = event => {
  const component = event.target?.dataset?.component;
  if (!component || !components.includes(component)) return;
  if (event.target.dataset.kind === 'hide') hidden[component] = !!event.target.checked;
  else modes[component] = event.target.value;
  renderHidden();
};
$('apply').onclick = () => action(async()=>{
  const operations = plan();
  if (!operations) throw new Error('At least one feature must be set to Prefer similar; a ranking needs something to rank on.');
  const before = {order: current.results.displayed.map(row => row.patient_id),
                  eligible: current.results.eligible_patient_ids.slice()};
  let revision = current;
  for (const operation of operations) revision = await api('/api/refine',{revision_id:revision.revision_id, operation});
  renderRevision(revision);
  $('effects').textContent = describeEffects(before, revision);
});
$('undo').onclick = () => action(async()=>renderRevision(await api('/api/revisions/'+encodeURIComponent(current.parent_revision_id))));
$('compare').onclick = () => action(async()=>{ clearComparison(); renderComparison(await api('/api/trajectory',{revision_id:current.revision_id,budget:$('budget').value})); });
for (const id of ['rankings','cohorts']) $(id).onclick = event => {const button=event.target.closest('[data-patient]'); if(button && current) action(()=>inspect(button.dataset.patient));};
async function initialize() {
  busy = true; controls();
  try {
    const capabilities = await api('/api/capabilities');
    const meta = capabilities.metadata;
    $('patient').innerHTML = meta.patients.map(p=>`<option value="${esc(p.patient_id)}">${esc(p.label || p.patient_id)}</option>`).join('');
    $('patient').value = meta.default_reference_patient_id;
    components = meta.components || [];
    baseWeights = meta.default_weights || {};
    for (const component of components) {
      const declared = baseWeights[component];
      modes[component] = declared && declared !== '0' ? 'prefer' : 'ignore';
      hidden[component] = false;
    }
    renderComponents();
    $('status').textContent = 'Ready. Choose a reference patient to start your analysis.';
  } catch(error) { $('status').className='error'; $('status').textContent=error.message; }
  finally { busy=false; controls(); }
}
initialize();
