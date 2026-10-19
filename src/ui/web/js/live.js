/* Dolmi web UI — Live view: waveform, caption feed (appendPair, showDraft, addFinal, resetFeed), stats, start/stop.
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
function resetFeed(){ brows.innerHTML=''; lines=0;words=0; draftEl=null; $('#sLines').textContent=0; $('#sWords').textContent=0; }
function appendPair(ts,de,en){ clearIdle(); $$('.pair.cur').forEach(p=>p.classList.remove('cur'));
  const p=document.createElement('div'); p.className='pair cur pop';
  p.innerHTML=`<div class="c de"><span class="ts">${esc(ts)}</span>${esc(de)}</div><div class="c en"><span class="ts">&nbsp;</span>${esc(en)}</div>`;
  brows.append(p); brows.scrollTop=brows.scrollHeight; }
function addFinal(ts,de,en){ if(draftEl){draftEl.remove();draftEl=null;} appendPair(ts,de,en);
  lines++; words+=en.split(/\s+/).filter(Boolean).length; $('#sLines').textContent=lines; $('#sWords').textContent=words; }
function showDraft(de,en){ if(!de&&!en){ if(draftEl){draftEl.remove();draftEl=null;} return; } clearIdle();
  if(!draftEl){ draftEl=document.createElement('div'); draftEl.className='pair cur'; draftEl.style.opacity='.7'; brows.append(draftEl); }
  draftEl.innerHTML=`<div class="c de"><span class="ts">…</span>${esc(de)}</div><div class="c en"><span class="ts">&nbsp;</span>${esc(en)}<span class="caret"></span></div>`;
  brows.scrollTop=brows.scrollHeight; }

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
