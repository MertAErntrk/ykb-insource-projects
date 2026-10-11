"""
isler.py — arayuzu dondurmamasi gereken isler (QThread): yakalama, not uretimi, yeniden ozetleme, soru.
"""
import contextlib
import datetime as dt
import os
import time
import traceback

from PyQt5 import QtCore

import ayar as ayar_mod
import llm
import motor as motor_mod
import outlook
from motor import Motor
from yakalayici import Yakalayici, ekran_okuyucu

try:
    import uiautomation as auto
    _UIA_THREAD = getattr(auto, "UIAutomationInitializerInThread", None)
except Exception:                       # Windows disi ortamda import edilemez
    _UIA_THREAD = None


def hata_logla(tb):
    """Is parcacigi hatasini hata.log'a ekler (calisma klasoru); asla yeni hata uretmez."""
    try:
        with open("hata.log", "a", encoding="utf-8") as f:
            f.write(f"\n===== {dt.datetime.now():%Y-%m-%d %H:%M:%S} =====\n{tb}")
    except Exception:
        pass


class YakalamaIsi(QtCore.QThread):
    """Toplanti boyunca calisir: ses olaylari (SesServisi olay_fn) ve altyazi okumalari (Yakalayici okuma_fn) -> motor.
    Bitince bekleyen onerileri yayar.
    Her yolda (hata dahil) motor.bitir() bir kez cagrilir ve inceleme_hazir yayilir; hata ayrica hata sinyaliyle."""
    olay = QtCore.pyqtSignal(str, object)
    durum = QtCore.pyqtSignal(str)
    inceleme_hazir = QtCore.pyqtSignal(list)
    hata = QtCore.pyqtSignal(str)

    def __init__(self, baslik, ctx, sozluk, duzelt, otobitir, ayar=None):
        super().__init__()
        self.baslik, self.ctx, self.sozluk, self.duzelt, self.otobitir = baslik, ctx, sozluk, duzelt, otobitir
        self.ayar = ayar or {}
        self.motor = None
        self.ses_servisi = None
        self._dur = False
        self._bitirildi = False

    def durdur(self):
        self._dur = True

    def run(self):
        oneriler = []
        try:
            self._yakala()
        except Exception as e:
            hata_logla(traceback.format_exc())
            self._bildir(f"Yakalama hatası: {type(e).__name__}: {e}")
        finally:
            oneriler = self._kapat()
            self.inceleme_hazir.emit(oneriler)       # arayuz motoru baglar, Baslat'i acar, notu baslatir

    def _bildir(self, mesaj):
        """Hata: gunluge + hata sinyali (arayuz ana thread'de gosterir)."""
        try:
            self.olay.emit("log", f"✖ {mesaj}")
            self.hata.emit(mesaj)
        except Exception:
            pass

    def _kapat(self):
        """Ses servisini durdurur, motor.bitir()'i EN FAZLA BIR KEZ cagirir -> oneriler (hata olursa [])."""
        try:
            if self.ses_servisi:
                self.durum.emit("Ses kuyruğu boşaltılıyor…")
                self.ses_servisi.durdur()
        except Exception as e:
            hata_logla(traceback.format_exc())
            self._bildir(f"Ses servisi durdurulamadı: {type(e).__name__}: {e}")
        if self.motor is None or self._bitirildi:
            return []
        self._bitirildi = True
        self.motor.yeniden_kes_fn = None              # ses durdu: bitiste devir bolunmez ('A → B')
        try:
            self.durum.emit("Parçalar tamamlanıyor…")
            return self.motor.bitir() or []
        except Exception as e:
            hata_logla(traceback.format_exc())
            self._bildir(f"Parçalar tamamlanamadı: {type(e).__name__}: {e}")
            return []

    def _stt_yok(self, neden):
        """Ses servisi kurulamadi/baslatilamadi: altyazi moduna DUSULMEZ; arayuz 'STT yok' durumunu gosterir."""
        self.olay.emit("log", f"✖ ses yakalama başlatılamadı: {neden} — transkript yazılamayacak "
                              "(Teams altyazı metni transkripte girmez)")
        durum = {"durum": "kesinti", "bas": time.time(), "bekleyen": 0, "kayit": False, "eksik": [],
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
        self.motor = Motor(klasor, self.baslik, tarih, self.sozluk, katilimcilar,
                           self.ctx.get("gundem", ""), duzelt=self.duzelt,
                           olay=lambda t, v: self.olay.emit(t, v),
                           kaynak="ses", ben=outlook.ben_adi(self.ayar),   # ayar bossa Outlook/Windows tam adi
                           sablon=self.ayar.get("not_sablonu", "genel"),
                           esikler=self.ayar.get("esikler"),
                           altyazi_yedek=bool(self.ayar.get("altyazi_yedek", False)))
        self.olay.emit("log", f"Klasör: {klasor}")
        olcum = bool(self.ayar.get("olcum_kaydi", False))
        try:
            import ses as ses_mod
            self.ses_servisi = ses_mod.SesServisi(
                self.ayar.get("stt_url", ""),
                ipucu_fn=lambda: (self.sozluk.metin(self.motor.seri) + "; " + ", ".join(self.motor.katilimcilar)),
                mod="otomatik",
                mik_cihaz=self.ayar.get("mikrofon_cihaz"),      # config.json: numara ya da ad; yoksa Windows varsayılanı
                olay=lambda t, v: self.olay.emit(t, v),
                olay_fn=self.motor.ses_olayi,                   # C1: klip / bosluk / kulaklik -> motor
                model=self.ayar.get("stt_model", "whisper"), api_key=self.ayar.get("stt_key", ""),
                proxy=self.ayar.get("stt_proxy", True),   # false: Windows proxy'sini atla (kopmalar icin)
                verify=ayar_mod.tls_dogrulama(self.ayar))
            if olcum:
                self.ses_servisi.olcum_kaydi_ac(klasor)       # olcum/klipler/*.wav + klipler.jsonl (kesinti diski dahil)
            elif self.ayar.get("kesinti_kaydi", False):
                self.ses_servisi.kesinti_kaydi_ac(klasor)     # STT yokken klipler diske, donunce cozulur
            self.motor.yeniden_kes_fn = self.ses_servisi.yeniden_kes
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
                bekleme_bas, deneme, ipucu_verildi = time.time(), 0, False
                pencere_yok_sn, son_pencere_kontrol = 0.0, time.time()
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
                    if (not basladi and not ipucu_verildi and time.time() - bekleme_bas > 20
                            and not self.ayar.get("altyazi_otomatik", True)):
                        ipucu_verildi = True
                        self.olay.emit("log", "altyazı görünmüyor. Konuşmacı adları için Teams'te bir kez: "
                                              "… → Ayarlar → Erişilebilirlik → 'Toplantılarımda her zaman alt yazıları göster'")
                    # --- toplanti penceresi kapandi mi? (altyazi olmadan bitisi anlamak icin, 5 sn'de bir)
                    if basladi and self.ses_servisi and time.time() - son_pencere_kontrol > 5:
                        son_pencere_kontrol = time.time()
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
                            and time.time() - bekleme_bas > 8 * (deneme + 1)):
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
    """Inceleme kararlarini uygular, notu uretir."""
    olay = QtCore.pyqtSignal(str, object)
    bitti = QtCore.pyqtSignal(str)
    hata = QtCore.pyqtSignal(str)

    def __init__(self, m, kararlar):
        super().__init__()
        self.m, self.kararlar = m, kararlar

    def run(self):
        self.m._olay = lambda t, v: self.olay.emit(t, v)
        try:
            n = self.m.kararlari_uygula(self.kararlar)
            if n:
                self.olay.emit("log", f"{n} parça güncellendi.")
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
    transkript_temizle); var olan not.md yedeklenir, sozluk tum parcalara uygulanir, TUM parcalar yeniden
    duzeltilip ozetlenir; otomatik_not ise yeni not dogrudan uretilir, degilse inceleme hazirlanir."""
    olay = QtCore.pyqtSignal(str, object)
    durum = QtCore.pyqtSignal(str)
    bitti = QtCore.pyqtSignal(str)
    inceleme_hazir = QtCore.pyqtSignal(list)
    hata = QtCore.pyqtSignal(str)

    def __init__(self, klasor, sozluk, otomatik_not=True, duzelt=True, ben=None, sablon="genel"):
        super().__init__()
        self.klasor, self.sozluk, self.otomatik_not = klasor, sozluk, otomatik_not
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
            self.motor = Motor.yukle(self.klasor, self.sozluk, olay=lambda t, v: self.olay.emit(t, v),
                                     duzelt=self.duzelt, ben=self.ben, sablon=self.sablon)
            yedek = self.motor.not_yedekle()
            if yedek:
                self.olay.emit("log", f"önceki not yedeklendi: {os.path.basename(yedek)}")
            self.durum.emit("Parçalar yeniden özetleniyor…")
            n = self.motor.yeniden_ozetle()
            self.olay.emit("log", f"{n} parça yeniden özetlendi.")
            oneriler = self.motor.bitir()
            if not self.otomatik_not:
                self.motor.kapat()
                self.inceleme_hazir.emit(oneriler)
                return
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
