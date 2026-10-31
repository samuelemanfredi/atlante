#!/usr/bin/env python3
"""Copia audio e video: legge una memoria qualsiasi (chiavetta USB, scheda SD, disco esterno, disco del computer,
cartella di rete) e ne copia i file audio e video in un'altra cartella. Funziona senza Internet.

Solo libreria standard di Python 3 (niente da installare). Uso:

    python3 copia_audio_video.py                         apre la pagina del programma nel browser
    python3 copia_audio_video.py --memorie               elenca le memorie collegate
    python3 copia_audio_video.py SORGENTE DESTINAZIONE   copia dal Terminale (opzioni: --help)

Come lavora, in tre passi:
  1. analisi: visita tutte le cartelle della memoria e riconosce i file audio e video dall'estensione
     (.mp3, .mp4, .mov...) e, se un file non ha estensione, dai primi byte del contenuto (la «firma» del formato);
  2. copia: ogni file passa a pezzi da 1 MB in un file nascosto «.parziale», che prende il nome vero solo a copia
     finita e controllata (SHA-256 uguale all'originale): un file a metà non resta mai con il nome giusto;
  3. doppioni: due file con la stessa impronta SHA-256 sono lo stesso file, anche se hanno nomi diversi:
     il secondo non si copia. Rilanciando la stessa copia, quello che c'è già si salta (riprende da dove era rimasta).

La memoria di origine si legge soltanto: il programma non cambia e non cancella mai niente lì.
"""
import argparse
import errno
import hashlib
import html
import json
import os
import platform
import secrets
import shutil
import stat
import struct
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

VERSIONE = "1.0"

AUDIO = {".mp3", ".m4a", ".m4b", ".m4p", ".aac", ".flac", ".wav", ".aif", ".aiff", ".aifc", ".ogg", ".oga", ".opus",
         ".wma", ".alac", ".ape", ".wv", ".amr", ".3ga", ".awb", ".mid", ".midi", ".dsf", ".dff", ".caf", ".ac3",
         ".mka", ".au", ".ra", ".mpc", ".spx", ".weba"}
VIDEO = {".mp4", ".m4v", ".mov", ".qt", ".avi", ".mkv", ".wmv", ".asf", ".flv", ".f4v", ".webm", ".mpg", ".mpeg",
         ".mpe", ".m2v", ".m2ts", ".mts", ".ts", ".vob", ".3gp", ".3g2", ".ogv", ".rm", ".rmvb", ".divx", ".dv",
         ".mxf", ".insv", ".lrv", ".mod", ".tod"}
# cartelle di sistema che non contengono musica né filmati di chi usa la memoria
SALTA = {".Trashes", ".Spotlight-V100", ".fseventsd", ".DocumentRevisions-V100", ".TemporaryItems", "$RECYCLE.BIN",
         "System Volume Information", "lost+found", "$Recycle.Bin", "Recovery", "node_modules"}
# alla radice del disco del computer: le cartelle del sistema operativo
SALTA_RADICE = {"System", "Library", "private", "usr", "bin", "sbin", "dev", "proc", "sys", "run", "snap", "boot",
                "cores", "opt", "etc", "var", "tmp", "Volumes", "media", "mnt", "Windows", "Program Files",
                "Program Files (x86)", "ProgramData", "Applications"}
BLOCCO = 1024 * 1024           # la copia passa a pezzi da 1 MB
TOLLERANZA_DATA = 2            # secondi: le chiavette FAT salvano l'ora con 2 s di precisione
SECONDI_1904 = 2082844800      # MP4 e MOV contano i secondi dal 1904, il computer dal 1970


# ---------- riconoscere i file ----------

def tipo_da_nome(nome):
    """'audio', 'video' o None, dall'estensione del nome."""
    est = os.path.splitext(nome)[1].lower()
    if est in AUDIO:
        return "audio"
    if est in VIDEO:
        return "video"
    return None


def tipo_da_contenuto(percorso):
    """(tipo, estensione) letti dai primi byte del file (la «firma» del formato), o (None, '')."""
    try:
        with open(percorso, "rb") as f:
            b = f.read(512)
    except OSError:
        return None, ""
    if len(b) < 12:
        return None, ""
    if b[:3] == b"ID3":
        return "audio", ".mp3"
    if b[:4] == b"fLaC":
        return "audio", ".flac"
    if b[:4] == b"OggS":
        return ("video", ".ogv") if b"theora" in b[:200] else ("audio", ".ogg")
    if b[:4] == b"RIFF" and b[8:12] == b"WAVE":
        return "audio", ".wav"
    if b[:4] == b"RIFF" and b[8:12] == b"AVI ":
        return "video", ".avi"
    if b[:4] == b"FORM" and b[8:12] in (b"AIFF", b"AIFC"):
        return "audio", ".aiff"
    if b[4:8] == b"ftyp":
        marca = b[8:12]
        if marca in (b"M4A ", b"M4B ", b"M4P ", b"F4A "):
            return "audio", ".m4a"
        if marca == b"qt  ":
            return "video", ".mov"
        if marca[:3] == b"3gp" or marca[:3] == b"3g2":
            return "video", ".3gp"
        return "video", ".mp4"
    if b[:4] == b"\x1a\x45\xdf\xa3":
        return "video", ".webm" if b"webm" in b[:64] else ".mkv"
    if b[:16] == b"\x30\x26\xb2\x75\x8e\x66\xcf\x11\xa6\xd9\x00\xaa\x00\x62\xce\x6c":
        return "video", ".wmv"
    if b[:4] in (b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"):
        return "video", ".mpg"
    if b[:3] == b"FLV":
        return "video", ".flv"
    if b[:4] == b"caff":
        return "audio", ".caf"
    if b[:4] == b"MThd":
        return "audio", ".mid"
    if b[:6] == b"#!AMR\n":
        return "audio", ".amr"
    if len(b) > 376 and b[0] == 0x47 and b[188] == 0x47 and b[376] == 0x47:
        return "video", ".ts"
    if b[0] == 0xFF and (b[1] & 0xF6) in (0xF2, 0xF4) and (b[2] & 0xF0) not in (0x00, 0xF0):
        return "audio", ".mp3"   # MP3 senza tag: inizia subito con un fotogramma audio
    return None, ""


def data_ripresa(percorso, mtime):
    """La data di un filmato MP4/MOV/3GP scritta dalla videocamera o dal telefono (atomo mvhd); se manca, mtime."""
    if os.path.splitext(percorso)[1].lower() not in (".mp4", ".m4v", ".mov", ".qt", ".3gp", ".3g2", ".insv", ".lrv"):
        return mtime
    try:
        with open(percorso, "rb") as f:
            t = _mvhd(f, 0, os.fstat(f.fileno()).st_size, 0)
    except (OSError, struct.error, IndexError):
        t = None
    return t or mtime


def _mvhd(f, inizio, fine, livello):
    pos, giri = inizio, 0
    while pos + 8 <= fine and giri < 500:
        giri += 1
        f.seek(pos)
        h = f.read(16)
        if len(h) < 8:
            return None
        dim, nome = struct.unpack(">I4s", h[:8])
        testa = 8
        if dim == 1 and len(h) == 16:
            dim, testa = struct.unpack(">Q", h[8:16])[0], 16
        elif dim == 0:
            dim = fine - pos
        if dim < testa:
            return None
        if nome == b"moov" and livello == 0:
            return _mvhd(f, pos + testa, pos + dim, 1)
        if nome == b"mvhd" and livello == 1:
            f.seek(pos + testa)
            d = f.read(12)
            sec = struct.unpack(">Q", d[4:12])[0] if d[0] == 1 else struct.unpack(">I", d[4:8])[0]
            t = sec - SECONDI_1904
            return t if 315532800 < t < time.time() + 86400 else None   # tra il 1980 e domani
        pos += dim
    return None


# ---------- memorie e cartelle ----------

def _spazio(percorso):
    try:
        u = shutil.disk_usage(percorso)
        return u.total, u.free
    except OSError:
        return 0, 0


def memorie():
    """Le memorie collegate: [{nome, percorso, totale, libero, tipo}] (tipo: casa, computer, esterna)."""
    casa = os.path.expanduser("~")
    elenco = [{"nome": "La mia cartella (%s)" % os.path.basename(casa), "percorso": casa, "tipo": "casa"}]
    sistema = platform.system()
    if sistema == "Windows":
        lettere = os.listdrives() if hasattr(os, "listdrives") else [
            "%s:\\" % c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists("%s:\\" % c)]
        for d in lettere:
            elenco.append({"nome": "Disco %s" % d.rstrip("\\"), "percorso": d,
                           "tipo": "computer" if d.upper().startswith("C:") else "esterna"})
    else:
        elenco.append({"nome": "Tutto il disco del computer", "percorso": "/", "tipo": "computer"})
        utente = os.environ.get("USER") or os.path.basename(casa)
        basi = ["/Volumes"] if sistema == "Darwin" else ["/media/" + utente, "/run/media/" + utente, "/media", "/mnt"]
        visti = set()
        for base in basi:
            try:
                voci = sorted(os.scandir(base), key=lambda e: e.name.casefold())
            except OSError:
                continue
            for e in voci:
                if e.name.startswith(".") or not e.is_dir():
                    continue
                vero = os.path.realpath(e.path)
                if vero == "/" or vero in visti or (base in ("/media", "/mnt") and vero.startswith("/media/" + utente)):
                    continue   # «Macintosh HD» in /Volumes è il disco del computer, già in elenco
                visti.add(vero)
                elenco.append({"nome": e.name, "percorso": e.path, "tipo": "esterna"})
    for m in elenco:
        m["totale"], m["libero"] = _spazio(m["percorso"])
    return elenco


def sottocartelle(percorso):
    """Le cartelle dentro `percorso` (senza quelle nascoste), per scegliere dove leggere o copiare."""
    percorso = os.path.abspath(os.path.expanduser(percorso or "~"))
    cartelle = []
    with os.scandir(percorso) as voci:
        for e in voci:
            try:
                if not e.name.startswith(".") and e.name not in SALTA and e.is_dir():
                    cartelle.append({"nome": e.name, "percorso": e.path})
            except OSError:
                pass
    cartelle.sort(key=lambda c: c["nome"].casefold())
    su = os.path.dirname(percorso)
    return {"percorso": percorso, "su": su if su != percorso else "", "cartelle": cartelle,
            "libero": _spazio(percorso)[1]}


def nome_memoria(sorgente):
    """Il nome della cartella che riceve i file nel modo «stesse cartelle»: il nome della memoria."""
    nome = os.path.basename(os.path.normpath(sorgente))
    if not nome:
        nome = "Memoria " + sorgente.strip("\\/:") if sorgente.strip("\\/:") else "Disco del computer"
    return nome


def _dentro(figlio, padre):
    figlio, padre = os.path.realpath(figlio), os.path.realpath(padre)
    return figlio == padre or figlio.startswith(padre.rstrip(os.sep) + os.sep)


# ---------- 1. analisi ----------

class Fermato(Exception):
    pass


def analizza(sorgente, audio=True, video=True, min_byte=0, tutti=False, escludi=(), ferma=None, avanzamento=None):
    """Tutti i file audio/video sotto `sorgente`. Restituisce (file, problemi):
    file = [{percorso, rel, byte, mtime, tipo, est}] (est: estensione da aggiungere se il nome non ne ha una),
    problemi = [(percorso, motivo)] delle cartelle che non si sono potute leggere.
    tutti=True legge la firma di ogni file con un'estensione sconosciuta (più lento); altrimenti solo di quelli
    senza estensione."""
    sorgente = os.path.abspath(os.path.expanduser(sorgente))
    if not os.path.isdir(sorgente):
        raise FileNotFoundError("La memoria o cartella non esiste: %s" % sorgente)
    tipi = {t for t, si in (("audio", audio), ("video", video)) if si}
    escludi = [os.path.realpath(e) for e in escludi if e]
    radice = os.path.dirname(sorgente) == sorgente or (len(sorgente) <= 3 and sorgente[1:2] == ":")
    file, problemi, cartelle = [], [], 0

    def errore(e):
        problemi.append((getattr(e, "filename", "") or "", e.strerror or str(e)))

    for cartella, sotto, nomi in os.walk(sorgente, onerror=errore):
        if ferma is not None and ferma.is_set():
            raise Fermato()
        cartelle += 1
        tenute = []
        for d in sotto:
            if d.startswith(".") or d in SALTA or (radice and cartella == sorgente and d in SALTA_RADICE):
                continue
            pieno = os.path.join(cartella, d)
            if any(_dentro(pieno, e) for e in escludi):
                continue   # la cartella di destinazione dentro la memoria: non si ricopia in sé stessa
            tenute.append(d)
        sotto[:] = sorted(tenute, key=str.casefold)
        # per nome senza estensione, come il Finder: «uno.mp3» prima di «uno - Copia.mp3» (tra due file uguali
        # resta quello con il nome originale)
        for n in sorted(nomi, key=lambda n: (os.path.splitext(n)[0].casefold(), n.casefold())):
            if n.startswith("."):
                continue   # anche «._canzone.mp3»: i dati nascosti che il Mac scrive sulle chiavette
            p = os.path.join(cartella, n)
            tipo, est = tipo_da_nome(n), ""
            if tipo is None:
                ha_est = bool(os.path.splitext(n)[1])
                if ha_est and not tutti:
                    continue
                tipo, est = tipo_da_contenuto(p)
                if tipo is None:
                    continue
            if tipo not in tipi:
                continue
            try:
                s = os.stat(p, follow_symlinks=False)
            except OSError as e:
                errore(e)
                continue
            if not stat.S_ISREG(s.st_mode) or s.st_size < max(min_byte, 1):
                continue
            file.append({"percorso": p, "rel": os.path.relpath(p, sorgente), "byte": s.st_size,
                         "mtime": s.st_mtime, "tipo": tipo, "est": est})
        if avanzamento:
            avanzamento(cartelle, file, cartella)
    return file, problemi


def riepilogo(file):
    """Quanti file e byte per tipo e per estensione."""
    r = {"audio": {"n": 0, "byte": 0}, "video": {"n": 0, "byte": 0}, "formati": {}}
    for f in file:
        r[f["tipo"]]["n"] += 1
        r[f["tipo"]]["byte"] += f["byte"]
        est = (f["est"] or os.path.splitext(f["rel"])[1]).lower().lstrip(".") or "?"
        x = r["formati"].setdefault(est, {"tipo": f["tipo"], "n": 0, "byte": 0})
        x["n"] += 1
        x["byte"] += f["byte"]
    return r


# ---------- 2. copia ----------

def impronta(percorso, ferma=None):
    """SHA-256 del file: l'«impronta digitale» del contenuto."""
    h = hashlib.sha256()
    with open(percorso, "rb") as f:
        while True:
            if ferma is not None and ferma.is_set():
                raise Fermato()
            b = f.read(BLOCCO)
            if not b:
                return h.hexdigest()
            h.update(b)


class Doppioni:
    """Ricorda i file audio/video già nella destinazione per dimensione; l'impronta si calcola solo quando la
    dimensione coincide (due file di dimensione diversa non possono essere uguali)."""

    def __init__(self, ferma=None):
        self.per_byte, self.impronte, self.ferma = {}, {}, ferma

    def leggi_cartella(self, cartella):
        for dove, sotto, nomi in os.walk(cartella):
            sotto[:] = [d for d in sotto if not d.startswith(".")]
            for n in nomi:
                if n.startswith(".") or (tipo_da_nome(n) is None and os.path.splitext(n)[1]):
                    continue
                p = os.path.join(dove, n)
                try:
                    self.aggiungi(p, os.path.getsize(p))
                except OSError:
                    pass

    def aggiungi(self, percorso, byte, sha=None):
        self.per_byte.setdefault(byte, []).append(percorso)
        if sha:
            self.impronte[percorso] = sha

    def _sha(self, percorso):
        if percorso not in self.impronte:
            self.impronte[percorso] = impronta(percorso, self.ferma)
        return self.impronte[percorso]

    def cerca(self, percorso, byte):
        """Il file della destinazione uguale a `percorso`, o None."""
        uguali = self.per_byte.get(byte)
        if not uguali:
            return None
        sha = self._sha(percorso)
        for altro in uguali:
            try:
                if self._sha(altro) == sha:
                    return altro
            except OSError:
                pass
        return None


def posto_per(origine, arrivo):
    """Dove scrivere la copia: (percorso, True) se c'è già la copia di `origine` («canzone.mp3» o «canzone (2).mp3»);
    altrimenti (primo nome libero, False). Così rilanciare la stessa copia non crea «canzone (3).mp3»."""
    base, est = os.path.splitext(arrivo)
    candidato, n = arrivo, 1
    while os.path.exists(candidato):
        if stessa_copia(origine, candidato):
            return candidato, True
        n += 1
        candidato = "%s (%d)%s" % (base, n, est)
    return candidato, False


def dove_copiare(f, destinazione, modo, nome_sorgente):
    """Il percorso nella destinazione: «cartelle» = come nella memoria, «data» = Audio|Video/anno/anno-mese/."""
    if modo == "data":
        t = time.localtime(data_ripresa(f["percorso"], f["mtime"]))
        cartella = os.path.join(destinazione, "Audio" if f["tipo"] == "audio" else "Video",
                                time.strftime("%Y", t), time.strftime("%Y-%m", t))
        return os.path.join(cartella, os.path.basename(f["rel"]) + f["est"])
    return os.path.join(destinazione, nome_sorgente, f["rel"] + f["est"])


def stessa_copia(origine, copia):
    """Il file `copia` è già la copia di `origine`? Stessa dimensione e stessa data (come la lascia questo programma)."""
    try:
        a, b = os.stat(origine), os.stat(copia)
    except OSError:
        return False
    return a.st_size == b.st_size and abs(a.st_mtime - b.st_mtime) <= TOLLERANZA_DATA


def copia_file(origine, arrivo, verifica=True, ferma=None, avanzamento=None):
    """Copia a pezzi in «.nome.parziale», controlla l'impronta e solo allora dà il nome vero. Restituisce lo SHA-256."""
    os.makedirs(os.path.dirname(arrivo), exist_ok=True)
    parziale = os.path.join(os.path.dirname(arrivo), "." + os.path.basename(arrivo) + ".parziale")
    h = hashlib.sha256()
    try:
        with open(origine, "rb") as a, open(parziale, "wb") as b:
            while True:
                if ferma is not None and ferma.is_set():
                    raise Fermato()
                pezzo = a.read(BLOCCO)
                if not pezzo:
                    break
                h.update(pezzo)
                b.write(pezzo)
                if avanzamento:
                    avanzamento(len(pezzo))
            b.flush()
            os.fsync(b.fileno())   # i dati sono davvero sulla memoria (si può staccare la chiavetta dopo la fine)
        sha = h.hexdigest()
        if verifica and impronta(parziale, ferma) != sha:
            raise OSError(errno.EIO, "la copia non è uguale all'originale (memoria danneggiata?)")
        try:
            shutil.copystat(origine, parziale)   # stessa data dell'originale
        except OSError:
            pass
        os.replace(parziale, arrivo)
        return sha
    except BaseException:
        try:
            os.remove(parziale)
        except OSError:
            pass
        raise


def copia(file, sorgente, destinazione, modo="cartelle", doppioni=False, verifica=True, ferma=None,
          avanzamento=None):
    """Copia l'elenco di `analizza`. doppioni=False: un file che la destinazione ha già (stessa impronta, anche con
    un altro nome) non si copia. Restituisce il resoconto {copiati, doppioni, gia, errori:[...], byte_copiati}."""
    destinazione = os.path.abspath(os.path.expanduser(destinazione))
    os.makedirs(destinazione, exist_ok=True)
    nome_sorgente = nome_memoria(sorgente)
    indice = None
    if not doppioni:
        indice = Doppioni(ferma)
        indice.leggi_cartella(destinazione)
    r = {"copiati": 0, "doppioni": 0, "gia": 0, "errori": [], "byte_copiati": 0, "fatti": 0, "byte_fatti": 0,
         "elenco_doppioni": []}

    def passo(n=0, f=None, stato=None):
        if avanzamento:
            avanzamento(r, f, n, stato)

    for f in file:
        if ferma is not None and ferma.is_set():
            raise Fermato()
        prima = r["byte_fatti"]
        passo(f=f, stato="inizio")
        try:
            arrivo, gia = posto_per(f["percorso"], dove_copiare(f, destinazione, modo, nome_sorgente))
            uguale = None if gia or not indice else indice.cerca(f["percorso"], f["byte"])
            if gia:
                r["gia"] += 1
            elif uguale:
                r["doppioni"] += 1
                r["elenco_doppioni"].append((f["rel"], os.path.relpath(uguale, destinazione)))
            else:
                def scritti(n):
                    r["byte_copiati"] += n
                    r["byte_fatti"] += n
                    passo(n, f)

                sha = copia_file(f["percorso"], arrivo, verifica, ferma, scritti)
                r["copiati"] += 1
                if indice:
                    indice.aggiungi(arrivo, f["byte"], sha)
        except Fermato:
            raise
        except OSError as e:
            if e.errno == errno.ENOSPC:
                raise OSError(errno.ENOSPC, "La destinazione è piena: libera spazio o scegli un altro disco")
            if not os.path.isdir(sorgente):
                raise OSError(errno.ENODEV, "La memoria è stata scollegata durante la copia")
            r["errori"].append((f["rel"], e.strerror or str(e)))
        r["fatti"] += 1
        r["byte_fatti"] = prima + f["byte"]   # il file conta una volta, copiato, saltato o non riuscito
        passo(f=f, stato="fine")
    return r


def scrivi_rapporto(destinazione, sorgente, file, r, problemi=(), fermato=False):
    """«Rapporto della copia AAAA-MM-GG HH.MM.txt» nella destinazione: cosa è stato copiato e cosa no."""
    nome = time.strftime("Rapporto della copia %Y-%m-%d %H.%M.txt")
    righe = ["Copia audio e video %s — %s" % (VERSIONE, time.strftime("%d/%m/%Y %H:%M")),
             "Da: %s" % sorgente, "A:  %s" % destinazione, "",
             "File trovati:          %d (%s)" % (len(file), misura(sum(f["byte"] for f in file))),
             "Copiati:               %d (%s)" % (r["copiati"], misura(r["byte_copiati"])),
             "Già presenti:          %d" % r["gia"],
             "Doppioni non copiati:  %d" % r["doppioni"],
             "Errori:                %d" % len(r["errori"])]
    if fermato:
        righe += ["", "La copia è stata fermata prima della fine: rilanciala e riprende da dove era rimasta."]
    if r["errori"]:
        righe += ["", "File non copiati (errore):"] + ["  %s — %s" % e for e in r["errori"]]
    if problemi:
        righe += ["", "Cartelle che non si sono potute leggere:"] + ["  %s — %s" % p for p in problemi]
    if r["elenco_doppioni"]:
        righe += ["", "Doppioni (il contenuto c'era già, con questo nome):"] + \
                 ["  %s  =  %s" % d for d in r["elenco_doppioni"]]
    percorso = os.path.join(destinazione, nome)
    try:
        with open(percorso, "w", encoding="utf-8") as fh:
            fh.write("\n".join(righe) + "\n")
        return percorso
    except OSError:
        return ""


def misura(n):
    """1536 → «1,5 KB» (all'italiana, con la virgola)."""
    n = float(n)
    for unita in ("byte", "KB", "MB", "GB"):
        if n < 1024:
            return ("%d %s" % (n, unita)) if unita == "byte" else ("%.1f %s" % (n, unita)).replace(".", ",")
        n /= 1024
    return ("%.2f TB" % n).replace(".", ",")


# ---------- il lavoro in sottofondo (per la pagina) ----------

class Lavoro:
    """Un'analisi o una copia alla volta, in un thread; la pagina legge `fotografia()` ogni mezzo secondo."""

    def __init__(self):
        self.lock = threading.Lock()
        self.ferma = threading.Event()
        self.thread = None
        self.file, self.problemi, self.sorgente = [], [], ""
        self.s = {"fase": "pronto", "messaggio": ""}

    def occupato(self):
        return self.thread is not None and self.thread.is_alive()

    def _avvia(self, funzione):
        if self.occupato():
            raise RuntimeError("Il programma sta già lavorando: aspetta o premi Ferma")
        self.ferma.clear()
        self.thread = threading.Thread(target=funzione, daemon=True)
        self.thread.start()

    def avvia_analisi(self, sorgente, audio, video, min_kb, tutti, destinazione=""):
        sorgente = os.path.abspath(os.path.expanduser(sorgente))
        if not os.path.isdir(sorgente):
            raise RuntimeError("La memoria non si trova più: %s" % sorgente)
        if not (audio or video):
            raise RuntimeError("Scegli almeno audio o video")

        def lavora():
            with self.lock:
                self.file, self.problemi, self.sorgente = [], [], sorgente
                self.s = {"fase": "analisi", "sorgente": sorgente, "cartelle": 0, "trovati": 0, "byte": 0,
                          "dove": sorgente, "inizio": time.time()}

            def avanti(cartelle, file, dove):
                with self.lock:
                    self.s.update(cartelle=cartelle, trovati=len(file), dove=dove)

            try:
                file, problemi = analizza(sorgente, audio, video, int(min_kb or 0) * 1024, tutti,
                                          [destinazione] if destinazione else [], self.ferma, avanti)
                with self.lock:
                    self.file, self.problemi = file, problemi
                    self.s = {"fase": "analizzato", "sorgente": sorgente, "riepilogo": riepilogo(file),
                              "esempi": [f["rel"] + f["est"] for f in file[:300]],
                              "problemi": [list(p) for p in problemi[:50]], "n_problemi": len(problemi)}
            except Fermato:
                with self.lock:
                    self.s = {"fase": "pronto", "messaggio": "Ricerca fermata."}
            except Exception as e:   # noqa: BLE001 — qualunque errore va detto nella pagina
                with self.lock:
                    self.s = {"fase": "errore", "messaggio": str(e)}

        self._avvia(lavora)

    def avvia_copia(self, destinazione, modo, doppioni, verifica):
        if not self.file:
            raise RuntimeError("Prima cerca i file nella memoria")
        destinazione = os.path.abspath(os.path.expanduser(destinazione))
        if os.path.realpath(destinazione) == os.path.realpath(self.sorgente):
            raise RuntimeError("La destinazione non può essere la memoria stessa")
        file, sorgente, problemi = list(self.file), self.sorgente, list(self.problemi)
        totale = sum(f["byte"] for f in file)

        def lavora():
            inizio = time.time()
            with self.lock:
                self.s = {"fase": "copia", "sorgente": sorgente, "destinazione": destinazione, "n": len(file),
                          "fatti": 0, "byte": totale, "byte_fatti": 0, "copiati": 0, "doppioni": 0, "gia": 0,
                          "errori": 0, "file": "", "velocita": 0, "inizio": inizio}
            r = None

            def avanti(rr, f, n, stato):
                nonlocal r
                r = rr
                with self.lock:
                    trascorsi = max(time.time() - inizio, 0.001)
                    self.s.update(fatti=rr["fatti"], byte_fatti=rr["byte_fatti"], copiati=rr["copiati"],
                                  doppioni=rr["doppioni"], gia=rr["gia"], errori=len(rr["errori"]),
                                  velocita=rr["byte_copiati"] / trascorsi, file=f["rel"] if f else "")

            fermato = False
            try:
                r = copia(file, sorgente, destinazione, modo, doppioni, verifica, self.ferma, avanti)
            except Fermato:
                fermato = True
            except Exception as e:   # noqa: BLE001
                with self.lock:
                    self.s.update(fase="errore", messaggio=str(e.strerror if isinstance(e, OSError) and e.strerror
                                                                 else e))
                if r:
                    scrivi_rapporto(destinazione, sorgente, file, r, problemi, True)
                return
            r = r or {"copiati": 0, "doppioni": 0, "gia": 0, "errori": [], "byte_copiati": 0, "elenco_doppioni": []}
            rapporto = scrivi_rapporto(destinazione, sorgente, file, r, problemi, fermato)
            with self.lock:
                self.s.update(fase="fine", fermato=fermato, copiati=r["copiati"], doppioni=r["doppioni"],
                              gia=r["gia"], errori=len(r["errori"]), byte_copiati=r["byte_copiati"],
                              elenco_errori=[list(e) for e in r["errori"][:100]], rapporto=rapporto,
                              secondi=time.time() - inizio, file="")

        self._avvia(lavora)

    def fotografia(self):
        with self.lock:
            return dict(self.s)


# ---------- la pagina (server solo su questo computer) ----------

PAGINA = r"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="token" content="__TOKEN__">
<title>Copia audio e video</title>
<style>
:root{--bg:#f2f2f7;--card:#fff;--fg:#1c1c1e;--fg2:#6e6e73;--line:#e5e5ea;--fill:#e9e9ee;--accent:#0a66d8;
  --onaccent:#fff;--ok:#1f8a3b;--bad:#c62828;--warn:#a15c00;color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{--bg:#000;--card:#1c1c1e;--fg:#f2f2f7;--fg2:#a1a1a6;--line:#38383a;
  --fill:#2c2c2e;--accent:#4c9aff;--onaccent:#00122b;--ok:#4cd964;--bad:#ff6b6b;--warn:#ffb340}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,
  Helvetica,Arial,sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:28px;margin:6px 0 2px}
.sub{color:var(--fg2);margin:0 0 18px}
.card{background:var(--card);border-radius:14px;padding:16px;margin:0 0 14px;border:1px solid var(--line)}
.card h2{font-size:18px;margin:0 0 10px;display:flex;gap:10px;align-items:center}
.num{display:inline-flex;width:26px;height:26px;border-radius:50%;background:var(--accent);color:var(--onaccent);
  align-items:center;justify-content:center;font-size:14px;flex:none}
.card.spenta{opacity:.5;pointer-events:none}
.mem{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:8px}
.mem button{all:unset;cursor:pointer;border:2px solid var(--line);border-radius:12px;padding:10px 12px;display:block}
.mem button:hover{border-color:var(--fg2)}
.mem button.on{border-color:var(--accent);background:color-mix(in srgb,var(--accent) 10%,transparent)}
.mem b{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mem small,.piccolo{color:var(--fg2);font-size:13px}
.btn{appearance:none;border:0;border-radius:10px;padding:10px 16px;font:inherit;font-weight:600;cursor:pointer;
  background:var(--fill);color:var(--accent)}
.btn.primo{background:var(--accent);color:var(--onaccent)}
.btn:disabled{opacity:.45;cursor:default}
.riga{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:10px 0 0}
.percorso{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px;background:var(--fill);border-radius:8px;
  padding:8px 10px;word-break:break-all;flex:1;min-width:200px}
label.sc{display:flex;gap:10px;align-items:flex-start;margin:8px 0;cursor:pointer}
label.sc input{width:20px;height:20px;margin:2px 0 0;flex:none;accent-color:var(--accent)}
.barra{height:10px;border-radius:5px;background:var(--fill);overflow:hidden;margin:10px 0 6px}
.barra i{display:block;height:100%;width:0;background:var(--accent);transition:width .3s}
.dati{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin:10px 0}
.dato{background:var(--fill);border-radius:10px;padding:8px 10px}
.dato b{display:block;font-size:20px}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
details{margin:8px 0}summary{cursor:pointer;color:var(--accent)}
ul.lista{margin:6px 0;padding-left:18px;max-height:260px;overflow:auto;font-size:13px;color:var(--fg2)}
table{border-collapse:collapse;width:100%;font-size:14px;margin:8px 0}
td,th{text-align:left;padding:4px 6px;border-bottom:1px solid var(--line)}th{color:var(--fg2);font-weight:500}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.file{font-size:13px;color:var(--fg2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
dialog{border:0;border-radius:14px;padding:0;width:min(560px,94vw);background:var(--card);color:var(--fg)}
dialog::backdrop{background:rgba(0,0,0,.4)}
.dtesta{padding:14px 16px;border-bottom:1px solid var(--line)}
.dtesta b{display:block;font-size:17px}
.dlista{max-height:52vh;overflow:auto;padding:6px 8px}
.dlista button{all:unset;display:block;width:100%;box-sizing:border-box;padding:9px 10px;border-radius:8px;cursor:pointer}
.dlista button:hover{background:var(--fill)}
.dpiede{padding:12px 16px;border-top:1px solid var(--line);display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}
.avviso{border-radius:10px;padding:10px 12px;margin:10px 0;background:color-mix(in srgb,var(--warn) 14%,transparent)}
.errore{border-radius:10px;padding:10px 12px;margin:10px 0;background:color-mix(in srgb,var(--bad) 14%,transparent)}
.testa{display:flex;gap:12px;align-items:flex-start;justify-content:space-between}
.testa .btn{margin-top:8px;flex:none}
</style>
</head>
<body>
<div class="wrap">
  <div class="testa"><h1>Copia audio e video</h1>
    <button class="btn" id="esci" title="Chiude il programma">Chiudi</button></div>
  <p class="sub">Legge una memoria (chiavetta, scheda, disco) e ne copia musica, registrazioni e filmati. Funziona senza
    Internet e non cambia niente nella memoria di origine.</p>

  <section class="card" id="c1">
    <h2><span class="num">1</span> Da quale memoria?</h2>
    <div class="mem" id="memorie"></div>
    <div class="riga"><button class="btn" id="aggiorna">Aggiorna l'elenco</button>
      <button class="btn" id="sfoglia1">Scegli una cartella…</button></div>
    <div class="riga"><span class="percorso" id="sorgente">Nessuna memoria scelta</span></div>
  </section>

  <section class="card spenta" id="c2">
    <h2><span class="num">2</span> Cosa cerco?</h2>
    <label class="sc"><input type="checkbox" id="audio" checked><span><b>Audio</b><br>
      <span class="piccolo">musica, registrazioni, messaggi vocali (mp3, m4a, flac, wav, ogg…)</span></span></label>
    <label class="sc"><input type="checkbox" id="video" checked><span><b>Video</b><br>
      <span class="piccolo">filmati del telefono e della videocamera, film (mp4, mov, avi, mkv…)</span></span></label>
    <label class="sc"><input type="checkbox" id="tutti"><span>Controlla il contenuto di tutti i file<br>
      <span class="piccolo">trova anche audio e video con un'estensione sbagliata (più lento; quelli senza estensione si
      riconoscono sempre)</span></span></label>
    <label class="sc"><span>Ignora i file più piccoli di <input type="number" id="minkb" value="0" min="0" step="10"
      style="width:80px;height:auto;font:inherit"> KB</span></label>
    <div class="riga"><button class="btn primo" id="cerca">Cerca i file</button></div>
    <div id="analisi"></div>
  </section>

  <section class="card spenta" id="c3">
    <h2><span class="num">3</span> Dove li copio?</h2>
    <div class="riga"><span class="percorso" id="dest"></span><button class="btn" id="sfoglia2">Cambia…</button></div>
    <div class="piccolo" id="spazio"></div>
    <label class="sc"><input type="radio" name="modo" value="cartelle" checked><span><b>Con le stesse cartelle</b><br>
      <span class="piccolo">in una cartella con il nome della memoria, ordinati come sono lì</span></span></label>
    <label class="sc"><input type="radio" name="modo" value="data"><span><b>In ordine di data</b><br>
      <span class="piccolo">Audio e Video, poi anno e mese (per i filmati: il giorno della ripresa)</span></span></label>
    <label class="sc"><input type="checkbox" id="nodoppi" checked><span>Non copiare i doppioni<br>
      <span class="piccolo">un file uguale a uno già copiato (anche con un altro nome) si salta</span></span></label>
    <label class="sc"><input type="checkbox" id="verifica" checked><span>Controlla ogni copia<br>
      <span class="piccolo">rilegge il file copiato e lo confronta con l'originale (più sicuro, un po' più lento)</span>
      </span></label>
    <div class="riga"><button class="btn primo" id="copia">Copia</button></div>
  </section>

  <section class="card" id="c4" hidden>
    <h2 id="t4">Copia in corso</h2>
    <div id="lavoro"></div>
  </section>
</div>

<dialog id="dlg">
  <div class="dtesta"><b id="dtitolo">Scegli una cartella</b><span class="percorso" id="dpercorso"
    style="display:block;margin-top:8px"></span></div>
  <div class="dlista" id="dlista"></div>
  <div class="dpiede"><button class="btn" id="dsu">↑ Su</button><button class="btn" id="dnuova">Nuova cartella</button>
    <span style="flex:1"></span><button class="btn" id="dannulla">Annulla</button>
    <button class="btn primo" id="dok">Scegli questa</button></div>
</dialog>

<script>
const T = document.querySelector('meta[name=token]').content;
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = n => Number(n).toLocaleString('it-IT');
function misura(n){ const u=['byte','KB','MB','GB','TB']; let i=0; while(n>=1024&&i<4){n/=1024;i++}
  return i ? n.toLocaleString('it-IT',{maximumFractionDigits:1})+' '+u[i] : n+' byte'; }
function durata(s){ s=Math.round(s); if(s<60) return s+' s'; if(s<3600) return Math.round(s/60)+' min';
  return Math.floor(s/3600)+' h '+Math.round(s%3600/60)+' min'; }
async function api(p, corpo){
  const r = await fetch(p, {method: corpo ? 'POST' : 'GET', headers: {'X-Token': T, 'Content-Type': 'application/json'},
    body: corpo ? JSON.stringify(corpo) : undefined});
  const d = await r.json().catch(() => ({errore: 'risposta non valida'}));
  if (!r.ok) throw new Error(d.errore || ('errore ' + r.status));
  return d;
}
let sorgente = '', destinazione = '', libero = 0, daCopiare = 0, giro = null;

async function caricaMemorie(){
  const d = await api('api/memorie');
  if (!destinazione) { destinazione = d.destinazione; mostraDest(); }
  $('memorie').innerHTML = d.memorie.map(m => `<button data-p="${esc(m.percorso)}" class="${m.percorso===sorgente?'on':''}">
    <b>${m.tipo==='esterna'?'💾':m.tipo==='casa'?'🏠':'🖥️'} ${esc(m.nome)}</b>
    <small>${m.totale ? misura(m.totale)+' · '+misura(m.libero)+' liberi' : esc(m.percorso)}</small></button>`).join('');
  for (const b of $('memorie').querySelectorAll('button')) b.onclick = () => scegliSorgente(b.dataset.p);
}
function scegliSorgente(p){
  sorgente = p; $('sorgente').textContent = p;
  for (const b of $('memorie').querySelectorAll('button')) b.classList.toggle('on', b.dataset.p === p);
  $('c2').classList.remove('spenta'); $('analisi').innerHTML = ''; $('c3').classList.add('spenta');
}
async function mostraDest(){
  $('dest').textContent = destinazione;
  try { libero = (await api('api/cartelle?p=' + encodeURIComponent(destinazione))).libero; }
  catch(e) { libero = 0; }
  spazio();
}
function spazio(){
  if (!libero) { $('spazio').textContent = ''; return; }
  const poco = daCopiare && daCopiare > libero;
  $('spazio').innerHTML = `Spazio libero lì: <b>${misura(libero)}</b>` + (daCopiare ? ` · da copiare al massimo:
    <b>${misura(daCopiare)}</b>` : '') + (poco ? ' <span class="warn">— potrebbe non bastare (i doppioni non occupano'
    + ' spazio)</span>' : '');
}

// finestra per scegliere una cartella
let dScopo = '', dAttuale = '';
async function apriDialogo(scopo, da){
  dScopo = scopo; $('dtitolo').textContent = scopo === 'sorgente' ? 'Da quale cartella leggo?' : 'Dove copio i file?';
  $('dnuova').hidden = scopo === 'sorgente';
  await vaiA(da); $('dlg').showModal();
}
async function vaiA(p){
  let d;
  try { d = await api('api/cartelle?p=' + encodeURIComponent(p || '')); }
  catch(e) { alert(e.message); return; }
  dAttuale = d.percorso; $('dpercorso').textContent = d.percorso; $('dsu').disabled = !d.su; $('dsu').dataset.p = d.su;
  $('dlista').innerHTML = d.cartelle.length ? d.cartelle.map(c => `<button data-p="${esc(c.percorso)}">📁 ${esc(c.nome)}</button>`).join('')
    : '<p class="piccolo" style="padding:8px">Nessuna cartella qui dentro.</p>';
  for (const b of $('dlista').querySelectorAll('button')) b.onclick = () => vaiA(b.dataset.p);
}
$('dsu').onclick = () => vaiA($('dsu').dataset.p);
$('dannulla').onclick = () => $('dlg').close();
$('dnuova').onclick = async () => {
  const nome = prompt('Nome della nuova cartella:'); if (!nome) return;
  try { const d = await api('api/nuova-cartella', {genitore: dAttuale, nome}); await vaiA(d.percorso); }
  catch(e) { alert(e.message); }
};
$('dok').onclick = () => {
  $('dlg').close();
  if (dScopo === 'sorgente') { scegliSorgente(dAttuale); caricaMemorie(); }
  else { destinazione = dAttuale; mostraDest(); }
};
$('sfoglia1').onclick = () => apriDialogo('sorgente', sorgente);
$('sfoglia2').onclick = () => apriDialogo('destinazione', destinazione);
$('aggiorna').onclick = caricaMemorie;

$('cerca').onclick = async () => {
  try { await api('api/analizza', {sorgente, audio: $('audio').checked, video: $('video').checked,
        tutti: $('tutti').checked, min_kb: +$('minkb').value || 0, destinazione}); segui(); }
  catch(e) { $('analisi').innerHTML = `<div class="errore">${esc(e.message)}</div>`; }
};
$('copia').onclick = async () => {
  const modo = document.querySelector('input[name=modo]:checked').value;
  try { await api('api/copia', {destinazione, modo, doppioni: !$('nodoppi').checked, verifica: $('verifica').checked});
        segui(); }
  catch(e) { alert(e.message); }
};
$('esci').onclick = async () => {
  if (!confirm('Chiudere il programma? (Se sta copiando, la copia si ferma.)')) return;
  try { await api('api/esci', {}); } catch(e) {}
  document.body.innerHTML = '<div class="wrap"><h1>Programma chiuso</h1><p class="sub">Puoi chiudere questa pagina.</p></div>';
};

function segui(){ if (!giro) { giro = setInterval(aggiornaStato, 500); } aggiornaStato(); }
function bloccaTutto(si){ for (const id of ['cerca','copia','sfoglia1','sfoglia2','aggiorna']) $(id).disabled = si;
  for (const b of $('memorie').querySelectorAll('button')) b.disabled = si; }
async function aggiornaStato(){
  let s; try { s = await api('api/stato'); } catch(e) { return; }
  const lavora = s.fase === 'analisi' || s.fase === 'copia';
  bloccaTutto(lavora);
  if (!lavora && giro) { clearInterval(giro); giro = null; }
  if (s.fase === 'analisi') {
    $('analisi').innerHTML = `<div class="barra"><i style="width:100%;opacity:.35"></i></div>
      <div>Cartelle lette: <b>${num(s.cartelle)}</b> · file trovati: <b>${num(s.trovati)}</b></div>
      <div class="file">${esc(s.dove)}</div><div class="riga"><button class="btn" onclick="ferma()">Ferma</button></div>`;
  } else if (s.fase === 'analizzato') {
    const r = s.riepilogo, tot = r.audio.n + r.video.n;
    daCopiare = r.audio.byte + r.video.byte;
    const formati = Object.entries(r.formati).sort((a,b) => b[1].n - a[1].n);
    $('analisi').innerHTML = tot ? `<div class="dati">
        <div class="dato">Audio<b>${num(r.audio.n)}</b><span class="piccolo">${misura(r.audio.byte)}</span></div>
        <div class="dato">Video<b>${num(r.video.n)}</b><span class="piccolo">${misura(r.video.byte)}</span></div></div>
      <details><summary>Formati trovati</summary><table><tr><th>Formato</th><th>Tipo</th><th class="n">File</th>
        <th class="n">Spazio</th></tr>${formati.map(([e,x]) => `<tr><td>${esc(e)}</td><td>${x.tipo}</td>
        <td class="n">${num(x.n)}</td><td class="n">${misura(x.byte)}</td></tr>`).join('')}</table></details>
      <details><summary>Elenco dei file${tot > s.esempi.length ? ' (i primi ' + s.esempi.length + ')' : ''}</summary>
        <ul class="lista">${s.esempi.map(e => `<li>${esc(e)}</li>`).join('')}</ul></details>
      ${s.n_problemi ? `<details><summary class="warn">${num(s.n_problemi)} cartelle non si sono potute leggere</summary>
        <ul class="lista">${s.problemi.map(p => `<li>${esc(p[0])} — ${esc(p[1])}</li>`).join('')}</ul></details>` : ''}`
      : `<div class="avviso">In questa memoria non ci sono file ${$('audio').checked && $('video').checked ? 'audio né video'
          : $('audio').checked ? 'audio' : 'video'}.</div>`;
    $('c3').classList.toggle('spenta', !tot); spazio();
  } else if (s.fase === 'pronto' && s.messaggio) {
    $('analisi').innerHTML = `<div class="avviso">${esc(s.messaggio)}</div>`;
  }
  if (s.fase === 'copia' || s.fase === 'fine' || (s.fase === 'errore' && s.n)) {
    $('c4').hidden = false;
    const pct = s.byte ? Math.min(100, s.byte_fatti / s.byte * 100) : 100;
    const resto = s.velocita > 0 ? (s.byte - s.byte_fatti) / s.velocita : 0;
    const dati = `<div class="dati">
      <div class="dato">Copiati<b class="ok">${num(s.copiati)}</b></div>
      <div class="dato">Già presenti<b>${num(s.gia)}</b></div>
      <div class="dato">Doppioni saltati<b>${num(s.doppioni)}</b></div>
      <div class="dato">Errori<b class="${s.errori ? 'bad' : ''}">${num(s.errori)}</b></div></div>`;
    if (s.fase === 'copia') {
      $('t4').textContent = 'Copia in corso';
      $('lavoro').innerHTML = `<div class="barra"><i style="width:${pct}%"></i></div>
        <div><b>${num(s.fatti)}</b> di <b>${num(s.n)}</b> file · ${misura(s.byte_fatti)} di ${misura(s.byte)}
        ${s.velocita ? ' · ' + misura(s.velocita) + '/s' : ''}${resto > 3 ? ' · mancano circa ' + durata(resto) : ''}</div>
        <div class="file">${esc(s.file)}</div>${dati}<div class="riga"><button class="btn" onclick="ferma()">Ferma</button></div>`;
    } else if (s.fase === 'fine') {
      $('t4').textContent = s.fermato ? 'Copia fermata' : 'Copia finita';
      $('lavoro').innerHTML = `<p>${s.fermato ? 'La copia è stata fermata. Premi di nuovo «Copia» e riprende da dove era rimasta.'
          : '<b class="ok">Fatto.</b> ' + num(s.copiati) + ' file copiati (' + misura(s.byte_copiati) + ') in ' + durata(s.secondi) + '.'}</p>
        ${dati}${s.elenco_errori && s.elenco_errori.length ? `<details open><summary class="bad">File non copiati</summary>
        <ul class="lista">${s.elenco_errori.map(e => `<li>${esc(e[0])} — ${esc(e[1])}</li>`).join('')}</ul></details>` : ''}
        ${s.rapporto ? `<p class="piccolo">Il resoconto completo è nel file «${esc(s.rapporto.split(/[\\/]/).pop())}» nella destinazione.</p>` : ''}
        <div class="riga"><button class="btn primo" onclick="apri()">Apri la cartella</button></div>`;
    } else {
      $('t4').textContent = 'Copia interrotta';
      $('lavoro').innerHTML = `<div class="errore">${esc(s.messaggio)}</div>${dati}`;
    }
  } else if (s.fase === 'errore') {
    $('analisi').innerHTML = `<div class="errore">${esc(s.messaggio)}</div>`;
  }
}
async function ferma(){ try { await api('api/ferma', {}); } catch(e) {} }
async function apri(){ try { await api('api/apri', {percorso: destinazione}); } catch(e) { alert(e.message); } }
caricaMemorie().then(segui).catch(e => document.body.insertAdjacentHTML('afterbegin',
  `<div class="errore">${esc(e.message)}</div>`));
</script>
</body>
</html>
"""


def apri_cartella(percorso):
    """Mostra la cartella nel Finder (Mac), in Esplora file (Windows) o nel gestore dei file (Linux)."""
    if platform.system() == "Darwin":
        subprocess.Popen(["open", percorso])
    elif platform.system() == "Windows":
        os.startfile(percorso)   # noqa: S606 — è la cartella scelta da chi usa il programma
    else:
        subprocess.Popen(["xdg-open", percorso])


def crea_gestore(lavoro, token, porta_ref, chiudi):
    class Gestore(BaseHTTPRequestHandler):
        server_version = "CopiaAudioVideo/" + VERSIONE

        def log_message(self, *a):
            pass

        def _host_buono(self):
            # solo «127.0.0.1:porta» o «localhost:porta»: un sito esterno non può parlare con il programma
            host = (self.headers.get("Host") or "").lower()
            return host in ("127.0.0.1:%d" % porta_ref[0], "localhost:%d" % porta_ref[0])

        def _json(self, codice, dati):
            corpo = json.dumps(dati, ensure_ascii=False).encode("utf-8")
            self.send_response(codice)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(corpo)

        def _corpo(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1024 * 1024:
                raise ValueError("richiesta troppo grande")
            return json.loads(self.rfile.read(n) or b"{}")

        def _controlla(self):
            if not self._host_buono():
                self._json(403, {"errore": "indirizzo non permesso"})
                return False
            if self.path.split("?")[0].startswith("/api/") and \
                    not secrets.compare_digest(self.headers.get("X-Token") or "", token):
                self._json(403, {"errore": "chiave mancante: riapri la pagina dal programma"})
                return False
            return True

        def do_GET(self):
            if not self._controlla():
                return
            u = urlsplit(self.path)
            q = parse_qs(u.query)
            try:
                if u.path == "/":
                    corpo = PAGINA.replace("__TOKEN__", html.escape(token)).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(corpo)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; "
                                     "script-src 'unsafe-inline'; frame-ancestors 'none'")
                    self.end_headers()
                    self.wfile.write(corpo)
                elif u.path == "/api/memorie":
                    self._json(200, {"memorie": memorie(),
                                     "destinazione": os.path.join(os.path.expanduser("~"), "Audio e video copiati")})
                elif u.path == "/api/cartelle":
                    p = (q.get("p") or [""])[0]
                    p = os.path.abspath(os.path.expanduser(p or "~"))
                    while not os.path.isdir(p) and os.path.dirname(p) != p:
                        p = os.path.dirname(p)   # una cartella che non c'è ancora: si mostra la più vicina che c'è
                    self._json(200, sottocartelle(p))
                elif u.path == "/api/stato":
                    self._json(200, lavoro.fotografia())
                else:
                    self._json(404, {"errore": "pagina inesistente"})
            except OSError as e:
                self._json(400, {"errore": "Non riesco a leggere questa cartella: %s" % (e.strerror or e)})

        def do_POST(self):
            if not self._controlla():
                return
            try:
                d = self._corpo()
                p = urlsplit(self.path).path
                if p == "/api/analizza":
                    lavoro.avvia_analisi(d.get("sorgente") or "", bool(d.get("audio")), bool(d.get("video")),
                                         d.get("min_kb") or 0, bool(d.get("tutti")), d.get("destinazione") or "")
                    self._json(200, {"ok": True})
                elif p == "/api/copia":
                    modo = d.get("modo") if d.get("modo") in ("cartelle", "data") else "cartelle"
                    lavoro.avvia_copia(d.get("destinazione") or "", modo, bool(d.get("doppioni")),
                                       d.get("verifica", True) is not False)
                    self._json(200, {"ok": True})
                elif p == "/api/ferma":
                    lavoro.ferma.set()
                    self._json(200, {"ok": True})
                elif p == "/api/nuova-cartella":
                    nome = (d.get("nome") or "").strip()
                    if not nome or nome in (".", "..") or any(c in nome for c in '/\\:*?"<>|'):
                        raise RuntimeError("Nome non valido (niente / \\ : * ? \" < > |)")
                    nuova = os.path.join(os.path.abspath(d.get("genitore") or os.path.expanduser("~")), nome)
                    os.makedirs(nuova, exist_ok=True)
                    self._json(200, {"percorso": nuova})
                elif p == "/api/apri":
                    percorso = os.path.abspath(d.get("percorso") or "")
                    if not os.path.isdir(percorso):
                        raise RuntimeError("La cartella non esiste ancora")
                    apri_cartella(percorso)
                    self._json(200, {"ok": True})
                elif p == "/api/esci":
                    lavoro.ferma.set()
                    self._json(200, {"ok": True})
                    threading.Thread(target=chiudi, daemon=True).start()
                else:
                    self._json(404, {"errore": "pagina inesistente"})
            except (RuntimeError, ValueError, OSError) as e:
                self._json(400, {"errore": str(e.strerror if isinstance(e, OSError) and e.strerror else e)})

    return Gestore


def avvia_pagina(porta=8766, apri=True):
    lavoro, token, porta_ref = Lavoro(), secrets.token_urlsafe(18), [0]
    server = None

    def chiudi():
        time.sleep(0.3)
        server.shutdown()

    for p in (porta, 0):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), crea_gestore(lavoro, token, porta_ref, chiudi))
            break
        except OSError:
            continue   # porta occupata (il programma è già aperto?): se ne prende una libera
    porta_ref[0] = server.server_address[1]
    url = "http://127.0.0.1:%d/" % porta_ref[0]
    print("Copia audio e video è aperto nel browser: %s" % url)
    print("Per chiuderlo: il pulsante «Chiudi» nella pagina, oppure Ctrl+C qui.", flush=True)
    if apri:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        lavoro.ferma.set()
        server.server_close()
    print("Programma chiuso.")


# ---------- dal Terminale ----------

def da_terminale(a):
    ferma = threading.Event()
    print("Cerco i file in %s ..." % a.sorgente)
    try:
        file, problemi = analizza(a.sorgente, a.solo != "video", a.solo != "audio", a.min_kb * 1024, a.tutti,
                                  [a.destinazione], ferma)
    except FileNotFoundError as e:
        print(e)
        return 1
    r = riepilogo(file)
    print("Trovati: %d audio (%s) e %d video (%s)." % (r["audio"]["n"], misura(r["audio"]["byte"]),
                                                       r["video"]["n"], misura(r["video"]["byte"])))
    for p in problemi[:10]:
        print("  non leggibile: %s — %s" % p)
    if a.prova or not file:
        for f in file:
            print("  " + f["rel"] + f["est"])
        return 0
    totale = sum(f["byte"] for f in file)
    inizio = [time.time(), 0.0]

    def avanti(rr, f, n, stato):
        if time.time() - inizio[1] < 0.3 and stato != "fine":
            return
        inizio[1] = time.time()
        pct = rr["byte_fatti"] * 100 / totale if totale else 100
        riga = "\r%3d%%  %d/%d file  copiati %d  doppioni %d  errori %d  " % (
            pct, rr["fatti"], len(file), rr["copiati"], rr["doppioni"], len(rr["errori"]))
        print(riga + (f["rel"][-40:] if f else "").ljust(40), end="", flush=True)

    try:
        rr = copia(file, a.sorgente, a.destinazione, a.ordina, a.anche_doppioni, not a.senza_controllo, ferma, avanti)
    except KeyboardInterrupt:
        ferma.set()
        print("\nFermato. Rilancia lo stesso comando per riprendere.")
        return 130
    except OSError as e:
        print("\n" + (e.strerror or str(e)))
        return 1
    print()
    rapporto = scrivi_rapporto(os.path.abspath(a.destinazione), os.path.abspath(a.sorgente), file, rr, problemi)
    print("Copiati %d file (%s), già presenti %d, doppioni saltati %d, errori %d." % (
        rr["copiati"], misura(rr["byte_copiati"]), rr["gia"], rr["doppioni"], len(rr["errori"])))
    for e in rr["errori"]:
        print("  errore: %s — %s" % e)
    if rapporto:
        print("Resoconto: %s" % rapporto)
    return 0 if not rr["errori"] else 2


def main(argv=None):
    ap = argparse.ArgumentParser(description="Copia i file audio e video da una memoria (chiavetta, scheda, disco). "
                                             "Senza argomenti apre la pagina nel browser.")
    ap.add_argument("sorgente", nargs="?", help="la memoria o cartella da leggere (es. /Volumes/CHIAVETTA)")
    ap.add_argument("destinazione", nargs="?", help="la cartella dove copiare")
    ap.add_argument("--memorie", action="store_true", help="elenca le memorie collegate ed esce")
    ap.add_argument("--solo", choices=("audio", "video"), help="copia solo audio o solo video")
    ap.add_argument("--ordina", choices=("cartelle", "data"), default="cartelle",
                    help="cartelle = come nella memoria (predefinito); data = Audio|Video/anno/anno-mese")
    ap.add_argument("--anche-doppioni", action="store_true", help="copia anche i file uguali a uno già copiato")
    ap.add_argument("--senza-controllo", action="store_true", help="non rilegge le copie (più veloce, meno sicuro)")
    ap.add_argument("--tutti", action="store_true", help="controlla il contenuto anche dei file con altre estensioni")
    ap.add_argument("--min-kb", type=int, default=0, help="ignora i file più piccoli di tanti KB")
    ap.add_argument("--prova", action="store_true", help="mostra solo cosa copierebbe, senza copiare")
    ap.add_argument("--porta", type=int, default=8766, help="porta della pagina (predefinita 8766)")
    ap.add_argument("--non-aprire", action="store_true", help="non apre il browser da solo")
    a = ap.parse_args(argv)
    if a.memorie:
        for m in memorie():
            print("%-34s %-40s %s" % (m["nome"], m["percorso"],
                                      "%s, %s liberi" % (misura(m["totale"]), misura(m["libero"])) if m["totale"] else ""))
        return 0
    if a.sorgente and not a.destinazione and not a.prova:
        ap.error("manca la destinazione (o aggiungi --prova per vedere soltanto cosa c'è)")
    if a.sorgente:
        a.destinazione = a.destinazione or os.path.join(os.path.expanduser("~"), "Audio e video copiati")
        return da_terminale(a)
    avvia_pagina(a.porta, not a.non_aprire)
    return 0


if __name__ == "__main__":
    sys.exit(main())
