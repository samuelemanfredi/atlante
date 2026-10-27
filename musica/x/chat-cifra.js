/* Atlante – cifratura della chat, da telefono a telefono (fase 5a). Solo WebCrypto del browser, nessuna libreria.

   - Ogni dispositivo ha una coppia di chiavi ECDH P-256: la privata è «non esportabile» (il browser la usa ma non la
     consegna a nessuno, nemmeno a questa pagina) e resta in IndexedDB; al Mac mini va solo la pubblica.
   - Ogni messaggio ha una chiave AES-GCM a 256 bit nuova, che cifra il contenuto. Quella chiave si cifra per ciascun
     dispositivo dei destinatari con una chiave derivata da ECDH (la privata di chi manda e la pubblica di chi riceve)
     e HKDF-SHA256, con un sale nuovo per messaggio: solo quel dispositivo la apre, e aprirla prova anche chi l'ha
     mandata (serve la privata del dispositivo che dice di averla mandata).
   - Conversazione, id del messaggio, persona e dispositivo di chi manda sono «dati aggiuntivi» di AES-GCM: il Mac mini
     non può spostare un messaggio in un'altra conversazione né cambiarne il mittente senza che il telefono se ne accorga.
   - Il contenuto si allunga con spazi fino a un multiplo di 256 byte: la grandezza non dice quale brano o quanto testo.
   - Allegati: una chiave AES-GCM loro (dentro il messaggio cifrato), pezzi da 8 MB cifrati uno per uno, ognuno legato
     al suo numero e al totale (non si possono scambiare né togliere pezzi).
   - Codice di sicurezza di una chat a due: 12 cifre da PBKDF2-SHA256 (100 000 giri) delle chiavi pubbliche di tutti i
     dispositivi delle due persone. Uguale sui due telefoni = il Mac mini non ha messo chiavi sue in mezzo.
   Usata da x/chat.html e dalle prove (musica/server/test_chat_cifra.js, con Deno). */
(function (G) {
  'use strict';
  const S = G.crypto && G.crypto.subtle;
  const V = 'atlante-chat/1';
  const PEZZO = 8 * 1024 * 1024;   // byte in chiaro di un pezzo di allegato (lo stesso CHAT_PEZZO del Mac mini)
  const CIFRA = 28;                // vettore iniziale (12) + controllo (16)
  const ITERAZIONI = 100000;
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

  /* io: {privata, disp, persona}; destinatari: [{id, chiave}] (i dispositivi, vedi destinatari()).
     -> il corpo della richiesta POST chat/messaggi (senza la chiave in chiaro) */
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
  /* m: un messaggio da GET chat/messaggi ({id, conv, da, disp, corpo, sale, chiave}); chiaveMittente: la pubblica del
     dispositivo m.disp. Errore se il messaggio non è per questo dispositivo, è stato cambiato o non l'ha mandato lui. */
  async function decifra(io, m, chiaveMittente) {
    const agg = datiMsg(m.conv, m.id, m.da, m.disp);
    const kw = await avvolgente(io.privata, chiaveMittente, deb64u(m.sale), infoChiave(m.id, m.disp, io.disp));
    const kraw = await deaes(kw, deb64u(m.chiave), agg);
    try {
      const k = await S.importKey('raw', kraw, 'AES-GCM', false, ['decrypt']);
      return JSON.parse(td.decode(await deaes(k, deb64u(m.corpo), agg)));
    } finally { kraw.fill(0); }
  }

  // ----- allegati -----
  const pezzi = dim => Math.max(1, Math.ceil(dim / PEZZO));
  const dimensioneCifrata = dim => dim + pezzi(dim) * CIFRA;
  const nuovaChiaveAllegato = () => b64u(caso(32));
  const chiaveAllegato = b => S.importKey('raw', deb64u(b), 'AES-GCM', false, ['encrypt', 'decrypt']);
  const datiPezzo = (aid, n, tot) => te.encode([V + ' allegato', aid, n, tot].join('\n'));
  const cifraPezzo = async (k, aid, n, tot, dati) => aes(k, dati, datiPezzo(aid, n, tot));
  const decifraPezzo = async (k, aid, n, tot, dati) => deaes(k, dati instanceof Uint8Array ? dati : new Uint8Array(dati), datiPezzo(aid, n, tot));

  // ----- chiavi delle persone -----
  // persone: da GET chat/persone ([{id, nome, dispositivi:[{id, chiave, attivo}]}])
  function destinatari(persone, membri, mioDisp) {
    const m = new Set(membri), out = [];
    for (const p of persone) if (m.has(p.id)) for (const d of p.dispositivi) if (d.attivo && d.id !== mioDisp) out.push({id:d.id, chiave:d.chiave, persona:p.id});
    return out;
  }
  function chiaveDi(persone, persona, disp) {
    const p = persone.find(x => x.id === persona), d = p && p.dispositivi.find(x => x.id === disp);
    return d ? d.chiave : null;
  }
  function chiaviAttive(persone, persona) {
    const p = persone.find(x => x.id === persona);
    return p ? p.dispositivi.filter(d => d.attivo).map(d => d.chiave).sort() : [];
  }
  function confronta(vecchie, nuove) {
    const v = new Set(vecchie || []), n = new Set(nuove || []);
    return {aggiunte:[...n].filter(x => !v.has(x)), tolte:[...v].filter(x => !n.has(x))};
  }
  // a, b: {id, chiavi:[pubbliche]} -> «123456789012»; non dipende dall'ordine delle persone né delle chiavi
  async function codice(a, b) {
    const righe = [a, b].map(p => p.id + ':' + [...p.chiavi].sort().join(',')).sort();
    const pw = await S.importKey('raw', te.encode(V + ' codice\n' + righe.join('\n')), 'PBKDF2', false, ['deriveBits']);
    const bits = new Uint8Array(await S.deriveBits({name:'PBKDF2', hash:'SHA-256', salt:te.encode(V + ' codice'), iterations:ITERAZIONI}, pw, 64));
    let n = 0n;
    for (const x of bits) n = (n << 8n) | BigInt(x);
    return (n % 1000000000000n).toString().padStart(12, '0');
  }

  G.ChatCifra = {V, PEZZO, CIFRA, b64u, deb64u, nuovoId, nuoveChiavi, cifra, decifra, pezzi, dimensioneCifrata,
    nuovaChiaveAllegato, chiaveAllegato, cifraPezzo, decifraPezzo, destinatari, chiaveDi, chiaviAttive, confronta, codice};
})(typeof globalThis !== 'undefined' ? globalThis : self);
