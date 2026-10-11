"""Faz 2 terim havuzu: kaynaklar, kara liste, prompt/aday, bagimsiz kanitla ogrenme, geri alma, aktarim (sentetik veri)."""
import json
import os
import sys

import pytest

import kelime
import terim
from terim import TerimHavuzu

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import terim_aktar  # noqa: E402
import terim_geri_al  # noqa: E402

SERI = "kampanya-haftalik"
YAYGIN = {"komite", "toplanti", "kampanya", "proje", "kod", "hafta", "gorusme", "yarin"}


@pytest.fixture(autouse=True)
def yaygin(monkeypatch):
    """Yaygin kelime listesi testte sabit (veri dosyasi bos olabilir)."""
    monkeypatch.setattr(kelime, "_yaygin", set(YAYGIN))


@pytest.fixture
def havuz(tmp_path):
    return TerimHavuzu(str(tmp_path / "terimler" / "terimler.json"))


def _ogret(h, hedef="Jenkins", eski="cenkins", seri=SERI, toplantilar=("t1", "t2"), neden="terim"):
    for t in toplantilar:
        h.gozlem_ekle(seri, t, eski, hedef + "'e", neden)


# ---------------------------------------------------------------- havuz dosyasi

def test_ekle_kaydet_yukle(havuz):
    assert havuz.ekle(SERI, ["Kampanya Modülü", "IFRS 9", "Kampanya Modülü"], "davet") == ["Kampanya Modülü", "IFRS 9"]
    havuz.kaydet()
    v = json.load(open(havuz.yol, encoding="utf-8"))
    assert v["surum"] == 1 and len(v["terimler"]) == 2
    e = v["terimler"][0]
    assert e["terim"] == "Kampanya Modülü" and e["seriler"] == {SERI: 2} and e["kaynak"] == ["davet"]
    assert e["genel"] is False and 0 < e["guven"] <= 1 and e["ilk"] and e["son"]
    assert [g["islem"] for g in v["gecmis"]] == ["ekle", "ekle"]
    assert [x["terim"] for x in TerimHavuzu(havuz.yol).terimler] == ["Kampanya Modülü", "IFRS 9"]
    assert not os.path.exists(havuz.yol + ".tmp")


def test_gecersiz_terim_eklenmez(havuz):
    assert havuz.ekle(SERI, ["Bu bir cümledir ve çok uzundur ama terim değildir.", "12", "x", "komite", ""], "davet") == []


def test_bozuk_dosya_ezilmez(tmp_path):
    yol = tmp_path / "terimler.json"
    yol.write_text("{bozuk", encoding="utf-8")
    h = TerimHavuzu(str(yol))
    assert h.terimler == [] and h.yukleme_hatasi
    assert any(a.startswith("terimler.json.bozuk-") for a in os.listdir(tmp_path))


def test_elle_txt_genel_ve_varyant(tmp_path):
    k = tmp_path / "terimler"
    k.mkdir()
    (k / "elle.txt").write_text("# yorum\nKubernetes | kuber netes; kubernetis\nJira\n", encoding="utf-8")
    h = TerimHavuzu(str(k / "terimler.json"))
    assert h.adaylar("baska-seri") == ["Jira", "Kubernetes"] or set(h.adaylar("baska-seri")) == {"Jira", "Kubernetes"}
    assert h.varyantlar("Kubernetes") == ["kuber netes", "kubernetis"]
    h.kaydet(zorla=True)
    assert json.load(open(h.yol, encoding="utf-8"))["terimler"] == []       # elle terimler JSON'a yazilmaz


# ---------------------------------------------------------------- aday / prompt

def test_adaylar_seri_genel_ve_kok_eslesmesi(havuz):
    havuz.ekle(SERI, ["Kampanya Modülü"], "davet")
    havuz.ekle("baska", ["Ödeme Geçidi", "Basel III"], "davet")
    havuz.ekle("baska", ["Ödeme Geçidi"], "davet")
    havuz._bul("Basel III")["genel"] = True
    assert havuz.adaylar(SERI) == ["Basel III", "Kampanya Modülü"] or set(havuz.adaylar(SERI)) == {"Basel III", "Kampanya Modülü"}
    assert "Ödeme Geçidi" not in havuz.adaylar(SERI)                           # baska serinin ozel terimi sizmaz
    a = havuz.adaylar(SERI, "yarın ödeme geçidinde sorun var")                 # kok eslesmesi: eki olan metinde gecer
    assert a[0] == "Ödeme Geçidi" and "Kampanya Modülü" in a
    assert terim.ilgili(["Ödeme Geçidi", "Basel III"], "ödeme geçidi hatası") == ["Ödeme Geçidi"]


def test_adaylar_katilimci_adini_atar_ve_tavan(havuz):
    havuz.ekle(SERI, ["Selin Kara"] + [f"Terim{i} Alfa" for i in range(60)], "davet")
    a = havuz.adaylar(SERI, adlar=["Selin Kara"])
    assert "Selin Kara" not in a and len(a) == terim.ADAY_TAVAN


def test_prompt_sinir_ad_terim(havuz):
    havuz.ekle(SERI, [f"Terim{i} Beta" for i in range(40)], "davet")
    p = havuz.prompt(SERI, ["Selin Kara", "Emre Aksoy", "?", "Selin Kara"], onceki="Dünkü toplantıda söz verilen işler tamamlandı mı")
    assert "Selin Kara" in p and "Emre Aksoy" in p and p.count("Selin Kara") == 1 and "?" not in p
    assert len(p) <= 400
    assert 0 < p.count("Beta") <= terim.PROMPT_TERIM
    assert p.endswith("tamamlandı mı")                                        # onceki cumlenin sonu en sonda
    assert havuz.prompt(SERI) .startswith("Terimler:")
    assert TerimHavuzu(havuz.yol + ".yok").prompt(SERI) == ""


def test_prompt_uzun_ad_listesi_ve_onceki_kirpilir(havuz):
    adlar = [f"Katilimci{i} Soyad{i}" for i in range(30)]
    p = havuz.prompt(SERI, adlar, onceki="kelime " * 100)
    assert len(p) <= 400 and p.startswith("Katılımcılar: Katilimci0 Soyad0")
    assert not p.endswith(" ") and p.split()[-1] == "kelime"


# ---------------------------------------------------------------- davet kaynagi + kara liste

DAVET = """Merhaba,
Kampanya Modülü için IFRS 9 gereksinimlerini Jenkins'te gözden geçireceğiz.
Dokümanlar: https://wiki.example.test/a?b=1 ve selin.kara@example.test adresinde, Tel: +90 212 555 01 02
Saygılarımla,
Selin Kara Genel Müdür Yardımcısı
________________________________________________________________________________
Microsoft Teams toplantısına katılın
Toplantı Kimliği: 123 456 789 12
Parola: AbCdEf
Bu e-posta mesajı gizlidir ve yetkisiz kişilerce kullanılamaz. Vodafone Disclaimer"""


def test_davetten_cikar_kaynaklar():
    t = terim.davetten_cikar("RE: Kampanya Modülü Haftalık Toplantı", DAVET,
                             ["Kampanya_Modulu_Gereksinimleri_v3_final.docx", "ek-1.pdf", "IFRS9-Model.pdf"])
    assert "Kampanya Modülü" in t and "IFRS 9" in t and "Jenkins" in t
    assert "Kampanya Modulu Gereksinimleri" in t and "IFRS9 Model" in t
    assert not any(x.lower().startswith(("re", "haftal", "toplant")) for x in t)
    assert len(t) == len({x.lower() for x in t})


@pytest.mark.parametrize("gurultu", ["Teams", "Microsoft", "Katılın", "Parola", "AbCdEf", "123", "Toplantı Kimliği",
                                     "Selin Kara Genel", "Müdür", "Disclaimer", "wiki", "example", "Saygılarımla",
                                     "Tel", "555", "gizlidir"])
def test_kara_liste_davet_gurultusu(gurultu):
    t = terim.davetten_cikar("Kampanya Modülü Toplantısı", DAVET)
    assert not any(gurultu.lower() in x.lower() for x in t), (gurultu, t)


def test_kara_liste_temizleme_parcalari():
    assert "@" not in terim.davet_temizle("yaz: ad.soyad@example.test sonra")
    assert "http" not in terim.davet_temizle("bak https://x.example.test/yol?a=1 tamam")
    assert "212" not in terim.davet_temizle("ara +90 212 555 01 02 tamam")
    assert "123" not in terim.davet_temizle("kimlik 123 456 789 tamam")
    assert terim.davet_temizle("Gündem IFRS 9\nBest regards\nSelin Kara") == "Gündem IFRS 9"
    assert "Teams" not in terim.davet_temizle("Proje planı\nMicrosoft Teams Need help?\nJoin the meeting now")
    assert "gizli" not in terim.davet_temizle("Plan Q3. This email message is confidential and privileged.")


def test_davette_yaygin_kucuk_kelime_terim_olmaz():
    t = terim.davetten_cikar("", "yarın komite ile görüşme. Komite Toplantısı. Yarın Kod İncelemesi var")
    assert "komite" not in [x.lower() for x in t]


# ---------------------------------------------------------------- onceki not kaynagi

def test_notlardan_cikar():
    md = ("# Kampanya Modülü Haftalık Toplantı — 2026-10-01\n\n## Özet\nMetin **Jenkins** pipeline, **15 Ekim** teslim, "
          "**kritik risk**, **IFRS 9** ve **Karar:** **Önemli:**.\n## Kararlar\n- **Ödeme Geçidi** için test\n"
          "## Aksiyonlar\n| 1 | Madde | Selin Kara | - |\n")
    assert terim.notlardan_cikar(md) == ["Kampanya Modülü", "Jenkins", "IFRS 9", "Ödeme Geçidi"]
    assert terim.notlardan_cikar("## Özet\n## Kararlar\n## Açık sorular") == []


# ---------------------------------------------------------------- ogrenme: bagimsiz kanit

KANIT = {"davet": "Kampanya Modülü ve Jenkins pipeline planı", "onceki_not": "", "katilimci": ["Selin Kara"]}


def test_iki_ayri_toplanti_ve_davet_kaniti_varyant_yapar(havuz):
    havuz.ekle(SERI, ["Jenkins"], "davet")
    _ogret(havuz)
    assert havuz.ogren(SERI, "t2", KANIT) == [("cenkins", "Jenkins")]
    e = TerimHavuzu(havuz.yol)._bul("Jenkins")                                 # ogren() kaydetti
    assert e["varyantlar"] == ["cenkins"] and "ogrenme" in e["kaynak"] and e["ogrenilen"] == ["cenkins"]
    g = [x for x in havuz.gecmis if x.get("tur") == "varyant"]
    assert len(g) == 1 and g[0]["eski"] == "cenkins" and g[0]["sayac"] == {"toplanti": 2, "gozlem": 2, "seri": 1}
    assert havuz.ogren(SERI, "t2", KANIT) == []                                # ikinci cagri tekrar ogrenmez


def test_ayni_toplanti_tekrari_kanit_degil(havuz):
    for _ in range(5):
        havuz.gozlem_ekle(SERI, "t1", "cenkins", "Jenkins'e", "terim")        # LLM kendini tekrarliyor
    assert havuz.ogren(SERI, "t1", KANIT) == []
    assert havuz._bul("Jenkins") is None or havuz._bul("Jenkins")["varyantlar"] == []


def test_farkli_seriler_toplanmaz(havuz):
    havuz.gozlem_ekle("seri-a", "t1", "cenkins", "Jenkins'e", "terim")
    havuz.gozlem_ekle("seri-b", "t2", "cenkins", "Jenkins'e", "terim")
    assert havuz.ogren("seri-a", "t1", KANIT) == [] and havuz.ogren("seri-b", "t2", KANIT) == []


@pytest.mark.parametrize("kanit", [
    {},                                                                         # kanit yok
    {"davet": "Başka konular konuşulacak"},                                     # hedef kaynakta gecmiyor
    {"transkript": "Jenkins Jenkins Jenkins", "duzeltilmis": "Jenkins"},        # transkript/LLM ciktisi kanit degil
    {"davet": "Jenkins planı ve cenkins diye yazılan ad"},                      # eski de kaynakta geciyor
])
def test_kanit_olmadan_ogrenilmez(havuz, kanit):
    havuz.ekle(SERI, ["Jenkins"], "davet")                                      # havuzda olmasi da kanit degil
    _ogret(havuz)
    assert havuz.ogren(SERI, "t2", kanit) == []
    assert havuz._bul("Jenkins")["varyantlar"] == []


def test_kanit_onceki_not_ve_katilimci_adi(havuz):
    _ogret(havuz, hedef="Jenkins", eski="cenkins")
    assert havuz.ogren(SERI, "t2", {"onceki_not": "## Jenkins kurulumu"}) == [("cenkins", "Jenkins")]
    _ogret(havuz, hedef="Selin", eski="selim", neden="ad")
    assert havuz.ogren(SERI, "t2", {"katilimci": ["Selin Kara"]}) == [("selim", "Selin")]
    _ogret(havuz, hedef="Emre", eski="emir", neden="ad")
    assert havuz.ogren(SERI, "t2", {"katilimci": ["Emre Aksoy", "Emir Tan"]}) == []   # eski de katilimci: gercek ad


def test_kanit_liste_bicimi_ve_ek_toleransi(havuz):
    _ogret(havuz, hedef="Kampanya Modülü", eski="kampanya modeli")
    assert havuz.ogren(SERI, "t2", ["Kampanya Modülünde sorun var"]) == [("kampanya modeli", "Kampanya Modülü")]


def test_yaygin_eski_varyant_olmaz_ve_yazim_sayilmaz(havuz):
    _ogret(havuz, hedef="Commit", eski="komite")                                # komite->commit
    assert havuz.ogren(SERI, "t2", {"davet": "Commit kuralları"}) == []
    assert not havuz.gozlem_ekle(SERI, "t3", "cenkins", "Jenkins", "yazim")     # yazim duzeltmesi ogrenme adayi degil
    assert not havuz.gozlem_ekle(SERI, "t3", "ayni", "Ayni", "terim")           # normalize esit: degisiklik yok
    assert not havuz.gozlem_ekle(SERI, None, "x1", "Y1", "terim")


def test_genel_ancak_bes_ayri_seride(havuz):
    for i in range(1, 5):
        _ogret(havuz, seri=f"seri-{i}", toplantilar=(f"a{i}", f"b{i}"))
        havuz.ogren(f"seri-{i}", f"b{i}", KANIT)
    e = havuz._bul("Jenkins")
    assert e["genel"] is False and len(e["seriler"]) == 4
    _ogret(havuz, seri="seri-5", toplantilar=("a5", "b5"))
    havuz.ogren("seri-5", "b5", KANIT)
    e = havuz._bul("Jenkins")
    assert e["genel"] is True and e["genel_ogrenme"] is True
    assert [g for g in havuz.gecmis if g.get("tur") == "genel"][0]["sayac"] == {"seri": 5}
    assert "Jenkins" in havuz.adaylar("hic-gorulmemis-seri")


def test_geri_al_varyant_genel_ve_yeniden_ogrenmeyi_engeller(havuz):
    havuz.ekle(SERI, ["Jenkins"], "davet")
    for i in range(1, 6):
        _ogret(havuz, seri=f"s{i}", toplantilar=(f"a{i}", f"b{i}"))
        havuz.ogren(f"s{i}", f"b{i}", KANIT)
    assert havuz._bul("Jenkins")["genel"]
    r = havuz.geri_al("jenkins")
    assert r["varyantlar"] == ["cenkins"] and r["genel_geri"] is True and r["silindi"] is False
    e = TerimHavuzu(havuz.yol)._bul("Jenkins")                                  # geri_al kaydetti
    assert e["varyantlar"] == [] and e["genel"] is False and "davet" in e["kaynak"] and "ogrenme" not in e["kaynak"]
    assert havuz.gecmis[-1]["islem"] == "geri_al" and havuz.gecmis[-1]["terim"] == "Jenkins"
    _ogret(havuz, seri="s1", toplantilar=("c1", "d1"))                         # ayni eslesme tekrar gelse de ogrenilmez
    assert havuz.ogren("s1", "d1", KANIT) == []
    assert havuz.geri_al("yok boyle terim") is None


def test_yalniz_ogrenmeyle_girmis_terim_geri_alinca_silinir(havuz):
    _ogret(havuz, hedef="Selin", eski="selim", neden="ad")
    assert havuz.ogren(SERI, "t2", {"katilimci": ["Selin Kara"]}) == [("selim", "Selin")]
    assert havuz._bul("Selin")["kaynak"] == ["ogrenme"]
    assert havuz.geri_al("Selin")["silindi"] is True and havuz._bul("Selin") is None


def test_gozlem_hedefi_havuzdaki_terim_kokune_cozulur(havuz):
    havuz.ekle(SERI, ["Kampanya Modülü"], "davet")
    assert havuz.gozlem_ekle(SERI, "t1", "kampanya modeli", "Kampanya Modülü'nde", "terim")
    assert havuz.gozlemler[0]["hedef"] == "Kampanya Modülü"


def test_eszamanli_yazim_dosya_bozmaz(havuz):
    import threading

    def is_(n):
        for i in range(20):
            havuz.ekle(f"s{n}", [f"Terim{n}x{i} Alfa"], "davet")
            havuz.kaydet()
    ts = [threading.Thread(target=is_, args=(n,)) for n in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    havuz.kaydet(zorla=True)
    assert len(TerimHavuzu(havuz.yol).terimler) == 80


# ---------------------------------------------------------------- araclar

def _sozluk(tmp_path):
    yol = tmp_path / "sozluk.json"
    yol.write_text(json.dumps({
        "terimler": ["IFRS 9", "Jira", "Ödeme Geçidi", "Dünkü toplantıda konuştuğumuz uzun bir cümle gibi metin."],
        "aliaslar": {"ı t 9": "IFRS 9", "komplo tit": "commit"},
        "etiketler": {"ödeme geçidi": [SERI]}}, ensure_ascii=False), encoding="utf-8")
    return str(yol)


def test_terim_aktar_yalniz_terimler_yedekli(tmp_path):
    s = _sozluk(tmp_path)
    hedef = str(tmp_path / "terimler" / "terimler.json")
    r = terim_aktar.aktar(s, hedef)
    assert sorted(r["eklenen"]) == ["IFRS 9", "Jira", "Ödeme Geçidi"] and len(r["atilan"]) == 1
    assert len(r["yedekler"]) == 1 and os.path.exists(r["yedekler"][0])
    ham = open(hedef, encoding="utf-8").read()
    assert "komplo" not in ham and "ı t 9" not in ham and "aliaslar" not in ham          # alias tasinmaz
    h = TerimHavuzu(hedef)
    assert h._bul("Ödeme Geçidi")["seriler"] == {SERI: 1} and h._bul("Ödeme Geçidi")["genel"] is False
    assert h._bul("Jira")["genel"] is True and h._bul("Jira")["kaynak"] == ["aktar"]
    assert json.load(open(s, encoding="utf-8"))["aliaslar"]                              # kaynak dosya degismedi
    r2 = terim_aktar.aktar(s, hedef)                                                     # tekrar: sisme yok, hedef de yedeklenir
    assert r2["eklenen"] == [] and len(r2["yedekler"]) == 2
    assert TerimHavuzu(hedef)._bul("Ödeme Geçidi")["seriler"] == {SERI: 1}


def test_terim_aktar_cli_kuru_ve_hata(tmp_path, capsys):
    s = _sozluk(tmp_path)
    hedef = str(tmp_path / "t.json")
    assert terim_aktar.main([s, "--hedef", hedef, "--kuru"]) == 0
    assert not os.path.exists(hedef) and os.listdir(tmp_path) == ["sozluk.json"]
    assert terim_aktar.main([str(tmp_path / "yok.json"), "--hedef", hedef]) == 1


def test_terim_geri_al_cli(tmp_path, capsys):
    yol = str(tmp_path / "terimler.json")
    h = TerimHavuzu(yol)
    h.ekle(SERI, ["Jenkins"], "davet")
    _ogret(h)
    h.ogren(SERI, "t2", KANIT)
    assert terim_geri_al.main(["--liste", "--yol", yol]) == 0
    cikti = capsys.readouterr().out
    assert "Jenkins" in cikti and "eski=cenkins" in cikti and "sayac=toplanti:2" in cikti
    assert terim_geri_al.main(["Jenkins", "--yol", yol]) == 0
    assert "cenkins" in capsys.readouterr().out
    assert TerimHavuzu(yol)._bul("Jenkins")["varyantlar"] == []
    assert terim_geri_al.main(["Yok Terim", "--yol", yol]) == 1


# ---------------------------------------------------------------- cumle basindaki ozel ad (bulgu regresyonu)

@pytest.fixture
def gercek_liste(monkeypatch):
    """Gercek yaygin kelime listesi (cumle basindaki kelimenin yaygin olup olmadigi listeyle ayrilir)."""
    monkeypatch.setattr(kelime, "_yaygin", None)
    assert kelime.liste_var_mi()


@pytest.mark.parametrize("govde,beklenen", [
    ("Jenkins'te pipeline hatalari konusulacak.", ["Jenkins"]),
    ("Gundem: Jenkins gecisi.", ["Jenkins"]),
    ("Merhaba, Kubernetes guncellemesi var.", ["Kubernetes"]),
    ("Kubernetes gecisi plani. Jenkins kurulumu.", ["Kubernetes", "Jenkins"]),
    ("Kampanya Modülü geçişi konuşulacak.", ["Kampanya Modülü"]),
    ("Kampanya Modulu gecisi", ["Kampanya Modulu"]),
])
def test_cumle_basindaki_ozel_ad_alinir(gercek_liste, govde, beklenen):
    assert terim.davetten_cikar("Toplanti", govde, []) == beklenen


@pytest.mark.parametrize("govde", ["Musteri sikayetleri incelenecek.", "Sistemde hata var.", "Mutabakat süreci konuşulacak.",
                                   "Yarın Ali gelecek.", "Raporlar hazır.", "Ekip toplantısı", "Lütfen hazırlıklı gelin.",
                                   "Ali gelecek.", "Kod incelemesi yapılacak."])
def test_cumle_basindaki_yaygin_kelime_terim_olmaz(gercek_liste, govde):
    assert terim.davetten_cikar("Toplanti", govde, []) == []


def test_eki_atilmis_eski_katilimci_ya_da_havuz_terimi_ise_ogrenilmez(havuz):
    # 'Sercan'ın' kapidan gecmis olsa bile (eski surum) Serkan'in varyanti olmaz: Sercan katilimci
    _ogret(havuz, hedef="Serkan", eski="Sercan'ın", neden="ad")
    assert havuz.ogren(SERI, "t2", {"katilimci": ["Serkan Ak", "Sercan Öz"]}) == []
    assert havuz._bul("Serkan") is None
    # eski havuzdaki baska bir terim: Jenkins -> Jenkinsfile ogrenilmez
    havuz.ekle(SERI, ["Jenkins", "Jenkinsfile"], "davet")
    _ogret(havuz, hedef="Jenkinsfile", eski="Jenkins'in")
    assert havuz.ogren(SERI, "t2", {"davet": "Jenkinsfile güncellemesi"}) == []
    assert havuz._bul("Jenkinsfile")["varyantlar"] == []
