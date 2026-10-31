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
let components = [], baseWeights = {}, admitted = {}, sourceModes = {};
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
  // Cleared with the result it describes. A profile line left standing beside no result names
  // a model that priced nothing on screen.
  $('priced-by').textContent = '';
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
  renderRequirement();
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
  $('profile').textContent = `${days(admitted.history_us)} pre-index history; minimum creatinine in the preceding `
    + `${days(admitted.creatinine_window_us)}; occurrence and availability strictly before index.`
    + ` Index: ${(admitted.index_rule || 'declared per patient').replace(/\.\s*$/, '')}. Current weights: ${weights || 'none shown'}.`
    + (withheld ? ` ${withheld} feature${withheld > 1 ? 's are' : ' is'} hidden from this view; the analysis used all ${components.length}.` : '')
    + ` ${admitted.availability_policy || ''}`;
}
const REQUIREMENT_INPUT = {age_band:'band', baseline_creatinine:'maximum', clinical_concepts:'concept'};
function requirementValue(component) {
  const input = REQUIREMENT_INPUT[component];
  return input ? String($(input).value ?? '').trim() : '';
}
function requirement(component) {
  const value = requirementValue(component);
  if (component === 'age_band') return {component, operator:'eq', value};
  if (component === 'baseline_creatinine') return {component, operator:'between', value:{min:'0', max:value}};
  if (component === 'clinical_concepts') return {component, operator:'contains', value};
  return null;
}
// The inputs below the dispositions are read only for a feature set to Must match, and the
// two ways of getting that wrong fail in opposite directions: a value with no Must match is
// dropped in silence, and a Must match with no value is refused by the engine in its own
// vocabulary. Stating what Apply will send makes the first visible; plan() refuses the
// second here, where the feature still has a name the operator recognises.
function renderRequirement() {
  const required = components.filter(component => modes[component] === 'must' && REQUIREMENT_INPUT[component]);
  const unused = components.filter(component => modes[component] !== 'must' && requirementValue(component));
  const names = list => list.map(component => LABELS[component] || component).join(' and ');
  const idle = unused.length
    ? ` Values entered for ${names(unused)} are not used, because ${unused.length > 1 ? 'those features are' : 'that feature is'} not set to Must match.`
    : '';
  $('requirement').textContent = (required.length
    ? `Apply will require: ${required.map(component =>
        `${LABELS[component] || component} ${requirementValue(component) ? '= ' + requirementValue(component) : '(no value entered)'}`).join('; ')}.`
    : 'No feature is set to Must match, so no value below is used; Apply will change ranking weights only.') + idle;
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
    if (!predicate) continue;
    if (!requirementValue(component)) {
      throw new Error(`${LABELS[component] || component} is set to Must match but has no value to match on. `
        + 'Enter the value to require, or set that feature to Prefer similar or Ignore.');
    }
    operations.push({type:'add_filter', predicate});
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
  $('changes').textContent = revision.parent_revision_id ? `Eligibility: added ${(changes.added || []).join(', ') || 'none'}; removed ${(changes.removed || []).join(', ') || 'none'}. Ranking may change without changing eligibility.` : `Defaults are explicit and declared for this ${sourceModes.cohort === 'public-demo' ? 'pinned public demo' : 'demonstration'}; they are not clinically validated.`;
  $('rankings').innerHTML = r.displayed.map(row => `<tr><td>${esc(row.patient_id)}</td><td>${esc(row.ranking_distance)}</td><td>${esc(row.coverage)}</td><td><button class="inspect" data-patient="${esc(row.patient_id)}">Inspect</button></td></tr>`).join('') || '<tr><td colspan="4">No rankable candidates under this revision.</td></tr>';
  $('missing').textContent = `Unresolved eligibility: ${(r.unresolved || []).map(x=>x.patient_id).join(', ') || 'none'}. Insufficient ranking evidence: ${(r.unrankable || []).map(x=>x.patient_id).join(', ') || 'none'}. Missing observations are not treated as a perfect match.`;
  controls();
}
function cohortGroup(title, ids, results) {
  const byId = new Map(results.map(row => [row.patient_id, row]));
  return `<div class="cohort-group"><h3>${esc(title)} · ${ids.length}</h3>${ids.map(id => `<button class="inspect" data-patient="${esc(id)}">${esc(id)} · inspect</button><p class="hint">${esc((byId.get(id)?.reason_codes || []).join(', '))}</p>`).join('') || '<p class="hint">No patients in this group.</p>'}</div>`;
}
// Decision D (docs/decisions/relaxation-cost-models.md) refused a combined view of the two
// cost models and required each result to name the model that priced it. The identifier is
// read out of the payload and never written here as a constant: a label kept by hand drifts
// from the model that actually answered, which is the defect the field exists to prevent.
function pricedBy(value) {
  const named = [value.exact, value.relaxed].map(part => part && part.relaxation_profile);
  const profiles = [...new Set(named)];
  if (profiles.length !== 1 || !profiles[0]) {
    return 'This result does not name the cost model that priced it; do not compare it with a result from another workflow.';
  }
  return `Priced by ${profiles[0]}. This budget bounds the sum of component costs across relaxed targets. A near match here is admitted on its worst-case timing, which is not the claim a certain match makes on the temporal uncertainty workflow.`;
}
function renderComparison(value) {
  $('trajectory-summary').textContent = `${value.eligible_patient_ids.length} eligible patients evaluated. ${value.exact.membership.included.length} exact matches; ${value.added.length} additional permitted near matches. Unresolved trajectory evidence: ${value.relaxed.membership.unresolved.join(', ') || 'none'}. This is an authored exposure-associated pattern, not a causal finding.`;
  $('priced-by').textContent = pricedBy(value);
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
  // The two cohorts carry different source bindings, so the table shows what each one
  // actually has rather than printing blank cells under authored column headings. The
  // derived cohort has no wall clock of its own: its records are offsets from the index,
  // which is what the engine ranks on, so that is the column it gets.
  const demo = sourceModes.cohort === 'public-demo';
  const heading = demo ? 'Original recorded rows' : 'Original authored records';
  const columns = demo ? ['Record', 'Component', 'Pinned source', 'Before index', 'Original value']
                       : ['Record', 'Concept', 'Recorded time', 'Original value'];
  const cells = row => demo
    ? [row.record_id, row.component, `${(row.document || '').split(':')[0]} row ${row.row}`,
       offset(row.occurrence_us), `${row.original_value || ''} ${row.original_unit || ''}`.trim() || '--']
    : [row.record_id, row.source_code, row.datetime, `${row.value} ${row.unit}`];
  const bindings = data.pro_solid
    ? `<details><summary>PRO / SOLID source bindings</summary><pre>${esc(JSON.stringify(data.pro_solid,null,2))}</pre></details>`
    : '';
  $('evidence').innerHTML = `<h3>${esc(patient)}</h3><p class="hint">${esc(data.notice)}</p>${notice}`
    + `<h4>${esc(heading)}</h4><div class="table-scroll"><table><thead><tr>${columns.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead>`
    + `<tbody>${data.source_rows.map(row=>`<tr>${cells(row).map(cell=>`<td>${esc(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`
    + `<details open><summary>Similarity components and eligibility</summary><pre>${esc(JSON.stringify(similarity,null,2))}</pre></details>`
    + bindings;
}
$('start').onclick = () => action(async()=>renderRevision(await api('/api/initial',{patient_id:$('patient').value,top_k:5})));
$('components').onchange = event => {
  const component = event.target?.dataset?.component;
  if (!component || !components.includes(component)) return;
  if (event.target.dataset.kind === 'hide') hidden[component] = !!event.target.checked;
  else modes[component] = event.target.value;
  renderHidden();
};
for (const input of Object.values(REQUIREMENT_INPUT)) $(input).oninput = () => renderRequirement();
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
function offset(microseconds) {
  if (microseconds === undefined || microseconds === null) return '--';
  const hours = -microseconds / 3600000000;
  return `${hours.toFixed(1)} h`;
}
function days(microseconds) {
  if (!microseconds) return 'the declared window';
  const hours = microseconds / 3600000000;
  if (hours % 24 || hours < 24) return `${hours} hour${hours === 1 ? '' : 's'}`;
  const count = hours / 24;
  return `${count} day${count === 1 ? '' : 's'}`;
}
// Every claim the page makes about its own data comes from the server. The markup used to
// assert an authored cohort in three places, which silently became false the moment a
// public-demo cohort could be admitted.
function describeCohort(capabilities, meta) {
  const demo = sourceModes.cohort === 'public-demo';
  const badge = document.querySelector('header .badge');
  if (badge) badge.textContent = demo ? 'Pinned public demo records' : 'Authored synthetic records';
  const hint = document.querySelector('#evidence');
  const evidenceHint = hint && hint.parentElement && hint.parentElement.querySelector('.hint');
  if (evidenceHint) evidenceHint.textContent = demo
    ? 'Open a patient to review feature eligibility and the original recorded rows. Graph bindings preserve the patient role and measured value.'
    : 'Open a patient to review feature eligibility and the original authored rows. Graph bindings preserve the patient role and measured value.';
  // The form's placeholder values are authored examples. In a demo cohort they match
  // nothing, so a required value typed from them silently empties the population.
  const concept = $('concept');
  if (concept && (meta.concept_examples || []).length) {
    concept.value = meta.concept_examples[0];
    concept.placeholder = meta.concept_examples[0];
  }
  const ageObserved = (meta.default_weights || {}).age_band !== '0';
  const band = $('band');
  if (band && !ageObserved) {
    band.value = '';
    band.disabled = true;
    band.placeholder = 'not derivable from the pinned files';
  }
  const intro = document.querySelector('.intro p:last-of-type');
  if (intro && demo) intro.textContent = `Explore pre-index similarity and refine the eligible population over ${meta.patients.length} pinned public-demo ICU stays. `
    + 'Source fidelity only: no clinical mapping approval, no clinical validity claim.';
  // The fixed kidney trajectory runs over the authored projections only. Say so where the
  // step was, and hide the controls rather than leave a button the server will refuse.
  // The nodes stay in the document: `controls()` addresses them by id on every render.
  if (!(capabilities.supported || []).includes('exact-and-declared-relaxed-trajectories')) {
    for (const card of document.querySelectorAll('.card')) {
      if (!/DEFINE THE TRAJECTORY|TRAJECTORY COMPARISON/.test(card.textContent || '')) continue;
      for (const node of card.querySelectorAll('p, label, select, button, div')) node.style.display = 'none';
      for (const node of card.querySelectorAll('button, select, input')) node.disabled = true;
      const note = document.createElement('p');
      note.className = 'hint';
      note.textContent = 'Not available for this cohort. The fixed kidney trajectory runs over the '
        + 'authored PRO/SOLID projections, which the derived public-demo cohort does not carry.';
      card.append(note);
    }
  }
}
async function initialize() {
  busy = true; controls();
  try {
    const capabilities = await api('/api/capabilities');
    const meta = capabilities.metadata;
    $('patient').innerHTML = meta.patients.map(p=>`<option value="${esc(p.patient_id)}">${esc(p.label || p.patient_id)}</option>`).join('');
    $('patient').value = meta.default_reference_patient_id;
    components = meta.components || [];
    baseWeights = meta.default_weights || {};
    admitted = meta;
    sourceModes = capabilities.source_modes || {};
    describeCohort(capabilities, meta);
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
