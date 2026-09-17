'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels = {CERTAIN_MATCH:'Certain', POSSIBLE_MATCH:'Possible only', NO_RECORDED_MATCH:'No recorded match', INCOMPARABLE:'Incomparable'};
let report = null, busy = false, ready = false;
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'Temporal evaluation failed.');
  return value;
}
function controls() {
  $('run').disabled = busy || !ready;
  $('temporal-budget').disabled = busy || !ready;
}
function clear() {
  report = null;
  $('temporal-rows').innerHTML = '';
  $('summary').textContent = 'Run the selected budget to evaluate all four histories.';
  $('temporal-evidence').textContent = 'Choose Inspect after running the question.';
  $('temporal-export').className = 'button hidden';
  $('temporal-export').removeAttribute('href');
}
function render(value) {
  if (value.result.status !== 'COMPLETED' || value.result.search_complete !== true) throw new Error('Incomplete evaluation; no cohort or export is available.');
  report = value;
  const original = value.result.evaluations[0].result;
  $('summary').textContent = `Budget ${value.budget}. Original question: ${original.certain_patient_ids.length} certain; ${original.possible_patient_ids.length - original.certain_patient_ids.length} possible only. Certain under a permitted option: ${value.result.robust_patient_ids.join(', ') || 'none'}. Added by widening: ${value.added_robust_patient_ids.join(', ') || 'none'}. Original classifications remain visible.`;
  $('temporal-rows').innerHTML = value.patients.map(row => `<tr><td>${esc(row.patient_id)}</td><td>${esc(labels[row.original_status] || row.original_status)}</td><td>${row.selected_option ? `${esc(row.selected_option === 'original' ? 'Original · 48 minutes' : 'Widened · 50 minutes')} · cost ${esc(row.selected_cost)}` : 'No certain option'}</td><td><button class="inspect" data-patient="${esc(row.patient_id)}">Inspect</button></td></tr>`).join('');
  $('temporal-export').href = '/api/temporal/export/' + encodeURIComponent(value.report_id);
  $('temporal-export').className = 'button';
}
function inspect(patient) {
  if (!report) return;
  const source = report.inputs.source;
  const events = source.events.filter(e => e.patient_id === patient);
  if (!events.length) return;
  const variables = source.variables.filter(v => v.patient_id === patient);
  const evaluations = report.result.evaluations.map(e => ({option:e.option, trajectories:e.result.trajectories.filter(t => t.patient_id === patient)}));
  const bindings = report.result.evaluations[0].result.evidence.bindings;
  const proofs = events.map(e => bindings[e.id]);
  const pair = evaluations[0].trajectories[0]?.bindings[0];
  const infusion = events.find(e => e.id === pair?.slots.infusion.event_id);
  const collection = events.find(e => e.id === pair?.slots.collection.event_id);
  const timelines = infusion && collection ? ['possible_witness','counterexample'].filter(key => pair[key]).map(key => {
    const timeline = pair[key];
    const end = timeline[infusion.end_var] / 60000000;
    const start = timeline[collection.start_var] / 60000000;
    const title = key === 'possible_witness' ? 'Satisfying timeline' : 'Counterexample to certainty';
    return `<div class="cohort-group"><h3>${esc(title)}</h3><p class="hint">Infusion completes at minute ${esc(end)}. Collection starts at minute ${esc(start)}. Gap: <strong>${esc((timeline[collection.start_var] - timeline[infusion.end_var]) / 60000000)} minutes</strong>.</p></div>`;
  }).join('') : '';
  const notes = {T01:'The original window already holds throughout the 29–31 minute gap range. No widening is needed.',T02:'The 47–49 minute gap range straddles the original 48-minute maximum. A witness and counterexample explain why possibility is weaker than certainty.',T03:'The 59–61 minute gap range exceeds both windows. This excludes the represented pair, not every event that might be missing from a clinical record.',T04:'These events use distinct clock identifiers with no admitted alignment. Equal-looking coordinates and origin strings do not authorize comparing them.'};
  $('temporal-evidence').innerHTML = `<h3>${esc(patient)}</h3><p class="hint">${esc(notes[patient] || '')}</p><div class="cohort-grid">${timelines}</div><h4>Authored endpoint bounds</h4><div class="table-scroll"><table><thead><tr><th>Endpoint</th><th>Lower (min)</th><th>Upper (min)</th><th>Clock</th></tr></thead><tbody>${variables.map(v=>`<tr><td>${esc(v.id)}</td><td>${esc(v.lower_us / 60000000)}</td><td>${esc(v.upper_us / 60000000)}</td><td>${esc(v.clock_id)}</td></tr>`).join('')}</tbody></table></div><details><summary>Query options, witnesses, counterexamples and proof paths</summary><p class="hint">Certificate coordinates and bounds below are integer microseconds. Each option fixes its event pair before testing every feasible source timeline.</p><pre>${esc(JSON.stringify(evaluations,null,2))}</pre></details><details><summary>Source events and PRO / SOLID provenance</summary><pre>${esc(JSON.stringify({events,bindings:proofs},null,2))}</pre></details>`;
}
$('temporal-budget').onchange = () => {clear(); $('status').textContent = 'Budget changed. Run again to obtain results for this choice.';};
$('run').onclick = async () => {
  if (busy || !ready) return;
  busy = true; controls(); clear();
  $('status').className = ''; $('status').textContent = 'Evaluating every represented pair…';
  try {
    render(await api('/api/temporal/run',{budget:$('temporal-budget').value}));
    $('status').textContent = 'Complete for these four authored histories and this permitted catalogue.';
  } catch(error) {clear(); $('status').className = 'error'; $('status').textContent = error.message;}
  finally {busy = false; controls();}
};
$('temporal-rows').onclick = event => {const button=event.target.closest('[data-patient]'); if(button) inspect(button.dataset.patient);};
async function initialize() {
  controls();
  try {
    const meta = await api('/api/temporal');
    const w = meta.workload, l = meta.limits;
    $('limits').textContent = `${w.patients} histories, ${w.events} events, ${w.variables} endpoint variables and ${w.slots} query slots. At most ${l.candidate_bindings_per_evaluation} candidate pairs per evaluation and ${l.evaluations} evaluations (original plus one widening). Oversized workloads are rejected before execution; results are never silently truncated. Up to ${l.saved_results} results are retained in memory.`;
    ready = true; $('temporal-budget').value = '0';
    $('status').textContent = 'Ready. Start with the original 48-minute question.';
  } catch(error) {$('status').className = 'error'; $('status').textContent = error.message;}
  controls();
}
initialize();
