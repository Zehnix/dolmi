/* Dolmi web UI — Live view: waveform, caption feed (appendPair, showDraft, addFinal, resetFeed; stick-to-bottom, lag, times), stats, start/stop.
   Classic script (not a module): top-level names are shared with the other js/*.js files.
   Load order (index.html): core, live, meetings, assistant, vocab, settings, onboarding. */
/* waveform */
const track=$('#track'),N=44; track.innerHTML=Array.from({length:N},()=>'<i></i>').join('');
const wb=[...track.children]; let lvl=new Array(N).fill(.2),listening=false,raf=0,speaking=0;
function wave(){for(let i=0;i<N;i++){const t=listening?(.16+Math.random()*(speaking?.82:.26)):.2;lvl[i]+=(t-lvl[i])*.35;wb[i].style.transform=`scaleY(${lvl[i].toFixed(3)})`;wb[i].classList.toggle('hot',listening&&lvl[i]>.7);}if(listening||lvl.some(l=>Math.abs(l-.2)>.01))raf=requestAnimationFrame(wave);}

/* ---- live caption rendering ---- */
let lines=0,words=0,draftEl=null,engineLabel='models load on Start'; const brows=$('#brows');
const idleHTML=$('#idle')?$('#idle').outerHTML:'';
function showIdle(){ if(!document.getElementById('idle')&&idleHTML){ brows.insertAdjacentHTML('beforeend',idleHTML); icons(); } }
function clearIdle(){ const i=document.getElementById('idle'); if(i) i.remove(); }

/* follow the newest line only while the reader is at the bottom: scrolling up un-sticks the feed and
   the "Jump to latest" pill counts the finals that arrived since, until it (or scrolling down) returns to live.
   Only the reader un-sticks it: autoTop is where follow() last put the feed, so a feed that fell behind
   while hidden (another tab open: display:none, follow() cannot scroll) or after a window resize stays live. */
const STICK_PX=48, pill=$('#livePill'); let stuck=true,unseen=0,autoTop=0;
const shown=()=>brows.clientHeight>0;
const nearBottom=()=>brows.scrollHeight-brows.scrollTop-brows.clientHeight<=STICK_PX;
const scrolledUp=()=>shown()&&brows.scrollTop<autoTop-1&&!nearBottom();
/* checked before each change too: a scroll-up and a new line can land in the same frame, before the scroll event */
function unstickIfScrolled(){ if(stuck&&scrolledUp()){ stuck=false; setPill(); } }
function follow(){ if(!stuck||!shown()) return; brows.scrollTop=brows.scrollHeight; autoTop=brows.scrollTop; }
function setPill(){ if(!pill) return; pill.hidden=stuck; $('#livePillN').textContent=unseen?` (${unseen} new)`:''; }
brows.addEventListener('scroll',()=>{ if(!shown()) return;
  if(stuck){ if(scrolledUp()){ stuck=false; setPill(); } else autoTop=Math.max(autoTop,brows.scrollTop); return; }
  if(nearBottom()){ stuck=true; unseen=0; autoTop=brows.scrollTop; setPill(); } });
if(pill) pill.onclick=()=>{ stuck=true; unseen=0; follow(); setPill(); };
/* catch up when the Live view is shown again (core.js's nav handler has already shown it) or the window changes size */
$$('.nav button[data-v="live"]').forEach(b=>b.addEventListener('click',follow));
if(window.ResizeObserver) new ResizeObserver(()=>follow()).observe(brows);

/* times: speech start as HH:MM:SS plus "+mm:ss" into the meeting, from the line's meta (epoch seconds) */
const two=n=>String(n).padStart(2,'0');
function clock(epoch){ const d=new Date(epoch*1000); return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`; }
function offsetText(sec){ const s=Math.max(0,Math.floor(sec)),h=Math.floor(s/3600),m=Math.floor(s%3600/60); return '+'+(h?h+':'+two(m):two(m))+':'+two(s%60); }
function stamp(ts,meta){ const m=meta||{}; const t=typeof m.start==='number'?clock(m.start):(ts||'');
  return t+(typeof m.offset==='number'?' · '+offsetText(m.offset):''); }

/* lag: end of speech -> line on screen, measured in Python (meta.lag); the figure is the median of the
   last 5 finals (meta.lag_median), '–' until one was measured; the latest draft's lag is in the tooltip */
let lagLine=null,lagDraft=null,lagSplit=null;
const secs=s=>(s<10?s.toFixed(1):String(Math.round(s)))+'s';
function showLag(){ const el=$('#sLag'); if(!el) return; el.textContent=lagLine==null?'–':secs(lagLine);
  el.parentElement.title='Lag: from the end of speech to the line on screen (median of the last 5 lines)'+(lagDraft==null?'':` · live draft ${secs(lagDraft)}`)
    +(lagSplit?` · last line: ${secs(lagSplit[0])} waiting for the pause, ${secs(lagSplit[1])} recognizing and translating`:''); }

function resetFeed(){ brows.innerHTML=''; lines=0;words=0; draftEl=null; $('#sLines').textContent=0; $('#sWords').textContent=0;
  stuck=true; unseen=0; autoTop=0; setPill(); lagLine=lagDraft=lagSplit=null; showLag(); spkReset(); }
function appendPair(ts,de,en,meta){ unstickIfScrolled(); clearIdle(); $$('.pair.cur').forEach(p=>p.classList.remove('cur'));
  const p=document.createElement('div'), t=esc(stamp(ts,meta)); p.className='pair cur pop';
  p.innerHTML=`<div class="c de"><span class="ts">${t}</span>${esc(de)}</div><div class="c en"><span class="ts">${t}</span>${esc(en)}</div>`;
  spkAttach(p,meta);
  brows.append(p); follow(); }
function addFinal(ts,de,en,meta){ if(draftEl){draftEl.remove();draftEl=null;} appendPair(ts,de,en,meta);
  lines++; words+=en.split(/\s+/).filter(Boolean).length; $('#sLines').textContent=lines; $('#sWords').textContent=words;
  if(!stuck){ unseen++; setPill(); }
  if(meta&&meta.lag_policy!=null&&meta.lag_compute!=null) lagSplit=[meta.lag_policy,meta.lag_compute];
  if(meta&&meta.lag_median!=null){ lagLine=meta.lag_median; showLag(); } }
/* LIVE-4: a draft that ended as nothing usable stays as a dim 'not understood' row (the reason in its tooltip)
   instead of vanishing; it is not a line and is not counted. resetFeed clears it with the rest of the feed. */
function markDropped(el,reason){ el.className='pair dropped'; el.style.opacity='.4'; el.title='Not understood: '+reason;
  el.querySelectorAll('.caret').forEach(c=>c.remove());
  const en=el.querySelector('.c.en'); if(en) en.insertAdjacentHTML('beforeend',' <i class="nu">(not understood)</i>'); }
/* STR-2: a draft is committed words (solid, never rewritten) + a tentative tail (dim, italic). Older
   producers send no split: then the whole draft is the tail. Only changed parts are rewritten, so the
   committed words never jump. */
function draftParts(text,committed,tentative){
  if(typeof committed!=='string'&&typeof tentative!=='string') return ['',text||''];
  return [committed||'',tentative||''];
}
function setPart(el,c,t){ if(el._c!==c){ el.querySelector('.cm').textContent=c+(c&&t?' ':''); el._c=c; }
  if(el._t!==t){ el.querySelector('.tent').textContent=t; el._t=t; } }
function showDraft(de,en,meta){ const m=meta||{}; if(m.lag!=null){ lagDraft=m.lag; showLag(); }
  if(!de&&!en){ if(draftEl){ if(m.dropped) markDropped(draftEl,m.dropped); else draftEl.remove(); draftEl=null; } return; } unstickIfScrolled(); clearIdle();
  if(!draftEl){ draftEl=document.createElement('div'); draftEl.className='pair cur draft';
    draftEl.innerHTML='<div class="c de"><span class="ts"></span><span class="cm"></span><span class="tent"></span></div><div class="c en"><span class="ts"></span><span class="cm"></span><span class="tent"></span><span class="caret"></span></div>';
    brows.append(draftEl); }
  const t=typeof m.start==='number'?stamp('',m)+' …':'…';
  draftEl.querySelectorAll('.ts').forEach(x=>{ if(x.textContent!==t) x.textContent=t; });
  const [dc,dt]=draftParts(de,m.committed,m.tentative), [ec,et]=draftParts(en,m.en_committed,m.en_tentative);
  setPart(draftEl.querySelector('.c.de'),dc,dt); setPart(draftEl.querySelector('.c.en'),ec,et);
  follow(); }

function setLive(on,label){
  listening=on; $('#bhead').classList.toggle('on',on);
  $('#go').innerHTML=`<span class="ic"><i data-lucide="${on?'square':'play'}"></i></span>`; icons();
  $('#status').innerHTML=(on?'Translating':statusWord())+'<b>'+esc(label||engineLabel)+'</b>';
  $('#hero').innerHTML=on?`Listening in<br><em>${LANG[curLang].name}.</em>`:'A clear bridge between<br><em>two languages.</em>';
  track.classList.toggle('on',on); cancelAnimationFrame(raf); wave();
}

/* start/stop — real bridge in the app, demo animation in a plain browser */
let demoTimer=0,di=0,dbusy=false;
const DEMO=[{de:'Ich habe den MCP gerade auf Production getestet.',en:'I just tested the MCP on production.'},{de:'Das war wirklich geil, wie schnell das lief.',en:'It was really awesome how fast it ran.'},{de:'Lena schaut sich das nächste Woche an.',en:'Lena will look at it next week.'}];
async function demoLoop(){ if(dbusy)return; dbusy=true; speaking=1; await sleep(1100); speaking=0; const s=DEMO[di++%DEMO.length]; addFinal(new Date().toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'}),s.de,s.en); dbusy=false; }
$('#go').onclick=()=>{
  const b=bridge();
  if(b){ if(listening){ b.stop_listening(); setLive(false); if(!lines) showIdle(); } else startLive(false); return; }
  if(listening){ clearInterval(demoTimer); setLive(false); resetFeed(); showIdle(); }
  else { resetFeed(); setLive(true); demoLoop(); demoTimer=setInterval(demoLoop,3600); }
};
async function startLive(download){ const b=bridge(); resetFeed(); setLive(true,download?'downloading models…':'loading…');
  const r=await b.start_listening(!!download);
  if(r&&r.missing){ setLive(false); if(!lines) showIdle(); askDownload(r.missing); } }

/* ---- a better subtitle for a line already shown (QA-7 cloud translator): replaced in place ----
   Python sends onLineUpdate(id, en, {source}) for final `id`; the pair keeps its time stamp and gets a
   small "cloud" mark (tooltip: which service). Lines are found by data-line, set when the final arrives. */
function onLineUpdate(id,en,meta){ const p=brows.querySelector(`.pair[data-line="${CSS.escape(String(id))}"]`); if(!p) return false;
  const c=p.querySelector('.c.en'); if(!c) return false; const ts=c.querySelector('.ts');
  const old=c.textContent.slice(ts?ts.textContent.length:0);
  words+=String(en).split(/\s+/).filter(Boolean).length-old.split(/\s+/).filter(Boolean).length; $('#sWords').textContent=words;
  [...c.childNodes].forEach(n=>{ if(n!==ts) n.remove(); }); c.append(document.createTextNode(en));
  const src=(meta&&meta.source)||'cloud'; p.dataset.enSource=src; c.title='Improved by '+src+' (the local translation was shown first)';
  c.classList.add('cloudfix'); return true; }
{ const d=window.dolmi, onLine=d.onLine;
  d.onLine=(ts,de,en,meta)=>{ onLine(ts,de,en,meta); const p=brows.lastElementChild;
    if(p&&p.classList.contains('pair')&&meta&&meta.id!=null) p.dataset.line=meta.id; };
  d.onLineUpdate=(id,en,meta)=>onLineUpdate(id,en,meta); }
/* ---- speaker labels (SPK-4, beta): a coloured chip per line; click it to rename, reassign or merge ----
   Lines keep the speaker id (data-spk), so a rename or merge re-renders every past chip of this meeting.
   Names/colours come from Python (onSpeakers / speaker_list); a low-confidence label is dimmed with "?". */
const SPK_LOW=0.7; let SPK={},SPK_MERGED={},spkMenuEl=null;
(()=>{ const s=document.createElement('style'); s.textContent=`
.spk{display:inline-flex;align-items:center;gap:5px;margin-left:8px;padding:1px 8px 1px 6px;border-radius:999px;border:1px solid var(--line);background:var(--card);color:var(--ink);font:500 10.5px/1.6 var(--mono);cursor:pointer;vertical-align:1px;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.spk:hover{border-color:var(--ac)}.spk i{flex:none;width:7px;height:7px;border-radius:50%;background:var(--spk,var(--faint))}
.spk.low{opacity:.55}.spk.ro{cursor:default}.spk.ro:hover{border-color:var(--line)}
.spkmenu{position:fixed;z-index:70;width:250px;background:var(--card);border:1px solid var(--line);border-radius:16px;box-shadow:var(--sh);padding:12px;font-size:13px;color:var(--ink)}
.spkmenu h4{margin:0 0 8px;font:600 11px var(--mono);color:var(--faint);letter-spacing:.04em;text-transform:uppercase}
.spkmenu .r{display:flex;gap:6px;margin-bottom:10px}.spkmenu .r .field{flex:1;min-width:0;padding:6px 10px}
.spkmenu .opts{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}.spkmenu .opts:last-child{margin-bottom:0}
.spkmenu .opts .spk{margin:0;cursor:pointer}.spkmenu .opts .spk:hover{border-color:var(--ac)}`; document.head.append(s); })();
function spkReset(){ SPK={}; SPK_MERGED={}; spkClose(); }
function spkInfo(id){ return SPK[id]||{id,name:'Speaker '+id,colour:''}; }
/* one chip; attrs adds data-* attributes (menu options), ro = a plain label (Meetings view) */
function spkChip(id,conf,{ro=false,attrs=''}={}){ const s=spkInfo(id), low=typeof conf==='number'&&conf<SPK_LOW;
  return `<button class="spk${low?' low':''}${ro?' ro':''}" data-spk="${esc(id)}" ${attrs} style="--spk:${esc(s.colour||'')}" title="${ro?esc(s.name):'Who said this? Click to rename, reassign or merge'}${low?' (not sure)':''}"><i></i>${esc(s.name)}${low?' ?':''}</button>`; }
function spkAttach(p,meta){ const m=meta||{}; if(m.speaker==null) return; let id=m.speaker;
  while(SPK_MERGED[id]!=null) id=SPK_MERGED[id];   /* a line cut just before a merge */
  p.dataset.spk=id; p.dataset.conf=m.speaker_conf==null?'':m.speaker_conf; if(m.id!=null) p.dataset.line=m.id;
  spkPaint(p); if(!SPK[id]) spkRefresh(); }
function spkPaint(p){ const ts=p.querySelector('.c.en .ts'); if(!ts) return; const old=ts.querySelector('.spk'); if(old) old.remove();
  ts.insertAdjacentHTML('beforeend',spkChip(+p.dataset.spk,p.dataset.conf===''?null:+p.dataset.conf));
  ts.querySelector('.spk').onclick=e=>{ e.stopPropagation(); spkMenu(p,e.currentTarget); }; }
function spkRepaint(){ brows.querySelectorAll('.pair[data-spk]').forEach(spkPaint); }
function spkSet(list){ if(!Array.isArray(list)) return; list.forEach(s=>{ SPK[s.id]=s; }); spkRepaint(); }
async function spkRefresh(){ const b=bridge(); if(!b||!b.speaker_list) return; try{ spkSet(await b.speaker_list()); }catch(e){ console.error('speakers',e); } }
function spkClose(){ if(spkMenuEl){ spkMenuEl.remove(); spkMenuEl=null; } }
document.addEventListener('click',e=>{ if(spkMenuEl&&!spkMenuEl.contains(e.target)) spkClose(); });
document.addEventListener('keydown',e=>{ if(e.key==='Escape') spkClose(); });
function spkMenu(p,chip){ spkClose(); const b=bridge(), id=+p.dataset.spk, me=spkInfo(id);
  const others=Object.values(SPK).filter(s=>s.id!==id);
  const opts=a=>others.map(s=>spkChip(s.id,null,{attrs:`data-a="${a}"`})).join('');
  const m=document.createElement('div'); m.className='spkmenu'; m.setAttribute('role','dialog'); m.setAttribute('aria-label','Speaker');
  m.innerHTML=`<h4>Name (all their lines)</h4><div class="r"><input class="field" maxlength="40" value="${esc(me.name)}" aria-label="Speaker name"><button class="btn ghost" data-a="ren">Save</button></div>`
    +`<h4>This line is someone else</h4><div class="opts">${opts('re')}<button class="spk" data-a="new"><i></i>New speaker</button></div>`
    +(others.length?`<h4>Same person as</h4><div class="opts">${opts('mrg')}</div>`:'');
  document.body.append(m); spkMenuEl=m;
  const r=chip.getBoundingClientRect(); m.style.left=Math.max(8,Math.min(innerWidth-258,r.left))+'px';
  m.style.top=(r.bottom+m.offsetHeight+8<innerHeight?r.bottom+6:Math.max(8,r.top-m.offsetHeight-6))+'px';
  const inp=m.querySelector('input'); inp.focus(); inp.select();
  const done=res=>{ if(res&&res.ok){ if(res.speakers) spkSet(res.speakers); spkClose(); } else toast((res&&res.message)||'Speaker labels work in the app.'); };
  const rename=async()=>{ if(!b) return done(); done(await b.speaker_rename(id,inp.value)); };
  inp.onkeydown=e=>{ if(e.key==='Enter'){ e.preventDefault(); rename(); } };
  m.querySelector('[data-a="ren"]').onclick=rename;
  m.querySelectorAll('[data-a="re"],[data-a="new"]').forEach(x=>x.onclick=async()=>{ if(!b||p.dataset.line==null) return done();
    const res=await b.speaker_reassign(+p.dataset.line,x.dataset.a==='new'?null:+x.dataset.spk);
    if(res&&res.ok){ p.dataset.spk=res.speaker; p.dataset.conf=''; } done(res); });
  m.querySelectorAll('[data-a="mrg"]').forEach(x=>x.onclick=async()=>{ if(!b) return done(); done(await b.speaker_merge(id,+x.dataset.spk)); }); }
{ const d=window.dolmi;
  d.onSpeakers=list=>spkSet(list);
  d.onSpeakerMerge=(from,to)=>{ brows.querySelectorAll(`.pair[data-spk="${+from}"]`).forEach(p=>{ p.dataset.spk=to; }); SPK_MERGED[+from]=+to; delete SPK[from]; spkRepaint(); }; }
/* Settings → Privacy & data: the on/off switch (beta, off by default; used from the next Start) */
document.addEventListener('DOMContentLoaded',()=>{ const slot=typeof settingsMount==='function'&&settingsMount('privacy.speakers'); if(!slot) return;
  slot.innerHTML=`<div class="set" data-kw="speaker labels who said speakers names voices diarization beta"><div><div class="lab">Speaker labels <span class="mtag">beta</span></div><div class="ps" data-wt="Marks who said each line (Speaker 1, 2 …); click a label to name it. A 30 MB voice model runs on this {computer}; voices are compared in memory only, never saved. From the next Start.">Marks who said each line (Speaker 1, 2 …); click a label to name it. A 30 MB voice model runs on this computer; voices are compared in memory only, never saved. From the next Start.</div></div><button class="sw" id="swSpeakers" aria-label="Speaker labels"></button></div>`;
  const sw=$('#swSpeakers');
  sw.onclick=async()=>{ const on=!sw.classList.contains('on'), b=bridge(); sw.classList.toggle('on',on);
    if(b&&!await b.set_setting('speaker_labels',on)){ sw.classList.toggle('on',!on); return; }
    toast(on?(listening?'Speaker labels: from the next Start':'Speaker labels on (beta)'):'Speaker labels off'); };
  const load=async()=>{ const b=bridge(); if(!b) return; try{ const s=await b.state(); sw.classList.toggle('on',!!s.speakerLabels); }catch(e){ console.error('speaker labels',e); } };
  if(bridge()) load(); else window.addEventListener('pywebviewready',load); });
/* Settings → Audio: caption the microphone too (SPK-5, beta, off by default; used from the next Start) */
document.addEventListener('DOMContentLoaded',()=>{ const slot=typeof settingsMount==='function'&&settingsMount('audio.capture_both'); if(!slot) return;
  slot.innerHTML=`<div class="set" data-kw="caption my microphone too both mic and speaker at once you my own voice beta"><div><div class="lab">Caption my microphone too <span class="mtag">beta</span></div><div class="ps">Listens to the speaker and your microphone at once, so your own words get captions. With Speaker labels on, your lines say You. From the next Start.</div></div><button class="sw" id="swCaptureBoth" aria-label="Caption my microphone too"></button></div>`;
  const sw=$('#swCaptureBoth');
  sw.onclick=async()=>{ const on=!sw.classList.contains('on'), b=bridge(); sw.classList.toggle('on',on);
    if(b&&!await b.set_setting('capture_both',on)){ sw.classList.toggle('on',!on); return; }
    toast(on?(listening?'Microphone too: from the next Start':'Microphone captions on (beta)'):'Microphone captions off'); };
  const load=async()=>{ const b=bridge(); if(!b) return; try{ const s=await b.state(); sw.classList.toggle('on',!!s.captureBoth); }catch(e){ console.error('capture both',e); } };
  if(bridge()) load(); else window.addEventListener('pywebviewready',load); });
