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

Serve anche le API dei moduli extra (pagine in x/, vedi x/CONTRATTO.md) sotto api/extra/: stato, proxy, video
(yt-dlp), voci e audiolibro (`say` del Mac), ia (Ollama sul Mac), dispositivo. Opzioni: --video-dir, --libri-dir,
--ia-modello, --ia-indirizzo. In un server esistente: `gestisci_extra(handler, metodo)` in do_GET, do_HEAD,
do_POST e do_DELETE, prima di `gestisci`.

Per aprire l'app anche da Internet, senza Tailscale sul telefono (`tailscale funnel`, porte 443, 8443 o 10000):

    python3 musica_libreria.py --imposta-password          # una volta: da Internet l'app chiede questa password
    tailscale funnel --bg --https=10000 8790

Dalla rete Tailscale, dalla rete di casa e dal Mac stesso la password non serve (vedi controlla_accesso).
"""
import argparse
import getpass
import hashlib
import hmac
import http.client
import ipaddress
import json
import mimetypes
import os
import platform
import queue
import re
import secrets
import shutil
import signal
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit

ESTENSIONI = {".mp3", ".m4a", ".m4b", ".aac", ".flac", ".wav", ".aif", ".aiff", ".ogg", ".oga", ".opus", ".alac", ".mp4"}
TIPI = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".m4b": "audio/mp4", ".mp4": "audio/mp4", ".alac": "audio/mp4",
        ".aac": "audio/aac", ".flac": "audio/flac", ".wav": "audio/wav", ".aif": "audio/aiff", ".aiff": "audio/aiff",
        ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg"}


# ---------- lettura dei tag ----------

def _ss32(b, p=0):
    return ((b[p] & 127) << 21) | ((b[p + 1] & 127) << 14) | ((b[p + 2] & 127) << 7) | (b[p + 3] & 127)


def _utf16(b, be=False):
    """UTF-16 dei tag ID3 (con o senza BOM). Un byte in più in fondo (terminatore scritto male) o un carattere rovinato
    non fanno perdere il titolo: si legge il resto, come fa l'app nel browser."""
    if b[:2] in (b"\xff\xfe", b"\xfe\xff"):
        be, b = b[:2] == b"\xfe\xff", b[2:]
    b = b[:len(b) - len(b) % 2]
    return b.decode("utf-16-be" if be else "utf-16-le", "replace")


def _testo(enc, b):
    if enc == 0:
        t = b.decode("latin-1")
    elif enc in (1, 2):
        t = _utf16(b, enc == 2)
    else:
        try:
            t = b.decode("utf-8")
        except UnicodeDecodeError:   # dichiarato UTF-8 ma scritto in latin-1 da qualche programma
            t = b.decode("latin-1")
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
            if fr[0] in (1, 2):
                parti = _utf16(fr[1:], fr[0] == 2).split("\x00")
            else:
                parti = fr[1:].decode("latin-1" if fr[0] == 0 else "utf-8", "replace").split("\x00")
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
PREFISSO = re.compile(r"^(?:.*/)?api/musica/(catalogo|file|copertina|aggiorna|pezzo)(?:/([^/]+))?$")


def _invia_file(h, percorso, tipo, intestazioni=None):
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
        for k, v in (intestazioni or {}).items():
            h.send_header(k, v)
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


PEZZO_HZ = 11025   # basta per il ritmo (cassa, rullante, charleston fino a 5 kHz) e pesa poco: 30 s = 660 KB


def _pezzo_wav(percorso, da, durata):
    """WAV mono 11025 Hz di un pezzo di brano (da «da» secondi, lungo «durata», al più 60 s), decodificato da ffmpeg con
    il taglio preciso al campione. None se ffmpeg manca. Il primo campione è proprio il secondo «da» del brano."""
    ff = _programma("ffmpeg")
    if not ff:
        return None
    da, durata = max(0.0, da), min(60.0, max(1.0, durata))
    args = [ff, "-nostdin", "-v", "error", "-ss", "%.3f" % da, "-t", "%.3f" % durata, "-i", percorso,
            "-map", "0:a:0", "-ac", "1", "-ar", str(PEZZO_HZ), "-c:a", "pcm_s16le", "-f", "wav", "pipe:1"]
    r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, env=_ambiente())
    if r.returncode != 0 or len(r.stdout) < 64:
        raise RuntimeError("ffmpeg: " + (r.stderr.decode("utf-8", "replace").strip()[-200:] or "nessun audio"))
    return r.stdout


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
        elif cosa == "pezzo":   # inizio o fine di un brano, già decodificati: l'app ne misura i BPM per il mix a tempo
            p = LIB.percorsi.get(arg or "")
            q = parse_qs(urlsplit(h.path).query)
            try:
                da, durata = float((q.get("da") or ["0"])[0]), float((q.get("durata") or ["30"])[0])
            except ValueError:
                da = durata = None
            if not p or not os.path.isfile(p):
                _json(h, {"errore": "Brano non trovato: il disco di backup è collegato?"}, 404)
            elif da is None:
                _json(h, {"errore": "da e durata devono essere numeri"}, 400)
            else:
                corpo = _pezzo_wav(p, da, durata)
                if corpo is None:
                    _json(h, {"errore": "ffmpeg non è installato sul Mac: brew install ffmpeg"}, 501)
                else:
                    h._musica_risposto = True
                    h.send_response(200)
                    h.send_header("Content-Type", "audio/wav")
                    h.send_header("Content-Length", str(len(corpo)))
                    h.send_header("Cache-Control", "private, max-age=2592000")
                    h.end_headers()
                    if h.command != "HEAD":
                        h.wfile.write(corpo)
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


# ---------- moduli extra (pagine in x/): API sotto api/extra/ ----------
#
#   GET    stato                       programmi presenti sul Mac (yt-dlp, ffmpeg, say, Ollama) e cartelle
#   GET    proxy?url=<url>             pagina o feed di un sito pubblico, letto dal Mac (max 15 MB, 20 s)
#   POST   video {url,tipo,qualita}    scaricamento con yt-dlp in sottofondo -> {id}
#   GET    video                       elenco dei lavori (il più recente per primo)
#   GET    video/file/<id>             il file scaricato (con Range)
#   POST   video/<id>/annulla          ferma lo scaricamento; un lavoro già finito esce dall'elenco (il file resta)
#   GET    voci                        voci di sintesi del Mac, prima le italiane
#   POST   audiolibro {...}            ogni capitolo diventa un m4a con `say` -> {id}
#   GET    audiolibro                  elenco
#   GET    audiolibro/file/<id>/<n>    capitolo n (da 0), con Range
#   DELETE audiolibro/<id>             toglie l'audiolibro dall'elenco e cancella i suoi file
#   GET    ia / POST ia                IA locale (Ollama)
#   GET    dispositivo                 nome, sistema, memoria, dischi, cartelle che si possono svuotare (solo lettura)
#
# Errori: {errore: "testo"} con codice >= 400.

EXTRA = re.compile(r"^(?:.*/)?api/extra/(.*)$")
CONF = {"cache": "~/.atlante-musica", "video_dir": "~/Movies/Atlante", "libri_dir": "~/Music/Atlante Audiolibri",
        "ia_modello": "", "ia_indirizzo": "http://127.0.0.1:11434"}
# avviato da launchd o da un'altra app il PATH è minimo: Homebrew sta in /opt/homebrew/bin (Apple Silicon) o /usr/local/bin
CARTELLE_PROGRAMMI = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/usr/sbin", "/bin", "/sbin")
MAX_CORPO = 20 * 1024 * 1024    # corpo delle richieste (testo di un audiolibro)
MAX_PROXY = 15 * 1024 * 1024
MAX_MEDIA = 1024 * 1024 * 1024   # audio e video (episodi di podcast, capitoli): passano a pezzi, senza tenerli in memoria
ATTESA_MEDIA = 30                # secondi massimi senza ricevere niente durante un audio o video
TEMPO_PROXY = 20
TEMPO_IA = 180
BROWSER = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
           "Version/18.0 Safari/605.1.15")
SENZA_IA = "IA locale non attiva sul Mac: installa Ollama (ollama.com) e un modello, es. ollama pull llama3.2"
TIPI_VIDEO = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
              ".mkv": "video/x-matroska", ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".aac": "audio/aac",
              ".opus": "audio/ogg", ".ogg": "audio/ogg", ".flac": "audio/flac", ".wav": "audio/wav"}
# cartelle di cui il «gestore dispositivo» mostra quanto occupano (il server non cancella niente)
PULIZIA = (("Cache delle app", "Library/Caches", "Si ricreano da sole: si possono svuotare ad app chiuse."),
           ("Xcode: DerivedData", "Library/Developer/Xcode/DerivedData", "File di compilazione di Xcode: si ricreano."),
           ("Xcode: supporto dispositivi iOS", "Library/Developer/Xcode/iOS DeviceSupport",
            "Si riscaricano quando colleghi un iPhone a Xcode."),
           ("Backup di iPhone e iPad", "Library/Application Support/MobileSync/Backup",
            "Da Finder > iPhone > Gestisci backup: tieni almeno l'ultimo."),
           ("Cestino", ".Trash", "Si svuota dal Finder."),
           ("Download", "Downloads", "Controlla i file prima di eliminarli."))


class ErroreExtra(Exception):
    """Risposta d'errore per l'app: {errore: testo} con il codice HTTP dato."""

    def __init__(self, codice, testo):
        super().__init__(testo)
        self.codice, self.testo = codice, testo


class Annullato(Exception):
    pass


def avvia_extra(cache=None, video_dir=None, libri_dir=None, ia_modello=None, ia_indirizzo=None):
    """Cartelle e IA dei moduli extra (senza chiamarla valgono quelle predefinite). Le code partono alla prima richiesta."""
    for k, v in (("cache", cache), ("video_dir", video_dir), ("libri_dir", libri_dir),
                 ("ia_modello", ia_modello), ("ia_indirizzo", ia_indirizzo)):
        if v is not None:
            CONF[k] = v
    # file temporanei di lavori interrotti (Mac spento a metà di uno scaricamento): via dopo un giorno
    tmp = os.path.join(_cart("cache"), "extra", "tmp")
    try:
        for n in os.listdir(tmp):
            p = os.path.join(tmp, n)
            if os.path.getmtime(p) < time.time() - 86400:
                if os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    os.unlink(p)
    except OSError:
        pass


def _cart(chiave):
    return os.path.abspath(os.path.expanduser(CONF[chiave]))


def _cartella_tmp():
    p = os.path.join(_cart("cache"), "extra", "tmp")
    os.makedirs(p, exist_ok=True)
    return p


def _cancella(p):
    try:
        os.unlink(p)
    except OSError:
        pass


def _nome_file(s, massimo=150):
    """Nome di file sicuro (niente / : né caratteri di controllo), al più `massimo` byte (macOS ne permette 255)."""
    s = re.sub(r"[\x00-\x1f/:\\]+", " ", str(s or ""))
    s = re.sub(r"\s+", " ", s).strip().lstrip(".").strip()
    return s.encode("utf-8")[:massimo].decode("utf-8", "ignore").strip()


def _pubblico(x):
    """Copia senza le chiavi interne (che cominciano con «_»: percorsi sul disco)."""
    if isinstance(x, dict):
        return {k: _pubblico(v) for k, v in x.items() if not k.startswith("_")}
    if isinstance(x, list):
        return [_pubblico(v) for v in x]
    return x


def _con_limite(fn, secondi, predefinito=None):
    """fn() in un thread a parte, al più per `secondi` (un disco di rete bloccato non deve bloccare la risposta)."""
    esito = [predefinito]

    def corri():
        try:
            esito[0] = fn()
        except Exception:   # noqa: BLE001
            pass
    t = threading.Thread(target=corri, daemon=True)
    t.start()
    t.join(secondi)
    return esito[0]


# ----- programmi del Mac -----

def _percorso_programmi():
    v = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    return os.pathsep.join(v + [p for p in CARTELLE_PROGRAMMI if p not in v])


def _programma(nome):
    return shutil.which(nome, path=_percorso_programmi())


def _ambiente():
    env = dict(os.environ)
    env["PATH"] = _percorso_programmi()   # così yt-dlp trova ffmpeg (e deno) anche con il PATH minimo
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _comando(args, tempo=10):
    """Uscita di un comando ('' se il programma manca, non riesce o va oltre il tempo)."""
    prog = args[0] if os.path.isabs(args[0]) else _programma(args[0])
    if not prog:
        return ""
    try:
        r = subprocess.run([prog] + list(args[1:]), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=tempo, env=_ambiente())
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""


def _termina(p):
    """Ferma un processo con i suoi figli (yt-dlp lancia ffmpeg): prima con garbo, dopo 5 s con la forza."""
    def segnale(s):
        try:
            if os.name == "posix":
                os.killpg(p.pid, s)   # il processo ha un gruppo suo (start_new_session)
            elif s == signal.SIGTERM:
                p.terminate()
            else:
                p.kill()
        except OSError:
            pass
    segnale(signal.SIGTERM)

    def forza():
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            segnale(getattr(signal, "SIGKILL", signal.SIGTERM))
    threading.Thread(target=forza, daemon=True).start()


_VERSIONI = {}


def _versione(prog):
    try:
        chiave = (prog, os.path.getmtime(prog))
    except OSError:
        return ""
    if chiave not in _VERSIONI:
        _VERSIONI[chiave] = _comando([prog, "--version"], 15).strip().splitlines()[0:1]
    return (_VERSIONI[chiave] or [""])[0]


def _sistema():
    if platform.system() == "Darwin":
        return ("macOS %s %s" % (platform.mac_ver()[0], platform.machine())).replace("  ", " ").strip()
    return "%s %s %s" % (platform.system(), platform.release(), platform.machine())


# ----- lavori in sottofondo -----

class Coda:
    """Lavori in sottofondo (scaricamenti, audiolibri), al più `paralleli` alla volta. L'elenco sta in un JSON nella
    cache: dopo un riavvio i lavori finiti restano; quelli rimasti a metà risultano interrotti."""
    MAX = 300   # lavori ricordati: i più vecchi già finiti escono dall'elenco (i loro file restano)

    def __init__(self, file, paralleli, esegui):
        self.file, self.esegui = file, esegui
        self.blocco = threading.RLock()
        self.lavori = {}       # id -> lavoro (in ordine di arrivo)
        self.processi = {}     # id -> processo in corso
        self.annullati = set()
        self.attesa = queue.Queue()
        try:
            with open(file, encoding="utf-8") as f:
                for l in json.load(f).get("lavori", []):
                    if isinstance(l, dict) and l.get("id"):
                        if l.get("stato") in ("in coda", "in corso"):
                            l["stato"], l["errore"] = "errore", "Interrotto: il server sul Mac è stato riavviato. Riprova."
                        self.lavori[l["id"]] = l
        except (OSError, ValueError, AttributeError, TypeError):
            pass
        for _ in range(paralleli):
            threading.Thread(target=self._lavora, daemon=True).start()

    def salva(self):
        with self.blocco:
            try:
                os.makedirs(os.path.dirname(self.file), exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.file), prefix=".lavori.", suffix=".tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump({"lavori": list(self.lavori.values())}, f, ensure_ascii=False)
                    os.replace(tmp, self.file)
                except BaseException:
                    _cancella(tmp)
                    raise
            except OSError as e:   # disco pieno: il lavoro va avanti, l'elenco si salverà la prossima volta
                print("Atlante: elenco dei lavori non salvato:", e, flush=True)

    def aggiungi(self, campi, dati=None):
        l = {"id": uuid.uuid4().hex[:12]}
        l.update(campi)
        l.update(stato="in coda", progresso=0, errore="", creato=int(time.time() * 1000))
        with self.blocco:
            self.lavori[l["id"]] = l
            vecchi = [i for i, x in self.lavori.items() if x.get("stato") in ("fatto", "errore")]
            for i in vecchi[:max(0, len(self.lavori) - self.MAX)]:
                del self.lavori[i]
            self.salva()
        self.attesa.put((l["id"], dati))
        return l["id"]

    def aggiorna(self, id_, salva=False, **campi):
        with self.blocco:
            l = self.lavori.get(id_)
            if l is not None:
                l.update(campi)
                if salva:
                    self.salva()

    def elenco(self):
        with self.blocco:
            return [_pubblico(l) for l in reversed(list(self.lavori.values()))]

    def rimuovi(self, id_):
        with self.blocco:
            l = self.lavori.pop(id_, None)
            if l is not None:
                self.salva()
            return l

    def controlla(self, id_):
        if id_ in self.annullati:
            raise Annullato()

    def processo(self, id_, args, **kw):
        """Avvia un programma per il lavoro (argomenti in lista: mai una shell) e lo ricorda per poterlo fermare."""
        with self.blocco:
            self.controlla(id_)
            p = subprocess.Popen(args, stdin=subprocess.DEVNULL, env=_ambiente(), start_new_session=os.name == "posix", **kw)
            self.processi[id_] = p
        return p

    def annulla(self, id_):
        with self.blocco:
            l = self.lavori.get(id_)
            if not l or l.get("stato") not in ("in coda", "in corso"):
                return
            self.annullati.add(id_)
            p = self.processi.get(id_)
            if l["stato"] == "in coda":
                l["stato"], l["errore"] = "errore", "Annullato"
                self.salva()
        if p is not None:
            _termina(p)

    def _lavora(self):
        while True:
            id_, dati = self.attesa.get()
            with self.blocco:
                l = self.lavori.get(id_)
                if l is None or l.get("stato") != "in coda":
                    self.annullati.discard(id_)
                    continue
                l["stato"] = "in corso"
                self.salva()
            try:
                self.esegui(self, l, dati)
            except Annullato:
                self.aggiorna(id_, salva=True, stato="errore", errore="Annullato")
            except Exception as e:   # noqa: BLE001
                print("Atlante: lavoro %s non riuscito: %s" % (id_, e), flush=True)
                self.aggiorna(id_, salva=True, stato="errore", errore=str(e) or e.__class__.__name__)
            else:
                self.aggiorna(id_, salva=True, stato="fatto", progresso=100, errore="")
            finally:
                with self.blocco:
                    self.processi.pop(id_, None)
                    self.annullati.discard(id_)


_CODE = {}
_blocco_code = threading.Lock()


def _coda(nome):
    with _blocco_code:
        if nome not in _CODE:
            base = os.path.join(_cart("cache"), "extra")
            if nome == "video":
                _CODE[nome] = Coda(os.path.join(base, "video.json"), 2, _scarica_video)
            else:   # say usa tutto il processore: un audiolibro alla volta
                _CODE[nome] = Coda(os.path.join(base, "audiolibri.json"), 1, _crea_audiolibro)
        return _CODE[nome]


def _disposizione(nome):
    """Content-Disposition con il nome del file (per «Salva in File» sull'iPhone)."""
    semplice = re.sub(r'[^\x20-\x7e]|["\\]', "_", nome)
    return "inline; filename=\"%s\"; filename*=UTF-8''%s" % (semplice, quote(nome, safe=""))


# ----- video e audio con yt-dlp -----

def _formato(tipo, qualita, ffmpeg):
    """Argomenti di formato per yt-dlp. Senza ffmpeg non si possono unire video e audio né convertire: un file unico."""
    if tipo == "audio":
        if not ffmpeg:
            return ["-f", "ba[ext=m4a]/ba/b"]
        return ["-f", "ba[ext=m4a]/ba/b", "-x", "--audio-format", "m4a", "--embed-metadata"]
    alt = {"1080": 1080, "720": 720}.get(qualita)
    if not ffmpeg:
        return ["-f", ("b[height<=%d]/b" % alt) if alt else "b"]
    f = ("bv*[height<=%d]+ba/b[height<=%d]/b" % (alt, alt)) if alt else "bv*+ba/b"
    # a parità di risoluzione H.264 e AAC: si vedono su iPhone, iPad e QuickTime
    return ["-f", f, "-S", "res,vcodec:h264,acodec:aac", "--merge-output-format", "mp4", "--embed-metadata"]


def _piu_recente(cartella, dopo):
    meglio, t = "", dopo - 2
    try:
        for e in os.scandir(cartella):
            if e.is_file() and not e.name.startswith(".") and not e.name.endswith((".part", ".ytdl", ".temp")):
                m = e.stat().st_mtime
                if m >= t:
                    meglio, t = e.path, m
    except OSError:
        pass
    return meglio


def _scarica_video(coda, l, _dati):
    yt = _programma("yt-dlp")
    if not yt:
        raise RuntimeError("yt-dlp non è installato sul Mac: brew install yt-dlp ffmpeg")
    cartella = _cart("video_dir")
    os.makedirs(cartella, exist_ok=True)
    tmp = os.path.join(_cartella_tmp(), "video-" + l["id"])   # pezzi a metà: via in ogni caso alla fine
    os.makedirs(tmp, exist_ok=True)
    ff = _programma("ffmpeg")
    args = [yt, "--newline", "--progress", "--no-playlist", "--no-mtime",
            "-P", "home:" + cartella, "-P", "temp:" + tmp, "-o", "%(title).150B [%(id)s].%(ext)s",
            "--print", "before_dl:[atlante] titolo %(title)s", "--print", "after_move:[atlante] file %(filepath)s"]
    if ff:
        args += ["--ffmpeg-location", ff]
    args += _formato(l.get("tipo"), l.get("qualita"), bool(ff)) + ["--", l["url"]]
    inizio, file_, errore, titolo = time.time(), "", "", ""
    parte, ultimo, prog = 0, 0.0, 0.0
    radice = os.path.realpath(cartella)
    try:
        p = coda.processo(l["id"], args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tmp)
        for riga in p.stdout:
            r = riga.decode("utf-8", "replace").strip()
            m = re.match(r"\[download\]\s+(\d+(?:\.\d+)?)%", r)
            if m:
                pct = float(m.group(1))
                if pct + 5 < ultimo:   # si riparte da zero: seconda parte (l'audio, dopo il video)
                    parte += 1
                ultimo = pct
                if l.get("tipo") == "audio":
                    x = pct * 0.97              # poi la conversione in m4a
                elif parte == 0:
                    x = pct * 0.9               # video
                else:
                    x = 90 + min(pct, 100) * 0.09   # audio, poi l'unione
                if x > prog:
                    prog = x
                    coda.aggiorna(l["id"], progresso=round(prog, 1))
            elif r.startswith("[atlante] titolo "):
                titolo = r[17:].strip()
                if titolo and titolo != "NA":
                    coda.aggiorna(l["id"], titolo=titolo)
            elif r.startswith("[atlante] file "):
                file_ = r[15:].strip()
            elif r.startswith("ERROR:"):
                errore = r[6:].strip()
            elif r.startswith("/") and _dentro(os.path.realpath(r), radice) and os.path.isfile(r):
                file_ = r   # --print after_move:filepath senza prefisso
        p.wait()
        coda.controlla(l["id"])
        if (not file_ or not os.path.isfile(file_)) and p.returncode == 0:
            file_ = _piu_recente(cartella, inizio)
        if not file_ or not os.path.isfile(file_):
            raise RuntimeError(errore or "yt-dlp non è riuscito (codice %s)" % p.returncode)
        nome = os.path.basename(file_)
        if not titolo or titolo == "NA":
            titolo = re.sub(r"\s*\[[^\[\]]*\]$", "", os.path.splitext(nome)[0]).strip() or nome
        coda.aggiorna(l["id"], titolo=titolo, nome=nome, dimensione=os.path.getsize(file_),
                      file="video/file/" + l["id"], _percorso=file_)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _nuovo_video(corpo):
    url = str(corpo.get("url") or "").strip()
    if not re.match(r"^https?://[^\s/]+\S*$", url, re.I):
        raise ErroreExtra(400, "Serve l'indirizzo (http o https) della pagina del video")
    tipo = str(corpo.get("tipo") or "video")
    if tipo not in ("video", "audio"):
        raise ErroreExtra(400, "Tipo non valido: video o audio")
    qualita = str(corpo.get("qualita") or "1080")
    if qualita not in ("1080", "720", "best"):
        raise ErroreExtra(400, "Qualità non valida: 1080, 720 o best")
    if not _programma("yt-dlp"):
        raise ErroreExtra(503, "yt-dlp non è installato sul Mac: brew install yt-dlp ffmpeg")
    return _coda("video").aggiungi({"url": url, "titolo": url, "tipo": tipo, "qualita": qualita,
                                    "file": "", "nome": "", "dimensione": 0})


def _annulla_video(id_):
    coda = _coda("video")
    l = coda.lavori.get(id_)
    if not l:
        raise ErroreExtra(404, "Scaricamento non trovato")
    if l.get("stato") in ("in coda", "in corso"):
        coda.annulla(id_)
    else:
        coda.rimuovi(id_)


def _file_video(h, id_):
    l = _coda("video").lavori.get(id_) or {}
    p = l.get("_percorso")
    if not p or not os.path.isfile(p):
        raise ErroreExtra(404, "File non trovato: non ancora scaricato, oppure spostato dalla cartella")
    tipo = TIPI_VIDEO.get(os.path.splitext(p)[1].lower()) or mimetypes.guess_type(p)[0] or "application/octet-stream"
    _invia_file(h, p, tipo, {"Content-Disposition": _disposizione(os.path.basename(p)), "X-Content-Type-Options": "nosniff"})


# ----- voci e audiolibri con `say` -----

_VOCI = {"t": 0.0, "say": "", "voci": []}


def voci():
    """Voci di sintesi del Mac (`say -v ?`): [{nome, lingua, esempio}], prima le italiane. [] senza `say`."""
    say = _programma("say")
    if not say:
        return []
    if _VOCI["say"] == say and time.time() - _VOCI["t"] < 600:
        return _VOCI["voci"]
    out = []
    for riga in _comando([say, "-v", "?"], 20).splitlines():
        # «Eddy (Italian (Italy)) it_IT    # Ciao! Mi chiamo Eddy.»: la lingua è l'ultima parola prima di «#»
        m = re.match(r"^(.+?)\s+([A-Za-z]{2,3}(?:[_-][A-Za-z0-9]+)+)\s+#\s?(.*)$", riga.strip())
        if m:
            out.append({"nome": m.group(1).strip(), "lingua": m.group(2), "esempio": m.group(3).strip()})
    out.sort(key=lambda v: (0, "", v["nome"].lower()) if v["lingua"].lower().startswith("it")
             else (1, v["lingua"], v["nome"].lower()))
    if out:
        _VOCI.update(t=time.time(), say=say, voci=out)
    return out


def _durata_audio(p):
    m = re.search(r"duration:\s*([\d.]+)\s*sec", _comando(["afinfo", p], 20))
    if m and float(m.group(1)) > 0:
        return float(m.group(1))
    return leggi_tag(p).get("duration") or 0


def _nuovo_audiolibro(corpo):
    if not _programma("say"):
        raise ErroreExtra(503, "Sintesi vocale non disponibile: serve il comando say del Mac")
    capitoli = corpo.get("capitoli")
    if not isinstance(capitoli, list) or not capitoli:
        raise ErroreExtra(400, "Mancano i capitoli")
    titoli, testi = [], []
    for i, c in enumerate(capitoli):
        if isinstance(c, str):
            c = {"testo": c}
        if not isinstance(c, dict):
            raise ErroreExtra(400, "Capitolo %d non valido" % (i + 1))
        testo = str(c.get("testo") or "").strip()
        if not testo:
            continue   # un capitolo vuoto non diventa un file
        titoli.append(str(c.get("titolo") or "").strip() or "Capitolo %d" % (len(titoli) + 1))
        testi.append(testo)
    if not testi:
        raise ErroreExtra(400, "I capitoli sono vuoti")
    titolo = str(corpo.get("titolo") or "").strip() or "Audiolibro"
    autore = str(corpo.get("autore") or "").strip()
    voce = str(corpo.get("voce") or "").strip()
    elenco = voci()
    if voce and elenco and voce not in {v["nome"] for v in elenco}:
        raise ErroreExtra(400, "Voce non disponibile sul Mac: %s" % voce)
    try:
        vel = float(corpo.get("velocita") or 0)
    except (TypeError, ValueError):
        vel = 0
    if 0 < vel <= 4:
        vel *= 180   # data come moltiplicatore (1 = normale)
    vel = int(min(500, max(80, vel))) if vel > 0 else 0   # parole al minuto; 0 = quella della voce
    radice = _cart("libri_dir")
    os.makedirs(radice, exist_ok=True)
    base = _nome_file("%s - %s" % (autore, titolo) if autore else titolo) or "Audiolibro"
    n = 1
    while True:
        cart = os.path.join(radice, base if n == 1 else "%s (%d)" % (base, n))
        try:
            os.makedirs(cart)
            break
        except FileExistsError:
            n += 1
    l = {"titolo": titolo, "autore": autore, "voce": voce, "velocita": vel, "_cartella": cart,
         "capitoli": [{"n": i, "titolo": t, "pronto": False, "durata": 0} for i, t in enumerate(titoli)]}
    return _coda("libri").aggiungi(l, testi)


def _crea_audiolibro(coda, l, testi):
    say = _programma("say")
    if not say:
        raise RuntimeError("Sintesi vocale non disponibile: serve il comando say del Mac")
    if not testi:
        raise RuntimeError("Testo dei capitoli non più disponibile: crea di nuovo l'audiolibro")
    cart = l["_cartella"]
    os.makedirs(cart, exist_ok=True)
    totale, fatto = sum(len(t) for t in testi) or 1, 0
    for i, testo in enumerate(testi):
        coda.controlla(l["id"])
        cap = l["capitoli"][i]
        out = os.path.join(cart, "%03d %s.m4a" % (i + 1, _nome_file(cap["titolo"], 120) or "Capitolo"))
        fd, txt = tempfile.mkstemp(dir=_cartella_tmp(), prefix="say.", suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(testo)
            args = [say]
            if l.get("voce"):
                args += ["-v", l["voce"]]
            if l.get("velocita"):
                args += ["-r", str(l["velocita"])]
            args += ["-o", out, "--file-format=m4af", "--data-format=aac", "-f", txt]
            p = coda.processo(l["id"], args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            _, err = p.communicate()
            coda.controlla(l["id"])
            if p.returncode != 0 or not os.path.isfile(out):
                motivo = err.decode("utf-8", "replace").strip()[:300] or "codice %s" % p.returncode
                raise RuntimeError("say non è riuscito sul capitolo %d: %s" % (i + 1, motivo))
        except BaseException:
            _cancella(out)
            raise
        finally:
            _cancella(txt)
        durata = _durata_audio(out)
        fatto += len(testo)
        with coda.blocco:
            cap.update(pronto=True, durata=round(durata, 1), _file=out)
        coda.aggiorna(l["id"], salva=True, progresso=round(min(99.9, fatto * 100.0 / totale), 1))


def _elimina_audiolibro(id_):
    coda = _coda("libri")
    l = coda.lavori.get(id_)
    if not l:
        raise ErroreExtra(404, "Audiolibro non trovato")
    coda.annulla(id_)
    fine = time.time() + 5   # il lavoro in corso si ferma e toglie il capitolo a metà
    while id_ in coda.processi and time.time() < fine:
        time.sleep(0.05)
    coda.rimuovi(id_)
    cart = l.get("_cartella") or ""
    for c in l.get("capitoli") or []:
        f = c.get("_file")
        if f and cart and _dentro(f, cart):
            _cancella(f)
    if cart:
        try:
            os.rmdir(cart)   # solo se è rimasta vuota: niente che non abbia creato il server
        except OSError:
            pass


def _file_capitolo(h, id_, n):
    l = _coda("libri").lavori.get(id_) or {}
    caps = l.get("capitoli") or []
    try:
        cap = caps[int(n)] if 0 <= int(n) < len(caps) else {}
    except ValueError:
        cap = {}
    p = cap.get("_file")
    if not cap.get("pronto") or not p or not os.path.isfile(p):
        raise ErroreExtra(404, "Capitolo non pronto o non trovato")
    _invia_file(h, p, "audio/mp4", {"Content-Disposition": _disposizione(os.path.basename(p))})


# ----- IA locale (Ollama) -----

_APRI = urllib.request.build_opener(urllib.request.ProxyHandler({})).open   # Ollama è sul Mac: niente proxy di sistema


def _ollama(percorso, dati=None, tempo=3):
    corpo = None if dati is None else json.dumps(dati).encode("utf-8")
    req = urllib.request.Request(CONF["ia_indirizzo"].rstrip("/") + percorso, data=corpo,
                                 headers={"Content-Type": "application/json"})
    with _APRI(req, timeout=tempo) as r:
        return json.loads(r.read().decode("utf-8"))


def _modelli_ia():
    """(attiva, [nomi dei modelli]) di Ollama."""
    try:
        j = _ollama("/api/tags")
    except (OSError, ValueError, http.client.HTTPException):
        return False, []
    nomi = [m.get("name") or m.get("model") for m in (j.get("models") if isinstance(j, dict) else None) or []
            if isinstance(m, dict) and (m.get("name") or m.get("model"))]
    return True, nomi


def _scegli_modello(richiesto, modelli):
    if richiesto:
        return richiesto
    voluto = (CONF.get("ia_modello") or "").strip()
    if voluto:
        for m in modelli:
            if m == voluto or m.split(":")[0] == voluto:
                return m
        if not modelli:
            return voluto
    return modelli[0] if modelli else ""


def _chiedi_ia(corpo):
    prompt = str(corpo.get("prompt") or "").strip()
    if not prompt:
        raise ErroreExtra(400, "Manca la domanda (prompt)")
    attiva, modelli = _modelli_ia()
    if not attiva:
        raise ErroreExtra(503, SENZA_IA)
    modello = _scegli_modello(str(corpo.get("modello") or "").strip(), modelli)
    if not modello:
        raise ErroreExtra(503, "Ollama è attivo ma non ha modelli: sul Mac esegui ollama pull llama3.2")
    dati = {"model": modello, "prompt": prompt, "stream": False}
    sistema = str(corpo.get("sistema") or "").strip()
    if sistema:
        dati["system"] = sistema
    if corpo.get("json"):
        dati["format"] = "json"
    try:
        j = _ollama("/api/generate", dati, TEMPO_IA)
    except urllib.error.HTTPError as e:
        try:
            testo = json.loads(e.read().decode("utf-8")).get("error") or ""
        except (OSError, ValueError, AttributeError):
            testo = ""
        raise ErroreExtra(502, "IA locale: %s" % (testo or "errore %d" % e.code))
    except (OSError, ValueError, http.client.HTTPException) as e:
        if isinstance(e, socket.timeout) or isinstance(getattr(e, "reason", None), socket.timeout):
            raise ErroreExtra(504, "L'IA locale non ha risposto entro %d secondi" % TEMPO_IA)
        raise ErroreExtra(503, SENZA_IA)
    return {"testo": j.get("response", "") if isinstance(j, dict) else "", "modello": modello}


# ----- stato e dispositivo -----

def stato():
    attiva, modelli = _modelli_ia()
    yt = _programma("yt-dlp")
    return {"ytdlp": bool(yt), "ffmpeg": bool(_programma("ffmpeg")), "say": bool(_programma("say")),
            "ollama": attiva, "modelli": modelli, "modello": _scegli_modello("", modelli),
            "video_dir": _cart("video_dir"), "libri_dir": _cart("libri_dir"), "sistema": _sistema(),
            "ytdlp_versione": _versione(yt) if yt else "",
            "ollama_installato": attiva or bool(_programma("ollama")) or os.path.isdir("/Applications/Ollama.app")}


def _memoria(mac):
    tot = libera = 0
    if mac:
        try:
            tot = int(_comando(["sysctl", "-n", "hw.memsize"]).strip() or 0)
        except ValueError:
            tot = 0
        vm = _comando(["vm_stat"])
        m = re.search(r"page size of (\d+) bytes", vm)
        pag = int(m.group(1)) if m else 4096
        for voce in ("free", "inactive", "speculative"):   # come «memoria disponibile»: libera + riutilizzabile
            m = re.search(r"^Pages %s:\s+(\d+)" % voce, vm, re.M)
            if m:
                libera += int(m.group(1)) * pag
    else:
        try:
            with open("/proc/meminfo") as f:
                info = dict(re.findall(r"^(\w+):\s+(\d+)", f.read(), re.M))
            tot = int(info.get("MemTotal", 0)) * 1024
            libera = int(info.get("MemAvailable", info.get("MemFree", 0))) * 1024
        except (OSError, ValueError):
            pass
    return {"totale": tot, "libera": libera}


def _dischi():
    def misura(nome, p):
        u = shutil.disk_usage(p)
        return {"nome": nome, "percorso": p, "totale": u.total, "libero": u.free, "usato": u.used}

    def esame(n, p):   # in un thread: un disco di rete bloccato non blocca la risposta
        if os.path.realpath(p) == "/":
            return n   # «Macintosh HD» in /Volumes è un collegamento al disco di avvio
        return misura(n, p) if os.path.ismount(p) else None

    radice, altri = "Disco di avvio", []
    try:
        nomi = sorted(os.listdir("/Volumes")) if os.path.isdir("/Volumes") else []
    except OSError:
        nomi = []
    for n in nomi:
        if n.startswith(".") or n.startswith("com.apple."):
            continue
        v = _con_limite(lambda n=n: esame(n, os.path.join("/Volumes", n)), 3)
        if isinstance(v, str):
            radice = v
        elif v:
            altri.append(v)
    primo = _con_limite(lambda: misura(radice, "/"), 3)
    return ([primo] if primo else []) + altri


_MISURE = {"t": 0.0, "voci": [], "fili": []}
_blocco_misure = threading.Lock()


def _misura_cartella(v, scadenza):
    """Spazio occupato da una cartella (solo lettura). Si ferma alla scadenza: «completa» dice se ha finito."""
    tot, pile, n = 0, [v["percorso"]], 0
    try:
        while pile:
            d = pile.pop()
            try:
                voci_ = os.scandir(d)
            except PermissionError:
                if d == v["percorso"]:
                    v["errore"] = ("macOS non permette di leggerla: dai a Terminale (o all'app che avvia il server) "
                                   "l'accesso completo al disco in Impostazioni di Sistema > Privacy e sicurezza")
                    tot = None
                    return
                continue
            except OSError:
                continue
            with voci_:
                for e in voci_:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            pile.append(e.path)
                        else:
                            st = e.stat(follow_symlinks=False)
                            tot += getattr(st, "st_blocks", 0) * 512 or st.st_size
                    except OSError:
                        pass
                    n += 1
                    if n % 2000 == 0:
                        v["dimensione"] = tot
                        if time.monotonic() > scadenza:
                            return
        v["completa"] = True
    finally:
        v["dimensione"] = tot


def _pulizia(attesa=8):
    """Quanto occupano le cartelle di PULIZIA. La misura continua in sottofondo (al più 60 s) e resta valida 5 minuti:
    la risposta aspetta al più `attesa` secondi e dà le misure fatte fin lì («completa»: false)."""
    with _blocco_misure:
        finita = all(not t.is_alive() for t in _MISURE["fili"])
        if not _MISURE["fili"] or (finita and time.time() - _MISURE["t"] > 300):
            casa = os.path.expanduser("~")
            voci_ = []
            for nome, rel, nota in PULIZIA:
                p = os.path.join(casa, rel)
                if os.path.isdir(p):
                    voci_.append({"nome": nome, "percorso": p, "dimensione": 0, "completa": False, "nota": nota})
            scadenza = time.monotonic() + 60
            fili = [threading.Thread(target=_misura_cartella, args=(v, scadenza), daemon=True) for v in voci_]
            for t in fili:
                t.start()
            _MISURE.update(t=time.time(), voci=voci_, fili=fili)
        fili, voci_ = _MISURE["fili"], _MISURE["voci"]
    fine = time.monotonic() + attesa
    for t in fili:
        t.join(max(0, fine - time.monotonic()))
    return [dict(v) for v in voci_]


def dispositivo():
    mac = platform.system() == "Darwin"
    nome = (_comando(["scutil", "--get", "ComputerName"]).strip() if mac else "") or socket.gethostname()
    avvio = 0.0
    if mac:
        m = re.search(r"sec\s*=\s*(\d+)", _comando(["sysctl", "-n", "kern.boottime"]))
        avvio = float(m.group(1)) if m else 0.0
    else:
        try:
            with open("/proc/uptime") as f:
                avvio = time.time() - float(f.read().split()[0])
        except (OSError, ValueError, IndexError):
            pass
    o = {"nome": nome, "sistema": "macOS" if mac else platform.system(),
         "versione": platform.mac_ver()[0] if mac else platform.release(), "architettura": platform.machine(),
         "avvio": int(avvio * 1000), "acceso_da": int(time.time() - avvio) if avvio else 0,
         "memoria": _memoria(mac), "dischi": _dischi(), "pulizia": _pulizia()}
    if mac:
        o["modello"] = _comando(["sysctl", "-n", "hw.model"]).strip()
        o["processore"] = _comando(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
    return o


# ----- proxy verso siti pubblici -----

_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _ip_vietato(testo):
    """True per ciò che non è Internet pubblico: il Mac stesso (Ollama), la rete di casa, Tailscale (100.64/10),
    link-local (169.254), multicast, riservati. Anche se nascosti in un indirizzo IPv6 (::ffff:, 6to4, NAT64, Teredo)."""
    try:
        ip = ipaddress.ip_address(str(testo).split("%", 1)[0])
    except ValueError:
        return True
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in _NAT64:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        elif ip.sixtofour is not None:
            ip = ip.sixtofour
        elif ip.teredo is not None:
            ip = ip.teredo[1]
    return (not ip.is_global or ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified)


class _Conn(http.client.HTTPConnection):
    """Connessione all'indirizzo già controllato: il nome non si risolve una seconda volta (niente DNS rebinding)."""

    def __init__(self, host, porta, ip, tempo):
        super().__init__(host, porta, timeout=tempo)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _ConnS(http.client.HTTPSConnection):
    def __init__(self, host, porta, ip, tempo, ctx):
        super().__init__(host, porta, timeout=tempo, context=ctx)
        self._ip = ip

    def connect(self):
        s = socket.create_connection((self._ip, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(s, server_hostname=self.host)
        except BaseException:
            s.close()
            raise


_SSL = []


def _ssl():
    if not _SSL:
        ctx = ssl.create_default_context()
        # il Python di python.org su macOS non usa i certificati di sistema: si aggiungono quelli di /etc/ssl/cert.pem
        if os.path.isfile("/etc/ssl/cert.pem"):
            try:
                ctx.load_verify_locations("/etc/ssl/cert.pem")
            except (OSError, ssl.SSLError):
                pass
        _SSL.append(ctx)
    return _SSL[0]


def _apri_pubblico(url, scadenza):
    """(risposta, connessione, socket) per una GET a un indirizzo pubblico. Niente rimandi: li segue _proxy."""
    u = urlsplit(url)
    sicuro = u.scheme.lower() == "https"
    if u.scheme.lower() not in ("http", "https") or not u.hostname:
        raise ErroreExtra(400, "Si possono leggere solo indirizzi http e https")
    try:
        host = u.hostname.encode("idna").decode("ascii")
        porta = u.port or (443 if sicuro else 80)
    except (UnicodeError, ValueError):
        raise ErroreExtra(400, "Indirizzo non valido")
    try:
        infos = socket.getaddrinfo(host, porta, 0, socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        raise ErroreExtra(502, "Sito non trovato: %s" % u.hostname)
    ips = []
    for info in infos:
        if info[4][0] not in ips:
            ips.append(info[4][0])
    if not ips or any(_ip_vietato(ip) for ip in ips):
        raise ErroreExtra(403, "Indirizzo non permesso: dal Mac si leggono solo siti pubblici "
                               "(non il Mac stesso né la rete di casa)")
    percorso = quote(u.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if u.query:
        percorso += "?" + quote(u.query, safe="/?%:@!$&'()*+,;=-._~")
    intest = {"User-Agent": BROWSER, "Accept-Language": "it-IT,it;q=0.9,en;q=0.8", "Accept-Encoding": "identity",
              "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/rss+xml,*/*;q=0.8",
              "Connection": "close"}
    ultimo = ""
    for ip in ips:
        resto = scadenza - time.time()
        if resto <= 0:
            break
        conn = _ConnS(host, porta, ip, resto, _ssl()) if sicuro else _Conn(host, porta, ip, resto)
        try:
            conn.request("GET", percorso, headers=intest)
            sock = conn.sock
            return conn.getresponse(), conn, sock
        except socket.timeout:
            conn.close()
            raise ErroreExtra(504, "Il sito non ha risposto in tempo")
        except ssl.SSLError as e:
            conn.close()
            ultimo = "connessione sicura non riuscita (%s)" % (getattr(e, "reason", None) or e)
        except (OSError, http.client.HTTPException) as e:
            conn.close()
            ultimo = str(e)
    if time.time() >= scadenza:
        raise ErroreExtra(504, "Il sito non ha risposto in tempo")
    raise ErroreExtra(502, "Sito non raggiungibile" + (": " + ultimo if ultimo else ""))


def _proxy(h):
    url = (parse_qs(urlsplit(h.path).query).get("url") or [""])[0].strip()
    if not url:
        raise ErroreExtra(400, "Manca l'indirizzo da leggere (url)")
    scadenza = time.time() + TEMPO_PROXY
    for _ in range(10):   # al più 9 rimandi, ognuno ricontrollato
        r, conn, sock = _apri_pubblico(url, scadenza)
        loc = r.getheader("Location")
        if r.status in (301, 302, 303, 307, 308) and loc:
            r.close()
            conn.close()
            url = urljoin(url, loc.strip())
            continue
        break
    else:
        raise ErroreExtra(502, "Il sito rimanda ad altri indirizzi troppe volte")
    pezzi, tot = [], 0
    try:
        if r.status >= 400:
            raise ErroreExtra(502, "Il sito ha risposto %d" % r.status)
        try:
            lun = int(r.getheader("Content-Length") or 0)
        except ValueError:
            lun = 0
        tipo = (r.getheader("Content-Type") or "").lower()
        if tipo.startswith(("audio/", "video/")) or tipo.startswith("application/octet-stream") and lun > MAX_PROXY:
            _proxy_media(h, r, sock, url, lun)
            return
        if lun > MAX_PROXY:
            raise ErroreExtra(413, "Contenuto troppo grande (oltre 15 MB)")
        while True:
            resto = scadenza - time.time()
            if resto <= 0:
                raise ErroreExtra(504, "Il sito non ha risposto in tempo")
            if sock is not None and not r.isclosed():   # a risposta finita http.client chiude già la connessione
                try:
                    sock.settimeout(resto)
                except OSError:
                    pass
            try:
                b = r.read1(65536)
            except socket.timeout:
                raise ErroreExtra(504, "Il sito non ha risposto in tempo")
            except (OSError, http.client.HTTPException):
                raise ErroreExtra(502, "Il sito ha interrotto la risposta")
            if not b:
                break
            tot += len(b)
            if tot > MAX_PROXY:
                raise ErroreExtra(413, "Contenuto troppo grande (oltre 15 MB)")
            pezzi.append(b)
    finally:
        r.close()
        conn.close()
    corpo = b"".join(pezzi)
    h._musica_risposto = True
    h.send_response(200)
    h.send_header("Content-Type", r.getheader("Content-Type") or "application/octet-stream")
    ce = r.getheader("Content-Encoding")
    if ce and ce.strip().lower() != "identity":
        h.send_header("Content-Encoding", ce)
    h.send_header("Content-Length", str(len(corpo)))
    h.send_header("Cache-Control", "no-store")
    h.send_header("X-Content-Type-Options", "nosniff")
    h.send_header("Content-Security-Policy", "sandbox")   # aperta come pagina, non può toccare i dati dell'app
    h.send_header("X-Url-Finale", quote(url, safe=":/?#[]@!$&'()*+,;=%-._~"))
    h.end_headers()
    if h.command != "HEAD":
        h.wfile.write(corpo)


def _proxy_media(h, r, sock, url, lun):
    """Audio o video letto per l'app (episodio di podcast, capitolo LibriVox): passa a pezzi, fino a 1 GB."""
    if lun > MAX_MEDIA:
        raise ErroreExtra(413, "File troppo grande (oltre 1 GB)")
    h._musica_risposto = True
    h.send_response(200)
    h.send_header("Content-Type", r.getheader("Content-Type") or "application/octet-stream")
    if lun:
        h.send_header("Content-Length", str(lun))
    else:
        h.close_connection = True   # lunghezza ignota: la fine del file è la chiusura della connessione
    h.send_header("Cache-Control", "no-store")
    h.send_header("X-Content-Type-Options", "nosniff")
    h.send_header("X-Url-Finale", quote(url, safe=":/?#[]@!$&'()*+,;=%-._~"))
    h.end_headers()
    if h.command == "HEAD":
        return
    tot = 0
    while True:
        if sock is not None and not r.isclosed():
            try:
                sock.settimeout(ATTESA_MEDIA)
            except OSError:
                pass
        try:
            b = r.read1(262144)
        except (socket.timeout, OSError, http.client.HTTPException):
            h.close_connection = True   # intestazioni già mandate: si chiude, l'app vede il file incompleto
            return
        if not b:
            return
        tot += len(b)
        if tot > MAX_MEDIA:
            h.close_connection = True
            return
        h.wfile.write(b)


# ----- richieste HTTP -----

def _leggi_corpo(h):
    if "chunked" in (h.headers.get("Transfer-Encoding") or "").lower():
        h.close_connection = True
        raise ErroreExtra(411, "Serve Content-Length")
    try:
        n = int(h.headers.get("Content-Length") or 0)
    except ValueError:
        n = -1
    if n < 0:
        h.close_connection = True
        raise ErroreExtra(400, "Content-Length non valido")
    if n > MAX_CORPO:
        h.close_connection = True   # il corpo non si legge: la connessione non si può riusare
        raise ErroreExtra(413, "Richiesta troppo grande (massimo 20 MB)")
    return h.rfile.read(n) if n else b""


def _da_json(dati):
    if not dati.strip():
        return {}
    try:
        o = json.loads(dati.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ErroreExtra(400, "Richiesta non valida: serve JSON")
    if not isinstance(o, dict):
        raise ErroreExtra(400, "Richiesta non valida: serve un oggetto JSON")
    return o


def _stessa_origine(h):
    """Le richieste che cambiano qualcosa (POST, DELETE) devono venire dall'app: un altro sito aperto nel browser
    del Mac non può avviare scaricamenti o cancellare audiolibri."""
    o = h.headers.get("Origin")
    if not o:
        return True   # non da una pagina web (curl, app)
    if o.strip() == "null":
        return False
    ammessi = {(h.headers.get("Host") or "").lower()}
    if INDIRIZZO:
        ammessi.add(urlsplit(INDIRIZZO).netloc.lower())
    return urlsplit(o.strip()).netloc.lower() in ammessi


def _instrada(h, metodo, parti, corpo):
    cosa, resto = (parti[0] if parti else ""), parti[1:]

    def solo(*metodi):
        if metodo not in metodi:
            raise ErroreExtra(405, "Metodo %s non permesso qui" % metodo)

    if cosa == "stato" and not resto:
        solo("GET")
        _json(h, stato())
    elif cosa == "proxy" and not resto:
        solo("GET")
        _proxy(h)
    elif cosa == "video" and not resto:
        solo("GET", "POST")
        if metodo == "GET":
            _json(h, {"lavori": _coda("video").elenco()})
        else:
            _json(h, {"id": _nuovo_video(corpo)})
    elif cosa == "video" and len(resto) == 2 and resto[0] == "file":
        solo("GET")
        _file_video(h, resto[1])
    elif cosa == "video" and len(resto) == 2 and resto[1] == "annulla":
        solo("POST")
        _annulla_video(resto[0])
        _json(h, {"ok": True})
    elif cosa == "voci" and not resto:
        solo("GET")
        _json(h, {"voci": voci()})
    elif cosa == "audiolibro" and not resto:
        solo("GET", "POST")
        if metodo == "GET":
            _json(h, {"lavori": _coda("libri").elenco()})
        else:
            _json(h, {"id": _nuovo_audiolibro(corpo)})
    elif cosa == "audiolibro" and len(resto) == 3 and resto[0] == "file":
        solo("GET")
        _file_capitolo(h, resto[1], resto[2])
    elif cosa == "audiolibro" and len(resto) == 1:
        solo("DELETE")
        _elimina_audiolibro(resto[0])
        _json(h, {"ok": True})
    elif cosa == "ia" and not resto:
        solo("GET", "POST")
        if metodo == "GET":
            attiva, modelli = _modelli_ia()
            _json(h, {"attiva": attiva, "modelli": modelli, "modello": _scegli_modello("", modelli)})
        else:
            _json(h, _chiedi_ia(corpo))
    elif cosa == "dispositivo" and not resto:
        solo("GET")
        _json(h, dispositivo())
    else:
        raise ErroreExtra(404, "Richiesta sconosciuta: api/extra/%s" % "/".join(parti))


def gestisci_extra(h, metodo=None):
    """API dei moduli extra: da chiamare in do_GET, do_HEAD, do_POST e do_DELETE, prima di `gestisci`.
    True se la richiesta era per api/extra/ (e ha avuto risposta)."""
    m = EXTRA.match(unquote(urlsplit(h.path).path))
    if not m:
        return False
    metodo = (metodo or h.command or "GET").upper()
    if metodo == "HEAD":
        metodo = "GET"   # stessa risposta senza corpo (_json e _invia_file guardano h.command)
    h._musica_risposto = False
    try:
        dati = _leggi_corpo(h)
        if metodo in ("POST", "DELETE", "PUT", "PATCH") and not _stessa_origine(h):
            raise ErroreExtra(403, "Richiesta da un altro sito: rifiutata")
        corpo = _da_json(dati) if metodo == "POST" else {}
        _instrada(h, metodo, [p for p in m.group(1).split("/") if p], corpo)
    except ErroreExtra as e:
        if not h._musica_risposto:
            try:
                _json(h, {"errore": e.testo}, e.codice)
            except OSError:
                h.close_connection = True
    except (BrokenPipeError, ConnectionResetError):
        h.close_connection = True
    except Exception as e:   # noqa: BLE001  l'app riceve comunque una risposta
        print("Atlante: errore su %s %s: %s" % (metodo, h.path, e), flush=True)
        if not h._musica_risposto:
            try:
                _json(h, {"errore": "Errore sul Mac: %s" % e}, 500)
            except OSError:
                pass
        h.close_connection = True
    return True


# ---------- accesso da Internet (Tailscale Funnel) ----------
#
# Dal Mac stesso, dalla rete di casa e dalla rete Tailscale (iPhone, Mac) l'app si apre come sempre, senza password:
# `tailscale serve` aggiunge a ogni richiesta l'identità di chi la fa (Tailscale-User-Login) e toglie quella falsa.
# Da Internet (`tailscale funnel`) le richieste arrivano con Tailscale-Funnel-Request e senza identità: per loro serve
# la password, che si imposta sul Mac con
#
#     python3 musica_libreria.py --imposta-password [--cache ~/.atlante-musica]
#
# Chi la inserisce riceve un biscotto firmato che vale 400 giorni e si rinnova da solo usando l'app. Lo ricevono anche
# i dispositivi della rete Tailscale: un iPhone che apre l'app con Tailscale acceso poi funziona anche senza.
# Senza password impostata da Internet non si entra. Cambiare la password fa uscire tutti i dispositivi entrati con
# quella vecchia (quelli della rete Tailscale rientrano da soli).

ACC = None                        # Accesso, preparato da main()
ACCESSO_BISCOTTO = "am_accesso"
ACCESSO_DURATA = 400 * 86400      # il massimo che Safari e Chrome tengono
ACCESSO_RINNOVO = 30 * 86400      # un biscotto con più di 30 giorni si rinnova alla prima richiesta
ACCESSO_ITER = 240000             # PBKDF2: ogni tentativo costa al Mac circa un decimo di secondo
ACCESSO_TENTATIVI = 5             # password sbagliate da uno stesso indirizzo in ACCESSO_FINESTRA, poi si aspetta
ACCESSO_TENTATIVI_TUTTI = 25      # e da tutti gli indirizzi insieme
ACCESSO_FINESTRA = 15 * 60
# senza biscotto passano solo la pagina della password e quello che serve per installare l'app (manifest, icone),
# e solo nella cartella principale dell'app: nessun altro indirizzo che finisca allo stesso modo (api/…/icon-1.png)
LIBERI = re.compile(r"^/(accesso|manifest\.webmanifest|icon-(?:maskable-)?\d+\.png|apple-touch-icon(?:-precomposed)?\.png"
                    r"|favicon\.ico)$")
RETI_TAILSCALE = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))


class Accesso:
    """Password (PBKDF2) e segreto che firma i biscotti, in <cache>/accesso.json (lo legge solo il proprietario)."""

    def __init__(self, file):
        self.file = file
        self.blocco = threading.Lock()
        self.dati, self.mtime = {}, None
        self.errori = {}    # indirizzo -> istanti delle password sbagliate
        self.tutti = []     # istanti di tutte le password sbagliate

    def _aggiorna(self):
        # la password si imposta da un altro processo (--imposta-password) mentre il server gira
        try:
            m = os.stat(self.file).st_mtime_ns
        except OSError:
            m = None
        if m != self.mtime:
            d = {}
            if m is not None:
                try:
                    with open(self.file, encoding="utf-8") as f:
                        d = json.load(f)
                except (OSError, ValueError):
                    d = {}
            self.dati, self.mtime = (d if isinstance(d, dict) else {}), m

    def _scrivi(self, d):
        os.makedirs(os.path.dirname(self.file) or ".", exist_ok=True)
        tmp = "%s.%d.tmp" % (self.file, os.getpid())
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, self.file)
        self.dati, self.mtime = d, os.stat(self.file).st_mtime_ns

    def _segreto(self):
        self._aggiorna()
        if not re.fullmatch(r"[0-9a-f]{64}", str(self.dati.get("segreto", ""))):
            self._scrivi(dict(self.dati, segreto=secrets.token_hex(32)))
        return bytes.fromhex(self.dati["segreto"])

    def ha_password(self):
        with self.blocco:
            self._aggiorna()
            return bool(self.dati.get("hash"))

    def imposta(self, password):
        sale = secrets.token_bytes(16)
        h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), sale, ACCESSO_ITER)
        with self.blocco:
            self._aggiorna()
            d = dict(self.dati, sale=sale.hex(), hash=h.hex(), iter=ACCESSO_ITER)
            if self.dati.get("hash") or not self.dati.get("segreto"):
                d["segreto"] = secrets.token_hex(32)   # password cambiata: i biscotti di prima non valgono più
            self._scrivi(d)

    def giusta(self, password):
        with self.blocco:
            self._aggiorna()
            d = dict(self.dati)
        try:
            sale, atteso, n = bytes.fromhex(d["sale"]), bytes.fromhex(d["hash"]), int(d.get("iter") or ACCESSO_ITER)
        except (KeyError, ValueError, TypeError):
            return False
        return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), sale, n), atteso)

    def biscotto(self):
        corpo = "%d.%s" % (int(time.time()) + ACCESSO_DURATA, secrets.token_hex(8))
        with self.blocco:
            k = self._segreto()
        return corpo + "." + hmac.new(k, corpo.encode("ascii"), hashlib.sha256).hexdigest()

    def scadenza(self, valore):
        """Scadenza (in secondi) di un biscotto valido; 0 se è falso, scaduto o firmato con un segreto vecchio."""
        m = re.fullmatch(r"([0-9]{1,12})\.([0-9a-f]{16})\.([0-9a-f]{64})", valore or "")
        if not m:
            return 0
        with self.blocco:
            self._aggiorna()
            s = str(self.dati.get("segreto", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", s):
            return 0
        firma = hmac.new(bytes.fromhex(s), ("%s.%s" % (m.group(1), m.group(2))).encode("ascii"), hashlib.sha256).hexdigest()
        scad, ora = int(m.group(1)), time.time()
        if not hmac.compare_digest(firma, m.group(3)) or not ora < scad <= ora + ACCESSO_DURATA + 60:
            return 0
        return scad

    def attesa(self, ip):
        """Secondi da aspettare prima di riprovare la password da questo indirizzo (0: si può subito)."""
        ora = time.time()
        with self.blocco:
            self.tutti = [t for t in self.tutti if ora - t < ACCESSO_FINESTRA]
            mie = [t for t in self.errori.get(ip, []) if ora - t < ACCESSO_FINESTRA]
            if mie:
                self.errori[ip] = mie
            else:
                self.errori.pop(ip, None)
            w = 0
            if len(mie) >= ACCESSO_TENTATIVI:
                w = max(w, mie[-ACCESSO_TENTATIVI] + ACCESSO_FINESTRA - ora)
            if len(self.tutti) >= ACCESSO_TENTATIVI_TUTTI:
                w = max(w, self.tutti[-ACCESSO_TENTATIVI_TUTTI] + ACCESSO_FINESTRA - ora)
            return int(w) + 1 if w > 0 else 0

    def sbagliata(self, ip):
        ora = time.time()
        with self.blocco:
            if len(self.errori) > 5000:   # moltissimi indirizzi diversi: resta il limite su tutti insieme
                self.errori.clear()
            self.errori.setdefault(ip, []).append(ora)
            self.tutti.append(ora)

    def riuscita(self, ip):
        with self.blocco:
            self.errori.pop(ip, None)


def _ip(testo):
    try:
        a = ipaddress.ip_address((testo or "").strip().split("%")[0])
    except ValueError:
        return None
    return a.ipv4_mapped or a if a.version == 6 else a


def _ip_tailscale(a):
    return a is not None and any(a in r for r in RETI_TAILSCALE)


def _fidato(h):
    """Richiesta dal Mac stesso, dalla rete di casa o dalla rete Tailscale: non da Internet."""
    if h.headers.get("Tailscale-Funnel-Request") is not None:
        return False
    vicino = _ip(h.client_address[0])
    xff = h.headers.get("X-Forwarded-For")
    if xff is None:   # nessun proxy davanti: il Mac stesso o, con ATLANTE_ASCOLTO=0.0.0.0, la rete di casa
        return vicino is not None and (vicino.is_loopback or vicino.is_private or vicino.is_link_local or _ip_tailscale(vicino))
    if vicino is None or not vicino.is_loopback:
        return False   # un proxy che non è tailscaled sul Mac: non si sa chi c'è dietro
    if h.headers.get("Tailscale-User-Login"):
        return True
    ultimo = _ip(xff.split(",")[-1])   # l'ultimo l'ha scritto tailscaled, quelli prima possono essere inventati
    return ultimo is not None and (ultimo.is_loopback or _ip_tailscale(ultimo))


def _ip_cliente(h):
    xff = h.headers.get("X-Forwarded-For")
    vicino = _ip(h.client_address[0])
    if xff and vicino is not None and vicino.is_loopback:
        return xff.split(",")[-1].strip()[:64]
    return h.client_address[0]


def _biscotto_valido(h):
    for parte in (h.headers.get("Cookie") or "").split(";"):
        k, _, v = parte.strip().partition("=")
        if k == ACCESSO_BISCOTTO:
            s = ACC.scadenza(v.strip())
            if s:
                return s
    return 0


def _set_cookie(h, valore):
    https = (h.headers.get("X-Forwarded-Proto") or "").lower() == "https" or isinstance(h.connection, ssl.SSLSocket)
    return "%s=%s; Max-Age=%d; Path=/; HttpOnly; SameSite=Lax%s" % (ACCESSO_BISCOTTO, valore, ACCESSO_DURATA,
                                                                  "; Secure" if https else "")


def _verso(h, dove):
    """`dove` (nella cartella dell'app) con un indirizzo relativo a questa richiesta: va bene anche sotto un prefisso."""
    return "../" * max(0, urlsplit(h.path).path.count("/") - 1) + dove


def _rimanda(h, dove):
    h.send_response(303)
    h.send_header("Location", dove)
    h.send_header("Content-Length", "0")
    h.send_header("Cache-Control", "no-store")
    h.end_headers()


_PAGINA_ACCESSO = """<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex">
<meta name="theme-color" content="#fa2d48">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Musica">
<link rel="manifest" href="@Rmanifest.webmanifest">
<link rel="apple-touch-icon" href="@Rapple-touch-icon.png?v=2">
<link rel="icon" href="@Ricon-192.png?v=2">
<title>Atlante Musica</title>
<style>
:root{--bg:#fff;--fg:#000;--fg2:rgba(60,60,67,.6);--fill:rgba(118,118,128,.12);--accent:#fa2d48;--bad:#ff3b30;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#000;--fg:#fff;--fg2:rgba(235,235,245,.6);--fill:rgba(118,118,128,.24);--accent:#fc3c54;--bad:#ff453a;color-scheme:dark}}
*{box-sizing:border-box}
html,body{background:var(--bg)}
body{margin:0;color:var(--fg);font:17px/1.4 -apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",Roboto,Arial,sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:400px;margin:0 auto;padding:max(48px,calc(env(safe-area-inset-top) + 32px)) 16px 40px;text-align:center}
.ic{width:96px;height:96px;border-radius:22px;box-shadow:0 6px 20px rgba(0,0,0,.15)}
h1{font-size:28px;margin:18px 0 6px}
p{color:var(--fg2);margin:0 0 22px;font-size:15px}
.err{color:var(--bad);font-weight:600}
form{display:flex;flex-direction:column;gap:12px}
input{font:inherit;color:inherit;width:100%;min-height:50px;padding:12px 14px;border:0;border-radius:12px;background:var(--fill);outline:none}
input:focus{outline:2px solid var(--accent)}
button{font:inherit;font-weight:600;min-height:50px;border:0;border-radius:12px;background:var(--accent);color:#fff;cursor:pointer}
.nome{position:absolute;left:-9999px;width:1px;height:1px;opacity:0}
</style>
</head>
<body>
<main>
<img class="ic" src="@Rapple-touch-icon.png?v=2" alt="">
<h1>Atlante Musica</h1>
@C
</main>
</body>
</html>
"""
_MODULO_ACCESSO = """<p>@M</p>
<form method="post" action="@Raccesso">
<input class="nome" type="text" name="nome" autocomplete="username" value="Atlante Musica" tabindex="-1" aria-hidden="true">
<input type="password" name="password" autocomplete="current-password" placeholder="Password" aria-label="Password" required autofocus>
<button type="submit">Entra</button>
</form>"""


def _scrivi_pagina_accesso(h, codice, messaggio, errore=False, modulo=True, intestazioni=()):
    m = '<span class="err">%s</span>' % messaggio if errore else messaggio
    c = _MODULO_ACCESSO.replace("@M", m) if modulo else "<p>%s</p>" % m
    corpo = _PAGINA_ACCESSO.replace("@C", c).replace("@R", _verso(h, "")).encode("utf-8")
    h.send_response(codice)
    h.send_header("Content-Type", "text/html; charset=utf-8")
    h.send_header("Content-Length", str(len(corpo)))
    h.send_header("Cache-Control", "no-store")
    for k, v in intestazioni:
        h.send_header(k, v)
    h.end_headers()
    if h.command != "HEAD":
        h.wfile.write(corpo)


def _pagina_accesso(h, entrato):
    """GET: la pagina con la password (o l'app, per chi è già dentro). POST: controlla la password."""
    dati = b""
    if h.command == "POST":
        try:
            n = int(h.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if not 0 <= n <= 4096:
            h.close_connection = True
            return _scrivi_pagina_accesso(h, 413, "Richiesta non valida.", True)
        dati = h.rfile.read(n)
    elif h.command not in ("GET", "HEAD"):
        h.close_connection = True
        return _scrivi_pagina_accesso(h, 405, "Richiesta non valida.", True)
    if entrato:
        return _rimanda(h, _verso(h, "./"))
    attiva = ACC.ha_password()
    if not attiva:
        return _scrivi_pagina_accesso(h, 403 if h.command == "POST" else 200, "L'accesso da Internet non è ancora attivo: "
                                      "la password si imposta sul Mac. Dalla rete Tailscale l'app si apre come sempre.", modulo=False)
    if h.command != "POST":
        return _scrivi_pagina_accesso(h, 200, "Da Internet la musica del Mac è protetta da una password.")
    if not _stessa_origine(h):
        return _scrivi_pagina_accesso(h, 403, "Richiesta da un altro sito: rifiutata.", True)
    ip = _ip_cliente(h)
    w = ACC.attesa(ip)
    if w:
        return _scrivi_pagina_accesso(h, 429, "Troppi tentativi sbagliati: riprova fra %d minut%s." % ((w + 59) // 60, "o" if w <= 60 else "i"),
                                      True, intestazioni=[("Retry-After", str(w))])
    try:
        pw = parse_qs(dati.decode("utf-8"), max_num_fields=10).get("password", [""])[0]
    except (UnicodeDecodeError, ValueError):
        pw = ""
    if pw and ACC.giusta(pw):
        ACC.riuscita(ip)
        h._biscotto = _set_cookie(h, ACC.biscotto())
        return _rimanda(h, _verso(h, "./"))
    ACC.sbagliata(ip)
    print("Atlante: password sbagliata da %s" % ip, flush=True)
    return _scrivi_pagina_accesso(h, 401, "Password sbagliata.", True)


def controlla_accesso(h):
    """Da chiamare per prima in do_GET, do_HEAD, do_POST e do_DELETE. True se ha già risposto: pagina della password,
    rimando lì o 401 per chi arriva da Internet senza biscotto valido. Se no la richiesta prosegue; quando serve, la
    risposta porta un biscotto nuovo (h._biscotto, scritto da end_headers)."""
    h._biscotto = None
    if ACC is None:
        return False
    p = unquote(urlsplit(h.path).path)
    libero = LIBERI.match(p)
    scad = _biscotto_valido(h)
    entrato = bool(scad) or _fidato(h)
    if entrato and (not scad or scad - time.time() < ACCESSO_DURATA - ACCESSO_RINNOVO):
        h._biscotto = _set_cookie(h, ACC.biscotto())
    if libero and libero.group(1) == "accesso":
        _pagina_accesso(h, entrato)
        return True
    if entrato or libero:
        return False
    if h.command not in ("GET", "HEAD"):
        h.close_connection = True   # corpo non letto
    elif h.headers.get("Sec-Fetch-Mode") == "navigate" or p.endswith("/") or p.endswith(".html"):
        _rimanda(h, _verso(h, "accesso"))   # una pagina: si apre quella della password
        return True
    corpo = json.dumps({"errore": "Da Internet Atlante Musica chiede la password", "accesso": True},
                       ensure_ascii=False).encode("utf-8")
    h.send_response(401)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(corpo)))
    h.send_header("Cache-Control", "no-store")
    h.send_header("X-Atlante-Accesso", "richiesto")   # l'app apre la pagina della password
    h.end_headers()
    if h.command != "HEAD":
        h.wfile.write(corpo)
    return True


def _imposta_password(file):
    if sys.stdin.isatty():
        p = getpass.getpass("Nuova password per aprire Atlante Musica da Internet: ")
        if p != getpass.getpass("Ripetila: "):
            print("Le due password non sono uguali: non ho cambiato niente.", file=sys.stderr)
            return 1
    else:
        p = sys.stdin.readline().rstrip("\r\n")
    if len(p) < 8:
        print("Password troppo corta: servono almeno 8 caratteri. Non ho cambiato niente.", file=sys.stderr)
        return 1
    Accesso(file).imposta(p)
    print("Password impostata (%s). Da Internet l'app la chiede, dalla rete Tailscale no." % file)
    return 0


def main():
    global ACC
    ap = argparse.ArgumentParser(description="Serve la musica di una o più cartelle all'app Atlante Musica.")
    ap.add_argument("cartelle", nargs="*", help="cartelle con la musica, per esempio /Volumes/Backup/Musica")
    ap.add_argument("--porta", type=int, default=8790)
    ap.add_argument("--cache", default="~/.atlante-musica", help="dove tenere indice e copertine")
    ap.add_argument("--app", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."), help="cartella dell'app (index.html)")
    ap.add_argument("--indirizzo", default="", help="indirizzo https fisso dell'app, es. https://mac-mini.tailnet.ts.net/ "
                    "(con `tailscale serve --bg 8790`): chi apre l'app in http da un altro dispositivo viene mandato lì")
    ap.add_argument("--cert", help="certificato HTTPS (es. da `tailscale cert`): il server risponde direttamente in https")
    ap.add_argument("--chiave", help="chiave privata del certificato")
    ap.add_argument("--video-dir", default=CONF["video_dir"], help="dove salvare video e audio scaricati con yt-dlp")
    ap.add_argument("--libri-dir", default=CONF["libri_dir"], help="dove salvare gli audiolibri creati con la voce del Mac")
    ap.add_argument("--ia-modello", default="", help="modello di Ollama per l'IA locale, es. llama3.2 "
                    "(predefinito: il primo installato)")
    ap.add_argument("--ia-indirizzo", default=CONF["ia_indirizzo"], help="indirizzo di Ollama sul Mac")
    ap.add_argument("--imposta-password", action="store_true", help="imposta (o cambia) la password per aprire l'app "
                    "da Internet con `tailscale funnel` e esce; la legge da tastiera o dalla prima riga dello standard input")
    a = ap.parse_args()
    file_accesso = os.path.join(os.path.expanduser(a.cache), "accesso.json")
    if a.imposta_password:
        sys.exit(_imposta_password(file_accesso))
    if not a.cartelle:
        ap.error("serve almeno una cartella di musica")
    ACC = Accesso(file_accesso)
    indirizzo = a.indirizzo.strip()
    if indirizzo and not indirizzo.endswith("/"):
        indirizzo += "/"
    avvia(a.cartelle, a.cache, indirizzo=indirizzo)
    avvia_extra(cache=a.cache, video_dir=a.video_dir, libri_dir=a.libri_dir, ia_modello=a.ia_modello.strip(),
                ia_indirizzo=a.ia_indirizzo.strip())
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
            if controlla_accesso(self):
                return
            if not gestisci_extra(self, "GET") and not gestisci(self) and not self._vai_al_sicuro():
                super().do_GET()

        def do_HEAD(self):
            if controlla_accesso(self):
                return
            if not gestisci_extra(self, "HEAD") and not gestisci(self) and not self._vai_al_sicuro():
                super().do_HEAD()

        def do_POST(self):
            if controlla_accesso(self):
                return
            if not gestisci_extra(self, "POST"):
                self.close_connection = True   # corpo non letto
                self.send_error(501, "Unsupported method ('POST')")

        def do_DELETE(self):
            if controlla_accesso(self):
                return
            if not gestisci_extra(self, "DELETE"):
                self.close_connection = True
                self.send_error(501, "Unsupported method ('DELETE')")

        def end_headers(self):
            # pagina, service worker e manifest (e le pagine dei moduli in x/ con kit.js e kit.css): il browser chiede
            # sempre al Mac se sono cambiati (304 se no). Senza, con un index.html vecchio di settimane terrebbe la sua
            # copia per giorni e un aggiornamento dell'app non arriverebbe. Le API (api/musica/, api/extra/) hanno già
            # le loro regole (no-store per i JSON) e non passano dalla copia offline del service worker.
            p = urlsplit(self.path).path
            q = unquote(p)
            if not PREFISSO.match(q) and not EXTRA.match(q) and (p.endswith("/") or p.endswith((".html", ".js", ".css", ".webmanifest"))):
                self.send_header("Cache-Control", "no-cache")
            b = getattr(self, "_biscotto", None)   # accesso da Internet: biscotto nuovo o rinnovato
            if b:
                self._biscotto = None
                self.send_header("Set-Cookie", b)
            super().end_headers()

        def log_message(self, *x):
            pass

    # ATLANTE_ASCOLTO=127.0.0.1 dietro Tailscale: dalla rete di casa nessuno deve arrivarci. La password la chiede
    # solo a chi arriva da Internet (tailscale funnel): vedi controlla_accesso
    ascolto = os.environ.get("ATLANTE_ASCOLTO", "0.0.0.0")
    ThreadingHTTPServer.request_queue_size = 128   # con 5, dietro Tailscale le copertine davano 502
    srv = ThreadingHTTPServer((ascolto, a.porta), H)
    if https:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(a.cert, a.chiave)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
    print("Atlante Musica su %s://%s:%d/%s  (Ctrl+C per fermare)" % ("https" if https else "http", ascolto, a.porta,
          "  -> dal telefono: " + indirizzo if indirizzo else ""), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
