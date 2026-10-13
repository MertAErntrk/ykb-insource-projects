"""Faz 2 uctan uca kabul kapilari (MIMARI_v3 §9 Faz 2): terim + duzeltme + halusinasyon filtresi + Whisper prompt'u.
Sentetik, ag yok, repo'ya dosya yazmaz. Gercek: Motor, duzelt.duzelt kapisi (duzelt.kapi), terim.TerimHavuzu (ogrenme dahil),
SesServisi (halusinasyon puani, prompt korumalari), Konusmaci. Sahte: STT metni, Teams paneli, LLM onerileri (komite -> commit,
eki bozan, tum satir yeniden yazma, 'yazim' tahmini...), saat.

Kapilar: bozulan = 0; ek kaybi 0; komite -> commit reddi; 'yazim' hicbir zaman uygulanmaz; halusinasyon klibi 0 kesin satir
(supheli ya da atilir) ve LLM'e / nota gitmez; sessiz klip + Whisper prompt'u 0 kesin satir (negatif kontrollerle fixture'in
isirdigi kanitlanir); ogrenme acik son kelime hatasi <= kapali; Faz 1 kapilari prompt acikken degismez; altyazi metni sizmaz.
Kapi gevsetilmez; kodun bilinen eksigi xfail(strict=True) ile belgelenir."""
import os
import re
import socket

import pytest

import duzenek as D
import llm
import stt

SEEDLER = D.SEDLER_FAZ2
FAZ2 = D.SENARYOLAR_FAZ2
KOK = D.REPO
F1_AD = ("gecikme2", "klip_ici_devir")
KONTROL_SEEDLER = D.SEDLER[:2]
F1_KARSILASTIR = ("klip", "dogru", "yanlis", "kirli", "soru", "adayli", "devir", "ayni", "cift_ut", "yanki_kalan", "satir",
                  "bekleyen_kalan", "capa", "L", "kes_tamam", "eksik")
K1 = {"ipucu_rms": 0.0}                                         # prompt korumasi 1 kapali: dusuk RMS klibe de prompt gider
K2 = {"ipucu_rms": 0.0, "hal_supheli": 2.0, "hal_at": 2.0}      # koruma 1 + 2 kapali: supheli puan da etkisiz (fixture isirmali)
HAL_KATEGORI = ("akici", "kara_liste", "altyazi_mk", "dongu", "krsn_ust", "krsn_alt", "akici_mikrofon", "krsn_orta", "rms_dusuk")


@pytest.fixture(autouse=True)
def _agsiz(monkeypatch):
    def yasak(*_a, **_k):
        raise RuntimeError("duzenek ag erisimi yok")
    monkeypatch.setattr(socket.socket, "connect", yasak)
    monkeypatch.setattr(socket, "create_connection", yasak)


_onbellek = {}


def _isler():
    isler = [((ad, sd), dict(seed=sd, **FAZ2[ad])) for ad in FAZ2 for sd in SEEDLER]
    for sd in KONTROL_SEEDLER:
        isler.append((("k1", sd), dict(seed=sd, **dict(FAZ2["prompt_sizintisi"], esikler=K1))))
        isler.append((("k2", sd), dict(seed=sd, **dict(FAZ2["prompt_sizintisi"], esikler=K2))))
    for ad in F1_AD:
        for sd in (7, 11):
            isler.append((("f1", ad, sd), dict(seed=sd, **D.SENARYOLAR[ad])))
            isler.append((("f1_ipucu", ad, sd), dict(seed=sd, **dict(D.SENARYOLAR[ad], ipucu=True))))
    isler.append((("ogrenme", True), {"ogrenme": True}, "ogrenme_kos"))
    isler.append((("ogrenme", False), {"ogrenme": False}, "ogrenme_kos"))
    return isler


@pytest.fixture(scope="module", autouse=True)
def _onisitma(request):
    """Tam kosumda tum kosumlar paralel (fork) onceden hesaplanir; az test secildiyse tembel (olc)."""
    kosan = [i for i in request.session.items if i.module is request.module]
    if len(kosan) > 25 and D.paralel_sayisi() > 1:
        for anahtar, (sonuc, hata) in D.toplu(_isler()).items():
            if hata is None:
                _onbellek[anahtar] = sonuc


def olc(*anahtar):
    """Kosum sonucu: ('terim_duzeltme', seed) | ('k1', seed) | ('f1', ad, seed) | ('ogrenme', True) ..."""
    if anahtar not in _onbellek:
        for a, *kw in [x for x in _isler() if x[0] == anahtar]:
            _onbellek[anahtar] = (D.ogrenme_kos(**kw[0]) if len(kw) > 1 else D.calistir(**kw[0]))
    return _onbellek[anahtar]


def _topla(ad, alan, seedler=SEEDLER):
    return sum(olc(ad, sd).get(alan, 0) for sd in seedler)


def _kirmizi(s, anahtar):
    return [a for a in s["ayrinti"] if a[0] == anahtar]


# ---------------- terim + duzeltme kapisi (yogun sahte LLM) ----------------

@pytest.mark.parametrize("seed", SEEDLER)
def test_terim_duzeltme_bozulan_sifir(seed):
    s = olc("terim_duzeltme", seed)
    assert s["bozulan"] == 0 and s["bozulan_kelime"] == 0, (s["bozulan"], s["bozulan_kelime"])
    assert s["ek_kaybi"] == 0, "kabul edilen duzeltme eki kisalttı"
    assert s["komite_say"] >= 8, "komite cumleleri uretilmedi: kapi sinanmadi"
    assert s["komite_commit"] == 0, "komite -> commit uygulandi"
    assert "duzeltme" in s["llm_gorevleri"]


@pytest.mark.parametrize("seed", SEEDLER)
def test_terim_duzeltme_dogrusunu_kabul_eder_ve_hatayi_azaltir(seed):
    s = olc("terim_duzeltme", seed)
    assert s["duzeltilen"] >= 15 and s["duz_kabul"] >= s["duzeltilen"], (s["duzeltilen"], s["duz_kabul"])
    assert s["kelime_hata"] < 0.7 * s["kelime_hata_ham"], (s["kelime_hata_ham"], s["kelime_hata"])
    assert s["belirsiz_kelime"] >= 3, "'yazim' tahminleri belirsiz isaretlenmedi"


def test_terim_duzeltme_kapi_zararli_onerileri_gercekten_reddetti():
    """Sahte LLM'in yanlis onerileri kapida takildi (red nedenleri); 'yazim' hicbir zaman uygulanmadi."""
    red, neden = {}, set()
    for sd in SEEDLER:
        g = olc("terim_duzeltme", sd)["duzelt_log"]
        for k, v in g["red"].items():
            red[k] = red.get(k, 0) + v
        neden |= set(g["neden"])
    for kod in ("yaygin_kelime", "uzaklik", "satir_orani", "yazim_uygulanmaz", "kesme_isareti", "sema_disi", "satir_yok"):
        assert red.get(kod, 0) >= 1, (kod, red)
    assert red.get("ek_kaybi", 0) + red.get("ek_uyumsuz", 0) >= 5, red
    assert sum(red.values()) >= 150, red
    assert neden <= {"terim", "ad"} and "ad" in neden, neden


@pytest.mark.parametrize("seed", SEEDLER)
def test_terim_duzeltme_faz1_kapilari_degismez(seed):
    s = olc("terim_duzeltme", seed)
    assert s.get("yanlis", 0) == 0 and s.get("cift_ut", 0) == 0 and s.get("yanki_kalan", 0) == 0
    assert s.get("yanlis_atilan", 0) == 0 and s.get("eksik", 0) == 0, s["eksik_liste"]
    assert s["altyazi_metni"] == 0 and s["sizinti"] == 0, "altyazi metni LLM mesajina / ciktiya girdi"
    assert s["jsonl_tutarsiz"] == 0 and s["gosterim_hatali"] == 0 and s["bekleyen_kalan"] == 0


# ---------------- halusinasyon filtresi ----------------

@pytest.mark.parametrize("seed", SEEDLER)
def test_halusinasyon_cesit_sifir_kesin_satir(seed):
    """B3 kurali: halusinasyon klibi kesin ad almaz; atilir (yalniz icsel puan hal_at), icsel supheli (nota girmez, LLM'e
    gitmez), 'Bilinmeyen' (speaker '?' ya da cizelge suphesi: nota 'Bilinmeyen:' girer) ya da 'Ad?' (sahip uretmez).
    Zaman cizelgesi hicbir klibi silmez."""
    s = olc("halusinasyon_cesit", seed)
    assert s["hal_toplam"] >= 10, "halusinasyon klibi uretilmedi"
    assert s["hal_kesin"] + s["hal_belirsiz_kesin"] == 0, _kirmizi(s, "HALUSINASYON_KESIN") + _kirmizi(s, "HALUSINASYON_KESIN_YAKIN")
    assert s["hal_supheli"] + s["hal_dusen"] + s["hal_bilinmeyen"] + s["hal_adayli"] == s["hal_toplam"]
    assert s["hal_llm_girdi"] == 0, "icsel supheli satir LLM'e (duzeltme / ozet) gitti"
    assert s["satir_sil"].get("halusinasyon", 0) == 0, "zaman cizelgesi klip sildi"
    assert s["hal_cizelge"] >= 1 and s["cizelge_bilinmeyen_eksik"] == 0, "cizelge suphesi satiri 'Bilinmeyen:' ile nota girmedi"
    assert s["sizinti"] == 0 and s["bozulan"] == 0 and s["bozulan_kelime"] == 0
    assert s.get("yanlis", 0) == 0 and s.get("cift_ut", 0) == 0 and s.get("eksik", 0) == 0


def test_halusinasyon_cesit_kapsam():
    """Tum turler (kara liste, dongu, kr/sn ucu, akici cumle, mikrofon, orta icsel puanli krsn_orta / rms_dusuk) hem sessiz
    bosluk hem konusma ardinda uretildi; icsel supheli satirlar var (soluk, nota girmez), atilanlar var (SesServisi suzgeci:
    hal_atilan), cizelge suphesi satirlari var (nota 'Bilinmeyen:'); hicbir tur kesin ad almadi."""
    dag = {}
    for sd in SEEDLER:
        for k, v in olc("halusinasyon_cesit", sd)["hal_dagilim"].items():
            dag[k] = dag.get(k, 0) + v
    turler = {k.split(":")[1] for k in dag}
    assert set(HAL_KATEGORI) <= turler, (set(HAL_KATEGORI) - turler)
    assert {k.split(":")[0] for k in dag} == {"uzak", "yakin"}
    assert sum(v for k, v in dag.items() if k.endswith(":supheli")) >= 20, dag
    assert sum(v for k, v in dag.items() if k.endswith(":dusen")) >= 20, dag
    assert sum(v for k, v in dag.items() if k.endswith(":bilinmeyen")) >= 15, dag
    assert _topla("halusinasyon_cesit", "hal_cizelge") >= 5, dag
    assert _topla("halusinasyon_cesit", "hal_atilan") >= 1, "hicbir klip atilmadi (SesServisi suzgeci)"
    assert all(not k.endswith(":kesin") and not k.endswith(":belirsiz_kesin") for k in dag), dag
    for sd in SEEDLER:
        assert olc("halusinasyon_cesit", sd)["hal_belirsiz_kesin"] == 0


# ---------------- Whisper prompt'u ----------------

@pytest.mark.parametrize("seed", SEEDLER)
def test_prompt_sizintisi_sifir_kesin_satir(seed):
    """Sessiz klipte prompt gitmez (RMS korumasi); gitse bile sizan prompt metni supheli puanla promptsuz tekrarlanir."""
    s = olc("prompt_sizintisi", seed)
    assert s["hal_toplam"] >= 5, "sessiz klip uretilmedi"
    assert s["hal_kesin"] + s["hal_belirsiz_kesin"] == 0, _kirmizi(s, "HALUSINASYON_KESIN")
    assert s["ipucu_sizinti_satir"] == 0, "prompt metni transkripte girdi"
    assert s["ipucu_dusuk_rms"] == 0, "dusuk RMS'li klibe Whisper prompt'u gitti"
    assert s["ipucu_gonderilen"] >= 50, "prompt konusmali kliplere gitmiyor"
    assert s["ipucu_yeniden"] == s["ipucu_sizinti_cagri"], "sizan prompt'lu sonuc promptsuz tekrarlanmadi"
    assert s["sizinti"] == 0 and s["bozulan"] == 0 and s["bozulan_kelime"] == 0


@pytest.mark.parametrize("seed", SEEDLER[:3])
def test_prompt_icerigi_ve_boyutu(seed):
    """terim.prompt: katilimci adlari + terimler, <= 400 karakter, <= 120 token."""
    p = olc("terim_duzeltme", seed)["ipucu_ornek"]
    assert p.startswith("Katılımcılar:") and "Terimler:" in p and "Kubernetes" in p and "Melek Taslak" in p
    assert len(p) <= stt.IPUCU_KR_TAVAN and llm.token_tahmin(p) <= 120, (len(p), p)
    assert olc("terim_duzeltme", seed)["ipucu_azami_kr"] <= stt.IPUCU_KR_TAVAN


def test_prompt_koruma_ikincisi_tek_basina_yeter():
    """Negatif kontrol 1: RMS korumasi kapali (quiet klibe de prompt gider, sizinti olur) -> promptsuz tekrar kesin satiri onler."""
    seedler = KONTROL_SEEDLER
    assert sum(olc("k1", sd)["ipucu_dusuk_rms"] for sd in seedler) >= 5, "kontrol dusuk RMS'e prompt gondermedi"
    assert sum(olc("k1", sd)["ipucu_sizinti_cagri"] for sd in seedler) >= 8
    for sd in seedler:
        s = olc("k1", sd)
        assert s["hal_kesin"] == 0 and s["ipucu_sizinti_satir"] == 0, _kirmizi(s, "HALUSINASYON_KESIN")


def test_prompt_negatif_kontrol_fixture_isirir():
    """Negatif kontrol 2: iki koruma da kapali -> sizan prompt metni transkripte girer ve satir supheli degildir (nota
    girer; B3 sonrasi sessizlikteki klip zaman cizelgesinden 'Bilinmeyen' / 'Ad?' kalir, icerigi dusmez): fixture bos degil."""
    seedler = KONTROL_SEEDLER
    assert sum(olc("k2", sd)["ipucu_sizinti_satir"] for sd in seedler) >= 3
    assert sum(olc("k2", sd)["hal_bilinmeyen"] + olc("k2", sd)["hal_adayli"] + olc("k2", sd)["hal_kesin"]
               for sd in seedler) >= 3


# ---------------- negatif kontroller: kapilar gercekten isirir ----------------

def _gevsek_kapi(satir, oneri, adaylar, adlar):
    """Yalniz sema + 'eski tam bir kez gecer' kontrolu yapan (yanlis) kapi: VE kurali, ek uyumu, yaygin kelime yok."""
    import duzelt
    if not isinstance(oneri, dict) or oneri.get("neden") not in ("terim", "ad") or not isinstance(oneri.get("eski"), str) \
            or not isinstance(oneri.get("yeni"), str):
        return False, "sema_disi"
    eski = oneri["eski"].strip()
    return (len(duzelt.gecisler(satir, eski)) == 1 and eski != oneri["yeni"].strip() and len(eski.split()) <= 2), "gevsek"


def test_negatif_kontrol_gevsek_kapi_mikro_testi_bozar(monkeypatch):
    import duzelt
    monkeypatch.setattr(duzelt, "kapi", _gevsek_kapi)
    s = D.terim_mikro()
    assert s["zarar"] and s["yanlis"] and s["ek_kaybi"] > 0, s


def test_negatif_kontrol_gevsek_kapi_bozulan_uretir(monkeypatch):
    import duzelt
    monkeypatch.setattr(duzelt, "kapi", _gevsek_kapi)
    s = D.calistir(seed=7, **FAZ2["terim_duzeltme"])
    assert s["bozulan_kelime"] > 0 and s["komite_commit"] > 0 and s["ek_kaybi"] > 0, s


def test_negatif_kontrol_cizelge_kurali_kapaliyken_bilinmeyen_etiketi_kalkar(monkeypatch):
    """B3: cizelge kurali kapaninca (CIZELGE_PUANI 0) orta icsel puanli (0.3-0.4) halusinasyon klipleri cizelge suphesi
    almaz: 'Bilinmeyen' zorlamasi kalkar, konusma ardindakiler onceki konusmaciya 'Ad?' (yapiskan) olarak kalir; kesin ad
    yine cikmaz (konusmaci karari). Kural acikken ayni tohumlarda cizelge suphesi >= 2 / tohum."""
    import motor
    acik = [olc("halusinasyon_cesit", sd) for sd in KONTROL_SEEDLER]
    assert all(s["hal_cizelge"] >= 2 for s in acik), [s["hal_cizelge"] for s in acik]
    monkeypatch.setattr(motor, "CIZELGE_PUANI", 0.0)
    kapali = [D.calistir(seed=sd, **FAZ2["halusinasyon_cesit"]) for sd in KONTROL_SEEDLER]
    assert all(s["hal_cizelge"] == 0 and s["cizelge_supheli"] == 0 for s in kapali), [s["hal_dagilim"] for s in kapali]
    assert sum(s["hal_adayli"] for s in kapali) > sum(s["hal_adayli"] for s in acik), \
        ([s["hal_dagilim"] for s in kapali], [s["hal_dagilim"] for s in acik])
    assert all(s["hal_kesin"] + s["hal_belirsiz_kesin"] == 0 for s in kapali)


# ---------------- ogrenme acik / kapali ----------------

def test_ogrenme_acik_kelime_hatasi_kapaliyi_gecmez():
    a, k = olc("ogrenme", True), olc("ogrenme", False)
    assert a["kelime_hata"] <= k["kelime_hata"], (a["kelime_hata"], k["kelime_hata"])
    assert a["kelime_ref"] == k["kelime_ref"] and a["kelime_ref"] > 1000
    assert a["bozulan"] == 0 and a["bozulan_kelime"] == 0 and k["bozulan_kelime"] == 0
    assert a["kelime_hata"] < 0.7 * a["kelime_hata_ham"]


def test_ogrenme_calisir_ve_zarar_vermez():
    a, k = olc("ogrenme", True), olc("ogrenme", False)
    assert k["ogrenilen"] == [] and k["gozlem"] == 0
    assert a["gozlem"] >= 4, "kabul edilen duzeltmeler gozlem olarak kaydedilmedi"
    assert len(a["ogrenilen"]) >= 1, "bagimsiz kanitli duzeltme (davette gecen terim) varyant olmadi"
    # ogrenilen varyantlar yalniz Whisper'in yanlis duydugu biçimler: gercek / yaygin kelime hic varyant olmaz
    assert set(a["ogrenilen"]) <= set(D.DOGRU_ONERI), a["ogrenilen"]
    for w in a["ogrenilen"]:
        assert not D.kelime.yaygin_mi(D.kelime.normalize(w)), w
    assert not {"komite", "kampanyacı", "terminal"} & {w.lower() for w in a["ogrenilen"]}


# ---------------- Faz 1 kapilari prompt acikken degismez ----------------

@pytest.mark.parametrize("ad", F1_AD)
@pytest.mark.parametrize("seed", (7, 11))
def test_faz1_sonuclari_prompt_acikken_ayni(ad, seed):
    a, b = olc("f1", ad, seed), olc("f1_ipucu", ad, seed)
    assert {k: a.get(k) for k in F1_KARSILASTIR} == {k: b.get(k) for k in F1_KARSILASTIR}
    assert b["ipucu_gonderilen"] >= 50 and a["ipucu_gonderilen"] == 0
    assert b["bozulan_kelime"] == 0 and b["ipucu_dusuk_rms"] == 0


# ---------------- sozluk kalintisi yok ----------------

def test_kodda_sozluk_sinifi_ve_ice_aktarma_yok():
    """Plan §9 Faz 2: Sozluk sinifi / modulu kullanimi yok (sinif cagrisi, import, uygula; kaldirilan Motor API'si test_akis'te). Eski dosya bicimi 'sozluk.json' goc
    araci (tools/terim_aktar.py) ve testinde bilincli kalir; 'Sozluk' sozcugu yorum / docstring'de gecebilir."""
    desen = re.compile(r"\bSozluk\s*\(|\bimport\s+sozluk\b|\bfrom\s+sozluk\b|\bsozluk_mod\b|\bsozluk\.uygula")
    bulunan = []
    for kok, klasorler, adlar in os.walk(KOK):
        klasorler[:] = [d for d in klasorler if d not in ("__pycache__", ".git", "docs", "deploy", "toplantilar", ".pytest_cache")]
        for ad in adlar:
            if not ad.endswith(".py") or ad == "test_faz2.py":
                continue
            yol = os.path.join(kok, ad)
            with open(yol, encoding="utf-8") as f:
                for i, satir in enumerate(f, 1):
                    if desen.search(satir) and not satir.lstrip().startswith("#"):
                        bulunan.append(f"{os.path.relpath(yol, KOK)}:{i}: {satir.strip()[:80]}")
    assert bulunan == [], bulunan
