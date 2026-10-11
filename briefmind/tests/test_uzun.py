"""Faz 3 uzun pencere: uzun.py hizalama (dikis LCS, SequenceMatcher dagitimi, sayi / yanlis hizalama korumalari),
ses.UzunPencere planlama + cozum (prompt sizintisi korumasi, halusinasyon tekrari), SesServisi baglantisi ve bayrak
kapaliyken degismeyen yol, Motor uzun pencere sonucu (konusmaci degismez, yaris kurali, yeniden ozet kuyrugu).
Ag / ses cihazi yok; adlar kurgusal."""
import datetime as dt
import json
import os

import numpy as np
import pytest

import motor as motor_mod
import saat
import ses
import uzun
from motor import Motor

T0 = 1_760_000_000.0


# ======================================================================================== uzun.py

def _k(no, bas, bit, metin):
    return {"klip_no": no, "bas": bas, "bit": bit, "text": metin}


def _sonuc(s, no):
    return next(r for r in s["klipler"] if r["klip_no"] == no)


def test_kelime_norm_turkce_kesme_sayi():
    assert uzun.kelime_norm("İstanbul'da,") == "istanbulda"
    assert uzun.kelime_norm("IŞIK") == "ışık"
    assert uzun.kelime_norm("3,5") == uzun.kelime_norm("3.5") == "3.5"
    assert uzun.kelime_norm("14:30'da") == "14.30da"
    assert uzun.kelime_norm("—") == ""
    assert [n for _, n in uzun.kelimeler("Evet — tamam.")] == ["evet", "tamam"]


def test_sinirda_kesilen_kelime_uzun_metinle_duzelir_cift_yazilmaz():
    """Kisa klip siniri kelimeyi boldu ('on' | 'da'): uzun pencere 'onda' der; kelime tek klibe yazilir, kaybolmaz,
    kisa / uzun farki 'belirsiz'e girer (⟨⟩ yok)."""
    k = [_k(1, 0, 4, "Toplantıyı yarın saat on"), _k(2, 4.0, 7, "da yapalım, herkes gelsin.")]
    s = uzun.pencere_hizala("Toplantıyı yarın saat onda yapalım, herkes gelsin.", k)
    a, b = _sonuc(s, 1), _sonuc(s, 2)
    assert a["text"] == "Toplantıyı yarın saat onda" and a["belirsiz"] == ["onda"]
    assert b["text"] == "yapalım, herkes gelsin." and b["belirsiz"] == []
    birlesik = (a["text"] + " " + b["text"]).split()
    assert birlesik.count("onda") == 1 and "⟨" not in a["text"] + b["text"]


def test_dikis_ortusmedeki_onceki_kelimeleri_atar():
    """Ortusme (1.5 sn) onceki pencerenin son kelimelerini tekrar duyar: baglama eslenen bas atilir (cift yok)."""
    baglam = [_k(0, -6.0, -0.4, "bunu sonra ayrıca konuşalım")]
    s = uzun.pencere_hizala("ayrıca konuşalım. Peki, devam edelim mi?", [_k(1, 0, 4, "peki devam edelim mi")], baglam,
                            pencere_bas=-1.5)
    assert s["dikis"] == 2 and s["dikis_kesin"]
    assert _sonuc(s, 1)["text"] == "Peki, devam edelim mi?"


def test_dikis_yarim_duyulan_parcayi_atar():
    """Pencere basi bir kelimenin ortasina dustu ('ar' < 'besar'): benzer_kelime ile baglama eslenir, sahip klibe girmez."""
    baglam = [_k(0, -5.0, -1.2, "kalan iş besar")]
    s = uzun.pencere_hizala("ar Kalabe rabom sebenes", [_k(1, 0, 3, "kalabe rabom sebenes")], baglam, pencere_bas=-1.5)
    assert s["dikis"] == 1
    assert _sonuc(s, 1)["text"] == "Kalabe rabom sebenes"


def test_dikis_tekrarlanan_ifade_sahip_klipten_kaybolmaz():
    """Baglam 'evet evet' ile biter, sahip klip 'evet tamam' ile baslar: ortusmede yalniz bir 'evet' var. Esit uzunluktaki
    LCS cozumlerinden sahip klibe daha cok esleyen secilir: sahip klibin 'evet'i kalir."""
    baglam = [_k(0, -3.0, -0.6, "evet evet")]
    s = uzun.pencere_hizala("evet evet tamam anlaştık", [_k(1, 0, 4, "evet tamam anlaştık")], baglam, pencere_bas=-1.5)
    assert s["dikis"] == 1
    assert _sonuc(s, 1)["text"] == "evet tamam anlaştık"


def test_dikis_kesik_baglam_kelimesi_sahibe_tasmaz():
    """Baglamin son kelimesi kisa metinde yarim ('on'), uzun pencere 'onda' duyar: 'onda' baglamin payidir (eslenmemis
    baglam kelimesi kadar eslenmemis uzun kelime atilir), sahip klibe eklenmez."""
    baglam = [_k(0, -4.0, -0.3, "yarın saat on")]
    s = uzun.pencere_hizala("saat onda yapalım herkes gelsin", [_k(1, 0, 3, "yapalım herkes gelsin")], baglam,
                            pencere_bas=-1.5)
    assert s["dikis"] == 2
    assert _sonuc(s, 1)["text"] == "yapalım herkes gelsin"


def test_dikis_eslemesiz_ve_ortusmede_konusma_varken_ilk_klip_kisa_kalir():
    """Ortusmede baglam kelimesi bekleniyor ama uzun metinde hic eslesmiyor: dikis kesin degil, ilk sahip klip kisa kalir;
    sonraki klipler yine duzelir."""
    baglam = [_k(0, -3.0, -0.2, "alfa beta gama delta")]
    k = [_k(1, 0, 3, "kalem masa sandalye"), _k(2, 3.5, 6, "pencere kapı duvar zemin")]
    s = uzun.pencere_hizala("zzz yyy kalem masa sandalye pencere kapı duvar zemini", k, baglam, pencere_bas=-1.5)
    assert not s["dikis_kesin"]
    assert _sonuc(s, 1)["text"] is None
    assert _sonuc(s, 2)["text"] == "pencere kapı duvar zemini" and _sonuc(s, 2)["belirsiz"] == ["zemini"]


def test_dikis_ortusmede_kelime_beklenmiyorsa_bir_sey_atilmaz():
    """Baglam klibi pencere basina yalniz degiyor (ortusmede kelimesi yok): uzun metnin basi sahip klibindir."""
    baglam = [_k(0, -6.0, -1.45, "bambaşka bir cümle")]
    s = uzun.pencere_hizala("Kalabe rabom sebenes lutetur", [_k(1, 0, 3, "kalabe rabom sebenes lutet")], baglam,
                            pencere_bas=-1.5)
    assert s["dikis"] == 0 and s["dikis_kesin"]
    assert _sonuc(s, 1)["text"] == "Kalabe rabom sebenes lutetur"


def test_ekleme_ve_silme_sinirda():
    """Klip sinirinda uzun pencere fazladan bir kelime duyar (ekleme) ya da kisa klipteki bir kelimeyi duymaz (silme).
    Ekleme tek klibe bir kez yazilir; hangisine: komsu kelimelerin klip araligindan tahmin edilen anlari arasinda, sinirin
    hangi yaninda kaldigina gore (sol klibin sonunda bos yer var -> sol; sag klibin basinda -> sag)."""
    k = [_k(1, 0, 4, "bir iki"), _k(2, 4.2, 8, "altı yedi sekiz dokuz on yirmi otuz kırk")]
    s = uzun.pencere_hizala("bir iki buçuk altı yedi sekiz dokuz on yirmi otuz kırk", k)
    assert _sonuc(s, 1)["text"] == "bir iki buçuk" and _sonuc(s, 1)["belirsiz"] == ["buçuk"]
    assert _sonuc(s, 2)["text"] == "altı yedi sekiz dokuz on yirmi otuz kırk"
    k = [_k(1, 0, 4, "bir iki üç dört beş altı yedi sekiz"), _k(2, 4.2, 8, "dokuz on")]
    s = uzun.pencere_hizala("bir iki üç dört beş altı yedi sekiz sıfır dokuz on", k)
    assert _sonuc(s, 1)["text"] == "bir iki üç dört beş altı yedi sekiz"
    assert _sonuc(s, 2)["text"] == "sıfır dokuz on" and _sonuc(s, 2)["belirsiz"] == ["sıfır"]
    k = [_k(1, 0, 4, "bir iki üç dört beş"), _k(2, 4.2, 8, "altı yedi sekiz dokuz")]
    s = uzun.pencere_hizala("bir iki üç dört beş yedi sekiz dokuz", k)
    assert _sonuc(s, 1)["text"] == "bir iki üç dört beş"
    assert _sonuc(s, 2)["text"] == "yedi sekiz dokuz"


def test_sayi_kaybolursa_kisa_kalir_uyusmayan_sayi_belirsiz():
    """Uzun metin kisadaki sayiyi farkli duydu: sayi sessizce degismez (kisa kalir), uyusmayan sayi 'belirsiz'."""
    s = uzun.pencere_hizala("saat 14'te 50 kişi geliyor", [_k(1, 0, 3, "saat 14'te 15 kişi geliyor")])
    r = _sonuc(s, 1)
    assert r["text"] is None and r["belirsiz"] == ["15"]
    # uzun metin fazladan sayi duydu: metin uzun olur ama sayi belirsiz
    s = uzun.pencere_hizala("toplam 3 tane 250 lira", [_k(1, 0, 3, "toplam 3 tane lira")])
    r = _sonuc(s, 1)
    assert r["text"] == "toplam 3 tane 250 lira" and r["belirsiz"] == ["250"]
    # ayni sayi farkli yazim: esit
    s = uzun.pencere_hizala("oran 3,5 oldu", [_k(1, 0, 3, "oran 3.5 oldu")])
    assert _sonuc(s, 1)["text"] == "oran 3,5 oldu" and _sonuc(s, 1)["belirsiz"] == []


def test_yanlis_hizalama_kisa_metin_kalir():
    k = [_k(1, 0, 3, "evet tamam anlaştık"), _k(2, 3.5, 6, "yarın konuşuruz")]
    s = uzun.pencere_hizala("tamamen başka bir konu hakkında uzun uzun konuşuyoruz", k)
    assert s["oran"] < uzun.ESIK
    assert all(r["text"] is None and r["belirsiz"] == [] for r in s["klipler"])
    # pencere iyi, tek klip kotu (< 0.6): o klip kisa kalir
    k = [_k(1, 0, 6, "birinci ikinci üçüncü dördüncü beşinci altıncı"), _k(2, 6.5, 8, "ka me")]
    s = uzun.pencere_hizala("birinci ikinci üçüncü dördüncü beşinci altıncı kara meşe", k)
    assert _sonuc(s, 1)["text"] is not None
    assert _sonuc(s, 2)["text"] is None and _sonuc(s, 2)["oran"] < uzun.ESIK


def test_metinsiz_klibe_dusen_uzun_kelimeler_atilir():
    """Aradaki klibin satiri yok (suzgecte dustu / halusinasyon): o araliga dusen uzun kelimeler komsu satira yazilmaz."""
    k = [_k(1, 0, 3, "bir iki üç"), _k(2, 3.5, 5, ""), _k(3, 5.5, 8, "dört beş altı")]
    s = uzun.pencere_hizala("bir iki üç uydurma laf dört beş altı", k)
    assert _sonuc(s, 1)["text"] == "bir iki üç" and _sonuc(s, 3)["text"] == "dört beş altı"
    assert _sonuc(s, 2)["text"] is None


def test_ortak_altdizi_esitlikte_sahibi_secer():
    ref = ["evet", "evet", "evet", "tamam"]
    c = uzun.ortak_altdizi(ref, ["evet", "evet", "tamam"], [False, False, True, True])
    assert c == [(1, 0), (2, 1), (3, 2)]


def test_son_cumle():
    assert uzun.son_cumle("Birinci cümle. İkinci cümle burada!") == "İkinci cümle burada!"
    assert uzun.son_cumle("") == ""


# ======================================================================================== ses.UzunPencere

class _Saat:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


def _pencere(cagri=None, yay=None, puan=None, saat_=None, **kw):
    cagri = cagri if cagri is not None else []
    yay = yay if yay is not None else []

    def coz(ses_, ipucu):
        cagri.append({"ses": ses_, "ipucu": ipucu})
        return [{"text": ses_["metin"] if isinstance(ses_, dict) else ""}]
    return ses.UzunPencere(coz, kw.pop("ses_fn", lambda a, b, e: {"metin": "", "bas": b, "bit": e}), yay.append,
                           puan_fn=puan, saat=saat_ or _Saat(), **kw)


def _klipler(u, araliklar, akis="loopback", rms=0.05, metin=True):
    for i, (b, e) in enumerate(araliklar, 1):
        u.klip_kaydet(akis, i, T0 + b, T0 + e, rms=rms)
        if metin:
            u.metin_kaydet(i, f"klip{i} metni")


def test_planlama_20_28_sn_en_genis_sessizlikte_ortusmeli():
    """Kesintisiz konusma (5 sn'lik klipler, aralar 0.1-0.9 sn): pencere sesi 20-28 sn, en genis sessizlikte bolunur, ilk
    sahip klipten 1.5 sn once baslar; her klip tam bir pencereye sahip olur; onceki pencerenin son klibi baglamdir."""
    u = _pencere()
    araliklar, t = [], 0.0
    bosluk = [0.1, 0.2, 0.9, 0.1, 0.3, 0.1, 0.8, 0.2, 0.1, 0.4, 0.2, 0.1, 0.9, 0.1, 0.2]
    for g in bosluk:
        araliklar.append((t, t + 5.0))
        t += 5.0 + g
    _klipler(u, araliklar)
    u.ilerle("loopback", T0 + t)
    pler = u.planla()
    assert len(pler) >= 2
    sahip = [n for p in pler for n in p["sahip"]]
    assert len(sahip) == len(set(sahip))                        # bir klip tek pencereye sahip
    for p in pler:
        uz = p["bit"] - p["bas"]
        ilk = araliklar[p["sahip"][0] - 1][0]
        assert p["bas"] == pytest.approx(T0 + ilk - ses.UZUN_ORTUSME_SN)
        assert uz <= ses.UZUN_MAKS_SN + 1e-6
        assert uz >= ses.UZUN_MIN_SN - 1e-6
    for onceki, p in zip(pler, pler[1:]):
        assert onceki["sahip"][-1] in p["baglam"]                # ortusmedeki onceki klip baglam
        assert p["kapsam"] > onceki["kapsam"]


def test_uzun_sessizlik_pencereyi_kapatir_tek_klip_stt_ye_gitmez():
    cagri, yay = [], []
    u = _pencere(cagri, yay)
    _klipler(u, [(0, 4), (4.3, 8), (20, 24)])
    u.ilerle("loopback", T0 + 30)                                # 24'ten sonra 6 sn sessizlik
    pler = u.planla()
    assert [p["sahip"] for p in pler] == [[1, 2], [3]]
    assert pler[1]["neden"] == "tek_klip"
    u.adim()
    assert len(cagri) == 1                                       # tek klipli pencere istek atmaz
    assert [e["kapsam"] for e in yay] == [T0 + 8, T0 + 24]
    assert yay[1]["neden"] == "tek_klip" and yay[1]["klipler"] == []


def test_prompt_onceki_pencerenin_son_cumlesi_yalniz_yuksek_rms():
    """prompt = ayni akisin onceki penceresinin son cumlesi; pencere RMS'i ipucu_rms altindaysa prompt GITMEZ."""
    cagri = []
    metinler = iter(["Birinci pencere. Son cümle burada.", "ikinci pencere metni", "ucuncu"])
    u = _pencere(cagri, ses_fn=lambda a, b, e: {"metin": next(metinler)})
    u.klip_kaydet("loopback", 1, T0, T0 + 4, rms=0.05)
    u.klip_kaydet("loopback", 2, T0 + 4.2, T0 + 8, rms=0.05)
    u.klip_kaydet("loopback", 3, T0 + 20, T0 + 24, rms=0.05)
    u.klip_kaydet("loopback", 4, T0 + 24.2, T0 + 28, rms=0.05)
    u.klip_kaydet("loopback", 5, T0 + 40, T0 + 44, rms=0.004)    # dusuk RMS pencere
    u.klip_kaydet("loopback", 6, T0 + 44.2, T0 + 48, rms=0.004)
    for no, m in ((1, "Birinci pencere."), (2, "Son cümle burada."), (3, "ikinci"), (4, "pencere metni"),
                  (5, "ucuncu"), (6, "")):
        u.metin_kaydet(no, m)
    u.ilerle("loopback", T0 + 60)
    u.adim()
    assert [c["ipucu"] for c in cagri] == ["", "Son cümle burada.", ""]


def test_supheli_promptlu_sonuc_promptsuz_tekrar_hala_supheli_ise_atlanir():
    cagri, yay = [], []
    puanlar = iter([0.8, 0.2, 0.95, 0.95])                      # 1. pencere: prompt'lu supheli -> promptsuz iyi; 2.: ikisi de

    def puan(p, seg, ipucu):
        return next(puanlar), ""
    u = _pencere(cagri, yay, puan=puan,
                 ses_fn=lambda a, b, e: {"metin": "alfa beta gama delta" if b < T0 + 10 else "epsilon zeta eta teta"})
    _klipler(u, [(0, 4), (4.2, 8), (20, 24), (24.2, 28)], metin=False)
    for no, m in ((1, "alfa beta"), (2, "gama delta."), (3, "epsilon zeta"), (4, "eta teta")):
        u.metin_kaydet(no, m)
    u._akis["loopback"]["onceki_cumle"] = "Önceki cümle."
    u.ilerle("loopback", T0 + 40)
    u.adim()
    # 1. pencere: prompt (onceki_cumle) -> puan 0.8 supheli -> promptsuz 0.2: promptsuz tutulur
    assert [c["ipucu"] for c in cagri[:2]] == ["Önceki cümle.", ""]
    assert yay[0]["neden"] is None
    # 2. pencere: prompt = 1. pencerenin son cumlesi; prompt'lu 0.95, promptsuz 0.95 -> halusinasyon: metin uygulanmaz,
    # kapsam yine ilerler
    assert [c["ipucu"] for c in cagri[2:]] == ["gama delta", ""]
    assert yay[1]["neden"] == "halusinasyon" and yay[1]["klipler"] == []
    assert yay[1]["kapsam"] == T0 + 28


def test_kisa_metin_beklenir_zorla_ile_metin_yok():
    saat_ = _Saat()
    yay = []
    u = _pencere(yay=yay, saat_=saat_, ses_fn=lambda a, b, e: {"metin": "a b c"})
    _klipler(u, [(0, 4), (4.2, 8)], metin=False)
    u.ilerle("loopback", T0 + 20)
    assert u.adim() == 0 and not yay                             # metinler gelmedi: bekler
    u.metin_kaydet(1, "a b")
    saat_.t += ses.UZUN_METIN_BEKLE_SN + 1
    assert u.adim() == 1 and yay[0]["neden"] == "metin_yok" and yay[0]["klipler"] == []


def test_stt_hatasi_kisa_metni_korur_kapsam_ilerler():
    yay = []

    def coz(ses_, ipucu):
        raise RuntimeError("503")
    u = ses.UzunPencere(coz, lambda a, b, e: {"metin": "x"}, yay.append, saat=_Saat())
    _klipler(u, [(0, 4), (4.2, 8)])
    u.ilerle("loopback", T0 + 20)
    u.adim()
    assert yay[0]["neden"] == "stt_hata" and yay[0]["klipler"] == [] and yay[0]["kapsam"] == T0 + 8
    assert u.istek == 1


def test_yeniden_kesilen_klibin_parcalari_ustun_yerine_gecer():
    u = _pencere()
    u.klip_kaydet("loopback", 1, T0, T0 + 6)
    u.klip_kaydet("loopback", 2, T0 + 6.2, T0 + 10)
    u.klip_kaydet("loopback", 11, T0, T0 + 3, ust=1)             # 1 bolundu: parcalari sahip olur
    u.klip_kaydet("loopback", 12, T0 + 3, T0 + 6, ust=1)
    u.ilerle("loopback", T0 + 30)
    p = u.planla()[0]
    assert p["sahip"] == [11, 12, 2] and 1 not in p["sahip"]


def test_kapsanmis_bolgede_gec_gelen_klip_yalniz_baglam():
    u = _pencere()
    _klipler(u, [(0, 4), (4.2, 8)])
    u.ilerle("loopback", T0 + 20)
    u.planla()
    u.klip_kaydet("loopback", 9, T0 + 2, T0 + 3)                 # kesinti sonrasi gec cozulen eski klip
    u.klip_kaydet("loopback", 10, T0 + 30, T0 + 34)
    u.klip_kaydet("loopback", 11, T0 + 34.2, T0 + 38)
    u.ilerle("loopback", T0 + 60)
    p = u.planla()[0]
    assert p["sahip"] == [10, 11] and u._klip[9]["baglam"]


def test_istatistik_saatlik_istek():
    saat_ = _Saat()
    u = _pencere(saat_=saat_, ses_fn=lambda a, b, e: {"metin": "x y"})
    _klipler(u, [(0, 4), (4.2, 8)])
    u.ilerle("loopback", T0 + 20)
    u.adim()
    saat_.t = T0 + 1800
    d = u.istatistik(kisa_istek=10)
    assert d["istek"] == 1 and d["saatlik_istek"] == 2.0 and d["kisa_saatlik"] == 20.0 and d["pencere"] == 1


# ======================================================================================== SesServisi

def test_bayrak_kapali_uzun_yok_ve_yayim_degismez(monkeypatch):
    sv = ses.SesServisi("", olay_fn=lambda ev: None, uzun_pencere=False)
    assert sv.uzun is None and not sv.uzun_pencere
    monkeypatch.setattr(ses.UzunPencere, "metin_kaydet", lambda *a: pytest.fail("bayrak kapaliyken cagrilmamali"))
    sv._olay_yay({"tur": "klip", "klip_no": 1, "akis": "loopback", "bas": T0, "bit": T0 + 2, "text": "Merhaba dünya.",
                  "ust_klip": None})
    assert sv.uzun_istatistik() is None


def test_bayrak_config_den_okunur(monkeypatch):
    import ayar
    monkeypatch.setattr(ayar, "ham_oku", lambda yol=None: {"uzun_pencere": True})
    assert ses.SesServisi("").uzun_pencere is True
    monkeypatch.setattr(ayar, "ham_oku", lambda yol=None: {"uzun_pencere": "evet"})
    assert ses.SesServisi("").uzun_pencere is False                # yalniz true acar
    monkeypatch.setattr(ayar, "ham_oku", lambda yol=None: {})
    assert ses.SesServisi("").uzun_pencere is False


def test_servis_yayim_metni_kaydeder_ve_uzun_durum_olayi():
    olaylar = []
    sv = ses.SesServisi("", olay_fn=olaylar.append, uzun_pencere=True)
    u = sv.uzun_ac()
    assert olaylar[-1] == {"tur": "uzun_durum", "acik": True}
    u.klip_kaydet("mikrofon", 5, T0, T0 + 3)
    sv._olay_yay({"tur": "klip", "klip_no": 5, "akis": "mikrofon", "bas": T0, "bit": T0 + 3,
                  "text": "Bu bir deneme cümlesidir.", "ust_klip": None})
    assert u._klip[5]["metin"] == "Bu bir deneme cümlesidir."
    # tekrar suzgecinde tamamen dusen mikrofon klibi: metin "" (satiri yok), olay yayilmaz
    u.klip_kaydet("mikrofon", 6, T0 + 4, T0 + 7)
    n = len(olaylar)
    sv._olay_yay({"tur": "klip", "klip_no": 6, "akis": "mikrofon", "bas": T0 + 4, "bit": T0 + 7,
                  "text": "Bu bir deneme cümlesidir.", "ust_klip": None})
    assert u._klip[6]["metin"] == "" and len(olaylar) == n


def test_halka_tampondan_pencere_sesi():
    sv = ses.SesServisi("", uzun_pencere=False)
    a = ses.AkisYakalayici("loopback", None, loopback=True)
    a._capa_kur(0)
    a.epoch_capa = T0
    a._capalar = [(0, T0)]
    ham = (np.sin(np.arange(30 * ses.ORNEK) / 7.0) * 0.1).astype("float32")
    a.halka.ekle(0, ham)
    a.toplam_ornek = len(ham)
    sv.akislar = [a]
    x = sv._uzun_ses("loopback", T0 + 2.0, T0 + 12.0)
    assert x is not None and len(x) == 10 * ses.ORNEK
    assert sv._uzun_ses("loopback", T0 + 29.5, T0 + 40.0) is None     # < 1 sn kaldi
    assert sv._uzun_ses("mikrofon", T0, T0 + 5) is None
    assert a.ilerleme() == pytest.approx(T0 + 30.0)
    a._acik_ornek = 25 * ses.ORNEK                                     # acik klip: bolme 25. sn'ye kadar kesin
    assert a.ilerleme() == pytest.approx(T0 + 25.0)


def test_isci_durdurulunca_kalan_pencereleri_cozer_ve_yuku_loglar():
    olaylar, loglar = [], []
    sv = ses.SesServisi("", olay_fn=olaylar.append, olay=lambda t, v: loglar.append(v) if t == "log" else None,
                        uzun_pencere=True)
    sv.stt = type("S", (), {"coz": staticmethod(lambda ses_, ipucu="": [{"text": "bir iki üç dört beş altı"}])})()
    u = sv.uzun_ac()
    u._ses_fn = lambda a, b, e: np.zeros(10)
    u.klip_kaydet("loopback", 1, T0, T0 + 3, rms=0.05)
    u.klip_kaydet("loopback", 2, T0 + 3.1, T0 + 6, rms=0.05)
    u.metin_kaydet(1, "bir iki üç")
    u.metin_kaydet(2, "dört beş al")
    import threading
    sv._uzun_isci = threading.Thread(target=sv._uzun_dongu, daemon=True)
    sv._uzun_isci.start()
    sv._uzun_kapat()
    uz = [e for e in olaylar if e.get("tur") == "uzun"]
    assert len(uz) == 1 and uz[0]["klipler"] == [{"klip_no": 2, "text": "dört beş altı", "belirsiz": ["altı"],
                                                   "oran": pytest.approx(0.6667, abs=1e-3)}]
    assert any("uzun pencere (son)" in m and "STT isteği" in m for m in loglar)


# ======================================================================================== Motor

@pytest.fixture
def sanal():
    s = {"t": T0}
    geri = saat.sanal_kur(lambda: s["t"])
    yield s
    geri()


def _motor(tmp_path, olaylar=None):
    m = Motor(str(tmp_path / "t"), "Uzun", dt.date(2026, 10, 11), None, ["Melek Taslak"], duzelt=False,
              olay=(lambda t, v: olaylar.append((t, v))) if olaylar is not None else None)
    m._isle = lambda veri, duzelt: None
    return m


def _klip(m, no, bas, bit, metin, akis="loopback"):
    return m.ses_olayi({"tur": "klip", "klip_no": no, "akis": akis, "bas": bas, "bit": bit, "text": metin,
                        "ust_klip": None, "supheli": False, "hal_puani": 0.0, "rms": 0.05})


def test_motor_uzun_sonucu_metni_degistirir_konusmaciya_dokunmaz(tmp_path, sanal):
    olaylar = []
    m = _motor(tmp_path, olaylar)
    s = _klip(m, 1, T0, T0 + 4, "Toplantıyı yarın saat on")
    s.update(speaker="Melek Taslak", spk_guven=0.82, durum="karar", adaylar=[["Melek Taslak", 0.82]])
    once = {k: s[k] for k in ("speaker", "spk_guven", "adaylar", "devir", "cakisma", "klip_no", "bas", "bit", "durum")}
    m.ses_olayi({"tur": "uzun", "akis": "loopback", "pencere_no": 1, "kapsam": T0 + 4,
                 "klipler": [{"klip_no": 1, "text": "Toplantıyı yarın saat onda", "belirsiz": ["onda"], "oran": 0.75}]})
    assert s["text"] == s["raw"] == "Toplantıyı yarın saat onda" and s["raw_kisa"] == "Toplantıyı yarın saat on"
    assert s["belirsiz"] == ["onda"] and "⟨" not in s["text"]
    assert {k: s[k] for k in once} == once
    g = [v for t, v in olaylar if t == "satir_guncelle"][-1]
    assert g["id"] == s["id"] and g["uzun_degisen"] == ["onda"]
    kayit = motor_mod.altyazi_kayitlari(m.klasor)
    assert kayit[0]["text"] == "Toplantıyı yarın saat onda" and kayit[0]["raw_kisa"] == "Toplantıyı yarın saat on"
    assert m.uzun_guncellenen == 1


def test_motor_supheli_satira_ve_yeni_metinsiz_sonuca_dokunmaz(tmp_path, sanal):
    m = _motor(tmp_path)
    s = _klip(m, 1, T0, T0 + 4, "izlediğiniz için teşekkürler")
    s["supheli"] = True
    m.ses_olayi({"tur": "uzun", "akis": "loopback", "kapsam": T0 + 4,
                 "klipler": [{"klip_no": 1, "text": "başka metin", "belirsiz": [], "oran": 0.9}]})
    assert s["text"] == "izlediğiniz için teşekkürler" and "raw_kisa" not in s
    s2 = _klip(m, 2, T0 + 5, T0 + 8, "saat 14'te 15 kişi")
    m.ses_olayi({"tur": "uzun", "akis": "loopback", "kapsam": T0 + 8,
                 "klipler": [{"klip_no": 2, "text": None, "belirsiz": ["15"], "oran": 0.8}]})
    assert s2["text"] == "saat 14'te 15 kişi" and s2["belirsiz"] == ["15"] and "raw_kisa" not in s2


def test_bayrak_kapali_parca_hemen_kapanir(tmp_path, sanal):
    m = _motor(tmp_path)
    s = _klip(m, 1, T0, T0 + 4, "bir iki üç")
    s.update(speaker="Melek Taslak", spk_guven=0.9, durum="karar")
    m.parca_kapat("token")
    assert m.parca_no == 1 and m._kapanis is None


def test_yaris_kurali_uzun_sonucu_bekler_gelince_kapanir(tmp_path, sanal):
    m = _motor(tmp_path)
    m.ses_olayi({"tur": "uzun_durum", "acik": True})
    s = _klip(m, 1, T0, T0 + 4, "bir iki üç")
    s.update(speaker="Melek Taslak", spk_guven=0.9, durum="karar")
    m.parca_kapat("token")
    assert m.parca_no == 0 and m._kapanis is not None and m._kapanis["kon_tamam"]
    sanal["t"] += 5
    m.kontrol()
    assert m.parca_no == 0                                       # sonuc yok, 20 sn dolmadi
    m.ses_olayi({"tur": "uzun", "akis": "loopback", "kapsam": T0 + 4,
                 "klipler": [{"klip_no": 1, "text": "bir iki üç dört", "belirsiz": ["dört"], "oran": 0.86}]})
    m.kontrol()
    assert m.parca_no == 1
    veri = m.parca_oku(1)
    assert veri["satirlar"][0]["text"] == "bir iki üç dört"


def test_yaris_kurali_20_sn_sonra_kisa_metinle_kapanir_gec_sonuc_yeniden_ozetler(tmp_path, sanal):
    m = _motor(tmp_path)
    isler = []
    m._isle = lambda veri, duzelt: isler.append([s["text"] for s in veri["satirlar"]])
    m.ses_olayi({"tur": "uzun_durum", "acik": True})
    s = _klip(m, 1, T0, T0 + 4, "bir iki üç")
    s.update(speaker="Melek Taslak", spk_guven=0.9, durum="karar")
    m.parca_kapat("token")
    sanal["t"] += motor_mod.UZUN_BEKLE_SN - 1
    m.kontrol()
    assert m.parca_no == 0
    sanal["t"] += 1.5
    m.kontrol()
    assert m.parca_no == 1
    m.havuz.shutdown(wait=True)
    assert isler == [["bir iki üç"]]
    m.havuz = motor_mod.cf.ThreadPoolExecutor(max_workers=1)
    # gec sonuc: kapanmis parcadaki satir guncellenir, parca yeniden ozet kuyruguna girer; konusmaci alanlari aynen kalir
    once = (s["speaker"], s["spk_guven"], s["durum"])
    m.ses_olayi({"tur": "uzun", "akis": "loopback", "kapsam": T0 + 4,
                 "klipler": [{"klip_no": 1, "text": "bir iki üç dört", "belirsiz": ["dört"], "oran": 0.86}]})
    assert m.parca_oku(1)["satirlar"][0]["text"] == "bir iki üç dört" and 1 in m._ozet_kirli and m.uzun_gec == 1
    assert (s["speaker"], s["spk_guven"], s["durum"]) == once
    sanal["t"] += motor_mod.OZET_YENI_SN + 1
    m.kontrol()
    m.havuz.shutdown(wait=True)
    assert isler[-1] == ["bir iki üç dört"]


def test_konusmaci_bekleyen_satir_eski_kurala_gore_zorlanir(tmp_path, sanal):
    """Konusmaci beklemesi ve uzun pencere beklemesi ayni anda: konusmaci zorlamasi eski davranisla ayni anda (20 sn)."""
    m = _motor(tmp_path)
    m.ses_olayi({"tur": "uzun_durum", "acik": True})
    _klip(m, 1, T0, T0 + 4, "bir iki üç")                        # durum 'beklemede'
    m.parca_kapat("token")
    assert not m._kapanis["kon_tamam"]
    sanal["t"] += motor_mod.KAPANIS_BEKLE_SN + 0.5
    m.kontrol()
    assert m.parca_no == 1 and m.parca_oku(1)["satirlar"][0]["durum"] == "karar"


def test_duzeltme_sirasinda_degisen_satir_ezilmez_uzun_belirsiz_korunur(tmp_path, sanal):
    m = _motor(tmp_path)
    s = _klip(m, 1, T0, T0 + 4, "kubernetis kümesi hazır")
    s.update(speaker="Melek Taslak", spk_guven=0.9, durum="karar", uzun_belirsiz=["hazır"])
    veri = {"sira": 1, "satirlar": [s], "duzeltmeler": [], "token": 1000}
    import duzelt as duzelt_mod

    def sahte_duzelt(girdi, adaylar, adlar, sor_fn=None, **kw):
        y = [dict(g, text=g["raw"].replace("kubernetis", "Kubernetes"),
                  duzeltmeler=[{"eski": "kubernetis", "yeni": "Kubernetes", "neden": "terim"}], belirsiz=[])
             for g in girdi]
        return y, {"satirlar": {str(g["id"]): {"kabul": [], "red": [], "belirsiz": []} for g in girdi}, "gecersiz": [],
                   "oneri": 1, "kabul": 1, "red": 0, "belirsiz": 0, "hata": None, "uyari": None}
    eski = duzelt_mod.duzelt
    duzelt_mod.duzelt = sahte_duzelt
    try:
        m._duzelt_parca(veri)
        assert s["text"] == "Kubernetes kümesi hazır" and s["belirsiz"] == ["hazır"]
        # duzeltme surerken raw degisti (uzun pencere): yazim atlanir
        def degistiren(girdi, *a, **kw):
            s["raw"] = s["text"] = "kubernetis kümesi hazır mı"
            return sahte_duzelt(girdi, *a, **kw)
        duzelt_mod.duzelt = degistiren
        m._duzelt_parca(veri)
        assert s["text"] == "kubernetis kümesi hazır mı"
    finally:
        duzelt_mod.duzelt = eski


def test_jsonl_guncelleme_kaydi_konusmaci_alani_tasimaz(tmp_path, sanal):
    m = _motor(tmp_path)
    s = _klip(m, 1, T0, T0 + 4, "bir iki")
    m.ses_olayi({"tur": "uzun", "akis": "loopback", "kapsam": T0 + 4,
                 "klipler": [{"klip_no": 1, "text": "bir iki üç", "belirsiz": ["üç"], "oran": 0.8}]})
    with open(os.path.join(m.klasor, "altyazi.jsonl"), encoding="utf-8") as f:
        kayitlar = [json.loads(x) for x in f]
    g = [k for k in kayitlar if k.get("guncelle")][-1]
    assert g["id"] == s["id"] and g["raw_kisa"] == "bir iki"
    assert not {"speaker", "spk_guven", "adaylar", "devir", "cakisma", "bas", "bit", "klip_no"} & set(g)


def test_uctan_uca_gercek_akis_halka_isle_motor(tmp_path, sanal):
    """Gercek AkisYakalayici (sentetik bloklar) -> klip_fn kaydi -> SesServisi._isle (kisa STT) -> sirali yayim (metin) ->
    uzun pencere: ilerleme akistan, ses halka tampondan, STT, hizalama -> Motor satiri. Konusmaci alanlari degismez."""
    rng = np.random.default_rng(3)

    def blok(g):
        return (g * rng.standard_normal(ses.BLOK)).astype("float32")
    bloklar = [blok(0.0005) for _ in range(10)]
    for _ in range(3):                                           # 3 x 7 sn konusma, aralar 0.6 sn sessizlik
        bloklar += [blok(0.1) for _ in range(70)] + [blok(0.0005) for _ in range(6)]
    bloklar += [blok(0.0005) for _ in range(30)]                 # 3 sn sessizlik: pencere kapanir
    kisa = iter(["bir iki üç dört beş al", "yedi sekiz dokuz on", "on bir on iki on üç"])
    istek = []

    class Stt:
        verbose = True
        yeniden_deneme = 0

        def coz(self, ses_, onceki="", ipucu=""):
            istek.append((round(len(ses_) / ses.ORNEK, 1), ipucu))
            if len(ses_) / ses.ORNEK < 12:
                return [{"text": next(kisa)}]
            return [{"text": "bir iki üç dört beş altı yedi sekiz dokuz on on bir on iki on üç"}]
    m = _motor(tmp_path)
    sv = ses.SesServisi("", olay_fn=m.ses_olayi, uzun_pencere=True)
    sv.stt = Stt()
    u = sv.uzun_ac()
    assert m.uzun_pencere is True

    class Akis(ses.AkisYakalayici):
        def _bloklar_mikrofon(self_):
            yield from bloklar
    a = Akis("mikrofon", sv.kuyruk, sayac=sv._sayac, saat=lambda: T0, klip_fn=sv._uzun_klip)
    sv.akislar = [a]
    a.run()
    klipler = []
    while not sv.kuyruk.empty():
        klipler.append(sv.kuyruk.get_nowait())
    assert len(klipler) == 3 and sorted(u._klip) == [1, 2, 3]
    for p in klipler:
        sv._isle(p)
    satirlar = [m._klip_satir[n] for n in (1, 2, 3)]
    assert [s["text"] for s in satirlar] == ["bir iki üç dört beş al", "yedi sekiz dokuz on", "on bir on iki on üç"]
    for s in satirlar:
        s.update(speaker="Melek Taslak", spk_guven=0.95, durum="karar")
    sv._uzun_ilerlet()
    assert u.adim() == 1
    # pencere: ilk klipten 1.5 sn once (halka basi 0'da kirpilir: 1.0 sn) + 3 x 7 sn + 2 x 0.6 sn + 0.3 sn kuyruk
    assert [x for x in istek if x[0] >= 12][0][0] == pytest.approx(23.5, abs=0.15)
    assert [s["text"] for s in satirlar] == ["bir iki üç dört beş altı", "yedi sekiz dokuz on", "on bir on iki on üç"]
    assert satirlar[0]["raw_kisa"] == "bir iki üç dört beş al" and satirlar[0]["belirsiz"] == ["altı"]
    assert all(s["speaker"] == "Melek Taslak" and s["spk_guven"] == 0.95 for s in satirlar)
    assert m._uzun_kapsam["mikrofon"] == pytest.approx(satirlar[2]["bit"], abs=1e-3)
    assert sv.uzun_istatistik()["istek"] == 1
