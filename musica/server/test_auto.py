#!/usr/bin/env python3
"""Prove di «La mia Volkswagen» (auto_vw.py, auto_ponte.py, x/auto.html). Solo libreria standard, senza rete e senza
il cloud Volkswagen: modello dei consumi, calibrazione sui viaggi, rendimento secondo la temperatura, diario (viaggi,
soste, ricariche), scelta delle soste di ricarica con le regole di Samuele, pianificazione con servizi finti, il ponte con
una CarConnectivity finta, le API con un ponte finto e i permessi sul server.

    python3 -I test_auto.py        (girano anche dentro test_extra.py, che le carica: è il file che lancia aggiorna.sh)
"""
import importlib.util
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock

QUI = os.path.dirname(os.path.abspath(__file__))


def carica(nome, file):
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(nome, os.path.join(QUI, file))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


AV = carica("auto_vw_prove", "auto_vw.py")
AUTO = AV.PREDEFINITE["auto"]
REGOLE = AV.PREDEFINITE["regole"]
CAL1 = {"k_guida": 1.0, "k_clima": 1.0, "viaggi": 0, "km": 0.0, "errore_pct": None, "tipici": True}


def aspetta(cond, tempo=20.0, passo=0.05):
    fine = time.time() + tempo
    while time.time() < fine:
        x = cond()
        if x:
            return x
        time.sleep(passo)
    return cond()


def viaggio_finto(km, vel, temp, kg=1.0, kc=1.0, soc_da=90.0, arrotonda=False):
    """Un viaggio registrato che consuma esattamente come il modello con i fattori kg e kc."""
    v = {"km": km, "partenza": 1.0e9, "arrivo": 1.0e9 + km / vel * 3600.0, "temp": temp, "soc_da": soc_da,
         "soc_a": 0.0, "con_ricarica": 0, "durata_stimata": 0, "quota_da": None, "quota_a": None}
    tr, cl, fi, _, _ = AV.componenti(v, AUTO)
    soc_a = soc_da - (kg * tr + kc * cl + fi) / AUTO["capacita_kwh"] * 100.0
    v["soc_a"] = float(round(soc_a)) if arrotonda else soc_a
    return v


def viaggi_finti(kg, kc, arrotonda=False):
    return [viaggio_finto(km, vel, t, kg, kc, arrotonda=arrotonda)
            for t in (-2, 3, 8, 12, 18, 24, 28) for vel in (35, 60, 90, 110) for km in (12, 30, 80)]


class TestAutoModello(unittest.TestCase):
    """La fisica della ID. Buzz: valori sensati, il freddo costa, la musica quasi niente, in discesa si recupera."""

    def cons(self, t, vel, lung=1000.0, musica=True, auto=None):
        return AV.consumo_100(t, vel, lung, auto or AUTO, CAL1, 21.0, musica)

    def test_consumi_tipici(self):
        a90, a120 = self.cons(15, 90), self.cons(15, 120)
        self.assertTrue(17 < a90 < 24, a90)
        self.assertTrue(25 < a120 < 33, a120)
        self.assertGreater(a120, a90)

    def test_il_freddo_costa(self):
        self.assertGreater(self.cons(0, 90, 20), self.cons(15, 90, 20) * 1.12)
        self.assertGreater(self.cons(-5, 90, 20), self.cons(0, 90, 20))

    def test_pompa_di_calore(self):
        con = dict(AUTO, pompa_calore=True)
        self.assertLess(AV.potenza_clima_kw(0, con), AV.potenza_clima_kw(0, AUTO))
        self.assertLess(self.cons(0, 90, 20, auto=con), self.cons(0, 90, 20))

    def test_musica_pesa_poco(self):
        d = self.cons(10, 90, musica=True) - self.cons(10, 90, musica=False)
        self.assertTrue(0 < d < 0.1, d)

    def test_discesa_recupera(self):
        self.assertLess(AV.energia_trazione(10000, 80, -500, 15, AUTO), 0)
        self.assertGreater(AV.energia_trazione(10000, 80, 500, 15, AUTO), AV.energia_trazione(10000, 80, 0, 15, AUTO))

    def test_ricarica_rapida(self):
        m = AV.minuti_ricarica(10, 80, 350, AUTO, 25)
        self.assertTrue(24 < m < 40, m)   # ID. Buzz: circa 30 minuti a batteria calda
        self.assertGreater(AV.minuti_ricarica(10, 80, 350, AUTO, 0), m * 1.5)    # fredda: molto più lenta
        self.assertGreater(AV.minuti_ricarica(10, 80, 50, AUTO, 25), m)         # colonnina da 50 kW
        self.assertEqual(AV.minuti_ricarica(50, 40, 150, AUTO, 25), 0.0)


class TestAutoCalibrazione(unittest.TestCase):
    """Dai viaggi veri tornano i due fattori personali (stile di guida, clima) e le fasce di temperatura."""

    def test_senza_viaggi(self):
        c = AV.calibra([], AUTO)
        self.assertEqual((c["k_guida"], c["k_clima"], c["viaggi"], c["tipici"]), (1.0, 1.0, 0, True))

    def test_ritrova_i_fattori(self):
        c = AV.calibra(viaggi_finti(1.12, 1.4), AUTO)
        self.assertEqual(c["viaggi"], 84)
        self.assertFalse(c["tipici"])
        self.assertAlmostEqual(c["k_guida"], 1.12, delta=0.03)
        self.assertAlmostEqual(c["k_clima"], 1.4, delta=0.1)
        self.assertLess(c["errore_pct"], 3)

    def test_batteria_a_punti_interi(self):
        c = AV.calibra(viaggi_finti(1.12, 1.4, arrotonda=True), AUTO)
        self.assertAlmostEqual(c["k_guida"], 1.12, delta=0.1)
        self.assertAlmostEqual(c["k_clima"], 1.4, delta=0.5)

    def test_viaggi_scartati(self):
        buono = viaggio_finto(30, 60, 10)
        self.assertTrue(AV.viaggio_valido(buono))
        for cambia in ({"km": 3}, {"con_ricarica": 1}, {"soc_a": buono["soc_da"] - 1}, {"soc_a": None},
                       {"arrivo": buono["partenza"]}):
            self.assertFalse(AV.viaggio_valido(dict(buono, **cambia)), cambia)

    def test_rendimento(self):
        r = AV.rendimento(viaggi_finti(1.12, 1.4), AUTO)
        self.assertEqual(r["viaggi_validi"], 84)
        self.assertEqual(len(r["fasce"]), 7)
        curva = {c["t"]: c for c in r["curva"]}
        self.assertEqual(curva[20]["rendimento_pct"], 100)
        self.assertLess(curva[0]["rendimento_pct"], 90)
        self.assertGreater(curva[0]["kwh_100"], curva[20]["kwh_100"])
        fasce = {(f["da"], f["a"]): f for f in r["fasce"]}
        self.assertGreater(fasce[(-5, 0)]["kwh_100"], fasce[(20, 25)]["kwh_100"])
        self.assertGreater(fasce[(20, 25)]["autonomia_100"], fasce[(-5, 0)]["autonomia_100"])


class TestAutoLetture(unittest.TestCase):
    """Dai valori di CarConnectivity ai campi di Atlante; le colonnine di OpenStreetMap."""

    def test_normalizza(self):
        attr = {"drives/primary/type": ["electric", None, None],
                "drives/primary/level": [64, "%", "2026-11-12T10:00:00+00:00"],
                "drives/primary/range": [205, "km", None], "odometer": [12345, "km", "2026-11-12T09:59:00+00:00"],
                "outside_temperature": [9.5, "°C", None], "drives/primary/battery/temperature_min": [11.0, "°C", None],
                "position/latitude": [43.9, None, "2026-11-12T09:00:00+00:00"], "position/longitude": [10.23, None, None],
                "doors/lock_state": ["locked", None, None], "charging/state": ["charging", None, None],
                "charging/power": [11.0, "kW", None], "climatization/state": ["heating", None, None]}
        n = AV.normalizza("V1", attr, {"doors/commands/lock-unlock": True}, 100.0)
        self.assertEqual((n["soc"], n["autonomia"], n["km"], n["est"], n["batt_min"]), (64, 205, 12345, 9.5, 11.0))
        self.assertEqual((n["lat"], n["lon"], n["porte"], n["ricarica"], n["potenza"], n["clima"]),
                         (43.9, 10.23, "locked", "charging", 11.0, "heating"))
        self.assertEqual(n["pos_t"], AV.epoca("2026-11-12T09:00:00Z"))
        self.assertEqual(n["aggiornato"], AV.epoca("2026-11-12T10:00:00+00:00"))
        self.assertEqual(n["comandi"], {"porte": True, "clima": False, "sbrina": False})
        attr["position/latitude"][0] = 0
        attr["position/longitude"][0] = 0
        self.assertIsNone(AV.normalizza("V1", attr, {}, 100.0)["lat"])   # 0,0: nessuna posizione

    def test_colonnine(self):
        c = AV.leggi_colonnina({"amenity": "charging_station", "socket:type2_combo": "2",
                                "socket:type2_combo:output": "50 kW;150 kW", "operator": "Free To X"}, 1.0, 2.0)
        self.assertEqual((c["kw"], c["kw_noto"], c["ionity"], c["operatore"]), (150.0, True, False, "Free To X"))
        c = AV.leggi_colonnina({"amenity": "charging_station", "network": "IONITY"}, 1.0, 2.0)
        self.assertEqual((c["kw"], c["ionity"]), (350.0, True))
        self.assertIsNone(AV.leggi_colonnina({"socket:type2": "2", "socket:type2:output": "22 kW"}, 1, 2))
        self.assertIsNone(AV.leggi_colonnina({"socket:type2_combo": "1", "access": "private"}, 1, 2))
        self.assertEqual(AV.leggi_colonnina({"socket:type2_combo": "1"}, 1, 2)["kw_noto"], False)
        self.assertEqual(AV._kw("150000 W"), 150.0)
        self.assertEqual(AV._kw("22"), 22.0)
        self.assertIsNone(AV._kw(""))


class TestAutoDiario(unittest.TestCase):
    """Soste, viaggi e ricariche ricavati dalle letture (posizione di parcheggio, contachilometri, batteria)."""
    A, B = (43.90, 10.23), (43.95, 10.30)
    T0 = 1800000000.0

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="atlante-auto-diario-")
        self.arch = AV.Archivio(os.path.join(self.base, "diario.sqlite"))
        self.d = AV.Diario(self.arch)

    def tearDown(self):
        self.arch.chiudi()
        shutil.rmtree(self.base, ignore_errors=True)

    @staticmethod
    def n(t, lat=None, lon=None, km=None, soc=None, est=None, pos_t=None, ricarica="off", potenza=None):
        return {"vin": "V1", "t": t, "lat": lat, "lon": lon, "km": km, "soc": soc, "est": est, "pos_t": pos_t,
                "ricarica": ricarica, "potenza": potenza, "tipo_ricarica": "ac"}

    def test_viaggio_visto_partire(self):
        t0, A, B = self.T0, self.A, self.B
        self.d.passo(self.n(t0, *A, km=1000, soc=80, est=10, pos_t=t0 - 3600))
        self.d.passo(self.n(t0 + 300, *A, km=1000, soc=80, est=10, pos_t=t0 - 3600))
        self.d.passo(self.n(t0 + 600, km=1000, soc=79, est=9))
        self.d.passo(self.n(t0 + 900, km=1005, soc=78, est=8))
        self.assertEqual(self.arch.viaggi(), [])
        ev = self.d.passo(self.n(t0 + 1500, *B, km=1012.4, soc=77, est=11, pos_t=t0 + 1400))
        self.assertIn("viaggio", ev)
        vv = self.arch.viaggi()
        self.assertEqual(len(vv), 1)
        v = vv[0]
        self.assertAlmostEqual(v["km"], 12.4, places=2)
        self.assertEqual((v["soc_da"], v["soc_a"], v["con_ricarica"], v["km_stimati"]), (80, 77, 0, 0))
        self.assertEqual((v["partenza"], v["arrivo"], v["durata_stimata"]), (t0 + 450, t0 + 1400, 0))
        self.assertAlmostEqual(v["temp"], 9.5)
        soste = self.arch.soste()
        self.assertEqual(len(soste), 2)
        self.assertEqual((soste[0]["lat"], soste[0]["arrivo"], soste[0]["partenza"]), (B[0], t0 + 1400, None))
        self.assertEqual((soste[1]["arrivo"], soste[1]["partenza"]), (t0 - 3600, t0 + 450))

    def test_andata_e_ritorno(self):
        t0, A = self.T0, self.A
        self.d.passo(self.n(t0, *A, km=1000, soc=80, pos_t=t0 - 60))
        self.d.passo(self.n(t0 + 300, km=1000, soc=80))
        self.d.passo(self.n(t0 + 1200, *A, km=1006, soc=78, pos_t=t0 + 1100))
        vv = self.arch.viaggi()
        self.assertEqual(len(vv), 1)
        self.assertAlmostEqual(vv[0]["km"], 6, places=2)

    def test_falso_allarme(self):
        t0, A = self.T0, self.A
        self.d.passo(self.n(t0, *A, km=1000, soc=80, pos_t=t0 - 60))
        self.d.passo(self.n(t0 + 300, km=1000, soc=80))            # posizione mancata una volta
        self.d.passo(self.n(t0 + 600, *A, km=1000, soc=80, pos_t=t0 - 60))
        self.assertEqual(self.arch.viaggi(), [])
        self.assertEqual(len(self.arch.soste()), 1)
        self.assertNotIn("via", self.arch.memoria("V1"))

    def test_viaggio_tra_due_letture(self):
        t0, A, B = self.T0, self.A, self.B
        self.d.passo(self.n(t0, *A, km=1000, soc=80, pos_t=t0 - 60))
        self.d.passo(self.n(t0 + 300, *B, km=1009, soc=77, pos_t=t0 + 280))
        v = self.arch.viaggi()[0]
        self.assertEqual(v["durata_stimata"], 1)
        self.assertEqual(v["arrivo"], t0 + 280)
        self.assertTrue(t0 <= v["partenza"] <= t0 + 220, v["partenza"])

    def test_ricarica(self):
        t0, A = self.T0, self.A
        self.d.passo(self.n(t0, *A, km=1000, soc=50, pos_t=t0 - 60))
        self.d.passo(self.n(t0 + 300, *A, km=1000, soc=52, pos_t=t0 - 60, ricarica="charging", potenza=11))
        self.d.passo(self.n(t0 + 600, *A, km=1000, soc=60, pos_t=t0 - 60, ricarica="charging", potenza=10.5))
        self.assertEqual(self.arch.ricariche()[0]["fine"], None)
        ev = self.d.passo(self.n(t0 + 900, *A, km=1000, soc=80, pos_t=t0 - 60, ricarica="off"))
        self.assertIn("ricarica", ev)
        r = self.arch.ricariche()
        self.assertEqual(len(r), 1)
        self.assertEqual((r[0]["inizio"], r[0]["fine"], r[0]["soc_da"], r[0]["soc_a"], r[0]["potenza_max"]),
                         (t0 + 300, t0 + 900, 52, 80, 11))
        self.assertEqual((r[0]["lat"], r[0]["lon"]), A)
        self.assertEqual(self.arch.viaggi(), [])

    def test_nomi_dei_posti(self):
        self.arch.salva_indirizzo(43.9001, 10.2301, "Via del Mare 1, Lido")
        self.assertEqual(self.arch.nome_luogo(43.9002, 10.2302), ("Via del Mare 1, Lido", "Via del Mare 1, Lido"))
        self.arch.rinomina(43.9, 10.23, "Casa")
        self.assertEqual(self.arch.nome_luogo(43.9003, 10.2303)[0], "Casa")     # entro 150 m
        self.assertEqual(self.arch.nome_luogo(43.92, 10.23), (None, None))       # 2 km più in là
        self.arch.rinomina(43.9, 10.23, "")
        self.assertEqual(self.arch.nome_luogo(43.9, 10.23)[0], "Via del Mare 1, Lido")


def colonnina(s, ionity=False, kw=150.0, nome=None):
    return {"s": float(s), "e": 0.25 * s, "dev_kwh": 0.0, "dev_min": 0.0, "kw": kw, "kw_noto": True, "ionity": ionity,
            "t": 15.0, "nome": nome or "km %s" % s, "operatore": "", "lat": 0.0, "lon": 0.0, "tempo": 0.0}


class TestAutoSoste(unittest.TestCase):
    """Le soste di ricarica con le regole di Samuele: alla colonnina 25–33% (ideale 33), 20–45% solo se serve, mai
    sotto il 20%; a destinazione almeno il 25%. Consumo finto: 0,25 kWh/km, batteria da 77 kWh."""
    OPZ = {"precond_kwh": 0.0, "precondiziona": True, "ionity_prima": True}

    def test_colonnine_fitte(self):
        cc = [colonnina(s) for s in range(10, 600, 10)]
        r = AV.scegli_soste(150.0, 90.0, cc, AUTO, REGOLE, self.OPZ)
        self.assertTrue(r["riuscito"])
        self.assertEqual(len(r["soste"]), 3)          # con 2 soste non si arriva sopra il 25%
        for s in r["soste"]:
            self.assertTrue(25 - 1e-6 <= s["arrivo_soc"] <= 33 + 1e-6, s)
            self.assertEqual(AV.fascia(s["arrivo_soc"], REGOLE), "ideale")
        for s in r["soste"][:-1]:
            self.assertEqual(s["partenza_soc"], 80)
        self.assertGreaterEqual(r["arrivo_soc"], 25 - 1e-6)

    def test_tolleranza_se_serve(self):
        r = AV.scegli_soste(75.0, 90.0, [colonnina(160), colonnina(240)], AUTO, REGOLE, self.OPZ)
        self.assertTrue(r["riuscito"])
        self.assertEqual([s["s"] for s in r["soste"]], [160.0])
        self.assertEqual(AV.fascia(r["soste"][0]["arrivo_soc"], REGOLE), "tollerata")
        self.assertAlmostEqual(r["arrivo_soc"], 33.0, delta=0.01)    # l'ultima sosta carica quanto basta per il 33%

    def test_impossibile(self):
        r = AV.scegli_soste(100.0, 80.0, [colonnina(270)], AUTO, REGOLE, self.OPZ)
        self.assertFalse(r["riuscito"])
        self.assertEqual(r["soste"], [])
        self.assertIn("20%", r["motivo"])

    def test_senza_soste(self):
        r = AV.scegli_soste(20.0, 80.0, [colonnina(40)], AUTO, REGOLE, self.OPZ)
        self.assertEqual((r["riuscito"], r["soste"]), (True, []))
        self.assertAlmostEqual(r["arrivo_soc"], 80 - 20 / 77 * 100, places=3)

    def test_destinazione_tollerata(self):
        r = AV.scegli_soste((80 - 23) * 0.77, 80.0, [], AUTO, REGOLE, self.OPZ)
        self.assertEqual((r["riuscito"], r["soste"]), (True, []))
        self.assertAlmostEqual(r["arrivo_soc"], 23.0, places=3)

    def test_ionity_prima(self):
        cc = [colonnina(185), colonnina(186, ionity=True, nome="IONITY")]
        r = AV.scegli_soste(75.0, 90.0, cc, AUTO, REGOLE, self.OPZ)
        self.assertEqual([s["nome"] for s in r["soste"]], ["IONITY"])
        r = AV.scegli_soste(75.0, 90.0, cc, AUTO, REGOLE, dict(self.OPZ, ionity_prima=False))
        self.assertEqual([s["nome"] for s in r["soste"]], ["km 185"])

    def test_penalita(self):
        self.assertEqual(AV.penalita_arrivo(33, REGOLE), 0)
        self.assertLess(AV.penalita_arrivo(26, REGOLE), AV.penalita_arrivo(35, REGOLE))
        self.assertLess(AV.penalita_arrivo(35, REGOLE), AV.penalita_arrivo(22, REGOLE))
        self.assertLess(AV.penalita_arrivo(44, REGOLE), AV.penalita_arrivo(50, REGOLE))
        self.assertIsNone(AV.penalita_arrivo(19.9, REGOLE))
        self.assertIsNone(AV.penalita_destinazione(19, REGOLE))
        self.assertEqual(AV.penalita_destinazione(25, REGOLE), 0)


class FintiServizi:
    """Strada dritta verso nord da (44.0, 10.0), a 110 km/h, in pianura, 10 °C senza vento; colonnine CCS ogni 20 km a
    100 m a destra (est), una a sinistra (la carreggiata opposta) e una a 5 km."""
    PASSO = 500.0

    def __init__(self, km=300, ogni=20):
        self.km, self.ogni = km, ogni
        n = int(km * 1000 / self.PASSO)
        self.coord = [[10.0, 44.0 + i * self.PASSO / 111195.0] for i in range(n + 1)]
        self.chiesti = []

    def percorso(self, da, a):
        n, v = len(self.coord) - 1, 110 / 3.6
        return {"coord": self.coord, "dist": [self.PASSO] * n, "dur": [self.PASSO / v] * n, "m": self.PASSO * n,
                "s": self.PASSO / v * n}

    def quote(self, punti):
        return [0.0] * len(punti)

    def meteo(self, punti, da, a):
        t0 = int(time.time() // 3600 * 3600) - 2 * 86400
        ore = [t0 + 3600 * i for i in range(24 * 6)]
        return [{"t": ore, "temp": [10.0] * len(ore), "vento": [0.0] * len(ore), "dir": [0.0] * len(ore),
                 "pioggia": [0.0] * len(ore)} for _ in punti]

    def colonnine(self, linea, raggio=2000):
        self.chiesti.append(len(linea))
        g = 1000.0 / 111195.0
        est = 100.0 / (111320.0 * math.cos(math.radians(45.3)))

        def c(km, dlon, nome):
            return {"lat": 44.0 + km * g, "lon": 10.0 + dlon, "nome": nome, "operatore": "Prova", "kw": 150.0,
                    "kw_noto": True, "ionity": False, "posti": 4}
        out = [c(k, est, "Rapida %d" % k) for k in range(self.ogni, self.km, self.ogni)]
        out.append(c(150.3, -est, "Dall'altra parte"))
        out.append(c(100.3, est * 50, "Lontana"))
        return out

    def paese(self, lat, lon):
        return "IT"


class TestAutoPianifica(unittest.TestCase):
    """Il viaggio completo con servizi finti: energia di ogni km, colonnine lungo la strada, soste, orari e costi."""

    def test_viaggio_con_soste(self):
        srv = FintiServizi()
        imp = AV._copia(AV.PREDEFINITE)
        p = AV.pianifica(srv, imp, CAL1, (44.0, 10.0), (46.7, 10.0), 60.0, time.time(), {})
        self.assertTrue(p["riuscito"], p["avvisi"])
        self.assertAlmostEqual(p["km"], 300, delta=1)
        self.assertTrue(22 < p["kwh_100"] < 34, p["kwh_100"])
        self.assertTrue(70 < p["energia"]["totale"] < 100, p["energia"])
        self.assertGreater(p["energia"]["preriscaldo"], 0)              # 10 °C e clima spento alla partenza
        self.assertTrue(p["soste"])
        nomi = [s["nome"] for s in p["soste"]]
        self.assertNotIn("Dall'altra parte", nomi)
        self.assertNotIn("Lontana", nomi)
        for s in p["soste"]:
            self.assertEqual(s["fascia"], "ideale", s)
            self.assertEqual(s["paese"], "IT")
            self.assertGreater(s["costo"], 0)
            self.assertGreater(s["ora_partenza"], s["ora_arrivo"])
        self.assertGreaterEqual(p["soc_arrivo"], 25 - 0.05)
        self.assertGreater(p["arrivo"], p["partenza"] + p["ore_guida"] * 3600)
        kms = [x["km"] for x in p["profilo"]]
        self.assertEqual(kms, sorted(kms))
        self.assertTrue(all(x["soc"] >= 20 - 0.5 for x in p["profilo"]), p["profilo"])
        self.assertLessEqual(len(p["geo"]), 1502)
        self.assertEqual(p["colonnine_viste"], 14)    # 14 a destra; quella dall'altra parte e quella lontana no

    def test_senza_soste_e_con_preferenze(self):
        srv = FintiServizi(km=120)
        imp = AV._copia(AV.PREDEFINITE)
        p = AV.pianifica(srv, imp, CAL1, (44.0, 10.0), (45.08, 10.0), 90.0, time.time(),
                         {"musica": False, "velocita_max": 90}, clima_acceso=True)
        self.assertEqual(p["soste"], [])
        self.assertEqual(p["energia"]["musica"], 0)
        self.assertEqual(p["energia"]["preriscaldo"], 0)
        self.assertEqual(p["velocita_max"], 90)
        self.assertAlmostEqual(p["ore_guida"], 120 / 90, delta=0.05)


FINTA_CC = {
    "carconnectivity/__init__.py": "",
    "carconnectivity/errors.py": """
class CarConnectivityError(Exception): pass
class RetrievalError(CarConnectivityError): pass
class TooManyRequestsError(RetrievalError): pass
class AuthenticationError(CarConnectivityError): pass
class TemporaryAuthenticationError(AuthenticationError): pass
class CommandError(CarConnectivityError): pass
""",
    "carconnectivity/units.py": """
import enum
class Temperature(enum.Enum):
    C = '°C'
    K = '°K'
""",
    "carconnectivity/commands.py": """
import enum, json, os
class GenericCommand:
    def __init__(self, padre, nome):
        self.padre, self.nome, self.enabled, self._valore = padre, nome, True, None
        padre.figli.append(self)
    def get_absolute_path(self):
        return self.padre.get_absolute_path() + '/' + self.nome
    @property
    def value(self):
        return self._valore
    @value.setter
    def value(self, v):
        self._valore = v
        with open(os.environ['FINTO_LOG'], 'a', encoding='utf-8') as f:
            f.write(json.dumps({'p': self.get_absolute_path(),
                                'v': {k: (x.value if isinstance(x, enum.Enum) else x) for k, x in v.items()}}) + '\\n')
""",
    "carconnectivity/command_impl.py": """
import enum
from carconnectivity.commands import GenericCommand
class LockUnlockCommand(GenericCommand):
    class Command(enum.Enum):
        LOCK = 'lock'
        UNLOCK = 'unlock'
class ClimatizationStartStopCommand(GenericCommand):
    class Command(enum.Enum):
        START = 'start'
        STOP = 'stop'
class WindowHeatingStartStopCommand(GenericCommand):
    class Command(enum.Enum):
        START = 'start'
        STOP = 'stop'
""",
    "carconnectivity/carconnectivity.py": """
import datetime
from carconnectivity import errors
from carconnectivity.units import Temperature
from carconnectivity.command_impl import LockUnlockCommand, ClimatizationStartStopCommand
QUANDO = datetime.datetime(2026, 11, 12, 10, 0, tzinfo=datetime.timezone.utc)
class Oggetto:
    def __init__(self, padre, nome):
        self.padre, self.nome, self.figli, self.enabled = padre, nome, [], True
        if padre is not None:
            padre.figli.append(self)
    def get_absolute_path(self):
        return (self.padre.get_absolute_path() + '/' if self.padre is not None else '') + self.nome
    def get_attributes(self, recursive=False):
        out = []
        for f in self.figli:
            if not f.enabled:
                continue
            if isinstance(f, Oggetto):
                if recursive:
                    out += f.get_attributes(True)
            else:
                out.append(f)
        return out
class Attributo:
    def __init__(self, padre, nome, valore, unita=None):
        self.padre, self.nome, self.value, self.unit, self.enabled = padre, nome, valore, unita, True
        self.last_updated = QUANDO
        padre.figli.append(self)
    def get_absolute_path(self):
        return self.padre.get_absolute_path() + '/' + self.nome
class Garage(Oggetto):
    def list_vehicles(self):
        return [f for f in self.figli if isinstance(f, Oggetto)]
class CarConnectivity:
    def __init__(self, config, tokenstore_file=None, cache_file=None):
        import os, json
        conn = config['carConnectivity']['connectors'][0]
        if os.environ.get('FINTO_CONFIG'):      # la prova controlla quale connettore e con quali opzioni
            with open(os.environ['FINTO_CONFIG'], 'w') as f:
                json.dump(conn, f)
        c = conn['config']
        if c['password'] == 'bloccata':     # come Volkswagen dal 27/05/2026: 403 già sulla pagina di accesso
            raise errors.AuthenticationError('Authorization URL could not be fetched due to WeConnect failure')
        self.giusta = c['password'] == 'giusta'
        self.garage = Garage(Oggetto(None, ''), 'garage')
        v = Oggetto(self.garage, 'WVTEST0000000001')
        v.vin = Attributo(v, 'vin', 'WVTEST0000000001')
        Attributo(v, 'odometer', 12345.0, 'km')
        Attributo(v, 'outside_temperature', 282.65, Temperature.K)
        d = Oggetto(Oggetto(v, 'drives'), 'primary')
        Attributo(d, 'type', 'electric')
        Attributo(d, 'level', 64, '%')
        porte = Oggetto(v, 'doors')
        Attributo(porte, 'lock_state', 'locked')
        LockUnlockCommand(Oggetto(porte, 'commands'), 'lock-unlock')
        clima = Oggetto(v, 'climatization')
        Attributo(clima, 'state', 'off')
        ClimatizationStartStopCommand(Oggetto(clima, 'commands'), 'start-stop')
    def get_garage(self):
        return self.garage
    def fetch_all(self):
        if not self.giusta:
            raise errors.AuthenticationError('login failed')
    def persist(self):
        pass
    def shutdown(self):
        pass
""",
}


class TestAutoPonte(unittest.TestCase):
    """auto_ponte.py con una CarConnectivity finta: il protocollo a righe JSON, le letture, i comandi, l'S-PIN."""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="atlante-auto-ponte-")
        for nome, testo in FINTA_CC.items():
            p = os.path.join(self.base, "finta", nome)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(testo)
        self.log = os.path.join(self.base, "comandi.log")
        self.p = None

    def tearDown(self):
        if self.p is not None and self.p.poll() is None:
            self.p.kill()
            self.p.wait()
        shutil.rmtree(self.base, ignore_errors=True)

    def avvia(self, password, **extra):
        self.config = os.path.join(self.base, "config.json")
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": os.path.join(self.base, "finta"),
               "FINTO_LOG": self.log, "FINTO_CONFIG": self.config, "PYTHONDONTWRITEBYTECODE": "1",
               "PYTHONIOENCODING": "utf-8"}
        self.err = open(os.path.join(self.base, "ponte.err"), "wb")
        self.p = subprocess.Popen([sys.executable, os.path.join(QUI, "auto_ponte.py")], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=self.err, env=env, cwd=self.base)
        self.righe = queue.Queue()

        def leggi():
            for r in self.p.stdout:
                self.righe.put(json.loads(r.decode("utf-8")))
        threading.Thread(target=leggi, daemon=True).start()
        prima = {"utente": "samuele@example.com", "password": password, "intervallo": 300,
                 "forza_accesso": False, "cartella": self.base}
        prima.update(extra)
        self.manda(prima)

    def manda(self, o):
        self.p.stdin.write((json.dumps(o) + "\n").encode("utf-8"))
        self.p.stdin.flush()

    def prossimo(self, tipo):
        fine = time.time() + 30
        while time.time() < fine:
            try:
                m = self.righe.get(timeout=1)
            except queue.Empty:
                continue
            if m.get("tipo") == tipo:
                return m
        with open(self.err.name, "rb") as f:
            raise AssertionError("niente «%s» dal ponte: %s" % (tipo, f.read().decode("utf-8", "replace")[-1500:]))

    def chiudi(self):
        self.p.stdin.close()
        self.assertEqual(self.p.wait(20), 0)
        self.err.close()

    def test_letture_e_comandi(self):
        self.avvia("giusta")
        self.prossimo("pronto")
        s = self.prossimo("stato")
        v = s["veicoli"][0]
        self.assertEqual(v["vin"], "WVTEST0000000001")
        self.assertEqual(v["attr"]["outside_temperature"], [9.5, "°C", "2026-11-12T10:00:00+00:00"])   # da kelvin
        self.assertEqual(v["attr"]["drives/primary/level"][:2], [64, "%"])
        self.assertEqual(v["attr"]["odometer"][:2], [12345.0, "km"])
        self.assertEqual(v["comandi"], {"doors/commands/lock-unlock": True, "climatization/commands/start-stop": True})
        vin = v["vin"]
        self.manda({"id": "a", "azione": "sblocca", "vin": vin, "spin": "12"})
        r = self.prossimo("risposta")
        self.assertEqual((r["id"], r["ok"]), ("a", False))
        self.assertIn("4 cifre", r["errore"])
        self.manda({"id": "b", "azione": "sblocca", "vin": vin, "spin": "1234"})
        self.assertEqual(self.prossimo("risposta"), {"tipo": "risposta", "id": "b", "ok": True})
        self.manda({"id": "c", "azione": "clima_on", "vin": vin, "temperatura": 21.5})
        self.assertTrue(self.prossimo("risposta")["ok"])
        self.manda({"id": "d", "azione": "sbrina_on", "vin": vin})
        r = self.prossimo("risposta")
        self.assertEqual((r["id"], r["ok"]), ("d", False))
        self.assertIn("sbrinamento", r["errore"])
        self.manda({"id": "e", "azione": "aggiorna", "vin": vin})
        self.assertTrue(self.prossimo("risposta")["ok"])
        self.chiudi()
        with open(self.log, encoding="utf-8") as f:
            fatti = [json.loads(x) for x in f]
        self.assertEqual(fatti, [
            {"p": "/garage/WVTEST0000000001/doors/commands/lock-unlock", "v": {"command": "unlock", "spin": "1234"}},
            {"p": "/garage/WVTEST0000000001/climatization/commands/start-stop",
             "v": {"command": "start", "target_temperature": 21.5, "target_temperature_unit": "°C"}}])

    def test_fonte_app_connettore_volkswagen(self):
        self.avvia("giusta")
        self.prossimo("stato")
        self.chiudi()
        with open(self.config, encoding="utf-8") as f:
            conn = json.load(f)
        self.assertEqual(conn["type"], "volkswagen")

    def test_fonte_dati_connettore_eu_data_act(self):
        # fonte «dati»: il ponte deve usare il connettore del portale EU Data Act, con paese/lingua/marca
        self.avvia("giusta", fonte="dati", paese="IT", lingua="it", marca="VOLKSWAGEN_COMMERCIAL_VEHICLES",
                   intervallo=900)
        self.prossimo("stato")
        self.chiudi()
        with open(self.config, encoding="utf-8") as f:
            conn = json.load(f)
        self.assertEqual(conn["type"], "vw_eu_data_act")
        self.assertEqual(conn["config"]["country"], "IT")
        self.assertEqual(conn["config"]["language"], "it")
        self.assertEqual(conn["config"]["brand"], "VOLKSWAGEN_COMMERCIAL_VEHICLES")
        self.assertNotIn("force_enable_access", conn["config"])   # è solo del connettore dell'app

    def test_password_sbagliata(self):
        self.avvia("sbagliata")
        self.prossimo("pronto")
        e = self.prossimo("errore")
        self.assertTrue(e["grave"])
        self.assertIn("email e password", e["testo"])
        self.assertEqual(self.p.wait(20), 0)    # si chiude da solo
        self.err.close()

    def test_volkswagen_blocca_le_app_non_ufficiali(self):
        # 12/11 sul Mac mini: «Authorization URL could not be fetched due to WeConnect failure» (403 del cloud
        # Volkswagen per tutti, issue #107 del connettore): non è la password, e non va chiesto di ricollegare
        self.avvia("bloccata")
        e = self.prossimo("errore")
        self.assertFalse(e["grave"])
        self.assertEqual(e["codice"], "vw_blocca")
        self.assertIn("app non ufficiali", e["testo"])
        self.assertEqual(self.p.wait(20), 5)
        self.err.close()


class TestAutoBlocco(unittest.TestCase):
    """Volkswagen blocca l'accesso delle app non ufficiali: niente «ricollega l'account», il ponte riprova da solo ogni
    RIPROVA_VW_BLOCCA secondi, e un account ricollegato nel frattempo riparte subito."""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="atlante-auto-blocco-")

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_si_riprova_tra_qualche_ora(self):
        attese = []

        class P(AV.Ponte):
            def _installa(self):
                pass

            def _esegui(self, cred):
                self._messaggio({"tipo": "errore", "grave": False, "codice": "vw_blocca", "testo": "VW blocca."})
                return 5

            def _aspetta(self, secondi):
                attese.append(secondi)
                return True               # Atlante si ferma: il filo finisce

        p = P(self.base, lambda m: None)
        p._vita(lambda: ("samuele@example.com", "x", False))
        self.assertEqual(attese, [AV.RIPROVA_VW_BLOCCA])
        self.assertEqual(p.bloccato, "")
        self.assertEqual(p.stato["fase"], "errore")
        self.assertIn("VW blocca.", p.stato["messaggio"])
        self.assertIn("Riprovo da solo alle", p.stato["messaggio"])

    def test_l_attesa_finisce_con_credenziali_nuove_o_alla_chiusura(self):
        p = AV.Ponte(self.base, lambda m: None)
        p.subito.set()                    # avvia() con l'account ricollegato
        t = time.time()
        self.assertFalse(p._aspetta(3600))
        self.assertLess(time.time() - t, 2)
        p.subito.clear()
        p.fermo.set()                     # Atlante si chiude
        self.assertTrue(p._aspetta(3600))


class PonteFintoInstalla(AV.Ponte):
    """Il ponte con comandi finti per la preparazione: niente rete, niente pip. Un Python è un file con una versione.
    Come il Mac mini il 12/11: il Python del server è il 3.9 e CarConnectivity vuole requests 2.34 (solo da 3.10)."""

    def __init__(self, cartella, server, rotto=()):
        super().__init__(cartella, lambda m: None)
        self.comandi = []                          # (comando, env)
        self.versioni = {sys.executable: server}   # percorso del Python → versione
        self.con_libreria = set()                  # i Python con CarConnectivity installata
        self.rotto = set(rotto)                    # i passi che non riescono: "uv", "scarica"

    def python_finto(self, py, versione):
        os.makedirs(os.path.dirname(py), exist_ok=True)
        open(py, "w").close()
        self.versioni[py] = versione

    def _run(self, comando, timeout, env=None, cwd=None):
        c = list(comando)
        self.comandi.append((c, env))

        def fatto(codice=0, testo=""):
            return subprocess.CompletedProcess(c, codice, stdout=testo.encode("utf-8"))

        py = c[0]
        if py != sys.executable and not os.path.exists(py):
            raise FileNotFoundError(py)
        if c[1:3] == ["-I", "-c"]:
            if "version_info" in c[3]:
                v = self.versioni.get(py)
                return fatto(0, "%d.%d\n" % v) if v else fatto(1, "rotto")
            return fatto(0 if py in self.con_libreria else 1)          # la prova dell'import
        if c[1:3] == ["-m", "venv"]:
            self.python_finto(os.path.join(c[3], "bin", "python3"), self.versioni[py])
            return fatto()
        if c[1:4] == ["-m", "pip", "install"]:
            if c[-1] == "pip":
                return fatto()
            if c[-1] == "uv":
                if "uv" in self.rotto:
                    return fatto(1, "ERROR: niente uv")
                open(os.path.join(os.path.dirname(py), "uv"), "w").close()
                return fatto()
            if self.versioni[py] < (3, 10):
                return fatto(1, "2.32.5) ERROR: No matching distribution found for requests~=2.34.2")
            self.con_libreria.add(py)
            return fatto()
        if os.path.basename(py) == "uv" and c[1] == "venv":
            if "scarica" in self.rotto:
                return fatto(1, "error: download failed")
            versione = tuple(int(x) for x in c[c.index("--python") + 1].split("."))
            self.python_finto(os.path.join(c[-1], "bin", "python3"), versione)
            return fatto()
        raise AssertionError("comando inatteso: %r" % c)


class TestAutoInstalla(unittest.TestCase):
    """La preparazione del ponte (Ponte._installa) con comandi finti. Il 12/11 sul Mac mini il collegamento non partiva:
    l'ambiente era fatto con il Python 3.9 del Mac e pip diceva «No matching distribution found for requests~=2.34.2»."""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="atlante-auto-installa-")
        self.ambiente = unittest.mock.patch.dict(os.environ)
        self.ambiente.start()
        for k in ("ATLANTE_AUTO_PYTHON", "ATLANTE_AUTO_INSTALLA"):
            os.environ.pop(k, None)
        self.venv = os.path.join(self.base, "python")
        self.py = os.path.join(self.venv, "bin", "python3")

    def tearDown(self):
        self.ambiente.stop()
        shutil.rmtree(self.base, ignore_errors=True)

    def test_python_del_mac_vecchio_ne_scarica_uno_nuovo(self):
        p = PonteFintoInstalla(self.base, (3, 9))
        p._installa()
        self.assertEqual(p.versioni[self.py], (3, 12))
        self.assertIn(self.py, p.con_libreria)
        uv = [(c, env) for c, env in p.comandi if os.path.basename(c[0]) == "uv"]
        self.assertEqual([c[1:] for c, _ in uv], [["venv", "--seed", "--python", AV.PYTHON_SCARICATO, self.venv]])
        env = uv[0][1]
        self.assertTrue(env["UV_PYTHON_INSTALL_DIR"].startswith(self.base))   # il Python scaricato resta in <cache>/auto
        self.assertTrue(env["UV_CACHE_DIR"].startswith(self.base))
        self.assertNotIn([sys.executable, "-m", "venv", self.venv], [c for c, _ in p.comandi])

    def test_ambiente_fatto_col_python_vecchio_si_rifa(self):
        p = PonteFintoInstalla(self.base, (3, 9))
        p.python_finto(self.py, (3, 9))       # com'era sul Mac mini: l'ambiente del 3.9, dove la libreria non entra
        open(os.path.join(self.venv, "pyvenv.cfg"), "w").close()
        p._installa()
        self.assertEqual(p.versioni[self.py], (3, 12))
        self.assertIn(self.py, p.con_libreria)
        self.assertFalse(os.path.exists(os.path.join(self.venv, "pyvenv.cfg")))   # rifatto da capo

    def test_python_del_server_nuovo_basta_lui(self):
        p = PonteFintoInstalla(self.base, (3, 12))
        p._installa()
        fatti = [c for c, _ in p.comandi]
        self.assertIn([sys.executable, "-m", "venv", self.venv], fatti)
        self.assertFalse([c for c in fatti if os.path.basename(c[0]) == "uv" or c[-1] == "uv"])
        self.assertIn(self.py, p.con_libreria)

    def test_ambiente_pronto_non_tocca_niente(self):
        p = PonteFintoInstalla(self.base, (3, 9))
        p.python_finto(self.py, (3, 12))
        p.con_libreria.add(self.py)
        p._installa()
        self.assertEqual([c[:3] for c, _ in p.comandi], [[self.py, "-I", "-c"]] * 2)   # versione e prova dell'import

    def test_download_non_riuscito_dice_cosa(self):
        p = PonteFintoInstalla(self.base, (3, 9), rotto=("scarica",))
        with self.assertRaises(RuntimeError) as e:
            p._installa()
        self.assertIn("Python " + AV.PYTHON_SCARICATO, str(e.exception))
        self.assertIn("download failed", str(e.exception))


FINTO_PONTE = r'''
import json, os, sys, time
conf = json.loads(sys.stdin.readline())
def manda(m):
    sys.stdout.write(json.dumps(m) + "\n")
    sys.stdout.flush()
if conf.get("password") != "giusta":
    manda({"tipo": "errore", "grave": True, "testo": "Volkswagen ha rifiutato l'accesso"})
    sys.exit(0)
manda({"tipo": "pronto"})
q = "2026-11-12T10:00:00+00:00"
attr = {"name": ["Bulli", None, None], "model": ["ID. Buzz", None, None], "model_year": [2023, None, None],
        "drives/primary/type": ["electric", None, None], "drives/primary/level": [64, "%", q],
        "drives/primary/range": [205, "km", q], "odometer": [12345, "km", q], "outside_temperature": [9.5, "°C", q],
        "drives/primary/battery/temperature_min": [11.0, "°C", q], "drives/primary/battery/temperature_max": [13.0, "°C", q],
        "position/latitude": [43.9, None, "2026-11-12T09:00:00+00:00"], "position/longitude": [10.23, None, q],
        "doors/lock_state": ["locked", None, q], "climatization/state": ["off", None, q],
        "climatization/settings/target_temperature": [21.0, "°C", q], "charging/state": ["off", None, q],
        "state": ["parked", None, q]}
manda({"tipo": "stato", "t": time.time(), "veicoli": [{"vin": "WVTEST0000000001", "attr": attr,
       "comandi": {"doors/commands/lock-unlock": True, "climatization/commands/start-stop": True}}]})
for riga in sys.stdin:
    r = json.loads(riga)
    with open(os.path.join(conf["cartella"], "comandi.log"), "a") as f:
        f.write(json.dumps({"azione": r.get("azione"), "spin": "spin" in r, "temperatura": r.get("temperatura")}) + "\n")
    if r.get("azione") == "sblocca" and r.get("spin") != "1234":
        manda({"tipo": "risposta", "id": r["id"], "ok": False, "errore": "S-PIN sbagliato"})
    else:
        manda({"tipo": "risposta", "id": r["id"], "ok": True})
'''


class TestAutoApi(unittest.TestCase):
    """Le API del modulo (Auto.api) con un ponte finto al posto del cloud Volkswagen."""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="atlante-auto-api-")
        ponte = os.path.join(self.base, "ponte_finto.py")
        with open(ponte, "w", encoding="utf-8") as f:
            f.write(FINTO_PONTE)
        self.ambiente = unittest.mock.patch.dict(os.environ, {
            "ATLANTE_AUTO_PORTACHIAVI": "0", "ATLANTE_AUTO_PYTHON": sys.executable, "ATLANTE_AUTO_PONTE": ponte,
            "ATLANTE_AUTO_INSTALLA": "0"})
        self.ambiente.start()
        self.cart = os.path.join(self.base, "auto")
        self.a = AV.Auto(self.cart)

    def tearDown(self):
        self.a.chiudi()
        self.ambiente.stop()
        shutil.rmtree(self.base, ignore_errors=True)

    def api(self, metodo, percorso, corpo=None, codice=200):
        parti = [x for x in percorso.split("?")[0].split("/") if x]
        query = {}
        if "?" in percorso:
            for kv in percorso.split("?", 1)[1].split("&"):
                k, _, v = kv.partition("=")
                query.setdefault(k, []).append(v)
        try:
            c, d = self.a.api(metodo, parti, query, corpo or {})
        except AV.ErroreAuto as e:
            self.assertEqual(e.codice, codice, e.testo)
            return e.testo
        self.assertEqual(c, codice)
        return d

    def collega(self, password="giusta"):
        return self.api("POST", "account", {"utente": "samuele@example.com", "password": password})

    def test_senza_account(self):
        s = self.api("GET", "stato")
        self.assertEqual((s["account"]["collegato"], s["veicoli"]), (False, []))
        self.api("POST", "impostazioni", {"collegamento": {"fonte": "app"}})   # comandi: solo sulla fonte «app»
        self.assertIn("non è attivo", self.api("POST", "comando", {"azione": "clima_off"}, codice=503))
        self.assertIn("email", self.api("POST", "account", {"utente": "non-una-email", "password": "x"}, codice=400))
        self.api("GET", "niente", codice=404)
        self.api("POST", "stato", codice=405)

    def test_collega_legge_e_comanda(self):
        self.api("POST", "impostazioni", {"collegamento": {"fonte": "app"}})   # i comandi esistono solo sulla fonte «app»
        s = self.collega()
        self.assertEqual((s["account"]["collegato"], s["account"]["utente"], s["account"]["dove"]),
                         (True, "sa•••@example.com", "file"))
        self.assertEqual((s["account"]["fonte"], s["account"]["solo_lettura"]), ("app", False))
        file = os.path.join(self.cart, "account.json")
        self.assertEqual(os.stat(file).st_mode & 0o777, 0o600)
        s = aspetta(lambda: (lambda x: x if x["veicoli"] else None)(self.api("GET", "stato")))
        self.assertIsNotNone(s, "nessuna lettura dal ponte finto")
        v = s["veicoli"][0]
        self.assertEqual((v["soc"], v["autonomia"], v["km"], v["est"], v["nome"], v["porte"]),
                         (64, 205, 12345, 9.5, "Bulli", "locked"))
        self.assertEqual(v["batteria"], {"min": 11.0, "max": 13.0})
        self.assertEqual((v["posizione"]["lat"], v["posizione"]["lon"]), (43.9, 10.23))
        self.assertEqual(v["comandi"], {"porte": True, "clima": True, "sbrina": False})
        self.assertEqual(s["account"]["ponte"]["fase"], "collegato")
        vin = v["vin"]
        self.assertIn("S-PIN", self.api("POST", "comando", {"vin": vin, "azione": "sblocca"}, codice=400))
        self.assertIn("S-PIN", self.api("POST", "comando", {"vin": vin, "azione": "sblocca", "spin": "12a4"}, codice=400))
        self.assertTrue(self.api("POST", "comando", {"vin": vin, "azione": "sblocca", "spin": "1234"})["ok"])
        self.assertIn("S-PIN sbagliato", self.api("POST", "comando", {"vin": vin, "azione": "sblocca", "spin": "9999"},
                                                  codice=502))
        self.assertIn("16", self.api("POST", "comando", {"vin": vin, "azione": "clima_on", "temperatura": 40}, codice=400))
        self.assertTrue(self.api("POST", "comando", {"vin": vin, "azione": "clima_on", "temperatura": 21.3})["ok"])
        with open(os.path.join(self.cart, "comandi.log"), encoding="utf-8") as f:
            fatti = [json.loads(x) for x in f]
        self.assertEqual(fatti[-1], {"azione": "clima_on", "spin": False, "temperatura": 21.5})
        # la sosta è nel diario; il posto prende un nome
        d = self.api("GET", "diario?giorni=7")
        self.assertEqual(len(d["soste"]), 1)
        self.assertEqual(self.api("POST", "luoghi", {"lat": 43.9, "lon": 10.23, "nome": "Casa"})["nome"], "Casa")
        self.assertEqual(self.api("GET", "stato")["veicoli"][0]["posizione"]["nome"], "Casa")
        # scollega: via account, sessione e stato
        s = self.api("DELETE", "account")
        self.assertEqual((s["account"]["collegato"], s["veicoli"]), (False, []))
        self.assertFalse(os.path.exists(file))

    def test_password_sbagliata(self):
        self.api("POST", "impostazioni", {"collegamento": {"fonte": "app"}})
        self.collega("sbagliata")
        s = aspetta(lambda: (lambda x: x if x["account"]["bloccato"] else None)(self.api("GET", "stato")))
        self.assertIsNotNone(s)
        self.assertIn("rifiutato", s["account"]["bloccato"])
        self.assertEqual(s["account"]["ponte"]["fase"], "errore")

    def test_fonte_dati_sola_lettura(self):
        # fonte «dati» (predefinita): il portale EU Data Act. I comandi sono rifiutati qui: solo dall'app Volkswagen.
        s = self.api("GET", "stato")
        self.assertEqual((s["account"]["fonte"], s["account"]["solo_lettura"]), ("dati", True))
        self.collega()
        s = aspetta(lambda: (lambda x: x if x["veicoli"] else None)(self.api("GET", "stato")))
        self.assertIsNotNone(s)
        vin = s["veicoli"][0]["vin"]
        self.assertIn("sola lettura", self.api("POST", "comando", {"vin": vin, "azione": "sblocca", "spin": "1234"},
                                               codice=409))
        self.assertIn("sola lettura", self.api("POST", "comando", {"vin": vin, "azione": "clima_on"}, codice=409))
        self.assertTrue(self.api("POST", "comando", {"vin": vin, "azione": "aggiorna"})["ok"])   # «aggiorna» sì

    def test_impostazioni(self):
        imp = self.api("GET", "impostazioni")
        self.assertEqual((imp["regole"]["arrivo_ideale"], imp["regole"]["arrivo_min"], imp["regole"]["toll_min"],
                          imp["regole"]["toll_max"]), (33, 25, 20, 45))
        self.assertIn("ordine", self.api("POST", "impostazioni", {"regole": {"arrivo_min": 40}}, codice=400))
        self.assertIn("capacità", self.api("POST", "impostazioni", {"auto": {"capacita_kwh": 5}}, codice=400))
        imp = self.api("POST", "impostazioni", {"auto": {"pompa_calore": True, "capacita_kwh": 79},
                                                "viaggio": {"velocita_max": 100}, "sconosciuto": {"x": 1}})
        self.assertEqual((imp["auto"]["pompa_calore"], imp["auto"]["capacita_kwh"], imp["viaggio"]["velocita_max"]),
                         (True, 79, 100))
        self.assertEqual(AV.Impostazioni(os.path.join(self.cart, "impostazioni.json")).tutte()["auto"]["capacita_kwh"], 79)
        # fonte e marca: solo valori buoni (se no, i predefiniti)
        self.assertEqual(self.api("GET", "impostazioni")["collegamento"],
                         {"fonte": "dati", "paese": "IT", "lingua": "it", "marca": "VOLKSWAGEN_PASSENGER_CARS"})
        c = self.api("POST", "impostazioni", {"collegamento": {"fonte": "boh", "marca": "tesla", "paese": "FR"}})
        self.assertEqual((c["collegamento"]["fonte"], c["collegamento"]["marca"], c["collegamento"]["paese"]),
                         ("dati", "VOLKSWAGEN_PASSENGER_CARS", "FR"))
        # una marca dell'elenco anche scritta in minuscolo (l'app manda sempre il nome intero, come in MARCHE)
        c = self.api("POST", "impostazioni", {"collegamento": {"fonte": "app", "marca": "volkswagen_commercial_vehicles"}})
        self.assertEqual((c["collegamento"]["fonte"], c["collegamento"]["marca"]), ("app", "VOLKSWAGEN_COMMERCIAL_VEHICLES"))

    def test_rendimento_e_viaggio_senza_dati(self):
        r = self.api("GET", "rendimento")
        self.assertTrue(r["calibrazione"]["tipici"])
        self.assertEqual(r["fasce"], [])
        self.assertEqual(len(r["curva"]), 46)
        self.assertIn("Scegli", self.api("POST", "viaggio", {}, codice=400))
        self.assertIn("auto", self.api("POST", "viaggio", {"a": {"lat": 45, "lon": 9}}, codice=400))
        self.assertIn("non valido", self.api("POST", "viaggio", {"a": {"lat": 95, "lon": 9}}, codice=400))
        self.assertEqual(self.api("GET", "cerca?q=ab"), {"risultati": []})


class FintoGestore:
    """Quanto basta di BaseHTTPRequestHandler per _instrada e _permesso di musica_libreria.py."""

    def __init__(self, ruolo, percorso="/api/extra/auto/stato"):
        self.client_address, self.headers, self.path, self.command = ("127.0.0.1", 50000), {}, percorso, "GET"
        self.connection = None
        self._chi = {"ruolo": ruolo, "persona": "x" if ruolo == "invitato" else None, "chiave": None}
        self._musica_risposto = False
        self.codice, self.corpo, self.wfile = None, b"", self

    def write(self, b):
        self.corpo += b

    def send_response(self, codice):
        self.codice = codice

    def send_header(self, k, v):
        pass

    def end_headers(self):
        pass


class TestAutoServer(unittest.TestCase):
    """Sul server di Atlante: api/extra/auto/… solo per Samuele (mai la rete di casa né gli invitati)."""

    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="atlante-auto-server-")
        cls.ambiente = unittest.mock.patch.dict(os.environ, {"ATLANTE_AUTO_PORTACHIAVI": "0"})
        cls.ambiente.start()
        cls.m = carica("musica_libreria_auto", "musica_libreria.py")
        cls.m.CONF["cache"] = cls.base

    @classmethod
    def tearDownClass(cls):
        if cls.m.AUTO is not None:
            cls.m.AUTO.chiudi()
        cls.ambiente.stop()
        shutil.rmtree(cls.base, ignore_errors=True)

    def test_solo_samuele(self):
        for ruolo in ("invitato", "casa", "nessuno"):
            with self.assertRaises(self.m.ErroreExtra, msg=ruolo) as e:
                self.m._instrada(FintoGestore(ruolo), "GET", ["auto", "stato"], {})
            self.assertEqual(e.exception.codice, 403, ruolo)
        h = FintoGestore("proprietario")
        self.m._instrada(h, "GET", ["auto", "stato"], {})
        self.assertEqual(h.codice, 200)
        self.assertFalse(json.loads(h.corpo.decode("utf-8"))["account"]["collegato"])
        self.assertTrue(os.path.isdir(os.path.join(self.base, "auto")))

    def test_errori_del_modulo(self):
        with self.assertRaises(self.m.ErroreExtra) as e:
            self.m._instrada(FintoGestore("proprietario", "/api/extra/auto/niente"), "GET", ["auto", "niente"], {})
        self.assertEqual(e.exception.codice, 404)

    def test_invitato_fermato_prima(self):
        h = FintoGestore("invitato")
        self.assertFalse(self.m._permesso(h))
        self.assertEqual(h.codice, 403)


class TestAutoPagina(unittest.TestCase):
    """La pagina x/auto.html segue il contratto dei moduli; nell'app la voce è solo di Samuele."""

    def leggi(self, *p):
        with open(os.path.join(QUI, "..", *p), encoding="utf-8") as f:
            return f.read()

    def test_pagina(self):
        src = self.leggi("x", "auto.html")
        self.assertIn('<script src="kit.js"></script>', src)
        self.assertIn('href="kit.css"', src)
        self.assertIn("AX.header('La mia Volkswagen'", src)
        self.assertIsNone(re.search(r"\b(alert|confirm|prompt)\(", src))
        self.assertNotIn("localStorage", src)
        esterni = set(re.findall(r"https://cdn\.jsdelivr\.net/npm/[^'\"]+", src))
        self.assertTrue(esterni)
        self.assertTrue(all(u.startswith("https://cdn.jsdelivr.net/npm/leaflet@1.9.4/") for u in esterni), esterni)

    def test_voce_solo_samuele(self):
        app = self.leggi("index.html")
        self.assertRegex(app, r"\{k:'auto', t:'La mia Volkswagen'[^}]*solo:'proprietario'\}")
        self.assertIn("m.solo === IO.ruolo", app)
        self.assertIn("## La mia Volkswagen", self.leggi("x", "CONTRATTO.md"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
