/* Atlante Musica – «Canta» e testo trascritto sul telefono. Gira dentro index.html, dopo il karaoke (TESTO, testoTempo,
   testoFine, testoPezzi, testoUsa…: vedi «testo sincronizzato» in index.html).

   CANTA: con il tasto 🎤 del testo il microfono ascolta chi canta. Il karaoke accende di bianco la parola della musica;
   il bordo della parola diventa VERDE se la stai cantando a tempo, ROSSO se fuori tempo (troppo presto, troppo tardi o
   nelle pause). LA MAPPA TEMPO/TESTO: ogni parola ha la sua finestra [inizio, fine) sul tempo del brano, presa dal testo
   a tempo; a ogni fotogramma la voce si confronta con la parola di quel secondo del brano (testoTempo, il tempo vero del
   lettore), quindi non si accumula ritardo: alla fine di ogni riga si riparte allineati alla musica. Il ritardo tra la
   musica che senti e la voce che arriva al microfono (cuffie Bluetooth, microfono) si misura da solo sugli attacchi delle
   parole e si aggiorna riga per riga.

   TESTO SUL TELEFONO: un brano senza testo (né nel file né su LRCLIB, e il Mac non l'ha ancora trascritto) lo trascrive
   questo telefono mentre suona, con Whisper dentro l'app (x/trascrivi.js: si scarica una volta, circa 80 MB, poi resta
   nell'app). Prima il pezzo che sta suonando, poi i successivi; le parole arrivano a tempo, riga per riga. Il testo resta
   sul telefono; se poi arriva quello del Mac, vale quello. */
(function () {
'use strict';
if (typeof TESTO === 'undefined') return;

/* ---------- stile ---------- */
const st = document.createElement('style');
st.textContent = '.lyr .w.ca{-webkit-text-stroke:1.4px #30d158;paint-order:stroke fill}' +
  '.lyr .w.cf{-webkit-text-stroke:1.4px #ff453a;paint-order:stroke fill}' +
  '.nplhead .cb.rec{background:#ff3b30;opacity:1;color:#fff}' +
  '#nplPunti{flex:none;font-size:13px;font-weight:600;opacity:.85;min-width:0}' +
  '#nplPunti:empty{display:none}';
document.head.appendChild(st);

/* ---------- la mappa tempo/testo: ogni parola con la sua finestra sul tempo del brano ---------- */
const C_MAX = 2.5;     // una parola tenuta più a lungo conta fino a qui (le note lunghe non rendono rosso chi smette prima)
function cantaMappa(R) {
  const M = [];
  (R || []).forEach((r, i) => {
    if (!r.testo || r.t == null) return;
    const P = testoPezzi(r), fine = testoFine(R, i), parole = !!(r.parole && r.parole.length);
    P.forEach((p, k) => {
      const s = p.t;
      let e = k + 1 < P.length ? P[k + 1].t : fine;
      if (p.fine != null && p.fine > s && p.fine < e) e = p.fine;
      if (!(e > s)) e = s + 0.3;
      M.push({i, k, s, e:parole ? Math.min(e, s + C_MAX) : e});   // senza i tempi delle parole: tutta la riga
    });
  });
  // dove finisce la parola prima e dove comincia quella dopo: le pause intorno
  M.forEach((w, n) => { w.prima = n ? M[n - 1].e : -Infinity; w.dopo = n + 1 < M.length ? M[n + 1].s : Infinity; });
  return M;
}

/* ---------- la voce: c'è, e ha una nota? (energia sopra il rumore e un'altezza chiara) ---------- */
function cantaVoce(x, sr, rumore) {   // x: campioni del microfono; → {rms, chiara, voce}
  const n = x.length >> 2, d = new Float32Array(n);
  let e = 0, m = 0;
  for (let i = 0; i < n; i++) { const v = (x[4 * i] + x[4 * i + 1] + x[4 * i + 2] + x[4 * i + 3]) / 4; d[i] = v; m += v; }
  m /= n;
  for (let i = 0; i < n; i++) { d[i] -= m; e += d[i] * d[i]; }
  const rms = Math.sqrt(e / n), sr4 = sr / 4;
  let chiara = 0;
  if (rms > 1e-4) {   // l'altezza: autocorrelazione normalizzata tra 70 e 1000 Hz (la voce che canta)
    const a = Math.max(2, Math.floor(sr4 / 1000)), z = Math.min(n - 2, Math.ceil(sr4 / 70));
    for (let l = a; l <= z; l++) {
      let s = 0, q = 0;
      for (let i = 0; i < n - l; i++) { s += d[i] * d[i + l]; q += d[i] * d[i] + d[i + l] * d[i + l]; }
      const r = q > 0 ? 2 * s / q : 0;
      if (r > chiara) chiara = r;
    }
  }
  return {rms, chiara, voce:rms > Math.max(0.006, rumore * 2.2) && chiara > 0.5};
}

/* ---------- il giudizio di una parola ---------- */
// S: [{t (tempo del brano quando si è sentita la voce, già senza il ritardo), v (voce sì/no), rms}]; w: la parola della
// mappa. 'ok' a tempo, 'ko' fuori tempo, '' non cantata. Fuori tempo: l'attacco più vicino all'inizio della parola è
// lontano più di 0,35 s, la voce entra nella pausa prima (anticipo) o continua nella pausa dopo (ritardo), o la parola
// è cantata per meno di un terzo del suo tempo.
function cantaGiudica(S, w) {
  let tot = 0, si = 0, prima = 0, primaV = 0, poi = 0, poiV = 0, vicino = null;
  const pausaPrima = w.s - w.prima >= 0.35, pausaDopo = w.dopo - w.e >= 0.35;
  for (let i = 0; i < S.length; i++) {
    const f = S[i];
    if (f.t >= w.s - 0.05 && f.t < w.e) { tot++; if (f.v) si++; }
    else if (pausaPrima && f.t >= Math.max(w.prima + 0.15, w.s - 0.6) && f.t < w.s - 0.25) { prima++; if (f.v) primaV++; }
    else if (pausaDopo && f.t >= w.e + 0.25 && f.t < Math.min(w.dopo - 0.15, w.e + 0.8)) { poi++; if (f.v) poiV++; }
    if (f.v && i > 0 && f.t >= w.s - 0.6 && f.t < w.s + 0.6) {
      // un attacco: la voce comincia, o l'energia sale di colpo (una sillaba nuova)
      let min = Infinity;
      for (let k = i - 1; k >= 0 && S[k].t > f.t - 0.12; k--) min = Math.min(min, S[k].rms);
      if ((!S[i - 1].v || f.rms > 1.6 * min) && (vicino == null || Math.abs(f.t - w.s) < Math.abs(vicino - w.s))) vicino = f.t;
    }
  }
  if (!tot) return '';
  const r = si / tot, g = prima ? primaV / prima : 0, d = poi ? poiV / poi : 0;
  if (r < 0.12 && g < 0.5 && d < 0.5) return '';
  const fuori = vicino != null && Math.abs(vicino - w.s) > 0.35;
  return r >= 0.3 && g < 0.5 && d < 0.5 && !fuori ? 'ok' : 'ko';
}
// l'entrata nella riga: di quanto la voce comincia prima o dopo la prima parola (se prima c'è una pausa: con le righe
// attaccate non si può sapere). {pronto, scarto}: scarto null = non si sa (nessuna pausa) o non hai cantato
function cantaEntrata(S, w0, tv) {
  if (w0.s - w0.prima < 0.35) return {pronto:true, scarto:null};
  let muto = 0, prec = null;
  for (const f of S) {   // il silenzio si conta da prima; l'entrata vale da 1 s prima a 1,5 s dopo la prima parola
    if (f.t > w0.s + 1.5) break;
    const dt = prec ? f.t - prec.t : 0.033;
    if (f.t >= w0.s - 1 && f.v && muto >= 0.15) return {pronto:true, scarto:f.t - w0.s};
    muto = f.v ? 0 : muto + dt;
    prec = f;
  }
  return {pronto:tv >= w0.s + 1.5, scarto:null};
}
const mediana = a => { const b = a.slice().sort((x, y) => x - y); return b.length ? b[b.length >> 1] : 0; };

/* ---------- il modo «Canta» ---------- */
const CANTA = {on:false, stream:null, ctx:null, an:null, buf:null, timer:0, rumore:0.004, lat:0.15, attacchi:[], S:[], esiti:new Map(),
  M:[], R:null, k:'', ultimo:null, ora:0, voce:false, muto:0, giudicate:0, fin:0, ok:0, ko:0, sessione:''};
function cantaTasto() {
  const h = document.querySelector('.nplhead'); if (!h || $('nplCanta')) return;
  const b = document.createElement('button');
  b.className = 'cb'; b.id = 'nplCanta'; b.dataset.a = 'canta'; b.hidden = true;
  b.setAttribute('aria-label', 'Canta: il microfono ascolta e le parole diventano verdi se sei a tempo');
  b.innerHTML = '<svg viewBox="0 0 24 24" width="19" height="19" fill="currentColor" aria-hidden="true"><path d="M12 14.5a3.5 3.5 0 0 0 3.5-3.5V5.5a3.5 3.5 0 0 0-7 0V11a3.5 3.5 0 0 0 3.5 3.5zm6-3.6h-1.6A4.4 4.4 0 0 1 12 15.4a4.4 4.4 0 0 1-4.4-4.5H6a6 6 0 0 0 5.2 6v3.1h1.6v-3.1a6 6 0 0 0 5.2-6z"/></svg>';
  const p = document.createElement('span'); p.id = 'nplPunti';
  const tr = $('nplTrad'); h.insertBefore(b, tr); h.insertBefore(p, b);
}
function cantaVisibile() {
  const b = $('nplCanta'); if (!b) return;
  const si = !!(TESTO.righe && TESTO.j && TESTO.j.sincronizzato);
  if (b.hidden === si) b.hidden = !si;
  if (!si && CANTA.on) cantaSpegni();
}
async function cantaAccendi() {
  if (CANTA.on) return;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { toast('Questo browser non lascia usare il microfono'); return; }
  try {
    try { if (navigator.audioSession) navigator.audioSession.type = 'play-and-record'; } catch (e) {}
    CANTA.stream = await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true, noiseSuppression:false, autoGainControl:false}});
  } catch (e) {
    try { if (navigator.audioSession) navigator.audioSession.type = 'playback'; } catch (x) {}
    toast(e && e.name === 'NotAllowedError' ? 'Serve il permesso del microfono: consentilo quando il telefono lo chiede (o in Impostazioni › Safari › Microfono)' : 'Microfono non disponibile');
    return;
  }
  const AC = window.AudioContext || window.webkitAudioContext;
  CANTA.ctx = new AC();
  const src = CANTA.ctx.createMediaStreamSource(CANTA.stream);
  CANTA.an = CANTA.ctx.createAnalyser(); CANTA.an.fftSize = 2048;
  src.connect(CANTA.an);   // non va alle casse: solo ascolto
  CANTA.buf = new Float32Array(CANTA.an.fftSize);
  // il ritardo di partenza: quello che il telefono dice (uscita + ingresso) e un attimo per reagire; poi si misura cantando
  CANTA.lat0 = CANTA.lat = Math.max(0.05, Math.min(0.5, (CANTA.ctx.baseLatency || 0) + (CANTA.ctx.outputLatency || 0) + 0.1));
  CANTA.on = true; CANTA.attacchi = []; CANTA.S = []; CANTA.ultimo = null; CANTA.rumore = 0.004; CANTA.entrate = new Map();
  const b = $('nplCanta'); b.classList.add('rec'); b.setAttribute('aria-label', 'Canta acceso: tocca per spegnere il microfono');
  CANTA.timer = setInterval(cantaGiro, 33);
  toast('Canta! Bordo verde: a tempo · rosso: fuori tempo. Con le cuffie il microfono sente solo te', true);
}
function cantaSpegni() {
  if (!CANTA.on) return;
  CANTA.on = false; clearInterval(CANTA.timer); CANTA.timer = 0;
  try { CANTA.stream.getTracks().forEach(t => t.stop()); } catch (e) {}
  try { CANTA.ctx.close(); } catch (e) {}
  CANTA.stream = CANTA.ctx = CANTA.an = null;
  try { if (navigator.audioSession) navigator.audioSession.type = 'playback'; } catch (e) {}
  const b = $('nplCanta'); if (b) { b.classList.remove('rec'); b.setAttribute('aria-label', 'Canta: il microfono ascolta e le parole diventano verdi se sei a tempo'); }
  cantaPulisci(); $('nplPunti').textContent = '';
}
function cantaPulisci() {   // via i colori (brano cambiato, salto indietro, Canta spento)
  CANTA.esiti.clear(); CANTA.ok = CANTA.ko = 0;
  for (const w of $('lyr').querySelectorAll('.w.ca,.w.cf')) w.classList.remove('ca', 'cf');
}
function cantaSegna(w, esito) {   // il bordo della parola sullo schermo (anche dopo un ridisegno del testo)
  const L = $('lyr'), r = L.children[w.i], el = r && r.querySelectorAll('.w')[w.k];
  if (!el) return;
  el.classList.toggle('ca', esito === 'ok'); el.classList.toggle('cf', esito === 'ko');
}
function cantaGiro() {
  if (!CANTA.on || !CANTA.an) return;
  // il testo è cambiato (brano nuovo, ridisegno, traduzione): la mappa si rifà
  if (TESTO.righe !== CANTA.R) {
    const nuovo = TESTO.k !== CANTA.k;
    CANTA.R = TESTO.righe; CANTA.k = TESTO.k; CANTA.M = cantaMappa(CANTA.R);
    CANTA.entrate = new Map();
    if (nuovo) { cantaPulisci(); CANTA.S = []; CANTA.attacchi = []; }
    else for (const [n, e] of CANTA.esiti) if (CANTA.M[n]) cantaSegna(CANTA.M[n], e);
  }
  CANTA.an.getFloatTimeDomainData(CANTA.buf);
  const v = cantaVoce(CANTA.buf, CANTA.ctx.sampleRate, CANTA.rumore);
  if (!v.voce) CANTA.rumore += (v.rms - CANTA.rumore) * (v.rms < CANTA.rumore ? 0.2 : 0.01);   // il rumore di fondo (anche la musica che rientra)
  const suona = RC.id ? !!(RC.dev && RC.dev.stato && RC.dev.stato.suona) : !au.paused;
  const ora = performance.now() / 1000, t = testoTempo();
  if (!suona) { CANTA.ultimo = null; return; }
  // un salto (tocco su una riga, avanti o indietro): la voce di prima non conta più
  if (CANTA.ultimo && Math.abs((t - CANTA.ultimo.t) - (ora - CANTA.ultimo.ora)) > 0.75) {
    CANTA.S = []; CANTA.entrate = new Map();
    for (const [n] of CANTA.esiti) if (CANTA.M[n] && CANTA.M[n].s > t - 1) { cantaSegna(CANTA.M[n], ''); CANTA.esiti.delete(n); }
  }
  CANTA.ultimo = {t, ora};
  const tv = t - CANTA.lat;   // il punto del brano che stai cantando adesso
  // l'attacco di una parola (voce dopo almeno 0,15 s di silenzio): misura il ritardo tra musica e voce
  if (v.voce && !CANTA.voce && CANTA.muto >= 0.15) {
    let best = null;
    for (const w of CANTA.M) { const d = t - w.s; if (d > -0.2 && d < 0.9 && (!best || Math.abs(d - CANTA.lat) < Math.abs(best - CANTA.lat))) best = d; if (w.s > t + 1) break; }
    if (best != null) { CANTA.attacchi.push(best); if (CANTA.attacchi.length > 15) CANTA.attacchi.shift(); }
  }
  CANTA.muto = v.voce ? 0 : CANTA.muto + 0.033; CANTA.voce = v.voce;
  CANTA.S.push({t, v:v.voce, rms:v.rms});
  while (CANTA.S.length && CANTA.S[0].t < t - 20) CANTA.S.shift();
  const S = CANTA.S.map(f => ({t:f.t - CANTA.lat, v:f.v, rms:f.rms}));
  // le parole finite: il giudizio resta. Quella di adesso: verde mentre la canti (se non hai anticipato).
  // Riga per riga ci si riallinea alla musica: l'entrata nella riga (la voce rispetto alla prima parola, dopo una pausa)
  // vale per tutte le sue parole; una riga cominciata più di 0,35 s prima o dopo è fuori tempo anche se poi la voce
  // copre le parole (chi canta in ritardo una frase intera)
  let rigaFinita = false;
  CANTA.M.forEach((w, n) => {
    if (CANTA.esiti.has(n) || w.s > tv + 0.05) return;
    const prima = n && CANTA.M[n - 1].i === w.i ? null : w;   // la prima parola della riga
    let en = CANTA.entrate.get(w.i);
    if (!en && prima) { const x = cantaEntrata(S, w, tv); if (x.pronto) CANTA.entrate.set(w.i, en = x); }
    const fine = w.dopo - w.e >= 0.35 ? Math.min(w.dopo - 0.15, w.e + 0.8) : w.e;   // con la pausa dopo, si guarda anche lì
    if (tv >= fine + 0.15 && en) {
      let e = cantaGiudica(S, w);
      const sc = en.scarto;
      if (sc != null && Math.abs(sc) > 0.35 && e === 'ok') e = 'ko';
      if (e === '' && prima && sc != null && Math.abs(sc) > 0.35) e = 'ko';   // entrata fuori tempo: la prima parola non l'hai cantata a tempo
      CANTA.esiti.set(n, e); cantaSegna(w, e);
      if (e === 'ok') CANTA.ok++; else if (e === 'ko') CANTA.ko++;
      if (!CANTA.M[n + 1] || CANTA.M[n + 1].i !== w.i) rigaFinita = true;
    } else if (tv >= w.s) {   // la parola di adesso, giudicata fin qui
      const x = cantaGiudica(S, {s:w.s, e:Math.max(w.s + 0.1, tv), prima:w.prima, dopo:Infinity});
      cantaSegna(w, x === 'ok' ? 'ok' : x === 'ko' && tv - w.s > 0.4 ? 'ko' : '');
    }
  });
  // a fine riga: il ritardo si riallinea sulla voce di questa riga (mediana degli attacchi, tra 0,02 e 0,5 s)
  // (vicino a quello del telefono: un cantante sempre in ritardo non deve diventare «a tempo»)
  if (rigaFinita && CANTA.attacchi.length >= 4) CANTA.lat = Math.max(CANTA.lat0 - 0.05, Math.min(CANTA.lat0 + 0.25, mediana(CANTA.attacchi)));
  const n = CANTA.ok + CANTA.ko;
  $('nplPunti').textContent = n ? Math.round(100 * CANTA.ok / n) + '% a tempo' : '';
}

/* ---------- testo trascritto su questo telefono (Whisper nell'app) ---------- */
const TEL = {w:null, k:'', id:null, testi:new Map(), lavoro:null, scarico:'', errore:''};
const TEL_FINESTRA = 25, TEL_PASSO = 20;   // pezzi da 25 s ogni 20: 5 s in comune, nessuna riga tagliata a metà
const TEL_RITARDO = 0.12;   // Whisper nell'app dà le parole in media 0,12 s prima della voce (misurato il 31/10 su parole a tempo noto)
const TEL_INVENTATE = /^[\s(\[♪*-]*(musica|music|applausi|applause|risate|sottotitoli|subtitles|amara\.org|grazie per la visione|thanks? for watching)/i;
const sillabe = s => Math.max(1, (String(s).toLowerCase().match(/[aeiouyàèéìòóù]+/g) || []).length);
// le parole dei pezzi in un testo a righe: una riga per frase (pausa oltre 0,8 s, punto, o troppe parole)
function telRighe(P) {
  const righe = []; let r = null;
  P.forEach((p, n) => {
    const prima = P[n - 1];
    if (!r || p.t - prima.fine > 0.8 || /[.!?]$/.test(prima.testo) || r.parole.length >= 11 || (r.parole.length >= 8 && /,$/.test(prima.testo))) {
      if (r) righe.push(r);
      r = {t:p.t, parole:[]};
    }
    r.parole.push({t:p.t, testo:p.testo + ' ', fine:p.fine});
  });
  if (r) righe.push(r);
  return righe.map(r => {
    const u = r.parole[r.parole.length - 1];
    u.testo = u.testo.trimEnd();
    return {t:r.t, testo:r.parole.map(p => p.testo).join(''), fine:u.fine, parole:r.parole};
  }).filter(r => r.testo && !TEL_INVENTATE.test(r.testo));
}
// le parole di un pezzo, sistemate: niente segni da soli, e una parola che «dura» tutta la pausa prima comincia poco prima
// di finire (Whisper dà la fine giusta, l'inizio lo attacca alla parola di prima)
function telPulisci(P) {
  const out = [];
  for (const p of P) {
    const testo = String(p.testo || '').trim();
    if (!testo || /^[\s\p{P}♪]+$/u.test(testo) || p.t == null || p.fine == null) continue;
    let t = +p.t + TEL_RITARDO, fine = Math.max(+p.fine + TEL_RITARDO, t);
    const max = 0.35 + 0.25 * sillabe(testo);
    if (fine - t > max) t = fine - (0.15 + 0.2 * sillabe(testo));
    out.push({t, fine, testo});
  }
  return out;
}
function telJ(lav) {   // il testo da mostrare con i pezzi fatti fin qui
  // i 5 s in comune tra due pezzi: metà all'uno e metà all'altro (se l'altro non c'è ancora, tutti a questo)
  const P = [], meta = (TEL_FINESTRA - TEL_PASSO) / 2;
  [...lav.pezzi.keys()].sort((a, b) => a - b).forEach(da => {
    const a = lav.pezzi.has(da - TEL_PASSO) ? da + meta : da, z = lav.pezzi.has(da + TEL_PASSO) ? da + TEL_PASSO + meta : Infinity;
    for (const p of lav.pezzi.get(da)) if (p.t >= a && p.t < z) P.push(p);
  });
  P.sort((a, b) => a.t - b.t);
  const fatti = lav.pezzi.size, tutti = Math.max(1, Math.ceil(Math.max(0, lav.durata - (TEL_FINESTRA - TEL_PASSO)) / TEL_PASSO));
  return {stato:'ok', sincronizzato:true, parole:true, parole_da:'voce', fonte:'telefono', lingua:lav.lingua || '', righe:telRighe(P),
    v:'tel-' + fatti, tel:{fatti, tutti, completo:fatti >= tutti}};
}
function telNota(j) {
  if (!j || !j.tel || j.tel.completo) return '';
  return TEL.scarico || ('Trascrivo sul telefono: ' + Math.round(100 * j.tel.fatti / j.tel.tutti) + '%');
}
window.telNota = telNota;
const senzaTesto = j => !!j && (j.stato === 'nessuno' || (j.stato === 'ok' && !(j.righe || []).some(r => r.testo)));
// testoUsa chiede: il testo del Mac, o quello del telefono se il Mac non ne ha (e intanto si comincia a trascrivere)
window.telScegli = function (j) {
  const b = TESTO.b; if (!b || RC.id) return j;
  const id = curId, t = id && T.get(id);
  if (!t || (j && j.fonte === 'telefono')) return j;   // il testo del telefono stesso (testoUsa lo ridisegna)
  if (j && j.stato === 'ok' && !senzaTesto(j)) { telFerma(b.k); return j; }   // c'è il testo (dal file, da LRCLIB o da Whisper sul Mac)
  if (!senzaTesto(j) && !(j && j.stato === 'errore')) return j;   // in arrivo, strumentale, vuoto: si aspetta il Mac
  const mio = TEL.testi.get(b.k);
  if (!TEL.lavoro || TEL.lavoro.k !== b.k) telInizia(b, id);
  if (mio && mio.righe.length) return mio;
  return {stato:'trascrivo', nota:TEL.errore || TEL.scarico || 'Il testo non c\'è nel file né su LRCLIB: lo ascolto qui, mentre suona'};
};
function telFerma(k) { if (TEL.lavoro && TEL.lavoro.k === k) TEL.lavoro.fermo = true; }
function telMostra(k) {   // il testo del telefono aggiornato: si vede se è ancora il brano di adesso
  if (TESTO.k !== k) return;
  const j = TEL.testi.get(k);
  if (j && j.righe.length) testoUsa(j);
  else if (TESTO.j && TESTO.j.stato === 'trascrivo') testoDisegna({stato:'trascrivo', nota:TEL.errore || TEL.scarico || 'Il testo non c\'è nel file né su LRCLIB: lo ascolto qui, mentre suona'});
}
function telWorker() {
  if (TEL.w) return TEL.w;
  TEL.w = new Worker('x/trascrivi.js', {type:'module'});
  TEL.attese = new Map(); TEL.n = 0;
  TEL.w.onmessage = e => {
    const m = e.data;
    if (m.tipo === 'scarica') {
      const pc = m.totale ? Math.round(100 * m.fatto / m.totale) : 0;
      TEL.scarico = pc < 100 ? 'Scarico il programma per il testo (una volta sola): ' + pc + '%' : '';
      if (TESTO.j && TESTO.j.stato === 'trascrivo' && TEL.lavoro) telMostra(TEL.lavoro.k);
      const f = $('lyr').querySelector('.lyrfonte, .lyrmsg small'); if (f && TEL.scarico) f.textContent = TEL.scarico;
      return;
    }
    const a = TEL.attese.get(m.n); if (!a) return;
    TEL.attese.delete(m.n);
    if (m.tipo === 'errore') a.ko(new Error(m.errore)); else a.ok(m);
  };
  TEL.w.onerror = e => { for (const a of TEL.attese.values()) a.ko(new Error(e.message || 'Whisper si è fermato')); TEL.attese.clear(); TEL.w = null; };
  return TEL.w;
}
const telChiedi = (msg, trasferisci) => new Promise((ok, ko) => { const w = telWorker(), n = ++TEL.n; TEL.attese.set(n, {ok, ko}); w.postMessage(Object.assign({n}, msg), trasferisci || []); });
async function telAudio(id) {   // il brano a 16 kHz, mono: dal telefono se c'è (anche un doppione), se no dal Mac
  let f = await getFile(id);
  if (!f) {
    const t = T.get(id);
    if (!t || !t.remote || !SRV) throw new Error('il brano non è sul telefono e il Mac non risponde');
    const r = await fetch(API + 'file/' + encodeURIComponent(t.sid), {cache:'no-store'});
    if (!r.ok) throw new Error('il Mac non dà il brano');
    f = await r.blob();
  }
  const OAC = window.OfflineAudioContext || window.webkitOfflineAudioContext;
  const ab = await new OAC(1, 16000, 16000).decodeAudioData(await f.arrayBuffer());
  if (ab.duration > 900) throw new Error('brano troppo lungo (oltre 15 minuti)');
  if (ab.numberOfChannels === 1) return ab.getChannelData(0);
  const x = new Float32Array(ab.length);
  for (let c = 0; c < ab.numberOfChannels; c++) { const d = ab.getChannelData(c); for (let i = 0; i < x.length; i++) x[i] += d[i] / ab.numberOfChannels; }
  return x;
}
async function telInizia(b, id) {
  const k = b.k;
  if (TEL.lavoro && TEL.lavoro.k !== k) TEL.lavoro.fermo = true;   // un brano alla volta: quello di adesso
  const lav = TEL.lavoro = {k, id, pezzi:new Map(), lingua:'', durata:0, fermo:false};
  TEL.errore = '';
  try {
    const c = await testoDalTelefono('tel|' + k);   // ripreso da dove era arrivato
    if (c && c.tel && c.tel.pezzi) { for (const [da, P] of c.tel.pezzi) lav.pezzi.set(da, P); lav.lingua = c.lingua || ''; lav.durata = c.tel.durata || 0; }
    if (lav.pezzi.size) {
      const j = telJ(lav); j.tel.pezzi = [...lav.pezzi]; j.tel.durata = lav.durata;
      TEL.testi.set(k, j); telMostra(k);
      if (c.tel.completo) return;   // già tutto trascritto
    }
    const x = await telAudio(id);
    if (lav.fermo) return;
    lav.durata = x.length / 16000;
    // l'ordine: prima il pezzo che suona adesso, poi i successivi, poi quelli già passati
    const tutti = []; for (let da = 0; da === 0 || da + (TEL_FINESTRA - TEL_PASSO) < lav.durata; da += TEL_PASSO) tutti.push(da);
    const ora = TESTO.k === k ? testoTempo() : 0, i0 = Math.max(0, tutti.findIndex(da => da + TEL_PASSO > ora));
    const ordine = tutti.slice(i0).concat(tutti.slice(0, i0).reverse());
    for (const da of ordine) {
      if (lav.fermo) return;
      if (lav.pezzi.has(da)) continue;
      const pezzo = x.slice(Math.floor(da * 16000), Math.min(x.length, Math.floor((da + TEL_FINESTRA) * 16000)));
      const r = await telChiedi({tipo:'pezzo', audio:pezzo, lingua:lav.lingua}, [pezzo.buffer]);
      if (!lav.lingua && r.parole.length >= 3) lav.lingua = r.lingua;   // la lingua dal primo pezzo con la voce
      lav.pezzi.set(da, telPulisci(r.parole).map(p => ({t:p.t + da, fine:p.fine + da, testo:p.testo})));
      const j = telJ(lav);
      j.tel.pezzi = [...lav.pezzi]; j.tel.durata = lav.durata;
      TEL.testi.set(k, j); testoSalva('tel|' + k, j);
      telMostra(k);
    }
    if (!(TEL.testi.get(k) || {righe:[]}).righe.length && TESTO.k === k) testoDisegna({stato:'strumentale', fonte:'voce'});
  } catch (e) {
    TEL.errore = 'Trascrizione sul telefono non riuscita: ' + ((e && e.message) || e);
    if (TESTO.k === k && TESTO.j && TESTO.j.stato === 'trascrivo') testoDisegna({stato:'errore', errore:TEL.errore});
  } finally { if (TEL.lavoro === lav) TEL.lavoro = null; }
}

/* ---------- avvio ---------- */
cantaTasto();
document.addEventListener('click', e => {
  const x = e.target.closest('[data-a="canta"]'); if (!x) return;
  if (CANTA.on) cantaSpegni(); else cantaAccendi();
});
new MutationObserver(cantaVisibile).observe($('lyr'), {childList:true});
setInterval(cantaVisibile, 1000);
document.addEventListener('visibilitychange', () => { if (document.hidden) cantaSpegni(); });
new MutationObserver(() => { if (!np.classList.contains('open') || !TESTO.on) cantaSpegni(); }).observe(np, {attributes:true, attributeFilter:['class']});
// per le prove
window.CANTA_PROVA = {cantaMappa, cantaVoce, cantaGiudica, telRighe, telPulisci, telJ, CANTA, TEL};
})();
