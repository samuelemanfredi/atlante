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

poi apri http://<mac>:8790/ (anche dal telefono, con Tailscale).
Per unirlo a un server esistente basta chiamare `gestisci(handler)` dentro do_GET:
restituisce True se la richiesta era per la musica.
"""
import argparse
import hashlib
import json
import mimetypes
import os
import re
import struct
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
            "TYER": "year", "TYE": "year", "TDRC": "year", "TCON": "genre", "TCO": "genre"}
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


def leggi_tag(percorso):
    try:
        with open(percorso, "rb") as f:
            for lettore in (tag_id3, tag_mp4, tag_flac):
                f.seek(0)
                o = lettore(f)
                if o is not None:
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

class Libreria:
    def __init__(self, cartelle, cache):
        self.cartelle = [os.path.abspath(os.path.expanduser(c)) for c in cartelle]
        self.cache = os.path.abspath(os.path.expanduser(cache))
        self.dir_copertine = os.path.join(self.cache, "copertine")
        os.makedirs(self.dir_copertine, exist_ok=True)
        self.file_indice = os.path.join(self.cache, "indice.json")
        self.blocco = threading.Lock()
        self.brani = {}       # id -> voce del catalogo
        self.percorsi = {}    # id -> percorso sul disco
        self.ultima = 0
        try:
            with open(self.file_indice, encoding="utf-8") as f:
                salvato = json.load(f)
            self.vecchie = {v["_p"]: v for v in salvato.get("brani", [])}
        except (OSError, ValueError):
            self.vecchie = {}

    def scansiona(self):
        """Rilegge le cartelle. I file già visti (stesso percorso, dimensione, data) non vengono riletti."""
        nuovi, percorsi = {}, {}
        for radice in self.cartelle:
            if not os.path.isdir(radice):
                continue
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
                    firma = "%d:%d" % (st.st_size, int(st.st_mtime))
                    v = self.vecchie.get(p)
                    if not v or v.get("_f") != firma:
                        v = self._voce(p, st, firma)
                    nuovi[v["id"]] = v
                    percorsi[v["id"]] = p
        with self.blocco:
            self.brani, self.percorsi = nuovi, percorsi
            self.vecchie = {v["_p"]: v for v in nuovi.values()}
            self.ultima = time.time()
        tmp = self.file_indice + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"brani": list(nuovi.values())}, f, ensure_ascii=False)
        os.replace(tmp, self.file_indice)
        return len(nuovi)

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
        }

    def catalogo(self):
        with self.blocco:
            pubblici = [{k: v for k, v in b.items() if not k.startswith("_")} for b in self.brani.values()]
        return {"brani": pubblici, "aggiornato": int(self.ultima * 1000)}


# ---------- risposte HTTP ----------

LIB = None
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
            h.send_response(416)
            h.send_header("Content-Range", "bytes */%d" % dim)
            h.end_headers()
            return
        parziale = True
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
    with open(percorso, "rb") as f:
        f.seek(inizio)
        resto = fine - inizio + 1
        try:
            while resto > 0:
                blocco = f.read(min(256 * 1024, resto))
                if not blocco:
                    break
                h.wfile.write(blocco)
                resto -= len(blocco)
        except (BrokenPipeError, ConnectionResetError):
            pass


def _json(h, dati, stato=200):
    corpo = json.dumps(dati, ensure_ascii=False).encode("utf-8")
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
    if cosa == "catalogo":
        _json(h, LIB.catalogo())
    elif cosa == "aggiorna":
        _json(h, {"brani": LIB.scansiona()})
    elif cosa == "file":
        p = LIB.percorsi.get(arg or "")
        if not p or not os.path.isfile(p):
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
    return True


def avvia(cartelle, cache, intervallo=600):
    """Prepara la libreria e la tiene aggiornata in sottofondo (nuovi brani, disco ricollegato)."""
    global LIB
    LIB = Libreria(cartelle, cache)

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
    a = ap.parse_args()
    avvia(a.cartelle, a.cache)
    app = os.path.abspath(a.app)

    class H(SimpleHTTPRequestHandler):
        def __init__(self, *x, **k):
            super().__init__(*x, directory=app, **k)

        def do_GET(self):
            if not gestisci(self):
                super().do_GET()

        def do_HEAD(self):
            if not gestisci(self):
                super().do_HEAD()

        def log_message(self, *x):
            pass

    print("Atlante Musica su http://0.0.0.0:%d/  (Ctrl+C per fermare)" % a.porta, flush=True)
    ThreadingHTTPServer(("0.0.0.0", a.porta), H).serve_forever()


if __name__ == "__main__":
    main()
