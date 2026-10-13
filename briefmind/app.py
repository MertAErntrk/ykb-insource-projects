"""
app.py — Toplanti Notu masaustu uygulamasi (PyQt5).

  pip install -r requirements.txt PyQt5
  python app.py

Sekmeler: Canli | Not | Gecmis | Ayarlar. Tepside kucuk ikon.
Akis: Baslat -> Teams'te altyazi gorunene kadar bekler -> yakalar, parcalari arka planda isler
      -> Bitir (ya da altyazi kaybolunca otomatik) -> toplanti_bitti sinyali: not dogrudan uretilir (kullanici onayi
      YOK), Not sekmesinde ilerleme seridi ve salt-okunur "Belirsizler" paneli (+ Outlook taslagi)
Terimler: terimler/terimler.json (terim.py) davetten / onceki notlardan / dogrulanmis duzeltmelerden kendiliginden
      birikir; elle eklemek icin terimler/elle.txt. Metne kor degisiklik yoktur (duzelt.py kapisi).
Modul yapisi: isler.py (QThread'ler), gorunum.py (tema, md_to_html), ayar.py (config.json).
Mesgul durum makinesi (Pencere._durum): bos | yakalaniyor | not_uretiliyor | yeniden_ozetleniyor | soru. Baslat, Bitir,
      Yeniden ozetle, Notu yeniden uret ('uygula'), Sor ve Ayarlar->Kaydet her durumda IZINLER tablosuna gore acik/kapali;
      kilitler (yakalama, not, yeniden ozetleme) ilgili thread'in finished sinyaliyle acilir.
Kapanis: closeEvent iscilere durmalarini soyler, finished'lerini (sinirli sure) bekler; yakalamada acik parca <=30 sn'de
      diske yazilir. Outlook COM / UIA / STT testi arayuz thread'inde calismaz (isler.ArkaIs).
Duman testi: python app.py --duman (pencereyi ekransiz kurar, 0 = tamam; derlenmis exe icin de gecerli).
"""
import datetime as dt
import json
import os
import subprocess
import sys
import threading

from PyQt5 import QtCore, QtGui, QtWidgets

import ayar as ayar_mod
import isler as isler_mod
import llm
import rag
import motor as motor_mod
import not_araclari
import outlook
import saat
from ayar import ayar_oku, ayar_yaz
from gorunum import QSS, RENKLER, kart, md_to_html, svg_ikon  # noqa: F401  (md_to_html: app.md_to_html uyumu)
from isler import (ArkaIs, KesintiDevamIsi, LlmTaniIsi, ParcaIsi, SoruIsi, TamamlamaIsi, YakalamaIsi,
                   YenidenOzetlemeIsi, hata_logla, kesinti_bekleyenleri, stt_saglik_testi, takvim_sorgula)
from metin import gorunen_ad
from motor import Motor
import terim as terim_mod


DURUMLAR = ("bos", "yakalaniyor", "not_uretiliyor", "yeniden_ozetleniyor", "soru")
# Durum -> hangi eylemler acik. baslat: Baslat; bitir: Bitir; yeniden: Gecmis -> Yeniden ozetle / Secili parcayi yeniden
# ozetle / kayit silme; uygula: 'Notu yeniden uret' (eski 'Uygula ve notu uret'in yerine); sor: Sor; kaydet: Ayarlar -> Kaydet.
IZINLER = {
    "bos":                 {"baslat": True,  "bitir": False, "yeniden": True,  "uygula": True,  "sor": True,  "kaydet": True},
    "yakalaniyor":         {"baslat": False, "bitir": True,  "yeniden": False, "uygula": False, "sor": False, "kaydet": True},
    "not_uretiliyor":      {"baslat": False, "bitir": False, "yeniden": False, "uygula": False, "sor": False, "kaydet": False},
    "yeniden_ozetleniyor": {"baslat": False, "bitir": False, "yeniden": False, "uygula": False, "sor": False, "kaydet": False},
    "soru":                {"baslat": True,  "bitir": False, "yeniden": False, "uygula": False, "sor": False, "kaydet": True},
}
KAPANIS_YAKALAMA_SN = 35                  # yakalama kapanisi: isler.YakalamaIsi.KAPANIS_SN (30) + pay
KAPANIS_URETIM_SN = 600                   # not / yeniden ozetleme / soru: LLM cagrisi bitene kadar en fazla bu kadar beklenir
KAPANIS_KISA_SN = 10                      # takvim, STT testi, tani: kisa isler


def _calisiyor(is_):
    return is_ is not None and is_.isRunning()


def _taslaklari_ac(epostalar):
    """Kisiye ozel e-posta taslaklari (arka plan thread'i): -> (acilan, toplam)."""
    acilan = 0
    for ad, konu, govde in epostalar:
        if outlook.taslak(konu, govde, [ad]):
            acilan += 1
    return acilan, len(epostalar)


class Pencere(QtWidgets.QMainWindow):
    SAYFALAR = ["Canlı", "Not", "Geçmiş", "Ayarlar"]
    IKON_AD = ["canli", "not", "gecmis", "ayarlar"]
    SAYFA_CANLI, SAYFA_NOT, SAYFA_GECMIS, SAYFA_AYARLAR = 0, 1, 2, 3
    BELIRSIZ_TAVAN = 300                  # Belirsizler panelinde en fazla bu kadar satir (kalani sayi olarak)
    TAKVIM_ZAMAN_ASIMI_MS = 20000         # Baslat'in takvim/Teams sorgusunu bekleme siniri

    def __init__(self, duman=False):
        super().__init__()
        self.duman = bool(duman)              # --duman: ag/COM/UIA/zamanlayici yok, yalniz pencere kurulumu
        self.setWindowTitle("BriefMind")
        self.resize(1180, 740)
        self.setStyleSheet(QSS)
        self.ayar = ayar_oku()
        llm.ayarla(self.ayar)                # uygulama klasorundeki config.json (LLM adresi, ca_bundle) gecerli
        rag.ayarla(self.ayar)                # embedding (toplantiya soru icin anlamsal arama)
        self.terimler = terim_mod.TerimHavuzu()       # terimler/terimler.json (uygulama klasoru); toplantilar paylasir
        self.is_ = None
        self.motor = None
        self.not_md = ""
        self.ctx = {}
        self.baslangic = None
        self._baslangic_t = 0.0              # baslangic'in saat.simdi() epoch'u (sayac duvar saati atlamasindan etkilenmez)
        self.bitis = None
        self._akis_satirlari = []
        self.renkler = {}
        self._canli_ozetler = {}             # parca sirasi -> ozet (canli ara sayac icin)
        self._serit_bas = None               # not uretimi ilerleme seridinin baslangic zamani
        self.tamamla = None
        self.yeniden = None
        self.parca_isi = None
        self._soru_isi = None
        self._llm_tani_isi = None
        self._takvim_isi = None              # takvim + Teams penceresi sorgusu (ArkaIs; COM/UIA arka planda)
        self._takvim_devamlari = []          # sorgu bitince cagrilacak fonksiyonlar (Baslat'in takvimli devami vb.)
        self._stt_test_isi = None
        self.kesinti_isi = None              # onceki oturumun STT kesinti kuyrugu (KesintiDevamIsi)
        self._arka_isler = []                # taslak/kullanici adi gibi kisa ArkaIs'ler (referans tutulur)
        self._adaylar = []
        self._tur_elle = None                # Tür kutusunda ELLE secilen (slug(baslik), sablon); ayni baslikta otomatik ezmez
        self._tur_kaynak = "otomatik"        # kutudaki degerin kaynagi: "otomatik" | "seri" (seri hafizasi) | "elle"
        self._tur_neden = ""                 # kutudaki degerin nedeni (tooltip)
        self._tur_bekleyen = None            # yakalama sirasinda motor henuz yokken secilen: motor baglaninca uygulanir
        self._not_kilit = False              # not uretilirken Baslat kapali (yeni toplanti eski nota baglanmasin); TamamlamaIsi.finished'te acilir
        self._not_sonuc_geldi = False        # TamamlamaIsi bitti/hata sinyali verdi mi (finished'te sessiz olum ayrimi)
        self._yeniden_kilit = False          # Gecmis -> Yeniden ozetle suruyor (YenidenOzetlemeIsi.finished'te acilir)
        self._yeniden_sonuc_geldi = False
        self._parca_kilit = False            # Gecmis -> secili parcayi yeniden ozetle suruyor (ParcaIsi.finished'te acilir)
        self._soru_kilit = False             # toplantiya soru suruyor (SoruIsi.finished'te acilir)
        self._yakalama_acik = False          # Baslat ile acildi; toplanti_bitti (thread finished SONRASI) ile kapanir
        self._bitiriliyor = False            # Bitir'e basildi, thread henuz bitmedi
        self._baslat_bekliyor = False        # takvim sorgusu / kesinti duraklatmasi bitince Baslat devam edecek
        self._baslat_takvim = False          # ... ve bekledigi sey takvim sorgusu mu
        self._bekleyen_belirsizler = None    # YakalamaIsi.toplanti_bitti sinyali ile gelir, finished'te islenir
        self._not_uret_aday = False          # 'Notu yeniden uret' icin hazir bir motor var mi
        self._kullanici_adi = ""             # Outlook/Windows tam adi (arka planda alinir)
        self._kapaniyor = False              # closeEvent iscileri durduruyor
        self._kapanis_tamam = False          # closeEvent nihai kabul
        self._kapanis_zamanlayici = None
        self._kapanis_son = {}               # id(thread) -> bitis tarihi (saat.simdi epoch'u)
        self.yetim_isler = []                # kapanis siniri dolunca hala calisan thread'ler (main os._exit eder)
        self._durum_adi = "bos"
        self._kur()
        for u in ayar_mod.ayar_uyarilari_al():       # config.json tip/goc uyarilari (bir kez)
            self.log("! " + u)
        if self.terimler.yukleme_hatasi:
            self.log(f"! terimler/terimler.json okunamadı, boş havuzla başlandı: {self.terimler.yukleme_hatasi}")
        self._tls_bildir()
        if not self.duman:
            self.llm_tani()                  # model adi / baglam penceresi sunucudan (arka planda)
            if not self.a_ben.text().strip():
                self._arka(outlook.kullanici_adi, bitti=self._kullanici_adi_geldi, ad="kullanici-adi")
        self._tepsi()
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self, self.ana_dugme)
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+1"), self, lambda: self.nav.setCurrentRow(0))
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+2"), self, lambda: self.nav.setCurrentRow(self.SAYFA_NOT))
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+3"), self, lambda: self.nav.setCurrentRow(self.SAYFA_GECMIS))
        self.zamanlayici = QtCore.QTimer(self)
        self.zamanlayici.timeout.connect(self._sayac_guncelle)
        self._takvim_zamanlayici = QtCore.QTimer(self)                 # bostayken takvim listesi tazelenir
        self._takvim_zamanlayici.timeout.connect(lambda: self.toplantilari_yenile(False))
        self._saklama_zamanlayici = QtCore.QTimer(self)                # KVKK: saklama suresi her saat yeniden uygulanir
        self._saklama_zamanlayici.timeout.connect(lambda: self.saklama_uygula())
        self._kesinti_deneme = 0
        self._durum_uygula()
        if self.duman:
            return
        self._takvim_zamanlayici.start(60000)
        self._saklama_zamanlayici.start(self.SAKLAMA_ARALIK_MS)
        self._tek_atim(800, lambda: self.toplantilari_yenile(False))
        self.zamanlayici.start(1000)
        self._tek_atim(1500, self.saklama_uygula)
        self._tek_atim(4000, self.kesinti_devam)                       # onceki oturumdan kalan STT kuyrugu
        if self.ayar.get("otomatik_basla"):
            self._tek_atim(500, self.baslat)

    def _tek_atim(self, ms, fn):
        """Pencereye bagli tek atimlik zamanlayici: pencere yok olunca iptal olur (QTimer.singleShot ile bagli yontem,
        pencere/yamali fonksiyon yok edildikten sonra tetiklenip cokebilir). fn guclu referansla tutulur."""
        t = QtCore.QTimer(self)
        t.setSingleShot(True)
        t.timeout.connect(self._tek_atim_cagri(fn))
        t.start(ms)
        return t

    @staticmethod
    def _tek_atim_cagri(fn):
        def cagri():
            fn()
        return cagri

    # ================================================================== iskelet
    def _kur(self):
        kok = QtWidgets.QWidget(objectName="icerik")
        self.setCentralWidget(kok)
        h = QtWidgets.QHBoxLayout(kok)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._kenar())
        sag = QtWidgets.QVBoxLayout()
        sag.setContentsMargins(0, 0, 0, 0)
        sag.setSpacing(0)
        sag.addWidget(self._baslik_bar())
        self.sayfalar = QtWidgets.QStackedWidget()
        for s in (self._canli(), self._not(), self._gecmis(), self._ayarlar()):
            self.sayfalar.addWidget(s)
        sag.addWidget(self.sayfalar, 1)
        h.addLayout(sag, 1)
        self.durum = QtWidgets.QLabel("Hazır")
        self.statusBar().addWidget(self.durum, 1)
        self.ilerleme = QtWidgets.QProgressBar()
        self.ilerleme.setRange(0, 0)
        self.ilerleme.setFixedWidth(140)
        self.ilerleme.hide()
        self.statusBar().addPermanentWidget(self.ilerleme)

    def _kenar(self):
        k = QtWidgets.QFrame(objectName="kenar")
        k.setFixedWidth(200)
        v = QtWidgets.QVBoxLayout(k)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(QtWidgets.QLabel("BriefMind", objectName="marka"))
        v.addWidget(QtWidgets.QLabel("Teams altyazısı → Qwen → not", objectName="marka_alt"))
        self.nav = QtWidgets.QListWidget(objectName="nav")
        self.nav.setIconSize(QtCore.QSize(18, 18))
        for ad, ikon in zip(self.SAYFALAR, self.IKON_AD):
            self.nav.addItem(QtWidgets.QListWidgetItem(svg_ikon(ikon), ad))
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(lambda i: self.sayfalar.setCurrentIndex(i))
        v.addWidget(self.nav, 1)
        alt = QtWidgets.QHBoxLayout()
        alt.setContentsMargins(16, 10, 16, 16)
        self.nokta = QtWidgets.QLabel("●", objectName="durum_nokta")
        self.nokta.setStyleSheet("color:#99F6E4")
        self.nokta_yazi = QtWidgets.QLabel("Beklemede", objectName="durum_yazi")
        alt.addWidget(self.nokta)
        alt.addWidget(self.nokta_yazi, 1)
        v.addLayout(alt)
        return k

    def _baslik_bar(self):
        b = QtWidgets.QFrame(objectName="baslik_bar")
        h = QtWidgets.QHBoxLayout(b)
        h.setContentsMargins(20, 12, 20, 12)
        sol = QtWidgets.QVBoxLayout()
        ust = QtWidgets.QHBoxLayout()
        self.toplanti_secici = QtWidgets.QComboBox(objectName="toplanti_adi")
        self.toplanti_secici.setEditable(True)
        self.toplanti_secici.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.toplanti_secici.setMinimumWidth(420)
        self.toplanti_secici.lineEdit().setPlaceholderText("Toplantı adı — takvimdeki toplantılar listede")
        self.toplanti_secici.currentIndexChanged.connect(self._toplanti_secildi)
        self.baslik = self.toplanti_secici.lineEdit()          # eski kodla uyum: .text()/.setText()
        b_yenile = QtWidgets.QToolButton()
        b_yenile.setText("⟳")
        b_yenile.setToolTip("Takvimdeki toplantıları yenile")
        b_yenile.clicked.connect(lambda: self.toplantilari_yenile(True))
        ust.addWidget(self.toplanti_secici, 1)
        ust.addWidget(b_yenile)
        self.alt_bilgi = QtWidgets.QLabel("Teams'e girdikten sonra Başlat'a bas; altyazı görünür görünmez yakalama başlar.",
                                          objectName="alt_bilgi")
        sol.addLayout(ust)
        sol.addWidget(self.alt_bilgi)
        h.addLayout(sol, 1)
        # toplanti turu (not sablonu): Baslat'tan ONCE davetten otomatik dolar, toplanti sirasinda da degistirilebilir
        tur = QtWidgets.QVBoxLayout()
        tur.addWidget(QtWidgets.QLabel("Tür", objectName="alt_bilgi"))
        self.tur_secici = QtWidgets.QComboBox(objectName="tur_secici")
        self.tur_secici.addItem("Otomatik", llm.SABLON_OTOMATIK)
        for anahtar, (etiket, _) in llm.SABLONLAR.items():
            self.tur_secici.addItem(etiket, anahtar)
        self.tur_secici.setMinimumWidth(170)
        self.tur_secici.activated.connect(self._tur_secildi)             # yalniz kullanici secimi (programatik degil)
        tur.addWidget(self.tur_secici)
        h.addLayout(tur)
        h.addSpacing(16)
        self.sayac = QtWidgets.QLabel("00:00", objectName="sayac")
        h.addWidget(self.sayac)
        h.addSpacing(16)
        self.b_ana = QtWidgets.QPushButton("Başlat", objectName="birincil")
        self.b_ana.setMinimumWidth(140)
        self.b_ana.clicked.connect(self.ana_dugme)
        h.addWidget(self.b_ana)
        return b

    def _sayfa(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(12)
        return w, v

    # ================================================================== sayfalar
    def _canli(self):
        w, v = self._sayfa()
        kartlar = QtWidgets.QHBoxLayout()
        self.k_durum = kart("Durum", "Beklemede")
        self.k_parca = kart("Parça", "0 / 0")
        self.k_belirsiz = kart("Belirsiz", "0")
        self.k_katilimci = kart("Konuşan", "0")
        self.k_kaynak = kart("Kaynak", "—")
        self.k_sablon = kart("Şablon", "—")              # toplantinin not sablonu (davetten otomatik ya da elle)
        for k in (self.k_durum, self.k_parca, self.k_belirsiz, self.k_katilimci, self.k_kaynak, self.k_sablon):
            kartlar.addWidget(k)
        v.addLayout(kartlar)
        h = QtWidgets.QHBoxLayout()
        sol = QtWidgets.QVBoxLayout()
        baslik = QtWidgets.QHBoxLayout()
        baslik.addWidget(QtWidgets.QLabel("Konuşmalar"))
        baslik.addStretch(1)
        # aktif mikrofon (cihaz.py otomatik secer; SesServisi 'mikrofon' olayi)
        self.mik_etiket = QtWidgets.QLabel("", objectName="alt_bilgi")
        self.mik_etiket.hide()
        baslik.addWidget(self.mik_etiket)
        sol.addLayout(baslik)
        # ses uyarisi: tum mikrofonlar tam sessiz / altyazida konusma var ama hoparlor sesi yok ('ses_uyari' olayi)
        self.ses_serit = QtWidgets.QLabel("", objectName="uyari")
        self.ses_serit.setWordWrap(True)
        self.ses_serit.hide()
        sol.addWidget(self.ses_serit)
        self._ses_uyarilari = {}
        # durum seritleri: STT kesintisi (transkript eksik) ve konusmaci bilgisi yok (altyazi okunamiyor)
        self.stt_serit = QtWidgets.QLabel("", objectName="uyari")
        self.stt_serit.setWordWrap(True)
        self.stt_serit.hide()
        sol.addWidget(self.stt_serit)
        self.kapsama_serit = QtWidgets.QLabel("Konuşmacı bilgisi yok — Teams altyazısı kapalı ya da okunamıyor; "
                                              "konuşmacılar 'Bilinmeyen' yazılıyor.", objectName="uyari")
        self.kapsama_serit.setWordWrap(True)
        self.kapsama_serit.hide()
        sol.addWidget(self.kapsama_serit)
        self._kapsama_serit_metni = self.kapsama_serit.text()
        self.atama_serit = QtWidgets.QLabel("Konuşmacı atanamıyor — altyazı okunuyor ama konuşmalarla eşleşmiyor; "
                                            "panel yeniden aranıyor, gecikme yeniden ölçülüyor.", objectName="uyari")
        self.atama_serit.setWordWrap(True)
        self.atama_serit.hide()
        sol.addWidget(self.atama_serit)
        self.akis = QtWidgets.QTextBrowser()
        self.akis.setOpenExternalLinks(False)
        sol.addWidget(self.akis, 1)
        sag = QtWidgets.QVBoxLayout()
        self.canli_sayac = QtWidgets.QLabel("", objectName="canli_sayac")
        self.canli_sayac.setToolTip("Özetlenen parçalardan, benzer maddeler birleştirilerek (LLM'siz) sayılır")
        sag.addWidget(self.canli_sayac)
        self._canli_sayac_guncelle()
        sag.addWidget(QtWidgets.QLabel("Oluşan not (canlı)"))
        self.canli_not = QtWidgets.QListWidget(objectName="liste")
        self.canli_not.setWordWrap(True)
        sag.addWidget(self.canli_not, 2)
        b_bilgi = QtWidgets.QPushButton("Katılımcıları bilgilendir (metni kopyala)")
        b_bilgi.setToolTip("Toplantı sohbetine yapıştırılacak 'not alınıyor' bilgilendirmesini panoya kopyalar")
        b_bilgi.clicked.connect(self.bilgilendirme_kopyala)
        sag.addWidget(b_bilgi)
        sag.addWidget(QtWidgets.QLabel("Olaylar"))
        self.gunluk = QtWidgets.QPlainTextEdit()
        self.gunluk.setReadOnly(True)
        self.gunluk.setMaximumBlockCount(400)
        sag.addWidget(self.gunluk, 1)
        h.addLayout(sol, 3)
        h.addLayout(sag, 2)
        v.addLayout(h, 1)
        return w

    def _not(self):
        w, v = self._sayfa()
        # durum seridi: not uretilirken uygulama donmus gibi gorunmesin (asama + gecen sure)
        self.not_serit = QtWidgets.QFrame(objectName="serit")
        hs_ = QtWidgets.QHBoxLayout(self.not_serit)
        hs_.setContentsMargins(12, 8, 12, 8)
        self.serit_bar = QtWidgets.QProgressBar()
        self.serit_bar.setRange(0, 0)
        self.serit_bar.setTextVisible(False)
        self.serit_bar.setFixedWidth(160)
        self.serit_metin = QtWidgets.QLabel("Not üretiliyor…")
        self.serit_sure = QtWidgets.QLabel("0 sn", objectName="alt_bilgi")
        hs_.addWidget(self.serit_bar)
        hs_.addWidget(self.serit_metin, 1)
        hs_.addWidget(self.serit_sure)
        self.not_serit.hide()
        v.addWidget(self.not_serit)
        self.not_bilgi = QtWidgets.QLabel("Not henüz üretilmedi.", objectName="alt_bilgi")
        hb = QtWidgets.QHBoxLayout()
        hb.addWidget(self.not_bilgi, 1)
        # toplantinin sablonu: davetten otomatik secilir; degistirmek istege bagli (hicbir adim bunu beklemez)
        hb.addWidget(QtWidgets.QLabel("Şablon:", objectName="alt_bilgi"))
        self.not_sablon = QtWidgets.QComboBox()
        for anahtar, (etiket, _) in llm.SABLONLAR.items():
            self.not_sablon.addItem(etiket, anahtar)
        self.not_sablon.setEnabled(False)
        self.not_sablon.activated.connect(self._sablon_secildi)          # yalniz kullanici secimi (programatik degil)
        hb.addWidget(self.not_sablon)
        v.addLayout(hb)
        self.not_goster = QtWidgets.QTextBrowser()
        self.not_goster.document().setDocumentMargin(16)
        self.not_goster.setOpenLinks(False)
        self.not_goster.anchorClicked.connect(lambda u: self._baglanti(u, self.motor.klasor if self.motor else None))
        v.addWidget(self.not_goster, 1)
        self.not_duzen = QtWidgets.QPlainTextEdit()
        self.not_duzen.hide()
        v.addWidget(self.not_duzen, 1)
        # salt-okunur "Belirsizler": belirsiz kelimeler / emin olunmayan konusmacilar / supheli klipler + kaynak baglantisi
        # (onay dugmesi YOK; hicbir sey buradan metne uygulanmaz)
        self.belirsiz_kutu = QtWidgets.QGroupBox("Belirsizler (salt okunur)")
        self.belirsiz_kutu.setCheckable(True)
        self.belirsiz_kutu.setChecked(False)
        bk = QtWidgets.QVBoxLayout(self.belirsiz_kutu)
        self.belirsiz_goster = QtWidgets.QTextBrowser()
        self.belirsiz_goster.setOpenLinks(False)
        self.belirsiz_goster.setMaximumHeight(180)
        self.belirsiz_goster.anchorClicked.connect(lambda u: self._baglanti(u, self.motor.klasor if self.motor else None))
        bk.addWidget(self.belirsiz_goster)
        self.belirsiz_kutu.toggled.connect(self.belirsiz_goster.setVisible)
        self.belirsiz_goster.hide()
        v.addWidget(self.belirsiz_kutu)
        # toplantiya soru
        hs = QtWidgets.QHBoxLayout()
        self.soru = QtWidgets.QLineEdit()
        self.soru.setPlaceholderText("Bu toplantıya soru sor (örn. Test ortamı ne zaman hazır olacak, kim söyledi?)")
        self.soru.returnPressed.connect(self.soru_sor)
        self.b_sor = QtWidgets.QPushButton("Sor")
        self.b_sor.clicked.connect(self.soru_sor)
        hs.addWidget(self.soru, 1)
        hs.addWidget(self.b_sor)
        v.addLayout(hs)
        self.cevap = QtWidgets.QTextBrowser()
        self.cevap.setOpenLinks(False)
        self.cevap.anchorClicked.connect(lambda u: self._baglanti(u, self.motor.klasor if self.motor else None))
        self.cevap.setMaximumHeight(170)
        self.cevap.hide()
        v.addWidget(self.cevap)
        h = QtWidgets.QHBoxLayout()
        self.b_outlook = QtWidgets.QPushButton("Outlook'ta taslak aç", objectName="birincil")
        self.b_outlook.clicked.connect(self.outlook_ac)
        b_kisi = self.b_kisi = QtWidgets.QPushButton("Kişiye özel e-postalar")
        b_kisi.setToolTip(self.KISI_IPUCU)
        b_kisi.clicked.connect(self.kisiye_ozel_eposta)
        b2 = QtWidgets.QPushButton("Panoya kopyala")
        b2.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(self.not_md))
        self.b_duzenle = QtWidgets.QPushButton("Düzenle")
        self.b_duzenle.clicked.connect(self.not_duzenle)
        b_word = QtWidgets.QPushButton("Word")
        b_word.clicked.connect(lambda: self.not_disa_aktar("docx"))
        b_pdf = QtWidgets.QPushButton("PDF")
        b_pdf.clicked.connect(lambda: self.not_disa_aktar("pdf"))
        b_jira = self.b_jira = QtWidgets.QPushButton("Jira/Planner CSV")
        b_jira.setToolTip(self.JIRA_IPUCU)
        b_jira.clicked.connect(self.jira_disa_aktar)
        b3 = QtWidgets.QPushButton("Klasörü aç")
        b3.clicked.connect(self.klasor_ac)
        self.b_not_uret = QtWidgets.QPushButton("Notu yeniden üret")
        self.b_not_uret.setToolTip("Bu toplantının notunu mevcut parça özetlerinden yeniden üretir (LLM hatasından sonra)")
        self.b_not_uret.setEnabled(False)
        self.b_not_uret.clicked.connect(lambda: self._not_uret())
        for b in (self.b_outlook, b_kisi, b2, self.b_duzenle, b_word, b_pdf, b_jira, b3, self.b_not_uret):
            h.addWidget(b)
        h.addStretch(1)
        v.addLayout(h)
        return w

    KISI_IPUCU = "Sorumlusu belli her kişi için yalnızca kendi işlerini içeren bir Outlook taslağı açar"
    JIRA_IPUCU = "Aksiyonları Jira 'Import from CSV' / Planner içe aktarma için CSV olarak kaydeder"

    def _sablon_goster(self):
        """Canli kart + Not sayfasi secicisi: acik motorun (toplantinin) sablonu ve nedeni; birebir'de kisiye ozel e-posta /
        Jira dugmeleri kisa aciklamayla kapali (not_araclari.taslak_izni)."""
        m = self.motor
        sablon = getattr(m, "sablon", None) if m else None
        neden = getattr(m, "sablon_neden", "") if m else ""
        if sablon:
            self.k_sablon.deger.setText(llm.sablon_etiketi(sablon))
            self.k_sablon.setToolTip(neden)
            self.not_sablon.setCurrentIndex(max(0, self.not_sablon.findData(sablon)))
            self.not_sablon.setToolTip(f"{neden}. İstersen değiştir: not yeni şablonla yeniden üretilir.")
            if self._tur_yakalamada():              # canli toplanti: Tür kutusu motorun gercek sablonunu gosterir
                self._tur_uygula(sablon, neden, "elle" if neden.endswith("elle seçildi") else "otomatik")
        else:
            self._tur_karti()                       # motor yok: kart Tür kutusundaki (Baslat'a hazir) oneriyi gosterir
        self._sablon_secici_uygula()
        izin, aciklama = not_araclari.taslak_izni(sablon, self.ayar)
        for b, ipucu in ((self.b_kisi, self.KISI_IPUCU), (self.b_jira, self.JIRA_IPUCU)):
            b.setEnabled(izin)
            b.setToolTip(ipucu if izin else aciklama)

    # ---------------------------------------------------------------- Tür kutusu (Baslat'tan once toplanti turu)
    def _tur_yakalamada(self):
        """Toplanti yakalaniyor ve motor bagli: Tür kutusu motorun sablonunu yansitir, secim motor.sablon_degistir'e gider."""
        return bool(self.motor is not None and getattr(self.motor, "sablon", None)
                    and (self._yakalama_acik or self._yakalama_suruyor()))

    def _tur_anahtar(self, baslik):
        """Elle secimin bagli oldugu baslik anahtari; baslik henuz yoksa None (bekleyen elle secim: ilk cozulen basliga
        uygulanir). slug('') 'toplanti' oldugu icin bos baslik ayrica tutulur."""
        baslik = (baslik or "").strip()
        return motor_mod.slug(baslik) if baslik else None

    def _tur_oner_ayrintili(self, baslik, ctx):
        """(sablon, neden, kaynak): seri hafizasi > ayarda sabit sablon > davetten tespit; ipucu yoksa Otomatik (emin
        olunamayan sey kesin gosterilmez)."""
        ctx = ctx or {}
        seri = getattr(Motor, "seri_son_sablon", None)           # Motor.seri_son_sablon (baslik) -> (sablon, neden) | None
        if callable(seri) and baslik:
            try:
                bulunan = seri(baslik)
            except Exception as e:
                self.log(f"! seri şablon hafızası okunamadı: {e!r}")
                bulunan = None
            if bulunan and bulunan[0] in llm.SABLONLAR:
                return bulunan[0], bulunan[1] or "aynı seride son toplantıda elle seçilmişti", "seri"
        govde = " ".join(x for x in (ctx.get("gundem"), (ctx.get("davet") or {}).get("govde")) if x)    # Motor ile ayni
        sablon, neden = llm.sablon_sec(self.ayar.get("not_sablonu", llm.SABLON_OTOMATIK), baslik, govde)
        if sablon == "genel" and neden.startswith("otomatik"):
            return llm.SABLON_OTOMATIK, neden + "; Başlat'ta ya da toplantı sırasında seçebilirsin", "otomatik"
        return sablon, neden, "otomatik"

    def _tur_oner(self, baslik, ctx):
        """Baslik + davetten onerilen toplanti turu -> (sablon, neden); sablon 'otomatik' olabilir."""
        return self._tur_oner_ayrintili(baslik, ctx)[:2]

    def _tur_uygula(self, sablon, neden, kaynak="otomatik"):
        """Kutuyu programatik ayarlar (sinyal yok); tooltip = neden; yakalama disinda Canli kart da ayni."""
        i = self.tur_secici.findData(sablon)
        self.tur_secici.setCurrentIndex(max(0, i))
        self.tur_secici.setToolTip(neden)
        self._tur_neden, self._tur_kaynak = neden, kaynak
        if not self._tur_yakalamada():          # yakalamada kartı _sablon_goster (motorun sablonu) yazar
            self._tur_karti()

    def _tur_karti(self):
        sablon = self.tur_secici.currentData()
        self.k_sablon.deger.setText(self.tur_secici.currentText() if sablon else "—")
        self.k_sablon.setToolTip(self._tur_neden)

    def _tur_yenile(self, baslik, ctx):
        """Secili / yazili basliga gore kutuyu doldurur; AYNI baslik icin elle secilen ezilmez."""
        baslik = (baslik or "").strip()
        anahtar = self._tur_anahtar(baslik)
        if self._tur_elle and self._tur_elle[0] is None and anahtar is not None:
            # baslik yokken secilen tur: ilk cozulen basliga (takvim secimi / Baslat'ta Teams-takvim) baglanir, ezilmez
            # (elle 'Otomatik' = davetten secilsin: bekleyen secim duser, oneri gosterilir)
            if self._tur_elle[1] == llm.SABLON_OTOMATIK:
                self._tur_elle = None
            else:
                self._tur_elle = (anahtar, self._tur_elle[1])
                self.log(f"Tür: başlık yokken seçilen '{llm.sablon_etiketi(self._tur_elle[1])}' '{baslik}' "
                         "toplantısına uygulandı")
        if self._tur_elle and self._tur_elle[0] == anahtar:
            sablon = self._tur_elle[1]
            neden = ("Başlat'ta davetten otomatik seçilir" if sablon == llm.SABLON_OTOMATIK
                     else "Başlat'ta elle seçildi")
            self._tur_uygula(sablon, neden, "elle")
            return
        sablon, neden, kaynak = self._tur_oner_ayrintili(baslik, ctx)
        self._tur_uygula(sablon, neden, kaynak)

    def _tur_secici_uygula(self, durum=None):
        """Kutu yalniz not uretilirken (ya da yeniden ozetleme Baslat'i da kapatiyorken) kapali; yakalamada ACIK."""
        d = durum or self._durum()
        acik = not self._kapaniyor and d != "not_uretiliyor" and not (d == "yeniden_ozetleniyor"
                                                                      and not self._izinler(d)["baslat"])
        if self.tur_secici.isEnabled() != acik:
            self.tur_secici.setEnabled(acik)

    def _tur_secildi(self, i):
        """Kullanici turu elle degistirdi. Yakalama oncesi: bu baslik icin hatirlanir, Baslat'ta motora gider. Yakalama
        sirasinda: motor.sablon_degistir (sonraki parcalar yeni sablonla; onceki parcalar not uretilirken yeniden ozetlenir;
        not burada URETILMEZ)."""
        sablon = self.tur_secici.itemData(i)
        baslik = self.baslik.text().strip()
        if self._yakalama_acik or self._yakalama_suruyor():
            if sablon == llm.SABLON_OTOMATIK:        # toplanti baslamis: 'otomatik' anlamsiz, gercek sablona don
                self.log("Tür: toplantı sürerken Otomatik seçilemez; bir tür seç")
                self._sablon_goster()
                return
            self._tur_elle = (self._tur_anahtar(baslik), sablon)
            if self.motor is None or not getattr(self.motor, "sablon", None):
                self._tur_bekleyen = sablon          # motor henuz kurulmadi: baglaninca uygulanir
                self._tur_uygula(sablon, "Başlat'ta elle seçildi", "elle")
                return
            if not self.motor.sablon_degistir(sablon):
                self._sablon_goster()
                return
            self.log(f"Not şablonu: {llm.sablon_etiketi(sablon)} (toplantı sırasında elle seçildi)")
            self._sablon_goster()
            return
        self._tur_elle = (self._tur_anahtar(baslik), sablon)
        if sablon == llm.SABLON_OTOMATIK:
            self._tur_uygula(sablon, "Başlat'ta davetten otomatik seçilir", "elle")
        else:
            self._tur_uygula(sablon, "Başlat'ta elle seçildi", "elle")

    def _tur_yakalama_kwargs(self):
        """YakalamaIsi'ne giden sablon kwargs'i (paylasilan ayar'a yazilmaz). Yalniz elle secilmis ya da seri hafizasindan
        gelen kesin tur gider; digerinde bos (bugunku yol: Motor ayardan / davetten secer)."""
        sablon = self.tur_secici.currentData()
        if sablon not in llm.SABLONLAR:
            return {}
        if self._tur_kaynak == "elle":
            return {"sablon": sablon, "sablon_neden": "Başlat'ta elle seçildi"}
        if self._tur_kaynak == "seri":
            return {"sablon": sablon, "sablon_neden": self._tur_neden}      # kutudaki ile motorun kullandigi ayni olsun
        return {}

    def _sablon_secilebilir(self, durum=None):
        """Not sayfasi secicisi yalniz 'bos' durumda (calisan yakalama / not uretimi / yeniden ozetleme ortasinda sablon
        degisirse ayni notta iki sablon karisir) ve konusma verisi silinmemis toplantida acik."""
        m = self.motor
        if not m or not getattr(m, "sablon", None) or (durum or self._durum()) != "bos":
            return False
        silindi = getattr(m, "transkript_silindi", None)
        return not (callable(silindi) and silindi())

    def _sablon_secici_uygula(self, durum=None):
        acik = self._sablon_secilebilir(durum)
        if self.not_sablon.isEnabled() != acik:
            self.not_sablon.setEnabled(acik)

    def _sablon_secildi(self, i):
        """Kullanici sablonu elle degistirdi (istege bagli): meta.json'a yazilir; not hemen yeni sablonla uretilir (baska
        sablonla ozetlenmis parcalar yeniden ozetlenir). Yalniz 'bos' durumda: bir is suruyorsa secim geri alinir."""
        if not self.motor:
            return
        sablon = self.not_sablon.itemData(i)
        if not self._sablon_secilebilir():
            d = self._durum()
            self.log("Not şablonu değiştirilmedi: " + (f"başka bir iş sürüyor ({d}); bitince tekrar dene" if d != "bos"
                                                       else "konuşma verisi saklama süresiyle silinmiş"))
            self._sablon_goster()                   # secici toplantinin gercek sablonuna doner
            return
        if not self.motor.sablon_degistir(sablon):
            self._sablon_goster()
            return
        self.log(f"Not şablonu: {llm.sablon_etiketi(sablon)} (elle seçildi)")
        self._sablon_goster()
        if self.not_md and self._durum() == "bos":
            self._not_uret()

    def _gecmis(self):
        w, v = self._sayfa()
        ha = QtWidgets.QHBoxLayout()
        self.g_ara = QtWidgets.QLineEdit()
        self.g_ara.setPlaceholderText("Tüm toplantılarda ara (transkript ve notlar; örn. test ortamı)")
        self.g_ara.returnPressed.connect(self.gecmiste_ara)
        b_ara = QtWidgets.QPushButton("Ara")
        b_ara.clicked.connect(self.gecmiste_ara)
        ha.addWidget(self.g_ara, 1)
        ha.addWidget(b_ara)
        v.addLayout(ha)
        self._arama_sonuclari = []
        h = QtWidgets.QHBoxLayout()
        self.g_liste = QtWidgets.QListWidget(objectName="liste")
        self.g_liste.currentItemChanged.connect(self._gecmis_secildi)
        h.addWidget(self.g_liste, 2)
        sag = QtWidgets.QVBoxLayout()
        self.g_bilgi = QtWidgets.QLabel("", objectName="alt_bilgi")
        self.g_bilgi.setWordWrap(True)
        sag.addWidget(self.g_bilgi)
        sag.addWidget(QtWidgets.QLabel("Parçalar"))
        self.g_parcalar = QtWidgets.QListWidget(objectName="liste")
        self.g_parcalar.setMaximumHeight(150)
        self.g_parcalar.currentItemChanged.connect(self._parca_secildi)
        sag.addWidget(self.g_parcalar)
        self.g_detay = QtWidgets.QTextBrowser()
        self.g_detay.document().setDocumentMargin(16)
        self.g_detay.setOpenLinks(False)
        self.g_detay.anchorClicked.connect(lambda u: self._baglanti(u, self._secili_klasor()))
        sag.addWidget(self.g_detay, 1)
        h.addLayout(sag, 3)
        v.addLayout(h, 1)
        hb = QtWidgets.QHBoxLayout()
        b1 = QtWidgets.QPushButton("Yenile")
        b1.clicked.connect(self.gecmis_yenile)
        b2 = self.b_g_not = QtWidgets.QPushButton("Notu 'Not' sayfasında aç")
        b2.clicked.connect(self.gecmis_not)
        b3 = self.b_g_parca = QtWidgets.QPushButton("Seçili parçayı yeniden özetle")
        b3.clicked.connect(self.parca_yeniden)
        b4 = self.b_g_yeniden = QtWidgets.QPushButton("Yeniden özetle", objectName="birincil")
        b4.setToolTip("Tüm parçaları yeniden özetler ve yeni not üretir (yarım kalan kayıtlar için de). "
                      "Var olan not not.md.yedek-… olarak saklanır.")
        b4.clicked.connect(self.gecmis_yeniden_ozetle)
        b5 = self.b_g_sil = QtWidgets.QPushButton("Sil", objectName="tehlike")
        b5.clicked.connect(self.gecmis_sil)
        b6 = self.b_g_bos = QtWidgets.QPushButton("Boş kayıtları temizle")
        b6.clicked.connect(self.gecmis_bos_temizle)
        for b in (b1, b2, b4, b3):
            hb.addWidget(b)
        hb.addStretch(1)
        hb.addWidget(b6)
        hb.addWidget(b5)
        v.addLayout(hb)
        self.gecmis_yenile()
        return w

    def _ayarlar(self):
        w, v = self._sayfa()
        k = QtWidgets.QFrame(objectName="kart")
        f = QtWidgets.QFormLayout(k)
        f.setContentsMargins(18, 14, 18, 14)
        self.a_route = QtWidgets.QLineEdit(self.ayar["route"])
        self.a_model = QtWidgets.QLineEdit(self.ayar["model"])
        self.a_model.setPlaceholderText("boş: sunucudaki tek model kullanılır")
        self.a_context = QtWidgets.QSpinBox()
        self.a_context.setRange(0, 1048576)
        self.a_context.setSingleStep(1024)
        self.a_context.setSuffix(" token")
        self.a_context.setSpecialValueText("sunucudan otomatik")
        self.a_context.setValue(max(0, llm._int(self.ayar.get("context"))))
        self.a_context.setToolTip("0: sunucunun max_model_len değeri kullanılır. Bir değer yazılırsa yalnızca üst "
                                  "sınırdır (sunucununkinden büyükse sunucunun değeri geçerli olur).")
        self.a_otobitir = QtWidgets.QSpinBox()
        self.a_otobitir.setRange(30, 1800)
        self.a_otobitir.setValue(int(self.ayar["otobitir"]))
        self.a_duzelt = QtWidgets.QCheckBox("Parçaları özetlemeden önce arka planda altyazı düzeltmesi yap")
        self.a_duzelt.setChecked(bool(self.ayar["duzelt"]))
        self.a_outlook = QtWidgets.QCheckBox("Not hazır olunca Outlook taslağını otomatik aç")
        self.a_outlook.setChecked(bool(self.ayar["outlook"]))
        self.a_oto = QtWidgets.QCheckBox("Uygulama açılınca beklemede başla (altyazı görünce yakala)")
        self.a_oto.setChecked(bool(self.ayar["otomatik_basla"]))
        self.a_stt_ipucu = QtWidgets.QCheckBox("Whisper'a katılımcı adlarını ve serinin terimlerini ipucu olarak ver")
        self.a_stt_ipucu.setToolTip("Terimler davetten ve önceki notlardan kendiliğinden birikir (terimler/terimler.json; "
                                    "elle eklemek için terimler/elle.txt). Sessiz kliplere ipucu gönderilmez.")
        self.a_stt_ipucu.setChecked(bool(self.ayar.get("stt_ipucu", True)))
        self.a_kaynak = QtWidgets.QComboBox()
        # metin hep sesten (Whisper), konusmaci Teams altyazisinin ZAMANINDAN; altyazi metni transkripte girmez
        self.a_kaynak.addItem("Ses (Whisper) — metin sesten, konuşmacı altyazı zamanından", "ses")
        self.a_kaynak.setCurrentIndex(0)
        self.a_kaynak.setEnabled(False)
        self.a_altyazi_yedek = QtWidgets.QCheckBox("STT yokken Teams altyazı metnini yedek olarak nota al "
                                                   "(düşük güven, şüpheli işaretli)")
        self.a_altyazi_yedek.setToolTip("Kapalıyken (önerilen) STT kesintisinde transkriptte boşluk kalır ve nota "
                                        "'STT yok — transkript eksik' yazılır. Açıkken o aralıkta altyazı satırları "
                                        "'altyazıdan, düşük güven' olarak girer; sahip/aksiyon üretmez.")
        self.a_altyazi_yedek.setChecked(bool(self.ayar.get("altyazi_yedek", False)))
        self.a_altyazi_goster = QtWidgets.QCheckBox("Teams altyazısını canlı ekranda da göster (transkripte girmez)")
        self.a_altyazi_goster.setChecked(bool(self.ayar.get("altyazi_goster", False)))
        self.a_stt = QtWidgets.QLineEdit(self.ayar.get("stt_url", ""))
        self.a_mik = QtWidgets.QComboBox()
        self.a_mik.addItem("Otomatik (kulaklık/hoparlör her başlatmada algılanır)", "otomatik")
        self.a_mik.setEnabled(False)
        # Outlook/Windows tam adi arka planda alinir (COM arayuz thread'inde calismaz): _kullanici_adi_geldi
        self.a_ben = QtWidgets.QLineEdit(self.ayar.get("ben", ""))
        self.a_ben.setPlaceholderText("uygulamayı açan kişi (mikrofondan gelen cümlelerin sahibi)")
        self.a_ben.setToolTip("Oturum açan kullanıcıdan otomatik alındı; istersen düzelt")
        self.a_altyazi_oto = QtWidgets.QCheckBox("Altyazı görünmezse otomatik aç (arka planda menü; olmazsa Alt+Shift+C kısayolu, "
                                                 "~0,2 sn odak geçişi)")
        self.a_altyazi_oto.setChecked(bool(self.ayar.get("altyazi_otomatik", True)))
        self.a_altyazi_tr = QtWidgets.QCheckBox("Altyazı bulununca konuşulan dili Türkçe yapmayı dene (Teams seçimi hatırlar)")
        self.a_altyazi_tr.setChecked(bool(self.ayar.get("altyazi_turkce", True)))
        self.a_olcum = QtWidgets.QCheckBox("Ölçüm kaydı (PoC): klipleri ve altyazı okumalarını yerel klasöre kaydet")
        self.a_olcum.setChecked(bool(self.ayar.get("olcum_kaydi", False)))
        self.a_sablon = QtWidgets.QComboBox()
        self.a_sablon.addItem("Otomatik (toplantı davetinden)", llm.SABLON_OTOMATIK)
        for anahtar, (etiket, _) in llm.SABLONLAR.items():
            self.a_sablon.addItem(f"Her zaman: {etiket}", anahtar)
        self.a_sablon.setCurrentIndex(max(0, self.a_sablon.findData(self.ayar.get("not_sablonu", llm.SABLON_OTOMATIK))))
        self.a_sablon.setToolTip("Otomatik: davet konusu / metnindeki ipuçlarından (haftalık, karar, 1:1, çalıştay ...) "
                                 "seçilir ve Olaylar'a nedeniyle yazılır. Sabit seçim her toplantıda o şablonu kullanır.")
        self.a_birebir_taslak = QtWidgets.QCheckBox("Birebir toplantıda da taslak (kişiye özel e-posta, Jira CSV)")
        self.a_birebir_taslak.setChecked(bool(self.ayar.get("birebir_taslak", False)))
        self.a_saklama = QtWidgets.QSpinBox()
        self.a_saklama.setRange(0, 3650)
        self.a_saklama.setSuffix(" gün")
        self.a_saklama.setSpecialValueText("süresiz (kapalı)")
        self.a_saklama.setValue(int(self.ayar.get("saklama_gun", 0) or 0))
        self.a_saklama.setToolTip("Bu süreden eski toplantılarda konuşma içeren her veri silinir: transkript, ses klipleri, "
                                  "ölçüm kaydı, arama dizini, yedekler (notu üretilmemiş toplantılar da, tarihe göre). "
                                  "not.md ve parça özetleri kalır. Açılışta, her saat ve ayar kaydedilince uygulanır.")
        b_test = self.b_stt_test = QtWidgets.QPushButton("STT servisini test et")
        b_test.clicked.connect(self.stt_test)
        f.addRow("Kaynak", self.a_kaynak)
        self.a_stt_model = QtWidgets.QLineEdit(self.ayar.get("stt_model", "whisper-large-v3-turbo-prod"))
        self.a_stt_key = QtWidgets.QLineEdit(self.ayar.get("stt_key", "EMPTY"))
        f.addRow("STT adresi", self.a_stt)
        f.addRow("STT modeli", self.a_stt_model)
        f.addRow("STT anahtarı", self.a_stt_key)
        f.addRow("Mikrofon", self.a_mik)
        f.addRow("Ben", self.a_ben)
        f.addRow(self.a_altyazi_oto)
        f.addRow(self.a_altyazi_tr)
        f.addRow(self.a_altyazi_goster)
        f.addRow(self.a_altyazi_yedek)
        f.addRow(self.a_olcum)
        f.addRow(b_test)
        f.addRow(QtWidgets.QLabel("— LLM —", objectName="alt_bilgi"))
        f.addRow("LLM adresi", self.a_route)
        f.addRow("Model", self.a_model)
        f.addRow("Bağlam penceresi", self.a_context)
        b_llm_test = self.b_llm_test = QtWidgets.QPushButton("LLM sunucusunu test et")
        b_llm_test.clicked.connect(lambda: self.llm_tani(test=True))
        f.addRow(b_llm_test)
        f.addRow("Altyazı kaybolunca otomatik bitiş (sn)", self.a_otobitir)
        f.addRow(self.a_duzelt)
        f.addRow(self.a_outlook)
        f.addRow(self.a_oto)
        f.addRow(QtWidgets.QLabel("— Not —", objectName="alt_bilgi"))
        f.addRow(self.a_stt_ipucu)
        f.addRow("Not şablonu", self.a_sablon)
        f.addRow(self.a_birebir_taslak)
        f.addRow("Transkript saklama süresi", self.a_saklama)
        b = self.b_kaydet = QtWidgets.QPushButton("Kaydet", objectName="birincil")
        b.clicked.connect(self.ayar_kaydet)
        f.addRow(b)
        v.addWidget(k)
        v.addStretch(1)
        return w

    def _tepsi(self):
        self.tepsi = None
        ikon = logo_ikonu() or self.style().standardIcon(QtWidgets.QStyle.SP_FileDialogDetailedView)
        self.setWindowIcon(ikon)
        if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tepsi = QtWidgets.QSystemTrayIcon(ikon, self)
        menu = QtWidgets.QMenu()
        menu.addAction("Göster", self.showNormal)
        menu.addAction("Başlat / Bitir", self.ana_dugme)
        menu.addSeparator()
        menu.addAction("Çıkış", self.close)              # closeEvent: iscileri durdurur, veriyi diske yazar, sonra cikar
        self.tepsi.setContextMenu(menu)
        self.tepsi.activated.connect(lambda r: self.showNormal() if r == QtWidgets.QSystemTrayIcon.Trigger else None)
        self.tepsi.show()

    # ================================================================== durum
    def _durum_ayarla(self, metin, renk="#6b7280"):
        kisa = metin.split(" — ")[0].rstrip("…")
        self.nokta.setStyleSheet(f"color:{renk}")
        self.nokta_yazi.setText(kisa)
        self.k_durum.deger.setText(kisa)
        self.durum.setText(metin)

    def _ana_stil(self, metin, ad):
        if self.b_ana.text() == metin and self.b_ana.objectName() == ad:
            return
        self.b_ana.setText(metin)
        self.b_ana.setObjectName(ad)
        self.b_ana.style().unpolish(self.b_ana)
        self.b_ana.style().polish(self.b_ana)
        self.b_ana.update()

    # ---------------------------------------------------------------- mesgul durum makinesi
    def _yakalama_suruyor(self):
        return bool(self.is_ and self.is_.isRunning())

    def _durum(self):
        """Tek mesgul durum: oncelik yakalama > not > yeniden ozetleme > soru. Kilitler ilgili thread'in finished'inde
        acilir; 'yakalaniyor' toplanti_bitti (thread gercekten bittikten sonra) islenene kadar surer."""
        if self._yakalama_acik or self._baslat_bekliyor or self._yakalama_suruyor():
            return "yakalaniyor"
        if self._not_kilit:
            return "not_uretiliyor"
        if self._yeniden_kilit or self._parca_kilit or _calisiyor(self.kesinti_isi):
            return "yeniden_ozetleniyor"
        if self._soru_kilit:
            return "soru"
        return "bos"

    def _izinler(self, durum=None):
        """Durumun eylem tablosu (IZINLER). Ek kurallar: kapanirken hicbiri acik degil; Bitir basildiysa ya da Baslat
        bir sorgunun bitmesini bekliyorsa tekrar Bitir yok; yalniz onceki oturumun kesinti kuyrugu suruyorsa Baslat acik
        (Baslat kuyrugu duraklatir, sonra baslar)."""
        durum = durum or self._durum()
        iz = dict(IZINLER[durum])
        if self._kapaniyor:
            return dict.fromkeys(iz, False)
        if durum == "yakalaniyor" and (self._bitiriliyor or self._baslat_bekliyor):
            iz["bitir"] = False
        if durum == "yeniden_ozetleniyor" and not self._yeniden_kilit and not self._parca_kilit \
                and _calisiyor(self.kesinti_isi):
            iz["baslat"] = True
        return iz

    def _durum_uygula(self):
        """Durumdan dugmeleri turet (her gecisten ve her saniye cagrilir; degismeyen ayar yan etkisiz)."""
        d = self._durum()
        self._durum_adi = d
        iz = self._izinler(d)
        if d == "yakalaniyor":
            self._ana_stil("Bitir", "tehlike")
            self.b_ana.setEnabled(iz["bitir"])
        else:
            self._ana_stil("Başlat", "birincil")
            self.b_ana.setEnabled(iz["baslat"])
        self.b_g_yeniden.setEnabled(iz["yeniden"])
        self.b_g_parca.setEnabled(iz["yeniden"])
        self.b_g_not.setEnabled(iz["yeniden"])
        silinebilir = d in ("bos", "yakalaniyor", "soru") and not self._kapaniyor     # etkin klasor ayrica korunur
        self.b_g_sil.setEnabled(silinebilir)
        self.b_g_bos.setEnabled(silinebilir)
        self.b_not_uret.setEnabled(iz["uygula"] and self._not_uret_aday and self.motor is not None)
        self._sablon_secici_uygula(d)
        self._tur_secici_uygula(d)
        self.b_sor.setEnabled(iz["sor"])
        self.soru.setEnabled(iz["sor"])
        self.b_kaydet.setEnabled(iz["kaydet"])
        return d

    def _sayac_guncelle(self):
        if self.baslangic and self.bitis is None and self.is_ and self.is_.isRunning():
            s = int(saat.simdi() - self._baslangic_t)
            self.sayac.setText(f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}")
        if self._serit_bas is not None:
            s = int(saat.simdi() - self._serit_bas)
            self.serit_sure.setText(f"{s} sn" if s < 60 else f"{s // 60} dk {s % 60:02d} sn")
        if self.motor:
            biten = sum(1 for i in self.motor.isler if i.done())
            self.k_parca.deger.setText(f"{biten} / {self.motor.parca_no}")
            self.k_katilimci.deger.setText(str(len(self.motor.katilimcilar)))
        self._durum_uygula()

    def _renk(self, ad):
        if ad not in self.renkler:
            self.renkler[ad] = RENKLER[len(self.renkler) % len(RENKLER)]
        return self.renkler[ad]

    def _serit_baslat(self, metin):
        self._serit_bas = saat.simdi()
        self.serit_metin.setText(metin + "…")
        self.serit_sure.setText("0 sn")
        self.not_serit.show()

    def _serit_adim(self, metin):
        """Motorun 'adim' olayi: seride o anki asama yazilir (serit kapaliysa acilir)."""
        if self._serit_bas is None:
            self._serit_baslat(metin)
        else:
            self.serit_metin.setText(metin + "…")
        self.durum.setText(metin + "…")

    def _serit_bitir(self):
        self._serit_bas = None
        self.not_serit.hide()

    def _canli_sifirla(self):
        self._canli_ozetler = {}
        self._canli_sayac_guncelle()

    def _canli_sayac_guncelle(self):
        """N10: su ana kadar ozetlenen parcalardan karar/aksiyon/acik soru sayisi (LLM'siz tekillestirme)."""
        t = llm.listeleri_tekille([self._canli_ozetler[k] for k in sorted(self._canli_ozetler)])
        self.canli_sayac.setText(f"Şu ana kadar: {len(t['kararlar'])} karar, {len(t['aksiyonlar'])} aksiyon, "
                                 f"{len(t['acik_sorular'])} açık soru")

    def _tls_bildir(self):
        """A8: acilista bir kez: TLS dogrulamasi kapaliysa Olaylar'a yaz (README: ca_bundle adimlari)."""
        if llm.TLS_HATASI:
            self.log(f"TLS doğrulaması kapalı (ca_bundle sertifikası okunamadı: {llm.TLS_HATASI})")
        elif not ayar_mod.tls_dogrulama(self.ayar):
            if (self.ayar.get("ca_bundle") or "").strip():
                self.log("TLS doğrulaması kapalı (config.json'daki ca_bundle dosyası bulunamadı)")
            else:
                self.log("TLS doğrulaması kapalı (config.json'da ca_bundle ayarlı değil)")

    def log(self, m):
        self.gunluk.appendPlainText(f"{dt.datetime.now():%H:%M:%S}  {m}")

    # ================================================================== akis
    def ana_dugme(self):
        if self._durum() == "yakalaniyor" and not self._baslat_bekliyor:
            self.bitir()
        else:
            self.baslat()

    # ---------------------------------------------------------------- arka plan isleri (COM / UIA / ag)
    def _arka(self, fn, *args, bitti=None, hata=None, uia=False, ad="arka-is"):
        """fn(*args)'i arayuzu dondurmadan calistirir (isler.ArkaIs). bitti(sonuc) / hata(mesaj) ana thread'de cagrilir;
        hata verilmezse Olaylar'a yazilir. Is referansi finished'e kadar tutulur."""
        isi = ArkaIs(fn, *args, uia=uia, ad=ad)
        if bitti:
            isi.bitti.connect(bitti)
        isi.hata.connect(hata or (lambda m, ad=ad: self.log(f"✖ {ad}: {m}")))
        self._arka_isler.append(isi)

        def bitti_temizle(isi=isi):
            if isi in self._arka_isler:
                self._arka_isler.remove(isi)
        isi.finished.connect(bitti_temizle)
        isi.start()
        return isi

    def _kullanici_adi_geldi(self, ad):
        self._kullanici_adi = (ad or "").strip()
        if self._kullanici_adi and not self.a_ben.text().strip():
            self.a_ben.setText(self._kullanici_adi)

    def _takvim_getir(self, devam, bekle=False):
        """Takvim + Teams penceresi sorgusunu arka planda baslatir; sonuc `devam(sonuc)` ile gelir. Sorgu zaten suruyorsa:
        bekle=True devami siraya koyar, False (zamanlayici) birakir. -> sorgu baslatildi/siraya alindi mi."""
        if _calisiyor(self._takvim_isi):
            if bekle:
                self._takvim_devamlari.append(devam)
                return True
            return False
        self._takvim_devamlari = [devam]
        self._takvim_isi = ArkaIs(takvim_sorgula, uia=True, ad="takvim")
        self._takvim_isi.bitti.connect(self._takvim_bitti)
        self._takvim_isi.hata.connect(self._takvim_hata)
        self._takvim_isi.start()
        return True

    def _takvim_cagir(self, sonuc):
        devamlar, self._takvim_devamlari = self._takvim_devamlari, []
        for fn in devamlar:
            try:
                fn(sonuc)
            except Exception:
                import traceback
                hata_logla(traceback.format_exc())
                self.log("✖ takvim sonucu işlenemedi (hata.log)")

    def _takvim_bitti(self, sonuc):
        self._takvim_cagir(sonuc if isinstance(sonuc, dict) else {})

    def _takvim_hata(self, mesaj):
        self.log(f"! takvim okunamadı: {mesaj}")
        self._takvim_cagir({"teams_adi": "", "secilen": None, "adaylar": [], "skor": 0.0})

    def toplantilari_yenile(self, elle=False):
        """Bu saatteki takvim toplantilarini acilir listeye doldurur (arka planda: Outlook COM + Teams UIA); Teams
        penceresindeki adla eslesen varsa onu secer. Yakalama surerken listeye dokunulmaz."""
        if self._yakalama_suruyor() or self._kapaniyor:
            return
        self._takvim_getir(lambda sonuc: self._takvim_uygula(sonuc, elle))

    def _takvim_uygula(self, sonuc, elle=False):
        if self._yakalama_suruyor() or self._baslat_bekliyor or self._kapaniyor:
            return                                       # sonuc gelene kadar yakalama basladiysa liste degismez
        teams_adi = sonuc.get("teams_adi") or ""
        secilen, adaylar = sonuc.get("secilen"), sonuc.get("adaylar") or []
        yazili = self.toplanti_secici.currentText().strip()
        self.toplanti_secici.blockSignals(True)
        self.toplanti_secici.clear()
        for a in adaylar:
            self.toplanti_secici.addItem(f"{a['baslangic']:%H:%M}–{a['bitis']:%H:%M}  {a['baslik']}", a)
        self.toplanti_secici.blockSignals(False)
        self._adaylar = adaylar
        onceki = next((k for k, a in enumerate(adaylar) if a["baslik"] == yazili), -1)
        if onceki >= 0 and not elle:                    # kullanicinin secimi listede hala var: koru
            self.toplanti_secici.setCurrentIndex(onceki)
            self._toplanti_secildi(onceki)
        elif secilen:
            i = next((k for k, a in enumerate(adaylar) if a is secilen), 0)
            self.toplanti_secici.setCurrentIndex(i)
            self._toplanti_secildi(i)
        elif adaylar and (elle or not yazili):
            self.toplanti_secici.setCurrentIndex(0)
            self._toplanti_secildi(0)
        elif teams_adi and not yazili:
            self.toplanti_secici.setEditText(teams_adi)
            self.ctx = {}
            self.alt_bilgi.setText("Takvimde eşleşen kayıt yok; ad Teams penceresinden alındı.")
        elif yazili:
            self.toplanti_secici.setEditText(yazili)
        if elle:
            self.log(f"takvim yenilendi: {len(adaylar)} toplantı" + (f", Teams: '{teams_adi}'" if teams_adi else ""))

    def _toplanti_secildi(self, i):
        if i < 0 or i >= len(getattr(self, "_adaylar", [])):
            return
        a = self._adaylar[i]
        self.ctx = a
        self.toplanti_secici.setEditText(a["baslik"])
        self.alt_bilgi.setText(f"{a['baslangic']:%H:%M}–{a['bitis']:%H:%M} · "
                               + ", ".join(a["katilimcilar"][:6]) + (" …" if len(a["katilimcilar"]) > 6 else ""))
        if not (self._yakalama_acik or self._yakalama_suruyor() or self._baslat_bekliyor):
            self._tur_yenile(a["baslik"], a)         # tur davetten (ya da seri hafizasindan) dolar; elle secilen ezilmez

    def _toplanti_bilgisi(self, sonuc):
        """O an KATILINAN toplantiyi belirler: Teams penceresindeki ad + takvimden eslesen kayit (sonuc: takvim_sorgula
        ciktisi, arka planda alinmistir). Emin olunamazsa kullaniciya adaylari sorar. Dondurur (baslik, ctx)."""
        teams_adi = sonuc.get("teams_adi") or ""
        secilen, adaylar, skor = sonuc.get("secilen"), sonuc.get("adaylar") or [], sonuc.get("skor") or 0.0
        if secilen:
            self.log(f"Toplantı: '{secilen['baslik']}' (Teams: '{teams_adi}', eşleşme {skor:.2f})")
            return secilen["baslik"], secilen
        if adaylar:
            secilen = self._toplanti_sor(adaylar, teams_adi)
            if secilen:
                return secilen["baslik"], secilen
        elle = self.baslik.text().strip() or teams_adi
        if elle:
            self.log(f"Takvimde eşleşme yok; başlık: '{elle}'")
            return elle, {}
        ad, tamam = QtWidgets.QInputDialog.getText(self, "Toplantı adı", "Toplantı adı:")
        return (ad.strip() or "Toplantı") if tamam else "Toplantı", {}

    def _toplanti_sor(self, adaylar, teams_adi):
        """Ayni saatte birden fazla toplanti varsa hangisine katildigini sorar."""
        d = QtWidgets.QDialog(self)
        d.setWindowTitle("Hangi toplantıdasın?")
        d.setMinimumWidth(520)
        v = QtWidgets.QVBoxLayout(d)
        bilgi = (f"Teams penceresi: <b>{teams_adi}</b><br>Bu saatte takvimde {len(adaylar)} toplantı var. "
                 f"Katıldığını seç:") if teams_adi else f"Bu saatte takvimde {len(adaylar)} toplantı var. Katıldığını seç:"
        et = QtWidgets.QLabel(bilgi)
        et.setWordWrap(True)
        v.addWidget(et)
        liste = QtWidgets.QListWidget(objectName="liste")
        for a in adaylar:
            liste.addItem(f"{a['baslangic']:%H:%M}–{a['bitis']:%H:%M}  {a['baslik']}   "
                          f"({len(a['katilimcilar'])} katılımcı)")
        liste.setCurrentRow(0)
        liste.itemDoubleClicked.connect(d.accept)
        v.addWidget(liste)
        yok = QtWidgets.QCheckBox("Hiçbiri — takvimsiz devam et")
        v.addWidget(yok)
        dugmeler = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        dugmeler.accepted.connect(d.accept)
        dugmeler.rejected.connect(d.reject)
        v.addWidget(dugmeler)
        if d.exec_() != QtWidgets.QDialog.Accepted or yok.isChecked():
            return None
        return adaylar[liste.currentRow()]

    def baslat(self):
        """Baslat: yalniz 'bos'/'soru' durumunda (ya da yalniz kesinti kuyrugu suruyorsa: kuyruk duraklatilip baslanir).
        Takvim/Teams sorgusu arka planda; sonuc gelince _baslat_devam toplantiyi baslatir."""
        d = self._durum()
        if d == "yakalaniyor":
            return
        if d == "not_uretiliyor":
            self.log("Önceki toplantının notu üretiliyor; bitince yeni toplantı başlatılabilir.")
            return
        if d == "yeniden_ozetleniyor" and not self._izinler(d)["baslat"]:
            self.log("Yeniden özetleme sürüyor; bitince yeni toplantı başlatılabilir.")
            return
        if self._kapaniyor:
            return
        if _calisiyor(self.kesinti_isi):             # onceki oturumun STT kuyrugu: duraklat, thread bitince baslat
            self.log("Önceki oturumun ses kuyruğu duraklatılıyor; kalan klipler sonraki açılışta çözülecek.")
            self._baslat_bekliyor = True
            self.kesinti_isi.durdur()
            self.kesinti_isi.finished.connect(self._baslat_kesinti_sonrasi)
            self._durum_uygula()
            return
        yazili = self.toplanti_secici.currentText().strip()
        if yazili:
            self._baslat_devam({})                   # secili/yazili baslik: takvim sorgusu gerekmez
            return
        self._baslat_bekliyor = self._baslat_takvim = True    # takvim + Teams penceresi (COM/UIA) arka planda
        self.durum.setText("Toplantı adı takvimden aranıyor…")
        self._durum_uygula()
        self._takvim_getir(self._baslat_takvimle, bekle=True)
        self._tek_atim(self.TAKVIM_ZAMAN_ASIMI_MS, self._baslat_takvim_zaman_asimi)

    def _baslat_kesinti_sonrasi(self):
        if self._baslat_bekliyor and not self._kapaniyor:
            self._baslat_bekliyor = False
            self.baslat()
        else:
            self._baslat_bekliyor = False
            self._durum_uygula()

    def _baslat_takvim_zaman_asimi(self):
        """Takvim/UIA sorgusu takildiysa Baslat sonsuza dek beklemez: bos sonuc (ad sorulur) ile devam."""
        if self._baslat_bekliyor and self._baslat_takvim:
            self.log("! takvim sorgusu zamanında bitmedi; toplantı adı elle sorulacak")
            self._baslat_takvimle({})

    def _baslat_takvimle(self, sonuc):
        """Takvim sorgusu bitti (ana thread). Liste doldurulur; secim/yazili ad yoksa Teams adi + takvimle belirlenir."""
        if not (self._baslat_bekliyor and self._baslat_takvim):
            return
        self._baslat_bekliyor = self._baslat_takvim = False
        if self._kapaniyor:
            self._durum_uygula()
            return
        self._takvim_uygula(sonuc)
        self._baslat_devam(sonuc)

    def _baslat_devam(self, sonuc):
        yazili = self.toplanti_secici.currentText().strip()
        i = self.toplanti_secici.currentIndex()
        adaylar = self._adaylar
        if 0 <= i < len(adaylar) and yazili == adaylar[i]["baslik"]:
            baslik, ctx = adaylar[i]["baslik"], adaylar[i]
        elif yazili:
            baslik, ctx = yazili, {}
        else:
            baslik, ctx = self._toplanti_bilgisi(sonuc)
        self.baslik.setText(baslik)
        self.ctx = ctx
        if ctx:
            self.alt_bilgi.setText(f"{ctx['baslangic']:%H:%M}–{ctx['bitis']:%H:%M} · "
                                   + ", ".join(ctx["katilimcilar"][:6])
                                   + (" …" if len(ctx["katilimcilar"]) > 6 else ""))
        else:
            self.alt_bilgi.setText("Takvim bilgisi yok; katılımcılar altyazıdan öğrenilecek.")
        self._tur_yenile(baslik, ctx)        # tur Baslat'ta kesinlesir (elle secilen ezilmez)
        self._tur_bekleyen = None
        self.akis.clear()
        self._akis_satirlari = []
        self.stt_serit.hide()
        self.kapsama_serit.hide()
        self.atama_serit.hide()
        self.ses_serit.hide()
        self._ses_uyarilari = {}
        self.mik_etiket.hide()
        self.canli_not.clear()
        self._canli_sifirla()
        self.renkler = {}
        self.not_md = ""
        self._not_uret_aday = False
        self._belirsizler_goster([])
        self.ayar["kaynak"] = "ses"
        self.ayar["stt_url"] = self.a_stt.text().strip()
        self.ayar["stt_model"] = self.a_stt_model.text().strip()
        self.ayar["stt_key"] = self.a_stt_key.text().strip()
        self.ayar["mikrofon_modu"] = "otomatik"
        self.ayar["ben"] = self.a_ben.text().strip() or self._kullanici_adi    # bos: YakalamaIsi kendi thread'inde bulur
        self.ayar["altyazi_otomatik"] = self.a_altyazi_oto.isChecked()
        self.ayar["altyazi_turkce"] = self.a_altyazi_tr.isChecked()
        self.ayar["olcum_kaydi"] = self.a_olcum.isChecked()
        self.ayar["altyazi_yedek"] = self.a_altyazi_yedek.isChecked()
        self.ayar["altyazi_goster"] = self.a_altyazi_goster.isChecked()
        self.ayar["stt_ipucu"] = self.a_stt_ipucu.isChecked()
        self.k_kaynak.deger.setText("Ses (Whisper)")
        self.baslangic = None            # altyazi/ses bulununca baslar
        self.bitis = None
        self.sayac.setText("00:00")
        self.motor = None                # onceki toplantinin motoru yeni yakalamaya karismasin
        self._sablon_goster()            # sablon motor baglaninca (davetten) gelir
        isi = YakalamaIsi(baslik, ctx, self.terimler, self.a_duzelt.isChecked(),
                          self.a_otobitir.value(), self.ayar, **self._tur_yakalama_kwargs())
        self.is_ = isi
        self._yakalama_acik = True
        self._bitiriliyor = False
        self._bekleyen_belirsizler = None
        isi.olay.connect(self.olay)
        isi.durum.connect(lambda m: self._durum_ayarla(m, "#EA580C" if "bekleniyor" in m.lower() or "görünmüyor" in m.lower() else "#14B8A6"))
        isi.toplanti_bitti.connect(self._bitis_sinyali)          # belirsizleri saklar; islem finished'te
        isi.hata.connect(self.yakalama_hatasi)
        isi.finished.connect(lambda isi=isi: self._yakalama_sonlandi(isi))
        isi.start()
        QtCore.QTimer.singleShot(1500, lambda isi=isi: self._motoru_bagla(isi))
        self.nav.setCurrentRow(self.SAYFA_CANLI)
        self._durum_uygula()

    def _motoru_bagla(self, isi):
        if isi is self.is_ and getattr(isi, "motor", None) is not None:
            self.motor = isi.motor
            bekleyen, self._tur_bekleyen = self._tur_bekleyen, None
            if bekleyen and bekleyen in llm.SABLONLAR and self.motor.sablon_degistir(bekleyen):
                self.log(f"Not şablonu: {llm.sablon_etiketi(bekleyen)} (toplantı sırasında elle seçildi)")
            self._sablon_goster()

    def bitir(self):
        if self._yakalama_suruyor() and not self._bitiriliyor:
            self.bitis = dt.datetime.now()
            self._bitiriliyor = True
            self.is_.durdur()
            self._durum_ayarla("Bitiriliyor…", "#EA580C")
            self.ilerleme.show()
            self._durum_uygula()

    def yakalama_hatasi(self, mesaj):
        """YakalamaIsi hata sinyali (ana thread). Motor.bitir + toplanti_bitti ayrica her yolda gelir."""
        self.log(f"✖ {mesaj}")
        self._durum_ayarla("Yakalama hatası", "#DC2626")
        if self._kapaniyor:
            return                                    # kapanirken pencere acilmaz (log yeter)
        QtCore.QTimer.singleShot(0, lambda: QtWidgets.QMessageBox.warning(
            self, "Yakalama hatası", mesaj[-800:] + "\n\nToplantı kaydı diskte kaldı; sürüyorsa not üretilecek."))

    def _satir_html(self, v):
        import html as _h
        ts = _h.escape(str(v.get("ts") or ""))
        metin = _h.escape(str(v.get("text") or ""))
        if v.get("kaynak") == "altyazi":            # canli altyazi gorunumu (altyazi_goster): transkript degil
            return (f'<span style="color:#CBD5E1;font-size:11px">{ts}</span> '
                    f'<span style="color:#94A3B8">{_h.escape(str(v.get("speaker") or "?"))}</span> '
                    f'<span style="color:#94A3B8;font-size:10px">[altyazı]</span>'
                    f'<span style="color:#94A3B8"> {metin}</span>')
        kim = gorunen_ad(v)                          # tek gosterim kurali: 'Ad?', 'Bilinmeyen', 'beklemede…'
        if v.get("durum") == "beklemede" or kim == "Bilinmeyen":
            kim_html = f'<span style="color:#94A3B8">{_h.escape(kim)}</span>'
        else:
            kim_html = f'<b style="color:{self._renk(v.get("speaker") or kim)}">{_h.escape(kim)}</b>'
        ek = ""
        if v.get("kaynak") == "altyazi-yedek":
            ek = ' <span style="color:#94A3B8;font-size:10px">[altyazıdan, düşük güven]</span>'
        elif v.get("supheli_neden") == "cizelge":   # altyazi canliyken etkinlik yok: soluk ama nota 'Bilinmeyen' girer
            ek = (' <span style="color:#94A3B8;font-size:10px" title="Altyazı canlıyken bu aralıkta etkinlik yok; '
                  'nota Bilinmeyen olarak girer">[şüpheli — Bilinmeyen girer]</span>')
            metin = f'<span style="color:#94A3B8" title="Altyazı canlıyken etkinlik yok; nota Bilinmeyen olarak girer">{metin}</span>'
        elif v.get("supheli"):                      # icsel halusinasyon supheli: soluk, nota girmez
            ek = ' <span style="color:#94A3B8;font-size:10px">[şüpheli — nota girmez]</span>'
            metin = f'<span style="color:#94A3B8">{metin}</span>'
        return f'<span style="color:#94A3B8;font-size:11px">{ts}</span> {kim_html}{ek}: {metin}'

    def _akis_ekle(self, v):
        self.akis.append(self._satir_html(v))

    def _akis_ciz(self):
        kaydirici = self.akis.verticalScrollBar()
        sonda = kaydirici.value() >= kaydirici.maximum() - 4
        self.akis.clear()
        for x in self._akis_satirlari:
            self.akis.append(self._satir_html(x))
        if sonda:
            kaydirici.setValue(kaydirici.maximum())

    def _stt_serit_guncelle(self, v):
        """SesServisi.stt_durumu (ya da isler'in 'STT yok' durumu): kesinti suruyorsa ya da cozulemeyen aralik
        varsa serit 'STT yok — transkript eksik (hh:mm–hh:mm)'."""
        if not isinstance(v, dict):
            return
        araliklar = [list(a)[:2] for a in (v.get("eksik") or []) if len(a) >= 2]
        if v.get("durum") == "kesinti":
            araliklar.append([v.get("bas") or saat.simdi(), None])
            self.k_kaynak.deger.setText("STT yok")
        else:
            self.k_kaynak.deger.setText("Ses (Whisper)")
        if araliklar:
            ek = (" — klipler diske yazılıyor, STT dönünce çözülecek" if v.get("kayit") and v.get("durum") == "kesinti"
                  else "")
            self.stt_serit.setText(motor_mod.stt_eksik_metni(araliklar) + ek)
            self.stt_serit.show()
        else:
            self.stt_serit.hide()

    def olay(self, tip, v):
        if tip == "satir":                            # yeni satir: konusmaci 'beklemede…'
            self._akis_satirlari.append(dict(v))
            self._akis_satirlari = self._akis_satirlari[-400:]
            self._akis_ekle(v)
        elif tip == "satir_guncelle":                 # konusmaci karari geldi (satirin tamami)
            for x in self._akis_satirlari:
                if x.get("id") == v.get("id") and x.get("kaynak") != "altyazi":
                    x.update(v)
            self._akis_ciz()
        elif tip == "satir_sil":                      # yanki klibi atildi
            self._akis_satirlari = [x for x in self._akis_satirlari
                                    if not (x.get("id") == v.get("id") and x.get("kaynak") != "altyazi")]
            self._akis_ciz()
        elif tip == "satir_degistir":                 # yeniden kesilen klip: satir parcalariyla degisti
            for i, x in enumerate(self._akis_satirlari):
                if x.get("id") == v.get("eski_id") and x.get("kaynak") != "altyazi":
                    self._akis_satirlari[i:i + 1] = [dict(y) for y in v.get("yeni") or []]
                    break
            self._akis_ciz()
        elif tip == "stt_durum":
            self._stt_serit_guncelle(v)
        elif tip == "mikrofon":                       # otomatik secilen (ya da elle ayarli) mikrofon
            if isinstance(v, dict) and v.get("ad"):
                self.mik_etiket.setText(f"Mikrofon: {v['ad']} ({'otomatik' if v.get('otomatik', True) else 'elle ayarlı'})"
                                        + (" — sessizde" if v.get("sessizde") else ""))
                self.mik_etiket.setToolTip(v.get("neden") or "")
                self.mik_etiket.show()
        elif tip == "ses_uyari":                      # bir kez gelir; ses donunce var=False ile kalkar
            if isinstance(v, dict):
                if v.get("var"):
                    self._ses_uyarilari[v.get("tur")] = v.get("metin") or ""
                else:
                    self._ses_uyarilari.pop(v.get("tur"), None)
                metin = "\n".join(m for m in self._ses_uyarilari.values() if m)
                self.ses_serit.setText(metin)
                self.ses_serit.setVisible(bool(metin))
        elif tip == "kapsama":                        # konusmaci bilgisi 60 sn'den uzun yok / geri geldi
            v = v or {}
            mesaj = v.get("mesaj")                    # neden: panel_donuk / okuma_yok / altyazi kapali
            self.kapsama_serit.setText(f"Konuşmacı bilgisi yok — {mesaj}; konuşmacılar 'Bilinmeyen' yazılıyor."
                                       if mesaj and not v.get("var", True) else self._kapsama_serit_metni)
            self.kapsama_serit.setVisible(not v.get("var", True))
        elif tip == "atama":                          # son 2 dk'da kapsamali kararlarin >= %60'i '?' / duzeldi
            oran = (v or {}).get("oran")
            if not (v or {}).get("var", True) and (v or {}).get("mesaj"):      # olcut: soru | kesin_az | gecikme
                self.atama_serit.setText(str(v["mesaj"]) + ".")
            elif not (v or {}).get("var", True) and isinstance(oran, (int, float)):
                self.atama_serit.setText(f"Konuşmacı atanamıyor — altyazı okunuyor ama konuşmalarla eşleşmiyor "
                                         f"(son 2 dk %{int(round(oran * 100))} Bilinmeyen); panel yeniden aranıyor, "
                                         "gecikme yeniden ölçülüyor.")
            self.atama_serit.setVisible(not (v or {}).get("var", True))
        elif tip == "log":
            self.log(v)
            if "yakalama başladı" in str(v) and self.baslangic is None:
                self.baslangic, self._baslangic_t = dt.datetime.now(), saat.simdi()
        elif tip == "parca_kapandi":
            self.log(f"▶ parça {v['sira']} kapandı ({v['neden']}, ~{v['token']} token, {v['aralik']})")
        elif tip == "adim":
            self._serit_adim(v.get("metin", "") if isinstance(v, dict) else str(v))
        elif tip == "parca_ozetlendi":
            o = v["ozet"]
            if o:
                self._canli_ozetler[v["sira"]] = o
                self._canli_sayac_guncelle()
                self.log(f"✔ parça {v['sira']}: {len(o['kararlar'])} karar, {len(o['aksiyonlar'])} aksiyon, {v['duzeltme']} düzeltme")
                if o.get("ozet"):
                    self.canli_not.addItem(f"Bölüm {v['sira']}: {o['ozet']}")
                for k in o["kararlar"]:              # konu eki: dogrulanmis ozetin kanonik konusu (yoksa ek yok)
                    self.canli_not.addItem(f"Karar: {k}{llm.canli_konu_eki(o, k, 'karar')}")
                for a in o["aksiyonlar"]:
                    self.canli_not.addItem(f"Aksiyon: {a['madde']}{llm.canli_konu_eki(o, a['madde'], 'aksiyon')}"
                                           f"  — {a['sorumlu']}, {a['tarih']}")
                for q in o.get("acik_sorular") or []:
                    self.canli_not.addItem(f"Soru: {q}{llm.canli_konu_eki(o, q, 'soru')}")
                for etiket, metin in not_araclari.sablon_canli_satirlari(o):
                    self.canli_not.addItem(f"{etiket}: {metin}")
                self.canli_not.scrollToBottom()
            else:
                self.log(f"✖ parça {v['sira']} özetlenemedi (bitişte tekrar denenecek)")
        elif tip == "altyazi_iste":
            self._altyazi_iste_popup()
        elif tip == "sure_dondur":
            self.bitis = self.bitis or dt.datetime.now()
        elif tip == "altyazi_izi":                    # canli altyazi: yalniz gorunum, transkripte girmez
            if self.ayar.get("altyazi_goster", False) and not v.get("guncelle"):
                self._akis_satirlari.append({"ts": v["ts"], "speaker": v["speaker"], "text": v["text"],
                                             "kaynak": "altyazi", "id": None})
                self._akis_satirlari = self._akis_satirlari[-400:]
                self._akis_ekle(self._akis_satirlari[-1])
            self.k_katilimci.deger.setText(str(len(self.motor.katilimcilar)) if self.motor else
                                           self.k_katilimci.deger.text())
        elif tip == "ses_parca":
            self.log(f"♪ {v['kaynak']} {v['sn']} sn gönderildi (kuyruk {v['kuyruk']})")
        elif tip == "ses_metin":
            g = f", gecikme {v['gecikme']} sn" if v.get("gecikme") is not None else ""
            self.log(f"✔ {v['kaynak']} {v['sn']} sn → {v['segment']} cümle ({v['islem']} sn{g})")
            if self.baslangic is None:
                self.baslangic, self._baslangic_t = dt.datetime.now(), saat.simdi()
        elif tip == "stt_gecikme":
            if isinstance(v, dict) and not v.get("atlandi"):
                self.log(f"! STT yavaş, {v['kuyruk']} parça sırada bekliyor (atlanmıyor)")
            else:
                self.log(f"! STT yanıt vermiyor, {v['kuyruk'] if isinstance(v, dict) else v} parça bekliyor — bir parça atlandı")

    def _bitis_sinyali(self, belirsizler):
        """YakalamaIsi.toplanti_bitti (thread henuz bitmedi): belirsizler saklanir; toplanti_bitti thread'in finished
        sinyalinde (_yakalama_sonlandi) calisir, boylece Baslat/not ancak thread gercekten bittiginde acilir."""
        self._bekleyen_belirsizler = list(belirsizler or [])

    def _yakalama_sonlandi(self, isi):
        if isi is not self.is_ or not self._yakalama_acik:
            return
        belirsizler, self._bekleyen_belirsizler = self._bekleyen_belirsizler or [], None
        self.toplanti_bitti(belirsizler)

    def toplanti_bitti(self, belirsizler, kaynak="canli"):
        """Toplanti bitti: YakalamaIsi thread'i GERCEKTEN bittikten sonra (finished) cagrilir; Motor.bitir bu yolda her
        zaman cagrilmistir. Motoru baglar, Baslat'i acar ve notu kullanici onayi beklemeden uretir. belirsizler: salt-okunur
        panele (Motor.belirsizler)."""
        if kaynak == "canli":
            self._yakalama_acik = False
            self._bitiriliyor = False
            self.motor = self.is_.motor if self.is_ else self.motor
            if self.motor is None:                    # yakalama motor kurulamadan coktu: sifirla, tekrar denenebilsin
                self.ilerleme.hide()
                self._durum_ayarla("Yakalama başlatılamadı", "#DC2626")
                self._durum_uygula()
                return
        self.ilerleme.hide()
        self._belirsizler_goster(belirsizler)
        if self._kapaniyor:                           # uygulama kapaniyor: veri diskte, not uretimi baslatilmaz
            self.log("Yakalama kapanışta durduruldu; açık parça diske yazıldı (not: Geçmiş → Yeniden özetle).")
            self._durum_uygula()
            return
        self._durum_ayarla("Toplantı bitti — not üretiliyor", "#EA580C")
        if self.tepsi and kaynak == "canli":
            self.tepsi.showMessage("BriefMind", "Toplantı bitti — not üretiliyor.")
        self._not_uret()
        self._durum_uygula()

    # ================================================================== belirsizler (salt okunur)
    _BELIRSIZ_ETIKET = {"kelime": "belirsiz kelime", "konusmaci": "emin olunmayan konuşmacı",
                        "supheli": "şüpheli klip (nota girmez)", "terim": "özetleyicinin bozuk dediği terim"}

    def _belirsizler_goster(self, liste=None):
        """Not sayfasindaki salt-okunur panel. liste None: acik motordan hesaplanir. Kaynak baglantisi 'ts:HH:MM:SS'
        (tiklaninca transkript o ani gosterir). Onay/duzeltme dugmesi yoktur."""
        import html as _h
        if liste is None:
            try:
                liste = self.motor.belirsizler() if self.motor else []
            except Exception as e:
                self.log(f"! belirsizler listesi okunamadı: {e!r}")
                liste = []
        liste = [x for x in liste if isinstance(x, dict)]
        self.k_belirsiz.deger.setText(str(len(liste)))
        self.belirsiz_kutu.setTitle(f"Belirsizler (salt okunur) — {len(liste)}")
        if not liste:
            self.belirsiz_goster.setHtml("<p>Belirsiz kelime ya da konuşmacı yok.</p>")
            return
        satirlar = []
        for x in liste[:self.BELIRSIZ_TAVAN]:
            bag = f" <a href='ts:{_h.escape(str(x['ts']))}'>⏱{_h.escape(str(x['ts']))}</a>" if x.get("ts") else ""
            etiket = self._BELIRSIZ_ETIKET.get(x.get("tur"), str(x.get("tur") or ""))
            adet = f" <span style='color:#64748B'>({x['satir_sayisi']} satır)</span>" if x.get("satir_sayisi", 1) > 1 else ""
            satirlar.append(f"<p><span style='color:#64748B'>{_h.escape(etiket)}</span> "
                            f"<b>{_h.escape(str(x.get('metin') or ''))}</b>{adet}{bag}<br>"
                            f"<span style='color:#64748B'>{_h.escape(str(x.get('baglam') or ''))}</span></p>")
        if len(liste) > self.BELIRSIZ_TAVAN:
            satirlar.append(f"<p>… {len(liste) - self.BELIRSIZ_TAVAN} kayıt daha (Geçmiş → transkript).</p>")
        self.belirsiz_goster.setHtml("".join(satirlar))

    def _not_uret(self):
        """Notu arka planda uretir (kullanici onayi yok). Yalniz 'bos'/'soru' durumunda: yakalama, baska not ya da yeniden
        ozetleme surerken baslamaz."""
        if not self.motor or self._kapaniyor:
            return
        d = self._durum()
        if d not in ("bos", "soru"):
            if d != "yakalaniyor":
                self.log("Başka bir iş sürüyor; not üretimi bitince tekrar deneyin.")
            return
        self._not_uret_aday = False
        self.ilerleme.show()
        self._durum_ayarla("Not üretiliyor…", "#EA580C")
        self.not_bilgi.setText("Not üretiliyor — parçalar birleştiriliyor…")
        self._serit_baslat("Not üretiliyor")
        self.nav.setCurrentRow(self.SAYFA_NOT)
        # sablon toplantiya baglidir (Motor: davetten / kayittan); genel ayar burada uygulanmaz
        if not self.motor.ben:
            self.motor.ben = self.a_ben.text().strip() or self.ayar.get("ben") or None
        self._not_kilit = True                        # TamamlamaIsi.finished'e kadar Baslat/Yeniden ozetle/Sor kapali
        self._not_sonuc_geldi = False
        self.tamamla = TamamlamaIsi(self.motor)
        self.tamamla.olay.connect(self.olay)
        self.tamamla.bitti.connect(self.not_hazir)
        self.tamamla.hata.connect(self.not_hata)
        self.tamamla.finished.connect(self._tamamla_sonlandi)
        self.tamamla.start()
        self._durum_uygula()

    def _not_kilidi_ac(self):
        self._not_kilit = False
        self._durum_uygula()

    def _tamamla_sonlandi(self):
        """TamamlamaIsi gercekten bitti: kilit acilir. Sonuc sinyali (bitti/hata) gelmeden olduyse arayuz 'not uretilemedi'
        durumuna alinir (kullanici Notu yeniden uretebilsin)."""
        if not self._not_kilit:
            self._durum_uygula()
            return
        sessiz = not self._not_sonuc_geldi
        if sessiz:
            self.ilerleme.hide()
            self._serit_bitir()
            self._not_uret_aday = self.motor is not None
            self._durum_ayarla("Not üretilemedi", "#DC2626")
        self._not_kilidi_ac()

    def not_hata(self, mesaj):
        self._not_sonuc_geldi = True
        self.ilerleme.hide()
        self._serit_bitir()
        self._not_uret_aday = self.motor is not None
        self._durum_ayarla("Not üretilemedi", "#DC2626")
        self.not_bilgi.setText("Not üretilemedi — LLM erişimini kontrol et; 'Notu yeniden üret' ya da Geçmiş → "
                               "Yeniden özetle ile tekrar dene. Ayrıntı: Olaylar paneli.")
        self._durum_uygula()
        if not self._kapaniyor:
            QtWidgets.QMessageBox.warning(self, "Not üretilemedi", mesaj[-800:])

    def not_hazir(self, md):
        self._not_sonuc_geldi = True
        self.not_md = md
        self._sablon_goster()
        self.not_goster.setHtml(md_to_html(md))
        self._not_gorunumu()
        self.ilerleme.hide()
        self._serit_bitir()
        self._not_uret_aday = self.motor is not None             # not istenirse yeniden uretilebilir
        self._durum_uygula()
        yol = os.path.join(self.motor.klasor, 'not.md')
        hic_bolum = "_(özetlenebilen bölüm yok)_" in md
        if hic_bolum or "## Eksik bölümler" in md:
            # LLM hatalari parca bazinda yutulur; eskiden not bos olsa bile durum yesil 'Not hazir' kaliyordu
            neden = f" Son LLM hatası: {llm.SON_HATA}" if llm.SON_HATA else ""
            self._durum_ayarla("Not eksik — bazı bölümler özetlenemedi", "#EA580C")
            self.not_bilgi.setText(f"Kaydedildi: {yol} — eksik bölümler var.{neden[:300]}")
            if hic_bolum:
                self._durum_ayarla("Not üretilemedi — hiçbir bölüm özetlenemedi", "#DC2626")
                if not self._kapaniyor:
                    QtWidgets.QMessageBox.warning(self, "Not üretilemedi",
                                                  ("Hiçbir bölüm özetlenemedi." + neden)[-800:]
                                                  + "\n\nAyarlar → 'LLM sunucusunu test et' ile bağlantıyı deneyin.")
        else:
            self._durum_ayarla("Not hazır", "#14B8A6")
            self.not_bilgi.setText(f"Kaydedildi: {yol}")
        self.nav.setCurrentRow(self.SAYFA_NOT)
        self._belirsizler_goster()
        self.gecmis_yenile()
        if self.a_outlook.isChecked() and not hic_bolum:      # bos notla Outlook taslagi acilmaz
            if self._kapaniyor:
                self.log("Outlook taslağı açılmadı: uygulama kapanıyor (Not sayfasından açılabilir)")
            else:
                self.outlook_ac()

    def outlook_ac(self):
        """Outlook taslagi (COM) arka planda acilir; arayuz Outlook'u beklemez."""
        if self.not_md and self.motor:
            self._arka(outlook.taslak, f"Toplantı notu: {self.motor.baslik} ({self.motor.tarih.isoformat()})",
                       self.not_md, list(self.motor.katilimcilar), bitti=self._taslak_acildi, ad="outlook-taslak")

    def _taslak_acildi(self, tamam):
        if tamam is False:
            self.log("! Outlook taslağı açılamadı (Outlook kurulu/açık mı?)")

    def klasor_ac(self):
        if self.motor and os.path.isdir(self.motor.klasor):
            if sys.platform.startswith("win"):
                os.startfile(os.path.abspath(self.motor.klasor))
            else:
                subprocess.Popen(["xdg-open", self.motor.klasor])

    # ================================================================== gecmis
    def _gecmis_kayitlari(self):
        kok = motor_mod.KOK
        if not os.path.isdir(kok):
            return []
        kayitlar = []
        for ad in os.listdir(kok):
            yol = os.path.join(kok, ad)
            try:
                with open(os.path.join(yol, "meta.json"), encoding="utf-8") as f:
                    m = json.load(f)
                kayitlar.append((m.get("tarih", ""), os.stat(yol).st_ctime, yol, m))
            except Exception:
                continue
        kayitlar.sort(key=lambda k: (k[0], k[1]), reverse=True)     # tarih, sonra olusturulma zamani
        return kayitlar

    def gecmis_yenile(self):
        self.g_liste.clear()
        for tarih, ctime, yol, m in self._gecmis_kayitlari():
            etiket = {"tamam": "tamam", "bitti": "not bekliyor", "inceleme": "not bekliyor",
                                                      "devam": "yarım"}.get(m["durum"], m["durum"])
            saat = dt.datetime.fromtimestamp(ctime).strftime("%H:%M")
            it = QtWidgets.QListWidgetItem(f"{tarih} {saat}  {m['baslik']}   [{etiket}, {m['parca']} parça]")
            it.setData(QtCore.Qt.UserRole, yol)
            if m["parca"] == 0:
                it.setForeground(QtGui.QColor("#94A3B8"))
            self.g_liste.addItem(it)

    def _secili_klasor(self):
        it = self.g_liste.currentItem()
        return it.data(QtCore.Qt.UserRole) if it else None

    def _parcalari_oku(self, k):
        pk = os.path.join(k, "parcalar")
        if not os.path.isdir(pk):
            return []
        parcalar = []
        for ad in sorted(os.listdir(pk)):
            if ad.endswith(".json"):
                with open(os.path.join(pk, ad), encoding="utf-8") as f:
                    parcalar.append(json.load(f))
        return parcalar

    def _tasma_bilgisi(self, k):
        """Secili kayitta altyazi tasmasi varsa uyari satiri (olcum dosya degismedikce yeniden yapilmaz)."""
        yol = os.path.join(k, "altyazi.jsonl")
        try:
            st = os.stat(yol)
        except OSError:
            return ""
        anahtar = (yol, st.st_mtime, st.st_size)
        onbellek = getattr(self, "_tasma_onbellek", {})
        if anahtar not in onbellek:
            try:
                onbellek[anahtar] = motor_mod.tasma_olcumu(k)
            except Exception:
                onbellek[anahtar] = None
            self._tasma_onbellek = onbellek
        t = onbellek[anahtar]
        if not t or not t["tasma"]:
            return ""
        return (f"⚠ Altyazı taşması: {t['satir']} satır (dakikada en çok {t['dk_en_cok']}, birebir tekrar "
                f"%{100 * t['tekrar_orani']:.0f}). 'Yeniden özetle' önce transkripti temizler (eski hali yedeklenir).")

    def _gecmis_secildi(self, it, _onceki=None):
        k = it.data(QtCore.Qt.UserRole) if it else None
        self.g_parcalar.clear()
        if not k:
            return
        try:
            with open(os.path.join(k, "meta.json"), encoding="utf-8") as f:
                m = json.load(f)
        except Exception:
            return
        bilgi = (f"{m['baslik']} — {m['tarih']} · durum: {m['durum']} · {m['parca']} parça · "
                 f"katılımcı: {len(m.get('katilimcilar', []))}"
                 + (f" · şablon: {llm.sablon_etiketi(m['sablon'])}" if m.get("sablon") in llm.SABLONLAR else ""))
        pay = not_araclari.konusma_payi_metni(m.get("konusma_paylari") or [])
        tasma = self._tasma_bilgisi(k)
        self.g_bilgi.setText(bilgi + ("\n" + pay if pay else "") + ("\n" + tasma if tasma else ""))
        for p_ in self._parcalari_oku(k):
            o = p_.get("ozet")
            oz = f"özet: {len(o['kararlar'])} karar, {len(o['aksiyonlar'])} aksiyon" if o else "özetsiz"
            it2 = QtWidgets.QListWidgetItem(f"Parça {p_['sira']}   {p_['baslangic']}–{p_['bitis']}   ~{p_['token']} token   "
                                            f"{len(p_.get('duzeltmeler', []))} düzeltme   {oz}")
            it2.setData(QtCore.Qt.UserRole, p_["sira"])
            if not o:
                it2.setForeground(QtGui.QColor("#B91C1C"))
            self.g_parcalar.addItem(it2)
        yol = os.path.join(k, "not.md")
        if os.path.exists(yol):
            with open(yol, encoding="utf-8") as f:
                self.g_detay.setHtml(md_to_html(f.read()))
        else:
            self.g_detay.setHtml(md_to_html(f"# {m['baslik']}\n\nNot henüz üretilmemiş. Parçaları tek tek inceleyebilir, "
                                            f"'Yeniden özetle' ile tüm parçaları özetleyip notu üretebilirsin.\n\n"
                                            f"Katılımcılar: {', '.join(m.get('katilimcilar', []))}"))

    def _parca_secildi(self, it, _onceki=None):
        k = self._secili_klasor()
        if not it or not k:
            return
        no = it.data(QtCore.Qt.UserRole)
        try:
            with open(os.path.join(k, "parcalar", f"parca_{no:03d}.json"), encoding="utf-8") as f:
                p_ = json.load(f)
        except Exception:
            return
        md = [f"# Parça {no} — {p_['baslangic']}–{p_['bitis']} (~{p_['token']} token, kapanış: {p_['neden']})"]
        o = p_.get("ozet")
        if o:
            o = llm._konulu_bolum(o)                 # eski / ham parca: {madde, konu} ogeleri duz metne + konu_etiketleri
            konu = llm.konu_bulucu(o)
            if o.get("ozet"):
                md.append("## Özet")
                md.append(o["ozet"])
            md.append("## Konular")
            md += [f"- {x}" for x in o.get("konular", [])] or ["- —"]
            md.append("## Kararlar")
            md += [f"- {x}{llm.konu_damgasi(konu(x, 'karar'))}" for x in o.get("kararlar", [])] or ["- —"]
            md.append("## Aksiyonlar")
            md.append(llm.AKSIYON_BASLIGI)           # notla ayni baslik (Konu son sutun; parcada kaynak zamani yok)
            md.append("|---|---|---|---|---|---|")
            md += [f"| {i + 1} | {a['madde']} | {a['sorumlu']} | {a['tarih']} | - | "
                   f"{llm.konu_temizle(konu(a['madde'], 'aksiyon')) or '-'} |"
                   for i, a in enumerate(o.get("aksiyonlar", []))]
            if o.get("acik_sorular"):
                md.append("## Açık sorular")
                md += [f"- {x}{llm.konu_damgasi(konu(x, 'soru'))}" for x in o["acik_sorular"]]
            if o.get("belirsiz_terimler"):
                md.append("## Belirsiz terimler")
                md += [f"- {x}" for x in o["belirsiz_terimler"]]
            # parca hangi sablonla ozetlendiyse onun ek bolumleri; secim yalniz parcanin kararlarinda karsiligi varsa kesin
            md += llm.sablon_bolumleri_md(llm.secenekleri_dogrula(o))
        else:
            md.append("## Özet yok")
            md.append("Bu parça özetlenmemiş. 'Seçili parçayı yeniden özetle' ile işleyebilirsin.")
        if p_.get("duzeltmeler"):
            md.append("## Uygulanan düzeltmeler")
            md += [f"- {a} → {b}" for a, b in p_["duzeltmeler"]]
        md.append("## Konuşmalar")
        try:
            with open(os.path.join(k, "meta.json"), encoding="utf-8") as f:
                tarih_ = dt.date.fromisoformat(json.load(f).get("tarih", ""))
        except Exception:
            tarih_ = dt.date.today()
        motor_mod.satirlari_goc(p_["satirlar"], tarih_)          # v2 oncesi kayit: 'Ad?' (gosterim kurali)
        md += [f"- {s_['ts']} **{motor_mod._konusan(s_)}**: {s_['text']}" for s_ in p_["satirlar"]]
        self.g_detay.setHtml(md_to_html("\n".join(md)))

    def _mesgul_mu(self, ileti):
        """Gecmis'ten motor yukleyen/degistiren eylemler yalniz 'bos' durumda (Motor LLM kancalarini ve self.motor'u
        degistirir; yakalama/not/yeniden ozetleme suruyorken karismamali)."""
        if self._durum() != "bos":
            QtWidgets.QMessageBox.information(self, "BriefMind", "Başka bir iş sürüyor; bitince tekrar dene.")
            self.log(f"{ileti}: başka bir iş sürüyor ({self._durum()})")
            return True
        return False

    def gecmis_not(self):
        if self._mesgul_mu("Notu aç"):
            return
        k = self._secili_klasor()
        yol = os.path.join(k, "not.md") if k else None
        if yol and os.path.exists(yol):
            with open(yol, encoding="utf-8") as f:
                self.not_md = f.read()
            self.motor = Motor.yukle(k, self.terimler, sablon=self.ayar.get("not_sablonu", llm.SABLON_OTOMATIK))
            self._sablon_goster()
            self.not_goster.setHtml(md_to_html(self.not_md))
            self._not_gorunumu()
            self.not_bilgi.setText(f"Kaydedildi: {yol}")
            self._not_uret_aday = True
            self._durum_uygula()
            self._belirsizler_goster()
            self.nav.setCurrentRow(self.SAYFA_NOT)
        else:
            QtWidgets.QMessageBox.information(self, "Not yok", "Bu toplantının notu henüz üretilmemiş.")

    def gecmis_yeniden_ozetle(self):
        """Secili toplantinin TUM parcalarini yeniden ozetler ve yeni not uretir (yarim kayitlar icin de). Duzeltme
        yalniz 'raw'i olan satirlarda calisir; v2 oncesi kayitlarin metni degismez."""
        k = self._secili_klasor()
        if not k:
            return
        if self._mesgul_mu("Yeniden özetle"):
            return
        self._not_uret_aday = False
        self._belirsizler_goster([])
        self.canli_not.clear()
        self._canli_sifirla()
        self.motor = None
        self.not_md = ""
        self.not_goster.clear()
        self._not_gorunumu()
        self.ilerleme.show()
        self._durum_ayarla("Yeniden özetleniyor…", "#EA580C")
        self.not_bilgi.setText(f"{os.path.basename(k)}: tüm parçalar yeniden özetleniyor…")
        self._serit_baslat("Kayıt yükleniyor")
        self.nav.setCurrentRow(self.SAYFA_NOT)
        try:
            with open(os.path.join(k, "meta.json"), encoding="utf-8") as f:
                self.baslik.setText(json.load(f).get("baslik", os.path.basename(k)))
        except Exception:
            self.baslik.setText(os.path.basename(k))
        ben = self.a_ben.text().strip() or self.ayar.get("ben") or None
        self.yeniden = YenidenOzetlemeIsi(k, self.terimler, self.a_duzelt.isChecked(), ben,
                                          self.ayar.get("not_sablonu", llm.SABLON_OTOMATIK))   # kayitli sablon once
        self._yeniden_kilit, self._yeniden_sonuc_geldi = True, False   # YenidenOzetlemeIsi.finished'e kadar Baslat kapali
        self.yeniden.olay.connect(self.olay)
        self.yeniden.durum.connect(lambda m: self._durum_ayarla(m, "#EA580C"))
        self.yeniden.bitti.connect(self._yeniden_bitti)
        self.yeniden.hata.connect(self._yeniden_hata)
        self.yeniden.finished.connect(self._yeniden_sonlandi)
        self.yeniden.start()
        self._durum_uygula()

    def _yeniden_bitti(self, md):
        self._yeniden_sonuc_geldi = True
        self.motor = self.yeniden.motor
        self.not_hazir(md)

    def _yeniden_hata(self, mesaj):
        self._yeniden_sonuc_geldi = True
        self.not_hata(mesaj)

    def _yeniden_sonlandi(self):
        """YenidenOzetlemeIsi gercekten bitti: kilit acilir (bitti/hata gelmeden olduyse arayuz sifirlanir)."""
        if self._yeniden_kilit and not self._yeniden_sonuc_geldi:
            self.ilerleme.hide()
            self._serit_bitir()
            self._durum_ayarla("Yeniden özetleme tamamlanamadı", "#DC2626")
        self._yeniden_kilit = False
        self._durum_uygula()

    def parca_yeniden(self):
        k = self._secili_klasor()
        it = self.g_parcalar.currentItem()
        if not k or not it:
            return
        if self._mesgul_mu("Parçayı yeniden özetle"):
            return
        no = it.data(QtCore.Qt.UserRole)
        if not (self.motor and self.motor.klasor == k):
            self.motor = Motor.yukle(k, self.terimler)
        self.ilerleme.show()
        self._durum_ayarla(f"Parça {no} yeniden özetleniyor…", "#EA580C")
        self.parca_isi = ParcaIsi(self.motor, no)
        self.parca_isi.olay.connect(self.olay)
        self.parca_isi.bitti.connect(self._parca_bitti)
        self.parca_isi.hata.connect(self._parca_hata)
        self._parca_kilit = True
        self.parca_isi.finished.connect(self._parca_sonlandi)
        self.parca_isi.start()
        self._durum_uygula()

    def _parca_sonlandi(self):
        self._parca_kilit = False
        self._durum_uygula()

    def _parca_hata(self, mesaj):
        self.ilerleme.hide()
        self._durum_ayarla("Parça özetlenemedi", "#DC2626")
        QtWidgets.QMessageBox.warning(self, "Parça özetlenemedi", mesaj[-800:])

    def _parca_bitti(self, no):
        self.ilerleme.hide()
        self._durum_ayarla(f"Parça {no} özetlendi", "#14B8A6")
        secili = self.g_liste.currentItem()
        self._gecmis_secildi(secili)
        for r in range(self.g_parcalar.count()):
            if self.g_parcalar.item(r).data(QtCore.Qt.UserRole) == no:
                self.g_parcalar.setCurrentRow(r)
                break

    def _klasor_mesgul(self, klasor):
        """Klasor etkin yakalamanin / notu uretilen motorun mu? (yakalama baslarken 0 parcali gorunur; silinmemeli)"""
        if self._durum() == "bos" and not _calisiyor(self.kesinti_isi):
            return False
        ad = os.path.normcase(os.path.abspath(klasor))
        for m in (self.motor, getattr(self.is_, "motor", None) if self._yakalama_acik else None):
            if m is not None and os.path.normcase(os.path.abspath(getattr(m, "klasor", "") or "")) == ad:
                return True
        return False

    def gecmis_sil(self):
        k = self._secili_klasor()
        if not k:
            return
        ad = os.path.basename(k)
        if self._klasor_mesgul(k):
            QtWidgets.QMessageBox.information(self, "Toplantıyı sil", "Bu toplantı şu an kullanılıyor (yakalama ya da "
                                              "not üretimi); bitince silebilirsin.")
            return
        if QtWidgets.QMessageBox.question(self, "Toplantıyı sil", f"'{ad}' klasörü ve içindeki her şey silinsin mi?",
                                          QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        import shutil
        shutil.rmtree(k, ignore_errors=True)
        if self.motor and self.motor.klasor == k:
            self.motor = None
        self.g_parcalar.clear()
        self.g_detay.clear()
        self.g_bilgi.clear()
        self.gecmis_yenile()

    def gecmis_bos_temizle(self):
        bos = [yol for _, _, yol, m in self._gecmis_kayitlari() if m.get("parca", 0) == 0 and not self._klasor_mesgul(yol)]
        if not bos:
            QtWidgets.QMessageBox.information(self, "Temiz", "0 parçalı kayıt yok.")
            return
        if QtWidgets.QMessageBox.question(self, "Boş kayıtları temizle", f"{len(bos)} adet 0 parçalı kayıt silinsin mi?",
                                          QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        import shutil
        for yol in bos:
            shutil.rmtree(yol, ignore_errors=True)
        self.gecmis_yenile()

    # ================================================================== ayarlar
    def ayar_kaydet(self):
        if not self._izinler()["kaydet"]:
            QtWidgets.QMessageBox.information(self, "Ayarlar", "Not üretilirken ayarlar kaydedilemez (LLM bağlantısı "
                                              "çalışıyor); bitince tekrar dene.")
            return
        self.a_route.setText(llm.route_normalize(self.a_route.text()))      # sonu '/v1', sondaki '/' yok
        self.ayar.update({"route": self.a_route.text().strip(), "model": self.a_model.text().strip(),
                          "context": self.a_context.value(),
                          "otobitir": self.a_otobitir.value(), "duzelt": self.a_duzelt.isChecked(),
                          "outlook": self.a_outlook.isChecked(), "otomatik_basla": self.a_oto.isChecked(),
                          "kaynak": "ses", "stt_url": self.a_stt.text().strip(),
                          "stt_model": self.a_stt_model.text().strip(), "stt_key": self.a_stt_key.text().strip(),
                          "mikrofon_modu": "otomatik", "ben": self.a_ben.text().strip(),
                          "altyazi_otomatik": self.a_altyazi_oto.isChecked(),
                          "altyazi_turkce": self.a_altyazi_tr.isChecked(),
                          "olcum_kaydi": self.a_olcum.isChecked(),
                          "altyazi_yedek": self.a_altyazi_yedek.isChecked(),
                          "altyazi_goster": self.a_altyazi_goster.isChecked(),
                          "not_sablonu": self.a_sablon.currentData(), "not_sablonu_surum": ayar_mod.SABLON_SURUM,
                          "birebir_taslak": self.a_birebir_taslak.isChecked(), "saklama_gun": self.a_saklama.value(),
                          "stt_ipucu": self.a_stt_ipucu.isChecked()})
        try:
            ayar_yaz(self.ayar)                # atomik: yarim config.json kalmaz
        except OSError as e:
            self.log(f"✖ config.json yazılamadı: {e!r}")
            QtWidgets.QMessageBox.warning(self, "Ayarlar", f"config.json yazılamadı: {e}")
            return
        llm.ayarla(self.ayar)                  # LLM adresi/modeli hemen gecerli: yeniden baslatma gerekmez
        rag.ayarla(self.ayar)
        self.durum.setText("Ayarlar kaydedildi")
        self.saklama_uygula()                  # saklama suresi hemen gecerli (acilis/saatlik turu beklenmez)
        self.llm_tani()

    def llm_tani(self, test=False):
        """LLM sunucusundan model listesi ve baglam penceresi (arka planda); uyarilar Olaylar'a yazilir.
        test=True: kisa bir sohbet denemesi de yapilir ve sonuc pencerede gosterilir."""
        if self._llm_tani_isi and self._llm_tani_isi.isRunning():
            if test:                         # tani surerken basilan 'LLM'i test et' sessizce kaybolmasin
                self._llm_test_bekliyor = True
                self.durum.setText("LLM tanısı sürüyor, bekleyin — bitince test başlayacak")
            return
        if test:
            self.durum.setText("LLM sunucusu test ediliyor…")
        self._llm_tani_isi = LlmTaniIsi(test=test)
        self._llm_tani_isi.bitti.connect(lambda satirlar, tamam: self._llm_tani_bitti(satirlar, tamam, test))
        self._llm_tani_isi.start()

    def _llm_tani_bitti(self, satirlar, tamam, test):
        for m in satirlar:
            self.log(("LLM: " if tamam else "! LLM: ") + m)
        if getattr(self, "_llm_test_bekliyor", False):
            self._llm_test_bekliyor = False
            if not test:                     # biten arka plan tanisiydi: bekleyen test simdi
                QtCore.QTimer.singleShot(0, lambda: self.llm_tani(test=True))
        if test:
            self.durum.setText("LLM testi tamam" if tamam else "LLM testi başarısız — ayrıntı: Olaylar")
            kutu = QtWidgets.QMessageBox.information if tamam else QtWidgets.QMessageBox.warning
            kutu(self, "LLM testi", "\n\n".join(satirlar)[-1500:] + "\n\nAyrıntılı ölçüm: python tools\\llm_teshis.py")

    def _altyazi_iste_popup(self):
        """Otomatik acma basarisiz: kullanicidan altyaziyi acmasini iste (modal degil, isi kesmez)."""
        if getattr(self, "_altyazi_popup", None):
            return
        k = QtWidgets.QMessageBox(self)
        k.setIcon(QtWidgets.QMessageBox.Information)
        k.setWindowTitle("BriefMind — altyazı")
        k.setText("Teams'te canlı altyazı açılamadı.")
        k.setInformativeText("Konuşmacı adlarının doğru gelmesi için toplantı penceresinde <b>Alt+Shift+C</b>'ye bas "
                             "(ya da … → Dil ve konuşma → Canlı altyazıları aç).<br><br>"
                             "Bir kez kalıcı yapmak için: … → Ayarlar → Erişilebilirlik → "
                             "<b>Toplantılarımda her zaman alt yazıları göster</b>.<br><br>"
                             "Ses kaydı sürüyor; altyazı olmadan da not üretilir.")
        k.setStandardButtons(QtWidgets.QMessageBox.Ok)
        k.setWindowModality(QtCore.Qt.NonModal)
        k.finished.connect(lambda _: setattr(self, "_altyazi_popup", None))
        self._altyazi_popup = k
        k.show()
        if self.tepsi:
            self.tepsi.showMessage("BriefMind", "Teams'te altyazıyı aç: Alt+Shift+C", QtWidgets.QSystemTrayIcon.Information, 8000)

    def stt_test(self):
        """Servis ayakta mi — ayarlar sayfasindan tek tikla (ag istegi arka planda; arayuz donmaz)."""
        if _calisiyor(self._stt_test_isi):
            return
        self.b_stt_test.setEnabled(False)
        self.durum.setText("STT servisi test ediliyor…")
        self._stt_test_isi = ArkaIs(stt_saglik_testi, self.a_stt.text().strip(), self.a_stt_model.text().strip(),
                                    self.a_stt_key.text().strip(), ayar_mod.tls_dogrulama(self.ayar), ad="stt-test")
        self._stt_test_isi.bitti.connect(self._stt_test_bitti)
        self._stt_test_isi.hata.connect(self._stt_test_hata)
        self._stt_test_isi.finished.connect(lambda: self.b_stt_test.setEnabled(True))
        self._stt_test_isi.start()

    def _stt_test_bitti(self, sonuc):
        tamam, neden = sonuc
        self.durum.setText("STT testi tamam" if tamam else "STT testi başarısız — ayrıntı: Olaylar")
        if not tamam and neden:
            self.log(f"! STT: {neden}")
        if self._kapaniyor:
            return
        # kulaklik/hoparlor artik toplanti sirasinda pasif korelasyonla (ton calmadan) algilanir
        QtWidgets.QMessageBox.information(
            self, "STT testi", f"Servis: {'ulaşılabilir' if tamam else 'ULAŞILAMIYOR'}\nKulaklık/hoparlör: toplantı "
                               "sırasında otomatik algılanır (mikrofon ve hoparlör sesi karşılaştırılır, ton çalınmaz).")

    def _stt_test_hata(self, mesaj):
        self.durum.setText("STT testi yapılamadı")
        self.log(f"✖ STT testi: {mesaj}")
        if not self._kapaniyor:
            QtWidgets.QMessageBox.warning(self, "STT testi", f"Test yapılamadı: {mesaj}\n\n"
                                          f"Gerekli paketler: pip install sounddevice soundcard numpy requests")

    # ================================================================== not araclari
    def bilgilendirme_kopyala(self):
        metin = self.ayar.get("bilgilendirme_metni") or not_araclari.BILGILENDIRME
        QtWidgets.QApplication.clipboard().setText(metin)
        self.log("bilgilendirme metni panoya kopyalandı — Teams toplantı sohbetine yapıştır (Ctrl+V)")

    def kesinti_devam(self):
        """Onceki oturumdan diskte kalan STT kesinti kuyrugunu (<toplanti>/olcum/kesinti) STT ayaktaysa otomatik cozer
        (acilista; baska is surerken 60 sn arayla en fazla 10 kez yeniden bakar). Sonuc Olaylar'a yazilir."""
        if self._kapaniyor or self._kapanis_tamam or not self.ayar.get("kesinti_devam", True) \
                or _calisiyor(self.kesinti_isi):
            return
        try:
            bekleyen = kesinti_bekleyenleri()
        except Exception as e:
            self.log(f"! kesinti kuyruğu taranamadı: {e!r}")
            return
        if not bekleyen:
            return
        if self._durum() != "bos":
            self._kesinti_deneme += 1
            if self._kesinti_deneme <= 10:
                self._tek_atim(60000, self.kesinti_devam)
            return
        toplam = sum(n for _, n in bekleyen)
        if not (self.ayar.get("stt_url") or "").strip():
            self.log(f"! önceki oturumdan {toplam} ses klibi çözülmeyi bekliyor ama stt_url ayarlı değil")
            return
        self.log(f"önceki oturumdan {toplam} ses klibi ({len(bekleyen)} toplantı) çözülmeyi bekliyor; "
                 "STT ulaşılabilirse otomatik çözülecek")
        self.kesinti_isi = KesintiDevamIsi(bekleyen, self.terimler, dict(self.ayar), self.a_duzelt.isChecked())
        self.kesinti_isi.olay.connect(self._kesinti_olay)
        self.kesinti_isi.bitti.connect(self._kesinti_bitti)
        self.kesinti_isi.finished.connect(self._durum_uygula)
        self.kesinti_isi.start()
        self._durum_uygula()

    def _kesinti_olay(self, tip, v):
        if tip == "log":                                 # satir/adim olaylari canli goruntuye karismaz
            self.log(v)

    def _kesinti_bitti(self, cozulen, kalan):
        self.log(f"önceki oturumun ses kuyruğu: {cozulen} klip çözüldü, {kalan} klip kaldı")
        if cozulen:
            self.gecmis_yenile()

    SAKLAMA_ARALIK_MS = 3600 * 1000

    def _saklama_aktif(self):
        """Saklama suresi dokunmayacagi klasorler: yalniz su an bir isin (yakalama, not uretimi, yeniden ozetleme, parca,
        soru, ses kuyrugu) calistigi toplantilar. Yalniz goruntulenen (Notu ac / bitmis kayit) toplanti korunmaz: temizlik
        sonrasi Motor zaten diskten okur; korunsaydi uygulama acik kaldikca transkripti suresiz kalirdi."""
        klasorler = [getattr(getattr(self.is_, "motor", None), "klasor", None) if _calisiyor(self.is_) else None]
        if self._durum() in ("yakalaniyor", "not_uretiliyor") or self._yeniden_kilit or self._parca_kilit \
                or self._soru_kilit:
            klasorler.append(getattr(self.motor, "klasor", None))          # isin motoru (baslarken / biterken de)
        for isi in (self.tamamla, self.parca_isi, self._soru_isi):          # .m: bu iscinin motoru
            if _calisiyor(isi):
                klasorler.append(getattr(getattr(isi, "m", None), "klasor", None))
        if _calisiyor(self.yeniden):
            klasorler.append(getattr(self.yeniden, "klasor", None))
        if _calisiyor(self.kesinti_isi):
            klasorler += list(getattr(self.kesinti_isi, "klasorler", []) or [])
        return [k[0] if isinstance(k, (tuple, list)) else k for k in klasorler if k]

    def saklama_uygula(self):
        """KVKK: acilista, her saat ve ayar kaydedilince. Notu uretilmemis toplantilar da tarihe gore temizlenir (aktif
        klasorler haric). Olaylar'a yalniz sayilar yazilir."""
        gun = int(self.ayar.get("saklama_gun", 0) or 0)
        if gun <= 0:
            return
        try:
            sonuc = motor_mod.saklama_tara(gun, aktif=self._saklama_aktif())
        except Exception as e:
            self.log(f"! saklama süresi uygulanamadı: {type(e).__name__}")
            return
        mesaj = motor_mod.saklama_ozeti(sonuc, gun)
        if mesaj:
            self.log(mesaj)
        if sonuc["toplanti"]:
            self.gecmis_yenile()

    def _baglanti(self, url, klasor):
        """Not/cevap/arama baglantilari: 'ts:HH:MM:SS' (o toplantida), 'git:N' (arama sonucu N)."""
        u = url.toString()
        if u.startswith("git:"):
            try:
                r = self._arama_sonuclari[int(u[4:])]
            except (ValueError, IndexError):
                return
            if r["ts"]:
                self._transkript_goster(r["klasor"], r["ts"], f"{r['tarih']} — {r['baslik']}")
            return
        if u.startswith("ts:"):
            if not klasor:
                QtWidgets.QMessageBox.information(self, "Kaynak", "Önce bir toplantı seçin ya da notu açın.")
                return
            self._transkript_goster(klasor, u[3:])

    def _transkript_goster(self, klasor, ts, baslik=None):
        import html as _h
        satirlar = not_araclari.ana_git(klasor, ts)
        d = QtWidgets.QDialog(self)
        d.setWindowTitle(f"Transkript — {ts}" + (f" · {baslik}" if baslik else ""))
        d.resize(760, 480)
        v = QtWidgets.QVBoxLayout(d)
        t = QtWidgets.QTextBrowser()
        if not satirlar:
            t.setHtml("<p>Bu ana ait transkript satırı yok (transkript saklama süresi nedeniyle silinmiş olabilir).</p>")
        else:
            parcalar = []
            for zaman, kim, metin, hedef in satirlar:
                stil = " style='background:#FEF3C7'" if hedef else ""
                parcalar.append(f"<p{stil}><span style='color:#64748B'>{_h.escape(str(zaman))}</span> "
                                f"<b>{_h.escape(str(kim))}</b>: {_h.escape(str(metin))}</p>")
            t.setHtml("".join(parcalar))
            hedef_no = next(i for i, x in enumerate(satirlar) if x[3])
            QtCore.QTimer.singleShot(50, lambda: t.verticalScrollBar().setValue(
                int(t.verticalScrollBar().maximum() * hedef_no / max(1, len(satirlar) - 1))))
        v.addWidget(t)
        b = QtWidgets.QPushButton("Kapat")
        b.clicked.connect(d.accept)
        v.addWidget(b, 0, QtCore.Qt.AlignRight)
        d.show()

    def soru_sor(self):
        soru = self.soru.text().strip()
        if not soru:
            return
        if not self._izinler()["sor"]:
            self.log(f"Soru sorulamaz: {self._durum()} sürüyor")
            return
        if not self.motor:
            QtWidgets.QMessageBox.information(self, "Soru", "Önce bir toplantı notu açın (Geçmiş → Notu aç).")
            return
        self.cevap.show()
        self.cevap.setHtml("<p>Cevap hazırlanıyor…</p>")
        self._soru_isi = SoruIsi(self.motor, soru)
        self._soru_isi.bitti.connect(self._cevap_geldi)
        self._soru_kilit = True
        self._soru_isi.finished.connect(self._soru_sonlandi)
        self._soru_isi.start()
        self._durum_uygula()

    def _soru_sonlandi(self):
        self._soru_kilit = False
        self._durum_uygula()

    def _cevap_geldi(self, metin):
        self.cevap.setHtml(md_to_html(metin))
        self._durum_uygula()

    def kisiye_ozel_eposta(self):
        if not self.not_md or not self.motor:
            return
        izin, aciklama = not_araclari.taslak_izni(self.motor.sablon, self.ayar)
        if not izin:                                  # birebir: varsayilan kapali (dugme de kapali)
            self.log(f"Kişiye özel e-posta açılmadı: {aciklama}")
            return
        epostalar = not_araclari.kisiye_ozel_epostalar(self.not_md, self.motor.baslik, self.motor.tarih.isoformat())
        if not epostalar:
            QtWidgets.QMessageBox.information(self, "E-posta", "Sorumlusu belli aksiyon yok.")
            return
        c = QtWidgets.QMessageBox.question(
            self, "Kişiye özel e-postalar",
            f"{len(epostalar)} kişi için Outlook taslağı açılacak (gönderilmez):\n"
            + ", ".join(ad for ad, _, _ in epostalar))
        if c != QtWidgets.QMessageBox.Yes:
            return
        self._arka(_taslaklari_ac, epostalar, bitti=self._taslaklar_acildi, ad="outlook-taslaklar")

    def _taslaklar_acildi(self, sonuc):
        acilan, toplam = sonuc
        self.log(f"{acilan}/{toplam} Outlook taslağı açıldı" + ("" if acilan == toplam else " — kalanlar açılamadı"))

    def _not_gorunumu(self):
        """Yeni not acilinca duzenleme kipini ve onceki soru cevabini kapat."""
        self.not_duzen.hide()
        self.not_goster.show()
        self.b_duzenle.setText("Düzenle")
        self.cevap.hide()

    def not_duzenle(self):
        if not self.not_md:
            return
        if self.not_duzen.isHidden():
            self.not_duzen.setPlainText(self.not_md)
            self.not_goster.hide()
            self.not_duzen.show()
            self.b_duzenle.setText("Kaydet")
            return
        self.not_md = self.not_duzen.toPlainText()
        if self.motor:
            with open(os.path.join(self.motor.klasor, "not.md"), "w", encoding="utf-8") as f:
                f.write(self.not_md)
        self.not_goster.setHtml(md_to_html(self.not_md))
        self.not_duzen.hide()
        self.not_goster.show()
        self.b_duzenle.setText("Düzenle")
        self.log("not düzenlendi ve kaydedildi")

    def not_disa_aktar(self, tur):
        if not self.not_md:
            return
        klasor = self.motor.klasor if self.motor else os.getcwd()
        filtre = {"docx": "Word belgesi (*.docx)", "pdf": "PDF (*.pdf)"}[tur]
        yol, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Dışa aktar", os.path.join(klasor, f"not.{tur}"), filtre)
        if not yol:
            return
        try:
            if tur == "docx":
                not_araclari.word_kaydet(self.not_md, yol)
            else:
                from PyQt5 import QtPrintSupport
                yazici = QtPrintSupport.QPrinter(QtPrintSupport.QPrinter.HighResolution)
                yazici.setOutputFormat(QtPrintSupport.QPrinter.PdfFormat)
                yazici.setOutputFileName(yol)
                belge = QtGui.QTextDocument()
                belge.setHtml(md_to_html(self.not_md))
                belge.print_(yazici)
        except ImportError:
            QtWidgets.QMessageBox.warning(self, "Word", "Word'e aktarmak için: pip install python-docx")
            return
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Dışa aktarma", repr(e))
            return
        self.log(f"not dışa aktarıldı: {yol}")

    def jira_disa_aktar(self):
        """N9: nottaki aksiyon tablosu -> Jira/Planner CSV (REST yok; dosya elle ice aktarilir)."""
        if not self.not_md:
            return
        izin, aciklama = not_araclari.taslak_izni(getattr(self.motor, "sablon", None), self.ayar)
        if not izin:
            self.log(f"Jira/Planner CSV yazılmadı: {aciklama}")
            return
        aks = not_araclari.aksiyonlari_ayikla(self.not_md)
        if not aks:
            QtWidgets.QMessageBox.information(self, "Jira/Planner CSV", "Notta aksiyon yok.")
            return
        klasor = self.motor.klasor if self.motor else os.getcwd()
        yol, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Aksiyonları CSV olarak kaydet",
                                                       os.path.join(klasor, "aksiyonlar.csv"), "CSV (*.csv)")
        if not yol:
            return
        ilk = self.not_md.splitlines()[0].lstrip("# ").strip() if self.not_md.strip() else ""
        baslik, _, tarih = ilk.rpartition(" — ")
        proje = f"{baslik} ({tarih})" if baslik and tarih else ilk
        try:
            n = not_araclari.jira_csv(aks, yol, proje)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Jira/Planner CSV", repr(e))
            return
        self.log(f"{n} aksiyon CSV'ye yazıldı: {yol} (Jira: Import from CSV; Planner: Excel'den içe aktar)")

    def gecmiste_ara(self):
        import html as _h
        sorgu = self.g_ara.text().strip()
        if not sorgu:
            return
        self._arama_sonuclari = not_araclari.ara(sorgu)
        if not self._arama_sonuclari:
            self.g_detay.setHtml(f"<p>'{_h.escape(sorgu)}' hiçbir toplantıda bulunamadı.</p>")
            return
        satirlar = [f"<h2>'{_h.escape(sorgu)}' — {len(self._arama_sonuclari)} sonuç</h2>"]
        for i, r in enumerate(self._arama_sonuclari):
            bag = f" <a href='git:{i}'>⏱{_h.escape(r['ts'])}</a>" if r["ts"] else ""
            kim = f"<b>{_h.escape(r['kim'])}</b>: " if r["kim"] else ""
            satirlar.append(f"<p><span style='color:#64748B'>{_h.escape(r['tarih'])} · {_h.escape(r['baslik'])} · "
                            f"{r['tur']}</span>{bag}<br>{kim}{_h.escape(r['metin'][:300])}</p>")
        self.g_detay.setHtml("".join(satirlar))

    # ================================================================== kapanis
    def _calisan_isler(self):
        """Calisan iscilerin listesi: [(ad, thread, azami_bekleme_sn)]."""
        adaylar = [("yakalama", self.is_, KAPANIS_YAKALAMA_SN), ("not", self.tamamla, KAPANIS_URETIM_SN),
                   ("yeniden özetleme", self.yeniden, KAPANIS_URETIM_SN), ("parça", self.parca_isi, KAPANIS_URETIM_SN),
                   ("soru", self._soru_isi, KAPANIS_URETIM_SN), ("ses kuyruğu", self.kesinti_isi, KAPANIS_URETIM_SN),
                   ("LLM tanısı", self._llm_tani_isi, KAPANIS_KISA_SN), ("takvim", self._takvim_isi, KAPANIS_KISA_SN),
                   ("STT testi", self._stt_test_isi, KAPANIS_KISA_SN)]
        adaylar += [("arka iş", i, KAPANIS_KISA_SN) for i in self._arka_isler]
        return [(ad, i, sn) for ad, i, sn in adaylar if _calisiyor(i)]

    def _kapanis_onayi(self, isler):
        adlar = {ad for ad, _, _ in isler}
        if "yakalama" in adlar:
            c = QtWidgets.QMessageBox.question(
                self, "BriefMind",
                "Yakalama sürüyor. Uygulama kapatılsın mı?\n\n"
                f"Evet: yakalama durur, açık parça en fazla {isler_mod.YakalamaIsi.KAPANIS_SN:g} sn içinde diske yazılır; toplantı "
                "kaydı diskte kalır (Geçmiş → Yeniden özetle).\n"
                "Hayır: pencere açık kalır.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
            return c == QtWidgets.QMessageBox.Yes
        if adlar & {"not", "yeniden özetleme", "parça", "ses kuyruğu"}:
            c = QtWidgets.QMessageBox.question(
                self, "BriefMind",
                "Not üretiliyor.\n\n"
                "Evet: üretim bitene kadar beklenir (en fazla "
                f"{KAPANIS_URETIM_SN // 60} dk), sonra uygulama kapanır; hiçbir şey yarıda kesilmez.\n"
                "Hayır: pencere açık kalır.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
            return c == QtWidgets.QMessageBox.Yes
        return True                                     # yalniz kisa isler (takvim, test): sormadan bekler

    def _kapanis_baslat(self, isler):
        """Iscilere durmalarini soyler, finished'lerini sinirli sure bekler (QThread'i canliyken yok etmemek icin)."""
        self._kapaniyor = True
        simdi = saat.simdi()
        for ad, i, sn in isler:
            self._kapanis_son[id(i)] = simdi + sn
            try:
                if ad == "yakalama":
                    i.durdur(hizli=True)               # ses servisi + acik parca <=30 sn'de diske
                elif hasattr(i, "durdur"):
                    i.durdur()
            except Exception:
                import traceback
                hata_logla(traceback.format_exc())
        self.ilerleme.show()
        self._durum_ayarla("Kapatılıyor — " + ", ".join(ad for ad, _, _ in isler) + " tamamlanıyor…", "#EA580C")
        self._durum_uygula()
        if self._kapanis_zamanlayici is None:
            self._kapanis_zamanlayici = QtCore.QTimer(self)
            self._kapanis_zamanlayici.timeout.connect(self._kapanis_kontrol)
        self._kapanis_zamanlayici.start(100)

    def _kapanis_kontrol(self):
        isler = self._calisan_isler()
        if not isler:
            QtCore.QCoreApplication.processEvents()      # biten iscilerin kuyruktaki sinyalleri (sonuc/finished) islensin
            isler = self._calisan_isler()
        if not isler:
            self._kapanis_bitir()
            return
        simdi = saat.simdi()
        suren = [x for x in isler if simdi < self._kapanis_son.setdefault(id(x[1]), simdi + x[2])]
        if not suren:                                  # hepsi azami beklemeyi astı
            self._kapanis_bitir(zorla=isler)

    def _kapanis_bitir(self, zorla=None):
        if self._kapanis_zamanlayici is not None:
            self._kapanis_zamanlayici.stop()
        if zorla:
            self.yetim_isler = [i for _, i, _ in zorla]
            hata_logla("Kapanış sınırı doldu, çalışan iş parçacıkları: " + ", ".join(ad for ad, _, _ in zorla)
                       + "\n(yakalama varsa açık parça son kapanış denemesinde diske yazılmış olmalı; yoksa "
                         "Geçmiş → Yeniden özetle)")
        self._kapanis_tamam = True
        self.close()

    def _zamanlayicilari_durdur(self):
        """Kapanan pencerenin tum zamanlayicilari (sayac, takvim, tek atimlar) durdurulur: kapali pencere tetiklenmez."""
        for t in self.findChildren(QtCore.QTimer):
            t.stop()

    def closeEvent(self, e):
        if self._kapanis_tamam:
            self._zamanlayicilari_durdur()
            if self.tepsi:
                self.tepsi.hide()
            e.accept()
            QtWidgets.QApplication.quit()
            return
        if self._kapaniyor:                            # kapanis suruyor; ikinci kapatma istegi yok sayilir
            e.ignore()
            return
        isler = self._calisan_isler()
        if isler and not self._kapanis_onayi(isler):
            e.ignore()
            return
        if isler:
            e.ignore()                                 # pencere iscilerin finished'ini bekleyerek acik kalir
            self._kapanis_baslat(isler)
            return
        self._kapanis_tamam = True
        self._zamanlayicilari_durdur()
        if self.tepsi:
            self.tepsi.hide()
        e.accept()
        QtWidgets.QApplication.quit()


def _hata_kaydet(tb):
    hata_logla(tb)


def _hata_kutusu(baslik, tb):
    """Hata penceresi. YALNIZ ana thread'de cagrilir (Qt sinyal koprusu ya da ana thread'in kendi hatasi)."""
    try:
        QtWidgets.QMessageBox.critical(None, baslik, tb[-1500:])
    except Exception:
        pass


class HataKoprusu(QtCore.QObject):
    """Is parcacigi hatalari -> ana thread. Sinyal kuyruklu baglanir: slot her zaman bu nesnenin
    (ana thread'de yaratilan) thread'inde calisir, QMessageBox hicbir zaman worker thread'de acilmaz."""
    hata = QtCore.pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hata.connect(self._goster, QtCore.Qt.QueuedConnection)

    @QtCore.pyqtSlot(str, str)
    def _goster(self, baslik, tb):
        _hata_kutusu(baslik, tb)


_KOPRU = None
_ANA_THREAD = threading.current_thread()


def hata_koprusu_kur():
    """Ana thread'de bir kez cagrilir (QApplication'dan sonra); koprunun ana thread'e ait olmasi gerekir."""
    global _KOPRU, _ANA_THREAD
    _ANA_THREAD = threading.current_thread()
    if _KOPRU is None:
        _KOPRU = HataKoprusu()
    return _KOPRU


def _hata_bildir(baslik, tb):
    """Log hata.log'a zaten yazildi. Ana thread'deysek dogrudan, degilse Qt sinyaliyle ana thread'e."""
    if _KOPRU is not None:
        _KOPRU.hata.emit(baslik, tb)
    elif threading.current_thread() is _ANA_THREAD:
        _hata_kutusu(baslik, tb)


def _excepthook(tip, deger, tb_obj):
    import traceback
    tb = "".join(traceback.format_exception(tip, deger, tb_obj))
    _hata_kaydet(tb)
    _hata_bildir("BriefMind — hata", tb)


def _thread_excepthook(args):
    """threading.excepthook (Python 3.8+): worker thread'de yakalanmayan istisna. Gunluk + ana thread'e sinyal."""
    if args.exc_type is SystemExit:
        return
    import traceback
    ad = getattr(args.thread, "name", "?")
    tb = f"[thread {ad}]\n" + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    _hata_kaydet(tb)
    _hata_bildir("BriefMind — arka plan hatası", tb)


def hata_kancalari_kur():
    sys.excepthook = _excepthook               # Qt slot'larindaki hatalar uygulamayi sessizce kapatmasin
    threading.excepthook = _thread_excepthook  # worker thread hatalari: hata.log + ana thread'e sinyal


def uygulama_klasoru():
    """Kaynak dosyalarin (config, terimler, toplantilar, logo) bulundugu klasor.
    Exe olarak calisirken exe'nin yanidir; PyInstaller'in gecici klasoru degil."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def logo_ikonu():
    """logo1.jpeg / logo.png / logo.ico'dan ilk bulunani QIcon olarak dondurur."""
    for ad in ("logo1.jpeg", "logo1.jpg", "logo.png", "logo.ico", "logo1.png"):
        yol = os.path.join(uygulama_klasoru(), ad)
        if os.path.exists(yol):
            pm = QtGui.QPixmap(yol)
            if not pm.isNull():
                return QtGui.QIcon(pm)
    return None


def duman_testi(pencere):
    """--duman: pencere kuruldu mu, durum makinesi 'bos' mu, paketlenen modullerin tamami ve veri/ dosyalari acilabiliyor
    mu? -> (tamam, satirlar). Exe'de (PyInstaller) eksik gizli import / veri dosyasini yakalar."""
    import importlib
    satirlar, tamam = [], True

    def kontrol(ad, kosul, ayrinti=""):
        nonlocal tamam
        satirlar.append(f"{'tamam' if kosul else 'HATA '}  {ad}" + (f"  {ayrinti}" if ayrinti else ""))
        tamam = tamam and bool(kosul)
    kontrol("pencere kuruldu", isinstance(pencere, QtWidgets.QMainWindow) and pencere.sayfalar.count() == 4)
    kontrol("durum makinesi: bos", pencere._durum() == "bos" and pencere.b_ana.isEnabled(), pencere._durum())
    for ad in ("konusmaci", "stt", "ses", "saat", "terim", "duzelt", "kelime", "rag", "motor", "llm", "yakalayici",
               "not_araclari", "outlook", "isler", "gorunum", "tarih", "metin", "uzun", "ses_izi", "cihaz"):
        try:
            importlib.import_module(ad)
            kontrol(f"modül {ad}", True)
        except Exception as e:                       # eksik gizli import
            kontrol(f"modül {ad}", False, repr(e))
    try:
        import kelime
        kontrol("veri/yaygin_kelimeler.txt", kelime.liste_var_mi())
    except Exception as e:
        kontrol("veri/yaygin_kelimeler.txt", False, repr(e))
    try:
        kontrol("config okuma", isinstance(ayar_oku(goc_yaz=False), dict) and ayar_mod.AYAR_HATASI is None,
                str(ayar_mod.AYAR_HATASI or ""))
    except Exception as e:
        kontrol("config okuma", False, repr(e))
    kontrol("not görünümü", "<h1>" in md_to_html("# Başlık\n\n- madde"))
    kontrol("hata.log yolu yazılabilir", ayar_mod.hata_yaz("") is not None, ayar_mod.hata_log_yolu())
    return tamam, satirlar


def _duman_yaz(satirlar):
    """Sonuc satirlari konsola ve hata.log'un yanindaki duman.txt'ye (--windowed exe'de konsol yok)."""
    metin = "\n".join(satirlar) + "\n"
    try:
        print(metin, flush=True)
    except Exception:
        pass
    try:
        klasor = os.path.dirname(os.path.abspath(ayar_mod.hata_log_yolu()))
        with open(os.path.join(klasor, "duman.txt"), "w", encoding="utf-8") as f:
            f.write(metin)
    except OSError:
        pass


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    duman = "--duman" in argv
    if duman:
        argv.remove("--duman")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")       # QApplication'dan ONCE: ekran/oturum gerekmez
    hata_kancalari_kur()
    os.chdir(uygulama_klasoru())                # config.json, terimler/, toplantilar/ hep bu klasorde
    try:
        app = QtWidgets.QApplication(argv)
        hata_koprusu_kur()                      # QApplication'dan sonra, ana thread'de
        app.setQuitOnLastWindowClosed(False)
        ikon = logo_ikonu()
        if ikon:
            app.setWindowIcon(ikon)
        p = Pencere(duman=duman)
        p.show()
        if duman:
            for _ in range(5):
                app.processEvents()
            tamam, satirlar = duman_testi(p)
            _duman_yaz(["BriefMind duman testi: " + ("TAMAM" if tamam else "BAŞARISIZ")] + satirlar)
            p._kapanis_tamam = True
            p.close()
            sys.exit(0 if tamam else 1)
        if ayar_mod.AYAR_HATASI:
            QtWidgets.QMessageBox.warning(
                p, "config.json okunamadı",
                "config.json geçerli JSON değil, varsayılan ayarlarla açıldı (STT/LLM adresleri boş olabilir).\n\n"
                f"Hata: {ayar_mod.AYAR_HATASI}\n\nSık neden: anahtar tırnaksız (stt_proxy: false yerine \"stt_proxy\": false) "
                "ya da önceki satırın sonunda virgül eksik. Düzeltip uygulamayı yeniden başlatın. "
                "Ayarlar sayfasında 'Kaydet'e basmayın; bozuk dosyanın üzerine varsayılanlar yazılır.")
        kod = app.exec_()
        if p.yetim_isler:                       # kapanis sinirinda hala calisan QThread: yok edilince Qt abort eder
            try:                                # pencereli exe'de sys.stdout None: flush hatasi os._exit'i atlatmasin
                sys.stdout.flush()
            except Exception:
                pass
            os._exit(kod)
        sys.exit(kod)
    except SystemExit:
        raise
    except Exception:
        import traceback
        tb = traceback.format_exc()
        _hata_kaydet(tb)
        try:
            QtWidgets.QMessageBox.critical(None, "BriefMind — açılış hatası", tb[-1500:])
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
