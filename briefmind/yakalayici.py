"""
yakalayici.py — Teams canli altyazisini UI Automation ile okur (pop-out gerekmez).
Altyazi grubunu 'Microsoft Teams' gecen herhangi bir pencerede arar; toplanti penceresi
arkada kalabilir ama simge durumuna kucultulmemeli.
"""
import ctypes
import datetime as dt
import difflib
import re

import uiautomation as auto

SPI_SETSCREENREADER = 0x0047
GRUP_ADLARI = ("canlı alt yazı", "live captions")
PENCERE_ANAHTAR = ("teams", "alt yaz", "caption")
user32 = ctypes.windll.user32


def ekran_okuyucu(acik):
    """Edge/Chromium sayfa icerigini ancak bu bayrak acikken UI Automation'a acar."""
    user32.SystemParametersInfoW(SPI_SETSCREENREADER, 1 if acik else 0, None, 0)


def _grup_bul():
    """Altyazi grubunu ve bulundugu pencerenin basligini dondurur: (grup, pencere_basligi)."""
    for w in auto.GetRootControl().GetChildren():
        ad = (w.Name or "").lower()
        if not any(k in ad for k in PENCERE_ANAHTAR):
            continue
        for c, _ in auto.WalkControl(w, includeTop=False, maxDepth=45):
            if c.ControlTypeName == "GroupControl" and (c.Name or "").strip().lower() in GRUP_ADLARI:
                return c, (w.Name or "")
    return None, ""


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
    return yeni.startswith(eski) or difflib.SequenceMatcher(None, eski, yeni).ratio() > 0.6


class Yakalayici:
    def __init__(self):
        self.grup = None
        self.bilinen = set()
        self.kuyruk = []
        self.canli = None
        self.hazir = False
        self.hazir_zamani = None
        self.kayip_zamani = None
        self.pencere_basligi = ""
        self.toplanti_adi = ""

    def _ciftler(self):
        metinler = [(c.Name or "").strip()
                    for c, _ in auto.WalkControl(self.grup, includeTop=False, maxDepth=15)
                    if c.ControlTypeName == "TextControl"]
        metinler = [m for m in metinler if m and not _durum_mesaji(m)]
        ciftler = []
        if metinler and len(metinler) % 2 == 0 and all(
                _isim_gibi(metinler[i], self.bilinen, gevsek=True) for i in range(0, len(metinler), 2)):
            for i in range(0, len(metinler), 2):
                self.bilinen.add(metinler[i])
                if len(metinler[i + 1]) >= 2:
                    ciftler.append((metinler[i], metinler[i + 1]))
            return ciftler
        konusan = None
        for m in metinler:
            if _isim_gibi(m, self.bilinen):
                konusan = m
                self.bilinen.add(m)
            elif konusan and len(m) >= 2:
                ciftler.append((konusan, m))
        return ciftler

    def _hizala(self, onceki):
        k_ = self.kuyruk
        for k in range(min(len(k_), len(onceki)), 0, -1):
            uyar = True
            for i in range(k):
                kay, (kim, ne) = k_[-k + i], onceki[i]
                if kay["speaker"] != kim or (kay["text"] != ne and not (i == k - 1 and _buyumus(kay["text"], ne))):
                    uyar = False
                    break
            if uyar:
                k_[-1]["text"] = onceki[k - 1][1]
                return k
        return 0

    def oku(self):
        """Yeni kesinlesmis satirlari dondurur. self.hazir: altyazi grubu bulundu mu."""
        try:
            if self.grup is None:
                self.grup, baslik = _grup_bul()
                if baslik:
                    self.pencere_basligi = baslik
                    self.toplanti_adi = toplanti_adi_cikar(baslik) or self.toplanti_adi
                if self.grup is None:
                    if self.hazir:
                        self.kayip_zamani = self.kayip_zamani or dt.datetime.now()
                    self.hazir = False
                    return []
                self.hazir, self.kayip_zamani = True, None
                self.hazir_zamani = self.hazir_zamani or dt.datetime.now()
            gorunen = self._ciftler()
        except Exception:
            self.grup = None
            return []
        if not gorunen:
            return []

        simdi = dt.datetime.now().strftime("%H:%M:%S")
        son, onceki = gorunen[-1], gorunen[:-1]
        yeni = []
        k = self._hizala(onceki)
        for kim, ne in onceki[k:]:
            if self.canli and self.canli["speaker"] == kim and _buyumus(self.canli["text"], ne):
                yeni.append({"ts": self.canli["ts"], "speaker": kim, "text": ne})
                self.canli = None
            else:
                yeni.append({"ts": simdi, "speaker": kim, "text": ne})
        if self.canli and self.canli["speaker"] == son[0] and _buyumus(self.canli["text"], son[1]):
            self.canli["text"] = son[1]
        elif self.kuyruk and self.kuyruk[-1]["speaker"] == son[0] and self.kuyruk[-1]["text"] == son[1]:
            self.canli = None
        elif yeni and yeni[-1]["speaker"] == son[0] and yeni[-1]["text"] == son[1]:
            self.canli = None
        else:
            if self.canli:
                yeni.append(self.canli)
            self.canli = {"ts": simdi, "speaker": son[0], "text": son[1]}
        self.kuyruk.extend(yeni)
        self.kuyruk = self.kuyruk[-30:]
        return yeni

    def kayip_saniye(self):
        """Altyazi grubu kayboldugundan beri gecen sure (0 = kayip degil)."""
        if self.kayip_zamani is None:
            return 0
        return (dt.datetime.now() - self.kayip_zamani).total_seconds()

    def bitir(self):
        c, self.canli = self.canli, None
        return [c] if c else []


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
