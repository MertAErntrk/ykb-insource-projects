"""
cihaz.py — Ses cihazlarinin otomatik secimi (kimse "mikrofon_cihaz" ayarlamak zorunda kalmasin).

Sorun: toplantiya bazen kulaklikla, bazen dizustu hoparlor/mikrofonuyla girilir; toplanti ortasinda kulaklik takilir/cikarilir;
Windows'un varsayilan mikrofonu tam sessiz (-98 dB) gelebilir. Eskiden loopback acilista bir kez secilen varsayilan
hoparlorden, mikrofon da config 'mikrofon_cihaz' ya da Windows varsayilanindan aliniyordu.

Arka uc (Arka): girisler() / cikislar() -> [(kimlik, ad)], varsayilan_giris() / varsayilan_cikis() -> kimlik | None,
  ac_giris(kimlik) / ac_loopback(kimlik) -> kayit nesnesi: oku() 16 kHz mono float32 BLOK ornek (cihaz giderse istisna;
  None: bu turda blok yok), kapat(). Gercek arka uc soundcard (WASAPI MMDevice): her cagrida yeniden sayar (tak-cikar ve
  varsayilan degisimi gorunur), mikrofonlar da 16 kHz'te soundcard ile acilir (sounddevice'in 16 kHz reddi ve PortAudio'nun
  acilis anindaki cihaz fotografi sorunu yok). Testler sahte arka uc verir (senkron=True: her tur her cihazdan bir blok).

Her acik cihaz kendi okuyucu ipinde sinirli kuyruga okur (blok sira numarasiyla: yetisen ipin ardisik bloklari atilmaz);
cihaz hatasi yalniz o cihazi kapatir. Windows: her ipte once soundcard yuklenir (Arka.hazirla), COM ondan sonra baslatilir
(soundcard yuklenirken CoInitializeEx'in S_FALSE donusunu hata sayar).

MikrofonSecici (mantiksal "mikrofon" akisi): tum etkin yakalama ucnoktalari acilir (en fazla 4; loopback sahte cihazlari
  degil). Bluetooth eller-serbest (HFP) mikrofonu (acmak kulakligi HFP moduna gecirir) yalniz Windows varsayilaniysa,
  baska mikrofon yoksa, diger mikrofonlarin hepsi tam sessizse ya da kulaklik zaten HFP'deyse (HFP cikisinda ses var) acilir.
  Cihaz basina blok RMS'i: tam sessizlik (tepe < 1e-6 ya da < -90 dBFS) >= 2 sn. Gurultu tabani: son 10 sn'nin tam sessiz
  olmayan bloklarinin alt %10'u (tek sifir blok tabani bozmaz; cihaz ilk kez ses verince sifirlanir); pencerenin >= %10'u
  tam sessizse cihaz kapili/sessize alinmis sayilir (taban ~ sifir). Konusma puani: kullanicinin konustugu 100 ms karelerde
  (karsi taraf loopback'te SUSARKEN; hoparlorden gelen eko kazanamasin) cihazin tabanina gore SNR'i (en fazla 50 dB).
  Kare konusma sayilir: en yuksek SNR'li cihazin tepe/RMS orani dusuk (klavye tiki degil) ve ikinci bir cihaz da yukseliyor
  ya da tek yukselen secili cihaz (ya da secili hic ses duymuyorsa >= 3 kare suren); secili cihaz o karede tam sessizse
  (sessize alma) puanlanmaz.
  Ilk secim: Windows varsayilani sesliyse (ve -80 dBFS'ten kisik degilse) o, yoksa ilk sesli cihaz.
  Gecis: aday ayni konusma karelerinde >= 6 dB onde, >= 3 sn kullanici konusmasi, konusma seviyesi klip acabilecek kadar
  yuksek (ya da seciliden yuksek), son gecisten >= 10 sn (ilk secim sayilmaz); acik klip kapaninca yapilir (klip ortadan
  bolunmez). Acil gecis (acik klip once kapatilir): secili cihaz kaybolur, blok vermez ya da hic ses vermemistir. Sesli
  calisirken tam sifira dusen secili cihaz (sessize alma / gurultu kapisi) listede kaldikca birakilmaz: sessizdeyken odada
  konusulan transkripte girmez ("Mikrofon sessizde" logu); yalniz Windows varsayilani degisirse ona gecilir.
  Akisa yalniz secili cihazin ornekleri girer; gecisten once Gecis isareti gelir (AkisYakalayici klibi kapatir, ornek sayaci
  surer, gerekirse saat capasi yenilenir). 3 sn'de bir yeniden sayim (yeni cihaz acilir, giden kapanir).
LoopbackSecici (mantiksal "loopback" akisi): tum etkin cikis ucnoktalarinin loopback'i (en fazla 4) blok blok toplanip
  [-1, 1]'e kirpilir (eksik blok = sifir): Teams hangi cikisi kullanirsa kullansin karsi taraf yakalanir.
Tavan doluyken: Windows varsayilani (ya da elle ayarli) hep acilir (en dusuk oncelikli cihaz kapatilir: once hic ses
  vermemis, sonra en uzun suredir sesi olmayan); yeni takilan cihaz uzun suredir sessiz olanin, digerleri hic ses vermemis
  olanin yerini alir (kapatilan geri cekilerek yeniden denenir).
Acma hatasi: 1, 2, 4 ... 300 sn geri cekilme; ArkaYok (eski yol) yalniz sayim hep hata verirse ya da listedeki her cihaz
  en az 4 kez acilamadiysa.
Uyarilar: tum mikrofonlar 10 sn tam sessiz ve hicbiri daha once ses vermemis -> bir kez acik uyari (Windows mikrofon izni);
  ses gelince kalkar. Altyazida karsi taraf konusurken 20 sn loopback sesi yoksa ayni sekilde.
"""
import collections
import math
import os
import threading
import time

import numpy as np

import saat as saat_mod

ORNEK = 16000
BLOK = 1600                  # 0.1 sn
KARE = BLOK / ORNEK
MIK_TAVAN = 4                # en fazla bu kadar mikrofon ayni anda acilir
LOOP_TAVAN = 4               # en fazla bu kadar cikis cihazinin loopback'i
SAYIM_SN = 3.0               # cihaz listesi bu aralikla yeniden sayilir
SESSIZ_TEPE = 1e-6           # |x| max bundan kucukse blok tam sessiz
SESSIZ_DB = -90.0            # ... ya da RMS bundan dusukse
SESSIZ_SN = 2.0              # bu kadar ust uste tam sessizlik: cihaz kullanilmaz (sesli calismis secili cihaz birakilmaz)
SESSIZDE_BILDIR_SN = 20.0    # sesli calismis secili cihaz bu kadar tam sessiz: "Mikrofon sessizde" (log + 'mikrofon' olayi)
DURGUN_SN = 2.0              # bu kadar blok gelmezse cihaz kullanilmaz
ACILIS_KARAR_SN = 1.0        # ilk secim icin en fazla bu kadar beklenir (bloklar tamponda, kayip yok)
KONUSMA_SNR_DB = 8.0         # kare konusma sayilir: bir cihazda taban + bu kadar ...
KONUSMA_MIN_DB = -60.0       # ... ve en az bu seviye
KONUSMA_IKINCI_DB = 3.0      # ikinci bir cihaz da tabaninin bu kadar ustundeyse kare tek cihazin sicramasi degil
KONUSMA_TEPE_ORAN = 6.5      # en yuksek SNR'li blogun tepe/RMS orani bundan buyukse tik/tus sesi: kare sayilmaz
KONUSMA_SURE_KARE = 3        # yalniz secili olmayan tek cihaz yukseliyorsa (secili hic ses duymuyorsa) en az bu kadar kare
TABAN_PENCERE = 100          # gurultu tabani: son bu kadar blok (10 sn) ...
TABAN_YUZDE = 0.10           # ... tam sessiz olmayanlarin alt %10'u (mutlak minimum tek sifir blokta takilirdi)
KAPI_ORAN = 0.10             # pencerenin bu orani tam sessizse cihaz kapili / sessize alinmis: puanda taban ~ sifir ...
KAPI_MIN_BLOK = 20           # ... (pencere en az bu kadar blok) ...
KAPI_SESLI_SN = 3.0          # ... cihaz bu kadar kesintisiz sesli kalinca kendi tabani kullanilir
TABAN_SIFIR_DB = -120.0      # kapili cihazin puan tabani
SNR_TAVAN_DB = 50.0          # puanlanan karede SNR bu kadarla kirpilir
KLIP_ESIK_DB = -38.4         # aday cihazin medyan konusma seviyesi bundan dusukse (ses.SESSIZ_ESIK + 6 dB) klip acamaz:
                             # seciliden de kisiksa aday olamaz
NEREDEYSE_SESSIZ_DB = -80.0  # ilk secim: varsayilanin bloklari hep bunun altindaysa sesli baska cihaz tercih edilir
GECIS_DB = 6.0               # aday ortak konusma karelerinde bu kadar onde olmali ...
GECIS_KARE = 30              # ... en az bu kadar karede (3 sn kullanici konusmasi)
PUAN_KARE = 100              # karsilastirma son bu kadar ortak konusma karesi uzerinden (10 sn)
BEKLEME_SN = 10.0            # puana dayali iki gecis arasi en az (ilk secimden sayilmaz)
SINIR_BEKLE_SN = 15.0        # puana dayali gecis acik klibin kapanmasini en fazla bu kadar bekler
ELLE_DONUS_SN = 1.0          # ayarlanan mikrofon bu kadar sesli kalinca ona geri donulur
KARE_GECIKME_SN = 0.35       # kare bu kadar eskiyince degerlendirilir (gec gelen bloklar)
UZAK_ONCE_SN = 0.6           # mikrofon karesi [bas - bu, bit + UZAK_SONRA_SN] icinde loopback aktifse eko olabilir
UZAK_SONRA_SN = 0.15
LOOP_SES_DB = -60.0          # karisim blogu bundan yuksekse loopback'te ses var (karsi taraf aktif)
TAMPON_BLOK = 20             # cihaz basina son 2 sn (ilk secim ve gecis boslugu buradan doldurulur)
OKUYUCU_KUYRUK = 50          # okuyucu ipi kuyrugu (dolarsa en eski atilir)
YENIDEN_DENE_SN = 10.0       # calisirken hata veren cihaz bu sure sonra yeniden denenir
ILK_DENEME_SN = 1.0          # acilamayan cihaz: 1, 2, 4 ... en fazla 300 sn sonra yeniden denenir
ARKA_DENEME_SN = 5.0         # en az bu kadar hicbir cihazdan blok gelmezse ve ...
ARKA_HATA_N = 4              # ... sayim hep hata verirse ya da her cihaz bu kadar kez acilamadiysa ArkaYok: eski yol
YER_SESSIZ_SN = 10.0         # tavan dolu: bu kadar suredir sesi olmayan cihaz yeni takilan cihaza yer acar
YER_DONUS_SN = 30.0          # yer acmak icin kapatilan cihaz bu sure (her seferinde iki kati, en fazla 300) sonra denenir
YENI_CIHAZ_SN = 60.0         # sonradan gorulen cihaz bu kadar sure "yeni takilan" sayilir (tavan doluyken oncelik)
HFP_TAZE_SN = 30.0           # Bluetooth eller-serbest cikisinda son bu surede ses varsa kulaklik zaten HFP modunda
MIK_UYARI_SN = 10.0          # tum mikrofonlar bu kadar tam sessiz: uyari
LOOP_UYARI_SN = 20.0         # altyazida konusma varken bu kadar loopback sesi yok: uyari
ALTYAZI_TAZE_SN = 6.0        # son uzak altyazi etkinligi bu kadar yeniyse "karsi taraf konusuyor"
USTA_DURGUN_SN = 0.5         # karisimin saat cihazi bu kadar blok vermezse baska cihaza gecilir
HIZALA_ONCE_SN = 0.12        # karisim: diger cihazin blogu ana bloktan bu kadar eskiyse atilir
HIZALA_SONRA_SN = 0.08       # ... bu kadar yeniye kadar ayni bloga katilir
KARISIM_GECIKME_SN = 0.06    # ipli modda ana blok bu kadar bekletilir (diger cihazlarin ayni andaki bloklari gelsin)

MIK_UYARI_METNI = ("Mikrofondan ses gelmiyor (tüm mikrofonlar tam sessiz): kendi konuşmalarınız transkripte girmiyor. "
                   "Masaüstü uygulamalarının mikrofon izni kapalı ya da cihaz sessize alınmış olabilir: Windows Ayarlar → "
                   "Gizlilik ve güvenlik → Mikrofon → 'Masaüstü uygulamalarının mikrofonunuza erişmesine izin ver' açık "
                   "olmalı; kulaklık/klavyedeki sessize alma tuşunu da kontrol edin.")
MIK_YOK_METNI = ("Mikrofon bulunamadı: kendi konuşmalarınız transkripte girmiyor. Bir mikrofon/kulaklık takın ya da Windows "
                 "Ayarlar → Gizlilik ve güvenlik → Mikrofon → 'Masaüstü uygulamalarının mikrofonunuza erişmesine izin ver' "
                 "ayarını açın.")
LOOP_UYARI_METNI = ("Teams'te konuşan var (altyazı) ama hoparlör/kulaklık sesi yakalanamıyor: karşı tarafın konuşmaları "
                    "transkripte girmiyor. Teams sesi bilgisayarın bir çıkış cihazından gelmiyor ya da ses kapalı olabilir.")


class ArkaYok(Exception):
    """Arka uc hic calismadi (paket yok / hicbir cihaz acilamadi): cagiran eski yola duser."""


class Gecis:
    """Mantiksal akista cihaz degisimi isareti (AkisYakalayici.run): kapat=True ise acik klip once kapatilir.
    an: gecisten sonraki ilk blogun varis ani (saat capasi denetimi icin) | None.
    taban: yeni cihazin olculen gurultu tabani (dogrusal RMS) | None: VAD esigi ondan baslar."""
    __slots__ = ("kaynak", "kimlik", "ad", "neden", "kapat", "an", "taban")

    def __init__(self, kaynak, kimlik, ad, neden="", kapat=True, an=None, taban=None):
        self.kaynak, self.kimlik, self.ad, self.neden, self.kapat, self.an = kaynak, kimlik, ad, neden, kapat, an
        self.taban = taban

    def __repr__(self):
        return f"Gecis({self.kaynak!r}, {self.ad!r}, neden={self.neden!r}, kapat={self.kapat})"


def _db(x):
    return max(-120.0, 20.0 * math.log10(max(float(x), 1e-12)))


def _norm(ad):
    return " ".join(str(ad or "").replace("İ", "i").replace("I", "ı").lower().split())


def _ole32():
    """Windows ole32 | None (testler sahtesini verir)."""
    if os.name != "nt":
        return None
    import ctypes
    return ctypes.windll.ole32


def _com_hazirla():
    """Windows: bu ipte COM (MTA); okuyucu ipleri icin de garanti. Once arka.hazirla() (soundcard yuklemesi) cagrilmali:
    soundcard yuklenirken kendi CoInitializeEx'ini cagirir ve ayni ipte ikinci cagrinin S_FALSE donusunu hata sayar
    (import basarisiz olur)."""
    try:
        o = _ole32()
        if o is not None:
            o.CoInitializeEx(None, 0)                  # COINIT_MULTITHREADED; zaten acik/farkli modsa zararsiz
    except Exception:
        pass


# ---------------------------------------------------------------- arka uclar

class Arka:
    """Cihaz arka ucu arayuzu. senkron=True (testler): okuyucu ipi yok, her tur her kayittan bir oku()."""
    ad = "?"
    senkron = False

    def hazirla(self):
        """Ip basinda, COM baslatilmadan ONCE: paket yuklemesi (hata: arka uc kullanilamaz)."""

    def girisler(self):
        return []

    def cikislar(self):
        return []

    def varsayilan_giris(self):
        return None

    def varsayilan_cikis(self):
        return None

    def ac_giris(self, kimlik):
        raise NotImplementedError

    def ac_loopback(self, kimlik):
        raise NotImplementedError


class _ScKayit:
    """soundcard kaydi: 16 kHz mono, BLOK'luk okuma (WASAPI paylasimli mod; hiz donusumu Windows'ta)."""

    def __init__(self, cihaz):
        self._r = cihaz.recorder(samplerate=ORNEK, channels=1, blocksize=BLOK)
        self._r.__enter__()

    def oku(self):
        x = np.asarray(self._r.record(numframes=BLOK), dtype=np.float32)
        if x.ndim == 2 and x.shape[1] > 1:
            x = x.mean(axis=1)
        return x.reshape(-1)

    def kapat(self):
        try:
            self._r.__exit__(None, None, None)
        except Exception:
            pass


class SoundcardArka(Arka):
    """soundcard (WASAPI MMDevice). Paket gec yuklenir (yakalama ipinde; Qt ana ipinin COM modu karismaz)."""
    ad = "soundcard"

    def __init__(self, sc=None):
        self._sc = sc

    @property
    def sc(self):
        if self._sc is None:
            import soundcard
            self._sc = soundcard
        return self._sc

    def hazirla(self):
        self.sc                                                 # COM'dan once yuklenir (bkz. _com_hazirla)

    @staticmethod
    def _sayim(cihazlar, loopback_atla=False):
        out = []
        for m in cihazlar:
            try:                                                # .name her cagrida ozellik deposunu acar: cihaz o an
                if loopback_atla and getattr(m, "isloopback", False):    # cikarildiysa / surucu bozuksa yalniz o atlanir
                    continue                                    # loopback sahte cihazi mikrofon degil
                out.append((str(m.id), str(m.name)))
            except Exception:
                continue
        return out

    def girisler(self):
        return self._sayim(self.sc.all_microphones(include_loopback=False), loopback_atla=True)

    def cikislar(self):
        return self._sayim(self.sc.all_speakers())

    def _bul(self, kimlik, loopback):
        """Kimlige gore cihaz (get_microphone tum cihazlarin adini okur: bir cihazin hatasi hepsini bozardi)."""
        for m in self.sc.all_microphones(include_loopback=loopback):
            try:
                if str(m.id) == kimlik and bool(getattr(m, "isloopback", False)) == loopback:
                    return m
            except Exception:
                continue
        raise RuntimeError(f"{'loopback' if loopback else 'mikrofon'} cihazı bulunamadı: {kimlik}")

    def varsayilan_giris(self):
        try:
            return str(self.sc.default_microphone().id)
        except Exception:
            return None

    def varsayilan_cikis(self):
        try:
            return str(self.sc.default_speaker().id)
        except Exception:
            return None

    def ac_giris(self, kimlik):
        return _ScKayit(self._bul(kimlik, False))

    def ac_loopback(self, kimlik):
        return _ScKayit(self._bul(kimlik, True))


def varsayilan_arka():
    """Gercek arka uc (soundcard kuruluysa) | None. Paket bulunursa yuklenmez (yakalama ipinde yuklenir); bulunamazsa
    (ornegin donmus exe'de find_spec desteklenmiyorsa) bir kez yuklemeyi dener."""
    try:
        import importlib.util
        if importlib.util.find_spec("soundcard") is not None:
            return SoundcardArka()
    except Exception:
        pass
    try:
        import soundcard
        return SoundcardArka(soundcard)
    except Exception:
        return None


_BT_HFP = ("hands-free", "handsfree", "hands free", "eller serbest", "ag audio")


def bt_eller_serbest_mi(ad):
    """Bluetooth eller-serbest (HFP) ucnoktasi adi mi ('Kulaklik (XYZ Hands-Free AG Audio)')."""
    n = _norm(ad)
    return any(k in n for k in _BT_HFP)


def sd_cihaz_adi(no):
    """config 'mikrofon_cihaz' numarasi (sounddevice dizini) -> cihaz adi | None."""
    try:
        import sounddevice as sd
        return str(sd.query_devices(int(no))["name"])
    except Exception:
        return None


def altyazi_son_fn(motor):
    """Motor'un konusmaci cizelgesinden 'ben' disindaki kisilerin son altyazi etkinliginin bitisi (epoch) | None.
    LoopbackSecici uyarisi icin (SesServisi(altyazi_fn=...))."""
    def fn():
        try:
            k = motor.konusmaci
            son = None
            for kisi in list(k._kisiler.values()):
                if getattr(kisi, "ben", 0) or not kisi.seg:
                    continue
                t = float(kisi.seg[-1][1])
                son = t if son is None else max(son, t)
            return son
        except Exception:
            return None
    return fn


# ---------------------------------------------------------------- okuyucu

class Okuyucu(threading.Thread):
    """Bir cihazi kendi ipinde acar ve okur; bloklar (blok, varis_ani) sinirli kuyruga (dolarsa en eski atilir).
    Hata/kopma: hata kaydedilir, ip biter (yalniz bu cihaz kapanir)."""

    def __init__(self, ac_fn, ad, saat, bildir, hazirla=None):
        super().__init__(daemon=True, name=f"ses-cihaz:{ad[:30]}")
        self._ac_fn, self._saat, self._bildir, self._hazirla = ac_fn, saat, bildir, hazirla
        self.q = collections.deque(maxlen=OKUYUCU_KUYRUK)
        self.hata = None
        self.bitti = False
        self._dur = threading.Event()

    def durdur(self):
        self._dur.set()

    def run(self):
        kayit = None
        try:
            if self._hazirla is not None:
                self._hazirla()                         # soundcard COM'dan once (bkz. _com_hazirla)
            _com_hazirla()
            kayit = self._ac_fn()
            while not self._dur.is_set():
                b = kayit.oku()
                if b is None:
                    continue
                self.q.append((np.asarray(b, dtype=np.float32).reshape(-1), self._saat()))
                self._bildir.set()
        except Exception as e:
            self.hata = e
        finally:
            self.bitti = True
            if kayit is not None:
                try:
                    kayit.kapat()
                except Exception:
                    pass
            self._bildir.set()


class _Cihaz:
    __slots__ = ("kimlik", "ad", "okuyucu", "kayit", "son_an", "sessiz_sn", "sesli_sn", "sesli_goruldu", "taban",
                 "son_kare", "puan", "tampon", "blok", "son_db", "enerji", "tepe", "acilis_an", "son_ses_an", "en_db",
                 "tpencere", "kapili", "cikan_no", "ust_uste")

    def __init__(self, kimlik, ad, an=None):
        self.kimlik, self.ad = kimlik, ad
        self.okuyucu = None
        self.kayit = None
        self.son_an = None
        self.sessiz_sn = 0.0                # ust uste tam sessizlik
        self.sesli_sn = 0.0                 # ust uste tam sessiz olmayan
        self.sesli_goruldu = False
        self.taban = None                   # gurultu tabani (dBFS): tam sessiz olmayan bloklarin alt %10'u
        self.tpencere = collections.deque(maxlen=TABAN_PENCERE)   # blok dB'leri (tam sessiz: None)
        self.kapili = False                 # pencerede cok tam sessiz blok: gurultu kapisi / sessize alma
        self.son_kare = None
        self.puan = collections.deque(maxlen=PUAN_KARE * 2)    # (kare, snr, db): kullanicinin konustugu kareler
        self.tampon = collections.deque(maxlen=TAMPON_BLOK)    # (blok, varis[, sira no])
        self.cikan_no = 0                   # akisa son cikan blogun sira numarasi (c.blok)
        self.ust_uste = 0                   # ust uste konusma seviyesinde kare
        self.blok = 0                       # gelen blok sayisi (= son blogun sira numarasi)
        self.son_db = None
        self.en_db = None                   # gorulen en yuksek blok seviyesi
        self.enerji = 0.0                   # rapor: toplam kare enerji
        self.tepe = 0.0
        self.acilis_an = an
        self.son_ses_an = None              # son tam sessiz olmayan blogun varisi


# ---------------------------------------------------------------- ortak secici

class _Secici:
    """Sayim, acma/kapama, okuyucu iplerinden toplama. Alt siniflar _liste/_varsayilan/_ac/_isle'yi verir."""
    kaynak = "?"
    tavan = 4

    def __init__(self, arka, olay=None, saat=None, tavan=None, senkron=None):
        self.arka = arka
        self._olay = olay or (lambda t, v: None)
        self._saat = saat or (lambda: saat_mod.simdi())
        if tavan is not None:
            self.tavan = int(tavan)
        self._senkron = bool(getattr(arka, "senkron", False)) if senkron is None else bool(senkron)
        self._cihazlar = collections.OrderedDict()
        self._ret = {}                      # kimlik -> yeniden deneme ani
        self._hata_n = {}                   # kimlik -> ust uste acma hatasi (geri cekilme ve log susturma)
        self._yer_n = {}                    # kimlik -> tavan dolu diye kac kez kapatildi
        self._ilk_gorulme = {}              # kimlik -> ilk goruldugu sayim ani (ilk sayimda vardiysa None)
        self._son_liste = []
        self._atlanan = set()               # son sayimda bilerek acilmayanlar (HFP)
        self._sayim_an = None
        self._sayim_ok = False              # en az bir sayim basarili
        self._varsayilan_k = None
        self._bildir = threading.Event()
        self._basarili = False              # en az bir cihazdan blok geldi
        self._hata_sayisi = 0
        self._son_hata = None
        self._bas_an = None
        self._sayim_hatasi_bildirildi = False
        self.olcum = {"tur": 0, "blok": 0, "sn": 0.0}

    # --- alt sinif ---
    def _liste(self):
        raise NotImplementedError

    def _varsayilan(self):
        return None

    def _ac(self, kimlik):
        raise NotImplementedError

    def _isle(self, gelen, an, klip_acik_fn):
        raise NotImplementedError

    def _sira(self, liste):
        """Acma onceligi: varsayilan once, sonra sayim sirasi."""
        v = self._varsayilan_k
        return sorted(liste, key=lambda x: 0 if x[0] == v else 1)

    def _oncelikli_mi(self, kimlik):
        """Tavan doluyken her cihazin yerini alabilir (Windows varsayilani; mikrofonda elle ayarli da)."""
        return kimlik == self._varsayilan_k

    def _korunan(self, c):
        """Tavan doluyken yer acmak icin kapatilmaz."""
        return c.kimlik == self._varsayilan_k

    # --- sayim ---
    def _say(self, an):
        self._sayim_an = an
        try:
            liste = [(str(k), str(a)) for k, a in self._liste()]
            self._varsayilan_k = self._varsayilan()
        except Exception as e:
            self._hata_sayisi += 1
            self._son_hata = e
            if not self._sayim_hatasi_bildirildi:
                self._sayim_hatasi_bildirildi = True
                self._olay("log", f"! {self.kaynak} cihazları sayılamadı: {e!r}")
            return
        ilk = not self._sayim_ok
        self._sayim_ok = True
        self._son_liste = liste
        for k, _ in liste:
            if k not in self._ilk_gorulme:
                self._ilk_gorulme[k] = None if ilk else an
        mevcut = dict(liste)
        for k in list(self._cihazlar):
            if k not in mevcut:
                self._cihaz_kapat(k, "çıkarıldı", an)
        self._atlanan = set()
        for k, a in self._sira(liste):
            if k in self._cihazlar or self._ret.get(k, an - 1) > an:
                continue
            if not self._acilsin_mi(k, a, liste, an):
                self._atlanan.add(k)
                continue
            if len(self._cihazlar) >= self.tavan and not self._yer_ac(k, a, an):
                continue
            self._cihaz_ac(k, a, an)
        self._sayim_sonrasi(an)

    def _sayim_sonrasi(self, an):
        pass

    def _acilsin_mi(self, kimlik, ad, liste, an):
        return True

    def _yer_ac(self, kimlik, ad, an):
        """Tavan dolu: yeni cihaz icin en dusuk oncelikli acik cihaz kapatilir (korunan: varsayilan, secili, elle ayarli).
        Sinif 0: hic ses vermemis (>= SESSIZ_SN acik), 1: >= YER_SESSIZ_SN suredir sesi yok, 2: digerleri. Varsayilan / elle
        ayarli her sinifin, yeni takilan 0-1'in, digerleri yalniz 0'in yerini alir. -> yer acildi mi."""
        if self._oncelikli_mi(kimlik):
            hak = 2
        else:
            g = self._ilk_gorulme.get(kimlik)
            hak = 1 if g is not None and an - g < YENI_CIHAZ_SN else 0
        en, en_anahtar = None, None
        for c in self._cihazlar.values():
            if self._korunan(c):
                continue
            acik_sn = an - (c.acilis_an if c.acilis_an is not None else an)
            if not c.sesli_goruldu:
                sinif = 0 if acik_sn >= SESSIZ_SN else 2
            elif c.son_ses_an is None or an - c.son_ses_an >= YER_SESSIZ_SN:
                sinif = 1
            else:
                sinif = 2
            anahtar = (sinif, c.son_ses_an if c.son_ses_an is not None else (c.acilis_an or 0.0))
            if sinif <= hak and (en_anahtar is None or anahtar < en_anahtar):
                en, en_anahtar = c, anahtar
        if en is None:
            return False
        n = self._yer_n.get(en.kimlik, 0) + 1
        self._yer_n[en.kimlik] = n
        self._ret[en.kimlik] = an + min(300.0, YER_DONUS_SN * 2 ** (n - 1))
        self._cihaz_kapat(en.kimlik, f"en fazla {self.tavan} cihaz: yer açıldı ({ad[:40]})", an, sessiz=n > 2)
        return True

    def _cihaz_ac(self, kimlik, ad, an):
        c = _Cihaz(kimlik, ad, an)
        if self._senkron:
            try:
                c.kayit = self._ac(kimlik)
            except Exception as e:
                self._acma_hatasi(c, e, an)
                return
        else:
            c.okuyucu = Okuyucu(lambda: self._ac(kimlik), ad, self._saat, self._bildir,
                                hazirla=getattr(self.arka, "hazirla", None))
            c.okuyucu.start()
        self._cihazlar[kimlik] = c
        if self._hata_n.get(kimlik, 0) < 2 and self._yer_n.get(kimlik, 0) < 2:
            self._olay("log", f"  · {self.kaynak} cihazı açıldı: {ad}")

    def _geri_cekil(self, kimlik, an, acilamadi):
        """Yeniden deneme ani: acilamayan cihaz icin 1, 2, 4 ... en fazla 300 sn (gecici hata cabuk duzelir); calisirken
        hata veren icin YENIDEN_DENE_SN. -> ust uste acma hatasi sayisi (calisirken hata: 0)."""
        if acilamadi:
            n = self._hata_n.get(kimlik, 0) + 1
            self._hata_n[kimlik] = n
            self._ret[kimlik] = an + min(300.0, ILK_DENEME_SN * 2 ** (n - 1))
            return n
        self._hata_n[kimlik] = 0
        self._ret[kimlik] = an + YENIDEN_DENE_SN
        return 0

    def _acma_hatasi(self, c, e, an):
        self._hata_sayisi += 1
        self._son_hata = e
        if self._geri_cekil(c.kimlik, an, True) <= 2:
            self._olay("log", f"! {self.kaynak} cihazı açılamadı: {c.ad} ({type(e).__name__}: {str(e)[:80]})")

    def _cihaz_kapat(self, kimlik, neden, an, sessiz=False):
        c = self._cihazlar.pop(kimlik, None)
        if c is None:
            return None
        if c.okuyucu is not None:
            c.okuyucu.durdur()
        if c.kayit is not None:
            try:
                c.kayit.kapat()
            except Exception:
                pass
        if not sessiz:
            self._olay("log", f"  · {self.kaynak} cihazı kapandı: {c.ad} ({neden})")
        self._kapandi(c, an)
        return c

    def _kapandi(self, c, an):
        pass

    def _kayip(self, c, e, an):
        """Okuma hatasi / kopma: yalniz bu cihaz kapanir; bir sure sonra (listede hala varsa) yeniden denenir."""
        acilamadi = c.blok == 0
        if acilamadi:
            self._hata_sayisi += 1
            self._son_hata = e
        n = self._geri_cekil(c.kimlik, an, acilamadi)
        self._cihaz_kapat(c.kimlik, f"hata: {type(e).__name__}: {str(e)[:60]}" if e is not None else "durdu", an,
                          sessiz=n > 2)

    def _arka_yok_mu(self):
        """Arka uc hic calismiyor mu: hicbir cihazdan blok gelmedi, acik cihaz yok ve sayim hep hata verdi ya da listedeki
        (bilerek atlanmayan) her cihaz en az ARKA_HATA_N kez acilamadi. Tek tek acma hatalarinda secici surer."""
        if self._basarili or self._cihazlar or not self._hata_sayisi:
            return False
        if not self._sayim_ok:
            return True
        adaylar = [k for k, _ in self._son_liste if k not in self._atlanan]
        return bool(adaylar) and all(self._hata_n.get(k, 0) >= ARKA_HATA_N for k in adaylar)

    # --- toplama ---
    def _topla(self):
        """-> [(kimlik, blok, varis)]. Senkron: her cihazdan bir oku(). Ipli: bildirim beklenir, kuyruklar bosaltilir."""
        gelen = []
        if self._senkron:
            tik = getattr(self.arka, "tik", None)
            if callable(tik):
                tik(self)
            an = self._saat()
            for c in list(self._cihazlar.values()):
                try:
                    b = c.kayit.oku()
                except Exception as e:
                    self._kayip(c, e, an)
                    continue
                if b is not None:
                    gelen.append((c.kimlik, b, an))
            return gelen
        self._bildir.wait(0.1)
        self._bildir.clear()
        an = self._saat()
        for c in list(self._cihazlar.values()):
            o = c.okuyucu
            while o.q:
                b, a = o.q.popleft()
                gelen.append((c.kimlik, b, a))
            if o.bitti:
                self._kayip(c, o.hata, an)
        gelen.sort(key=lambda x: x[2])
        return gelen

    def _blok_al(self, c, blok, a):
        """Ortak blok olcumleri: tam sessizlik sayaclari, rapor. -> (x, rms, db, sessiz, tepe)."""
        x = np.asarray(blok, dtype=np.float32).reshape(-1)
        if not len(x):
            return None
        tepe = float(np.max(np.abs(x)))
        rms = math.sqrt(float(np.dot(x, x)) / len(x))
        db = _db(rms)
        sn = len(x) / ORNEK
        sessiz = tepe < SESSIZ_TEPE or db < SESSIZ_DB
        if sessiz:
            c.sessiz_sn += sn
            c.sesli_sn = 0.0
        else:
            c.sessiz_sn = 0.0
            c.sesli_sn += sn
            c.sesli_goruldu = True
            c.son_ses_an = a
        c.son_an = a
        c.blok += 1
        if c.blok == 1:
            self._hata_n.pop(c.kimlik, None)               # calisti: acma hatasi sayaci sifirlanir
        c.son_db = db
        c.en_db = db if c.en_db is None else max(c.en_db, db)
        c.enerji += rms * rms
        c.tepe = max(c.tepe, tepe)
        self._basarili = True
        return x, rms, db, sessiz, tepe

    # --- ana dongu ---
    def bloklar(self, dur=None, klip_acik_fn=None):
        """Mantiksal akisin bloklari (ve Gecis isaretleri). dur set edilince biter, tum cihazlar kapanir.
        Senkron modda her tur None ile biter (tur siniri; AkisYakalayici None'u atlar).
        Arka uc yuklenemezse ya da hic calismazsa (_arka_yok_mu, en erken ARKA_DENEME_SN sonra) ArkaYok."""
        dur = dur if dur is not None else threading.Event()
        try:
            self.arka.hazirla()                     # soundcard bu ipte COM'dan ONCE yuklenir (bkz. _com_hazirla)
        except Exception as e:
            raise ArkaYok(f"{self.kaynak}: arka uç yüklenemedi ({e!r})")
        _com_hazirla()
        self._bas_an = self._saat()
        try:
            while not dur.is_set():
                an = self._saat()
                if self._sayim_an is None or an - self._sayim_an >= SAYIM_SN:
                    self._say(an)
                gelen = self._topla()
                an = self._saat()
                t0 = time.perf_counter()
                cikis = self._isle(gelen, an, klip_acik_fn)
                self.olcum["sn"] += time.perf_counter() - t0
                self.olcum["tur"] += 1
                self.olcum["blok"] += len(gelen)
                if an - self._bas_an >= ARKA_DENEME_SN and self._arka_yok_mu():
                    raise ArkaYok(f"{self.kaynak}: hiçbir cihaz açılamadı ({self._son_hata!r})")
                for x in cikis:
                    yield x
                if self._senkron:
                    yield None
        finally:
            self.kapat()

    def kapat(self):
        okuyucular = []
        for k in list(self._cihazlar):
            c = self._cihazlar.pop(k)
            if c.okuyucu is not None:
                c.okuyucu.durdur()
                okuyucular.append(c.okuyucu)
            if c.kayit is not None:
                try:
                    c.kayit.kapat()
                except Exception:
                    pass
        son = time.monotonic() + 1.0
        for o in okuyucular:                  # cihazlar duzgun kapansin (okuma en fazla ~0.1 sn surer)
            o.join(timeout=max(0.0, son - time.monotonic()))

    def blok_maliyeti_us(self):
        """Olcum: islenen blok basina ortalama islem suresi (mikrosaniye) | None."""
        n = self.olcum["blok"]
        return (self.olcum["sn"] / n * 1e6) if n else None

    def cihazlar(self):
        """[(kimlik, ad)] acik cihazlar (teshis/rapor)."""
        return [(c.kimlik, c.ad) for c in self._cihazlar.values()]

    def durum_raporu(self):
        """Teshis: acik cihaz basina {kimlik, ad, varsayilan, blok, rms_db, tepe_db, sessiz, taban_db}."""
        out = []
        for c in self._cihazlar.values():
            out.append({"kimlik": c.kimlik, "ad": c.ad, "varsayilan": c.kimlik == self._varsayilan_k, "blok": c.blok,
                        "rms_db": round(_db(math.sqrt(c.enerji / c.blok)), 1) if c.blok else None,
                        "tepe_db": round(_db(c.tepe), 1) if c.blok else None,
                        "sessiz": bool(c.blok) and not c.sesli_goruldu,
                        "taban_db": round(c.taban, 1) if c.taban is not None else None})
        return out


# ---------------------------------------------------------------- mikrofon secici

class MikrofonSecici(_Secici):
    """Mantiksal "mikrofon" akisi: acik mikrofonlardan secilenin bloklari (bkz. modul aciklamasi).
    elle: config 'mikrofon_cihaz' (sounddevice numarasi, ad ya da kimlik): bulunur ve ses verirse o kullanilir (otomatik
    secim kapali); sessiz/yoksa otomatige dusulur (log), yeniden ses verince ona donulur.
    uzak_fn(bas, bit) -> bool: karsi taraf (loopback) o aralikta aktif mi (LoopbackSecici.aktif_mi).
    hfp_fn() -> bool: Bluetooth kulaklik zaten eller-serbest modunda mi (LoopbackSecici.hfp_aktif_mi)."""
    kaynak = "mikrofon"
    tavan = MIK_TAVAN

    def __init__(self, arka, elle=None, uzak_fn=None, olay=None, saat=None, tavan=None, senkron=None, hfp_fn=None):
        super().__init__(arka, olay=olay, saat=saat, tavan=tavan, senkron=senkron)
        self.elle = None if elle is None or isinstance(elle, bool) or str(elle).strip() == "" else elle
        self._uzak_fn = uzak_fn
        self._hfp_fn = hfp_fn
        self.secili = None
        self._secim = None
        self._secim_varsayilan = None      # secim anindaki Windows varsayilani
        self._elle_k = None
        self._elle_yok_bildirildi = False
        self._bt_bildirilen = set()
        self._son_gecis_an = None
        self._son_cikis_an = None
        self._bekleyen = None              # (kimlik, neden, an): klip sinirini bekleyen gecis
        self._t0 = None
        self._kareler = {}
        self._yeni_kare = False
        self._ilk_blok_an = None
        self._sessiz_bas = None
        self.sessizde = False              # secili cihaz sesli calismisti, simdi tam sessiz (sessize alma)
        self.uyari = None
        self.gecis_sayisi = 0

    def _liste(self):
        return self.arka.girisler()

    def _varsayilan(self):
        v = self.arka.varsayilan_giris()
        return None if v is None else str(v)

    def _ac(self, kimlik):
        return self.arka.ac_giris(kimlik)

    def _sira(self, liste):
        if self.elle is not None:
            self._elle_k = self._elle_coz(liste)
        e, v = self._elle_k, self._varsayilan_k
        return sorted(liste, key=lambda x: 0 if x[0] == e else (1 if x[0] == v else 2))

    def _oncelikli_mi(self, kimlik):
        return kimlik in (self._varsayilan_k, self._elle_k)

    def _korunan(self, c):
        return c.kimlik in (self.secili, self._varsayilan_k, self._elle_k)

    def _acilsin_mi(self, kimlik, ad, liste, an):
        """Bluetooth eller-serbest (HFP) mikrofonu acmak kulakligi dusuk kaliteli HFP moduna gecirir (A2DP ile dinleyenin
        sesi bozulur): yalniz Windows varsayilani / elle ayarli ise, baska mikrofon yoksa, kulaklik zaten HFP'deyse (HFP
        cikisinda ses var) ya da diger mikrofonlarin hepsi tam sessizse (izin kapali dizi: tek ses veren o olabilir)."""
        if not bt_eller_serbest_mi(ad) or kimlik in (self._varsayilan_k, self._elle_k):
            return True
        if all(bt_eller_serbest_mi(a) for _, a in liste):
            return True
        neden = None
        try:
            if self._hfp_fn is not None and self._hfp_fn():
                neden = "kulaklık zaten eller-serbest modunda"
        except Exception:
            pass
        if neden is None and self._hfp_disi_sessiz(liste, an):
            neden = "diğer mikrofonlar tam sessiz"
        if neden is not None:
            self._olay("log", f"  · Bluetooth eller-serbest mikrofonu açılıyor ({neden}): {ad}")
            return True
        if kimlik not in self._bt_bildirilen:
            self._bt_bildirilen.add(kimlik)
            self._olay("log", f"  · Bluetooth eller-serbest mikrofonu açılmadı (Windows varsayılanı değil): {ad}")
        return False

    def _hfp_disi_sessiz(self, liste, an):
        """HFP disindaki mikrofonlarin hepsi olculdu ve hicbiri ses vermiyor (tam sessiz / blok yok / acilamiyor)."""
        for k, a in liste:
            if bt_eller_serbest_mi(a):
                continue
            c = self._cihazlar.get(k)
            if c is None:
                if self._hata_n.get(k, 0) > 0:
                    continue                                    # acilamiyor
                return False                                    # henuz acilmadi: once o olculur
            if c.blok == 0:
                if c.acilis_an is None or an - c.acilis_an < DURGUN_SN:
                    return False                                # yeni acildi
                continue
            if c.sessiz_sn >= SESSIZ_SN or (c.son_an is not None and an - c.son_an >= DURGUN_SN):
                continue
            return False
        return True

    def _sayim_sonrasi(self, an):
        if self.elle is not None and self._elle_k is None and not self._elle_yok_bildirildi:
            self._elle_yok_bildirildi = True
            self._olay("log", f"! Mikrofon: ayarlanan cihaz ({self.elle!r}) bulunamadı, otomatik seçim kullanılıyor")

    def _elle_coz(self, liste):
        d = self.elle
        hedefler = []
        if isinstance(d, int) or (isinstance(d, str) and d.strip().isdigit()):
            ad = sd_cihaz_adi(int(d))
            if ad:
                hedefler.append(ad)
        else:
            s = str(d).strip()
            for k, a in liste:
                if k == s:
                    return k
            hedefler.append(s)
        for h in (_norm(x) for x in hedefler):
            if not h:
                continue
            for k, a in liste:
                if _norm(a) == h:
                    return k
            for k, a in liste:
                n = _norm(a)
                if h in n or (len(n) >= 8 and n in h):
                    return k
        return None

    # --- durum ---
    def secili_mikrofon(self):
        """UI: {"kimlik", "ad", "otomatik", "neden", "sessizde"} | None (henuz secilmedi)."""
        s = self._secim
        return dict(s) if s else None

    def _kullanilir(self, c, an):
        return (c.blok > 0 and c.sessiz_sn < SESSIZ_SN and c.son_an is not None and an - c.son_an < DURGUN_SN)

    def _uygun(self, c, an):
        return self._kullanilir(c, an) and c.sesli_sn > 0

    def _oncelik(self):
        e, v = self._elle_k, self._varsayilan_k
        return sorted(self._cihazlar.values(), key=lambda c: 0 if c.kimlik == e else (1 if c.kimlik == v else 2))

    def _kapandi(self, c, an):
        if self._bekleyen and self._bekleyen[0] == c.kimlik:
            self._bekleyen = None

    # --- bloklar ve kareler ---
    def _isle(self, gelen, an, klip_acik_fn):
        for kimlik, blok, a in gelen:
            c = self._cihazlar.get(kimlik)
            if c is None:
                continue
            hic_sesli = not c.sesli_goruldu
            r = self._blok_al(c, blok, a)
            if r is None:
                continue
            x, rms, db, sessiz, tepe = r
            if self._ilk_blok_an is None:
                self._ilk_blok_an = a
            self._taban_guncelle(c, db, sessiz, hic_sesli)
            c.tampon.append((x, a, c.blok))
            self._kareye(c, db, sessiz, tepe / rms if rms > 0 else 0.0, a)
        self._kareleri_isle(an)
        self._uyari_denetle(an)
        return self._karar(an, klip_acik_fn)

    @staticmethod
    def _taban_guncelle(c, db, sessiz, hic_sesli):
        """Gurultu tabani: penceredeki tam sessiz olmayan bloklarin alt yuzdeligi. Tam sessiz bloklar tabani dusurmez
        (WASAPI acilis paketi, kisa kesinti); cihaz ilk kez ses verince eski sessizlik unutulur. Pencerede cok tam sessiz
        blok varsa ve cihaz yeni ses vermeye basladiysa kapili (gurultu kapisi / sessize alma): puanda taban ~ sifir."""
        p = c.tpencere
        if not sessiz and hic_sesli:
            p.clear()
        p.append(None if sessiz else db)
        sesli = sorted(d for d in p if d is not None)
        if sesli:
            c.taban = sesli[int(len(sesli) * TABAN_YUZDE)]
        c.kapili = (len(p) >= KAPI_MIN_BLOK and len(p) - len(sesli) >= KAPI_ORAN * len(p)
                    and c.sesli_sn < KAPI_SESLI_SN)

    @staticmethod
    def _taban_etkin(c):
        return TABAN_SIFIR_DB if c.kapili else c.taban

    def _kareye(self, c, db, sessiz, oran, a):
        if self._t0 is None:
            self._t0 = a
        k = int(round((a - self._t0) / KARE))
        if c.son_kare is not None and k <= c.son_kare:
            k = c.son_kare + 1                                   # ayni kareye iki blok (titresim): sonrakine
        c.son_kare = k
        self._kareler.setdefault(k, {})[c.kimlik] = (db, sessiz, oran)

    def _kareleri_isle(self, an):
        if self._t0 is None or not self._kareler:
            return
        sinir = an - KARE_GECIKME_SN
        for k in sorted(self._kareler):
            if self._t0 + k * KARE > sinir:
                break
            self._kare_degerlendir(k, self._kareler.pop(k))

    def _kare_degerlendir(self, k, d):
        """Kullanicinin konustugu kare mi; oyleyse karedeki her cihazin SNR'i puana eklenir."""
        cz = self._cihazlar
        yukselen = []                                            # (snr, kimlik, tepe/rms)
        for kimlik, (db, sessiz, oran) in d.items():
            c = cz.get(kimlik)
            if c is None:
                continue
            t = self._taban_etkin(c)
            if not sessiz and t is not None and db >= max(t + KONUSMA_SNR_DB, KONUSMA_MIN_DB):
                c.ust_uste += 1
                yukselen.append((db - t, kimlik, oran))
            else:
                c.ust_uste = 0
        if not yukselen:
            return
        s = d.get(self.secili)
        if s is not None and s[1]:
            return                                               # secili tam sessiz (sessize alma / kapi): puanlanmaz
        t_bit = self._t0 + k * KARE
        t_bas = t_bit - KARE
        if self._uzak_fn is not None:
            try:
                if self._uzak_fn(t_bas - UZAK_ONCE_SN, t_bit + UZAK_SONRA_SN):
                    return                                       # karsi taraf konusuyor: eko kazanmasin
            except Exception:
                pass
        _, en_k, en_oran = max(yukselen)
        if en_oran >= KONUSMA_TEPE_ORAN:
            return                                               # tik / tus sesi (yuksek tepe faktoru)
        ikinci = False
        for kimlik, (db, sessiz, _) in d.items():
            c = cz.get(kimlik)
            t = self._taban_etkin(c) if c is not None else None
            if kimlik != en_k and not sessiz and t is not None and db >= t + KONUSMA_IKINCI_DB:
                ikinci = True
                break
        if not ikinci and en_k != self.secili:
            sec = cz.get(self.secili)
            duymuyor = sec is not None and (sec.en_db is None or sec.en_db < NEREDEYSE_SESSIZ_DB)
            if not duymuyor or cz[en_k].ust_uste < KONUSMA_SURE_KARE:
                return                                           # yalniz bir cihazin sicramasi (secili duyuyorsa
                                                                 # o da yukselirdi): tik ya da eskimis taban
        for kimlik, (db, sessiz, _) in d.items():
            c = cz.get(kimlik)
            if c is None:
                continue
            t = self._taban_etkin(c)
            if sessiz:
                c.puan.append((k, 0.0, TABAN_SIFIR_DB))
            elif t is not None:
                c.puan.append((k, min(SNR_TAVAN_DB, max(0.0, db - t)), db))
        self._yeni_kare = True

    # --- uyari ---
    def _uyari_denetle(self, an):
        cz = list(self._cihazlar.values())
        if (any(c.sesli_sn > 0 and c.son_an is not None and an - c.son_an < DURGUN_SN for c in cz)
                or any(c.sesli_goruldu for c in cz)):
            # ses var ya da daha once ses vermis cihaz susuyor (sessize alma / gurultu kapisi): izin sorunu degil
            self._sessiz_bas = None
            if self.uyari:
                self.uyari = None
                self._olay("log", "✔ Mikrofon sesi geldi")
                self._olay("ses_uyari", {"tur": "mikrofon", "var": False})
            return
        if self._sessiz_bas is None:
            self._sessiz_bas = an
        if not self.uyari and an - self._sessiz_bas >= MIK_UYARI_SN:
            self.uyari = MIK_UYARI_METNI if self._cihazlar else MIK_YOK_METNI
            self._olay("log", "! " + self.uyari)
            self._olay("ses_uyari", {"tur": "mikrofon", "var": True, "metin": self.uyari})

    def _sessizde_denetle(self, sec):
        """Sesli calismis secili cihaz uzun suredir tam sessiz: bir kez bildirilir; ses donunce kalkar."""
        if sec.sesli_goruldu and sec.sessiz_sn >= SESSIZDE_BILDIR_SN:
            if not self.sessizde:
                self.sessizde = True
                self._secim["sessizde"] = True
                self._olay("log", f"Mikrofon sessizde: {sec.ad} (tam sıfır; sessize alınmış olabilir) — kendi "
                                  "konuşmalarınız transkripte girmiyor, başka mikrofona geçilmedi")
                self._olay("mikrofon", dict(self._secim))
        elif self.sessizde and sec.sesli_sn > 0:
            self.sessizde = False
            self._secim["sessizde"] = False
            self._olay("log", f"✔ Mikrofon yeniden ses veriyor: {sec.ad}")
            self._olay("mikrofon", dict(self._secim))

    # --- karar ---
    def _karar(self, an, klip_acik_fn):
        cz = self._cihazlar
        if self.secili is None:
            hedef, neden = self._ilk_secim(an)
            return self._gec(hedef, neden, an, ilk=True) if hedef is not None else []
        sec = cz.get(self.secili)
        acil = None
        if sec is None:
            acil = "önceki cihaz kayboldu"
        elif sec.son_an is None or an - sec.son_an >= DURGUN_SN:
            acil = "önceki cihazdan ses gelmiyor"
        elif sec.sessiz_sn >= SESSIZ_SN and not sec.sesli_goruldu:
            acil = "önceki cihaz tam sessiz"
        if acil is not None:
            # yalniz cihaz kaybolunca / blok vermeyince / hic ses vermemisse: sesli calismis cihazin tam sessizligi
            # sessize alma ya da gurultu kapisidir (asagida), o zaman odadaki konusma baska mikrofondan alinmaz
            aday = self._ilk_uygun(an, haric=self.secili)
            if aday is None and sec is None and cz:
                aday = next(iter(self._oncelik())).kimlik          # hepsi sessiz/yeni: akis sursun
            if aday is not None:
                if self.secili == self._elle_k:
                    acil += "; ayarlanan mikrofon kullanılamıyor, otomatik seçime geçildi"
                return self._gec(aday, acil, an)
            return self._cikis(sec) if sec is not None else []
        self._sessizde_denetle(sec)
        sessiz = sec.sessiz_sn >= SESSIZ_SN
        elle = cz.get(self._elle_k) if self._elle_k is not None else None
        elle_secili = elle is not None and self.secili == elle.kimlik      # elle ayar gecerli: otomatik secim kapali
        if sessiz:
            self._bekleyen = None                                # sessizdeyken puana dayali gecis yok
            v = cz.get(self._varsayilan_k) if self._varsayilan_k is not None else None
            if (not elle_secili and v is not None and v is not sec and self._varsayilan_k != self._secim_varsayilan
                    and self._uygun(v, an)):
                return self._gec(v.kimlik, "Windows varsayılanı değişti", an)
        if self._bekleyen is None and not elle_secili:
            if elle is not None and self._uygun(elle, an) and elle.sesli_sn >= ELLE_DONUS_SN:
                self._bekleyen = (elle.kimlik, "ayarlanan mikrofon yeniden ses veriyor", an)
            elif self._yeni_kare and not sessiz:
                aday = self._puan_adayi(sec, an)
                if aday is not None:
                    self._bekleyen = (aday[0], aday[1], an)
        self._yeni_kare = False
        if self._bekleyen is not None:
            k, neden, t = self._bekleyen
            c = cz.get(k)
            if c is None or not self._kullanilir(c, an):
                self._bekleyen = None
            elif not (klip_acik_fn is not None and klip_acik_fn()) or an - t >= SINIR_BEKLE_SN:
                return self._gec(k, neden, an)
        return self._cikis(sec)

    def _ilk_secim(self, an):
        if not self._cihazlar or self._ilk_blok_an is None:
            return None, None
        elle = self._cihazlar.get(self._elle_k)
        tercih = elle or self._cihazlar.get(self._varsayilan_k)
        if tercih is not None and tercih.sesli_goruldu and (tercih is elle or tercih.en_db >= NEREDEYSE_SESSIZ_DB):
            return tercih.kimlik, ""
        if an - self._ilk_blok_an < ACILIS_KARAR_SN:
            return None, None
        neden = ""
        if tercih is not None:
            if tercih is elle:
                neden = "ayarlanan mikrofon sessiz, otomatik seçildi"
            elif tercih.sesli_goruldu:
                neden = "Windows varsayılanı neredeyse sessiz"
            else:
                neden = "Windows varsayılanı sessiz"
        sesliler = [c for c in self._oncelik() if c.sesli_goruldu and self._kullanilir(c, an)]
        for c in sesliler:
            if c.en_db >= NEREDEYSE_SESSIZ_DB:
                return c.kimlik, ("" if c is tercih else neden)
        if tercih is not None and tercih.sesli_goruldu:
            return tercih.kimlik, ""                             # hepsi kisik: varsayilan
        if sesliler:
            return sesliler[0].kimlik, neden
        c = tercih or next((c for c in self._oncelik() if c.blok), None)
        return (c.kimlik, "hiçbir mikrofon ses vermiyor") if c is not None else (None, None)

    def _ilk_uygun(self, an, haric=None):
        for c in self._oncelik():
            if c.kimlik != haric and self._uygun(c, an):
                return c.kimlik
        return None

    @staticmethod
    def _medyan(x):
        x = sorted(x)
        return x[len(x) // 2] if x else None

    def _puan_adayi(self, sec, an):
        if self._son_gecis_an is not None and an - self._son_gecis_an < BEKLEME_SN:
            return None
        sp = {k: (v, d) for k, v, d in sec.puan}
        en, en_fark = None, GECIS_DB
        for c in self._cihazlar.values():
            if c is sec or not self._uygun(c, an):
                continue
            ortak = [(v - sp[k][0], d, sp[k][1]) for k, v, d in c.puan if k in sp][-PUAN_KARE:]
            if len(ortak) < GECIS_KARE:
                continue
            f = sum(x[0] for x in ortak) / len(ortak)
            if f < en_fark:
                continue
            seviye = self._medyan(x[1] for x in ortak)
            if seviye < KLIP_ESIK_DB and seviye < self._medyan(x[2] for x in ortak):
                continue                                         # SNR iyi ama klip acamayacak kadar kisik
            en, en_fark = c.kimlik, f
        return (en, f"konuşma {en_fark:.0f} dB daha net") if en is not None else None

    def _gec(self, kimlik, neden, an, ilk=False):
        c = self._cihazlar[kimlik]
        self.secili = kimlik
        self._bekleyen = None
        self.sessizde = False
        self._secim_varsayilan = self._varsayilan_k
        if not ilk:                                              # ilk secim bekleme suresi baslatmaz
            self._son_gecis_an = an
            self.gecis_sayisi += 1
        otomatik = kimlik != self._elle_k
        self._secim = {"kimlik": kimlik, "ad": c.ad, "otomatik": otomatik, "neden": neden or "", "sessizde": False}
        self._olay("log", f"Mikrofon: {c.ad} ({'otomatik' if otomatik else 'elle ayarlı'})" + (f" — {neden}" if neden else ""))
        self._olay("mikrofon", dict(self._secim))
        bloklar = self._yeni_bloklar(c, hizala=True)
        taban = 10.0 ** (c.taban / 20.0) if c.taban is not None else None
        return [Gecis(self.kaynak, kimlik, c.ad, neden, kapat=not ilk, an=bloklar[0][1] if bloklar else None,
                      taban=taban)] + [x for x, _ in bloklar]

    def _yeni_bloklar(self, c, hizala=False):
        """Cihazin tamponundan henuz cikmamis bloklar (sira numarasina gore: ipli modda okuyucu takilip yetisirken ardisik
        bloklar < 50 ms arayla gelse de atilmaz). hizala=True (gecis): yeni cihazin tamponu eski cihazin son cikan blogunun
        varis anindan sonrasiyla sinirlanir (gecis boslugu dolar, ortusme olmaz)."""
        s = self._son_cikis_an if hizala else None
        bloklar = [(x, a) for x, a, no in c.tampon if no > c.cikan_no and (s is None or a > s + KARE / 2)]
        if c.tampon:
            c.cikan_no = max(c.cikan_no, c.tampon[-1][2])
        c.tampon.clear()
        if bloklar:
            self._son_cikis_an = bloklar[-1][1]
        return bloklar

    def _cikis(self, c):
        return [x for x, _ in self._yeni_bloklar(c)]


# ---------------------------------------------------------------- loopback secici

class LoopbackSecici(_Secici):
    """Mantiksal "loopback" akisi: acik cikis cihazlarinin loopback'leri toplanip kirpilir. Saat cihazi (usta): Windows
    varsayilani (yoksa en taze blok veren); digerlerinin bloklari varis anina gore hizalanir, eksik blok = sifir.
    altyazi_fn() -> uzak altyazi etkinliginin son ani | None (uyari icin). hfp_aktif_mi(): Bluetooth eller-serbest cikisinda
    yakin zamanda ses var mi (kulaklik zaten HFP modunda: MikrofonSecici HFP mikrofonunu acabilir)."""
    kaynak = "loopback"
    tavan = LOOP_TAVAN

    def __init__(self, arka, olay=None, saat=None, tavan=None, senkron=None, altyazi_fn=None):
        super().__init__(arka, olay=olay, saat=saat, tavan=tavan, senkron=senkron)
        self._altyazi_fn = altyazi_fn
        self._usta = None
        self._aktif = collections.deque(maxlen=300)         # karsi taraf sesli blok varis anlari
        self._aktif_kilit = threading.Lock()
        self._son_ses_an = None
        self._sessiz_altyazi_sn = 0.0
        self._son_denetim = None
        self._set_adlari = None
        self._hfp = {}                                      # kimlik -> Bluetooth eller-serbest cikisi mi
        self._hfp_ses_an = None                             # HFP cikisinda son sesli blok
        self.uyari = None
        self.usta_degisimi = 0

    def _liste(self):
        return self.arka.cikislar()

    def _varsayilan(self):
        v = self.arka.varsayilan_cikis()
        return None if v is None else str(v)

    def _ac(self, kimlik):
        return self.arka.ac_loopback(kimlik)

    def _set_bildir(self):
        adlar = [c.ad for c in self._cihazlar.values()]
        if adlar != self._set_adlari:
            self._set_adlari = adlar
            self._olay("log", "Hoparlör yakalama: " + (" + ".join(adlar) if adlar else "çıkış cihazı yok")
                       + (" (karışım)" if len(adlar) > 1 else ""))

    def _sayim_sonrasi(self, an):
        self._set_bildir()

    def _kapandi(self, c, an):
        if self._sayim_an is not None:
            self._set_bildir()

    def hfp_aktif_mi(self):
        """Baska ipten (mikrofon secici) okunur: yalniz bir sayi okunur."""
        a = self._hfp_ses_an
        return a is not None and self._saat() - a < HFP_TAZE_SN

    def aktif_mi(self, bas, bit):
        """Karsi taraf [bas, bit] icinde loopback'te sesli miydi (mikrofon secicinin eko korumasi)."""
        with self._aktif_kilit:
            for a in reversed(self._aktif):
                if a < bas:
                    return False
                if a <= bit:
                    return True
        return False

    def _usta_sec(self, an):
        cz = self._cihazlar
        u = cz.get(self._usta)
        taze = (lambda c: c.son_an is not None and (self._senkron or an - c.son_an < USTA_DURGUN_SN))
        if u is not None and (taze(u) or u.tampon):
            return u.kimlik
        v = cz.get(self._varsayilan_k)
        if v is not None and v.tampon and taze(v):
            return v.kimlik
        adaylar = [c for c in cz.values() if c.tampon]
        if not adaylar:
            return u.kimlik if u is not None else None
        return max(adaylar, key=lambda c: c.son_an).kimlik

    def _isle(self, gelen, an, klip_acik_fn):
        for kimlik, blok, a in gelen:
            c = self._cihazlar.get(kimlik)
            if c is None:
                continue
            r = self._blok_al(c, blok, a)
            if r is not None:
                c.tampon.append((r[0], a))
                if not r[3]:
                    h = self._hfp.get(kimlik)
                    if h is None:
                        h = self._hfp[kimlik] = bt_eller_serbest_mi(c.ad)
                    if h:
                        self._hfp_ses_an = a
        out = []
        usta = self._usta_sec(an)
        if usta is not None:
            u = self._cihazlar[usta]
            yeni_usta = usta != self._usta
            digerleri = [c for c in self._cihazlar.values() if c is not u]
            # ipli modda ana blok biraz bekletilir: diger cihazlarin ayni andaki bloklari da gelmis olsun
            sinir = an if self._senkron else an - KARISIM_GECIKME_SN
            while u.tampon and u.tampon[0][1] <= sinir:
                x, a = u.tampon.popleft()
                mix = np.array(x, dtype=np.float32, copy=True)
                katildi = False
                for c in digerleri:
                    while c.tampon and c.tampon[0][1] < a - HIZALA_ONCE_SN:
                        c.tampon.popleft()                   # cok eski (kayma): atilir
                    if c.tampon and c.tampon[0][1] <= a + HIZALA_SONRA_SN:
                        y = c.tampon.popleft()[0]
                        n = min(len(y), len(mix))
                        mix[:n] += y[:n]
                        katildi = True
                if katildi:
                    np.clip(mix, -1.0, 1.0, out=mix)
                if yeni_usta:
                    if self._usta is not None:
                        self.usta_degisimi += 1
                    out.append(Gecis(self.kaynak, usta, u.ad, "saat cihazı değişti" if self._usta else "ilk",
                                     kapat=False, an=a))
                    self._usta = usta
                    yeni_usta = False
                self._kaydet(mix, a)
                out.append(mix)
        self._uyari_denetle(an)
        return out

    def _kaydet(self, mix, a):
        db = _db(math.sqrt(float(np.dot(mix, mix)) / max(1, len(mix))))
        if db >= LOOP_SES_DB:
            self._son_ses_an = a
            with self._aktif_kilit:
                self._aktif.append(a)

    def _uyari_denetle(self, an):
        onceki, self._son_denetim = self._son_denetim, an
        if self._altyazi_fn is None:
            return
        if self._son_ses_an is not None and an - self._son_ses_an < 1.0:
            self._sessiz_altyazi_sn = 0.0
            if self.uyari:
                self.uyari = None
                self._olay("log", "✔ Hoparlör sesi geliyor")
                self._olay("ses_uyari", {"tur": "loopback", "var": False})
            return
        try:
            son = self._altyazi_fn()
        except Exception:
            son = None
        if son is None or an - float(son) > ALTYAZI_TAZE_SN or onceki is None:
            return
        self._sessiz_altyazi_sn += max(0.0, min(1.0, an - onceki))
        if not self.uyari and self._sessiz_altyazi_sn >= LOOP_UYARI_SN:
            self.uyari = LOOP_UYARI_METNI
            self._olay("log", "! " + self.uyari)
            self._olay("ses_uyari", {"tur": "loopback", "var": True, "metin": self.uyari})
