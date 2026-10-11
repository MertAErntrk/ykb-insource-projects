"""Cok konusmacili toplanti: satir sirasi, konusmaci atamasi (Faz 1: yalniz zaman), yanki; aksiyon tarihleri;
LLM gunlugu."""
import datetime as dt
import json
import threading
import time
import types

import pytest

import llm
import motor as motor_mod
import ses
import tarih
from test_motor_faz1 import T0, akit, saat_kur


@pytest.fixture
def saat(monkeypatch):
    return saat_kur(monkeypatch)


# ---------------------------------------------------------------- sira (A3)

def test_stt_ciktisi_kuyruk_sirasiyla_yayilir():
    yayilan = []
    s = ses.SesServisi("https://stt.ornek", satir_fn=lambda ts, kim, metin, akis: yayilan.append(metin))
    t = dt.datetime(2026, 9, 29, 10, 0, 0)
    # 2 numarali parca (kisa) once, 0 ve 1 sonra cozuldu
    s._sirali_yay(2, [(t + dt.timedelta(seconds=20), "loopback", "Üçüncü cümle burada.")])
    s._sirali_yay(0, [(t, "loopback", "Birinci cümle burada.")])
    assert yayilan == ["Birinci cümle burada."]
    s._sirali_yay(1, [])                       # STT hatasi: bos ama numara kapanir
    assert yayilan == ["Birinci cümle burada.", "Üçüncü cümle burada."]


def test_iki_isci_ters_bitirse_de_sira_korunur(monkeypatch):
    yayilan = []
    s = ses.SesServisi("https://stt.ornek", satir_fn=lambda ts, kim, metin, akis: yayilan.append(metin))
    gecikme = {0: 0.3, 1: 0.0}                 # ilk parca daha yavas cozuluyor

    def coz(ses_):
        no = int(ses_[0])
        time.sleep(gecikme[no])
        return [{"text": f"Parça numarası {no} için uzun bir cümle."}]

    monkeypatch.setattr(s.stt, "coz", coz)
    t = dt.datetime(2026, 9, 29, 10, 0, 0)
    for no in (0, 1):
        s.kuyruk.put({"kaynak": "loopback", "basla": t + dt.timedelta(seconds=10 * no), "bitis": t,
                      "ses": ses.np.full(16000 * 2, no, dtype="float32"), "no": no})
    isciler = [threading.Thread(target=s._calis, daemon=True) for _ in range(2)]
    s._dur.set()
    for i in isciler:
        i.start()
    for i in isciler:
        i.join(timeout=5)
    assert yayilan == ["Parça numarası 0 için uzun bir cümle.", "Parça numarası 1 için uzun bir cümle."]


# ---------------------------------------------------------------- konusmaci (Faz 1: yalniz zaman)

A_, B_ = "Ahmet Yılmaz", "Ayşe Demir"


def _motor(tmp_path, ben="Deniz"):
    m = motor_mod.Motor(str(tmp_path / "t"), "Test", dt.date.fromtimestamp(T0), duzelt=False, ben=ben,
                        katilimcilar=[A_, B_, ben])
    m._isle = lambda veri, duzelt: None
    return m


def test_iki_kisi_konusurken_metin_degil_zaman_belirler(tmp_path, saat):
    """Eskiden: metin eslesmezse '?', eslesirse metnin sahibine (zamanca daha yakin olsa bile). Simdi altyazi
    metnine hic bakilmaz; klip kimin konusma araligindaysa ona yazilir (altyazi metni ayni cumleyi tasisa da)."""
    m = _motor(tmp_path)
    sozler = [(A_, 1, 4), (B_, 6, 9), (A_, 11, 14), (B_, 16, 19)]
    klipler = [(i + 1, "loopback", s_, e, "Test ortamı perşembe hazır olacak.") for i, (_, s_, e) in
               enumerate(sozler)]
    akit(m, saat, sozler, klipler, 25, metin="Test ortamı perşembe hazır olacak")
    assert [s["speaker"] for s in m.mevcut] == [A_, B_, A_, B_]
    m.kapat()


def test_tek_kisi_konusuyorsa_ona_atar_kapsama_disi_bilinmeyen(tmp_path, saat):
    m = _motor(tmp_path)
    akit(m, saat, [(A_, 1, 4)], [(1, "loopback", 1, 4, "Bütçe tablosunu güncelledim."),
                                (2, "loopback", 10, 12, "Kampanya görsellerini de ekledim.")], 15)
    assert m.mevcut[0]["speaker"] == A_
    assert m.mevcut[1]["speaker"] == "?"            # o anda altyazida kimse yok: eskiden son konusana yaziliyordu
    m.kapat()


def test_hoparlor_yankisi_atilir_kullanicinin_sozu_ben(tmp_path, saat):
    """Kulakliksiz: Ayse'nin sesi mikrofona da girdi -> yanki klibi atilir (eskiden Ayse'ye ikinci satir
    yaziliyordu). Kullanicinin kendi sozu (altyazida kendi adiyla) mikrofon klibinde 'ben'."""
    m = _motor(tmp_path)
    m.ses_olayi({"tur": "kulaklik", "durum": False, "guven": 0.9})
    sozler = [(B_, 1, 5), ("Deniz Can Öztürk", 10, 13)]
    klipler = [(1, "loopback", 1, 5, "Test ortamı perşembe hazır olacak."),
               (2, "mikrofon", 1.1, 4.9, "Test ortamı perşembe hazır olacak."),
               (3, "mikrofon", 10, 13, "Ben Jira kaydını açarım.")]
    akit(m, saat, sozler, klipler, 16)
    assert [(s["akis"], s["speaker"]) for s in m.mevcut] == [("loopback", B_), ("mikrofon", "Deniz")]
    m.kapat()


# ---------------------------------------------------------------- tarih (A10)

def test_tarih_coz():
    g = dt.date(2026, 9, 29)                   # Sali
    assert tarih.tarih_coz("perşembeye", g) == "2026-10-01 (Perşembe)"
    assert tarih.tarih_coz("salıya", g) == "2026-10-06 (Salı)"          # ayni gun adi: gelecek hafta
    assert tarih.tarih_coz("haftaya pazartesi", g) == "2026-10-05 (Pazartesi)"
    assert tarih.tarih_coz("cumartesi", g) == "2026-10-03 (Cumartesi)"
    assert tarih.tarih_coz("yarın", g) == "2026-09-30 (Çarşamba)"
    assert tarih.tarih_coz("ay sonu", g) == "2026-09-30 (Çarşamba)"
    # LLM'in tutarsiz tarihi: gun adi esas
    assert tarih.tarih_coz("2026-10-02 (Perşembe)", g) == "2026-10-01 (Perşembe)"
    assert tarih.tarih_coz("2026-10-01 (Perşembe)", g) == "2026-10-01 (Perşembe)"
    assert tarih.tarih_coz("2026-10-09", g) == "2026-10-09 (Cuma)"
    assert tarih.tarih_coz("-", g) == "-"
    assert tarih.tarih_coz("sprint sonuna kadar", g) == "sprint sonuna kadar"


# ---------------------------------------------------------------- LLM gunlugu (A2)

def test_llm_gunlugu_istatistik_yazar_icerik_yazmaz(tmp_path, monkeypatch):
    m = _motor(tmp_path)
    cevap = types.SimpleNamespace(
        choices=[types.SimpleNamespace(finish_reason="length", message=types.SimpleNamespace(
            content="GİZLİ TOPLANTI İÇERİĞİ", reasoning_content="düşünce"))],
        usage=types.SimpleNamespace(completion_tokens=3000))
    monkeypatch.setattr(llm, "token_say", llm.token_tahmin)
    monkeypatch.setattr(llm.client.chat.completions, "create", lambda **kw: cevap)
    llm.sor([{"role": "system", "content": llm.MAP_SISTEM}, {"role": "user", "content": "x"}], 3000,
            effort="low")
    satirlar = (tmp_path / "t" / "llm_log.jsonl").read_text(encoding="utf-8").splitlines()
    k = json.loads(satirlar[-1])
    assert k["gorev"] == "bolum" and k["finish"] == "length" and k["cikti_token"] == 3000
    assert k["dusunme"] == "low" and k["dusunce_karakter"] == len("düşünce")
    assert "GİZLİ" not in satirlar[-1]
    m.kapat()
