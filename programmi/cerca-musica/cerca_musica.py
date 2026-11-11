#!/usr/bin/env python3
"""Cerca musica: cerca e scarica musica dai siti dove gli artisti la lasciano scaricare (licenze Creative Commons e
pubblico dominio). Funziona da solo, senza IA: solo libreria standard di Python 3.

    python3 cerca_musica.py                  apre la pagina del programma nel browser
    python3 cerca_musica.py "jazz piano"     cerca dal Terminale e chiede quali brani scaricare (opzioni: --help)

Le fonti (ognuna è un «servizio» con un indirizzo pubblico, una API, che risponde in JSON):
  - Internet Archive (archive.org): album e concerti con licenza Creative Commons o di pubblico dominio;
  - ccMixter (ccmixter.org): brani e remix con licenza Creative Commons;
  - Wikimedia Commons (commons.wikimedia.org): registrazioni libere, molta musica classica di pubblico dominio;
  - Openverse (api.openverse.org): un motore di ricerca di opere con licenza libera, qui per i brani di Jamendo.
    Nessuna chiave, ma a volte è lento o non risponde (fino a un minuto);
  - Jamendo (api.jamendo.com), solo con il client_id gratuito di devportal.jamendo.com nella variabile
    JAMENDO_CLIENT_ID (o ATLANTE_JAMENDO_CLIENT_ID, la stessa del server di Atlante Musica).

Dai servizi commerciali (Spotify, Apple Music, YouTube...) non si scarica: lì la musica si ascolta, non si copia.
I brani vanno in ~/Music/Musica libera; in LICENZE.txt, nella stessa cartella, autore, licenza e pagina d'origine
di ognuno (le licenze Creative Commons chiedono di citare l'autore se si condivide il brano).
"""
import argparse
import concurrent.futures
import html
import http.client
import json
import os
import platform
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlsplit

VERSIONE = "1.0"
# Wikimedia chiede un nome del programma con un indirizzo di contatto
AGENTE = "CercaMusica/%s (https://github.com/samuelemanfredi/atlante; programma personale) Python-urllib" % VERSIONE
PER_PAGINA = 20
TEMPO = {"openverse": 70, "archivio": 25, "ccmixter": 25, "wikimedia": 25, "jamendo": 25}   # secondi di attesa per fonte
MAX_FILE = 600 * 1024 * 1024      # un file più grande non è un brano
MAX_RISPOSTA = 10 * 1024 * 1024   # risposte JSON delle ricerche
CARTELLA = os.path.join(os.path.expanduser("~"), "Music", "Musica libera")
# i soli siti da cui il programma scarica (anche dopo un reindirizzamento)
SITI_LIBERI = ("jamendo.com", "archive.org", "ccmixter.org", "wikimedia.org", "openverse.org")
FONTI = {
    "archivio": "Internet Archive",
    "ccmixter": "ccMixter",
    "wikimedia": "Wikimedia Commons",
    "openverse": "Jamendo (via Openverse)",
    "jamendo": "Jamendo",
}
ESTENSIONI = (".mp3", ".ogg", ".oga", ".opus", ".flac", ".wav", ".m4a", ".aiff", ".aif")
# Internet Archive: di ogni brano si prende una versione sola, la prima di questo elenco che c'è
IA_FORMATI = ("VBR MP3", "320Kbps MP3", "256Kbps MP3", "224Kbps MP3", "192Kbps MP3", "160Kbps MP3", "128Kbps MP3",
              "112Kbps MP3", "96Kbps MP3", "64Kbps MP3", "MP3", "MPEG-4 Audio", "Ogg Vorbis", "Flac", "24bit Flac",
              "WAVE", "Apple Lossless Audio", "AIFF")
# collezioni escluse: audiolibri, podcast e radio (non sono musica) e i dischi a 78 giri (la registrazione è antica,
# ma la canzone può avere ancora autori con i diritti)
IA_ESCLUSE = ("librivoxaudio", "podcasts", "ourmedia", "radioprograms", "oldtimeradio", "audio_bookspoetry",
              "audio_religion", "audio_news", "audio_tech", "audio_podcast", "78rpm", "georgeblood")


class ErroreFonte(Exception):
    pass


# ---------- rete ----------

def leggi_json(url, tempo=25):
    """GET di un indirizzo che risponde in JSON."""
    r = urllib.request.Request(url, headers={"User-Agent": AGENTE, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=tempo) as risp:
            dati = risp.read(MAX_RISPOSTA + 1)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise ErroreFonte("troppe ricerche di fila: riprova fra qualche minuto")
        raise ErroreFonte("il sito risponde con l'errore %d" % e.code)
    except (urllib.error.URLError, OSError) as e:
        motivo = getattr(e, "reason", e)
        if isinstance(motivo, (socket.timeout, TimeoutError)) or "timed out" in str(motivo):
            raise ErroreFonte("il sito non risponde (troppo lento)")
        raise ErroreFonte("il sito non si raggiunge: controlla la connessione a Internet")
    except http.client.HTTPException:
        raise ErroreFonte("il sito ha dato una risposta rovinata: riprova")
    if len(dati) > MAX_RISPOSTA:
        raise ErroreFonte("risposta troppo grande")
    try:
        return json.loads(dati.decode("utf-8", "replace"))
    except ValueError:
        raise ErroreFonte("risposta non valida")


def sito_libero(url):
    """True se `url` è https (o http) su uno dei siti di musica libera."""
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme in ("https", "http") and any(host == s or host.endswith("." + s) for s in SITI_LIBERI)


class _SoloSitiLiberi(urllib.request.HTTPRedirectHandler):
    """I reindirizzamenti (Internet Archive manda ai suoi server, Jamendo al suo archivio) restano sui siti liberi."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not sito_libero(newurl):
            raise urllib.error.HTTPError(newurl, code, "reindirizzato fuori dai siti di musica libera", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


APRI_LIBERI = urllib.request.build_opener(_SoloSitiLiberi)


# ---------- licenze ----------

def licenza_da_url(url):
    """https://creativecommons.org/licenses/by-nc-sa/4.0/ → «CC BY-NC-SA 4.0». '' se non è una licenza libera."""
    u = (url or "").lower()
    if "creativecommons.org" not in u:
        return ""
    if "/publicdomain/zero" in u:
        return "CC0"
    if "/publicdomain" in u:
        return "Pubblico dominio"
    m = re.search(r"/licenses/([a-z+-]+)/(\d(?:\.\d)?)", u)
    if m:
        return "CC %s %s" % (m.group(1).upper(), m.group(2))
    m = re.search(r"/licenses/([a-z+-]+)", u)
    return "CC %s" % m.group(1).upper() if m else "Creative Commons"


def licenza_openverse(codice, versione):
    codice = (codice or "").lower()
    if codice == "cc0":
        return "CC0"
    if codice == "pdm":
        return "Pubblico dominio"
    return ("CC %s %s" % (codice.upper(), versione or "")).strip()


def uso(licenza):
    """'libero' (qualsiasi uso, citando l'autore), 'personale' (NC: non a scopo commerciale), ''."""
    if not licenza:
        return ""
    parti = licenza.split()   # «CC BY-NC-SA 4.0» → il codice è la seconda parola
    return "personale" if len(parti) > 1 and "NC" in parti[1].split("-") else "libero"


# ---------- le fonti ----------

def _durata(v):
    """Secondi da 240.9, '240.9', '4:01' o '1:02:03'."""
    if v in (None, ""):
        return 0
    try:
        if isinstance(v, str) and ":" in v:
            s = 0
            for parte in v.split(":"):
                s = s * 60 + float(parte)
            return int(s)
        return int(float(v))
    except ValueError:
        return 0


def _est(url, ripiego=".mp3"):
    est = os.path.splitext(urlsplit(url).path)[1].lower()
    return est if est in ESTENSIONI else ripiego


def _uno(v):
    if isinstance(v, list):
        v = v[0] if v else ""
    return str(v or "").strip()


def cerca_openverse(q, pagina=1):
    url = "https://api.openverse.org/v1/audio/?" + urlencode(
        {"q": q, "page": pagina, "page_size": PER_PAGINA, "source": "jamendo"})
    d = leggi_json(url, TEMPO["openverse"])
    if isinstance(d, dict) and d.get("detail") and not d.get("results"):
        raise ErroreFonte(str(d["detail"])[:200])
    risultati = []
    for x in d.get("results") or []:
        audio = x.get("url") or ""
        if not sito_libero(audio):
            continue
        fonte = "Jamendo" if x.get("source") == "jamendo" else "Openverse"
        album = x.get("audio_set") or {}
        lic = licenza_openverse(x.get("license"), x.get("license_version"))
        risultati.append({
            "id": "ov:" + str(x.get("id")), "tipo": "brano", "fonte": fonte,
            "titolo": x.get("title") or "Senza titolo", "artista": x.get("creator") or "",
            "album": album.get("title") if isinstance(album, dict) else "",
            "durata": int((x.get("duration") or 0) / 1000), "licenza": lic, "uso": uso(lic),
            "licenza_url": x.get("license_url") or "", "pagina": x.get("foreign_landing_url") or "",
            "audio": audio, "est": ".mp3" if (x.get("filetype") or "").startswith("mp3") else _est(audio),
            "immagine": x.get("thumbnail") or "", "generi": x.get("genres") or []})
    return risultati, pagina < int(d.get("page_count") or 0)


def cerca_archivio(q, pagina=1):
    # solo con licenza libera, con almeno un file audio («MP3» vale per tutti gli MP3: «VBR MP3», «128Kbps MP3»...)
    filtro = ("(%s) AND mediatype:audio AND (licenseurl:*creativecommons.org* OR licenseurl:*publicdomain*) AND "
              "format:(MP3 OR \"MPEG-4 Audio\" OR \"Ogg Vorbis\" OR Flac OR WAVE) AND -collection:(%s)") % (
        q, " OR ".join(IA_ESCLUSE))
    campi = ("identifier", "title", "creator", "licenseurl", "year", "downloads")
    url = "https://archive.org/advancedsearch.php?" + urlencode(
        [("q", filtro)] + [("fl[]", c) for c in campi] + [("rows", PER_PAGINA), ("page", pagina), ("output", "json")])
    d = leggi_json(url, TEMPO["archivio"])
    risp = d.get("response") or {}
    risultati = []
    for x in risp.get("docs") or []:
        ident = _uno(x.get("identifier"))
        lic = licenza_da_url(_uno(x.get("licenseurl")))
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", ident) or not lic:
            continue
        risultati.append({
            "id": "ia:" + ident, "tipo": "album", "fonte": "Internet Archive", "ia": ident,
            "titolo": _uno(x.get("title")) or ident, "artista": _uno(x.get("creator")),
            "album": "", "anno": _uno(x.get("year")), "licenza": lic, "uso": uso(lic),
            "licenza_url": _uno(x.get("licenseurl")), "pagina": "https://archive.org/details/" + ident,
            "immagine": "https://archive.org/services/img/" + ident})
    return risultati, pagina * PER_PAGINA < int(risp.get("numFound") or 0)


def brani_archivio(ident):
    """I brani di un album di Internet Archive: una versione per brano (MP3 se c'è), in ordine di traccia."""
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", ident or ""):
        raise ErroreFonte("album non valido")
    d = leggi_json("https://archive.org/metadata/" + ident, TEMPO["archivio"])
    meta = d.get("metadata") or {}
    lic_url = _uno(meta.get("licenseurl"))
    lic = licenza_da_url(lic_url)
    if not lic:
        raise ErroreFonte("questo album non ha una licenza libera")
    migliori = {}
    for f in d.get("files") or []:
        formato = f.get("format") or ""
        if formato not in IA_FORMATI:
            continue
        chiave = f.get("original") if f.get("source") == "derivative" and f.get("original") else f.get("name")
        rango = IA_FORMATI.index(formato)
        if chiave not in migliori or rango < migliori[chiave][0]:
            migliori[chiave] = (rango, f)
    originali = {f.get("name"): f for f in d.get("files") or []}
    brani = []
    for chiave, (_, f) in migliori.items():
        orig = originali.get(chiave) or f
        nome = f.get("name") or ""
        traccia = re.match(r"\d+", _uno(orig.get("track") or f.get("track")))
        brani.append({
            "id": "ia:%s/%s" % (ident, nome), "tipo": "brano", "fonte": "Internet Archive",
            "titolo": _uno(orig.get("title") or f.get("title")) or os.path.splitext(os.path.basename(nome))[0],
            "artista": _uno(orig.get("creator") or f.get("creator") or orig.get("artist") or meta.get("creator")),
            "album": _uno(meta.get("title")), "traccia": int(traccia.group()) if traccia else 0,
            "durata": _durata(f.get("length") or orig.get("length")), "licenza": lic, "uso": uso(lic),
            "licenza_url": lic_url, "pagina": "https://archive.org/details/" + ident,
            "audio": "https://archive.org/download/%s/%s" % (ident, quote(nome)), "est": _est(nome),
            "immagine": "https://archive.org/services/img/" + ident})
    brani.sort(key=lambda b: (b["traccia"] or 10 ** 6, b["id"].casefold()))
    return brani[:300]


def cerca_ccmixter(q, pagina=1):
    # f=js è lo stesso JSON di f=json, ma arriva intero anche quando è grande (f=json oltre ~100 KB si rompe)
    url = "https://ccmixter.org/api/query?" + urlencode(
        {"f": "js", "search": q, "limit": PER_PAGINA, "offset": (pagina - 1) * PER_PAGINA, "sort": "rank"})
    d = leggi_json(url, TEMPO["ccmixter"])
    risultati = []
    for x in d if isinstance(d, list) else []:
        audio, est, durata = "", ".mp3", 0
        for f in x.get("files") or []:
            info = f.get("file_format_info") or {}
            if info.get("media-type") == "audio" and sito_libero(f.get("download_url") or ""):
                audio, est, durata = f["download_url"], "." + (info.get("default-ext") or "mp3"), _durata(info.get("ps"))
                if est == ".mp3":
                    break   # il primo MP3 è il brano completo (gli altri file sono spesso tracce separate)
        lic = licenza_da_url(x.get("license_url"))
        if not audio or not lic:
            continue
        risultati.append({
            "id": "cc:%s" % x.get("upload_id"), "tipo": "brano", "fonte": "ccMixter",
            "titolo": x.get("upload_name") or "Senza titolo",
            "artista": x.get("user_real_name") or x.get("user_name") or "", "album": "", "durata": durata,
            "licenza": lic, "uso": uso(lic), "licenza_url": x.get("license_url") or "",
            "pagina": x.get("file_page_url") or "", "audio": audio, "est": est, "immagine": ""})
    return risultati, len(d if isinstance(d, list) else []) >= PER_PAGINA


def _testo_html(v, quanto=80):
    """Il testo di un pezzetto di HTML (Wikimedia scrive l'autore come link)."""
    t = html.unescape(re.sub(r"<[^>]+>", " ", str(v or "")))
    return " ".join(t.split())[:quanto]


def cerca_wikimedia(q, pagina=1):
    url = "https://commons.wikimedia.org/w/api.php?" + urlencode({
        "action": "query", "format": "json", "formatversion": "2", "generator": "search",
        "gsrsearch": "%s filetype:audio" % q, "gsrnamespace": 6, "gsrlimit": PER_PAGINA,
        "gsroffset": (pagina - 1) * PER_PAGINA, "prop": "imageinfo", "iiprop": "url|size|mediatype|extmetadata",
        "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist|ObjectName"})
    d = leggi_json(url, TEMPO["wikimedia"])
    if d.get("error"):
        raise ErroreFonte("Wikimedia: %s" % str((d["error"] or {}).get("info", "errore"))[:200])
    pagine = (d.get("query") or {}).get("pages") or []
    if isinstance(pagine, dict):   # formatversion 1: {id: pagina}
        pagine = list(pagine.values())
    risultati = []
    for x in sorted(pagine, key=lambda x: x.get("index") or 0):
        info = (x.get("imageinfo") or [{}])[0]
        audio, meta = info.get("url") or "", info.get("extmetadata") or {}
        est = os.path.splitext(urlsplit(audio).path)[1].lower()
        if not sito_libero(audio) or est not in ESTENSIONI or info.get("mediatype", "AUDIO") != "AUDIO":
            continue   # anche i MIDI (.mid): sono spartiti per il computer, non registrazioni
        lic_url = (meta.get("LicenseUrl") or {}).get("value") or ""
        breve = _testo_html((meta.get("LicenseShortName") or {}).get("value"), 40)
        lic = licenza_da_url(lic_url) or ("Pubblico dominio" if "public domain" in breve.lower() else breve)
        nome = re.sub(r"^File:", "", x.get("title") or "")
        titolo = _testo_html((meta.get("ObjectName") or {}).get("value"), 150) or \
            os.path.splitext(nome)[0].replace("_", " ")
        risultati.append({
            "id": "wm:%s" % (x.get("pageid") or nome), "tipo": "brano", "fonte": "Wikimedia Commons",
            "titolo": titolo or "Senza titolo", "artista": _testo_html((meta.get("Artist") or {}).get("value")),
            "album": "", "durata": _durata(info.get("duration")), "licenza": lic, "uso": uso(lic),
            "licenza_url": lic_url, "pagina": info.get("descriptionurl") or "", "audio": audio, "est": est,
            "immagine": ""})
    return risultati, "continue" in d


def jamendo_id():
    return (os.environ.get("JAMENDO_CLIENT_ID") or os.environ.get("ATLANTE_JAMENDO_CLIENT_ID") or "").strip()


def cerca_jamendo(q, pagina=1):
    cid = jamendo_id()
    if not cid:
        raise ErroreFonte("serve il client_id gratuito di devportal.jamendo.com (variabile JAMENDO_CLIENT_ID)")
    url = "https://api.jamendo.com/v3.0/tracks/?" + urlencode(
        {"client_id": cid, "format": "json", "limit": PER_PAGINA, "offset": (pagina - 1) * PER_PAGINA,
         "search": q, "audiodlformat": "mp32", "include": "licenses"})
    try:
        d = leggi_json(url, TEMPO["jamendo"])
    except ErroreFonte as e:
        raise ErroreFonte(str(e).replace(cid, "…"))   # il client_id non si mostra mai
    testa = d.get("headers") or {}
    if testa.get("status") == "failed":
        raise ErroreFonte(("Jamendo: %s" % testa.get("error_message", "errore")).replace(cid, "…"))
    risultati = []
    for x in d.get("results") or []:
        audio = x.get("audiodownload") if x.get("audiodownload_allowed") else ""
        lic = licenza_da_url(x.get("license_ccurl"))
        if not audio or not sito_libero(audio) or not lic:
            continue
        risultati.append({
            "id": "jm:%s" % x.get("id"), "tipo": "brano", "fonte": "Jamendo", "titolo": x.get("name") or "",
            "artista": x.get("artist_name") or "", "album": x.get("album_name") or "",
            "durata": _durata(x.get("duration")), "licenza": lic, "uso": uso(lic),
            "licenza_url": x.get("license_ccurl") or "", "pagina": x.get("shareurl") or "", "audio": audio,
            "est": ".mp3", "immagine": x.get("album_image") or x.get("image") or ""})
    return risultati, int(testa.get("results_count") or 0) >= PER_PAGINA


CERCA = {"archivio": cerca_archivio, "ccmixter": cerca_ccmixter, "wikimedia": cerca_wikimedia,
         "openverse": cerca_openverse, "jamendo": cerca_jamendo}


def fonti_attive():
    return [f for f in FONTI if f != "jamendo" or jamendo_id()]


def cerca(fonte, q, pagina=1):
    """{risultati, altri} di una fonte, o ErroreFonte."""
    q = " ".join((q or "").split())[:200]
    if not q:
        raise ErroreFonte("scrivi cosa cercare")
    if fonte not in CERCA:
        raise ErroreFonte("fonte sconosciuta")
    risultati, altri = CERCA[fonte](q, max(1, min(int(pagina), 50)))
    return {"risultati": risultati, "altri": bool(altri and risultati)}


# ---------- scaricare ----------

def pulisci(nome, quanto=120):
    """Un nome di file valido su Mac, Windows e Linux."""
    nome = re.sub(r'[\x00-\x1f/\\:*?"<>|]+', " ", nome or "")
    nome = " ".join(nome.split()).strip(" .")
    return nome[:quanto].strip(" .") or "Senza titolo"


def _con_artista(artista, nome):
    """«Artista - nome», o solo «nome» se l'artista è già scritto lì dentro."""
    if not artista or artista.casefold() in (nome or "").casefold():
        return nome or ""
    return "%s - %s" % (artista, nome or "")


def nome_file(brano):
    """«Artista - Titolo.mp3»; i brani di un album di Internet Archive in «Artista - Album/NN Titolo.mp3»."""
    est = brano.get("est") or ".mp3"
    if brano.get("fonte") == "Internet Archive" and brano.get("album"):
        cartella = pulisci(_con_artista(brano.get("artista"), brano["album"]))
        n = "%02d " % brano["traccia"] if brano.get("traccia") else ""
        return os.path.join(cartella, pulisci(n + brano.get("titolo", "")) + est)
    return pulisci(_con_artista(brano.get("artista"), brano.get("titolo", ""))) + est


def scarica(brano, cartella=CARTELLA, avanzamento=None, ferma=None):
    """Scarica il brano in `cartella` (prima in «.nome.parziale», poi il nome vero) e aggiunge una riga a LICENZE.txt.
    Restituisce (percorso, nuovo): nuovo=False se il file c'era già."""
    url = brano.get("audio") or ""
    if not sito_libero(url):
        raise ErroreFonte("si scarica solo dai siti di musica libera")
    arrivo = os.path.join(cartella, nome_file(brano))
    if os.path.exists(arrivo) and os.path.getsize(arrivo) > 0:
        return arrivo, False
    os.makedirs(os.path.dirname(arrivo), exist_ok=True)
    parziale = os.path.join(os.path.dirname(arrivo), "." + os.path.basename(arrivo) + ".parziale")
    r = urllib.request.Request(url, headers={"User-Agent": AGENTE})
    try:
        with APRI_LIBERI.open(r, timeout=60) as risp, open(parziale, "wb") as f:
            totale = int(risp.headers.get("Content-Length") or 0)
            tipo = (risp.headers.get("Content-Type") or "").lower()
            if tipo.startswith("text/html"):
                raise ErroreFonte("il sito ha dato una pagina invece del brano")
            if totale > MAX_FILE:
                raise ErroreFonte("file troppo grande per essere un brano")
            fatti = 0
            while True:
                if ferma is not None and ferma.is_set():
                    raise ErroreFonte("fermato")
                pezzo = risp.read(256 * 1024)
                if not pezzo:
                    break
                fatti += len(pezzo)
                if fatti > MAX_FILE:
                    raise ErroreFonte("file troppo grande per essere un brano")
                f.write(pezzo)
                if avanzamento:
                    avanzamento(fatti, totale)
        if fatti == 0 or (totale and fatti < totale):
            raise ErroreFonte("scaricamento incompleto: riprova")
        os.replace(parziale, arrivo)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ErroreFonte("il sito non lascia scaricare questo file a un programma: scaricalo dalla pagina del "
                              "brano (%s)" % (brano.get("pagina") or brano.get("fonte") or "sul sito"))
        raise ErroreFonte("il sito risponde con l'errore %d" % e.code if e.code else str(e.reason))
    except urllib.error.URLError:
        raise ErroreFonte("il sito non si raggiunge o è troppo lento")
    except (http.client.HTTPException, socket.timeout, TimeoutError, ConnectionError):
        raise ErroreFonte("lo scaricamento si è interrotto: riprova")
    finally:
        if os.path.exists(parziale):
            try:
                os.remove(parziale)
            except OSError:
                pass
    annota_licenza(cartella, brano, os.path.relpath(arrivo, cartella))
    return arrivo, True


def annota_licenza(cartella, brano, nome):
    riga = "%s — %s | %s %s | %s | %s | %s\n" % (
        brano.get("titolo", ""), brano.get("artista") or "autore sconosciuto", brano.get("licenza", ""),
        brano.get("licenza_url", ""), brano.get("fonte", ""), brano.get("pagina", ""), nome)
    percorso = os.path.join(cartella, "LICENZE.txt")
    nuovo = not os.path.exists(percorso)
    with open(percorso, "a", encoding="utf-8") as f:
        if nuovo:
            f.write("Brani scaricati con Cerca musica: titolo — autore | licenza | fonte | pagina | file\n"
                    "Se condividi un brano, cita l'autore e la licenza (è la condizione «BY» delle Creative Commons).\n"
                    "NC = non a scopo commerciale, ND = senza modifiche, SA = le modifiche con la stessa licenza.\n\n")
        f.write(riga)


class Coda:
    """Scaricamenti uno alla volta in un thread; la pagina legge `elenco()`."""

    def __init__(self, cartella):
        self.cartella = cartella
        self.lock = threading.Lock()
        self.lavori = []
        self.sveglia = threading.Event()
        self.ferma = threading.Event()
        threading.Thread(target=self._lavora, daemon=True).start()

    def aggiungi(self, brano):
        if not sito_libero(brano.get("audio") or ""):
            raise ErroreFonte("si scarica solo dai siti di musica libera")
        with self.lock:
            for l in self.lavori:
                if l["brano"].get("id") == brano.get("id") and l["stato"] in ("in coda", "scarico", "fatto"):
                    return l["id"]
            lavoro = {"id": secrets.token_hex(6), "brano": brano, "stato": "in coda", "fatti": 0, "totale": 0,
                      "file": "", "errore": ""}
            self.lavori.append(lavoro)
        self.sveglia.set()
        return lavoro["id"]

    def elenco(self):
        with self.lock:
            return [{"id": l["id"], "brano_id": l["brano"].get("id"), "titolo": l["brano"].get("titolo"),
                     "artista": l["brano"].get("artista"), "stato": l["stato"], "fatti": l["fatti"],
                     "totale": l["totale"], "file": l["file"], "errore": l["errore"]} for l in reversed(self.lavori)]

    def _prossimo(self):
        with self.lock:
            for l in self.lavori:
                if l["stato"] == "in coda":
                    l["stato"] = "scarico"
                    return l
        return None

    def _lavora(self):
        while not self.ferma.is_set():
            lavoro = self._prossimo()
            if lavoro is None:
                self.sveglia.wait(1)
                self.sveglia.clear()
                continue

            def avanti(fatti, totale):
                with self.lock:
                    lavoro["fatti"], lavoro["totale"] = fatti, totale

            try:
                percorso, nuovo = scarica(lavoro["brano"], self.cartella, avanti, self.ferma)
                with self.lock:
                    lavoro.update(stato="fatto" if nuovo else "c'era già", file=os.path.relpath(percorso, self.cartella))
            except (ErroreFonte, OSError) as e:
                with self.lock:
                    lavoro.update(stato="errore", errore=str(e.strerror if isinstance(e, OSError) and e.strerror else e))


# ---------- la pagina (server solo su questo computer) ----------

PAGINA = r"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="token" content="__TOKEN__">
<title>Cerca musica</title>
<style>
:root{--bg:#f2f2f7;--card:#fff;--fg:#1c1c1e;--fg2:#6e6e73;--line:#e5e5ea;--fill:#e9e9ee;--accent:#d6336c;
  --onaccent:#fff;--ok:#1f8a3b;--bad:#c62828;--warn:#9a5b00;color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{--bg:#000;--card:#1c1c1e;--fg:#f2f2f7;--fg2:#a1a1a6;--line:#38383a;
  --fill:#2c2c2e;--accent:#ff6b9a;--onaccent:#2b0010;--ok:#4cd964;--bad:#ff6b6b;--warn:#ffb340}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,
  Helvetica,Arial,sans-serif}
.wrap{max-width:820px;margin:0 auto;padding:20px 16px 60px}
.testa{display:flex;gap:12px;align-items:flex-start;justify-content:space-between}
h1{font-size:28px;margin:6px 0 2px}
.sub{color:var(--fg2);margin:0 0 16px}
form{display:flex;gap:8px;margin:0 0 10px}
input[type=search]{flex:1;min-width:0;font:inherit;padding:11px 14px;border-radius:12px;border:1px solid var(--line);
  background:var(--card);color:var(--fg);outline:none}
input[type=search]:focus{border-color:var(--accent)}
.btn{appearance:none;border:0;border-radius:10px;padding:10px 16px;font:inherit;font-weight:600;cursor:pointer;
  background:var(--fill);color:var(--accent);white-space:nowrap}
.btn.primo{background:var(--accent);color:var(--onaccent)}
.btn.sm{padding:6px 10px;font-size:14px}
.btn:disabled{opacity:.45;cursor:default}
.fonti{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 6px}
.fonti label{display:inline-flex;gap:6px;align-items:center;background:var(--card);border:1px solid var(--line);
  border-radius:20px;padding:5px 12px;font-size:14px;cursor:pointer}
.fonti input{accent-color:var(--accent)}
.piccolo{color:var(--fg2);font-size:13px}
.card{background:var(--card);border-radius:14px;padding:12px 14px;margin:12px 0;border:1px solid var(--line)}
.card h2{font-size:16px;margin:2px 0 8px;display:flex;gap:8px;align-items:center}
.card h2 .piccolo{font-weight:400}
.r{display:flex;gap:10px;align-items:center;padding:8px 0;border-top:1px solid var(--line)}
.r:first-of-type{border-top:0}
.r img,.r .ph{width:48px;height:48px;border-radius:8px;object-fit:cover;flex:none;background:var(--fill);
  display:flex;align-items:center;justify-content:center;font-size:20px}
.r .tx{flex:1;min-width:0}
.r .tx b{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600}
.r .tx span{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--fg2);font-size:14px}
.lic{display:inline-block;font-size:11px;font-weight:600;border-radius:6px;padding:1px 6px;margin-right:4px;
  background:color-mix(in srgb,var(--ok) 15%,transparent);color:var(--ok);vertical-align:1px}
.lic.personale{background:color-mix(in srgb,var(--warn) 15%,transparent);color:var(--warn)}
.az{display:flex;gap:6px;flex:none;align-items:center}
.tondo{width:38px;height:38px;border-radius:50%;border:0;background:var(--fill);color:var(--accent);cursor:pointer;
  font-size:15px;display:inline-flex;align-items:center;justify-content:center;flex:none}
.tondo.on{background:var(--accent);color:var(--onaccent)}
.tondo:disabled{cursor:default;opacity:.8}
.stato{font-size:13px;min-width:44px;text-align:center;color:var(--accent);font-weight:600;font-variant-numeric:tabular-nums}
.brani{margin:0 0 6px 58px;border-left:3px solid var(--fill);padding-left:10px}
.brani .r img{display:none}
.giro{display:inline-block;width:14px;height:14px;border:2px solid var(--fill);border-top-color:var(--accent);
  border-radius:50%;animation:g 1s linear infinite}
@keyframes g{to{transform:rotate(360deg)}}
.errore{color:var(--bad);font-size:14px}
.barra{height:4px;border-radius:2px;background:var(--fill);overflow:hidden;margin-top:4px}
.barra i{display:block;height:100%;background:var(--accent)}
details{margin:14px 0}summary{cursor:pointer;color:var(--accent)}
.spiega{font-size:14px;color:var(--fg2)}
.spiega b{color:var(--fg)}
a{color:var(--accent)}
</style>
</head>
<body>
<div class="wrap">
  <div class="testa"><h1>Cerca musica</h1><button class="btn" id="esci">Chiudi</button></div>
  <p class="sub">Musica che gli artisti lasciano scaricare (Creative Commons e pubblico dominio).</p>
  <form id="f"><input type="search" id="q" placeholder="Artista, titolo, genere… (es. jazz piano)" autofocus>
    <button class="btn primo">Cerca</button></form>
  <div class="fonti" id="fonti"></div>
  <div class="piccolo">I brani vanno in <span id="cartella"></span> · <a href="#" id="apri">apri la cartella</a></div>
  <div id="scaricamenti"></div>
  <div id="risultati"></div>
  <details><summary>Cosa vogliono dire le licenze?</summary><div class="spiega">
    <p><b>CC</b> = Creative Commons: l'autore ti permette di copiare e ascoltare il brano a certe condizioni.</p>
    <p><b>BY</b>: se lo condividi, cita l'autore. <b>SA</b>: le modifiche con la stessa licenza.
      <b>ND</b>: non modificarlo. <b>NC</b> (in arancione): non usarlo per guadagnare (va bene ascoltarlo).</p>
    <p><b>CC0</b> e <b>Pubblico dominio</b>: nessuna condizione.</p>
    <p>Autore, licenza e pagina di ogni brano scaricato sono nel file LICENZE.txt della cartella.</p></div></details>
</div>
<audio id="lettore" preload="none"></audio>
<script>
const T = document.querySelector('meta[name=token]').content;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(p, corpo){
  const r = await fetch(p, {method: corpo ? 'POST' : 'GET', headers: {'X-Token': T, 'Content-Type': 'application/json'},
    body: corpo ? JSON.stringify(corpo) : undefined});
  const d = await r.json().catch(() => ({errore: 'risposta non valida'}));
  if (!r.ok) throw new Error(d.errore || ('errore ' + r.status));
  return d;
}
function durata(s){ if (!s) return ''; s = Math.round(s); const m = Math.floor(s / 60);
  return m >= 60 ? Math.floor(m / 60) + ':' + String(m % 60).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0')
    : m + ':' + String(s % 60).padStart(2, '0'); }
const brani = new Map();          // id → brano (per ascoltare e scaricare)
let fonti = [], ricerca = '', stati = {}, giro = null, inAscolto = '';

async function inizio(){
  const d = await api('api/fonti');
  fonti = d.fonti; $('cartella').textContent = d.cartella;
  let tolte = []; try { tolte = JSON.parse(localStorage.getItem('fonti-tolte') || '[]'); } catch(e) {}
  $('fonti').innerHTML = d.fonti.map(f => `<label title="${esc(f.nota)}"><input type="checkbox" value="${f.id}"
    ${!f.attiva ? 'disabled' : tolte.includes(f.id) ? '' : 'checked'}> ${esc(f.nome)}</label>`).join('');
  $('fonti').onchange = () => { try { localStorage.setItem('fonti-tolte', JSON.stringify(
    [...$('fonti').querySelectorAll('input:not(:checked):not(:disabled)')].map(i => i.value))); } catch(e) {} };
  aggiornaScaricamenti();
}
$('f').onsubmit = e => {
  e.preventDefault(); ricerca = $('q').value.trim(); if (!ricerca) return;
  const scelte = [...$('fonti').querySelectorAll('input:checked')].map(i => i.value);
  $('risultati').innerHTML = scelte.map(f => `<section class="card" id="s-${f}"><h2>${esc(fonti.find(x => x.id === f).nome)}
    <span class="piccolo" id="n-${f}"></span></h2><div id="l-${f}"></div></section>`).join('');
  for (const f of scelte) carica(f, 1);
};
async function carica(fonte, pagina){
  const lista = $('l-' + fonte); if (!lista) return;
  const attesa = document.createElement('div'); attesa.className = 'piccolo';
  attesa.innerHTML = '<span class="giro"></span> cerco…' + (fonte === 'openverse' ? ' (questa fonte può metterci un minuto)' : '');
  lista.appendChild(attesa);
  const q = ricerca;
  try {
    const d = await api(`api/cerca?fonte=${fonte}&pagina=${pagina}&q=${encodeURIComponent(q)}`);
    if (q !== ricerca) return;
    attesa.remove();
    if (!d.risultati.length && pagina === 1) { lista.innerHTML = '<div class="piccolo">Niente con queste parole.</div>'; return; }
    for (const b of d.risultati) lista.insertAdjacentHTML('beforeend', riga(b));
    $('n-' + fonte).textContent = lista.querySelectorAll(':scope > .r').length + ' risultati';
    if (d.altri) {
      const altri = document.createElement('button'); altri.className = 'btn sm'; altri.textContent = 'Altri risultati';
      altri.onclick = () => { altri.remove(); carica(fonte, pagina + 1); }; lista.appendChild(altri);
    }
  } catch(e) {
    if (q !== ricerca) return;
    attesa.innerHTML = `<span class="errore">${esc(e.message)}</span> `;
    const ancora = document.createElement('button'); ancora.className = 'btn sm'; ancora.textContent = 'Riprova';
    ancora.onclick = () => { attesa.remove(); carica(fonte, pagina); }; attesa.appendChild(ancora);
  }
}
function riga(b){
  brani.set(b.id, b);
  const info = [b.artista, b.album, b.anno, durata(b.durata)].filter(Boolean).map(esc).join(' · ');
  const img = b.immagine ? `<img src="${esc(b.immagine)}" alt="" loading="lazy" onerror="this.outerHTML='<div class=ph>♪</div>'">`
    : '<div class="ph">♪</div>';
  const lic = b.licenza ? `<a class="lic ${b.uso}" href="${esc(b.licenza_url)}" target="_blank" rel="noopener"
    title="Licenza: apri la spiegazione">${esc(b.licenza)}</a>` : '';
  const pag = b.pagina ? ` · <a href="${esc(b.pagina)}" target="_blank" rel="noopener">${esc(b.fonte)}</a>` : '';
  if (b.tipo === 'album') return `<div class="r" data-id="${esc(b.id)}">${img}<div class="tx"><b>${esc(b.titolo)}</b>
    <span>${lic}${info}${pag}</span></div><div class="az"><button class="btn sm album" data-ia="${esc(b.ia)}">Brani ▾</button></div></div>`;
  return `<div class="r" data-id="${esc(b.id)}">${img}<div class="tx"><b>${esc(b.titolo)}</b><span>${lic}${info}${pag}</span>
    <div class="barra" hidden><i></i></div></div><div class="az"><span class="stato"></span>
    <button class="tondo ascolta" title="Ascolta">▶</button>
    <button class="tondo scarica" title="Scarica">⬇</button></div></div>`;
}
async function apriAlbum(bottone, ident){
  const r = bottone.closest('.r');
  if (r.nextElementSibling && r.nextElementSibling.classList.contains('brani')) {
    r.nextElementSibling.remove(); bottone.textContent = 'Brani ▾'; return; }
  bottone.disabled = true; bottone.innerHTML = '<span class="giro"></span>';
  const box = document.createElement('div'); box.className = 'brani'; r.after(box);
  try {
    const d = await api('api/album?id=' + encodeURIComponent(ident));
    box.innerHTML = d.brani.length ? d.brani.map(riga).join('') +
      `<div class="r"><button class="btn sm tutti">Scarica tutto l'album (${d.brani.length} ${d.brani.length === 1 ? 'brano' : 'brani'})</button></div>`
      : '<div class="piccolo">Nessun brano audio in questo album.</div>';
    mostraStati();
  } catch(e) { box.innerHTML = `<div class="errore">${esc(e.message)}</div>`; }
  bottone.disabled = false; bottone.textContent = 'Brani ▴';
}
function ascolta(id){
  const l = $('lettore'), b = brani.get(id);
  document.querySelectorAll('.ascolta.on').forEach(x => { x.classList.remove('on'); x.textContent = '▶'; });
  if (inAscolto === id && !l.paused) { l.pause(); inAscolto = ''; return; }
  inAscolto = id; l.src = b.audio; l.play().catch(() => {});
  document.querySelectorAll(`.r[data-id="${CSS.escape(id)}"] .ascolta`).forEach(x => { x.classList.add('on'); x.textContent = '❚❚'; });
}
$('lettore').onended = () => { document.querySelectorAll('.ascolta.on').forEach(x => { x.classList.remove('on'); x.textContent = '▶'; }); inAscolto = ''; };
async function scarica(id){
  try { await api('api/scarica', brani.get(id)); stati[id] = {stato: 'in coda'}; mostraStati(); segui(); }
  catch(e) { alert(e.message); }
}
async function scaricaTutti(bottone){
  bottone.disabled = true;
  for (const r of bottone.closest('.brani').querySelectorAll('.r[data-id]')) await scarica(r.dataset.id);
}
function segui(){ if (!giro) giro = setInterval(aggiornaScaricamenti, 700); }
async function aggiornaScaricamenti(){
  let d; try { d = await api('api/scaricamenti'); } catch(e) { return; }
  stati = {}; for (const l of [...d.lavori].reverse()) stati[l.brano_id] = l;
  mostraStati();
  const attivi = d.lavori.filter(l => l.stato === 'in coda' || l.stato === 'scarico').length;
  const fatti = d.lavori.filter(l => l.stato === 'fatto').length, errori = d.lavori.filter(l => l.stato === 'errore');
  $('scaricamenti').innerHTML = d.lavori.length ? `<div class="card"><h2>Scaricamenti <span class="piccolo">
    ${fatti} scaricati${attivi ? ' · ' + attivi + ' in corso' : ''}${errori.length ? ' · ' + errori.length + ' non riusciti' : ''}</span></h2>
    ${errori.slice(0, 5).map(l => `<div class="errore">${esc(l.titolo)}: ${esc(l.errore)}</div>`).join('')}</div>` : '';
  if (!attivi && giro) { clearInterval(giro); giro = null; }
}
function mostraStati(){
  for (const r of document.querySelectorAll('.r[data-id]')) {
    const l = stati[r.dataset.id], s = r.querySelector('.stato'), btn = r.querySelector('.scarica'), barra = r.querySelector('.barra');
    if (!s) continue;
    if (!l) { s.textContent = ''; btn.disabled = false; btn.textContent = '⬇'; barra.hidden = true; continue; }
    const pct = l.totale ? Math.round(l.fatti / l.totale * 100) : 0;
    btn.disabled = l.stato !== 'errore';
    btn.textContent = l.stato === 'fatto' || l.stato === "c'era già" ? '✓' : l.stato === 'errore' ? '↻' : '⬇';
    s.textContent = l.stato === 'scarico' ? (l.totale ? pct + '%' : '…') : l.stato === 'in coda' ? 'in coda' : '';
    s.title = l.errore || l.file || '';
    barra.hidden = l.stato !== 'scarico'; barra.firstElementChild.style.width = pct + '%';
    if (l.stato === 'errore') { s.textContent = 'errore'; s.style.color = 'var(--bad)'; } else s.style.color = '';
  }
}
// un solo gestore per i pulsanti dei risultati: il brano si trova dalla riga (data-id), mai scritto nel codice
$('risultati').onclick = e => {
  const b = e.target.closest('button'); if (!b || b.disabled) return;
  const id = b.closest('.r') && b.closest('.r').dataset.id;
  if (b.classList.contains('ascolta')) ascolta(id);
  else if (b.classList.contains('scarica')) scarica(id);
  else if (b.classList.contains('album')) apriAlbum(b, b.dataset.ia);
  else if (b.classList.contains('tutti')) scaricaTutti(b);
};
$('apri').onclick = async e => { e.preventDefault(); try { await api('api/apri', {}); } catch(x) { alert(x.message); } };
$('esci').onclick = async () => {
  if (!confirm('Chiudere il programma? (Gli scaricamenti in corso si fermano.)')) return;
  try { await api('api/esci', {}); } catch(e) {}
  document.body.innerHTML = '<div class="wrap"><h1>Programma chiuso</h1><p class="sub">Puoi chiudere questa pagina.</p></div>';
};
inizio().catch(e => document.body.insertAdjacentHTML('afterbegin', `<p class="errore">${esc(e.message)}</p>`));
</script>
</body>
</html>
"""

NOTE_FONTI = {
    "archivio": "Album, concerti ed etichette indipendenti (netlabel) con licenza libera",
    "ccmixter": "Brani e remix della comunità ccMixter",
    "wikimedia": "Registrazioni libere, molta musica classica di pubblico dominio (spesso in formato .ogg)",
    "openverse": "I brani di Jamendo tramite il motore di ricerca Openverse: a volte lento o non risponde",
    "jamendo": "Serve il client_id gratuito di devportal.jamendo.com nella variabile JAMENDO_CLIENT_ID",
}


def apri_cartella(percorso):
    os.makedirs(percorso, exist_ok=True)
    if platform.system() == "Darwin":
        subprocess.Popen(["open", percorso])
    elif platform.system() == "Windows":
        os.startfile(percorso)   # noqa: S606 — la cartella dei brani scaricati
    else:
        subprocess.Popen(["xdg-open", percorso])


def crea_gestore(coda, token, porta_ref, chiudi):
    class Gestore(BaseHTTPRequestHandler):
        server_version = "CercaMusica/" + VERSIONE

        def log_message(self, *a):
            pass

        def _json(self, codice, dati):
            corpo = json.dumps(dati, ensure_ascii=False).encode("utf-8")
            self.send_response(codice)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(corpo)

        def _controlla(self):
            # solo «127.0.0.1:porta» o «localhost:porta», e la chiave della pagina per le API: un sito esterno
            # aperto nel browser non può usare il programma
            host = (self.headers.get("Host") or "").lower()
            if host not in ("127.0.0.1:%d" % porta_ref[0], "localhost:%d" % porta_ref[0]):
                self._json(403, {"errore": "indirizzo non permesso"})
                return False
            if self.path.startswith("/api/") and not secrets.compare_digest(self.headers.get("X-Token") or "", token):
                self._json(403, {"errore": "chiave mancante: riapri la pagina dal programma"})
                return False
            return True

        def do_GET(self):
            if not self._controlla():
                return
            u = urlsplit(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if u.path == "/":
                    corpo = PAGINA.replace("__TOKEN__", html.escape(token)).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(corpo)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; "
                                     "script-src 'unsafe-inline'; img-src 'self' https: data:; media-src https:; "
                                     "frame-ancestors 'none'")
                    self.end_headers()
                    self.wfile.write(corpo)
                elif u.path == "/api/fonti":
                    attive = fonti_attive()
                    self._json(200, {"cartella": coda.cartella, "fonti": [
                        {"id": f, "nome": n, "attiva": f in attive, "nota": NOTE_FONTI[f]} for f, n in FONTI.items()]})
                elif u.path == "/api/cerca":
                    self._json(200, cerca(q.get("fonte", ""), q.get("q", ""), q.get("pagina", "1")))
                elif u.path == "/api/album":
                    self._json(200, {"brani": brani_archivio(q.get("id", ""))})
                elif u.path == "/api/scaricamenti":
                    self._json(200, {"lavori": coda.elenco()})
                else:
                    self._json(404, {"errore": "pagina inesistente"})
            except (ErroreFonte, ValueError) as e:
                self._json(502 if isinstance(e, ErroreFonte) else 400, {"errore": str(e)})

        def do_POST(self):
            if not self._controlla():
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                d = json.loads(self.rfile.read(min(n, 1024 * 1024)) or b"{}")
                p = urlsplit(self.path).path
                if p == "/api/scarica":
                    campi = ("id", "tipo", "fonte", "titolo", "artista", "album", "traccia", "durata", "licenza",
                             "licenza_url", "pagina", "audio", "est", "immagine")
                    brano = {k: d.get(k) for k in campi if d.get(k) is not None}
                    if brano.get("est") not in ESTENSIONI:
                        brano["est"] = ".mp3"
                    self._json(200, {"id": coda.aggiungi(brano)})
                elif p == "/api/apri":
                    apri_cartella(coda.cartella)
                    self._json(200, {"ok": True})
                elif p == "/api/esci":
                    coda.ferma.set()
                    self._json(200, {"ok": True})
                    threading.Thread(target=chiudi, daemon=True).start()
                else:
                    self._json(404, {"errore": "pagina inesistente"})
            except (ErroreFonte, ValueError, OSError) as e:
                self._json(400, {"errore": str(e)})

    return Gestore


def avvia_pagina(cartella, porta=8765, apri=True):
    coda, token, porta_ref = Coda(cartella), secrets.token_urlsafe(18), [0]
    server = None

    def chiudi():
        time.sleep(0.3)
        server.shutdown()

    for p in (porta, 0):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), crea_gestore(coda, token, porta_ref, chiudi))
            break
        except OSError:
            continue   # porta occupata: se ne prende una libera
    porta_ref[0] = server.server_address[1]
    url = "http://127.0.0.1:%d/" % porta_ref[0]
    print("Cerca musica è aperto nel browser: %s" % url)
    print("I brani vanno in: %s" % cartella)
    print("Per chiuderlo: il pulsante «Chiudi» nella pagina, oppure Ctrl+C qui.", flush=True)
    if apri:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        coda.ferma.set()
        server.server_close()
    print("Programma chiuso.")


# ---------- dal Terminale ----------

def _riga_testo(n, b):
    info = " · ".join(x for x in (b.get("artista"), b.get("album"), b.get("anno"),
                                  ("%d:%02d" % divmod(b["durata"], 60)) if b.get("durata") else "") if x)
    album = " [album]" if b["tipo"] == "album" else ""
    return "%3d. %s%s\n       %s  (%s, %s)" % (n, b["titolo"], album, info, b.get("licenza", ""), b["fonte"])


def da_terminale(a):
    scelte = [f for f in (a.fonti.split(",") if a.fonti else fonti_attive()) if f in CERCA]
    print("Cerco «%s» in: %s ..." % (a.cerca, ", ".join(FONTI[f] for f in scelte)))
    tutti = []
    with concurrent.futures.ThreadPoolExecutor(len(scelte) or 1) as ex:
        lavori = {ex.submit(cerca, f, a.cerca, a.pagina): f for f in scelte}
        for fut in concurrent.futures.as_completed(lavori):
            f = lavori[fut]
            try:
                risultati = fut.result()["risultati"]
            except ErroreFonte as e:
                print("\n%s: %s" % (FONTI[f], e))
                continue
            print("\n== %s (%d)" % (FONTI[f], len(risultati)))
            for b in risultati:
                tutti.append(b)
                print(_riga_testo(len(tutti), b))
    if not tutti:
        print("\nNessun risultato.")
        return 1
    try:
        scritto = input("\nNumeri da scaricare (es. 1 3 5, oppure 2-6; Invio per uscire): ")
    except EOFError:
        scritto = ""
    numeri = []
    for parte in scritto.replace(",", " ").split():
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", parte)
        if m:
            fine = int(m.group(2) or m.group(1))
            numeri += [n for n in range(int(m.group(1)), fine + 1) if 1 <= n <= len(tutti)]
    errori = 0
    for n in dict.fromkeys(numeri):
        b = tutti[n - 1]
        try:
            brani = brani_archivio(b["ia"]) if b["tipo"] == "album" else [b]
        except ErroreFonte as e:
            print("%s: %s" % (b["titolo"], e))
            errori += 1
            continue
        for x in brani:
            def avanti(fatti, totale, x=x):
                pct = " %3d%%" % (fatti * 100 // totale) if totale else ""
                print("\r  ⬇ %s%s" % (x["titolo"][:60], pct), end="", flush=True)

            try:
                percorso, nuovo = scarica(x, a.cartella, avanti)
                print(("\r  %s %s" % ("✓" if nuovo else "= (c'era già)",
                                      os.path.relpath(percorso, a.cartella))).ljust(70))
            except (ErroreFonte, OSError) as e:
                print("\r  ✗ %s: %s" % (x["titolo"], e))
                errori += 1
    if numeri:
        print("\nI brani sono in: %s (autori e licenze in LICENZE.txt)" % a.cartella)
    return 0 if not errori else 2


def main(argv=None):
    ap = argparse.ArgumentParser(description="Cerca e scarica musica con licenza libera (Creative Commons, pubblico "
                                             "dominio). Senza parole da cercare apre la pagina nel browser.")
    ap.add_argument("cerca", nargs="?", help="le parole da cercare (es. \"jazz piano\")")
    ap.add_argument("--fonti", help="fonti separate da virgola: " + ",".join(FONTI))
    ap.add_argument("--pagina", type=int, default=1, help="pagina dei risultati (20 per fonte)")
    ap.add_argument("--cartella", default=CARTELLA, help="dove salvare i brani (predefinita: %(default)s)")
    ap.add_argument("--porta", type=int, default=8765, help="porta della pagina (predefinita 8765)")
    ap.add_argument("--non-aprire", action="store_true", help="non apre il browser da solo")
    a = ap.parse_args(argv)
    a.cartella = os.path.abspath(os.path.expanduser(a.cartella))
    if a.cerca:
        return da_terminale(a)
    avvia_pagina(a.cartella, a.porta, not a.non_aprire)
    return 0


if __name__ == "__main__":
    sys.exit(main())
