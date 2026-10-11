"""
terim_geri_al.py — terim havuzunda ogrenilen varyantlari / genel yukseltmeyi geri alir, gecmisi listeler.

Geri alinan (eski -> terim) bir daha ogrenilmez. Yalniz ogrenmeyle girmis terim havuzdan silinir.

Kullanim (proje kokunden):
  python tools/terim_geri_al.py --liste [--son 20]      # gecmis (ekle / geri_al kayitlari, sayaclariyla)
  python tools/terim_geri_al.py "Terim Adi"             # o terimin ogrenmelerini geri al
  --yol terimler/terimler.json                          # baska havuz dosyasi
"""
import argparse
import os
import sys

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KOK)
import terim  # noqa: E402


def liste(havuz, son=20):
    """Gecmisin son kayitlarini okunur satirlar olarak dondurur."""
    satirlar = []
    for g in havuz.gecmis[-son:]:
        ek = []
        if g.get("eski"):
            ek.append(f"eski={g['eski']}")
        if g.get("sayac"):
            ek.append("sayac=" + ",".join(f"{k}:{v}" for k, v in g["sayac"].items()))
        if g.get("tur"):
            ek.append(g["tur"])
        satirlar.append(f"{g.get('t', '')}  {g.get('islem', '')}  {g.get('terim', '')}  ({g.get('neden', '')})"
                        + ("  " + " ".join(ek) if ek else ""))
    return satirlar


def main(argv=None):
    ap = argparse.ArgumentParser(description="Terim ogrenmesini geri al / gecmisi listele")
    ap.add_argument("terim", nargs="?", help="geri alinacak terim")
    ap.add_argument("--yol", default=None, help="varsayilan: <uygulama klasoru>/terimler/terimler.json")
    ap.add_argument("--liste", action="store_true", help="gecmisi listele")
    ap.add_argument("--son", type=int, default=20)
    a = ap.parse_args(argv)
    havuz = terim.TerimHavuzu(a.yol)
    if a.liste or not a.terim:
        for s in liste(havuz, a.son) or ["(gecmis bos)"]:
            print(s)
        return 0
    r = havuz.geri_al(a.terim)
    if r is None:
        print("Terim havuzda yok:", a.terim)
        return 1
    if not (r["varyantlar"] or r["genel_geri"] or r["silindi"]):
        print(f"{r['terim']}: geri alinacak ogrenme yok (eslesen gozlemler engellendi).")
        return 0
    print(f"{r['terim']}: geri alindi" + (f", varyantlar: {'; '.join(r['varyantlar'])}" if r["varyantlar"] else "")
          + (", genel kaldirildi" if r["genel_geri"] else "") + (", terim silindi" if r["silindi"] else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    sys.exit(main())
