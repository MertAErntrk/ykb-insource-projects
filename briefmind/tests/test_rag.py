import numpy as np

import llm
import rag


def satir(ts, kim, metin):
    return {"ts": ts, "speaker": kim, "text": metin}


SATIRLAR = [satir(f"00:0{i}:00", "Ayşe" if i % 2 else "Mehmet", m) for i, m in enumerate([
    "Günaydın, başlayalım.",
    "Bütçe onayını finans ekibi gelecek hafta verecek.",
    "Test ortamı perşembe günü hazır olacak.",
    "Kampanya görselleri tasarım ekibinde.",
    "Müşteri şikayetleri geçen aya göre azaldı.",
    "Sunucu kapasitesini artırmamız gerekiyor.",
    "Tamam, teşekkürler herkese.",
])]


def test_pencereler_ortusur_ve_hepsini_kapsar():
    p = rag.pencereler(SATIRLAR, boy=3, ortusme=1)
    assert p[0]["bas"] == "00:00:00" and p[-1]["son"] == "00:06:00"
    assert "Mehmet: Günaydın" in p[0]["metin"]
    assert all(len(x["metin"].splitlines()) <= 3 for x in p)
    assert rag.pencereler([]) == []


def test_bm25_turkce_ek_ve_karakter_duyarsiz():
    belgeler = ["butceyi finans onaylayacak", "test ortami hazir", "kampanya gorseli"]
    puan = rag.bm25("Bütçe onayı ne zaman?", belgeler)
    assert puan[0] > 0 and puan[1] == 0 and puan[2] == 0


def test_rrf_iki_listede_de_ust_siradaki_kazanir():
    assert rag.rrf([0, 1, 2], [1, 0, 2])[:2] in ([0, 1], [1, 0])
    assert rag.rrf([2, 0], [2, 1])[0] == 2


def test_embedding_yoksa_kelime_aramasi(monkeypatch):
    monkeypatch.setattr(rag, "URL", "")
    metinler = [p["metin"] for p in rag.pencereler(SATIRLAR, boy=1, ortusme=0)]
    sira, yontem = rag.hibrit_sirala("test ortamı ne zaman hazır", metinler)
    assert yontem == "kelime" and sira[0] == 2 and sorted(sira) == list(range(len(metinler)))


def _sahte_gomucu(cagri):
    anahtar = ["butce", "test ortam", "sunucu", "kampanya"]

    def gom(metinler, sorgu=False):
        cagri.append((len(metinler), sorgu))
        m = np.array([[1.0 if k in rag._katla(t) else 0.0 for k in anahtar] + [0.01] for t in metinler])
        return rag._normalize(m)
    return gom


def test_hibrit_anlam_ve_onbellek(tmp_path, monkeypatch):
    cagri = []
    monkeypatch.setattr(rag, "URL", "http://sahte")
    monkeypatch.setattr(rag, "_http", object())
    monkeypatch.setattr(rag, "gom", _sahte_gomucu(cagri))
    metinler = [p["metin"] for p in rag.pencereler(SATIRLAR, boy=1, ortusme=0)]
    # sorguda transkriptle ortak kelime yok: yalnizca anlam (vektor) bulabilir
    sira, yontem = rag.hibrit_sirala("Sunucu?", metinler, str(tmp_path))
    assert yontem == "hibrit" and sira[0] == 5
    assert (tmp_path / rag.DIZIN_AD).exists()
    cagri.clear()
    rag.hibrit_sirala("bütçe", metinler + ["Yeni satır: kampanya bitti"], str(tmp_path))
    assert cagri[0] == (1, False)                     # yalnizca yeni pencere gomuldu


def test_embedding_hatasinda_kelimeye_duser(monkeypatch):
    olaylar = []
    monkeypatch.setattr(rag, "URL", "http://sahte")
    monkeypatch.setattr(rag, "_http", object())

    def bozuk(metinler, sorgu=False):
        raise RuntimeError("embedding HTTP 503")
    monkeypatch.setattr(rag, "gom", bozuk)
    sira, yontem = rag.hibrit_sirala("test ortamı", ["a", "test ortami hazir"], olay=olaylar.append)
    assert yontem == "kelime" and sira[0] == 1 and olaylar and "503" in rag.SON_HATA


def test_ayarla_adres_sonunu_kirpar():
    eski = (rag.URL, rag.BOYUT)
    try:
        rag.ayarla({"embed_url": "https://x.example/v1/embeddings/", "embed_boyut": 512})
        assert rag.URL == "https://x.example" and rag.BOYUT == 512 and rag.aktif()
        assert rag._normalize(np.ones((1, 4096))).shape == (1, 512)
    finally:
        rag.ayarla({})
        rag.URL, rag.BOYUT = "", eski[1]


def test_toplantiya_sor_verilen_sirayi_kullanir(monkeypatch):
    gelen = {}

    def sor(mesajlar, *a, **k):
        gelen["icerik"] = mesajlar[1]["content"]
        return "Perşembe.", "stop"
    monkeypatch.setattr(llm, "sor", sor)
    monkeypatch.setattr(llm, "token_say", lambda s: 10 ** 6 if "DEV" in s else 10)
    bolumler = [("a", "ilk bolum"), ("b", "DEV bolum"), ("c", "test ortami persembe")]
    llm.toplantiya_sor("test?", bolumler, "", sira=[2, 1, 0])
    assert "test ortami persembe" in gelen["icerik"]
