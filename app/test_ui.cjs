/* Exercise the actual UI script against responses produced by the Python engine. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const root = path.resolve(__dirname,'..');
const payload = JSON.parse(execFileSync(process.env.PYTHON || 'python', ['-c', `
import json
from app.server import Workspace
w=Workspace()
a=w.engine.initial('P00',5)
b=w.engine.refine(a['revision_id'],{'type':'set_weights','weights':{'age_band':'1','baseline_creatinine':'3','clinical_concepts':'1'}})
c=w.engine.refine(b['revision_id'],{'type':'add_filter','predicate':{'component':'baseline_creatinine','operator':'between','value':{'min':'0','max':'1.1'}}})
print(json.dumps({'capabilities':w.capabilities(),'initial':a,'weighted':b,'refined':c,'comparison':w.compare(c['revision_id'],'2'),'evidence':w.evidence(c['revision_id'],'P03')}))
`], {cwd:root, encoding:'utf8'}));
class Element {
  constructor(){this.value='';this.innerHTML='';this.textContent='';this.className='';this.disabled=false;this.href='';}
  removeAttribute(name){delete this[name];}
}
const elements=new Map();
const document={getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id);}};
const calls=[]; let refinements=0, failNext=false;
const fetch=async(url, options={})=>{
  const body=options.body?JSON.parse(options.body):undefined; calls.push({url,body});
  if(failNext){failNext=false;return{ok:false,json:async()=>({error:'Deliberate evaluation failure'})};}
  let value;
  if(url==='/api/capabilities')value=payload.capabilities;
  else if(url==='/api/initial')value=payload.initial;
  else if(url==='/api/refine')value=++refinements===1?payload.weighted:payload.refined;
  else if(url==='/api/trajectory')value=payload.comparison;
  else if(url.startsWith('/api/evidence?'))value=payload.evidence;
  else if(url.startsWith('/api/revisions/'))value=payload.weighted;
  else throw new Error('Unexpected API '+url);
  return{ok:true,json:async()=>structuredClone(value)};
};
const context=vm.createContext({document,fetch,console,encodeURIComponent,Map,Error});
vm.runInContext(fs.readFileSync(path.join(__dirname,'app.js'),'utf8'),context);
const el=id=>document.getElementById(id);
const settle=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  await settle();
  assert.equal(el('patient').value,'P00');
  assert.equal(el('compare').disabled,true);
  await el('start').onclick();
  assert.equal(calls.at(-1).body.patient_id,'P00');
  assert.match(el('rankings').innerHTML,/P01/);
  assert.match(el('scope').textContent,/10 eligible/);
  // UI-002: the four dispositions. The first three are one control; hiding is its own.
  assert.match(el('components').innerHTML,/Must match/);
  assert.match(el('components').innerHTML,/Prefer similar/);
  assert.match(el('components').innerHTML,/Ignore/);
  assert.match(el('components').innerHTML,/Hide from view/);
  assert.match(el('effect-age_band').textContent,/ranking only/);
  const change=(component,kind,value)=>el('components').onchange(
    {target:{dataset:{component,kind},value,checked:value===true}});
  change('baseline_creatinine','mode','must');
  assert.match(el('effect-baseline_creatinine').textContent,/eligibility/);
  change('clinical_concepts','mode','ignore');
  assert.match(el('effect-clinical_concepts').textContent,/neither/);
  el('maximum').value='1.1';
  const beforeApply=calls.length;
  await el('apply').onclick();
  assert.equal(calls.length,beforeApply+2,'weights first, then the required value');
  assert.equal(calls[beforeApply].body.operation.type,'set_weights');
  assert.equal(calls[beforeApply].body.revision_id,payload.initial.revision_id);
  // A feature that must match is given weight zero, so the two effects stay separable.
  assert.equal(calls[beforeApply].body.operation.weights.baseline_creatinine,'0');
  assert.equal(calls[beforeApply].body.operation.weights.clinical_concepts,'0');
  assert.equal(calls[beforeApply].body.operation.weights.age_band,'1');
  assert.equal(calls.at(-1).body.operation.type,'add_filter');
  assert.equal(calls.at(-1).body.operation.predicate.component,'baseline_creatinine');
  assert.equal(calls.at(-1).body.operation.predicate.value.max,'1.1');
  assert.equal(calls.at(-1).body.revision_id,payload.weighted.revision_id);
  assert.match(el('effects').textContent,/Eligibility: \d+ added, \d+ removed/);
  assert.match(el('effects').textContent,/Ranking: /);
  assert.match(el('missing').textContent,/P08/);
  assert.match(el('changes').textContent,/removed P08/);
  // The invariant: hiding is display-only. No request, no revision, no change to who is
  // eligible or to the order they are shown in.
  el('rankings').onclick({target:{closest:()=>({dataset:{patient:'P03'}})}});
  await settle();
  const quiet=calls.length, ranking=el('rankings').innerHTML, scope=el('scope').textContent;
  const evidenceBefore=el('evidence').innerHTML;
  change('age_band','hide',true);
  await settle();
  assert.equal(calls.length,quiet,'hiding a feature must not reach the server');
  assert.equal(el('rankings').innerHTML,ranking,'hiding must not reorder the ranking');
  assert.equal(el('scope').textContent,scope,'hiding must not change who is eligible');
  assert.match(el('effect-age_band').textContent,/hidden from view/);
  assert.match(el('profile').textContent,/1 feature is hidden from this view; the analysis used all 3/);
  // esc() escapes the quotes, so the rendered JSON spells keys &quot;like this&quot;.
  assert.match(evidenceBefore,/&quot;age_band&quot;/,'the unhidden evidence names the feature');
  assert.doesNotMatch(el('evidence').innerHTML,/&quot;age_band&quot;/);
  assert.match(el('evidence').innerHTML,/Hidden from this view: Age band/);
  change('age_band','hide',false);
  await settle();
  assert.equal(calls.length,quiet,'unhiding must not reach the server either');
  assert.equal(el('evidence').innerHTML,evidenceBefore,'unhiding restores the withheld rows exactly');
  // A ranking needs something to rank on, and that is refused before any request.
  for (const component of ['age_band','baseline_creatinine','clinical_concepts']) change(component,'mode','ignore');
  await el('apply').onclick();
  assert.equal(calls.length,quiet,'a plan with nothing to rank on must not be sent');
  assert.equal(el('status').className,'error');
  assert.match(el('status').textContent,/Prefer similar/);
  change('baseline_creatinine','mode','prefer');
  el('budget').value='2';
  await el('compare').onclick();
  assert.equal(calls.at(-1).body.revision_id,payload.refined.revision_id);
  assert.match(el('trajectory-summary').textContent,/3 exact matches; 3 additional/);
  assert.match(el('trajectory-summary').textContent,/P10/);
  assert.match(el('cohorts').innerHTML,/P06/);
  assert.equal(el('export').href,'/api/export/'+payload.comparison.comparison_id);
  // Decision D: the result names the model that priced it, and the name is read from the
  // payload rather than printed. Two probes establish that it is read -- a different
  // identifier has to reach the screen, and two disagreeing identifiers have to refuse
  // rather than quietly pick one.
  assert.match(el('priced-by').textContent,/point-anchor-relaxation-1\.0/);
  assert.match(el('priced-by').textContent,/worst-case timing/);
  payload.comparison.exact.relaxation_profile='probe-profile-9.9';
  payload.comparison.relaxed.relaxation_profile='probe-profile-9.9';
  await el('compare').onclick();
  assert.match(el('priced-by').textContent,/probe-profile-9\.9/,'the identifier is printed, not read from the result');
  payload.comparison.relaxed.relaxation_profile='a-disagreeing-profile';
  await el('compare').onclick();
  assert.match(el('priced-by').textContent,/does not name the cost model/,'disagreeing identifiers must refuse, not pick one');
  payload.comparison.exact.relaxation_profile='point-anchor-relaxation-1.0';
  payload.comparison.relaxed.relaxation_profile='point-anchor-relaxation-1.0';
  await el('compare').onclick();
  payload.evidence.source_rows[0].source_code='<img src=x onerror=alert(1)>';
  el('cohorts').onclick({target:{closest:()=>({dataset:{patient:'P03'}})}});
  await settle();
  assert.match(el('evidence').innerHTML,/&lt;img/);
  assert.doesNotMatch(el('evidence').innerHTML,/<img/);
  assert.match(el('evidence').innerHTML,/patient_role/);
  await el('undo').onclick();
  assert.match(el('export').className,/hidden/);
  assert.equal(el('cohorts').innerHTML,'');
  assert.equal(el('priced-by').textContent,'','a profile line outliving its result names a model that priced nothing on screen');
  failNext=true;
  await el('compare').onclick();
  assert.equal(el('status').className,'error');
  assert.match(el('status').textContent,/Deliberate evaluation failure/);
  assert.match(el('export').className,/hidden/);
  assert.equal(el('compare').disabled,false);
  console.log('Research UI: four feature dispositions with display-only hiding, complete two-refinement journey, comparison, the cost model named from the payload, evidence, safe rendering, undo and failure states passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
