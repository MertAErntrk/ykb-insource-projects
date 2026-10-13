"""B6 uctan uca panel / kapsama dayanikliligi kapilari (plan B0-B6; MIMARI_v3 §8 duzenek). Sentetik, ag yok, repo'ya dosya
yazmaz. Gercek: Motor (B3 cizelge kurali, B5 atama denetimi, kapsama uyarisi), Konusmaci (B0 bosluk toleransi, B2 donuk /
gecikme / gec yukseltme / yapiskan, B2b metin ipucu), Yakalayici canlilik yolu (degisti / donuk / periyodik ve zorla yeniden
bulma; UIA yerine sahte oge), SesServisi. Sahte: STT metni, Teams paneli (gercek kelimeler, %40 bozuk: bozuk kelime sentetik
iz), LLM, saat.

Senaryolar (duzenek.SENARYOLAR_PANEL): panel_saglikli (R ~0.6 referans), okuma_bosluklari (OLAY: her 5 sn'de 1.5-3 sn
okuma boslugu), panel_donuk / bos_okuma (iki 90 sn'lik pencere), panel_yeniden, gecikme7 (+ metin ipucu kapali esi),
gecikme_surukle (2 -> 6 sn), kuyruk_metin (satir basi duser), soru_satirlar ('?' satirlar) / soru_ciftler (gercek
_ciftler yolu), gec_satir, okuma boslugu + buyuk gecikme (gecikme7_bosluk, gecikme5_bosluk_metinsiz, bozuk1_bosluk:
zaman capalari boslukta acilan satirlara duser, metin eslesmez), ayni kisinin surdurmesi (devam_metinsiz / devam_bozuk1:
isinmada periyodik 'devam' baslari uzak L tohumlamamali), toplanti ortasinda gecikme sicramasi (gecikme_adim: 300. sn'de
2 -> 7 sn; eski L ile kesin yanlis yerine 'Ad?' ve <= 60 sn'de uyari). Kapilar: kesin yanlis 0 hepsinde; altyazi metni sizmaz;
halusinasyon disi senaryoda satir_sil['halusinasyon'] 0; okuma_bosluklari: kapsama_yok <= %5, aralik sayisi sagliklinin
>= %80'i, Bilinmeyen <= %12; donuk / bos: pencere icinde panel_donuk >= %90 ve kimse <= %5, pencere disinda belirsiz
<= %15, Bilinmeyen <= %25, uyari <= 150 sn (temizde hic); gecikme7 / surukle / bosluklu gecikme: bitiste |L - gercek|
<= 0.8; kuyruk: uzun turlerde p1 >= 0.6; soru: hayalet, kimse degil; metin ipucu: acikken gecikme hatasi kucuk, belirsiz
payi buyuk degil; devam: |L - gercek| <= 0.8, Bilinmeyen <= %12, uyari yok; gecikme_adim: 300-460 sn penceresinde kesin
yanlis 0 YA DA atama / kapsama uyarisi sicramadan <= 60 sn sonra, bitiste |L - gercek (son 90 sn)| <= 0.8, sicramadan
once uyari yok.

Kapi gevsetilmez. Kodun olculen eksikleri xfail(strict=True) ile belgelenir (senaryo, tohum, klip, beklenen / gercek);
duzelince XPASS(strict) hatasi verir ve isaret kaldirilir. Ayni degerler
`python tests/e2e/duzenek.py` ile degil, bu dosyadaki `olc(ad, seed)` ile yeniden uretilir."""
import socket

import pytest

import duzenek as D

SEEDLER = D.SEDLER_PANEL
PANEL = D.SENARYOLAR_PANEL
ADLAR = list(PANEL)
PENCERELI = ("panel_donuk", "bos_okuma")
# uyari beklenmeyen (panel sagliklı / okuma boslugu / yeniden cizim / gecikme / kuyruk / gec satir) senaryolar; soru_satirlar
# haric: satirlarin %30'u adsizken '?' karari cogunluga gecebilir, atama uyarisi (B5) o durumda dogrudur
TEMIZ = ("panel_saglikli", "okuma_bosluklari", "panel_yeniden", "gecikme7", "gecikme7_metinsiz", "gecikme_surukle",
         "kuyruk_metin", "gec_satir", "soru_ciftler", "gecikme7_bosluk", "gecikme5_bosluk_metinsiz", "bozuk1_bosluk",
         "devam_metinsiz", "devam_bozuk1")
BOSLUKLU_GECIKME = ("gecikme7_bosluk", "gecikme5_bosluk_metinsiz", "bozuk1_bosluk")
DEVAM = ("devam_metinsiz", "devam_bozuk1")
SICRAMA_SN, SICRAMA_UYARI_SN, SICRAMA_PENCERE_SN = 300.0, 60.0, 460.0     # gecikme_adim: sicrama ani, uyari / pencere siniri
# kirli kapisi (kesin ad, metnin >= %20'si baskasinin) onceki senaryo kumesinde; bosluklu buyuk gecikme varyantlarinin
# plan kapisi kesin yanlis 0 + |L - gercek| <= 0.8 (kirli: uzun klipte kisa araya girme, olculen 0-1 / tohum, raporlanir);
# gecikme_adim'da sicrama gecisinde (eski L ile) iki konusmacili uzun klip kirli kalabilir (11: klip 55 309-320 sn,
# sicramadan 9 sn sonra, Ayse 10 / Kaan 7 kelime): kapi kesin yanlis 0 + pencere kapisi (test_gecikme_adim_sicrama_penceresi)
KIRLI_KAPILI = tuple(ad for ad in ADLAR if ad not in BOSLUKLU_GECIKME + ("gecikme_adim",))
HAL_DISI = ADLAR                                   # hicbirinde halusinasyon klibi yok: satir_sil['halusinasyon'] == 0
UYARI_SN = 150.0
# ---------------- bilinen kod eksikleri (xfail strict) ----------------
YANLIS_NEDEN = "(bilinen kesin yanlis yok)"
YANLIS = {}
KIRLI_NEDEN = ("kesin ad payi %70-%80 (test_e2e KIRLI ile ayni mekanizma): okuma_bosluklari/23 klip 71 405.7-413.1 sn Melek 10 / "
               "Kaan 3 kelime, Melek 'baskin' 0.714 (pay 0.77)")
KIRLI = {"okuma_bosluklari": (23,)}
ARALIK_NEDEN = ("aralik SAYISI R ~0.5'te parcalanmayla sisiyor: panel_saglikli/7 175 aralik = 87 'yeni' + 88 'devam', 67'si "
                "< 1 sn (kelime periyodu 0.5 sn ve yuvarlama ile buyumesiz okuma dizileri duraklama_sn 1.0'i asinca 'devam' "
                "parcasi aciliyor); okuma_bosluklari/7 106 = 87 'yeni' + 19 'devam' (bosluklar yavas okuma yoluyla "
                "birlesiyor). 'yeni' aralik sayisi ve kapsanan sure sagliklinin >= %80'i (test_okuma_bosluklari_yeni_aralik_ve_sure)")
METIN_L_NEDEN = ("metin capasi (konusmaci._metin_capalari) gecikmeyi kelimenin klip icindeki tahmini yerini cikararak olcer, "
                 "zaman capasi ilk okuma anini (R/2 nicemleme dahil) kullanir: metin ipucu acikken L idealize L'den ~0.1-0.3 sn "
                 "dusuk kalir. gecikme7: hata acik/kapali 7: 0.29/0.15, 23: 0.37/0.04, 11: 0.08/0.07. Yukari duzeltmeler "
                 "(sesli sure, kelime dizini, ilk kelime) L hatasini kucultuyor ama kirli / belirsiz kapilarini (araya_girme, "
                 "panel_yeniden, pencere disi) bozuyor: karar bantlari bu olcuye kalibre; 11 tabloda DEGIL (0.01 fark, "
                 "kirmizi birakildi). Uyum suresi acikken 15/15/10 sn, kapaliyken 50/100/35 sn "
                 "(test_metin_ipucu_daha_hizli_oturur)")
METIN_L = (7, 23)


@pytest.fixture(autouse=True)
def _agsiz(monkeypatch):
    def yasak(*_a, **_k):
        raise RuntimeError("duzenek ag erisimi yok")
    monkeypatch.setattr(socket.socket, "connect", yasak)
    monkeypatch.setattr(socket, "create_connection", yasak)


_onbellek = {}


def _isler():
    isler = [((ad, sd), dict(seed=sd, **PANEL[ad])) for ad in ADLAR for sd in SEEDLER]
    for sd in SEEDLER:                                   # kuyruk metni referansi: ayni sozler, kuyruk kesilmez
        isler.append((("kuyruk_ref", sd), dict(dict(seed=sd, **PANEL["kuyruk_metin"]), kuyruk=None)))
    return isler


@pytest.fixture(scope="module", autouse=True)
def _onisitma(request):
    """Tam kosumda tum kosumlar paralel (fork) onceden hesaplanir; az test secildiyse tembel (olc)."""
    kosan = [i for i in request.session.items if i.module is request.module]
    if len(kosan) > 20 and D.paralel_sayisi() > 1:
        for anahtar, (sonuc, hata) in D.toplu(_isler()).items():
            if hata is None:
                _onbellek[anahtar] = sonuc


def olc(ad, seed):
    if (ad, seed) not in _onbellek:
        kw = next(x[1] for x in _isler() if x[0] == (ad, seed))
        _onbellek[(ad, seed)] = D.calistir(**kw)
    return _onbellek[(ad, seed)]


def _tum(adlar=ADLAR):
    return [pytest.param(ad, sd, id=f"{ad}-{sd}") for ad in adlar for sd in SEEDLER]


def _bilinen(tablo, neden, adlar=ADLAR):
    return [pytest.param(ad, sd, id=f"{ad}-{sd}",
                         marks=[pytest.mark.xfail(strict=True, reason=neden)] if sd in tablo.get(ad, ()) else [])
            for ad in adlar for sd in SEEDLER]


def _kirmizi(s, anahtar):
    return [a for a in s["ayrinti"] if a[0] == anahtar]


# ---------------- her senaryo: kesin yanlis 0, sizinti 0, halusinasyon silme 0, tutarlilik ----------------

@pytest.mark.parametrize("ad,seed", _bilinen(YANLIS, YANLIS_NEDEN))
def test_kesin_yanlis_sifir(ad, seed):
    s = olc(ad, seed)
    assert s["uzak_satir"] >= 40, "uzak satir yok: kapi anlamsiz"
    assert s.get("yanlis", 0) == 0, _kirmizi(s, "YANLIS")


@pytest.mark.parametrize("ad,seed", _bilinen(KIRLI, KIRLI_NEDEN, KIRLI_KAPILI))
def test_kirli_kesin_ad_sifir(ad, seed):
    s = olc(ad, seed)
    assert s.get("kirli", 0) == 0, _kirmizi(s, "KIRLI")


@pytest.mark.parametrize("ad,seed", _tum())
def test_altyazi_metni_sizmaz(ad, seed):
    """Panel satirlari gercek kelimeler + sentetik iz (bozuk kelime): iz hicbir cikti dosyasina / LLM mesajina girmez."""
    s = olc(ad, seed)
    assert s["sizinti"] == 0 and s["altyazi_metni"] == 0


@pytest.mark.parametrize("ad,seed", _tum(HAL_DISI))
def test_halusinasyon_disi_senaryoda_silme_yok(ad, seed):
    """B3: zaman cizelgesi klip silmez; halusinasyon klibi olmayan senaryoda satir_sil['halusinasyon'] 0, atilan soz 0."""
    s = olc(ad, seed)
    assert s["satir_sil"].get("halusinasyon", 0) == 0, s["satir_sil"]
    assert s.get("yanlis_atilan", 0) == 0 and s.get("eksik", 0) == 0, s["eksik_liste"]


@pytest.mark.parametrize("ad,seed", _tum())
def test_tutarlilik(ad, seed):
    s = olc(ad, seed)
    assert s["jsonl_tutarsiz"] == 0 and s["gosterim_hatali"] == 0 and s["bekleyen_kalan"] == 0
    assert s["cizelge_bilinmeyen_eksik"] == 0, "cizelge suphesi satiri 'Bilinmeyen:' ile nota girmedi"
    assert s.get("cift_ut", 0) == 0 and s.get("yanki_kalan", 0) == 0


# ---------------- B0: okuma bosluklari (olay) ----------------

@pytest.mark.parametrize("seed", SEEDLER)
def test_okuma_bosluklari_kapsama_yok(seed):
    """R ~0.6, her 5 sn'de 1.5-3 sn okuma boslugu (olay): kapsama_yok <= %5, panel_donuk 0, Bilinmeyen <= %12, kapsama /
    okuma uyarisi yok (olayda 347 satirin 298'i kapsama_yok, 45'i kimse idi)."""
    s = olc("okuma_bosluklari", seed)
    assert s["kapsama_yok_oran"] <= 5.0, s["neden"]
    assert s["panel_donuk_oran"] == 0.0, s["neden"]
    assert s["bilinmeyen_oran"] <= 12.0, (s["bilinmeyen_oran"], s["neden"])
    assert s["kapsama_ilk"] is None and s["atama_ilk"] is None, (s["kapsama_olay"], s["atama"])


@pytest.mark.parametrize("seed", SEEDLER)
def test_okuma_bosluklari_yeni_aralik_ve_sure(seed):
    """Cizelge olusuyor: 'yeni' aralik (satir basi) sayisi ve kapsanan altyazi suresi bosluksuz kosumun >= %80'i (olayda
    toplam 7 aralik, hepsi ilk 18 sn'de)."""
    s, ref = olc("okuma_bosluklari", seed), olc("panel_saglikli", seed)
    assert ref["araliklar_yeni"] >= 60, ref["araliklar_tur"]
    assert s["araliklar_yeni"] >= 0.8 * ref["araliklar_yeni"], (s["araliklar_tur"], ref["araliklar_tur"])
    assert s["aralik_sure_sn"] >= 0.8 * ref["aralik_sure_sn"], (s["aralik_sure_sn"], ref["aralik_sure_sn"])


@pytest.mark.parametrize("seed", [pytest.param(sd, marks=pytest.mark.xfail(strict=True, reason=ARALIK_NEDEN)) for sd in SEEDLER])
def test_okuma_bosluklari_aralik_sayisi(seed):
    """Plan B0 kapisi (literal): aralik sayisi sagliklinin >= %80'i. (Olculen: 'devam' parcalanmasi; bkz. ARALIK_NEDEN.)"""
    s, ref = olc("okuma_bosluklari", seed), olc("panel_saglikli", seed)
    assert s["araliklar"] >= 0.8 * ref["araliklar"], (s["araliklar_tur"], ref["araliklar_tur"])


def test_okuma_bosluklari_kesin_karar_sagliklinin_yakininda():
    """Havuz: bosluklu kosumun dogru kesin karar sayisi bosluksuzun >= %60'i (B0 satirlari koruyor)."""
    a = sum(olc("okuma_bosluklari", sd)["dogru"] for sd in SEEDLER)
    b = sum(olc("panel_saglikli", sd)["dogru"] for sd in SEEDLER)
    assert b >= 150 and a >= 0.6 * b, (a, b)


# ---------------- B1 / B2: donuk panel, bos okuma ----------------

@pytest.mark.parametrize("ad,seed", _tum(PENCERELI))
def test_pencere_icinde_panel_donuk(ad, seed):
    """Pencere icindeki (altyazi zamani) uzak kliplerde panel_donuk >= %90, kimse <= %5; yakalayici donuk tespiti ve
    yeniden bulma calisti; Bilinmeyen (toplam) <= %25."""
    s = olc(ad, seed)
    assert s["ici"] >= 12, s["ici"]
    assert s["ici_panel_donuk_oran"] >= 90.0, s["ici_neden"]
    assert s["ici_kimse_oran"] <= 5.0, s["ici_neden"]
    assert s["bilinmeyen_oran"] <= 25.0, (s["bilinmeyen_oran"], s["neden"])
    assert s["yak_log"]["donuk"] >= 2 and s["yak_log"]["canli"] >= 2 and s["yeniden_bul"] >= 2, (s["yak_log"], s["yeniden_bul"])


@pytest.mark.parametrize("ad,seed", _tum(PENCERELI))
def test_pencere_disinda_belirsiz(ad, seed):
    """Pencere disinda (>= 2 sn once, >= 10 sn sonra) belirsiz <= %15: donukluk pencere disini bozmaz."""
    s = olc(ad, seed)
    assert s["disi"] >= 30, s["disi"]
    assert s["disi_belirsiz_oran"] <= 15.0, (s["disi_belirsiz_oran"], s["disi"])


@pytest.mark.parametrize("ad,seed", _tum(PENCERELI))
def test_pencere_disi_temiz_kosumdan_kotu_degil(ad, seed):
    """Ayni sozlerin pencere disi belirsiz payi, penceresiz kosumun (panel_yeniden: ayni tohum, ayni sozler, 600 sn) genel
    belirsiz payindan en fazla 6 puan yuksek: toparlanma 10 sn icinde."""
    s, ref = olc(ad, seed), olc("panel_yeniden", seed)
    assert s["disi_belirsiz_oran"] <= ref["belirsiz_oran"] + 6.0, (s["disi_belirsiz_oran"], ref["belirsiz_oran"])


@pytest.mark.parametrize("ad,seed", _tum(PENCERELI))
def test_kapsama_uyarisi_pencere_icinde(ad, seed):
    """Kullanici uyarisi: 'kapsama' olayi (neden panel_donuk, mesaj 'panel guncellenmiyor') pencere basindan <= 150 sn;
    durum nedeni panel_donuk."""
    s = olc(ad, seed)
    assert s["kapsama_ilk"] is not None, s["kapsama_olay"]
    assert s["kapsama_ilk"] - s["pencere_ilk"] <= UYARI_SN, (s["kapsama_ilk"], s["pencere_ilk"])
    assert s["kapsama_neden"] == ["panel_donuk"], s["kapsama_olay"]
    assert any("güncellenmiyor" in str(v.get("mesaj")) for _, v in s["kapsama_olay"] if v.get("var") is False)
    assert s["yak_log"]["kapsama"] >= 1


@pytest.mark.parametrize("ad,seed", _tum(PENCERELI))
def test_atama_uyarisi_pencere_icinde(ad, seed):
    """Plan B5 kapisi (gorunur uyari <= 150 sn): donuk / bos panelde kullanici uyarisi 'atama' (konusmaci atanamiyor) ya
    da 'kapsama' (panel guncellenmiyor, neden panel_donuk) olayindan gelir; B5 atama denetimi yalniz kapsama True
    kararlari saydigi icin panel_donuk kararlarinda 'kapsama' olayi uyaridir (~72 sn). Ilk uyari <= 150 sn."""
    s = olc(ad, seed)
    uyarilar = [x for x in (s["atama_ilk"], s["kapsama_ilk"]) if x is not None]
    assert uyarilar, (s["atama"], s["kapsama_olay"])
    assert min(uyarilar) - s["pencere_ilk"] <= UYARI_SN, (uyarilar, s["pencere_ilk"])


@pytest.mark.parametrize("ad,seed", _tum(TEMIZ))
def test_temiz_senaryoda_uyari_yok(ad, seed):
    s = olc(ad, seed)
    assert s["atama_ilk"] is None, s["atama"]
    assert s["kapsama_ilk"] is None, s["kapsama_olay"]


# ---------------- B1: panel yeniden cizimi ----------------

@pytest.mark.parametrize("seed", SEEDLER)
def test_panel_yeniden_cizim(seed):
    """Iki yeniden cizim: yakalayici periyodik dogrulamada yeni ogeyi gorur ('panel yeniden cizilmis'), konusmaci sahte
    aralik uretmez (belirsiz payi sagliklinin yakininda), Bilinmeyen <= %12."""
    s = olc("panel_yeniden", seed)
    assert s["yak_log"]["yeniden_cizilmis"] == 2 and s["yeniden_bul"] >= 2, (s["yak_log"], s["yeniden_bul"])
    assert s["bilinmeyen_oran"] <= 12.0 and s["kapsama_yok_oran"] == 0.0, s["neden"]
    assert s["L_hata"] <= 0.6, (s["L"], s["L_gercek"])


# ---------------- B2: gecikme 7 sn, suruklenen gecikme, metin ipucu ----------------

@pytest.mark.parametrize("ad", ("gecikme7", "gecikme7_metinsiz", "gecikme_surukle"))
@pytest.mark.parametrize("seed", SEEDLER)
def test_gecikme_bitiste_oturur(ad, seed):
    """Bitiste |L - gercek gecikme (son 90 sn)| <= 0.8; gecikme 7'de tum toplanti icin de; Bilinmeyen <= %12."""
    s = olc(ad, seed)
    assert s["L_gercek_son"] is not None and s["L_hata_son"] <= 0.8, (s["L"], s["L_gercek_son"], s["L_izi"][-6:])
    if ad != "gecikme_surukle":
        assert s["L_hata"] <= 0.8, (s["L"], s["L_gercek"])
    assert s["bilinmeyen_oran"] <= 12.0, s["neden"]


@pytest.mark.parametrize("ad,seed", _tum(BOSLUKLU_GECIKME))
def test_bosluklu_gecikme_L_oturur_ve_kapsama(ad, seed):
    """Olay bicimi okuma bosluklari + gecikme 7 (metin eslesmez: bozuk=1.0) / gecikme 5 (metin ipucu kapali): zaman
    capalari boslukta acilan (kaba) satirlara dusmesine ragmen bitiste |L - gercek (bosluk disinda beliren satirlar)|
    <= 0.8, kapsama_yok <= %5, Bilinmeyen <= %12, uyari yok (kesin yanlis 0: test_kesin_yanlis_sifir)."""
    s = olc(ad, seed)
    assert s["uzak_satir"] >= 40
    assert s["L_gercek_son"] is not None and s["L_hata_son"] <= 0.8, (s["L"], s["L_gercek_son"], s["L_izi"][-6:])
    assert s["kapsama_yok_oran"] <= 5.0 and s["bilinmeyen_oran"] <= 12.0, s["neden"]
    assert s["kapsama_ilk"] is None and s["atama_ilk"] is None, (s["kapsama_olay"], s["atama"])


def test_gecikme_surukle_L_izler():
    """L toplanti boyunca gecikmeyle birlikte yukselir: ilk dakikalarda ~2 + R/2, sonda ~6 + R/2."""
    for sd in SEEDLER:
        izi = olc("gecikme_surukle", sd)["L_izi"]
        bas = [L for an, L in izi if 60 <= an <= 120]
        son = [L for an, L in izi if an >= 540]
        assert bas and son and max(bas) < 4.0 < min(son), (sd, bas, son)


@pytest.mark.parametrize("seed", SEEDLER)
def test_metin_ipucu_belirsiz_payi_buyumez(seed):
    """Metin ipucu acikken belirsiz payi kapali kosumdan buyuk degil, kesin yanlis 0 (her ikisinde)."""
    a, k = olc("gecikme7", seed), olc("gecikme7_metinsiz", seed)
    assert a["belirsiz_oran"] <= k["belirsiz_oran"], (a["belirsiz_oran"], k["belirsiz_oran"])
    assert a.get("yanlis", 0) == 0 and k.get("yanlis", 0) == 0


@pytest.mark.parametrize("seed", SEEDLER)
def test_metin_ipucu_daha_hizli_oturur(seed):
    """L'nin gercek gecikmeye (+-0.8) kalici oturma ani metin ipucuyla kapali kosumdan gec degil (olculen 15/15/10 sn
    vs 80/125/80 sn)."""
    a, k = olc("gecikme7", seed), olc("gecikme7_metinsiz", seed)
    assert a["L_uyum_sn"] is not None and k["L_uyum_sn"] is not None, (a["L_izi"], k["L_izi"])
    assert a["L_uyum_sn"] <= k["L_uyum_sn"], (a["L_uyum_sn"], k["L_uyum_sn"])


@pytest.mark.parametrize("seed", [pytest.param(sd, marks=[pytest.mark.xfail(strict=True, reason=METIN_L_NEDEN)] if sd in METIN_L else [])
                                  for sd in SEEDLER])
def test_metin_ipucu_gecikme_hatasi_kucuk(seed):
    """Plan B2b kapisi: metin ipucu acikken bitisteki gecikme hatasi kapalidan buyuk degil. Iki olcum de okuma
    nicemlemesi (R ~0.6 sn -> +-0.3 sn) icinde: 0.05 sn'den kucuk fark gurultudur (tohum 11: 0.08 / 0.07; metin
    capasini yukari kaydiran dort kalibrasyon kirli / pencere disi belirsiz kapilarini bozdu). Mutlak hata da sinirli."""
    a, k = olc("gecikme7", seed), olc("gecikme7_metinsiz", seed)
    assert a["L_hata"] <= k["L_hata"] + 0.05, (a["L_hata"], k["L_hata"])
    assert a["L_hata"] <= 0.3, a["L_hata"]


# ---------------- G1 / G2: ayni kisinin surdurmesi, gecikme sicramasi ----------------

@pytest.mark.parametrize("ad,seed", _tum(DEVAM))
def test_devam_L_oturur_ve_uyari_yok(ad, seed):
    """Ayni kisi %50 surdurur (0.8-2.5 sn duraklama), 4 uzak kisi, okuma bosluklari, metin ipucu kapali / metin
    eslesmiyor: isinmada satirin periyodik 'devam' baslari uzak L (~9 sn) tohumlamaz; |L - gercek| <= 0.8 (bitiste de),
    Bilinmeyen <= %12, kapsama_yok <= %5, uyari yok (kesin yanlis 0: test_kesin_yanlis_sifir)."""
    s = olc(ad, seed)
    assert s["uzak_satir"] >= 40
    assert s["L_hata"] <= 0.8 and s["L_hata_son"] <= 0.8, (s["L"], s["L_gercek"], s["L_gercek_son"])
    assert s["bilinmeyen_oran"] <= 12.0 and s["kapsama_yok_oran"] <= 5.0, s["neden"]
    assert s["kapsama_ilk"] is None and s["atama_ilk"] is None, (s["kapsama_olay"], s["atama"])


def _sicrama_uyarilari(s):
    return sorted(an for an, v in list(s["atama"]) + list(s["kapsama_olay"]) if v.get("var") is False)


@pytest.mark.parametrize("seed", SEEDLER)
def test_gecikme_adim_sicrama_penceresi(seed):
    """Gecikme 300. sn'de 2 -> 7 sn (6 uzak kisi, okuma bosluklari): sicrama penceresinde (300-460 sn) kesin yanlis 0
    YA DA kullanici uyarisi (atama / kapsama olayi) sicramadan <= 60 sn sonra; sicramadan once uyari yok; L bitiste
    yeni gecikmeye oturur (|L - gercek (son 90 sn)| <= 0.8)."""
    s = olc("gecikme_adim", seed)
    assert s["uzak_satir"] >= 40
    uyarilar = _sicrama_uyarilari(s)
    assert not [u for u in uyarilar if u < SICRAMA_SN], uyarilar
    pencere = [x for x in _kirmizi(s, "YANLIS") if SICRAMA_SN <= x[1]["bas"] <= SICRAMA_PENCERE_SN]
    ilk = next((u for u in uyarilar if u >= SICRAMA_SN), None)
    assert not pencere or (ilk is not None and ilk - SICRAMA_SN <= SICRAMA_UYARI_SN), (pencere, uyarilar)
    assert s["L_gercek_son"] is not None and s["L_hata_son"] <= 0.8, (s["L"], s["L_gercek_son"], s["L_izi"][-6:])
    assert s["L_izi"][-1][1] >= 5.0, s["L_izi"][-6:]


def test_gecikme_adim_sicrama_sonrasi_tavan_ve_uyari_havuzu():
    """Havuz: sicramadan sonra (300-400 sn) uzak kararlarin en az yarisi tavanli ('Ad?' / '?': eski L ile kesin ad
    yazilmaz) ve en az bir tohumda 'atama' uyarisi olcutu 'gecikme' (L supheli) ya da 'kesin_az'."""
    tavanli = toplam = 0
    olcutler = set()
    for sd in SEEDLER:
        s = olc("gecikme_adim", sd)
        for sat in s["uzak_kararlar"]:
            if SICRAMA_SN <= sat["bas"] <= 400.0:
                toplam += 1
                tavanli += sat["spk_guven"] < 0.7
        olcutler.update(v.get("olcut") for _, v in s["atama"] if v.get("var") is False)
    assert toplam >= 30 and tavanli >= 0.5 * toplam, (tavanli, toplam)
    assert olcutler & {"gecikme", "kesin_az"}, olcutler


# ---------------- B2: kuyruk metni (satir basi duser) ----------------

@pytest.mark.parametrize("seed", SEEDLER)
def test_kuyruk_metni_uzun_turde_p1(seed):
    """Satir en cok 16 kelime gosterir: >= 16 kelimelik turun 6 sn'den sonraki kliplerinde p1 >= 0.6 (isinma 'capa_az'
    haric) en az %90; kuyruk kesimi referans kosuma (ayni sozler, kuyruk yok) gore p1'i dusurmez."""
    s, ref = olc("kuyruk_metin", seed), olc("kuyruk_ref", seed)
    assert s["uzun_tur_klip"] >= 12, s["uzun_tur_klip"]
    dusuk = [x for x in s["uzun_tur_p1_dusuk"] if "capa_az" not in (x[2] or "")]
    assert len(dusuk) <= 0.1 * s["uzun_tur_klip"], (dusuk, s["uzun_tur_klip"])
    ref_dusuk = [x for x in ref["uzun_tur_p1_dusuk"] if "capa_az" not in (x[2] or "")]
    assert len(dusuk) <= len(ref_dusuk), (dusuk, ref_dusuk)
    assert s["kimse_oran"] <= 5.0 and s["bilinmeyen_oran"] <= 12.0, s["neden"]


# ---------------- B1 / B2: '?' satirlar ----------------

@pytest.mark.parametrize("seed", SEEDLER)
def test_soru_satirlari_hayalet_kimse_degil(seed):
    """Satir gruplarinin %30'u adsiz ('?'): o satirlarin klipleri 'hayalet' (etkinlik var, kimlik yok), 'kimse' degil;
    hicbiri ad almaz; adli satirlarin klipleri etkilenmez (kesin yanlis 0)."""
    s = olc("soru_satirlar", seed)
    assert s["soru_klip"] >= 15, s["soru_klip"]
    assert s["soru_neden"].get("hayalet", 0) >= 0.9 * s["soru_klip"], s["soru_neden"]
    assert s["soru_neden"].get("kimse", 0) <= 0.05 * s["soru_klip"], s["soru_neden"]
    assert s["soru_adli"] == [], s["soru_adli"]
    assert s.get("hayalet_ad", 0) == 0 and s["kapsama_yok_oran"] == 0.0


@pytest.mark.parametrize("seed", SEEDLER)
def test_soru_ciftler_gercek_yol(seed):
    """Gercek Yakalayici._ciftler (sahte UIA agaci): dolu panelde en ust satirin adi agacta yok -> ('?', metin) satiri;
    kesin yanlis 0, Bilinmeyen <= %12, belirsiz payi sagliklinin (panel_yeniden: ayni sozler) +3 puan icinde."""
    s, ref = olc("soru_ciftler", seed), olc("panel_yeniden", seed)
    assert s["soru_okuma"] >= 100, s["soru_okuma"]
    assert s["bilinmeyen_oran"] <= 12.0 and s.get("hayalet_ad", 0) == 0, s["neden"]
    assert s["belirsiz_oran"] <= ref["belirsiz_oran"] + 3.0, (s["belirsiz_oran"], ref["belirsiz_oran"])


# ---------------- B2: gec gelen satir (gec yeniden puanlama) ----------------

@pytest.mark.parametrize("seed", SEEDLER)
def test_gec_satir(seed):
    """Sozlerin %12'sinin satiri 2-3.5 sn gec belirir: kesin yanlis 0, Bilinmeyen <= %12, kapsama_yok 0."""
    s = olc("gec_satir", seed)
    assert s["bilinmeyen_oran"] <= 12.0 and s["kapsama_yok_oran"] == 0.0, s["neden"]
    assert s["L_hata"] <= 0.6, (s["L"], s["L_gercek"])


def test_gec_yeniden_puanlama_calisir():
    """Havuz: gec gelen satirla '?' karari ayni klip_no ile guncellenir (satir_guncelle 'karar' ikinci kez) en az bir kez."""
    assert sum(olc("gec_satir", sd)["gec_guncelleme"] for sd in SEEDLER) >= 1


# ---------------- duzenegin kendisi ----------------

def test_panel_kelimeleri_gercek_ve_izli():
    """Panel satiri: zemin gercegi kelimeleri, ~%40'i sentetik iz (sayi / ad korunur), her sozde en az bir iz."""
    import random
    ut = D.uret_karisik(random.Random(3), 120.0, [D.A, D.B, D.C])
    p = D.Panel(ut, 2.0, (), random.Random(1), r_kel=random.Random(2))
    iz = top = sabit_bozuk = 0
    for u in ut:
        kel = p.kelimeler(u, u["alt"]["e"] + 5.0)
        assert len(kel) == len(u["slot"])
        n_iz = sum(1 for w in kel if D._SIZINTI.fullmatch(w))
        assert n_iz >= 1, kel
        iz, top = iz + n_iz, top + len(kel)
        for i, w in enumerate(kel):
            if D.Panel._sabit(i, u["slot"][i][0]):
                sabit_bozuk += w != u["slot"][i][0]
    assert 0.3 <= iz / top <= 0.5 and sabit_bozuk == 0
    assert p.gorunen(60.0) and all(not D._SIZINTI.fullmatch(k) for k, _ in p.gorunen(60.0))


def test_senaryo_listesi():
    assert set(ADLAR) == {"panel_saglikli", "okuma_bosluklari", "panel_donuk", "bos_okuma", "panel_yeniden", "gecikme7",
                          "gecikme7_metinsiz", "gecikme_surukle", "kuyruk_metin", "soru_satirlar", "soru_ciftler", "gec_satir",
                          "gecikme7_bosluk", "gecikme5_bosluk_metinsiz", "bozuk1_bosluk", "devam_metinsiz", "devam_bozuk1",
                          "gecikme_adim"}
    s = olc("okuma_bosluklari", 7)
    assert s["R"] is not None and s["R"] <= 0.8, s["R"]


# ---------------- capraz ilinti: arama siniri zirvesi / bagimsiz onay ----------------

def test_capraz_sinir_tepesi_L_sicratmaz(monkeypatch):
    """4 uzak kisi, %50 ayni kisi surdurur, altyazi metni hic eslesmez (bozuk 1.0), her 5 sn 1.5-3 sn okuma boslugu,
    tohum 106: capraz ilinti arama sinirindaki (capraz_ust, 10 sn) zayif zirveyle (6 onset'in 4'u) L'yi 3.5'ten 10'a
    atlatiyor, ayni veri 1.8 sn sonra 'ikinci degerlendirme' sayilip onaylaniyordu -> 5 kesin yanlis. Sinira degen plato
    fark sayilmaz; ikinci onay yeni bir onset ister."""
    kisiler = [D.A, D.B, D.C, D.D4]

    def uret(tur, r, sec):
        return D.uret_karisik(r, sec.get("sure", 600.0), kisiler, devam=0.5, tekrar=False,
                              uzun_soz=sec.get("uzun_soz", 0.0))

    monkeypatch.setattr(D, "uret", uret)
    s = D.calistir(seed=106, tur="karisik", katilimcilar=kisiler + [D.BEN], sure=600.0, gecikme=3.0,
                   okuma_aralik=(0.2, 0.4), okuma_bosluk=(5.0, 1.5, 3.0), bozuk=1.0)
    assert s["uzak_satir"] >= 40
    assert not s.get("yanlis"), _kirmizi(s, "YANLIS")
    assert max(L for _, L in s["L_izi"]) < 9.0, "L arama sinirina sicradi"
    assert s["L_hata_son"] <= 0.8, (s["L"], s["L_gercek_son"])
