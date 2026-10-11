"""
ses.py — Toplanti sesini yakalar, klip klip Whisper STT servisine gonderir, klip olaylari uretir (Faz 1).

Iki akis:
  loopback : hoparlor/kulaklik cikisi = karsi taraf
  mikrofon : sen (hoparlorle calisilirsa eko olur; konusmaci.py zamana bakarak eler)

Parcalama: konusma basladiktan sonra 0.55 sn sessizlik gorunce klip biter ve HEMEN STT'ye gider.
Sessizlik gelmezse 8 sn'den sonra ilk 0.25 sn'lik nefes payinda, o da gelmezse 12 sn'de zorla keser.
Konusmanin ilk hecesi kesilmesin diye 0.3 sn on-yuk eklenir.

Zaman tabani (plan 3): her akisin sifirinci orneginin epoch'u (epoch_capa) + ornek sayaci. Klip bas/bit bu sayactan gelir,
varis anindan degil; bit = SON SESLI blogun sonu. Her 60 sn'de sayac ile saat karsilastirilir, 0.3 sn'den fazla
kayma varsa yeniden capalanir (olay + olaylar.jsonl). Saat = saat.simdi() (monotonik tabanli): duvar saati adimi
(NTP/elle) ankrajlari ve altyazi okumalarini ayni anda etkilemez, ikisi hep ayni zaman tabaninda kalir.

Olaylar (SesServisi(olay_fn=...)): klip / bosluk / kulaklik sozlukleri, akis basina sirali (bkz. SesServisi).
olay_fn verilmezse eski yol: satir_fn(ts, konusmaci, metin, kaynak).

Halusinasyon: klip metni stt.halusinasyon_puani ile puanlanir (klip olayinda hal_puani); >= hal_supheli 'supheli', >= hal_at atilir.
Whisper prompt'u (ipucu_fn) yalniz yeterli RMS'li klipte gider; supheli sonuc promptsuz bir kez tekrar cozulur.
Cihazlar (cihaz.py): mikrofon akisi acik mikrofonlardan otomatik secilenin, loopback akisi tum cikis cihazlarinin
loopback karisiminin bloklaridir (soundcard, 16 kHz; tak-cikar 3 sn'de gorulur). Cihaz degisiminde akisa Gecis isareti
gelir: acik klip kapatilir, ornek sayaci surer, gerekirse saat capasi yenilenir ('capa' olayi). config 'mikrofon_cihaz'
yalniz elle ayardir. soundcard yoksa / acilamazsa eski yol: varsayilan hoparlor + sounddevice mikrofonu.
Eski yolda mikrofon 16 kHz disindaki cihaz hizinda acilirsa YenidenOrnekleyici (alcak geciren + secme; scipy varsa
resample_poly) indirir.

Halka tampon: her akisin son 10 dk'si float16 olarak bellekte; yeniden_kes(klip_no, an) klibi en sessiz blokta ikiye boler.

Uzun pencere (Faz 3, config 'uzun_pencere', varsayilan kapali): UzunPencere iscisi halka tampondan 20-28 sn'lik, sessizlikte
bolunmus, 1.5 sn ortusen pencereleri yeniden cozer; metin uzun.pencere_hizala ile kisa kliplere dagitilir ({"tur": "uzun"}).

Ses diske YAZILMAZ: klip bellekte tutulur, STT cevabi gelince silinir. Istisnalar (yerel, repo disi):
  - olcum kaydi (config 'olcum_kaydi', varsayilan kapali): klipler <klasor>/olcum/ altina yazilir.
  - STT kesintisi: 'olcum_kaydi' ya da 'kesinti_kaydi' aciksa cozulemeyen klipler <klasor>/olcum/kesinti/ altina yazilir,
    STT donunce sirayla cozulur.
  pip install sounddevice soundcard numpy requests
"""
import collections
import datetime as dt
import inspect
import itertools
import json
import math
import os
import queue
import re
import threading
import time

import numpy as np
import requests

import cihaz as cihaz_mod               # otomatik mikrofon secimi + loopback karisimi
import saat as saat_mod                 # adima dayanikli zaman kaynagi (time.time yerine)
import uzun as uzun_mod                 # Faz 3 uzun pencere hizalamasi (saf mantik)

# STT ve metin suzgecleri stt.py'ye tasindi; eski importlar (ses.SttIstemci, ses.sonuc_gecerli, ses.requests...) calismaya devam eder
from stt import (HAL_ESIKLER, HALUSINASYON, IPUCU_GONDER, IPUCU_KR_TAVAN, KISA_GECERLI, ORNEK, KesintiKuyrugu, SttIstemci,
                 cumlelere_bol, hal_esik, halusinasyon_puani, kesinti_hatasi_mi, segment_ozeti, sonuc_gecerli,
                 tekrar_temizle, wav_bayt, wav_oku)

# yeniden disa aktarilan adlar (pyflakes: kullanilmamis import sayilmasin)
__all__ = ["HAL_ESIKLER", "HALUSINASYON", "IPUCU_GONDER", "IPUCU_KR_TAVAN", "KISA_GECERLI", "ORNEK", "KesintiKuyrugu",
           "SttIstemci", "cumlelere_bol", "hal_esik", "halusinasyon_puani", "kesinti_hatasi_mi", "requests",
           "segment_ozeti", "sonuc_gecerli", "tekrar_temizle", "wav_bayt", "wav_oku",
           "AkisYakalayici", "HalkaTampon", "KulaklikTespit", "OlcumKaydi", "SesServisi", "UzunPencere",
           "YenidenOrnekleyici", "altyazi_son_fn", "bolme_noktasi", "cihazlari_listele", "normalize_et", "yeniden_ornekle",
           "zarf_hesapla"]

altyazi_son_fn = cihaz_mod.altyazi_son_fn      # isler/toplanti: SesServisi(altyazi_fn=ses.altyazi_son_fn(motor))

MIN_SN = 1.5                 # bundan kisa "cumle" gonderilmez (cok kisa klipte Whisper tekrar uretir)
CUMLE_SESSIZLIK = 0.55       # cumle sonu sayilan sessizlik (kisa: konusmaci degisimi ayni klibe dusmesin)
TAVAN_SN = 8.0               # bundan sonra ilk nefes payinda bol
NEFES_SESSIZLIK = 0.25
ZORLA_SN = 12.0              # sessizlik hic gelmezse
ONYUK_BLOK = 3               # 0.3 sn on-yuk
SESSIZ_ESIK = 0.006          # RMS taban esigi; gercek esik gurultu tabanina gore uyarlanir
GURULTU_KATI = 3.5           # esik = max(SESSIZ_ESIK, taban * GURULTU_KATI)
ACILIS_BLOK = 3              # konusma sayilmasi icin ust uste bu kadar "sesli" blok (0.3 sn) — klik/tus sesini eler
KONUSMA_ORANI = 0.35         # parcadaki sesli blok orani bundan azsa gonderilmez
BLOK = 1600                  # 0.1 sn
KUYRUK_UYARI = 12            # bu kadar cumle birikirse STT yetisemiyor: uyar (parca atilmaz)
KUYRUK_TAVAN = 60            # ancak bu kadar birikirse (STT fiilen yok) klip diske (izin varsa) ya da bosluga gider; ~45 MB bellek
BOLME_PENCERE = 15           # zorla kesimde son 1.5 sn icindeki en sessiz blokta bolunur
NORMAL_TEPE = 0.3            # tepe genligi bundan dusuk (kisik) parca Whisper'a gitmeden yukseltilir
NORMAL_HEDEF = 0.9           # yukseltilen parcanin hedef tepesi
NORMAL_KAZANC = 8.0          # en fazla kazanc (gurultu tabani da o kadar yukselir)
ISCI_SAYISI = 2

HALKA_SN = 600               # halka tampon: akis basina son 10 dk (float16 ~ 19 MB)
KES_ARALIK_SN = 1.0          # yeniden_kes: an +- bu kadar icinde en sessiz 100 ms blok
KES_MIN_PARCA_SN = 1.0       # bolunen parcalardan biri bundan kisaysa bolunmez ("A -> B" kalir)
KES_KONTRAST = 0.4           # kesim blogu RMS'i klibin medyan blok RMS'inin bu katindan buyukse sessiz blok yok:
                             # sinir ust uste binen konusmanin icinde, kesilmez ("A -> B" kalir)
CAPA_ARALIK_SN = 60          # saat capasi bu sure aralikla (akis zamani) denetlenir
CAPA_ESIK_SN = 0.3           # |simdi - t| bundan buyukse yeniden capalanir
CAPA_GECIS_ESIK_SN = 0.05    # cihaz gecisinden sonraki ilk blokta bu esikle denetlenir (gecis boslugu/ortusmesi)
HOL_SN = 60                  # sirada bekleyen sonuc icin: bastaki numara bu kadar gelmezse atlanir
IPUCU_RMS = 0.015            # Whisper prompt'u yalniz sesli aralik RMS'i bundan buyuk klipte gider (config esikler 'ipucu_rms');
                             # sessiz/gurultulu klipte prompt kelimeleri ciktiya sizar (Faz 0 denemesi)
TEKRAR_YAKIN_SN = 30         # ayni akista ayni cumle (>= 3 kelime) bu sure icinde ikinci kez gelirse tekrar sayilir
TEKRAR_PENCERE_SN = 3600     # ... ve bu pencerede (1 saat) ucuncu kez gelirse; eskiden toplanti boyunca sayilirdi
KISA_CUMLE_KELIME = 2        # bu kadar ya da daha kisa cumle ('Tamam, anladim.') tekrar suzgecine girmez: gercek kisa cevap
KESINTI_YOKLAMA = 5.0        # STT kesintisinde saglik yoklamasi araligi (sn)
KESINTI_BITIS_SN = 120       # durdurulurken kesinti kuyrugunun bosalmasi icin en fazla bekleme
KUYRUK_KLIP_DENEME = 3       # kesinti kuyrugunun basindaki klip saglik yoklamasi gecerken bu kadar kez ust uste
                             # hata verirse klibe ozgu sayilir: 'stt_hata' boslugu olur, kuyruktan duser

# pasif kulaklik tespiti (plan 3: mikrofon RMS <-> loopback RMS korelasyonu)
KULAKLIK_PENCERE_SN = 30     # korelasyon penceresi
KULAKLIK_MIN_VERI_SN = 15    # pencerede iki akisin ortak verisi bundan azsa karar yok (None)
KULAKLIK_MIN_SESLI = 40      # loopback'te en az bu kadar 100 ms'lik sesli kare (4 sn uzak konusma) gerekir
KULAKLIK_LAG_MAX = 3         # 0-300 ms gecikme (hoparlor -> hava -> mikrofon)
KULAKLIK_YUKSEK = 0.40       # korelasyon >= bu: hoparlor (kulaklik yok)
KULAKLIK_DUSUK = 0.25        # korelasyon < bu: kulaklik var; arasi: karar yok, onceki durum korunur
KULAKLIK_YUMUSAK = 11        # yavas bilesen (konusma var/yok basamaklari) 1.1 sn'lik ortalama ile cikarilir
KULAKLIK_DEGERLENDIR_SN = 5  # degerlendirme sikligi (akis zamani)

# Faz 3 uzun pencere (config 'uzun_pencere', varsayilan kapali; MIMARI_v3 §4 "Uzun pencere")
UZUN_MIN_SN = 20.0           # pencere sesi (ortusme + sahip klipler + kuyruk) bu kadar olunca klip sinirinda bolunur ...
UZUN_MAKS_SN = 28.0          # ... ve bunu asmaz (aradaki en genis sessizlikte)
UZUN_ORTUSME_SN = 1.5        # pencere ilk sahip klibinden bu kadar once baslar (onceki pencereyle ortusme; LCS dikisi)
UZUN_KUYRUK_SN = 0.3         # son sahip klibin ardindan pencereye eklenen ses (sonraki klibe tasmaz)
UZUN_SESSIZLIK_SN = 2.0      # klibin ardindan bu kadar sessizlik: pencere orada kapanir (uzun sessizlik pencereye girmez)
UZUN_METIN_BEKLE_SN = 30     # pencerenin kisa klip metinleri en fazla bu kadar beklenir (gelmezse pencere atlanir)
UZUN_YOKLAMA_SN = 0.5        # uzun pencere iscisinin tur araligi
UZUN_BITIS_SN = 60           # durdurulurken kalan pencerelerin cozulmesi icin en fazla bekleme
UZUN_RAPOR_SN = 600          # GPU yuku: STT istek sayisi (saatlik) bu aralikla loglanir
UZUN_KLIP_TAVAN = 4000       # kayitta tutulan en fazla klip (eskiler dusurulur)


# ---------------------------------------------------------------- cihazlar

def cihazlari_listele():
    """(giris_cihazlari, cikis_cihazlari, varsayilanlar) — UI ve teshis icin."""
    import sounddevice as sd
    girisler, cikislar = [], []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            girisler.append((i, d["name"]))
        if d["max_output_channels"] > 0:
            cikislar.append((i, d["name"]))
    try:
        vg, vc = sd.default.device
    except Exception:
        vg = vc = None
    return girisler, cikislar, (vg, vc)


# ---------------------------------------------------------------- yeniden ornekleme (alcak geciren suzgec + secme)

RS_KESIM = 0.45              # alcak geciren kesim: hedef hizin 0.45 kati (16 kHz icin 7.2 kHz); 8 kHz ustu alias olmasin
RS_SIFIR_GECIS = 22          # suzgec yarim uzunlugu: kesim frekansinin bu kadar sifir gecisi (Kaiser penceresi)
RS_BETA = 8.6                # Kaiser beta (~ -80 dB durdurma)
RS_TABLO_TAVAN = 2048        # faz tablosu en fazla bu kadar satir (up); fazlasi her cagrida benzersiz fazlardan hesaplanir


def _scipy_resample_poly():
    """scipy.signal.resample_poly ya da None (scipy kurulu degilse). Kurulum zorunlu degil (pip aynasi)."""
    try:
        from scipy.signal import resample_poly
        return resample_poly
    except Exception:
        return None


class YenidenOrnekleyici:
    """Akan (blok blok) anti-alias'li yeniden ornekleme: hiz -> hedef Hz. np.interp'in dogrusal enterpolasyonu 8 kHz ustu
    enerjiyi altina katlıyordu (alias); burada once Kaiser pencereli sinc alcak geciren (kesim 0.45 * hedef), sonra secme.
    Arka uc: scipy kuruluysa scipy.signal.resample_poly (ayni suzgec katsayilariyla, her blokta gecmis/ileri baglamla;
    kurulumda sentetik tonla numpy arka ucuna karsi dogrulanir, uyusmazsa numpy'a duser), yoksa numpy polifaz sinc.
    isle(x) her cagrida o ana kadar hazir olan cikis orneklerini dondurur. gecikme=True (varsayilan, mikrofon akisi):
    sabit H ornek (~1.5 ms) gecikme eklenir; boylece N girisli ilk blok tam N*hedef/hiz cikis verir (BLOK korunur).
    gecikme=False: zaman hizali (yeniden_ornekle, bitir() ile sonu bosaltilir)."""

    def __init__(self, hiz, hedef=16000, gecikme=True, arka_uc=None):
        hiz, hedef = int(hiz), int(hedef)
        g = math.gcd(hiz, hedef)
        self.up, self.down = hedef // g, hiz // g
        self.hiz, self.hedef = hiz, hedef
        fa = RS_KESIM * min(hiz, hedef) / float(hiz)               # kesim, girisin ornek basina cevrimi
        self._fa = fa
        self.H = int(math.ceil(RS_SIFIR_GECIS / (2.0 * fa)))      # suzgec yarim uzunlugu (giris ornegi)
        self._d = self.H if gecikme else 0                         # basa eklenen sifir ornek (sabit gecikme)
        self._tampon = np.zeros(self._d, dtype=np.float32)
        self._b0 = 0                                               # tampon[0]'in mutlak indeksi (down'un kati)
        self._sonraki = 0                                          # uretilecek ilk cikis indeksi
        self._tablo = None
        self._poly = None
        if arka_uc != "numpy" and self.up != self.down:
            rp = _scipy_resample_poly()
            if rp is not None:
                self._poly = rp
                if not self._dogrula():
                    self._poly = None

    @property
    def arka_uc(self):
        return "scipy" if self._poly is not None else "numpy"

    # --- suzgec ---
    def _pencere(self, d):
        """Surekli sinc cekirdegi k(d) (d: giris ornegi cinsinden uzaklik), Kaiser pencereli; integrali ~1."""
        w = np.i0(RS_BETA * np.sqrt(np.clip(1.0 - (d / self.H) ** 2, 0.0, 1.0))) / np.i0(RS_BETA)
        return 2.0 * self._fa * np.sinc(2.0 * self._fa * d) * w * (np.abs(d) < self.H)

    def _agirliklar(self, faz):
        """faz: dizi, 0..up-1 -> (len, 2H) agirlik matrisi; her satir birim DC kazanc."""
        ofset = np.arange(-self.H + 1, self.H + 1, dtype=np.float64)
        if self.up <= RS_TABLO_TAVAN:
            if self._tablo is None:
                self._tablo = self._satirlar(np.arange(self.up), ofset)
            return self._tablo[faz]
        benz, ters = np.unique(faz, return_inverse=True)
        return self._satirlar(benz, ofset)[ters]

    def _satirlar(self, fazlar, ofset):
        d = ofset[None, :] - (fazlar[:, None] / float(self.up))
        k = self._pencere(d)
        return k / k.sum(axis=1, keepdims=True)

    def _poly_suzgec(self):
        """resample_poly'ye verilecek yukseltilmis hizdaki katsayilar (toplam 1; scipy up ile carpar)."""
        m = np.arange(-self.H * self.up, self.H * self.up + 1, dtype=np.float64)
        k = self._pencere(m / float(self.up))
        return k / k.sum()

    # --- cikis hesabi: [n0, n1) cikis indeksleri ---
    def _numpy_hesapla(self, n0, n1):
        j = np.arange(n0, n1, dtype=np.int64)
        pay = j * self.down
        i0 = pay // self.up
        w = self._agirliklar(pay % self.up)
        idx = i0[:, None] + np.arange(-self.H + 1, self.H + 1, dtype=np.int64)[None, :] - self._b0
        gecerli = (idx >= 0) & (idx < len(self._tampon))
        x = self._tampon[np.clip(idx, 0, max(0, len(self._tampon) - 1))]
        return np.sum(np.where(gecerli, x, 0.0) * w, axis=1).astype(np.float32)

    def _poly_hesapla(self, n0, n1):
        # segment bas indeksi down'un kati olmali (cikis izgarasi mutlak izgarayla ayni hizada kalsin)
        bas = max(self._b0, ((n0 * self.down // self.up - self.H - 1) // self.down) * self.down)
        seg = self._tampon[bas - self._b0:]
        y = self._poly(seg.astype(np.float64), self.up, self.down, window=self._poly_suzgec())
        o = bas // self.down * self.up
        return np.asarray(y[n0 - o:n1 - o], dtype=np.float32)

    def _hesapla(self, n0, n1):
        return self._poly_hesapla(n0, n1) if self._poly is not None else self._numpy_hesapla(n0, n1)

    def _dogrula(self):
        """scipy yolunu sentetik tonla numpy yoluna karsi sina: kucuk bir kayma/olcek hatasi uretimde sessizce bozmasin."""
        try:
            n = 6 * self.H * self.down // self.up + self.down * 8
            n = max(n, 4 * self.H + 8)
            t = np.arange(n, dtype=np.float64)
            x = (0.5 * np.sin(2 * np.pi * 0.01 * t) + 0.2 * np.sin(2 * np.pi * 0.037 * t)).astype(np.float32)
            a = YenidenOrnekleyici(self.hiz, self.hedef, gecikme=False, arka_uc="numpy")
            b = YenidenOrnekleyici(self.hiz, self.hedef, gecikme=False, arka_uc="numpy")
            b._poly = self._poly
            ya, yb = a.isle(x), b.isle(x)
            return len(ya) == len(yb) and len(ya) > 0 and float(np.max(np.abs(ya - yb))) < 5e-3
        except Exception:
            return False

    def isle(self, x):
        """Girdi blogunu ekler; hazir cikis orneklerini float32 dizi olarak dondurur (bos olabilir)."""
        x = np.asarray(x, dtype=np.float32).reshape(-1)
        if self.up == self.down:
            return x.copy()
        self._tampon = np.concatenate([self._tampon, x])
        toplam = self._b0 + len(self._tampon)
        n_son = ((toplam - 1 - self.H) * self.up) // self.down      # son hesaplanabilir cikis (ileri baglam H ornek)
        if n_son < self._sonraki:
            return np.zeros(0, dtype=np.float32)
        cikti = self._hesapla(self._sonraki, n_son + 1)
        self._sonraki = n_son + 1
        gerek = max(0, (self._sonraki * self.down) // self.up - self.H - 1)   # sonraki cikis icin gereken ilk giris
        yeni_b0 = (gerek // self.down) * self.down
        if yeni_b0 > self._b0:
            self._tampon = self._tampon[yeni_b0 - self._b0:]
            self._b0 = yeni_b0
        return cikti

    def bitir(self):
        """Akis sonu: kalan cikis orneklerini (ileri baglam yerine sifir) dondurur."""
        return self.isle(np.zeros(self.H + self.down, dtype=np.float32))


def yeniden_ornekle(x, hiz, hedef=16000):
    """Tek seferde (zaman hizali, gecikmesiz) anti-alias'li yeniden ornekleme: x (hiz Hz) -> hedef Hz, float32.
    Uzunluk = ceil(len(x) * hedef / hiz). Mikrofon akisi icin YenidenOrnekleyici kullanilir (ayni suzgec)."""
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    if int(hiz) == int(hedef):
        return x.copy()
    r = YenidenOrnekleyici(hiz, hedef, gecikme=False)
    y = np.concatenate([r.isle(x), r.bitir()])
    n = int(math.ceil(len(x) * int(hedef) / float(hiz)))
    return y[:n]


# ---------------------------------------------------------------- halka tampon

class HalkaTampon:
    """Bir akisin son `sn` saniyesi (float16). Ornek numaralari akisin mutlak sayacidir (toplam_ornek):
    al(bas, bit) o araligi float32 dondurur; aralik pencere disindaysa (cok eski ya da henuz gelmemis) None."""

    def __init__(self, sn=HALKA_SN):
        self.maks = int(sn * ORNEK)
        self._parcalar = collections.deque()          # (bas_ornek, float16 dizi)
        self._bas = 0
        self._bit = 0
        self._kilit = threading.Lock()

    def ekle(self, bas_ornek, blok):
        a = np.asarray(blok, dtype=np.float16)
        with self._kilit:
            if self._parcalar and bas_ornek != self._bit:        # kopukluk: eski veri artik bitisik degil
                self._parcalar.clear()
            self._parcalar.append((bas_ornek, a))
            self._bit = bas_ornek + len(a)
            while len(self._parcalar) > 1 and self._bit - self._parcalar[0][0] > self.maks:
                self._parcalar.popleft()
            self._bas = self._parcalar[0][0]

    @property
    def baslangic(self):
        return self._bas

    @property
    def bitis(self):
        return self._bit

    def al(self, bas, bit):
        bas, bit = int(bas), int(bit)
        if bit <= bas:
            return np.zeros(0, dtype=np.float32)
        with self._kilit:
            if not self._parcalar or bas < self._bas or bit > self._bit:
                return None
            secilen = []
            for b, a in reversed(self._parcalar):               # istenen aralik genelde yakin gecmiste
                if b >= bit:
                    continue
                secilen.append((b, a))
                if b <= bas:
                    break
        secilen.reverse()
        ilk = secilen[0][0]
        birlesik = np.concatenate([a for _, a in secilen]).astype(np.float32)
        return birlesik[bas - ilk:bit - ilk]


def zarf_hesapla(ham):
    """Ses -> 100 ms'lik karelerin RMS listesi (son eksik kare de sayilir)."""
    ham = np.asarray(ham, dtype=np.float32)
    n = int(math.ceil(len(ham) / BLOK))
    return [round(float(np.sqrt(np.mean(np.square(ham[i * BLOK:(i + 1) * BLOK])))), 5) for i in range(n)]


# ---------------------------------------------------------------- pasif kulaklik tespiti

def _yuksek_gecir(x, w=KULAKLIK_YUMUSAK):
    """x - hareketli ortalama(w): yavas bilesen cikarilir."""
    pad = np.pad(x, (w // 2, w // 2), mode="edge")
    return x - np.convolve(pad, np.ones(w) / w, mode="valid")[:len(x)]


class KulaklikTespit:
    """Mikrofon ve loopback RMS zarflarinin (100 ms kare) 30 sn'lik penceredeki korelasyonundan kulaklik/hoparlor ayirir.
    Hoparlorde karsi tarafin sesi mikrofona 0-300 ms gecikmeyle girer: zarflar yuksek korele olur. Kulaklikta bagimsizdir.
      durum: True (kulaklik) | False (hoparlor, eko) | None (bilinmiyor)
    hesapla() yeterli veri yoksa (az ortak sure, karsi tarafta az konusma) ya da korelasyon belirsiz bolgedeyse
    (KULAKLIK_DUSUK..YUKSEK) durum=None doner; izlenen durum degismez (karsi taraf susunca kulaklik degismis sayilmaz).
    Degisimde olay_fn({'tur':'kulaklik','durum','guven'}) cagrilir.
    Ses ornekleri saklanmaz, yalniz kare RMS'leri; iki akisin kare zamanlari bagimsizdir, ortak izgaraya enterpole edilir."""

    def __init__(self, olay_fn=None):
        self._olay_fn = olay_fn
        self._kilit = threading.Lock()
        n = int(KULAKLIK_PENCERE_SN * 10 * 1.5)
        self._kareler = {"loopback": collections.deque(maxlen=n), "mikrofon": collections.deque(maxlen=n)}
        self.durum = None
        self.guven = 0.0
        self.korelasyon = None
        self.sabit = False                       # True: config (cift/tek) belirledi, korelasyon karar vermez
        self._son_deger = None

    def sabitle(self, durum):
        """mikrofon_modu 'cift' (True) / 'tek' (False) ayari: korelasyon yerine sabit karar."""
        self.sabit = True
        self._degistir(durum, 1.0)

    def sifirla(self):
        """Mikrofon cihazi degisti: korelasyon penceresi bosaltilir (eski cihazin zarfi yeni karari bozmasin).
        Izlenen durum korunur; yeni cihazla yeterli veri gelince yeniden degerlendirilir."""
        with self._kilit:
            for d in self._kareler.values():
                d.clear()
            self._son_deger = None
            self.korelasyon = None

    def blok_ekle(self, akis, t_orta, rms):
        """Akis iplerinden: bir blogun orta epoch'u ve RMS'i. Degerlendirme 5 sn'de bir."""
        if akis not in self._kareler or self.sabit:
            return
        with self._kilit:
            self._kareler[akis].append((float(t_orta), float(rms)))
            if self._son_deger is not None and t_orta - self._son_deger < KULAKLIK_DEGERLENDIR_SN:
                return
            if not (self._kareler["loopback"] and self._kareler["mikrofon"]):
                return
            self._son_deger = t_orta
            durum, guven, r = self.hesapla()
        self.korelasyon = r
        if durum is not None:
            self._degistir(durum, guven)

    def _degistir(self, durum, guven):
        self.guven = guven
        if durum == self.durum:
            return
        self.durum = durum
        if self._olay_fn is not None:
            try:
                self._olay_fn({"tur": "kulaklik", "durum": durum, "guven": round(float(guven), 2)})
            except Exception:
                pass

    def hesapla(self, kareler=None):
        """-> (durum, guven, korelasyon). durum: False (hoparlor) | True (kulaklik) | None (veri yetersiz ya da belirsiz).
        kareler: {'loopback': [(t, rms), ...], 'mikrofon': [...]} (testler verir); yoksa izlenen pencere."""
        k = kareler if kareler is not None else {a: list(d) for a, d in self._kareler.items()}
        l, m = k.get("loopback") or [], k.get("mikrofon") or []
        if len(l) < 10 or len(m) < 10:
            return None, 0.0, 0.0
        lt, lr = np.array([x[0] for x in l]), np.array([x[1] for x in l])
        mt, mr = np.array([x[0] for x in m]), np.array([x[1] for x in m])
        t_bit = min(lt[-1], mt[-1])
        t_bas = max(lt[0], mt[0], t_bit - KULAKLIK_PENCERE_SN)
        if t_bit - t_bas < KULAKLIK_MIN_VERI_SN:
            return None, 0.0, 0.0
        izgara = np.arange(t_bas, t_bit, 0.1)
        L = np.interp(izgara, lt, lr)
        M = np.interp(izgara, mt, mr)
        esik = max(SESSIZ_ESIK, 0.25 * float(np.percentile(L, 95)))
        if int(np.sum(L > esik)) < KULAKLIK_MIN_SESLI:
            return None, 0.0, 0.0                 # karsi taraf neredeyse susmus: korelasyon anlamsiz
        # Yavas bilesen (kim ne zaman konusuyor) iki bagimsiz akista da basamak desenidir ve 30 sn'de birkac segmentten ibarettir:
        # tesadufen yuksek korelasyon verir. Eko ise hece duzeyindeki ince yapiyi tasir: yavas bilesen cikarilip ince yapi korele edilir.
        L, M = _yuksek_gecir(L), _yuksek_gecir(M)
        en_iyi = 0.0
        for lag in range(KULAKLIK_LAG_MAX + 1):
            x = L[:len(L) - lag] if lag else L
            y = M[lag:]
            if x.std() < 1e-9 or y.std() < 1e-9:
                continue
            en_iyi = max(en_iyi, float(np.corrcoef(x, y)[0, 1]))
        if en_iyi >= KULAKLIK_YUKSEK:
            return False, min(1.0, en_iyi), en_iyi
        if en_iyi < KULAKLIK_DUSUK:
            return True, max(0.0, min(1.0, 1.0 - en_iyi)), en_iyi
        return None, 0.0, en_iyi


# ---------------------------------------------------------------- yakalama

class AkisYakalayici(threading.Thread):
    """Tek bir ses akisini (loopback ya da mikrofon) klip klip boler, kuyruga koyar.

    Zaman tabani: epoch_capa (akisin sifirinci orneginin epoch'u) + toplam_ornek sayaci. ornek_epoch(n)/epoch_ornek(t)
    capa gecmisini kullanir (yeniden capalamadan onceki orneklerin zamani degismez).

    secici (cihaz.py) verilirse bloklar ondan gelir; cihaz.Gecis isaretinde acik klip kapatilir (klip iki cihazin sesini
    karistirmaz), gurultu tabani sifirlanir, sonraki ilk blokta saat capasi CAPA_GECIS_ESIK_SN ile denetlenir. Ornek sayaci
    kesintisiz surer. Secici calismazsa (cihaz.ArkaYok) eski yola (sounddevice / varsayilan hoparlor) dusulur."""

    def __init__(self, kaynak, kuyruk, loopback=False, cihaz=None, olay=None, sayac=None, blok_fn=None, tasma_fn=None,
                 saat=None, halka_sn=HALKA_SN, klip_fn=None, secici=None, gecis_fn=None):
        super().__init__(daemon=True)
        self.secici = secici                    # cihaz.MikrofonSecici / LoopbackSecici | None (eski yol)
        self._gecis_fn = gecis_fn               # (akis, cihaz.Gecis): cihaz degisti (kulaklik tespiti sifirlanir)
        self._capa_bekle = None                 # Gecis sonrasi ilk blokta capa denetimi: (an | None,)
        self._klip_fn = klip_fn                 # (akis, klip sozlugu): her olusan klip (Faz 3 uzun pencere kaydi)
        self._acik_ornek = None                 # acik (henuz kapanmamis) klibin ilk ornegi | None
        self.kaynak, self.kuyruk, self.loopback, self.cihaz = kaynak, kuyruk, loopback, cihaz
        self._sayac = sayac                     # paylasilan sira sayaci: cikti kuyruga giris sirasinda yayilir
        self._yerel_sayac = itertools.count()   # sayac verilmediyse (testler) klip_no buradan
        self._olay = olay or (lambda t, v: None)
        self._blok_fn = blok_fn                 # (akis, blok_orta_epoch, rms): pasif kulaklik tespiti
        self._tasma_fn = tasma_fn               # kuyruk KUYRUK_TAVAN'i asinca klip(p) -> True: diske alindi
        self._saat = saat or (lambda: saat_mod.simdi())     # gec baglanir (testler saat.simdi'yi degistirir)
        self._dur = threading.Event()
        self.hata = None
        self.toplam_sn = 0.0
        self.toplam_ornek = 0                   # bu akisin 16 kHz'te yakaladigi toplam ornek
        self.epoch_capa = None                  # akisin sifirinci orneginin epoch'u (guncel capa)
        self._capalar = []                      # [(ilk_ornek, epoch_capa)]: yeniden capalama gecmisi
        self._sonraki_capa = CAPA_ARALIK_SN * ORNEK
        self.capa_sayisi = 0
        self.halka = HalkaTampon(halka_sn)
        self.klipler = collections.OrderedDict()    # klip_no -> {ornek_bas, ornek_bit, v_bas, v_bit, kesildi}
        self._klip_kilit = threading.Lock()

    def durdur(self):
        self._dur.set()

    # --- zaman tabani ---
    def ornek_epoch(self, n):
        """Akis ornek numarasi -> epoch saniye."""
        for ob, capa in reversed(self._capalar):
            if n >= ob:
                return capa + n / ORNEK
        return (self._capalar[0][1] if self._capalar else (self.epoch_capa or 0.0)) + n / ORNEK

    def ilerleme(self):
        """Klip bolmesinin kesinlestigi an (epoch): acik klip varsa onun basi, yoksa akisin yakaladigi son ornek. Bu andan
        once yeni klip olusmaz (uzun pencere sessizlik karari bununla verilir). Hic blok gelmediyse None."""
        if not self._capalar:
            return None
        acik = self._acik_ornek
        return self.ornek_epoch(acik if acik is not None else self.toplam_ornek)

    def epoch_ornek(self, t):
        """Epoch saniye -> akis ornek numarasi (float)."""
        for ob, capa in reversed(self._capalar):
            n = (t - capa) * ORNEK
            if n >= ob:
                return n
        return (t - (self._capalar[0][1] if self._capalar else (self.epoch_capa or 0.0))) * ORNEK

    def _capa_kur(self, blok_uzunluk, simdi=None):
        self.epoch_capa = (self._saat() if simdi is None else simdi) - blok_uzunluk / ORNEK       # blok yeni bitti
        self._capalar = [(0, self.epoch_capa)]

    def _capa_kontrol(self, simdi=None, gecis=False, ilk_ornek=None):
        """Her CAPA_ARALIK_SN'de (akis zamani) sayac zamani ile saati karsilastirir; kayma > CAPA_ESIK_SN ise yeniden capalar.
        gecis=True: cihaz gecisinden sonraki ilk blok; aralik beklenmez, esik CAPA_GECIS_ESIK_SN, simdi = blogun varis ani,
        yeni capa ilk_ornek'ten (yeni cihazin ilk blogunun basi) gecerli: o blok da yeni zamanla damgalanir."""
        if not self._capalar:
            return
        if not gecis:
            if self.toplam_ornek < self._sonraki_capa:
                return
            while self._sonraki_capa <= self.toplam_ornek:
                self._sonraki_capa += CAPA_ARALIK_SN * ORNEK
            saat_mod.adim_denetle()             # duvar saati adimi yalniz loglanir (simdi() etkilenmez)
        simdi = self._saat() if simdi is None else simdi
        fark = simdi - self.ornek_epoch(self.toplam_ornek)
        if abs(fark) <= (CAPA_GECIS_ESIK_SN if gecis else CAPA_ESIK_SN):
            return
        yeni = simdi - self.toplam_ornek / ORNEK
        ob = self.toplam_ornek if ilk_ornek is None else max(int(ilk_ornek), self._capalar[-1][0])
        self._capalar.append((ob, yeni))
        self.epoch_capa = yeni
        self.capa_sayisi += 1
        self._olay("capa", {"akis": self.kaynak, "fark": round(fark, 3), "ornek": ob,
                            "epoch_capa": yeni, "saat": simdi, **({"neden": "gecis"} if gecis else {})})
        self._olay("log", f"  · {self.kaynak} saat çapası yenilendi (fark {fark:+.2f} sn{', cihaz geçişi' if gecis else ''})")

    # --- ses kaynaklari ---
    def _klip_acik(self):
        return self._acik_ornek is not None

    def _secici_bloklari(self):
        """Secicinin bloklari; secici calismazsa (ArkaYok) False dondurur (cagiran eski yola duser)."""
        try:
            yield from self.secici.bloklar(self._dur, klip_acik_fn=self._klip_acik)
            return True
        except cihaz_mod.ArkaYok as e:
            self._olay("log", f"! {self.kaynak} otomatik cihaz seçimi çalışmadı ({e}); Windows varsayılanı kullanılıyor")
            return False

    def _bloklar_loopback(self):
        if self.secici is not None:
            if (yield from self._secici_bloklari()):
                return
        import soundcard as sc
        hoparlor = sc.default_speaker()
        mik = sc.get_microphone(str(hoparlor.name), include_loopback=True)
        with mik.recorder(samplerate=ORNEK, channels=1, blocksize=BLOK) as kayit:
            while not self._dur.is_set():
                yield kayit.record(numframes=BLOK).reshape(-1)

    def _bloklar_mikrofon(self):
        if self.secici is not None:
            if (yield from self._secici_bloklari()):
                return
        yield from self._bloklar_sd()

    def _bloklar_sd(self):
        """Eski yol: sounddevice (config 'mikrofon_cihaz' ya da PortAudio varsayilani; tak-cikar gorulmez)."""
        import sounddevice as sd
        q = queue.Queue()
        # Cihaz kendi hizinda acilir, 16 kHz'e indirilir: WASAPI cihazlari 16 kHz'i reddeder, config'teki
        # "mikrofon_cihaz" boyle bir cihazi gosterse de akis acilabilsin.
        try:
            hiz = int(sd.query_devices(self.cihaz, "input")["default_samplerate"]) or ORNEK
        except Exception:
            hiz = ORNEK
        blok = int(BLOK * hiz / ORNEK)
        # np.interp 8 kHz ustu enerjiyi altina katiyordu (alias): akan alcak geciren + secme. Hesap geri cagirmada degil
        # bu is parcaciginda yapilir (PortAudio geri cagirmasi hafif kalsin).
        rs = YenidenOrnekleyici(hiz, ORNEK) if hiz != ORNEK else None

        def geri(indata, frames, zaman, durum):
            q.put(indata[:, 0].copy())
        with sd.InputStream(samplerate=hiz, channels=1, dtype="float32", blocksize=blok,
                            device=self.cihaz, callback=geri):
            while not self._dur.is_set():
                try:
                    x = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if rs is not None:
                    x = rs.isle(x)
                    if not len(x):
                        continue
                yield x

    # --- parcalama ---
    def run(self):
        tampon, enerji, bayrak = [], [], []                     # bloklar, RMS'leri, sesli mi
        sessiz_sn = 0.0
        parca_ornek = 0                                         # tampon[0]'in akis ornek numarasi
        onyuk, aday = [], []                                    # on-yuk (blok, rms, sesli); acilis adayi bloklar
        taban = SESSIZ_ESIK                                     # uyarlanan gurultu tabani
        self._olay("log", f"  {self.kaynak} akışı açılıyor…")
        try:
            bloklar = self._bloklar_loopback() if self.loopback else self._bloklar_mikrofon()
            for blok in bloklar:
                if isinstance(blok, cihaz_mod.Gecis):
                    # cihaz degisti: acik klip once kapanir (iki cihazin sesi ayni klibe girmez), sayac surer
                    if blok.kapat and tampon:
                        self._gonder(tampon, None, sum(bayrak) / len(tampon), parca_ornek, enerji, bayrak)
                        tampon, enerji, bayrak, sessiz_sn = [], [], [], 0.0
                    if blok.kapat:
                        self._acik_ornek = None
                        onyuk, aday = [], []
                        # yeni cihazin gurultu tabani (secici olctu); SESSIZ_ESIK'ten baslatmak konusma ortasinda
                        # gecildiyse konusmayi taban sanip klibi hic acmayabilirdi
                        taban = min(SESSIZ_ESIK, max(1e-6, blok.taban)) if blok.taban is not None else SESSIZ_ESIK
                    self._capa_bekle = (blok.an,)
                    if self._gecis_fn is not None:
                        try:
                            self._gecis_fn(self.kaynak, blok)
                        except Exception:
                            pass
                    continue
                if blok is None or not len(blok):
                    continue
                sn = len(blok) / ORNEK
                blok_ornek = self.toplam_ornek                  # bu blogun akis ornek numarasi
                gecis_an = None
                if self._capa_bekle is not None:
                    gecis_an, self._capa_bekle = self._capa_bekle[0], None
                    if self.epoch_capa is not None:
                        gecis_an = gecis_an if gecis_an is not None else self._saat()
                if self.epoch_capa is None:
                    self._capa_kur(len(blok), simdi=gecis_an)
                    gecis_an = None
                self.toplam_sn += sn
                self.toplam_ornek += len(blok)
                self.halka.ekle(blok_ornek, blok)
                if gecis_an is not None:
                    self._capa_kontrol(simdi=gecis_an, gecis=True, ilk_ornek=blok_ornek)
                rms = float(np.sqrt(np.mean(np.square(blok))))
                if self._blok_fn is not None:
                    try:
                        self._blok_fn(self.kaynak, self.ornek_epoch(blok_ornek) + sn / 2, rms)
                    except Exception:
                        pass                                    # tespit hatasi yakalamayi durdurmaz
                self._capa_kontrol()
                # gurultu tabani: sessiz bloklarda hizli iner, yavas yukselir
                taban = min(taban * 1.01 + 1e-6, rms) if rms < taban * 2 else taban
                esik = max(SESSIZ_ESIK, taban * GURULTU_KATI)
                sesli = rms >= esik
                if not tampon:
                    onyuk.append((blok, rms, sesli))
                    onyuk = onyuk[-(ONYUK_BLOK + ACILIS_BLOK):]
                    aday = (aday + [blok]) if sesli else []
                    if len(aday) < ACILIS_BLOK:
                        continue                               # tek klik/tus sesi acmaz
                    tampon = [b for b, _, _ in onyuk]          # on-yuk + acilis bloklari
                    enerji = [r for _, r, _ in onyuk]
                    bayrak = [False] * (len(onyuk) - ACILIS_BLOK) + [True] * ACILIS_BLOK
                    aday, onyuk = [], []
                    parca_ornek = self.toplam_ornek - sum(len(b) for b in tampon)
                    self._acik_ornek = parca_ornek
                    sessiz_sn = 0.0
                    continue
                tampon.append(blok)
                enerji.append(rms)
                bayrak.append(sesli)
                sessiz_sn = 0.0 if sesli else sessiz_sn + sn
                uzunluk = sum(len(b) for b in tampon) / ORNEK
                if ((uzunluk >= MIN_SN and sessiz_sn >= CUMLE_SESSIZLIK)          # cumle bitti
                        or (uzunluk >= TAVAN_SN and sessiz_sn >= NEFES_SESSIZLIK)):  # uzun konusma, nefes payi
                    self._gonder(tampon, None, sum(bayrak) / len(tampon), parca_ornek, enerji, bayrak)
                    tampon, enerji, bayrak, sessiz_sn = [], [], [], 0.0
                    self._acik_ornek = None
                elif uzunluk >= ZORLA_SN:
                    # Sessizlik hic gelmedi. Tam ZORLA_SN'de kesmek kelimeyi ortadan boler, iki parca da
                    # Whisper'da bozuk cikar: son 1.5 sn'nin en sessiz blogunda bol, kalani sonraki parcaya.
                    bol = bolme_noktasi(enerji)
                    self._gonder(tampon[:bol], None, sum(bayrak[:bol]) / bol, parca_ornek, enerji[:bol], bayrak[:bol])
                    parca_ornek += sum(len(b) for b in tampon[:bol])
                    tampon, enerji, bayrak = tampon[bol:], enerji[bol:], bayrak[bol:]
                    self._acik_ornek = parca_ornek
                    sessiz_sn = 0.0
            if tampon:
                self._gonder(tampon, None, sum(bayrak) / len(tampon), parca_ornek, enerji, bayrak)
            self._acik_ornek = None
        except Exception as e:
            self.hata = e
            ek = ""
            if self.loopback:
                ek = " (soundcard paketi kurulu mu? pip install soundcard)"
            self._olay("log", f"✖ {self.kaynak} akışı durdu: {e!r}{ek}")

    def _gonder(self, tampon, basla=None, konusma_orani=1.0, ornek_bas=None, enerji=None, bayrak=None):
        """Klibi kuyruga koyar. basla: eski imza (kullanilmaz; zaman sayactan gelir). enerji/bayrak: blok RMS'leri ve
        sesli bayraklari — bas/bit ilk/son SESLI bloktan, zarf bloklarin RMS'inden turetilir."""
        ham = np.concatenate(tampon)
        if len(ham) / ORNEK < MIN_SN:                           # cok kisa parcayi atla
            return
        if konusma_orani < KONUSMA_ORANI:                        # cogu sessizlik/gurultu: Whisper uydurur
            return
        if float(np.sqrt(np.mean(np.square(ham)))) < SESSIZ_ESIK:
            return
        birikmis = self.kuyruk.qsize()
        if birikmis >= KUYRUK_TAVAN and self._tasma_fn is None:
            self._olay("stt_gecikme", {"kuyruk": birikmis, "atlandi": True})
            return                                              # STT fiilen yok: parcayi dusur
        if birikmis and birikmis % KUYRUK_UYARI == 0 and birikmis < KUYRUK_TAVAN:
            # Eskiden 12'de parca atiliyordu: STT kisa bir sure yavasladiginda konusma kayboluyordu.
            self._olay("stt_gecikme", {"kuyruk": birikmis, "atlandi": False})
        p = self._klip_olustur(tampon, ham, ornek_bas, enerji, bayrak)
        if birikmis >= KUYRUK_TAVAN:
            kaydedildi = bool(self._tasma_fn(p))
            self._olay("stt_gecikme", {"kuyruk": birikmis, "atlandi": not kaydedildi})
            return
        self.kuyruk.put(p)

    def _klip_olustur(self, tampon, ham, ornek_bas, enerji, bayrak):
        """Klip sozlugu: zaman damgalari ornek sayacindan, halka tamponda kayit."""
        n = len(ham)
        if ornek_bas is None:
            ornek_bas = max(0, self.toplam_ornek - n)
        if self.epoch_capa is None:                              # hic blok gelmedi (elle uretilmis klip)
            self._capa_kur(0)
            self.epoch_capa = self._capalar[0][1] - n / ORNEK
            self._capalar = [(0, self.epoch_capa)]
        # sesli aralik: ilk sesli blogun basi .. son sesli blogun sonu (on-yuk ve sondaki sessizlik disarida)
        j0, j1 = 0, len(tampon) - 1
        if bayrak is not None and any(bayrak):
            j0 = bayrak.index(True)
            j1 = len(bayrak) - 1 - bayrak[::-1].index(True)
        uz = [len(b) for b in tampon]
        v_bas = ornek_bas + sum(uz[:j0])
        v_bit = ornek_bas + sum(uz[:j1 + 1])
        ses_ = ham[v_bas - ornek_bas:v_bit - ornek_bas]
        rms = float(np.sqrt(np.mean(np.square(ses_)))) if len(ses_) else 0.0
        if enerji is not None and len(enerji) == len(tampon):
            zarf = [round(float(r), 5) for r in enerji[j0:j1 + 1]]
        else:
            zarf = zarf_hesapla(ses_)
        bas, bit = self.ornek_epoch(v_bas), self.ornek_epoch(v_bit)
        no = next(self._sayac) if self._sayac is not None else None
        klip_no = (no if no is not None else next(self._yerel_sayac)) + 1
        p = {"kaynak": self.kaynak, "basla": saat_mod.yerel(bas), "bitis": saat_mod.yerel(bit),
             "ses": normalize_et(ham), "no": no, "klip_no": klip_no,
             "ornek_bas": ornek_bas, "ornek_bit": ornek_bas + n, "epoch_capa": self.epoch_capa,
             "bas_epoch": bas, "bit_epoch": bit, "v_ornek_bas": v_bas, "v_ornek_bit": v_bit,
             "zarf": zarf, "rms": rms, "ust_klip": None}
        self._kaydet(p)
        return p

    def _kaydet(self, p):
        with self._klip_kilit:
            self.klipler[p["klip_no"]] = {"ornek_bas": p["ornek_bas"], "ornek_bit": p["ornek_bit"],
                                          "v_bas": p["v_ornek_bas"], "v_bit": p["v_ornek_bit"], "kesildi": False}
            esik = self.halka.baslangic
            while self.klipler and next(iter(self.klipler.values()))["ornek_bit"] < esik:
                self.klipler.popitem(last=False)                # halkadan cikmis klip artik kesilemez
        if self._klip_fn is not None:
            try:
                self._klip_fn(self.kaynak, p)
            except Exception:
                pass                                            # uzun pencere kaydi yakalamayi durdurmaz

    # --- yeniden kesme ---
    def kes(self, klip_no, an):
        """Klibi an +- KES_ARALIK_SN icindeki en dusuk enerjili 100 ms blokta ikiye boler; iki parcayi (yeni klip_no'larla,
        ust_klip=klip_no) STT kuyruguna koyar. Klip bilinmiyor/halkadan cikmis/zaten kesilmis, bir parca
        KES_MIN_PARCA_SN'den kisa ya da blok sessiz degilse (kesim_sessiz_mi) False."""
        with self._klip_kilit:
            m = self.klipler.get(klip_no)
            if m is None or m["kesildi"]:
                return False
            m = dict(m)
        if not self._capalar:
            return False
        s_an = self.epoch_ornek(an)
        v0, v1 = m["v_bas"], m["v_bit"]
        lo, hi = max(v0, int(s_an - KES_ARALIK_SN * ORNEK)), min(v1, int(s_an + KES_ARALIK_SN * ORNEK))
        k0 = int(math.ceil((lo - v0) / BLOK))
        k1 = int((hi - v0) // BLOK)                              # kare k: [v0 + k*BLOK, v0 + (k+1)*BLOK)
        if k1 <= k0:
            return False
        ham = self.halka.al(v0, v1)
        if ham is None:
            return False
        adaylar = []
        for k in range(k0, k1):
            kare = ham[k * BLOK:(k + 1) * BLOK]
            if not len(kare):
                continue
            merkez = v0 + k * BLOK + BLOK // 2
            adaylar.append((round(float(np.sqrt(np.mean(np.square(kare)))), 6), abs(merkez - s_an), merkez))
        if not adaylar:
            return False
        en = min(adaylar)
        kes_ornek = en[2]
        if (kes_ornek - v0) / ORNEK < KES_MIN_PARCA_SN or (v1 - kes_ornek) / ORNEK < KES_MIN_PARCA_SN:
            return False
        tum = [float(np.sqrt(np.mean(np.square(ham[k * BLOK:(k + 1) * BLOK]))))
               for k in range(len(ham) // BLOK) if len(ham[k * BLOK:(k + 1) * BLOK])]
        if not kesim_sessiz_mi(en[0], tum):
            return False                                          # sessiz blok yok: "A -> B" kalir
        a_ses = self.halka.al(m["ornek_bas"], kes_ornek)
        b_ses = self.halka.al(kes_ornek, m["ornek_bit"])
        if a_ses is None or b_ses is None:
            return False
        with self._klip_kilit:
            m2 = self.klipler.get(klip_no)
            if m2 is None or m2["kesildi"]:
                return False
            m2["kesildi"] = True
        parcalar = []
        for ses_, ob, oe, vb, ve, ham_v in ((a_ses, m["ornek_bas"], kes_ornek, v0, kes_ornek, ham[:kes_ornek - v0]),
                                           (b_ses, kes_ornek, m["ornek_bit"], kes_ornek, v1, ham[kes_ornek - v0:])):
            no = next(self._sayac) if self._sayac is not None else None
            yeni_no = (no if no is not None else next(self._yerel_sayac)) + 1
            bas, bit = self.ornek_epoch(vb), self.ornek_epoch(ve)
            p = {"kaynak": self.kaynak, "basla": saat_mod.yerel(bas),
                 "bitis": saat_mod.yerel(bit), "ses": normalize_et(ses_), "no": no, "klip_no": yeni_no,
                 "ornek_bas": ob, "ornek_bit": oe, "epoch_capa": self.epoch_capa,
                 "bas_epoch": bas, "bit_epoch": bit, "v_ornek_bas": vb, "v_ornek_bit": ve,
                 "zarf": zarf_hesapla(ham_v), "rms": float(np.sqrt(np.mean(np.square(ham_v)))) if len(ham_v) else 0.0,
                 "ust_klip": klip_no}
            self._kaydet(p)
            parcalar.append(p)
        for p in parcalar:
            self.kuyruk.put(p)
        return True


def kesim_sessiz_mi(blok_rms, tum_rms, kontrast=None):
    """Yeniden kesme blogu gercekten sessiz mi: blok RMS'i <= kontrast * klibin medyan 100 ms blok RMS'i.
    Degilse en dusuk enerjili blok konusmanin icinde (ust uste binme / araya girme): kesim baskasinin sozunu
    parcaya tasir, kesilmez."""
    k = KES_KONTRAST if kontrast is None else kontrast
    xs = sorted(float(x) for x in tum_rms)
    if not xs:
        return False
    n = len(xs)
    medyan = xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0
    return float(blok_rms) <= k * medyan


def normalize_et(ses):
    """A5: kisik parca (tepe < NORMAL_TEPE; uzak mikrofon, kisik Teams sesi) tepe ~NORMAL_HEDEF olacak
    sekilde yukseltilir (kazanc en fazla NORMAL_KAZANC), [-1, 1]'e kirpilir. Yuksek ses degismez."""
    tepe = float(np.max(np.abs(ses))) if len(ses) else 0.0
    if tepe <= 0 or tepe >= NORMAL_TEPE:
        return ses
    kazanc = min(NORMAL_KAZANC, NORMAL_HEDEF / tepe)
    return np.clip(ses * kazanc, -1.0, 1.0).astype(np.float32)


def bolme_noktasi(enerji, pencere=BOLME_PENCERE):
    """Zorla kesimde bolunecek blok indeksi: son `pencere` blok icindeki en sessiz blok.
    Donen i icin tampon[:i] gonderilir, tampon[i:] sonraki parcaya kalir (1 <= i < len)."""
    n = len(enerji)
    if n < 2:
        return n
    bas = max(1, n - pencere)
    return min(range(bas, n), key=lambda k: enerji[k])


# ---------------------------------------------------------------- olcum kaydi (Faz 0, opt-in)

class OlcumKaydi:
    """config 'olcum_kaydi' acikken STT'ye giden her klibi yazar (yerel, repo disi; toplantilar/ .gitignore'da):
      <klasor>/olcum/klipler/NNNN.wav   16 kHz mono int16 (STT'ye giden, normalize edilmis hali)
      <klasor>/olcum/klipler.jsonl      {no, klip_no, akis, bas, bit, ornek_bas, ornek_bit, sure, ts, rms, zarf, tsler, raw,
                                         segmentler, stt_sure[, stt_hata][, ust_klip]}
      <klasor>/olcum/olaylar.jsonl      {zaman, tur, ...}: saat capasi yenileme, STT kesintisi
    bas/bit: klibin epoch saniyesi — SESLI aralik (ilk sesli blok basi .. son sesli blok sonu) varsa o, yoksa
    akis sayaci + akis capasi (tum ses); ornek_*: o akisin 16 kHz kumulatif ornek sayaci (tum ses, on-yuk dahil).
    wav klip kuyruktan alininca, jsonl satiri STT sonucu gelince (raw ile birlikte) yazilir.
    Is parcacigi guvenli; IO hatasinda bir kez bildirir ve kendini kapatir, yakalama dongusune istisna sizmaz."""

    def __init__(self, klasor, olay=None):
        self.kok = os.path.join(klasor, "olcum")
        self.klip_klasor = os.path.join(self.kok, "klipler")
        self.jsonl = os.path.join(self.kok, "klipler.jsonl")
        self.olaylar_jsonl = os.path.join(self.kok, "olaylar.jsonl")
        self.acik = True
        self._olay = olay or (lambda t, v: None)
        self._kilit = threading.Lock()
        self._bekleyen = {}                       # no -> yarim kayit (wav yazildi, sonuc bekleniyor)
        self._ic_sayac = itertools.count()
        self._ofset = 0                           # ayni klasore ikinci kayitta numaralar cakismasin
        try:
            os.makedirs(self.klip_klasor, exist_ok=True)
            for ad in os.listdir(self.klip_klasor):
                if ad.endswith(".wav") and ad[:-4].isdigit():
                    self._ofset = max(self._ofset, int(ad[:-4]))
        except Exception as e:
            self._kapat(e)

    def _kapat(self, e):
        if self.acik:
            self.acik = False
            try:
                self._olay("log", f"✖ ölçüm kaydı kapandı (disk hatası): {e!r}")
            except Exception:
                pass

    def olay_yaz(self, tur, veri=None):
        """olaylar.jsonl'e {zaman, tur, ...veri} ekler (saat capasi yenileme, STT kesintisi). Asla istisna firlatmaz."""
        if not self.acik:
            return
        try:
            satir = {"zaman": round(saat_mod.simdi(), 3), "tur": tur}
            if isinstance(veri, dict):
                satir.update({k: v for k, v in veri.items() if k not in satir})
            with self._kilit:
                with open(self.olaylar_jsonl, "a", encoding="utf-8") as f:
                    f.write(json.dumps(satir, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            self._kapat(e)

    def klip_yaz(self, p):
        """Kuyruktan alinan klibin wav'ini yazar; p['_olcum_no'] atanir. Asla istisna firlatmaz."""
        if not self.acik:
            return
        try:
            sira = p.get("no")
            no = self._ofset + 1 + (sira if sira is not None else next(self._ic_sayac))
            with open(os.path.join(self.klip_klasor, f"{no:04d}.wav"), "wb") as f:
                f.write(wav_bayt(p["ses"]))
            sure = len(p["ses"]) / ORNEK
            ob, capa = p.get("ornek_bas"), p.get("epoch_capa")
            if p.get("bas_epoch") is not None and p.get("bit_epoch") is not None:
                bas, bit = p["bas_epoch"], p["bit_epoch"]
            elif ob is not None and capa is not None:
                bas, bit = capa + ob / ORNEK, capa + p["ornek_bit"] / ORNEK
            else:                                  # sayac yok (ornegin elle uretilmis klip): varis anlarindan
                bit = saat_mod.epoch(p["bitis"]) if isinstance(p.get("bitis"), dt.datetime) else saat_mod.simdi()
                bas = saat_mod.epoch(p["basla"]) if isinstance(p.get("basla"), dt.datetime) else bit - sure
            kayit = {"no": no, "akis": p["kaynak"], "bas": round(bas, 3), "bit": round(bit, 3),
                     "ornek_bas": ob, "ornek_bit": p.get("ornek_bit"), "sure": round(sure, 2),
                     "ts": p["basla"].strftime("%H:%M:%S") if isinstance(p.get("basla"), dt.datetime) else None}
            if p.get("klip_no") is not None:
                kayit["klip_no"] = p["klip_no"]
            if p.get("zarf") is not None:
                kayit["zarf"] = p["zarf"]
            if p.get("rms") is not None:
                kayit["rms"] = round(float(p["rms"]), 5)
            if p.get("ust_klip") is not None:
                kayit["ust_klip"] = p["ust_klip"]
            with self._kilit:
                self._bekleyen[no] = kayit
            p["_olcum_no"] = no
        except Exception as e:
            self._kapat(e)

    def sonuc(self, p, ham, stt_sure, tsler=None, segmentler=None, hata=None):
        """STT sonucu geldi (ya da hata): klip satirini tamamlayip jsonl'e ekler. Asla istisna firlatmaz."""
        no = p.get("_olcum_no")
        if no is None or not self.acik:
            return
        try:
            with self._kilit:
                kayit = self._bekleyen.pop(no, None)
                if kayit is None:
                    return
                if tsler:
                    kayit["ts"] = tsler[0]            # satirin tasiyacagi HH:MM:SS (ilk cumle)
                kayit.update(tsler=list(tsler or []), raw=ham, stt_sure=stt_sure)
                if segmentler:
                    kayit["segmentler"] = segmentler
                if hata:
                    kayit["stt_hata"] = hata
                with open(self.jsonl, "a", encoding="utf-8") as f:
                    f.write(json.dumps(kayit, ensure_ascii=False) + "\n")
        except Exception as e:
            self._kapat(e)


# ---------------------------------------------------------------- Faz 3: uzun pencere

class UzunPencere:
    """Uzun pencere planlayici + cozucu (MIMARI_v3 §4 "Uzun pencere"; is parcacigindan bagimsiz cekirdek: SesServisi
    iscisi de, sanal saatli duzenek / replay de ayni metotlari cagirir).

    Girdi: klip_kaydet (klip olustu: akis, klip_no, bas/bit epoch, rms), metin_kaydet (klibin yayilan kisa metni, "" =
    satiri yok), ilerle (akisin klip bolmesinin kesinlestigi an). planla(): akis basina, henuz pencereye girmemis klipleri
    zaman sirasiyla bir pencerede toplar; pencere sesi (ilk sahip klipten UZUN_ORTUSME_SN once .. son sahip klip +
    UZUN_KUYRUK_SN) UZUN_MIN_SN..UZUN_MAKS_SN araliginda en genis klip arasi sessizlikte biter; UZUN_SESSIZLIK_SN'lik
    sessizlik pencereyi erken kapatir. Tek klipli pencere STT'ye gitmez (kazanc yok, GPU yuku). coz(p): halka tampondan
    (ses_fn) pencere sesi STT'ye; prompt = ayni akisin ONCEKI penceresinin son cumlesi ve yalniz pencerenin sesli RMS'i
    esikler.ipucu_rms uzerindeyse (prompt sizintisi korumasi); halusinasyon puani supheliyse promptsuz bir kez daha, yine
    supheliyse pencere atlanir. hizala(p): kisa metinler gelince uzun.pencere_hizala, sonuc olayi yay_fn'e:
      {"tur": "uzun", "akis", "pencere_no", "bas", "bit", "kapsam", "klipler": [{"klip_no", "text"|None, "belirsiz", "oran"}],
       "oran", "neden": None | "tek_klip" | "ses_yok" | "stt_hata" | "halusinasyon" | "metin_yok" | "hizalama"}
    kapsam: bu akista sonucu kesinlesen son an (Motor parca kapanisinda bunu bekler). Pencereler akis basina sirayla yayilir.
    Konusmaci atamasina dokunulmaz: yalniz klip_no -> metin."""

    def __init__(self, coz_fn, ses_fn, yay_fn, puan_fn=None, metin_fn=None, esik_fn=None, ipucu_acik=True, olay=None,
                 saat=None, min_sn=UZUN_MIN_SN, maks_sn=UZUN_MAKS_SN, ortusme_sn=UZUN_ORTUSME_SN,
                 kuyruk_sn=UZUN_KUYRUK_SN, sessizlik_sn=UZUN_SESSIZLIK_SN, hizalama_esik=uzun_mod.ESIK):
        self._coz_fn = coz_fn                    # (ses, ipucu) -> STT segmentleri
        self._ses_fn = ses_fn                    # (akis, bas, bit) -> pencere sesi | None (halkadan cikmis)
        self._yay_fn = yay_fn                    # sonuc olayi -> Motor (SesServisi sirasiyla)
        self._puan_fn = puan_fn                  # (p, segmentler, ipucu) -> (puan, metin)
        self._metin_fn = metin_fn or (lambda seg: " ".join((s.get("text") or "").strip() for s in seg).strip())
        self._esik_fn = esik_fn or (lambda ad: {"ipucu_rms": IPUCU_RMS, "hal_supheli": HAL_ESIKLER["hal_supheli"]}[ad])
        self.ipucu_acik = bool(ipucu_acik)
        self._olay = olay or (lambda t, v: None)
        self._saat = saat or (lambda: saat_mod.simdi())
        self.min_sn, self.maks_sn, self.ortusme_sn = float(min_sn), float(maks_sn), float(ortusme_sn)
        self.kuyruk_sn, self.sessizlik_sn, self.esik = float(kuyruk_sn), float(sessizlik_sn), float(hizalama_esik)
        self._kilit = threading.RLock()
        self._klip = {}                          # klip_no -> {"klip_no","akis","bas","bit","rms","metin","pencere","baglam"}
        self._akis = {}                          # akis -> {"klipler": [...], "ilerleme", "sinir", "onceki_cumle"}
        self._sira = collections.deque()         # planlanmis pencereler (plan sirasi = akis icinde zaman sirasi)
        self._no = itertools.count(1)
        self.istek = 0                           # uzun pencere STT istegi (promptsuz tekrar dahil): GPU yuku
        self.pencere = 0                         # planlanan pencere
        self.cozulen = 0                         # STT'ye giden pencere
        self.atlanan = collections.Counter()     # neden -> pencere
        self.degisen_klip = 0                    # metni uzun pencereyle degisen klip
        self.ilk_an = None

    # --- girdi ---
    def _ak(self, akis):
        ak = self._akis.get(akis)
        if ak is None:
            ak = self._akis[akis] = {"klipler": [], "ilerleme": None, "sinir": None, "onceki_cumle": ""}
        return ak

    def klip_kaydet(self, akis, klip_no, bas, bit, rms=None, ust=None):
        """Klip olustu (akis ipi / kesinti kuyrugu / yeniden kesme parcasi). ust: yeniden kesilen klibin parcasi: ust klip
        henuz pencereye girmediyse yerini parcalar alir; girdiyse parca yalniz baglam olur (metni kisa kalir)."""
        if klip_no is None or bas is None:
            return
        with self._kilit:
            if self.ilk_an is None:
                self.ilk_an = self._saat()
            ak = self._ak(akis)
            bit = max(float(bit if bit is not None else bas), float(bas))
            k = {"klip_no": klip_no, "akis": akis, "bas": float(bas), "bit": bit,
                 "rms": float(rms) if isinstance(rms, (int, float)) and not isinstance(rms, bool) else None,
                 "metin": None, "uzun_metin": None, "pencere": None,
                 "baglam": ak["sinir"] is not None and float(bas) < ak["sinir"]}     # kapsanmis bolge: sahip olunmaz
            if ust is not None:
                u = self._klip.get(ust)
                if u is not None and u["pencere"] is None and not u["baglam"]:
                    u["baglam"] = True                    # parcalari sahip olur; ust klip satiri parcalarla degisir
            self._klip[klip_no] = k
            liste = ak["klipler"]
            i = len(liste)
            while i > 0 and liste[i - 1]["bas"] > k["bas"]:
                i -= 1
            liste.insert(i, k)
            self._buda()

    def metin_kaydet(self, klip_no, metin):
        """Klibin yayilan kisa metni ("" = metinsiz / satiri yok)."""
        with self._kilit:
            k = self._klip.get(klip_no)
            if k is not None:
                k["metin"] = (metin or "").strip()

    def ilerle(self, akis, an):
        """akis icin klip bolmesi `an`a kadar kesin (bu andan once yeni klip olusmaz). float('inf'): akis bitti."""
        if an is None:
            return
        with self._kilit:
            ak = self._ak(akis)
            ak["ilerleme"] = an if ak["ilerleme"] is None else max(ak["ilerleme"], an)

    def bitir_isaretle(self):
        """Yakalama bitti: tum akislar kapanir, kalan klipler son pencerelere girer."""
        with self._kilit:
            for akis in list(self._akis):
                self.ilerle(akis, float("inf"))

    def _buda(self):
        if len(self._klip) <= UZUN_KLIP_TAVAN:
            return
        for ak in self._akis.values():
            sinir = ak["sinir"]
            if sinir is None:
                continue
            tut = []
            for k in ak["klipler"]:
                if k["bit"] < sinir - 120 and (k["pencere"] is not None or k["baglam"]):
                    self._klip.pop(k["klip_no"], None)
                else:
                    tut.append(k)
            ak["klipler"] = tut

    # --- planlama ---
    def bekleyen(self):
        """Planlanmis ama yayilmamis pencere + pencereye girmemis klip sayisi."""
        with self._kilit:
            acik = sum(1 for ak in self._akis.values() for k in ak["klipler"] if k["pencere"] is None and not k["baglam"])
            return len(self._sira) + acik

    def planla(self):
        """Hazir pencereleri planlar (sira sonuna ekler). Dondurur: yeni pencereler."""
        yeni = []
        with self._kilit:
            for akis in sorted(self._akis):
                while True:
                    p = self._akis_planla(akis)
                    if p is None:
                        break
                    self._sira.append(p)
                    yeni.append(p)
        return yeni

    def _akis_planla(self, akis):
        ak = self._akis[akis]
        adaylar = [k for k in ak["klipler"] if k["pencere"] is None and not k["baglam"]]
        if not adaylar:
            return None
        ilerleme = ak["ilerleme"] if ak["ilerleme"] is not None else -float("inf")
        ws = adaylar[0]["bas"]
        a = ws - self.ortusme_sn

        def uzunluk(k):
            return k["bit"] + self.kuyruk_sn - a

        sinirlar = []                                     # (indeks, ses uzunlugu, ardindaki sessizlik)
        for i, k in enumerate(adaylar):
            if i + 1 < len(adaylar):
                sinirlar.append((i, uzunluk(k), max(0.0, adaylar[i + 1]["bas"] - k["bit"])))
        son = adaylar[-1]
        if ilerleme - son["bit"] >= self.sessizlik_sn:   # son klibin ardindan yeterli sessizlik kesinlesti
            sinirlar.append((len(adaylar) - 1, uzunluk(son), ilerleme - son["bit"]))
        secilen = None
        for i, uz, ses_ in sinirlar:                      # 1) uzun sessizlik: pencere orada biter (kisa da olsa)
            if uz > self.maks_sn:
                break
            if ses_ >= self.sessizlik_sn:
                secilen = i
                break
        # 2) pencere ufku (UZUN_MAKS_SN) asildiysa ya da akis bittiyse artik [min, maks] icine yeni sinir gelemez:
        #    aralikta en genis sessizlikte bol (esitlikte gec olan); aralikta sinir yoksa ufkun altindaki en genis
        kesin = (ilerleme == float("inf") or uzunluk(son) > self.maks_sn
                 or ilerleme + self.kuyruk_sn - a > self.maks_sn)
        if secilen is None and kesin:
            if not sinirlar or sinirlar[-1][0] != len(adaylar) - 1:
                sinirlar.append((len(adaylar) - 1, uzunluk(son), max(0.0, min(ilerleme, son["bit"] + 60) - son["bit"])))
            aralik = [(ses_, i) for i, uz, ses_ in sinirlar if self.min_sn <= uz <= self.maks_sn]
            alt = [(ses_, i) for i, uz, ses_ in sinirlar if uz <= self.maks_sn]
            genis = [(ses_, i) for i, uz, ses_ in sinirlar if self.min_sn / 2 <= uz <= self.maks_sn]
            if aralik:
                secilen = max(aralik)[1]
            elif alt:
                secilen = max(genis or alt)[1]
            else:
                secilen = sinirlar[0][0]
        if secilen is None:
            return None
        sahip = adaylar[:secilen + 1]
        sonraki = adaylar[secilen + 1]["bas"] if secilen + 1 < len(adaylar) else None
        b = sahip[-1]["bit"] + self.kuyruk_sn
        if sonraki is not None:
            b = min(b, sonraki)
        no = next(self._no)
        for k in sahip:
            k["pencere"] = no
        sahip_no = {k["klip_no"] for k in sahip}
        baglam = [k for k in ak["klipler"] if k["klip_no"] not in sahip_no and k["bas"] < ws and k["bit"] > a]
        ak["sinir"] = sahip[-1]["bit"]
        for k in ak["klipler"]:                           # yeni kapsanmis bolgeye dusen bekleyen klip yalniz baglam olur
            if k["pencere"] is None and k["bas"] < ak["sinir"]:
                k["baglam"] = True
        sure = sum(max(0.0, k["bit"] - k["bas"]) for k in sahip)
        rms_ = [(k["rms"], k["bit"] - k["bas"]) for k in sahip if k["rms"] is not None and k["bit"] > k["bas"]]
        rms = math.sqrt(sum(r * r * d for r, d in rms_) / sum(d for _, d in rms_)) if rms_ else None
        self.pencere += 1
        p = {"no": no, "akis": akis, "bas": a, "bit": b, "kapsam": sahip[-1]["bit"],
             "sahip": [k["klip_no"] for k in sahip], "baglam": [k["klip_no"] for k in baglam],
             "konusma_sn": round(sure, 3), "rms": rms, "plan_an": self._saat(), "metin": None, "neden": None,
             "cozuldu": False, "ipucu": ""}
        if len(sahip) < 2:
            p["neden"], p["cozuldu"] = "tek_klip", True
        return p

    # --- cozum ---
    def coz(self, p):
        """Pencere sesini STT'ye gonderir (korumali prompt + halusinasyon puani). p['metin'] | p['neden']."""
        if p.get("cozuldu"):
            return p
        p["cozuldu"] = True
        try:
            ses_ = self._ses_fn(p["akis"], p["bas"], p["bit"])
        except Exception as e:
            self._olay("log", f"! uzun pencere sesi alınamadı: {e!r}")
            ses_ = None
        if ses_ is None:
            p["neden"] = "ses_yok"
            return p
        ak = self._akis.get(p["akis"]) or {}
        ipucu = ""
        rms = p.get("rms")
        if self.ipucu_acik and isinstance(rms, (int, float)) and rms >= self._esik_fn("ipucu_rms"):
            ipucu = (ak.get("onceki_cumle") or "")[:IPUCU_KR_TAVAN]
        try:
            self.istek += 1
            seg = self._coz_fn(ses_, ipucu) if ipucu else self._coz_fn(ses_, "")
            puan = self._puan(p, seg, ipucu)
            if ipucu and puan >= self._esik_fn("hal_supheli"):
                try:
                    self.istek += 1
                    seg2 = self._coz_fn(ses_, "")
                    puan2 = self._puan(p, seg2, "")
                    if puan2 <= puan:
                        seg, puan, ipucu = seg2, puan2, ""
                except Exception as e:
                    self._olay("log", f"! uzun pencere promptsuz tekrar başarısız: {e!r}")
        except Exception as e:
            p["neden"] = "stt_hata"
            self._olay("log", f"! uzun pencere STT hatası (kısa metin korunur): {e!r}")
            return p
        p["ipucu"], p["puan"] = ipucu, round(float(puan), 3)
        if puan >= self._esik_fn("hal_supheli"):
            p["neden"] = "halusinasyon"
            return p
        self.cozulen += 1
        p["metin"] = self._metin_fn(seg)
        return p

    def _puan(self, p, seg, ipucu):
        if self._puan_fn is None:
            return 0.0
        return float(self._puan_fn(p, seg, ipucu)[0])

    def hizala(self, p, zorla=False):
        """Pencere sonucu -> olay (kisa metinler eksikse ve zorla degilse None: sonra tekrar denenir)."""
        with self._kilit:
            ev = {"tur": "uzun", "akis": p["akis"], "pencere_no": p["no"], "bas": round(p["bas"], 3),
                  "bit": round(p["bit"], 3), "kapsam": p["kapsam"], "klipler": [], "oran": None, "neden": p.get("neden")}
            ak = self._ak(p["akis"])
            if ev["neden"] is None:
                sahip = [self._klip.get(n) for n in p["sahip"]]
                baglam = [self._klip.get(n) for n in p["baglam"]]
                eksik = [k for k in sahip + baglam if k is None or k["metin"] is None]
                if eksik and not zorla and self._saat() - p["plan_an"] < UZUN_METIN_BEKLE_SN:
                    return None
                if eksik:
                    ev["neden"] = "metin_yok"
                else:
                    s = uzun_mod.pencere_hizala(
                        p["metin"] or "", [{"klip_no": k["klip_no"], "bas": k["bas"], "bit": k["bit"], "text": k["metin"]}
                                           for k in sahip],
                        [{"klip_no": k["klip_no"], "bas": k["bas"], "bit": k["bit"],
                          "text": k["uzun_metin"] or k["metin"]} for k in baglam],
                        pencere_bas=p["bas"], esik=self.esik)
                    ev["oran"], ev["dikis"] = s["oran"], s["dikis"]
                    for r in s["klipler"]:
                        k = self._klip.get(r["klip_no"])
                        if r["text"] is not None and k is not None:
                            k["uzun_metin"] = r["text"]
                        if r["text"] is not None and r["text"] != (k or {}).get("metin") or r["belirsiz"]:
                            ev["klipler"].append(dict(r))
                    self.degisen_klip += sum(1 for r in ev["klipler"] if r["text"] is not None)
                    if s["oran"] < self.esik:
                        ev["neden"] = "hizalama"
            # sonraki pencerenin prompt'u: bu pencerenin (guvenilir) son cumlesi; supheli / hizalanamayan pencere aktarilmaz
            son = self._klip.get(p["sahip"][-1]) if p.get("sahip") else None
            if ev["neden"] in (None, "tek_klip") and son is not None:
                ak["onceki_cumle"] = uzun_mod.son_cumle(son.get("uzun_metin") or son.get("metin") or "")
            else:
                ak["onceki_cumle"] = ""
            if ev["neden"] is not None:
                self.atlanan[ev["neden"]] += 1
            return ev

    def adim(self, zorla=False, cozucu=True, planla=True):
        """Bir tur: planla, siradaki pencereleri sirayla coz + hizala + yay. Bastaki pencerenin kisa metinleri eksikse
        (zorla degilse) durur. cozucu=False: STT cagrilmaz (cozulmemis pencerede durur; sanal saatli duzenek coz'u
        kendisi zamanlar); zorla ile cozulmemis pencere yine cozulur. planla=False: cagiran planlamistir.
        Dondurur: yayilan pencere sayisi."""
        if planla:
            self.planla()
        n = 0
        while True:
            with self._kilit:
                if not self._sira:
                    return n
                p = self._sira[0]
            if not p.get("cozuldu"):
                if not cozucu and not zorla:
                    return n
                self.coz(p)
            ev = self.hizala(p, zorla=zorla)
            if ev is None:
                return n
            with self._kilit:
                if self._sira and self._sira[0] is p:
                    self._sira.popleft()
            try:
                self._yay_fn(ev)
            except Exception as e:
                self._olay("log", f"✖ uzun pencere sonucu işlenemedi: {e!r}")
            n += 1

    def istatistik(self, kisa_istek=None):
        """GPU yuku: {"pencere", "cozulen", "istek", "saatlik_istek", "kisa_istek", "kisa_saatlik", "sure_sa", "atlanan",
        "degisen_klip"}."""
        sure = max(0.0, self._saat() - self.ilk_an) if self.ilk_an is not None else 0.0
        sa = sure / 3600.0
        d = {"pencere": self.pencere, "cozulen": self.cozulen, "istek": self.istek, "sure_sa": round(sa, 3),
             "saatlik_istek": round(self.istek / sa, 1) if sa > 0 else None, "atlanan": dict(self.atlanan),
             "degisen_klip": self.degisen_klip}
        if kisa_istek is not None:
            d["kisa_istek"] = kisa_istek
            d["kisa_saatlik"] = round(kisa_istek / sa, 1) if sa > 0 else None
        return d


# ---------------------------------------------------------------- servis

class SesServisi:
    """Yakalama + STT'yi yonetir.

    olay_fn(dict) verilirse (Faz 1) her olay sozlugu SIRALI cagrilir (STT cikis sirasi degil, klip sirasi):
      klip    {"tur","klip_no","akis","bas","bit","sure","text","ts","rms","zarf","ust_klip","supheli","hal_puani"[,"atildi"]}
              hal_puani: stt.halusinasyon_puani (0-1); >= esikler.hal_supheli (0.5) -> supheli; >= hal_at (0.9) -> text "" ve
              atildi="halusinasyon" (olay yine gelir: o anda akista ses vardi)
      bosluk  {"tur","akis","bas","bit","neden"}   cozulemeyen klip ('stt_yok': kesinti; 'kuyruk_tasti'; 'stt_hata')
      kulaklik {"tur","durum","guven"}             pasif tespit degisince
    olay_fn yoksa eski yol: satir_fn(ts, konusmaci, metin, kaynak); konusmaci: mikrofon icin 'ben', loopback icin None.

    Whisper prompt'u: ipucu_fn(akis) -> str (terim.prompt baglanacak). Korumalar: config 'stt_ipucu' (varsayilan True); klibin
    sesli RMS'i esikler.ipucu_rms (0.015) altindaysa prompt GITMEZ ve onceki cumle hic gonderilmez; prompt'lu sonuc 'supheli'
    (puan >= hal_supheli) cikarsa klip promptsuz bir kez daha cozulur ve puani dusuk olan tutulur.
    stt_repetition_penalty (config; None: alan gonderilmez). cizelge_fn(akis, bas, bit) -> bool|None puana cizelge kuralini ekler.

    STT kesintisi altyazi moduna dusurmez: yakalama surer, klipler bosluk olur; olcum_kaydi/kesinti_kaydi aciksa diske yazilip
    STT donunce sirayla cozulur. Durum: stt_durumu.

    Faz 3 uzun pencere (uzun_pencere=True; None: config.json 'uzun_pencere', varsayilan kapali): arka plan iscisi halka
    tampondan 20-28 sn'lik pencereleri yeniden cozer (UzunPencere); sonuc olayi {"tur": "uzun", ...} olay_fn'e gider (Motor
    klip metnini degistirir, konusmaciya dokunmaz). Baslarken {"tur": "uzun_durum", "acik": True}. GPU yuku: STT istek
    sayisi (kisa + uzun, saatlik) UZUN_RAPOR_SN'de ve durdururken loglanir. Kapaliyken hicbir yol degismez.

    Cihazlar (cihaz.py; cihaz_otomatik=True, varsayilan): mikrofon acik mikrofonlardan otomatik secilir (mik_cihaz = config
    'mikrofon_cihaz' yalniz elle ayar; sessiz/yoksa otomatige dusulur), loopback tum cikis cihazlarinin karisimidir.
    Olaylar (olay(tip, veri)): 'mikrofon' {"kimlik","ad","otomatik","neden","sessizde"} her secim/geciste ve secili cihaz
    sessize alininca / ses donunce; 'ses_uyari' {"tur":
    'mikrofon'|'loopback', "var", "metin"} (tum mikrofonlar 10 sn tam sessiz / altyazida konusma varken 20 sn loopback
    sesi yok; ses gelince var=False). secili_mikrofon() UI icin. arka: cihaz arka ucu (testler; None: soundcard).
    altyazi_fn() -> uzak altyazi etkinliginin son ani (loopback uyarisi; ses.altyazi_son_fn(motor))."""

    def __init__(self, stt_adres, ipucu_fn=None, mod="otomatik", mik_cihaz=None, olay=None, satir_fn=None,
                 model="whisper", api_key="", proxy=True, verify=None, olcum_klasor=None, olay_fn=None,
                 kesinti_klasor=None, esikler=None, stt_ipucu=True, stt_repetition_penalty=None, cizelge_fn=None,
                 uzun_pencere=None, cihaz_otomatik=True, arka=None, altyazi_fn=None):
        if uzun_pencere is None:                 # config.json 'uzun_pencere' (yalniz true acar)
            try:
                import ayar as ayar_mod
                uzun_pencere = ayar_mod.ham_oku().get("uzun_pencere") is True
            except Exception:
                uzun_pencere = False
        self.uzun_pencere = bool(uzun_pencere)
        self.uzun = None                         # UzunPencere (baslat ya da uzun_ac ile kurulur)
        self._uzun_isci = None
        self._uzun_dur = threading.Event()
        self._uzun_rapor_an = None
        self.kuyruk = queue.Queue()
        self.stt = SttIstemci(stt_adres, model=model, api_key=api_key, proxy=proxy, verify=verify,
                              repetition_penalty=stt_repetition_penalty)
        self._ipucu_fn = ipucu_fn            # ipucu_fn(akis) -> str (Whisper prompt'u); eski sifir argumanli lambda da olur
        self.stt_ipucu = bool(stt_ipucu)     # config 'stt_ipucu'
        self.esikler = dict(esikler.get("esikler") if isinstance((esikler or {}).get("esikler"), dict)
                            else (esikler or {}))      # config 'esikler' (halusinasyon, ipucu_rms)
        self._cizelge_fn = cizelge_fn        # cizelge_fn(akis, bas, bit) -> True/False/None (None: kapsama yok, kural uygulanmaz)
        self.ipucu_gonderilen = 0            # prompt ile giden klip sayisi (A/B teshisi)
        self.ipucu_yeniden = 0               # supheli prompt'lu sonucun promptsuz tekrarlandigi klip sayisi
        self.atilan_halusinasyon = 0         # puani 'at' esigini asip metni atilan klip sayisi
        self.mod = mod                       # otomatik | cift | tek
        self.mik_cihaz = mik_cihaz           # config 'mikrofon_cihaz': elle ayar (yoksa otomatik secim)
        self.cihaz_otomatik = cihaz_otomatik is not False
        self._arka = arka
        self._altyazi_fn = altyazi_fn
        self.mik_secici = None               # cihaz.MikrofonSecici (baslat kurar)
        self.loop_secici = None              # cihaz.LoopbackSecici
        self._olay = olay or (lambda t, v: None)
        self._olay_fn = olay_fn
        self._satir = satir_fn or (lambda *a: None)
        self.akislar = []
        self._dur = threading.Event()
        self._isci = None
        self._isciler = []
        self.kulaklik = True if mod == "cift" else (False if mod == "tek" else None)   # None: pasif tespit bekleniyor
        self._kulaklik_tespit = None
        self.son_hata = None
        self.cozulen = 0
        self.gonderilen = 0
        self._son_satirlar = []          # (zaman, kaynak, metin) — eski yol eko tekillestirme icin
        self._cumle_gecmisi = []         # (an, kaynak, normalize cumle): tekrar suzgeci (TEKRAR_PENCERE_SN)
        self._bicim_bildirildi = False
        self.elenen = 0
        self._kilit = threading.RLock()
        # Sirali yayim: 2 STT iscisi parcalari farkli hizda cozer; olaylar kuyruga giris sirasiyla yayilir.
        # Eskiden kisa bir parca uzun olandan once donunce satirlar transkripte ters sirayla giriyordu.
        self._sayac = itertools.count()
        self._sonraki = 0
        self._hazir = {}
        self._hazir_zaman = {}           # no -> sonucun hazir oldugu an: bastaki numara HOL_SN'den uzun takilirsa atlanir
        self._mono = time.monotonic
        self.atlanan_numara = 0
        self._olcum = None               # OlcumKaydi (opt-in); baslat'tan once ya da sonra acilabilir
        # STT kesintisi
        self._kesinti_kuyrugu = None     # KesintiKuyrugu (olcum_kaydi / kesinti_kaydi izinliyse)
        self._durum_kilit = threading.RLock()
        self._kesinti_bas = None         # STT kesintisi basladi (epoch) | None
        self._kesinti_hata = None
        self._eksik = []                 # [[bas, bit], ...] hic cozulemeyecek klip araliklari (diske yazilamadi)
        self._kuyruk_hata = {}           # kesinti kuyrugu basindaki klip -> ust uste STT hatasi (saglik gecerken)
        self._bekci = None
        self._bekci_var = False
        if olcum_klasor:
            self.olcum_kaydi_ac(olcum_klasor)
        if kesinti_klasor:
            self.kesinti_kaydi_ac(kesinti_klasor)

    # --- ayarlar ---
    def olcum_kaydi_ac(self, klasor):
        """Faz 0: STT'ye giden her klibi <klasor>/olcum/ altina yazar (bkz. OlcumKaydi). Olcum izni, STT kesintisinde
        klipleri diske yazma iznini de verir. Dondurur: OlcumKaydi."""
        self._olcum = OlcumKaydi(klasor, olay=self._olay)
        self._olay("log", f"ölçüm kaydı AÇIK: klipler {self._olcum.kok} altına yazılıyor")
        if self._kesinti_kuyrugu is None:
            self.kesinti_kaydi_ac(klasor)
        return self._olcum

    def kesinti_kaydi_ac(self, klasor):
        """config 'kesinti_kaydi': STT yokken cozulemeyen klipler <klasor>/olcum/kesinti/ altina yazilir, STT donunce
        sirayla cozulur. Dondurur: KesintiKuyrugu."""
        self._kesinti_kuyrugu = KesintiKuyrugu(klasor, olay=self._olay)
        return self._kesinti_kuyrugu

    # --- olay yayimi ---
    def _olay_ver(self, ev):
        fn = self._olay_fn
        if fn is None:
            return
        try:
            fn(ev)
        except Exception as e:
            self._olay("log", f"✖ ses olayı işlenemedi: {e!r}")

    def _akis_olayi(self, tip, veri):
        """Akis ipleri: saat capasi yenilemeyi ve mikrofon secimini olaylar.jsonl'e de yazar."""
        if tip in ("capa", "mikrofon", "ses_uyari") and self._olcum is not None:
            self._olcum.olay_yaz(tip, veri)
        self._olay(tip, veri)
        if tip == "capa" and isinstance(veri, dict):
            # motor -> konusmaci: o akisin klip epoch'lari sicradi (L capalari duzeltilir)
            with self._kilit:
                self._olay_ver({"tur": "capa", "akis": veri.get("akis"), "fark": veri.get("fark")})

    def _secicileri_kur(self):
        """cihaz.py secicileri (soundcard yoksa ya da cihaz_otomatik kapaliysa None, None: eski yol)."""
        if not self.cihaz_otomatik:
            self._olay("log", "ses cihazları: otomatik seçim kapalı (config 'cihaz_otomatik': false) — Windows varsayılan "
                              "hoparlörü ve " + (f"mikrofon {self.mik_cihaz!r}" if self.mik_cihaz not in (None, "")
                                                 else "varsayılan mikrofon"))
            return None, None
        arka = self._arka if self._arka is not None else cihaz_mod.varsayilan_arka()
        if arka is None:
            self._olay("log", "! ses cihazları: soundcard kurulu değil, otomatik seçim yok — Windows varsayılanları kullanılıyor "
                              "(pip install soundcard)")
            return None, None
        saat_fn = getattr(arka, "simdi", None)               # sahte arka ucun saati (testler)
        loop = cihaz_mod.LoopbackSecici(arka, olay=self._akis_olayi, saat=saat_fn, altyazi_fn=self._altyazi_fn)
        mik = cihaz_mod.MikrofonSecici(arka, elle=self.mik_cihaz, uzak_fn=loop.aktif_mi, olay=self._akis_olayi,
                                       saat=saat_fn, hfp_fn=loop.hfp_aktif_mi)
        return loop, mik

    def secili_mikrofon(self):
        """UI: {"kimlik","ad","otomatik","neden","sessizde"} | None (secilmedi ya da eski yol)."""
        m = self.mik_secici
        return m.secili_mikrofon() if m is not None else None

    def _gecis(self, akis, g):
        """Akis ipi: mantiksal akista cihaz degisti. Mikrofon degisince kulaklik korelasyon penceresi sifirlanir."""
        if akis == "mikrofon" and getattr(g, "kapat", False) and self._kulaklik_tespit is not None:
            self._kulaklik_tespit.sifirla()

    def _kulaklik_olayi(self, ev):
        self.kulaklik = ev["durum"]
        etiket = {True: "kulaklık (ayrı akışlar)", False: "hoparlör (eko olası)", None: "bilinmiyor"}[ev["durum"]]
        self._olay("log", f"kulaklık durumu: {etiket} (güven {ev['guven']})")
        with self._kilit:
            self._olay_ver(ev)

    # --- yasam dongusu ---
    def baslat(self):
        """Yakalamayi ve STT iscilerini baslatir. STT'ye ulasilamasa da True doner: yakalama surer, klipler bosluk olur
        (ya da izin varsa diske yazilip STT donunce cozulur). Altyazi moduna DUSMEZ."""
        if not self.stt.saglik():
            self._kesinti_ac(getattr(self.stt, "saglik_hatasi", None) or "saglik denetimi basarisiz")
        self._kulaklik_tespit = KulaklikTespit(olay_fn=self._kulaklik_olayi)
        if self.mod in ("cift", "tek"):
            self._kulaklik_tespit.sabitle(self.kulaklik)
        self._olay("log", "ses yakalama: hoparlör + mikrofon"
                          + (" (kulaklık durumu pasif korelasyonla izlenecek)" if self.kulaklik is None
                             else (" (kulaklık: ayrı akışlar)" if self.kulaklik else " (hoparlör: eko olası)")))
        # Mikrofon HER ZAMAN yakalanir: yalniz toplantida ya da sadece sen konusurken
        # loopback'te ses olmaz. Hoparlorde ayni cumle iki akistan gelir -> konusmaci.py zamana bakip eler.
        ortak = dict(olay=self._akis_olayi, sayac=self._sayac, blok_fn=self._kulaklik_tespit.blok_ekle,
                     tasma_fn=self._tasma, gecis_fn=self._gecis)
        if self.uzun_pencere:
            self.uzun_ac()
            ortak["klip_fn"] = self._uzun_klip
        # Cihazlar otomatik (cihaz.py): mikrofon = acik mikrofonlardan secilen, loopback = tum cikislarin karisimi
        self.loop_secici, self.mik_secici = self._secicileri_kur()
        saat_fn = getattr(self._arka, "simdi", None) if self._arka is not None else None
        if saat_fn is not None:
            ortak["saat"] = saat_fn
        self.akislar = [AkisYakalayici("loopback", self.kuyruk, loopback=True, secici=self.loop_secici, **ortak),
                        AkisYakalayici("mikrofon", self.kuyruk, cihaz=self.mik_cihaz, secici=self.mik_secici, **ortak)]
        for a in self.akislar:
            a.start()
        self._isciler = [threading.Thread(target=self._calis, daemon=True) for _ in range(ISCI_SAYISI)]
        for i in self._isciler:
            i.start()
        self._isci = self._isciler[0]
        if self.uzun is not None:
            self._uzun_isci = threading.Thread(target=self._uzun_dongu, daemon=True, name="uzun-pencere")
            self._uzun_isci.start()
        return True

    # --- Faz 3: uzun pencere ---
    def uzun_ac(self):
        """UzunPencere'yi kurar (baslat bunu bayrak aciksa cagirir; duzenek / replay dogrudan). Motor'a
        {"tur": "uzun_durum", "acik": True} gider: parca kapanisi uzun pencere sonucunu bekler. Dondurur: UzunPencere."""
        if self.uzun is None:
            self.uzun = UzunPencere(lambda ses_, ipucu: self.stt.coz(ses_, ipucu=ipucu), self._uzun_ses, self._uzun_yay,
                                    puan_fn=self._uzun_puan, metin_fn=lambda seg: self.klip_metni(seg, say=False),
                                    esik_fn=self._esik, ipucu_acik=self.stt_ipucu, olay=self._olay)
            self.uzun_pencere = True
            self._uzun_rapor_an = saat_mod.simdi()
            self._olay("log", "uzun pencere AÇIK: 20-28 sn'lik pencereler yeniden çözülüyor (STT yükü ~2x)")
            with self._kilit:
                self._olay_ver({"tur": "uzun_durum", "acik": True})
        return self.uzun

    def _uzun_klip(self, akis, p):
        """Akis ipi (AkisYakalayici._kaydet): klip olustu -> uzun pencere kaydi."""
        u = self.uzun
        if u is not None:
            u.klip_kaydet(akis, p.get("klip_no"), p.get("bas_epoch"), p.get("bit_epoch"), p.get("rms"), p.get("ust_klip"))

    def _uzun_metin(self, klip_no, metin):
        u = self.uzun
        if u is not None and klip_no is not None:
            u.metin_kaydet(klip_no, metin)

    def ses_al(self, akis, bas, bit):
        """Halka tampondan (akis, epoch araligi) 16 kHz ses; yoksa None (ses izi icin)."""
        return self._uzun_ses(akis, bas, bit)

    def _uzun_ses(self, akis, bas, bit):
        """Pencere sesi halka tampondan (epoch araligi); halkadan cikmis / henuz gelmemis kisim kirpilir. < 1 sn: None."""
        for a in self.akislar:
            if a.kaynak != akis or not a._capalar:
                continue
            s0 = max(int(round(a.epoch_ornek(bas))), a.halka.baslangic)
            s1 = min(int(round(a.epoch_ornek(bit))), a.halka.bitis)
            if s1 - s0 < ORNEK:
                return None
            ham = a.halka.al(s0, s1)
            return normalize_et(ham) if ham is not None else None
        return None

    def _uzun_puan(self, p, segmentler, ipucu):
        """Uzun pencere halusinasyon puani (kisa klip ile ayni kurallar); sure = pencerenin konusma suresi (klip ici)."""
        bas = p.get("bas") or 0.0
        q = {"kaynak": p.get("akis"), "bas_epoch": bas, "bit_epoch": bas + max(float(p.get("konusma_sn") or 0.0), 0.1),
             "rms": p.get("rms")}
        return self._puan(q, segmentler, ipucu=ipucu)

    def _uzun_yay(self, ev):
        with self._kilit:
            self._olay_ver(ev)

    def uzun_istatistik(self):
        """GPU yuku: uzun pencere STT istekleri + kisa klip istekleri (saatlik) | None (uzun pencere kapali)."""
        u = self.uzun
        return u.istatistik(kisa_istek=self.gonderilen + self.ipucu_yeniden) if u is not None else None

    def _uzun_raporla(self, son=False):
        d = self.uzun_istatistik()
        if not d:
            return
        saatlik = f"saatte ~{d['saatlik_istek']:.0f}" if d.get("saatlik_istek") is not None else "saatlik: ölçülemedi"
        kisa = (f"; kısa klip {d['kisa_istek']} istek (saatte ~{d['kisa_saatlik']:.0f})"
                if d.get("kisa_saatlik") is not None else "")
        self._olay("log", f"  · uzun pencere{' (son)' if son else ''}: {d['pencere']} pencere, {d['istek']} STT isteği "
                          f"({saatlik}){kisa}; {d['degisen_klip']} klip metni güncellendi, atlanan {d['atlanan']}")

    def _uzun_ilerlet(self):
        u = self.uzun
        for a in self.akislar:
            try:
                u.ilerle(a.kaynak, a.ilerleme())
            except Exception:
                pass

    def _uzun_dongu(self):
        """Uzun pencere iscisi: akis ilerlemesi -> planla -> coz -> hizala -> yay. Durdurulunca kalan pencereler en fazla
        UZUN_BITIS_SN icinde cozulur (kisa klip metinleri o ana kadar gelmistir)."""
        u = self.uzun
        while not self._uzun_dur.is_set():
            try:
                self._uzun_ilerlet()
                u.adim()
            except Exception as e:
                self._olay("log", f"✖ uzun pencere turu başarısız: {e!r}")
            simdi = saat_mod.simdi()
            if self._uzun_rapor_an is not None and simdi - self._uzun_rapor_an >= UZUN_RAPOR_SN:
                self._uzun_rapor_an = simdi
                self._uzun_raporla()
            self._uzun_dur.wait(UZUN_YOKLAMA_SN)
        son = saat_mod.simdi() + UZUN_BITIS_SN
        try:
            self._uzun_ilerlet()
            u.bitir_isaretle()
            while u.bekleyen() and saat_mod.simdi() < son:
                if not u.adim(zorla=True):
                    break
        except Exception as e:
            self._olay("log", f"✖ uzun pencere son turu başarısız: {e!r}")
        self._uzun_raporla(son=True)

    def _uzun_kapat(self):
        isci = self._uzun_isci
        if isci is None:
            return
        self._uzun_dur.set()
        try:
            isci.join(timeout=UZUN_BITIS_SN + 10)
        except RuntimeError:
            pass
        if isci.is_alive():
            self._olay("log", f"! uzun pencere {UZUN_BITIS_SN} sn içinde bitmedi: kalan satırlar kısa metinle kalır")

    def durdur(self):
        for a in self.akislar:
            a.durdur()
        for a in self.akislar:                 # son (yarim kalan) klip kuyruga girmeden iscileri bitirme
            try:
                a.join(timeout=5)
            except RuntimeError:               # hic baslamamis is parcacigi
                pass
        self._dur.set()
        for i in self._isciler:
            i.join(timeout=60)
        bekci = self._bekci
        if bekci is not None and bekci.is_alive():
            bekci.join(timeout=KESINTI_BITIS_SN)
        with self._kilit:                      # takilan/eksik numara kaldiysa kalanlari sirayla bosalt
            for no in sorted(self._hazir):
                self._yay(self._hazir.pop(no))
            self._hazir_zaman.clear()
        self._uzun_kapat()                     # kisa metinlerin hepsi geldi: kalan uzun pencereler cozulur

    def yeniden_kes(self, klip_no, an):
        """Klibi an (epoch) +- 1 sn icindeki en dusuk enerjili 100 ms blokta ikiye boler (halka tampon). Bir parca 1.0 sn'den
        kisaysa, klip halkadan cikmissa ya da zaten kesilmisse False. True ise iki parca STT'ye gider ve sonra iki klip
        olayi gelir (yeni klip_no, ust_klip=klip_no)."""
        for a in self.akislar:
            with a._klip_kilit:
                var = klip_no in a.klipler
            if var:
                return a.kes(klip_no, an)
        return False

    # --- STT isleme ---
    def _calis(self):
        while not (self._dur.is_set() and self.kuyruk.empty()):
            try:
                p = self.kuyruk.get(timeout=0.5)
            except queue.Empty:
                self._hol_kontrol()
                continue
            self._isle(p)

    def _isle(self, p):
        sure = len(p["ses"]) / ORNEK
        self.gonderilen += 1
        self._olay("ses_parca", {"kaynak": p["kaynak"], "sn": round(sure, 1),
                                 "kuyruk": self.kuyruk.qsize()})
        t0 = saat_mod.simdi()
        olcum = self._olcum
        olaylar, ham, seg_ozet, hata = [], None, None, None
        kesintide = False
        if olcum is not None:
            olcum.klip_yaz(p)
        try:
            if self._kesinti_bas is not None:                   # kesinti suruyor: istek atma, klibi sakla/bosluk say
                kesintide = True
                hata = "stt kesintisi"
            else:
                once = self.stt.yeniden_deneme
                segmentler = self._coz(p)
                self.cozulen += 1
                if olcum is not None:
                    ham = " ".join((s.get("text") or "").strip() for s in segmentler).strip()
                    seg_ozet = segment_ozeti(segmentler)
                if self.stt.yeniden_deneme != once:
                    self._olay("log", f"  · STT bağlantısı koptu, yeniden denendi ve kurtarıldı "
                                      f"(toplam {self.stt.yeniden_deneme})")
                if p.get("bit_epoch") is not None:
                    gecikme = round(saat_mod.simdi() - p["bit_epoch"], 2)
                else:
                    gecikme = (round(saat_mod.simdi() - saat_mod.epoch(p["bitis"]), 2)
                               if isinstance(p.get("bitis"), dt.datetime) else None)
                self._olay("ses_metin", {"kaynak": p["kaynak"], "segment": len(segmentler),
                                         "sn": round(sure, 1), "islem": round(saat_mod.simdi() - t0, 2),
                                         "gecikme": gecikme})
                if not self._bicim_bildirildi:
                    self._bicim_bildirildi = True
                    self._olay("log", "STT zaman damgası: " + ("segment bazlı (verbose_json)" if self.stt.verbose
                                                              else "yok (json) — metin klip bazında alınır"))
                olaylar = self._olaylar_uret(p, segmentler)
        except Exception as e:
            self.son_hata = e
            hata = repr(e)
            self._olay("log", f"✖ STT hatası: {e!r}")
            if kesinti_hatasi_mi(e):
                kesintide = True
                self._kesinti_ac(hata)
            else:                                               # yalniz bu klibi reddetti (400/422...)
                olaylar = self._bosluk_olaylari(p, "stt_hata")
        finally:
            try:
                if kesintide:
                    olaylar = self._kesintiye_al(p, "stt_yok")[1]
                if olcum is not None:
                    tsler = [a[0].strftime("%H:%M:%S") if isinstance(a, tuple) else a["ts"]
                             for a in olaylar if isinstance(a, tuple) or a.get("tur") == "klip"]
                    olcum.sonuc(p, ham, round(saat_mod.simdi() - t0, 2), tsler, seg_ozet, hata)
            finally:
                p["ses"] = None                       # ses bellekten dusurulur
                if self.uzun is not None and not any(isinstance(o, dict) and o.get("tur") == "klip" for o in olaylar):
                    self._uzun_metin(p.get("klip_no"), "")    # satiri olmayacak klip: uzun pencere beklemez
                self._sirali_yay(p.get("no"), olaylar)   # hata olsa da numara kapanir, sira takilmaz

    # --- klip olaylari ---
    def adaylar_uret(self, segmentler, sure, basla, kaynak):
        """ESKI YOL (olay_fn yokken). STT segmentleri -> [(an, kaynak, metin)]: cumlelere bol, tekrar temizle,
        gecersizleri ele. replay.py de ayni yolu kullanir."""
        adaylar = []
        for gorel, ham, s in cumlelere_bol(segmentler, sure):
            metin = tekrar_temizle(ham)
            if len(metin) < 2 or not sonuc_gecerli(s, metin):
                self.elenen += 1
                continue
            adaylar.append((basla + dt.timedelta(seconds=gorel), kaynak, metin))
        return adaylar

    def klip_metni(self, segmentler, say=True):
        """STT segmentleri -> tek klip metni: segment basina tekrar temizle, cumle cumle gecerlilik suzgeci
        (halusinasyon/anlamsiz cumle atilir), kalanlar birlestirilir. Bos olabilir. say=False: elenen sayaci artmaz."""
        parcalar = []
        for s in segmentler:
            ham = (s.get("text") or "").strip()
            if not ham:
                continue
            metin = tekrar_temizle(ham)
            for c in re.split(r"(?<=[.?!])\s+", metin):
                c = c.strip()
                if len(c) < 2 or not sonuc_gecerli(s, c):
                    if say:
                        self.elenen += 1
                    continue
                parcalar.append(c)
        return " ".join(parcalar)

    # --- halusinasyon puani, Whisper prompt'u ---
    def _esik(self, ad):
        """config 'esikler' degeri (sayi ise) ya da varsayilan: hal_* (stt.HAL_ESIKLER) ve ipucu_rms."""
        if ad == "ipucu_rms":
            v = self.esikler.get(ad)
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else IPUCU_RMS
        return hal_esik(self.esikler, ad)

    @staticmethod
    def _token_sayisi(segmentler):
        """Segmentlerdeki 'tokens' alanlarinin toplam uzunlugu; servis vermiyorsa (bos/yok) None (kural uygulanmaz)."""
        toplam, var = 0, False
        for s in segmentler:
            t = s.get("tokens")
            if isinstance(t, (list, tuple)) and t:
                toplam += len(t)
                var = True
        return toplam if var else None

    def _puan(self, p, segmentler, metin=None, ipucu=None):
        """Klip metni (suzgeclerden gecmis) icin halusinasyon puani. cizelge_fn varsa cizelge kuralini da uygular.
        Suzgecler (kara liste cumlesi, dongu tekrari) bir sey sildiyse ham segment metni de puanlanir ve buyuk olan
        alinir: silinen kanit kalan metni aklamaz ('Altyazı M.K. Herkese iyi akşamlar.'). ipucu: bu sonuc icin
        gonderilen Whisper prompt'u (prompt yankisi kurali); None ise _coz'un p'ye yazdigi kullanilir."""
        if metin is None:
            metin = self.klip_metni(segmentler, say=False)
        if not metin:
            return 0.0, metin
        if ipucu is None:
            ipucu = p.get("ipucu_kullanilan") or ""
        bas, bit = self._aralik(p)
        cizelge = None
        if self._cizelge_fn is not None:
            try:
                cizelge = self._cizelge_fn(p["kaynak"], bas, bit)
            except Exception:
                cizelge = None
        sure = max(0.0, bit - bas)
        ortak = dict(tokens=self._token_sayisi(segmentler), rms=p.get("rms"), cizelge_etkin=cizelge,
                     esikler=self.esikler, ipucu=ipucu)
        puan = halusinasyon_puani(metin, sure, **ortak)
        ham = re.sub(r"\s+", " ", " ".join((s.get("text") or "").strip() for s in segmentler)).strip()
        if ham and ham != metin:
            puan = max(puan, halusinasyon_puani(ham, sure, **ortak))
        return puan, metin

    def _ipucu_hazirla(self, p):
        """Bu klip icin Whisper prompt'u ('' = gonderme). Dusuk RMS (ya da bilinmeyen) klibe prompt yok."""
        if not self.stt_ipucu or self._ipucu_fn is None:
            return ""
        rms = p.get("rms")
        if not isinstance(rms, (int, float)) or rms < self._esik("ipucu_rms"):
            return ""
        try:
            try:
                n = len(inspect.signature(self._ipucu_fn).parameters)
            except (TypeError, ValueError):
                n = 1
            m = self._ipucu_fn(p["kaynak"]) if n else self._ipucu_fn()
        except Exception as e:
            self._olay("log", f"! ipucu üretilemedi: {e!r}")
            return ""
        return re.sub(r"\s+", " ", m or "").strip()[:IPUCU_KR_TAVAN]

    def _coz(self, p):
        """Klibi STT'ye gonderir (korumali prompt ile). Prompt'lu sonuc supheliyse promptsuz bir kez daha cozer ve puani
        dusuk olani tutar (esitlikte promptsuz). Istisna yalniz ilk istekten gelir (ikinci istek hatasi birinciyi bozmaz)."""
        ses_ = p["ses"]
        p.pop("ipucu_kullanilan", None)          # klip olayi puani tutulan sonucun prompt'uyla hesaplanir
        ipucu = self._ipucu_hazirla(p)
        if not ipucu:
            return self.stt.coz(ses_)
        self.ipucu_gonderilen += 1
        birinci = self.stt.coz(ses_, ipucu=ipucu)
        puan1, _ = self._puan(p, birinci, ipucu=ipucu)
        if puan1 < self._esik("hal_supheli"):
            p["ipucu_kullanilan"] = ipucu
            return birinci
        self.ipucu_yeniden += 1
        try:
            ikinci = self.stt.coz(ses_)
        except Exception as e:
            self._olay("log", f"! promptsuz tekrar çözüm başarısız: {e!r}")
            p["ipucu_kullanilan"] = ipucu
            return birinci
        puan2, _ = self._puan(p, ikinci, ipucu="")
        self._olay("log", f"  · prompt'lu sonuç şüpheli (puan {puan1:.2f}): promptsuz tekrar (puan {puan2:.2f})")
        if puan2 <= puan1:
            return ikinci
        p["ipucu_kullanilan"] = ipucu
        return birinci

    def klip_olayi_uret(self, p, segmentler):
        """Klip sozlugu p ve STT segmentlerinden C1 klip olayi (suzgecler uygulanmis metinle) | None (metin bos).
        Yeniden kesme parcasi (ust_klip) metni bos olsa da olay olur (text ""): motor parcanin geldigini bilir, diger
        parcayla ozgun satiri degistirir. Tekrar suzgeci yayim sirasinda ayrica uygulanir.
        hal_puani >= hal_supheli -> supheli; >= hal_at -> text "" ve atildi="halusinasyon"."""
        metin = self.klip_metni(segmentler)
        if not metin and p.get("ust_klip") is None:
            return None
        bas, bit = p.get("bas_epoch"), p.get("bit_epoch")
        if bas is None:
            bas = saat_mod.epoch(p["basla"])
        if bit is None:
            bit = saat_mod.epoch(p["bitis"]) if isinstance(p.get("bitis"), dt.datetime) else bas
        puan, _ = self._puan(p, segmentler, metin)
        ev = {"tur": "klip", "klip_no": p.get("klip_no"), "akis": p["kaynak"], "bas": bas, "bit": bit,
              "sure": round(bit - bas, 3), "text": metin, "ts": saat_mod.ts(bas),
              "rms": float(p.get("rms") or 0.0), "zarf": list(p.get("zarf") or []),
              "ust_klip": p.get("ust_klip"), "supheli": puan >= self._esik("hal_supheli"),
              "hal_puani": round(puan, 3)}
        if puan >= self._esik("hal_at"):
            ev.update(text="", atildi="halusinasyon")
            self.elenen += 1
            self.atilan_halusinasyon += 1
        return ev

    def _olaylar_uret(self, p, segmentler):
        """Worker/kesinti tekrar cozumu: olay_fn varsa [klip olayi], yoksa eski yol [(an, kaynak, metin)]."""
        if self._olay_fn is None:
            return self.adaylar_uret(segmentler, len(p["ses"]) / ORNEK, p["basla"], p["kaynak"])
        ev = self.klip_olayi_uret(p, segmentler)
        return [ev] if ev else []

    def _bosluk_olaylari(self, p, neden):
        if self._olay_fn is None:
            return []
        bas, bit = self._aralik(p)
        return [{"tur": "bosluk", "akis": p["kaynak"], "bas": bas, "bit": bit, "neden": neden}]

    @staticmethod
    def _aralik(p):
        bas, bit = p.get("bas_epoch"), p.get("bit_epoch")
        if bas is None:
            bas = saat_mod.epoch(p["basla"]) if isinstance(p.get("basla"), dt.datetime) else saat_mod.simdi()
        if bit is None:
            bit = saat_mod.epoch(p["bitis"]) if isinstance(p.get("bitis"), dt.datetime) else bas
        return bas, bit

    # --- sirali yayim ---
    def _sirali_yay(self, no, adaylar):
        with self._kilit:
            if no is None or no < self._sonraki:
                self._yay(adaylar)                  # sirasiz klip ya da sirasi atlanmis (HOL) gec gelen sonuc
                return
            self._hazir[no] = adaylar
            self._hazir_zaman[no] = self._mono()
            self._hazir_bosalt()
            self._hol_kontrol()

    def _hazir_bosalt(self):
        while self._sonraki in self._hazir:
            self._hazir_zaman.pop(self._sonraki, None)
            self._yay(self._hazir.pop(self._sonraki))
            self._sonraki += 1

    def _hol_kontrol(self):
        """Bastaki numara HOL_SN'den uzun suredir gelmiyor ve arkasi hazirsa (takilan/olu istek): numara atlanir, arkadaki
        sonuclar yayilir. Atlanan numaranin sonucu sonra gelirse hemen yayilir (kaybolmaz)."""
        with self._kilit:
            if not self._hazir or self._sonraki in self._hazir:
                return
            en_eski = min(self._hazir_zaman.get(n, self._mono()) for n in self._hazir)
            if self._mono() - en_eski < HOL_SN:
                return
            ilk = min(self._hazir)
            self.atlanan_numara += ilk - self._sonraki
            self._olay("log", f"! ses sırası {HOL_SN} sn takıldı: {ilk - self._sonraki} klip beklenmeden devam ediliyor")
            self._sonraki = ilk
            self._hazir_bosalt()

    def _yay(self, adaylar):
        """Kilit altinda cagrilir. Tekrar/eko kontrolu de yayim sirasinda yapilir (sira onlar icin de onemli).
        Ogeler: sozluk (klip/bosluk olayi -> olay_fn) ya da eski yolda (an, kaynak, metin)."""
        for oge in adaylar:
            if isinstance(oge, dict):
                self._olay_yay(oge)
                continue
            an, kaynak, metin = oge
            # once tekrar kontrolu (kaydi eklemez), sonra eko kontrolu (kaydi ekler)
            if self._tekrar_mi(an, kaynak, metin) or self._eko_mu(an, kaynak, metin):
                self.elenen += 1
                continue
            try:
                self._satir(an.strftime("%H:%M:%S"), "ben" if kaynak == "mikrofon" else None, metin, kaynak)
            except Exception as e:
                self._olay("log", f"✖ satır işlenemedi: {e!r}")

    def _olay_yay(self, ev):
        """Klip olayi tekrar suzgecinden gecer. Yeniden kesme parcalari (ust_klip) muaf: cumleleri ust klibin zaten
        kaydedilmis cumleleridir, suzgec ikisini de dusururdu. Tum cumleleri suzgecte dusen loopback klibi metinsiz
        (text "") yine yayilir: o anda loopback'te ses vardi (konusmaci yanki tespiti ve L capasi icin); satir olusmaz."""
        if ev.get("tur") == "klip" and ev.get("ust_klip") is None:
            an = dt.datetime.fromtimestamp(ev["bas"])
            kalan = []
            for c in re.split(r"(?<=[.?!])\s+", ev["text"]):
                c = c.strip()
                if not c:
                    continue
                if self._tekrar_mi(an, ev["akis"], c):
                    self.elenen += 1
                    continue
                self._son_kaydet(an, ev["akis"], c)
                kalan.append(c)
            if self.uzun is not None:
                self._uzun_metin(ev.get("klip_no"), " ".join(kalan))    # satirin tasiyacagi (suzgecten gecmis) metin
            if not kalan and ev.get("akis") != "loopback":
                return
            ev = dict(ev, text=" ".join(kalan))
        elif ev.get("tur") == "klip" and self.uzun is not None:
            self._uzun_metin(ev.get("klip_no"), ev.get("text") or "")
        self._olay_ver(ev)

    def _son_kaydet(self, an, kaynak, metin):
        self._son_satirlar = [(t, k, m) for t, k, m in self._son_satirlar if (an - t).total_seconds() < 90]
        self._son_satirlar.append((an, kaynak, metin))

    def _tekrar_mi(self, an, kaynak, metin):
        """Whisper'in sessizlige yapistirdigi kalip / dongu: ayni akista TEKRAR_YAKIN_SN icinde ayni cumle ya da TEKRAR_PENCERE_SN
        icinde ucuncu kez ayni cumle. Gercek kisa cevaplar ve tekrarlar korunur: KISA_GECERLI ve <= KISA_CUMLE_KELIME kelimelik
        cumleler ('Tamam, anladim.') hic elenmez. Elenmeyen cumle gecmise yazilir."""
        n = metin.lower().strip(" .?!,;")
        if n in KISA_GECERLI or len(re.findall(r"[^\W\d_]+", n)) <= KISA_CUMLE_KELIME:
            return False
        self._cumle_gecmisi = [g for g in self._cumle_gecmisi if abs((an - g[0]).total_seconds()) < TEKRAR_PENCERE_SN]
        adet = 1
        for t, k, m in self._cumle_gecmisi:
            if m != n:
                continue
            if k == kaynak and abs((an - t).total_seconds()) < TEKRAR_YAKIN_SN:
                return True
            adet += 1
        if adet >= 3:
            return True
        self._cumle_gecmisi.append((an, kaynak, n))
        return False

    def _eko_mu(self, an, kaynak, metin):
        """ESKI YOL: kulakliksiz kullanimda ayni cumle hem hoparlorden hem mikrofondan gelir.
        Son 20 sn icinde DIGER akistan benzer bir cumle geldiyse bunu atla. (olay_fn yolunda yankiyi konusmaci.py
        zamana bakarak eler.)"""
        import difflib
        self._son_satirlar = [(t, k, m) for t, k, m in self._son_satirlar
                              if (an - t).total_seconds() < 90]
        eko = False
        if not self.kulaklik:
            n = metin.lower()
            for t, k, m in self._son_satirlar:
                if (k != kaynak and (an - t).total_seconds() < 20
                        and difflib.SequenceMatcher(None, n, m.lower()).ratio() > 0.75):
                    eko = True
                    break
        self._son_satirlar.append((an, kaynak, metin))
        return eko

    # --- STT kesintisi ---
    @property
    def stt_durumu(self):
        """UI icin: {'durum': 'ok'|'kesinti', 'bas': kesinti baslangici (epoch)|None, 'bekleyen': diskte cozulmeyi bekleyen
        klip sayisi, 'kayit': klipler diske yaziliyor mu, 'eksik': [[bas, bit], ...] hic cozulemeyecek (diske de yazilamayan)
        klip araliklari — transkriptte 'STT yok' notu icin, 'hata': son hata}."""
        with self._durum_kilit:
            kuyruk = self._kesinti_kuyrugu
            return {"durum": "kesinti" if self._kesinti_bas is not None else "ok",
                    "bas": self._kesinti_bas, "bekleyen": kuyruk.sayi() if kuyruk is not None and kuyruk.acik else 0,
                    "kayit": bool(kuyruk is not None and kuyruk.acik),
                    "eksik": [list(a) for a in self._eksik], "hata": self._kesinti_hata}

    def _kesinti_ac(self, hata):
        with self._durum_kilit:
            yeni = self._kesinti_bas is None
            if yeni:
                self._kesinti_bas = saat_mod.simdi()
            self._kesinti_hata = str(hata)[:200]
            self._bekci_baslat()
        if yeni:
            kayit = self._kesinti_kuyrugu is not None and self._kesinti_kuyrugu.acik
            self._olay("log", f"✖ STT erişilemiyor ({str(hata)[:120]}) — ses yakalanmaya devam ediyor; klipler "
                              + ("diske yazılıyor, STT dönünce çözülecek" if kayit
                                 else "çözülemeyecek (transkriptte boşluk kalır)"))
            if self._olcum is not None:
                self._olcum.olay_yaz("stt_kesinti", {"durum": "basladi", "hata": str(hata)[:200]})
            self._olay("stt_durum", self.stt_durumu)

    def _kesinti_bitti(self):
        with self._durum_kilit:
            if self._kesinti_bas is None:
                return
            sure = saat_mod.simdi() - self._kesinti_bas
            self._kesinti_bas = None
        self._olay("log", f"✔ STT geri geldi (kesinti {int(sure)} sn)")
        if self._olcum is not None:
            self._olcum.olay_yaz("stt_kesinti", {"durum": "bitti", "sure": round(sure, 1)})
        self._olay("stt_durum", self.stt_durumu)

    def _eksik_ekle(self, bas, bit):
        """Hic cozulemeyecek klip araligi; yakin (30 sn) araliklar birlesir."""
        with self._durum_kilit:
            if self._eksik and bas - self._eksik[-1][1] < 30:
                self._eksik[-1][1] = max(self._eksik[-1][1], bit)
            else:
                self._eksik.append([bas, bit])

    def _kesintiye_al(self, p, neden):
        """Cozulemeyen klip: izin varsa diske yazar (STT donunce cozulur), bosluk olayi uretir.
        -> (diske yazildi mi, olaylar)."""
        olaylar = self._bosluk_olaylari(p, neden)
        bas, bit = self._aralik(p)
        kaydedildi = False
        with self._durum_kilit:
            kuyruk = self._kesinti_kuyrugu
            if kuyruk is not None and kuyruk.acik and p.get("klip_no") is not None and p.get("ses") is not None:
                meta = {"klip_no": p["klip_no"], "akis": p["kaynak"], "bas": bas, "bit": bit,
                        "zarf": list(p.get("zarf") or []), "rms": float(p.get("rms") or 0.0),
                        "ust_klip": p.get("ust_klip")}
                kaydedildi = kuyruk.ekle(meta, p["ses"])
            if kaydedildi:
                self._bekci_baslat()
            else:
                self._eksik_ekle(bas, bit)
        return kaydedildi, olaylar

    def _tasma(self, p):
        """Akis ipi: kuyruk KUYRUK_TAVAN'i asti (STT fiilen yok). Klip diske yazilir/bosluk olur; sirasi kapatilir."""
        kaydedildi, olaylar = self._kesintiye_al(p, "kuyruk_tasti")
        self._uzun_metin(p.get("klip_no"), "")
        self._sirali_yay(p.get("no"), olaylar)
        return kaydedildi

    def _bekci_baslat(self):
        with self._durum_kilit:
            if self._bekci_var:
                return
            self._bekci_var = True
            self._bekci = threading.Thread(target=self._bekci_dongusu, daemon=True)
            self._bekci.start()

    def duraklat(self):
        """Kesinti bekcisini (ve yakalamayi) durdurur: bekci bir sonraki turda cikar, kalan klipler diskte kalir."""
        self._dur.set()

    def kesinti_kalan(self):
        """Diskte cozulmeyi bekleyen klip sayisi (kuyruk yoksa/kapaliysa 0)."""
        kuyruk = self._kesinti_kuyrugu
        return kuyruk.sayi() if kuyruk is not None and kuyruk.acik else 0

    def kesinti_bosalt(self, duraklatildi=None, kesinti_bekle_sn=None, kesinti_asildi=None, duraklat_hatasi=RuntimeError):
        """Diskteki kesinti kuyrugunu klip_no sirasiyla cozer; kuyruk bosalinca/duraklatilinca doner (bloklar).
        duraklatildi: fn() -> True ise yeni klip cozulmez (duraklat_hatasi firlatilir: kesinti hatasi sayilir, klip diskte kalir).
        kesinti_bekle_sn: STT bu kadar suredir yine kesikse duraklat() + kesinti_asildi() (bir kez). -> kalan klip sayisi."""
        if duraklatildi is not None:
            orijinal = self._coz

            def coz(p):                                   # duraklatma: klip diskte kalir, sira korunur
                if duraklatildi():
                    raise duraklat_hatasi("kesinti kuyruğu duraklatıldı")
                return orijinal(p)
            self._coz = coz
        self._bekci_baslat()
        bekci = self._bekci
        while bekci is not None and bekci.is_alive():
            bekci.join(1.0)
            bas = self._kesinti_bas
            if (kesinti_bekle_sn is not None and bas is not None and saat_mod.simdi() - bas > kesinti_bekle_sn
                    and not self._dur.is_set()):
                self.duraklat()
                if kesinti_asildi is not None:
                    kesinti_asildi()
        return self.kesinti_kalan()

    def _saglik(self):
        try:
            return bool(self.stt.saglik(deneme=1))
        except Exception:
            return False

    def _bekci_dongusu(self):
        """Kesinti/tasma sonrasi: STT'ye erisilebilir olunca diskteki klipleri klip_no sirasiyla cozer, kesinti bitince cikar."""
        try:
            ilk = True
            while True:
                if not ilk and not self._dur.is_set():
                    self._dur.wait(KESINTI_YOKLAMA)
                ilk = False
                durdu = self._dur.is_set()
                if self._kesinti_bas is not None:
                    if not self._saglik():
                        if durdu:
                            return
                        continue
                elif self.kuyruk.qsize() >= KUYRUK_UYARI and not durdu:
                    continue                          # canli klipler once; tasma kuyrugu sonra
                if self._kuyruk_bosalt():
                    with self._durum_kilit:
                        kuyruk = self._kesinti_kuyrugu
                        if kuyruk is not None and kuyruk.acik and kuyruk.sayi() > 0:
                            ilk = True                # bosaltirken yeni klip yazildi: bir tur daha
                            continue
                        self._bekci_var = False
                        bitti = self._kesinti_bas is not None
                    if bitti:
                        self._kesinti_bitti()
                    return
                if durdu:
                    return
        except Exception as e:
            self._olay("log", f"✖ STT kesinti bekçisi durdu: {e!r}")
        finally:
            with self._durum_kilit:
                if self._bekci is threading.current_thread():
                    self._bekci_var = False

    def _kuyruk_bosalt(self):
        """Diskteki klipleri sirayla cozer ve olaylarini yayar. -> True: kuyruk bos; False: STT yine hata verdi."""
        kuyruk = self._kesinti_kuyrugu
        if kuyruk is None or not kuyruk.acik:
            return True
        while True:
            liste = kuyruk.liste()
            if not liste:
                return True
            for meta in liste:
                try:
                    ses_ = kuyruk.oku(meta)
                except Exception as e:
                    self._olay("log", f"✖ kesinti kuyruğu klibi okunamadı ({meta.get('klip_no')}): {e!r}")
                    kuyruk.sil(meta)
                    continue
                p = {"kaynak": meta["akis"], "ses": ses_, "no": None, "klip_no": meta["klip_no"],
                     "bas_epoch": meta["bas"], "bit_epoch": meta["bit"], "zarf": meta.get("zarf") or [],
                     "rms": meta.get("rms") or 0.0, "ust_klip": meta.get("ust_klip"),
                     "basla": saat_mod.yerel(meta["bas"]), "bitis": saat_mod.yerel(meta["bit"])}
                try:
                    segmentler = self._coz(p)
                except Exception as e:
                    self.son_hata = e
                    no = meta.get("klip_no")
                    if kesinti_hatasi_mi(e):
                        # servis ayaktayken (saglik yoklamasi geciyor) ayni klip ust uste hata veriyorsa klibe ozgu
                        # kalici hata (5xx): kuyruk basinda sonsuza dek takilip arkasindakileri bekletmesin
                        n = self._kuyruk_hata.get(no, 0) + (1 if self._saglik() else 0)
                        self._kuyruk_hata = {no: n}
                        if n < KUYRUK_KLIP_DENEME:
                            self._kesinti_ac(repr(e))
                            return False              # klip diskte kalir, sira korunur
                        self._olay("log", f"✖ kesinti kuyruğu klibi {n} kez STT hatası verdi, atlanıyor ({no}): {e!r}")
                    else:
                        self._olay("log", f"✖ kesinti kuyruğu klibi STT'ce reddedildi ({no}): {e!r}")
                    self._kuyruk_hata.pop(no, None)
                    self._eksik_ekle(meta["bas"], meta["bit"])
                    with self._kilit:
                        self._yay(self._bosluk_olaylari(p, "stt_hata"))
                    kuyruk.sil(meta)
                    continue
                self._kuyruk_hata.pop(meta.get("klip_no"), None)
                self.cozulen += 1
                olaylar = self._olaylar_uret(p, segmentler)
                with self._kilit:
                    self._yay(olaylar)
                kuyruk.sil(meta)
