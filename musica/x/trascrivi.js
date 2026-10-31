// Atlante Musica – Whisper dentro l'app (Web Worker, modulo): trascrive un pezzo di brano (25 s, 16 kHz mono) con le
// parole a tempo. Lo usa x/canta.js per i brani senza testo. Il programma (transformers.js) e il modello (Whisper base,
// circa 80 MB) si scaricano la prima volta e restano nell'app (Cache Storage): dopo funziona anche senza rete.
// Con WebGPU (iPhone e Mac recenti) è veloce; se no WebAssembly. La lingua la riconosce dalla voce del primo pezzo.
import { pipeline, env, Tensor } from 'https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.8.1';

env.allowLocalModels = false;
const MODELLO = 'onnx-community/whisper-base_timestamped';   // con le teste di allineamento: il tempo di ogni parola
const LINGUE = ['it', 'en', 'es', 'fr', 'de', 'pt', 'nl', 'ca', 'ro', 'pl', 'cs', 'hu', 'sv', 'da', 'no', 'fi', 'el', 'tr',
  'ru', 'uk', 'hr', 'sq', 'ar', 'he', 'hi', 'zh', 'ja', 'ko', 'vi', 'id', 'th', 'sk', 'sl', 'sr', 'bg', 'lt', 'lv', 'et', 'la'];
let asr = null, prep = null;
const scaricati = new Map();   // file del modello → [fatto, totale] (byte)

function prepara() {
  if (prep) return prep;
  const avanzamento = p => {
    if (p.status !== 'progress' || !p.file) return;
    scaricati.set(p.file, [p.loaded || 0, p.total || 0]);
    let fatto = 0, totale = 0; for (const [a, b] of scaricati.values()) { fatto += a; totale += b; }
    self.postMessage({tipo:'scarica', fatto, totale});
  };
  prep = (async () => {
    let gpu = false;
    try { gpu = !!(self.navigator.gpu && await self.navigator.gpu.requestAdapter()); } catch (e) {}
    if (gpu) {
      try {
        asr = await pipeline('automatic-speech-recognition', MODELLO,
          {device:'webgpu', dtype:{encoder_model:'fp32', decoder_model_merged:'q4'}, progress_callback:avanzamento});
        return;
      } catch (e) { /* WebGPU che non va: WebAssembly */ }
    }
    asr = await pipeline('automatic-speech-recognition', MODELLO, {dtype:'q8', progress_callback:avanzamento});
  })();
  prep.catch(() => { prep = null; });
  return prep;
}

// la lingua: il primo passo del decodificatore dopo <|startoftranscript|>, la lingua più probabile tra quelle note
async function lingua(audio) {
  const tok = asr.tokenizer.model.tokens_to_ids, sot = tok.get('<|startoftranscript|>');
  const { input_features } = await asr.processor(audio);
  const out = await asr.model({input_features, decoder_input_ids:new Tensor('int64', BigInt64Array.from([BigInt(sot)]), [1, 1])});
  const lg = out.logits.data, V = out.logits.dims[2], base = (out.logits.dims[1] - 1) * V;
  let best = 'en', v = -Infinity;
  for (const l of LINGUE) { const id = tok.get('<|' + l + '|>'); if (id != null && lg[base + id] > v) { v = lg[base + id]; best = l; } }
  return best;
}

// tempi delle parole rotti (tutte allo stesso istante: succede quando il pezzo finisce a metà di una frase)
const rotti = P => P.length > 2 && P.filter(p => !(p.fine > p.t)).length > P.length / 2;
const sillabe = s => Math.max(1, (String(s).toLowerCase().match(/[aeiouyàèéìòóù]+/g) || []).length);
function stimate(frasi) {   // le parole dentro le frasi, divise per sillabe (come il karaoke quando non ha i tempi)
  const P = [];
  for (const c of frasi || []) {
    const [a, z] = c.timestamp || [];
    const W = String(c.text || '').trim().split(/\s+/).filter(Boolean);
    if (a == null || !W.length) continue;
    const fine = z != null && z > a ? z : a + 0.4 * W.length, tot = W.reduce((s, w) => s + sillabe(w), 0);
    let t = a;
    for (const w of W) { const d = (fine - a) * sillabe(w) / tot; P.push({testo:w, t, fine:t + d}); t += d; }
  }
  return P;
}

self.onmessage = async e => {
  const m = e.data;
  try {
    if (m.tipo !== 'pezzo') return;
    await prepara();
    const lg = m.lingua || await lingua(m.audio);
    const out = await asr(m.audio, {return_timestamps:'word', task:'transcribe', language:lg});
    let parole = (out.chunks || []).map(c => ({testo:c.text, t:c.timestamp && c.timestamp[0], fine:c.timestamp && c.timestamp[1]}));
    if (rotti(parole)) {   // si rifà con i tempi delle frasi e le parole si stimano dentro
      const o2 = await asr(m.audio, {return_timestamps:true, task:'transcribe', language:lg});
      parole = stimate(o2.chunks);
    }
    self.postMessage({tipo:'pezzo', n:m.n, lingua:lg, parole});
  } catch (err) {
    self.postMessage({tipo:'errore', n:m.n, errore:String((err && err.message) || err)});
  }
};
