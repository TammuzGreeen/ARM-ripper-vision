(() => {
  const root=document.getElementById('beta-review-list');
  const filter=document.getElementById('beta-review-filter');
  if(!root||!filter)return;
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function api(url,body){
    const options=body?{method:'POST',headers:{'Content-Type':'application/json','X-Queue-Request':'1'},body:JSON.stringify(body)}:{};
    const response=await fetch(url,options);if(!response.ok)throw new Error((await response.json()).detail||response.statusText);return response.json();
  }
  function render(item){
    const review=item.local_review||{},rec=review.recognition||item.recognition||{},result=rec.result||{},runs=result.runs||{},event=item.event;
    const outputs=review.outputs||[];const scan=review.scan||item.mapping||[];
    const modelBlocks=Object.entries(runs).map(([tag,data],i)=>`<details><summary>${esc(tag)} · ${data.usable?'usable':'unusable'}</summary><pre>${esc(JSON.stringify(data.transcription??data.fields??data,null,2))}</pre><img class="beta-evidence" src="/api/evidence/${encodeURIComponent(event)}/agreement-input-${i}.jpg" alt="Exact model input image"></details>`).join('');
    const originals=[0,1,2].map(i=>`<img class="beta-evidence" src="/api/evidence/${encodeURIComponent(event)}/original-${i}.jpg" alt="Original full-resolution capture ${i+1}">`).join('');
    const identity=review.identity_fields||{};
    const identityForm=`<div class="beta-identity"><label>Series / title <input data-identity="series" value="${esc(identity.series_display??identity.series??'')}"></label><label>Season <input data-identity="season" type="number" min="0" max="99" value="${esc(identity.season??'')}"></label></div>`;
    const titleRows=outputs.map(o=>{
      const id=String(o.makemkv_id),ep=o.corrections?.at(-1)||{};
      return `<fieldset class="beta-title" data-source="${esc(id)}"><legend>Source title ${esc(id)} · ${esc(o.validation?.decode||'not validated')}</legend>
       <p><strong>Actual:</strong> <code>${esc(o.host_path||o.actual_path)}</code> · ${esc(o.bytes)} bytes · SHA-256 ${esc(o.sha256)}</p>
       <label>Episode number <input data-field="episode_number" type="number" min="1" value="${esc(ep.episode_number??o.episode?.number??'')}" data-original="${esc(ep.episode_number??o.episode?.number??'')}"></label>
       <label>Episode range <input data-field="episode_range" placeholder="01-02" value="${esc(ep.episode_range??'')}" data-original="${esc(ep.episode_range??'')}"></label>
       <label>Episode name <input data-field="episode_name" value="${esc(ep.episode_name??o.episode?.title??'')}" data-original="${esc(ep.episode_name??o.episode?.title??'')}"></label>
       <label>Version <input data-field="version" value="${esc(ep.version??o.version??'')}" data-original="${esc(ep.version??o.version??'')}"></label>
       <label>Filename <input data-field="filename" data-original="${esc(o.actual_filename)}" value="${esc(o.actual_filename)}"></label>
       <p class="muted">Source ID ${esc(o.makemkv_id)}; mapping: ${esc(o.mapping_evidence||'unavailable')}; provisional: ${o.provisional?'yes':'no'}</p></fieldset>`;
    }).join('');
    return `<article class="beta-review-card"><h3>Job ${esc(item.job)} · ${esc(item.state)} · ${esc(review.status||'')}</h3>
      <p>Disc: ${esc(review.observed_label||item.observed_label||'unlabelled')} · Insertion: ${esc(item.event)} · Mapping: ${esc(review.mapping_source||item.workflow||'unknown')}</p>
      ${item.error?`<p class="error">Job error: ${esc(item.error)}</p>`:''}
      ${(review.warnings||[]).length?`<aside class="warning"><strong>Warnings / assumptions</strong><ul>${review.warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul></aside>`:''}
      ${review.arm_progress?`<details><summary>Last ARM progress update · ${esc(review.arm_progress.status)} · ${esc(review.arm_progress.updated_at)}</summary><pre>${esc(JSON.stringify(review.arm_progress.tracks,null,2))}</pre></details>`:''}
      ${review.tray_action?`<p class="${review.tray_action.status==='failed'||review.tray_action.status==='not_confirmed'?'warning':''}">Tray: ARM AUTO_EJECT · ${esc(review.tray_action.status)}${review.tray_action.error?` · ${esc(review.tray_action.error)}`:''}${review.tray_action.physical_open_verified?' · physically verified':' · physical state not verified'}</p>`:''}
      <details open><summary>Captured evidence and recognition</summary><div class="beta-images">${originals}</div><p><b>Combined fields:</b></p><pre>${esc(JSON.stringify(result.agreement||{},null,2))}</pre>${modelBlocks}</details>
      <details><summary>Scan, selection and exclusions</summary><pre>${esc(JSON.stringify({source_titles:scan,selection_policy:review.selection_policy||item.workflow,selected:review.selected_titles||item.mapping,excluded:review.excluded_titles||[],masterlist:review.masterlist||item.masterlist,provenance:review.masterlist_provenance},null,2))}</pre></details>
      ${identityForm}<div class="beta-title-list">${titleRows}</div>
      <label>Review notes <textarea class="beta-notes" rows="2">${esc(review.feedback?.notes||'')}</textarea></label>
      <p>Reviewed fields: <label><input type="checkbox" data-reviewed="identity">Identity</label> <label><input type="checkbox" data-reviewed="episodes">Episode mapping</label> <label><input type="checkbox" data-reviewed="files">Filenames/files</label> <label><input type="checkbox" data-reviewed="selection">Selection/exclusions</label></p>
      <div class="buttons"><button data-review="accept">Accept as correct</button><button data-review="save_reviewed">Save corrections and mark reviewed</button><button data-review="needs_attention">Needs attention / leave unreviewed</button></div><p class="beta-result" role="status"></p></article>`;
  }
  async function load(){
    try{const state=await api('/api/state');document.getElementById('local-beta-review').hidden=!state.local_beta_enabled;const dry=document.getElementById('dry-run-panel');if(dry)dry.hidden=!state.dry_run_only;const status=document.getElementById('beta-tray-status');if(status)status.textContent='ARM AUTO_EJECT owns automatic opening after terminal rips. Queue-open/post-rip tray commands are disabled; per-job physical tray state is reported as unverified unless observed.'}catch{}
    root.textContent='Loading local beta reviews…';
    try{const data=await api('/api/local-beta/reviews?status='+encodeURIComponent(filter.value));root.innerHTML=data.items.map(render).join('')||'<p>No matching local beta jobs.</p>';
      root.querySelectorAll('[data-review]').forEach(button=>button.addEventListener('click',async()=>{
        const card=button.closest('.beta-review-card'),job=card.querySelector('h3').textContent.match(/Job (\d+)/)?.[1],result=card.querySelector('.beta-result');
        if(!job)return;const corrections={};card.querySelectorAll('.beta-title').forEach(row=>{const f={};row.querySelectorAll('[data-field]').forEach(input=>{if(input.value.trim()===input.dataset.original)return;if(input.value.trim())f[input.dataset.field]=input.value.trim()});if(Object.keys(f).length)corrections[row.dataset.source]=f});
        const identity={};card.querySelectorAll('[data-identity]').forEach(input=>{if(input.value.trim())identity[input.dataset.identity]=input.value.trim()});
        const reviewed_fields=[...card.querySelectorAll('[data-reviewed]:checked')].map(x=>x.dataset.reviewed);
        button.disabled=true;result.textContent='Saving review…';
        try{await api(`/api/local-beta/jobs/${job}/review`,{action:button.dataset.review,identity,corrections,reviewed_fields,notes:card.querySelector('.beta-notes').value});result.textContent='Review saved locally.';await load()}catch(e){result.textContent='Could not save: '+e.message;button.disabled=false}
      }));
    }catch(e){root.textContent='Local review unavailable: '+e.message}
  }
  document.getElementById('beta-refresh')?.addEventListener('click',load);filter.addEventListener('change',load);load();
})();
