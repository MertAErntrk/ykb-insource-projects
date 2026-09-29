"""Cok konusmacili toplanti: satir sirasi, konusmaci atamasi, yanki; aksiyon tarihleri; LLM gunlugu."""
import datetime as dt
import json
import threading
import time
import types

import llm
import motor as motor_mod
import ses
import tarih


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


# ---------------------------------------------------------------- konusmaci

def _motor(tmp_path, ben="Mert"):
    m = motor_mod.Motor(str(tmp_path / "t"), "Test", dt.date(2026, 9, 29), duzelt=False, kaynak="ses", ben=ben)
    return m


def test_metin_eslesmezse_ve_iki_kisi_konusuyorsa_tahmin_etmez(tmp_path):
    m = _motor(tmp_path)
    m.altyazi_satiri({"ts": "10:00:01", "speaker": "Ahmet Yılmaz", "text": "Bütçe tablosunu güncelledim"})
    m.altyazi_satiri({"ts": "10:00:04", "speaker": "Ayşe Demir", "text": "Test ortamı hazır olacak"})
    s = m.ses_satiri("10:00:03", None, "Kampanya görsellerini kim onaylıyor acaba", "loopback")
    assert s["speaker"] == "?"                 # eskiden zamana en yakin kisiye yaziliyordu
    m.kapat()


def test_metin_eslesirse_dogru_kisiye_yazar(tmp_path):
    m = _motor(tmp_path)
    m.altyazi_satiri({"ts": "10:00:01", "speaker": "Ahmet Yılmaz", "text": "Bütçe tablosunu güncelledim"})
    m.altyazi_satiri({"ts": "10:00:04", "speaker": "Ayşe Demir", "text": "Test ortamı perşembe hazır olacak"})
    s = m.ses_satiri("10:00:02", None, "Test ortamı perşembe hazır olacak.", "loopback")
    assert s["speaker"] == "Ayşe Demir"        # zamanca Ahmet daha yakin ama metin Ayse'nin
    m.kapat()


def test_tek_kisi_konusuyorsa_zamana_gore_atar(tmp_path):
    m = _motor(tmp_path)
    m.altyazi_satiri({"ts": "10:00:01", "speaker": "Ahmet Yılmaz", "text": "Bütçe tablosunu güncelledim"})
    s = m.ses_satiri("10:00:03", None, "Kampanya görsellerini de ekledim", "loopback")
    assert s["speaker"] == "Ahmet Yılmaz"
    m.kapat()


def test_hoparlor_yankisi_mikrofon_satirini_konusana_yazar(tmp_path):
    m = _motor(tmp_path)
    m.altyazi_satiri({"ts": "10:00:01", "speaker": "Ayşe Demir", "text": "Test ortamı perşembe hazır olacak"})
    s = m.ses_satiri("10:00:02", "ben", "Test ortamı perşembe hazır olacak.", "mikrofon")
    assert s["speaker"] == "Ayşe Demir"
    # kullanicinin kendi sozu (altyazida kendi adiyla) 'ben' olarak kalir
    m.altyazi_satiri({"ts": "10:00:10", "speaker": "Mert Ali Erentürk", "text": "Ben Jira kaydını açarım"})
    s2 = m.ses_satiri("10:00:11", "ben", "Ben Jira kaydını açarım.", "mikrofon")
    assert s2["speaker"] == "Mert"
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
