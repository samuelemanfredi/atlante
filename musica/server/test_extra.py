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
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import quote as _quote

QUI =os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(QUI, "musica_libreria.py")

FINTO_YTDLP = r'''
import json, os, sys, time
a = sys.argv[1:]
if "--version" in a:
    print("2099.01.01")
    sys.exit(0)
url = a[-1]
nome = url.rstrip("/").rsplit("/", 1)[-1]
with open(os.path.join(os.environ["FINTO_DIR"], "ytdlp-%s.json" % nome), "w") as f:
    json.dump({"args": a, "pid": os.getpid()}, f)
home = [v[5:] for i, v in enumerate(a) if i and a[i - 1] == "-P" and v.startswith("home:")][0]
stampe = [v for i, v in enumerate(a) if i and a[i - 1] in ("--print", "-O")]

def stampa(quando, **campi):
    for s in stampe:
        w, _, t = s.partition(":")
        if w == quando:
            for k, v in campi.items():
                t = t.replace("%%(%s)s" % k, v)
            print(t, flush=True)

print("[youtube] %s: Downloading webpage" % nome, flush=True)
if nome == "errore":
    print("ERROR: [youtube] errore: Video non disponibile", flush=True)
    sys.exit(1)
stampa("before_dl", title="Video di prova")
print("[download]   0.0% of   10.00MiB at    1.00MiB/s ETA 00:10", flush=True)
print("[download]  42.0% of   10.00MiB at    1.00MiB/s ETA 00:05", flush=True)
time.sleep(60 if nome == "lento" else 1.5)
p = os.path.join(home, "Video di prova [%s].%s" % (nome, "m4a" if "-x" in a else "mp4"))
with open(p, "wb") as f:
    f.write(bytes(range(256)) * 4)
print("[download] 100% of   10.00MiB in 00:00:01 at 10.00MiB/s", flush=True)
stampa("after_move", filepath=p)
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
    def __init__(self, base, cartelle):
        self.base, self.c = base, cartelle
        self.porta = porta_libera()
        self.p = None

    def avvia(self):
        env = dict(os.environ)
        env["PATH"] = self.c["bin"] + os.pathsep + env.get("PATH", "")
        env["FINTO_DIR"] = self.c["finto"]
        self.log = open(os.path.join(self.base, "server-%d.log" % self.porta), "wb")
        self.p = subprocess.Popen([sys.executable, "-I", SERVER, self.c["musica"], "--porta", str(self.porta),
                                   "--cache", self.c["cache"], "--app", self.c["app"], "--video-dir", self.c["video"],
                                   "--libri-dir", self.c["libri"],
                                   "--ia-indirizzo", "http://127.0.0.1:%d" % self.c["porta_ollama"]],
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
    os.makedirs(os.path.join(c["app"], "x"))
    for nome, testo in (("index.html", "<!doctype html><title>app</title>"), ("x/prova.html", "<!doctype html><p>x"),
                        ("x/kit.css", "body{}")):
        with open(os.path.join(c["app"], nome), "w", encoding="utf-8") as f:
            f.write(testo)
    for nome, codice in (("yt-dlp", FINTO_YTDLP), ("say", FINTO_SAY)):
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
        cls.s = Server(cls.base, cls.c).avvia()

    @classmethod
    def tearDownClass(cls):
        cls.s.ferma()
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
