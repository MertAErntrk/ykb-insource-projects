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


# ---------------- Faz 2: --duzelt (sahte onerici ya da gercek LLM), duzeltilen / bozulan sayaclari ----------------

GT_1 = "Kubernetes kümesine üç yeni düğüm ekleyeceğiz çünkü yük arttı."
HAM_1 = "kubernetis kümesine üç yeni düğüm ekleyeceğiz çünkü yük arttı."
GT_2 = "Komite toplantısı perşembe günü saat dörtte yapılacak."
HAM_2 = "komite toplantısı perşembe günü saat dörtte yapılacak."


def _kayit_duzelt(tmp_path):
    k = str(tmp_path / "toplanti")
    klipler = []
    for no, (ts, ham) in enumerate((("10:00:10", HAM_1), ("10:00:30", HAM_2)), 1):
        sn = int(ts[-2:])
        klipler.append({"no": no, "akis": "loopback", "bas": T0 + sn, "bit": T0 + sn + 5.0, "sure": 5.0, "ts": ts,
                        "tsler": [ts], "zarf": [0.05] * 50, "rms": 0.05, "raw": ham, "stt_sure": 0.5})
    _yaz(os.path.join(k, "olcum", "klipler.jsonl"), klipler)
    _yaz(os.path.join(k, "olcum", "gercek.jsonl"), [
        {"klip_no": 1, "speaker": KIM_A, "cakisma": False, "halusinasyon": False, "text": GT_1},
        {"klip_no": 2, "speaker": KIM_B, "cakisma": False, "halusinasyon": False, "text": GT_2}])
    _yaz(os.path.join(k, "olcum", "okumalar.jsonl"), [
        {"okuma_no": 1, "bas_epoch": T0 + 13.0, "bit_epoch": T0 + 13.2, "grup": True, "gorunen": [[KIM_A, "k0 k1 k2"]]},
        {"okuma_no": 2, "bas_epoch": T0 + 33.0, "bit_epoch": T0 + 33.2, "grup": True, "gorunen": [[KIM_B, "k0 k1 k2"]]}])
    return k


def _duzelt_kos(tmp_path, monkeypatch, **ayar):
    import motor
    monkeypatch.setattr(motor, "DUZELT_MIN_TOKEN", 1)           # kucuk parca da duzeltme gecisine girer
    k = _kayit_duzelt(tmp_path)
    cikti = str(tmp_path / "c")
    m, _ = replay.kos(k, cikti=cikti, ayar=dict({"duzelt": True, "katilimcilar": [KIM_A, KIM_B]}, **ayar),
                      yazdir=lambda *a: None)
    return m, _satirlar(cikti)


def test_replay_duzelt_sahte_onerici_dogruyu_kabul_komiteyi_reddeder(tmp_path, monkeypatch):
    m, satirlar = _duzelt_kos(tmp_path, monkeypatch, terimler=["Kubernetes", "commit"])
    d = m["duzeltme"]
    assert d["duzeltilen"] == 1 and d["bozulan"] == 0 and d["net"] == 1, d
    assert d["degisen_satir"] == 1 and d["kabul"] == 1 and d["satir"] == 2
    metinler = {s["klip_no"]: s["text"] for s in satirlar}
    assert metinler[1] == GT_1 and metinler[2] == HAM_2        # komite -> commit YOK
    z = m["replay"]["duzelt"]
    assert z["llm"] == "sahte" and z["oneri"] >= 2 and z["kabul"] == 1 and sum(z["red"].values()) >= 1, z
    assert m["wer"]["hata"] < m["wer"]["whisper_ham_hata"]


def test_replay_duzelt_kapali_metin_degismez(tmp_path, monkeypatch):
    import motor
    monkeypatch.setattr(motor, "DUZELT_MIN_TOKEN", 1)
    k = _kayit_duzelt(tmp_path)
    m, _ = replay.kos(k, cikti=str(tmp_path / "c"), ayar={"katilimcilar": [KIM_A, KIM_B]}, yazdir=lambda *a: None)
    assert m["duzeltme"]["degisen_satir"] == 0 and m["duzeltme"]["duzeltilen"] == 0 and "duzelt" not in m["replay"]


def test_replay_duzelt_davet_metninden_terim(tmp_path, monkeypatch):
    """--davet: metindeki ozel ad terim havuzuna girer (toplanti klasorune ozel havuz; uygulamanin terimler/ dosyasi degil)."""
    _duzelt_kos(tmp_path, monkeypatch, davet="Mikroservisler için Jenkins pipeline planı görüşülecek.")
    with open(str(tmp_path / "c" / "toplanti" / "terimler" / "terimler.json"), encoding="utf-8") as f:
        terimler = [e["terim"] for e in json.load(f)["terimler"]]
    assert "Jenkins" in terimler


def test_replay_duzelt_agresif_sahte_onerici_zarar_veremez(tmp_path, monkeypatch):
    """Terim listesinde komite'ye benzeyen 'commit' ve ad olarak 'Taslak' var; agresif onerici her yakin kelimeyi onerir."""
    m, satirlar = _duzelt_kos(tmp_path, monkeypatch, terimler=["Kubernetes", "commit", "Komitesi", "Perşembe"])
    assert m["duzeltme"]["bozulan"] == 0, m["duzeltme"]
    assert {s["klip_no"]: s["text"] for s in satirlar}[2] == HAM_2


def test_replay_duzelt_cli(tmp_path, monkeypatch, capsys):
    import motor
    monkeypatch.setattr(motor, "DUZELT_MIN_TOKEN", 1)
    k = _kayit_duzelt(tmp_path)
    assert replay.main([k, "--duzelt", "--terimler", "Kubernetes,commit", "--katilimcilar", f"{KIM_A},{KIM_B}",
                        "--cikti", str(tmp_path / "c")]) == 0
    cikti = capsys.readouterr().out
    assert "Düzeltme:" in cikti and "+1 düzeldi" in cikti and "-0 bozuldu" in cikti and "sahte LLM" in cikti


def test_replay_duzelt_gercek_llm_secenegi_sahte_onericiyi_baglamaz(tmp_path, monkeypatch):
    """--llm: duzelt_sor_fn None (duzelt.duzelt gercek llm modulunu cagirir); burada llm.sor yamali bir sahtedir."""
    import llm
    gelen = []

    def sahte_sor(mesajlar, max_tokens, sema=None, effort="medium", temperature=0.6, dusunme=True):
        gelen.append(mesajlar[0]["content"][:20])
        return '{"oneriler": [{"satir": 1, "eski": "kubernetis", "yeni": "Kubernetes", "neden": "terim"}]}', "stop"
    monkeypatch.setattr(llm, "sor", sahte_sor)
    m, satirlar = _duzelt_kos(tmp_path, monkeypatch, terimler=["Kubernetes"], llm=True)
    assert gelen and m["replay"]["duzelt"]["llm"] == "gercek"
    assert m["duzeltme"]["bozulan"] == 0


def test_olcum_kelime_etkisi():
    import olcum
    # raw'da yanlis -> text'te dogru: duzeltilen; raw'da dogru -> text'te yanlis: bozulan
    assert olcum.kelime_etkisi("bugün kubernetes kümesi komite", "bugün kubernetis kümesi komite",
                               "bugün Kubernetes kümesi komite") == (1, 0, 4)
    assert olcum.kelime_etkisi("bugün kubernetes kümesi komite", "bugün kubernetes kümesi komite",
                               "bugün kubernetes kümesi commit") == (0, 1, 4)
    assert olcum.kelime_etkisi("a b c", "a b c", "a b c") == (0, 0, 3)
    assert olcum.kelime_etkisi("", "x", "y") == (0, 0, 0)
    # ekli biçim: Jenkins'e == jenkinse (tr_normalize apostrofu atar)
    assert olcum.kelime_etkisi("Jenkins'e bak", "cenkinse bak", "Jenkins'e bak")[:2] == (1, 0)


def test_olcum_cikti_metin_ve_ad_tasimaz_duzeltme_dahil(tmp_path, monkeypatch):
    import json as j
    m, _ = _duzelt_kos(tmp_path, monkeypatch, terimler=["Kubernetes", "commit"])
    dump = j.dumps(m, ensure_ascii=False).lower()
    assert "kubernetes" not in dump and "komite" not in dump and KIM_A.lower() not in dump
