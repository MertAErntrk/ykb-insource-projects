"""Not uretimi: Ingilizce/kesik LLM cevaplarinda notun Turkce ve eksiksiz kalmasi.
LLM sunucusu gerekmez; llm.sor taklit edilir.   python -m pytest tests
"""
import datetime as dt
import json

import pytest

import llm
import motor as motor_mod

BOLUMLER = [
    {"ozet": "Ahmet IFRS 9 raporunun durumunu anlattı, veri tipi sorunu görüşüldü.",
     "konular": ["IFRS 9 raporu"], "kararlar": ["PD kolonu number tipine çevrilecek"],
     "aksiyonlar": [{"madde": "PD kolonunu düzelt", "sorumlu": "Ahmet", "tarih": "-"}],
     "acik_sorular": ["Test ortamı ne zaman hazır olacak?"], "aralik": "10:00:05–10:11:40"},
    {"ozet": "Ayşe test ortamının perşembe hazır olacağını söyledi, Jira kaydı açılması kararlaştırıldı.",
     "konular": ["test ortamı"], "kararlar": ["PD kolonu number tipine çevrilecek.", "Jira kaydı açılacak"],
     "aksiyonlar": [{"madde": "PD kolonunu düzelt", "sorumlu": "belirsiz", "tarih": "2026-10-01 (Perşembe)"},
                    {"madde": "Jira kaydı aç", "sorumlu": "Ayşe", "tarih": "-"}],
     "acik_sorular": [], "aralik": "10:11:41–10:20:00"},
]

LISTE_TR = {"kararlar": ["PD kolonu number tipine çevrilecek", "Jira kaydı açılacak"],
            "aksiyonlar": [{"madde": "PD kolonunu düzelt", "sorumlu": "Ahmet", "tarih": "2026-10-01 (Perşembe)"},
                           {"madde": "Jira kaydı aç", "sorumlu": "Ayşe", "tarih": "-"}],
            "acik_sorular": []}
GENEL_TR = {"ozet": "Toplantı IFRS 9 raporunun durumuyla başladı; veri tipi sorunu görüşüldü ve test ortamının "
                    "perşembe hazır olacağı belirtildi.",
            "sonraki_adim": "PD kolonu düzeltilecek ve Jira kaydı açılacak."}
GENEL_EN = {"ozet": "The meeting started with the status of the IFRS 9 report and the team discussed the data "
                    "type issue. It was decided that a Jira ticket will be opened.",
            "sonraki_adim": "Fix the PD column."}


class SahteLLM:
    """Sistem mesajina gore cevap secer; her cagriyi kaydeder."""

    def __init__(self, liste=None, genel=None):
        self.liste = liste or [(json.dumps(LISTE_TR, ensure_ascii=False), "stop")]
        self.genel = genel or [(json.dumps(GENEL_TR, ensure_ascii=False), "stop")]
        self.cagrilar = []

    def __call__(self, mesajlar, max_tokens, sema=None, effort="medium", temperature=0.6, dusunme=True):
        sistem = mesajlar[0]["content"]
        self.cagrilar.append({"sistem": sistem[:40], "max_tokens": max_tokens, "dusunme": dusunme})
        kuyruk = self.liste if sistem == llm.LISTE_SISTEM else self.genel
        ham, neden = kuyruk[0] if len(kuyruk) == 1 else kuyruk.pop(0)
        return llm.dusunce_temizle(ham), neden


@pytest.fixture(autouse=True)
def yerel_token(monkeypatch):
    monkeypatch.setattr(llm, "token_say", llm.token_tahmin)


def test_dusunce_temizle():
    assert llm.dusunce_temizle("<think>Let me think in English.</think>\n{\"a\": 1}") == '{"a": 1}'
    assert llm.dusunce_temizle("Okay, the user wants a summary...</think>## Özet") == "## Özet"
    assert llm.dusunce_temizle("<think>Okay so the meeting was about") == ""      # dusunurken kesilmis
    assert llm.dusunce_temizle(None) == ""
    assert llm.json_ayikla('<think>{"x": 2} maybe?</think>{"x": 1}') == {"x": 1}


def test_ingilizce_mi():
    assert llm.ingilizce_mi(GENEL_EN["ozet"])
    assert not llm.ingilizce_mi(GENEL_TR["ozet"])
    assert not llm.ingilizce_mi("Pull request'i merge etmeden önce code review yapılacak ve deploy pipeline "
                                "üzerinde test edilecek.")
    assert not llm.ingilizce_mi("Tamam.")


def test_birlestir_normal_akis(monkeypatch):
    sahte = SahteLLM()
    monkeypatch.setattr(llm, "sor", sahte)
    md = llm.birlestir(BOLUMLER, "Toplantı: test")
    for baslik in ("## Özet", "## Kararlar", "## Aksiyonlar", "## Açık sorular", "## Bir sonraki adım"):
        assert baslik in md
    assert "| 1 | PD kolonunu düzelt | Ahmet | 2026-10-01 (Perşembe) |" in md
    assert GENEL_TR["ozet"] in md
    assert all(not c["dusunme"] for c in sahte.cagrilar)          # birlestirme dusunmesiz


def test_birlestir_ingilizce_ozet_yedege_duser(monkeypatch):
    en = (json.dumps(GENEL_EN), "stop")
    monkeypatch.setattr(llm, "sor", SahteLLM(genel=[en, en, en, en]))
    md = llm.birlestir(BOLUMLER, "Toplantı: test")
    assert "The meeting" not in md
    assert BOLUMLER[0]["ozet"] in md and BOLUMLER[1]["ozet"] in md
    assert not llm.ingilizce_mi(md)


def test_birlestir_ingilizce_sonra_turkce(monkeypatch):
    sahte = SahteLLM(genel=[(json.dumps(GENEL_EN), "stop"), (json.dumps(GENEL_TR, ensure_ascii=False), "stop")])
    monkeypatch.setattr(llm, "sor", sahte)
    md = llm.birlestir(BOLUMLER, "Toplantı: test")
    assert GENEL_TR["ozet"] in md


def test_birlestir_kesik_liste_madde_kaybetmez(monkeypatch):
    kesik = ('{"kararlar": ["PD kolonu number tipine çevrilecek"], "aksiyonlar": [{"madde": "PD kol', "length")
    monkeypatch.setattr(llm, "sor", SahteLLM(liste=[kesik, kesik]))
    md = llm.birlestir(BOLUMLER, "Toplantı: test")
    assert "Jira kaydı açılacak" in md
    assert "Jira kaydı aç" in md and "PD kolonunu düzelt" in md
    # ayni karar iki bolumde: tek satir
    assert md.count("PD kolonu number tipine çevrilecek") == 1
    # sonraki bolumde sorumlu 'belirsiz' -> onceki bolumdeki sorumlu korunur, tarih sonrakinden gelir
    assert "| PD kolonunu düzelt | Ahmet | 2026-10-01 (Perşembe) |" in md


def test_birlestir_dusunce_sizintisi(monkeypatch):
    sizinti = ("<think>We need to merge the lists. The user wants Turkish...</think>"
               + json.dumps(LISTE_TR, ensure_ascii=False), "stop")
    ozet_sizinti = ("Okay, let me write the summary in Turkish.</think>" + json.dumps(GENEL_TR, ensure_ascii=False),
                    "stop")
    monkeypatch.setattr(llm, "sor", SahteLLM(liste=[sizinti], genel=[ozet_sizinti]))
    md = llm.birlestir(BOLUMLER, "Toplantı: test")
    assert "let me" not in md and "We need" not in md
    assert GENEL_TR["ozet"] in md


def test_liste_madde_kaybi_reddedilir(monkeypatch):
    bos = (json.dumps({"kararlar": [], "aksiyonlar": [], "acik_sorular": []}), "stop")
    monkeypatch.setattr(llm, "sor", SahteLLM(liste=[bos]))
    md = llm.birlestir(BOLUMLER, "Toplantı: test")
    assert "Jira kaydı açılacak" in md


def test_dayanak_soyut_ozeti_atmaz():
    transkript = ("Ahmet: IFRS 9 raporunda PD kolonu string geliyor, number yapmamız lazım.\n"
                  "Ayşe: Test ortamı perşembe hazır olur, Jira kaydını ben açarım.")
    md = ("# T\n\n## Özet\nIFRS 9 raporundaki veri tipi sorunu görüşüldü. Test ortamının perşembe hazır "
          "olacağı belirtildi.\n\n## Kararlar\n- PD kolonu number yapılacak\n- Yeni bütçe onaylandı\n\n"
          "## Aksiyonlar\n-\n\n## Açık sorular\n-\n\n## Bir sonraki adım\n-\n")
    ozet_kaynak = "IFRS 9 raporundaki veri tipi sorunu görüşüldü. Test ortamının perşembe hazır olacağı belirtildi."
    yeni, atilan = motor_mod.dayanak_kontrolu(md, transkript, ozet_kaynak=ozet_kaynak)
    assert "veri tipi sorunu görüşüldü" in yeni
    assert "Yeni bütçe onaylandı" not in yeni and "Yeni bütçe onaylandı" in atilan
    assert "PD kolonu number yapılacak" in yeni


def test_motor_notu_uret_eksik_bolumu_soyler(tmp_path, monkeypatch):
    monkeypatch.setattr(motor_mod, "KOK", str(tmp_path))
    monkeypatch.setattr(llm, "sor", SahteLLM())
    m = motor_mod.Motor(str(tmp_path / "t"), "Test toplantısı", dt.date(2026, 9, 29), duzelt=False)
    satirlar = [
        {"id": 1, "ts": "10:00:05", "speaker": "Ahmet", "text": "IFRS 9 raporunda PD kolonu string geliyor, "
                                                                 "number tipine çevireceğiz, düzeltmeyi ben yaparım."},
        {"id": 2, "ts": "10:11:50", "speaker": "Ayşe", "text": "Test ortamı perşembe hazır, Jira kaydı açılacak, "
                                                              "kaydı ben açarım."},
        {"id": 3, "ts": "10:25:00", "speaker": "Ahmet", "text": "Son olarak bir şey daha var."},
    ]
    for no, (s, ozet) in enumerate(zip(satirlar, BOLUMLER + [None]), 1):
        o = None if ozet is None else {k: v for k, v in ozet.items() if k != "aralik"}
        if o:
            o["belirsiz_terimler"] = []
        m.parca_no = no
        m.parca_yaz({"sira": no, "neden": "token", "token": 100, "baslangic": s["ts"], "bitis": s["ts"],
                     "satirlar": [s], "duzeltmeler": [], "ozet": o})
    monkeypatch.setattr(m, "_isle", lambda veri, duzelt: None)       # 3. parca yine ozetlenemiyor
    md = m.notu_uret()
    m.kapat()
    assert md.startswith("# Test toplantısı — 2026-09-29")
    assert "## Eksik bölümler" in md and "10:25:00–10:25:00" in md
    assert "Jira kaydı açılacak" in md
    assert (tmp_path / "t" / "not.md").read_text(encoding="utf-8") == md


def test_dusunmeli_duz_metin_semasiz_tekrarlanir(monkeypatch):
    """Sunucu dusunme acikken json_schema uygulamiyor (teshis): duz metin onarilmaz, dusunmesiz tekrar edilir."""
    parca = {"ozet": "Ahmet PD kolonunun number olacağını söyledi.", "konular": ["PD kolonu"], "kararlar": [],
             "aksiyonlar": [], "acik_sorular": [], "belirsiz_terimler": []}
    cagrilar = []

    def sahte(mesajlar, max_tokens, sema=None, effort="medium", temperature=0.6, dusunme=True):
        cagrilar.append(dusunme)
        if dusunme:
            return "Bu bölümde Ahmet PD kolonundan bahsetti.", "stop"
        return json.dumps(parca, ensure_ascii=False), "stop"

    monkeypatch.setattr(llm, "sor", sahte)
    o = llm.bolum_ozetle("Ahmet: PD kolonu number olacak.", 1, "Toplantı: test")
    assert o["ozet"] == parca["ozet"]
    assert cagrilar == [True, False]


def test_yanlis_anahtarli_json_kabul_edilmez(monkeypatch):
    def sahte(mesajlar, max_tokens, sema=None, effort="medium", temperature=0.6, dusunme=True):
        return '{"summary": "The meeting was short."}', "stop"

    monkeypatch.setattr(llm, "sor", sahte)
    assert llm.bolum_ozetle("Ahmet: tamam.", 1, "Toplantı: test") is None
