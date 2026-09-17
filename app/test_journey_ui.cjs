const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {execFileSync}=require('node:child_process');
const payload=JSON.parse(execFileSync(process.env.PYTHON||'python',['-c',`
import json
from app.journey import JourneyWorkspace
w=JourneyWorkspace();m=w.metadata();c=m['default_request']
variants={'original':dict(c,budget='0'),'relaxed':c,'filtered':dict(c,maximum_baseline=50),'empty':dict(c,maximum_baseline=0),'unrankable':dict(c,reference_patient_id='T04'),'sequential':dict(c,question='sequential')}
print(json.dumps({'metadata':m,**{k:w.run(v) for k,v in variants.items()}}))
`],{cwd:path.resolve(__dirname,'..'),encoding:'utf8'}));
class Element{constructor(){this.value='';this.innerHTML='';this.textContent='';this.disabled=false;this.className='';}removeAttribute(name){delete this[name];}scrollIntoView(){}}
const elements=new Map(),document={getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id);}},el=id=>document.getElementById(id);
let fail=false,incomplete=false,incompleteOption=false,gate=null;const calls=[];
function backend(url,body){return JSON.parse(execFileSync(process.env.PYTHON||'python',['-c',`
import json,sys
from app.journey import JourneyWorkspace
from app.pattern_builder import compile_pattern,decompile_query
body=json.loads(sys.argv[2])
try:
 if sys.argv[1].endswith('/compile'):
  query=compile_pattern(body['pattern']); value={'query':query,'pattern':decompile_query(query)}
 else: value=JourneyWorkspace().run(body)
 print(json.dumps({'ok':True,'value':value}))
except ValueError as e: print(json.dumps({'ok':False,'value':{'error':str(e)}}))
`,url,JSON.stringify(body)],{cwd:path.resolve(__dirname,'..'),encoding:'utf8'}));}

const fetch=async(url,options={})=>{
 const body=options.body?JSON.parse(options.body):undefined;calls.push({url,body});
 if(url==='/api/journey')return{ok:true,json:async()=>payload.metadata};
 assert.ok(['/api/journey/run','/api/journey/compile'].includes(url));if(gate)await new Promise(resolve=>{gate.resolve=resolve;});
 if(fail){fail=false;return{ok:false,json:async()=>({error:'Evaluation unavailable'})};}
 if(body.question==='custom'||url.endsWith('/compile')){const result=backend(url,body);return{ok:result.ok,json:async()=>result.value};}
 const key=body.maximum_baseline===0?'empty':body.maximum_baseline===50?'filtered':body.reference_patient_id==='T04'?'unrankable':body.question==='sequential'?'sequential':body.budget==='0'?'original':'relaxed';
 const value=structuredClone(payload[key]);
 if(incomplete){incomplete=false;value.result.search_complete=false;}
 if(incompleteOption){incompleteOption=false;value.relaxation.evaluations[1].result.search_complete=false;}
 return{ok:true,json:async()=>value};
};
vm.runInContext(fs.readFileSync(path.join(__dirname,'journey.js'),'utf8'),vm.createContext({document,fetch,console,Error,encodeURIComponent}));
const inspect=patient=>el('rows').onclick({target:{closest:()=>({dataset:{patient}})}});
(async()=>{
 await new Promise(resolve=>setImmediate(resolve));
 assert.match(el('question').innerHTML,/value="custom"/);
 assert.equal(el('reference').value,'T03');assert.equal(el('budget').value,'0');assert.equal(el('question').value,'overlap');
 // Guided setup and failure: do not advance past an unsuccessful evaluation.
 await el('guide-next').onclick();assert.match(el('guide-next').textContent,/2\. Evaluate/);
 fail=true;await el('guide-next').onclick();assert.match(el('guide-next').textContent,/2\. Evaluate/);assert.equal(el('export').href,undefined);
 gate={};const pending=el('guide-next').onclick();assert.equal(el('reference').disabled,true);assert.equal(el('guide-next').disabled,true);
 const n=calls.length;await el('run').onclick();await el('guide-next').onclick();assert.equal(calls.length,n);gate.resolve();gate=null;await pending;
 assert.deepEqual(calls.at(-1).body,{reference_patient_id:'T03',top_k:1,maximum_baseline:null,question:'overlap',budget:'0'});
 assert.match(el('summary').textContent,/0 certain, 1 possible only, 1 incomparable/);
 assert.match(el('summary').textContent,/excluded by budget/);
 assert.match(el('ranking-rows').innerHTML,/<td>T02<\/td>.*?<td>Yes<\/td>/);
 assert.match(el('rows').innerHTML,/<td>T01<\/td>/);assert.doesNotMatch(el('rows').innerHTML,/<td>T03<\/td>/);
 assert.match(el('guide-next').textContent,/3\. Apply/);
 await el('guide-next').onclick();assert.equal(calls.at(-1).body.budget,'1.25');
 assert.match(el('summary').textContent,/1 additional certain history/);assert.match(el('rows').innerHTML,/Possible only<\/td><td>Certain/);
 assert.match(el('export').href,/^\/api\/journey\/export\//);
 await el('guide-next').onclick();assert.match(el('evidence').innerHTML,/T01/);assert.match(el('evidence').innerHTML,/counterexample/);assert.match(el('evidence').innerHTML,/This constraint holds/);assert.match(el('evidence').innerHTML,/This constraint does not hold/);assert.match(el('evidence').innerHTML,/Recorded endpoint bounds/);assert.match(el('evidence').innerHTML,/Clinical outcome/);assert.match(el('evidence').innerHTML,/not an efficacy outcome/);
 await el('guide-next').onclick();assert.match(el('evidence').innerHTML,/T04/);assert.match(el('evidence').innerHTML,/clock/i);assert.match(el('guide-progress').textContent,/Journey complete/);
 // Every input change removes reports, evidence and the download URL.
 for(const id of ['reference','top-k','maximum-baseline','question','budget']){
  el(id).oninput();assert.equal(el('rows').innerHTML,'');assert.equal(el('ranking-rows').innerHTML,'');assert.equal(el('export').href,undefined);assert.equal(el('query').textContent,'No executed query yet.');
  await el('run').onclick();
 }
 // Missing baselines are unresolved under filtering, not falsely excluded or evaluated.
 el('maximum-baseline').value='50';el('maximum-baseline').onchange();await el('run').onclick();
 assert.match(el('eligibility-rows').innerHTML,/<td>T04<\/td><td>Unresolved/);assert.doesNotMatch(el('rows').innerHTML,/<td>T04<\/td>/);
 el('maximum-baseline').value='0';await el('run').onclick();assert.equal(el('rows').innerHTML,'');assert.match(el('ranking-summary').textContent,/all 0 eligible/);assert.match(el('export').href,/\/export\//);
 el('maximum-baseline').value='';el('reference').value='T04';await el('run').onclick();assert.match(el('ranking-summary').textContent,/0 highlighted/);assert.doesNotMatch(el('ranking-rows').innerHTML,/<td>Yes<\/td>/);
 el('reference').value='T03';el('question').value='sequential';await el('run').onclick();assert.equal(calls.at(-1).body.question,'sequential');assert.match(el('query').textContent,/gap/);
 el('question').value='overlap';
 payload.relaxed.patients[0].evidence.treatment='<img src=x onerror=alert(1)>';
 payload.relaxed.patients[0].explanation.original.summary='<script>alert(1)</script>';
 await el('run').onclick();inspect(payload.relaxed.patients[0].patient_id);
 assert.match(el('evidence').innerHTML,/&lt;img/);assert.match(el('evidence').innerHTML,/&lt;script/);assert.doesNotMatch(el('evidence').innerHTML,/<img|<script/);
 for(const mode of ['error','incomplete','incomplete-option']){
  fail=mode==='error';incomplete=mode==='incomplete';incompleteOption=mode==='incomplete-option';await el('run').onclick();
  assert.equal(el('status').className,'error');assert.equal(el('rows').innerHTML,'');assert.equal(el('export').href,undefined);assert.equal(el('run').disabled,false);assert.equal(el('guide-next').disabled,false);
 }
 // Construct, edit and execute a three-event pattern through the actual Python engine.
 const edit=(container,row,field,value)=>el(container).onchange({target:{dataset:{row,field},value}});
 const remove=(container,row,action)=>el(container).onclick({target:{closest:()=>({dataset:{row,action}})}});
 el('question').value='custom';el('question').onchange();
 assert.equal(el('builder').className,'');assert.equal(el('budget').disabled,true);assert.equal(el('budget').value,'0');
 assert.match(el('builder-slots').innerHTML,/followup/);assert.match(el('builder-slots').innerHTML,/Recorded input segment/);
 await el('preview-query').onclick();assert.match(el('builder-query').textContent,/authored-configurable-trajectory-query/);
 const preview=JSON.parse(el('builder-query').textContent);assert.equal(preview.slots.length,3);assert.equal(preview.constraints.length,2);
 await el('run').onclick();assert.equal(calls.at(-1).body.budget,'0');assert.equal(calls.at(-1).body.pattern.slots.length,3);
 assert.deepEqual(JSON.parse(el('query').textContent).query,preview);assert.match(el('summary').textContent,/original query only/);assert.match(el('rows').innerHTML,/No option defined/);assert.match(el('export').href,/export/);
 inspect('T01');assert.match(el('evidence').innerHTML,/T01/);
 // Reject deletion of referenced slots; remove the dependent constraint first.
 remove('builder-slots','followup','remove-slot');assert.match(el('builder-message').textContent,/Remove timing constraints followup-gap/);
 remove('builder-constraints','followup-gap','remove-constraint');assert.equal(el('export').href,undefined);assert.equal(el('builder-query').textContent,'Validate the pattern to view its executable query.');
 remove('builder-slots','followup','remove-slot');await el('run').onclick();assert.equal(calls.at(-1).body.pattern.slots.length,2);assert.equal(calls.at(-1).body.pattern.constraints[0].right,'collection');
 remove('builder-slots','collection','remove-slot');assert.match(el('builder-message').textContent,/at least two/);
 await el('add-slot').onclick();await el('add-constraint').onclick();
 edit('builder-slots','event1','event_kind','specimen_collection');edit('builder-constraints','timing1','operator','gap');edit('builder-constraints','timing1','left','collection');edit('builder-constraints','timing1','right','event1');edit('builder-constraints','timing1','minimum_minutes','0');edit('builder-constraints','timing1','maximum_minutes','30.000000');
 await el('preview-query').onclick();await el('run').onclick();assert.equal(calls.at(-1).body.pattern.constraints[1].maximum_minutes,'30');assert.equal(calls.at(-1).body.pattern.slots[2].id,'event1');assert.match(el('export').href,/export/);
 // Pending validation locks generated fields and prevents a concurrent run or mutation.
 gate={};const validating=el('preview-query').onclick();assert.equal(el('slot-event1').disabled,true);assert.equal(el('constraint-timing1-maximum').disabled,true);const before=calls.length;
 edit('builder-slots','event1','event_kind','infusion');await el('run').onclick();await el('add-slot').onclick();assert.equal(calls.length,before);gate.resolve();gate=null;await validating;
 assert.equal(el('slot-event1').disabled,false);assert.equal(el('budget').disabled,true);assert.equal(el('export').href,undefined);
 // Selector changes genuinely reach the engine; no represented input records exist.
 edit('builder-slots','event1','event_kind','recorded_input_segment');await el('run').onclick();assert.equal(calls.at(-1).body.pattern.slots[2].event_kind,'recorded_input_segment');assert.match(el('summary').textContent,/0 certain, 0 possible only, 0 incomparable/);
 edit('builder-slots','event1','event_kind','specimen_collection');
 // Convert an Allen relation to duration, fail validation, then recover with valid bounds.
 edit('builder-constraints','during-infusion','operator','duration');edit('builder-constraints','during-infusion','minimum_minutes','0');await el('preview-query').onclick();assert.equal(el('status').className,'error');assert.equal(el('export').href,undefined);assert.equal(el('preview-query').disabled,false);
 edit('builder-constraints','during-infusion','minimum_minutes','10');edit('builder-constraints','during-infusion','maximum_minutes','10');await el('run').onclick();assert.match(el('export').href,/export/);assert.equal(calls.at(-1).body.pattern.constraints[0].slot,'infusion');assert.equal(calls.at(-1).body.pattern.constraints[0].left,undefined);
 // Changing a relationship and event reference invalidates evidence and retains names.
 edit('builder-constraints','during-infusion','operator','overlaps');assert.equal(el('export').href,undefined);await el('run').onclick();assert.equal(calls.at(-1).body.pattern.constraints[0].operator,'overlaps');assert.equal(calls.at(-1).body.pattern.constraints[0].left,'infusion');
 // Stable generated IDs are never reused after deletion.
 remove('builder-constraints','timing1','remove-constraint');remove('builder-slots','event1','remove-slot');await el('add-slot').onclick();await el('run').onclick();assert.equal(calls.at(-1).body.pattern.slots[2].id,'event2');
 await el('guide-next').onclick();assert.equal(el('question').value,'overlap');assert.equal(el('builder').className,'hidden');assert.equal(el('budget').disabled,false);
 console.log('Journey UI: configurable three-event builder, canonical preview, selector/relationship edits, stable references, validation recovery, pending lock, and guided flow passed.');
 console.log('Journey UI: guided flow, baseline/temporal separation, uncertainty, source evidence, filters, empty cohorts, escaping and failure recovery passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
