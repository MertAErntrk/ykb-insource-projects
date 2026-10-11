"""tools/replay.py Faz1Hat: devirde yeniden kesme (wav enerjisi + STT), gosterim kurali, eski hattin secilebilirligi.
Sentetik kayit (ses cihazi, ag, LLM yok); adlar kurgusal."""
import json
import os
import sys

import numpy as np

KOK = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(KOK, "tools"))
import replay  # noqa: E402

T0 = 1_760_000_000.0
KIM_A, KIM_B = "Melek Taslak", "Kaan Uydurma"
ORNEK = 16000


def _yaz(yol, kayitlar):
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    with open(yol, "w", encoding="utf-8") as f:
        for k in kayitlar:
            f.write(json.dumps(k, ensure_ascii=False) + "\n")


def _wav(yol, ses_):
    import wave
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    with wave.open(yol, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(ORNEK)
        w.writeframes((np.clip(ses_, -1, 1) * 32767).astype("<i2").tobytes())


def _sentetik_klip(on_yuk=0.3):
    """On-yuk 0.3 sn + 9 sn sesli (4.4 sn A, 0.2 sn bosluk, 4.4 sn B) + 0.55 sn sonluk. Dondurur (wav, zarf, gap_sn)."""
    r = np.random.RandomState(3)
    def parca(sn, genlik):
        return (r.randn(int(sn * ORNEK)) * genlik).astype("float32")
    sesli = np.concatenate([parca(4.4, 0.08), parca(0.2, 0.002), parca(4.4, 0.08)])
    ses_ = np.concatenate([parca(on_yuk, 0.002), sesli, parca(0.55, 0.002)])
    blok = 1600
    nb = len(sesli) // blok
    zarf = [round(float(np.sqrt(np.mean(np.square(sesli[i * blok:(i + 1) * blok])))) / 3.0, 5) for i in range(nb)]
    return ses_, zarf


def test_kesim_noktasi_on_yuku_hizalar_ve_boslukta_keser():
    ses_, zarf = _sentetik_klip()
    # an: A->B gecisi 4.4-4.6 sn civarinda; tahmin 0.5 sn kaymis
    sonuc = replay.kesim_noktasi(ses_, zarf, 4.0, 1.0, 1.0)
    assert sonuc is not None
    ofs, kk = sonuc
    assert ofs == 3                                    # 0.3 sn on-yuk = 3 blok
    assert 43 <= kk <= 45                              # bosluk 4.4-4.6 sn (blok 44, 45)
    # parcalar < 1 sn ise None
    assert replay.kesim_noktasi(ses_, zarf, 0.0, 1.0, 1.0) is None


def _kayit(tmp_path, stt_icin_wav=True):
    k = str(tmp_path / "toplanti")
    ses_, zarf = _sentetik_klip()
    ts = "10:00:10"
    _yaz(os.path.join(k, "olcum", "klipler.jsonl"), [
        {"no": 1, "akis": "loopback", "bas": T0 + 10.0, "bit": T0 + 19.0, "ornek_bas": 0, "ornek_bit": len(ses_),
         "sure": round(len(ses_) / ORNEK, 2), "ts": ts, "tsler": [ts], "zarf": zarf, "rms": 0.05,
         "raw": "Bütçe tablosunu güncelledim. Test ortamı perşembe hazır.", "stt_sure": 0.5}])
    if stt_icin_wav:
        _wav(os.path.join(k, "olcum", "klipler", "0001.wav"), ses_)
    okumalar, no, t = [], 0, 0.0
    # Teams paneli: A 10-14.4 sn, B 14.6-19 sn konusur; altyazi 2 sn gecikmeli, 3 kelime/sn
    while t < 30:
        no += 1
        gor = []
        for kim, b, e in ((KIM_A, 10.0, 14.4), (KIM_B, 14.6, 19.0)):
            if t >= b + 2.0:
                n = max(1, min(int((e - b) * 3), 1 + int((t - b - 2.0) * 3)))
                gor.append([kim, " ".join(f"k{i}" for i in range(n))])
        okumalar.append({"okuma_no": no, "bas_epoch": T0 + t - 0.1, "bit_epoch": T0 + t, "grup": True, "gorunen": gor})
        t = round(t + 0.7, 3)
    _yaz(os.path.join(k, "olcum", "okumalar.jsonl"), okumalar)
    return k, ses_


class SahteStt:
    """wav parcasi -> tek segment; sirayla ilk / ikinci cumle"""

    def __init__(self):
        self.cagri = []

    def coz(self, ses_):
        self.cagri.append(len(ses_) / ORNEK)
        return [{"start": 0.0, "end": len(ses_) / ORNEK, "text": ["Bütçe tablosunu güncelledim.", "Test ortamı perşembe hazır."][len(self.cagri) - 1]}]


def _satirlar(cikti):
    with open(os.path.join(cikti, "satirlar.jsonl"), encoding="utf-8") as f:
        return [json.loads(s) for s in f if s.strip()]


def test_faz1_hat_devirde_yeniden_keser_parcalar_ust_klibe_yazilir(tmp_path):
    k, _ = _kayit(tmp_path)
    stt = SahteStt()
    m, _ = replay.kos(k, stt=None, cikti=str(tmp_path / "c"), ayar={"stt": stt, "katilimcilar": [KIM_A, KIM_B]},
                      yazdir=lambda *a: None)
    # kos(stt=None) segmentleri kayittan alir; yeniden kesme icin ayar["stt"] verildi
    satirlar = _satirlar(str(tmp_path / "c"))
    assert len(stt.cagri) == 2 and abs(sum(stt.cagri) - 9.85) < 0.1
    assert [s["speaker_ham"] for s in satirlar] == [KIM_A, KIM_B]
    assert all(s["klip_no"] == 1 and s["cocuk_klip_no"] > replay.Faz1Hat.COCUK_NO for s in satirlar)
    assert [s["text"] for s in satirlar] == ["Bütçe tablosunu güncelledim.", "Test ortamı perşembe hazır."]
    assert m["replay"]["hat"] == "Faz1Hat"


def test_faz1_hat_stt_yoksa_a_ok_b(tmp_path):
    k, _ = _kayit(tmp_path)
    m, _ = replay.kos(k, cikti=str(tmp_path / "c"), ayar={"katilimcilar": [KIM_A, KIM_B]}, yazdir=lambda *a: None)
    satirlar = _satirlar(str(tmp_path / "c"))
    assert len(satirlar) == 1 and satirlar[0]["speaker_ham"] == f"{KIM_A} → {KIM_B}"
    assert satirlar[0]["devir"] and satirlar[0]["speaker"] == satirlar[0]["speaker_ham"]


def test_faz1_hat_wav_yoksa_a_ok_b(tmp_path):
    k, _ = _kayit(tmp_path, stt_icin_wav=False)
    replay.kos(k, cikti=str(tmp_path / "c"), ayar={"stt": SahteStt(), "katilimcilar": [KIM_A, KIM_B]},
               yazdir=lambda *a: None)
    satirlar = _satirlar(str(tmp_path / "c"))
    assert len(satirlar) == 1 and " → " in satirlar[0]["speaker_ham"]


def test_olcum_adi_gosterim_kurali():
    assert replay.olcum_adi({"speaker": "Ayşe Demir", "spk_guven": 0.9}) == "Ayşe Demir"
    assert replay.olcum_adi({"speaker": "Ayşe Demir", "spk_guven": 0.6}) == "Ayşe Demir?"
    assert replay.olcum_adi({"speaker": "Ayşe Demir?", "spk_guven": 0.6}) == "Ayşe Demir?"
    assert replay.olcum_adi({"speaker": "?", "spk_guven": 0.0}) == "?"
    assert replay.olcum_adi({"speaker": "A → B", "spk_guven": 0.5}) == "A → B"
    assert replay.olcum_adi({"speaker": "A / B", "spk_guven": 0.5, "cakisma": True}) == "A / B"
    assert replay.olcum_adi({"speaker": "Ayşe Demir"}) == "Ayşe Demir"          # spk_guven alani yok: degismez


def test_hat_secimi():
    assert replay.hat_sinifi_yukle("") is replay.Faz1Hat
    assert replay.hat_sinifi_yukle("faz1") is replay.Faz1Hat
    assert replay.hat_sinifi_yukle("eski") is replay.MevcutHat
    assert replay.hat_sinifi_yukle("replay:MevcutHat") is replay.MevcutHat


def test_okuma_olayi_eski_hat_imzasi_calisir():
    """Hat sozlesmesi: okuma_olayi varsayilani eski `okuma(an, gorunen)` imzasini cagirir."""
    goruldu = []

    class Iz(replay.Hat):
        def okuma(self, an, gorunen):
            goruldu.append((an, gorunen))

    Iz({}).okuma_olayi({"bas_epoch": 1.0, "bit_epoch": 1.5, "gorunen": [["A", "x"]]})
    assert goruldu == [(1.5, [["A", "x"]])]


def test_faz1_hat_cocuk_tekrar_muafiyeti_kapali_da_parcalar_gelir(tmp_path):
    """Uretim davranisi: ses.py yeniden kesme parcalarini (ust_klip) tekrar suzgecinden muaf tutar; replay'in
    muafiyet bayragi kapali olsa da iki parca gelir ve ust klibin yerine yazilir."""
    k, _ = _kayit(tmp_path)
    replay.kos(k, cikti=str(tmp_path / "c"), ayar={"stt": SahteStt(), "katilimcilar": [KIM_A, KIM_B],
                                                    "cocuk_tekrar_muaf": False}, yazdir=lambda *a: None)
    satirlar = _satirlar(str(tmp_path / "c"))
    assert [s["speaker_ham"] for s in satirlar] == [KIM_A, KIM_B]
    assert [s["text"] for s in satirlar] == ["Bütçe tablosunu güncelledim.", "Test ortamı perşembe hazır."]
