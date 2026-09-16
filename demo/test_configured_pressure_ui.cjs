// Verify the exact concatenated script used by the configured HTTP page.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path'),{spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'..');
const python=spawnSync(process.env.PYTHON||'python',['-c',`
import json,sys
sys.path.insert(0,'demo')
from mapped_pressure import MappedPressureService
s=MappedPressureService(config='examples/configured-pressure-service/config.json')
try: print(json.dumps(s.metadata()))
finally: s.close()
`],{cwd:root,encoding:'utf8'});
assert.equal(python.status,0,python.stderr);const metadata=JSON.parse(python.stdout);
const html=fs.readFileSync(path.join(__dirname,'pressure.html'),'utf8');
const script=fs.readFileSync(path.join(__dirname,'pressure.js'),'utf8')+'\n'+fs.readFileSync(path.join(__dirname,'configured_pressure.js'),'utf8');
const nodes=new Map();for(const m of html.matchAll(/id="([^"]+)"/g))nodes.set(m[1],{innerHTML:'',textContent:'',value:'',hidden:false,disabled:false,setAttribute(){},removeAttribute(){}});
let fetches=0,posted;
const config=structuredClone(metadata);config.source_label='<supplied records>';config.strata.configured='<reviewed item>';
const context=vm.createContext({document:{getElementById:id=>{assert(nodes.has(id),id);return nodes.get(id);},querySelectorAll:()=>[]},setTimeout:()=>1,fetch:async(url,options={})=>{
  if(url.endsWith('/config')){fetches++;return {ok:true,json:async()=>config};}
  if(options.method==='POST')posted=JSON.parse(options.body);
  return {ok:true,json:async()=>({id:'a'.repeat(32),status:'RUNNING',progress:{stage:'Preparing'}})};
}});
vm.runInContext(script,context);
(async()=>{
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(fetches,1,'one automatic configured initialization');
  assert.equal(nodes.get('source').textContent,'<supplied records>');
  assert(nodes.get('stratum').innerHTML.includes('&lt;reviewed item&gt;'));
  assert.equal(nodes.get('stratum').value,'configured');
  assert.equal(nodes.get('treatment-label').textContent,metadata.treatment_label);
  assert.equal(nodes.get('baseline').max,15);assert.equal(nodes.get('followup').max,60);
  assert.equal(Number(nodes.get('baseline').value),15);assert.equal(Number(nodes.get('followup').value),60);
  nodes.get('baseline').value='10';nodes.get('followup').value='30';nodes.get('threshold').value='59';
  await vm.runInContext('runQuery()',context);
  assert.deepEqual(posted,{stratum:'configured',controls:{threshold:'59',baseline_minutes:10,followup_minutes:30}});
  vm.runInContext('setBusy(false);restore()',context);assert.equal(Number(nodes.get('baseline').value),15);
  console.log('Configured pressure UI: actual metadata, one initialization, escaped labels, request defaults/limits and query body passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
