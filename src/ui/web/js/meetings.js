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
async function loadMeetings(){
  const b=bridge(), list=$('#mList');
  if(!b){ list.innerHTML='<div style="padding:16px;color:var(--faint)">Meetings appear here when running in the app.</div>'; return; }
  const items=await b.list_meetings(mArch);
  if(!items.length){ list.innerHTML='<div style="padding:16px;color:var(--faint)">'+(mArch?'No archived meetings.':'No meetings yet.')+'</div>'; currentPairs=[]; $('#mDetail').innerHTML='<div style="margin:auto;color:var(--faint)">Nothing saved yet.</div>'; currentMeeting=null; return; }
  const keep=items.some(m=>m.name===currentMeeting)?currentMeeting:items[0].name;
  mItems=items; mSel=new Set([...mSel].filter(n=>items.some(m=>m.name===n)));
  renderMeetingList(keep);
  openMeeting(keep);
}
/* meeting list with checkboxes: tick several, then Archive/Delete act on all of them */
let mItems=[],mSel=new Set();
function renderMeetingList(active){
  const list=$('#mList'), n=mSel.size;
  const bar=`<div class="selbar">${n?`<b>${n} selected</b>`:'Tick to select several'}<span class="sp"></span>${n<mItems.length?'<button id="mSelAll">Select all</button>':''}${n?'<button id="mSelNone">Clear</button>':''}</div>`;
  list.innerHTML=bar+mItems.map(m=>{ const on=mSel.has(m.name); return `<div class="rowc${on?' sel':''}"><span class="ck${on?' on':''}" role="checkbox" aria-checked="${on}" tabindex="0" data-n="${esc(m.name)}" title="Select"><i data-lucide="check"></i></span><button class="row${m.name===active?' a':''}" data-n="${esc(m.name)}"><span class="t">${esc(m.day)} · ${esc(m.time)} · ${m.lines} lines</span><b>${esc(m.day)} ${esc(m.time)}</b><span>${esc(m.name)}</span></button></div>`; }).join('');
  icons();
  list.querySelectorAll('.row').forEach(r=>r.onclick=()=>{ list.querySelectorAll('.row').forEach(x=>x.classList.remove('a')); r.classList.add('a'); openMeeting(r.dataset.n); });
  const tick=c=>{ const k=c.dataset.n; mSel.has(k)?mSel.delete(k):mSel.add(k); renderMeetingList(currentMeeting); };
  list.querySelectorAll('.ck').forEach(c=>{ c.onclick=()=>tick(c); c.onkeydown=e=>{ if(e.key===' '||e.key==='Enter'){ e.preventDefault(); tick(c); } }; });
  const all=$('#mSelAll'), none=$('#mSelNone');
  if(all) all.onclick=()=>{ mItems.forEach(m=>mSel.add(m.name)); renderMeetingList(currentMeeting); };
  if(none) none.onclick=()=>{ mSel.clear(); renderMeetingList(currentMeeting); };
  $('#mDelete').lastChild.textContent=n>1?`Delete ${n}`:'Delete';
}
/* the ticked meetings, or else the open one */
const mTargets=()=>mSel.size?[...mSel]:(currentMeeting?[currentMeeting]:[]);

async function openMeeting(name){
  const b=bridge(); if(!b) return; currentMeeting=name;
  const pairs=await b.read_meeting(name); currentPairs=pairs;
  $('#mDetail').innerHTML=pairs.length?pairs.map(p=>`<div class="pair2"><div class="de"><span class="t">DE · ${esc(p.ts)}</span>${esc(p.de)}</div><div class="en">${esc(p.en)}</div></div>`).join(''):'<div style="margin:auto;color:var(--faint)">Empty transcript.</div>';
}
$$('.ph .btn.ghost').forEach(btn=>{ if(btn.textContent.includes('Summarize')) btn.onclick=()=>{ if(!currentMeeting){ toast('Open a meeting first.'); return; } runSummary(currentMeeting); }; });

/* ---- copy / archive / delete ---- */
function meetingText(){ return currentPairs.map(p=>`[${p.ts}] ${p.en}\n        ${p.de}`).join('\n\n'); }
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
