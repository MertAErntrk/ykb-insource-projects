"""
replay.py — Kaydedilmis bir toplantiyi (olcum/okumalar.jsonl + olcum/klipler) MEVCUT boru hattindan cevrimdisi gecirir.
Ag yok: LLM cagrilari kapali (parca isleme saplanir), STT yalniz --stt ile ve yalniz wav'lar icin kullanilir.

Akis: okumalar (UIA ham panel okumasi) ve klipler (STT cikisi) zaman sirasiyla bir Hat'a verilir; zaman sanal saatle
akar (motor.time / yakalayici.time epoch'a sabitlenir, bekleme yok). Cikan son satirlar <cikti>/satirlar.jsonl'e
(klip_no ile) yazilir, ardindan olcum.py metrikleri hesaplanip <cikti>/olcum.json'a yazilir.

Faz1 dikisi: hat degistirilebilir. `Hat` sozlesmesini uygulayan bir sinif (or. konusmaci.py'yi saran) --hat modul:Sinif
ile takilir; kurucusu `ayar` sozlugunu alir ({"kaynak", "ben", "kulaklik"}). Dongu yalniz Hat metotlarini cagirir.

Kullanim:
  python tools/replay.py toplantilar/<klasor> [--stt] [--cikti klasor] [--bas 0 --bit 10] [--hat modul:Sinif]
                         [--kaynak ses|ikisi|altyazi] [--ben ad] [--hoparlor]
"""
import argparse
import contextlib
import ctypes
import datetime as dt
import importlib
import json
import os
import sys
import tempfile
import time
import types
import wave

ARA = os.path.dirname(os.path.abspath(__file__))
KOK = os.path.dirname(ARA)
for _yol in (KOK, ARA):
    if _yol not in sys.path:
        sys.path.insert(0, _yol)
import olcum  # noqa: E402
import etiketle  # noqa: E402


def platform_destegi():
    """yakalayici.py Windows'a ozgu modulleri iceri aktarir; baska platformda (CI, gelistirme) bos taslak."""
    if "uiautomation" not in sys.modules:
        try:
            import uiautomation  # noqa: F401
        except Exception:
            sys.modules["uiautomation"] = types.ModuleType("uiautomation")
    if not hasattr(ctypes, "windll"):
        ctypes.windll = types.SimpleNamespace(user32=types.SimpleNamespace(SystemParametersInfoW=lambda *a: 0))


# ---------------------------------------------------------------- sanal saat

class SanalSaat:
    def __init__(self):
        self.t = 0.0


class SahteZaman:
    """`time` modulu yerine: time() sanal saati verir, gerisi (sleep, perf_counter...) gercek modulden gelir."""

    def __init__(self, saat):
        self._saat = saat

    def time(self):
        return self._saat.t

    def __getattr__(self, ad):
        return getattr(time, ad)


def saat_kaymasi(klipler):
    """Kaydin yerel saat dilimi bu makineninkinden farkliysa (klip ts'si ile epoch'tan turetilen ts arasi saat
    farki) saniye cinsinden duzeltme; ayni dilimde 0."""
    farklar = []
    for k in klipler.values():
        sn = olcum._sn(k.get("ts"))
        if sn is not None and k.get("bas"):
            yerel = dt.datetime.fromtimestamp(k["bas"])
            farklar.append((sn - (yerel.hour * 3600 + yerel.minute * 60 + yerel.second) + 43200) % 86400 - 43200)
    if not farklar:
        return 0
    farklar.sort()
    f = farklar[len(farklar) // 2]
    return round(f / 3600) * 3600 if abs(f) > 1800 else 0


# ---------------------------------------------------------------- hat sozlesmesi

class Hat:
    """Replay'in cagirdigi arayuz. Faz1 hatti da bunu uygular. `an` her zaman epoch saniyesidir."""

    def __init__(self, ayar):
        self.ayar = ayar

    def baslat(self, klasor):
        """klasor: gecici calisma klasoru."""

    def okuma(self, an, gorunen):
        """Bir UIA panel okumasi: gorunen = [(konusmaci, metin), ...] (okuma bitisi `an`)."""

    def klip(self, an, kayit, segmentler):
        """Bir klibin STT sonucu `an`'da geldi. kayit: klipler.jsonl satiri; segmentler: [{start,end,text,...}]."""

    def kontrol(self, an):
        """Her olaydan sonra (gercek dongudeki motor.kontrol)."""

    def bitir(self):
        """-> son transkript satirlari [{id, ts, speaker, text, raw, kaynak, klip_no}] (klip_no yoksa None)."""
        return []

    def kapat(self):
        """Yama/kaynak temizligi."""


class MevcutHat(Hat):
    """Bugunku hat: Yakalayici._isle -> Motor.altyazi_satiri; klipler SesServisi.adaylar_uret + yayim filtreleri
    (tekrar/eko) -> Motor.ses_satiri. Parca isleme (LLM) saplanir."""

    def baslat(self, klasor):
        platform_destegi()
        import llm
        import motor as motor_mod
        import sozluk as sozluk_mod
        import ses as ses_mod
        import yakalayici as yak_mod
        self._modul = (llm, motor_mod, yak_mod)
        self._eski = (llm.GUNLUK_FN, llm.UYARI_FN, llm.SON_HATA, motor_mod.time, yak_mod.time)
        self.saat = SanalSaat()
        sahte = SahteZaman(self.saat)
        motor_mod.time, yak_mod.time = sahte, sahte
        self.kayma = self.ayar.get("saat_kaymasi", 0)
        sozluk_yolu = os.path.join(klasor, "sozluk.json")          # bos sozluk: yerel sozluk.json'a dokunulmaz
        with open(sozluk_yolu, "w", encoding="utf-8") as f:
            json.dump({"terimler": [], "aliaslar": {}}, f)
        self._satirlar = []                                        # (satir referansi, klip_no)
        self._aktif = None
        self.motor = motor_mod.Motor(os.path.join(klasor, "toplanti"), "Replay", dt.date.today(),
                                     sozluk_mod.Sozluk(sozluk_yolu), [], "", duzelt=False, olay=self._olay,
                                     kaynak=self.ayar.get("kaynak", "ses"), ben=self.ayar.get("ben"))
        self.motor._isle = lambda veri, duzelt: None               # LLM yok: duzeltme/ozet atlanir
        self.motor.kulaklik = self.ayar.get("kulaklik")
        self.yak = yak_mod.Yakalayici()
        self.servis = ses_mod.SesServisi("", satir_fn=self._ses_satiri)
        self.servis.kulaklik = self.ayar.get("kulaklik") is not False

    def _olay(self, tip, veri):
        if tip == "satir":
            klip_no = self._aktif if veri.get("kaynak") == "ses" else None
            self._satirlar.append((veri, klip_no))

    def _ses_satiri(self, ts, kim, metin, akis):
        return self.motor.ses_satiri(ts, kim, metin, akis)

    def _dt(self, an):
        return dt.datetime.fromtimestamp(an + self.kayma)

    def okuma(self, an, gorunen):
        self.saat.t = an
        for s in self.yak._isle([tuple(x[:2]) for x in gorunen if len(x) >= 2], self._dt(an).strftime("%H:%M:%S")):
            self.motor.altyazi_satiri(s)

    def klip(self, an, kayit, segmentler):
        self.saat.t = an
        sure = kayit.get("sure") or 0.0
        adaylar = self.servis.adaylar_uret(segmentler, sure, self._dt(kayit.get("bas") or an),
                                           kayit.get("akis") or "loopback")
        self._aktif = kayit["no"]
        try:
            self.servis._sirali_yay(None, adaylar)
        finally:
            self._aktif = None

    def kontrol(self, an):
        self.saat.t = an
        self.motor.kontrol()

    def bitir(self):
        for s in self.yak.bitir():
            self.motor.altyazi_satiri(s)
        self.motor.bitir()
        return [dict(s, klip_no=no) for s, no in self._satirlar]

    def kapat(self):
        if getattr(self, "_eski", None):                          # baslat yarida kaldiysa da yamalar geri alinir
            llm, motor_mod, yak_mod = self._modul
            llm.GUNLUK_FN, llm.UYARI_FN, llm.SON_HATA, motor_mod.time, yak_mod.time = self._eski
            self._eski = None
        with contextlib.suppress(Exception):
            self.motor.kapat()


# ---------------------------------------------------------------- girdi

def wav_oku(yol):
    with wave.open(yol, "rb") as w:
        import numpy as np
        ham = w.readframes(w.getnframes())
    return np.frombuffer(ham, dtype="<i2").astype("float32") / 32767.0


def segmentler_al(kayit, klasor, stt=None):
    """Klibin STT segmentleri: --stt ile wav yeniden cozulur; yoksa kayitli segmentler; o da yoksa ham metin tek segment."""
    if stt is not None:
        return stt.coz(wav_oku(os.path.join(klasor, "olcum", "klipler", f"{kayit['no']:04d}.wav")))
    if kayit.get("segmentler"):
        return kayit["segmentler"]
    if kayit.get("raw"):
        return [{"start": 0.0, "end": kayit.get("sure") or 0.0, "text": kayit["raw"]}]
    return []


def stt_istemci():
    """config.json'daki STT ayarlariyla SttIstemci (yalniz --stt)."""
    import ayar as ayar_mod
    import ses as ses_mod
    c = ayar_mod.ayar_oku()
    return ses_mod.SttIstemci(c.get("stt_url", ""), model=c.get("stt_model", "whisper"), api_key=c.get("stt_key", ""),
                              proxy=c.get("stt_proxy", True), verify=ayar_mod.tls_dogrulama(c))


def hat_sinifi_yukle(ad):
    """'modul:Sinif' -> sinif (bos: MevcutHat)."""
    if not ad:
        return MevcutHat
    modul, _, sinif = ad.partition(":")
    return getattr(importlib.import_module(modul), sinif or "Hat")


def kos(klasor, hat_sinifi=None, stt=None, bas=None, bit=None, cikti=None, ayar=None, ben=None, yazdir=print):
    """Kaydi hattan gecirir. hat_sinifi: Hat sozlesmesini uygulayan sinif (kurucu ayar sozlugu alir; varsayilan
    MevcutHat). Dondurur: (metrikler, cikti klasoru)."""
    klipler = olcum.klipler_oku(klasor)
    okumalar = olcum.jsonl_oku(os.path.join(klasor, "olcum", "okumalar.jsonl"))
    if not klipler and not okumalar:
        raise SystemExit("olcum/ kaydı yok (klipler.jsonl ve okumalar.jsonl boş)")
    ilk = min([k["bas"] for k in klipler.values() if k.get("bas")] + [o["bas_epoch"] for o in okumalar] or [0])
    t0, t1 = etiketle.zaman_coz(bas, ilk), etiketle.zaman_coz(bit, ilk)
    if t0 is not None or t1 is not None:
        t0 = t0 if t0 is not None else 0
        t1 = t1 if t1 is not None else float("inf")
        klipler = {n: k for n, k in klipler.items() if t0 <= (k.get("bas") or 0) < t1}
        okumalar = [o for o in okumalar if t0 - 30 <= o["bit_epoch"] < t1 + 15]
    meta = {}
    with contextlib.suppress(Exception):
        with open(os.path.join(klasor, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
    ayar = dict({"kaynak": meta.get("kaynak") or ("ses" if klipler else "altyazi"), "ben": ben,
                 "kulaklik": None, "saat_kaymasi": saat_kaymasi(klipler)}, **(ayar or {}))
    cikti = cikti or tempfile.mkdtemp(prefix="replay_")
    os.makedirs(cikti, exist_ok=True)

    olaylar = [(o["bit_epoch"], 0, "okuma", o) for o in okumalar]
    olaylar += [((k.get("bit") or 0) + (k.get("stt_sure") or 0), 1, "klip", k) for k in klipler.values()]
    olaylar.sort(key=lambda e: (e[0], e[1], e[3].get("no") or e[3].get("okuma_no") or 0))

    hat = (hat_sinifi or MevcutHat)(ayar)
    try:
        hat.baslat(cikti)
        for an, _, tur, v in olaylar:
            if tur == "okuma":
                hat.okuma(an, v.get("gorunen") or [])
            else:
                hat.klip(an, v, segmentler_al(v, klasor, stt))
            hat.kontrol(an)
        satirlar = hat.bitir()
    finally:
        hat.kapat()
    with open(os.path.join(cikti, "satirlar.jsonl"), "w", encoding="utf-8") as f:
        for s in satirlar:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    m = olcum.hesapla(klipler, olcum.gercek_oku(klasor), satirlar, ben=ben)
    m["replay"] = {"okuma": len(okumalar), "klip": len(klipler), "stt": stt is not None,
                   "hat": type(hat).__name__}
    with open(os.path.join(cikti, "olcum.json"), "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    yazdir(olcum.ozet_metin(m))
    yazdir(f"çıktı: {cikti}")
    return m, cikti


def main(argv=None):
    ap = argparse.ArgumentParser(description="Kaydı mevcut boru hattından çevrimdışı geçir")
    ap.add_argument("klasor", help="toplantı klasörü (içinde olcum/ var)")
    ap.add_argument("--stt", action="store_true", help="wav'ları config.json'daki STT ile yeniden çöz")
    ap.add_argument("--cikti", help="çıktı klasörü (varsayılan: geçici klasör)")
    ap.add_argument("--bas", help="başlangıç: SS:DD[:SN] ya da ilk kayıttan dakika")
    ap.add_argument("--bit", help="bitiş: SS:DD[:SN] ya da ilk kayıttan dakika")
    ap.add_argument("--hat", help="modul:Sinif (Hat sözleşmesi); varsayılan mevcut hat")
    ap.add_argument("--kaynak", choices=("ses", "ikisi", "altyazi"), help="motor kaynağı (varsayılan: kaydın meta.json'u)")
    ap.add_argument("--ben", help="mikrofon sahibinin adı")
    ap.add_argument("--hoparlor", action="store_true", help="kulaklık yok: eko tekilleştirme açık")
    a = ap.parse_args(argv)
    ayar = {"kulaklik": False} if a.hoparlor else {}
    if a.kaynak:
        ayar["kaynak"] = a.kaynak
    kos(a.klasor, hat_sinifi_yukle(a.hat), stt_istemci() if a.stt else None, a.bas, a.bit, a.cikti, ayar, a.ben)
    return 0


if __name__ == "__main__":
    sys.exit(main())
