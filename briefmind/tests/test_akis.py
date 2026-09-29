"""Not akisi (UYGULAMA_PLANI_v2 A, B): 'adim' olaylari, sozlugun parcalara uygulanmasi, Gecmis -> 'Yeniden
ozetle' (yedek + tum parcalar + not), otomatik_not ayari, llm.ayarla. LLM sunucusu gerekmez."""
import json
import os
import threading

import pytest

import ayar as ayar_mod
import llm
import motor as motor_mod
from sozluk import Sozluk

PARCA_OZETI = {"ozet": "Elif MVP prototipini ve test ortamını anlattı.", "konular": ["MVP prototipi"],
               "kararlar": ["Pilot çağrı merkeziyle başlayacak"],
               "aksiyonlar": [{"madde": "MVP prototipini hazırlamak", "sorumlu": "belirsiz", "tarih": "-"}],
               "acik_sorular": [], "belirsiz_terimler": []}
GENEL = {"ozet": "Toplantıda MVP prototipi ve test ortamı konuşuldu; pilotun çağrı merkeziyle başlaması kararlaştırıldı.",
         "sonraki_adim": "MVP prototipi hazırlanacak."}


class SahteLLM:
    def __init__(self):
        self.gorevler = []
        self.kilit = threading.Lock()

    def __call__(self, mesajlar, max_tokens, sema=None, effort="medium", temperature=0.6, dusunme=True):
        gorev = llm._gorev_adi(mesajlar)
        with self.kilit:
            self.gorevler.append(gorev)
        if gorev == "bolum":
            return json.dumps(PARCA_OZETI, ensure_ascii=False), "stop"
        if gorev == "duzeltme":
            return json.dumps({"duzeltmeler": []}), "stop"
        if gorev == "liste":
            return json.dumps({k: PARCA_OZETI[k] for k in ("kararlar", "aksiyonlar", "acik_sorular")},
                              ensure_ascii=False), "stop"
        return json.dumps(GENEL, ensure_ascii=False), "stop"


@pytest.fixture(autouse=True)
def yerel(monkeypatch):
    monkeypatch.setattr(llm, "token_say", llm.token_tahmin)


def _toplanti(tmp_path, parca=3, ozetli=True, not_md=None, olay=None):
    k = tmp_path / "toplantilar" / "2026-09-23_strateji"
    (k / "parcalar").mkdir(parents=True)
    (k / "meta.json").write_text(json.dumps({"baslik": "Strateji", "tarih": "2026-09-23", "durum": "devam",
                                             "katilimcilar": ["Elif Bala", "Cemil Kahveci"], "parca": parca,
                                             "kaynak": "ses"}), encoding="utf-8")
    for no in range(1, parca + 1):
        satirlar = [{"id": no, "ts": f"10:0{no}:00", "speaker": "Elif Bala",
                     "text": f"MVP prototipini ben hazırlarım, komplo tit sonrası test ortamı {no}"}]
        (k / "parcalar" / f"parca_{no:03d}.json").write_text(json.dumps(
            {"sira": no, "neden": "token", "token": 100, "baslangic": satirlar[0]["ts"], "bitis": satirlar[0]["ts"],
             "satirlar": satirlar, "duzeltmeler": [], "ozet": dict(PARCA_OZETI) if ozetli else None},
            ensure_ascii=False), encoding="utf-8")
    if not_md is not None:
        (k / "not.md").write_text(not_md, encoding="utf-8")
    olaylar = []
    m = motor_mod.Motor.yukle(str(k), Sozluk(str(tmp_path / "sozluk.json")),
                              olay=olay or (lambda t, v: olaylar.append((t, v))), duzelt=False,
                              ben="Mert Ali Erentürk")
    return m, k, olaylar


def test_sozlugu_uygula_kirli_parcalari_dondurur(tmp_path):
    m, k, _ = _toplanti(tmp_path)
    assert m.sozlugu_uygula() == []                              # sozluk bos: hicbir parca degismez
    m.sozluk.alias_ekle("komplo tit", "commit")
    kirli = m.sozlugu_uygula()
    assert [v["sira"] for v in kirli] == [1, 2, 3]
    v = m.parca_oku(1)
    assert "commit sonrası" in v["satirlar"][0]["text"] and v["ozet"] is None
    assert m.sozlugu_uygula() == []                              # ikinci kez: zaten uygulanmis
    m.kapat()


def test_kararlari_uygula_adim_olaylari(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "sor", SahteLLM())
    m, k, olaylar = _toplanti(tmp_path, parca=2)
    m.oneriler = [{"id": 1, "parca": 1, "yanlis": "komplo tit", "oneri": "commit", "baglam": "", "tur": "duzeltme",
                   "durum": "bekliyor"}]
    assert m.kararlari_uygula({1: "commit"}) == 2
    adimlar = [v for t, v in olaylar if t == "adim"]
    assert len(adimlar) == 2 and all(a["toplam"] == 2 for a in adimlar)
    assert all(m.parca_oku(n)["ozet"] for n in (1, 2))
    m.kapat()


def test_notu_uret_adim_olaylari_ve_sorumlu_dogrulama(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "sor", SahteLLM())
    m, k, olaylar = _toplanti(tmp_path, parca=2)
    md = m.notu_uret()
    m.kapat()
    adimlar = [v["metin"] for t, v in olaylar if t == "adim"]
    assert adimlar == ["Kararlar ve aksiyonlar birleştiriliyor", "Özet paragrafı yazılıyor", "Dayanak kontrolü",
                       "Not kaydediliyor"]
    assert all(set(v) == {"metin", "no", "toplam"} for t, v in olaylar if t == "adim")
    # K8: 'belirsiz' aksiyonun kaynak satirinda Elif 'ben hazirlarim' diyor -> sorumlu Elif Bala
    assert "| MVP prototipini hazırlamak | Elif Bala |" in md
    assert json.loads((k / "meta.json").read_text(encoding="utf-8"))["durum"] == "tamam"


def test_notu_uret_ozetsiz_parca_adimi(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "sor", SahteLLM())
    m, k, olaylar = _toplanti(tmp_path, parca=2, ozetli=False)
    m.notu_uret()
    m.kapat()
    adimlar = [v for t, v in olaylar if t == "adim"]
    assert adimlar[0] == {"metin": "Parça 1/2 özetleniyor", "no": 1, "toplam": 2}


def test_not_yedekle(tmp_path):
    m, k, _ = _toplanti(tmp_path, not_md="# eski not\n")
    yedek = m.not_yedekle()
    assert yedek and os.path.basename(yedek).startswith("not.md.yedek-")
    assert open(yedek, encoding="utf-8").read() == "# eski not\n"
    ikinci = m.not_yedekle()                                     # ayni dakikada ikinci yedek eskisini ezmez
    assert ikinci != yedek and os.path.exists(yedek)
    m.kapat()
    m2, _, _ = _toplanti(tmp_path / "b")
    assert m2.not_yedekle() is None
    m2.kapat()


def test_yeniden_ozetle_tum_parcalar_ve_not(tmp_path, monkeypatch):
    """Gecmis -> 'Yeniden ozetle' (otomatik_not): yedek + sozluk + TUM parcalar (ozetli olanlar da) + yeni not."""
    sahte = SahteLLM()
    monkeypatch.setattr(llm, "sor", sahte)
    m, k, olaylar = _toplanti(tmp_path, parca=3, not_md="# eski not\n")
    m.sozluk.alias_ekle("komplo tit", "commit")
    yedek = m.not_yedekle()
    assert m.yeniden_ozetle() == 3
    assert sahte.gorevler.count("bolum") == 3                   # ozetli parcalar da yeniden ozetlendi
    parca_adimlari = sorted(v["no"] for t, v in olaylar if t == "adim" and v["toplam"] == 3)
    assert parca_adimlari == [1, 2, 3]
    assert sorted(v["sira"] for t, v in olaylar if t == "parca_ozetlendi") == [1, 2, 3]
    assert "commit" in m.parca_oku(2)["satirlar"][0]["text"]
    m.bitir()
    md = m.notu_uret()
    m.kapat()
    assert md.startswith("# Strateji — 2026-09-23") and (k / "not.md").read_text(encoding="utf-8") == md
    assert open(yedek, encoding="utf-8").read() == "# eski not\n"


def test_otomatik_not_varsayilan_acik(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert ayar_mod.ayar_oku()["otomatik_not"] is True
    (tmp_path / "config.json").write_text(json.dumps({"otomatik_not": False}), encoding="utf-8")
    assert ayar_mod.ayar_oku()["otomatik_not"] is False
    assert ayar_mod.AYAR_HATASI is None
    (tmp_path / "config.json").write_text("{bozuk", encoding="utf-8")
    assert ayar_mod.ayar_oku()["otomatik_not"] is True and ayar_mod.AYAR_HATASI
    assert ayar_mod.tls_dogrulama() is False


def test_llm_ayarla():
    eski = (llm.ROUTE, llm.MODEL, llm.BAGLAM_PENCERESI)
    try:
        llm.ayarla({"route": "http://llm.ornek:9000/v1", "model": "deneme-model", "context": "32768"})
        assert (llm.ROUTE, llm.MODEL, llm.BAGLAM_PENCERESI) == ("http://llm.ornek:9000/v1", "deneme-model", 32768)
        assert str(llm.client.base_url).rstrip("/") == "http://llm.ornek:9000/v1"
        llm.ayarla({"context": "gecersiz"})
        assert llm.ROUTE == "http://localhost:8000/v1" and llm.BAGLAM_PENCERESI == 16384
    finally:
        llm.ayarla({"route": eski[0], "model": eski[1], "context": eski[2]})
