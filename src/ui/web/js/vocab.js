/* Dolmi web UI — Vocabulary view.
   Classic script (not a module): top-level names are shared with the other js/*.js files.
   Load order (index.html): core, live, meetings, assistant, vocab, settings, onboarding. */
/* ---- vocabulary editor: four groups over two files — Spelling hints (glossary.txt) and the [keep],
   [german], [english] sections of vocabulary.txt. "Rows" edits them as term/replacement rows, "Raw text"
   as the files themselves; both save through save_vocab(vocabulary, glossary). Python's parser is the
   judge: validate_vocab returns the lines it will not use, with line numbers, before anything is saved.
   Rows mode keeps the files' comments (header block, comments above a rule, inline notes, notes on a
   section header) and any unknown section verbatim, but writes all three sections in a fixed order with
   unknown sections last; blank lines inside a section are not kept.
   Hand edits to the files are used live (the app watches them), so the editor never saves over a file that
   changed on disk since it was loaded: unchanged editors reload, edited ones ask before overwriting. ---- */
const VOC_SECTIONS=['keep','german','english'];
const VOC_GROUPS={gloss:'Spelling hints',keep:'Keep as is',german:'German → English',english:'Fix English'};
const VOC_PH={gloss:['Term'],keep:['Term','Same in English'],german:['German','English'],english:['Wrong English','Correct English']};
let voc=null, vocMode='rows', vocConfirm=false, vocOverwrite=false, vocDirty=false, vocDisk=null;

/* same comment rule as live_subs.strip_comment: '#' starts a comment, '\#' is a literal '#' */
function vocStrip(raw){ const i=raw.search(/(?<!\\)#/);
  return {body:(i<0?raw:raw.slice(0,i)).replace(/\\#/g,'#').trim(), note:i<0?'':raw.slice(i+1).trim()}; }
const vocEsc=s=>String(s||'').trim().replace(/#/g,'\\#');
const vocRow=(src='',dst='',note='',pre=[])=>({src,dst,note,pre});

function vocParse(vtext,gtext){
  const m={pre:[],tail:[],gpre:[],gpost:[],hnote:{},groups:{gloss:[],keep:[],german:[],english:[]},post:{keep:[],german:[],english:[]}};
  let sec=null, notes=[];
  const park=()=>{ if(notes.length) (sec?m.post[sec]:m.pre).push(...notes); notes=[]; };
  for(const raw of String(vtext||'').split(/\r?\n/)){
    const {body,note}=vocStrip(raw);
    if(body.startsWith('[')){
      const name=body.endsWith(']')?body.slice(1,-1).trim().toLowerCase():'';
      if(sec!=='') park();
      if(VOC_SECTIONS.includes(name)){ sec=name; if(note) m.hnote[name]=note; continue; }
      sec=''; m.tail.push(raw); continue;          // unknown section: kept as written, reported by Python
    }
    if(sec===''){ m.tail.push(raw); continue; }
    if(sec===null){ m.pre.push(raw); continue; }  // header comments (and stray lines) stay on top
    if(!body){ if(raw.trim()) notes.push(raw.trim()); continue; }
    const i=body.indexOf('=');
    m.groups[sec].push(vocRow(i<0?body:body.slice(0,i).trim(), i<0?'':body.slice(i+1).trim(), note, notes)); notes=[];
  }
  if(sec!=='') park(); else m.tail.push(...notes);
  notes=[]; let seen=false;
  for(const raw of String(gtext||'').split(/\r?\n/)){
    const {body,note}=vocStrip(raw);
    if(!body){ if(VOC_PROFILE.test(raw)||(seen||notes.length)&&raw.trim()) notes.push(raw.trim()); else if(!seen&&!notes.length) m.gpre.push(raw); continue; }
    seen=true; m.groups.gloss.push(vocRow(body,'',note,notes)); notes=[];
  }
  m.gpost=notes;
  while(m.gpre.length&&!m.gpre[m.gpre.length-1].trim()) m.gpre.pop();
  return m;
}

/* rows -> the two files; rowAt maps 'v:<line>' / 'g:<line>' to the row written there */
function vocSerialize(){
  const rowAt=new Map(), line=r=>vocEsc(r.src)+(r.dst.trim()?' = '+vocEsc(r.dst):'')+(r.note?'  # '+r.note:'');
  const v=[...voc.pre]; while(v.length&&!v[v.length-1].trim()) v.pop();
  for(const s of VOC_SECTIONS){
    if(v.length) v.push('');
    v.push('['+s+']'+(voc.hnote[s]?'  # '+voc.hnote[s]:''));
    for(const r of voc.groups[s]){ if(!r.src.trim()&&!r.dst.trim()) continue; v.push(...r.pre); v.push(line(r)); rowAt.set('v:'+v.length,r); }
    v.push(...voc.post[s]);
  }
  if(voc.tail.length){ v.push(''); v.push(...voc.tail); }
  const g=[...voc.gpre];
  for(const r of voc.groups.gloss){ if(!r.src.trim()) continue; g.push(...r.pre); g.push(line(r)); rowAt.set('g:'+g.length,r); }
  g.push(...voc.gpost);
  return {vocabulary:v.join('\n'), glossary:g.join('\n'), rowAt};
}
const vocCurrent=()=>vocMode==='raw'?{vocabulary:$('#vocabRaw').value, glossary:$('#vocabBox').value, rowAt:new Map()}:vocSerialize();

/* '## profile: Name' in glossary.txt starts a per-meeting block (live_subs.parse_glossary_profiles); in Rows
   mode it rides along as a comment above the block's first term and is shown as a divider */
const VOC_PROFILE=/^\s*##\s*profile\s*:(.*)$/i;
const vocProfileOf=r=>{ const h=[...r.pre].reverse().map(l=>l.match(VOC_PROFILE)).find(Boolean); return h?h[1].replace(/(?<!\\)#.*/,'').trim():null; };

function vocRender(){
  for(const g of Object.keys(VOC_GROUPS)){
    const card=$(`.voc-g[data-g="${g}"]`), list=card.querySelector('.voc-list'), rows=voc.groups[g];
    list.textContent='';
    rows.forEach(r=>{ const p=g==='gloss'&&vocProfileOf(r);
      if(p!=null&&p!==false){ const d=document.createElement('div'); d.className='voc-prof'; d.textContent='Profile · '+(p||'(no name)'); list.append(d); }
      list.append(vocRowEl(g,r)); });
    if(!rows.length){ const p=document.createElement('p'); p.className='voc-empty'; p.textContent='Nothing here yet.'; list.append(p); }
    card.querySelector('.voc-n').textContent=rows.length||'';
  }
  vocMarkCut();
  icons();
}
const vocKeyOf=s=>String(s||'').trim().toLowerCase();
/* a spelling hint is also kept in the translation when [keep] has the same term (as is, no target) */
const vocKeepRow=src=>voc.groups.keep.find(k=>vocKeyOf(k.src)===vocKeyOf(src)&&(!k.dst.trim()||vocKeyOf(k.dst)===vocKeyOf(src)));
function vocToolBtn(icon,label,fn){ const b=document.createElement('button'); b.className='voc-tb'; b.title=label; b.setAttribute('aria-label',label);
  b.innerHTML=`<i data-lucide="${icon}"></i>`; b.onclick=fn; return b; }
function vocRowEl(g,r){
  const row=document.createElement('div'); row.className='voc-row'+(g==='gloss'?' voc-one voc-gl':''); r.el=row;
  const input=(key,ph)=>{ const i=document.createElement('input'); i.className='field'; i.value=r[key]; i.placeholder=ph;
    i.spellcheck=false; i.setAttribute('aria-label',VOC_GROUPS[g]+': '+ph); i.oninput=()=>{ r[key]=i.value; vocTouched(); }; return i; };
  row.append(input('src',VOC_PH[g][0]));
  if(g!=='gloss'){ const a=document.createElement('span'); a.className='voc-arrow'; a.textContent='→'; row.append(a,input('dst',VOC_PH[g][1])); }
  if(g==='gloss'){
    const rows=voc.groups.gloss, i=rows.indexOf(r);
    const keep=vocToolBtn('lock','Also keep in translation (adds it to Keep as is)',()=>{
      if(!r.src.trim()) return;
      const k=vocKeepRow(r.src);
      if(k) voc.groups.keep.splice(voc.groups.keep.indexOf(k),1); else voc.groups.keep.push(vocRow(r.src.trim()));
      vocTouched(); vocRender(); });
    keep.classList.toggle('on',!!(r.src.trim()&&vocKeepRow(r.src))); keep.setAttribute('aria-pressed',keep.classList.contains('on'));
    /* moving swaps the terms, not the comments above them: a profile divider stays where it is */
    const move=d=>()=>{ const o=rows[i+d]; if(!o) return; for(const k of ['src','dst','note']) [r[k],o[k]]=[o[k],r[k]]; vocTouched(); vocRender();
      const b=o.el&&o.el.querySelector(d<0?'[data-mv="up"]':'[data-mv="down"]'); (b&&!b.disabled?b:o.el.querySelector('input')).focus(); };
    const up=vocToolBtn('chevron-up','Move up (more important)',move(-1)), down=vocToolBtn('chevron-down','Move down',move(1));
    up.dataset.mv='up'; down.dataset.mv='down'; up.disabled=i===0; down.disabled=i===rows.length-1;
    row.append(keep,up,down);
  }
  const del=document.createElement('button'); del.className='voc-del'; del.title='Delete'; del.setAttribute('aria-label','Delete row');
  del.innerHTML='<i data-lucide="x"></i>';
  del.onclick=()=>{ const rows=voc.groups[g], i=rows.indexOf(r), next=rows[i+1];
    if(r.pre.length){ if(next) next.pre=[...r.pre,...next.pre]; else (g==='gloss'?voc.gpost:voc.post[g]).unshift(...r.pre); }   // comments (and a profile line) stay
    rows.splice(i,1); vocTouched(); vocRender(); };
  row.append(del);
  return row;
}

/* ---- live summary (VOC-2) + hotword budget (VOC-5/11): Api.vocab_summary over what the editor shows ---- */
let vocSum=null, vocSumTimer=0, vocSumSeq=0;
const VOC_SHORT={gloss:'Spelling hints',keep:'Keep',german:'German → English',english:'Fix English'};
function vocMarkCut(){
  const cut=new Set(((vocSum&&vocSum.budget.cut)||[]).map(vocKeyOf));
  for(const r of (voc?voc.groups.gloss:[])) if(r.el){ const c=cut.has(vocKeyOf(r.src)); r.el.classList.toggle('voc-cut',c);
    if(c) r.el.title='Over the hotword budget: Whisper never sees this term. Move it up or remove others.'; else if(!r.el.classList.contains('voc-bad')) r.el.removeAttribute('title'); }
}
function vocShowSummary(s){
  vocSum=s; const b=s.budget, box=$('#vocBudget');
  const over=b.cut.length>0, warn=over||b.tooLong;
  const pct=Math.round(Math.min(1,b.tokens/b.limit)*100);
  let html=`<div><b>${b.tokens} / ${b.limit}</b> Whisper tokens${b.exact?'':' (estimate)'} · ${b.terms} term${b.terms===1?'':'s'} · ${b.chars} characters`+
    (s.profile?` · profile <b>${esc(s.profile)}</b>`:'')+`</div><div class="voc-meter"><i style="width:${pct}%"></i></div>`;
  if(b.cloud) html+='<p>Cloud captions are on: they get every term; this budget is for captions on this computer.</p>';
  if(over) html+=`<p>${b.cut.length} term${b.cut.length>1?'s are':' is'} cut and never reach Whisper: ${esc(b.cut.slice(0,8).join(', '))}${b.cut.length>8?', …':''}. Put the most important first, or split the list into profiles.</p>`;
  else if(b.tooLong) html+=`<p>Long list (over ${b.softTerms} terms or ${b.softChars} characters): Whisper may read it back during silence. Keep only words it really gets wrong.</p>`;
  box.innerHTML=html; box.classList.toggle('voc-over',warn); box.hidden=false;
  const c=s.counts;
  $('#vocCounts').innerHTML=Object.keys(VOC_SHORT).map(k=>`<span class="chipt${c[k]?' on':''}">${esc(VOC_SHORT[k])} · ${c[k]}</span>`).join('')+
    (s.problems?`<span class="chipt warn">${s.problems} line${s.problems>1?'s':''} not used</span>`:'');
  $('#vocRecent').innerHTML=s.recent.length?`<b>${s.recentFrom==='save'?'Changed in your last save':'Newest in your files'}</b><div class="chips">${
    s.recent.map(x=>`<span class="chipt" title="${esc(VOC_GROUPS[x.group])}">${esc(x.term)}</span>`).join('')}</div>`:'';
  $('#vocBias').innerHTML=s.bias.length?`<b>May mislead Whisper</b><ul>${s.bias.slice(0,6).map(x=>`<li><code>${esc(x.term)}</code> — ${esc(x.reason)}</li>`).join('')}</ul>`:'';
  vocMarkCut();
  if(!vocDirty) vocProfiles(s);   // the picker offers saved profiles only
}
async function vocSummary(){
  const b=bridge(); if(!b||!voc||!b.vocab_summary) return;
  const c=vocCurrent(), seq=++vocSumSeq;
  try{ const s=await b.vocab_summary(c.vocabulary,c.glossary,null); if(seq===vocSumSeq) vocShowSummary(s); }
  catch(e){ console.error('vocab_summary',e); }
}
const vocSummarySoon=()=>{ clearTimeout(vocSumTimer); vocSumTimer=setTimeout(vocSummary,500); };

/* profile picker next to Start (#vocabProfile): hidden until glossary.txt has '## profile:' blocks */
function vocProfiles(s){
  const sel=$('#vocabProfile'); if(!sel||!s) return;
  if(!s.profiles.length){ sel.hidden=true; return; }
  if(document.activeElement===sel) return;
  sel.innerHTML=`<option value="">All spelling hints</option>`+s.profiles.map(p=>`<option value="${esc(p.name)}">${esc(p.name)} · ${p.terms}</option>`).join('');
  sel.value=s.profile||''; sel.hidden=false;
}
async function vocProfilesBoot(){ const b=bridge(); if(!b||!b.vocab_summary) return;
  try{ vocProfiles(await b.vocab_summary(null,null,null)); }catch(e){ console.error('vocab_summary',e); } }
$('#vocabProfile').onchange=async()=>{
  const b=bridge(), sel=$('#vocabProfile'); if(!b) return;
  try{ const r=await b.set_vocab_profile(sel.value);
    if(!r.ok){ toast('That profile is gone from glossary.txt.'); vocProfilesBoot(); return; }
    toast(r.profile?`Spelling hints: ${r.profile}`:'Spelling hints: all terms');
    if(voc) vocSummary(); }
  catch(e){ console.error('set_vocab_profile',e); toast('Could not switch the profile.'); }
};
document.addEventListener('DOMContentLoaded',()=>{ if(bridge()) vocProfilesBoot(); else window.addEventListener('pywebviewready',vocProfilesBoot); });

function vocStatus(text,kind=''){ const s=$('#vocabStatus'); s.textContent=text; s.className='voc-status'+(kind?' voc-'+kind:''); }
function vocTouched(){ vocConfirm=vocOverwrite=false; vocDirty=true; $('#vocabSave').textContent='Save'; vocStatus('Unsaved changes'); $('#vocSaved').hidden=true; vocSummarySoon(); }

/* what is on disk, compared the way save_vocab writes it (trailing whitespace and line endings ignored) */
const vocNorm=s=>String(s||'').replace(/\r\n/g,'\n').replace(/\s+$/,'');
const vocSnap=(v,g)=>({vocabulary:vocNorm(v),glossary:vocNorm(g)});
const vocDiskChanged=d=>!vocDisk||vocNorm(d.vocabulary)!==vocDisk.vocabulary||vocNorm(d.glossary)!==vocDisk.glossary;
function vocApply(v){
  voc=vocParse(v.vocabulary,v.glossary); vocDisk=vocSnap(v.vocabulary,v.glossary);
  vocDirty=vocConfirm=vocOverwrite=false; $('#vocabSave').textContent='Save'; $('#vocSaved').hidden=true;
  $('#vocabRaw').value=v.vocabulary||''; $('#vocabBox').value=v.glossary||'';
  vocRender();
}

/* problems from validate_vocab -> list above the groups + marked rows; returns how many */
function vocShowProblems(res,rowAt){
  const box=$('#vocProblems');
  $$('.voc-row.voc-bad').forEach(e=>{ e.classList.remove('voc-bad'); e.removeAttribute('title'); });
  const all=[...(res.vocabulary||[]).map(p=>({...p,k:'v',file:'vocabulary.txt'})),...(res.glossary||[]).map(p=>({...p,k:'g',file:'glossary.txt'}))];
  if(!all.length){ box.hidden=true; box.textContent=''; return 0; }
  const where=p=>{ const r=rowAt.get(p.k+':'+p.line);
    if(!r) return `${p.file} · line ${p.line}`+(vocMode==='rows'?' (Raw text)':'');
    const g=p.k==='g'?'gloss':Object.keys(voc.groups).find(k=>voc.groups[k].includes(r));
    r.el&&r.el.classList.add('voc-bad'); r.el&&(r.el.title=p.message);
    return `${VOC_GROUPS[g]} · row ${voc.groups[g].indexOf(r)+1}`; };
  box.innerHTML=`<div class="cardh"><span class="ic"><i data-lucide="triangle-alert"></i></span>${all.length} line${all.length>1?'s':''} will not be used until fixed</div>`+
    `<ul class="voc-plist">${all.map((p,i)=>`<li><button data-i="${i}"><b>${esc(where(p))}</b><code>${esc(p.text)}</code><span>${esc(p.message)}</span></button></li>`).join('')}</ul>`;
  box.querySelectorAll('button[data-i]').forEach(b=>b.onclick=()=>vocJump(all[+b.dataset.i],rowAt));
  box.hidden=false; icons();
  return all.length;
}
function vocJump(p,rowAt){
  const r=rowAt.get(p.k+':'+p.line);
  if(r&&r.el){ r.el.scrollIntoView({block:'nearest'}); r.el.querySelector('input').focus(); return; }
  if(vocMode==='rows') vocSetMode('raw');
  const ta=p.k==='g'?$('#vocabBox'):$('#vocabRaw'), lines=ta.value.split('\n');
  const start=lines.slice(0,p.line-1).reduce((n,l)=>n+l.length+1,0);
  ta.focus(); ta.setSelectionRange(start,start+(lines[p.line-1]||'').length);
}

function vocSetMode(m){
  if(!voc||m===vocMode) return;
  if(m==='raw'){ const c=vocSerialize(); $('#vocabRaw').value=c.vocabulary; $('#vocabBox').value=c.glossary; }
  else { voc=vocParse($('#vocabRaw').value,$('#vocabBox').value); vocRender(); }
  vocMode=m; setSegVal('#vocMode','data-m',m);
  $('#vocRows').hidden=m!=='rows'; $('#vocRaw').hidden=m!=='raw';
  vocCheck(true);
}

async function vocCheck(quiet){
  const b=bridge(); if(!b||!voc) return 0;
  const c=vocCurrent();
  vocSummary();
  try{ const n=vocShowProblems(await b.validate_vocab(c.vocabulary,c.glossary),c.rowAt);
    if(!quiet) vocStatus(n?`${n} line${n>1?'s':''} to fix`:'No problems found', n?'warn':'ok');
    return n; }
  catch(e){ console.error('validate_vocab',e); if(!quiet) vocStatus('Could not check the vocabulary','warn'); return 0; }
}

async function loadVocab(){
  const b=bridge();
  if(!b){ vocStatus('Vocabulary loads when running in the app.'); return; }
  try{
    vocApply(await b.read_vocab());
    $('#vocabSave').disabled=$('#vocabCheck').disabled=false;
    vocStatus('');
    vocCheck(true);
  }catch(e){ console.error('read_vocab',e); vocStatus('Could not load the vocabulary.','warn'); }
}

/* tab opened again: pick up hand edits, unless the editor holds unsaved work (Save then asks) */
async function vocRefresh(){
  const b=bridge(); if(!b||!voc||vocDirty) return;
  try{ const d=await b.read_vocab();
    if(vocDiskChanged(d)){ vocApply(d); vocStatus('Reloaded: the files changed outside Dolmi'); vocCheck(true); } }
  catch(e){ console.error('read_vocab',e); }
}

$$('.voc-g').forEach(card=>card.querySelector('.voc-add').onclick=()=>{
  if(!voc) return;
  const g=card.dataset.g, r=vocRow(); voc.groups[g].push(r); vocRender(); vocTouched();
  r.el.scrollIntoView({block:'nearest'}); r.el.querySelector('input').focus();
});
$$('#vocMode button').forEach(b=>b.addEventListener('click',()=>vocSetMode(b.dataset.m)));
['#vocabRaw','#vocabBox'].forEach(s=>$(s).addEventListener('input',vocTouched));
$('#vocabCheck').onclick=()=>vocCheck(false);
$('#vocabSave').onclick=async()=>{
  const b=bridge();
  if(!b||!voc){ toast('Vocabulary has not loaded yet.'); return; }
  if(!vocOverwrite){
    let d=null;
    try{ d=await b.read_vocab(); }catch(e){ console.error('read_vocab',e); }
    if(d&&vocDiskChanged(d)){
      if(!vocDirty){ vocApply(d); vocCheck(true); vocStatus('The files changed outside Dolmi, so they were reloaded. Nothing was saved.','warn'); return; }
      vocOverwrite=true; $('#vocabSave').textContent='Overwrite';
      vocStatus('vocabulary.txt or glossary.txt changed outside Dolmi since this editor loaded them. Save again to replace them with what you see here.','warn');
      return;
    }
  }
  const c=vocCurrent();
  let n=0;
  try{ n=vocShowProblems(await b.validate_vocab(c.vocabulary,c.glossary),c.rowAt); }catch(e){ console.error('validate_vocab',e); }
  if(n&&!vocConfirm){   // nothing invalid goes in unseen: the first click only shows what will be skipped
    vocConfirm=true; $('#vocabSave').textContent='Save anyway';
    vocStatus('Fix the marked lines, or save anyway: they stay in the file but are not used.','warn');
    return;
  }
  try{ await b.save_vocab(c.vocabulary,c.glossary);
    vocConfirm=vocOverwrite=vocDirty=false; vocDisk=vocSnap(c.vocabulary,c.glossary); $('#vocabSave').textContent='Save';
    $('#vocSaved').hidden=false; vocSummary();   // the badge only ever follows a real save
    vocStatus(n?'Saved — used from the next line, except the marked lines':'Saved — used from the next line', n?'warn':'ok'); }
  catch(e){ console.error('save_vocab',e); vocStatus('Could not save the vocabulary.','warn'); toast('Could not save the vocabulary.'); }
};
