# Moduli extra di Atlante Musica — contratto comune

L'app Musica (`musica/index.html`) ha una scheda **Altro** che apre ogni modulo (`musica/x/<nome>.html`)
in un riquadro a tutto schermo **dentro l'app**: la musica continua a suonare, il mini player resta.
Il server sul Mac (`musica/server/musica_libreria.py`) serve sia l'app sia queste pagine e le API `api/extra/`.

## Regole per ogni modulo
- Un file HTML autonomo in `musica/x/`, in italiano, che include `kit.css` e `kit.js` (`window.AX`).
- Nessun framework; librerie esterne solo da `cdn.jsdelivr.net/npm/<pacchetto>@<versione esatta>` o `unpkg.com`, caricate solo quando servono.
- Funziona su iPhone (Safari, PWA installata) e su Mac (Safari/Chrome); larghezza minima 320 px; tema chiaro/scuro da `kit.css`.
- Dati dell'utente: `AX.store` (localStorage, prefisso `ax-`) o IndexedDB proprio del modulo (nome `ax-<modulo>`). Mai numeri completi di carte di pagamento, CVV o password.
- Se il Mac non risponde (`(await AX.stato()).ok === false`) il modulo resta usabile per tutto ciò che non dipende dal Mac e lo dice con una nota chiara.
- Audio da riprodurre (podcast, audiolibri, voce) → `AX.player.play([{id,title,artist,album,url,cover}], start, 'Nome lista')`: suona nel lettore dell'app (schermata di blocco, CarPlay, cuffie).
- Intestazione con `AX.header('Titolo')` (pulsante «‹ Altro» che chiude il riquadro).
- Niente `alert()`/`confirm()`: usare `AX.toast` e controlli nella pagina.

## API del Mac (`AX.api(path, opt)` → JSON; errori: `{errore:"testo"}` con codice ≥ 400)
Aperta da Internet (`tailscale funnel`) senza password, ogni API risponde 401 con `X-Atlante-Accesso: richiesto`: `AX.fetchT` (e quindi `AX.api`, `AX.proxy`) fa aprire all'app la pagina della password.

| Metodo | Percorso | Risposta |
|---|---|---|
| GET | `stato` | `{ytdlp:bool, ffmpeg:bool, say:bool, ollama:bool, modelli:[nomi], video_dir, libri_dir, sistema}` |
| GET | `proxy?url=<url codificato>` | il contenuto della pagina/feed (stesso Content-Type), max 15 MB, solo http/https pubblici (`AX.proxy(url)` restituisce la Response) |
| POST | `video` `{url, tipo:'video'|'audio', qualita:'1080'|'720'|'best'}` | `{id}` — scaricamento con yt-dlp in sottofondo |
| GET | `video` | `{lavori:[{id,url,titolo,tipo,stato:'in coda'|'in corso'|'fatto'|'errore',progresso:0-100,file,nome,dimensione,errore}]}` |
| GET | `video/file/<id>` | il file scaricato (con Range) |
| POST | `video/<id>/annulla` | `{ok:true}` |
| GET | `musica/cerca?q=<testo>&fonte=ytm\|yt` | `{fonte, risultati:[{id,titolo,artista,album,durata,copertina,explicit,censurata}]}` — YouTube Music (predefinita) o YouTube; le censurate in fondo |
| GET | `musica/ascolta/<id YouTube>` | l'audio del brano per ascoltarlo prima (m4a, con Range; la prima volta ~15 s) |
| POST | `musica` `{id, titolo, artista, album, copertina, explicit}` oppure `{url}` | `{id}` — scarica il brano in MP3 (tag, copertina, purl) nella cartella della musica scaricata, che fa parte della libreria; lo stesso brano due volte = lo stesso lavoro |
| GET | `musica` | `{lavori:[{id,yt,url,titolo,artista,album,stato,progresso,nome,dimensione,brano,errore}]}` — `brano` = id nel catalogo quando è in libreria |
| POST | `musica/<id>/annulla` | `{ok:true}` — ferma lo scaricamento; uno finito esce dall'elenco (il brano resta) |
| GET | `dispositivi` | `{dispositivi:[{id,nome,tipo,fa,stato}]}` — le app aperte sentite negli ultimi 60 s (telecomando dell'app: `fa` = secondi dall'ultimo stato) |
| POST | `dispositivi/<id>/stato` `{nome,tipo,stato}` | `{comandi:[…]}` — lo manda ogni app ogni pochi secondi |
| GET | `dispositivi/<id>/comandi?attesa=25` | `{comandi:[…]}` appena arriva un comando (attesa lunga) |
| POST | `dispositivi/<id>/comando` `{azione, …}` | `{ok:true}` — toggle, play, pause, next, prev, seek {t}, volume {v}, suona {coda,t,da,shuffle,repeat} |
| GET | `voci` | `{voci:[{nome, lingua, esempio}]}` — voci di sintesi del Mac (`say -v ?`), prima quelle italiane |
| POST | `audiolibro` `{titolo, autore, voce, velocita, capitoli:[{titolo, testo}]}` | `{id}` — ogni capitolo diventa un file m4a con `say` |
| GET | `audiolibro` | `{lavori:[{id,titolo,autore,stato,progresso,capitoli:[{n,titolo,pronto:bool,durata}]}]}` |
| GET | `audiolibro/file/<id>/<n>` | m4a del capitolo n (da 0), con Range |
| DELETE | `audiolibro/<id>` | `{ok:true}` |
| GET | `ia` | `{attiva:bool, modelli:[...], modello}` |
| POST | `ia` `{prompt, sistema, json:bool, modello}` | `{testo}` — IA locale (Ollama su `localhost:11434`), tempo massimo 180 s |
| GET | `dispositivo` | `{nome, sistema, versione, avvio, memoria:{totale,libera}, dischi:[{nome,percorso,totale,libero}], pulizia:[{nome,percorso,dimensione}]}` |
| GET | `ascoltati` | `{io:{nome,condivido,ascolti,cambia}, settimana:[…], mese:[…], per_te:[{…,da}], condivisi:[{nome,brani}], soglia}` — classifiche «I più ascoltati in Atlante» (uguali per tutti; un brano solo se l'hanno ascoltato almeno `soglia` persone), «Per te» e «Cosa ascolta …» di chi condivide; ogni brano `{brano,titolo,artista,album,copertina}` |
| POST | `ascoltati` `{ascolti:[{brano, giorno:'AAAA-MM-GG'}]}` | `{contati, scartati}` — brani del catalogo che hanno suonato davvero (anche quelli di giorni passati, fatti senza rete) |
| POST | `ascoltati/condividi` `{si:bool}` | `{condivido}` — «Condividi i miei ascolti» (spento all'inizio) |
| DELETE | `ascoltati` | `{ok, cancellati}` — cancella dal Mac mini i propri ascolti |
| GET | `ascoltati/anche/<brano>` | `{brani:[…]}` — «Chi ascolta questo brano ascolta anche…» |

## API dell'app che ospita (`AX.player`, oppure `parent.AM`)
`playExternal(items, start, from)`, `toggle()`, `next()`, `prev()`, `seek(sec)`,
`now()` → `{id,title,artist,album,cover,playing,ext,t,d}` oppure `null`, `onChange(cb)` → funzione per smettere, `toast(msg)`, `close()`,
`libreria()` → Promise (rilegge il catalogo del Mac), `suona(ids, start, from)` e `dopo(id)` per i brani della libreria (id del catalogo, es. `brano` di `musica`).

## Moduli
| File | Contenuto |
|---|---|
| `cerca.html` | cerca su YouTube Music e YouTube (anche dalla barra del tasto **+** dell'app), ascolta prima, scarica nella libreria, scarica da un link; «Cerca anche su» altri siti (mp3juice.sc segnato come sito a rischio, voluto da Samuele) |
| `podcast.html` | cerca (iTunes Search API), iscrizioni, episodi dal feed (via proxy), ascolto con ripresa del punto, scarica per l'ascolto offline |
| `libri.html` | libri (EPUB, TXT, PDF testuale, testo incollato) e lettore; lettura ad alta voce con voce naturale; «Crea audiolibro» col Mac; audiolibri (anche LibriVox) |
| `video.html` | accesso ai servizi di streaming; scarica video/audio da qualsiasi sito supportato da yt-dlp sul Mac; elenco scaricati |
| `live.html` | andare in diretta su social e piattaforme di streaming audio/video: collegamenti, prova fotocamera/microfono, chiavi di trasmissione (solo sul dispositivo) |
| `naviga.html` | navigatore GPS con musica (comandi del lettore sempre visibili), istruzioni vocali, vignette autostradali con link ufficiali, scheda veicolo da foto e targa |
| `benessere.html` | conta calorie (Open Food Facts), «Settimana» (dispendio dal calendario .ics, bilancio dieta e integratori, confronto prezzi), Trainer |
| `strumenti.html` | ricerca del prezzo migliore, negozi per categoria, risolutore di test (crocette e domande aperte), carte fedeltà e di pagamento, gestore dispositivo |
| `studio.html` | Atlante UCI (accesso con le proprie credenziali), notizie di ciclismo, allenamenti creati dall'IA su qualsiasi argomento, Assistente Idee |
