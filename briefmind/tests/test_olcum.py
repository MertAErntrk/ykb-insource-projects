"""Faz 0 olcum: klip/okuma kaydi, olcum.py metrikleri, etiketle ve replay (ses cihazi, ag, LLM yok; sentetik veri)."""
import datetime as dt
import json
import os
import queue
import sys
import time

import numpy as np
import pytest

import ses
import yakalayici

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import etiketle  # noqa: E402
import olcum  # noqa: E402
import replay  # noqa: E402

KIM_A, KIM_B = "Selin Kara", "Emre Aksoy"          # sentetik adlar
T0 = 1_700_000_000.0


def _jsonl(yol, kayitlar):
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    with open(yol, "w", encoding="utf-8") as f:
        for k in kayitlar:
            f.write(json.dumps(k, ensure_ascii=False) + "\n")


def _oku_jsonl(yol):
    with open(yol, encoding="utf-8") as f:
        return [json.loads(s) for s in f if s.strip()]


# ---------------------------------------------------------------- metin / WER

def test_tr_normalize_i_harfleri_ve_noktalama():
    assert olcum.tr_normalize("İstanbul'da, TOPLANTI!") == "istanbulda toplantı"
    assert olcum.tr_normalize("ISIK İşık") == "ısık işık"        # I -> ı
    assert olcum.tr_normalize("  Evet...  tamam?  ") == "evet tamam"


def test_wer_sayimi():
    assert olcum.wer("Bugün rapor hazır.", "bugün RAPOR hazır") == (0, 3)
    assert olcum.wer("bugün rapor hazır", "bugün rapor") == (1, 3)           # silme
    assert olcum.wer("bugün rapor hazır", "bugün yeni rapor hazır") == (1, 3)  # ekleme
    assert olcum.wer("bugün rapor hazır", "yarın rapor hazır") == (1, 3)     # degistirme
    assert olcum.wer("", "bir iki") == (2, 0)


# ---------------------------------------------------------------- esleme

def _klip(no, akis, ts, sure=4.0, raw="", tsler=None):
    return {"no": no, "akis": akis, "ts": ts, "sure": sure, "raw": raw, "tsler": tsler if tsler is not None else [ts]}


def test_eslestir_ts_tam_benzerlik_ve_klip_no():
    klipler = {1: _klip(1, "loopback", "10:00:00", raw="rapor perşembeye yetişir mi"),
               2: _klip(2, "mikrofon", "10:00:00", raw="evet bence yetişir ama test lazım"),
               3: _klip(3, "loopback", "10:00:30", raw="Jira kaydını açalım")}
    satirlar = [
        {"id": 1, "ts": "10:00:00", "speaker": KIM_A, "text": "Rapor perşembeye yetişir mi", "kaynak": "ses"},
        {"id": 2, "ts": "10:00:00", "speaker": "ben", "text": "Evet bence yetişir ama test lazım", "kaynak": "ses"},
        {"id": 3, "ts": "10:00:31", "speaker": KIM_B, "text": "Jira kaydını açalım", "kaynak": "ses"},   # pencere
        {"id": 4, "ts": "10:00:05", "speaker": KIM_B, "text": "altyazidan satir", "kaynak": "altyazi"},
        {"id": 5, "ts": "11:00:00", "speaker": KIM_B, "text": "hicbir klibe uymaz", "kaynak": "ses"},
        {"id": 6, "ts": "10:05:00", "speaker": KIM_A, "text": "dogrudan", "kaynak": "ses", "klip_no": 3},
    ]
    esl, say = olcum.eslestir(klipler, satirlar)
    assert [s["id"] for s in esl[1]] == [1]
    assert [s["id"] for s in esl[2]] == [2]                  # ayni ts, metin benzerligi ayirir
    assert sorted(s["id"] for s in esl[3]) == [3, 6]
    assert say["altyazi"] == 1 and say["eslesmeyen"] == 1 and say["klip_no"] == 1
    assert say["belirsiz_aday"] == 2 and say["ts_pencere"] == 1


# ---------------------------------------------------------------- metrikler

def test_hesapla_ser_belirsiz_halusinasyon_wer():
    klipler = {1: _klip(1, "loopback", "10:00:00", raw="rapor perşembeye hazır"),
               2: _klip(2, "loopback", "10:00:10", raw="jira kaydı açalım"),
               3: _klip(3, "loopback", "10:00:20", raw="belirsiz biri"),
               4: _klip(4, "loopback", "10:00:30", raw="bir sonraki sefer"),
               5: _klip(5, "mikrofon", "10:00:40", raw="tamam ben bakarım")}
    gercek = {1: {"klip_no": 1, "speaker": KIM_A, "cakisma": False, "halusinasyon": False,
                  "text": "Rapor perşembeye hazır."},
              2: {"klip_no": 2, "speaker": KIM_B, "cakisma": False, "halusinasyon": False,
                  "text": "Jira kaydı açalım"},
              4: {"klip_no": 4, "speaker": "sessizlik", "cakisma": False, "halusinasyon": True, "text": ""}}
    satirlar = [
        {"id": 1, "ts": "10:00:00", "speaker": KIM_A, "text": "Rapor perşembeye hazır", "kaynak": "ses"},   # dogru
        {"id": 2, "ts": "10:00:10", "speaker": KIM_A, "text": "Jira kaydı açalım", "kaynak": "ses"},         # YANLIS kesin
        {"id": 3, "ts": "10:00:20", "speaker": "?", "text": "belirsiz biri", "kaynak": "ses"},               # belirsiz
        {"id": 4, "ts": "10:00:30", "speaker": KIM_A + "?", "text": "bir sonraki sefer", "kaynak": "ses"},    # halusinasyon
        {"id": 5, "ts": "10:00:40", "speaker": "Ben", "text": "tamam ben bakarım", "kaynak": "ses"},
    ]
    m = olcum.hesapla(klipler, gercek, satirlar)
    assert m["klip"] == {"toplam": 5, "etiketli": 3, "akis": {"loopback": 4, "mikrofon": 1}, "cakisma_etiketli": 0}
    assert m["satir"]["belirsiz"] == 2 and m["satir"]["belirsiz_oran"] == 0.4
    assert m["ser_kesin"] == {"kesin_satir": 2, "yanlis": 1, "oran": 0.5}
    assert m["halusinasyon"] == {"klip": 1, "satir_cikaran": 1, "kesin_satir_cikaran": 0}
    assert m["wer"]["hata"] == 0 and m["wer"]["referans_kelime"] == 6 and m["wer"]["klip"] == 2
    assert m["wer"]["whisper_ham_oran"] == 0.0
    # cikti ad ya da metin tasimaz
    duz = json.dumps(m, ensure_ascii=False)
    assert KIM_A not in duz and "rapor" not in duz.lower()


def test_hesapla_cakisma_devir_ve_yanlis_ayni_anda():
    klipler = {1: _klip(1, "loopback", "10:00:00"), 2: _klip(2, "loopback", "10:00:10")}
    gercek = {1: {"klip_no": 1, "speaker": f"{KIM_A} / {KIM_B}", "cakisma": True, "halusinasyon": False, "text": "a"},
              2: {"klip_no": 2, "speaker": f"{KIM_A} → {KIM_B}", "cakisma": False, "halusinasyon": False, "text": "b"}}
    satirlar = [
        {"id": 1, "ts": "10:00:00", "speaker": KIM_A, "text": "a", "kaynak": "ses"},                 # cakismada kisilerden biri: dogru
        {"id": 2, "ts": "10:00:10", "speaker": f"{KIM_A} / {KIM_B}", "text": "b", "kaynak": "ses"},   # sirali devir 'aynı anda' gosterildi
    ]
    m = olcum.hesapla(klipler, gercek, satirlar)
    assert m["yanlis_ayni_anda"] == {"satir": 1, "sirali_devir_klip": 1}
    assert m["ser_kesin"]["yanlis"] == 0 and m["ser_kesin"]["kesin_satir"] == 1


def test_gercek_son_kayit_gecerli_ve_satirlar_parcadan(tmp_path):
    k = str(tmp_path)
    _jsonl(os.path.join(k, "olcum", "gercek.jsonl"),
           [{"klip_no": 1, "speaker": "x", "text": "ilk"}, {"klip_no": 1, "speaker": "y", "text": "son"}])
    assert olcum.gercek_oku(k)[1]["speaker"] == "y"
    os.makedirs(os.path.join(k, "parcalar"))
    with open(os.path.join(k, "parcalar", "parca_001.json"), "w", encoding="utf-8") as f:
        json.dump({"satirlar": [{"id": 1, "ts": "10:00:00", "speaker": KIM_A, "text": "t", "kaynak": "ses"}]}, f)
    assert [s["id"] for s in olcum.satirlar_oku(k)] == [1]


def test_token_orani(tmp_path):
    k = str(tmp_path)
    _jsonl(os.path.join(k, "llm_log.jsonl"), [
        {"olay": "ayar"},
        {"cevap_karakter": 300, "dusunce_karakter": 100, "cikti_token": 100, "giris_token": 1000,
         "sunucu_giris_token": 1500},
        {"cevap_karakter": 100, "dusunce_karakter": 0, "cikti_token": 50, "giris_token": 500, "sunucu_giris_token": 500,
         "giris_karakter": 1600},
    ])
    t = olcum.token_orani(k)
    assert t["kayit"] == 3
    assert t["cikti"] == {"cagri": 2, "karakter_token": round(500 / 150, 3)}
    assert t["girdi"]["karakter_token"] == 3.2 and t["girdi"]["sunucu_tahmin_orani"] == round(2000 / 1500, 3)


# ---------------------------------------------------------------- ses kaydi

class _SahteStt:
    verbose = True
    yeniden_deneme = 0

    def __init__(self, segmentler):
        self._seg = segmentler

    def coz(self, ses_dizisi, onceki=""):
        return self._seg


def _klip_ogesi(no=0, akis="loopback", ornek_bas=16000, capa=T0):
    x = (0.1 * np.sin(np.arange(16000 * 2) / 7.0)).astype("float32")
    basla = dt.datetime.fromtimestamp(capa + ornek_bas / ses.ORNEK)
    return {"kaynak": akis, "basla": basla, "bitis": basla + dt.timedelta(seconds=2), "ses": x, "no": no,
            "ornek_bas": ornek_bas, "ornek_bit": ornek_bas + len(x), "epoch_capa": capa}


def test_olcum_kaydi_wav_ve_jsonl(tmp_path):
    kayit = ses.OlcumKaydi(str(tmp_path))
    p = _klip_ogesi(no=0)
    kayit.klip_yaz(p)
    assert os.path.exists(os.path.join(str(tmp_path), "olcum", "klipler", "0001.wav"))
    assert not os.path.exists(kayit.jsonl)                         # satir sonuc gelince yazilir
    kayit.sonuc(p, "merhaba dünya", 0.31, ["12:00:01"], [{"start": 0.0, "end": 1.9, "text": "merhaba dünya"}])
    (k,) = _oku_jsonl(kayit.jsonl)
    assert k["no"] == 1 and k["akis"] == "loopback" and k["raw"] == "merhaba dünya" and k["stt_sure"] == 0.31
    assert k["ornek_bas"] == 16000 and k["ornek_bit"] == 16000 + 32000
    assert k["bas"] == round(T0 + 1.0, 3) and k["bit"] == round(T0 + 3.0, 3) and k["sure"] == 2.0
    assert k["ts"] == "12:00:01" and k["tsler"] == ["12:00:01"] and k["segmentler"][0]["text"] == "merhaba dünya"
    # wav: 16 kHz mono int16
    import wave
    with wave.open(os.path.join(str(tmp_path), "olcum", "klipler", "0001.wav"), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 16000, 32000)
    # ikinci kayit ayni klasore: numara cakismaz
    assert ses.OlcumKaydi(str(tmp_path))._ofset == 1


def test_olcum_kaydi_io_hatasi_istisna_sizdirmaz(tmp_path):
    olaylar = []
    engel = tmp_path / "olcum"
    engel.write_text("dosya, klasor degil")                     # makedirs basarisiz olur
    kayit = ses.OlcumKaydi(str(tmp_path), olay=lambda t, v: olaylar.append((t, v)))
    assert not kayit.acik
    p = _klip_ogesi()
    kayit.klip_yaz(p)
    kayit.sonuc(p, "x", 0.1)
    assert [t for t, _ in olaylar] == ["log"]                  # bir kez bildirildi


def test_ses_servisi_calis_klip_ve_sonucu_yazar(tmp_path):
    satirlar = []
    s = ses.SesServisi("", satir_fn=lambda *a: satirlar.append(a), olcum_klasor=str(tmp_path))
    s.stt = _SahteStt([{"start": 0.0, "end": 1.9, "text": "Raporu perşembeye yetiştirelim.",
                        "avg_logprob": -0.3, "no_speech_prob": 0.01}])
    s.kuyruk.put(_klip_ogesi(no=0))
    s._dur.set()
    s._calis()                                                # kuyruk bosalinca doner
    (k,) = _oku_jsonl(os.path.join(str(tmp_path), "olcum", "klipler.jsonl"))
    assert k["raw"] == "Raporu perşembeye yetiştirelim." and len(k["tsler"]) == 1 and k["ts"] == k["tsler"][0]
    assert k["segmentler"][0]["avg_logprob"] == -0.3
    assert len(satirlar) == 1 and satirlar[0][0] == k["ts"]    # satirin ts'si klip kaydindakiyle ayni


def test_ses_servisi_stt_hatasi_kaydi_hataliIsaretler(tmp_path):
    class Bozuk(_SahteStt):
        def coz(self, *a, **k):
            raise RuntimeError("kesinti")

    s = ses.SesServisi("", olcum_klasor=str(tmp_path))
    s.stt = Bozuk([])
    s.kuyruk.put(_klip_ogesi())
    s._dur.set()
    s._calis()
    (k,) = _oku_jsonl(os.path.join(str(tmp_path), "olcum", "klipler.jsonl"))
    assert k["raw"] is None and "kesinti" in k["stt_hata"]


def test_akis_yakalayici_ornek_sayaci_ve_capa():
    rng = np.random.default_rng(1)

    def blok(g):
        return (g * rng.standard_normal(ses.BLOK)).astype("float32")

    bloklar = [blok(0.0005) for _ in range(10)] + [blok(0.1) for _ in range(20)] + [blok(0.0005) for _ in range(10)]

    class Sahte(ses.AkisYakalayici):
        def _bloklar_mikrofon(self):
            yield from bloklar

    q = queue.Queue()
    a = Sahte("mikrofon", q)
    once = time.time()
    a.run()
    p = q.get()
    assert a.toplam_ornek == 40 * ses.BLOK
    # konusma 10. blokta basladi; on-yuk 3 blok geri alir
    assert p["ornek_bas"] == 7 * ses.BLOK
    assert p["ornek_bit"] - p["ornek_bas"] == len(p["ses"])
    assert once - 1 <= p["epoch_capa"] <= time.time()


def test_yakalayici_okuma_kaydi(tmp_path):
    y = yakalayici.Yakalayici()
    assert y.okuma_kaydi_ac(str(tmp_path)).endswith(os.path.join("olcum", "okumalar.jsonl"))
    a = (KIM_A, "IFRS dokuz raporunda PD kolonu string geliyor")
    b = (KIM_B, "Test ortamı perşembe hazır olur diye düşünüyorum")
    y.grup = object()
    y._ciftler = lambda: [a, b]
    y.oku()
    y._ciftler = lambda: []
    y.oku()
    o = _oku_jsonl(os.path.join(str(tmp_path), "olcum", "okumalar.jsonl"))
    assert [x["okuma_no"] for x in o] == [1, 2]
    assert o[0]["gorunen"] == [list(a), list(b)] and o[1]["gorunen"] == []
    assert 0 <= o[0]["bit_epoch"] - o[0]["bas_epoch"] < 5
    # kapaliyken (varsayilan) hicbir sey yazilmaz
    y2 = yakalayici.Yakalayici()
    y2.grup, y2._ciftler = object(), (lambda: [a])
    y2.oku()
    assert y2._okuma_yolu is None


# ---------------------------------------------------------------- etiketle

def _kayit_klasoru(tmp_path):
    """Sentetik kayit: 2 klip + okumalar (replay ve etiketle icin)."""
    k = str(tmp_path / "toplanti")
    ts1 = dt.datetime.fromtimestamp(T0 + 10).strftime("%H:%M:%S")
    ts2 = dt.datetime.fromtimestamp(T0 + 30).strftime("%H:%M:%S")
    metin1 = "Bugün raporu perşembeye yetiştirelim mi acaba"
    _jsonl(os.path.join(k, "olcum", "klipler.jsonl"), [
        {"no": 1, "akis": "loopback", "bas": T0 + 10, "bit": T0 + 14, "ornek_bas": 0, "ornek_bit": 64000, "sure": 4.0,
         "ts": ts1, "tsler": [ts1], "raw": metin1 + ".", "stt_sure": 0.4},
        {"no": 2, "akis": "loopback", "bas": T0 + 30, "bit": T0 + 33, "ornek_bas": 0, "ornek_bit": 48000, "sure": 3.0,
         "ts": ts2, "tsler": [ts2], "raw": "Bir sonraki sefere görüşürüz.", "stt_sure": 0.3},
    ])
    okumalar = []
    for i in range(12):                                       # panelde KIM_A satiri 10-16 sn arasi gorunur
        an = T0 + 10 + i * 0.6
        okumalar.append({"okuma_no": i + 1, "bas_epoch": an - 0.1, "bit_epoch": an,
                         "gorunen": [[KIM_A, metin1]]})
    okumalar.append({"okuma_no": 99, "bas_epoch": T0 + 20, "bit_epoch": T0 + 20.1, "gorunen": [[KIM_B, "x"]]})
    _jsonl(os.path.join(k, "olcum", "okumalar.jsonl"), okumalar)
    return k


def test_etiketle_oturumu_devam_edilebilir(tmp_path):
    k = _kayit_klasoru(tmp_path)
    cevaplar = iter(["1", "", "s"])                           # klip 1: aday 1, ham metin kabul; klip 2: sessizlik
    cal = []
    n = etiketle.etiketle(k, giris=lambda soru: next(cevaplar), yazdir=lambda *a: None, oynat_fn=cal.append)
    assert n == 2 and len(cal) == 2 and cal[0].endswith("0001.wav")
    g = olcum.gercek_oku(k)
    assert g[1]["speaker"] == KIM_A and g[1]["text"].startswith("Bugün raporu") and not g[1]["halusinasyon"]
    assert g[2]["halusinasyon"] and g[2]["speaker"] == "sessizlik"
    # ikinci calistirma: etiketliler atlanir
    assert etiketle.etiketle(k, giris=lambda s: pytest.fail("sorulmamali"), yazdir=lambda *a: None,
                             oynat_fn=lambda y: None) == 0
    # aralik: ilk klipten 0.2 dk'ya kadar yalniz klip 1
    cevaplar = iter(["?", "duzeltilmis metin"])
    etiketle.etiketle(k, bas="0", bit="0.2", tekrar=True, giris=lambda s: next(cevaplar), yazdir=lambda *a: None,
                      oynat_fn=lambda y: None)
    assert olcum.gercek_oku(k)[1]["speaker"] == "?" and olcum.gercek_oku(k)[1]["text"] == "duzeltilmis metin"
    assert olcum.gercek_oku(k)[2]["halusinasyon"]


def test_etiketle_cakisma_ve_devir(tmp_path):
    k = _kayit_klasoru(tmp_path)
    cevaplar = iter(["a", "1 2", "", "d", "1 2", ""])
    etiketle.etiketle(k, giris=lambda s: next(cevaplar), yazdir=lambda *a: None, oynat_fn=lambda y: None)
    g = olcum.gercek_oku(k)
    assert g[1]["cakisma"] and " / " in g[1]["speaker"]
    assert not g[2]["cakisma"] and " → " in g[2]["speaker"]


# ---------------------------------------------------------------- replay

def test_replay_sentetik_kayit(tmp_path):
    k = _kayit_klasoru(tmp_path)
    _jsonl(os.path.join(k, "olcum", "gercek.jsonl"), [
        {"klip_no": 1, "speaker": KIM_A, "cakisma": False, "halusinasyon": False,
         "text": "Bugün raporu perşembeye yetiştirelim mi acaba"},
        {"klip_no": 2, "speaker": "sessizlik", "cakisma": False, "halusinasyon": True, "text": ""}])
    cikti = str(tmp_path / "cikti")
    m, yol = replay.kos(k, cikti=cikti, yazdir=lambda *a: None)
    assert yol == cikti
    satirlar = _oku_jsonl(os.path.join(cikti, "satirlar.jsonl"))
    ses_satirlari = [s for s in satirlar if s.get("klip_no") == 1]
    assert len(ses_satirlari) == 1 and ses_satirlari[0]["speaker"] == KIM_A
    assert ses_satirlari[0]["kaynak"] == "ses" and ses_satirlari[0]["ts"] == olcum.jsonl_oku(
        os.path.join(k, "olcum", "klipler.jsonl"))[0]["ts"]
    assert m["wer"]["hata"] == 0 and m["wer"]["klip"] == 1
    assert m["ser_kesin"]["yanlis"] == 0 and m["ser_kesin"]["kesin_satir"] == 1
    assert m["halusinasyon"]["klip"] == 1
    assert m["replay"] == {"okuma": 13, "klip": 2, "stt": False, "hat": "MevcutHat"}
    assert os.path.exists(os.path.join(cikti, "olcum.json"))
    # replay sonrasi motor/yakalayici gercek saate doner
    import motor
    assert abs(motor.time.time() - time.time()) < 5


def test_replay_hat_dikisi(tmp_path):
    """Faz1 dikisi: Hat sozlesmesini uygulayan baska bir sinif takilabilir; dongu yalniz Hat metotlarini cagirir."""
    k = _kayit_klasoru(tmp_path)

    class IzHatti(replay.Hat):
        olaylar = []

        def okuma(self, an, gorunen):
            IzHatti.olaylar.append("okuma")

        def klip(self, an, kayit, segmentler):
            IzHatti.olaylar.append(("klip", kayit["no"], segmentler[0]["text"][:5]))

        def bitir(self):
            return [{"id": 1, "ts": "00:00:00", "speaker": "?", "text": "x", "kaynak": "ses", "klip_no": 1}]

    m, _ = replay.kos(k, hat_sinifi=IzHatti, cikti=str(tmp_path / "c2"), yazdir=lambda *a: None)
    assert IzHatti.olaylar.count("okuma") == 13 and ("klip", 1, "Bugün") in IzHatti.olaylar
    assert m["replay"]["hat"] == "IzHatti" and m["satir"]["belirsiz"] == 1
    # klipler okumalarla zaman sirasinda: klip 1 (T0+14.4) son okumadan (T0+16.6) once gelir
    assert IzHatti.olaylar.index(("klip", 1, "Bugün")) < len(IzHatti.olaylar) - 1


def test_olcum_cli_cikti_ad_icermez(tmp_path, capsys):
    k = _kayit_klasoru(tmp_path)
    _jsonl(os.path.join(k, "olcum", "gercek.jsonl"),
           [{"klip_no": 1, "speaker": KIM_A, "cakisma": False, "halusinasyon": False, "text": "Bugün raporu"}])
    _jsonl(os.path.join(k, "satirlar.jsonl"),
           [{"id": 1, "ts": "00:00:00", "speaker": KIM_A, "text": "Bugün raporu", "kaynak": "ses", "klip_no": 1}])
    assert olcum.main([k, "--satirlar", os.path.join(k, "satirlar.jsonl"), "--token",
                       "--json", str(tmp_path / "o.json")]) == 0
    cikti = capsys.readouterr().out
    assert "WER" in cikti and "SER-kesin" in cikti
    assert KIM_A not in cikti and "perşembe" not in cikti
    assert json.load(open(str(tmp_path / "o.json"), encoding="utf-8"))["ser_kesin"]["kesin_satir"] == 1


def test_replay_baslat_hatasinda_saat_yamasi_geri_alinir(tmp_path, monkeypatch):
    import time as gercek_time
    import motor as motor_mod
    import sozluk as sozluk_mod
    import yakalayici as yak_mod

    def bozuk(*a, **k):
        raise RuntimeError("sozluk bozuk")
    monkeypatch.setattr(sozluk_mod, "Sozluk", bozuk)
    (tmp_path / "olcum").mkdir()
    (tmp_path / "olcum" / "klipler.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "olcum" / "okumalar.jsonl").write_text(json.dumps(
        {"okuma_no": 1, "bas_epoch": 1760000000.0, "bit_epoch": 1760000000.1, "gorunen": [["Ayşe Demir", "Merhaba"]]})
        + "\n", encoding="utf-8")
    try:
        replay.kos(str(tmp_path), cikti=str(tmp_path / "cikti"), yazdir=lambda *a: None)
    except RuntimeError:
        pass
    assert motor_mod.time is gercek_time and yak_mod.time is gercek_time
