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
    ['audio','#swVoice','click','set_setting','voice_readout'],                           // VOICE-1
    ['captions','#setSource','change:gemini','set_setting','speech_source'],
    ['captions','#setSource','change:local','set_setting','speech_source'],
    ['captions','#setLang','change:en','set_setting','language'],
    ['captions','#setLang','change:de','set_setting','language'],
    ['captions','#swGerman','click','set_setting','show_german'],
    ['speech','#setModel','change:small','set_setting','model'],
    ['speech','#swPreload','click','set_setting','preload_models'],
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
    ['bar','#swBarLock','click','set_bar_locked',true],                                       // APP-8
    ['bar','#swBarLock','click','set_bar_locked',false],
    ['bar','#swBarTime','click','set_setting','bar_time'],                                     // LIVE-5
    ['about','#swUpdates','click','set_setting','check_updates'],                          // APP-6
    ['about','#setCheckUpdate','click','check_update',true],
    ['about','#swShortcuts','click','set_setting','shortcuts_on'],                          // APP-7
    ['about','#sc-toggle_live','rec:ctrl+alt+shift+KeyK','set_shortcut','toggle_live'],
    ['about','#sc-toggle_bar','rec:ctrl+alt+shift+F9','set_shortcut','toggle_bar'],
    ['about','#sc-toggle_source','rec:ctrl+alt+shift+Digit7','set_shortcut','toggle_source'],
    ['about','#sc-toggle_lock','rec:Backspace','set_shortcut','toggle_lock'],
    ['about','#setDownloadUpdate','click','open_release','https://github.com/Zehnix/dolmi/releases/tag/v2.3.0'],
    ['about','#setReport','click','report_problem',false],                                  // APP-12
    ['about','#swRepTranscripts','click','report_problem',null,true],                      // local switches: no call
    ['about','#swRepVocab','click','report_problem',null,true],
    ['about','#setReport','click','report_problem',true],                                   // now with transcripts
    ['captions','#setTranslator','change:deepl','translator_set','deepl'],                 // QA-7
    ['captions','#setTrxKey','change:mock-key','translator_set_key','deepl'],
    ['captions','#setTranslator','change:local','translator_set','local'],
    ['privacy','#swSpeakers','click','set_setting','speaker_labels'],                      // SPK-4
    ['audio','#swCaptureBoth','click','set_setting','capture_both'],                    // SPK-5
  ];
  window.__uiControls=CONTROLS;   // other checks (new features) push rows here before run() starts
  async function use(el,how){
    const [act,val]=how.split(/:(.*)/s);
    if(act==='click') el.click();
    else if(act==='click2'){ el.click(); await tick(); el.click(); }
    else if(act==='change'){ el.value=val; el.dispatchEvent(new Event('change',{bubbles:true})); }
    else if(act==='input'){ el.value=val; el.dispatchEvent(new Event('input',{bubbles:true})); await tick(250); }
    else if(act==='rec'){ el.click(); await tick(); const parts=val.split('+'), code=parts.pop();
      const has=m=>parts.includes(m), key=code==='Backspace'?'Backspace':code.replace(/^Key|^Digit/,'');
      document.dispatchEvent(new KeyboardEvent('keydown',{key,code,ctrlKey:has('ctrl'),altKey:has('alt'),shiftKey:has('shift'),metaKey:has('meta'),bubbles:true})); }
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
      const again=q(sel); if(again) used.add(again);   /* a control its own action re-rendered */
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
    /* APP-9: titles, search, date filter, rename */
    ok(qa('#mRows .row b').map(b=>b.textContent).join('|')==='Release planning for the next sprint|Weekly sync|Kick-off',`${label}: meeting titles not shown: ${qa('#mRows .row b').map(b=>b.textContent).join('|')}`);
    let m=mark(); const s=q('#mSearch'); s.value='Sprint'; s.dispatchEvent(new Event('input')); await tick(450);
    const sc=since(m,'search_meetings');
    ok(sc.length===1&&sc[0].args[0]==='Sprint',`${label}: search does not call search_meetings('Sprint'): ${JSON.stringify(sc)}`);
    ok(qa('#mRows .row').length===1&&q('#mRows .mhit mark')&&/sprint/i.test(q('#mRows .mhit mark').textContent),`${label}: a search hit is not shown with the matching line`);
    noOverflow(q('#v-meetings'),`${label} meetings search`);
    s.value='planning'; s.dispatchEvent(new Event('input')); await tick(450);      // a word only in the English line
    ok(q('#mRows .mhit mark')&&q('#mRows .mhit mark').textContent==='planning',`${label}: a hit only in the English line is not shown marked`);
    s.value='zzz-nothing'; s.dispatchEvent(new Event('input')); await tick(450);
    ok(/No meeting matches/.test(q('#mRows').textContent),`${label}: an empty search result says nothing`);
    s.value=''; s.dispatchEvent(new Event('input')); await tick(450);
    m=mark(); const w=q('#mWhen'); w.value='7'; w.dispatchEvent(new Event('change')); await tick(200);
    const dc=since(m,'search_meetings');
    ok(dc.length===1&&/^\d{4}-\d\d-\d\d$/.test(dc[0].args[2])&&dc[0].args[3]>=dc[0].args[2],`${label}: the date filter does not ask for dates: ${JSON.stringify(dc)}`);
    w.value='day'; w.dispatchEvent(new Event('change')); await tick();
    ok(visible(q('#mDay')),`${label}: 'On a day' does not show a date field`);
    m=mark(); q('#mDay').value='2026-10-12'; q('#mDay').dispatchEvent(new Event('change')); await tick(200);
    ok(since(m,'search_meetings').some(c=>c.args[2]==='2026-10-12'&&c.args[3]==='2026-10-12'),`${label}: a day does not filter to that day`);
    w.value=''; w.dispatchEvent(new Event('change')); await tick(200);
    ok(!visible(q('#mDay'))&&since(m,'list_meetings').length>0,`${label}: 'Any time' does not list every meeting again`);
    m=mark(); q('#mRows [data-ren]').click(); await tick();
    const inp=q('#mTitleEdit'); ok(inp&&visible(inp),`${label}: rename does not open a title field`);
    if(inp){ inp.value='Budget review'; inp.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true})); await tick(100);
      ok(since(m,'rename_meeting').some(c=>c.args[1]==='Budget review'),`${label}: rename does not call rename_meeting`);
      ok(q('#mRows .row.a b').textContent==='Budget review',`${label}: the new title is not shown`); }
    noOverflow(q('#v-meetings'),`${label} meetings after filters`);
    /* APP-10: a structured summary renders as cards with the two copy buttons */
    sumOpen(); sumDone('',{title:'Budget review',summary:'We went over the budget.',decisions:['Keep the date'],
      action_items:[{who:'Lena',what:'Send the numbers',when:'Friday'}],open_questions:[],email:'Subject: x',teams:'**x**'});
    ok(q('#sumTitle')&&q('#sumTitle').textContent==='Budget review'&&qa('#sumModal .sum-card').length===4,`${label}: the summary cards do not render`);
    ok(qa('#sumModal .sum-act .mtag').map(e=>e.textContent).join('|')==='Lena|Friday',`${label}: action item owner/deadline not shown`);
    m=mark(); q('#sumEmail').click(); await tick(); ok(since(m,'copy_text').some(c=>c.args[0]==='Subject: x'),`${label}: Copy as email does not copy the email text`);
    m=mark(); q('#sumTeams').click(); await tick(); ok(since(m,'copy_text').some(c=>c.args[0]==='**x**'),`${label}: Copy as Teams does not copy the Teams text`);
    noOverflow(q('#sumModal .onb'),`${label} summary cards`);
    sumClose();
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
    /* APP-4: the status says Ready only once the models are loaded */
    ok(/^Not loaded/.test(q('#status').textContent),`${label}: status before the models load: ${q('#status').textContent}`);
    window.dolmi.onEngine('speech and translation models','loading'); await tick();
    ok(/^Loading/.test(q('#status').textContent),`${label}: status while the models load: ${q('#status').textContent}`);
    window.dolmi.onEngine('CUDA · large-v3-turbo','ready'); await tick();
    ok(/^Ready/.test(q('#status').textContent),`${label}: status once loaded: ${q('#status').textContent}`);
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
  /* APP-7/8: a global shortcut does what the button on screen does; the lock switch follows Python */
  window.__uiExtra.push(async(label,{ok,tick,q,mark,since,visible})=>{
    openSettings('about'); await tick();
    ok(!visible(q('#setDownloadUpdate'))&&/Not checked/.test(q('#updStatus').textContent),`${label}: 'Download' shows before any newer version is known`);
    window.dolmi.onUpdate({available:true,version:'2.4.0',url:'https://github.com/Zehnix/dolmi/releases/tag/v2.4.0',current:'2.2.0'}); await tick();
    ok(visible(q('#setDownloadUpdate'))&&q('#setDownloadUpdate').textContent==='Download 2.4.0',`${label}: a newer version found at start is not offered`);
    window.dolmi.onUpdate({available:false,version:'2.2.0',url:'',current:'2.2.0'}); await tick();
    nav('live'); await tick();
    let m=mark(); window.dolmi.onShortcut('toggle_live'); await tick(80);
    ok(since(m,'start_listening').length===1,`${label}: the start/stop shortcut does not start captions`);
    window.dolmi.onListening(false,''); await tick();
    m=mark(); window.dolmi.onShortcut('toggle_bar'); await tick(80);
    ok(since(m,'show_overlay').length+since(m,'hide_overlay').length===1,`${label}: the bar shortcut does not show/hide the bar`);
    m=mark(); window.dolmi.onShortcut('toggle_source'); await tick(80);
    ok(since(m,'set_source').length===1,`${label}: the source shortcut does not switch the source`);
    window.dolmi.onBarLock(true); await tick();
    ok(q('#swBarLock').classList.contains('on'),`${label}: the lock switch does not follow a lock by shortcut`);
    window.dolmi.onBarLock(false); await tick();
    ok(/Ctrl\+Alt\+Shift\+L/.test(q('#barLockHint').textContent),`${label}: the lock hint does not name the unlock shortcut`);
    ok(/Ctrl\+Alt\+Shift\+S/.test(q('#sc-toggle_live').textContent),`${label}: shortcut keys are not shown (${q('#sc-toggle_live').textContent})`);
    ok(/another app/i.test(q('#scs-toggle_lock').textContent),`${label}: a shortcut another app owns is not flagged`);
  });

  /* LIVE-4: a dropped draft stays as a dim 'not understood' row with the reason; it is not a line */
  window.__uiExtra.push(async(label,{ok,tick,q,qa,nav})=>{
    nav('live'); await tick(); resetFeed(); await tick();
    const n0=+q('#sLines').textContent;
    window.dolmi.onDraft('Ähm also','Um so',{start:Date.now()/1000-1,end:Date.now()/1000}); await tick();
    window.dolmi.onDraft('','',{dropped:'junk',start:Date.now()/1000-1,end:Date.now()/1000}); await tick();
    const d=q('#brows .pair.dropped');
    ok(d&&/junk/.test(d.title)&&/not understood/.test(d.textContent),`${label}: a dropped draft does not stay as 'not understood'`);
    ok(+q('#sLines').textContent===n0,`${label}: a dropped draft counts as a line`);
    window.dolmi.onLine('10:01','Gut.','Good.',{start:Date.now()/1000,end:Date.now()/1000}); await tick();
    ok(qa('#brows .pair.dropped').length===1&&qa('#brows .pair').length===2,`${label}: a final removed the not-understood row`);
    window.dolmi.onDraft('','',{dropped:'stale'}); await tick();
    ok(qa('#brows .pair.dropped').length===1,`${label}: a dropped draft with no draft on screen added a row`);
    resetFeed(); await tick();
    ok(!qa('#brows .pair').length,`${label}: resetFeed leaves the not-understood row`);
  });

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
