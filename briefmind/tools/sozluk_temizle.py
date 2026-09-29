"""
sozluk_temizle.py — sozluk.json'daki cumle boyundaki 'terim'leri temizler.

Neden: Inceleme'de onaylanan duzeltmeler tam cumle olarak terimlere girmisti; bunlar LLM'e
"sozluk" diye gidip baska toplantilarin notuna konu olarak siziyordu. Aliaslar (yanlis -> dogru)
korunur, sadece terim listesi temizlenir.

Kullanim (proje kokunden):  python tools\\sozluk_temizle.py sozluk.json
                            python tools\\sozluk_temizle.py dist\\BriefMind\\sozluk.json
Yedek:     sozluk.json.yedek-<tarih>
"""
import datetime as dt
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sozluk import normalize, terim_gibi_mi  # noqa: E402


def temizle(yol):
    with open(yol, encoding="utf-8") as f:
        v = json.load(f)
    terimler = v.get("terimler", [])
    etiketler = v.get("etiketler", {})
    tutulan, atilan, gorulen = [], [], set()
    for t in terimler:
        t2 = t.strip()
        # "ODS'de", "Cemil'le" gibi ek almis ozel adlar -> kok ("ODS", "Cemil")
        if "'" in t2 and " " not in t2:
            t2 = t2.split("'")[0]
        if not terim_gibi_mi(t2):
            atilan.append(t)
            continue
        n = normalize(t2)
        if n in gorulen:
            atilan.append(t)
            continue
        gorulen.add(n)
        tutulan.append(t2)
        if t2 != t and normalize(t) in etiketler:
            etiketler[n] = etiketler.pop(normalize(t))
    if not atilan:
        print("temizlenecek bir sey yok:", yol)
        return
    yedek = f"{yol}.yedek-{dt.datetime.now():%Y%m%d-%H%M}"
    shutil.copy(yol, yedek)
    v["terimler"] = tutulan
    v["etiketler"] = {k: e for k, e in etiketler.items() if k in gorulen and e}
    with open(yol, "w", encoding="utf-8") as f:
        json.dump(v, f, ensure_ascii=False, indent=1)
    print(f"{yol}: {len(terimler)} -> {len(tutulan)} terim, {len(atilan)} girdi atildi (yedek: {yedek})")
    for a in atilan:
        print("  -", a[:90])
    print("\nKalan terimler:", "; ".join(tutulan))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    temizle(sys.argv[1] if len(sys.argv) > 1 else "sozluk.json")
