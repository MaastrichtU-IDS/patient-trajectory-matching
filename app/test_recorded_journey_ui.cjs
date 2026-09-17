const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {execFileSync}=require('node:child_process');
// Exercise rendering with actual completed source-service responses, including the
// reviewed Rust selector, exact lexical deltas and retained missing follow-ups.
const payload=JSON.parse(execFileSync(process.env.PYTHON||'python',['-c',`
import json,time
from app.recorded_journey import RecordedJourneyWorkspace
w=RecordedJourneyWorkspace()
try:
 result={'metadata':w.metadata()}
 for profile in ('literal','reviewed'):
  job=w.start({'profile':profile,'stratum':'synthetic','controls':result['metadata']['profiles'][profile]['defaults']})
  while (job:=w.get(profile,job['id']))['status']=='RUNNING':time.sleep(.01)
  assert job['status']=='COMPLETED',job
  result[profile]={'job':job,'details':{a['token']:w.inspect(profile,job['id'],a['token']) for a in job['summary']['anchors']}}
 print(json.dumps(result))
finally:w.close()
`],{cwd:path.resolve(__dirname,'..'),encoding:'utf8'}));
const html=fs.readFileSync(path.join(__dirname,'journey.html'),'utf8'),script=fs.readFileSync(path.join(__dirname,'recorded_journey.js'),'utf8');
class Element{constructor(){this.value='';this._text='';this._html='';this.disabled=false;this.className='';}set textContent(v){this._text=v;this._html='';}get textContent(){return this._text;}set innerHTML(v){this._html=v;this._text='';}get innerHTML(){return this._html;}}
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve};};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function harness(metadata=payload.metadata){
 const elements=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()])),calls=[];const el=id=>elements.get('recorded-'+id);
 const controls={postGate:null,detailGate:null,jobGate:null,fail:false,mutate:null,pollRunning:false,detailMutate:null};
 const fetch=async(url,options={})=>{const body=options.body?JSON.parse(options.body):null;calls.push({url,body});if(url.endsWith('/config'))return{ok:true,json:async()=>structuredClone(metadata)};
  const response=value=>({ok:true,json:async()=>structuredClone(value)});
  if(url==='/api/journey/recorded/jobs'){if(controls.postGate)await controls.postGate.promise;if(controls.fail){controls.fail=false;return{ok:false,json:async()=>({error:'Source review changed'})};}return response({id:payload[body.profile].job.id,profile:body.profile});}
  const mode=url.includes('/literal/')?'literal':'reviewed';
  if(url.includes('/anchors/')){if(controls.detailGate)await controls.detailGate.promise;const value=structuredClone(payload[mode].details[url.split('/').at(-1)]);if(controls.detailMutate)controls.detailMutate(value);return response(value);}
  if(controls.jobGate)await controls.jobGate.promise;
  if(controls.pollRunning){controls.pollRunning=false;return response({status:'RUNNING',progress:{stage:'Checking source',completed:1,total:2}});}
  const value=structuredClone(payload[mode].job);if(controls.mutate)controls.mutate(value);return response(value);
 };
 const document={getElementById:id=>elements.get(id)||null};
 // Existing journey globals can coexist with this script because it is IIFE scoped.
 const context=vm.createContext({document,fetch,setTimeout:resolve=>setImmediate(resolve),console,Error,encodeURIComponent});
 vm.runInContext('const api=1,busy=2,config=3,revision=4;',context);vm.runInContext(script,context);
 return{el,calls,controls,inspect:token=>el('anchors').onclick({target:{closest:()=>({dataset:{anchor:token}})}})};
}
(async()=>{
 const h=harness(),{el,calls,controls}=h;await tick();
 assert.equal(el('profile').value,'reviewed');assert.equal(el('run').disabled,false);assert.match(el('selector-evidence').textContent,/rust/i);assert.match(el('selection').textContent,/clinical interpretation remains unverified/);
 controls.pollRunning=true;const run=el('run').onclick();assert.equal(el('run').disabled,true);assert.match(el('status').textContent,/No partial cohort count/);assert.equal(el('summary').innerHTML,'');const n=calls.length;await el('run').onclick();assert.equal(calls.length,n);await run;
 assert.deepEqual(calls.find(c=>c.body).body,{profile:'reviewed',stratum:'synthetic',controls:{threshold:'65',baseline_minutes:30,followup_minutes:120}});
 assert.match(el('summary').innerHTML,/Completed query/);assert.match(el('summary').innerHTML,/Pairs lacking follow-up/);assert.match(el('summary').innerHTML,/dependent record observations/);
 const example=Object.values(payload.reviewed.details).find(d=>d.observations.some(o=>o.followup===null));assert.ok(example);el('patient').value=example.anchor.patient_id;el('patient').onchange();el('stay').value=example.anchor.episode_id;el('stay').onchange();await h.inspect(example.anchor.token);
 assert.match(el('inspector').innerHTML,/No selected follow-up · pair remains eligible/);assert.match(el('inspector').innerHTML,/Not available/);assert.match(el('inspector').innerHTML,/PRO patient role, SOLID values/);assert.match(el('inspector').innerHTML,/Ontology selection and review evidence/);assert.match(el('inspector').innerHTML,/do not establish a treatment effect/);
 // Every query input invalidates cohort and evidence before a new run.
 for(const id of ['threshold','baseline','followup']){el(id).oninput();assert.equal(el('patient').disabled,true);assert.equal(el('anchors').innerHTML,'');assert.match(el('summary').textContent,/current controls/);await el('run').onclick();}
 // Source item choice routes a distinct service, with its own provenance.
 el('profile').value='literal';el('profile').onchange();assert.match(el('selection').textContent,/Direct source-item selection/);await el('run').onclick();assert.equal(calls.filter(c=>c.body).at(-1).body.profile,'literal');assert.ok(calls.at(-1).url.includes('/literal/'));
 // Blank numeric windows are invalid, never an implicit zero-minute follow-up.
 el('followup').value='';el('followup').oninput();await el('run').onclick();assert.equal(calls.filter(c=>c.body).at(-1).body.controls.followup_minutes,null);assert.equal(el('status').className,'error');el('followup').value='120';
 // A delayed old job cannot repopulate results after a control revision.
 controls.jobGate=deferred();const old=el('run').onclick();await tick();el('threshold').oninput();controls.jobGate.resolve();await old;controls.jobGate=null;assert.equal(el('anchors').innerHTML,'');assert.match(el('summary').textContent,/current controls/);
 // A delayed POST likewise never polls or renders under changed controls.
 controls.postGate=deferred();const late=el('run').onclick();el('baseline').oninput();const count=calls.length;controls.postGate.resolve();await late;controls.postGate=null;assert.equal(calls.length,count);
 // Failure, partial verification and mismatched controls all withhold membership.
 for(const mutate of [j=>{j.status='BLOCKED';j.error='No complete source result';},j=>{j.summary.status='BLOCKED';},j=>{j.summary.anchors_verified=0;},j=>{j.summary.context.controls.threshold='64';},j=>{j.request.controls.followup_minutes=119;},j=>{j.profile='reviewed';}]){controls.mutate=mutate;await el('run').onclick();assert.equal(el('status').className,'error');assert.equal(el('anchors').innerHTML,'');assert.equal(el('patient').disabled,true);}controls.mutate=null;
 controls.fail=true;await el('run').onclick();assert.match(el('status').textContent,/Source review changed/);assert.equal(el('run').disabled,false);
 await el('run').onclick();const first=payload.literal.job.summary.anchors.find(a=>a.patient_id===el('patient').value&&a.episode_id===el('stay').value);assert.ok(first);
 controls.detailMutate=d=>{d.query_context_id='wrong';};await h.inspect(first.token);assert.match(el('inspector').innerHTML,/different query/);controls.detailMutate=d=>{d.anchor.token='wrong';};await h.inspect(first.token);assert.match(el('inspector').innerHTML,/different query/);
 controls.detailMutate=d=>{d.treatment.label='<img src=x onerror=alert(1)>';d.reviewer='<script>bad</script>';};await h.inspect(first.token);assert.match(el('inspector').innerHTML,/&lt;img/);assert.match(el('inspector').innerHTML,/&lt;script/);assert.doesNotMatch(el('inspector').innerHTML,/<img|<script/);controls.detailMutate=null;
 controls.detailGate=deferred();const detail=h.inspect(first.token);el('followup').oninput();controls.detailGate.resolve();await detail;controls.detailGate=null;assert.match(el('inspector').innerHTML,/Complete an evaluation/);
 // Empty complete populations are successful, with no fabricated patient/anchor.
 controls.mutate=j=>{j.summary.roster=[];j.summary.anchors=[];j.summary.anchors_total=0;j.summary.anchors_verified=0;for(const k of Object.keys(j.summary.metrics))j.summary.metrics[k]=0;};await el('run').onclick();assert.match(el('inspector').innerHTML,/roster is empty/);assert.equal(el('patient').disabled,true);assert.equal(el('status').className,'');
 // Unavailable ontology review remains visibly blocked and never becomes literal.
 const blocked=structuredClone(payload.metadata);blocked.profiles.reviewed.available=false;blocked.profiles.reviewed.error='BLOCKED_MAPPING_REVIEW';blocked.profiles.reviewed.selectors={};const b=harness(blocked);await tick();assert.equal(b.el('profile').value,'reviewed');assert.equal(b.el('run').disabled,true);assert.match(b.el('status').textContent,/BLOCKED_MAPPING_REVIEW/);await b.el('run').onclick();assert.equal(b.calls.length,1);b.el('profile').value='literal';b.el('profile').onchange();assert.equal(b.el('run').disabled,false);
 console.log('Recorded journey UI: real source/reviewed results, missing follow-ups, complete-only counts, isolation, errors, stale responses and escaped evidence passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
