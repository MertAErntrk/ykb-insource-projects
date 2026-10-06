"""
siniflandirici.py — çağrı metnini anlar: sınıf (girdi / cikti / hata), kural kodları, müşteri
numaraları. Önce deterministik (düzenli ifade + anahtar kelime); LLM verilmişse sınıf ve kodlar
LLM ile zenginleştirilir. LLM'in önerdiği kod metinde ya da katalogda, numara metinde yoksa
alınmaz (guardrail G1: LLM olgu üretmez).

VARSAYIM: müşteri numarası (CIF) 8 hanelidir (Boran'ın mailindeki örneklere göre).
Başvuru numarası deseni Smile dökümü gelince eklenecek.
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from yardimci import tr_kucuk

KURAL_KODU = re.compile(r"\b([A-Z]{1,2})\s?-?\s?(\d{3})\b")      # H221, H 221, H-221
CIF_DESENI = re.compile(r"(?<![\d.])(\d{8})(?![\d.])")             # 8 haneli, sayı/nokta bitişik değil

HATA_KELIMELERI = ["hata", "error", "exception", "timeout", "zaman aşımı", "çalışmıyor", "çalışmadı",
                   "patladı", "açılmıyor", "yanıt vermiyor", "sonuç gelmedi", "boş döndü", "takılı kaldı",
                   "donuyor", "kilitlendi"]
GIRDI_KELIMELERI = ["kkb", "findeks", "bilanço", "mali tablo", "mizan", "veri", "girdi", "yanlış görünüyor",
                    "yanlış geliyor", "güncel değil", "eksik geliyor", "hatalı geliyor", "nereden geliyor",
                    "kaynağı", "güncellenmemiş", "eski kalmış"]
CIKTI_KELIMELERI = ["neden", "niçin", "niye", "kural", "yetki", "seviye", "limit", "takıldı", "takılıyor",
                    "çalıştı", "çalışıyor", "ret", "red", "karar", "sonuç", "çıktı", "istiyor", "isteniyor",
                    "talep ediliyor", "gerekçe"]

SINIF_SISTEM = (
    "Bir bankanın kredi strateji (karar motoru) ekibine gelen çağrıyı sınıflandırıyorsun. "
    "Sınıflar: 'girdi' (stratejiye giren veri neden böyle), 'cikti' (strateji neden bu sonucu/kuralı verdi), "
    "'hata' (çalışma hatası, teknik sorun). Çağrıda geçen kural kodlarını (H221 gibi) ve 8 haneli müşteri "
    "numaralarını metinden olduğu gibi al; uydurma. Yalnızca JSON döndür."
)
SINIF_SEMASI = {
    "type": "object",
    "properties": {
        "sinif": {"type": "string", "enum": ["girdi", "cikti", "hata"]},
        "kural_kodlari": {"type": "array", "items": {"type": "string"}},
        "cifler": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["sinif", "kural_kodlari", "cifler"],
}


@dataclass
class Siniflandirma:
    sinif: str                                   # girdi | cikti | hata | belirsiz
    kural_kodlari: List[str] = field(default_factory=list)
    cifler: List[str] = field(default_factory=list)
    yontem: str = "kural"                        # kural | llm
    puanlar: Dict[str, int] = field(default_factory=dict)


def kural_kodlari(metin: str, bilinen: Optional[set] = None) -> List[str]:
    """Metindeki kural kodları (sırayla, tekil). bilinen verilirse yalnızca katalogdakiler ve H ile başlayanlar."""
    bulunan: List[str] = []
    for harf, no in KURAL_KODU.findall((metin or "").upper()):
        kod = harf + no
        if bilinen is not None and kod not in bilinen and not kod.startswith("H"):
            continue
        if kod not in bulunan:
            bulunan.append(kod)
    return bulunan


def cifler(metin: str) -> List[str]:
    bulunan: List[str] = []
    for c in CIF_DESENI.findall(metin or ""):
        if c not in bulunan:
            bulunan.append(c)
    return bulunan


def _puanla(metin_k: str) -> Dict[str, int]:
    return {
        "hata": sum(1 for k in HATA_KELIMELERI if k in metin_k),
        "girdi": sum(1 for k in GIRDI_KELIMELERI if k in metin_k),
        "cikti": sum(1 for k in CIKTI_KELIMELERI if k in metin_k),
    }


def siniflandir(metin: str, katalog=None, llm=None) -> Siniflandirma:
    metin = metin or ""
    m = tr_kucuk(metin)
    bilinen = set(katalog.kodlar()) if katalog is not None else None
    kodlar = kural_kodlari(metin, bilinen)
    if katalog is not None:
        for k in katalog.anahtar_kelime_eslestir(metin):
            if k not in kodlar:
                kodlar.append(k)
    numaralar = cifler(metin)
    puan = _puanla(m)
    if kodlar:
        puan["cikti"] += 2
    en_yuksek = max(puan.values())
    if en_yuksek == 0:
        sinif = "belirsiz"
    elif puan["hata"] == en_yuksek:
        sinif = "hata"
    elif puan["girdi"] == en_yuksek:
        sinif = "girdi"
    else:
        sinif = "cikti"
    sonuc = Siniflandirma(sinif, kodlar, numaralar, "kural", puan)

    if llm is not None:
        cevap = llm.json_cevap(SINIF_SISTEM, metin, SINIF_SEMASI)
        if isinstance(cevap, dict):
            if cevap.get("sinif") in ("girdi", "cikti", "hata"):
                sonuc.sinif = cevap["sinif"]
                sonuc.yontem = "llm"
            metin_sikisik = metin.upper().replace(" ", "").replace("-", "")
            for k in cevap.get("kural_kodlari") or []:
                k = str(k).upper().replace(" ", "").replace("-", "")
                if k and k not in sonuc.kural_kodlari and (k in metin_sikisik or (bilinen and k in bilinen)):
                    sonuc.kural_kodlari.append(k)
            for c in cevap.get("cifler") or []:
                c = re.sub(r"\D", "", str(c))
                if c and c in metin and c not in sonuc.cifler:
                    sonuc.cifler.append(c)
    return sonuc
