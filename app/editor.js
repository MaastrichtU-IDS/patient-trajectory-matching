'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fields=['fixture','relation','gap-min','gap-max','duration-enabled','duration-min','duration-max','overlap-enabled','overlap-min','relax-enabled','relax-budget','relax-gap-enabled','relax-gap-min','relax-gap-max','relax-duration-enabled','relax-duration-min','relax-duration-max','relax-overlap-enabled','relax-overlap-min'];
const labels={CERTAIN:'Certain',POSSIBLE:'Possible only',INCOMPARABLE:'Incomparable',NO_RECORDED_MATCH:'No recorded match'};
let busy=false, ready=false, report=null;
async function api(path,body){
  const r=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data=await r.json();if(!r.ok)throw new Error(data.error||'Query evaluation failed.');return data;
}
function controls(){
  for(const id of fields)$(id).disabled=busy||!ready;
  for(const id of ['gap-min','gap-max'])$(id).disabled=busy||!ready||$('relation').value!=='gap';
  for(const id of ['duration-min','duration-max'])$(id).disabled=busy||!ready||$('duration-enabled').value!=='on';
  $('overlap-min').disabled=busy||!ready||$('overlap-enabled').value!=='on';
  const relaxOff=busy||!ready||$('relax-enabled').value!=='on';
  $('relax-budget').disabled=relaxOff;
  for(const metric of ['gap','duration','overlap']){
    const available=metric==='gap'?$('relation').value==='gap':$(metric+'-enabled').value==='on';
    $('relax-'+metric+'-enabled').disabled=relaxOff||!available;
    for(const bound of (metric==='overlap'?['min']:['min','max']))
      $('relax-'+metric+'-'+bound).disabled=relaxOff||!available||$('relax-'+metric+'-enabled').value!=='on';
  }
  $('run').disabled=busy||!ready;
}
function clear(){
  report=null;$('rows').innerHTML='';$('summary').textContent='Evaluate the current controls to see results.';
  $('query').textContent='No executed query yet.';$('evidence').textContent='Select Inspect after evaluation.';
  $('export').className='button hidden';$('export').removeAttribute('href');
}
function changed(){clear();controls();$('status').className='';$('status').textContent='Query changed. Evaluate again to obtain results for these controls.';}
for(const id of fields){$(id).onchange=changed;$(id).oninput=changed;}
function request(){
  const range=metric=>({minimum_minutes:$('relax-'+metric+'-min').value,maximum_minutes:$('relax-'+metric+'-max').value});
  return {fixture:$('fixture').value,relation:$('relation').value,
    gap:$('relation').value==='gap'?{minimum_minutes:$('gap-min').value,maximum_minutes:$('gap-max').value}:null,
    duration:$('duration-enabled').value==='on'?{minimum_minutes:$('duration-min').value,maximum_minutes:$('duration-max').value}:null,
    minimum_overlap_minutes:$('overlap-enabled').value==='on'?$('overlap-min').value:null,
    relaxation:$('relax-enabled').value==='on'?{
      max_cost:$('relax-budget').value,
      gap:$('relation').value==='gap'&&$('relax-gap-enabled').value==='on'?range('gap'):null,
      duration:$('duration-enabled').value==='on'&&$('relax-duration-enabled').value==='on'?range('duration'):null,
      minimum_overlap_minutes:$('overlap-enabled').value==='on'&&$('relax-overlap-enabled').value==='on'?$('relax-overlap-min').value:null
    }:null};
}
function render(value){
  if(value.result.search_complete!==true||value.relaxation.search_complete!==true||value.relaxation.evaluations.some(e=>e.result.search_complete!==true))throw new Error('Incomplete query; no completed cohort is available.');
  report=value;
  const counts=value.patients.reduce((out,row)=>{out[row.status]=(out[row.status]||0)+1;return out;},{});
  $('summary').textContent=`${value.controls.fixture} fixture · ${value.query.constraints.length} constraints evaluated together · ${counts.CERTAIN||0} certain, ${counts.POSSIBLE||0} possible only, ${counts.INCOMPARABLE||0} incomparable, ${counts.NO_RECORDED_MATCH||0} with no recorded match.`;
  const optionNote=value.policy.options.length?(value.relaxation.excluded_by_budget.length?'Option excluded by budget.':`Option evaluated; ${value.patients.filter(r=>r.status!=='CERTAIN'&&r.selected_option==='edited-option').length} additional certain histories.`):'No relaxation option requested.';
  $('summary').textContent+=' '+optionNote;
  $('rows').innerHTML=value.patients.map(row=>`<tr><td>${esc(row.patient_id)}</td><td>${esc(labels[row.status]||row.status)}</td><td>${esc(row.option_status?labels[row.option_status]:value.policy.options.length?'Excluded by budget':'Not requested')}</td><td>${esc(row.selected_option?(row.selected_option==='original'?'Original':'Edited option')+' · cost '+row.selected_cost:'None')}</td><td><button class="inspect" data-patient="${esc(row.patient_id)}">Inspect</button></td></tr>`).join('');
  $('query').textContent=JSON.stringify({query:value.query,policy:value.policy},null,2);
  $('export').href='/api/editor/export/'+encodeURIComponent(value.report_id);$('export').className='button';
}
function inspect(patient){
  if(!report)return;
  const trajectories=report.result.trajectories.filter(t=>t.patient_id===patient);
  if(!trajectories.length)return;
  const events=report.source.events.filter(e=>e.patient_id===patient);
  const variables=report.source.variables.filter(v=>v.patient_id===patient);
  const bindings=events.map(e=>report.result.evidence.bindings[e.id]);
  const optionEvidence=report.relaxation.evaluations.slice(1).map(e=>({option:e.option,trajectories:e.result.trajectories.filter(t=>t.patient_id===patient)}));
  $('evidence').innerHTML=`<h3>${esc(patient)}</h3><p class="hint">Endpoint bounds below are minutes on each declared clock. Certificates retain integer microseconds.</p><div class="table-scroll"><table><thead><tr><th>Endpoint</th><th>Lower (min)</th><th>Upper (min)</th><th>Clock</th></tr></thead><tbody>${variables.map(v=>`<tr><td>${esc(v.id)}</td><td>${esc(v.lower_us/60000000)}</td><td>${esc(v.upper_us/60000000)}</td><td>${esc(v.clock_id)}</td></tr>`).join('')}</tbody></table></div><details><summary>Original query certificates: witness, counterexample or contradiction</summary><pre>${esc(JSON.stringify(trajectories,null,2))}</pre></details><details><summary>Edited option and certificates</summary><pre>${esc(optionEvidence.length?JSON.stringify(optionEvidence,null,2):report.policy.options.length?'Option excluded by budget.':'No relaxation option requested.')}</pre></details><details><summary>Source events and PRO / SOLID provenance</summary><pre>${esc(JSON.stringify({events,bindings},null,2))}</pre></details>`;
}
$('rows').onclick=event=>{const button=event.target.closest('[data-patient]');if(button)inspect(button.dataset.patient);};
$('run').onclick=async()=>{
  if(busy||!ready)return;
  const body=request();busy=true;controls();clear();$('status').className='';$('status').textContent='Evaluating every represented pair…';
  try{render(await api('/api/editor/run',body));$('status').textContent='Complete for the current authored fixture and conjunction.';}
  catch(error){clear();$('status').className='error';$('status').textContent=error.message;}
  finally{busy=false;controls();}
};
async function initialize(){
  controls();
  try{
    const meta=await api('/api/editor');
    $('relation').innerHTML=meta.relations.map(r=>`<option value="${esc(r)}">${esc(r.replaceAll('_',' '))}</option>`).join('');
    $('fixture').value=meta.default_controls.fixture;$('relation').value=meta.default_controls.relation;
    $('duration-enabled').value='off';$('overlap-enabled').value='off';
    $('relax-enabled').value='off';$('relax-budget').value=meta.relaxation_cost;
    for(const metric of ['gap','duration','overlap'])$('relax-'+metric+'-enabled').value='off';
    const l=meta.limits;
    $('limits').textContent=`At most ${l.patients} histories, ${l.events} events, ${l.variables} endpoint variables, ${l.slots} slots and ${l.query_constraints} conjuncts. At most ${l.candidate_bindings_per_evaluation} candidate pairs per evaluation, ${l.catalogue_options} relaxation option and ${l.evaluations} evaluations, including the original. ${l.saved_results} complete results retained in memory. Oversized work is rejected before the solver runs.`;
    ready=true;$('status').textContent='Ready. Try infusion contains collection on the overlapping fixture.';
  }catch(error){$('status').className='error';$('status').textContent=error.message;}
  controls();
}
initialize();
