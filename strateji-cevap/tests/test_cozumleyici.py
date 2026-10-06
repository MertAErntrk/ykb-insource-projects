import copy
import datetime as dt
import json
import os

import cozumleyici
import katalog as katalog_mod
from cozumleyici import coz, kural_calisti_mi

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def basvuru(no):
    with open(os.path.join(KOK, "ornekler", "basvurular", "sentetik_{}.json".format(no)), encoding="utf-8") as f:
        return json.load(f)


K = katalog_mod.Katalog.yukle()
M = katalog_mod.matris_yukle()
AYAR = {"kendi_banka_kodu": "0067", "matris": M}


def test_h221_baska_banka_boran_metni():
    s = coz(K.bul("H221"), basvuru("10000001"), AYAR)
    assert s.sablon == "baska_banka" and s.onayli and not s.notlar
    assert s.olgular["ay_yil"] == "Nisan 2025" and s.olgular["kayit_sayisi"] == 1   # 2019 pencere dışı, 2025-09 başvurudan sonra
    assert s.metin == ('Merhaba, "10000001" numaralı müşterinin ticari KKB kayıtlarına göre ilk olarak Nisan 2025 '
                       "tarihli bağımsız denetim raporu farklı bir banka tarafından alınmıştır. Bu nedenle 5 yıl "
                       "dolmadığı için bankamızda da H221 kuralına takılarak bağımsız denetim raporu talep edilmektedir.")


def test_h221_kendi_banka_taslak_sablon():
    s = coz(K.bul("H221"), basvuru("10000001"), {"kendi_banka_kodu": "0099"})
    assert s.sablon == "kendi_banka" and not s.onayli and s.inceleme_gerekli


def test_h221_banka_kodu_bilinmiyorsa_not():
    s = coz(K.bul("H221"), basvuru("10000001"), {})
    assert s.sablon == "baska_banka" and any("kendi_banka_kodu" in n for n in s.notlar)


def test_h221_kayit_yok():
    s = coz(K.bul("H221"), basvuru("10000002"), AYAR)
    assert s.sablon == "kayit_yok" and s.inceleme_gerekli and s.olgular["kayit_sayisi"] == 0


def test_h221_pencere_siniri():
    b = basvuru("10000001")                      # başvuru 2025-07-10 → pencere 2020-07-10'dan itibaren
    b["Indata"]["CRBNotice"] = [{"Status": 2, "NoticeDate": "2020-07-10", "BankCode": "0099"}]
    assert coz(K.bul("H221"), b, AYAR).olgular["ay_yil"] == "Temmuz 2020"
    b["Indata"]["CRBNotice"] = [{"Status": 2, "NoticeDate": "2020-07-09", "BankCode": "0099"}]
    assert coz(K.bul("H221"), b, AYAR).sablon == "kayit_yok"


def test_h221_liste_yoksa_not():
    b = basvuru("10000001")
    del b["Indata"]["CRBNotice"]
    s = coz(K.bul("H221"), b, AYAR)
    assert s.sablon == "kayit_yok" and any("yok ya da liste değil" in n for n in s.notlar)


def test_h221_tarihsiz_kayit_atlanir():
    b = basvuru("10000001")
    b["Indata"]["CRBNotice"].append({"Status": 1, "NoticeDate": None, "BankCode": "0099"})
    s = coz(K.bul("H221"), b, AYAR)
    assert s.olgular["ay_yil"] == "Nisan 2025" and any("tarihsiz" in n for n in s.notlar)


def test_yetki_h_kurali_belirleyici():
    s = coz(K.bul("YETKI"), basvuru("10000001"), AYAR)
    assert s.sablon == "h_kurali_belirleyici" and not s.notlar
    o = s.olgular
    assert (o["nominal"], o["agirlikli"], o["matris_seviyesi"], o["h_seviyesi"], o["nihai"], o["h_kodlari"]) == \
        ("4.250.000,00", "1.375.000,00", 3, 8, 8, "H999")
    assert o["hesap_matris"] == 3
    assert "H999 kuralı yetki seviyesini 8. seviyeye" in s.metin


def test_yetki_matris_belirleyici():
    s = coz(K.bul("YETKI"), basvuru("10000002"), AYAR)
    assert s.sablon == "matris_belirleyici" and not s.notlar
    assert s.olgular["nominal"] == "2.400.000,00" and s.olgular["nihai"] == 2


def test_yetki_tutarsizliklar_not_olur():
    b = basvuru("10000001")
    b["Outdata"]["AUTHLEVELFORMATRIX"] = 4                       # katalog matrisi 3 diyor
    s = coz(K.bul("YETKI"), b, AYAR)
    assert any("matris seviyesi JSON'da 4" in n for n in s.notlar)
    b["Outdata"]["AUTHLEVELFORMATRIX"] = 3
    b["Outdata"]["APPROVALAUTHORITY"] = 5                        # max(3, 8) = 8 değil
    s = coz(K.bul("YETKI"), b, AYAR)
    assert any("birleştirme varsayımı" in n for n in s.notlar)
    b["Outdata"]["APPROVALAUTHORITY"] = 8
    b["Outdata"]["HRules"] = [{"Code": "H221", "Hit": 1}]        # yükselten kural listede yok
    s = coz(K.bul("YETKI"), b, AYAR)
    assert s.sablon == "h_kurali_belirleyici" and s.olgular["h_kodlari"] == "ilgili H"
    assert any("yükselten H kuralı" in n for n in s.notlar)


def test_yetki_nihai_yoksa_hesaplanir():
    b = basvuru("10000001")
    del b["Outdata"]["APPROVALAUTHORITY"]
    s = coz(K.bul("YETKI"), b, AYAR)
    assert s.olgular["nihai"] == 8 and any("hesaplandı" in n for n in s.notlar)


def test_yetki_veri_eksik():
    b = basvuru("10000001")
    del b["Outdata"]["CORPCOMMMATRIXOUTPUTS"]
    s = coz(K.bul("YETKI"), b, AYAR)
    assert s.sablon == "veri_eksik" and s.inceleme_gerekli


def test_yetki_matrissiz_json_seviyesi_kullanilir():
    s = coz(K.bul("YETKI"), basvuru("10000001"), {"kendi_banka_kodu": "0067"})
    assert s.sablon == "h_kurali_belirleyici" and "hesap_matris" not in s.olgular and not s.notlar


def test_sabit_kural():
    kural = {"kod": "H500", "ad": "x", "sinif": "cikti", "aciklama": "Açıklama metni.", "cozum": {"tur": "sabit"},
             "sablonlar": {"aciklama": {"metin": 'Merhaba, "{cif}" numaralı müşteride {kural} kuralı çalışmıştır. {aciklama}',
                                        "onay": "test"}}}
    s = coz(kural, basvuru("10000001"))
    assert s.metin == 'Merhaba, "10000001" numaralı müşteride H500 kuralı çalışmıştır. Açıklama metni.' and s.onayli


def test_sablon_eksikse_not():
    kural = {"kod": "H501", "ad": "x", "sinif": "cikti", "cozum": {"tur": "sabit"},
             "sablonlar": {"baska": {"metin": "x", "onay": "t"}}}
    s = coz(kural, basvuru("10000001"))
    assert s.metin == "" and any("şablonu katalogda yok" in n for n in s.notlar)
    kural["sablonlar"] = {"aciklama": {"metin": "{olmayan_alan}", "onay": "t"}}
    s = coz(kural, basvuru("10000001"))
    assert s.metin == "" and any("doldurulamayan alan" in n for n in s.notlar)


def test_kural_calisti_mi():
    b = basvuru("10000001")
    assert kural_calisti_mi(b, "H221") is True
    assert kural_calisti_mi(b, "h999") is True
    assert kural_calisti_mi(b, "H100") is False
    assert kural_calisti_mi(b, "H777") is False
    assert kural_calisti_mi({"Outdata": {}}, "H221") is None
