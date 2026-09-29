"""Not kalitesi son islemleri (UYGULAMA_PLANI_v2 bolum 0, S1-S5) ve K8 sorumlu dogrulama.
Gercek bir notta gorulen belirtilerin yapay ornekleri; LLM gerekmez."""
import json

import pytest

import llm
import motor as motor_mod

KATILIMCILAR = ["Elif Bala", "Cemil Kahveci", "Berk Nacar", "Mert Ali Erentürk"]


@pytest.fixture(autouse=True)
def yerel_token(monkeypatch):
    monkeypatch.setattr(llm, "token_say", llm.token_tahmin)


# ---------- S1: bos maddeli aksiyon ----------

def test_normalize_ozet_bos_maddeyi_atar():
    o = llm.normalize_ozet({"ozet": "x", "aksiyonlar": [
        {"madde": "", "sorumlu": "Berk Nacar", "tarih": "-"},
        {"madde": "-", "sorumlu": "Elif Bala", "tarih": "-"},
        {"madde": "a", "sorumlu": "Elif Bala", "tarih": "-"},
        {"madde": "MVP prototipini hazırla", "sorumlu": "Elif Bala", "tarih": "-"}, " ", "Tabloyu ilet"]})
    assert [a["madde"] for a in o["aksiyonlar"]] == ["MVP prototipini hazırla", "Tabloyu ilet"]


def test_kisi_bazli_bos_maddeyi_atlar():
    g = dict(llm.kisi_bazli([{"madde": "", "sorumlu": "Berk Nacar"}, {"madde": "Örnek hazırla", "sorumlu": "Elif Bala"}]))
    assert "Berk Nacar" not in g and g["Elif Bala"] == ["Örnek hazırla"]


ISLER = ["MVP prototipini hazırla", "Girdi tablolarını paylaş", "Workshop takvimini çıkar", "Bütçe onayını al",
         "Test ortamını kur", "Sunum dosyasını güncelle", "Kayıtları Excel'e aktar", "Jira kaydı aç",
         "Ekran tasarımını çiz", "Kullanıcı rehberini yaz"]


def _bolumler(n=5):
    return [{"ozet": "Elif MVP'yi anlattı.", "konular": ["MVP"], "kararlar": [],
             "aksiyonlar": [{"madde": m, "sorumlu": "Elif Bala", "tarih": "-"} for m in ISLER[:n]],
             "acik_sorular": []}]


def _aks(maddeler):
    return [{"madde": m, "sorumlu": "Elif Bala", "tarih": "-"} for m in maddeler]


def test_liste_birlestirme_bos_madde_orani_yuksekse_yerel_yedek(monkeypatch):
    """LLM 5 aksiyonun 3'unu bos madde ile dondurdu: eskiden bos maddeler 'var' sayiliyor, tablo '-' doluyordu."""
    v = {"kararlar": [], "acik_sorular": [], "aksiyonlar": _aks(ISLER[:2] + ["", "", "-"])}
    monkeypatch.setattr(llm, "sor", lambda *a, **k: (json.dumps(v, ensure_ascii=False), "stop"))
    loglar = []
    sonuc = llm.listeleri_birlestir(_bolumler(), "Toplantı: test", loglar.append)
    assert [a["madde"] for a in sonuc["aksiyonlar"]] == ISLER[:5]
    assert any("maddesi boş" in m for m in loglar)


def test_liste_birlestirme_kayip_yalniz_dolu_maddeleri_sayar(monkeypatch):
    # 1 bos / 5 (%20, esik asilmiyor) ama 4 dolu madde yedegin (10) yarisindan az: yerel yedege dusulur.
    # Eskiden bos madde de sayiliyordu (5 >= 5): LLM sonucu kabul edilip tabloya '-' satiri giriyordu.
    v = {"kararlar": [], "acik_sorular": [], "aksiyonlar": _aks(ISLER[:4] + [""])}
    monkeypatch.setattr(llm, "sor", lambda *a, **k: (json.dumps(v, ensure_ascii=False), "stop"))
    sonuc = llm.listeleri_birlestir(_bolumler(10), "Toplantı: test", lambda m: None)
    assert len(sonuc["aksiyonlar"]) == 10


def test_liste_birlestirme_dolu_sonuc_kabul(monkeypatch):
    v = {"kararlar": [], "acik_sorular": [], "aksiyonlar": _aks(ISLER[:5])}
    v["aksiyonlar"][0]["sorumlu"] = "Berk Nacar"
    monkeypatch.setattr(llm, "sor", lambda *a, **k: (json.dumps(v, ensure_ascii=False), "stop"))
    sonuc = llm.listeleri_birlestir(_bolumler(), "Toplantı: test", lambda m: None)
    assert sonuc["aksiyonlar"][0]["sorumlu"] == "Berk Nacar"


# ---------- S2: kararlarda aksiyon ----------

def test_karar_aksiyon_ayikla():
    listeler = {"kararlar": ["Girdi tabloları Cemil tarafından paylaşılacak",
                             "Mert Ali Erentürk bu konuyu takip edecek",
                             "Son iki yıllık kayıtlar istenecek",
                             "Chatbot ana sayfaya eklenecek (Cemil önerdi, Elif onayladı)",
                             "Jira kaydı açılacak",
                             "Pilot kapsamı çağrı merkeziyle sınırlı tutulacak"],
                "aksiyonlar": [{"madde": "Jira kaydı aç", "sorumlu": "Berk Nacar", "tarih": "-"}],
                "acik_sorular": []}
    v = llm.karar_aksiyon_ayikla(listeler, KATILIMCILAR)
    # (a) aksiyona benzeyen karar duser; (b) ad + is fiili: aksiyona tasinir
    assert "Jira kaydı açılacak" not in v["kararlar"]
    tasinan = {a["madde"]: a["sorumlu"] for a in v["aksiyonlar"]}
    assert tasinan["Girdi tabloları Cemil tarafından paylaşılacak"] == "Cemil Kahveci"
    assert tasinan["Mert Ali Erentürk bu konuyu takip edecek"] == "Mert Ali Erentürk"
    # adsiz is fiili ve karar vereni gosteren parantez: karar olarak kalir
    assert "Son iki yıllık kayıtlar istenecek" in v["kararlar"]
    assert "Chatbot ana sayfaya eklenecek (Cemil önerdi, Elif onayladı)" in v["kararlar"]
    assert "Pilot kapsamı çağrı merkeziyle sınırlı tutulacak" in v["kararlar"]
    assert len(v["aksiyonlar"]) == 3
    assert listeler["aksiyonlar"] == [{"madde": "Jira kaydı aç", "sorumlu": "Berk Nacar", "tarih": "-"}]   # girdi degismez


def test_tasinan_karar_benzeri_varsa_eklenmez():
    listeler = {"kararlar": ["Girdi tabloları Cemil tarafından paylaşılacak"],
                "aksiyonlar": [{"madde": "Girdi tabloları paylaşılacak", "sorumlu": "Cemil Kahveci", "tarih": "-"}],
                "acik_sorular": []}
    v = llm.karar_aksiyon_ayikla(listeler, KATILIMCILAR)
    assert v["kararlar"] == [] and len(v["aksiyonlar"]) == 1


# ---------- S4: tek kelimelik parantez ----------

def test_karar_parantez_temizle():
    assert llm.karar_parantez_temizle(["Pilot çağrı merkeziyle başlayacak (Elif)",
                                       "Workshop yapılacak (Elif).",
                                       "Chatbot eklenecek (Cemil önerdi, Elif onayladı)",
                                       "PD kolonu number olacak (?)"]) == [
        "Pilot çağrı merkeziyle başlayacak", "Workshop yapılacak.",
        "Chatbot eklenecek (Cemil önerdi, Elif onayladı)", "PD kolonu number olacak (?)"]


# ---------- S5: genel/belirsiz aksiyon ----------

def test_belirsiz_kisa_kaynaksiz_aksiyon_duser():
    satirlar = [{"ts": "10:00:00", "speaker": "Elif Bala", "text": "Örnek dosyaları yarın paylaşırım"}]
    bul = motor_mod.kaynak_bulucu(satirlar)
    aks = [{"madde": "Gereksinimleri anlamak", "sorumlu": "belirsiz", "tarih": "-"},
           {"madde": "Örnek dosyaları paylaşmak", "sorumlu": "belirsiz", "tarih": "-"},
           {"madde": "Ne yapılabilir belirlemek", "sorumlu": "Elif Bala", "tarih": "-"},
           {"madde": "Gereksinimleri anlamak ve kapsamı birlikte netleştirmek", "sorumlu": "belirsiz", "tarih": "-"}]
    kalan = [a["madde"] for a in llm.belirsiz_aksiyonlari_ele(aks, bul)]
    assert kalan == ["Örnek dosyaları paylaşmak", "Ne yapılabilir belirlemek",
                     "Gereksinimleri anlamak ve kapsamı birlikte netleştirmek"]
    assert len(llm.belirsiz_aksiyonlari_ele(aks, None)) == 4          # kaynak bulucu yoksa dokunulmaz


# ---------- S3: acik sorular ----------

def test_acik_sorulari_temizle():
    sorular = ["Varun(?) kimdir veya hangi sistemdir? Katılımcı listesinde geçmiyor.",
               "Veri ne zaman gelecek? (Cemil: 'bakarız, belki')",
               "(Elif Bala 'emin değilim' dedi)",
               "Rapor sunucusunun DB'sinden veri çekme imkânı var mı?",
               "Rapor sunucusundaki veriye erişim mümkün mü?",
               "Test ortamı ne zaman hazır olacak?",
               "Otomatik raporlama için bir sonraki somut adım ne olacak?",
               "Bütçe onayı kimden alınacak?"]
    v = llm.acik_sorulari_temizle(sorular, ["Test ortamı perşembe hazır olacak"], [],
                                  "Otomatik raporlama için bir sonraki somut adım pilot kapsamının netleşmesi.")
    assert v == ["Veri ne zaman gelecek?", "Rapor sunucusunun DB'sinden veri çekme imkânı var mı?",
                 "Bütçe onayı kimden alınacak?"]


def test_acik_sorular_en_fazla_on():
    sorular = ["Bütçe onayı kimden alınacak?", "Pilot hangi şubede başlayacak?", "Veri saklama süresi ne olacak?",
               "Lisans maliyetini kim karşılayacak?", "Hukuk görüşü gerekiyor mu?", "Canlıya geçiş tarihi belli mi?",
               "Eğitim ihtiyacı nasıl karşılanacak?", "Raporlar hangi sıklıkla güncellenecek?",
               "Kullanıcı yetkileri nasıl verilecek?", "Destek ekibi hazır mı?", "Yedekleme planı var mı?"]
    assert len(llm.acik_sorulari_temizle(sorular)) == 10


# ---------- birlestir: temizlenmis listeler nota girer ----------

def test_birlestir_temiz_listeleri_yazar(monkeypatch):
    liste = {"kararlar": ["Pilot çağrı merkeziyle başlayacak (Elif)", "Girdi tabloları Cemil tarafından paylaşılacak"],
             "aksiyonlar": [{"madde": "MVP prototipini hazırla", "sorumlu": "Elif Bala", "tarih": "-"},
                            {"madde": "", "sorumlu": "Berk Nacar", "tarih": "-"}],
             "acik_sorular": ["Varun kimdir?", "Bütçe onayı kimden alınacak?"]}
    genel = {"ozet": "Toplantıda çağrı merkezi pilotu ve MVP prototipi konuşuldu, girdi tabloları ele alındı.",
             "sonraki_adim": "MVP prototipi hazırlanacak."}

    def sahte(mesajlar, max_tokens, sema=None, **k):
        v = liste if mesajlar[0]["content"] == llm.LISTE_SISTEM else genel
        return json.dumps(v, ensure_ascii=False), "stop"
    monkeypatch.setattr(llm, "sor", sahte)
    bolumler = [{"ozet": "Pilot ve MVP konuşuldu.", "konular": ["pilot"], "kararlar": liste["kararlar"],
                 "aksiyonlar": liste["aksiyonlar"][:1] * 2, "acik_sorular": liste["acik_sorular"]}]
    adimlar = []
    md = llm.birlestir(bolumler, "Toplantı: test", lambda m: None, katilimcilar=KATILIMCILAR,
                       aksiyon_fn=lambda aks: [dict(a, tarih="2026-10-01") for a in aks], adim_fn=adimlar.append)
    kararlar = md.split("## Kararlar\n", 1)[1].split("\n\n", 1)[0]
    assert kararlar == "- Pilot çağrı merkeziyle başlayacak"
    assert "| Girdi tabloları Cemil tarafından paylaşılacak | Cemil Kahveci | 2026-10-01 |" in md
    assert "| - | Berk Nacar |" not in md
    sorular = md.split("## Açık sorular\n", 1)[1].split("\n\n", 1)[0]
    assert sorular == "- Bütçe onayı kimden alınacak?"
    assert adimlar == ["Kararlar ve aksiyonlar birleştiriliyor", "Özet paragrafı yazılıyor"]


def test_prompt_kurallari():
    assert "parantezle tek ad ekleme" in llm.MAP_SISTEM
    assert "BİTİNCE BELLİ OLAN" in llm.MAP_SISTEM and "'konular'a gider" in llm.MAP_SISTEM
    assert "en fazla 4" in llm.MAP_SISTEM and "belirsiz_terimler'e" in llm.MAP_SISTEM
    assert "toplam en fazla 8" in llm.LISTE_SISTEM


# ---------- K8: sorumlu dogrulama ----------

SATIRLAR = [
    {"ts": "10:01:00", "speaker": "Elif Bala", "text": "MVP prototipini ben hazırlarım, haftaya gösteririz"},
    {"ts": "10:02:00", "speaker": "Cemil Kahveci", "text": "Girdi tablolarını Berk'e ileteceğim, o kontrol etsin"},
    {"ts": "10:03:00", "speaker": "?", "text": "Sunum dosyasını ben güncellerim"},
    {"ts": "10:04:00", "speaker": "Berk Nacar", "text": "Test ortamı perşembe hazır olur"},
]


def test_sorumlu_dogrula():
    aks = [{"madde": "MVP prototipini hazırlamak", "sorumlu": "belirsiz", "tarih": "-"},       # 1: belirsiz -> konusan
           {"madde": "MVP prototipini hazırlamak", "sorumlu": "Cemil Kahveci", "tarih": "-"},  # 2: baskasi -> (?)
           {"madde": "Girdi tablolarını iletmek", "sorumlu": "Berk Nacar", "tarih": "-"},      # 3: satirda adi geciyor
           {"madde": "Sunum dosyasını güncellemek", "sorumlu": "belirsiz", "tarih": "-"},      # 4: konusan '?'
           {"madde": "Test ortamını hazırlamak", "sorumlu": "Elif Bala", "tarih": "-"},        # 5: 1. tekil sahis yok
           {"madde": "MVP prototipini hazırlamak", "sorumlu": "Elif", "tarih": "-"}]           # 6: ayni kisi (ilk ad)
    v = motor_mod.sorumlu_dogrula(aks, SATIRLAR, ben="Mert Ali Erentürk")
    assert [a["sorumlu"] for a in v] == ["Elif Bala", "Cemil Kahveci (?)", "Berk Nacar", "belirsiz", "Elif Bala", "Elif"]
    assert aks[0]["sorumlu"] == "belirsiz"                              # girdi degismez


def test_sorumlu_dogrula_ben_satiri():
    satirlar = [{"ts": "10:00:00", "speaker": "Ben", "text": "Raporu cumaya kadar ben göndereceğim"}]
    v = motor_mod.sorumlu_dogrula([{"madde": "Raporu cumaya kadar göndermek", "sorumlu": "belirsiz", "tarih": "-"}],
                                  satirlar, ben="Mert Ali Erentürk")
    assert v[0]["sorumlu"] == "Mert Ali Erentürk"
