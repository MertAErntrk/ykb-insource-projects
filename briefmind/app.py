"""
app.py — Toplanti Notu masaustu uygulamasi (PyQt5).

  pip install -r requirements.txt PyQt5
  python app.py

Sekmeler: Canli | Inceleme | Not | Sozluk | Gecmis | Ayarlar. Tepside kucuk ikon.
Akis: Baslat -> Teams'te altyazi gorunene kadar bekler -> yakalar, parcalari arka planda isler
      -> Bitir (ya da altyazi kaybolunca otomatik) -> Inceleme sekmesi: supheli terimler
      -> "Uygula ve notu uret" -> Not sekmesi (+ Outlook taslagi)
"""
import contextlib
import datetime as dt
import json
import os
import subprocess
import sys
import time

from PyQt5 import QtCore, QtGui, QtWidgets

import llm
import motor as motor_mod
import outlook
from motor import Motor
from sozluk import Sozluk
from yakalayici import Yakalayici, ekran_okuyucu

try:
    import uiautomation as auto
    _UIA_THREAD = getattr(auto, "UIAutomationInitializerInThread", None)
except Exception:                       # Windows disi ortamda import edilemez
    _UIA_THREAD = None

AYAR_DOSYA = "config.json"
AYAR_HATASI = None                         # config.json okunamadiysa neden (acilista kullaniciya gosterilir)


def ayar_oku():
    v = {"route": llm.ROUTE, "model": llm.MODEL, "otobitir": 180, "duzelt": True, "outlook": True,
         "otomatik_basla": False,
         "kaynak": "ses",                  # altyazi | ses | ikisi
         "stt_url": "",                        # config.json: STT servisi (OpenAI uyumlu /v1/audio/transcriptions)
         "stt_model": "whisper-large-v3-turbo-prod",     # ARGE GPU servisi (Turkce fine-tune)
         "stt_key": "EMPTY",                              # kendi CPU servisimiz icin: model "whisper", anahtar bos
         "mikrofon_modu": "otomatik",      # otomatik | cift | tek
         "ben": "",
         "altyazi_otomatik": True,         # altyazi gorunmezse Teams'te acmayi dene
         "altyazi_turkce": True}           # altyazi bulununca konusulan dili Turkce yapmayi dene
    global AYAR_HATASI
    if os.path.exists(AYAR_DOSYA):
        try:
            with open(AYAR_DOSYA, encoding="utf-8") as f:
                v.update(json.load(f))
            AYAR_HATASI = None
        except Exception as e:
            # Eskiden sessizce varsayilanlara dusuluyordu: stt_url bos kalip "STT'ye ulasilamiyor" gorunuyordu
            AYAR_HATASI = f"{e}"
    return v


def ayar_yaz(v):
    with open(AYAR_DOSYA, "w", encoding="utf-8") as f:
        json.dump(v, f, ensure_ascii=False, indent=1)


# ====================================================================== is parcaciklari

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
                           kaynak=kaynak, ben=self.ayar.get("ben") or None)
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
                    proxy=self.ayar.get("stt_proxy", True))   # false: Windows proxy'sini atla (kopmalar icin)
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
                    time.sleep(0.6)
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
            self.olay.emit("log", f"Not üretilemedi: {e!r}")
            self.hata.emit(str(e))
            return
        self.bitti.emit(md)


class YuklemeIsi(QtCore.QThread):
    """Diskteki yarim toplantiyi incelemeye hazirlar: once ozetsiz parcalari isler."""
    olay = QtCore.pyqtSignal(str, object)
    durum = QtCore.pyqtSignal(str)
    inceleme_hazir = QtCore.pyqtSignal(list)

    def __init__(self, klasor, sozluk):
        super().__init__()
        self.klasor, self.sozluk, self.motor = klasor, sozluk, None

    def run(self):
        self.motor = Motor.yukle(self.klasor, self.sozluk, olay=lambda t, v: self.olay.emit(t, v))
        self.durum.emit("Eksik parçalar işleniyor…")
        n = self.motor.eksikleri_isle()
        if n:
            self.olay.emit("log", f"{n} parça işlendi.")
        self.inceleme_hazir.emit(self.motor.bitir())


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


# ====================================================================== pencere


# ====================================================================== gorunum

NOT_CSS = ("body{font-family:'Plus Jakarta Sans','Segoe UI',sans-serif;font-size:13px;color:#134E4A} "
           "h1{font-size:20px;margin-bottom:4px} h2{font-size:14px;color:#0D9488;margin-top:18px;margin-bottom:4px} "
           "p,li{line-height:145%} th{background:#f3f4f6;text-align:left;border-bottom:1px solid #d1d5db} "
           "td{border-bottom:1px solid #e5e7eb}")

def md_to_html(md):
    """Notun sinirli Markdown'ini (basliklar, maddeler, tablolar, paragraflar) stilli HTML'e cevirir."""
    import html as _h
    cikti, tablo, liste = [], [], False
    def liste_kapat():
        nonlocal liste
        if liste:
            cikti.append("</ul>")
            liste = False
    def tablo_kapat():
        nonlocal tablo
        if tablo:
            cikti.append("<table cellspacing='0' cellpadding='6' width='100%'>" + "".join(tablo) + "</table>")
            tablo = []
    for ham in md.splitlines():
        s_ = ham.strip()
        if s_.startswith("|"):
            liste_kapat()
            hucreler = [c.strip() for c in s_.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in hucreler):
                continue
            etiket = "th" if not tablo else "td"
            tablo.append("<tr>" + "".join(f"<{etiket}>{_h.escape(c)}</{etiket}>" for c in hucreler) + "</tr>")
            continue
        tablo_kapat()
        if s_.startswith("# "):
            liste_kapat(); cikti.append(f"<h1>{_h.escape(s_[2:])}</h1>")
        elif s_.startswith("## "):
            liste_kapat(); cikti.append(f"<h2>{_h.escape(s_[3:])}</h2>")
        elif s_.startswith(("- ", "* ")):
            if not liste:
                cikti.append("<ul>"); liste = True
            cikti.append(f"<li>{_h.escape(s_[2:])}</li>")
        elif s_:
            liste_kapat(); cikti.append(f"<p>{_h.escape(s_)}</p>")
    liste_kapat(); tablo_kapat()
    return "<html><head><style>" + NOT_CSS + "</style></head><body>" + "".join(cikti) + "</body></html>"


RENKLER = ["#0D9488", "#EA580C", "#7C3AED", "#2563EB", "#DB2777", "#65A30D", "#0891B2", "#B45309"]

QSS = """
* { font-family: 'Plus Jakarta Sans', 'Segoe UI', 'Noto Sans', sans-serif; font-size: 13px; color: #134E4A; }
QMainWindow, QWidget#icerik { background: #F0FDFA; }
QFrame#kenar { background: #134E4A; }
QLabel#marka { color: white; font-size: 17px; font-weight: 700; padding: 18px 16px 4px 16px; }
QLabel#marka_alt { color: #99F6E4; font-size: 11px; padding: 0 16px 14px 16px; }
QListWidget#nav { background: transparent; border: none; outline: 0; }
QListWidget#nav::item { color: #CCFBF1; padding: 11px 16px; border-left: 3px solid transparent; min-height: 20px; }
QListWidget#nav::item:selected { color: white; background: #0F766E; border-left: 3px solid #EA580C; }
QListWidget#nav::item:hover { background: #115E59; }
QLabel#durum_nokta { font-size: 18px; }
QLabel#durum_yazi { color: #CCFBF1; font-size: 12px; }
QFrame#baslik_bar { background: white; border-bottom: 1px solid #CCFBF1; }
QLineEdit#toplanti_adi { font-size: 16px; font-weight: 700; border: none; border-bottom: 2px solid transparent; background: transparent; padding: 2px 0; }
QLineEdit#toplanti_adi:focus { border-bottom: 2px solid #0D9488; }
QLabel#alt_bilgi { color: #475569; font-size: 12px; }
QLabel#sayac { color: #134E4A; font-size: 14px; font-weight: 700; }
QPushButton { background: white; border: 1px solid #99F6E4; border-radius: 6px; padding: 7px 14px; min-height: 18px; }
QPushButton:hover { background: #E8F1F4; }
QPushButton:pressed { background: #CCFBF1; }
QPushButton:focus { border: 2px solid #0D9488; }
QPushButton:disabled { color: #94A3B8; background: #E8F1F4; border-color: #E8F1F4; }
QPushButton#birincil { background: #0D9488; color: white; border: none; font-weight: 700; padding: 9px 22px; font-size: 14px; }
QPushButton#birincil:hover { background: #0F766E; }
QPushButton#birincil:focus { border: 2px solid #134E4A; }
QPushButton#birincil:disabled { background: #99F6E4; color: #F0FDFA; }
QPushButton#vurgu { background: #EA580C; color: white; border: none; font-weight: 700; padding: 9px 22px; font-size: 14px; }
QPushButton#vurgu:hover { background: #C2410C; }
QPushButton#vurgu:disabled { background: #FDBA74; color: white; }
QPushButton#tehlike { background: #DC2626; color: white; border: none; font-weight: 700; padding: 9px 22px; font-size: 14px; }
QPushButton#tehlike:hover { background: #B91C1C; }
QFrame#kart { background: white; border: 1px solid #CCFBF1; border-radius: 8px; }
QLabel#kart_baslik { color: #475569; font-size: 11px; font-weight: 700; letter-spacing: 1px; }
QLabel#kart_deger { font-size: 22px; font-weight: 700; }
QTextBrowser, QPlainTextEdit, QTableWidget, QListWidget#liste, QTextEdit { background: white; border: 1px solid #CCFBF1; border-radius: 8px; }
QTextBrowser:focus, QPlainTextEdit:focus, QTableWidget:focus, QListWidget#liste:focus { border: 1px solid #0D9488; }
QHeaderView::section { background: #E8F1F4; border: none; border-bottom: 1px solid #99F6E4; padding: 7px; font-weight: 700; color: #134E4A; }
QTableWidget { gridline-color: #E8F1F4; selection-background-color: #CCFBF1; selection-color: #134E4A; alternate-background-color: #F8FDFC; }
QLabel#bilgi { background: #E8F1F4; color: #134E4A; border: 1px solid #99F6E4; border-radius: 6px; padding: 8px 10px; }
QLineEdit, QSpinBox, QComboBox { background: white; border: 1px solid #99F6E4; border-radius: 6px; padding: 6px 8px; min-height: 18px; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border: 2px solid #0D9488; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; }
QProgressBar { border: none; background: #CCFBF1; border-radius: 3px; height: 6px; }
QProgressBar::chunk { background: #0D9488; border-radius: 3px; }
QStatusBar { background: white; border-top: 1px solid #CCFBF1; color: #475569; }
"""


IKONLAR = {
    "canli":    '<circle cx="12" cy="12" r="4" fill="{c}"/><circle cx="12" cy="12" r="9" fill="none" stroke="{c}" stroke-width="2"/>',
    "inceleme": '<path d="M4 17.5V20h2.5L17 9.5 14.5 7 4 17.5z" fill="{c}"/><path d="M15.5 6l2.5 2.5 1.5-1.5a1 1 0 000-1.4L18.4 4.5a1 1 0 00-1.4 0L15.5 6z" fill="{c}"/>',
    "not":      '<rect x="5" y="3" width="14" height="18" rx="2" fill="none" stroke="{c}" stroke-width="2"/><path d="M8 8h8M8 12h8M8 16h5" stroke="{c}" stroke-width="2" stroke-linecap="round"/>',
    "sozluk":   '<path d="M4 5a2 2 0 012-2h13v16H6a2 2 0 00-2 2V5z" fill="none" stroke="{c}" stroke-width="2"/><path d="M4 19a2 2 0 012-2h13" fill="none" stroke="{c}" stroke-width="2"/>',
    "gecmis":   '<circle cx="12" cy="12" r="9" fill="none" stroke="{c}" stroke-width="2"/><path d="M12 7v5l3 2" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round"/>',
    "ayarlar":  '<circle cx="12" cy="12" r="3" fill="none" stroke="{c}" stroke-width="2"/>' + "".join(
                f'<rect x="11" y="2" width="2" height="4" rx="1" fill="{{c}}" transform="rotate({a} 12 12)"/>' for a in range(0, 360, 45)),
}


def svg_ikon(ad, renk_normal="#CCFBF1", renk_secili="#FFFFFF"):
    """Kucuk duz SVG ikon; secili durumda beyaz. QtSvg yoksa bos ikon doner (metin yeter)."""
    try:
        from PyQt5 import QtSvg
    except Exception:
        return QtGui.QIcon()
    ikon = QtGui.QIcon()
    for renk, mod in ((renk_normal, QtGui.QIcon.Normal), (renk_secili, QtGui.QIcon.Selected)):
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{IKONLAR[ad].format(c=renk)}</svg>'
        r = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode()))
        pm = QtGui.QPixmap(32, 32)
        pm.fill(QtCore.Qt.transparent)
        pt = QtGui.QPainter(pm)
        r.render(pt)
        pt.end()
        ikon.addPixmap(pm, mod)
    return ikon


def kart(baslik, deger="—"):
    k = QtWidgets.QFrame(objectName="kart")
    v = QtWidgets.QVBoxLayout(k)
    v.setContentsMargins(14, 10, 14, 10)
    b = QtWidgets.QLabel(baslik.upper(), objectName="kart_baslik")
    d = QtWidgets.QLabel(deger, objectName="kart_deger")
    v.addWidget(b)
    v.addWidget(d)
    k.deger = d
    return k


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
        sag.addWidget(QtWidgets.QLabel("Oluşan not (canlı)"))
        self.canli_not = QtWidgets.QListWidget(objectName="liste")
        self.canli_not.setWordWrap(True)
        sag.addWidget(self.canli_not, 2)
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
        self.b_uygula.clicked.connect(self.uygula)
        h.addWidget(b1)
        h.addWidget(b2)
        h.addStretch(1)
        h.addWidget(self.b_uygula)
        v.addLayout(h)
        return w

    def _not(self):
        w, v = self._sayfa()
        self.not_bilgi = QtWidgets.QLabel("Not henüz üretilmedi.", objectName="alt_bilgi")
        v.addWidget(self.not_bilgi)
        self.not_goster = QtWidgets.QTextBrowser()
        self.not_goster.document().setDocumentMargin(16)
        v.addWidget(self.not_goster, 1)
        h = QtWidgets.QHBoxLayout()
        self.b_outlook = QtWidgets.QPushButton("Outlook'ta taslak aç", objectName="birincil")
        self.b_outlook.clicked.connect(self.outlook_ac)
        b2 = QtWidgets.QPushButton("Panoya kopyala")
        b2.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(self.not_md))
        b3 = QtWidgets.QPushButton("Klasörü aç")
        b3.clicked.connect(self.klasor_ac)
        h.addWidget(self.b_outlook)
        h.addWidget(b2)
        h.addWidget(b3)
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
        b4 = QtWidgets.QPushButton("Yarım kalanı tamamla", objectName="birincil")
        b4.clicked.connect(self.gecmis_tamamla)
        b5 = QtWidgets.QPushButton("Sil", objectName="tehlike")
        b5.clicked.connect(self.gecmis_sil)
        b6 = QtWidgets.QPushButton("Boş kayıtları temizle")
        b6.clicked.connect(self.gecmis_bos_temizle)
        for b in (b1, b2, b3, b4):
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
        b = QtWidgets.QPushButton("Kaydet", objectName="birincil")
        b.clicked.connect(self.ayar_kaydet)
        f.addRow(b)
        f.addRow(QtWidgets.QLabel("LLM adresi/model değişikliği uygulama yeniden başlatılınca geçerli olur.", objectName="alt_bilgi"))
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
        if self.motor:
            biten = sum(1 for i in self.motor.isler if i.done())
            self.k_parca.deger.setText(f"{biten} / {self.motor.parca_no}")
            self.k_katilimci.deger.setText(str(len(self.motor.katilimcilar)))

    def _renk(self, ad):
        if ad not in self.renkler:
            self.renkler[ad] = RENKLER[len(self.renkler) % len(RENKLER)]
        return self.renkler[ad]

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
        elif tip == "parca_ozetlendi":
            o = v["ozet"]
            if o:
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

    def inceleme_hazir(self, oneriler, kaynak="canli"):
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
        if not self.motor:
            return
        self.b_uygula.setEnabled(False)
        self.ilerleme.show()
        self._durum_ayarla("Not üretiliyor…", "#EA580C")
        self.not_bilgi.setText("Not üretiliyor — düzeltmeler uygulanıyor, parçalar birleştiriliyor…")
        self.nav.setCurrentRow(2)
        self.tamamla = TamamlamaIsi(self.motor, self.kararlar())
        self.tamamla.olay.connect(self.olay)
        self.tamamla.bitti.connect(self.not_hazir)
        self.tamamla.hata.connect(self.not_hata)
        self.tamamla.start()

    def not_hata(self, mesaj):
        self.ilerleme.hide()
        self.b_uygula.setEnabled(True)
        self._durum_ayarla("Not üretilemedi", "#DC2626")
        self.not_bilgi.setText("Not üretilemedi — LLM erişimini kontrol et, İnceleme sayfasından tekrar dene. Ayrıntı: Olaylar paneli.")
        QtWidgets.QMessageBox.warning(self, "Not üretilemedi", mesaj[-800:])

    def not_hazir(self, md):
        self.not_md = md
        self.not_goster.setHtml(md_to_html(md))
        self.ilerleme.hide()
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
            oz = f"özet: {len(o['kararlar'])} karar, {len(o['aksiyonlar'])} aksiyon" if o else "özet YOK"
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
                                            f"'Yarım kalanı tamamla' ile özet ve incelemeyi başlatabilirsin.\n\n"
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
            self.not_bilgi.setText(f"Kaydedildi: {yol}")
            self.nav.setCurrentRow(2)
        else:
            QtWidgets.QMessageBox.information(self, "Not yok", "Bu toplantının notu henüz üretilmemiş.")

    def gecmis_tamamla(self):
        k = self._secili_klasor()
        if not k:
            return
        self.tablo.setRowCount(0)
        self.canli_not.clear()
        self.ilerleme.show()
        self._durum_ayarla("Yarım kalan toplantı hazırlanıyor…", "#EA580C")
        try:
            with open(os.path.join(k, "meta.json"), encoding="utf-8") as f:
                self.baslik.setText(json.load(f).get("baslik", os.path.basename(k)))
        except Exception:
            self.baslik.setText(os.path.basename(k))
        self.yukle = YuklemeIsi(k, self.sozluk)
        self.yukle.olay.connect(self.olay)
        self.yukle.durum.connect(lambda m: self._durum_ayarla(m, "#EA580C"))
        self.yukle.inceleme_hazir.connect(lambda o: (setattr(self, "motor", self.yukle.motor),
                                                      self.inceleme_hazir(o, kaynak="gecmis")))
        self.yukle.start()

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
                          "altyazi_turkce": self.a_altyazi_tr.isChecked()})
        ayar_yaz(self.ayar)
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

    def closeEvent(self, e):
        if self.is_ and self.is_.isRunning():
            c = QtWidgets.QMessageBox.question(
                self, "BriefMind",
                "Yakalama sürüyor. Uygulama kapatılsın mı?\n\n"
                "Evet: yakalama durur, toplantı kaydı diskte kalır (Geçmiş → Yarım kalanı tamamla).\n"
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
        if AYAR_HATASI:
            QtWidgets.QMessageBox.warning(
                p, "config.json okunamadı",
                "config.json geçerli JSON değil, varsayılan ayarlarla açıldı (STT/LLM adresleri boş olabilir).\n\n"
                f"Hata: {AYAR_HATASI}\n\nSık neden: anahtar tırnaksız (stt_proxy: false yerine \"stt_proxy\": false) "
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
