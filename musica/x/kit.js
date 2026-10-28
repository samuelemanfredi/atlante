/* Atlante – kit comune dei moduli extra (pagine in musica/x/, aperte dentro l'app Musica in un riquadro).
   La musica continua a suonare nell'app che le ospita: per riprodurre audio (podcast, audiolibri) si usa AX.player,
   così compaiono schermata di blocco, CarPlay e cuffie. Tutte le funzioni restano utilizzabili anche aprendo la pagina da sola. */
(function () {
  'use strict';
  const host = (() => { try { return window.parent !== window && window.parent.AM ? window.parent.AM : null; } catch (e) { return null; } })();
  const API = new URL('../api/extra/', location.href).href;   // server sul Mac (musica_libreria.py)
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let toastT = 0;
  function toast(m) {
    if (host && host.toast) return host.toast(m);
    let t = $('axToast'); if (!t) { t = document.createElement('div'); t.id = 'axToast'; document.body.appendChild(t); }
    t.textContent = m; t.classList.add('show'); clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove('show'), 2600);
  }
  // da Internet senza password il Mac risponde 401: la pagina della password la apre l'app che ospita il modulo
  function accesso() {
    if (host && host.accesso) host.accesso(); else location.href = new URL('../accesso', location.href).href;
  }
  // fetch con limite d'attesa (un Mac in stop può tenere la connessione aperta senza rispondere)
  async function fetchT(url, opt, ms) {
    const c = new AbortController(), tm = setTimeout(() => c.abort(), ms || 20000);
    try {
      const r = await fetch(url, Object.assign({}, opt, {signal:c.signal}));
      if (r.status === 401 && r.headers.get('X-Atlante-Accesso')) accesso();
      return r;
    } finally { clearTimeout(tm); }
  }
  // server del Mac: AX.api('stato'), AX.api('video', {method:'POST', body:{...}})
  async function api(path, opt) {
    opt = Object.assign({}, opt);
    if (opt.body && typeof opt.body !== 'string') { opt.body = JSON.stringify(opt.body); opt.headers = Object.assign({'Content-Type':'application/json'}, opt.headers); }
    const r = await fetchT(API + path, opt, opt.timeout || 30000);
    let j = null; try { j = await r.json(); } catch (e) {}
    if (!r.ok) throw new Error((j && j.errore) || ('Il Mac ha risposto ' + r.status));
    return j;
  }
  // pagina o feed di un altro sito, letto dal Mac (i siti esterni non permettono di leggerli direttamente dal browser)
  async function proxy(url, ms) {
    const r = await fetchT(API + 'proxy?url=' + encodeURIComponent(url), {}, ms || 25000);
    if (!r.ok) { let j = null; try { j = await r.json(); } catch (e) {} throw new Error((j && j.errore) || 'Sito non raggiungibile'); }
    return r;
  }
  let statoP = null;
  // {ok, ytdlp, say, ollama, modelli, ...}; {ok:false} se il Mac non risponde
  function stato(refresh) {
    if (!statoP || refresh) statoP = api('stato', {timeout:6000}).then(j => Object.assign({ok:true}, j)).catch(() => ({ok:false}));
    return statoP;
  }
  // IA locale sul Mac (Ollama): testo = await AX.ia('domanda', {sistema:'...', json:false})
  async function ia(prompt, o) {
    o = o || {};
    const j = await api('ia', {method:'POST', body:{prompt, sistema:o.sistema || '', json:!!o.json, modello:o.modello || ''}, timeout:o.timeout || 180000});
    return j.testo;
  }
  const store = {
    get(k, d) { try { const v = localStorage.getItem('ax-' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem('ax-' + k, JSON.stringify(v)); } catch (e) { toast('Spazio pieno: dati non salvati'); } }
  };
  // indirizzo assoluto (i relativi da questa pagina) solo se è http, https o blob:; se no ''
  function sicuro(u) {
    if (!u) return '';
    try { u = new URL(String(u).trim(), location.href).href; } catch (e) { return ''; }
    return /^(?:https?:\/\/|blob:)/i.test(u) ? u : '';
  }
  // lettore dell'app Musica. items: [{id, title, artist, album, url, cover}] (url assoluti o relativi a questa pagina).
  // Qui per tutti i moduli: url e copertina solo http, https e blob: (anche i dati salvati da una versione di prima);
  // un brano con un altro indirizzo resta fuori, una copertina con un altro indirizzo resta vuota
  const player = {
    available: !!host,
    play(items, start, from) {
      const tutti = (items || []).map(it => Object.assign({}, it, {url:sicuro(it.url), cover:sicuro(it.cover)}));
      const abs = tutti.filter(it => it.url);
      if (!abs.length) return;
      start = Math.min(tutti.slice(0, start || 0).filter(it => it.url).length, abs.length - 1);   // lo stesso brano di prima, o il seguente
      if (host) return host.playExternal(abs, start, from || document.title);
      const a = player._au || (player._au = Object.assign(document.createElement('audio'), {controls:true}));
      if (!a.isConnected) { a.style.cssText = 'position:fixed;left:8px;right:8px;bottom:8px;width:calc(100% - 16px);z-index:50'; document.body.appendChild(a); }
      a.src = abs[start].url; a.play().catch(() => {});
    },
    toggle() { if (host) host.toggle(); else if (player._au) player._au.paused ? player._au.play() : player._au.pause(); },
    next() { if (host) host.next(); },
    prev() { if (host) host.prev(); },
    now() { return host ? host.now() : null; },            // {id, title, artist, album, cover, playing, ext, t, d}
    seek(t) { if (host) host.seek(t); else if (player._au) player._au.currentTime = t; },
    onChange(cb) { return host ? host.onChange(cb) : () => {}; }
  };
  function close() { if (host && host.close) host.close(); else history.back(); }
  // intestazione standard: AX.header('Podcast') → pulsante «Fine» che chiude il riquadro
  function header(title, extra) {
    const h = document.createElement('div');
    h.style.cssText = 'display:flex;align-items:center;justify-content:space-between;min-height:44px;gap:10px';
    h.innerHTML = '<button id="axBack" style="color:var(--accent);font-size:17px;padding:8px 8px 8px 0">‹ Altro</button><span>' + (extra || '') + '</span>';
    const w = document.querySelector('.wrap') || document.body; w.insertBefore(h, w.firstChild);
    const t = document.createElement('h1'); t.className = 'lt'; t.textContent = title; h.after(t);
    $('axBack').onclick = close;
  }
  window.AX = {$, esc, toast, fetchT, api, proxy, stato, ia, store, player, close, header, API, host};
})();
