'use strict';
const $ = id => document.getElementById(id);
let current = null, busy = false;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'The request could not be completed.');
  return value;
}
function controls() {
  $('start').disabled = busy;
  for (const id of ['weight','filter','compare']) $(id).disabled = busy || !current;
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
  $('evidence').textContent = 'No patient selected.';
}
function renderRevision(revision) {
  current = revision; clearComparison();
  $('revision').textContent = revision.parent_revision_id ? 'Refined revision' : 'Initial revision';
  const r = revision.results;
  $('scope').textContent = `Reference ${revision.reference_patient_id} · ${r.eligible_patient_ids.length} eligible patients · showing ${r.displayed.length}. Ranking uses only pre-index history; the trajectory uses the entire eligible pool.`;
  $('profile').textContent = `Authored defaults: 14-day pre-index history; minimum creatinine in the preceding 48 hours; occurrence and authored availability strictly before index. Current weights: age ${revision.query.weights.age_band}, creatinine ${revision.query.weights.baseline_creatinine}, concepts ${revision.query.weights.clinical_concepts}. This availability overlay is constructed, not a historical clinical replay.`;
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
async function inspect(patient) {
  const data = await api('/api/evidence?revision_id=' + encodeURIComponent(current.revision_id) + '&patient_id=' + encodeURIComponent(patient));
  $('evidence').innerHTML = `<h3>${esc(patient)}</h3><p class="hint">${esc(data.notice)}</p><h4>Original authored records</h4><div class="table-scroll"><table><thead><tr><th>Record</th><th>Concept</th><th>Recorded time</th><th>Original value</th></tr></thead><tbody>${data.source_rows.map(row=>`<tr><td>${esc(row.record_id)}</td><td>${esc(row.source_code)}</td><td>${esc(row.datetime)}</td><td>${esc(row.value)} ${esc(row.unit)}</td></tr>`).join('')}</tbody></table></div><details open><summary>Similarity components and eligibility</summary><pre>${esc(JSON.stringify(data.similarity,null,2))}</pre></details><details><summary>PRO / SOLID source bindings</summary><pre>${esc(JSON.stringify(data.pro_solid,null,2))}</pre></details>`;
}
$('start').onclick = () => action(async()=>renderRevision(await api('/api/initial',{patient_id:$('patient').value,top_k:5})));
$('weight').onclick = () => action(async()=>renderRevision(await api('/api/refine',{revision_id:current.revision_id,operation:{type:'set_weights',weights:{age_band:'1',baseline_creatinine:'3',clinical_concepts:'1'}}})));
$('filter').onclick = () => action(async()=>renderRevision(await api('/api/refine',{revision_id:current.revision_id,operation:{type:'add_filter',predicate:{component:'baseline_creatinine',operator:'between',value:{min:'0',max:$('maximum').value}}}})));
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
    $('status').textContent = 'Ready. Choose a reference patient to start your analysis.';
  } catch(error) { $('status').className='error'; $('status').textContent=error.message; }
  finally { busy=false; controls(); }
}
initialize();
