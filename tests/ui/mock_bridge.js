/* Mock of the Python bridge (window.pywebview.api) for the headless UI tests (APP-11).
   Every call is recorded in window.__calls as {name, args}; known methods answer with canned data
   shaped like src/ui/app.py's, any other method answers {} and is listed in window.__unknown.
   Loaded before the page's own scripts by tests/ui/run_ui_tests.py. Fake data only. */
(function(){
  const calls=[], unknown=[];
  window.__calls=calls; window.__unknown=unknown;
  window.__errors=[];
  window.addEventListener('error',e=>window.__errors.push(String(e.error&&e.error.stack||e.message||e)));
  window.addEventListener('unhandledrejection',e=>window.__errors.push('promise: '+String(e.reason&&e.reason.stack||e.reason)));

  const WORDS={computer:'PC',COMPUTER:'PC',os_user:'Windows user',mod:'Ctrl',hotkey:'Ctrl+Alt+Shift+D',taskbar:'taskbar',
    speaker_default:'Default speaker',mic_privacy:'Windows privacy settings',speaker_hint:'The speaker your meeting plays through',
    key_storage:'Encrypted with Windows DPAPI',fit_needs_gpu:'Needs NVIDIA GPU',fit_slow:'Slow on this PC'};
  const state={speechSource:'local',language:'de',showGerman:true,invisible:false,saveTranscripts:true,keepDays:30,
    provider:'claude',providers:{claude:'Claude',openai:'OpenAI',gemini:'Gemini',nvidia:'NVIDIA',cloudflare:'Cloudflare'},
    model:'claude-sonnet',hasKey:false,modelDefault:'claude-sonnet',keyHint:'sk-ant-…',cfAccount:'',
    audioDevice:'',audioSource:'speaker',micDevice:'',sourceLabel:{source:'speaker',device:'Default speaker'},
    speechModel:'auto',overlay:true,theme:'paper',listening:false,opacity:.88,font:22,warnSilence:true,barVisible:false,
    engine:'not loaded',engineState:'idle',dataFolder:'C:\\Users\\Test\\Documents\\Dolmi',version:'2.2.0',
    checkUpdates:true,barLocked:false,shortcutsOn:true,
    shortcuts:{toggle_live:'ctrl+alt+shift+s',toggle_bar:'ctrl+alt+shift+b',toggle_source:'ctrl+alt+shift+m',toggle_lock:'ctrl+alt+shift+l'},
    shortcutStatus:{toggle_live:true,toggle_bar:true,toggle_source:true,toggle_lock:false},
    platform:{os:'win',words:WORDS}};
  const meetings=[
    {name:'meeting_2026-10-19_10-00.md',day:'Mon 19 Oct 2026',time:'10:00',lines:3,date:'2026-10-19',title:'Release planning for the next sprint',titleSource:'lines'},
    {name:'meeting_2026-10-12_09-30.md',day:'Mon 12 Oct 2026',time:'09:30',lines:2,date:'2026-10-12',title:'Weekly sync',titleSource:'custom'},
    {name:'meeting_2026-09-01_14-15.md',day:'Tue 01 Sep 2026',time:'14:15',lines:1,date:'2026-09-01',title:'Kick-off',titleSource:'summary'}];
  const lines=[
    {ts:'10:00:05',end:'10:00:08',offset:'+00:05',de:'Wir planen das Release für den nächsten Sprint.',en:'We are planning the release for the next sprint.'},
    {ts:'10:00:12',end:'10:00:15',offset:'+00:12',de:'Die Tests laufen jetzt in der Pipeline.',en:'The tests now run in the pipeline.'},
    {ts:'10:01:10',end:'',offset:'+01:10',de:'Danke, bis morgen.',en:'Thanks, see you tomorrow.'}];
  const models=[
    {key:'large-v3-turbo',name:'Whisper Large v3 Turbo',kind:'speech',sizeMb:1600,installed:true,downloading:false,fit:'Runs well on your GPU',fitOk:true,fitLevel:'ok',note:'Near best accuracy.',needed:true,loaded:false,selected:false,auto:true},
    {key:'small',name:'Whisper Small',kind:'speech',sizeMb:480,installed:false,downloading:false,fit:'Runs well on your GPU',fitOk:true,fitLevel:'ok',note:'Good balance.',needed:false,loaded:false,selected:false,auto:false},
    {key:'medium',name:'Whisper Medium',kind:'speech',sizeMb:1500,installed:true,downloading:false,fit:'Runs well on your GPU',fitOk:true,fitLevel:'ok',note:'More accurate.',needed:false,loaded:false,selected:false,auto:false},
    {key:'opus-de-en',name:'Opus-MT German → English',kind:'translate',sizeMb:300,installed:true,downloading:false,fit:'Runs well',fitOk:true,fitLevel:'ok',note:'Translator.',needed:true,loaded:false,selected:false,auto:false}];
  const canned={
    state:()=>JSON.parse(JSON.stringify(state)),
    set_setting:()=>true,
    pc_info:()=>({ram_gb:32,cores:16,gpu:true,gpu_name:'NVIDIA GeForce RTX 4060',model_dir:'C:\\Users\\Test\\.cache\\huggingface\\hub',data_dir:state.dataFolder}),
    devices:()=>['Speakers (Realtek Audio)','Headset (USB)'],
    mic_devices:()=>['Microphone (USB)','Microphone Array'],
    list_models:()=>models,
    list_ai_models:()=>({models:['claude-sonnet','claude-haiku'],live:false,current:'claude-sonnet',default:'claude-sonnet'}),
    set_provider:p=>({...state,provider:p}),
    set_ai_model:m=>({...state,model:m}),
    set_cf_account:a=>({ok:true,...state,cfAccount:a}),
    set_key:()=>true,
    test_key:()=>({ok:false,message:'No key yet'}),
    set_source:s=>({source:s,device:s==='mic'?'Default microphone':'Default speaker'}),
    toggle_source:()=>({source:'mic',device:'Default microphone'}),
    start_listening:()=>({ok:true}),
    stop_listening:()=>true,
    missing_models:()=>[],
    use_model:()=>true, download_model:()=>true, remove_model:()=>({ok:true}),
    list_meetings:()=>meetings,
    search_meetings:(q)=>meetings.filter(m=>!q||/sprint/i.test(q)).map(m=>q?{...m,matches:1,hits:[lines[0]]}:m).slice(0,q?1:3),
    rename_meeting:(n,title)=>({ok:true,title:title||'Weekly sync',titleSource:title?'custom':'lines'}),
    read_meeting:()=>lines,
    meeting_txt:()=>'Dolmi meeting',
    export_meeting:()=>({ok:false,cancelled:true}),
    archive_meeting:()=>true, delete_meeting:()=>true,
    read_vocab:()=>({vocabulary:'[keep]\nNordlicht GmbH\n',glossary:'Nordlicht\n'}),
    validate_vocab:()=>({ok:true,vocabulary:[],glossary:[]}),
    save_vocab:()=>({ok:true,problems:{ok:true,vocabulary:[],glossary:[]}}),
    list_chats:()=>[{id:1,title:'What did Lena say?',when:'Mon 19 Oct, 10:05',questions:1}],
    open_chat:()=>[{q:'What did Lena say?',a:'She will look at it next week.',err:'',when:'10:05'}],
    new_chat:()=>true, delete_chat:()=>true, archive_chat:()=>true, ask:()=>1,
    summarize:()=>({ok:false,message:'Add an API key in Settings → Assistant first.'}),
    show_overlay:()=>true, hide_overlay:()=>true, toggle_invisible:on=>on, recover:()=>true,
    check_update:()=>({available:true,version:'2.3.0',url:'https://github.com/Zehnix/dolmi/releases/tag/v2.3.0',current:'2.2.0'}),
    open_release:()=>true,
    pause_shortcuts:on=>({ok:true,status:on?{}:state.shortcutStatus}),
    set_shortcut:(name,combo)=>({ok:true,shortcuts:{...state.shortcuts,[name]:combo||state.shortcuts[name]},status:state.shortcutStatus}),
    set_bar_locked:on=>({locked:!!on,applied:true}), toggle_bar_lock:()=>({locked:true,applied:true}),
    copy_text:()=>true, open_data_folder:()=>true, open_model_folder:()=>true, open_mic_privacy:()=>true,
    geometry:()=>({w:640,h:200}), resize:()=>true, hide:()=>true,   // the subtitle bar's own bridge (overlay.py)
  };
  window.__canned=canned; window.__state=state;
  window.pywebview={api:new Proxy({},{get:(t,name)=>{
    if(typeof name!=='string'||name==='then') return undefined;
    return (...args)=>{ calls.push({name,args});
      if(!(name in canned)){ unknown.push(name); return Promise.resolve({}); }
      try{ return Promise.resolve(canned[name](...args)); }catch(e){ return Promise.reject(e); } };
  }})};
})();
