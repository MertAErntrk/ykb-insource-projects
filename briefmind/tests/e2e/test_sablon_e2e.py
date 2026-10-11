"""Not sablonlari uctan uca kapilari: sablon basina bir sentetik toplanti (genel, haftalik, karar, birebir, calistay).
Gercek: Motor (ses klibi olayi -> satir -> parca kapanisi -> map (parca ozeti) -> canli ozet -> liste birlestirme -> ozet ->
dayanak kontrolu -> sahip dogrulama -> son denetim), davetten otomatik sablon secimi, not_araclari. Sahte: konusmaci
kararlari (elle; 'Ad?' dusuk guven, 'A / B' cakisma), saat ve KARSIT DAVRANISLI LLM (SablonDusmani): her sablon alanina
transkriptte olmayan bir madde uydurur, ⟨⟩ ve halusinasyon supheli satirlarindan madde / tutar cikarir, calistayda fikirleri
karar diye de yazar, karar toplantisinda kararlasmamis secenege 'secildi' der, belirsiz etiketli kisiyi is sahibi yapar.

Kapilar: sablon davetten secilir ve meta.json'da kalir; her parca isteginde sablonun prompt eki ve semasi; uydurma madde 0;
⟨⟩ / supheli degeri kesin gorunmez; belirsiz etiketten kesin is sahibi 0; calistay fikri kararlarda 0 (acik uzlasma karar
olarak kalir); dogrulanmamis secim '(?)'; birebir taslaklari kapali; bos sablon bolumu yazilmaz; her istek 16k pencereye
cevap butcesi kirpilmadan sigar. Ag yok, repo'ya dosya yazmaz. Kapi gevsetilmez."""
import json
import os
import re
import socket

import pytest

import llm
import motor as motor_mod
import not_araclari
from test_motor_faz1 import T0, SahteKonusmaci, karar, klip, saat_kur

AYSE, KEMAL, SELIN, BEN = "Ayşe Yılmaz", "Kemal Tuncer", "Selin Kara", "Deniz Kaya"
KISILER = [AYSE, KEMAL, SELIN, BEN]
PENCERE = 16384
UYDURMA = "Uydurma kalem 999 numaralı sunucuda kuruldu"


@pytest.fixture(autouse=True)
def _agsiz(monkeypatch):
    def yasak(*_a, **_k):
        raise RuntimeError("duzenek ag erisimi yok")
    monkeypatch.setattr(socket.socket, "connect", yasak)
    monkeypatch.setattr(socket, "create_connection", yasak)


@pytest.fixture(autouse=True)
def _ortam(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "token_say", llm.token_tahmin)
    monkeypatch.setattr(llm, "BAGLAM_PENCERESI", PENCERE)
    monkeypatch.setattr(motor_mod, "KOK", str(tmp_path / "toplantilar"))


# ---------------------------------------------------------------- karsit sahte LLM

_SATIR = re.compile(r"^([^:\n]{1,80}): (.*)$")
_CUMLE = re.compile(r"[^.!?]+[.!?]?")


def _cumleler(blok):
    """Blok satirlari -> [(etiket, cumle, sarmal)] (⟨⟩ icindeki cumleler de: karsit LLM onlari da kullanir)."""
    out = []
    for ham in blok.strip().split("\n"):
        m = _SATIR.match(ham)
        if not m:
            continue
        for parca in re.split(r"(⟨[^⟩]*⟩)", m.group(2)):
            sarmal = parca.startswith("⟨")
            for c in _CUMLE.findall(parca.strip("⟨⟩")):
                if c.strip(" .,"):
                    out.append((m.group(1), c.strip().rstrip(".!? "), sarmal))
    return out


class SablonDusmani:
    def __init__(self):
        self.cagrilar = []

    def gorev(self, ad):
        return [c for c in self.cagrilar if c["gorev"] == ad]

    def __call__(self, mesajlar, max_tokens, sema=None, effort="medium", temperature=0.6, dusunme=True):
        g = llm._gorev_adi(mesajlar)
        self.cagrilar.append({"gorev": g, "sistem": mesajlar[0]["content"], "sema": sema, "max_tokens": max_tokens,
                              "giris": llm._giris_token(mesajlar)})
        icerik = mesajlar[-1]["content"]
        if g == "bolum":
            v = self._bolum(icerik, sema or {})
        elif g == "liste":
            v = self._liste(icerik)
        else:
            v = {"ozet": "Toplantıda ekip işleri, sorunları ve önerileri konuştu; 750 sunucu ve 900 lisans tutarı ele "
                         "alındı.",
                 "sonraki_adim": f"{KEMAL} bütçe tablosunu hazırlayacak."}
        return json.dumps(v, ensure_ascii=False), "stop"

    def _bolum(self, icerik, sema):
        blok = icerik.split("bölümü:", 1)[-1]
        alanlar = [a for a in (sema.get("properties") or {}) if a in llm.ALAN_TURU]
        v = {"ozet": "Bölümde ekip konuştu.", "konular": ["ekip durumu"], "kararlar": [], "aksiyonlar": [],
             "acik_sorular": [], "belirsiz_terimler": []}
        for a in alanlar:
            v[a] = []
        for etiket, c, sarmal in _cumleler(blok):
            k = c.lower()
            kim = re.sub(r"\?$|\s*\(.*\)$", "", etiket).split(" / ")[0].strip()
            if "karar verdik" in k:
                v["kararlar"].append(re.sub(r"^.*karar verdik,?\s*", "", c, flags=re.I).capitalize())
            if re.search(r"\bben\b.*(hazırlarım|gönderirim|yaparım)", k):
                v["aksiyonlar"].append({"madde": re.sub(r"\bben\s+", "", c), "sorumlu": kim, "tarih": "-"})
            if "ilerleme" in alanlar and re.search(r"tamamladım|bitti", k):
                v["ilerleme"].append(c)
            if "engeller" in alanlar and re.search(r"hazır değil|bekliyoruz|engel", k):
                v["engeller"].append(c)
            if "gelecek_donem" in alanlar and re.search(r"gelecek hafta|önümüzdeki", k):
                v["gelecek_donem"].append(f"{kim} {c}")           # belirsiz etiketli kisiyi de sahip yapar
            if "secenekler" in alanlar:
                m = re.search(r"(\w+ \w+) seçeneği", c)
                if m:
                    v["secenekler"].append({"secenek": m.group(1).capitalize(), "artilar": [], "eksiler": [],
                                            "secildi": True})          # karar yokken de 'secildi' der
                if "artısı" in k and v["secenekler"]:
                    v["secenekler"][-1]["artilar"].append(c.split("artısı", 1)[1].strip(" ,:"))
                if "eksisi" in k and v["secenekler"]:
                    v["secenekler"][-1]["eksiler"].append(c.split("eksisi", 1)[1].strip(" ,:"))
            if "geri_bildirimler" in alanlar and re.search(r"geri bildirim|beğendim|netti", k):
                v["geri_bildirimler"].append(c)
            if "anlasilanlar" in alanlar and re.search(r"anlaştık", k):
                v["anlasilanlar"].append(re.sub(r"^.*anlaştık,?\s*", "", c, flags=re.I).capitalize())
            if "fikirler" in alanlar and re.search(r"bence|fikrim|önerim|olabilir", k):
                fikir = re.sub(r"^(bence|bir fikrim daha var|önerim)\s*,?\s*", "", c, flags=re.I).capitalize()
                v["fikirler"].append({"fikir": fikir, "tema": "Ödeme" if "ödeme" in k else "Kanal"})
                v["kararlar"].append(fikir + " yapılacak")          # fikri karar diye de yazar
        for a in alanlar:                                          # her alana transkriptte olmayan bir madde
            tur = llm.ALAN_TURU[a]
            v[a].append({"metin": UYDURMA, "secenek": {"secenek": UYDURMA, "artilar": [], "eksiler": [],
                                                      "secildi": True},
                         "fikir": {"fikir": UYDURMA, "tema": "Uydurma"}}[tur])
        return v

    @staticmethod
    def _liste(girdi):
        alan_adi = {"Kararlar": "kararlar", "Aksiyonlar": "aksiyonlar", "Açık sorular": "acik_sorular"}
        v, alan = {"kararlar": [], "aksiyonlar": [], "acik_sorular": []}, None
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
                if sorumlu == "belirsiz":
                    sorumlu = KEMAL                                # belirsiz sorumluyu doldurur
                v[alan].append({"madde": madde, "sorumlu": sorumlu, "tarih": tarih})
            else:
                v[alan].append(m.group(1))
        return v


# ---------------------------------------------------------------- toplantilar (davet konusu -> sablon)
# satir: (kim, metin, guven, ek) ; ek: {"supheli": True} | {"cakisma": True}; None: parca siniri

def S(kim, metin, guven=0.9, **ek):
    return (kim, metin, guven, ek)


TOPLANTILAR = {
    "genel": ("Ürün ekibi toplantısı", "", [
        S(SELIN, "Karar verdik, sürüm takvimi iki hafta kayacak."),
        S(KEMAL, "Release notlarını ben hazırlarım."),
        None,
        S(AYSE, "Test ortamı hâlâ hazır değil, bekliyoruz."),
    ]),
    "haftalik": ("Mobil ekip haftalık durum", "", [
        S(SELIN, "Rapor ekranını bu hafta tamamladım."),
        S(AYSE, "Test ortamı hâlâ hazır değil, entegrasyon bekliyoruz."),
        S(KEMAL, "Gelecek hafta bütçe tablosunu yeniden ele alırız.", 0.6),
        None,
        S(f"{AYSE} / {KEMAL}", "Lisans tarafında 750 sunucu bitti.", 0.5, cakisma=True),
        S(SELIN, "Giriş sayfası da bitti.", 0.9),
        S(KEMAL, "Önümüzdeki dönem için 900 lisans bekliyoruz.", 0.9, supheli=True),
    ]),
    "karar": ("Altyapı kurulum karar toplantısı", "", [
        S(AYSE, "Bulut kurulum seçeneği hızlı, artısı kurulumun bir haftada bitmesi."),
        S(KEMAL, "Bulut kurulumun eksisi aylık maliyetin yüksek olması."),
        S(SELIN, "Yerinde kurulum seçeneği de var, artısı verinin içeride kalması."),
        None,
        S(AYSE, "Karar verdik, bulut kurulumla devam ediyoruz."),
        S(KEMAL, "Hibrit model seçeneği de konuşulabilir.", 0.6),
    ]),
    "birebir": ("Selin / Kemal 1:1", "", [
        S(KEMAL, "Sunumun çok netti, ekip de beğendi; bu bir geri bildirim."),
        S(SELIN, "Teşekkürler, kod incelemelerinde daha hızlı dönüş istiyorum, bu da benim geri bildirimim."),
        None,
        S(KEMAL, "Anlaştık, her hafta yarım saat görüşeceğiz."),
        S(SELIN, "Eğitim planını ben hazırlarım."),
    ]),
    "calistay": ("Çalıştay: müşteri deneyimi", "", [
        S(SELIN, "Bence müşteri uygulamasına QR ile ödeme ekleyebiliriz."),
        S(KEMAL, "Bir fikrim daha var, sesli asistan ile bakiye sorgulama olabilir."),
        None,
        S(AYSE, "Önerim, taksitli ödeme seçeneği de olabilir."),
        S(AYSE, "Tamam, karar verdik, pilotu kurumsal müşterilerle yapıyoruz."),
    ]),
}


def _kos(tmp_path, monkeypatch, sablon):
    saat = saat_kur(monkeypatch)
    konu, govde, satirlar = TOPLANTILAR[sablon]
    olaylar = []
    m = motor_mod.Motor(str(tmp_path / "toplantilar" / f"2026-10-12_{sablon}"), konu, saat_tarih(), None, KISILER,
                        govde, duzelt=False, ben=BEN, sablon="otomatik", olay=lambda t, v: olaylar.append((t, v)))
    m.konusmaci = SahteKonusmaci()
    sahte = SablonDusmani()
    monkeypatch.setattr(llm, "sor", sahte)
    no = 0
    for x in satirlar:
        if x is None:
            m.parca_kapat("token", zorla=True)
            continue
        kim, metin, guven, ek = x
        no += 1
        k = klip(no, no * 4, no * 4 + 3, metin)
        k["supheli"] = bool(ek.get("supheli"))
        m.ses_olayi(k)
        m.konusmaci.kararlar = [karar(no, kim, guven, cakisma=bool(ek.get("cakisma")))]
        saat.t = T0 + no * 4 + 4
        m.kontrol()
    m.bitir()
    md = m.notu_uret()
    meta = json.load(open(os.path.join(m.klasor, "meta.json"), encoding="utf-8"))
    canli = [v["ozet"] for t, v in olaylar if t == "parca_ozetlendi" and v.get("ozet")]
    loglar = [str(v) for t, v in olaylar if t == "log"]
    m.kapat()
    return {"md": md, "m": m, "meta": meta, "llm": sahte, "canli": canli, "log": loglar}


def saat_tarih():
    import datetime as dt
    return dt.date.fromtimestamp(T0)


_onbellek = {}


def kosum(tmp_path_factory, monkeypatch, sablon):
    if sablon not in _onbellek:
        _onbellek[sablon] = _kos(tmp_path_factory.mktemp(sablon), monkeypatch, sablon)
    return _onbellek[sablon]


def _bolum(md, baslik):
    m = re.search(r"^## " + re.escape(baslik) + r"\n(.*?)(?=^## |\Z)", md, flags=re.S | re.M)
    return m.group(1) if m else None


SABLONLAR = list(TOPLANTILAR)


# ---------------------------------------------------------------- ortak kapilar

@pytest.mark.parametrize("sablon", SABLONLAR)
def test_sablon_davetten_secilir_meta_jsonda_kalir_olaylara_yazilir(tmp_path_factory, monkeypatch, sablon):
    k = kosum(tmp_path_factory, monkeypatch, sablon)
    assert k["m"].sablon == sablon and k["meta"]["sablon"] == sablon
    assert k["meta"]["sablon_neden"].startswith("otomatik")
    if sablon != "genel":
        assert any(f"Not şablonu: {llm.sablon_etiketi(sablon)} (otomatik" in x for x in k["log"])
    assert motor_mod.Motor.yukle(k["m"].klasor, sablon="genel").sablon == sablon     # yeniden ozetle: kayitli


@pytest.mark.parametrize("sablon", SABLONLAR)
def test_her_parca_istegi_sablonun_prompt_ve_semasini_tasir(tmp_path_factory, monkeypatch, sablon):
    k = kosum(tmp_path_factory, monkeypatch, sablon)
    bolum = k["llm"].gorev("bolum")
    assert len(bolum) == 2
    for c in bolum:
        assert c["sistem"] == llm.map_sistemi(sablon)
        alanlar = {a for a, _, _ in llm.sablon_alanlari(sablon)}
        assert {a for a in c["sema"]["properties"] if a in llm.ALAN_TURU} == alanlar
    if sablon == "genel":
        assert all(c["sema"] is llm.BOLUM_SEMASI for c in bolum)
        assert not any(llm.sablon_bolumu_mu(s) for s in re.findall(r"^## .*$", k["md"], re.M))


@pytest.mark.parametrize("sablon", SABLONLAR)
def test_uydurma_ve_belirsiz_deger_notta_ve_canli_panelde_kesin_gorunmez(tmp_path_factory, monkeypatch, sablon):
    k = kosum(tmp_path_factory, monkeypatch, sablon)
    md = k["md"]
    assert "Uydurma" not in md and "999" not in md
    assert "750" not in md and "900" not in md                     # ⟨⟩ / supheli satir degeri: (?) ya da atilir
    for o in k["canli"]:
        metin = json.dumps(o, ensure_ascii=False)
        assert "Uydurma" not in metin and "750 sunucu" not in metin
    assert "## Özet" in md and "## Kararlar" in md and "## Aksiyonlar" in md and "## Bir sonraki adım" in md


@pytest.mark.parametrize("sablon", SABLONLAR)
def test_istekler_16k_pencereye_cevap_butcesi_kirpilmadan_sigar(tmp_path_factory, monkeypatch, sablon):
    k = kosum(tmp_path_factory, monkeypatch, sablon)
    assert k["llm"].gorev("bolum") and k["llm"].gorev("genel")
    for c in k["llm"].cagrilar:
        assert c["giris"] + c["max_tokens"] + llm.MARJ <= PENCERE, c["gorev"]
        assert c["max_tokens"] == llm.butce(c["gorev"]) or c["gorev"] == "liste"


# ---------------------------------------------------------------- sablona ozel kapilar

def test_haftalik_bolumleri_ve_bos_bolum_yazilmaz(tmp_path_factory, monkeypatch):
    md = kosum(tmp_path_factory, monkeypatch, "haftalik")["md"]
    il, eng = _bolum(md, "İlerleme"), _bolum(md, "Engeller")
    assert "Rapor ekranını bu hafta tamamladım" in il and "Giriş sayfası da bitti" in il
    assert "Lisans" not in il                                     # cakisma satiri (⟨⟩): atildi
    assert "Test ortamı hâlâ hazır değil" in eng
    gel = _bolum(md, "Gelecek dönem")
    if gel is not None:                                           # 'Kemal?' etiketinden: kesin sahip yok
        assert "Kemal" not in gel and "(?)" in gel
    assert md.index("## İlerleme") < md.index("## Kararlar")
    assert "Kemal Tuncer bütçe tablosunu hazırlayacak" not in _bolum(md, "Bir sonraki adım")


def test_karar_secenekleri_gerekceler_ve_dogrulanmamis_secim(tmp_path_factory, monkeypatch):
    k = kosum(tmp_path_factory, monkeypatch, "karar")
    sec = _bolum(k["md"], "Seçenekler ve gerekçeler")
    assert re.search(r"^- Seçilen: Bulut kurulum", sec, re.M)                 # kararlarda karsiligi var
    assert "Artı: kurulumun bir haftada bitmesi" in sec and "Eksi: aylık maliyetin yüksek olması" in sec
    assert re.search(r"^- Seçilen \(\?\): Yerinde kurulum", sec, re.M)        # model 'secildi' dedi, karar yok
    assert "Artı: verinin içeride kalması" in sec
    assert "bulut kurulumla devam ediyoruz" in _bolum(k["md"], "Kararlar").lower()


def test_birebir_bolumleri_ve_taslaklar_kapali(tmp_path_factory, monkeypatch):
    k = kosum(tmp_path_factory, monkeypatch, "birebir")
    gb, an = _bolum(k["md"], "Geri bildirimler"), _bolum(k["md"], "Anlaşılanlar")
    assert "Sunumun çok netti" in gb and "kod incelemelerinde daha hızlı dönüş" in gb.lower()
    assert "Her hafta yarım saat görüşeceğiz" in an
    assert not_araclari.taslak_izni(k["m"].sablon, {})[0] is False
    assert not_araclari.taslak_izni(k["m"].sablon, {"birebir_taslak": True})[0] is True
    assert not_araclari.aksiyonlari_ayikla(k["md"])                         # not tablosu yine tam


def test_calistay_fikir_karar_olmaz_acik_uzlasma_karar_kalir(tmp_path_factory, monkeypatch):
    k = kosum(tmp_path_factory, monkeypatch, "calistay")
    kararlar, fikirler = _bolum(k["md"], "Kararlar"), _bolum(k["md"], "Fikirler")
    assert "Pilotu kurumsal müşterilerle yapıyoruz" in kararlar
    for fikir in ("QR ile ödeme", "sesli asistan", "taksitli ödeme"):
        assert fikir.lower() not in kararlar.lower(), fikir
        assert fikirler.lower().count(fikir.lower()) == 1, fikir
    assert "Ödeme:" in fikirler and "Kanal:" in fikirler
    assert sum("karar değil fikir olarak yazıldı" in x for x in k["log"]) >= 3
    for o in k["canli"]:                                                     # canli panel de ayni kurali uygular
        assert not any(re.search(r"qr|sesli|taksit", x.lower()) for x in o["kararlar"])
