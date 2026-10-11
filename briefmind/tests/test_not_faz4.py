"""Faz 4 / A (MIMARI_v3 §6): sahip ve dayanak konusmaci guvenini tasir.
blok_metni gosterimi + ⟨⟩, sorumlu_dogrula (kesin / belirsiz / yok), dayanak_kontrolu (satir bazli, ⟨⟩ payi, etiket),
not_araclari (e-posta / Jira yalniz kesin, konusma payi, ana_git), tarih_coz birlestir sonrasi, rag gosterimi.
Ses cihazi, ag, LLM gerekmez; adlar sentetik."""
import csv
import datetime as dt
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


import llm
import motor as motor_mod
import not_araclari
import rag
import tarih
from metin import gorunen_ad

AYSE, KEMAL, SELIN = "Ayşe Yılmaz", "Kemal Tuncer", "Selin Kara"
KISILER = [AYSE, KEMAL, SELIN]


def sat(ts, kim, metin, guven=0.9, **kw):
    s = {"ts": ts, "speaker": kim, "text": metin, "spk_guven": guven, "cakisma": False, "devir": False}
    s.update(kw)
    return s


def aks(madde, sorumlu="belirsiz"):
    return {"madde": madde, "sorumlu": sorumlu, "tarih": "-"}


def dogrula(satirlar, madde, sorumlu="belirsiz", ben=None, kisiler=KISILER):
    a = motor_mod.sorumlu_dogrula([aks(madde, sorumlu)], satirlar, ben, katilimcilar=kisiler)[0]
    return a["sahip"]["durum"], a["sahip"]["ad"], a["sorumlu"]


# ---------------------------------------------------------------- blok_metni

def test_blok_metni_gorunen_ad_cakisma_sarmal_supheli_disarda():
    satirlar = [sat("10:00:00", AYSE, "Bütçeyi onaylıyoruz."),
                sat("10:00:05", KEMAL, "Tamam, not aldım.", 0.6),
                sat("10:00:08", KEMAL, "Yarın bakarım.", 0.6),
                sat("10:00:12", "?", "Duyulmadı ama bir şey söylendi.", 0.0),
                sat("10:00:15", f"{AYSE} → {KEMAL}", "Devir sonrası söz.", 0.5, devir=True),
                sat("10:00:20", f"{AYSE} / {KEMAL}", "Aynı anda iki ses.", 0.5, cakisma=True),
                sat("10:00:25", SELIN, "Hayalet cümle.", 0.9, supheli=True)]
    assert motor_mod.blok_metni(satirlar).split("\n") == [
        f"{AYSE}: Bütçeyi onaylıyoruz.",
        f"{KEMAL}?: Tamam, not aldım. Yarın bakarım.",
        "Bilinmeyen: Duyulmadı ama bir şey söylendi.",
        f"{AYSE} → {KEMAL}: Devir sonrası söz.",
        f"{AYSE} / {KEMAL} (aynı anda): ⟨Aynı anda iki ses.⟩"]
    assert "Hayalet" not in motor_mod.blok_metni(satirlar)                       # supheli nota girmez
    assert motor_mod.sarmal_sil("a ⟨b c⟩ d") == "a   d"
    assert all(gorunen_ad(s) for s in satirlar)


# ---------------------------------------------------------------- sorumlu_dogrula

def test_sahip_ben_yaparim_kesin_dusuk_guven_belirsiz():
    # (a) kesin konusman + birinci tekil taahhut + model ayni kisiyi yazmis
    assert dogrula([sat("10:00:00", SELIN, "Raporu ben yaparım")], "Raporu yapmak", SELIN) == ("kesin", SELIN, SELIN)
    assert dogrula([sat("10:00:00", SELIN, "Raporu yarın göndereceğim")], "Raporu göndermek", SELIN)[0] == "kesin"
    assert dogrula([sat("10:00:00", SELIN, "Raporu şimdi hazırlıyorum")], "Raporu hazırlamak", SELIN)[0] == "kesin"
    # model 'belirsiz' dediyse kod kesin sahip uretmez: en fazla aday
    assert dogrula([sat("10:00:00", SELIN, "Raporu ben yaparım")], "Raporu yapmak") == ("belirsiz", SELIN, SELIN + " (?)")
    # 'Ad?' (spk_guven < 0.7): ayni cumle sahip uretmez
    d = dogrula([sat("10:00:00", SELIN, "Raporu ben yaparım", 0.6)], "Raporu yapmak")
    assert d == ("belirsiz", "", "belirsiz")
    d = dogrula([sat("10:00:00", SELIN, "Raporu ben yaparım", 0.6)], "Raporu yapmak", sorumlu=SELIN)
    assert d == ("belirsiz", SELIN, SELIN + " (?)")
    # model baska birini yazmis ve konusan ustleniyor: belirsiz
    assert dogrula([sat("10:00:00", SELIN, "Raporu ben yaparım")], "Raporu yapmak", AYSE)[0] == "belirsiz"
    # taahhut degil: bilgi / olumsuz / gecmis
    for cumle in ("Raporu yapmıyorum", "Raporu yapmayacağım", "Raporu biliyorum", "Raporu hazırladım"):
        assert dogrula([sat("10:00:00", SELIN, cumle)], "Raporu hazırlamak", SELIN)[0] == "belirsiz", cumle


def test_sahip_mikrofon_ben_adi():
    s = [sat("10:00:00", "Ben", "Raporu ben hazırlarım")]
    assert dogrula(s, "Raporu hazırlamak", SELIN, ben=SELIN) == ("kesin", SELIN, SELIN)
    assert dogrula(s, "Raporu hazırlamak", ben=SELIN) == ("belirsiz", SELIN, SELIN + " (?)")      # model yazmamis: aday
    assert dogrula(s, "Raporu hazırlamak", ben=None) == ("belirsiz", "", "belirsiz")     # ad bilinmiyor


def test_sahip_bana_benim_bende_kanit_degil():
    s = [sat("10:00:00", KEMAL, "Raporu bana gönder")]
    assert dogrula(s, "Raporu göndermek")[0] == "yok"
    assert dogrula(s, "Raporu göndermek", KEMAL) == ("belirsiz", KEMAL, KEMAL + " (?)")   # konusani sahip yapmak yanlis
    for cumle in ("Rapor benim elimde", "Rapor bende hazır"):
        assert dogrula([sat("10:00:00", KEMAL, cumle)], "Raporu hazırlamak")[0] == "yok", cumle


def test_sahip_adlandirilmis_sen_yap():
    s = [sat("10:00:00", KEMAL, "Ayşe, sen cumaya raporu hazırla")]
    assert dogrula(s, "Raporu cumaya hazırlamak", AYSE) == ("kesin", AYSE, AYSE)         # (b) model de ayni kisiyi yazmis
    assert dogrula(s, "Raporu cumaya hazırlamak", "Ayşe")[0] == "kesin"                  # ilk ad
    assert dogrula(s, "Raporu cumaya hazırlamak") == ("belirsiz", AYSE, AYSE + " (?)")   # model yazmamis: hitap adaydir
    assert dogrula(s, "Raporu cumaya hazırlamak", "Selin Kara")[0] == "belirsiz"         # satirda adi gecmiyor
    # konusan kesin degil -> belirsiz
    assert dogrula([sat("10:00:00", KEMAL, "Ayşe, sen cumaya raporu hazırla", 0.6)],
                   "Raporu cumaya hazırlamak", AYSE) == ("belirsiz", AYSE, AYSE + " (?)")
    # eszamanli / birlesik konusmaci
    for kim, kw in ((f"{KEMAL} / {SELIN}", {"cakisma": True}), (f"{KEMAL} → {SELIN}", {"devir": True}), ("?", {})):
        d = dogrula([sat("10:00:00", kim, "Ayşe, sen cumaya raporu hazırla", 0.5, **kw)], "Raporu cumaya hazırlamak", AYSE)
        assert d[0] == "belirsiz", kim
    # ad ⟨⟩ icinde ise sayilmaz
    d = dogrula([sat("10:00:00", KEMAL, "Raporu ⟨Ayşe, sen⟩ cumaya hazırla")], "Raporu cumaya hazırlamak", AYSE)
    assert d[0] == "belirsiz"


def test_sahip_yanlis_duyulan_ad_ayse_degil_aysen():
    s = [sat("10:00:00", KEMAL, "Ayşen, sen cumaya raporu hazırla")]
    assert dogrula(s, "Raporu cumaya hazırlamak", AYSE) == ("belirsiz", AYSE, AYSE + " (?)")
    assert dogrula(s, "Raporu cumaya hazırlamak")[0] == "yok"                             # hitap de eslesmez
    # ayni ilk ad iki katilimcida: ilk ad yetmez
    ikiz = [AYSE, "Ayşe Kaya", KEMAL]
    assert dogrula([sat("10:00:00", KEMAL, "Ayşe, sen cumaya raporu hazırla")], "Raporu cumaya hazırlamak", "Ayşe",
                   kisiler=ikiz)[0] == "belirsiz"


def test_sahip_a_ok_b_devir_ve_cakisma_satiri_sahip_uretmez():
    for kim, kw in ((f"{AYSE} → {KEMAL}", {"devir": True}), (f"{AYSE} / {KEMAL}", {"cakisma": True})):
        d = dogrula([sat("10:00:00", kim, "Raporu ben hazırlarım", 0.5, **kw)], "Raporu hazırlamak", AYSE)
        assert d[0] == "belirsiz" and d[2].endswith("(?)"), kim
    # kaynak satir bulunamadi
    assert dogrula([sat("10:00:00", KEMAL, "Başka konu")], "Bütçe onayı almak", AYSE) == ("belirsiz", AYSE, AYSE + " (?)")
    assert dogrula([sat("10:00:00", KEMAL, "Başka konu")], "Bütçe onayı almak")[0] == "yok"


def test_sahip_coklu_ad_hepsi_dogrulanmali():
    d = dogrula([sat("10:00:00", KEMAL, "Ayşe, sen cumaya raporu hazırla; Selin de bakar")], "Raporu cumaya hazırlamak",
                f"{AYSE}, {SELIN}")
    assert d[0] == "kesin" and d[1] == f"{AYSE}, {SELIN}"
    # biri satirda gecmiyorsa tumu belirsiz; belirsiz olan (?) alir
    d = dogrula([sat("10:00:00", KEMAL, "Ayşe, sen cumaya raporu hazırla")], "Raporu cumaya hazırlamak", f"{AYSE}, {SELIN}")
    assert d == ("belirsiz", f"{AYSE}, {SELIN}", f"{AYSE}, {SELIN} (?)")


def test_sorumlu_dogrula_girdi_degismez_ve_sorumlu_metni_notta_basilir():
    girdi = [aks("Raporu cumaya hazırlamak", AYSE)]
    s = [sat("10:00:00", KEMAL, "Ayşen, sen cumaya raporu hazırla")]
    cikti = motor_mod.sorumlu_dogrula(girdi, s, None, katilimcilar=KISILER)
    assert girdi[0]["sorumlu"] == AYSE and "sahip" not in girdi[0]
    md = llm.not_markdown({"ozet": "Özet.", "sonraki_adim": "-"},
                          {"kararlar": [], "aksiyonlar": cikti, "acik_sorular": []})
    assert f"| 1 | Raporu cumaya hazırlamak | {AYSE} (?) | - |" in md


def test_katilimci_verilmezse_kesin_konusmacilar_kisi_sayilir():
    s = [sat("10:00:00", KEMAL, "Selin, sen cumaya raporu hazırla"), sat("10:00:10", SELIN, "Tamam")]
    a = motor_mod.sorumlu_dogrula([aks("Raporu cumaya hazırlamak", SELIN)], s)[0]
    assert a["sahip"] == {"ad": SELIN, "durum": "kesin"}


ALI, ALI_K, AYSE_K, ZEYNEP, MEHMET = "Ali Demir", "Ali Kaya", "Ayşe Kaya", "Zeynep Ak", "Mehmet Can"
EKIP = [ALI, AYSE_K, ZEYNEP, MEHMET]


def test_sahip_a_yolu_bekle_rica_oner_sor_ve_baskasina_is_konusani_sahip_yapmaz(tmp_path):
    """Bulgu: her 1. tekil -yorum/-irim fiili taahhut sayiliyordu; konusan isi baskasina verirken de kesin sahip oluyordu."""
    for cumle, madde in (("Bütçe raporunu cumaya kadar Ayşe'den bekliyorum", "Bütçe raporunu cumaya kadar hazırlamak"),
                         ("Zeynep, test planını göndermeni rica ediyorum", "Test planını göndermek"),
                         ("Mehmet'in hazırlamasını öneriyorum", "Hazırlamasını yapmak"),
                         ("Ayşe'ye soruyorum, testi kim yapacak?", "Testi yapmak"),
                         ("Dosyalarım hazır, taslağı Mehmet incelesin", "Taslağı incelemek")):
        s = [sat("10:00:00", ALI, cumle, 0.95)]
        assert motor_mod.kaynak_satiri_bulucu(s)(madde) is s[0], cumle
        for sorumlu in ("belirsiz", ALI):
            durum, ad, _ = dogrula(s, madde, sorumlu, kisiler=EKIP)
            assert durum != "kesin", (cumle, sorumlu)
            assert sorumlu == ALI or ad != ALI, (cumle, ad)                 # model 'belirsiz': Ali aday bile degil
        assert not motor_mod.birinci_tekil_taahhut(cumle), cumle
    # hitap: is muhataba gider (model yazdiysa kesin, yazmadiysa aday) -- konusana degil
    s = [sat("10:00:00", ALI, "Zeynep, test planını göndermeni rica ediyorum", 0.95)]
    assert dogrula(s, "Test planını göndermek", "Zeynep", kisiler=EKIP) == ("kesin", ZEYNEP, ZEYNEP)
    assert dogrula(s, "Test planını göndermek", kisiler=EKIP) == ("belirsiz", ZEYNEP, ZEYNEP + " (?)")
    s = [sat("10:00:00", ALI, "Dosyalarım hazır, taslağı Mehmet incelesin", 0.95)]
    assert dogrula(s, "Taslağı incelemek", "Mehmet", kisiler=EKIP) == ("kesin", MEHMET, MEHMET)
    # uctan uca: e-posta ve Jira Ali'ye 'Guvenlik raporu' yazmaz
    satirlar = [sat("10:00:00", ALI, "Güvenlik raporunu cumaya kadar Zeynep'ten bekliyorum", 0.95),
                sat("10:00:10", ALI, "Sunum taslağını ben hazırlarım", 0.95)]
    cikti = motor_mod.sorumlu_dogrula([aks("Güvenlik raporunu cumaya kadar hazırlamak", ALI),
                                       aks("Sunum taslağını hazırlamak", ALI)], satirlar, None, katilimcilar=EKIP)
    assert [a["sahip"]["durum"] for a in cikti] == ["belirsiz", "kesin"]
    md = _not_md(cikti)
    epostalar = not_araclari.kisiye_ozel_epostalar(md, "Güvenlik", "2026-10-12")
    assert [e[0] for e in epostalar] == [ALI] and "Güvenlik raporunu" not in epostalar[0][2]
    yol = tmp_path / "j.csv"
    not_araclari.jira_csv(not_araclari.aksiyonlari_ayikla(md), str(yol))
    jira = list(csv.reader(open(yol, encoding="utf-8-sig")))
    assert [r[1] for r in jira[1:]] == ["", ALI]


def test_sahip_b_yolu_soru_olumsuz_gecmis_ve_model_isareti_kesin_olmaz():
    """Bulgu: adin satirda gecmesi yetiyordu; soru / olumsuz / gecmis zaman ve modelin '(?)' isareti kesin sayiliyordu."""
    for cumle, madde in (("Entegrasyon testini Mehmet mi yapacak, yoksa başka biri mi?", "Entegrasyon testini yapmak"),
                         ("Mehmet sözleşme taslağını incelemeyecek, o izinde", "Sözleşme taslağını incelemek"),
                         ("Mehmet taslağı geçen hafta zaten inceledi", "Taslağı incelemek"),
                         ("Mehmet, sen değil; taslağı Zeynep incelesin", "Taslağı incelemek"),
                         ("Mehmet, sen geçen hafta raporu göndermiştin", "Raporu göndermek")):
        s = [sat("10:00:00", AYSE_K, cumle, 0.95)]
        assert motor_mod.kaynak_satiri_bulucu(s)(madde) is s[0], cumle      # kaynak satir bulunuyor (bos gecmez)
        assert dogrula(s, madde, MEHMET, kisiler=EKIP)[0] == "belirsiz", cumle
        assert dogrula(s, madde, ZEYNEP, kisiler=EKIP)[0] == "belirsiz", cumle
        durum, ad, _ = dogrula(s, madde, kisiler=EKIP)
        assert durum != "kesin" and ad == "", (cumle, ad)                    # hitap adayi da yok
    s = [sat("10:00:00", ALI, "Ayşe, siz bütçe tablosunu inceleme fırsatı buldunuz mu?", 0.95)]
    assert dogrula(s, "Bütçe tablosunu incelemek", kisiler=EKIP)[1] == ""
    assert dogrula(s, "Bütçe tablosunu incelemek", AYSE_K, kisiler=EKIP)[0] == "belirsiz"
    # model '(?)' yazdiysa kod kesin yapmaz (zayif satirda da, guclu satirda da)
    for cumle in ("Risk analizini Ayşe ile konuştuk", "Ayşe, sen risk analizini hazırla"):
        s = [sat("10:00:00", ALI, cumle, 0.95)]
        assert dogrula(s, "Risk analizini hazırlamak", AYSE_K + " (?)", kisiler=EKIP) == (
            "belirsiz", AYSE_K, AYSE_K + " (?)"), cumle
    s = [sat("10:00:00", ALI, "Raporu ben hazırlarım", 0.95)]
    assert dogrula(s, "Raporu hazırlamak", ALI + " (?)", kisiler=EKIP)[0] == "belirsiz"
    # olumlu kaliplar kesin kalir: hitap + emir / gelecek, ozne + 3. kisi gelecek / emir
    for cumle, kim in (("Mehmet, sen yarın taslağı gönder", MEHMET), ("Taslağı Mehmet hazırlayacak", MEHMET),
                       ("Ayşe, siz taslağı inceleyin", AYSE_K), ("Taslağı Zeynep incelesin", ZEYNEP)):
        s = [sat("10:00:00", ALI, cumle, 0.95)]
        assert dogrula(s, "Taslağı incelemek", kim.split()[0], kisiler=EKIP) == ("kesin", kim, kim), cumle


def test_sahip_ayni_ilk_adli_katilimci_ya_da_belirsiz_ilk_ad_kesin_olmaz():
    """Bulgu: _ayni_kisi yalniz ilk ada bakiyordu; 'Ali Kaya' / 'Ali' konusan Ali Demir'in isinde kesin sahip oluyordu."""
    kisiler = [ALI, ALI_K, AYSE_K]
    s = [sat("10:00:00", ALI, "Risk raporunu ben hazırlarım", 0.95)]
    assert dogrula(s, "Risk raporunu hazırlamak", ALI_K, kisiler=kisiler) == ("belirsiz", ALI_K, ALI_K + " (?)")
    assert dogrula(s, "Risk raporunu hazırlamak", "Ali", kisiler=kisiler) == ("belirsiz", "Ali", "Ali (?)")
    assert dogrula(s, "Risk raporunu hazırlamak", ALI, kisiler=kisiler) == ("kesin", ALI, ALI)
    # mikrofon satiri: ben = Ali Demir
    m = [sat("10:00:00", "Ben", "Risk raporunu ben hazırlarım", 0.95)]
    assert dogrula(m, "Risk raporunu hazırlamak", ALI_K, ben=ALI, kisiler=kisiler)[0] == "belirsiz"
    assert dogrula(m, "Risk raporunu hazırlamak", ALI, ben=ALI, kisiler=kisiler) == ("kesin", ALI, ALI)
    # (b) ilk ad ortaksa satirdaki 'Ali' yetmez, tam ad gerekir
    h = [sat("10:00:00", AYSE_K, "Ali, sen risk raporunu hazırla", 0.95)]
    assert dogrula(h, "Risk raporunu hazırlamak", ALI_K, kisiler=kisiler)[0] == "belirsiz"
    h = [sat("10:00:00", AYSE_K, "Ali Kaya, sen risk raporunu hazırla", 0.95)]
    assert dogrula(h, "Risk raporunu hazırlamak", ALI_K, kisiler=kisiler) == ("kesin", ALI_K, ALI_K)
    # tekil ilk ad kesin olunca notta katilimci listesindeki tam ad yazilir
    t = [sat("10:00:00", ALI, "Bütçe tablosunu ben güncellerim", 0.95)]
    assert dogrula(t, "Bütçe tablosunu güncellemek", "Ali", kisiler=EKIP) == ("kesin", ALI, ALI)


# ---------------------------------------------------------------- dayanak_kontrolu

def _not(*aksiyonlar, karar=()):
    md = "# T\n\n## Özet\n-\n\n## Kararlar\n" + ("\n".join(f"- {k}" for k in karar) or "-") + "\n\n## Aksiyonlar\n"
    if aksiyonlar:
        md += "| # | Madde | Sorumlu | Tarih |\n|---|---|---|---|\n"
        md += "\n".join(f"| {i} | {m} | {s} | - |" for i, (m, s) in enumerate(aksiyonlar, 1)) + "\n"
    return md + "\n## Açık sorular\n-\n\n## Bir sonraki adım\n-\n"


ZEMIN = "Selin Kara: Günaydın herkese, toplantıyı başlatıyoruz, gündemde üç madde var.\n"


def test_dayanak_cakisma_satirindan_karar_cikmaz():
    transkript = ZEMIN + f"{AYSE} / {KEMAL} (aynı anda): ⟨Fiyat artışı onaylandı, ocak ayında uygulanacak⟩\n"
    yeni, atilan = motor_mod.dayanak_kontrolu(_not(karar=["Fiyat artışı onaylandı, ocakta uygulanacak"]), transkript)
    assert "Fiyat artışı" not in yeni and atilan == ["Fiyat artışı onaylandı, ocakta uygulanacak"]
    # ayni sozu kesin konusmaci soylediyse madde kalir
    temiz = ZEMIN + f"{KEMAL}: Fiyat artışı onaylandı, ocak ayında uygulanacak\n"
    yeni, atilan = motor_mod.dayanak_kontrolu(_not(karar=["Fiyat artışı onaylandı, ocakta uygulanacak"]), temiz)
    assert "Fiyat artışı" in yeni and not atilan


def test_dayanak_satir_bazli_1_3_satir_kok_ortusmesi_en_az_yarim():
    # roller: 6 kok; 3 ardisik satirda 4'u (0.67): dayanakli. Kokler 5 satira dagilirsa dayanaksiz.
    madde = "Bütçe tablosu cuma hazırlanacak ve onaya sunulacak"
    yakin = (ZEMIN + f"{AYSE}: Bütçe tablosu hakkında konuşalım.\n{KEMAL}: Peki.\n"
             f"{SELIN}: Cuma günü hazırlanacak mı?\n")
    yeni, atilan = motor_mod.dayanak_kontrolu(_not(karar=[madde]), yakin)
    assert madde in yeni and not atilan
    uzak = (ZEMIN + f"{AYSE}: Bütçe tablosu hakkında konuşalım.\n" + f"{KEMAL}: Peki.\n{SELIN}: Tamam.\n"
            f"{KEMAL}: Başka konu var mı?\n{SELIN}: Yok.\n{AYSE}: Cuma günü hazırlanacak mı?\n")
    yeni, atilan = motor_mod.dayanak_kontrolu(_not(karar=[madde]), uzak)
    assert atilan == [madde]


def test_dayanak_sarmal_payi_yuzde_otuzu_asarsa_madde_belirsiz():
    madde = "Bütçe tablosu cuma hazırlanacak ve onaya sunulacak"
    transkript = (ZEMIN + f"{AYSE}: Bütçe tablosu.\n"
                  f"{KEMAL} / {SELIN} (aynı anda): ⟨bir iki üç dört beş altı yedi sekiz dokuz on⟩\n"
                  f"{KEMAL}: Cuma hazırlanacak.\n")
    yeni, atilan = motor_mod.dayanak_kontrolu(_not(karar=[madde], *[(madde, AYSE)]), transkript)
    assert not atilan
    assert f"- {madde} (?)" in yeni                                                    # karar belirsiz
    assert f"| 1 | {madde} (?) | {AYSE} (?) | - |" in yeni                              # aksiyon + sahip belirsiz
    # ⟨⟩ yoksa ya da pay dusukse isaret yok
    temiz = ZEMIN + f"{AYSE}: Bütçe tablosu.\n{KEMAL}: Cuma hazırlanacak.\n"
    yeni, _ = motor_mod.dayanak_kontrolu(_not(karar=[madde]), temiz)
    assert f"- {madde}\n" in yeni and "(?)" not in yeni


def test_dayanak_yalniz_sarmalda_gecen_sayi_tarih_komsu_koklerle_nota_girmez():
    """Bulgu: '480 bin' / 'mart' yalniz ⟨⟩ icindeyken komsu kesin satirlarin kokleri (5/7, pay 0) maddeyi dayanakli
    sayiyordu; karar ve aksiyonlar (?) bile almadan nota (ve Jira'ya) giriyordu."""
    transkript = (ZEMIN + f"{MEHMET} / {AYSE_K} (aynı anda): ⟨Yeni bütçe 480 bin lira olarak onaylandı, teslim 20 Kasım. "
                  "Mart ayında lisans alımı yapılacak⟩\n"
                  f"{ALI}: Geçen yılki bütçe de bu dönem onaylandı, yeni kalemlere bakıyoruz, lisans alımı da var\n"
                  f"{AYSE_K}: Ödemeler lira olarak yapılacak, onay süreci netleştirilecek, ayında bakarız\n")
    karar = "Yeni bütçe 480 bin lira olarak onaylandı"
    md = _not(("Bütçe 480 bin onay süreci netleştirilecek", "belirsiz"), ("Mart ayında lisans alımı yapılacak", "belirsiz"),
              karar=[karar + " ⏱10:00:15"])
    yeni, atilan = motor_mod.dayanak_kontrolu(md, transkript)
    assert "480" not in yeni and "Mart" not in yeni
    assert [a.split(" ⏱")[0] for a in atilan] == [karar, "Bütçe 480 bin onay süreci netleştirilecek",
                                                  "Mart ayında lisans alımı yapılacak"]
    # ayni deger ⟨⟩ DISINDA da soylendiyse madde kalir, isaretsiz
    temiz = transkript + f"{ALI}: Yeni bütçe 480 bin lira olarak onaylandı\n"
    yeni, atilan = motor_mod.dayanak_kontrolu(_not(karar=[karar]), temiz)
    assert f"- {karar}\n" in yeni and not atilan
    # deger hicbir yerde yoksa (model uydurdu / donusturdu) madde kalir ama (?) alir
    yeni, atilan = motor_mod.dayanak_kontrolu(_not(karar=["Yeni bütçe 750 bin lira olarak onaylandı"]), temiz)
    assert "- Yeni bütçe 750 bin lira olarak onaylandı (?)" in yeni and not atilan


def test_dayanak_tekil_en_iyi_eslesme_cakisma_satiriysa_madde_belirsiz():
    """Maddeyi tek basina en iyi karsilayan satir ⟨⟩ (cakisma) satiri: pencerenin ⟨⟩ payi dusuk olsa da (?)."""
    madde = "Yeni kampanya bütçesi onaylandı, lansman ertelendi"
    transkript = (ZEMIN + f"{MEHMET} / {AYSE_K} (aynı anda): ⟨Yeni kampanya bütçesi onaylandı, lansman ertelendi⟩\n"
                  f"{ALI}: Geçen kampanya bütçesi onaylandı.\n{AYSE_K}: Lansman tarihi ertelendi.\n")
    yeni, atilan = motor_mod.dayanak_kontrolu(_not(karar=[madde]), transkript)
    assert f"- {madde} (?)" in yeni and not atilan
    temiz = (ZEMIN + f"{ALI}: Yeni kampanya bütçesi onaylandı.\n{AYSE_K}: Lansman tarihi ertelendi.\n")
    yeni, _ = motor_mod.dayanak_kontrolu(_not(karar=[madde]), temiz)
    assert f"- {madde}\n" in yeni


def test_dayanak_dusuk_guven_etiketi_sahibi_belirsiz_yapar():
    madde = "Raporu cuma günü hazırlamak"
    for etiket, beklenen in ((f"{KEMAL}?", f"{AYSE} (?)"), ("Bilinmeyen", f"{AYSE} (?)"),
                             (f"{KEMAL} → {SELIN}", f"{AYSE} (?)"), (f"{KEMAL} / {SELIN} (aynı anda)", f"{AYSE} (?)"),
                             (KEMAL, AYSE)):
        transkript = ZEMIN + f"{etiket}: Raporu cuma günü hazırlayacağım.\n"
        yeni, atilan = motor_mod.dayanak_kontrolu(_not((madde, AYSE)), transkript)
        assert f"| 1 | {madde} | {beklenen} | - |" in yeni and not atilan, etiket
    # 'belirsiz' ya da zaten (?) olan hucreye dokunulmaz
    transkript = ZEMIN + f"{KEMAL}?: Raporu cuma günü hazırlayacağım.\n"
    yeni, _ = motor_mod.dayanak_kontrolu(_not((madde, "belirsiz"), (madde, f"{AYSE} (?)")), transkript)
    assert "| 1 | Raporu cuma günü hazırlamak | belirsiz | - |" in yeni and f"| 2 | {madde} | {AYSE} (?) | - |" in yeni


def test_dayanak_eski_cagri_bicimi_degismez():
    transkript = "Ahmet: IFRS 9 raporunda PD kolonu string geliyor, number yapmamız lazım.\nAyşe: Test ortamı hazır."
    md = "# T\n\n## Kararlar\n- PD kolonu number yapılacak\n- Yeni bütçe onaylandı\n"
    yeni, atilan = motor_mod.dayanak_kontrolu(md, transkript)
    assert "PD kolonu number yapılacak" in yeni and atilan == ["Yeni bütçe onaylandı"]


# ---------------------------------------------------------------- not_araclari

def _not_md(aksiyonlar):
    return llm.not_markdown({"ozet": "Toplantı özeti.", "sonraki_adim": "-"},
                            {"kararlar": [], "aksiyonlar": aksiyonlar, "acik_sorular": []})


def test_eposta_ve_jira_yalniz_kesin_sahip(tmp_path):
    s = [sat("10:00:00", KEMAL, "Ayşe, sen cumaya raporu hazırla"),
         sat("10:00:20", SELIN, "Sunumu ben hazırlarım"),
         sat("10:00:40", KEMAL, "Veri tablosunu Ayşen kontrol etsin", 0.6)]
    cikti = motor_mod.sorumlu_dogrula(
        [aks("Raporu cumaya hazırlamak", AYSE), aks("Sunumu hazırlamak", SELIN), aks("Veri tablosunu kontrol etmek", AYSE),
         aks("Sözleşmeyi imzalatmak")], s, None, katilimcilar=KISILER)
    assert [a["sahip"]["durum"] for a in cikti] == ["kesin", "kesin", "belirsiz", "yok"]
    md = _not_md(cikti)
    ayiklanan = not_araclari.aksiyonlari_ayikla(md)
    assert [a["sahip"] for a in ayiklanan] == [
        {"ad": AYSE, "durum": "kesin"}, {"ad": SELIN, "durum": "kesin"}, {"ad": AYSE, "durum": "belirsiz"},
        {"ad": "", "durum": "yok"}]
    # e-posta: yalniz kesin sahipler, belirsiz isler hicbir govdede yok
    epostalar = not_araclari.kisiye_ozel_epostalar(md, "Strateji", "2026-09-23")
    assert sorted(e[0] for e in epostalar) == sorted([AYSE, SELIN])
    tum = "\n".join(e[2] for e in epostalar)
    assert "Raporu cumaya hazırlamak" in tum and "Sunumu hazırlamak" in tum
    assert "Veri tablosunu kontrol etmek" not in tum and "Sözleşmeyi" not in tum and "(?)" not in tum
    # 'Sahibi belirsiz' blogu
    blok = not_araclari.sahibi_belirsiz_blogu(md)
    assert blok.startswith("Sahibi belirsiz:") and f"- Veri tablosunu kontrol etmek (aday: {AYSE})" in blok
    assert "- Sözleşmeyi imzalatmak" in blok and "Raporu cumaya" not in blok
    assert [a["madde"] for a in not_araclari.sahibi_belirsiz_aksiyonlar(md)] == [
        "Veri tablosunu kontrol etmek", "Sözleşmeyi imzalatmak"]
    # Jira: Assignee yalniz kesin
    yol = tmp_path / "a.csv"
    assert not_araclari.jira_csv(ayiklanan, str(yol), "Strateji") == 4
    with open(yol, encoding="utf-8-sig", newline="") as f:
        satirlar = list(csv.reader(f))
    assert [r[1] for r in satirlar[1:]] == [AYSE, SELIN, "", ""]
    assert "sahibi belirsiz (aday: " + AYSE + ")" in satirlar[3][3] and "sahibi belirsiz" in satirlar[4][3]
    assert "sahibi belirsiz" not in satirlar[1][3]


def test_jira_sorumlu_isareti_olmayan_eski_bicim_ve_sahip_alani():
    # sahip alani yoksa Sorumlu metninden turetilir ('Ad (?)' belirsiz)
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        yol = os.path.join(d, "a.csv")
        not_araclari.jira_csv([{"madde": "A işi", "sorumlu": "Selin Kara (?)", "tarih": "-"},
                               {"madde": "B işi", "sorumlu": "Selin Kara", "tarih": "-"},
                               {"madde": "C işi", "sorumlu": "x", "sahip": {"ad": "", "durum": "yok"}, "tarih": "-"}], yol)
        with open(yol, encoding="utf-8-sig", newline="") as f:
            satirlar = list(csv.reader(f))
    assert [r[1] for r in satirlar[1:]] == ["", "Selin Kara", ""]


def test_madde_isaretli_ise_sahip_belirsiz():
    assert not_araclari.sahip_coz("Selin Kara", "Rapor hazırlamak (?)") == {"ad": "Selin Kara", "durum": "belirsiz"}
    assert not_araclari.sahip_coz("Selin Kara, Kemal Tuncer", "x")["durum"] == "kesin"
    assert not_araclari.sahip_coz("-", "x") == {"ad": "", "durum": "yok"}


def test_konusma_paylari_yalniz_kesin_tek_konusmaci_belirsiz_ayri():
    satirlar = [sat("10:00:00", SELIN, "bir iki üç dört beş"),                    # 10 sn
                sat("10:00:10", "?", "bir iki üç dört beş"),                       # 10 sn: belirsiz
                sat("10:00:20", AYSE, "bir iki üç dört beş", 0.6),                 # 'Ayşe?': belirsiz
                sat("10:00:30", f"{AYSE} → {KEMAL}", "bir iki üç dört beş", 0.5, devir=True),   # belirsiz
                sat("10:00:40", f"{AYSE} / {KEMAL}", "bir iki üç dört beş", 0.5, cakisma=True),  # belirsiz
                sat("10:00:50", KEMAL, "bir iki üç dört beş altı"),                # son satir: 6/2.5 = 2.4 sn
                sat("10:00:55", SELIN, "bir iki üç dört beş")]                     # 2 sn... 5/2.5
    p = not_araclari.konusma_paylari(satirlar)
    assert [x[0] for x in p] == [SELIN, KEMAL] and all("?" not in x[0] and "→" not in x[0] and "/" not in x[0] for x in p)
    assert p[0][1] == 12.0 and p[1][1] == 5.0 and sum(x[2] for x in p) in (99, 100, 101)
    # belirsiz 40 sn / toplam 57 sn
    assert not_araclari.konusma_belirsiz_payi(satirlar) == 70
    assert not_araclari.konusma_payi_metni(p, 70) == \
        f"Konuşma payı: {SELIN} %{p[0][2]}, {KEMAL} %{p[1][2]}, konuşmacısı belirsiz %70 (tümüne göre)"
    assert not_araclari.konusma_payi_metni(p) == f"Konuşma payı: {SELIN} %{p[0][2]}, {KEMAL} %{p[1][2]}"
    assert not_araclari.konusma_paylari([sat("10:00:00", "?", "bir iki", 0.0)]) == []
    assert not_araclari.konusma_belirsiz_payi([]) == 0


def test_ana_git_ve_ara_gorunen_ad(tmp_path):
    k = tmp_path / "toplantilar" / "2026-09-23_strateji"
    (k / "parcalar").mkdir(parents=True)
    (k / "meta.json").write_text(json.dumps({"baslik": "Strateji", "tarih": "2026-09-23"}), encoding="utf-8")
    satirlar = [sat("10:00:00", AYSE, "Bütçe tablosu hazır", 0.6), sat("10:00:05", "?", "Bütçe için tamam", 0.0),
                sat("10:00:09", f"{AYSE} / {KEMAL}", "Bütçe aynı anda", 0.5, cakisma=True)]
    (k / "parcalar" / "parca_001.json").write_text(json.dumps({"sira": 1, "satirlar": satirlar}), encoding="utf-8")
    pencere = not_araclari.ana_git(str(k), "10:00:05")
    assert [x[1] for x in pencere] == [AYSE + "?", "Bilinmeyen", f"{AYSE} / {KEMAL} (aynı anda)"]
    sonuc = not_araclari.ara("bütçe", kok=str(tmp_path / "toplantilar"))
    assert [x["kim"] for x in sonuc] == [AYSE + "?", "Bilinmeyen", f"{AYSE} / {KEMAL} (aynı anda)"]


# ---------------------------------------------------------------- tarih, rag

def test_tarih_gun_adi_tam_kelime_ve_acik_tarih_bicimleri():
    g = dt.date(2026, 9, 29)
    assert tarih.tarih_coz("pazarlama sunumu hazır olunca", g) == "pazarlama sunumu hazır olunca"   # Pazar degil
    assert tarih.tarih_coz("salon rezervasyonu", g) == "salon rezervasyonu"
    assert tarih.tarih_coz("pazar günü", g) == "2026-10-04 (Pazar)"
    assert tarih.tarih_coz("cumaya kadar", g) == "2026-10-02 (Cuma)"
    assert tarih.tarih_coz("pazartesiye", g) == "2026-10-05 (Pazartesi)"
    assert tarih.tarih_coz("15.10.2026", g) == "2026-10-15 (Perşembe)"
    assert tarih.tarih_coz("15 Ekim", g) == "2026-10-15 (Perşembe)"
    assert tarih.tarih_coz("31 Şubat", g) == "31 Şubat"
    assert tarih.tarih_coz("2026-10-02 (Perşembe)", g) == "2026-10-01 (Perşembe)"


def test_tarih_belirsiz_ya_da_celiskili_ifade_kesin_gune_cevrilmez(tmp_path):
    g = dt.date(2026, 10, 12)
    for ifade in ("cuma (?)", "15 Ekim (?)", "perşembe mi cuma mı", "pazartesi değil salı", "2 hafta sonra cuma",
                  "cumaya ya da pazartesiye", "cuma veya pazartesi", "belki perşembe", "iki hafta içinde", "cuma?",
                  "2026-10-16 (Cuma) (?)"):
        assert tarih.tarih_coz(ifade, g) == ifade, ifade
        assert tarih.belirsiz_tarih_mi(ifade), ifade
    # tek ve acik ifade cozulmeye devam eder
    assert tarih.tarih_coz("cuma", g) == "2026-10-16 (Cuma)"
    assert tarih.tarih_coz("Cuma (16 Ekim)", g) == "2026-10-16 (Cuma)"
    assert tarih.tarih_coz("haftaya salı", g) == "2026-10-20 (Salı)"
    assert not tarih.belirsiz_tarih_mi("Perşembe günü") and not tarih.belirsiz_tarih_mi("15 Ekim")
    # Jira: '(?)' tasiyan tarih Due Date'e girmez, Description'da kalir
    yol = tmp_path / "j.csv"
    not_araclari.jira_csv([{"madde": "Raporu göndermek", "sorumlu": AYSE, "tarih": "2026-10-16 (Cuma) (?)",
                            "sahip": {"ad": AYSE, "durum": "kesin"}},
                           {"madde": "Sunumu hazırlamak", "sorumlu": AYSE, "tarih": "2026-10-16 (Cuma)",
                            "sahip": {"ad": AYSE, "durum": "kesin"}}], str(yol))
    satirlar = list(csv.reader(open(yol, encoding="utf-8-sig")))
    assert satirlar[1][2] == "" and "tarih belirsiz: 2026-10-16 (Cuma) (?)" in satirlar[1][3]
    assert satirlar[2][2] == "2026-10-16" and "tarih belirsiz" not in satirlar[2][3]


def test_tarih_coz_birlestir_sonrasi_motor_aksiyon_islemi(tmp_path):
    m = motor_mod.Motor(str(tmp_path / "t"), "Test", dt.date(2026, 9, 29), duzelt=False, ben=SELIN,
                        katilimcilar=KISILER)
    s = [sat("10:00:00", SELIN, "Raporu ben hazırlarım")]
    cikti = m._aksiyonlari_kesinlestir([
        {"madde": "Raporu hazırlamak", "sorumlu": SELIN, "tarih": "2026-10-02 (Perşembe)"},   # gun adi esas
        {"madde": "Raporu hazırlamak", "sorumlu": "belirsiz", "tarih": "yarın"}], s)
    m.kapat()
    assert [a["tarih"] for a in cikti] == ["2026-10-01 (Perşembe)", "2026-09-30 (Çarşamba)"]
    assert cikti[0]["sahip"] == {"ad": SELIN, "durum": "kesin"}


def test_rag_pencereleri_gorunen_ad_ve_sarmal():
    satirlar = [sat("10:00:00", AYSE, "Birinci söz", 0.6), sat("10:00:05", "?", "İkinci söz", 0.0),
                sat("10:00:09", f"{AYSE} / {KEMAL}", "Üçüncü söz", 0.5, cakisma=True),
                sat("10:00:12", SELIN, "Hayalet", supheli=True), sat("10:00:15", SELIN, "Dördüncü söz")]
    p = rag.pencereler(satirlar)
    assert p[0]["metin"].split("\n") == [
        f"[10:00:00] {AYSE}?: Birinci söz", "[10:00:05] Bilinmeyen: İkinci söz",
        f"[10:00:09] {AYSE} / {KEMAL} (aynı anda): ⟨Üçüncü söz⟩", f"[10:00:15] {SELIN}: Dördüncü söz"]
    # motor.soru_sor'un hazir 'gorunen' alani (altyazi yedegi son eki) oldugu gibi kullanilir, ikinci kez eklenmez
    z = rag.pencereler([sat("10:00:00", f"{AYSE} / {KEMAL}", "x", 0.5, cakisma=True, gorunen="X (özel)")])
    assert z[0]["metin"] == "[10:00:00] X (özel): ⟨x⟩"


# ---------------------------------------------------------------- not_son_denetim (dayanak kontrolunden sonra)

SON_TRANSKRIPT = (ZEMIN + f"{SELIN}: Bütçe tablosunu ben cuma günü paylaşırım.\n"
                  f"{KEMAL}: Risk raporunu ben hazırlarım.\n"
                  f"{AYSE}?: Sunum dosyasını ben güncellerim.\n"
                  f"{KEMAL} / {SELIN} (aynı anda): ⟨Selin, sen lisans 480 sözleşmesini gönder. "
                  "Teklif 750 bin lira olarak onaylandı.⟩\n"
                  f"{SELIN}: Müşteri listesini ben temizlerim.\n"
                  f"{KEMAL}?: Kampanya takvimini anlattım, bütçe kalemlerini özetledim.\n")
SON_AKSIYONLAR = [aks("Bütçe tablosunu cuma paylaşmak", SELIN), aks("Risk raporunu hazırlamak", KEMAL),
                  aks("Sunum dosyasını güncellemek", AYSE),                       # kaynak 'Ad?': dayanak (?) yapar
                  aks("Lisans 480 sözleşmesini göndermek", SELIN),                # yalniz ⟨⟩: dayanak atar
                  aks("Müşteri listesini temizlemek", SELIN)]


def _son_not(ozet="Bütçe tablosu ve risk raporu konuşuldu.", sonraki="-", konu="", aksiyonlar=SON_AKSIYONLAR,
             transkript=SON_TRANSKRIPT, supheli=""):
    genel = {"ozet": ozet, "sonraki_adim": sonraki}
    if konu:
        genel["konu_akisi"] = konu
    md = "# T\n\n" + llm.not_markdown(genel, {"kararlar": [], "aksiyonlar": [dict(a) for a in aksiyonlar],
                                              "acik_sorular": []})
    md, _ = motor_mod.dayanak_kontrolu(md, transkript)
    return motor_mod.not_son_denetim(md, transkript, KISILER + [ZEYNEP], supheli)


def _bolum(md, baslik):
    return md.split(f"## {baslik}\n", 1)[1].split("\n## ", 1)[0].strip()


def test_kisiye_gore_liste_son_tablodan_kurulur_atilan_yok_belirsiz_aday_olarak():
    """Bulgu (not_temiz/7): liste dayanak kontrolunden ONCE kuruluyordu; tablodan atilan ⟨⟩ maddeleri listede '(aday: Ad)'
    ile kaliyor, dayanagin (?) yaptigi sahip kisinin isi sayiliyordu."""
    once = llm.not_markdown({"ozet": "x", "sonraki_adim": "-"},
                            {"kararlar": [], "aksiyonlar": [dict(a) for a in SON_AKSIYONLAR], "acik_sorular": []})
    assert "Lisans 480" in _bolum(once, "Kişiye göre iş listesi")                # fixture: eski liste maddeyi tasir
    md, _ = _son_not()
    tablo = not_araclari.aksiyonlari_ayikla(md)
    assert [a["madde"] for a in tablo] == ["Bütçe tablosunu cuma paylaşmak", "Risk raporunu hazırlamak",
                                          "Sunum dosyasını güncellemek", "Müşteri listesini temizlemek"]
    assert [ln.split("|")[1].strip() for ln in md.split("\n") if ln.startswith("| ") and ln[2].isdigit()] == \
        ["1", "2", "3", "4"]                                                     # atilan satir numara boslugu birakmaz
    assert _bolum(md, "Kişiye göre iş listesi").split("\n") == [
        f"- {SELIN} (2): Bütçe tablosunu cuma paylaşmak; Müşteri listesini temizlemek",
        f"- {KEMAL} (1): Risk raporunu hazırlamak",
        f"- belirsiz (1): Sunum dosyasını güncellemek (aday: {AYSE})"]
    assert "\n\n## Açık sorular" in md and "\n\n## Kişiye göre iş listesi" in md
    # tablo 4 maddenin altina inerse bolum kalkar
    md, _ = _son_not(aksiyonlar=SON_AKSIYONLAR[:4])
    assert "Kişiye göre iş listesi" not in md and "\n\n## Açık sorular" in md


def test_kisiye_gore_yenile_madde_isareti_de_belirsiz_sayilir_ve_bolum_eklenir():
    md = ("# T\n\n## Aksiyonlar\n| # | Madde | Sorumlu | Tarih |\n|---|---|---|---|\n"
          f"| 1 | Rapor hazırlanacak (?) | {SELIN} | - |\n| 2 | Tablo güncellenecek | {SELIN} | - |\n"
          f"| 5 | Sunum yapılacak | {KEMAL} | - |\n| 9 | Liste temizlenecek | belirsiz | - |\n\n## Açık sorular\n-\n")
    yeni = not_araclari.kisiye_gore_yenile(not_araclari.aksiyon_sirasini_yenile(md))
    assert "| 3 | Sunum yapılacak |" in yeni and "| 4 | Liste temizlenecek |" in yeni
    assert _bolum(yeni, "Kişiye göre iş listesi").split("\n") == [
        f"- {KEMAL} (1): Sunum yapılacak", f"- {SELIN} (1): Tablo güncellenecek",
        f"- belirsiz (2): Rapor hazırlanacak (?) (aday: {SELIN}); Liste temizlenecek"]


def test_ozette_yalniz_sarmalda_gecen_tutar_soru_isaretiyle_kalir():
    """Bulgu (not_temiz/7): '667 bin lira tutarı da ele alındı' -- 667 yalniz ⟨⟩ (aynı anda) icinde. Deger '(?)' olur,
    'ele alindi' baglami kalir; konu akisi da ayni kurala tabi; ⟨⟩ disinda da soylenen deger isaretsiz kalir."""
    md, olaylar = _son_not(ozet="Bütçe tablosu ve risk raporu konuşuldu. Teklif 750 bin lira olarak ele alındı.",
                           konu="- 10:00:00–10:05:00: bütçe tablosu; 750 bin lira teklif")
    assert _bolum(md, "Özet") == "Bütçe tablosu ve risk raporu konuşuldu. Teklif (?) bin lira olarak ele alındı."
    assert _bolum(md, "Konu akışı") == "- 10:00:00–10:05:00: bütçe tablosu; (?) bin lira teklif"
    assert "750" not in md and any("750" in o for o in olaylar)
    temiz = SON_TRANSKRIPT + f"{KEMAL}: Teklif 750 bin lira, cuma netleşir.\n"
    md, _ = _son_not(ozet="Teklif 750 bin lira olarak ele alındı.", transkript=temiz)
    assert _bolum(md, "Özet") == "Teklif 750 bin lira olarak ele alındı."


def test_ozette_yalniz_supheli_satirda_gecen_deger_ve_gun_isaretlenir():
    t = SON_TRANSKRIPT + f"{SELIN}: Lansman ayında hangi gün olacağı ele alındı, sonra netleşir.\n"
    md, _ = _son_not(ozet="Bütçe tablosu konuşuldu. Lansman 2026 mart ayında, salı günü ele alındı.",
                     supheli="Lansman 2026 mart ayında yapılacak salı", transkript=t)
    assert _bolum(md, "Özet") == "Bütçe tablosu konuşuldu. Lansman (?) ayında, (?) günü ele alındı."   # '(?) (?)' teklenir
    # transkriptte ⟨⟩ disinda gecen gun ('cuma') isaretlenmez
    md, _ = _son_not(ozet="Bütçe tablosu cuma günü ele alındı.", supheli="cuma")
    assert _bolum(md, "Özet") == "Bütçe tablosu cuma günü ele alındı."


def test_sonraki_adim_yalniz_kesin_sahibin_isini_sahibiyle_tasir():
    """Bulgu (not_temiz/7): 'Bir sonraki adım' ⟨⟩ / 'A → B' / 'Ad?' kaynakli isi SAHIBIYLE yaziyordu. Kesin sahip + dayanakli
    is kalir; sahibi '(?)' olan is ve yalniz ⟨⟩ icindeki is cumlesi duser; dayanakli isle eslesmeyen cumlede ad cikar."""
    md, olaylar = _son_not(sonraki="Selin bütçe tablosunu cuma paylaşacak. Ayşe sunum dosyasını güncelleyecek. "
                                   "Selin lisans 480 sözleşmesini gönderecek. Zeynep toplantı odasını ayarlayacak.")
    assert _bolum(md, "Bir sonraki adım") == "Selin bütçe tablosunu cuma paylaşacak. Toplantı odasını ayarlayacak (?)."
    assert "Ayşe" not in _bolum(md, "Bir sonraki adım") and "480" not in md
    assert sum("sonraki adım" in o for o in olaylar) == 3
    # baskasinin kesin isi de baska kisiye yazilmaz
    md, _ = _son_not(sonraki=f"Kemal bütçe tablosunu cuma paylaşacak; {SELIN} müşteri listesini temizleyecek.")
    assert _bolum(md, "Bir sonraki adım") == f"{SELIN} müşteri listesini temizleyecek."


def test_sonraki_adim_bosalirsa_yalniz_kesin_sahipli_aksiyonlardan_kurulur():
    md, _ = _son_not(sonraki="Ayşe sunum dosyasını güncelleyecek. Selin lisans 480 sözleşmesini gönderecek.")
    s = _bolum(md, "Bir sonraki adım")
    assert s.startswith("Öncelikli aksiyonlar:") and f"({SELIN})" in s and "Sunum" not in s and AYSE not in s
    md, _ = _son_not(sonraki="Ayşe sunum dosyasını güncelleyecek.", aksiyonlar=[SON_AKSIYONLAR[2]])
    assert _bolum(md, "Bir sonraki adım") == "-"


def test_ozette_belirsiz_kaynakli_kisi_adi_cikar_kesin_kaynakli_kalir():
    """Ozet cumlesi: is cumlesi (gelecek zaman) yalniz kesin sahip adiyla; bir seyi 'yapti' diyen cumlenin kisiye
    dayanagi yalniz 'Ad?' satiriysa ad cikar ve cumle (?) alir; kesin satirla dayanakli olan degismez."""
    md, _ = _son_not(ozet="Kemal kampanya takvimini anlattı. Kemal risk raporunu hazırlayacak. "
                          "Ayşe sunum dosyasını güncelleyecek. Selin müşteri listesinden söz etti.")
    assert _bolum(md, "Özet") == ("Kampanya takvimini anlattı (?). Kemal risk raporunu hazırlayacak. "
                                  "Sunum dosyasını güncelleyecek (?). Selin müşteri listesinden söz etti.")


def test_ad_cikar_ekli_ad_ve_baglac_artigi_temizlenir():
    assert motor_mod._ad_cikar(f"{AYSE} ve {KEMAL} raporu hazırlayacak.", [AYSE, KEMAL]) == "Raporu hazırlayacak (?)."
    assert motor_mod._ad_cikar("Ayşe'nin raporu Kemal tarafından gönderilecek", [AYSE, KEMAL]) == \
        "Raporu gönderilecek (?)."
    assert motor_mod._ad_cikar(f"{AYSE}.", [AYSE]) is None
