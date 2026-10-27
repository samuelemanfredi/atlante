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
                                                            # (o dalla pagina <app>/accesso aperta con Tailscale)
    tailscale funnel --bg --https=10000 8790

Dalla rete Tailscale, dalla rete di casa e dal Mac stesso la password non serve (vedi controlla_accesso).
"""
import argparse
import array
import collections
import getpass
import hashlib
import hmac
import html
import http.client
import ipaddress
import json
import math
import mimetypes
import operator
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
        pronti = CLIP.pronti() if CLIP else {}
        with self.blocco:
            pubblici = []
            for b in self.brani.values():
                x = {k: v for k, v in b.items() if not k.startswith("_")}
                if b["id"] in self.compilation:
                    x["compilation"] = True   # solo quando è vero: il catalogo di 10 000 brani resta leggero
                if pronti and pronti.get(os.path.basename(b["_p"])) == b.get("_f"):
                    x["clip"] = True          # videoclip scaricato e allineato con questo file
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
#   GET    musica/cerca?q=&fonte=ytm|yt  brani da YouTube Music (predefinita) o YouTube -> {risultati:[...]}
#   GET    musica/ascolta/<id YouTube>   audio del brano per ascoltarlo prima (m4a, con Range)
#   POST   musica {id,...} o {url}     scarica il brano nella cartella della musica scaricata (MP3, tag) -> {id}
#   GET    musica                      elenco degli scaricamenti (`brano` = id nel catalogo quando è in libreria)
#   POST   musica/<id>/annulla         ferma lo scaricamento; uno finito esce dall'elenco (il brano resta)
#   GET    clip/<id brano>             videoclip del brano: {stato, url, mappa, durata} (se va preparato entra in coda)
#   GET    clip/file/<ID YouTube>.mp4  il videoclip (con Range)
#   GET    clip                        avanzamento della preparazione dei videoclip
#   GET    dispositivi                 dispositivi con l'app aperta (sentiti negli ultimi 60 s) e cosa suonano
#   POST   dispositivi/<id>/stato      {nome,tipo,stato} ogni pochi secondi -> {comandi:[...]} in attesa
#   GET    dispositivi/<id>/comandi?attesa=25  attesa lunga: i comandi appena arrivano
#   POST   dispositivi/<id>/comando    {azione,...} per quel dispositivo (telecomando)
#   GET    voci                      voci di sintesi del Mac, prima le italiane
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
        "musica_dir": "~/Music/Atlante Scaricati", "ia_modello": "", "ia_indirizzo": "http://127.0.0.1:11434"}
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


def avvia_extra(cache=None, video_dir=None, libri_dir=None, ia_modello=None, ia_indirizzo=None, musica_dir=None):
    """Cartelle e IA dei moduli extra (senza chiamarla valgono quelle predefinite). Le code partono alla prima richiesta."""
    for k, v in (("cache", cache), ("video_dir", video_dir), ("libri_dir", libri_dir), ("musica_dir", musica_dir),
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
            elif nome == "musica":
                _CODE[nome] = Coda(os.path.join(base, "musica.json"), 2, _scarica_musica)
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


# ----- musica da internet: ricerca, ascolto e scaricamento nella libreria -----
#
# La ricerca fa la stessa richiesta delle pagine web di YouTube Music e YouTube (youtubei/v1/search): risponde in un
# secondo con titolo, artisti, album, durata, copertina e «E» degli explicit (yt-dlp `ytsearch` impiega 14 secondi e
# da YouTube Music dà solo i titoli). Se YouTube cambia formato o non risponde si ripiega su yt-dlp.
# Ascolto e scaricamento usano lo stesso audio originale (m4a), preparato una volta sola nella cache degli ascolti;
# il brano scaricato diventa un MP3 alla qualità massima (come quelli del Cloud) con tag, copertina quadrata e
# indirizzo d'origine (purl), nella cartella della musica scaricata, che fa parte della libreria.

YOUTUBEI = {"ytm": ("https://music.youtube.com", {"clientName": "WEB_REMIX", "clientVersion": "1.20251020.01.00"},
                    "EgWKAQIIAWoKEAkQBRAKEAMQBA%3D%3D"),   # filtro «Brani»
            "yt": ("https://www.youtube.com", {"clientName": "WEB", "clientVersion": "2.20251020.01.00"}, None)}
ID_YT = re.compile(r"^[A-Za-z0-9_-]{11}$")
# solo tra parentesi o dopo un trattino («(Clean)», «- Radio Edit»): «Clean» di Taylor Swift o i Clean Bandit no
CENSURATA = re.compile(r"[(\[\-–—][^()\[\]]*?\b(clean|censored|censurat[ao]|edited|radio edit)\b", re.I)
# pezzi dei titoli dei video che non fanno parte del nome del brano
RUMORE = re.compile(r"\s*[(\[][^()\[\]]*\b(official|video|audio|lyrics?|testo|visuali[sz]er|videoclip|ufficiale|hd|4k|mv)\b"
                    r"[^()\[\]]*[)\]]", re.I)
HOST_COPERTINE = ("googleusercontent.com", "ggpht.com", "ytimg.com", "sndcdn.com", "bcbits.com")
MAX_COPERTINA = 5 * 1024 * 1024
ASCOLTI_MAX = 40       # brani pronti per l'ascolto tenuti nella cache (i più vecchi si cancellano)
TEMPO_YTDLP = 300      # secondi massimi per un audio da YouTube
# righe che yt-dlp stampa per noi (lette da _esegui_ytdlp): i campi giusti per i brani di YouTube Music e degli altri siti
_STAMPE_INFO = ["--print", "before_dl:[atlante] titolo %(track,title)s",
                "--print", "before_dl:[atlante] artista %(artist,creator,uploader,channel)s",
                "--print", "before_dl:[atlante] album %(album)s",
                "--print", "before_dl:[atlante] copertina %(thumbnail)s",
                "--print", "after_move:[atlante] file %(filepath)s"]


def _trova(o, chiave):
    """Tutti i valori di `chiave` dentro una risposta JSON, a qualsiasi profondità, nell'ordine in cui compaiono."""
    if isinstance(o, dict):
        for k, v in o.items():
            if k == chiave:
                yield v
            yield from _trova(v, chiave)
    elif isinstance(o, list):
        for v in o:
            yield from _trova(v, chiave)


def _secondi(s):
    m = re.match(r"^(?:(\d+):)?(\d+):(\d\d)$", (s or "").strip())
    return (int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))) if m else 0


def _corse(runs):
    return "".join(r.get("text", "") for r in runs or [] if isinstance(r, dict))


def _youtubei(fonte, q):
    base, client, params = YOUTUBEI[fonte]
    base = os.environ.get("ATLANTE_YOUTUBEI") or base   # le prove usano un YouTube finto
    corpo = {"context": {"client": dict(client, hl="it", gl="IT")}, "query": q}
    if params:
        corpo["params"] = params
    r = urllib.request.Request(base + "/youtubei/v1/search?prettyPrint=false", data=json.dumps(corpo).encode("utf-8"),
                               headers={"Content-Type": "application/json", "Origin": base, "User-Agent": BROWSER})
    with urllib.request.urlopen(r, timeout=15) as f:
        return json.loads(f.read(16 * 1024 * 1024).decode("utf-8"))


def _brano_ytm(it):
    """Un brano della ricerca di YouTube Music: colonne «titolo» e «artisti • album • durata»."""
    cols = [((c.get("musicResponsiveListItemFlexColumnRenderer") or {}).get("text") or {}).get("runs") or []
            for c in it.get("flexColumns") or [] if isinstance(c, dict)]
    if not cols:
        return None
    vid = next(_trova(cols[0], "videoId"), None) or next(_trova(it.get("overlay"), "videoId"), None)
    if not isinstance(vid, str) or not ID_YT.match(vid):
        return None
    parti, pezzo = [], []
    for r in (cols[1] if len(cols) > 1 else []):
        if r.get("text", "").strip() == "•":
            parti.append(pezzo)
            pezzo = []
        else:
            pezzo.append(r.get("text", ""))
    parti = [x for x in ("".join(p).strip() for p in parti + [pezzo]) if x]
    if parti and parti[0] in ("Brano", "Song", "Video", "Episodio", "Episode"):
        parti = parti[1:]
    durata = _secondi(parti[-1]) if parti else 0
    if durata:
        parti = parti[:-1]
    miniature = next(_trova(it.get("thumbnail"), "thumbnails"), None) or []
    copertina = (miniature[-1].get("url") or "") if miniature and isinstance(miniature[-1], dict) else ""
    return {"id": vid, "titolo": _corse(cols[0]).strip(), "artista": parti[0] if parti else "",
            "album": parti[1] if len(parti) > 1 else "", "durata": durata,
            "copertina": re.sub(r"=w\d+-h\d+", "=w544-h544", copertina),
            "explicit": "MUSIC_EXPLICIT_BADGE" in json.dumps(it.get("badges") or [])}


def _da_titolo_video(titolo, canale):
    """(artista, titolo) dal titolo di un video («Artista - Titolo (Official Video)») e dal nome del canale."""
    t = RUMORE.sub("", titolo or "").strip()
    if " - " in t:
        a, t = t.split(" - ", 1)
        return a.strip(), t.strip()
    canale = re.sub(r"\s*(?:- Topic|VEVO)$", "", canale or "").strip()
    return canale, t


def _brano_yt(v):
    vid = v.get("videoId")
    if not isinstance(vid, str) or not ID_YT.match(vid) or not v.get("lengthText"):
        return None   # dirette e annunci non hanno durata
    canale = _corse((v.get("ownerText") or {}).get("runs"))
    artista, titolo = _da_titolo_video(_corse((v.get("title") or {}).get("runs")), canale)
    return {"id": vid, "titolo": titolo, "artista": artista, "album": "",
            "durata": _secondi((v.get("lengthText") or {}).get("simpleText")),
            "copertina": "https://i.ytimg.com/vi/%s/mqdefault.jpg" % vid, "explicit": False}


def _cerca_ytdlp(q):
    yt = _programma("yt-dlp")
    uscita = _comando([yt, "--flat-playlist", "-J", "--", "ytsearch15:" + q], 60) if yt else ""
    try:
        voci = json.loads(uscita).get("entries") or []
    except ValueError:
        return []
    fuori = []
    for e in voci:
        if isinstance(e, dict) and ID_YT.match(str(e.get("id") or "")) and e.get("duration"):
            artista, titolo = _da_titolo_video(e.get("title"), e.get("channel") or e.get("uploader"))
            fuori.append({"id": e["id"], "titolo": titolo, "artista": artista, "album": "", "durata": int(e["duration"]),
                          "copertina": "https://i.ytimg.com/vi/%s/mqdefault.jpg" % e["id"], "explicit": False})
    return fuori


def _ordina(risultati):
    """Samuele vuole sempre la versione explicit: le censurate (clean, censored, edited, radio edit, oppure la copia
    senza «E» di un brano che c'è anche explicit) vanno in fondo, segnate. Per il resto resta l'ordine di YouTube."""
    norma = lambda s: re.sub(r"\W+", " ", (s or "").lower()).strip()   # noqa: E731
    con_e = {(norma(r["titolo"]), norma(r["artista"])) for r in risultati if r["explicit"]}
    for r in risultati:
        r["censurata"] = bool(CENSURATA.search(r["titolo"] + " " + r["album"])) or (
            not r["explicit"] and (norma(r["titolo"]), norma(r["artista"])) in con_e)
    return sorted(risultati, key=lambda r: r["censurata"])


def cerca_musica(q, fonte):
    q = " ".join(str(q or "").split())[:200]
    if not q:
        raise ErroreExtra(400, "Scrivi cosa cercare")
    if fonte not in YOUTUBEI:
        raise ErroreExtra(400, "Fonte non valida: ytm (YouTube Music) o yt (YouTube)")
    risultati, voci, errore = [], None, ""
    try:
        d = _youtubei(fonte, q)
        voci = list(_trova(d, "musicResponsiveListItemRenderer" if fonte == "ytm" else "videoRenderer"))
        risultati = [b for b in ((_brano_ytm if fonte == "ytm" else _brano_yt)(x) for x in voci if isinstance(x, dict)) if b]
    except (OSError, ValueError) as e:   # anche urllib.error.URLError e HTTPError
        errore = str(e)
    if not voci:   # nessuna risposta o formato cambiato (una ricerca senza risultati ha comunque la sua struttura)
        risultati = _cerca_ytdlp(q)
        if not risultati and errore:
            raise ErroreExtra(502, "YouTube non risponde: %s" % errore)
    visti, unici = set(), []
    for r in risultati:
        if r["id"] not in visti:
            visti.add(r["id"])
            unici.append(r)
    return {"risultati": _ordina(unici[:30]), "fonte": fonte}


def _id_da_link(url):
    m = re.match(r"^https?://(?:www\.|m\.|music\.)?(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/)|youtu\.be/)"
                 r"([A-Za-z0-9_-]{11})(?:[&?#/].*)?$", url)
    return m.group(1) if m else ""


def _cartella_ascolti():
    p = os.path.join(_cart("cache"), "extra", "ascolta")
    os.makedirs(p, exist_ok=True)
    return p


def _audio_pronto(vid):
    c = _cartella_ascolti()
    for n in os.listdir(c):
        if n.startswith(vid + ".") and not n.endswith((".part", ".tmp", ".info")):
            return os.path.join(c, n)
    return ""


def _info_ascolto(vid):
    """Titolo, artista, album e copertina letti da yt-dlp quando ha preparato l'audio (servono per i link incollati)."""
    try:
        with open(os.path.join(_cartella_ascolti(), vid + ".info"), encoding="utf-8") as f:
            o = json.load(f)
        return o if isinstance(o, dict) else {}
    except (OSError, ValueError):
        return {}


def _pulisci_ascolti():
    c = _cartella_ascolti()
    try:
        audio = sorted((e for e in os.scandir(c) if e.is_file() and not e.name.endswith(".info")),
                       key=lambda e: e.stat().st_mtime, reverse=True)
    except OSError:
        return
    for e in audio[ASCOLTI_MAX:]:
        _cancella(e.path)
        _cancella(os.path.join(c, e.name.split(".", 1)[0] + ".info"))


def _esegui_ytdlp(args, coda=None, id_=None, progresso=None):
    """yt-dlp con il registro letto riga per riga: {file, errore, e le righe «[atlante] chiave valore»}."""
    if coda is not None:
        p = coda.processo(id_, args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    else:
        p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             env=_ambiente(), start_new_session=os.name == "posix")
    tempo = threading.Timer(TEMPO_YTDLP, _termina, (p,))   # YouTube che non risponde non tiene fermo il lavoro
    tempo.daemon = True
    tempo.start()
    info = {"file": "", "errore": ""}
    try:
        for riga in p.stdout:
            r = riga.decode("utf-8", "replace").strip()
            m = re.match(r"\[download\]\s+(\d+(?:\.\d+)?)%", r)
            if m:
                if progresso:
                    progresso(float(m.group(1)))
            elif r.startswith("[atlante] "):
                k, _, v = r[10:].partition(" ")
                if v.strip() and v.strip() != "NA" and "%(" not in v:
                    info[k] = v.strip()
            elif r.startswith("ERROR:"):
                info["errore"] = r[6:].strip()
        p.wait()
    finally:
        tempo.cancel()
    if coda is not None:
        coda.controlla(id_)
    if info["file"] and not os.path.isfile(info["file"]):
        info["file"] = ""
    return info


_PREPARAZIONI = {}
_blocco_preparazioni = threading.Lock()


def _prepara_audio(vid, coda=None, id_=None, progresso=None):
    """L'audio originale del brano (m4a se c'è) nella cache degli ascolti, scaricato una volta sola: lo usano ▶︎ e ⬇︎.
    Due richieste insieme per lo stesso brano (l'iPhone ne fa più d'una, a pezzi) aspettano lo stesso scaricamento."""
    with _blocco_preparazioni:
        b = _PREPARAZIONI.setdefault(vid, threading.Lock())
    with b:
        p = _audio_pronto(vid)
        if p:
            os.utime(p)   # usato adesso: nella cache resta tra i più recenti
            return p
        yt = _programma("yt-dlp")
        if not yt:
            raise ErroreExtra(503, "yt-dlp non è installato sul Mac: brew install yt-dlp ffmpeg")
        tmp = tempfile.mkdtemp(prefix="ascolto-", dir=_cartella_tmp())
        try:
            info = {}
            # il client predefinito; se YouTube lo blocca (403), quello del telefono, come per il Cloud
            for client in ("", "mweb"):
                args = [yt, "--newline", "--progress", "--no-playlist", "--no-mtime", "-f", "ba[ext=m4a]/ba/b",
                        "-P", "home:" + tmp, "-P", "temp:" + tmp, "-o", "%(id)s.%(ext)s"] + _STAMPE_INFO
                if client:
                    args += ["--extractor-args", "youtube:player_client=" + client]
                info = _esegui_ytdlp(args + ["--", "https://music.youtube.com/watch?v=" + vid], coda, id_, progresso)
                if info["file"]:
                    break
            if not info["file"]:
                raise RuntimeError(info.get("errore") or "YouTube non ha dato l'audio del brano")
            dest = os.path.join(_cartella_ascolti(), vid + (os.path.splitext(info["file"])[1].lower() or ".m4a"))
            shutil.move(info["file"], dest)
            with open(os.path.join(_cartella_ascolti(), vid + ".info"), "w", encoding="utf-8") as f:
                json.dump({k: v for k, v in info.items() if k in ("titolo", "artista", "album", "copertina")}, f,
                          ensure_ascii=False)
            _pulisci_ascolti()
            return dest
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def _ascolta(h, vid):
    if not ID_YT.match(vid or ""):
        raise ErroreExtra(400, "Brano non valido")
    try:
        p = _prepara_audio(vid)
    except RuntimeError as e:
        raise ErroreExtra(502, "Ascolto non riuscito: %s" % e)
    tipo = TIPI_VIDEO.get(os.path.splitext(p)[1].lower()) or "application/octet-stream"
    _invia_file(h, p, tipo, {"X-Content-Type-Options": "nosniff"})


def _cartella_musica():
    """Cartella della musica scaricata; si crea solo se esiste quella che la contiene: un disco scollegato non deve
    diventare una cartella vuota sul disco interno."""
    p = _cart("musica_dir")
    if not os.path.isdir(p):
        if not os.path.isdir(os.path.dirname(p)):
            raise ErroreExtra(503, "Cartella della musica scaricata non raggiungibile (il disco è collegato?): %s" % p)
        os.makedirs(p, exist_ok=True)
    return p


def _scarica_immagine(url, dest):
    s = urlsplit(url)
    host = (s.hostname or "").lower()
    if s.scheme != "https" or not any(host == d or host.endswith("." + d) for d in HOST_COPERTINE):
        return False   # solo dai siti delle copertine: un indirizzo qualsiasi farebbe del Mac un proxy
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": BROWSER}), timeout=10)
        with r:
            if not (r.headers.get("Content-Type") or "").startswith("image/"):
                return False
            dati = r.read(MAX_COPERTINA + 1)
    except (OSError, ValueError):
        return False
    if not dati or len(dati) > MAX_COPERTINA:
        return False
    with open(dest, "wb") as f:
        f.write(dati)
    return True


def _copertina(l, info, tmp):
    """Prima la copertina quadrata di YouTube Music; se no le immagini grandi del video (16:9 senza bande nere,
    ritagliate al centro da ffmpeg), poi quella piccola data dalla ricerca o dalla pagina."""
    date = [u for u in (l.get("copertina"), info.get("copertina")) if u]
    quadrate = [u for u in date if "googleusercontent.com" in u]
    video = ["https://i.ytimg.com/vi/%s/%s.jpg" % (l["yt"], n) for n in ("maxresdefault", "hq720")] if l.get("yt") else []
    dest = os.path.join(tmp, "copertina.img")
    for u in quadrate + video + [u for u in date if u not in quadrate]:
        if _scarica_immagine(u, dest):
            return dest
    return ""


def _libero(p):
    base, est = os.path.splitext(p)
    n = 2
    while os.path.exists(p):   # il disco del Mac non distingue maiuscole e accenti: exists lo sa
        p = "%s (%d)%s" % (base, n, est)
        n += 1
    return p


def _in_libreria(percorso):
    """Id del brano nel catalogo, dopo una rilettura (la prima può essere una lettura già in corso, cominciata
    prima che il file ci fosse: allora se ne fa un'altra)."""
    if LIB is None:
        return ""
    for _ in range(2):
        LIB.scansiona()
        with LIB.blocco:
            for i, p in LIB.percorsi.items():
                if p == percorso:
                    return i
    return ""


def _mp3(ff, sorgente, copertina, tag, uscita):
    args = [ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", sorgente]
    if copertina:
        args += ["-i", copertina, "-map", "0:a:0", "-map", "1:v:0", "-c:v", "mjpeg",
                 "-filter:v", "crop='min(iw,ih)':'min(iw,ih)',scale='min(800,iw)':-2",
                 "-disposition:v:0", "attached_pic", "-metadata:s:v", "title=Album cover",
                 "-metadata:s:v", "comment=Cover (front)"]
    else:
        args += ["-map", "0:a:0"]
    args += ["-c:a", "libmp3lame", "-q:a", "0", "-map_metadata", "-1", "-id3v2_version", "3"]
    for k, v in tag.items():
        if v:
            args += ["-metadata", "%s=%s" % (k, v)]
    r = subprocess.run(args + ["-f", "mp3", uscita], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, env=_ambiente(), timeout=TEMPO_YTDLP)
    if r.returncode != 0:
        _cancella(uscita)
        raise RuntimeError("conversione non riuscita: %s" % r.stderr.decode("utf-8", "replace").strip()[-300:])


def _scarica_musica(coda, l, _dati):
    dest_dir = _cartella_musica()
    ff = _programma("ffmpeg")
    tmp = tempfile.mkdtemp(prefix="musica-", dir=_cartella_tmp())
    avanza = lambda pct: coda.aggiorna(l["id"], progresso=round(min(pct, 100) * 0.8, 1))   # noqa: E731
    try:
        if l.get("yt"):
            sorgente = _prepara_audio(l["yt"], coda, l["id"], avanza)
            info = _info_ascolto(l["yt"])
        else:   # link di un altro sito (SoundCloud, Bandcamp…): titolo, artista e copertina dalla sua pagina
            info = _esegui_ytdlp([_programma("yt-dlp"), "--newline", "--progress", "--no-playlist", "--no-mtime",
                                  "-f", "ba/b", "-P", "home:" + tmp, "-P", "temp:" + tmp, "-o", "%(id).80B.%(ext)s"] +
                                 _STAMPE_INFO + ["--", l["url"]], coda, l["id"], avanza)
            if not info["file"]:
                raise RuntimeError(info["errore"] or "yt-dlp non è riuscito a scaricare l'audio")
            sorgente = info["file"]
        coda.controlla(l["id"])
        # dalla ricerca arrivano già titolo e artista giusti; per i link si usano quelli della pagina
        titolo, artista = l.get("titolo") or "", l.get("artista") or ""
        if not titolo or titolo == l.get("url"):
            titolo = info.get("titolo") or re.sub(r"\s*\[[^\[\]]*\]$", "", os.path.splitext(os.path.basename(sorgente))[0])
            if not artista:
                artista, titolo = _da_titolo_video(titolo, info.get("artista", ""))
        album = l.get("album") or info.get("album", "")
        coda.aggiorna(l["id"], progresso=82, titolo=titolo, artista=artista, album=album)
        nome = _nome_file("%s - %s" % (artista, titolo) if artista else titolo, 180) or l["id"]
        if ff:
            finale = _libero(os.path.join(dest_dir, nome + ".mp3"))
            parziale = os.path.join(dest_dir, "." + l["id"] + ".tmp")   # col punto davanti la libreria non lo legge
            tag = {"title": titolo, "artist": artista, "album": album, "purl": l["url"],
                   "ITUNESADVISORY": "1" if l.get("explicit") else ""}
            copertina = _copertina(l, info, tmp)
            try:
                _mp3(ff, sorgente, copertina, tag, parziale)
            except RuntimeError:
                if not copertina:
                    raise
                _mp3(ff, sorgente, "", tag, parziale)   # immagine che ffmpeg non legge: senza copertina
        else:   # senza ffmpeg niente conversione: il file com'è (m4a), senza tag
            finale = _libero(os.path.join(dest_dir, nome + (os.path.splitext(sorgente)[1] or ".m4a")))
            parziale = os.path.join(dest_dir, "." + l["id"] + ".tmp")
            shutil.copyfile(sorgente, parziale)
        coda.controlla(l["id"])
        os.replace(parziale, finale)
        coda.aggiorna(l["id"], progresso=95, nome=os.path.basename(finale), dimensione=os.path.getsize(finale),
                      _percorso=finale)
        coda.aggiorna(l["id"], brano=_in_libreria(finale))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        _cancella(os.path.join(dest_dir, "." + l["id"] + ".tmp"))   # annullato o non riuscito a metà


def _nuova_musica(corpo):
    vid = str(corpo.get("id") or "").strip()
    url = str(corpo.get("url") or "").strip()
    if not vid and url:
        vid = _id_da_link(url)   # un link di YouTube vale come un risultato della ricerca
    if vid and not ID_YT.match(vid):
        raise ErroreExtra(400, "Brano non valido")
    if not vid and not re.match(r"^https?://[^\s/]+\S*$", url, re.I):
        raise ErroreExtra(400, "Serve un brano della ricerca o il link (http o https) della sua pagina")
    if not _programma("yt-dlp"):
        raise ErroreExtra(503, "yt-dlp non è installato sul Mac: brew install yt-dlp ffmpeg")
    _cartella_musica()
    coda = _coda("musica")
    if vid:   # lo stesso brano chiesto di nuovo (due tocchi, due dispositivi): lo stesso lavoro, nessun doppione
        with coda.blocco:
            for l in reversed(list(coda.lavori.values())):
                if l.get("yt") == vid and (l.get("stato") in ("in coda", "in corso") or
                                           (l.get("stato") == "fatto" and os.path.isfile(l.get("_percorso") or ""))):
                    return l["id"]
    testo = lambda k, n: str(corpo.get(k) or "").strip()[:n]   # noqa: E731
    return coda.aggiungi({"yt": vid, "url": "https://music.youtube.com/watch?v=" + vid if vid else url,
                          "titolo": testo("titolo", 300) or ("" if vid else url), "artista": testo("artista", 300),
                          "album": testo("album", 300), "copertina": testo("copertina", 1000),
                          "explicit": bool(corpo.get("explicit")), "nome": "", "dimensione": 0, "brano": ""})


def _annulla_musica(id_):
    coda = _coda("musica")
    l = coda.lavori.get(id_)
    if not l:
        raise ErroreExtra(404, "Scaricamento non trovato")
    if l.get("stato") in ("in coda", "in corso"):
        coda.annulla(id_)
    else:
        coda.rimuovi(id_)   # esce dall'elenco: il brano resta nella libreria


# ----- dispositivi: telecomando tra i dispositivi con l'app aperta (come Spotify Connect) -----
#
# Ogni app aperta (iPhone, Mac…) manda il suo stato ogni pochi secondi e tiene aperta un'«attesa lunga» per i comandi:
# un altro dispositivo vede cosa suona e la comanda (riproduci, pausa, avanti, posizione, volume) o le passa la sua
# musica (azione «suona» con la coda). Tutto in memoria: chi non si fa sentire da DISP_VIVO secondi sparisce.

DISP_VIVO = 60           # secondi: oltre, il dispositivo non compare più nell'elenco
DISP_MAX = 30            # dispositivi ricordati per persona (i più vecchi escono)
DISP_MAX_TUTTI = 300     # e in tutto
DISP_CORPO = 32 * 1024   # stato o comando, in JSON
COMANDI_MAX = 50         # comandi in attesa per dispositivo
COMANDO_SCADE = 30       # secondi: un comando non consegnato in tempo si scarta («riproduci» dieci minuti dopo no)
ATTESA_MAX = 25          # secondi dell'attesa lunga (dietro Tailscale una richiesta ferma più a lungo può essere chiusa)
AZIONI = ("toggle", "play", "pause", "next", "prev", "seek", "volume", "suona")
TIPI_DISP = ("iphone", "ipad", "android", "mac", "pc")
ID_DISP = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


NON_RAGGIUNGIBILE = "Dispositivo non raggiungibile: l'app su quel dispositivo è chiusa?"


class Dispositivi:
    """Telecomando tra i dispositivi. Ognuno è di una persona (`di`: "p" per Samuele, l'id per un invitato): l'elenco,
    i comandi e lo stato restano tra i dispositivi della stessa persona. Un id di un'altra persona risponde come un
    dispositivo che non c'è."""

    def __init__(self):
        self.cond = threading.Condition()
        self.d = {}   # id -> {di, nome, tipo, stato, visto, comandi:[(creato, comando)]}

    @staticmethod
    def _misura(o, cosa):
        if not isinstance(o, dict):
            raise ErroreExtra(400, "%s non valido: serve un oggetto" % cosa)
        if len(json.dumps(o, ensure_ascii=False)) > DISP_CORPO:
            raise ErroreExtra(413, "%s troppo grande" % cosa)

    def _vivo(self, x, ora):
        return ora - x["visto"] <= DISP_VIVO

    def _prendi(self, x, ora):
        """Comandi ancora validi, tolti dalla coda (consegnati una volta sola)."""
        c = [cmd for creato, cmd in x["comandi"] if ora - creato <= COMANDO_SCADE]
        x["comandi"] = []
        return c

    def _suo(self, x, di):
        return x is not None and x.get("di", "p") == di

    def stato(self, id_, corpo, di="p"):
        if not ID_DISP.match(id_ or ""):
            raise ErroreExtra(400, "Dispositivo non valido")
        stato = corpo.get("stato") or {}
        self._misura(stato, "Stato")
        nome = " ".join(str(corpo.get("nome") or "").split())[:40] or "Dispositivo"
        tipo = corpo.get("tipo") if corpo.get("tipo") in TIPI_DISP else "pc"
        ora = time.time()
        with self.cond:
            x = self.d.get(id_)
            if x is not None and not self._suo(x, di):
                raise ErroreExtra(404, NON_RAGGIUNGIBILE)
            if x is None:
                x = self.d[id_] = {"comandi": [], "di": di}
            x.update(nome=nome, tipo=tipo, stato=stato, visto=ora)
            # escono quelli sentiti meno di recente (mai quello appena arrivato): prima tra i dispositivi della stessa
            # persona, così uno che ne inventa tanti non fa uscire quelli degli altri
            suoi = [k for k, v in self.d.items() if v.get("di", "p") == di]
            for v in sorted(suoi, key=lambda k: self.d[k].get("visto", 0))[:max(0, len(suoi) - DISP_MAX)]:
                del self.d[v]
            if len(self.d) > DISP_MAX_TUTTI:
                for v in sorted(self.d, key=lambda k: self.d[k].get("visto", 0))[:len(self.d) - DISP_MAX_TUTTI]:
                    del self.d[v]
            return self._prendi(x, ora)

    def elenco(self, di="p"):
        ora = time.time()
        with self.cond:
            return [{"id": i, "nome": x["nome"], "tipo": x["tipo"], "fa": round(ora - x["visto"], 1), "stato": x["stato"]}
                    for i, x in sorted(self.d.items(), key=lambda kv: -kv[1]["visto"])
                    if "nome" in x and self._vivo(x, ora) and self._suo(x, di)]

    def comando(self, id_, corpo, di="p"):
        self._misura(corpo, "Comando")
        if corpo.get("azione") not in AZIONI:
            raise ErroreExtra(400, "Azione non valida: %s" % ", ".join(AZIONI))
        ora = time.time()
        with self.cond:
            x = self.d.get(id_)
            if x is None or "nome" not in x or not self._vivo(x, ora) or not self._suo(x, di):
                raise ErroreExtra(404, NON_RAGGIUNGIBILE)
            x["comandi"] = (x["comandi"] + [(ora, dict(corpo))])[-COMANDI_MAX:]
            self.cond.notify_all()

    def attendi(self, id_, secondi, di="p"):
        """Comandi per il dispositivo appena ce n'è uno (al più `secondi`); intanto il dispositivo risulta vivo."""
        if not ID_DISP.match(id_ or ""):
            raise ErroreExtra(400, "Dispositivo non valido")
        fine = time.time() + max(0.0, min(float(secondi), ATTESA_MAX))
        with self.cond:
            while True:
                ora = time.time()
                x = self.d.get(id_)
                if x is not None and not self._suo(x, di):
                    raise ErroreExtra(404, NON_RAGGIUNGIBILE)
                if x is not None:
                    x["visto"] = ora
                    c = self._prendi(x, ora)
                    if c:
                        return c
                if ora >= fine:
                    return []
                self.cond.wait(fine - ora)


DISP = Dispositivi()


def _link_invito(codice):
    if not PUBBLICO:
        raise ErroreExtra(409, "Manca l'indirizzo pubblico dell'app (ATLANTE_PUBBLICO): senza, il link non si apre da fuori")
    return PUBBLICO + "accesso/invito/" + codice


def _con_link(r):
    """Una richiesta d'invito per l'app: il link al posto del codice, e il nome di chi l'ha chiesta."""
    x = {k: v for k, v in r.items() if k != "codice"}
    if r.get("codice"):
        x["link"] = _link_invito(r["codice"])
    x["da_nome"] = PERS.nome(r["da"])
    return x


def _chi_e(h):
    return getattr(h, "_chi", None) or {"ruolo": "proprietario", "persona": None, "chiave": None}


def _gestione(h, metodo, resto, corpo):
    """Gestione → Persone: inviti, nuovi link, richieste degli invitati, revoche. Solo Samuele."""
    if _chi_e(h)["ruolo"] != "proprietario":
        raise ErroreExtra(403, "Solo Samuele può usare la Gestione")
    if metodo not in ("GET", "POST", "DELETE"):
        raise ErroreExtra(405, "Metodo %s non permesso qui" % metodo)
    if resto == ["persone"] and metodo == "GET":
        el, mio = PERS.elenco(), _chi_e(h)["chiave"]
        for x in el["persone"]:
            if x.get("da"):
                x["da_nome"] = PERS.nome(x["da"])
        for x in el["tuoi"]:
            if x["id"] == mio:
                x["attuale"] = True   # questo non si revoca da qui (409): la Gestione lo segna
        return _json(h, el)
    if resto == ["persone"] and metodo == "POST":
        persona = PERS.crea(corpo.get("nome"))
        codice, scade = PERS.invito(persona["id"])
        return _json(h, {"persona": persona, "invito": {"link": _link_invito(codice), "scade": scade}})
    if len(resto) == 3 and resto[0] == "persone" and resto[2] == "invito" and metodo == "POST":
        codice, scade = PERS.invito(resto[1])
        return _json(h, {"invito": {"link": _link_invito(codice), "scade": scade}})
    if len(resto) == 2 and resto[0] == "persone" and metodo == "DELETE":
        if not PERS.revoca_persona(resto[1]):
            raise ErroreExtra(404, "Persona sconosciuta")
        return _json(h, {"ok": True})
    if len(resto) == 2 and resto[0] == "dispositivi" and metodo == "DELETE":
        if resto[1] == _chi_e(h)["chiave"]:
            raise ErroreExtra(409, "Questo è il dispositivo che stai usando")
        if not PERS.revoca_dispositivo(resto[1]):
            raise ErroreExtra(404, "Dispositivo sconosciuto")
        return _json(h, {"ok": True})
    if resto == ["richieste"] and metodo == "GET":
        return _json(h, {"richieste": [_con_link(r) for r in PERS.richieste()]})
    if len(resto) == 3 and resto[0] == "richieste" and metodo == "POST" and resto[2] in ("consenti", "rifiuta"):
        if resto[2] == "rifiuta":
            if not PERS.rifiuta(resto[1]):
                raise ErroreExtra(404, "Richiesta sconosciuta o già decisa")
            return _json(h, {"ok": True})
        persona, codice, scade = PERS.consenti(resto[1])
        return _json(h, {"persona": persona, "invito": {"link": _link_invito(codice), "scade": scade}})
    raise ErroreExtra(404, "Richiesta sconosciuta: api/extra/gestione/%s" % "/".join(resto))


def _inviti(h, metodo, corpo):
    """«Invita un amico»: un invitato chiede, Samuele decide in Gestione; Samuele invita subito."""
    chi = _chi_e(h)
    if chi["ruolo"] == "invitato":
        if metodo == "GET":
            return _json(h, {"inviti": [_con_link(r) for r in PERS.richieste_di(chi["persona"])]})
        return _json(h, {"richiesta": _con_link(PERS.richiedi(chi["persona"], corpo.get("nome")))})
    if chi["ruolo"] != "proprietario":
        raise ErroreExtra(403, "Gli inviti li chiedono Samuele e i suoi invitati")
    if metodo == "GET":
        return _json(h, {"inviti": []})
    persona = PERS.crea(corpo.get("nome"))
    codice, scade = PERS.invito(persona["id"])
    return _json(h, {"persona": persona, "invito": {"link": _link_invito(codice), "scade": scade}})


def _di(h):
    """Di chi sono i dispositivi di questa richiesta: "p" (Samuele, anche dalla rete di casa) o l'id dell'invitato."""
    chi = getattr(h, "_chi", None)
    return chi["persona"] if chi and chi["ruolo"] == "invitato" else "p"


def _attesa(h):
    try:
        return float((parse_qs(urlsplit(h.path).query).get("attesa") or ["0"])[0])
    except ValueError:
        raise ErroreExtra(400, "Attesa non valida")


# ----- videoclip sincronizzati con i brani -----
#
# Nel lettore dell'app, al posto della copertina, il videoclip ufficiale del brano, muto: si sente il file della
# libreria (per esempio la versione explicit) e il video lo segue istante per istante. I videoclip non hanno sempre gli
# stessi tempi del brano (introduzione, pezzi aggiunti o tagliati, finale diverso, audio un po' più lento). Il confronto
# usa tre bande del suono (bassi, voce e melodia, acuti: la voce distingue un ritornello dall'altro anche quando il giro
# di batteria si ripete uguale), finestre di 10 s ogni 7,5 s del brano e, per ognuna, quanto si somiglia il video a ogni
# ritardo entro ±60 s. Un percorso con continuità (Viterbi: i videoclip cambiano ritardo di rado) sceglie per ogni
# finestra uno dei ritardi candidati o «nessuno»; le finestre consecutive sullo stesso ritardo diventano tratti con una
# retta «video = brano + sp + k·(brano − da)» (k: deriva lenta), i cui bordi si cercano con prove di 4 s.
# Dove nessun tratto è sicuro si mostra la copertina: meglio niente video che un video fuori sincrono.
# Elenco dei videoclip: un JSON {nome del file: {"id": ID YouTube}} (--clip-piano / ATLANTE_CLIP_PIANO); i video
# (MP4 fino a 1080p, H.264, con il loro audio che serve all'allineamento) e le mappe in --clip-dir / ATLANTE_CLIP_DIR.

CLIP_HZ = 8000            # campioni al secondo delle tre bande
CLIP_FILTRI = ("[0:a]aformat=sample_rates=8000:channel_layouts=mono,asplit=3[a][b][c];[a]lowpass=f=250[l];"
               "[b]highpass=f=250,lowpass=f=2000[m];[c]highpass=f=2000[h];[l][m][h]join=inputs=3:channel_layout=3.0[o]")
CLIP_PASSO = 80           # campioni per punto (10 ms)
CLIP_GROSSO = 5           # punti fini per punto grossolano (50 ms) nella ricerca larga
CLIP_FINESTRA = 10.0      # secondi di brano per ogni confronto
CLIP_OGNI = 7.5           # secondi tra l'inizio di una finestra e la successiva
CLIP_RITARDO = 60.0       # secondi massimi di differenza cercati (introduzioni lunghe dei videoclip)
CLIP_VICINO = 0.5         # secondi: un ritardo candidato vale per una finestra se il suo picco è entro questa distanza
CLIP_SOGLIA = 0.4         # correlazione sopra la quale una finestra «sta» su un ritardo
CLIP_CAMBIO = 0.35        # costo nel percorso di un piccolo cambio di ritardo (pochi secondi aggiunti o tolti nel video)
CLIP_SALTO = 0.7          # costo di un salto grande (oltre CLIP_SALTO_S): un ritornello uguale più avanti o più indietro
CLIP_SALTO_S = 5.0
CLIP_CAMBIO_NESSUNO = 0.15  # costo di un passaggio tra un ritardo e «nessuno»
CLIP_CANDIDATI = 8        # ritardi candidati al più
CLIP_DERIVA = 0.01        # deriva massima tra brano e video (1%)
CLIP_COPERTURA = 0.5      # parte del brano che i tratti devono coprire
CLIP_BORDO = 0.35         # bordo di un tratto: dove la somiglianza scende sotto questa parte di quella tipica del tratto
CLIP_VERSIONE = 2         # versione dell'allineamento: le schede fatte con una versione diversa si rifanno
CLIP_SPAZIO_MIN = 50 * 1024 ** 3
CLIP_RIPROVA = 86400      # un videoclip non riuscito per un errore temporaneo si riprova dopo un giorno
ID_CLIP = re.compile(r"^([A-Za-z0-9_-]{11})\.mp4$")


def _caratteristiche(bande):
    """Per ogni banda l'energia ogni 10 ms in scala logaritmica meno la sua media dell'ultimo secondo: restano colpi e
    frasi di voce e melodia, non il volume generale (il video ha spesso un altro missaggio, più forte)."""
    out = []
    for pcm in bande:
        n, en = CLIP_PASSO, []
        for i in range(0, len(pcm) - n + 1, n):
            q = pcm[i:i + n]
            en.append(math.log1p(sum(map(operator.mul, q, q)) / n))
        acc, f = 0.0, []
        for i, x in enumerate(en):
            acc += x
            if i >= 100:
                acc -= en[i - 100]
            f.append(x - acc / min(i + 1, 100))
        out.append(f)
    return out


def _riduci(x, k):
    return [sum(x[i:i + k]) / k for i in range(0, len(x) - k + 1, k)]


def _prefissi(v):
    p, p2, s, s2 = [0.0], [0.0], 0.0, 0.0
    for x in v:
        s += x
        s2 += x * x
        p.append(s)
        p2.append(s2)
    return p, p2


def _correlazioni(w, v, inizio, da, a, p, p2):
    """Correlazione normalizzata della finestra w con v[inizio+L : inizio+L+len(w)] per L da `da` ad `a` (escluso).
    Banda muta nella finestra: 0 (non dice niente); ritardo fuori dal video: -1."""
    m = len(w)
    mw = sum(w) / m
    wc = [x - mw for x in w]
    dw = math.sqrt(sum(x * x for x in wc))
    if dw <= 1e-9:
        return [0.0] * (a - da)
    out = []
    for L in range(da, a):
        s = inizio + L
        if s < 0 or s + m > len(v):
            out.append(-1.0)
            continue
        somma = p[s + m] - p[s]
        var = (p2[s + m] - p2[s]) - somma * somma / m
        out.append(sum(map(operator.mul, wc, v[s:s + m])) / (dw * math.sqrt(var)) if var > 1e-9 else 0.0)
    return out


def _curva(wb, vb, inizio, da, a, pref):
    """Correlazione media sulle bande."""
    tot = None
    for w, v, (p, p2) in zip(wb, vb, pref):
        c = _correlazioni(w, v, inizio, da, a, p, p2)
        tot = c if tot is None else [x + y for x, y in zip(tot, c)]
    return [x / len(wb) for x in tot]


def _picchi(c, quanti, distanza):
    """I picchi più alti di una curva, lontani tra loro almeno `distanza` punti."""
    scelti = []
    for j in sorted(range(len(c)), key=c.__getitem__, reverse=True):
        if all(abs(j - s) > distanza for s in scelti):
            scelti.append(j)
            if len(scelti) == quanti:
                break
    return scelti


def _normali(bande):
    """Bande con media 0 e scarto 1 (per la somiglianza punto per punto)."""
    out = []
    for x in bande:
        m = sum(x) / len(x) if x else 0.0
        d = math.sqrt(sum((y - m) ** 2 for y in x) / len(x)) if x else 0.0
        out.append([(y - m) / d for y in x] if d > 1e-9 else [0.0] * len(x))
    return out


def _somiglianza(nb, nv, i0, i1, lag):
    """Somiglianza di brano e video spostato (lag(t) secondi) per i punti da i0 a i1 (10 ms), media su 1 s centrata:
    vicina allo scarto comune dove coincidono, intorno a 0 dove no."""
    grezza = []
    for i in range(i0, i1):
        j = i + int(round(lag(i / 100) * 100))
        grezza.append(sum(b[i] * v[j] for b, v in zip(nb, nv)) / len(nb) if 0 <= j < len(nv[0]) else -1.0)
    p, _ = _prefissi(grezza)
    return [(p[min(len(grezza), k + 51)] - p[max(0, k - 50)]) / (min(len(grezza), k + 51) - max(0, k - 50))
            for k in range(len(grezza))]


def allinea(car_brano, car_video):
    """Mappa a tratti dal tempo del brano al tempo del video: {mappa:[{da, a, sp, k}], copertura, finestre, durata}.
    car_brano e car_video: le bande di _caratteristiche (punti ogni 10 ms)."""
    fb, fv = car_brano, car_video
    nb, nv = _normali(fb), _normali(fv)
    gb, gv = [_riduci(x, CLIP_GROSSO) for x in fb], [_riduci(x, CLIP_GROSSO) for x in fv]
    pref = [_prefissi(x) for x in gv]
    hz_g = 100 // CLIP_GROSSO
    durata, durata_v = len(fb[0]) / 100, len(fv[0]) / 100
    mg, rg, vic = int(CLIP_FINESTRA * hz_g), int(CLIP_RITARDO * hz_g), int(CLIP_VICINO * hz_g)
    finestre, t = [], 0.0
    while t + CLIP_FINESTRA <= durata + 1e-9:
        ig = int(round(t * hz_g))
        wb = [x[ig:ig + mg] for x in gb]
        if len(wb[0]) == mg and any(max(w) - min(w) > 1e-6 for w in wb):   # le finestre mute non dicono niente
            finestre.append((t, _curva(wb, gv, ig, -rg, rg + 1, pref)))
        t += CLIP_OGNI
    if not finestre:
        return {"mappa": [], "copertura": 0.0, "finestre": [], "durata": round(durata, 2)}
    # ritardi candidati: i picchi più alti di ogni finestra, raggruppati entro mezzo secondo, pesati con la correlazione
    picchi = sorted(((j - rg) / hz_g, c[j]) for _, c in finestre for j in _picchi(c, 3, hz_g) if c[j] >= CLIP_SOGLIA)
    gruppi = []
    for lag, v in picchi:
        if gruppi and lag - gruppi[-1]["ultimo"] <= CLIP_VICINO:
            g = gruppi[-1]
            g["peso"] += v
            g["somma"] += lag * v
            g["ultimo"] = lag
        else:
            gruppi.append({"peso": v, "somma": lag * v, "ultimo": lag})
    candidati = [g["somma"] / g["peso"] for g in sorted(gruppi, key=lambda g: -g["peso"])[:CLIP_CANDIDATI]]
    if not candidati:
        return {"mappa": [], "copertura": 0.0, "finestre": [], "durata": round(durata, 2)}
    idx = [int(round(L * hz_g)) + rg for L in candidati]

    def punteggio(c, j):
        return max(c[max(0, j - vic):j + vic + 1]) - CLIP_SOGLIA

    S = [[punteggio(c, j) for j in idx] for _, c in finestre]
    # percorso con continuità (Viterbi): stato 0 = nessun ritardo, stati 1..n = i candidati
    n = len(candidati)

    def costo(p, s):
        if p == s:
            return 0.0
        if p == 0 or s == 0:
            return CLIP_CAMBIO_NESSUNO
        return CLIP_SALTO if abs(candidati[p - 1] - candidati[s - 1]) > CLIP_SALTO_S else CLIP_CAMBIO
    meglio = [0.0] + S[0]
    indietro = []
    for w in range(1, len(finestre)):
        nuovo, da_dove = [], []
        for s in range(n + 1):
            v, p = max((meglio[p] - costo(p, s), p) for p in range(n + 1))
            nuovo.append(v + (0.0 if s == 0 else S[w][s - 1]))
            da_dove.append(p)
        meglio = nuovo
        indietro.append(da_dove)
    s = max(range(n + 1), key=meglio.__getitem__)
    percorso = [s]
    for da_dove in reversed(indietro):
        s = da_dove[s]
        percorso.append(s)
    percorso.reverse()
    # ritardo preciso di ogni finestra del percorso: picco vicino al candidato, poi ogni 10 ms (±0,1 s)
    prefin = [_prefissi(x) for x in fv]
    trovate = []
    for (t, c), s in zip(finestre, percorso):
        if not s:
            trovate.append(None)
            continue
        j0 = idx[s - 1]
        lo = max(0, j0 - vic)
        j = lo + max(range(len(c[lo:j0 + vic + 1])), key=c[lo:j0 + vic + 1].__getitem__)
        lf = (j - rg) * CLIP_GROSSO
        i_f = int(round(t * 100))
        mf = int(CLIP_FINESTRA * 100)
        cf = _curva([x[i_f:i_f + mf] for x in fb], fv, i_f, lf - 10, lf + 11, prefin)
        bf = max(range(len(cf)), key=cf.__getitem__)
        trovate.append({"t": t, "sp": (lf - 10 + bf) / 100, "c": round(c[j], 3), "s": s})
    # tratti: finestre consecutive sullo stesso candidato (almeno due), retta del ritardo con i minimi quadrati
    tratti, corrente = [], []
    for f in trovate + [None]:
        if f and corrente and f["s"] == corrente[-1]["s"]:
            corrente.append(f)
            continue
        if len(corrente) >= 2:
            tratti.append(corrente)
        corrente = [f] if f else []
    mappa, limite = [], 0.0
    for k, tr in enumerate(tratti):
        xs = [f["t"] + CLIP_FINESTRA / 2 for f in tr]
        ys = [f["sp"] for f in tr]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        vx = sum((x - mx) ** 2 for x in xs)
        pend = max(-CLIP_DERIVA, min(CLIP_DERIVA, sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vx)) if vx else 0.0
        if xs[-1] - xs[0] < 30:
            pend = 0.0   # tratto corto: troppo poco per misurare una deriva
        lag = lambda t, mx=mx, my=my, pend=pend: my + pend * (t - mx)   # noqa: E731
        prossimo = tratti[k + 1][0]["t"] if k + 1 < len(tratti) else durata
        sx = max(limite, 0.0)
        dx = min(durata, prossimo + CLIP_FINESTRA / 2 if k + 1 < len(tratti) else durata)
        da0, a0 = max(sx, tr[0]["t"]), min(dx, tr[-1]["t"] + CLIP_FINESTRA)
        i0, i1 = int(sx * 100), int(dx * 100)
        sim = _somiglianza(nb, nv, i0, i1, lag)
        centro = sorted(sim[int(da0 * 100) - i0:int(a0 * 100) - i0]) or [0.0]
        soglia = CLIP_BORDO * centro[len(centro) // 2]   # un terzo della somiglianza tipica del tratto
        ok = lambda t: soglia > 0 and 0 <= int(t * 100) - i0 < len(sim) and sim[int(t * 100) - i0] >= soglia   # noqa: E731
        da, a = da0, a0
        while da < a and da - da0 < CLIP_FINESTRA and not ok(da):   # finestra a cavallo di un taglio: si restringe
            da += 0.01
        while da - 0.01 >= sx and ok(da - 0.01):                       # si allarga finché coincide
            da -= 0.01
        while a > da and a0 - a < CLIP_FINESTRA and not ok(a - 0.01):
            a -= 0.01
        while a + 0.01 <= dx and ok(a):
            a += 0.01
        # dentro il video: tempo del video tra 0 e la sua fine
        while da < a and da + lag(da) < 0:
            da += 0.5
        while a > da and a + lag(a) > durata_v:
            a -= 0.5
        if a - da >= CLIP_FINESTRA:
            mappa.append({"da": round(da, 2), "a": round(a, 2), "sp": round(lag(da), 3), "k": round(pend, 5)})
            limite = a
    copertura = sum(x["a"] - x["da"] for x in mappa) / durata if durata else 0.0
    return {"mappa": mappa, "copertura": round(copertura, 3), "finestre": [f for f in trovate if f],
            "durata": round(durata, 2)}


def _bande(percorso, ff):
    """Le tre bande (bassi, voce e melodia, acuti) dell'audio di un file, a CLIP_HZ, 16 bit."""
    r = subprocess.run([ff, "-v", "error", "-nostdin", "-i", percorso, "-vn", "-filter_complex", CLIP_FILTRI,
                        "-map", "[o]", "-f", "s16le", "-"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, env=_ambiente(), timeout=900)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("audio non leggibile: %s" % r.stderr.decode("utf-8", "replace").strip()[-200:])
    a = array.array("h")
    a.frombytes(r.stdout[:len(r.stdout) // 6 * 6])
    if sys.byteorder == "big":
        a.byteswap()
    return [a[0::3], a[1::3], a[2::3]]


class Videoclip:
    """Elenco dei videoclip, coda di preparazione (scaricamento + allineamento, uno alla volta) e mappe pronte."""

    def __init__(self, piano, cartella, auto=False, prima=""):
        self.file_piano, self.dir, self.auto, self.prima = piano, cartella, auto, prima
        self.blocco = threading.Lock()
        self.piano, self.piano_m = {}, None
        self.richiesti = collections.deque()   # chiesti dall'app: passano davanti a tutto
        self.ora = ""                           # nome del brano in preparazione
        self.sveglia = threading.Event()
        self._pronti = None                     # {nome del file: firma del file audio allineato}
        self.avviato = False

    def leggi_piano(self):
        try:
            m = os.path.getmtime(self.file_piano)
        except OSError:
            return self.piano
        if m != self.piano_m:
            try:
                with open(self.file_piano, encoding="utf-8") as f:
                    d = json.load(f)
                self.piano = {n: v["id"] for n, v in d.items()
                              if isinstance(v, dict) and isinstance(v.get("id"), str) and ID_YT.match(v["id"])}
                self.piano_m = m
            except (OSError, ValueError, AttributeError):
                pass
        return self.piano

    def _scheda(self, vid):
        try:
            with open(os.path.join(self.dir, vid + ".json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def pronti(self):
        with self.blocco:
            if self._pronti is None:
                self._pronti = {}
                try:
                    for e in os.scandir(self.dir):
                        if e.name.endswith(".json"):
                            s = self._scheda(e.name[:-5])
                            if s and s.get("stato") == "pronto" and s.get("nome") and s.get("versione") == CLIP_VERSIONE:
                                self._pronti[s["nome"]] = s.get("firma")
                except OSError:
                    pass
            return self._pronti

    def stato(self, percorso, firma):
        """Per un brano della libreria: com'è il suo videoclip (e lo mette in coda se va preparato)."""
        nome = os.path.basename(percorso)
        vid = self.leggi_piano().get(nome)
        if not vid:
            return {"stato": "nessuno"}
        s = self._scheda(vid) or {}
        if s.get("nome") == nome and s.get("firma") == firma and s.get("versione") == CLIP_VERSIONE:
            if s.get("stato") == "pronto" and os.path.isfile(os.path.join(self.dir, vid + ".mp4")):
                return {"stato": "pronto", "url": "clip/file/%s.mp4" % vid, "mappa": s.get("mappa", []),
                        "durata": s.get("durata_video", 0)}
            if s.get("stato") == "non allineato":
                return {"stato": "non allineato"}
            if s.get("stato") == "errore" and (s.get("permanente") or time.time() - s.get("quando", 0) < CLIP_RIPROVA):
                return {"stato": "errore", "errore": s.get("errore", "")}
        self.chiedi(nome)
        return {"stato": "in preparazione" if self.ora == nome else "in coda"}

    def chiedi(self, nome):
        with self.blocco:
            if nome != self.ora and nome not in self.richiesti:
                self.richiesti.appendleft(nome)   # l'ultimo chiesto (il brano che si ascolta adesso) prima di tutti
        self.avvia()
        self.sveglia.set()

    def avanzamento(self):
        p = self.leggi_piano()
        try:
            libero = shutil.disk_usage(self.dir if os.path.isdir(self.dir) else os.path.dirname(self.dir)).free
        except OSError:
            libero = 0
        return {"attivo": bool(p), "auto": self.auto, "totale": len(p), "pronti": len(self.pronti()),
                "in_coda": len(self.richiesti), "ora": self.ora, "spazio_libero_gb": round(libero / 1024 ** 3, 1)}

    def avvia(self):
        with self.blocco:
            if self.avviato:
                return
            self.avviato = True
        threading.Thread(target=self._lavora, daemon=True).start()

    def _ordine(self):
        """Tutti i videoclip in ordine di preparazione: prima quelli della playlist indicata (Brani preferiti)."""
        p = self.leggi_piano()
        primi = []
        if self.prima and os.path.isdir(self.prima):
            try:
                for e in sorted(os.scandir(self.prima), key=lambda e: e.name):
                    n = os.path.basename(os.path.realpath(e.path)) if e.is_symlink() else e.name
                    if n in p and n not in primi:
                        primi.append(n)
            except OSError:
                pass
        visti = set(primi)
        return primi + [n for n in sorted(p) if n not in visti]

    def _da_fare(self, nome, percorsi):
        vid = self.leggi_piano().get(nome)
        percorso = percorsi.get(nome)
        if not vid or not percorso:
            return False
        try:
            st = os.stat(percorso)
        except OSError:
            return False
        s = self._scheda(vid) or {}
        if (s.get("nome") == nome and s.get("firma") == "%d:%d" % (st.st_size, int(st.st_mtime))
                and s.get("versione") == CLIP_VERSIONE):
            if s.get("stato") in ("pronto", "non allineato"):
                return False
            if s.get("stato") == "errore" and (s.get("permanente") or time.time() - s.get("quando", 0) < CLIP_RIPROVA):
                return False
        return True

    def _lavora(self):
        ordine, i, rifatto = [], 0, 0
        while True:
            nome = None
            percorsi = {os.path.basename(p): p for p in (dict(LIB.percorsi) if LIB else {}).values()}
            with self.blocco:
                while self.richiesti and not nome:
                    n = self.richiesti.popleft()
                    if self._da_fare(n, percorsi):
                        nome = n
            if not nome and self.auto:
                if time.time() - rifatto > 3600:   # l'elenco può cambiare (brani nuovi): si rifà ogni ora
                    ordine, i, rifatto = self._ordine(), 0, time.time()
                while i < len(ordine) and not nome:
                    if self._da_fare(ordine[i], percorsi):
                        nome = ordine[i]
                    i += 1
            if not nome:
                self.sveglia.wait(600)
                self.sveglia.clear()
                continue
            try:
                pieno = shutil.disk_usage(self.dir if os.path.isdir(self.dir) else os.path.dirname(self.dir)).free < CLIP_SPAZIO_MIN
            except OSError:
                pieno = True   # disco scollegato
            if pieno:
                print("Videoclip: disco dei videoclip pieno (meno di 50 GB liberi) o scollegato, aspetto", flush=True)
                with self.blocco:
                    if nome not in self.richiesti:
                        self.richiesti.appendleft(nome)   # il brano chiesto non si perde: torna davanti
                self.sveglia.wait(1800)
                self.sveglia.clear()
                continue
            self.ora = nome
            try:
                self.prepara(nome, percorsi[nome])
            except Exception as e:   # noqa: BLE001  un videoclip che non va non ferma gli altri
                print("Videoclip: %s non riuscito: %s" % (nome, e), flush=True)
            finally:
                self.ora = ""
            time.sleep(1)

    def _scarica(self, vid):
        yt, ff = _programma("yt-dlp"), _programma("ffmpeg")
        if not yt or not ff:
            raise RuntimeError("servono yt-dlp e ffmpeg sul Mac")
        tmp = tempfile.mkdtemp(prefix="clip-", dir=_cartella_tmp())
        try:
            info = {}
            for client in ("", "mweb"):
                args = [yt, "--newline", "--no-playlist", "--no-mtime", "-P", "home:" + tmp, "-P", "temp:" + tmp,
                        "-o", "%(id)s.%(ext)s", "--ffmpeg-location", ff, "--print", "after_move:[atlante] file %(filepath)s"]
                args += _formato("video", "1080", True)
                if client:
                    args += ["--extractor-args", "youtube:player_client=" + client]
                info = _esegui_ytdlp(args + ["--", "https://www.youtube.com/watch?v=" + vid])
                if info["file"]:
                    break
            if not info["file"]:
                raise RuntimeError(info.get("errore") or "scaricamento non riuscito")
            dest = os.path.join(self.dir, vid + ".mp4")
            shutil.move(info["file"], dest + ".tmp")
            os.replace(dest + ".tmp", dest)
            return dest
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def prepara(self, nome, percorso):
        """Scarica il videoclip (se non c'è già) e lo allinea con il file del brano; scrive la scheda <ID>.json."""
        vid = self.leggi_piano()[nome]
        if not os.path.isdir(self.dir):
            if not os.path.isdir(os.path.dirname(self.dir)):
                raise RuntimeError("cartella dei videoclip non raggiungibile: %s" % self.dir)
            os.makedirs(self.dir, exist_ok=True)
        st = os.stat(percorso)
        scheda = {"id": vid, "nome": nome, "firma": "%d:%d" % (st.st_size, int(st.st_mtime)), "quando": int(time.time()),
                  "versione": CLIP_VERSIONE}
        video = os.path.join(self.dir, vid + ".mp4")
        try:
            if not os.path.isfile(video):
                video = self._scarica(vid)
            ff = _programma("ffmpeg")
            if not ff:
                raise RuntimeError("serve ffmpeg sul Mac")
            r = allinea(_caratteristiche(_bande(percorso, ff)), _caratteristiche(_bande(video, ff)))
            scheda.update(mappa=r["mappa"], copertura=r["copertura"], finestre=len(r["finestre"]),
                          durata_video=round(_durata_audio(video) or 0, 2))
            scheda["stato"] = "pronto" if r["copertura"] >= CLIP_COPERTURA else "non allineato"
        except Exception as e:   # noqa: BLE001
            testo = str(e)
            scheda.update(stato="errore", errore=testo[:300],
                          permanente=bool(re.search(r"Sign in to confirm your age|Video unavailable|Private video|"
                                                    r"removed|copyright|not available in your country", testo, re.I)))
        with open(os.path.join(self.dir, vid + ".json.tmp"), "w", encoding="utf-8") as f:
            json.dump(scheda, f, ensure_ascii=False)
        os.replace(os.path.join(self.dir, vid + ".json.tmp"), os.path.join(self.dir, vid + ".json"))
        with self.blocco:
            if self._pronti is not None:
                if scheda["stato"] == "pronto":
                    self._pronti[nome] = scheda["firma"]
                else:
                    self._pronti.pop(nome, None)
        print("Videoclip: %s → %s (copertura %s)" % (nome, scheda["stato"], scheda.get("copertura", "-")), flush=True)
        return scheda


CLIP = None


def _clip_brano(id_):
    if not CLIP:
        raise ErroreExtra(404, "Videoclip non attivi su questo Mac")
    p = LIB.percorsi.get(id_) if LIB else None
    if not p:
        raise ErroreExtra(404, "Brano non trovato")
    try:
        st = os.stat(p)
    except OSError:
        raise ErroreExtra(404, "File del brano non raggiungibile")
    return CLIP.stato(p, "%d:%d" % (st.st_size, int(st.st_mtime)))


def _clip_file(h, nome):
    m = ID_CLIP.match(nome or "")
    if not CLIP or not m:
        raise ErroreExtra(404, "Videoclip non trovato")
    p = os.path.join(CLIP.dir, m.group(1) + ".mp4")
    if not os.path.isfile(p):
        raise ErroreExtra(404, "Videoclip non ancora scaricato")
    _invia_file(h, p, "video/mp4", {"X-Content-Type-Options": "nosniff"})


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
            "video_dir": _cart("video_dir"), "libri_dir": _cart("libri_dir"), "musica_dir": _cart("musica_dir"),
            "sistema": _sistema(),
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


# ----- interfaccia: strato di modifiche sopra l'app (base di Samuele, personale di ciascuno) -----
#
# L'app si disegna come sempre (l'originale); sopra vanno la base di Samuele e lo strato personale di chi la usa: per
# ogni proprietà di un elemento vale il livello più in alto. Qui gli strati si tengono e si controllano, li applica l'app.
# Un elemento si indica con il suo «indirizzo» (UI_INDIRIZZO) e le proprietà sono poche, con valori in una forma rigida:
# niente CSS né HTML liberi, così un valore assurdo non arriva mai alle app degli altri.
#
#   GET    interfaccia                         {chi, base, personale}; a Samuele anche la bozza
#   GET    interfaccia/base                    {base}: la base pubblicata, per tutti
#   POST   interfaccia/personale {modifiche}   sostituisce lo strato personale di chi lo chiede
#   GET/POST/DELETE interfaccia/bozza          (Samuele) la bozza della base, che vede solo lui
#   POST   interfaccia/pubblica                (Samuele) la bozza diventa la base di tutti, come versione nuova
#   GET    interfaccia/versioni                (Samuele) le ultime 10 versioni pubblicate
#   POST   interfaccia/versioni/<n>/ripristina (Samuele) una versione vecchia torna la base (come versione nuova)

UI = None                         # Interfaccia, preparata alla prima richiesta (cartella interfaccia/ nella cache)
UI_MAX = 500                      # modifiche per livello: ogni proprietà di ogni elemento ne conta una
UI_VERSIONI = 10
UI_TESTO_MAX = 80
UI_INDIRIZZO_MAX = 400
UI_CORPO_MAX = 1024 * 1024        # 500 modifiche con indirizzi e testi lunghi stanno in meno di 300 KB
UI_GRANDEZZA = (70, 200)          # per cento
UI_ORDINE = 20                    # posti di cui un elemento si sposta, prima (-) o dopo (+), tra quelli del suo gruppo
# colori per nome: l'app li traduce (quelli del tema cambiano con chiaro e scuro); «originale» toglie il colore di sotto
UI_TAVOLOZZA = ("originale", "accento", "principale", "secondario", "fondo", "fondo2", "rosso", "arancione", "giallo",
                "verde", "menta", "azzurro", "blu", "viola", "rosa", "marrone", "grigio", "nero", "bianco")
UI_COLORE = re.compile(r"#[0-9a-f]{6}")
# indirizzo: da 1 a 7 pezzi separati da uno spazio, dal contenitore con un id fino all'elemento. Ogni pezzo: tag, #id,
# al più 4 classi, poi [a=…][tab=…][v=…][k=…][sez=…] in quest'ordine (data-a, data-tab, data-v, data-k dei riquadri di
# Altro, sezione della pagina); in fondo, solo per l'elemento, la posizione tra i fratelli uguali (:2)
_UI_VAL = r"[A-Za-z0-9_-]{1,40}"
_UI_PEZZO = (r"[a-z][a-z0-9]{0,9}(?:#[A-Za-z][A-Za-z0-9_-]{0,39})?(?:\.[A-Za-z][A-Za-z0-9_-]{0,29}){0,4}" +
             "".join(r"(?:\[%s=%s\])?" % (n, _UI_VAL) for n in ("a", "tab", "v", "k", "sez")))
UI_INDIRIZZO = re.compile(r"%s(?: %s){0,6}(?::[1-9][0-9]?)?" % (_UI_PEZZO, _UI_PEZZO))
UI_CONTROLLO = re.compile("[\x00-\x1f\x7f​-‏ -‮⁠-⁩﻿]")   # anche i segni che girano il testo


def _ui_valore(p, v):
    """(True, valore pulito) oppure (False, perché no)."""
    if p == "testo":   # "" = il testo originale, anche sopra quello della base
        if not isinstance(v, str):
            return False, "Testo non valido"
        t = " ".join(UI_CONTROLLO.sub(" ", v).split())
        return (True, t) if len(t) <= UI_TESTO_MAX else (False, "Testo troppo lungo: al più %d caratteri" % UI_TESTO_MAX)
    if p in ("colore", "sfondo"):
        c = v.lower() if isinstance(v, str) else ""
        if c in UI_TAVOLOZZA or UI_COLORE.fullmatch(c):
            return True, c
        return False, "Colore non valido: serve #rrggbb o un colore della tavolozza"
    if p == "grandezza":   # 100 = la grandezza originale
        if type(v) is int and UI_GRANDEZZA[0] <= v <= UI_GRANDEZZA[1]:
            return True, v
        return False, "Grandezza non valida: da %d a %d per cento" % UI_GRANDEZZA
    if p == "nascosto":    # False = visibile, anche se la base lo nasconde
        return (True, v) if type(v) is bool else (False, "«Nascosto» vale vero o falso")
    if p == "ordine":      # 0 = al suo posto
        if type(v) is int and -UI_ORDINE <= v <= UI_ORDINE:
            return True, v
        return False, "Spostamento non valido: al più %d posti" % UI_ORDINE
    return False, "Proprietà sconosciuta: si cambiano testo, colore, sfondo, grandezza, nascosto e ordine"


def convalida_modifiche(m, severa=True):
    """Le modifiche di un livello, {indirizzo: {proprietà: valore}}, controllate. Con severa=False (un file scritto a
    mano sul Mac mini) le voci sbagliate si tolgono invece di rifiutare tutto."""
    def no(testo):
        if severa:
            raise ErroreExtra(400, testo)

    if not isinstance(m, dict):
        no("Modifiche non valide: serve un oggetto {indirizzo: {proprietà: valore}}")
        return {}
    fuori, n = {}, 0
    for ind, props in m.items():
        if not isinstance(ind, str) or len(ind) > UI_INDIRIZZO_MAX or not UI_INDIRIZZO.fullmatch(ind):
            no("Indirizzo di un elemento non valido")
            continue
        if not isinstance(props, dict) or not props:
            no("Modifica non valida: servono le proprietà dell'elemento")
            continue
        x = {}
        for p, v in props.items():
            ok, val = _ui_valore(p, v)
            if ok:
                x[p] = val
            else:
                no(val)
        if not x:
            continue
        n += len(x)
        if n > UI_MAX:
            no("Troppe modifiche: al più %d per livello" % UI_MAX)
            break
        fuori[ind] = x
    return fuori


def _ui_numero(v):
    return v if type(v) in (int, float) and v >= 0 else 0


class Interfaccia:
    """Strati dell'interfaccia sul Mac mini: base.json (base pubblicata, bozza di Samuele, ultime versioni) e
    persone/<chi>.json (lo strato personale: «p» per Samuele, l'id per un invitato). File 0600, scritti per intero.
    Le personalizzazioni di una persona revocata restano finché Samuele non le toglie."""

    def __init__(self, cartella):
        self.cartella = cartella
        self.blocco = threading.Lock()

    def _file(self, chi=None):
        if chi is None:
            return os.path.join(self.cartella, "base.json")
        if not re.fullmatch(r"p|[0-9a-f]{16}", chi or ""):
            raise ErroreExtra(400, "Persona non valida")
        return os.path.join(self.cartella, "persone", chi + ".json")

    @staticmethod
    def _leggi(file):
        """(dati, leggibile): un file che manca è vuoto; uno che non si legge non va sovrascritto."""
        try:
            with open(file, encoding="utf-8") as f:
                d = json.load(f)
        except FileNotFoundError:
            return {}, True
        except (OSError, ValueError):
            return {}, False
        return (d, True) if isinstance(d, dict) else ({}, False)

    @staticmethod
    def _scrivi(file, d, leggibile=True):
        if not leggibile:
            print("Atlante: %s non si legge: lo lascio com'è" % file, flush=True)
            raise ErroreExtra(503, "Il file dell'interfaccia sul Mac mini non si legge: non lo sovrascrivo")
        os.makedirs(os.path.dirname(file), mode=0o700, exist_ok=True)
        tmp = "%s.%d.tmp" % (file, os.getpid())
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, file)

    def _base(self):
        d, ok = self._leggi(self._file())
        b = d.get("base") if isinstance(d.get("base"), dict) else {}
        z = d.get("bozza") if isinstance(d.get("bozza"), dict) else None
        versioni = [{"versione": x["versione"], "pubblicato": _ui_numero(x.get("pubblicato")),
                     "modifiche": convalida_modifiche(x.get("modifiche"), False)}
                    for x in (d.get("versioni") if isinstance(d.get("versioni"), list) else [])
                    if isinstance(x, dict) and type(x.get("versione")) is int and x["versione"] > 0][:UI_VERSIONI]
        return {"base": {"modifiche": convalida_modifiche(b.get("modifiche") or {}, False),
                         "versione": b["versione"] if type(b.get("versione")) is int else 0,
                         "pubblicato": _ui_numero(b.get("pubblicato"))},
                "bozza": None if z is None else {"modifiche": convalida_modifiche(z.get("modifiche") or {}, False),
                                                 "aggiornato": _ui_numero(z.get("aggiornato"))},
                "versioni": versioni}, ok

    def leggi(self, chi, samuele):
        with self.blocco:
            d, _ = self._base()
            p, _ = self._leggi(self._file(chi))
        r = {"chi": chi, "base": d["base"],
             "personale": {"modifiche": convalida_modifiche(p.get("modifiche") or {}, False),
                           "aggiornato": _ui_numero(p.get("aggiornato"))}}
        if samuele:
            r["bozza"] = d["bozza"]
        return r

    def base(self):
        with self.blocco:
            return self._base()[0]["base"]

    def personale(self, chi, modifiche):
        m = convalida_modifiche(modifiche)
        with self.blocco:
            f = self._file(chi)
            x = {"modifiche": m, "aggiornato": time.time()}
            self._scrivi(f, x, self._leggi(f)[1])
            return x

    def bozza(self):
        """La bozza della base (None se non c'è)."""
        with self.blocco:
            return self._base()[0]["bozza"]

    def salva_bozza(self, modifiche):
        m = convalida_modifiche(modifiche)
        with self.blocco:
            d, ok = self._base()
            d["bozza"] = {"modifiche": m, "aggiornato": time.time()}
            self._scrivi(self._file(), d, ok)
            return d["bozza"]

    def scarta(self):
        with self.blocco:
            d, ok = self._base()
            if d["bozza"] is not None:
                d["bozza"] = None
                self._scrivi(self._file(), d, ok)

    @staticmethod
    def _pubblica(d, modifiche):
        v = max([d["base"]["versione"]] + [x["versione"] for x in d["versioni"]]) + 1
        ora = time.time()
        d["base"] = {"modifiche": modifiche, "versione": v, "pubblicato": ora}
        d["versioni"] = ([{"versione": v, "pubblicato": ora, "modifiche": modifiche}] + d["versioni"])[:UI_VERSIONI]
        return d["base"]

    def pubblica(self):
        with self.blocco:
            d, ok = self._base()
            if d["bozza"] is None:
                raise ErroreExtra(409, "Non c'è una bozza da pubblicare: prima «Modifica la base»")
            b = self._pubblica(d, d["bozza"]["modifiche"])
            d["bozza"] = None
            self._scrivi(self._file(), d, ok)
            return b

    def versioni(self):
        with self.blocco:
            d, _ = self._base()
        return {"attuale": d["base"]["versione"],
                "versioni": [{"versione": x["versione"], "pubblicato": x["pubblicato"],
                              "n": sum(len(p) for p in x["modifiche"].values())} for x in d["versioni"]]}

    def ripristina(self, n):
        with self.blocco:
            d, ok = self._base()
            x = next((x for x in d["versioni"] if x["versione"] == n), None)
            if x is None:
                raise ErroreExtra(404, "Versione non più tenuta: restano le ultime %d" % UI_VERSIONI)
            if d["bozza"] is not None:
                raise ErroreExtra(409, "C'è una bozza non pubblicata: pubblicala o buttala, poi torna a una versione")
            if n == d["base"]["versione"]:
                raise ErroreExtra(409, "È già la base pubblicata")
            b = self._pubblica(d, x["modifiche"])
            self._scrivi(self._file(), d, ok)
            return b


def _interfaccia():
    global UI
    if UI is None:
        UI = Interfaccia(os.path.join(_cart("cache"), "interfaccia"))
    return UI


def _rotte_interfaccia(h, metodo, resto, corpo):
    """Strati dell'interfaccia: la base pubblicata la leggono tutti, il proprio strato personale lo cambia ognuno
    (Samuele e la rete di casa: «p», come per i dispositivi), la bozza e le versioni sono solo di Samuele."""
    def solo(*metodi):
        if metodo not in metodi:
            raise ErroreExtra(405, "Metodo %s non permesso qui" % metodo)

    samuele, ui = _chi_e(h)["ruolo"] == "proprietario", _interfaccia()
    if metodo == "POST":
        try:
            n = int(h.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n > UI_CORPO_MAX:
            raise ErroreExtra(413, "Troppe modifiche in una volta")
    if not resto:
        solo("GET")
        return _json(h, ui.leggi(_di(h), samuele))
    if resto == ["base"]:
        solo("GET")
        return _json(h, {"base": ui.base()})
    if resto == ["personale"]:
        solo("POST")
        return _json(h, {"personale": ui.personale(_di(h), corpo.get("modifiche"))})
    if resto[0] in ("bozza", "pubblica", "versioni") and not samuele:
        raise ErroreExtra(403, "Solo Samuele cambia la base dell'interfaccia")
    if resto == ["bozza"]:
        solo("GET", "POST", "DELETE")
        if metodo == "DELETE":
            ui.scarta()
            return _json(h, {"ok": True})
        return _json(h, {"bozza": ui.salva_bozza(corpo.get("modifiche")) if metodo == "POST" else ui.bozza()})
    if resto == ["pubblica"]:
        solo("POST")
        return _json(h, {"base": ui.pubblica()})
    if resto == ["versioni"]:
        solo("GET")
        return _json(h, ui.versioni())
    if len(resto) == 3 and resto[0] == "versioni" and resto[2] == "ripristina":
        solo("POST")
        if not re.fullmatch(r"[1-9][0-9]{0,8}", resto[1]):
            raise ErroreExtra(404, "Versione sconosciuta")
        return _json(h, {"base": ui.ripristina(int(resto[1]))})
    raise ErroreExtra(404, "Richiesta sconosciuta: api/extra/interfaccia/%s" % "/".join(resto))


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
    elif cosa == "musica" and resto == ["cerca"]:
        solo("GET")
        q = parse_qs(urlsplit(h.path).query)
        _json(h, cerca_musica((q.get("q") or [""])[0], (q.get("fonte") or ["ytm"])[0]))
    elif cosa == "musica" and len(resto) == 2 and resto[0] == "ascolta":
        solo("GET")
        _ascolta(h, resto[1])
    elif cosa == "musica" and not resto:
        solo("GET", "POST")
        if metodo == "GET":
            _json(h, {"lavori": _coda("musica").elenco()})
        else:
            _json(h, {"id": _nuova_musica(corpo)})
    elif cosa == "musica" and len(resto) == 2 and resto[1] == "annulla":
        solo("POST")
        _annulla_musica(resto[0])
        _json(h, {"ok": True})
    elif cosa == "clip" and not resto:
        solo("GET")
        _json(h, CLIP.avanzamento() if CLIP else {"attivo": False})
    elif cosa == "clip" and len(resto) == 2 and resto[0] == "file":
        solo("GET")
        _clip_file(h, resto[1])
    elif cosa == "clip" and len(resto) == 1:
        solo("GET")
        _json(h, _clip_brano(resto[0]))
    elif cosa == "gestione":
        _gestione(h, metodo, resto, corpo)
    elif cosa == "interfaccia":
        _rotte_interfaccia(h, metodo, resto, corpo)
    elif cosa == "inviti" and not resto:
        solo("GET", "POST")
        _inviti(h, metodo, corpo)
    elif cosa == "io" and not resto:   # chi è questo dispositivo: l'app mostra la Gestione o le funzioni degli invitati
        solo("GET")
        chi = getattr(h, "_chi", None) or {"ruolo": "proprietario", "persona": None}
        pid = chi.get("persona")
        r = {"ruolo": chi["ruolo"], "persona": {"id": pid, "nome": PERS.nome(pid)} if pid and PERS else None}
        if chi["ruolo"] == "proprietario" and PERS:   # il riquadro Gestione mostra quante cose nuove aspettano Samuele
            r["nuove"] = {"richieste": sum(1 for x in PERS.richieste() if x["stato"] == "attesa")}
        _json(h, r)
    elif cosa == "dispositivi" and not resto:
        solo("GET")
        _json(h, {"dispositivi": DISP.elenco(_di(h))})
    elif cosa == "dispositivi" and len(resto) == 2 and resto[1] == "stato":
        solo("POST")
        _json(h, {"comandi": DISP.stato(resto[0], corpo, _di(h))})
    elif cosa == "dispositivi" and len(resto) == 2 and resto[1] == "comandi":
        solo("GET")
        _json(h, {"comandi": DISP.attendi(resto[0], _attesa(h), _di(h))})
    elif cosa == "dispositivi" and len(resto) == 2 and resto[1] == "comando":
        solo("POST")
        DISP.comando(resto[0], corpo, _di(h))
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
# la password. Si sceglie dalla pagina <app>/accesso aperta dalla rete Tailscale (per cambiarla: <app>/accesso?cambia,
# con quella di adesso) oppure sul Mac con
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
LIBERI = re.compile(r"^/(accesso|accesso/invito/[A-Za-z0-9_-]{8,64}|manifest\.webmanifest|icon-(?:maskable-)?\d+\.png"
                    r"|apple-touch-icon(?:-precomposed)?\.png|favicon\.ico)$")
RETI_TAILSCALE = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))
# persone invitate da Samuele: ognuna entra con il suo link (vedi Persone)
PERS = None                       # Persone, preparato da main()
PROPRIETARI = set()               # login Tailscale di Samuele (ATLANTE_PROPRIETARI); vuoto: tutta la rete Tailscale
PUBBLICO = ""                     # indirizzo dell'app da Internet (Funnel) per i link d'invito (--pubblico, ATLANTE_PUBBLICO)
INVITO_DURATA = 7 * 86400         # un link d'invito non usato scade dopo una settimana
COLLEGAMENTO_DURATA = 15 * 60     # codice per l'app sulla schermata Home dell'iPhone (biscotti separati da Safari)
COLLEGAMENTO_ALFABETO = "ACDEFGHJKMNPQRTUVWXY3479"   # niente lettere e cifre che si confondono (0/O, 1/I, 5/S…)
PERSONE_MAX = 200
DISPOSITIVI_PERSONA = 10          # oltre, esce quello visto meno di recente
RICHIESTE_ATTESA_MAX = 5          # inviti chiesti da un invitato e non ancora decisi da Samuele
NOME_PERSONA_MAX = 40
VISTO_OGNI = 60                   # l'ultimo accesso dei dispositivi va su disco al più ogni minuto
INVITO_GRAZIA = 120               # un invito appena usato rientra nello stesso dispositivo (doppio tocco su «Entra»)


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

    def biscotto(self, rnd=None):
        """Biscotto nuovo; `rnd` (la parte casuale di quello di prima) lo rinnova restando lo stesso dispositivo, che così
        nella Gestione non compare due volte e una revoca resta valida."""
        corpo = "%d.%s" % (int(time.time()) + ACCESSO_DURATA, rnd if re.fullmatch(r"[0-9a-f]{16}", rnd or "") else secrets.token_hex(8))
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


def _impronta(codice):
    return hashlib.sha256(codice.encode("utf-8")).hexdigest()


def _nome_persona(nome):
    n = " ".join(str(nome or "").split())[:NOME_PERSONA_MAX].strip()
    if not n:
        raise ErroreExtra(400, "Serve il nome della persona")
    return n


def tipo_da_agente(ua):
    """Tipo di dispositivo dallo User-Agent, con i nomi di TIPI_DISP (per l'elenco della Gestione)."""
    ua = ua or ""
    if "iPad" in ua:
        return "ipad"
    if "iPhone" in ua or "iPod" in ua:
        return "iphone"
    if "Android" in ua:
        return "android"
    return "mac" if "Macintosh" in ua else "pc"


def browser_da_agente(ua):
    """Il browser dallo User-Agent, per riconoscere i dispositivi nella Gestione (Chrome su iPhone scrive «CriOS»…)."""
    ua = ua or ""
    for segno, nome in (("Edg", "Edge"), ("SamsungBrowser", "Samsung Internet"), ("OPR/", "Opera"), ("CriOS", "Chrome"),
                        ("FxiOS", "Firefox"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari")):
        if segno in ua:
            return nome
    return "Altro"


class Persone:
    """Persone invitate, in <cache>/persone.json (lo legge solo il proprietario), con un segreto suo per firmare i loro
    biscotti: cambiare la password di Samuele non fa uscire gli invitati. Dei link d'invito e dei codici di collegamento
    resta solo l'impronta; il codice in chiaro di un invito chiesto da un invitato resta nella richiesta finché non è
    usato o scaduto, perché chi l'ha chiesto deve poterlo rivedere per mandarlo."""

    def __init__(self, file):
        self.file = file
        self.blocco = threading.Lock()
        self.dati, self.mtime = {}, None
        self.visti = {}      # chiave -> (tipo, istante, via, browser): l'ultimo accesso, su disco al più ogni VISTO_OGNI
        self.salvato = 0.0
        self.usati = {}      # impronta di un invito appena usato -> (dispositivo, istante, indirizzo)
        self.rotto = False   # il file non si legge: niente scritture, o lo si sovrascriverebbe con un archivio vuoto

    def _aggiorna(self):
        try:
            m = os.stat(self.file).st_mtime_ns
        except OSError:
            m = None
        if m != self.mtime:
            d, rotto = {}, False
            if m is not None:
                try:
                    with open(self.file, encoding="utf-8") as f:
                        d = json.load(f)
                except (OSError, ValueError):
                    rotto = True
                if not isinstance(d, dict):
                    rotto = True
            if rotto:   # si tiene quello che c'era in memoria; il file lo si guarda di nuovo quando cambia
                if not self.rotto:
                    print("Atlante: %s non si legge: lo lascio com'è e non scrivo più finché non torna leggibile" % self.file,
                          flush=True)
                self.rotto, self.mtime = True, m
            else:
                self.rotto, self.dati, self.mtime = False, d, m
        d = self.dati
        for k in ("persone", "inviti", "collegamenti", "dispositivi", "tuoi", "richieste"):
            if not isinstance(d.get(k), dict):
                d[k] = {}
        if not isinstance(d.get("revocati"), list):
            d["revocati"] = []
        return d

    def _scrivi(self, d):
        if self.rotto:
            raise ErroreExtra(503, "L'archivio delle persone sul Mac mini non si legge: non lo sovrascrivo")
        ora = time.time()
        for k in ("inviti", "collegamenti"):
            d[k] = {h: x for h, x in d[k].items() if x.get("scade", 0) > ora}
        for r in d["richieste"].values():   # invito scaduto: il codice in chiaro non serve più
            if r.get("invito") and r["invito"] not in d["inviti"]:
                r.pop("codice", None)
        for c, (tipo, t, via, browser) in self.visti.items():
            x = d["dispositivi"].get(c) or (d["tuoi"].setdefault(c, {}) if c.startswith("p:") and c not in d["revocati"] else None)
            if x is not None:
                x.setdefault("creato", t)
                x.update({k: v for k, v in (("tipo", tipo), ("visto", t), ("via", via), ("browser", browser)) if v})
        self.salvato = ora
        os.makedirs(os.path.dirname(self.file) or ".", exist_ok=True)
        tmp = "%s.%d.tmp" % (self.file, os.getpid())
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, self.file)
        self.dati, self.mtime = d, os.stat(self.file).st_mtime_ns

    def _segreto(self, d):
        if not re.fullmatch(r"[0-9a-f]{64}", str(d.get("segreto", ""))):
            d["segreto"] = secrets.token_hex(32)
            self._scrivi(d)
        return bytes.fromhex(d["segreto"])

    def _crea(self, d, nome, da=None):
        if len(d["persone"]) >= PERSONE_MAX:
            raise ErroreExtra(409, "Ci sono già %d persone: revocane qualcuna" % PERSONE_MAX)
        pid = secrets.token_hex(8)
        d["persone"][pid] = dict({"nome": _nome_persona(nome), "creato": time.time()}, **({"da": da} if da else {}))
        return pid

    def _invito(self, d, pid):
        if pid not in d["persone"]:
            raise ErroreExtra(404, "Persona sconosciuta")
        codice, scade = secrets.token_urlsafe(16), time.time() + INVITO_DURATA
        d["inviti"][_impronta(codice)] = {"persona": pid, "scade": scade}
        return codice, scade

    def _dispositivo(self, d, pid, tipo):
        """Nuovo dispositivo di una persona; oltre DISPOSITIVI_PERSONA esce quello visto meno di recente."""
        ora = time.time()
        suoi = [k for k, x in d["dispositivi"].items() if x.get("persona") == pid]
        if len(suoi) >= DISPOSITIVI_PERSONA:
            vecchio = min(suoi, key=lambda k: self.visti.get(k, (0, d["dispositivi"][k].get("visto", 0)))[1])   # (…, istante, …)
            del d["dispositivi"][vecchio]
            self.visti.pop(vecchio, None)
        did = secrets.token_hex(8)
        d["dispositivi"][did] = {"persona": pid, "tipo": tipo if tipo in TIPI_DISP else "pc", "creato": ora, "visto": ora}
        return did

    def crea(self, nome):
        with self.blocco:
            d = self._aggiorna()
            pid = self._crea(d, nome)
            self._scrivi(d)
            x = d["persone"][pid]
            return {"id": pid, "nome": x["nome"], "creato": x["creato"]}

    def invito(self, pid):
        with self.blocco:
            d = self._aggiorna()
            r = self._invito(d, pid)
            self._scrivi(d)
            return r

    def leggi_invito(self, codice):
        with self.blocco:
            d = self._aggiorna()
            x = d["inviti"].get(_impronta(str(codice or "")))
            if not x or x["scade"] <= time.time() or x["persona"] not in d["persone"]:
                return None
            return {"persona": x["persona"], "nome": d["persone"][x["persona"]]["nome"]}

    def usa_invito(self, codice, tipo, ip=None):
        h = _impronta(str(codice or ""))
        with self.blocco:
            d = self._aggiorna()
            ora = time.time()
            self.usati = {k: v for k, v in self.usati.items() if ora - v[1] <= INVITO_GRAZIA}
            x = d["inviti"].get(h)
            if not x or x["scade"] <= ora or x["persona"] not in d["persone"]:
                # doppio tocco su «Entra» o pagina rimandata subito dopo: lo stesso telefono riceve lo stesso dispositivo
                u = self.usati.get(h)
                if u and ip and u[2] == ip and u[0] in d["dispositivi"]:
                    return u[0]
                return None
            del d["inviti"][h]
            for r in d["richieste"].values():
                if r.get("invito") == h:
                    r.pop("codice", None)
                    r["stato"] = "usata"
            did = self._dispositivo(d, x["persona"], tipo)
            self._scrivi(d)
            self.usati[h] = (did, ora, ip)
            return did

    def persona_di(self, did):
        with self.blocco:
            return (self._aggiorna()["dispositivi"].get(did) or {}).get("persona")

    def collegamento(self, pid):
        with self.blocco:
            d = self._aggiorna()
            if pid not in d["persone"]:
                raise ErroreExtra(404, "Persona sconosciuta")
            codice = "".join(secrets.choice(COLLEGAMENTO_ALFABETO) for _ in range(6))
            scade = time.time() + COLLEGAMENTO_DURATA
            d["collegamenti"][_impronta(codice)] = {"persona": pid, "scade": scade}
            self._scrivi(d)
            return codice, scade

    def usa_collegamento(self, codice, tipo):
        h = _impronta(re.sub(r"[^A-Z0-9]", "", str(codice or "").upper()))
        with self.blocco:
            d = self._aggiorna()
            x = d["collegamenti"].get(h)
            if not x or x["scade"] <= time.time() or x["persona"] not in d["persone"]:
                return None
            del d["collegamenti"][h]
            did = self._dispositivo(d, x["persona"], tipo)
            self._scrivi(d)
            return did

    def biscotto(self, did):
        corpo = "o.%d.%s" % (int(time.time()) + ACCESSO_DURATA, did)
        with self.blocco:
            k = self._segreto(self._aggiorna())
        return corpo + "." + hmac.new(k, corpo.encode("ascii"), hashlib.sha256).hexdigest()

    def chi(self, valore):
        """(persona, dispositivo, scadenza) di un biscotto valido; None se è falso, scaduto o revocato."""
        m = re.fullmatch(r"o\.([0-9]{1,12})\.([0-9a-f]{16})\.([0-9a-f]{64})", valore or "")
        if not m:
            return None
        with self.blocco:
            d = self._aggiorna()
            s, x = str(d.get("segreto", "")), d["dispositivi"].get(m.group(2))
            if not re.fullmatch(r"[0-9a-f]{64}", s) or not x or x.get("persona") not in d["persone"]:
                return None
            pid = x["persona"]
        firma = hmac.new(bytes.fromhex(s), ("o.%s.%s" % (m.group(1), m.group(2))).encode("ascii"), hashlib.sha256).hexdigest()
        scad, ora = int(m.group(1)), time.time()
        if not hmac.compare_digest(firma, m.group(3)) or not ora < scad <= ora + ACCESSO_DURATA + 60:
            return None
        return pid, m.group(2), scad

    def visto(self, chiave, tipo, via=None, browser=None):
        """Ultimo accesso di un dispositivo (con da dove arriva: «tailscale» o «internet», e il browser): in memoria, su
        disco al più ogni VISTO_OGNI. Non solleva mai: un disco pieno non deve far cadere la richiesta."""
        with self.blocco:
            self.visti[chiave] = (tipo if tipo in TIPI_DISP else "pc", time.time(), via, browser)
            if len(self.visti) > 5000:   # moltissime chiavi diverse (biscotti falsi non arrivano qui): si salva e si riparte
                self.visti = dict(list(self.visti.items())[-1000:])
            if time.time() - self.salvato >= VISTO_OGNI:
                try:
                    self._scrivi(self._aggiorna())
                except (ErroreExtra, OSError) as e:
                    self.salvato = time.time()   # si riprova fra un minuto, non a ogni richiesta
                    print("Atlante: ultimo accesso non salvato: %s" % e, flush=True)

    def revoca_persona(self, pid):
        with self.blocco:
            d = self._aggiorna()
            if d["persone"].pop(pid, None) is None:
                return False
            for k in ("dispositivi", "inviti", "collegamenti"):
                d[k] = {h: x for h, x in d[k].items() if x.get("persona") != pid}
            self._scrivi(d)
            return True

    def revoca_dispositivo(self, chiave):
        with self.blocco:
            d = self._aggiorna()
            self.visti.pop(chiave, None)
            if chiave.startswith("p:"):
                if chiave in d["revocati"]:
                    return False
                d["revocati"] = (d["revocati"] + [chiave])[-1000:]
                d["tuoi"].pop(chiave, None)
            elif d["dispositivi"].pop(chiave, None) is None:
                return False
            self._scrivi(d)
            return True

    def revocato(self, chiave):
        with self.blocco:
            return chiave in self._aggiorna()["revocati"]

    def nome(self, pid):
        with self.blocco:
            return (self._aggiorna()["persone"].get(pid) or {}).get("nome", "")

    def elenco(self):
        with self.blocco:
            d = self._aggiorna()

            def visto(k, x):
                tipo, t, via, browser = self.visti.get(k, (None, None, None, None))
                return {"id": k, "tipo": tipo or x.get("tipo", "pc"), "visto": t or x.get("visto", 0),
                        "via": via or x.get("via", ""), "browser": browser or x.get("browser", ""),
                        "creato": x.get("creato") or t or x.get("visto", 0)}

            disp = {}
            for k, x in d["dispositivi"].items():
                disp.setdefault(x.get("persona"), []).append(visto(k, x))
            tuoi = {k: dict(x) for k, x in d["tuoi"].items()}
            for k in self.visti:
                if k.startswith("p:"):
                    tuoi.setdefault(k, {})
            persone = [dict({"id": p, "nome": x["nome"], "creato": x["creato"],
                             "dispositivi": sorted(disp.get(p, []), key=lambda y: -y["visto"])},
                            **({"da": x["da"]} if x.get("da") else {}))
                       for p, x in sorted(d["persone"].items(), key=lambda kv: kv[1]["creato"])]
            return {"persone": persone,
                    "tuoi": sorted((visto(k, x) for k, x in tuoi.items() if k not in d["revocati"]), key=lambda y: -y["visto"])}

    def richiedi(self, da_pid, nome):
        with self.blocco:
            d = self._aggiorna()
            if da_pid not in d["persone"]:
                raise ErroreExtra(404, "Persona sconosciuta")
            if sum(1 for r in d["richieste"].values() if r["da"] == da_pid and r["stato"] == "attesa") >= RICHIESTE_ATTESA_MAX:
                raise ErroreExtra(409, "Hai già %d inviti in attesa" % RICHIESTE_ATTESA_MAX)
            rid = secrets.token_hex(8)
            r = d["richieste"][rid] = {"nome": _nome_persona(nome), "da": da_pid, "creato": time.time(), "stato": "attesa"}
            if len(d["richieste"]) > 1000:   # le più vecchie già decise escono
                decise = sorted((k for k, x in d["richieste"].items() if x["stato"] != "attesa"),
                                key=lambda k: d["richieste"][k]["creato"])
                for k in decise[:len(d["richieste"]) - 1000]:
                    del d["richieste"][k]
            self._scrivi(d)
            return dict(r, id=rid)

    def _richiesta(self, d, rid, r):
        x = {"id": rid, "nome": r["nome"], "da": r["da"], "creato": r["creato"], "stato": r["stato"]}
        if r.get("persona"):
            x["persona"] = r["persona"]
        if r.get("codice") and r.get("invito") in d["inviti"] and d["inviti"][r["invito"]]["scade"] > time.time():
            x.update(codice=r["codice"], scade=d["inviti"][r["invito"]]["scade"])
        return x

    def richieste(self):
        with self.blocco:
            d = self._aggiorna()
            return [self._richiesta(d, k, r) for k, r in sorted(d["richieste"].items(), key=lambda kv: -kv[1]["creato"])]

    def richieste_di(self, pid):
        with self.blocco:
            d = self._aggiorna()
            return [self._richiesta(d, k, r) for k, r in sorted(d["richieste"].items(), key=lambda kv: -kv[1]["creato"])
                    if r["da"] == pid]

    def consenti(self, rid):
        with self.blocco:
            d = self._aggiorna()
            r = d["richieste"].get(rid)
            if not r:
                raise ErroreExtra(404, "Richiesta sconosciuta")
            if r["stato"] != "attesa":
                raise ErroreExtra(409, "Richiesta già decisa")
            pid = self._crea(d, r["nome"], da=r["da"])
            codice, scade = self._invito(d, pid)
            r.update(stato="consentita", persona=pid, codice=codice, invito=_impronta(codice))
            self._scrivi(d)
            x = d["persone"][pid]
            return {"id": pid, "nome": x["nome"], "creato": x["creato"], "da": r["da"]}, codice, scade

    def rifiuta(self, rid):
        with self.blocco:
            d = self._aggiorna()
            r = d["richieste"].get(rid)
            if not r or r["stato"] != "attesa":
                return False
            r["stato"] = "rifiutata"
            self._scrivi(d)
            return True


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
    """("p", scadenza, parte casuale, None) per il biscotto della password, ("o", scadenza, dispositivo, persona) per un
    invitato; None se manca, è falso, scaduto o revocato."""
    for parte in (h.headers.get("Cookie") or "").split(";"):
        k, _, v = parte.strip().partition("=")
        if k != ACCESSO_BISCOTTO:
            continue
        v = v.strip()
        if v.startswith("o."):
            c = PERS.chi(v) if PERS else None
            if c:
                return "o", c[2], c[1], c[0]
            continue
        s = ACC.scadenza(v)
        if s:
            rnd = v.split(".")[1]
            if PERS is None or not PERS.revocato("p:" + rnd):
                return "p", s, rnd, None
    return None


def _proprietario_rete(h):
    """Il Mac stesso o Samuele dalla rete Tailscale (con ATLANTE_PROPRIETARI, solo quei login): non serve il biscotto.
    La rete di casa no: lì si ascolta, ma senza diventare Samuele."""
    if h.headers.get("Tailscale-Funnel-Request") is not None:
        return False
    vicino = _ip(h.client_address[0])
    xff = h.headers.get("X-Forwarded-For")
    if xff is None:
        return vicino is not None and (vicino.is_loopback or (_ip_tailscale(vicino) and not PROPRIETARI))
    if vicino is None or not vicino.is_loopback:
        return False
    login = (h.headers.get("Tailscale-User-Login") or "").strip().lower()
    if login:
        return not PROPRIETARI or login in PROPRIETARI
    ultimo = _ip(xff.split(",")[-1])
    return ultimo is not None and (ultimo.is_loopback or (_ip_tailscale(ultimo) and not PROPRIETARI))


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
.o{margin-top:30px}
.codice{display:inline-block;font:700 30px/1.2 ui-monospace,Menlo,monospace;letter-spacing:4px;color:var(--fg)}
a.btn{display:flex;align-items:center;justify-content:center;min-height:50px;border-radius:12px;background:var(--accent);color:#fff;font-weight:600;text-decoration:none}
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
# chi è entrato con un invito da Safari scrive qui il codice di collegamento nell'app sulla schermata Home (sull'iPhone
# ha i biscotti separati da Safari)
_MODULO_CODICE = """<p>@M</p>
<p>Hai un invito? Scrivi il codice di collegamento.</p>
<form method="post" action="@Raccesso">
<input type="text" name="codice" autocomplete="one-time-code" autocapitalize="characters" autocorrect="off" spellcheck="false" maxlength="9" placeholder="Codice di collegamento" aria-label="Codice di collegamento" required>
<button type="submit">Collega</button>
</form>"""
_MODULO_ACCESSO = _MODULO_CODICE + """
<p class="o">Sei Samuele? Entra con la password.</p>
<form method="post" action="@Raccesso">
<input class="nome" type="text" name="nome" autocomplete="username" value="Atlante Musica" tabindex="-1" aria-hidden="true">
<input type="password" name="password" autocomplete="current-password" placeholder="Password" aria-label="Password" required>
<button type="submit">Entra</button>
</form>"""
_MODULO_INVITO = """<p>@M</p>
<form method="post" action="" onsubmit="this.querySelector('button').disabled=true">
<button type="submit">Entra</button>
</form>"""
_INVITO_FATTO = """<p>@M</p>
<p><a class="btn" href="@R./">Apri Atlante Musica</a></p>
<p class="o">Vuoi l'app sulla schermata Home? In Safari tocca Condividi e poi «Aggiungi alla schermata Home». Alla prima
apertura scrivi questo codice (vale 15 minuti):</p>
<p><b class="codice">@K</b></p>"""
# dalla rete Tailscale (o dal Mac): si sceglie la password per Internet, senza Terminale. Per cambiarla serve quella di adesso
_MODULO_NUOVA = """<p>@M</p>
<form method="post" action="@Raccesso">
<input class="nome" type="text" name="nome" autocomplete="username" value="Atlante Musica" tabindex="-1" aria-hidden="true">
@A<input type="password" name="nuova" autocomplete="new-password" minlength="8" placeholder="Nuova password (almeno 8 caratteri)" aria-label="Nuova password" required>
<input type="password" name="ripeti" autocomplete="new-password" minlength="8" placeholder="Ripetila" aria-label="Ripeti la nuova password" required>
<button type="submit">Salva la password</button>
</form>"""
_CAMPO_ATTUALE = """<input type="password" name="password" autocomplete="current-password" placeholder="Password di adesso" aria-label="Password di adesso" required>
"""


def _scrivi_pagina_accesso(h, codice, messaggio, errore=False, modulo=True, intestazioni=()):
    m = '<span class="err">%s</span>' % messaggio if errore else messaggio
    if modulo is True:
        c = _MODULO_ACCESSO.replace("@M", m)
    elif modulo:   # un altro modulo (scelta della password), con @M al posto del messaggio
        c = modulo.replace("@M", m)
    else:
        c = "<p>%s</p>" % m
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


def _nome_pagina(nome):
    return html.escape(nome).replace("@", "&#64;")   # i segnaposto delle pagine (@M, @R…) restano dove sono


def _troppi_tentativi(h, w, **k):
    return _scrivi_pagina_accesso(h, 429, "Troppi tentativi sbagliati: riprova fra %d minut%s." % ((w + 59) // 60, "o" if w <= 60 else "i"),
                                  True, intestazioni=[("Retry-After", str(w))], **k)


def _leggi_modulo(h):
    """Corpo di un modulo POST (al più 4 KB): (byte, campi) o None se è troppo grande."""
    try:
        n = int(h.headers.get("Content-Length") or 0)
    except ValueError:
        n = -1
    if not 0 <= n <= 4096:
        h.close_connection = True
        return None
    dati = h.rfile.read(n) if n else b""
    try:
        return dati, parse_qs(dati.decode("utf-8"), max_num_fields=10)
    except (UnicodeDecodeError, ValueError):
        return dati, {}


def _usa_collegamento(h, codice):
    """Codice di collegamento scritto nell'app sulla schermata Home: il dispositivo entra come la persona invitata."""
    modulo = _MODULO_ACCESSO if ACC.ha_password() else _MODULO_CODICE
    if not _stessa_origine(h):
        return _scrivi_pagina_accesso(h, 403, "Richiesta da un altro sito: rifiutata.", True, modulo=modulo)
    ip = _ip_cliente(h)
    w = ACC.attesa(ip)
    if w:
        return _troppi_tentativi(h, w, modulo=modulo)
    did = PERS.usa_collegamento(codice, tipo_da_agente(h.headers.get("User-Agent"))) if PERS else None
    if not did:
        ACC.sbagliata(ip)
        print("Atlante: codice di collegamento sbagliato da %s" % ip, flush=True)
        return _scrivi_pagina_accesso(h, 401, "Codice sbagliato o scaduto.", True, modulo=modulo)
    ACC.riuscita(ip)
    h._biscotto = _set_cookie(h, PERS.biscotto(did))
    return _rimanda(h, _verso(h, "./"))


def _gia_dentro(h, pid):
    """Chi è già entrato e riapre il link (o lo rimanda con «indietro»): niente errore, il link dell'app e un codice nuovo."""
    k, _ = PERS.collegamento(pid)
    return _scrivi_pagina_accesso(h, 200, "Sei già dentro, %s!" % _nome_pagina(PERS.nome(pid)), modulo=_INVITO_FATTO.replace("@K", k))


def _pagina_invito(h, codice, samuele, chi=None):
    """Link d'invito. GET: chi invita e il tasto «Entra» — WhatsApp e Messaggi aprono i link per l'anteprima, quindi
    leggere la pagina non usa l'invito. POST (il tocco su «Entra»): l'invito si usa, il dispositivo riceve il biscotto
    della persona e il codice per l'app sulla schermata Home."""
    morto = "Questo invito non vale più: chiedi a Samuele un link nuovo."
    if h.command not in ("GET", "HEAD", "POST"):
        h.close_connection = True
        return _scrivi_pagina_accesso(h, 405, "Richiesta non valida.", True, modulo=False)
    if h.command == "POST" and _leggi_modulo(h) is None:
        return _scrivi_pagina_accesso(h, 413, "Richiesta non valida.", True, modulo=False)
    info = PERS.leggi_invito(codice) if PERS else None
    invitato = chi["persona"] if chi and chi["ruolo"] == "invitato" else None
    if h.command != "POST":
        if info is None:
            return _gia_dentro(h, invitato) if invitato else _scrivi_pagina_accesso(h, 410, morto, modulo=False)
        if samuele:
            return _scrivi_pagina_accesso(h, 200, "Questo è l'invito per %s: aprilo dal suo telefono. Tu sei già dentro "
                                          "come Samuele." % _nome_pagina(info["nome"]), modulo=False)
        return _scrivi_pagina_accesso(h, 200, "Ciao %s! Samuele ti ha invitato ad ascoltare la sua musica."
                                      % _nome_pagina(info["nome"]), modulo=_MODULO_INVITO)
    if not _stessa_origine(h):
        return _scrivi_pagina_accesso(h, 403, "Richiesta da un altro sito: rifiutata.", True, modulo=False)
    if samuele:   # Samuele che prova il link resta Samuele, e l'invito resta per chi deve riceverlo
        return _scrivi_pagina_accesso(h, 409, "Questo invito è per un'altra persona: tu sei già dentro come Samuele.",
                                      True, modulo=False)
    ip = _ip_cliente(h)
    w = ACC.attesa(ip)
    if w:
        return _troppi_tentativi(h, w, modulo=False)
    did = PERS.usa_invito(codice, tipo_da_agente(h.headers.get("User-Agent")), ip)
    if not did:
        if invitato:   # già dentro (pagina rimandata più tardi): non è un tentativo sbagliato
            return _gia_dentro(h, invitato)
        ACC.sbagliata(ip)
        return _scrivi_pagina_accesso(h, 410, morto, True, modulo=False)
    pid = PERS.persona_di(did)
    h._biscotto = _set_cookie(h, PERS.biscotto(did))
    k, _ = PERS.collegamento(pid)
    print("Atlante: %s è entrato con l'invito" % PERS.nome(pid), flush=True)
    return _scrivi_pagina_accesso(h, 200, "Sei dentro, %s!" % _nome_pagina(PERS.nome(pid)),
                                  modulo=_INVITO_FATTO.replace("@K", k))


def _nuova_password(h, dati):
    """Dalla rete Tailscale o dal Mac: scelta (o cambio) della password per Internet."""
    cambio = ACC.ha_password()
    modulo = _MODULO_NUOVA.replace("@A", _CAMPO_ATTUALE if cambio else "")
    if h.command != "POST":
        return _scrivi_pagina_accesso(h, 200, ("Cambia la password per aprire l'app da Internet." if cambio else
                                               "Scegli la password per aprire l'app da Internet, senza Tailscale. "
                                               "Qui non serve: la chiede solo da fuori, una volta per dispositivo."), modulo=modulo)
    if not _stessa_origine(h):
        return _scrivi_pagina_accesso(h, 403, "Richiesta da un altro sito: rifiutata.", True, modulo=modulo)
    try:
        q = parse_qs(dati.decode("utf-8"), max_num_fields=10)
    except (UnicodeDecodeError, ValueError):
        q = {}
    nuova, ripeti = q.get("nuova", [""])[0], q.get("ripeti", [""])[0]
    if cambio:
        ip = _ip_cliente(h)
        w = ACC.attesa(ip)
        if w:
            return _scrivi_pagina_accesso(h, 429, "Troppi tentativi sbagliati: riprova fra %d minut%s." % (
                (w + 59) // 60, "o" if w <= 60 else "i"), True, modulo=modulo, intestazioni=[("Retry-After", str(w))])
        if not ACC.giusta(q.get("password", [""])[0]):
            ACC.sbagliata(ip)
            return _scrivi_pagina_accesso(h, 401, "La password di adesso è sbagliata.", True, modulo=modulo)
        ACC.riuscita(ip)
    if len(nuova) < 8:
        return _scrivi_pagina_accesso(h, 400, "Servono almeno 8 caratteri.", True, modulo=modulo)
    if nuova != ripeti:
        return _scrivi_pagina_accesso(h, 400, "Le due password non sono uguali.", True, modulo=modulo)
    ACC.imposta(nuova)
    h._biscotto = _set_cookie(h, ACC.biscotto())   # firmato col segreto nuovo: questo dispositivo resta dentro
    print("Atlante: password per Internet %s" % ("cambiata" if cambio else "impostata"), flush=True)
    return _scrivi_pagina_accesso(h, 200, "Password salvata. Da Internet l'app la chiederà una volta per dispositivo; "
                                  "dalla rete Tailscale nessuna differenza.<br><br><a href=\"@R./\">Apri Atlante Musica</a>", modulo=False)


def _pagina_accesso(h, entrato, samuele):
    """GET: la pagina con la password (o l'app, per chi è già dentro). POST: controlla la password.
    Samuele (dalla rete Tailscale o dal Mac): scelta della password, se non c'è ancora (o con ?cambia, per cambiarla)."""
    dati, campi = b"", {}
    if h.command == "POST":
        letto = _leggi_modulo(h)
        if letto is None:
            return _scrivi_pagina_accesso(h, 413, "Richiesta non valida.", True)
        dati, campi = letto
    elif h.command not in ("GET", "HEAD"):
        h.close_connection = True
        return _scrivi_pagina_accesso(h, 405, "Richiesta non valida.", True)
    if samuele and (not ACC.ha_password() or "cambia" in parse_qs(urlsplit(h.path).query, keep_blank_values=True)
                   or b"nuova=" in dati):
        return _nuova_password(h, dati)
    if entrato:
        return _rimanda(h, _verso(h, "./"))
    if h.command == "POST" and "codice" in campi:
        return _usa_collegamento(h, campi["codice"][0])
    attiva = ACC.ha_password()
    if not attiva:   # senza password entra solo chi ha un invito (con il codice di collegamento)
        return _scrivi_pagina_accesso(h, 403 if h.command == "POST" else 200, "L'accesso da Internet non è ancora attivo: "
                                      "la password si sceglie aprendo questa pagina con Tailscale acceso.", modulo=_MODULO_CODICE)
    if h.command != "POST":
        return _scrivi_pagina_accesso(h, 200, "Da Internet la musica del Mac è protetta da una password.")
    if not _stessa_origine(h):
        return _scrivi_pagina_accesso(h, 403, "Richiesta da un altro sito: rifiutata.", True)
    ip = _ip_cliente(h)
    w = ACC.attesa(ip)
    if w:
        return _scrivi_pagina_accesso(h, 429, "Troppi tentativi sbagliati: riprova fra %d minut%s." % ((w + 59) // 60, "o" if w <= 60 else "i"),
                                      True, intestazioni=[("Retry-After", str(w))])
    pw = campi.get("password", [""])[0]
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
    h._chi = {"ruolo": "proprietario", "persona": None, "chiave": None}
    if ACC is None:
        return False
    p = unquote(urlsplit(h.path).path)
    libero = LIBERI.match(p)
    b = _biscotto_valido(h)
    rete = _proprietario_rete(h)
    if rete and b is not None and b[0] == "o":
        b = None   # Samuele dalla sua rete Tailscale resta Samuele anche con il biscotto di un invito provato da lui
    samuele = rete or (b is not None and b[0] == "p")
    if b is not None and b[0] == "o":
        h._chi = {"ruolo": "invitato", "persona": b[3], "chiave": b[2]}
    elif samuele:
        h._chi = {"ruolo": "proprietario", "persona": None, "chiave": "p:" + b[2] if b else None}
    elif _fidato(h):
        h._chi = {"ruolo": "casa", "persona": None, "chiave": None}   # rete di casa: niente biscotto, niente Gestione
    else:
        h._chi = None
    entrato = h._chi is not None
    vecchio = not b or b[1] - time.time() < ACCESSO_DURATA - ACCESSO_RINNOVO
    if b is not None and b[0] == "o":
        if vecchio:
            h._biscotto = _set_cookie(h, PERS.biscotto(b[2]))
    elif samuele and vecchio:
        h._biscotto = _set_cookie(h, ACC.biscotto(b[2] if b else None))
    if PERS is not None and h._chi and h._chi["chiave"]:
        ua = h.headers.get("User-Agent")
        PERS.visto(h._chi["chiave"], tipo_da_agente(ua), "tailscale" if _fidato(h) else "internet", browser_da_agente(ua))
    if libero and libero.group(1) == "accesso":
        _pagina_accesso(h, entrato, samuele)
        return True
    if libero and libero.group(1).startswith("accesso/invito/"):
        _pagina_invito(h, libero.group(1).rsplit("/", 1)[1], samuele, h._chi)
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


# un invitato ascolta e basta: la musica, i videoclip, i suoi dispositivi, chi è e i suoi inviti. Il resto (proxy,
# scaricamenti, IA, voci, la Gestione, i moduli di «Altro») usa le risorse del Mac mini o è di Samuele
PERMESSE_INVITATI = re.compile(
    r"^/(?:|index\.html|sw\.js|manifest\.webmanifest|favicon\.ico|icon-(?:maskable-)?\d+\.png"
    r"|apple-touch-icon(?:-precomposed)?\.png|x/kit\.(?:js|css)|x/inviti\.html)$"
    r"|^(?:/.*)?/api/(?:musica/(?:catalogo|file/[^/]+|copertina/[^/]+|pezzo/[^/]+)"
    r"|extra/(?:io|inviti|clip|clip/file/[^/]+|clip/[^/]+|dispositivi(?:/[^/]+/(?:stato|comandi|comando))?"
    r"|interfaccia(?:/base|/personale)?"   # la base pubblicata e il proprio strato personale (non bozza né versioni)
    r"))$")


def _permesso(h):
    """Da chiamare subito dopo controlla_accesso: False (e risposta 403) se un invitato chiede quello che non può."""
    chi = getattr(h, "_chi", None)
    if not chi or chi["ruolo"] != "invitato" or PERMESSE_INVITATI.match(unquote(urlsplit(h.path).path)):
        return True
    if h.command not in ("GET", "HEAD"):
        h.close_connection = True   # corpo non letto
    corpo = json.dumps({"errore": "Non disponibile per gli invitati"}, ensure_ascii=False).encode("utf-8")
    h.send_response(403)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(corpo)))
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    if h.command != "HEAD":
        h.wfile.write(corpo)
    return False


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
    global ACC, CLIP, PERS, PROPRIETARI, PUBBLICO
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
    # anche da ATLANTE_MUSICA_DIR (come ATLANTE_ASCOLTO): nel LaunchAgent una versione vecchia del server la ignora,
    # mentre un argomento che non conosce la farebbe uscire subito (e l'aggiornamento automatico tornerebbe indietro)
    ap.add_argument("--musica-dir", default=os.environ.get("ATLANTE_MUSICA_DIR") or CONF["musica_dir"],
                    help="dove salvare i brani scaricati da internet (fa parte della libreria; anche ATLANTE_MUSICA_DIR)")
    # videoclip sincronizzati: anche da variabili d'ambiente (nel LaunchAgent una versione vecchia del server le ignora)
    ap.add_argument("--clip-piano", default=os.environ.get("ATLANTE_CLIP_PIANO", ""),
                    help="JSON {nome del file del brano: {\"id\": ID YouTube}} dei videoclip (anche ATLANTE_CLIP_PIANO)")
    ap.add_argument("--clip-dir", default=os.environ.get("ATLANTE_CLIP_DIR") or "~/Movies/Atlante Videoclip",
                    help="dove tenere videoclip e mappe di allineamento (anche ATLANTE_CLIP_DIR)")
    ap.add_argument("--clip-auto", action="store_true", default=os.environ.get("ATLANTE_CLIP_AUTO") == "1",
                    help="prepara in sottofondo tutti i videoclip, uno alla volta (anche ATLANTE_CLIP_AUTO=1)")
    ap.add_argument("--clip-prima", default=os.environ.get("ATLANTE_CLIP_PRIMA", ""),
                    help="cartella (playlist) i cui brani si preparano per primi (anche ATLANTE_CLIP_PRIMA)")
    ap.add_argument("--ia-modello", default="", help="modello di Ollama per l'IA locale, es. llama3.2 "
                    "(predefinito: il primo installato)")
    ap.add_argument("--ia-indirizzo", default=CONF["ia_indirizzo"], help="indirizzo di Ollama sul Mac")
    ap.add_argument("--pubblico", default=os.environ.get("ATLANTE_PUBBLICO", ""),
                    help="indirizzo dell'app da Internet (tailscale funnel), es. https://mac-mini.tailnet.ts.net:10000/, "
                    "per i link d'invito (anche ATLANTE_PUBBLICO)")
    ap.add_argument("--imposta-password", action="store_true", help="imposta (o cambia) la password per aprire l'app "
                    "da Internet con `tailscale funnel` e esce; la legge da tastiera o dalla prima riga dello standard input")
    a = ap.parse_args()
    file_accesso = os.path.join(os.path.expanduser(a.cache), "accesso.json")
    if a.imposta_password:
        sys.exit(_imposta_password(file_accesso))
    if not a.cartelle:
        ap.error("serve almeno una cartella di musica")
    ACC = Accesso(file_accesso)
    PERS = Persone(os.path.join(os.path.expanduser(a.cache), "persone.json"))
    # login Tailscale di Samuele: dalla rete Tailscale solo loro sono Samuele (vuoto: tutta la rete, come prima)
    PROPRIETARI = {x.strip().lower() for x in os.environ.get("ATLANTE_PROPRIETARI", "").split(",") if x.strip()}
    PUBBLICO = a.pubblico.strip()
    if PUBBLICO and not PUBBLICO.endswith("/"):
        PUBBLICO += "/"
    indirizzo = a.indirizzo.strip()
    if indirizzo and not indirizzo.endswith("/"):
        indirizzo += "/"
    # i brani scaricati da internet entrano nella libreria (se la cartella non è già dentro una delle altre)
    scaricati = os.path.abspath(os.path.expanduser(a.musica_dir))
    cartelle = list(a.cartelle)
    if not any(scaricati == c or _dentro(scaricati, c) for c in (os.path.abspath(os.path.expanduser(x)) for x in cartelle)):
        cartelle.append(scaricati)
    avvia(cartelle, a.cache, indirizzo=indirizzo)
    avvia_extra(cache=a.cache, video_dir=a.video_dir, libri_dir=a.libri_dir, ia_modello=a.ia_modello.strip(),
                ia_indirizzo=a.ia_indirizzo.strip(), musica_dir=a.musica_dir)
    if a.clip_piano:
        CLIP = Videoclip(os.path.abspath(os.path.expanduser(a.clip_piano)), os.path.abspath(os.path.expanduser(a.clip_dir)),
                         auto=a.clip_auto, prima=os.path.abspath(os.path.expanduser(a.clip_prima)) if a.clip_prima else "")
        if a.clip_auto:
            CLIP.avvia()
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
            if controlla_accesso(self) or not _permesso(self):
                return
            if not gestisci_extra(self, "GET") and not gestisci(self) and not self._vai_al_sicuro():
                super().do_GET()

        def do_HEAD(self):
            if controlla_accesso(self) or not _permesso(self):
                return
            if not gestisci_extra(self, "HEAD") and not gestisci(self) and not self._vai_al_sicuro():
                super().do_HEAD()

        def do_POST(self):
            if controlla_accesso(self) or not _permesso(self):
                return
            if not gestisci_extra(self, "POST"):
                self.close_connection = True   # corpo non letto
                self.send_error(501, "Unsupported method ('POST')")

        def do_DELETE(self):
            if controlla_accesso(self) or not _permesso(self):
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
            if not PREFISSO.match(q) and not EXTRA.match(q) and (p.endswith("/") or p.endswith(".html") or LIBERI.match(q)):
                # la Gestione (e ogni pagina) non si apre dentro un altro sito, che potrebbe far toccare i tasti a Samuele
                self.send_header("X-Frame-Options", "SAMEORIGIN")
                self.send_header("Content-Security-Policy", "frame-ancestors 'self'")
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
