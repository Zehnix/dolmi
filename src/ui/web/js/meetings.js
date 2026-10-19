/* Dolmi web UI — Meetings view: list, transcript, select/archive/delete, export, copy, summary.
   Classic script (not a module): top-level names are shared with the other js/*.js files.
   Load order (index.html): core, live, meetings, assistant, vocab, settings, onboarding. */
let mArch=false,currentPairs=[];

/* ---- summary modal (opened before the request, so no early chunk is lost) ---- */
let sumBox=null;
function sumOpen(){ let s=document.getElementById('sumModal'); if(!s){ s=document.createElement('div'); s.id='sumModal'; s.className='scrim'; s.innerHTML='<div class="onb" style="max-width:600px"><h3 style="font-size:24px">Meeting summary</h3><div id="sumText" style="white-space:pre-wrap;margin-top:12px;max-height:50vh;overflow:auto;font-size:14px;line-height:1.6"></div><div class="foot" style="justify-content:flex-end"><button class="btn ghost" id="sumClose">Close</button></div></div>'; document.body.append(s); s.onclick=e=>{ if(e.target.id==='sumModal') s.hidden=true; }; s.querySelector('#sumClose').onclick=()=>s.hidden=true; } s.hidden=false; sumBox=s.querySelector('#sumText'); sumBox.textContent='Writing the summary…'; }
function sumClose(){ const s=document.getElementById('sumModal'); if(s) s.hidden=true; }
function sumChunk(p){ if(!sumBox) return; if(sumBox.textContent==='Writing the summary…') sumBox.textContent=''; sumBox.textContent+=p; sumBox.scrollTop=sumBox.scrollHeight; }
function sumDone(err){ if(sumBox&&err) sumBox.textContent=err; }
async function runSummary(meeting){
  const b=bridge(); if(!b){ toast('Summaries run in the app.'); return; }
  sumOpen();
  try{ const r=await b.summarize(meeting||null); if(!r.ok){ sumClose(); toast(r.message); } }
  catch(e){ sumClose(); toast('Summary failed.'); }
}

/* ---- meetings ---- */
/* APP-9: the list pane has a search over every transcript (case and accents ignored) and a date filter;
   rows show each meeting's title (typed by the user, from its summary, or its first meaningful line). */
const M_WHEN=[['','Any time'],['0','Today'],['7','Last 7 days'],['30','Last 30 days'],['365','Last 12 months'],['day','On a day…']];
function mListShell(){ const list=$('#mList'); if($('#mRows')) return;
  list.innerHTML=`<div class="selbar mhead" style="flex-direction:column;align-items:stretch;gap:8px">
    <label class="search" style="margin:0"><i data-lucide="search" style="width:15px;height:15px"></i><input id="mSearch" type="search" placeholder="Search all meetings" autocomplete="off" spellcheck="false" aria-label="Search all meetings"></label>
    <div style="display:flex;gap:8px;min-width:0"><select class="field" id="mWhen" aria-label="Date" style="flex:1;min-width:0;width:auto">${M_WHEN.map(([v,l])=>`<option value="${v}">${l}</option>`).join('')}</select><input class="field" id="mDay" type="date" aria-label="Day" hidden style="flex:1;min-width:0;width:auto"></div>
    <div id="mSelInfo" style="display:flex;align-items:center;gap:10px"></div></div>
    <div id="mRows" style="display:flex;flex-direction:column;gap:5px"></div>`;
  icons();
  let tm; $('#mSearch').addEventListener('input',()=>{ clearTimeout(tm); tm=setTimeout(loadMeetings,250); });
  $('#mSearch').addEventListener('keydown',e=>{ if(e.key==='Escape'&&$('#mSearch').value){ e.stopPropagation(); $('#mSearch').value=''; loadMeetings(); } });
  $('#mWhen').onchange=()=>{ $('#mDay').hidden=$('#mWhen').value!=='day'; if($('#mWhen').value!=='day') loadMeetings(); else $('#mDay').focus(); };
  $('#mDay').onchange=loadMeetings; }
const isoDay=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
function mDates(){ const w=$('#mWhen')?$('#mWhen').value:''; if(!w) return ['',''];
  if(w==='day'){ const d=$('#mDay').value; return d?[d,d]:['','']; }
  const from=new Date(); from.setDate(from.getDate()-(+w)); return [isoDay(from),isoDay(new Date())]; }
let mQuery='';
async function loadMeetings(){
  const b=bridge(), list=$('#mList');
  if(!b){ list.innerHTML='<div style="padding:16px;color:var(--faint)">Meetings appear here when running in the app.</div>'; return; }
  mListShell();
  const q=$('#mSearch').value.trim(), [from,to]=mDates(), filtered=!!(q||from);
  mQuery=q;
  const items=filtered?await b.search_meetings(q,mArch,from,to):await b.list_meetings(mArch);
  if(q!==$('#mSearch').value.trim()) return;                /* a newer search is on its way */
  mItems=items; mSel=new Set([...mSel].filter(n=>items.some(m=>m.name===n)));
  if(!items.length){ renderMeetingList(null); $('#mRows').innerHTML='<div style="padding:16px;color:var(--faint)">'+(filtered?'No meeting matches.':mArch?'No archived meetings.':'No meetings yet.')+'</div>';
    currentPairs=[]; $('#mDetail').innerHTML=`<div style="margin:auto;color:var(--faint)">${filtered?'Nothing found. Try other words or dates.':'Nothing saved yet.'}</div>`; currentMeeting=null; return; }
  const keep=items.some(m=>m.name===currentMeeting)?currentMeeting:items[0].name;
  renderMeetingList(keep);
  await openMeeting(keep);
  showHit(keep);
}
/* meeting list with checkboxes: tick several, then Archive/Delete act on all of them */
let mItems=[],mSel=new Set();
const mTitle=m=>m.title||`${m.day} ${m.time}`;
const mFold=s=>String(s||'').normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
const mWords=()=>mQuery.split(/\s+/).map(mFold).filter(Boolean);
/* the line of a hit that shows the search words: German, or English when the words are only there */
function hitLine(hit){ const n=t=>mWords().filter(w=>mFold(t).includes(w)).length; return n(hit.en)>n(hit.de)?hit.en:hit.de; }
/* mark the words on the plain text, then escape each piece, so a mark never lands inside an HTML entity */
function markHit(text){ const plain=String(text||''), f=mFold(plain); if(f.length!==plain.length) return esc(plain);
  const on=new Array(plain.length).fill(false);
  mWords().forEach(w=>{ for(let i=f.indexOf(w);i>=0;i=f.indexOf(w,i+w.length)) on.fill(true,i,i+w.length); });
  let h='', i=0; while(i<plain.length){ let j=i; while(j<plain.length&&on[j]===on[i]) j++; const part=esc(plain.slice(i,j)); h+=on[i]?`<mark>${part}</mark>`:part; i=j; }
  return h; }
function renderMeetingList(active){
  const n=mSel.size, info=$('#mSelInfo');
  if(info) info.innerHTML=`${n?`<b>${n} selected</b>`:(mQuery?`${mItems.length} found`:'Tick to select several')}<span class="sp" style="flex:1"></span>${n<mItems.length?'<button id="mSelAll">Select all</button>':''}${n?'<button id="mSelNone">Clear</button>':''}`;
  const rows=$('#mRows'); if(!rows) return;
  rows.innerHTML=mItems.map(m=>{ const on=mSel.has(m.name), hit=m.hits&&m.hits[0];
    const sub=hit?`<span class="mhit">${markHit(hitLine(hit))}${m.matches>1?` <i style="font-style:normal;color:var(--faint)">+${m.matches-1}</i>`:''}</span>`:`<span>${esc(m.name)}</span>`;
    return `<div class="rowc${on?' sel':''}"><span class="ck${on?' on':''}" role="checkbox" aria-checked="${on}" tabindex="0" data-n="${esc(m.name)}" title="Select"><i data-lucide="check"></i></span><button class="row${m.name===active?' a':''}" data-n="${esc(m.name)}" data-title-source="${esc(m.titleSource||'')}"><span class="t">${esc(m.day)} · ${esc(m.time)} · ${m.lines} lines</span><b>${esc(mTitle(m))}</b>${sub}</button>${m.name===active?`<button class="mren" data-ren="${esc(m.name)}" title="Rename" aria-label="Rename" style="padding:12px 2px;color:var(--faint)"><i data-lucide="pencil" style="width:14px;height:14px"></i></button>`:''}</div>`; }).join('');
  icons();
  rows.querySelectorAll('.row').forEach(r=>r.onclick=async()=>{ renderMeetingList(r.dataset.n); await openMeeting(r.dataset.n); showHit(r.dataset.n); });
  const tick=c=>{ const k=c.dataset.n; mSel.has(k)?mSel.delete(k):mSel.add(k); renderMeetingList(currentMeeting); };
  rows.querySelectorAll('.ck').forEach(c=>{ c.onclick=()=>tick(c); c.onkeydown=e=>{ if(e.key===' '||e.key==='Enter'){ e.preventDefault(); tick(c); } }; });
  rows.querySelectorAll('[data-ren]').forEach(btn=>btn.onclick=()=>renameMeeting(btn.dataset.ren));
  const all=$('#mSelAll'), none=$('#mSelNone');
  if(all) all.onclick=()=>{ mItems.forEach(m=>mSel.add(m.name)); renderMeetingList(currentMeeting); };
  if(none) none.onclick=()=>{ mSel.clear(); renderMeetingList(currentMeeting); };
  $('#mDelete').lastChild.textContent=n>1?`Delete ${n}`:'Delete';
}
/* rename in place: Enter saves, Esc cancels, an empty title goes back to the automatic one */
function renameMeeting(name){ const row=[...$$('#mRows .row')].find(r=>r.dataset.n===name), m=mItems.find(x=>x.name===name); if(!row||!m) return;
  const b=row.querySelector('b'), inp=document.createElement('input');
  inp.className='field'; inp.id='mTitleEdit'; inp.value=m.titleSource==='custom'?m.title:''; inp.placeholder=mTitle(m); inp.maxLength=120; inp.style.cssText='width:100%;margin:2px 0;font-size:14px';
  b.replaceWith(inp); inp.focus(); row.onclick=null;
  let done=false; const finish=async save=>{ if(done) return; done=true; const br=bridge();
    if(save&&br){ const r=await br.rename_meeting(name,inp.value); if(r.ok){ m.title=r.title; m.titleSource=r.titleSource; toast(r.titleSource==='custom'?'Meeting renamed':'Title is automatic again'); } else toast(r.message); }
    renderMeetingList(currentMeeting); };
  inp.addEventListener('keydown',e=>{ if(e.key==='Enter'){ e.preventDefault(); finish(true); } else if(e.key==='Escape'){ e.preventDefault(); e.stopPropagation(); finish(false); } });
  inp.addEventListener('blur',()=>finish(true)); }
/* a search opened this meeting: scroll its transcript to the first matching line */
function showHit(name){ const m=mItems.find(x=>x.name===name), hit=m&&m.hits&&m.hits[0]; if(!hit) return;
  const line=[...$$('#mDetail .pair2')].find(e=>e.textContent.includes(hitLine(hit))); if(line) line.scrollIntoView({block:'center'}); }
/* the ticked meetings, or else the open one */
const mTargets=()=>mSel.size?[...mSel]:(currentMeeting?[currentMeeting]:[]);

async function openMeeting(name){
  const b=bridge(); if(!b) return; currentMeeting=name;
  const pairs=await b.read_meeting(name); currentPairs=pairs;
  $('#mDetail').innerHTML=pairs.length?pairs.map(p=>{ const t=esc(lineTime(p)); return `<div class="pair2"><div class="de"><span class="t">DE · ${t}</span>${esc(p.de)}</div><div class="en"><span class="feed-t">EN · ${t}</span>${esc(p.en)}</div></div>`; }).join(''):'<div style="margin:auto;color:var(--faint)">Empty transcript.</div>';
}
/* when a saved line was said: speech start and "+mm:ss" into the meeting (old files: write time only) */
function lineTime(p){ return (p.ts||'')+(p.offset?' · '+p.offset:''); }
$$('.ph .btn.ghost').forEach(btn=>{ if(btn.textContent.includes('Summarize')) btn.onclick=()=>{ if(!currentMeeting){ toast('Open a meeting first.'); return; } runSummary(currentMeeting); }; });

/* ---- copy / archive / delete ---- */
function meetingText(){ return currentPairs.map(p=>{ const t=`[${lineTime(p)}]`; return `${t} EN  ${p.en}\n${t} DE  ${p.de}`; }).join('\n\n'); }
$('#mArchView').onclick=()=>{ mArch=!mArch; mSel.clear(); $('#mArchView').classList.toggle('on',mArch); archLabel($('#mArchive'),mArch); currentMeeting=null; loadMeetings(); };
$('#mCopy').onclick=()=>{ if(!currentPairs.length){ toast('Open a meeting first.'); return; } copyText(meetingText()); };
$('#mExport').onclick=async()=>{ const b=bridge(); if(!b||!currentMeeting){ toast('Open a meeting first.'); return; } const r=await b.export_meeting(currentMeeting); if(r.ok) toast('Exported: '+r.path); else if(!r.cancelled) toast(r.message||'Could not export'); };
$('#mArchive').onclick=async()=>{ const b=bridge(), t=mTargets(); if(!b||!t.length){ toast('Open or tick a meeting first.'); return; }
  for(const n of t) await b.archive_meeting(n,!mArch);
  const w=t.length>1?`${t.length} meetings`:'Meeting'; toast(mArch?`${w} restored`:`${w} archived`); mSel.clear(); currentMeeting=null; loadMeetings(); };
$('#mDelete').onclick=async()=>{ const b=bridge(), t=mTargets(); if(!b||!t.length){ toast('Open or tick a meeting first.'); return; }
  const many=t.length>1, label=n=>{ const m=mItems.find(x=>x.name===n); return m?`${m.day} ${m.time} · ${m.lines} lines`:n; };
  if(!await askConfirm({title:many?`Delete ${t.length} meetings?`:'Delete this meeting?', body:`The transcript and subtitle files are removed from this ${W('computer')}. This can't be undone.`, ok:many?`Delete ${t.length}`:'Delete', items:t.map(label)})) return;
  for(const n of t) await b.delete_meeting(n);
  toast(many?`${t.length} meetings deleted`:'Meeting deleted'); mSel.clear(); currentMeeting=null; loadMeetings(); };
