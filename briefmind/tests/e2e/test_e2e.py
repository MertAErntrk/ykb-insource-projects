"""Uctan uca duzenek kapilari, Faz 1 kabulu (MIMARI_v3 §8 Duzenek, §9 Faz 1) + Faz 2'nin 14 senaryoya yayilan kapilari
(bozulan = 0, halusinasyon klibi 0 kesin satir, terim mikro testi). Sentetik, ag yok, repo'ya dosya yazmaz.
Faz 2'ye ozgu senaryolar (terim_duzeltme, halusinasyon_cesit, prompt_sizintisi, ogrenme) test_faz2.py'de.

14 senaryo (12 aile; gecikme 0 / 2 / 3.5) x 3 tohum, tum hat gercek: SesServisi klip olayi -> Motor -> Konusmaci.
Kapilar (plan §9): kesin gosterilen yanlis ad 0, cift satir 0, yanlis "ayni anda" 0, altyazi acikken belirsiz <= %12
(klip bazli, yeniden kesme sonrasi), altyazi kapaliyken uzak satirlarin %100'u "?", L tahmini +-0.6 sn, altyazi metni
transkripte girmez, yanki klibi atilir, STT kesintisi nota yazilir.

Kapi gevsetilmez. Kodun (konusmaci.py / ses.py / motor.py) bilinen eksikleri xfail(strict=True) ile belgelenir: sonuc
duzelince "XPASS(strict)" hatasi verir ve isaret kaldirilir. Her isaretin nedeni olculen (senaryo, tohum, klip,
beklenen / gercek) degeridir; ayni degerler tests/e2e/duzenek.py --ayrinti ile yeniden uretilir.
"""
import os
import socket

import pytest

import duzenek as D
import ses

SEEDLER = D.SEDLER + D.SEDLER_GENIS        # 3 tablo tohumu + 10 ek tohum: oranlar ~1.7-1.9k klip uzerinden havuzlanir
ADLAR = list(D.SENARYOLAR)
BELIRSIZ_ESIK = 12.0                      # % (plan §1: Faz1 sim, klip bazli)
# Belirsiz orani kapisi: gercek konusma karisimi senaryolari. Hizli devir / araya girme / ayni anda / klip ici devir /
# ben+uzak (oda, konuk etiketi tavan 0.6) dogasi geregi cok belirsizdir; yalniz sert kapilara tabidir.
BELIRSIZ_KAPILI = ["gecikme0", "gecikme2", "gecikme3_5", "tekrar", "halusinasyon", "altyazi_kapali", "yanki",
                   "okuyucu_takilmasi", "stt_kesintisi"]
# Altyazi kapali / okuyucu takilmasi penceresi icinde en az bu kadar uzak klip olmali (kapi dis olmasin)
KAPSAMASIZ_EN_AZ = {"altyazi_kapali": 8, "yanki": 6, "okuyucu_takilmasi": 2}
# ---------------- bilinen kod eksikleri (xfail strict): kapi -> {senaryo: (tohumlar)} ----------------
# Her isaret duzenek.py --seed N <senaryo> --ayrinti ile klip / beklenen / gercek olarak yeniden uretilir. Duzelince
# XPASS(strict) hatasi verir ve tohum buradan silinir.
KIRLI_NEDEN = ("kesin ad payi %70-%80 (spk_guven = baskin - ikinci, plan formulu) ya da sessiz blogu zayif (kontrast <= 0.4) "
               "kesimin cocugu: klip baska kisinin sozunden >= %20 kelime tasiyor ama 'baskin' >= 0.7. Sessiz blok yoksa "
               "artik kesilmiyor (ses.kesim_sessiz_mi, 31 -> 13 tohum). Ornek: altyazi_kapali/101 klip 4 25.8-32.1 sn "
               "Sinan 7 / Kaan 2 kelime, Sinan 'baskin' 0.703; gecikme2/107 klip 100 Kaan 8 / Sinan 3, Kaan 0.716")
KIRLI = {
    "hizli_devir": (7,), "araya_girme": (103, 104, 105), "klip_ici_devir": (104,), "altyazi_kapali": (101,),
    "gecikme0": (102,), "gecikme2": (101, 107), "halusinasyon": (105,), "okuyucu_takilmasi": (101,),
    "stt_kesintisi": (101, 107),
}
# Belirsiz orani (havuzlanmis) %12'yi asan senaryolar: olculen yuzde
BELIRSIZ_BILINEN = {"tekrar": 12.9}
BELIRSIZ_NEDEN = ("kok neden: tek konusmacili klipte bile spk_guven < 0.7 ('Ad?', ~%8) - kisa klipte altyazi bandi kapsami "
                  "< %70 ve onceki/sonraki konusmacinin bandi (basta R/2 erken) klibe tasiyor; ust uste binme ('A / B' ~%1.5) "
                  "ve cozulemeyen devir ('A -> B' ~%1) ekleniyor. Sinir degeri: yavas okuma orani %2 -> %3 ve altyazi parca "
                  "periyodu 0.5 -> 0.8 sn, havuzlanmis oran ~%2.5 puan artar. Olculen")


@pytest.fixture(autouse=True)
def _agsiz(monkeypatch):
    """Duzenek ag acamaz: baglanti denemesi testi hemen kirar (sessiz yutulmaz)."""
    def yasak(*_a, **_k):
        raise RuntimeError("duzenek ag erisimi yok")
    monkeypatch.setattr(socket.socket, "connect", yasak)
    monkeypatch.setattr(socket, "create_connection", yasak)


_onbellek = {}


def olc(ad, seed):
    """Senaryo sonucu (ayni (ad, seed) bir kez kosar; testler ayni veriyi paylasir)."""
    if (ad, seed) not in _onbellek:
        _onbellek[(ad, seed)] = D.calistir(seed=seed, **D.SENARYOLAR[ad])
    return _onbellek[(ad, seed)]


@pytest.fixture(scope="module", autouse=True)
def _onisitma(request):
    """Modulun testlerinin cogu kosuyorsa (tam kosum) 14 senaryo x tohum paralel (fork) onceden hesaplanir: sonuclar seri
    kosumla ayni, duvar suresi cekirdek sayisiyla duser. Az test secildiyse (-k) tembel kalir."""
    kosan = [i for i in request.session.items if i.module is request.module]
    if len(kosan) > 300 and D.paralel_sayisi() > 1:
        sonuc = D.toplu([((ad, seed), dict(seed=seed, **D.SENARYOLAR[ad])) for ad in ADLAR for seed in SEEDLER
                         if (ad, seed) not in _onbellek])
        for anahtar, (s, hata) in sonuc.items():
            if hata is None:
                _onbellek[anahtar] = s                       # hatali kosum onbellege girmez: ilk kullanan test seri kosar, hatayi gorur


def _tum():
    return [pytest.param(ad, seed, id=f"{ad}-{seed}") for ad in ADLAR for seed in SEEDLER]


def _bilinen(tablo, neden, ad, seed):
    """tablo[ad] icinde tohum varsa xfail(strict) isaretli parametre."""
    marks = [pytest.mark.xfail(strict=True, reason=neden)] if seed in tablo.get(ad, ()) else []
    return pytest.param(ad, seed, id=f"{ad}-{seed}", marks=marks)


def _tum_bilinen(tablo, neden):
    return [_bilinen(tablo, neden, ad, seed) for ad in ADLAR for seed in SEEDLER]


# ---------------- (a) altyazi metni transkripte / ciktiya girmez (K5 yok) ----------------

@pytest.mark.parametrize("ad,seed", _tum())
def test_a_altyazi_metni_transkripte_girmez(ad, seed):
    s = olc(ad, seed)
    assert s["altyazi_metni"] == 0, "kaynak 'altyazi*' satiri var"
    assert s["sizinti"] == 0, "altyazi izi (capNNNkNN) altyazi.jsonl / parca / not.md icinde"


# ---------------- (b) kesin gosterilen yanlis ad: 0 ----------------

@pytest.mark.parametrize("ad,seed", _tum())
def test_b_yanlis_kesin_ad_sifir(ad, seed):
    """spk_guven >= 0.7 ve kesin adin klip kelimelerindeki payi < %50 (metin baskasinin)."""
    s = olc(ad, seed)
    assert s.get("yanlis", 0) == 0, [a for a in s["ayrinti"] if a[0] == "YANLIS"]


@pytest.mark.parametrize("ad,seed", _tum_bilinen(KIRLI, KIRLI_NEDEN))
def test_b2_kirli_kesin_ad_sifir(ad, seed):
    """spk_guven >= 0.7 ve kesin ad klibin cogunlugunun sahibi ama metnin >= %20'si baskasinin."""
    s = olc(ad, seed)
    assert s.get("kirli", 0) == 0, [a for a in s["ayrinti"] if a[0] == "KIRLI"]


# ---------------- (c) cift satir: 0 ----------------

@pytest.mark.parametrize("ad,seed", _tum())
def test_c_cift_satir_sifir(ad, seed):
    """Ayni soz (soz id + kelime no) iki satirda yok; yanki klibi (mikrofon kopyasi) satir olarak kalmaz."""
    s = olc(ad, seed)
    assert s.get("cift_ut", 0) == 0, f"cift soz id'leri {s['cift_ut_liste']}"
    assert s.get("yanki_kalan", 0) == 0, [a for a in s["ayrinti"] if a[0] == "YANKI_KALDI"]


# ---------------- (d) yanlis "ayni anda": 0 ----------------

@pytest.mark.parametrize("ad,seed", _tum())
def test_d_yanlis_ayni_anda_sifir(ad, seed):
    """Gercekte ust uste binme olmayan klipte 'A / B' (cakisma): sirali devir ya da tek konusmaci."""
    s = olc(ad, seed)
    assert s.get("yanlis_ayni_anda_sirali", 0) == 0, [a for a in s["ayrinti"] if a[0] == "YANLIS_AYNI_ANDA"]
    assert s.get("yanlis_ayni_anda_dayanaksiz", 0) == 0, [a for a in s["ayrinti"] if a[0] == "YANLIS_AYNI_ANDA"]


# ---------------- (e) altyazi acikken belirsiz orani <= %12 (havuzlanmis, senaryo basina) ----------------

def _belirsiz_orani(ad):
    top = bel = 0
    for seed in SEEDLER:
        s = olc(ad, seed)
        top += s.get("kapsamali", 0)
        bel += s.get("kapsamali_belirsiz", 0)
    return 100.0 * bel / max(1, top), top


@pytest.mark.parametrize("ad", [
    pytest.param(ad, marks=([pytest.mark.xfail(strict=True, reason=f"{BELIRSIZ_NEDEN} %{BELIRSIZ_BILINEN[ad]} (kapi %12)")]
                            if ad in BELIRSIZ_BILINEN else []))
    for ad in BELIRSIZ_KAPILI])
def test_e_belirsiz_orani(ad):
    """'?', 'Ad?', 'A -> B', 'A / B' klip orani; altyazi penceresine degmeyen klipler, 3 tohum havuzu."""
    oran, n = _belirsiz_orani(ad)
    assert n >= 200, f"{n} kapsamali klip: kapi anlamsiz"
    assert oran <= BELIRSIZ_ESIK, f"belirsiz %{oran:.1f} > %{BELIRSIZ_ESIK} ({n} klip)"


# ---------------- (f) altyazi kapaliyken uzak satirlar %100 '?' ----------------

@pytest.mark.parametrize("ad,seed", [(ad, seed) for ad in KAPSAMASIZ_EN_AZ for seed in SEEDLER])
def test_f_altyazi_kapali_uzak_satirlar_soru(ad, seed):
    s = olc(ad, seed)
    assert s.get("kapsamasiz_uzak", 0) >= KAPSAMASIZ_EN_AZ[ad], "pencerede yeterli uzak klip yok"
    assert s.get("kapsamasiz_uzak_soru_degil", 0) == 0, [a for a in s["ayrinti"] if a[0] == "KAPSAMASIZ_SORU_DEGIL"]


@pytest.mark.parametrize("ad,seed", _tum())
def test_f2_kapsamasiz_uzak_hicbir_senaryoda_kesin_ad_almaz(ad, seed):
    assert olc(ad, seed).get("kapsamasiz_uzak_soru_degil", 0) == 0


# ---------------- (g) L tahmini +-0.6 sn ----------------

@pytest.mark.parametrize("ad,seed", _tum())
def test_g_L_tahmini(ad, seed):
    """Konusmaci.durum()['L'] ile idealize L (>= 1 sn sessizlik sonrasi ilk satirin klip basina gecikmesi, medyan)."""
    s = olc(ad, seed)
    assert s["L_gercek"] is not None, "idealize L olculemedi"
    assert s["L_hata"] <= 0.6, f"L={s['L']} idealize={s['L_gercek']} MAD={s['MAD']}"


def test_g2_L_gecikmeyi_izler():
    """Gecikme 0 / 2 / 3.5 sn senaryolarinda tahmin gercek gecikmeyle birlikte hareket eder."""
    L = {ad: sum(olc(ad, s)["L"] for s in SEEDLER) / len(SEEDLER) for ad in ("gecikme0", "gecikme2", "gecikme3_5")}
    assert L["gecikme0"] + 1.0 < L["gecikme2"] < L["gecikme3_5"] - 0.8, L


# ---------------- (h) yanki: atilir, kullanici sozu atilmaz ----------------

@pytest.mark.parametrize("seed", SEEDLER)
def test_h_yanki_klipleri_atilir(seed):
    """Hoparlor: mikrofon yankisi kullanici altyazisi olmadan atilir (cogunlugu); atilan sayisi anlamli."""
    s = olc("yanki", seed)
    assert s.get("yanki_atilan", 0) >= 20, "yanki klibi olusmadi / atilmadi"
    assert s.get("yanki_atilan", 0) >= 20 * s.get("yanki_kalan", 0)


@pytest.mark.parametrize("ad,seed", _tum())
def test_h2_gercek_soz_kaybolmaz(ad, seed):
    """Yanki olmayan, teslim edilmis hicbir klip atilmaz; metni olan hicbir soz (kaybi aciklanabilir olanlar haric) eksik kalmaz."""
    s = olc(ad, seed)
    assert s.get("yanlis_atilan", 0) == 0, [a for a in s["ayrinti"] if a[0] == "YANLIS_ATILAN"]
    assert s.get("eksik", 0) == 0, s["eksik_liste"]


# ---------------- (i) halusinasyon klibi: kesin ad almaz ----------------

@pytest.mark.parametrize("seed", SEEDLER)
def test_i_halusinasyon_klibi_kesin_ad_almaz(seed):
    s = olc("halusinasyon", seed)
    assert s.get("hayalet_klip", 0) >= 5, "sessizlik klibi uretilmedi"
    assert s.get("hayalet_kesin", 0) == 0, [a for a in s["ayrinti"] if a[0] == "HAYALET_KESIN"]


@pytest.mark.parametrize("seed", SEEDLER)
def test_i2_halusinasyon_klibi_kesin_satir_sifir(seed):
    """Faz 2: sessizlikte uydurulan klipten kesin (supheli olmayan) satir cikmaz -- supheli (soluk, nota girmez) ya da atilir.
    Konusma ardindaki ('yakin') klipler de dahil."""
    s = olc("halusinasyon", seed)
    assert s.get("hal_toplam", 0) >= 5, "halusinasyon klibi uretilmedi"
    assert s.get("hal_kesin", 0) + s.get("hal_belirsiz_kesin", 0) == 0, [a for a in s["ayrinti"] if a[0] == "HALUSINASYON_KESIN"]
    assert s.get("hal_supheli", 0) + s.get("hal_dusen", 0) == s["hal_toplam"]
    assert s.get("hal_llm_girdi", 0) == 0, "supheli satir LLM'e (duzeltme / ozet) gitti"


# ---------------- (j) oda / konuk etiketi, hayalet ad ----------------

@pytest.mark.parametrize("seed", SEEDLER)
def test_j_oda_ve_konuk_etiketi_kesin_olmaz(seed):
    s = olc("ben_uzak", seed)
    assert s.get("oda_klip", 0) >= 20, "oda / konuk klibi yok"
    assert s.get("oda_kesin", 0) == 0
    assert s.get("oda_gorunen_hatali", 0) == 0


@pytest.mark.parametrize("ad,seed", _tum())
def test_j2_hayalet_ad_satira_girmez(ad, seed):
    assert olc(ad, seed).get("hayalet_ad", 0) == 0


# ---------------- (k) STT kesintisi ----------------

@pytest.mark.parametrize("seed", SEEDLER)
def test_k_stt_kesintisi_eksik_aralik_notta(seed):
    s = olc("stt_kesintisi", seed)
    assert s["stt_eksik_kapsanmayan"] == 0, "cozulemeyen klip araligi Motor.stt_eksik() disinda"
    assert s["stt_eksik_notta"] is True, "not.md 'STT yok — transkript eksik' icermiyor"
    assert s["altyazi_metni"] == 0 and s["sizinti"] == 0


# ---------------- (l) tutarlilik ----------------

@pytest.mark.parametrize("ad,seed", _tum())
def test_l_jsonl_gosterim_bekleyen_tutarli(ad, seed):
    s = olc(ad, seed)
    assert s["jsonl_tutarsiz"] == 0, "altyazi.jsonl (guncelle / sil / yerine) bellekteki nihai satirlardan farkli"
    assert s["gosterim_hatali"] == 0, "metin.gorunen_ad kurali ihlali"
    assert s["bekleyen_kalan"] == 0, "bitir sonrasi 'beklemede' satir"
    assert s["karar_gecikme_p95"] is not None and s["karar_gecikme_p95"] <= 9.0, s["karar_gecikme_p95"]


# ---------------- (m) yeniden kesme calisiyor ----------------

@pytest.mark.parametrize("seed", SEEDLER)
def test_m_yeniden_kesme_calisir(seed):
    s = olc("klip_ici_devir", seed)
    assert s["kes_tamam"] >= 15, f"yeniden kesme {s['kes_tamam']}/{s['kes_istek']}"
    assert s["kes_tamam"] >= 0.75 * s["kes_istek"]
    assert olc("ayni_anda", seed)["kes_tamam"] <= 3, "ayni anda klibi kesilmemeli"


# ---------------- (o) yeniden kesme parcasi tekrar suzgecinden gecer ----------------

def test_o_yeniden_kesme_parcasi_tekrar_suzgecinden_gecer():
    gelen = []
    servis = ses.SesServisi("", olay_fn=gelen.append)
    ust = {"tur": "klip", "klip_no": 1, "akis": "loopback", "bas": D.T0, "bit": D.T0 + 7.0, "sure": 7.0,
           "text": "Bütçe tablosunu güncelledim. Test ortamı perşembe hazır olacak.", "ts": "10:00:00", "rms": 0.1,
           "zarf": [0.1] * 70, "ust_klip": None, "supheli": False}
    servis._sirali_yay(None, [ust])
    a = dict(ust, klip_no=2, bit=D.T0 + 3.4, sure=3.4, text="Bütçe tablosunu güncelledim.", ust_klip=1)
    b = dict(ust, klip_no=3, bas=D.T0 + 3.4, sure=3.6, text="Test ortamı perşembe hazır olacak.", ust_klip=1)
    servis._sirali_yay(None, [a])
    servis._sirali_yay(None, [b])
    assert [e["klip_no"] for e in gelen] == [1, 2, 3]


def test_o2_cocuk_muafiyeti_duzenekte_acik():
    """Duzenek parcayi muaf tutar: klip_ici_devir'de yeniden kesilen her klibin iki parcasi Motor'a ulasir."""
    s = olc("klip_ici_devir", 7)
    assert s["kes_tamam"] >= 15 and s["karar_gecikme_p95"] <= 9.0


# ---------------- (n) Faz 2: bozulan = 0 (gercek duzelt.kapi + sahte LLM'in zararli onerileri) ----------------

@pytest.mark.parametrize("ad", ADLAR)
def test_n_bozulan_sifir(ad):
    """Sahte LLM dogru terim duzeltmelerinin yanina yanlis olanlari da onerir (eki bozan, tum satir, 'yazim' tahmini);
    kapi bunlari tutar: dogru bir kelime hicbir satirda yanlis olmaz (satir duzeyi `bozulan` ve kelime duzeyi)."""
    for seed in SEEDLER:
        s = olc(ad, seed)
        assert s["bozulan"] == 0 and s["bozulan_kelime"] == 0, (ad, seed, s["bozulan"], s["bozulan_kelime"])
        assert s["ek_kaybi"] == 0, (ad, seed)
        assert "duzeltme" in s["llm_gorevleri"], "sahte LLM duzeltme icin cagrilmadi: kapi sinanmadi"


def test_n2_duzeltme_gercekten_isliyor():
    """Kapi hepsini reddetmiyor: tohum havuzunda dogru duzeltmeler kabul edilir, kelime hatasi azalir, 'yazim' uygulanmaz."""
    iyi = kabul = ham = son = belirsiz = 0
    for seed in SEEDLER:
        s = olc("gecikme2", seed)
        iyi, kabul = iyi + s["duzeltilen"], kabul + s["duz_kabul"]
        ham, son, belirsiz = ham + s["kelime_hata_ham"], son + s["kelime_hata"], belirsiz + s["belirsiz_kelime"]
        assert set(s["duzelt_log"]["neden"]) <= {"terim", "ad"}, "yazim onerisi metne uygulandi"
    assert iyi >= 60 and kabul >= iyi, (iyi, kabul)
    assert son < 0.8 * ham, (ham, son)
    assert belirsiz >= 10, "'yazim' tahminleri belirsiz kelime olarak isaretlenmedi"


# ---------------- (p) terim mikro testi (sozluk.py yerine duzelt.kapi + terim havuzu) ----------------

def test_terim_mikro_18_ornek_sifir_zarar():
    s = D.terim_mikro()
    assert s["toplam"] == 18
    assert s["zarar"] == [], f"komite->commit / kampanyacı->kampanya: {s['zarar']}"
    assert s["ek_kaybi"] == 0, "kabul edilen duzeltme eki kisalttı"
    assert s["yanlis"] == [], f"beklenenden farkli cikti (girdi, cikti, beklenen): {s['yanlis']}"
    assert s["degisen"] == 4 and s["kabul"] == 4, s              # Jenkins'e, Kubernetes, commit, Kubernetes'i
    assert s["oneri"] >= 30, "sahte LLM yeterince zararli oneri uretmedi"


def test_terim_mikro_komite_commit_reddedilir():
    from duzelt import kapi
    kabul, neden = kapi("komite toplandı", {"satir": 1, "eski": "komite", "yeni": "commit", "neden": "terim"},
                        ["commit", "Kubernetes"], [])
    assert not kabul and neden in ("uzaklik", "yaygin_kelime"), neden


def test_terim_mikro_ek_uyumu_vakalari():
    assert D.ek_uyum_mikro() == []


# ---------------- duzenegin kendisi ----------------

def test_senaryolar_12_aile():
    assert len(ADLAR) == 14
    assert {"hizli_devir", "araya_girme", "ayni_anda", "klip_ici_devir", "ben_uzak", "altyazi_kapali", "gecikme0",
            "gecikme2", "gecikme3_5", "tekrar", "halusinasyon", "yanki", "okuyucu_takilmasi", "stt_kesintisi"} == set(ADLAR)


def test_deterministik():
    a = D.calistir(seed=7, **D.SENARYOLAR["gecikme2"])
    b = olc("gecikme2", 7)
    for k in ("klip", "dogru", "soru", "adayli", "devir", "ayni", "yanlis", "kirli", "L", "kes_tamam", "satir"):
        assert a.get(k) == b.get(k), k


def test_dosya_yazmaz(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    D.calistir(seed=7, **D.SENARYOLAR["gecikme2"])
    assert os.listdir(str(tmp_path)) == []
