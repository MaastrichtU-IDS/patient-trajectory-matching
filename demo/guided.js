'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const short = value => String(value).split('/').pop();
const isLive = location.protocol === 'http:' || location.protocol === 'https:';
const dataset = GUIDED.dataset;
const patients = Object.fromEntries(dataset.patients.map(p => [p.patient_id, p]));
const labels = {EXACT:'Exact match', RELAXED:'Relaxed match', UNRESOLVED:'Unresolved', NONE:'Not selected'};
let step = 0, selected = 'P00', result = null, budget = '0', busy = false, revision = 0;
let added = [], removed = [], previousCount = null, retryAction = null;
let elapsedSeconds = 0, timerRunning = false;

function events(patient) {
  const all = patient.case.events;
  return {baseline:all.find(e => e.event_id.endsWith('-B')),
          exposure:all.find(e => e.event_id.endsWith('-E')),
          followup:all.find(e => e.event_id.endsWith('-F'))};
}

function facts(patient) {
  const e = events(patient), b = e.baseline, x = e.exposure, f = e.followup;
  return {b, x, f, delta:((Number(f.value)*1000-Number(b.value)*1000)/1000).toFixed(2),
          hours:(f.time.start_min_us-b.time.start_min_us)/36e8,
          days:(f.time.start_min_us-x.time.start_min_us)/864e8,
          drug:x.concept.replace('ex:', '')};
}

function currentResult(pid = selected) {
  return result?.results.find(r => r.patient_id === pid);
}

function setBusy(value) {
  busy = value;
  $('workspace').setAttribute('aria-busy', String(value));
  document.querySelectorAll('[data-step], [data-budget]').forEach(b => b.disabled = value);
  $('next').disabled = value;
  $('back').disabled = value || step === 0;
}

async function enterStep(target, overrideBudget = null) {
  const number = ++revision;
  const story = GUIDED.story[target];
  const desiredBudget = overrideBudget ?? story.budget;
  const wasIncluded = result?.membership.included ?? [];
  const hadResult = !!result;
  setBusy(true);
  $('error').hidden = true;
  $('export-message').textContent = '';
  let nextResult = null;
  try {
    if (desiredBudget !== null) {
      if (isLive) {
        const response = await fetch('/api/cohort?budget=' + encodeURIComponent(desiredBudget), {cache:'no-store'});
        const value = await response.json();
        if (!response.ok) throw Error(value.error || 'The matcher did not return a result.');
        if (value.execution_mode !== 'live-python' || !Array.isArray(value.results)
            || value.query?.budget.max_total_cost !== desiredBudget) throw Error('Unexpected matcher response.');
        if (value.dataset_sha256 !== GUIDED.replays[desiredBudget].dataset_sha256) throw Error('The page and server use different snapshots. Rebuild the guided demo and reload.');
        nextResult = value;
      } else {
        nextResult = structuredClone(GUIDED.replays[desiredBudget]);
      }
    }
    if (number !== revision) return;
    step = target;
    budget = desiredBudget ?? '0';
    result = nextResult;
    selected = story.patient;
    const now = result?.membership.included ?? [];
    added = hadResult && result ? now.filter(id => !wasIncluded.includes(id)) : [];
    removed = hadResult && result ? wasIncluded.filter(id => !now.includes(id)) : [];
    previousCount = hadResult && result ? wasIncluded.length : null;
    retryAction = null;
    render();
  } catch (error) {
    if (number !== revision) return;
    retryAction = () => enterStep(target, overrideBudget);
    $('error').hidden = false;
    $('error').innerHTML = `${esc(error.message)} The previous view is retained; no new cohort was computed. <button id="retry">Retry</button>`;
    $('retry').onclick = () => retryAction?.();
    $('mode').textContent = 'Live request failed · previous view retained';
  } finally {
    if (number === revision) setBusy(false);
  }
}

function render() {
  const story = GUIDED.story[step];
  $('mode').textContent = isLive ? (result ? 'Live · Python matcher' : 'Local server · ready') : 'Offline · recorded replay';
  $('lab-link').href = isLive ? '/lab' : 'Patient_Trajectory_Demo.html';
  $('steps').innerHTML = GUIDED.story.map((s,i) => `<button class="step" data-step="${i}" ${i === step ? 'aria-current="step"' : ''}><b>${i+1}</b><span>${esc(s.short)}</span></button>`).join('');
  document.querySelectorAll('[data-step]').forEach(b => b.onclick = () => enterStep(Number(b.dataset.step)));
  document.querySelectorAll('[data-budget]').forEach(b => {
    b.setAttribute('aria-pressed', String(b.dataset.budget === budget));
    b.onclick = () => enterStep(Math.max(step, 1), b.dataset.budget);
  });
  $('cue-label').textContent = `Step ${step+1} of 5 · ${story.time}`;
  $('cue-title').textContent = story.title;
  const custom = result && story.budget !== budget;
  $('cue').textContent = custom ? `Exploring cost budget ${budget}. Use the next guided step to resume the presentation.` : story.cue;
  $('narrative').textContent = custom ? 'The controls differ from this scripted step. Select its step above to restore the expected cohort before reading the narrative.' : story.narrative;
  $('narrative-time').textContent = story.time;
  $('next').textContent = story.next + (step < 4 ? ' →' : ' ↓');
  $('back').disabled = step === 0;
  $('snapshot').textContent = `${dataset.snapshot_id} · ${isLive ? 'local execution' : 'portable replay'}`;
  if (!result) {
    $('summary').innerHTML = '<div class="total"><b>1</b><span>reference history</span></div><span class="breakdown">Ready to evaluate 10 candidates</span><span class="change">Reference patient P00 will be excluded from the cohort.</span>';
  } else {
    const m = result.membership;
    const change = previousCount === null ? 'First query · all 10 candidates evaluated' :
      `${previousCount} → ${m.included.length} included · ${added.length} entered${added.length ? ' ('+added.join(', ')+')' : ''} · ${removed.length} left${removed.length ? ' ('+removed.join(', ')+')' : ''}`;
    $('summary').innerHTML = `<div class="total"><b>${m.included.length}<span> / ${result.candidate_count}</span></b><span>in the cohort</span></div><span class="breakdown"><b class="EXACT">${m.exact.length} exact</b> &nbsp;·&nbsp; <b class="RELAXED">${m.relaxed.length} relaxed</b> &nbsp;·&nbsp; <b class="UNRESOLVED">${m.unresolved.length} unresolved</b> &nbsp;·&nbsp; ${m.not_matched.length} not selected</span><span class="change">${esc(change)}</span>`;
  }
  renderPatients();
  renderPatient();
}

function renderPatients() {
  const reference = `<button class="patient reference-button" data-patient="P00" aria-pressed="${selected === 'P00'}"><span class="status-dot"></span><strong>P00</strong><span class="patient-status">Reference · not counted</span></button>`;
  $('patients').innerHTML = reference + (result ? result.results.map(r =>
    `<button class="patient" data-patient="${r.patient_id}" aria-pressed="${selected === r.patient_id}"><span class="status-dot ${r.accepted_as}"></span><strong>${r.patient_id}</strong><span class="patient-status ${r.accepted_as}">${labels[r.accepted_as]}</span>${added.includes(r.patient_id) ? '<span class="new">NEW</span>' : r.total_cost !== null ? '<span class="cost">cost '+esc(r.total_cost)+'</span>' : ''}</button>`).join('') :
    '<div class="empty">The same explicit trajectory pattern will be checked against each candidate.<br><br>Use <b>Find the exact cohort</b> to begin.</div>');
  document.querySelectorAll('[data-patient]').forEach(b => b.onclick = () => selectPatient(b.dataset.patient));
}

function selectPatient(pid) {
  selected = pid;
  renderPatients();
  renderPatient();
}

function timeline(f) {
  // All histories share the same relative scale. Recorded anchors are not process durations.
  const x = day => 50 + ((day+10)/10)*430;
  const bx = x(-f.hours/24), ex = x(-f.days), fx = x(0);
  let svg = `<svg viewBox="0 0 530 206" role="img" aria-label="${esc(f.drug)} at minus ${f.days} days; baseline ${f.hours} hours before follow-up; rise ${f.delta} milligrams per decilitre"><rect x="${x(-2)}" y="28" width="${fx-x(-2)}" height="126" fill="#f0f7ef" rx="5"/>`;
  for (const day of [-10,-7,-2,0]) {
    svg += `<line x1="${x(day)}" x2="${x(day)}" y1="28" y2="157" stroke="${day===0 ? '#94ada1' : '#e0e8e0'}" stroke-dasharray="3 4"/><text class="axis" x="${x(day)}" y="178" text-anchor="middle">${day===0 ? 'Index · 0' : day+'d'}</text>`;
  }
  svg += `<line x1="${ex}" x2="${fx}" y1="60" y2="60" stroke="#cfb47c" stroke-width="2"/><rect x="${ex-5}" y="55" width="10" height="10" rx="1" fill="#a87a25"/><text class="event-label" x="${ex}" y="46" text-anchor="${ex<90 ? 'start' : 'middle'}">${esc(f.drug)}</text><text x="${Math.min(ex+10,430)}" y="78">${f.days}d before follow-up</text>`;
  svg += `<line x1="${bx}" x2="${fx}" y1="129" y2="109" stroke="#0b7867" stroke-width="2"/><circle cx="${bx}" cy="129" r="5" fill="#0b7867"/><circle cx="${fx}" cy="109" r="5" fill="#0b7867"/><text class="event-label" x="${bx-10}" y="130" text-anchor="end">${Number(f.b.value).toFixed(2)}</text><text class="event-label" x="${fx-7}" y="97" text-anchor="end">${Number(f.f.value).toFixed(2)} mg/dL</text><text class="axis" x="${(bx+fx)/2}" y="150" text-anchor="middle">${f.hours}h</text><text class="axis" x="265" y="200" text-anchor="middle">Recorded anchors aligned to each patient's follow-up</text></svg>`;
  return svg;
}

function renderPatient() {
  const p = patients[selected], f = facts(p), r = currentResult();
  $('history').innerHTML = `<div class="history-head"><div><div class="eyebrow">${selected === 'P00' ? 'Reference history' : 'Inspect a candidate'}</div><h2>Patient ${selected}</h2><p>One admission · synthetic adult</p></div><span class="status-badge ${r?.accepted_as ?? ''}">${r ? labels[r.accepted_as] : 'Reference'}${r?.total_cost !== null && r?.total_cost !== undefined ? ' · cost '+r.total_cost : ''}</span></div><div class="trajectory">${timeline(f)}</div><div class="fact-strip"><div><b>+${f.delta}</b><span>mg/dL creatinine rise</span></div><div><b>${f.hours} hours</b><span>baseline → follow-up</span></div><div><b>${f.days} days</b><span>exposure → follow-up</span></div></div>`;
  renderExplanation(p, f, r);
  renderEvidence(p, f, r);
}

function renderExplanation(p, f, r) {
  const check = (title, text, symbol='✓') => `<div class="check"><div class="symbol">${symbol}</div><div><b>${esc(title)}</b><span>${esc(text)}</span></div></div>`;
  let title, intro, checks = '', callout = '';
  if (!r) {
    title = 'Make the history explicit';
    intro = 'This reference supplies the worked pattern. The cohort search evaluates ten other patients.';
    checks = check('Clinical meaning', 'An administration of Drug A, including entailed subtypes.') +
      check('Temporal structure', 'Exposure strictly before follow-up; gap at most 7 elapsed days.') +
      check('Measured change', 'At least 0.30 mg/dL within 48 hours. This threshold stays fixed.');
  } else if (r.accepted_as === 'EXACT' || r.accepted_as === 'RELAXED') {
    const exact = r.accepted_as === 'EXACT';
    title = exact ? 'Every condition is satisfied' : 'An explicit, costed change';
    intro = exact ? 'The original query has a named matching binding.' : 'This patient satisfies a broadened query. The original query is not satisfied.';
    const subtype = f.drug === 'DrugAChild';
    checks += check(subtype ? 'Ontology entailment · cost 0' : 'Exposure concept', subtype ? 'DrugAChild ⊑ DrugA in the declared taxonomy.' :
      `${f.drug}${r.components.exposure !== '0' ? ' is a permitted alternative · cost '+r.components.exposure : ' satisfies DrugA · cost 0'}`, r.components.exposure !== '0' ? '+' : '✓');
    checks += check('Exposure window', `${f.days} elapsed days${r.components.exposure_window !== '0' ? ' · '+(f.days-7)+' extra days · cost '+r.components.exposure_window : ' ≤ 7 days · cost 0'}`, r.components.exposure_window !== '0' ? '+' : '✓');
    checks += check('Hard conditions', `Rise ${f.delta} ≥ 0.30 mg/dL; ${f.hours} ≤ 48 hours. Same person and admission.`);
    if (!exact) callout = `Cost ${r.components.exposure} for concept + ${r.components.exposure_window} for time = <b>${r.total_cost}</b>. Entailment and relaxation are reported separately.`;
    else if (subtype) callout = 'A subtype match is an entailed match, not a paid substitution. This demo uses the declared named-class taxonomy.';
  } else if (r.accepted_as === 'UNRESOLVED') {
    title = 'The evidence is incomplete';
    intro = 'P10 has an incomplete source search. The matcher cannot determine cohort membership from this snapshot.';
    checks = check('Not counted as a negative', 'Unresolved remains separate from a failed record query.', '?') +
      check('Not added to the cohort', 'The uncertainty is not removed by a larger budget.', '?');
    callout = 'The displayed measurements do not establish that the source search is complete.';
  } else {
    const reasons = r.reason_codes;
    if (reasons.includes('HARD_VALUE_FAILURE')) {
      title = 'This boundary does not move';
      intro = `A ${f.delta} mg/dL rise is below the fixed 0.30 threshold.`;
      checks = check('Hard measurement condition', 'The permitted changes concern only the exposure concept and window.', '×') +
        check('No accepted binding', 'Increasing the budget cannot relax an unapproved constraint.', '×');
    } else if (reasons.includes('NO_PERMITTED_CONCEPT')) {
      title = 'No permitted concept match';
      intro = `${f.drug} is neither an entailed Drug A subtype nor the permitted Drug B alternative.`;
    } else if (reasons.includes('HARD_TEMPORAL_FAILURE')) {
      title = 'Beyond the permitted extension';
      intro = `${f.days} days exceeds even the permitted 9-day window. A larger cost budget cannot expand that cap.`;
    } else if (f.hours > 48) {
      title = 'No baseline in the required window';
      intro = `The available baseline is ${f.hours} hours before follow-up. The fixed laboratory window is 48 hours.`;
    } else {
      title = 'Outside the current budget';
      intro = 'A permitted change exists, but its cost exceeds the displayed budget.';
      const semantic = f.drug === 'DrugB' ? 1 : 0, time = Math.max(0,(f.days-7)/2);
      checks = check('Required cost for these records', `${semantic} for concept + ${time} for time = ${semantic+time}; current budget ${budget}.`, '+');
    }
    callout = 'Not selected from these records. This is not evidence that a clinical event was absent.';
  }
  const choices = step === 2 ? [['P04','P04 · alternative'],['P05','P05 · longer gap']] : step === 3 ?
    [['P06','P06 · both changes'],['P07','P07 · hard limit'],['P10','P10 · unresolved']] : step === 4 ? [['P03','P03 · unit normalization'],['P02','P02 · subtype']] : [];
  $('explanation').innerHTML = `<div class="eyebrow">Why ${r?.accepted_as === 'EXACT' || r?.accepted_as === 'RELAXED' ? 'included' : r ? 'not included' : 'this pattern'}?</div><h2>${esc(title)}</h2><p>${esc(intro)}</p>${checks}${callout ? '<div class="callout">'+callout+'</div>' : ''}${choices.length ? '<div class="inspection">'+choices.map(([id,label])=>`<button data-inspect="${id}">${label}</button>`).join('')+'</div>' : ''}`;
  document.querySelectorAll('[data-inspect]').forEach(b => b.onclick = () => selectPatient(b.dataset.inspect));
}

function renderEvidence(p, f, r) {
  const bound = !!r?.binding;
  const evidence = p.evidence.bindings[f.b.event_id];
  const source = p.source_rows.find(row => row.event_id === f.b.event_id);
  const role = short(evidence.patient_role), person = short(evidence.patient_bearer), process = short(evidence.process);
  $('evidence').innerHTML = `<details class="evidence-block" ${step === 4 ? 'open' : ''}><summary>${bound ? 'Inspect the matched evidence' : 'Inspect the available source records'}</summary>
    <div class="normalization">${esc(evidence.original_value)} ${esc(evidence.original_unit)} → <b>${Number(evidence.normalized_value).toFixed(2)} ${esc(evidence.normalized_unit)}</b><span>Original baseline value retained · normalization factor ${esc(evidence.unit_factor)}</span></div>
    <h3>PRO · who participated, and in what role?</h3><div class="binding"><code>${esc(process)}</code> <b>hasParticipant</b> <code>${esc(role)}</code><br><code>${esc(role)}</code> <b>isFeatureOf</b> <code>${esc(person)}</code></div><p>Each process has its own patient-role instance. The roles share the same person as bearer.</p>
    <h3>SOLID · what does the recorded literal mean?</h3><div class="binding"><code>${esc(short(evidence.result_datum))}</code> <b>hasValue</b> <code>${esc(evidence.original_value)}</code><br>Typed creatinine result · unit <code>${esc(short(evidence.unit_resource))}</code> · source <code>${esc(short(evidence.source_record))}</code></div>
    <details><summary>Source row, graph bindings &amp; fingerprints</summary><pre>${esc(JSON.stringify({source_row:source,matcher_binding:r?.binding ?? null,source_graph_sha256:p.evidence.source_graph_sha256,graph_bindings:p.evidence.bindings},null,2))}</pre></details><p>Graphs validated and projected at dataset build time. Live requests evaluate the projected records with the Python matcher.</p></details>`;
}

async function exportCurrent() {
  if (!result || busy) return;
  const currentRevision = revision;
  setBusy(true);
  $('error').hidden = true;
  try {
    let bundle;
    if (isLive) {
      const response = await fetch('/api/cohort/export?budget=' + encodeURIComponent(budget), {cache:'no-store'});
      bundle = await response.json();
      if (!response.ok) throw Error(bundle.error || 'Export failed.');
      if (bundle.evaluation.query_sha256 !== result.query_sha256 ||
          bundle.evaluation.dataset_sha256 !== result.dataset_sha256 ||
          JSON.stringify(bundle.evaluation.results) !== JSON.stringify(result.results)) {
        throw Error('The export differs from the displayed cohort. Run the query again.');
      }
    } else bundle = {format:'guided-cohort-export-1', dataset, evaluation:result};
    if (currentRevision !== revision) return;
    const name = `trajectory-cohort-budget-${budget}.json`;
    const url = URL.createObjectURL(new Blob([JSON.stringify(bundle,null,2)], {type:'application/json'}));
    const link = document.createElement('a');
    link.href = url; link.download = name;
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    $('export-message').textContent = `Downloaded ${name}: ${result.membership.included.length} selected patients, the query, all candidate results, source rows and graph evidence.`;
  } catch (error) {
    if (currentRevision !== revision) return;
    $('error').hidden = false;
    $('error').textContent = error.message + ' No export was saved.';
  } finally { if (currentRevision === revision) setBusy(false); }
}

function renderTimer() {
  $('timer').innerHTML = `${timerRunning ? 'Ⅱ' : '▶'} <span id="elapsed">${Math.floor(elapsedSeconds/60)}:${String(elapsedSeconds%60).padStart(2,'0')}</span>`;
  $('timer').setAttribute('aria-label', (timerRunning ? 'Pause' : 'Start') + ' presentation timer');
}
$('timer').onclick = () => { timerRunning = !timerRunning; renderTimer(); };
setInterval(() => { if (timerRunning) { elapsedSeconds++; renderTimer(); } }, 1000);
$('next').onclick = () => {
  if (step === 4) return exportCurrent();
  if (step === 0) { timerRunning = true; renderTimer(); }
  return enterStep(step+1);
};
$('back').onclick = () => enterStep(Math.max(0,step-1));
function restart() { elapsedSeconds = 0; timerRunning = false; renderTimer(); return enterStep(0); }
$('restart').onclick = restart;
$('home').onclick = event => { event.preventDefault(); restart(); };
render();
