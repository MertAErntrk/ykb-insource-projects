"""
motor.py — uçtan uca boru hattı:
  çağrı → sınıflandırma (sınıf, kural kodları, CIF) → başvuru JSON'u → kural başına çözüm
        → taslak (tek selamlama, tek kapanış) → guardrail denetimi → durum (otomatik | inceleme_gerekli)

Hiçbir şey gönderilmez: taslak, strateji ekibinin onayına sunulmak üzere döner (MVP: insan onayı).

python motor.py --cagri ornekler/cagrilar/h221.json [--basvurular ornekler/basvurular] [--config config.json] [--llm]
"""
import argparse
import json
import os
import sys
from dataclasses import asdict
from typing import Optional

import cozumleyici
import katalog as katalog_mod
import siniflandirici
import yazici
from veri import DosyaKaynagi
from yardimci import tarih_ayristir, yol_al

KOK = os.path.dirname(os.path.abspath(__file__))
VARSAYILAN_AYAR = {
    "cif_yolu": "CIF",                       # başvuru JSON'unda müşteri numarası
    "tarih_yolu": "BasvuruTarihi",           # başvuru JSON'unda strateji çalışma tarihi
    "h_kurallari_yolu": "Outdata.HRules",    # çalışan kuralların listesi (VARSAYIM)
    "kendi_banka_kodu": "",                  # KKB kayıtlarında bankamızın kodu (H221: farklı banka ayrımı)
    "basvurular": os.path.join(KOK, "ornekler", "basvurular"),
}


def ayar_oku(yol: Optional[str] = None) -> dict:
    ayar = dict(VARSAYILAN_AYAR)
    if yol and os.path.exists(yol):
        with open(yol, encoding="utf-8") as f:
            ayar.update(json.load(f))
    return ayar


class Motor:
    def __init__(self, katalog: katalog_mod.Katalog, matris: dict, kaynak, ayar: Optional[dict] = None, llm=None):
        self.katalog = katalog
        self.matris = matris
        self.kaynak = kaynak
        self.ayar = dict(VARSAYILAN_AYAR, **(ayar or {}))
        self.llm = llm

    @classmethod
    def kur(cls, ayar: Optional[dict] = None, llm=None) -> "Motor":
        ayar = dict(VARSAYILAN_AYAR, **(ayar or {}))
        kaynak = DosyaKaynagi(ayar["basvurular"], ayar["cif_yolu"], ayar["tarih_yolu"])
        return cls(katalog_mod.Katalog.yukle(), katalog_mod.matris_yukle(), kaynak, ayar, llm)

    def cevapla(self, cagri: dict) -> yazici.Taslak:
        metin = cagri.get("metin", "") or ""
        s = siniflandirici.siniflandir(metin, self.katalog, self.llm)
        cif = str(cagri.get("cif") or (s.cifler[0] if s.cifler else "") or "")
        bos = yazici.Taslak("", "inceleme_gerekli", [], [], list(s.kural_kodlari), {}, "sablon", s.sinif, cif)

        if s.sinif == "hata":
            bos.notlar.append("hata sınıfı çağrı: strateji ekibi / BT'ye yönlendirilir, otomatik cevap yok")
            return bos
        if not s.kural_kodlari:
            bos.notlar.append("kural kodu ya da bilinen konu tespit edilemedi" +
                              (" (girdi sorusu: veri kaynağı açıklamaları faz 2)" if s.sinif == "girdi" else ""))
            return bos
        if not cif:
            bos.notlar.append("müşteri numarası bulunamadı")
            return bos
        uyarilar = []
        if len(s.cifler) > 1:
            uyarilar.append("birden çok müşteri numarası geçiyor; ilki alındı: " + ", ".join(s.cifler))
        if s.sinif == "girdi":
            uyarilar.append("girdi sınıfı soru: kural açıklaması verildi, veri kaynağı açıklaması faz 2")

        basvuru = self.kaynak.bul(cif, tarih_ayristir(cagri.get("tarih")))
        if basvuru is None:
            bos.notlar.append("{} için başvuru JSON'u bulunamadı".format(cif))
            bos.uyarilar = uyarilar
            return bos
        tarih = tarih_ayristir(yol_al(basvuru, self.ayar["tarih_yolu"]))

        notlar, sonuclar = [], []
        for kod in s.kural_kodlari:
            kural = self.katalog.bul(kod, tarih)
            if kural is None:
                notlar.append("{}: katalogda bu tarihte geçerli tanım yok".format(kod))
                continue
            if kural["cozum"]["tur"] != "yetki_matrisi":
                calisti = cozumleyici.kural_calisti_mi(basvuru, kod, self.ayar["h_kurallari_yolu"])
                if calisti is False:
                    notlar.append("{}: başvuru çıktısında bu kural çalışmamış görünüyor".format(kod))
                elif calisti is None:
                    uyarilar.append("{}: çalışan kural listesi JSON'da yok, kuralın çalıştığı doğrulanamadı".format(kod))
            sonuclar.append(cozumleyici.coz(kural, basvuru, dict(self.ayar, matris=self.matris)))

        kapanis = self.katalog.kapanis(self.katalog.bul(s.kural_kodlari[0], tarih))
        taslak = yazici.kur(sonuclar, kapanis, self.llm, metin)
        taslak.notlar = notlar + taslak.notlar
        taslak.uyarilar = uyarilar + taslak.uyarilar
        taslak.sinif, taslak.cif, taslak.kurallar = s.sinif, cif, list(s.kural_kodlari)
        return taslak.durumu_guncelle()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Strateji çağrısına cevap taslağı üretir (göndermez).")
    p.add_argument("--cagri", required=True, help="çağrı JSON'u: {id, tarih, metin, cif?}")
    p.add_argument("--basvurular", help="başvuru JSON klasörü (varsayılan: ornekler/basvurular)")
    p.add_argument("--config", default=os.path.join(KOK, "config.json"))
    p.add_argument("--llm", action="store_true", help="config'teki LLM ile sınıflandırma ve yeniden yazım")
    p.add_argument("--json", action="store_true", help="yalnızca JSON çıktı")
    a = p.parse_args(argv)

    ayar = ayar_oku(a.config)
    if a.basvurular:
        ayar["basvurular"] = a.basvurular
    llm = None
    if a.llm:
        from llm import LLM
        llm = LLM(ayar)
    with open(a.cagri, encoding="utf-8") as f:
        cagri = json.load(f)
    taslak = Motor.kur(ayar, llm).cevapla(cagri)
    if a.json:
        print(json.dumps(asdict(taslak), ensure_ascii=False, indent=2))
        return 0
    print("Sınıf: {}   Kurallar: {}   CIF: {}   Durum: {}   Yöntem: {}".format(
        taslak.sinif, ", ".join(taslak.kurallar) or "-", taslak.cif or "-", taslak.durum, taslak.yontem))
    for n in taslak.notlar:
        print("  ! " + n)
    for u in taslak.uyarilar:
        print("  - " + u)
    print()
    print(taslak.metin or "(cevap metni yok)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
