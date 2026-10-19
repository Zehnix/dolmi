/* Dolmi web UI — Vocabulary view.
   Classic script (not a module): top-level names are shared with the other js/*.js files.
   Load order (index.html): core, live, meetings, assistant, vocab, settings, onboarding. */
/* ---- vocabulary (only the glossary is edited; vocabulary.txt is never overwritten from here) ---- */
let loadedVocab=null;
async function loadVocab(){
  const b=bridge(), box=$('#vocabBox');
  if(!b){ box.placeholder='Vocabulary loads when running in the app.'; return; }
  try{ loadedVocab=await b.read_vocab(); box.value=(loadedVocab.glossary||'').trim(); box.disabled=false; box.placeholder='One term per line…'; }
  catch(e){ box.placeholder='Could not load the vocabulary.'; }
}
if($('#vocabSave')) $('#vocabSave').onclick=async()=>{
  const b=bridge(), st=$('#vocabStatus');
  if(!b||!loadedVocab){ toast('Vocabulary has not loaded yet.'); return; }
  await b.save_vocab(null, $('#vocabBox').value);
  if(st){ st.style.visibility='visible'; setTimeout(()=>st.style.visibility='hidden',4000); }
};
