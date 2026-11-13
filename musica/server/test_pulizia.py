#!/usr/bin/env python3
"""Prove della pulizia audio (pulizia_audio.py). Girano da sole (python3 -I test_pulizia.py) e dentro test_extra.py, che
l'aggiornamento automatico del Mac mini (aggiorna.sh) lancia prima di installare: se una prova non passa la versione
nuova non si installa e nessun brano della libreria si tocca. Le prove con ffmpeg vero si saltano dove ffmpeg non c'è."""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest

QUI = os.path.dirname(os.path.abspath(__file__))


def _carica(nome, file):
    spec = importlib.util.spec_from_file_location(nome, os.path.join(QUI, file))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


PA = _carica("pulizia_audio_prove", "pulizia_audio.py")


# ---------- MP3 finti, costruiti bit per bit ----------

class Bit:
    def __init__(self):
        self.bit = []

    def metti(self, v, n):
        self.bit += [(v >> (n - 1 - k)) & 1 for k in range(n)]

    def byte(self):
        b = self.bit + [0] * (-len(self.bit) % 8)
        return bytes(int("".join(map(str, b[k:k + 8])), 2) for k in range(0, len(b), 8))


def frame1(guadagni, parti=None, crc=False, stereo=True):
    """Un frame MPEG-1 Layer III (128 kbps, 44100 Hz, 417 byte): global_gain e part2_3_length di ogni granulo."""
    can = 2 if stereo else 1
    h = bytes([0xFF, 0xFA if crc else 0xFB, 0x90, 0x44 if stereo else 0xC4])
    s = Bit()
    s.metti(0, 9)                      # main_data_begin
    s.metti(0, 3 if stereo else 5)     # private_bits
    s.metti(0, 4 * can)                # scfsi
    for k in range(2 * can):
        s.metti((parti or [100] * 4)[k], 12)   # part2_3_length
        s.metti(0, 9)                          # big_values
        s.metti(guadagni[k], 8)                # global_gain
        s.metti(0, 30)                         # il resto dei 59 bit
    lato = s.byte()
    assert len(lato) == (32 if stereo else 17)
    testa = h + (PA.crc16(h[2:4] + lato).to_bytes(2, "big") if crc else b"") + lato
    return testa + bytes(417 - len(testa))


def frame2(guadagno, parte=100):
    """Un frame MPEG-2 Layer III mono (64 kbps, 22050 Hz, 208 byte): un granulo."""
    h = bytes([0xFF, 0xF3, 0x80, 0xC4])
    s = Bit()
    s.metti(0, 8)       # main_data_begin
    s.metti(0, 1)       # private_bits
    s.metti(parte, 12)
    s.metti(0, 9)
    s.metti(guadagno, 8)
    s.metti(0, 34)      # il resto dei 63 bit
    lato = s.byte()
    assert len(lato) == 9
    return h + lato + bytes(208 - 4 - len(lato))


def guadagni(b):
    """I global_gain di ogni frame (stereo MPEG-1 o mono MPEG-2), anche dei granuli senza audio."""
    out = []
    for f in PA.frame_mp3(b):
        si, _ = PA._info_laterale(f)
        if f[2]:
            out.append([PA._bit(b, si * 8 + 20 + k * 59 + 21, 8) for k in range(4)])
        else:
            out.append([PA._bit(b, si * 8 + 9 + 21, 8)])
    return out


def id3_frame(fid, dati):
    return fid + len(dati).to_bytes(4, "big") + b"\x00\x00" + dati


def id3(*frame, riempimento=64, flag=0):
    corpo = b"".join(frame) + bytes(riempimento)
    n = len(corpo)
    return b"ID3\x03\x00" + bytes([flag, (n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127]) + corpo


def ape(*voci):
    corpo = b"".join(len(v).to_bytes(4, "little") + bytes(4) + k + b"\x00" + v for k, v in voci)
    return corpo + b"APETAGEX" + (2000).to_bytes(4, "little") + (len(corpo) + 32).to_bytes(4, "little") + \
        len(voci).to_bytes(4, "little") + bytes(12)


ID3V1 = b"TAG" + b"Titolo vecchio".ljust(125, b"\x00")


class TestPuliziaMp3(unittest.TestCase):
    def test_crc(self):
        self.assertEqual(PA.crc16(b"123456789"), 0xAEE7)   # CRC-16/CMS, quello dei frame MPEG

    def test_guadagno_senza_ricodifica_e_ritorno(self):
        tag = id3(id3_frame(b"TIT2", b"\x00Prova"))
        xing = bytearray(frame1([0] * 4, parti=[0] * 4))
        xing[36:40] = b"Xing"
        dati = tag + bytes(xing) + frame1([150, 151, 152, 153]) + frame1([140, 141, 142, 143], crc=True) + \
            b"spazzatura" + frame1([120, 121, 122, 123]) + frame1([110, 111, 112, 113]) + ID3V1
        b = bytearray(dati)
        self.assertEqual(PA.guadagno_mp3(b, 2), 4)   # il frame Xing (il riassunto, senza musica) resta com'è
        self.assertEqual(guadagni(b), [[0, 0, 0, 0], [152, 153, 154, 155], [142, 143, 144, 145], [122, 123, 124, 125],
                                       [112, 113, 114, 115]])
        self.assertEqual(bytes(b[:len(tag)]), tag)              # tag in testa uguali
        self.assertEqual(bytes(b[-128:]), ID3V1)                 # e in coda
        self.assertIn(b"spazzatura", bytes(b))                   # i byte che non sono frame restano
        f = PA.frame_mp3(b)[2]
        self.assertTrue(f[4])
        self.assertEqual(PA._crc_frame(b, f), int.from_bytes(b[f[0] + 4:f[0] + 6], "big"))   # CRC ricalcolato
        self.assertEqual(len(b), len(dati))
        self.assertNotEqual(PA.sha_audio(b), PA.sha_audio(dati))
        PA.guadagno_mp3(b, -2)
        self.assertEqual(bytes(b), dati)                         # si torna indietro al bit

    def test_crc_sbagliato_resta_sbagliato(self):
        f = bytearray(frame1([150] * 4, crc=True))
        f[4:6] = b"\x12\x34"                                     # un codificatore che scrive il CRC male
        dati = bytes(f) + frame1([150] * 4)
        b = bytearray(dati)
        PA.guadagno_mp3(b, 3)
        self.assertEqual(bytes(b[4:6]), b"\x12\x34")
        PA.guadagno_mp3(b, -3)
        self.assertEqual(bytes(b), dati)

    def test_granuli_senza_audio_e_limiti(self):
        dati = frame1([250, 7, 250, 7], parti=[100, 0, 100, 0]) * 2
        b = bytearray(dati)
        with self.assertRaises(ValueError):                      # 250 + 6 non sta in 8 bit: niente si cambia
            PA.guadagno_mp3(b, 6)
        self.assertEqual(bytes(b), dati)
        PA.guadagno_mp3(b, -10)                                  # i granuli vuoti (7) non si toccano: niente errore
        self.assertEqual(guadagni(b), [[240, 7, 240, 7]] * 2)

    def test_mpeg2_mono(self):
        dati = id3(id3_frame(b"TPE1", b"\x00Atlante")) + frame2(130) + frame2(131) + frame2(132, parte=0)
        b = bytearray(dati)
        self.assertEqual(PA.guadagno_mp3(b, 4), 2)
        self.assertEqual(guadagni(b), [[134], [135], [132]])
        PA.guadagno_mp3(b, -4)
        self.assertEqual(bytes(b), dati)

    def test_non_mp3(self):
        with self.assertRaises(ValueError):
            PA.frame_mp3(bytearray(b"RIFF" + bytes(5000)))
        with self.assertRaises(ValueError):                      # troppa spazzatura tra i frame: meglio non toccarlo
            PA.frame_mp3(bytearray(frame1([150] * 4) * 2 + bytes(9000) + frame1([150] * 4) * 2))

    def test_tag_del_volume(self):
        rg16 = b"\x01\xff\xfe" + "replaygain_album_gain".encode("utf-16-le") + b"\x00\x00" + b"\xff\xfe" + \
            "-1 dB".encode("utf-16-le")
        tag = id3(id3_frame(b"TIT2", b"\x00Prova"), id3_frame(b"TXXX", b"\x00REPLAYGAIN_TRACK_GAIN\x00-3.2 dB"),
                  id3_frame(b"COMM", b"\x00engiTunNORM\x00 000001F4 000001F4"), id3_frame(b"TXXX", rg16),
                  id3_frame(b"RVA2", b"traccia\x00\x01\x00\x00\x00"), id3_frame(b"TPE1", b"\x00Atlante"),
                  id3_frame(b"TXXX", b"\x00COMPILATION\x001"))
        out = PA.id3_senza_volume(tag)
        self.assertEqual(len(out), len(tag))                      # stessa lunghezza: il riempimento al posto dei frame
        for via in (b"REPLAYGAIN", b"iTunNORM", "replaygain".encode("utf-16-le"), b"RVA2"):
            self.assertNotIn(via, out)
        for resta in (b"Prova", b"Atlante", b"COMPILATION"):
            self.assertIn(resta, out)
        strano = id3(id3_frame(b"TXXX", b"\x00REPLAYGAIN_TRACK_GAIN\x00-3.2 dB"), flag=0x80)   # unsynchronisation
        self.assertEqual(PA.id3_senza_volume(strano), strano)

    def test_tag_attorno_all_audio(self):
        tag = id3(id3_frame(b"TIT2", b"\x00Prova"))
        audio = frame1([150] * 4) * 3
        solo_volume = ape((b"REPLAYGAIN_TRACK_GAIN", b"-3 dB"), (b"MP3GAIN_MINMAX", b"100,200"))
        testa, coda = PA.tag_mp3(tag + audio + solo_volume + ID3V1)
        self.assertEqual((testa, coda), (tag, ID3V1))             # l'APE di mp3gain va via
        altro = ape((b"REPLAYGAIN_TRACK_GAIN", b"-3 dB"), (b"Artist", b"Atlante"))
        self.assertEqual(PA.tag_mp3(tag + audio + altro + ID3V1), (tag, altro + ID3V1))
        self.assertEqual(PA.code_mp3(tag + audio + altro + ID3V1)[0], len(tag + audio))


# ---------- quello che dice ffmpeg e cosa se ne decide ----------

REGISTRO = """Input #0, mp3, from 'error song.mp3':
  Metadata:
    title           : Trial and Error
[mp3float @ 0x7f8] Header missing
[Parsed_astats_1 @ 0x6000] Channel: 1
[Parsed_astats_1 @ 0x6000] DC offset: 0.012000
[Parsed_astats_1 @ 0x6000] Peak level dB: -0.100000
[Parsed_astats_1 @ 0x6000] Bit depth: 32/32
[Parsed_astats_1 @ 0x6000] Number of samples: 441000
[Parsed_astats_1 @ 0x6000] Channel: 2
[Parsed_astats_1 @ 0x6000] DC offset: -0.020000
[Parsed_astats_1 @ 0x6000] Number of samples: 441000
[Parsed_astats_1 @ 0x6000] Overall
[Parsed_astats_1 @ 0x6000] DC offset: 0.020000
[Parsed_astats_1 @ 0x6000] Peak level dB: -0.050000
[Parsed_astats_1 @ 0x6000] Flat factor: -inf
[Parsed_astats_1 @ 0x6000] Peak count: 12.000000
[Parsed_astats_1 @ 0x6000] Number of samples: 441000
[Parsed_ebur128_2 @ 0x6001] Summary:

  Integrated loudness:
    I:         -19.3 LUFS
    Threshold: -29.8 LUFS

  Loudness range:
    LRA:         5.2 LU
    Threshold: -40.1 LUFS
    LRA low:   -23.4 LUFS
    LRA high:  -17.9 LUFS

  True peak:
    Peak:        0.4 dBFS
"""

MP3 = {"codec": "mp3", "hz": 44100, "durata": 200.0, "video": 0}


def misure(i, tp, **k):
    return dict({"i": i, "tp": tp, "picco": tp, "dc": 0.0, "piatto": None, "volte": 0, "campioni": 0, "errori": 0}, **k)


class TestPuliziaDecisioni(unittest.TestCase):
    def test_registro_di_ffmpeg(self):
        m = PA.leggi_misure(REGISTRO)
        self.assertEqual((m["i"], m["lra"], m["tp"], m["picco"], m["dc"], m["volte"], m["campioni"], m["errori"]),
                         (-19.3, 5.2, 0.4, -0.05, 0.02, 12.0, 441000, 1))   # «Error» nel titolo non è un errore
        self.assertEqual(m["piatto"], float("-inf"))
        self.assertEqual(PA.riassunto(dict(m, tp=float("inf"))), {"i": -19.3, "tp": None, "lra": 5.2, "dc": 0.02})
        self.assertIsNone(PA.leggi_misure("niente")["i"])

    def test_mp3(self):
        p = lambda m: PA.piano(m, MP3, ".mp3")   # noqa: E731
        self.assertEqual(p(misure(-14.4, -1.2))["azione"], "nulla")
        x = p(misure(-20.0, -6.0))   # +6 servirebbero, ma i picchi lasciano +5: 3 passi (+4,5 dB)
        self.assertEqual((x["azione"], x["passi"], x["guadagno"]), ("guadagno", 3, 4.5))
        x = p(misure(-8.0, 0.6))
        self.assertEqual((x["azione"], x["passi"]), ("guadagno", -4))
        self.assertEqual(len(x["motivi"]), 2)                     # volume alto e picchi oltre lo zero
        x = p(misure(-18.0, -0.5))
        self.assertEqual((x["azione"], x["motivo"]), ("nulla", "più forte distorcerebbe: lasciato com'è"))
        x = p(misure(-14.2, 0.3))    # volume giusto ma picchi che distorcono: un passo giù
        self.assertEqual((x["azione"], x["passi"]), ("guadagno", -1))
        x = p(misure(-40.0, -30.0))  # mai più di +10 dB
        self.assertEqual(x["passi"], 6)
        x = p(misure(-14.0, -3.0, dc=0.02))   # DC forte: si ricodifica, il volume resta
        self.assertEqual((x["azione"], x["guadagno"], x["picco_max"]), ("ricodifica", 0.0, PA.PICCO_CON_PERDITA))
        self.assertEqual(PA.piano(misure(-20.0, -6.0), MP3, ".mp3", ricodifica=True)["azione"], "ricodifica")

    def test_altri_formati(self):
        flac = {"codec": "flac", "hz": 44100, "durata": 200.0, "video": 0}
        x = PA.piano(misure(-9.0, 0.0, volte=500, piatto=-3.0), flac, ".flac")   # tagliato
        self.assertEqual((x["azione"], x["declip"], x["guadagno"]), ("ricodifica", True, -5.0))
        x = PA.piano(misure(-14.3, -3.0, dc=0.002), flac, ".flac")
        self.assertEqual((x["azione"], x["guadagno"]), ("ricodifica", 0.0))
        aac = {"codec": "aac", "hz": 44100, "durata": 200.0, "video": 0, "kbps": 256}
        self.assertEqual(PA.piano(misure(-15.5, -2.0), aac, ".m4a")["azione"], "nulla")   # entro 2 LU: niente codifica
        self.assertEqual(PA.piano(misure(-18.0, -6.0), aac, ".m4a")["azione"], "ricodifica")
        for info, est in ((dict(MP3, codec="vorbis"), ".ogg"), (dict(aac), ".m4b"), (dict(aac, video=1), ".mp4"),
                          (dict(MP3, durata=2.0), ".mp3")):
            self.assertEqual(PA.piano(misure(-20.0, -6.0), info, est)["azione"], "salta", est)
        self.assertEqual(PA.piano(misure(-70.0, -60.0), MP3, ".mp3")["azione"], "salta")   # silenzio


# ---------- dischi: archivio, sostituzione, giornale; registro e stato ----------

class TestPuliziaDischi(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="atlante-pulizia-")
        self.radice = os.path.join(self.base, "Musica")
        os.makedirs(os.path.join(self.radice, "Album"))
        self.p = os.path.join(self.radice, "Album", "brano.flac")
        with open(self.p, "wb") as f:
            f.write(b"originale" * 100)
        os.utime(self.p, (1700000000, 1700000000))
        self.nuovo = os.path.join(self.base, "nuovo.flac")
        with open(self.nuovo, "wb") as f:
            f.write(b"pulito" * 100)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_sostituzione_con_archivio(self):
        st0 = os.stat(self.p)
        f1 = PA.metti_al_posto(self.p, st0, self.nuovo, PA._sha1_file(self.nuovo), self.radice, salva_originale=True)
        with open(self.p, "rb") as f:
            self.assertEqual(f.read(), b"pulito" * 100)
        a = os.path.join(self.radice, ".atlante-originali", "Album", "brano.flac")
        with open(a, "rb") as f:
            self.assertEqual(f.read(), b"originale" * 100)
        self.assertEqual(int(os.stat(self.p).st_mtime), 1700000001)   # un secondo dopo: la data di «aggiunto» resta
        self.assertEqual(f1, "600:1700000001")
        self.assertEqual([n for n in os.listdir(os.path.dirname(self.p)) if n.startswith(".")], [])
        st1 = os.stat(self.p)
        with self.assertRaises(PA.ErroreBrano):                   # mai sopra un originale che c'è già
            PA.metti_al_posto(self.p, st1, self.nuovo, PA._sha1_file(self.nuovo), self.radice, salva_originale=True)

    def test_file_cambiato_nel_frattempo(self):
        st0 = os.stat(self.p)
        with open(self.p, "ab") as f:
            f.write(b"!")
        with self.assertRaises(PA.Cambiato):
            PA.metti_al_posto(self.p, st0, self.nuovo, PA._sha1_file(self.nuovo), self.radice, salva_originale=True)
        self.assertFalse(os.path.exists(os.path.join(self.radice, ".atlante-originali", "Album", "brano.flac")))
        with open(self.p, "rb") as f:
            self.assertTrue(f.read().startswith(b"originale"))
        with self.assertRaises(PA.ErroreMacchina):                # una copia non uguale non va al posto dell'originale
            PA.metti_al_posto(self.p, os.stat(self.p), self.nuovo, "0" * 40)
        self.assertEqual([n for n in os.listdir(os.path.dirname(self.p)) if n.startswith(".")], [])

    def test_giornale(self):
        self.assertIsNone(PA.dal_giornale(self.radice, "Album/brano.mp3"))
        PA.scrivi_giornale(self.radice, {"p": "Album/brano.mp3", "passi": 2, "prima": "a", "dopo": "b"})
        PA.scrivi_giornale(self.radice, {"p": "Album/altro.mp3", "passi": -1, "prima": "c", "dopo": "d"})
        PA.scrivi_giornale(self.radice, {"p": "Album/brano.mp3", "passi": 3, "prima": "e", "dopo": "f"})
        self.assertEqual(PA.dal_giornale(self.radice, "Album/brano.mp3")["passi"], 3)

    def test_registro_e_stato(self):
        voci = [("a" * 16, {"_f": "10:1", "added": 3}, os.path.join(self.radice, "a.mp3"), self.radice),
                ("b" * 16, {"_f": "10:2", "added": 2}, os.path.join(self.radice, "b.mp3"), self.radice),
                ("c" * 16, {"_f": "10:3", "added": 1}, os.path.join(self.radice, "c.mp3"), self.radice),
                ("d" * 16, {"_f": "10:9", "added": 0}, os.path.join(self.radice, "d.mp3"), self.radice)]
        cache = os.path.join(self.base, "cache")
        p = PA.Pulizia(cache, lambda: voci, acceso_di_serie=False, programma=lambda n: None)
        self.assertFalse(p.acceso)
        ora = int(time.time())
        p.registro = {"a" * 16: {"e": "migliorato", "f": "10:5", "v": PA.VERSIONE, "q": ora},   # file cambiato dopo: basta
                      "b" * 16: {"e": "buono", "f": "10:2", "v": PA.VERSIONE, "q": ora},
                      "c" * 16: {"e": "errore", "f": "10:3", "v": PA.VERSIONE, "q": ora, "n": 1, "err": "x"},
                      "d" * 16: {"e": "buono", "f": "10:4", "v": PA.VERSIONE, "q": ora}}        # file cambiato: si riascolta
        self.assertEqual([x[0] for x in p._prossimi(10)], ["d" * 16])
        s = p.stato()
        self.assertEqual((s["conti"]["migliorati"], s["conti"]["buoni"], s["conti"]["errori"], s["da_fare"], s["totale"]),
                         (1, 1, 1, 1, 4))
        self.assertFalse(s["ffmpeg"])
        json.dumps(s, allow_nan=False)                            # niente inf né nan per l'app
        p.registro["c" * 16]["q"] = ora - PA.RIPROVA_ERRORE - 1  # il giorno dopo l'errore si riprova
        self.assertEqual([x[0] for x in p._prossimi(10)], ["c" * 16, "d" * 16])
        p.imposta(True)   # il lavoro parte (i file non ci sono: non tocca niente) e si ferma con chiudi
        p.chiudi()
        p.filo.join(15)
        self.assertFalse(p.filo.is_alive())
        self.assertTrue(PA.Pulizia(cache, lambda: voci, acceso_di_serie=False).acceso)   # l'interruttore resta
        with self.assertRaises(PA.ErroreBrano):
            p.ripristina("e" * 16)


# ---------- tutto il giro con ffmpeg vero ----------

def _ffmpeg_vero():
    for d in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
        ff, fp = os.path.join(d, "ffmpeg"), os.path.join(d, "ffprobe")
        if os.access(ff, os.X_OK) and os.access(fp, os.X_OK):
            break
    else:
        ff, fp = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ff or not fp:
        return None
    try:
        r = subprocess.run([ff, "-hide_banner", "-encoders"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    cod = r.stdout.decode("utf-8", "replace")
    return (ff, fp) if r.returncode == 0 and " libmp3lame " in cod and " flac " in cod else None


class TestPuliziaFfmpeg(unittest.TestCase):
    """Un MP3 troppo piano (guadagno senza ricodifica) e un FLAC con il DC (ricodifica con archivio), puliti con ffmpeg
    vero, controllati e ripristinati."""

    @classmethod
    def setUpClass(cls):
        cls.ff = _ffmpeg_vero()
        if not cls.ff:
            raise unittest.SkipTest("ffmpeg vero (con libmp3lame e flac) non c'è")
        cls.ML = _carica("musica_libreria_pulizia", "musica_libreria.py")   # il lettore dei tag della libreria

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="atlante-pulizia-ff-")
        self.radice = os.path.join(self.base, "Musica")
        os.makedirs(self.radice)
        self.cambi = []
        self.voci = []
        self.p = PA.Pulizia(os.path.join(self.base, "cache"), lambda: self.voci, sostituito=self._sostituito,
                            leggi_tag=self.ML.leggi_tag, programma=lambda n: dict(zip(("ffmpeg", "ffprobe"), self.ff)).get(n),
                            acceso_di_serie=False, spazio_min=0, spazio_cache=0)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _sostituito(self, i, p, vecchia, nuova):
        self.cambi.append((i, p, vecchia, nuova))

    def _genera(self, nome, sorgente, args):
        p = os.path.join(self.radice, nome)
        r = subprocess.run([self.ff[0], "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
                            sorgente] + args + [p], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, timeout=120)
        if r.returncode != 0:
            self.skipTest("ffmpeg non genera il brano di prova: %s" % r.stderr.decode("utf-8", "replace")[-300:])
        os.utime(p, (1700000000, 1700000000))
        i = hashlib.sha1(p.encode()).hexdigest()[:16]
        self.voci.append((i, {"_f": PA.firma(os.stat(p)), "title": nome, "artist": "Atlante", "added": 1}, p, self.radice))
        return i, p

    def test_mp3_troppo_piano(self):
        i, p = self._genera("piano.mp3", "aevalsrc=0.01*sin(2*PI*440*t)|0.01*sin(2*PI*440*t):s=44100:d=8",
                            ["-c:a", "libmp3lame", "-b:a", "192k", "-id3v2_version", "3", "-metadata", "title=Piano",
                             "-metadata", "artist=Atlante", "-metadata", "album=Prove"])
        prima = PA._sha1_file(p)
        self.p._uno(*self.voci[0])
        r = self.p.registro[i]
        self.assertEqual((r["e"], r.get("modo"), r.get("passi")), ("migliorato", "guadagno", 6), r)   # +9 dB (al più +10)
        self.assertAlmostEqual(r["dopo"]["i"], r["prima"]["i"] + 9.0, delta=0.3)
        self.assertFalse(os.path.exists(os.path.join(self.radice, ".atlante-originali", "piano.mp3")))   # nessuna copia
        t = self.ML.leggi_tag(p)
        self.assertEqual((t.get("title"), t.get("artist"), t.get("album")), ("Piano", "Atlante", "Prove"))
        self.assertEqual(int(os.stat(p).st_mtime), 1700000001)
        self.assertEqual(self.cambi, [(i, p, self.voci[0][1]["_f"], PA.firma(os.stat(p)))])
        self.assertEqual(r["f"], PA.firma(os.stat(p)))
        self.assertEqual(self.p.stato()["conti"]["migliorati"], 1)
        self.assertEqual(self.p._prossimi(5), [])                 # mai due volte
        self.p.ripristina(i)                                     # il guadagno disfatto: il file di prima, al bit
        self.assertEqual(PA._sha1_file(p), prima)
        self.assertEqual(int(os.stat(p).st_mtime), 1700000000)
        self.assertEqual(self.p.registro[i]["e"], "ripristinato")
        self.assertEqual(self.p._prossimi(5), [])                 # ripristinato: non si pulisce più

    def test_flac_con_dc(self):
        i, p = self._genera("dc.flac", "aevalsrc=0.25*sin(2*PI*440*t)+0.03|0.25*sin(2*PI*440*t)+0.03:s=44100:d=6",
                            ["-c:a", "flac", "-sample_fmt", "s16", "-metadata", "title=Continua",
                             "-metadata", "artist=Atlante"])
        prima = PA._sha1_file(p)
        self.p._uno(*self.voci[0])
        r = self.p.registro[i]
        self.assertEqual((r["e"], r.get("modo")), ("migliorato", "ricodifica"), r)
        self.assertGreater(r["prima"]["dc"], 0.02)
        self.assertLess(r["dopo"]["dc"], 0.001)
        a = os.path.join(self.radice, ".atlante-originali", "dc.flac")
        self.assertEqual(PA._sha1_file(a), prima)                # l'originale identico in archivio
        self.assertEqual(self.ML.leggi_tag(p).get("title"), "Continua")
        self.p.ripristina(i)
        self.assertEqual(PA._sha1_file(p), prima)
        self.assertFalse(os.path.exists(a))
        self.assertEqual(len(self.cambi), 2)                      # pulito e ripristinato: la firma cambia due volte

    def test_gia_giusto(self):
        i, p = self._genera("giusto.mp3", "aevalsrc=0.2*sin(2*PI*440*t)|0.2*sin(2*PI*440*t):s=44100:d=6",
                            ["-c:a", "libmp3lame", "-b:a", "192k"])
        prima = PA._sha1_file(p)
        r = self.p.tratta(p, self.radice)
        if r["e"] != "buono":   # il tono è a circa -14 LUFS: entro 1 LU non si tocca (altrimenti cambia di un passo)
            self.assertEqual(r.get("passi") in (-1, 1), True, r)
        else:
            self.assertEqual(PA._sha1_file(p), prima)


if __name__ == "__main__":
    unittest.main(verbosity=2)
