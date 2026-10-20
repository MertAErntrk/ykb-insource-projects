"""
matris.py — onay yetki seviyesi: limit → seviye lookup'ı ve nihai seviyenin birleştirilmesi.

Boran'ın maili: Excel'deki sarı hücrelere (nominal ve ağırlıklı limit) bakılıp yeşil hücredeki
seviye atanıyor; "nominal ya da ağırlıklandırılmış limit, hangisi daha yüksek seviyeye
çıkarıyorsa o basılıyor". Örnek JSON'da matris 3, H kuralları 8, nihai 8 olduğu için
nihai = max(matris, H kuralları) VARSAYIMI yapıldı (docs/UYGULAMA_PLANI.md bölüm 7 ve 12).

katalog/yetki_matrisi.json SENTETİKTİR (sayılar uydurma): Excel gelince gerçek eşiklerle değiştirilecek ve
Boran'ın mailindeki örnek değerlerle doğrulanacak (gerçek tutarlar repoya yazılmadı; testteki
tutarlar sentetiktir). v2 biçimde segment başına satır listesi, satır koşulu (nominal'e göre) ve
tavan satırı vardır; eski "seviyeler" biçimi de okunur.
"""
from typing import Any, Dict, List, Optional, Tuple


class MatrisHatasi(ValueError):
    pass


def _seviye(satirlar: List[dict], alan: str, deger: float) -> int:
    """deger'in sığdığı ilk satırın seviyesi. Satırlar seviyeye göre artan; üst sınır None = sınırsız."""
    for s in satirlar:
        ust = s.get(alan)
        if ust is None or deger <= float(ust):
            return int(s["seviye"])
    raise MatrisHatasi("{}={} hiçbir satıra sığmadı; son satırın üst sınırı null olmalı".format(alan, deger))


def _kosul_saglanir(satir: dict, nominal: float) -> bool:
    """Satırın isteğe bağlı koşulu (yalnız nominal alanı; op '<' ya da '>='). Koşul yoksa True."""
    k = satir.get("kosul")
    if not k:
        return True
    if k.get("alan") != "nominal":
        raise MatrisHatasi("desteklenmeyen koşul alanı: {}".format(k.get("alan")))
    deger = float(k["deger"])
    if k.get("op") == "<":
        return nominal < deger
    if k.get("op") == ">=":
        return nominal >= deger
    raise MatrisHatasi("desteklenmeyen koşul işleci: {}".format(k.get("op")))


def _segment_seviyesi(satirlar: List[dict], alan: str, deger: float, nominal: float) -> int:
    """v2: koşulu sağlayan ve deger'in sığdığı ilk satırın seviyesi. Alanın üst sınırı None olan satır
    bu alan için atlanır; iki üst sınırı da None olan koşulsuz satır sınırsızdır."""
    for s in satirlar:
        if not _kosul_saglanir(s, nominal):
            continue
        ust = s.get(alan)
        if ust is None:
            if s.get("nominal_ust") is None and s.get("agirlikli_ust") is None and not s.get("kosul"):
                return int(s["seviye"])
            continue
        if deger <= float(ust):
            return int(s["seviye"])
    raise MatrisHatasi("{}={} hiçbir satıra sığmadı; son satırın üst sınırları null olmalı".format(alan, deger))


def seviye_bul(matris: dict, nominal: float, agirlikli: float,
               segment: Optional[str] = None) -> Tuple[int, Dict[str, Any]]:
    """(matris seviyesi, {'nominal', 'agirlikli', 'segment', 'tavan_uygulandi'}).

    Eski biçim ("seviyeler"): segment yok sayılır, seviye iki limitin verdiğinin büyüğü.
    v2 biçim ("segmentler"): segment zorunlu; her alan için koşulu sağlayan ilk uyan satır,
    seviye = max(nominal, ağırlıklı); koşulu sağlanan tavan satırı varsa seviye onunla sınırlanır.
    """
    nominal, agirlikli = float(nominal), float(agirlikli)
    if "seviyeler" in matris:
        satirlar = sorted(matris.get("seviyeler") or [], key=lambda s: int(s["seviye"]))
        if not satirlar:
            raise MatrisHatasi("matris boş")
        n = _seviye(satirlar, "nominal_ust", nominal)
        a = _seviye(satirlar, "agirlikli_ust", agirlikli)
        return max(n, a), {"nominal": n, "agirlikli": a, "segment": None, "tavan_uygulandi": False}

    segmentler = matris.get("segmentler")
    if not isinstance(segmentler, dict) or not segmentler:
        raise MatrisHatasi("matris boş: 'seviyeler' ya da 'segmentler' yok")
    if segment is None:
        raise MatrisHatasi("segmentli matriste segment verilmeli")
    if segment not in segmentler:
        raise MatrisHatasi("bilinmeyen segment: {} (tanımlı: {})".format(segment, ", ".join(sorted(segmentler))))
    satirlar = sorted(segmentler[segment] or [], key=lambda s: int(s["sira"]))
    if not satirlar:
        raise MatrisHatasi("{} segmentinin satırı yok".format(segment))
    n = _segment_seviyesi(satirlar, "nominal_ust", nominal, nominal)
    a = _segment_seviyesi(satirlar, "agirlikli_ust", agirlikli, nominal)
    seviye = max(n, a)
    tavan = False   # yalnız seviyeyi gerçekten düşürdüyse True (olgu olarak cevaba girer)
    for s in satirlar:
        if s.get("tavan") and _kosul_saglanir(s, nominal) and int(s["seviye"]) < seviye:
            seviye = int(s["seviye"])
            tavan = True
    return seviye, {"nominal": n, "agirlikli": a, "segment": segment, "tavan_uygulandi": tavan}


def nihai_seviye(matris_seviyesi: int, h_seviyesi: Optional[int]) -> int:
    """Nihai onay yetkisi = max(matris, H kuralları). H seviyesi yoksa matris seviyesi."""
    if h_seviyesi is None:
        return int(matris_seviyesi)
    return max(int(matris_seviyesi), int(h_seviyesi))
