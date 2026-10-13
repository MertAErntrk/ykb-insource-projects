"""
altyazi_izle.py — Teams altyazi panelini UIA ile izler (Windows alan araci). AD VE METIN ASLA YAZILMAZ.

Her okumada bir satir: saat, panel bulundu mu, oge kimligi (GetRuntimeId ozeti), rect, satir/ad/'?' sayisi, kelime sayisi,
degisti mi (ve kac sn'dir ayni), Teams penceresi (simge / buyuk / onplan), okuma ms. Oge degisince '*** panel öğesi
değişti', kaybolup gelince '*** panel yeniden bulundu'.

Kullanim (briefmind klasorunden, Teams toplantisinda altyazi acikken):
  python tools\\altyazi_izle.py --dosya izle.txt
  python tools\\altyazi_izle.py --sn 0.6 --sure 600 --katilimci "Ad Soyad;Ad Soyad"
Senaryo (her adimda ~30 sn): altyazi altta -> Teams buyutulmus -> arka planda -> simge durumunda -> altyazi kapat/ac ->
ayri pencere. Cikti yapistirilabilir (Ctrl+C ile bitirince ozet yazilir).
"""
import argparse
import ctypes
import datetime as dt
import hashlib
import os
import sys
import time

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if KOK not in sys.path:
    sys.path.insert(0, KOK)

try:                                      # Windows disinda yok: arac acik bir mesajla cikar
    import uiautomation as auto
except Exception:
    auto = None

DOGRULA_SN = 10.0                         # onbellekteki oge ile taze arama karsilastirmasi (0: kapali)


def windows_mu():
    return sys.platform == "win32" and auto is not None and hasattr(ctypes, "windll")


# ---------------------------------------------------------------- Windows yardimcilari (hepsi try/except)

def _user32():
    try:
        return ctypes.windll.user32
    except Exception:
        return None


def pencere_durumu(hwnd):
    """-> {"simge": bool|None, "buyuk": bool|None, "onplan": bool|None}"""
    d = {"simge": None, "buyuk": None, "onplan": None}
    u = _user32()
    if not hwnd or u is None:
        return d
    for ad, fn in (("simge", lambda: bool(u.IsIconic(hwnd))), ("buyuk", lambda: bool(u.IsZoomed(hwnd))),
                   ("onplan", lambda: _onplanda(u, hwnd))):
        try:
            d[ad] = fn()
        except Exception:
            pass
    return d


def _onplanda(u, hwnd):
    on = u.GetForegroundWindow()
    if on == hwnd:
        return True
    try:
        return bool(on) and u.GetAncestor(on, 2) == hwnd         # GA_ROOT
    except Exception:
        return False


def _hwnd_cikar(pencere):
    """_grup_bul_ayrintili'nin pencere ciktisi: hwnd (int) | {'hwnd':..} | UIA penceresi | None"""
    try:
        if isinstance(pencere, int):
            return pencere
        if isinstance(pencere, dict):
            return pencere.get("hwnd")
        return getattr(pencere, "NativeWindowHandle", None)
    except Exception:
        return None


def _teams_hwnd():
    try:
        for w in auto.GetRootControl().GetChildren():
            if "teams" in (w.Name or "").lower():
                return w.NativeWindowHandle
    except Exception:
        pass
    return None


def panel_bul():
    """-> (grup, hwnd). yakalayici._grup_bul_ayrintili varsa o (pencere ile), yoksa _grup_bul."""
    import yakalayici
    ayrintili = getattr(yakalayici, "_grup_bul_ayrintili", None)
    if ayrintili is not None:
        grup, pencere, _baslik = ayrintili()
        return grup, (_hwnd_cikar(pencere) or (_teams_hwnd() if grup is not None else None))
    grup, _baslik = yakalayici._grup_bul()
    return grup, (_teams_hwnd() if grup is not None else None)


def oge_kimligi(grup):
    """GetRuntimeId ozeti (6 hex) | None"""
    try:
        f = getattr(grup, "GetRuntimeId", None)
        rid = f() if f else grup.Element.GetRuntimeId()
        return hashlib.md5(repr(tuple(rid)).encode("utf-8")).hexdigest()[:6]
    except Exception:
        return None


def oge_rect(grup):
    try:
        r = grup.BoundingRectangle
        return (r.left, r.top, r.right, r.bottom)
    except Exception:
        return None


# ---------------------------------------------------------------- bicim (saf; testlenir)

def _pencere_metni(p):
    if not p or all(v is None for v in p.values()):
        return "?"
    parca = []
    if p.get("simge"):
        parca.append("simge")
    if p.get("buyuk"):
        parca.append("büyük")
    parca.append("önplan" if p.get("onplan") else "arka")
    return "/".join(parca)


def satir_bicimle(zaman, bulundu, kimlik=None, rect=None, satir=0, ad=0, soru=0, kelime=0, degisti=None, ayni_sn=0.0,
                  pencere=None, ms=None):
    """Tek okuma satiri. Ad ve metin ALINMAZ: yalniz sayilar."""
    if not bulundu:
        return f"{zaman} | panel=YOK | pencere={_pencere_metni(pencere)}" + (f" | {ms:.0f} ms" if ms is not None else "")
    rc = "?" if not rect else f"{rect[0]},{rect[1]} {rect[2] - rect[0]}x{rect[3] - rect[1]}"
    dg = "?" if degisti is None else ("değişti" if degisti else f"ayni={ayni_sn:.0f}sn")
    return (f"{zaman} | panel=var | id={kimlik or '?'} | rect={rc} | satir={satir} ad={ad} ?={soru} | kelime={kelime} | "
            f"{dg} | pencere={_pencere_metni(pencere)}" + (f" | {ms:.0f} ms" if ms is not None else ""))


class Cikti:
    def __init__(self, yol=None):
        self.satirlar = []
        self._f = open(yol, "w", encoding="utf-8") if yol else None

    def yaz(self, s):
        self.satirlar.append(s)
        print(s, flush=True)
        if self._f:
            self._f.write(s + "\n")
            self._f.flush()

    def kapat(self):
        if self._f:
            self._f.close()


# ---------------------------------------------------------------- izleme dongusu

def izle(cikti, sn=0.6, sure=600.0, katilimcilar=(), dogrula_sn=DOGRULA_SN):
    import yakalayici
    y = yakalayici.Yakalayici(katilimcilar=katilimcilar)
    grup, hwnd, kimlik = None, None, None
    kayip = False
    imza_onceki, degisim_an = None, time.time()
    istat = {"okuma": 0, "yok": 0, "hata": 0, "degisti": 0, "oge_degisti": 0, "yeniden": 0, "ms": [], "en_uzun_ayni": 0.0}
    son_dogrula = time.time()
    bitis = time.time() + sure
    try:
        while time.time() < bitis:
            t0 = time.perf_counter()
            an = dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            if grup is None:
                try:
                    grup, hwnd = panel_bul()
                except Exception:
                    grup, hwnd = None, None
                if grup is None:
                    istat["yok"] += 1
                    kayip = True
                    cikti.yaz(satir_bicimle(an, False, pencere=pencere_durumu(_teams_hwnd()),
                                            ms=1000 * (time.perf_counter() - t0)))
                    time.sleep(sn)
                    continue
                kimlik = oge_kimligi(grup)
                if kayip:
                    istat["yeniden"] += 1
                    cikti.yaz("*** panel yeniden bulundu")
                    kayip = False
                son_dogrula = time.time()
            elif dogrula_sn and time.time() - son_dogrula >= dogrula_sn:
                son_dogrula = time.time()
                try:
                    taze, hwnd2 = panel_bul()
                except Exception:
                    taze, hwnd2 = None, None
                taze_kimlik = oge_kimligi(taze) if taze is not None else None
                if taze is None:
                    cikti.yaz("*** panel öğesi ağaçta bulunamadı (önbellekteki öğe eskimiş olabilir)")
                elif taze_kimlik != kimlik:
                    istat["oge_degisti"] += 1
                    cikti.yaz(f"*** panel öğesi değişti ({kimlik or '?'} → {taze_kimlik or '?'})")
                    grup, hwnd, kimlik = taze, hwnd2 or hwnd, taze_kimlik
            try:
                y.grup = grup
                ciftler = y._ciftler()
                etiketli = y._etiketle(ciftler)
            except Exception as e:
                istat["hata"] += 1
                cikti.yaz(f"{an} | okuma HATASI ({type(e).__name__}): panel kaybedildi, yeniden aranacak")
                grup, kayip = None, True
                time.sleep(sn)
                continue
            ms = 1000 * (time.perf_counter() - t0)
            imza = hash(tuple((k, n) for k, n in ciftler))
            degisti = imza != imza_onceki
            if degisti:
                istat["degisti"] += 1
                degisim_an = time.time()
            imza_onceki = imza
            ayni = time.time() - degisim_an
            istat["en_uzun_ayni"] = max(istat["en_uzun_ayni"], ayni)
            istat["okuma"] += 1
            istat["ms"].append(ms)
            yeni_kimlik = oge_kimligi(grup)
            if kimlik and yeni_kimlik != kimlik:
                istat["oge_degisti"] += 1
                cikti.yaz(f"*** panel öğesi değişti ({kimlik} → {yeni_kimlik or 'okunamıyor'})")
                kimlik = yeni_kimlik
            cikti.yaz(satir_bicimle(
                an, True, kimlik=kimlik, rect=oge_rect(grup), satir=len(ciftler), ad=len({k for k, _ in ciftler}),
                soru=sum(1 for k, _ in etiketli if k == "?"), kelime=sum(len(n.split()) for _, n in ciftler),
                degisti=degisti, ayni_sn=ayni, pencere=pencere_durumu(hwnd), ms=ms))
            time.sleep(max(0.0, sn - (time.perf_counter() - t0)))
    except KeyboardInterrupt:
        pass
    ms = sorted(istat["ms"])
    cikti.yaz("")
    cikti.yaz(f"ÖZET: okuma {istat['okuma']} | panel yok {istat['yok']} | hata {istat['hata']} | değişen okuma "
              f"{istat['degisti']} | öğe değişimi {istat['oge_degisti']} | yeniden bulunma {istat['yeniden']} | "
              f"en uzun aynı kalma {istat['en_uzun_ayni']:.0f} sn | okuma ms p95 "
              f"{ms[min(len(ms) - 1, int(0.95 * len(ms)))]:.0f}" if ms else "ÖZET: okuma yok")


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Teams altyazı panelini izler (ad ve metin yazılmaz)")
    ap.add_argument("--sn", type=float, default=0.6, help="okumalar arası bekleme (varsayılan 0.6)")
    ap.add_argument("--sure", type=float, default=600.0, help="toplam izleme süresi, sn (varsayılan 600)")
    ap.add_argument("--dosya", help="çıktıyı bu dosyaya da yaz")
    ap.add_argument("--katilimci", default="", help="bilinen adlar, ';' ile ayrılmış (ad tanıma için; çıktıya yazılmaz)")
    ap.add_argument("--dogrula-sn", type=float, default=DOGRULA_SN, dest="dogrula_sn",
                    help="önbellekteki panel öğesini ağaçtan taze aramayla karşılaştırma aralığı (0: kapalı)")
    a = ap.parse_args(argv)
    if not windows_mu():
        print("Bu araç yalnızca Windows'ta ve 'uiautomation' paketi kuruluyken çalışır (Teams altyazı panelini UI "
              "Automation ile okur). Bu makinede çalıştırılamadı.", file=sys.stderr)
        return 2
    cikti = Cikti(a.dosya)
    try:
        cikti.yaz(f"altyazi_izle — {dt.datetime.now():%Y-%m-%d %H:%M} | aralık {a.sn} sn | süre {a.sure:.0f} sn "
                  "(ad ve metin yazılmaz; Ctrl+C ile bitir)")
        izle(cikti, sn=a.sn, sure=a.sure, katilimcilar=[x.strip() for x in a.katilimci.split(";") if x.strip()],
             dogrula_sn=a.dogrula_sn)
    finally:
        cikti.kapat()
    return 0


if __name__ == "__main__":
    sys.exit(main())
