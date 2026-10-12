"""Faz 3 uctan uca kabul kapilari (MIMARI_v3 §4 "Uzun pencere", §9 Faz 3): config uzun_pencere arkasinda 20-28 sn'lik
pencereler (sessizlikte bolunmus, 1.5 sn ortusen) yeniden cozulur, metin kisa kliplere difflib ile dagitilir, dikis LCS.
Sentetik, ag yok, repo'ya dosya yazmaz. Gercek: Motor, Konusmaci, SesServisi + ses.UzunPencere (planlama, prompt korumasi,
halusinasyon puani), uzun.pencere_hizala. Sahte: STT (kisa klip sinirinda kelimeyi yarim duyar, uzun pencere dogru duyar),
Teams paneli, LLM, saat.

Kapilar (her tohum + havuz): son kelime hatasi kisa-yalnizdan dusuk (havuzda goreli <= -%25); benzersiz kelimelerden
uzun pencerenin kaybettigi / cift yazdigi 0 (dikis cevresi dahil); konusmaci degisikligi 0 (klip_no ve atama birebir); parca
kapanis gecikmesi <= 20 sn (+ duzenek tik payi); uyusmayan kelime 'belirsiz'e girer, ⟨⟩ kullanilmaz; STT yuku olculur
(uzun istek <= kisa istek: ~2x); Faz 1 / Faz 2 kapilari bayrak acikken degismez; bayrak kapali = eski yol (uzun kodu hic
cagrilmaz). tools/replay.py --uzun kayitli toplantida kisa / uzun WER raporlar. Kapi gevsetilmez."""
import json
import os
import socket
import sys
import wave

import numpy as np
import pytest

import duzenek as D
import motor as M
import ses

SEEDLER = D.SEDLER_FAZ3
FAZ3 = D.SENARYOLAR_FAZ3
F3_AD = ("uzun_sinir", "uzun_surekli")
F1_AD = ("gecikme2", "klip_ici_devir", "hizli_devir", "yanki", "stt_kesintisi")
F2_AD = ("terim_duzeltme", "halusinasyon_cesit", "prompt_sizintisi")
F1_KARSILASTIR = ("klip", "dogru", "yanlis", "kirli", "soru", "adayli", "devir", "ayni", "cift_ut", "yanki_kalan", "satir",
                  "bekleyen_kalan", "capa", "L", "kes_tamam", "eksik", "bozulan", "hayalet_kesin", "yanlis_atilan",
                  "kapsamasiz_uzak_soru_degil", "yanlis_ayni_anda_sirali", "oda_kesin", "stt_eksik_kapsanmayan")
TIK_PAYI = 1.0                                                   # duzenek kontrol() en sik 1 sn'de bir (sanal tik)


@pytest.fixture(autouse=True)
def _agsiz(monkeypatch):
    def yasak(*_a, **_k):
        raise RuntimeError("duzenek ag erisimi yok")
    monkeypatch.setattr(socket.socket, "connect", yasak)
    monkeypatch.setattr(socket, "create_connection", yasak)


_onbellek = {}


def _isler():
    isler = []
    for ad in F3_AD:
        for sd in SEEDLER:
            isler.append(((ad, sd), dict(seed=sd, **FAZ3[ad])))
            isler.append(((ad + "_kisa", sd), dict(seed=sd, **FAZ3[ad + "_kisa"])))
    for ad in F1_AD:
        isler.append((("f1", ad, False), dict(seed=7, f3_olcum=True, **D.SENARYOLAR[ad])))
        isler.append((("f1", ad, True), dict(seed=7, uzun=True, **D.SENARYOLAR[ad])))
    for ad in F2_AD:
        isler.append((("f2", ad, False), dict(seed=7, f3_olcum=True, **D.SENARYOLAR_FAZ2[ad])))
        isler.append((("f2", ad, True), dict(seed=7, uzun=True, **D.SENARYOLAR_FAZ2[ad])))
    return isler


@pytest.fixture(scope="module", autouse=True)
def _onisitma(request):
    kosan = [i for i in request.session.items if i.module is request.module]
    if len(kosan) > 6 and D.paralel_sayisi() > 1:
        for anahtar, (sonuc, hata) in D.toplu(_isler()).items():
            if hata is None:
                _onbellek[anahtar] = sonuc


def olc(*anahtar):
    if anahtar not in _onbellek:
        for a, kw in [x for x in _isler() if x[0] == anahtar]:
            _onbellek[anahtar] = D.calistir(**kw)
    return _onbellek[anahtar]


def _cift(ad, sd):
    return olc(ad, sd), olc(ad + "_kisa", sd)


def _konusmaci_farki(a, b):
    ka, kb = a["f3_konusmaci"], b["f3_konusmaci"]
    return sorted(n for n in set(ka) | set(kb) if ka.get(n) != kb.get(n))


# ---------------------------------------------------------------------------------------------- Faz 3 kapilari

@pytest.mark.parametrize("ad", F3_AD)
@pytest.mark.parametrize("sd", SEEDLER)
def test_uzun_pencere_kelime_hatasini_dusurur(ad, sd):
    a, b = _cift(ad, sd)
    assert b["f3_kelime_hata"] > 0, "fixture isirmiyor: kisa klipte sinir hatasi yok"
    assert a["f3_kelime_hata"] < b["f3_kelime_hata"]
    assert a["f3_klip_hata"] < b["f3_klip_hata"]                 # olcum.py gibi klip basina sayim da duser
    assert a["f3_ref_kelime"] == b["f3_ref_kelime"]
    assert a["f3_guncellenen"] > 0


@pytest.mark.parametrize("ad", F3_AD)
def test_havuzda_goreli_kazanc_hedefi(ad):
    a = sum(olc(ad, sd)["f3_kelime_hata"] for sd in SEEDLER)
    b = sum(olc(ad + "_kisa", sd)["f3_kelime_hata"] for sd in SEEDLER)
    assert (a - b) / b <= -0.25                                  # plan: replay WER goreli -%25 hedefi (sentetik)


@pytest.mark.parametrize("ad", F3_AD)
@pytest.mark.parametrize("sd", SEEDLER)
def test_dikiste_cift_ya_da_kayip_kelime_yok(ad, sd):
    """Benzersiz her kelime: uzun pencere bir kelimeyi kaybetmez (kisada vardi, uzunda yok) ya da cift yazmaz. Dikis
    cevresindeki (pencere ortusmesi) kelimeler ayrica: tam bir kez."""
    a, b = _cift(ad, sd)
    yeni_kayip = sorted(set(a["f3_sifir"]) - set(b["f3_sifir"]))
    yeni_cift = sorted(set(a["f3_coklu"]) - set(b["f3_coklu"]))
    assert yeni_kayip == [] and yeni_cift == []
    assert a["f3_coklu"] == []
    dikis = set(a["f3_dikis"])
    assert len(dikis) >= 20, "fixture isirmiyor: dikis cevresinde kelime yok"
    assert not dikis & set(a["f3_coklu"])
    assert not dikis & (set(a["f3_sifir"]) - set(b["f3_sifir"]))


@pytest.mark.parametrize("ad", F3_AD)
@pytest.mark.parametrize("sd", SEEDLER)
def test_konusmaci_atamasi_degismez(ad, sd):
    a, b = _cift(ad, sd)
    assert set(a["f3_konusmaci"]) == set(b["f3_konusmaci"])      # klip sinirlari / klip_no ayni
    assert _konusmaci_farki(a, b) == []
    for alan in ("yanlis", "cift_ut", "eksik", "yanlis_ayni_anda_sirali"):
        assert a.get(alan, 0) == b.get(alan, 0) == 0, alan


@pytest.mark.parametrize("ad", F3_AD)
@pytest.mark.parametrize("sd", SEEDLER)
def test_parca_kapanisi_en_fazla_20_sn_bekler(ad, sd):
    a, _ = _cift(ad, sd)
    assert a["f3_parca"] >= 3
    assert a["f3_kapanis_gecikme_maks"] <= M.UZUN_BEKLE_SN + TIK_PAYI


@pytest.mark.parametrize("ad", F3_AD)
@pytest.mark.parametrize("sd", SEEDLER)
def test_belirsiz_isaretlenir_sarmal_kullanilmaz_yuk_olculur(ad, sd):
    a, b = _cift(ad, sd)
    assert a["f3_belirsiz"] > 0                                  # kisa / uzun farkli kelime: belirsiz listesinde
    assert a["f3_sarmal"] == 0                                   # ⟨⟩ yalniz ust uste konusma isaretidir
    assert a["f3_raw_kisa"] == a["f3_guncellenen"] > 0           # ozgun kisa metin satirda saklanir
    # GPU yuku: uzun pencere istegi kisa klip isteginden az (toplam ~2x'in altinda), saatlik sayi uretilir
    assert 0 < a["f3_uzun_istek"] <= a["f3_kisa_istek"]
    assert a["f3_istatistik"]["istek"] == a["f3_uzun_istek"]
    assert a["f3_istatistik"]["saatlik_istek"] is not None
    assert b["f3_uzun_istek"] == 0 and b["f3_raw_kisa"] == 0 and b["f3_pencere"] == 0


# ---------------------------------------------------------------------------------------------- eski kapilar

@pytest.mark.parametrize("ad", F1_AD)
def test_faz1_kapilari_bayrak_acikken_degismez(ad):
    a, b = olc("f1", ad, False), olc("f1", ad, True)
    assert {k: a.get(k) for k in F1_KARSILASTIR} == {k: b.get(k) for k in F1_KARSILASTIR}
    assert _konusmaci_farki(a, b) == []
    assert b["f3_pencere"] > 0                                   # uzun pencere gercekten calisti
    assert b["f3_kapanis_gecikme_maks"] <= M.UZUN_BEKLE_SN + TIK_PAYI
    assert len(set(b["f3_sifir"]) - set(a["f3_sifir"])) == 0 and len(set(b["f3_coklu"]) - set(a["f3_coklu"])) == 0
    assert b["f3_kelime_hata"] <= a["f3_kelime_hata"]


@pytest.mark.parametrize("ad", F2_AD)
def test_faz2_kapilari_bayrak_acikken_degismez(ad):
    a, b = olc("f2", ad, False), olc("f2", ad, True)
    alanlar = F1_KARSILASTIR + ("bozulan_kelime", "duzeltilen", "kelime_hata", "ek_kaybi", "komite_commit", "komite_say",
                                "hal_kesin", "hal_belirsiz_kesin", "hal_dagilim", "hal_atilan", "hal_toplam", "sizinti",
                                "ipucu_sizinti_satir", "ipucu_sizinti_cagri", "ipucu_dusuk_rms", "gosterim_hatali",
                                "jsonl_tutarsiz", "altyazi_metni")
    assert any(a.get(k) is not None for k in ("bozulan_kelime", "hal_kesin", "ipucu_sizinti_satir"))
    assert {k: a.get(k) for k in alanlar} == {k: b.get(k) for k in alanlar}
    assert _konusmaci_farki(a, b) == []
    assert b.get("bozulan", 0) == 0


def test_bayrak_kapali_uzun_kodu_hic_cagrilmaz(monkeypatch):
    """uzun_pencere kapali: UzunPencere kurulmaz, Motor uzun sonucu islemez; sonuc bayraksiz kosumla birebir."""
    once = D.calistir(seed=11, **FAZ3["uzun_sinir_kisa"])

    def yasak(*a, **k):
        raise AssertionError("bayrak kapaliyken uzun pencere kodu cagrildi")
    monkeypatch.setattr(ses.UzunPencere, "__init__", yasak)
    monkeypatch.setattr(M.Motor, "_uzun_uygula", yasak)
    sonra = D.calistir(seed=11, **FAZ3["uzun_sinir_kisa"])
    oynak = {"ayrinti", "cift_ut_liste", "eksik_liste", "ayar"}
    assert {k: v for k, v in once.items() if k not in oynak} == {k: v for k, v in sonra.items() if k not in oynak}
    assert sonra["f3_pencere"] == 0 and sonra["f3_guncellenen"] == 0


# ---------------------------------------------------------------------------------------------- replay --uzun

sys.path.insert(0, os.path.join(D.REPO, "tools"))
import replay  # noqa: E402

T0 = D.T0
ORNEK = 16000
# 9 bitisik loopback klibi (0.2 sn ara, ~46 sn kesintisiz) + ayri bir klip. Kisa STT sinirda kelimeyi yarim duyar ya da
# dusurur; uzun pencere dogru duyar.
DOGRU = ["alfa beta gama delta epsilon", "zeta eta teta yota kapa", "lamda mü nü ksi omikron", "pi ro sigma tau ipsilon",
         "fi hi psi omega sonra", "bir iki üç dört beş", "altı yedi sekiz dokuz on", "kalem masa kitap defter silgi",
         "elma armut kiraz erik dut", "yeni konu başlıyor şimdi"]
KISA = ["alfa beta gama delta eps", "eta teta yota kapa", "lamda mü nü ksi omik", "ro sigma tau ipsilon",
        "fi hi psi omega sonra", "bir iki üç dört be", "yedi sekiz dokuz on", "kalem masa kitap defter silgi",
        "elma armut kiraz erik dut", "yeni konu başlıyor şimdi"]


def _klip_araligi(i):
    if i < 9:
        bas = 10.0 + 5.0 * i
        return bas, bas + 4.8
    return 70.0, 74.8


def _deger(i):
    return 0.1 + 0.03 * (i + 1)


class UzunSahteStt:
    """Ses -> metin: her klibin wav'i sabit bir genlikte (klip kimligi); 10 ms bloklarla hangi kliplerin ne kadari var bulunur.
    Tek klibin tamami: kisa metin (sinir hatali). Pencere: her klibin pencereye dusen payi kadar dogru kelime (pencere basi
    klibin ortasindaysa son kelimeleri, sonu ortasindaysa ilk kelimeleri)."""

    def __init__(self):
        self.cagri = []

    def coz(self, ses_, ipucu=""):
        x = np.asarray(ses_, dtype="float64")
        n = len(x) // 160
        sira, say = [], {}
        for b in range(n):
            v = float(np.mean(x[b * 160:(b + 1) * 160]))
            if v < 0.05:
                continue
            i = int(round((v - 0.1) / 0.03)) - 1
            if 0 <= i < len(DOGRU):
                if i not in say:
                    sira.append(i)
                say[i] = say.get(i, 0) + 1
        self.cagri.append({"sn": round(len(x) / ORNEK, 2), "ipucu": ipucu, "klip": list(sira)})
        tam = {i: int(round((_klip_araligi(i)[1] - _klip_araligi(i)[0]) * 100)) for i in sira}
        if len(sira) == 1 and say[sira[0]] >= 0.95 * tam[sira[0]]:
            return [{"start": 0.0, "end": len(x) / ORNEK, "text": KISA[sira[0]]}]
        kel = []
        for pos, i in enumerate(sira):
            w = DOGRU[i].split()
            pay = say[i] / tam[i]
            if pay >= 0.95:
                kel += w
            elif pos == 0:
                kel += w[-max(1, int(round(pay * len(w)))):]
            else:
                kel += w[:max(1, int(round(pay * len(w))))]
        return [{"start": 0.0, "end": len(x) / ORNEK, "text": " ".join(kel)}]


def _kayit(tmp_path):
    k = str(tmp_path / "toplanti")
    os.makedirs(os.path.join(k, "olcum", "klipler"), exist_ok=True)
    klipler, gercek = [], []
    for i in range(len(DOGRU)):
        bas, bit = _klip_araligi(i)
        ses_ = np.concatenate([np.zeros(int(0.3 * ORNEK)), np.full(int(round((bit - bas) * ORNEK)), _deger(i)),
                               np.zeros(int(0.2 * ORNEK))]).astype("float32")
        with wave.open(os.path.join(k, "olcum", "klipler", f"{i + 1:04d}.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(ORNEK)
            w.writeframes((ses_ * 32767).astype("<i2").tobytes())
        ob = int(round((bas - 0.3) * ORNEK))
        klipler.append({"no": i + 1, "klip_no": i + 1, "akis": "loopback", "bas": T0 + bas, "bit": T0 + bit,
                        "ornek_bas": ob, "ornek_bit": ob + len(ses_), "sure": round(len(ses_) / ORNEK, 2),
                        "ts": "10:00:00", "zarf": [], "rms": _deger(i), "raw": KISA[i], "stt_sure": 0.5})
        gercek.append({"klip_no": i + 1, "text": DOGRU[i], "speaker": "?"})
    for ad, kayitlar in (("klipler.jsonl", klipler), ("gercek.jsonl", gercek)):
        with open(os.path.join(k, "olcum", ad), "w", encoding="utf-8") as f:
            for x in kayitlar:
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
    return k


def test_replay_uzun_kisa_ve_uzun_wer_raporlar(tmp_path):
    k = _kayit(tmp_path)
    stt = UzunSahteStt()
    cikti = []
    m, _ = replay.kos(k, stt=stt, cikti=str(tmp_path / "c"), ayar={"uzun": True}, yazdir=cikti.append)
    u = m["uzun"]
    assert u["hata_kisa"] == 6 and u["referans_kelime"] == 49
    assert u["hata_uzun"] == 0 and u["goreli"] == -1.0 and u["hedef_gecti"] and u["alt_gecti"]
    assert u["degisen_satir"] == 6 and u["belirsiz_kelime"] >= 6
    g = u["gpu"]
    assert g["pencere"] >= 3 and g["istek"] >= 2 and g["kisa_istek"] == 10 and g["saatlik_istek"] is not None
    pencereler = [c for c in stt.cagri if len(c["klip"]) > 1]
    assert len(pencereler) >= 2
    assert all(15.0 <= c["sn"] <= ses.UZUN_MAKS_SN + 0.01 for c in pencereler)
    assert any(set(a["klip"]) & set(b["klip"]) for a, b in zip(pencereler, pencereler[1:]))     # ortusme var
    assert any("Uzun pencere: WER kısa" in s for s in cikti)
    with open(os.path.join(str(tmp_path / "c"), "satirlar.jsonl"), encoding="utf-8") as f:
        satirlar = sorted((json.loads(x) for x in f if x.strip()), key=lambda s: s["klip_no"])
    assert [s["text"] for s in satirlar] == DOGRU                  # her kelime tam bir kez, dogru klipte
    assert [s.get("raw_kisa", s["text"]) for s in satirlar] == KISA
    with open(os.path.join(str(tmp_path / "c"), "olcum.json"), encoding="utf-8") as f:
        assert json.load(f)["uzun"]["hata_uzun"] == 0


def test_replay_uzun_stt_olmadan_reddedilir(tmp_path):
    k = _kayit(tmp_path)
    with pytest.raises(SystemExit):
        replay.main([k, "--uzun"])
