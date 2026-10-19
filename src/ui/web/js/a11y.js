/* Dolmi web UI — APP-14 accessibility: screen-reader roles and labels, keyboard handling for the custom
   controls, focus in dialogs, and the 'Larger UI text' option (Appearance).
   Classic script, loaded last: it only adds attributes and key handlers to what the other files built,
   and re-runs when they re-render (a MutationObserver), so their markup stays as it is. */

/* ---- roles and labels ---- */
const a11yRowLabel=el=>{ const row=el.closest('.set'); const lab=row&&row.querySelector('.lab'); return lab?lab.textContent.trim():''; };
function a11yEnhance(scope=document){
  /* switches: a button whose state is the 'on' class */
  scope.querySelectorAll('.sw').forEach(s=>{ s.setAttribute('role','switch'); s.setAttribute('aria-checked',String(s.classList.contains('on')));
    if(!s.hasAttribute('aria-label')){ const l=a11yRowLabel(s)||s.title; if(l) s.setAttribute('aria-label',l); } });
  /* segmented controls: one choice of several */
  scope.querySelectorAll('.seg').forEach(g=>{ g.setAttribute('role','radiogroup');
    if(!g.hasAttribute('aria-label')){ const l=a11yRowLabel(g)||g.title; if(l) g.setAttribute('aria-label',l); }
    const kids=[...g.children], cur=kids.find(b=>b.classList.contains('a'))||kids[0];
    kids.forEach(b=>{ b.setAttribute('role','radio'); const on=b.classList.contains('a'); b.setAttribute('aria-checked',String(on)); b.tabIndex=b===cur?0:-1; }); });
  /* the open tab and the open Settings section */
  scope.querySelectorAll('.nav button,#setMenu [data-sec]').forEach(b=>{ if(b.classList.contains('a')) b.setAttribute('aria-current','page'); else b.removeAttribute('aria-current'); });
  /* icon-only buttons: their tooltip is their name */
  scope.querySelectorAll('button,[role="button"]').forEach(b=>{ if(b.hasAttribute('aria-label')||b.textContent.trim()) return;
    const l=b.title||b.dataset.a11y; if(l) b.setAttribute('aria-label',l); });
  const chip=$('.top .chip'); if(chip){ chip.setAttribute('role','img'); chip.setAttribute('aria-label','Translating '+(($('#chipFrom')||{}).textContent||'')+' to '+(($('#chipTo')||{}).textContent||'')); }
  const st=$('#stealth'); if(st) st.setAttribute('aria-pressed',String(st.classList.contains('on')));
  scope.querySelectorAll('.scrim .onb,.scrim .cfm').forEach(d=>{ if(!d.hasAttribute('role')){ d.setAttribute('role','dialog'); d.setAttribute('aria-modal','true'); } });
}
function a11yStatic(){
  const set=(sel,attrs)=>{ const e=$(sel); if(e) Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,v)); };
  set('.nav',{'aria-label':'Main'});
  set('#newChat',{'aria-label':'New chat'});
  set('#askForm button[type="submit"]',{'aria-label':'Send question'});
  set('#askInput',{'aria-label':'Ask about this meeting'});
  set('#chatSearch',{'aria-label':'Search chats'});
  set('#barBtn',{'aria-label':'Subtitle bar'});
  /* the feed changes on every draft: it is not read out itself, the finals go to #a11yLive */
  set('#brows',{'role':'log','aria-live':'off','aria-label':'Captions'});
  set('#status',{'role':'status'});
  set('#onb .onb',{'role':'dialog','aria-modal':'true','aria-label':'Set up Dolmi'});
  set('#vocMode',{'aria-label':'Edit as'});
  set('#setKeep',{'aria-label':'Delete after'});
  if(!$('#a11yLive')){ const r=document.createElement('div'); r.id='a11yLive'; r.className='sr-only'; r.setAttribute('aria-live','polite'); r.setAttribute('aria-atomic','true'); document.body.append(r); }
}
/* finals only are read out (drafts would talk over the speaker) */
{ const d=window.dolmi; if(d&&d.onLine){ const onLine=d.onLine;
  d.onLine=(ts,de,en,meta)=>{ onLine(ts,de,en,meta); const r=$('#a11yLive'); if(r) r.textContent=en||de||''; }; } }

/* ---- keyboard ---- */
/* arrow keys move along a segmented control (and pick, like a click) and through the Settings menu */
function a11yArrow(e,items,horizontal){
  const next={ArrowRight:1,ArrowLeft:-1,ArrowDown:1,ArrowUp:-1}[e.key];
  if(!next||(horizontal===false&&(e.key==='ArrowLeft'||e.key==='ArrowRight'))) return false;
  const i=items.indexOf(e.target); if(i<0) return false;
  const to=items[(i+next+items.length)%items.length]; e.preventDefault(); to.focus(); to.click(); return true; }
document.addEventListener('keydown',e=>{
  if(e.ctrlKey||e.altKey||e.metaKey) return;
  const t=e.target; if(!(t instanceof Element)) return;
  const seg=t.closest('.seg'); if(seg&&t.parentElement===seg){ a11yArrow(e,[...seg.children]); return; }
  if(t.closest('#setMenu')&&t.matches('[data-sec]')){ a11yArrow(e,[...$$('#setMenu [data-sec]')].filter(b=>!b.hidden),false); return; }
  if(t.closest('.nav')&&t.matches('button')){ a11yArrow(e,$$('.nav button')); return; }
  /* custom (non-button) controls: Enter and Space click them */
  if((e.key==='Enter'||e.key===' ')&&t.matches('[role="button"]:not(button)')){ e.preventDefault(); t.click(); }
});
/* Esc closes the open dialog (the confirm dialog handles its own Esc first) */
document.addEventListener('keydown',e=>{ if(e.key!=='Escape') return;
  const open=$$('.scrim').filter(s=>!s.hidden&&s.id!=='onb'&&s.getClientRects().length);
  if(open.length) open[open.length-1].hidden=true; });
/* a dialog that opens takes the focus, keeps Tab inside, and gives the focus back when it closes */
const a11yFocusable=d=>[...d.querySelectorAll('button,input,select,textarea,[tabindex="0"]')].filter(x=>!x.disabled&&x.getClientRects().length&&getComputedStyle(x).visibility!=='hidden');
let a11yBack=new WeakMap();
function a11yDialog(s){ if(!s.hidden){ if(s.contains(document.activeElement)) return;
    a11yBack.set(s,document.activeElement);
    setTimeout(()=>{ if(s.hidden) return; const f=a11yFocusable(s), pick=f.find(x=>x.classList.contains('pri'))||f[0]; if(pick) pick.focus(); },0); }
  else if(a11yBack.has(s)){ const back=a11yBack.get(s); a11yBack.delete(s);
    if(back&&back.isConnected&&(s.contains(document.activeElement)||document.activeElement===document.body)) back.focus(); } }
document.addEventListener('keydown',e=>{ if(e.key!=='Tab') return;
  const open=$$('.scrim').filter(s=>!s.hidden&&s.getClientRects().length); const s=open[open.length-1]; if(!s) return;
  const f=a11yFocusable(s); if(!f.length) return;
  const i=f.indexOf(document.activeElement);
  if(e.shiftKey&&(i<=0)){ e.preventDefault(); f[f.length-1].focus(); }
  else if(!e.shiftKey&&(i<0||i===f.length-1)){ e.preventDefault(); f[0].focus(); } });

/* ---- Larger UI text (Appearance) ---- */
function applyTextSize(v){ root.dataset.textsize=v==='large'?'large':'normal'; setSw('#swTextSize',v==='large'); }
{ const m=typeof settingsMount==='function'&&settingsMount('appearance.textsize');
  if(m){ m.innerHTML='<div class="set" data-kw="larger bigger text size font zoom accessibility read"><div><div class="lab">Larger UI text</div><div class="ps">Makes the text and controls of this window bigger. The subtitle bar has its own size</div></div><button class="sw" data-sw id="swTextSize"></button></div>';
    const sw=m.querySelector('#swTextSize');
    sw.onclick=()=>{ const on=!sw.classList.contains('on'), v=on?'large':'normal'; applyTextSize(v); const b=bridge(); if(b) b.set_setting('ui_text_size',v); }; } }
async function textSizeBoot(){ const b=bridge(); if(!b) return; try{ const s=await b.state(); applyTextSize(s.uiTextSize); }catch(e){ console.error('text size boot',e); } }

/* ---- start ---- */
let a11yQueued=false;
function a11yRun(){ a11yQueued=false; a11yEnhance(); $$('.scrim').forEach(a11yDialog); }
new MutationObserver(()=>{ if(!a11yQueued){ a11yQueued=true; queueMicrotask(a11yRun); } })
  .observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['class','hidden']});
a11yStatic(); a11yRun();
document.addEventListener('DOMContentLoaded',()=>{ if(bridge()) textSizeBoot(); else window.addEventListener('pywebviewready',textSizeBoot); });
