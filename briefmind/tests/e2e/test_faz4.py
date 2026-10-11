"""Faz 4 uctan uca kabul kapilari (MIMARI_v3 §9 Faz 4, §6): not uretimine konusmaci guveninin etkisi. Sentetik, ag yok,
repo'ya dosya yazmaz. Gercek: Motor (parcalama, duzeltme, ozet, birlestirme, sahip dogrulama, dayanak kontrolu, tarih),
Konusmaci + SesServisi (gercek 'Ad?', 'A → B', 'A / B' ⟨⟩ satirlari), not_araclari (e-posta, Jira, konusma payi).
Sahte: STT metni, Teams paneli, saat ve KARSIT DAVRANISLI LLM (duzenek.DusmanLLM): MAP_SISTEM / GENEL_SISTEM kurallarini
bilerek cignier -- 'Ad?', 'Bilinmeyen', 'A → B', 'A / B' ve ⟨⟩ icindeki cumlelerden sahip adlandirir, ⟨⟩ icinden karar / tutar
cikarir, 'belirsiz' sorumluyu 'liste' asamasinda doldurur, ozete ⟨⟩ tutarini kopyalar, 'sonraki adim'a belirsiz kaynakli isi
sahibiyle yazar. Her soz benzersiz kayit numarasi tasir: not maddesi -> soz eslemesi kodla (benzerlik tahmini yok).

Kapilar: dusuk guvenli kaynak (Ad?, Bilinmeyen) -> 0 kesin sahip; kesin kaynak + 'ben ...' / 'Ad, sen ...' -> dogru kesin sahip
(hic eksik yok); 'A / B' ve 'A → B' -> 0 sahip; ⟨⟩ satirindan karar / aksiyon / tutar yok; 170+ madde LLM gruplariyla birlesir
(sessiz yedek yok); e-posta / Jira yalniz kesin sahip; konusma payi belirsiz etiketleri saymaz. Negatif kontrol
(not_korumasiz: sahip dogrulama + dayanak kapali) ayni olcumlerde ihlal uretir -- fixture'in isirdigi kanitlanir.
Kapi gevsetilmez; kodun bilinen eksigi xfail(strict=True) ile belgelenir (duzelince XPASS(strict) hatasi verir)."""
import re
import socket

import pytest

import duzenek as D
import llm

FAZ4 = D.SENARYOLAR_FAZ4
SEEDLER = D.SEDLER_FAZ4
KORUMALI = [ad for ad in FAZ4 if ad != "not_korumasiz"]
BELIRSIZ_SINIF = ("adayli", "bilinmeyen", "devir", "ayni")        # kaynak satirda konusmaci kesin degil
KATILIMCILAR = {D.A, D.B, D.C, D.BEN}
KARAR_BOLUMLERI = {"Kararlar", "Aksiyonlar", "Açık sorular"}
CAPACITY_KENARI = ("not_cok_uzun", 7)

# ---------------- bilinen kod eksikleri (xfail strict) ----------------
OZET_NEDEN = ("Özet paragrafi ⟨⟩ (A / B ayni anda) satirindaki TUTARI tasiyor: DusmanLLM bolum ozetini (tutar dahil) kopyalar; "
              "motor.dayanak_kontrolu ozet cumlelerini ozet_zemin = _kokler(transkript) ile -- ⟨⟩ DAHIL -- denetler, sayi / tutar "
              "icin ⟨⟩ korumasi yok. Ornek not_temiz/7: '... kayitlari ve 23 madde konusuldu. 667 bin lira tutari da ele alindi' "
              "(167 numarali soz yalniz ⟨⟩ icinde)")
KISIYE_NEDEN = ("'Kişiye göre iş listesi' dayanak_kontrolu'nun denetlemedigi baslik (llm.not_markdown 'aksiyon/karar/soru' "
                "sozcuklerinden bilerek kacinir): Aksiyonlar tablosundan dayanaksiz diye atilan ⟨⟩ maddeleri bu listede "
                "'(aday: Ad)' ile kaliyor. Ornek not_temiz/7: kayit 118, 150, 152, 181 (hepsi yalniz ⟨⟩ icinde)")
SONRAKI_NEDEN = ("'Bir sonraki adım' cumlesi yalniz kok ortusmesiyle (esik 0.34) denetlenir ve katilimci adi transkript "
                 "etiketlerinde gectigi icin ⟨⟩ kaynakli maddeyi SAHIBIYLE tasiyan cumle geciyor. Ornek not_temiz/7: 'Melek Taslak "
                 "... 181 ... işini tamamlayacak.' (181 yalniz 'Melek Taslak / Kaan Uydurma (aynı anda)' ⟨⟩ satirinda)")
SONRAKI_BELIRSIZ_NEDEN = ("'Bir sonraki adım' kaynagi 'Ad?' / 'A → B' / 'Bilinmeyen' olan bir isi SAHIBIYLE yaziyor (genel asama "
                          "listeleri sorumlu (?) isaretiyle alir; kod cumlede sahip adini denetlemez). Ornek not_temiz/7: kayit 206")
CAPACITY_NEDEN = ("Toplam madde tek istek kapasitesini birkac madde asinca (not_cok_uzun/7: 268 madde, kapasite ~262; tur 2 "
                  "gruplari [258, 10]) gruplar kuculmedigi icin dongu birakilir ve TUM liste yerel birlestirmeye duser "
                  "('kademeli birleştirmeye de sığmadı'; LOGLU, sessiz degil). Kalan kucuk grup yeniden dagitilmiyor")


@pytest.fixture(autouse=True)
def _agsiz(monkeypatch):
    def yasak(*_a, **_k):
        raise RuntimeError("duzenek ag erisimi yok")
    monkeypatch.setattr(socket.socket, "connect", yasak)
    monkeypatch.setattr(socket, "create_connection", yasak)


_onbellek = {}


def _isler():
    return [((ad, sd), dict(seed=sd, **FAZ4[ad])) for ad in FAZ4 for sd in SEEDLER[ad]]


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
        _onbellek[(ad, seed)] = D.calistir(seed=seed, **FAZ4[ad])
    return _onbellek[(ad, seed)]


def _kosumlar(adlar=KORUMALI):
    return [pytest.param(ad, sd, id=f"{ad}-{sd}") for ad in adlar for sd in SEEDLER[ad]]


def _hepsi(adlar=KORUMALI):
    return [olc(ad, sd) for ad in adlar for sd in SEEDLER[ad]]


def _iddia(s, tur, sinif, sarmal):
    return s["n_iddia"].get(f"{tur}:{sinif}:{'sarmal' if sarmal else 'acik'}", 0)


def _sizinti(s, bolumler):
    return [(x[0], x[1], x[3].get(b)) for x in s["n_sarmal_sizinti"] for b in x[2] if b in bolumler]


# ---------------- fixture isirir: karsit LLM gercekten tuzaga cekti ----------------

def test_fixture_karsit_llm_belirsiz_kaynaklardan_sahip_ve_karar_cikardi():
    """Kapilar bos gecmesin: DusmanLLM her belirsiz sinifta sahip adlandirdi, ⟨⟩ icinden karar / aksiyon cikardi."""
    t = _hepsi()
    for sinif, en_az in (("adayli", 15), ("bilinmeyen", 15), ("devir", 4)):
        n = sum(_iddia(s, "aksiyon", sinif, False) for s in t)
        assert n >= en_az, (sinif, n)
    assert sum(_iddia(s, "aksiyon", "ayni", True) for s in t) >= 10, "⟨⟩ icinden aksiyon cikarilmadi"
    assert sum(_iddia(s, "karar", "ayni", True) for s in t) >= 8, "⟨⟩ icinden karar cikarilmadi"
    # kesin kaynakli, adlandirilmis sahip ve 'ben' taahhudu yeterince var
    for ad, sd in ((a, d) for a in KORUMALI for d in SEEDLER[a]):
        s = olc(ad, sd)
        assert s["n_beklenen_tur"].get("ben", 0) >= 5 and s["n_beklenen_tur"].get("hitap", 0) >= 5, (ad, sd, s["n_beklenen_tur"])


def test_negatif_kontrol_korumalar_kapaliyken_ihlaller_olculur():
    """not_korumasiz: sahip dogrulama + dayanak kontrolu kapali. Ayni fixture ihlal uretmeli (kapi gercekten sinaniyor)."""
    k = [olc("not_korumasiz", sd) for sd in SEEDLER["not_korumasiz"]]
    assert sum(len(s["n_kesin_belirsiz_kaynak"]) for s in k) >= 10, "dusuk guvenli kaynak kesin sahip oldu (beklenen: korumasizken evet)"
    assert sum(len(_sizinti(s, {"Kararlar", "Aksiyonlar"})) for s in k) >= 6, "⟨⟩ maddeleri nota girdi (korumasizken evet)"
    assert sum(len(s["n_eposta_hata"]) for s in k) >= 10 and sum(len(s["n_jira_hata"]) for s in k) >= 10
    assert all(s["n_belirsiz_aksiyon"] == 0 for s in k), "korumasizken hicbir sahip belirsiz isaretlenmez"


# ---------------- kapi 1: dusuk guvenli kaynak -> 0 kesin sahip ----------------

@pytest.mark.parametrize("ad,seed", _kosumlar())
def test_dusuk_guvenli_kaynak_sifir_kesin_sahip(ad, seed):
    s = olc(ad, seed)
    assert s["n_kesin_belirsiz_kaynak"] == [], s["n_kesin_belirsiz_kaynak"]
    assert s["n_aksiyon_iliskisiz"] == 0
    for sinif in BELIRSIZ_SINIF + ("oda",):
        c = s["n_sinif_aksiyon"].get(sinif, {})
        assert c.get("kesin", 0) == 0, (sinif, c)
        assert c.get("aksiyon", 0) == c.get("belirsiz", 0) + c.get("yok", 0), (sinif, c)    # 'Ad (?)' ya da 'belirsiz'


@pytest.mark.parametrize("sinif", ("adayli", "bilinmeyen"))
def test_ad_soru_ve_bilinmeyen_kaynak_notta_belirsiz_sahip_olarak_kalir(sinif):
    """'Ad?:' ve 'Bilinmeyen:' kaynakli aksiyonlar notta KALIR (dayanak var) ama sahibi belirsiz: tablo '(?)' / 'belirsiz'."""
    toplam = sum(s["n_sinif_aksiyon"].get(sinif, {}).get("aksiyon", 0) for s in _hepsi())
    belirsiz = sum(s["n_sinif_aksiyon"].get(sinif, {}).get("belirsiz", 0) + s["n_sinif_aksiyon"].get(sinif, {}).get("yok", 0)
                   for s in _hepsi())
    assert toplam >= 15 and belirsiz == toplam, (sinif, toplam, belirsiz)


# ---------------- kapi 2: adlandirilmis sahip / 'ben' -> dogru kesin sahip ----------------

@pytest.mark.parametrize("ad,seed", _kosumlar())
def test_adlandirilmis_sahip_ve_ben_taahhudu_dogru_kesin(ad, seed):
    """Kesin kaynak satirda 'Ad, sen ...' (muhatap) ve 'ben ...' (konusan): her biri DOGRU kisiyle kesin; yanlis kisi yok."""
    s = olc(ad, seed)
    assert s["n_kesin_yanlis"] == [], s["n_kesin_yanlis"]
    assert s["n_kesin_eksik"] == [], f"kesin kaynakli ama kesin sahibi cikmayan: {s['n_kesin_eksik']}"
    assert s["n_beklenen_kesin"] >= 20 and s["n_kesin_dogru"] == s["n_beklenen_kesin"]
    assert s["n_dogru_tur"] == s["n_beklenen_tur"] and s["n_dogru_tur"].get("hitap", 0) >= 5


# ---------------- kapi 3: A / B ve A → B -> 0 sahip ----------------

@pytest.mark.parametrize("sinif,en_az_iddia", (("ayni", 10), ("devir", 4)))
def test_a_b_ve_a_ok_b_kaynakli_aksiyonda_kesin_sahip_yok(sinif, en_az_iddia):
    t = _hepsi()
    sarmal = sinif == "ayni"
    assert sum(_iddia(s, "aksiyon", sinif, sarmal) for s in t) >= en_az_iddia, "fixture: karsit LLM bu siniftan sahip adlandirmadi"
    for s in t:
        c = s["n_sinif_aksiyon"].get(sinif, {})
        assert c.get("kesin", 0) == 0, (sinif, c)
    if sinif == "devir":                            # 'A → B' kaynakli aksiyon notta kalir ama sahibi belirsiz
        assert sum(s["n_sinif_aksiyon"].get("devir", {}).get("belirsiz", 0) for s in t) >= 4


@pytest.mark.parametrize("ad,seed", _kosumlar())
def test_sarmal_satirindan_aksiyon_karar_ve_acik_soru_notta_yok(ad, seed):
    """⟨⟩ (A / B aynı anda) icindeki soz: kayit numarasi ya da tutari Kararlar / Aksiyonlar / Açık sorular'a girmez
    (dayanak_kontrolu ⟨⟩'yi dayanak saymaz); aksiyon tablosunda ⟨⟩ kaynakli satir yok."""
    s = olc(ad, seed)
    assert s["n_sarmal_soz"] >= 1 and s["n_sarmal_karar_soz"] + s["n_sarmal_soz"] >= 2, "fixture: ⟨⟩ icinde kodlu soz yok"
    assert _sizinti(s, KARAR_BOLUMLERI) == [], _sizinti(s, KARAR_BOLUMLERI)
    assert s["n_sinif_aksiyon"].get("ayni", {}).get("aksiyon", 0) == 0


def test_komsu_satirla_kok_paylasan_sarmal_karari_nota_sizmaz():
    """not_komsu_sarmal: ⟨⟩ karari komsu kesin satirla ayni kod adi ve kalibi tasir; kokleri ⟨⟩ disinda karsilanir (yalniz
    kok ortusmesi 'dayanakli' der: fixture isirir), numara / tutar yalniz ⟨⟩ icinde. Kararlar / Aksiyonlar / Açık sorular'a
    girmez (dayanak zorunlu deger denetimi); komsu kesin karar notta kalir."""
    t = [olc("not_komsu_sarmal", sd) for sd in SEEDLER["not_komsu_sarmal"]]
    komsu = sum(len(s["n_komsu_sarmal"]) for s in t)
    assert komsu >= 6, komsu
    assert sum(len(s["n_komsu_kok_dayanakli"]) for s in t) >= komsu // 2, "fixture: komsu kokler dayanak saglamiyor"
    for s in t:
        kodlar = set(s["n_komsu_sarmal"])
        assert [x for x in _sizinti(s, KARAR_BOLUMLERI) if x[0] in kodlar] == []


def test_sarmal_karar_ve_tutar_cogu_kosumda_cikarilmaya_calisildi():
    t = _hepsi()
    assert sum(s["n_sarmal_karar_soz"] for s in t) >= 8, "⟨⟩ icinde tutarli karar sozu yetersiz"


# ---------------- kapi 3b: ⟨⟩ / belirsiz kaynak notun diger bolumlerinde (bilinen eksikler) ----------------

@pytest.mark.xfail(strict=True, reason=OZET_NEDEN)
def test_sarmal_tutari_ozette_yok():
    assert [x for s in _hepsi() for x in _sizinti(s, {"Özet"})] == []


@pytest.mark.xfail(strict=True, reason=KISIYE_NEDEN)
def test_sarmal_maddesi_kisiye_gore_listede_yok():
    assert [x for s in _hepsi() for x in _sizinti(s, {"Kişiye göre iş listesi"})] == []


@pytest.mark.xfail(strict=True, reason=SONRAKI_NEDEN)
def test_sarmal_maddesi_sonraki_adimda_yok():
    assert [x for s in _hepsi() for x in _sizinti(s, {"Bir sonraki adım"})] == []


@pytest.mark.xfail(strict=True, reason=SONRAKI_BELIRSIZ_NEDEN)
def test_belirsiz_kaynakli_is_sonraki_adimda_sahipli_yazilmaz():
    assert [x for s in _hepsi() for x in s["n_sonraki_belirsiz"]] == []


# ---------------- kapi 4: e-posta / Jira yalniz kesin ----------------

@pytest.mark.parametrize("ad,seed", _kosumlar())
def test_eposta_ve_jira_yalniz_kesin_sahip(ad, seed):
    """Zemin gercegi: e-postadaki / Jira'da atanan her madde KESIN kaynakli ve DOGRU kisiye; belirsiz isler atanmaz."""
    s = olc(ad, seed)
    assert s["n_eposta_hata"] == [], s["n_eposta_hata"]
    assert s["n_jira_hata"] == [], s["n_jira_hata"]
    assert len(s["n_eposta_kisi"]) >= 3 and s["n_eposta_madde"] == s["n_kesin_dogru"]
    assert s["n_jira_kayit"] == s["n_aksiyon"], "Jira bir aksiyonu dusurdu"
    assert s["n_jira_atanan"] == s["n_kesin_dogru"], (s["n_jira_atanan"], s["n_kesin_dogru"])
    assert s["n_jira_kayit"] - s["n_jira_atanan"] == s["n_belirsiz_aksiyon"] >= 1, "belirsiz isler atamasiz kalmali"


@pytest.mark.parametrize("ad,seed", _kosumlar())
def test_sahibi_belirsiz_blogu_tum_belirsizleri_ve_yalniz_onlari_icerir(ad, seed):
    s = olc(ad, seed)
    assert s["n_belirsiz_blok_satir"] == s["n_belirsiz_aksiyon"] >= 1
    assert s["n_belirsiz_blok_kesin"] == [] and s["n_belirsiz_blok_eksik"] == []
    assert s["n_aksiyon"] - s["n_belirsiz_aksiyon"] == s["n_kesin_dogru"]


# ---------------- kapi 5: konusma payi belirsiz etiketleri saymaz ----------------

@pytest.mark.parametrize("ad,seed", _kosumlar())
def test_konusma_payi_yalniz_kesin_etiketler(ad, seed):
    s = olc(ad, seed)
    assert set(s["n_pay_ad"]) <= KATILIMCILAR, s["n_pay_ad"]                       # '?', 'Ad?', 'A / B', 'A → B', Bilinmeyen yok
    assert len(s["n_pay_ad"]) >= 3 and 98 <= s["n_pay_toplam"] <= 102
    assert s["n_pay_meta_uyumlu"], "meta.json konusma_paylari / konusma_belirsiz notun hesabiyla ayni degil"
    assert abs(s["n_pay_belirsiz"] - s["n_pay_kelime_belirsiz"]) <= 8, (s["n_pay_belirsiz"], s["n_pay_kelime_belirsiz"])
    assert "konuşmacısı belirsiz %" in s["n_pay_metin"] and s["n_pay_belirsiz"] >= 5
    # mutasyon: en cok konusanin tum satirlari 'Ad?' olursa payi kesinden duser, belirsiz pay buyur
    assert not s["n_pay_mutasyon_var"], s["n_pay_mutasyon_ad"]
    assert s["n_pay_mutasyon_belirsiz"] >= s["n_pay_belirsiz"] + 10


def test_konusma_payi_altyazi_kapaliyken_belirsiz_pay_buyuk():
    """not_kapali: 3 dakikada uzak konusmacilar 'Bilinmeyen' -> belirsiz pay temiz toplantidakinden cok buyuk, adlar yine kesin."""
    kapali = [olc("not_kapali", sd)["n_pay_belirsiz"] for sd in SEEDLER["not_kapali"]]
    temiz = [olc("not_temiz", sd)["n_pay_belirsiz"] for sd in SEEDLER["not_temiz"]]
    assert min(kapali) >= 30 and max(temiz) <= 25 and min(kapali) > max(temiz) + 10, (kapali, temiz)


# ---------------- kapi 6: 170+ madde LLM gruplariyla birlesir, sessiz yedek yok ----------------

def _uzun(kenar_xfail=False, adlar=("not_uzun", "not_cok_uzun")):
    return [pytest.param(ad, sd, id=f"{ad}-{sd}",
                         marks=[pytest.mark.xfail(strict=True, reason=CAPACITY_NEDEN)]
                         if kenar_xfail and (ad, sd) == CAPACITY_KENARI else [])
            for ad in adlar for sd in SEEDLER[ad]]


@pytest.mark.parametrize("ad,seed", _uzun(kenar_xfail=True))
def test_170_arti_madde_llm_gruplariyla_birlesir_yedege_dusmez(ad, seed):
    s = olc(ad, seed)
    girdi = s["n_liste_girdi"]
    assert s["n_liste_yedek"] == [] and s["n_liste_yerel_log"] == [], (s["n_liste_yedek"], s["n_liste_yerel_log"], girdi)
    assert max(girdi) >= 170, f"fixture: en buyuk birlestirme istegi {max(girdi)} madde"
    assert len(girdi) >= 4 and s["n_liste_cagri"] == len(girdi)
    assert s["n_liste_grup_log"], "gruplama Olaylar'a yazilmadi"
    k = int(re.match(r"\s*\d+ madde tek istekte sığmıyor: (\d+) grupta", s["n_liste_grup_log"][0]).group(1))
    assert max(girdi[:k]) <= llm.LISTE_GRUP_MADDE, girdi


@pytest.mark.parametrize("ad,seed", _uzun())
def test_uzun_toplantida_hicbir_aksiyon_ya_da_karar_kaybolmaz(ad, seed):
    """Kayit numarasiyla: kesin / belirsiz fark etmez, ⟨⟩ disindaki her cikarilan madde notta; fazla madde yok."""
    s = olc(ad, seed)
    assert s["n_kayip_aksiyon"] == [] and s["n_kayip_karar"] == [], (s["n_kayip_aksiyon"], s["n_kayip_karar"])
    assert s["n_aksiyon"] == s["n_iddia_aksiyon_acik"] and s["n_not_kararlar"] == s["n_iddia_karar_acik"]


@pytest.mark.parametrize("ad,seed", _uzun())
def test_yedege_dusus_olursa_gorunur_ve_madde_kaybi_yok(ad, seed):
    """Kapasite kenarinda bile (not_cok_uzun/7) yerel birlestirme SESSIZ degil: llm_log olayi + Olaylar mesaji ayni sayida;
    ve hicbir madde kaybolmaz."""
    s = olc(ad, seed)
    assert len(s["n_liste_yedek"]) == len(s["n_liste_yerel_log"])
    assert s["n_kayip_aksiyon"] == [] and s["n_kayip_karar"] == []
    assert (ad, seed) == CAPACITY_KENARI or s["n_liste_yedek"] == []


# ---------------- onceki kapilar degismez ----------------

@pytest.mark.parametrize("ad,seed", _kosumlar())
def test_onceki_fazlarin_degismez_kapilari(ad, seed):
    """Faz 1 / 2 kapilarindan not hattindan bagimsiz olanlar yeni senaryolarda da tutar (yanlis kesin ad, cift satir, bozulan,
    altyazi metni sizintisi, jsonl tutarliligi, gosterim kurali)."""
    s = olc(ad, seed)
    assert s.get("yanlis", 0) == 0 and s.get("cift_ut", 0) == 0 and s.get("yanki_kalan", 0) == 0
    assert s["bozulan"] == 0 and s["bozulan_kelime"] == 0
    assert s["altyazi_metni"] == 0 and s["sizinti"] == 0
    assert s["jsonl_tutarsiz"] == 0 and s["gosterim_hatali"] == 0 and s["bekleyen_kalan"] == 0
