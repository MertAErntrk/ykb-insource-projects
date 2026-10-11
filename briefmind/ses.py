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
kayma varsa yeniden capalanir (olay + olaylar.jsonl).

Olaylar (SesServisi(olay_fn=...)): klip / bosluk / kulaklik sozlukleri, akis basina sirali (bkz. SesServisi).
olay_fn verilmezse eski yol: satir_fn(ts, konusmaci, metin, kaynak).

Halka tampon: her akisin son 10 dk'si float16 olarak bellekte; yeniden_kes(klip_no, an) klibi en sessiz blokta ikiye boler.

Ses diske YAZILMAZ: klip bellekte tutulur, STT cevabi gelince silinir. Istisnalar (yerel, repo disi):
  - olcum kaydi (config 'olcum_kaydi', varsayilan kapali): klipler <klasor>/olcum/ altina yazilir.
  - STT kesintisi: 'olcum_kaydi' ya da 'kesinti_kaydi' aciksa cozulemeyen klipler <klasor>/olcum/kesinti/ altina yazilir,
    STT donunce sirayla cozulur.
  pip install sounddevice soundcard numpy requests
"""
import collections
import datetime as dt
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

# STT ve metin suzgecleri stt.py'ye tasindi; eski importlar (ses.SttIstemci, ses.sonuc_gecerli, ses.requests...) calismaya devam eder
from stt import (HALUSINASYON, IPUCU_GONDER, KISA_GECERLI, ORNEK, KesintiKuyrugu, SttIstemci, cumlelere_bol,
                 kesinti_hatasi_mi, segment_ozeti, sonuc_gecerli, tekrar_temizle, wav_bayt, wav_oku)

# yeniden disa aktarilan adlar (pyflakes: kullanilmamis import sayilmasin)
__all__ = ["HALUSINASYON", "IPUCU_GONDER", "KISA_GECERLI", "ORNEK", "KesintiKuyrugu", "SttIstemci", "cumlelere_bol",
           "kesinti_hatasi_mi", "requests", "segment_ozeti", "sonuc_gecerli", "tekrar_temizle", "wav_bayt", "wav_oku",
           "AkisYakalayici", "HalkaTampon", "KulaklikTespit", "OlcumKaydi", "SesServisi", "bolme_noktasi",
           "cihazlari_listele", "normalize_et", "zarf_hesapla"]

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
HOL_SN = 60                  # sirada bekleyen sonuc icin: bastaki numara bu kadar gelmezse atlanir
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
    capa gecmisini kullanir (yeniden capalamadan onceki orneklerin zamani degismez)."""

    def __init__(self, kaynak, kuyruk, loopback=False, cihaz=None, olay=None, sayac=None, blok_fn=None, tasma_fn=None,
                 saat=None, halka_sn=HALKA_SN):
        super().__init__(daemon=True)
        self.kaynak, self.kuyruk, self.loopback, self.cihaz = kaynak, kuyruk, loopback, cihaz
        self._sayac = sayac                     # paylasilan sira sayaci: cikti kuyruga giris sirasinda yayilir
        self._yerel_sayac = itertools.count()   # sayac verilmediyse (testler) klip_no buradan
        self._olay = olay or (lambda t, v: None)
        self._blok_fn = blok_fn                 # (akis, blok_orta_epoch, rms): pasif kulaklik tespiti
        self._tasma_fn = tasma_fn               # kuyruk KUYRUK_TAVAN'i asinca klip(p) -> True: diske alindi
        self._saat = saat or time.time
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

    def epoch_ornek(self, t):
        """Epoch saniye -> akis ornek numarasi (float)."""
        for ob, capa in reversed(self._capalar):
            n = (t - capa) * ORNEK
            if n >= ob:
                return n
        return (t - (self._capalar[0][1] if self._capalar else (self.epoch_capa or 0.0))) * ORNEK

    def _capa_kur(self, blok_uzunluk):
        self.epoch_capa = self._saat() - blok_uzunluk / ORNEK       # blok yeni bitti
        self._capalar = [(0, self.epoch_capa)]

    def _capa_kontrol(self):
        """Her CAPA_ARALIK_SN'de (akis zamani) sayac zamani ile saati karsilastirir; kayma > CAPA_ESIK_SN ise yeniden capalar."""
        if self.toplam_ornek < self._sonraki_capa or not self._capalar:
            return
        while self._sonraki_capa <= self.toplam_ornek:
            self._sonraki_capa += CAPA_ARALIK_SN * ORNEK
        simdi = self._saat()
        fark = simdi - self.ornek_epoch(self.toplam_ornek)
        if abs(fark) <= CAPA_ESIK_SN:
            return
        yeni = simdi - self.toplam_ornek / ORNEK
        self._capalar.append((self.toplam_ornek, yeni))
        self.epoch_capa = yeni
        self.capa_sayisi += 1
        self._olay("capa", {"akis": self.kaynak, "fark": round(fark, 3), "ornek": self.toplam_ornek,
                            "epoch_capa": yeni, "saat": simdi})
        self._olay("log", f"  · {self.kaynak} saat çapası yenilendi (fark {fark:+.2f} sn)")

    # --- ses kaynaklari ---
    def _bloklar_loopback(self):
        import soundcard as sc
        hoparlor = sc.default_speaker()
        mik = sc.get_microphone(str(hoparlor.name), include_loopback=True)
        with mik.recorder(samplerate=ORNEK, channels=1, blocksize=BLOK) as kayit:
            while not self._dur.is_set():
                yield kayit.record(numframes=BLOK).reshape(-1)

    def _bloklar_mikrofon(self):
        import sounddevice as sd
        q = queue.Queue()
        # Cihaz kendi hizinda acilir, 16 kHz'e indirilir: WASAPI cihazlari 16 kHz'i reddeder, config'teki
        # "mikrofon_cihaz" boyle bir cihazi gosterse de akis acilabilsin.
        try:
            hiz = int(sd.query_devices(self.cihaz, "input")["default_samplerate"]) or ORNEK
        except Exception:
            hiz = ORNEK
        blok = int(BLOK * hiz / ORNEK)
        hedef_t = np.arange(BLOK) * (hiz / ORNEK)
        kaynak_t = np.arange(blok)

        def geri(indata, frames, zaman, durum):
            x = indata[:, 0].copy()
            q.put(x if hiz == ORNEK else np.interp(hedef_t, kaynak_t[:len(x)], x).astype("float32"))
        with sd.InputStream(samplerate=hiz, channels=1, dtype="float32", blocksize=blok,
                            device=self.cihaz, callback=geri):
            while not self._dur.is_set():
                try:
                    yield q.get(timeout=0.5)
                except queue.Empty:
                    continue

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
                if blok is None or not len(blok):
                    continue
                sn = len(blok) / ORNEK
                blok_ornek = self.toplam_ornek                  # bu blogun akis ornek numarasi
                if self.epoch_capa is None:
                    self._capa_kur(len(blok))
                self.toplam_sn += sn
                self.toplam_ornek += len(blok)
                self.halka.ekle(blok_ornek, blok)
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
                elif uzunluk >= ZORLA_SN:
                    # Sessizlik hic gelmedi. Tam ZORLA_SN'de kesmek kelimeyi ortadan boler, iki parca da
                    # Whisper'da bozuk cikar: son 1.5 sn'nin en sessiz blogunda bol, kalani sonraki parcaya.
                    bol = bolme_noktasi(enerji)
                    self._gonder(tampon[:bol], None, sum(bayrak[:bol]) / bol, parca_ornek, enerji[:bol], bayrak[:bol])
                    parca_ornek += sum(len(b) for b in tampon[:bol])
                    tampon, enerji, bayrak = tampon[bol:], enerji[bol:], bayrak[bol:]
                    sessiz_sn = 0.0
            if tampon:
                self._gonder(tampon, None, sum(bayrak) / len(tampon), parca_ornek, enerji, bayrak)
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
        p = {"kaynak": self.kaynak, "basla": dt.datetime.fromtimestamp(bas), "bitis": dt.datetime.fromtimestamp(bit),
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
            p = {"kaynak": self.kaynak, "basla": dt.datetime.fromtimestamp(bas),
                 "bitis": dt.datetime.fromtimestamp(bit), "ses": normalize_et(ses_), "no": no, "klip_no": yeni_no,
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
            satir = {"zaman": round(time.time(), 3), "tur": tur}
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
                bit = p["bitis"].timestamp() if isinstance(p.get("bitis"), dt.datetime) else time.time()
                bas = p["basla"].timestamp() if isinstance(p.get("basla"), dt.datetime) else bit - sure
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


# ---------------------------------------------------------------- servis

class SesServisi:
    """Yakalama + STT'yi yonetir.

    olay_fn(dict) verilirse (Faz 1) her olay sozlugu SIRALI cagrilir (STT cikis sirasi degil, klip sirasi):
      klip    {"tur","klip_no","akis","bas","bit","sure","text","ts","rms","zarf","ust_klip","supheli"}
      bosluk  {"tur","akis","bas","bit","neden"}   cozulemeyen klip ('stt_yok': kesinti; 'kuyruk_tasti'; 'stt_hata')
      kulaklik {"tur","durum","guven"}             pasif tespit degisince
    olay_fn yoksa eski yol: satir_fn(ts, konusmaci, metin, kaynak); konusmaci: mikrofon icin 'ben', loopback icin None.

    STT kesintisi altyazi moduna dusurmez: yakalama surer, klipler bosluk olur; olcum_kaydi/kesinti_kaydi aciksa diske yazilip
    STT donunce sirayla cozulur. Durum: stt_durumu."""

    def __init__(self, stt_adres, ipucu_fn=None, mod="otomatik", mik_cihaz=None, olay=None, satir_fn=None,
                 model="whisper", api_key="", proxy=True, verify=None, olcum_klasor=None, olay_fn=None,
                 kesinti_klasor=None):
        self.kuyruk = queue.Queue()
        self.stt = SttIstemci(stt_adres, ipucu_fn, model=model, api_key=api_key, proxy=proxy, verify=verify)
        self.mod = mod                       # otomatik | cift | tek
        self.mik_cihaz = mik_cihaz
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
        self._son_satirlar = []          # (zaman, kaynak, metin) — tekrar/eko tekillestirme icin
        self._sayim = {}                 # cumle -> kac kez geldi (halusinasyon kalibi tespiti)
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
        """Akis ipleri: saat capasi yenilemeyi olaylar.jsonl'e de yazar."""
        if tip == "capa" and self._olcum is not None:
            self._olcum.olay_yaz("capa", veri)
        self._olay(tip, veri)
        if tip == "capa" and isinstance(veri, dict):
            # motor -> konusmaci: o akisin klip epoch'lari sicradi (L capalari duzeltilir)
            with self._kilit:
                self._olay_ver({"tur": "capa", "akis": veri.get("akis"), "fark": veri.get("fark")})

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
                     tasma_fn=self._tasma)
        self.akislar = [AkisYakalayici("loopback", self.kuyruk, loopback=True, **ortak),
                        AkisYakalayici("mikrofon", self.kuyruk, cihaz=self.mik_cihaz, **ortak)]
        for a in self.akislar:
            a.start()
        self._isciler = [threading.Thread(target=self._calis, daemon=True) for _ in range(ISCI_SAYISI)]
        for i in self._isciler:
            i.start()
        self._isci = self._isciler[0]
        return True

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
        t0 = time.time()
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
                segmentler = self.stt.coz(p["ses"])
                self.cozulen += 1
                if olcum is not None:
                    ham = " ".join((s.get("text") or "").strip() for s in segmentler).strip()
                    seg_ozet = segment_ozeti(segmentler)
                if self.stt.yeniden_deneme != once:
                    self._olay("log", f"  · STT bağlantısı koptu, yeniden denendi ve kurtarıldı "
                                      f"(toplam {self.stt.yeniden_deneme})")
                if p.get("bit_epoch") is not None:
                    gecikme = round(time.time() - p["bit_epoch"], 2)
                else:
                    gecikme = (round(time.time() - p["bitis"].timestamp(), 2)
                               if isinstance(p.get("bitis"), dt.datetime) else None)
                self._olay("ses_metin", {"kaynak": p["kaynak"], "segment": len(segmentler),
                                         "sn": round(sure, 1), "islem": round(time.time() - t0, 2),
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
                    olcum.sonuc(p, ham, round(time.time() - t0, 2), tsler, seg_ozet, hata)
            finally:
                p["ses"] = None                       # ses bellekten dusurulur
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

    def klip_metni(self, segmentler):
        """STT segmentleri -> tek klip metni: segment basina tekrar temizle, cumle cumle gecerlilik suzgeci
        (halusinasyon/anlamsiz cumle atilir), kalanlar birlestirilir. Bos olabilir."""
        parcalar = []
        for s in segmentler:
            ham = (s.get("text") or "").strip()
            if not ham:
                continue
            metin = tekrar_temizle(ham)
            for c in re.split(r"(?<=[.?!])\s+", metin):
                c = c.strip()
                if len(c) < 2 or not sonuc_gecerli(s, c):
                    self.elenen += 1
                    continue
                parcalar.append(c)
        return " ".join(parcalar)

    def klip_olayi_uret(self, p, segmentler):
        """Klip sozlugu p ve STT segmentlerinden C1 klip olayi (suzgecler uygulanmis metinle) | None (metin bos).
        Yeniden kesme parcasi (ust_klip) metni bos olsa da olay olur (text ""): motor parcanin geldigini bilir, diger
        parcayla ozgun satiri degistirir. Tekrar suzgeci yayim sirasinda ayrica uygulanir."""
        metin = self.klip_metni(segmentler)
        if not metin and p.get("ust_klip") is None:
            return None
        bas, bit = p.get("bas_epoch"), p.get("bit_epoch")
        if bas is None:
            bas = p["basla"].timestamp()
        if bit is None:
            bit = p["bitis"].timestamp() if isinstance(p.get("bitis"), dt.datetime) else bas
        return {"tur": "klip", "klip_no": p.get("klip_no"), "akis": p["kaynak"], "bas": bas, "bit": bit,
                "sure": round(bit - bas, 3), "text": metin, "ts": dt.datetime.fromtimestamp(bas).strftime("%H:%M:%S"),
                "rms": float(p.get("rms") or 0.0), "zarf": list(p.get("zarf") or []),
                "ust_klip": p.get("ust_klip"), "supheli": False}

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
            bas = p["basla"].timestamp() if isinstance(p.get("basla"), dt.datetime) else time.time()
        if bit is None:
            bit = p["bitis"].timestamp() if isinstance(p.get("bitis"), dt.datetime) else bas
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
            if not kalan and ev.get("akis") != "loopback":
                return
            ev = dict(ev, text=" ".join(kalan))
        self._olay_ver(ev)

    def _son_kaydet(self, an, kaynak, metin):
        self._son_satirlar = [(t, k, m) for t, k, m in self._son_satirlar if (an - t).total_seconds() < 90]
        self._son_satirlar.append((an, kaynak, metin))

    def _tekrar_mi(self, an, kaynak, metin):
        """Ayni akista son 90 sn icinde ayni cumle, ya da toplanti boyunca 3+ kez birebir ayni cumle
        (kisa onaylar haric): Whisper'in sessizlige yapistirdigi kalip."""
        n = metin.lower().strip(" .?!,;")
        if n in KISA_GECERLI:
            return False
        for t, k, m in self._son_satirlar:
            if k == kaynak and m.lower().strip(" .?!,;") == n and (an - t).total_seconds() < 90:
                return True
        self._sayim[n] = self._sayim.get(n, 0) + 1
        return self._sayim[n] >= 3

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
                self._kesinti_bas = time.time()
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
            sure = time.time() - self._kesinti_bas
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
        self._sirali_yay(p.get("no"), olaylar)
        return kaydedildi

    def _bekci_baslat(self):
        with self._durum_kilit:
            if self._bekci_var:
                return
            self._bekci_var = True
            self._bekci = threading.Thread(target=self._bekci_dongusu, daemon=True)
            self._bekci.start()

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
                     "basla": dt.datetime.fromtimestamp(meta["bas"]), "bitis": dt.datetime.fromtimestamp(meta["bit"])}
                try:
                    segmentler = self.stt.coz(ses_)
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
