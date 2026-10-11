"""Toplantiya bagli not sablonlari: davetten otomatik secim, meta.json'da saklama / yeniden kullanma, map (parca) asamasinin
sablon semasi ve prompt'u, sablon bolumlerinin notta kurulmasi, deterministik denetimlerin (dayanak, deger, sahip, ⟨⟩,
supheli) yeni bolumlere de uygulanmasi, birebir taslak kapatma, calistay fikir / karar ayrimi, prompt boyutlari (16k).
LLM sunucusu gerekmez (llm.sor taklit edilir); adlar sentetik."""
import datetime as dt
import json
import os
import re
import sys
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")     # PyQt5 import edilmeden ONCE

import pytest

import ayar as ayar_mod
import llm
import motor as motor_mod
import not_araclari

AYSE, KEMAL, SELIN = "Ayşe Yılmaz", "Kemal Tuncer", "Selin Kara"
KISILER = [AYSE, KEMAL, SELIN]
TARIH = dt.date(2026, 10, 12)
PENCERE = 16384


@pytest.fixture(autouse=True)
def yerel_token(monkeypatch):
    monkeypatch.setattr(llm, "token_say", llm.token_tahmin)
    monkeypatch.setattr(llm, "BAGLAM_PENCERESI", PENCERE)


def sat(no, ts, kim, metin, guven=0.9, **kw):
    s = {"id": no, "ts": ts, "speaker": kim, "text": metin, "spk_guven": guven, "cakisma": False, "devir": False}
    s.update(kw)
    return s


def _cevap(v):
    return json.dumps(v, ensure_ascii=False), "stop"


class SablonLLM:
    """Gorev adina gore cevap: 'bolum' -> harita(blok) ya da sabit sozluk; 'liste' -> girdideki maddeleri aynen geri
    verir (sablon listeleri dahil); 'genel' -> sabit ozet. Her cagrinin sistem mesaji, semasi ve girdisi kaydedilir."""

    def __init__(self, bolum=None, liste=None, genel=None):
        self.bolum, self.liste, self.genel = bolum, liste, genel
        self.cagrilar = []

    def __call__(self, mesajlar, max_tokens, sema=None, effort="medium", temperature=0.6, dusunme=True):
        g = llm._gorev_adi(mesajlar)
        self.cagrilar.append({"gorev": g, "sistem": mesajlar[0]["content"], "sema": sema, "max_tokens": max_tokens,
                              "girdi": mesajlar[-1]["content"], "mesajlar": mesajlar})
        if g == "bolum":
            v = self.bolum(mesajlar[-1]["content"]) if callable(self.bolum) else self.bolum
            return _cevap(v)
        if g == "liste":
            return _cevap(self.liste(mesajlar[-1]["content"]) if callable(self.liste) else
                          (self.liste or yanki_liste(mesajlar[-1]["content"])))
        return _cevap(self.genel or {"ozet": "Toplantıda ekip işlerin durumunu, sorunları ve sonraki planı konuştu.",
                                     "sonraki_adim": "-"})

    def gorev(self, ad):
        return [c for c in self.cagrilar if c["gorev"] == ad]


def yanki_liste(girdi):
    """Liste birlestirme girdisini (blok basliklari + '- [Bn] ...') LLM cevabi gibi geri verir."""
    alan_adi = {"Kararlar": "kararlar", "Aksiyonlar": "aksiyonlar", "Açık sorular": "acik_sorular"}
    v = {"kararlar": [], "aksiyonlar": [], "acik_sorular": []}
    alan = None
    for satir in girdi.split("\n"):
        m = re.match(r"^(.+?)(?: \((\w+)\))?:$", satir.strip())
        if m and not satir.startswith("-"):
            alan = m.group(2) or alan_adi.get(m.group(1))
            if alan:
                v.setdefault(alan, [])
            continue
        m = re.match(r"^- \[B\d+\] (.+)$", satir)
        if not m or not alan:
            continue
        if alan == "aksiyonlar":
            madde, sorumlu, tarih = (m.group(1).split(" — ") + ["belirsiz", "-"])[:3]
            v[alan].append({"madde": madde, "sorumlu": sorumlu, "tarih": tarih})
        else:
            v[alan].append(m.group(1))
    return v


def _bolum(md, baslik):
    m = re.search(r"^## " + re.escape(baslik) + r"\n(.*?)(?=^## |\Z)", md, flags=re.S | re.M)
    return m.group(1) if m else None


# ================================================================ 1) davetten otomatik secim

@pytest.mark.parametrize("konu, govde, beklenen", [
    ("Haftalık durum toplantısı", "", "haftalik"),
    ("HAFTALIK SENKRON", "", "haftalik"),
    ("Weekly sync — mobil ekip", "", "haftalik"),
    ("Proje durumu", "", "haftalik"),
    ("Sprint status", "", "haftalik"),
    ("Mimari karar toplantısı", "", "karar"),
    ("Tedarikçi DEĞERLENDİRME", "", "karar"),
    ("Bütçe onayı", "", "karar"),
    ("Vendor decision", "", "karar"),
    ("1:1 Selin / Kemal", "", "birebir"),
    ("Kemal - Selin 1-1", "", "birebir"),
    ("BİREBİR görüşme", "", "birebir"),
    ("One on one", "", "birebir"),
    ("Çalıştay: müşteri deneyimi", "", "calistay"),
    ("ÇALIŞTAY", "", "calistay"),
    ("Workshop", "", "calistay"),
    ("Beyin fırtınası — yeni ürün", "", "calistay"),
    ("Q3 brainstorm", "", "calistay"),
    ("Sprint planlama", "", "genel"),
    ("Toplantı 2026-11-01", "", "genel"),                      # tarih '1-1' sayilmaz
    ("Saat 11-12 arası", "", "genel"),
    ("Sprint planlama", "Bu oturum bir workshop olarak yapılacak.", "calistay"),   # konu bos: davet metni
    ("Haftalık birebir", "", "birebir"),                       # ayni metinde ozel olan once
    ("Ürün sync", "Gündem: haftalık durum ve onay bekleyenler", "haftalik"),
    ("Karar toplantısı", "Bu bir workshop değil", "karar"),    # konu metinden once gelir
])
def test_sablon_tespit_tablosu(konu, govde, beklenen):
    sablon, neden = llm.sablon_tespit(konu, govde)
    assert sablon == beklenen, (konu, govde, neden)
    assert neden.startswith("otomatik")
    if beklenen != "genel":
        assert ("konusunda" if llm.sablon_tespit(konu)[0] == beklenen else "metninde") in neden


def test_sabit_ayar_sablonu_zorlar_otomatik_ve_bilinmeyen_davetten_secer():
    assert llm.sablon_sec("karar", "Haftalık durum") == ("karar", "ayarda sabit şablon")
    assert llm.sablon_sec("genel", "Çalıştay")[0] == "genel"
    assert llm.sablon_sec("otomatik", "Çalıştay")[0] == "calistay"
    assert llm.sablon_sec("", "Weekly")[0] == "haftalik"
    assert llm.sablon_sec("yok_boyle", "1:1")[0] == "birebir"
    assert llm.sablon_adi("otomatik") == "genel" and llm.sablon_adi("karar") == "karar"


# ================================================================ 2) toplantiya bagli: meta.json, yeniden kullanma

def test_sablon_meta_jsona_yazilir_olaylara_nedeniyle_gecer_yukle_kayitliyi_kullanir(tmp_path):
    loglar = []
    k = str(tmp_path / "2026-10-12_haftalik")
    m = motor_mod.Motor(k, "Mobil ekip haftalık durum", TARIH, katilimcilar=KISILER, sablon="otomatik",
                        olay=lambda t, v: loglar.append(v) if t == "log" else None)
    meta = json.load(open(os.path.join(k, "meta.json"), encoding="utf-8"))
    assert meta["sablon"] == "haftalik" and "davet konusunda 'haftalik durum'" in meta["sablon_neden"]
    assert any("Not şablonu: Haftalık durum (otomatik: davet konusunda" in x for x in loglar)
    m.kapat()
    # 'Yeniden ozetle' / notu acma: ayar baska bir sey dese de toplantinin kayitli sablonu
    loglar.clear()
    m2 = motor_mod.Motor.yukle(k, sablon="karar", olay=lambda t, v: loglar.append(v) if t == "log" else None)
    assert m2.sablon == "haftalik" and "haftalik" in m2.sablon_neden
    assert not any("Not şablonu" in str(x) for x in loglar)          # kayitli secim tekrar loglanmaz
    # elle degistirme (istege bagli): meta.json'a yazilir, durum korunur
    m2.meta_yaz("tamam")
    assert m2.sablon_degistir("karar") is True and m2.sablon_degistir("karar") is False
    meta = json.load(open(os.path.join(k, "meta.json"), encoding="utf-8"))
    assert meta["sablon"] == "karar" and meta["sablon_neden"] == "elle seçildi" and meta["durum"] == "tamam"
    m2.kapat()
    assert motor_mod.Motor.yukle(k, sablon="genel").sablon == "karar"


def test_kayitli_sablonu_olmayan_eski_toplanti_ayara_duser(tmp_path):
    k = tmp_path / "2026-09-01_eski"
    k.mkdir()
    (k / "meta.json").write_text(json.dumps({"baslik": "Çalıştay: ürün", "tarih": "2026-09-01", "durum": "tamam",
                                             "parca": 0}), encoding="utf-8")
    assert motor_mod.Motor.yukle(str(k), sablon="birebir").sablon == "birebir"        # sabit ayar
    k2 = tmp_path / "2026-09-02_eski"
    k2.mkdir()
    (k2 / "meta.json").write_text(json.dumps({"baslik": "Çalıştay: ürün", "tarih": "2026-09-02", "durum": "tamam",
                                              "parca": 0}), encoding="utf-8")
    m = motor_mod.Motor.yukle(str(k2), sablon="otomatik")                               # otomatik: kayitli davet
    assert m.sablon == "calistay"
    assert json.load(open(k2 / "meta.json", encoding="utf-8"))["sablon"] == "calistay"


def test_motor_davet_metninden_secer(tmp_path):
    m = motor_mod.Motor(str(tmp_path / "t"), "Ürün oturumu", TARIH, sablon="otomatik",
                        davet={"govde": "Merhaba, bu oturumu beyin fırtınası olarak planladık.", "ekler": []})
    assert m.sablon == "calistay" and "davet metninde" in m.sablon_neden


def test_ayar_goc_eski_genel_otomatik_olur_elle_secilen_genel_kalir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ayar_mod.uyarilari_sifirla()
    (tmp_path / "config.json").write_text(json.dumps({"not_sablonu": "genel", "stt_url": "http://stt.ornek"}),
                                          encoding="utf-8")
    v = ayar_mod.ayar_oku()
    assert v["not_sablonu"] == "otomatik" and v["birebir_taslak"] is False
    assert any("not_sablonu" in u for u in ayar_mod.ayar_uyarilari_al())
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))["not_sablonu_surum"] == ayar_mod.SABLON_SURUM
    # Ayarlar'dan bilincli 'genel' (surum isaretiyle yazilir): yeniden goc edilmez
    ayar_mod.ayar_yaz({"not_sablonu": "genel", "not_sablonu_surum": ayar_mod.SABLON_SURUM})
    assert ayar_mod.ayar_oku()["not_sablonu"] == "genel"
    # anahtar hic yoksa varsayilan otomatik, dosyaya dokunulmaz
    ayar_mod.ayar_yaz({"stt_url": "x"})
    assert ayar_mod.ayar_oku()["not_sablonu"] == "otomatik"
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8")) == {"stt_url": "x"}
    ayar_mod.uyarilari_sifirla()


# ================================================================ 3) map (parca) asamasi sablonu bilir

HARITA = {
    "haftalik": {"ilerleme": ["Rapor ekranı tamamlandı"], "engeller": ["Test ortamı hazır değil"],
                 "gelecek_donem": ["Ödeme modülü geliştirilecek"]},
    "karar": {"secenekler": [{"secenek": "Bulut kurulumu", "artilar": ["Kurulum hızlı"], "eksiler": ["Maliyet yüksek"],
                              "secildi": True}]},
    "birebir": {"geri_bildirimler": ["Sunum anlatımı net bulundu"], "anlasilanlar": ["Haftalık kısa görüşme yapılacak"]},
    "calistay": {"fikirler": [{"fikir": "QR ile ödeme", "tema": "Ödeme"}]},
}


def _bos_ozet(**ek):
    v = {"ozet": "Bölümde konular konuşuldu.", "konular": ["durum"], "kararlar": [], "aksiyonlar": [],
         "acik_sorular": [], "belirsiz_terimler": []}
    v.update(ek)
    return v


@pytest.mark.parametrize("sablon", ["haftalik", "karar", "birebir", "calistay"])
def test_map_semasi_ve_promptu_sablona_gore(monkeypatch, sablon):
    sahte = SablonLLM(bolum=_bos_ozet(**HARITA[sablon]))
    monkeypatch.setattr(llm, "sor", sahte)
    o = llm.bolum_ozetle("Selin Kara: Rapor ekranını tamamladım.", 1, "Toplantı: x", sablon=sablon)
    c = sahte.gorev("bolum")[0]
    alanlar = [a for a, _, _ in llm.SABLON_ALANLARI[sablon]]
    assert c["sistem"].startswith(llm.MAP_SISTEM) and c["sistem"] != llm.MAP_SISTEM
    assert all(f"- {a}:" in c["sistem"] for a in alanlar)
    assert all(a in c["sema"]["properties"] and a in c["sema"]["required"] for a in alanlar)
    assert o["sablon"] == sablon
    for a in alanlar:
        assert o[a], a
    diger = [a for s, v in llm.SABLON_ALANLARI.items() if s != sablon for a, _, _ in v]
    assert not any(a in c["sema"]["properties"] for a in diger)


def test_map_genel_eskisiyle_ayni_ve_eksik_ek_alan_cevabi_dusurmez(monkeypatch):
    sahte = SablonLLM(bolum=_bos_ozet())
    monkeypatch.setattr(llm, "sor", sahte)
    o = llm.bolum_ozetle("Ali: tamam.", 1, "Toplantı: x")
    assert sahte.cagrilar[0]["sistem"] == llm.MAP_SISTEM and sahte.cagrilar[0]["sema"] is llm.BOLUM_SEMASI
    assert "sablon" not in o and not any(a in o for a in llm.ALAN_TURU)
    # sablonlu istek; model ek alani yazmadi: ozet reddedilmez, alan bos liste olur (tekrar cagrisi yok)
    sahte = SablonLLM(bolum=_bos_ozet())
    monkeypatch.setattr(llm, "sor", sahte)
    o = llm.bolum_ozetle("Ali: tamam.", 1, "Toplantı: x", sablon="calistay")
    assert o["fikirler"] == [] and len(sahte.cagrilar) == 1


def test_calistay_map_promptu_fikirleri_karardan_ayirir():
    s = llm.map_sistemi("calistay")
    assert "'kararlar'a ya da 'acik_sorular'a YAZMA" in s and "'karar verdik'" in s and "'böyle yapıyoruz'" in s
    assert "(?) yazma" in s and "⟨⟩ içinden madde" in s


def test_sablon_alanlari_modelin_isaretini_atar_duz_metni_nesneye_cevirir():
    assert llm.sablon_alani_normalize(["Rapor bitti (?)", "-", ""], "metin") == ["Rapor bitti"]
    assert llm.sablon_alani_normalize(["Bulut | artı: hızlı; ucuz | eksi: risk | seçildi: evet"], "secenek") == [
        {"secenek": "Bulut", "artilar": ["hızlı", "ucuz"], "eksiler": ["risk"], "secildi": True}]
    assert llm.sablon_alani_normalize(["QR ile ödeme (tema: Ödeme)", {"fikir": "Sesli asistan", "tema": ""}],
                                      "fikir") == [{"fikir": "QR ile ödeme", "tema": "Ödeme"},
                                                   {"fikir": "Sesli asistan", "tema": "Diğer"}]


def test_motor_parca_ozetine_sablonu_verir_degisince_yeniden_ozetler(tmp_path, monkeypatch):
    gorulen = []

    def bolum_ozetle(blok, sira, baglam, onceki=None, **k):
        gorulen.append(k.get("sablon", "genel"))
        return llm.normalize_ozet(_bos_ozet(**({"ilerleme": ["Rapor ekranı tamamlandı"]} if k.get("sablon") else {})))
    monkeypatch.setattr(llm, "bolum_ozetle", bolum_ozetle)
    monkeypatch.setattr(llm, "sor", SablonLLM(bolum=_bos_ozet()))
    m = motor_mod.Motor(str(tmp_path / "t"), "Ekip toplantısı", TARIH, katilimcilar=KISILER, duzelt=False)
    s = sat(1, "10:00:00", SELIN, "Rapor ekranını bu hafta tamamladım, test ortamı hazır değil.")
    m.parca_no = 1
    m.parca_yaz({"sira": 1, "neden": "token", "token": 100, "baslangic": "10:00:00", "bitis": "10:00:00",
                 "satirlar": [s], "duzeltmeler": [], "ozet": llm.normalize_ozet(_bos_ozet())})
    m.notu_uret()
    assert gorulen == []                                         # genel parca, genel toplanti: yeniden ozet yok
    m.sablon_degistir("haftalik")
    md = m.notu_uret()
    assert gorulen == ["haftalik"]                               # sablon degisti: map yeni sablonla
    assert m.parca_oku(1)["ozet"]["sablon"] == "haftalik"
    assert "## İlerleme" in md and "Rapor ekranı tamamlandı" in md
    m.notu_uret()
    assert gorulen == ["haftalik"]                               # artik ayni sablon
    m.kapat()


# ================================================================ 4) birlestirme ve not: her sablonun bolumleri

def test_not_markdown_sablon_bolumleri_ve_bos_bolum_yok():
    listeler = {"kararlar": ["Bulut kurulumu seçildi"], "aksiyonlar": [], "acik_sorular": [],
                "ilerleme": ["Rapor ekranı tamamlandı"], "engeller": [], "gelecek_donem": ["Ödeme modülü"],
                "secenekler": [{"secenek": "Bulut kurulumu", "artilar": ["Hızlı"], "eksiler": ["Pahalı"], "secildi": True},
                               {"secenek": "Yerinde kurulum", "artilar": [], "eksiler": [], "secildi": "belirsiz"},
                               {"secenek": "Hibrit", "artilar": [], "eksiler": [], "secildi": False}],
                "fikirler": [{"fikir": "QR ile ödeme", "tema": "Ödeme"}, {"fikir": "Sesli asistan", "tema": "Kanal"},
                             {"fikir": "Taksitli ödeme", "tema": "Ödeme"}]}
    md = llm.not_markdown({"ozet": "Özet.", "sonraki_adim": "-"}, listeler)
    assert "## İlerleme\n- Rapor ekranı tamamlandı" in md and "## Engeller" not in md
    assert md.index("## İlerleme") < md.index("## Kararlar")
    sec = _bolum(md, "Seçenekler ve gerekçeler")
    assert sec.split("\n")[:5] == ["- Seçilen: Bulut kurulumu", "  - Artı: Hızlı", "  - Eksi: Pahalı",
                                   "- Seçilen (?): Yerinde kurulum", "- Hibrit"]
    fik = _bolum(md, "Fikirler")
    assert fik.split("\n")[:5] == ["Ödeme:", "- QR ile ödeme", "- Taksitli ödeme", "Kanal:", "- Sesli asistan"]
    # genel not: sablon basligi yok
    md = llm.not_markdown({"ozet": "Özet.", "sonraki_adim": "-"}, {"kararlar": [], "aksiyonlar": [], "acik_sorular": []})
    assert not any(f"## {b}" in md for v in llm.SABLON_ALANLARI.values() for _, b, _ in v)


def test_secenek_secildi_kodda_dogrulanir():
    v = llm.secenekleri_dogrula({"kararlar": ["Bulut kurulumuyla devam edilecek"],
                                 "secenekler": [{"secenek": "Bulut kurulumu", "secildi": True},
                                                {"secenek": "Yerinde kurulum", "secildi": True},
                                                {"secenek": "Hibrit kurulum", "secildi": False}]})
    assert [x["secildi"] for x in v["secenekler"]] == [True, "belirsiz", False]


BOLUMLER = {
    "haftalik": [_bos_ozet(ilerleme=["Rapor ekranı tamamlandı"], engeller=["Test ortamı hazır değil"],
                           gelecek_donem=[], kararlar=["Sprint süresi iki hafta olacak"]),
                 _bos_ozet(ilerleme=["Rapor ekranı tamamlandı.", "Giriş sayfası bitti"], engeller=[],
                           gelecek_donem=["Ödeme modülü geliştirilecek"])],
    "karar": [_bos_ozet(kararlar=["Bulut kurulumu seçildi"],
                        secenekler=[{"secenek": "Bulut kurulumu", "artilar": ["Kurulum hızlı"], "eksiler": [],
                                     "secildi": False}]),
              _bos_ozet(secenekler=[{"secenek": "Bulut kurulumu", "artilar": [], "eksiler": ["Maliyet yüksek"],
                                     "secildi": True},
                                    {"secenek": "Yerinde kurulum", "artilar": ["Veri içeride"], "eksiler": [],
                                     "secildi": False}])],
    "birebir": [_bos_ozet(geri_bildirimler=["Sunum anlatımı net bulundu"], anlasilanlar=[],
                          kararlar=["Sprint süresi iki hafta olacak"]),
                _bos_ozet(geri_bildirimler=[], anlasilanlar=["Haftalık kısa görüşme yapılacak"],
                          kararlar=["Eğitim bütçesi bu çeyrek kullanılacak"])],
    "calistay": [_bos_ozet(fikirler=[{"fikir": "QR ile ödeme", "tema": "Ödeme"}],
                           kararlar=["Sprint süresi iki hafta olacak"]),
                 _bos_ozet(fikirler=[{"fikir": "QR ile ödeme", "tema": "Diğer"},
                                     {"fikir": "Sesli asistan", "tema": "Kanal"}])],
}


@pytest.mark.parametrize("sablon", ["haftalik", "karar", "birebir", "calistay"])
def test_birlestir_yerel_ve_llm_listesi_sablon_alanlarini_birlestirir(monkeypatch, sablon):
    sahte = SablonLLM()
    monkeypatch.setattr(llm, "sor", sahte)
    md = llm.birlestir([dict(b) for b in BOLUMLER[sablon]], "Toplantı: x", ilerleme=lambda m: None, sablon=sablon)
    liste = sahte.gorev("liste")
    assert liste and liste[0]["sistem"].startswith(llm.LISTE_SISTEM) and liste[0]["sistem"] != llm.LISTE_SISTEM
    assert all(a in liste[0]["sema"]["properties"] for a, _, _ in llm.SABLON_ALANLARI[sablon])
    if sablon == "haftalik":
        il = _bolum(md, "İlerleme")
        assert il.count("Rapor ekranı tamamlandı") == 1 and "Giriş sayfası bitti" in il
        assert "## Engeller" in md and "## Gelecek dönem" in md
    elif sablon == "karar":
        sec = _bolum(md, "Seçenekler ve gerekçeler")
        assert "- Seçilen: Bulut kurulumu" in sec and "Artı: Kurulum hızlı" in sec and "Eksi: Maliyet yüksek" in sec
        assert "- Yerinde kurulum" in sec
    elif sablon == "birebir":
        assert "Sunum anlatımı net bulundu" in _bolum(md, "Geri bildirimler")
        assert "Haftalık kısa görüşme yapılacak" in _bolum(md, "Anlaşılanlar")
    else:
        fik = _bolum(md, "Fikirler")
        assert fik.count("QR ile ödeme") == 1 and "Kanal:" in fik


def test_liste_birlestirme_sablon_listesini_dusururse_geri_eklenir_alan_yoksa_yerel(monkeypatch):
    def dusuren(girdi):
        v = yanki_liste(girdi)
        v["fikirler"] = v.get("fikirler", [])[:1]                # model bir fikri yuttu
        return v
    loglar = []
    monkeypatch.setattr(llm, "sor", SablonLLM(liste=dusuren))
    md = llm.birlestir([dict(b) for b in BOLUMLER["calistay"]], "Toplantı: x", ilerleme=loglar.append,
                       sablon="calistay")
    assert "QR ile ödeme" in md and "Sesli asistan" in md
    assert any("maddeyi düşürdü" in x for x in loglar)

    def alansiz(girdi):
        v = yanki_liste(girdi)
        v.pop("ilerleme", None)
        return v
    monkeypatch.setattr(llm, "sor", SablonLLM(liste=alansiz))
    md = llm.birlestir([dict(b) for b in BOLUMLER["haftalik"]], "Toplantı: x", ilerleme=lambda m: None,
                       sablon="haftalik")
    assert "Giriş sayfası bitti" in _bolum(md, "İlerleme")


def test_baska_sablonun_alanlari_notta_yer_almaz(monkeypatch):
    monkeypatch.setattr(llm, "sor", SablonLLM())
    md = llm.birlestir([dict(b) for b in BOLUMLER["haftalik"]], "Toplantı: x", ilerleme=lambda m: None, sablon="karar")
    assert "## İlerleme" not in md and "Rapor ekranı" not in md


def test_kademeli_birlestirme_sablon_listeleriyle_gruplanir(monkeypatch):
    """Cok maddeli calistay: ilk kademe gruplara bolunur; sablon listeleri her grupta LLM'e gider, hepsi notta kalir."""
    bolumler = [_bos_ozet(kararlar=[f"Karar {i} için onay {i * 7 + 100} verildi"],
                          fikirler=[{"fikir": f"Fikir {i}-{j} kanal {i * 10 + j} öneri", "tema": f"Tema {i % 3}"}
                                    for j in range(6)]) for i in range(14)]
    sahte = SablonLLM()
    monkeypatch.setattr(llm, "sor", sahte)
    v = llm.listeleri_birlestir(bolumler, "Toplantı: x", ilerleme=lambda m: None)
    assert len(sahte.gorev("liste")) >= 2                        # 98 madde > LISTE_GRUP_MADDE: gruplar + ust tur
    assert len(v["fikirler"]) == 84 and len(v["kararlar"]) == 14
    for c in sahte.gorev("liste"):
        assert llm._giris_token(c["mesajlar"]) + c["max_tokens"] + llm.MARJ <= PENCERE


# ================================================================ 5) deterministik denetimler yeni bolumlerde de

TRANSKRIPT = "\n".join([
    f"{SELIN}: Rapor ekranını bu hafta tamamladım, giriş sayfası da bitti.",
    f"{KEMAL}?: Bütçe onayı için 480 bin lira bekliyoruz, ben talep ederim.",
    f"{AYSE}: Test ortamı ⟨750 sunucu⟩ hâlâ hazır değil, bu bizi engelliyor.",
    f"{AYSE}: Bulut kurulumu hızlı ama aylık maliyeti yüksek, verinin içeride kalması önemli.",
    f"{SELIN}: Sunum anlatımı çok netti, teşekkürler.",
])


def test_dayanak_kontrolu_sablon_bolumlerinde_madde_denetler():
    md = ("# T\n\n## Özet\nEkip işlerin durumunu konuştu.\n\n"
          "## İlerleme\n- Rapor ekranı tamamlandı\n- Mobil uygulama mağazada yayına alındı\n\n"
          "## Engeller\n- Test ortamı 750 sunucu ile hazır değil\n- Bütçe onayı bekleniyor\n\n"
          "## Seçenekler ve gerekçeler\n- Seçilen: Bulut kurulumu\n  - Artı: Kurulum hızlı\n"
          "  - Eksi: Aylık maliyet yüksek\n- Yerinde kurulum seçeneği değerlendirildi\n  - Artı: Veri içeride kalır\n\n"
          "## Geri bildirimler\n- Kod incelemeleri gecikiyor dendi\n\n"
          "## Kararlar\n-\n\n## Aksiyonlar\n-\n\n## Açık sorular\n-\n\n## Bir sonraki adım\n-\n")
    yeni, atilan = motor_mod.dayanak_kontrolu(md, TRANSKRIPT)
    assert "Rapor ekranı tamamlandı" in yeni and "Mobil uygulama" not in yeni
    assert "750 sunucu" not in yeni                                  # deger yalniz ⟨⟩ icinde: madde atilir
    assert "- Bütçe onayı bekleniyor" in yeni
    sec = _bolum(yeni, "Seçenekler ve gerekçeler")
    assert "- Seçilen: Bulut kurulumu" in sec and "Kurulum hızlı" in sec
    assert "Yerinde kurulum" not in sec and "Veri içeride" not in sec          # ust madde atildi: alt madde de
    assert "Kod incelemeleri" not in yeni
    assert any("Mobil uygulama" in a for a in atilan) and any("Yerinde" in a for a in atilan)
    # maddesi kalmayan sablon bolumu son denetimde notta yer almaz; ortak bolumler ('-') kalir
    son, _ = motor_mod.not_son_denetim(yeni, TRANSKRIPT, KISILER)
    assert "## Geri bildirimler" not in son and "## Kararlar\n-" in son and "## İlerleme" in son


def test_sarmal_kaynakli_madde_belirsiz_isaretlenir():
    transkript = (f"{SELIN}: Bugün yalnızca rapor konuşuldu.\n"
                  f"{KEMAL}: Entegrasyon testleri ⟨tamamlandı galiba ödeme servisi de bağlandı sanırım⟩\n"
                  f"{AYSE} / {KEMAL} (aynı anda): ⟨Mobil yayın tamamlandı.⟩\n"
                  f"{KEMAL}: Raporun son hali bitti mi?")
    md = ("## Özet\nRapor konuşuldu.\n\n## İlerleme\n- Entegrasyon testleri tamamlandı\n- Mobil yayın tamamlandı\n\n"
          "## Kararlar\n-\n")
    yeni, atilan = motor_mod.dayanak_kontrolu(md, transkript)
    assert "- Entegrasyon testleri tamamlandı (?)" in yeni                  # dayanagin cogu ⟨⟩: belirsiz
    assert "Mobil yayın" not in yeni and "Mobil yayın tamamlandı" in atilan   # yalniz ⟨⟩ icinde: atilir


def test_son_denetim_sablon_bolumunde_deger_ve_sahip_kurali():
    md = ("# T\n\n## Özet\nEkip durumu konuştu.\n\n"
          "## Engeller\n- Test ortamı için 750 sunucu konuşuldu ⏱10:02:00\n\n"
          "## Gelecek dönem\n- Kemal bütçe onayını talep edecek\n- Selin raporu sunacak\n\n"
          "## Fikirler\n480 bin bütçe:\n- Bütçe onayı için 480 bin lira\n\n"
          "## Kararlar\n-\n\n## Aksiyonlar\n| # | Madde | Sorumlu | Tarih |\n|---|---|---|---|\n"
          "| 1 | Raporu sun | Selin Kara | - |\n\n## Açık sorular\n-\n\n## Bir sonraki adım\n-\n")
    yeni, olaylar = motor_mod.not_son_denetim(md, TRANSKRIPT, KISILER, supheli_metin="")
    eng = _bolum(yeni, "Engeller")
    assert "- Test ortamı için (?) sunucu konuşuldu ⏱10:02:00" in eng         # ⟨⟩ icindeki deger kesin gorunmez
    gel = _bolum(yeni, "Gelecek dönem")
    assert "Kemal" not in gel and "(?)" in gel                              # 'Kemal?' etiketinden: kesin sahip degil
    assert "- Selin raporu sunacak" in gel                                  # tablonun kesin sahibi: kalir
    fik = _bolum(yeni, "Fikirler")
    assert "480 bin lira" in fik                                            # ⟨⟩ disinda geciyor (etiket belirsiz ama deger acik)
    assert any("'gelecek dönem' bölümünde" in o for o in olaylar)
    assert any("'engeller' bölümünde kesin olmayan değer" in o for o in olaylar)


def test_son_denetim_supheli_satir_degeri_sablon_bolumunde_isaretlenir():
    md = "## Özet\nDurum konuşuldu.\n\n## İlerleme\n- Lisans için 900 kullanıcı tamamlandı\n\n## Kararlar\n-\n"
    yeni, olaylar = motor_mod.not_son_denetim(md, TRANSKRIPT, KISILER, supheli_metin="lisans 900 kullanıcı")
    assert "- Lisans için (?) kullanıcı tamamlandı" in yeni and any("yalnız ⟨⟩ içinde: 900" in o for o in olaylar)


def test_bos_tema_satiri_ve_bos_bolum_temizlenir():
    md = "## Özet\nx\n\n## Fikirler\nÖdeme:\nKanal:\n- Sesli asistan\n\n## Engeller\n\n## Kararlar\n-\n"
    yeni = llm.sablon_bolumlerini_temizle(md)
    assert "Ödeme:" not in yeni and "Kanal:\n- Sesli asistan" in yeni and "## Engeller" not in yeni
    assert yeni.endswith("## Kararlar\n-\n")


# ================================================================ 6) calistay: fikir karar degildir

CALISTAY_SATIRLAR = [
    sat(1, "10:00:00", SELIN, "Bence müşteri uygulamasına QR ile ödeme ekleyebiliriz."),
    sat(2, "10:00:20", KEMAL, "Bir fikrim daha var, sesli asistan ile bakiye sorgulama olabilir."),
    sat(3, "10:00:45", AYSE, "Tamam, karar verdik, pilotu kurumsal müşterilerle yapıyoruz."),
]
CALISTAY_HARITA = _bos_ozet(
    ozet="Ödeme ve kanal fikirleri konuşuldu, pilot kurumsal müşterilerle yapılacak.",
    kararlar=["QR ile ödeme eklenecek", "Sesli asistan ile bakiye sorgulama yapılacak",
              "Pilot kurumsal müşterilerle yapılacak"],
    fikirler=[{"fikir": "QR ile ödeme", "tema": "Ödeme"},
              {"fikir": "Sesli asistan ile bakiye sorgulama", "tema": "Kanal"}])


def test_calistay_kararlari_ayikla_uzlasma_ifadesine_bakar():
    v, olaylar = motor_mod.calistay_kararlari_ayikla(
        {"kararlar": CALISTAY_HARITA["kararlar"] + ["Mobil bildirim tasarımı yenilenecek"],
         "fikirler": [dict(f) for f in CALISTAY_HARITA["fikirler"]]},
        CALISTAY_SATIRLAR + [sat(4, "10:01:00", SELIN, "Mobil bildirim tasarımını yenilesek mi?")])
    assert v["kararlar"] == ["Pilot kurumsal müşterilerle yapılacak"]
    assert [f["fikir"] for f in v["fikirler"]] == ["QR ile ödeme", "Sesli asistan ile bakiye sorgulama",
                                                   "Mobil bildirim tasarımı yenilenecek"]
    assert len(olaylar) == 3
    assert motor_mod.acik_uzlasma_var("Tamam, böyle yapıyoruz.") and motor_mod.acik_uzlasma_var("Karar verdik.")
    assert not motor_mod.acik_uzlasma_var("Buna karar verelim mi?")
    assert not motor_mod.acik_uzlasma_var("⟨karar verdik⟩ diye duydum")          # ⟨⟩ icindeki ifade sayilmaz


def test_calistay_uzlasmali_karar_fikirlerde_tekrar_edilmez():
    v, _ = motor_mod.calistay_kararlari_ayikla(
        {"kararlar": ["Pilot kurumsal müşterilerle yapılacak"],
         "fikirler": [{"fikir": "Pilot kurumsal müşterilerle", "tema": "Pilot"}]}, CALISTAY_SATIRLAR)
    assert v["kararlar"] == ["Pilot kurumsal müşterilerle yapılacak"] and v["fikirler"] == []


def _motor(tmp_path, baslik, satir_gruplari, sablon="otomatik", ad="t"):
    m = motor_mod.Motor(str(tmp_path / ad), baslik, TARIH, katilimcilar=KISILER, duzelt=False, sablon=sablon)
    for no, satirlar in enumerate(satir_gruplari, 1):
        m.parca_no = no
        m.parca_yaz({"sira": no, "neden": "token", "token": 100, "baslangic": satirlar[0]["ts"],
                     "bitis": satirlar[-1]["ts"], "satirlar": satirlar, "duzeltmeler": [], "ozet": None})
    return m


def test_calistay_sahte_llm_fikri_karar_yazsa_da_not_yalniz_fikirlerde_gosterir(tmp_path, monkeypatch):
    sahte = SablonLLM(bolum=CALISTAY_HARITA)
    monkeypatch.setattr(llm, "sor", sahte)
    loglar = []
    m = _motor(tmp_path, "Çalıştay: müşteri deneyimi", [CALISTAY_SATIRLAR])
    m._olay = lambda t, v: loglar.append(v) if t == "log" else None
    md = m.notu_uret()
    assert m.sablon == "calistay" and sahte.gorev("bolum")[0]["sistem"] == llm.map_sistemi("calistay")
    kararlar, fikirler = _bolum(md, "Kararlar"), _bolum(md, "Fikirler")
    assert "Pilot kurumsal müşterilerle yapılacak" in kararlar
    assert "QR" not in kararlar and "Sesli asistan" not in kararlar
    assert fikirler.count("QR ile ödeme") == 1 and fikirler.count("Sesli asistan") == 1
    assert "Ödeme:" in fikirler and "Kanal:" in fikirler and "Pilot" not in fikirler
    assert sum("karar değil fikir olarak yazıldı" in str(x) for x in loglar) == 2
    # canli panel de ayni kurali uygular
    canli = m.canli_ozet(llm.normalize_ozet(CALISTAY_HARITA), CALISTAY_SATIRLAR)
    assert canli["kararlar"] == ["Pilot kurumsal müşterilerle yapılacak"]
    assert [f["fikir"] for f in canli["fikirler"]] == ["QR ile ödeme", "Sesli asistan ile bakiye sorgulama"]
    m.kapat()


# ================================================================ 7) birebir: kisiye ozel e-posta / Jira taslagi kapali

def test_taslak_izni_birebirde_varsayilan_kapali_ayarla_acilir():
    izin, neden = not_araclari.taslak_izni("birebir", {})
    assert izin is False and "Birebir" in neden and "Ayarlar" in neden
    assert not_araclari.taslak_izni("birebir", {"birebir_taslak": True}) == (True, "")
    for s in ("genel", "haftalik", "karar", "calistay", None):
        assert not_araclari.taslak_izni(s, {})[0] is True


@pytest.fixture
def qapp():
    from PyQt5 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def pencere(tmp_path, monkeypatch, qapp):
    from PyQt5 import QtWidgets
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    import app
    monkeypatch.setattr(app.terim_mod, "uygulama_klasoru", lambda: str(tmp_path))
    monkeypatch.setattr(app.Pencere, "toplantilari_yenile", lambda self, elle=False: None)
    monkeypatch.setattr(app.Pencere, "saklama_uygula", lambda self: None)
    monkeypatch.setattr(app.Pencere, "llm_tani", lambda self, test=False: None)
    monkeypatch.setattr(app.Pencere, "kesinti_devam", lambda self: None)
    monkeypatch.setattr(app.outlook, "kullanici_adi", lambda: "Deneme Kisi")
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *a, **k: None)
    p = app.Pencere()
    p.a_outlook.setChecked(False)
    yield p
    for _ in range(50):
        qapp.processEvents()
        if not p._calisan_isler():
            break
    p._kapanis_tamam = True
    p.close()
    qapp.processEvents()


def test_arayuz_birebir_taslak_dugmeleri_kapali_aciklamali_sablon_gosterilir(pencere, tmp_path, monkeypatch):
    p = pencere
    assert p.a_sablon.currentData() == "otomatik" and p.a_sablon.itemData(0) == "otomatik"
    m = motor_mod.Motor(str(tmp_path / "toplantilar" / "b"), "Selin / Kemal 1:1", TARIH, katilimcilar=KISILER,
                        sablon="otomatik")
    p.motor, p.not_md = m, "## Özet\nx\n\n## Aksiyonlar\n| # | Madde | Sorumlu | Tarih |\n|---|---|---|---|\n" \
                           "| 1 | Raporu gönder | Selin Kara | - |\n"
    p._sablon_goster()
    assert p.k_sablon.deger.text() == "Birebir (1:1)" and p.not_sablon.currentData() == "birebir"
    assert p.not_sablon.isEnabled() and "1:1" in p.not_sablon.toolTip()
    assert not p.b_kisi.isEnabled() and not p.b_jira.isEnabled() and "Birebir" in p.b_kisi.toolTip()
    acilan = []
    monkeypatch.setattr(p, "_arka", lambda *a, **k: acilan.append(a))
    p.kisiye_ozel_eposta()                                   # kisayol / programatik cagri da taslak acmaz
    assert acilan == []
    p.ayar["birebir_taslak"] = True                          # acikca acilabilir
    p._sablon_goster()
    assert p.b_kisi.isEnabled() and p.b_jira.isEnabled()
    # elle sablon degisimi (istege bagli): meta.json'a yazilir, dugmeler yeniden degerlendirilir
    p.ayar["birebir_taslak"] = False
    monkeypatch.setattr(p, "_not_uret", lambda: None)
    p.not_sablon.setCurrentIndex(p.not_sablon.findData("genel"))
    p._sablon_secildi(p.not_sablon.currentIndex())
    assert m.sablon == "genel" and p.b_kisi.isEnabled()
    assert json.load(open(os.path.join(m.klasor, "meta.json"), encoding="utf-8"))["sablon"] == "genel"
    m.kapat()


def test_not_uret_toplantinin_sablonunu_genel_ayarla_ezmez(pencere, tmp_path, monkeypatch):
    p = pencere
    import app
    m = motor_mod.Motor(str(tmp_path / "toplantilar" / "k"), "Mimari karar toplantısı", TARIH, sablon="otomatik")
    p.motor = m
    p.ayar["not_sablonu"] = "genel"
    calisan = []

    class Sahte:
        def __init__(self, motor):
            calisan.append(motor.sablon)
            self.olay = self.bitti = self.hata = self.finished = type("S", (), {"connect": lambda *a: None})()

        def start(self):
            pass

        def isRunning(self):
            return False

        def wait(self, *a):
            return True
    monkeypatch.setattr(app, "TamamlamaIsi", Sahte)
    p._not_uret()
    assert calisan == ["karar"]
    p._not_kilit = False
    m.kapat()


# ================================================================ 8) prompt boyutlari 16k pencerede

def _buyuk_baglam():
    return llm.baglam_metni("Mobil ekip haftalık durum toplantısı", TARIH, KISILER + [f"Kişi {i} Soyad" for i in range(12)],
                            "; ".join(f"Terim{i}" for i in range(60)), gundem="Gündem maddesi " * 80)


@pytest.mark.parametrize("sablon", ["genel", "haftalik", "karar", "birebir", "calistay"])
def test_map_istegi_parca_butcesiyle_pencereye_sigar(monkeypatch, sablon):
    sahte = SablonLLM(bolum=_bos_ozet(**HARITA.get(sablon, {})))
    monkeypatch.setattr(llm, "sor", sahte)
    blok = "\n".join(f"{SELIN}: " + "rapor ekranı test ortamı bütçe onayı sunum " * 6 for _ in range(200))
    blok = blok[:llm.butce("parca") * 3]                       # tam parca butcesi (token_tahmin: len // 3)
    onceki = llm.onceki_baglam_hazirla(["Rapor"], [f"{KEMAL}: " + "önceki satır " * 30] * 6, ["Bütçe ne zaman?"])
    llm.bolum_ozetle(blok, 3, _buyuk_baglam(), onceki, sablon=sablon)
    c = sahte.gorev("bolum")[0]
    giris = llm._giris_token(c["mesajlar"])
    ek = llm.token_say(llm.map_sistemi(sablon)) - llm.token_say(llm.MAP_SISTEM)
    assert ek <= 300, ek                                       # sablon eki kucuk kalir
    assert giris + llm.butce("bolum") + llm.MARJ <= PENCERE, giris  # cevap butcesi kirpilmaz
    assert c["max_tokens"] == llm.butce("bolum")


@pytest.mark.parametrize("sablon", ["haftalik", "karar", "birebir", "calistay"])
def test_liste_ve_ozet_istekleri_pencereye_sigar(monkeypatch, sablon):
    alan, _, tur = llm.SABLON_ALANLARI[sablon][0]
    ornek = {"metin": lambda i, j: f"Madde {i}-{j} rapor ekranı kayıt {i * 10 + j} tamamlandı",
             "secenek": lambda i, j: {"secenek": f"Seçenek {i}-{j} kurulum {i * 10 + j}", "artilar": ["Hızlı kurulum"],
                                      "eksiler": ["Aylık maliyet yüksek"], "secildi": j == 0},
             "fikir": lambda i, j: {"fikir": f"Fikir {i}-{j} kanal {i * 10 + j}", "tema": f"Tema {j % 3}"}}[tur]
    bolumler = [dict(_bos_ozet(kararlar=[f"Karar {i} için onay {i + 100} verildi"],
                               aksiyonlar=[{"madde": f"İş {i} kayıt {i + 300} hazırlanacak", "sorumlu": SELIN,
                                            "tarih": "-"}]),
                     **{alan: [ornek(i, j) for j in range(5)]}, aralik="10:00:00–10:10:00") for i in range(30)]
    sahte = SablonLLM()
    monkeypatch.setattr(llm, "sor", sahte)
    loglar = []
    llm.birlestir(bolumler, _buyuk_baglam(), ilerleme=loglar.append, sablon=sablon)
    assert sahte.gorev("liste") and sahte.gorev("genel")
    for c in sahte.gorev("liste") + sahte.gorev("genel"):
        assert llm._giris_token(c["mesajlar"]) + c["max_tokens"] + llm.MARJ <= PENCERE, c["gorev"]
    assert not any("sığmıyor, yerel" in x or "yerel birleştirme kullanıldı" in x for x in loglar), loglar
    ek = llm.token_say(llm.liste_sistemi(sablon)) - llm.token_say(llm.LISTE_SISTEM)
    assert ek <= 150, ek


# ================================================================ 9) bulgu regresyonlari

# --- saklama sonrasi sablon degisimi / eski toplanti: parca ozetleri ve not.md yedeksiz silinmez

def _saklanmis_toplanti(tmp_path, meta_ek=None, not_md="# Mobil ekip haftalık durum — 2026-08-01\n\n## Kararlar\n"
                        "- Bulut kurulumu seçildi\n", silindi=True, ad="2026-08-01_mobil"):
    """Saklama suresinin temizledigi (satirlar bos, ozet duruyor) eski bir toplanti: meta'da 'sablon' yok."""
    k = tmp_path / "toplantilar" / ad
    (k / "parcalar").mkdir(parents=True)
    meta = {"baslik": "Mobil ekip haftalık durum", "tarih": "2026-08-01", "durum": "tamam", "parca": 1,
            "katilimcilar": KISILER}
    if silindi:
        meta["transkript_silindi"] = "2026-09-10"
    meta.update(meta_ek or {})
    (k / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    ozet = llm.normalize_ozet(_bos_ozet(ozet="Bulut kurulumu konuşuldu.", kararlar=["Bulut kurulumu seçildi"]))
    (k / "parcalar" / "parca_001.json").write_text(json.dumps(
        {"sira": 1, "neden": "token", "token": 100, "baslangic": "10:00:00", "bitis": "10:09:00", "satirlar": [],
         "duzeltmeler": [], "ozet": ozet}, ensure_ascii=False), encoding="utf-8")
    if not_md is not None:
        (k / "not.md").write_text(not_md, encoding="utf-8")
    return k, ozet


def _yedekler(k):
    return sorted(p.name for p in k.iterdir() if p.name.startswith("not.md.yedek-"))


def test_saklanmis_eski_toplantida_notu_yeniden_uret_ozeti_ve_notu_korur(tmp_path, monkeypatch):
    sahte = SablonLLM(bolum=_bos_ozet())
    monkeypatch.setattr(llm, "sor", sahte)
    k, ozet = _saklanmis_toplanti(tmp_path)
    once = (k / "not.md").read_text(encoding="utf-8")
    loglar = []
    m = motor_mod.Motor.yukle(str(k), sablon="otomatik", olay=lambda t, v: loglar.append(v) if t == "log" else None)
    assert m.sablon == "haftalik" and m.transkript_silindi()               # Notu ac: davet konusundan
    md = m.notu_uret(yedekle=True)                                         # 'Notu yeniden üret'
    assert md == once and (k / "not.md").read_text(encoding="utf-8") == once
    assert m.parca_oku(1)["ozet"] == ozet and "Bulut kurulumu seçildi" in md
    assert sahte.cagrilar == [] and _yedekler(k) == []                     # LLM'e gidilmedi, not degismedi
    assert any("konuşma verisi saklama süresiyle silinmiş" in str(x) for x in loglar)
    # sablon da degistirilemez (yeni sablonla ozetlenecek konusma yok); meta.json degismez
    assert m.sablon_degistir("calistay") is False and m.sablon == "haftalik"
    assert json.load(open(k / "meta.json", encoding="utf-8")).get("sablon") == "haftalik"
    # Gecmis -> Yeniden ozetle de bos satirlardan ozet yazmaz
    m.yeniden_ozetle()
    assert m.parca_oku(1)["ozet"] == ozet and m.notu_uret() == once
    assert m.parca_yeniden(1)["ozet"] == ozet
    m.kapat()


def test_satirlari_bos_parca_sablon_farkinda_yeniden_ozetlenmez(tmp_path, monkeypatch):
    """transkript_silindi isareti olmasa da (or. notu hic uretilmemis yarim kayit) satirsiz parcanin ozeti korunur."""
    sahte = SablonLLM(bolum=_bos_ozet())
    monkeypatch.setattr(llm, "sor", sahte)
    k, ozet = _saklanmis_toplanti(tmp_path, not_md=None, silindi=False, ad="2026-08-02_mobil")
    m = motor_mod.Motor.yukle(str(k), sablon="otomatik")
    assert m.sablon == "haftalik"
    md = m.notu_uret()
    assert sahte.gorev("bolum") == [] and m.parca_oku(1)["ozet"] == ozet
    assert "Bulut kurulumu seçildi" in _bolum(md, "Kararlar")
    m.kapat()


def test_saklama_sonrasi_secicide_sablon_degisimi_notu_bozmaz(tmp_path, monkeypatch):
    """r1: 'genel' toplanti notu uretildi, saklama temizledi, Notu ac -> secicide 'Haftalık': not ve ozet aynen kalir."""
    sahte = SablonLLM(bolum=_bos_ozet(ozet="Bulut kurulumu konuşuldu, karar verildi.",
                                      kararlar=["Bulut kurulumu seçildi"]))
    monkeypatch.setattr(llm, "sor", sahte)
    kok = tmp_path / "toplantilar"
    m = _motor(kok, "Ekip toplantısı", [[sat(1, "10:00:00", SELIN, "Bulut kurulumu seçildi, böyle yapıyoruz."),
                                         sat(2, "10:00:30", KEMAL, "Tamam, bulut kurulumu ile devam ediyoruz.")]],
               ad="2026-08-03_ekip")
    md = m.notu_uret()
    m.kapat()
    assert m.sablon == "genel" and "Bulut kurulumu seçildi" in md
    ozet = m.parca_oku(1)["ozet"]
    assert motor_mod.saklama_tara(30, str(kok), TARIH + dt.timedelta(days=40), aktif=[])["toplanti"] == 1
    m2 = motor_mod.Motor.yukle(m.klasor, sablon="otomatik")
    assert m2.sablon_degistir("haftalik") is False
    assert m2.notu_uret(yedekle=True) == md and m2.parca_oku(1)["ozet"] == ozet
    assert _yedekler(tmp_path / "toplantilar" / "2026-08-03_ekip") == []
    m2.kapat()


def test_notu_yeniden_uret_onceki_notu_yedekler(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "sor", SablonLLM(bolum=_bos_ozet(**HARITA["haftalik"])))
    m = _motor(tmp_path, "Ekip toplantısı", [[sat(1, "10:00:00", SELIN, "Rapor ekranını tamamladım, test ortamı hazır değil.")]])
    ilk = m.notu_uret(yedekle=True)
    k = tmp_path / "t"
    assert _yedekler(k) == []                                              # ilk not: yedeklenecek bir sey yok
    assert m.notu_uret(yedekle=True) == ilk and _yedekler(k) == []         # icerik ayni: yedek yok
    assert m.sablon_degistir("haftalik") is True
    yeni = m.notu_uret(yedekle=True)
    assert yeni != ilk and len(_yedekler(k)) == 1
    assert (k / _yedekler(k)[0]).read_text(encoding="utf-8") == ilk
    m.kapat()


def test_tamamlama_isi_notu_yedekleyerek_uretir(qapp):
    import isler
    cagri = []

    class SahteMotor:
        def notu_uret(self, **k):
            cagri.append(k)
            return "# not"

        def kapat(self):
            pass
    isler.TamamlamaIsi(SahteMotor()).run()
    assert cagri == [{"yedekle": True}]


# --- not uretilirken sablon degismez

def test_not_uretimi_ortasinda_sablon_degismez(tmp_path, monkeypatch):
    """r10: liste birlestirme aninda secimden 'Çalıştay' gelir: calisan not tek sablonla biter, kesin karar Kararlar'da."""
    m = None
    sonuc = []

    def liste(girdi):
        sonuc.append(m.sablon_degistir("calistay"))
        return yanki_liste(girdi)
    harita = _bos_ozet(kararlar=["Sprint süresi iki hafta olacak"], **HARITA["haftalik"])
    monkeypatch.setattr(llm, "sor", SablonLLM(bolum=harita, liste=liste))
    m = _motor(tmp_path, "Mobil ekip haftalık durum", [
        [sat(1, "10:00:00", SELIN, "Rapor ekranını tamamladım, test ortamı hazır değil.")],
        [sat(2, "10:10:00", AYSE, "Karar verdik, sprint süresi iki hafta olacak.")]])
    md = m.notu_uret()
    assert sonuc and not any(sonuc) and m.sablon == "haftalik"
    assert "## İlerleme" in md and "## Fikirler" not in md
    assert "Sprint süresi iki hafta olacak" in _bolum(md, "Kararlar")
    assert json.load(open(os.path.join(m.klasor, "meta.json"), encoding="utf-8"))["sablon"] == "haftalik"
    assert m.sablon_degistir("calistay") is True                           # is bitince elle degisim yine mumkun
    m.kapat()


def test_arayuz_sablon_secicisi_yalniz_bos_durumda(pencere, tmp_path, monkeypatch):
    p = pencere
    m = motor_mod.Motor(str(tmp_path / "toplantilar" / "h"), "Mobil ekip haftalık durum", TARIH, sablon="otomatik")
    p.motor, p.not_md = m, "## Özet\nx\n"
    uretilen = []
    monkeypatch.setattr(p, "_not_uret", lambda: uretilen.append(1))
    p._sablon_goster()
    assert p.not_sablon.isEnabled()
    p._not_kilit = True                                                    # not uretiliyor
    try:
        p._durum_uygula()
        assert not p.not_sablon.isEnabled()
        p.not_sablon.setCurrentIndex(p.not_sablon.findData("calistay"))
        p._sablon_secildi(p.not_sablon.currentIndex())                     # programatik / gec gelen secim de reddedilir
        assert m.sablon == "haftalik" and p.not_sablon.currentData() == "haftalik" and uretilen == []
        assert "Not şablonu değiştirilmedi" in p.gunluk.toPlainText()
    finally:
        p._not_kilit = False
    p._durum_uygula()
    assert p.not_sablon.isEnabled()
    # konusma verisi silinmis toplanti: secici kapali
    meta = json.load(open(os.path.join(m.klasor, "meta.json"), encoding="utf-8"))
    meta["transkript_silindi"] = "2026-10-01"
    json.dump(meta, open(os.path.join(m.klasor, "meta.json"), "w", encoding="utf-8"))
    p._durum_uygula()
    assert not p.not_sablon.isEnabled()
    m.kapat()


# --- calistay: soru / olumsuz / kosul / baska konudaki uzlasma karar yapmaz

CALISTAY_UZLASMA_DEGIL = [
    "QR ile ödeme yapalım, anlaştık mı?",
    "Sesli asistan da olabilir, QR ile ödemeyi de bunu seçtik mi diyelim?",
    "Takvim konusunda anlaştık; QR ile ödeme fikri de var, ona sonra bakarız.",
    "QR ile ödeme konusunda anlaştık diyemeyiz, henüz fikir aşamasında.",
    "Bütçe çıkarsa QR ile ödemeyi böyle yapıyoruz.",
    "Eğer güvenlik onay verirse, QR ile ödeme için böyle yapıyoruz.",
    "Takvim konusunda anlaştık, QR ile ödeme fikri de var.",
    "QR ile ödeme konusunda anlaştık sayılmaz.",
]
CALISTAY_UZLASMA = [
    "QR ile ödeme konusunda anlaştık.",
    "QR ile ödeme yapıyoruz, anlaştık.",
    "Tamam, karar verdik, QR ile ödeme yapıyoruz.",
]


@pytest.mark.parametrize("metin", CALISTAY_UZLASMA_DEGIL)
def test_uzlasma_degil_tablosu(metin):
    assert not motor_mod.acik_uzlasma_var(metin, "QR ile ödeme yapılacak"), metin


@pytest.mark.parametrize("metin", CALISTAY_UZLASMA)
def test_uzlasma_tablosu(metin):
    assert motor_mod.acik_uzlasma_var(metin, "QR ile ödeme yapılacak"), metin


@pytest.mark.parametrize("metin, karar", [(m, False) for m in CALISTAY_UZLASMA_DEGIL[:5]] + [(CALISTAY_UZLASMA[0], True)])
def test_calistay_tam_hat_soru_olumsuz_kosul_kararda_kesin_kalmaz(tmp_path, monkeypatch, metin, karar):
    """r3: sahte LLM fikri hem kararlara hem fikirlere yazar; tam notu_uret hatti."""
    harita = _bos_ozet(ozet="Ödeme fikirleri konuşuldu.", kararlar=["QR ile ödeme yapılacak"],
                       fikirler=[{"fikir": "QR ile ödeme", "tema": "Ödeme"}])
    monkeypatch.setattr(llm, "sor", SablonLLM(bolum=harita))
    m = _motor(tmp_path, "Çalıştay: ödeme", [[sat(1, "10:00:00", SELIN, metin),
                                              sat(2, "10:00:30", KEMAL, "Müşteri deneyimi açısından önemli bir konu.")]])
    md = m.notu_uret()
    assert m.sablon == "calistay"
    assert ("QR ile ödeme" in _bolum(md, "Kararlar")) is karar, md
    if not karar:
        assert "QR ile ödeme" in _bolum(md, "Fikirler")
    m.kapat()


# --- sablon bolumleri: tema satiri ve soyad / 'K. Soyad' ile sahip

def test_son_denetim_tema_satiri_ve_soyadla_sahip_kurali():
    md = ("# T\n\n## Özet\nEkip durumu konuştu.\n\n"
          "## Gelecek dönem\n- Bütçe onayını Kemal talep edecek\n- Bütçe onayını Tuncer talep edecek\n"
          "- Bütçe onayını K. Tuncer talep edecek\n- Selin raporu sunacak\n\n"
          "## Fikirler\nKemal Tuncer önerileri:\n- Bütçe onayı için talep\nSelin Kara önerileri:\n"
          "- Rapor ekranı giriş sayfası\n\n"
          "## Kararlar\n-\n\n## Aksiyonlar\n| # | Madde | Sorumlu | Tarih |\n|---|---|---|---|\n"
          "| 1 | Raporu sun | Selin Kara | - |\n\n## Açık sorular\n-\n\n## Bir sonraki adım\n-\n")
    yeni, olaylar = motor_mod.not_son_denetim(md, TRANSKRIPT, KISILER, supheli_metin="")
    gel = _bolum(yeni, "Gelecek dönem")
    assert "Kemal" not in gel and "Tuncer" not in gel and gel.count("(?)") == 3, gel
    assert "- Selin raporu sunacak" in gel
    fik = _bolum(yeni, "Fikirler")
    assert "Kemal" not in fik and "Tuncer" not in fik, fik                 # 'Kemal Tuncer?:' etiketi: tema kesin degil
    assert "Selin Kara önerileri:" in fik                                  # kesin etiketli satirda dayanagi var
    assert any("tema satırından kişi adı çıkarıldı" in o for o in olaylar)


def test_birebir_anlasilanlarda_soyad_ve_bas_harfli_ad_kesin_sahip_olmaz():
    transkript = "\n".join([f"{SELIN}: Sunum anlatımı çok netti, teşekkürler.",
                            f"{KEMAL}?: Raporu cuma ben gönderirim."])
    md = ("# T\n\n## Özet\nGörüşme yapıldı.\n\n## Anlaşılanlar\n- Raporu Tuncer cuma gönderecek\n"
          "- Raporu cuma K. Tuncer gönderecek\n\n## Kararlar\n-\n\n## Aksiyonlar\n| # | Madde | Sorumlu | Tarih |\n"
          "|---|---|---|---|\n| 1 | Raporu gönder | Kemal Tuncer (?) | Cuma |\n\n## Açık sorular\n-\n\n"
          "## Bir sonraki adım\n-\n")
    yeni, _ = motor_mod.not_son_denetim(md, transkript, [SELIN, KEMAL], supheli_metin="")
    an = _bolum(yeni, "Anlaşılanlar")
    assert "Tuncer" not in an and an.count("(?)") == 2, an


def test_soyad_siradan_kelime_olarak_gecince_kisi_sayilmaz():
    # 'Kara' soyadi kucuk harfle siradan kelime: kisi adi sayilmaz, cumleye dokunulmaz
    assert motor_mod._gecen_kisiler("kara liste güncellenecek", [SELIN]) == []
    assert motor_mod._gecen_kisiler("Raporu Kara gönderecek", [SELIN]) == [SELIN]
    assert motor_mod._gecen_kisiler("Raporu S. Kara gönderecek", [SELIN]) == [SELIN]


# --- otomatik sablon tespiti: davet metnindeki siradan kalip ve konu onekleri

@pytest.mark.parametrize("konu, govde, beklenen", [
    ("Ürün sunumu", "Katılamamanız durumunda kaydı paylaşacağım.", "genel"),
    ("Ürün sunumu", "Herhangi bir sorun olması durumunda bana yazın.", "genel"),
    ("Ürün sunumu", "Sunumu inceleyip onayınızı rica ederim.", "genel"),
    ("Ürün sunumu", "Toplantı 1-1,5 saat sürecek.", "genel"),
    ("Ürün sunumu", "Toplantı 1-1.5 saat sürecek.", "genel"),
    ("Ürün sunumu", "Karar verilmeyecek, yalnız bilgilendirme.", "genel"),
    ("Ürün sunumu", "Proje durumu hakkında kısa bilgi.", "genel"),          # zayif ipucu davet metninde aranmaz
    ("Kararlılık eğitimi", "", "genel"),
    ("Statusbar tasarımı", "", "genel"),
    ("Durumsal liderlik eğitimi", "", "genel"),
    ("RE:1:1 Selin", "", "birebir"),
    ("FW:1:1", "", "birebir"),
    ("RE: FW: 1:1", "", "birebir"),
    ("1on1 Kemal", "", "birebir"),
    ("1-on-1 Kemal", "", "birebir"),
    ("Bire bir görüşme", "", "birebir"),
    ("one-to-one", "", "birebir"),
    ("Ürün sunumu", "Bu hafta bire bir görüşelim.", "birebir"),
    ("Haftalık karar toplantısı", "", "karar"),
    ("Haftalık onay", "", "karar"),
    ("Ürün sync", "Gündem: weekly status ve riskler", "haftalik"),
])
def test_sablon_tespit_siradan_kalip_ve_onek_tablosu(konu, govde, beklenen):
    assert llm.sablon_tespit(konu, govde)[0] == beklenen, (konu, govde, llm.sablon_tespit(konu, govde))
