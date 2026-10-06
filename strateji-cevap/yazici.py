"""
yazici.py — çözüm sonuçlarından cevap taslağını kurar ve guardrail denetimlerini uygular.

Deterministik taslak = şablon metinleri (tek selamlama) + tek kapanış. LLM yalnızca isteğe bağlı
"yeniden yazım" içindir (birden çok kuralı tek akıcı metne toplamak). LLM çıktısı taslağa karşı
doğrulanır: taslakta olmayan sayı, tarih, kural kodu geçiyorsa ya da metin İngilizceye kaydıysa
LLM çıktısı atılır ve deterministik taslak kalır (BriefMind'daki LLM'siz yedek mantığı).
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from cozumleyici import Sonuc
from yardimci import AYLAR, tr_buyuk_ilk

SAYI = re.compile(r"\d[\d.,]*\d|\d")
KOD = re.compile(r"\b[A-Z]{1,2}\d{3}\b")
AY_YIL = re.compile(r"\b(?:" + "|".join(AYLAR) + r")\s+\d{4}\b")
INGILIZCE = {"the", "and", "is", "are", "for", "with", "this", "that", "customer", "bank", "report",
             "rule", "because", "please", "hello", "regards", "has", "been", "was", "were", "of", "to"}
SELAM = re.compile(r"^\s*merhaba[,;]?\s*", re.I)

YENIDEN_YAZIM_SISTEM = (
    "Bankanın kredi strateji ekibi adına şube/satış ekibine cevap yazıyorsun. Verilen taslağı, çağrıdaki "
    "soruya cevap veren tek bir akıcı Türkçe mesaj olarak yeniden yaz. Kurallar: taslakta olmayan hiçbir "
    "sayı, tarih, kural kodu ya da müşteri bilgisi ekleme; taslaktaki bütün olguları koru; kapanış "
    "cümlesini aynen koru; 'Merhaba' ile başla; yalnızca mesajı döndür."
)


@dataclass
class Taslak:
    metin: str
    durum: str                                   # otomatik | inceleme_gerekli
    notlar: List[str] = field(default_factory=list)      # inceleme gerektiren nedenler
    uyarilar: List[str] = field(default_factory=list)    # bilgi (durumu değiştirmez)
    kurallar: List[str] = field(default_factory=list)
    olgular: Dict[str, Any] = field(default_factory=dict)
    yontem: str = "sablon"                       # sablon | llm
    sinif: str = ""
    cif: str = ""

    def durumu_guncelle(self) -> "Taslak":
        self.durum = "inceleme_gerekli" if self.notlar or not self.metin.strip() else "otomatik"
        return self


def olgu_tokenlari(metin: str) -> Set[str]:
    """Metindeki sayılar, kural kodları ve 'Nisan 2025' biçimli tarihler."""
    return set(SAYI.findall(metin)) | set(KOD.findall(metin)) | set(AY_YIL.findall(metin))


def ingilizce_mi(metin: str) -> bool:
    kelimeler = re.findall(r"[A-Za-z]+", metin)
    if len(kelimeler) < 8:
        return False
    ing = sum(1 for k in kelimeler if k.lower() in INGILIZCE)
    return ing / len(kelimeler) > 0.12


def dogrula(aday: str, taslak: str) -> List[str]:
    """LLM metni taslağa sadık mı: fazladan olgu, İngilizce, çok kısa. Boş liste = geçerli."""
    sorunlar: List[str] = []
    fazla = olgu_tokenlari(aday) - olgu_tokenlari(taslak)
    if fazla:
        sorunlar.append("taslakta olmayan olgular: " + ", ".join(sorted(fazla)))
    eksik = olgu_tokenlari(taslak) - olgu_tokenlari(aday)
    if eksik:
        sorunlar.append("taslaktaki olgular düşmüş: " + ", ".join(sorted(eksik)))
    if ingilizce_mi(aday):
        sorunlar.append("metin İngilizceye kaymış")
    if len(aday.strip()) < 40:
        sorunlar.append("metin çok kısa")
    return sorunlar


def _birlestir(paragraflar: List[str]) -> str:
    """İlk paragrafın selamlaması kalır, sonrakilerin başındaki 'Merhaba,' düşer."""
    govde = [paragraflar[0].strip()]
    for p in paragraflar[1:]:
        p = SELAM.sub("", p.strip())
        if p:
            govde.append(tr_buyuk_ilk(p))
    return "\n\n".join(govde)


def kur(sonuclar: List[Sonuc], kapanis: str, llm=None, cagri_metni: str = "") -> Taslak:
    notlar = ["{}: {}".format(s.kod, n) for s in sonuclar for n in s.notlar]
    for s in sonuclar:
        if not s.onayli and s.metin:
            notlar.append("{}: '{}' şablonu henüz onaylı değil".format(s.kod, s.sablon))
    olgular = {s.kod: s.olgular for s in sonuclar}
    kurallar = [s.kod for s in sonuclar]
    paragraflar = [s.metin.strip() for s in sonuclar if s.metin.strip()]
    if not paragraflar:
        return Taslak("", "inceleme_gerekli", notlar + ["cevap metni üretilemedi"], [], kurallar, olgular)

    metin = _birlestir(paragraflar)
    if kapanis and kapanis.strip() not in metin:
        metin = metin + "\n\n" + kapanis.strip()
    taslak = Taslak(metin, "", notlar, [], kurallar, olgular, "sablon")

    if llm is not None and len(paragraflar) > 1:
        aday = llm.metin(YENIDEN_YAZIM_SISTEM, "Çağrı:\n{}\n\nTaslak:\n{}".format(cagri_metni, metin))
        if aday:
            sorun = dogrula(aday, metin)
            if sorun:
                taslak.uyarilar.append("LLM yeniden yazımı reddedildi: " + "; ".join(sorun))
            else:
                taslak.metin, taslak.yontem = aday.strip(), "llm"
        else:
            taslak.uyarilar.append("LLM yeniden yazımı alınamadı; şablon metni kullanıldı")
    return taslak.durumu_guncelle()
