const assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path'), vm=require('node:vm');
const {execFileSync}=require('node:child_process');
const payload=JSON.parse(execFileSync(process.env.PYTHON||'python',['-c',`
import json
from app.interval_editor import IntervalEditor
w=IntervalEditor();m=w.metadata();c=m['default_controls'];a=w.run(c)
c={**c,'duration':{'minimum_minutes':'10','maximum_minutes':'10'},'minimum_overlap_minutes':'3'}
print(json.dumps({'metadata':m,'original':a,'changed':w.run(c)}))
`],{cwd:path.resolve(__dirname,'..'),encoding:'utf8'}));
class Element{constructor(){this.value='';this.innerHTML='';this.textContent='';this.disabled=false;this.className='';}removeAttribute(name){delete this[name];}}
const elements=new Map(),document={getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id);}}, el=id=>document.getElementById(id);
let fail=false,incomplete=false,gate=null;const calls=[];
const fetch=async(url,options={})=>{
 const body=options.body?JSON.parse(options.body):undefined;calls.push({url,body});
 if(url==='/api/editor')return{ok:true,json:async()=>payload.metadata};
 assert.equal(url,'/api/editor/run');if(gate)await new Promise(resolve=>{gate.resolve=resolve;});
 if(fail){fail=false;return{ok:false,json:async()=>({error:'Invalid minutes'})};}
 const value=structuredClone(body.minimum_overlap_minutes?payload.changed:payload.original);
 if(incomplete){incomplete=false;value.result.search_complete=false;}
 return{ok:true,json:async()=>value};
};
vm.runInContext(fs.readFileSync(path.join(__dirname,'editor.js'),'utf8'),vm.createContext({document,fetch,console,Error,encodeURIComponent}));
(async()=>{
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(el('relation').value,'contains');assert.equal(el('gap-min').disabled,true);
 gate={};const pending=el('run').onclick();assert.equal(el('fixture').disabled,true);
 const n=calls.length;await el('run').onclick();assert.equal(calls.length,n);gate.resolve();gate=null;await pending;
 assert.match(el('summary').textContent,/1 certain, 1 possible only/);
 assert.equal(calls.at(-1).body.gap,null);
 el('rows').onclick({target:{closest:()=>({dataset:{patient:'T02'}})}});
 assert.match(el('evidence').innerHTML,/counterexample/);
 el('duration-enabled').value='on';el('duration-enabled').onchange();
 assert.equal(el('duration-min').disabled,false);assert.equal(el('rows').innerHTML,'');assert.equal(el('export').href,undefined);
 el('duration-min').value='10';el('duration-max').value='10';el('overlap-enabled').value='on';el('overlap-enabled').onchange();el('overlap-min').value='3';
 await el('run').onclick();assert.deepEqual(calls.at(-1).body.duration,{minimum_minutes:'10',maximum_minutes:'10'});
 assert.match(el('query').textContent,/minimum_overlap/);assert.match(el('summary').textContent,/0 certain, 1 possible only/);
 el('overlap-min').value='4';el('overlap-min').oninput();assert.equal(el('query').textContent,'No executed query yet.');assert.equal(el('export').href,undefined);
 payload.changed.source.events[0].source_key='<img src=x onerror=alert(1)>';
 await el('run').onclick();el('rows').onclick({target:{closest:()=>({dataset:{patient:'T01'}})}});
 assert.match(el('evidence').innerHTML,/&lt;img/);assert.doesNotMatch(el('evidence').innerHTML,/<img/);
 el('relation').value='gap';el('relation').onchange();assert.equal(el('gap-min').disabled,false);
 el('gap-min').value='-7';el('gap-max').value='-6';await el('run').onclick();assert.deepEqual(calls.at(-1).body.gap,{minimum_minutes:'-7',maximum_minutes:'-6'});
 for(const mode of ['error','incomplete']){fail=mode==='error';incomplete=mode==='incomplete';await el('run').onclick();assert.equal(el('status').className,'error');assert.equal(el('rows').innerHTML,'');assert.equal(el('export').href,undefined);assert.equal(el('run').disabled,false);}
 console.log('Interval editor UI: controls, conjunctions, editing, certificates, escaping and failure states passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
