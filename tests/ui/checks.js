/* Headless UI checks for the whole app (APP-11). tests/ui/run_ui_tests.py puts mock_bridge.js before the
   page's scripts and this file after them, opens the page in headless Edge at several window sizes and
   display scales, and reads the JSON this writes into pre#result:
     {ok, failures:[...], checks:n, viewport:{w,h,dpr}, unknown:[bridge methods without canned data]}
   With ?shot=<view>[:<section>] it only opens that screen (for --screenshot) and reports nothing. */
(function(){
  const failures=[]; let checks=0;
  const ok=(cond,what)=>{ checks++; if(!cond) failures.push(what); return !!cond; };
  const tick=(ms=40)=>new Promise(r=>setTimeout(r,ms));
  const q=s=>document.querySelector(s), qa=s=>[...document.querySelectorAll(s)];
  const calls=()=>window.__calls;
  const mark=()=>calls().length;
  const since=(m,name)=>calls().slice(m).filter(c=>c.name===name);
  const visible=e=>!!e&&e.getClientRects().length>0&&getComputedStyle(e).visibility!=='hidden';
  const nav=v=>q(`.nav button[data-v="${v}"]`).click();
  const describe=e=>e.id?'#'+e.id:(e.tagName.toLowerCase()+(e.className&&typeof e.className==='string'?'.'+e.className.trim().split(/\s+/).join('.'):''));
  const W=()=>document.documentElement.clientWidth;

  /* nothing in the open view may stick out of the window (that is a horizontal scroll / clipped control) */
  function noOverflow(scope,label){
    const w=W(), bad=[];
    for(const e of [q('.top'),...scope.querySelectorAll('*')]){ if(!visible(e)) continue;
      if(e.closest('svg')&&e.tagName.toLowerCase()!=='svg') continue;
      const r=e.getBoundingClientRect(); if(r.width===0) continue;
      if(r.right>w+1||r.left<-1) bad.push(describe(e)+' '+Math.round(r.left)+'..'+Math.round(r.right)); }
    ok(!bad.length,`${label}: sticks out of the ${w}px window: ${bad.slice(0,4).join(', ')}`);
    for(const e of [scope,...scope.querySelectorAll('.wrap,.list,.detail,.set-pages,.set-page.a,.brows')]){ if(!visible(e)) continue;
      const cs=getComputedStyle(e); if(cs.overflowX==='hidden') continue;
      ok(e.scrollWidth<=e.clientWidth+1,`${label}: ${describe(e)} scrolls sideways (${e.scrollWidth} > ${e.clientWidth})`); }
  }

  /* a switch sits on its label's line, to the right of it (the old bug: toggles wrapped below the text) */
  function noWrappedToggles(page,label){
    for(const sw of page.querySelectorAll('.set .sw')){ if(!visible(sw)) continue;
      const row=sw.closest('.set'), lab=row.firstElementChild===sw?null:row.firstElementChild;
      const s=sw.getBoundingClientRect();
      ok(Math.round(s.height)<=30&&Math.round(s.width)>=40,`${label}: ${describe(sw)} is squashed (${Math.round(s.width)}x${Math.round(s.height)})`);
      if(!lab) continue;
      const l=lab.getBoundingClientRect();
      ok(s.left>=l.right-1&&s.top<l.bottom&&s.bottom>l.top,`${label}: ${describe(sw)} wrapped below its label`); }
  }

  /* Settings: open every section, then use every control and check it calls the bridge */
  const CONTROLS=[
    // [section, selector, action, bridge method, expected first argument (optional)]
    ['audio','#setSrc [data-s="mic"]','click','set_source','mic'],
    ['audio','#setSrc [data-s="speaker"]','click','set_source','speaker'],
    ['audio','#setDevice','change:Headset (USB)','set_setting','audio_device'],
    ['audio','#setMic','change:Microphone (USB)','set_setting','mic_device'],
    ['audio','#swWarnSilence','click','set_setting','warn_silence'],
    ['captions','#setSource','change:gemini','set_setting','speech_source'],
    ['captions','#setSource','change:local','set_setting','speech_source'],
    ['captions','#setLang','change:en','set_setting','language'],
    ['captions','#setLang','change:de','set_setting','language'],
    ['captions','#swGerman','click','set_setting','show_german'],
    ['speech','#setModel','change:small','set_setting','model'],
    ['speech','#openModels','click','open_model_folder'],
    ['speech','#modelList [data-use]','click','use_model'],
    ['speech','#modelList [data-download]','click','download_model'],
    ['speech','#modelList [data-remove]','click2','remove_model'],
    ['vocab','#setOpenVocab','click','read_vocab'],
    ['assistant','#setProvider [data-p="openai"]','click','set_provider','openai'],
    ['assistant','#setProvider [data-p="gemini"]','click','set_provider','gemini'],
    ['assistant','#setProvider [data-p="nvidia"]','click','set_provider','nvidia'],
    ['assistant','#setProvider [data-p="cloudflare"]','click','set_provider','cloudflare'],
    ['assistant','#setCfAccount','change:0123456789abcdef0123456789abcdef','set_cf_account'],
    ['assistant','#setProvider [data-p="claude"]','click','set_provider','claude'],
    ['assistant','#setKey','change:sk-test','set_key'],
    ['assistant','#setTest','click','test_key'],
    ['assistant','#setAiModel','change:claude-haiku','set_ai_model','claude-haiku'],
    ['assistant','#setAiModel','change:__other','list_ai_models',null,true],
    ['assistant','#setAiModelOther','change:my-model','set_ai_model','my-model'],
    ['bar','#swBar','click','set_setting','overlay'],
    ['bar','#setOpacity','input:50','set_setting','opacity'],
    ['bar','#setFont','input:30','set_setting','font'],
    ['privacy','#swInv','click','toggle_invisible'],
    ['privacy','#swSave','click','set_setting','save_transcripts'],
    ['privacy','#setKeep [data-d="7"]','click','set_setting','keep_days'],
    ['privacy','#setKeep [data-d="0"]','click','set_setting','keep_days'],
    ['privacy','#setKeep [data-d="90"]','click','set_setting','keep_days'],
    ['privacy','#setKeep [data-d="30"]','click','set_setting','keep_days'],
    ['privacy','#setDataOpen','click','open_data_folder'],
    ['appearance','#theme [data-t="ink"]','click','set_setting','theme'],
    ['appearance','#theme [data-t="paper"]','click','set_setting','theme'],
  ];
  window.__uiControls=CONTROLS;   // other checks (new features) push rows here before run() starts
  async function use(el,how){
    const [act,val]=how.split(/:(.*)/s);
    if(act==='click') el.click();
    else if(act==='click2'){ el.click(); await tick(); el.click(); }
    else if(act==='change'){ el.value=val; el.dispatchEvent(new Event('change',{bubbles:true})); }
    else if(act==='input'){ el.value=val; el.dispatchEvent(new Event('input',{bubbles:true})); await tick(250); }
    else if(act==='key'){ el.dispatchEvent(new KeyboardEvent('keydown',{key:val,bubbles:true})); }
  }
  async function settingsChecks(label){
    nav('settings'); await tick(150);
    const used=new Set();
    for(const sec of qa('#setMenu [data-sec]').map(b=>b.dataset.sec)){
      q(`#setMenu [data-sec="${sec}"]`).click(); await tick();
      const page=q('.set-page.a');
      if(!ok(page&&page.dataset.sec===sec&&visible(page),`${label}: Settings section ${sec} does not open`)) continue;
      ok(page.getBoundingClientRect().height>40,`${label}: Settings section ${sec} is empty`);
      noOverflow(q('#v-settings'),`${label} settings/${sec}`);
      noWrappedToggles(page,`${label} settings/${sec}`);
    }
    for(const [sec,sel,how,method,arg0,optional] of window.__uiControls){
      q(`#setMenu [data-sec="${sec}"]`).click(); await tick();
      if(q('.nav button.a').dataset.v!=='settings'){ nav('settings'); await tick(); q(`#setMenu [data-sec="${sec}"]`).click(); await tick(); }
      const el=q(sel);
      if(!ok(el&&visible(el),`${label}: control ${sel} is missing or hidden in ${sec}`)) continue;
      used.add(el);
      const m=mark(); await use(el,how); await tick(80);
      const got=since(m,method);
      if(optional&&!got.length) continue;
      ok(got.length&&(arg0==null||got.some(c=>c.args[0]===arg0)),`${label}: ${sel} (${how}) did not call ${method}(${arg0??''}); got ${calls().slice(m).map(c=>c.name).join(',')||'nothing'}`);
    }
    nav('settings'); await tick();
    /* every control in Settings must be in the table above (new controls need a check) */
    const untested=[];
    for(const sec of qa('#setMenu [data-sec]').map(b=>b.dataset.sec)){
      q(`#setMenu [data-sec="${sec}"]`).click(); await tick();
      for(const el of q('.set-page.a').querySelectorAll('button,select,input,textarea')){
        if(!visible(el)||el.disabled||used.has(el)||el.closest('#modelList')) continue;
        if(el.matches('#setSrc button,#setProvider button,#setKeep button,#theme button')&&[...el.parentElement.children].some(x=>used.has(x))) continue;
        untested.push(sec+' '+describe(el)+(el.dataset&&Object.keys(el.dataset).length?JSON.stringify(el.dataset):''));
      } }
    ok(!untested.length,`${label}: Settings controls without a check: ${untested.join('; ')}`);
    /* search finds a setting and jumps to it */
    const s=q('#setSearch'); s.value='opacity'; s.dispatchEvent(new Event('input')); await tick();
    s.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter'})); await tick();
    ok(q('.set-page.a').dataset.sec==='bar',`${label}: search 'opacity' does not open Subtitle bar`);
  }

  async function meetingsChecks(label){
    nav('meetings'); await tick(200);
    ok(since(0,'list_meetings').length>0,`${label}: Meetings did not call list_meetings`);
    const list=q('#mList'), det=q('#mDetail');
    const l=list.getBoundingClientRect(), d=det.getBoundingClientRect();
    ok(l.width>150&&d.width>300,`${label}: Meetings panes too narrow (list ${Math.round(l.width)}, transcript ${Math.round(d.width)})`);
    ok(l.right<=d.left+1&&Math.abs(l.top-d.top)<2,`${label}: Meetings list and transcript are not side by side (list ${Math.round(l.left)}..${Math.round(l.right)} top ${Math.round(l.top)}, transcript ${Math.round(d.left)} top ${Math.round(d.top)})`);
    ok(d.height>120,`${label}: Meetings transcript is squashed (${Math.round(d.height)}px)`);
    ok(det.querySelectorAll('.pair2').length===3,`${label}: Meetings transcript did not render`);
    noOverflow(q('#v-meetings'),`${label} meetings`);
  }

  async function viewChecks(label){
    for(const v of ['live','meetings','assistant','vocab','settings']){
      nav(v); await tick(120);
      const view=q('#v-'+v);
      if(!ok(visible(view)&&view.classList.contains('a'),`${label}: view ${v} does not show`)) continue;
      const r=view.getBoundingClientRect();
      ok(r.height>200,`${label}: view ${v} has no height (${Math.round(r.height)})`);
      ok(qa('.view.a').length===1,`${label}: more than one view open after ${v}`);
      noOverflow(view,`${label} ${v}`);
    }
    nav('live'); await tick();
    let m=mark(); q('#go').click(); await tick(100);
    ok(since(m,'start_listening').length===1,`${label}: Start does not call start_listening`);
    window.dolmi.onListening(true,'GPU · fake'); await tick();
    window.dolmi.onLine('10:00','Hallo zusammen.','Hello everyone.',{start:Date.now()/1000,end:Date.now()/1000,lag:0.4,lag_median:0.4});
    await tick();
    ok(qa('#brows .pair').length===1,`${label}: a caption did not render in Live`);
    m=mark(); q('#go').click(); await tick();
    ok(since(m,'stop_listening').length===1,`${label}: Stop does not call stop_listening`);
    window.dolmi.onListening(false,''); await tick();
    nav('assistant'); await tick(150);
    ok(since(0,'list_chats').length>0&&q('#aList').textContent.includes('What did Lena say'),`${label}: Assistant chats did not load`);
    nav('vocab'); await tick(150);
    ok(since(0,'read_vocab').length>0,`${label}: Vocabulary did not call read_vocab`);
    nav('settings'); await tick();
    q('#openOnb').click(); await tick();
    ok(visible(q('#onb .onb')),`${label}: onboarding does not open`);
    noOverflow(q('#onb'),`${label} onboarding`);
    document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'})); await tick();
    ok(q('#onb').hidden,`${label}: onboarding does not close with Esc`);
    ok(qa('#setDevice option').length===3&&qa('#setMic option').length===3,`${label}: speaker/microphone lists did not load`);
  }

  window.__uiExtra=window.__uiExtra||[];   // async (label, h) => {} checks for newer features

  async function run(){
    const vp={w:innerWidth,h:innerHeight,dpr:devicePixelRatio};
    const label=`${vp.w}x${vp.h}@${vp.dpr}`;
    try{
      await tick(300);                     // boot(), settingsBoot() and the first loads settle
      await viewChecks(label);
      await meetingsChecks(label);
      for(const extra of window.__uiExtra) await extra(label,{ok,tick,q,qa,mark,since,nav,visible,noOverflow,noWrappedToggles,use});
      await settingsChecks(label);         // last: it changes theme, provider and other state
      ok(!window.__errors.length,`${label}: JavaScript errors: ${window.__errors.slice(0,3).join(' | ')}`);
    }catch(e){ failures.push('harness error: '+String(e&&e.stack||e)); }
    const out={ok:!failures.length,failures,checks,viewport:vp,unknown:[...new Set(window.__unknown)]};
    const pre=document.createElement('pre'); pre.id='result';
    pre.textContent=JSON.stringify(out).replace(/[\u007f-￿]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
    document.body.append(pre);
  }

  async function shot(spec){
    const [view,section]=spec.split(':');
    await tick(300);
    if(view==='onboarding'){ nav('settings'); await tick(); q('#openOnb').click(); }
    else if(view==='settings'&&section){ openSettings(section); }
    else nav(view);
    if(view==='meetings'){ await tick(200); }
    await tick(300);
  }

  document.addEventListener('DOMContentLoaded',()=>{
    const spec=new URLSearchParams(location.search).get('shot');
    setTimeout(()=>spec?shot(spec):run(),0);
  });
})();
