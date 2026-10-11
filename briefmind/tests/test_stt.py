"""stt.py: ses.py'den ayrilan STT istemcisi/suzgecleri, yeniden export, toplam sure siniri, kesinti kuyrugu (ag/cihaz yok)."""
import threading

import numpy as np
import pytest

import ses
import stt


# ---------------------------------------------------------------- yeniden export

def test_ses_stt_adlarini_yeniden_export_eder():
    for ad in ("SttIstemci", "sonuc_gecerli", "tekrar_temizle", "cumlelere_bol", "wav_bayt", "segment_ozeti",
               "HALUSINASYON", "KISA_GECERLI", "ORNEK", "KesintiKuyrugu", "kesinti_hatasi_mi", "wav_oku"):
        assert getattr(ses, ad) is getattr(stt, ad), ad
    assert ses.requests is stt.requests                 # eski testler ses.requests.* kullanir
    assert stt.ORNEK == 16000 == ses.ORNEK
    assert not hasattr(ses, "kulaklik_var_mi")          # duyulur ton testi kalkti (pasif korelasyon)


def test_tekrar_temizle_ve_halusinasyon():
    assert stt.tekrar_temizle("Selamlar. Selamlar. Selamlar.") == "Selamlar."
    assert stt.tekrar_temizle("bu hangi sebepten dolayı bu hangi sebepten dolayı bu hangi sebepten dolayı") \
        == "Bu hangi sebepten dolayı"
    assert stt.tekrar_temizle("") == ""
    assert any("abone" in h for h in stt.HALUSINASYON)
    assert not stt.sonuc_gecerli({}, "Kanalıma abone olmayı unutmayın.")
    assert not stt.sonuc_gecerli({"no_speech_prob": 0.9}, "Hmm hmm hmm.")
    assert stt.sonuc_gecerli({}, "Raporu perşembeye yetiştirelim.")


def test_segment_ozeti_ve_wav_gidis_donus(tmp_path):
    ozet = stt.segment_ozeti([{"start": 0, "end": 1.5, "text": " merhaba ", "avg_logprob": -0.31234,
                               "no_speech_prob": 0.01}, {"text": "x"}])
    assert ozet[0] == {"start": 0, "end": 1.5, "text": "merhaba", "avg_logprob": -0.312, "no_speech_prob": 0.01}
    assert ozet[1]["text"] == "x" and "avg_logprob" not in ozet[1]
    x = (0.5 * np.sin(np.arange(8000) / 9.0)).astype("float32")
    yol = tmp_path / "k.wav"
    yol.write_bytes(stt.wav_bayt(x))
    y = stt.wav_oku(str(yol))
    assert len(y) == 8000 and float(np.max(np.abs(y - x))) < 1e-3


# ---------------------------------------------------------------- istemci: zaman asimi / yeniden deneme

class _Oturum:
    trust_env = True

    def __init__(self):
        self.cagrilar = []

    def post(self, url, **kw):
        self.cagrilar.append((url, kw))
        return type("C", (), {"status_code": 200, "json": lambda self_: {"text": "tamam"},
                              "raise_for_status": lambda self_: None})()

    def close(self):
        pass


def test_istek_zaman_asimi_klip_suresine_olceklenir():
    s = stt.SttIstemci("https://stt.ornek", proxy=False)
    o = s._oturum = _Oturum()
    s.coz(np.zeros(16000 * 2, dtype="float32"))
    s.coz(np.zeros(16000 * 60, dtype="float32"))
    (_, kisa), (_, uzun) = o.cagrilar
    assert kisa["timeout"] == (stt.SttIstemci.BAGLANTI_ZAMAN_ASIMI, 30.0)      # taban
    assert uzun["timeout"] == (stt.SttIstemci.BAGLANTI_ZAMAN_ASIMI, 180.0)     # zaman_asimi ile sinirli


def test_toplam_sure_siniri_yeniden_denemeyi_keser(monkeypatch):
    monkeypatch.setattr(stt.time, "sleep", lambda s: None)
    s = stt.SttIstemci("https://stt.ornek")
    s.TOPLAM_SN = -1                                           # sure doldu: ilk hatadan sonra yeni deneme yok
    sayac = []

    def istek(ses_, bicim, onceki=""):
        sayac.append(1)
        raise stt.requests.Timeout("x")

    monkeypatch.setattr(s, "_istek", istek)
    with pytest.raises(stt.requests.Timeout):
        s.coz(np.zeros(16000, dtype="float32"))
    assert len(sayac) == 1
    s2 = stt.SttIstemci("https://stt.ornek")
    sayac2 = []

    def istek2(ses_, bicim, onceki=""):
        sayac2.append(1)
        raise stt.requests.Timeout("x")

    monkeypatch.setattr(s2, "_istek", istek2)
    with pytest.raises(stt.requests.Timeout):
        s2.coz(np.zeros(16000, dtype="float32"))
    assert len(sayac2) == s2.DENEME                            # sinir dolmadiysa tum denemeler yapilir


def test_429_ve_503_tekrar_denenir(monkeypatch):
    monkeypatch.setattr(stt.time, "sleep", lambda s: None)
    s = stt.SttIstemci("https://stt.ornek")

    class C:
        def __init__(self, kod):
            self.status_code = kod

        def json(self):
            return {"text": "Raporu perşembeye yetiştirelim."}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise stt.requests.HTTPError(str(self.status_code), response=self)

    sira = [C(429), C(503), C(200)]
    monkeypatch.setattr(s, "_istek", lambda *a, **k: sira.pop(0))
    assert s.coz(np.zeros(16000, dtype="float32"))[0]["text"].startswith("Raporu")
    assert s.yeniden_deneme == 1


def test_oturum_is_parcacigi_basina():
    s = stt.SttIstemci("https://stt.ornek")
    ana = s._oturum
    assert s._oturum is ana
    gorulen = []
    t = threading.Thread(target=lambda: gorulen.append(s._oturum))
    t.start()
    t.join()
    assert gorulen[0] is not ana                               # biri kapatinca digeri bozulmasin


def test_kesinti_hatasi_mi_siniflandirir():
    class H(Exception):
        def __init__(self, kod):
            self.response = type("R", (), {"status_code": kod})()

    assert stt.kesinti_hatasi_mi(stt.requests.ConnectionError("x"))
    assert stt.kesinti_hatasi_mi(RuntimeError("x"))
    assert stt.kesinti_hatasi_mi(H(503)) and stt.kesinti_hatasi_mi(H(404)) and stt.kesinti_hatasi_mi(H(401))
    assert not stt.kesinti_hatasi_mi(H(400)) and not stt.kesinti_hatasi_mi(H(422))   # yalniz bu klibe ozgu


# ---------------------------------------------------------------- kesinti kuyrugu

def test_kesinti_kuyrugu_sirali_geri_okur(tmp_path):
    k = stt.KesintiKuyrugu(str(tmp_path))
    x = (0.3 * np.sin(np.arange(16000) / 5.0)).astype("float32")
    for no in (12, 3, 7):
        assert k.ekle({"klip_no": no, "akis": "loopback", "bas": 100.0 + no, "bit": 101.0 + no}, x * (no / 12.0))
    assert [m["klip_no"] for m in k.liste()] == [3, 7, 12]      # klip_no artan
    assert k.sayi() == 3
    m = k.liste()[1]
    y = k.oku(m)
    assert len(y) == 16000 and float(np.max(np.abs(y - x * (7 / 12.0)))) < 1e-3
    k.sil(m)
    assert [mm["klip_no"] for mm in k.liste()] == [3, 12]
    assert (tmp_path / "olcum" / "kesinti").is_dir()


def test_kesinti_kuyrugu_yarim_dosya_listede_gorunmez_io_hatasi_kapatir(tmp_path):
    k = stt.KesintiKuyrugu(str(tmp_path))
    (tmp_path / "olcum" / "kesinti" / "000005.wav").write_bytes(b"x")       # json yok: yarim yazilmis
    (tmp_path / "olcum" / "kesinti" / "000006.json").write_text("{bozuk", encoding="utf-8")
    assert k.liste() == []
    olaylar = []
    engel = tmp_path / "engel"
    engel.write_text("dosya, klasor degil")
    k2 = stt.KesintiKuyrugu(str(tmp_path / "x"), olay=lambda t, v: olaylar.append(t))
    k2.klasor = str(engel / "alt")                             # klasor yok: yazma IO hatasi verir
    assert not k2.ekle({"klip_no": 1}, np.zeros(10, dtype="float32"))
    assert not k2.acik and olaylar == ["log"]
    assert not k2.ekle({"klip_no": 2}, np.zeros(10, dtype="float32"))
    assert olaylar == ["log"]                                  # bir kez bildirildi
