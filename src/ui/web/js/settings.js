/* Dolmi web UI — Settings view: switches, languages, captions source, AI provider/keys, audio source, this computer & speech models, subtitle bar.
   Classic script (not a module): top-level names are shared with the other js/*.js files.
   Load order (index.html): core, live, meetings, assistant, vocab, settings, onboarding, heard. */
let pcSummary=null,modelChip='Speech: auto';   /* what applyPc found; the onboarding shows it */

/* ---- settings wiring ---- */
function applyGerman(on){ $('#brows').classList.toggle('hidede',!on); $('#bhead').classList.toggle('hidede',!on); }
function wireSwitch(sel,key){ const e=$(sel); if(e) e.addEventListener('click',()=>{ const b=bridge(); if(b) b.set_setting(key,e.classList.contains('on')); }); }
wireSwitch('#swGerman','show_german'); wireSwitch('#swSave','save_transcripts');
if($('#swGerman')) $('#swGerman').addEventListener('click',()=>applyGerman($('#swGerman').classList.contains('on')));
/* subtitle bar: the top-bar button and the Settings switch both open/close it and remember the choice */
function setBarBtn(on){ const e=$('#barBtn'); if(!e) return; e.classList.toggle('on',!!on); $('#barLab').textContent=on?'Subtitles on':'Subtitles'; }
function setBar(on){ const b=bridge(); setSw('#swBar',on); setBarBtn(on); if(!b) return;
  b.set_setting('overlay',on); (on?b.show_overlay():b.hide_overlay()).catch(()=>{ setBarBtn(false); toast('Could not open the subtitle bar.'); }); }
if($('#swBar')) $('#swBar').addEventListener('click',()=>setBar($('#swBar').classList.contains('on')));
$('#barBtn').onclick=()=>setBar(!$('#barBtn').classList.contains('on'));
if($('#setModel')) $('#setModel').onchange=()=>{ const b=bridge(); if(b){ b.set_setting('model',$('#setModel').value); toast(listening?'Speech model set — used from the next Start.':'Speech model set.'); loadModels(); } };
/* ---- captions by: this computer (Whisper) or a cloud speech API ---- */
const SOURCES={gemini:'Gemini',openai:'OpenAI',cloudflare:'Cloudflare Workers AI'};
function applySource(v){ if(!$('#setSource')) return; $('#setSource').value=v; const cloud=v!=='local';
  $('#modelRow').hidden=cloud;
  $('#sourceHint').textContent=cloud?`Each sentence's audio goes to ${SOURCES[v]} with your API key (Settings → Assistant)`:'Whisper on this computer — audio never leaves it'; }
if($('#setSource')) $('#setSource').onchange=async()=>{ const b=bridge(), v=$('#setSource').value; if(b) await b.set_setting('speech_source',v); applySource(v);
  toast(v==='local'?'Captions: this computer':`Captions: ${SOURCES[v]} (cloud) — next Start`); };
/* ---- spoken language: 27 languages (or Auto-detect) get English subtitles; English gets German ---- */
const LANG_LIST=[['de','German','Deutsch'],['en','English','English'],['fr','French','Français'],['es','Spanish','Español'],
 ['it','Italian','Italiano'],['nl','Dutch','Nederlands'],['pl','Polish','Polski'],['pt','Portuguese','Português'],
 ['ru','Russian','Русский'],['uk','Ukrainian','Українська'],['tr','Turkish','Türkçe'],['ar','Arabic','العربية'],
 ['fa','Persian','فارسی'],['ur','Urdu','اردو'],['hi','Hindi','हिन्दी'],['zh','Chinese','中文'],['ja','Japanese','日本語'],
 ['ko','Korean','한국어'],['sv','Swedish','Svenska'],['da','Danish','Dansk'],['fi','Finnish','Suomi'],['cs','Czech','Čeština'],
 ['hu','Hungarian','Magyar'],['ro','Romanian','Română'],['el','Greek','Ελληνικά'],['bg','Bulgarian','Български'],
 ['id','Indonesian','Bahasa Indonesia'],['vi','Vietnamese','Tiếng Việt']];
const LANG={auto:{name:'any language',self:'Auto',to:'en'}};
LANG_LIST.forEach(([c,n,s])=>{ LANG[c]={name:n,self:s,to:c==='en'?'de':'en'}; });
if($('#setLang')){ const opt=(v,t)=>`<option value="${v}">${t}</option>`;
  $('#setLang').innerHTML=opt('de','German → English')+opt('en','English → German')+opt('auto','Auto-detect → English')
    +'<optgroup label="More languages → English">'+LANG_LIST.slice(2).sort((a,b)=>a[1].localeCompare(b[1])).map(([c,n])=>opt(c,n+' → English')).join('')+'</optgroup>'; }
let curLang='de';
function applyLang(l){ curLang=LANG[l]?l:'de'; const f=LANG[curLang], t=LANG[f.to];
  $('#chipFrom').textContent=f.self; $('#chipTo').textContent=t.self;
  $('#headFrom').textContent=f.self.toUpperCase(); $('#headTo').textContent=t.self.toUpperCase();
  const hint=$('#idleHint'); if(hint) hint.textContent=`Press start — ${f.name} on the left, ${t.name} on the right.`;   /* gone while captions show */
  if($('#srcLab')) $('#srcLab').textContent=curLang==='auto'?'Show original speech':`Show ${f.name} source`;
  if($('#setLang')) $('#setLang').value=curLang;
  $$('#onbBody [data-lang]').forEach(x=>x.classList.toggle('on',x.dataset.lang===curLang)); }
function pickLang(l){ const b=bridge(); if(b) b.set_setting('language',l); applyLang(l); }
if($('#setLang')) $('#setLang').onchange=()=>pickLang($('#setLang').value);
if($('#setKeep'))[...$('#setKeep').children].forEach(x=>x.addEventListener('click',()=>{ const b=bridge(); if(b) b.set_setting('keep_days',+x.dataset.d); }));
function applyProvider(s){ const cf=s.provider==='cloudflare'; if($('#cfRow')){ $('#cfRow').hidden=!cf; $('#setCfAccount').value=s.cfAccount||''; }
  if($('#setKey')) $('#setKey').placeholder=s.hasKey?'•••••• saved':(s.keyHint||'sk-…'); if($('#keyStatus')) $('#keyStatus').textContent=s.hasKey?'Key saved':(cf&&!s.cfAccount?'Add your Account ID and API token':'No key yet');
  loadAiModels(); }
async function loadAiModels(){ const b=bridge(), m=$('#setAiModel'); if(!b||!m) return;
  m.innerHTML='<option>Loading models…</option>'; const r=await b.list_ai_models();
  m.innerHTML=r.models.map(id=>`<option value="${esc(id)}">${esc(id)}${id===r.default?' (default)':''}</option>`).join('')+'<option value="__other">Other…</option>';
  m.value=r.current; $('#setAiModelOther').hidden=true;
  $('#aiModelHint').textContent=r.live?`${r.models.length} models from this provider`:'Add your API key to see every model'; }
async function useAiModel(id){ const b=bridge(); if(!b||!id) return; const s=await b.set_ai_model(id); toast('Assistant model: '+s.model); loadAiModels(); }
if($('#setProvider'))[...$('#setProvider').children].forEach(x=>x.addEventListener('click',async()=>{ const b=bridge(); if(b) applyProvider(await b.set_provider(x.dataset.p)); }));
if($('#setAiModel')) $('#setAiModel').addEventListener('change',()=>{ const v=$('#setAiModel').value; if(v==='__other'){ const o=$('#setAiModelOther'); o.hidden=false; o.value=''; o.focus(); } else useAiModel(v); });
if($('#setCfAccount')) $('#setCfAccount').addEventListener('change',async()=>{ const b=bridge(); if(!b) return; const r=await b.set_cf_account($('#setCfAccount').value); if(!r.ok){ toast(r.message); return; } toast('Account ID saved'); applyProvider(r); });
if($('#setAiModelOther')) $('#setAiModelOther').addEventListener('change',()=>useAiModel($('#setAiModelOther').value.trim()));
if($('#setKey')) $('#setKey').addEventListener('change',()=>{ const v=$('#setKey').value.trim(); const b=bridge(); if(v&&b){ b.set_key(v).then(async()=>applyProvider(await b.state())); $('#setKey').value=''; $('#setKey').placeholder='•••••• saved'; if($('#keyStatus')) $('#keyStatus').textContent='Key saved'; } });
if($('#setTest')) $('#setTest').onclick=async()=>{ const b=bridge(), st=$('#keyStatus'); if(!b){ if(st) st.textContent='Runs in the app'; return; } if(st) st.textContent='Testing…'; const r=await b.test_key(); if(st) st.textContent=(r.ok?'✓ ':'✗ ')+r.message; };

/* ---- audio source: speaker (online meeting) or microphone (in person) ---- */
function applyAudioSource(lab){ if(!lab) return; const mic=lab.source==='mic';
  $('#srcChip').classList.toggle('mic',mic); $('#srcKind').textContent=mic?'Microphone':'Speaker'; $('#srcDev').textContent=lab.device;
  $('#srcIc').innerHTML=`<i data-lucide="${mic?'mic':'volume-2'}"></i>`; icons(); setSegVal('#setSrc','data-s',lab.source); }
async function switchSource(src){ const b=bridge(); if(!b){ applyAudioSource({source:src,device:src==='mic'?'Default microphone':W('speaker_default')}); return; }
  const lab=await b.set_source(src); applyAudioSource(lab); toast(src==='mic'?'Listening to the microphone':'Listening to the speaker'); }
$('#srcChip').onclick=()=>switchSource($('#srcChip').classList.contains('mic')?'speaker':'mic');
if($('#setSrc'))[...$('#setSrc').children].forEach(x=>x.addEventListener('click',()=>switchSource(x.dataset.s)));

/* ---- this computer & models ---- */
async function applyPc(){ const b=bridge(); if(!b) return; const pc=await b.pc_info();
  if(PLATFORM.os==='mac'){ applyMac(pc); return; }
  const gpuShort=(pc.gpu_name||'NVIDIA GPU').replace(/^NVIDIA\s+(GeForce\s+)?/i,'');
  pcSummary=`${pc.gpu?gpuShort:'no NVIDIA GPU'} · ${pc.ram_gb} GB RAM · ${pc.cores} CPU threads`;
  modelChip=pc.gpu?'GPU · large-v3-turbo':'CPU · small';
  $('#eye').textContent='ON THIS '+W('COMPUTER')+' · '+(pc.gpu?gpuShort.toUpperCase():'CPU ONLY');
  if($('#modelHint')) $('#modelHint').textContent=pc.gpu?`Auto picks large-v3-turbo on your ${gpuShort}`:'No usable NVIDIA GPU — Auto picks Small, which runs on the CPU';
  const nvidia=/nvidia/i.test(pc.gpu_name||''), speech=pc.gpu?['GPU speech ready','on']:nvidia?['NVIDIA found, GPU add-on not installed','warn']:['Speech runs on the CPU',''];
  if($('#specs')) $('#specs').innerHTML=[[pc.gpu_name||'No dedicated GPU found',''],[pc.ram_gb?pc.ram_gb+' GB RAM':'RAM unknown',''],[pc.cores+' CPU threads',''],speech].map(([t,c])=>`<span class="chipt ${c}">${esc(t)}</span>`).join('');
  if($('#modelDir')) $('#modelDir').textContent=(pc.model_dir||'Model folder unknown')+' — shared by every Dolmi install for this '+W('os_user');
}
/* the Mac: no GPU path (CTranslate2 has no Metal backend), so the chip name stands in for the graphics card */
function applyMac(pc){
  const chip=pc.gpu_name||W('computer');
  pcSummary=`${chip} · ${pc.ram_gb} GB RAM · ${pc.cores} CPU cores`;
  modelChip='CPU · small';
  $('#eye').textContent='ON THIS '+W('COMPUTER')+' · '+chip.toUpperCase();
  if($('#modelHint')) $('#modelHint').textContent=`Auto picks Small, which runs live on your ${chip}`;
  if($('#specs')) $('#specs').innerHTML=[[chip,''],[pc.ram_gb?pc.ram_gb+' GB RAM':'RAM unknown',''],[pc.cores+' CPU cores',''],['Speech runs on the CPU','']].map(([t,c])=>`<span class="chipt ${c}">${esc(t)}</span>`).join('');
  if($('#modelDir')) $('#modelDir').textContent=(pc.model_dir||'Model folder unknown')+' — shared by every Dolmi install for this '+W('os_user');
}
function fmtMb(mb){ return mb>=1000?(mb/1000).toFixed(1)+' GB':Math.round(mb)+' MB'; }
async function loadModels(){ const b=bridge(), box=$('#modelList'); if(!box) return; if(!b){ box.innerHTML='<div class="ps">Models are managed when running in the app.</div>'; return; }
  const ms=await b.list_models();
  box.innerHTML=ms.map(m=>{
    const tags=(m.loaded?'<span class="mtag use">In use</span>':'')+(m.auto?'<span class="mtag ok">Auto</span>':m.selected?'<span class="mtag ok">Selected</span>':m.needed?'<span class="mtag ok">Needed</span>':'');
    let act;
    if(m.downloading) act='<div class="mbar"><i></i></div><span class="mtag mpct">…</span>';
    else if(m.installed) act='<span class="inst"><i data-lucide="check"></i>Installed</span>'+(m.kind==='speech'&&!m.selected&&!m.auto?`<button class="btn ghost" data-use="${esc(m.key)}">Use</button>`:'')+`<button class="btn ghost" data-remove="${esc(m.key)}" ${m.loaded&&listening?'disabled title="Stop translating first"':''}>Remove</button>`;
    else act=(m.kind==='speech'&&!m.selected&&!m.auto?`<button class="btn ghost" data-use="${esc(m.key)}" title="Use it; it downloads on the next Start">Use</button>`:'')+`<button class="btn ghost on" data-download="${esc(m.key)}">Download</button>`;
    return `<div class="mrow${m.selected||m.auto?' sel':''}" data-k="${esc(m.key)}"><div><div class="nm">${esc(m.name)}${tags}</div><div class="sub">${m.kind==='speech'?'':'Translator · '}${esc(m.note)}</div></div><div class="msize">${fmtMb(m.sizeMb)}</div><div class="mfit ${esc(m.fitLevel)}">${esc(m.fit)}</div><div class="mact">${act}</div></div>`;
  }).join(''); icons();
  box.querySelectorAll('[data-download]').forEach(btn=>btn.onclick=async()=>{ await b.download_model(btn.dataset.download); loadModels(); });
  box.querySelectorAll('[data-use]').forEach(btn=>btn.onclick=async()=>{ await b.use_model(btn.dataset.use); if($('#setModel')) $('#setModel').value=btn.dataset.use; toast(btn.closest('.mrow').querySelector('.nm').firstChild.textContent+' will be used from the next Start'); loadModels(); });
  box.querySelectorAll('[data-remove]').forEach(btn=>btn.onclick=async()=>{
    if(!btn.dataset.armed){ btn.dataset.armed='1'; btn.textContent='Click again'; setTimeout(()=>{ if(btn.isConnected){ delete btn.dataset.armed; btn.textContent='Remove'; } },4000); return; }
    const r=await b.remove_model(btn.dataset.remove); toast(r.ok?'Model removed':r.message); loadModels(); });
}
$('#openModels').onclick=()=>{ const b=bridge(); if(b) b.open_model_folder(); };

/* ---- subtitle bar opacity + text size ---- */
function setRange(sel,v){ const e=$(sel); if(e&&v!=null){ e.value=v; const o=$(sel+'Val'); if(o) o.textContent=v+(sel==='#setOpacity'?'%':' px'); } }
function wireRange(sel,key,conv){ const e=$(sel); if(!e) return; let tm; e.addEventListener('input',()=>{ setRange(sel,e.value); clearTimeout(tm); tm=setTimeout(()=>{ const b=bridge(); if(b) b.set_setting(key,conv(+e.value)); },150); }); }
wireRange('#setOpacity','opacity',v=>v/100); wireRange('#setFont','font',v=>v);

/* ---- sections (APP-2): left menu, one short page each, search, deep links ---- */
const SET_SECTION_KEY='dolmi.settings.section';
const setSections=()=>$$('#setMenu [data-sec]').map(b=>b.dataset.sec);
function showSettingsSection(sec){ if(!setSections().includes(sec)) sec='audio';
  $$('#setMenu [data-sec]').forEach(b=>{ const on=b.dataset.sec===sec; b.classList.toggle('a',on); b.setAttribute('aria-current',on?'page':'false');
    const m=b.parentElement; if(on&&m.scrollWidth>m.clientWidth) m.scrollLeft=b.offsetLeft-m.offsetLeft-12; });   /* narrow window: menu is one row */
  $$('.set-page').forEach(p=>p.classList.toggle('a',p.dataset.sec===sec));
  try{ localStorage.setItem(SET_SECTION_KEY,sec); }catch(e){}
  return sec; }
function lastSettingsSection(){ try{ return localStorage.getItem(SET_SECTION_KEY)||'audio'; }catch(e){ return 'audio'; } }
/* open Settings at a section, optionally at one row: rowId is a row's or a control's id (or the row) */
function openSettings(section,rowId){
  const n=$('.nav button[data-v="settings"]'); if(n&&!n.classList.contains('a')) n.click();
  const sec=showSettingsSection(section||lastSettingsSection());
  const t=typeof rowId==='string'?document.getElementById(rowId):rowId, row=t&&(t.closest('.set,.set-models')||t);
  const page=$(`.set-page[data-sec="${sec}"]`);
  if(row&&page&&page.contains(row)){ row.scrollIntoView({block:'nearest'}); row.classList.remove('set-hit'); void row.offsetWidth; row.classList.add('set-hit');
    const f=row.matches('.set')&&row.querySelector('select,input,button'); if(f) f.focus({preventScroll:true}); }
  else if(page) page.scrollTop=0;
}
/* an empty, hidden slot another domain fills (e.g. settingsMount('bar.time')); shown once used */
function settingsMount(name){ const m=$(`[data-mount="${name}"]`); if(m) m.hidden=false; return m; }
/* switches added after core.js wired the page's own (rows filled into a mount) */
function wireNewSwitches(scope){ if(scope) scope.querySelectorAll('[data-sw]').forEach(s=>{ if(!s.onclick) s.onclick=()=>s.classList.toggle('on'); }); }
$$('#setMenu [data-sec]').forEach(b=>b.addEventListener('click',()=>showSettingsSection(b.dataset.sec)));
showSettingsSection(lastSettingsSection());

/* search: label + keywords of every row (mounted rows included), jumps to the section and row */
function settingRows(){ return $$('.set-page').flatMap(p=>[...p.querySelectorAll('.set,.set-models')].filter(r=>!r.closest('[hidden]')).map(r=>{
  const lab=r.querySelector('.lab'), name=(r.dataset.name||(lab?lab.textContent:p.querySelector('.cardh').textContent)).trim();
  return {row:r,sec:p.dataset.sec,secName:p.querySelector('.cardh').textContent.trim(),name,hay:(name+' '+(r.dataset.kw||'')+' '+p.querySelector('.cardh').textContent).toLowerCase()}; })); }
function searchSettings(q){ const box=$('#setResults'), words=q.toLowerCase().split(/\s+/).filter(Boolean);
  $('#setMenu').hidden=!!words.length; box.hidden=!words.length; if(!words.length){ box.innerHTML=''; return []; }
  const hits=settingRows().filter(x=>words.every(w=>x.hay.includes(w))).slice(0,8);
  box.innerHTML=hits.length?hits.map((h,i)=>`<button data-i="${i}"><b>${esc(h.name)}</b><span>${esc(h.secName)}</span></button>`).join(''):'<div class="ps set-none">No setting matches</div>';
  [...box.querySelectorAll('button')].forEach(btn=>btn.onclick=()=>pickSearchHit(hits[+btn.dataset.i]));
  return hits; }
function pickSearchHit(h){ if(!h) return; $('#setSearch').value=''; searchSettings(''); openSettings(h.sec,h.row); }
if($('#setSearch')){ const s=$('#setSearch');
  s.addEventListener('input',()=>searchSettings(s.value));
  s.addEventListener('keydown',e=>{ if(e.key==='Enter'){ e.preventDefault(); pickSearchHit(searchSettings(s.value)[0]); } else if(e.key==='Escape'){ s.value=''; searchSettings(''); } }); }

/* rows that only exist since the sections: warning switch, input level, data folder, version, vocabulary link */
wireSwitch('#swWarnSilence','warn_silence');
wireSwitch('#swPreload','preload_models');                                    /* APP-4 */
if($('#setOpenVocab')) $('#setOpenVocab').onclick=()=>{ const n=$('.nav button[data-v="vocab"]'); if(n) n.click(); };
if($('#setDataOpen')) $('#setDataOpen').onclick=()=>{ const b=bridge(); if(b) b.open_data_folder(); };
function setLevelMeter(rms){ const e=$('#setLevel'); if(e) e.style.width=Math.min(100,Math.round(Math.sqrt(Math.max(0,rms||0))*400))+'%'; }
{ const onLevel=window.dolmi.onLevel, onListening=window.dolmi.onListening;
  window.dolmi.onLevel=rms=>{ onLevel(rms); setLevelMeter(rms); };
  window.dolmi.onListening=(on,info)=>{ onListening(on,info); if(!on) setLevelMeter(0); }; }
async function settingsBoot(){ const b=bridge(); if(!b) return; try{ const s=await b.state();
  setSw('#swWarnSilence',s.warnSilence!==false);
  setSw('#swPreload',s.preloadModels!==false);
  if($('#setVersion')) $('#setVersion').textContent=s.version||'—';
  if($('#setDataDir')) $('#setDataDir').textContent=s.dataFolder||'—';
}catch(e){ console.error('settings boot',e); } }
document.addEventListener('DOMContentLoaded',()=>{ if(bridge()) settingsBoot(); else window.addEventListener('pywebviewready',settingsBoot); });

/* ---- APP-6 update check: About & updates. Dolmi asks GitHub for the latest release when it opens (can be
   turned off); a newer one is offered as "Download x.y.z", which opens the release page. Nothing installs. ---- */
{ const m=settingsMount('about.updates');
  if(m) m.innerHTML=`<div class="set" data-kw="update updates new version release download check github"><div><div class="lab">Check for updates</div><div class="ps">When Dolmi opens, asks GitHub for the latest release. Nothing is downloaded or installed by itself</div></div><button class="sw on" data-sw id="swUpdates"></button></div>
    <div class="set" data-kw="update new version download release check now"><div><div class="lab">Latest version</div><div class="ps" id="updStatus">Not checked yet</div></div><div style="display:flex;gap:8px"><button class="btn ghost" id="setCheckUpdate">Check now</button><button class="btn ghost on" id="setDownloadUpdate" hidden>Download</button></div></div>`; wireNewSwitches(m); }
wireSwitch('#swUpdates','check_updates');
function applyUpdate(u,asked){ const st=$('#updStatus'), dl=$('#setDownloadUpdate'); if(!st||!u) return;
  if(u.available){ st.textContent=`Dolmi ${u.version} is out (you have ${u.current})`; dl.hidden=false; dl.textContent=`Download ${u.version}`; dl.dataset.url=u.url; }
  else { dl.hidden=true; st.textContent=u.version?`You have the latest version (${u.current})`:(asked?'Could not reach GitHub. Try again later':'Not checked yet'); } }
if($('#setCheckUpdate')) $('#setCheckUpdate').onclick=async()=>{ const b=bridge(); if(!b) return; $('#updStatus').textContent='Checking…'; applyUpdate(await b.check_update(true),true); };
if($('#setDownloadUpdate')) $('#setDownloadUpdate').onclick=()=>{ const b=bridge(); if(b) b.open_release($('#setDownloadUpdate').dataset.url||''); };
{ const prev=window.dolmi.onUpdate; window.dolmi.onUpdate=u=>{ if(prev) prev(u); applyUpdate(u); toast(`Dolmi ${u.version} is available: Settings → About & updates`); }; }
async function updatesBoot(){ const b=bridge(); if(!b) return; try{ const s=await b.state(); setSw('#swUpdates',s.checkUpdates!==false); if(s.update) applyUpdate(s.update); }catch(e){ console.error('updates boot',e); } }
document.addEventListener('DOMContentLoaded',()=>{ if(bridge()) updatesBoot(); else window.addEventListener('pywebviewready',updatesBoot); });

/* ---- APP-8 subtitle bar lock: the bar ignores the mouse so it never takes a click from the meeting window.
   It can't be clicked while locked, so it is unlocked here or with the shortcut (About & updates). ---- */
{ const m=settingsMount('bar.clickthrough');
  if(m) m.innerHTML=`<div class="set" data-kw="lock click through clickthrough mouse ignore pass bar overlay pin"><div><div class="lab">Lock the bar</div><div class="ps" id="barLockHint">Clicks go through it to the meeting window. Unlock here or with the shortcut</div></div><button class="sw" data-sw id="swBarLock"></button></div>`; wireNewSwitches(m); }
if($('#swBarLock')) $('#swBarLock').addEventListener('click',async()=>{ const b=bridge(); if(!b) return; const on=$('#swBarLock').classList.contains('on');
  const r=await b.set_bar_locked(on); setSw('#swBarLock',r.locked); toast(r.locked?'Subtitle bar locked: clicks go through it':'Subtitle bar unlocked'); });
{ const prev=window.dolmi.onBarLock; window.dolmi.onBarLock=on=>{ if(prev) prev(on); setSw('#swBarLock',on); }; }
async function barLockBoot(){ const b=bridge(); if(!b) return; try{ const s=await b.state(); setSw('#swBarLock',!!s.barLocked); }catch(e){ console.error('bar lock boot',e); } }
document.addEventListener('DOMContentLoaded',()=>{ if(bridge()) barLockBoot(); else window.addEventListener('pywebviewready',barLockBoot); });
