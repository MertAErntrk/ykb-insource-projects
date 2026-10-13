"""Faz 2: stt.halusinasyon_puani (sentetik sessizlik/gurultu fixture'lari, esikler), klip olayinda hal_puani/supheli/atildi,
korumali Whisper prompt'u (dusuk RMS'e yok, supheli sonuc promptsuz tekrar). Ag ve ses cihazi yok; adlar kurgusal."""
import datetime as dt

import numpy as np
import pytest

import ses
import stt

T0 = 1_700_000_000.0

# ---------------------------------------------------------------- puan fixture'lari
# (ad, metin, sesli sure sn, tokens, rms, cizelge_etkin)
# cizelge_etkin=False: altyazi saglikliyken o aralikta etkinlik yok (motor/konusmaci verir). Cizelge agirligi 0.3: akici
# cumle gurultuye uydurulmussa metin ve hiz kurallari tek basina yakalayamaz, cizelge tek basina da supheli yapmaz
# (CIZELGE_TEK); bu klipler Motor'da 'Bilinmeyen' olarak notta kalir, yalniz icsel puani da >= hal_cizelge_taban
# olanlar 'cizelge' suphesi alir (tests/test_motor_b3.py).
GERCEK = [
    ("kisa cevap", "Evet.", 0.8, None, 0.06, None),
    ("kisa cevap iki kelime", "Tamam, anladım.", 1.4, None, 0.05, True),
    ("orta cumle", "Raporu perşembeye yetiştirelim.", 2.6, None, 0.04, None),
    ("uzun cumle", "Geçen haftaki toplantıda konuştuğumuz üzere veritabanı geçişini cuma gününe kadar tamamlamamız "
                   "gerekiyor ama test ortamında bazı sorunlar yaşıyoruz.", 11.0, 60, 0.03, True),
    ("hizli konusma", "Bu konuyu bir de müşteri tarafıyla konuşup net bir tarih verelim diye düşünüyorum.", 5.5, None,
     0.05, None),
    ("kisik uzak ses", "Bence bu maddeyi bir sonraki toplantıya bırakalım çünkü ekipten kimse hazır değil.", 6.0, None,
     0.009, True),
    ("gercek tekrar kelime", "Çok çok güzel olmuş, gerçekten çok güzel.", 3.0, None, 0.05, None),
    ("rakamli", "Bütçe 250 bin lira, teslim 14 Mart.", 3.0, None, 0.05, True),
]
HALUSINASYON = [
    ("video altyazi kalibi", "Altyazı M.K.", 1.6, None, 0.003, False),
    ("abone kalibi + cizelge", "Abone olmayı unutmayın.", 2.0, None, 0.005, False),
    ("tekrar dongusu", "Selamlar. " * 14, 6.0, None, 0.01, None),
    ("sessizlige yapisan uzun cumle (kr/sn)", "Bu konuyu daha sonra tekrar konuşuruz ve karar verelim ve devam edelim.",
     1.5, None, 0.01, None),
    ("cok seyrek metin (kr/sn)", "Evet, tamam.", 7.0, None, 0.01, None),
    ("3-gram dongu", "bir sonraki adım bir sonraki adım bir sonraki adım bir sonraki adım", 9.0, None, 0.01, None),
    ("muzik etiketi", "[Müzik]", 3.0, None, 0.004, None),
]
# yalniz cizelge kuraliyla ayirt edilebilenler: tek basina 0.3 (< hal_supheli); eskiden 0.6 ile supheliydi
CIZELGE_TEK = [
    ("gurultuye uydurulmus akici cumle", "Bu konuyu daha sonra tekrar konuşuruz.", 2.4, None, 0.008, False),
    ("gurultuye uydurulmus kisa cumle", "Evet, şimdi başlayabiliriz.", 2.0, None, 0.01, False),
]


@pytest.mark.parametrize("ad,metin,sure,tokens,rms,cizelge", CIZELGE_TEK, ids=[h[0] for h in CIZELGE_TEK])
def test_yalniz_cizelgeyle_ayirt_edilen_klip_tek_basina_supheli_degil(ad, metin, sure, tokens, rms, cizelge):
    """Eski 0.6 agirlik tek sinyalle klibi supheli yapip nottan dusuruyordu (panel donuk/bayatken her klip). 0.3 ile
    puan hal_cizelge_taban'a esit, hal_supheli'nin altinda: karari Motor'daki cizelge kurali verir."""
    puan = stt.halusinasyon_puani(metin, sure, tokens=tokens, rms=rms, cizelge_etkin=cizelge)
    assert puan == pytest.approx(stt.CIZELGE_PUANI) and puan < stt.HAL_ESIKLER["hal_supheli"]
    assert puan >= stt.HAL_ESIKLER["hal_cizelge_taban"]
    assert puan == pytest.approx(stt.halusinasyon_puani(metin, sure, tokens=tokens, rms=rms) + 0.3)


@pytest.mark.parametrize("ad,metin,sure,tokens,rms,cizelge", GERCEK, ids=[g[0] for g in GERCEK])
def test_gercek_konusma_supheli_degil(ad, metin, sure, tokens, rms, cizelge):
    assert stt.halusinasyon_puani(metin, sure, tokens=tokens, rms=rms, cizelge_etkin=cizelge) < 0.5


@pytest.mark.parametrize("ad,metin,sure,tokens,rms,cizelge", HALUSINASYON, ids=[h[0] for h in HALUSINASYON])
def test_halusinasyon_fixture_kesin_gecmez(ad, metin, sure, tokens, rms, cizelge):
    assert stt.halusinasyon_puani(metin, sure, tokens=tokens, rms=rms, cizelge_etkin=cizelge) >= 0.5


def test_en_az_bir_fixture_atilma_esigini_asar_ve_hepsi_0_1_araliginda():
    puanlar = [stt.halusinasyon_puani(m, s, tokens=t, rms=r, cizelge_etkin=c) for _, m, s, t, r, c in HALUSINASYON]
    assert all(0.0 <= p <= 1.0 for p in puanlar) and max(puanlar) >= 0.9


def test_cizelge_none_kural_uygulanmaz_false_arti_03():
    """Cizelge agirligi 0.6 -> 0.3 (stt.CIZELGE_PUANI, motor ile tek sabit): zaman cizelgesi tek basina klibi supheli
    (>= 0.5) yapamaz; icsel puani zaten >= hal_cizelge_taban olan klibi supheli yapar. Kuralin niyeti ayni: None / True
    uygulanmaz, False eklenir."""
    m, s = "Bu konuyu daha sonra tekrar konuşuruz.", 2.4
    assert stt.halusinasyon_puani(m, s, cizelge_etkin=None) == stt.halusinasyon_puani(m, s, cizelge_etkin=True) == 0.0
    assert stt.CIZELGE_PUANI == 0.3
    assert stt.halusinasyon_puani(m, s, cizelge_etkin=False) == pytest.approx(0.3)
    assert 0.3 < stt.HAL_ESIKLER["hal_supheli"]


# ---------------------------------------------------------------- kural agirliklari
SAGLAM = "Raporu perşembeye yetiştirelim bence, ardından toplantıyı kapatalım."          # 67 kr


def test_kr_sn_uclari_tek_basina_supheli_yumusak_bolge_04():
    sure = lambda oran: len(SAGLAM) / oran                                            # noqa: E731
    P = lambda oran: stt.halusinasyon_puani(SAGLAM, sure(oran))                       # noqa: E731
    assert P(14.0) == 0.0
    assert P(31.0) == pytest.approx(0.6) and P(27.0) == pytest.approx(0.4)
    assert P(1.5) == pytest.approx(0.6) and P(2.5) == pytest.approx(0.4)
    assert P(3.5) == 0.0 and P(24.0) == 0.0
    assert stt.halusinasyon_puani(SAGLAM, 0) == 0.0 and stt.halusinasyon_puani(SAGLAM, None) == 0.0   # sure yok: kural yok


def test_tek_kelimelik_kisa_cevap_dusuk_kr_sn_kuralindan_muaf():
    assert stt.halusinasyon_puani("Evet.", 3.0) == 0.0                                 # 1.7 kr/sn ama gercek kisa cevap
    assert stt.halusinasyon_puani("Çünkü.", 4.0) == pytest.approx(0.6)


def test_token_sn_kurali_liste_ya_da_sayi_ve_yoksa_uygulanmaz():
    taban = stt.halusinasyon_puani(SAGLAM, 10.0)
    assert stt.halusinasyon_puani(SAGLAM, 10.0, tokens=[1] * 130) == pytest.approx(taban + 0.3)
    assert stt.halusinasyon_puani(SAGLAM, 10.0, tokens=130) == pytest.approx(taban + 0.3)
    assert stt.halusinasyon_puani(SAGLAM, 10.0, tokens=[1] * 100) == taban             # 10 token/sn: normal
    assert stt.halusinasyon_puani(SAGLAM, 10.0, tokens=None) == taban
    assert stt.halusinasyon_puani(SAGLAM, 10.0, tokens=[]) == taban


def test_zlib_ve_3gram_tekrar_kurallari():
    dongu = "evet tamam " * 12
    assert stt.halusinasyon_puani(dongu, len(dongu) / 12.0) >= 0.7                    # zlib + 3-gram
    # 3-gram payi > 0.5 degilse ve metin kisaysa kural yok
    assert stt.halusinasyon_puani("bu hangi sebepten dolayı bu hangi sebepten dolayı", 4.0) == 0.0


def test_kara_liste_alt_dizge_regex_ve_tam_eslesme():
    assert stt.halusinasyon_puani("Teşekkürler, izlediğiniz için teşekkür ederim.", 3.0) == pytest.approx(0.5)
    assert stt.halusinasyon_puani("altyazı: m.k.", 1.5) >= 0.5 and stt.halusinasyon_puani("Altyazı M. K.", 1.5) >= 0.5
    assert stt.halusinasyon_puani("Müzik", 1.0) >= 0.5
    # genel sozcuk yalniz TAM metin ise kara listede; toplanti cumlesinde gecmesi sorun degil
    assert stt.halusinasyon_puani("Altyazı özelliğini Teams'te açalım mı?", 3.0) == 0.0
    assert stt.halusinasyon_puani("Müzik ekibinin sunumunu dinleyelim.", 3.0) == 0.0
    assert stt.halusinasyon_puani("Teşekkürler.", 1.0) == 0.0                         # gercek kapanis cumlesi kara listede degil


def test_dusuk_rms_ve_uzun_metin_kurali():
    uzun = "Bence bu maddeyi bir sonraki toplantıya bırakalım çünkü ekipten kimse hazır değil ve veri yok."
    assert len(uzun) >= 80
    taban = stt.halusinasyon_puani(uzun, 7.0)
    assert stt.halusinasyon_puani(uzun, 7.0, rms=0.005) == pytest.approx(taban + 0.3)
    assert stt.halusinasyon_puani(uzun, 7.0, rms=0.05) == taban and stt.halusinasyon_puani(uzun, 7.0, rms=None) == taban
    assert stt.halusinasyon_puani("Evet, olur.", 1.0, rms=0.001) == 0.0               # kisa metin: kural yok


def test_puan_1_ile_sinirli_bos_metin_sifir():
    assert stt.halusinasyon_puani("", 3.0) == 0.0 and stt.halusinasyon_puani("   ", 3.0) == 0.0
    assert stt.halusinasyon_puani("Abone olmayı unutmayın. " * 10, 1.0, tokens=500, rms=0.001, cizelge_etkin=False) == 1.0


def test_esikler_config_ile_ezilir():
    assert stt.halusinasyon_puani(SAGLAM, len(SAGLAM) / 27.0) == pytest.approx(0.4)
    assert stt.halusinasyon_puani(SAGLAM, len(SAGLAM) / 27.0, esikler={"hal_krsn_ust_yumusak": 40, "hal_krsn_ust": 60}) == 0.0
    assert stt.halusinasyon_puani(SAGLAM, 10.0, esikler={"hal_tok_sn": 5}, tokens=130) == pytest.approx(0.3)
    assert stt.hal_esik({"hal_at": "x"}, "hal_at") == 0.9 and stt.hal_esik({"hal_at": True}, "hal_at") == 0.9
    assert stt.hal_esik({"hal_at": 0.8}, "hal_at") == 0.8


# ---------------------------------------------------------------- klip olayi: hal_puani / supheli / atildi

def _p(metinsiz_sure=3.0, akis="loopback", rms=0.07, no=0, ust=None, bas=T0 + 10.0):
    x = (0.1 * np.sin(np.arange(int(16000 * (metinsiz_sure + 0.6))) / 7.0)).astype("float32")
    return {"kaynak": akis, "basla": dt.datetime.fromtimestamp(bas),
            "bitis": dt.datetime.fromtimestamp(bas + metinsiz_sure), "ses": x, "no": no, "klip_no": no + 1,
            "bas_epoch": bas, "bit_epoch": bas + metinsiz_sure, "zarf": [0.1] * int(metinsiz_sure * 10), "rms": rms,
            "ust_klip": ust, "ornek_bas": 0, "ornek_bit": len(x), "epoch_capa": bas}


class _Stt:
    """Sahte STT: cevaplar sirayla; her cagri (ipucu) kaydedilir. coz imzasi gercek istemciyle ayni."""
    verbose = True
    yeniden_deneme = 0

    def __init__(self, *cevaplar, hata_ikinci=False):
        self.cevaplar = list(cevaplar)
        self.cagrilar = []
        self.hata_ikinci = hata_ikinci

    def coz(self, ses_, onceki="", ipucu=""):
        self.cagrilar.append(ipucu)
        if self.hata_ikinci and len(self.cagrilar) > 1:
            raise RuntimeError("ikinci istek patladi")
        c = self.cevaplar.pop(0) if len(self.cevaplar) > 1 else self.cevaplar[0]
        return [{"text": c}] if isinstance(c, str) else c

    def saglik(self, deneme=1):
        return True


def _servis(**kw):
    olaylar = []
    s = ses.SesServisi("", olay_fn=olaylar.append, **kw)
    return s, olaylar


def test_normal_klip_puani_dusuk_supheli_degil():
    s, _ = _servis()
    ev = s.klip_olayi_uret(_p(), [{"text": "Raporu perşembeye yetiştirelim."}])
    assert ev["hal_puani"] == 0.0 and ev["supheli"] is False and "atildi" not in ev


def test_puan_supheli_esigini_asarsa_supheli_metin_kalir():
    s, _ = _servis()
    ev = s.klip_olayi_uret(_p(metinsiz_sure=1.0), [{"text": "Bu konuyu daha sonra tekrar konuşuruz ve karar verelim."}])
    assert ev["hal_puani"] == pytest.approx(0.6) and ev["supheli"] is True
    assert ev["text"] and "atildi" not in ev                                          # 0.5 <= puan < 0.9: metin kalir


def test_cizelge_fn_false_supheli_none_ve_hata_kural_yok():
    cagrilar = []

    def cizelge(akis, bas, bit):
        cagrilar.append((akis, bas, bit))
        return False

    s, _ = _servis(cizelge_fn=cizelge)
    ev = s.klip_olayi_uret(_p(), [{"text": "Bu konuyu daha sonra tekrar konuşuruz."}])
    # cizelge_fn False -> +0.3: tek basina supheli degil (zaman cizelgesi suphesini Motor verir, hal_ic taban ister)
    assert cagrilar == [("loopback", T0 + 10.0, T0 + 13.0)] and not ev["supheli"]
    assert ev["hal_puani"] == pytest.approx(0.3)
    s2, _ = _servis(cizelge_fn=lambda *a: None)
    assert s2.klip_olayi_uret(_p(), [{"text": "Bu konuyu daha sonra tekrar konuşuruz."}])["hal_puani"] == 0.0

    def patlar(*a):
        raise RuntimeError("x")
    s3, _ = _servis(cizelge_fn=patlar)
    assert s3.klip_olayi_uret(_p(), [{"text": "Bu konuyu daha sonra tekrar konuşuruz."}])["hal_puani"] == 0.0


def test_puan_at_esigini_asarsa_metin_atilir_olay_kalir():
    s, olaylar = _servis(cizelge_fn=lambda *a: False)
    ev = s.klip_olayi_uret(_p(metinsiz_sure=1.0), [{"text": "Bu konuyu daha sonra tekrar konuşuruz ve karar verelim."}])
    assert ev["hal_puani"] >= 0.9 and ev["supheli"] is True
    assert ev["text"] == "" and ev["atildi"] == "halusinasyon" and s.atilan_halusinasyon == 1
    # yayimda: loopback klibi metinsiz yine gelir (o anda akista ses vardi), mikrofon klibi olay uretmez
    s._sirali_yay(None, [ev])
    assert [(e["klip_no"], e["text"], e.get("atildi")) for e in olaylar] == [(1, "", "halusinasyon")]
    s._sirali_yay(None, [dict(ev, akis="mikrofon", klip_no=2)])
    assert len(olaylar) == 1


def test_esikler_servise_verilen_config_ile_ezilir():
    s, _ = _servis(esikler={"hal_supheli": 0.95, "hal_at": 0.99})
    ev = s.klip_olayi_uret(_p(metinsiz_sure=1.0), [{"text": "Bu konuyu daha sonra tekrar konuşuruz ve karar verelim."}])
    assert ev["hal_puani"] == pytest.approx(0.6) and ev["supheli"] is False
    s2, _ = _servis(esikler={"esikler": {"hal_supheli": 0.3}})                          # config sozlugunun tamami da olur
    assert s2.klip_olayi_uret(_p(metinsiz_sure=1.0), [{"text": "Bu konuyu daha sonra tekrar konuşuruz ve karar verelim."}]
                              )["supheli"] is True


def test_segment_tokens_toplami_puana_girer_bos_tokens_uygulanmaz():
    s, _ = _servis()
    metin = "Raporu perşembeye yetiştirelim bence."
    assert s.klip_olayi_uret(_p(), [{"text": metin, "tokens": []}])["hal_puani"] == 0.0
    ev = s.klip_olayi_uret(_p(), [{"text": metin, "tokens": list(range(30))}, {"text": "", "tokens": list(range(20))}])
    assert ev["hal_puani"] == pytest.approx(0.3)                                       # 50 token / 3 sn = 16.7


def test_metinsiz_cocuk_klip_puan_sifir_ve_olay_olur():
    s, _ = _servis()
    ev = s.klip_olayi_uret(_p(ust=7), [{"text": ""}])
    assert ev["text"] == "" and ev["hal_puani"] == 0.0 and ev["supheli"] is False


# ---------------------------------------------------------------- korumali Whisper prompt'u

def _kos(s, stt_sahte, **p_kw):
    s.stt = stt_sahte
    s.kuyruk.put(_p(**p_kw))
    s._dur.set()
    s._calis()


def test_prompt_rms_yeterliyse_gider_ipucu_fn_akisi_alir():
    alinan = []
    s, olaylar = _servis(ipucu_fn=lambda akis: alinan.append(akis) or "Katılımcılar: Ayşe, Kemal; Jenkins")
    f = _Stt("Raporu perşembeye yetiştirelim.")
    _kos(s, f, rms=0.07, akis="mikrofon")
    assert alinan == ["mikrofon"] and f.cagrilar == ["Katılımcılar: Ayşe, Kemal; Jenkins"]
    assert s.ipucu_gonderilen == 1 and s.ipucu_yeniden == 0 and olaylar[0]["text"] == "Raporu perşembeye yetiştirelim."


def test_dusuk_rms_klibe_prompt_gitmez_ipucu_fn_cagrilmaz():
    cagri = []
    s, _ = _servis(ipucu_fn=lambda akis: cagri.append(akis) or "Jenkins")
    f = _Stt("Raporu perşembeye yetiştirelim.")
    _kos(s, f, rms=0.005)
    assert f.cagrilar == [""] and cagri == [] and s.ipucu_gonderilen == 0
    s2, _ = _servis(ipucu_fn=lambda akis: "Jenkins", esikler={"ipucu_rms": 0.002})      # esik config'ten
    f2 = _Stt("Raporu perşembeye yetiştirelim.")
    _kos(s2, f2, rms=0.005)
    assert f2.cagrilar == ["Jenkins"]


def test_rms_bilinmiyorsa_prompt_yok():
    s, _ = _servis(ipucu_fn=lambda akis: "Jenkins")
    p = _p()
    p["rms"] = None
    assert s._ipucu_hazirla(p) == ""


def test_stt_ipucu_kapaliysa_ya_ipucu_fn_yoksa_prompt_yok():
    s, _ = _servis(ipucu_fn=lambda akis: "Jenkins", stt_ipucu=False)
    f = _Stt("Raporu perşembeye yetiştirelim.")
    _kos(s, f, rms=0.07)
    assert f.cagrilar == [""]
    s2, _ = _servis()
    assert s2._ipucu_hazirla(_p()) == ""


def test_eski_sifir_argumanli_ipucu_fn_ve_uzun_ipucu_kirpilir():
    s, _ = _servis(ipucu_fn=lambda: "terim " * 200)
    ip = s._ipucu_hazirla(_p())
    assert ip and len(ip) <= stt.IPUCU_KR_TAVAN
    s2, olaylar = _servis(ipucu_fn=lambda akis: 1 / 0)                                # ipucu hatasi klibi bozmaz
    assert s2._ipucu_hazirla(_p()) == ""


def test_supheli_promptlu_sonuc_promptsuz_tekrarlanir_dusuk_puanli_tutulur():
    s, olaylar = _servis(ipucu_fn=lambda akis: "Jenkins ODS")
    sizinti = "Jenkins ODS'i kullandığında bu konuyu daha sonra tekrar konuşuruz ve karar verelim."   # 1.5 sn'de uydurulmus
    f = _Stt(sizinti, "Raporu perşembeye yetiştirelim.")
    _kos(s, f, rms=0.07, metinsiz_sure=1.5)
    assert f.cagrilar == ["Jenkins ODS", ""] and s.ipucu_yeniden == 1 and s.ipucu_gonderilen == 1
    assert olaylar[0]["text"] == "Raporu perşembeye yetiştirelim." and olaylar[0]["supheli"] is False


def test_tekrar_cozum_daha_kotuyse_ilk_sonuc_tutulur_esitlikte_promptsuz():
    s, olaylar = _servis(ipucu_fn=lambda akis: "Jenkins")
    kotu = "Bu konuyu daha sonra tekrar konuşuruz ve karar verelim ve devam edelim."          # 1.0 sn: 0.6
    daha_kotu = [{"text": kotu, "tokens": list(range(60))}]                                   # ayni metin + token/sn: 0.9
    f = _Stt(kotu, daha_kotu)
    _kos(s, f, rms=0.07, metinsiz_sure=1.0)
    assert f.cagrilar == ["Jenkins", ""] and olaylar[0]["hal_puani"] == pytest.approx(0.6)
    # esit puan: promptsuz tutulur
    s2, olaylar2 = _servis(ipucu_fn=lambda akis: "Jenkins")
    f2 = _Stt(kotu, "Konuyu şimdilik daha sonra bırakıp toplantıyı kapatalım ve devam edelim.")
    _kos(s2, f2, rms=0.07, metinsiz_sure=1.0)
    assert olaylar2[0]["text"].startswith("Konuyu şimdilik")


def test_tekrar_istegi_hata_verirse_ilk_sonuc_kalir():
    s, olaylar = _servis(ipucu_fn=lambda akis: "Jenkins")
    kotu = "Bu konuyu daha sonra tekrar konuşuruz ve karar verelim ve devam edelim."
    f = _Stt(kotu, hata_ikinci=True)
    _kos(s, f, rms=0.07, metinsiz_sure=1.0)
    assert olaylar[0]["text"] == kotu and olaylar[0]["supheli"] is True and s.ipucu_yeniden == 1


def test_supheli_olmayan_promptlu_sonuc_tekrarlanmaz():
    s, _ = _servis(ipucu_fn=lambda akis: "Jenkins")
    f = _Stt("Raporu perşembeye yetiştirelim.")
    _kos(s, f, rms=0.07)
    assert len(f.cagrilar) == 1


def test_repetition_penalty_istemciye_iletilir():
    s, _ = _servis(stt_repetition_penalty=1.1)
    assert s.stt.repetition_penalty == 1.1
    assert _servis()[0].stt.repetition_penalty is None


# ---------------------------------------------------------------- prompt yankisi, suzgec oncesi kanit (bulgu regresyonu)

PROMPT = "Katılımcılar: Esra Demir, Serkan Ak. Terimler: Jenkins, Kampanya Modülü, Kubernetes."


def test_prompt_yankisi_etiket_ya_da_prompt_kelimeleri_atilir():
    # etiket: ipucu bilinmese de (replay, eski kayit) yakalanir
    assert stt.halusinasyon_puani("Katılımcılar: Serkan Ak. Terimler: Jenkins, Kubernetes.", 4.0, rms=0.02) >= 0.9
    # etiketsiz parca: cikti kelimelerinin cogu gonderilen prompt'ta
    assert stt.halusinasyon_puani("Serkan Ak, Jenkins, Kubernetes.", 3.0, rms=0.02, ipucu=PROMPT) >= 0.9
    assert stt.halusinasyon_puani("Serkan Ak, Jenkins, Kubernetes.", 3.0, rms=0.02) < 0.5        # ipucu yoksa kural yok
    # gercek cumlede prompt kelimesi az: kural uygulanmaz (en az 3 kelime ve %60)
    assert stt.halusinasyon_puani("Serkan, Jenkins'e bakalım.", 2.0, ipucu=PROMPT) < 0.5
    assert stt.halusinasyon_puani("Jenkins tarafında Kubernetes geçişini yarın konuşalım mı acaba.", 4.0,
                                  ipucu=PROMPT) < 0.5


def test_prompt_sizintisi_promptsuz_tekrarlanir_kalirsa_atilir():
    sizinti = "Katılımcılar: Serkan Ak. Terimler: Jenkins, Kubernetes."
    s, olaylar = _servis(ipucu_fn=lambda akis: PROMPT)
    f = _Stt(sizinti, "")
    _kos(s, f, rms=0.02, metinsiz_sure=4.0)
    assert f.cagrilar == [PROMPT, ""] and s.ipucu_yeniden == 1
    assert olaylar == [] or all(not ev.get("text") for ev in olaylar)
    # promptsuz tekrar patlarsa prompt'lu sizinti tutulur ama klip olayi onu atar (puan prompt'la hesaplanir)
    s2, olaylar2 = _servis(ipucu_fn=lambda akis: PROMPT)
    _kos(s2, _Stt("Serkan Ak, Jenkins, Kubernetes.", hata_ikinci=True), rms=0.02, metinsiz_sure=4.0)
    assert olaylar2[0]["text"] == "" and olaylar2[0]["atildi"] == "halusinasyon"
    # gercek konusma prompt'la cozulse de kural tetiklenmez
    s3, olaylar3 = _servis(ipucu_fn=lambda akis: PROMPT)
    _kos(s3, _Stt("Serkan, Jenkins'e yarın bakalım."), rms=0.05, metinsiz_sure=2.0)
    assert olaylar3[0]["text"] == "Serkan, Jenkins'e yarın bakalım." and olaylar3[0]["supheli"] is False


@pytest.mark.parametrize("ham,kalan", [
    ("Altyazı M.K. Herkese iyi akşamlar.", "Herkese iyi akşamlar."),
    ("Abone olmayı unutmayın. Bir sonraki videoda hepinize iyi günler.", "Bir sonraki videoda hepinize iyi günler."),
    ("İzlediğiniz için teşekkür ederim. Görüşmek üzere.", "Görüşmek üzere."),
])
def test_kara_liste_kaniti_suzgecle_silinse_de_kalan_supheli(ham, kalan):
    s, _ = _servis()
    ev = s.klip_olayi_uret(_p(), [{"text": ham}])
    assert ev["text"] == kalan and ev["supheli"] is True and ev["hal_puani"] >= 0.5


def test_dongu_kaniti_tekrar_temizlese_de_kalan_supheli():
    s, _ = _servis()
    ev = s.klip_olayi_uret(_p(metinsiz_sure=3.0), [{"text": "Teşekkür ederim. " * 4}])
    assert ev["text"] in ("", "Teşekkür ederim.") and ev["hal_puani"] >= 0.5
    # suzgec bir sey silmediyse ham puan ayni (gercek kisa cumle etkilenmez)
    assert s.klip_olayi_uret(_p(), [{"text": "Raporu perşembeye yetiştirelim."}])["hal_puani"] == 0.0
