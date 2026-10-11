"""Asama 2 (UYGULAMA_PLANI_v2 bolum C): ayni anda konusma, ses normalizasyonu (A5), UIA okuma araligi (A7), TLS
dogrulamasi (A8), konusma payi (N7), Jira/Planner CSV (N9). Faz 1: K5 (altyazi satirini transkripte ekleme), 'ikisi'
(A6) ve 'altyazi' modlari kaldirildi; ayni anda konusma cizelge araliklarinin kesisimiyle 'A / B (aynı anda)'
olarak isaretlenir, altyazi metni hic eklenmez. Ag/ses cihazi gerekmez."""
import csv
import datetime as dt
import json
import queue

import numpy as np
import pytest

import ayar as ayar_mod
import llm
import motor as motor_mod
import not_araclari
import ses
import yakalayici
from metin import gorunen_ad
from test_motor_faz1 import T0, akit, saat_kur


@pytest.fixture
def saat(monkeypatch):
    return saat_kur(monkeypatch)


A_, B_ = "Ayşe Demir", "Ahmet Yılmaz"


def _motor(tmp_path, ben="Deniz", isle=False):
    m = motor_mod.Motor(str(tmp_path / "t"), "Test", dt.date.fromtimestamp(T0), duzelt=False, ben=ben,
                        katilimcilar=[A_, B_, ben])
    if not isle:
        m._isle = lambda veri, duzelt: None
    return m


# ---------------------------------------------------------------- ayni anda konusma (eski K5)

def test_ayni_anda_konusma_a_b_isaretlenir_altyazi_satiri_eklenmez(tmp_path, saat):
    m = _motor(tmp_path)
    # once iki ayri soz (L capalari), sonra Ayse ve Ahmet ayni anda 4 sn konusur; Whisper tek klip yazdi
    sozler = [(A_, 1, 4), (B_, 6, 9), (A_, 11, 14), (B_, 16, 19), (A_, 21, 26), (B_, 21.2, 26)]
    klipler = [(1, "loopback", 1, 4, "Bir."), (2, "loopback", 6, 9, "İki."), (3, "loopback", 11, 14, "Üç."),
               (4, "loopback", 16, 19, "Dört."), (5, "loopback", 21, 26, "Test ortamı perşembe hazır olacak.")]
    akit(m, saat, sozler, klipler, 30)
    son = m.mevcut[-1]
    assert son["cakisma"] and son["speaker"] in (f"{A_} / {B_}", f"{B_} / {A_}")
    assert gorunen_ad(son).endswith("(aynı anda)")
    assert [x["kaynak"] for x in m.mevcut] == ["ses"] * 5            # altyazi satiri transkripte girmedi
    assert son["speaker"] not in m.katilimcilar
    m.kapat()


def test_ikisi_ve_altyazi_modlari_yok_kaynak_hep_ses(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                                      # yerel config.json okunmasin
    m = motor_mod.Motor(str(tmp_path / "t"), "Test", dt.date(2026, 9, 29), duzelt=False)
    assert m.kaynak == "ses"
    m.altyazi_satiri({"ts": "10:00:01", "speaker": A_, "text": "Raporu cuma günü gönderirim"})
    m.kontrol()
    assert m.mevcut == []                                            # 'altyazi'/'ikisi' gibi eklenmez
    assert ayar_mod.ayar_oku()["kaynak"] == "ses" and ayar_mod.ayar_oku()["altyazi_yedek"] is False
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
        {"ts": "10:00:00", "speaker": "Selin Kara", "text": "Başlayalım"},
        {"ts": "10:00:10", "speaker": "Emre Sancak", "text": "Tamam"},
        {"ts": "10:00:15", "speaker": "Selin Kara", "text": "Bir"},
        {"ts": "10:01:15", "speaker": "Kemal Tuncer", "text": "Mola sonrası devam edelim mi"},  # 60 sn -> 15 tavan
        {"ts": "10:01:20", "speaker": "Emre Sancak", "text": "bir iki üç dört beş"},             # son satir: 5/2.5
    ]
    p = not_araclari.konusma_paylari(satirlar)
    assert p == [("Selin Kara", 25.0, 68), ("Emre Sancak", 7.0, 19), ("Kemal Tuncer", 5.0, 14)]
    assert not_araclari.konusma_payi_metni(p) == "Konuşma payı: Selin Kara %68, Emre Sancak %19, Kemal Tuncer %14"
    assert not_araclari.konusma_paylari([]) == [] and not_araclari.konusma_payi_metni([]) == ""


def test_konusma_paylari_meta_json_a_yazilir(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "bolum_ozetle", lambda *a, **k: {"ozet": "MVP konuşuldu.", "konular": [],
                                                              "kararlar": [], "aksiyonlar": []})
    m = _motor(tmp_path, isle=True)
    m.satir_ekle({"ts": "10:00:00", "speaker": A_, "text": "MVP prototipini ben hazırlarım",
                  "spk_guven": 0.9}, kaynak="ses")                 # kesin konusmaci (altyazi yedegi 'Ad?' sayilir, paya girmez)
    m.satir_ekle({"ts": "10:00:10", "speaker": B_, "text": "Test ortamı hazır olacak",
                  "spk_guven": 0.9}, kaynak="ses")
    m.parca_kapat("test")
    import concurrent.futures as cf
    cf.wait(m.isler)
    monkeypatch.setattr(llm, "birlestir", lambda *a, **k: "## Özet\nMVP konuşuldu.\n")
    not_md = m.notu_uret()
    meta = json.loads((tmp_path / "t" / "meta.json").read_text(encoding="utf-8"))
    assert meta["durum"] == "tamam" and meta["konusma_paylari"][0][0] == A_ and meta["konusma_belirsiz"] == 0
    assert "Konuşma payı" not in not_md
    m.kapat()


# ---------------------------------------------------------------- N9 Jira/Planner CSV

def test_jira_csv(tmp_path):
    aks = [{"madde": "MVP prototipini hazırlamak", "sorumlu": "Selin Kara", "tarih": "2026-10-01 (Perşembe)",
            "kaynak": "⏱10:02:00"},
           {"madde": "Ara tabloyu incelemek", "sorumlu": "Emre Sancak, Deniz Can Öztürk", "tarih": "sprint sonu",
            "kaynak": ""},
           {"madde": "Kayıtları iletmek", "sorumlu": "belirsiz", "tarih": "-", "kaynak": ""},
           {"madde": "-", "sorumlu": "Selin Kara", "tarih": "-", "kaynak": ""}]
    yol = tmp_path / "aksiyonlar.csv"
    assert not_araclari.jira_csv(aks, str(yol), "Strateji (2026-09-23)") == 3
    ham = yol.read_bytes()
    assert ham.startswith(b"\xef\xbb\xbf")                    # UTF-8 BOM (Excel Turkce karakter)
    with open(yol, encoding="utf-8-sig", newline="") as f:
        satirlar = list(csv.reader(f))
    assert satirlar[0] == ["Summary", "Assignee", "Due Date", "Description", "Issue Type"]
    assert satirlar[1] == ["MVP prototipini hazırlamak", "Selin Kara", "2026-10-01",
                           "BriefMind — Strateji (2026-09-23) · kaynak 10:02:00", "Task"]
    assert satirlar[2][1:3] == ["Emre Sancak", ""]
    assert "sorumlular: Emre Sancak, Deniz Can Öztürk" in satirlar[2][3]
    assert satirlar[3][:3] == ["Kayıtları iletmek", "", ""] and satirlar[3][4] == "Task"
