"""
metin.py — metin karsilastirma yardimcilari (llm.py ve motor.py ortak kullanir; dongusel import olmasin).
"""
import difflib
import re

KONU_DAMGASI = re.compile(r"\s*\[konu:\s*([^\]]+)\]")   # nottaki '[konu: X]' damgasi (tek regex)


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
    """Kisi adi metinde kelime olarak geciyor mu? Ekli yazim ("Kemal'in", "Emre'e") de sayilir."""
    ad = (ad or "").strip()
    if len(ad) < 3:
        return False
    return re.search(r"(?<!\w)" + re.escape(tr_kucuk(ad)) + r"(?!\w)", tr_kucuk(metin)) is not None


KESIN_GUVEN = 0.7               # spk_guven bunun altindaysa ad "Ad?" gosterilir (gosterim kurali, MIMARI_v3 §3)


def nota_girer(satir):
    """Satir nota / ozete / dayanaga / RAG pencerelerine girer mi? (motor ve rag icin TEK kural; motor.nota_girer bunu
    sarar.) Icsel halusinasyon supheli ('ic') satirlar girmez (canli ekranda soluk, transkriptte kalir); altyazi yedegi
    'supheli' isaretlidir ama bilincli olarak girer; zaman cizelgesi suphesi ('cizelge': altyazi canliyken etkinlik yok)
    icerigi dusurmez: girer, yalniz 'Bilinmeyen' etiketiyle."""
    if not isinstance(satir, dict):
        return False
    if satir.get("supheli_neden") == "cizelge":
        return True
    return not (satir.get("supheli") and satir.get("kaynak") != "altyazi-yedek")


def gorunen_ad(satir, esik=KESIN_GUVEN):
    """Satirin konusmacisinin TEK gosterim kurali (canli ekran, not.md transkripti, LLM girdisi, Gecmis):
    karar bekleniyor -> "beklemede…"; "?" -> "Bilinmeyen"; "A / B" cakisma -> "A / B (aynı anda)"; "A → B" oldugu
    gibi; oda etiketi -> "Ad (oda)"; spk_guven < esik olan kisi adi -> "Ad?". spk_guven alani olmayan (v2 oncesi,
    gocsuz) satirda ad degismez. jsonl'deki speaker alani degismez; bu yalniz gorunum katmanidir."""
    if not isinstance(satir, dict):
        return "Bilinmeyen"
    if satir.get("durum") == "beklemede":
        return "beklemede…"
    ad = str(satir.get("speaker") or "").strip()
    if not ad or ad == "?":
        return "Bilinmeyen"
    if " / " in ad:
        return f"{ad} (aynı anda)" if satir.get("cakisma") else ad
    if " → " in ad:
        return ad
    if satir.get("kimlik_turu") == "oda":
        return f"{ad} (oda)"
    guven = satir.get("spk_guven")
    if isinstance(guven, (int, float)) and guven < esik and not ad.endswith("?"):
        return ad + "?"
    return ad


def kesin_ad_mi(satir, esik=KESIN_GUVEN):
    """Satirin konusmacisi kesin bir kisi adi mi? (katilimci listesi, sorumlu/sahip uretimi). "?", "Ad?",
    birlesik adlar ("A → B", "A / B"), oda/genel etiketleri, karar bekleyen, eski (goc) ve altyazi yedegi
    satirlari kesin degildir. spk_guven alani olmayan satir (v2 oncesi, gocsuz test verisi) eski davranisla kesin."""
    if not isinstance(satir, dict):
        return False
    ad = str(satir.get("speaker") or "").strip()
    if not ad or "?" in ad or " → " in ad or " / " in ad:
        return False
    if satir.get("durum") in ("beklemede", "eski") or satir.get("kaynak") == "altyazi-yedek":
        return False
    if satir.get("kimlik_turu") in ("oda", "genel", "hayalet"):
        return False
    guven = satir.get("spk_guven")
    return not (isinstance(guven, (int, float)) and guven < esik)
