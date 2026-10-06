"""
analiz_smile.py — Smile çağrı dökümünü (smile2025.xlsx / .csv) sınıflandırıp MVP kapsamını belirleyen
sayıları çıkarır: aylık çağrı, sınıf dağılımı, kural kodu sıklığı ve birikimli kapsama, kodsuz
çağrılarda sık kelimeler ve maskeli örnekler. Rapor Markdown'dır; 6+ haneli sayılar (müşteri no)
maskelenir. Rapor ve CSV veri/ altında kalır, repoya girmez (.gitignore).

python analiz_smile.py veri/smile2025.xlsx [--rapor veri/smile_analiz.md] [--sayfa "Sayfa1"]
       [--tarih-sutun "Açılış Tarihi"] [--metin-sutun Konu Açıklama] [--cevap-sutun Çözüm]
       [--csv veri/smile_siniflar.csv]

Sütun adları verilmezse başlıklardan tahmin edilir (SUTUN_ADAYLARI); rapor hangi sütunların
kullanıldığını yazar.
"""
import argparse
import collections
import csv
import re
import sys
from typing import List, Optional

import katalog as katalog_mod
import siniflandirici
from yardimci import tarih_ayristir, tr_kucuk

SUTUN_ADAYLARI = {
    "tarih": ["tarih", "date", "açılış", "acilis", "oluşturma", "olusturma", "created"],
    "metin": ["açıklama", "aciklama", "konu", "talep", "soru", "metin", "description", "summary",
              "subject", "başlık", "baslik"],
    "cevap": ["cevap", "çözüm", "cozum", "yanıt", "yanit", "answer", "resolution"],
}
DURAK = set(("ve veya ile için icin bir bu şu da de mi mı mu mü ki ne neden nasıl merhaba selamlar iyi "
             "çalışmalar teşekkürler rica ederim ederiz müşteri musteri numaralı numarali müşterinin "
             "musterinin müşterimiz müşterimizin ekte bilgi bilgisi hakkında hakkinda olarak olan var yok "
             "ama fakat ancak daha çok cok en ilgili konu konuda tarafından tarafindan gerekiyor "
             "misiniz mısınız edebilir olduğu olup için").split())
MASKE = re.compile(r"\d{6,}")
KELIME = re.compile(r"[a-zçğıöşü]{4,}")


def sutun_bul(sutunlar, anahtar: str) -> Optional[str]:
    for s in sutunlar:
        sk = tr_kucuk(str(s))
        for aday in SUTUN_ADAYLARI[anahtar]:
            if aday in sk:
                return s
    return None


def yukle(yol: str, sayfa=None):
    import pandas as pd
    if yol.lower().endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(yol, sheet_name=sayfa or 0)
    return pd.read_csv(yol, sep=None, engine="python", encoding="utf-8-sig")


def maskele(metin: str) -> str:
    return MASKE.sub("######", metin)


def _hucre(satir, sutun) -> str:
    try:
        deger = satir[sutun]
    except (KeyError, IndexError):
        return ""
    s = "" if deger is None else str(deger).strip()
    return "" if s in ("nan", "NaN", "None", "NaT") else s


def analiz(df, tarih_sutun=None, metin_sutunlar: Optional[List[str]] = None, cevap_sutun=None, katalog=None) -> dict:
    sutunlar = list(df.columns)
    cevap_sutun = cevap_sutun or sutun_bul(sutunlar, "cevap")
    tarih_sutun = tarih_sutun or sutun_bul(sutunlar, "tarih")
    if not metin_sutunlar:
        metin_sutunlar = [s for s in sutunlar if s != cevap_sutun and sutun_bul([s], "metin")]
    if not metin_sutunlar:
        raise ValueError("metin sütunu bulunamadı; --metin-sutun verin. Sütunlar: " + ", ".join(map(str, sutunlar)))
    katalog = katalog or katalog_mod.Katalog.yukle()

    aylik, siniflar, kodlar, kelimeler = (collections.Counter() for _ in range(4))
    kodlu, cevapli = 0, 0
    kodsuz_ornekler, satirlar = [], []
    for _, satir in df.iterrows():
        metin = " ".join(h for h in (_hucre(satir, s) for s in metin_sutunlar) if h)
        s = siniflandirici.siniflandir(metin, katalog)
        tarih = tarih_ayristir(_hucre(satir, tarih_sutun)) if tarih_sutun else None
        ay = tarih.strftime("%Y-%m") if tarih else "bilinmiyor"
        aylik[ay] += 1
        siniflar[s.sinif] += 1
        if cevap_sutun and _hucre(satir, cevap_sutun):
            cevapli += 1
        if s.kural_kodlari:
            kodlu += 1
            for k in s.kural_kodlari:
                kodlar[k] += 1
        else:
            for kelime in KELIME.findall(tr_kucuk(metin)):
                if kelime not in DURAK:
                    kelimeler[kelime] += 1
            if len(kodsuz_ornekler) < 40 and metin:
                kodsuz_ornekler.append(maskele(metin[:160]).replace("\n", " "))
        satirlar.append({"ay": ay, "sinif": s.sinif, "kodlar": " ".join(s.kural_kodlari), "cif_sayisi": len(s.cifler)})

    toplam = len(satirlar)
    birikimli, top = [], 0
    for kod, n in kodlar.most_common():
        top += n
        birikimli.append((kod, n, 100.0 * n / toplam if toplam else 0.0, 100.0 * top / toplam if toplam else 0.0))
    return {"toplam": toplam, "sutunlar": {"tarih": tarih_sutun, "metin": metin_sutunlar, "cevap": cevap_sutun},
            "aylik": dict(sorted(aylik.items())), "siniflar": dict(siniflar), "kodlar": birikimli,
            "kodlu": kodlu, "cevapli": cevapli, "kelimeler": kelimeler.most_common(30),
            "kodsuz_ornekler": kodsuz_ornekler, "satirlar": satirlar}


def rapor(sonuc: dict) -> str:
    toplam = max(1, sonuc["toplam"])
    s = ["# Smile çağrı analizi", "",
         "- Toplam çağrı: {}".format(sonuc["toplam"]),
         "- Kural kodu ya da bilinen konu bulunan çağrı: {} (%{:.0f})".format(sonuc["kodlu"], 100.0 * sonuc["kodlu"] / toplam),
         "- Cevap metni dolu çağrı: {}".format(sonuc["cevapli"]),
         "- Kullanılan sütunlar: tarih={tarih}, metin={metin}, cevap={cevap}".format(**sonuc["sutunlar"]),
         "", "## Aylık çağrı", "", "| Ay | Çağrı |", "|---|---|"]
    s += ["| {} | {} |".format(ay, n) for ay, n in sonuc["aylik"].items()]
    s += ["", "## Sınıf dağılımı (anahtar kelime tahmini)", "", "| Sınıf | Çağrı | Pay % |", "|---|---|---|"]
    s += ["| {} | {} | {:.0f} |".format(k, n, 100.0 * n / toplam)
          for k, n in sorted(sonuc["siniflar"].items(), key=lambda x: -x[1])]
    s += ["", "## Kural kodu sıklığı ve birikimli kapsama", "", "| Kod | Çağrı | Pay % | Birikimli % |", "|---|---|---|---|"]
    s += ["| {} | {} | {:.1f} | {:.1f} |".format(kod, n, pay, bir) for kod, n, pay, bir in sonuc["kodlar"]]
    s += ["", "## Kodsuz çağrılarda sık kelimeler", "",
          ", ".join("{} ({})".format(k, n) for k, n in sonuc["kelimeler"]) or "-",
          "", "## Kodsuz çağrı örnekleri (maskeli, en fazla 40)", ""]
    s += ["- " + o for o in sonuc["kodsuz_ornekler"]] or ["-"]
    return "\n".join(s) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Smile çağrı dökümü analizi (MVP kapsamı için).")
    p.add_argument("dosya", help="smile2025.xlsx ya da .csv")
    p.add_argument("--rapor", help="Markdown rapor yolu (verilmezse ekrana yazar)")
    p.add_argument("--sayfa", help="Excel sayfa adı")
    p.add_argument("--tarih-sutun")
    p.add_argument("--metin-sutun", nargs="+")
    p.add_argument("--cevap-sutun")
    p.add_argument("--csv", help="çağrı başına sınıf/kod CSV'si (workshop için)")
    a = p.parse_args(argv)
    sonuc = analiz(yukle(a.dosya, a.sayfa), a.tarih_sutun, a.metin_sutun, a.cevap_sutun)
    metin = rapor(sonuc)
    if a.rapor:
        with open(a.rapor, "w", encoding="utf-8") as f:
            f.write(metin)
        print("rapor yazıldı: " + a.rapor)
    else:
        print(metin)
    if a.csv:
        with open(a.csv, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["ay", "sinif", "kodlar", "cif_sayisi"])
            w.writeheader()
            w.writerows(sonuc["satirlar"])
        print("csv yazıldı: " + a.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
