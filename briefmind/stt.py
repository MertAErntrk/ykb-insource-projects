"""
stt.py — Whisper STT istemcisi ve cikti suzgecleri (ses.py'den ayrildi, Faz 1).

  SttIstemci       OpenAI uyumlu /v1/audio/transcriptions; baglanti kopmasinda toplam sure sinirli yeniden deneme
  sonuc_gecerli    halusinasyon / anlamsiz dizi eleme (segment alanlari yoksa yalniz metne bakar)
  tekrar_temizle   Whisper'in kisa klipte urettigi tekrarlar
  KesintiKuyrugu   STT yokken klipleri yerel diske yazar (olcum_kaydi / kesinti_kaydi izinliyse), sirayla geri okur

Saf Python + numpy + requests: Qt ve ses cihazi gerekmez. ses.py bu adlari yeniden disa aktarir (eski importlar calisir).
"""
import io
import json
import os
import re
import threading
import time
import wave

import numpy as np
import requests

import ayar as ayar_mod

ORNEK = 16000
IPUCU_GONDER = False         # Whisper prompt'u: sozluk terimleri sessizlikte halusinasyona donusuyor ("ODS'i kullandiginda")

KISA_GECERLI = {"evet", "hayır", "hayir", "tamam", "peki", "yok", "ok", "olur", "aynen", "doğru", "dogru", "hı", "hıhı"}

# Whisper'in sessizlige/muzige yapistirdigi, egitim verisindeki video altyazilarindan gelen kaliplar
HALUSINASYON = ("izlediğiniz için teşekkür", "izlediginiz icin tesekkur", "abone olmayı unutmayın",
                "abone olmayi unutmayin", "beğenmeyi unutmayın", "altyazı m.k", "altyazı: m.k",
                "bir sonraki videoda görüşmek", "kanalıma abone")


# ---------------------------------------------------------------- metin suzgecleri

def tekrar_temizle(metin):
    """Whisper'in kisa parcalarda urettigi tekrarlari siler:
    'Selamlar. Selamlar. Selamlar.' -> 'Selamlar.'   'bu hangi sebepten dolayi bu hangi sebepten dolayi ...' -> tek.
    Tekrarlarin SONUNCUSU tutulur (devami olan o)."""
    import difflib
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
    """STT ciktisini cumle cumle (goreli baslangic saniyesiyle) dondurur. ESKI YOL: zamanlama icin kullanilmaz
    (Faz 1'de bir klip = bir olay); yalniz eski satir_fn yolu, replay ve duzenek icin durur.
    Servis segment zamanlari veriyorsa onlar kullanilir; tek segment ya da 4 sn'den uzun segment
    cumlelere ayrilip zaman karakter sayisina orantili dagitilir."""
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


def sonuc_gecerli(segment, metin):
    """Whisper ciktisi gercek konusma mi? Halusinasyon belirtilerini eler."""
    nsp = segment.get("no_speech_prob")
    lp = segment.get("avg_logprob")
    lp_var = isinstance(lp, (int, float))
    # Whisper'in kendi kurali: sessizlik olasiligi yuksek VE guven dusukse konusma yoktur. Yalniz
    # no_speech_prob'a bakmak, arka plan gurultulu gercek cumleleri de atiyordu.
    if isinstance(nsp, (int, float)) and nsp > 0.6 and (not lp_var or lp < -1.0):
        return False
    if lp_var and lp < -1.5:                   # -1.2 esigi aksanli/gurultulu gercek konusmayi da eliyordu
        return False
    ml = metin.replace("İ", "i").replace("I", "ı").lower()
    if any(h in ml for h in HALUSINASYON):
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


def segment_ozeti(segmentler):
    """Replay icin: STT segmentlerinin kucuk, JSON'a yazilabilir ozeti."""
    sonuc = []
    for s in segmentler or []:
        o = {"start": s.get("start"), "end": s.get("end"), "text": (s.get("text") or "").strip()}
        for k in ("avg_logprob", "no_speech_prob"):
            if isinstance(s.get(k), (int, float)):
                o[k] = round(float(s[k]), 3)
        sonuc.append(o)
    return sonuc


# ---------------------------------------------------------------- wav

def wav_bayt(ses):
    tampon = io.BytesIO()
    with wave.open(tampon, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(ORNEK)
        w.writeframes((np.clip(ses, -1, 1) * 32767).astype("<i2").tobytes())
    return tampon.getvalue()


def wav_oku(yol):
    """16 kHz mono int16 wav -> float32 dizi."""
    with wave.open(yol, "rb") as w:
        ham = w.readframes(w.getnframes())
    return np.frombuffer(ham, dtype="<i2").astype("float32") / 32767.0


# ---------------------------------------------------------------- STT

def kesinti_hatasi_mi(e):
    """STT hatasi servis kesintisi mi (klip sonra tekrar denenebilir), yoksa yalniz bu klibe ozgu mu?
    400/413/415/422 klibin kendisinden kaynaklanir; geri kalani (baglanti, zaman asimi, 5xx, 401/404...)
    servis durumudur: klip atilmaz, STT donunce cozulur."""
    cevap = getattr(e, "response", None)
    kod = getattr(cevap, "status_code", None)
    return kod not in (400, 413, 415, 422)


class SttIstemci:
    """OpenAI uyumlu /v1/audio/transcriptions istemcisi. ARGE'nin GPU'lu Turkce servisi
    (model whisper-large-v3-turbo-prod, Bearer EMPTY) ve kendi CPU servisimiz ayni arayuzle calisir."""

    DENEME = 3                                   # baglanti kopmasinda toplam deneme
    BEKLEME = (0.5, 1.5)                         # denemeler arasi (sn)
    BAGLANTI_ZAMAN_ASIMI = 10                    # baglanti kurma (sn): servis yoksa hemen anlasilsin
    OKUMA_TABAN = 30                             # cevap bekleme: max(OKUMA_TABAN, OKUMA_KATI*sure + 15), zaman_asimi ile sinirli
    OKUMA_KATI = 4
    TOPLAM_SN = 120                              # tum denemeler icin ust sinir (eskiden 3 x 180 sn'ye kadar uzayabiliyordu)
    TEKRARLANAN_KODLAR = (408, 429, 502, 503, 504)

    def __init__(self, adres, ipucu_fn=None, zaman_asimi=180, model="whisper", api_key="", proxy=True,
                 verify=None):
        self.proxy = proxy                        # False: Windows/ortam proxy'si atlanir, dogrudan baglanilir
        # A8: ca_bundle yolu ya da False; verilmezse config.json'dan (ayar.tls_dogrulama)
        self.verify = ayar_mod.tls_dogrulama() if verify is None else verify
        self._yerel = threading.local()           # requests.Session iscilerde paylasilmaz (biri kapatinca digeri bozulmasin)
        self._oturum = self._yeni_oturum()
        self.yeniden_deneme = 0                   # kopma sonrasi basarili tekrar sayisi (teshis)
        self.saglik_hatasi = None
        self.adres = adres.rstrip("/")
        if self.adres.endswith("/v1/audio/transcriptions"):
            self.adres = self.adres[:-len("/v1/audio/transcriptions")]
        self._ipucu = ipucu_fn or (lambda: "")
        self.zaman_asimi = zaman_asimi
        self.model = model or "whisper"
        self.api_key = api_key
        self.verbose = True                       # servis verbose_json bilmiyorsa json'a duser

    def _yeni_oturum(self):
        o = requests.Session()
        o.trust_env = bool(self.proxy)
        return o

    @property
    def _oturum(self):
        o = getattr(self._yerel, "oturum", None)
        if o is None:
            o = self._yerel.oturum = self._yeni_oturum()
        return o

    @_oturum.setter
    def _oturum(self, deger):
        self._yerel.oturum = deger

    def _basliklar(self):
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def saglik(self, deneme=2):
        """Servis ayakta mi? Basarisizsa nedeni self.saglik_hatasi'na yazar (log'da gosterilir)."""
        self.saglik_hatasi = None
        if not self.adres:
            self.saglik_hatasi = "stt_url boş (config.json okunamadı ya da alan yok)"
            return False
        for d in range(deneme):
            if d:
                time.sleep(2)
            for yol in ("/health", "/v1/models"):
                try:
                    r = self._oturum.get(f"{self.adres}{yol}", headers=self._basliklar(), verify=self.verify,
                                         timeout=10)
                    if r.status_code == 200:
                        return True
                    self.saglik_hatasi = f"{yol} -> HTTP {r.status_code}"
                except Exception as e:
                    self.saglik_hatasi = f"{yol} -> {type(e).__name__}: {str(e)[:160]}"
        return False

    def _istek(self, ses, bicim, onceki=""):
        # Not: onceki cumleyi prompt'a eklemek Whisper'da tekrar dongusune ("Selamlar. Selamlar...")
        # yol aciyor; yalnizca kisa sozluk/katilimci ipucu gonderilir.
        veri = {"language": "tr", "model": self.model, "temperature": "0", "response_format": bicim}
        if IPUCU_GONDER:
            veri["prompt"] = self._ipucu()[:200]
        okuma = min(float(self.zaman_asimi), max(float(self.OKUMA_TABAN),
                                                  self.OKUMA_KATI * len(ses) / ORNEK + 15.0))
        return self._oturum.post(f"{self.adres}/v1/audio/transcriptions", verify=self.verify,
                                 timeout=(self.BAGLANTI_ZAMAN_ASIMI, okuma), headers=self._basliklar(),
                                 files={"file": ("parca.wav", wav_bayt(ses), "audio/wav")}, data=veri)

    def _dayanikli_istek(self, ses, bicim, onceki=""):
        """Baglanti koparsa (Windows 10053/10054, proxy/VPN/OpenShift router bosta kalan baglantiyi
        kapatti, pod yeniden basladi) ya da sunucu 408/429/502/503/504 donerse, yeni bir baglantiyla tekrar dener.
        Eskiden tek kopma o ses parcasini (konusmayi) kaybettiriyordu. Toplam sure TOPLAM_SN ile sinirlidir:
        her deneme icin ayri 180 sn beklemek is parcacigini dakikalarca tutup siradaki klipleri geciktiriyordu."""
        son_hata = None
        bas = time.monotonic()
        for deneme in range(self.DENEME):
            if deneme:
                bekle = self.BEKLEME[min(deneme - 1, len(self.BEKLEME) - 1)]
                if time.monotonic() - bas + bekle > self.TOPLAM_SN:
                    break
                time.sleep(bekle)
            try:
                r = self._istek(ses, bicim, onceki)
            except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as e:
                son_hata = e
                self._oturum.close()
                self._oturum = self._yeni_oturum()      # bozuk/yari kapali baglanti havuzunu at
                continue
            if r.status_code in self.TEKRARLANAN_KODLAR and deneme < self.DENEME - 1:
                son_hata = requests.HTTPError(f"{r.status_code}", response=r)
                continue
            if deneme:
                self.yeniden_deneme += 1
            return r
        raise son_hata

    def coz(self, ses, onceki=""):
        """Ses -> [{'start','end','text'}] (parca icindeki goreli saniyeler).
        onceki: ayni akisin bir onceki cumlesi (baglam kosullamasi, dogrulugu artirir)."""
        r = self._dayanikli_istek(ses, "verbose_json" if self.verbose else "json", onceki)
        if r.status_code == 400 and self.verbose:
            self.verbose = False
            r = self._dayanikli_istek(ses, "json", onceki)
        r.raise_for_status()
        v = r.json()
        return v.get("segments") or ([{"start": 0, "end": 0, "text": v.get("text", "")}]
                                     if v.get("text") else [])


# ---------------------------------------------------------------- STT kesintisi: yerel kuyruk

class KesintiKuyrugu:
    """STT servisi yokken klipleri <klasor>/olcum/kesinti/ altina yazar; servis donunce klip_no sirasiyla okunur.
      NNNNNN.wav   STT'ye gidecek 16 kHz mono int16 klip
      NNNNNN.json  klip bilgisi (klip_no, akis, bas, bit, sure, zarf, rms, ust_klip)
    json en son yazilir: yarim yazilmis klip listede gorunmez. Is parcacigi guvenli; IO hatasinda bir kez bildirir
    ve kendini kapatir (klip kaybolur, yakalama dongusune istisna sizmaz)."""

    def __init__(self, klasor, olay=None):
        self.klasor = os.path.join(klasor, "olcum", "kesinti")
        self.acik = True
        self._olay = olay or (lambda t, v: None)
        self._kilit = threading.Lock()
        try:
            os.makedirs(self.klasor, exist_ok=True)
        except Exception as e:
            self._kapat(e)

    def _kapat(self, e):
        if self.acik:
            self.acik = False
            try:
                self._olay("log", f"✖ kesinti kuyruğu kapandı (disk hatası): {e!r}")
            except Exception:
                pass

    def _yol(self, klip_no, uzanti):
        return os.path.join(self.klasor, f"{int(klip_no):06d}.{uzanti}")

    def ekle(self, meta, ses):
        """-> True: klip diske yazildi. meta: JSON'a yazilabilir dict ('klip_no' zorunlu)."""
        if not self.acik:
            return False
        try:
            with self._kilit:
                with open(self._yol(meta["klip_no"], "wav"), "wb") as f:
                    f.write(wav_bayt(ses))
                with open(self._yol(meta["klip_no"], "json"), "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False)
            return True
        except Exception as e:
            self._kapat(e)
            return False

    def liste(self):
        """Bekleyen klip bilgileri, klip_no artan sirada."""
        sonuc = []
        try:
            for ad in os.listdir(self.klasor):
                if not ad.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(self.klasor, ad), encoding="utf-8") as f:
                        sonuc.append(json.load(f))
                except Exception:
                    continue                       # bozuk/yarim dosya: atla
        except Exception:
            return []
        return sorted(sonuc, key=lambda m: m.get("klip_no", 0))

    def oku(self, meta):
        return wav_oku(self._yol(meta["klip_no"], "wav"))

    def sil(self, meta):
        for uz in ("json", "wav"):               # once json: yarida kalirsa klip listeden duser
            try:
                os.remove(self._yol(meta["klip_no"], uz))
            except OSError:
                pass

    def sayi(self):
        return len(self.liste())
