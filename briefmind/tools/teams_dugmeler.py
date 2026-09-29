"""
teams_dugmeler.py — Teams pencerelerindeki dugme/menu adlarini listeler (altyazi otomasyonu icin teshis).
Toplanti acikken calistir:  python teams_dugmeler.py
Cikti: teams_dugmeler.txt  (bunu paylas)
"""
import ctypes
import sys

import uiautomation as auto

ctypes.windll.user32.SystemParametersInfoW(0x0047, 1, None, 0)   # ekran okuyucu bayragi
TIPLER = ("ButtonControl", "MenuItemControl", "MenuControl", "TabItemControl", "SplitButtonControl")

def main():
    bekle = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    if bekle:
        import time
        print(f"{bekle} sn icinde Teams'te 'Tumu' -> 'Dil ve konusma' menusunu ac ve acik birak...")
        for i in range(bekle, 0, -1):
            print(f"  {i}", end=" ", flush=True)
            time.sleep(1)
        print()
    satirlar = []
    for w in auto.GetRootControl().GetChildren():
        ad = w.Name or ""
        if not (w.ClassName == "TeamsWebView" or ad.lower().endswith("microsoft teams")):
            continue
        satirlar.append(f"\n===== PENCERE: {ad!r}  (sinif={w.ClassName}) =====")
        n = 0
        for c, derinlik in auto.WalkControl(w, includeTop=False, maxDepth=60):
            if c.ControlTypeName in TIPLER and (c.Name or "").strip():
                satirlar.append(f"  d{derinlik:02d} {c.ControlTypeName:18} {c.Name!r}"
                                + (f"  [auto={c.AutomationId}]" if c.AutomationId else ""))
                n += 1
                if n >= 400:
                    satirlar.append("  ... (400 ile kesildi)")
                    break
    metin = "\n".join(satirlar) or "Teams penceresi bulunamadi."
    with open("teams_dugmeler.txt", "w", encoding="utf-8") as f:
        f.write(metin)
    print(metin[:6000])
    print("\n-> teams_dugmeler.txt yazildi")

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    main()
