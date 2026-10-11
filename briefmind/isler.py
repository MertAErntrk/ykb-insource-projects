"""
isler.py — arayuzu dondurmamasi gereken isler (QThread): yakalama, not uretimi, yeniden ozetleme, soru, takvim/Outlook/
STT testi (ArkaIs), onceki oturumun STT kesinti kuyrugu (KesintiDevamIsi).
"""
import contextlib
import datetime as dt
import os
import threading
import time
import traceback

from PyQt5 import QtCore

import ayar as ayar_mod
import llm
import motor as motor_mod
import outlook
import saat
from motor import Motor
from yakalayici import Yakalayici, ekran_okuyucu

try:
    import uiautomation as auto
    _UIA_THREAD = getattr(auto, "UIAutomationInitializerInThread", None)
except Exception:                       # Windows disi ortamda import edilemez
    _UIA_THREAD = None


def hata_logla(tb):
    """Is parcacigi hatasini hata.log'a ekler (%LOCALAPPDATA%\\BriefMind\\hata.log; yazilamazsa uygulama klasoru);
    asla yeni hata uretmez."""
    ayar_mod.hata_yaz(f"\n===== {dt.datetime.now():%Y-%m-%d %H:%M:%S} =====\n{tb}")


class YakalamaIsi(QtCore.QThread):
    """Toplanti boyunca calisir: ses olaylari (SesServisi olay_fn) ve altyazi okumalari (Yakalayici okuma_fn) -> motor.
    Bitince salt-okunur belirsizler listesini yayar.
    Her yolda (hata dahil) motor.bitir() bir kez cagrilir ve toplanti_bitti yayilir; hata ayrica hata sinyaliyle.
    terimler: paylasilan terim.TerimHavuzu (None: toplantiya ozel havuz). Whisper prompt'u terim.prompt(seri, adlar).
    durdur(hizli=True) (uygulama kapanisi): ses servisinin durmasi ve motor.bitir() KAPANIS_SN icinde sonuclanmazsa
    beklenmez (arka plan thread'inde surer); acik parca motor.bitir()'in basinda diske yazildigi icin veri kaybolmaz.
    Arayuz toplanti_bitti'yi thread'in finished sinyalinden SONRA isler (Baslat ancak thread gercekten bittiginde acilir)."""
    olay = QtCore.pyqtSignal(str, object)
    durum = QtCore.pyqtSignal(str)
    toplanti_bitti = QtCore.pyqtSignal(list)
    hata = QtCore.pyqtSignal(str)
    KAPANIS_SN = 30                         # hizli kapanista ses servisi + acik parcanin diske yazilmasi icin toplam sure

    def __init__(self, baslik, ctx, terimler, duzelt, otobitir, ayar=None):
        super().__init__()
        self.baslik, self.ctx, self.terimler, self.duzelt, self.otobitir = baslik, ctx, terimler, duzelt, otobitir
        self.ayar = ayar or {}
        self.motor = None
        self.ses_servisi = None
        self._dur = False
        self._bitirildi = False
        self._son_tarih = None              # hizli kapanis son tarihi (saat.simdi epoch'u) | None

    def durdur(self, hizli=False):
        if hizli and self._son_tarih is None:
            self._son_tarih = saat.simdi() + self.KAPANIS_SN
        self._dur = True

    def _sinirli(self, fn, pay=1.0):
        """Normal kapanista fn() dogrudan; hizli kapanista arka plan thread'inde, kalan surenin `pay` kadari beklenerek.
        -> (bitti, sonuc, istisna). bitti False: sure doldu, fn arka planda surer (daemon)."""
        if self._son_tarih is None:
            try:
                return True, fn(), None
            except Exception as e:
                return True, None, e
        kutu = {}

        def is_():
            try:
                kutu["sonuc"] = fn()
            except Exception as e:
                kutu["hata"] = e
            finally:
                kutu["bitti"] = True
        t = threading.Thread(target=is_, daemon=True, name="kapanis-" + str(getattr(fn, "__name__", "is")))
        t.start()
        t.join(max(0.2, (self._son_tarih - saat.simdi()) * pay))
        return bool(kutu.get("bitti")), kutu.get("sonuc"), kutu.get("hata")

    def run(self):
        belirsizler = []
        try:
            self._yakala()
        except Exception as e:
            hata_logla(traceback.format_exc())
            self._bildir(f"Yakalama hatası: {type(e).__name__}: {e}")
        finally:
            belirsizler = self._kapat()
            self.toplanti_bitti.emit(belirsizler)    # arayuz motoru baglar, Baslat'i acar, notu baslatir

    def _bildir(self, mesaj):
        """Hata: gunluge + hata sinyali (arayuz ana thread'de gosterir)."""
        try:
            self.olay.emit("log", f"✖ {mesaj}")
            self.hata.emit(mesaj)
        except Exception:
            pass

    def _kapat(self):
        """Ses servisini durdurur, motor.bitir()'i EN FAZLA BIR KEZ cagirir -> belirsizler (hata olursa [])."""
        if self.ses_servisi:
            self.durum.emit("Ses kuyruğu boşaltılıyor…")
            bitti, _, hata = self._sinirli(self.ses_servisi.durdur, pay=0.6)
            if hata is not None:
                hata_logla("".join(traceback.format_exception(type(hata), hata, hata.__traceback__)))
                self._bildir(f"Ses servisi durdurulamadı: {type(hata).__name__}: {hata}")
            elif not bitti:
                self.olay.emit("log", f"! ses servisi {self.KAPANIS_SN} sn sınırında durmadı; açık parça yine de "
                                      "diske yazılıyor")
        if self.motor is None or self._bitirildi:
            return []
        self._bitirildi = True
        self.motor.yeniden_kes_fn = None              # ses durdu: bitiste devir bolunmez ('A → B')
        self.durum.emit("Parçalar tamamlanıyor…")
        bitti, belirsizler, hata = self._sinirli(self.motor.bitir)
        if hata is not None:
            hata_logla("".join(traceback.format_exception(type(hata), hata, hata.__traceback__)))
            self._bildir(f"Parçalar tamamlanamadı: {type(hata).__name__}: {hata}")
            return []
        if not bitti:
            self.olay.emit("log", "! kapanış sınırı doldu: açık parça diske yazıldı, özetler arka planda sürüyor "
                                  "(eksikse Geçmiş → Yeniden özetle)")
            return []
        return belirsizler or []

    def _ipucu(self, akis=None):
        """Whisper prompt'u: katilimci adlari + serinin <=20 ilgili terimi (<=~380 karakter). Bos olabilir."""
        return self.motor.terimler.prompt(self.motor.seri, list(self.motor.katilimcilar))

    def _stt_yok(self, neden):
        """Ses servisi kurulamadi/baslatilamadi: altyazi moduna DUSULMEZ; arayuz 'STT yok' durumunu gosterir."""
        self.olay.emit("log", f"✖ ses yakalama başlatılamadı: {neden} — transkript yazılamayacak "
                              "(Teams altyazı metni transkripte girmez)")
        durum = {"durum": "kesinti", "bas": saat.simdi(), "bekleyen": 0, "kayit": False, "eksik": [],
                 "hata": str(neden)[:200]}
        self.motor.stt_durumu_ayarla(durum)
        self.olay.emit("stt_durum", durum)

    def _yakala(self):
        tarih = dt.date.today()
        klasor = motor_mod.yeni_klasor(self.baslik, tarih)
        if self.ayar.get("kaynak", "ses") != "ses":
            # 'altyazi' / 'ikisi' kaldirildi (MIMARI_v3 §3): metin hep sesten, konusmaci altyazi zamanindan
            self.olay.emit("log", f"! kaynak '{self.ayar.get('kaynak')}' artık yok, ses kullanılıyor "
                                  "(altyazı metni yalnız 'altyazi_yedek' açıkken ve STT yokken girer)")
        katilimcilar = list(self.ctx.get("katilimcilar") or [])
        self.motor = Motor(klasor, self.baslik, tarih, self.terimler, katilimcilar,
                           self.ctx.get("gundem", ""), duzelt=self.duzelt, davet=self.ctx.get("davet"),
                           olay=lambda t, v: self.olay.emit(t, v),
                           kaynak="ses", ben=outlook.ben_adi(self.ayar),   # ayar bossa Outlook/Windows tam adi
                           sablon=self.ayar.get("not_sablonu", "genel"),
                           esikler=self.ayar.get("esikler"),
                           altyazi_yedek=bool(self.ayar.get("altyazi_yedek", False)))
        self.olay.emit("log", f"Klasör: {klasor}")
        try:                                              # davet + onceki not terimleri bu serinin havuzuna (prompt icin)
            self.motor.terimleri_hazirla()
        except Exception as e:
            self.olay.emit("log", f"! davet terimleri alınamadı: {e!r}")
        olcum = bool(self.ayar.get("olcum_kaydi", False))
        try:
            import ses as ses_mod
            self.ses_servisi = ses_mod.SesServisi(
                self.ayar.get("stt_url", ""),
                ipucu_fn=self._ipucu,                           # terim.prompt(seri, adlar): ad + <=20 ilgili terim
                mod="otomatik",
                mik_cihaz=self.ayar.get("mikrofon_cihaz"),      # config.json: elle ayar (numara ya da ad); yoksa otomatik seçim
                cihaz_otomatik=self.ayar.get("cihaz_otomatik", True),   # false: eski yol (Windows varsayılanları)
                altyazi_fn=ses_mod.altyazi_son_fn(self.motor),  # altyazıda konuşma var ama loopback sessiz: uyarı
                olay=lambda t, v: self.olay.emit(t, v),
                olay_fn=self.motor.ses_olayi,                   # C1: klip / bosluk / kulaklik -> motor
                model=self.ayar.get("stt_model", "whisper"), api_key=self.ayar.get("stt_key", ""),
                proxy=self.ayar.get("stt_proxy", True),   # false: Windows proxy'sini atla (kopmalar icin)
                verify=ayar_mod.tls_dogrulama(self.ayar),
                esikler=self.ayar.get("esikler"),               # hal_* (halusinasyon puani), ipucu_rms
                stt_ipucu=bool(self.ayar.get("stt_ipucu", True)),
                stt_repetition_penalty=self.ayar.get("stt_repetition_penalty"))
            # cizelge_fn verilmez: STT aninda altyazi henuz gelmemistir (gecikme L); cizelge kurali konusmaci karari
            # gelince Motor tarafindan uygulanir (motor.cizelge_etkin)
            if olcum:
                self.ses_servisi.olcum_kaydi_ac(klasor)       # olcum/klipler/*.wav + klipler.jsonl (kesinti diski dahil)
            elif self.ayar.get("kesinti_kaydi", False):
                self.ses_servisi.kesinti_kaydi_ac(klasor)     # STT yokken klipler diske, donunce cozulur
            self.motor.yeniden_kes_fn = self.ses_servisi.yeniden_kes
            if self.ayar.get("ses_izi", False):               # Faz 6: altyazisiz '?' satirlara ses izi yardimi
                self.motor.ses_izi_kur(self.ayar, self.ses_servisi.ses_al)
            if not self.ses_servisi.baslat():                 # STT yokken de True (kesinti durumu); False: yakalama yok
                self.ses_servisi = None
                self._stt_yok("ses yakalama açılamadı")
        except Exception as e:
            self.ses_servisi = None
            self.motor.yeniden_kes_fn = None
            self._stt_yok(repr(e))
        y = Yakalayici(katilimcilar=katilimcilar, okuma_fn=self.motor.okuma_ekle)
        if olcum:
            try:
                y.okuma_kaydi_ac(klasor)                          # olcum/okumalar.jsonl
                self.olay.emit("log", "Ölçüm kaydı açık: olcum/ klasörüne klip ve altyazı okumaları yazılıyor")
            except Exception as e:
                self.olay.emit("log", f"✖ ölçüm kaydı açılamadı: {e!r}")
        basladi = False
        cm = _UIA_THREAD() if _UIA_THREAD else contextlib.nullcontext()
        with cm:
            ekran_okuyucu(True)
            try:
                self.durum.emit("Teams'te altyazı bekleniyor…")
                bekleme_bas, deneme, ipucu_verildi = saat.simdi(), 0, False
                pencere_yok_sn, son_pencere_kontrol = 0.0, saat.simdi()
                while not self._dur:
                    stt_kesik = False
                    if self.ses_servisi is not None:
                        try:
                            durum = self.ses_servisi.stt_durumu
                            self.motor.stt_durumu_ayarla(durum)
                            stt_kesik = durum.get("durum") == "kesinti"
                        except Exception:
                            pass
                    else:
                        stt_kesik = True
                    # --- altyazisiz mod: ses olayi geliyorsa (STT yokken bosluk olayi da) toplanti basladi sayilir
                    if not basladi and self.motor.ses_olay_sayisi > 0:
                        basladi = True
                        self.olay.emit("log", "ses akıyor, yakalama başladı (altyazı yok: konuşmacı adları "
                                              "'Bilinmeyen' olur)")
                        self.durum.emit("Yakalanıyor (ses) — altyazı yok")
                    if (not basladi and not ipucu_verildi and saat.simdi() - bekleme_bas > 20
                            and not self.ayar.get("altyazi_otomatik", True)):
                        ipucu_verildi = True
                        self.olay.emit("log", "altyazı görünmüyor. Konuşmacı adları için Teams'te bir kez: "
                                              "… → Ayarlar → Erişilebilirlik → 'Toplantılarımda her zaman alt yazıları göster'")
                    # --- toplanti penceresi kapandi mi? (altyazi olmadan bitisi anlamak icin, 5 sn'de bir)
                    if basladi and self.ses_servisi and saat.simdi() - son_pencere_kontrol > 5:
                        son_pencere_kontrol = saat.simdi()
                        try:
                            from yakalayici import toplanti_penceresi_var
                            if toplanti_penceresi_var():
                                pencere_yok_sn = 0.0
                            else:
                                pencere_yok_sn += 5
                                if pencere_yok_sn >= 45:
                                    self.olay.emit("log", "Teams toplantı penceresi kapandı, toplantı bitti kabul edildi.")
                                    self.olay.emit("sure_dondur", None)
                                    break
                        except Exception:
                            pass
                    if (not basladi and self.ayar.get("altyazi_otomatik", True) and deneme < 2
                            and saat.simdi() - bekleme_bas > 8 * (deneme + 1)):
                        deneme += 1
                        basarili = False
                        try:
                            from yakalayici import altyazi_ac
                            self.olay.emit("log", f"altyazı görünmüyor, Teams'te açmayı deniyorum ({deneme}/2)…")
                            basarili = altyazi_ac(lambda m: self.olay.emit("log", m))
                        except Exception as e:
                            self.olay.emit("log", f"altyazı otomatik açma hatası: {e!r}")
                        if not basarili and deneme >= 2:
                            self.olay.emit("altyazi_iste", None)
                    for s in y.oku():                    # okuma olayi okuma_fn ile motora; satirlar yalniz gorunum/yedek
                        self.motor.altyazi_satiri(s)
                    if y.hazir and not basladi:
                        basladi = True
                        if self.ayar.get("altyazi_turkce", True):
                            try:
                                from yakalayici import altyazi_dili_turkce
                                altyazi_dili_turkce(lambda m: self.olay.emit("log", m))
                            except Exception as e:
                                self.olay.emit("log", f"altyazı dili ayarlanamadı: {e!r}")
                        self.durum.emit("Yakalanıyor")
                        self.olay.emit("log", "altyazı bulundu, yakalama başladı.")
                    self.motor.kontrol()
                    ek = " — STT yok, transkript eksik" if stt_kesik else ""
                    if basladi and y.kayip_saniye() > 0:
                        kalan = int(self.otobitir - y.kayip_saniye())
                        if kalan <= 0:
                            self.olay.emit("log", "altyazı kayboldu, toplantı bitti kabul edildi.")
                            self.olay.emit("sure_dondur", None)
                            break
                        self.durum.emit(f"Altyazı görünmüyor — {kalan} sn sonra otomatik bitiş{ek}")
                    elif basladi:
                        self.durum.emit(f"Yakalanıyor — {self.motor.parca_no} parça, "
                                        f"açık parça ~{self.motor.mevcut_tok} token{ek}")
                    time.sleep(y.onerilen_aralik())      # A7: UIA okumasi yavassa 1,2 sn
            finally:
                ekran_okuyucu(False)
            for s in y.bitir():
                self.motor.altyazi_satiri(s)


class TamamlamaIsi(QtCore.QThread):
    """Notu uretir (toplanti bitince kullanici onayi beklenmez)."""
    olay = QtCore.pyqtSignal(str, object)
    bitti = QtCore.pyqtSignal(str)
    hata = QtCore.pyqtSignal(str)

    def __init__(self, m):
        super().__init__()
        self.m = m

    def run(self):
        self.m._olay = lambda t, v: self.olay.emit(t, v)
        try:
            md = self.m.notu_uret()
            self.m.kapat()
        except Exception as e:
            hata_logla(traceback.format_exc())
            self.olay.emit("log", f"Not üretilemedi: {llm.hata_metni(e)}")
            self.hata.emit(llm.hata_metni(e))
            return
        self.bitti.emit(md)


class ParcaIsi(QtCore.QThread):
    """Tek bir parcayi yeniden ozetler."""
    olay = QtCore.pyqtSignal(str, object)
    bitti = QtCore.pyqtSignal(int)
    hata = QtCore.pyqtSignal(str)

    def __init__(self, m, no):
        super().__init__()
        self.m, self.no = m, no

    def run(self):
        self.m._olay = lambda t, v: self.olay.emit(t, v)
        try:
            self.m.parca_yeniden(self.no)
        except Exception as e:
            hata_logla(traceback.format_exc())
            self.olay.emit("log", f"✖ Parça {self.no} özetlenemedi: {llm.hata_metni(e)}")
            self.hata.emit(llm.hata_metni(e))
            return
        self.bitti.emit(self.no)


class SoruIsi(QtCore.QThread):
    """Toplantiya soru: LLM cagrisi arayuzu dondurmesin."""
    bitti = QtCore.pyqtSignal(str)

    def __init__(self, m, soru):
        super().__init__()
        self.m, self.soru = m, soru

    def run(self):
        try:
            self.bitti.emit(self.m.soru_sor(self.soru))
        except Exception as e:
            self.bitti.emit(f"Cevap alınamadı: {llm.hata_metni(e)}")


class YenidenOzetlemeIsi(QtCore.QThread):
    """Gecmis -> 'Yeniden ozetle': kayitta altyazi tasmasi varsa once transkript temizlenir (motor.
    transkript_temizle); var olan not.md yedeklenir, TUM parcalar yeniden duzeltilip ozetlenir (duzeltme yalniz
    'raw'i olan satirlarda; terim kapisindan gecer) ve yeni not dogrudan uretilir."""
    olay = QtCore.pyqtSignal(str, object)
    durum = QtCore.pyqtSignal(str)
    bitti = QtCore.pyqtSignal(str)
    hata = QtCore.pyqtSignal(str)

    def __init__(self, klasor, terimler, duzelt=True, ben=None, sablon="genel"):
        super().__init__()
        self.klasor, self.terimler = klasor, terimler
        self.duzelt, self.ben, self.sablon = duzelt, ben, sablon
        self.motor = None

    def run(self):
        try:
            if motor_mod.tasma_var_mi(self.klasor):
                # altyazi tasmasiyla bozulmus kayit (ayni satirlar yuzlerce kez): once transkript temizlenir,
                # parcalar temiz satirlardan yeniden kurulur (eski hali .yedek-* olarak klasorde kalir)
                self.durum.emit("Taşmış transkript temizleniyor…")
                t = motor_mod.transkript_temizle(self.klasor)
                if t:
                    self.olay.emit("log", f"taşma temizlendi: {t['once']} → {t['sonra']} satır, "
                                          f"{t['parca_once']} → {t['parca_sonra']} parça (yedekler kayıt klasöründe)")
            self.motor = Motor.yukle(self.klasor, self.terimler, olay=lambda t, v: self.olay.emit(t, v),
                                     duzelt=self.duzelt, ben=self.ben, sablon=self.sablon)
            yedek = self.motor.not_yedekle()
            if yedek:
                self.olay.emit("log", f"önceki not yedeklendi: {os.path.basename(yedek)}")
            self.durum.emit("Parçalar yeniden özetleniyor…")
            n = self.motor.yeniden_ozetle()
            self.olay.emit("log", f"{n} parça yeniden özetlendi.")
            self.motor.bitir()                       # terim ogrenmesi + meta; belirsizler arayuzde motor.belirsizler()
            self.durum.emit("Not üretiliyor…")
            md = self.motor.notu_uret()
            self.motor.kapat()
        except Exception as e:
            self.olay.emit("log", f"Yeniden özetleme başarısız: {llm.hata_metni(e)}")
            self.hata.emit(llm.hata_metni(e))
            return
        self.bitti.emit(md)


class LlmTaniIsi(QtCore.QThread):
    """Acilista ve Ayarlar -> Kaydet sonrasi: LLM sunucusu (model listesi, baglam penceresi) arka planda
    sorulur; test=True ise kisa bir sohbet denemesi de yapilir. bitti(satirlar, basarili)."""
    bitti = QtCore.pyqtSignal(list, bool)

    def __init__(self, test=False):
        super().__init__()
        self.test = test

    def run(self):
        try:
            satirlar = llm.sunucuyu_tani(zorla=True)
            satirlar.append(llm.ayar_ozeti())
            basarili = not (llm.SUNUCU or {}).get("hata")
            if self.test:
                sonuc, tamam = llm.kisa_test()
                satirlar.append(sonuc)
                basarili = basarili and tamam
        except Exception as e:                # tani uygulamayi hicbir zaman dusurmez
            satirlar, basarili = [f"LLM sunucusu denetlenemedi: {llm.hata_metni(e)}"], False
        self.bitti.emit(satirlar, basarili)


# ---------------------------------------------------------------- arka plan yardimcilari (COM / UIA / ag)

class ArkaIs(QtCore.QThread):
    """Arayuzu dondurmamasi gereken tek seferlik blokaj: fn(*args) arka planda calisir. Sonuc bitti(object), istisna
    hata(str) (+ hata.log). Outlook COM islevleri (outlook.com_icinde) kendi thread'lerinde CoInitialize yapar;
    uia=True ise bu thread'de uiautomation baslaticisi kurulur (Teams pencere basliklari icin)."""
    bitti = QtCore.pyqtSignal(object)
    hata = QtCore.pyqtSignal(str)

    def __init__(self, fn, *args, uia=False, ad="arka-is"):
        super().__init__()
        self.fn, self.args, self.uia = fn, args, uia
        self.setObjectName(ad)
        self.calisan_thread = None          # test: islevin calistigi thread (ana thread olmamali)

    def run(self):
        self.calisan_thread = threading.current_thread()
        cm = _UIA_THREAD() if (self.uia and _UIA_THREAD) else contextlib.nullcontext()
        try:
            with cm:
                sonuc = self.fn(*self.args)
        except Exception as e:
            hata_logla(traceback.format_exc())
            self.hata.emit(f"{type(e).__name__}: {e}")
            return
        self.bitti.emit(sonuc)


def takvim_sorgula():
    """Teams penceresindeki toplanti adi (UIA) + Outlook takvimi (COM) -> {"teams_adi", "secilen", "adaylar", "skor"}.
    ArkaIs(takvim_sorgula, uia=True) ile calistirilir; UIA/COM hatasi bos sonuc verir (asla firlatmaz)."""
    teams_adi = ""
    try:
        from yakalayici import teams_pencere_basliklari, toplanti_adi_cikar
        for b in teams_pencere_basliklari():
            ad = toplanti_adi_cikar(b)
            if ad:
                teams_adi = ad
                break
    except Exception:
        pass
    try:
        secilen, adaylar, skor = outlook.toplanti_esle(teams_adi)
    except Exception:
        secilen, adaylar, skor = None, [], 0.0
    return {"teams_adi": teams_adi, "secilen": secilen, "adaylar": adaylar, "skor": skor}


def stt_saglik_testi(adres, model, anahtar, verify):
    """Ayarlar -> 'STT servisini test et' (ag istegi; arayuz thread'inde calistirilmaz). -> (ulasilabilir, neden)."""
    import ses as ses_mod
    ist = ses_mod.SttIstemci(adres, model=model, api_key=anahtar, verify=verify)
    tamam = bool(ist.saglik())
    return tamam, getattr(ist, "saglik_hatasi", None)


# ---------------------------------------------------------------- onceki oturumun STT kesinti kuyrugu

def kesinti_bekleyenleri(kok=None):
    """Onceki oturumdan diskte kalan STT kesinti kuyrugu (<toplanti>/olcum/kesinti/*.json) -> [(klasor, klip_sayisi)]."""
    kok = kok or motor_mod.KOK
    try:
        adlar = sorted(os.listdir(kok))
    except OSError:
        return []
    sonuc = []
    for ad in adlar:
        klasor = os.path.join(kok, ad)
        try:
            n = sum(1 for f in os.listdir(os.path.join(klasor, "olcum", "kesinti")) if f.endswith(".json"))
        except OSError:
            continue
        if n and os.path.exists(os.path.join(klasor, "meta.json")):
            sonuc.append((klasor, n))
    return sonuc


class Duraklatildi(RuntimeError):
    """KesintiDevamIsi duraklatilirken STT cagrisi yerine firlatilir: klip diskte kalir (stt.kesinti_hatasi_mi True)."""


class KesintiDevamIsi(QtCore.QThread):
    """Acilista: onceki oturumdan kalan STT kesinti kuyruklarini (klasorler: [(klasor, klip_sayisi)]) STT ayaktaysa
    cozer. Her klasor Motor.yukle ile acilir, SesServisi yalniz diskteki kuyrugu bosaltir (yakalama baslatilmaz),
    cozulen satirlar motora gider; sonunda motor.bitir (acik parca diske + ozet). Not yeniden uretilmez: Gecmis -> Yeniden
    ozetle. durdur(): kalan klipler diskte kalir (sonraki acilista devam). Hata uygulamayi dusurmez."""
    olay = QtCore.pyqtSignal(str, object)
    bitti = QtCore.pyqtSignal(int, int)               # (cozulen klip, kuyrukta kalan klip)

    KESINTI_BEKLE_SN = 60                             # STT bu kadar suredir yine kesikse vazgec (klipler diskte kalir)

    def __init__(self, klasorler, terimler, ayar=None, duzelt=True, servis_fabrikasi=None):
        super().__init__()
        self.klasorler, self.terimler = list(klasorler), terimler
        self.ayar, self.duzelt = ayar or {}, duzelt
        self.servis_fabrikasi = servis_fabrikasi          # test: fn(motor, klasor) -> SesServisi benzeri
        self._dur = False
        self._servis = None

    def durdur(self):
        self._dur = True
        sv = self._servis
        if sv is not None:
            try:
                sv.duraklat()                             # bekci dongusu bir sonraki turda cikar
            except Exception:
                pass

    def _log(self, m):
        self.olay.emit("log", m)

    def run(self):
        cozulen = kalan = 0
        try:
            for klasor, n in self.klasorler:
                if self._dur:
                    kalan += n
                    continue
                try:
                    c, k = self._bir(klasor, n)
                except Exception as e:
                    hata_logla(traceback.format_exc())
                    self._log(f"✖ kesinti kuyruğu çözülemedi ({os.path.basename(klasor)}): {type(e).__name__}: {e}")
                    c, k = 0, n
                cozulen += c
                kalan += k
        finally:
            self.bitti.emit(cozulen, kalan)

    def _servis_kur(self, m, klasor):
        if self.servis_fabrikasi:
            return self.servis_fabrikasi(m, klasor)
        import ses as ses_mod
        a = self.ayar
        return ses_mod.SesServisi(
            a.get("stt_url", ""), ipucu_fn=lambda akis=None: m.terimler.prompt(m.seri, list(m.katilimcilar)),
            mod="otomatik", olay=lambda t, v: self.olay.emit(t, v) if t == "log" else None, olay_fn=m.ses_olayi,
            model=a.get("stt_model", "whisper"), api_key=a.get("stt_key", ""), proxy=a.get("stt_proxy", True),
            verify=ayar_mod.tls_dogrulama(a), esikler=a.get("esikler"), stt_ipucu=bool(a.get("stt_ipucu", True)),
            stt_repetition_penalty=a.get("stt_repetition_penalty"), kesinti_klasor=klasor)

    def _bir(self, klasor, n):
        ad = os.path.basename(klasor)
        m = Motor.yukle(klasor, self.terimler, olay=lambda t, v: self.olay.emit(t, v), duzelt=self.duzelt,
                        ben=outlook.ben_adi(self.ayar), sablon=self.ayar.get("not_sablonu", "genel"))
        try:
            servis = self._servis_kur(m, klasor)
            self._servis = servis
            if not servis.stt.saglik():
                self._log(f"! önceki oturumdan {n} ses klibi çözülmeyi bekliyor ({ad}); STT şu an ulaşılamıyor, "
                          "sonraki açılışta yeniden denenecek")
                return 0, n
            self._log(f"önceki oturumdan {n} ses klibi çözülüyor ({ad})…")
            kalan = servis.kesinti_bosalt(
                duraklatildi=lambda: self._dur, kesinti_bekle_sn=self.KESINTI_BEKLE_SN, duraklat_hatasi=Duraklatildi,
                kesinti_asildi=lambda: (self._log("! STT kuyruk çözülürken yine kesildi; kalan klipler sonraki açılışa "
                                                  "bırakıldı"), self.durdur()))
            m.bitir()                                     # acik parca diske + ozet; durum 'bitti' (not yeniden uretilebilir)
            cozulen = max(0, n - kalan)
            if cozulen:
                self._log(f"✔ {ad}: {cozulen} klip çözüldü" + (f", {kalan} klip hâlâ bekliyor" if kalan else "")
                          + "; notu yenilemek için Geçmiş → Yeniden özetle")
            elif kalan:
                self._log(f"! {ad}: {kalan} klip çözülemedi, diskte duruyor")
            return cozulen, kalan
        finally:
            self._servis = None
            try:
                m.kapat()
            except Exception:
                pass
