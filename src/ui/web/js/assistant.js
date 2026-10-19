/* Dolmi web UI — Assistant view: chats, conversation, ask, archive, copy.
   Classic script (not a module): top-level names are shared with the other js/*.js files.
   Load order (index.html): core, live, meetings, assistant, vocab, settings, onboarding. */
let aArch=false,currentMsgs=[];

/* ---- assistant: chat list + conversation ---- */
const asrSpines={};
function scrollAsr(){ const m=$('#asrMsgs'); if(m) m.scrollTop=m.scrollHeight; }
function asrIdle(msg){ $('#asrMsgs').innerHTML='<div class="asr-empty" style="margin:auto;color:var(--faint);text-align:center">'+msg+'</div>'; }
function clearAsrIdle(){ const e=$('#asrMsgs .asr-empty'); if(e) e.remove(); }
function asrYou(q){ clearAsrIdle(); const d=document.createElement('div'); d.className='you'; d.textContent=q; $('#asrMsgs').append(d); scrollAsr(); }
function asrStart(qid){ clearAsrIdle(); const a=document.createElement('div'); a.className='asr'; a.innerHTML='<span class="lbl">ASSISTANT</span><div class="spine"></div>'; $('#asrMsgs').append(a); asrSpines[qid]=a.querySelector('.spine'); scrollAsr(); }
function asrChunk(qid,p){ const s=asrSpines[qid]; if(s){ s.textContent+=p; scrollAsr(); } }
function asrDone(qid,err){ const s=asrSpines[qid]; if(err&&s){ s.textContent=err; s.style.color='var(--src)'; } delete asrSpines[qid]; scrollAsr(); }
function renderChat(msgs){
  const m=$('#asrMsgs'); m.innerHTML='';
  if(!msgs.length){ asrIdle('This chat is empty.'); return; }
  msgs.forEach(x=>{
    const y=document.createElement('div'); y.className='you'; y.textContent=x.q; m.append(y);
    const a=document.createElement('div'); a.className='asr'; a.innerHTML='<span class="lbl">ASSISTANT · '+esc(x.when)+'</span><div class="spine"></div>';
    const sp=a.querySelector('.spine'); sp.textContent=x.err||x.a; if(x.err) sp.style.color='var(--src)'; m.append(a);
  });
  scrollAsr();
}
async function loadChats(){
  const b=bridge(), list=$('#aList');
  if(!b){ list.innerHTML='<div style="padding:16px;color:var(--faint);font-size:13px">Chats appear here when running in the app.</div>'; return; }
  const items=await b.list_chats($('#chatSearch').value||'', aArch);
  if(!items.length){ list.innerHTML='<div style="padding:16px;color:var(--faint);font-size:13px">'+(aArch?'No archived chats.':'No chats yet — ask something.')+'</div>'; return; }
  list.innerHTML=items.map(c=>`<button class="row${c.id===currentChat?' a':''}" data-id="${c.id}"><span class="t">${esc(c.when)} · ${c.questions} Q</span><b>${esc(c.title.length>50?c.title.slice(0,48)+'…':c.title)}</b></button>`).join('');
  list.querySelectorAll('.row').forEach(r=>r.onclick=()=>openChat(+r.dataset.id));
}
async function openChat(id){
  const b=bridge(); if(!b) return;
  currentChat=id; $$('#aList .row').forEach(r=>r.classList.toggle('a',+r.dataset.id===id));
  currentMsgs=await b.open_chat(id); renderChat(currentMsgs);
}
$('#newChat').onclick=async()=>{ const b=bridge(); if(b) await b.new_chat(); currentChat=null; $$('#aList .row').forEach(r=>r.classList.remove('a')); asrIdle('New chat — ask about the meeting below.'); $('#askInput').focus(); };
$('#delChat').onclick=async()=>{ const b=bridge(); if(!b||currentChat==null){ toast('Open a chat first.'); return; } if(!await askConfirm({title:'Delete this chat?',body:`The questions and answers in this chat are removed from this ${W('computer')}. This can't be undone.`})) return; await b.delete_chat(currentChat); currentChat=null; currentMsgs=[]; asrIdle('Chat deleted.'); loadChats(); };
let chatT; $('#chatSearch').oninput=()=>{ clearTimeout(chatT); chatT=setTimeout(loadChats,250); };
$('#askForm').onsubmit=e=>{ e.preventDefault(); const v=$('#askInput').value.trim(); if(!v) return; $('#askInput').value=''; asrYou(v); const b=bridge(); if(b) b.ask(v); else { asrStart('demo'); asrChunk('demo','The assistant answers here when running in the app.'); } };

/* ---- copy / archive ---- */
function chatText(){ return currentMsgs.map(m=>`You (${m.when}): ${m.q}\nAssistant: ${m.err||m.a}`).join('\n\n'); }
$('#aArchView').onclick=()=>{ aArch=!aArch; $('#aArchView').classList.toggle('on',aArch); archLabel($('#aArchive'),aArch); currentChat=null; currentMsgs=[]; asrIdle(aArch?'Archived chats — open one to read it.':'Ask about the meeting below.'); loadChats(); };
$('#aCopy').onclick=()=>{ if(!currentMsgs.length){ toast('Open a chat first.'); return; } copyText(chatText()); };
$('#aArchive').onclick=async()=>{ const b=bridge(); if(!b||currentChat==null){ toast('Open a chat first.'); return; } await b.archive_chat(currentChat,!aArch); if(!aArch) await b.new_chat(); toast(aArch?'Chat restored':'Chat archived'); currentChat=null; currentMsgs=[]; asrIdle(aArch?'Chat restored.':'Chat archived.'); loadChats(); };
