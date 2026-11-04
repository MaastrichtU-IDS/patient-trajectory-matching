// Appended to the shared pressure script for the configured route. This initializer
// replaces the fixed-fixture initializer; query and inspection rendering are shared.
async function initialize(){
  try{
    config=await api('/api/pressure/config');
    $('source').textContent=config.source_label;
    $('setup').hidden=config.configured;
    $('stratum').innerHTML=Object.entries(config.strata).map(([id,label])=>`<option value="${esc(id)}">${esc(label)}</option>`).join('');
    $('stratum').value=Object.keys(config.strata)[0];
    $('treatment-label').textContent=config.treatment_label;
    for(const [id,key] of [['baseline','baseline_minutes'],['followup','followup_minutes']]){
      $(id).min=config.limits[key][0];$(id).max=config.limits[key][1];
    }
    setBusy(false);restore();
  }catch(e){error(e.message);setBusy(false);}
}
