/* Dolmi web UI — the 'nothing heard' warning on the Live view (APP-3).
   Python's watcher (Api._heard_tick) sends onHeardWarn('silent' | 'no_captions' | null) while live;
   this draws a dismissible banner in #heardWarn. Any caption or Stop hides it at once.
   Classic script (not a module): top-level names are shared with the other js/*.js files.
   Load order (index.html): core, live, meetings, assistant, vocab, settings, onboarding, heard. */
let heardState=null,heardDismissed=null;

function setHeard(state){ if(state===heardState) return; heardState=state||null; if(!heardState) heardDismissed=null; renderHeard(); }
function heardFix(label,icon,act){ return `<button class="btn ghost" data-heard="${act}"><span class="ic"><i data-lucide="${icon}" style="width:15px;height:15px"></i></span>${esc(label)}</button>`; }
function renderHeard(){ const box=$('#heardWarn'); if(!box) return;
  const show=!!heardState&&heardState!==heardDismissed;
  box.hidden=!show; $('#v-live').classList.toggle('heard-on',show);
  if(!show){ box.innerHTML=''; return; }
  const mic=$('#srcChip').classList.contains('mic'), dev=$('#srcDev').textContent;
  let title,reasons,fixes;
  if(heardState==='silent'){
    title='Dolmi hears nothing';
    /* the OS microphone privacy switch only blocks microphones, so it is offered when listening to one */
    reasons=mic?[`Wrong microphone, or it is muted (listening to ${dev})`,`The microphone is blocked in ${W('mic_privacy')}`,'Nobody is talking']
      :[`Wrong speaker: the meeting plays somewhere else (listening to ${dev})`,'The meeting is muted, or nobody is talking'];
    fixes=heardFix(mic?'Listen to the speaker':'Listen to the microphone',mic?'volume-2':'mic','switch')
      +heardFix('Audio settings','settings-2','audio')+(mic?heardFix('Open '+W('mic_privacy'),'shield','privacy'):'');
  } else {
    title='Sound but no captions';
    const lang=(typeof LANG!=='undefined'&&LANG[curLang])?LANG[curLang].name:'the spoken language';
    reasons=[`Spoken language is set to ${lang}: does it match the meeting?`,'The speech model may still be loading, or be too small for this audio'];
    fixes=heardFix('Spoken language','languages','lang')+heardFix('Speech models','cpu','speech');
  }
  box.innerHTML=`<div class="heard-card" role="status" aria-live="polite"><span class="heard-ic"><i data-lucide="${heardState==='silent'?'ear-off':'captions-off'}"></i></span>
    <div class="heard-body"><b>${esc(title)}</b><span class="heard-sub">for 20 seconds. Check:</span><ul>${reasons.map(r=>`<li>${esc(r)}</li>`).join('')}</ul><div class="heard-fix">${fixes}</div></div>
    <button class="heard-x" data-heard="close" title="Hide this warning" aria-label="Hide this warning"><i data-lucide="x"></i></button></div>`;
  box.querySelectorAll('[data-heard]').forEach(b=>b.onclick=()=>heardAct(b.dataset.heard)); icons(); }
async function heardAct(act){
  if(act==='close'){ heardDismissed=heardState; renderHeard(); return; }
  if(act==='switch'){ setHeard(null); switchSource($('#srcChip').classList.contains('mic')?'speaker':'mic'); return; }
  if(act==='audio') openSettings('audio');
  else if(act==='lang') openSettings('captions','setLang');
  else if(act==='speech') openSettings('speech');
  else if(act==='privacy'){ const b=bridge(); const ok=b?await b.open_mic_privacy():false; if(!ok) toast('Could not open '+W('mic_privacy')); }
}

/* Python -> UI: the warning itself; captions and Stop clear it without waiting for the next tick */
{ const d=window.dolmi, wrap=(k,f)=>{ const o=d[k]; d[k]=(...a)=>{ const r=o&&o(...a); f(...a); return r; }; };
  d.onHeardWarn=setHeard;
  wrap('onLine',()=>setHeard(null));
  wrap('onDraft',(de,en)=>{ if(de||en) setHeard(null); });
  wrap('onListening',on=>{ if(!on) setHeard(null); });
  wrap('onStopped',()=>setHeard(null));
  wrap('onSource',()=>renderHeard()); }                 /* the reasons name the device listened to */
