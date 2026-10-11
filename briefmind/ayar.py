"""
ayar.py — config.json okuma/yazma (arayuz ve komut satiri ortak).

  Okuma:  utf-8-sig (Not Defteri'nin BOM'u sorun olmaz); JSON nesnesi degilse AYAR_HATASI + varsayilanlar.
  Sema:   SEMA'daki anahtarlarin tipi denetlenir; yanlis tipli deger varsayilana doner ve BIR KEZ uyari uretilir
          (AYAR_UYARILARI; arayuz acilista Olaylar'a yazar). Bilinmeyen anahtarlar oldugu gibi korunur.
  Goc:    eski anahtarlar (kaynak 'ikisi'/'altyazi' -> 'ses' + altyazi_yedek; sozluk/inceleme anahtarlari) dosyada
          da duzeltilir (ilk goc oncesi config.json.yedek-goc yedegi).
  Yazma:  gecici dosya + os.replace (kesinti/cokme yarim config.json birakmaz).
  hata.log: %LOCALAPPDATA%\\BriefMind\\hata.log; yazilamazsa uygulama klasoru (calisma klasoru).
"""
import json
import os
import time

AYAR_DOSYA = "config.json"
AYAR_HATASI = None                         # config.json okunamadiysa neden (acilista kullaniciya gosterilir)
AYAR_UYARILARI = []                        # bir kez gosterilecek tip/goc uyarilari (ayar_uyarilari_al)
_UYARILANLAR = set()
LOG_BOYUT_TAVAN = 1000000                  # hata.log bu boyutu asarsa hata.log.1'e dondurulur

# anahtar -> (tur, alt, ust); tur: str | bool | int | sayi_veya_yok | dict | cihaz. alt/ust: sinirlar (yoksa None)
SEMA = {
    "route": ("str", None, None), "model": ("str", None, None), "context": ("int", 0, 1048576),
    "otobitir": ("int", 30, 1800), "duzelt": ("bool", None, None), "outlook": ("bool", None, None),
    "otomatik_basla": ("bool", None, None), "kaynak": ("str", None, None),
    "altyazi_yedek": ("bool", None, None), "altyazi_goster": ("bool", None, None),
    "kesinti_kaydi": ("bool", None, None), "kesinti_devam": ("bool", None, None),
    "stt_url": ("str", None, None), "stt_model": ("str", None, None), "stt_key": ("str", None, None),
    "stt_proxy": ("bool", None, None), "mikrofon_modu": ("str", None, None), "mikrofon_cihaz": ("cihaz", None, None),
    "ben": ("str", None, None), "altyazi_otomatik": ("bool", None, None), "altyazi_turkce": ("bool", None, None),
    "olcum_kaydi": ("bool", None, None), "stt_ipucu": ("bool", None, None),
    "stt_repetition_penalty": ("sayi_veya_yok", 0.1, 3.0), "ca_bundle": ("str", None, None),
    "not_sablonu": ("str", None, None), "saklama_gun": ("int", 0, 3650), "esikler": ("dict", None, None),
    "llm_akis": ("bool", None, None), "llm_proxy": ("bool", None, None), "llm_key": ("str", None, None),
    "api_key": ("str", None, None), "llm_zaman_asimi": ("int", 0, 86400), "llm_tekrar": ("int", 0, 20),
    "parca_token": ("int", 0, 1048576), "paralel": ("int", 0, 64),
    "embed_url": ("str", None, None), "embed_key": ("str", None, None), "embed_boyut": ("int", 0, 65536),
    "embed_proxy": ("bool", None, None), "embed_talimat": ("bool", None, None),
    "bilgilendirme_metni": ("str", None, None),
}
_DOGRU = {"true", "evet", "1", "acik", "açık", "yes", "on"}
_YANLIS = {"false", "hayir", "hayır", "0", "kapali", "kapalı", "no", "off"}
# kaldirilan ozellik anahtarlari (sozluk / inceleme akisi): okununca atilir, dosyadan da silinir
KALDIRILAN_ANAHTARLAR = ("otomatik_not", "inceleme", "inceleme_yok")


def _kaldirildi_mi(anahtar):
    return anahtar in KALDIRILAN_ANAHTARLAR or str(anahtar).lower().startswith("sozluk")


def varsayilanlar():
    import llm                             # gec: llm de bu modulu (tls_dogrulama) ice aktarir
    # model: config'teki deger (bos: sunucudaki tek model); context: 0 = sunucunun max_model_len'i
    return {"route": llm.ROUTE, "model": llm.CONFIG_MODEL, "context": 0, "otobitir": 180, "duzelt": True,
            "outlook": True,
            "otomatik_basla": False,
            "kaynak": "ses",                  # yalniz ses (Faz1: 'altyazi'/'ikisi' kaldirildi)
            "altyazi_yedek": False,           # opt-in: STT yokken altyazi metni yedek olarak (supheli, dusuk guven)
            "altyazi_goster": False,          # canli ekranda Teams altyazisini da goster (transkripte girmez)
            "kesinti_kaydi": False,           # STT yokken klipleri diske yaz, STT donunce coz
            "kesinti_devam": True,            # onceki oturumdan diskte kalan kesinti kuyrugunu acilista coz
            "stt_url": "",                        # config.json: STT servisi (OpenAI uyumlu /v1/audio/transcriptions)
            "stt_model": "whisper-large-v3-turbo-prod",     # ARGE GPU servisi (Turkce fine-tune)
            "stt_key": "EMPTY",                              # kendi CPU servisimiz icin: model "whisper", anahtar bos
            "mikrofon_modu": "otomatik",      # Faz1: kulaklik/hoparlor pasif tespit; 'cift'/'tek' goc eder
            "ben": "",
            "altyazi_otomatik": True,         # altyazi gorunmezse Teams'te acmayi dene
            "altyazi_turkce": True,           # altyazi bulununca konusulan dili Turkce yapmayi dene
            "olcum_kaydi": False,             # Faz 0 PoC: klip + altyazi okumalarini toplanti klasorune yaz
            "stt_ipucu": True,                # Whisper prompt'u: katilimci adlari + serinin <=20 terimi (dusuk RMS klibe gitmez)
            "stt_repetition_penalty": None,   # STT servisine repetition_penalty (None: gonderilmez; A/B icin 1.1 denenir)
            "ca_bundle": ""}                  # kurum kok sertifikasi (.cer, Base64); bos: TLS dogrulamasi kapali


# ---------------------------------------------------------------- okuma

def ham_oku(yol=None):
    """config.json -> dict (utf-8-sig: BOM'lu dosya da okunur). Dosya yok: {}. Bozuksa/nesne degilse istisna.
    llm.py / rag.py gibi dogrudan okuyan modullerin de kullanmasi icin."""
    yol = yol or AYAR_DOSYA
    if not os.path.exists(yol):
        return {}
    with open(yol, encoding="utf-8-sig") as f:
        veri = json.load(f)
    if not isinstance(veri, dict):
        raise ValueError(f"config.json bir JSON nesnesi olmalı ({{...}}), {type(veri).__name__} bulundu")
    return veri


def _uyar(mesaj):
    """Ayni uyari surec boyunca bir kez."""
    if mesaj not in _UYARILANLAR:
        _UYARILANLAR.add(mesaj)
        AYAR_UYARILARI.append(mesaj)


def uyarilari_sifirla():
    """Test: bir-kez bellegini ve bekleyen uyarilari temizler."""
    _UYARILANLAR.clear()
    del AYAR_UYARILARI[:]


def ayar_uyarilari_al():
    """Bekleyen uyarilari dondurur ve bosaltir (arayuz Olaylar'a yazar)."""
    liste = list(AYAR_UYARILARI)
    del AYAR_UYARILARI[:]
    return liste


def _tur_adi(deger):
    return {"str": "metin", "bool": "doğru/yanlış", "int": "tam sayı", "float": "sayı", "dict": "nesne",
            "list": "liste", "NoneType": "boş"}.get(type(deger).__name__, type(deger).__name__)


def _tip_duzelt(deger, sema):
    """-> (yeni_deger, aciklama|None). Uyumsuzsa ValueError. Belirsiz olmayan donusumler (\"true\" -> true,
    \"180\" -> 180, 180.0 -> 180) kabul edilir ve aciklama doner; sinir disi sayilar sinira cekilir."""
    tur, alt, ust = sema
    if tur == "str":
        if not isinstance(deger, str):
            raise ValueError("metin bekleniyordu")
        return deger, None
    if tur == "bool":
        if isinstance(deger, bool):
            return deger, None
        if isinstance(deger, str) and deger.strip().lower() in (_DOGRU | _YANLIS):
            dogru = deger.strip().lower() in _DOGRU
            return dogru, f"'{deger}' metni {str(dogru).lower()} olarak okundu"
        if isinstance(deger, int) and deger in (0, 1):
            return bool(deger), f"{deger} sayısı {str(bool(deger)).lower()} olarak okundu"
        raise ValueError("doğru/yanlış (true/false) bekleniyordu")
    if tur == "int":
        aciklama = None
        if isinstance(deger, bool):
            raise ValueError("tam sayı bekleniyordu")
        if isinstance(deger, float) and deger == int(deger):
            deger, aciklama = int(deger), "ondalık sayı tam sayıya çevrildi"
        elif isinstance(deger, str) and deger.strip().lstrip("-").isdigit():
            deger, aciklama = int(deger.strip()), "metin sayıya çevrildi"
        elif not isinstance(deger, int):
            raise ValueError("tam sayı bekleniyordu")
        sinirli = min(max(deger, alt if alt is not None else deger), ust if ust is not None else deger)
        if sinirli != deger:
            return sinirli, f"{deger} sınır dışı ({alt}..{ust}); {sinirli} kullanıldı"
        return deger, aciklama
    if tur == "sayi_veya_yok":
        if deger is None or deger == "":
            return None, None
        if isinstance(deger, bool) or not isinstance(deger, (int, float)):
            raise ValueError("sayı ya da boş bekleniyordu")
        if not (alt <= deger <= ust):
            raise ValueError(f"{alt}..{ust} aralığı bekleniyordu")
        return deger, None
    if tur == "dict":
        if not isinstance(deger, dict):
            raise ValueError("nesne ({...}) bekleniyordu")
        return deger, None
    if tur == "cihaz":
        if deger is None or (isinstance(deger, (str, int)) and not isinstance(deger, bool)):
            return deger, None
        raise ValueError("cihaz numarası, adı ya da boş bekleniyordu")
    return deger, None


def _goc_et(ham):
    """Eski anahtarlari yeni bicime tasir. -> (yeni_ham, notlar[]). ham degismez."""
    yeni, notlar = dict(ham), []
    kaynak = yeni.get("kaynak")
    if isinstance(kaynak, str) and kaynak.strip().lower() in ("ikisi", "altyazi", "altyazı"):
        yeni["kaynak"] = "ses"
        if "altyazi_yedek" not in yeni:               # kullanici acikca yazdiysa onun degeri korunur
            yeni["altyazi_yedek"] = True
        notlar.append(f"kaynak '{kaynak}' artık yok: 'ses' yapıldı, Teams altyazısı yalnız STT yokken yedek olarak "
                      f"girer (altyazi_yedek: {str(yeni['altyazi_yedek']).lower()})")
    mod = yeni.get("mikrofon_modu")
    if isinstance(mod, str) and mod not in ("otomatik", ""):
        notlar.append(f"mikrofon_modu '{mod}' artık yok: kulaklık/hoparlör otomatik algılanıyor")
        yeni["mikrofon_modu"] = "otomatik"
    for k in [k for k in yeni if _kaldirildi_mi(k)]:
        del yeni[k]
        notlar.append(f"'{k}' kaldırıldı (sözlük/inceleme akışı Faz 2'de kalktı)")
    return yeni, notlar


def _dogrula(ham):
    """ham (dosyadan) -> (sonuc, uyarilar[]): tip denetimi; hatali deger varsayilana doner (varsayilani olmayan
    anahtar atilir: tuketici kendi varsayilanini kullanir)."""
    varsayilan = varsayilanlar()
    sonuc, uyarilar = dict(varsayilan), []
    for k, deger in ham.items():
        sema = SEMA.get(k)
        if sema is None:
            sonuc[k] = deger                          # bilinmeyen anahtar oldugu gibi korunur
            continue
        try:
            yeni, aciklama = _tip_duzelt(deger, sema)
        except ValueError as e:
            uyarilar.append(f"config.json '{k}': {e} (bulunan: {_tur_adi(deger)} {str(deger)[:40]!r}); "
                            + (f"varsayılan kullanıldı ({varsayilan[k]!r})" if k in varsayilan
                               else "yok sayıldı, uygulamanın kendi varsayılanı kullanılacak"))
            continue
        sonuc[k] = yeni
        if aciklama:
            uyarilar.append(f"config.json '{k}': {aciklama}")
    return sonuc, uyarilar


def ayar_oku(goc_yaz=True):
    """config.json -> ayar sozlugu (varsayilanlar + dosya). Okunamayan/bozuk dosya: varsayilanlar + AYAR_HATASI.
    goc_yaz: eski anahtarlar bulunduysa dosya duzeltilmis halde (atomik, once yedek) yeniden yazilir."""
    global AYAR_HATASI
    try:
        ham = ham_oku()
        AYAR_HATASI = None
    except Exception as e:
        # Eskiden sessizce varsayilanlara dusuluyordu: stt_url bos kalip "STT'ye ulasilamiyor" gorunuyordu
        AYAR_HATASI = f"{e}"
        return varsayilanlar()
    ham, notlar = _goc_et(ham)
    if notlar:
        for n in notlar:
            _uyar(f"config.json göçü: {n}")
        if goc_yaz:
            _goc_dosyaya_yaz(ham)
    sonuc, uyarilar = _dogrula(ham)
    for u in uyarilar:
        _uyar(u)
    return sonuc


def _goc_dosyaya_yaz(ham):
    """Goc sonrasi ham sozlugu (varsayilanlar EKLENMEDEN) yazar; ilk seferde eski dosya yedeklenir. Hata sessiz."""
    try:
        yedek = AYAR_DOSYA + ".yedek-goc"
        if not os.path.exists(yedek):
            with open(AYAR_DOSYA, "rb") as f, open(yedek, "wb") as g:
                g.write(f.read())
        ayar_yaz(ham)
    except Exception as e:
        _uyar(f"config.json göçü dosyaya yazılamadı: {e!r}")


# ---------------------------------------------------------------- yazma

def atomik_yaz(yol, metin, yeniden=5):
    """Gecici dosya (ayni klasor) + os.replace: yazma yarida kesilse eski dosya bozulmaz. Windows'ta hedef bir antivirus/
    baska surec tarafindan acikken PermissionError olabilir: kisa araliklarla yeniden denenir."""
    klasor = os.path.dirname(os.path.abspath(yol))
    gecici = os.path.join(klasor, f".{os.path.basename(yol)}.tmp-{os.getpid()}")
    try:
        with open(gecici, "w", encoding="utf-8") as f:
            f.write(metin)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        for deneme in range(yeniden):
            try:
                os.replace(gecici, yol)
                return
            except PermissionError:
                if deneme == yeniden - 1:
                    raise
                time.sleep(0.1 * (deneme + 1))
    finally:
        if os.path.exists(gecici):
            try:
                os.remove(gecici)
            except OSError:
                pass


def ayar_yaz(v):
    atomik_yaz(AYAR_DOSYA, json.dumps(v, ensure_ascii=False, indent=1))


def tls_dogrulama(ayar=None):
    """LLM/STT isteklerinde TLS dogrulamasi (httpx/requests `verify=`): config.json -> ca_bundle dosyasi
    varsa onun yolu, yoksa False (dogrulama kapali; eski davranis). ayar verilmezse config.json okunur.
    Kok sertifika Edge'den disa aktarilir (README: 'TLS dogrulamasi')."""
    if ayar is None:
        try:
            ayar = ham_oku()
        except Exception:
            ayar = {}
    yol = ayar.get("ca_bundle") if isinstance(ayar, dict) else None
    yol = (yol or "").strip() if isinstance(yol, str) else ""
    if yol and os.path.isfile(yol):
        return yol
    return False


# ---------------------------------------------------------------- hata.log

def hata_log_yolu():
    """%LOCALAPPDATA%\\BriefMind\\hata.log (klasor olusturulur); LOCALAPPDATA yok/yazilamazsa calisma klasoru
    (uygulama basinda uygulama klasorune gecilir) 'hata.log'."""
    kok = os.environ.get("LOCALAPPDATA")
    if kok:
        try:
            klasor = os.path.join(kok, "BriefMind")
            os.makedirs(klasor, exist_ok=True)
            return os.path.join(klasor, "hata.log")
        except OSError:
            pass
    return "hata.log"


def hata_yaz(metin):
    """Metni hata.log'a ekler (boyut tavani asilirsa hata.log.1'e doner); ana yol yazilamazsa uygulama klasorune
    duser. ASLA istisna firlatmaz. -> yazilan yol | None."""
    yollar = [hata_log_yolu()]
    if yollar[0] != "hata.log":
        yollar.append("hata.log")
    for yol in yollar:
        try:
            try:
                if os.path.getsize(yol) > LOG_BOYUT_TAVAN:
                    os.replace(yol, yol + ".1")
            except OSError:
                pass
            with open(yol, "a", encoding="utf-8") as f:
                f.write(metin)
            return yol
        except Exception:
            continue
    return None
