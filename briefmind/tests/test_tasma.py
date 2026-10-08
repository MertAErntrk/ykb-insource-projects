"""Altyazi tasmasi (2026-09-30): Teams toplanti penceresinin paneli kalabalik toplantida 30'dan fazla satir
gosterince eski hizalama her okumada 0 cikiyor, panel her 0,6 sn'de yeniden yayiliyordu. Yakalayicinin panel
boyutundan bagimsiz yayimi, motorun guvenlik agi (hiz siniri, K5 sinirlari), bozuk kaydin temizlenmesi
(transkript_temizle, tasma_var_mi) ve 'Yeniden ozetle'nin temizligi cagirmasi. Ag/Teams gerekmez; adlar uydurma."""
import datetime as dt
import glob
import json
import os
import random

import llm
import motor as motor_mod
import yakalayici

KISILER = [f"Kisi{h} Soyad{h}" for h in "ABCDEFGHIJKLMN"]          # 14 uydurma katilimci
KONULAR = ["rapor ekranı", "test ortamı", "kampanya modülü", "veritabanı indeksi", "yük testi", "oryantasyon",
           "bağlantı havuzu", "sprint planı", "müşteri kaydı", "canlı geçiş"]
FIILLER = ["bakalım", "konuşalım", "erteleyelim", "hızlandıralım", "netleştirelim", "raporlayalım", "ölçelim"]
GUNLER = ["pazartesi", "salı", "çarşamba", "perşembe", "cuma", "gelecek hafta", "ay sonu"]


def _okut(y, panel):
    y._ciftler = lambda: list(panel)
    y.grup = object()
    return y.oku()


def _gercek_akis(n, tohum=7):
    """n satirlik uydurma konusma: cogu uzun (benzersiz) cumle, araya ayni kisinin tekrar eden kisa onaylari."""
    r = random.Random(tohum)
    satirlar = []
    for i in range(n):
        kim = KISILER[r.randrange(len(KISILER))]
        if i % 7 == 3:
            satirlar.append((kim, r.choice(["Evet.", "Tamam.", "Aynen."])))
        else:
            satirlar.append((kim, f"{r.choice(KONULAR).capitalize()} konusunu {r.choice(GUNLER)} {r.choice(KONULAR)} "
                                  f"ekibiyle {r.choice(FIILLER)}, {i} numaralı madde"))
    return satirlar


def _uygula(yayilan):
    """Yayilan satirlar + guncellemeler -> son transkript (konusmaci, metin) listesi."""
    sonuc = []
    for s in yayilan:
        if s.get("guncelle"):
            for i in range(len(sonuc) - 1, -1, -1):
                if sonuc[i] == (s["speaker"], s["onceki_text"]):
                    sonuc[i] = (s["speaker"], s["text"])
                    break
            else:
                raise AssertionError(f"guncellenecek satir yok: {s}")
        else:
            sonuc.append((s["speaker"], s["text"]))
    return sonuc


# ---------------------------------------------------------------- yakalayici

def test_40_satirlik_panel_14_konusmaci_her_satir_bir_kez():
    akis = _gercek_akis(100)
    y = yakalayici.Yakalayici()
    yayilan, gosterilen = [], 20
    for okuma in range(20):
        gosterilen = min(len(akis), gosterilen + 4)
        panel = akis[max(0, gosterilen - 40):gosterilen]          # panel 40 satir: eski 30'luk kuyruktan buyuk
        kim, ne = panel[-1]
        if okuma % 2 == 0 and len(ne) > 20:                       # son satir canli: yarim gorunur
            panel = panel[:-1] + [(kim, ne[:len(ne) // 2])]
        yayilan += _okut(y, panel)
        yayilan += _okut(y, akis[max(0, gosterilen - 40):gosterilen])   # ayni panel bir daha (0,6 sn sonra)
    yayilan += y.bitir()
    assert _uygula(yayilan) == akis[:gosterilen]
    assert sum(1 for s in yayilan if not s.get("guncelle")) == gosterilen
    assert len(y.gorulen) <= yakalayici.GORULEN_UST


def test_kayan_panel_eski_satirlari_yeniden_yaymaz():
    """Gercek kayittaki desen: panel ayni kalirken her okuma; eskiden 0,6 sn'de bir panelin tamami gelirdi."""
    akis = _gercek_akis(60, tohum=3)
    y = yakalayici.Yakalayici()
    toplam = []
    for _ in range(30):
        toplam += _okut(y, akis[:45])
    assert len(toplam) == 44                                      # son satir canli: bitiste gelir
    assert len(set((s["speaker"], s["text"]) for s in toplam)) <= 44
    assert [(s["speaker"], s["text"]) for s in y.bitir()] == [akis[44]]


def test_kisa_onay_tekrari_kaybolmaz_ama_kopyalanmaz():
    a = ("Kisi A", "Test ortamı perşembe günü hazır olacak gibi görünüyor")
    b = ("Kisi B", "Evet.")
    c = ("Kisi A", "Kampanya modülünü de aynı gün canlıya alalım mı")
    d = ("Kisi C", "Bence bir hafta daha bekleyelim, yük testi bitmedi")
    y = yakalayici.Yakalayici()
    yayilan = []
    panel = [a, b, c, b, d]
    for _ in range(4):
        yayilan += _okut(y, panel)
    for kayan in ([b, c, b, d], [c, b, d], [b, d]):              # panel basi kaydi: kisa satir capasiz kalir
        yayilan += _okut(y, kayan)
        yayilan += _okut(y, kayan)
    yayilan += y.bitir()
    assert _uygula(yayilan) == [a, b, c, b, d]


def test_buyuyen_satir_guncelleme_olarak_gelir():
    a1 = ("Kisi A", "Rapor ekranındaki gecikmeyi")
    b = ("Kisi B", "Veritabanı sorgusu sekiz saniye sürüyor bence")
    a2 = ("Kisi A", "Rapor ekranındaki gecikmeyi bu sprintte mutlaka çözmeliyiz")
    c = ("Kisi C", "Katılıyorum, indeks ekleyelim")
    y = yakalayici.Yakalayici()
    yayilan = []
    for _ in range(3):                                            # Kisi A durakladi: yarim hali kararli -> yayildi
        yayilan += _okut(y, [a1, b])
    assert [s["text"] for s in yayilan] == [a1[1]]
    for _ in range(3):                                            # Teams ayni satiri buyuttu
        yayilan += _okut(y, [a2, b, c])
    g = [s for s in yayilan if s.get("guncelle")]
    assert len(g) == 1 and g[0]["onceki_text"] == a1[1] and g[0]["text"] == a2[1]
    assert g[0]["ts"] == yayilan[0]["ts"]                         # ilk halinin zamani korunur
    assert _uygula(yayilan) == [a2, b]


def test_canli_buyuyen_son_satir_yayilmaz_cok_konusmaci():
    """Iki kisi ayni anda konusuyor: ikisinin de son satiri her okumada buyuyor -> hicbiri yarim yayilmaz."""
    y = yakalayici.Yakalayici()
    a = "Bütçe tablosunu cuma gününe kadar finans ekibine gönderiyorum"
    b = "Ben de test senaryolarını aynı gün paylaşırım, sorun olmaz"
    yayilan = []
    for n in range(10, max(len(a), len(b)) + 1, 6):
        yayilan += _okut(y, [("Kisi A", a[:n]), ("Kisi B", b[:n])])
    assert yayilan == []
    yayilan += _okut(y, [("Kisi A", a), ("Kisi B", b)])
    yayilan += _okut(y, [("Kisi A", a), ("Kisi B", b)])
    assert [s["text"] for s in yayilan] == [a]                    # B panelin son satiri: canli
    assert [s["text"] for s in y.bitir()] == [b]


# ---------------------------------------------------------------- motor: guncelleme, hiz siniri, K5 sinirlari

def _motor(tmp_path, kaynak="ses", ben="Zeynep Örnek", olay=None):
    return motor_mod.Motor(str(tmp_path / "t"), "Test", dt.date(2026, 9, 30), duzelt=False, kaynak=kaynak, ben=ben,
                           olay=olay)


def _transkript(m):
    return [(s["speaker"], s["kaynak"], s["text"]) for s in m.mevcut]


def test_guncelleme_acik_parcada_yerinde_uygulanir(tmp_path):
    olaylar = []
    m = _motor(tmp_path, kaynak="altyazi", olay=lambda t, v: olaylar.append((t, v)))
    m.altyazi_satiri({"ts": "10:00:01", "speaker": "Kisi A", "text": "Rapor ekranındaki gecikmeyi"})
    tok = m.mevcut_tok
    m.altyazi_satiri({"ts": "10:00:01", "speaker": "Kisi A", "text": "Rapor ekranındaki gecikmeyi bu sprintte çözelim",
                      "guncelle": True, "onceki_text": "Rapor ekranındaki gecikmeyi"})
    assert _transkript(m) == [("Kisi A", "altyazi", "Rapor ekranındaki gecikmeyi bu sprintte çözelim")]
    assert m.mevcut[0]["raw"].endswith("çözelim") and m.mevcut_tok > tok
    g = [v for t, v in olaylar if t == "satir_guncelle"]
    assert g and g[-1]["text"].endswith("çözelim") and g[-1]["id"] == m.mevcut[0]["id"]
    kayit = motor_mod.altyazi_kayitlari(str(tmp_path / "t"))
    assert [s["text"] for s in kayit] == ["Rapor ekranındaki gecikmeyi bu sprintte çözelim"]
    # parca kapandiktan sonra gelen guncelleme: eski hali kalir, bir log satiri
    m.parca_kapat("test")
    m.altyazi_satiri({"ts": "10:00:01", "speaker": "Kisi A",
                      "text": "Rapor ekranındaki gecikmeyi bu sprintte çözelim mi",
                      "guncelle": True, "onceki_text": "Rapor ekranındaki gecikmeyi bu sprintte çözelim"})
    assert m.mevcut == [] and any(t == "log" and "kapanmış parçada" in v for t, v in olaylar)
    m.kapat()


def test_k5_ile_eklenen_satirin_guncellemesi(tmp_path):
    m = _motor(tmp_path)
    m.altyazi_satiri({"ts": "10:00:01", "speaker": "Kisi A", "text": "Kampanya bütçesini yarın"})
    m.altyazi_satiri({"ts": "10:00:02", "speaker": "Kisi B", "text": "Test ortamı perşembe hazır olacak"})
    m.ses_satiri("10:00:02", None, "Test ortamı perşembe hazır olacak.", "loopback")
    assert ("Kisi A", "altyazi-cakisma", "Kampanya bütçesini yarın") in _transkript(m)
    m.altyazi_satiri({"ts": "10:00:01", "speaker": "Kisi A", "text": "Kampanya bütçesini yarın onaylarım",
                      "guncelle": True, "onceki_text": "Kampanya bütçesini yarın"})
    assert ("Kisi A", "altyazi-cakisma", "Kampanya bütçesini yarın onaylarım") in _transkript(m)
    assert len(m.mevcut) == 2 and len(m.konusmaci_izi) == 2
    m.kapat()


def test_altyazi_tasmasinda_k5_askiya_alinir_whisper_etkilenmez(tmp_path, monkeypatch):
    saat = [1000.0]
    monkeypatch.setattr(motor_mod.time, "time", lambda: saat[0])
    olaylar = []
    m = _motor(tmp_path, olay=lambda t, v: olaylar.append((t, v)))
    for i in range(160):                                     # 1 dakikada 160 altyazi satiri: tasma
        saat[0] += 0.3
        m.altyazi_satiri({"ts": "10:00:%02d" % (i % 4), "speaker": KISILER[i % 3],
                          "text": f"Eski panel satırı {i % 5} yeniden yayıldı"})
    loglar = [v for t, v in olaylar if t == "log" and "altyazı taşması" in v]
    assert len(loglar) == 1 and "askıya alındı" in loglar[0]
    s = m.ses_satiri("10:00:02", "Kisi D", "Yük testi raporunu pazartesi paylaşırım.", "loopback")
    assert s is not None and [x[1] for x in _transkript(m)] == ["ses"]      # yalniz Whisper satiri
    # tasma bitti: 61 sn sonra yeni satirlar normal islenir (askidaki satirlar sonradan eklenmez)
    saat[0] += 61
    m.altyazi_satiri({"ts": "10:02:00", "speaker": "Kisi A", "text": "Kampanya bütçesini yarın onaylarım"})
    m.altyazi_satiri({"ts": "10:02:01", "speaker": "Kisi B", "text": "Test ortamı hazır olacak"})
    m.ses_satiri("10:02:01", None, "Test ortamı hazır olacak.", "loopback")
    assert [x[1] for x in _transkript(m)] == ["ses", "altyazi-cakisma", "ses"]
    m.kapat()


def test_k5_satir_basina_ust_sinir_ve_ayni_kisiden_bir(tmp_path):
    m = _motor(tmp_path)
    for i, kim in enumerate(["Kisi A", "Kisi A", "Kisi B", "Kisi C", "Kisi D", "Kisi E"]):
        m.altyazi_satiri({"ts": "10:00:0%d" % (i % 4), "speaker": kim,
                          "text": f"{KONULAR[i]} konusunu ayrıca konuşalım"})
    m.altyazi_satiri({"ts": "10:00:02", "speaker": "Kisi F", "text": "Bağlantı havuzunu büyütüp testi yeniden koşarım"})
    s = m.ses_satiri("10:00:02", None, "Bağlantı havuzunu büyütüp testi yeniden koşarım.", "loopback")
    assert s["speaker"] == "Kisi F"
    eklenen = [x for x in _transkript(m) if x[1] == "altyazi-cakisma"]
    assert len(eklenen) == motor_mod.K5_UST == 3
    assert len({x[0] for x in eklenen}) == 3                 # ayni konusmacidan en fazla bir
    m.kapat()


def test_k5_son_2_dakikada_yazilmis_metni_eklemez(tmp_path):
    m = _motor(tmp_path)
    m.altyazi_satiri({"ts": "10:00:01", "speaker": "Kisi A", "text": "Kampanya bütçesini yarın onaylarım"})
    m.altyazi_satiri({"ts": "10:00:02", "speaker": "Kisi B", "text": "Test ortamı hazır olacak"})
    m.ses_satiri("10:00:02", None, "Test ortamı hazır olacak.", "loopback")
    assert sum(1 for x in _transkript(m) if x[1] == "altyazi-cakisma") == 1
    # ayni metin (eski panelden) yeniden geldi: transkripte ikinci kez girmez, kullanildi isaretlenir
    m.altyazi_satiri({"ts": "10:00:40", "speaker": "Kisi A", "text": "Kampanya bütçesini yarın onaylarım"})
    m.altyazi_satiri({"ts": "10:00:41", "speaker": "Kisi B", "text": "Yük testi pazartesi biter"})
    m.ses_satiri("10:00:41", None, "Yük testi pazartesi biter.", "loopback")
    assert sum(1 for x in _transkript(m) if x[1] == "altyazi-cakisma") == 1
    assert m.konusmaci_izi[2]["kullanildi"] == "altyazi"
    m.kapat()


# ---------------------------------------------------------------- bozuk kaydi kurtarma

def _tasmis_kayit(kok, tekrar=40):
    """Gercek hatanin deseni (uydurma icerikle): her satir yeniden yayilip K5 ile tekrar tekrar eklenmis,
    araya yarim (onek) halleri girmis; parcalar ve oneriler bu bozuk transkriptten."""
    k = kok / "2026-09-30_ornek"
    (k / "parcalar").mkdir(parents=True)
    temiz = [("10:%02d:%02d" % (i // 6, (i * 10) % 60), KISILER[i % 14],
              f"{KONULAR[i % 10].capitalize()} için {i} numaralı madde konuşuldu ve not alındı") for i in range(30)]
    satirlar, no = [], 0
    for i, (ts, kim, ne) in enumerate(temiz):
        yarim = ne[:len(ne) // 2]
        for j in range(tekrar):                         # kayan tasma: ayni satir her 0,6 sn'de yeniden
            no += 1
            satirlar.append({"id": no, "ts": ts, "speaker": kim, "text": ne, "raw": ne, "kaynak": "altyazi-cakisma"})
            if j == 0:
                no += 1
                satirlar.append({"id": no, "ts": ts, "speaker": kim, "text": yarim, "raw": yarim,
                                 "kaynak": "altyazi-cakisma"})
        no += 1
        satirlar.append({"id": no, "ts": ts, "speaker": "Kisi Ben", "text": f"Whisper satırı {i}",
                         "raw": f"Whisper satırı {i}", "kaynak": "ses"})
    with open(k / "altyazi.jsonl", "w", encoding="utf-8") as f:
        for s in sorted(satirlar, key=lambda s: random.Random(s["id"]).random()):   # sira da bozuk
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    for n in range(1, 13):
        (k / "parcalar" / f"parca_{n:03d}.json").write_text(json.dumps(
            {"sira": n, "satirlar": satirlar[:3], "ozet": {"ozet": "bozuk"}, "duzeltmeler": [], "token": 3000,
             "baslangic": "10:00:00", "bitis": "10:00:20", "neden": "token"}), encoding="utf-8")
    (k / "oneriler.json").write_text(json.dumps([{"id": 1, "yanlis": "x"}]), encoding="utf-8")
    (k / "meta.json").write_text(json.dumps({"baslik": "Örnek", "tarih": "2026-09-30", "durum": "tamam",
                                             "katilimcilar": [], "parca": 12, "kaynak": "ses"}), encoding="utf-8")
    return k, temiz


def test_tasma_var_mi_ve_transkript_temizle(tmp_path):
    k, temiz = _tasmis_kayit(tmp_path)
    o = motor_mod.tasma_olcumu(str(k))
    assert o["tasma"] and o["satir"] == 30 * 42 and o["tekrar_orani"] > 0.9 and motor_mod.tasma_var_mi(str(k))
    kuru = motor_mod.transkript_temizle(str(k), kuru=True)
    assert kuru["once"] == 1260 and kuru["sonra"] == 60 and kuru["onek"] == 30 and kuru["parca_once"] == 12
    assert len(os.listdir(k / "parcalar")) == 12 and not glob.glob(str(k / "*.yedek-*"))   # --kuru yazmaz
    t = motor_mod.transkript_temizle(str(k))
    assert t == kuru
    satirlar = motor_mod.altyazi_kayitlari(str(k))
    assert [s["text"] for s in satirlar if s["kaynak"] != "ses"] == [ne for _, _, ne in temiz]
    zamanlar = [s["ts"] for s in satirlar]
    assert zamanlar == sorted(zamanlar)
    parcalar = sorted(glob.glob(str(k / "parcalar" / "parca_*.json")))
    assert len(parcalar) == t["parca_sonra"] >= 1
    v = json.loads(open(parcalar[0], encoding="utf-8").read())
    assert v["ozet"] is None and v["satirlar"][0]["ts"] == "10:00:00"
    assert sum(len(json.loads(open(p, encoding="utf-8").read())["satirlar"]) for p in parcalar) == 60
    assert json.loads((k / "meta.json").read_text(encoding="utf-8"))["parca"] == t["parca_sonra"]
    assert json.loads((k / "oneriler.json").read_text(encoding="utf-8")) == []
    yedek_p = glob.glob(str(k / "parcalar.yedek-*"))
    assert len(yedek_p) == 1 and len(os.listdir(yedek_p[0])) == 12
    assert glob.glob(str(k / "altyazi.jsonl.yedek-*")) and glob.glob(str(k / "oneriler.json.yedek-*"))
    assert sum(1 for _ in open(glob.glob(str(k / "altyazi.jsonl.yedek-*"))[0], encoding="utf-8")) == 1260
    assert not motor_mod.tasma_var_mi(str(k))
    # Motor temiz kayitla acilir: parca sayisi yeni parcalardan
    m = motor_mod.Motor.yukle(str(k))
    assert m.parca_no == t["parca_sonra"] and m.oneriler == []
    m.kapat()


def test_temizlik_kisa_onaylari_ve_normal_kaydi_korur(tmp_path):
    k = tmp_path / "k"
    k.mkdir()
    satirlar = [{"id": 1, "ts": "10:00:00", "speaker": "Kisi A", "text": "Evet.", "kaynak": "ses"},
                {"id": 2, "ts": "10:00:05", "speaker": "Kisi A", "text": "Evet.", "kaynak": "altyazi-cakisma"},
                {"id": 3, "ts": "10:30:00", "speaker": "Kisi A", "text": "Evet.", "kaynak": "ses"},
                {"id": 4, "ts": "10:30:10", "speaker": "Kisi B", "text": "Raporu cuma günü gönderirim",
                 "kaynak": "ses"}]
    (k / "altyazi.jsonl").write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in satirlar) + "\nbozuk\n",
                                     encoding="utf-8")
    assert not motor_mod.tasma_var_mi(str(k))
    t = motor_mod.transkript_temizle(str(k), kuru=True)
    assert (t["once"], t["sonra"]) == (4, 3)                 # 10 dk icindeki tekrar elenir, yarim saat sonraki kalir
    assert motor_mod.transkript_temizle(str(tmp_path / "yok")) is None


def test_saklama_temizlik_yedeklerini_de_siler(tmp_path):
    k, _ = _tasmis_kayit(tmp_path, tekrar=3)
    motor_mod.transkript_temizle(str(k))
    assert glob.glob(str(k / "*.yedek-*"))
    assert motor_mod.saklama_uygula(30, kok=str(tmp_path), bugun=dt.date(2026, 12, 1)) == 1
    assert not glob.glob(str(k / "*.yedek-*")) and not (k / "altyazi.jsonl").exists()


# ---------------------------------------------------------------- isler: 'Yeniden ozetle' temizligi cagirir

def test_yeniden_ozetle_tasmayi_once_temizler(tmp_path, monkeypatch):
    import isler
    from sozluk import Sozluk
    monkeypatch.chdir(tmp_path)
    k, _ = _tasmis_kayit(tmp_path)
    ozetlenen = []

    def bolum_ozetle(metin, sira, baglam, onceki=None):
        ozetlenen.append(sira)
        return {"ozet": "Rapor ekranı ve test ortamı konuşuldu.", "konular": [], "kararlar": [], "aksiyonlar": [],
                "acik_sorular": [], "belirsiz_terimler": []}
    monkeypatch.setattr(llm, "bolum_ozetle", bolum_ozetle)
    monkeypatch.setattr(llm, "birlestir", lambda *a, **kw: "## Özet\nRapor ekranı ve test ortamı konuşuldu.\n")
    loglar = []
    is_ = isler.YenidenOzetlemeIsi(str(k), Sozluk(), otomatik_not=True, duzelt=False)
    is_.olay.connect(lambda t, v: loglar.append(v) if t == "log" else None)
    bitti = []
    is_.bitti.connect(bitti.append)
    is_.run()
    temiz = [m for m in loglar if str(m).startswith("taşma temizlendi")]
    assert temiz and "1260 → 60 satır" in temiz[0] and "12 → " in temiz[0], loglar
    n = json.loads((k / "meta.json").read_text(encoding="utf-8"))["parca"]
    assert sorted(ozetlenen) == list(range(1, n + 1))        # bozuk 12 parca degil, temiz parcalar ozetlendi
    assert bitti and (k / "not.md").exists()
