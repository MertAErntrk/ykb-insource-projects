"""B3 + B5 + K1 kancalari (motor): halusinasyon zaman cizelgesinden ayrilir (hal_ic, CIZELGE_PUANI 0.3, supheli_neden
'ic' | 'cizelge', nota_girer / 'Bilinmeyen:' etiketi, silme yalniz hal_ic, gec yukseltmede supheli kalkar) ve atama
basarisizligi uyarisi (_atama_denetle: son 120 sn'de >= 10 kapsamali loopback kararinin >= %60'i '?': olay + log +
yeniden_bul_fn + L_yeniden_olc, %30 altinda kalkar); kapsama mesaji durum()['neden']'e gore; Motor.son_loopback_ses /
olay_kaydet / yeniden_bul_fn; okuma alanlari konusmaciya aynen gider. Ses cihazi, ag, LLM gerekmez; adlar sentetik."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")     # PyQt5 import edilmeden ONCE

import pytest

import motor as motor_mod
import stt
from metin import kesin_ad_mi
from test_motor_faz1 import A, B, BEN, T0, karar, klip, motor, saat_kur
from test_faz0 import ortam, pencere, qapp  # noqa: F401  (UI testi icin fixture'lar)


@pytest.fixture
def saat(monkeypatch):
    return saat_kur(monkeypatch)


def _satir(m, no, hal, **ek):
    """Klip olayi (ses.py icsel hal_puani ile) -> satir."""
    ev = klip(no, no * 3, no * 3 + 2, "Kampanya görsellerini de ekledim.")
    ev["hal_puani"] = hal
    ev.update(ek)
    return m.ses_olayi(ev)


def _karar(m, no, **ek):
    m.konusmaci.kararlar = [karar(no, ek.pop("speaker", "?"), ek.pop("guven", 0.0), **ek)]
    m.kontrol()


YOK = dict(adaylar=[], neden="kimse")                       # altyazi canli ama aralikta kimse yok
VAR = dict(adaylar=[[A, 0.8]], neden="baskin")


def test_cizelge_puani_tek_sabit():
    assert motor_mod.CIZELGE_PUANI == stt.CIZELGE_PUANI == 0.3
    assert stt.HAL_ESIKLER["hal_cizelge_taban"] == 0.3


# ---------------------------------------------------------------- B3: kural tablosu

@pytest.mark.parametrize("ic,canli,ek,supheli", [
    (0.0, True, YOK, False),                  # icsel puan yok: zaman cizelgesi tek basina supheli yapmaz
    (0.29, True, YOK, False),                 # taban (0.3) altinda
    (0.3, True, YOK, True),                   # taban + 0.3 >= hal_supheli (0.5)
    (0.45, True, YOK, True),
    (0.3, False, YOK, False),                 # cizelge canli degil (panel bayat / klipten sonra degismemis)
    (0.45, True, VAR, False),                 # aralikta etkinlik var
    (0.45, True, dict(adaylar=[], neden="kapsama_yok", kapsama=False), False),     # cizelge_etkin None
    (0.45, True, dict(adaylar=[], neden="panel_donuk", kapsama=False), False),
    (0.45, True, dict(adaylar=[], neden="panel_donuk"), False),                    # kapsama True gelse bile panel_donuk
])
def test_cizelge_kural_tablosu(tmp_path, saat, ic, canli, ek, supheli):
    olaylar = []
    m = motor(tmp_path, olaylar=olaylar)
    s = _satir(m, 1, ic)
    _karar(m, 1, cizelge_canli=canli, **ek)
    assert s["supheli"] is supheli
    assert not any(t == "satir_sil" for t, _ in olaylar) and s in m.mevcut      # zaman cizelgesi asla silmez
    assert motor_mod.nota_girer(s)                                               # 'ic' olmadigindan her durumda notta
    assert s["hal_ic"] == ic
    if supheli:
        assert s["supheli_neden"] == "cizelge" and s["cizelge_etkin"] is False
        assert s["hal_puani"] == pytest.approx(ic + 0.3) and s["speaker"] == "?"
        assert "Bilinmeyen: Kampanya görsellerini" in motor_mod.blok_metni([s])
        assert any("altyazı canlıyken etkinlik yok; nota Bilinmeyen olarak girer" in v for t, v in olaylar if t == "log")
        kayit = [r for r in motor_mod.altyazi_kayitlari(m.klasor) if r["id"] == s["id"]][0]
        assert kayit["supheli"] is True and kayit["supheli_neden"] == "cizelge"
    else:
        assert "supheli_neden" not in s and s["hal_puani"] == ic
        assert not any("nota Bilinmeyen olarak girer" in v for t, v in olaylar if t == "log")


def test_etkinliksiz_canli_klip_icsel_puansiz_not_supheli_ama_cizelge_etkin_kaydi_tutulur(tmp_path, saat):
    m = motor(tmp_path)
    s = _satir(m, 1, 0.0)
    _karar(m, 1, cizelge_canli=True, **YOK)
    assert not s["supheli"] and s["cizelge_etkin"] is False and s["hal_puani"] == 0.0
    s2 = _satir(m, 2, 0.4)
    _karar(m, 2, cizelge_canli=False, **YOK)
    assert "cizelge_etkin" not in s2                       # canli degil: gozlem sayilmaz


def test_icsel_supheli_ic_kalir_nota_girmez_cizelge_degistirmez(tmp_path, saat):
    m = motor(tmp_path)
    s = _satir(m, 1, 0.6, supheli=True)                    # ses.py: icsel puan >= hal_supheli
    assert s["supheli_neden"] == "ic" and s["hal_ic"] == 0.6
    _karar(m, 1, cizelge_canli=True, **YOK)
    assert s["supheli"] and s["supheli_neden"] == "ic" and s["hal_puani"] == 0.6    # cizelge puani eklenmez
    assert not motor_mod.nota_girer(s) and motor_mod.blok_metni([s]) == ""
    assert [x["tur"] for x in m.belirsizler()] == ["supheli"]
    m.kapat()


def test_hicbir_yol_zaman_cizelgesiyle_silmez_silme_yalniz_hal_ic(tmp_path, saat):
    olaylar = []
    m = motor(tmp_path, olaylar=olaylar)
    for no, ic in enumerate((0.0, 0.3, 0.5, 0.6, 0.89), 1):
        _satir(m, no, ic, supheli=ic >= 0.5)
        _karar(m, no, cizelge_canli=True, **YOK)
        _karar(m, no, cizelge_canli=True, **YOK)           # ikinci karar da silmez
    assert not any(t == "satir_sil" for t, _ in olaylar) and len(m.mevcut) == 5
    s = _satir(m, 9, 0.9, supheli=True)                    # icsel puan >= hal_at
    _karar(m, 9, cizelge_canli=False, **YOK)
    assert ("satir_sil", {"id": s["id"], "neden": "halusinasyon"}) in olaylar and s not in m.mevcut
    m.kapat()


def test_gec_yukseltme_etkinlik_gelirse_supheli_kalkar_hal_puani_ic_olur(tmp_path, saat):
    olaylar = []
    m = motor(tmp_path, olaylar=olaylar)
    s = _satir(m, 1, 0.4)
    _karar(m, 1, cizelge_canli=True, **YOK)
    assert s["supheli"] and s["supheli_neden"] == "cizelge" and s["hal_puani"] == pytest.approx(0.7)
    _karar(m, 1, speaker=A, guven=0.9, cizelge_canli=True, guncelleme=True, **VAR)       # satir geç geldi
    assert not s["supheli"] and "supheli_neden" not in s and "cizelge_etkin" not in s
    assert s["hal_puani"] == pytest.approx(0.4) and s["speaker"] == A
    kayit = [r for r in motor_mod.altyazi_kayitlari(m.klasor) if r["id"] == s["id"]][0]
    assert kayit["supheli"] is False and kayit["speaker"] == A and not kayit.get("cizelge_etkin")
    son = [v for t, v in olaylar if t == "satir_guncelle"][-1]
    assert son["supheli"] is False and son["gorunen"] == A
    assert motor_mod.blok_metni([s]).startswith(f"{A}: ")
    m.kapat()


def test_gec_yukseltme_kapsama_yoktan_adla_ve_icsel_supheli_korunur(tmp_path, saat):
    m = motor(tmp_path)
    s = _satir(m, 1, 0.45)
    _karar(m, 1, cizelge_canli=False, kapsama=False, adaylar=[], neden="kapsama_yok")
    assert not s["supheli"] and s["speaker"] == "?" and "cizelge_etkin" not in s
    _karar(m, 1, speaker=A, guven=0.9, guncelleme=True, **VAR)
    assert s["speaker"] == A and not s["supheli"]
    ic = _satir(m, 2, 0.7, supheli=True)
    _karar(m, 2, cizelge_canli=True, **YOK)
    _karar(m, 2, speaker=A, guven=0.9, cizelge_canli=True, guncelleme=True, **VAR)
    assert ic["supheli"] and ic["supheli_neden"] == "ic"          # icsel suphe etkinlikle kalkmaz
    m.kapat()


def test_cizelge_suphesi_konusmaci_adi_ne_olursa_olsun_bilinmeyen_etiketi(tmp_path, saat):
    m = motor(tmp_path)
    s = _satir(m, 1, 0.3)
    _karar(m, 1, speaker=A, guven=0.95, cizelge_canli=True, adaylar=[[A, 0.0]], neden="baskin")
    assert s["supheli_neden"] == "cizelge" and s["speaker"] == "?" and not kesin_ad_mi(s)
    satir = dict(s, speaker=A, spk_guven=0.99)                    # eski / elle yazilmis kayit: ad kalsa bile etiket zorlanir
    assert motor_mod.blok_metni([satir]) == "Bilinmeyen: Kampanya görsellerini de ekledim."
    assert motor_mod._konusan(satir) == "Bilinmeyen" and motor_mod.nota_girer(satir)
    assert motor_mod.nota_girer(dict(satir, supheli=True, supheli_neden="ic")) is False
    assert motor_mod.nota_girer(dict(satir, supheli=True, supheli_neden=None)) is False     # eski kayit: nota girmez
    m.kapat()


# ---------------------------------------------------------------- B5: atama basarisizligi uyarisi

def _atama_olaylari(olaylar):
    return [v for t, v in olaylar if t == "atama"]


def _kararlar(m, saat, say, speaker="?", t0=None, adim=1.0, no0=None, **ek):
    """say adet loopback karari (kapsamali); no0'dan baslayan klip_no, her karar adim sn arayla."""
    no0 = no0 if no0 is not None else getattr(m, "_test_no", 0) + 1
    for i in range(say):
        no = no0 + i
        if t0 is not None:
            saat.t = t0 + i * adim
        _satir(m, no, 0.0)
        _karar(m, no, speaker=speaker, guven=0.0 if speaker == "?" else 0.9,
               **dict(dict(adaylar=[], neden="kimse") if speaker == "?" else VAR, **ek))
    m._test_no = no0 + say - 1


def test_atama_uyarisi_tetiklenir_hooklari_cagirir_ve_temizlenir(tmp_path, saat):
    olaylar = []
    m = motor(tmp_path, olaylar=olaylar)
    cagrilar = []
    m.yeniden_bul_fn = lambda: cagrilar.append("bul")
    _kararlar(m, saat, 9, t0=T0 + 10)
    assert _atama_olaylari(olaylar) == [] and m.konusmaci.l_olcum == 0           # 9 karar: yetmez
    _kararlar(m, saat, 3)
    ev = _atama_olaylari(olaylar)
    assert len(ev) == 1 and ev[0]["var"] is False and ev[0]["oran"] == 1.0 and ev[0]["bas"] == saat.t
    assert cagrilar == ["bul"] and m.konusmaci.l_olcum == 1                      # panel yeniden aranir, L yeniden olculur
    log = [v for t, v in olaylar if t == "log" and "Konuşmacı atanamıyor" in v]
    assert log == ["! Konuşmacı atanamıyor: altyazı okunuyor ama konuşmalarla eşleşmiyor (son 2 dk %100 Bilinmeyen) "
                   "— panel yeniden aranıyor, gecikme yeniden ölçülüyor"]
    olay_kayitlari = [json.loads(x) for x in open(os.path.join(m.klasor, "olaylar.jsonl"), encoding="utf-8")]
    assert [r for r in olay_kayitlari if r.get("olay") == "atama"][0]["mesaj"] == log[0]
    _kararlar(m, saat, 2)
    m.kontrol()
    assert len(_atama_olaylari(olaylar)) == 1 and cagrilar == ["bul"]            # tek sefer (120 sn sogukluk)
    # kapsamali kararlarin '?' payi %30'un altina inince kalkar
    _kararlar(m, saat, 40, speaker=A)
    ev = _atama_olaylari(olaylar)
    assert [e["var"] for e in ev] == [False, True] and ev[-1]["oran"] < 0.3
    assert m._atama_uyarildi is False
    m.kapat()


def test_atama_uyari_ayakta_120_sn_sonra_hala_bozuksa_hooklar_yeniden_calisir(tmp_path, saat):
    olaylar = []
    m = motor(tmp_path, olaylar=olaylar)
    cagrilar = []
    m.yeniden_bul_fn = lambda: cagrilar.append("bul")
    _kararlar(m, saat, 12, t0=T0 + 10)
    assert cagrilar == ["bul"]
    _kararlar(m, saat, 12, t0=T0 + 60)                      # 120 sn dolmadi
    assert cagrilar == ["bul"]
    _kararlar(m, saat, 12, t0=T0 + 140)                     # ilk uyari + 120 sn gecti, hala bozuk
    assert cagrilar == ["bul", "bul"] and m.konusmaci.l_olcum == 2
    assert [e["var"] for e in _atama_olaylari(olaylar)] == [False, False]
    m.kapat()


@pytest.mark.parametrize("senaryo", ["az_karar", "kapsama_yok", "mikrofon", "oran_dusuk", "eski_karar"])
def test_atama_uyarisi_tetiklenmemesi_gerekenler(tmp_path, saat, senaryo):
    olaylar = []
    m = motor(tmp_path, olaylar=olaylar)
    if senaryo == "az_karar":
        _kararlar(m, saat, 9, t0=T0)
    elif senaryo == "kapsama_yok":                          # kapsama_yok ayri uyari (kapsama): atama sayilmaz
        _kararlar(m, saat, 30, t0=T0, kapsama=False, neden="kapsama_yok", cizelge_canli=False)
    elif senaryo == "mikrofon":
        _kararlar(m, saat, 30, t0=T0, akis="mikrofon")
    elif senaryo == "oran_dusuk":                           # %50 '?'
        for i in range(10):
            _kararlar(m, saat, 1, speaker="?" if i % 2 else A, t0=T0 + i)
    else:                                                    # 6 + 6 '?': ilk 6'si 120 sn'den eski, penceredeki 6 < 10
        _kararlar(m, saat, 6, t0=T0)
        _kararlar(m, saat, 6, t0=T0 + 200)
    assert _atama_olaylari(olaylar) == [] and m.konusmaci.l_olcum == 0
    m.kapat()


def test_atama_hooklari_korumali_hata_ya_da_yok_digerini_engellemez(tmp_path, saat):
    olaylar = []
    m = motor(tmp_path, olaylar=olaylar)

    def patlar():
        raise RuntimeError("x")
    m.yeniden_bul_fn = patlar
    _kararlar(m, saat, 12, t0=T0)
    assert m.konusmaci.l_olcum == 1 and len(_atama_olaylari(olaylar)) == 1
    assert any("panel yeniden arama başarısız" in v for t, v in olaylar if t == "log")
    m2 = motor(tmp_path, ad="t2", olaylar=olaylar)
    m2.konusmaci.L_yeniden_olc = None                       # eski konusmaci: kanca yok (getattr korumasi)
    assert m2.yeniden_bul_fn is None
    _kararlar(m2, saat, 12, t0=T0 + 500)
    assert m2._atama_uyarildi
    m.kapat()
    m2.kapat()


def test_atama_gec_guncelleme_ayni_klibi_degistirir(tmp_path, saat):
    olaylar = []
    m = motor(tmp_path, olaylar=olaylar)
    _kararlar(m, saat, 12, t0=T0)                           # 12 '?' -> uyari
    assert m._atama_uyarildi
    for no in range(1, 13):                                  # geç guncellemelerle hepsi ada cikar
        _karar(m, no, speaker=A, guven=0.9, guncelleme=True, cizelge_canli=True, **VAR)
    assert [e["var"] for e in _atama_olaylari(olaylar)] == [False, True]
    m.kapat()


# ---------------------------------------------------------------- kapsama mesaji neden'e gore

@pytest.mark.parametrize("neden,beklenen", [
    ("panel_donuk", "Teams altyazı paneli güncellenmiyor (pencere küçültülmüş/arka planda olabilir)"),
    ("okuma_yok", "altyazı okuma döngüsü duraklıyor"),
    ("grup_yok", "Teams altyazısı kapalı ya da okunamıyor"),
    (None, "Teams altyazısı kapalı ya da okunamıyor"),
])
def test_kapsama_uyarisi_nedene_gore(tmp_path, saat, neden, beklenen):
    olaylar = []
    m = motor(tmp_path, olaylar=olaylar)
    m.konusmaci.kapsama, m.konusmaci.neden = False, neden
    m.kontrol()
    saat.t += motor_mod.KAPSAMA_UYARI_SN + 1
    m.kontrol()
    ev = [v for t, v in olaylar if t == "kapsama"]
    assert len(ev) == 1 and ev[0]["var"] is False and ev[0]["mesaj"] == beklenen and ev[0]["neden"] == neden
    assert any(v == f"! Konuşmacı bilgisi yok: {beklenen} — konuşmacılar 'Bilinmeyen' yazılır"
               for t, v in olaylar if t == "log")
    m.konusmaci.kapsama = True
    m.kontrol()
    assert [v["var"] for t, v in olaylar if t == "kapsama"] == [False, True]
    m.kapat()


# ---------------------------------------------------------------- K1 kancalari

def test_son_loopback_ses_klip_olayindan_guncellenir(tmp_path, saat):
    m = motor(tmp_path)
    assert m.son_loopback_ses() is None
    m.ses_olayi(klip(1, 10, 12))
    assert m.son_loopback_ses() == T0 + 12
    m.ses_olayi(klip(2, 3, 5))                              # gec gelen eski klip geriletmez
    assert m.son_loopback_ses() == T0 + 12
    m.ses_olayi(klip(3, 20, 22, akis="mikrofon"))           # mikrofon sayilmaz
    m.ses_olayi(klip(4, 30, 33, metin="  "))                # metinsiz klip (gurultu) sayilmaz
    assert m.son_loopback_ses() == T0 + 12
    m.ses_olayi(klip(5, 40, 44))
    assert m.son_loopback_ses() == T0 + 44
    m.kapat()


def test_olay_kaydet_log_ve_olaylar_jsonl(tmp_path, saat):
    olaylar = []
    m = motor(tmp_path, olaylar=olaylar)
    m.olay_kaydet("altyazı paneli 12 sn değişmedi", neden="donuk", sn=12)
    assert ("log", "altyazı paneli 12 sn değişmedi") in olaylar
    kayit = [json.loads(x) for x in open(os.path.join(m.klasor, "olaylar.jsonl"), encoding="utf-8")][-1]
    assert kayit["tur"] == "log" and kayit["kaynak"] == "yakalayici" and kayit["mesaj"] == "altyazı paneli 12 sn değişmedi"
    assert kayit["neden"] == "donuk" and kayit["sn"] == 12 and "an" in kayit
    assert m.yeniden_bul_fn is None                         # atribut var, varsayilan None
    m.kapat()


def test_okuma_yeni_alanlariyla_konusmaciya_aynen_gider(tmp_path, saat):
    m = motor(tmp_path)
    okuma = {"tur": "okuma", "okuma_no": 7, "bas_epoch": T0, "bit_epoch": T0 + 0.1, "grup": True,
             "gorunen": [[A, "merhaba"]], "degisti": False, "donuk": True, "yeniden_bul": 2,
             "pencere": {"simge": True, "buyuk": False, "onplan": False}, "okuma_sn": 0.42}
    m.okuma_ekle(okuma)
    assert m.konusmaci.okumalar == [okuma] and m.konusmaci.okumalar[0] is okuma


# ---------------------------------------------------------------- arayuz: atama seridi + cizelge satiri

def test_arayuz_atama_seridi_kapsama_mesaji_ve_cizelge_satiri(pencere, qapp):
    p = pencere
    assert not p.atama_serit.isVisibleTo(p)
    p.olay("atama", {"var": False, "oran": 0.74, "bas": T0})
    assert p.atama_serit.isVisibleTo(p) and "Konuşmacı atanamıyor" in p.atama_serit.text()
    assert "%74" in p.atama_serit.text()
    p.olay("atama", {"var": True, "oran": 0.1, "bas": None})
    assert not p.atama_serit.isVisibleTo(p)
    p.olay("atama", {"var": False, "oran": 0.8, "bas": T0})
    p.olay("kapsama", {"var": False, "bas": T0, "mesaj": "Teams altyazı paneli güncellenmiyor (pencere küçültülmüş/arka "
                                                           "planda olabilir)"})
    assert p.kapsama_serit.isVisibleTo(p) and "paneli güncellenmiyor" in p.kapsama_serit.text()
    p.olay("kapsama", {"var": True, "bas": None})
    p.olay("kapsama", {"var": False, "bas": T0})
    assert "Teams altyazısı kapalı ya da okunamıyor" in p.kapsama_serit.text()      # mesajsiz: varsayilan metin
    s = {"id": 1, "klip_no": 1, "ts": "10:00:01", "speaker": "?", "spk_guven": 0.0, "text": "Kampanya görselleri hazır.",
         "kaynak": "ses", "durum": "karar", "supheli": True, "supheli_neden": "cizelge"}
    html = p._satir_html(s)
    assert "nota Bilinmeyen olarak girer" in html and "title=" in html and "nota girmez" not in html
    assert "nota girmez" in p._satir_html(dict(s, supheli_neden="ic"))


# ---------------------------------------------------------------- K1b: halusinasyon klibi panel canliligini beslemez

def test_halusinasyon_klibi_lb_ses_bitini_ilerletmez_ve_konusma_suresi(tmp_path, saat):
    m = motor(tmp_path)
    assert m.loopback_konusma_sn(T0) == 0.0 and m.loopback_konusma_sn(None) == 0.0 and m.loopback_konusma_sn("x") == 0.0
    m.ses_olayi(klip(1, 10, 12))                                # temiz klip
    assert m.son_loopback_ses() == T0 + 12
    hal = dict(klip(2, 20, 26), hal_puani=0.95)                 # icsel suzgec: hal_at (0.9) ustu
    m.ses_olayi(hal)
    assert m.son_loopback_ses() == T0 + 12                      # ilerlemedi
    sup = dict(klip(3, 30, 36), supheli=True, hal_puani=0.55)   # ses.py 'supheli' isaretli
    m.ses_olayi(sup)
    assert m.son_loopback_ses() == T0 + 12
    assert m.loopback_konusma_sn(T0 + 5) == 2.0                 # yalniz temiz klip sayildi
    m.ses_olayi(klip(4, 11, 14))                                # ortusen temiz klip: birlesim 10-14
    m.ses_olayi(klip(5, 40, 43))
    assert m.son_loopback_ses() == T0 + 43
    assert m.loopback_konusma_sn(T0 + 5) == 7.0                 # 4 + 3
    assert m.loopback_konusma_sn(T0 + 13) == 4.0                # 1 + 3 (bas_epoch'tan itibaren)
    assert m.loopback_konusma_sn(T0 + 41) == 2.0
    assert m.loopback_konusma_sn(T0 + 100) == 0.0
    m.ses_olayi(klip(6, 50, 52, akis="mikrofon"))               # mikrofon sayilmaz
    assert m.loopback_konusma_sn(T0 + 45) == 0.0
    m.kapat()


# ---------------------------------------------------------------- M2: kesin ad payi dusuk ('kesin_az') ve 'gecikme'

def test_kesin_az_uyarisi_tetiklenir_ve_kalkar(tmp_path, saat):
    olaylar = []
    m = motor(tmp_path, olaylar=olaylar)
    cagrilar = []
    m.yeniden_bul_fn = lambda: cagrilar.append("bul")
    # 12 'Ad?' karari (spk_guven 0.6 < KESIN_GUVEN, neden yapisal degil): '?' payi 0 ama kesin pay 0 -> kesin_az
    for i in range(12):
        saat.t = T0 + 10 + i
        no = getattr(m, "_test_no", 0) + 1
        _satir(m, no, 0.0)
        _karar(m, no, speaker=A, guven=0.6, adaylar=[[A, 0.6]], neden="baskin")
        m._test_no = no
    ev = _atama_olaylari(olaylar)
    assert len(ev) == 1 and ev[0]["var"] is False and ev[0]["olcut"] == "kesin_az" and ev[0]["oran"] == 0.0
    assert ev[0]["kesin_oran"] == 0.0 and "yalnız %0'i kesin" in ev[0]["mesaj"]
    assert cagrilar == ["bul"] and m.konusmaci.l_olcum == 1
    log = [v for t, v in olaylar if t == "log" and "Konuşmacı ataması belirsiz" in v]
    assert len(log) == 1 and "panel yeniden aranıyor" in log[0]
    kayit = [json.loads(x) for x in open(os.path.join(m.klasor, "olaylar.jsonl"), encoding="utf-8")]
    assert [r for r in kayit if r.get("olay") == "atama"][0]["olcut"] == "kesin_az"
    # sogukluk: 120 sn dolmadan tekrar yok
    for i in range(5):
        saat.t = T0 + 30 + i
        no = m._test_no + 1
        _satir(m, no, 0.0)
        _karar(m, no, speaker=A, guven=0.6, adaylar=[[A, 0.6]], neden="baskin")
        m._test_no = no
    assert len(_atama_olaylari(olaylar)) == 1 and cagrilar == ["bul"]
    # kesin pay 2 x ATAMA_KESIN_ORAN (%60) ustune cikinca kalkar
    for i in range(40):
        saat.t = T0 + 40 + i
        no = m._test_no + 1
        _satir(m, no, 0.0)
        _karar(m, no, speaker=A, guven=0.9, adaylar=[[A, 0.9]], neden="baskin")
        m._test_no = no
    ev = _atama_olaylari(olaylar)
    assert [e["var"] for e in ev] == [False, True] and ev[-1]["kesin_oran"] >= 2 * motor_mod.ATAMA_KESIN_ORAN
    assert m._atama_uyarildi is False and m._atama_olcut is None
    m.kapat()


@pytest.mark.parametrize("senaryo", ["temiz", "yapisal_tavan", "az"])
def test_kesin_az_tetiklenmemesi_gerekenler(tmp_path, saat, senaryo):
    """Temiz akista (kesin adlar) sessiz; yapisal tavanli (isinma capa_az, L_supheli, kisa, bosluk ...) 'Ad?' kararlari
    kesin_az olcutunde sayilmaz; < ATAMA_EN_AZ karar yetmez."""
    olaylar = []
    m = motor(tmp_path, olaylar=olaylar)
    if senaryo == "temiz":
        _kararlar(m, saat, 30, speaker=A, t0=T0)
    elif senaryo == "yapisal_tavan":
        for i, ek in enumerate(("capa_az", "L_supheli", "kisa", "bosluk", "L_onaysiz", "kaba", "gec", "mad") * 4):
            saat.t = T0 + i
            no = i + 1
            _satir(m, no, 0.0)
            _karar(m, no, speaker=A, guven=0.6, adaylar=[[A, 0.6]], neden="baskin+" + ek)
    else:
        for i in range(9):
            saat.t = T0 + i
            _satir(m, i + 1, 0.0)
            _karar(m, i + 1, speaker=A, guven=0.6, adaylar=[[A, 0.6]], neden="baskin")
    assert _atama_olaylari(olaylar) == [] and m.konusmaci.l_olcum == 0
    m.kapat()


def test_gecikme_olcutu_konusmaci_L_supheli_bildirince(tmp_path, saat):
    """konusmaci.durum()['L_supheli'] >= ATAMA_GECIKME_SN surerse 'gecikme' olcutlu uyari; suphe bitince kalkar."""
    olaylar = []
    m = motor(tmp_path, olaylar=olaylar)
    durum = {"L_supheli": False}
    taban = m.konusmaci.durum()
    m.konusmaci.durum = lambda: dict(taban, **durum)
    _kararlar(m, saat, 3, speaker=A, t0=T0)
    durum["L_supheli"] = True
    saat.t = T0 + 20
    m.kontrol()
    assert _atama_olaylari(olaylar) == []                                   # ilk gorulme: sayac basladi
    saat.t = T0 + 20 + motor_mod.ATAMA_GECIKME_SN - 1
    m.kontrol()
    assert _atama_olaylari(olaylar) == []
    saat.t = T0 + 20 + motor_mod.ATAMA_GECIKME_SN + 0.5
    m.kontrol()
    ev = _atama_olaylari(olaylar)
    assert len(ev) == 1 and ev[0]["olcut"] == "gecikme" and "gecikmesi değişmiş olabilir" in ev[0]["mesaj"]
    assert m.konusmaci.l_olcum == 1
    durum["L_supheli"] = False
    saat.t = T0 + 40
    m.kontrol()
    ev = _atama_olaylari(olaylar)
    assert [e["var"] for e in ev] == [False, True]
    m.kapat()


def test_arayuz_atama_seridi_olcut_mesaji(pencere, qapp):
    p = pencere
    p.olay("atama", {"var": False, "oran": 0.0, "kesin_oran": 0.2, "bas": T0, "olcut": "kesin_az",
                     "mesaj": "Konuşmacı ataması belirsiz: son 2 dk kararların yalnız %20'i kesin — panel yeniden "
                              "aranıyor, gecikme yeniden ölçülüyor"})
    assert p.atama_serit.isVisibleTo(p) and p.atama_serit.text().startswith("Konuşmacı ataması belirsiz")
    assert p.atama_serit.text().endswith("ölçülüyor.")
    p.olay("atama", {"var": False, "oran": 0.0, "bas": T0, "olcut": "gecikme",
                     "mesaj": "Altyazı gecikmesi değişmiş olabilir: konuşmacılar 'Ad?' yazılıyor — panel yeniden aranıyor, "
                              "gecikme yeniden ölçülüyor"})
    assert "gecikmesi değişmiş" in p.atama_serit.text()
    p.olay("atama", {"var": True, "oran": 0.0, "bas": None})
    assert not p.atama_serit.isVisibleTo(p)
