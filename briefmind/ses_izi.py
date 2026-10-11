"""
ses_izi.py — Faz 6 spike: altyazi kapaliyken '?' kalan loopback satirlari icin ses izi (konusmaci gommesi) yardimi.
MIMARI_v3 §3 "Ses gommesi", §9 satir 6. Varsayilan KAPALI (config 'ses_izi': false); sherpa-onnx zorunlu bagimlilik degil.

Akis (Motor parca kapanisinda, arka planda — Motor._ses_izi_isle):
  1) KESIN loopback satirlarindan (spk_guven >= 0.9, cakisma/devir yok, klip >= 2 sn; ses halka tampondan) kisi basina
     centroid kurulur (L2-normalize gommelerin toplami; yalniz toplanti suresince bellekte, diske YAZILMAZ).
  2) Altyazi kapaliyken konusmaci karari 'kapsama_yok' olan '?' satirlari centroid'lerle karsilastirilir.
     Atama yalniz: en iyi kosinus >= esik (0.75) VE ikinciye farki >= 0.10 VE o kisinin >= 5 centroid klibi VE
     en az iki kisi tanidik (tek kisiyle fark olculemez). Aksi halde satir '?' kalir.
  3) Atanan satir: speaker = kisi, spk_guven <= 0.6 (gosterim kurali -> "Ad?"; kesin_ad_mi False: katilimci/sahip uretmez),
     kaynak_konusmaci 'ses_izi'. Altyazi tabanli bir karari olan satira (Ad, Ad?, A → B, A / B, kapsama var) DOKUNULMAZ.

Cikarici sozlesmesi: Cikarici.vektor(ses_16k) -> np.ndarray (tek boyutlu gomme). Varsayilan: SherpaCikarici
(sherpa_onnx.SpeakerEmbeddingExtractor; config 'ses_izi_model' = var olan .onnx dosyasi). sherpa_onnx yoksa / model yoksa
ozellik tek log satiriyla kapanir (kur -> None). Testler/replay kendi Cikarici'sini verir.
"""
import os
import threading
import time

import numpy as np

from metin import kesin_ad_mi

KAYNAK = "ses_izi"
ORNEK = 16000
ESIK = 0.75                  # en iyi kosinus alt siniri (config 'ses_izi_esik')
MARJ = 0.10                  # en iyi ile ikinci kisi arasindaki en az kosinus farki
MIN_KLIP = 5                 # atanacak kisinin en az centroid klibi
TAVAN = 0.6                  # atanan satirin spk_guven ust siniri (sabit: 0.7 kesinlik esiginin altinda kalmali)
KESIN_GUVEN = 0.9            # centroid kurmak icin satirin en az spk_guven'i
MIN_SN = 2.0                 # centroid klibi ve sorgu klibi en az bu kadar sn
MAKS_SN = 20.0               # daha uzun klipte ortadaki bu kadar sn gomulur (RTF)
SES_PAYI = 0.95              # halka tampondan gelen ses, istenen surenin en az bu kadari olmali


class Cikarici:
    """Gomme cikarici sozlesmesi. vektor(ses_16k): 16 kHz mono float32 dizi -> tek boyutlu np.ndarray (None: gomulemedi)."""

    def vektor(self, ses_16k):
        raise NotImplementedError


class SherpaCikarici(Cikarici):
    """sherpa_onnx.SpeakerEmbeddingExtractor sarmalayicisi. Kurulamazsa istisna firlatir (kur() yakalar ve ozelligi kapatir)."""

    def __init__(self, model, sherpa=None, is_parcacigi=1):
        if sherpa is None:
            import sherpa_onnx as sherpa
        self._sherpa = sherpa
        cfg = sherpa.SpeakerEmbeddingExtractorConfig(model=model, num_threads=int(is_parcacigi), debug=False,
                                                     provider="cpu")
        if hasattr(cfg, "validate") and not cfg.validate():
            raise ValueError("sherpa-onnx model yapılandırması geçersiz")
        self._cikarici = sherpa.SpeakerEmbeddingExtractor(cfg)

    def vektor(self, ses_16k):
        akim = self._cikarici.create_stream()
        akim.accept_waveform(sample_rate=ORNEK, waveform=np.asarray(ses_16k, dtype=np.float32))
        akim.input_finished()
        if not self._cikarici.is_ready(akim):
            return None
        return np.asarray(self._cikarici.compute(akim), dtype=np.float32)


def _sayi(deger, varsayilan, alt, ust):
    try:
        x = float(deger)
    except (TypeError, ValueError):
        return varsayilan
    return x if alt <= x <= ust else varsayilan


def kur(ayar, ses_al_fn, ben=None, log=None, cikarici=None, sherpa=None):
    """config -> SesIzi | None. ayar: 'ses_izi' (bool, varsayilan False), 'ses_izi_model' (var olan .onnx), 'ses_izi_esik'
    (0.5..0.99, varsayilan 0.75). Kapaliysa sessizce None. Aciksa ama kurulamazsa TEK log satiri + None (not hatti etkilenmez).
    cikarici/sherpa: test ve replay icin enjekte edilir (cikarici verilirse model aranmaz)."""
    ayar = ayar or {}
    log = log or (lambda m: None)
    if ayar.get("ses_izi") is not True:
        return None
    if ses_al_fn is None:
        log("ses izi kapalı: ses kaynağı (halka tampon) bağlı değil")
        return None
    if cikarici is None:
        model = ayar.get("ses_izi_model")
        if not isinstance(model, str) or not model.strip().lower().endswith(".onnx") or not os.path.isfile(model.strip()):
            log("ses izi kapalı: 'ses_izi_model' var olan bir .onnx dosyasını göstermiyor")
            return None
        try:
            cikarici = SherpaCikarici(model.strip(), sherpa=sherpa)
        except ImportError:
            log("ses izi kapalı: sherpa-onnx kurulu değil (isteğe bağlı bağımlılık: pip install sherpa-onnx)")
            return None
        except Exception as e:
            log(f"ses izi kapalı: model yüklenemedi ({type(e).__name__})")
            return None
    esik = _sayi(ayar.get("ses_izi_esik", ESIK), ESIK, 0.5, 0.99)
    return SesIzi(cikarici, ses_al_fn, esik=esik, ben=ben, log=log)


def _birim(v):
    """Vektor -> L2-normalize float64 ya da None (bos, sifir, sonlu degil)."""
    try:
        a = np.asarray(v, dtype=np.float64).reshape(-1)
    except (TypeError, ValueError):
        return None
    if a.size == 0 or not np.all(np.isfinite(a)):
        return None
    n = float(np.linalg.norm(a))
    return a / n if n > 1e-9 else None


class Merkezler:
    """Kisi basina centroid: normalize gommelerin toplami + klip sayisi. Kosinus = birim vektor . toplam/|toplam|."""

    def __init__(self):
        self._toplam = {}
        self._sayi = {}

    def ekle(self, ad, vektor):
        b = _birim(vektor)
        if b is None:
            return False
        if ad in self._toplam:
            if self._toplam[ad].shape != b.shape:
                return False                                   # boyut tutarsiz (cikarici degisti): atla
            self._toplam[ad] = self._toplam[ad] + b
        else:
            self._toplam[ad] = b.copy()
        self._sayi[ad] = self._sayi.get(ad, 0) + 1
        return True

    def klip_sayisi(self, ad):
        return self._sayi.get(ad, 0)

    def adlar(self):
        return list(self._toplam)

    def sirala(self, vektor):
        """[(ad, kosinus)] azalan; vektor gecersizse bos."""
        b = _birim(vektor)
        if b is None:
            return []
        sonuc = []
        for ad, t in self._toplam.items():
            if t.shape != b.shape:
                continue
            n = float(np.linalg.norm(t))
            if n > 1e-9:
                sonuc.append((ad, float(np.dot(b, t) / n)))
        sonuc.sort(key=lambda x: (-x[1], x[0]))
        return sonuc


def _sure(satir):
    try:
        return float(satir["bit"]) - float(satir["bas"])
    except (KeyError, TypeError, ValueError):
        return 0.0


class SesIzi:
    """Toplanti basina bir ornek. Is parcacigi guvenli (cikarici cagrilari ve centroid'ler tek kilit altinda).
    ses_al_fn(akis, bas_epoch, bit_epoch) -> 16 kHz float32 dizi | None (halkadan cikmis / yok)."""

    def __init__(self, cikarici, ses_al_fn, esik=ESIK, marj=MARJ, min_klip=MIN_KLIP, tavan=TAVAN, ben=None, log=None):
        self.cikarici = cikarici
        self.ses_al_fn = ses_al_fn
        self.esik, self.marj, self.min_klip = float(esik), float(marj), int(min_klip)
        self.tavan = min(float(tavan), TAVAN)               # config ne olursa olsun 0.6'yi gecemez
        self.ben = (ben or "").strip().lower() or None
        self.log = log or (lambda m: None)
        self.merkezler = Merkezler()
        self._ogrenilen = set()                             # centroid'e katilan satir id'leri (parca yeniden islenirse cift sayilmaz)
        self._kilit = threading.Lock()
        self.ses_sn = 0.0                                   # gomulen toplam ses (RTF payi)
        self.hesap_sn = 0.0                                 # gomme cikarma duvar suresi
        self.atanan = 0

    # ---------- uygunluk ----------
    def kesin_mi(self, satir):
        """Centroid'e girebilir mi: kesin tek kisi, loopback, spk_guven >= 0.9, cakisma/devir yok, klip >= 2 sn."""
        if not isinstance(satir, dict) or satir.get("akis") != "loopback" or satir.get("kaynak") != "ses":
            return False
        if satir.get("supheli") or satir.get("cakisma") or satir.get("devir") or satir.get("kapsama") is False:
            return False
        if satir.get("kaynak_konusmaci") == KAYNAK or satir.get("durum") != "karar":
            return False
        guven = satir.get("spk_guven")
        if isinstance(guven, bool) or not isinstance(guven, (int, float)) or guven < KESIN_GUVEN:
            return False
        if not kesin_ad_mi(satir):
            return False
        ad = str(satir.get("speaker") or "").strip()
        if self.ben and ad.lower() == self.ben:
            return False
        return _sure(satir) >= MIN_SN

    def aday_mi(self, satir):
        """Ses izi yardimi alabilir mi: yalniz altyazi kapaliyken ('kapsama_yok') '?' kalan loopback satiri. Altyazi tabanli
        bir karari olan (kimse/baskin/Ad?/A → B/A / B), ses izi atanmis, supheli, mikrofon satirlarina dokunulmaz."""
        if not isinstance(satir, dict) or satir.get("akis") != "loopback" or satir.get("kaynak") != "ses":
            return False
        if str(satir.get("speaker") or "").strip() != "?" or satir.get("durum") != "karar":
            return False
        if satir.get("kapsama") is not False or satir.get("spk_neden") != "kapsama_yok":
            return False
        if satir.get("supheli") or satir.get("cakisma") or satir.get("devir") or satir.get("kaynak_konusmaci"):
            return False
        return _sure(satir) >= MIN_SN

    # ---------- gomme ----------
    def _vektor(self, satir):
        """Satirin sesinden gomme (halka tampondan). None: ses yok/kisa ya da cikarici hata verdi (sessizce atlanir)."""
        try:
            ses = self.ses_al_fn(satir.get("akis"), satir["bas"], satir["bit"])
        except Exception:
            return None
        if ses is None:
            return None
        ses = np.asarray(ses, dtype=np.float32).reshape(-1)
        if len(ses) < MIN_SN * ORNEK * SES_PAYI:
            return None
        if len(ses) > MAKS_SN * ORNEK:
            ofs = (len(ses) - int(MAKS_SN * ORNEK)) // 2
            ses = ses[ofs:ofs + int(MAKS_SN * ORNEK)]
        t0 = time.perf_counter()
        try:
            v = self.cikarici.vektor(ses)
        except Exception:
            return None
        finally:
            self.hesap_sn += time.perf_counter() - t0
            self.ses_sn += len(ses) / ORNEK
        return v

    def ogren(self, satir):
        """Kesin satirdan centroid'e bir klip ekler. True: eklendi."""
        if not self.kesin_mi(satir) or satir.get("id") in self._ogrenilen:
            return False
        v = self._vektor(satir)
        if v is None or not self.merkezler.ekle(str(satir["speaker"]).strip(), v):
            return False
        self._ogrenilen.add(satir.get("id"))
        return True

    def karar(self, satir, vektor=None):
        """Aday satir icin atama alanlari ya da None. vektor verilirse ses alinmaz (test/replay)."""
        if not self.aday_mi(satir):
            return None
        v = vektor if vektor is not None else self._vektor(satir)
        if v is None:
            return None
        return self.karar_vektor(v)

    def karar_vektor(self, vektor):
        """Esik kurallari (saf): en iyi >= esik, ikinciye fark >= marj, en az iki kisi, en iyinin >= min_klip klibi."""
        sirali = self.merkezler.sirala(vektor)
        if len(sirali) < 2:
            return None                                     # tek tanidik kisi: marj olculemez, atama yok
        (ad, s1), (_, s2) = sirali[0], sirali[1]
        n = self.merkezler.klip_sayisi(ad)
        if n < self.min_klip or s1 < self.esik or (s1 - s2) < self.marj - 1e-9:
            return None
        return {"speaker": ad, "spk_guven": round(min(self.tavan, s1), 3), "kaynak_konusmaci": KAYNAK,
                "spk_neden": KAYNAK, "ses_izi": {"skor": round(s1, 3), "marj": round(s1 - s2, 3), "klip": n}}

    # ---------- parca kapanisi ----------
    def parca_isle(self, satirlar):
        """Parca satirlari: once kesin olanlardan centroid ogrenilir, sonra aday '?' satirlar karara baglanir.
        Dondurur [(satir, alan)] — uygulamak Motor'un isi (kilit altinda uygula ile). Satirlari DEGISTIRMEZ."""
        with self._kilit:
            for s in satirlar:
                self.ogren(s)
            sonuc = []
            for s in satirlar:
                alan = self.karar(s)
                if alan is not None:
                    sonuc.append((s, alan))
            return sonuc

    def uygula(self, satir, alan):
        """Karari satira yazar; yazma ani yeniden dogrulanir (arada altyazi karari geldiyse yazilmaz). True: yazildi."""
        if not self.aday_mi(satir):
            return False
        satir.update(alan)
        self.atanan += 1
        return True

    def istatistik(self):
        return {"kisi": {a: self.merkezler.klip_sayisi(a) for a in self.merkezler.adlar()},
                "ses_sn": round(self.ses_sn, 2), "hesap_sn": round(self.hesap_sn, 3),
                "rtf": round(self.hesap_sn / self.ses_sn, 3) if self.ses_sn > 0 else None, "atanan": self.atanan}
