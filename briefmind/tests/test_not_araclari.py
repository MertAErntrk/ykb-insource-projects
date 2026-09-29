"""Kaynaga gitme, kisi bazli liste, sablon, arama, e-posta, Word, saklama suresi, toplantiya soru."""
import datetime as dt
import json
import os

import llm
import motor as motor_mod
import not_araclari

SATIRLAR = [
    {"ts": "10:02:00", "speaker": "Elif Bala", "text": "MVP prototipini ben hazırlarım, edite edilebilir olsun"},
    {"ts": "10:05:30", "speaker": "Berk Nacar", "text": "Ara tabloyu Excel olarak bana iletin, Mert Ali ile incelerim"},
    {"ts": "10:09:10", "speaker": "Cemil Kahveci", "text": "Follow-up toplantılarını 2-3 haftada bir yapalım"},
]
LISTELER = {"kararlar": ["Follow-up toplantıları 2-3 haftada bir yapılacak"],
            "aksiyonlar": [{"madde": "Edite edilebilir MVP prototipi hazırlamak", "sorumlu": "Elif Bala", "tarih": "-"},
                           {"madde": "Ara tabloyu Excel olarak incelemek", "sorumlu": "Berk Nacar, Mert Ali Erentürk",
                            "tarih": "-"},
                           {"madde": "Örnekleri hazırlamak", "sorumlu": "Elif Bala", "tarih": "-"},
                           {"madde": "Kayıtları iletmek", "sorumlu": "belirsiz", "tarih": "-"}],
            "acik_sorular": []}


def _klasor(tmp_path, durum="tamam", tarih="2026-06-01"):
    k = tmp_path / "toplantilar" / f"{tarih}_strateji"
    (k / "parcalar").mkdir(parents=True)
    (k / "meta.json").write_text(json.dumps({"baslik": "Strateji", "tarih": tarih, "durum": durum, "parca": 1}),
                                 encoding="utf-8")
    (k / "parcalar" / "parca_001.json").write_text(json.dumps(
        {"sira": 1, "baslangic": "10:02:00", "bitis": "10:09:10", "satirlar": SATIRLAR, "duzeltmeler": [],
         "ozet": {"ozet": "MVP ve follow-up konuşuldu."}}, ensure_ascii=False), encoding="utf-8")
    (k / "altyazi.jsonl").write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in SATIRLAR), encoding="utf-8")
    return k


def test_kaynak_bulucu_ve_not_markdown():
    bul = motor_mod.kaynak_bulucu(SATIRLAR)
    assert bul("Edite edilebilir MVP prototipi hazırlamak", "Elif Bala") == "10:02:00"
    assert bul("Follow-up toplantıları 2-3 haftada bir yapılacak") == "10:09:10"
    assert bul("Bütçe onayı alınacak") is None
    md = llm.not_markdown({"ozet": "Özet paragrafı burada.", "sonraki_adim": "-"}, LISTELER, bul)
    assert "- Follow-up toplantıları 2-3 haftada bir yapılacak ⏱10:09:10" in md
    assert "| # | Madde | Sorumlu | Tarih | Kaynak |" in md and "| ⏱10:02:00 |" in md
    assert "## Kişiye göre iş listesi" in md
    assert "- Elif Bala (2): Edite edilebilir MVP prototipi hazırlamak; Örnekleri hazırlamak" in md
    assert "- Mert Ali Erentürk (1): Ara tabloyu Excel olarak incelemek" in md
    assert md.index("- belirsiz (1)") > md.index("- Elif Bala")          # belirsiz en sonda
    # dayanak kontrolu kisi listesini ve kaynak isaretlerini bozmaz
    yeni, atilan = motor_mod.dayanak_kontrolu("# T\n\n" + md, " ".join(s["text"] for s in SATIRLAR))
    assert "## Kişiye göre iş listesi" in yeni and "⏱10:09:10" in yeni


def test_sablon_talimati_baglama_eklenir(monkeypatch):
    goren = []

    def sahte(mesajlar, max_tokens, sema=None, effort="medium", temperature=0.6, dusunme=True):
        goren.append(mesajlar[1]["content"])
        return json.dumps({"ozet": "Toplantıda ilerleme ve engeller ayrıntılı olarak konuşuldu, plan yapıldı.",
                           "sonraki_adim": "-", "kararlar": [], "aksiyonlar": [], "acik_sorular": []}), "stop"

    monkeypatch.setattr(llm, "sor", sahte)
    monkeypatch.setattr(llm, "token_say", llm.token_tahmin)
    llm.birlestir([{"ozet": "a", "konular": [], "kararlar": [], "aksiyonlar": [], "acik_sorular": []}],
                  "Toplantı: x", sablon="haftalik")
    assert any("ilerleme, engeller" in g for g in goren)


def test_arama_ana_git_aksiyon_ayikla_eposta_word(tmp_path):
    k = _klasor(tmp_path)
    sonuc = not_araclari.ara("excel ILETIN", kok=str(tmp_path / "toplantilar"))
    assert len(sonuc) == 1 and sonuc[0]["kim"] == "Berk Nacar" and sonuc[0]["ts"] == "10:05:30"
    pencere = not_araclari.ana_git(str(k), "10:05:30", pencere_sn=120)
    assert [x[0] for x in pencere] == ["10:05:30"] or any(x[3] and x[0] == "10:05:30" for x in pencere)
    md = llm.not_markdown({"ozet": "Özet.", "sonraki_adim": "-"}, LISTELER, motor_mod.kaynak_bulucu(SATIRLAR))
    aks = not_araclari.aksiyonlari_ayikla(md)
    assert len(aks) == 4 and aks[0]["sorumlu"] == "Elif Bala" and aks[0]["kaynak"] == "⏱10:02:00"
    epostalar = not_araclari.kisiye_ozel_epostalar(md, "Strateji", "2026-09-23")
    adlar = [e[0] for e in epostalar]
    assert adlar[0] == "Elif Bala" and "belirsiz" not in adlar and "Mert Ali Erentürk" in adlar
    assert "Örnekleri hazırlamak" in epostalar[0][2] and "Kayıtları iletmek" not in epostalar[0][2]
    yol = not_araclari.word_kaydet("# T\n\n" + md, str(tmp_path / "n.docx"))
    import docx
    metin = "\n".join(p.text for p in docx.Document(yol).paragraphs)
    assert "Kişiye göre iş listesi" in metin and len(docx.Document(yol).tables) == 1


def test_saklama_suresi(tmp_path):
    kok = str(tmp_path / "toplantilar")
    eski = _klasor(tmp_path, tarih="2026-06-01")
    yeni = _klasor(tmp_path, tarih="2026-09-25")
    (eski / "not.md").write_text("# not", encoding="utf-8")
    bugun = dt.date(2026, 9, 29)
    assert motor_mod.saklama_uygula(0, kok, bugun) == 0                    # kapali
    assert motor_mod.saklama_uygula(90, kok, bugun) == 1
    assert not (eski / "altyazi.jsonl").exists() and (yeni / "altyazi.jsonl").exists()
    p = json.loads((eski / "parcalar" / "parca_001.json").read_text(encoding="utf-8"))
    assert p["satirlar"] == [] and p["ozet"]                               # ozet kalir
    assert (eski / "not.md").exists()
    assert json.loads((eski / "meta.json").read_text(encoding="utf-8"))["transkript_silindi"] == "2026-09-29"
    assert motor_mod.saklama_uygula(90, kok, bugun) == 0                   # ikinci kez dokunmaz
    # yarim (durumu 'tamam' olmayan) toplanti silinmez
    yarim = _klasor(tmp_path, durum="devam", tarih="2026-01-01")
    assert motor_mod.saklama_uygula(90, kok, bugun) == 0 and (yarim / "altyazi.jsonl").exists()


def test_kaydi_acmak_durumu_bozmaz(tmp_path):
    k = _klasor(tmp_path)
    m = motor_mod.Motor.yukle(str(k))
    m.kapat()
    assert json.loads((k / "meta.json").read_text(encoding="utf-8"))["durum"] == "tamam"


def test_toplantiya_soru_ilgili_bolumu_secer(monkeypatch):
    goren = {}

    def sahte(mesajlar, max_tokens, sema=None, effort="medium", temperature=0.6, dusunme=True):
        goren["icerik"] = mesajlar[1]["content"]
        return "Elif Bala hazırlayacak ⏱10:02:00", "stop"

    monkeypatch.setattr(llm, "sor", sahte)
    monkeypatch.setattr(llm, "token_say", llm.token_tahmin)
    monkeypatch.setattr(llm, "BAGLAM_PENCERESI", 2600)                    # tek bolum sigacak kadar dar
    bolumler = [("09:00–09:10", "[09:01:00] Ali: Bütçe konuşuldu. " * 40),
                ("10:00–10:10", "[10:02:00] Elif Bala: MVP prototipini ben hazırlarım"),
                ("11:00–11:10", "[11:01:00] Ayşe: Kampanya takvimi. " * 40)]
    cevap = llm.toplantiya_sor("MVP prototipini kim hazırlayacak?", bolumler, "Toplantı: x")
    assert "Elif Bala" in cevap and "MVP prototipini" in goren["icerik"]
    assert "Kampanya takvimi" not in goren["icerik"]
