"""
metin.py — metin karsilastirma yardimcilari (llm.py ve motor.py ortak kullanir; dongusel import olmasin).
"""
import difflib
import re


def _kelimeler(metin):
    m = (metin or "").replace("İ", "i").replace("I", "ı").lower()
    for a, b in zip("çğıöşüâî", "cgiosuai"):
        m = m.replace(a, b)
    return [k[:4] for k in re.findall(r"[a-z0-9]+", m)]


def metin_benzerligi(a, b):
    """Whisper cumlesi ile altyazi satiri ayni sozu mu tasiyor? (0-1)
    Kelime kokleri (ilk 4 harf) sirali eslenir; Teams'in bozuk yazdigi kelimelerde bile ortak kokler kalir.
    Eskiden harf duzeyinde bakiliyordu: alakasiz iki Turkce cumle bile 0.29-0.35 cikip esigi (0.25) asiyor,
    birden fazla kisi konusurken cumle rastgele birine yaziliyordu. Altyazi satiri uzun bir paragraf
    olabilecegi icin 4+ kelimelik cumlede 'kapsanma' orani da hesaba katilir."""
    ka, kb = _kelimeler(a), _kelimeler(b)
    if not ka or not kb:
        return 0.0
    sm = difflib.SequenceMatcher(None, ka, kb, autojunk=False)
    oran = sm.ratio()
    if len(ka) >= 4:
        ortak = sum(blok.size for blok in sm.get_matching_blocks())
        oran = max(oran, ortak / len(ka))
    return oran


def cift_yonlu_benzerlik(a, b):
    """metin_benzerligi yonludur (a'nin b'de kapsanmasi); not maddelerini karsilastirirken iki yonun buyugu."""
    return max(metin_benzerligi(a, b), metin_benzerligi(b, a))


def tr_kucuk(s):
    """Turkce kurallariyla kucuk harf (I -> ı, İ -> i)."""
    return (s or "").replace("İ", "i").replace("I", "ı").lower()


def ad_geciyor(ad, metin):
    """Kisi adi metinde kelime olarak geciyor mu? Ekli yazim ("Cemil'in", "Berk'e") de sayilir."""
    ad = (ad or "").strip()
    if len(ad) < 3:
        return False
    return re.search(r"(?<!\w)" + re.escape(tr_kucuk(ad)) + r"(?!\w)", tr_kucuk(metin)) is not None
