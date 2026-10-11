"""
uzun.py — Faz 3 uzun pencere hizalamasi (saf mantik; MIMARI_v3 §4 "Uzun pencere"). Ag, saat, is parcacigi yok.

Uzun pencere (20-28 sn, sessizlikte bolunmus, onceki pencereyle 1.5 sn ortusen) Whisper'a bir kez daha gider; cikan metin
pencerenin kapsadigi KISA kliplere dagitilir. Konusmaci atamasi hic degismez: klip sinirlari ve klip_no aynen kalir,
yalniz klibin metni (uygunsa) uzun pencerenin o kliple hizalanan bolumu olur.

  pencere_hizala(uzun_metin, klipler, baglam, pencere_bas)
    1) dikis (LCS): pencerenin basi onceki pencerenin sonuyla 1.5 sn ortusur. Ortusmedeki (onceki pencereye ait) kelimeler
       'baglam' kliplerinin (onceki pencerenin son klipleri; metinleri o pencerenin sonucu) kuyrugu + ilk sahip klibin basi
       ile kelime duzeyinde en uzun ortak altdiziyle eslenir; baglama eslenen bas kelimeleri atilir (cift yazilmaz), sahip
       klibe eslenen kalir (kaybolmaz). Belirsiz eslemede ortak kelime sahip klibe yazilir. Ortusmede konusma varken hic
       esleme yoksa dikis kesin degildir: ilk sahip klip kisa metnini korur.
    2) difflib.SequenceMatcher (normalize kelimeler): kalan uzun kelimeler sahip kliplerin kisa kelimeleriyle hizalanir;
       esit / degisen bloklar kliplerine, eklenen kelimeler komsu klibe (aradaki metinsiz klip varsa ona: atilir) duser.
       Ekleme (kisada karsiligi olmayan ardisik uzun kelimeler) en fazla EKLEME_TAVAN belirtec olabilir (sinirda yarim
       duyulan kelime); daha uzun ekleme atilir: Whisper'in sessiz kuyrukta / pencere sonunda uydurdugu akici cumle kisa
       klibin halusinasyon korumalarindan (cizelge kurali, kara liste) hic gecmemistir, klip oranini dusurmeden de reddedilir.
       Iki klibin sinirindaki eklemede cumle sonu noktalamasi tasiyan belirtec (ve oncesi) sol klibe yazilir (cumleyi
       kapatan kelime onceki konusmaciya aittir); zaman esitliginde de sol secilir.
    3) karar (klip basina): oran = 2*esit / (kisa + uzun kelime). oran >= ESIK (0.6) -> klip metni uzun bolum olur; kisa ile
       uzunun farkli verdigi kelimeler 'belirsiz' listesine girer. Metin burada duz kalir (satirin raw / text'i ⟨⟩ tasimaz);
       nota / LLM'e giden gorunumde motor.satir_metni bu kelimeleri ⟨⟩ ile isaretler (MIMARI_v3 §4). oran < ESIK
       ya da pencerenin tamami < ESIK (yanlis hizalama) -> kisa metin kalir. Uzun metin kisadaki bir sayiyi kaybettiyse kisa
       kalir ve uyusmayan kisa kelimeler (sayi dahil) belirsiz olur (sayi asla sessizce dusmez, kesin de gorunmez).
"""
import difflib
import re
import unicodedata

ESIK = 0.6                  # klip / pencere hizalama orani alt siniri (altinda kisa metin kalir)
DIKIS_EN_FAZLA = 15         # dikiste karsilastirilan en fazla baglam / sahip kelimesi
DIKIS_PAY = 1               # baglam klibinin ortusmeye dusen kelime tahminine eklenen pay (esleme icin)
EKLEME_TAVAN = 2            # kisada karsiligi olmayan ardisik uzun kelime en fazla (fazlasi atilir: uydurma cumle)

_KESME = re.compile(r"['’`´]")
_AYRAC = re.compile(r"(?<=\d)[.,:](?=\d)")
_SAYI = re.compile(r"\d")
_CUMLE_BITER = re.compile(r"[.?!…][\"'’”»)\]]*$")


def tr_kucuk(s):
    """Turkce kurallariyla kucuk harf (I -> ı, İ -> i)."""
    return (s or "").replace("İ", "i").replace("I", "ı").lower()


def kelime_norm(k):
    """Karsilastirma bicimi: NFC, Turkce kucuk harf, kesme isareti bitisir ('15'te' -> '15te'), noktalama atilir; sayi
    icindeki ayrac korunur ('3,5' -> '3.5', '14:30' -> '14.30'). Yalniz noktalamadan olusan belirtec ''."""
    k = tr_kucuk(unicodedata.normalize("NFC", k or ""))
    k = _KESME.sub("", k)
    k = _AYRAC.sub("#", k)
    k = re.sub(r"[^\w#]|_", "", k)
    return k.replace("#", ".")


def kelimeler(metin):
    """Metin -> [(ozgun belirtec, normalize)]; normalize bos olanlar (yalniz noktalama) atlanir."""
    out = []
    for t in (metin or "").split():
        n = kelime_norm(t)
        if n:
            out.append((t, n))
    return out


def _sade(t):
    """belirsiz listesi icin: belirtecin bas / sondaki noktalamasi atilir (kesme ve ek korunur)."""
    return t.strip(".,;:!?\"()[]{}…«»“”-–—")


def _sayili(n):
    return bool(_SAYI.search(n))


# ---------------------------------------------------------------- LCS dikis

def benzer_kelime(a, b):
    """Dikis eslemesi icin kelime esitligi: ayni, ya da biri digerinin yarim duyulmus hali (on ek / son ek, >= 2 harf: klip
    ya da pencere siniri kelimeyi kesti: 'on' / 'onda', 'ar' / 'besar'), ya da harf benzerligi >= 0.75."""
    if a == b:
        return True
    if min(len(a), len(b)) >= 2 and (a.startswith(b) or b.startswith(a) or a.endswith(b) or b.endswith(a)):
        return True
    return min(len(a), len(b)) >= 4 and difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.75


def ortak_altdizi(ref, hyp, sahip=None, esit=None):
    """Kelime duzeyinde en uzun ortak altdizi (dinamik programlama). sahip: ref ile ayni uzunlukta bool listesi; esit uzunluktaki
    cozumlerden sahip (True) konumlara daha cok esleme yapani secilir (tekrarlanan kelime once sahip klibe yazilir: kaybolmaz).
    esit: kelime esitligi (varsayilan ==). Dondurur: [(i, j)] artan sirada."""
    n, m = len(ref), len(hyp)
    if not n or not m:
        return []
    esit = esit or (lambda x, y: x == y)
    sahip = list(sahip) if sahip is not None else [False] * n
    ayni = [[esit(ref[i], hyp[j]) for j in range(m)] for i in range(n)]
    dp = [[(0, 0)] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            en = max(dp[i - 1][j], dp[i][j - 1])
            if ayni[i - 1][j - 1]:
                a = dp[i - 1][j - 1]
                cap = (a[0] + 1, a[1] + (1 if sahip[i - 1] else 0))
                if cap > en:
                    en = cap
            dp[i][j] = en
    ciftler, i, j = [], n, m
    while i > 0 and j > 0:
        if ayni[i - 1][j - 1]:
            a = dp[i - 1][j - 1]
            if dp[i][j] == (a[0] + 1, a[1] + (1 if sahip[i - 1] else 0)):
                ciftler.append((i - 1, j - 1))
                i, j = i - 1, j - 1
                continue
        if dp[i - 1][j] == dp[i][j]:
            i -= 1
        else:
            j -= 1
    ciftler.reverse()
    return ciftler


def dikis(baglam, sahip_bas, hyp, baglam_tahmin=None):
    """Pencere dikisi. baglam: ortusmeye dusen onceki malzemenin normalize kelimeleri (sirali); sahip_bas: ilk sahip
    kliplerin kisa metninin basi; hyp: uzun pencerenin normalize kelimeleri. baglam_tahmin: ortusmedeki baglam kelimesi
    tahmini (esleme yoksa atilacak en fazla kelime). Dondurur (atilacak bas kelime sayisi, kesin_mi).

    Esleme LCS'tir; kelime esitligi benzer_kelime (yarim duyulmus kelime de eslenir). Baglama eslenen en son uzun kelimeye
    kadar olan bas atilir; baglamda ondan sonra eslenmemis kelime kaldiysa ilk sahip eslemesine kadar ayni sayida eslenmemis
    uzun kelime de atilir. Hic baglam eslemesi yoksa: ortusmede kelime beklenmiyorsa (baglam_tahmin 0) bir sey atilmaz;
    bekleniyorsa ilk sahip eslemesinden onceki kelimeler (en fazla baglam_tahmin kadar) atilir ve dikis kesin sayilmaz."""
    if not baglam or not hyp:
        return 0, True
    baglam = list(baglam)[-DIKIS_EN_FAZLA:]
    sahip_bas = list(sahip_bas)[:DIKIS_EN_FAZLA]
    ref = baglam + sahip_bas
    pencere = hyp[:len(ref) + 3]
    ciftler = ortak_altdizi(ref, pencere, [False] * len(baglam) + [True] * len(sahip_bas), esit=benzer_kelime)
    nb = len(baglam)
    b_cift = [(i, j) for i, j in ciftler if i < nb]
    s_cift = [(i, j) for i, j in ciftler if i >= nb]
    ilk_sahip = s_cift[0][1] if s_cift else len(pencere)
    if b_cift:
        i_son, j_son = b_cift[-1]
        kalan_baglam = nb - 1 - i_son
        ara = max(0, ilk_sahip - (j_son + 1))           # baglam ve sahip eslemeleri arasindaki eslenmemis uzun kelimeler
        return j_son + 1 + min(kalan_baglam, ara), True
    tahmin = nb if baglam_tahmin is None else max(0, int(baglam_tahmin))
    if not tahmin:
        return 0, True
    return min(ilk_sahip, tahmin), False


def _baglam_kuyrugu(baglam, pencere_bas):
    """Baglam kliplerinden ortusmeye dusen kuyruk kelimeleri (normalize; esleme icin DIKIS_PAY kadar fazlasi, ortusmeye
    yalniz degen klibin son kelimesi dahil: yarim duyulan parca eslensin) ve ortusmedeki tahmini kelime sayisi."""
    kuyruk, tahmin = [], 0
    for b in sorted(baglam or [], key=lambda x: x.get("bas") or 0):
        k = [n for _, n in kelimeler(b.get("text"))]
        if not k:
            continue
        bas, bit = b.get("bas"), b.get("bit")
        if isinstance(pencere_bas, (int, float)) and isinstance(bas, (int, float)) and isinstance(bit, (int, float)) \
                and bit > bas:
            pay = min(1.0, max(0.0, (bit - max(bas, pencere_bas)) / (bit - bas)))
        else:
            pay = 1.0
        if pay <= 0:
            continue
        t = min(len(k), int(round(pay * len(k))))
        tahmin += t
        kuyruk += k[-min(len(k), t + DIKIS_PAY):]
    return kuyruk[-DIKIS_EN_FAZLA:], tahmin


# ---------------------------------------------------------------- hizalama

def pencere_hizala(uzun_metin, klipler, baglam=(), pencere_bas=None, esik=ESIK):
    """Uzun pencere metnini sahip kliplere dagitir.
    klipler: pencerenin sahip oldugu kisa klipler [{"klip_no", "bas", "bit", "text"}] (zaman sirasi; metinsiz klip "" olabilir:
      satiri yoktur, ona dusen uzun kelimeler atilir). baglam: ortusmedeki onceki klipler (ayni bicim; text = son metinleri).
    pencere_bas: pencere sesinin baslangici (epoch; baglam kliplerinin ortusmeye dusen payi icin).
    Dondurur {"klipler": [{"klip_no", "text": yeni metin | None (kisa kalir), "belirsiz": [kelime], "oran"}],
              "oran": pencere orani, "dikis": atilan bas kelime, "dikis_kesin": bool,
              "atilan_ekleme": EKLEME_TAVAN'i asan eklemelerden atilan uzun kelime sayisi}."""
    sahipler = sorted(klipler or [], key=lambda x: x.get("bas") or 0)
    uzun = kelimeler(uzun_metin)
    kisa = []                                                # (klip indeksi, ozgun, normalize)
    for ci, k in enumerate(sahipler):
        kisa += [(ci, t, n) for t, n in kelimeler(k.get("text"))]
    kuyruk, tahmin = _baglam_kuyrugu(baglam, pencere_bas)
    kes, kesin = dikis(kuyruk, [n for _, _, n in kisa], [n for _, n in uzun], tahmin) if kuyruk else (0, True)
    uzun = uzun[kes:]
    sonuc = {"klipler": [], "oran": 0.0, "dikis": kes, "dikis_kesin": kesin, "atilan_ekleme": 0}
    a = [n for _, _, n in kisa]
    b = [n for _, n in uzun]
    if not a or not b:
        sonuc["klipler"] = [{"klip_no": k.get("klip_no"), "text": None, "belirsiz": [], "oran": 0.0} for k in sahipler]
        return sonuc
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    sonuc["oran"] = round(sm.ratio(), 4)
    atama = [None] * len(b)                                  # uzun kelime -> klip indeksi
    esit = [False] * len(b)
    kisa_fark = [False] * len(a)                             # kisa kelime uzunla uyusmadi (degisti / silindi)
    bos = [ci for ci, k in enumerate(sahipler) if not kelimeler(k.get("text"))]

    say = [sum(1 for x in kisa if x[0] == ci) for ci in range(len(sahipler))]
    sira_ici = []
    for i, x in enumerate(kisa):
        sira_ici.append(i - next(j for j in range(i + 1) if kisa[j][0] == x[0]))

    def zaman(i):
        """Kisa kelimenin tahmini ani: klip araligina esit dagilim (zaman damgasi yok; MIMARI_v3 §10)."""
        k = sahipler[kisa[i][0]]
        bas, bit = k.get("bas"), k.get("bit")
        if not isinstance(bas, (int, float)) or not isinstance(bit, (int, float)):
            return None
        return bas + (sira_ici[i] + 0.5) / max(1, say[kisa[i][0]]) * max(0.0, bit - bas)

    def bos_arada(sol, sag):
        """sol ve sag klip indeksleri arasinda (sinirlar dahil degil) metinsiz klip var mi -> indeksi."""
        lo = -1 if sol is None else sol
        hi = len(sahipler) if sag is None else sag
        for ci in bos:
            if lo < ci < hi:
                return ci
        return None

    def ekle_ata(i, j1, j2):
        """Kisada karsiligi olmayan uzun kelimeler b[j1:j2], kisa kelime i-1 ile i arasina: EKLEME_TAVAN'dan uzunsa atilir
        (kisa klip bunu hic duymadi: uydurma cumle); ayni klip icindeyse o klibe; aradaki metinsiz klibe (atilir); iki
        klibin sinirindaysa cumle sonu noktalamasi tasiyan son belirtece kadarki kelimeler sol klibe, kalanlar komsu
        kelimelerin tahmini anlari arasina esit dagitilip sinirin (sol bitis / sag baslangic ortasi) hangi yanina
        dusuyorsa o klibe (esitlikte sol)."""
        if j2 - j1 > EKLEME_TAVAN:
            sonuc["atilan_ekleme"] += j2 - j1
            return
        sol = kisa[i - 1][0] if i > 0 else None
        sag = kisa[i][0] if i < len(kisa) else None
        hedef = bos_arada(sol, sag)
        if hedef is not None or sol is None or sag is None or sol == sag:
            hedef = hedef if hedef is not None else (sol if sol is not None else sag)
            for t in range(j1, j2):
                atama[t] = hedef
            return
        t_sol, t_sag = zaman(i - 1), zaman(i)
        try:
            sinir = (float(sahipler[sol]["bit"]) + float(sahipler[sag]["bas"])) / 2.0
        except (KeyError, TypeError, ValueError):
            sinir = None
        son_nokta = max([r_ for r_, t in enumerate(range(j1, j2)) if _CUMLE_BITER.search(uzun[t][0])], default=-1)
        for r_, t in enumerate(range(j1, j2)):
            if r_ <= son_nokta or t_sol is None or t_sag is None or sinir is None:
                atama[t] = sol
                continue
            tw = t_sol + (r_ + 1) / (j2 - j1 + 1) * (t_sag - t_sol)
            atama[t] = sol if tw <= sinir + 1e-6 else sag

    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for t in range(j2 - j1):
                atama[j1 + t] = kisa[i1 + t][0]
                esit[j1 + t] = True
        elif op == "replace":
            nk, nu = i2 - i1, j2 - j1
            if nu <= nk:                                     # uzun kelime az / esit: orantili ('on da' -> 'onda')
                for t in range(nu):
                    atama[j1 + t] = kisa[i1 + (t * nk) // nu][0]
            else:                                            # uzun fazla: once bire bir, artan kelimeler ekleme sayilir
                for t in range(nk):
                    atama[j1 + t] = kisa[i1 + t][0]
                ekle_ata(i2, j1 + nk, j2)
            for i in range(i1, i2):
                kisa_fark[i] = True
        elif op == "delete":
            for i in range(i1, i2):
                kisa_fark[i] = True
        else:                                                # insert: kisada karsiligi yok
            ekle_ata(i1, j1, j2)
    pencere_iyi = sonuc["oran"] >= esik
    ilk_dolu = kisa[0][0]
    for ci, k in enumerate(sahipler):
        ks = [x for x in kisa if x[0] == ci]
        ku = [(uzun[j][0], uzun[j][1], esit[j]) for j in range(len(b)) if atama[j] == ci]
        if not ks:                                           # metinsiz klip: satiri yok, dusen kelimeler atilir
            sonuc["klipler"].append({"klip_no": k.get("klip_no"), "text": None, "belirsiz": [], "oran": 0.0})
            continue
        m_esit = sum(1 for _, _, e in ku if e)
        oran = 2.0 * m_esit / (len(ks) + len(ku)) if (ks or ku) else 0.0
        kayit = {"klip_no": k.get("klip_no"), "text": None, "belirsiz": [], "oran": round(oran, 4)}
        sonuc["klipler"].append(kayit)
        if not pencere_iyi or oran < esik or not ku:
            continue                                         # yanlis hizalama: kisa metin kalir
        k_sayi = sorted(n for _, _, n in ks if _sayili(n))
        u_sayi = sorted(n for _, n, _ in ku if _sayili(n))
        sayi_kaybi = any(k_sayi.count(n) > u_sayi.count(n) for n in set(k_sayi))
        if ci == ilk_dolu and not kesin:
            continue                                         # dikis kesin degil: ilk sahip klip kisa kalir
        if sayi_kaybi:                                       # uzun metin bir sayiyi dusurdu / degistirdi: kisa kalir
            fark = [_sade(t) for (cj, t, n), f in zip(kisa, kisa_fark) if cj == ci and f]
            kayit["belirsiz"] = list(dict.fromkeys(x for x in fark if x))
            continue
        kayit["text"] = " ".join(t for t, _, _ in ku)
        kayit["belirsiz"] = list(dict.fromkeys(x for x in (_sade(t) for t, _, e in ku if not e) if x))
    return sonuc


def son_cumle(metin, tavan=200):
    """Metnin son cumlesi (Whisper prompt'u: onceki pencerenin son cumlesi); en fazla tavan karakter."""
    parcalar = [p.strip() for p in re.split(r"(?<=[.?!…])\s+", (metin or "").strip()) if p.strip()]
    if not parcalar:
        return ""
    return parcalar[-1][-tavan:].strip()
