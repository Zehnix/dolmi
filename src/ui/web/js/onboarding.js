/* Dolmi web UI — first-run onboarding and the 'download models first?' dialog.
   Classic script (not a module): top-level names are shared with the other js/*.js files.
   Load order (index.html): core, live, meetings, assistant, vocab, settings, onboarding. */
/* onboarding */
const STEPS=[
 {h:'Welcome to<br>Dolmi.',p:'Dolmi captions your meeting in real time — 27 languages into English, or English into German — with the original underneath — all on this {computer}. Only the optional Assistant chat goes to the AI.',b:''},
 {h:'Pick your<br><em>languages.</em>',p:'Choose what\'s being spoken. More languages are in Settings → Spoken language.',b:'<div class="prov"><button data-lang="de"><b>German</b><span>→ English</span></button><button data-lang="en"><b>English</b><span>→ German</span></button><button data-lang="auto"><b>Auto</b><span>detect → English</span></button></div>'},
 {h:'You\'re<br><em>ready.</em>',p:'Dolmi checked this {computer}: {PC}. Pick your speech model in Settings → Speech models, or press Start and Dolmi asks before downloading the one that suits this {computer}.',b:'<div class="chips"><span class="chipt on">{MODEL}</span><span class="chipt on">Invisible on</span><span class="chipt on">Subtitle bar on</span></div>'}
];
let step=0;
function render(){ const s=STEPS[step];
  $('#onbBody').innerHTML=`<h3 class="pop">${s.h}</h3><p class="pop">${wt(s.p.replace('{PC}',pcSummary??'this '+W('computer')))}</p><div class="pop">${s.b.replace('{MODEL}',modelChip)}</div>`;
  $$('#onbBody [data-lang]').forEach(x=>{ x.classList.toggle('on',x.dataset.lang===curLang); x.onclick=()=>pickLang(x.dataset.lang); });
  [...$('#steps').children].forEach((d,i)=>d.classList.toggle('on',i<=step));
  $('#onbBack').style.visibility=step?'visible':'hidden';
  $('#onbNext').textContent=step===STEPS.length-1?'Start using Dolmi':'Continue';
  $$('.prov button').forEach(b=>b.onclick=()=>$$('.prov button').forEach(x=>x.classList.toggle('on',x===b))); icons();
}
$('#openOnb').onclick=()=>{step=0;render();$('#onb').hidden=false;};
$('#onbNext').onclick=()=>{ if(step<STEPS.length-1){step++;render();} else $('#onb').hidden=true; };
$('#onbBack').onclick=()=>{ if(step){step--;render();} };
$('#onb').onclick=e=>{ if(e.target.id==='onb') $('#onb').hidden=true; };
document.addEventListener('keydown',e=>{ if(e.key==='Escape') $('#onb').hidden=true; });

/* Start with a model that isn't downloaded yet: the user decides, nothing downloads silently */
function askDownload(missing){
  let s=document.getElementById('dlModal');
  if(!s){ s=document.createElement('div'); s.id='dlModal'; s.className='scrim'; document.body.append(s); s.onclick=e=>{ if(e.target.id==='dlModal') s.hidden=true; }; }
  const total=missing.reduce((a,m)=>a+m.sizeMb,0);
  s.innerHTML=`<div class="onb" style="max-width:480px"><h3 style="font-size:24px">Download models first?</h3><p style="margin-top:10px;color:var(--mute);font-size:14px;line-height:1.55">Dolmi needs these on this ${W('computer')} before it can caption. They download once (${fmtMb(total)}) and then work offline.</p><div class="mlist">${missing.map(m=>`<div class="set" style="padding:6px 0"><b>${esc(m.name)}</b><span class="mtag">${fmtMb(m.sizeMb)}</span></div>`).join('')}</div><div class="foot" style="justify-content:flex-end;gap:8px"><button class="btn ghost" id="dlPick">Choose another model</button><button class="btn pri" id="dlGo">Download &amp; start</button></div></div>`;
  s.hidden=false;
  s.querySelector('#dlPick').onclick=()=>{ s.hidden=true; openSettings('speech','modelsCard'); };
  s.querySelector('#dlGo').onclick=()=>{ s.hidden=true; startLive(true); };
}
