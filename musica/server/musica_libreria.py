#!/usr/bin/env python3
"""Atlante Musica: la musica dei dischi del Mac, servita all'app.

Legge una o più cartelle di musica (per esempio il disco principale e il suo backup),
estrae titolo, artista, album, durata e copertina dai tag MP3 (ID3), M4A/ALAC (MP4) e FLAC,
unisce i doppioni e li offre all'app Musica con queste richieste:

    GET api/musica/catalogo            brani, playlist e collegamenti (JSON)
    GET api/musica/stato               solo lo stato della lettura del disco (leggero)
    GET api/musica/aggiorna            avvia subito una nuova lettura, in sottofondo
    GET api/musica/file/<id>           il brano (con Range: si può saltare avanti)
    GET api/musica/copertina/<chiave>  la copertina (JPEG o PNG; quelle grandi ridotte una volta con sips)

Solo libreria standard di Python (va bene anche il Python 3.9 di macOS). Sul Mac, per ridurre le copertine
grandi (le PNG che yt-dlp mette negli mp3), usa /usr/bin/sips, che c'è di serie; altrove le serve come sono.

Da solo:

    python3 musica_libreria.py "/Volumes/Disco/Musica" "/Volumes/Backup/Musica" --porta 8790

poi apri http://<mac>:8790/ (anche dal telefono, con Tailscale).

Dentro un server esistente (vedi AGGANCIO-BENESSERE.md):

    import musica_libreria as atlante_musica
    atlante_musica.avvia([cartelle...], "~/.atlante-musica", playlist_cartelle=[...], playlist_json=..., collegamenti=[...])
    # e nel gestore delle GET, con percorso = unquote(urlsplit(self.path).path):
    if atlante_musica.servi_app(self, percorso, prefisso="/musica", app="/cartella/dell/app"):
        return

L'id di un brano è lo sha1 del suo percorso relativo alla cartella (in forma NFC), primi
16 caratteri: lo stesso file sul disco principale e sul backup ha lo stesso id, così se un
disco manca il brano resta raggiungibile dall'altro e i brani salvati sull'iPhone e le
playlist non si rompono.
"""
import argparse
import base64
import gzip
import hashlib
import json
import os
import re
import struct
import subprocess
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

ESTENSIONI = {".mp3", ".m4a", ".m4b", ".aac", ".flac", ".wav", ".aif", ".aiff", ".ogg", ".oga", ".opus", ".alac", ".mp4"}
TIPI = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".m4b": "audio/mp4", ".mp4": "audio/mp4", ".alac": "audio/mp4",
        ".aac": "audio/aac", ".flac": "audio/flac", ".wav": "audio/wav", ".aif": "audio/aiff", ".aiff": "audio/aiff",
        ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg"}
VERSIONE_INDICE = 2
ERRORE_LETTURA = "Non sono riuscito a rileggere il disco: riprova tra qualche minuto"
APP =os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
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
    """Tag ID3v2. Oltre ai testi dà la copertina ("pic") e, se i suoi byte stanno così come
    sono nel file, dove si trovano ("pic_pos": posizione, lunghezza): si potrà servirla
    direttamente dal file senza copiarla."""
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
            "TYER": "year", "TYE": "year", "TDRC": "year", "TCON": "genre", "TCO": "genre", "TLEN": "tlen", "TLE": "tlen"}
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
                pos = (10 + inizio + i, len(fr) - i) if diretto and i < len(fr) else None
        p += hs + sz
    o["pic"] = pic
    o["pic_pos"] = pos
    tl = o.pop("tlen", "").strip()
    if re.fullmatch(r"\d{1,9}", tl) and 0 < int(tl) < 86400000:
        o["duration"] = int(tl) / 1000.0
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
                    "TRACKNUMBER": "track", "DISCNUMBER": "disc", "DATE": "year", "GENRE": "genre"}
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


# ---------- durata degli MP3 ----------

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
        b = f.read(8192)
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


def leggi_tag(percorso):
    """Tag del file: titolo, artista, …, durata, copertina. {} se il file non si legge."""
    o = {}
    try:
        with open(percorso, "rb") as f:
            for lettore in (tag_id3, tag_mp4, tag_flac):
                f.seek(0)
                try:
                    r = lettore(f)
                except (ValueError, struct.error, IndexError):
                    r = None
                if r is not None:
                    o = r
                    break
            if os.path.splitext(percorso)[1].lower() in (".mp3", ".mp2", ".mpga") and not o.get("duration"):
                d = durata_mp3(f, _fine_id3(f))
                if d > 0:
                    o["duration"] = d
    except (OSError, ValueError, struct.error, IndexError):
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
    return hashlib.sha1(_nfc(rel).encode("utf-8")).hexdigest()[:16]


def _confronto(s):
    """Artista o titolo ridotti all'osso, per riconoscere i doppioni: minuscole, senza accenti né punteggiatura."""
    s = unicodedata.normalize("NFKD", s or "").casefold()
    return " ".join(re.findall(r"\w+", "".join(c for c in s if not unicodedata.combining(c))))


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
        self.alias = {}        # id di un doppione (o di una copia tenuta in passato e ora sparita) -> id della voce tenuta
        self.storia = []       # coppie [id, id] unite come doppioni, anche in letture passate (vedi _unisci)
        self.copertine = {}    # chiave -> [(percorso, (posizione, lunghezza) o None)]
        self.playlist = []     # [{"id", "nome", "fonte", "brani", "_da"}]
        self.aggiornato = 0
        self.presenti = []     # cartelle lette davvero all'ultima scansione
        self.json = b"{}"      # catalogo già pronto, senza "scansione"
        self.json_per = {}     # login Tailscale -> catalogo senza le playlist della radio di altre persone
        self.cerca_abs = {}    # percorso assoluto (NFC) -> id
        self.cerca_rel = {}    # percorso relativo (NFC) -> id
        self.cerca_nome = {}   # nome del file (NFC, minuscolo) -> id


class Libreria:
    def __init__(self, cartelle, cache, playlist_cartelle=None, playlist_json=None, collegamenti=None, radio_per_persona=False):
        self.radio_per_persona = radio_per_persona   # True: ognuno vede solo le playlist della radio che ha fatto lui (campo "di")
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
        self._carica_indice()

    # --- indice salvato: l'avvio è istantaneo, la scansione vera gira dopo, in sottofondo ---

    def _carica_indice(self):
        try:
            with open(self.file_indice, encoding="utf-8") as f:
                salvato = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(salvato, dict) or salvato.get("versione") != VERSIONE_INDICE:
            return
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
        d = self._costruisci(record, presenti, vecchi, salvato.get("aggiornato") or 0, leggi_fonti=False)
        with self._blocco:
            self.record, self.dati = record, d
        self._firma_dati = self._firma(record, d)
        _log("indice caricato: %d brani, %d playlist" % (len(d.brani), len(d.playlist)))

    @staticmethod
    def _firma(record, d):
        """Quello che finisce nell'indice, tranne l'ora: file (percorso, dimensione e data), dischi letti, playlist, doppioni."""
        return ({p: (r.get("f"), r.get("rad")) for p, r in record.items()}, list(d.presenti), d.playlist, d.storia)

    def _salva_indice(self, record, d):
        """Scrive indice.json (prima in un .tmp, poi lo sostituisce). True se è andata bene."""
        dati = {"versione": VERSIONE_INDICE, "aggiornato": d.aggiornato, "presenti": d.presenti,
                "record": list(record.values()), "playlist": d.playlist, "storia": d.storia}
        tmp = self.file_indice + ".tmp"
        try:
            testo = json.dumps(dati, ensure_ascii=False, separators=(",", ":"))   # in un colpo: molto più veloce di json.dump
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(testo)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.file_indice)
            return True
        except (OSError, ValueError) as e:
            _log("non riesco a salvare l'indice:", e)
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
            _log("lettura non riuscita:", repr(e))
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
        for rad in self.cartelle:
            trovati, ok = {}, os.path.isdir(rad)
            if ok:
                try:
                    for p, st in _cammina(rad):
                        rel = _nfc(os.path.relpath(p, rad).replace(os.sep, "/"))
                        firma = "%d:%d" % (st.st_size, int(st.st_mtime))
                        r = vecchi.get(p)
                        if not r or r.get("f") != firma or r.get("rad") != rad or r.get("rel") != rel:
                            g = gemelli.get(rel)
                            if g and g["f"].split(":")[0] == str(st.st_size):
                                # copia dello stesso file su un altro disco: non serve rileggerla
                                r = dict(g, p=p, rad=rad, f=firma)
                                r["m"] = dict(g["m"], added=int(st.st_mtime * 1000))
                            else:
                                r = self._record(p, rad, rel, st, firma, cop_cart)
                        trovati[p] = r
                        gemelli.setdefault(rel, r)
                        visti.add(rel)
                        self.scansione = {"in_corso": True, "letti": len(visti)}
                except OSError as e:
                    _log("lettura di %s interrotta: %s" % (rad, e))
                    ok = False
                if ok and not os.path.isdir(rad):   # disco staccato a metà lettura
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
        d = self._costruisci(nuovi, presenti, self.dati, int(time.time() * 1000))
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

    def _record(self, p, rad, rel, st, firma, cop_cart):
        t = leggi_tag(p)
        nm = da_nome(rel.rsplit("/", 1)[-1])
        r = {"p": p, "rad": rad, "rel": rel, "f": firma, "cop": None}
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
            "title": t.get("title") or nm.get("title") or rel.rsplit("/", 1)[-1],
            "artist": t.get("artist") or nm.get("artist") or "",
            "album": t.get("album") or "",
            "albumArtist": t.get("albumArtist") or "",
            "track": _intero(t.get("track")),
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

    def _costruisci(self, record, presenti, vecchi, aggiornato, leggi_fonti=True):
        d = _Dati()
        d.aggiornato = int(aggiornato)
        d.presenti = list(presenti)
        self._unisci(d, list(record.values()), set(presenti), vecchi.storia)
        cartella, lette, radio = [], set(), None
        if leggi_fonti:
            # una playlist scritta in modo strano non deve mai fermare la lettura dei brani
            try:
                cartella, lette = self._playlist_cartelle(d)
            except Exception as e:
                _log("playlist del disco non lette:", repr(e))
                cartella, lette = [], set()
            try:
                radio = self._playlist_radio(d)
            except Exception as e:
                _log("playlist della radio non lette:", repr(e))
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
            # come in Apple Music, un brano messo due volte nella playlist resta due volte
            brani = [x for x in (d.alias.get(x, x) for x in p["brani"]) if x in d.percorsi]
            if brani:   # brani non trovati scartati, playlist vuote omesse
                p["brani"] = brani
                d.playlist.append(p)
        d.json = self._json_pubblico(d)
        return d

    def _unisci(self, d, record, presenti, storia=()):
        """Livello 1: stesso percorso relativo = una voce (vince l'ordine delle cartelle).
        Livello 2: percorsi diversi ma stesso artista e titolo e durata entro ±2 s = una voce, l'altra diventa alias.
        storia: le coppie unite nelle letture passate (vedi _alias_storici)."""
        prio = {c: i for i, c in enumerate(self.cartelle)}
        reali = {c: _reale(c) or c for c in self.cartelle}
        gruppi = {}
        for r in record:
            gruppi.setdefault(r["rel"], []).append(r)
        voci = []
        for rel, lista in gruppi.items():
            lista.sort(key=lambda r: (prio.get(r["rad"], len(prio)), r["p"]))
            voci.append({"id": id_brano(rel), "rel": rel, "rec": lista, "prio": prio.get(lista[0]["rad"], len(prio)),
                         "presente": any(r["rad"] in presenti for r in lista), "n": len(lista)})
        voci.sort(key=lambda v: (v["prio"], -v["n"], v["rel"]))
        tenute, simili, per_disco = [], {}, {}
        for v in voci:
            m = v["rec"][0]["m"]
            dur = m.get("duration") or 0
            a, t = _confronto(m.get("artist")), _confronto(m.get("title"))
            if a and t and dur > 0:   # senza durata non si unisce niente (versioni live, remix…)
                k = a + "\x00" + t
                gemella = next((c for c in simili.get(k, ()) if abs(c["dur"] - dur) <= 2), None)
                if gemella:
                    d.alias[v["id"]] = gemella["id"]
                    gemella["rec"] = gemella["rec"] + v["rec"]
                    gemella["presente"] = gemella["presente"] or v["presente"]
                    continue
                v["dur"] = dur
                simili.setdefault(k, []).append(v)
            tenute.append(v)
        for v in sorted(tenute, key=lambda v: v["rel"]):
            rec = v["rec"]
            voce = dict(rec[0]["m"])
            voce["id"] = v["id"]
            if not voce.get("cover"):
                voce["cover"] = next((r.get("cop") for r in rec if r.get("cop")), None)
            if not v["presente"]:
                voce["assente"] = True
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
        for v in voci:   # in ordine di priorità: a parità di nome vince il disco principale
            fin = d.alias.get(v["id"], v["id"])
            d.cerca_rel.setdefault(v["rel"], fin)
            d.cerca_nome.setdefault(v["rel"].rsplit("/", 1)[-1].casefold(), fin)
            for r in v["rec"]:
                d.cerca_abs.setdefault(_nfc(os.path.join(reali.get(r["rad"], r["rad"]), r["rel"])), fin)
                d.cerca_abs.setdefault(_nfc(r["p"]), fin)
        self._alias_storici(d, storia)

    @staticmethod
    def _alias_storici(d, storia):
        """Doppioni uniti in passato. Se la copia tenuta sparisce (per esempio tolta dal disco principale) e resta
        un suo doppione, il vecchio id diventa un alias del doppione: così l'app non perde preferiti, ascolti e
        playlist. Le coppie si ricordano (anche nell'indice) finché almeno uno dei brani è nel catalogo."""
        coppie = {tuple(sorted(c)) for c in storia if c[0] != c[1]}
        coppie.update(tuple(sorted(c)) for c in d.alias.items())
        vicini = {}
        for a, b in coppie:
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
                d.alias[x] = d.alias.get(trovato, trovato)
        d.storia = sorted([a, b] for a, b in coppie if a in d.alias or a in d.percorsi)

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
        return json.dumps(pub, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

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
                trovate.append({"id": "c:" + hashlib.sha1(nome.encode("utf-8")).hexdigest()[:12], "nome": nome,
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
            pid = "r:" + (str(pid) if pid not in (None, "") else hashlib.sha1(nome.encode("utf-8")).hexdigest()[:12])
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

    def catalogo_json(self, persona=None):
        """Il catalogo. persona: login Tailscale di chi chiede; le playlist della radio fatte da altri non le vede."""
        d = self.dati
        js = d.json
        if persona and self.radio_per_persona and any(self._altrui(p, persona) for p in d.playlist):
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

    def percorso_brano(self, id_):
        """Il primo file esistente del brano (disco principale, poi backup, poi i doppioni)."""
        d = self.dati
        id_ = d.alias.get(id_, id_)
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
API_COSE = ("catalogo", "stato", "aggiorna", "file", "copertina")
PREFISSO = re.compile(r"^(?:.*/)?api/musica/(catalogo|stato|file|copertina|aggiorna)(?:/([^/]+))?$")
TIPI_APP = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
            ".webmanifest": "application/manifest+json", ".json": "application/json; charset=utf-8",
            ".css": "text/css; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".svg": "image/svg+xml", ".ico": "image/x-icon", ".webp": "image/webp", ".txt": "text/plain; charset=utf-8"}
SEMPRE_FRESCHI = ("index.html", "sw.js", "manifest.webmanifest")


def _rispondi(h, stato, corpo=b"", tipo="text/plain; charset=utf-8", intestazioni=None):
    h._musica_partita = True   # da qui in poi non si può più mandare un'altra risposta (vedi servi_app)
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
    corpo = dati if isinstance(dati, bytes) else json.dumps(dati, ensure_ascii=False).encode("utf-8")
    extra = {"Cache-Control": "no-store"}
    if gz and len(corpo) > 2048 and "gzip" in (h.headers.get("Accept-Encoding") or ""):
        corpo = gzip.compress(corpo, 5)
        extra["Content-Encoding"] = "gzip"
        extra["Vary"] = "Accept-Encoding"
    _rispondi(h, stato, corpo, "application/json; charset=utf-8", extra)


def _invia_file(h, percorso, tipo):
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
        h.send_header("X-Content-Type-Options", "nosniff")
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
        _json(h, lib.catalogo_json((h.headers.get("Tailscale-User-Login") or "").strip() or None), gz=True)
    elif cosa == "stato":
        _json(h, lib.stato())
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


def gestisci(h):
    """Vecchio aggancio, per compatibilità: da chiamare in do_GET / do_HEAD. True se ha risposto.
    Risponde a qualunque indirizzo che finisce con api/musica/<richiesta>."""
    m = PREFISSO.match(unquote(urlsplit(h.path).path))
    if not m or LIB is None:
        return False
    try:
        _api(h, m.group(1), m.group(2))
    except (BrokenPipeError, ConnectionResetError):
        pass
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
             "Cache-Control": "no-cache" if nome in SEMPRE_FRESCHI or nome.endswith(".html") else "public, max-age=86400"}
    if tipo.startswith("text/html"):
        extra["Content-Security-Policy"] = "frame-ancestors 'self'"
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
      prefisso/api/musica/...  -> catalogo, stato, aggiorna, file/<id>, copertina/<chiave>
      prefisso/<file>          -> i file della cartella `app` (index.html per "/"), solo quelli che ci sono davvero
    Con prefisso="" l'app sta alla radice del sito."""
    prefisso = (prefisso or "").rstrip("/")
    h._musica_partita = False
    try:
        if prefisso and percorso == prefisso:
            q = urlsplit(h.path).query
            dest = prefisso.rsplit("/", 1)[-1] + "/" + ("?" + q if q else "")
            _rispondi(h, 301, b"", intestazioni={"Location": dest, "Cache-Control": "no-cache"})
            return True
        if not percorso.startswith(prefisso + "/"):
            return False
        resto = percorso[len(prefisso) + 1:]
        if resto.startswith("api/musica/"):
            parti = resto[len("api/musica/"):].split("/")
            if len(parti) > 2 or parti[0] not in API_COSE:
                _json(h, {"errore": "Richiesta sconosciuta"}, 404)
            else:
                _api(h, parti[0], parti[1] if len(parti) > 1 else None)
        else:
            _statico(h, resto, app or APP)
    except (BrokenPipeError, ConnectionResetError):
        pass
    except Exception as e:   # un errore della musica non deve mai far cadere il server che la ospita
        _log("errore con %s: %r" % (percorso, e))
        if getattr(h, "_musica_partita", False):
            h.close_connection = True   # la risposta era già cominciata: niente secondo messaggio dentro il primo
        else:
            try:
                _json(h, {"errore": "Errore del server della musica"}, 500)
            except Exception:
                pass
    return True


def avvia(cartelle, cache, intervallo=600, playlist_cartelle=None, playlist_json=None, collegamenti=None, radio_per_persona=False):
    """Prepara la libreria: carica subito l'indice salvato (avvio istantaneo) e la tiene aggiornata
    in sottofondo, con una lettura ogni `intervallo` secondi e subito quando un disco viene collegato o staccato.

      cartelle           cartelle dei brani, in ordine di priorità (prima il disco principale, poi il backup)
      cache              dove tenere indice e copertine, per esempio "~/.atlante-musica"
      playlist_cartelle  cartelle con una sottocartella per playlist (dentro, link "001 - …mp3" ai brani)
      playlist_json      playlist.json della vecchia pagina radio
      collegamenti       [{"nome": "Benessere", "url": "../"}]: schede in più nell'app
      radio_per_persona  True: ognuno vede solo le playlist della radio che ha fatto lui (campo "di" = login Tailscale
                         nell'intestazione Tailscale-User-Login); False: tutte a tutti, come prima (vedi AGGANCIO, punto 5e)
    """
    global LIB
    LIB = Libreria(cartelle, cache, playlist_cartelle, playlist_json, collegamenti, radio_per_persona)
    threading.Thread(target=LIB.ciclo, args=(intervallo,), daemon=True, name="musica-ciclo").start()
    return LIB


def main():
    ap = argparse.ArgumentParser(description="Serve la musica di una o più cartelle all'app Atlante Musica.")
    ap.add_argument("cartelle", nargs="+", help="cartelle con la musica, in ordine di priorità (prima il disco principale)")
    ap.add_argument("--porta", type=int, default=8790)
    ap.add_argument("--indirizzo", default="0.0.0.0")
    ap.add_argument("--cache", default="~/.atlante-musica", help="dove tenere indice e copertine")
    ap.add_argument("--app", default=APP, help="cartella dell'app (index.html)")
    ap.add_argument("--playlist", action="append", default=[], metavar="CARTELLA",
                    help="cartella con una sottocartella per playlist (si può ripetere)")
    ap.add_argument("--playlist-json", metavar="FILE", help="playlist.json della vecchia pagina radio")
    ap.add_argument("--collegamento", action="append", default=[], metavar="NOME=URL",
                    help="scheda in più nell'app, per esempio Benessere=../ (si può ripetere)")
    ap.add_argument("--intervallo", type=int, default=600, help="secondi tra una lettura del disco e l'altra")
    a = ap.parse_args()
    coll = []
    for c in a.collegamento:
        nome, _, url = c.partition("=")
        if not nome.strip() or not url.strip():
            ap.error("--collegamento vuole NOME=URL, per esempio Benessere=../")
        coll.append({"nome": nome.strip(), "url": url.strip()})
    avvia(a.cartelle, a.cache, a.intervallo, playlist_cartelle=a.playlist, playlist_json=a.playlist_json, collegamenti=coll)
    app = os.path.abspath(os.path.expanduser(a.app))

    class H(BaseHTTPRequestHandler):
        def _gestisci(self):
            percorso = unquote(urlsplit(self.path).path)
            if not servi_app(self, percorso, prefisso="", app=app):
                _non_trovato(self)

        do_GET = _gestisci
        do_HEAD = _gestisci

        def log_message(self, *x):
            pass

    print("Atlante Musica su http://%s:%d/  (Ctrl+C per fermare)" % (a.indirizzo, a.porta), flush=True)
    ThreadingHTTPServer((a.indirizzo, a.porta), H).serve_forever()


if __name__ == "__main__":
    main()
