/* Run the real temporal UI script with real engine-produced reports. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const payload = JSON.parse(execFileSync(process.env.PYTHON || 'python', ['-c', `
import json
from app.temporal import TemporalWorkspace
w=TemporalWorkspace()
print(json.dumps({'metadata':w.metadata(),'original':w.run('0'),'widened':w.run('1.25')}))
`], {cwd:path.resolve(__dirname,'..'),encoding:'utf8'}));
class Element {
  constructor(){this.value='';this.innerHTML='';this.textContent='';this.className='';this.disabled=false;}
  removeAttribute(name){delete this[name];}
}
const elements = new Map();
const document = {getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id);}};
const el = id => document.getElementById(id);
const calls = []; let failNext = false, incompleteNext = false, release;
const fetch = async (url,options={}) => {
  const body=options.body?JSON.parse(options.body):undefined; calls.push({url,body});
  if(url==='/api/temporal')return{ok:true,json:async()=>payload.metadata};
  assert.equal(url,'/api/temporal/run');
  if(release) await new Promise(resolve=>{release.resolve=resolve;});
  if(failNext){failNext=false;return{ok:false,json:async()=>({error:'Evaluation blocked'})};}
  const result=structuredClone(body.budget==='0'?payload.original:payload.widened);
  if(incompleteNext){incompleteNext=false;result.result.search_complete=false;}
  return{ok:true,json:async()=>result};
};
vm.runInContext(fs.readFileSync(path.join(__dirname,'temporal.js'),'utf8'),vm.createContext({document,fetch,console,encodeURIComponent,Error}));
(async()=>{
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(el('run').disabled,false);
  assert.equal(el('temporal-budget').value,'0');
  assert.match(el('limits').textContent,/4 histories, 8 events/);
  release={};
  const pending=el('run').onclick();
  assert.equal(el('temporal-budget').disabled,true);
  assert.equal(el('run').disabled,true);
  const count=calls.length;
  await el('run').onclick(); assert.equal(calls.length,count);
  release.resolve(); release=null; await pending;
  for(const status of ['Certain','Possible only','No recorded match','Incomparable'])assert.ok(el('temporal-rows').innerHTML.includes(status));
  assert.match(el('summary').textContent,/1 certain; 1 possible only/);
  el('temporal-rows').onclick({target:{closest:()=>({dataset:{patient:'T02'}})}});
  assert.match(el('temporal-evidence').innerHTML,/counterexample/);
  assert.match(el('temporal-evidence').innerHTML,/possible_witness/);
  assert.match(el('temporal-evidence').innerHTML,/Satisfying timeline/);
  assert.match(el('temporal-evidence').innerHTML,/Counterexample to certainty/);
  assert.match(el('temporal-evidence').innerHTML,/Gap:/);
  assert.equal(el('temporal-export').href,'/api/temporal/export/'+payload.original.report_id);
  el('temporal-budget').value='1.25'; el('temporal-budget').onchange();
  assert.equal(el('temporal-rows').innerHTML,'');
  assert.match(el('temporal-export').className,/hidden/);
  assert.equal(el('temporal-export').href,undefined);
  // Source content is escaped when evidence is displayed.
  payload.widened.inputs.source.events[0].source_key='<img src=x onerror=alert(1)>';
  await el('run').onclick();
  assert.match(el('summary').textContent,/Added by widening: T02/);
  assert.match(el('temporal-rows').innerHTML,/cost 1.25/);
  el('temporal-rows').onclick({target:{closest:()=>({dataset:{patient:'T01'}})}});
  assert.match(el('temporal-evidence').innerHTML,/&lt;img/);
  assert.doesNotMatch(el('temporal-evidence').innerHTML,/<img/);
  for(const mode of ['error','incomplete']){
    failNext=mode==='error'; incompleteNext=mode==='incomplete';
    await el('run').onclick();
    assert.equal(el('status').className,'error');
    assert.equal(el('temporal-rows').innerHTML,'');
    assert.match(el('temporal-export').className,/hidden/);
    assert.equal(el('run').disabled,false);
  }
  console.log('Temporal UI: classifications, fixed budgets, certificates, escaping, stale-result clearing and blocked execution passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
