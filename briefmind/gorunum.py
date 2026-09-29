"""
gorunum.py — arayuzun gorunumu: QSS temasi, not HTML'i (md_to_html), ikonlar, kart.
"""
from PyQt5 import QtCore, QtGui, QtWidgets

NOT_CSS = ("body{font-family:'Plus Jakarta Sans','Segoe UI',sans-serif;font-size:13px;color:#134E4A} "
           "h1{font-size:20px;margin-bottom:4px} h2{font-size:14px;color:#0D9488;margin-top:18px;margin-bottom:4px} "
           "p,li{line-height:145%} th{background:#f3f4f6;text-align:left;border-bottom:1px solid #d1d5db} "
           "td{border-bottom:1px solid #e5e7eb}")


def md_to_html(md):
    """Notun sinirli Markdown'ini (basliklar, maddeler, tablolar, paragraflar) stilli HTML'e cevirir.
    '⏱10:12:03' kaynak isaretleri tiklanabilir baglanti olur (transkriptte o ani acar)."""
    import html as _h
    import re as _re

    def kac(x):
        return _re.sub(r"⏱(\d{1,2}:\d{2}:\d{2})", r'<a href="ts:\1">⏱\1</a>', _h.escape(x))
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
            tablo.append("<tr>" + "".join(f"<{etiket}>{kac(c)}</{etiket}>" for c in hucreler) + "</tr>")
            continue
        tablo_kapat()
        if s_.startswith("# "):
            liste_kapat(); cikti.append(f"<h1>{kac(s_[2:])}</h1>")
        elif s_.startswith("## "):
            liste_kapat(); cikti.append(f"<h2>{kac(s_[3:])}</h2>")
        elif s_.startswith(("- ", "* ")):
            if not liste:
                cikti.append("<ul>"); liste = True
            cikti.append(f"<li>{kac(s_[2:])}</li>")
        elif s_:
            liste_kapat(); cikti.append(f"<p>{kac(s_)}</p>")
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
QFrame#serit { background: white; border: 1px solid #99F6E4; border-radius: 6px; }
QLabel#canli_sayac { color: #0F766E; font-weight: 700; }
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
