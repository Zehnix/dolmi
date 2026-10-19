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
    if(!body){ if(!seen) m.gpre.push(raw); else if(raw.trim()) notes.push(raw.trim()); continue; }
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

function vocRender(){
  for(const g of Object.keys(VOC_GROUPS)){
    const card=$(`.voc-g[data-g="${g}"]`), list=card.querySelector('.voc-list'), rows=voc.groups[g];
    list.textContent='';
    rows.forEach(r=>list.append(vocRowEl(g,r)));
    if(!rows.length){ const p=document.createElement('p'); p.className='voc-empty'; p.textContent='Nothing here yet.'; list.append(p); }
    card.querySelector('.voc-n').textContent=rows.length||'';
  }
  icons();
}
function vocRowEl(g,r){
  const row=document.createElement('div'); row.className='voc-row'+(g==='gloss'?' voc-one':''); r.el=row;
  const input=(key,ph)=>{ const i=document.createElement('input'); i.className='field'; i.value=r[key]; i.placeholder=ph;
    i.spellcheck=false; i.setAttribute('aria-label',VOC_GROUPS[g]+': '+ph); i.oninput=()=>{ r[key]=i.value; vocTouched(); }; return i; };
  row.append(input('src',VOC_PH[g][0]));
  if(g!=='gloss'){ const a=document.createElement('span'); a.className='voc-arrow'; a.textContent='→'; row.append(a,input('dst',VOC_PH[g][1])); }
  const del=document.createElement('button'); del.className='voc-del'; del.title='Delete'; del.setAttribute('aria-label','Delete row');
  del.innerHTML='<i data-lucide="x"></i>';
  del.onclick=()=>{ const rows=voc.groups[g]; rows.splice(rows.indexOf(r),1); vocTouched(); vocRender(); };
  row.append(del);
  return row;
}

function vocStatus(text,kind=''){ const s=$('#vocabStatus'); s.textContent=text; s.className='voc-status'+(kind?' voc-'+kind:''); }
function vocTouched(){ vocConfirm=vocOverwrite=false; vocDirty=true; $('#vocabSave').textContent='Save'; vocStatus('Unsaved changes'); }

/* what is on disk, compared the way save_vocab writes it (trailing whitespace and line endings ignored) */
const vocNorm=s=>String(s||'').replace(/\r\n/g,'\n').replace(/\s+$/,'');
const vocSnap=(v,g)=>({vocabulary:vocNorm(v),glossary:vocNorm(g)});
const vocDiskChanged=d=>!vocDisk||vocNorm(d.vocabulary)!==vocDisk.vocabulary||vocNorm(d.glossary)!==vocDisk.glossary;
function vocApply(v){
  voc=vocParse(v.vocabulary,v.glossary); vocDisk=vocSnap(v.vocabulary,v.glossary);
  vocDirty=vocConfirm=vocOverwrite=false; $('#vocabSave').textContent='Save';
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
    vocStatus(n?'Saved — used from the next line, except the marked lines':'Saved — used from the next line', n?'warn':'ok'); }
  catch(e){ console.error('save_vocab',e); vocStatus('Could not save the vocabulary.','warn'); toast('Could not save the vocabulary.'); }
};
