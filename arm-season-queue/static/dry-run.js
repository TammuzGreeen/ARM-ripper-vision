(()=>{
  const $=id=>document.getElementById(id), panel=$('dry-run-review');
  let signature='';
  const node=(tag,value='',cls='')=>{const e=document.createElement(tag);e.textContent=value;e.className=cls;return e};
  async function request(path,body){
    const r=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'X-Queue-Request':'1','Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
    const result=await r.json();if(!r.ok)throw Error(result.detail||'Dry-run request failed');return result;
  }
  function choice(select,items,selected){const value=selected||select.value;select.replaceChildren(...items.map(([id,label])=>{const o=node('option',label);o.value=id;return o}));if(items.some(x=>x[0]===value))select.value=value;}
  function action(label,callback,disabled=false){const b=node('button',label);b.disabled=disabled;b.onclick=async()=>{try{$('dry-run-status').textContent='Working…';await callback();await refresh()}catch(e){$('dry-run-status').textContent=e.message}};return b}
  function group(label,control){const l=node('label',label);l.append(control);return l}
  function input(label,value='',type='text'){const i=document.createElement('input');i.type=type;i.value=value;return group(label,i);}
  function output(parent,label,value){parent.append(node('strong',label),node('pre',typeof value==='string'?value:JSON.stringify(value,null,2)));}
  function render(s){
    const masters=(s.masters||[]).filter(m=>m.approved), masterSel=$('dry-master'), discSel=$('dry-disc');
    const mOptions=masters.map(m=>[m.id,`${m.series} · Season ${m.season} · ${m.edition_name||m.edition||'edition unspecified'}`]);
    if(masterSel.dataset.signature!==JSON.stringify(mOptions)){choice(masterSel,mOptions);masterSel.dataset.signature=JSON.stringify(mOptions)}
    const master=masters.find(m=>m.id===masterSel.value)||masters[0];
    const dOptions=(master?.discs||[]).map(d=>[d.id,`Disc ${d.number}`]);
    if(discSel.dataset.signature!==JSON.stringify(dOptions)){choice(discSel,dOptions);discSel.dataset.signature=JSON.stringify(dOptions)}
    const disc=master?.discs.find(d=>d.id===discSel.value)||master?.discs[0];
    const run=s.dry_run;
    $('start').disabled=true;
    document.querySelectorAll('[data-action]').forEach(b=>b.disabled=true);
    $('dry-start').disabled=!s.dry_run_only||!master||!disc||!!s.active_batch||(s.reservations||[]).some(r=>!['failed','cancelled'].includes(r.state));
    $('dry-run-status').textContent=run?`Separate record ${run.id} · ${run.status} · execution lock active`:s.dry_run_only?'No dry-run record. Start one, clear the view and calibrate empty.':'Blocked: deployed queue is not configured with DRY_RUN_ONLY.';
    const current=JSON.stringify([s.dry_run_only,run,master?.id,disc?.id]);if(current===signature)return;signature=current;panel.replaceChildren();
    if(!s.dry_run_only){panel.append(node('p','Execution safety lock is missing. Normal batch start remains disabled until DRY_RUN_ONLY is enabled in this local deployment.','error'));return}
    if(!run){panel.append(node('p','After starting the record, clear the entire camera/tray view and click “View is empty” above. Then place the disc label-up in the open optical tray, centered inside the live camera preview; leave it there until recognition finishes.','muted'));return}
    panel.append(node('h3',`${master?.series||run.master} · Season ${master?.season??'?'} · Disc ${disc?.number??run.disc}`));
    if(run.event)panel.append(node('p',`Fresh capture event UUID: ${run.event}`,'muted'));
    panel.append(node('p','This is a separate dry-run record. No production reservation/completion is written; all destinations are proposals only.','muted'));
    for(const attempt of run.capture_attempts||[])panel.append(node('p',`Prior camera attempt ${attempt.status}: ${attempt.reason}`,'error'));
    if(run.recognition){
      panel.append(node('h3','Fresh camera presentation · configured production recognition'));
      const evidence=run.recognition.result?.frames?.[0]?.evidence;
      if(evidence){const a=document.createElement('a');a.href='/api/evidence/'+evidence;a.target='_blank';const im=document.createElement('img');im.src=a.href;im.alt='Retained camera capture';im.className='dry-run-image';a.append(im);panel.append(a)}
      const matches=run.recognition.matches||[];
      panel.append(node('p',matches.length===1?`Camera match: ${matches[0].series} · Season ${matches[0].season} · Disc ${matches[0].disc_number} · ${matches[0].edition}`:`Camera outcome: ${run.recognition.status} · no unique approved match`,'badge'));
      output(panel,'Machine observation and model result (not a correction)',run.recognition.result);
      panel.append(node('p',`Comparison target: approved ${master?.series} · Season ${master?.season} · Disc ${disc?.number} · ${master?.edition_name||master?.edition}`,'muted'));
      if(run.correction){panel.append(node('h3','Human correction · separate from camera evidence and masterlist'),node('pre',JSON.stringify(run.correction.fields,null,2)))}
      else{
        const box=node('div','','dry-run-block'),form=node('div','','dry-run-grid'),values={series:master?.series||'',season:master?.season??'',disc_number:disc?.number??'',episodes:disc?.episodes?.map(e=>e.printed).join(', ')||'',titles:disc?.episodes?.map(e=>e.title).filter(Boolean).join(', ')||'',edition:master?.edition_name||master?.edition||'',notes:''},fields={};
        box.append(node('h3','Optional authoritative human correction'),node('p','This can correct identity only. It cannot change approved mappings, title inventories, scan evidence, or the plan’s readiness checks.','muted'));
        for(const [key,label] of Object.entries({series:'Series',season:'Season',disc_number:'Disc number',episodes:'Printed episode numbers/range',titles:'Episode titles',edition:'Edition',notes:'What you verified from the image'})){const l=input(label,values[key]);const el=l.querySelector('input');if(key==='notes')el.placeholder='Review note required';fields[key]=el;form.append(l)}
        box.append(form,action('Save authoritative human correction',()=>request(`/api/dry-run/${run.id}/correction`,Object.fromEntries(Object.entries(fields).map(([k,v])=>[k,v.value.trim()]).filter(([,v])=>v)))));panel.append(box);
      }
    } else panel.append(node('p','Waiting for fresh automatic three-frame capture and recognition. Keep the label visible in the live preview.'));
    if(run.recognition&&!run.scan){
      const box=node('div','','dry-run-block');box.append(node('h3','Close tray, then attach information-only scan'),node('p','The authorized local host helper runs MakeMKV’s info subcommand only, network-isolated. Never use ARM’s drive scan endpoint: the inspected implementation launches ARM processing.','muted'));
      const file=document.createElement('input');file.type='file';file.accept='.txt,text/plain';const area=document.createElement('textarea');area.placeholder='Choose the private MakeMKV info report or paste its contents.';file.onchange=async()=>{if(file.files[0])area.value=await file.files[0].text()};
      const checks=node('div','','buttons'), makeCheck=label=>{const l=node('label',label),c=document.createElement('input');c.type='checkbox';l.prepend(c);checks.append(l);return c},closed=makeCheck('Tray closed and medium ready'),same=makeCheck('Same insertion as camera capture'),unchanged=makeCheck('No removal/replacement during scan');
      box.append(file,area,checks,action('Attach scan and build proposed files',()=>{if(!closed.checked||!same.checked||!unchanged.checked)throw Error('Confirm tray/medium and same-insertion checks');return request(`/api/dry-run/${run.id}/scan`,{info:area.value,context:{device:'/dev/sr0',capture_event:run.event,tray_closed:true,medium_ready:true,same_insertion:true,media_changed_during_scan:false,operation:'makemkvcon-info-only',media_output_created:false}})}));panel.append(box);
    }
    if(run.scan){panel.append(node('h3','MakeMKV info-only scan · disc, layout and per-title streams'));output(panel,'Scan summary',run.scan.summary)}
    if(run.plan){
      const p=run.plan;panel.append(node('h3','Final proposed files · WOULD BE CREATED · NOT RIP-READY',p.blockers.length?'error':'badge'));
      panel.append(node('p',`Identity source: ${p.identity_source} · ${p.plan_status} · ready_for_ripping=false`));
      if(p.blockers.length)panel.append(node('pre','Blockers:\n'+p.blockers.map(x=>'• '+x).join('\n'),'error'));
      for(const f of p.outputs){const card=node('div','','dry-run-output'),ep=f.episode,content=ep?`Episode ${ep.number}${ep.end?'-'+ep.end:''} · ${ep.title||'title unknown'}`:`Extra · ${f.extra?.title||f.content_name}`;card.append(node('strong',`WOULD BE CREATED · MakeMKV ID ${f.makemkv_id} · DVD title ${f.dvd_title??'unknown'} · requested angle ${f.angle??'MakeMKV default'} · scanned angle count ${f.angle_count??'not reported'}`),node('p',`${master?.series} · Season ${master?.season} · ${content}${f.version?' · '+f.version:''}`),node('p','Filename: '+f.destination.split('/').pop(),'dry-run-files'),node('p','Full local SSD destination: '+f.destination,'dry-run-files'),node('small',`Duration ${f.duration||'unknown'} · chapters ${f.chapters??'unknown'} · size ${f.size||'unknown'} · mapping evidence: ${f.mapping_evidence||'not recorded'}`,'muted'),node('p','Scanned inventory; no output stream is selected or written during this dry run. If executed, the verified persistent MakeMKV +sel:all policy would retain every listed audio/subtitle stream.'));
        output(card,'Video stream',f.video);output(card,'Audio streams',f.audio_streams);output(card,'Subtitle streams',f.subtitle_streams);panel.append(card)}
      for(const x of p.excluded_titles)panel.append(node('p',`Excluded MakeMKV ID ${x.makemkv_id} · DVD title ${x.dvd_title??'unknown'} · ${x.duration||'duration unknown'} · ${x.reason}`,'error'));
      if(run.assessment)panel.append(node('p',`Assessment saved: ${run.assessment.outcome}${run.assessment.notes?' · '+run.assessment.notes:''}`,'badge'));
      else{const notes=document.createElement('input');notes.placeholder='Optional assessment notes';panel.append(node('h3','Assessment · neither choice can dispatch work'),notes,node('div','','buttons'));const actions=panel.lastChild;actions.append(action('Test successful',()=>request(`/api/dry-run/${run.id}/assessment`,{outcome:'test_successful',notes:notes.value}),!!p.blockers.length),action('Needs changes',()=>request(`/api/dry-run/${run.id}/assessment`,{outcome:'needs_changes',notes:notes.value})))}
    }
  }
  async function refresh(){try{const s=await request('/api/state');render(s)}catch(e){$('dry-run-status').textContent='Dry-run panel unavailable: '+e.message}}
  $('dry-master').onchange=()=>{const m=JSON.parse($('dry-master').dataset.signature||'[]').find(x=>x[0]===$('dry-master').value);if(m){$('dry-disc').dataset.signature='';refresh()}};
  $('dry-start').onclick=async()=>{try{const r=await request('/api/dry-run',{master:$('dry-master').value,disc:$('dry-disc').value});$('dry-run-status').textContent=`Dry run ${r.id} started. Clear view, calibrate empty, then present.`;signature='';await refresh()}catch(e){$('dry-run-status').textContent=e.message}};
  refresh();setInterval(refresh,3000);
})();
