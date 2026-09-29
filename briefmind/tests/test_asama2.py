"""Asama 2 (UYGULAMA_PLANI_v2 bolum C): ayni anda konusma (K5), 'ikisi' modu (A6), ses normalizasyonu (A5),
UIA okuma araligi (A7), TLS dogrulamasi (A8), konusma payi (N7), Jira/Planner CSV (N9). Ag/ses cihazi gerekmez."""
import csv
import datetime as dt
import json
import queue

import numpy as np

import ayar as ayar_mod
import llm
import motor as motor_mod
import not_araclari
import ses
import yakalayici


def _motor(tmp_path, kaynak="ses", ben="Mert"):
    return motor_mod.Motor(str(tmp_path / "t"), "Test", dt.date(2026, 9, 29), duzelt=False, kaynak=kaynak, ben=ben)


def _transkript(m):
    return [(s["speaker"], s["kaynak"], s["text"]) for s in m.mevcut]


# ---------------------------------------------------------------- K5 ayni anda konusma

def test_cakismada_diger_konusmacinin_altyazisi_eklenir_bir_kez(tmp_path):
    m = _motor(tmp_path)
    m.altyazi_satiri({"ts": "10:00:01", "speaker": "Ayşe Demir", "text": "Kampanya bütçesini yarın onaylarım"})
    m.altyazi_satiri({"ts": "10:00:02", "speaker": "Ahmet Yılmaz", "text": "Test ortamı perşembe hazır olacak"})
    # Whisper karisik seste yalnizca baskin sesi (Ahmet) yazdi
    s = m.ses_satiri("10:00:02", None, "Test ortamı perşembe hazır olacak.", "loopback")
    assert s["speaker"] == "Ahmet Yılmaz"
    assert _transkript(m) == [("Ayşe Demir", "altyazi-cakisma", "Kampanya bütçesini yarın onaylarım"),
                              ("Ahmet Yılmaz", "ses", "Test ortamı perşembe hazır olacak.")]
    # ikinci bir Whisper satiri ayni pencerede: Ayse'nin satiri tekrar eklenmez
    m.ses_satiri("10:00:03", None, "Test ortamı perşembe hazır olacak, bekliyoruz", "loopback")
    assert sum(1 for x in _transkript(m) if x[1] == "altyazi-cakisma") == 1
    # Ayse'nin sozu sonradan Whisper'dan da gelirse ikinci kez yazilmaz (altyazi satiri onun yerini tutuyor)
    assert m.ses_satiri("10:00:04", None, "Kampanya bütçesini yarın onaylarım.", "loopback") is None
    assert len(m.mevcut) == 3
    m.kapat()


def test_cakisma_yok_whisper_digerinin_sozunu_de_yazmissa(tmp_path):
    m = _motor(tmp_path)
    m.altyazi_satiri({"ts": "10:00:01", "speaker": "Ayşe Demir", "text": "Kampanya bütçesini onaylarım"})
    m.altyazi_satiri({"ts": "10:00:02", "speaker": "Ahmet Yılmaz", "text": "Test ortamı hazır olacak"})
    # Whisper iki sozu birlestirdi: Ayse'nin kokleri Whisper metninde geciyor -> ek satir yok
    m.ses_satiri("10:00:02", None, "Kampanya bütçesini onaylarım test ortamı hazır olacak", "loopback")
    assert [x[1] for x in _transkript(m)] == ["ses"]
    # uzak (> 4 sn) altyazi satiri cakisma sayilmaz
    m.altyazi_satiri({"ts": "10:00:20", "speaker": "Ayşe Demir", "text": "Raporu cuma günü gönderirim"})
    m.ses_satiri("10:00:30", None, "Toplantıyı burada bitirelim arkadaşlar", "loopback")
    assert "altyazi-cakisma" not in [x[1] for x in _transkript(m)]
    m.kapat()


def test_cakisma_kullanicinin_kendi_altyazisini_eklemez(tmp_path):
    m = _motor(tmp_path)
    m.altyazi_satiri({"ts": "10:00:01", "speaker": "Mert Ali Erentürk", "text": "Jira kaydını ben açarım"})
    m.altyazi_satiri({"ts": "10:00:02", "speaker": "Ahmet Yılmaz", "text": "Test ortamı hazır olacak"})
    m.ses_satiri("10:00:02", None, "Test ortamı hazır olacak.", "loopback")
    assert [x[1] for x in _transkript(m)] == ["ses"]        # kullanicinin sozu mikrofon akisindan gelir
    m.kapat()


# ---------------------------------------------------------------- A6 'ikisi' modu

def test_ikisi_eslesen_whisper_varsa_altyazi_dusulur(tmp_path, monkeypatch):
    saat = [1000.0]
    monkeypatch.setattr(motor_mod.time, "time", lambda: saat[0])
    m = _motor(tmp_path, kaynak="ikisi")
    m.altyazi_satiri({"ts": "10:00:01", "speaker": "Ayşe Demir", "text": "Test ortamı perşembe hazır olacak"})
    assert m.mevcut == []                                    # altyazi satiri hemen girmez
    m.ses_satiri("10:00:01", None, "Test ortamı perşembe hazır olacak.", "loopback")
    saat[0] += 9
    m.kontrol()
    assert _transkript(m) == [("Ayşe Demir", "ses", "Test ortamı perşembe hazır olacak.")]
    # Whisper once, altyazi sonra geldiyse de tek satir
    m.ses_satiri("10:00:10", None, "Kampanya görsellerini cuma gönderirim.", "loopback")
    m.altyazi_satiri({"ts": "10:00:11", "speaker": "Ayşe Demir", "text": "Kampanya görsellerini cuma gönderirim"})
    saat[0] += 9
    m.kontrol()
    assert len(m.mevcut) == 2 and all(s["kaynak"] == "ses" for s in m.mevcut)
    m.kapat()


def test_ikisi_whisper_kacirdiysa_altyazi_satiri_eklenir(tmp_path, monkeypatch):
    saat = [1000.0]
    monkeypatch.setattr(motor_mod.time, "time", lambda: saat[0])
    m = _motor(tmp_path, kaynak="ikisi")
    m.altyazi_satiri({"ts": "10:00:01", "speaker": "Ayşe Demir", "text": "Raporu cuma günü gönderirim"})
    saat[0] += 5
    m.kontrol()
    assert m.mevcut == []                                    # 8 sn dolmadi
    saat[0] += 4
    m.kontrol()
    assert _transkript(m) == [("Ayşe Demir", "altyazi", "Raporu cuma günü gönderirim")]
    m.kontrol()
    assert len(m.mevcut) == 1                                # bir kez
    # sonradan gelen ayni sozlu Whisper satiri ikinci kez yazilmaz
    assert m.ses_satiri("10:00:02", None, "Raporu cuma günü gönderirim.", "loopback") is None
    # bitiste suresi dolmamis altyazi satiri da kaybolmaz
    m.altyazi_satiri({"ts": "10:00:30", "speaker": "Ahmet Yılmaz", "text": "Toplantıyı burada bitirelim"})
    monkeypatch.setattr(llm, "bolum_ozetle", lambda *a, **k: None)          # LLM sunucusu yok
    m.bitir()
    satirlar = m.tum_satirlar()
    assert [s["text"] for s in satirlar][-1] == "Toplantıyı burada bitirelim"
    m.kapat()


def test_altyazi_modu_hemen_ekler(tmp_path):
    m = _motor(tmp_path, kaynak="altyazi")
    m.altyazi_satiri({"ts": "10:00:01", "speaker": "Ayşe Demir", "text": "Raporu cuma günü gönderirim"})
    assert _transkript(m) == [("Ayşe Demir", "altyazi", "Raporu cuma günü gönderirim")]
    m.kapat()


# ---------------------------------------------------------------- A5 ses normalizasyonu

def _gonderilen(genlik):
    q = queue.Queue()
    a = ses.AkisYakalayici("mikrofon", q)
    t = np.arange(ses.ORNEK * 2) / ses.ORNEK
    a._gonder([(genlik * np.sin(2 * np.pi * 220 * t)).astype("float32")], None)
    return q.get_nowait()["ses"]


def test_kisik_ses_yukseltilir_yuksek_ses_degismez():
    kisik = _gonderilen(0.2)
    assert abs(float(np.max(np.abs(kisik))) - 0.9) < 0.01 and kisik.dtype == np.float32
    yuksek = _gonderilen(0.5)
    assert abs(float(np.max(np.abs(yuksek))) - 0.5) < 0.01
    # cok kisik: kazanc 8 ile sinirli, kirpma [-1, 1]
    cok_kisik = ses.normalize_et(np.full(100, 0.01, dtype="float32"))
    assert abs(float(cok_kisik.max()) - 0.08) < 1e-6
    assert float(np.max(np.abs(ses.normalize_et(np.array([0.29, -0.01], dtype="float32"))))) <= 1.0


# ---------------------------------------------------------------- A7 UIA yuku

def test_okuma_suresi_araligi_belirler(monkeypatch):
    y = yakalayici.Yakalayici()
    assert y.onerilen_aralik() == 0.6
    saat = [0.0]
    sure = [0.4]
    monkeypatch.setattr(yakalayici.time, "perf_counter", lambda: saat[0])

    def _oku():
        saat[0] += sure[0]
        return []
    y._oku = _oku
    for _ in range(10):
        y.oku()
    assert y.onerilen_aralik() == 1.2                        # ortalama 400 ms
    sure[0] = 0.05
    for _ in range(2):
        y.oku()
    assert y.onerilen_aralik() == 1.2                        # son 10: 8x0.4 + 2x0.05 -> ort. 330 ms
    for _ in range(3):
        y.oku()
    assert y.onerilen_aralik() == 0.6                        # 5x0.4 + 5x0.05 -> ort. 225 ms


# ---------------------------------------------------------------- A8 TLS

def test_tls_dogrulama_ca_bundle(tmp_path, monkeypatch):
    cer = tmp_path / "kurum_kok.cer"
    cer.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    assert ayar_mod.tls_dogrulama({"ca_bundle": str(cer)}) == str(cer)
    assert ayar_mod.tls_dogrulama({"ca_bundle": str(tmp_path / "yok.cer")}) is False
    assert ayar_mod.tls_dogrulama({"ca_bundle": ""}) is False
    assert ayar_mod.tls_dogrulama({}) is False
    # ayar verilmezse config.json okunur
    monkeypatch.chdir(tmp_path)
    assert ayar_mod.tls_dogrulama() is False
    (tmp_path / "config.json").write_text(json.dumps({"ca_bundle": str(cer)}), encoding="utf-8")
    assert ayar_mod.tls_dogrulama() == str(cer)
    # STT istemcisi: requests verify= bu yol
    assert ses.SttIstemci("https://stt.ornek").verify == str(cer)
    assert ses.SttIstemci("https://stt.ornek", verify=False).verify is False


def test_stt_istekleri_verify_tasir(monkeypatch):
    gorulen = []

    class Oturum:
        trust_env = True

        def get(self, url, **kw):
            gorulen.append(kw.get("verify"))
            return type("C", (), {"status_code": 200})()

    s = ses.SttIstemci("https://stt.ornek", verify="C:/kok.cer")
    s._oturum = Oturum()
    assert s.saglik() and gorulen == ["C:/kok.cer"]


def test_llm_ayarla_ca_bundle(tmp_path, monkeypatch):
    istenen = []
    gercek = llm.ssl.create_default_context
    monkeypatch.setattr(llm.ssl, "create_default_context", lambda cafile=None: (istenen.append(cafile), gercek())[1])
    eski = {"route": llm.ROUTE, "model": llm.MODEL}
    cer = tmp_path / "kok.cer"
    cer.write_text("x", encoding="utf-8")
    try:
        llm.ayarla({"ca_bundle": str(cer)})             # Ayarlar -> Kaydet de ayni yoldan gecer
        assert llm.TLS_DOGRULAMA == str(cer) and istenen == [str(cer)] and llm.TLS_HATASI is None
        istenen.clear()
        llm.ayarla({})
        assert llm.TLS_DOGRULAMA is False and istenen == []
    finally:
        monkeypatch.undo()
        llm.ayarla(eski)


def test_llm_bozuk_sertifika_uygulamayi_dusurmez(tmp_path):
    cer = tmp_path / "bozuk.cer"
    cer.write_text("sertifika degil", encoding="utf-8")
    eski = {"route": llm.ROUTE, "model": llm.MODEL}
    try:
        llm.ayarla({"ca_bundle": str(cer)})
        assert llm.TLS_DOGRULAMA is False and llm.TLS_HATASI
    finally:
        llm.ayarla(eski)
    assert llm.TLS_HATASI is None


# ---------------------------------------------------------------- N7 konusma payi

def test_konusma_paylari():
    satirlar = [
        {"ts": "10:00:00", "speaker": "Elif Bala", "text": "Başlayalım"},
        {"ts": "10:00:10", "speaker": "Berk Nacar", "text": "Tamam"},
        {"ts": "10:00:15", "speaker": "Elif Bala", "text": "Bir"},
        {"ts": "10:01:15", "speaker": "Cemil Kahveci", "text": "Mola sonrası devam edelim mi"},  # 60 sn -> 15 tavan
        {"ts": "10:01:20", "speaker": "Berk Nacar", "text": "bir iki üç dört beş"},             # son satir: 5/2.5
    ]
    p = not_araclari.konusma_paylari(satirlar)
    assert p == [("Elif Bala", 25.0, 68), ("Berk Nacar", 7.0, 19), ("Cemil Kahveci", 5.0, 14)]
    assert not_araclari.konusma_payi_metni(p) == "Konuşma payı: Elif Bala %68, Berk Nacar %19, Cemil Kahveci %14"
    assert not_araclari.konusma_paylari([]) == [] and not_araclari.konusma_payi_metni([]) == ""


def test_konusma_paylari_meta_json_a_yazilir(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "bolum_ozetle", lambda *a, **k: {"ozet": "MVP konuşuldu.", "konular": [],
                                                              "kararlar": [], "aksiyonlar": []})
    m = _motor(tmp_path, kaynak="altyazi")
    m.satir_ekle({"ts": "10:00:00", "speaker": "Elif Bala", "text": "MVP prototipini ben hazırlarım"})
    m.satir_ekle({"ts": "10:00:10", "speaker": "Berk Nacar", "text": "Test ortamı hazır olacak"})
    m.parca_kapat("test")
    import concurrent.futures as cf
    cf.wait(m.isler)
    monkeypatch.setattr(llm, "birlestir", lambda *a, **k: "## Özet\nMVP konuşuldu.\n")
    not_md = m.notu_uret()
    meta = json.loads((tmp_path / "t" / "meta.json").read_text(encoding="utf-8"))
    assert meta["durum"] == "tamam" and meta["konusma_paylari"][0][0] == "Elif Bala"
    assert "Konuşma payı" not in not_md
    m.kapat()


# ---------------------------------------------------------------- N9 Jira/Planner CSV

def test_jira_csv(tmp_path):
    aks = [{"madde": "MVP prototipini hazırlamak", "sorumlu": "Elif Bala", "tarih": "2026-10-01 (Perşembe)",
            "kaynak": "⏱10:02:00"},
           {"madde": "Ara tabloyu incelemek", "sorumlu": "Berk Nacar, Mert Ali Erentürk", "tarih": "sprint sonu",
            "kaynak": ""},
           {"madde": "Kayıtları iletmek", "sorumlu": "belirsiz", "tarih": "-", "kaynak": ""},
           {"madde": "-", "sorumlu": "Elif Bala", "tarih": "-", "kaynak": ""}]
    yol = tmp_path / "aksiyonlar.csv"
    assert not_araclari.jira_csv(aks, str(yol), "Strateji (2026-09-23)") == 3
    ham = yol.read_bytes()
    assert ham.startswith(b"\xef\xbb\xbf")                    # UTF-8 BOM (Excel Turkce karakter)
    with open(yol, encoding="utf-8-sig", newline="") as f:
        satirlar = list(csv.reader(f))
    assert satirlar[0] == ["Summary", "Assignee", "Due Date", "Description", "Issue Type"]
    assert satirlar[1] == ["MVP prototipini hazırlamak", "Elif Bala", "2026-10-01",
                           "BriefMind — Strateji (2026-09-23) · kaynak 10:02:00", "Task"]
    assert satirlar[2][1:3] == ["Berk Nacar", ""]
    assert "sorumlular: Berk Nacar, Mert Ali Erentürk" in satirlar[2][3]
    assert satirlar[3][:3] == ["Kayıtları iletmek", "", ""] and satirlar[3][4] == "Task"
