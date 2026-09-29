"""
outlook.py — Outlook takviminden aktif toplantiyi okur, taslak e-posta acar.
pip install pywin32   (Outlook masaustunde acik/kurulu olmali)
"""
import datetime as dt
import re


def _outlook():
    import win32com.client
    return win32com.client.Dispatch("Outlook.Application")


def _katilimci_listesi(*alanlar):
    adlar = []
    for alan in alanlar:
        for ad in (alan or "").split(";"):
            ad = re.sub(r"\s*\(.*?\)\s*$", "", ad).strip()
            if ad and ad not in adlar:
                adlar.append(ad)
    return adlar


def _benzerlik(a, b):
    import difflib
    import re as _re
    def nrm(x):
        x = (x or "").lower()
        for k, v in zip("çğıöşü", "cgiosu"):
            x = x.replace(k, v)
        return _re.sub(r"[^a-z0-9 ]", " ", x).split()
    A, B = nrm(a), nrm(b)
    if not A or not B:
        return 0.0
    ortak = len(set(A) & set(B)) / min(len(set(A)), len(set(B)))          # kelime ortakligi
    dizi = difflib.SequenceMatcher(None, " ".join(A), " ".join(B)).ratio()  # metin benzerligi
    return max(ortak, dizi)


def aday_toplantilar(tolerans_dk=20):
    """Su an (±tolerans) suren TUM takvim kayitlarini dondurur (paralel toplantilar icin)."""
    adaylar = []
    try:
        ns = _outlook().GetNamespace("MAPI")
        takvim = ns.GetDefaultFolder(9).Items
        takvim.IncludeRecurrences = True
        takvim.Sort("[Start]")
        bugun = dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        yarin = bugun + dt.timedelta(days=1)
        kayitlar = None
        for fmt in ("%d.%m.%Y %H:%M", "%m/%d/%Y %H:%M"):
            try:
                kayitlar = takvim.Restrict(
                    f"[Start] >= '{bugun.strftime(fmt)}' AND [Start] < '{yarin.strftime(fmt)}'")
                if kayitlar.Count > 0:
                    break
            except Exception:
                kayitlar = None
        if not kayitlar:
            return []
        simdi = dt.datetime.now()
        tol = dt.timedelta(minutes=tolerans_dk)
        for k in kayitlar:
            try:
                bas = dt.datetime(k.Start.year, k.Start.month, k.Start.day, k.Start.hour, k.Start.minute)
                bit = dt.datetime(k.End.year, k.End.month, k.End.day, k.End.hour, k.End.minute)
            except Exception:
                continue
            if bas - tol <= simdi <= bit + tol:
                govde = re.sub(r"\s+", " ", (k.Body or "")).strip()
                adaylar.append({"baslik": k.Subject or "Toplantı",
                                "katilimcilar": _katilimci_listesi(k.Organizer, k.RequiredAttendees,
                                                                   k.OptionalAttendees),
                                "gundem": govde[:1500], "baslangic": bas, "bitis": bit})
    except Exception:
        return []
    adaylar.sort(key=lambda a: a["baslangic"])
    return adaylar


def toplanti_esle(teams_adi, tolerans_dk=20):
    """Teams penceresindeki toplanti adini takvimdeki adaylarla eslestirir.
    Dondurur (secilen|None, adaylar, skor). Emin degilse secilen None olur, UI sorar."""
    adaylar = aday_toplantilar(tolerans_dk)
    if not adaylar:
        return None, [], 0.0
    if not teams_adi:
        return (adaylar[0] if len(adaylar) == 1 else None), adaylar, 0.0
    skorlu = sorted(((_benzerlik(teams_adi, a["baslik"]), a) for a in adaylar),
                    key=lambda x: x[0], reverse=True)
    en_iyi, ikinci = skorlu[0], (skorlu[1] if len(skorlu) > 1 else (0.0, None))
    if en_iyi[0] >= 0.55 and en_iyi[0] - ikinci[0] >= 0.15:
        return en_iyi[1], adaylar, en_iyi[0]
    # Teams'teki ad biliniyor ama takvimdeki hicbir kayda benzemiyor: otomatik secme, sor.
    # (Yanlis toplantinin gundemi/katilimcilari nota karisiyordu.)
    return None, adaylar, en_iyi[0]


def aktif_toplanti(tolerans_dk=15):
    """Geriye donuk uyum: su an suren ilk takvim kaydi."""
    try:
        ns = _outlook().GetNamespace("MAPI")
        takvim = ns.GetDefaultFolder(9).Items
        takvim.IncludeRecurrences = True
        takvim.Sort("[Start]")
        bugun = dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        yarin = bugun + dt.timedelta(days=1)
        kayitlar = None
        for fmt in ("%d.%m.%Y %H:%M", "%m/%d/%Y %H:%M"):        # TR ve US tarih bicimleri
            try:
                kayitlar = takvim.Restrict(
                    f"[Start] >= '{bugun.strftime(fmt)}' AND [Start] < '{yarin.strftime(fmt)}'")
                if kayitlar.Count > 0:
                    break
            except Exception:
                kayitlar = None
        if not kayitlar:
            return None
        simdi = dt.datetime.now()
        tol = dt.timedelta(minutes=tolerans_dk)
        for k in kayitlar:
            try:
                bas = dt.datetime(k.Start.year, k.Start.month, k.Start.day, k.Start.hour, k.Start.minute)
                bit = dt.datetime(k.End.year, k.End.month, k.End.day, k.End.hour, k.End.minute)
            except Exception:
                continue
            if bas - tol <= simdi <= bit + tol:
                govde = re.sub(r"\s+", " ", (k.Body or "")).strip()
                return {"baslik": k.Subject or "Toplantı",
                        "katilimcilar": _katilimci_listesi(k.Organizer, k.RequiredAttendees, k.OptionalAttendees),
                        "gundem": govde[:1500],
                        "baslangic": bas, "bitis": bit}
    except Exception:
        return None
    return None


def taslak(konu, govde, alicilar=None):
    """Gondermez; Outlook'ta taslagi acar. alicilar: ad listesi (Outlook adres defterinden cozer)."""
    try:
        mail = _outlook().CreateItem(0)
        mail.Subject = konu
        mail.Body = govde
        if alicilar:
            mail.To = "; ".join(alicilar)
        mail.Display()
        return True
    except Exception as e:
        print(f"Outlook taslağı açılamadı: {e}")
        return False


def kullanici_adi():
    """Uygulamayi acan kisinin adi: once Outlook (takvimdeki katilimci yazimiyla ayni),
    sonra Windows hesabinin gorunen adi, en son oturum adi."""
    try:
        ad = _outlook().Session.CurrentUser.Name
        if ad and "@" not in ad:
            return ad.strip()
    except Exception:
        pass
    try:
        import ctypes
        boy = ctypes.c_ulong(0)
        ctypes.windll.secur32.GetUserNameExW(3, None, ctypes.byref(boy))       # NameDisplay
        tampon = ctypes.create_unicode_buffer(boy.value + 1)
        if ctypes.windll.secur32.GetUserNameExW(3, tampon, ctypes.byref(boy)):
            ad = tampon.value.strip()
            if "," in ad:                                       # "Erentürk, Mert Ali" -> "Mert Ali Erentürk"
                soy, isim = [x.strip() for x in ad.split(",", 1)]
                ad = f"{isim} {soy}"
            if ad:
                return ad
    except Exception:
        pass
    try:
        import os
        return os.getlogin()
    except Exception:
        return ""
