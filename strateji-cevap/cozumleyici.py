"""
cozumleyici.py — katalogdaki kural tanımını başvuru JSON'una uygular ve cevabın olgularını
deterministik olarak çıkarır. Bu katmanda LLM yoktur: sayı, tarih ve kural kodu yalnızca buradan
gelir (guardrail G1). Üç çözüm türü:

  liste_kosul    Indata'daki bir listede koşulu sağlayan ve tarih penceresine düşen kayıtlar
                 (H221: CRBNotice, Status 1/2, son 5 yıl, en eski kayıt, kaydı veren banka)
  yetki_matrisi  limitlerden matris seviyesi, H kurallarıyla birleştirme, JSON'daki seviyelerle tutarlılık
  sabit          yalnızca kural açıklaması (ek veri gerektirmeyen kurallar)

Çıktı Sonuc: seçilen şablon, doldurulmuş metin (kapanış hariç), olgular, inceleme notları, onay durumu.
"""
import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from matris import MatrisHatasi, nihai_seviye, seviye_bul
from yardimci import ay_yil, tarih_ayristir, tl_bicim, yil_once, yol_al

HIT_DEGERLERI = (1, True, "1", "true", "TRUE", "True", "E", "Y", "EVET")


@dataclass
class Sonuc:
    kod: str
    sablon: str                                        # seçilen şablon adı
    metin: str                                         # doldurulmuş şablon (kapanış hariç)
    olgular: Dict[str, Any] = field(default_factory=dict)
    notlar: List[str] = field(default_factory=list)    # inceleme gerektiren durumlar
    onayli: bool = True                                # şablon strateji ekibince onaylı mı

    @property
    def inceleme_gerekli(self) -> bool:
        return bool(self.notlar) or not self.onayli


def _tam(deger: Any) -> Optional[int]:
    """'3', 3.0, 3 → 3; boş ya da sayı değilse None."""
    if deger is None or deger == "":
        return None
    try:
        return int(float(deger))
    except (TypeError, ValueError):
        return None


def _sablon_doldur(kural: dict, ad: str, olgular: dict) -> Tuple[str, bool, Optional[str]]:
    """(metin, onaylı mı, hata notu). Şablon yoksa ya da alanı eksikse metin boş, not dolu."""
    s = kural["sablonlar"].get(ad)
    if s is None:
        return "", False, "'{}' şablonu katalogda yok".format(ad)
    try:
        metin = s["metin"].format(**olgular)
    except (KeyError, IndexError, ValueError) as e:
        return "", False, "'{}' şablonunda doldurulamayan alan: {}".format(ad, e)
    onayli = (s.get("onay") or "taslak").strip().lower() != "taslak"
    return metin, onayli, None


def _sonuc(kural: dict, sablon: str, olgular: dict, notlar: List[str]) -> Sonuc:
    metin, onayli, hata = _sablon_doldur(kural, sablon, olgular)
    if hata:
        notlar = notlar + [hata]
    return Sonuc(kural["kod"], sablon, metin, olgular, notlar, onayli)


def _hit(kayit: dict) -> bool:
    return yol_al(kayit, "Hit") in HIT_DEGERLERI


def kural_calisti_mi(basvuru: dict, kod: str, yol: str = "Outdata.HRules") -> Optional[bool]:
    """Başvuru çıktısındaki kural listesine göre kod çalışmış mı. Liste yoksa None (bilinmiyor)."""
    liste = yol_al(basvuru, yol)
    if not isinstance(liste, list):
        return None
    for k in liste:
        if isinstance(k, dict) and str(yol_al(k, "Code", "")).upper() == kod.upper():
            return _hit(k)
    return False


def _ortak(kural: dict, basvuru: dict, ayar: dict) -> Tuple[str, Optional[dt.date], Dict[str, Any]]:
    cif = str(yol_al(basvuru, ayar.get("cif_yolu", "CIF"), "") or "")
    ref = tarih_ayristir(yol_al(basvuru, ayar.get("tarih_yolu", "BasvuruTarihi")))
    olgular = {"cif": cif, "kural": kural["kod"], "aciklama": kural.get("aciklama", "")}
    return cif, ref, olgular


def coz(kural: dict, basvuru: dict, ayar: Optional[dict] = None) -> Sonuc:
    """Kuralı başvuruya uygular. ayar: cif_yolu, tarih_yolu, kendi_banka_kodu, matris (yetki için)."""
    ayar = ayar or {}
    tur = kural["cozum"]["tur"]
    if tur == "liste_kosul":
        return _liste_kosul(kural, basvuru, ayar)
    if tur == "yetki_matrisi":
        return _yetki_matrisi(kural, basvuru, ayar)
    return _sabit(kural, basvuru, ayar)


def _liste_kosul(kural: dict, basvuru: dict, ayar: dict) -> Sonuc:
    c = kural["cozum"]
    cif, ref, olgular = _ortak(kural, basvuru, ayar)
    notlar: List[str] = []
    if ref is None:
        notlar.append("başvuru tarihi JSON'da yok; pencere bugüne göre hesaplandı")
        ref = dt.date.today()
    liste = yol_al(basvuru, c["liste"])
    if not isinstance(liste, list):
        notlar.append("{} başvuru JSON'unda yok ya da liste değil".format(c["liste"]))
        liste = []
    degerler = {str(v) for v in c["kosul"]["degerler"]}
    pencere = c.get("pencere_yil")
    baslangic = yil_once(ref, int(pencere)) if pencere else None
    olgular["pencere_yil"] = pencere
    uygun: List[Tuple[dt.date, dict]] = []
    for kayit in liste:
        if not isinstance(kayit, dict):
            continue
        if str(yol_al(kayit, c["kosul"]["alan"], "")) not in degerler:
            continue
        t = tarih_ayristir(yol_al(kayit, c["tarih_alani"]))
        if t is None:
            notlar.append("tarihsiz {} kaydı atlandı".format(c["liste"]))
            continue
        if baslangic is not None and not (baslangic <= t <= ref):
            continue
        uygun.append((t, kayit))
    uygun.sort(key=lambda x: x[0])
    olgular["kayit_sayisi"] = len(uygun)
    if not uygun:
        notlar.append("koşulu sağlayan kayıt yok; kural çalıştıysa veri ya da katalog tutarsız")
        return _sonuc(kural, "kayit_yok", olgular, notlar)
    t, kayit = uygun[0] if c.get("secim", "en_eski") == "en_eski" else uygun[-1]
    olgular["tarih"] = t.isoformat()
    olgular["ay_yil"] = ay_yil(t)
    sablon = "baska_banka"
    banka_alani = c.get("banka_alani")
    if banka_alani:
        kayit_banka = str(yol_al(kayit, banka_alani, "") or "")
        kendi = str(ayar.get("kendi_banka_kodu") or "")
        olgular["banka_kodu"] = kayit_banka
        if kendi and kayit_banka == kendi:
            sablon = "kendi_banka"
        elif not kendi:
            notlar.append("kendi_banka_kodu ayarı boş: kaydın başka bankadan geldiği doğrulanamadı")
    return _sonuc(kural, sablon, olgular, notlar)


def _yetki_matrisi(kural: dict, basvuru: dict, ayar: dict) -> Sonuc:
    c = kural["cozum"]
    cif, _ref, olgular = _ortak(kural, basvuru, ayar)
    notlar: List[str] = []
    nominal = yol_al(basvuru, c["nominal"])
    agirlikli = yol_al(basvuru, c["agirlikli"])
    m_json = _tam(yol_al(basvuru, c.get("matris_seviyesi", "")))
    h_json = _tam(yol_al(basvuru, c.get("h_seviyesi", "")))
    nihai_json = _tam(yol_al(basvuru, c.get("nihai", "")))
    olgular.update({"nominal": tl_bicim(nominal), "agirlikli": tl_bicim(agirlikli),
                    "matris_seviyesi": m_json, "h_seviyesi": h_json, "nihai": nihai_json, "h_kodlari": "ilgili H"})
    if nominal is None or agirlikli is None:
        notlar.append("limit alanları başvuru JSON'unda bulunamadı")
        return _sonuc(kural, "veri_eksik", olgular, notlar)

    hesap = None
    matris = ayar.get("matris")
    if matris is not None:
        try:
            hesap, ayrinti = seviye_bul(matris, nominal, agirlikli)
            olgular["hesap_matris"] = hesap
            olgular["hesap_ayrinti"] = ayrinti
        except (MatrisHatasi, TypeError, ValueError) as e:
            notlar.append("matris hesabı: {}".format(e))
    if hesap is not None and m_json is not None and hesap != m_json:
        notlar.append("matris seviyesi JSON'da {}, katalog matrisine göre {}: matris dosyası ya da "
                      "ağırlıklı limit seçimi güncel değil".format(m_json, hesap))
    m = m_json if m_json is not None else hesap
    if m is None:
        notlar.append("matris seviyesi ne JSON'dan ne matristen bulunabildi")
        return _sonuc(kural, "veri_eksik", olgular, notlar)
    olgular["matris_seviyesi"] = m

    yukselten: List[str] = []
    h_liste = yol_al(basvuru, c["h_kurallari"]) if c.get("h_kurallari") else None
    if isinstance(h_liste, list):
        for k in h_liste:
            if isinstance(k, dict) and _hit(k) and _tam(yol_al(k, "AuthLevel")) not in (None, 0):
                yukselten.append(str(yol_al(k, "Code", "?")).upper())
    if yukselten:
        olgular["h_kodlari"] = ", ".join(yukselten)

    beklenen = nihai_seviye(m, h_json)
    if nihai_json is None:
        olgular["nihai"] = beklenen
        notlar.append("nihai seviye JSON'da yok; max(matris, H) ile hesaplandı")
    elif nihai_json != beklenen:
        notlar.append("nihai seviye JSON'da {}, max(matris, H) = {}: birleştirme varsayımı bu başvuruda "
                      "tutmuyor".format(nihai_json, beklenen))
    if h_json is not None and h_json > m:
        if not yukselten:
            notlar.append("seviyeyi yükselten H kuralı çıktı listesinde bulunamadı")
        return _sonuc(kural, "h_kurali_belirleyici", olgular, notlar)
    return _sonuc(kural, "matris_belirleyici", olgular, notlar)


def _sabit(kural: dict, basvuru: dict, ayar: dict) -> Sonuc:
    _cif, _ref, olgular = _ortak(kural, basvuru, ayar)
    return _sonuc(kural, "aciklama", olgular, [])
