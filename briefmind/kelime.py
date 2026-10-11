"""
kelime.py — Turkce kelime yardimcilari (terim.py ve duzelt.py ortak kullanir).

normalize: karsilastirma bicimi (kucuk harf, Turkce I/İ, noktalama atilir).
yaygin_mi: kelime Turkcede yaygin mi (veri/yaygin_kelimeler.txt: kok listesi, normalize; cekimli bicim koke indirgenir).
  Yaygin kelime terime "duzeltilmez" (ornek: 'komite' -> 'commit' reddedilir).
  Liste yoksa (eksik kurulum) bir kez uyari loglanir; liste_var_mi() False olur ve duzelt kapisi fail-closed calisir.
  Donmus exe'de PyInstaller _MEIPASS/veri ve exe yanindaki veri/ de denenir.
yaygin_mi_katli: ayni, ASCII yazilmis metin icin ('Musteri' -> müşteri).
turkce_gibi_mi: kok Turkce ses yapisina uyuyor mu (listede olmayan Turkce kelime: redif, jest).
terim_gibi_mi: terim olabilecek kisa ifade mi (cumle degil).
"""
import logging
import os
import re
import sys

VERI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "veri", "yaygin_kelimeler.txt")
_yaygin = None
_yaygin_katli = None
_eksik_uyarildi = False
LISTE_YOK_UYARI = ("yaygın kelime listesi (veri/yaygin_kelimeler.txt) bulunamadı ya da boş: "
                   "terim/ad düzeltmesinde yalnız büyük/küçük harf ve kesme farkı kabul edilir")
_ASCII = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def normalize(s):
    s = (s or "").replace("İ", "i").replace("I", "ı").lower()
    s = re.sub(r"['’`´]", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def terim_gibi_mi(t):
    """Terim olabilecek kisa ifade mi? (cumle degil)"""
    t = (t or "").strip()
    if not t or len(t) > 40 or len(t.split()) > 4:
        return False
    return t[-1] not in ".?!:;,"


def _veri_yollari():
    """VERI; yoksa donmus exe yerlesimi (PyInstaller _MEIPASS/veri ve exe yanindaki veri/)."""
    yollar = [VERI]
    ad = os.path.basename(VERI)
    for kok in (getattr(sys, "_MEIPASS", None),
                os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else None):
        if kok:
            yollar.append(os.path.join(kok, "veri", ad))
    return yollar


def _yukle():
    global _yaygin, _eksik_uyarildi
    if _yaygin is None:
        k = set()
        for yol in _veri_yollari():
            if not os.path.exists(yol):
                continue
            try:
                with open(yol, encoding="utf-8") as f:
                    for satir in f:
                        satir = satir.strip()
                        if satir and not satir.startswith("#"):
                            k.add(normalize(satir))
            except (OSError, UnicodeDecodeError):
                k = set()
            if k:
                break
        _yaygin = k
        if not k and not _eksik_uyarildi:
            _eksik_uyarildi = True                  # bir kez: kapi fail-closed calisir (duzelt.kapi 'liste_yok')
            logging.getLogger(__name__).warning(LISTE_YOK_UYARI)
    return _yaygin


def liste_var_mi():
    """Yaygin kelime listesi yuklendi mi? Yoksa duzelt kapisi yalniz harf/kesme farkini kabul eder."""
    return bool(_yukle())


def yeniden_yukle():
    """Liste onbellegini bosaltir (test ya da VERI degisince); eksik liste uyarisi yeniden verilebilir."""
    global _yaygin, _yaygin_katli, _eksik_uyarildi
    _yaygin = _yaygin_katli = None
    _eksik_uyarildi = False


def ascii_katla(s):
    """Turkce harfleri ASCII karsiligina indirger: 'Müşteri' -> 'musteri' (ASCII yazilmis davet metni icin)."""
    return normalize(s).translate(_ASCII)


def _katli_liste():
    global _yaygin_katli
    liste = _yukle()
    if _yaygin_katli is None or _yaygin_katli[0] is not liste:     # liste degistiyse (test yamasi) yeniden
        _yaygin_katli = (liste, frozenset(k.translate(_ASCII) for k in liste))
    return _yaygin_katli[1]


def yaygin_mi(kelime, kok=True, en_az_oran=0.6):
    """Normalize edilmis tek kelime yaygin Turkce kelime listesinde mi? Liste kok (lemma) listesidir;
    kok=True iken cekim ekleri atilmis kok de aranir ('komitede' -> 'komite'). Kok >=3 harf ve kelimenin
    en az 'en_az_oran'i olmali: 'cenkinse' -> 'cenk' + 'inse' gibi uydurma ayrim yaygin sayilmaz."""
    n = normalize(kelime)
    liste = _yukle()
    if n in liste:
        return True
    if not kok or " " in n:
        return False
    uzun = len(_sade(n))
    return any(k in liste and len(k) >= en_az_oran * uzun for k, _ in kok_ayir(n, en_kisa=3, esnek=True))


_KATLI_KARSI = {"i": "iı", "o": "oö", "u": "uü"}


def _katli_ekler(kok):
    """ASCII katli kokun alabilecegi eklerin katli bicimleri (son unlu i/ı, o/ö, u/ü belirsiz)."""
    unlu, unlu_son, sert = govde_baglami(kok)
    sonuc = set()
    for u in _KATLI_KARSI.get(unlu, unlu or "e"):
        sonuc.update(e.translate(_ASCII) for e in ekler((u, unlu_son, sert)))
    return sonuc


def yaygin_mi_katli(kelime, en_az_oran=0.6):
    """yaygin_mi ya da ASCII'ye katlanmis bicimi listede ('Musteri', 'Toplantida' -> yaygin): ASCII yazilmis
    davet metni icin. Katli kok de listede olmali, ek katli ek kumesinde ve kok kelimenin en az 'en_az_oran'i."""
    if yaygin_mi(kelime, en_az_oran=en_az_oran):
        return True
    n = ascii_katla(kelime)
    if not n or " " in n:
        return False
    liste = _katli_liste()
    if n in liste:
        return True
    for i in range(len(n) - 1, 2, -1):
        kok = n[:i]
        if i >= en_az_oran * len(n) and kok in liste and n[i:] in _katli_ekler(kok):
            return True
    return False


# ---------- Turkce ses yapisi (duzelt.py kapisi: Turkce kelime terime cevrilmez) ----------

_TR_HARF = set("abcçdefgğhıijklmnoöprsştuüvyz")
_SON_OBEK = {"rt", "rk", "rç", "rs", "lt", "lk", "lç", "nt", "nk", "nç", "st", "şt", "ft"}


def turkce_gibi_mi(s):
    """Kok Turkce ses yapisina uyuyor mu? (sozlukte olmasa da gercek Turkce kelime olabilir: redif, jest, sap)
    Yabanci kanit: q/w/x, kalin-ince unlu karisimi (api, kubernetis, komit), ilk hecede olmayan o/o, yan yana unlu,
    basta ya da ortada uc unsuz obegi, sonda Turkcede olmayan unsuz obegi (jenkins: 'ns'), bastaki unsuz obegi (sql)."""
    k = _sade(s)
    if len(k) < 2 or any(h not in _TR_HARF for h in k):
        return False
    unlu = [i for i, h in enumerate(k) if h in UNLULER]
    if not unlu:
        return False
    sesliler = [k[i] for i in unlu]
    if not (all(h in KALIN for h in sesliler) or all(h in INCE for h in sesliler)):
        return False
    if any(h in "oö" for h in sesliler[1:]):
        return False
    if any(b - a == 1 for a, b in zip(unlu, unlu[1:])):
        return False
    if len(k) >= 2 and k[0] not in UNLULER and k[1] not in UNLULER:
        return False
    obek = 0
    for h in k:
        obek = 0 if h in UNLULER else obek + 1
        if obek >= 3:
            return False
    if k[-1] not in UNLULER and k[-2] not in UNLULER and k[-2:] not in _SON_OBEK:
        return False
    return True


# ---------- Turkce ad cekimi ve ek uyumu (duzelt.py kapisi, yaygin kok kontrolu) ----------
# Ek kaliplari arsifonemle yazilir: A=a/e, I=ı/i/u/ü, D=d/t, C=c/ç, K=k/ğ; (y)(n)(s) kaynastirma
# unluyle biten govdede, (I) unsuz biten govdede gelir. Fiil cekimi yok: terimler ad soylu.

KALIN = "aıou"
INCE = "eiöü"
UNLULER = KALIN + INCE
SERT = "çfhkpsşt"
_DUZ = str.maketrans("âîû", "aiu")

_TUREME = ("CI", "lIK", "lI", "sIz")                           # yalniz ek kaybi tespiti icin
_COGUL = "lAr"
_IYELIK = ("(I)m", "(I)n", "(s)I", "(I)mIz", "(I)nIz", "lArI")
_IYELIK_3 = ("(s)I", "lArI")                                   # ardindan zamir n'si gelir
_HAL = ("(y)I", "(y)A", "DA", "DAn", "(n)In", "(y)lA", "CA")
_HAL_N = ("nI", "nA", "nDA", "nDAn", "nIn", "(y)lA", "nCA")    # 3. tekil/cogul iyelikten sonra
_KI_ONCESI = ("DA", "nDA", "(n)In", "nIn")
_EK_FIIL = ("DIr", "(y)DI", "(y)sA", "(y)mIş")
_ek_onbellek = {}


def _sade(s):
    return normalize(s).replace(" ", "").translate(_DUZ)


def son_unlu(s):
    """Kelimedeki son unlu (yoksa '')."""
    for h in reversed(_sade(s)):
        if h in UNLULER:
            return h
    return ""


def govde_baglami(kok):
    """(son_unlu, unluyle_biter, sert_unsuzle_biter) — ek uyumu icin."""
    k = _sade(kok)
    if not k:
        return ("", False, False)
    return (son_unlu(k), k[-1] in UNLULER, k[-1] in SERT)


def _gercekle(kalip, metin, durum, lik_once):
    """Tek ek kalibini metnin sonuna uyumla yazar. durum=(son_unlu, unluyle_biter, sert_biter)."""
    unlu, unlu_son, sert_son = durum
    parca = ""
    i = 0
    while i < len(kalip):
        if kalip[i] == "(":
            harf = kalip[i + 1]
            i += 3
            onceki_unlu = (parca[-1] in UNLULER) if parca else unlu_son
            if harf == "I":
                if onceki_unlu:
                    continue                    # unluden sonra (I) duser: kampanya-m
            else:
                if not onceki_unlu:
                    continue                    # unsuzden sonra (y)(n)(s) duser: rapor-u
                parca += harf
                continue
        else:
            harf = kalip[i]
            i += 1
        son_sert = (parca[-1] in SERT) if parca else sert_son
        kalin = unlu in KALIN
        yuvarlak = unlu in "ouöü"
        if harf == "A":
            h = "a" if kalin else "e"
        elif harf == "I":
            h = ("u" if kalin else "ü") if yuvarlak else ("ı" if kalin else "i")
        elif harf == "D":
            h = "t" if son_sert else "d"
        elif harf == "C":
            h = "ç" if son_sert else "c"
        elif harf == "K":
            h = "k"
        else:
            h = harf
        if h in UNLULER:
            if not parca and lik_once and metin.endswith("k"):
                metin = metin[:-1] + "ğ"         # -lIK + unlu: komiteliği
            unlu = h
        parca += h
    if parca:
        unlu_son, sert_son = parca[-1] in UNLULER, parca[-1] in SERT
    return metin + parca, (unlu, unlu_son, sert_son)


def _zincirler(tureme):
    """Izinli ek dizileri (kalip demetleri): [tureme] [cogul] [iyelik] [hal [ki]] [ek-fiil]."""
    dizi = []
    for t in ((None,) + _TUREME if tureme else (None,)):
        for c in (None, _COGUL):
            for iy in (None,) + _IYELIK:
                if c and iy == "lArI":
                    continue
                hal_listesi = _HAL_N if iy in _IYELIK_3 else _HAL
                for h in (None,) + hal_listesi:
                    for ki in ((None, "ki") if h in _KI_ONCESI else (None,)):
                        for ef in (None,) + _EK_FIIL:
                            dizi.append(tuple(x for x in (t, c, iy, h, ki, ef) if x))
    return dizi


def ekler(baglam, tureme=False):
    """Govde baglamina (govde_baglami) uyan butun ek yazimlari (bos ek dahil), kume."""
    anahtar = (baglam, tureme)
    if anahtar in _ek_onbellek:
        return _ek_onbellek[anahtar]
    unlu0, unlu_son0, sert_son0 = baglam
    durum0 = (unlu0 or "e", unlu_son0, sert_son0)            # unlusuz govde (SQL): ince varsayilir
    sonuc = set()
    for zincir in _zincirler(tureme):
        metin, durum, onceki = "", durum0, None
        for kalip in zincir:
            metin, durum = _gercekle(kalip, metin, durum, onceki == "lIK")
            onceki = kalip
        sonuc.add(metin)
    sonuc = frozenset(sonuc)
    _ek_onbellek[anahtar] = sonuc
    return sonuc


def ek_gecerli_mi(kok, ek, tureme=False, baglam=None, esnek=False):
    """'ek' (normalize, kesmesiz) 'kok' govdesine Turkce unlu/unsuz uyumuyla eklenebilir mi?
    esnek=True: karsi uyum da kabul (okunusu yazilisindan farkli alinti: pipeline'lar, terminale)."""
    if not ek:
        return True
    if baglam:
        return _sade(ek) in ekler(baglam, tureme)
    return _uyar_mi(kok, ek, tureme, esnek)


def arsifonem(ek):
    """Ekin uyumdan bagimsiz bicimi: 'lerinde' ve 'larında' -> 'lArIndA'."""
    tablo = str.maketrans({"a": "A", "e": "A", "ı": "I", "i": "I", "u": "I", "ü": "I",
                           "d": "D", "t": "D", "c": "C", "ç": "C", "ğ": "K", "k": "K"})
    return _sade(ek).translate(tablo)


_YUMUSAMA = {"b": "p", "c": "ç", "d": "t", "ğ": "k", "g": "k"}


_KARSI = str.maketrans("aıoueiöü", "eiöüaıou")


def _uyar_mi(kok, ek, tureme, esnek):
    b = govde_baglami(kok)
    if _sade(ek) in ekler(b, tureme):
        return True
    # esnek: ince 'l'li alintilar ('terminale', 'rolü') icin karsi uyum da kabul
    return esnek and bool(b[0]) and _sade(ek) in ekler((b[0].translate(_KARSI),) + b[1:], tureme)


def kok_ayir(kelime, tureme=False, en_kisa=2, esnek=False):
    """Kelimeyi (kok, ek) ciftlerine ayirir; ek bos degil ve koke uyumlu. Yumusama geri alinir
    ('kitabı' -> ('kitap', 'ı')). Kokler uzundan kisaya."""
    k = _sade(kelime)
    cift = []
    for i in range(len(k) - 1, en_kisa - 1, -1):
        kok, ek = k[:i], k[i:]
        if _uyar_mi(kok, ek, tureme, esnek):
            cift.append((kok, ek))
        if ek[:1] in UNLULER and kok[-1:] in _YUMUSAMA:
            sert = kok[:-1] + _YUMUSAMA[kok[-1]]
            if _uyar_mi(sert, ek, tureme, esnek):
                cift.append((sert, ek))
    return cift

