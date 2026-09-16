// DOM-state verification with actual synthetic session responses; not browser/layout verification.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path'),{spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'..');
const python=spawnSync(process.env.PYTHON||'python',['-c',`
import json,sys
from pathlib import Path
sys.path.insert(0,'demo')
import pressure
from patterns.reviewed_pressure_session import Session
request=json.loads(Path('examples/indexed-source-windows/synthetic-request.json').read_text())
declaration=json.loads(Path('demo/pressure-synthetic-review.json').read_text())
s=Session(Path('examples/source-mixed-query'),request,declaration)
service=pressure.PressureService(synthetic=True)
payload={'config':service.metadata(),'jobs':{},'details':{}}
for threshold,letter in [('65','a'),('59','b')]:
 options={'threshold':threshold,'baseline_minutes':30,'followup_minutes':120}
 result=s.execute(options)
 summary={k:v for k,v in result.items() if k!='details'}
 summary.update(stratum='synthetic',source_mode='synthetic')
 jid=letter*32
 payload['jobs'][threshold]={'id':jid,'status':'COMPLETED','request':{'stratum':'synthetic','controls':options},'summary':summary,'timing':{'prepare_seconds':0}}
 payload['details'][jid]={t:s.inspect(result,t) for t in result['details']}
print(json.dumps(payload))
service.close()
`],{cwd:root,encoding:'utf8',maxBuffer:8*1024*1024});
assert.equal(python.status,0,python.stderr);const data=JSON.parse(python.stdout);
const html=fs.readFileSync(path.join(__dirname,'pressure.html'),'utf8'),script=fs.readFileSync(path.join(__dirname,'pressure.js'),'utf8');
function harness(){const nodes=new Map(),buttons=new Map(),timers=[];function node(id){return {id,innerHTML:'',textContent:'',value:'',hidden:false,disabled:false,dataset:{},attributes:{},setAttribute(k,v){this.attributes[k]=v;},removeAttribute(k){delete this.attributes[k];}};}
for(const m of html.matchAll(/id="([^"]+)"/g))nodes.set(m[1],node(m[1]));nodes.get('threshold').value='65';nodes.get('baseline').value='30';nodes.get('followup').value='120';
let chosen='65';const context=vm.createContext({console,document:{getElementById:id=>{assert(nodes.has(id),id);return nodes.get(id);},querySelectorAll:()=>{const markup=[...nodes.values()].map(n=>n.innerHTML).join('');const result=[];for(const m of markup.matchAll(/data-anchor="([^"]+)"/g)){if(!buttons.has(m[1])){const b=node(m[1]);b.dataset.anchor=m[1];buttons.set(m[1],b);}result.push(buttons.get(m[1]));}return result;}},setTimeout:fn=>{timers.push(fn);return timers.length;},fetch:async(url,options={})=>{let body;if(url.endsWith('/config'))body=data.config;else if(options.method==='POST'){chosen=JSON.parse(options.body).controls.threshold;body={id:data.jobs[chosen].id,status:'RUNNING'};}else {const bits=url.split('/');body=bits.includes('anchors')?data.details[bits[4]][bits[6]]:Object.values(data.jobs).find(j=>j.id===bits[4]);}return {ok:true,json:async()=>structuredClone(body)};}});
vm.runInContext(script,context);return {nodes,buttons,timers,context,run:code=>vm.runInContext(code,context)};}
(async()=>{const h=harness();await h.run('initialize()');assert.equal(h.nodes.get('source').textContent,'Authored synthetic records');await h.run('runQuery()');assert.equal(h.run('current.metrics.patients'),3);assert(h.nodes.get('summary').innerHTML.includes('3 / 3 anchors agree with SQL'));
for(const [token,d] of Object.entries(data.details['a'.repeat(32)])){await h.run(`inspect('${token}')`);const view=h.nodes.get('inspector').innerHTML;assert(!/undefined|NaN/.test(view));assert(view.includes('hasParticipant'));if(d.bindings.some(b=>b[4]===null))assert(view.includes('remains eligible'));}
h.run("renderResult({prepare_seconds:0.1,execute_seconds:0,total_seconds:0.2},{mode:'cached_complete_result'})");assert(h.nodes.get('summary').innerHTML.includes('Reused a completed query; source and review rechecked.'));assert(h.nodes.get('summary').innerHTML.includes('query execution 0 s'));
h.nodes.get('patient').value='4';h.run('selectPatient()');assert(h.nodes.get('inspector').innerHTML.includes('no admitted treatment anchor'));
h.nodes.get('threshold').value='59';await h.run('runQuery()');assert.equal(h.run('current.metrics.patients'),1);const no=Object.entries(data.details['b'.repeat(32)]).find(([,d])=>!d.bindings.length)[0];await h.run(`inspect('${no}')`);assert(h.nodes.get('inspector').innerHTML.includes('No eligible selected baseline'));
const wrongUnit=structuredClone(Object.values(data.details['b'.repeat(32)])[0]);wrongUnit.measurements=wrongUnit.measurements.map(m=>({...m,unit:'kPa'}));h.context.wrongUnit=wrongUnit;assert(!h.run('timeline(wrongUnit)').includes('<circle'));
const previous=h.nodes.get('summary').innerHTML;h.context.fetch=async()=>({ok:false,json:async()=>({error:'Source changed'})});await h.run('runQuery()');assert.equal(h.nodes.get('summary').innerHTML,previous);assert(h.nodes.get('error').textContent.includes('previous completed result'));assert.equal(h.run('busy'),false);
const token=Object.keys(data.details['b'.repeat(32)])[0];h.context.fetch=async()=>({ok:true,json:async()=>({...structuredClone(data.details['b'.repeat(32)][token]),query_context_id:'wrong'})});await h.run(`inspect('${token}')`);assert(h.nodes.get('inspector').innerHTML.includes('different query'));
let release;h.context.fetch=()=>new Promise(resolve=>{release=resolve;});const pending=h.run(`inspect('${token}')`);h.run('selectStay()');release({ok:true,json:async()=>structuredClone(data.details['b'.repeat(32)][token])});await pending;assert(!h.nodes.get('inspector').innerHTML.includes('SQL agreement verified'));
const fresh=harness();await fresh.run('initialize()');fresh.context.fetch=async()=>({ok:true,json:async()=>({id:'a'.repeat(32),status:'RUNNING',progress:{stage:'Matching',completed:2,total:3}})});await fresh.run('runQuery()');assert.equal(fresh.run('current'),null);assert.equal(fresh.run('busy'),true);assert(fresh.nodes.get('progress-text').textContent.includes('No partial cohort'));assert.equal(fresh.timers.length,1);
console.log('Pressure UI states: completed queries, evidence, exclusions, empty stays, failures, progress and stale responses passed.');})().catch(e=>{console.error(e);process.exitCode=1;});
