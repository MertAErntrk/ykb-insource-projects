import katalog as katalog_mod
from siniflandirici import cifler, kural_kodlari, siniflandir

K = katalog_mod.Katalog.yukle()


def test_kural_kodu_ve_cif():
    s = siniflandir("Merhaba, 10000001 numaralı müşteride H221 kuralı çalışıyor, neden?", K)
    assert s.sinif == "cikti" and s.kural_kodlari == ["H221"] and s.cifler == ["10000001"] and s.yontem == "kural"


def test_kod_yazimlari():
    assert kural_kodlari("H 221 ve H-221 ve h221") == ["H221"]
    assert kural_kodlari("SM 123 ve H221", bilinen={"H221"}) == ["H221"]       # katalogda olmayan, H dışı kod atılır
    assert kural_kodlari("SM 123 ve H221") == ["SM123", "H221"]


def test_anahtar_kelimeyle_konu():
    s = siniflandir("10000001 numaralı müşterinin onay yetki seviyesi neden 8 çıktı?", K)
    assert s.kural_kodlari == ["YETKI"] and s.sinif == "cikti"


def test_siniflar():
    assert siniflandir("10000002 müşterisinde strateji ekranı hata veriyor, sonuç gelmedi.", K).sinif == "hata"
    assert siniflandir("Müşterinin KKB verisi stratejiye yanlış geliyor, güncel değil.", K).sinif == "girdi"
    assert siniflandir("", K).sinif == "belirsiz"
    assert siniflandir("Bu müşteri için limit kararını neden böyle verdi?", K).sinif == "cikti"


def test_cif_deseni():
    assert cifler("1234567890 ve 12.345.678 ve 10000001 ve 10000001") == ["10000001"]
    assert cifler("CIF: 10000003, diğer 10000004") == ["10000003", "10000004"]


class SahteLLM:
    def __init__(self, cevap):
        self.cevap = cevap

    def json_cevap(self, sistem, kullanici, sema, max_tokens=400):
        return self.cevap


def test_llm_zenginlestirme_guardrail():
    metin = "10000001 müşterisinde H221 neden çalıştı?"
    llm = SahteLLM({"sinif": "girdi", "kural_kodlari": ["H 221", "H777", "YETKI"], "cifler": ["99999999", "10000001"]})
    s = siniflandir(metin, K, llm)
    assert s.sinif == "girdi" and s.yontem == "llm"
    assert s.kural_kodlari == ["H221", "YETKI"]          # H777 ne metinde ne katalogda → atıldı; YETKI katalogda
    assert s.cifler == ["10000001"]                      # 99999999 metinde yok → atıldı


def test_llm_bozuk_cevap_zarar_vermez():
    s = siniflandir("10000001 H221", K, SahteLLM(None))
    assert s.sinif == "cikti" and s.kural_kodlari == ["H221"] and s.yontem == "kural"
    s = siniflandir("10000001 H221", K, SahteLLM({"sinif": "uydurma"}))
    assert s.sinif == "cikti" and s.yontem == "kural"
