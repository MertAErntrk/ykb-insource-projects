"""
matris.py — onay yetki seviyesi: limit → seviye lookup'ı ve nihai seviyenin birleştirilmesi.

Boran'ın maili: Excel'deki sarı hücrelere (nominal ve ağırlıklı limit) bakılıp yeşil hücredeki
seviye atanıyor; "nominal ya da ağırlıklandırılmış limit, hangisi daha yüksek seviyeye
çıkarıyorsa o basılıyor". Örnek JSON'da matris 3, H kuralları 8, nihai 8 olduğu için
nihai = max(matris, H kuralları) VARSAYIMI yapıldı (docs/UYGULAMA_PLANI.md bölüm 7 ve 12).

katalog/yetki_matrisi.json SENTETİKTİR: Excel gelince gerçek eşiklerle değiştirilecek ve
Boran'ın mailindeki örnek değerlerle doğrulanacak (gerçek tutarlar repoya yazılmadı; testteki
tutarlar sentetiktir).
"""
from typing import Dict, List, Optional, Tuple


class MatrisHatasi(ValueError):
    pass


def _seviye(satirlar: List[dict], alan: str, deger: float) -> int:
    """deger'in sığdığı ilk satırın seviyesi. Satırlar seviyeye göre artan; üst sınır None = sınırsız."""
    for s in satirlar:
        ust = s.get(alan)
        if ust is None or deger <= float(ust):
            return int(s["seviye"])
    raise MatrisHatasi("{}={} hiçbir satıra sığmadı; son satırın üst sınırı null olmalı".format(alan, deger))


def seviye_bul(matris: dict, nominal: float, agirlikli: float) -> Tuple[int, Dict[str, int]]:
    """(matris seviyesi, {'nominal': s, 'agirlikli': s}). Seviye, iki limitin verdiğinin büyüğü."""
    satirlar = sorted(matris.get("seviyeler", []), key=lambda s: int(s["seviye"]))
    if not satirlar:
        raise MatrisHatasi("matris boş")
    n = _seviye(satirlar, "nominal_ust", float(nominal))
    a = _seviye(satirlar, "agirlikli_ust", float(agirlikli))
    return max(n, a), {"nominal": n, "agirlikli": a}


def nihai_seviye(matris_seviyesi: int, h_seviyesi: Optional[int]) -> int:
    """Nihai onay yetkisi = max(matris, H kuralları). H seviyesi yoksa matris seviyesi."""
    if h_seviyesi is None:
        return int(matris_seviyesi)
    return max(int(matris_seviyesi), int(h_seviyesi))
