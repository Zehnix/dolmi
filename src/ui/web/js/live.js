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
   the "Jump to latest" pill counts the finals that arrived since, until it (or scrolling down) returns to live */
const STICK_PX=48, pill=$('#livePill'); let stuck=true,unseen=0;
const nearBottom=()=>brows.scrollHeight-brows.scrollTop-brows.clientHeight<=STICK_PX;
/* checked before each change too: a scroll-up and a new line can land in the same frame, before the scroll event */
function unstickIfScrolled(){ if(stuck&&!nearBottom()){ stuck=false; setPill(); } }
function follow(){ if(stuck) brows.scrollTop=brows.scrollHeight; }
function setPill(){ if(!pill) return; pill.hidden=stuck; $('#livePillN').textContent=unseen?` (${unseen} new)`:''; }
brows.addEventListener('scroll',()=>{ const s=nearBottom(); if(s===stuck) return; stuck=s; if(s) unseen=0; setPill(); });
if(pill) pill.onclick=()=>{ stuck=true; unseen=0; follow(); setPill(); };

/* times: speech start as HH:MM:SS plus "+mm:ss" into the meeting, from the line's meta (epoch seconds) */
const two=n=>String(n).padStart(2,'0');
function clock(epoch){ const d=new Date(epoch*1000); return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`; }
function offsetText(sec){ const s=Math.max(0,Math.floor(sec)),h=Math.floor(s/3600),m=Math.floor(s%3600/60); return '+'+(h?h+':'+two(m):two(m))+':'+two(s%60); }
function stamp(ts,meta){ const m=meta||{}; const t=typeof m.start==='number'?clock(m.start):(ts||'');
  return t+(typeof m.offset==='number'?' · '+offsetText(m.offset):''); }

/* lag: end of speech -> line on screen, measured in Python (meta.lag); the figure is the median of the
   last 5 finals (meta.lag_median), '–' until one was measured; the latest draft's lag is in the tooltip */
let lagLine=null,lagDraft=null;
const secs=s=>(s<10?s.toFixed(1):String(Math.round(s)))+'s';
function showLag(){ const el=$('#sLag'); if(!el) return; el.textContent=lagLine==null?'–':secs(lagLine);
  el.parentElement.title='Lag: from the end of speech to the line on screen (median of the last 5 lines)'+(lagDraft==null?'':` · live draft ${secs(lagDraft)}`); }

function resetFeed(){ brows.innerHTML=''; lines=0;words=0; draftEl=null; $('#sLines').textContent=0; $('#sWords').textContent=0;
  stuck=true; unseen=0; setPill(); lagLine=lagDraft=null; showLag(); }
function appendPair(ts,de,en,meta){ unstickIfScrolled(); clearIdle(); $$('.pair.cur').forEach(p=>p.classList.remove('cur'));
  const p=document.createElement('div'), t=esc(stamp(ts,meta)); p.className='pair cur pop';
  p.innerHTML=`<div class="c de"><span class="ts">${t}</span>${esc(de)}</div><div class="c en"><span class="ts">${t}</span>${esc(en)}</div>`;
  brows.append(p); follow(); }
function addFinal(ts,de,en,meta){ if(draftEl){draftEl.remove();draftEl=null;} appendPair(ts,de,en,meta);
  lines++; words+=en.split(/\s+/).filter(Boolean).length; $('#sLines').textContent=lines; $('#sWords').textContent=words;
  if(!stuck){ unseen++; setPill(); }
  if(meta&&meta.lag_median!=null){ lagLine=meta.lag_median; showLag(); } }
function showDraft(de,en,meta){ if(meta&&meta.lag!=null){ lagDraft=meta.lag; showLag(); }
  if(!de&&!en){ if(draftEl){draftEl.remove();draftEl=null;} return; } unstickIfScrolled(); clearIdle();
  if(!draftEl){ draftEl=document.createElement('div'); draftEl.className='pair cur'; draftEl.style.opacity='.7'; brows.append(draftEl); }
  const t=esc(meta&&typeof meta.start==='number'?stamp('',meta)+' …':'…');
  draftEl.innerHTML=`<div class="c de"><span class="ts">${t}</span>${esc(de)}</div><div class="c en"><span class="ts">${t}</span>${esc(en)}<span class="caret"></span></div>`;
  follow(); }
/* core.js forwards only the first arguments; the caption hooks also take the timing meta */
window.dolmi.onLine=(ts,de,en,meta)=>addFinal(ts,de,en,meta);
window.dolmi.onDraft=(de,en,meta)=>showDraft(de,en,meta);

function setLive(on,label){
  listening=on; $('#bhead').classList.toggle('on',on);
  $('#go').innerHTML=`<span class="ic"><i data-lucide="${on?'square':'play'}"></i></span>`; icons();
  $('#status').innerHTML=(on?'Translating':'Ready')+'<b>'+esc(label||engineLabel)+'</b>';
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
