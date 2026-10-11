"""
terim.py — terim havuzu (sozlugun yerine, plan v3 §5). Metne KOR degisiklik yapmaz: terimler yalniz
Whisper prompt'una, duzelt.py beyaz listesine ve LLM baglamina ipucu olarak gider; metni degistirme
kararini duzelt.kapi verir.

Dosya (varsayilan <uygulama klasoru>/terimler/terimler.json):
  {"surum": 1, "terimler": [{"terim", "varyantlar", "kaynak", "seriler": {seri: sayi}, "genel", "ilk", "son",
                             "guven", "ogrenilen": [...], "genel_ogrenme": bool}],
   "gozlemler": [{"seri", "eski", "hedef", "toplantilar": [...], "ogrenildi", "geri_alindi"}],
   "gecmis": [{"t", "islem": "ekle|geri_al", "terim", "neden", ...}]}
Elle terimler: yanindaki elle.txt (satir basina 'Terim' ya da 'Terim | varyant1; varyant2'; '#' yorum).
  elle.txt dosyasi okunur, JSON'a yazilmaz; elle terimler her seride gecerlidir (genel).

Kaynaklar: davet (davetten_cikar), onceki not (notlardan_cikar), katilimci adi (kisi), ogrenme, elle.
Ogrenme (bagimsiz kanit): gozlem_ekle ile kapinin KABUL ettigi terim|ad duzeltmeleri sayilir; ogren() bir
  duzeltmeyi yalniz (1) ayni seride >=2 FARKLI toplantida goruldu, (2) hedef terim davette / onceki notta /
  katilimci adinda aciklikla geciyor, (3) eski kelime yaygin Turkce kelime degil, (4) eski kanit
  metinlerinde gecmiyor ise varyant yapar. LLM'in kendi tekrari kanit degildir (kanit anahtarlari sabit).
  genel=true yalniz >=5 farkli seride ogrenilince. Her ogrenme gecmis'e sayaclariyla yazilir; geri_al geri alir.

Kaydetme: ekle/gozlem_ekle bellekte kalir, kaydet() yazar; ogren ve geri_al kendileri kaydeder.
"""
import datetime as dt
import json
import os
import re
import sys
import threading

import kelime
from kelime import normalize, terim_gibi_mi

SURUM = 1
ADAY_TAVAN = 40                  # adaylar() en fazla bu kadar terim doner
PROMPT_KARAKTER = 380            # ~115 token (plan: <=120 token, ~400 karakter)
PROMPT_TERIM = 20
PROMPT_AD = 12
OGRENME_TOPLANTI = 2             # ayni seride bu kadar FARKLI toplanti
GENEL_SERI = 5                   # genel=true icin farkli seri sayisi
KANIT_ANAHTARLARI = ("davet", "onceki_not", "katilimci")
GECMIS_TAVAN = 2000
DAVET_TAVAN = 30


def uygulama_klasoru():
    """terimler/ klasorunun yeri (app.uygulama_klasoru ile ayni mantik; app.py Qt ice aktardigi icin kopya)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _simdi():
    return dt.datetime.now().isoformat(timespec="seconds")


def _bugun():
    return dt.date.today().isoformat()


# ---------------------------------------------------------------- metin yardimcilari

_TR = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def _tokenler(s):
    """Karsilastirma icin sade kelimeler: kucuk harf, Turkce harfler ASCII'ye, apostrof ve noktalama ayirac."""
    s = (s or "").replace("İ", "i").replace("I", "ı").lower()
    s = re.sub(r"['’`´]", " ", s)
    return re.findall(r"[^\W_]+", s.translate(_TR))


def _kokler(metin):
    """Turkce ekleri kabaca yok saymak icin kelimelerin ilk 5 harfi (motor.ilgili_terimler ile ayni)."""
    return {k[:5] for k in _tokenler(metin) if len(k) >= 3}


def ilgili(terimler, metin):
    """Terimlerden metinde gercekten gecenler (kok eslesmesi). motor.ilgili_terimler ile ayni davranis."""
    kokler = _kokler(metin)
    secilen = []
    for t in terimler:
        tk = _kokler(t)
        if tk and all(k in kokler for k in tk):
            secilen.append(t)
    return secilen


def _gecer(hedef, kaynak_tokenleri):
    """hedef (kelime listesi) kaynak token listesinde ardisik geciyor mu? Son kelime kisa bir ek alabilir
    ('jenkins e' ya da 'modulunde'); ara kelimeler birebir."""
    n = len(hedef)
    if not n:
        return False
    for i in range(len(kaynak_tokenleri) - n + 1):
        ok = True
        for j, h in enumerate(hedef):
            t = kaynak_tokenleri[i + j]
            if t == h:
                continue
            if j == n - 1 and len(h) >= 4 and t.startswith(h) and len(t) - len(h) <= 5:
                continue
            ok = False
            break
        if ok:
            return True
    return False


def _yaygin_ifade(eski):
    """eski yaygin Turkce kelime(ler) mi: tek kelime listede ya da tum kelimeler listede."""
    kel = [k for k in normalize(eski).split() if k]
    return bool(kel) and all(kelime.yaygin_mi(k) for k in kel)


# ---------------------------------------------------------------- davet / not ayiklama

# Kara liste (sentetik kalip listesi). Once KES: eslesen yerden sona kadar atilir (Teams blogu, imza, yasal
# uyari hep govdenin sonundadir); sonra SIL: URL, e-posta, telefon, kimlik yerinde silinir.
_KES = [re.compile(p, re.I) for p in (
    # Teams katilim blogu
    r"_{5,}", r"={8,}", r"-{8,}",
    r"microsoft\s+teams\s+(?:toplant|yardım|need\s+help|meeting|does\s+not)",
    r"toplantıya\s+(?:şimdi\s+)?katıl", r"join\s+(?:the\s+meeting|on\s+your\s+computer|with\s+a\s+video|conversation)",
    r"click\s+here\s+to\s+join", r"toplantı\s+kimliği", r"meeting\s+id\b", r"pass\s?code", r"\bparola\s*:",
    r"dial[\s-]?in", r"telefonla\s+(?:katıl|arama)", r"bilgisayarınızdan\s+veya", r"yardım\s+mı\s+gerekiyor",
    r"need\s+help\?", r"düzenleyiciler\s+için", r"for\s+organizers", r"toplantı\s+seçenekleri",
    r"meeting\s+options", r"video\s+konferans\s+cihaz", r"conference\s+id", r"konferans\s+kimliği",
    # yasal uyari
    r"yasal\s+(?:uyarı|bilgilendirme)", r"gizlilik\s+(?:uyarısı|bildirimi)", r"sorumluluk\s+reddi",
    r"\bdisclaimer\b", r"confidentiality\s+notice", r"intended\s+recipient", r"yanlışlıkla\s+size",
    r"this\s+(?:e-?mail|message|communication)\b[^.]{0,80}\b(?:confidential|privileged|intended)",
    r"bu\s+(?:e-?posta|ileti|mesaj)\b[^.]{0,80}\b(?:gizli|yalnızca|sadece|özeldir)",
    r"yetkisiz\s+(?:kişi|kullanım|ifşa)",
    # imza ve iletilen e-posta basligi
    r"(?:^|[.!?:\n])\s*(?:saygılarımla|saygılarımızla|saygılar|iyi\s+çalışmalar|iyi\s+günler|kolay\s+gelsin|"
    r"sevgiler|teşekkürler|teşekkür\s+ederim|best\s+regards|kind\s+regards|warm\s+regards|regards|thanks|"
    r"thank\s+you|sincerely|cheers)\b",
    r"original\s+message", r"\bgönderen\s*:", r"\bfrom\s*:\s", r"\bsent\s+from\b", r"outlook\s+for\s+(?:ios|android)",
)]
_SIL = [re.compile(p, re.I) for p in (
    r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+",                      # e-posta URL'den once (alan adi yarim kalmasin)
    r"(?:https?://|www\.)\S+",
    r"\b[\w-]+(?:\.[\w-]+)*\.(?:com|net|org|edu|gov|io|ms|tr|co)\b(?:/\S*)?",
    r"(?<![\w.])(?:\+\s?\d[\d\s().-]{6,}\d|\(?0?\d{3}\)?[\s.-]\d{3}[\s.-]\d{2}[\s.-]?\d{2}"
    r"|\d{3}[\s-]\d{3}[\s-]\d{3,4}(?:[\s-]\d{1,4})?|\d{9,})(?!\w)",
    r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b",
)]

# Toplanti/bicim kelimeleri terim degildir: tam eslesme ve onek (yalniz >=5 harfli kokler) listeleri
_DURAK_TAM = {
    "konu", "yer", "saat", "ek", "iyi", "hi", "hello", "dear", "merhaba", "selam", "sayin", "degerli", "gunaydin",
    "sync", "daily", "weekly", "monthly", "standup", "meeting", "review", "agenda", "call", "teams", "outlook", "zoom",
    "microsoft", "re", "fw", "fwd", "ynt", "pazartesi", "sali", "carsamba", "persembe", "cuma", "cumartesi", "pazar",
    "ocak", "subat", "mart", "nisan", "mayis", "haziran", "temmuz", "agustos", "eylul", "ekim", "kasim", "aralik",
    "final", "taslak", "draft", "copy", "kopya", "guncel", "son", "yeni", "notlar", "not", "ve", "ile", "icin",
    "tarih", "bilgi", "lutfen", "rica", "tesekkurler", "yarin", "bugun", "dun", "simdi", "ayrica", "ancak", "ama",
    "fakat", "eger", "cunku", "sonra", "once", "daha", "hem", "her", "bazi", "tum", "bu", "su", "bir",
}
_DURAK_ON = ("toplant", "gundem", "haftal", "aylik", "gunluk", "gorusme", "degerlendirme", "sunum",
             "bilgilendirme", "katilimci", "planlama", "dokuman", "workshop", "toplanti")
_SABIT_BASLIK = {"ozet", "konu akisi", "kararlar", "aksiyonlar", "acik sorular", "bir sonraki adim",
                 "kisiye gore is listesi", "notlar", "transkript", "katilimcilar", "gundem"}
_ETIKET = {"karar", "aksiyon", "sorumlu", "tarih", "not", "ozet", "dikkat", "onemli", "kaynak", "madde", "soru",
           "risk", "sonuc", "durum"}
_EK_GURULTU = re.compile(r"^(?:v\d+(?:\.\d+)*|rev\d*|r\d+|\d{6,8}|\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{2,4})$", re.I)
_EK_UZANTI = re.compile(r"\.(?:docx?|xlsx?|pptx?|pdf|msg|txt|csv|png|jpe?g|zip|vsdx?|md|json|xml|html?|eml)$", re.I)
_AYIRAC = re.compile(r"[\n\r!?;:|•·–—()\[\]{}\"“”]+|\.(?:\s+|$)|\s[-/]\s|,\s")


def _durak_mu(token):
    t = "".join(_tokenler(token))
    if not t:
        return True
    return t in _DURAK_TAM or (len(t) >= 5 and t.startswith(_DURAK_ON))


def davet_temizle(metin):
    """Davet gövdesinden Teams katilim metni, imza, yasal uyari, URL, e-posta, telefon, kimlik atilir."""
    m = (metin or "").replace("\xa0", " ")
    kes = len(m)
    for p in _KES:
        e = p.search(m)
        if e and e.start() < kes:
            kes = e.start()
    m = m[:kes]
    for p in _SIL:
        m = p.sub(" ", m)
    return re.sub(r"[ \t]+", " ", m).strip()


def _tur(tok):
    """Token turu: akronim | karma | baslik | sayi | None. tok apostrof eki atilmis kok."""
    if not tok or not any(c.isalnum() for c in tok):
        return None
    if tok.isdigit():
        return "sayi" if len(tok) <= 3 else None
    if re.fullmatch(r"v\d+(?:\.\d+)*", tok, re.I):
        return "sayi"
    if len(tok) >= 2 and tok == tok.upper() and tok != tok.lower():
        return "akronim"                                  # IFRS, PD, Q3
    if tok[0].isupper() and any(c.isupper() for c in tok[1:]) and any(c.islower() for c in tok):
        return "karma"                                    # SharePoint
    if tok[0].isupper():
        return "baslik"
    return None


def _ifadeler(metin, serbest=False):
    """Metindeki ozel ad benzeri ifadeler (buyuk harfli oebekler, kisaltmalar, 'IFRS 9').
    serbest: metin bir baslik (konu/ek adi): cumle basindaki tek buyuk harfli kelime de kabul edilir."""
    sonuc = []
    for parca in _AYIRAC.split(metin or ""):
        toklar = []
        for ham in parca.split():
            t = ham.strip(".,;:!?()[]{}\"“”‘")
            toklar.append((t, re.split(r"['’]", t)[0]))
        faz = []        # (indeks, ham, kok, tur)
        i = 0
        while i <= len(toklar):
            ek = None
            if i < len(toklar):
                ham, kok = toklar[i]
                tur = _tur(kok)
                if tur == "sayi":
                    tur = "sayi" if faz and faz[-1][3] != "sayi" else None
                ek = (i, ham, kok, tur) if tur else None
            if ek:
                faz.append(ek)
                if ek[1] != ek[2]:                 # apostrof eki ("Jenkins'te"): ifade burada biter
                    sonuc.extend(_faz_ifade(faz, serbest))
                    faz = []
            else:
                sonuc.extend(_faz_ifade(faz, serbest))
                faz = []
            i += 1
    return sonuc


def _faz_ifade(faz, serbest):
    """Ardisik terim tokenlerinden bir ifade uretir (bos olabilir)."""
    # kenarlardaki durak kelimeler ve sahipsiz sayilar atilir ('Merhaba Ali', 'IFRS 9 Toplantısı')
    degisti = True
    while faz and degisti:
        degisti = False
        if faz[0][3] == "sayi" or _durak_mu(faz[0][2]):
            faz, degisti = faz[1:], True
        elif not serbest and faz[0][0] == 0 and faz[0][3] == "baslik" and len(faz) > 1 \
                and kelime.yaygin_mi(faz[0][2]) and not _tamlama_mi(faz):
            faz, degisti = faz[1:], True              # cumle basindaki yaygin kelime ('Yarın Ali ...')
        elif faz[-1][3] == "sayi":
            if len(faz) < 2 or _durak_mu(faz[-2][2]):
                faz, degisti = faz[:-1], True
        elif _durak_mu(faz[-1][2]):
            faz, degisti = faz[:-1], True
    if not faz:
        return []
    ifade = " ".join(f[2] for f in faz)                   # kok biciminde: apostrof ekleri atilmis
    if len(faz) == 1:
        _, _, kok, tur = faz[0]
        if len(kok) < 2:
            return []
        if tur == "baslik":
            if len(kok) < 3 or (kelime.yaygin_mi(kok) and not kok.isupper()):
                return []
            if not serbest and faz[0][0] == 0 and not _cumle_basi_ozel_mi(faz[0][1], kok):
                return []                          # cumle basindaki tek buyuk harfli kelime: ozel ad olmayabilir
    return [ifade] if terim_gibi_mi(ifade) else []


def _cumle_basi_ozel_mi(ham, kok):
    """Cumle basindaki tek buyuk harfli kelime ozel ad mi? Yaygin Turkce kelime degil (ASCII yazim dahil:
    'Musteri', 'Toplantida') ve en az 4 harf ya da kesmeyle ek almis ('Jenkins'te', 'Kubernetes')."""
    if kelime.yaygin_mi_katli(kok, en_az_oran=0.4):
        return False
    return len(kok) >= 4 or ham != kok


def _tamlama_mi(faz):
    """Cumle basinda iki buyuk harfli kelimelik isim tamlamasi mi ('Kampanya Modülü', 'Kampanya Modulu')?
    Ikinci kelime iyelik ekli (-(s)I) bir yaygin kok; kendisi listede kok olan ad ('Yarın Ali') degil."""
    if len(faz) != 2 or faz[1][3] != "baslik" or faz[1][1] != faz[1][2]:
        return False
    n = kelime.ascii_katla(faz[1][2])
    liste = kelime._katli_liste()
    if n in liste:
        return False
    for i in (1, 2):
        ek, kok = n[-i:], n[:-i]
        if ek in ("i", "u", "si", "su") and len(kok) >= 3 and kok in liste:
            return True
    return False


def _ek_ifadesi(ad):
    """Ek dosya adindan terim: 'Kampanya_Modulu_v3_final.docx' -> 'Kampanya Modulu'."""
    ad = os.path.basename((ad or "").replace("\\", "/")).strip()
    ad = _EK_UZANTI.sub("", ad)
    ad = re.sub(r"(?<=[a-zçğıöşü])(?=[A-ZÇĞİÖŞÜ])", " ", ad)
    ad = re.sub(r"[_+]+|(?<!\d)[.-]+|[.-]+(?!\d)", " ", ad)
    toklar = [t for t in ad.split() if t and not _EK_GURULTU.match(t)]
    while toklar and _durak_mu(toklar[0]):
        toklar = toklar[1:]
    while toklar and _durak_mu(toklar[-1]):
        toklar = toklar[:-1]
    ifade = " ".join(toklar)
    if not ifade or re.fullmatch(r"[\d\W]+", ifade) or not terim_gibi_mi(ifade):
        return None
    if len(toklar) == 1 and kelime.yaygin_mi(toklar[0]) and not toklar[0][:1].isupper():
        return None
    return ifade


def _gecerli(t):
    t = (t or "").strip()
    if not terim_gibi_mi(t) or len(normalize(t)) < 2 or re.fullmatch(r"[\d\W]+", t):
        return False
    kel = t.split()
    if len(kel) == 1 and kelime.yaygin_mi(t) and t == t.lower():
        return False                                # kucuk harfli yaygin kelime terim olmaz
    return True


def _tekil(liste, tavan):
    gorulen, cikti = set(), []
    for t in liste:
        n = normalize(t)
        if n and n not in gorulen:
            gorulen.add(n)
            cikti.append(t)
        if len(cikti) >= tavan:
            break
    return cikti


def davetten_cikar(konu, govde, ek_adlari=()):
    """Outlook davetinden terim adaylari: konu, govde (kara liste temizlenmis) ve ek dosya adlari."""
    konu = re.sub(r"^\s*(?:(?:re|fw|fwd|ynt|iletilen)\s*:\s*)+", "", konu or "", flags=re.I)
    sonuc = _ifadeler(davet_temizle(konu), serbest=True)
    sonuc += _ifadeler(davet_temizle(govde), serbest=False)
    for ad in ek_adlari or ():
        t = _ek_ifadesi(ad)
        if t:
            sonuc.append(t)
    return _tekil([t for t in sonuc if _gecerli(t)], DAVET_TAVAN)


def notlardan_cikar(not_md):
    """Onceki not.md'den terim adaylari: basliklar (tarih ve sabit not basliklari haric) ve **kalin** terimler."""
    sonuc = []
    for satir in (not_md or "").splitlines():
        s = satir.strip()
        b = re.match(r"^#{1,6}\s+(.*)$", s)
        if b:
            h = re.sub(r"\s+[—–-]\s+\d{4}-\d{2}-\d{2}.*$", "", b.group(1)).strip()
            if " ".join(_tokenler(h)) in _SABIT_BASLIK:
                continue
            sonuc += _ifadeler(h, serbest=True)
        for k in re.findall(r"\*\*(.+?)\*\*|__(.+?)__", s):
            t = (k[0] or k[1]).strip()
            etiket = t.endswith(":")
            t = t.rstrip(":").strip()
            if not t or etiket or " ".join(_tokenler(t)) in _ETIKET or re.fullmatch(r"[\d\W]+", t):
                continue
            toklar = t.split()
            if not any(_tur(re.split(r"['’]", x.strip(".,;:!?"))[0]) in ("akronim", "karma", "baslik") for x in toklar):
                continue                           # kucuk harfli kalin ifade ('kritik risk') ozel ad degil
            if all(_durak_mu(x) or x.strip(".,").isdigit() for x in toklar):
                continue                           # tarih/gun adi ('15 Ekim') ya da toplanti kelimesi
            sonuc.append(re.split(r"['’]", t)[0] if len(toklar) == 1 else t)
    return _tekil([t for t in sonuc if _gecerli(t)], DAVET_TAVAN)


# ---------------------------------------------------------------- havuz

_KAYNAK_GUVEN = {"elle": 1.0, "davet": 0.8, "kisi": 0.8, "not": 0.7, "aktar": 0.7, "ogrenme": 0.6}


class TerimHavuzu:
    def __init__(self, yol=None):
        self.yol = yol or os.path.join(uygulama_klasoru(), "terimler", "terimler.json")
        self.elle_yol = os.path.join(os.path.dirname(self.yol), "elle.txt")
        self._kilit = threading.RLock()
        self._kirli = False
        self._idx = None
        self._elle_onb = (None, [])                # (mtime, [(terim, [varyant])])
        self.terimler = []
        self.gozlemler = []
        self.gecmis = []
        self.yukleme_hatasi = None
        self._yukle()

    # ---------- dosya ----------
    def _yukle(self):
        if not os.path.exists(self.yol):
            return
        try:
            with open(self.yol, encoding="utf-8") as f:
                v = json.load(f)
            if not isinstance(v, dict):
                raise ValueError("terimler.json bir nesne degil")
            for e in v.get("terimler") or []:
                if isinstance(e, dict) and str(e.get("terim") or "").strip():
                    self.terimler.append(self._giris(e))
            self.gozlemler = [g for g in v.get("gozlemler") or [] if isinstance(g, dict) and g.get("hedef")]
            self.gecmis = [g for g in v.get("gecmis") or [] if isinstance(g, dict)]
        except Exception as e:
            # bozuk dosyayi ezme: yana al, bos havuzla basla
            self.yukleme_hatasi = str(e)
            self.terimler, self.gozlemler, self.gecmis = [], [], []
            try:
                os.replace(self.yol, f"{self.yol}.bozuk-{dt.datetime.now():%Y%m%d-%H%M%S}")
            except OSError:
                pass

    @staticmethod
    def _giris(e):
        seriler = e.get("seriler") if isinstance(e.get("seriler"), dict) else {}
        return {"terim": str(e["terim"]).strip(),
                "varyantlar": [str(x) for x in e.get("varyantlar") or []],
                "kaynak": [str(x) for x in e.get("kaynak") or []],
                "seriler": {str(k): int(v) for k, v in seriler.items() if isinstance(v, (int, float))},
                "genel": bool(e.get("genel")),
                "ilk": e.get("ilk") or _bugun(), "son": e.get("son") or _bugun(),
                "guven": float(e.get("guven", 0.5)),
                "ogrenilen": [str(x) for x in e.get("ogrenilen") or []],
                "genel_ogrenme": bool(e.get("genel_ogrenme"))}

    def kaydet(self, zorla=False):
        """Atomik yazim (gecici dosya + os.replace). Degisiklik yoksa yazmaz."""
        with self._kilit:
            if not (self._kirli or zorla):
                return False
            klasor = os.path.dirname(self.yol)
            if klasor:
                os.makedirs(klasor, exist_ok=True)
            veri = {"surum": SURUM, "terimler": self.terimler, "gozlemler": self.gozlemler,
                    "gecmis": self.gecmis[-GECMIS_TAVAN:]}
            gecici = self.yol + ".tmp"
            with open(gecici, "w", encoding="utf-8") as f:
                json.dump(veri, f, ensure_ascii=False, indent=1)
            os.replace(gecici, self.yol)
            self._kirli = False
            return True

    def _elle(self):
        try:
            mt = os.stat(self.elle_yol).st_mtime
        except OSError:
            self._elle_onb = (None, [])
            return []
        if self._elle_onb[0] != mt:
            liste = []
            try:
                with open(self.elle_yol, encoding="utf-8-sig") as f:
                    for satir in f:
                        satir = satir.strip()
                        if not satir or satir.startswith("#"):
                            continue
                        t, _, v = satir.partition("|")
                        t = t.strip()
                        if _gecerli(t):
                            liste.append((t, [x.strip() for x in v.split(";") if x.strip()]))
            except OSError:
                pass
            self._elle_onb = (mt, liste)
        return self._elle_onb[1]

    # ---------- icerik ----------
    def _dizin(self):
        if self._idx is None:
            self._idx = {normalize(e["terim"]): e for e in self.terimler}
        return self._idx

    def _bul(self, terim):
        return self._dizin().get(normalize(terim))

    def _gorunum(self):
        """Havuz + elle.txt: elle terimler genel ve guven 1.0; havuza yazilmaz."""
        elle = self._elle()
        if not elle:
            return list(self.terimler)
        gor = {normalize(e["terim"]): e for e in self.terimler}
        for t, vary in elle:
            n = normalize(t)
            e = gor.get(n)
            if e is None:
                gor[n] = {"terim": t, "varyantlar": list(vary), "kaynak": ["elle"], "seriler": {}, "genel": True,
                          "ilk": "", "son": "", "guven": 1.0, "ogrenilen": [], "genel_ogrenme": False}
            else:
                e = dict(e, genel=True, guven=1.0, kaynak=list(e["kaynak"]) + ["elle"],
                         varyantlar=list(e["varyantlar"]) + [x for x in vary if x not in e["varyantlar"]])
                gor[n] = e
        return list(gor.values())

    def _gecmis_yaz(self, islem, terim, neden, **ek):
        self.gecmis.append(dict({"t": _simdi(), "islem": islem, "terim": terim, "neden": neden}, **ek))

    def ekle(self, seri, terimler, kaynak="davet"):
        """Terimleri havuza ekler (yoksa) ve seri sayacini artirir. Yeni eklenenleri dondurur. Kaydetmez."""
        eklenen = []
        with self._kilit:
            bugun = _bugun()
            for t in terimler or []:
                t = (t or "").strip()
                if not _gecerli(t):
                    continue
                e = self._bul(t)
                if e is None:
                    e = {"terim": t, "varyantlar": [], "kaynak": [kaynak], "seriler": {}, "genel": False,
                         "ilk": bugun, "son": bugun, "guven": _KAYNAK_GUVEN.get(kaynak, 0.5),
                         "ogrenilen": [], "genel_ogrenme": False}
                    self.terimler.append(e)
                    self._idx = None
                    self._gecmis_yaz("ekle", t, kaynak, tur="terim", seri=seri or "")
                    eklenen.append(t)
                else:
                    if kaynak not in e["kaynak"]:
                        e["kaynak"].append(kaynak)
                    e["son"] = bugun
                    e["guven"] = max(e["guven"], _KAYNAK_GUVEN.get(kaynak, 0.5))
                if seri:
                    e["seriler"][seri] = e["seriler"].get(seri, 0) + 1
            if terimler:
                self._kirli = True
        return eklenen

    def varyantlar(self, terim):
        with self._kilit:
            for e in self._gorunum():
                if normalize(e["terim"]) == normalize(terim):
                    return list(e["varyantlar"])
        return []

    def adaylar(self, seri, metin=None, adlar=()):
        """LLM/duzelt beyaz listesi (<=40): bu serinin terimleri + genel terimler + (metin verilirse) baska
        serilerden metinde kok eslesmesiyle gecenler. Metin verilirse eslesenler basa gelir. Katilimci adinin
        kendisi olan terimler atilir (adlar ayri gider). Yalniz metinde gecenleri isteyen ilgili(adaylar, metin) kullanir."""
        adn = {" ".join(_tokenler(a)) for a in adlar or ()}
        kokler = _kokler(metin) if metin else None
        sirali = []
        with self._kilit:
            for e in self._gorunum():
                t = e["terim"]
                if " ".join(_tokenler(t)) in adn or not terim_gibi_mi(t):
                    continue
                seri_sayi = e["seriler"].get(seri, 0) if seri else 0
                esle = False
                if kokler:
                    tk = _kokler(t)
                    esle = bool(tk) and all(k in kokler for k in tk)
                if not (seri_sayi or e["genel"] or esle):
                    continue
                sirali.append((0 if esle else 1, 0 if seri_sayi else 1, -seri_sayi, -e["guven"], t))
        sirali.sort()
        return [x[-1] for x in sirali[:ADAY_TAVAN]]

    def prompt(self, seri, adlar=(), onceki=""):
        """Whisper prompt'u (<=~380 karakter): katilimci adlari + <=20 ilgili terim + (varsa) onceki cumlenin sonu.
        Dusuk RMS klibe verilmemesi cagiranin isidir (sessiz klipte prompt ciktiya sizar)."""
        isimler = []
        for a in adlar or ():
            a = re.sub(r"\s+", " ", str(a or "")).strip()
            if a and a not in ("?", "-", "ben", "Ben") and a not in isimler and "?" not in a:
                isimler.append(a)
        isimler = isimler[:PROMPT_AD]
        onceki = re.sub(r"\s+", " ", onceki or "").strip()
        rezerv = min(80, len(onceki)) if onceki else 0
        sinir = PROMPT_KARAKTER - rezerv
        bas = ""
        parca = ""
        for a in isimler:
            deneme = (parca + ", " if parca else "Katılımcılar: ") + a
            if len(deneme) + 1 > min(170, sinir):
                break
            parca = deneme
        if parca:
            bas = parca + "."
        terimler = self.adaylar(seri, onceki or None, isimler)[:PROMPT_TERIM]
        parca = ""
        for t in terimler:
            deneme = (parca + ", " if parca else "Terimler: ") + t
            if len(bas) + 1 + len(deneme) + 1 > sinir:
                break
            parca = deneme
        if parca:
            bas = (bas + " " if bas else "") + parca + "."
        kalan = PROMPT_KARAKTER - len(bas) - (1 if bas else 0)
        if onceki and kalan >= 30:
            kuyruk = onceki[-kalan:]
            if len(onceki) > kalan:
                kuyruk = kuyruk.split(" ", 1)[1] if " " in kuyruk else ""
            if kuyruk:
                bas = (bas + " " if bas else "") + kuyruk
        return bas[:PROMPT_KARAKTER]

    # ---------- ogrenme ----------
    def _hedef_coz(self, yeni):
        """Duzeltmenin 'yeni' (ekli) biciminden terim kokunu bulur: havuzdaki en uzun eslesen terim, yoksa
        apostrof oncesi."""
        yt = _tokenler(yeni)
        en = None
        for e in self._gorunum():
            tt = _tokenler(e["terim"])
            if tt and _gecer(tt, yt[:len(tt)]) and len(yt) >= len(tt):
                if en is None or len(tt) > len(_tokenler(en)):
                    en = e["terim"]
        if en:
            return en
        kok = re.split(r"['’]", (yeni or "").strip())[0].strip(" .,;:!?")
        return kok if terim_gibi_mi(kok) else None

    def gozlem_ekle(self, seri, toplanti_id, eski, yeni, neden):
        """Kapinin KABUL ettigi duzeltmeyi kaydeder (ogrenme adayi). Yalniz neden terim|ad sayilir.
        Ayni toplantinin tekrari tek toplanti sayilir. Kaydetmez; ogren() sonunda yazilir."""
        if neden not in ("terim", "ad") or not seri or toplanti_id in (None, ""):
            return False
        eski, yeni = (eski or "").strip(), (yeni or "").strip()
        if not eski or not yeni:
            return False
        with self._kilit:
            hedef = self._hedef_coz(yeni)
            if not hedef or normalize(eski) == normalize(hedef):
                return False
            g = self._grup(seri, eski, hedef)
            if g is None:
                g = {"seri": seri, "eski": eski, "hedef": hedef, "toplantilar": [], "sayi": 0}
                self.gozlemler.append(g)
            g["sayi"] = g.get("sayi", 0) + 1
            tid = str(toplanti_id)
            if tid not in g["toplantilar"]:
                g["toplantilar"] = (g["toplantilar"] + [tid])[-20:]
            self._kirli = True
        return True

    def _grup(self, seri, eski, hedef):
        ne, nh = normalize(eski), normalize(hedef)
        for g in self.gozlemler:
            if g.get("seri") == seri and normalize(g.get("eski", "")) == ne and normalize(g["hedef"]) == nh:
                return g
        return None

    @staticmethod
    def _kanit_tokenleri(kanit):
        """Bagimsiz kanit metinleri -> token listeleri. Yalniz davet / onceki_not / katilimci anahtarlari
        kabul edilir (duzeltilmis transkript ya da LLM ciktisi kanit degildir). Sozluk disi bicim
        (liste/metin): cagiran bunlarin bagimsiz kaynak oldugunu soyler."""
        metinler = []

        def ekle(v):
            if isinstance(v, str):
                metinler.append(v)
            elif isinstance(v, (list, tuple, set)):
                for x in v:
                    ekle(x)
        if isinstance(kanit, dict):
            for k in KANIT_ANAHTARLARI:
                ekle(kanit.get(k))
        else:
            ekle(kanit)
        return [_tokenler(m) for m in metinler if m]

    def ogren(self, seri, toplanti_id, kanit_kaynaklari):
        """Toplanti sonunda: bu toplantida gorulen duzeltmelerden bagimsiz kanit kuralini saglayanlar varyant
        olur. kanit_kaynaklari: {"davet": metin, "onceki_not": metin, "katilimci": [adlar]}. Dondurur:
        [(eski, terim), ...] bu cagrida ogrenilenler. Kaydeder."""
        ogrenilen = []
        kanitlar = self._kanit_tokenleri(kanit_kaynaklari)
        tid = str(toplanti_id)
        with self._kilit:
            for g in self.gozlemler:
                if g.get("seri") != seri or g.get("ogrenildi") or g.get("geri_alindi"):
                    continue
                if tid not in g["toplantilar"] or len(set(g["toplantilar"])) < OGRENME_TOPLANTI:
                    continue
                if _yaygin_ifade(g["eski"]):
                    continue
                ht, et = _tokenler(g["hedef"]), _tokenler(g["eski"])
                if not any(_gecer(ht, k) for k in kanitlar):
                    continue                       # hedef bagimsiz kaynakta gecmiyor
                if any(_gecer(et, k) for k in kanitlar):
                    continue                       # eski de kaynakta geciyor: gercek bir kelime/ad olabilir
                kesmesiz = re.split(r"['’]", g["eski"])[0]
                kt = _tokenler(kesmesiz)
                if kt != et and any(_gecer(kt, k) for k in kanitlar):
                    continue                       # eki atilmis eski kaynakta geciyor (Sercan'ın: katilimci Sercan)
                diger = self._bul(kesmesiz)
                if diger is not None and normalize(diger["terim"]) != normalize(g["hedef"]):
                    continue                       # eski havuzdaki baska bir terim: dogru olani baskasina cevirmez
                sonuc = self._varyant_yap(seri, g)
                if sonuc:
                    ogrenilen.append(sonuc)
            self.kaydet()
        return ogrenilen

    def _varyant_yap(self, seri, g):
        e = self._bul(g["hedef"])
        if e is None:
            self.ekle(seri, [g["hedef"]], "ogrenme")
            e = self._bul(g["hedef"])
            if e is None:
                return None
        g["ogrenildi"] = True
        self._kirli = True
        if "ogrenme" not in e["kaynak"]:
            e["kaynak"].append("ogrenme")
        e["seriler"].setdefault(seri, 1)
        e["son"] = _bugun()
        yeni_varyant = normalize(g["eski"]) not in {normalize(v) for v in e["varyantlar"]}
        if yeni_varyant:
            e["varyantlar"].append(g["eski"])
            e["ogrenilen"].append(g["eski"])
            e["guven"] = min(1.0, e["guven"] + 0.05)
        seriler = {x["seri"] for x in self.gozlemler
                   if x.get("ogrenildi") and normalize(x["hedef"]) == normalize(e["terim"])}
        # varyant baska seride zaten ogrenilmisse yalniz kanit sayaci yazilir (genel yukseltmesi icin seri sayilir)
        self._gecmis_yaz("ekle", e["terim"], "ogrenme", tur="varyant" if yeni_varyant else "kanit",
                         eski=g["eski"], seri=seri,
                         sayac={"toplanti": len(set(g["toplantilar"])), "gozlem": g.get("sayi", 0),
                                "seri": len(seriler)})
        if len(seriler) >= GENEL_SERI and not e["genel"] and not e.get("genel_engelli"):
            e["genel"] = True
            e["genel_ogrenme"] = True
            self._gecmis_yaz("ekle", e["terim"], "ogrenme", tur="genel", sayac={"seri": len(seriler)})
        return (g["eski"], e["terim"]) if yeni_varyant else None

    def geri_al(self, terim):
        """Bir terimin ogrenmelerini geri alir: ogrenilen varyantlar silinir, ogrenmeyle gelen genel kalkar,
        yalniz ogrenmeyle girmis terim silinir, ayni (eski -> terim) bir daha ogrenilmez. Kaydeder.
        Dondurur {"terim", "varyantlar", "genel_geri", "silindi"} ya da terim yoksa None."""
        with self._kilit:
            e = self._bul(terim)
            if e is None:
                return None
            n = normalize(e["terim"])
            kal = [v for v in e["varyantlar"] if v not in e["ogrenilen"]]
            kaldirilan = [v for v in e["varyantlar"] if v in e["ogrenilen"]]
            e["varyantlar"] = kal
            e["ogrenilen"] = []
            genel_geri = bool(e.get("genel_ogrenme"))
            if genel_geri:
                e["genel"] = False
                e["genel_ogrenme"] = False
                e["genel_engelli"] = True
            engellenen = 0
            for g in self.gozlemler:
                if normalize(g["hedef"]) == n and not g.get("geri_alindi"):
                    g["geri_alindi"] = True
                    engellenen += 1
            silindi = False
            if "ogrenme" in e["kaynak"]:
                e["kaynak"] = [k for k in e["kaynak"] if k != "ogrenme"]
                if not e["kaynak"]:
                    self.terimler.remove(e)
                    self._idx = None
                    silindi = True
            if kaldirilan or genel_geri or silindi or engellenen:
                self._gecmis_yaz("geri_al", e["terim"], "elle", varyantlar=kaldirilan, genel=genel_geri,
                                 silindi=silindi)
                self._kirli = True
                self.kaydet()
            return {"terim": e["terim"], "varyantlar": kaldirilan, "genel_geri": genel_geri, "silindi": silindi}
