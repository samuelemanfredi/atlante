# Programmi di Samuele

Due programmi che funzionano da soli, senza IA e senza installare niente: basta Python 3 (sul Mac c'è già, o si
installa da [python.org](https://www.python.org/downloads/)). Ognuno è un solo file `.py`, più un `.command` per il
doppio clic.

| Programma | A cosa serve | Internet |
|---|---|---|
| **Copia audio e video** | legge una memoria (chiavetta, scheda SD, disco esterno, il disco del Mac) e ne copia musica, registrazioni e filmati | **non serve** |
| **Cerca musica** | cerca e scarica musica che gli artisti lasciano scaricare (Creative Commons, pubblico dominio) | serve |

## Come si aprono

1. Doppio clic su **`Copia audio e video.command`** (cartella `copia-audio-video`) o su **`Cerca musica.command`**
   (cartella `cerca-musica`).
2. Si apre una finestra del Terminale e, subito dopo, la pagina del programma nel browser. La pagina funziona solo su
   questo computer (indirizzo `http://127.0.0.1:…`).
3. Per chiudere: il pulsante **Chiudi** in alto nella pagina.

La prima volta il Mac può chiedere due cose, ed è giusto dire di sì:
- se il file è arrivato da Internet: «sviluppatore non identificato» → tasto destro sul file → **Apri** → **Apri**;
- quando leggi una chiavetta: «Terminale vuole accedere ai file su un volume rimovibile» → **Consenti**.

Dal Terminale si usano anche senza pagina: `python3 copia_audio_video.py --help` e `python3 cerca_musica.py --help`.

## Copia audio e video

Tre passi nella pagina: **1** scegli la memoria → **2** «Cerca i file» (mostra quanti audio e video ci sono, per
formato) → **3** scegli dove copiarli e premi «Copia».

- **La memoria di origine si legge soltanto**: il programma non cambia e non cancella niente lì.
- **Due modi di ordinare**: «con le stesse cartelle» (una cartella col nome della memoria, tutto come lì) oppure
  «in ordine di data» (`Audio/2024/2024-05/…`, `Video/…`; per i filmati vale il giorno della ripresa scritto dentro
  il file dal telefono o dalla videocamera).
- **Niente doppioni**: un file uguale a uno già copiato (anche con un altro nome) si salta.
- **Si può fermare e riprendere**: rilanciando la stessa copia, quello che c'è già si salta.
- **Ogni copia è controllata**: il programma rilegge il file copiato e lo confronta con l'originale.
- Alla fine lascia nella destinazione il file **«Rapporto della copia …txt»**: copiati, doppioni, errori.
- Trova anche i file **senza estensione** (li riconosce dal contenuto e aggiunge l'estensione giusta) e salta i file
  nascosti che il Mac scrive sulle chiavette (`._canzone.mp3`), il Cestino e le cartelle di sistema.

Dal Terminale, per esempio:

```
python3 copia_audio_video.py --memorie                                   # quali memorie vede
python3 copia_audio_video.py /Volumes/CHIAVETTA --prova                  # cosa c'è, senza copiare
python3 copia_audio_video.py /Volumes/CHIAVETTA ~/Desktop/Copia --ordina data --solo video
```

**Telefoni**: iPhone e quasi tutti gli Android collegati col cavo non si presentano come una memoria, ma come
un «dispositivo». Per le loro foto e i loro video usa **Acquisizione Immagine** del Mac (o Foto); le **schede SD**
e le memorie USB del telefono, invece, si leggono qui.

## Cerca musica

Scrivi cosa cerchi (artista, titolo, genere), premi **Cerca**: i risultati arrivano fonte per fonte. ▶ ascolta,
⬇ scarica; gli album di Internet Archive si aprono con «Brani ▾» e si scaricano anche interi.

Le fonti:
- **Internet Archive**: album, concerti ed etichette indipendenti con licenza libera (solo quelli con licenza);
- **ccMixter**: brani e remix della sua comunità;
- **Wikimedia Commons**: registrazioni libere, molta musica classica di pubblico dominio;
- **Jamendo (via Openverse)**: i brani di Jamendo; questo servizio a volte è lento o non risponde: c'è «Riprova»;
- **Jamendo** diretto: solo con il codice gratuito di [devportal.jamendo.com](https://devportal.jamendo.com) nella
  variabile `JAMENDO_CLIENT_ID` (è lo stesso `ATLANTE_JAMENDO_CLIENT_ID` del server di Atlante Musica).

I brani vanno in **`Musica/Musica libera`** della tua cartella Inizio. Lì il file **`LICENZE.txt`** ricorda autore,
licenza e pagina di ogni brano. Se un sito non lascia scaricare un file a un programma, il messaggio indica la pagina
del brano, da cui lo scarichi a mano.

Le licenze, in breve: **BY** = cita l'autore se condividi; **NC** (in arancione) = non per guadagnare (ascoltare va
bene); **ND** = non modificarlo; **SA** = le modifiche con la stessa licenza; **CC0** e **pubblico dominio** = nessuna
condizione.

La licenza mostrata è quella che dichiara chi ha messo il brano sul sito: il programma si fida del sito. Prima di
pubblicare un brano o usarlo in pubblico, controlla la sua pagina d'origine (è nel file `LICENZE.txt`).

Perché solo questi siti: Spotify, Apple Music, YouTube e simili danno la musica da **ascoltare**, non da copiare;
scaricarla da lì va contro le loro regole e, quasi sempre, contro il diritto d'autore.

I file `.ogg` (frequenti su Wikimedia) non si aprono con l'app Musica del Mac: si ascoltano con VLC o con l'app
Musica di Atlante.

## Come funzionano dentro (le idee da ricordare)

1. **Riconoscere un file dalla «firma»**: i primi byte di un file dicono cos'è. Un MP3 comincia spesso con `ID3`,
   un FLAC con `fLaC`, un MP4 o MOV ha `ftyp` dal quinto byte. Così si riconosce un video anche se ha perso il nome.
2. **L'impronta SHA-256**: da tutto il contenuto di un file si calcola un codice di 64 caratteri. Due file con la
   stessa impronta sono identici, anche con nomi diversi: così si trovano i doppioni e si controlla che la copia sia
   uguale all'originale.
3. **Prima «.parziale», poi il nome vero**: si copia (o si scarica) in un file nascosto; solo a lavoro finito e
   controllato prende il suo nome. Se manca la corrente o stacchi la chiavetta, non resta mai un file rotto con un
   nome normale.
4. **Le API**: Internet Archive, ccMixter, Wikimedia e Openverse hanno un indirizzo a cui un programma fa domande
   (es. `archive.org/advancedsearch.php?q=jazz`) e da cui riceve risposte in **JSON**, un testo ordinato che il
   programma legge come una tabella.
5. **Un server solo per te**: il programma apre una pagina su `127.0.0.1`, l'indirizzo «di questo computer». Dagli
   altri dispositivi non si raggiunge; in più la pagina ha una chiave segreta nuova a ogni avvio, così nemmeno un
   sito aperto nel browser può comandare il programma.

## Ripasso (prova a rispondere prima di aprire)

<details><summary>1. Cosa fa il programma se un file della chiavetta c'è già nella destinazione con un altro nome?</summary>

Calcola l'impronta SHA-256 dei due file: se è uguale, il file non si copia e conta come «doppione».
Prima confronta la dimensione: due file di dimensione diversa non possono essere uguali, e l'impronta non serve.
</details>

<details><summary>2. Stacchi la chiavetta a metà copia. Cosa trovi nella destinazione?</summary>

Solo i file completi e controllati. Quello a metà era un «.parziale» nascosto e viene cancellato. Ricollega la
chiavetta e rilancia la copia: riprende da dove era rimasta.
</details>

<details><summary>3. Come si riconosce un video che ha perso l'estensione?</summary>

Dalla firma nei primi byte: per MP4 e MOV la parola `ftyp` dal quinto byte. Il programma aggiunge l'estensione giusta
alla copia.
</details>

<details><summary>4. Puoi usare per un video su YouTube con la pubblicità un brano «CC BY-NC»?</summary>

No: NC vuol dire «non commerciale», e un video con la pubblicità guadagna. Per ascoltarlo, invece, va bene.
Un brano «CC BY» sì, citando l'autore.
</details>

<details><summary>5. Perché la pagina del programma non si apre dal telefono?</summary>

Perché il programma risponde solo su `127.0.0.1`, cioè «questo computer»: è una protezione voluta.
</details>

## Prove automatiche

Ogni programma ha le sue prove, che non usano Internet:

```
python3 copia-audio-video/test_copia_audio_video.py
python3 cerca-musica/test_cerca_musica.py
```
