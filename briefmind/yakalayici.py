"""
yakalayici.py — Teams canli altyazisini UI Automation ile okur (pop-out gerekmez).
Altyazi grubunu 'Microsoft Teams' gecen herhangi bir pencerede arar; toplanti penceresi
arkada kalabilir ama simge durumuna kucultulmemeli.
"""
import ctypes
import datetime as dt
import difflib
import re
import time
from collections import deque

import uiautomation as auto

from sozluk import normalize

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

    def __init__(self):
        self.grup = None
        self.bilinen = set()
        self.kuyruk = []                           # son yayilan 30 satir (yalnizca tani/geri uyumluluk)
        self.canli = {}                            # konusmaci -> {"ts", "speaker", "text"}: paneldeki son satiri
        self.hazir = False
        self.hazir_zamani = None
        self.kayip_zamani = None
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

    def oku(self):
        """Yeni kesinlesmis satirlari (ve guncellemeleri) dondurur. self.hazir: altyazi grubu bulundu mu.
        Okuma suresi olculur (onerilen_aralik)."""
        bas = time.perf_counter()
        try:
            return self._oku()
        finally:
            self.okuma_sureleri.append(time.perf_counter() - bas)

    def _oku(self):
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
        return self._isle(gorunen, dt.datetime.now().strftime("%H:%M:%S"))

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
        cikan = []
        gorunen_kume = self._son_gorunen
        for aday in self._son_adaylar:
            if aday["a"] in gorunen_kume:
                continue
            if any(kim == aday["kim"] and _buyumus(aday["ne"], ne) for kim, ne in gorunen):
                continue
            if aday["uzun"] and aday["a"] in self._uzun:
                continue
            cikan.append(self._yay(aday)["_satir"])
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
        simdi, bakilan, en, en_puan = time.time(), 0, None, 0.0
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
            k = {"no": self._no, "ts": aday["ts"], "speaker": kim, "text": ne, "zaman": time.time()}
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
        return (dt.datetime.now() - self.kayip_zamani).total_seconds()

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
