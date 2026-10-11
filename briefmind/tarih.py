"""
tarih.py — aksiyon tarihlerini toplanti tarihine gore deterministik cozer.

LLM 'persembeye' gibi ifadeleri tarihe cevirirken gun adini ve tarihi tutarsiz yazabiliyor
('2026-10-02 (Persembe)' ama 2 Ekim Cuma). Soylenen sey gun adi oldugu icin gun adi esas alinir.
"""
import datetime as dt
import re

GUNLER = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
# uzun adlar once: 'cumartesi' 'cuma' ile, 'pazartesi' 'pazar' ile baslar
_GUN_KOK = [("cumartesi", 5), ("pazartesi", 0), ("carsamba", 2), ("persembe", 3), ("sali", 1), ("cuma", 4),
            ("pazar", 6)]


def _sade(s):
    s = (s or "").replace("İ", "i").replace("I", "ı").lower()
    for a, b in zip("çğıöşüâî", "cgiosuai"):
        s = s.replace(a, b)
    return s


def bicimle(d):
    return f"{d.isoformat()} ({GUNLER[d.weekday()]})"


# Gun adi kelimesi: kok + (en fazla) hal eki. Eskiden onek eslesmesi vardi: 'pazarlama' Pazar, 'salon'/'salih' Sali,
# 'cumhuriyet' Cuma sayiliyordu (aksiyon tarihi yanlis cozulurdu).
_GUN_EK = r"(?:y?[ae]|y?[iu]|n[iu]n|[dt][ae]n?|[dt][ae]ki|ki|s[iu]|s[iu]n[ae]|n[ae]|[iu]n)?"     # _sade ASCII'ye katlar
_GUN_RE = [(re.compile(kok + _GUN_EK), no) for kok, no in _GUN_KOK]


def _gun_adi(s):
    for kelime in re.findall(r"[a-z]+", s):
        for kalip, no in _GUN_RE:
            if kalip.fullmatch(kelime):
                return no
    return None


_AYLAR = ["ocak", "subat", "mart", "nisan", "mayis", "haziran", "temmuz", "agustos", "eylul", "ekim", "kasim", "aralik"]


def _acik_tarih(s, bugun):
    """Metindeki acik tarih: 'YYYY-MM-DD', 'GG.AA.YYYY' ya da '15 Ekim [2026]' -> date | None (gecersiz tarih de None).
    Yilsiz '15 Ekim' toplanti tarihinden 60 gunden fazla geriyse gelecek yil sayilir."""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        y, a, g = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.search(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4})(?!\d)", s)
        if m:
            g, a, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            m = re.search(r"(?<!\d)(\d{1,2})\s+(" + "|".join(_AYLAR) + r")[a-z]*(?:\s+(\d{4}))?", s)
            if not m:
                return None
            g, a = int(m.group(1)), _AYLAR.index(m.group(2)) + 1
            y = int(m.group(3)) if m.group(3) else bugun.year
            try:
                if not m.group(3) and dt.date(y, a, g) < bugun - dt.timedelta(days=60):
                    y += 1
            except ValueError:
                return None
    try:
        return dt.date(y, a, g)
    except ValueError:
        return None


# Belirsiz / celiskili ifade: kesin gune cevrilmez (Jira Due Date'e yanlis gun girmesin). '(?)', soru ('cuma mi'),
# secenek ('ya da', 'veya', 'yoksa'), duzeltme ('pazartesi degil sali'), tahmin ('belki', 'galiba') ve goreli sure
# ('2 hafta sonra cuma', 'uc gun icinde': hangi hafta oldugu kodla bilinemez).
_BELIRSIZ_TARIH = re.compile(r"\?|(?<![a-z])(?:mi|mu|ya da|veya|yahut|yoksa|degil|belki|galiba|muhtemelen|sanirim"
                             r"|civari|gibi)(?![a-z])")
_SURE = re.compile(r"(?<![a-z0-9])(?:\d+|bir|iki|uc|dort|bes|alti|yedi|sekiz|dokuz|on)\s+(?:hafta|gun|ay)(?![a-z])")


def belirsiz_tarih_mi(ifade):
    """Tarih ifadesi tek bir gune guvenle cozulemez mi? ('cuma (?)', 'persembe mi cuma mi', 'pazartesi degil sali',
    'cumaya ya da pazartesiye', '2 hafta sonra cuma'; iki farkli gun adi.)"""
    s = _sade(str(ifade or ""))
    if _BELIRSIZ_TARIH.search(s) or _SURE.search(s):
        return True
    gunler = set()
    for kelime in re.findall(r"[a-z]+", s):
        for kalip, no in _GUN_RE:
            if kalip.fullmatch(kelime):
                gunler.add(no)
                break
    return len(gunler) > 1


def tarih_coz(ifade, bugun):
    """Aksiyon tarih ifadesini 'YYYY-MM-DD (Gun)' bicimine getirir. Cozemedigini ve belirsiz / celiskili ifadeyi
    (belirsiz_tarih_mi: '(?)', 'mi', 'ya da', 'degil', iki gun adi, '2 hafta sonra') oldugu gibi birakir.
    bugun: toplanti tarihi (datetime.date)."""
    if ifade is None:
        return "-"
    ham = str(ifade).strip()
    if not ham or ham in ("-", "belirsiz", "?"):
        return "-"
    if belirsiz_tarih_mi(ham):
        return ham
    s = _sade(ham)
    gun = _gun_adi(s)
    haftaya = bool(re.search(r"\b(haftaya|gelecek hafta|onumuzdeki hafta|sonraki hafta)", s))
    d = _acik_tarih(s, bugun)
    if d and (gun is None or d.weekday() == gun):
        return bicimle(d)
    # tarih ile gun adi celisiyor: soylenen gun adidir, tarih yeniden hesaplanir
    if re.search(r"\bbugun", s):
        return bicimle(bugun)
    if re.search(r"\byarin", s):
        return bicimle(bugun + dt.timedelta(days=1))
    if re.search(r"\b(obur gun|ertesi gun)", s):
        return bicimle(bugun + dt.timedelta(days=2))
    if gun is not None:
        if haftaya:                                       # 'haftaya sali': bir sonraki haftanin salisi
            pazartesi = bugun - dt.timedelta(days=bugun.weekday()) + dt.timedelta(days=7)
            return bicimle(pazartesi + dt.timedelta(days=gun))
        fark = (gun - bugun.weekday()) % 7 or 7           # 'persembeye' persembe gunu soylendiyse: gelecek hafta
        return bicimle(bugun + dt.timedelta(days=fark))
    if re.search(r"\bay sonu", s):
        sonraki_ay = (bugun.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        return bicimle(sonraki_ay - dt.timedelta(days=1))
    return ham
