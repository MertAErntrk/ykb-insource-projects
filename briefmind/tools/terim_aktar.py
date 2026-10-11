"""
terim_aktar.py — eski sozluk.json'u terim havuzuna (terimler/terimler.json) tasir.

Yalniz terimler[] tasinir; aliaslar (yanlis -> dogru) ASLA tasinmaz: alias metne kor degisiklik demekti,
yeni mimaride metni yalniz duzelt.py kapisi degistirir. Cumle boyundaki girdiler (terim_gibi_mi degil) atlanir.
etiketler (terim -> seri listesi) varsa terim o serilere baglanir; etiketsiz terim genel olur.
Tasimadan once kaynak sozluk.json ve (varsa) hedef terimler.json yedeklenir (<dosya>.yedek-<tarih>).

Kullanim (proje kokunden):  python tools/terim_aktar.py [sozluk.json] [--hedef terimler/terimler.json] [--kuru]
"""
import argparse
import datetime as dt
import json
import os
import shutil
import sys

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KOK)
import terim  # noqa: E402
from kelime import normalize  # noqa: E402


def _yedekle(yol):
    yedek = f"{yol}.yedek-{dt.datetime.now():%Y%m%d-%H%M%S}"
    shutil.copy(yol, yedek)
    return yedek


def aktar(sozluk_yolu, hedef_yolu=None, yedekle=True, kuru=False):
    """Dondurur {"eklenen": [...], "atilan": [...], "yedekler": [...], "hedef": yol}.
    Ayni terim ikinci kez tasinirsa havuzda tekrarlanmaz (tekrar calistirmak guvenlidir)."""
    with open(sozluk_yolu, encoding="utf-8") as f:
        eski = json.load(f)
    terimler = eski.get("terimler") if isinstance(eski, dict) else None
    if not isinstance(terimler, list):
        raise ValueError("sozluk.json'da terimler[] yok")
    etiketler = {normalize(k): [s for s in v if s] for k, v in (eski.get("etiketler") or {}).items()
                 if isinstance(v, list)}
    havuz = terim.TerimHavuzu(hedef_yolu)
    yedekler = []
    if not kuru and yedekle:
        yedekler.append(_yedekle(sozluk_yolu))
        if os.path.exists(havuz.yol):
            yedekler.append(_yedekle(havuz.yol))
    eklenen, atilan = [], []
    for t in terimler:
        t = str(t).strip()
        if not terim._gecerli(t):
            atilan.append(t)
            continue
        seriler = etiketler.get(normalize(t)) or []
        if seriler:
            yeniler = []
            for s in seriler:
                var = havuz._bul(t)
                if var is not None and s in var["seriler"]:
                    continue                 # onceki aktarimda islendi: sayac sismesin
                yeniler += havuz.ekle(s, [t], "aktar")
            if yeniler:
                eklenen.append(t)
        else:
            if havuz.ekle(None, [t], "aktar"):
                eklenen.append(t)
            e = havuz._bul(t)
            if e is not None and not e["seriler"]:
                e["genel"] = True            # etiketsiz terim eskiden her toplantida gecerliydi
    if not kuru:
        havuz.kaydet(zorla=True)
    return {"eklenen": eklenen, "atilan": atilan, "yedekler": yedekler, "hedef": havuz.yol}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Eski sozluk.json -> terimler/terimler.json (yalniz terimler[])")
    ap.add_argument("sozluk", nargs="?", default="sozluk.json")
    ap.add_argument("--hedef", default=None, help="varsayilan: <uygulama klasoru>/terimler/terimler.json")
    ap.add_argument("--kuru", action="store_true", help="yazmadan ne olacagini goster")
    a = ap.parse_args(argv)
    if not os.path.exists(a.sozluk):
        print("sozluk.json bulunamadi:", a.sozluk)
        return 1
    try:
        r = aktar(a.sozluk, a.hedef, kuru=a.kuru)
    except (ValueError, OSError) as e:
        print("Aktarilamadi:", e)
        return 1
    print(f"{len(r['eklenen'])} terim {'eklenecek' if a.kuru else 'eklendi'}, {len(r['atilan'])} girdi atlandi "
          f"(cumle/gecersiz). Hedef: {r['hedef']}")
    for y in r["yedekler"]:
        print("  yedek:", y)
    for t in r["atilan"][:20]:
        print("  - atlandi:", t[:70])
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    sys.exit(main())
