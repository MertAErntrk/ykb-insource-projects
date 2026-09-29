"""
app.py — Toplanti Notu masaustu uygulamasi (PyQt5).

  pip install -r requirements.txt PyQt5
  python app.py

Sekmeler: Canli | Inceleme | Not | Sozluk | Gecmis | Ayarlar. Tepside kucuk ikon.
Akis: Baslat -> Teams'te altyazi gorunene kadar bekler -> yakalar, parcalari arka planda isler
      -> Bitir (ya da altyazi kaybolunca otomatik) -> otomatik_not (varsayilan): not dogrudan uretilir,
      Not sekmesinde ilerleme seridi; kapaliysa Inceleme sekmesi: supheli terimler
      -> "Uygula ve notu uret" -> Not sekmesi (+ Outlook taslagi)
Modul yapisi: isler.py (QThread'ler), gorunum.py (tema, md_to_html), ayar.py (config.json).
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time

from PyQt5 import QtCore, QtGui, QtWidgets

import ayar as ayar_mod
import llm
import motor as motor_mod
import not_araclari
import outlook
from ayar import ayar_oku, ayar_yaz
from gorunum import QSS, RENKLER, kart, md_to_html, svg_ikon  # noqa: F401  (md_to_html: app.md_to_html uyumu)
from isler import ParcaIsi, SoruIsi, TamamlamaIsi, YakalamaIsi, YenidenOzetlemeIsi
from motor import Motor
from sozluk import Sozluk

class Pencere(QtWidgets.QMainWindow):
    SAYFALAR = ["Canlı", "İnceleme", "Not", "Sözlük", "Geçmiş", "Ayarlar"]
    IKON_AD = ["canli", "inceleme", "not", "sozluk", "gecmis", "ayarlar"]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("BriefMind")
        self.resize(1180, 740)
        self.setStyleSheet(QSS)
        self.ayar = ayar_oku()
        self.sozluk = Sozluk()
        self.is_ = None
        self.motor = None
        self.not_md = ""
        self.ctx = {}
        self.baslangic = None
        self.bitis = None
        self._akis_satirlari = []
        self.renkler = {}
        self._canli_ozetler = {}             # parca sirasi -> ozet (canli ara sayac icin)
        self._serit_bas = None               # not uretimi ilerleme seridinin baslangic zamani
        self.tamamla = None
        self.yeniden = None
        self._kur()
        self._tepsi()
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self, self.ana_dugme)
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+1"), self, lambda: self.nav.setCurrentRow(0))
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+2"), self, lambda: self.nav.setCurrentRow(1))
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+3"), self, lambda: self.nav.setCurrentRow(2))
        self.zamanlayici = QtCore.QTimer(self)
        self.zamanlayici.timeout.connect(self._sayac_guncelle)
        self._takvim_zamanlayici = QtCore.QTimer(self)                 # bostayken takvim listesi tazelenir
        self._takvim_zamanlayici.timeout.connect(lambda: self.toplantilari_yenile(False))
        self._takvim_zamanlayici.start(60000)
        QtCore.QTimer.singleShot(800, lambda: self.toplantilari_yenile(False))
        self.zamanlayici.start(1000)
        QtCore.QTimer.singleShot(1500, self.saklama_uygula)
        if self.ayar.get("otomatik_basla"):
            QtCore.QTimer.singleShot(500, self.baslat)

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
        for s in (self._canli(), self._inceleme(), self._not(), self._sozluk(), self._gecmis(), self._ayarlar()):
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
        self.k_oneri = kart("Şüpheli terim", "0")
        self.k_katilimci = kart("Konuşan", "0")
        self.k_kaynak = kart("Kaynak", "—")
        for k in (self.k_durum, self.k_parca, self.k_oneri, self.k_katilimci, self.k_kaynak):
            kartlar.addWidget(k)
        v.addLayout(kartlar)
        h = QtWidgets.QHBoxLayout()
        sol = QtWidgets.QVBoxLayout()
        sol.addWidget(QtWidgets.QLabel("Konuşmalar"))
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

    def _inceleme(self):
        w, v = self._sayfa()
        self.inc_bilgi = QtWidgets.QLabel("Altyazıda bozuk duyulmuş olabilecek terimler toplantı sürerken burada birikir. "
                                          "Toplantı bitince doğrusunu yazıp Onayla/Yoksay seç; onaylananlar sözlüğe girer, "
                                          "yalnızca etkilenen parçalar yeniden özetlenir.", objectName="bilgi")
        self.inc_bilgi.setWordWrap(True)
        v.addWidget(self.inc_bilgi)
        self.tablo = QtWidgets.QTableWidget(0, 7)
        self.tablo.setHorizontalHeaderLabels(["#", "Parça", "Tür", "Altyazıda", "Doğrusu (çift tıkla düzenle)", "Bağlam", "Karar"])
        hb = self.tablo.horizontalHeader()
        hb.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        hb.setSectionResizeMode(4, QtWidgets.QHeaderView.Interactive)
        hb.setSectionResizeMode(5, QtWidgets.QHeaderView.Stretch)
        self.tablo.setColumnWidth(4, 220)
        self.tablo.itemChanged.connect(self._dogrusu_degisti)
        self.tablo.verticalHeader().setVisible(False)
        self.tablo.setAlternatingRowColors(True)
        self.tablo.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked | QtWidgets.QAbstractItemView.SelectedClicked)
        v.addWidget(self.tablo, 1)
        h = QtWidgets.QHBoxLayout()
        b1 = QtWidgets.QPushButton("Hepsini onayla")
        b1.clicked.connect(lambda: self._toplu_karar("Onayla"))
        b2 = QtWidgets.QPushButton("Hepsini yoksay")
        b2.clicked.connect(lambda: self._toplu_karar("Yoksay"))
        self.b_uygula = QtWidgets.QPushButton("Uygula ve notu üret", objectName="vurgu")
        self.b_uygula.setEnabled(False)
        self.b_uygula.clicked.connect(lambda: self.uygula())
        h.addWidget(b1)
        h.addWidget(b2)
        h.addStretch(1)
        h.addWidget(self.b_uygula)
        v.addLayout(h)
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
        v.addWidget(self.not_bilgi)
        self.not_goster = QtWidgets.QTextBrowser()
        self.not_goster.document().setDocumentMargin(16)
        self.not_goster.setOpenLinks(False)
        self.not_goster.anchorClicked.connect(lambda u: self._baglanti(u, self.motor.klasor if self.motor else None))
        v.addWidget(self.not_goster, 1)
        self.not_duzen = QtWidgets.QPlainTextEdit()
        self.not_duzen.hide()
        v.addWidget(self.not_duzen, 1)
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
        b_kisi = QtWidgets.QPushButton("Kişiye özel e-postalar")
        b_kisi.setToolTip("Sorumlusu belli her kişi için yalnızca kendi işlerini içeren bir Outlook taslağı açar")
        b_kisi.clicked.connect(self.kisiye_ozel_eposta)
        b2 = QtWidgets.QPushButton("Panoya kopyala")
        b2.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(self.not_md))
        self.b_duzenle = QtWidgets.QPushButton("Düzenle")
        self.b_duzenle.clicked.connect(self.not_duzenle)
        b_word = QtWidgets.QPushButton("Word")
        b_word.clicked.connect(lambda: self.not_disa_aktar("docx"))
        b_pdf = QtWidgets.QPushButton("PDF")
        b_pdf.clicked.connect(lambda: self.not_disa_aktar("pdf"))
        b3 = QtWidgets.QPushButton("Klasörü aç")
        b3.clicked.connect(self.klasor_ac)
        for b in (self.b_outlook, b_kisi, b2, self.b_duzenle, b_word, b_pdf, b3):
            h.addWidget(b)
        h.addStretch(1)
        v.addLayout(h)
        return w

    def _sozluk(self):
        w, v = self._sayfa()
        v.addWidget(QtWidgets.QLabel("Altyazının bozduğu terimleri ve doğru yazımları buraya ekle. 'Altyazıda' boşsa sadece "
                                     "terim olarak modele tanıtılır; doluysa yakalama anında otomatik düzeltilir.",
                                     objectName="bilgi"))
        h = QtWidgets.QHBoxLayout()
        self.s_yanlis = QtWidgets.QLineEdit()
        self.s_yanlis.setPlaceholderText("altyazıda görünen (örn. komplo tit)")
        self.s_dogru = QtWidgets.QLineEdit()
        self.s_dogru.setPlaceholderText("doğrusu (örn. commit)")
        self.s_dogru.returnPressed.connect(self.sozluk_ekle)
        b = QtWidgets.QPushButton("Ekle", objectName="birincil")
        b.clicked.connect(self.sozluk_ekle)
        h.addWidget(self.s_yanlis, 1)
        h.addWidget(QtWidgets.QLabel("→"))
        h.addWidget(self.s_dogru, 1)
        h.addWidget(b)
        v.addLayout(h)
        self.s_tablo = QtWidgets.QTableWidget(0, 2)
        self.s_tablo.setHorizontalHeaderLabels(["Altyazıda", "Doğrusu / Terim"])
        self.s_tablo.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.s_tablo.verticalHeader().setVisible(False)
        self.s_tablo.setAlternatingRowColors(True)
        self.s_tablo.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.s_tablo.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        v.addWidget(self.s_tablo, 1)
        b2 = QtWidgets.QPushButton("Seçili satırı sil")
        b2.clicked.connect(self.sozluk_sil)
        v.addWidget(b2, 0, QtCore.Qt.AlignLeft)
        self.sozluk_yenile()
        return w

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
        b2 = QtWidgets.QPushButton("Notu 'Not' sayfasında aç")
        b2.clicked.connect(self.gecmis_not)
        b3 = QtWidgets.QPushButton("Seçili parçayı yeniden özetle")
        b3.clicked.connect(self.parca_yeniden)
        b4 = QtWidgets.QPushButton("Yeniden özetle", objectName="birincil")
        b4.setToolTip("Tüm parçaları yeniden özetler ve yeni not üretir (yarım kalan kayıtlar için de). "
                      "Var olan not not.md.yedek-… olarak saklanır.")
        b4.clicked.connect(self.gecmis_yeniden_ozetle)
        b5 = QtWidgets.QPushButton("Sil", objectName="tehlike")
        b5.clicked.connect(self.gecmis_sil)
        b6 = QtWidgets.QPushButton("Boş kayıtları temizle")
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
        self.a_otobitir = QtWidgets.QSpinBox()
        self.a_otobitir.setRange(30, 1800)
        self.a_otobitir.setValue(int(self.ayar["otobitir"]))
        self.a_duzelt = QtWidgets.QCheckBox("Parçaları özetlemeden önce arka planda altyazı düzeltmesi yap")
        self.a_duzelt.setChecked(bool(self.ayar["duzelt"]))
        self.a_outlook = QtWidgets.QCheckBox("Not hazır olunca Outlook taslağını otomatik aç")
        self.a_outlook.setChecked(bool(self.ayar["outlook"]))
        self.a_oto = QtWidgets.QCheckBox("Uygulama açılınca beklemede başla (altyazı görünce yakala)")
        self.a_oto.setChecked(bool(self.ayar["otomatik_basla"]))
        self.a_otomatik_not = QtWidgets.QCheckBox("Toplantı bitince incelemeyi atla, notu doğrudan üret")
        self.a_otomatik_not.setToolTip("Şüpheli terimler yine İnceleme sekmesine yazılır; sonradan onaylayıp "
                                       "'Uygula ve notu üret' ile notu yeniden üretebilirsin.")
        self.a_otomatik_not.setChecked(bool(self.ayar.get("otomatik_not", True)))
        self.a_kaynak = QtWidgets.QComboBox()
        for etiket, deger in (("Ses (Whisper) — metin sesten, konuşmacı altyazıdan", "ses"),
                              ("Ses + Altyazı (ikisini de nota al)", "ikisi"),
                              ("Yalnızca altyazı", "altyazi")):
            self.a_kaynak.addItem(etiket, deger)
        i = self.a_kaynak.findData(self.ayar.get("kaynak", "ses"))
        self.a_kaynak.setCurrentIndex(max(0, i))
        self.a_stt = QtWidgets.QLineEdit(self.ayar.get("stt_url", ""))
        self.a_mik = QtWidgets.QComboBox()
        self.a_mik.addItem("Otomatik (kulaklık/hoparlör her başlatmada algılanır)", "otomatik")
        self.a_mik.setEnabled(False)
        ben = self.ayar.get("ben", "") or outlook.kullanici_adi()
        self.a_ben = QtWidgets.QLineEdit(ben)
        self.a_ben.setPlaceholderText("uygulamayı açan kişi (mikrofondan gelen cümlelerin sahibi)")
        self.a_ben.setToolTip("Oturum açan kullanıcıdan otomatik alındı; istersen düzelt")
        self.a_altyazi_oto = QtWidgets.QCheckBox("Altyazı görünmezse otomatik aç (arka planda menü; olmazsa Alt+Shift+C kısayolu, "
                                                 "~0,2 sn odak geçişi)")
        self.a_altyazi_oto.setChecked(bool(self.ayar.get("altyazi_otomatik", True)))
        self.a_altyazi_tr = QtWidgets.QCheckBox("Altyazı bulununca konuşulan dili Türkçe yapmayı dene (Teams seçimi hatırlar)")
        self.a_altyazi_tr.setChecked(bool(self.ayar.get("altyazi_turkce", True)))
        self.a_sablon = QtWidgets.QComboBox()
        for anahtar, (etiket, _) in llm.SABLONLAR.items():
            self.a_sablon.addItem(etiket, anahtar)
        self.a_sablon.setCurrentIndex(max(0, self.a_sablon.findData(self.ayar.get("not_sablonu", "genel"))))
        self.a_saklama = QtWidgets.QSpinBox()
        self.a_saklama.setRange(0, 3650)
        self.a_saklama.setSuffix(" gün")
        self.a_saklama.setSpecialValueText("süresiz (kapalı)")
        self.a_saklama.setValue(int(self.ayar.get("saklama_gun", 0) or 0))
        self.a_saklama.setToolTip("Notu üretilmiş toplantıların transkripti bu süreden sonra silinir; not.md kalır.")
        b_test = QtWidgets.QPushButton("STT servisini test et")
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
        f.addRow(b_test)
        f.addRow(QtWidgets.QLabel("— LLM —", objectName="alt_bilgi"))
        f.addRow("LLM adresi", self.a_route)
        f.addRow("Model", self.a_model)
        f.addRow("Altyazı kaybolunca otomatik bitiş (sn)", self.a_otobitir)
        f.addRow(self.a_duzelt)
        f.addRow(self.a_outlook)
        f.addRow(self.a_oto)
        f.addRow(QtWidgets.QLabel("— Not —", objectName="alt_bilgi"))
        f.addRow(self.a_otomatik_not)
        f.addRow("Not şablonu", self.a_sablon)
        f.addRow("Transkript saklama süresi", self.a_saklama)
        b = QtWidgets.QPushButton("Kaydet", objectName="birincil")
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
        menu.addAction("Çıkış", QtWidgets.qApp.quit)
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
        self.b_ana.setText(metin)
        self.b_ana.setObjectName(ad)
        self.b_ana.style().unpolish(self.b_ana)
        self.b_ana.style().polish(self.b_ana)
        self.b_ana.update()

    def _sayac_guncelle(self):
        if self.baslangic and self.bitis is None and self.is_ and self.is_.isRunning():
            s = int((dt.datetime.now() - self.baslangic).total_seconds())
            self.sayac.setText(f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}")
        if self._serit_bas is not None:
            s = int(time.time() - self._serit_bas)
            self.serit_sure.setText(f"{s} sn" if s < 60 else f"{s // 60} dk {s % 60:02d} sn")
        if self.motor:
            biten = sum(1 for i in self.motor.isler if i.done())
            self.k_parca.deger.setText(f"{biten} / {self.motor.parca_no}")
            self.k_katilimci.deger.setText(str(len(self.motor.katilimcilar)))

    def _renk(self, ad):
        if ad not in self.renkler:
            self.renkler[ad] = RENKLER[len(self.renkler) % len(RENKLER)]
        return self.renkler[ad]

    def _serit_baslat(self, metin):
        self._serit_bas = time.time()
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

    def log(self, m):
        self.gunluk.appendPlainText(f"{dt.datetime.now():%H:%M:%S}  {m}")

    # ================================================================== akis
    def ana_dugme(self):
        if self.is_ and self.is_.isRunning():
            self.bitir()
        else:
            self.baslat()

    def toplantilari_yenile(self, elle=False):
        """Bu saatteki takvim toplantilarini acilir listeye doldurur; Teams penceresindeki adla
        eslesen varsa onu secer. Yakalama surerken listeye dokunulmaz."""
        if self.is_ and self.is_.isRunning():
            return
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
        secilen, adaylar, skor = outlook.toplanti_esle(teams_adi)
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

    def _toplanti_bilgisi(self):
        """O an KATILINAN toplantiyi belirler: Teams penceresindeki ad + takvimden eslesen kayit.
        Emin olunamazsa kullaniciya adaylari sorar. Dondurur (baslik, ctx)."""
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
        secilen, adaylar, skor = outlook.toplanti_esle(teams_adi)
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
        if self.is_ and self.is_.isRunning():
            return
        if self.toplanti_secici.count() == 0 and not self.toplanti_secici.currentText().strip():
            self.toplantilari_yenile()
        yazili = self.toplanti_secici.currentText().strip()
        i = self.toplanti_secici.currentIndex()
        adaylar = getattr(self, "_adaylar", [])
        if 0 <= i < len(adaylar) and yazili == adaylar[i]["baslik"]:
            baslik, ctx = adaylar[i]["baslik"], adaylar[i]
        elif yazili:
            baslik, ctx = yazili, {}
        else:
            baslik, ctx = self._toplanti_bilgisi()
        self.baslik.setText(baslik)
        self.ctx = ctx
        if ctx:
            self.alt_bilgi.setText(f"{ctx['baslangic']:%H:%M}–{ctx['bitis']:%H:%M} · "
                                   + ", ".join(ctx["katilimcilar"][:6])
                                   + (" …" if len(ctx["katilimcilar"]) > 6 else ""))
        else:
            self.alt_bilgi.setText("Takvim bilgisi yok; katılımcılar altyazıdan öğrenilecek.")
        self.akis.clear()
        self._akis_satirlari = []
        self._uyum_uyari = False
        self.canli_not.clear()
        self._canli_sifirla()
        self.tablo.setRowCount(0)
        self.renkler = {}
        self.not_md = ""
        self.b_uygula.setEnabled(False)
        self.nav.item(1).setText(self.SAYFALAR[1])
        self.k_oneri.deger.setText("0")
        self.ayar["kaynak"] = self.a_kaynak.currentData()
        self.ayar["stt_url"] = self.a_stt.text().strip()
        self.ayar["stt_model"] = self.a_stt_model.text().strip()
        self.ayar["stt_key"] = self.a_stt_key.text().strip()
        self.ayar["mikrofon_modu"] = "otomatik"
        self.ayar["ben"] = self.a_ben.text().strip() or outlook.kullanici_adi()
        self.ayar["altyazi_otomatik"] = self.a_altyazi_oto.isChecked()
        self.ayar["altyazi_turkce"] = self.a_altyazi_tr.isChecked()
        self.k_kaynak.deger.setText({"altyazi": "Altyazı", "ses": "Ses (Whisper)",
                                     "ikisi": "Ses + Altyazı"}[self.ayar["kaynak"]])
        self.baslangic = None            # altyazi/ses bulununca baslar
        self.bitis = None
        self.sayac.setText("00:00")
        self.is_ = YakalamaIsi(baslik, ctx, self.sozluk, self.a_duzelt.isChecked(),
                               self.a_otobitir.value(), self.ayar)
        self.is_.olay.connect(self.olay)
        self.is_.durum.connect(lambda m: self._durum_ayarla(m, "#EA580C" if "bekleniyor" in m.lower() or "görünmüyor" in m.lower() else "#14B8A6"))
        self.is_.inceleme_hazir.connect(self.inceleme_hazir)
        self.is_.start()
        QtCore.QTimer.singleShot(1500, lambda: setattr(self, "motor", getattr(self.is_, "motor", None)))
        self._ana_stil("Bitir", "tehlike")
        self.nav.setCurrentRow(0)

    def bitir(self):
        if self.is_ and self.is_.isRunning():
            self.bitis = dt.datetime.now()
            self.is_.durdur()
            self.b_ana.setEnabled(False)
            self._durum_ayarla("Bitiriliyor…", "#EA580C")
            self.ilerleme.show()

    def _satir_html(self, v):
        r = self._renk(v["speaker"])
        if v.get("kaynak") == "altyazi" and self.ayar.get("kaynak") != "altyazi":
            return (f'<span style="color:#CBD5E1;font-size:11px">{v["ts"]}</span> '
                    f'<span style="color:{r};opacity:0.55">{v["speaker"]}</span> '
                    f'<span style="color:#94A3B8;font-size:10px">[altyazı]</span>'
                    f'<span style="color:#94A3B8"> {v["text"]}</span>')
        kim = v["speaker"]
        kim_html = (f'<span style="color:#94A3B8">?</span>' if kim == "?" else f'<b style="color:{r}">{kim}</b>')
        return f'<span style="color:#94A3B8;font-size:11px">{v["ts"]}</span> {kim_html}: {v["text"]}'

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

    def _uyum_guncelle(self):
        """Kaynak karti: Whisper cumleleri altyaziyla ne kadar ortusuyor? Dusukse uyar."""
        m = getattr(self, "motor", None) or (self.is_.motor if self.is_ else None)
        if not m or m.kaynak == "altyazi":
            return
        u = m.uyum_orani()
        if u is None:
            self.k_kaynak.deger.setText("Ses (Whisper)")
            return
        self.k_kaynak.deger.setText(f"Ses ✓ uyum %{int(u * 100)}")
        if len(m.uyum) >= 6 and u < 0.15 and not getattr(self, "_uyum_uyari", False):
            self._uyum_uyari = True
            self.log("! Whisper metni altyazıyla hiç örtüşmüyor: ses yanlış cihazdan geliyor olabilir "
                     "(Windows ses çıkışı Teams'in kullandığı cihaz mı?) ya da altyazı başka dilde.")

    def olay(self, tip, v):
        if tip == "satir":
            self._akis_satirlari.append(dict(v))
            self._akis_satirlari = self._akis_satirlari[-400:]
            self._akis_ekle(v)
        elif tip == "satir_guncelle":
            for x in self._akis_satirlari:
                if x.get("id") == v["id"]:
                    x["speaker"] = v["speaker"]
            self._akis_ciz()
        elif tip == "log":
            self.log(v)
            if "yakalama başladı" in str(v) and self.baslangic is None:
                self.baslangic = dt.datetime.now()
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
                for k in o["kararlar"]:
                    self.canli_not.addItem(f"Karar: {k}")
                for a in o["aksiyonlar"]:
                    self.canli_not.addItem(f"Aksiyon: {a['madde']}  — {a['sorumlu']}, {a['tarih']}")
                self.canli_not.scrollToBottom()
            else:
                self.log(f"✖ parça {v['sira']} özetlenemedi (bitişte tekrar denenecek)")
        elif tip == "altyazi_iste":
            self._altyazi_iste_popup()
        elif tip == "sure_dondur":
            self.bitis = self.bitis or dt.datetime.now()
        elif tip == "altyazi_izi":
            if self.ayar.get("altyazi_goster", False):
                self._akis_satirlari.append({"ts": v["ts"], "speaker": v["speaker"], "text": v["text"],
                                             "kaynak": "altyazi", "id": None})
                self._akis_satirlari = self._akis_satirlari[-400:]
                self._akis_ekle(self._akis_satirlari[-1])
            self.k_katilimci.deger.setText(str(len(self.motor.katilimcilar)) if self.motor else
                                           self.k_katilimci.deger.text())
            self._uyum_guncelle()
        elif tip == "ses_parca":
            self.log(f"♪ {v['kaynak']} {v['sn']} sn gönderildi (kuyruk {v['kuyruk']})")
        elif tip == "ses_metin":
            g = f", gecikme {v['gecikme']} sn" if v.get("gecikme") is not None else ""
            self.log(f"✔ {v['kaynak']} {v['sn']} sn → {v['segment']} cümle ({v['islem']} sn{g})")
            if self.baslangic is None:
                self.baslangic = dt.datetime.now()
            self._uyum_guncelle()
        elif tip == "kaynak_degisti":
            self.k_kaynak.deger.setText({"altyazi": "Altyazı (yedek)", "ses": "Ses (Whisper)",
                                         "ikisi": "Ses + Altyazı"}.get(v, str(v)))
            self.log(f"kaynak değişti: {v}")
        elif tip == "stt_gecikme":
            if isinstance(v, dict) and not v.get("atlandi"):
                self.log(f"! STT yavaş, {v['kuyruk']} parça sırada bekliyor (atlanmıyor)")
            else:
                self.log(f"! STT yanıt vermiyor, {v['kuyruk'] if isinstance(v, dict) else v} parça bekliyor — bir parça atlandı")
        elif tip == "oneri":
            self.oneri_satiri(v)
            self.nav.item(1).setText(f"{self.SAYFALAR[1]}  ({self.tablo.rowCount()})")
            self.k_oneri.deger.setText(str(self.tablo.rowCount()))

    def inceleme_hazir(self, oneriler, kaynak="canli", otomatik=None):
        """Toplanti bitti (ya da Gecmis'ten kayit hazirlandi). otomatik_not acikken inceleme beklenmez:
        oneriler bilgi icin tabloya yazilir, not bos kararlarla hemen uretilir."""
        if otomatik is None:
            otomatik = self.a_otomatik_not.isChecked()
        if kaynak == "canli":
            self.motor = self.is_.motor if self.is_ else self.motor
        mevcut = {int(self.tablo.item(r, 0).text()) for r in range(self.tablo.rowCount())}
        for o in oneriler:
            if o["id"] not in mevcut:
                self.oneri_satiri(o)
        self.ilerleme.hide()
        self.b_uygula.setEnabled(True)
        self.b_ana.setEnabled(True)
        self._ana_stil("Başlat", "birincil")
        n = self.tablo.rowCount()
        onerili = sum(1 for r in range(n) if self.tablo.item(r, 4).text().strip())
        self._durum_ayarla(f"İnceleme bekliyor — {n} şüpheli terim", "#EA580C")
        self.k_oneri.deger.setText(str(n))
        baslik = "Toplantı bitti." if kaynak == "canli" else "Kayıt yüklendi, parçalar işlendi."
        if otomatik:
            self.inc_bilgi.setText(f"{baslik} Not inceleme beklemeden üretildi. {n} şüpheli terim bilgi için burada: "
                                   "istersen doğrusunu yazıp onayla, 'Uygula ve notu üret' ile sözlüğe al ve notu "
                                   "yeniden üret.")
            if self.tepsi and kaynak == "canli":
                self.tepsi.showMessage("BriefMind", "Toplantı bitti — not üretiliyor.")
            self._not_uret({})
            return
        self.inc_bilgi.setText(f"{baslik} {n} şüpheli terim: {onerili} tanesinde öneri hazır (Onayla seçili), "
                               f"{n - onerili} tanesinde doğrusunu senin yazman gerekiyor (yazınca otomatik onaylanır). "
                               "Onaylananlar sözlüğe girer ve yalnızca etkilenen parçalar yeniden özetlenir. "
                               "Hiç şüpheli yoksa doğrudan 'Uygula ve notu üret'.")
        self.nav.setCurrentRow(1)
        self.showNormal()
        self.activateWindow()
        if self.tepsi and kaynak == "canli":
            self.tepsi.showMessage("BriefMind", f"Toplantı bitti — {n} şüpheli terim inceleme bekliyor.")

    # ================================================================== inceleme
    def oneri_satiri(self, o):
        import html as _h
        self.tablo.blockSignals(True)
        r = self.tablo.rowCount()
        self.tablo.insertRow(r)
        tur = "düzeltme önerisi" if o.get("tur") == "duzeltme" else "belirsiz terim"
        for c, deger in enumerate([str(o["id"]), str(o["parca"]), tur, o["yanlis"], o.get("oneri", "")]):
            it = QtWidgets.QTableWidgetItem(deger)
            if c != 4:
                it.setFlags(it.flags() & ~QtCore.Qt.ItemIsEditable)
            if c == 3:
                it.setForeground(QtGui.QColor("#B91C1C"))
            if c == 4 and not deger:
                it.setBackground(QtGui.QColor("#FFEDD5"))
                it.setToolTip("Öneri yok — doğrusunu sen yaz; yazınca karar otomatik 'Onayla' olur")
            if c == 2:
                it.setForeground(QtGui.QColor("#475569"))
            self.tablo.setItem(r, c, it)
        baglam = o.get("baglam", "") or ""
        vurgulu = _h.escape(baglam)
        if o["yanlis"]:
            import re as _re
            vurgulu = _re.sub(_re.escape(_h.escape(o["yanlis"])), lambda m: f"<b style='color:#B91C1C'>{m.group(0)}</b>",
                              vurgulu, flags=_re.IGNORECASE)
        et = QtWidgets.QLabel(vurgulu)
        et.setWordWrap(True)
        et.setToolTip(baglam)
        et.setStyleSheet("padding:2px 6px;")
        self.tablo.setCellWidget(r, 5, et)
        kutu = QtWidgets.QComboBox()
        kutu.addItems(["Onayla", "Yoksay"])
        kutu.setCurrentIndex(0 if o.get("oneri") else 1)
        self.tablo.setCellWidget(r, 6, kutu)
        self.tablo.resizeRowToContents(r)
        self.tablo.blockSignals(False)

    def _dogrusu_degisti(self, it):
        """'Doğrusu' hücresine bir şey yazıldıysa kararı otomatik Onayla yap (yazılan kaybolmasın)."""
        if it.column() != 4:
            return
        kutu = self.tablo.cellWidget(it.row(), 6)
        if kutu:
            kutu.setCurrentText("Onayla" if it.text().strip() else "Yoksay")
        if it.text().strip():
            it.setBackground(QtGui.QColor("#FFFFFF"))

    def _toplu_karar(self, k):
        for r in range(self.tablo.rowCount()):
            if k == "Onayla" and not self.tablo.item(r, 4).text().strip():
                continue                                   # doğrusu boşsa onaylanacak bir şey yok
            self.tablo.cellWidget(r, 6).setCurrentText(k)

    def kararlar(self):
        k = {}
        for r in range(self.tablo.rowCount()):
            oid = int(self.tablo.item(r, 0).text())
            dogru = self.tablo.item(r, 4).text().strip()
            onay = self.tablo.cellWidget(r, 6).currentText() == "Onayla"
            k[oid] = dogru if (onay and dogru) else None
        return k

    def uygula(self):
        self._not_uret(self.kararlar())

    def _not_uret(self, kararlar):
        """Inceleme kararlarini (otomatik_not: bos) uygulayip notu arka planda uretir."""
        if not self.motor or (self.tamamla and self.tamamla.isRunning()):
            return
        self.b_uygula.setEnabled(False)
        self.ilerleme.show()
        self._durum_ayarla("Not üretiliyor…", "#EA580C")
        self.not_bilgi.setText("Not üretiliyor — düzeltmeler uygulanıyor, parçalar birleştiriliyor…")
        self._serit_baslat("Not üretiliyor")
        self.nav.setCurrentRow(2)
        self.motor.sablon = self.ayar.get("not_sablonu", "genel")
        if not self.motor.ben:
            self.motor.ben = self.a_ben.text().strip() or self.ayar.get("ben") or None
        self.tamamla = TamamlamaIsi(self.motor, kararlar)
        self.tamamla.olay.connect(self.olay)
        self.tamamla.bitti.connect(self.not_hazir)
        self.tamamla.hata.connect(self.not_hata)
        self.tamamla.start()

    def not_hata(self, mesaj):
        self.ilerleme.hide()
        self._serit_bitir()
        self.b_uygula.setEnabled(self.motor is not None)
        self._durum_ayarla("Not üretilemedi", "#DC2626")
        self.not_bilgi.setText("Not üretilemedi — LLM erişimini kontrol et; İnceleme sayfasından ya da Geçmiş → "
                               "Yeniden özetle ile tekrar dene. Ayrıntı: Olaylar paneli.")
        QtWidgets.QMessageBox.warning(self, "Not üretilemedi", mesaj[-800:])

    def not_hazir(self, md):
        self.not_md = md
        self.not_goster.setHtml(md_to_html(md))
        self._not_gorunumu()
        self.ilerleme.hide()
        self._serit_bitir()
        self.b_uygula.setEnabled(self.tablo.rowCount() > 0)    # oneriler sonradan onaylanip not yeniden uretilebilir
        self._durum_ayarla("Not hazır", "#14B8A6")
        self.not_bilgi.setText(f"Kaydedildi: {os.path.join(self.motor.klasor, 'not.md')}")
        self.nav.setCurrentRow(2)
        self.sozluk_yenile()
        self.gecmis_yenile()
        if self.a_outlook.isChecked():
            self.outlook_ac()

    def outlook_ac(self):
        if self.not_md and self.motor:
            outlook.taslak(f"Toplantı notu: {self.motor.baslik} ({self.motor.tarih.isoformat()})",
                           self.not_md, self.motor.katilimcilar)

    def klasor_ac(self):
        if self.motor and os.path.isdir(self.motor.klasor):
            if sys.platform.startswith("win"):
                os.startfile(os.path.abspath(self.motor.klasor))
            else:
                subprocess.Popen(["xdg-open", self.motor.klasor])

    # ================================================================== sozluk
    def sozluk_yenile(self):
        self.s_tablo.setRowCount(0)
        for y, d in sorted(self.sozluk.aliaslar.items()):
            r = self.s_tablo.rowCount()
            self.s_tablo.insertRow(r)
            self.s_tablo.setItem(r, 0, QtWidgets.QTableWidgetItem(y))
            self.s_tablo.setItem(r, 1, QtWidgets.QTableWidgetItem(d))
        for t in self.sozluk.terimler:
            r = self.s_tablo.rowCount()
            self.s_tablo.insertRow(r)
            self.s_tablo.setItem(r, 0, QtWidgets.QTableWidgetItem(""))
            self.s_tablo.setItem(r, 1, QtWidgets.QTableWidgetItem(t))

    def sozluk_ekle(self):
        y, d = self.s_yanlis.text().strip(), self.s_dogru.text().strip()
        if not d:
            return
        if y:
            self.sozluk.alias_ekle(y, d)
        else:
            self.sozluk.terim_ekle(d)
        self.s_yanlis.clear()
        self.s_dogru.clear()
        self.sozluk_yenile()
        self.s_yanlis.setFocus()

    def sozluk_sil(self):
        r = self.s_tablo.currentRow()
        if r < 0:
            return
        y, d = self.s_tablo.item(r, 0).text(), self.s_tablo.item(r, 1).text()
        if y:
            self.sozluk.aliaslar.pop(y, None)
        else:
            self.sozluk.terimler = [t for t in self.sozluk.terimler if t != d]
        self.sozluk._derle()
        self.sozluk.kaydet()
        self.sozluk_yenile()

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
            etiket = {"tamam": "tamam", "inceleme": "inceleme bekliyor", "devam": "yarım"}.get(m["durum"], m["durum"])
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
        self.g_bilgi.setText(f"{m['baslik']} — {m['tarih']} · durum: {m['durum']} · {m['parca']} parça · "
                             f"katılımcı: {len(m.get('katilimcilar', []))}")
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
            if o.get("ozet"):
                md.append("## Özet")
                md.append(o["ozet"])
            md.append("## Konular")
            md += [f"- {x}" for x in o.get("konular", [])] or ["- —"]
            md.append("## Kararlar")
            md += [f"- {x}" for x in o.get("kararlar", [])] or ["- —"]
            md.append("## Aksiyonlar")
            md.append("| # | Madde | Sorumlu | Tarih |")
            md.append("|---|---|---|---|")
            md += [f"| {i + 1} | {a['madde']} | {a['sorumlu']} | {a['tarih']} |" for i, a in enumerate(o.get("aksiyonlar", []))]
            if o.get("acik_sorular"):
                md.append("## Açık sorular")
                md += [f"- {x}" for x in o["acik_sorular"]]
            if o.get("belirsiz_terimler"):
                md.append("## Belirsiz terimler")
                md += [f"- {x}" for x in o["belirsiz_terimler"]]
        else:
            md.append("## Özet yok")
            md.append("Bu parça özetlenmemiş. 'Seçili parçayı yeniden özetle' ile işleyebilirsin.")
        if p_.get("duzeltmeler"):
            md.append("## Uygulanan düzeltmeler")
            md += [f"- {a} → {b}" for a, b in p_["duzeltmeler"]]
        md.append("## Konuşmalar")
        md += [f"- {s_['ts']} **{s_['speaker']}**: {s_['text']}" for s_ in p_["satirlar"]]
        self.g_detay.setHtml(md_to_html("\n".join(md)))

    def gecmis_not(self):
        k = self._secili_klasor()
        yol = os.path.join(k, "not.md") if k else None
        if yol and os.path.exists(yol):
            with open(yol, encoding="utf-8") as f:
                self.not_md = f.read()
            self.motor = Motor.yukle(k, self.sozluk)
            self.not_goster.setHtml(md_to_html(self.not_md))
            self._not_gorunumu()
            self.not_bilgi.setText(f"Kaydedildi: {yol}")
            self.nav.setCurrentRow(2)
        else:
            QtWidgets.QMessageBox.information(self, "Not yok", "Bu toplantının notu henüz üretilmemiş.")

    def gecmis_yeniden_ozetle(self):
        """Secili toplantinin TUM parcalarini yeniden ozetler ve yeni not uretir (yarim kayitlar icin de).
        otomatik_not kapaliysa sonunda Inceleme sekmesine gidilir (eski 'Yarim kalani tamamla' davranisi)."""
        k = self._secili_klasor()
        if not k:
            return
        if (self.is_ and self.is_.isRunning()) or (self.yeniden and self.yeniden.isRunning()) \
                or (self.tamamla and self.tamamla.isRunning()):
            QtWidgets.QMessageBox.information(self, "Yeniden özetle", "Başka bir iş sürüyor; bitince tekrar dene.")
            return
        otomatik = self.a_otomatik_not.isChecked()
        self.tablo.setRowCount(0)
        self.nav.item(1).setText(self.SAYFALAR[1])
        self.k_oneri.deger.setText("0")
        self.b_uygula.setEnabled(False)
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
        self.nav.setCurrentRow(2)
        try:
            with open(os.path.join(k, "meta.json"), encoding="utf-8") as f:
                self.baslik.setText(json.load(f).get("baslik", os.path.basename(k)))
        except Exception:
            self.baslik.setText(os.path.basename(k))
        ben = self.a_ben.text().strip() or self.ayar.get("ben") or None
        self.yeniden = YenidenOzetlemeIsi(k, self.sozluk, otomatik, self.a_duzelt.isChecked(), ben,
                                          self.ayar.get("not_sablonu", "genel"))
        self.yeniden.olay.connect(self.olay)
        self.yeniden.durum.connect(lambda m: self._durum_ayarla(m, "#EA580C"))
        self.yeniden.bitti.connect(self._yeniden_bitti)
        self.yeniden.inceleme_hazir.connect(self._yeniden_inceleme)
        self.yeniden.hata.connect(self.not_hata)
        self.yeniden.start()

    def _yeniden_bitti(self, md):
        self.motor = self.yeniden.motor
        mevcut = {int(self.tablo.item(r, 0).text()) for r in range(self.tablo.rowCount())}
        for o in self.motor.bekleyen_oneriler():        # bilgi icin: sonradan onaylanip not yeniden uretilebilir
            if o["id"] not in mevcut:
                self.oneri_satiri(o)
        n = self.tablo.rowCount()
        if n:
            self.nav.item(1).setText(f"{self.SAYFALAR[1]}  ({n})")
            self.k_oneri.deger.setText(str(n))
            self.inc_bilgi.setText(f"Kayıt yeniden özetlendi, not üretildi. {n} şüpheli terim bilgi için burada: "
                                   "istersen onaylayıp 'Uygula ve notu üret' ile notu yeniden üret.")
        self.not_hazir(md)

    def _yeniden_inceleme(self, oneriler):
        self.motor = self.yeniden.motor
        self._serit_bitir()
        self.inceleme_hazir(oneriler, kaynak="gecmis", otomatik=False)

    def parca_yeniden(self):
        k = self._secili_klasor()
        it = self.g_parcalar.currentItem()
        if not k or not it:
            return
        no = it.data(QtCore.Qt.UserRole)
        if not (self.motor and self.motor.klasor == k):
            self.motor = Motor.yukle(k, self.sozluk)
        self.ilerleme.show()
        self._durum_ayarla(f"Parça {no} yeniden özetleniyor…", "#EA580C")
        self.parca_isi = ParcaIsi(self.motor, no)
        self.parca_isi.olay.connect(self.olay)
        self.parca_isi.bitti.connect(self._parca_bitti)
        self.parca_isi.start()

    def _parca_bitti(self, no):
        self.ilerleme.hide()
        self._durum_ayarla(f"Parça {no} özetlendi", "#14B8A6")
        secili = self.g_liste.currentItem()
        self._gecmis_secildi(secili)
        for r in range(self.g_parcalar.count()):
            if self.g_parcalar.item(r).data(QtCore.Qt.UserRole) == no:
                self.g_parcalar.setCurrentRow(r)
                break

    def gecmis_sil(self):
        k = self._secili_klasor()
        if not k:
            return
        ad = os.path.basename(k)
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
        bos = [yol for _, _, yol, m in self._gecmis_kayitlari() if m.get("parca", 0) == 0]
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
        self.ayar.update({"route": self.a_route.text().strip(), "model": self.a_model.text().strip(),
                          "otobitir": self.a_otobitir.value(), "duzelt": self.a_duzelt.isChecked(),
                          "outlook": self.a_outlook.isChecked(), "otomatik_basla": self.a_oto.isChecked(),
                          "kaynak": self.a_kaynak.currentData(), "stt_url": self.a_stt.text().strip(),
                          "stt_model": self.a_stt_model.text().strip(), "stt_key": self.a_stt_key.text().strip(),
                          "mikrofon_modu": "otomatik", "ben": self.a_ben.text().strip(),
                          "altyazi_otomatik": self.a_altyazi_oto.isChecked(),
                          "altyazi_turkce": self.a_altyazi_tr.isChecked(),
                          "not_sablonu": self.a_sablon.currentData(), "saklama_gun": self.a_saklama.value(),
                          "otomatik_not": self.a_otomatik_not.isChecked()})
        ayar_yaz(self.ayar)
        llm.ayarla(self.ayar)                  # LLM adresi/modeli hemen gecerli: yeniden baslatma gerekmez
        self.durum.setText("Ayarlar kaydedildi")

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
        """Servis ayakta mi, kulaklik var mi — ayarlar sayfasindan tek tikla."""
        try:
            import ses as ses_mod
            ist = ses_mod.SttIstemci(self.a_stt.text().strip(), model=self.a_stt_model.text().strip(),
                                     api_key=self.a_stt_key.text().strip())
            saglik = "ulaşılabilir" if ist.saglik() else "ULAŞILAMIYOR"
            kul = ses_mod.kulaklik_var_mi()
            QtWidgets.QMessageBox.information(
                self, "STT testi",
                f"Servis: {saglik}\nKulaklık testi: {'kulaklık var (çift akış)' if kul else 'hoparlör (tek akış)'}")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "STT testi", f"Test yapılamadı: {e!r}\n\n"
                                          f"Gerekli paketler: pip install sounddevice soundcard numpy requests")

    # ================================================================== not araclari
    def bilgilendirme_kopyala(self):
        metin = self.ayar.get("bilgilendirme_metni") or not_araclari.BILGILENDIRME
        QtWidgets.QApplication.clipboard().setText(metin)
        self.log("bilgilendirme metni panoya kopyalandı — Teams toplantı sohbetine yapıştır (Ctrl+V)")

    def saklama_uygula(self):
        gun = int(self.ayar.get("saklama_gun", 0) or 0)
        if gun <= 0:
            return
        try:
            n = motor_mod.saklama_uygula(gun)
        except Exception as e:
            self.log(f"! saklama süresi uygulanamadı: {e!r}")
            return
        if n:
            self.log(f"saklama süresi ({gun} gün): {n} toplantının transkripti silindi, notları duruyor")
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
        if not self.motor:
            QtWidgets.QMessageBox.information(self, "Soru", "Önce bir toplantı notu açın (Geçmiş → Notu aç).")
            return
        self.b_sor.setEnabled(False)
        self.cevap.show()
        self.cevap.setHtml("<p>Cevap hazırlanıyor…</p>")
        self._soru_isi = SoruIsi(self.motor, soru)
        self._soru_isi.bitti.connect(self._cevap_geldi)
        self._soru_isi.start()

    def _cevap_geldi(self, metin):
        self.b_sor.setEnabled(True)
        self.cevap.setHtml(md_to_html(metin))

    def kisiye_ozel_eposta(self):
        if not self.not_md or not self.motor:
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
        for ad, konu, govde in epostalar:
            outlook.taslak(konu, govde, [ad])

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

    def closeEvent(self, e):
        uretim = [i for i in (self.tamamla, self.yeniden) if i is not None and i.isRunning()]
        if uretim and not (self.is_ and self.is_.isRunning()):
            c = QtWidgets.QMessageBox.question(
                self, "BriefMind",
                "Not üretiliyor, bitmesini bekleyin.\n\n"
                "Yine de kapatılırsa not yarım kalır; parçalar diskte durur (Geçmiş → Yeniden özetle).\n"
                "Kapatılsın mı?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
            if c != QtWidgets.QMessageBox.Yes:
                e.ignore()
                return
        if self.is_ and self.is_.isRunning():
            c = QtWidgets.QMessageBox.question(
                self, "BriefMind",
                "Yakalama sürüyor. Uygulama kapatılsın mı?\n\n"
                "Evet: yakalama durur, toplantı kaydı diskte kalır (Geçmiş → Yeniden özetle).\n"
                "Hayır: pencere açık kalır.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
            if c != QtWidgets.QMessageBox.Yes:
                e.ignore()
                return
            try:
                self.is_.durdur()
                self.is_.wait(3000)
            except Exception:
                pass
        if self.tepsi:
            self.tepsi.hide()
        e.accept()
        QtWidgets.QApplication.quit()

def _hata_kaydet(tb):
    with open("hata.log", "a", encoding="utf-8") as f:
        f.write(f"\n===== {dt.datetime.now():%Y-%m-%d %H:%M:%S} =====\n{tb}")


def _excepthook(tip, deger, tb_obj):
    import traceback
    tb = "".join(traceback.format_exception(tip, deger, tb_obj))
    _hata_kaydet(tb)
    try:
        QtWidgets.QMessageBox.critical(None, "BriefMind — hata", tb[-1500:])
    except Exception:
        pass


def uygulama_klasoru():
    """Kaynak dosyalarin (config, sozluk, toplantilar, logo) bulundugu klasor.
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


def main():
    sys.excepthook = _excepthook               # Qt slot'larindaki hatalar uygulamayi sessizce kapatmasin
    os.chdir(uygulama_klasoru())                # config.json, sozluk.json, toplantilar/ hep bu klasorde
    try:
        app = QtWidgets.QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)
        ikon = logo_ikonu()
        if ikon:
            app.setWindowIcon(ikon)
        p = Pencere()
        p.show()
        if ayar_mod.AYAR_HATASI:
            QtWidgets.QMessageBox.warning(
                p, "config.json okunamadı",
                "config.json geçerli JSON değil, varsayılan ayarlarla açıldı (STT/LLM adresleri boş olabilir).\n\n"
                f"Hata: {ayar_mod.AYAR_HATASI}\n\nSık neden: anahtar tırnaksız (stt_proxy: false yerine \"stt_proxy\": false) "
                "ya da önceki satırın sonunda virgül eksik. Düzeltip uygulamayı yeniden başlatın. "
                "Ayarlar sayfasında 'Kaydet'e basmayın; bozuk dosyanın üzerine varsayılanlar yazılır.")
        sys.exit(app.exec_())
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
