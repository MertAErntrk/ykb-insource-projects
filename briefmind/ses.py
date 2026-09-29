"""
ses.py — Toplanti sesini yakalar, Whisper STT servisine gonderir, metin satirlari uretir.

Iki akis:
  loopback : hoparlor/kulaklik cikisi = karsi taraf (konusmaci altyazidan eslesir)
  mikrofon : sen (kulaklik varsa ayri akis; hoparlorle calisilirsa eko olur, kapatilir)

Parcalama (cumle bazli, dusuk gecikme): konusma basladiktan sonra 0.7 sn sessizlik gorunce
cumle biter ve HEMEN STT'ye gider (GPU servisi ~10 ms). Sessizlik gelmezse 14 sn'den sonra ilk
0.3 sn'lik nefes payinda, o da gelmezse 20 sn'de zorla keser. Konusmanin ilk hecesi kesilmesin
diye 0.3 sn on-yuk eklenir.

Ses diske YAZILMAZ: parca bellekte tutulur, STT cevabi gelince silinir.
  pip install sounddevice soundcard numpy requests
"""
import datetime as dt
import io
import queue
import threading
import time
import wave

import numpy as np
import requests

ORNEK = 16000
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
IPUCU_GONDER = False         # Whisper prompt'u: sozluk terimleri sessizlikte halusinasyona donusuyor ("ODS'i kullandiginda")
BLOK = 1600                  # 0.1 sn
KUYRUK_TAVAN = 12            # bu kadar cumle birikirse STT yetisemiyor demektir
ISCI_SAYISI = 2


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


def kulaklik_var_mi(sure=1.2):
    """Hoparlorden cikan sesi mikrofon duyuyor mu? Duyuyorsa kulaklik yok (tek akis moduna gecilir).
    Kisa, duyulur esikte bir ton calip mikrofonda o frekansi arar."""
    try:
        import sounddevice as sd
        t = np.arange(int(ORNEK * sure)) / ORNEK
        ton = (0.25 * np.sin(2 * np.pi * 1000 * t)).astype("float32")
        kayit = sd.rec(len(ton), samplerate=ORNEK, channels=1, dtype="float32")
        sd.play(ton, samplerate=ORNEK)
        sd.wait()
        mik = kayit[:, 0]
        if len(mik) < ORNEK // 2:
            return True
        spektrum = np.abs(np.fft.rfft(mik * np.hanning(len(mik))))
        frek = np.fft.rfftfreq(len(mik), 1 / ORNEK)
        hedef = spektrum[(frek > 950) & (frek < 1050)].max() if len(spektrum) else 0
        zemin = np.median(spektrum) + 1e-9
        return (hedef / zemin) < 25          # ton duyulmuyorsa kulaklik var
    except Exception:
        return True                           # emin olamadik: kulaklik varsay (cift akis)


# ---------------------------------------------------------------- yakalama

class AkisYakalayici(threading.Thread):
    """Tek bir ses akisini (loopback ya da mikrofon) parcalara boler, kuyruga koyar."""

    def __init__(self, kaynak, kuyruk, loopback=False, cihaz=None, olay=None):
        super().__init__(daemon=True)
        self.kaynak, self.kuyruk, self.loopback, self.cihaz = kaynak, kuyruk, loopback, cihaz
        self._olay = olay or (lambda t, v: None)
        self._dur = threading.Event()
        self.hata = None
        self.toplam_sn = 0.0

    def durdur(self):
        self._dur.set()

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
        def geri(indata, frames, zaman, durum):
            q.put(indata.copy().reshape(-1))
        with sd.InputStream(samplerate=ORNEK, channels=1, dtype="float32", blocksize=BLOK,
                            device=self.cihaz, callback=geri):
            while not self._dur.is_set():
                try:
                    yield q.get(timeout=0.5)
                except queue.Empty:
                    continue

    # --- parcalama ---
    def run(self):
        tampon, sessiz_sn, parca_bas = [], 0.0, None
        onyuk, aday = [], []                                    # on-yuk; acilis adayi bloklar
        sesli_sayac = 0                                         # parcadaki sesli blok sayisi
        taban = SESSIZ_ESIK                                     # uyarlanan gurultu tabani
        self._olay("log", f"  {self.kaynak} akışı açılıyor…")
        try:
            bloklar = self._bloklar_loopback() if self.loopback else self._bloklar_mikrofon()
            for blok in bloklar:
                if blok is None or not len(blok):
                    continue
                sn = len(blok) / ORNEK
                self.toplam_sn += sn
                rms = float(np.sqrt(np.mean(np.square(blok))))
                # gurultu tabani: sessiz bloklarda hizli iner, yavas yukselir
                taban = min(taban * 1.01 + 1e-6, rms) if rms < taban * 2 else taban
                esik = max(SESSIZ_ESIK, taban * GURULTU_KATI)
                sesli = rms >= esik
                if not tampon:
                    onyuk.append(blok)
                    onyuk = onyuk[-(ONYUK_BLOK + ACILIS_BLOK):]
                    aday = (aday + [blok]) if sesli else []
                    if len(aday) < ACILIS_BLOK:
                        continue                               # tek klik/tus sesi acmaz
                    tampon.extend(onyuk)                       # on-yuk + acilis bloklari
                    sesli_sayac, aday, onyuk = ACILIS_BLOK, [], []
                    parca_bas = dt.datetime.now() - dt.timedelta(seconds=len(tampon) * sn)
                    sessiz_sn = 0.0
                    continue
                tampon.append(blok)
                sesli_sayac += 1 if sesli else 0
                sessiz_sn = 0.0 if sesli else sessiz_sn + sn
                uzunluk = sum(len(b) for b in tampon) / ORNEK
                kes = ((uzunluk >= MIN_SN and sessiz_sn >= CUMLE_SESSIZLIK)      # cumle bitti
                       or (uzunluk >= TAVAN_SN and sessiz_sn >= NEFES_SESSIZLIK)  # uzun konusma, nefes payi
                       or uzunluk >= ZORLA_SN)
                if kes:
                    self._gonder(tampon, parca_bas, sesli_sayac / max(1, len(tampon)))
                    tampon, sessiz_sn, parca_bas, sesli_sayac = [], 0.0, None, 0
            if tampon:
                self._gonder(tampon, parca_bas, sesli_sayac / max(1, len(tampon)))
        except Exception as e:
            self.hata = e
            ek = ""
            if self.loopback:
                ek = " (soundcard paketi kurulu mu? pip install soundcard)"
            self._olay("log", f"✖ {self.kaynak} akışı durdu: {e!r}{ek}")

    def _gonder(self, tampon, basla, konusma_orani=1.0):
        ses = np.concatenate(tampon)
        if len(ses) / ORNEK < MIN_SN:                           # cok kisa parcayi atla
            return
        if konusma_orani < KONUSMA_ORANI:                        # cogu sessizlik/gurultu: Whisper uydurur
            return
        if float(np.sqrt(np.mean(np.square(ses)))) < SESSIZ_ESIK:
            return
        if self.kuyruk.qsize() >= KUYRUK_TAVAN:
            self._olay("stt_gecikme", self.kuyruk.qsize())
            return                                              # STT yetisemiyor: parcayi dusur
        self.kuyruk.put({"kaynak": self.kaynak, "basla": basla,
                         "bitis": dt.datetime.now(), "ses": ses})


def tekrar_temizle(metin):
    """Whisper'in kisa parcalarda urettigi tekrarlari siler:
    'Selamlar. Selamlar. Selamlar.' -> 'Selamlar.'   'bu hangi sebepten dolayi bu hangi sebepten dolayi ...' -> tek.
    Tekrarlarin SONUNCUSU tutulur (devami olan o)."""
    import difflib
    import re
    m = re.sub(r"\s+", " ", (metin or "")).strip()
    if not m:
        return m

    def sade(k):
        return re.sub(r"[^\w']", "", k.lower())

    # a) ardisik tekrarlayan kelime obekleri (token bazli; 1-8 kelimelik obek)
    tok = m.split()
    nrm = [sade(k) for k in tok]
    out, i = [], 0
    while i < len(tok):
        atlandi = False
        for n in range(min(8, (len(tok) - i) // 2), 0, -1):
            if nrm[i:i + n] and nrm[i:i + n] == nrm[i + n:i + 2 * n]:
                k = 1
                while nrm[i + k * n:i + (k + 1) * n] == nrm[i:i + n]:
                    k += 1
                i += (k - 1) * n                 # onceki tekrarlari at, sonuncusundan devam et
                atlandi = True
                break
        if not atlandi:
            out.append(tok[i])
            i += 1
    m = " ".join(out)
    # b) ardisik ayni/benzer/kapsayan cumleler
    cumleler = re.split(r"(?<=[.?!])\s+", m)
    tutulan = []
    for c in cumleler:
        c = c.strip()
        if not c:
            continue
        if tutulan:
            a, b = tutulan[-1].lower().strip(".?! "), c.lower().strip(".?! ")
            if a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.85 \
                    or (len(a) > 6 and a in b) or (len(b) > 6 and b in a):
                if len(c) >= len(tutulan[-1]):
                    tutulan[-1] = c
                continue
        tutulan.append(c)
    sonuc = " ".join(tutulan)
    return (sonuc[0].upper() + sonuc[1:]) if sonuc and sonuc[0].islower() else sonuc


def cumlelere_bol(segmentler, sure):
    """STT ciktisini cumle cumle (goreli baslangic saniyesiyle) dondurur.
    Servis segment zamanlari veriyorsa onlar kullanilir; tek segment ya da 4 sn'den uzun segment
    cumlelere ayrilip zaman karakter sayisina orantili dagitilir. Boylece ayni klipteki farkli
    konusmacilarin cumleleri ayri ayri, kendi zamanlariyla eslesir."""
    import re
    sonuc = []
    for seg in segmentler:
        metin = (seg.get("text") or "").strip()
        if not metin:
            continue
        bas = float(seg.get("start") or 0.0)
        bit = seg.get("end")
        bit = float(bit) if isinstance(bit, (int, float)) and bit > bas else sure
        cumleler = [c.strip() for c in re.split(r"(?<=[.?!])\s+", metin) if c.strip()]
        if len(cumleler) <= 1 or (bit - bas) < 2.5:
            sonuc.append((bas, metin, seg))
            continue
        toplam = sum(len(c) for c in cumleler) or 1
        t = bas
        for c in cumleler:
            sonuc.append((t, c, seg))
            t += (bit - bas) * len(c) / toplam
    return sonuc


KISA_GECERLI = {"evet", "hayır", "hayir", "tamam", "peki", "yok", "ok", "olur", "aynen", "doğru", "dogru", "hı", "hıhı"}


def sonuc_gecerli(segment, metin):
    """Whisper ciktisi gercek konusma mi? Halusinasyon belirtilerini eler."""
    import re
    nsp = segment.get("no_speech_prob")
    if isinstance(nsp, (int, float)) and nsp > 0.6:
        return False
    lp = segment.get("avg_logprob")
    if isinstance(lp, (int, float)) and lp < -1.2:
        return False
    kelimeler = re.findall(r"[^\W\d_]+", metin, flags=re.UNICODE)
    if not kelimeler:
        return False
    if len(kelimeler) == 1:
        k = kelimeler[0].lower()
        return k in KISA_GECERLI or len(k) >= 4
    # sesli harf orani: "Vok Vokok Vok", "Rides;ides" gibi anlamsiz diziler
    harf = "".join(kelimeler).lower()
    sesli = sum(1 for c in harf if c in "aeıioöuü")
    if len(harf) >= 6 and sesli / len(harf) < 0.25:
        return False
    return True


def wav_bayt(ses):
    tampon = io.BytesIO()
    with wave.open(tampon, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(ORNEK)
        w.writeframes((np.clip(ses, -1, 1) * 32767).astype("<i2").tobytes())
    return tampon.getvalue()


# ---------------------------------------------------------------- STT

class SttIstemci:
    """OpenAI uyumlu /v1/audio/transcriptions istemcisi. ARGE'nin GPU'lu Turkce servisi
    (model whisper-large-v3-turbo-prod, Bearer EMPTY) ve kendi CPU servisimiz ayni arayuzle calisir."""

    def __init__(self, adres, ipucu_fn=None, zaman_asimi=180, model="whisper", api_key=""):
        self.adres = adres.rstrip("/")
        if self.adres.endswith("/v1/audio/transcriptions"):
            self.adres = self.adres[:-len("/v1/audio/transcriptions")]
        self._ipucu = ipucu_fn or (lambda: "")
        self.zaman_asimi = zaman_asimi
        self.model = model or "whisper"
        self.api_key = api_key
        self.verbose = True                       # servis verbose_json bilmiyorsa json'a duser

    def _basliklar(self):
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def saglik(self):
        for yol in ("/health", "/v1/models"):
            try:
                r = requests.get(f"{self.adres}{yol}", headers=self._basliklar(), verify=False, timeout=6)
                if r.status_code == 200:
                    return True
            except Exception:
                continue
        return False

    def _istek(self, ses, bicim, onceki=""):
        # Not: onceki cumleyi prompt'a eklemek Whisper'da tekrar dongusune ("Selamlar. Selamlar...")
        # yol aciyor; yalnizca kisa sozluk/katilimci ipucu gonderilir.
        veri = {"language": "tr", "model": self.model, "temperature": "0", "response_format": bicim}
        if IPUCU_GONDER:
            veri["prompt"] = self._ipucu()[:200]
        return requests.post(f"{self.adres}/v1/audio/transcriptions", verify=False,
                             timeout=self.zaman_asimi, headers=self._basliklar(),
                             files={"file": ("parca.wav", wav_bayt(ses), "audio/wav")}, data=veri)

    def coz(self, ses, onceki=""):
        """Ses -> [{'start','end','text'}] (parca icindeki goreli saniyeler).
        onceki: ayni akisin bir onceki cumlesi (baglam kosullamasi, dogrulugu artirir)."""
        r = self._istek(ses, "verbose_json" if self.verbose else "json", onceki)
        if r.status_code == 400 and self.verbose:
            self.verbose = False
            r = self._istek(ses, "json", onceki)
        r.raise_for_status()
        v = r.json()
        return v.get("segments") or ([{"start": 0, "end": 0, "text": v.get("text", "")}]
                                     if v.get("text") else [])


# ---------------------------------------------------------------- servis

class SesServisi:
    """Yakalama + STT'yi yonetir. satir_fn(ts, konusmaci, metin, kaynak) ile satir uretir.
    konusmaci: mikrofon akisi icin 'ben', loopback icin None (motor altyazidan esler)."""

    def __init__(self, stt_adres, ipucu_fn=None, mod="otomatik", mik_cihaz=None, olay=None, satir_fn=None,
                 model="whisper", api_key=""):
        self.kuyruk = queue.Queue()
        self.stt = SttIstemci(stt_adres, ipucu_fn, model=model, api_key=api_key)
        self.mod = mod                       # otomatik | cift | tek
        self.mik_cihaz = mik_cihaz
        self._olay = olay or (lambda t, v: None)
        self._satir = satir_fn or (lambda *a: None)
        self.akislar = []
        self._dur = threading.Event()
        self._isci = None
        self.kulaklik = None
        self.son_hata = None
        self.cozulen = 0
        self.gonderilen = 0
        self._son_satirlar = []          # (zaman, kaynak, metin) — eko tekillestirme icin
        self._sayim = {}                 # cumle -> kac kez geldi (halusinasyon kalibi tespiti)
        self._bicim_bildirildi = False
        self.elenen = 0
        self._kilit = threading.Lock()

    def baslat(self):
        if not self.stt.saglik():
            self._olay("log", "✖ STT servisine ulaşılamıyor — altyazı moduna düşülüyor")
            return False
        self.kulaklik = True if self.mod == "cift" else (False if self.mod == "tek" else kulaklik_var_mi())
        self._olay("log", "ses yakalama: hoparlör + mikrofon"
                          + (" (kulaklık: ayrı akışlar)" if self.kulaklik else " (hoparlör: eko tekilleştirme açık)"))
        # Mikrofon HER ZAMAN yakalanir: yalniz toplantida ya da sadece sen konusurken
        # loopback'te ses olmaz. Kulaklik yoksa ayni cumle iki akistan gelebilir -> tekillestirilir.
        self.akislar = [AkisYakalayici("loopback", self.kuyruk, loopback=True, olay=self._olay),
                        AkisYakalayici("mikrofon", self.kuyruk, cihaz=self.mik_cihaz, olay=self._olay)]
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
        self._dur.set()
        for i in getattr(self, "_isciler", []):
            i.join(timeout=60)

    def _calis(self):
        while not (self._dur.is_set() and self.kuyruk.empty()):
            try:
                p = self.kuyruk.get(timeout=0.5)
            except queue.Empty:
                continue
            sure = len(p["ses"]) / ORNEK
            self.gonderilen += 1
            self._olay("ses_parca", {"kaynak": p["kaynak"], "sn": round(sure, 1),
                                     "kuyruk": self.kuyruk.qsize()})
            t0 = time.time()
            try:
                segmentler = self.stt.coz(p["ses"])
                self.cozulen += 1
                gecikme = round(time.time() - p["bitis"].timestamp(), 2) if isinstance(p["bitis"], dt.datetime) else None
                self._olay("ses_metin", {"kaynak": p["kaynak"], "segment": len(segmentler),
                                         "sn": round(sure, 1), "islem": round(time.time() - t0, 2),
                                         "gecikme": gecikme})
            except Exception as e:
                self.son_hata = e
                self._olay("log", f"✖ STT hatası: {e!r}")
                continue
            finally:
                p["ses"] = None                       # ses bellekten dusurulur
            kim = "ben" if p["kaynak"] == "mikrofon" else None
            if not self._bicim_bildirildi:
                self._bicim_bildirildi = True
                self._olay("log", "STT zaman damgası: " + ("segment bazlı (verbose_json)" if self.stt.verbose
                                                          else "yok (json) — cümleler süreye orantılı dağıtılır"))
            with self._kilit:
                for gorel, ham, s in cumlelere_bol(segmentler, sure):
                    metin = tekrar_temizle(ham)
                    if len(metin) < 2 or not sonuc_gecerli(s, metin):
                        self.elenen += 1
                        continue
                    an = p["basla"] + dt.timedelta(seconds=gorel)
                    # once tekrar kontrolu (kaydi eklemez), sonra eko kontrolu (kaydi ekler)
                    if self._tekrar_mi(an, p["kaynak"], metin) or self._eko_mu(an, p["kaynak"], metin):
                        self.elenen += 1
                        continue
                    self._satir(an.strftime("%H:%M:%S"), kim, metin, p["kaynak"])

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
        """Kulakliksiz kullanimda ayni cumle hem hoparlorden hem mikrofondan gelir.
        Son 20 sn icinde DIGER akistan benzer bir cumle geldiyse bunu atla."""
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
