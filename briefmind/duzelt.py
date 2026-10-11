"""
duzelt.py — Whisper satirlarinda terim/ad duzeltmesi: LLM yalniz ONERIR, deterministik kapi karar verir.

duzelt(satirlar, adaylar, adlar, sor_fn=None) -> (yeni_satirlar, rapor)
  satirlar: [{"id", "text" ya da "raw", ...}]; adaylar: terim.adaylar (<=40); adlar: katilimci adlari.
  Model satiri yeniden yazmaz; yalniz [{"satir", "eski", "yeni", "neden"}] onerir. Her oneri kapi()'dan
  gecer, kabul edilen tek gecis satira bayt bayt korunarak yazilir. 'yazim' onerisi uygulanmaz, kelime
  'belirsiz' olur.

kapi(satir_metni, oneri, adaylar, adlar) -> (kabul, neden)   VE kurali (MIMARI_v3 §4 "Diff kapisi"):
  - sema: yalniz satir/eski/yeni/neden alanlari, metin alanlar, neden terim|ad|yazim
  - eski satirda kelime sinirli ve BIREBIR tam bir kez gecer (birden fazla: belirsiz, uygulanmaz)
  - sayi/tarih/para degismez; kelime sayisi farki <=1; eski satir kelimelerinin <=%30'u (en az 1 kelime)
  - terim|ad: yeni = aday (+ ') + ek; eski koku <-> aday normalize edit uzakligi <=0.4 (<=3 harfli
    adayda 0: yalniz buyuk harf/kesme farki); eski yaygin
    Turkce kelime degil; ek eskideki ekle ayni (arsifonem), Turkce unlu/unsuz uyumlu; ozel adda kesme
    zorunlu ('Kubernetesin' reddedilir); eskide fazladan ek kalmaz ('kampanyacı' -> 'kampanya' reddedilir).
  - eski (koku) zaten listedeki baska bir aday/ad degil (Sercan -> Serkan, Jenkins -> Jenkinsfile: eski_aday); t'den
    daha yakin baska aday/ad yok (yakin_aday); terime soylenmemis kelime eklenmez (kubernetis -> Kubernetes API) ve
    eskinin terime katkisiz kelimesi yutulmaz (kubernetis pod'u -> Kubernetes'i)
  - terimde: Turkce ses yapisina uyan kok (kelime.turkce_gibi_mi: jest, sap, redif) terime cevrilmez; yalniz harf
    farkiysa red, degilse kelime belirsiz isaretlenir
  - yaygin kelime listesi yoksa (eksik kurulum) fail-closed: yalniz harf/kesme farki kabul edilir (liste_yok)
  - OGRENILMIS varyant (terim.varyant_ciftleri: bagimsiz kanitla ogrenilen / elle 'eski -> terim'): eski bu varyanttan
    biri ve yeni onun terimiyse uzaklik, yaygin-kelime, yakin-aday ve Turkce-ses-yapisi kurallari aranmaz (kanit
    zaten bunlari karsiladi); sayi/tarih, ek uyumu, kesme, kelime sayisi ve satir orani kurallari AYNEN uygulanir
"""
import re

import kelime
import llm

NEDENLER = ("terim", "ad", "yazim")
ALANLAR = ("satir", "eski", "yeni", "neden")
ESIK_UZAKLIK = 0.4
ORAN_SATIR = 0.30
EN_FAZLA_ONERI = 25                  # cagri basina (cikti butcesi llm.butce('duzeltme') = 1200)
PARCA_KARAKTER = 9000                # ~3000 token satir metni (MIMARI_v3 §4 Maliyet)
EN_FAZLA_ADAY = 60
EN_FAZLA_VARYANT = 15
EN_UZUN_YENI = 80

KESME = "'’"
_SAYI = re.compile(r"\d+(?:[.,:/]\d+)*|[₺$€£%]")
_KORUNAN = set((
    "sıfır bir iki üç dört beş altı yedi sekiz dokuz on yirmi otuz kırk elli altmış yetmiş seksen doksan "
    "yüz bin milyon milyar trilyon buçuk yarım çeyrek yüzde "
    "ocak şubat mart nisan mayıs haziran temmuz ağustos eylül ekim kasım aralık "
    "pazartesi salı çarşamba perşembe cuma cumartesi pazar "
    "tl lira kuruş dolar euro avro sterlin").split())
_GECERSIZ = re.compile(r"[\x00-\x1f\x7f<>{}\[\]|\\]|\(\?\)")
_EK_HARF = re.compile(r"[a-zçğıöşüâîû]*")
_YUMUSAT = {"p": "b", "ç": "c", "t": "d", "k": "ğ"}
# Kisaltma harf adlari (Turkce/Ingilizce okunus): SQL'e / SQL'ye / SQL'de hepsi gecerli
_KISALTMA_BAGLAM = (("e", True, False), ("e", False, False), ("e", False, True), ("a", True, False))


# ---------- yardimcilar ----------

def uzaklik(a, b):
    """Normalize (bosluksuz) Levenshtein uzakligi / uzun olanin boyu: 0 ayni, 1 tamamen farkli."""
    a, b = kelime._sade(a), kelime._sade(b)
    if not a or not b:
        return 1.0
    onceki = list(range(len(b) + 1))
    for i, ha in enumerate(a, 1):
        simdi = [i]
        for j, hb in enumerate(b, 1):
            simdi.append(min(onceki[j] + 1, simdi[j - 1] + 1, onceki[j - 1] + (ha != hb)))
        onceki = simdi
    return onceki[-1] / max(len(a), len(b))


def terim_uzakligi(eski_kok, t):
    """uzaklik, buyuk 'I' iki okunusla: Turkce 'ı' ve alinti 'i' (API -> 'apı' / 'api'); kucuk olan."""
    d = uzaklik(eski_kok, t)
    return min(d, uzaklik(eski_kok, t.replace("I", "i"))) if "I" in t else d


def gecisler(satir, eski):
    """eski'nin satirdaki kelime sinirli gecis konumlari ('ata' 'hatalar' icinde, 'cira' "cira'ya" icinde
    sayilmaz: kesme isareti kelimenin parcasidir, ek yeniden yazilmasin)."""
    return [m.start() for m in re.finditer(r"(?<![\w'’])" + re.escape(eski) + r"(?![\w'’])", satir)]


def _korunanlar(s):
    """Sayi, tarih, para belirten parcalar (sirali). Cekimli bicim koke indirgenir: 'martta' -> 'mart'."""
    parca = _SAYI.findall(s)
    for w in kelime.normalize(s).split():
        if w in _KORUNAN or any(k in _KORUNAN for k, _ in kelime.kok_ayir(w, en_kisa=2)):
            parca.append(w)
    return sorted(parca)


def _kisaltma_mi(t):
    harf = [h for h in t if h.isalpha()]
    return 1 < len(harf) <= 6 and all(h.isupper() for h in harf)


def _ozel_mi(t):
    """Buyuk harfle baslayan terim/ad ozel ad sayilir: ek kesme isaretiyle ayrilir (Jira'ya)."""
    return t[:1].isupper()


def _aday_ayir(yeni, t):
    """yeni = t (+ ') + ek mi? Dondurur (ek, kesme_var, yumusama) ya da None."""
    govde = None
    if yeni.startswith(t):
        govde = t
    elif t[:1].islower() and yeni[:1].isupper() and kelime.normalize(yeni[:1]) == t[:1] \
            and yeni[1:len(t)] == t[1:]:
        govde = t                                    # kucuk harfli terim cumle basinda: 'Pipeline'
    yumusama = False
    if govde is None and not _ozel_mi(t) and t[-1:] in _YUMUSAT \
            and yeni.startswith(t[:-1] + _YUMUSAT[t[-1]]):
        govde, yumusama = t, True                    # kitap -> kitabı (yalniz ozel olmayan terim)
    if govde is None:
        return None
    kalan = yeni[len(govde):]
    kesme = bool(kalan) and kalan[0] in KESME
    ek = kalan[1:] if kesme else kalan
    if not _EK_HARF.fullmatch(ek):
        return None
    if yumusama and (kesme or ek[:1] not in kelime.UNLULER):
        return None
    return ek, kesme, yumusama


def _adaylar(neden, adaylar, adlar):
    if neden == "ad":
        liste = []
        for a in adlar or ():
            a = (a or "").strip()
            if a:
                liste.append(a)
                liste.extend(p for p in a.split() if len(p) >= 2)
    else:
        liste = [(a or "").strip() for a in adaylar or ()]
    goruldu, cikti = set(), []
    for a in sorted((a for a in liste if a), key=len, reverse=True):     # uzun aday once
        if a not in goruldu:
            goruldu.add(a)
            cikti.append(a)
    return cikti


def _ek_uyumlu_mu(t, ek, eski_kok, eski_ek):
    """Ek, terimin yazilis uyumuna, kisaltmanin harf okunusuna ya da eskide duyulan ayni eke uyuyor mu?"""
    if not ek:
        return True
    if kelime.ek_gecerli_mi(t, ek):
        return True
    if _kisaltma_mi(t) and any(kelime.ek_gecerli_mi(t, ek, baglam=b) for b in _KISALTMA_BAGLAM):
        return True
    # okunusu yazilisindan farkli alinti: ek duyulanla birebir ayni ve duyulan kokun uyumuna uygun
    return ek == eski_ek and bool(eski_kok) and kelime.ek_gecerli_mi(eski_kok, ek)


def _rakipler(t, adaylar, adlar):
    """t disindaki butun aday ve adlar (ad parcalari dahil), neden ne olursa olsun: (ozgun, sade)."""
    ts = kelime._sade(t)
    goruldu, cikti = set(), []
    for a in _adaylar("terim", adaylar, adlar) + _adaylar("ad", adaylar, adlar):
        sa = kelime._sade(a)
        if sa and sa != ts and sa not in goruldu:
            goruldu.add(sa)
            cikti.append((a, sa))
    return cikti


def _eski_kokleri(eski, eski_n, eski_kok):
    """eski'nin olasi kokleri (sade): tamami, onerinin ek ayrimi, kesmeden onceki kisim, tek kelimede ek ayrimlari."""
    kokler = {eski_n, eski_kok}
    kel = eski.split()
    son = re.split("[" + KESME + "]", kel[-1])[0] if kel else ""
    if son:
        kokler.add(kelime._sade(" ".join(kel[:-1] + [son])))
    if len(kel) == 1:
        kokler.update(k for k, _ in kelime.kok_ayir(eski_n, en_kisa=3))
    return {k for k in kokler if k}


def _kelime_dusurme(eski, ek, t, d):
    """ke > ky: eski'nin bir kelimesi atildiginda uzaklik artmiyorsa o kelime terimin parcasi degil (yutulmus).
    Dondurur atilabilen kelime ya da None ('kubernetis pod'u' -> Kubernetes'i: 'pod'u' yutulur)."""
    kel = eski.split()
    for i in range(len(kel)):
        kalan = kel[:i] + kel[i + 1:]
        kok = kelime._sade(" ".join(kalan))
        if i != len(kel) - 1 and ek:
            kok = kok[:-len(ek)] if len(kok) > len(ek) else ""
        if kok and terim_uzakligi(kok, t) <= d:
            return kel[i]
    return None


def _kelime_ekleme(eski_kok, t, d):
    """ky > ke: terimden bir kelime atildiginda eski'ye uzaklik artmiyorsa o kelime soylenmemis (eklenmis).
    ('kubernetis' -> 'Kubernetes API': 'API' eklenir)."""
    tk = t.split()
    for i in range(len(tk)):
        alt = " ".join(tk[:i] + tk[i + 1:])
        if alt and terim_uzakligi(eski_kok, alt) <= d:
            return tk[i]
    return None


def _varyant_koku(vn, t):
    """Varyantin KENDI koku: ek ayrimlarindan terime en yakin olan (esitlikte en uzun). 'cenkinse' (Jenkins) ->
    'cenkins'; 'cenk' gibi daha kisa (ve yaygin kelime olabilen) kokler varyantin koku sayilmaz."""
    adaylar = [k for k, _ in kelime.kok_ayir(vn, en_kisa=3)]
    if not adaylar:
        return None
    return min(adaylar, key=lambda k: (terim_uzakligi(k, t), -len(k)))


def _varyant_izinli(eski_n, eski_kok, t, varyantlar):
    """eski (sade, ekli) / eski_kok (ekten arinmis) ogrenilmis bir varyant mi ve varyantin terimi t mi?
    Yalniz varyantin kendisi (eski_n == varyant), varyant + ek (eski_kok == varyant) ya da varyantin kendi koku + ek
    (eski_kok == _varyant_koku) eslesir; varyanttan turetilen daha kisa kokler ('cenkinse' -> 'cenk') izinli DEGILDIR.
    Yaygin kelime kurali kok yolunda surer: varyantin koku yaygin Turkce kelimeyse ('voltu' -> 'volt', 'tablosuna' ->
    'tablosu') o kok varyant sayilmaz ('220 volt'taki 'volt' Vault olmaz). Dondurur eslesen (varyant, terim) ya da None."""
    ts = kelime._sade(t)
    for v, vt in varyantlar or ():
        if not isinstance(v, str) or not isinstance(vt, str) or kelime._sade(vt) != ts:
            continue
        vn = kelime._sade(v)
        if not vn:
            continue
        if eski_n == vn or eski_kok == vn:
            return v, vt
        if eski_kok and eski_kok == _varyant_koku(vn, t) and not kelime.yaygin_mi(eski_kok):
            return v, vt
    return None


def _yaygin_var_mi(eski, eski_kok):
    if eski_kok and kelime.yaygin_mi(eski_kok):
        return True
    return any(kelime.yaygin_mi(w) for w in eski.split())


# ---------- kapi ----------

def kapi(satir_metni, oneri, adaylar, adlar, varyantlar=None):
    """Tek oneriyi deterministik kurallarla sinar. Dondurur (kabul, neden); neden 'tamam' ya da red kodu.
    varyantlar: [(eski, terim)] ogrenilmis / elle varyant eslemeleri (terim.varyant_ciftleri)."""
    if not isinstance(oneri, dict) or set(oneri) - set(ALANLAR):
        return False, "sema_disi"
    eski, yeni, neden = oneri.get("eski"), oneri.get("yeni"), oneri.get("neden")
    if not all(isinstance(x, str) for x in (eski, yeni, neden)) or not isinstance(satir_metni, str):
        return False, "sema_disi"
    if neden not in NEDENLER:
        return False, "neden_gecersiz"
    eski, yeni = eski.strip(), yeni.strip()
    if not eski or not yeni or not re.search(r"\w", eski):
        return False, "bos"
    if neden == "yazim":
        return False, "yazim_uygulanmaz"
    if eski == yeni:
        return False, "degisiklik_yok"
    if _GECERSIZ.search(yeni) or _GECERSIZ.search(eski) or len(yeni) > EN_UZUN_YENI:
        return False, "gecersiz_karakter"
    if not eski[0].isalnum() or not eski[-1].isalnum():
        return False, "noktalama"                     # eski noktalamayla biterse yeni onu silebilir
    g = gecisler(satir_metni, eski)
    if not g:
        return False, "eski_yok"
    if len(g) > 1:
        return False, "coklu_gecis"
    if _korunanlar(eski) != _korunanlar(yeni):
        return False, "sayi_degisti"
    ke, ky = len(eski.split()), len(yeni.split())
    if abs(ke - ky) > 1:
        return False, "kelime_sayisi"
    if ke > satir_payi(satir_metni):
        return False, "satir_orani"

    # terim | ad: yeni = aday (+ ') + ek
    eslesme = None
    for t in _adaylar(neden, adaylar, adlar):
        a = _aday_ayir(yeni, t)
        if a is not None:
            eslesme = (t,) + a
            break
    if eslesme is None:
        return False, "aday_yok"
    t, ek, kesme, _yumusama = eslesme
    if _ozel_mi(t) and ek and not kesme:
        return False, "kesme_isareti"                 # Kubernetesin -> Kubernetes'in olmali
    if kesme and (not ek or not (_ozel_mi(t) or any(h in eski for h in KESME))):
        return False, "kesme_isareti"                 # kucuk harfli terime kesme eklenmez: kampanya'yı
    eski_n = kelime._sade(eski)
    if ek:
        eski_ek = eski_n[-len(ek):] if len(eski_n) > len(ek) else ""
        if kelime.arsifonem(eski_ek) != kelime.arsifonem(ek):
            return False, "ek_degisti"
        eski_kok = eski_n[:-len(ek)]
    else:
        eski_ek, eski_kok = "", eski_n
    d = terim_uzakligi(eski_kok, t)
    varyant = _varyant_izinli(eski_n, eski_kok, t, varyantlar) if neden == "terim" else None
    if varyant:
        # kanitli varyant: benzerlik (uzaklik / yaygin kelime / yakin aday / Turkce ses yapisi) kurallari aranmaz; EK kurallari
        # aynen surer (ek kaybi: 'kubernetisin' -> 'Kubernetes' reddedilir; ek uyumu) ve kelime sayisi varyantla ayni olmali
        if ke != len(varyant[0].split()) or ky != len(t.split()):
            return False, "varyant_kelime"
        for x, _s in kelime.kok_ayir(eski_kok, tureme=True, esnek=True):
            if terim_uzakligi(x, t) < d:
                return False, "ek_kaybi"
        if not _ek_uyumlu_mu(kelime._sade(t), ek, eski_kok, eski_ek):
            return False, "ek_uyumsuz"
        return True, "varyant"
    if d > ESIK_UZAKLIK or (d > 0 and len(kelime._sade(t)) <= 3):
        return False, "uzaklik"                       # <=3 harfli terimde (SQL, API) yalniz yazim farki
    # eski kokte fazladan ek kaldiysa (kampanyacı -> kampanya, raporu -> rapor) yeni eki silmis demektir
    for x, _s in kelime.kok_ayir(eski_kok, tureme=True, esnek=True):
        if terim_uzakligi(x, t) < d:
            return False, "ek_kaybi"
    if not _ek_uyumlu_mu(kelime._sade(t), ek, eski_kok, eski_ek):
        return False, "ek_uyumsuz"
    # eski zaten listedeki baska bir terim/ad: dogru olani baskasina cevirme (Sercan -> Serkan, Jenkins -> Jenkinsfile)
    kokler = _eski_kokleri(eski, eski_n, eski_kok)
    rakipler = _rakipler(t, adaylar, adlar)
    if any(sa in kokler for _a, sa in rakipler):
        return False, "eski_aday"
    # t'den daha yakin baska aday/ad varsa hangisi kastedildi belli degil (kucuk harfli yaygin Turkce terim haric:
    # Whisper yaygin kelimeyi bozmaz, 'komit' komite degil commit'tir)
    for a, sa in rakipler:
        if (a[:1].isupper() or not kelime.yaygin_mi(a)) and terim_uzakligi(eski_kok, a) < d:
            return False, "yakin_aday"
    if ky > ke and _kelime_ekleme(eski_kok, t, d):
        return False, "kelime_eklendi"
    if ke > ky and _kelime_dusurme(eski, ek, t, d):
        return False, "kelime_yutuldu"
    if _yaygin_var_mi(eski, eski_kok):
        return False, "yaygin_kelime"
    if neden == "terim" and kelime.turkce_gibi_mi(eski_kok):
        # Turkce ses yapisina uyan (sozlukte olmayabilir: redif, jest, sap) kok terime cevrilmez; yalniz harf/kesme
        # farki ise kozmetik, degilse kelime belirsiz isaretlenir (_isle)
        if d == 0:
            return False, "turkce_harf"
        if ke == 1:
            return False, "turkce_kelime"
    if d > 0 and not kelime.liste_var_mi():
        return False, "liste_yok"                     # fail-closed: liste yoksa yalniz harf/kesme farki
    return True, "tamam"


def satir_payi(satir_metni):
    """Bir satirda degisebilecek en fazla kelime: %30 (kisa satirda en az 1 kelime)."""
    return max(1, int(ORAN_SATIR * len(satir_metni.split()) + 1e-9))


# ---------- LLM ----------

DUZELT_SISTEM = (
    "Aşağıdaki satırlar konuşmayı yazıya çeviren Whisper modelinin ham çıktısıdır. Whisper, bilmediği "
    "terimleri ve kişi adlarını sesçe yakın Türkçe kelimelere ya da uydurma yazımlara çevirir. Görevin: "
    "TERİMLER ya da ADLAR listesindeki bir öğenin bozuk yazıldığı yerleri bulup öneri listesi döndürmek.\n"
    "Kurallar:\n"
    "- Satırı YENİDEN YAZMA. Her öneri yalnız bozuk kelimeyi (en çok iki kelime) kapsar.\n"
    "- satir: köşeli parantezdeki satır numarası. eski: satırdaki ifadenin BİREBİR kopyası "
    "(büyük/küçük harf ve ekleriyle).\n"
    "- yeni: listedeki yazım + eski'deki ekin aynısı. Ek silinmez, eklenmez, değiştirilmez; büyük harfli "
    "terim ve adlarda ek kesme işaretiyle ayrılır (Jira'ya, Kubernetes'in).\n"
    "- neden: TERİMLER'deki öğe için 'terim', ADLAR'daki kişi için 'ad'. Listede olmayan ama bozuk duran "
    "kelime için 'yazim' (yeni = tahminin; metne uygulanmaz, yalnız işaretlenir).\n"
    "- Anlamlı, gerçek bir Türkçe kelimeyi terime çevirme. Sayı, tarih, saat ve tutarlara dokunma.\n"
    "- BİLİNEN YAZIMLAR: daha önce bu terimin bozuk duyulduğu biçimler (bozuk -> terim); satırda aynısını "
    "görürsen aynı terime çevir (neden 'terim').\n"
    "- Emin değilsen öneri yazma; bozuk yer yoksa boş liste döndür. En fazla 25 öneri. Yalnız JSON döndür.\n"
    "Örnek (uydurma): TERİMLER: Jenkins; Kubernetes; Jira; commit  ADLAR: Nil Ekinci\n"
    "[1] cenkinse bakarız -> {\"satir\": 1, \"eski\": \"cenkinse\", \"yeni\": \"Jenkins'e\", \"neden\": \"terim\"}\n"
    "[2] kubernetis kümesi düştü -> {\"satir\": 2, \"eski\": \"kubernetis\", \"yeni\": \"Kubernetes\", "
    "\"neden\": \"terim\"}\n"
    "[3] ciraya kayıt aç -> {\"satir\": 3, \"eski\": \"ciraya\", \"yeni\": \"Jira'ya\", \"neden\": \"terim\"}\n"
    "[4] ekincinin dediği gibi -> {\"satir\": 4, \"eski\": \"ekincinin\", \"yeni\": \"Ekinci'nin\", \"neden\": \"ad\"}\n"
    "[5] dün deplay ettik -> {\"satir\": 5, \"eski\": \"deplay\", \"yeni\": \"deploy\", \"neden\": \"yazim\"}\n"
    "[6] komite toplandı / 3 milyon bütçe -> öneri yok (komite gerçek kelime; sayıya dokunulmaz)"
)


def oneri_semasi(id_tamsayi=True):
    oge = {"type": "object",
           "properties": {"satir": {"type": "integer" if id_tamsayi else "string"},
                          "eski": {"type": "string"}, "yeni": {"type": "string"},
                          "neden": {"type": "string", "enum": list(NEDENLER)}},
           "required": list(ALANLAR), "additionalProperties": False}
    return {"type": "object",
            "properties": {"oneriler": {"type": "array", "maxItems": EN_FAZLA_ONERI, "items": oge}},
            "required": ["oneriler"], "additionalProperties": False}


def _bilinen_yazimlar(varyantlar, parca):
    """Bu parcanin metninde gecen varyantlar ('bozuk -> terim', en fazla EN_FAZLA_VARYANT): komut istemi kisa kalir."""
    kokler = set()
    for _sid, metin in parca:
        kokler |= {kelime._sade(w) for w in metin.split()}
    cikti = []
    for v, t in varyantlar or ():
        if not isinstance(v, str) or not isinstance(t, str) or not v.strip():
            continue
        vs = kelime._sade(v)
        if vs and any(k.startswith(vs) for k in kokler) and f"{v} -> {t}" not in cikti:
            cikti.append(f"{v} -> {t}")
    return cikti[:EN_FAZLA_VARYANT]


def mesajlar(parca, adaylar, adlar, varyantlar=None):
    """parca: [(id, metin)]. Kisa kullanici mesaji: listeler + (varsa) bilinen yazimlar + numarali satirlar."""
    terimler = "; ".join(a for a in (adaylar or [])[:EN_FAZLA_ADAY] if a) or "-"
    kisiler = "; ".join(a for a in (adlar or []) if a) or "-"
    govde = "\n".join(f"[{sid}] {metin}" for sid, metin in parca)
    bilinen = _bilinen_yazimlar(varyantlar, parca)
    bilinen = ("BİLİNEN YAZIMLAR: " + "; ".join(bilinen) + "\n") if bilinen else ""
    return [{"role": "system", "content": DUZELT_SISTEM},
            {"role": "user", "content": f"TERİMLER: {terimler}\nADLAR: {kisiler}\n{bilinen}\nSATIRLAR:\n{govde}"}]


def _llm_sor(msj, sema):
    # dusunme kapali: json_schema dusunmeyle uygulanmiyor (llm._json_sor notu); butce eski cagriyla ayni
    return llm._json_sor(msj, llm.butce("duzeltme"), sema, temperature=0.2, dusunme=False)


def _oneriler(cevap):
    """Model cevabini oneri listesine cevirir; bicim bozuksa None."""
    if cevap is None:
        return None
    if isinstance(cevap, str):
        try:
            cevap = llm.json_ayikla(cevap)
        except Exception:
            return None
    if isinstance(cevap, dict):
        cevap = cevap.get("oneriler")
    return cevap if isinstance(cevap, list) else None


def _metin_al(s):
    m = s.get("text")
    if not isinstance(m, str) or not m:
        m = s.get("raw")
    return m if isinstance(m, str) else ""


def _parcala(kayitlar):
    parca, boy = [], 0
    for sid, metin in kayitlar:
        if parca and boy + len(metin) > PARCA_KARAKTER:
            yield parca
            parca, boy = [], 0
        parca.append((sid, metin))
        boy += len(metin) + 8
    if parca:
        yield parca


# ---------- ana islev ----------

def duzelt(satirlar, adaylar, adlar, sor_fn=None, varyantlar=None):
    """Dondurur (yeni_satirlar, rapor). yeni_satirlar girdinin kopyasi: 'text' duzeltilmis metin,
    'duzeltmeler' kabul edilenler, 'belirsiz' isaretli kelimeler. Model hatasinda metin degismez.
    rapor = {"satirlar": {id: {"kabul": [...], "red": [...], "belirsiz": [...]}}, "gecersiz": [...],
             "oneri", "kabul", "red", "belirsiz" (sayilar), "hata", "uyari" (yaygin liste yoksa metin)}
    varyantlar: [(eski, terim)] ogrenilmis / elle varyantlar (terim.varyant_ciftleri): kapida izinli esleme."""
    sor_fn = sor_fn or _llm_sor
    varyantlar = [(v, t) for v, t in (varyantlar or ()) if isinstance(v, str) and isinstance(t, str)]
    adaylar = list(adaylar or []) + [t for t in dict.fromkeys(t for _v, t in varyantlar) if t not in (adaylar or [])]
    adaylar = [a for a in (adaylar or []) if isinstance(a, str) and a.strip()]
    adlar = [a for a in (adlar or []) if isinstance(a, str) and a.strip()]
    kayitlar, metin = [], {}
    for s in satirlar or []:
        sid = str(s.get("id"))
        metin[sid] = _metin_al(s)
        if metin[sid].strip():
            kayitlar.append((sid, metin[sid]))
    rapor = {"satirlar": {sid: {"kabul": [], "red": [], "belirsiz": []} for sid in metin},
             "gecersiz": [], "oneri": 0, "kabul": 0, "red": 0, "belirsiz": 0, "hata": None,
             "uyari": None if kelime.liste_var_mi() else kelime.LISTE_YOK_UYARI}
    kabul_aralik = {sid: [] for sid in metin}          # (bas, bit, yeni) — ozgun satir konumlari
    if kayitlar and (adaylar or adlar or varyantlar):
        id_tamsayi = all(re.fullmatch(r"-?\d+", sid) for sid, _ in kayitlar)
        sema = oneri_semasi(id_tamsayi)
        for parca in _parcala(kayitlar):
            try:
                oneriler = _oneriler(sor_fn(mesajlar(parca, adaylar, adlar, varyantlar), sema))
            except Exception as e:                  # LLM hatasi: metin degismez
                rapor["hata"] = llm.hata_metni(e)
                continue
            if oneriler is None:
                rapor["hata"] = rapor["hata"] or "gecersiz_cevap"
                continue
            parca_idleri = {sid for sid, _ in parca}
            for sira, o in enumerate(oneriler):
                rapor["oneri"] += 1
                _isle(o, sira, parca_idleri, metin, adaylar, adlar, rapor, kabul_aralik, varyantlar)
    return _uygula(satirlar or [], metin, kabul_aralik, rapor), rapor


def _isle(o, sira, parca_idleri, metin, adaylar, adlar, rapor, kabul_aralik, varyantlar=None):
    sid = str(o.get("satir")) if isinstance(o, dict) else None
    if sid not in parca_idleri:
        rapor["gecersiz"].append({"oneri": o, "sebep": "satir_yok" if isinstance(o, dict) else "sema_disi"})
        rapor["red"] += 1
        return
    kayit = rapor["satirlar"][sid]
    oz = {k: (o.get(k).strip() if isinstance(o.get(k), str) else o.get(k)) for k in ("eski", "yeni", "neden")}
    if sira >= EN_FAZLA_ONERI:
        kabul, neden = False, "fazla_oneri"
    else:
        kabul, neden = (kapi(metin[sid], o, adaylar, adlar, varyantlar) if varyantlar
                        else kapi(metin[sid], o, adaylar, adlar))     # varyantsiz: eski imza (e2e gevsek-kapi taklidi)
    eski = oz["eski"] if isinstance(oz["eski"], str) else ""
    if kabul:
        bas = gecisler(metin[sid], eski)[0]
        bit = bas + len(eski)
        aralik = kabul_aralik[sid]
        if any(bas < b2 and b1 < bit for b1, b2, _ in aralik):
            kabul, neden = False, "cakisma"
        elif sum(len(metin[sid][b1:b2].split()) for b1, b2, _ in aralik) + len(eski.split()) \
                > satir_payi(metin[sid]):
            kabul, neden = False, "satir_orani"
        else:
            aralik.append((bas, bit, oz["yeni"]))
            kayit["kabul"].append(oz)
            rapor["kabul"] += 1
            return
    kayit["red"].append(dict(oz, sebep=neden))
    rapor["red"] += 1
    # 'yazim' ve hangi gecisin kastedildigi belli olmayan oneri: kelime belirsiz isaretlenir, metin degismez
    if neden in ("yazim_uygulanmaz", "coklu_gecis", "turkce_kelime") and eski and gecisler(metin[sid], eski) \
            and eski not in kayit["belirsiz"]:
        kayit["belirsiz"].append(eski)
        rapor["belirsiz"] += 1


def _uygula(satirlar, metin, kabul_aralik, rapor):
    cikti = []
    for s in satirlar:
        y = dict(s)
        sid = str(s.get("id"))
        m = metin.get(sid, "")
        for bas, bit, yeni in sorted(kabul_aralik.get(sid, []), reverse=True):
            m = m[:bas] + yeni + m[bit:]               # satirin geri kalani bayt bayt korunur
        kayit = rapor["satirlar"].get(sid, {"kabul": [], "belirsiz": []})
        y["text"] = m
        if kayit["kabul"]:
            y["duzeltmeler"] = list(s.get("duzeltmeler") or []) + [dict(k) for k in kayit["kabul"]]
        if kayit["belirsiz"]:
            onceki = list(s.get("belirsiz") or [])
            y["belirsiz"] = onceki + [b for b in kayit["belirsiz"] if b not in onceki]
        cikti.append(y)
    return cikti
