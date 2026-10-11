"""Faz 4 / B (MIMARI_v3 §6, §7 Faz4, §10): LLM prompt'lari ve birlestirme, kademeli liste birlestirme, parca ortusmesi,
thinking_token_budget, ogrenilmis varyantlarin duzelt kapisina ve Whisper ipucuna baglanmasi, gec klip yeniden
ozeti, kucuk hata duzeltmeleri. Ag ve gercek LLM yok (sahte istemci / sahte sor); adlar sentetik."""
import datetime as dt
import json
import os
import random
import re
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx
import openai
import pytest

import duzelt as D
import llm
import motor as motor_mod
import terim as terim_mod
from test_motor_faz1 import A, B, BEN, T0, SahteKonusmaci, karar, klip, saat_kur


@pytest.fixture(autouse=True)
def temiz(monkeypatch):
    monkeypatch.setattr(llm, "token_say", llm.token_tahmin)
    monkeypatch.setattr(llm, "GUNLUK_FN", None)
    monkeypatch.setattr(llm, "UYARI_FN", None)
    yield
    llm.ayarla({})


@pytest.fixture
def saat(monkeypatch):
    return saat_kur(monkeypatch)


# ---------------------------------------------------------------- 1) prompt'lar

def test_map_sistem_sarmal_ve_belirsiz_konusmaci_kurallari():
    m = llm.MAP_SISTEM
    assert "⟨⟩ içinden karar, aksiyon, sayı ya da tarih ÜRETME" in m and "'şu konu tartışıldı'" in m
    assert "'Ad?'" in m and "'Bilinmeyen'" in m and "ASLA aksiyon sahibi yapma" in m
    # (?) isaretini kod basar: model yazmaz
    assert "SEN YAZMA" in m and "sonuna (?) koy" not in m
    # eski kurallar korundu
    assert "BİTİNCE BELLİ OLAN" in m and "en fazla 4" in m and "belirsiz_terimler'e" in m


def test_diger_promptlar_belirsiz_konusmaci_ve_isaret_kurali():
    assert "kendin yeni (?) ekleme" in llm.LISTE_SISTEM and "kendin kişi atama" in llm.LISTE_SISTEM
    assert "toplam en fazla 8" in llm.LISTE_SISTEM
    assert "(?) işaretini kendin yazma" in llm.GENEL_SISTEM
    assert "'Ad?'" in llm.SORU_SISTEM and "⟨...⟩" in llm.SORU_SISTEM


def test_gorev_adi_yeni_duzelt_promptu_duzeltme():
    msj = D.mesajlar([(1, "cenkinse bakarız")], ["Jenkins"], [], [("cenkins", "Jenkins")])
    assert llm._gorev_adi(msj) == "duzeltme"
    assert "BİLİNEN YAZIMLAR" in D.DUZELT_SISTEM and D.DUZELT_SISTEM.startswith(llm.DUZELT_ONEK)
    assert llm._gorev_adi([{"role": "system", "content": "  " + D.DUZELT_SISTEM}]) == "duzeltme"
    assert llm._gorev_adi([{"role": "system", "content": llm.MAP_SISTEM}]) == "bolum"
    assert llm._gorev_adi([{"role": "system", "content": llm.LISTE_SISTEM}]) == "liste"


# ---------------------------------------------------------------- 2) (?) kodla basilir; acik sorular silmez

def test_acik_sorulari_temizle_kodun_belirsizlik_isaretini_silmez():
    sorular = ["Bütçe onayı kimden alınacak (?)",                  # kodun ' (?)' isareti: soru kalir
               "Pilot hangi şubede başlayacak (?)",
               "Varun(?) kimdir?",                                  # modelin kendi anlama sorusu: atilir
               "Rapor sunucusu nedir? Varun(?) nedir"]
    v = llm.acik_sorulari_temizle(sorular)
    assert v == ["Bütçe onayı kimden alınacak (?)", "Pilot hangi şubede başlayacak (?)"]


def test_karar_aksiyona_tasininca_belirsizlik_isareti_kaybolmaz():
    listeler = {"kararlar": ["Girdi tabloları Kemal tarafından paylaşılacak (?)"], "aksiyonlar": [], "acik_sorular": []}
    v = llm.karar_aksiyon_ayikla(listeler, ["Selin Kara", "Kemal Tuncer"])
    assert v["aksiyonlar"][0]["madde"] == "Girdi tabloları Kemal tarafından paylaşılacak (?)"
    assert v["aksiyonlar"][0]["sorumlu"] == "Kemal Tuncer"


def test_kisi_bazli_sahibi_belirsiz_kisinin_isi_sayilmaz():
    g = dict(llm.kisi_bazli([{"madde": "Raporu hazırla", "sorumlu": "Selin Kara", "tarih": "-"},
                             {"madde": "Tabloyu gönder", "sorumlu": "Kemal Tuncer (?)", "tarih": "-"},
                             {"madde": "Sunumu güncelle", "sorumlu": "belirsiz", "tarih": "-"}]))
    assert g["Selin Kara"] == ["Raporu hazırla"] and "Kemal Tuncer" not in g and "Kemal Tuncer (?)" not in g
    assert g["belirsiz"] == ["Tabloyu gönder (aday: Kemal Tuncer)", "Sunumu güncelle"]


def test_belirsiz_aksiyonlari_ele_maddesi_none_olsa_da_cokmez():
    bul = lambda metin, sorumlu=None: None
    aks = [{"madde": None, "sorumlu": "belirsiz", "tarih": "-"},
           {"madde": "Rapor yaz", "sorumlu": "belirsiz", "tarih": "-"},
           {"madde": "Raporu cuma gününe kadar hazırla", "sorumlu": "belirsiz", "tarih": "-"}]
    assert [a["madde"] for a in llm.belirsiz_aksiyonlari_ele(aks, bul)] == ["Raporu cuma gününe kadar hazırla"]


def test_yedek_sonraki_adim_isaretli_sahipli_aksiyon_sona_kalir():
    s = llm.yedek_sonraki_adim({"aksiyonlar": [{"madde": "A işi", "sorumlu": "Kemal Tuncer (?)"},
                                               {"madde": "B işi", "sorumlu": "Selin Kara"}]})
    assert s.index("B işi") < s.index("A işi")


# ---------------------------------------------------------------- 3) kademeli liste birlestirme

def _kalem(i):
    """Birbirine benzemeyen (listeleri_tekille %85 benzerlikle birlestirmesin) sentetik is tanimi."""
    rng = random.Random(i)
    return " ".join("".join(rng.choice("abcdefghklmnoprstuvyz") for _ in range(7)) for _ in range(3)) + f" {i}"


def _uzun_toplanti(bolum=12, kalem=20):
    """bolum x kalem benzersiz aksiyon (toplam 240 > LISTE_GRUP_MADDE)."""
    bolumler, n = [], 0
    for _ in range(bolum):
        aks = []
        for _ in range(kalem):
            n += 1
            aks.append({"madde": _kalem(n), "sorumlu": "Selin Kara", "tarih": "-"})
        bolumler.append({"ozet": "x", "konular": [], "kararlar": [], "aksiyonlar": aks, "acik_sorular": []})
    return bolumler


class SahteBirlestirici:
    """llm.sor yerine: kullanici mesajindaki '- [B3] madde — sorumlu — tarih' satirlarini ayni ada birlestirir (kimlik)."""

    def __init__(self, bozuk=None):
        self.cagrilar = []
        self.bozuk = bozuk or (lambda sira, mesajlar: False)

    def __call__(self, mesajlar, max_tokens, sema=None, **kw):
        self.cagrilar.append(mesajlar)
        if mesajlar[0]["content"] != llm.LISTE_SISTEM:
            return "{}", "stop"
        if self.bozuk(len(self.cagrilar), mesajlar):
            return "{ yarim", "length"
        icerik = mesajlar[1]["content"]
        aks, gorulen = [], set()
        for m in re.finditer(r"^- \[B\d+\] (.+?) — (.+?) — (.+)$", icerik, re.M):
            if m.group(1) not in gorulen:
                gorulen.add(m.group(1))
                aks.append({"madde": m.group(1), "sorumlu": m.group(2), "tarih": m.group(3)})
        return json.dumps({"kararlar": [], "aksiyonlar": aks, "acik_sorular": []}, ensure_ascii=False), "stop"


def test_170_arti_madde_kademeli_birlesir_yedege_dusmez(monkeypatch):
    monkeypatch.setattr(llm, "BAGLAM_PENCERESI", 32768)
    fake = SahteBirlestirici()
    monkeypatch.setattr(llm, "sor", fake)
    log = []
    sonuc = llm.listeleri_birlestir(_uzun_toplanti(), "Toplantı: test", log.append)
    assert len(sonuc["aksiyonlar"]) == 240                                    # hicbiri kaybolmadi
    assert len(fake.cagrilar) >= 4                                            # >=3 grup + son birlestirme
    assert any("grupta birleştiriliyor" in m for m in log)
    assert not any("yerel birleştirme" in m for m in log)                     # sessiz/yerel yedek YOK
    # her ilk kademe istegi en fazla LISTE_GRUP_MADDE madde tasir; son istek grup sonuclarini birlestirir
    adetler = [len(re.findall(r"^- \[B\d+\]", c[1]["content"], re.M)) for c in fake.cagrilar]
    assert max(adetler[:-1]) <= llm.LISTE_GRUP_MADDE and adetler[-1] == 240


def test_kademeli_birlestirme_bozuk_grup_yalniz_o_grubu_yedege_dusurur_ve_loglar(monkeypatch):
    monkeypatch.setattr(llm, "BAGLAM_PENCERESI", 32768)
    gunluk = []
    monkeypatch.setattr(llm, "GUNLUK_FN", gunluk.append)
    monkeypatch.setattr(llm, "sor", SahteBirlestirici(bozuk=lambda sira, m: sira in (2, 3)))      # _json_tam iki kez dener
    log = []
    sonuc = llm.listeleri_birlestir(_uzun_toplanti(), "Toplantı: test", log.append)
    assert len(sonuc["aksiyonlar"]) == 240
    assert sum("yerel birleştirme kullanıldı" in m for m in log) == 1          # yalniz 2. grup
    olay = [g for g in gunluk if g.get("olay") == "liste_yedek"]
    assert len(olay) == 1 and "kesildi/bozuk" in olay[0]["neden"]


class KayipliBirlestirici(SahteBirlestirici):
    """Her istekte aksiyonlarin %40'ini atar ve acik sorulari bos dondurur (esigin altinda kalan, birikimli kayip)."""

    def __call__(self, mesajlar, max_tokens, sema=None, **kw):
        cevap, bitis = super().__call__(mesajlar, max_tokens, sema, **kw)
        if mesajlar[0]["content"] != llm.LISTE_SISTEM:
            return cevap, bitis
        v = json.loads(cevap)
        v["aksiyonlar"] = [a for i, a in enumerate(v["aksiyonlar"]) if i % 5 not in (0, 1)]
        return json.dumps(v, ensure_ascii=False), bitis


def test_kademeli_birlestirmede_kayip_turlar_boyunca_birikmez_acik_sorular_korunur(monkeypatch):
    """24 bolum x 10 aksiyon + 24 acik soru, 16k pencere; model her istekte %40 atar, sorulari bosaltir. Eskiden her grup
    kendi esigini (yarisi) gectigi icin 240 -> 76 madde ve 0 soru kalir, 'liste_yedek' bile yazilmazdi."""
    monkeypatch.setattr(llm, "BAGLAM_PENCERESI", 16384)
    gunluk = []
    monkeypatch.setattr(llm, "GUNLUK_FN", gunluk.append)
    fake = KayipliBirlestirici()
    monkeypatch.setattr(llm, "sor", fake)
    bolumler = _uzun_toplanti(24, 10)
    for i, b in enumerate(bolumler):
        b["acik_sorular"] = [f"{_kalem(1000 + i)} ne zaman netleşecek?"]
    log = []
    sonuc = llm.listeleri_birlestir(bolumler, "Toplantı: test", log.append)
    assert len(fake.cagrilar) >= 3 and any("grupta birleştiriliyor" in m for m in log)
    assert sorted(a["madde"] for a in sonuc["aksiyonlar"]) == sorted(
        a["madde"] for b in bolumler for a in b["aksiyonlar"])                     # hicbiri kaybolmadi
    assert len(sonuc["acik_sorular"]) == 24
    geri = [g for g in gunluk if g.get("olay") == "liste_geri_ekle"]
    assert geri and all(g["eksik"] >= 1 for g in geri)                            # kayip sessiz degil
    assert any("maddeyi düşürdü; maddeler geri eklendi" in m for m in log)
    assert any(g.get("olay") == "liste_soru_geri" for g in gunluk)


def test_karsilanmayanlar_kisaltma_ve_birlestirmeyi_kayip_saymaz():
    girdi = {"kararlar": ["Kampanya bütçesi 300 bin TL olarak onaylandı"],
             "aksiyonlar": [{"madde": "Sunucu 3 güncellemesi yapılacak", "sorumlu": "Ali Demir", "tarih": "-"},
                            {"madde": "Test planı paylaşılacak", "sorumlu": "belirsiz", "tarih": "-"}]}
    cikti = {"kararlar": ["Kampanya bütçesi 300 bin TL onaylandı"],                      # kisaltma
             "aksiyonlar": [{"madde": "Sunucu 4 güncellemesi yapılacak", "sorumlu": "Ali Demir", "tarih": "-"},
                            {"madde": "Test planı ve sunum paylaşılacak", "sorumlu": "belirsiz", "tarih": "-"}]}
    eksik = llm._karsilanmayanlar(girdi, cikti)
    assert eksik["kararlar"] == [] and [a["madde"] for a in eksik["aksiyonlar"]] == ["Sunucu 3 güncellemesi yapılacak"]


def test_listeleri_tekille_sayisi_ya_da_sorumlusu_farkli_maddeyi_yutmaz():
    """Yerel birlestirme: yalniz sayisi farkli kararlar ve sorumlusu farkli aksiyonlar ayri kalir (tutar / sahip kaybi)."""
    b1 = {"kararlar": ["Kampanya bütçesi 300 bin TL olarak onaylandı"],
          "aksiyonlar": [{"madde": "Sunucu 3 güncellemesi yapılacak", "sorumlu": "Ali Demir", "tarih": "-"}],
          "acik_sorular": []}
    b2 = {"kararlar": ["Kampanya bütçesi 500 bin TL olarak onaylandı"],
          "aksiyonlar": [{"madde": "Sunucu 4 güncellemesi yapılacak", "sorumlu": "Ayşe Kaya", "tarih": "-"}],
          "acik_sorular": []}
    v = llm.listeleri_tekille([b1, b2])
    assert v["kararlar"] == ["Kampanya bütçesi 300 bin TL olarak onaylandı", "Kampanya bütçesi 500 bin TL olarak onaylandı"]
    assert [(a["madde"], a["sorumlu"]) for a in v["aksiyonlar"]] == [
        ("Sunucu 3 güncellemesi yapılacak", "Ali Demir"), ("Sunucu 4 güncellemesi yapılacak", "Ayşe Kaya")]
    # ayni madde, farkli sorumlu: ayri; ayni madde, biri belirsiz: birlesir (sorumlu oncekinden)
    b3 = {"aksiyonlar": [{"madde": "Sunucu 3 güncellemesi yapılacak", "sorumlu": "Ayşe Kaya", "tarih": "-"}]}
    b4 = {"aksiyonlar": [{"madde": "Sunucu 3 güncellemesi yapılacak", "sorumlu": "belirsiz", "tarih": "cuma"}]}
    assert len(llm.listeleri_tekille([b1, b3])["aksiyonlar"]) == 2
    tek = llm.listeleri_tekille([b1, b4])["aksiyonlar"]
    assert tek == [{"madde": "Sunucu 3 güncellemesi yapılacak", "sorumlu": "Ali Demir", "tarih": "cuma"}]
    # metin ayni, sayi ayni: yine tek madde
    assert len(llm.listeleri_tekille([b1, b1])["kararlar"]) == 1
    assert not llm._benzer("Sunucu 3 güncellemesi", "Sunucu 4 güncellemesi") and llm._benzer("Rapor 3 hazır", "Rapor 3 hazır.")


def test_kucuk_liste_tek_istek_ve_uc_madde_altinda_istek_yok(monkeypatch):
    fake = SahteBirlestirici()
    monkeypatch.setattr(llm, "sor", fake)
    llm.listeleri_birlestir(_uzun_toplanti(2, 10), "Toplantı: test", lambda m: None)
    assert len(fake.cagrilar) == 1
    sonuc = llm.listeleri_birlestir(_uzun_toplanti(1, 2), "Toplantı: test", lambda m: None)
    assert len(fake.cagrilar) == 1 and len(sonuc["aksiyonlar"]) == 2


def test_gruplar_dar_pencerede_token_sinirina_gore_bolunur(monkeypatch):
    monkeypatch.setattr(llm, "BAGLAM_PENCERESI", 8192)
    fake = SahteBirlestirici()
    monkeypatch.setattr(llm, "sor", fake)
    log = []
    sonuc = llm.listeleri_birlestir(_uzun_toplanti(8, 8), "Toplantı: test", log.append)      # 64 madde: tek grup siger
    assert len(sonuc["aksiyonlar"]) == 64
    gruplar = llm._liste_gruplari(_uzun_toplanti(40, 8), "Toplantı: test")
    assert len(gruplar) > 1 and sum(len(g) for g in gruplar) == 40
    assert [i for g in gruplar for i, _ in g] == list(range(40))               # kronoloji korunur


def test_tek_bolum_bile_sigmiyorsa_yedek_loglanir(monkeypatch):
    monkeypatch.setattr(llm, "BAGLAM_PENCERESI", 8192)
    monkeypatch.setattr(llm, "BUTCE_AYAR", {"liste": 600})                      # istek basina ~450 token girdi
    monkeypatch.setattr(llm, "sor", SahteBirlestirici())
    log = []
    sonuc = llm.listeleri_birlestir(_uzun_toplanti(3, 60), "Toplantı: test", log.append)   # her bolum tek basina buyuk
    assert any("kademeli birleştirmeye de sığmadı" in m for m in log)
    assert len(sonuc["aksiyonlar"]) == 180 and all(a["sorumlu"] == "Selin Kara" for a in sonuc["aksiyonlar"])


# ---------------------------------------------------------------- 4) parca ortusmesi

def test_onceki_baglam_son_6_satir_ve_acik_maddeler_400_token(monkeypatch):
    satirlar = [f"Ahmet: satır numarası {i} " + "uzun " * 30 for i in range(1, 11)]
    bag = llm.onceki_baglam_hazirla(["Mimari"], satirlar, ["Bütçe onayı kimden alınacak?", "Pilot nerede?", "c", "d"])
    assert bag["konular"] == ["Mimari"] and len(bag["acik"]) == 3
    toplam = len(bag["satirlar"]) + sum(len(a) + 4 for a in bag["acik"])
    assert toplam <= llm.ONCEKI_BAGLAM_TOKEN * 3 + 5                          # ~400 token (len//3)
    kalan = bag["satirlar"].splitlines()
    assert 1 <= len(kalan) <= llm.ONCEKI_SATIR and kalan[-1].startswith("Ahmet: satır numarası 10")   # sondan kirpilir
    kisa = llm.onceki_baglam_hazirla(None, [f"Ali: {i}" for i in range(10)], None)
    assert kisa["satirlar"].splitlines() == [f"Ali: {i}" for i in range(4, 10)] and kisa["acik"] == []


def test_bolum_ozetle_onceki_baglami_yalniz_baglam_olarak_verir(monkeypatch):
    gorulen = []

    def json_sor(mesajlar, *a, **k):
        gorulen.append(mesajlar)
        return {"ozet": "x", "konular": [], "kararlar": [], "aksiyonlar": [], "acik_sorular": [], "belirsiz_terimler": []}

    monkeypatch.setattr(llm, "_json_sor", json_sor)
    bag = llm.onceki_baglam_hazirla(["Mimari"], ["Ali: bütçeyi sonra konuşalım", "Selin: tamam"], ["Bütçe kimde?"])
    llm.bolum_ozetle("Ali: bütçe onaylandı", 2, "Toplantı: test", bag)
    kullanici = gorulen[0][1]["content"]
    assert "Önceki bölümde konuşulanlar: Mimari" in kullanici and "cevapsız kalanlar: Bütçe kimde?" in kullanici
    assert "YALNIZ bağlam" in kullanici and "Ali: bütçeyi sonra konuşalım" in kullanici
    assert kullanici.index("Ali: bütçeyi sonra") < kullanici.index("Toplantının 2. bölümü")
    # eski bicim (konu listesi) calismaya devam eder, satir eklenmez
    llm.bolum_ozetle("Ali: bütçe onaylandı", 2, "Toplantı: test", ["Mimari"])
    assert "YALNIZ bağlam" not in gorulen[1][1]["content"] and "Mimari" in gorulen[1][1]["content"]


def _motor(tmp_path, havuz=None, **kw):
    m = motor_mod.Motor(str(tmp_path / "toplantilar" / "2026-10-05_test"), "Test", dt.date(2026, 10, 5),
                        havuz or terim_mod.TerimHavuzu(str(tmp_path / "terimler" / "terimler.json")),
                        kw.pop("katilimcilar", [A, B]), "", duzelt=kw.pop("duzelt", True), ben=BEN, **kw)
    m.konusmaci = SahteKonusmaci()
    return m


def _satir(i, metin, speaker=A, **ek):
    s = {"id": i, "ts": f"10:00:{i:02d}", "speaker": speaker, "text": metin, "raw": metin, "surum": 2,
         "spk_guven": 0.9, "bas": T0 + i, "bit": T0 + i + 1}
    s.update(ek)
    return s


def test_motor_onceki_baglam_son_6_satir_supheli_haric_ve_acik_maddeler(tmp_path):
    m = _motor(tmp_path)
    satirlar = [_satir(i, f"satır {i} burada konuşuldu") for i in range(1, 11)]
    satirlar[9]["supheli"] = True                                              # 10. satir nota girmez
    m.parca_yaz({"sira": 1, "neden": "token", "token": 900, "baslangic": "10:00:00", "bitis": "10:00:10",
                 "satirlar": satirlar, "duzeltmeler": [],
                 "ozet": {"ozet": "x", "konular": ["Mimari"], "kararlar": [], "aksiyonlar": [],
                          "acik_sorular": ["Bütçe onayı kimden alınacak?"], "belirsiz_terimler": []}})
    m.parca_no = 1
    assert m._onceki_baglam(1) is None
    bag = m._onceki_baglam(2)
    metin = bag["satirlar"]
    assert [s for s in metin.splitlines() if "satır" in s] == [f"{A}: satır {i} burada konuşuldu" for i in range(4, 10)]
    assert "satır 10" not in metin and bag["konular"] == ["Mimari"] and bag["acik"] == ["Bütçe onayı kimden alınacak?"]
    m.kapat()


def test_motor_isle_onceki_baglami_ozetleyiciye_gecirir(tmp_path, monkeypatch):
    m = _motor(tmp_path, duzelt=False)
    m.parca_yaz({"sira": 1, "neden": "token", "token": 900, "baslangic": "10:00:00", "bitis": "10:00:10",
                 "satirlar": [_satir(1, "bütçeyi sonra konuşalım")], "duzeltmeler": [],
                 "ozet": {"ozet": "x", "konular": ["Mimari"], "kararlar": [], "aksiyonlar": [],
                          "acik_sorular": [], "belirsiz_terimler": []}})
    veri2 = {"sira": 2, "neden": "token", "token": 900, "baslangic": "10:01:00", "bitis": "10:01:10",
             "satirlar": [_satir(2, "bütçe onaylandı")], "duzeltmeler": [], "ozet": None}
    m.parca_yaz(veri2)
    m.parca_no = 2
    gorulen = []
    monkeypatch.setattr(llm, "bolum_ozetle", lambda blok, sira, baglam, onceki=None: (
        gorulen.append(onceki), llm.normalize_ozet({"ozet": "y"}))[1])
    m._isle(m.parca_oku(2), False)
    assert isinstance(gorulen[0], dict) and "bütçeyi sonra konuşalım" in gorulen[0]["satirlar"]
    assert gorulen[0]["konular"] == ["Mimari"]
    m.kapat()


# ---------------------------------------------------------------- 5) thinking_token_budget

def _cevap(icerik="{}", neden="stop"):
    mesaj = types.SimpleNamespace(content=icerik, reasoning_content=None)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(finish_reason=neden, message=mesaj)],
                                 usage=types.SimpleNamespace(prompt_tokens=100, completion_tokens=50))


def _istek_hatasi(kod, govde):
    istek = httpx.Request("POST", "http://llm.ornek/v1/chat/completions")
    yanit = httpx.Response(kod, request=istek, content=json.dumps(govde).encode(),
                           headers={"content-type": "application/json"})
    sinif = openai.BadRequestError if kod == 400 else openai.UnprocessableEntityError
    return sinif(f"Error code: {kod}", response=yanit, body=govde)


def _istemci(monkeypatch, cevaplar, **cfg):
    llm.ayarla(dict({"route": "http://llm.ornek/v1", "model": "m", "llm_akis": False}, **cfg))
    monkeypatch.setattr(llm, "OTOMATIK_TANI", False)
    gelen = []

    def create(**kw):
        gelen.append(kw)
        c = cevaplar[min(len(gelen) - 1, len(cevaplar) - 1)]
        if isinstance(c, Exception):
            raise c
        return c

    monkeypatch.setattr(llm.client.chat.completions, "create", create)
    return gelen


MESAJ = [{"role": "user", "content": "x"}]


def test_dusunmeli_cagri_thinking_token_budget_gonderir(monkeypatch):
    gelen = _istemci(monkeypatch, [_cevap()], llm_dusunme_butcesi=800)
    llm.sor(MESAJ, 3000, dusunme=True)
    eb = gelen[0]["extra_body"]
    assert eb["thinking_token_budget"] == 800 and eb["chat_template_kwargs"]["reasoning_effort"] == "medium"
    # max_tokens kucukse butce cevaba en az %40 birakir
    gelen.clear()
    llm.sor(MESAJ, 1000, dusunme=True)
    assert gelen[0]["extra_body"]["thinking_token_budget"] == 600
    # cok kucuk max_tokens: butce gonderilmez (cevap sigsin)
    gelen.clear()
    llm.sor(MESAJ, 80, dusunme=True)
    assert "thinking_token_budget" not in gelen[0]["extra_body"]


def test_dusunmesiz_cagri_ve_sifir_ayar_butce_gondermez(monkeypatch):
    gelen = _istemci(monkeypatch, [_cevap()])
    llm.sor(MESAJ, 3000, dusunme=False)
    assert "thinking_token_budget" not in gelen[0]["extra_body"]
    assert gelen[0]["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert llm.DUSUNME_BUTCESI == llm.VARSAYILAN_DUSUNME_BUTCESI
    gelen = _istemci(monkeypatch, [_cevap()], llm_dusunme_butcesi=0)
    llm.sor(MESAJ, 3000, dusunme=True)
    assert llm.DUSUNME_BUTCESI == 0 and "thinking_token_budget" not in gelen[0]["extra_body"]


def test_sunucu_alani_reddederse_bir_kez_bildirilir_ve_bir_daha_gonderilmez(monkeypatch):
    uyarilar = []
    monkeypatch.setattr(llm, "UYARI_FN", uyarilar.append)
    gunluk = []
    monkeypatch.setattr(llm, "GUNLUK_FN", gunluk.append)
    ret = _istek_hatasi(400, {"message": "thinking_token_budget\n  Extra inputs are not permitted"})
    gelen = _istemci(monkeypatch, [ret, _cevap("ok"), _cevap("ikinci")])
    metin, _ = llm.sor(MESAJ, 3000, dusunme=True)
    assert metin == "ok" and len(gelen) == 2
    assert "thinking_token_budget" in gelen[0]["extra_body"] and "thinking_token_budget" not in gelen[1]["extra_body"]
    assert llm._BUTCE_DESTEGI is False and len(uyarilar) == 1 and "thinking_token_budget" in uyarilar[0]
    llm.sor(MESAJ, 3000, dusunme=True)
    assert "thinking_token_budget" not in gelen[2]["extra_body"] and len(uyarilar) == 1
    assert sum(1 for g in gunluk if g.get("olay") == "dusunme_butcesi_yok") == 1
    llm.ayarla({"route": "http://llm.ornek/v1"})                               # ayarla destegi sifirlar
    assert llm._BUTCE_DESTEGI is True


def test_alani_anmayan_400_alansiz_tekrar_basarirsa_butce_kapanir(monkeypatch):
    uyarilar = []
    monkeypatch.setattr(llm, "UYARI_FN", uyarilar.append)
    gelen = _istemci(monkeypatch, [_istek_hatasi(400, {"message": "bad request"}), _cevap("ok")])
    assert llm.sor(MESAJ, 3000, dusunme=True)[0] == "ok"
    assert "thinking_token_budget" not in gelen[1]["extra_body"] and llm._BUTCE_DESTEGI is False and len(uyarilar) == 1


def test_baglam_400u_butce_denemesine_harcanmaz_ve_yukselir(monkeypatch):
    govde = {"message": "This model's maximum context length is 8192 tokens. However, you requested 9000 tokens"}
    gelen = _istemci(monkeypatch, [_istek_hatasi(400, govde), _istek_hatasi(400, govde)], context=16384)
    with pytest.raises(llm.LlmHatasi):
        llm.sor(MESAJ, 3000, dusunme=True)
    assert llm._BUTCE_DESTEGI is True                                          # baglam hatasi alan nedeni sayilmadi
    assert llm.BAGLAM_PENCERESI == 8192 and len(gelen) == 2                    # pencere ogrenildi, bir kez tekrar


def test_json_sor_dusunme_butcesi_yenmis_bos_cevapta_dusunmesiz_tekrar(monkeypatch):
    """16 tokenlik denemede content null: dusunme butceyi yedi. Mevcut yedek korunur: dusunmesiz, daha genis tekrar."""
    gelen = _istemci(monkeypatch, [_cevap(None, "length"), _cevap('{"a": 1}')])
    sema = {"type": "object", "required": ["a"]}
    assert llm._json_sor(MESAJ, 2000, sema, dusunme=True) == {"a": 1}
    assert "thinking_token_budget" in gelen[0]["extra_body"]
    assert gelen[1]["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert "thinking_token_budget" not in gelen[1]["extra_body"] and gelen[1]["max_tokens"] == 3000


# ---------------------------------------------------------------- 6) kucuk hatalar (llm.py)

def test_json_ayikla_dizge_icindeki_virgulu_bozmaz_ve_arkadaki_metni_atar():
    assert llm.json_ayikla('```json\n{"a": ["x, }", "y"],}\n```') == {"a": ["x, }", "y"]}
    assert llm.json_ayikla('{"a": 1} Not: {b} diye bitirdim') == {"a": 1}
    assert llm.json_ayikla('{"a": "x, ]ok", "b": [1, 2,],}') == {"a": "x, ]ok", "b": [1, 2]}
    assert llm.json_ayikla('<think>{x}</think>\n{"k": "v"}') == {"k": "v"}
    with pytest.raises(ValueError):
        llm.json_ayikla("json yok")


def test_tani_ulasilamazsa_bir_sure_sonra_yeniden_sorulur(monkeypatch):
    llm.ayarla({"route": "http://llm.ornek/v1", "model": "x"})
    istekler = []

    class Http:
        def __init__(self, hata):
            self.hata = hata

        def get(self, url, timeout=None):
            istekler.append(url)
            if self.hata:
                raise self.hata
            return httpx.Response(200, json={"data": [{"id": "yeni", "max_model_len": 12000}]},
                                  request=httpx.Request("GET", url))

    monkeypatch.setattr(llm, "_http", Http(httpx.ConnectError("ag yok")))
    llm.sunucuyu_tani()
    llm.sunucuyu_tani()
    assert len(istekler) == 1                                                  # sure dolmadan yeniden sorulmaz
    zaman = [llm._TANI_TEKRAR_SONA + 1]
    monkeypatch.setattr(llm.time, "time", lambda: zaman[0])
    monkeypatch.setattr(llm, "_http", Http(None))
    llm.sunucuyu_tani()
    assert len(istekler) == 2 and llm.MODEL == "yeni" and llm.BAGLAM_PENCERESI == 12000
    llm.sunucuyu_tani()
    assert len(istekler) == 2                                                  # basarili tani kalicidir


def test_ayarla_route_str_olmasa_da_cokmez():
    llm.ayarla({"route": 8000})
    assert llm.ROUTE.endswith("/v1")
    llm.ayarla({"route": None})
    assert llm.ROUTE == "http://localhost:8000/v1"


# ---------------------------------------------------------------- 7) ogrenilmis varyantlar: duzelt kapisi

TERIMLER = ["Jenkins", "Jira", "Kubernetes"]


def _o(eski, yeni, neden="terim", satir=1):
    return {"satir": satir, "eski": eski, "yeni": yeni, "neden": neden}


def test_kapi_ogrenilmis_varyanti_izinli_sayar_ama_ek_ve_sayi_kurallari_surer():
    s = "kira kaydı açtım"
    assert D.kapi(s, _o("kira", "Jira"), TERIMLER, []) == (False, "yaygin_kelime")      # varyantsiz reddedilir
    v = [("kira", "Jira")]
    assert D.kapi(s, _o("kira", "Jira"), TERIMLER, [], v) == (True, "varyant")
    assert D.kapi("kira'ya kayıt aç", _o("kira'ya", "Jira'ya"), TERIMLER, [], v) == (True, "varyant")
    assert D.kapi("kuber kümesi", _o("kuber", "Kubernetes"), TERIMLER, [], [("kuber", "Kubernetes")]) == (True, "varyant")
    # ek kurali: ozel ada kesmesiz ek reddedilir
    assert D.kapi("kirayı aç", _o("kirayı", "Jirayı"), TERIMLER, [], v) == (False, "kesme_isareti")
    # varyanti baska terime cevirmek yok; neden 'ad' varyanti kullanmaz
    assert D.kapi(s, _o("kira", "Jenkins"), TERIMLER, [], v)[0] is False
    assert D.kapi(s, _o("kira", "Jira", "ad"), TERIMLER, ["Jira Ak"], v)[0] is False
    # ek kaybi: ekli varyant ekini silerek terime cevrilemez (e2e ogrenme kapisi bunu yakaladi); ekiyle cevrilir
    ev = [("kubernetisin", "Kubernetes")]
    assert D.kapi("kubernetisin düştü", _o("kubernetisin", "Kubernetes"), TERIMLER, [], ev) == (False, "ek_kaybi")
    assert D.kapi("kubernetisin düştü", _o("kubernetisin", "Kubernetes'in"), TERIMLER, [], ev) == (True, "varyant")
    # varyant yalniz kendi terimine izin verir: 'kira' -> baska aday terim varyant kolayligi tasimaz
    assert D.kapi("kira kaydı", _o("kira", "Jira kaydı"), TERIMLER + ["Jira kaydı"], [], v) == (False, "uzaklik")
    # sayi / kelime sayisi / tek gecis kurallari AYNEN
    assert D.kapi("kira 5 kayıt", _o("kira 5", "Jira 6"), TERIMLER, [], v) == (False, "sayi_degisti")
    assert D.kapi("kira kira", _o("kira", "Jira"), TERIMLER, [], v) == (False, "coklu_gecis")
    assert D.kapi("bir kira kaydı aç şimdi hemen", _o("bir kira kaydı", "Jira"), TERIMLER, [], v)[0] is False


def test_kapi_varyantin_kisa_koku_ve_yaygin_kok_terime_cevrilmez():
    """Ogrenilmis varyant ('cenkinse' -> Jenkins) yalniz kendisini, kendisi + eki ve kendi kokunu ('cenkins') + eki izinli
    kilar; kok_ayir ile turetilen daha kisa kok ('cenk') ve yaygin kok ('volt', 'tablosu') varyant sayilmaz."""
    t = ["Jenkins", "Tableau", "Vault"]
    for satir, eski, yeni, v in (("Dün akşam arkadaşlarla cenk ettik", "cenk", "Jenkins", ("cenkinse", "Jenkins")),
                                 ("Hesap tablosu açık", "tablosu", "Tableau'su", ("tablosuna", "Tableau")),
                                 ("Şebeke 220 volt veriyor", "volt", "Vault", ("voltu", "Vault"))):
        varyantsiz = D.kapi(satir, _o(eski, yeni), t, [])
        assert varyantsiz[0] is False, (eski, varyantsiz)
        assert D.kapi(satir, _o(eski, yeni), t, [], [v]) == varyantsiz, eski       # varyant kapiyi gevsetmez
    v = [("cenkinse", "Jenkins")]
    assert D.kapi("cenkinse bağlan", _o("cenkinse", "Jenkins'e"), t, [], v) == (True, "varyant")
    assert D.kapi("cenkinsin hattı", _o("cenkinsin", "Jenkins'in"), t, [], v) == (True, "varyant")   # kendi koku + ek
    assert D.kapi("voltu aç", _o("voltu", "Vault'u"), t, [], [("voltu", "Vault")]) == (True, "varyant")


def test_duzelt_varyanti_adaylara_ekler_ve_promptta_bilinen_yazimi_gosterir():
    cagrilar = []

    def sor(mesajlar, sema):
        cagrilar.append(mesajlar)
        return {"oneriler": [_o("kira", "Jira")]}

    satirlar = [{"id": 1, "raw": "kira kaydı açtım, kubernetis de düştü"}]
    yeni, rapor = D.duzelt(satirlar, ["Kubernetes"], [], sor_fn=sor, varyantlar=[("kira", "Jira")])
    assert yeni[0]["text"] == "Jira kaydı açtım, kubernetis de düştü" and rapor["kabul"] == 1
    kullanici = cagrilar[0][1]["content"]
    assert "BİLİNEN YAZIMLAR: kira -> Jira" in kullanici and "Jira" in kullanici.split("TERİMLER:")[1].splitlines()[0]
    # metinde gecmeyen varyant promptu sismez
    cagrilar.clear()
    D.duzelt([{"id": 1, "raw": "hiçbir şey yok"}], ["Kubernetes"], [], sor_fn=sor, varyantlar=[("kira", "Jira")])
    assert "BİLİNEN YAZIMLAR" not in cagrilar[0][1]["content"]
    # varyantsiz eski cagri: kapi eskisi gibi reddeder
    yeni, rapor = D.duzelt(satirlar, ["Jira"], [], sor_fn=sor)
    assert yeni[0]["text"] == satirlar[0]["raw"] and rapor["kabul"] == 0


def _havuz(tmp_path):
    return terim_mod.TerimHavuzu(str(tmp_path / "terimler" / "terimler.json"))


def _varyantli_havuz(tmp_path, seri="test"):
    h = _havuz(tmp_path)
    h.ekle(seri, ["Jira", "Kubernetes"], "davet")
    e = h._bul("Jira")
    e["varyantlar"].append("kira")
    e["ogrenilen"].append("kira")
    return h


def test_terim_varyant_ciftleri_ve_adaylar_sirasi(tmp_path):
    h = _varyantli_havuz(tmp_path)
    assert h.varyant_ciftleri("test") == [("kira", "Jira")]
    assert h.varyant_ciftleri("baska-seri") == []                              # seri kapsami (genel degil)
    e = h._bul("Jira")
    e["genel"] = True
    assert h.varyant_ciftleri("baska-seri") == [("kira", "Jira")]
    # Whisper ipucu adaylari: bozuk duyulmasi bilinen terim one gecer
    h.ekle("test", ["Aaa Modülü", "Bbb Portal"], "davet")
    assert h.adaylar("test")[0] == "Jira"
    assert h.prompt("test", ["Selin Kara"]).split("Terimler: ")[1].startswith("Jira")
    # ham metinde varyant gecince terim metin eslesmesi sayilir (baska seride bile)
    h2 = _havuz(tmp_path)
    h2.ekle("a", ["Jira"], "davet")
    h2._bul("Jira")["varyantlar"].append("kira")
    assert "Jira" in h2.adaylar("b", "dün kira kaydı açtık")
    assert "Jira" not in h2.adaylar("b", "dün bir kayıt açtık")
    # elle.txt varyantlari da izinli eslemedir
    (tmp_path / "terimler").mkdir(exist_ok=True)
    (tmp_path / "terimler" / "elle.txt").write_text("Sharepoint | şerpoynt; sherpoint\n", encoding="utf-8")
    assert ("sherpoint", "Sharepoint") in h.varyant_ciftleri("herhangi")


def test_katilimci_soyadi_ve_kisi_girisi_terim_olmaz(tmp_path):
    h = _havuz(tmp_path)
    adlar = ["Selin Kara", "Kemal Tuncer"]
    eklenen = h.ekle("s", ["Kara", "Selin Kara", "Tuncer", "Kara Kutu", "Jenkins"], "davet", adlar=adlar)
    assert sorted(eklenen) == ["Jenkins", "Kara Kutu"]                         # yalniz soyad / ad ifadesi elendi
    h.ekle("s", adlar, "kisi")                                                 # tam ad kisi olarak girer ...
    assert h._bul("Selin Kara") is not None
    # ... ama baska toplantida (ya da ad listesi verilmeden) terim adayi olmaz
    assert "Selin Kara" not in h.adaylar("s") and "Kemal Tuncer" not in h.adaylar("s", "Kemal Tuncer dedi")
    # havuzda duz terim olarak kalmis soyad adaylardan ve ipucundan atilir
    h.ekle("s", ["Kara"], "davet")
    assert "Kara" in h.adaylar("s") and "Kara" not in h.adaylar("s", adlar=adlar)
    assert "Kara" not in h.prompt("s", adlar).split("Terimler: ")[-1].rstrip(".").split(", ")
    assert ("Kara", "Kara") not in h.varyant_ciftleri("s", adlar)


def test_motor_terimleri_hazirla_soyadi_havuza_eklemez(tmp_path):
    m = _motor(tmp_path, katilimcilar=["Selin Kara", "Kemal Tuncer"],
               davet={"govde": "Toplantı Jenkins geçişi hakkında. Kara'nın önerisi görüşülecek."})
    m.terimleri_hazirla()
    terimler = [e["terim"] for e in m.terimler.terimler]
    assert "Kara" not in terimler and "Jenkins" in terimler
    m.kapat()


def test_motor_duzelt_parca_ogrenilmis_varyanti_uygular(tmp_path, saat, monkeypatch):
    monkeypatch.setattr(motor_mod, "DUZELT_MIN_TOKEN", 0)
    monkeypatch.setattr(llm, "bolum_ozetle", lambda *a, **k: llm.normalize_ozet({"ozet": "Jira konuşuldu."}))
    h = _varyantli_havuz(tmp_path, seri="test")
    m = _motor(tmp_path, havuz=h)
    m.duzelt_sor_fn = lambda mesajlar, sema: {"oneriler": [_o("kira", "Jira")]}
    for i, metin in enumerate(("kira kaydı açtım", "bütçe tamam"), 1):
        m.ses_olayi(klip(i, i * 3, i * 3 + 2, metin))
        m.konusmaci.kararlar = [karar(i, A)]
        saat.t = T0 + i * 3 + 3
        m.kontrol()
    m.parca_kapat("token", zorla=True)
    for i in m.isler:
        i.result(timeout=10)
    metinler = [s["text"] for s in m.parca_oku(1)["satirlar"]]
    assert metinler == ["Jira kaydı açtım", "bütçe tamam"]
    m.kapat()


# ---------------------------------------------------------------- 8) gec klip yeniden ozeti duzeltmeyi de calistirir

def test_gec_klip_yeniden_ozeti_duzeltme_gecisini_de_calistirir(tmp_path, monkeypatch):
    m = _motor(tmp_path, duzelt=True)
    veri = {"sira": 1, "neden": "token", "token": 900, "satirlar": [_satir(1, "kira kaydı")], "duzeltmeler": [],
            "ozet": None}
    m._parca_veri[1] = veri
    m._ozet_kirli[1] = 0.0
    cagrilar = []
    monkeypatch.setattr(m, "_isle", lambda v, duzelt: cagrilar.append((v["sira"], duzelt)))
    m._ozetleri_yenile(hepsi=True)
    for i in m.isler:
        i.result(timeout=5)
    assert cagrilar == [(1, True)]
    m.duzelt = False                                                           # kullanici duzeltmeyi kapattiysa kapali
    m._ozet_kirli[1] = 0.0
    m._ozetleri_yenile(hepsi=True)
    for i in m.isler:
        i.result(timeout=5)
    assert cagrilar[-1] == (1, False)
    m.kapat()


def test_kaynak_satiri_bulucu_sorumlu_bonusu_esigin_altini_gecirmez():
    satirlar = [{"ts": "10:00:00", "speaker": "Selin Kara", "text": "rapor taslağı hazır"},
                {"ts": "10:00:05", "speaker": "Kemal Tuncer", "text": "bütçe tablosunu cuma paylaşırım"}]
    bul = motor_mod.kaynak_satiri_bulucu(satirlar, esik=0.5)
    # madde kokleri: 'butce', 'tablo', 'cuma', 'paylas' -> Kemal satirinda 4/4; Selin satirinda 0
    assert bul("Bütçe tablosunu cuma paylaş", "Selin Kara")["ts"] == "10:00:05"
    # yalniz 1/4 ortusme (0.25) + sorumlu bonusu (0.1) = 0.35 < 0.5: eskiden esik 0.3'te gecerdi
    bul3 = motor_mod.kaynak_satiri_bulucu([{"ts": "10:00:00", "speaker": "Selin Kara", "text": "rapor taslağı"}], esik=0.3)
    assert bul3("rapor sunumu hazırlığı toplantısı", "Selin Kara") is None
    assert bul3("rapor taslağı", "Selin Kara")["ts"] == "10:00:00"


# ---------------------------------------------------------------- 5) kapasite kenari: sinir birlestirmesi

def test_toplam_tek_istek_kapasitesini_asinca_kalan_grup_sinirdan_birlesir_yedege_dusmez(monkeypatch):
    """Bulgu (not_cok_uzun/7): 268 madde, kapasite ~262; tur 2 gruplari [258, 10] kuculmedigi icin TUM liste yerel
    birlestirmeye dusuyordu. Artik kucuk grup onceki grubun kuyruguyla LLM'e gider: yerel yedek yok, madde kaybi yok,
    kronoloji korunur, hicbir istek kapasiteyi asmaz."""
    monkeypatch.setattr(llm, "BAGLAM_PENCERESI", 32768)
    gunluk = []
    monkeypatch.setattr(llm, "GUNLUK_FN", gunluk.append)
    fake = SahteBirlestirici()
    monkeypatch.setattr(llm, "sor", fake)
    bolumler = _uzun_toplanti(36, 10)                                         # 360 madde > tek istek kapasitesi
    ust = llm._liste_girdi_ust("Toplantı: test")
    assert llm.token_say(llm._liste_girdisi(bolumler)[0]) > ust               # fixture: tek istege sigmiyor
    log = []
    sonuc = llm.listeleri_birlestir(bolumler, "Toplantı: test", log.append)
    assert [a["madde"] for a in sonuc["aksiyonlar"]] == [a["madde"] for b in bolumler for a in b["aksiyonlar"]]
    assert not any("yerel birleştirme" in m for m in log), log
    assert not [g for g in gunluk if g.get("olay") == "liste_yedek"]
    assert [g for g in gunluk if g.get("olay") == "liste_sinir"]
    assert any("sınırlarından birleştiriliyor" in m for m in log)
    for c in fake.cagrilar:
        girdi = c[1]["content"].split("Bölümlerden gelen maddeler:\n\n", 1)[1]
        assert llm.token_say(girdi) <= ust
    # ust turda tek basina kalan grup sonucu ayni listeyle yeniden istenmez: ayni girdi iki kez gonderilmez
    girdiler = [c[1]["content"].split("Bölümlerden gelen maddeler:", 1)[1] for c in fake.cagrilar]
    assert len(set(girdiler)) == len(girdiler)


def test_kuyruk_ayir_kronolojiyi_korur_ve_tavani_asmaz():
    v = {"kararlar": [f"karar {i}" for i in range(10)],
         "aksiyonlar": [{"madde": _kalem(i), "sorumlu": "Selin Kara", "tarih": "-"} for i in range(40)],
         "acik_sorular": ["soru 1?"]}
    tavan = llm.token_say(llm._liste_girdisi([v])[0]) // 3
    sabit, kuyruk = llm._liste_kuyruk_ayir(v, tavan)
    assert llm.token_say(llm._liste_girdisi([kuyruk])[0]) <= tavan
    for alan in ("kararlar", "aksiyonlar", "acik_sorular"):
        assert sabit[alan] + kuyruk[alan] == v[alan]                          # bas + son = asil sira
    assert kuyruk["aksiyonlar"] and kuyruk["aksiyonlar"][-1] == v["aksiyonlar"][-1]
    assert llm._liste_kuyruk_ayir(v, 1) == (v, None)                          # tek madde bile sigmiyor


# ---------------------------------------------------------------- 6) kucuk hatalar: soru cevabi kesilmesi, BOM'lu config

def test_toplantiya_sor_dusunmesiz_tekrar_da_kesilirse_uyari_doner(monkeypatch):
    cagrilar = []

    def sahte(mesajlar, max_tokens, sema=None, effort="medium", temperature=0.6, dusunme=True):
        cagrilar.append(dusunme)
        return "Selin bütçe tablosunu", "length"
    monkeypatch.setattr(llm, "sor", sahte)
    bolumler = [("10:00–10:05", "[10:00:00] Selin Kara: Bütçe tablosunu cuma paylaşırım.")]
    cevap = llm.toplantiya_sor("Bütçeyi kim paylaşacak?", bolumler, "Toplantı: x")
    assert cagrilar == [True, False]
    assert cevap.startswith("Selin bütçe tablosunu") and llm.KESIK_CEVAP_UYARISI in cevap
    # tekrar tamamlanirsa uyari yok
    yanit = iter([("", "length"), ("Selin Kara paylaşacak ⏱10:00:00", "stop")])
    monkeypatch.setattr(llm, "sor", lambda *a, **k: next(yanit))
    cevap = llm.toplantiya_sor("Bütçeyi kim paylaşacak?", bolumler, "Toplantı: x")
    assert cevap == "Selin Kara paylaşacak ⏱10:00:00"
    # bos ve kesik: yalniz uyari
    monkeypatch.setattr(llm, "sor", lambda *a, **k: ("", "length"))
    assert llm.toplantiya_sor("Bütçe?", bolumler, "Toplantı: x") == llm.KESIK_CEVAP_UYARISI


def test_llm_ve_rag_acilis_ayari_bomlu_config_okur(tmp_path, monkeypatch):
    import ayar
    import rag
    yol = tmp_path / "config.json"
    yol.write_bytes(b"\xef\xbb\xbf" + json.dumps({"route": "http://localhost:9/v1", "embed_url": ""}).encode("utf-8"))
    monkeypatch.setattr(llm, "CONFIG", str(yol))
    monkeypatch.setattr(ayar, "AYAR_DOSYA", str(yol))
    assert llm._ilk_ayar()["route"] == "http://localhost:9/v1"
    assert rag._ilk_ayar() == {"route": "http://localhost:9/v1", "embed_url": ""}
    yol.write_text("{bozuk", encoding="utf-8")
    assert llm._ilk_ayar() == {} and rag._ilk_ayar() == {}
