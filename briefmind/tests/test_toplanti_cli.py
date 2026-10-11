"""toplanti.py basla (CLI): Teams altyazi METNI transkripte girmez; metin sesten (SesServisi -> Motor.ses_olayi),
okumalar Motor.okuma_ekle'ye gider (isler.YakalamaIsi ile ayni baglanti). Ses cihazi, ag, LLM, UIA yok; adlar sentetik."""
import types

import outlook
import toplanti

T0 = 1760170000.0


class SahteServis:
    son = None

    def __init__(self, adres, olay_fn=None, **kw):
        self.olay_fn = olay_fn
        self.durdu = False
        SahteServis.son = self

    def baslat(self):
        self.olay_fn({"tur": "klip", "klip_no": 1, "akis": "loopback", "bas": T0, "bit": T0 + 2.0, "sure": 2.0,
                      "text": "Sesten gelen doğru cümle.", "ts": "10:00:00", "rms": 0.1, "zarf": [0.1] * 20,
                      "ust_klip": None, "supheli": False})
        return True

    def yeniden_kes(self, no, an):
        return False

    @property
    def stt_durumu(self):
        return {"durum": "ok", "bas": None, "bekleyen": 0, "kayit": False, "eksik": [], "hata": None}

    def durdur(self):
        self.durdu = True


class SahteYakalayici:
    def __init__(self, katilimcilar=(), okuma_fn=None):
        self.okuma_fn = okuma_fn
        self.hazir = True
        self.tur = 0

    def oku(self):
        self.tur += 1
        if self.tur > 1:
            raise KeyboardInterrupt
        self.okuma_fn({"tur": "okuma", "okuma_no": 1, "bas_epoch": T0 + 1.0, "bit_epoch": T0 + 1.1, "grup": True,
                       "gorunen": [["Ayşe Demir", "bozuk altyazi metni"]]})
        return [{"ts": "10:00:01", "speaker": "Ayşe Demir", "text": "bozuk altyazi metni"}]

    def kayip_saniye(self):
        return 0

    def onerilen_aralik(self):
        return 0

    def bitir(self):
        return [{"ts": "10:00:02", "speaker": "Ayşe Demir", "text": "son bozuk altyazi"}]


def test_basla_altyazi_metnini_transkripte_yazmaz_sesi_baglar(tmp_path, monkeypatch):
    """Bulgu: komut_basla Yakalayici satirlarini m.satir_ekle ile ('altyazi-yedek') transkripte yaziyordu; ses servisi
    hic kurulmuyordu."""
    import llm
    import ses
    import terim
    monkeypatch.setattr(terim, "uygulama_klasoru", lambda: str(tmp_path))          # terimler/ repoya yazilmaz
    monkeypatch.setattr(ses, "SesServisi", SahteServis)
    monkeypatch.setattr(llm, "bolum_ozetle", lambda *a, **k: None)
    monkeypatch.setattr(toplanti, "Yakalayici", SahteYakalayici)
    monkeypatch.setattr(toplanti, "ekran_okuyucu", lambda acik: None)
    monkeypatch.setattr(toplanti.ayar_mod, "ayar_oku", lambda: {"stt_url": "", "ben": "Deniz Kaya"})
    monkeypatch.setattr(outlook, "aktif_toplanti", lambda: {})
    monkeypatch.setattr(toplanti.motor_mod, "yeni_klasor", lambda b, t: str(tmp_path / "k"))
    yakalanan = {}

    def tamamla(m, a_outlook, alicilar=None):
        m.bitir()
        yakalanan["m"] = m

    monkeypatch.setattr(toplanti, "tamamla", tamamla)
    a = types.SimpleNamespace(sessiz=True, baslik="CLI testi", duzeltme_yok=True, otobitir=180, outlook=False,
                              inceleme_yok=True)
    toplanti.komut_basla(a)
    m = yakalanan["m"]
    satirlar = m.tum_satirlar()
    assert [s["text"] for s in satirlar] == ["Sesten gelen doğru cümle."]
    assert all(s["kaynak"] == "ses" for s in satirlar)
    assert "altyazi" not in m.transkript_metni() and "bozuk" not in m.transkript_metni()
    assert SahteServis.son.durdu and m.yeniden_kes_fn is None
    assert m.ben == "Deniz Kaya" and m.konusmaci._son_okuma_bit == T0 + 1.1   # okuma konusmaciya gitti
    m.kapat()


def test_ben_adi_ayar_yoksa_yalniz_tam_ad_kullanilir(monkeypatch):
    assert outlook.ben_adi({"ben": "Deniz Kaya"}) == "Deniz Kaya"
    monkeypatch.setattr(outlook, "kullanici_adi", lambda: "Kaya, Deniz")
    assert outlook.ben_adi({"ben": ""}) == "Kaya, Deniz"                      # konusmaci 'Soyad, Ad' bicimini tanir
    monkeypatch.setattr(outlook, "kullanici_adi", lambda: "dkaya")
    assert outlook.ben_adi({}) is None                                          # oturum adi: Teams adiyla eslesmez
