from cozumleyici import Sonuc
from yazici import dogrula, ingilizce_mi, kur, olgu_tokenlari

KAPANIS = "Ek sorularınız için Satış ekipleriyle iletişime geçebilirsiniz. İyi çalışmalar."
H221 = ('Merhaba, "10000001" numaralı müşterinin ticari KKB kayıtlarına göre ilk olarak Nisan 2025 tarihli '
        "bağımsız denetim raporu farklı bir banka tarafından alınmıştır. Bu nedenle 5 yıl dolmadığı için "
        "bankamızda da H221 kuralına takılarak bağımsız denetim raporu talep edilmektedir.")
YETKI = ('Merhaba, "10000001" numaralı müşterinin onay yetki seviyesi 8 olarak belirlenmiştir. Nominal limit '
         "4.250.000,00 TL ve ağırlıklı limit 1.375.000,00 TL için onay yetki matrisi 3. seviyeyi vermektedir.")


def test_olgu_tokenlari():
    t = olgu_tokenlari(YETKI + " " + H221)
    assert {"10000001", "8", "4.250.000,00", "1.375.000,00", "3", "H221", "Nisan 2025", "5"} <= t


def test_dogrula():
    assert dogrula(H221.replace("Merhaba, ", "Merhaba, bilginize: "), H221) == []
    assert any("olmayan olgular" in s for s in dogrula(H221 + " Ayrıca H305 kuralı da 2024 yılında çalışmıştır.", H221))
    assert any("düşmüş" in s for s in dogrula(H221.replace("Nisan 2025", "o tarihte"), H221))
    assert any("İngilizce" in s for s in dogrula("Hello, the customer report was obtained by another bank and the rule is applied because of this.", "x"))
    assert any("kısa" in s for s in dogrula("Merhaba.", H221))


def test_ingilizce_mi():
    assert ingilizce_mi("Hello, the independent audit report of the customer was obtained by another bank and this is the reason.")
    assert not ingilizce_mi(H221)
    assert not ingilizce_mi("kısa")


def test_kur_tek_selam_tek_kapanis():
    t = kur([Sonuc("H221", "baska_banka", H221), Sonuc("YETKI", "matris", YETKI)], KAPANIS)
    assert t.durum == "otomatik" and t.yontem == "sablon" and t.kurallar == ["H221", "YETKI"]
    assert t.metin.count("Merhaba") == 1 and t.metin.count(KAPANIS) == 1 and t.metin.endswith(KAPANIS)
    assert '\n\n"10000001" numaralı müşterinin onay yetki' in t.metin


def test_kur_inceleme_durumlari():
    assert kur([Sonuc("H221", "kendi", H221, onayli=False)], KAPANIS).durum == "inceleme_gerekli"
    assert kur([Sonuc("H221", "x", H221, notlar=["sorun"])], KAPANIS).notlar == ["H221: sorun"]
    bos = kur([Sonuc("H221", "x", "", notlar=["şablon yok"])], KAPANIS)
    assert bos.durum == "inceleme_gerekli" and bos.metin == "" and "cevap metni üretilemedi" in bos.notlar


class SahteLLM:
    def __init__(self, cevap):
        self.cevap = cevap

    def metin(self, sistem, kullanici, max_tokens=700):
        return self.cevap


def test_llm_yeniden_yazim_kabul_ve_ret():
    sonuclar = [Sonuc("H221", "b", H221), Sonuc("YETKI", "m", YETKI)]
    sadik = H221 + " " + YETKI.replace("Merhaba, ", "Ayrıca ") + " " + KAPANIS
    t = kur(sonuclar, KAPANIS, SahteLLM(sadik), "soru")
    assert t.yontem == "llm" and t.metin == sadik and t.durum == "otomatik"
    uydurma = sadik + " Bu durum 2019 yılından beri sürmektedir."
    t = kur(sonuclar, KAPANIS, SahteLLM(uydurma), "soru")
    assert t.yontem == "sablon" and "2019" not in t.metin and t.uyarilar and t.durum == "otomatik"
    t = kur(sonuclar, KAPANIS, SahteLLM(None), "soru")
    assert t.yontem == "sablon" and any("alınamadı" in u for u in t.uyarilar)
    t = kur([sonuclar[0]], KAPANIS, SahteLLM("x"), "soru")          # tek kural: LLM'e hiç gidilmez
    assert t.yontem == "sablon" and not t.uyarilar
