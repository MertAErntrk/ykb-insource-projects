"""
stt_kesfet.py — STT (Whisper) servisinin neler verebildigini DENEYEREK bulur; kimseye sormaya gerek kalmaz.

Olculenler:
  1) Servisin kabul ettigi alanlar (FastAPI ise /openapi.json'daki form alanlari: prompt, timestamp_granularities ...)
  2) Zaman damgasi: verbose_json + timestamp_granularities[]=word/segment + word_timestamps -> segment/kelime
     start/end geliyor mu, olasilik alanlari (avg_logprob, no_speech_prob) var mi
  3) prompt modele gercekten gidiyor mu (uc ayri deneme: yazim kopyalama, kucuk harf/noktalama, sessizlikte prompt)

Kullanim (briefmind klasorunden; adres/anahtar config.json'dan okunur):
  python tools\\stt_kesfet.py                 -> 8 sn mikrofon kaydi alir (ekrandaki cumleyi oku)
  python tools\\stt_kesfet.py --wav kayit.wav -> var olan bir konusma kaydiyla (16 kHz'e cevrilir)
Cikti ekrana ve stt_kesfet_cikti.txt'ye yazilir; adres maskeli, konusma metni yazilmaz (--goster ile yazilir).
"""
import argparse
import difflib
import io
import json
import os
import re
import sys
import time
import wave
from urllib.parse import urlparse

import numpy as np

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(KOK)
sys.path.insert(0, KOK)
ORNEK = 16000
CIKTI = "stt_kesfet_cikti.txt"
OKUNACAK = "Jira kaydını açtım, Jenkins hattı yarın hazır olacak, IFRS 9 raporu da cuma günü gelecek."
# prompt'taki kasitli yazimlar: cikti bunlari kopyalarsa prompt modele gidiyordur
TUHAF_PROMPT = "Ceyra kaydı, Cenkins hattı, Ayefares dokuz raporu."
KUCUK_PROMPT = "evet ceyra kaydini actim cenkins hatti yarin hazir sonra ayefares raporu"
SESSIZ_PROMPT = "Kampanya modülü bütçe onayı toplantısı Jenkins entegrasyonu"
SATIRLAR = []
ARGS = None


def yaz(s=""):
    SATIRLAR.append(s)
    print(s)


def wav_oku(yol):
    with wave.open(yol, "rb") as w:
        kanal, gen, hiz, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        ham = w.readframes(n)
    tur = {1: np.int8, 2: np.int16, 4: np.int32}[gen]
    x = np.frombuffer(ham, dtype=tur).astype("float32") / float(np.iinfo(tur).max)
    if kanal > 1:
        x = x.reshape(-1, kanal).mean(axis=1)
    if hiz != ORNEK:
        t = np.arange(0, len(x) / hiz, 1 / ORNEK)
        x = np.interp(t, np.arange(len(x)) / hiz, x).astype("float32")
    return x


def kaydet(saniye):
    import sounddevice as sd
    bilgi = sd.query_devices(None, "input")
    hiz = int(bilgi["default_samplerate"]) or ORNEK
    yaz(f"{saniye:.0f} sn kayıt başlıyor. Şu cümleyi normal hızda OKU:\n  \"{OKUNACAK}\"")
    time.sleep(1.5)
    x = sd.rec(int(saniye * hiz), samplerate=hiz, channels=1, dtype="float32")
    sd.wait()
    x = x[:, 0]
    if hiz != ORNEK:
        t = np.arange(0, len(x) / hiz, 1 / ORNEK)
        x = np.interp(t, np.arange(len(x)) / hiz, x).astype("float32")
    return x


def wav_bayt(x):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(ORNEK)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    return b.getvalue()


class Stt:
    def __init__(self, cfg):
        import requests
        import ayar as ayar_mod
        self.adres = (cfg.get("stt_url") or "").rstrip("/")
        if self.adres.endswith("/v1/audio/transcriptions"):
            self.adres = self.adres[:-len("/v1/audio/transcriptions")]
        self.model = cfg.get("stt_model") or "whisper"
        anahtar = cfg.get("stt_key") or ""
        self.bas = {"Authorization": f"Bearer {anahtar}"} if anahtar else {}
        self.verify = ayar_mod.tls_dogrulama(cfg)
        self.o = requests.Session()
        self.o.trust_env = bool(cfg.get("stt_proxy", True))

    def get(self, yol):
        try:
            r = self.o.get(self.adres + yol, headers=self.bas, verify=self.verify, timeout=20)
            try:
                return r.status_code, r.json()
            except Exception:
                return r.status_code, r.text
        except Exception as e:
            return None, f"{type(e).__name__}: {str(e)[:150]}"

    def coz(self, ses, ek=None, coklu=None):
        """ek: ek form alanlari; coklu: [(alan, deger)] tekrarlanan alanlar (timestamp_granularities[])."""
        veri = [("language", "tr"), ("model", self.model), ("temperature", "0")]
        veri += list((ek or {}).items()) + list(coklu or [])
        t0 = time.time()
        try:
            r = self.o.post(self.adres + "/v1/audio/transcriptions", headers=self.bas, verify=self.verify,
                            timeout=180, data=veri, files={"file": ("deneme.wav", wav_bayt(ses), "audio/wav")})
        except Exception as e:
            return None, f"{type(e).__name__}: {str(e)[:150]}", time.time() - t0
        try:
            return r.status_code, r.json(), time.time() - t0
        except Exception:
            return r.status_code, r.text[:300], time.time() - t0


def maskele(s, adres):
    h = urlparse(adres).netloc
    return str(s).replace(h, "<STT>") if h else str(s)


def metin(j):
    if isinstance(j, dict):
        return (j.get("text") or " ".join((s.get("text") or "") for s in j.get("segments") or [])).strip()
    return ""


def _sade(s):
    s = (s or "").replace("İ", "i").replace("I", "ı").lower()
    for a, b in zip("çğıöşü", "cgiosu"):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9 ]", " ", s)


def zaman_raporu(j):
    """-> (ozet, segment start/end var mi, kelime zamani var mi)"""
    if not isinstance(j, dict):
        return "JSON değil", False, False
    segler = j.get("segments") or []
    alanlar = sorted({k for s in segler if isinstance(s, dict) for k in s})
    sayisal = [s for s in segler if isinstance(s, dict) and isinstance(s.get("start"), (int, float))
               and isinstance(s.get("end"), (int, float)) and s["end"] > 0]
    kelimeler = j.get("words") or [w for s in segler if isinstance(s, dict) for w in (s.get("words") or [])]
    kz = [w for w in kelimeler if isinstance(w, dict) and isinstance(w.get("start"), (int, float))]
    olas = [k for k in ("avg_logprob", "no_speech_prob", "compression_ratio") if k in alanlar]
    ozet = (f"üst alanlar={sorted(j)}  segment={len(segler)}  segment alanları={alanlar}  "
            f"start/end dolu segment={len(sayisal)}  kelime zamanı={len(kz)}  olasılık alanları={olas or 'yok'}")
    return ozet, bool(sayisal), bool(kz)


def form_alanlari(spec):
    try:
        for yol, islemler in spec["paths"].items():
            if "transcri" not in yol:
                continue
            islem = islemler.get("post") or {}
            icerik = islem.get("requestBody", {}).get("content", {})
            sema = (icerik.get("multipart/form-data") or next(iter(icerik.values()), {})).get("schema", {})
            if "$ref" in sema:
                sema = spec["components"]["schemas"][sema["$ref"].split("/")[-1]]
            return yol, sorted(sema.get("properties") or {})
    except Exception:
        pass
    return None, []


def main():
    global ARGS
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    import warnings
    warnings.filterwarnings("ignore")
    a = argparse.ArgumentParser()
    a.add_argument("--wav", help="konuşma içeren wav (yoksa mikrofondan kayıt)")
    a.add_argument("--sure", type=float, default=8)
    a.add_argument("--goster", action="store_true", help="tanınan metinleri de yaz")
    ARGS = a.parse_args()
    with open("config.json", encoding="utf-8-sig") as f:
        cfg = json.load(f)
    stt = Stt(cfg)
    if not stt.adres:
        print("config.json → stt_url boş.")
        return
    m = lambda s: maskele(s, stt.adres)                     # noqa: E731

    yaz("== 1) Servis ve kabul ettiği alanlar ==")
    for yol in ("/health", "/docs", "/openapi.json"):
        d, j = stt.get(yol)
        yaz(f"  GET {yol:14} -> {d}" + ("" if yol != "/health" else f"  {m(str(j)[:120])}"))
        if yol == "/openapi.json" and d == 200 and isinstance(j, dict):
            uc, alanlar = form_alanlari(j)
            yaz(f"  tüm yollar: {sorted(j.get('paths', {}))}")
            yaz(f"  transkripsiyon yolu: {uc}  form alanları: {alanlar or '(şemada yok)'}")

    ses = wav_oku(ARGS.wav) if ARGS.wav else kaydet(ARGS.sure)
    rms = float(np.sqrt(np.mean(np.square(ses)))) if len(ses) else 0.0
    yaz(f"\nses: {len(ses) / ORNEK:.1f} sn, rms {20 * np.log10(max(rms, 1e-9)):.1f} dB")
    if rms < 0.003:
        yaz("  UYARI: ses çok sessiz; mikrofon seçimi için python tools\\ses_teshis.py")

    yaz("\n== 2) Zaman damgası ==")
    denemeler = [
        ("json", {"response_format": "json"}, None),
        ("verbose_json", {"response_format": "verbose_json"}, None),
        ("verbose_json + timestamp_granularities[]=word,segment", {"response_format": "verbose_json"},
         [("timestamp_granularities[]", "word"), ("timestamp_granularities[]", "segment")]),
        ("verbose_json + timestamp_granularities=word", {"response_format": "verbose_json",
                                                         "timestamp_granularities": "word"}, None),
        ("verbose_json + word_timestamps=true", {"response_format": "verbose_json", "word_timestamps": "true"}, None),
    ]
    taban, seg_var, kelime_var = "", False, False
    for ad, ek, coklu in denemeler:
        d, j, sure = stt.coz(ses, ek, coklu)
        if d != 200:
            yaz(f"  [{ad}] -> HTTP {d}  {m(str(j)[:200])}")
            continue
        ozet, s_var, k_var = zaman_raporu(j)
        seg_var, kelime_var = seg_var or s_var, kelime_var or k_var
        if ad == "verbose_json":
            taban = metin(j)
        yaz(f"  [{ad}] {sure:.1f} sn  {ozet}")
        if ARGS.goster:
            yaz(f"      metin: {metin(j)}")
    yaz(f"  SONUÇ: segment start/end {'VAR' if seg_var else 'YOK'}, kelime zamanı {'VAR' if kelime_var else 'YOK'}")

    yaz("\n== 3) prompt modele gidiyor mu ==")
    if not taban:
        yaz("  taban metin alınamadı; prompt denemesi atlandı.")
    else:
        kanit = []
        # a) kasitli yazim kopyalama
        d, j, _ = stt.coz(ses, {"response_format": "verbose_json", "prompt": TUHAF_PROMPT})
        t = metin(j) if d == 200 else ""
        kopya = [k for k in ("ceyra", "cenkins", "ayefares") if k in _sade(t) and k not in _sade(taban)]
        benz = difflib.SequenceMatcher(None, taban, t).ratio() if t else 0
        yaz(f"  a) tuhaf yazımlı prompt: HTTP {d}, tabana benzerlik {benz:.2f}, prompt yazımı kopyalandı: {kopya or 'hayır'}")
        kanit.append(bool(kopya) or (t and benz < 0.9))
        # b) kucuk harf / noktalamasiz stil
        d, j, _ = stt.coz(ses, {"response_format": "verbose_json", "prompt": KUCUK_PROMPT})
        t2 = metin(j) if d == 200 else ""
        def stil(s):
            return sum(c.isupper() for c in s), sum(c in ".,!?" for c in s)
        yaz(f"  b) küçük harfli/noktalamasız prompt: HTTP {d}, büyük harf+noktalama taban {stil(taban)} → {stil(t2)}")
        kanit.append(bool(t2) and stil(t2) != stil(taban))
        # c) sessizlikte prompt
        sessiz = (np.random.RandomState(0).randn(ORNEK * 3) * 0.001).astype("float32")
        d0, j0, _ = stt.coz(sessiz, {"response_format": "verbose_json"})
        d1, j1, _ = stt.coz(sessiz, {"response_format": "verbose_json", "prompt": SESSIZ_PROMPT})
        s0, s1 = metin(j0) if d0 == 200 else "", metin(j1) if d1 == 200 else ""
        sizan = [k for k in _sade(SESSIZ_PROMPT).split() if len(k) > 4 and k in _sade(s1) and k not in _sade(s0)]
        yaz(f"  c) sessizlik: prompt'suz {len(s0)} kr, prompt'lu {len(s1)} kr; prompt kelimesi çıktıya sızdı: {sizan or 'hayır'}")
        kanit.append(bool(sizan))
        if ARGS.goster:
            yaz(f"      taban: {taban}\n      a: {t}\n      b: {t2}\n      sessiz: {s0!r} / {s1!r}")
        evet = sum(bool(k) for k in kanit)
        yaz("  SONUÇ: prompt " + ("modele GİDİYOR" if evet >= 2 else "büyük olasılıkla YOK SAYILIYOR" if evet == 0
                                   else "belirsiz (tek denemede etki var)") + f" ({evet}/3 deneme etkili)")

    with open(CIKTI, "w", encoding="utf-8") as f:
        f.write("\n".join(m(s) for s in SATIRLAR) + "\n")
    yaz(f"\nKaydedildi: {CIKTI} (adres maskeli). İçeriğini bana gönder.")


if __name__ == "__main__":
    main()
