"""
sozluk.py — terimler ve alias'lar (yanlis duyulan -> dogru), birebir ve bulanik esleme.

sozluk.json:
{
  "terimler": ["IFRS 9", "Jira", "commit", "Ata Çelik", ...],
  "aliaslar": {"ı t 9": "IFRS 9", "komplo tit": "commit", ...}
}
Eski sozluk.txt varsa ilk calismada otomatik tasinir.
"""
import difflib
import json
import os
import re

DOSYA = "sozluk.json"
ESKI_DOSYA = "sozluk.txt"
BULANIK_ESIK = 0.86          # normalize edilmis metinde benzerlik esigi
BULANIK_MIN = 5              # bu uzunluktan kisa terimler bulanik eslenmez (yanlis pozitif)


def normalize(s):
    s = s.replace("İ", "i").replace("I", "ı").lower()
    s = re.sub(r"['’`´]", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def terim_gibi_mi(t):
    """Sozluk terimi olabilecek kisa ifade mi? (cumle degil)"""
    t = (t or "").strip()
    if not t or len(t) > 40 or len(t.split()) > 4:
        return False
    if t[-1] in ".?!:;," :
        return False
    return True


def fark_terimi(yanlis, dogru):
    """'... IFRS PD kolonunun veri tipi number 86 ...' gibi tam ifade duzeltmelerinden yalnizca
    DEGISEN kisa parcayi terim olarak dondurur; degisen parca da cumle boyundaysa None."""
    import difflib
    a, b = (yanlis or "").split(), (dogru or "").split()
    if not b:
        return None
    def ozel_mi(k):          # buyuk harf / rakam iceren kelime: kisaltma, ad, surum ("PD", "9", "IFRS")
        k = k.strip(" .,;:!?")
        return bool(k) and (k[:1].isupper() or any(c.isdigit() for c in k))
    sm = difflib.SequenceMatcher(None, [normalize(x) for x in a], [normalize(x) for x in b])
    araliklar = [(j1, j2) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal" and j2 > j1]
    if not araliklar:
        return dogru.strip() if terim_gibi_mi(dogru) and len(b) <= 3 else None
    j1, j2 = max(araliklar, key=lambda x: x[1] - x[0])
    while j2 < len(b) and ozel_mi(b[j2]):          # "IFRS" + "PD", "Tera" + "9"
        j2 += 1
    while j1 > 0 and ozel_mi(b[j1 - 1]):
        j1 -= 1
    terim = " ".join(b[j1:j2]).strip(" .,;:!?")
    return terim if terim_gibi_mi(terim) else None


class Sozluk:
    def __init__(self, yol=DOSYA):
        self.yol = yol
        self.terimler = []
        self.aliaslar = {}                       # normalize(yanlis) -> dogru
        self.etiketler = {}                      # normalize(terim) -> [seri, ...]
        if os.path.exists(yol):
            with open(yol, encoding="utf-8") as f:
                v = json.load(f)
            self.terimler = v.get("terimler", [])
            self.aliaslar = {normalize(k): d for k, d in v.get("aliaslar", {}).items()}
            # terim -> toplanti serileri (bos/yok = genel terim, her toplantida gecerli)
            self.etiketler = {normalize(k): list(v_) for k, v_ in v.get("etiketler", {}).items()}
        elif os.path.exists(ESKI_DOSYA):
            self._txt_tasi()
        self._derle()

    # ---------- dosya ----------
    def _txt_tasi(self):
        with open(ESKI_DOSYA, encoding="utf-8") as f:
            for satir in f:
                satir = satir.strip()
                if not satir or satir.startswith("#"):
                    continue
                if "->" in satir:
                    y, d = [p.strip() for p in satir.split("->", 1)]
                    self.aliaslar[normalize(y)] = d
                    self._terim_ekle(d)
                else:
                    self._terim_ekle(re.sub(r"\s*\(.*?\)\s*$", "", satir))   # parantezli aciklamayi at
        self.kaydet()

    def kaydet(self):
        with open(self.yol, "w", encoding="utf-8") as f:
            json.dump({"terimler": self.terimler, "aliaslar": self.aliaslar,
                       "etiketler": {k: v for k, v in self.etiketler.items() if v}},
                      f, ensure_ascii=False, indent=1)

    # ---------- icerik ----------
    def _terim_ekle(self, t, seri=None):
        t = t.strip()
        if not t:
            return
        n = normalize(t)
        if n not in {normalize(x) for x in self.terimler}:
            self.terimler.append(t)
        if seri:
            etk = self.etiketler.setdefault(n, [])
            if seri not in etk:
                etk.append(seri)

    def alias_ekle(self, yanlis, dogru, seri=None):
        """seri: terimin ogrenildigi toplanti serisi (slug). Elle/genel eklemede None.
        Alias tam ifade icin kaydedilir (deterministik uygulanir); TERIM olarak yalnizca
        degisen kisa parca eklenir — cumleler sozluge girip LLM'e konu olarak gitmesin."""
        y = normalize(yanlis)
        if not y or not dogru.strip():
            return
        self.aliaslar[y] = dogru.strip()
        terim = fark_terimi(yanlis, dogru)
        if terim:
            self._terim_ekle(terim, seri)
        self._derle()
        self.kaydet()

    def terim_ekle(self, t, seri=None):
        self._terim_ekle(t, seri)
        self._derle()
        self.kaydet()

    def bilinen_mi(self, yanlis):
        return normalize(yanlis) in self.aliaslar

    def terimler_icin(self, seri=None):
        """Bu toplanti serisinde gecerli terimler: etiketi olmayanlar (genel) + bu seriye etiketliler.
        Baska toplantilarda ogrenilen ozel terimler buraya sizmaz."""
        secilen = []
        for t in self.terimler:
            if len(t) > 40 or len(t.split()) > 4:      # cumle/ifade: alias olarak uygulanir, ipucu olmaz
                continue
            etk = self.etiketler.get(normalize(t)) or []
            if not etk or (seri and seri in etk):
                secilen.append(t)
        return secilen

    def metin(self, seri=None):
        return "; ".join(self.terimler_icin(seri)) or "-"

    def _derle(self):
        # bulanik adaylar: (normalize edilmis hedef, yazilacak dogru)
        self._adaylar = [(k, d) for k, d in self.aliaslar.items() if len(k) >= BULANIK_MIN]
        self._adaylar += [(normalize(t), t) for t in self.terimler if len(normalize(t)) >= BULANIK_MIN]

    # ---------- uygulama ----------
    def uygula(self, s):
        """Metne alias'lari (birebir) ve bulanik terim eslemesini uygular.
        Dondurur: (yeni_metin, [(eski, yeni), ...])"""
        degisiklikler = []
        tokenler = re.findall(r"\S+", s)
        if not tokenler:
            return s, degisiklikler
        cikti, i = [], 0
        while i < len(tokenler):
            eslesti = False
            for n in (3, 2, 1):
                if i + n > len(tokenler):
                    continue
                pencere = " ".join(tokenler[i:i + n])
                nrm = normalize(pencere)
                if not nrm:
                    continue
                dogru = self.aliaslar.get(nrm)
                if dogru is None:
                    for hedef, d in self._adaylar:
                        if abs(len(hedef) - len(nrm)) <= 3 and nrm != hedef \
                                and difflib.SequenceMatcher(None, nrm, hedef).ratio() >= BULANIK_ESIK:
                            dogru = d
                            break
                if dogru is not None and normalize(dogru) != nrm:
                    # pencerenin sonundaki noktalama isaretini koru
                    kuyruk = re.search(r"[.,!?;:]+$", tokenler[i + n - 1])
                    cikti.append(dogru + (kuyruk.group(0) if kuyruk else ""))
                    degisiklikler.append((pencere, dogru))
                    i += n
                    eslesti = True
                    break
            if not eslesti:
                cikti.append(tokenler[i])
                i += 1
        return " ".join(cikti), degisiklikler
