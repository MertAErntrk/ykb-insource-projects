"""
json_yapi.py — bir JSON dosyasının DEĞERSİZ anahtar ağacını yazar (anahtar: tür). Gerçek log
kaydının yapısını değer sızdırmadan paylaşmak içindir.

python json_yapi.py kayit.json                      # ağaç ekrana
python json_yapi.py kayitlar.jsonl --cikti agac.txt # satır başına JSON: ilk kayıt

Python 3.9+, yalnız standart kütüphane.
"""
import argparse
import json
import os
import sys
from typing import Any, Dict


def _tur(deger: Any) -> str:
    if deger is None:
        return "null"
    if isinstance(deger, bool):
        return "bool"
    if isinstance(deger, int):
        return "int"
    if isinstance(deger, float):
        return "float"
    if isinstance(deger, str):
        return "str"
    if isinstance(deger, list):
        return "list[{}]".format(len(deger))
    return "dict"


def _dugum(deger: Any, derinlik: int) -> Any:
    if isinstance(deger, dict):
        return agac(deger, derinlik - 1) if derinlik > 0 else "..."
    if isinstance(deger, list):
        if derinlik <= 0:
            return "..."
        d = {"tur": _tur(deger)}
        if deger:
            d["eleman"] = _dugum(deger[0], derinlik - 1)
        return d
    return _tur(deger)


def agac(obj: Any, derinlik: int = 8) -> Dict[str, Any]:
    """Anahtar -> tür adı; dict iç ağaç, list {"tur": "list[N]", "eleman": ilk elemanın ağacı}. Değer yazılmaz."""
    if isinstance(obj, dict):
        return {str(k): _dugum(v, derinlik) for k, v in obj.items()}
    return {"tur": _tur(obj), "eleman": _dugum(obj[0], derinlik - 1)} if isinstance(obj, list) and obj \
        else {"tur": _tur(obj)}


def metin(agac_: Dict[str, Any], girinti: int = 2) -> str:
    satirlar = []

    def yaz(d: Dict[str, Any], seviye: int) -> None:
        bosluk = " " * (girinti * seviye)
        for k, v in d.items():
            if isinstance(v, dict):
                if "tur" in v and set(v) <= {"tur", "eleman"} and str(v["tur"]).startswith("list["):
                    satirlar.append("{}{}: {}".format(bosluk, k, v["tur"]))
                    if "eleman" in v:
                        e = v["eleman"]
                        yaz(e if isinstance(e, dict) and not _liste_mi(e) else {"[]": e}, seviye + 1)
                else:
                    satirlar.append("{}{}:".format(bosluk, k))
                    yaz(v, seviye + 1)
            else:
                satirlar.append("{}{}: {}".format(bosluk, k, v))

    yaz(agac_, 0)
    return "\n".join(satirlar) + "\n"


def _liste_mi(d: Dict[str, Any]) -> bool:
    return set(d) <= {"tur", "eleman"} and str(d.get("tur", "")).startswith("list[")


def oku(yol: str) -> Any:
    with open(yol, encoding="utf-8") as f:
        icerik = f.read()
    if yol.lower().endswith(".jsonl"):
        for satir in icerik.splitlines():
            if satir.strip():
                return json.loads(satir)
        raise ValueError("boş jsonl")
    return json.loads(icerik)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="JSON anahtar ağacı (değersiz).")
    p.add_argument("dosya", help=".json ya da .jsonl (ilk kayıt)")
    p.add_argument("--derinlik", type=int, default=8)
    p.add_argument("--cikti", help="çıktı dosyası (verilmezse yalnız ekrana)")
    a = p.parse_args(argv)
    if not os.path.isfile(a.dosya):
        print("dosya bulunamadı: " + a.dosya, file=sys.stderr)
        return 2
    try:
        cikti = metin(agac(oku(a.dosya), a.derinlik))
    except ValueError as e:
        print("JSON okunamadı: {}".format(e), file=sys.stderr)
        return 2
    if a.cikti:
        with open(a.cikti, "w", encoding="utf-8") as f:
            f.write(cikti)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    print(cikti)
    return 0


if __name__ == "__main__":
    sys.exit(main())
