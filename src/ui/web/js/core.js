/* Dolmi web UI — core: helpers, navigation, theme, invisible mode, bridge events, toast, dialogs, boot.
   Classic script (not a module): top-level names are shared with the other js/*.js files.
   Load order (index.html): core, live, meetings, assistant, vocab, settings, onboarding. */
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
const root=document.documentElement,icons=()=>window.lucide&&lucide.createIcons();
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

$$('.nav button').forEach(b=>b.onclick=()=>{ $$('.nav button').forEach(n=>n.classList.toggle('a',n===b)); $$('.view').forEach(v=>v.classList.toggle('a',v.id==='v-'+b.dataset.v)); });
$$('[data-sw]').forEach(s=>s.onclick=()=>s.classList.toggle('on'));
$$('.seg:not(#theme)').forEach(s=>[...s.children].forEach(b=>b.onclick=()=>[...s.children].forEach(x=>x.classList.toggle('a',x===b))));
/* invisible mode — wired to the Python bridge (falls back to a visual toggle in a plain browser) */
const bridge=()=>window.pywebview&&window.pywebview.api;
function setInvisible(on){ try{ const b=bridge(); if(b&&b.toggle_invisible) b.toggle_invisible(on); }catch(e){} }
$('#stealth').onclick=()=>{ const on=$('#stealth').classList.toggle('on'); const sw=$('#swInv'); if(sw) sw.classList.toggle('on',on); setInvisible(on); };
if($('#swInv')) $('#swInv').onclick=()=>{ const on=$('#swInv').classList.toggle('on'); $('#stealth').classList.toggle('on',on); setInvisible(on); };
function setTheme(t){ root.dataset.theme=t; $$('#theme button').forEach(x=>x.classList.toggle('a',x.dataset.t===t)); }
$$('#theme button').forEach(b=>b.onclick=()=>{ setTheme(b.dataset.t); const a=window.pywebview&&window.pywebview.api; if(a) a.set_setting('theme',b.dataset.t); });

/* platform words: every PC/Mac, Ctrl/⌃ … word comes from dolmi_platform.WORDS (state().platform).
   W('computer') -> 'PC' | 'Mac'. Static text carries data-wt="… {key} …" and is filled in at boot;
   without the app (plain browser) the HTML keeps its authored text and W() returns the key. */
let PLATFORM={os:'',words:{}};
const W=k=>(PLATFORM.words&&PLATFORM.words[k])??k;
const wt=s=>String(s).replace(/\{([A-Za-z_]+)\}/g,(_,k)=>W(k));
function applyPlatform(p){ if(!p) return; PLATFORM=p; root.dataset.platform=p.os;
  $$('[data-wt]').forEach(e=>{ e.textContent=wt(e.dataset.wt); }); }

/* ===================== bridge integration ===================== */
function toast(msg){ let t=document.getElementById('toast'); if(!t){ t=document.createElement('div'); t.id='toast'; t.style.cssText='position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:var(--ink);color:var(--bg);padding:10px 16px;border-radius:999px;font-size:13px;font-weight:600;z-index:80;box-shadow:var(--sh);max-width:80vw;transition:opacity .3s'; document.body.append(t); } t.textContent=msg; t.style.opacity='1'; clearTimeout(t._h); t._h=setTimeout(()=>t.style.opacity='0',4200); }
let currentMeeting=null,currentChat=null,mLoaded=false,vLoaded=false,aLoaded=false;
/* APP-4: 'Ready' only once the models are loaded (Python loads them in the background when Dolmi opens) */
let engineState='idle';
const statusWord=()=>({ready:'Ready',loading:'Loading',missing:'Not downloaded'})[engineState]||'Not loaded';

/* Python -> UI */
window.dolmi={
  onSource:(lab)=>applyAudioSource(lab),
  onEngine:(label,state)=>{ engineLabel=label; if(state) engineState=state; $('#status').innerHTML=(listening?'Translating':statusWord())+'<b>'+esc(label)+'</b>'; },
  onListening:(on,info)=>{
    if(on){ engineLabel=info||engineLabel; setLive(true,engineLabel); return; }
    setLive(false,engineLabel); if(!lines) showIdle();
    if(info) toast('Transcript saved: '+info);
    if(mLoaded) loadMeetings();
  },
  onLine:(ts,de,en,meta)=>addFinal(ts,de,en,meta),
  onDraft:(de,en,meta)=>showDraft(de,en,meta),
  onLevel:(rms)=>{ speaking = rms>0.01?1:0; },
  onError:(msg)=>toast(msg),
  onStopped:()=>{ setLive(false,engineLabel); if(!lines) showIdle(); },
  onAsk:(qid)=>asrStart(qid),
  onAnswerChunk:(qid,p)=>asrChunk(qid,p),
  onAnswerDone:(qid,err)=>asrDone(qid,err),
  onSummaryChunk:(p)=>sumChunk(p),
  onSummaryDone:(err)=>sumDone(err),
  onInvisible:(on)=>{ setSw('#swInv',on); $('#stealth').classList.toggle('on',!!on); },
  onBar:(on)=>{ setSw('#swBar',on); setBarBtn(on); },
  onChatsChanged:(id)=>{ currentChat=id; if(aLoaded&&!aArch) loadChats(); },
  onModelProgress:(k,mb,total)=>{ const r=[...document.querySelectorAll('.mrow')].find(x=>x.dataset.k===k); if(!r) return; const bar=r.querySelector('.mbar i'), lab=r.querySelector('.mpct'); if(!bar){ loadModels(); return; } const pct=total?Math.min(99,Math.round(mb/total*100)):0; bar.style.width=pct+'%'; if(lab) lab.textContent=pct+'%'; },
  onModelDone:(k,err)=>{ toast(err||'Model downloaded'); loadModels(); },
};

/* ---- shared UI helpers ---- */
function setSw(sel,on){ const e=$(sel); if(e) e.classList.toggle('on',!!on); }
function setSegVal(sel,attr,val){ const g=$(sel); if(g)[...g.children].forEach(c=>c.classList.toggle('a',c.getAttribute(attr)===val)); }
async function copyText(t){ const b=bridge(); let ok=false; if(b){ try{ ok=await b.copy_text(t); }catch(e){} } if(!ok){ try{ await navigator.clipboard.writeText(t); ok=true; }catch(e){} } toast(ok?'Copied to clipboard':'Could not copy'); }
function archLabel(btn,arch){ const s=btn.querySelector(':scope > span:last-child'); if(s) s.textContent=arch?'Unarchive':'Archive'; }

/* in-app confirmation dialog (the WebView's confirm() is a plain system box) */
function askConfirm({title,body,ok='Delete',items=[]}){
  return new Promise(res=>{
    let s=document.getElementById('cfmModal');
    if(!s){ s=document.createElement('div'); s.id='cfmModal'; s.className='scrim'; document.body.append(s); }
    s.innerHTML=`<div class="cfm" role="alertdialog" aria-modal="true" aria-labelledby="cfmT"><div class="ci"><i data-lucide="trash-2"></i></div><h3 id="cfmT">${esc(title)}</h3><p>${esc(body)}</p>${items.length?`<div class="cl">${items.slice(0,12).map(i=>`<span>${esc(i)}</span>`).join('')}${items.length>12?`<span>… and ${items.length-12} more</span>`:''}</div>`:''}<div class="row2"><button class="btn ghost" id="cfmNo">Cancel</button><button class="btn danger" id="cfmOk">${esc(ok)}</button></div></div>`;
    icons(); s.hidden=false;
    const key=e=>{ if(e.key==='Escape'){ e.stopPropagation(); done(false); } };
    const done=v=>{ s.hidden=true; document.removeEventListener('keydown',key,true); res(v); };
    document.addEventListener('keydown',key,true);
    s.onclick=e=>{ if(e.target===s) done(false); };
    s.querySelector('#cfmNo').onclick=()=>done(false);
    s.querySelector('#cfmOk').onclick=()=>done(true);
    s.querySelector('#cfmNo').focus();
  });
}

/* load each tab's data the first time it opens (Vocabulary also re-reads its files on every reopen) */
$$('.nav button').forEach(btn=>btn.addEventListener('click',()=>{ const v=btn.dataset.v;
  if(v==='meetings'&&!mLoaded){ mLoaded=true; loadMeetings(); }
  if(v==='vocab'){ if(!vLoaded){ vLoaded=true; loadVocab(); } else vocRefresh(); }   // reopen: pick up hand edits
  if(v==='assistant'&&!aLoaded){ aLoaded=true; loadChats(); }
  if(v==='settings') loadModels();
}));

/* boot: pull state from Python */
async function boot(){ const b=bridge(); if(!b) return; try{ const s=await b.state();
  applyPlatform(s.platform);
  setTheme(s.theme||'paper');
  engineState = s.engineState||(s.engine==='not loaded'?'idle':'ready');
  engineLabel = s.engine==='not loaded' ? (engineState==='loading'?'speech and translation models':'models load on Start') : s.engine;
  setSw('#swInv',s.invisible); $('#stealth').classList.toggle('on',s.invisible);
  setSw('#swGerman',s.showGerman); setSw('#swSave',s.saveTranscripts); setSw('#swBar',s.overlay); setBarBtn(s.barVisible);
  applyGerman(s.showGerman);
  setSegVal('#setProvider','data-p',s.provider); setSegVal('#setKeep','data-d',String(s.keepDays));
  if($('#setModel')) $('#setModel').value=s.speechModel;
  applySource(s.speechSource||'local');
  applyLang(s.language);
  applyProvider(s);
  $('#status').innerHTML=statusWord()+'<b>'+esc(engineLabel)+'</b>';
  setRange('#setFont',s.font); setRange('#setOpacity',Math.round(s.opacity*100));
  applyPc();
  applyAudioSource(s.sourceLabel);
  try{ const mics=await b.mic_devices(); if($('#setMic')){ $('#setMic').innerHTML='<option value="">Default microphone</option>'+mics.map(d=>`<option ${d===s.micDevice?'selected':''}>${esc(d)}</option>`).join('');
    $('#setMic').onchange=async()=>{ await b.set_setting('mic_device',$('#setMic').value); const st=await b.state(); applyAudioSource(st.sourceLabel); }; } }catch(e){ console.error('mics',e); }
  try{ const devs=await b.devices(); if($('#setDevice')){ $('#setDevice').innerHTML='<option value="">'+esc(W('speaker_default'))+'</option>'+devs.map(d=>`<option ${d===s.audioDevice?'selected':''}>${esc(d)}</option>`).join(''); $('#setDevice').onchange=()=>b.set_setting('audio_device',$('#setDevice').value); } }catch(e){}
}catch(e){ console.error('boot',e); } }
/* every js/*.js file has run by DOMContentLoaded (classic scripts at the end of <body>), so boot()
   and the first paint can use functions from any of them */
document.addEventListener('DOMContentLoaded',()=>{
  if(window.pywebview&&window.pywebview.api) boot(); else window.addEventListener('pywebviewready',boot);
  icons(); wave();
});
