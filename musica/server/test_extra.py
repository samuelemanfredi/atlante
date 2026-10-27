#!/usr/bin/env python3
"""Prove delle API dei moduli extra (api/extra/) di musica_libreria.py. Solo libreria standard:

    python3 -I test_extra.py

Avvia il server su una porta libera con una cartella di musica vuota, cartelle temporanee e programmi finti
(yt-dlp, say) in testa al PATH; l'IA è cercata su una porta dove non risponde nessuno (quindi «Ollama assente»
anche su un Mac dove Ollama c'è). Il proxy verso un sito pubblico si prova solo se la rete c'è.
"""
import http.client
import http.server
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from urllib.parse import quote as _quote, urlsplit

QUI =os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(QUI, "musica_libreria.py")

FINTO_YTDLP = r'''
import json, os, re, sys, time
a = sys.argv[1:]
if "--version" in a:
    print("2099.01.01")
    sys.exit(0)
url = a[-1]
nome = url.rstrip("/").rsplit("/", 1)[-1]
with open(os.path.join(os.environ["FINTO_DIR"], "ytdlp-%s.json" % nome), "w") as f:
    json.dump({"args": a, "pid": os.getpid()}, f)
with open(os.path.join(os.environ["FINTO_DIR"], "chiamate.log"), "a") as f:
    f.write(nome + "\n")
home = [v[5:] for i, v in enumerate(a) if i and a[i - 1] == "-P" and v.startswith("home:")][0]
stampe = [v for i, v in enumerate(a) if i and a[i - 1] in ("--print", "-O")]

def stampa(quando, **campi):
    for s in stampe:
        w, _, t = s.partition(":")
        if w == quando:
            # «%(a,b)s»: il primo dei campi che c'è (quelli che non ci sono restano scritti com'erano, come «NA»)
            t = re.sub(r"%\(([\w,]+)\)s", lambda m: next((campi[x] for x in m.group(1).split(",") if x in campi),
                                                       m.group(0)), t)
            print(t, flush=True)

print("[youtube] %s: Downloading webpage" % nome, flush=True)
if "errore" in nome:
    print("ERROR: [youtube] errore: Video non disponibile", flush=True)
    sys.exit(1)
stampa("before_dl", title="Video di prova")
print("[download]   0.0% of   10.00MiB at    1.00MiB/s ETA 00:10", flush=True)
print("[download]  42.0% of   10.00MiB at    1.00MiB/s ETA 00:05", flush=True)
time.sleep(60 if "lento" in nome else 1.5)
p = os.path.join(home, "Video di prova [%s].%s" % (nome, "m4a" if "-x" in a else "mp4"))
with open(p, "wb") as f:
    f.write(bytes(range(256)) * 4)
print("[download] 100% of   10.00MiB in 00:00:01 at 10.00MiB/s", flush=True)
stampa("after_move", filepath=p)
'''

# ffmpeg finto: scrive un MP3 piccolissimo con i tag ID3v2.3 ricevuti (-metadata), così la libreria li legge davvero
FINTO_FFMPEG = r'''
import json, os, struct, sys
a = sys.argv[1:]
ingressi = [a[i + 1] for i, v in enumerate(a) if v == "-i"]
if a and a[-1] == "-":
    sys.stderr.write("audio grezzo non disponibile\n")
    sys.exit(1)
if not ingressi or not all(os.path.isfile(p) for p in ingressi):
    sys.stderr.write("ingresso mancante\n")
    sys.exit(1)
tag = {}
for i, v in enumerate(a):
    if v == "-metadata" and i + 1 < len(a):
        k, _, x = a[i + 1].partition("=")
        tag[k] = x
with open(os.path.join(os.environ["FINTO_DIR"], "ffmpeg.log"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"args": a, "tag": tag}) + "\n")

def frame(fid, testo):
    d = b"\x01" + testo.encode("utf-16")
    return fid.encode() + struct.pack(">I", len(d)) + b"\x00\x00" + d

corpo = b"".join(frame(f, tag[k]) for f, k in (("TIT2", "title"), ("TPE1", "artist"), ("TALB", "album")) if tag.get(k))
n = len(corpo)
with open(a[-1], "wb") as f:
    f.write(b"ID3\x03\x00\x00" + bytes([(n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127]) + corpo)
    f.write((b"\xff\xfb\x90\x00" + bytes(413)) * 10)   # dieci fotogrammi MPEG da 128 kbit/s
'''

FINTO_SAY = r'''
import json, os, struct, sys
a = sys.argv[1:]
if a[:2] == ["-v", "?"]:
    print("Alex                en_US    # Most people recognize me by my voice.")
    print("Alice               it_IT    # Ciao! Mi chiamo Alice.")
    print("Eddy (Italian (Italy)) it_IT    # Ciao! Mi chiamo Eddy.")
    print("Fiona               en-scotland # Hello, my name is Fiona.")
    sys.exit(0)
if "--file-format=m4af" not in a or "--data-format=aac" not in a or "-o" not in a or "-f" not in a:
    sys.stderr.write("argomenti sbagliati: %r\n" % a)
    sys.exit(2)
if "-v" in a and a[a.index("-v") + 1] not in ("Alex", "Alice", "Eddy (Italian (Italy))", "Fiona"):
    sys.stderr.write("Voice not found\n")
    sys.exit(1)
out = a[a.index("-o") + 1]
with open(a[a.index("-f") + 1], encoding="utf-8") as f:
    testo = f.read()
with open(os.path.join(os.environ["FINTO_DIR"], "say.log"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"args": a, "testo": testo}) + "\n")

def scatola(t, d):
    return struct.pack(">I", 8 + len(d)) + t + d

# m4a minimo: ftyp + moov/mvhd con la durata (1 s ogni 10 caratteri), quanto basta per leggerne la durata
mvhd = bytes(12) + struct.pack(">II", 1000, len(testo) * 100) + bytes(80)
with open(out, "wb") as f:
    f.write(scatola(b"ftyp", b"M4A \0\0\0\0M4A mp42isom") + scatola(b"moov", scatola(b"mvhd", mvhd)))
'''


def quote(s):
    return _quote(s, safe="")


# ----- YouTube finto: risposte con la stessa struttura di youtubei/v1/search (ridotta all'essenziale) -----

def _brano_ytm(vid, titolo, artisti, album, durata, explicit):
    corse = []
    for a in artisti:
        corse += [{"text": a, "navigationEndpoint": {"browseEndpoint": {"browseId": "UC" + a}}}, {"text": " e "}]
    corse = corse[:-1] + [{"text": " • "}, {"text": album}, {"text": " • "}, {"text": durata}]
    return {"musicResponsiveListItemRenderer": {
        "flexColumns": [
            {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [
                {"text": titolo, "navigationEndpoint": {"watchEndpoint": {"videoId": vid}}}]}}},
            {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": corse}}},
            {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": "3,3 Mld riproduzioni"}]}}}],
        "badges": [{"musicInlineBadgeRenderer": {"icon": {"iconType": "MUSIC_EXPLICIT_BADGE"}}}] if explicit else [],
        "thumbnail": {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": [
            {"url": "https://yt3.googleusercontent.com/c-%s=w60-h60-s-l90-rj" % vid, "width": 60},
            {"url": "https://yt3.googleusercontent.com/c-%s=w120-h120-s-l90-rj" % vid, "width": 120}]}}},
        "overlay": {"musicItemThumbnailOverlayRenderer": {"content": {"musicPlayButtonRenderer": {
            "playNavigationEndpoint": {"watchEndpoint": {"videoId": vid}}}}}}}}


def _video_yt(vid, titolo, canale, durata):
    v = {"videoId": vid, "title": {"runs": [{"text": titolo}]}, "ownerText": {"runs": [{"text": canale}]},
         "thumbnail": {"thumbnails": [{"url": "https://i.ytimg.com/vi/%s/hq720.jpg" % vid}]}}
    if durata:
        v["lengthText"] = {"simpleText": durata}
    return {"videoRenderer": v}


RISPOSTA_YTM = {"contents": {"tabbedSearchResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
    "sectionListRenderer": {"contents": [{"musicShelfRenderer": {"contents": [
        _brano_ytm("NoExplicit1", "Without Me", ["Eminem"], "The Eminem Show", "4:50", False),
        _brano_ytm("tqxRidAWER8", "Without Me", ["Eminem"], "The Eminem Show", "4:51", True),
        _brano_ytm("RadioEdit01", "Shake That (Radio Edit)", ["Eminem", "Nate Dogg"], "Curtain Call", "4:35", True),
        _brano_ytm("D1I1x2pYMK0", "Kill You", ["Eminem"], "The Marshall Mathers LP", "1:04:25", True),
        _brano_ytm("TaylorClean", "Clean", ["Taylor Swift"], "1989", "4:31", False),
        _brano_ytm("tqxRidAWER8", "Without Me", ["Eminem"], "The Eminem Show", "4:51", True)]}}]}}}}]}}}
RISPOSTA_YT = {"contents": {"twoColumnSearchResultsRenderer": {"primaryContents": {"sectionListRenderer": {"contents": [
    {"itemSectionRenderer": {"contents": [
        _video_yt("YVkUvmDQ3HY", "Eminem - Without Me (Official Music Video)", "EminemMusic", "4:58"),
        _video_yt("LiveLiveLiv", "Eminem radio 24/7", "Radio", None),
        _video_yt("fsG39JgqTmo", "Without Me [HD]", "Eminem - Topic", "4:51")]}}]}}}}}


class FintoYouTube(http.server.BaseHTTPRequestHandler):
    richieste = []

    def do_POST(self):
        corpo = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        FintoYouTube.richieste.append((self.path, corpo))
        if corpo.get("query") == "rotto":
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        musica = corpo.get("context", {}).get("client", {}).get("clientName") == "WEB_REMIX"
        dati = json.dumps(RISPOSTA_YTM if musica else RISPOSTA_YT).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dati)))
        self.end_headers()
        self.wfile.write(dati)

    def log_message(self, *x):
        pass


def porta_libera():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def richiesta(porta, metodo, percorso, corpo=None, intest=None, tempo=30):
    """(codice, intestazioni, corpo in byte)."""
    c = http.client.HTTPConnection("127.0.0.1", porta, timeout=tempo)
    try:
        h = dict(intest or {})
        dati = None
        if corpo is not None:
            dati = corpo if isinstance(corpo, bytes) else json.dumps(corpo).encode("utf-8")
            h.setdefault("Content-Type", "application/json")
        c.request(metodo, percorso, body=dati, headers=h)
        r = c.getresponse()
        return r.status, r.headers, r.read()
    finally:
        c.close()


def aspetta(cond, tempo=20, passo=0.05):
    fine = time.time() + tempo
    while time.time() < fine:
        v = cond()
        if v:
            return v
        time.sleep(passo)
    return cond()


class Server:
    def __init__(self, base, cartelle, extra=()):
        self.base, self.c, self.extra = base, cartelle, list(extra)
        self.porta = porta_libera()
        self.p = None

    def avvia(self):
        env = dict(os.environ)
        env["PATH"] = self.c["bin"] + os.pathsep + env.get("PATH", "")
        env["FINTO_DIR"] = self.c["finto"]
        if self.c.get("youtubei"):
            env["ATLANTE_YOUTUBEI"] = self.c["youtubei"]
        self.log = open(os.path.join(self.base, "server-%d.log" % self.porta), "wb")
        self.p = subprocess.Popen([sys.executable, "-I", SERVER, self.c["musica"], "--porta", str(self.porta),
                                   "--cache", self.c["cache"], "--app", self.c["app"], "--video-dir", self.c["video"],
                                   "--libri-dir", self.c["libri"], "--musica-dir", self.c["scaricati"],
                                   "--ia-indirizzo", "http://127.0.0.1:%d" % self.c["porta_ollama"]] + self.extra,
                                  env=env, stdout=self.log, stderr=subprocess.STDOUT)

        def pronto():
            try:
                return richiesta(self.porta, "GET", "/api/extra/stato", tempo=5)[0] == 200
            except OSError:
                return self.p.poll() is not None   # il server è uscito: lo dice l'asserzione sotto
        aspetta(pronto, 20, 0.1)
        if self.p.poll() is not None:
            raise RuntimeError("il server non è partito: %s" % self.leggi_log())
        return self

    def leggi_log(self):
        self.log.flush()
        with open(self.log.name, "rb") as f:
            return f.read().decode("utf-8", "replace")

    def ferma(self):
        if self.p and self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(10)
            except subprocess.TimeoutExpired:
                self.p.kill()
                self.p.wait()
        self.log.close()

    def get(self, percorso, **k):
        return richiesta(self.porta, "GET", percorso, **k)

    def json(self, metodo, percorso, corpo=None, codice=200, **k):
        st, h, b = richiesta(self.porta, metodo, percorso, corpo, **k)
        try:
            j = json.loads(b.decode("utf-8"))
        except ValueError:
            j = None
        if codice is not None and st != codice:
            raise AssertionError("%s %s: atteso %d, avuto %d %r" % (metodo, percorso, codice, st, b[:300]))
        return j


def prepara_cartelle(base):
    c = {k: os.path.join(base, k) for k in ("musica", "cache", "app", "video", "libri", "bin", "finto")}
    for p in c.values():
        os.makedirs(p, exist_ok=True)
    c["scaricati"] = os.path.join(base, "scaricati")   # non c'è ancora: la crea il primo scaricamento
    os.makedirs(os.path.join(c["app"], "x"))
    for nome, testo in (("index.html", "<!doctype html><title>app</title>"), ("x/prova.html", "<!doctype html><p>x"),
                        ("x/kit.css", "body{}")):
        with open(os.path.join(c["app"], nome), "w", encoding="utf-8") as f:
            f.write(testo)
    for nome, codice in (("yt-dlp", FINTO_YTDLP), ("say", FINTO_SAY), ("ffmpeg", FINTO_FFMPEG)):
        p = os.path.join(c["bin"], nome)
        with open(p, "w", encoding="utf-8") as f:
            f.write("#!%s\n%s" % (sys.executable, codice))
        os.chmod(p, 0o755)
    c["porta_ollama"] = porta_libera()   # nessuno in ascolto: Ollama «assente»
    return c


class TestExtra(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="atlante-extra-")
        cls.c = prepara_cartelle(cls.base)
        cls.yt = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FintoYouTube)
        threading.Thread(target=cls.yt.serve_forever, daemon=True).start()
        cls.c["youtubei"] = "http://127.0.0.1:%d" % cls.yt.server_address[1]
        cls.s = Server(cls.base, cls.c).avvia()

    @classmethod
    def tearDownClass(cls):
        cls.s.ferma()
        cls.yt.shutdown()
        shutil.rmtree(cls.base, ignore_errors=True)

    def lavoro(self, tipo, id_):
        for l in self.s.json("GET", "/api/extra/%s" % tipo)["lavori"]:
            if l["id"] == id_:
                return l
        return None

    def finito(self, tipo, id_, tempo=20):
        l = aspetta(lambda: (lambda x: x if x and x["stato"] in ("fatto", "errore") else None)(self.lavoro(tipo, id_)), tempo)
        self.assertIsNotNone(l, "lavoro %s non finito: %r\n%s" % (id_, self.lavoro(tipo, id_), self.s.leggi_log()))
        return l

    def argomenti_ytdlp(self, nome):
        with open(os.path.join(self.c["finto"], "ytdlp-%s.json" % nome)) as f:
            return json.load(f)

    # ----- stato e richieste in generale -----

    def test_stato(self):
        j = self.s.json("GET", "/api/extra/stato")
        for k in ("ytdlp", "ffmpeg", "say", "ollama", "modelli", "video_dir", "libri_dir", "sistema"):
            self.assertIn(k, j)
        self.assertIs(j["ytdlp"], True)
        self.assertIs(j["say"], True)
        self.assertIs(j["ollama"], False)
        self.assertIsInstance(j["ffmpeg"], bool)
        self.assertEqual(j["modelli"], [])
        self.assertEqual(j["video_dir"], self.c["video"])
        self.assertEqual(j["libri_dir"], self.c["libri"])
        self.assertTrue(j["sistema"])
        self.assertEqual(j["ytdlp_versione"], "2099.01.01")

    def test_prefisso_e_head(self):
        self.assertEqual(self.s.get("/musica/api/extra/stato")[0], 200)
        st, h, b = richiesta(self.s.porta, "HEAD", "/api/extra/stato")
        self.assertEqual((st, b), (200, b""))

    def test_intestazioni_cache(self):
        st, h, _ = self.s.get("/api/extra/stato")
        self.assertEqual(h.get_all("Cache-Control"), ["no-store"])
        st, h, _ = self.s.get("/api/extra/proxy?url=http%3A%2F%2F127.0.0.1%2Fx.js")
        self.assertEqual(h.get_all("Cache-Control"), ["no-store"])
        for p in ("/x/prova.html", "/x/kit.css", "/index.html", "/"):
            st, h, _ = self.s.get(p)
            self.assertEqual(st, 200, p)
            self.assertEqual(h.get_all("Cache-Control"), ["no-cache"], p)

    def test_errori_richiesta(self):
        j = self.s.json("GET", "/api/extra/nulla", codice=404)
        self.assertIn("errore", j)
        self.assertIn("errore", self.s.json("DELETE", "/api/extra/stato", codice=405))
        self.assertIn("JSON", self.s.json("POST", "/api/extra/video", b"non json", codice=400)["errore"])
        self.assertIn("JSON", self.s.json("POST", "/api/extra/video", [1, 2], codice=400)["errore"])
        # un altro sito aperto nel browser non può avviare scaricamenti
        self.s.json("POST", "/api/extra/video", {"url": "https://example.com/v"}, codice=403,
                    intest={"Origin": "http://sito-cattivo.example"})
        self.s.json("POST", "/api/extra/video", {"url": "ftp://example.com/v"}, codice=400,
                    intest={"Origin": "http://127.0.0.1:%d" % self.s.porta})
        self.s.json("POST", "/api/extra/video", {"url": "https://example.com/v", "tipo": "film"}, codice=400)
        self.s.json("POST", "/api/extra/video/nessuno/annulla", codice=404)
        self.assertEqual(self.s.get("/api/extra/video/file/nessuno")[0], 404)
        self.assertEqual(richiesta(self.s.porta, "POST", "/altro", {})[0], 501)
        st, _, b = richiesta(self.s.porta, "POST", "/api/extra/ia", b"x" * 10,
                             intest={"Content-Length": str(30 * 1024 * 1024)}, tempo=10)
        self.assertEqual(st, 413)

    # ----- proxy -----

    def test_proxy_rifiuta_indirizzi_locali(self):
        for url, codice in (("http://127.0.0.1:%d/api/extra/stato" % self.s.porta, 403),
                            ("http://localhost:11434/api/tags", 403), ("http://10.0.0.1/", 403),
                            ("http://192.168.1.1/", 403), ("http://169.254.169.254/latest/meta-data/", 403),
                            ("http://100.100.100.100/", 403), ("http://[::1]/", 403), ("http://[::ffff:127.0.0.1]/", 403),
                            ("http://0.0.0.0/", 403), ("file:///etc/passwd", 400), ("ftp://example.com/", 400),
                            ("javascript:alert(1)", 400)):
            st, h, b = self.s.get("/api/extra/proxy?url=" + quote(url))
            self.assertEqual(st, codice, "%s -> %d %r" % (url, st, b))
            self.assertIn("errore", json.loads(b.decode("utf-8")))
        self.assertEqual(self.s.get("/api/extra/proxy")[0], 400)

    def test_proxy_sito_pubblico(self):
        try:
            socket.create_connection(("example.com", 443), 5).close()
        except OSError:
            self.skipTest("rete non disponibile")
        st, h, b = self.s.get("/api/extra/proxy?url=" + quote("https://example.com/"), tempo=40)
        if st in (502, 504):
            self.skipTest("example.com non raggiungibile dal server: %r" % b[:200])
        self.assertEqual(st, 200, b[:300])
        self.assertIn("text/html", h["Content-Type"])
        self.assertEqual(h.get_all("Cache-Control"), ["no-store"])
        self.assertIn(b"Example Domain", b)

    # ----- video -----

    def test_video_audio(self):
        stato = self.s.json("GET", "/api/extra/stato")
        id_ = self.s.json("POST", "/api/extra/video", {"url": "https://example.com/watch/audio1", "tipo": "audio"})["id"]
        self.assertTrue(id_)
        l = aspetta(lambda: (lambda x: x if x and x["stato"] == "in corso" and x["progresso"] > 0 else None)(
            self.lavoro("video", id_)), 15)
        self.assertIsNotNone(l, self.lavoro("video", id_))
        self.assertTrue(0 < l["progresso"] < 100, l)
        l = self.finito("video", id_)
        self.assertEqual(l["stato"], "fatto", l)
        self.assertEqual(l["progresso"], 100)
        self.assertEqual(l["titolo"], "Video di prova")
        self.assertEqual(l["nome"], "Video di prova [audio1].m4a")
        self.assertEqual(l["dimensione"], 1024)
        self.assertEqual(l["file"], "video/file/" + id_)
        self.assertEqual(l["url"], "https://example.com/watch/audio1")
        self.assertNotIn("_percorso", l)
        self.assertTrue(os.path.isfile(os.path.join(self.c["video"], l["nome"])))
        a = self.argomenti_ytdlp("audio1")["args"]
        self.assertIn("--newline", a)
        self.assertEqual(a[-2:], ["--", "https://example.com/watch/audio1"])
        self.assertIn("home:" + self.c["video"], a)
        if stato["ffmpeg"]:
            self.assertIn("-x", a)
            self.assertEqual(a[a.index("--audio-format") + 1], "m4a")
        # il file, intero e a pezzi
        st, h, b = self.s.get("/api/extra/" + l["file"])
        self.assertEqual((st, len(b)), (200, 1024))
        self.assertEqual(h["Content-Type"], "audio/mp4")
        st, h, b = self.s.get("/api/extra/video/file/" + id_, intest={"Range": "bytes=10-19"})
        self.assertEqual((st, b), (206, bytes(range(10, 20))))
        self.assertEqual(h["Content-Range"], "bytes 10-19/1024")
        # nessun file temporaneo rimasto
        self.assertEqual(os.listdir(os.path.join(self.c["cache"], "extra", "tmp")), [])

    def test_video_video_e_riavvio(self):
        stato = self.s.json("GET", "/api/extra/stato")
        id_ = self.s.json("POST", "/api/extra/video", {"url": "https://example.com/v/video1", "tipo": "video",
                                                       "qualita": "720"})["id"]
        lento = self.s.json("POST", "/api/extra/video", {"url": "https://example.com/v/lento"})["id"]
        l = self.finito("video", id_)
        self.assertEqual(l["stato"], "fatto", l)
        self.assertEqual(l["nome"], "Video di prova [video1].mp4")
        a = self.argomenti_ytdlp("video1")["args"]
        f = a[a.index("-f") + 1]
        self.assertIn("height<=720", f)
        if stato["ffmpeg"]:
            self.assertTrue(f.startswith("bv*[height<=720]+ba/b[height<=720]"), f)
            self.assertEqual(a[a.index("--merge-output-format") + 1], "mp4")
        self.assertEqual(self.s.get("/api/extra/video/file/" + id_)[1]["Content-Type"], "video/mp4")
        self.assertTrue(aspetta(lambda: (self.lavoro("video", lento) or {}).get("stato") == "in corso", 10))
        # dopo un riavvio (secondo server con la stessa cache) i lavori finiti restano, quelli a metà sono interrotti
        s2 = Server(self.base, self.c).avvia()
        try:
            elenco = {x["id"]: x for x in s2.json("GET", "/api/extra/video")["lavori"]}
            self.assertEqual(elenco[id_]["stato"], "fatto")
            self.assertEqual(elenco[id_]["nome"], l["nome"])
            self.assertEqual(s2.get("/api/extra/video/file/" + id_)[0], 200)
            self.assertEqual(elenco[lento]["stato"], "errore")
            self.assertIn("Interrotto", elenco[lento]["errore"])
        finally:
            s2.ferma()
        self.s.json("POST", "/api/extra/video/%s/annulla" % lento)
        self.assertEqual(self.finito("video", lento)["errore"], "Annullato")
        # un lavoro finito, «annullato», esce dall'elenco ma il file resta
        self.assertEqual(self.s.json("POST", "/api/extra/video/%s/annulla" % id_), {"ok": True})
        self.assertIsNone(self.lavoro("video", id_))
        self.assertTrue(os.path.isfile(os.path.join(self.c["video"], l["nome"])))

    def test_video_annulla(self):
        id_ = self.s.json("POST", "/api/extra/video", {"url": "https://example.com/v/lento", "tipo": "video"})["id"]
        self.assertTrue(aspetta(lambda: (self.lavoro("video", id_) or {}).get("progresso", 0) > 0, 15))
        pid = aspetta(lambda: os.path.exists(os.path.join(self.c["finto"], "ytdlp-lento.json")) and
                      self.argomenti_ytdlp("lento")["pid"], 5)
        self.assertEqual(self.s.json("POST", "/api/extra/video/%s/annulla" % id_), {"ok": True})
        l = self.finito("video", id_, 15)
        self.assertEqual((l["stato"], l["errore"]), ("errore", "Annullato"))

        def morto():
            try:
                os.kill(pid, 0)
                return False
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
        self.assertTrue(aspetta(morto, 10), "yt-dlp ancora vivo dopo l'annullamento")
        self.assertFalse(os.path.exists(os.path.join(self.c["cache"], "extra", "tmp", "video-" + id_)))

    def test_video_errore(self):
        id_ = self.s.json("POST", "/api/extra/video", {"url": "https://example.com/v/errore"})["id"]
        l = self.finito("video", id_)
        self.assertEqual(l["stato"], "errore")
        self.assertIn("Video non disponibile", l["errore"])

    # ----- musica da internet -----

    def chiamate_ytdlp(self, nome):
        try:
            with open(os.path.join(self.c["finto"], "chiamate.log")) as f:
                return [r.strip() for r in f].count(nome)
        except OSError:
            return 0

    def ffmpeg_ultimo(self):
        with open(os.path.join(self.c["finto"], "ffmpeg.log"), encoding="utf-8") as f:
            return json.loads(f.read().strip().splitlines()[-1])

    def test_musica_cerca_youtube_music(self):
        j = self.s.json("GET", "/api/extra/musica/cerca?q=" + quote("eminem  without me"))
        self.assertEqual(j["fonte"], "ytm")
        r = j["risultati"]
        # doppione tolto; explicit prima, censurate (la copia senza «E» e la «Radio Edit») in fondo, il resto in ordine
        # «Clean» è il titolo del brano, non una versione censurata
        self.assertEqual([x["id"] for x in r], ["tqxRidAWER8", "D1I1x2pYMK0", "TaylorClean", "NoExplicit1", "RadioEdit01"])
        self.assertEqual([x["censurata"] for x in r], [False, False, False, True, True])
        self.assertEqual(r[0], {"id": "tqxRidAWER8", "titolo": "Without Me", "artista": "Eminem", "album": "The Eminem Show",
                                "durata": 291, "copertina": "https://yt3.googleusercontent.com/c-tqxRidAWER8=w544-h544-s-l90-rj",
                                "explicit": True, "censurata": False})
        self.assertEqual(r[1]["durata"], 3865)
        self.assertEqual(r[4]["artista"], "Eminem e Nate Dogg")
        percorso, corpo = FintoYouTube.richieste[-1]
        self.assertEqual(percorso, "/youtubei/v1/search?prettyPrint=false")
        self.assertEqual(corpo["query"], "eminem without me")   # spazi in più tolti
        self.assertEqual(corpo["context"]["client"]["clientName"], "WEB_REMIX")
        self.assertEqual(corpo["context"]["client"]["hl"], "it")
        self.assertTrue(corpo["params"])   # filtro «Brani»

    def test_musica_cerca_youtube(self):
        r = self.s.json("GET", "/api/extra/musica/cerca?fonte=yt&q=eminem")["risultati"]
        self.assertEqual([(x["id"], x["artista"], x["titolo"]) for x in r],
                         [("YVkUvmDQ3HY", "Eminem", "Without Me"), ("fsG39JgqTmo", "Eminem", "Without Me")])   # niente dirette
        self.assertEqual(r[0]["copertina"], "https://i.ytimg.com/vi/YVkUvmDQ3HY/mqdefault.jpg")
        self.assertEqual(r[0]["durata"], 298)
        self.assertNotIn("params", FintoYouTube.richieste[-1][1])

    def test_musica_cerca_errori(self):
        self.assertIn("errore", self.s.json("GET", "/api/extra/musica/cerca?q=%20", codice=400))
        self.assertIn("errore", self.s.json("GET", "/api/extra/musica/cerca?q=x&fonte=spotify", codice=400))
        # YouTube non risponde e anche la riserva (yt-dlp) non trova niente
        self.assertIn("YouTube non risponde", self.s.json("GET", "/api/extra/musica/cerca?q=rotto", codice=502)["errore"])
        self.assertIn("errore", self.s.json("POST", "/api/extra/musica/cerca?q=x", {}, codice=405))

    def test_musica_ascolta(self):
        st, h, b = self.s.get("/api/extra/musica/ascolta/Ascolto0001", tempo=40)
        self.assertEqual((st, len(b)), (200, 1024), b[:200])
        self.assertIn(h["Content-Type"], ("audio/mp4", "video/mp4"))
        a = self.argomenti_ytdlp("watch?v=Ascolto0001")["args"]
        self.assertEqual(a[-2:], ["--", "https://music.youtube.com/watch?v=Ascolto0001"])
        self.assertEqual(a[a.index("-f") + 1], "ba[ext=m4a]/ba/b")
        # la seconda volta (anche a pezzi) dalla cache: yt-dlp non riparte
        st, h, b = self.s.get("/api/extra/musica/ascolta/Ascolto0001", intest={"Range": "bytes=10-19"})
        self.assertEqual((st, b), (206, bytes(range(10, 20))))
        self.assertEqual(self.chiamate_ytdlp("watch?v=Ascolto0001"), 1)
        self.assertEqual(self.s.get("/api/extra/musica/ascolta/corto")[0], 400)
        self.assertEqual(self.s.get("/api/extra/musica/ascolta/errore00000")[0], 502)

    def test_musica_scarica(self):
        brano = {"id": "Scarica0001", "titolo": "Without Me", "artista": "Eminem", "album": "The Eminem Show",
                 "copertina": "https://127.0.0.1/copertina.jpg", "explicit": True}
        id_ = self.s.json("POST", "/api/extra/musica", brano)["id"]
        self.assertEqual(self.s.json("POST", "/api/extra/musica", brano)["id"], id_)   # due tocchi: un lavoro solo
        l = self.finito("musica", id_, 40)
        self.assertEqual(l["stato"], "fatto", l)
        self.assertEqual(l["nome"], "Eminem - Without Me.mp3")
        self.assertEqual((l["yt"], l["url"]), ("Scarica0001", "https://music.youtube.com/watch?v=Scarica0001"))
        self.assertTrue(l["brano"], l)
        self.assertNotIn("_percorso", l)
        self.assertTrue(os.path.isfile(os.path.join(self.c["scaricati"], "Eminem - Without Me.mp3")))
        self.assertEqual([n for n in os.listdir(self.c["scaricati"]) if n.startswith(".")], [])
        f = self.ffmpeg_ultimo()
        self.assertEqual(f["tag"]["title"], "Without Me")
        self.assertEqual(f["tag"]["purl"], "https://music.youtube.com/watch?v=Scarica0001")
        self.assertEqual(f["tag"]["ITUNESADVISORY"], "1")
        self.assertEqual(f["args"][f["args"].index("-q:a") + 1], "0")   # MP3 alla qualità massima, come il Cloud
        # nella libreria, con i tag giusti
        cat = self.s.json("GET", "/api/musica/catalogo")["brani"]
        b = [x for x in cat if x["id"] == l["brano"]]
        self.assertEqual([(x["title"], x["artist"], x["album"]) for x in b], [("Without Me", "Eminem", "The Eminem Show")])
        # di nuovo lo stesso brano a lavoro finito: niente doppione
        self.assertEqual(self.s.json("POST", "/api/extra/musica", brano)["id"], id_)
        self.assertEqual(self.chiamate_ytdlp("watch?v=Scarica0001"), 1)
        # un brano con lo stesso nome ma un altro video: file accanto, non sopra
        l2 = self.finito("musica", self.s.json("POST", "/api/extra/musica", dict(brano, id="Scarica0002"))["id"], 40)
        self.assertEqual(l2["nome"], "Eminem - Without Me (2).mp3")
        # tolto dall'elenco: il brano resta nella libreria
        self.assertEqual(self.s.json("POST", "/api/extra/musica/%s/annulla" % id_), {"ok": True})
        self.assertIsNone(self.lavoro("musica", id_))
        self.assertTrue(os.path.isfile(os.path.join(self.c["scaricati"], "Eminem - Without Me.mp3")))
        self.assertEqual(self.s.json("GET", "/api/extra/stato")["musica_dir"], self.c["scaricati"])

    def test_musica_link(self):
        # un link di un altro sito: titolo dalla pagina
        l = self.finito("musica", self.s.json("POST", "/api/extra/musica", {"url": "https://soundcloud.com/x/brano1"})["id"], 40)
        self.assertEqual((l["stato"], l["nome"], l["yt"]), ("fatto", "Video di prova.mp3", ""), l)
        self.assertEqual(self.ffmpeg_ultimo()["tag"]["purl"], "https://soundcloud.com/x/brano1")
        # un link di YouTube vale come un brano della ricerca
        l = self.finito("musica", self.s.json("POST", "/api/extra/musica", {"url": "https://youtu.be/Collegato01?si=abc"})["id"], 40)
        self.assertEqual((l["stato"], l["yt"]), ("fatto", "Collegato01"), l)
        self.assertEqual(l["titolo"], "Video di prova")

    def test_musica_annulla_ed_errori(self):
        id_ = self.s.json("POST", "/api/extra/musica", {"url": "https://example.com/m/lento"})["id"]
        self.assertTrue(aspetta(lambda: (self.lavoro("musica", id_) or {}).get("progresso", 0) > 0, 15))
        self.assertEqual(self.s.json("POST", "/api/extra/musica/%s/annulla" % id_), {"ok": True})
        l = self.finito("musica", id_, 15)
        self.assertEqual((l["stato"], l["errore"]), ("errore", "Annullato"))
        l = self.finito("musica", self.s.json("POST", "/api/extra/musica", {"url": "https://example.com/m/errore"})["id"])
        self.assertEqual(l["stato"], "errore")
        self.assertIn("Video non disponibile", l["errore"])
        for corpo in ({}, {"id": "corto"}, {"url": "ftp://example.com/x"}, {"url": "non un link"}):
            self.assertIn("errore", self.s.json("POST", "/api/extra/musica", corpo, codice=400), corpo)
        self.s.json("POST", "/api/extra/musica", {"id": "Scarica0001"}, codice=403, intest={"Origin": "http://cattivo.example"})
        self.s.json("POST", "/api/extra/musica/nessuno/annulla", codice=404)

    # ----- dispositivi (telecomando) -----

    def test_gestione_senza_indirizzo_pubblico(self):
        r = self.s.json("POST", "/api/extra/gestione/persone", {"nome": "Ida"}, codice=409)
        self.assertIn("ATLANTE_PUBBLICO", r["errore"])

    def test_dispositivi(self):
        iphone, mac = "iphone-prova-0001", "mac-prova-00000001"
        stato = {"brano": {"sid": "abc", "titolo": "Without Me", "artista": "Eminem"}, "suona": True, "t": 12.5,
                 "coda": ["abc", "def"]}
        self.assertEqual(self.s.json("POST", "/api/extra/dispositivi/%s/stato" % mac,
                                     {"nome": "  MacBook   di Samuele ", "tipo": "mac", "stato": stato}), {"comandi": []})
        self.s.json("POST", "/api/extra/dispositivi/%s/stato" % iphone, {"nome": "iPhone", "tipo": "telefono", "stato": {}})
        d = {x["id"]: x for x in self.s.json("GET", "/api/extra/dispositivi")["dispositivi"]}
        self.assertEqual((d[mac]["nome"], d[mac]["tipo"], d[mac]["stato"]), ("MacBook di Samuele", "mac", stato))
        self.assertEqual(d[iphone]["tipo"], "pc")   # tipo sconosciuto
        self.assertLess(d[mac]["fa"], 5)
        # comando dall'iPhone al Mac: l'attesa lunga del Mac risponde subito
        esito = {}

        def attendi():
            t0 = time.time()
            esito["c"] = self.s.json("GET", "/api/extra/dispositivi/%s/comandi?attesa=10" % mac)["comandi"]
            esito["dopo"] = time.time() - t0
        th = threading.Thread(target=attendi)
        th.start()
        time.sleep(0.4)
        self.assertEqual(self.s.json("POST", "/api/extra/dispositivi/%s/comando" % mac, {"azione": "seek", "t": 42}), {"ok": True})
        th.join(10)
        self.assertEqual(esito["c"], [{"azione": "seek", "t": 42}])
        self.assertLess(esito["dopo"], 5)
        # consegnato una volta sola; senza comandi l'attesa finisce vuota
        self.assertEqual(self.s.json("GET", "/api/extra/dispositivi/%s/comandi?attesa=0.3" % mac)["comandi"], [])
        # i comandi arrivano anche con lo stato (se l'attesa lunga non c'è)
        self.s.json("POST", "/api/extra/dispositivi/%s/comando" % mac, {"azione": "pause"})
        self.s.json("POST", "/api/extra/dispositivi/%s/comando" % mac, {"azione": "next"})
        self.assertEqual(self.s.json("POST", "/api/extra/dispositivi/%s/stato" % mac, {"nome": "MacBook", "stato": stato})["comandi"],
                         [{"azione": "pause"}, {"azione": "next"}])
        # errori
        self.assertIn("errore", self.s.json("POST", "/api/extra/dispositivi/sconosciuto-000/comando", {"azione": "play"}, codice=404))
        self.assertIn("errore", self.s.json("POST", "/api/extra/dispositivi/%s/comando" % mac, {"azione": "spegni"}, codice=400))
        self.assertIn("errore", self.s.json("POST", "/api/extra/dispositivi/corto/stato", {"stato": {}}, codice=400))
        self.assertIn("errore", self.s.json("POST", "/api/extra/dispositivi/%s/stato" % mac, {"stato": [1]}, codice=400))
        self.assertIn("errore", self.s.json("POST", "/api/extra/dispositivi/%s/stato" % mac, {"stato": {"x": "a" * 40000}}, codice=413))
        self.assertIn("errore", self.s.json("GET", "/api/extra/dispositivi/%s/comandi?attesa=x" % mac, codice=400))
        self.assertIn("errore", self.s.json("GET", "/api/extra/dispositivi/%s/stato" % mac, codice=405))
        self.s.json("POST", "/api/extra/dispositivi/%s/comando" % mac, {"azione": "play"}, codice=403,
                    intest={"Origin": "http://cattivo.example"})

    # ----- voci e audiolibri -----

    def test_voci(self):
        v = self.s.json("GET", "/api/extra/voci")["voci"]
        # prima le italiane, poi le altre per lingua
        self.assertEqual([x["nome"] for x in v], ["Alice", "Eddy (Italian (Italy))", "Fiona", "Alex"])
        self.assertEqual(v[1], {"nome": "Eddy (Italian (Italy))", "lingua": "it_IT", "esempio": "Ciao! Mi chiamo Eddy."})
        self.assertEqual(v[2]["lingua"], "en-scotland")

    def test_audiolibro(self):
        corpo = {"titolo": "Libro di prova", "autore": "Autrice", "voce": "Alice", "velocita": 200,
                 "capitoli": [{"titolo": "Uno", "testo": "Nel mezzo del cammin di nostra vita è così."},
                              {"titolo": "Vuoto", "testo": "   "},
                              {"titolo": "Due/tre: fine", "testo": "Secondo capitolo."}]}
        id_ = self.s.json("POST", "/api/extra/audiolibro", corpo)["id"]
        l = self.finito("audiolibro", id_)
        self.assertEqual(l["stato"], "fatto", l)
        self.assertEqual((l["titolo"], l["autore"], l["progresso"]), ("Libro di prova", "Autrice", 100))
        self.assertEqual([(c["n"], c["titolo"], c["pronto"]) for c in l["capitoli"]],
                         [(0, "Uno", True), (1, "Due/tre: fine", True)])
        self.assertAlmostEqual(l["capitoli"][0]["durata"], 4.3, places=1)
        cart = os.path.join(self.c["libri"], "Autrice - Libro di prova")
        self.assertEqual(sorted(os.listdir(cart)), ["001 Uno.m4a", "002 Due tre fine.m4a"])
        with open(os.path.join(self.c["finto"], "say.log"), encoding="utf-8") as f:
            chiamate = [json.loads(r) for r in f if r.strip()]
        primo = [c for c in chiamate if c["testo"].startswith("Nel mezzo")][-1]
        self.assertEqual(primo["testo"], "Nel mezzo del cammin di nostra vita è così.")
        a = primo["args"]
        self.assertEqual(a[a.index("-v") + 1], "Alice")
        self.assertEqual(a[a.index("-r") + 1], "200")
        self.assertTrue(a[a.index("-o") + 1].endswith(".m4a"))
        st, h, b = self.s.get("/api/extra/audiolibro/file/%s/0" % id_)
        self.assertEqual((st, h["Content-Type"]), (200, "audio/mp4"))
        self.assertEqual(b[4:8], b"ftyp")
        st, h, b = self.s.get("/api/extra/audiolibro/file/%s/1" % id_, intest={"Range": "bytes=0-7"})
        self.assertEqual((st, len(b)), (206, 8))
        self.assertEqual(self.s.get("/api/extra/audiolibro/file/%s/2" % id_)[0], 404)
        self.assertEqual(self.s.get("/api/extra/audiolibro/file/%s/x" % id_)[0], 404)
        # DELETE: via dall'elenco e dal disco
        self.assertEqual(self.s.json("DELETE", "/api/extra/audiolibro/" + id_), {"ok": True})
        self.assertIsNone(self.lavoro("audiolibro", id_))
        self.assertFalse(os.path.exists(cart))
        self.s.json("DELETE", "/api/extra/audiolibro/" + id_, codice=404)

    def test_audiolibro_richieste_sbagliate(self):
        self.s.json("POST", "/api/extra/audiolibro", {"titolo": "x", "capitoli": []}, codice=400)
        self.s.json("POST", "/api/extra/audiolibro", {"titolo": "x", "capitoli": [{"testo": ""}]}, codice=400)
        j = self.s.json("POST", "/api/extra/audiolibro", {"voce": "Nessuna", "capitoli": [{"testo": "ciao"}]}, codice=400)
        self.assertIn("Voce", j["errore"])

    # ----- IA e dispositivo -----

    def test_ia_senza_ollama(self):
        self.assertEqual(self.s.json("GET", "/api/extra/ia"), {"attiva": False, "modelli": [], "modello": ""})
        j = self.s.json("POST", "/api/extra/ia", {"prompt": "Ciao", "sistema": "", "json": False}, codice=503)
        self.assertEqual(j, {"errore": "IA locale non attiva sul Mac: installa Ollama (ollama.com) e un modello, "
                                       "es. ollama pull llama3.2"})
        self.s.json("POST", "/api/extra/ia", {"prompt": ""}, codice=400)

    def test_dispositivo(self):
        j = self.s.json("GET", "/api/extra/dispositivo", tempo=60)
        for k in ("nome", "sistema", "versione", "avvio", "memoria", "dischi", "pulizia"):
            self.assertIn(k, j)
        self.assertTrue(j["nome"])
        self.assertGreater(j["memoria"]["totale"], 0)
        self.assertGreaterEqual(j["memoria"]["libera"], 0)
        self.assertGreater(j["avvio"], 0)
        self.assertTrue(j["dischi"])
        d = j["dischi"][0]
        self.assertEqual(d["percorso"], "/")
        self.assertGreater(d["totale"], 0)
        self.assertGreaterEqual(d["libero"], 0)
        self.assertIsInstance(j["pulizia"], list)
        for v in j["pulizia"]:
            self.assertTrue({"nome", "percorso", "dimensione"} <= set(v), v)


def suono_finto(secondi, seme, hz=8000):
    """Audio finto a 8 kHz: colpi casuali (circa 4 al secondo, ampiezze e lunghezze diverse) su un fondo leggero,
    come gli attacchi di un brano vero. Stesso seme = stesso suono."""
    import array
    import random
    r = random.Random(seme)
    a = array.array("h", bytes(2 * int(secondi * hz)))
    t = 0.0
    while True:
        t += r.expovariate(4.0)
        if t >= secondi:
            break
        i, amp, lung = int(t * hz), r.randint(2000, 20000), r.randint(80, 400)
        for k in range(min(lung, len(a) - i)):
            a[i + k] = max(-32767, min(32767, a[i + k] + int(amp * (1 - k / lung) * (1 if (k // 3) % 2 else -1))))
    for j in range(0, len(a), 7):
        a[j] = max(-32767, min(32767, a[j] + r.randint(-150, 150)))
    return a


class TestVideoclip(unittest.TestCase):
    """Allineamento brano ↔ videoclip su suoni finti e preparazione con scaricamento e audio finti."""

    @classmethod
    def setUpClass(cls):
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("musica_libreria_clip", SERVER)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)
        cls.brano = suono_finto(120, 1)

    def tempo_video(self, mappa, t):
        for x in mappa:
            if x["da"] <= t < x["a"]:
                return t + x["sp"] + x.get("k", 0) * (t - x["da"])
        return None

    def allinea(self, brano, video):
        return self.m.allinea(self.m._caratteristiche([brano]), self.m._caratteristiche([video]))

    def test_introduzione_e_taglio(self):
        b, hz = self.brano, 8000
        video = suono_finto(7.3, 99) + b[:60 * hz] + b[80 * hz:]   # 7,3 s in più all'inizio, 20 s tagliati a metà
        r = self.allinea(b, video)
        self.assertEqual(len(r["mappa"]), 2, r)
        for t in (2, 10, 30, 50, 57):
            self.assertAlmostEqual(self.tempo_video(r["mappa"], t), t + 7.3, delta=0.03, msg=(t, r["mappa"]))
        for t in (83, 90, 110, 117):
            self.assertAlmostEqual(self.tempo_video(r["mappa"], t), t - 12.7, delta=0.03, msg=(t, r["mappa"]))
        for t in (62, 70, 77):   # il pezzo tagliato dal video: copertina, mai un'altra scena
            self.assertIsNone(self.tempo_video(r["mappa"], t), (t, r["mappa"]))
        self.assertGreater(r["copertura"], 0.75)

    def test_finale_diverso_e_silenzio(self):
        import array
        b, hz = self.brano, 8000
        silenzio = array.array("h", bytes(2 * 20 * hz))
        con_silenzio = b[:50 * hz] + silenzio + b[50 * hz:]   # 20 s muti in mezzo al brano
        video = b[:50 * hz] + silenzio + b[50 * hz:100 * hz] + suono_finto(30, 5)   # finale del video diverso
        r = self.allinea(con_silenzio, video)
        self.assertAlmostEqual(self.tempo_video(r["mappa"], 20), 20, delta=0.03, msg=r["mappa"])
        self.assertAlmostEqual(self.tempo_video(r["mappa"], 110), 110, delta=0.03, msg=r["mappa"])
        for t in (125, 135):   # il brano finisce in un altro modo: copertina
            self.assertIsNone(self.tempo_video(r["mappa"], t), (t, r["mappa"]))

    def test_deriva(self):
        # il video è l'1,5 per mille più lento (es. 25 contro 24,96 fotogrammi) e comincia 4 s dopo
        import array
        b = self.brano
        lento = array.array("h", (b[int(i / 1.0015)] for i in range(int(len(b) * 1.0015))))
        r = self.allinea(b, suono_finto(4, 3) + lento)
        for t in (5, 60, 115):
            self.assertAlmostEqual(self.tempo_video(r["mappa"], t), 4 + t * 1.0015, delta=0.03, msg=(t, r["mappa"]))

    def test_video_senza_rapporto(self):
        r = self.allinea(self.brano, suono_finto(150, 42))
        self.assertLess(r["copertura"], self.m.CLIP_COPERTURA, r)

    def test_preparazione_e_scheda(self):
        m = self.m
        base = tempfile.mkdtemp(prefix="atlante-clip-")
        try:
            cart, brano = os.path.join(base, "clip"), os.path.join(base, "A - Brano.mp3")
            with open(brano, "wb") as f:
                f.write(b"mp3")
            piano = os.path.join(base, "piano.json")
            with open(piano, "w") as f:
                json.dump({"A - Brano.mp3": {"id": "abcdefghijk"}, "B.mp3": None}, f)
            v = m.Videoclip(piano, cart)
            pcm = {brano: self.brano}
            vid_pcm = suono_finto(2.5, 7) + self.brano

            def scarica(vid):
                p = os.path.join(cart, vid + ".mp4")
                with open(p, "wb") as f:
                    f.write(b"video")
                pcm[p] = vid_pcm
                return p
            v._scarica = scarica
            m._bande = lambda p, ff: [pcm[p]]
            m._programma = lambda n: "/bin/" + n
            m._durata_audio = lambda p: 122.5
            s = v.prepara("A - Brano.mp3", brano)
            self.assertEqual(s["stato"], "pronto", s)
            self.assertAlmostEqual(s["mappa"][0]["sp"], 2.5, delta=0.03)
            st = os.stat(brano)
            firma = "%d:%d" % (st.st_size, int(st.st_mtime))
            self.assertEqual(v.pronti(), {"A - Brano.mp3": firma})
            x = v.stato(brano, firma)
            self.assertEqual((x["stato"], x["url"], x["durata"]), ("pronto", "clip/file/abcdefghijk.mp4", 122.5))
            # il file del brano cambia (es. versione explicit): si riallinea, la scheda vecchia non vale più
            self.assertEqual(v.stato(brano, "1:2")["stato"], "in coda")
            self.assertEqual(v.stato(os.path.join(base, "B.mp3"), "1:1"), {"stato": "nessuno"})
        finally:
            shutil.rmtree(base, ignore_errors=True)


class TestClipServer(unittest.TestCase):
    """API dei videoclip con un brano della libreria e il suo videoclip già pronto (scheda e MP4 finti)."""

    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="atlante-clipsrv-")
        cls.c = prepara_cartelle(cls.base)
        cls.brano = os.path.join(cls.c["musica"], "Artista - Brano.mp3")
        with open(cls.brano, "wb") as f:
            f.write(b"ID3\x03\x00\x00\x00\x00\x00\x00" + (b"\xff\xfb\x90\x00" + bytes(413)) * 20)
        cls.clip = os.path.join(cls.base, "clip")
        os.makedirs(cls.clip)
        with open(os.path.join(cls.clip, "abcdefghijk.mp4"), "wb") as f:
            f.write(bytes(range(256)) * 8)
        st = os.stat(cls.brano)
        cls.mappa = [{"da": 0.0, "a": 8.0, "sp": 3.25}]
        with open(os.path.join(cls.clip, "abcdefghijk.json"), "w") as f:
            json.dump({"id": "abcdefghijk", "nome": "Artista - Brano.mp3", "firma": "%d:%d" % (st.st_size, int(st.st_mtime)),
                       "stato": "pronto", "mappa": cls.mappa, "durata_video": 11.5, "versione": 2}, f)
        cls.piano = os.path.join(cls.base, "videoclip-piano.json")
        with open(cls.piano, "w") as f:
            json.dump({"Artista - Brano.mp3": {"id": "abcdefghijk"}}, f)
        cls.s = Server(cls.base, cls.c, ["--clip-piano", cls.piano, "--clip-dir", cls.clip]).avvia()

    @classmethod
    def tearDownClass(cls):
        cls.s.ferma()
        shutil.rmtree(cls.base, ignore_errors=True)

    def test_pronto(self):
        cat = aspetta(lambda: self.s.json("GET", "/api/musica/catalogo")["brani"], 20)
        self.assertEqual(len(cat), 1, cat)
        self.assertIs(cat[0].get("clip"), True)
        j = self.s.json("GET", "/api/extra/clip/" + cat[0]["id"])
        self.assertEqual(j, {"stato": "pronto", "url": "clip/file/abcdefghijk.mp4", "mappa": self.mappa, "durata": 11.5})
        st, h, b = self.s.get("/api/extra/clip/file/abcdefghijk.mp4", intest={"Range": "bytes=0-9"})
        self.assertEqual((st, b, h["Content-Type"]), (206, bytes(range(10)), "video/mp4"))
        a = self.s.json("GET", "/api/extra/clip")
        self.assertEqual((a["attivo"], a["totale"], a["pronti"], a["auto"]), (True, 1, 1, False))

    def test_errori(self):
        self.assertIn("errore", self.s.json("GET", "/api/extra/clip/nessuno", codice=404))
        for n in ("../x.mp4", "corto.mp4", "abcdefghijk.mov", "zzzzzzzzzzz.mp4"):
            self.assertEqual(self.s.get("/api/extra/clip/file/" + quote(n))[0], 404, n)


class TestDispositiviTempi(unittest.TestCase):
    """Scadenze del telecomando con i tempi accorciati: comandi vecchi scartati, dispositivi spariti, limite dell'elenco."""

    @classmethod
    def setUpClass(cls):
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("musica_libreria_disp", SERVER)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)

    def test_scadenze(self):
        m = self.m
        m.COMANDO_SCADE, m.DISP_VIVO = 0.3, 0.6
        d = m.Dispositivi()
        d.stato("vecchio-0001", {"nome": "iPhone", "stato": {}})
        d.comando("vecchio-0001", {"azione": "play"})
        time.sleep(0.4)
        self.assertEqual(d.stato("vecchio-0001", {"nome": "iPhone", "stato": {}}), [])   # «riproduci» troppo vecchio
        time.sleep(0.7)
        self.assertEqual(d.elenco(), [])   # non si fa sentire: sparito
        with self.assertRaises(m.ErroreExtra) as e:
            d.comando("vecchio-0001", {"azione": "play"})
        self.assertEqual(e.exception.codice, 404)
        # l'attesa lunga lo tiene vivo
        d.stato("vivo-00000001", {"nome": "Mac", "stato": {}})
        for _ in range(3):
            d.attendi("vivo-00000001", 0.3)
        self.assertEqual([x["id"] for x in d.elenco()], ["vivo-00000001"])

    def test_limite(self):
        m = self.m
        m.DISP_VIVO, m.DISP_MAX = 60, 3
        d = m.Dispositivi()
        for i in range(5):
            d.stato("disp-%04d" % i, {"nome": "D%d" % i, "stato": {}})
            time.sleep(0.01)
        self.assertEqual([x["id"] for x in d.elenco()], ["disp-0004", "disp-0003", "disp-0002"])


class TestProxyInterno(unittest.TestCase):
    """Il proxy dentro un server qualsiasi (gestisci_extra), verso un sito «pubblico» finto su 127.0.0.1:
    rimandi, Content-Type, limite di 15 MB, codici d'errore e rimando verso un indirizzo vietato."""

    @classmethod
    def setUpClass(cls):
        sys.dont_write_bytecode = True   # niente __pycache__ accanto al server
        spec = importlib.util.spec_from_file_location("musica_libreria_prova", SERVER)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)
        cls.vietato_vero = staticmethod(cls.m._ip_vietato)
        cls.m._ip_vietato = lambda ip: ip == "127.0.0.2"   # qui 127.0.0.1 fa da sito pubblico

        class Sito(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/rimando":
                    self.send_response(302)
                    self.send_header("Location", "/feed?a=1")
                    self.end_headers()
                elif self.path == "/feed?a=1":
                    corpo = "<rss>è</rss>".encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/rss+xml; charset=utf-8")
                    self.send_header("Content-Length", str(len(corpo)))
                    self.end_headers()
                    self.wfile.write(corpo)
                elif self.path == "/vietato":
                    self.send_response(301)
                    self.send_header("Location", "http://127.0.0.2:%d/feed?a=1" % self.server.server_port)
                    self.end_headers()
                elif self.path == "/grande":   # 16 MB senza Content-Length
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.end_headers()
                    try:
                        for _ in range(64):
                            self.wfile.write(b"x" * 262144)
                    except OSError:
                        pass
                elif self.path == "/dichiarato":
                    self.send_response(200)
                    self.send_header("Content-Length", str(20 * 1024 * 1024))
                    self.end_headers()
                elif self.path == "/episodio":   # 20 MB di audio: passa a pezzi, oltre il limite delle pagine
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/mpeg")
                    self.send_header("Content-Length", str(80 * 262144))
                    self.end_headers()
                    try:
                        for _ in range(80):
                            self.wfile.write(b"a" * 262144)
                    except OSError:
                        pass
                elif self.path == "/ua":
                    corpo = (self.headers.get("User-Agent") or "").encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(corpo)))
                    self.end_headers()
                    self.wfile.write(corpo)
                else:
                    self.send_error(404)

            def log_message(self, *x):
                pass

        m = cls.m

        class Proxy(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if not m.gestisci_extra(self, "GET"):
                    self.send_error(404)

            def log_message(self, *x):
                pass

        cls.sito = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Sito)
        cls.proxy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
        for s in (cls.sito, cls.proxy):
            threading.Thread(target=s.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.m._ip_vietato = cls.vietato_vero
        for s in (cls.sito, cls.proxy):
            s.shutdown()
            s.server_close()

    def leggi(self, percorso):
        url = "http://127.0.0.1:%d%s" % (self.sito.server_port, percorso)
        return richiesta(self.proxy.server_port, "GET", "/api/extra/proxy?url=" + quote(url))

    def test_rimando_e_tipo(self):
        st, h, b = self.leggi("/rimando")
        self.assertEqual(st, 200, b)
        self.assertEqual(b.decode("utf-8"), "<rss>è</rss>")
        self.assertEqual(h["Content-Type"], "application/rss+xml; charset=utf-8")
        self.assertEqual(h["Cache-Control"], "no-store")
        self.assertTrue(h["X-Url-Finale"].endswith("/feed?a=1"))

    def test_user_agent_da_browser(self):
        st, h, b = self.leggi("/ua")
        self.assertEqual(st, 200)
        self.assertTrue(b.startswith(b"Mozilla/5.0"), b)

    def test_rimando_verso_indirizzo_vietato(self):
        st, h, b = self.leggi("/vietato")
        self.assertEqual(st, 403, b)

    def test_troppo_grande(self):
        self.assertEqual(self.leggi("/grande")[0], 413)
        self.assertEqual(self.leggi("/dichiarato")[0], 413)

    def test_audio_grande_passa(self):
        st, h, b = self.leggi("/episodio")
        self.assertEqual(st, 200)
        self.assertEqual(h["Content-Type"], "audio/mpeg")
        self.assertEqual(len(b), 80 * 262144)

    def test_errore_del_sito(self):
        st, h, b = self.leggi("/manca")
        self.assertEqual(st, 502)
        self.assertIn("404", json.loads(b.decode("utf-8"))["errore"])

    def test_indirizzi_vietati(self):
        v = self.vietato_vero
        for ip in ("127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.10", "169.254.169.254", "100.101.102.103",
                   "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fd00::1", "::ffff:192.168.0.1", "64:ff9b::7f00:1",
                   "2002:7f00:1::1", "255.255.255.255", "non un ip"):
            self.assertTrue(v(ip), ip)
        for ip in ("93.184.215.14", "8.8.8.8", "2606:4700:4700::1111", "::ffff:8.8.8.8"):
            self.assertFalse(v(ip), ip)


FUNNEL = {"Tailscale-Funnel-Request": "?1", "X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https",
          "X-Forwarded-Host": "mac.esempio.ts.net:10000", "Host": "mac.esempio.ts.net:10000"}


class TestAccesso(unittest.TestCase):
    """Da Internet (tailscale funnel) serve la password; dalla rete Tailscale e dal Mac no."""

    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="atlante-accesso-")
        cls.c = prepara_cartelle(cls.base)
        cls.s = Server(cls.base, cls.c, ["--pubblico", "https://mac.esempio.ts.net:10000"]).avvia()
        cls.file = os.path.join(cls.c["cache"], "accesso.json")
        # le persone si preparano da qui, sullo stesso file del server (che lo rilegge quando cambia)
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("musica_libreria_accesso", SERVER)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)
        cls.pers = cls.m.Persone(os.path.join(cls.c["cache"], "persone.json"))

    @classmethod
    def tearDownClass(cls):
        cls.s.ferma()
        shutil.rmtree(cls.base, ignore_errors=True)

    def imposta(self, pw):
        r = subprocess.run([sys.executable, "-I", SERVER, "--imposta-password", "--cache", self.c["cache"]],
                           input=(pw + "\n").encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        return r.returncode, r.stdout.decode("utf-8", "replace")

    def da_fuori(self, metodo, percorso, corpo=None, ip="203.0.113.7", biscotto=None, extra=None):
        h = dict(FUNNEL, **{"X-Forwarded-For": ip})
        if biscotto:
            h["Cookie"] = "altro=1; am_accesso=" + biscotto
        h.update(extra or {})
        return richiesta(self.s.porta, metodo, percorso, corpo, intest=h)

    def entra(self, pw, ip):
        corpo = ("nome=Atlante+Musica&password=" + quote(pw)).encode("utf-8")
        return self.da_fuori("POST", "/accesso", corpo, ip=ip,
                             extra={"Content-Type": "application/x-www-form-urlencoded", "Origin": "https://mac.esempio.ts.net:10000"})

    @staticmethod
    def biscotto(h):
        for v in h.get_all("Set-Cookie") or []:
            if v.startswith("am_accesso="):
                return v
        return None

    def test_1_senza_password_da_fuori_non_si_entra(self):
        if os.path.exists(self.file):
            with open(self.file) as f:
                self.assertNotIn("hash", json.load(f))
        st, h, b = self.da_fuori("GET", "/", extra={"Sec-Fetch-Mode": "navigate"})
        self.assertEqual((st, h["Location"]), (303, "accesso"))
        st, h, b = self.da_fuori("GET", "/accesso")
        self.assertEqual(st, 200)
        self.assertIn("non è ancora attivo", b.decode("utf-8"))
        self.assertNotIn('name="password"', b.decode("utf-8"))
        self.assertIn('name="codice"', b.decode("utf-8"))   # chi ha un invito entra anche senza password
        st, h, b = self.entra("qualsiasi-cosa", "203.0.113.1")
        self.assertEqual(st, 403)
        self.assertIsNone(self.biscotto(h))

    def test_2_dal_mac_e_da_tailscale_si_entra(self):
        for intest in ({},   # il Mac stesso (127.0.0.1, nessun proxy)
                       {"X-Forwarded-For": "100.127.102.35", "Tailscale-User-Login": "samuele@example.com"},
                       {"X-Forwarded-For": "100.101.1.2"},   # dispositivo della rete Tailscale senza identità
                       {"X-Forwarded-For": "fd7a:115c:a1e0::1"}):
            for p in ("/", "/api/extra/stato", "/x/prova.html"):
                st, h, b = richiesta(self.s.porta, "GET", p, intest=intest)
                self.assertEqual(st, 200, (p, intest))
            self.assertIn("am_accesso=", self.biscotto(h) or "", intest)
        # i biscotti dati in rete Tailscale valgono poi anche da Internet
        v = self.biscotto(h).split(";")[0].split("=", 1)[1]
        self.assertEqual(self.da_fuori("GET", "/api/extra/stato", biscotto=v)[0], 200)

    def test_3_da_fuori_senza_biscotto(self):
        st, h, b = self.da_fuori("GET", "/", extra={"Sec-Fetch-Mode": "navigate"})
        self.assertEqual((st, h["Location"]), (303, "accesso"))
        st, h, b = self.da_fuori("GET", "/x/prova.html")
        self.assertEqual((st, h["Location"]), (303, "../accesso"))
        for metodo, p in (("GET", "/api/extra/stato"), ("GET", "/api/musica/catalogo"), ("GET", "/musica/api/extra/stato"),
                          ("POST", "/api/extra/video"), ("DELETE", "/api/extra/audiolibro/x"), ("GET", "/x/kit.css"),
                          ("GET", "/sw.js"), ("HEAD", "/api/musica/file/1")):
            st, h, b = self.da_fuori(metodo, p, {} if metodo == "POST" else None)
            self.assertEqual(st, 401, (metodo, p))
            self.assertEqual(h["X-Atlante-Accesso"], "richiesto")
            if metodo != "HEAD":
                self.assertIs(json.loads(b.decode("utf-8"))["accesso"], True)
            self.assertIsNone(self.biscotto(h))
        # l'identità di Tailscale scritta da chi arriva da Internet non vale (tailscale serve la toglie, ma comunque)
        self.assertEqual(self.da_fuori("GET", "/api/extra/stato", extra={"Tailscale-User-Login": "x@y.z"})[0], 401)
        # un proxy davanti che non è tailscaled, o un indirizzo pubblico senza funnel: niente
        self.assertEqual(richiesta(self.s.porta, "GET", "/api/extra/stato", intest={"X-Forwarded-For": "8.8.8.8"})[0], 401)
        # per installare l'app: manifest e icone passano (qui non ci sono: 404, non 401)
        for p in ("/manifest.webmanifest", "/apple-touch-icon.png", "/icon-192.png", "/icon-maskable-512.png"):
            self.assertNotEqual(self.da_fuori("GET", p)[0], 401, p)
        for p in ("/api/musica/file/icon-1.png", "/api/extra/video/file/apple-touch-icon.png", "/x/manifest.webmanifest"):
            self.assertEqual(self.da_fuori("GET", p)[0], 401, p)

    def ha_password(self):
        if not os.path.exists(self.file):
            return False
        with open(self.file) as f:
            return "hash" in json.load(f)

    def dal_tailscale(self, metodo, percorso, corpo=None):
        h = {"X-Forwarded-For": "100.127.102.35", "Tailscale-User-Login": "samuele@example.com",
             "X-Forwarded-Proto": "https", "Host": "mac.esempio.ts.net:8448"}
        if corpo is not None:
            h.update({"Content-Type": "application/x-www-form-urlencoded", "Origin": "https://mac.esempio.ts.net:8448"})
        return richiesta(self.s.porta, metodo, percorso, corpo, intest=h)

    def test_3b_password_scelta_dalla_rete_tailscale(self):
        self.assertFalse(self.ha_password())
        # da Internet non si può scegliere
        st, h, b = self.da_fuori("POST", "/accesso", b"nuova=da+internet+123&ripeti=da+internet+123",
                                 extra={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(st, 403)
        self.assertFalse(self.ha_password())
        # dalla rete Tailscale: modulo per la password nuova, senza quella di adesso
        st, h, b = self.dal_tailscale("GET", "/accesso")
        t = b.decode("utf-8")
        self.assertEqual(st, 200)
        self.assertIn('autocomplete="new-password"', t)
        self.assertNotIn('name="password"', t)
        self.assertEqual(self.dal_tailscale("POST", "/accesso", b"nuova=corta&ripeti=corta")[0], 400)
        self.assertEqual(self.dal_tailscale("POST", "/accesso", b"nuova=abcdefgh1&ripeti=abcdefgh2")[0], 400)
        st, h, b = richiesta(self.s.porta, "POST", "/accesso", b"nuova=abcdefgh1&ripeti=abcdefgh1",
                             intest={"Content-Type": "application/x-www-form-urlencoded", "Origin": "https://altro.example"})
        self.assertEqual(st, 403)
        st, h, b = self.dal_tailscale("POST", "/accesso", b"nuova=scelta+su+iphone&ripeti=scelta+su+iphone")
        self.assertEqual(st, 200)
        self.assertIn("Password salvata", b.decode("utf-8"))
        self.assertIn("Secure", self.biscotto(h))
        self.assertEqual(os.stat(self.file).st_mode & 0o777, 0o600)
        self.assertEqual(self.entra("scelta su iphone", "203.0.113.30")[0], 303)
        # con la password già scelta: la pagina rimanda all'app; per cambiarla serve quella di adesso
        self.assertEqual(self.dal_tailscale("GET", "/accesso")[0], 303)
        st, h, b = self.dal_tailscale("GET", "/accesso?cambia")
        self.assertIn('name="password"', b.decode("utf-8"))
        self.assertEqual(self.dal_tailscale("POST", "/accesso", b"nuova=un+altra+ancora&ripeti=un+altra+ancora")[0], 401)
        self.assertEqual(self.dal_tailscale("POST", "/accesso", b"password=sbagliata&nuova=un+altra+ancora&ripeti=un+altra+ancora")[0], 401)
        st, h, b = self.dal_tailscale("POST", "/accesso", b"password=scelta+su+iphone&nuova=un+altra+ancora&ripeti=un+altra+ancora")
        self.assertEqual(st, 200)
        self.assertEqual(self.entra("scelta su iphone", "203.0.113.31")[0], 401)
        self.assertEqual(self.entra("un altra ancora", "203.0.113.31")[0], 303)

    def test_4_password(self):
        self.assertEqual(self.imposta("corta")[0], 1)
        cod, out = self.imposta("una password di prova")
        self.assertEqual(cod, 0, out)
        self.assertNotIn("una password di prova", out)
        self.assertEqual(os.stat(self.file).st_mode & 0o777, 0o600)
        st, h, b = self.da_fuori("GET", "/accesso")
        t = b.decode("utf-8")
        self.assertEqual(st, 200)
        self.assertIn('autocomplete="current-password"', t)
        self.assertIn('href="manifest.webmanifest"', t)
        self.assertEqual(h["Cache-Control"], "no-store")
        # sbagliata
        st, h, b = self.entra("sbagliata!!", "203.0.113.20")
        self.assertEqual(st, 401)
        self.assertIn("Password sbagliata", b.decode("utf-8"))
        self.assertIsNone(self.biscotto(h))
        # giusta: biscotto sicuro e ritorno all'app
        st, h, b = self.entra("una password di prova", "203.0.113.20")
        self.assertEqual((st, h["Location"]), (303, "./"))
        c = self.biscotto(h)
        for parte in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/", "Max-Age=34560000"):
            self.assertIn(parte, c)
        v = c.split(";")[0].split("=", 1)[1]
        for metodo, p in (("GET", "/"), ("GET", "/api/extra/stato"), ("GET", "/x/prova.html")):
            self.assertEqual(self.da_fuori(metodo, p, biscotto=v)[0], 200, p)
        # con il biscotto la pagina della password rimanda all'app
        self.assertEqual(self.da_fuori("GET", "/accesso", biscotto=v)[0], 303)
        # da un altro sito: no
        st, h, b = self.da_fuori("POST", "/accesso", b"password=una+password+di+prova", ip="203.0.113.21",
                                 extra={"Content-Type": "application/x-www-form-urlencoded", "Origin": "https://altro.example"})
        self.assertEqual(st, 403)
        # biscotti falsi, ritoccati o scaduti
        with open(self.file) as f:
            seg = bytes.fromhex(json.load(f)["segreto"])
        import hashlib, hmac
        def firma(scad):
            corpo = "%d.%s" % (scad, "0123456789abcdef")
            return corpo + "." + hmac.new(seg, corpo.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(self.da_fuori("GET", "/api/extra/stato", biscotto=firma(int(time.time()) + 3600))[0], 200)
        for falso in ("x", v[:-1] + ("0" if v[-1] != "0" else "1"), firma(int(time.time()) - 10),
                      firma(int(time.time()) + 900 * 86400), "1.2.3", "é.é.é"):
            self.assertEqual(self.da_fuori("GET", "/api/extra/stato", biscotto=falso)[0], 401, falso)
        # cambiando password il biscotto di prima non vale più
        self.assertEqual(self.imposta("un'altra password ancora")[0], 0)
        self.assertEqual(self.da_fuori("GET", "/api/extra/stato", biscotto=v)[0], 401)
        st, h, b = self.entra("un'altra password ancora", "203.0.113.22")
        self.assertEqual(st, 303)

    def test_5_troppi_tentativi(self):
        if self.imposta("password per i tentativi")[0]:
            self.fail("password non impostata")
        for i in range(5):
            self.assertEqual(self.entra("sbagliata %d" % i, "198.51.100.9")[0], 401)
        st, h, b = self.entra("password per i tentativi", "198.51.100.9")   # giusta, ma deve aspettare
        self.assertEqual(st, 429)
        self.assertGreater(int(h["Retry-After"]), 0)
        self.assertIsNone(self.biscotto(h))
        self.assertEqual(self.entra("password per i tentativi", "198.51.100.10")[0], 303)   # un altro indirizzo sì

    # ----- invitati: link d'invito, codice per l'app sulla Home, revoca -----

    ORIGINE = {"Content-Type": "application/x-www-form-urlencoded", "Origin": "https://mac.esempio.ts.net:10000"}
    IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15"

    def invitato(self, nome="Anna", ip="203.0.113.60"):
        """Persona invitata che entra dal suo link: (persona, biscotto, pagina dopo «Entra»)."""
        a = self.pers.crea(nome)
        codice, _ = self.pers.invito(a["id"])
        st, h, b = self.da_fuori("POST", "/accesso/invito/" + codice, b"", ip=ip,
                                 extra=dict(self.ORIGINE, **{"User-Agent": self.IPHONE}))
        self.assertEqual(st, 200, b[:300])
        v = (self.biscotto(h) or "").split(";")[0].split("=", 1)[-1]
        self.assertTrue(v.startswith("o."), self.biscotto(h))
        return a["id"], v, b.decode("utf-8")

    def test_6_invito_da_fuori(self):
        a = self.pers.crea("Anna")
        codice, _ = self.pers.invito(a["id"])
        for _ in range(3):   # WhatsApp e Messaggi aprono il link per l'anteprima: non deve consumarlo
            st, h, b = self.da_fuori("GET", "/accesso/invito/" + codice, extra={"Sec-Fetch-Mode": "navigate"})
            self.assertEqual(st, 200)
            self.assertIn("Ciao Anna", b.decode("utf-8"))
            self.assertIn('<form method="post"', b.decode("utf-8"))
            self.assertIsNone(self.biscotto(h))
        # Samuele che apre il link per provarlo non diventa Anna e non lo consuma
        st, h, b = self.dal_tailscale("GET", "/accesso/invito/" + codice)
        self.assertIn("già dentro", b.decode("utf-8"))
        self.assertEqual(self.dal_tailscale("POST", "/accesso/invito/" + codice, b"")[0], 409)
        self.assertEqual(self.da_fuori("POST", "/accesso/invito/" + codice, b"",
                                       extra={"Origin": "https://altro.example"})[0], 403)
        st, h, b = self.da_fuori("POST", "/accesso/invito/" + codice, b"", ip="203.0.113.61",
                                 extra=dict(self.ORIGINE, **{"User-Agent": self.IPHONE}))
        t = b.decode("utf-8")
        self.assertEqual(st, 200)
        self.assertIn("Sei dentro", t)
        self.assertRegex(t, r'<b class="codice">[ACDEFGHJKMNPQRTUVWXY3479]{6}</b>')
        v = self.biscotto(h).split(";")[0].split("=", 1)[1]
        self.assertTrue(v.startswith("o."))
        self.assertIn("HttpOnly", self.biscotto(h))
        # usato: da un altro telefono non vale più (dallo stesso, subito dopo, sì: vedi test_9g)
        st, h, b = self.da_fuori("POST", "/accesso/invito/" + codice, b"", ip="203.0.113.62", extra=self.ORIGINE)
        self.assertEqual(st, 410)
        self.assertIn("non vale più", b.decode("utf-8"))
        self.assertEqual(self.da_fuori("GET", "/api/musica/catalogo", biscotto=v)[0], 200)
        self.assertEqual(self.da_fuori("GET", "/accesso/invito/nonesiste12345678")[0], 410)
        self.assertEqual([y["tipo"] for x in self.pers.elenco()["persone"] if x["id"] == a["id"] for y in x["dispositivi"]],
                         ["iphone"])

    def test_7_collegamento_app_home(self):
        pid, v, t = self.invitato("Bruno", ip="203.0.113.70")
        codice = re.search(r'<b class="codice">(\w{6})</b>', t).group(1)
        corpo = ("codice=" + codice[:3].lower() + "-" + codice[3:]).encode("utf-8")
        st, h, b = self.da_fuori("POST", "/accesso", corpo, ip="203.0.113.71", extra=self.ORIGINE)
        self.assertEqual((st, h["Location"]), (303, "./"))
        v2 = self.biscotto(h).split(";")[0].split("=", 1)[1]
        self.assertTrue(v2.startswith("o.") and v2 != v)
        self.assertEqual(self.da_fuori("GET", "/api/musica/catalogo", biscotto=v2)[0], 200)
        st, h, b = self.da_fuori("POST", "/accesso", corpo, ip="203.0.113.72", extra=self.ORIGINE)
        self.assertEqual(st, 401)
        self.assertIn("Codice sbagliato o scaduto", b.decode("utf-8"))
        for i in range(5):
            self.assertEqual(self.da_fuori("POST", "/accesso", b"codice=ZZZZZ%d" % i, ip="198.51.100.77",
                                           extra=self.ORIGINE)[0], 401)
        st, h, b = self.da_fuori("POST", "/accesso", b"codice=" + self.pers.collegamento(pid)[0].encode(),
                                 ip="198.51.100.77", extra=self.ORIGINE)
        self.assertEqual(st, 429)
        self.assertGreater(int(h["Retry-After"]), 0)

    def test_8_password_cambiata_invitati_restano(self):
        self.assertEqual(self.imposta("password dell'ottava prova")[0], 0)
        st, h, b = self.entra("password dell'ottava prova", "203.0.113.80")
        p = self.biscotto(h).split(";")[0].split("=", 1)[1]
        pid, o, t = self.invitato("Carla", ip="203.0.113.81")
        self.assertEqual(self.imposta("password dell'ottava prova, nuova")[0], 0)
        self.assertEqual(self.da_fuori("GET", "/api/musica/catalogo", biscotto=p)[0], 401)
        self.assertEqual(self.da_fuori("GET", "/api/musica/catalogo", biscotto=o)[0], 200)

    def test_9_revoca_immediata(self):
        pid, v, t = self.invitato("Dario", ip="203.0.113.90")
        self.assertEqual(self.da_fuori("GET", "/api/musica/catalogo", biscotto=v)[0], 200)
        did = v.split(".")[2]
        self.assertTrue(self.pers.revoca_dispositivo(did))
        st, h, b = self.da_fuori("GET", "/api/musica/catalogo", biscotto=v)
        self.assertEqual((st, h["X-Atlante-Accesso"]), (401, "richiesto"))

    def test_9b_nome_nella_pagina(self):
        a = self.pers.crea("<script>x</script>")
        st, h, b = self.da_fuori("GET", "/accesso/invito/" + self.pers.invito(a["id"])[0])
        self.assertIn("&lt;script&gt;x&lt;/script&gt;", b.decode("utf-8"))
        self.assertNotIn("<script>x", b.decode("utf-8"))

    def test_9d_permessi_invitato(self):
        pid, v, t = self.invitato("Elena", ip="203.0.113.100")
        for f in ("index.html", "sw.js", "x/kit.js", "x/kit.css", "x/inviti.html", "x/benessere.html"):
            with open(os.path.join(self.c["app"], f), "w", encoding="utf-8") as fh:
                fh.write("ciao")
        permesse = [("GET", "/"), ("GET", "/index.html"), ("GET", "/sw.js"), ("GET", "/x/kit.js"), ("GET", "/x/kit.css"),
                    ("GET", "/x/inviti.html"), ("GET", "/api/musica/catalogo"), ("HEAD", "/api/musica/file/1"),
                    ("GET", "/api/musica/copertina/x.jpg"), ("GET", "/api/musica/pezzo/1?da=0&durata=30"),
                    ("GET", "/api/extra/clip"), ("GET", "/api/extra/clip/file/abc.mp4"), ("GET", "/api/extra/clip/abc"),
                    ("GET", "/api/extra/dispositivi"), ("GET", "/api/extra/io"), ("GET", "/api/extra/inviti"),
                    ("POST", "/api/extra/inviti"), ("GET", "/musica/api/musica/catalogo")]
        for metodo, p in permesse:
            st, h, b = self.da_fuori(metodo, p, {} if metodo == "POST" else None, biscotto=v, extra=self.ORIGINE)
            self.assertNotEqual(st, 403, (metodo, p, b[:200]))
            self.assertNotEqual(st, 401, (metodo, p))
        vietate = [("GET", "/api/musica/aggiorna"), ("GET", "/api/extra/proxy?url=https://example.com/"),
                   ("GET", "/api/extra/video"), ("POST", "/api/extra/video"), ("GET", "/api/extra/musica/cerca?q=x"),
                   ("POST", "/api/extra/musica"), ("GET", "/api/extra/voci"), ("POST", "/api/extra/audiolibro"),
                   ("DELETE", "/api/extra/audiolibro/x"), ("POST", "/api/extra/ia"), ("GET", "/api/extra/dispositivo"),
                   ("GET", "/api/extra/stato"), ("GET", "/api/extra/gestione/persone"), ("GET", "/x/benessere.html"),
                   ("GET", "/x/"), ("GET", "/x/studio.html"), ("GET", "/musica/api/extra/stato")]
        for metodo, p in vietate:
            st, h, b = self.da_fuori(metodo, p, {} if metodo == "POST" else None, biscotto=v, extra=self.ORIGINE)
            self.assertEqual(st, 403, (metodo, p))
            self.assertEqual(json.loads(b.decode("utf-8"))["errore"], "Non disponibile per gli invitati", p)
        st, h, b = self.da_fuori("GET", "/api/extra/io", biscotto=v)
        self.assertEqual(json.loads(b.decode("utf-8")), {"ruolo": "invitato", "persona": {"id": pid, "nome": "Elena"}})
        st, h, b = richiesta(self.s.porta, "GET", "/api/extra/io")
        io = json.loads(b.decode("utf-8"))
        self.assertEqual((io["ruolo"], io["persona"], sorted(io["nuove"])), ("proprietario", None, ["idee", "richieste"]))

    def test_9e_dispositivi_per_persona(self):
        pid, v, t = self.invitato("Franca", ip="203.0.113.110")

        def anna(metodo, p, corpo=None):
            st, h, b = self.da_fuori(metodo, p, corpo, biscotto=v, extra=self.ORIGINE)
            return st, (json.loads(b.decode("utf-8")) if b else None)

        def samuele(metodo, p, corpo=None):
            st, h, b = richiesta(self.s.porta, metodo, p, corpo)
            return st, (json.loads(b.decode("utf-8")) if b else None)

        self.assertEqual(samuele("POST", "/api/extra/dispositivi/samuele-iphone-1/stato", {"nome": "iPhone", "stato": {}})[0], 200)
        self.assertEqual(anna("POST", "/api/extra/dispositivi/franca-telefono-1/stato", {"nome": "Telefono", "stato": {}})[0], 200)
        self.assertEqual([x["id"] for x in anna("GET", "/api/extra/dispositivi")[1]["dispositivi"]], ["franca-telefono-1"])
        self.assertEqual([x["id"] for x in samuele("GET", "/api/extra/dispositivi")[1]["dispositivi"]], ["samuele-iphone-1"])
        # l'id di un dispositivo di Samuele non serve a niente a un'altra persona
        self.assertEqual(anna("POST", "/api/extra/dispositivi/samuele-iphone-1/comando", {"azione": "pause"})[0], 404)
        self.assertEqual(anna("POST", "/api/extra/dispositivi/samuele-iphone-1/stato", {"nome": "x", "stato": {}})[0], 404)
        self.assertEqual(anna("GET", "/api/extra/dispositivi/samuele-iphone-1/comandi?attesa=0")[0], 404)
        self.assertEqual(samuele("GET", "/api/extra/dispositivi/samuele-iphone-1/comandi?attesa=0")[1], {"comandi": []})
        # e tanti dispositivi finti di un'altra persona non fanno uscire quelli di Samuele
        for i in range(40):
            anna("POST", "/api/extra/dispositivi/franca-finto-%04d/stato" % i, {"nome": "F", "stato": {}})
        self.assertEqual([x["id"] for x in samuele("GET", "/api/extra/dispositivi")[1]["dispositivi"]], ["samuele-iphone-1"])
        anna("POST", "/api/extra/dispositivi/franca-telefono-1/stato", {"nome": "Telefono", "stato": {}})
        self.assertEqual(anna("POST", "/api/extra/dispositivi/franca-telefono-1/comando", {"azione": "pause"})[0], 200)
        self.assertEqual(anna("GET", "/api/extra/dispositivi/franca-telefono-1/comandi?attesa=0")[1],
                         {"comandi": [{"azione": "pause"}]})

    def test_9f_gestione_persone(self):
        def samuele(metodo, p, corpo=None, codice=200):
            return self.s.json(metodo, p, corpo, codice)

        def biscotto_da(link, ip):
            st, h, b = self.da_fuori("POST", urlsplit(link).path, b"", ip=ip, extra=dict(self.ORIGINE, **{"User-Agent": self.IPHONE}))
            self.assertEqual(st, 200, b[:200])
            return self.biscotto(h).split(";")[0].split("=", 1)[1]

        r = samuele("POST", "/api/extra/gestione/persone", {"nome": "Gina"})
        gina, link = r["persona"]["id"], r["invito"]["link"]
        self.assertTrue(link.startswith("https://mac.esempio.ts.net:10000/accesso/invito/"), link)
        v = biscotto_da(link, "203.0.113.120")
        el = {x["id"]: x for x in samuele("GET", "/api/extra/gestione/persone")["persone"]}
        self.assertEqual([d["tipo"] for d in el[gina]["dispositivi"]], ["iphone"])
        nuovo = samuele("POST", "/api/extra/gestione/persone/%s/invito" % gina)["invito"]["link"]
        self.assertNotEqual(nuovo, link)
        # le rotte della Gestione: solo Samuele, e solo dalla sua app
        for metodo, rotta in (("GET", "persone"), ("GET", "richieste"), ("POST", "persone"), ("DELETE", "persone/" + gina)):
            st, h, b = self.da_fuori(metodo, "/api/extra/gestione/" + rotta, {} if metodo == "POST" else None, biscotto=v,
                                     extra=self.ORIGINE)
            self.assertEqual(st, 403, rotta)
        st, h, b = richiesta(self.s.porta, "POST", "/api/extra/gestione/persone", {"nome": "X"},
                             intest={"Origin": "https://altro.example"})
        self.assertEqual(st, 403)
        # Gina chiede un invito per Marco; Samuele lo consente; Gina rivede il link da mandare
        st, h, b = self.da_fuori("POST", "/api/extra/inviti", {"nome": "Marco"}, biscotto=v, extra={"Origin": self.ORIGINE["Origin"]})
        self.assertEqual(st, 200, b[:200])
        rid = json.loads(b.decode("utf-8"))["richiesta"]["id"]
        rq = [x for x in samuele("GET", "/api/extra/gestione/richieste")["richieste"] if x["id"] == rid][0]
        self.assertEqual((rq["nome"], rq["da"], rq["da_nome"], rq["stato"]), ("Marco", gina, "Gina", "attesa"))
        marco = samuele("POST", "/api/extra/gestione/richieste/%s/consenti" % rid)
        st, h, b = self.da_fuori("GET", "/api/extra/inviti", biscotto=v)
        suoi = json.loads(b.decode("utf-8"))["inviti"]
        self.assertEqual(suoi[0]["link"], marco["invito"]["link"])
        self.assertNotIn("codice", suoi[0])
        el = {x["id"]: x for x in samuele("GET", "/api/extra/gestione/persone")["persone"]}
        self.assertEqual((el[marco["persona"]["id"]]["da"], el[marco["persona"]["id"]]["da_nome"]), (gina, "Gina"))
        rid2 = json.loads(self.da_fuori("POST", "/api/extra/inviti", {"nome": "Nora"}, biscotto=v,
                                        extra={"Origin": self.ORIGINE["Origin"]})[2].decode("utf-8"))["richiesta"]["id"]
        self.assertEqual(samuele("POST", "/api/extra/gestione/richieste/%s/rifiuta" % rid2), {"ok": True})
        samuele("POST", "/api/extra/gestione/richieste/%s/rifiuta" % rid2, codice=404)
        # revoche: un dispositivo, poi la persona
        did = v.split(".")[2]
        self.assertEqual(samuele("DELETE", "/api/extra/gestione/dispositivi/" + did), {"ok": True})
        self.assertEqual(self.da_fuori("GET", "/api/musica/catalogo", biscotto=v)[0], 401)
        self.assertEqual(samuele("DELETE", "/api/extra/gestione/persone/" + gina), {"ok": True})
        self.assertNotIn(gina, [x["id"] for x in samuele("GET", "/api/extra/gestione/persone")["persone"]])
        samuele("DELETE", "/api/extra/gestione/persone/" + gina, codice=404)
        # Samuele entrato con la password: i suoi dispositivi si vedono, ma non revoca quello che sta usando
        self.assertEqual(self.imposta("password della prova gestione")[0], 0)
        p = self.biscotto(self.entra("password della prova gestione", "203.0.113.121")[1]).split(";")[0].split("=", 1)[1]
        chiave = "p:" + p.split(".")[1]
        self.assertEqual(self.da_fuori("GET", "/api/extra/io", biscotto=p)[0], 200)
        self.assertIn(chiave, [x["id"] for x in samuele("GET", "/api/extra/gestione/persone")["tuoi"]])
        st, h, b = self.da_fuori("DELETE", "/api/extra/gestione/dispositivi/" + chiave, biscotto=p, extra={"Origin": self.ORIGINE["Origin"]})
        self.assertEqual(st, 409)
        self.assertIn("stai usando", json.loads(b.decode("utf-8"))["errore"])
        self.assertEqual(samuele("DELETE", "/api/extra/gestione/dispositivi/" + chiave), {"ok": True})
        self.assertEqual(self.da_fuori("GET", "/api/extra/io", biscotto=p)[0], 401)

    def test_9g_doppio_tocco_su_entra(self):
        a = self.pers.crea("Ivo")
        codice, _ = self.pers.invito(a["id"])
        intest = dict(self.ORIGINE, **{"User-Agent": self.IPHONE})
        v = []
        for _ in range(2):   # due tocchi su «Entra» dallo stesso telefono: dentro tutte e due le volte, stesso dispositivo
            st, h, b = self.da_fuori("POST", "/accesso/invito/" + codice, b"", ip="203.0.113.130", extra=intest)
            self.assertEqual(st, 200, b[:200])
            v.append(self.biscotto(h).split(";")[0].split("=", 1)[1])
        self.assertEqual(v[0].split(".")[2], v[1].split(".")[2])
        self.assertIn('onsubmit=', self.da_fuori("GET", "/accesso/invito/" + self.pers.invito(a["id"])[0])[2].decode("utf-8"))
        # più tardi, con la pagina rimandata (indietro e di nuovo «Entra»): chi è già dentro lo vede, e non conta come errore
        for _ in range(6):
            st, h, b = self.da_fuori("POST", "/accesso/invito/" + codice, b"", ip="203.0.113.131", biscotto=v[1], extra=intest)
            self.assertEqual(st, 200)
            self.assertIn("già dentro", b.decode("utf-8"))
        st, h, b = self.da_fuori("GET", "/accesso/invito/" + codice, biscotto=v[1])
        self.assertIn("già dentro", b.decode("utf-8"))
        nuovo = self.pers.invito(a["id"])[0]
        self.assertEqual(self.da_fuori("POST", "/accesso/invito/" + nuovo, b"", ip="203.0.113.131", extra=intest)[0], 200)

    def test_9h_gestione_riconosce_i_dispositivi(self):
        self.assertEqual(self.imposta("password della prova dispositivi")[0], 0)
        st, h, b = self.da_fuori("POST", "/accesso", b"nome=Atlante+Musica&password=password+della+prova+dispositivi",
                                 ip="203.0.113.140", extra=dict(self.ORIGINE, **{"User-Agent": self.IPHONE + " Version/18.0 Mobile/15E148 Safari/604.1"}))
        self.assertEqual(st, 303)
        p = self.biscotto(h).split(";")[0].split("=", 1)[1]
        chiave = "p:" + p.split(".")[1]
        st, h, b = self.da_fuori("GET", "/api/extra/gestione/persone", biscotto=p,
                                 extra={"User-Agent": self.IPHONE + " Version/18.0 Mobile/15E148 Safari/604.1"})
        tuoi = {x["id"]: x for x in json.loads(b.decode("utf-8"))["tuoi"]}
        self.assertEqual((tuoi[chiave]["via"], tuoi[chiave]["browser"], tuoi[chiave]["tipo"], tuoi[chiave].get("attuale")),
                         ("internet", "Safari", "iphone", True))
        self.assertTrue(all(not x.get("attuale") for k, x in tuoi.items() if k != chiave))

    def test_9i_numeri_nuovi_nella_gestione(self):
        pid, v, t = self.invitato("Lia", ip="203.0.113.150")
        st, h, b = self.da_fuori("POST", "/api/extra/inviti", {"nome": "Amico di Lia"}, biscotto=v, extra={"Origin": self.ORIGINE["Origin"]})
        self.assertEqual(st, 200)
        io = json.loads(richiesta(self.s.porta, "GET", "/api/extra/io")[2].decode("utf-8"))
        self.assertGreaterEqual(io["nuove"]["richieste"], 1)
        self.assertNotIn("nuove", json.loads(self.da_fuori("GET", "/api/extra/io", biscotto=v)[2].decode("utf-8")))

    def test_9j_pagine_non_incorniciabili(self):
        for p in ("/", "/index.html", "/x/inviti.html", "/accesso"):
            st, h, b = self.da_fuori("GET", p)
            self.assertEqual(h["X-Frame-Options"], "SAMEORIGIN", p)
            self.assertIn("frame-ancestors 'self'", h["Content-Security-Policy"], p)


class FintaRichiesta:
    """Quanto basta di BaseHTTPRequestHandler per controlla_accesso (indirizzi che dal Mac non si possono fingere)."""

    def __init__(self, ip, intest=None, percorso="/"):
        self.client_address, self.headers, self.path, self.command = (ip, 50000), dict(intest or {}), percorso, "GET"
        self.connection = None


class TestRuoli(unittest.TestCase):
    """Chi è una richiesta: Samuele (Mac, Tailscale, password), casa (rete di casa) o invitato."""

    @classmethod
    def setUpClass(cls):
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("musica_libreria_ruoli", SERVER)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)
        cls.base = tempfile.mkdtemp(prefix="atlante-ruoli-")
        cls.m.ACC = cls.m.Accesso(os.path.join(cls.base, "accesso.json"))
        cls.m.PERS = cls.m.Persone(os.path.join(cls.base, "persone.json"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def ruolo(self, ip, intest=None):
        h = FintaRichiesta(ip, intest)
        self.assertFalse(self.m.controlla_accesso(h))
        return h._chi["ruolo"], h._biscotto

    def test_casa_senza_biscotto(self):
        ruolo, biscotto = self.ruolo("192.168.1.20")
        self.assertEqual(ruolo, "casa")
        self.assertIsNone(biscotto)   # dalla rete di casa si ascolta, ma senza diventare Samuele

    def test_tailscale_vince_sul_biscotto_invitato(self):
        a = self.m.PERS.crea("Ospite")
        did = self.m.PERS.usa_invito(self.m.PERS.invito(a["id"])[0], "iphone")
        intest = {"X-Forwarded-For": "100.127.102.35", "Tailscale-User-Login": "samuele@example.com",
                  "Cookie": "am_accesso=" + self.m.PERS.biscotto(did)}
        ruolo, biscotto = self.ruolo("127.0.0.1", intest)
        self.assertEqual(ruolo, "proprietario")
        self.assertFalse(biscotto.split("=", 1)[1].startswith("o."))   # il telefono torna di Samuele

    def test_mac_e_tailscale(self):
        self.assertEqual(self.ruolo("127.0.0.1")[0], "proprietario")
        self.assertIsNotNone(self.ruolo("127.0.0.1")[1])
        ts = {"X-Forwarded-For": "100.127.102.35", "Tailscale-User-Login": "samuele@example.com"}
        self.assertEqual(self.ruolo("127.0.0.1", ts)[0], "proprietario")
        with unittest.mock.patch.object(self.m, "PROPRIETARI", {"samuele@example.com"}):
            self.assertEqual(self.ruolo("127.0.0.1", ts)[0], "proprietario")
            altro = dict(ts, **{"Tailscale-User-Login": "anna@example.com"})
            self.assertEqual(self.ruolo("127.0.0.1", altro), ("casa", None))


class TestPersone(unittest.TestCase):
    """Archivio delle persone invitate: inviti usabili una volta, codici per l'app sulla Home, revoche, richieste."""

    @classmethod
    def setUpClass(cls):
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("musica_libreria_persone", SERVER)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="atlante-persone-")
        self.file = os.path.join(self.base, "cache", "persone.json")
        self.p = self.m.Persone(self.file)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def piu_tardi(self, secondi):
        """time.time spostato in avanti (il modulo usa lo stesso time di queste prove)."""
        return unittest.mock.patch.object(self.m.time, "time", return_value=time.time() + secondi)

    def entrata(self, nome="Anna"):
        a = self.p.crea(nome)
        codice, _ = self.p.invito(a["id"])
        return a["id"], self.p.usa_invito(codice, "iphone")

    def test_invito_una_volta(self):
        a = self.p.crea("Anna")
        codice, scade = self.p.invito(a["id"])
        self.assertAlmostEqual(scade, time.time() + 7 * 86400, delta=5)
        atteso = {"persona": a["id"], "nome": "Anna"}
        self.assertEqual(self.p.leggi_invito(codice), atteso)   # un'anteprima del link non lo consuma
        self.assertEqual(self.p.leggi_invito(codice), atteso)
        did = self.p.usa_invito(codice, "iphone")
        self.assertRegex(did, r"^[0-9a-f]{16}$")
        self.assertIsNone(self.p.usa_invito(codice, "iphone"))
        self.assertIsNone(self.p.leggi_invito(codice))
        self.assertEqual(self.p.chi(self.p.biscotto(did))[0:2], (a["id"], did))
        with open(self.file, encoding="utf-8") as f:
            self.assertNotIn(codice, f.read())   # del codice resta solo l'impronta

    def test_invito_scaduto(self):
        a = self.p.crea("Anna")
        codice, _ = self.p.invito(a["id"])
        with self.piu_tardi(7 * 86400 + 1):
            self.assertIsNone(self.p.leggi_invito(codice))
            self.assertIsNone(self.p.usa_invito(codice, "iphone"))
        with self.assertRaises(self.m.ErroreExtra) as e:
            self.p.invito("nessuno")
        self.assertEqual(e.exception.codice, 404)

    def test_collegamento(self):
        pid, did = self.entrata()
        codice, scade = self.p.collegamento(pid)
        self.assertEqual(len(codice), 6)
        self.assertTrue(set(codice) <= set("ACDEFGHJKMNPQRTUVWXY3479"), codice)
        self.assertAlmostEqual(scade, time.time() + 15 * 60, delta=5)
        d2 = self.p.usa_collegamento(codice.lower()[:3] + "-" + codice.lower()[3:], "iphone")
        self.assertIsNotNone(d2)
        self.assertNotEqual(d2, did)
        self.assertEqual(self.p.chi(self.p.biscotto(d2))[0], pid)
        self.assertIsNone(self.p.usa_collegamento(codice, "iphone"))   # una volta sola
        codice, _ = self.p.collegamento(pid)
        with self.piu_tardi(15 * 60 + 1):
            self.assertIsNone(self.p.usa_collegamento(codice, "iphone"))

    def test_revoca(self):
        pid, d1 = self.entrata()
        d2 = self.p.usa_collegamento(self.p.collegamento(pid)[0], "mac")
        b1, b2 = self.p.biscotto(d1), self.p.biscotto(d2)
        self.assertTrue(self.p.revoca_dispositivo(d1))
        self.assertIsNone(self.p.chi(b1))
        self.assertEqual(self.p.chi(b2)[0:2], (pid, d2))
        falso = b2[:-1] + ("0" if b2[-1] != "0" else "1")
        self.assertIsNone(self.p.chi(falso))
        self.assertTrue(self.p.revoca_persona(pid))
        self.assertIsNone(self.p.chi(b2))
        self.assertIsNone(self.p.chi("o.1.2.3"))
        # i dispositivi di Samuele (biscotto della password): si vedono e si revocano con la loro chiave «p:…»
        self.p.visto("p:00112233aabbccdd", "mac")
        self.assertEqual([x["id"] for x in self.p.elenco()["tuoi"]], ["p:00112233aabbccdd"])
        self.assertFalse(self.p.revocato("p:00112233aabbccdd"))
        self.assertTrue(self.p.revoca_dispositivo("p:00112233aabbccdd"))
        self.assertTrue(self.p.revocato("p:00112233aabbccdd"))
        self.assertEqual(self.p.elenco()["tuoi"], [])

    def test_file_e_limiti(self):
        pid, did = self.entrata("  <b>Anna</b>  " * 10)
        self.assertEqual(os.stat(self.file).st_mode & 0o777, 0o600)
        nome = self.m.Persone(self.file).nome(pid)   # un altro processo legge le stesse persone
        self.assertTrue(nome.startswith("<b>Anna</b> <b>Anna</b>"), nome)
        self.assertLessEqual(len(nome), 40)
        with self.assertRaises(self.m.ErroreExtra) as e:
            self.p.crea("   ")
        self.assertEqual(e.exception.codice, 400)
        # al più 10 dispositivi per persona: l'undicesimo fa uscire quello visto meno di recente
        biscotti = [self.p.biscotto(did)]
        for i in range(10):
            with self.piu_tardi(i + 1):
                biscotti.append(self.p.biscotto(self.p.usa_collegamento(self.p.collegamento(pid)[0], "iphone")))
        self.assertIsNone(self.p.chi(biscotti[0]))
        self.assertTrue(all(self.p.chi(b) for b in biscotti[1:]))
        for i in range(199):
            self.p.crea("Persona %d" % i)
        with self.assertRaises(self.m.ErroreExtra) as e:
            self.p.crea("Una di troppo")
        self.assertEqual(e.exception.codice, 409)

    def test_richieste(self):
        anna, _ = self.entrata()
        r = self.p.richiedi(anna, "Marco")
        self.assertEqual((r["nome"], r["da"], r["stato"]), ("Marco", anna, "attesa"))
        self.assertNotIn("codice", self.p.richieste_di(anna)[0])
        self.assertEqual([x["id"] for x in self.p.richieste()], [r["id"]])
        marco, codice, scade = self.p.consenti(r["id"])
        self.assertEqual((marco["nome"], marco["da"]), ("Marco", anna))
        self.assertEqual(self.p.richieste_di(anna)[0]["codice"], codice)   # Anna rivede il link da mandare
        self.assertEqual([x.get("da") for x in self.p.elenco()["persone"] if x["id"] == marco["id"]], [anna])
        self.assertIsNotNone(self.p.usa_invito(codice, "iphone"))
        self.assertNotIn("codice", self.p.richieste_di(anna)[0])
        with open(self.file, encoding="utf-8") as f:
            self.assertNotIn(codice, f.read())
        attesa = [self.p.richiedi(anna, "Amico %d" % i) for i in range(5)]
        with self.assertRaises(self.m.ErroreExtra) as e:
            self.p.richiedi(anna, "Uno di troppo")
        self.assertEqual(e.exception.codice, 409)
        prima = len(self.p.elenco()["persone"])
        self.assertTrue(self.p.rifiuta(attesa[0]["id"]))
        self.assertEqual([x["stato"] for x in self.p.richieste_di(anna) if x["id"] == attesa[0]["id"]], ["rifiutata"])
        self.assertEqual(len(self.p.elenco()["persone"]), prima)

    def test_doppio_tocco(self):
        a = self.p.crea("Anna")
        codice, _ = self.p.invito(a["id"])
        d1 = self.p.usa_invito(codice, "iphone", "203.0.113.5")
        self.assertIsNotNone(d1)
        # il secondo tocco su «Entra» (o la pagina ricaricata) poco dopo, dallo stesso indirizzo: lo stesso dispositivo
        self.assertEqual(self.p.usa_invito(codice, "iphone", "203.0.113.5"), d1)
        self.assertIsNone(self.p.usa_invito(codice, "iphone", "198.51.100.1"))   # da un altro indirizzo no
        self.assertEqual(self.p.persona_di(d1), a["id"])
        with self.piu_tardi(121):
            self.assertIsNone(self.p.usa_invito(codice, "iphone", "203.0.113.5"))
        self.assertEqual(len([x for x in self.p.elenco()["persone"][0]["dispositivi"]]), 1)

    def test_file_rotto(self):
        pid, did = self.entrata()
        b = self.p.biscotto(did)
        with open(self.file, "w", encoding="utf-8") as f:
            f.write("{rotto")
        self.assertEqual(self.p.chi(b)[0], pid)   # in memoria resta quello di prima
        with self.assertRaises(self.m.ErroreExtra) as e:
            self.p.crea("Bruno")
        self.assertEqual(e.exception.codice, 503)
        with self.piu_tardi(self.m.VISTO_OGNI + 1):
            self.p.visto(did, "iphone")   # non solleva: l'accesso della richiesta non cade
        with open(self.file, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{rotto")   # il file rotto non viene sovrascritto con un archivio vuoto
        nuovo = self.m.Persone(self.file)   # un processo che parte con il file rotto non lo svuota
        with self.assertRaises(self.m.ErroreExtra):
            nuovo.crea("Carla")
        with open(self.file, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{rotto")

    def test_dispositivi_riconoscibili(self):
        pid, did = self.entrata()
        with self.piu_tardi(self.m.VISTO_OGNI + 1):
            self.p.visto("p:00112233aabbccdd", "iphone", "tailscale", "Safari")
            self.p.visto(did, "android", "internet", "Chrome")
        tuo = self.p.elenco()["tuoi"][0]
        self.assertEqual((tuo["id"], tuo["tipo"], tuo["via"], tuo["browser"]), ("p:00112233aabbccdd", "iphone", "tailscale", "Safari"))
        self.assertGreater(tuo["creato"], 0)
        d = self.p.elenco()["persone"][0]["dispositivi"][0]
        self.assertEqual((d["via"], d["browser"]), ("internet", "Chrome"))
        self.assertGreater(d["creato"], 0)
        # e restano dopo la scrittura su disco, letti da un altro processo
        self.assertEqual(self.m.Persone(self.file).elenco()["tuoi"][0]["browser"], "Safari")

    def test_tipo_da_agente(self):
        for ua, tipo in (("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15", "iphone"),
                         ("Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X)", "ipad"),
                         ("Mozilla/5.0 (Linux; Android 14; Pixel 8) Chrome/130.0", "android"),
                         ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15", "mac"),
                         ("Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "pc"), ("", "pc")):
            self.assertEqual(self.m.tipo_da_agente(ua), tipo, ua)
        for ua, nome in (("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1", "Safari"),
                         ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 CriOS/130.0 Mobile/15E148 Safari/604.1", "Chrome"),
                         ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 FxiOS/130.0 Mobile/15E148 Safari/605.1.15", "Firefox"),
                         ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36 Edg/130.0", "Edge"),
                         ("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36", "Chrome"),
                         ("Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0", "Firefox"), ("curl/8", "Altro")):
            self.assertEqual(self.m.browser_da_agente(ua), nome, ua)


# ----- idee per migliorare l'app (riquadro «Idee», Gestione → Idee, --idee-nuove) -----

ANNA = "a0a1a2a3a4a5a6a7"   # id di una persona invitata, come quelli di Persone


class TestIdee(unittest.TestCase):
    """Archivio delle idee: al più 2000 caratteri e 10 al giorno per persona, stati, nota di Samuele, file 0600."""

    @classmethod
    def setUpClass(cls):
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("musica_libreria_idee", SERVER)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="atlante-idee-")
        self.file = os.path.join(self.base, "cache", "idee.json")
        self.i = self.m.Idee(self.file)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def errore(self, codice, fn, *a, **k):
        with self.assertRaises(self.m.ErroreExtra) as e:
            fn(*a, **k)
        self.assertEqual(e.exception.codice, codice)
        return e.exception.testo

    def piu_tardi(self, secondi):
        return unittest.mock.patch.object(self.m.time, "time", return_value=time.time() + secondi)

    def test_manda_e_le_mie(self):
        a = self.i.manda(ANNA, "Anna", "  Vorrei vedere i testi delle canzoni\r\nmentre suonano.  ")
        self.assertEqual((a["testo"], a["stato"]), ("Vorrei vedere i testi delle canzoni\nmentre suonano.", "nuova"))
        self.assertRegex(a["id"], r"^[0-9a-f]{16}$")
        s = self.i.manda("p", "Samuele", "Un tasto per la radio")
        self.assertEqual([x["id"] for x in self.i.mie(ANNA)], [a["id"]])
        self.assertEqual([x["id"] for x in self.i.mie("p")], [s["id"]])
        self.assertNotIn("nota", self.i.mie(ANNA)[0])   # la nota è per Samuele e Claude, non per chi scrive
        el = self.i.elenco()
        self.assertEqual([x["id"] for x in el], [s["id"], a["id"]])   # la più recente per prima
        self.assertEqual((el[1]["da"], el[1]["nome"], el[1]["nota"]), (ANNA, "Anna", ""))
        self.assertEqual(self.i.nuove(), 2)
        self.assertEqual(os.stat(self.file).st_mode & 0o777, 0o600)
        self.assertEqual([x["id"] for x in self.m.Idee(self.file).elenco()], [s["id"], a["id"]])   # un altro processo

    def test_limiti(self):
        self.errore(400, self.i.manda, "p", "Samuele", "   \n  ")
        self.errore(400, self.i.manda, "p", "Samuele", None)
        self.errore(400, self.i.manda, "p", "Samuele", ["una", "lista"])
        self.assertIn("2000", self.errore(400, self.i.manda, "p", "Samuele", "x" * 2001))
        self.assertEqual(len(self.i.manda("p", "Samuele", "è" * 2000)["testo"]), 2000)
        self.assertEqual(self.i.restano("p"), 9)
        for k in range(9):
            self.i.manda("p", "Samuele", "Idea %d" % k)
        self.assertIn("domani", self.errore(429, self.i.manda, "p", "Samuele", "L'undicesima"))
        self.assertEqual((self.i.restano("p"), self.i.restano(ANNA)), (0, 10))
        self.i.manda(ANNA, "Anna", "Un'altra persona sì")
        with self.piu_tardi(2 * 86400):   # un altro giorno: si riparte
            self.assertEqual(self.i.restano("p"), 10)
            self.i.manda("p", "Samuele", "Un altro giorno")

    def test_testo_pulito(self):
        # caratteri di controllo e di direzione del testo: in un terminale (--idee-nuove) o in una pagina ingannano
        a = self.i.manda("p", "Samuele", "a\x1b[31mb\u202ec\x00d\te \U0001F468\u200d\U0001F469\u200d\U0001F467")
        self.assertEqual(a["testo"], "a[31mbcd\te \U0001F468\u200d\U0001F469\u200d\U0001F467")

    def test_stati_e_nota(self):
        a = self.i.manda(ANNA, "Anna", "Più playlist")
        self.assertEqual(self.i.cambia(a["id"], stato="proposta")["stato"], "proposta")
        self.assertEqual(self.i.nuove(), 0)
        x = self.i.cambia(a["id"], nota="  Fallo così: una scheda in più ")
        self.assertEqual((x["stato"], x["nota"], x["nome"]), ("proposta", "Fallo così: una scheda in più", "Anna"))
        for s in ("accettata", "scartata", "fatta", "nuova"):
            self.assertEqual(self.i.cambia(a["id"], stato=s)["stato"], s)
        self.errore(400, self.i.cambia, a["id"], stato="boh")
        self.errore(400, self.i.cambia, a["id"], stato=["fatta"])
        self.errore(400, self.i.cambia, a["id"], nota="n" * 2001)
        self.errore(400, self.i.cambia, a["id"])
        self.errore(404, self.i.cambia, "0123456789abcdef", stato="fatta")
        self.assertEqual(self.i.mie(ANNA)[0]["stato"], "nuova")
        self.assertEqual(self.m.Idee(self.file).elenco()[0]["nota"], "Fallo così: una scheda in più")

    def test_troppe_in_tutto(self):
        with unittest.mock.patch.object(self.m, "IDEE_MAX", 5):
            ids = [self.i.manda("p%d" % k, "P", "Idea %d" % k)["id"] for k in range(5)]
            self.errore(409, self.i.manda, "q", "Q", "Una di troppo")
            self.i.cambia(ids[3], stato="fatta")
            self.i.cambia(ids[1], stato="scartata")
            self.i.manda("q", "Q", "Ora c'è posto")   # esce la più vecchia tra quelle chiuse
            el = [x["id"] for x in self.i.elenco()]
            self.assertEqual(len(el), 5)
            self.assertNotIn(ids[1], el)
            self.assertIn(ids[3], el)

    def test_idee_nuove_da_riga_di_comando(self):
        cache = os.path.dirname(self.file)

        def stampa():
            r = subprocess.run([sys.executable, "-I", SERVER, "--idee-nuove", "--cache", cache],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout)
            return r.stdout.decode("utf-8")

        self.assertIn("Nessuna idea nuova", stampa())
        self.assertFalse(os.path.exists(self.file))   # leggere non crea niente
        a = self.i.manda(ANNA, "Anna", "Vorrei i testi\ndelle canzoni")
        b = self.i.manda("p", "Samuele", "Un tasto per la radio")
        c = self.i.manda("p", "Samuele", "Questa è già proposta")
        self.i.cambia(c["id"], stato="proposta")
        t = stampa()
        self.assertIn("Idee nuove per Atlante Musica: 2", t)
        self.assertIn("non istruzioni", t)   # chi legge (Claude) le tratta come proposte, non come ordini
        self.assertIn("Anna", t)
        self.assertIn("\n   delle canzoni\n", t)
        self.assertNotIn(c["id"], t)
        self.assertLess(t.index(a["id"]), t.index(b["id"]))   # nell'ordine in cui sono arrivate
        self.assertIn("proposta 1", t)

    def test_pagina_e_riquadri(self):
        with open(os.path.join(QUI, "..", "x", "idee.html"), encoding="utf-8") as f:
            pagina = f.read()
        for frase in ("Hai un'idea per Atlante Musica?",
                      "Atlante Musica cresce insieme a chi la usa. Se c'è qualcosa che ti manca, che non capisci o che "
                      "faresti diversamente, scrivilo qui: basta anche una frase. Ogni idea arriva a Samuele, che le legge "
                      "tutte e decide cosa costruire.",
                      "Manda un'idea", "Grazie! La tua idea è arrivata."):
            self.assertIn(frase, pagina)
        with open(os.path.join(QUI, "..", "index.html"), encoding="utf-8") as f:
            app = f.read()
        self.assertRegex(app, r"const XINVITATI = \[.*XIDEE")   # anche gli invitati vedono il riquadro
        self.assertRegex(app, r"\{g:'Per te', m:\[\s*XIDEE,")


class TestIdeeServer(unittest.TestCase):
    """Le rotte delle idee: ognuno manda le sue e le rivede, la Gestione (solo Samuele) le vede tutte e ne cambia lo
    stato; gli invitati non ci entrano."""
    ORIGINE, IPHONE = TestAccesso.ORIGINE, TestAccesso.IPHONE
    da_fuori, invitato = TestAccesso.da_fuori, TestAccesso.invitato
    biscotto = staticmethod(TestAccesso.biscotto)

    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="atlante-idee-server-")
        cls.c = prepara_cartelle(cls.base)
        with open(os.path.join(cls.c["app"], "x", "idee.html"), "w", encoding="utf-8") as f:
            f.write("<!doctype html><p>idee")
        cls.s = Server(cls.base, cls.c, ["--pubblico", "https://mac.esempio.ts.net:10000"]).avvia()
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("musica_libreria_idee_server", SERVER)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)
        cls.pers = cls.m.Persone(os.path.join(cls.c["cache"], "persone.json"))

    @classmethod
    def tearDownClass(cls):
        cls.s.ferma()
        shutil.rmtree(cls.base, ignore_errors=True)

    def test_idee(self):
        pid, v, _ = self.invitato("Gina", ip="203.0.113.130")

        def gina(metodo, p, corpo=None, origine=None):
            st, h, b = self.da_fuori(metodo, p, corpo, biscotto=v, extra={"Origin": origine or self.ORIGINE["Origin"]})
            return st, (json.loads(b.decode("utf-8")) if b.startswith(b"{") else b)

        def samuele(metodo, p, corpo=None, codice=200):
            return self.s.json(metodo, p, corpo, codice)

        self.assertEqual(gina("GET", "/x/idee.html")[0], 200)
        st, j = gina("GET", "/api/extra/idee")
        self.assertEqual((st, j["idee"], j["restano"], j["max_caratteri"]), (200, [], 10, 2000))
        st, j = gina("POST", "/api/extra/idee", {"testo": "<b>Più</b> playlist <script>x</script>"})
        self.assertEqual(st, 200, j)
        ig = j["idea"]
        self.assertEqual((ig["testo"], ig["stato"], j["restano"]), ("<b>Più</b> playlist <script>x</script>", "nuova", 9))
        self.assertEqual(gina("POST", "/api/extra/idee", {"testo": "x" * 2001})[0], 400)
        self.assertEqual(gina("POST", "/api/extra/idee", {"testo": "Da un altro sito"}, "https://altro.example")[0], 403)
        mia = samuele("POST", "/api/extra/idee", {"testo": "Un tasto per la radio"})["idea"]
        self.assertEqual([x["id"] for x in gina("GET", "/api/extra/idee")[1]["idee"]], [ig["id"]])   # solo le sue
        self.assertEqual([x["id"] for x in samuele("GET", "/api/extra/idee")["idee"]], [mia["id"]])
        # Gestione → Idee: tutte, con chi le ha scritte
        el = samuele("GET", "/api/extra/gestione/idee")
        self.assertEqual([(x["id"], x["nome"], x["da"]) for x in el["idee"]], [(mia["id"], "Samuele", "p"), (ig["id"], "Gina", pid)])
        self.assertEqual(el["nuove"], 2)
        self.assertEqual(samuele("GET", "/api/extra/io")["nuove"]["idee"], 2)   # il numero sul riquadro della Gestione
        self.assertNotIn("nuove", gina("GET", "/api/extra/io")[1])
        x = samuele("POST", "/api/extra/gestione/idee/" + ig["id"], {"stato": "accettata", "nota": "Fallo così"})["idea"]
        self.assertEqual((x["stato"], x["nota"]), ("accettata", "Fallo così"))
        self.assertEqual(samuele("GET", "/api/extra/io")["nuove"]["idee"], 1)
        samuele("POST", "/api/extra/gestione/idee/" + ig["id"], {"stato": "boh"}, codice=400)
        samuele("POST", "/api/extra/gestione/idee/0123456789abcdef", {"stato": "fatta"}, codice=404)
        samuele("POST", "/api/extra/gestione/idee/../persone", {"stato": "fatta"}, codice=404)
        mie = gina("GET", "/api/extra/idee")[1]["idee"]
        self.assertEqual(mie[0]["stato"], "accettata")
        self.assertNotIn("nota", mie[0])
        # la Gestione delle idee è solo di Samuele, e solo dalla sua app
        for metodo, p in (("GET", "gestione/idee"), ("POST", "gestione/idee/" + ig["id"])):
            st, j = gina(metodo, "/api/extra/" + p, {"stato": "fatta"} if metodo == "POST" else None)
            self.assertEqual((st, j["errore"]), (403, "Non disponibile per gli invitati"), p)
        st, h, b = richiesta(self.s.porta, "POST", "/api/extra/gestione/idee/" + ig["id"], {"stato": "fatta"},
                             intest={"Origin": "https://altro.example"})
        self.assertEqual(st, 403)
        self.assertEqual(samuele("GET", "/api/extra/gestione/idee")["idee"][1]["stato"], "accettata")
        # 10 al giorno per persona
        for k in range(9):
            self.assertEqual(gina("POST", "/api/extra/idee", {"testo": "Idea %d" % k})[0], 200)
        st, j = gina("POST", "/api/extra/idee", {"testo": "L'undicesima"})
        self.assertEqual(st, 429)
        self.assertIn("domani", j["errore"])
        self.assertEqual(gina("GET", "/api/extra/idee")[1]["restano"], 0)
        # una persona revocata: le sue idee restano, con il suo nome
        self.assertTrue(self.pers.revoca_persona(pid))
        self.assertIn("Gina", [x["nome"] for x in samuele("GET", "/api/extra/gestione/idee")["idee"]])
        with open(os.path.join(self.c["cache"], "idee.json"), encoding="utf-8") as f:
            self.assertEqual(os.stat(f.name).st_mode & 0o777, 0o600)
            self.assertIn("Un tasto per la radio", f.read())


# ----- classifiche e playlist: brani nuovi da soli (pagine e risposte finte salvate qui: nessuna richiesta a Internet) -----

# i server delle prove non leggono mai le classifiche vere, anche se queste variabili ci sono (sul Mac mini nel servizio)
for _v in ("ATLANTE_CLASSIFICHE", "ATLANTE_CLASSIFICHE_FINTE"):
    os.environ.pop(_v, None)

PAGINA_KWORB = """<!DOCTYPE html><html><body><table id="spotifyweekly" class="sortable">
<thead><tr><th class="np">Pos</th><th class="np">P+</th><th class="mp">Artist and Title</th><th>Wks</th></tr></thead><tbody>
<tr><td class="np">1</td><td class="np">=</td><td class="text mp"><div><a href="../artist/0Y5tJX1MQlPlqiwlOH1tJY.html">Travis Scott</a> - <a href="../track/42VsgItocQwOQC3XWZ8JNA.html">FE!N</a> (w/ <a href="../artist/699OTQXzgjhIYAHMy9RyPD.html">Playboi Carti</a>)</div></td><td>60</td></tr>
<tr><td class="np">2</td><td class="np">+1</td><td class="text mp"><div><a href="../artist/1.html">Sfera Ebbasta</a>, <a href="../artist/2.html">Shiva</a> - <a href="../track/2.html">Mamma mia</a></div></td><td>3</td></tr>
<tr><td class="np">3</td><td class="np">-1</td><td class="text mp"><div><a href="../artist/3.html">ROS&Eacute;</a> &amp; <a href="../artist/4.html">Bruno Mars</a> - <a href="../track/3.html">APT.</a></div></td><td>3</td></tr>
</tbody></table></body></html>"""

PAGINA_BILLBOARD = """<html><body><div class="chart-results-list">
<div class="o-chart-results-list-row-container"><ul class="o-chart-results-list-row // lrv-a-unstyle-list">
<li><span class="c-label  a-font-primary-bold-l u-font-size-32@tablet">1</span></li>
<li class="lrv-u-width-100p"><ul><li class="o-chart-results-list__item">
<h3 id="title-of-a-story" class="c-title  a-no-trucate a-font-primary-bold-s">
	Die With A Smile
</h3>
<span class="c-label  a-no-trucate a-font-primary-s lrv-u-font-size-14@mobile-max">
	Lady Gaga &amp; Bruno Mars
</span></li></ul></li></ul></div>
<div class="o-chart-results-list-row-container"><ul class="o-chart-results-list-row">
<li><span class="c-label a-font-primary-bold-l">2</span><span class="c-label">NEW</span></li>
<li><h3 id="title-of-a-story" class="c-title a-no-trucate">luther</h3>
<span class="c-label a-no-trucate">Kendrick Lamar &amp; SZA</span></li></ul></div>
</div>
<h3 id="title-of-a-story" class="c-title">Gains in Weekly Performance</h3><span class="c-label a-no-trucate">Articolo</span>
</body></html>"""

PAGINA_UK = """<html><body>
<div class="chart-item"><div class="chart-item-content"><div class="description block">
<p><a class="chart-name font-bold inline-block" href="/songs/sabrina-carpenter-espresso/"><span class="badge new">New</span><span>ESPRESSO</span></a></p>
<a class="chart-artist text-lg inline-block" href="/artist/sabrina-carpenter/"><span>SABRINA CARPENTER</span></a></div></div></div>
<div class="chart-item chart-ad"><div class="ad">Pubblicità</div></div>
<div class="chart-item"><div class="chart-item-content"><p><a class="chart-name font-bold" href="/songs/dua-lipa-new-rules/"><span>NEW RULES</span></a></p>
<a class="chart-artist" href="/artist/dua-lipa/"><span>DUA LIPA</span></a></div></div>
</body></html>"""

PAGINA_FIMI = """<html><body><nav><a href="/album">Album</a> <a href="/singoli">Singoli</a></nav>
<h2>Top Album</h2>
<div class="chart-item"><div class="title">ALBUM CHE NON E UN BRANO</div><div class="artist">Qualcuno</div></div>
<h2>Top Singoli</h2>
""" + "".join('<div class="chart-item"><div class="position">%d</div><div class="title">%s</div><div class="artist">%s</div>'
              '<div class="label">Etichetta</div></div>\n' % (i + 1, t, a) for i, (t, a) in enumerate((
                  ("BALORDA NOSTALGIA", "OLLY"), ("TU CON CHI FAI L'AMORE", "THE KOLORS"), ("CUORICINI", "COMA_COSE"),
                  ("FUORILEGGE", "ROSE VILLAIN"), ("NUN TE SCETÀ", "GEOLIER")))) + """
<h2>Top Vinili</h2><div class="chart-item"><div class="title">VINILE</div><div class="artist">Altro</div></div>
</body></html>"""

PAGINA_TABELLA = """<html><body><table><tr><th>Pos.</th><th>Titolo</th><th>Artista</th></tr>
""" + "".join("<tr><td>%d</td><td><a href='#'>%s</a></td><td>%s</td></tr>" % (i + 1, t, a) for i, (t, a) in enumerate((
    ("Brano uno", "Artista uno"), ("Brano due", "Artista due"), ("Brano tre", "Artista tre"), ("Brano quattro", "Artista quattro"),
    ("Brano cinque", "Artista cinque")))) + "</table></body></html>"

RSS_APPLE = json.dumps({"feed": {"title": "Top Songs", "author": {"name": "Apple"}, "results": [
    {"artistName": "Olly", "id": "1", "name": "Balorda nostalgia", "kind": "songs"},
    {"artistName": "Kendrick Lamar & SZA", "id": "2", "name": "luther", "kind": "songs"}]}})

PAGINA_APPLE = """<html><head><script type="application/ld+json">{"@type":"MusicPlaylist","name":"Top 100: Global"}</script></head>
<body><script type="application/json" id="serialized-server-data">[{"data":{"sections":[{"items":[
{"title":"Die With A Smile","artistName":"Lady Gaga & Bruno Mars","contentDescriptor":{"kind":"song"}},
{"title":"APT.","artistName":"ROSÉ & Bruno Mars"}]}]}}]</script></body></html>"""

PAGINA_SPOTIFY = """<html><body><script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"state":{"data":
{"entity":{"name":"RapCaviar","subtitle":"Spotify","trackList":[{"uri":"spotify:track:1","title":"Not Like Us","subtitle":"Kendrick Lamar",
"isExplicit":true},{"uri":"spotify:track:2","title":"Timeless (with Playboi Carti)","subtitle":"The Weeknd, Playboi Carti"}]}}}}}}</script></body></html>"""

PAGINA_CAMBIATA = "<html><body><h1>Manutenzione</h1><p>Torniamo presto.</p></body></html>"


def modulo_server(nome):
    """Il server caricato come modulo nelle prove (senza main(): nessun server in ascolto)."""
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(nome, SERVER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def risultato(vid, titolo, artista, album="", explicit=False, durata=200):
    """Un risultato della ricerca di YouTube Music com'è dopo _brano_ytm (prima di _ordina)."""
    return {"id": vid, "titolo": titolo, "artista": artista, "album": album, "durata": durata, "copertina": "",
            "explicit": explicit}


def ricerca_playlist(voci):
    """Risposta finta di youtubei/v1/search con playlist: (id, nome, sotto)."""
    return {"contents": {"tabbedSearchResultsRenderer": {"tabs": [{"tabRenderer": {"content": {"sectionListRenderer": {
        "contents": [{"musicShelfRenderer": {"contents": [{"musicResponsiveListItemRenderer": {
            "navigationEndpoint": {"browseEndpoint": {"browseId": vid}},
            "flexColumns": [
                {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": nome}]}}},
                {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": sotto}]}}}]}}
            for vid, nome, sotto in voci],
            "continuations": [{"nextContinuationData": {"continuation": "PAGINA2"}}]}}]}}}}]}}}


class Orologio:
    """Il tempo delle prove: parte da adesso e va avanti solo quando lo si sposta."""

    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


class TestClassificheLettori(unittest.TestCase):
    """Un lettore per fonte, con pagine finte; una pagina cambiata dà un elenco vuoto (la fonte si salta)."""

    @classmethod
    def setUpClass(cls):
        cls.m = modulo_server("musica_libreria_cl_lettori")

    def test_kworb(self):
        self.assertEqual(self.m._cl_kworb(PAGINA_KWORB), [("Travis Scott", "FE!N"), ("Sfera Ebbasta, Shiva", "Mamma mia"),
                                                          ("ROSÉ, Bruno Mars", "APT.")])

    def test_billboard(self):
        self.assertEqual(self.m._cl_billboard(PAGINA_BILLBOARD), [("Lady Gaga & Bruno Mars", "Die With A Smile"),
                                                                  ("Kendrick Lamar & SZA", "luther")])

    def test_uk(self):
        # il segno «New» non entra nel titolo, «NEW RULES» resta intero
        self.assertEqual(self.m._cl_uk(PAGINA_UK), [("SABRINA CARPENTER", "ESPRESSO"), ("DUA LIPA", "NEW RULES")])

    def test_fimi(self):
        r = self.m._cl_fimi(PAGINA_FIMI)
        self.assertEqual(r[0], ("OLLY", "BALORDA NOSTALGIA"))
        self.assertEqual(len(r), 5, r)   # solo i singoli: né l'album prima né il vinile dopo
        self.assertEqual(r[4], ("GEOLIER", "NUN TE SCETÀ"))
        self.assertEqual(self.m._cl_fimi(PAGINA_TABELLA)[1], ("Artista due", "Brano due"))   # anche una tabella

    def test_apple(self):
        self.assertEqual(self.m._cl_apple(RSS_APPLE), [("Olly", "Balorda nostalgia"), ("Kendrick Lamar & SZA", "luther")])
        self.assertEqual(self.m._cl_apple(PAGINA_APPLE), [("Lady Gaga & Bruno Mars", "Die With A Smile"),
                                                          ("ROSÉ & Bruno Mars", "APT.")])

    def test_spotify(self):
        self.assertEqual(self.m._cl_spotify(PAGINA_SPOTIFY), [("Kendrick Lamar", "Not Like Us"),
                                                              ("The Weeknd, Playboi Carti", "Timeless (with Playboi Carti)")])

    def test_pagina_cambiata(self):
        m = self.m
        for lettore in (m._cl_kworb, m._cl_billboard, m._cl_uk, m._cl_fimi, m._cl_apple, m._cl_spotify):
            self.assertEqual(lettore(PAGINA_CAMBIATA), [], lettore.__name__)
            self.assertEqual(lettore(""), [], lettore.__name__)
        self.assertEqual(m._cl_apple("{non json"), [])

    def test_leggi_pagine_finte(self):
        m = self.m
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FintoWeb)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with unittest.mock.patch.dict(os.environ, {"ATLANTE_CLASSIFICHE_FINTE": "http://127.0.0.1:%d" % srv.server_address[1]}):
                f = [x for x in m.CL_FONTI if x["id"] == "spotify-it"][0]
                self.assertEqual(m._cl_leggi(f), [("Eminem", "Without Me")])
                with self.assertRaises(RuntimeError) as e:
                    m._cl_leggi([x for x in m.CL_FONTI if x["id"] == "billboard"][0])
                self.assertIn("404", str(e.exception))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_youtube_music(self):
        m = self.m
        classifiche = {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
            "sectionListRenderer": {"contents": [{"musicCarouselShelfRenderer": {
                "header": {"musicCarouselShelfBasicHeaderRenderer": {"title": {"runs": [{"text": "Classifiche video"}]}}},
                "contents": [
                    {"musicTwoRowItemRenderer": {"title": {"runs": [{"text": "Top 100 video musicali Italia"}]},
                                                 "navigationEndpoint": {"browseEndpoint": {"browseId": "VLPLvideoIT"}}}},
                    {"musicTwoRowItemRenderer": {"title": {"runs": [{"text": "Top 100 brani Italia"}]},
                                                 "navigationEndpoint": {"browseEndpoint": {"browseId": "VLPLbraniIT"}}}},
                    {"musicTwoRowItemRenderer": {"title": {"runs": [{"text": "Rap italiano: novità"}]},
                                                 "navigationEndpoint": {"browseEndpoint": {"browseId": "VLPLaltro"}}}}]}}]}}}}]}}}
        pagina1 = {"contents": {"musicPlaylistShelfRenderer": {"contents": [
            _brano_ytm("Brano000001", "Balorda nostalgia", ["Olly"], "Balorda nostalgia", "3:10", False),
            _brano_ytm("Brano000002", "Cuoricini", ["Coma_Cose"], "Cuoricini", "2:58", False),
            {"continuationItemRenderer": {"continuationEndpoint": {"continuationCommand": {"token": "SEGUE"}}}}]}}}
        pagina2 = {"onResponseReceivedActions": [{"appendContinuationItemsAction": {"continuationItems": [
            _brano_ytm("Brano000003", "Fuorilegge", ["Rose Villain"], "Fuorilegge", "3:01", True)]}}]}
        chieste = []

        def ytm(percorso, corpo, extra=""):
            chieste.append(corpo.get("browseId") or corpo.get("continuation"))
            if corpo.get("browseId") == "FEmusic_charts":
                self.assertEqual(corpo["formData"], {"selectedValues": ["IT"]})
                return classifiche
            return {"VLPLbraniIT": pagina1, "SEGUE": pagina2}.get(chieste[-1], {"contents": {}})
        with unittest.mock.patch.object(m, "_cl_ytm", ytm):
            r = m._cl_ytm_classifica("IT")
        # prima la playlist dei brani (poi quella dei video); le altre playlist della pagina no
        self.assertEqual(r, [("Olly", "Balorda nostalgia"), ("Coma_Cose", "Cuoricini"), ("Rose Villain", "Fuorilegge")])
        self.assertEqual(chieste[:3], ["FEmusic_charts", "VLPLbraniIT", "SEGUE"])
        self.assertNotIn("VLPLaltro", chieste)

    def test_scopri_playlist(self):
        m = self.m
        risposta = ricerca_playlist([("VLRDCLAK5uy_a", "Pop italiano: le hit", "Playlist • YouTube Music • 250.000 visualizzazioni"),
                                     ("UCartista", "Un artista", "Artista"),
                                     ("VLPLcommunity", "Hit pop 2026", "Playlist • Mario • 1,2 Mln visualizzazioni"),
                                     ("VLPLpiccola", "Pop nuovo", "Playlist • Anna • 12K views")])
        corpi = []

        def ytm(percorso, corpo, extra=""):
            corpi.append((percorso, corpo, extra))
            return risposta
        with unittest.mock.patch.object(m, "_cl_ytm", ytm):
            trovate, seguito = m._cl_scopri("pop italiano", m.CL_FILTRI_PLAYLIST[0], "")
            m._cl_scopri("pop italiano", None, "PAGINA2")
        self.assertEqual([p["id"] for p in trovate], ["VLPLcommunity", "VLRDCLAK5uy_a", "VLPLpiccola"])   # dalla più vista
        self.assertEqual([p["viste"] for p in trovate], [1200000, 250000, 12000])
        self.assertEqual(seguito, "PAGINA2")
        self.assertEqual((corpi[0][0], corpi[0][1]["query"], corpi[0][1]["params"]), ("search", "pop italiano", m.CL_FILTRI_PLAYLIST[0]))
        self.assertEqual(corpi[1][1], {"continuation": "PAGINA2"})
        self.assertIn("ctoken=PAGINA2", corpi[1][2])

    def test_viste(self):
        for testo, n in (("1,2 Mln visualizzazioni", 1200000), ("350.000 visualizzazioni", 350000), ("1.5M views", 1500000),
                         ("12K views", 12000), ("2 Mld di visualizzazioni", 2000000000), ("Playlist • YouTube Music", 0)):
            self.assertEqual(self.m._cl_viste(testo), n, testo)


class TestClassificheScelta(unittest.TestCase):
    """Quale risultato di YouTube Music si scarica per un brano di una classifica, e quando un brano c'è già."""

    @classmethod
    def setUpClass(cls):
        cls.m = modulo_server("musica_libreria_cl_scelta")

    def scegli(self, artista, titolo, risultati):
        return self.m._cl_scegli(artista, titolo, self.m._ordina([dict(r) for r in risultati]))

    def test_sempre_la_versione_explicit(self):
        r, motivo = self.scegli("Eminem", "Without Me", [risultato("NoExplicit1", "Without Me", "Eminem"),
                                                         risultato("Explicit001", "Without Me", "Eminem", explicit=True)])
        self.assertEqual((r["id"], motivo), ("Explicit001", ""))
        # c'è solo la versione censurata (clean, edited, radio edit): niente, mai la censurata
        for t in ("Without Me (Clean)", "Without Me (Radio Edit)", "Without Me - Edited", "Without Me [Censored]"):
            r, motivo = self.scegli("Eminem", "Without Me", [risultato("Pulita00001", t, "Eminem")])
            self.assertEqual((r, motivo), (None, "censurata"), t)
        # un brano senza versione explicit (nessuna parolaccia): va bene quella che c'è
        r, motivo = self.scegli("Olly", "Balorda nostalgia", [risultato("Olly0000001", "Balorda nostalgia", "Olly")])
        self.assertEqual(r["id"], "Olly0000001")
        # «Clean» di Taylor Swift è il titolo, non una versione censurata
        self.assertEqual(self.scegli("Taylor Swift", "Clean", [risultato("TaylorClean", "Clean", "Taylor Swift")])[0]["id"],
                         "TaylorClean")

    def test_niente_tributi_cover_karaoke(self):
        falsi = [risultato("Tributo0001", "Lose Yourself", "Eminem Tribute Band"),
                 risultato("Karaoke0001", "Lose Yourself (Karaoke Version)", "Karaoke Hits"),
                 risultato("Cover000001", "Lose Yourself", "Piano Covers", album="Rap Hits on Piano"),
                 risultato("Stile000001", "Lose Yourself (In the Style of Eminem)", "Eminem"),
                 risultato("Altro000001", "Lose Yourself", "Altro Cantante")]
        self.assertEqual(self.scegli("Eminem", "Lose Yourself", falsi), (None, "non trovato"))
        r, _ = self.scegli("Eminem", "Lose Yourself", falsi + [risultato("Vero0000001", "Lose Yourself", "Eminem", explicit=True)])
        self.assertEqual(r["id"], "Vero0000001")

    def test_altre_versioni(self):
        versioni = [risultato("Remix000001", "Houdini (Remix)", "Dua Lipa"), risultato("Live0000001", "Houdini (Live)", "Dua Lipa"),
                    risultato("Veloce00001", "Houdini (Sped Up)", "Dua Lipa"), risultato("Acustica001", "Houdini - Acoustic", "Dua Lipa"),
                    risultato("Strument001", "Houdini (Instrumental)", "Dua Lipa")]
        self.assertEqual(self.scegli("Dua Lipa", "Houdini", versioni)[1], "non trovato")
        self.assertEqual(self.scegli("Dua Lipa", "Houdini", versioni + [risultato("Houdini0001", "Houdini", "Dua Lipa")])[0]["id"],
                         "Houdini0001")
        # se è la classifica a volere il remix, il remix va bene
        self.assertEqual(self.scegli("Dua Lipa", "Houdini (Remix)", versioni)[0]["id"], "Remix000001")

    def test_artista_principale_tra_gli_esecutori(self):
        self.assertEqual(self.scegli("Shiva", "Mamma mia", [risultato("Mamma000001", "Mamma mia", "Sfera Ebbasta e Shiva")])[0]["id"],
                         "Mamma000001")
        self.assertEqual(self.scegli("Travis Scott, Playboi Carti", "FE!N",
                                     [risultato("Fein0000001", "FE!N (feat. Playboi Carti)", "Travis Scott", explicit=True)])[0]["id"],
                         "Fein0000001")
        self.assertEqual(self.scegli("ROSÉ & Bruno Mars", "APT.", [risultato("Apt00000001", "APT.", "ROSÉ & Bruno Mars")])[0]["id"],
                         "Apt00000001")
        self.assertEqual(self.scegli("Adele", "Hello", [risultato("Hello000001", "Hello", "Lionel Richie")]), (None, "non trovato"))
        # durate assurde (un'ora di «mix», tre secondi)
        self.assertEqual(self.scegli("Adele", "Hello", [risultato("Hello000002", "Hello", "Adele", durata=3600)])[0], None)

    def test_stesso_brano_scritto_in_altro_modo(self):
        m = self.m
        for a, b in (("FE!N", "FE!N (feat. Playboi Carti)"), ("APT.", "Apt"), ("Perché", "perche"), ("N.Y. State", "NY State"),
                     ("Shake That", "Shake That [Explicit]"), ("ESPRESSO", "Espresso"), ("Rock & Roll", "Rock and Roll")):
            self.assertEqual(m._cl_titolo(a), m._cl_titolo(b), (a, b))
            self.assertEqual(m._cl_chiave("Eminem", a), m._cl_chiave("eminem feat. Nate Dogg", b))
        self.assertNotEqual(m._cl_titolo("사랑"), m._cl_titolo("노래"))   # titoli in altre scritture restano diversi
        self.assertTrue(m._cl_titolo("사랑"))


class TestClassificheLavoro(unittest.TestCase):
    """Il lavoro in sottofondo, passo per passo, con fonti, ricerca e scaricamento finti e il tempo spostato a mano."""

    @classmethod
    def setUpClass(cls):
        cls.m = modulo_server("musica_libreria_cl_lavoro")
        cls.m.CL_PAUSA = 0

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="atlante-classifiche-")
        self.ora = Orologio()
        self.pagine = {}          # id della fonte -> brani (o un'eccezione da sollevare)
        self.predefinito = []     # brani delle fonti che non sono in self.pagine
        self.risposte = {}        # ricerca -> risultati (o un'eccezione)
        self.ricerche, self.scaricati, self.letture = [], [], []
        self.libreria = []
        self.libero = 7 * 1024 ** 4
        self.playlist = {}        # (ricerca, filtro, seguito) -> ([playlist], seguito)
        self.scoperte = []

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def leggi(self, f):
        self.letture.append(f["id"])
        x = self.pagine.get(f["id"], self.predefinito)
        if isinstance(x, Exception):
            raise x
        return list(x)

    def cerca(self, q):
        self.ricerche.append(q)
        x = self.risposte.get(q, [])
        if isinstance(x, Exception):
            raise x
        return self.m._ordina([dict(r) for r in x])

    def scarica(self, r):
        self.scaricati.append(r["id"])
        x = self.risposte.get("scarica " + r["id"])
        if isinstance(x, Exception):
            raise x
        return {"nome": "%s - %s.mp3" % (r["artista"], r["titolo"]), "brano": "b%d" % len(self.scaricati), "percorso": ""}

    def scopri(self, q, filtro, seguito):
        self.scoperte.append((q, filtro, seguito))
        x = self.playlist.get((q, filtro, seguito), ([], ""))
        if isinstance(x, Exception):
            raise x
        return x

    def nuovo(self, fonti=None, spotify=(), generi=()):
        m = self.m
        b = m.BloccoYouTube(os.path.join(self.base, "youtube-blocco.json"))
        b.adesso = self.ora
        c = m.Classifiche(os.path.join(self.base, "classifiche"), b)
        c.adesso = self.ora
        c.fonti = fonti if fonti is not None else [{"id": "prova", "nome": "Classifica di prova", "tipo": "finta", "quanti": 100}]
        c.spotify, c.generi = list(spotify), list(generi)
        c.leggi, c.cerca, c.scarica, c.scopri = self.leggi, self.cerca, self.scarica, self.scopri
        c.libero = lambda: self.libero
        c.catalogo = lambda: (len(self.libreria), list(self.libreria))
        c.videoclip = lambda artista, titolo: ""
        return c

    def gira(self, c, massimo=200):
        """Passi finché il lavoro non chiede di aspettare: restituisce quanto."""
        for _ in range(massimo):
            attesa = c.passo()
            if attesa:
                return attesa
        self.fail("il lavoro non si ferma mai")

    def test_una_classifica(self):
        self.pagine["prova"] = [("Eminem", "Without Me"), ("Travis Scott, Playboi Carti", "FE!N"), ("Eminem", "Without Me"),
                                ("Eminem", "Lose Yourself"), ("Artista", "Solo Pulita"), ("Olly", "Balorda nostalgia")]
        self.risposte = {"Eminem Without Me": [risultato("NoExplicit1", "Without Me", "Eminem"),
                                               risultato("Explicit001", "Without Me", "Eminem", "The Eminem Show", True)],
                         "Eminem Lose Yourself": [risultato("Tributo0001", "Lose Yourself", "Eminem Tribute Band")],
                         "Artista Solo Pulita": [risultato("Pulita00001", "Solo Pulita (Clean)", "Artista")],
                         "Olly Balorda nostalgia": [risultato("Explicit001", "Without Me", "Eminem", explicit=True),
                                                    risultato("Olly0000001", "Balorda nostalgia", "Olly")]}
        self.libreria = [("Travis Scott", "FE!N (feat. Playboi Carti)")]   # c'è già, scritto in un altro modo
        c = self.nuovo()
        self.assertEqual(self.gira(c), 3600)   # tutto fatto: aspetta
        self.assertEqual(self.letture, ["prova"])
        # il brano che la libreria ha già e il doppione della classifica: nessuna ricerca
        self.assertEqual(self.ricerche, ["Eminem Without Me", "Eminem Lose Yourself", "Artista Solo Pulita", "Olly Balorda nostalgia"])
        self.assertEqual(self.scaricati, ["Explicit001", "Olly0000001"])
        r = c.riassunto()
        self.assertEqual((r["attivo"], r["oggi"], r["totale"], r["blocco"], r["pausa"]), (True, 2, 2, None, None))
        self.assertEqual([(u["titolo"], u["artista"], u["fonte"]) for u in r["ultimi"]],
                         [("Balorda nostalgia", "Olly", "Classifica di prova"), ("Without Me", "Eminem", "Classifica di prova")])
        self.assertEqual(r["classifiche"][0]["esito"], "ok")
        self.assertEqual(r["classifiche"][0]["brani"], 5)   # senza il doppione
        # la settimana dopo la classifica si rilegge, ma i brani già cercati non si cercano più
        self.ora.t += 3 * 86400
        self.assertEqual(self.gira(c), 3600)
        self.assertEqual(self.letture, ["prova"])
        self.ora.t += 5 * 86400
        self.assertEqual(self.gira(c), 3600)
        self.assertEqual(self.letture, ["prova", "prova"])
        self.assertEqual(len(self.ricerche), 4)
        self.assertEqual(c.riassunto()["oggi"], 0)   # è un altro giorno
        self.assertEqual(c.riassunto()["totale"], 2)
        # dopo tre mesi un brano non trovato si cerca di nuovo (forse ora c'è)
        self.ora.t += 90 * 86400
        self.gira(c)
        self.assertEqual(self.ricerche[4:], ["Eminem Lose Yourself", "Artista Solo Pulita"])

    def test_ripresa_dopo_il_riavvio(self):
        self.pagine["prova"] = [("A%d" % i, "Brano %d" % i) for i in range(4)]
        self.risposte = {"A%d Brano %d" % (i, i): [risultato("Brano%06d" % i, "Brano %d" % i, "A%d" % i)] for i in range(4)}
        c = self.nuovo()
        c.passo()   # la classifica
        c.passo()   # il primo brano
        self.assertEqual(self.scaricati, ["Brano000000"])
        c = self.nuovo()   # il Mac mini si è riavviato
        self.assertEqual(c.riassunto()["totale"], 1)
        self.assertEqual(c.riassunto()["fonte"]["in_coda"], 3)
        self.gira(c)
        self.assertEqual(self.letture, ["prova"])
        self.assertEqual(self.scaricati, ["Brano%06d" % i for i in range(4)])
        self.assertEqual(self.ricerche.count("A0 Brano 0"), 1)

    def test_blocco_429_con_la_pausa_che_cresce(self):
        m = self.m
        self.pagine["prova"] = [("Eminem", "Without Me"), ("Olly", "Balorda nostalgia")]
        self.risposte = {"Eminem Without Me": m.Bloccato("YouTube ha risposto 429 Too Many Requests"),
                         "Olly Balorda nostalgia": [risultato("Olly0000001", "Balorda nostalgia", "Olly")]}
        c = self.nuovo()
        inizio = self.ora.t
        self.assertEqual(self.gira(c), 600)
        b = c.riassunto()["blocco"]
        self.assertEqual((b["tipo"], b["quando"], b["prossima"]), ("rallenta", inizio, inizio + 1800))
        self.assertIn("429", b["motivo"])
        self.assertEqual(c.riassunto()["fonte"]["in_coda"], 2)   # il brano resta in coda
        self.assertEqual(self.ricerche, ["Eminem Without Me"])
        self.assertEqual(c.passo(), 600)   # fermo: nessuna richiesta a YouTube
        self.assertEqual(self.ricerche, ["Eminem Without Me"])
        attese = []
        for pausa in (1800, 7200, 28800, 86400, 86400):   # ogni prova fallita allunga la pausa, poi una al giorno
            self.ora.t = c.riassunto()["blocco"]["prossima"]
            self.gira(c)
            attese.append(c.riassunto()["blocco"]["prossima"] - self.ora.t)
        self.assertEqual(attese, [7200, 28800, 86400, 86400, 86400])
        self.assertEqual(len(self.ricerche), 6)   # una ricerca sola per ogni prova
        self.assertEqual(c.riassunto()["blocco"]["quando"], inizio)   # l'ora del blocco resta quella della prima volta
        # riavvio durante il blocco: resta fermo
        c = self.nuovo()
        self.assertIsNotNone(c.riassunto()["blocco"])
        self.assertGreater(c.passo(), 0)
        self.assertEqual(len(self.ricerche), 6)
        # il blocco finisce: alla prova seguente si riparte e il brano rimasto in coda si fa
        self.risposte["Eminem Without Me"] = [risultato("Explicit001", "Without Me", "Eminem", explicit=True)]
        self.ora.t = c.riassunto()["blocco"]["prossima"]
        self.assertEqual(self.gira(c), 3600)
        self.assertIsNone(c.riassunto()["blocco"])
        self.assertEqual(self.scaricati, ["Explicit001", "Olly0000001"])

    def test_robot_e_riprova_adesso(self):
        m = self.m
        self.pagine["prova"] = [("Eminem", "Without Me")]
        self.risposte = {"Eminem Without Me": m.Bloccato("Sign in to confirm you’re not a bot")}
        c = self.nuovo()
        self.gira(c)
        b = c.riassunto()["blocco"]
        self.assertEqual((b["tipo"], b["prossima"] - b["quando"]), ("robot", 86400))   # una prova al giorno, da subito
        self.ora.t += 3600
        self.assertGreater(c.passo(), 0)
        self.assertEqual(len(self.ricerche), 1)
        # «Riprova adesso» in Gestione: una prova sola, subito
        self.risposte["Eminem Without Me"] = [risultato("Explicit001", "Without Me", "Eminem", explicit=True)]
        c.riprova_adesso()
        self.assertEqual(self.gira(c), 3600)
        self.assertIsNone(c.riassunto()["blocco"])
        self.assertEqual(self.scaricati, ["Explicit001"])
        self.assertEqual(len(self.ricerche), 2)   # la prova serve anche al brano: nessuna ricerca in più

    def test_blocco_durante_lo_scaricamento(self):
        m = self.m
        self.pagine["prova"] = [("Eminem", "Without Me"), ("Olly", "Balorda nostalgia")]
        self.risposte = {"Eminem Without Me": [risultato("Explicit001", "Without Me", "Eminem", explicit=True)],
                         "Olly Balorda nostalgia": [risultato("Olly0000001", "Balorda nostalgia", "Olly")],
                         "scarica Explicit001": m._cl_errore("ERROR: [youtube] Explicit001: Sign in to confirm you're not a bot. "
                                                             "Use --cookies-from-browser")}
        c = self.nuovo()
        self.gira(c)
        self.assertEqual(c.riassunto()["blocco"]["tipo"], "robot")
        self.assertEqual(c.riassunto()["fonte"]["in_coda"], 2)
        self.assertEqual(c.riassunto()["totale"], 0)
        # gli errori: blocco, rete o brano che non va (questo si salta)
        self.assertIsInstance(m._cl_errore("ERROR: unable to download video data: HTTP Error 429: Too Many Requests"), m.Bloccato)
        self.assertIsInstance(m._cl_errore("ERROR: [youtube] x: The current session has been rate-limited by YouTube"), m.Bloccato)
        self.assertIsInstance(m._cl_errore("ERROR: Unable to download webpage: <urlopen error [Errno 8] nodename nor servname "
                                           "provided, or not known>"), m.Rete)
        self.assertIsInstance(m._cl_errore("ERROR: [youtube] x: Video unavailable"), RuntimeError)
        self.assertNotIsInstance(m._cl_errore("ERROR: [youtube] x: Video unavailable"), (m.Bloccato, m.Rete))
        # un brano che non si scarica (video tolto) si salta, il lavoro continua
        self.risposte["scarica Explicit001"] = m._cl_errore("ERROR: [youtube] Explicit001: Video unavailable")
        c.riprova_adesso()
        self.assertEqual(self.gira(c), 3600)
        self.assertEqual(c.riassunto()["totale"], 1)
        self.assertEqual(self.scaricati[-1], "Olly0000001")

    def test_spazio_e_rete(self):
        m = self.m
        self.pagine["prova"] = [("Eminem", "Without Me")]
        self.risposte = {"Eminem Without Me": [risultato("Explicit001", "Without Me", "Eminem", explicit=True)]}
        self.libero = 150 * 1024 ** 3   # sotto i 200 GB: fermo
        c = self.nuovo()
        self.assertEqual(self.gira(c), 1800)
        self.assertIn("200 GB", c.riassunto()["pausa"]["motivo"])
        self.assertEqual((self.letture, self.ricerche), ([], []))
        self.libero = None   # disco scollegato
        self.ora.t += 1801
        self.assertEqual(self.gira(c), 1800)
        self.assertIn("raggiungibile", c.riassunto()["pausa"]["motivo"])
        self.libero = 7 * 1024 ** 4
        self.ora.t += 1801
        self.risposte["Eminem Without Me"] = m.Rete("timed out")
        self.assertEqual(self.gira(c), 600)   # Internet o YouTube che non rispondono: il brano resta, si riprova
        self.assertIn("non rispondono", c.riassunto()["pausa"]["motivo"])
        self.assertIsNone(c.riassunto()["blocco"])
        self.risposte["Eminem Without Me"] = [risultato("Explicit001", "Without Me", "Eminem", explicit=True)]
        self.ora.t += 601
        self.assertEqual(self.gira(c), 3600)
        self.assertEqual(self.scaricati, ["Explicit001"])

    def test_fonte_che_non_si_legge(self):
        fonti = [{"id": "uno", "nome": "Prima", "tipo": "finta"}, {"id": "due", "nome": "Seconda", "tipo": "finta"},
                 {"id": "tre", "nome": "Terza", "tipo": "finta", "quanti": 1}]
        self.pagine = {"uno": RuntimeError("il sito ha risposto 503"), "due": [],
                       "tre": [("Olly", "Balorda nostalgia"), ("Eminem", "Without Me")]}
        self.risposte = {"Olly Balorda nostalgia": [risultato("Olly0000001", "Balorda nostalgia", "Olly")]}
        c = self.nuovo(fonti)
        self.assertEqual(self.gira(c), 3600)
        self.assertEqual(self.ricerche, ["Olly Balorda nostalgia"])   # «quanti»: solo il primo della terza
        esiti = {f["id"]: f for f in c.riassunto()["classifiche"]}
        self.assertEqual((esiti["uno"]["esito"], esiti["due"]["esito"], esiti["tre"]["esito"]), ("errore", "errore", "ok"))
        self.assertIn("503", esiti["uno"]["errore"])
        self.assertIn("cambiato", esiti["due"]["errore"])
        # una fonte che non si è letta si riprova il giorno dopo, non dopo una settimana
        self.pagine["uno"] = [("Olly", "Balorda nostalgia")]
        self.ora.t += 86401
        self.gira(c)
        self.assertEqual(self.letture, ["uno", "due", "tre", "uno", "due"])

    def test_playlist_senza_fine(self):
        m = self.m
        f0, f1, f2 = m.CL_FILTRI_PLAYLIST
        spotify = [("RapCaviar", "rap e hip hop", "37i9dQZF1DX0XUsuxWHRQd"), ("Today's Top Hits", "pop", "37i9dQZF1DXcBWIGoYBM5M")]
        generi = [("pop", "pop hits"), ("rap italiano", "rap italiano")]
        self.playlist = {("pop hits", f0, ""): ([{"id": "VLpop1", "nome": "Pop 1", "viste": 0}], "SEGUITO"),
                         ("pop hits", f1, ""): ([{"id": "VLpop2", "nome": "Pop 2", "viste": 900}], ""),
                         ("rap italiano", f0, ""): ([{"id": "VLrap1", "nome": "Rap 1", "viste": 0}], ""),
                         ("pop hits", f0, "SEGUITO"): ([{"id": "VLpop3", "nome": "Pop 3", "viste": 0}], "")}
        self.predefinito = [("Olly", "Balorda nostalgia")]
        self.risposte = {"Olly Balorda nostalgia": [risultato("Olly0000001", "Balorda nostalgia", "Olly")]}
        c = self.nuovo(fonti=[], spotify=spotify, generi=generi)
        self.assertEqual(self.gira(c), 3600)
        # una di Spotify e una di YouTube Music a turno; su YouTube Music la prima di ogni genere, poi la seconda…
        self.assertEqual(self.letture, ["sp:37i9dQZF1DX0XUsuxWHRQd", "yt:VLpop1", "sp:37i9dQZF1DXcBWIGoYBM5M", "yt:VLrap1",
                                        "yt:VLpop2", "yt:VLpop3"])
        self.assertIn(("pop hits", f0, "SEGUITO"), self.scoperte)   # le pagine dopo la prima: senza un numero massimo
        self.assertIn(("rap italiano", f2, ""), self.scoperte)      # e anche la ricerca senza filtro
        # le playlist fatte si rileggono dopo un mese (per i brani nuovi), non prima
        self.ora.t += 10 * 86400
        self.gira(c)
        self.assertEqual(len(self.letture), 6)
        self.ora.t += 25 * 86400
        self.gira(c)
        self.assertEqual(len(self.letture), 12)

    def test_videoclip_nel_piano(self):
        m = self.m
        self.pagine["prova"] = [("Eminem", "Without Me")]
        self.risposte = {"Eminem Without Me": [risultato("Explicit001", "Without Me", "Eminem", explicit=True)]}
        c = self.nuovo()
        c.videoclip = lambda artista, titolo: "YVkUvmDQ3HY" if (artista, titolo) == ("Eminem", "Without Me") else ""
        with unittest.mock.patch.object(m, "CLIP", object()):
            self.gira(c)
        with open(c.piano_clip, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"Eminem - Without Me.mp3": {"id": "YVkUvmDQ3HY"}})
        # il piano dei videoclip lo legge insieme a quello principale (che vince se un nome c'è in tutti e due)
        principale = os.path.join(self.base, "piano.json")
        with open(principale, "w", encoding="utf-8") as f:
            json.dump({"Altro - Brano.mp3": {"id": "abcdefghijk"}}, f)
        v = m.Videoclip(principale, os.path.join(self.base, "clip"))
        with unittest.mock.patch.object(m, "PIANI_AGGIUNTI", [c.piano_clip]):
            self.assertEqual(v.leggi_piano(), {"Altro - Brano.mp3": "abcdefghijk", "Eminem - Without Me.mp3": "YVkUvmDQ3HY"})
            with open(principale, "w", encoding="utf-8") as f:
                json.dump({"Eminem - Without Me.mp3": {"id": "Principale1"}}, f)
            os.utime(principale, (time.time() + 5, time.time() + 5))
            self.assertEqual(v.leggi_piano(), {"Eminem - Without Me.mp3": "Principale1"})

    def test_videoclip_fermi_durante_il_blocco(self):
        m = self.m
        b = m.BloccoYouTube(os.path.join(self.base, "blocco.json"))
        b.adesso = self.ora
        with unittest.mock.patch.object(m, "BLOCCO", b), unittest.mock.patch.object(m, "CL", None):
            self.assertFalse(m._videoclip_fermo())
            b.segnala("HTTP Error 429: Too Many Requests")
            self.assertTrue(m._videoclip_fermo())
            self.ora.t += 1800
            self.assertFalse(m._videoclip_fermo())   # senza classifiche, la prova la fa il videoclip
            with unittest.mock.patch.object(m, "CL", object()):
                self.assertTrue(m._videoclip_fermo())   # con le classifiche la fa la ricerca
            b.riuscito()
            self.assertFalse(m._videoclip_fermo())

    def test_gestione_solo_samuele(self):
        m = self.m

        class H:
            _chi = {"ruolo": "casa", "persona": None, "chiave": None}
        for ruolo in ("casa", "invitato"):
            H._chi = dict(H._chi, ruolo=ruolo)
            with self.assertRaises(m.ErroreExtra) as e:
                m._gestione(H(), "GET", ["classifiche"], {})
            self.assertEqual(e.exception.codice, 403)
        for p in ("/api/extra/gestione/classifiche", "/api/extra/gestione/classifiche/riprova"):
            self.assertIsNone(m.PERMESSE_INVITATI.match(p), p)   # agli invitati non arriva nemmeno


class FintoWeb(http.server.BaseHTTPRequestHandler):
    """Le pagine delle classifiche (indirizzo del sito + percorso) e YouTube Music, finti."""
    blocca = False
    ricerche = []
    PAGINE = {"/kworb.net/spotify/country/it_weekly.html":
              '<table><tr><td class="text mp"><div><a href="../artist/1.html">Eminem</a> - '
              '<a href="../track/1.html">Without Me</a></div></td></tr></table>',
              "/kworb.net/spotify/country/global_weekly.html":
              '<table><tr><td class="text mp"><div><a href="../artist/2.html">Dua Lipa</a> - '
              '<a href="../track/2.html">Houdini</a></div></td></tr></table>'}

    def do_GET(self):
        dati = self.PAGINE.get(self.path, "").encode("utf-8")
        self.send_response(200 if dati else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(dati)))
        self.end_headers()
        self.wfile.write(dati)

    def do_POST(self):
        corpo = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        q = str(corpo.get("query") or "").lower()
        if "/search" in self.path:
            FintoWeb.ricerche.append(q)
        if "/search" in self.path and FintoWeb.blocca:
            stato, d = 429, {"error": {"code": 429, "message": "Too Many Requests"}}
        elif "/search" in self.path and q == "eminem without me":
            stato, d = 200, RISPOSTA_YTM
        elif "/search" in self.path and q == "dua lipa houdini":
            stato, d = 200, {"contents": {"tabbedSearchResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
                "sectionListRenderer": {"contents": [{"musicShelfRenderer": {"contents": [
                    _brano_ytm("HoudiniRmx1", "Houdini (Remix)", ["Dua Lipa"], "Houdini", "3:10", True),
                    _brano_ytm("HoudiniExp1", "Houdini", ["Dua Lipa"], "Radical Optimism", "3:05", True)]}}]}}}}]}}}
        elif "/search" in self.path:
            stato, d = 200, {"contents": {}}
        else:
            stato, d = 404, {"error": {"code": 404}}
        dati = json.dumps(d).encode("utf-8")
        self.send_response(stato)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dati)))
        self.end_headers()
        self.wfile.write(dati)

    def log_message(self, *x):
        pass


class TestClassificheServer(unittest.TestCase):
    """Il server vero con ATLANTE_CLASSIFICHE=1, pagine e YouTube finti, yt-dlp e ffmpeg finti: un brano entra nella
    libreria, YouTube blocca, Samuele lo vede in Gestione e sul riquadro (io), «Riprova adesso» e si riparte."""

    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="atlante-classifiche-srv-")
        cls.c = prepara_cartelle(cls.base)
        cls.web = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FintoWeb)
        threading.Thread(target=cls.web.serve_forever, daemon=True).start()
        finto = "http://127.0.0.1:%d" % cls.web.server_address[1]
        cls.c["youtubei"] = finto
        FintoWeb.blocca, FintoWeb.ricerche = False, []
        # copertine e tutto il resto che andrebbe su Internet: verso un indirizzo dove non risponde nessuno
        morto = "http://127.0.0.1:%d" % porta_libera()
        with unittest.mock.patch.dict(os.environ, {"ATLANTE_CLASSIFICHE": "1", "ATLANTE_CLASSIFICHE_FINTE": finto,
                                                   "http_proxy": morto, "https_proxy": morto, "no_proxy": "127.0.0.1,localhost"}):
            cls.s = Server(cls.base, cls.c).avvia()

    @classmethod
    def tearDownClass(cls):
        cls.s.ferma()
        cls.web.shutdown()
        cls.web.server_close()
        shutil.rmtree(cls.base, ignore_errors=True)

    def stato(self):
        return self.s.json("GET", "/api/extra/gestione/classifiche")

    def test_dalla_classifica_alla_libreria(self):
        r = aspetta(lambda: (lambda x: x if x["totale"] >= 1 else None)(self.stato()), 40, 0.1)
        self.assertIsNotNone(r, "%r\n%s" % (self.stato(), self.s.leggi_log()))
        FintoWeb.blocca = True   # dal prossimo brano YouTube risponde 429
        self.assertEqual((r["ultimi"][0]["titolo"], r["ultimi"][0]["artista"]), ("Without Me", "Eminem"))
        self.assertEqual(r["ultimi"][0]["fonte"], "Spotify Top 50 Italia")
        self.assertTrue(os.path.isfile(os.path.join(self.c["scaricati"], "Eminem - Without Me.mp3")))
        with open(os.path.join(self.c["finto"], "ffmpeg.log"), encoding="utf-8") as f:
            tag = json.loads(f.read().strip().splitlines()[-1])["tag"]
        self.assertEqual((tag["ITUNESADVISORY"], tag["purl"]), ("1", "https://music.youtube.com/watch?v=tqxRidAWER8"))
        cat = self.s.json("GET", "/api/musica/catalogo")["brani"]
        self.assertIn(("Without Me", "Eminem"), [(x["title"], x["artist"]) for x in cat])
        # YouTube blocca: fermo, avviso in Gestione e nel riquadro di «Altro»
        r = aspetta(lambda: (lambda x: x if x["blocco"] else None)(self.stato()), 40, 0.1)
        self.assertIsNotNone(r, self.s.leggi_log())
        self.assertEqual(r["blocco"]["tipo"], "rallenta")
        self.assertEqual(self.s.json("GET", "/api/extra/io")["nuove"]["youtube_bloccato"], 1)
        n = len(FintoWeb.ricerche)
        time.sleep(1)
        self.assertEqual(len(FintoWeb.ricerche), n)   # fermo davvero: nessuna richiesta
        # «Riprova adesso»: una prova sola; il blocco è finito, si riparte col brano rimasto in coda (la versione giusta)
        FintoWeb.blocca = False
        self.assertEqual(self.s.json("POST", "/api/extra/gestione/classifiche/riprova", {}), {"ok": True})
        r = aspetta(lambda: (lambda x: x if x["totale"] >= 2 else None)(self.stato()), 40, 0.1)
        self.assertIsNotNone(r, "%r\n%s" % (self.stato(), self.s.leggi_log()))
        self.assertIsNone(r["blocco"])
        self.assertTrue(os.path.isfile(os.path.join(self.c["scaricati"], "Dua Lipa - Houdini.mp3")))
        self.assertNotIn("youtube_bloccato", self.s.json("GET", "/api/extra/io").get("nuove", {}))
        # le fonti che non esistono qui (404) si saltano e lo si dice
        esiti = {f["id"]: f["esito"] for f in self.stato()["classifiche"]}
        self.assertEqual((esiti["spotify-it"], esiti["spotify-mondo"]), ("ok", "ok"))
        aspetta(lambda: self.stato()["classifiche"][-1]["esito"], 20, 0.2)
        self.assertEqual({f["id"]: f["esito"] for f in self.stato()["classifiche"]}["billboard"], "errore")
        # le scritture della Gestione: solo dalla stessa origine
        self.s.json("POST", "/api/extra/gestione/classifiche/riprova", {}, codice=403, intest={"Origin": "https://altro.example"})
        self.s.json("GET", "/api/extra/gestione/classifiche/nessuna", codice=404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
