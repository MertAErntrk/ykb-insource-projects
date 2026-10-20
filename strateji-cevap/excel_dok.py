"""
excel_dok.py — Excel dosyasının yapısını (ve istenirse hücrelerini) dışarı verilebilir bir metin
dökümü olarak yazar. Dökümde müşteri verisi olmasın diye 6+ haneli sayı dizileri maskelenir ve
serbest metin sütunlarının içeriği yazılmaz (yalnız doluluk ve uzunluk sayıları).

python excel_dok.py smile2025.xlsx                                   # yapı: sayfalar, başlık, tip, dağılım
python excel_dok.py "Kurumsal ve Ticari Kredi Onay Yetki Seviyeleri.xlsx" --hucreler
                                                                     # her dolu hücre: değer, dolgu rengi,
                                                                     # formül, birleşik alanlar (müşteri
                                                                     # verisi OLMAYAN dosyalar için)
python excel_dok.py smile2025.xlsx --ornek 5                         # serbest metin sütunlarından maskeli
                                                                     # 5 örnek; yalnız KVKK onayı varsa

Çıktı <dosya>.dokum.txt (UTF-8) dosyasına ve ekrana yazılır. Python 3.9+, yalnız openpyxl gerekir
(pip install openpyxl). Bağımsız betiktir, projenin diğer dosyalarına ihtiyaç duymaz.
"""
import argparse
import collections
import datetime as dt
import os
import re
import sys
from typing import Any, Dict, List, Optional

MASKE = re.compile(r"\d{6,}")
# içinde en az 4 rakam olan harf-rakam karışık belirteç (ör. kayıt/sicil kodu); tarih, saat, yıl etkilenmez
KARISIK = re.compile(r"\b(?=(?:[A-Za-z]*\d){4})(?=\d*[A-Za-z])[A-Za-z0-9]+\b")
KISI_KELIME = {"kisi", "ad", "adi", "soyad", "soyadi", "sicil", "personel", "kullanici", "musteri", "isim", "unvan"}
_TR = str.maketrans("ışğüöçâîû", "isguocaiu")
TUTAR_SAYI = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{3})+(?:,\d+)?(?!\d)")
TUTAR_ISARET = re.compile(r"([<>]=?\s*)\d+(?:[.,]\d+)*")
TUTAR_EK = re.compile(r"\d+(?:[.,]\d+)*(?=\s*(?:TL|USD|EUR)\b)")
DUSUK_KARDINALITE = 25     # bu kadar ya da daha az farklı değer: dağılım yazılır...
KISA_DEGER = 40            # ...ama yalnız değerler bu uzunluğu aşmıyorsa (uzun metin serbest metindir)
ORNEK_UZUNLUK = 160


def maskele(metin: str) -> str:
    return MASKE.sub("######", KARISIK.sub("***", metin))


def _kisi_sutunu(baslik: Any) -> bool:
    ad = str(baslik).replace("İ", "i").lower().translate(_TR)
    return bool(KISI_KELIME & set(re.findall(r"[a-z0-9]+", ad)))


def tutar_gizle(metin: str) -> str:
    metin = TUTAR_SAYI.sub("#", metin)
    metin = TUTAR_ISARET.sub(r"\1#", metin)
    return TUTAR_EK.sub("#", metin)


def tip(deger: Any) -> str:
    if deger is None or (isinstance(deger, str) and not deger.strip()):
        return "boş"
    if isinstance(deger, bool):
        return "mantık"
    if isinstance(deger, (int, float)):
        return "sayı"
    if isinstance(deger, (dt.datetime, dt.date, dt.time)):
        return "tarih"
    return "metin"


def _kimlik_gibi(sayilar: List[float]) -> bool:
    """Tamamı 6–12 haneli tam sayı ve neredeyse hepsi farklı: müşteri/başvuru numarası olabilir."""
    if not sayilar:
        return False
    tam = all(float(s).is_integer() and 1e5 <= abs(s) < 1e12 for s in sayilar)
    return tam and len(set(sayilar)) > 0.8 * len(sayilar)


def _deger_metni(deger: Any) -> str:
    if isinstance(deger, (dt.datetime, dt.date)):
        return deger.isoformat()[:19]
    return maskele(str(deger).replace("\n", " ").strip())


def sutun_ozet(baslik: Any, degerler: List[Any], ornek: int = 0, dagilim_zorla: bool = False) -> List[str]:
    """Tek sütunun özeti: tip sayıları, farklı değer sayısı, tipine göre aralık ya da dağılım."""
    tipler = collections.Counter(tip(d) for d in degerler)
    dolu = [d for d in degerler if tip(d) != "boş"]
    s = ["- {}: {} dolu / {} satır; tip: {}".format(
        maskele(str(baslik)), len(dolu), len(degerler),
        ", ".join("{} {}".format(t, n) for t, n in tipler.most_common()) or "-")]
    if not dolu:
        return s
    farkli = len(set(_deger_metni(d) for d in dolu))
    s.append("  farklı değer: {}".format(farkli))
    sayilar = [float(d) for d in dolu if tip(d) == "sayı"]
    tarihler = [d for d in dolu if tip(d) == "tarih"]
    metinler = [str(d) for d in dolu if tip(d) == "metin"]
    if sayilar:
        if _kimlik_gibi(sayilar):
            s.append("  sayı: kimlik numarası gibi (6–12 haneli, çoğu farklı); aralık yazılmadı")
        else:
            s.append("  sayı: en küçük {}, en büyük {}".format(min(sayilar), max(sayilar)))
    if tarihler:
        tarih_m = sorted(_deger_metni(t) for t in tarihler)
        aylar = collections.Counter(t[:7] for t in tarih_m)
        s.append("  tarih: en eski {}, en yeni {}".format(tarih_m[0], tarih_m[-1]))
        s.append("  aylık: " + ", ".join("{} {}".format(ay, n) for ay, n in sorted(aylar.items())))
    kisa = max(len(_deger_metni(d)) for d in dolu) <= KISA_DEGER
    if _kisi_sutunu(baslik):
        s.append("  dagilim yok sayıldı (kişi sütunu)" if dagilim_zorla
                 else "  içerik yazılmadı (kişi/kimlik sütunu)")
    elif dagilim_zorla or (farkli <= DUSUK_KARDINALITE and kisa):
        dagilim = collections.Counter(_deger_metni(d) for d in dolu)
        s.append("  dağılım: " + "; ".join("{} ({})".format(v, n) for v, n in dagilim.most_common()))
    elif metinler:
        uzunluklar = [len(m) for m in metinler]
        s.append("  serbest metin: ortalama {:.0f}, en uzun {} karakter; içerik yazılmadı".format(
            sum(uzunluklar) / len(uzunluklar), max(uzunluklar)))
        if ornek > 0:
            for m in metinler[:ornek]:
                s.append("  örnek (maskeli): " + maskele(m.replace("\n", " ").strip())[:ORNEK_UZUNLUK])
    return s


def baslik_satiri_bul(satirlar: List[List[Any]]) -> int:
    """İlk 20 satırda en az 2 metin hücresi olan ilk satırın dizini (0 tabanlı); yoksa 0."""
    for i, satir in enumerate(satirlar[:20]):
        if sum(1 for h in satir if tip(h) == "metin") >= 2:
            return i
    return 0


def dokum_yapi(yol: str, ornek: int = 0, baslik_satir: Optional[int] = None,
               dagilim: Optional[List[str]] = None) -> str:
    zorla = {str(d).strip().casefold() for d in (dagilim or [])}
    from openpyxl import load_workbook
    wb = load_workbook(yol, read_only=True, data_only=True)
    s = ["# Yapı dökümü: {}".format(os.path.basename(yol)),
         "sayfalar: " + ", ".join(wb.sheetnames), ""]
    for ws in wb.worksheets:
        satirlar = [list(r) for r in ws.iter_rows(values_only=True)]
        s.append("## Sayfa: {} ({} satır, {} sütun)".format(ws.title, len(satirlar),
                                                             max((len(r) for r in satirlar), default=0)))
        if not satirlar:
            s.append("boş sayfa")
            s.append("")
            continue
        b = baslik_satiri_bul(satirlar) if baslik_satir is None else baslik_satir - 1
        basliklar = satirlar[b]
        veri = satirlar[b + 1:]
        s.append("başlık satırı: {}; veri satırı: {}".format(b + 1, len(veri)))
        if b > 0:
            s.append("başlık üstü satırlar: " + " | ".join(
                " ".join(_deger_metni(h) for h in r if tip(h) != "boş") for r in satirlar[:b]))
        for j, baslik in enumerate(basliklar):
            if tip(baslik) == "boş" and all(tip(r[j]) == "boş" for r in veri if j < len(r)):
                continue
            degerler = [r[j] if j < len(r) else None for r in veri]
            s += sutun_ozet(baslik if tip(baslik) != "boş" else "(sütun {})".format(j + 1), degerler, ornek,
                            tip(baslik) != "boş" and str(baslik).strip().casefold() in zorla)
        s.append("")
    wb.close()
    return "\n".join(s) + "\n"


def _dolgu(cell) -> str:
    dolgu = cell.fill
    if dolgu is None or dolgu.patternType is None:
        return ""
    renk = dolgu.fgColor
    if renk is None:
        return dolgu.patternType
    if renk.type == "rgb":
        return str(renk.rgb)
    if renk.type == "theme":
        return "theme{}{}".format(renk.theme, "" if not renk.tint else "{:+.2f}".format(renk.tint))
    if renk.type == "indexed":
        return "indexed{}".format(renk.indexed)
    return dolgu.patternType


SABIT = re.compile(r"(?<![A-Za-z$])\d+(?:[.,]\d+)?")   # formüldeki sabitler (B12 gibi hücre adresleri kalır)


def dokum_hucreler(yol: str, limit: int = 3000, degersiz: bool = False) -> str:
    """Her dolu hücre: koordinat, değer, dolgu rengi, formül, sayı biçimi; sayfa başına birleşik alanlar.
    Sayısal hücreler tutar/eşik sayıldığı için maskelenmez; metin hücrelerindeki 6+ haneli sayılar maskelenir.
    degersiz=True: sayı ve tarih hücreleri yalnız tipiyle ('sayı', 'tarih'), formüldeki sabitler '#' ile yazılır;
    düzen (hangi hücre sarı/yeşil, başlıklar, birleşik alanlar) görünür, eşik tutarları dışarı çıkmaz."""
    from openpyxl import load_workbook
    wb = load_workbook(yol, data_only=True)
    wb_f = load_workbook(yol, data_only=False)
    s = ["# Hücre dökümü: {}".format(os.path.basename(yol)),
         "sayfalar: " + ", ".join(wb.sheetnames), ""]
    for ws in wb.worksheets:
        ws_f = wb_f[ws.title]
        s.append("## Sayfa: {} (boyut {})".format(ws.title, ws.dimensions))
        birlesik = [str(r) for r in ws.merged_cells.ranges]
        if birlesik:
            s.append("birleşik alanlar: " + ", ".join(sorted(birlesik)))
        yazilan, kalan = 0, 0
        # formül kitabında her dolu hücre var (formül hücresinin önbellek değeri boş olabilir)
        for row in ws_f.iter_rows():
            for cell in row:
                if cell.value is None:
                    continue
                if yazilan >= limit:
                    kalan += 1
                    continue
                formul = cell.value if isinstance(cell.value, str) and cell.value.startswith("=") else None
                deger = ws[cell.coordinate].value
                if deger is None:
                    deger_m = "(hesaplanmamış)" if formul else ""
                elif isinstance(deger, (int, float)) and not isinstance(deger, bool):
                    deger_m = "sayı" if degersiz else deger
                elif isinstance(deger, (dt.datetime, dt.date)) and degersiz:
                    deger_m = "tarih"
                else:
                    deger_m = _deger_metni(deger)
                    if degersiz:
                        deger_m = tutar_gizle(deger_m)
                if formul and degersiz:
                    formul = SABIT.sub("#", formul)
                parcalar = ["{} | {}".format(cell.coordinate, deger_m)]
                dolgu = _dolgu(cell)
                if dolgu:
                    parcalar.append("dolgu=" + dolgu)
                if cell.font is not None and cell.font.bold:
                    parcalar.append("kalın")
                if cell.number_format and cell.number_format != "General":
                    parcalar.append("biçim=" + cell.number_format)
                if formul:
                    parcalar.append("formül=" + formul)
                s.append(" | ".join(parcalar))
                yazilan += 1
        if kalan > 0:
            s.append("... {} dolu hücre daha yazılmadı (--limit {}); bu sayfa veri tablosuysa --hucreler yerine "
                     "yapı dökümünü kullan".format(kalan, limit))
        s.append("")
    return "\n".join(s) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Excel yapı/hücre dökümü (dışarı verilebilir, maskeli).")
    p.add_argument("dosya", help=".xlsx dosyası")
    p.add_argument("--hucreler", action="store_true",
                   help="her dolu hücreyi değer, dolgu rengi ve formülüyle yaz (müşteri verisi olmayan dosya için)")
    p.add_argument("--limit", type=int, default=3000, help="--hucreler: sayfa başına en çok hücre")
    p.add_argument("--degersiz", action="store_true",
                   help="--hucreler: sayı/tarih değerlerini ve formül sabitlerini gizle, yalnız düzen ve renkleri yaz")
    p.add_argument("--ornek", type=int, default=0, help="serbest metin sütunundan maskeli örnek sayısı (KVKK onayı varsa)")
    p.add_argument("--baslik-satir", type=int, help="başlık satırı numarası (1 tabanlı); verilmezse tahmin edilir")
    p.add_argument("--dagilim", action="append", metavar="SUTUN",
                   help="bu başlıklı sütunun dağılımını kardinaliteye bakmadan yaz (tekrarlanabilir; kişi sütunu hariç)")
    p.add_argument("--cikti", help="çıktı dosyası (varsayılan <dosya>.dokum.txt)")
    a = p.parse_args(argv)
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        print("openpyxl yok: pip install openpyxl", file=sys.stderr)
        return 2
    if not os.path.isfile(a.dosya):
        print("dosya bulunamadı: " + a.dosya, file=sys.stderr)
        return 2
    metin = dokum_hucreler(a.dosya, a.limit, a.degersiz) if a.hucreler else dokum_yapi(a.dosya, a.ornek, a.baslik_satir, a.dagilim)
    cikti = a.cikti or (a.dosya + ".dokum.txt")
    with open(cikti, "w", encoding="utf-8") as f:
        f.write(metin)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    print(metin)
    print("döküm yazıldı: " + cikti)
    return 0


if __name__ == "__main__":
    sys.exit(main())
