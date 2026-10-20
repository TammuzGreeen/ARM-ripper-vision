(()=>{
 const master=document.getElementById('master-select'),disc=document.getElementById('master-disc-select'),entry=document.getElementById('master-entry-select');
 if(!master||!disc||!entry)return;let last='';
 const option=(value,label)=>{const o=document.createElement('option');o.value=value;o.textContent=label;return o};
 function render(){
  let selected=null;try{selected=window.queueState?.masters?.find(m=>m.id===master.value)}catch{}
  const signature=JSON.stringify([selected?.id,selected?.discs]);if(signature===last)return;last=signature;
  const oldDisc=disc.value,oldEntry=entry.value;disc.replaceChildren(option('','Automatic / no fixed start'));
  for(const d of selected?.discs||[])disc.append(option(d.id,`Disc ${d.number}`));
  disc.disabled=!selected;disc.value=(selected?.discs||[]).some(d=>d.id===oldDisc)?oldDisc:'';
  entry.replaceChildren(option('','Automatic / no fixed start'));
  const chosen=selected?.discs?.find(d=>d.id===disc.value);
  for(const [i,ep] of (chosen?.episodes||[]).entries())entry.append(option(String(i),`S${String(selected.season).padStart(2,'0')}E${String(ep.number).padStart(2,'0')} · ${ep.title||'Title unknown'}`));
  entry.disabled=!chosen;entry.value=[...entry.options].some(o=>o.value===oldEntry)?oldEntry:'';
 }
 disc.addEventListener('change',()=>{last='';render()});master.addEventListener('change',()=>{disc.value='';entry.value='';last='';render()});
 setInterval(render,500);render();
})();
