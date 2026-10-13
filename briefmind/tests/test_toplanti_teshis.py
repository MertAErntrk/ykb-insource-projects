"""tools/toplanti_teshis.py ve tools/altyazi_izle.py: sentetik toplanti klasorleri (sahte adlar, 'capNNN' sozcukleri).
Kapilar: ad/sozcuk SIZMAZ; hukum mantigi uc hazir klasorde (olay benzeri bosluklar, donuk panel, saglikli); eski bicimli
klasor cokmez; --dosya ciktisi stdout ile ayni."""
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import altyazi_izle  # noqa: E402
import toplanti_teshis as tt  # noqa: E402

T0 = 1_700_000_000.0
OFSET = 3 * 3600 + 17                      # yerel saat = epoch + OFSET (sentetik)
SELIN, EMRE, DENIZ = "Selin Kara", "Emre Aksoy", "Deniz Yılmaz"
ADLAR = ("Selin", "Kara", "Emre", "Aksoy", "Deniz", "Yılmaz")


def _ts(epoch):
    sn = int(epoch + OFSET) % 86400
    return f"{sn // 3600:02d}:{sn % 3600 // 60:02d}:{sn % 60:02d}"


def _yaz_jsonl(yol, kayitlar):
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    with open(yol, "w", encoding="utf-8") as f:
        for k in kayitlar:
            f.write(json.dumps(k, ensure_ascii=False) + "\n")


def _satir(no, bas, akis="loopback", **ek):
    s = {"surum": 2, "id": no, "klip_no": no, "akis": akis, "bas": round(bas, 3), "bit": round(bas + 4, 3),
         "ts": _ts(bas), "raw": f"cap{no:03d} cap{no + 1:03d}", "text": f"cap{no:03d} cap{no + 1:03d}",
         "speaker": "?", "spk_guven": 0.0, "adaylar": [], "devir": False, "cakisma": False, "supheli": False,
         "belirsiz": [], "duzeltmeler": [], "kaynak": "ses", "durum": "karar"}
    s.update(ek)
    return s


def _okumalar(sure, kelime_fn, bosluk_her=0, bosluk_sn=2.5, donuk_sonra=None, ad=SELIN):
    """Her 0,6 sn'de bir okuma. kelime_fn(i) -> metin; bosluk_her: her N okumada bosluk_sn kadar okuma yok;
    donuk_sonra: bu saniyeden sonra gorunen satirlar sabit kalir."""
    kayit, t, i = [], 0.0, 0
    sabit = None
    while t < sure:
        i += 1
        metin = kelime_fn(i)
        if donuk_sonra is not None and t >= donuk_sonra:
            sabit = sabit or metin
            metin = sabit
        kayit.append({"okuma_no": i, "bas_epoch": round(T0 + t, 3), "bit_epoch": round(T0 + t + 0.05, 3), "grup": True,
                      "gorunen": [[ad, metin]] if metin else []})
        t += 0.6
        if bosluk_her and i % bosluk_her == 0:
            t += bosluk_sn
    return kayit


def _metin_buyuyen(i):
    return " ".join(f"cap{j:03d}" for j in range(i % 7 + 1)) + f" cap{i % 997:03d}"


def _klasor(tmp_path, ad, meta=None, satirlar=None, guncellemeler=(), cizelge=None, olaylar=None, okumalar=None):
    k = tmp_path / ad
    k.mkdir()
    (k / "meta.json").write_text(json.dumps(meta or {"tarih": "2026-10-12", "durum": "tamam", "kaynak": "ses",
                                                      "katilimcilar": [SELIN, EMRE, DENIZ], "sablon": None},
                                             ensure_ascii=False), encoding="utf-8")
    if satirlar is not None:
        _yaz_jsonl(str(k / "altyazi.jsonl"), list(satirlar) + list(guncellemeler))
    if cizelge is not None:
        (k / "cizelge.json").write_text(json.dumps(cizelge, ensure_ascii=False), encoding="utf-8")
    if olaylar is not None:
        _yaz_jsonl(str(k / "olaylar.jsonl"), olaylar)
    if okumalar is not None:
        _yaz_jsonl(str(k / "olcum" / "okumalar.jsonl"), okumalar)
    return str(k)


def _olay(msg, an, **ek):
    d = {"tur": "log", "kaynak": "konusmaci", "mesaj": msg, "an": round(T0 + an, 3)}
    d.update(ek)
    return d


def _cfg(tmp_path, ben=EMRE):
    yol = tmp_path / "config.json"
    yol.write_text(json.dumps({"ben": ben, "route": "https://llm.ornek-kurum.example.com/v1"}), encoding="utf-8")
    return str(yol)


def _hata_log(tmp_path):
    yol = tmp_path / "hata.log"
    yol.write_text("\n".join([
        f"2026-10-12 11:05:00 Traceback File C:\\Users\\kullanici01\\AppData\\x.py line 3 for {SELIN}",
        "baglanti https://llm.ornek-kurum.example.com/v1/chat/completions api_key=sk-abcdefghijklmnop1234567890",
        "gonderen kisi.adi@ornek-kurum.example.com sunucu 10.20.30.40",
    ]), encoding="utf-8")
    return str(yol)


def _rapor(klasor, tmp_path, ek=()):
    return tt.rapor_uret(klasor, cfg=json.load(open(_cfg(tmp_path), encoding="utf-8")), hata_log=_hata_log(tmp_path))


def _hukum(metin, ad):
    m = re.search(r"\[(DESTEKLER|DESTEKLEMEZ|VERİ YOK)\s*\] " + re.escape(ad), metin)
    assert m, f"hüküm satırı yok: {ad}"
    return {"DESTEKLER": "destekler", "DESTEKLEMEZ": "desteklemez", "VERİ YOK": "veri yok"}[m.group(1)]


# ---------------------------------------------------------------- senaryolar

def _olay_benzeri(tmp_path):
    """347 uzak satirin cogu kapsama_yok, 3 aralik ilk 18 sn'de, L takili, okumada duzenli boslukla (olay verisi)."""
    satirlar, guncel = [], []
    for i in range(1, 61):
        bas = 12.0 * i
        satirlar.append(_satir(i, T0 + bas))
        if i <= 3:
            guncel.append({"id": i, "guncelle": True, "speaker": "Selin Kara?" if i == 1 else "?",
                           "spk_guven": 0.5, "adaylar": [], "kapsama": True, "spk_neden": "zayif+capa_az", "durum": "karar"})
        elif i % 4 == 0:
            guncel.append({"id": i, "guncelle": True, "speaker": "?", "adaylar": [], "kapsama": True,
                           "spk_neden": "kimse", "supheli": True, "cizelge_etkin": False, "durum": "karar"})
        else:
            guncel.append({"id": i, "guncelle": True, "speaker": "?", "adaylar": [], "kapsama": False,
                           "spk_neden": "kapsama_yok", "durum": "karar"})
    satirlar.append({"id": 99, "sil": True, "neden": "halusinasyon"})
    cizelge = {"durum": {"L": 3.797, "MAD": 0.0, "R": 0.609, "kapsama": True, "araliklar": 3, "capa": 1,
                         "atilan_ornek": 40, "tasma": False, "kulaklik": None, "bekleyen": 0},
               "araliklar": [{"kim": SELIN, "ilk": T0 + 7, "son": T0 + 7, "tur": "yeni"},
                             {"kim": EMRE, "ilk": T0 + 12, "son": T0 + 19, "tur": "yeni"},
                             {"kim": EMRE, "ilk": T0 + 16, "son": T0 + 18, "tur": "eski"}]}
    olaylar = ([_olay("L örneği", 5 + i, L_ornek=2.9, capa="a", L=2.894, MAD=0.903, R=0.671) for i in range(3)]
               + [_olay("L örneği atıldı (L_maks içinde aralık başı yok)", 30 + 7 * i, klip_bas=T0 + 30 + 7 * i)
                  for i in range(40)]
               + [_olay("ses saat çapası", 100 + i, akis="loopback", fark=0.5, L=2.9, MAD=0.9) for i in range(2)])
    okumalar = _okumalar(300, _metin_buyuyen, bosluk_her=8)
    return _klasor(tmp_path, "olay", satirlar=satirlar, guncellemeler=guncel, cizelge=cizelge, olaylar=olaylar,
                   okumalar=okumalar)


def _donuk_panel(tmp_path):
    """Panel ilk dakikadan sonra sabit kalir; hoparlorde konusma suruyor; okuma boslugu yok."""
    satirlar, guncel = [], []
    for i in range(1, 41):
        satirlar.append(_satir(i, T0 + 12.0 * i))
        guncel.append({"id": i, "guncelle": True, "speaker": "?", "adaylar": [], "kapsama": True, "spk_neden": "kimse",
                       "durum": "karar"})
    cizelge = {"durum": {"L": 2.0, "MAD": 0.1, "R": 0.6, "kapsama": True, "araliklar": 2},
               "araliklar": [{"kim": SELIN, "ilk": T0 + 14, "son": T0 + 20, "tur": "yeni"},
                             {"kim": EMRE, "ilk": T0 + 26, "son": T0 + 31, "tur": "yeni"}]}
    olaylar = [_olay("L örneği", 14 + 12 * i, L_ornek=2.0 + 0.1 * i, capa="a", L=2.0 + 0.05 * i, MAD=0.1, R=0.6)
               for i in range(12)]
    okumalar = _okumalar(480, _metin_buyuyen, donuk_sonra=60)
    return _klasor(tmp_path, "donuk", satirlar=satirlar, guncellemeler=guncel, cizelge=cizelge, olaylar=olaylar,
                   okumalar=okumalar)


def _saglikli(tmp_path):
    satirlar, guncel, araliklar = [], [], []
    for i in range(1, 51):
        bas = 10 + 12.0 * i
        kim = (SELIN, DENIZ, EMRE)[i % 3]
        satirlar.append(_satir(i, T0 + bas))
        guncel.append({"id": i, "guncelle": True, "speaker": kim, "spk_guven": 0.9, "adaylar": [[kim, 0.9]],
                       "kapsama": True, "spk_neden": "baskin+capa_az", "durum": "karar"})
        araliklar.append({"kim": kim, "ilk": T0 + bas + 2.0, "son": T0 + bas + 5.0, "tur": "yeni"})
    for j in range(5):
        satirlar.append(_satir(100 + j, T0 + 30 + 20 * j, akis="mikrofon"))
        guncel.append({"id": 100 + j, "guncelle": True, "speaker": EMRE, "spk_guven": 0.95, "adaylar": [[EMRE, 0.9]],
                       "kapsama": True, "spk_neden": "baskin+capa_az", "durum": "karar"})
    cizelge = {"durum": {"L": 2.0, "MAD": 0.1, "R": 0.6, "kapsama": True, "araliklar": len(araliklar)},
               "araliklar": araliklar}
    olaylar = [_olay("L örneği", 12 + 12 * i, L_ornek=1.9 + 0.02 * (i % 6), capa="a", L=2.0, MAD=0.1, R=0.6)
               for i in range(30)]
    okumalar = _okumalar(600, _metin_buyuyen, ad=SELIN)
    return _klasor(tmp_path, "saglikli", satirlar=satirlar, guncellemeler=guncel, cizelge=cizelge, olaylar=olaylar,
                   okumalar=okumalar)


# ---------------------------------------------------------------- testler

def test_olay_benzeri_hukumler(tmp_path):
    m = _rapor(_olay_benzeri(tmp_path), tmp_path)
    assert _hukum(m, "okuma boşlukları") == "destekler"
    assert _hukum(m, "L takılı") == "destekler"
    assert _hukum(m, "tümü-Bilinmeyen çöküşü") == "destekler"
    assert _hukum(m, "gecikme > L_maks") == "veri yok"          # aralik olusmadigi icin olculemez
    assert _hukum(m, "donuk/bayat panel") == "desteklemez"       # okuma serileri degisiyor
    assert "silinen 1" in m
    assert "okuma boşluğu" in m and "kapsama_yok" in m


def test_donuk_panel_hukumleri(tmp_path):
    m = _rapor(_donuk_panel(tmp_path), tmp_path)
    assert _hukum(m, "donuk/bayat panel") == "destekler"
    assert _hukum(m, "boş okuma") == "desteklemez"
    assert _hukum(m, "okuma boşlukları") == "desteklemez"
    assert _hukum(m, "tümü-Bilinmeyen çöküşü") == "destekler"
    assert re.search(r"konuşma var: evet", m)


def test_saglikli_hicbir_hukum_desteklemez(tmp_path):
    m = _rapor(_saglikli(tmp_path), tmp_path)
    for ad in ("donuk/bayat panel", "boş okuma", "ad eşleme hatası", "gecikme > L_maks", "L takılı",
               "ben uyumsuzluğu", "okuma boşlukları", "tümü-Bilinmeyen çöküşü"):
        assert _hukum(m, ad) == "desteklemez", (ad, m)
    assert "destekleyen hüküm yok" in m
    # gercek gecikme tahmini ~2 sn
    assert re.search(r"medyan gecikme 2\.0\d? sn", m)


def _saglikli_bosluklu(tmp_path):
    """Saglikli toplanti: okuma aralarinda 2,5 sn bosluk var ama kapsama_yok yok; sonda 70 sn sessizlik (panel sabit)."""
    k = _saglikli(tmp_path)
    yol = os.path.join(k, "olcum", "okumalar.jsonl")
    _yaz_jsonl(yol, _okumalar(720, _metin_buyuyen, bosluk_her=10, donuk_sonra=650, ad=SELIN))
    return k


def test_bosluk_kapsama_yok_olmadan_desteklemez(tmp_path):
    m = _rapor(_saglikli_bosluklu(tmp_path), tmp_path)
    assert _hukum(m, "okuma boşlukları") == "desteklemez", m
    assert re.search(r"kapsama_yok 0/", m) and "okuma boşluğu" in m       # bosluk bilgi olarak yazilir
    assert "kapsama_yok baskın" not in m


def test_sessizlikte_sabit_panel_donuk_sayilmaz(tmp_path):
    m = _rapor(_saglikli_bosluklu(tmp_path), tmp_path)
    assert _hukum(m, "donuk/bayat panel") == "desteklemez", m


def test_kisa_semasiz_adres_kelimeleri_bozmaz():
    mk = tt.Maske()
    mk.adres_gizle("yeni")
    mk.adres_gizle("llm.ornek.example")
    m = mk.maskele("yeni toplantı; yeniden_bul çağrıldı; sunucu llm.ornek.example açık")
    assert "yeni toplantı" in m and "yeniden_bul" in m
    assert "<ADRES>" in m and "llm.ornek.example" not in m


def test_sizinti_yok(tmp_path):
    for yap in (_olay_benzeri, _donuk_panel, _saglikli):
        alt = tmp_path / yap.__name__.strip("_")
        alt.mkdir()
        m = _rapor(yap(alt), tmp_path)
        assert not re.search(r"cap\d{3}", m)
        for ad in ADLAR:
            assert not re.search(ad, m, flags=re.I), (ad, yap.__name__)
        assert re.search(r"\bK1\b", m)
        assert "kullanici01" not in m and "sk-abcdefgh" not in m and "ornek-kurum" not in m
        assert "10.20.30.40" not in m and "https://" not in m
        assert "C:\\Users\\<K>" in m


def test_ad_parcalari_maskelenir():
    mk = tt.Maske()
    mk.katilimci_ekle("Kaya, Deniz")
    mk.ekle("Selin Aksoy → Emre Tan")
    m = mk.maskele("Deniz Kaya konuştu; Selin'in sesi; Aksoyun payı; Emre / Tan?")
    for ad in ("Deniz", "Kaya", "Selin", "Aksoy", "Emre", "Tan"):
        assert ad not in m, m
    assert "K1" in m and "K2" in m


def test_eski_bicimli_klasor(tmp_path):
    k = tmp_path / "eski"
    k.mkdir()
    (k / "meta.json").write_text(json.dumps({"tarih": "2026-01-02"}), encoding="utf-8")
    with open(k / "altyazi.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps({"id": 1, "ts": "10:00:01", "speaker": SELIN, "text": "cap001 cap002"}) + "\n")
        f.write("bozuk satir{\n")
        f.write(json.dumps({"id": 2, "ts": "10:00:30", "speaker": "?", "text": "cap003"}) + "\n")
    m = tt.rapor_uret(str(k), cfg={}, hata_log=str(tmp_path / "yok.log"))
    assert "HİÇ ARALIK OLUŞMADI" in m
    assert "toplam 2" in m
    assert "okuma kaydı yok" in m
    assert not re.search(r"cap\d{3}", m) and "Selin" not in m
    # yalniz meta.json olan klasor de cokmez
    bos = tmp_path / "bos"
    bos.mkdir()
    (bos / "meta.json").write_text("{}", encoding="utf-8")
    assert "satır yok" in tt.rapor_uret(str(bos), cfg={}, hata_log=str(tmp_path / "yok.log"))


def test_dosya_cikti_stdout_ile_ayni(tmp_path, capsys):
    klasor = _saglikli(tmp_path)
    hedef = tmp_path / "cikti.txt"
    kod = tt.main([klasor, "--dosya", str(hedef), "--config", _cfg(tmp_path), "--hata-log", _hata_log(tmp_path)])
    cikti = capsys.readouterr().out
    assert kod == 0
    assert hedef.read_text(encoding="utf-8") == cikti
    assert "OTOMATİK HÜKÜM" in cikti


def test_olmayan_klasor(tmp_path, capsys):
    assert tt.main([str(tmp_path / "yok")]) == 2


def test_ben_uyumsuzlugu(tmp_path):
    klasor = _saglikli(tmp_path)
    m = tt.rapor_uret(klasor, cfg={"ben": "Zeynep Demir"}, hata_log=str(tmp_path / "yok.log"))
    assert _hukum(m, "ben uyumsuzluğu") == "destekler"
    m2 = tt.rapor_uret(klasor, cfg={}, hata_log=str(tmp_path / "yok.log"))
    assert _hukum(m2, "ben uyumsuzluğu") == "veri yok"


def test_yeni_okuma_alanlari(tmp_path):
    klasor = _donuk_panel(tmp_path)
    yol = os.path.join(klasor, "olcum", "okumalar.jsonl")
    kayit = [json.loads(s) for s in open(yol, encoding="utf-8")]
    for i, k in enumerate(kayit):
        k.update(degisti=i % 2 == 0, donuk=i > 200, okuma_sn=0.05, yeniden_bul=1,
                 pencere={"simge": False, "buyuk": True, "onplan": False})
    _yaz_jsonl(yol, kayit)
    m = _rapor(klasor, tmp_path)
    assert "yeni alanlar:" in m and "donuk" in m
    assert _hukum(m, "donuk/bayat panel") == "destekler"


# ---------------------------------------------------------------- altyazi_izle (Windows araci)

def test_altyazi_izle_windows_disinda_mesaj(capsys, monkeypatch):
    monkeypatch.setattr(altyazi_izle, "windows_mu", lambda: False)
    assert altyazi_izle.main(["--sure", "1"]) == 2
    cikti = capsys.readouterr()
    assert "Windows" in (cikti.out + cikti.err)


def test_altyazi_izle_satir_bicimi_ad_ve_metin_yazmaz():
    s = altyazi_izle.satir_bicimle(
        zaman="11:05:03.200", bulundu=True, kimlik="a3f2c1", rect=(10, 800, 990, 860), satir=12, ad=5, soru=1,
        kelime=87, degisti=False, ayni_sn=14.0, pencere={"simge": False, "buyuk": True, "onplan": False}, ms=212)
    assert "11:05:03.200" in s and "a3f2c1" in s and "kelime=87" in s and "ayni=14" in s
    assert "büyük" in s and "212" in s
    yok = altyazi_izle.satir_bicimle(zaman="11:05:04.000", bulundu=False)
    assert "panel=YOK" in yok
