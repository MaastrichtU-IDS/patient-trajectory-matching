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
  await el('weight').onclick();
  assert.equal(calls.at(-1).body.operation.type,'set_weights');
  assert.equal(calls.at(-1).body.revision_id,payload.initial.revision_id);
  el('maximum').value='1.1';
  await el('filter').onclick();
  assert.equal(calls.at(-1).body.revision_id,payload.weighted.revision_id);
  assert.match(el('missing').textContent,/P08/);
  assert.match(el('changes').textContent,/removed P08/);
  el('budget').value='2';
  await el('compare').onclick();
  assert.equal(calls.at(-1).body.revision_id,payload.refined.revision_id);
  assert.match(el('trajectory-summary').textContent,/3 exact matches; 3 additional/);
  assert.match(el('trajectory-summary').textContent,/P10/);
  assert.match(el('cohorts').innerHTML,/P06/);
  assert.equal(el('export').href,'/api/export/'+payload.comparison.comparison_id);
  payload.evidence.source_rows[0].source_code='<img src=x onerror=alert(1)>';
  el('cohorts').onclick({target:{closest:()=>({dataset:{patient:'P03'}})}});
  await settle();
  assert.match(el('evidence').innerHTML,/&lt;img/);
  assert.doesNotMatch(el('evidence').innerHTML,/<img/);
  assert.match(el('evidence').innerHTML,/patient_role/);
  await el('undo').onclick();
  assert.match(el('export').className,/hidden/);
  assert.equal(el('cohorts').innerHTML,'');
  failNext=true;
  await el('compare').onclick();
  assert.equal(el('status').className,'error');
  assert.match(el('status').textContent,/Deliberate evaluation failure/);
  assert.match(el('export').className,/hidden/);
  assert.equal(el('compare').disabled,false);
  console.log('Research UI: complete two-refinement journey, comparison, evidence, safe rendering, undo and failure states passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
