"""
yakalayici.py — Teams canli altyazisini UI Automation ile okur (pop-out gerekmez).
Altyazi grubunu 'Microsoft Teams' gecen herhangi bir pencerede arar; toplanti penceresi
arkada kalabilir ama simge durumuna kucultulmemeli.
"""
import ctypes
import datetime as dt
import difflib
import json
import os
import re
import time
from collections import deque

import uiautomation as auto

import saat                                # adima dayanikli zaman kaynagi (time.time yerine)
from kelime import normalize

SPI_SETSCREENREADER = 0x0047
GRUP_ADLARI = ("canlı alt yazı", "live captions")
PENCERE_ANAHTAR = ("teams", "alt yaz", "caption")
user32 = ctypes.windll.user32
OKUMA_ARALIK = 0.6           # dongude iki okuma arasi (sn)
YAVAS_ARALIK = 1.2           # UIA okumasi yavassa (kalabalik toplanti, zayif makine): CPU'yu bogmasin
YAVAS_OKUMA_SN = 0.25        # son 10 okumanin ortalamasi bunu gecerse yavas sayilir
KISA_SATIR = 20              # bundan kisa (normalize) satirlar ('Evet.') gercekten tekrar edilebilir: baglamla taninir
GORULEN_UST = 2000           # yayilmis satir hafizasi: panel kac satir gosterirse gostersin tekrar yayilmaz
GUNCELLEME_SN = 90           # ayni konusmacinin bu kadar yeni satiri buyur/duzeltilirse yeni satir degil guncelleme
GUNCELLEME_SATIR = 3         # ... ve yalnizca o konusmacinin son bu kadar satiri icin
SIRA_BEKLE = 10              # ustteki buyuyen satir alttaki kararli satirlari en fazla bu kadar okuma bekletir
BENZER_ORAN = 0.9            # Teams'in sonradan duzelttigi satir (SequenceMatcher) esigi
PANEL_DONUK_SN = 12          # panel bu kadar sn DEGISMEDI ve hoparlorde konusma var -> donuk (eski/bayat oge): yeniden bul
DONUK_SES_PAYI = 1.0         # donuk: son loopback konusma bitisi >= son degisim - bu pay (degisimden sonra da konusma var)
DONUK_SES_SN = 5.0           # ... ses_sure_fn varsa: son degisimden sonra en az bu kadar sesli loopback konusmasi (K1b: tek
                             #     kisa halusinasyon klibi sessiz panelde donuk saydirmaz)
YENIDEN_BUL_SN = 30          # panel ogesi bu aralikla agactan yeniden bulunup runtime id'si karsilastirilir
AD_ESIGI = 3                 # sezgisel (katilimci listesinde olmayan) ad, bu kadar AYRI satirda gorulunce bilinen ad olur
                             # (canli gorunumde once '?'; okuma olayina ad oldugu gibi gider, hayalet kurali konusmaci.py)


def ekran_okuyucu(acik):
    """Edge/Chromium sayfa icerigini ancak bu bayrak acikken UI Automation'a acar."""
    user32.SystemParametersInfoW(SPI_SETSCREENREADER, 1 if acik else 0, None, 0)


def _grup_bul_ayrintili():
    """Altyazi grubu, bulundugu ust pencere ve basligi: (grup, pencere, baslik). Bulunamazsa (None, None, "")."""
    for w in auto.GetRootControl().GetChildren():
        ad = (w.Name or "").lower()
        if not any(k in ad for k in PENCERE_ANAHTAR):
            continue
        for c, _ in auto.WalkControl(w, includeTop=False, maxDepth=45):
            if c.ControlTypeName == "GroupControl" and (c.Name or "").strip().lower() in GRUP_ADLARI:
                return c, w, (w.Name or "")
    return None, None, ""


def _grup_bul():
    """Altyazi grubunu ve bulundugu pencerenin basligini dondurur: (grup, pencere_basligi).
    (Testler ve Yakalayici bu adi cagirir/yamar; ayrintili sonuc icin _grup_bul_ayrintili.)"""
    grup, _, baslik = _grup_bul_ayrintili()
    return grup, baslik


def _rid(oge):
    """UIA ogesinin runtime id'si (yeniden cizilince degisir) -> tuple | None (desteklenmiyorsa/hatada None).
    uiautomation.Control'de COM ogesi .Element'tedir; sahte/sarmal nesnelerde dogrudan GetRuntimeId olabilir."""
    for ad in ("Element", None):
        try:
            kaynak = getattr(oge, ad) if ad else oge
            r = kaynak.GetRuntimeId()
            if r is not None:
                return tuple(r)
        except Exception:
            continue
    return None


def _pencere_handle(grup):
    """Altyazi grubunun ust pencere handle'i (int) | 0."""
    try:
        return int(grup.GetTopLevelControl().NativeWindowHandle or 0)
    except Exception:
        return 0


def pencere_durumu(hwnd):
    """Teams penceresi: {"simge": simge durumunda, "buyuk": buyutulmus, "onplan": on planda}. Bilinmiyorsa ya da
    Windows disinda {} (user32 cagrilari best effort)."""
    if not hwnd:
        return {}
    try:
        return {"simge": bool(user32.IsIconic(hwnd)), "buyuk": bool(user32.IsZoomed(hwnd)),
                "onplan": user32.GetForegroundWindow() == hwnd}
    except Exception:
        return {}


def teams_pencere_basliklari():
    """Acik Teams pencerelerinin basliklari (toplanti adini cikarmak icin)."""
    return [w.Name for w in auto.GetRootControl().GetChildren()
            if w.Name and "teams" in w.Name.lower()]


def toplanti_adi_cikar(baslik):
    """'Alt yazilar | modeldev | Microsoft Teams | Sabitlenmis pencere' -> 'modeldev'."""
    if not baslik:
        return ""
    parcalar = [p.strip() for p in baslik.split("|") if p.strip()]
    ele = ("microsoft teams", "teams", "alt yazılar", "alt yazilar", "captions", "live captions",
           "sabitlenmiş pencere", "sabitlenmis pencere", "pinned window", "sohbet", "chat")
    kalan = [p for p in parcalar if p.lower() not in ele]
    kalan = [re.sub(r"\s*\((Dış|Dis|External)\)\s*$", "", p).strip() for p in kalan]
    kalan = [p for p in kalan if p and p.lower() not in ele]
    return kalan[-1] if kalan else ""


def _isim_gibi(m, bilinen, gevsek=False):
    if m in bilinen:
        return True
    if any(ch in m for ch in "?!,:;") or m.endswith((".", "…")):
        return False
    kelimeler = m.split()
    if not kelimeler or len(kelimeler) > 5:
        return False
    if gevsek:
        return kelimeler[0][:1].isupper()
    return len(kelimeler) > 1 and all(k[:1].isupper() for k in kelimeler)


def _durum_mesaji(m):
    ml = m.lower()
    return ("alt yaz" in ml or "caption" in ml) and any(
        k in ml for k in ("duraklat", "başlat", "kapat", "paused", "started", "turned", "beklemede"))


def _buyumus(eski, yeni):
    """Ayni satirin buyumus/duzeltilmis hali mi? Teams canli satiri sona kelime ekleyerek buyutur, son kelimeyi
    ya da birkac kelimeyi duzeltebilir: yeni metnin eski uzunluktaki bas kismi eskiye cok benzer olmali.
    (Eski surumdeki 'tum metin > 0,6 benzer' olcutu ayni kisinin benzer kalipli iki cumlesini birlestiriyordu.)"""
    e, y = normalize(eski), normalize(yeni)
    if not e or not y:
        return False
    if y.startswith(e) or (" " in e and y.startswith(e.rsplit(" ", 1)[0] + " ")):
        return True
    return difflib.SequenceMatcher(None, e, y[:len(e) + 3]).ratio() >= 0.85


class Yakalayici:
    """Altyazi panelini okur, her satiri BIR KEZ yayar.

    Eski surum gorunen satirlari son 30 yayilan satirin kuyruguyla hizaliyordu (pop-out penceresinin birkac
    satir gosterdigi varsayimi). Teams toplanti penceresinin paneli kalabalik toplantida 30'dan fazla satir
    gosterince hizalama her okumada 0 cikiyor, panelin tamami 'yeni' sayilip her 0,6 sn'de yeniden yayiliyordu
    (altyazi tasmasi). Simdi karar panel boyutundan bagimsizdir:
      - uzun satir (>= KISA_SATIR): (konusmaci, normalize(metin)) son GORULEN_UST yayilan satirda yoksa aday;
      - kisa satir ('Evet.', 'Tamam.' gercekten tekrar edilir): paneldeki bir onceki satirin kaydina gore
        taninir; onceki satir henuz yayilmadiysa bekler, panelin basindaysa yalniz metniyle taninir;
      - kararlilik: aday ancak iki ardisik okumada ayni metinle gorulurse yayilir (canli buyuyen satir
        yayilmaz); panelin son satiri, altinda yeni bir satir belirene kadar beklenir (canli);
      - buyume/duzeltme: aday, ayni konusmacinin son GUNCELLEME_SN icinde yayilmis (ve artik panelde
        gorunmeyen) bir satirinin onek-uzantisi ya da >= BENZER_ORAN benzeri ise yeni satir degil
        {'guncelle': True, 'onceki_text': ...} olarak doner (motor acik parcadaki satiri yerinde gunceller)."""

    def __init__(self, katilimcilar=(), okuma_fn=None, ad_esigi=AD_ESIGI, ses_var_fn=None, log_fn=None, ses_sure_fn=None):
        """katilimcilar: bilinen adlar (Outlook daveti/ayar) — sezgisel tanima beklemeden gecerli. okuma_fn(okuma): HER UIA
        okumasinda (bos okuma ve altyazi grubu yokken de) cagrilir, bkz. _okuma_yayinla. ses_var_fn(): son loopback
        konusma bitisinin epoch'u | None (motor.son_loopback_ses; panel canliligi icin). log_fn(mesaj, **ek): motor.olay_kaydet.
        ses_sure_fn(bas_epoch) -> bas_epoch'tan bu yana suzgeci gecen loopback konusma suresi (sn; motor.loopback_konusma_sn,
        K1b): varsa donuk yalniz son degisimden sonra >= DONUK_SES_SN konusmayla tetiklenir."""
        self.grup = None
        self.ses_var_fn = ses_var_fn
        self.ses_sure_fn = ses_sure_fn
        self.log_fn = log_fn
        self._grup_rid = None                      # panel ogesinin runtime id'si (yeniden cizilme tespiti)
        self.pencere_hwnd = 0                      # altyazi panelinin ust penceresi
        self._son_bulma = None                     # panelin agactan en son bulundugu an (periyodik dogrulama)
        self._zorla_bul = False                    # yeniden_bul(): bir sonraki okumada yeniden ara
        self.yeniden_bul_sayisi = 0                # okuma olayindaki "yeniden_bul": yapilan yeniden bulma sayisi
        self._onceki_okuma = None                  # canlilik: bir onceki okumanin satirlari
        self._son_degisim = None                   # satirlarin en son degistigi an (saat.simdi epoch'u)
        self._donuk_acik = False
        self._son_donuk_bul = 0.0
        self._donuk_ses = None                     # donuk yeniden aramasinin yapildigi andaki son konusma bitisi
        self._bulunamadi_loglandi = False
        self._simge_loglandi = False
        self.bilinen = set(a for a in katilimcilar if a)
        self.okuma_fn = okuma_fn
        self.ad_esigi = ad_esigi                   # sezgisel ad kac AYRI satirda gorulunce ad sayilir
        self._ad_adaylari = {}                     # sezgisel ad -> gorulen ayri satirlar (henuz onaylanmamis)
        self.son_okuma_epoch = None                # heartbeat: son UIA okumasinin bitis epoch'u
        self._okuma_baslari = deque(maxlen=11)     # son okumalarin baslangic epoch'lari (gercek periyot icin)
        self.kuyruk = []                           # son yayilan 30 satir (yalnizca tani/geri uyumluluk)
        self.canli = {}                            # konusmaci -> {"ts", "speaker", "text"}: paneldeki son satiri
        self.hazir = False
        self.hazir_zamani = None
        self.kayip_zamani = None
        self._kayip_simdi = None                   # kayip_zamani'nin simdi epoch'u: kayip_saniye saat adimindan etkilenmez
        self.pencere_basligi = ""
        self.toplanti_adi = ""
        self.okuma_sureleri = deque(maxlen=10)     # A7: son okumalarin suresi (sn)
        self._no = 0                               # yayim sirasi
        self._uzun = {}                            # (kim, norm) -> kayit (guncel ve onceki halleri)
        self._uzun_sira = deque()                  # _uzun anahtarlarinin eklenme sirasi (ust sinir)
        self._kisa = {}                            # (kim, norm, onceki satirin no'su) -> kayit: yayilmis kisa satirlar
        self._kisa_ad = {}                         # (kim, norm) -> sayi: capasiz (panel basi) tanima icin
        self._kisa_sira = deque()
        self._son_kayitlar = deque(maxlen=300)     # guncelleme aramasi: {"no", "ts", "speaker", "text", "zaman",
                                                   # "son_gorulme"}
        self._onceki_adaylar = {}                  # kararlilik anahtari -> (ts, ilk_okuma): onceki okumanin adaylari
        self._okuma_no = 0
        self._son_adaylar = []                     # bir onceki okumanin adaylari, panel sirasiyla (bitir icin)
        self._son_gorunen = set()
        self._okuma_yolu = None                    # olcum: okuma_kaydi_ac ile acilir (okumalar.jsonl)
        self._okuma_sira = 0

    def okuma_kaydi_ac(self, klasor):
        """Faz 0 olcum kaydi (opt-in): her UIA panel okumasi <klasor>/olcum/okumalar.jsonl'e
        {okuma_no, bas_epoch, bit_epoch, gorunen: [[konusmaci, metin], ...]} olarak eklenir. Yayim mantigina dokunmaz."""
        try:
            kok = os.path.join(klasor, "olcum")
            os.makedirs(kok, exist_ok=True)
            self._okuma_yolu = os.path.join(kok, "okumalar.jsonl")
            return self._okuma_yolu
        except Exception:
            self._okuma_yolu = None
            return None

    def _okuma_yaz(self, okuma):
        if not self._okuma_yolu:
            return
        try:
            satir = {"okuma_no": okuma["okuma_no"], "bas_epoch": round(okuma["bas_epoch"], 3),
                     "bit_epoch": round(okuma["bit_epoch"], 3), "grup": okuma["grup"], "gorunen": okuma["gorunen"],
                     "degisti": okuma["degisti"], "donuk": okuma["donuk"], "yeniden_bul": okuma["yeniden_bul"],
                     "pencere": okuma["pencere"], "okuma_sn": round(okuma["okuma_sn"], 3)}
            with open(self._okuma_yolu, "a", encoding="utf-8") as f:
                f.write(json.dumps(satir, ensure_ascii=False) + "\n")
        except Exception:
            self._okuma_yolu = None                # IO hatasi: kaydi kapat, okuyucu etkilenmesin

    def _okuma_yayinla(self, bas_epoch, bit_epoch, grup, gorunen, degisti=True, donuk=False, pencere=None):
        """Her UIA okumasi (bos okuma ve altyazi grubu yokken de) bir okuma olayi uretir (plan 3 'Cizelge', C2):
          {"tur": "okuma", "okuma_no", "bas_epoch", "bit_epoch", "grup": altyazi paneli bulundu mu,
           "gorunen": [[konusmaci, metin], ...],
           "degisti": satirlar bir onceki okumadan farkli mi, "donuk": satirlar >= PANEL_DONUK_SN degismedi ve
           hoparlorde konusma var, "yeniden_bul": yapilan yeniden bulma sayisi, "pencere": {"simge", "buyuk",
           "onplan"} | {}, "okuma_sn": okuma suresi}
        bas_epoch okuma baslamadan, bit_epoch okuma bittikten HEMEN sonra olculur (satirlarin gorundugu an bu aralikta).
        degisti varsayilani True: canlilik bilinmiyorsa (dogrudan cagri) 'degismedi' iddia edilmez.
        Olay okuma_fn'e gider (konusmaci.py cizelgesi + heartbeat) ve olcum kaydi aciksa okumalar.jsonl'e yazilir."""
        self._okuma_sira += 1
        self.son_okuma_epoch = bit_epoch
        self._okuma_baslari.append(bas_epoch)
        okuma = {"tur": "okuma", "okuma_no": self._okuma_sira, "bas_epoch": bas_epoch, "bit_epoch": bit_epoch,
                 "grup": bool(grup), "gorunen": [[k, n] for k, n in gorunen],
                 "degisti": bool(degisti), "donuk": bool(donuk), "yeniden_bul": self.yeniden_bul_sayisi,
                 "pencere": dict(pencere or {}), "okuma_sn": max(0.0, bit_epoch - bas_epoch)}
        self._okuma_yaz(okuma)
        if self.okuma_fn is not None:
            try:
                self.okuma_fn(okuma)
            except Exception:
                pass                                # tuketici hatasi okuyucuyu durdurmaz
        return okuma

    def okuma_periyodu(self):
        """Son okumalarin gercek periyodu (sn): okuma baslangiclari arasi ortalama = okuma suresi + uyku. Yoksa None.
        (konusmaci.py R'yi okuma olaylarindan kendisi de hesaplar; bu yalniz tani/heartbeat icin.)"""
        b = list(self._okuma_baslari)
        if len(b) < 2:
            return None
        return (b[-1] - b[0]) / (len(b) - 1)

    def onerilen_aralik(self):
        """A7: dongunun bir sonraki okumaya kadar beklemesi. UIA agaci yuruyusu son 10 okumada ortalama
        250 ms'yi gecerse 1,2 sn (Teams'i ve makineyi yormasin), altina inince yine 0,6 sn."""
        if not self.okuma_sureleri:
            return OKUMA_ARALIK
        ort = sum(self.okuma_sureleri) / len(self.okuma_sureleri)
        return YAVAS_ARALIK if ort > YAVAS_OKUMA_SN else OKUMA_ARALIK

    def _ciftler(self):
        metinler = [(c.Name or "").strip()
                    for c, _ in auto.WalkControl(self.grup, includeTop=False, maxDepth=15)
                    if c.ControlTypeName == "TextControl"]
        metinler = [m for m in metinler if m and not _durum_mesaji(m)]
        tanidik = self.bilinen | set(self._ad_adaylari)        # yapi tanima: gorulmus adaylar da sayilir
        ciftler = []
        if metinler and len(metinler) % 2 == 0 and all(
                _isim_gibi(metinler[i], tanidik, gevsek=True) for i in range(0, len(metinler), 2)):
            for i in range(0, len(metinler), 2):
                if len(metinler[i + 1]) >= 2:
                    ciftler.append((metinler[i], metinler[i + 1]))
            return self._adlari_dogrula(ciftler)
        konusan = None
        for m in metinler:
            if _isim_gibi(m, tanidik):
                konusan = m
            elif konusan and len(m) >= 2:
                ciftler.append((konusan, m))
            elif not konusan and len(m) >= KISA_SATIR:
                ciftler.append(("?", m))          # adi gorunmeyen uzun satir atilmaz: canlida Bilinmeyen (adi sayilmaz)
        return self._adlari_dogrula(ciftler)

    def _adlari_dogrula(self, ciftler):
        """Sezgisel ad (katilimci listesinde olmayan, buyuk harf kalibiyla bulunan) ad_esigi AYRI satirda gorulunce bilinen
        adlara girer (yapi tanima, canli gorunum). Ayni satirin buyumesi ya da ayni panelin yeniden okunmasi yeni satir
        sayilmaz. Okuma olayina adlar OLDUGU GIBI gider: hayalet ad kurali (hayalet_satir) konusmaci.py'de uygulanir.
        Burada '?' ikamesi yapilsaydi esik dolunca eski satirlarin etiketi '?'dan gercek ada doner, konusmaci onlari
        yeni satir/aralik sanip sahte 'A → B' / 'A / B' uretirdi."""
        for kim, ne in ciftler:
            if kim not in self.bilinen and kim != "?":
                self._ad_say(kim, ne)
        return list(ciftler)

    def _etiketle(self, gorunen):
        """Canli gorunum / altyazi satiri yolu: henuz onaylanmamis sezgisel ad '?' gosterilir."""
        return [(kim if kim in self.bilinen or kim not in self._ad_adaylari else "?", ne) for kim, ne in gorunen]

    def _ad_say(self, kim, ne):
        satirlar = self._ad_adaylari.setdefault(kim, [])
        for i, s in enumerate(satirlar):
            if s == ne or _buyumus(s, ne) or _buyumus(ne, s):
                if len(ne) > len(s):
                    satirlar[i] = ne                           # ayni satirin buyumus hali
                break
        else:
            satirlar.append(ne)
        if len(satirlar) >= self.ad_esigi:
            self.bilinen.add(kim)
            del self._ad_adaylari[kim]
        elif len(self._ad_adaylari) > 200:                     # ust sinir: en eski aday dussun
            self._ad_adaylari.pop(next(iter(self._ad_adaylari)))

    def oku(self):
        """Yeni kesinlesmis satirlari (ve guncellemeleri) dondurur. self.hazir: altyazi grubu bulundu mu.
        Okuma suresi olculur (onerilen_aralik). Her cagri bir okuma olayi yayar (okuma_fn, bkz. _okuma_yayinla)."""
        bas = time.perf_counter()
        try:
            return self._oku()
        finally:
            self.okuma_sureleri.append(time.perf_counter() - bas)

    def _oku(self):
        bas_epoch = saat.simdi()                   # UIA okumasi baslamadan once
        gorunen = []
        try:
            if self.grup is None:
                self._zorla_bul = False
                grup, baslik = _grup_bul()
                self._baslik_guncelle(baslik)
                if grup is None:
                    if self.hazir:
                        self.kayip_zamani = self.kayip_zamani or dt.datetime.now()
                        self._kayip_simdi = self._kayip_simdi or saat.simdi()
                    self.hazir = False
                    self._onceki_okuma = None
                    self._okuma_yayinla(bas_epoch, saat.simdi(), False, [], degisti=False, pencere=self._pencere())
                    return []
                self._ata(grup)
                self.hazir, self.kayip_zamani, self._kayip_simdi = True, None, None
                self.hazir_zamani = self.hazir_zamani or dt.datetime.now()
            gorunen = self._ciftler()
        except Exception:
            self.grup = None
            self._onceki_okuma = None
            self._okuma_yayinla(bas_epoch, saat.simdi(), False, [], degisti=False, pencere=self._pencere())
            return []
        bit_epoch = saat.simdi()                   # okuma biter bitmez (yayim hesabindan once)
        degisti = self._degisim_izle(gorunen, bit_epoch)
        donuk = self._donuk_denetle(bit_epoch)
        pencere = self._pencere()
        self._okuma_yayinla(bas_epoch, bit_epoch, True, gorunen, degisti=degisti, donuk=donuk, pencere=pencere)
        self._bakim()                              # zorla/periyodik yeniden bulma: olay yayilandan SONRA (okuma zamanlari bozulmaz)
        if not gorunen:
            return []
        # satir zamani: okumanin ortasi (eskiden okuma bittikten sonraki an: yavas okumada kayardi)
        return self._isle(self._etiketle(gorunen), saat.ts((bas_epoch + bit_epoch) / 2))   # ekranda yerel saat

    # ---------- panel canliligi ve yeniden bulma (bayat/donuk UIA ogesi: Teams buyutulunce/arkaya alinca)
    def _log(self, mesaj, **ek):
        if self.log_fn is None:
            return
        try:
            self.log_fn(mesaj, **ek)
        except TypeError:
            try:
                self.log_fn(mesaj)                 # yalniz mesaj alan basit gunluk islevi
            except Exception:
                pass
        except Exception:
            pass

    def yeniden_bul(self):
        """Panel atamasi basarisiz (motor) ya da bayat panel supheli: bir sonraki okumada paneli agactan yeniden ara.
        Baska thread'den cagrilabilir (yalniz bayrak kurar)."""
        self._zorla_bul = True

    def pencere_durumu(self):
        return pencere_durumu(self.pencere_hwnd)

    def _pencere(self):
        p = self.pencere_durumu()
        if p.get("simge"):
            if not self._simge_loglandi:
                self._simge_loglandi = True
                self._log("Teams penceresi küçültülmüş: altyazı güncellenmiyor", neden="pencere_simge")
        else:
            self._simge_loglandi = False
        return p

    def _baslik_guncelle(self, baslik):
        if baslik:
            self.pencere_basligi = baslik
            self.toplanti_adi = toplanti_adi_cikar(baslik) or self.toplanti_adi

    def _ata(self, grup):
        """Bulunan panel ogesini onbellege alir (runtime id + pencere handle)."""
        self.grup = grup
        self._grup_rid = _rid(grup)
        self.pencere_hwnd = _pencere_handle(grup)
        self._son_bulma = saat.simdi()

    def _bakim(self):
        """Okuma olayi yayilandan sonra: yeniden_bul() istegi varsa ya da panel YENIDEN_BUL_SN'dir agactan dogrulanmadiysa dogrula."""
        if self.grup is None:
            return
        simdi = saat.simdi()
        if self._son_bulma is None:
            self._son_bulma = simdi
        if self._zorla_bul:
            self._zorla_bul = False
            self._log("altyazı paneli yeniden aranıyor (konuşmacı ataması başarısız)", neden="yeniden_bul")
            self._yeniden_bul_et("zorla")
        elif simdi - self._son_bulma >= YENIDEN_BUL_SN:
            self._yeniden_bul_et("periyodik")

    def _yeniden_bul_et(self, sebep):
        """Paneli agactan yeniden bulur. sebep 'donuk'/'zorla': bulunan oge her zaman benimsenir; 'periyodik': runtime id
        ayniysa eski oge kalir, degistiyse yeni oge gecilir (log 'panel yeniden çizilmiş'). Bulunamazsa eski oge
        okunmaya devam eder (altyazi kapanmis sayilip toplanti bitirilmez; kayip sayimi okuma yolunda)."""
        eski_rid = self._grup_rid
        try:
            yeni, baslik = _grup_bul()
        except Exception:
            yeni, baslik = None, ""
        self._son_bulma = saat.simdi()
        if yeni is None:
            if sebep != "periyodik":
                self.yeniden_bul_sayisi += 1
                if not self._bulunamadi_loglandi:
                    self._bulunamadi_loglandi = True
                    self._log("altyazı paneli yeniden aranırken bulunamadı: eski öğe okunuyor", neden="panel_bulunamadi")
            return False
        self._bulunamadi_loglandi = False
        self._baslik_guncelle(baslik)
        yeni_rid = _rid(yeni)
        oge_degisti = eski_rid is not None and yeni_rid is not None and yeni_rid != eski_rid
        if sebep == "periyodik" and not oge_degisti and eski_rid is not None and yeni_rid is not None:
            return False                                   # ayni oge: dokunma
        self._ata(yeni)
        if oge_degisti:
            self._log("panel yeniden çizilmiş (yeni öğe)", neden="panel_yeniden")
        if sebep != "periyodik" or oge_degisti:
            self.yeniden_bul_sayisi += 1
        return True

    def _degisim_izle(self, gorunen, simdi):
        """Satirlar bir onceki okumayla ayni mi? Degistiyse _son_degisim ilerler."""
        liste = [(k, n) for k, n in gorunen]
        degisti = liste != self._onceki_okuma
        if degisti or self._son_degisim is None:
            self._son_degisim = simdi
        self._onceki_okuma = liste
        return degisti

    def _donuk_mu(self, simdi):
        """-> (donuk, ses): ses = son loopback konusma bitisi (epoch) | None. ses_sure_fn varsa (K1b) donuk icin son
        degisimden (- DONUK_SES_PAYI) bu yana >= DONUK_SES_SN sesli konusma gerekir; yoksa son konusma bitisi
        >= son degisim - DONUK_SES_PAYI yeter."""
        if (self.ses_var_fn is None and self.ses_sure_fn is None) or self._son_degisim is None \
                or simdi - self._son_degisim < PANEL_DONUK_SN:
            return False, None
        ses = None
        if self.ses_var_fn is not None:
            try:
                ses = self.ses_var_fn()
            except Exception:
                return False, None
        if self.ses_sure_fn is not None:
            try:
                sure = self.ses_sure_fn(self._son_degisim - DONUK_SES_PAYI)
            except Exception:
                return False, None
            try:
                donuk = sure is not None and float(sure) >= DONUK_SES_SN
            except (TypeError, ValueError):
                donuk = False
            return donuk, ses
        return (ses is not None and ses >= self._son_degisim - DONUK_SES_PAYI), ses

    def _donuk_denetle(self, simdi):
        """Donuk: PANEL_DONUK_SN'dir satir degismedi ve hoparlorde o aralikta konusma var -> panel ogesi bayat sayilir,
        hemen yeniden aranir. Donuk surerse yeniden arama PANEL_DONUK_SN'de bir ve yalniz YENI konusma geldikse
        (sessizlikte UIA yuruyusu bosuna okuma boslugu acmasin). Olay bayragi donuk surdukce True kalir."""
        donuk, ses = self._donuk_mu(simdi)
        if donuk:
            if not self._donuk_acik:
                self._donuk_acik = True
                self._son_donuk_bul, self._donuk_ses = simdi, ses
                self._log(f"altyazı paneli {int(simdi - self._son_degisim)} sn değişmedi, hoparlörde konuşma var: "
                          "panel yeniden aranıyor", neden="panel_donuk")
                self._yeniden_bul_et("donuk")
            elif (simdi - self._son_donuk_bul >= PANEL_DONUK_SN and ses is not None
                  and ses > (self._donuk_ses or 0) + DONUK_SES_PAYI):
                self._son_donuk_bul, self._donuk_ses = simdi, ses
                self._yeniden_bul_et("donuk")
        elif self._donuk_acik:
            self._donuk_acik = False
            self._log("altyazı paneli yeniden güncelleniyor", neden="panel_canli")
        return donuk

    # ---------- yayim karari (UIA'dan bagimsiz; testler dogrudan cagirir)
    def _isle(self, gorunen, simdi):
        self._okuma_no += 1
        anahtarlar = [(kim, normalize(ne)) for kim, ne in gorunen]
        self._son_gorunen = set(anahtarlar)
        for a in self._son_gorunen:                # gorunen yayilmis satirlar: duzeltme/buyume tespiti icin
            k = self._uzun.get(a)
            if k is not None:
                k["son_gorulme"] = self._okuma_no
        yeni = self._kaybolanlar(gorunen)
        son_i = len(gorunen) - 1
        kisi_son = {kim: i for i, (kim, _) in enumerate(gorunen)}
        adaylar, aday_listesi = {}, []
        # Kisa satir kimligi: (kim, metin, BIR ONCEKI gorunen satirin kayit no'su). Onceki satir:
        #   "ust"  : panelin basi, kimligi metinden bilinemiyor (kisa satirlar yalniz metinle taninir),
        #   "kayit": yayilmis, no'su onceki_no; "aday": henuz yayilmadi (alttaki kisa satir bekler).
        durum, onceki_no, onceki_a = "ust", None, None
        tutan = False
        adet = {}
        for a in anahtarlar:
            adet[a] = adet.get(a, 0) + 1
        bekleyen_kisa = {p["a"] for p in self._son_adaylar if not p["uzun"]}
        for i, ((kim, ne), a) in enumerate(zip(gorunen, anahtarlar)):
            if not a[1]:
                continue
            uzun = len(a[1]) >= KISA_SATIR
            if uzun:
                kayit = self._uzun.get(a)
                if kayit is not None:
                    durum, onceki_no, onceki_a = "kayit", kayit["no"], a
                    self._canli_birak(kim, a[1])
                    continue
                kkey = a
            else:
                if durum == "ust":
                    # panelin basina kaymis, daha once yayilmis kisa satir; ancak bir onceki okumada beklerken
                    # (ustundeki satir buyurken) basa kaydiysa ve panelde tek ise o bekleyen satirdir
                    if self._kisa_ad.get(a) and not (adet[a] == 1 and a in bekleyen_kisa):
                        # gorulmus satir: kaydi bu okumada da gorulmus sayilir (duzeltme/buyume tespiti) ve altindaki kisa
                        # satirlar ona gore taninir (eskiden durum 'ust' kalip alttaki tekrar eden 'Evet.' yutuluyordu)
                        k = self._kisa.get((kim, a[1], None))          # panel basindayken yayilmis kayit
                        if k is not None:
                            k["son_gorulme"] = self._okuma_no
                            durum, onceki_no = "kayit", k["no"]
                        onceki_a = a
                        continue
                elif durum == "kayit":
                    k = self._kisa.get((kim, a[1], onceki_no))
                    if k is not None:
                        k["son_gorulme"] = self._okuma_no
                        onceki_no, onceki_a = k["no"], a
                        continue
                kkey = (a, onceki_a)
            onceki = self._onceki_adaylar.get(kkey)
            zincir = onceki or self._zincir(kim, ne)          # buyuyen satir: ilk halinin zamani ve okumasi
            ts = self._ts(kim, ne, zincir, kisi_son.get(kim) == i, simdi)
            aday = {"kim": kim, "ne": ne, "a": a, "kkey": kkey, "ts": ts, "uzun": uzun, "ust": durum == "ust",
                    "capa_no": onceki_no if durum == "kayit" else None, "onceki_kkey": onceki_a,
                    "ilk": zincir[1] if zincir else self._okuma_no}
            adaylar[kkey] = (ts, aday["ilk"])
            capasi_hazir = uzun or durum != "aday"
            if i == son_i or onceki is None or not capasi_hazir or tutan:
                aday_listesi.append(aday)
                g = self._guncellenecek(aday)
                if g is not None:
                    # yayilmis bir satirin buyuyen/duzeltilen hali: kimligi o kayit, alttaki kisa satirlar
                    # ona gore taninir; yeni satir getirmez, alttakileri bekletmez
                    durum, onceki_no, onceki_a = "kayit", g["no"], kkey if not uzun else a
                    continue
                durum, onceki_a = "aday", kkey if not uzun else a
                # panel sirasi korunur: ustte hala buyuyen bir satir varsa alttaki kararli satirlar da bekler
                # (en fazla SIRA_BEKLE okuma; titreyen bir satir akisi durdurmasin)
                tutan = tutan or (aday["ilk"] > self._okuma_no - SIRA_BEKLE and i != son_i)
                continue
            k = self._yay(aday)
            durum, onceki_no, onceki_a = "kayit", k["no"], a
            yeni.append(k["_satir"])
        self._onceki_adaylar = adaylar
        self._son_adaylar = aday_listesi
        return yeni

    def _kaybolanlar(self, gorunen):
        """Bir onceki okumada bekleyen (kararlilik/canli) ama artik panelde olmayan satirlar: kararli hale
        gelmeden yukari kaydi. Ayni konusmacinin buyumus hali gorunuyorsa kaybolmamistir (buyudu)."""
        cikan, son = [], None
        gorunen_kume = self._son_gorunen
        for aday in self._son_adaylar:
            if aday["a"] in gorunen_kume:
                continue
            if any(kim == aday["kim"] and _buyumus(aday["ne"], ne) for kim, ne in gorunen):
                continue
            if aday["uzun"] and aday["a"] in self._uzun:
                continue
            # topluca yayilirken bir onceki satirin bu arada aldigi kayit no'su gecilir (bitir ile ayni zincir): yoksa
            # alttaki kisa satir 'panel basi' anahtariyla kaydolur ve ayni satir sonra yeniden yayilabilirdi
            k = self._yay(aday, son["no"] if son is not None and son["_kkey"] == aday["onceki_kkey"] else None)
            k["_kkey"] = aday["kkey"]
            son = k
            cikan.append(k["_satir"])
        return cikan

    def _zincir(self, kim, ne):
        """Bir onceki okumada bekleyen ve bu satirin buyumemis hali olan aday -> (ts, ilk_okuma) | None."""
        for p in self._son_adaylar:
            if p["kim"] == kim and _buyumus(p["ne"], ne):
                return p["ts"], p["ilk"]
        return None

    def _ts(self, kim, ne, onceki, kisinin_sonu, simdi):
        """Satirin zamani: ilk gorundugu an. Buyuyen satir (canli) ilk halinin zamanini korur."""
        c = self.canli.get(kim)
        buyuyor = c is not None and (c["text"] == ne or _buyumus(c["text"], ne))
        if kisinin_sonu:
            if buyuyor:
                c["text"] = ne
                return c["ts"]
            ts = onceki[0] if onceki else simdi
            self.canli[kim] = {"ts": ts, "speaker": kim, "text": ne}
            return ts
        if onceki:
            return onceki[0]
        return c["ts"] if buyuyor else simdi

    def _canli_birak(self, kim, norm):
        c = self.canli.get(kim)
        if c is not None and normalize(c["text"]) == norm:
            del self.canli[kim]

    def _guncellenecek(self, aday):
        """Teams satiri buyuttu ya da duzeltti mi? Ayni konusmacinin son GUNCELLEME_SN icinde yayilmis son
        GUNCELLEME_SATIR kaydindan; bu aday ilk belirdiginde panelde olan (en gec bir onceki okumada gorulmus)
        ama artik gorunmeyen ve adayin onek-uzantisi ya da >= BENZER_ORAN benzeri olan kayit. 'Bir onceki
        okumada gorulmus' sarti, panelden coktan kaymis eski bir cumleye benzeyen YENI satiri ayirir."""
        kim, ne, a = aday["kim"], aday["ne"], aday["a"]
        simdi, bakilan, en, en_puan = saat.simdi(), 0, None, 0.0
        for k in reversed(self._son_kayitlar):
            if simdi - k["zaman"] > GUNCELLEME_SN or bakilan >= GUNCELLEME_SATIR:
                break
            if k["speaker"] != kim:
                continue
            bakilan += 1                   # Teams konusmacinin yalnizca son birkac satirini buyutur/duzeltir
            eski = normalize(k["text"])
            if not eski or eski == a[1] or (kim, eski) in self._son_gorunen:
                continue
            if k["son_gorulme"] < aday["ilk"] - 1:
                continue
            if a[1].startswith(eski):
                puan = float(len(eski))
            else:
                oran = difflib.SequenceMatcher(None, k["text"], ne).ratio()
                puan = oran * len(eski) if oran >= BENZER_ORAN else 0.0
            if puan > en_puan:             # birden cok aday: en uzun ortak kisim (ayni kalipla baslayan iki satir)
                en, en_puan = k, puan
        return en

    def _yay(self, aday, onceki_no=None):
        """Adayi yayar -> kayit (kayit["_satir"]: motora gidecek satir ya da guncelleme). onceki_no: bekleyen
        satirlar topluca yayilirken (bitir, kaybolanlar) bir onceki satirin bu arada aldigi kayit no'su."""
        kim, ne, a = aday["kim"], aday["ne"], aday["a"]
        capa_no = aday["capa_no"] if aday["capa_no"] is not None else onceki_no
        kisa_anahtar = (kim, a[1], None if aday["ust"] else capa_no)
        k = self._guncellenecek(aday)
        guncelle = k is not None
        if guncelle:
            onceki, k["text"] = k["text"], ne
        else:
            self._no += 1
            k = {"no": self._no, "ts": aday["ts"], "speaker": kim, "text": ne, "zaman": saat.simdi()}
            self._son_kayitlar.append(k)
        k["son_gorulme"] = self._okuma_no
        if aday["uzun"]:
            self._uzun_ekle(a, k)
        else:
            self._kisa_ekle(kisa_anahtar, k)
        self._canli_birak(kim, a[1])
        if guncelle:
            k["_satir"] = {"ts": k["ts"], "speaker": kim, "text": ne, "guncelle": True, "onceki_text": onceki}
        else:
            k["_satir"] = {"ts": k["ts"], "speaker": kim, "text": ne}
            self.kuyruk = (self.kuyruk + [k["_satir"]])[-30:]
        return k

    def _uzun_ekle(self, a, kayit):
        if a not in self._uzun:
            self._uzun_sira.append(a)
        self._uzun[a] = kayit
        while len(self._uzun_sira) > GORULEN_UST:
            self._uzun.pop(self._uzun_sira.popleft(), None)

    def _kisa_ekle(self, anahtar, kayit):
        if anahtar in self._kisa:
            self._kisa[anahtar] = kayit
            return
        self._kisa[anahtar] = kayit
        ad = anahtar[:2]
        self._kisa_ad[ad] = self._kisa_ad.get(ad, 0) + 1
        self._kisa_sira.append(anahtar)
        while len(self._kisa_sira) > GORULEN_UST:
            eski = self._kisa_sira.popleft()
            self._kisa.pop(eski, None)
            n = self._kisa_ad.get(eski[:2], 0) - 1
            if n > 0:
                self._kisa_ad[eski[:2]] = n
            else:
                self._kisa_ad.pop(eski[:2], None)

    @property
    def gorulen(self):
        """Yayilmis uzun satirlarin (konusmaci, normalize(metin)) kumesi (tani/test)."""
        return set(self._uzun)

    def kayip_saniye(self):
        """Altyazi grubu kayboldugundan beri gecen sure (0 = kayip degil)."""
        if self.kayip_zamani is None:
            return 0
        if self._kayip_simdi is not None:
            return saat.simdi() - self._kayip_simdi
        return (dt.datetime.now() - self.kayip_zamani).total_seconds()      # kayip_zamani elle atanmissa

    def bitir(self):
        """Son okumada bekleyen (kararlilik/canli) satirlar: toplanti bitti, beklemeden yayilir."""
        yeni, son = [], None
        for aday in self._son_adaylar:
            if aday["uzun"] and aday["a"] in self._uzun:
                continue
            k = self._yay(aday, son["no"] if son is not None and son["_kkey"] == aday["onceki_kkey"] else None)
            k["_kkey"] = aday["kkey"]
            son = k
            yeni.append(k["_satir"])
        self._son_adaylar, self._onceki_adaylar, self.canli = [], {}, {}
        return yeni


# ---------------------------------------------------------------- altyaziyi Teams'te acma (best effort)

_MENU_DUGME = ("tümü", "diğer eylemler", "diğer seçenekler", "diğer", "daha fazla",
               "more actions", "more options", "more")
_DIL_MENU = ("dil ve konuşma", "language and speech")
_ALTYAZI_AC = ("canlı altyazıları aç", "canlı alt yazıları aç", "altyazıları aç", "alt yazıları aç",
               "alt yazıları göster", "altyazıları göster", "canlı alt yazıları göster",
               "turn on live captions", "show live captions")
_ALTYAZI_ACIK = ("canlı altyazıları kapat", "canlı alt yazıları kapat", "altyazıları kapat", "alt yazıları kapat",
                 "alt yazıları gizle", "altyazıları gizle", "turn off live captions", "hide live captions")
# Teams'in sabit AutomationId'leri (teams_dugmeler.py ile dogrulandi)
_ID_TUMU = "callingButtons-showMoreBtn"
_ID_DIL_MENU = "LanguageSpeechMenuControl-id"
_ID_ALTYAZI_TASMA = "closed-captions-overflow-menu-button"


def _toplanti_pencereleri():
    """Toplanti penceresi olabilecek Teams pencereleri. Gercek Teams pencereleri 'TeamsWebView'
    sinifindadir ve basligi '... | Microsoft Teams' ile biter; VS Code gibi basliginda 'teams'
    gecen baska pencereler elenir. Ana pencere (Sohbet/Takvim) sona atilir."""
    adaylar = []
    for w in auto.GetRootControl().GetChildren():
        ad = (w.Name or "").lower()
        sinif = (w.ClassName or "")
        teams_mi = sinif == "TeamsWebView" or ad.endswith("microsoft teams") or "| microsoft teams" in ad
        if not teams_mi or any(k in ad for k in ("alt yaz", "caption")):
            continue
        ana = any(k in ad for k in ("sohbet", "chat", "takvim", "calendar", "etkinlik", "activity", "ekipler"))
        adaylar.append((1 if ana else 0, 0 if sinif == "TeamsWebView" else 1, w))
    adaylar.sort(key=lambda x: (x[0], x[1]))
    return [w for _, _, w in adaylar]


def toplanti_penceresi_var():
    """Salt okunur: acik bir Teams toplanti penceresi (Ayril dugmesi olan) var mi?
    Altyazi olmadan calisirken toplantinin bittigini anlamak icin."""
    try:
        for w in _toplanti_pencereleri():
            if _bul_id(w, "hangup-button", derinlik=30) is not None:
                return True
    except Exception:
        pass
    return False


def _bul_id(kok, auto_id, derinlik=60):
    """AutomationId ile bul (dile bagimsiz, en guvenilir yol)."""
    for c, _ in auto.WalkControl(kok, includeTop=False, maxDepth=derinlik):
        if (c.AutomationId or "") == auto_id:
            return c
    return None


def _toplanti_penceresi():
    p = _toplanti_pencereleri()
    return p[0] if p else None


def _bul(kok, adlar, tipler=("ButtonControl", "MenuItemControl"), derinlik=60):
    adlar = tuple(a.lower() for a in adlar)
    for c, _ in auto.WalkControl(kok, includeTop=False, maxDepth=derinlik):
        if c.ControlTypeName in tipler:
            ad = (c.Name or "").strip().lower()
            if ad and any(ad == a or ad.startswith(a + " ") or ad.startswith(a + "(") or ad == a + "…"
                          or ad.startswith(a + "…") for a in adlar):
                return c
    return None


def _tikla(ctrl):
    """Once Invoke (fare oynamaz), sonra Expand (alt menuler), en son gercek tiklama."""
    for deneme in ("invoke", "expand", "click"):
        try:
            if deneme == "invoke":
                ctrl.GetInvokePattern().Invoke()
            elif deneme == "expand":
                ctrl.GetExpandCollapsePattern().Expand()
            else:
                ctrl.Click(waitTime=0.2)
            return True
        except Exception:
            continue
    return False


def _menu_ogesi(kok, parcalar, derinlik=60):
    """Adinda verilen parcalarin HEPSI gecen ilk menu ogesi/dugme (kucuk harf karsilastirma)."""
    parcalar = tuple(x.lower() for x in parcalar)
    for c, _ in auto.WalkControl(kok, includeTop=False, maxDepth=derinlik):
        if c.ControlTypeName in ("MenuItemControl", "ButtonControl", "ListItemControl"):
            ad = (c.Name or "").lower()
            if ad and all(x in ad for x in parcalar):
                return c
    return None


def altyazi_ac(log=print):
    """Altyaziyi acar. 1) Gorunmez yol: UI Automation Invoke ile menu (odak degismez).
    2) Kisayol yolu: toplanti penceresine Alt+Shift+C (Teams 'Alt yazilari goster/gizle').
    Her iki yolda da sonuc altyazi grubunun gorunmesiyle dogrulanir."""
    import time as _t
    if _altyazi_ac_kisayol(log):                  # 1) Alt+Shift+C (arka planda, ~0.2 sn odak)
        return True
    if _altyazi_ac_menu(log):                     # 2) menu yolu (UIA Invoke)
        for _ in range(8):
            _t.sleep(0.5)
            if _grup_bul()[0] is not None:
                return True
    return False


def _altyazi_ac_kisayol(log=print):
    """Alt+Shift+C: Teams'te canli altyazilari ac/kapat. Kisayol odaklanmis pencereye gider;
    bu yuzden toplanti penceresi ~0.2 sn one alinir, tus gonderilir, onceki pencere geri getirilir.
    Ekranda kisa bir odak gecisi olur, baska etkisi yoktur. Altyazi zaten aciksa gonderilmez."""
    import time as _t
    if _grup_bul()[0] is not None:
        return True
    pencereler = [w for w in _toplanti_pencereleri() if _bul_id(w, "hangup-button", derinlik=30) is not None]
    if not pencereler:
        log("kısayol: toplantı penceresi bulunamadı")
        return False
    w = pencereler[0]
    onceki = None
    try:
        onceki = auto.GetForegroundControl()
    except Exception:
        pass
    try:
        w.SetActive(waitTime=0.15)
        auto.SendKeys("{Alt}{Shift}c", waitTime=0.1)
    except Exception as e:
        log(f"kısayol gönderilemedi: {e!r}")
        return False
    finally:
        try:
            if onceki is not None and onceki.NativeWindowHandle != w.NativeWindowHandle:
                onceki.SetActive(waitTime=0.05)
        except Exception:
            pass
    for _ in range(10):
        _t.sleep(0.5)
        if _grup_bul()[0] is not None:
            log("altyazı kısayolla (Alt+Shift+C) açıldı")
            return True
    log("kısayol gönderildi ama altyazı görünmedi (Teams'te 'Alt yazıları göster' bu sürümde Alt+Shift+C mi?)")
    return False


def _altyazi_ac_menu(log=print):
    """Teams toplanti penceresinde  Tumu -> Dil ve konusma -> Canli altyazilari ac.
    Dugmeler AutomationId ile bulunur (teams_dugmeler.py ciktisindan):
      Tumu            : callingButtons-showMoreBtn
      Dil ve konusma  : LanguageSpeechMenuControl-id
    Basarili: True. Arayuz degisirse sessizce False doner, altyazi elle acilir."""
    import time as _t
    pencereler = _toplanti_pencereleri()
    if not pencereler:
        log("altyazı otomatik açma: toplantı penceresi bulunamadı")
        return False
    w = dugme = None
    for aday in pencereler:
        if _bul(aday, _ALTYAZI_ACIK) is not None:
            return True
        d = _bul_id(aday, "callingButtons-showMoreBtn") or _bul(aday, _MENU_DUGME, tipler=("ButtonControl",))
        if d is not None:
            w, dugme = aday, d
            break
    if dugme is None:
        log("altyazı otomatik açma: 'Tümü' düğmesi bulunamadı (toplantı penceresi görünür mü?)")
        return False
    if not _tikla(dugme):
        log("altyazı otomatik açma: 'Tümü' düğmesine tıklanamadı")
        return False
    _t.sleep(0.9)
    dil = _bul_id(w, "LanguageSpeechMenuControl-id") or _bul(w, _DIL_MENU)
    if dil is None:
        ac = _bul(w, _ALTYAZI_AC) or _menu_ogesi(w, ("alt yaz", "aç")) or _menu_ogesi(w, ("caption", "on"))
        if ac is not None and _tikla(ac):
            log("altyazı otomatik açıldı")
            return True
        log("altyazı otomatik açma: 'Dil ve konuşma' menüsü bulunamadı")
        return False
    _tikla(dil)
    _t.sleep(0.9)
    ac = (_bul(w, _ALTYAZI_AC) or _bul_id(w, "ClosedCaptionsMenuControl-id")
          or _bul_id(w, "LiveCaptionsMenuControl-id")
          or _menu_ogesi(w, ("alt yaz", "aç")) or _menu_ogesi(w, ("altyaz", "aç"))
          or _menu_ogesi(w, ("caption", "turn on")) or _menu_ogesi(w, ("caption", "show")))
    if ac is None:
        if _bul(w, _ALTYAZI_ACIK) is not None or _menu_ogesi(w, ("alt yaz", "kapat")) is not None:
            log("altyazı zaten açık")
            return True
        log("altyazı otomatik açma: 'Canlı altyazıları aç' seçeneği bulunamadı")
        return False
    if _tikla(ac):
        log("altyazı otomatik açıldı")
        _t.sleep(1.5)
        altyazi_dili_turkce(log)
        return True
    return False


def altyazi_dili_turkce(log=print):
    """Best effort: altyazi cubugundaki menu (closed-captions-overflow-menu-button) ->
    dil ayari -> 'Türkçe'. Teams konusulan dili kullanici bazinda hatirlar; bir kez elle
    secmek de yeterlidir. Bulamazsa sadece log yazar."""
    import time as _t
    try:
        w = _toplanti_penceresi()
        if w is None:
            return False
        disli = _bul_id(w, "closed-captions-overflow-menu-button")
        if disli is None:
            return False
        _tikla(disli)
        _t.sleep(0.8)
        dil = (_menu_ogesi(w, ("dil ayar",)) or _menu_ogesi(w, ("konuşulan dil",))
               or _menu_ogesi(w, ("language sett",)) or _menu_ogesi(w, ("spoken language",)))
        if dil is None:
            log("altyazı dili: dil menüsü bulunamadı — altyazı çubuğundaki ⚙ → Konuşulan dil: Türkçe (bir kez)")
            return False
        _tikla(dil)
        _t.sleep(0.8)
        tr = _menu_ogesi(w, ("türkçe",)) or _menu_ogesi(w, ("turkish",))
        if tr is None:
            for c, _ in auto.WalkControl(w, includeTop=False, maxDepth=60):
                if c.ControlTypeName == "ComboBoxControl":
                    ad = (c.Name or "") + " " + (getattr(c, "Value", "") or "")
                    if "türkçe" in ad.lower() or "turkish" in ad.lower():
                        log("altyazı dili zaten Türkçe")
                        return True
            log("altyazı dili: 'Türkçe' seçeneği bulunamadı — ⚙ → Konuşulan dil'den bir kez seç")
            return False
        _tikla(tr)
        log("altyazı dili Türkçe olarak seçildi")
        return True
    except Exception as e:
        log(f"altyazı dili ayarlanamadı: {e!r}")
        return False
