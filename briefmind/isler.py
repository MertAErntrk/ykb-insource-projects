"""
isler.py — arayuzu dondurmamasi gereken isler (QThread): yakalama, not uretimi, yeniden ozetleme, soru.
"""
import contextlib
import datetime as dt
import os
import time

from PyQt5 import QtCore

import ayar as ayar_mod
import llm
import motor as motor_mod
from motor import Motor
from yakalayici import Yakalayici, ekran_okuyucu

try:
    import uiautomation as auto
    _UIA_THREAD = getattr(auto, "UIAutomationInitializerInThread", None)
except Exception:                       # Windows disi ortamda import edilemez
    _UIA_THREAD = None


class YakalamaIsi(QtCore.QThread):
    """Toplanti boyunca calisir: altyazi -> motor. Bitince bekleyen onerileri yayar."""
    olay = QtCore.pyqtSignal(str, object)
    durum = QtCore.pyqtSignal(str)
    inceleme_hazir = QtCore.pyqtSignal(list)

    def __init__(self, baslik, ctx, sozluk, duzelt, otobitir, ayar=None):
        super().__init__()
        self.baslik, self.ctx, self.sozluk, self.duzelt, self.otobitir = baslik, ctx, sozluk, duzelt, otobitir
        self.ayar = ayar or {}
        self.motor = None
        self.ses_servisi = None
        self._dur = False

    def durdur(self):
        self._dur = True

    def run(self):
        tarih = dt.date.today()
        klasor = motor_mod.yeni_klasor(self.baslik, tarih)
        kaynak = self.ayar.get("kaynak", "altyazi")
        self.motor = Motor(klasor, self.baslik, tarih, self.sozluk, self.ctx.get("katilimcilar"),
                           self.ctx.get("gundem", ""), duzelt=self.duzelt,
                           olay=lambda t, v: self.olay.emit(t, v),
                           kaynak=kaynak, ben=self.ayar.get("ben") or None,
                           sablon=self.ayar.get("not_sablonu", "genel"))
        self.olay.emit("log", f"Klasör: {klasor}")
        if kaynak in ("ses", "ikisi"):
            try:
                import ses as ses_mod
                self.ses_servisi = ses_mod.SesServisi(
                    self.ayar.get("stt_url", ""),
                    ipucu_fn=lambda: (self.sozluk.metin(self.motor.seri) + "; " + ", ".join(self.motor.katilimcilar)),
                    mod="otomatik",
                    mik_cihaz=self.ayar.get("mikrofon_cihaz"),      # config.json: numara ya da ad; yoksa Windows varsayılanı
                    olay=lambda t, v: self.olay.emit(t, v),
                    satir_fn=lambda ts, kim, metin, akis: self.motor.ses_satiri(ts, kim, metin, akis),
                    model=self.ayar.get("stt_model", "whisper"), api_key=self.ayar.get("stt_key", ""),
                    proxy=self.ayar.get("stt_proxy", True),   # false: Windows proxy'sini atla (kopmalar icin)
                    verify=ayar_mod.tls_dogrulama(self.ayar))
                if not self.ses_servisi.baslat():
                    self.ses_servisi = None
                    self.motor.kaynak = "altyazi"
                    self.olay.emit("kaynak_degisti", "altyazi")
            except Exception as e:
                self.olay.emit("log", f"✖ ses yakalama başlatılamadı: {e!r} — altyazı moduna geçildi")
                self.ses_servisi = None
                self.motor.kaynak = "altyazi"
                self.olay.emit("kaynak_degisti", "altyazi")
        y = Yakalayici()
        basladi = False
        cm = _UIA_THREAD() if _UIA_THREAD else contextlib.nullcontext()
        with cm:
            ekran_okuyucu(True)
            try:
                self.durum.emit("Teams'te altyazı bekleniyor…")
                bekleme_bas, deneme, ipucu_verildi = time.time(), 0, False
                pencere_yok_sn, son_pencere_kontrol = 0.0, time.time()
                while not self._dur:
                    # --- altyazisiz mod: ses akiyorsa toplanti basladi sayilir
                    if not basladi and self.ses_servisi and self.ses_servisi.cozulen > 0:
                        basladi = True
                        self.olay.emit("log", "ses akıyor, yakalama başladı (altyazı yok: konuşmacı adları '?' olur)")
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
                    for s in y.oku():
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
                    if basladi and y.kayip_saniye() > 0:
                        kalan = int(self.otobitir - y.kayip_saniye())
                        if kalan <= 0:
                            self.olay.emit("log", "altyazı kayboldu, toplantı bitti kabul edildi.")
                            self.olay.emit("sure_dondur", None)
                            break
                        self.durum.emit(f"Altyazı görünmüyor — {kalan} sn sonra otomatik bitiş")
                    elif basladi:
                        self.durum.emit(f"Yakalanıyor — {self.motor.parca_no} parça, "
                                        f"açık parça ~{self.motor.mevcut_tok} token")
                    time.sleep(y.onerilen_aralik())      # A7: UIA okumasi yavassa 1,2 sn
            finally:
                ekran_okuyucu(False)
            for s in y.bitir():
                self.motor.altyazi_satiri(s)
        if self.ses_servisi:
            self.durum.emit("Ses kuyruğu boşaltılıyor…")
            self.ses_servisi.durdur()
        self.durum.emit("Parçalar tamamlanıyor…")
        self.inceleme_hazir.emit(self.motor.bitir())


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
            self.olay.emit("log", f"Not üretilemedi: {llm.hata_metni(e)}")
            self.hata.emit(llm.hata_metni(e))
            return
        self.bitti.emit(md)


class ParcaIsi(QtCore.QThread):
    """Tek bir parcayi yeniden ozetler."""
    olay = QtCore.pyqtSignal(str, object)
    bitti = QtCore.pyqtSignal(int)

    def __init__(self, m, no):
        super().__init__()
        self.m, self.no = m, no

    def run(self):
        self.m._olay = lambda t, v: self.olay.emit(t, v)
        self.m.parca_yeniden(self.no)
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
    """Gecmis -> 'Yeniden ozetle': var olan not.md yedeklenir, sozluk tum parcalara uygulanir, TUM parcalar
    yeniden duzeltilip ozetlenir; otomatik_not ise yeni not dogrudan uretilir, degilse inceleme hazirlanir."""
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
