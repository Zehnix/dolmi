/* Headless checks of the subtitle bar (src/ui/web/overlay.html) for tests/ui/run_ui_tests.py: lines show,
   the corner buttons call the bar's bridge, and the click-through lock (APP-8) hides the controls.
   Writes {ok, failures, checks} into pre#result. */
(function(){
  const failures=[]; let checks=0;
  const ok=(c,w)=>{ checks++; if(!c) failures.push(w); };
  const tick=(ms=40)=>new Promise(r=>setTimeout(r,ms));
  const q=s=>document.querySelector(s);
  const shown=e=>!!e&&e.getClientRects().length>0&&getComputedStyle(e).visibility!=='hidden';
  const n=name=>window.__calls.filter(c=>c.name===name).length;
  async function run(){
    try{
      await tick(100);
      window.ov.style(.7,24); window.ov.line('Hallo zusammen.','Hello everyone.'); await tick();
      ok(q('#body .en')&&q('#body .en').textContent==='Hello everyone.','bar: a line does not show');
      window.ov.line('Gut.','Good.','10:42'); await tick();
      ok(q('#body .tm')&&q('#body .tm').textContent==='10:42','bar: a line with a time does not show it');
      ok(document.querySelectorAll('#body .tm').length===1,'bar: a line without a time shows one');
      q('#srcb').click(); await tick();
      ok(n('toggle_source')===1,'bar: the source button does not call toggle_source');
      q('#close').click(); await tick();
      ok(n('hide')===1,'bar: the close button does not call hide');
      if(window.ov.locked){
        window.ov.locked(true); await tick();
        ok(document.documentElement.classList.contains('locked'),'bar: locked(true) does not mark the bar locked');
        ok(!shown(q('#close'))&&!shown(q('#grip'))&&!shown(q('#srcb')),'bar: close/resize/source controls still show while locked');
        ok(shown(q('#lockMark')),'bar: no lock mark while locked');
        window.ov.locked(false); await tick();
        ok(shown(q('#close'))&&shown(q('#grip'))&&!shown(q('#lockMark')),'bar: controls do not come back after unlock');
      }
      ok(!window.__errors.length,'bar: JavaScript errors: '+window.__errors.join(' | '));
    }catch(e){ failures.push('harness error: '+String(e&&e.stack||e)); }
    const pre=document.createElement('pre'); pre.id='result';
    pre.textContent=JSON.stringify({ok:!failures.length,failures,checks,viewport:{w:innerWidth,h:innerHeight,dpr:devicePixelRatio}});
    document.body.append(pre);
  }
  document.addEventListener('DOMContentLoaded',()=>setTimeout(run,0));
  if(document.readyState!=='loading') setTimeout(run,0);
})();
