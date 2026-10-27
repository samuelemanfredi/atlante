/* Atlante – cifratura da telefono a telefono per la posizione condivisa. Solo WebCrypto del browser, nessuna libreria.

   Lo stesso schema e gli stessi formati della chat (x/chat-cifra.js), così unendo i rami le due cose possono usare una
   rubrica sola; un'etichetta sua (V) tiene separati i messaggi: una posizione non si fa passare per un messaggio.
   - Ogni dispositivo ha una coppia di chiavi ECDH P-256: la privata è «non esportabile» (il browser la usa ma non la
     consegna a nessuno, nemmeno a questa pagina) e resta in IndexedDB («ax-cifra»); al Mac mini va solo la pubblica
     (POST api/extra/chiavi {id, chiave}: punto non compresso, base64url).
   - Ogni aggiornamento ha una chiave AES-GCM a 256 bit nuova, che cifra la posizione. Quella chiave si cifra per
     ciascun dispositivo dei destinatari con una chiave derivata da ECDH (la privata di chi manda e la pubblica di chi
     riceve) e HKDF-SHA256, con un sale nuovo: solo quel dispositivo la apre, e aprirla prova anche chi l'ha mandata
     (serve la privata del dispositivo che dice di averla mandata). Chiave cifrata: 12 + 32 + 16 = 60 byte.
   - Condivisione, id dell'aggiornamento, persona e dispositivo di chi manda sono «dati aggiuntivi» di AES-GCM: il Mac
     mini non può spostare una posizione in un'altra condivisione né cambiarne il mittente senza che il telefono se ne
     accorga. Il contenuto si allunga con spazi fino a un multiplo di 256 byte.
   Usata dall'app (x/posizione.js), dal Navigatore (x/naviga.html) e dalle prove (musica/server/test_posizione.js). */
(function (G) {
  'use strict';
  const S = G.crypto && G.crypto.subtle;
  const V = 'atlante-posizione/1';
  const CIFRA = 28;   // vettore iniziale (12) + controllo (16)
  const te = new TextEncoder(), td = new TextDecoder();

  function b64u(b) {
    b = b instanceof Uint8Array ? b : new Uint8Array(b);
    let s = '';
    for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
    return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
  function deb64u(s) {
    if (typeof s !== 'string' || !/^[A-Za-z0-9_-]*$/.test(s)) throw new Error('base64 non valido');
    const t = atob(s.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((s.length + 3) % 4));
    const b = new Uint8Array(t.length);
    for (let i = 0; i < t.length; i++) b[i] = t.charCodeAt(i);
    return b;
  }
  const caso = n => G.crypto.getRandomValues(new Uint8Array(n));
  function unisci(...parti) {
    const out = new Uint8Array(parti.reduce((a, p) => a + p.length, 0));
    let i = 0; for (const p of parti) { out.set(p, i); i += p.length; }
    return out;
  }
  const nuovoId = () => b64u(caso(16));

  async function nuoveChiavi() {
    const k = await S.generateKey({name:'ECDH', namedCurve:'P-256'}, false, ['deriveBits']);
    return {privata:k.privateKey, pubblica:b64u(await S.exportKey('raw', k.publicKey))};
  }
  const importate = new Map();   // chiave pubblica (base64url) -> CryptoKey
  async function pubblica(b) {
    let k = importate.get(b);
    if (!k) {
      k = await S.importKey('raw', deb64u(b), {name:'ECDH', namedCurve:'P-256'}, false, []);
      if (importate.size > 500) importate.clear();
      importate.set(b, k);
    }
    return k;
  }
  async function avvolgente(privata, pub, sale, info) {
    const z = await S.deriveBits({name:'ECDH', public:await pubblica(pub)}, privata, 256);
    const hk = await S.importKey('raw', z, 'HKDF', false, ['deriveKey']);
    return S.deriveKey({name:'HKDF', hash:'SHA-256', salt:sale, info:te.encode(info)}, hk, {name:'AES-GCM', length:256}, false, ['encrypt', 'decrypt']);
  }
  const datiMsg = (conv, id, da, disp) => te.encode([V, conv, id, da, disp].join('\n'));
  const infoChiave = (id, daDisp, aDisp) => [V + ' chiave', id, daDisp, aDisp].join('\n');
  async function aes(k, dati, agg) {
    const iv = caso(12);
    return unisci(iv, new Uint8Array(await S.encrypt({name:'AES-GCM', iv, additionalData:agg}, k, dati)));
  }
  async function deaes(k, dati, agg) {
    if (dati.length < CIFRA) throw new Error('cifrato troppo corto');
    return new Uint8Array(await S.decrypt({name:'AES-GCM', iv:dati.subarray(0, 12), additionalData:agg}, k, dati.subarray(12)));
  }
  function imbottisci(contenuto) {
    const b = te.encode(JSON.stringify(contenuto));
    const out = new Uint8Array(Math.ceil((b.length + 1) / 256) * 256).fill(32);   // spazi: JSON.parse li ignora
    out.set(b);
    return out;
  }

  /* io: {privata, disp, persona}; conv: la condivisione; destinatari: [{id, chiave}] (i dispositivi, vedi destinatari()).
     -> {id, conv, disp, corpo, sale, chiavi}: il corpo di POST posizione/<conv> (senza la chiave in chiaro) */
  async function cifra(io, conv, id, contenuto, destinatari) {
    const kraw = caso(32);
    try {
      const k = await S.importKey('raw', kraw, 'AES-GCM', false, ['encrypt']);
      const agg = datiMsg(conv, id, io.persona, io.disp);
      const corpo = await aes(k, imbottisci(contenuto), agg);
      const sale = caso(16), chiavi = {};
      for (const d of destinatari) chiavi[d.id] = b64u(await aes(await avvolgente(io.privata, d.chiave, sale, infoChiave(id, io.disp, d.id)), kraw, agg));
      return {id, conv, disp:io.disp, corpo:b64u(corpo), sale:b64u(sale), chiavi};
    } finally { kraw.fill(0); }
  }
  /* m: {id, conv, da, disp, corpo, sale, chiave}; chiaveMittente: la pubblica del dispositivo m.disp. Errore se non è
     per questo dispositivo, è stata cambiata, spostata o non l'ha mandata lui. */
  async function decifra(io, m, chiaveMittente) {
    const agg = datiMsg(m.conv, m.id, m.da, m.disp);
    const kw = await avvolgente(io.privata, chiaveMittente, deb64u(m.sale), infoChiave(m.id, m.disp, io.disp));
    const kraw = await deaes(kw, deb64u(m.chiave), agg);
    try {
      const k = await S.importKey('raw', kraw, 'AES-GCM', false, ['decrypt']);
      return JSON.parse(td.decode(await deaes(k, deb64u(m.corpo), agg)));
    } finally { kraw.fill(0); }
  }

  // persone: da GET chiavi ([{id, nome, dispositivi:[{id, chiave, attivo}]}])
  function destinatari(persone, membri, mioDisp) {
    const m = new Set(membri), out = [];
    for (const p of persone) if (m.has(p.id)) for (const d of p.dispositivi) if (d.attivo && d.id !== mioDisp) out.push({id:d.id, chiave:d.chiave, persona:p.id});
    return out;
  }
  function chiaveDi(persone, persona, disp) {
    const p = persone.find(x => x.id === persona), d = p && p.dispositivi.find(x => x.id === disp);
    return d ? d.chiave : null;
  }

  // ----- le chiavi di questo dispositivo, in IndexedDB («ax-cifra»): le stesse per l'app e per il Navigatore -----
  function idb(modo, fn) {
    return new Promise((ok, no) => {
      const r = G.indexedDB.open('ax-cifra', 1);
      r.onupgradeneeded = () => r.result.createObjectStore('chiavi');
      r.onerror = () => no(r.error || new Error('archivio delle chiavi non disponibile'));
      r.onsuccess = () => {
        const db = r.result;
        let tx, q;
        try { tx = db.transaction('chiavi', modo); q = fn(tx.objectStore('chiavi')); } catch (e) { db.close(); no(e); return; }
        tx.oncomplete = () => { db.close(); ok(q && q.result); };
        tx.onerror = tx.onabort = () => { db.close(); no(tx.error || new Error('archivio delle chiavi non disponibile')); };
      };
    });
  }
  const archivio = {   // le prove lo sostituiscono (Deno non ha IndexedDB)
    leggi: () => idb('readonly', s => s.get('io')),
    scrivi: x => idb('readwrite', s => s.put(x, 'io'))
  };
  let mieP = null;
  // {disp, privata, pubblica}: create la prima volta, poi sempre le stesse (anche tra app e Navigatore, che sono pagine diverse)
  function mie() {
    if (!mieP) {
      const prendi = async () => {
        let x = await archivio.leggi();
        if (!x || !x.privata || !x.pubblica || !x.disp) {
          const k = await nuoveChiavi();
          x = {disp:nuovoId(), privata:k.privata, pubblica:k.pubblica, creato:Date.now()};
          await archivio.scrivi(x);
        }
        return x;
      };
      const L = G.navigator && G.navigator.locks;   // due pagine aperte insieme non creano due coppie di chiavi
      mieP = (L && L.request ? L.request('ax-cifra', prendi) : prendi()).catch(e => { mieP = null; throw e; });
    }
    return mieP;
  }
  const disponibile = () => !!(S && G.indexedDB && G.isSecureContext !== false);

  // ----- la rubrica del Mac mini (api/extra/chiavi). `api(percorso, opzioni)` la dà chi usa il modulo -----
  let api = null, registrato = null, rubricaP = null, rubricaT = 0;
  function usa(f) { api = f; }
  async function registra(forza) {
    const io = await mie();
    if (!registrato || forza) registrato = api('chiavi', {method:'POST', body:{id:io.disp, chiave:io.pubblica}}).catch(e => { registrato = null; throw e; });
    await registrato;
    return io;
  }
  // [{id, nome, dispositivi}] di tutti; si rilegge al più ogni minuto (o subito con `forza`: un telefono nuovo)
  function rubrica(forza) {
    if (!rubricaP || forza || Date.now() - rubricaT > 60000) {
      rubricaT = Date.now();
      rubricaP = api('chiavi').catch(e => { rubricaP = null; throw e; });
    }
    return rubricaP;
  }
  // una posizione da mandare a `membri` (le persone): il corpo di POST posizione/<conv>
  async function chiudi(conv, persona, contenuto, membri) {
    const io = await mie(), r = await rubrica();
    return cifra({privata:io.privata, disp:io.disp, persona}, conv, nuovoId(), contenuto, destinatari(r.persone, membri, io.disp));
  }
  // una posizione arrivata: m = {id, disp, corpo, sale, chiave} (da GET posizione), conv = la condivisione, da = la persona
  async function apri(m, conv, da) {
    const io = await mie();
    let r = await rubrica(), k = chiaveDi(r.persone, da, m.disp);
    if (!k) { r = await rubrica(true); k = chiaveDi(r.persone, da, m.disp); }
    if (!k) throw new Error('chiave del mittente sconosciuta');
    return decifra({privata:io.privata, disp:io.disp}, {id:m.id, conv, da, disp:m.disp, corpo:m.corpo, sale:m.sale, chiave:m.chiave}, k);
  }

  G.Cifra = {V, b64u, deb64u, nuovoId, nuoveChiavi, cifra, decifra, destinatari, chiaveDi, archivio, mie, disponibile,
    usa, registra, rubrica, chiudi, apri};
})(typeof globalThis !== 'undefined' ? globalThis : self);
