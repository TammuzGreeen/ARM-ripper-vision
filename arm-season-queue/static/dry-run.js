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
    const masters=s.masters||[];
    const run=s.dry_run;
    $('start').disabled=true;
    document.querySelectorAll('[data-action]').forEach(b=>b.disabled=true);
    $('dry-start').disabled=!s.dry_run_only||!!s.active_batch||(s.reservations||[]).some(r=>!['failed','cancelled'].includes(r.state));
    $('dry-run-status').textContent=run?`Separate record ${run.id} · ${run.status} · execution lock active`:s.dry_run_only?'No dry-run record. Start one, clear the view and calibrate empty.':'Blocked: deployed queue is not configured with DRY_RUN_ONLY.';
    const current=JSON.stringify([s.dry_run_only,run]);if(current===signature)return;signature=current;panel.replaceChildren();
    if(!s.dry_run_only){panel.append(node('p','Execution safety lock is missing. Normal batch start remains disabled until DRY_RUN_ONLY is enabled in this local deployment.','error'));return}
    if(!run){panel.append(node('p','After starting the record, clear the entire camera/tray view and click “View is empty” above. Then place the disc label-up in the open optical tray, centered inside the live camera preview; leave it there until recognition finishes.','muted'));return}
    panel.append(node('h3','Unrestricted current-disc dry run'));
    if(run.context){const ref=masters.find(m=>m.id===run.context.master),rd=ref?.discs?.find(d=>d.id===run.context.disc);panel.append(node('p',`Optional batch context only (never forces recognition): ${ref?.series||run.context.master} · Season ${ref?.season??'?'} · Disc ${rd?.number??run.context.disc}`,'muted'))}
    if(run.event)panel.append(node('p',`Fresh capture event UUID: ${run.event}`,'muted'));
    panel.append(node('p','This is a separate dry-run record. No production reservation/completion is written; all destinations are proposals only.','muted'));
    for(const attempt of run.capture_attempts||[])panel.append(node('p',`Prior camera attempt ${attempt.status}: ${attempt.reason}`,'error'));
    if(run.recognition){
      panel.append(node('h3','Fresh camera presentation · configured production recognition'));
      for(const [index,frame] of (run.recognition.result?.frames||[]).entries())if(frame.evidence){
        const a=document.createElement('a');a.href='/api/evidence/'+frame.evidence;a.target='_blank';
        const im=document.createElement('img');im.src=a.href;im.alt=`Retained camera capture frame ${index+1}`;im.className='dry-run-image';
        a.append(im);panel.append(node('small',`Camera image · frame ${index+1}`,'muted'),a)
      }
      const matches=run.recognition.matches||[];
      const reference=(run.recognition.result?.agreement||{}).reference_match||{};
      panel.append(node('p',matches.length===1?`Optional reference match: ${matches[0].series} · Season ${matches[0].season??'unknown'} · Disc ${matches[0].disc_number??'unknown'} · ${matches[0].edition||'edition unspecified'} · ${matches[0].mapping_confirmed?'confirmed technical mapping':'descriptive candidate; technical mapping unverified'}`:reference.status==='ambiguous'?`Optional reference match is ambiguous (${reference.candidate_count} candidates); recognition and scanning continue.`:`Recognition: ${run.recognition.status} · ${reference.status==='none'?'no matching masterlist entry (normal; continue with scan)':'reference status '+(reference.status||'unknown')}`,'badge'));
       output(panel,'Machine observation and model result (not a correction)',run.recognition.result);
       const agreement=run.recognition.result?.agreement||{},normalized=agreement.normalized_priority_fields||{};
       for(const [tag,model] of Object.entries(run.recognition.result?.runs||{})){
         const details=node('div','','dry-run-block');details.append(node('h4',`Model observation · ${tag}`));
         output(details,'Original transcription (retained unchanged)',model.transcription||'');
         output(details,'Normalized identifying fields',normalized[tag]||{});panel.append(details);
       }
       output(panel,'Matching decision',{
         status:agreement.status,reasons:agreement.reasons||[],disagreements:agreement.field_disagreements||[],
         selected:run.recognition.matches||[],candidate_proofs:agreement.validated_candidates||[],
         missing:agreement.missing_fields||[],conflicts:agreement.wrong_fields||[]
       });
      if(reference.candidates?.length)output(panel,'Reference candidates (source and trust remain visible)',reference.candidates.map(c=>{const m=masters.find(x=>x.id===c.master);return {master:c.master,disc:c.disc,approved:c.approved,mapping_confirmed:c.mapping_confirmed,series:m?.series,season:m?.season,provenance:m?.provenance}}));
      if(run.correction){panel.append(node('h3','Human correction · separate from camera evidence and masterlist'),node('pre',JSON.stringify(run.correction.fields,null,2)))}
      if(run.correction)panel.append(node('h3','Human correction · authoritative for this dry-run job only'),node('pre',JSON.stringify(run.correction,null,2)));
    } else panel.append(node('p','Waiting for fresh automatic three-frame capture and recognition. Keep the label visible in the live preview.'));
    if(run.recognition&&!run.scan){
      const box=node('div','','dry-run-block');box.append(node('h3','Close tray, then attach information-only scan'),node('p','The authorized local host helper runs MakeMKV’s info subcommand only, network-isolated. Never use ARM’s drive scan endpoint: the inspected implementation launches ARM processing.','muted'));
      const file=document.createElement('input');file.type='file';file.accept='.txt,text/plain';const area=document.createElement('textarea');area.placeholder='Choose the private MakeMKV info report or paste its contents.';file.onchange=async()=>{if(file.files[0])area.value=await file.files[0].text()};
      const checks=node('div','','buttons'), makeCheck=label=>{const l=node('label',label),c=document.createElement('input');c.type='checkbox';l.prepend(c);checks.append(l);return c},closed=makeCheck('Tray closed and medium ready'),same=makeCheck('Same insertion as camera capture'),unchanged=makeCheck('No removal/replacement during scan');
      box.append(file,area,checks,action('Attach scan and build proposed files',()=>{if(!closed.checked||!same.checked||!unchanged.checked)throw Error('Confirm tray/medium and same-insertion checks');return request(`/api/dry-run/${run.id}/scan`,{info:area.value,context:{device:'/dev/sr0',capture_event:run.event,tray_closed:true,medium_ready:true,same_insertion:true,media_changed_during_scan:false,operation:'makemkvcon-info-only',media_output_created:false}})}));panel.append(box);
    }
    if(run.scan){
      panel.append(node('h3','MakeMKV info-only scan · disc, layout and per-title streams'));
      output(panel,'Scan summary',run.scan.summary);
      const corr=run.correction||{},saved=corr.fields||{},assignments=corr.assignments||{};
      const box=node('div','','dry-run-block'),form=node('div','','dry-run-grid'),fields={};
      box.append(node('h3','Optional human confirmation and job-specific title assignments'),node('p','Camera observations, reference metadata, and these human-confirmed current-job values remain separate. Assignments are not written to a reusable masterlist. Without assignments, collision-free source-ID proposals remain provisional.','muted'));
      for(const [key,label] of Object.entries({media_type:'Media type (tv/movie/music/audiobook/unknown)',series:'Series or title',season:'Season (if applicable)',disc_number:'Disc number (if known)',episodes:'Observed episode number/range',titles:'Episode titles (if known)',edition:'Edition (if known)',notes:'What you verified from the physical disc'})){
        const current=saved[key]??'';const l=input(label,current);const el=l.querySelector('input');if(key==='notes')el.placeholder='Review note required';fields[key]=el;form.append(l)
      }
      box.append(form);
      const mapForm=node('div','','dry-run-grid'),mapFields={};
      for(const title of run.scan.summary.titles||[]){
        const tid=String(title.makemkv_id),value=assignments[tid]||{};
        const row=node('div','','dry-run-assignment');row.append(node('strong',`MakeMKV ID ${tid} · DVD title ${title.dvd_title??'unknown'} · ${title.duration||'duration unknown'}`));
        const ep=input('Library episode number',value.episode_number??'','number').querySelector('input');
        const name=input('Episode title / content name',value.episode_title||value.content_name||'').querySelector('input');
        mapFields[tid]={episode_number:ep,episode_title:name};row.append(group('Assignment',ep),group('Name',name));mapForm.append(row)
      }
      const payload=()=>{const out=Object.fromEntries(Object.entries(fields).map(([k,v])=>[k,v.value.trim()]).filter(([,v])=>v));out.assignments={};for(const [tid,row] of Object.entries(mapFields)){const a={};if(row.episode_number.value)a.episode_number=row.episode_number.value;if(row.episode_title.value)a.episode_title=row.episode_title.value;if(Object.keys(a).length)out.assignments[tid]=a}return out};
      box.append(mapForm,action('Save current-job human confirmation / assignments',()=>request(`/api/dry-run/${run.id}/correction`,payload())));panel.append(box)
    }
     if(run.plan){
       const p=run.plan;panel.append(node('h3','Final proposed files · WOULD BE CREATED · NOT RIP-READY',p.blockers.length?'error':'badge'));
       if(run.scan&&run.recognition&&!(run.recognition.matches||[]).length&&!run.assessment){
         panel.append(action('Re-evaluate retained responses (no new inference)',()=>request(`/api/dry-run/${run.id}/reevaluate-recognition`,{})));
       }
       panel.append(node('p',`Identity: ${p.identity?.series||'unresolved'} · ${p.identity?.media_type||'media type unknown'} · source: ${p.identity_source} · mapping: ${p.mapping_source} · ${p.plan_status} · ready_for_ripping=false`));
       if(p.reference_metadata)output(panel,'Reference metadata and technical trust',{
         trust:p.reference_metadata.trust,
         candidate:p.reference_metadata.master?{id:p.reference_metadata.master.id,series:p.reference_metadata.master.series,
           season:p.reference_metadata.master.season,edition:p.reference_metadata.master.edition_name,
           provenance:p.reference_metadata.master.provenance}:null
       });
       if(p.scan_resolution)output(panel,'Scan-corroborated reference selection',p.scan_resolution);
       if(p.unknown_selection)panel.append(node('p',p.unknown_selection,'muted'));
      if(p.blockers.length)panel.append(node('pre','Blockers:\n'+p.blockers.map(x=>'• '+x).join('\n'),'error'));
       for(const f of p.outputs){const card=node('div','','dry-run-output'),ep=f.episode,content=ep?`Episode ${ep.number}${ep.end?'-'+ep.end:''} · ${ep.title||'title unknown'}`:f.content_name||`Source title ID ${f.makemkv_id} · episode identity unresolved`;card.append(node('strong',`WOULD BE CREATED · MakeMKV ID ${f.makemkv_id} · DVD title ${f.dvd_title??'unknown'} · requested angle ${f.angle??'MakeMKV default'} · scanned angle count ${f.angle_count??'not reported'}${f.provisional?' · PROVISIONAL':''}`),node('p',`${p.identity?.series||'Unidentified disc'} · ${p.identity?.season?`Season ${p.identity.season} · `:''}${content}${f.version?' · '+f.version:''}`),node('p','Filename: '+f.destination.split('/').pop(),'dry-run-files'),node('p','Full local SSD destination: '+f.destination,'dry-run-files'),node('small',`Duration ${f.duration||'unknown'} · chapters ${f.chapters??'unknown'} · size ${f.size||'unknown'} · mapping evidence: ${f.mapping_evidence||'not recorded'}`,'muted'),node('p','Scanned inventory; no output stream is selected or written during this dry run. If executed, the verified persistent MakeMKV +sel:all policy would retain every listed audio/subtitle stream.'));
        output(card,'Video stream',f.video);output(card,'Audio streams',f.audio_streams);output(card,'Subtitle streams',f.subtitle_streams);panel.append(card)}
      for(const x of p.excluded_titles)panel.append(node('p',`Excluded MakeMKV ID ${x.makemkv_id} · DVD title ${x.dvd_title??'unknown'} · ${x.duration||'duration unknown'} · ${x.reason}`,'error'));
       panel.append(node('p','This assessment rates the supervised dry-run workflow only. It never clears mapping uncertainty or authorizes execution.','muted'));
      if(run.assessment)panel.append(node('p',`Assessment saved: ${run.assessment.outcome}${run.assessment.notes?' · '+run.assessment.notes:''}`,'badge'));
      else{const notes=document.createElement('input');notes.placeholder='Optional assessment notes';panel.append(node('h3','Assessment · neither choice can dispatch work'),notes,node('div','','buttons'));const actions=panel.lastChild;actions.append(action('Test successful',()=>request(`/api/dry-run/${run.id}/assessment`,{outcome:'test_successful',notes:notes.value})),action('Needs changes',()=>request(`/api/dry-run/${run.id}/assessment`,{outcome:'needs_changes',notes:notes.value})))}
   }
   for(const prior of s.dry_run_history||[]){
     const block=node('details','','dry-run-block'),summary=node('summary',`Previous disc · ${prior.id} · ${prior.status} · ${prior.assessment?.outcome||'not assessed'}`);
     block.append(summary,node('p',`Assessment: ${prior.assessment?.outcome||'none; no success was inferred'} · superseded ${prior.superseded_at?new Date(prior.superseded_at*1000).toLocaleString():'previous session'}`,'muted'));
     const frames=prior.recognition?.result?.frames||[];
     for(const [index,frame] of frames.entries())if(frame.evidence){const a=document.createElement('a');a.href='/api/evidence/'+frame.evidence;a.target='_blank';const im=document.createElement('img');im.src=a.href;im.alt=`Archived dry-run capture ${index+1}`;im.className='dry-run-image';a.append(im);block.append(node('small',`Archived image · frame ${index+1}`,'muted'),a)}
     if(prior.recognition)output(block,'Recognition and optional reference match',{status:prior.recognition.status,
       matches:prior.recognition.matches,reference_match:prior.recognition.result?.agreement?.reference_match,
       observations:prior.recognition.result?.agreement?.normalized_priority_fields});
     if(prior.scan)output(block,'Information-only scan',prior.scan.summary);
     if(prior.correction)output(block,'Job-specific human correction',prior.correction);
     if(prior.plan){output(block,'Selected/excluded titles and remaining uncertainties',{
       selected:prior.plan.selected_titles?.map(x=>({makemkv_id:x.makemkv_id,destination:x.destination,provisional:x.provisional})),
       excluded:prior.plan.excluded_titles,blockers:prior.plan.blockers});
       for(const file of prior.plan.outputs||[])block.append(node('p',`Would be created: ${file.destination} · ${file.provisional?'PROVISIONAL':'mapped'}`,'dry-run-files'))}
     panel.append(block)
   }
  }
  async function refresh(){try{const s=await request('/api/state');render(s)}catch(e){$('dry-run-status').textContent='Dry-run panel unavailable: '+e.message}}
  $('dry-start').onclick=async()=>{try{const r=await request('/api/dry-run',{});$('dry-run-status').textContent=`Unrestricted dry run ${r.id} started. Clear view, calibrate empty, then present any supported DVD.`;signature='';await refresh()}catch(e){$('dry-run-status').textContent=e.message}};
  refresh();setInterval(refresh,3000);
})();
