#!/usr/bin/env python3
"""Atlante Musica: la musica dei dischi del Mac, servita all'app.

Legge una o più cartelle di musica (per esempio il disco principale e il suo backup),
estrae titolo, artista, album, durata e copertina dai tag MP3 (ID3v2 e ID3v1), M4A/ALAC (MP4),
FLAC, WAV e AIFF (chunk ID3), unisce i doppioni e li offre all'app Musica con queste richieste:

    GET api/musica/catalogo?v=2        brani, playlist e collegamenti (JSON); senza «v=2» (app della versione
                                       precedente) i brani hanno ancora gli id di prima dell'aggiornamento
    GET api/musica/stato               solo lo stato della lettura del disco (leggero)
    GET api/musica/aggiorna            avvia subito una nuova lettura, in sottofondo
    GET api/musica/file/<id>           il brano (con Range: si può saltare avanti)
    GET api/musica/copertina/<chiave>  la copertina (JPEG o PNG; quelle grandi ridotte una volta con sips)
    GET api/musica/alias               id della versione precedente del server -> id di adesso (dopo un aggiornamento)

Solo libreria standard di Python (va bene anche il Python 3.9 di macOS). Sul Mac, per ridurre le copertine
grandi (le PNG che yt-dlp mette negli mp3), usa /usr/bin/sips, che c'è di serie; altrove le serve come sono.

Da solo:

    python3 musica_libreria.py "/Volumes/Disco/Musica" "/Volumes/Backup/Musica" --porta 8790 \
        --playlist "/Volumes/Disco/Playlist" --playlist-json ~/.cloud-web/playlist.json --collegamento "Benessere del Mac=/benessere/"

(le cartelle in ordine di priorità: prima il disco principale, poi il backup; sul Mac mini gira così come servizio
it.samuele.atlante-musica-app, vedi DISCHI-E-PLAYLIST.md). La prima volta (e dopo un aggiornamento da una versione
precedente del server) si leggono i tag di tutti i file, in sottofondo: con dischi USB e migliaia di brani sono minuti.
Intanto l'app riceve il catalogo dell'indice di prima, se c'è (i brani si ascoltano, anche con gli id di prima),
oppure dice che il Mac sta leggendo il disco.

Sul Mac stesso l'app si apre da http://localhost:8790/. Dal telefono serve un indirizzo
HTTPS (Safari e Chrome tengono l'app offline e la lasciano installare solo così). Con Tailscale:

    tailscale serve --bg 8790            # una volta sola: resta attivo anche dopo un riavvio
    python3 musica_libreria.py "/Volumes/Disco/Musica" --indirizzo https://<mac>.<tailnet>.ts.net/

e sull'iPhone si apre sempre e solo https://<mac>.<tailnet>.ts.net/ (un indirizzo fisso: brani
salvati e playlist stanno con l'indirizzo). Con --indirizzo chi apre la vecchia http://<mac>:8790/
da un altro dispositivo viene mandato lì. In alternativa: `tailscale cert` e --cert/--chiave.

Serve anche le pagine dei moduli della scheda Altro (x/, vedi x/CONTRATTO.md) e le loro API sotto api/extra/: stato,
proxy, video (yt-dlp), voci e audiolibro (`say` del Mac), ia (Ollama sul Mac), dispositivo. Opzioni: --video-dir,
--libri-dir, --ia-modello, --ia-indirizzo. Con ATLANTE_ASCOLTO=127.0.0.1 nell'ambiente risponde solo dal Mac stesso
(dietro `tailscale serve`).

Dentro un altro server Python (facoltativo: sul Mac mini gira da solo, con main()):

    import musica_libreria as atlante_musica
    atlante_musica.avvia([cartelle...], "~/.atlante-musica", playlist_cartelle=[...], playlist_json=..., collegamenti=[...])
    # nel gestore delle GET, con percorso = unquote(urlsplit(self.path).path):
    if atlante_musica.servi_app(self, percorso, prefisso="/musica", app="/cartella/dell/app"):
        return

(i vecchi agganci `gestisci(handler)` per api/musica/ e `gestisci_extra(handler, metodo)` per api/extra/, da chiamare
in do_GET, do_HEAD, do_POST e do_DELETE prima di `gestisci`, funzionano ancora.)

L'id di un brano è lo sha1 del suo percorso relativo alla cartella (in forma NFC), primi
16 caratteri: lo stesso file sul disco principale e sul backup ha lo stesso id, così se un
disco manca il brano resta raggiungibile dall'altro e i brani salvati sull'iPhone e le
playlist non si rompono. Un file spostato o rinominato (anche con i tag corretti, come fa
Musica con «Mantieni organizzata la cartella») ha un id nuovo, ma il catalogo dice all'app
che il vecchio id è diventato il nuovo (alias), come per i doppioni.
"""
import argparse
import base64
import gzip
import hashlib
import http.client
import ipaddress
import json
import mimetypes
import os
import platform
import queue
import re
import shutil
import signal
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit

ESTENSIONI = {".mp3", ".m4a", ".m4b", ".aac", ".flac", ".wav", ".aif", ".aiff", ".ogg", ".oga", ".opus", ".alac", ".mp4"}
TIPI = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".m4b": "audio/mp4", ".mp4": "audio/mp4", ".alac": "audio/mp4",
        ".aac": "audio/aac", ".flac": "audio/flac", ".wav": "audio/wav", ".aif": "audio/aiff", ".aiff": "audio/aiff",
        ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg"}
VERSIONE_INDICE = 2         # forma di indice.json
VERSIONE_RECORD = 3         # cosa si legge da ogni file: le voci più vecchie si rileggono una volta, in sottofondo
                            # (3: durata di WAV/AIFF/OGG, compilation, ID3 di AIFF/WAV e ID3v1, cartelle Artista/Album)
ERRORE_LETTURA = "Non sono riuscito a rileggere il disco: riprova tra qualche minuto"
APP = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PASSO_DISCHI = 2            # secondi: ogni quanto si guarda se un disco è stato collegato o staccato (due stat, costa nulla)
SIPS = "/usr/bin/sips"      # di serie su macOS: riduce le copertine grandi senza librerie in più
MINI_SOGLIA = 200 * 1024    # copertine più grandi di così (le PNG di yt-dlp: 0,5-1,5 MB) si servono ridotte
MINI_LATO = 600             # px del lato lungo della copertina ridotta: basta per il lettore a tutto schermo dell'iPhone
_RIDUZIONI = threading.BoundedSemaphore(2)   # al massimo due sips alla volta


def _log(*x):
    try:
        print("Musica:", *x, flush=True)
    except Exception:
        pass


def _errore_breve(e):
    """Un errore in una riga corta per il log: il repr di un UnicodeEncodeError contiene tutto il testo (il catalogo intero)."""
    return "%s: %s" % (type(e).__name__, str(e)[:200])


def _impronta(s):
    """sha1 di un testo (anche con caratteri rovinati: un nome di file non UTF-8, un emoji tagliato a metà)."""
    return hashlib.sha1(s.encode("utf-8", "surrogatepass")).hexdigest()


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


def tag_id3(f, base=0):
    """Tag ID3v2 che comincia alla posizione attuale di f (base: dove sta nel file, 0 per gli MP3; per AIFF e WAV
    il chunk «ID3 »). Oltre ai testi dà la copertina ("pic") e, se i suoi byte stanno così come sono nel file,
    dove si trovano ("pic_pos": posizione, lunghezza): si potrà servirla direttamente dal file senza copiarla."""
    h = f.read(10)
    if len(h) < 10 or h[:3] != b"ID3":
        return None
    v, fl, size = h[3], h[5], _ss32(h, 6)
    d = f.read(size)
    tag_unsync = bool(fl & 0x80) and v < 4
    if tag_unsync:
        d = _unsync(d)
    p = 0
    if fl & 0x40 and v >= 3:
        p = _ss32(d) if v == 4 else struct.unpack(">I", d[:4])[0] + 4
    nomi = {"TIT2": "title", "TT2": "title", "TPE1": "artist", "TP1": "artist", "TALB": "album", "TAL": "album",
            "TPE2": "albumArtist", "TP2": "albumArtist", "TRCK": "track", "TRK": "track", "TPOS": "disc", "TPA": "disc",
            "TYER": "year", "TYE": "year", "TDRC": "year", "TCON": "genre", "TCO": "genre",
            "TCMP": "compilation", "TCP": "compilation", "TLEN": "tlen", "TLE": "tlen"}
    o, pic, tipo_pic, pos = {}, None, -1, None
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
        inizio = p + hs
        fr = d[inizio:inizio + sz]
        diretto = not tag_unsync
        if v == 4:
            if d[p + 9] & 0x0C:          # compresso o cifrato: illeggibile
                p += hs + sz
                continue
            if d[p + 9] & 0x02:
                fr = _unsync(fr)
                diretto = False
            if d[p + 9] & 0x01:
                fr = fr[4:]
                inizio += 4
        elif v == 3 and d[p + 9] & 0xC0:  # compresso o cifrato
            p += hs + sz
            continue
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
                pos = (base + 10 + inizio + i, len(fr) - i) if diretto and i < len(fr) else None
        p += hs + sz
    o["pic"] = pic
    o["pic_pos"] = pos
    tl = o.pop("tlen", "").strip()
    if re.fullmatch(r"\d{1,9}", tl) and 0 < int(tl) < 86400000:
        o["duration"] = int(tl) / 1000.0
    return o


def tag_riff(f):
    """WAV e AIFF: i tag stanno in un chunk «ID3 » / «id3 » (Musica e iTunes lo scrivono negli AIFF, alcuni
    programmi anche nei WAV). {} se il chunk non c'è (la durata la calcola poi _durata)."""
    h = f.read(12)
    if h[:4] in (b"RIFF", b"RF64") and h[8:12] == b"WAVE":
        fmt = "<I"
    elif h[:4] == b"FORM" and h[8:12] in (b"AIFF", b"AIFC"):
        fmt = ">I"
    else:
        return None
    for _ in range(256):
        c = f.read(8)
        if len(c) < 8:
            break
        sz = struct.unpack(fmt, c[4:])[0]
        inizio = f.tell()
        if c[:4] in (b"ID3 ", b"id3 "):
            return tag_id3(f, inizio) or {}
        f.seek(inizio + sz + (sz & 1))
    return {}


def _id3v1(f, o):
    """Tag ID3v1 (gli ultimi 128 byte, «TAG…»): completa quello che manca, per i file che hanno solo quello."""
    f.seek(0, 2)
    if f.tell() < 128:
        return
    f.seek(-128, 2)
    b = f.read(128)
    if b[:3] != b"TAG":
        return
    for k, x in (("title", b[3:33]), ("artist", b[33:63]), ("album", b[63:93]), ("year", b[93:97])):
        v = x.split(b"\x00")[0].decode("latin-1").strip()
        if v and not o.get(k):
            o[k] = v
    if b[125] == 0 and b[126] and not o.get("track"):
        o["track"] = str(b[126])


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

_BITRATE = {
    (1, 1): (32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    (1, 2): (32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (1, 3): (32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (2, 1): (32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    (2, 2): (8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    (2, 3): (8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
_FREQUENZE = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def _frame_mpeg(b, i):
    """Intestazione di un frame MPEG audio in b[i:]: (versione, layer, kbps, frequenza, lunghezza, campioni, mono)."""
    if i + 4 > len(b) or b[i] != 0xFF or (b[i + 1] & 0xE0) != 0xE0:
        return None
    vb, lb = (b[i + 1] >> 3) & 3, (b[i + 1] >> 1) & 3
    bri, fri, pad = b[i + 2] >> 4, (b[i + 2] >> 2) & 3, (b[i + 2] >> 1) & 1
    if vb == 1 or lb == 0 or bri in (0, 15) or fri == 3:
        return None
    layer = 4 - lb
    kbps = _BITRATE[(1 if vb == 3 else 2, layer)][bri - 1]
    fr = _FREQUENZE[vb][fri]
    if layer == 1:
        campioni, lun = 384, (12 * kbps * 1000 // fr + pad) * 4
    else:
        campioni = 1152 if (layer == 2 or vb == 3) else 576
        lun = campioni // 8 * kbps * 1000 // fr + pad
    return vb, layer, kbps, fr, lun, campioni, (b[i + 3] >> 6) == 3


def _fine_id3(f):
    f.seek(0)
    h = f.read(10)
    if len(h) == 10 and h[:3] == b"ID3":
        return 10 + _ss32(h, 6) + (10 if h[3] == 4 and h[5] & 0x10 else 0)
    return 0


def durata_mp3(f, inizio=0):
    """Durata in secondi di un MP3: dall'header Xing/Info o VBRI del primo frame,
    altrimenti stima CBR (dimensione dell'audio × 8 / bitrate). Legge pochi KB."""
    try:
        f.seek(0, 2)
        dim = f.tell()
        f.seek(inizio)
        b = f.read(65536)
        i, h = 0, None
        while i + 4 <= len(b):
            if b[i] == 0xFF:
                h = _frame_mpeg(b, i)
                if h:
                    j = i + h[4]
                    if j + 4 > len(b):
                        break          # il frame seguente è oltre i byte letti: si accetta
                    h2 = _frame_mpeg(b, j)
                    if h2 and h2[0] == h[0] and h2[1] == h[1] and h2[3] == h[3]:
                        break          # due frame di fila: è davvero l'inizio dell'audio
                    h = None
            i += 1
        if not h:
            return 0.0
        vb, layer, kbps, freq, lun, campioni, mono = h
        lato = ((17 if mono else 32) if vb == 3 else (9 if mono else 17)) if layer == 3 else 0
        x, frames = i + 4 + lato, 0
        if b[x:x + 4] in (b"Xing", b"Info") and len(b) >= x + 12:
            if struct.unpack(">I", b[x + 4:x + 8])[0] & 1:
                frames = struct.unpack(">I", b[x + 8:x + 12])[0]
        elif b[i + 36:i + 40] == b"VBRI" and len(b) >= i + 54:
            frames = struct.unpack(">I", b[i + 50:i + 54])[0]
        if frames:
            return frames * campioni / float(freq)
        audio = dim - inizio - i
        f.seek(max(0, dim - 128))
        if f.read(3) == b"TAG":
            audio -= 128
        return max(0.0, audio * 8.0 / (kbps * 1000))
    except (OSError, struct.error, KeyError, IndexError):
        return 0.0


def durata_riff(f, dim):
    """WAV: byte del chunk «data» diviso i byte al secondo del chunk «fmt »."""
    f.seek(0)
    h = f.read(12)
    if h[:4] not in (b"RIFF", b"RF64") or h[8:12] != b"WAVE":
        return 0.0
    al_secondo = 0
    for _ in range(256):
        c = f.read(8)
        if len(c) < 8:
            break
        cid, sz = c[:4], struct.unpack("<I", c[4:])[0]
        if cid == b"fmt ":
            d = f.read(sz + (sz & 1))
            al_secondo = struct.unpack("<I", d[8:12])[0]
        elif cid == b"data":
            resto = dim - f.tell()
            return (resto if sz in (0, 0xFFFFFFFF) else min(sz, resto)) / al_secondo if al_secondo else 0.0
        else:
            f.seek(sz + (sz & 1), 1)
    return 0.0


def durata_aiff(f):
    """AIFF: campioni e frequenza (numero a 80 bit) del chunk «COMM»."""
    f.seek(0)
    h = f.read(12)
    if h[:4] != b"FORM" or h[8:12] not in (b"AIFF", b"AIFC"):
        return 0.0
    for _ in range(256):
        c = f.read(8)
        if len(c) < 8:
            break
        cid, sz = c[:4], struct.unpack(">I", c[4:])[0]
        if cid == b"COMM":
            d = f.read(sz)
            campioni = struct.unpack(">I", d[2:6])[0]
            esp, mant = ((d[8] & 0x7F) << 8) | d[9], int.from_bytes(d[10:18], "big")
            hz = mant * 2.0 ** (esp - 16383 - 63) if mant else 0
            return campioni / hz if hz else 0.0
        f.seek(sz + (sz & 1), 1)
    return 0.0


def durata_ogg(f, dim):
    """OGG Vorbis / Opus: posizione (granule) dell'ultima pagina diviso la frequenza."""
    f.seek(0)
    b = f.read(4096)
    if b[:4] != b"OggS":
        return 0.0
    hz, salta = 0, 0
    k = b.find(b"\x01vorbis")
    if k >= 0:
        hz = struct.unpack("<I", b[k + 12:k + 16])[0]
    else:
        k = b.find(b"OpusHead")
        if k >= 0:
            hz, salta = 48000, struct.unpack("<H", b[k + 10:k + 12])[0]
    if not hz:
        return 0.0
    f.seek(max(0, dim - 65536))
    t = f.read()
    j = t.rfind(b"OggS")
    while j >= 0:
        if j + 14 <= len(t):
            g = struct.unpack("<q", t[j + 6:j + 14])[0]
            if g > 0:
                return max(0, g - salta) / hz
        j = t.rfind(b"OggS", 0, j)
    return 0.0


def _durata(f, est):
    try:
        if est in (".mp3", ".mp2", ".mpga"):
            return durata_mp3(f, _fine_id3(f))
        f.seek(0, 2)
        dim = f.tell()
        if est == ".wav":
            return durata_riff(f, dim)
        if est in (".aif", ".aiff"):
            return durata_aiff(f)
        if est in (".ogg", ".oga", ".opus"):
            return durata_ogg(f, dim)
    except (OSError, ValueError, struct.error, IndexError, ZeroDivisionError, OverflowError):
        pass
    return 0.0


def leggi_tag(percorso):
    """Tag del file: titolo, artista, …, durata, copertina. {} se il file non ha tag leggibili;
    {"_errore": True} se il disco non ha risposto (EIO a metà rsync, disco USB che si stacca): il brano
    si rilegge alla lettura dopo, invece di restare per sempre senza durata, copertina e titolo."""
    o = {}
    est = os.path.splitext(percorso)[1].lower()
    try:
        with open(percorso, "rb") as f:
            for lettore in (tag_id3, tag_mp4, tag_flac, tag_riff):
                f.seek(0)
                try:
                    r = lettore(f)
                except (ValueError, struct.error, IndexError):
                    r = None
                if r is not None:
                    o = r
                    break
            if not (o.get("title") and o.get("artist")):
                _id3v1(f, o)
            if not o.get("duration"):
                d = _durata(f, est)
                if d > 0:
                    o["duration"] = d
    except OSError:
        return {"_errore": True}
    except (ValueError, struct.error, IndexError):
        pass
    return o


def da_nome(nome):
    """Titolo e artista dal nome del file (solo per i file senza tag). Il numero all'inizio si toglie solo se
    è chiaramente il numero della traccia ("01 - …", "1. …", "01 - Artista - Titolo"): così restano gli
    artisti col numero nel nome ("883 - …", "50 Cent - …", "3 Doors Down - …", "99 Posse - …")."""
    n = os.path.splitext(nome)[0].replace("_", " ").strip()
    m = re.match(r"(\d{1,3})\s*([-._)])\s*(\S.*)$", n)
    if m and (" - " in m.group(3) or m.group(1).startswith("0") or m.group(2) in ".)"):
        n = m.group(3)
    else:
        n = re.sub(r"^0\d{0,2}\s+(?=\D)", "", n)
    if " - " in n:
        a, t = n.split(" - ", 1)
        return {"artist": a.strip(), "title": t.strip()}
    return {"title": n}


def _numero_traccia(nome):
    """Il numero della traccia all'inizio del nome del file («01 Titolo», «1. Titolo», «01 - Titolo»), 0 se non c'è
    o se fa parte del nome dell'artista («883 - …», «50 Cent - …»): la stessa regola di da_nome."""
    n = os.path.splitext(nome)[0].replace("_", " ").strip()
    m = re.match(r"(\d{1,3})\s*([-._)])\s*(\S.*)$", n)
    if m and (" - " in m.group(3) or m.group(1).startswith("0") or m.group(2) in ".)"):
        return int(m.group(1))
    m = re.match(r"(0\d{0,2})\s+\D", n)
    return int(m.group(1)) if m else 0


def _intero(s):
    m = re.match(r"\s*(\d+)", s or "")
    return int(m.group(1)) if m else 0


# ---------- piccoli aiuti ----------

def _nfc(s):
    return unicodedata.normalize("NFC", s)


def _assoluto(c):
    return os.path.abspath(os.path.expanduser(c))


def _reale(p):
    """os.path.realpath che non fallisce: un link che sparisce mentre lo si legge (disco staccato,
    cartella Playlist rigenerata) non deve far fallire tutta la lettura. None se non si risolve."""
    try:
        return os.path.realpath(p)
    except (OSError, ValueError):
        return None


def id_brano(rel):
    """Id di un brano: sha1 del percorso relativo alla sua cartella (NFC), primi 16 caratteri."""
    return _impronta(_nfc(rel))[:16]


def _confronto(s):
    """Artista o titolo ridotti all'osso, per riconoscere i doppioni: minuscole, senza accenti né punteggiatura."""
    s = unicodedata.normalize("NFKD", s or "").casefold()
    return " ".join(re.findall(r"\w+", "".join(c for c in s if not unicodedata.combining(c))))


def _parole(s):
    return " " + re.sub(r"\W+", " ", (s or "").lower()).strip() + " "


def _ordine_numerato(nome):
    m = re.match(r"\s*(\d+)", nome)
    return (0, int(m.group(1)), nome) if m else (1, 0, nome)


def _da_base64(s):
    if not isinstance(s, str) or not s:
        return None
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8")
    except ValueError:
        return None


def _collegamenti(lista):
    out = []
    for c in lista or []:
        if isinstance(c, dict) and isinstance(c.get("nome"), str) and isinstance(c.get("url"), str) and c["nome"].strip() and c["url"].strip():
            out.append({"nome": c["nome"].strip(), "url": c["url"].strip()})
    return out


def _playlist_valida(p):
    return (isinstance(p, dict) and isinstance(p.get("id"), str) and isinstance(p.get("nome"), str)
            and p.get("fonte") in ("cartella", "radio") and isinstance(p.get("brani"), list))


def _cammina(radice):
    """I file audio sotto radice. Salta link simbolici (a file e a cartelle), file e cartelle col punto
    (per esempio .atlante-tmp, dove rsync tiene i file a metà) e file vuoti."""
    pila = [radice]
    while pila:
        cart = pila.pop()
        try:
            with os.scandir(cart) as it:
                voci = list(it)
        except OSError:
            if cart == radice:
                raise
            continue
        for e in voci:
            if e.name.startswith("."):
                continue
            try:
                if e.is_symlink():
                    continue
                if e.is_dir(follow_symlinks=False):
                    pila.append(e.path)
                    continue
                if os.path.splitext(e.name)[1].lower() not in ESTENSIONI or not e.is_file(follow_symlinks=False):
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if st.st_size > 0:
                yield e.path, st


def _montaggio_vuoto(c):
    """Cartella in /Volumes rimasta lì vuota dopo che il suo disco è stato staccato male (macOS a volte la lascia):
    non è un disco senza musica, è un disco che non c'è."""
    try:
        return os.path.dirname(c.rstrip(os.sep)) == "/Volumes" and not os.path.ismount(c) and not os.listdir(c)
    except OSError:
        return False


COPERTINE_CARTELLA = ("cover.jpg", "Cover.jpg", "folder.jpg", "Folder.jpg", "cover.png", "front.jpg", "AlbumArt.jpg")


def _copertina_cartella(cart):
    for n in COPERTINE_CARTELLA:
        q = os.path.join(cart, n)
        try:
            if os.path.isfile(q) and not os.path.islink(q) and 0 < os.path.getsize(q) < 20 * 2 ** 20:
                with open(q, "rb") as f:
                    dati = f.read()
                return hashlib.sha1(dati).hexdigest()[:20] + os.path.splitext(n)[1].lower(), q
        except OSError:
            pass
    return None


# ---------- la libreria ----------

class _Dati:
    """Una fotografia del catalogo. Si sostituisce in blocco: chi la legge non vede mai metà lavoro."""

    def __init__(self):
        self.brani = []        # voci pubbliche del catalogo
        self.percorsi = {}     # id -> percorsi sul disco (il primo è il preferito)
        self.alias = {}        # id di un doppione, di un file spostato o di una copia tenuta in passato e ora sparita -> id tenuto
        self.storia = []       # coppie [id, id] unite come doppioni o file spostati, anche in letture passate (vedi _unisci)
        self.alias_vecchi = {} # id della versione precedente del server -> id di adesso (fuori dal catalogo: api/musica/alias)
        self.migra = []        # tutte le coppie [id vecchio, id nuovo] da cui vengono (si salvano nell'indice, anche quelle
                               # col brano nuovo ora assente: un disco staccato alla prima lettura non le fa perdere)
        self.migra_dal = 0     # quando è stato letto l'indice della versione precedente (ms)
        self.migra_aperta = False   # si stanno ancora cercando i vecchi id dai percorsi (vedi Libreria._id_dai_percorsi)
        self.segno_vecchi = 0  # impronta di alias_vecchi: cambia quando cambiano, e allora l'app li richiede
        self.copertine = {}    # chiave -> [(percorso, (posizione, lunghezza) o None)]
        self.playlist = []     # [{"id", "nome", "fonte", "brani", "_da"}]
        self.aggiornato = 0
        self.presenti = []     # cartelle lette davvero all'ultima scansione
        self.json = b"{}"      # catalogo già pronto, senza "scansione"
        self.json_per = {}     # login Tailscale -> catalogo senza le playlist della radio di altre persone
        self.json_prima = None # catalogo con gli id della versione precedente, per la sua app (vedi Libreria._json_prima)
        self.cerca_abs = {}    # percorso assoluto (NFC) -> id
        self.cerca_rel = {}    # percorso relativo (NFC) -> id
        self.cerca_nome = {}   # nome del file (NFC, minuscolo) -> id


class Libreria:
    def __init__(self, cartelle, cache, playlist_cartelle=None, playlist_json=None, collegamenti=None,
                 radio_per_persona=False, indirizzo=""):
        self.radio_per_persona = radio_per_persona   # True: ognuno vede solo le playlist della radio che ha fatto lui (campo "di")
        self.indirizzo = indirizzo or ""             # indirizzo https fisso dell'app (facoltativo): l'app lo propone a chi la apre in http
        self.cartelle = []
        for c in cartelle:
            c = _assoluto(c)
            if c not in self.cartelle:
                self.cartelle.append(c)
        self.playlist_cartelle = [_assoluto(c) for c in (playlist_cartelle or [])]
        self.playlist_json = _assoluto(playlist_json) if playlist_json else None
        self.collegamenti = _collegamenti(collegamenti)
        self.cache = _assoluto(cache)
        self.dir_copertine = os.path.join(self.cache, "copertine")
        self.file_indice = os.path.join(self.cache, "indice.json")
        try:
            os.makedirs(self.dir_copertine, exist_ok=True)
        except OSError as e:
            _log("non riesco a creare", self.dir_copertine, e)
        self._basi = []          # nomi delle cartelle (es. "Da YouTube"): da lì in poi c'è il percorso relativo
        for c in self.cartelle:
            b = _nfc(os.path.basename(c))
            if b and b not in self._basi:
                self._basi.append(b)
        self._lucchetto = threading.Lock()   # una sola scansione alla volta
        self._blocco = threading.Lock()
        self.scansione = {"in_corso": False, "letti": 0}
        self.record = {}                     # percorso -> quello che si è letto del file
        self.dati = _Dati()
        self.dati.json = self._json_pubblico(self.dati)
        self._firma_dati = None              # impronta di self.dati: se una lettura non cambia niente, niente da riscrivere
        self._da_salvare = False             # l'ultimo salvataggio dell'indice non è riuscito: riprovare
        self._fermo = threading.Event()      # ferma ciclo() (per le prove)
        self._mini_no = set()                # copertine che sips non è riuscito a ridurre: si servono come sono
        self._migra = []                     # [id vecchio, id nuovo] dall'indice della versione precedente (vedi _id_vecchi)
        self._migra_dal = 0
        self._migra_aperta = False           # vedi _id_dai_percorsi
        self._ids_prima = set()              # gli id del catalogo della versione precedente: quelli che ha la sua app
        self._carica_indice()

    # --- indice salvato: l'avvio è istantaneo, la scansione vera gira dopo, in sottofondo ---

    def _carica_indice(self):
        try:
            with open(self.file_indice, encoding="utf-8") as f:
                salvato = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(salvato, dict):
            return
        if "record" not in salvato and isinstance(salvato.get("brani"), list):
            # indice della versione precedente del server (stessa cartella ~/.atlante-musica): lì l'id era lo sha1 del
            # percorso assoluto. Finché la prima lettura non finisce (con i dischi USB sono minuti) si serve quel catalogo,
            # con i suoi id: i brani del Mac si ascoltano subito. Dopo, i vecchi id diventano alias dei nuovi, così
            # l'iPhone non perde preferiti, ascolti, playlist, coda e brani salvati
            self._migra = self._id_vecchi(salvato["brani"])
            self._migra_dal = int(time.time() * 1000)
            # anche se quell'indice è vuoto (la versione f63b7f3 lo riscriveva vuoto dopo un giro col disco staccato):
            # i vecchi id si ricalcolano dai percorsi dei file (vedi _id_dai_percorsi)
            self._migra_aperta = True
            record = self._record_vecchi(salvato["brani"])
            d = self._catalogo_vecchio(salvato["brani"])
            self._ids_prima = {v["id"] for v in d.brani}
            with self._blocco:
                self.record, self.dati = record, d
            _log("indice della versione precedente: %d brani, da ricollegare dopo la prima lettura" % len(d.brani))
            return
        if salvato.get("versione") != VERSIONE_INDICE:
            return
        self._migra = [c for c in salvato.get("migra") or [] if isinstance(c, list) and len(c) == 2 and all(isinstance(x, str) for x in c)]
        self._migra_dal = salvato.get("migra_dal") if isinstance(salvato.get("migra_dal"), int) else 0
        self._migra_aperta = salvato.get("migra_aperta") is True
        self._ids_prima = {x for x in salvato.get("ids_prima") or [] if isinstance(x, str)}
        record = {}
        for r in salvato.get("record") or []:
            if (isinstance(r, dict) and isinstance(r.get("p"), str) and isinstance(r.get("rel"), str)
                    and isinstance(r.get("m"), dict) and r.get("rad") in self.cartelle):
                record[r["p"]] = r
        vecchi = _Dati()
        vecchi.playlist = [p for p in salvato.get("playlist") or [] if _playlist_valida(p)]
        vecchi.storia = [c for c in salvato.get("storia") or []
                         if isinstance(c, list) and len(c) == 2 and all(isinstance(x, str) for x in c)]
        presenti = [c for c in salvato.get("presenti") or [] if c in self.cartelle]
        d = self._costruisci(record, presenti, vecchi, salvato.get("aggiornato") or 0, leggi_fonti=False, migra=self._migra)
        with self._blocco:
            self.record, self.dati = record, d
        self._firma_dati = self._firma(record, d)
        _log("indice caricato: %d brani, %d playlist" % (len(d.brani), len(d.playlist)))

    def _catalogo_vecchio(self, voci):
        """Catalogo provvisorio dall'indice della versione precedente: i suoi id e i suoi percorsi, «aggiornato» 0 (l'app
        sa che il Mac sta ancora leggendo e non toglie niente). Le copertine di allora sono in dir_copertine."""
        d = _Dati()
        collegate = set(self._collegate())
        for v in voci:
            if not isinstance(v, dict):
                continue
            i, p = v.get("id"), v.get("_p")
            if not isinstance(i, str) or not re.fullmatch(r"[0-9a-f]{16}", i) or not isinstance(p, str) or i in d.percorsi:
                continue
            voce = {k: x for k, x in v.items() if not k.startswith("_")}
            if v.get("_comp"):
                voce["compilation"] = True
            rad = next((c for c in self.cartelle if p.startswith(c + os.sep)), None)
            if rad is not None and rad not in collegate:
                voce["assente"] = True
            d.brani.append(voce)
            d.percorsi[i] = [p]
        d.json = self._json_pubblico(d)
        return d

    def _dove(self, q, reali):
        """(cartella dei brani, resto del percorso) per un percorso assoluto scritto da una versione precedente: dentro una
        delle cartelle, oppure, risolvendo i link (per esempio «~/Musica Cloud/…»), dentro il suo percorso reale."""
        for c in self.cartelle:
            if q.startswith(c + os.sep):
                return c, q[len(c) + 1:]
        vq = _reale(q)
        if vq:
            for c, rc in reali:
                for b in (c, rc):
                    if b and vq.startswith(b + os.sep):
                        return c, vq[len(b) + 1:]
        return None, None

    def _record_vecchi(self, voci):
        """Record dalle voci dell'indice della versione precedente, da rileggere (f=None). Se un disco è staccato alla
        prima lettura, i suoi brani restano nel catalogo «assenti» col loro id nuovo (come ogni brano di un disco
        staccato): i vecchi id hanno subito il loro alias e sull'iPhone non sparisce niente. Quando il disco torna si
        rileggono. Le copertine di allora sono in dir_copertine."""
        record, reali = {}, [(c, _reale(c)) for c in self.cartelle]
        for v in voci:
            if not isinstance(v, dict) or not isinstance(v.get("_p"), str):
                continue
            rad, resto = self._dove(v["_p"], reali)
            if not rad or not resto:
                continue
            p = os.path.join(rad, resto)
            rel = _nfc(resto.replace(os.sep, "/"))
            testo = lambda k: v[k] if isinstance(v.get(k), str) else ""   # noqa: E731
            numero = lambda k: v[k] if isinstance(v.get(k), (int, float)) and not isinstance(v.get(k), bool) else 0   # noqa: E731
            cop = v.get("cover") if isinstance(v.get("cover"), str) and re.fullmatch(r"[0-9a-f]{20}\.(?:jpg|jpeg|png)", v["cover"]) else None
            r = {"p": p, "rad": rad, "rel": rel, "f": None, "v": VERSIONE_RECORD, "cop": cop,
                 "m": {"title": testo("title") or rel.rsplit("/", 1)[-1], "artist": testo("artist"), "album": testo("album"),
                       "albumArtist": testo("albumArtist"), "track": int(numero("track")), "disc": int(numero("disc")),
                       "year": testo("year")[:4], "genre": testo("genre"), "duration": numero("duration"),
                       "added": int(numero("added")), "cover": cop}}
            if cop:
                r["cf"] = os.path.join(self.dir_copertine, cop)
            if v.get("_comp"):
                r["comp"] = True
            record.setdefault(p, r)
        return record

    def _id_dai_percorsi(self, record):
        """Gli id che le versioni precedenti davano ai file di adesso: sha1 del percorso assoluto, primi 16 caratteri.
        Il percorso è quello della cartella dei brani oppure quello passando da un link della cartella di casa che porta
        lì o più su (per esempio «~/Musica Cloud» -> «/Volumes/Cloud/Musica Cloud»), come si poteva averla data a quel
        server. Servono quando l'indice di allora non c'è più o è vuoto: l'iPhone ha ancora quegli id.
        Si cercano solo dopo un aggiornamento da una versione precedente e finché non si è letto ogni disco almeno una
        volta (poi i file nuovi non possono avere un id vecchio)."""
        forme = {c: [c] for c in self.cartelle}
        casa = os.path.expanduser("~")
        try:
            with os.scandir(casa) as it:
                link = [e.path for e in it if e.is_symlink()]
        except OSError:
            link = []
        for c in self.cartelle:
            vera = _reale(c) or c
            for q in link:
                dest = _reale(q)
                if dest and dest != os.sep and (vera == dest or vera.startswith(dest.rstrip(os.sep) + os.sep)):
                    forme[c].append(q + vera[len(dest.rstrip(os.sep)):])
        coppie = []
        for r in record.values():
            rad, p = r.get("rad"), r.get("p") or ""
            if rad not in forme or not p.startswith(rad + os.sep):
                continue
            resto, nuovo = p[len(rad):], id_brano(r["rel"])
            for f in forme[rad]:
                try:
                    vecchio = hashlib.sha1((f + resto).encode("utf-8")).hexdigest()[:16]
                except UnicodeEncodeError:   # nome non UTF-8: la versione di allora non lo leggeva nemmeno
                    continue
                if vecchio != nuovo:
                    coppie.append([vecchio, nuovo])
        return coppie

    def _aggiungi_migra(self, coppie):
        noti = {tuple(c) for c in self._migra}
        nuove = []
        for c in coppie:
            if tuple(c) not in noti:
                noti.add(tuple(c))
                nuove.append(c)
        if nuove:
            self._migra = self._migra + nuove

    def _id_vecchi(self, voci):
        """Coppie [id vecchio, id nuovo] dalle voci dell'indice di prima ({"id", "_p": percorso assoluto, …}).
        Il percorso relativo si prende dalla cartella dei brani che lo contiene, oppure da dopo «Da YouTube/»."""
        coppie = []
        for v in voci:
            if not isinstance(v, dict) or not isinstance(v.get("id"), str) or not isinstance(v.get("_p"), str):
                continue
            p, rel = v["_p"], None
            for c in self.cartelle:
                if p.startswith(c + os.sep):
                    rel = _nfc(os.path.relpath(p, c).replace(os.sep, "/"))
                    break
            if rel is None:
                rel = self._dopo_radice(_nfc(p))
            if rel and id_brano(rel) != v["id"]:
                coppie.append([v["id"], id_brano(rel)])
        return coppie

    @staticmethod
    def _firma(record, d):
        """Quello che finisce nell'indice, tranne l'ora: file (percorso, dimensione e data), dischi letti, playlist, doppioni."""
        return ({p: (r.get("f"), r.get("rad"), r.get("v")) for p, r in record.items()}, list(d.presenti), d.playlist, d.storia, d.migra,
                d.migra_aperta)

    def _salva_indice(self, record, d):
        """Scrive indice.json (prima in un file temporaneo col nome unico, poi lo sostituisce). True se è andata bene.
        ensure_ascii: un nome di file non UTF-8 (possibile su un disco exFAT/NTFS) si salva e si rilegge uguale."""
        dati = {"versione": VERSIONE_INDICE, "aggiornato": d.aggiornato, "presenti": d.presenti,
                "record": list(record.values()), "playlist": d.playlist, "storia": d.storia}
        if d.migra:
            dati["migra"], dati["migra_dal"] = d.migra, d.migra_dal
        if d.migra_aperta:
            dati["migra_aperta"] = True
        if self._ids_prima:
            dati["ids_prima"] = sorted(self._ids_prima)
        tmp = None
        try:
            testo = json.dumps(dati, separators=(",", ":"))   # in un colpo: molto più veloce di json.dump
            fd, tmp = tempfile.mkstemp(dir=self.cache, prefix="indice.", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(testo)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.file_indice)
            return True
        except (OSError, ValueError) as e:
            _log("non riesco a salvare l'indice:", _errore_breve(e))
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            return False

    # --- scansione ---

    def scansiona(self):
        """Rilegge i dischi adesso (se un'altra lettura è in corso, aspetta che finisca). Dà il numero di brani."""
        with self._lucchetto:
            self.scansione = {"in_corso": True, "letti": 0}
            errore = None
            try:
                return self._scansiona()
            except Exception:
                errore = ERRORE_LETTURA
                raise
            finally:
                self._fine_scansione(errore)

    def _fine_scansione(self, errore):
        s = {"in_corso": False, "letti": self.scansione.get("letti", 0)}
        if errore:   # l'app lo dice a Samuele invece di «Libreria aggiornata»
            s["errore"] = errore
        self.scansione = s

    def avvia_scansione(self):
        """Avvia una lettura in sottofondo e torna subito. False se ce n'è già una in corso."""
        if not self._lucchetto.acquire(blocking=False):
            return False
        self.scansione = {"in_corso": True, "letti": 0}
        try:
            threading.Thread(target=self._esegui, daemon=True, name="musica-scansione").start()
        except Exception:
            self.scansione = {"in_corso": False, "letti": 0}
            self._lucchetto.release()
            raise
        return True

    def _esegui(self):   # da chiamare col lucchetto già preso
        errore = None
        try:
            t0 = time.time()
            n = self._scansiona()
            _log("%d brani, %d playlist (letti in %.1f s)" % (n, len(self.dati.playlist), time.time() - t0))
        except Exception as e:   # il disco può sparire a metà lettura
            errore = ERRORE_LETTURA
            _log("lettura non riuscita:", _errore_breve(e))
        finally:
            self._fine_scansione(errore)
            self._lucchetto.release()

    def _collegate(self):
        return [c for c in self.cartelle if os.path.isdir(c)]

    def ciclo(self, intervallo=600):
        """Tiene la libreria aggiornata: subito una lettura, poi una ogni `intervallo` secondi. In più, ogni
        PASSO_DISCHI secondi guarda quali cartelle ci sono: se un disco è stato collegato (anche dopo l'avvio,
        per esempio col Mac appena riacceso) o staccato, rilegge subito, senza aspettare `intervallo`."""
        prossima, viste = 0.0, None
        while not self._fermo.is_set():
            collegate = self._collegate()
            if (time.time() >= prossima or collegate != viste) and self._lucchetto.acquire(blocking=False):
                # se c'è già una lettura in corso (tasto Aggiorna) si riprova al passo dopo
                viste = collegate
                self.scansione = {"in_corso": True, "letti": 0}
                self._esegui()
                prossima = time.time() + max(30, intervallo)
            self._fermo.wait(PASSO_DISCHI)

    def _scansiona(self):
        vecchi = self.record
        nuovi, presenti, visti, gemelli, cop_cart = {}, [], set(), {}, {}
        # prima lettura in assoluto (nessun indice da servire intanto): il catalogo si riempie man mano, così i brani già
        # letti si ascoltano subito invece di aspettare i minuti che servono per leggere i tag di migliaia di file
        a_pezzi = not vecchi and not self.dati.brani and not self.dati.aggiornato
        pezzo = time.time() + 1
        for rad in self.cartelle:
            trovati, ok = {}, os.path.isdir(rad)
            if ok:
                try:
                    for p, st in _cammina(rad):
                        rel = _nfc(os.path.relpath(p, rad).replace(os.sep, "/"))
                        firma = "%d:%d" % (st.st_size, int(st.st_mtime))
                        r = vecchi.get(p)
                        if (not r or r.get("f") != firma or r.get("rad") != rad or r.get("rel") != rel
                                or r.get("v") != VERSIONE_RECORD):
                            g = gemelli.get(rel)
                            if g and g["f"].split(":")[0] == str(st.st_size):
                                # copia dello stesso file su un altro disco: non serve rileggerla
                                r = dict(g, p=p, rad=rad, f=firma)
                                r["m"] = dict(g["m"], added=int(st.st_mtime * 1000))
                                if r.get("cf") and os.path.dirname(r["cf"]) != self.dir_copertine:
                                    # copertina di cartella (cover.jpg): quella accanto a questo file, non all'altro disco
                                    self._copertina_di_cartella(r, cop_cart)
                            else:
                                r = self._record(p, rad, rel, st, firma, cop_cart)
                        trovati[p] = r
                        if r.get("f"):   # un file letto male (EIO) non fa da modello per l'altro disco
                            gemelli.setdefault(rel, r)
                        visti.add(rel)
                        self.scansione = {"in_corso": True, "letti": len(visti)}
                        if a_pezzi and time.time() >= pezzo:
                            pezzo = self._pubblica_pezzo(nuovi, trovati, presenti + [rad])
                except OSError as e:
                    _log("lettura di %s interrotta: %s" % (rad, _errore_breve(e)))
                    ok = False
                if ok and not os.path.isdir(rad):   # disco staccato a metà lettura
                    ok = False
                if ok and not trovati and _montaggio_vuoto(rad) and any(r.get("rad") == rad for r in vecchi.values()):
                    _log("%s è vuota: la tratto come un disco staccato" % rad)   # disco smontato male: resta la cartella vuota
                    ok = False
            if ok:
                presenti.append(rad)
                nuovi.update(trovati)
            else:
                # disco non collegato: i suoi brani restano (segnati "assente") finché non torna
                for p, r in vecchi.items():
                    if r.get("rad") == rad:
                        nuovi[p] = r
                        visti.add(r["rel"])
                self.scansione = {"in_corso": True, "letti": len(visti)}
        rinomine = self._rinomine(vecchi, nuovi, presenti)
        if self._migra_aperta:   # dopo un aggiornamento da una versione precedente: i vecchi id ricalcolati dai percorsi
            self._aggiungi_migra(self._id_dai_percorsi(nuovi))
            if len(presenti) == len(self.cartelle):   # ogni disco letto almeno una volta: i file nuovi non hanno id vecchi
                self._migra_aperta = False
        # i vecchi id della versione precedente: alias a parte (api/musica/alias), ricordati nell'indice
        d = self._costruisci(nuovi, presenti, self.dati, int(time.time() * 1000), rinomine=rinomine, migra=self._migra)
        firma = self._firma(nuovi, d)
        with self._blocco:
            if firma != self._firma_dati:
                self.record, self.dati = nuovi, d
                self._firma_dati = firma
                self._da_salvare = True
            else:   # niente di nuovo: catalogo e indice restano quelli di prima (niente riscritture inutili sull'SSD)
                self.record = nuovi
            d = self.dati
        if self._da_salvare:
            self._da_salvare = not self._salva_indice(nuovi, d)
        return len(d.brani)

    def _pubblica_pezzo(self, nuovi, trovati, presenti):
        """Prima lettura in assoluto: catalogo coi brani letti finora, «aggiornato» 0 (l'app sa che il Mac sta ancora
        leggendo e non toglie niente). Niente playlist né indice: arrivano a fine lettura. Dà quando rifarlo: circa ogni
        secondo, più di rado se il catalogo è già grande (rifarlo costa)."""
        t0 = time.time()
        parziali = dict(nuovi)
        parziali.update(trovati)
        d = self._costruisci(parziali, presenti, _Dati(), 0, leggi_fonti=False)
        with self._blocco:
            if not self.dati.aggiornato:
                self.dati = d
        fine = time.time()
        return fine + max(1.0, 4 * (fine - t0))

    @staticmethod
    def _rinomine(vecchi, nuovi, presenti):
        """File spostati o rinominati dall'ultima lettura, su un disco che c'è: coppie [id vecchio, id nuovo].
        Stesso file (stessa dimensione e data) con un altro nome, oppure stesso titolo, numero di brano e disco e
        durata entro ±1 s con artista o album uguali (Musica che corregge i tag e sposta il file in «Artista/Album/»).
        Si ricordano come i doppioni: il vecchio id diventa un alias del nuovo, così l'app sposta preferiti, ascolti,
        playlist e copia sull'iPhone sul brano nuovo."""
        rel_nuovi = {r["rel"] for r in nuovi.values()}
        spariti = {}
        for r in vecchi.values():
            if r["rel"] not in rel_nuovi and r.get("rad") in presenti and r.get("f"):
                spariti.setdefault(r["rel"], r)
        if not spariti:
            return []
        rel_vecchi = {r["rel"] for r in vecchi.values()}
        arrivati = {}
        for r in nuovi.values():
            if r["rel"] not in rel_vecchi:
                arrivati.setdefault(r["rel"], r)
        if not arrivati:
            return []
        per_firma = {}
        for rel, r in sorted(arrivati.items()):
            per_firma.setdefault(r.get("f"), []).append(rel)
        coppie, usati, resto = [], set(), []
        for rel, r in sorted(spariti.items()):   # prima i file solo spostati: stessa dimensione, data e titolo
            t = _confronto(r["m"].get("title"))
            x = next((x for x in per_firma.get(r["f"], ()) if x not in usati and _confronto(arrivati[x]["m"].get("title")) == t), None)
            if x:
                usati.add(x)
                coppie.append([id_brano(rel), id_brano(x)])
            else:
                resto.append((rel, r))
        for rel, r in resto:   # poi quelli con i tag corretti e spostati
            m = r["m"]
            t, dur = _confronto(m.get("title")), m.get("duration") or 0
            if not t or dur <= 0:
                continue
            cand = [x for x, n in sorted(arrivati.items()) if x not in usati
                    and _confronto(n["m"].get("title")) == t and n["m"].get("track") == m.get("track")
                    and n["m"].get("disc") == m.get("disc") and abs((n["m"].get("duration") or 0) - dur) <= 1
                    and any(_confronto(n["m"].get(k)) == _confronto(m.get(k)) != "" for k in ("artist", "album"))]
            if cand:
                x = max(cand, key=lambda x: sum(_confronto(arrivati[x]["m"].get(k)) == _confronto(m.get(k))
                                                for k in ("artist", "album", "albumArtist", "year")))
                usati.add(x)
                coppie.append([id_brano(rel), id_brano(x)])
        return coppie

    def _copertina_di_cartella(self, r, cop_cart):
        cart = os.path.dirname(r["p"])
        if cart not in cop_cart:
            cop_cart[cart] = _copertina_cartella(cart)
        if cop_cart[cart]:
            r["cop"], r["cf"] = cop_cart[cart]
            r["m"] = dict(r["m"], cover=r["cop"])
        else:
            r["cop"] = None
            r.pop("cf", None)

    def _record(self, p, rad, rel, st, firma, cop_cart):
        t = leggi_tag(p)
        nome = rel.rsplit("/", 1)[-1]
        # senza tag: le cartelle «Artista/Album/NN Titolo.ext» (come le tiene iTunes/Musica), se il file è almeno due livelli
        # sotto; lì il numero all'inizio del nome è sempre quello della traccia (anche «12 …» e «1-03 …», disco-traccia)
        parti = rel.split("/")
        artista_cart, album_cart, traccia = "", "", _numero_traccia(nome)
        nm = da_nome(nome)
        if len(parti) >= 3:
            artista_cart, album_cart = parti[-3], parti[-2]
            m = re.match(r"(?:\d{1,2}-)?(\d{1,3})\s+(?=\D)", nome)
            if m:
                traccia, nm = int(m.group(1)), da_nome(nome[m.end():])
        r = {"p": p, "rad": rad, "rel": rel, "f": None if t.get("_errore") else firma, "v": VERSIONE_RECORD, "cop": None}
        if (t.get("compilation") or "").strip().lower() in ("1", "true", "yes"):
            r["comp"] = True
        pic = t.get("pic")
        if pic and pic[0]:
            dati, mime = pic
            chiave = hashlib.sha1(dati).hexdigest()[:20] + (".png" if "png" in mime else ".jpg")
            pos = t.get("pic_pos")
            if pos and pos[1] == len(dati):
                r["cop"], r["cpos"] = chiave, [pos[0], pos[1]]   # si servirà direttamente dal file
            else:
                dest = self._salva_copertina(chiave, dati)
                if dest:
                    r["cop"], r["cf"] = chiave, dest
        if not r["cop"]:   # cover.jpg / folder.jpg nella cartella dell'album
            cart = os.path.dirname(p)
            if cart not in cop_cart:
                cop_cart[cart] = _copertina_cartella(cart)
            if cop_cart[cart]:
                r["cop"], r["cf"] = cop_cart[cart]
        r["m"] = {
            "title": t.get("title") or nm.get("title") or nome,
            "artist": t.get("artist") or nm.get("artist") or artista_cart,
            "album": t.get("album") or album_cart,
            "albumArtist": t.get("albumArtist") or "",
            "track": _intero(t.get("track")) or traccia,
            "disc": _intero(t.get("disc")),
            "year": (t.get("year") or "")[:4],
            "genre": re.sub(r"^\(\d+\)", "", t.get("genre") or "").strip(),
            "duration": round(t.get("duration") or 0, 2),
            "added": int(st.st_mtime * 1000),
            "cover": r["cop"],
        }
        return r

    def _salva_copertina(self, chiave, dati):
        dest = os.path.join(self.dir_copertine, chiave)
        try:
            if not os.path.exists(dest):
                with open(dest + ".tmp", "wb") as f:
                    f.write(dati)
                os.replace(dest + ".tmp", dest)
            return dest
        except OSError:
            return None

    # --- dal disco al catalogo ---

    def _costruisci(self, record, presenti, vecchi, aggiornato, leggi_fonti=True, rinomine=(), migra=()):
        d = _Dati()
        d.aggiornato = int(aggiornato)
        d.presenti = list(presenti)
        self._unisci(d, list(record.values()), set(presenti), list(vecchi.storia) + list(rinomine), migra)
        d.migra_dal, d.migra_aperta = self._migra_dal, self._migra_aperta
        if d.alias_vecchi:   # cambia ogni volta che gli alias cambiano (per esempio un disco che torna): l'app li richiede
            h = hashlib.sha1(json.dumps(sorted(d.alias_vecchi.items()), separators=(",", ":")).encode("utf-8", "surrogateescape"))
            d.segno_vecchi = int(h.hexdigest()[:12], 16) or 1
        cartella, lette, radio = [], set(), None
        if leggi_fonti:
            # una playlist scritta in modo strano non deve mai fermare la lettura dei brani
            try:
                cartella, lette = self._playlist_cartelle(d)
            except Exception as e:
                _log("playlist del disco non lette:", _errore_breve(e))
                cartella, lette = [], set()
            try:
                radio = self._playlist_radio(d)
            except Exception as e:
                _log("playlist della radio non lette:", _errore_breve(e))
                radio = None
        # una fonte che ora non si legge (disco staccato, file a metà) tiene le playlist dell'ultima volta
        for p in vecchi.playlist:
            if (p.get("fonte") == "cartella" and p.get("_da") in self.playlist_cartelle and p.get("_da") not in lette
                    and not any(q["id"] == p["id"] for q in cartella)):
                cartella.append(dict(p))
        if radio is None:
            radio = [dict(p) for p in vecchi.playlist if p.get("fonte") == "radio"]
        cartella.sort(key=lambda p: (p["nome"].casefold(), p["nome"]))
        for p in cartella + radio:
            # come in Apple Music, un brano messo due volte nella playlist resta due volte; ma due file diversi
            # uniti come doppioni (o un file spostato) restano una volta sola, come nelle playlist dell'iPhone
            brani, primo = [], {}
            for x in p["brani"]:
                fin = d.alias.get(x, x)
                if fin not in d.percorsi or primo.setdefault(fin, x) != x:
                    continue
                brani.append(fin)
            if brani:   # brani non trovati scartati, playlist vuote omesse
                p["brani"] = brani
                d.playlist.append(p)
        d.json = self._json_pubblico(d)
        return d

    def _unisci(self, d, record, presenti, storia=(), migra=()):
        """Livello 1: stesso percorso relativo = una voce (vince l'ordine delle cartelle).
        Livello 2: percorsi diversi ma stesso artista e titolo e durata entro ±2 s (e non due album diversi: lo stesso brano
        in due album, come in Musica, resta due volte) = una voce, l'altra diventa alias.
        storia: le coppie unite nelle letture passate e i file spostati (vedi _alias_storici e _rinomine).
        migra: le coppie [id vecchio, id nuovo] dall'indice della versione precedente del server."""
        prio = {c: i for i, c in enumerate(self.cartelle)}
        reali = {c: _reale(c) or c for c in self.cartelle}
        gruppi = {}
        for r in record:
            gruppi.setdefault(r["rel"], []).append(r)
        voci = []
        for rel, lista in gruppi.items():
            lista.sort(key=lambda r: (prio.get(r["rad"], len(prio)), r["p"]))
            voci.append({"id": id_brano(rel), "rel": rel, "rec": lista, "propri": lista, "prio": prio.get(lista[0]["rad"], len(prio)),
                         "presente": any(r["rad"] in presenti for r in lista), "n": len(lista)})
        voci.sort(key=lambda v: (v["prio"], -v["n"], v["rel"]))
        tenute, simili, per_disco = [], {}, {}
        for v in voci:
            m = v["rec"][0]["m"]
            dur = m.get("duration") or 0
            a, t, al = _confronto(m.get("artist")), _confronto(m.get("title")), _confronto(m.get("album"))
            if a and t and dur > 0:   # senza durata non si unisce niente (versioni live, remix…)
                k = a + "\x00" + t
                gemella = next((c for c in simili.get(k, ()) if abs(c["dur"] - dur) <= 2 and not (al and c["al"] and al != c["al"])), None)
                if gemella:
                    d.alias[v["id"]] = gemella["id"]
                    gemella["rec"] = gemella["rec"] + v["rec"]
                    gemella["presente"] = gemella["presente"] or v["presente"]
                    continue
                v["dur"], v["al"] = dur, al
                simili.setdefault(k, []).append(v)
            tenute.append(v)
        album_cartella = {}
        for v in sorted(tenute, key=lambda v: v["rel"]):
            rec = v["rec"]
            voce = dict(rec[0]["m"])
            voce["id"] = v["id"]
            if not voce.get("cover"):
                voce["cover"] = next((r.get("cop") for r in rec if r.get("cop")), None)
            if not v["presente"]:
                voce["assente"] = True
            if any(r.get("comp") for r in rec):
                voce["compilation"] = True   # solo quando è vero: il catalogo di 10 000 brani resta leggero
            elif voce.get("album") and not voce.get("albumArtist") and "/" in v["rel"]:
                # un album in una sua cartella, senza «artista album»: se gli artisti sono diversi è una compilation
                # (non nella cartella principale, dove stanno insieme brani di album diversi, come «Da YouTube»)
                album_cartella.setdefault((v["rel"].rsplit("/", 1)[0], voce["album"].casefold()), []).append(voce)
            d.brani.append(voce)
            d.percorsi[v["id"]] = [r["p"] for r in rec]
            for r in rec:
                k = r.get("cop")
                if k:
                    # pochi file per ogni disco (non pochi in tutto): se un disco manca, la copertina arriva dall'altro
                    n = per_disco.get((k, r["rad"]), 0)
                    if n < 3:
                        per_disco[(k, r["rad"])] = n + 1
                        d.copertine.setdefault(k, []).append((r["cf"], None) if r.get("cf") else (r["p"], r.get("cpos")))
        for g in album_cartella.values():
            artisti = {_parole(x.get("artist")) for x in g} - {"  "}
            if len(artisti) > 1 and not any(all(a in b for b in artisti) for a in artisti):   # «X» e «X feat. Y» no
                for x in g:
                    x["compilation"] = True
        for v in voci:   # in ordine di priorità: a parità di nome vince il disco principale
            # l'id del file (non quello tenuto): le playlist sanno così se due voci erano davvero lo stesso file
            d.cerca_rel.setdefault(v["rel"], v["id"])
            d.cerca_nome.setdefault(v["rel"].rsplit("/", 1)[-1].casefold(), v["id"])
            for r in v["propri"]:   # solo i file di questa voce (in "rec" ci sono anche quelli dei doppioni uniti a lei)
                d.cerca_abs.setdefault(_nfc(os.path.join(reali.get(r["rad"], r["rad"]), r["rel"])), v["id"])
                d.cerca_abs.setdefault(_nfc(r["p"]), v["id"])
        self._alias_storici(d, storia, migra)

    @staticmethod
    def _alias_storici(d, storia, migra=()):
        """Doppioni uniti in passato e file spostati. Se la copia tenuta sparisce (per esempio tolta dal disco principale)
        e resta un suo doppione, il vecchio id diventa un alias del doppione: così l'app non perde preferiti, ascolti e
        playlist. Le coppie si ricordano (anche nell'indice) finché almeno uno dei brani è nel catalogo.
        Gli id della versione precedente (migra) sono uno per brano: vanno in d.alias_vecchi, fuori dal catalogo
        (che altrimenti peserebbe la metà in più per sempre); l'app li chiede una volta, con api/musica/alias."""
        coppie = {tuple(sorted(c)) for c in storia if c[0] != c[1]}
        coppie.update(tuple(sorted(c)) for c in d.alias.items())
        mig = [(c[0], c[1]) for c in migra if c[0] != c[1]]
        normali = {x for c in coppie for x in c}
        vecchi = {a for a, b in mig if a not in normali}
        vicini = {}
        for a, b in list(coppie) + mig:
            vicini.setdefault(a, set()).add(b)
            vicini.setdefault(b, set()).add(a)
        noti = set(d.percorsi) | set(d.alias)
        for x in sorted(vicini):
            if x in noti:
                continue
            visti, coda, trovato = {x}, [x], None
            while coda and not trovato:   # in ampiezza: il doppione più vicino ancora nel catalogo
                y = coda.pop(0)
                for z in sorted(vicini[y]):
                    if z in noti:
                        trovato = z
                        break
                    if z not in visti:
                        visti.add(z)
                        coda.append(z)
            if trovato:
                (d.alias_vecchi if x in vecchi else d.alias)[x] = d.alias.get(trovato, trovato)
        d.storia = sorted([a, b] for a, b in coppie if a in d.alias or a in d.percorsi)
        # tutte: un brano che ora non c'è (disco staccato) ritrova il suo vecchio id quando torna
        d.migra = [list(c) for c in sorted(set(mig))]

    @staticmethod
    def _altrui(p, persona):
        """Playlist della radio fatta da un'altra persona (campo "di" diverso dal login Tailscale di chi chiede)."""
        return p.get("fonte") == "radio" and bool(p.get("_di")) and p["_di"].casefold() != persona.casefold()

    def _json_pubblico(self, d, persona=None):
        pub = {"brani": d.brani,
               "playlist": [{k: v for k, v in p.items() if not k.startswith("_")} for p in d.playlist
                            if not (persona and self._altrui(p, persona))],
               "collegamenti": self.collegamenti,
               "alias": d.alias,
               "aggiornato": int(d.aggiornato)}
        if d.alias_vecchi:   # ci sono gli alias della versione precedente: l'app li chiede (api/musica/alias) se le servono
            pub["alias_vecchi"] = d.segno_vecchi
        if self.indirizzo:
            pub["indirizzo"] = self.indirizzo
        # ensure_ascii: un carattere rovinato (emoji tagliato a metà in playlist.json, nome di file non UTF-8) non può
        # far fallire la lettura; JSON.parse nell'app legge gli \uXXXX
        return json.dumps(pub, separators=(",", ":")).encode("ascii")

    # --- playlist ---

    def _dopo_radice(self, t):
        """Il percorso relativo dopo il nome della cartella dei brani ("…/Da YouTube/x.mp3" -> "x.mp3")."""
        s = "/" + t
        for b in self._basi:
            k = s.find("/" + b + "/")
            if k >= 0:
                return s[k + len(b) + 2:]
        return None

    def _risolvi(self, d, testi, nome_file=None):
        testi = [_nfc(t) for t in testi if t]
        for t in testi:   # 1. il percorso vero (link risolto)
            x = d.cerca_abs.get(t)
            if x:
                return x
        for t in testi:   # 2. il percorso relativo dopo "Da YouTube/"
            rel = self._dopo_radice(t)
            if rel:
                x = d.cerca_rel.get(rel)
                if x:
                    return x
        if nome_file:     # 3. il nome del file, senza il "001 - " della playlist
            n = _nfc(nome_file.replace("\\", "/").rsplit("/", 1)[-1])
            for c in (re.sub(r"^\s*\d+\s*-\s*", "", n), n):
                x = d.cerca_nome.get(c.casefold())
                if x:
                    return x
        return None

    def _playlist_cartelle(self, d):
        """Ogni sottocartella diretta = una playlist; dentro, link numerati "001 - …mp3" ai brani."""
        trovate, lette, nomi = [], set(), set()
        for base in self.playlist_cartelle:
            try:
                sotto = sorted(os.listdir(base))
            except OSError:
                continue
            lette.add(base)
            for nome_dir in sotto:
                cart = os.path.join(base, nome_dir)
                nome = _nfc(nome_dir)
                if nome.startswith(".") or nome in nomi or not os.path.isdir(cart):
                    continue
                try:
                    voci = [v for v in os.listdir(cart) if not v.startswith(".") and os.path.splitext(v)[1].lower() in ESTENSIONI]
                except OSError:
                    continue
                nomi.add(nome)
                brani = []
                for v in sorted(voci, key=_ordine_numerato):
                    q = os.path.join(cart, v)
                    testi = []
                    if os.path.islink(q):
                        testi.append(_reale(q))   # None se il link sparisce proprio adesso: _risolvi lo salta
                        try:
                            t = os.readlink(q)
                            testi.append(t if os.path.isabs(t) else os.path.join(cart, t))
                        except OSError:
                            pass
                    x = self._risolvi(d, testi, v)
                    if x:
                        brani.append(x)
                trovate.append({"id": "c:" + _impronta(nome)[:12], "nome": nome,
                                "fonte": "cartella", "brani": brani, "_da": base})
        return trovate, lette

    def _playlist_radio(self, d):
        """playlist.json della vecchia pagina radio: brani in base64url (senza =) del percorso relativo a "Musica Cloud"."""
        if not self.playlist_json:
            return []
        try:
            with open(self.playlist_json, encoding="utf-8") as f:
                dati = json.load(f)
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            return None   # illeggibile proprio adesso: restano quelle dell'ultima volta
        lista = dati.get("playlist", []) if isinstance(dati, dict) else None
        if not isinstance(lista, list):
            return None   # forma inattesa (il file lo scrive la radio): restano quelle dell'ultima volta
        out, usati = [], set()
        for p in lista:
            if not isinstance(p, dict) or not isinstance(p.get("brani"), list):
                continue
            nome = _nfc(str(p.get("nome") or "Playlist"))
            pid = p.get("id")
            pid = "r:" + (str(pid) if pid not in (None, "") else _impronta(nome)[:12])
            if pid in usati:
                continue
            usati.add(pid)
            brani = []
            for b in p["brani"]:
                t = _da_base64(b)
                x = self._risolvi(d, [t], t) if t else None
                if x:
                    brani.append(x)
            di = p.get("di")
            out.append({"id": pid, "nome": nome, "fonte": "radio", "brani": brani,
                        "_di": di.strip() if isinstance(di, str) else ""})   # chi l'ha fatta (login Tailscale)
        return out

    # --- per le risposte ---

    def _json_prima(self, d):
        """Catalogo per l'app della versione precedente (quella di main, che chiede il catalogo senza «?v=2»): ogni brano
        con l'id (o gli id) di allora, se ne aveva uno. Quell'app non conosce gli alias: con gli id nuovi crederebbe spariti
        preferiti, playlist e coda, e toglierebbe per sempre dalla coda dei download i brani chiesti. Così resta com'era
        finché il telefono non apre l'app nuova, che chiede il catalogo con gli id nuovi e sposta tutto (api/musica/alias).
        I file e le copertine si trovano anche con gli id di allora (percorso_brano). Se c'era l'indice della versione
        precedente, solo gli id del suo catalogo: quelli ricalcolati dai percorsi (link della cartella di casa, NFD) quella
        app non li ha, e sarebbero brani doppi."""
        prima, note = {}, self._ids_prima
        for v, n in d.alias_vecchi.items():
            if not note or v in note:
                prima.setdefault(n, []).append(v)
        brani = []
        for b in d.brani:
            for v in sorted(prima.get(b["id"], ())) or [b["id"]]:
                brani.append(b if v == b["id"] else dict(b, id=v))
        pub = {"brani": brani, "aggiornato": int(d.aggiornato)}
        if self.indirizzo:
            pub["indirizzo"] = self.indirizzo
        return json.dumps(pub, separators=(",", ":")).encode("ascii")

    def catalogo_json(self, persona=None, prima=False):
        """Il catalogo. persona: login Tailscale di chi chiede; le playlist della radio fatte da altri non le vede.
        prima: lo chiede l'app della versione precedente (vedi _json_prima)."""
        d = self.dati
        js = d.json
        if prima and d.alias_vecchi:
            js = d.json_prima
            if js is None:
                js = d.json_prima = self._json_prima(d)
        elif persona and self.radio_per_persona and any(self._altrui(p, persona) for p in d.playlist):
            k = persona.casefold()
            js = d.json_per.get(k)
            if js is None:
                js = d.json_per[k] = self._json_pubblico(d, persona)
        return js[:-1] + b',"scansione":' + json.dumps(self.scansione).encode("utf-8") + b"}"

    def catalogo(self, persona=None):
        return json.loads(self.catalogo_json(persona).decode("utf-8"))

    def stato(self):
        d = self.dati
        return {"scansione": dict(self.scansione), "aggiornato": int(d.aggiornato), "brani": len(d.brani)}

    def alias_vecchi(self):
        """Id della versione precedente del server -> id di adesso (uno per brano: non stanno nel catalogo)."""
        return dict(self.dati.alias_vecchi)

    def percorso_brano(self, id_):
        """Il primo file esistente del brano (disco principale, poi backup, poi i doppioni)."""
        d = self.dati
        id_ = d.alias.get(id_) or d.alias_vecchi.get(id_) or id_   # anche l'id della versione precedente (app non ancora aggiornata)
        for p in d.percorsi.get(id_, ()):
            if os.path.isfile(p) and not os.path.islink(p):
                return p
        return None

    def copertina(self, chiave):
        """(byte, tipo) della copertina, oppure None. I byte si controllano con l'impronta della chiave.
        Una copertina grande (oltre MINI_SOGLIA) si dà ridotta a MINI_LATO px, se sips c'è (sul Mac sì):
        così ogni riga della libreria sull'iPhone non scarica 1 MB."""
        if not re.fullmatch(r"[0-9a-f]{20}\.(?:jpg|jpeg|png)", chiave or ""):
            return None
        mini = os.path.join(self.dir_copertine, chiave[:20] + ".mini.jpg")
        try:
            with open(mini, "rb") as f:   # ridotta una volta per sempre (il nome viene dall'impronta dell'originale)
                return f.read(), "image/jpeg"
        except OSError:
            pass
        c = self._copertina_vera(chiave)
        if c and len(c[0]) > MINI_SOGLIA:
            m = self._riduci(chiave, c[0], mini)
            if m:
                return m, "image/jpeg"
        return c

    def _riduci(self, chiave, dati, dest):
        """Copertina ridotta con sips (JPEG, MINI_LATO px), salvata in dest. None se non si può: niente sips
        (non è un Mac), immagine che sips non capisce, risultato non più piccolo."""
        if chiave in self._mini_no or not os.access(SIPS, os.X_OK):
            return None
        t = "%s.%d" % (dest, threading.get_ident())
        dentro, fuori = t + os.path.splitext(chiave)[1], t + ".jpg"
        try:
            with _RIDUZIONI:
                with open(dentro, "wb") as f:
                    f.write(dati)
                subprocess.run([SIPS, "-s", "format", "jpeg", "-s", "formatOptions", "80", "-Z", str(MINI_LATO), dentro, "--out", fuori],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=True)
                with open(fuori, "rb") as f:
                    m = f.read()
            if m[:2] != b"\xff\xd8" or len(m) >= len(dati):
                self._mini_no.add(chiave)
                return None
            os.replace(fuori, dest)
            return m
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            _log("copertina %s non ridotta: %r" % (chiave, e))
            self._mini_no.add(chiave)
            return None
        finally:
            for x in (dentro, fuori):
                try:
                    os.remove(x)
                except OSError:
                    pass

    def _copertina_vera(self, chiave):
        cand = list(self.dati.copertine.get(chiave, ()))
        cand.append((os.path.join(self.dir_copertine, chiave), None))   # salvate da una versione precedente
        for percorso, pos in cand:
            try:
                with open(percorso, "rb") as f:
                    if pos:
                        f.seek(pos[0])
                        dati = f.read(pos[1])
                    else:
                        dati = f.read(20 * 2 ** 20)
            except OSError:
                continue
            if dati and hashlib.sha1(dati).hexdigest()[:20] == chiave[:20]:
                return dati, "image/png" if chiave.endswith(".png") else "image/jpeg"
        return None


# ---------- risposte HTTP ----------

LIB = None
API_COSE = ("catalogo", "stato", "aggiorna", "file", "copertina", "alias")
PREFISSO = re.compile(r"^(?:.*/)?api/musica/(catalogo|stato|file|copertina|aggiorna|alias)(?:/([^/]+))?$")
TIPI_APP = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
            ".webmanifest": "application/manifest+json", ".json": "application/json; charset=utf-8",
            ".css": "text/css; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".svg": "image/svg+xml", ".ico": "image/x-icon", ".webp": "image/webp", ".txt": "text/plain; charset=utf-8"}
# pagine, script, fogli di stile e manifest (dell'app e dei moduli in x/): il browser chiede sempre al Mac se sono cambiati
# (304 se no). Senza, con un index.html vecchio di settimane terrebbe la sua copia per giorni e un aggiornamento non arriverebbe
SEMPRE_FRESCHI = (".html", ".js", ".css", ".webmanifest")


def _rispondi(h, stato, corpo=b"", tipo="text/plain; charset=utf-8", intestazioni=None):
    h._musica_partita = True   # da qui in poi non si può più mandare un'altra risposta (vedi _al_riparo)
    h.send_response(stato)
    h.send_header("Content-Type", tipo)
    h.send_header("Content-Length", str(len(corpo)))
    h.send_header("X-Content-Type-Options", "nosniff")
    for k, v in (intestazioni or {}).items():
        h.send_header(k, v)
    h.end_headers()
    if h.command != "HEAD" and corpo:
        h.wfile.write(corpo)


def _json(h, dati, stato=200, gz=False):
    corpo = dati if isinstance(dati, bytes) else json.dumps(dati).encode("ascii")
    extra = {"Cache-Control": "no-store"}
    if gz and len(corpo) > 2048 and "gzip" in (h.headers.get("Accept-Encoding") or ""):
        corpo = gzip.compress(corpo, 5)
        extra["Content-Encoding"] = "gzip"
        extra["Vary"] = "Accept-Encoding"
    _rispondi(h, stato, corpo, "application/json; charset=utf-8", extra)


def _invia_file(h, percorso, tipo, intestazioni=None):
    """Un file (brano, video, capitolo di un audiolibro) con Range; `intestazioni` in più (Content-Disposition…)."""
    try:
        dim = os.path.getsize(percorso)
        f = open(percorso, "rb")
    except OSError:
        _json(h, {"errore": "Brano non trovato: il disco è collegato?"}, 404)
        return
    with f:
        inizio, fine, parziale = 0, dim - 1, False
        m = re.match(r"bytes=(\d*)-(\d*)$", (h.headers.get("Range") or "").strip())
        if m and dim and (m.group(1) or m.group(2)):
            if m.group(1):
                inizio = int(m.group(1))
                if m.group(2):
                    fine = min(int(m.group(2)), dim - 1)
            else:
                inizio = max(0, dim - int(m.group(2)))
            if inizio > fine or inizio >= dim:
                _rispondi(h, 416, b"", intestazioni={"Content-Range": "bytes */%d" % dim})
                return
            parziale = True
        h._musica_partita = True
        h.send_response(206 if parziale else 200)
        h.send_header("Content-Type", tipo)
        h.send_header("Accept-Ranges", "bytes")
        h.send_header("Content-Length", str(fine - inizio + 1))
        if parziale:
            h.send_header("Content-Range", "bytes %d-%d/%d" % (inizio, fine, dim))
        h.send_header("Cache-Control", "private, max-age=86400")
        for k, v in dict({"X-Content-Type-Options": "nosniff"}, **(intestazioni or {})).items():
            h.send_header(k, v)
        h.end_headers()
        if h.command == "HEAD":
            return
        resto = fine - inizio + 1
        try:
            f.seek(inizio)
            while resto > 0:
                try:
                    blocco = f.read(min(256 * 1024, resto))
                except OSError as e:   # disco staccato mentre il brano suona (EIO, ENXIO)
                    _log("invio di %r interrotto: %r" % (os.path.basename(percorso), e))
                    break
                if not blocco:
                    break
                h.wfile.write(blocco)
                resto -= len(blocco)
        except OSError:   # il telefono ha chiuso la connessione
            resto = -1
        if resto:
            # risposta incompleta: le intestazioni sono già partite, non si può scrivere altro (nemmeno un errore).
            # Si chiude la connessione, così il telefono non resta ad aspettare i byte che mancano.
            h.close_connection = True


def _api(h, cosa, arg):
    lib = LIB
    if lib is None:
        _json(h, {"errore": "La musica non è ancora pronta"}, 503)
    elif cosa == "catalogo":   # con Tailscale, chi chiede è nell'intestazione (la stessa che Benessere usa in persona())
        # l'app di adesso chiede «catalogo?v=2»; senza, è l'app della versione precedente (main) e riceve gli id di allora
        prima = "2" not in parse_qs(urlsplit(h.path).query).get("v", [])
        _json(h, lib.catalogo_json((h.headers.get("Tailscale-User-Login") or "").strip() or None, prima), gz=True)
    elif cosa == "stato":
        _json(h, lib.stato())
    elif cosa == "alias":   # id della versione precedente -> id di adesso: l'app li chiede una volta, se le servono
        _json(h, {"alias": lib.alias_vecchi()}, gz=True)
    elif cosa == "aggiorna":   # non blocca: la lettura gira in sottofondo, l'app chiede "stato"
        avviata = lib.avvia_scansione()
        _json(h, {"avviata": avviata, "scansione": dict(lib.scansione)})
    elif cosa == "file":
        p = lib.percorso_brano(arg or "")
        if not p:
            _json(h, {"errore": "Brano non trovato: il disco è collegato?"}, 404)
        else:
            _invia_file(h, p, TIPI.get(os.path.splitext(p)[1].lower(), "application/octet-stream"))
    elif cosa == "copertina":
        c = lib.copertina(arg or "")
        if not c:
            _json(h, {"errore": "Copertina non trovata"}, 404)
        else:
            _rispondi(h, 200, c[0], c[1], {"Cache-Control": "private, max-age=2592000"})
    else:
        _json(h, {"errore": "Richiesta sconosciuta"}, 404)


def _al_riparo(h, percorso, fai):
    """Esegue fai() per una richiesta della musica: un errore non deve mai far cadere il server che la ospita, e l'app
    riceve comunque una risposta (500 in JSON) se non ne era già partita una."""
    h._musica_partita = False
    try:
        fai()
    except (BrokenPipeError, ConnectionResetError):
        h.close_connection = True
    except Exception as e:
        _log("errore con %s: %s" % (percorso, _errore_breve(e)))
        if getattr(h, "_musica_partita", False):
            h.close_connection = True   # la risposta era già cominciata: niente secondo messaggio dentro il primo
        else:
            try:
                _json(h, {"errore": "Errore del server della musica"}, 500)
            except Exception:
                h.close_connection = True


def gestisci(h):
    """Vecchio aggancio, per compatibilità: da chiamare in do_GET / do_HEAD. True se ha risposto.
    Risponde a qualunque indirizzo che finisce con api/musica/<richiesta>."""
    percorso = unquote(urlsplit(h.path).path)
    m = PREFISSO.match(percorso)
    if not m or LIB is None:
        return False
    _al_riparo(h, percorso, lambda: _api(h, m.group(1), m.group(2)))
    return True


def _non_trovato(h):
    _rispondi(h, 404, "Non trovato".encode("utf-8"), intestazioni={"Cache-Control": "no-store"})


def _statico(h, resto, app):
    parti = (resto or "index.html").split("/")
    if parti[0] == "server" or any(x in ("", ".", "..") or x.startswith(".") or "\\" in x or "\x00" in x for x in parti):
        return _non_trovato(h)
    base = os.path.realpath(app)
    p = os.path.realpath(os.path.join(base, *parti))
    tipo = TIPI_APP.get(os.path.splitext(p)[1].lower())
    if not tipo or not p.startswith(base + os.sep) or not os.path.isfile(p):   # niente ../ né link che escono
        return _non_trovato(h)
    try:
        st = os.stat(p)
        with open(p, "rb") as f:
            dati = f.read()
    except OSError:
        return _non_trovato(h)
    nome = os.path.basename(p)
    extra = {"ETag": '"%x-%x"' % (int(st.st_mtime * 1000), st.st_size),
             "Cache-Control": "no-cache" if nome.lower().endswith(SEMPRE_FRESCHI) else "public, max-age=86400"}
    if tipo.startswith("text/html"):
        extra["Content-Security-Policy"] = "frame-ancestors 'self'"
        if parti[0] != "x":   # i moduli (x/) usano servizi esterni che vogliono sapere da dove arriva la richiesta (mappe OSM)
            extra["Referrer-Policy"] = "same-origin"
    if h.headers.get("If-None-Match") == extra["ETag"]:
        h._musica_partita = True
        h.send_response(304)
        for k in ("ETag", "Cache-Control"):
            h.send_header(k, extra[k])
        h.end_headers()
        return
    _rispondi(h, 200, dati, tipo, extra)


def servi_app(h, percorso, prefisso="/musica", app=None):
    """Incolla l'app Musica dentro un server esistente. Da chiamare nel gestore delle GET (e HEAD),
    con percorso = unquote(urlsplit(self.path).path). Restituisce True se ha risposto.

      prefisso                 -> redirect 301 relativo a "musica/" (sotto /benessere/ diventa /benessere/musica/)
      prefisso/api/musica/...  -> catalogo, stato, aggiorna, file/<id>, copertina/<chiave>, alias
      prefisso/<file>          -> i file della cartella `app` (index.html per "/"), solo quelli che ci sono davvero
    Con prefisso="" l'app sta alla radice del sito."""
    prefisso = (prefisso or "").rstrip("/")
    if prefisso and percorso == prefisso:
        def fai():
            q = urlsplit(h.path).query
            dest = prefisso.rsplit("/", 1)[-1] + "/" + ("?" + q if q else "")
            _rispondi(h, 301, b"", intestazioni={"Location": dest, "Cache-Control": "no-cache"})
    elif not percorso.startswith(prefisso + "/"):
        return False
    else:
        resto = percorso[len(prefisso) + 1:]

        def fai():
            if resto.startswith("api/musica/"):
                parti = resto[len("api/musica/"):].split("/")
                if len(parti) > 2 or parti[0] not in API_COSE:
                    _json(h, {"errore": "Richiesta sconosciuta"}, 404)
                else:
                    _api(h, parti[0], parti[1] if len(parti) > 1 else None)
            else:
                _statico(h, resto, app or APP)
    _al_riparo(h, percorso, fai)
    return True


def avvia(cartelle, cache, intervallo=600, playlist_cartelle=None, playlist_json=None, collegamenti=None,
          radio_per_persona=False, indirizzo=""):
    """Prepara la libreria: carica subito l'indice salvato (avvio istantaneo) e la tiene aggiornata
    in sottofondo, con una lettura ogni `intervallo` secondi e subito quando un disco viene collegato o staccato.

      cartelle           cartelle dei brani, in ordine di priorità (prima il disco principale, poi il backup)
      cache              dove tenere indice e copertine, per esempio "~/.atlante-musica"
      playlist_cartelle  cartelle con una sottocartella per playlist (dentro, link "001 - …mp3" ai brani)
      playlist_json      playlist.json della vecchia pagina radio
      collegamenti       [{"nome": "Benessere", "url": "/benessere/"}]: pagine in più nella scheda Altro dell'app
      radio_per_persona  True: ognuno vede solo le playlist della radio che ha fatto lui (campo "di" = login Tailscale
                         nell'intestazione Tailscale-User-Login); False: tutte a tutti (main(): --radio-per-persona)
      indirizzo          indirizzo https fisso dell'app (solo per main(): l'app lo propone a chi la apre in http)
    """
    global LIB
    LIB = Libreria(cartelle, cache, playlist_cartelle, playlist_json, collegamenti, radio_per_persona, indirizzo)
    threading.Thread(target=LIB.ciclo, args=(intervallo,), daemon=True, name="musica-ciclo").start()
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


def _dentro(p, radice):
    return p.startswith(radice.rstrip(os.sep) + os.sep)


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
    h._musica_partita = True
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
    h._musica_partita = True
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
    indirizzo = LIB.indirizzo if LIB is not None else ""   # l'indirizzo https fisso dell'app (--indirizzo)
    if indirizzo:
        ammessi.add(urlsplit(indirizzo).netloc.lower())
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
    h._musica_partita = False
    try:
        dati = _leggi_corpo(h)
        if metodo in ("POST", "DELETE", "PUT", "PATCH") and not _stessa_origine(h):
            raise ErroreExtra(403, "Richiesta da un altro sito: rifiutata")
        corpo = _da_json(dati) if metodo == "POST" else {}
        _instrada(h, metodo, [p for p in m.group(1).split("/") if p], corpo)
    except ErroreExtra as e:
        if not getattr(h, "_musica_partita", False):
            try:
                _json(h, {"errore": e.testo}, e.codice)
            except OSError:
                h.close_connection = True
    except (BrokenPipeError, ConnectionResetError):
        h.close_connection = True
    except Exception as e:   # noqa: BLE001  l'app riceve comunque una risposta
        print("Atlante: errore su %s %s: %s" % (metodo, h.path, e), flush=True)
        if not getattr(h, "_musica_partita", False):
            try:
                _json(h, {"errore": "Errore sul Mac: %s" % e}, 500)
            except OSError:
                pass
        h.close_connection = True
    return True


def main():
    ap = argparse.ArgumentParser(description="Serve la musica di una o più cartelle all'app Atlante Musica "
                                 "(e le pagine e le API dei moduli della scheda Altro).")
    ap.add_argument("cartelle", nargs="+", help="cartelle con la musica, in ordine di priorità (prima il disco principale)")
    ap.add_argument("--porta", type=int, default=8790)
    ap.add_argument("--indirizzo", default="", help="indirizzo https fisso dell'app, es. https://mac-mini.tailnet.ts.net/ "
                    "(con `tailscale serve --bg 8790`): chi apre l'app in http da un altro dispositivo viene mandato lì")
    ap.add_argument("--cert", help="certificato HTTPS (es. da `tailscale cert`): il server risponde direttamente in https")
    ap.add_argument("--chiave", help="chiave privata del certificato")
    ap.add_argument("--cache", default=CONF["cache"], help="dove tenere indice, copertine e lavori dei moduli")
    ap.add_argument("--app", default=APP, help="cartella dell'app (index.html e x/)")
    ap.add_argument("--playlist", action="append", default=[], metavar="CARTELLA",
                    help="cartella con una sottocartella per playlist (si può ripetere)")
    ap.add_argument("--playlist-json", metavar="FILE", help="playlist.json della vecchia pagina radio")
    ap.add_argument("--radio-per-persona", action="store_true", help="delle playlist di playlist.json ognuno vede solo "
                    "le sue (campo \"di\" = login Tailscale di chi apre l'app); senza: tutti vedono tutte")
    ap.add_argument("--collegamento", action="append", default=[], metavar="NOME=URL",
                    help="pagina in più nella scheda Altro dell'app, per esempio \"Benessere del Mac=/benessere/\" (si può ripetere)")
    ap.add_argument("--intervallo", type=int, default=600, help="secondi tra una lettura del disco e l'altra")
    ap.add_argument("--video-dir", default=CONF["video_dir"], help="dove salvare video e audio scaricati con yt-dlp")
    ap.add_argument("--libri-dir", default=CONF["libri_dir"], help="dove salvare gli audiolibri creati con la voce del Mac")
    ap.add_argument("--ia-modello", default="", help="modello di Ollama per l'IA locale, es. llama3.2 "
                    "(predefinito: il primo installato)")
    ap.add_argument("--ia-indirizzo", default=CONF["ia_indirizzo"], help="indirizzo di Ollama sul Mac")
    a = ap.parse_args()
    coll = []
    for c in a.collegamento:
        nome, _, url = c.partition("=")
        if not nome.strip() or not url.strip():
            ap.error("--collegamento vuole NOME=URL, per esempio \"Benessere del Mac=/benessere/\"")
        coll.append({"nome": nome.strip(), "url": url.strip()})
    indirizzo = a.indirizzo.strip()
    if indirizzo and not indirizzo.endswith("/"):
        indirizzo += "/"
    avvia(a.cartelle, a.cache, a.intervallo, playlist_cartelle=a.playlist, playlist_json=a.playlist_json,
          collegamenti=coll, radio_per_persona=a.radio_per_persona, indirizzo=indirizzo)
    avvia_extra(cache=a.cache, video_dir=a.video_dir, libri_dir=a.libri_dir, ia_modello=a.ia_modello.strip(),
                ia_indirizzo=a.ia_indirizzo.strip())
    app = os.path.abspath(os.path.expanduser(a.app))
    https = bool(a.cert)
    host_sicuro = urlsplit(indirizzo).netloc.lower()

    class H(BaseHTTPRequestHandler):
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

        def _gestisci(self):
            # le API dei moduli prima di tutto (rispondono anche da un indirizzo non sicuro, come le API della musica)
            if gestisci_extra(self) or gestisci(self) or self._vai_al_sicuro():
                return
            if not servi_app(self, unquote(urlsplit(self.path).path), prefisso="", app=app):
                _non_trovato(self)

        do_GET = _gestisci
        do_HEAD = _gestisci

        def _scrive(self):   # POST e DELETE: solo le API dei moduli (scaricamenti, audiolibri, IA)
            if not gestisci_extra(self):
                self.close_connection = True   # corpo non letto
                self.send_error(501, "Unsupported method (%r)" % self.command)

        do_POST = _scrive
        do_DELETE = _scrive

        def log_message(self, *x):
            pass

    # ATLANTE_ASCOLTO=127.0.0.1 dietro Tailscale: il server non chiede codici, dalla rete di casa nessuno deve arrivarci
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
