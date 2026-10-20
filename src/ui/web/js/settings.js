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
    <div class="set" data-kw="update new version download release check now"><div><div class="lab">Latest version</div><div class="ps" id="updStatus">Not checked yet</div></div><div style="display:flex;gap:8px"><button class="btn ghost" id="setCheckUpdate">Check now</button><button class="btn ghost on" id="setDownloadUpdate" style="display:none">Download</button></div></div>`; wireNewSwitches(m); }
wireSwitch('#swUpdates','check_updates');
function applyUpdate(u,asked){ const st=$('#updStatus'), dl=$('#setDownloadUpdate'); if(!st||!u) return;
  if(u.available){ st.textContent=`Dolmi ${u.version} is out (you have ${u.current})`; dl.style.display=''; dl.textContent=`Download ${u.version}`; dl.dataset.url=u.url; }
  else { dl.style.display='none'; st.textContent=u.version?`You have the latest version (${u.current})`:(asked?'Could not reach GitHub. Try again later':'Not checked yet'); } }
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

/* ---- LIVE-5: speech-start time on the subtitle bar's lines (off by default) ---- */
{ const m=settingsMount('bar.time');
  if(m) m.innerHTML=`<div class="set" data-kw="time times clock timestamp start bar overlay lines"><div><div class="lab">Show times</div><div class="ps">Each line on the bar starts with the time it was spoken</div></div><button class="sw" data-sw id="swBarTime"></button></div>`; wireNewSwitches(m); }
wireSwitch('#swBarTime','bar_time');
async function barTimeBoot(){ const b=bridge(); if(!b) return; try{ const s=await b.state(); setSw('#swBarTime',!!s.barTime); }catch(e){ console.error('bar time boot',e); } }
document.addEventListener('DOMContentLoaded',()=>{ if(bridge()) barTimeBoot(); else window.addEventListener('pywebviewready',barTimeBoot); });

/* ---- APP-7 global shortcuts: work while Teams or Zoom has the focus. Click a key button, then press the new
   keys (Esc cancels, Backspace puts the default back). ---- */
const SHORTCUTS=[['toggle_live','Start / stop captions'],['toggle_bar','Show / hide the subtitle bar'],['toggle_source','Switch speaker / microphone'],['toggle_lock','Lock / unlock the subtitle bar'],['hold_original','Hear the original (hold, headphone mix)']];
let scMap={},scStatus={},scRec=null;
function fmtCombo(c){ if(!c) return '—'; const parts=c.split('+'), key=parts.pop().toUpperCase();
  if(PLATFORM.os==='mac') return parts.map(m=>({ctrl:'⌃',alt:'⌥',shift:'⇧',meta:'⌘'})[m]||m).join('')+key;
  return [...parts.map(m=>({ctrl:'Ctrl',alt:'Alt',shift:'Shift',meta:'Win'})[m]||m),key].join('+'); }
{ const m=settingsMount('about.shortcuts');
  if(m){ m.innerHTML=`<div class="set" data-kw="shortcuts hotkeys keyboard global keys teams zoom"><div><div class="lab">Global shortcuts</div><div class="ps" id="scHint">Work while your meeting app has the focus</div></div><button class="sw on" data-sw id="swShortcuts"></button></div>`
      +SHORTCUTS.map(([k,lab])=>`<div class="set" data-kw="shortcut hotkey keyboard ${esc(lab.toLowerCase())}"><div><div class="lab">${esc(lab)}</div><div class="ps" id="scs-${k}">Click, then press the keys</div></div><button class="btn ghost" id="sc-${k}" data-sc="${k}" style="font-family:var(--mono);font-size:12px;min-width:150px">—</button></div>`).join('');
    wireNewSwitches(m); } }
function applyShortcuts(map,status,on){ scMap=map||scMap; scStatus=status||scStatus;
  SHORTCUTS.forEach(([k])=>{ const b=$('#sc-'+k), s=$('#scs-'+k); if(!b) return; if(scRec!==k) b.textContent=fmtCombo(scMap[k]);
    if(s) s.textContent=on===false?'Shortcuts are off':scStatus[k]===false?'Another app uses these keys. Pick others':'Click, then press the keys'; });
  if($('#scHint')) $('#scHint').textContent=`Work while your meeting app has the focus. ${W('hotkey')} always shows Dolmi`;
  if($('#barLockHint')&&scMap.toggle_lock) $('#barLockHint').textContent=`Clicks go through it to the meeting window. Unlock here or with ${fmtCombo(scMap.toggle_lock)}`; }
/* while recording, Dolmi's own shortcuts are let go, else the system takes a registered combo first */
function pauseShortcuts(on){ const b=bridge(); return b&&b.pause_shortcuts?b.pause_shortcuts(on).catch(e=>console.error('pause shortcuts',e)):Promise.resolve(); }
function stopRec(resume=true){ if(!scRec) return Promise.resolve(); const k=scRec; scRec=null; const b=$('#sc-'+k); if(b){ b.classList.remove('on'); b.textContent=fmtCombo(scMap[k]); } return resume?pauseShortcuts(false):Promise.resolve(); }
$$('[data-sc]').forEach(btn=>btn.addEventListener('click',()=>{ const k=btn.dataset.sc; if(scRec===k){ stopRec(); return; }
  if(scRec) stopRec(false); else pauseShortcuts(true);                          /* another key button: stay paused */
  scRec=k; btn.classList.add('on'); btn.textContent='Press keys…'; }));
window.addEventListener('blur',()=>{ stopRec(); });
function comboFromKey(e){ const c=e.code||'', key=/^Key[A-Z]$/.test(c)?c.slice(3).toLowerCase():/^Digit\d$/.test(c)?c.slice(5):/^F\d{1,2}$/.test(c)?c.toLowerCase():null;
  if(!key) return null; return [e.ctrlKey&&'ctrl',e.altKey&&'alt',e.shiftKey&&'shift',e.metaKey&&'meta'].filter(Boolean).concat(key).join('+'); }
document.addEventListener('keydown',async e=>{ if(!scRec) return; e.preventDefault(); e.stopPropagation();
  if(e.key==='Escape'){ stopRec(); return; }
  const k=scRec, combo=(e.key==='Backspace'||e.key==='Delete')?'':comboFromKey(e);
  if(combo===null) return;                                                     /* only a modifier so far */
  const b=bridge(); await stopRec(); if(!b) return;
  const r=await b.set_shortcut(k,combo);
  if(!r.ok){ toast(r.message); return; }
  applyShortcuts(r.shortcuts,r.status); toast(combo?`Shortcut set: ${fmtCombo(r.shortcuts[k])}`:'Shortcut back to its default'); },true);
if($('#swShortcuts')) $('#swShortcuts').addEventListener('click',async()=>{ const b=bridge(); if(!b) return; const on=$('#swShortcuts').classList.contains('on');
  await b.set_setting('shortcuts_on',on); const s=await b.state(); applyShortcuts(s.shortcuts,s.shortcutStatus,s.shortcutsOn); });
/* a shortcut pressed anywhere: do what the button on screen does */
{ const prev=window.dolmi.onShortcut; window.dolmi.onShortcut=name=>{ if(prev) prev(name);
  if(name==='toggle_live') $('#go').click();
  else if(name==='toggle_bar') setBar(!$('#barBtn').classList.contains('on'));
  else if(name==='toggle_source') switchSource($('#srcChip').classList.contains('mic')?'speaker':'mic'); }; }
async function shortcutsBoot(){ const b=bridge(); if(!b) return; try{ const s=await b.state(); setSw('#swShortcuts',s.shortcutsOn!==false); applyShortcuts(s.shortcuts||{},s.shortcutStatus||{},s.shortcutsOn); }catch(e){ console.error('shortcuts boot',e); } }
document.addEventListener('DOMContentLoaded',()=>{ if(bridge()) shortcutsBoot(); else window.addEventListener('pywebviewready',shortcutsBoot); });

/* ---- APP-12 report a problem: a zip (log, settings without keys, version, OS, models) saved where the user picks.
   Transcripts and vocabulary only when switched on here. Nothing is sent anywhere. ---- */
{ const m=settingsMount('about.report');
  if(m){ m.innerHTML=`<div class="set" data-kw="report problem bug issue log support zip help"><div><div class="lab">Report a problem</div><div class="ps" id="repStatus">Saves a zip with the log, your settings without any keys, the version and your system. Nothing is sent</div></div><button class="btn ghost" id="setReport">Save report…</button></div>
    <div class="set" data-kw="report include transcripts meetings"><div><div class="lab">Include transcripts</div><div class="ps">Your saved meetings and summaries go into the zip too</div></div><button class="sw" data-sw id="swRepTranscripts"></button></div>
    <div class="set" data-kw="report include vocabulary glossary"><div><div class="lab">Include vocabulary</div><div class="ps">Your spelling hints and glossary go into the zip too</div></div><button class="sw" data-sw id="swRepVocab"></button></div>`; wireNewSwitches(m); } }
if($('#setReport')) $('#setReport').onclick=async()=>{ const b=bridge(); if(!b){ toast('Reports are saved in the app.'); return; }
  const on=s=>$(s).classList.contains('on');
  try{ const r=await b.report_problem(on('#swRepTranscripts'),on('#swRepVocab'));
    if(r.ok){ $('#repStatus').textContent='Saved: '+r.path; toast('Report saved'); } else if(!r.cancelled) toast(r.message||'Could not save the report'); }
  catch(e){ console.error('report_problem',e); toast('Could not save the report'); } };
/* ---- VOICE-1 prototype: read the English subtitles aloud with the system voice (off by default). It does
   not mute or turn down the meeting audio yet, so both are heard; subtitles keep showing every line.
   VOICE-8: natural voice (Kokoro, download once). VOICE-7: Dolmi's headphone mix and its setup. ---- */
{ const m=settingsMount('audio.voice');
  if(m){ m.innerHTML=`<div class="set" data-kw="voice read aloud speak translation tts text to speech headset experimental"><div><div class="lab">Read translations aloud <span class="mtag">experimental</span></div><div class="ps" id="voiceHint">Speaks each English line with the system voice. The meeting audio is not muted or turned down yet, so you hear both. Lines that fall behind are skipped; subtitles show everything</div></div><button class="sw" id="swVoice" aria-label="Read translations aloud"></button></div>
    <div class="set" data-kw="voice clause faster speak sooner latency delay experimental"><div><div class="lab">Speak clause by clause <span class="mtag">experimental</span></div><div class="ps">Starts speaking each part of a sentence as soon as it is clear, instead of waiting for the whole line. Faster, but the voice may differ slightly from the final subtitle</div></div><button class="sw" id="swVoiceClauses" aria-label="Speak clause by clause"></button></div>
    <div class="set" data-kw="natural voice kokoro tts download human sounding experimental"><div><div class="lab">Natural voice <span class="mtag">experimental</span></div><div class="ps" id="voiceNatHint">A more human English voice that runs on this computer. Falls back to the system voice when it is not downloaded</div></div><div style="display:flex;gap:8px;align-items:center"><button class="btn ghost" id="voiceDl" hidden>Download</button><button class="sw" data-sw id="swVoiceNatural" aria-label="Natural voice"></button></div></div>
    <div class="set" data-kw="headphone mix original german safety net duck quiet translated voice experimental"><div><div class="lab">Headphone mix <span class="mtag">experimental</span></div><div class="ps">Dolmi plays the voice on your headphones with the original speech quietly underneath, turned down while the voice speaks. Hold <span id="voiceHoldKey">Ctrl+Alt+Shift+O</span> to hear the original</div></div><button class="sw" data-sw id="swVoiceMix" aria-label="Headphone mix"></button></div>
    <div class="set" data-kw="headphones output device headset bluetooth voice mix setup"><div><div class="lab">Headphones</div><div class="ps" id="voiceCheck">Pick the headphones you wear in the meeting</div></div><div style="display:flex;gap:8px;align-items:center"><button class="btn ghost" id="voiceTest">Test tone</button><select class="field" id="setVoiceOut"><option value="">Default output</option></select></div></div>
    <div class="set" data-kw="original level safety net german volume quiet slider"><div><div class="lab">Original underneath</div><div class="ps">How loud the original speech plays under the voice. 0 % is English only</div></div><div style="display:flex;gap:10px;align-items:center"><input type="range" id="setVoiceSafety" min="0" max="30" value="10" style="accent-color:var(--ac);width:150px" aria-label="Original underneath"><span class="mtag" id="setVoiceSafetyVal">10%</span></div></div>
    <div class="set" data-kw="stereo split left right ear english german"><div><div class="lab">English left, original right</div><div class="ps">Each in its own ear</div></div><button class="sw" data-sw id="swVoiceSplit" aria-label="English left, original right"></button></div>
    <div class="set" data-kw="name chime alert my name called mentioned"><div><div class="lab">Chime when I am named</div><div class="ps">Your name as people say it, several separated by commas. Dolmi chimes when it appears in what they say</div></div><input class="field" id="setVoiceNames" placeholder="e.g. Anna, Anni" spellcheck="false" maxlength="120" style="width:200px;max-width:100%"></div>`;
    wireNewSwitches(m); } }
async function voiceHint(){ const b=bridge(), h=$('#voiceHint'); if(!b||!h||!b.voice_state) return;
  try{ const v=await b.voice_state(); if(!v.available) h.textContent='No system voice was found on this computer, so nothing can be read aloud'; }catch(e){ console.error('voice state',e); } }
async function voiceSetup(){ const b=bridge(); if(!b||!b.voice_setup) return;
  try{ const v=await b.voice_setup(), s=await b.state(), sel=$('#setVoiceOut');
    if(sel){ const cur=s.voiceOutDevice||''; sel.innerHTML='<option value="">Default output</option>'+(v.devices||[]).map(d=>`<option value="${esc(d)}">${esc(d)}</option>`).join('');
      if(cur&&!(v.devices||[]).includes(cur)) sel.insertAdjacentHTML('beforeend',`<option value="${esc(cur)}">${esc(cur)}</option>`); sel.value=cur; }
    const c=$('#voiceCheck'); if(c){ c.textContent=v.check.message; c.style.color=v.check.level==='ok'?'':'var(--danger)'; }
    const n=v.natural||{}, dl=$('#voiceDl'), nh=$('#voiceNatHint');
    if(dl){ dl.hidden=!!n.installed; dl.disabled=!!n.downloading; dl.textContent=n.downloading?`Downloading… ${n.mb||0} MB`:`Download (${n.sizeMb||94} MB)`; }
    if(nh) nh.textContent=!n.installed?'A more human English voice that runs on this computer. Download it once; until then the system voice speaks'
      :!n.deps?'Downloaded, but this build cannot run it yet, so the system voice speaks':'A more human English voice that runs on this computer. Falls back to the system voice if it fails';
  }catch(e){ console.error('voice setup',e); } }
if($('#swVoice')) $('#swVoice').onclick=async()=>{ const sw=$('#swVoice'), on=!sw.classList.contains('on'), b=bridge(); sw.classList.toggle('on',on);
  if(b&&!await b.set_setting('voice_readout',on)){ sw.classList.toggle('on',!on); return; }
  toast(on?'Reading translations aloud (experimental)':'Reading aloud off'); voiceHint(); };
if($('#swVoiceClauses')) $('#swVoiceClauses').onclick=async()=>{ const sw=$('#swVoiceClauses'), on=!sw.classList.contains('on'), b=bridge(); sw.classList.toggle('on',on);
  if(b&&!await b.set_setting('voice_clauses',on)){ sw.classList.toggle('on',!on); return; }
  toast(on?'Speaking clause by clause (experimental)':'Speaking whole lines'); };
[['#swVoiceNatural','voice_natural'],['#swVoiceMix','voice_mix'],['#swVoiceSplit','voice_split']].forEach(([sel,key])=>{ const e=$(sel); if(e) e.addEventListener('click',async()=>{
  const b=bridge(); if(!b) return; const on=e.classList.contains('on'); if(!await b.set_setting(key,on)){ e.classList.toggle('on',!on); return; } voiceSetup(); }); });
if($('#setVoiceOut')) $('#setVoiceOut').addEventListener('change',async()=>{ const b=bridge(); if(b){ await b.set_setting('voice_out_device',$('#setVoiceOut').value); voiceSetup(); } });
if($('#setVoiceSafety')){ const e=$('#setVoiceSafety'); let tm; e.addEventListener('input',()=>{ $('#setVoiceSafetyVal').textContent=e.value+'%'; clearTimeout(tm);
  tm=setTimeout(()=>{ const b=bridge(); if(b) b.set_setting('voice_safety',(+e.value)/100); },150); }); }
if($('#setVoiceNames')) $('#setVoiceNames').addEventListener('change',()=>{ const b=bridge(); if(b) b.set_setting('voice_names',$('#setVoiceNames').value); });
if($('#voiceTest')) $('#voiceTest').onclick=async()=>{ const b=bridge(); if(!b) return; const r=await b.voice_test_tone(); toast(r.ok?'Playing a test tone on your headphones':r.message); };
if($('#voiceDl')) $('#voiceDl').onclick=async()=>{ const b=bridge(); if(!b) return; if(await b.download_voice_model()){ toast('Downloading the natural voice'); voiceSetup(); } };
{ const pp=window.dolmi.onModelProgress, pd=window.dolmi.onModelDone, pn=window.dolmi.onNameHeard;
  window.dolmi.onModelProgress=(k,mb,total)=>{ if(k==='kokoro'){ const dl=$('#voiceDl'); if(dl) dl.textContent=`Downloading… ${mb} of ${total} MB`; return; } if(pp) pp(k,mb,total); };
  window.dolmi.onModelDone=(k,err)=>{ if(k==='kokoro'){ toast(err||'Natural voice downloaded'); voiceSetup(); return; } if(pd) pd(k,err); };
  window.dolmi.onNameHeard=de=>{ if(pn) pn(de); toast('Someone said your name'); }; }
async function voiceBoot(){ const b=bridge(); if(!b) return; try{ const s=await b.state(); setSw('#swVoice',!!s.voiceReadout); setSw('#swVoiceClauses',!!s.voiceClauses);
  setSw('#swVoiceNatural',!!s.voiceNatural); setSw('#swVoiceMix',!!s.voiceMix); setSw('#swVoiceSplit',!!s.voiceSplit);
  const pct=Math.round((s.voiceSafety??0.1)*100); if($('#setVoiceSafety')){ $('#setVoiceSafety').value=pct; $('#setVoiceSafetyVal').textContent=pct+'%'; }
  if($('#setVoiceNames')) $('#setVoiceNames').value=s.voiceNames||'';
  if($('#voiceHoldKey')&&s.shortcuts&&s.shortcuts.hold_original) $('#voiceHoldKey').textContent=fmtCombo(s.shortcuts.hold_original);
  voiceHint(); voiceSetup(); }catch(e){ console.error('voice boot',e); } }
document.addEventListener('DOMContentLoaded',()=>{ if(bridge()) voiceBoot(); else window.addEventListener('pywebviewready',voiceBoot); });
/* ---- VOICE-5 (Windows) / VOICE-6 (Mac 14.2+, process tap), experimental, off by default: 'English only' sends Teams/Zoom/Chrome/Edge to a
   second speaker that Dolmi mutes, captures them from there, and puts everything back on Stop/exit,
   after a crash at the next start, or with Restore audio. Without a second speaker it falls back. ---- */
{ const m=settingsMount('audio.voice');
  if(m){ m.insertAdjacentHTML('beforeend',`<div class="set" data-kw="english only mute meeting german original route speaker experimental" id="eoRow" hidden><div><div class="lab">English only <span class="mtag">experimental</span></div><div class="ps" id="eoHint">Sends the meeting apps to the speaker below and mutes it, so you hear only the English voice. Dolmi still listens to the meeting</div></div><button class="sw" id="swEnglishOnly" aria-label="English only"></button></div>
    <div class="set" data-kw="english only speaker unheard device route" id="eoDevRow" hidden><div><div class="lab">Speaker for the meeting</div><div class="ps">Not the one you listen on. Dolmi mutes it while English only is on</div></div><select class="field" id="setEoDevice" aria-label="Speaker for the meeting"><option value="">Pick a speaker</option></select></div>
    <div class="set" data-kw="restore audio routing undo speaker meeting" id="eoRestoreRow" hidden><div><div class="lab">Restore audio</div><div class="ps" id="eoRestoreHint">Puts the meeting apps and the speaker back the way they were</div></div><button class="btn ghost" id="eoRestore">Restore audio</button></div>`); } }
async function englishOnlyRefresh(){ const b=bridge(); if(!b||!b.english_only_state) return;
  try{ const v=await b.english_only_state(); if(!v.available) return;
    const tap=v.mode==='tap';            // VOICE-6 Mac: the meeting app is muted while tapped, no speaker to pick
    $('#eoRow').hidden=false; $('#eoDevRow').hidden=tap||!v.on; $('#eoRestoreRow').hidden=tap||!(v.on||v.pending);
    setSw('#swEnglishOnly',v.on);
    const sel=$('#setEoDevice'); sel.innerHTML='<option value="">Pick a speaker</option>'+v.devices.map(d=>`<option value="${esc(d.id)}">${esc(d.name)}${d.default?' (default)':''}</option>`).join('');
    sel.value=v.device||'';
    $('#eoHint').textContent=v.notice||(tap?'Mutes the meeting apps while Dolmi listens to them, so you hear only the English voice. Their sound comes back when you stop'
      :'Sends the meeting apps to the speaker below and mutes it, so you hear only the English voice. Dolmi still listens to the meeting');
  }catch(e){ console.error('english only state',e); } }
if($('#swEnglishOnly')) $('#swEnglishOnly').onclick=async()=>{ const sw=$('#swEnglishOnly'), on=!sw.classList.contains('on'), b=bridge(); sw.classList.toggle('on',on);
  if(b&&!await b.set_setting('english_only',on)){ sw.classList.toggle('on',!on); return; }
  toast(on?'English only on: takes effect at the next Start':'English only off: audio restored'); englishOnlyRefresh(); };
if($('#setEoDevice')) $('#setEoDevice').onchange=async()=>{ const b=bridge(); if(b) await b.set_setting('english_only_device',$('#setEoDevice').value); };
if($('#eoRestore')) $('#eoRestore').onclick=async()=>{ const b=bridge(); if(!b) return;
  try{ const r=await b.restore_audio(); toast(r.ok?'Audio restored':'Restored what is running now; the rest is restored when those apps start'); }catch(e){ console.error('restore_audio',e); toast('Could not restore the audio'); }
  englishOnlyRefresh(); };
window.dolmi=window.dolmi||{}; window.dolmi.onRouting=msg=>{ if(msg) toast(msg); englishOnlyRefresh(); };
document.addEventListener('DOMContentLoaded',()=>{ if(bridge()) englishOnlyRefresh(); else window.addEventListener('pywebviewready',englishOnlyRefresh); });
/* STR-7: local-only mode switch and the "Local only" chip; STR-9: the "Using this PC" (cloud down) chip */
function statusChips(){ const t=$('.top'); if(t) t.classList.toggle('has-status',!!$('#chipLocal:not([hidden]),#chipCloud:not([hidden])')); }
function applyLocalOnly(on){ setSw('#swLocalOnly',on); const c=$('#chipLocal'); if(c) c.hidden=!on; statusChips(); }
if($('#swLocalOnly')) $('#swLocalOnly').addEventListener('click',async()=>{ const on=$('#swLocalOnly').classList.contains('on'), b=bridge();
  if(b&&!await b.set_setting('local_only',on)){ applyLocalOnly(!on); return; } applyLocalOnly(on); toast(on?'Local only: nothing leaves this computer':'Local only off'); });
wireSwitch('#swUnsure','unsure_marks'); wireSwitch('#swFailover','cloud_failover');
async function localOnlyBoot(){ const b=bridge(); if(!b) return; try{ const s=await b.state(); applyLocalOnly(!!s.localOnly); setSw('#swUnsure',s.unsureMarks); setSw('#swFailover',s.cloudFailover); }catch(e){ console.error('local only boot',e); } }
if(bridge()) localOnlyBoot(); else window.addEventListener('pywebviewready',localOnlyBoot);
window.dolmi=window.dolmi||{}; window.dolmi.onCloudStatus=st=>{ const c=$('#chipCloud'); if(c) c.hidden=st!=='local'; statusChips(); };
