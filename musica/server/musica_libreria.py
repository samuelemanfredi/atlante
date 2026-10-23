#!/usr/bin/env python3
"""Atlante Musica: la libreria del disco di backup, servita all'app.

Legge le cartelle di musica (anche su un disco esterno), estrae titolo, artista,
album, numero del brano e copertina dai tag MP3 (ID3), M4A/ALAC (MP4) e FLAC,
e li offre all'app Musica con queste richieste:

    GET api/musica/catalogo            elenco dei brani (JSON)
    GET api/musica/file/<id>           il brano (con Range: si può saltare avanti)
    GET api/musica/copertina/<chiave>  la copertina (JPEG o PNG)

Solo libreria standard di Python. Uso da solo:

    python3 musica_libreria.py "/Volumes/Backup/Musica" --porta 8790

Sul Mac stesso l'app si apre da http://localhost:8790/. Dal telefono serve un indirizzo
HTTPS (Safari e Chrome tengono l'app offline e la lasciano installare solo così). Con Tailscale:

    tailscale serve --bg 8790            # una volta sola: resta attivo anche dopo un riavvio
    python3 musica_libreria.py "/Volumes/Backup/Musica" --indirizzo https://<mac>.<tailnet>.ts.net/

e sull'iPhone si apre sempre e solo https://<mac>.<tailnet>.ts.net/ (un indirizzo fisso: brani
salvati e playlist stanno con l'indirizzo). Con --indirizzo chi apre la vecchia http://<mac>:8790/
da un altro dispositivo viene mandato lì. In alternativa: `tailscale cert` e --cert/--chiave.
Per unirlo a un server esistente basta chiamare `gestisci(handler)` dentro do_GET:
restituisce True se la richiesta era per la musica.
"""
import argparse
import hashlib
import json
import mimetypes
import os
import re
import ssl
import struct
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

ESTENSIONI = {".mp3", ".m4a", ".m4b", ".aac", ".flac", ".wav", ".aif", ".aiff", ".ogg", ".oga", ".opus", ".alac", ".mp4"}
TIPI = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".m4b": "audio/mp4", ".mp4": "audio/mp4", ".alac": "audio/mp4",
        ".aac": "audio/aac", ".flac": "audio/flac", ".wav": "audio/wav", ".aif": "audio/aiff", ".aiff": "audio/aiff",
        ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg"}


# ---------- lettura dei tag ----------

def _ss32(b, p=0):
    return ((b[p] & 127) << 21) | ((b[p + 1] & 127) << 14) | ((b[p + 2] & 127) << 7) | (b[p + 3] & 127)


def _testo(enc, b):
    try:
        if enc == 0:
            t = b.decode("latin-1")
        elif enc == 1:
            t = b.decode("utf-16")
        elif enc == 2:
            t = b.decode("utf-16-be")
        else:
            t = b.decode("utf-8")
    except UnicodeDecodeError:
        t = b.decode("latin-1", "replace")
    return t.split("\x00")[0].strip()


def _unsync(b):
    return b.replace(b"\xff\x00", b"\xff")


def tag_id3(f):
    h = f.read(10)
    if len(h) < 10 or h[:3] != b"ID3":
        return None
    v, fl, size = h[3], h[5], _ss32(h, 6)
    d = f.read(size)
    if fl & 0x80 and v < 4:
        d = _unsync(d)
    p = 0
    if fl & 0x40 and v >= 3:
        p = _ss32(d) if v == 4 else struct.unpack(">I", d[:4])[0] + 4
    nomi = {"TIT2": "title", "TT2": "title", "TPE1": "artist", "TP1": "artist", "TALB": "album", "TAL": "album",
            "TPE2": "albumArtist", "TP2": "albumArtist", "TRCK": "track", "TRK": "track", "TPOS": "disc", "TPA": "disc",
            "TYER": "year", "TYE": "year", "TDRC": "year", "TCON": "genre", "TCO": "genre",
            "TCMP": "compilation", "TCP": "compilation", "TLEN": "_tlen", "TLE": "_tlen"}
    o, pic, tipo_pic = {}, None, -1
    hs = 6 if v == 2 else 10
    while p + hs <= len(d):
        if v == 2:
            fid = d[p:p + 3].decode("latin-1")
            sz = int.from_bytes(d[p + 3:p + 6], "big")
        else:
            fid = d[p:p + 4].decode("latin-1")
            sz = _ss32(d, p + 4) if v == 4 else struct.unpack(">I", d[p + 4:p + 8])[0]
        if not re.fullmatch(r"[A-Z0-9]{3,4}", fid) or sz <= 0 or p + hs + sz > len(d):
            break
        fr = d[p + hs:p + hs + sz]
        if v == 4:
            if d[p + 9] & 0x02:
                fr = _unsync(fr)
            if d[p + 9] & 0x01:
                fr = fr[4:]
        if fid in nomi and fr:
            t = _testo(fr[0], fr[1:])
            if t and nomi[fid] not in o:
                o[nomi[fid]] = t
        elif fid in ("TXXX", "TXX") and fr:   # alcuni programmi scrivono la compilation come TXXX:TCMP / COMPILATION
            try:
                parti = fr[1:].decode({0: "latin-1", 1: "utf-16", 2: "utf-16-be"}.get(fr[0], "utf-8")).split("\x00")
            except UnicodeDecodeError:
                parti = []
            if len(parti) > 1 and parti[0].strip("\ufeff ").upper() in ("TCMP", "COMPILATION") and "compilation" not in o:
                o["compilation"] = parti[1].strip("\ufeff ")
        elif fid in ("APIC", "PIC") and fr:
            enc, i = fr[0], 1
            if fid == "APIC":
                j = fr.index(b"\x00", i) if b"\x00" in fr[i:] else i
                mime = fr[i:j].decode("latin-1").lower() or "image/jpeg"
                i = j + 1
            else:
                mime = "image/png" if fr[1:4].upper() == b"PNG" else "image/jpeg"
                i = 4
            tipo = fr[i] if i < len(fr) else 0
            i += 1
            if enc in (1, 2):
                while i + 1 < len(fr) and fr[i:i + 2] != b"\x00\x00":
                    i += 2
                i += 2
            else:
                while i < len(fr) and fr[i] != 0:
                    i += 1
                i += 1
            if "/" not in mime:
                mime = "image/png" if "png" in mime else "image/jpeg"
            if pic is None or (tipo == 3 and tipo_pic != 3):
                pic, tipo_pic = (fr[i:], mime), tipo
        p += hs + sz
    o["pic"] = pic
    o["_audio"] = 10 + size + (10 if fl & 0x10 else 0)   # dove cominciano i frame MPEG
    return o


def _scatole(b, s, e):
    p = s
    while p + 8 <= e:
        sz = struct.unpack(">I", b[p:p + 4])[0]
        ty = b[p + 4:p + 8].decode("latin-1")
        hs = 8
        if sz == 1:
            sz = struct.unpack(">Q", b[p + 8:p + 16])[0]
            hs = 16
        elif sz == 0:
            sz = e - p
        if sz < hs:
            break
        yield ty, p + hs, min(p + sz, e)
        p += sz


def tag_mp4(f):
    h = f.read(8)
    if len(h) < 8 or h[4:8] != b"ftyp":
        return None
    f.seek(0, 2)
    fine = f.tell()
    off, moov = 0, None
    for _ in range(200):
        if off >= fine:
            break
        f.seek(off)
        b = f.read(16)
        if len(b) < 8:
            break
        sz = struct.unpack(">I", b[:4])[0]
        ty = b[4:8]
        hs = 8
        if sz == 1 and len(b) >= 16:
            sz = struct.unpack(">Q", b[8:16])[0]
            hs = 16
        elif sz == 0:
            sz = fine - off
        if sz < 8:
            break
        if ty == b"moov":
            if sz < 64_000_000:
                f.seek(off + hs)
                moov = f.read(sz - hs)
            break
        off += sz
    o = {}
    if not moov:
        return o
    ilst = None
    for ty, a, z in _scatole(moov, 0, len(moov)):
        if ty == "mvhd":
            ver = moov[a]
            if ver == 1:
                scala, dur = struct.unpack(">IQ", moov[a + 20:a + 32])
            else:
                scala, dur = struct.unpack(">II", moov[a + 12:a + 20])
            if scala:
                o["duration"] = dur / scala
        metas = []
        if ty == "udta":
            metas = [(a2, z2) for t2, a2, z2 in _scatole(moov, a, z) if t2 == "meta"]
        elif ty == "meta":
            metas = [(a, z)]
        for a2, z2 in metas:
            for t3, a3, z3 in _scatole(moov, a2 + 4, z2):
                if t3 == "ilst":
                    ilst = (a3, z3)
    if not ilst:
        return o
    nomi = {"\xa9nam": "title", "\xa9ART": "artist", "\xa9alb": "album", "aART": "albumArtist", "\xa9day": "year", "\xa9gen": "genre"}
    for ty, a, z in _scatole(moov, *ilst):
        for t2, a2, z2 in _scatole(moov, a, z):
            if t2 != "data":
                continue
            tipo = struct.unpack(">I", moov[a2:a2 + 4])[0] & 0xFFFFFF
            val = moov[a2 + 8:z2]
            if ty in nomi and nomi[ty] not in o:
                o[nomi[ty]] = val.decode("utf-8", "replace").strip()
            elif ty == "trkn" and len(val) >= 4:
                o["track"] = str(int.from_bytes(val[2:4], "big"))
            elif ty == "disk" and len(val) >= 4:
                o["disc"] = str(int.from_bytes(val[2:4], "big"))
            elif ty == "covr" and "pic" not in o:
                o["pic"] = (val, "image/png" if tipo == 14 else "image/jpeg")
            elif ty == "cpil" and val:
                o["compilation"] = "1" if val[-1] else "0"
            break
    return o


def tag_flac(f):
    if f.read(4) != b"fLaC":
        return None
    o = {}
    for _ in range(64):
        h = f.read(4)
        if len(h) < 4:
            break
        ultimo, tipo, lun = h[0] & 0x80, h[0] & 0x7F, int.from_bytes(h[1:4], "big")
        if tipo == 0:
            d = f.read(lun)
            if len(d) >= 18:
                x = int.from_bytes(d[10:18], "big")
                freq, campioni = x >> 44, x & 0xFFFFFFFFF
                if freq:
                    o["duration"] = campioni / freq
        elif tipo == 4:
            d = f.read(lun)
            p = 4 + struct.unpack("<I", d[:4])[0]
            n = struct.unpack("<I", d[p:p + 4])[0]
            p += 4
            nomi = {"TITLE": "title", "ARTIST": "artist", "ALBUM": "album", "ALBUMARTIST": "albumArtist", "ALBUM ARTIST": "albumArtist",
                    "TRACKNUMBER": "track", "DISCNUMBER": "disc", "DATE": "year", "GENRE": "genre", "COMPILATION": "compilation"}
            for _ in range(n):
                if p + 4 > len(d):
                    break
                l = struct.unpack("<I", d[p:p + 4])[0]
                kv = d[p + 4:p + 4 + l].decode("utf-8", "replace")
                p += 4 + l
                k, _, v = kv.partition("=")
                k = nomi.get(k.upper())
                if k and k not in o:
                    o[k] = v.strip()
        elif tipo == 6 and "pic" not in o:
            d = f.read(lun)
            p = 4
            ml = struct.unpack(">I", d[p:p + 4])[0]
            mime = d[p + 4:p + 4 + ml].decode("latin-1") or "image/jpeg"
            p += 4 + ml
            dl = struct.unpack(">I", d[p:p + 4])[0]
            p += 4 + dl + 16
            pl = struct.unpack(">I", d[p:p + 4])[0]
            o["pic"] = (d[p + 4:p + 4 + pl], mime)
        else:
            f.seek(lun, 1)
        if ultimo:
            break
    return o


# ---------- durata (MP3, WAV, AIFF, OGG: per MP4 e FLAC la danno già i tag) ----------

_KBPS = {(1, 1): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
         (1, 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
         (1, 3): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
         (2, 1): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
         (2, 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)}
_KBPS[(2, 3)] = _KBPS[(2, 2)]
_HZ = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def _frame_mpeg(b, i):
    """(byte del frame, campioni per frame, frequenza, kbps, mono, mpeg1) se in b[i:] c'è un'intestazione MPEG valida."""
    if i + 4 > len(b) or b[i] != 0xFF or (b[i + 1] & 0xE0) != 0xE0:
        return None
    ver, lay, bri, sri = (b[i + 1] >> 3) & 3, (b[i + 1] >> 1) & 3, b[i + 2] >> 4, (b[i + 2] >> 2) & 3
    if ver == 1 or lay == 0 or bri in (0, 15) or sri == 3:
        return None
    mpeg1, layer = ver == 3, 4 - lay
    kbps, hz, pad = _KBPS[(1 if mpeg1 else 2, layer)][bri], _HZ[ver][sri], (b[i + 2] >> 1) & 1
    if layer == 1:
        spf, lun = 384, (12 * kbps * 1000 // hz + pad) * 4
    else:
        spf = 1152 if (layer == 2 or mpeg1) else 576
        lun = spf // 8 * kbps * 1000 // hz + pad
    return lun, spf, hz, kbps, (b[i + 3] >> 6) == 3, mpeg1


def durata_mp3(f, inizio, dim):
    f.seek(inizio)
    b = f.read(128 * 1024)
    fine = dim
    if dim >= 128:
        f.seek(dim - 128)
        if f.read(3) == b"TAG":
            fine -= 128
    for i in range(len(b) - 4):
        fr = _frame_mpeg(b, i)
        if not fr:
            continue
        lun, spf, hz, kbps, mono, mpeg1 = fr
        if i + lun + 4 <= len(b) and not _frame_mpeg(b, i + lun):
            continue   # falso inizio di frame (dentro dati che non sono audio)
        x = i + 4 + ((17 if mono else 32) if mpeg1 else (9 if mono else 17))
        if b[x:x + 4] in (b"Xing", b"Info") and struct.unpack(">I", b[x + 4:x + 8])[0] & 1:
            n = struct.unpack(">I", b[x + 8:x + 12])[0]
            if n:
                return n * spf / hz
        if b[i + 36:i + 40] == b"VBRI":
            n = struct.unpack(">I", b[i + 50:i + 54])[0]
            if n:
                return n * spf / hz
        return max(0, fine - inizio - i) * 8 / (kbps * 1000)
    return 0


def durata_riff(f, dim):
    f.seek(0)
    h = f.read(12)
    if h[:4] not in (b"RIFF", b"RF64") or h[8:12] != b"WAVE":
        return 0
    al_secondo = 0
    while True:
        c = f.read(8)
        if len(c) < 8:
            return 0
        cid, sz = c[:4], struct.unpack("<I", c[4:])[0]
        if cid == b"fmt ":
            d = f.read(sz + (sz & 1))
            al_secondo = struct.unpack("<I", d[8:12])[0]
        elif cid == b"data":
            resto = dim - f.tell()
            return (resto if sz in (0, 0xFFFFFFFF) else min(sz, resto)) / al_secondo if al_secondo else 0
        else:
            f.seek(sz + (sz & 1), 1)


def durata_aiff(f):
    f.seek(0)
    h = f.read(12)
    if h[:4] != b"FORM" or h[8:12] not in (b"AIFF", b"AIFC"):
        return 0
    while True:
        c = f.read(8)
        if len(c) < 8:
            return 0
        cid, sz = c[:4], struct.unpack(">I", c[4:])[0]
        if cid == b"COMM":
            d = f.read(sz)
            campioni = struct.unpack(">I", d[2:6])[0]
            esp, mant = ((d[8] & 0x7F) << 8) | d[9], int.from_bytes(d[10:18], "big")
            hz = mant * 2.0 ** (esp - 16383 - 63) if mant else 0
            return campioni / hz if hz else 0
        f.seek(sz + (sz & 1), 1)


def durata_ogg(f, dim):
    f.seek(0)
    b = f.read(4096)
    if b[:4] != b"OggS":
        return 0
    hz, salta = 0, 0
    k = b.find(b"\x01vorbis")
    if k >= 0:
        hz = struct.unpack("<I", b[k + 12:k + 16])[0]
    else:
        k = b.find(b"OpusHead")
        if k >= 0:
            hz, salta = 48000, struct.unpack("<H", b[k + 10:k + 12])[0]
    if not hz:
        return 0
    f.seek(max(0, dim - 65536))
    t = f.read()
    j = t.rfind(b"OggS")
    while j >= 0:
        if j + 14 <= len(t):
            g = struct.unpack("<q", t[j + 6:j + 14])[0]
            if g > 0:
                return max(0, g - salta) / hz
        j = t.rfind(b"OggS", 0, j)
    return 0


def _durata(f, est, o):
    try:
        if o.get("_tlen"):
            ms = _intero(o["_tlen"])
            if ms > 0:
                return ms / 1000
        f.seek(0, 2)
        dim = f.tell()
        if est == ".mp3" or "_audio" in o:
            return durata_mp3(f, o.get("_audio", 0), dim)
        if est == ".wav":
            return durata_riff(f, dim)
        if est in (".aif", ".aiff"):
            return durata_aiff(f)
        if est in (".ogg", ".oga", ".opus"):
            return durata_ogg(f, dim)
    except (OSError, ValueError, struct.error, IndexError, ZeroDivisionError, OverflowError):
        pass
    return 0


def leggi_tag(percorso):
    try:
        with open(percorso, "rb") as f:
            o = None
            for lettore in (tag_id3, tag_mp4, tag_flac):
                f.seek(0)
                o = lettore(f)
                if o is not None:
                    break
            o = o or {}
            if not o.get("duration"):
                o["duration"] = _durata(f, os.path.splitext(percorso)[1].lower(), o)
            return o
    except (OSError, ValueError, struct.error, IndexError):
        pass
    return {}


def da_nome(nome):
    n = os.path.splitext(nome)[0].replace("_", " ").strip()
    n = re.sub(r"^\d{1,3}\s*[-._)]\s*", "", n)
    n = re.sub(r"^\d{1,3}\s+(?=\D)", "", n)
    if " - " in n:
        a, t = n.split(" - ", 1)
        return {"artist": a.strip(), "title": t.strip()}
    return {"title": n}


def _intero(s):
    m = re.match(r"\s*(\d+)", s or "")
    return int(m.group(1)) if m else 0


# ---------- la libreria ----------

VERSIONE = 2   # versione delle voci dell'indice: le più vecchie si rileggono una volta (durata MP3/WAV, compilation)


def _dentro(p, radice):
    return p.startswith(radice.rstrip(os.sep) + os.sep)


def _parole(s):
    return " " + re.sub(r"\W+", " ", (s or "").lower()).strip() + " "


class Libreria:
    def __init__(self, cartelle, cache):
        self.cartelle = [os.path.abspath(os.path.expanduser(c)) for c in cartelle]
        self.cache = os.path.abspath(os.path.expanduser(cache))
        self.dir_copertine = os.path.join(self.cache, "copertine")
        os.makedirs(self.dir_copertine, exist_ok=True)
        self.file_indice = os.path.join(self.cache, "indice.json")
        self.blocco = threading.Lock()           # brani, percorsi, compilation
        self.blocco_lettura = threading.Lock()   # una sola lettura del disco alla volta
        self.letture = 0                         # letture del disco finite
        self._presto = 0
        self.brani = {}       # id -> voce del catalogo
        self.percorsi = {}    # id -> percorso sul disco
        self.compilation = set()
        self.ultima = 0
        try:
            with open(self.file_indice, encoding="utf-8") as f:
                salvato = json.load(f)
            self.vecchie = {v["_p"]: v for v in salvato.get("brani", []) if v.get("_p") and v.get("id")}
            self.ultima = os.path.getmtime(self.file_indice)
        except (OSError, ValueError, AttributeError, TypeError):
            self.vecchie = {}
        # Dopo un riavvio si serve subito l'indice salvato (la lettura di un disco USB può durare minuti):
        # solo le voci delle cartelle raggiungibili. La lettura in sottofondo poi lo aggiorna.
        radici = [r for r in self.cartelle if os.path.isdir(r)]
        for v in self.vecchie.values():
            if any(_dentro(v["_p"], r) for r in radici):
                self.brani[v["id"]] = v
                self.percorsi[v["id"]] = v["_p"]
        self.compilation = self._compilation(self.brani.values())
        self._mancanti = {r for r in self.cartelle if r not in radici}   # cartelle non raggiungibili all'ultima lettura
        if not self.brani:
            self.ultima = 0   # «aggiornato: 0» = il Mac sta ancora leggendo il disco

    def scansiona(self):
        """Rilegge le cartelle. Una lettura alla volta: chi arriva mentre una è in corso ne aspetta la fine
        e ne usa il risultato. I file già visti (stesso percorso, dimensione, data) non vengono riletti."""
        prima = self.letture
        with self.blocco_lettura:
            if self.letture != prima:
                return len(self.brani)
            try:
                return self._scansiona()
            finally:
                self.letture += 1

    def rileggi_presto(self, subito=False, attendi=0):
        """Rilettura in sottofondo quando il disco cambia stato: un brano del catalogo non c'è più (disco scollegato?,
        al più una volta al minuto) oppure una cartella mancante è tornata (subito). Con attendi=N si aspetta la fine per N s."""
        prima = self.letture
        if not self.blocco_lettura.locked():
            if not subito and time.time() - self._presto < 60:
                return
            self._presto = time.time()

            def leggi():
                try:
                    self.scansiona()
                except Exception as e:   # noqa: BLE001
                    print("Musica: lettura non riuscita:", e, flush=True)
            threading.Thread(target=leggi, daemon=True).start()
        fine = time.time() + attendi
        while self.letture == prima and time.time() < fine:
            time.sleep(0.05)

    def _scansiona(self):
        trovati, radici_ok = [], []
        for radice in self.cartelle:
            if not os.path.isdir(radice):
                continue
            qui = []
            for cart, sotto, files in os.walk(radice):
                sotto[:] = [s for s in sotto if not s.startswith(".")]
                for nome in files:
                    if nome.startswith(".") or os.path.splitext(nome)[1].lower() not in ESTENSIONI:
                        continue
                    p = os.path.join(cart, nome)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    qui.append((p, st, "%d:%d" % (st.st_size, int(st.st_mtime))))
            if not qui and any(_dentro(p, radice) for p in self.vecchie):
                continue   # cartella rimasta vuota (disco smontato male): si tengono le voci di prima
            radici_ok.append(radice)
            trovati += qui
        letti = {p for p, _, _ in trovati}
        raggiungibile = lambda p: any(_dentro(p, r) for r in radici_ok)   # noqa: E731
        # disco scollegato: le sue voci restano nell'indice (al ritorno non si rilegge niente), fuori dal catalogo
        tenute = [v for v in self.vecchie.values() if not raggiungibile(v["_p"]) and any(_dentro(v["_p"], r) for r in self.cartelle)]
        # file spariti: uno «nuovo» con la stessa firma (dimensione e data) è lo stesso file rinominato o spostato
        # (es. Musica che riordina la cartella) e tiene il suo id, così preferiti, playlist e copie sull'iPhone restano
        sparite = {}
        for v in self.vecchie.values():
            if v["_p"] not in letti and raggiungibile(v["_p"]):
                sparite.setdefault(v.get("_f"), []).append(v)
        usati = {v["id"] for v in tenute} | {self.vecchie[p]["id"] for p in letti if p in self.vecchie}
        nuovi, percorsi = {}, {}
        a_pezzi, t_pezzo = not self.brani and not self.ultima, time.time()   # prima lettura: catalogo man mano
        for p, st, firma in trovati:
            v = self.vecchie.get(p)
            if not v or v.get("_f") != firma or v.get("_v") != VERSIONE:
                nuova = self._voce(p, st, firma)
                nuova["id"] = v["id"] if v else self._id_nuovo(p, nuova, sparite.get(firma), usati)
                v = nuova
            nuovi[v["id"]] = v
            percorsi[v["id"]] = p
            if a_pezzi and time.time() - t_pezzo > 1:
                t_pezzo = time.time()
                with self.blocco:
                    self.brani, self.percorsi = dict(nuovi), dict(percorsi)
        comp = self._compilation(nuovi.values())
        indice = list(nuovi.values()) + tenute
        self._mancanti = set(self.cartelle) - set(radici_ok)
        with self.blocco:
            self.brani, self.percorsi, self.compilation = nuovi, percorsi, comp
            self.vecchie = {v["_p"]: v for v in indice}
            self.ultima = time.time()
        fd, tmp = tempfile.mkstemp(dir=self.cache, prefix="indice.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"versione": VERSIONE, "brani": indice}, f, ensure_ascii=False)
            os.replace(tmp, self.file_indice)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return len(nuovi)

    @staticmethod
    def _id_nuovo(p, voce, candidati, usati):
        if candidati:
            v = max(candidati, key=lambda c: sum(c.get(k) == voce.get(k) for k in ("title", "artist", "album", "track")))
            candidati.remove(v)
            if v["id"] not in usati:
                usati.add(v["id"])
                return v["id"]
        i, n = hashlib.sha1(p.encode("utf-8")).hexdigest()[:16], 0
        while i in usati:
            n += 1
            i = hashlib.sha1(("%s\n%d" % (p, n)).encode("utf-8")).hexdigest()[:16]
        usati.add(i)
        return i

    @staticmethod
    def _compilation(voci):
        """id dei brani di compilation: segnati nei tag (TCMP, cpil, COMPILATION) oppure, senza «artista album»,
        stessa cartella e stesso album con artisti diversi (se uno compare in tutti, es. «X» e «X feat. Y», non lo è)."""
        si, gruppi = set(), {}
        for v in voci:
            if v.get("_comp"):
                si.add(v["id"])
            elif v.get("album") and not v.get("albumArtist"):
                gruppi.setdefault((os.path.dirname(v["_p"]), v["album"].lower()), []).append(v)
        for g in gruppi.values():
            artisti = {_parole(v.get("artist")) for v in g} - {"  "}
            if len(artisti) > 1 and not any(all(a in b for b in artisti) for a in artisti):
                si.update(v["id"] for v in g)
        return si

    def _voce(self, p, st, firma):
        t = leggi_tag(p)
        nm = da_nome(os.path.basename(p))
        copertina = None
        pic = t.get("pic")
        if pic and pic[0]:
            dati, mime = pic
            chiave = hashlib.sha1(dati).hexdigest()[:20]
            est = ".png" if "png" in mime else ".jpg"
            dest = os.path.join(self.dir_copertine, chiave + est)
            if not os.path.exists(dest):
                with open(dest + ".tmp", "wb") as f:
                    f.write(dati)
                os.replace(dest + ".tmp", dest)
            copertina = chiave + est
        if not copertina:  # cover.jpg / folder.jpg nella cartella dell'album
            cart = os.path.dirname(p)
            for n in ("cover.jpg", "Cover.jpg", "folder.jpg", "Folder.jpg", "cover.png", "front.jpg", "AlbumArt.jpg"):
                q = os.path.join(cart, n)
                if os.path.isfile(q):
                    with open(q, "rb") as f:
                        dati = f.read()
                    chiave = hashlib.sha1(dati).hexdigest()[:20]
                    est = os.path.splitext(n)[1].lower()
                    dest = os.path.join(self.dir_copertine, chiave + est)
                    if not os.path.exists(dest):
                        with open(dest, "wb") as f:
                            f.write(dati)
                    copertina = chiave + est
                    break
        return {
            "id": hashlib.sha1(p.encode("utf-8")).hexdigest()[:16],
            "title": t.get("title") or nm.get("title") or os.path.basename(p),
            "artist": t.get("artist") or nm.get("artist") or "",
            "album": t.get("album") or "",
            "albumArtist": t.get("albumArtist") or "",
            "track": _intero(t.get("track")),
            "disc": _intero(t.get("disc")),
            "year": (t.get("year") or "")[:4],
            "genre": re.sub(r"^\(\d+\)", "", t.get("genre") or "").strip(),
            "duration": round(t.get("duration") or 0, 2),
            "added": int(st.st_mtime * 1000),
            "cover": copertina,
            "_p": p,
            "_f": firma,
            "_v": VERSIONE,
            "_comp": (t.get("compilation") or "").strip().lower() in ("1", "true", "yes"),
        }

    def catalogo(self):
        if any(os.path.isdir(r) for r in self._mancanti):   # disco ricollegato: si rilegge subito (con l'indice è veloce)
            self.rileggi_presto(subito=True, attendi=5)
        with self.blocco:
            pubblici = []
            for b in self.brani.values():
                x = {k: v for k, v in b.items() if not k.startswith("_")}
                if b["id"] in self.compilation:
                    x["compilation"] = True   # solo quando è vero: il catalogo di 10 000 brani resta leggero
                pubblici.append(x)
        o = {"brani": pubblici, "aggiornato": int(self.ultima * 1000)}
        if INDIRIZZO:
            o["indirizzo"] = INDIRIZZO
        return o


# ---------- risposte HTTP ----------

LIB = None
INDIRIZZO = ""   # indirizzo https fisso dell'app (facoltativo): l'app lo propone a chi la apre in http
PREFISSO = re.compile(r"^(?:.*/)?api/musica/(catalogo|file|copertina|aggiorna)(?:/([^/]+))?$")


def _invia_file(h, percorso, tipo):
    dim = os.path.getsize(percorso)
    inizio, fine, parziale = 0, dim - 1, False
    rng = h.headers.get("Range", "")
    m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
    if m and dim:
        if m.group(1):
            inizio = int(m.group(1))
            if m.group(2):
                fine = min(int(m.group(2)), dim - 1)
        elif m.group(2):
            inizio = max(0, dim - int(m.group(2)))
        if inizio > fine or inizio >= dim:
            h._musica_risposto = True
            h.send_response(416)
            h.send_header("Content-Range", "bytes */%d" % dim)
            h.end_headers()
            return
        parziale = True
    with open(percorso, "rb") as f:
        h._musica_risposto = True
        h.send_response(206 if parziale else 200)
        h.send_header("Content-Type", tipo)
        h.send_header("Accept-Ranges", "bytes")
        h.send_header("Content-Length", str(fine - inizio + 1))
        if parziale:
            h.send_header("Content-Range", "bytes %d-%d/%d" % (inizio, fine, dim))
        h.send_header("Cache-Control", "private, max-age=86400")
        h.end_headers()
        if h.command == "HEAD":
            return
        f.seek(inizio)
        resto = fine - inizio + 1
        try:
            while resto > 0:
                blocco = f.read(min(256 * 1024, resto))
                if not blocco:
                    break
                h.wfile.write(blocco)
                resto -= len(blocco)
        except OSError:   # telefono che chiude la connessione, o disco scollegato a metà
            h.close_connection = True


def _json(h, dati, stato=200):
    corpo = json.dumps(dati, ensure_ascii=False).encode("utf-8")
    h._musica_risposto = True
    h.send_response(stato)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(corpo)))
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    if h.command != "HEAD":
        h.wfile.write(corpo)


def gestisci(h):
    """Da chiamare in do_GET / do_HEAD di un server esistente. True se ha risposto."""
    m = PREFISSO.match(unquote(urlsplit(h.path).path))
    if not m or LIB is None:
        return False
    cosa, arg = m.group(1), m.group(2)
    h._musica_risposto = False
    try:
        if cosa == "catalogo":
            _json(h, LIB.catalogo())
        elif cosa == "aggiorna":
            _json(h, {"brani": LIB.scansiona()})
        elif cosa == "file":
            p = LIB.percorsi.get(arg or "")
            if not p or not os.path.isfile(p):
                if p:
                    LIB.rileggi_presto()   # il file è sparito: disco scollegato? il catalogo si aggiorna da sé
                _json(h, {"errore": "Brano non trovato: il disco di backup è collegato?"}, 404)
            else:
                _invia_file(h, p, TIPI.get(os.path.splitext(p)[1].lower(), "application/octet-stream"))
        elif cosa == "copertina":
            nome = os.path.basename(arg or "")
            p = os.path.join(LIB.dir_copertine, nome)
            if not nome or not os.path.isfile(p):
                _json(h, {"errore": "Copertina non trovata"}, 404)
            else:
                _invia_file(h, p, mimetypes.guess_type(p)[0] or "image/jpeg")
    except (BrokenPipeError, ConnectionResetError):
        h.close_connection = True
    except Exception as e:   # noqa: BLE001  (es. disco che sparisce durante «Aggiorna»): l'app riceve comunque una risposta
        print("Musica: errore su %s: %s" % (h.path, e), flush=True)
        if not h._musica_risposto:
            try:
                _json(h, {"errore": "Il Mac non è riuscito a leggere la musica: %s" % e}, 500)
            except OSError:
                pass
        h.close_connection = True
    return True


def avvia(cartelle, cache, intervallo=600, indirizzo=""):
    """Prepara la libreria e la tiene aggiornata in sottofondo (nuovi brani, disco ricollegato)."""
    global LIB, INDIRIZZO
    LIB = Libreria(cartelle, cache)
    INDIRIZZO = indirizzo or ""

    def ciclo():
        while True:
            try:
                n = LIB.scansiona()
                print("Musica: %d brani" % n, flush=True)
            except Exception as e:  # il disco può sparire a metà lettura
                print("Musica: lettura non riuscita:", e, flush=True)
            time.sleep(intervallo)

    threading.Thread(target=ciclo, daemon=True).start()
    return LIB


def main():
    ap = argparse.ArgumentParser(description="Serve la musica di una o più cartelle all'app Atlante Musica.")
    ap.add_argument("cartelle", nargs="+", help="cartelle con la musica, per esempio /Volumes/Backup/Musica")
    ap.add_argument("--porta", type=int, default=8790)
    ap.add_argument("--cache", default="~/.atlante-musica", help="dove tenere indice e copertine")
    ap.add_argument("--app", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."), help="cartella dell'app (index.html)")
    ap.add_argument("--indirizzo", default="", help="indirizzo https fisso dell'app, es. https://mac-mini.tailnet.ts.net/ "
                    "(con `tailscale serve --bg 8790`): chi apre l'app in http da un altro dispositivo viene mandato lì")
    ap.add_argument("--cert", help="certificato HTTPS (es. da `tailscale cert`): il server risponde direttamente in https")
    ap.add_argument("--chiave", help="chiave privata del certificato")
    a = ap.parse_args()
    indirizzo = a.indirizzo.strip()
    if indirizzo and not indirizzo.endswith("/"):
        indirizzo += "/"
    avvia(a.cartelle, a.cache, indirizzo=indirizzo)
    app = os.path.abspath(a.app)
    https = bool(a.cert)
    host_sicuro = urlsplit(indirizzo).netloc.lower()

    class H(SimpleHTTPRequestHandler):
        def __init__(self, *x, **k):
            super().__init__(*x, directory=app, **k)

        def _vai_al_sicuro(self):
            # la pagina aperta in http da un altro dispositivo non può funzionare offline né essere installata
            if not indirizzo or https or self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
                return False
            if (self.headers.get("Host") or "").lower() == host_sicuro:
                return False
            self.send_response(302)
            self.send_header("Location", indirizzo + urlsplit(self.path).path.lstrip("/"))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True

        def do_GET(self):
            if not gestisci(self) and not self._vai_al_sicuro():
                super().do_GET()

        def do_HEAD(self):
            if not gestisci(self) and not self._vai_al_sicuro():
                super().do_HEAD()

        def log_message(self, *x):
            pass

    srv = ThreadingHTTPServer(("0.0.0.0", a.porta), H)
    if https:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(a.cert, a.chiave)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
    print("Atlante Musica su %s://0.0.0.0:%d/%s  (Ctrl+C per fermare)" % ("https" if https else "http", a.porta,
          "  -> dal telefono: " + indirizzo if indirizzo else ""), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
