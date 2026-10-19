/* Dolmi web UI — Settings → Captions & languages → "Subtitles by" (QA-7, experimental, off by default).
   Local Opus-MT always shows the line first; with Gemini or DeepL chosen, each finished line is also sent
   there and replaced in place when a better subtitle comes back within 1.5 s (live.js onLineUpdate).
   Classic script, loaded after settings.js (settingsMount). */
(()=>{ const s=document.createElement('style'); s.textContent=`
.pair .en.cloudfix .ts::after{content:' · cloud';color:var(--faint)}
.trx-note{margin-top:6px;font-size:12px;line-height:1.45;color:var(--mute)}
.trx-note b{color:var(--ink);font-weight:600}
.trx-warn{color:var(--warn,#b25a00)}`; document.head.append(s); })();

const TRX={translator:'local',providers:{},status:'experimental - not benchmarked yet',stats:null};
function trxRender(slot){
  const st=TRX, p=st.providers[st.translator], cloud=st.translator!=='local';
  const opts=[['local','This computer (Opus-MT)']].concat(Object.entries(st.providers).map(([k,v])=>[k,v.label+' — cloud']));
  slot.innerHTML=`<div class="set" data-kw="subtitles by translator translation cloud gemini deepl opus better context experimental"><div><div class="lab">Subtitles by <span class="mtag">experimental</span></div>`
    +`<div class="ps">${cloud?'Each finished line is shown from this computer first, then replaced if '+esc(p?p.label:'the service')+' answers within 1.5 s':'Translated on this computer — text never leaves it'}</div></div>`
    +`<select class="field" id="setTranslator">${opts.map(([k,l])=>`<option value="${esc(k)}" ${k===st.translator?'selected':''}>${esc(l)}</option>`).join('')}</select></div>`
    +(cloud&&p?`<div class="set" data-kw="translator key deepl gemini api key cost privacy"><div><div class="lab">${esc(p.label)} key</div>`
      +`<div class="ps">${p.hasKey?'Saved, encrypted on this computer':'No key saved yet — until then subtitles stay local'}${st.translator==='gemini'?' · same key as Assistant → Gemini':''}</div>`
      +`<div class="trx-note">${esc(st.status[0].toUpperCase()+st.status.slice(1))}. Cost: <b>${esc(p.cost)}</b>, on your own key. Only finished lines and the 3 before them are sent, never audio or drafts.</div>`
      +`<div class="trx-note trx-warn">${esc(p.privacy)}</div>`
      +(st.stats?`<div class="trx-note" id="trxStats">${trxStats(st.stats)}</div>`:'')
      +`</div><input class="field" id="setTrxKey" type="password" placeholder="${p.hasKey?'•••••• saved':'Paste key'}" autocomplete="off" spellcheck="false" style="width:170px;font-family:var(--mono)"></div>`:'');
  const sel=$('#setTranslator'); if(sel) sel.onchange=async()=>{ const b=bridge(); if(!b){ TRX.translator=sel.value; trxRender(slot); return; }
    const r=await b.translator_set(sel.value); if(r&&r.ok){ Object.assign(TRX,r); trxRender(slot); toast(sel.value==='local'?'Subtitles from this computer only':'Better subtitles from '+TRX.providers[sel.value].label+' from the next line'); } };
  const key=$('#setTrxKey'); if(key) key.onchange=async()=>{ const v=key.value.trim(), b=bridge(); if(!v||!b) return;
    const r=await b.translator_set_key(TRX.translator,v); key.value=''; if(r&&r.ok){ Object.assign(TRX,r); trxRender(slot); toast('Key saved'); } };
}
function trxStats(s){ const lat=s.latency_p50==null?'':' · typical answer '+s.latency_p50.toFixed(1)+' s';
  return `This meeting: ${s.replaced} lines improved, ${s.same} unchanged, ${s.late} too slow, ${s.errors} failed${lat}`; }
async function trxLoad(){ const slot=settingsMount('captions.translator'); if(!slot) return; const b=bridge();
  if(b&&b.translator_state){ try{ Object.assign(TRX,await b.translator_state()); }catch(e){ console.error('translator',e); } }
  trxRender(slot); }
document.addEventListener('DOMContentLoaded',()=>{ if(bridge()) trxLoad(); else { trxLoad(); window.addEventListener('pywebviewready',trxLoad); } });
/* refresh the counts when Settings opens */
$$('.nav button[data-v="settings"]').forEach(b=>b.addEventListener('click',()=>{ if(bridge()&&TRX.translator!=='local') trxLoad(); }));
