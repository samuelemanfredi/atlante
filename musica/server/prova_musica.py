#!/usr/bin/env python3
"""Prova di musica_libreria.py, senza toccare i dischi veri.

    python3 prova_musica.py

Crea in una cartella temporanea due finti dischi (principale e backup) con MP3 sintetici
(tag ID3 con titolo, artista e copertina, frame MPEG veri per la durata), link simbolici di
playlist e un playlist.json della radio. Poi controlla: doppioni (livello 1 e 2), id stabili,
disco principale scollegato, durate, playlist, e l'aggancio servi_app dentro un finto server
Benessere (redirect, file dell'app, percorsi vietati, API, Range).
Alla fine scrive «TUTTO OK» (codice 0) oppure l'elenco degli errori (codice 1).
Solo libreria standard: va bene il Python 3.9 di macOS.
"""
import base64
import gzip
import hashlib
import http.client
import json
import os
import shutil
import struct
import sys
import tempfile
import threading
import time
import unicodedata
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

QUI = os.path.dirname(os.path.abspath(__file__))
if QUI not in sys.path:
    sys.path.insert(0, QUI)
import musica_libreria as ml  # noqa: E402

# ---------- finti MP3 ----------

KBPS, FREQ, CAMPIONI = 32, 44100, 1152
LUN = 144 * KBPS * 1000 // FREQ   # 104 byte per frame (MPEG-1 Layer III, mono, 32 kbps, 44,1 kHz)


def png(r, g, b, lato=8):
    """Una piccola immagine PNG di un colore solo."""
    def blocco(tipo, dati):
        return struct.pack(">I", len(dati)) + tipo + dati + struct.pack(">I", zlib.crc32(tipo + dati) & 0xFFFFFFFF)
    riga = b"\x00" + bytes([r, g, b]) * lato
    return (b"\x89PNG\r\n\x1a\n" + blocco(b"IHDR", struct.pack(">IIBBBBB", lato, lato, 8, 2, 0, 0, 0))
            + blocco(b"IDAT", zlib.compress(riga * lato)) + blocco(b"IEND", b""))


def _syncsafe(n):
    return bytes([(n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127])


def id3(titolo=None, artista=None, copertina=None, tlen_ms=None):
    def testo(fid, s):
        dati = b"\x01" + s.encode("utf-16")   # UTF-16 con BOM
        return fid.encode("ascii") + struct.pack(">I", len(dati)) + b"\x00\x00" + dati
    fr = b""
    if titolo:
        fr += testo("TIT2", titolo)
    if artista:
        fr += testo("TPE1", artista)
    if tlen_ms:
        fr += testo("TLEN", str(tlen_ms))
    if copertina:
        dati = b"\x00image/png\x00\x03\x00" + copertina
        fr += b"APIC" + struct.pack(">I", len(dati)) + b"\x00\x00" + dati
    fr += b"\x00" * 64   # spazio libero, come lasciano i programmi veri
    return b"ID3\x03\x00\x00" + _syncsafe(len(fr)) + fr


def frame(xing=None, vbri=None):
    f = bytearray(LUN)
    f[0:4] = b"\xff\xfb\x10\xc4"
    if xing is not None:
        f[21:33] = b"Xing" + struct.pack(">II", 1, xing)          # dopo 4 byte di intestazione + 17 di side info
    if vbri is not None:
        f[36:54] = b"VBRI" + struct.pack(">HHH", 1, 0, 50) + struct.pack(">II", vbri * LUN, vbri)
    return bytes(f)


def mp3(titolo=None, artista=None, copertina=None, secondi=None, xing=None, vbri=None, tlen_ms=None, senza_audio=False):
    """Un MP3 sintetico. secondi: audio CBR vero (si stima dalla dimensione); xing/vbri: durata scritta
    nell'intestazione del primo frame; tlen_ms: durata nel tag TLEN."""
    corpo = id3(titolo, artista, copertina, tlen_ms)
    if senza_audio:
        return corpo + b"qui non c'e' audio. " * 20
    if xing:
        return corpo + frame(xing=round(xing * FREQ / CAMPIONI)) + frame() * 3
    if vbri:
        return corpo + frame(vbri=round(vbri * FREQ / CAMPIONI)) + frame() * 3
    n = max(3, round((secondi or 1) * KBPS * 1000 / (LUN * 8)))
    return corpo + frame() * n


def scrivi(percorso, dati):
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    with open(percorso, "wb") as f:
        f.write(dati)


def b64(s):
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")


# ---------- i finti dischi ----------

ROSSO, VERDE, BLU, GIALLO = png(220, 30, 40), png(30, 200, 60), png(30, 60, 220), png(240, 220, 20)
CAFFE_NFC = unicodedata.normalize("NFC", "Caffè.mp3")
CAFFE_NFD = unicodedata.normalize("NFD", "Caffè.mp3")


def crea_dischi(base):
    """Disco principale "Cloud", backup "Cippo2", una casa con il link ~/Musica Cloud, playlist e playlist.json."""
    princ = os.path.join(base, "Cloud", "Musica Cloud", "Da YouTube")
    back = os.path.join(base, "Cippo2", "Backup Cloud", "Musica Cloud", "Da YouTube")
    casa = os.path.join(base, "casa")
    os.makedirs(casa)
    os.symlink(os.path.join(base, "Cloud", "Musica Cloud"), os.path.join(casa, "Musica Cloud"))   # ~/Musica Cloud
    yt_casa = os.path.join(casa, "Musica Cloud", "Da YouTube")

    uno = mp3("Song One", "Artist A", ROSSO, xing=180)
    caffe = mp3("Caffè", "Artista E", GIALLO, secondi=20)
    nested = mp3("Nested", "Artist F", secondi=10)
    senza = mp3("Senza durata", "Artist G", senza_audio=True)
    p = {
        "Artist A - Song One.mp3": uno,
        "Song Two.mp3": mp3("Song Two", "Band B", VERDE, secondi=30),
        "Artist C - Live Song.mp3": mp3("Live Song", "Artist C", BLU, xing=240),
        "Artist C - Live Song [live].mp3": mp3("Live Song", "Artist C", BLU, xing=260),
        "Tlen.mp3": mp3("Con TLEN", "Artist D", tlen_ms=123456, secondi=1),
        "Vbri.mp3": mp3("Con VBRI", "Artist D", vbri=52),
        CAFFE_NFC: caffe,
        "Sub/Nested.mp3": nested,
        "Senza durata.mp3": senza,
        "Solo principale.mp3": mp3("Solo principale", "Artist H", secondi=15),
        ".atlante-tmp/Temp.mp3": mp3("Temporaneo", "Rsync", secondi=5),
        "._Artist A - Song One.mp3": b"AppleDouble",
        "vuoto.mp3": b"",
        "note.txt": b"appunti",
    }
    for rel, dati in p.items():
        scrivi(os.path.join(princ, rel), dati)
    os.symlink(os.path.join(princ, "Artist A - Song One.mp3"), os.path.join(princ, "link.mp3"))
    os.symlink(os.path.join(princ, "Sub"), os.path.join(princ, "CartellaLink"))

    b = {
        "Artist A - Song One.mp3": uno,                                   # stesso file, stesso percorso: livello 1
        "Band B - Song Two.mp3": mp3("Song Two", "Band B", VERDE, secondi=31),   # altro nome, stessi tag: livello 2
        CAFFE_NFD: caffe,                                                 # stesso nome ma scritto in NFD
        "Sub/Nested.mp3": nested,
        "Only Backup.mp3": mp3("Only Backup", "Artist I", secondi=12),
        "Artist G - Senza durata.mp3": senza,                             # senza durata: non si unisce
        ".atlante-tmp/partial.mp3": mp3("A metà", "Rsync", secondi=5),
    }
    for rel, dati in b.items():
        scrivi(os.path.join(back, rel), dati)

    pl = os.path.join(base, "Cloud", "Musica Cloud", "Playlist")
    sara = os.path.join(pl, "Sara❤️")
    os.makedirs(sara)
    os.symlink(os.path.join(yt_casa, "Artist A - Song One.mp3"), os.path.join(sara, "001 - Artist A - Song One.mp3"))
    os.symlink(os.path.join(back, "Band B - Song Two.mp3"), os.path.join(sara, "002 - Band B - Song Two.mp3"))
    os.symlink(os.path.join(yt_casa, "Only Backup.mp3"), os.path.join(sara, "003 - Only Backup.mp3"))
    scrivi(os.path.join(sara, "004 - Artist C - Live Song.mp3"), b"copia, non link")
    os.symlink(os.path.join(yt_casa, "Sconosciuto.mp3"), os.path.join(sara, "005 - Sconosciuto.mp3"))
    scrivi(os.path.join(sara, "leggimi.txt"), b"note")
    bella = os.path.join(pl, "Bella musica  1")
    os.makedirs(bella)
    os.symlink(os.path.join(yt_casa, "Tlen.mp3"), os.path.join(bella, "001 - Tlen.mp3"))
    os.symlink(os.path.join(yt_casa, CAFFE_NFD), os.path.join(bella, "002 - " + CAFFE_NFD))
    vuota = os.path.join(pl, "Vuota")
    os.makedirs(vuota)
    os.symlink(os.path.join(yt_casa, "Niente.mp3"), os.path.join(vuota, "001 - Niente.mp3"))
    os.makedirs(os.path.join(pl, ".nascosta"))

    radio = os.path.join(casa, ".cloud-web", "playlist.json")
    scrivi(radio, json.dumps({"playlist": [
        {"id": "abc", "di": "samuele@example.com", "nome": "Sara❤️",
         "brani": [b64("Da YouTube/Artist A - Song One.mp3"), b64("Da YouTube/Only Backup.mp3"),
                   b64("Da YouTube/Band B - Song Two.mp3"), b64("Da YouTube/Non esiste.mp3")]},
        {"id": "vuota", "di": "samuele@example.com", "nome": "Vuota", "brani": [b64("Da YouTube/Non esiste.mp3")]},
        {"id": "sara", "di": "Sara@example.com", "nome": "Di Sara", "brani": [b64("Da YouTube/Tlen.mp3")]},
    ]}).encode("utf-8"))

    app = os.path.join(base, "app")
    scrivi(os.path.join(app, "index.html"), b"<!doctype html><title>Musica</title>")
    scrivi(os.path.join(app, "sw.js"), b"self.addEventListener('fetch', () => {});")
    scrivi(os.path.join(app, "manifest.webmanifest"), b'{"name":"Musica"}')
    scrivi(os.path.join(app, "icon-192.png"), ROSSO)
    scrivi(os.path.join(app, "server", "musica_libreria.py"), b"# non si deve vedere")
    scrivi(os.path.join(app, ".nascosto"), b"segreto")
    scrivi(os.path.join(base, "segreto.txt"), b"segreto")
    os.symlink(os.path.join(base, "segreto.txt"), os.path.join(app, "fuori.html"))
    return {"princ": princ, "back": back, "playlist": pl, "radio": radio, "app": app, "casa": casa}


# ---------- finto Benessere: stessa forma del server vero ----------

class Rifiuto(Exception):
    def __init__(self, codice, messaggio=""):
        super().__init__(messaggio)
        self.codice, self.messaggio = codice, messaggio


def finto_benessere(app_musica):
    class Gestore(BaseHTTPRequestHandler):
        def do_GET(self):
            self.gestisci(post=False)

        def do_POST(self):
            self.gestisci(post=True)

        def do_HEAD(self):   # facoltativo, come in AGGANCIO-BENESSERE.md
            percorso = unquote(urlsplit(self.path).path)
            if (percorso == "/musica" or percorso.startswith("/musica/")) and self.headers.get("Tailscale-User-Login"):
                ml.servi_app(self, percorso, app=app_musica)
            else:
                self.send_error(405)

        def log_message(self, *x):
            pass

        def persona(self):
            chi = self.headers.get("Tailscale-User-Login")
            if not chi:
                raise Rifiuto(403, "Solo dalla rete Tailscale")
            return chi

        def invia(self, codice, corpo, tipo="application/json; charset=utf-8", pagina=False):
            self.send_response(codice)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            if pagina:
                self.send_header("Content-Security-Policy", "frame-ancestors 'self' https://esempio:8443")
            self.end_headers()
            self.wfile.write(corpo)

        def gestisci(self, post=False):
            percorso = unquote(urlsplit(self.path).path)
            try:
                # --- Atlante Musica ---
                if not post and (percorso == "/musica" or percorso.startswith("/musica/")):
                    self.persona()
                    ml.servi_app(self, percorso, app=app_musica)
                    return
                # --- fine Atlante Musica ---
                if percorso == "/":
                    self.invia(200, b"<h1>Benessere</h1>", "text/html; charset=utf-8", pagina=True)
                    return
                if not percorso.startswith("/api/"):
                    raise Rifiuto(404, "Non trovato")
                self.persona()
                self.invia(200, b'{"ok":true}')
            except Rifiuto as r:
                self.invia(r.codice, json.dumps({"errore": r.messaggio}).encode("utf-8"))
    return Gestore


def chiedi(porta, percorso, metodo="GET", intestazioni=None, login=True):
    c = http.client.HTTPConnection("127.0.0.1", porta, timeout=15)
    c.putrequest(metodo, percorso, skip_accept_encoding=True)
    h = dict(intestazioni or {})
    if login:
        h.setdefault("Tailscale-User-Login", "samuele@example.com")
    for k, v in h.items():
        c.putheader(k, v)
    c.endheaders()
    r = c.getresponse()
    corpo = r.read()
    c.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, corpo


# ---------- la prova ----------

ERRORI = []


def controlla(cond, cosa):
    print(("  ok      " if cond else "  ERRORE  ") + cosa, flush=True)
    if not cond:
        ERRORI.append(cosa)


def titolo(s):
    print("\n" + s, flush=True)


def prova(base):
    x = crea_dischi(base)
    cache = os.path.join(base, "cache")
    coll = [{"nome": "Benessere", "url": "../"}]
    args = dict(playlist_cartelle=[x["playlist"]], playlist_json=x["radio"], collegamenti=coll, radio_per_persona=True)
    lib = ml.Libreria([x["princ"], x["back"]], cache, **args)
    t0 = time.time()
    n = lib.scansiona()
    print("Lettura dei finti dischi: %d brani in %.2f s" % (n, time.time() - t0))
    cat = lib.catalogo()
    per_rel = {}
    for v in cat["brani"]:
        per_rel[v["id"]] = v
    I = {k: ml.id_brano(k) for k in ("Artist A - Song One.mp3", "Song Two.mp3", "Band B - Song Two.mp3",
                                      "Artist C - Live Song.mp3", "Artist C - Live Song [live].mp3", "Tlen.mp3", "Vbri.mp3",
                                      CAFFE_NFC, "Sub/Nested.mp3", "Senza durata.mp3", "Solo principale.mp3",
                                      "Only Backup.mp3", "Artist G - Senza durata.mp3")}

    titolo("Id stabili e doppioni")
    controlla(I["Artist A - Song One.mp3"] == hashlib.sha1("Artist A - Song One.mp3".encode()).hexdigest()[:16],
              "id = sha1 del percorso relativo, primi 16 caratteri")
    attesi = set(I.values()) - {I["Band B - Song Two.mp3"]}
    controlla(set(per_rel) == attesi, "12 brani attesi nel catalogo (trovati %d)" % len(cat["brani"]))
    controlla(len(cat["brani"]) == 12, "nessun brano in più (link, cartelle col punto, file vuoti, AppleDouble esclusi)")
    p1 = lib.dati.percorsi.get(I["Artist A - Song One.mp3"], [])
    controlla(len(p1) == 2 and p1[0].startswith(x["princ"]) and p1[1].startswith(x["back"]),
              "livello 1: stesso percorso sui due dischi = una voce, prima il disco principale, il backup tenuto da parte")
    controlla(len(lib.dati.percorsi.get(I[CAFFE_NFC], [])) == 2, "livello 1 anche con nomi NFC/NFD diversi (Caffè)")
    controlla(cat["alias"].get(I["Band B - Song Two.mp3"]) == I["Song Two.mp3"],
              "livello 2: «Band B - Song Two» (31 s) è un alias di «Song Two» (30 s)")
    controlla(lib.percorso_brano(I["Band B - Song Two.mp3"]) == os.path.join(x["princ"], "Song Two.mp3"),
              "file/<alias> porta al brano tenuto")
    controlla(I["Artist C - Live Song.mp3"] in per_rel and I["Artist C - Live Song [live].mp3"] in per_rel,
              "durate diverse di 20 s (versione live): non unite")
    controlla(I["Senza durata.mp3"] in per_rel and I["Artist G - Senza durata.mp3"] in per_rel,
              "durata sconosciuta: non unite")
    controlla(not any(v.get("assente") for v in cat["brani"]), "con entrambi i dischi nessun brano è «assente»")

    titolo("Durate")
    def dur(k):
        return per_rel.get(I[k], {}).get("duration", -1)
    controlla(abs(dur("Artist A - Song One.mp3") - 180) < 0.1, "Xing: %.2f s (attesi 180)" % dur("Artist A - Song One.mp3"))
    controlla(abs(dur("Vbri.mp3") - 52) < 0.1, "VBRI: %.2f s (attesi 52)" % dur("Vbri.mp3"))
    controlla(abs(dur("Tlen.mp3") - 123.456) < 0.01, "TLEN: %.3f s (attesi 123,456)" % dur("Tlen.mp3"))
    controlla(abs(dur("Song Two.mp3") - 30) < 0.5, "stima CBR: %.2f s (attesi 30)" % dur("Song Two.mp3"))
    controlla(abs(dur(CAFFE_NFC) - 20) < 0.5, "stima CBR: %.2f s (attesi 20)" % dur(CAFFE_NFC))
    controlla(dur("Senza durata.mp3") == 0, "senza frame MPEG la durata resta 0")
    controlla(per_rel[I["Artist A - Song One.mp3"]]["title"] == "Song One" and per_rel[I["Artist A - Song One.mp3"]]["artist"] == "Artist A",
              "titolo e artista dai tag ID3")
    with open(os.path.join(x["princ"], "Song Two.mp3"), "rb") as f:
        controlla(abs(ml.durata_mp3(f, ml._fine_id3(f)) - 30) < 0.5, "durata_mp3 da sola")

    titolo("Copertine")
    k1 = per_rel[I["Artist A - Song One.mp3"]]["cover"]
    c1 = lib.copertina(k1 or "")
    controlla(bool(c1) and c1[0] == ROSSO and c1[1] == "image/png", "copertina servita direttamente dal file MP3")
    controlla(not os.listdir(os.path.join(cache, "copertine")), "nessuna copia delle copertine nella cache")
    controlla(per_rel[I["Song Two.mp3"]]["cover"] != k1, "copertine diverse per brani diversi")
    controlla(lib.copertina("0" * 20 + ".jpg") is None and lib.copertina("../x") is None, "chiavi di copertina sconosciute rifiutate")

    titolo("Playlist")
    pl = {p["id"]: p for p in cat["playlist"]}
    sara = pl.get("c:" + hashlib.sha1("Sara❤️".encode()).hexdigest()[:12])
    controlla(bool(sara) and sara["nome"] == "Sara❤️" and sara["fonte"] == "cartella", "cartella «Sara❤️» letta")
    controlla(bool(sara) and sara["brani"] == [I["Artist A - Song One.mp3"], I["Song Two.mp3"], I["Only Backup.mp3"], I["Artist C - Live Song.mp3"]],
              "link risolti: realpath, alias, percorso dopo «Da YouTube/», nome senza «004 - »; link rotto scartato")
    bella = pl.get("c:" + hashlib.sha1("Bella musica  1".encode()).hexdigest()[:12])
    controlla(bool(bella) and bella["nome"] == "Bella musica  1" and bella["brani"] == [I["Tlen.mp3"], I[CAFFE_NFC]],
              "«Bella musica  1» (spazi doppi, link con nome NFD)")
    controlla(not any(p["nome"] == "Vuota" for p in cat["playlist"]), "playlist vuote omesse")
    controlla(not any(p["nome"].startswith(".") for p in cat["playlist"]), "cartelle col punto ignorate")
    radio = pl.get("r:abc")
    controlla(bool(radio) and radio["fonte"] == "radio" and radio["brani"] == [I["Artist A - Song One.mp3"], I["Only Backup.mp3"], I["Song Two.mp3"]],
              "playlist.json della radio (base64url senza =) risolta, alias compreso")
    controlla(sum(1 for p in cat["playlist"] if p["nome"] == "Sara❤️") == 2, "stesso nome da fonti diverse: restano entrambe")
    controlla(cat["collegamenti"] == coll, "collegamenti nel catalogo")
    controlla(cat["scansione"] == {"in_corso": False, "letti": 13}, "scansione nel catalogo: %r" % cat["scansione"])

    titolo("Indice salvato e avvio istantaneo")
    controlla(os.path.isfile(os.path.join(cache, "indice.json")) and not os.path.exists(os.path.join(cache, "indice.json.tmp")),
              "indice.json salvato (senza file .tmp lasciati in giro)")
    t0 = time.time()
    lib2 = ml.Libreria([x["princ"], x["back"]], cache, **args)
    cat2 = lib2.catalogo()
    controlla(time.time() - t0 < 2 and len(cat2["brani"]) == 12, "nuova partenza: 12 brani subito, senza rileggere i dischi")
    controlla(cat2["playlist"] == cat["playlist"] and cat2["alias"] == cat["alias"], "playlist e alias tornano dall'indice")
    controlla(lib2.percorso_brano(I["Artist A - Song One.mp3"]) == os.path.join(x["princ"], "Artist A - Song One.mp3"), "file/<id> funziona subito")
    m0 = os.stat(os.path.join(cache, "indice.json")).st_mtime_ns
    time.sleep(0.02)
    lib.scansiona()
    controlla(os.stat(os.path.join(cache, "indice.json")).st_mtime_ns == m0, "lettura senza cambiamenti: indice.json non riscritto")

    titolo("Disco principale scollegato")
    staccato = os.path.join(base, "Cloud-staccato")
    os.rename(os.path.join(base, "Cloud"), staccato)
    lib.scansiona()
    cat3 = lib.catalogo()
    per3 = {v["id"]: v for v in cat3["brani"]}
    controlla(set(per3) == attesi, "stessi 12 brani, con gli stessi id")
    assenti = {k for k, v in I.items() if per3.get(v, {}).get("assente")}
    controlla(assenti == {"Artist C - Live Song.mp3", "Artist C - Live Song [live].mp3", "Tlen.mp3", "Vbri.mp3",
                          "Senza durata.mp3", "Solo principale.mp3"},
              "segnati «assente» solo i brani che stanno solo sul principale: %s" % sorted(assenti))
    controlla(lib.percorso_brano(I["Artist A - Song One.mp3"]) == os.path.join(x["back"], "Artist A - Song One.mp3"),
              "«Song One» ora arriva dal backup, stesso id")
    controlla(lib.percorso_brano(I["Song Two.mp3"]) == os.path.join(x["back"], "Band B - Song Two.mp3"),
              "«Song Two» arriva dal suo doppione sul backup")
    controlla(not per3[I["Song Two.mp3"]].get("assente"), "e quindi non è «assente»")
    controlla(cat3["playlist"] == cat["playlist"], "playlist del disco scollegato tenute com'erano")
    c3 = lib.copertina(k1)
    controlla(bool(c3) and c3[0] == ROSSO, "copertina dal backup")
    os.rename(staccato, os.path.join(base, "Cloud"))
    lib.scansiona()
    cat4 = lib.catalogo()
    controlla(not any(v.get("assente") for v in cat4["brani"]) and len(cat4["brani"]) == 12, "disco ricollegato: tutto come prima")
    controlla(lib.percorso_brano(I["Artist A - Song One.mp3"]).startswith(x["princ"]), "di nuovo dal disco principale")

    titolo("Tutti e due i dischi scollegati")
    os.rename(os.path.join(base, "Cloud"), staccato)
    os.rename(os.path.join(base, "Cippo2"), os.path.join(base, "Cippo2-staccato"))
    lib.scansiona()
    cat5 = lib.catalogo()
    controlla(len(cat5["brani"]) == 12 and all(v.get("assente") for v in cat5["brani"]), "catalogo tenuto com'era, tutto «assente»")
    controlla(cat5["playlist"] == cat["playlist"], "playlist tenute")
    os.rename(staccato, os.path.join(base, "Cloud"))
    os.rename(os.path.join(base, "Cippo2-staccato"), os.path.join(base, "Cippo2"))
    lib.scansiona()
    controlla(not any(v.get("assente") for v in lib.catalogo()["brani"]), "dischi ricollegati")

    titolo("Scansione in sottofondo, una alla volta")
    lib._lucchetto.acquire()
    controlla(lib.avvia_scansione() is False, "se una lettura è già in corso non ne parte un'altra")
    lib._lucchetto.release()
    controlla(lib.avvia_scansione() is True and lib.scansione["in_corso"], "avvia_scansione torna subito, con in_corso = true")
    for _ in range(100):
        if not lib.scansione["in_corso"]:
            break
        time.sleep(0.05)
    controlla(not lib.scansione["in_corso"], "la lettura in sottofondo finisce")

    titolo("Copia tenuta tolta dal principale, brani ripetuti in una playlist, nomi senza tag")
    b2 = os.path.join(base, "secondo")
    p2, k2, pl2 = (os.path.join(b2, n) for n in ("Principale", "Backup", "Playlist"))
    scrivi(os.path.join(p2, "Song Two.mp3"), mp3("Song Two", "Band B", VERDE, secondi=30))
    scrivi(os.path.join(k2, "Band B - Song Two.mp3"), mp3("Song Two", "Band B", VERDE, secondi=31))
    os.makedirs(os.path.join(pl2, "Ripetuta"))
    for i in (1, 2):
        os.symlink(os.path.join(p2, "Song Two.mp3"), os.path.join(pl2, "Ripetuta", "%03d - Song Two.mp3" % i))
    tenuto, doppione = ml.id_brano("Song Two.mp3"), ml.id_brano("Band B - Song Two.mp3")
    lb = ml.Libreria([p2, k2], os.path.join(b2, "cache"), playlist_cartelle=[pl2])
    lb.scansiona()
    c = lb.catalogo()
    controlla(c["alias"] == {doppione: tenuto}, "i due «Song Two» uniti, tenuto quello del principale")
    controlla([p["brani"] for p in c["playlist"]] == [[tenuto, tenuto]], "brano messo due volte nella cartella: resta due volte, come in Apple Music")
    os.remove(os.path.join(p2, "Song Two.mp3"))
    lb.scansiona()
    c = lb.catalogo()
    controlla([v["id"] for v in c["brani"]] == [doppione] and c["alias"].get(tenuto) == doppione,
              "tolto dal principale: il vecchio id diventa un alias del doppione rimasto (l'app non perde preferiti e playlist)")
    controlla(ml.Libreria([p2, k2], os.path.join(b2, "cache")).catalogo()["alias"].get(tenuto) == doppione,
              "e se lo ricorda anche dopo un riavvio (indice)")
    log, ml._log = ml._log, lambda *a: None
    try:
        lb._scansiona = lambda: 1 / 0   # una lettura che fallisce (per esempio il disco si stacca)
        lb.avvia_scansione()
        for _ in range(100):
            if not lb.scansione["in_corso"]:
                break
            time.sleep(0.02)
        controlla(bool(lb.stato()["scansione"].get("errore")), "lettura non riuscita: lo stato lo dice all'app")
    finally:
        ml._log = log
    nomi = {"883 - Hanno ucciso l'Uomo Ragno.mp3": {"artist": "883", "title": "Hanno ucciso l'Uomo Ragno"},
            "50 Cent - In Da Club.mp3": {"artist": "50 Cent", "title": "In Da Club"},
            "3 Doors Down - Kryptonite.mp3": {"artist": "3 Doors Down", "title": "Kryptonite"},
            "01 - Intro.mp3": {"title": "Intro"}, "3. Titolo.mp3": {"title": "Titolo"},
            "02 - Artista - Titolo.mp3": {"artist": "Artista", "title": "Titolo"}}
    sbagliati = {n: ml.da_nome(n) for n, v in nomi.items() if ml.da_nome(n) != v}
    controlla(not sbagliati, "titolo e artista dal nome del file (883, 50 Cent, 01 - …): %s" % (sbagliati or "ok"))

    titolo("Aggancio a un finto Benessere (servi_app)")
    ml.LIB = lib
    srv = ThreadingHTTPServer(("127.0.0.1", 0), finto_benessere(x["app"]))
    porta = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        st, h, _ = chiedi(porta, "/musica/", login=False)
        controlla(st == 403, "senza login Tailscale: 403 (%d)" % st)
        st, h, _ = chiedi(porta, "/musica")
        controlla(st == 301 and h.get("location") == "musica/", "/musica -> 301 verso «musica/» (%d %s)" % (st, h.get("location")))
        st, h, b = chiedi(porta, "/musica/")
        controlla(st == 200 and b.startswith(b"<!doctype html>") and h.get("content-type", "").startswith("text/html")
                  and h.get("cache-control") == "no-cache" and h.get("x-content-type-options") == "nosniff",
                  "/musica/ -> index.html, no-cache, nosniff")
        st2, _, _ = chiedi(porta, "/musica/", intestazioni={"If-None-Match": h.get("etag", "")})
        controlla(st2 == 304, "index.html non cambiato -> 304")
        st, h, _ = chiedi(porta, "/musica/sw.js")
        controlla(st == 200 and h.get("content-type", "").startswith("application/javascript") and h.get("cache-control") == "no-cache",
                  "sw.js: application/javascript, no-cache")
        st, h, _ = chiedi(porta, "/musica/manifest.webmanifest")
        controlla(st == 200 and h.get("content-type") == "application/manifest+json", "manifest: application/manifest+json")
        st, h, b = chiedi(porta, "/musica/icon-192.png")
        controlla(st == 200 and h.get("content-type") == "image/png" and b == ROSSO, "icona PNG")
        for p in ("/musica/../segreto.txt", "/musica/%2e%2e/segreto.txt", "/musica/..%2fsegreto.txt", "/musica//etc/passwd",
                  "/musica/fuori.html", "/musica/server/musica_libreria.py", "/musica/.nascosto", "/musica/non-esiste.js",
                  "/musica/sub/", "/musica/a\\..\\segreto.txt"):
            st, _, b = chiedi(porta, p)
            controlla(st == 404 and b"segreto" not in b, "vietato %s -> 404" % p)
        st, _, _ = chiedi(porta, "/altro")
        controlla(st == 404, "il resto di Benessere non cambia (/altro -> 404 di Benessere)")
        st, _, b = chiedi(porta, "/")
        controlla(st == 200 and b"Benessere" in b, "la pagina di Benessere c'è ancora")

        st, h, b = chiedi(porta, "/musica/api/musica/catalogo")
        cj = json.loads(b.decode("utf-8")) if st == 200 else {}
        controlla(st == 200 and len(cj.get("brani", [])) == 12 and "scansione" in cj and cj.get("collegamenti") == coll
                  and len(cj.get("playlist", [])) == 3, "API catalogo sotto /musica/")
        st, h, bz = chiedi(porta, "/musica/api/musica/catalogo", intestazioni={"Accept-Encoding": "gzip, deflate, br"})
        controlla(st == 200 and h.get("content-encoding") == "gzip" and json.loads(gzip.decompress(bz).decode("utf-8")) == cj,
                  "catalogo compresso con gzip quando il browser lo accetta")
        controlla(not any(p["nome"] == "Di Sara" for p in cj.get("playlist", [])), "a samuele@ non arriva la playlist della radio fatta da sara@")
        st, h, b = chiedi(porta, "/musica/api/musica/catalogo", intestazioni={"Tailscale-User-Login": "sara@example.com"})
        nomi_sara = [p["nome"] for p in json.loads(b.decode("utf-8")).get("playlist", []) if p["fonte"] == "radio"] if st == 200 else []
        controlla(nomi_sara == ["Di Sara"], "a sara@ solo la sua playlist della radio (campo «di»): %s" % nomi_sara)
        controlla(any(p["nome"] == "Di Sara" for p in lib.catalogo()["playlist"]), "senza login (server da solo, senza Tailscale) le vede tutte")
        tutte = ml.Libreria([x["princ"], x["back"]], cache, **dict(args, radio_per_persona=False))
        controlla(any(p["nome"] == "Di Sara" for p in tutte.catalogo("samuele@example.com")["playlist"]),
                  "con radio_per_persona=False tutti vedono tutte le playlist della radio")
        st, h, b = chiedi(porta, "/musica/api/musica/stato")
        sj = json.loads(b.decode("utf-8")) if st == 200 else {}
        controlla(st == 200 and sj.get("brani") == 12 and "in_corso" in sj.get("scansione", {}), "API stato (leggera)")

        fid = I["Artist A - Song One.mp3"]
        with open(os.path.join(x["princ"], "Artist A - Song One.mp3"), "rb") as f:
            vero = f.read()
        st, h, b = chiedi(porta, "/musica/api/musica/file/" + fid, intestazioni={"Range": "bytes=0-99"})
        controlla(st == 206 and h.get("content-range") == "bytes 0-99/%d" % len(vero) and b == vero[:100]
                  and h.get("content-type") == "audio/mpeg", "file con Range: 206, i primi 100 byte")
        st, h, b = chiedi(porta, "/musica/api/musica/file/" + fid, intestazioni={"Range": "bytes=%d-" % (len(vero) - 10)})
        controlla(st == 206 and b == vero[-10:], "Range dalla fine")
        st, h, b = chiedi(porta, "/musica/api/musica/file/" + fid)
        controlla(st == 200 and b == vero and h.get("accept-ranges") == "bytes", "file intero")
        st, h, b = chiedi(porta, "/musica/api/musica/file/" + fid, metodo="HEAD")
        controlla(st == 200 and b == b"" and h.get("content-length") == str(len(vero)), "HEAD: niente corpo, lunghezza giusta")
        st, _, _ = chiedi(porta, "/musica/api/musica/file/" + I["Band B - Song Two.mp3"])
        controlla(st == 200, "file/<alias> -> 200")
        st, _, _ = chiedi(porta, "/musica/api/musica/file/ffffffffffffffff")
        controlla(st == 404, "file sconosciuto -> 404")
        st, _, _ = chiedi(porta, "/musica/api/musica/file/" + fid, intestazioni={"Range": "bytes=999999999-"})
        controlla(st == 416, "Range fuori dal file -> 416")
        st, h, b = chiedi(porta, "/musica/api/musica/copertina/" + k1)
        controlla(st == 200 and b == ROSSO and h.get("content-type") == "image/png", "API copertina")
        st, _, _ = chiedi(porta, "/musica/api/musica/sconosciuta")
        controlla(st == 404, "richiesta API sconosciuta -> 404")
        lib._lucchetto.acquire()
        t0 = time.time()
        st, _, b = chiedi(porta, "/musica/api/musica/aggiorna")
        controlla(st == 200 and json.loads(b.decode("utf-8")).get("avviata") is False and time.time() - t0 < 2,
                  "aggiorna non blocca la richiesta, nemmeno con una lettura in corso")
        lib._lucchetto.release()
        st, _, b = chiedi(porta, "/musica/api/musica/aggiorna")
        controlla(st == 200 and json.loads(b.decode("utf-8")).get("avviata") is True, "aggiorna avvia la lettura in sottofondo")
        for _ in range(100):
            st, _, b = chiedi(porta, "/musica/api/musica/stato")
            if not json.loads(b.decode("utf-8"))["scansione"]["in_corso"]:
                break
            time.sleep(0.05)
        controlla(not json.loads(b.decode("utf-8"))["scansione"]["in_corso"], "stato: la lettura è finita")
    finally:
        srv.shutdown()
        srv.server_close()

    titolo("App alla radice e vecchio gestisci(h)")

    class Radice(BaseHTTPRequestHandler):
        def do_GET(self):
            if ml.gestisci(self):
                return
            if not ml.servi_app(self, unquote(urlsplit(self.path).path), prefisso="", app=x["app"]):
                self.send_error(404)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Radice)
    porta = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        st, _, b = chiedi(porta, "/", login=False)
        controlla(st == 200 and b.startswith(b"<!doctype html>"), "alla radice: / -> index.html")
        st, _, b = chiedi(porta, "/qualcosa/api/musica/catalogo", login=False)
        controlla(st == 200 and len(json.loads(b.decode("utf-8"))["brani"]) == 12, "gestisci(h) risponde ancora a …/api/musica/catalogo")
        st, _, _ = chiedi(porta, "/../segreto.txt", login=False)
        controlla(st == 404, "alla radice: niente ../")
    finally:
        srv.shutdown()
        srv.server_close()


def aspetta(cond, secondi=5):
    fine = time.time() + secondi
    while time.time() < fine:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def png_grande(lato_x=800, lato_y=450):
    """PNG grande come quelle che yt-dlp mette negli mp3 (qui senza compressione: circa 1 MB), con una sfumatura."""
    def blocco(tipo, dati):
        return struct.pack(">I", len(dati)) + tipo + dati + struct.pack(">I", zlib.crc32(tipo + dati) & 0xFFFFFFFF)
    righe = b"".join(b"\x00" + bytes(v for x in range(lato_x) for v in (x * 255 // lato_x, y * 255 // lato_y, 128))
                     for y in range(lato_y))
    return (b"\x89PNG\r\n\x1a\n" + blocco(b"IHDR", struct.pack(">IIBBBBB", lato_x, lato_y, 8, 2, 0, 0, 0))
            + blocco(b"IDAT", zlib.compress(righe, 0)) + blocco(b"IEND", b""))


def prova_giro2(base):
    titolo("Disco collegato o staccato mentre la musica gira (Mac riacceso, dischi montati dopo)")
    p, k = os.path.join(base, "g2", "Principale"), os.path.join(base, "g2", "Backup")
    scrivi(os.path.join(p, "Uno.mp3"), mp3("Uno", "Artista U", secondi=10))
    scrivi(os.path.join(k, "Uno.mp3"), mp3("Uno", "Artista U", secondi=10))
    scrivi(os.path.join(k, "Solo backup.mp3"), mp3("Solo backup", "Artista S", secondi=12))
    os.rename(k, k + "-x")   # il backup non c'è quando la musica parte
    passo, ml.PASSO_DISCHI = ml.PASSO_DISCHI, 0.1
    lib = ml.Libreria([p, k], os.path.join(base, "g2", "cache"))
    t = threading.Thread(target=lib.ciclo, args=(3600,), daemon=True)
    t.start()
    sb = ml.id_brano("Solo backup.mp3")
    try:
        controlla(aspetta(lambda: lib.dati.presenti == [p] and not lib.scansione["in_corso"]), "all'avvio c'è solo il disco principale")
        os.rename(k + "-x", k)
        controlla(aspetta(lambda: any(b["id"] == sb and not b.get("assente") for b in lib.catalogo()["brani"])),
                  "backup collegato dopo l'avvio: i suoi brani arrivano da soli, senza Aggiorna e senza aspettare l'intervallo")
        os.rename(k, k + "-x")
        controlla(aspetta(lambda: any(b["id"] == sb and b.get("assente") for b in lib.catalogo()["brani"])),
                  "backup staccato: i suoi brani diventano «assente» da soli")
        os.rename(k + "-x", k)
        controlla(aspetta(lambda: not any(b.get("assente") for b in lib.catalogo()["brani"])), "backup ricollegato: di nuovo tutto ascoltabile")
    finally:
        lib._fermo.set()
        t.join(5)
        ml.PASSO_DISCHI = passo
    controlla(not t.is_alive(), "il controllo dei dischi si ferma")

    titolo("Stessa copertina su molti brani, disco principale staccato")
    p, k = os.path.join(base, "g2c", "Principale"), os.path.join(base, "g2c", "Backup")
    for i in range(7):   # titoli diversi: non sono doppioni
        scrivi(os.path.join(p, "A %d.mp3" % i), mp3("Canzone %d" % i, "Album Uguale", BLU, secondi=10 + 3 * i))
    scrivi(os.path.join(p, "Z.mp3"), mp3("Ultima", "Album Uguale", BLU, secondi=50))
    scrivi(os.path.join(k, "Z.mp3"), mp3("Ultima", "Album Uguale", BLU, secondi=50))
    lib = ml.Libreria([p, k], os.path.join(base, "g2c", "cache"))
    lib.scansiona()
    os.rename(os.path.join(base, "g2c", "Principale"), os.path.join(base, "g2c", "Principale-x"))
    lib.scansiona()
    v = [b for b in lib.catalogo()["brani"] if b["title"] == "Ultima"][0]
    c = lib.copertina(v["cover"] or "")
    controlla(not v.get("assente") and bool(c) and c[0] == BLU, "«Ultima» (anche sul backup): copertina dal backup anche se 7 brani del principale hanno la stessa")

    titolo("playlist.json della radio con forme strane: la lettura dei brani continua")
    p = os.path.join(base, "g2r", "Da YouTube")
    radio = os.path.join(base, "g2r", "playlist.json")
    scrivi(os.path.join(p, "Primo.mp3"), mp3("Primo", "Artista P", secondi=10))
    scrivi(radio, json.dumps({"playlist": [{"id": "r", "nome": "Radio", "brani": [b64("Da YouTube/Primo.mp3")]}]}).encode("utf-8"))
    lib = ml.Libreria([p], os.path.join(base, "g2r", "cache"), playlist_json=radio)
    lib.scansiona()
    log, ml._log = ml._log, lambda *a: None
    try:
        for i, forma in enumerate(({"playlist": 5}, {"playlist": "abc"}, [1, 2], {"playlist": [{"id": "x", "nome": "X", "brani": 5}]},
                                   {"playlist": [{"id": "y", "nome": "Y", "brani": True}, {"id": "r", "nome": "Radio", "brani": [b64("Da YouTube/Primo.mp3")]}]},
                                   {"playlist": [{"id": "z", "nome": {"a": 1}, "brani": [7, None, {"b": 2}, b64("Da YouTube/Primo.mp3")]}]})):
            scrivi(radio, json.dumps(forma).encode("utf-8"))
            scrivi(os.path.join(p, "Nuovo %d.mp3" % i), mp3("Nuovo %d" % i, "Artista N", secondi=20 + i))
            try:
                lib.scansiona()
                errore = lib.scansione.get("errore")
            except Exception as e:
                errore = repr(e)
            titoli = {b["title"] for b in lib.catalogo()["brani"]}
            controlla(not errore and "Nuovo %d" % i in titoli, "playlist.json %s: brano nuovo letto, nessun errore (%s)" % (json.dumps(forma)[:50], errore))
            if i == 2:
                controlla([q["nome"] for q in lib.catalogo()["playlist"]] == ["Radio"],
                          "playlist.json di forma sbagliata: resta la playlist della radio dell'ultima volta")
    finally:
        ml._log = log
    controlla([q["nome"] for q in lib.catalogo()["playlist"]] == ["{'a': 1}"], "voci strane dentro la playlist saltate, le altre lette")

    titolo("Copertine grandi (le PNG di yt-dlp): ridotte una volta, poi dalla cache")
    p = os.path.join(base, "g2p", "Da YouTube")
    grande = png_grande()
    scrivi(os.path.join(p, "Grande.mp3"), mp3("Grande", "Canale", grande, secondi=10))
    scrivi(os.path.join(p, "Piccola.mp3"), mp3("Piccola", "Canale", VERDE, secondi=11))
    cache = os.path.join(base, "g2p", "cache")
    lib = ml.Libreria([p], cache)
    lib.scansiona()
    kg = [b["cover"] for b in lib.catalogo()["brani"] if b["title"] == "Grande"][0]
    kp = [b["cover"] for b in lib.catalogo()["brani"] if b["title"] == "Piccola"][0]
    vero, sips = os.access(ml.SIPS, os.X_OK), ml.SIPS
    conta = os.path.join(base, "g2p", "volte")
    if not vero:   # non è un Mac: un finto sips che scrive un piccolo JPEG e conta le volte
        ml.SIPS = os.path.join(base, "g2p", "sips")
        scrivi(ml.SIPS, ("#!%s -I\nimport sys\na = sys.argv\nopen(a[a.index('--out') + 1], 'wb').write(b'\\xff\\xd8finto\\xff\\xd9')\n"
                         "open(%r, 'a').write('x')\n" % (sys.executable, conta)).encode("utf-8"))
        os.chmod(ml.SIPS, 0o755)
    try:
        c = lib.copertina(kg)
        controlla(bool(c) and c[1] == "image/jpeg" and c[0][:2] == b"\xff\xd8" and len(c[0]) < len(grande) // 4,
                  "copertina di %d KB servita ridotta: %s, %d KB (sips %s)" % (len(grande) // 1024, c and c[1], len(c[0]) // 1024 if c else -1,
                                                                             "vero" if vero else "finto"))
        controlla(os.path.isfile(os.path.join(cache, "copertine", kg[:20] + ".mini.jpg")), "la ridotta resta in ~/.atlante-musica/copertine")
        c2 = lib.copertina(kg)
        controlla(bool(c2) and c2 == c and (vero or open(conta).read() == "x"), "la seconda volta arriva dalla cache, senza rifarla")
        c3 = lib.copertina(kp)
        controlla(bool(c3) and c3 == (VERDE, "image/png"), "una copertina piccola si serve com'è")
    finally:
        ml.SIPS = sips
    ml.SIPS = os.path.join(base, "non-esiste", "sips")
    try:
        lib2 = ml.Libreria([p], os.path.join(base, "g2p", "cache2"))
        lib2.scansiona()
        c = lib2.copertina(kg)
        controlla(bool(c) and c == (grande, "image/png"), "senza sips (non è un Mac) la copertina grande si serve com'è")
    finally:
        ml.SIPS = sips


def main():
    print("Prova di musica_libreria.py con Python %s" % sys.version.split()[0])
    base = tempfile.mkdtemp(prefix="prova-musica-")
    try:
        prova(base)
        prova_giro2(base)
    except Exception:
        import traceback
        traceback.print_exc()
        ERRORI.append("la prova si è interrotta per un errore")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    if ERRORI:
        print("\n%d ERRORI:" % len(ERRORI))
        for e in ERRORI:
            print("  - " + e)
        sys.exit(1)
    print("\nTUTTO OK")


if __name__ == "__main__":
    main()
