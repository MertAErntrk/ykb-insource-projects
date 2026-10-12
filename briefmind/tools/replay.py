"""
replay.py — Kaydedilmis bir toplantiyi (olcum/okumalar.jsonl + olcum/klipler) boru hattindan cevrimdisi gecirir.
Ag yok: LLM cagrilari kapali (parca isleme saplanir), STT yalniz --stt ile (wav'lar yeniden cozulur; devirde yeniden kesme).

Akis: okumalar (UIA ham panel okumasi, C2) ve klipler (STT cikisi, C1) zaman sirasiyla bir Hat'a verilir; zaman sanal saatle
akar (saat.simdi sanal saate baglanir, bekleme yok). Cikan son satirlar <cikti>/satirlar.jsonl'e (klip_no ile) yazilir,
ardindan olcum.py metrikleri hesaplanip <cikti>/olcum.json'a yazilir.

Hatlar (--hat):
  Faz1Hat (varsayilan, MIMARI_v3 §3): okuma -> Motor.okuma_ekle; klip -> SesServisi.klip_olayi_uret + sirali yayim ->
      Motor.ses_olayi; konusmaci karari yalniz zamandan (konusmaci.py). Devirde yeniden kesme: --stt ve klip wav'i varsa
      gercek (wav enerjisi + STT), yoksa Motor 'A -> B' kararina duser. Cikti satirlarinin `speaker`i gosterim kurali
      (spk_guven < 0.7 -> "Ad?") uygulanmis halidir (olcum.py kesin / belirsiz ayrimi icin); ham ad `speaker_ham`.
  MevcutHat (--eski ya da --hat replay:MevcutHat): Faz 0 hatti; Yakalayici._isle + Motor.ses_satiri uyum katmani.
      Okuma olayi yaymadigindan konusmaci cizelgesi olusmaz; yalniz eski kayitlari karsilastirmak icin durur.
Baska hat: `Hat` sozlesmesini uygulayan bir sinif --hat modul:Sinif ile takilir; kurucusu `ayar` sozlugunu alir
({"kaynak", "ben", "kulaklik", "katilimcilar", "stt", "klasor"}). Dongu yalniz Hat metotlarini cagirir.

Duzeltme (Faz 2, --duzelt): Faz1Hat parca kapaninca terim/ad duzeltmesini de calistirir (duzelt.duzelt: LLM yalniz onerir,
deterministik kapi karar verir; ozetleme yine saplanir). Varsayilan onerici SAHTE ve AGRESIF (SahteOneri: terim listesine
benzeyen her kelimeyi oneririr; kapinin ne kadarini tuttugunu olcer, ag yok); --llm ile gercek LLM (config.json). Terim kaynagi:
--terimler "A,B" ve/ya da --davet dosya (davet metni). Cikti olcum.json "duzeltme": duzeltilen / bozulan kelime (zemin gercegine
gore; bozulan = dogru kelimenin yanlis olmasi, hedef 0), kabul, belirsiz; replay.duzelt: oneri / kabul / red nedenleri.

Uzun pencere (Faz 3, --uzun, --stt gerekir): Faz1Hat gercek ses.UzunPencere ile kosar. Pencere sesi kayitli klip wav'larindan
akisin ornek sayaci (ornek_bas) uzerinde yeniden kurulur (klipler arasi sessizlik sifir); pencere STT'ye gider, metin
uzun.pencere_hizala ile kisa kliplere dagitilir (Motor ses_olayi 'uzun'). Cikti olcum.json "uzun": kisa ve uzun WER, goreli
degisim (kapi: hedef -%25, alt sinir -%10; altinda uzun pencere kapali kalmali), pencere / STT istek sayisi ve saatlik GPU
yuku (kisa + uzun). Kisa WER, uzun pencerenin degistirdigi satirlarin 'raw_kisa' (ozgun kisa metin) haliyle hesaplanir
(--duzelt ile birlikte kisa taraf duzeltmesiz kalir: karsilastirma icin --duzelt'siz kosun).

Kullanim:
  python tools/replay.py toplantilar/<klasor> [--stt [--uzun]] [--cikti klasor] [--bas 0 --bit 10] [--hat modul:Sinif | --eski]
                         [--ben ad] [--katilimcilar "Ad1,Ad2"] [--hoparlor]
                         [--duzelt [--llm] [--terimler "T1,T2"] [--davet dosya]]
"""
import argparse
import contextlib
import ctypes
import datetime as dt
import importlib
import json
import os
import re
import sys
import tempfile
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


# ---------------------------------------------------------------- sahte onerici (--duzelt, --llm yok)

def mesaj_coz(mesajlar):
    """duzelt.mesajlar() kullanici mesaji -> (terimler, adlar, [(satir id, metin)])."""
    icerik = mesajlar[-1]["content"]
    terim = re.search(r"^TERİMLER:\s*(.*)$", icerik, re.M)
    ad = re.search(r"^ADLAR:\s*(.*)$", icerik, re.M)
    liste = lambda m: [x.strip() for x in m.group(1).split(";") if x.strip() and x.strip() != "-"] if m else []
    satirlar = re.findall(r"^\[(-?\d+)\]\s(.*)$", icerik.split("SATIRLAR:", 1)[-1], re.M)
    return liste(terim), liste(ad), satirlar


class SahteOneri:
    """duzelt.duzelt(sor_fn=): AGRESIF sahte LLM. Her kelime icin terim/ad listesinde en yakin ogeyi (normalize uzaklik
    <= 0.5, ayni degil) onerir; kelime onerilen ogeyle basliyorsa eki korur (Jira'ya), aksi halde eksiz yazar. Gercek
    modelden cok daha cok yanlis oneri uretir: kapinin onleyemedigi zarar bu hatta gorulur. Her cagri (mesajlar, sema)."""
    ESIK = 0.5

    def __init__(self):
        self.cagri = 0

    def __call__(self, mesajlar, sema):
        import duzelt
        self.cagri += 1
        terimler, adlar, satirlar = mesaj_coz(mesajlar)
        liste = list(terimler) + [p for a in adlar for p in a.split() if len(p) >= 3]
        oneriler = []
        for sid, metin in satirlar:
            for w in re.findall(r"[^\W\d_]{4,}", metin):
                en = min(((duzelt.uzaklik(w, t), t) for t in liste if " " not in t), default=None)
                if en is None or not 0 < en[0] <= self.ESIK or en[1].lower() == w.lower():
                    continue
                t = en[1]
                yeni = t + "'" + w[len(t):] if w.lower().startswith(t.lower()) and len(w) > len(t) else t
                neden = "ad" if t not in terimler else "terim"
                oneriler.append({"satir": int(sid), "eski": w, "yeni": yeni, "neden": neden})
        return {"oneriler": oneriler[:duzelt.EN_FAZLA_ONERI]}


# ---------------------------------------------------------------- hat sozlesmesi

class Hat:
    """Replay'in cagirdigi arayuz. `an` her zaman epoch saniyesidir."""

    def __init__(self, ayar):
        self.ayar = ayar

    def baslat(self, klasor):
        """klasor: gecici calisma klasoru."""

    def okuma_olayi(self, okuma):
        """Kayitli UIA okumasinin tamami ({okuma_no, bas_epoch, bit_epoch, grup, gorunen}); varsayilan: eski `okuma`."""
        self.okuma(okuma.get("bit_epoch") or okuma.get("bas_epoch") or 0, okuma.get("gorunen") or [])

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
    """ESKI (Faz 0) hat: Yakalayici._isle -> Motor.altyazi_satiri (artik yalniz canli iz); klipler SesServisi.adaylar_uret
    + yayim filtreleri (tekrar/eko) -> Motor.ses_satiri (uyum katmani: bas=bit, eksi klip_no). Parca isleme (LLM) saplanir.
    Okuma olayi yaymadigi icin konusmaci cizelgesi olusmaz; Faz1Hat yerine gecmez."""

    def baslat(self, klasor):
        platform_destegi()
        import llm
        import motor as motor_mod
        import ses as ses_mod
        import yakalayici as yak_mod
        import saat as saat_mod
        self._modul = (llm, motor_mod, yak_mod)
        self._eski = (llm.GUNLUK_FN, llm.UYARI_FN, llm.SON_HATA)
        self.saat = SanalSaat()
        self._saat_geri = saat_mod.sanal_kur(lambda: self.saat.t)   # simdi() sanal saat, duvar ofseti 0
        self.kayma = self.ayar.get("saat_kaymasi", 0)
        self._satirlar = []                                        # (satir referansi, klip_no)
        self._aktif = None
        self.motor = motor_mod.Motor(os.path.join(klasor, "toplanti"), "Replay", dt.date.today(),
                                     None, [], "", duzelt=False, olay=self._olay,
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
            llm = self._modul[0]
            llm.GUNLUK_FN, llm.UYARI_FN, llm.SON_HATA = self._eski
            self._saat_geri()
            self._eski = None
        with contextlib.suppress(Exception):
            self.motor.kapat()


class Faz1Hat(Hat):
    """Yeni hat (MIMARI_v3 §3): okumalar Motor.okuma_ekle'ye (C2: bas/bit epoch, grup, gorunen), klipler (kayitli ya da
    --stt ile yeniden cozulmus STT metni) SesServisi.klip_olayi_uret + sirali yayim uzerinden Motor.ses_olayi'na (C1).
    Konusmaci karari yalniz zamandan gelir; altyazi metni transkripte girmez. Parca isleme (LLM) saplanir.
    Yeniden kesme (devir): --stt varsa ve klibin wav'i + zarfi kayitliysa gercek (zarfin wav enerjisiyle hizalanmasi, an +- 1 sn
    icinde en dusuk enerjili 100 ms blok, iki parca STT'ye); aksi halde Motor 'A -> B' kararina duser.
    bitir(): son satirlar; `speaker` gosterim kurali uygulanmis hali ("Ad?" = spk_guven < 0.7; olcum.py kesin / belirsiz
    ayrimi bununla yapar), ham ad `speaker_ham`; yeniden kesilen klibin parcalari ust klibin `klip_no`'sunu tasir."""

    COCUK_NO = 10_000_000                      # yeniden kesme parcalarinin klip_no tabani (kayit numaralariyla carpismaz)

    def baslat(self, klasor):
        platform_destegi()
        import llm
        import motor as motor_mod
        import ses as ses_mod
        import saat as saat_mod
        self._modul = (llm, motor_mod, ses_mod)
        self._eski = (llm.GUNLUK_FN, llm.UYARI_FN, llm.SON_HATA)
        self.saat = SanalSaat()
        self._saat_geri = saat_mod.sanal_kur(lambda: self.saat.t)   # simdi() sanal saat, duvar ofseti 0
        self._kayit = {}                                           # klip no -> klipler.jsonl kaydi (yeniden kesme)
        self._kesilen = set()
        self._cocuk = {}                                           # parca klip_no -> ust klip_no
        self._bekleyen = []                                        # kontrol()'de yayilacak parca olaylari
        self._cocuk_no = self.COCUK_NO
        self._klasor = self.ayar.get("klasor")
        # terimler=None: Motor toplanti klasorunde bos, ozel bir terim havuzu kurar (uygulamanin terimler/ dosyasina dokunulmaz)
        duzelt = bool(self.ayar.get("duzelt"))
        self.motor = motor_mod.Motor(os.path.join(klasor, "toplanti"), "Replay", dt.date.today(), None,
                                     list(self.ayar.get("katilimcilar") or []), "", duzelt=duzelt, kaynak="ses",
                                     ben=self.ayar.get("ben"), esikler=self.ayar.get("esikler"),
                                     davet={"govde": self.ayar["davet"], "ekler": []} if self.ayar.get("davet") else None)
        self.sahte_oneri = None
        if duzelt:                                                 # parca kapaninca yalniz duzeltme (ozet saplanir)
            self.motor._isle = self._isle_duzelt
            if not self.ayar.get("llm"):
                self.sahte_oneri = self.motor.duzelt_sor_fn = SahteOneri()
            self.motor.terimler.ekle(self.motor.seri, list(self.ayar.get("terimler") or []), "elle")
            self.motor.terimleri_hazirla()                         # davet metninden terimler
        else:
            self.motor._isle = lambda veri, duzelt: None           # LLM yok: duzeltme/ozet atlanir
        self.motor.yeniden_kes_fn = self._yeniden_kes
        self.servis = ses_mod.SesServisi("", olay_fn=self.motor.ses_olayi, uzun_pencere=False)
        if self.ayar.get("kulaklik") in (True, False):             # pasif tespit kaydedilmedi: elle (--hoparlor)
            self.motor.ses_olayi({"tur": "kulaklik", "durum": self.ayar["kulaklik"], "guven": 1.0})
        self.uzun = None
        if self.ayar.get("uzun"):                                  # Faz 3: gercek UzunPencere, ses kayitli wav'lardan
            stt = self.ayar.get("stt")
            if stt is None:
                raise SystemExit("--uzun için --stt gerekir (pencereler kayıtlı wav'lardan yeniden çözülür)")
            self.uzun = self.servis.uzun_ac()
            self.uzun._coz_fn = lambda ses_, ipucu: stt.coz(ses_, ipucu=ipucu) if ipucu else stt.coz(ses_)
            self.uzun._ses_fn = self._uzun_ses
            self.uzun._saat = lambda: self.saat.t
            self._wav_onbellek = {}
            self._akis_wav = {}                                    # akis -> [(wav baslangici epoch, sure, yol)]

    def _isle_duzelt(self, veri, duzelt):
        """Motor._isle yerine: yalniz terim/ad duzeltmesi (kapali oneri LLM'i ya da --llm gercek); ozet yok."""
        if duzelt and veri["token"] >= self._modul[1].DUZELT_MIN_TOKEN:
            with contextlib.suppress(Exception):
                self.motor._duzelt_parca(veri)
        with self.motor._iz_kilit:
            self.motor.parca_yaz(veri)
        return None

    def duzelt_ozeti(self):
        """duzelt_log.jsonl (kabul/red kaydi, satir metni yok) -> {"oneri", "kabul", "red": {sebep: sayi}} | None."""
        kayitlar = olcum.jsonl_oku(os.path.join(self.motor.klasor, "duzelt_log.jsonl"))
        if not self.ayar.get("duzelt"):
            return None
        red = {}
        for k in kayitlar:
            for r in k.get("red") or []:
                red[r.get("sebep", "?")] = red.get(r.get("sebep", "?"), 0) + 1
        return {"oneri": sum(k.get("oneri", 0) for k in kayitlar), "kabul": sum(len(k.get("kabul") or []) for k in kayitlar),
                "red": dict(sorted(red.items())), "llm": "gercek" if self.ayar.get("llm") else "sahte"}

    def okuma_olayi(self, okuma):
        self.saat.t = okuma.get("bit_epoch") or okuma.get("bas_epoch") or self.saat.t
        gorunen = [[g[0], g[1]] for g in (okuma.get("gorunen") or []) if len(g) >= 2 and isinstance(g[1], str)]
        self.motor.okuma_ekle({"tur": "okuma", "okuma_no": okuma.get("okuma_no"), "bas_epoch": okuma["bas_epoch"],
                               "bit_epoch": okuma.get("bit_epoch", okuma["bas_epoch"]),
                               "grup": okuma.get("grup", True), "gorunen": gorunen})

    def okuma(self, an, gorunen):                                  # eski imza (Hat sozlesmesi)
        self.okuma_olayi({"bas_epoch": an, "bit_epoch": an, "gorunen": gorunen})

    def _klip_olayi(self, no, kayit, segmentler, ust=None):
        p = {"kaynak": kayit.get("akis") or "loopback", "klip_no": no, "no": None,
             "bas_epoch": kayit["bas"], "bit_epoch": kayit.get("bit") or kayit["bas"],
             "rms": kayit.get("rms") or 0.0, "zarf": kayit.get("zarf") or [], "ust_klip": ust}
        return self.servis.klip_olayi_uret(p, segmentler)

    def klip(self, an, kayit, segmentler):
        self.saat.t = an
        no = kayit["no"]
        self._kayit[no] = kayit
        if self.uzun is not None:
            self._uzun_klip(no, kayit)
        ev = self._klip_olayi(no, kayit, segmentler, kayit.get("ust_klip"))
        if ev is not None:
            self.servis._sirali_yay(None, [ev])
        elif self.uzun is not None:
            self.servis._uzun_metin(no, "")

    def kontrol(self, an):
        self.saat.t = an
        self._cocuklari_yay()
        if self.uzun is not None:
            for akis in list(self._akis_wav):
                self.uzun.ilerle(akis, an - self.UZUN_GECIKME_SN)
            self.uzun.adim()                                       # STT burada (cevrimdisi: bloklar)
        self.motor.kontrol()

    # --- Faz 3 uzun pencere (--uzun): sanal saat, pencere sesi kayitli wav'lardan ---
    UZUN_GECIKME_SN = 15.0                     # kisa STT sonucu en gec bu kadar sonra gelir: ilerleme = an - bu
    ONYUK_SN = 0.3                             # wav'in sesli araliktan once basladigi pay (ses.ONYUK_BLOK)

    def _uzun_klip(self, no, kayit):
        akis = kayit.get("akis") or "loopback"
        self.uzun.klip_kaydet(akis, no, kayit["bas"], kayit.get("bit") or kayit["bas"], kayit.get("rms"),
                              kayit.get("ust_klip"))
        yol = os.path.join(self._klasor or "", "olcum", "klipler", f"{no:04d}.wav")
        if not self._klasor or not os.path.exists(yol):
            return
        ob = kayit.get("ornek_bas")
        capa = self._capa(akis, kayit)
        bas = (capa + ob / 16000.0) if (ob is not None and capa is not None) else kayit["bas"] - self.ONYUK_SN
        sure = kayit.get("sure") or max(0.0, (kayit.get("bit") or kayit["bas"]) - kayit["bas"]) + self.ONYUK_SN + 0.55
        self._akis_wav.setdefault(akis, []).append((bas, float(sure), yol))

    def _capa(self, akis, kayit):
        """Akisin sifirinci orneginin epoch'u (kayitta yok): ilk kayitli klipten (bas - ornek_bas/16k - on-yuk)."""
        c = getattr(self, "_capalar", None)
        if c is None:
            c = self._capalar = {}
        if akis not in c and kayit.get("ornek_bas") is not None:
            c[akis] = kayit["bas"] - kayit["ornek_bas"] / 16000.0 - self.ONYUK_SN
        return c.get(akis)

    def _uzun_ses(self, akis, bas, bit):
        """Pencere sesi: akisin kayitli klip wav'lari zaman cizgisine yerlestirilir (aralar sessiz). Hic klip yoksa None."""
        import numpy as np
        n = int(round((bit - bas) * 16000))
        if n <= 0:
            return None
        cikti = np.zeros(n, dtype="float32")
        dolu = np.zeros(n, dtype=bool)                             # ortusen wav'larda (on-yuk) ilk yazilan kalir
        var = False
        for w_bas, sure, yol in self._akis_wav.get(akis, ()):
            if w_bas >= bit or w_bas + sure <= bas:
                continue
            ses_ = self._wav_onbellek.get(yol)
            if ses_ is None:
                ses_ = self._wav_onbellek[yol] = wav_oku(yol)
                while len(self._wav_onbellek) > 64:
                    self._wav_onbellek.pop(next(iter(self._wav_onbellek)))
            i0 = int(round((w_bas - bas) * 16000))
            a, b = max(0, i0), min(n, i0 + len(ses_))
            if b > a:
                bos = ~dolu[a:b]
                cikti[a:b][bos] = ses_[a - i0:b - i0][bos]
                dolu[a:b] = True
                var = True
        return cikti if var else None

    def uzun_bitir(self):
        """Yakalama bitti: kalan pencereler cozulur (Motor.bitir'den once)."""
        if self.uzun is None:
            return
        self.uzun.bitir_isaretle()
        self.uzun.adim(zorla=True)

    def _cocuklari_yay(self):
        """Yeniden kesme parcalari. SesServisi._olay_yay parcayi (ust_klip) tekrar suzgecinden zaten muaf tutar;
        ayar['cocuk_tekrar_muaf'] (varsayilan acik) eski kayitlarla karsilastirma icin ayrica _tekrar_mi'yi kapatir."""
        while self._bekleyen:
            ev = self._bekleyen.pop(0)
            gercek = self.servis._tekrar_mi
            if self.ayar.get("cocuk_tekrar_muaf", True):
                self.servis._tekrar_mi = lambda *a, **k: False
            try:
                self.servis._sirali_yay(None, [ev])
            finally:
                self.servis._tekrar_mi = gercek

    # --- yeniden kesme (Motor.yeniden_kes_fn): SesServisi.yeniden_kes sozlesmesi, kayitli wav + zarf uzerinde ---
    def _yeniden_kes(self, klip_no, an):
        stt = self.ayar.get("stt")
        kayit = self._kayit.get(klip_no)
        if stt is None or kayit is None or klip_no in self._kesilen or not self._klasor:
            return False
        zarf = [float(z) for z in (kayit.get("zarf") or [])]
        yol = os.path.join(self._klasor, "olcum", "klipler", f"{klip_no:04d}.wav")
        if not zarf or not os.path.exists(yol):
            return False
        ses_mod = self._modul[2]
        try:
            ses_ = wav_oku(yol)
            kes = kesim_noktasi(ses_, zarf, an - kayit["bas"], ses_mod.KES_ARALIK_SN, ses_mod.KES_MIN_PARCA_SN)
            if kes is None:
                return False
            (ofs, kk) = kes
            idx = int((ofs + kk + 0.5) * 0.1 * ses_mod.ORNEK)
            seg_a, seg_b = stt.coz(ses_[:idx]), stt.coz(ses_[idx:])
        except Exception:
            return False
        kes_an = kayit["bas"] + (kk + 0.5) * 0.1
        self._kesilen.add(klip_no)
        for seg, bas, bit in ((seg_a, kayit["bas"], kes_an), (seg_b, kes_an, kayit.get("bit") or kayit["bas"])):
            self._cocuk_no += 1
            self._cocuk[self._cocuk_no] = klip_no
            alt = dict(kayit, bas=bas, bit=bit, zarf=[])
            if self.uzun is not None:
                self.uzun.klip_kaydet(alt.get("akis") or "loopback", self._cocuk_no, bas, bit, alt.get("rms"), klip_no)
            ev = self._klip_olayi(self._cocuk_no, alt, seg, ust=klip_no)
            if ev is not None:
                self._bekleyen.append(ev)
            elif self.uzun is not None:
                self.servis._uzun_metin(self._cocuk_no, "")
        return True

    def bitir(self):
        self._cocuklari_yay()
        self.uzun_bitir()
        self._cocuklari_yay()
        self.motor.bitir()
        cikti = []
        for s in self.motor.tum_satirlar():
            d = dict(s)
            d["speaker_ham"] = s.get("speaker")
            d["speaker"] = olcum_adi(s)
            if d.get("klip_no") in self._cocuk:                    # parca satiri ust klibin etiketine yazilir
                d["cocuk_klip_no"] = d["klip_no"]
                d["klip_no"] = self._cocuk[d["klip_no"]]
            cikti.append(d)
        return cikti

    def kapat(self):
        if getattr(self, "_eski", None):                          # baslat yarida kaldiysa da yamalar geri alinir
            llm = self._modul[0]
            llm.GUNLUK_FN, llm.UYARI_FN, llm.SON_HATA = self._eski
            self._saat_geri()
            self._eski = None
        with contextlib.suppress(Exception):
            self.motor.kapat()


def olcum_adi(satir, esik=0.7):
    """Satirin olcum icin konusmacisi: spk_guven < esik olan kisi adi "Ad?" (metin.gorunen_ad ile ayni kural; "?" ve
    birlesik adlar ("A -> B", "A / B") oldugu gibi, oda etiketi tavan 0.6 oldugundan "Ad?")."""
    ad = str(satir.get("speaker") or "?").strip()
    if ad in ("", "?") or " → " in ad or " / " in ad or ad.endswith("?"):
        return ad or "?"
    g = satir.get("spk_guven")
    return ad + "?" if isinstance(g, (int, float)) and g < esik else ad


def kesim_noktasi(ses_, zarf, an_goreli, aralik_sn, min_parca_sn):
    """Yeniden kesme blogu: zarf (klibin sesli araliginin 100 ms RMS'leri) wav'in 100 ms RMS dizisiyle hizalanir (wav
    on-yuk + sondaki sessizligi icerir); an_goreli (sesli baslangica gore sn) +- aralik_sn icindeki en dusuk enerjili blok.
    Dondurur (wav icinde sesli aralik ofseti [blok], kesim blogu [sesli araliga gore]) | None (parca < min_parca_sn ya da
    blok sessiz degil: ses.kesim_sessiz_mi)."""
    import numpy as np
    blok = 1600
    nb = len(ses_) // blok
    if nb < len(zarf) or len(zarf) < 2:
        return None
    rw = np.sqrt(np.mean(np.square(ses_[:nb * blok]).reshape(nb, blok), axis=1))
    z = np.asarray(zarf, dtype="float64")
    en_ofs, en_puan = 0, -2.0
    for ofs in range(0, nb - len(z) + 1):
        w = rw[ofs:ofs + len(z)]
        if np.std(w) < 1e-9 or np.std(z) < 1e-9:
            puan = -1.0
        else:
            puan = float(np.corrcoef(w, z)[0, 1])
        if puan > en_puan:
            en_ofs, en_puan = ofs, puan
    sure = len(z) * 0.1
    k0 = int(np.ceil(max(0.0, an_goreli - aralik_sn) / 0.1))
    k1 = int(min(sure, an_goreli + aralik_sn) // 0.1)
    adaylar = [(round(float(z[k]), 6), abs((k + 0.5) * 0.1 - an_goreli), k) for k in range(max(0, k0), min(len(z), k1))]
    if not adaylar:
        return None
    kk = min(adaylar)[2]
    c = (kk + 0.5) * 0.1
    if c < min_parca_sn or sure - c < min_parca_sn:
        return None
    import ses as ses_mod
    if not ses_mod.kesim_sessiz_mi(z[kk], z):
        return None                                  # sessiz blok yok (SesServisi.yeniden_kes ile ayni kural)
    return en_ofs, kk


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
    """'modul:Sinif' -> sinif. Bos / 'faz1': Faz1Hat (varsayilan); 'eski': MevcutHat."""
    if not ad or ad == "faz1":
        return Faz1Hat
    if ad == "eski":
        return MevcutHat
    modul, _, sinif = ad.partition(":")
    return getattr(importlib.import_module(modul), sinif or "Hat")


def kos(klasor, hat_sinifi=None, stt=None, bas=None, bit=None, cikti=None, ayar=None, ben=None, yazdir=print):
    """Kaydi hattan gecirir. hat_sinifi: Hat sozlesmesini uygulayan sinif (kurucu ayar sozlugu alir; varsayilan
    Faz1Hat). Dondurur: (metrikler, cikti klasoru)."""
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
                 "kulaklik": None, "saat_kaymasi": saat_kaymasi(klipler), "stt": stt, "klasor": klasor}, **(ayar or {}))
    cikti = cikti or tempfile.mkdtemp(prefix="replay_")
    os.makedirs(cikti, exist_ok=True)

    olaylar = [(o["bit_epoch"], 0, "okuma", o) for o in okumalar]
    olaylar += [((k.get("bit") or 0) + (k.get("stt_sure") or 0), 1, "klip", k) for k in klipler.values()]
    olaylar.sort(key=lambda e: (e[0], e[1], e[3].get("no") or e[3].get("okuma_no") or 0))

    hat = (hat_sinifi or Faz1Hat)(ayar)
    try:
        hat.baslat(cikti)
        for an, _, tur, v in olaylar:
            if tur == "okuma":
                hat.okuma_olayi(v)
            else:
                hat.klip(an, v, segmentler_al(v, klasor, stt))
            hat.kontrol(an)
        satirlar = hat.bitir()
        duzelt_ozeti = hat.duzelt_ozeti() if hasattr(hat, "duzelt_ozeti") else None
    finally:
        hat.kapat()
    with open(os.path.join(cikti, "satirlar.jsonl"), "w", encoding="utf-8") as f:
        for s in satirlar:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    gercek = olcum.gercek_oku(klasor)
    m = olcum.hesapla(klipler, gercek, satirlar, ben=ben)
    m["replay"] = {"okuma": len(okumalar), "klip": len(klipler), "stt": stt is not None,
                   "hat": type(hat).__name__}
    if duzelt_ozeti is not None:
        m["replay"]["duzelt"] = duzelt_ozeti
    if getattr(hat, "uzun", None) is not None:
        m["uzun"] = uzun_ozeti(klipler, gercek, satirlar, m, hat.uzun, ben=ben, kisa_istek=len(klipler) if stt else 0)
    with open(os.path.join(cikti, "olcum.json"), "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    yazdir(olcum.ozet_metin(m))
    if duzelt_ozeti is not None:
        yazdir(f"Düzeltme önerisi ({duzelt_ozeti['llm']} LLM): {duzelt_ozeti['oneri']} öneri, {duzelt_ozeti['kabul']} kabul; "
               f"red nedenleri: {duzelt_ozeti['red']}")
    if m.get("uzun"):
        yazdir(uzun_metin(m["uzun"]))
    yazdir(f"çıktı: {cikti}")
    return m, cikti


UZUN_KAPI_HEDEF = -0.25                        # MIMARI_v3 §9 Faz 3: replay WER goreli degisimi hedefi
UZUN_KAPI_ALT = -0.10                          # ... acik kalma sarti (bundan kotu ise uzun_pencere kapali kalir)


def uzun_ozeti(klipler, gercek, satirlar, m_uzun, uzun, ben=None, kisa_istek=0):
    """Kisa / uzun WER karsilastirmasi + GPU yuku. Kisa taraf: uzun pencerenin degistirdigi satirlarin raw_kisa'si."""
    kisa = []
    for s in satirlar:
        d = dict(s)
        if isinstance(s.get("raw_kisa"), str):
            d["text"] = d["raw"] = s["raw_kisa"]
        kisa.append(d)
    m_kisa = olcum.hesapla(klipler, gercek, kisa, ben=ben)
    w_k, w_u = m_kisa["wer"]["oran"], m_uzun["wer"]["oran"]
    goreli = round((w_u - w_k) / w_k, 4) if isinstance(w_k, (int, float)) and isinstance(w_u, (int, float)) and w_k else None
    bas = min((k.get("bas") or 0) for k in klipler.values()) if klipler else 0
    bit = max((k.get("bit") or 0) for k in klipler.values()) if klipler else 0
    sa = max(0.0, bit - bas) / 3600.0
    ist = uzun.istatistik(kisa_istek=kisa_istek)
    ist.update(sure_sa=round(sa, 3), saatlik_istek=round(ist["istek"] / sa, 1) if sa > 0 else None,
               kisa_saatlik=round(kisa_istek / sa, 1) if sa > 0 else None)
    return {"wer_kisa": w_k, "wer_uzun": w_u, "hata_kisa": m_kisa["wer"]["hata"], "hata_uzun": m_uzun["wer"]["hata"],
            "referans_kelime": m_uzun["wer"]["referans_kelime"], "goreli": goreli,
            "kapi_hedef": UZUN_KAPI_HEDEF, "kapi_alt": UZUN_KAPI_ALT,
            "hedef_gecti": goreli is not None and goreli <= UZUN_KAPI_HEDEF,
            "alt_gecti": goreli is not None and goreli <= UZUN_KAPI_ALT,
            "degisen_satir": sum(1 for s in satirlar if isinstance(s.get("raw_kisa"), str)),
            "belirsiz_kelime": sum(len(s.get("uzun_belirsiz") or []) for s in satirlar), "gpu": ist}


def uzun_metin(u):
    def y(x):
        return "-" if x is None else f"%{100 * x:.1f}"
    g = u["gpu"]
    karar = ("hedef geçti" if u["hedef_gecti"] else ("alt sınır geçti" if u["alt_gecti"] else
             "ALT SINIRIN ALTINDA: uzun_pencere kapalı kalmalı"))
    goreli = "-" if u["goreli"] is None else f"{100 * u['goreli']:+.1f}%"
    return (f"Uzun pencere: WER kısa {y(u['wer_kisa'])} -> uzun {y(u['wer_uzun'])} (göreli "
            f"{goreli}; {karar}); {u['degisen_satir']} satır, "
            f"{u['belirsiz_kelime']} belirsiz kelime; GPU: {g['pencere']} pencere, {g['istek']} uzun istek "
            f"(saatte {g.get('saatlik_istek') or '-'}), kısa {g.get('kisa_istek', 0)} (saatte {g.get('kisa_saatlik') or '-'})")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Kaydı boru hattından çevrimdışı geçir (varsayılan: Faz 1 hattı)")
    ap.add_argument("klasor", help="toplantı klasörü (içinde olcum/ var)")
    ap.add_argument("--stt", action="store_true", help="wav'ları config.json'daki STT ile yeniden çöz (devirde yeniden kesme de)")
    ap.add_argument("--uzun", action="store_true", help="Faz 3 uzun pencere (--stt ile): kısa / uzun WER ve STT yükü raporu")
    ap.add_argument("--cikti", help="çıktı klasörü (varsayılan: geçici klasör)")
    ap.add_argument("--bas", help="başlangıç: SS:DD[:SN] ya da ilk kayıttan dakika")
    ap.add_argument("--bit", help="bitiş: SS:DD[:SN] ya da ilk kayıttan dakika")
    ap.add_argument("--hat", help="modul:Sinif (Hat sözleşmesi) | faz1 | eski; varsayılan faz1")
    ap.add_argument("--eski", action="store_true", help="Faz 0 hattı (MevcutHat); --hat eski ile aynı")
    ap.add_argument("--ben", help="mikrofon sahibinin adı")
    ap.add_argument("--katilimcilar", help="virgülle ayrılmış katılımcı adları (Outlook daveti gibi); yoksa ad ≥ 3 ayrı satırda "
                                           "görülünce kişi sayılır")
    ap.add_argument("--hoparlor", action="store_true", help="kulaklık yok: yankı kuralı (kulaklik=False)")
    ap.add_argument("--duzelt", action="store_true", help="terim/ad düzeltmesini de çalıştır (varsayılan: sahte agresif "
                                                          "önerici); çıktı: düzeltilen / bozulan kelime")
    ap.add_argument("--llm", action="store_true", help="--duzelt ile gerçek LLM (config.json); yoksa sahte önerici")
    ap.add_argument("--terimler", help="--duzelt için virgülle ayrılmış terimler (Whisper'ın bozduğu doğru yazımlar)")
    ap.add_argument("--davet", help="--duzelt için davet metni dosyası (büyük harfli öbekler terim olur)")
    a = ap.parse_args(argv)
    if a.uzun and not a.stt:
        ap.error("--uzun için --stt gerekir (pencereler kayıtlı wav'lardan yeniden çözülür)")
    ayar = {"kulaklik": False} if a.hoparlor else {}
    if a.uzun:
        ayar["uzun"] = True
    if a.duzelt:
        ayar.update(duzelt=True, llm=a.llm)
        if a.terimler:
            ayar["terimler"] = [t.strip() for t in a.terimler.split(",") if t.strip()]
        if a.davet:
            with open(a.davet, encoding="utf-8") as f:
                ayar["davet"] = f.read()
    if a.katilimcilar:
        ayar["katilimcilar"] = [k.strip() for k in a.katilimcilar.split(",") if k.strip()]
    kos(a.klasor, hat_sinifi_yukle("eski" if a.eski else a.hat), stt_istemci() if a.stt else None, a.bas, a.bit, a.cikti,
        ayar, a.ben)
    return 0


if __name__ == "__main__":
    sys.exit(main())
