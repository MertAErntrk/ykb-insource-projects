"""
toplanti_teshis.py — Bir toplanti klasoru icin MASKELI metin raporu (konusmaci atamasi neden "Bilinmeyen"e coktu?).

Kullanim (briefmind klasorunden):
  python tools\\toplanti_teshis.py "toplantilar\\2026-10-12_ornek_1103"
  python tools\\toplanti_teshis.py "toplantilar\\..." --dosya cikti.txt     (ayni metin dosyaya da yazilir)

Cikti yapistirilabilir metindir: adlar K1..Kn olur (ad parcalari dahil), URL / e-posta / anahtar / C:\\Users\\x maskelenir;
transkript metni, ozet ve altyazi (gorunen) metni HIC yazilmaz, yalniz sayilar. Eski bicimli klasorlerde eksik alanlar
atlanir. Bolumler: kimlik, satir dagilimi, dakika tablosu, L/MAD/R zaman cizgisi, aralik istatistigi, klip-aralik
gecikme histogrami, okuma istatistigi (olcum/okumalar.jsonl varsa), hata.log, otomatik hukum.
"""
import argparse
import bisect
import collections
import json
import os
import re
import sys

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARAC = os.path.dirname(os.path.abspath(__file__))
for _yol in (KOK, ARAC):
    if _yol not in sys.path:
        sys.path.insert(0, _yol)

import olcum  # noqa: E402  (tools/olcum.py: jsonl_oku)

PANEL_DONUK_SN = 12.0          # panel bu kadar degismeden konusma varsa donuk
L_MAKS = 5.0                   # konusmaci.VARSAYILAN_ESIKLER["L_maks"] (okunamazsa)
L_PAY = 2.0
KAPSAMA_YOK_ESIK = 0.30        # uzak satirlarin bu payi kapsama_yok -> okuma bosluklari destekler
BILINMEYEN_ESIK = 0.60         # ilk dilimden sonra uzak satirlarin bu payi "?" -> coku
SORU_AD_ESIK = 0.25            # okumalardaki satirlarin bu payi "?" adli -> ad esleme hatasi
GECIKME_ESIK = 0.40            # aralik bulunan kliplerin bu payi L_maks'tan gec -> gecikme destekler
HATA_SATIR = 30

_ETIKET = {"destekler": "DESTEKLER", "desteklemez": "DESTEKLEMEZ", "veri yok": "VERİ YOK"}
_AD_DURAK = frozenset(("konuk", "guest", "external", "dış", "dis", "oda", "room", "anda", "aynı", "toplantı",
                       "ben", "ses", "sil", "tur", "kim", "yok", "var", "ile", "için", "microsoft", "teams"))


# ---------------------------------------------------------------- yardimcilar

def _sayi(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _sn(ts):
    """'HH:MM[:SS]' -> gun saniyesi | None"""
    try:
        p = [int(x) for x in str(ts).split(":")]
        return p[0] * 3600 + p[1] * 60 + (p[2] if len(p) > 2 else 0)
    except Exception:
        return None


def _medyan(xs):
    s = sorted(xs)
    n = len(s)
    if not n:
        return 0.0
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _yuzdelik(xs, p):
    s = sorted(xs)
    if not s:
        return 0.0
    return s[min(len(s) - 1, int(p * len(s)))]


def _oran(a, b):
    return a / b if b else 0.0


def _yuzde(a, b):
    return f"%{100 * _oran(a, b):.0f}"


def _hms(sn):
    if sn is None:
        return "-"
    sn = int(sn) % 86400
    return f"{sn // 3600:02d}:{sn % 3600 // 60:02d}:{sn % 60:02d}"


def _hm(sn):
    sn = int(sn) % 86400
    return f"{sn // 3600:02d}:{sn % 3600 // 60:02d}"


def _kucuk(s):
    return (s or "").replace("İ", "i").replace("I", "ı").lower()


def _json_oku(yol):
    try:
        with open(yol, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _tablo(basliklar, satirlar):
    """Hizali, '|' ayracli tablo -> [str]"""
    satirlar = [[str(x) for x in s] for s in satirlar]
    gen = [len(b) for b in basliklar]
    for s in satirlar:
        for i, x in enumerate(s):
            gen[i] = max(gen[i], len(x))
    cik = [" | ".join(b.rjust(gen[i]) for i, b in enumerate(basliklar))]
    for s in satirlar:
        cik.append(" | ".join(x.rjust(gen[i]) for i, x in enumerate(s)))
    return cik


# ---------------------------------------------------------------- maskeleme

_URL = re.compile(r"(?i)\b(?:https?|wss?|ftp)://[^\s\"'<>]+")
_EPOSTA = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_KULLANICI_YOLU = re.compile(r"(?i)((?:[a-z]:)?[\\/](?:users|home)[\\/])[^\\/\s\"':]+")
_GIZLI_ATAMA = re.compile(r"(?i)\b(api[_-]?key|apikey|token|secret|password|parola|sifre|authorization|bearer)"
                          r"(\s*[=:]\s*|\s+)(?!<)[^\s,;\"']+")
_UZUN_ANAHTAR = re.compile(r"\b(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{28,}\b")
_ANAHTAR_ONEK = re.compile(r"\b(?:sk|pk|ghp|gho|xox[abp]|eyJ)[A-Za-z0-9_\-.]{12,}")
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_ALAN_ADI = re.compile(r"(?i)\b[\w-]+(?:\.[\w-]+)*\.(?:com|net|org|tr|local|lan|corp|intra|internal|io|ai|gov|edu)\b")
_TEKRAR_K = re.compile(r"\b(K\d+)(?:\s+\1\b)+")


class Maske:
    """Adlari K1..Kn yapar (ad parcalari dahil); URL/e-posta/anahtar/kullanici yolunu gizler."""

    def __init__(self):
        self.kisiler = []            # [(etiket, frozenset(parca))] ilk gorulus sirasiyla
        self.tam = {}                # kucuk tam ad -> etiket
        self.parca = {}              # kucuk ad parcasi -> etiket
        self.katilimci = []          # meta katilimcilari (+ ben): [frozenset]
        self._uzun = []              # >= 5 harfli parcalar (ek almis bicimler: Denizin)
        self._gizli_adresler = []

    @staticmethod
    def _parcalar(ad):
        temiz = re.sub(r"\([^)]*\)", " ", ad or "")
        return [p for p in re.findall(r"[^\W\d_]{3,}", _kucuk(temiz)) if p not in _AD_DURAK]

    @staticmethod
    def bilesenler(speaker):
        """'A → B', 'A / B', 'Ad?' -> ['A', 'B']; '?' ve bos atlanir."""
        cik = []
        for p in re.split(r"→|->|\s/\s|/", speaker or ""):
            p = re.sub(r"\(aynı anda\)", "", p, flags=re.I).strip().rstrip("?").strip()
            if p and p != "?":
                cik.append(p)
        return cik

    def ekle(self, ad):
        """Ad(lar)i sozluge ekler; ilk bilesenin etiketini dondurur (yoksa None)."""
        etiket = None
        for b in self.bilesenler(ad):
            e = self._ekle1(b)
            etiket = etiket or e
        return etiket

    def _ekle1(self, ad):
        anahtar = _kucuk(re.sub(r"\s+", " ", ad)).strip()
        parcalar = frozenset(self._parcalar(ad))
        if anahtar in self.tam:
            return self.tam[anahtar]
        etiket = None
        if parcalar:
            for e, p in self.kisiler:
                if p == parcalar:
                    etiket = e
                    break
        if etiket is None:
            etiket = f"K{len(self.kisiler) + 1}"
            self.kisiler.append((etiket, parcalar))
        self.tam[anahtar] = etiket
        for p in parcalar:
            if p not in self.parca:
                self.parca[p] = etiket
                if len(p) >= 5:
                    self._uzun.append(p)
        return etiket

    def katilimci_ekle(self, ad):
        for b in self.bilesenler(ad):
            self._ekle1(b)
            s = frozenset(self._parcalar(b))
            if s:
                self.katilimci.append(s)

    def eslesir(self, ad):
        """Ad, katilimci listesindeki biriyle (parca kumesi esit / alt-ust) eslesir mi?"""
        s = frozenset(self._parcalar(ad))
        return bool(s) and any(s <= k or k <= s for k in self.katilimci)

    def etiket(self, ad):
        """Tek ad -> etiket (yoksa eklenir)."""
        return self.ekle(ad) or "?"

    def adres_gizle(self, adres):
        # yalniz URL/host gibi gorunen degerler (kisa semasiz degerler ortak kelimeleri bozar)
        if adres and len(adres) >= 6 and ("." in adres or ":" in adres):
            self._gizli_adresler.append(adres)

    def _ad_degistir(self, m):
        tok = m.group(0)
        low = _kucuk(tok)
        e = self.parca.get(low)
        if e:
            return e
        for p in self._uzun:
            if low.startswith(p) and len(low) - len(p) <= 4:
                return self.parca[p]
        return tok

    def maskele(self, s):
        s = str(s)
        s = _URL.sub("<URL>", s)
        for adres in self._gizli_adresler:
            s = re.sub(r"(?<![\w.-])" + re.escape(adres) + r"(?![\w-])", "<ADRES>", s, flags=re.I)
        s = _EPOSTA.sub("<E-POSTA>", s)
        s = _KULLANICI_YOLU.sub(lambda m: m.group(1) + "<K>", s)
        s = _GIZLI_ATAMA.sub(lambda m: m.group(1) + "=<GIZLI>", s)
        s = _ANAHTAR_ONEK.sub("<ANAHTAR>", s)
        s = _UZUN_ANAHTAR.sub("<ANAHTAR>", s)
        s = _IPV4.sub("<IP>", s)
        s = _ALAN_ADI.sub("<ALAN>", s)
        if self.parca:
            s = re.sub(r"[^\W\d_]+", self._ad_degistir, s)
            s = _TEKRAR_K.sub(r"\1", s)
        return s


class Rapor:
    def __init__(self, maske):
        self.maske = maske
        self.satirlar = []

    def yaz(self, *satirlar):
        for s in satirlar:
            self.satirlar.append(self.maske.maskele(s))

    def baslik(self, s):
        self.yaz("", "== " + s + " ==")

    def metin(self):
        return "\n".join(self.satirlar) + "\n"


# ---------------------------------------------------------------- zaman

class Zaman:
    """Epoch (saat.simdi alani) <-> yerel gun saniyesi: ofset = medyan(sn(ts) - bas) (dairesel; satir ts'si yerel saat)."""

    def __init__(self, satirlar):
        self.ofset = None
        self.sarma = False
        farklar, sn_listesi = [], []
        for s in satirlar:
            bas, t = _sayi(s.get("bas")), _sn(s.get("ts"))
            if t is not None:
                sn_listesi.append(t)
            if bas is not None and t is not None:
                farklar.append((t - bas) % 86400)
        if sn_listesi and max(sn_listesi) - min(sn_listesi) > 12 * 3600:
            self.sarma = True
        if farklar:
            d0 = farklar[0]
            goreli = [((f - d0 + 43200) % 86400) - 43200 for f in farklar]
            self.ofset = (d0 + _medyan(goreli)) % 86400

    def gun(self, epoch):
        e = _sayi(epoch)
        if self.ofset is None or e is None:
            return None
        v = (e + self.ofset) % 86400
        if self.sarma and v < 43200:
            v += 86400
        return v

    def satir_gun(self, s):
        """Satirin gun saniyesi: ts, yoksa bas."""
        t = _sn(s.get("ts"))
        if t is not None:
            return t + 86400 if self.sarma and t < 43200 else t
        return self.gun(s.get("bas"))


# ---------------------------------------------------------------- veri yukleme

def _kayitlar_yerel(klasor):
    """motor.altyazi_kayitlari alinamazsa (import hatasi) ayni mantik: guncelle/sil/yerine_id."""
    satirlar, id_ile = [], {}
    for s in olcum.jsonl_oku(os.path.join(klasor, "altyazi.jsonl")):
        if s.get("sil"):
            hedef = id_ile.pop(s.get("id"), None)
            if hedef is not None:
                satirlar = [x for x in satirlar if x is not hedef]
            continue
        if s.get("guncelle"):
            hedef = id_ile.get(s.get("id"))
            if hedef is not None:
                hedef.update({k: v for k, v in s.items() if k not in ("id", "guncelle", "onceki_text")})
            continue
        if "text" not in s:
            continue
        s = dict(s)
        s.pop("yerine_id", None)
        satirlar.append(s)
        if s.get("id") is not None:
            id_ile[s["id"]] = s
    return satirlar


def altyazi_satirlari(klasor):
    try:
        import motor
        return motor.altyazi_kayitlari(klasor)
    except Exception:
        return _kayitlar_yerel(klasor)


def sil_bilgisi(klasor):
    """altyazi.jsonl ham kayitlari -> (silinen id sayisi, {neden: adet}, [silinen satirin ts'si])"""
    ts_ile, silinen, nedenler, tsler = {}, set(), collections.Counter(), []
    for k in olcum.jsonl_oku(os.path.join(klasor, "altyazi.jsonl")):
        if k.get("sil"):
            if k.get("id") not in silinen:
                silinen.add(k.get("id"))
                nedenler[str(k.get("neden") or "?")] += 1
                if k.get("id") in ts_ile:
                    tsler.append(ts_ile[k.get("id")])
        elif "text" in k and not k.get("guncelle") and k.get("id") is not None:
            ts_ile[k["id"]] = k.get("ts")
    return len(silinen), dict(nedenler), tsler


def satir_tur(s):
    k = (s.get("speaker") or "?").strip()
    if k in ("", "?"):
        return "?"
    if "→" in k:
        return "devir"
    if "/" in k:
        return "cak"
    if k.endswith("?"):
        return "Ad?"
    return "kesin"


def _p1(s):
    ad = s.get("adaylar")
    try:
        return float(ad[0][1]) if ad else None
    except (TypeError, ValueError, IndexError):
        return None


def _mik(s):
    return (s.get("akis") or "") == "mikrofon"


def olaylari_yukle(klasor):
    return olcum.jsonl_oku(os.path.join(klasor, "olaylar.jsonl"))


def _olay_say(olaylar, parca):
    return sum(1 for o in olaylar if parca in str(o.get("mesaj") or ""))


def adlari_topla(maske, meta, cfg, satirlar, parca_satirlari, cizelge, olaylar, okumalar, ben):
    for a in meta.get("katilimcilar") or []:
        if isinstance(a, str):
            maske.katilimci_ekle(a)
    if ben:
        maske.katilimci_ekle(ben)
    for kaynak in (satirlar, parca_satirlari):
        for s in kaynak:
            maske.ekle(s.get("speaker"))
            for a in s.get("adaylar") or []:
                if isinstance(a, (list, tuple)) and a and isinstance(a[0], str):
                    maske.ekle(a[0])
    for a in cizelge.get("araliklar") or []:
        if isinstance(a, dict) and isinstance(a.get("kim"), str):
            maske.ekle(a["kim"])
    for o in olaylar:
        for k in ("kim", "ad", "speaker", "konusmaci", "aday"):
            if isinstance(o.get(k), str):
                maske.ekle(o[k])
        for a in o.get("adaylar") or []:
            if isinstance(a, str):
                maske.ekle(a)
            elif isinstance(a, (list, tuple)) and a and isinstance(a[0], str):
                maske.ekle(a[0])
    for r in okumalar:
        for p in r.get("gorunen") or []:
            if isinstance(p, (list, tuple)) and p and isinstance(p[0], str):
                maske.ekle(p[0])
    for k in ("route", "stt_url", "llm_url", "embed_url"):
        v = cfg.get(k)
        if isinstance(v, str) and v:
            m = re.match(r"(?i)^[a-z]+://([^/:\s]+)", v)
            maske.adres_gizle(m.group(1) if m else v)


# ---------------------------------------------------------------- analiz parcalari

def dilim_uzunlugu(satirlar, zaman):
    gunler = [g for g in (zaman.satir_gun(s) for s in satirlar) if g is not None]
    if gunler and max(gunler) - min(gunler) > 40 * 60:
        return 300
    return 60


def _anahtar(gun, dilim):
    return None if gun is None else int(gun // dilim) * dilim


def okuma_analizi(okumalar, zaman, klipler, L_ust):
    """olcum/okumalar.jsonl -> sayilar sozlugu (None: kayit yok)."""
    if not okumalar:
        return None
    oku = sorted((r for r in okumalar if _sayi(r.get("bas_epoch")) is not None and _sayi(r.get("bit_epoch")) is not None),
                 key=lambda r: r["bas_epoch"])
    if not oku:
        return None
    pencereler = sorted((b, bit + L_ust) for b, bit in klipler)
    birlesik = []
    for b, e in pencereler:
        if birlesik and b <= birlesik[-1][1]:
            birlesik[-1][1] = max(birlesik[-1][1], e)
        else:
            birlesik.append([b, e])
    bas_listesi = [b for b, _ in birlesik]

    def konusmada(t):
        i = bisect.bisect_right(bas_listesi, t) - 1
        return i >= 0 and birlesik[i][1] >= t

    def konusma_sn(a, b):
        """[a, b] araliginin konusma pencereleriyle kesisim suresi."""
        top = 0.0
        for x, y in birlesik:
            if y < a:
                continue
            if x > b:
                break
            top += max(0.0, min(y, b) - max(x, a))
        return top

    sonuc = {"n": len(oku), "sure": oku[-1]["bit_epoch"] - oku[0]["bas_epoch"], "grup_yok": 0, "bos": 0,
             "konusmada": 0, "konusmada_bos": 0, "satir": 0, "soru_satir": 0, "buyume": 0,
             "okuma_sn": [], "periyot": [], "seriler": [], "degisti_yok": 0, "donuk": 0, "yeniden_bul": 0,
             "pencere": collections.Counter(), "yeni_alan": False, "dilim": collections.defaultdict(
                 lambda: collections.Counter()), "adlar": collections.Counter(), "boslu_seri": 0}
    onceki_imza, onceki_kelime, seri = None, None, None
    bos_seri_bas, en_uzun_bos = None, 0.0
    onceki_bas = None
    for r in oku:
        gorunen = [p for p in (r.get("gorunen") or []) if isinstance(p, (list, tuple)) and len(p) >= 2]
        bas, bit = r["bas_epoch"], r["bit_epoch"]
        sure = _sayi(r.get("okuma_sn"))
        sonuc["okuma_sn"].append(sure if sure is not None else max(0.0, bit - bas))
        if any(k in r for k in ("degisti", "donuk", "okuma_sn", "pencere", "yeniden_bul")):
            sonuc["yeni_alan"] = True
        if r.get("degisti") is False:
            sonuc["degisti_yok"] += 1
        if r.get("donuk"):
            sonuc["donuk"] += 1
        sonuc["yeniden_bul"] = max(sonuc["yeniden_bul"], int(_sayi(r.get("yeniden_bul")) or 0))
        pen = r.get("pencere")
        if isinstance(pen, dict):
            for k, v in pen.items():
                if v:
                    sonuc["pencere"][k] += 1
        if onceki_bas is not None:
            sonuc["periyot"].append((onceki_bas, bas - onceki_bas))
        onceki_bas = bas
        dk = sonuc["dilim"][zaman.gun(bas)]          # anahtar: gun saniyesi (rapor_uret dilimler)
        dk["okuma"] += 1
        icerde = konusmada(bas)
        if icerde:
            sonuc["konusmada"] += 1
        if not r.get("grup"):
            sonuc["grup_yok"] += 1
            dk["grup_yok"] += 1
            imza = None
        elif not gorunen:
            sonuc["bos"] += 1
            dk["bos"] += 1
            if icerde:
                sonuc["konusmada_bos"] += 1
            imza = None
            if bos_seri_bas is None:
                bos_seri_bas = bas
            en_uzun_bos = max(en_uzun_bos, bit - bos_seri_bas)
        else:
            imza = tuple((str(k), str(n)) for k, n in (p[:2] for p in gorunen))
        if imza is not None:
            bos_seri_bas = None
            sonuc["satir"] += len(gorunen)
            dk["satir"] += len(gorunen)
            for k, n in (p[:2] for p in gorunen):
                if k == "?":
                    sonuc["soru_satir"] += 1
                    dk["soru"] += 1
                else:
                    sonuc["adlar"][str(k)] += 1
            kelime = sum(len(str(n).split()) for _, n in (p[:2] for p in gorunen))
            if onceki_kelime is not None and kelime > onceki_kelime:
                sonuc["buyume"] += 1
                dk["buyume"] += 1
            onceki_kelime = kelime
            if imza == onceki_imza:
                dk["ayni"] += 1
                if seri is None:
                    seri = [bas, bit, 2, icerde]
                else:
                    seri[1], seri[2], seri[3] = bit, seri[2] + 1, seri[3] or icerde
            else:
                if seri is not None:
                    sonuc["seriler"].append(tuple(seri) + (konusma_sn(seri[0], seri[1]),))
                seri = None
            onceki_imza = imza
        else:
            if seri is not None:
                sonuc["seriler"].append(tuple(seri) + (konusma_sn(seri[0], seri[1]),))
            seri = None
            onceki_imza = None
    if seri is not None:
        sonuc["seriler"].append(tuple(seri) + (konusma_sn(seri[0], seri[1]),))
    sonuc["en_uzun_bos"] = en_uzun_bos
    # seri = (bas_epoch, bit_epoch, okuma_sayisi, konusma_var, konusma_sn); ilk okuma serinin basinda sayilmadi
    periyotlar = [p for _, p in sonuc["periyot"]]
    R = _medyan(periyotlar) if periyotlar else 0.0
    esik = max(3.0 * R, 1.0)
    bosluklar = [(t, p) for t, p in sonuc["periyot"] if p > esik]
    sonuc["R"] = R
    sonuc["bosluk_esik"] = esik
    sonuc["bosluklar"] = bosluklar
    return sonuc


def aralik_listesi(cizelge):
    sonuc = []
    for a in cizelge.get("araliklar") or []:
        if not isinstance(a, dict):
            continue
        ilk, son = _sayi(a.get("ilk")), _sayi(a.get("son"))
        if ilk is None:
            continue
        sonuc.append((str(a.get("kim") or "?"), ilk, ilk if son is None else max(ilk, son), str(a.get("tur") or "?")))
    sonuc.sort(key=lambda x: (x[1], x[0]))
    return sonuc


def gecikme_histogrami(uzak_klipler, baslar, L_ust):
    """Her uzak klip icin en yakin aralik basi (|fark| <= 30 sn): d = aralik_basi - klip_basi."""
    kovalar = [(-99, -1, "< -1"), (-1, 0, "-1..0")] + [(i, i + 1, f"{i}..{i + 1}") for i in range(10)] + [
        (10, 15, "10..15"), (15, 30, "15..30"), (30, 99, "30+")]
    sayac = collections.OrderedDict((k[2], 0) for k in kovalar)
    sayac["yok (>30 sn)"] = 0
    farklar = []
    for bas, _bit in uzak_klipler:
        i = bisect.bisect_left(baslar, bas)
        aday = []
        if i < len(baslar):
            aday.append(baslar[i] - bas)
        if i > 0:
            aday.append(baslar[i - 1] - bas)
        d = min(aday, key=abs) if aday else None
        if d is None or abs(d) > 30:
            sayac["yok (>30 sn)"] += 1
            continue
        farklar.append(d)
        for a, b, ad in kovalar:
            if a <= d < b:
                sayac[ad] += 1
                break
    return sayac, farklar


def pencere_degen_orani(uzak_klipler, araliklar, L, L_ust):
    """Klibin altyazi penceresine (genis: bas..bit+L_ust; dar: bas+L-1.5..bit+L+1.5) degen aralik orani."""
    if not uzak_klipler:
        return 0, 0, 0
    baslar = [a[1] for a in araliklar]
    sonlar = [a[2] for a in araliklar]
    # aralik sayisi kucuk (<= birkac bin): dogrusal tarama yeterli
    genis = dar = 0
    for bas, bit in uzak_klipler:
        g0, g1 = bas, bit + L_ust
        d0, d1 = bas + L - 1.5, bit + L + 1.5
        g = d = False
        for ilk, son in zip(baslar, sonlar):
            if ilk <= g1 and son >= g0:
                g = True
            if ilk <= d1 and son >= d0:
                d = True
            if g and d:
                break
        genis += g
        dar += d
    return len(uzak_klipler), genis, dar


# ---------------------------------------------------------------- rapor

def rapor_uret(klasor, cfg=None, ben=None, hata_log=None, dilim=None):
    """Klasor -> maskeli rapor metni."""
    cfg = cfg or {}
    klasor = os.path.abspath(klasor)
    meta = _json_oku(os.path.join(klasor, "meta.json")) or {}
    cizelge_ham = _json_oku(os.path.join(klasor, "cizelge.json"))
    cizelge = cizelge_ham if isinstance(cizelge_ham, dict) else {}
    satirlar = altyazi_satirlari(klasor)
    try:
        parca_satirlari = olcum.satirlar_oku(klasor)
    except Exception:
        parca_satirlari = []
    if not satirlar:
        satirlar = list(parca_satirlari)
    satirlar = [s for s in satirlar if isinstance(s, dict)]
    olaylar = olaylari_yukle(klasor)
    okuma_yolu = os.path.join(klasor, "olcum", "okumalar.jsonl")
    okumalar = olcum.jsonl_oku(okuma_yolu)
    ben = ben if ben is not None else (cfg.get("ben") if isinstance(cfg.get("ben"), str) else None)

    maske = Maske()
    adlari_topla(maske, meta, cfg, satirlar, parca_satirlari, cizelge, olaylar, okumalar, ben)
    r = Rapor(maske)
    zaman = Zaman(satirlar)
    dilim = dilim or dilim_uzunlugu(satirlar, zaman)

    esikler = cfg.get("esikler") if isinstance(cfg.get("esikler"), dict) else {}
    L_maks = _sayi(esikler.get("L_maks")) or L_MAKS
    durum = cizelge.get("durum") if isinstance(cizelge.get("durum"), dict) else {}
    L_son = _sayi(durum.get("L")) if _sayi(durum.get("L")) is not None else 2.0
    MAD_son = _sayi(durum.get("MAD")) or 0.0
    L_ust = max(L_maks, L_son + L_PAY + 2.0 * MAD_son)

    uzak = [s for s in satirlar if not _mik(s)]
    mik = [s for s in satirlar if _mik(s)]
    uzak_klipler = sorted((_sayi(s.get("bas")), _sayi(s.get("bit")) if _sayi(s.get("bit")) is not None else _sayi(s.get("bas")))
                          for s in uzak if _sayi(s.get("bas")) is not None)
    silinen, sil_nedenleri, sil_tsleri = sil_bilgisi(klasor)
    araliklar = aralik_listesi(cizelge)
    ok = okuma_analizi(okumalar, zaman, uzak_klipler, L_ust)

    # ---- 1) kimlik
    r.baslik("KİMLİK")
    dosyalar = collections.OrderedDict((
        ("altyazi.jsonl", os.path.exists(os.path.join(klasor, "altyazi.jsonl"))),
        ("cizelge.json", os.path.exists(os.path.join(klasor, "cizelge.json"))),
        ("olaylar.jsonl", os.path.exists(os.path.join(klasor, "olaylar.jsonl"))),
        ("llm_log.jsonl", os.path.exists(os.path.join(klasor, "llm_log.jsonl"))),
        ("not.md", os.path.exists(os.path.join(klasor, "not.md"))),
        ("olcum/okumalar.jsonl", os.path.exists(okuma_yolu)),
        ("parcalar", os.path.isdir(os.path.join(klasor, "parcalar"))
         and any(a.startswith("parca_") for a in os.listdir(os.path.join(klasor, "parcalar")))),
    ))
    kat = meta.get("katilimcilar")
    r.yaz(f"tarih {meta.get('tarih')} | durum {meta.get('durum')} | şablon {meta.get('sablon')} | "
          f"kaynak {meta.get('kaynak')} | katılımcı {len(kat) if isinstance(kat, list) else '-'} | "
          f"konuşma_belirsiz {meta.get('konusma_belirsiz', '-')}")
    r.yaz("dosyalar: " + ", ".join(f"{k}={'var' if v else 'YOK'}" for k, v in dosyalar.items()))
    if not dosyalar["cizelge.json"]:
        r.yaz("HİÇ ARALIK OLUŞMADI (cizelge.json yok: konuşmacı çizelgesine tek aralık bile yazılmadı)")
    if zaman.ofset is None:
        r.yaz("zaman: satırlarda bas+ts çifti yok, epoch→saat eşlemesi yapılamadı (olay/okuma tabloları atlanır)")
    r.yaz(f"tablo dilimi: {dilim // 60} dk" + (" (toplantı 40 dk'dan uzun)" if dilim > 60 else ""))

    # ---- 2) satir dagilimi
    r.baslik("SATIRLAR")
    r.yaz(f"toplam {len(satirlar)} | silinen {silinen} | uzak (loopback) {len(uzak)} | mikrofon {len(mik)}")
    kaynaklar = collections.Counter(str(s.get("kaynak")) for s in satirlar)
    r.yaz(f"kaynak: {dict(kaynaklar)}")
    r.yaz(f"tür: {dict(collections.Counter(satir_tur(s) for s in satirlar))}")
    neden = collections.Counter(s.get("spk_neden") for s in satirlar if s.get("spk_neden"))
    r.yaz(f"spk_neden: {dict(neden)}")
    kapsama_false = sum(1 for s in satirlar if s.get("kapsama") is False)
    supheli = [s for s in satirlar if s.get("supheli")]
    r.yaz(f"kapsama_false {kapsama_false} | süpheli {len(supheli)} "
          f"(neden {dict(collections.Counter(str(s.get('supheli_neden')) for s in supheli))}) | "
          f"cizelge_etkin_false {sum(1 for s in satirlar if s.get('cizelge_etkin') is False)} | "
          f"adaylar_boş {sum(1 for s in satirlar if not s.get('adaylar'))} | "
          f"beklemede {sum(1 for s in satirlar if s.get('durum') == 'beklemede')}")
    r.yaz(f"silme nedenleri: {sil_nedenleri or '-'}")
    hal = [_sayi(s.get("hal_puani")) for s in satirlar if _sayi(s.get("hal_puani")) is not None]
    if hal:
        r.yaz(f"halüsinasyon puanı (satır başına): ort {sum(hal) / len(hal):.2f}, ≥0.5 olan {sum(1 for h in hal if h >= 0.5)}")

    # ---- 3) dakika tablosu
    r.baslik(f"{dilim // 60} DK TABLO")
    sutunlar = collections.defaultdict(collections.Counter)
    p1_topla = collections.defaultdict(list)
    for s in satirlar:
        k = _anahtar(zaman.satir_gun(s), dilim)
        if k is None:
            continue
        c = sutunlar[k]
        c["mik" if _mik(s) else "lb"] += 1
        c[{"kesin": "kesin", "Ad?": "ad", "?": "soru", "devir": "dev", "cak": "dev"}[satir_tur(s)]] += 1
        if s.get("supheli"):
            c["supheli"] += 1
        n = s.get("spk_neden")
        if n in ("kimse", "kapsama_yok", "hayalet", "zaman_asimi", "panel_donuk"):
            c[n] += 1
        if not s.get("adaylar"):
            c["bos"] += 1
        p = _p1(s)
        if p is not None:
            p1_topla[k].append(p)
    for ts in sil_tsleri:
        t = _sn(ts)
        if t is not None:
            sutunlar[_anahtar(t + 86400 if zaman.sarma and t < 43200 else t, dilim)]["sil"] += 1
    ok_dilim = collections.defaultdict(collections.Counter)
    if ok:
        for g, c in ok["dilim"].items():
            k = _anahtar(g, dilim)
            if k is not None:
                ok_dilim[k].update(c)
    olay_dilim = collections.defaultdict(collections.Counter)
    L_dilim = {}
    for o in olaylar:
        g = zaman.gun(o.get("an"))
        k = _anahtar(g, dilim)
        if k is None:
            continue
        m = str(o.get("mesaj") or "")
        if m == "L örneği":
            olay_dilim[k]["ornek"] += 1
            L_dilim[k] = (o.get("L"), o.get("MAD"), o.get("R"))
        elif m.startswith("L örneği atıldı (L_maks"):
            olay_dilim[k]["aralik_yok"] += 1
        elif m == "L örneği atıldı":
            olay_dilim[k]["atildi"] += 1
        elif m.startswith("L örneği sayılmadı"):
            olay_dilim[k]["belirsiz"] += 1
        elif m == "L basamağı":
            olay_dilim[k]["basamak"] += 1
        elif m == "ses saat çapası":
            olay_dilim[k]["saat"] += 1
    araliklar_dilim = collections.defaultdict(collections.Counter)
    for kim, ilk, _son, tur in araliklar:
        k = _anahtar(zaman.gun(ilk), dilim)
        if k is not None:
            araliklar_dilim[k]["hepsi"] += 1
            if tur == "yeni":
                araliklar_dilim[k]["yeni"] += 1
    tum = set(sutunlar) | set(ok_dilim) | set(olay_dilim) | set(araliklar_dilim)
    anahtarlar = list(range(min(tum), max(tum) + dilim, dilim)) if tum else []
    satir_tbl = []
    for k in anahtarlar:
        c = sutunlar.get(k, collections.Counter())
        p = p1_topla.get(k)
        satir_tbl.append([_hm(k), c["lb"], c["mik"], c["kesin"], c["ad"], c["soru"], c["dev"], c["supheli"], c["sil"],
                          c["kimse"], c["kapsama_yok"], c["hayalet"], c["zaman_asimi"], c["panel_donuk"], c["bos"],
                          f"{sum(p) / len(p):.2f}" if p else "-"])
    if satir_tbl:
        r.yaz(*_tablo(["dk", "lb", "mik", "kesin", "Ad?", "?", "dev/cak", "süpheli", "sil", "kimse", "kapsama_yok",
                       "hayalet", "zaman_asimi", "panel_donuk", "boş_aday", "ort_p1"], satir_tbl))
    else:
        r.yaz("satır yok")

    # ---- 4) L/MAD/R zaman cizgisi
    r.baslik("L / MAD / R ZAMAN ÇİZGİSİ VE ÇAPA OLAYLARI")
    mesajlar = collections.Counter()
    for o in olaylar:
        m = re.sub(r"\d+(?:\.\d+)?", "#", str(o.get("mesaj") or o.get("tur") or "?"))[:70]
        mesajlar[m] += 1
    r.yaz(f"olaylar.jsonl: {len(olaylar)} kayıt" + ("" if olaylar else " (yok ya da boş)"))
    for m, n in mesajlar.most_common(14):
        r.yaz(f"  {n:5d}  {m}")
    ornekler = [o for o in olaylar if o.get("mesaj") == "L örneği"]
    atilan_buyuk = [o for o in olaylar if o.get("mesaj") == "L örneği atıldı"]
    atilan_yok = [o for o in olaylar if str(o.get("mesaj") or "").startswith("L örneği atıldı (L_maks")]
    belirsiz = [o for o in olaylar if str(o.get("mesaj") or "").startswith("L örneği sayılmadı")]
    basamak = [o for o in olaylar if o.get("mesaj") == "L basamağı"]
    saat_capa = [o for o in olaylar if o.get("mesaj") == "ses saat çapası"]
    r.yaz(f"L örneği {len(ornekler)} | atıldı (L>L_maks) {len(atilan_buyuk)} | atıldı (L_maks içinde aralık başı yok) "
          f"{len(atilan_yok)} | sayılmadı (belirsiz çapa) {len(belirsiz)} | basamak {len(basamak)} | saat çapası {len(saat_capa)}")
    if ornekler:
        degerler = [_sayi(o.get("L_ornek")) for o in ornekler if _sayi(o.get("L_ornek")) is not None]
        son = ornekler[-1]
        r.yaz(f"son L/MAD/R: {son.get('L')} {son.get('MAD')} {son.get('R')} | ilk 5 örnek: {degerler[:5]} | "
              f"son 5 örnek: {degerler[-5:]}")
    if durum:
        r.yaz("cizelge durum: " + ", ".join(f"{k}={durum.get(k)}" for k in (
            "L", "MAD", "R", "kapsama", "araliklar", "capa", "atilan_ornek", "tasma", "kulaklik", "bekleyen",
            "neden", "bosluk_sn") if k in durum))
    elif dosyalar["cizelge.json"]:
        r.yaz("cizelge durum: yok (eski biçim)")
    if anahtarlar and (olay_dilim or L_dilim):
        tbl = []
        for k in anahtarlar:
            c = olay_dilim.get(k, collections.Counter())
            lmr = L_dilim.get(k)
            tbl.append([_hm(k), c["ornek"], c["atildi"], c["aralik_yok"], c["belirsiz"], c["basamak"], c["saat"],
                        lmr[0] if lmr else "-", lmr[1] if lmr else "-", lmr[2] if lmr else "-"])
        r.yaz(*_tablo(["dk", "örnek", "atıldı", "aralık_yok", "belirsiz", "basamak", "saat", "L", "MAD", "R"], tbl))

    # ---- 5) aralik istatistigi
    r.baslik("ARALIK İSTATİSTİĞİ (cizelge.json)")
    if not araliklar:
        r.yaz("aralık yok" + ("" if dosyalar["cizelge.json"] else " (HİÇ ARALIK OLUŞMADI)"))
    else:
        ilk_satir = min((_sayi(s.get("bas")) for s in satirlar if _sayi(s.get("bas")) is not None), default=None)
        kisi = collections.OrderedDict()
        for kim, ilk, son, tur in araliklar:
            kisi.setdefault(kim, []).append((ilk, son, tur))
        r.yaz(f"{len(araliklar)} aralık, {len(kisi)} kişi")
        for kim, lst in list(kisi.items())[:25]:
            toplam = sum(s - i for i, s, _ in lst)
            goreli = lambda e: (f" ({e - ilk_satir:+.0f} sn)" if ilk_satir is not None else "")   # noqa: E731
            gi, gs = zaman.gun(lst[0][0]), zaman.gun(max(x[1] for x in lst))
            r.yaz(f"  {maske.etiket(kim)}: {len(lst)} aralık, toplam {toplam:.0f} sn, ilk {_hms(gi)}{goreli(lst[0][0])}, "
                  f"son {_hms(gs)}{goreli(max(x[1] for x in lst))}, tür {dict(collections.Counter(t for _, _, t in lst))}")
        if len(kisi) > 25:
            r.yaz(f"  ... {len(kisi) - 25} kişi daha")
        if anahtarlar:
            tbl = [[_hm(k), araliklar_dilim.get(k, {}).get("hepsi", 0), araliklar_dilim.get(k, {}).get("yeni", 0)]
                   for k in anahtarlar]
            r.yaz(*_tablo(["dk", "yeni_başlayan_aralık", "tür=yeni"], tbl))
        son_aralik = zaman.gun(max(a[2] for a in araliklar))
        if son_aralik is not None and anahtarlar:
            son_satir = max(anahtarlar) + dilim
            if son_satir - son_aralik > 5 * 60:
                r.yaz(f"! son aralık {_hms(son_aralik)}: sonrası {(son_satir - son_aralik) / 60:.0f} dk boyunca aralık oluşmadı")

    # ---- 6) klip <-> aralik
    r.baslik("KLİP BAŞI ↔ EN YAKIN ARALIK BAŞI (gerçek gecikme tahmini)")
    baslar = sorted(a[1] for a in araliklar)
    hist, farklar = ({}, [])
    if uzak_klipler and baslar:
        hist, farklar = gecikme_histogrami(uzak_klipler, baslar, L_ust)
        r.yaz(f"uzak klip {len(uzak_klipler)} | aralık başı {len(baslar)} | durum L={L_son} MAD={MAD_son} L_maks={L_maks:g}")
        r.yaz(*_tablo(["fark_sn", "klip"], [[k, v] for k, v in hist.items() if v]))
        olculen = [d for d in farklar if 0 <= d <= 10]
        if olculen:
            r.yaz(f"0–10 sn arası {len(olculen)} klip: medyan gecikme {_medyan(olculen):.2f} sn "
                  f"(p25 {_yuzdelik(olculen, 0.25):.2f}, p75 {_yuzdelik(olculen, 0.75):.2f}) | durum L={L_son}")
        n, genis, dar = pencere_degen_orani(uzak_klipler, araliklar, L_son, L_ust)
        r.yaz(f"pencereye değen aralık: geniş (bas..bit+{L_ust:.1f}) {genis}/{n} {_yuzde(genis, n)} | "
              f"dar (L±1.5) {dar}/{n} {_yuzde(dar, n)}")
    elif not uzak_klipler:
        r.yaz("uzak klip yok (bas alanı olan loopback satırı bulunamadı)")
    else:
        r.yaz(f"uzak klip {len(uzak_klipler)} ama aralık yok: klip pencerelerine değen aralık 0")

    # ---- 7) okuma istatistigi
    r.baslik("OKUMA İSTATİSTİĞİ (olcum/okumalar.jsonl)")
    if ok is None:
        r.yaz("okuma kaydı yok: Ayarlar → 'Ölçüm kaydı (PoC)' açılıp yeni bir toplantı kaydedilmeli (okuma/dk, boş okuma, "
              "donuk panel bununla görünür)")
    else:
        dk_sure = max(ok["sure"], 1e-9) / 60.0
        r.yaz(f"okuma {ok['n']} ({ok['n'] / dk_sure:.0f}/dk, {ok['sure'] / 60:.1f} dk) | grup yok {ok['grup_yok']} | "
              f"boş okuma {ok['bos']} (en uzun boş seri {ok['en_uzun_bos']:.1f} sn) | okuma süresi p95 "
              f"{1000 * _yuzdelik(ok['okuma_sn'], 0.95):.0f} ms, ort {1000 * sum(ok['okuma_sn']) / len(ok['okuma_sn']):.0f} ms")
        r.yaz(f"R (medyan periyot) {ok['R']:.2f} sn | boşluk eşiği {ok['bosluk_esik']:.2f} sn")
        r.yaz(f"satır görünümü {ok['satir']} | '?' adlı {ok['soru_satir']} ({_yuzde(ok['soru_satir'], ok['satir'])}) | "
              f"kelime artışı {ok['buyume']} ({ok['buyume'] / dk_sure:.1f}/dk) | farklı ad {len(ok['adlar'])}")
        if ok["yeni_alan"]:
            r.yaz(f"yeni alanlar: değişmedi {ok['degisti_yok']} | donuk {ok['donuk']} | yeniden_bul (en çok) "
                  f"{ok['yeniden_bul']} | pencere {dict(ok['pencere'])}")
        seriler = sorted(ok["seriler"], key=lambda s: -(s[1] - s[0]))
        uzun = [s for s in seriler if s[1] - s[0] >= 3.0]
        r.yaz(f"birebir aynı ardışık okuma serisi (≥3 sn): {len(uzun)} adet, toplam {sum(s[1] - s[0] for s in uzun):.0f} sn")
        for s in uzun[:6]:
            r.yaz(f"  {_hms(zaman.gun(s[0]))} başlangıç, {s[1] - s[0]:.0f} sn, {s[2]} okuma, konuşma var: "
                  f"{'evet' if s[3] else 'hayır'}")
        gap = ok["bosluklar"]
        if gap:
            araliklar_g = sorted(t for t, _ in gap)
            aralar = [b - a for a, b in zip(araliklar_g, araliklar_g[1:])]
            r.yaz(f"okuma boşluğu (> {ok['bosluk_esik']:.1f} sn): {len(gap)} adet, süre medyan "
                  f"{_medyan([p for _, p in gap]):.1f} sn, p95 {_yuzdelik([p for _, p in gap], 0.95):.1f} sn, en uzun "
                  f"{max(p for _, p in gap):.1f} sn" + (f" | boşluk başları arası medyan {_medyan(aralar):.1f} sn" if aralar else ""))
        else:
            r.yaz(f"okuma boşluğu (> {ok['bosluk_esik']:.1f} sn): 0")
        if anahtarlar:
            tbl = []
            for k in anahtarlar:
                c = ok_dilim.get(k, collections.Counter())
                tbl.append([_hm(k), c["okuma"], c["bos"], c["grup_yok"], c["ayni"], c["soru"], c["buyume"]])
            r.yaz(*_tablo(["dk", "okuma", "boş", "grup_yok", "aynı_okuma", "'?'_satır", "kelime_artışı"], tbl))

    # ---- 8) hata.log
    r.baslik(f"HATA.LOG (son {HATA_SATIR} satır, maskeli)")
    if hata_log is None:
        try:
            import ayar
            hata_log = ayar.hata_log_yolu()
        except Exception:
            hata_log = None
    if not hata_log or not os.path.exists(hata_log):
        r.yaz(f"hata.log yok: {hata_log or '-'}")
    else:
        r.yaz(f"yol: {hata_log}")
        try:
            with open(hata_log, encoding="utf-8", errors="replace") as f:
                satirlar_log = f.read().splitlines()
            r.yaz(f"toplam {len(satirlar_log)} satır")
            for s in satirlar_log[-HATA_SATIR:]:
                r.yaz("  " + s[:220])
        except OSError as e:
            r.yaz(f"hata.log okunamadı: {type(e).__name__}")

    # ---- 9) hukum
    v = {"satirlar": satirlar, "uzak": uzak, "mik": mik, "olaylar": olaylar, "ok": ok, "araliklar": araliklar,
         "durum": durum, "L_maks": L_maks, "L_son": L_son, "farklar": farklar, "hist": hist, "ben": ben,
         "meta": meta, "maske": maske, "ornekler": ornekler, "atilan_buyuk": atilan_buyuk, "atilan_yok": atilan_yok,
         "uzak_klipler": uzak_klipler, "dilim": dilim, "zaman": zaman, "anahtarlar": anahtarlar, "sutunlar": sutunlar,
         "cizelge_var": dosyalar["cizelge.json"], "cfg": cfg}
    r.baslik("OTOMATİK HÜKÜM (destekler / desteklemez / veri yok)")
    sonuc = hukumler(v)
    for ad, (d, ayrinti) in sonuc.items():
        r.yaz(f"[{_ETIKET[d]:11}] {ad}: {ayrinti}")
    guclu = [ad for ad, (d, _) in sonuc.items() if d == "destekler"]
    r.yaz("", "özet: " + (", ".join(guclu) if guclu else "destekleyen hüküm yok"))
    for ipucu in yorumlar(sonuc):
        r.yaz("  → " + ipucu)
    return r.metin()


# ---------------------------------------------------------------- hukumler

def hukumler(v):
    """-> OrderedDict {ad: (durum, ayrinti)}; durum: destekler | desteklemez | veri yok"""
    sonuc = collections.OrderedDict()
    for ad, fn in (("donuk/bayat panel", _h_donuk), ("boş okuma", _h_bos), ("ad eşleme hatası", _h_ad),
                   ("gecikme > L_maks", _h_gecikme), ("L takılı", _h_L_takili), ("ben uyumsuzluğu", _h_ben),
                   ("okuma boşlukları", _h_bosluk), ("tümü-Bilinmeyen çöküşü", _h_coku)):
        try:
            sonuc[ad] = fn(v)
        except Exception as e:                      # bir hukum digerlerini dusurmesin
            sonuc[ad] = ("veri yok", f"hesaplanamadı ({type(e).__name__})")
    return sonuc


def _h_donuk(v):
    ok, olaylar = v["ok"], v["olaylar"]
    olay_n = _olay_say(olaylar, "değişmedi") + _olay_say(olaylar, "panel yeniden")
    if ok is None and not olay_n:
        return "veri yok", "okuma kaydı (olcum/okumalar.jsonl) yok; Ölçüm kaydı açık bir toplantı gerekir"
    if ok is None:
        return "destekler", f"olaylarda 'panel değişmedi/yeniden aranıyor' {olay_n} kayıt"
    supheli = [s for s in ok["seriler"] if s[1] - s[0] >= PANEL_DONUK_SN and s[4] >= PANEL_DONUK_SN]
    uzun = max((s[1] - s[0] for s in ok["seriler"]), default=0.0)
    toplam = sum(s[1] - s[0] for s in supheli)
    sayi = (f"içinde ≥{PANEL_DONUK_SN:.0f} sn konuşma olan aynı seri {len(supheli)} (toplam {toplam:.0f} sn), en uzun aynı seri "
            f"{uzun:.0f} sn, donuk bayrağı {ok['donuk']} okuma, olay {olay_n}")
    if supheli or ok["donuk"] >= 3 or olay_n:
        return "destekler", sayi
    return "desteklemez", sayi


def _h_bos(v):
    ok = v["ok"]
    if ok is None:
        return "veri yok", "okuma kaydı yok"
    gr = ok["n"] - ok["grup_yok"]
    ic = ok["konusmada"]
    oran_ic = _oran(ok["konusmada_bos"], ic)
    sayi = (f"boş okuma {ok['bos']}/{gr} ({_yuzde(ok['bos'], gr)}), konuşma sırasında {ok['konusmada_bos']}/{ic} "
            f"({_yuzde(ok['konusmada_bos'], ic)}), grup yok {ok['grup_yok']}, en uzun boş seri {ok['en_uzun_bos']:.0f} sn")
    if ok["konusmada_bos"] >= 10 and oran_ic >= 0.30:
        return "destekler", sayi
    return "desteklemez", sayi


def _h_ad(v):
    ok, maske = v["ok"], v["maske"]
    kanit, sayi = [], []
    if ok is not None and ok["satir"]:
        oran = _oran(ok["soru_satir"], ok["satir"])
        sayi.append(f"okumalarda '?' adlı satır {ok['soru_satir']}/{ok['satir']} ({_yuzde(ok['soru_satir'], ok['satir'])})")
        kanit.append(oran >= SORU_AD_ESIK)
    kim = sorted({a[0] for a in v["araliklar"]})
    if kim and maske.katilimci:
        eslesmeyen = [k for k in kim if not maske.eslesir(k)]
        sayi.append(f"çizelge kişileri {len(kim)}, katılımcı listesiyle eşleşmeyen {len(eslesmeyen)}")
        kanit.append(len(eslesmeyen) >= max(2, len(kim) / 2.0))
    if not sayi:
        return "veri yok", "okuma kaydı ve aralık/katılımcı verisi yok"
    return ("destekler" if any(kanit) else "desteklemez"), "; ".join(sayi)


def _h_gecikme(v):
    farklar, L_maks = v["farklar"], v["L_maks"]
    atilan = v["atilan_buyuk"]
    kabul = len(v["ornekler"])
    sayi = []
    kanit = None
    mevcut = [d for d in farklar if -1 <= d <= 30]
    if len(mevcut) >= 5:
        gec = [d for d in mevcut if d > L_maks]
        sayi.append(f"aralığı bulunan {len(mevcut)} klipten {len(gec)} tanesinde fark > L_maks={L_maks:g} "
                    f"({_yuzde(len(gec), len(mevcut))}), medyan fark {_medyan(mevcut):.1f} sn")
        kanit = _oran(len(gec), len(mevcut)) >= GECIKME_ESIK
    if len(atilan) + kabul >= 5:
        sayi.append(f"L>L_maks atılan örnek {len(atilan)}/{len(atilan) + kabul}")
        if len(atilan) >= 5 and _oran(len(atilan), len(atilan) + kabul) >= 0.5:
            kanit = True
        elif kanit is None:
            kanit = False
    if kanit is None:
        if not v["araliklar"]:
            return "veri yok", "aralık oluşmadı: gecikme ölçülemez (bkz. okuma boşlukları)"
        return "veri yok", (f"yeterli klip–aralık eşleşmesi yok (aralığı bulunan klip {len(mevcut)}/{len(v['uzak_klipler'])}, "
                            f"aralık {len(v['araliklar'])})")
    return ("destekler" if kanit else "desteklemez"), "; ".join(sayi)


def _h_L_takili(v):
    kabul, a1, a2 = len(v["ornekler"]), len(v["atilan_buyuk"]), len(v["atilan_yok"])
    toplam = kabul + a1 + a2
    capa = _sayi(v["durum"].get("capa"))
    degerler = [_sayi(o.get("L")) for o in v["ornekler"] if _sayi(o.get("L")) is not None]
    sayi = (f"kabul {kabul}, atılan {a1 + a2} (aralık başı yok {a2}) → kabul oranı {_yuzde(kabul, toplam)}; "
            f"L değerleri {sorted(set(round(d, 2) for d in degerler))[:6]}" + (f"; çizelge çapa sayısı {capa:g}" if capa is not None else ""))
    if toplam < 10:
        if capa is not None and capa <= 3 and len(v["uzak_klipler"]) >= 30:
            return "destekler", f"uzak klip {len(v['uzak_klipler'])} ama çizelge çapa sayısı {capa:g}; L örnek olayı az (kabul {kabul})"
        return "veri yok", f"L örnek olayı az (kabul {kabul}, atılan {a1 + a2})"
    if _oran(kabul, toplam) < 0.10:
        return "destekler", sayi
    return "desteklemez", sayi


def _h_ben(v):
    ben, maske = v["ben"], v["maske"]
    mik = len(v["mik"])
    if not ben:
        return "veri yok", f"config.json'da 'ben' yok; mikrofon klibi {mik}"
    adlar = set()
    for a in v["araliklar"]:
        adlar.add(a[0])
    if v["ok"] is not None:
        adlar.update(v["ok"]["adlar"])
    for s in v["uzak"]:
        for b in maske.bilesenler(s.get("speaker")):
            adlar.add(b)
    adlar = sorted(a for a in adlar if a and a != "?")
    if not adlar:
        return "veri yok", f"altyazı adı bulunamadı; mikrofon klibi {mik}"
    try:
        from konusmaci import Konusmaci
        k = Konusmaci(ben=ben, katilimcilar=v["meta"].get("katilimcilar") or [])
        esles = [k._ben_esles(a) for a in adlar]
    except Exception as e:
        return "veri yok", f"eşleştirici yüklenemedi ({type(e).__name__})"
    kesin, zayif = sum(1 for e in esles if e == 2), sum(1 for e in esles if e == 1)
    sayi = f"{len(adlar)} ad: kesin eşleşen {kesin}, zayıf {zayif}; mikrofon klibi {mik}"
    if kesin == 0 and zayif == 0:
        if not mik:
            return "veri yok", sayi + " → 'ben' hiçbir altyazı adıyla eşleşmiyor ama mikrofon klibi yok: sınanamadı"
        return "destekler", sayi + " → ayarlı 'ben' hiçbir altyazı adıyla eşleşmiyor"
    if kesin >= 2:
        return "destekler", sayi + " → 'ben' birden çok adla kesin eşleşiyor (belirsiz)"
    return "desteklemez", sayi + ("" if mik else " (ben dalı hiç çalışmadı)")


def _h_bosluk(v):
    uzak, ok, durum = v["uzak"], v["ok"], v["durum"]
    n = len(uzak)
    kyok = sum(1 for s in uzak if s.get("spk_neden") == "kapsama_yok")
    sayi = []
    ky_baskin = False
    if n >= 10:
        sayi.append(f"kapsama_yok {kyok}/{n} uzak satır ({_yuzde(kyok, n)})")
        ky_baskin = _oran(kyok, n) >= KAPSAMA_YOK_ESIK
    if ok is not None:
        g = ok["bosluklar"]
        toplam = sum(p - ok["R"] for _, p in g)
        sayi.append(f"okuma boşluğu (>{ok['bosluk_esik']:.1f} sn) {len(g)} adet, toplam {toplam:.0f} sn "
                    f"({_yuzde(toplam, ok['sure'])} süre)")
        # boşluk sayısı/süresi yalnız bilgi; hüküm kapsama_yok payına bağlı
    bosluk_olay = _olay_say(v["olaylar"], "boşluk")
    if bosluk_olay:
        sayi.append(f"olaylarda 'boşluk' kaydı {bosluk_olay}")
    if _sayi(durum.get("bosluk_sn")) is not None:
        sayi.append(f"durum.bosluk_sn {durum['bosluk_sn']}")
    if not sayi:
        return "veri yok", "uzak satır azdı ve okuma kaydı yok"
    ek = ""
    if durum.get("kapsama") is True and kyok:
        ek = " (bitişte durum.kapsama=True: boşluklar geçici)"
    return ("destekler" if ky_baskin else "desteklemez"), "; ".join(sayi) + ek


def _h_coku(v):
    uzak, sutunlar, dilim = v["uzak"], v["sutunlar"], v["dilim"]
    zaman = v["zaman"]
    gunler = [(zaman.satir_gun(s), s) for s in uzak]
    gunler = [(g, s) for g, s in gunler if g is not None]
    if len(gunler) < 10:
        return "veri yok", f"uzak satır az ({len(uzak)})"
    ilk = min(g for g, _ in gunler)
    sonra = [s for g, s in gunler if g >= ilk + dilim]
    if len(sonra) < 10:
        return "veri yok", f"ilk dilimden sonra satır az ({len(sonra)})"
    bil = sum(1 for s in sonra if satir_tur(s) == "?")
    oran = _oran(bil, len(sonra))
    # coku dakikasi: bu dilimden itibaren kalan tum dilimlerde '?' payi >= %80
    kovalar = collections.defaultdict(lambda: [0, 0])
    for g, s in gunler:
        k = _anahtar(g, dilim)
        kovalar[k][0] += 1
        kovalar[k][1] += satir_tur(s) == "?"
    coku = None
    for k in sorted(kovalar, reverse=True):
        n, b = kovalar[k]
        if n and b / n >= 0.8:
            coku = k
        else:
            break
    sayi = f"ilk dilimden sonra '?' {bil}/{len(sonra)} ({_yuzde(bil, len(sonra))})"
    if coku is not None:
        sayi += f", çöküş {_hm(coku)}'den itibaren"
    return ("destekler" if oran >= BILINMEYEN_ESIK else "desteklemez"), sayi


def yorumlar(sonuc):
    """Hukum ozeti -> kisa yonlendirmeler (kok neden ipuclari)."""
    d = {ad: durum for ad, (durum, _) in sonuc.items()}
    cik = []
    if d.get("okuma boşlukları") == "destekler":
        cik.append("okuma döngüsü kesiliyor: uzak satırların kapsama_yok payı eşiği aştı. Okuma döngüsünde UIA yürüyüşü (toplantı penceresi "
                   "denetimi) olabilir; Teams büyütülünce yavaşlar")
    if d.get("L takılı") == "destekler" and d.get("okuma boşlukları") == "destekler":
        cik.append("L takılı kalması büyük olasılıkla boşluklardan: aralık oluşmayınca çapa örneği atılır")
    if d.get("donuk/bayat panel") == "destekler":
        cik.append("panel konuşma varken değişmiyor: eski UIA öğesi tutuluyor olabilir (yeniden bulma gerekir)")
    if d.get("boş okuma") == "destekler":
        cik.append("okuma boş dönüyor ama grup bulunmuş görünüyor: boş okuma 'kimse konuşmuyor' sanılıyor")
    if d.get("ad eşleme hatası") == "destekler":
        cik.append("altyazı adları katılımcı/ayar adlarıyla eşleşmiyor ya da '?' kalıyor")
    if d.get("tümü-Bilinmeyen çöküşü") == "destekler" and not cik:
        cik.append("çöküş var ama net neden çıkmadı: Ölçüm kaydı açık yeni bir toplantı verisi gerekir")
    return cik


# ---------------------------------------------------------------- komut satiri

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Toplantı klasörü teşhisi (maskeli metin raporu)")
    ap.add_argument("klasor", help="toplantı klasörü (ör. toplantilar\\2026-10-12_ornek_1103)")
    ap.add_argument("--dosya", help="aynı metni bu dosyaya da yaz")
    ap.add_argument("--config", default=os.path.join(KOK, "config.json"), help="config.json yolu ('ben' için)")
    ap.add_argument("--ben", help="kullanıcının altyazıdaki adı (config'teki 'ben' yerine)")
    ap.add_argument("--hata-log", dest="hata_log", help="hata.log yolu (varsayılan: ayar.hata_log_yolu())")
    ap.add_argument("--dilim", type=int, choices=(1, 5), help="tablo dilimi (dk); varsayılan: otomatik")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.klasor):
        print(f"toplantı klasörü bulunamadı: {a.klasor}", file=sys.stderr)
        return 2
    cfg = _json_oku(a.config) if a.config and os.path.exists(a.config) else None
    metin = rapor_uret(a.klasor, cfg=cfg if isinstance(cfg, dict) else {}, ben=a.ben, hata_log=a.hata_log,
                       dilim=a.dilim * 60 if a.dilim else None)
    sys.stdout.write(metin)
    sys.stdout.flush()
    if a.dosya:
        with open(a.dosya, "w", encoding="utf-8") as f:
            f.write(metin)
        print("Kaydedildi: " + os.path.abspath(a.dosya), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
