// DOM state checks, not a browser/layout test. No browser or network dependency.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, 'Guided_Cohort_Demo.html'), 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('\n');

function harness(protocol) {
  const nodes = new Map(), buttons = new Map(), downloads = [];
  function node(id) {
    return {id, innerHTML:'', textContent:'', dataset:{}, disabled:false, hidden:false,
      attributes:{}, setAttribute(k,v){this.attributes[k]=v;},
      click(){return this.onclick?.();}, remove(){}, appendChild(){}};
  }
  [...html.matchAll(/\bid="([^"]+)"/g)].forEach(m => nodes.set(m[1], node(m[1])));
  const document = {
    body:{appendChild(){}},
    getElementById(id) {
      if (!nodes.has(id)) {
        assert([...nodes.values()].some(n => n.innerHTML.includes(`id="${id}"`)), 'Unknown element '+id);
        nodes.set(id, node(id));
      }
      return nodes.get(id);
    },
    querySelectorAll(selector) {
      const markup = html + [...nodes.values()].map(n=>n.innerHTML).join('');
      const found = new Map();
      for (const match of selector.matchAll(/\[data-([a-z]+)\]/g)) {
        const kind = match[1], regex = new RegExp(`data-${kind}="([^"]+)"`, 'g');
        for (const item of markup.matchAll(regex)) {
          const key = kind+':'+item[1];
          if (!buttons.has(key)) { const b = node(key); b.dataset[kind] = item[1]; buttons.set(key,b); }
          found.set(key, buttons.get(key));
        }
      }
      return [...found.values()];
    },
    createElement() {const link=node('download');link.click=()=>downloads.push(link.download);return link;},
  };
  const context = vm.createContext({document, location:{protocol}, console, structuredClone, Blob,
    URL:{createObjectURL(){return 'blob:test';},revokeObjectURL(){}},
    setTimeout(){return 1;},setInterval(){return 1;},
    fetch:async url => {
      const budget=new URL(url,'http://localhost').searchParams.get('budget');
      const result=JSON.parse(vm.runInContext(`JSON.stringify(GUIDED.replays[${JSON.stringify(budget)}])`,context));
      result.execution_mode='live-python';
      if(url.includes('/export')) return {ok:true,json:async()=>({format:'guided-cohort-export-1',dataset:JSON.parse(vm.runInContext('JSON.stringify(dataset)',context)),evaluation:result})};
      return {ok:true,json:async()=>result};
    },
  });
  vm.runInContext(scripts,context);
  return {nodes, buttons, downloads, context, run:code=>vm.runInContext(code,context)};
}

(async()=>{
  for(const protocol of ['file:','http:']) {
    const h=harness(protocol);
    assert(h.nodes.get('history').innerHTML.includes('Patient P00'));
    for(const [step,count] of [[1,3],[2,5],[3,6],[4,6]]) {
      await h.run(`enterStep(${step})`);
      assert.equal(h.run('result.membership.included.length'),count);
      for(const pid of ['P00','P01','P02','P03','P04','P05','P06','P07','P08','P09','P10']) {
        h.run(`selectPatient('${pid}')`);
        for(const id of ['history','evidence','explanation']) {
          assert(!/undefined|NaN/.test(h.nodes.get(id).innerHTML), id+' '+pid);
        }
      }
    }
    h.run("selectPatient('P02')");
    assert(h.nodes.get('explanation').innerHTML.includes('DrugAChild ⊑ DrugA'));
    h.run("selectPatient('P07')");
    assert(h.nodes.get('explanation').innerHTML.includes('0.29'));
    h.run("selectPatient('P10')");
    assert(h.nodes.get('explanation').innerHTML.includes('incomplete source search'));
    h.run("selectPatient('P03')");
    assert(h.nodes.get('evidence').innerHTML.includes('8.0 mg/L'));
    assert(h.nodes.get('evidence').innerHTML.includes('0.80 mg/dL'));
    assert(h.nodes.get('evidence').innerHTML.includes('hasParticipant'));
    await h.run('exportCurrent()');
    assert.deepEqual(h.downloads,['trajectory-cohort-budget-2.json']);
    await h.run("enterStep(2, '0')");
    assert.equal(h.run('result.membership.included.length'),3);
    assert(h.nodes.get('narrative').textContent.includes('controls differ'));
    await h.run('restart()');
    assert.equal(h.run('result'),null);
    assert.equal(h.run('timerRunning'),false);
  }
  const h=harness('http:');
  await h.run('enterStep(1)');
  const previous=h.nodes.get('summary').innerHTML;
  h.context.fetch=async()=>({ok:false,json:async()=>({error:'Server unavailable'})});
  await h.run('enterStep(2)');
  assert.equal(h.run('step'),1);
  assert.equal(h.run('budget'),'0');
  assert.equal(h.nodes.get('summary').innerHTML,previous);
  assert.equal(h.nodes.get('error').hidden,false);
  assert(h.nodes.get('error').innerHTML.includes('no new cohort'));
  assert.equal(h.run('busy'),false);
  // A stale response cannot overwrite a newer query or a reset.
  const pending=[];
  h.context.fetch=url=>new Promise(resolve=>pending.push({url,resolve}));
  const first=h.run('enterStep(2)'), second=h.run('enterStep(3)');
  function resolveRequest(index,budget) {
    const value=JSON.parse(h.run(`JSON.stringify(GUIDED.replays['${budget}'])`));
    value.execution_mode='live-python';
    pending[index].resolve({ok:true,json:async()=>value});
  }
  resolveRequest(1,'2');await second;
  resolveRequest(0,'1');await first;
  assert.equal(h.run('step'),3);
  assert.equal(h.run('budget'),'2');
  assert.equal(h.run('result.membership.included.length'),6);
  const third=h.run('enterStep(1)');await h.run('restart()');
  resolveRequest(2,'0');await third;
  assert.equal(h.run('result'),null);
  assert.equal(h.run('step'),0);
  console.log('Guided UI: live and replay journeys, patient evidence, export, failures, stale responses and reset passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
