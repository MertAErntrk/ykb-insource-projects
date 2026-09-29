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


def _gun_adi(s):
    for kelime in re.findall(r"[a-z]+", s):
        for kok, no in _GUN_KOK:
            if kelime.startswith(kok):
                return no
    return None


def tarih_coz(ifade, bugun):
    """Aksiyon tarih ifadesini 'YYYY-MM-DD (Gun)' bicimine getirir. Cozemedigini oldugu gibi birakir.
    bugun: toplanti tarihi (datetime.date)."""
    if ifade is None:
        return "-"
    ham = str(ifade).strip()
    if not ham or ham in ("-", "belirsiz", "?"):
        return "-"
    s = _sade(ham)
    gun = _gun_adi(s)
    haftaya = bool(re.search(r"\b(haftaya|gelecek hafta|onumuzdeki hafta|sonraki hafta)", s))
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            d = None
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
