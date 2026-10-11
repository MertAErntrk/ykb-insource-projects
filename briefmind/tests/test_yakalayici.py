"""Teams altyazisi: onceki satir sonradan duzeltilince ayni satirlar yeniden yayilmamali."""
import saat as saat_mod
import yakalayici


def _oku(y, gorunen):
    y._ciftler = lambda: list(gorunen)
    y.grup = object()
    return y.oku()


def test_duzeltilen_satir_tekrar_yayilmaz():
    y = yakalayici.Yakalayici()
    a = ("Ahmet Yılmaz", "IFRS dokuz raporunda PD kolonu string geliyor")
    b = ("Ayşe Demir", "Test ortamı perşembe hazır olur diye düşünüyorum")
    c = ("Ahmet Yılmaz", "Tamam o zaman Jira kaydını sen açar mısın")
    d = ("Ayşe Demir", "Açarım.")
    yayilan = []
    yayilan += _oku(y, [a, b])
    yayilan += _oku(y, [a, b, c])
    # Teams ilk satiri duzeltti ("dokuz" -> "9"): hizalama kopar, eskiden a/b yeniden yayiliyordu
    a2 = ("Ahmet Yılmaz", "IFRS 9 raporunda PD kolonu string geliyor")
    yayilan += _oku(y, [a2, b, c, d])
    metinler = [s["text"] for s in yayilan]
    assert metinler.count(b[1]) == 1
    assert sum(1 for m in metinler if "PD kolonu" in m) == 1
    assert c[1] in metinler
    # sonraki okumada yeni satir bir kez gelir; duzeltilmis ilk satir YENI satir degil guncellemedir
    e = ("Ahmet Yılmaz", "Süper, teşekkürler herkese, görüşmek üzere")
    yayilan2 = _oku(y, [a2, b, c, d, e])
    assert [s["text"] for s in yayilan2 if not s.get("guncelle")] == [d[1]]
    g = [s for s in yayilan2 if s.get("guncelle")]
    assert len(g) == 1 and g[0]["text"] == a2[1] and g[0]["onceki_text"] == a[1]
    # ayni panel bir daha okununca hicbir sey gelmez
    assert _oku(y, [a2, b, c, d, e]) == []


# ================================================================ Faz 1: okuma olaylari (C2), heartbeat, hata duzeltmeleri

import time
import types

import pytest

from kelime import normalize

KIM_A, KIM_B = "Ahmet Yılmaz", "Ayşe Demir"


def _y(**kw):
    y = yakalayici.Yakalayici(**kw)
    y.okumalar = []
    y.okuma_fn = y.okumalar.append
    return y


def test_her_okuma_olay_uretir_bos_okuma_ve_grup_yokken_de(monkeypatch):
    y = _y()
    # 1) altyazi grubu yok
    monkeypatch.setattr(yakalayici, "_grup_bul", lambda: (None, ""))
    assert y.oku() == []
    # 2) grup bulundu, panel dolu
    a = (KIM_A, "IFRS dokuz raporunda PD kolonu string geliyor")
    b = (KIM_B, "Test ortamı perşembe hazır olur diye düşünüyorum")
    monkeypatch.setattr(yakalayici, "_grup_bul", lambda: (object(), "Alt yazılar | Toplantı | Microsoft Teams"))
    y._ciftler = lambda: [a, b]
    y.oku()
    # 3) grup var ama panel bos (bos okuma da olay)
    y._ciftler = lambda: []
    assert y.oku() == []
    # 4) UIA hatasi: grup dusurulur, yine olay
    def bozuk():
        raise RuntimeError("uia")
    y._ciftler = bozuk
    assert y.oku() == [] and y.grup is None
    assert [o["okuma_no"] for o in y.okumalar] == [1, 2, 3, 4]
    assert [o["grup"] for o in y.okumalar] == [False, True, True, False]
    assert [o["gorunen"] for o in y.okumalar] == [[], [list(a), list(b)], [], []]
    assert all(o["tur"] == "okuma" and o["bas_epoch"] <= o["bit_epoch"] for o in y.okumalar)
    assert set(y.okumalar[1]) == {"tur", "okuma_no", "bas_epoch", "bit_epoch", "grup", "gorunen"}
    assert y.son_okuma_epoch == y.okumalar[-1]["bit_epoch"]                 # heartbeat


def test_okuma_zamanlari_uia_okumasini_cevreler():
    y = _y()
    y.grup = object()

    okuma_ani = []

    def yavas():
        time.sleep(0.05)
        okuma_ani.append(saat_mod.simdi())                 # UIA okumasinin bittigi an
        return [(KIM_A, "IFRS dokuz raporunda PD kolonu string geliyor")]

    y._ciftler = yavas
    once = saat_mod.simdi()
    y.oku()
    sonra = saat_mod.simdi()
    o = y.okumalar[0]
    assert once <= o["bas_epoch"] and o["bit_epoch"] <= sonra
    # bas okuma BASLAMADAN, bit bittikten sonra alinir (sure sleep'e degil olculen ana bagli: Windows saat cozunurlugu)
    assert o["bas_epoch"] <= okuma_ani[0] <= o["bit_epoch"] and o["bit_epoch"] > o["bas_epoch"]


def test_okuma_fn_hatasi_okuyucuyu_durdurmaz_ve_oznitelik_atanabilir():
    y = yakalayici.Yakalayici(okuma_fn=lambda o: 1 / 0)
    y.grup = object()
    y._ciftler = lambda: [(KIM_A, "IFRS dokuz raporunda PD kolonu string geliyor")]
    y.oku()
    y.oku()
    assert y.son_okuma_epoch is not None
    y.okuma_fn = None
    y.oku()


def test_okuma_periyodu_gercek_aralik():
    y = _y()
    assert y.okuma_periyodu() is None
    for i in range(5):
        y._okuma_yayinla(100.0 + i * 0.7, 100.1 + i * 0.7, True, [])      # okuma suresi 0.1 + uyku 0.6
    assert abs(y.okuma_periyodu() - 0.7) < 1e-9
    for i in range(30):
        y._okuma_yayinla(200.0 + i * 1.3, 200.2 + i * 1.3, True, [])
    assert abs(y.okuma_periyodu() - 1.3) < 1e-9                             # yalniz son 10 okuma


def test_okuma_jsonl_grup_alani_ve_grupsuz_okuma(tmp_path, monkeypatch):
    y = _y()
    y.okuma_kaydi_ac(str(tmp_path))
    monkeypatch.setattr(yakalayici, "_grup_bul", lambda: (None, ""))
    y.oku()
    y.grup = object()
    y._ciftler = lambda: [(KIM_A, "IFRS dokuz raporunda PD kolonu string geliyor")]
    y.oku()
    import json
    with open(y._okuma_yolu, encoding="utf-8") as f:
        o = [json.loads(x) for x in f if x.strip()]
    assert [x["okuma_no"] for x in o] == [1, 2] and [x["grup"] for x in o] == [False, True]
    assert o[1]["gorunen"] == [[KIM_A, "IFRS dokuz raporunda PD kolonu string geliyor"]]


def test_satir_zamani_okumanin_ortasi(monkeypatch):
    """Eskiden okuma BITTIKTEN sonraki an (yavas okumada kayardi)."""
    import datetime as dt
    y = _y()
    y.grup = object()
    y._ciftler = lambda: [(KIM_A, "IFRS dokuz raporunda PD kolonu string geliyor")]
    gorulen = []
    gercek = y._isle
    y._isle = lambda gorunen, simdi: (gorulen.append(simdi), gercek(gorunen, simdi))[1]
    y.oku()
    o = y.okumalar[0]
    beklenen = dt.datetime.fromtimestamp((o["bas_epoch"] + o["bit_epoch"]) / 2).strftime("%H:%M:%S")
    assert gorulen == [beklenen]


# ---------------------------------------------------------------- sezgisel ad: >= 3 ayri satir (plan 3 'Cizelge', ~150-171)

def _sahte_uia(monkeypatch, metinler):
    """_ciftler'in gercek yolunu calistirir: panelde TextControl'ler `metinler` (sirayla)."""
    kontroller = [(types.SimpleNamespace(Name=m, ControlTypeName="TextControl"), 0) for m in metinler]
    monkeypatch.setattr(yakalayici.auto, "WalkControl", lambda *a, **k: iter(kontroller), raising=False)


def test_sezgisel_ad_uc_ayri_satirda_bilinir_okumaya_ad_oldugu_gibi_gider(monkeypatch):
    """Bulgu: okuma olayinda onaylanmamis ad '?' gidiyordu; esik dolunca eski satirlarin etiketi gercek ada donup
    konusmaci onlari yeni satir sandi (sahte 'A → B'). Okuma olayina ad hep oldugu gibi gider (hayalet kurali
    konusmaci.py'de); bilinen adlar ve canli gorunum (oku() satirlari) icin 3 ayri satir esigi surer."""
    y = _y()
    y.grup = object()
    hatlar = ["Raporu perşembeye kadar tamamlayalım mı", "Test ortamı hazır olunca haber veririm",
              "Kampanya modülünü de aynı gün alalım"]
    panel = [KIM_A, hatlar[0]]
    _sahte_uia(monkeypatch, panel)
    y.oku()
    assert y.okumalar[-1]["gorunen"] == [[KIM_A, hatlar[0]]] and KIM_A not in y.bilinen
    # ayni satirin buyumesi ve ayni panelin yeniden okunmasi yeni satir sayilmaz
    _sahte_uia(monkeypatch, [KIM_A, hatlar[0] + " diye düşünüyorum"])
    y.oku()
    satirlar = y.oku()
    assert y.okumalar[-1]["gorunen"][0][0] == KIM_A and KIM_A not in y.bilinen
    assert all(x["speaker"] == "?" for x in satirlar)                     # canli gorunum: henuz '?'
    # ikinci ayri satir
    _sahte_uia(monkeypatch, [KIM_A, hatlar[0] + " diye düşünüyorum", KIM_A, hatlar[1]])
    y.oku()
    assert [g[0] for g in y.okumalar[-1]["gorunen"]] == [KIM_A, KIM_A] and KIM_A not in y.bilinen
    # ucuncu ayri satir: ad onaylanir; okuma etiketleri hic degismez (eski satirlar yeni satir sanilmaz)
    _sahte_uia(monkeypatch, [KIM_A, hatlar[1], KIM_A, hatlar[2]])
    y.oku()
    assert [g[0] for g in y.okumalar[-1]["gorunen"]] == [KIM_A, KIM_A] and KIM_A in y.bilinen


@pytest.mark.parametrize("tohum", [7, 9, 11, 14])
def test_ad_esigi_dolunca_konusmaci_sahte_devir_uretmez(monkeypatch, tohum):
    """Katilimci listesi bos (ad hoc toplanti): A ve B sirayla konusur, her klip tek konusmacili. Etiket gecisi
    ('?' -> ad) olmadigi icin eski satirlar yeni aralik sanilmaz: hicbir klipte 'A → B' / 'A / B' yok."""
    import random
    from konusmaci import Konusmaci
    y = _y()
    y.grup = object()
    kon = Konusmaci(ben="Deniz Kaya")
    y.okuma_fn = kon.okuma_ekle
    T = 1760000000.0
    r = random.Random(tohum)
    kelime = "rapor test ortam hazir kampanya modul butce tablo pilot sube liste toplanti cuma sabah".split()
    sozler, t0 = [], 2.0
    for i in range(16):
        d = r.uniform(1.6, 3.5)
        sozler.append((KIM_A if i % 2 == 0 else KIM_B, t0, t0 + d, " ".join(r.choice(kelime) for _ in range(8))))
        t0 += d + r.uniform(0.6, 1.2)
    gecikme, t, kararlar = 2.0, 0.0, []
    for no, (kim, b, e, _) in enumerate(sozler):
        kon.klip_ekle({"tur": "klip", "klip_no": no + 1, "akis": "loopback", "bas": T + b, "bit": T + e})
    while t < t0 + 8.0:
        panel = []
        for kim, b, e, metin in sozler:
            if t >= b + gecikme:
                kel = metin.split()
                n = len(kel) if t >= e + gecikme else max(1, int(len(kel) * (t - b - gecikme) / (e - b)))
                panel += [kim, " ".join(kel[:n])]
        _sahte_uia(monkeypatch, panel[-12:])
        monkeypatch.setattr(saat_mod, "simdi", lambda t=t: T + t)
        y.oku()
        kararlar += kon.kontrol(T + t)
        t += 0.6
    kararlar += kon.bitir()
    assert len(kararlar) == len(sozler)
    assert not [(k["klip_no"], k["speaker"]) for k in kararlar
                if k["devir"] or k["cakisma"] or "→" in k["speaker"] or "/" in k["speaker"]]
    assert sum(1 for k in kararlar if k["speaker"] == sozler[k["klip_no"] - 1][0]) >= len(sozler) // 2


def test_katilimci_listesindeki_ad_hemen_gecerli(monkeypatch):
    y = _y(katilimcilar=[KIM_B])
    y.grup = object()
    _sahte_uia(monkeypatch, [KIM_B, "Test ortamı hazır olunca haber veririm"])
    y.oku()
    assert y.okumalar[-1]["gorunen"] == [[KIM_B, "Test ortamı hazır olunca haber veririm"]]


def test_altyazi_metni_ad_sanilip_cizelgeyi_kirletmez(monkeypatch):
    """Cift sayida eleman ve her cift basi buyuk harfle basliyor: yapi 'ad+metin' gibi okunur; ama bu 'ad'lar bir kez
    gorulup onaylanmadigindan bilinen adlara girmez; konusmaci.py hayalet kuraliyla '?' yazar."""
    y = _y()
    y.grup = object()
    _sahte_uia(monkeypatch, ["Evet tabii ki", "Bence yarın sabah yapalım"])
    y.oku()
    assert y.okumalar[-1]["gorunen"] == [["Evet tabii ki", "Bence yarın sabah yapalım"]] and not y.bilinen
    from konusmaci import Konusmaci
    assert Konusmaci().kimlik_turu("Evet tabii ki") == "hayalet"           # konusmaci '?' yazar


# ---------------------------------------------------------------- _isle: kisa satir zinciri (~233-239) ve kaybolanlar (~287)

def _ilk_cift(y, gorunen, n=1):
    yayilan = []
    for _ in range(n):
        yayilan += y._isle(gorunen, "10:00:00")
    return yayilan


def test_panel_basindaki_gorulmus_kisa_satir_altindaki_tekrari_yutmaz():
    """Panel basinda gorulmus 'Evet.' altindaki ayni konusmacinin YENI 'Evet.'i, eskiden durum 'ust' kaldigi icin
    isim esleşmesiyle yutuluyordu; simdi ustteki satirin kaydina gore taninir ve yayilir."""
    y = yakalayici.Yakalayici()
    uzun = (KIM_A, "Test ortamı perşembe günü hazır olacak gibi görünüyor")
    evet = (KIM_B, "Evet.")
    yeni = (KIM_A, "Kampanya modülünü de aynı gün canlıya alalım mı")
    ilk = _ilk_cift(y, [evet, uzun], n=3)
    assert [s["text"] for s in ilk] == ["Evet."]                            # panel basi 'Evet.' yayildi
    yayilan = _ilk_cift(y, [evet, evet, yeni], n=3)
    assert [s["text"] for s in yayilan].count("Evet.") == 1                 # ikinci (yeni) 'Evet.' kaybolmadi
    # ayni panel yeniden okununca ikisi de tekrar yayilmaz
    assert _ilk_cift(y, [evet, evet, yeni], n=2) == []


def test_ust_dali_kaydi_gorulmus_sayar():
    y = yakalayici.Yakalayici()
    uzun = (KIM_A, "Test ortamı perşembe günü hazır olacak gibi görünüyor")
    evet = (KIM_B, "Evet.")
    _ilk_cift(y, [evet, uzun], n=3)
    kayit = y._kisa[(KIM_B, normalize("Evet."), None)]
    kayit["son_gorulme"] = -5
    y._isle([evet, uzun], "10:00:00")
    assert kayit["son_gorulme"] == y._okuma_no                               # panel basinda 'continue' kaydi guncellemeden geciyordu


def test_kaybolan_bekleyen_satirlar_zincirle_yayilir():
    """Iki bekleyen satir ayni anda panelden kayinca (kaybolanlar) alttaki kisa satir, ustteki satirin kayit no'suna gore
    kaydolur (bitir ile ayni); eskiden 'panel basi' anahtariyla kaydedilirdi."""
    y = yakalayici.Yakalayici()
    uzun = (KIM_A, "Test ortamı perşembe günü hazır olacak gibi görünüyor")
    evet = (KIM_B, "Evet.")
    diger = (KIM_A, "Bence bir hafta daha bekleyelim, yük testi bitmedi")
    assert y._isle([uzun, evet], "10:00:00") == []                          # ikisi de bekliyor
    yayilan = y._isle([diger], "10:00:01")                                  # ikisi birden panelden kaydi
    assert [s["text"] for s in yayilan] == [uzun[1], "Evet."]
    uzun_no = y._uzun[(KIM_A, normalize(uzun[1]))]["no"]
    assert (KIM_B, normalize("Evet."), uzun_no) in y._kisa                  # zincir: ustteki satirin no'su
    assert (KIM_B, normalize("Evet."), None) not in y._kisa
