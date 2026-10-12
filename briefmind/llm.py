"""
llm.py — Qwen (vLLM, OpenAI uyumlu): altyazi duzeltme, bolum ozeti, birlestirme.

Birlestirme (tam not) tek uzun Markdown cevabi istemez: once karar/aksiyon/acik soru listeleri
(kucuk JSON), sonra ozet paragrafi + sonraki adim (kucuk JSON) alinir, Markdown kodda kurulur.
Boylece cevap max_tokens'a takilip yarim kalmaz, basliklar hep Turkce olur; Ingilizce ya da
kesik cevapta LLM'siz yedege (bolum notlarinin kendisi) dusulur.
"""
import datetime as dt
import difflib
import json
import re
import ssl
import threading
import time

import httpx
import openai
from openai import BadRequestError, OpenAI, UnprocessableEntityError

import ayar as ayar_mod
from metin import ad_geciyor, cift_yonlu_benzerlik, tr_kucuk

CONFIG = "config.json"


def _ilk_ayar():
    """Acilis ayari: ayar.ham_oku ile (utf-8-sig: Not Defteri'nin BOM'lu config.json'u da okunur). Bozuksa {}."""
    try:
        return ayar_mod.ham_oku(CONFIG)
    except Exception:
        return {}


_cfg = _ilk_ayar()

MARJ = 350                                             # sablon + guvenlik payi
VARSAYILAN_PENCERE = 16384                             # sunucu sorulamazsa ve config'te 'context' yoksa
ROUTE = MODEL = client = _http = None
TOKENIZE_URL = None                                    # vLLM /tokenize kokte durur: ROUTE'un sonundaki /v1 atilir
TLS_DOGRULAMA = False                                  # ca_bundle yolu ya da False (ayar.tls_dogrulama)
TLS_HATASI = None                                      # ca_bundle var ama sertifika okunamadi (acilista gosterilir)
BAGLAM_PENCERESI = VARSAYILAN_PENCERE                  # gecerli pencere: min(config 'context', sunucu max_model_len)
CONFIG_PENCERE = 0                                     # config.json 'context' (0/bos: sunucudan otomatik)
CONFIG_MODEL = ""                                      # config.json 'model' (bos: sunucudaki tek model)
ZAMAN_ASIMI = 300                                      # sohbet istegi okuma zaman asimi (sn)
VARSAYILAN_DUSUNME_BUTCESI = 1500                      # vLLM thinking_token_budget (config 'llm_dusunme_butcesi'; 0: gonderilmez)
DUSUNME_BUTCESI = VARSAYILAN_DUSUNME_BUTCESI
DUSUNME_ICERIK_PAYI = 0.4                              # max_tokens'in en az bu orani cevaba kalir (dusunme butcesi ust siniri 1 - pay)
AKIS = True                                            # stream=True: route'un bosta kalma zaman asimina takilmaz
BUTCE_AYAR = {}                                        # config.json 'parca_token' / 'paralel' gecersiz kilmalari
SUNUCU = None                                          # sunucu_bilgisi() sonucu (modeller, pencere, hata)
AYAR_UYARILARI = []                                    # ayarla + sunucu tanisi uyarilari (arayuz Olaylar'a yazar)
SON_HATA = None                                        # son LLM hatasinin anlasilir metni (arayuz durum satiri)
UYARI_FN = None                                        # motor ayarlar: calisirken cikan uyarilar Olaylar'a
# Ag davranisi (ayarla() bunlari degistirmez; testler kapatir)
OTOMATIK_TANI = True                                   # ilk LLM isteginden once sunucu bir kez sorulur
SUNUCU_TOKENIZER = True                                # token sayimi sunucunun /tokenize'iyla

_tani_kilit = threading.Lock()
_TANI_YAPILDI = False
_AYAR_NESIL = 0                                        # her ayarla() cagrisinda artar
_SEMA_DESTEGI = True                                   # json_schema reddedildiyse oturum boyunca gonderilmez
_BUTCE_DESTEGI = True                                  # thinking_token_budget 400 ile reddedildiyse oturum boyunca gonderilmez
_TANI_TEKRAR_SN = 60                                   # sunucuya ulasilamayan tani bu kadar sn sonra yeniden denenir
_TANI_TEKRAR_SONA = 0.0
_MESAJ_TOKENIZE = True                                 # /tokenize 'messages' bicimini destekliyor mu
_TOKENIZE_KAPALI_SONA = 0.0                            # /tokenize basarisizsa bu ana kadar tahmin kullanilir
_tokenize_bildirildi = False
_token_onbellek = {}


class LlmHatasi(RuntimeError):
    """LLM cagrisi basarisiz: mesaj Turkce ve tani icin gereken degerleri (model, pencere, HTTP durumu,
    sunucunun mesaji) tasir. motor/isler bunu oldugu gibi Olaylar'a ve hata penceresine yazar."""

    def __init__(self, mesaj, durum=None):
        super().__init__(mesaj)
        self.durum = durum

    def __repr__(self):
        return f"LlmHatasi: {self}"


def _int(x, varsayilan=0):
    try:
        return int(x if x not in (None, "") else varsayilan)
    except (TypeError, ValueError):
        return varsayilan


_UC_NOKTA = re.compile(r"/(?:chat/completions|completions|models|tokenize|detokenize|embeddings)$", re.I)


def route_normalize(route, ayrinti=False):
    """Bosluk ve sondaki '/' atilir; sona yapistirilmis uc nokta (/chat/completions, /completions, /models,
    /tokenize) kirpilir; yol '/v1' (ya da /vN) ile bitmiyorsa eklenir. openai istemcisi /chat/completions'i
    bunun ustune kurar: '/v1'siz adres 404, '/v1/' '/tokenize/' (307), tam uc nokta ise
    '.../v1/chat/completions/v1/chat/completions' (404) uretiyordu. ayrinti=True: (adres, kirpilan_uc_nokta)."""
    r = (route or "").strip().rstrip("/")
    kirpilan = ""
    while r:
        m = _UC_NOKTA.search(r)
        if not m:
            break
        kirpilan = m.group(0) + kirpilan
        r = r[:m.start()].rstrip("/")
    if r and not re.search(r"/v\d+$", r):
        r += "/v1"
    return (r, kirpilan) if ayrinti else r


def maskeli_adres(adres):
    """Olaylar/gunluk icin: sunucu adi gizlenir ('https://<sunucu>/v1')."""
    return re.sub(r"^(\w+://)[^/]+", r"\1<sunucu>", adres or "")


def _kok(route):
    return re.sub(r"/v\d+$", "", (route or "").rstrip("/"))


def ayarla(cfg):
    """LLM adresi/model/baglam penceresi ve istemciler calisirken yeniden kurulur (Ayarlar -> Kaydet
    sonrasi uygulamayi yeniden baslatmak gerekmez). cfg: config.json sozlugu. Ag istegi YAPMAZ: sunucu
    (model listesi, max_model_len) ilk LLM isteginden once ya da sunucuyu_tani() ile bir kez sorulur."""
    global ROUTE, MODEL, BAGLAM_PENCERESI, TLS_DOGRULAMA, TLS_HATASI, client, _http, TOKENIZE_URL
    global CONFIG_PENCERE, CONFIG_MODEL, ZAMAN_ASIMI, AKIS, BUTCE_AYAR, SUNUCU, AYAR_UYARILARI, SON_HATA
    global _TANI_YAPILDI, _SEMA_DESTEGI, _MESAJ_TOKENIZE, _TOKENIZE_KAPALI_SONA, _tokenize_bildirildi, _AYAR_NESIL
    global DUSUNME_BUTCESI, _BUTCE_DESTEGI, _TANI_TEKRAR_SONA
    cfg = cfg or {}
    _AYAR_NESIL += 1
    ham = str(cfg.get("route") or "http://localhost:8000/v1").strip()   # config.json: LLM adresi (OpenAI uyumlu)
    # OpenShift icinden: "http://<servis>.<namespace>.svc.cluster.local:8000/v1"
    ROUTE, kirpilan = route_normalize(ham, ayrinti=True)
    TOKENIZE_URL = _kok(ROUTE) + "/tokenize"
    AYAR_UYARILARI = []
    if kirpilan:
        AYAR_UYARILARI.append(f"adres düzeltildi: LLM adresinin sonundaki '{kirpilan}' uç noktası atıldı, "
                              f"kullanılan adres {maskeli_adres(ROUTE)} (Ayarlar → LLM adresi '.../v1' ile bitmeli; uç noktayı "
                              f"uygulama kendisi ekler)")
    elif ROUTE != ham:
        AYAR_UYARILARI.append("LLM adresi düzeltildi: sonu '/v1' olmalı (sondaki '/' atıldı ya da '/v1' eklendi)")
    CONFIG_MODEL = str(cfg.get("model") or "").strip()
    MODEL = CONFIG_MODEL                                # bos ise sunucudaki tek model secilir (sunucuyu_tani)
    CONFIG_PENCERE = max(0, _int(cfg.get("context")))  # 0/bos/bozuk: sunucunun max_model_len'i
    BAGLAM_PENCERESI = CONFIG_PENCERE or VARSAYILAN_PENCERE
    ZAMAN_ASIMI = max(30, _int(cfg.get("llm_zaman_asimi"), 300))
    AKIS = bool(cfg.get("llm_akis", True))
    DUSUNME_BUTCESI = max(0, _int(cfg.get("llm_dusunme_butcesi"), VARSAYILAN_DUSUNME_BUTCESI))
    BUTCE_AYAR = {ad: _int(cfg.get(k)) for ad, k in (("parca", "parca_token"), ("paralel", "paralel"))
                  if _int(cfg.get(k)) > 0}
    SUNUCU, SON_HATA = None, None
    _TANI_YAPILDI, _SEMA_DESTEGI, _MESAJ_TOKENIZE, _BUTCE_DESTEGI, _TANI_TEKRAR_SONA = False, True, True, True, 0.0
    _TOKENIZE_KAPALI_SONA, _tokenize_bildirildi = 0.0, False
    _token_onbellek.clear()
    # A8: config.json -> ca_bundle (kurum kok sertifikasi) varsa TLS dogrulanir, yoksa kapali
    TLS_DOGRULAMA, TLS_HATASI, dogrula = ayar_mod.tls_dogrulama(cfg), None, False
    if TLS_DOGRULAMA:
        try:
            dogrula = ssl.create_default_context(cafile=TLS_DOGRULAMA)
        except (ssl.SSLError, OSError, ValueError) as e:     # bozuk/yanlis bicimli .cer: uygulama acilsin
            TLS_DOGRULAMA, TLS_HATASI = False, f"{e}"
    anahtar = str(cfg.get("llm_key") or cfg.get("api_key") or "").strip()
    vekil = bool(cfg.get("llm_proxy", False))           # True: sistem proxy'si (HTTPS_PROXY) kullanilir
    # max_retries: SDK varsayilani 2 idi; route zaman asiminda (504) ayni uzun istek 3 kez uretiliyordu
    tekrar = max(0, _int(cfg.get("llm_tekrar"), 1))
    # eski istemciler kapatilmaz: o an baska bir is parcaciginda suren istek yarida kesilmesin
    client = OpenAI(base_url=ROUTE, api_key=anahtar or "x", max_retries=tekrar,
                    http_client=httpx.Client(verify=dogrula, trust_env=vekil,
                                             timeout=httpx.Timeout(ZAMAN_ASIMI, connect=15.0)))
    _http = httpx.Client(verify=dogrula, trust_env=vekil, timeout=httpx.Timeout(10.0, connect=5.0),
                         headers={"Authorization": f"Bearer {anahtar}"} if anahtar else None)


ayarla(_cfg)


def _uyar(m):
    if UYARI_FN:
        try:
            UYARI_FN(m)
        except Exception:
            pass


def sunucu_bilgisi(zaman_asimi=20):
    """GET {ROUTE}/models -> {"modeller": [{"id", "max_model_len"}], "hata": None | metin}. Hic yukseltmez."""
    try:
        r = _http.get(ROUTE + "/models", timeout=zaman_asimi)
    except Exception as e:
        return {"modeller": [], "hata": _istisna_ozeti(e)}
    if r.status_code != 200:
        return {"modeller": [], "hata": f"GET /models HTTP {r.status_code}: {_kisalt(r.text, 200)}"}
    try:
        veri = r.json().get("data") or []
    except Exception:
        return {"modeller": [], "hata": f"GET /models JSON değil: {_kisalt(r.text, 200)}"}
    return {"modeller": [{"id": m.get("id"), "max_model_len": m.get("max_model_len")} for m in veri
                         if isinstance(m, dict)], "hata": None}


def sunucuyu_tani(zorla=False):
    """Sunucudaki modelleri ve baglam penceresini bir kez sorar, ayarlari ona gore duzeltir:
    - config modeli listede yoksa ve sunucu tek model sunuyorsa o model kullanilir;
    - pencere = sunucunun max_model_len'i; config 'context' daha kucukse o (yalniz ust sinir).
    Sunucuya ulasilamazsa config degerleri kalir. Dondurur: uyari satirlari (ayarla uyarilari dahil)."""
    global _TANI_YAPILDI, _TANI_TEKRAR_SONA
    with _tani_kilit:
        if _TANI_YAPILDI and not zorla:
            # sunucuya ulasilamayan tani kalici sayilmaz: ag gecici koptuysa model/pencere _TANI_TEKRAR_SN sonra
            # yeniden sorulur (eskiden oturum boyunca bos/eski model adi ve varsayilan pencere kaliyordu)
            if not (SUNUCU and SUNUCU.get("hata") and time.time() >= _TANI_TEKRAR_SONA):
                return list(AYAR_UYARILARI)
        nesil = _AYAR_NESIL
        try:
            return _tani(zorla)
        finally:
            # bayrak sorgu BITINCE kalkar: eskiden GET /models surerken True oluyor, kilitsiz okuyan sor()
            # taniyi atlayip bos/eski model adiyla 404 aliyordu. Bu arada ayarla() cagrildiysa (yeni adres)
            # tani yeni ayarla yeniden yapilsin.
            if nesil == _AYAR_NESIL:
                _TANI_YAPILDI = True
                _TANI_TEKRAR_SONA = time.time() + _TANI_TEKRAR_SN


def _tani(zorla):
    """sunucuyu_tani'nin govdesi (_tani_kilit altinda cagrilir)."""
    global SUNUCU, MODEL, BAGLAM_PENCERESI
    bilgi = sunucu_bilgisi()
    uyarilar = []
    sunucu_pencere = None
    if bilgi["hata"]:
        uyarilar.append(f"LLM sunucusu sorgulanamadı ({bilgi['hata']}); model '{MODEL or '-'}', bağlam "
                        f"penceresi {BAGLAM_PENCERESI} varsayıldı")
    else:
        idler = [m["id"] for m in bilgi["modeller"] if m.get("id")]
        if MODEL not in idler:
            if len(idler) == 1:
                eski, MODEL = MODEL, idler[0]
                uyarilar.append(f"config.json 'model' ({eski}) sunucuda yok; sunucudaki tek model kullanılıyor: "
                                f"{MODEL}" if eski else f"model sunucudan alındı: {MODEL}")
            elif idler:
                uyarilar.append(f"config.json 'model' ({MODEL or 'boş'}) sunucuda yok; sunucudaki modeller: "
                                f"{', '.join(idler)} — Ayarlar → Model alanına birini yazın")
            else:
                uyarilar.append("LLM sunucusu hiç model listelemedi (GET /models boş)")
        sunucu_pencere = next((_int(m.get("max_model_len")) for m in bilgi["modeller"]
                               if m.get("id") == MODEL and _int(m.get("max_model_len")) > 0), None)
        if sunucu_pencere:
            if CONFIG_PENCERE > sunucu_pencere:
                uyarilar.append(f"config.json 'context' ({CONFIG_PENCERE}) sunucunun bağlam penceresinden "
                                f"({sunucu_pencere}) büyük; {sunucu_pencere} kullanılıyor")
            BAGLAM_PENCERESI = min(CONFIG_PENCERE, sunucu_pencere) if CONFIG_PENCERE else sunucu_pencere
    bilgi["pencere"] = sunucu_pencere
    SUNUCU = bilgi
    yeni = [u for u in uyarilar if u not in AYAR_UYARILARI]
    AYAR_UYARILARI.extend(yeni)
    _gunluge_yaz(olay="ayar", zaman=dt.datetime.now().strftime("%H:%M:%S"), model=MODEL,
                 pencere=BAGLAM_PENCERESI, sunucu_pencere=sunucu_pencere, config_context=CONFIG_PENCERE,
                 akis=AKIS, uyari=list(AYAR_UYARILARI))
    if not zorla:                         # acik cagiran (arayuz, teshis) donen listeyi kendisi yazar
        for u in yeni:
            _uyar(u)
    return list(AYAR_UYARILARI)


def ayar_ozeti():
    """Arayuz/teshis icin tek satir: gecerli model, pencere ve kaynagi."""
    kaynak = ("sunucu" if SUNUCU and SUNUCU.get("pencere") and BAGLAM_PENCERESI == SUNUCU["pencere"]
              else "config" if CONFIG_PENCERE else "varsayılan")
    return (f"LLM modeli: {MODEL or '-'} · bağlam penceresi: {BAGLAM_PENCERESI} ({kaynak}) · "
            f"akış: {'açık' if AKIS else 'kapalı'}")


# ---------- token butceleri ----------
# 100-200 token/sn'lik sunucuda max_tokens bir maliyet degil ust sinirdir; kesilen cevap (length) ise
# tekrar cagrisi demektir. Butceler pencereye gore uc kademede: <12k (8k), 12k-32k (16k), >=32k.
# Liste birlestirmede dusunme ACILMAZ: eski sunucuda dusunme aciksa json_schema uygulanmiyordu; yeni
# sunucuda tools/llm_teshis.py 'json_schema + dusunme' satiri bunu olcer, karar o olcume baglidir.
_BUTCELER = {
    #               8k     16k    32k+
    "duzeltme":     (1200, 1200, 1200),    # madde sayisi (<=25) sinirli: pencereyle buyumez
    "bolum":        (3000, 3000, 4000),    # bolum ozeti (dusunme low + JSON)
    "liste":        (2500, 6000, 8000),    # karar/aksiyon/acik soru birlestirmesi (dusunmesiz) ust siniri
    "genel":        (800, 1500, 2000),     # ozet paragrafi + sonraki adim (dusunmesiz)
    "soru":         (1500, 1500, 2500),    # toplantiya soru: cevap
    "soru_dusunme": (0, 1500, 1500),       # toplantiya soru: dusunme payi (8k'da dusunme kapali)
    "parca":        (2500, 3000, 4000),    # motor: canli parca boyutu (token)
    "paralel":      (2, 2, 2),             # motor: ayni anda islenen parca (config 'paralel' ile artirilabilir)
}
SORU_GIRDI_UST = 48000                     # buyuk pencerede bile soru basina en fazla bu kadar transkript


def butce(ad, pencere=None):
    """Cagri turune gore max_tokens (ya da motor icin parca boyutu/paralellik): pencereye olcekli."""
    if ad in BUTCE_AYAR:
        return BUTCE_AYAR[ad]
    p = pencere or BAGLAM_PENCERESI
    return _BUTCELER[ad][0 if p < 12000 else (1 if p < 32768 else 2)]


# ---------- hata metinleri ----------
_BAGLAM_IFADE = re.compile(r"maximum context length|context length|max_model_len|maximum model length|"
                           r"model length|too many tokens|prompt is too long|longer than the maximum", re.I)
_SEMA_IFADE = re.compile(r"json_schema|response_format|guided|structured output|grammar|xgrammar|outlines", re.I)
_BUTCE_IFADE = re.compile(r"thinking[_ ]token[_ ]budget|thinking[_ ]budget", re.I)


def _kisalt(s, n):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[:n] + "…"


def _govde(e):
    """openai APIStatusError'in sunucu mesaji (vLLM: {'message'} / {'error': {'message'}} / FastAPI {'detail'})."""
    b = getattr(e, "body", None)
    if isinstance(b, dict):
        ic = b.get("error") if isinstance(b.get("error"), dict) else b
        for k in ("message", "detail"):
            if ic.get(k):
                return str(ic[k])
        return json.dumps(b, ensure_ascii=False)
    if b:
        return str(b)
    yanit = getattr(e, "response", None)
    try:
        return yanit.text
    except Exception:
        return str(e)


def _istisna_ozeti(e):
    """'Tur: mesaj <- NedenTuru: mesaj': openai 'Connection error.' gercek nedeni (__cause__) gizliyordu."""
    parcalar, x = [], e
    while x is not None and len(parcalar) < 4:
        p = f"{type(x).__name__}: {_kisalt(x, 240)}"
        if p not in parcalar:                 # httpx/httpcore ayni mesaji iki kez sarar
            parcalar.append(p)
        x = x.__cause__ or (None if x.__suppress_context__ else x.__context__)
    return " ← ".join(parcalar)


def _baglam_siniri(govde):
    """vLLM 400 metninden sunucunun penceresi: "maximum context length is 8192 tokens"."""
    m = re.search(r"maximum (?:context|model) length (?:is|of) (\d+)", govde or "", re.I)
    return int(m.group(1)) if m else None


def _akis_hatasi(e):
    """Akisli cevabin ICINDE gelen hata olayi (vLLM 'data: {"error": ...}'): openai bunu HTTP durumu olmayan
    duz APIError olarak yukseltir; baglanti/zaman asimi hatalari degildir."""
    return (isinstance(e, openai.APIError) and getattr(e, "status_code", None) is None
            and not isinstance(e, openai.APIConnectionError))


def _hata_durumu(e):
    """HTTP durumu; akis ici hatada govdedeki 'code' (vLLM 400/422 tasir) ya da baglam metni varsa 400."""
    durum = getattr(e, "status_code", None)
    if durum or not _akis_hatasi(e):
        return durum
    b = getattr(e, "body", None)
    kod = _int(b.get("code")) if isinstance(b, dict) else 0
    if 100 <= kod < 600:
        return kod
    return 400 if _BAGLAM_IFADE.search(_govde(e) or "") else None


def hata_metni(e):
    """Her LLM hatasi icin tek satir Turkce, tani degerleriyle. LlmHatasi zaten hazir metindir."""
    if isinstance(e, LlmHatasi):
        return str(e)
    akis_ici = _akis_hatasi(e)
    durum = _hata_durumu(e)
    govde = _kisalt(_govde(e) if not akis_ici else (_govde(e) or str(e)), 600) if (durum or akis_ici) else ""
    if akis_ici:
        govde = "akış içi hata: " + govde
    html = "<html" in govde.lower() or "<!doctype" in govde.lower()
    if durum == 404:
        if "does not exist" in govde or "model" in govde.lower():
            modeller = ", ".join(m["id"] for m in (SUNUCU or {}).get("modeller", []) if m.get("id")) or "?"
            return (f"LLM sunucusu '{MODEL or '(boş)'}' modelini tanımıyor (HTTP 404; sunucudaki modeller: {modeller}). "
                    f"Ayarlar → Model alanını düzeltin ya da boş bırakın. Sunucu: {_kisalt(govde, 240)}")
        return (f"LLM adresi bulunamadı (HTTP 404): Ayarlar → LLM adresi '.../v1' ile bitmeli. "
                f"Sunucu: {_kisalt(govde, 200)}")
    if durum in (401, 403):
        return (f"LLM sunucusu isteği reddetti (HTTP {durum}): anahtar gerekiyorsa config.json 'llm_key'. "
                f"Sunucu: {_kisalt(govde, 200)}")
    if durum in (400, 422) and _BAGLAM_IFADE.search(govde):
        return (f"İstek LLM bağlam penceresini aşıyor (HTTP {durum}; uygulamanın kullandığı pencere "
                f"{BAGLAM_PENCERESI}). config.json 'context' değerini boş bırakın ya da sunucununkine eşitleyin. "
                f"Sunucu: {_kisalt(govde, 400)}")
    if durum in (502, 503, 504) or html:
        return (f"LLM ağ geçidi HTTP {durum} döndü{' (HTML gövde: OpenShift route/proxy)' if html else ''}. Uzun "
                f"isteklerde route zaman aşımı olabilir: config.json 'llm_akis': true (akışlı istek) ya da route'a "
                f"haproxy.router.openshift.io/timeout=600s. Gövde: {_kisalt(govde, 160)}")
    if durum:
        return f"LLM HTTP {durum}: {_kisalt(govde, 400)}"
    if akis_ici:
        return f"LLM cevabı akış sırasında hata ile kesildi: {_kisalt(govde, 400)}"
    if isinstance(e, openai.APITimeoutError):
        return (f"LLM {ZAMAN_ASIMI} sn içinde cevap vermedi (zaman aşımı; config.json 'llm_zaman_asimi'). "
                f"{_istisna_ozeti(e)}")
    if isinstance(e, openai.APIConnectionError):
        neden = _istisna_ozeti(e.__cause__) if e.__cause__ else _istisna_ozeti(e)
        ipucu = ""
        if re.search(r"ssl|certificate|sertifika", neden, re.I):
            ipucu = (" TLS: config.json 'ca_bundle' sertifikası bu sunucuyu doğrulamıyor olabilir."
                     if TLS_DOGRULAMA else " TLS el sıkışması başarısız.")
        elif re.search(r"proxy|getaddrinfo|name or service|nodename|resolve|timed out|refused", neden, re.I):
            ipucu = " Tarayıcıdan erişiliyorsa config.json 'llm_proxy': true deneyin (sistem proxy'si)."
        return f"LLM adresine ulaşılamadı: {neden}.{ipucu}"
    return _istisna_ozeti(e)

GUNLER = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]

BOLUM_SEMASI = {
    "type": "object",
    "properties": {
        "ozet":         {"type": "string"},
        "konular":      {"type": "array", "items": {"type": "string"}},
        "kararlar":     {"type": "array", "items": {"type": "string"}},
        "aksiyonlar":   {"type": "array", "items": {"type": "object", "properties": {
                            "madde": {"type": "string"}, "sorumlu": {"type": "string"},
                            "tarih": {"type": "string"}},
                         "required": ["madde", "sorumlu", "tarih"]}},
        "acik_sorular": {"type": "array", "items": {"type": "string"}},
        "belirsiz_terimler": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["ozet", "konular", "kararlar", "aksiyonlar", "acik_sorular", "belirsiz_terimler"],
}

# duzeltme isteginin sistem mesaji duzelt.DUZELT_SISTEM'dedir (llm duzelt'i ice aktarmaz); _gorev_adi bu onekle tanir
DUZELT_ONEK = "Aşağıdaki satırlar konuşmayı yazıya çeviren Whisper"

TURKCE_KURAL = ("ÇIKTI DİLİ: Bütün alanları yalnızca TÜRKÇE yaz. Terimleri, ürün ve kişi adlarını olduğu gibi "
                "bırak ama cümleleri asla İngilizce kurma.")

MAP_SISTEM = (
    "Sen deneyimli bir toplantı not alıcısısın. Sana bir toplantının BİR bölümünün transkripti verilecek "
    "(konuşmadan yazıya çevrilmiş; kısmen düzeltilmiş, hâlâ bozuk kelimeler olabilir).\n"
    "Görev: bu bölümden yalnızca gerçekten söylenenleri, aşağıdaki alanlarla JSON olarak çıkar.\n"
    "Satır etiketleri: 'Ad:' konuşmacı kesin; 'Ad?:' adı emin değil; 'Bilinmeyen:' kim olduğu bilinmiyor; "
    "'A → B:' ve 'A / B:' sözün el değiştirdiği ya da aynı anda konuşulan yer. ⟨...⟩ içindeki kelimeler eşzamanlı ya "
    "da belirsiz duyulan konuşmadır: ⟨⟩ içinden karar, aksiyon, sayı ya da tarih ÜRETME; oradan yalnızca 'şu konu "
    "tartışıldı' diyebilirsin (ozet ve konular). 'Ad?', 'Bilinmeyen', 'A → B' ve 'A / B' konuşmacıları ASLA aksiyon "
    "sahibi yapma: sorumlu, satırda adı açıkça geçen kişi değilse 'belirsiz'.\n"
    "- ozet: bölümde ne konuşulduğunu 2-4 cümleyle, kronolojik ve olgusal anlat (kim ne önerdi, ne "
    "tartışıldı, neye varıldı). Yorum yok, genelleme yok, transkriptte olmayan bilgi yok.\n"
    "- konular: ele alınan başlıklar (kısa, 2-6 kelime).\n"
    "- kararlar: AÇIKÇA karara bağlanmış şeyler. Öneri, niyet ya da 'bakarız' düzeyindeki ifadeler karar "
    "DEĞİLDİR; onlar 'acik_sorular'a gider. Kararı vereni yalnızca gerçekten tartışmalı ya da tek kişinin "
    "verdiği kararlarda, cümle içinde yaz ('Selin'in önerisiyle ...'); sonuna parantezle tek ad ekleme.\n"
    "- aksiyonlar: bir kişinin üstlendiği, BİTİNCE BELLİ OLAN somut işler; 'madde' alanı asla boş kalmaz. "
    "Sorumlusu belirsiz ve fiili genel ('anlamak', 'değerlendirmek', 'bakmak') olan ifadeler aksiyon değildir, "
    "'konular'a gider. Sorumlu katılımcı listesindeki yazımla, belli değilse 'belirsiz'; tarih toplantı "
    "tarihine göre gerçek tarihe çevrilir ('perşembeye' -> 'YYYY-MM-DD (Perşembe)'), belli değilse '-'.\n"
    "- acik_sorular: yalnızca bu bölümde cevapsız kalan ve bir sonraki adımı etkileyen sorular; en fazla 4. "
    "Şunları YAZMA: kendi anlama soruların (kim/ne olduğu belirsiz ad ve terimler 'belirsiz_terimler'e "
    "gider), transkriptten alıntı ya da parantez içi açıklama, aynı bölümde cevaplanan soru.\n"
    "- belirsiz_terimler: bozuk duyulduğunu düşündüğün kelime/terimler (transkriptteki yazımıyla).\n"
    "Kurallar: emin olamadığın yeri olduğu gibi aktar ya da 'belirsiz_terimler'e yaz; (?) işaretini SEN YAZMA, "
    "uygulama belirsiz maddelere kendisi koyar. Terim listesi ve gündem yalnızca yazım "
    "ipucudur; oradan içerik üretme. Bölümde bir şey yoksa ilgili liste boş kalır, ozet 1 cümle olur.\n"
    + TURKCE_KURAL + " Yalnızca JSON döndür."
)

LISTE_SEMASI = {
    "type": "object",
    "properties": {k: BOLUM_SEMASI["properties"][k] for k in ("kararlar", "aksiyonlar", "acik_sorular")},
    "required": ["kararlar", "aksiyonlar", "acik_sorular"],
}

GENEL_SEMASI = {
    "type": "object",
    "properties": {"ozet": {"type": "string"}, "sonraki_adim": {"type": "string"}},
    "required": ["ozet", "sonraki_adim"],
}

LISTE_SISTEM = (
    "Sen deneyimli bir toplantı not alıcısısın. Sana bir toplantının bölümlerinden çıkarılmış karar, aksiyon "
    "ve açık soru listeleri verilecek; her maddenin başında hangi bölümden geldiği [B3] gibi yazılı.\n"
    "Görev: listeleri TEK toplantı için birleştir.\n"
    "- Aynı ya da çok benzer maddeleri tek maddede birleştir; çelişkide sonraki bölüm geçerlidir.\n"
    "- Sonraki bir bölümde cevaplanan ya da karara bağlanan açık soruyu listeden çıkar.\n"
    "- Öneri ya da niyet düzeyindeki ifadeyi karara çevirme.\n"
    "- Birinin yapacağı somut iş ('... iletilecek', '... hazırlanacak', '... toplantı organize edecek') karar "
    "değil AKSİYONDUR: kararlar listesinden çıkar, aksiyonlara (sorumlusuyla) taşı. Aynı iş hem kararda hem "
    "aksiyonda yazılmasın.\n"
    "- Aynı işi farklı bölümlerde farklı kişiler üstlendiyse tek satırda birleştir, sorumluları virgülle yaz.\n"
    "- Aksiyonlarda sorumlu adını katılımcı listesindeki yazımla ver, belli değilse 'belirsiz'; tarih yoksa '-'. "
    "Aksiyonun 'madde' alanını asla boş bırakma.\n"
    "- Açık sorularda aynı konudaki soruları tek soruda birleştir; birleştirilmiş kararlar ya da aksiyonlarla "
    "cevaplanan soruyu çıkar; toplam en fazla 8 açık soru.\n"
    "- Sorumluyu yalnızca girdide yazılı adlardan al; 'belirsiz' ya da (?) işaretli bir aksiyona kendin kişi atama, "
    "'Ad?' / 'Bilinmeyen' diye anılan kimseyi sorumlu yapma.\n"
    "- Yeni madde EKLEME; terim listesi, gündem ya da genel bilgiden içerik üretme. İfadeleri kısaltabilirsin, anlamı "
    "değiştirme. [B..] etiketlerini çıktıya yazma. Girdideki (?) işaretlerini aynen koru; kendin yeni (?) ekleme "
    "(uygulama koyar).\n"
    + TURKCE_KURAL + " Yalnızca JSON döndür."
)

GENEL_SISTEM = (
    "Sen deneyimli bir toplantı not alıcısısın. Sana bir toplantının bölüm bölüm çıkarılmış özetleri ve "
    "birleştirilmiş kararları/aksiyonları verilecek.\n"
    "- ozet: bölüm özetlerini KRONOLOJİK sırayla bağlayıp toplantının akışını anlatan TEK paragraf (neyle "
    "başladı, ne tartışıldı, nereye varıldı). Bölüm özetlerinde olmayan bilgi ekleme; genel bilginle boşluk "
    "doldurma. Uzunluk içerikle orantılı: kısa toplantı 2-3 cümle, uzun toplantı en fazla 8 cümle.\n"
    "- sonraki_adim: 1-2 cümle, yalnızca verilen kararlardan ve aksiyonlardan türetilmiş; hiç karar ve "
    "aksiyon yoksa '-'. Sorumlusu 'belirsiz' olan işi birine atfetme; (?) işaretini kendin yazma.\n"
    + TURKCE_KURAL + " Yalnızca JSON döndür."
)

# ---------- not sablonlari: sablona ozel bolumler (map + liste + not) ----------
# Sablon toplantiya baglidir (meta.json 'sablon'); ayar 'otomatik' ise davet konusu / metninden secilir (sablon_sec).
# Ek alanlar yalniz o sablonun semasina ve prompt'una girer: 'genel' istekleri (prompt + sema) eskisiyle birebir ayni.
# Ek bolumlerin maddeleri de dayanak / deger / sahip denetiminden gecer (motor); bos bolum notta yer almaz.

SABLON_OTOMATIK = "otomatik"
_METIN_LISTESI = {"type": "array", "items": {"type": "string"}}
# sablon -> ((alan, not basligi, tur), ...); tur: metin (dizi) | secenek (nesne: secenek, artilar, eksiler, secildi) |
# fikir (nesne: fikir, tema)
SABLON_ALANLARI = {
    "haftalik": (("ilerleme", "İlerleme", "metin"), ("engeller", "Engeller", "metin"),
                 ("gelecek_donem", "Gelecek dönem", "metin")),
    "karar": (("secenekler", "Seçenekler ve gerekçeler", "secenek"),),
    "birebir": (("geri_bildirimler", "Geri bildirimler", "metin"), ("anlasilanlar", "Anlaşılanlar", "metin")),
    "calistay": (("fikirler", "Fikirler", "fikir"),),
}
ALAN_TURU = {alan: tur for v in SABLON_ALANLARI.values() for alan, _, tur in v}
SABLON_ALAN_ADLARI = tuple(ALAN_TURU)
_SABLON_SEMA = {
    "metin": _METIN_LISTESI,
    "secenek": {"type": "array", "items": {"type": "object", "properties": {
        "secenek": {"type": "string"}, "artilar": _METIN_LISTESI, "eksiler": _METIN_LISTESI,
        "secildi": {"type": "boolean"}}, "required": ["secenek", "artilar", "eksiler", "secildi"]}},
    "fikir": {"type": "array", "items": {"type": "object", "properties": {
        "fikir": {"type": "string"}, "tema": {"type": "string"}}, "required": ["fikir", "tema"]}},
}
_MAP_EK = {
    "haftalik": ("Bu bir HAFTALIK DURUM toplantısı; ek alanlar:\n"
                 "- ilerleme: tamamlanan ya da ilerleyen işler (kim neyi bitirdi, iş ne durumda).\n"
                 "- engeller: işi durduran ya da geciktiren sorunlar ve bağımlılıklar.\n"
                 "- gelecek_donem: bir sonraki döneme planlanan işler (sahibi belli somut iş 'aksiyonlar'a da yazılır)."),
    "karar": ("Bu bir KARAR toplantısı; ek alan:\n"
              "- secenekler: karşılaştırılan her seçenek için 'secenek' (kısa ad), 'artilar' ve 'eksiler' (yalnız "
              "toplantıda SÖYLENEN gerekçeler; kendin gerekçe ekleme), 'secildi' (yalnız açıkça seçildiyse true; seçilen "
              "seçenek 'kararlar'a da yazılır)."),
    "birebir": ("Bu bir BİREBİR (1:1) görüşme; ek alanlar:\n"
                "- geri_bildirimler: verilen ya da istenen geri bildirimler (kim kime, olgusal; yorum katma).\n"
                "- anlasilanlar: iki tarafın açıkça üzerinde anlaştığı noktalar."),
    "calistay": ("Bu bir ÇALIŞTAY / BEYİN FIRTINASI; ek alan:\n"
                 "- fikirler: ortaya atılan öneri ve fikirler; 'fikir' kısa, 'tema' fikirleri gruplayan 2-4 kelimelik "
                 "başlık (benzer fikirlere aynı tema). Öneri ve fikirleri 'kararlar'a ya da 'acik_sorular'a YAZMA, buraya "
                 "yaz. 'kararlar'a yalnız açık uzlaşma girer ('karar verdik', 'böyle yapıyoruz', 'anlaştık')."),
}
_EK_KURAL = ("Ek alanlarda da aynı kurallar geçerli: yalnız söyleneni yaz, ⟨⟩ içinden madde / sayı / tarih üretme, (?) "
             "yazma; bölümde yoksa liste boş kalır.")
_LISTE_EK = {
    "secenekler": "Aynı seçeneği tek nesnede birleştir, artı ve eksileri birleştir; 'secildi' yalnız girdide 'evet' ise true.",
    "fikirler": "Aynı fikri tek maddede birleştir, benzer temaları ortak adla yaz; fikri 'kararlar'a taşıma.",
}
# not satirinda madde metninden once gelen, kodun yazdigi onekler (dayanak / deger denetimi bunlari madde saymaz)
SABLON_ONEKLERI = ("Seçilen (?): ", "Seçilen: ", "Artı: ", "Eksi: ")


def sablon_adi(sablon):
    """Gecerli sablon anahtari; bilinmeyen / 'otomatik' / bos -> 'genel'."""
    return sablon if sablon in SABLONLAR else "genel"


def sablon_alanlari(sablon):
    return SABLON_ALANLARI.get(sablon_adi(sablon), ())


def _sema_genislet(taban, sablon):
    alanlar = sablon_alanlari(sablon)
    if not alanlar:
        return taban
    return {"type": "object",
            "properties": dict(taban["properties"], **{a: _SABLON_SEMA[t] for a, _, t in alanlar}),
            "required": list(taban["required"]) + [a for a, _, _ in alanlar]}


def bolum_semasi(sablon="genel"):
    """Map (parca) semasi: 'genel' icin BOLUM_SEMASI'nin kendisi; diger sablonlarda ek alanlar zorunlu (sunucu uygular;
    cevap ayristirilirken eksik ek alan bos liste sayilir: _sema_json)."""
    return _sema_genislet(BOLUM_SEMASI, sablon)


def liste_semasi(sablon="genel"):
    return _sema_genislet(LISTE_SEMASI, sablon)


def map_sistemi(sablon="genel"):
    ek = _MAP_EK.get(sablon_adi(sablon))
    return f"{MAP_SISTEM}\n{ek}\n{_EK_KURAL}" if ek else MAP_SISTEM


def liste_sistemi(sablon="genel"):
    alanlar = sablon_alanlari(sablon)
    if not alanlar:
        return LISTE_SISTEM
    ek = [f"Ek listeler ({', '.join(a for a, _, _ in alanlar)}): aynı kurallarla birleştir (benzerleri tek maddede, yeni "
          f"madde ekleme, [B..] etiketini yazma)."]
    ek += [_LISTE_EK[a] for a, _, _ in alanlar if a in _LISTE_EK]
    return LISTE_SISTEM + "\n" + " ".join(ek)


_SABLON_BASLIK_KUCUK = {tr_kucuk(b) for v in SABLON_ALANLARI.values() for _, b, _ in v}


def sablon_bolumu_mu(baslik):
    """'## Fikirler' gibi bir not basligi (ya da duz baslik metni) sablona ozel bir bolum mu?"""
    return tr_kucuk(str(baslik or "").lstrip("#").strip()) in _SABLON_BASLIK_KUCUK


# Otomatik secim: davet konusu once, sonra davet metni; ayni metinde ozelden genele. Eslesme Turkce harf / buyuk-kucuk
# duyarsiz (sade ASCII). Guclu ipuclari (birebir, calistay, cok kelimeli 'haftalik durum' ...) konu ve davet metninde
# aranir; tek kelimelik 'durum', 'onay', 'karar', 'status' gibi zayif ipuclari yalniz konuda ve kelime sonu sinirli
# ('durumu' evet; 'durumunda', 'durumsal', 'statusbar', 'kararlilik' hayir): davet metninde ('Katılamamanız durumunda',
# 'onayınızı rica ederim', 'karar verilmeyecek') neredeyse her davette gecerler.
_K = r"(?![a-z0-9])"                                # kelime sonu
SABLON_GUCLU = (
    ("birebir", (("birebir", r"birebir"), ("bire bir", r"bire bir" + _K), ("one on one", r"one[ -]on[ -]one" + _K),
                 ("one-to-one", r"one[ -]to[ -]one" + _K), ("1on1", r"1[ -]?on[ -]?1"),
                 ("1:1", r"1:1"), ("1-1", r"1-1"))),
    ("calistay", (("calistay", r"calistay"), ("workshop", r"workshop"), ("beyin firtinasi", r"beyin firtinasi"),
                  ("brainstorm", r"brainstorm"))),
    ("karar", (("karar toplantisi", r"karar toplantisi"), ("decision meeting", r"decision meeting"))),
    ("haftalik", (("haftalik durum", r"haftalik durum"), ("weekly status", r"weekly (?:status|sync)" + _K),
                  ("durum toplantisi", r"durum toplantisi"), ("status meeting", r"status meeting"))),
)
# yalniz konuda, guclulerden sonra; 'Haftalık karar': karar (toplanti turu) > haftalik (siklik)
SABLON_ZAYIF = (
    ("karar", (("karar", r"karar(?:i|in|lar|lari|larin)?" + _K), ("degerlendirme", r"degerlendirme(?:si|leri)?" + _K),
               ("onay", r"onay(?:i|lar|lari)?" + _K), ("decision", r"decisions?" + _K))),
    ("haftalik", (("haftalik", r"haftalik"), ("weekly", r"weekly" + _K), ("durum", r"durum(?:u|un|lar|lari)?" + _K),
                  ("status", r"status" + _K))),
)


def _sablon_sade(s):
    m = tr_kucuk(s)
    for a, b in zip("çğıöşüâî", "cgiosuai"):
        m = m.replace(a, b)
    return re.sub(r"\s+", " ", m)


def _anahtar_gecer(desen, metin):
    """Kelime basinda; rakamla baslayan ('1:1', '1-1', '1on1') rakama / tarihe / ondaliga bitisik degil ('11-12',
    '2026-11-01', '1-1,5 saat' degil; 'RE:1:1' evet)."""
    if desen[0].isdigit():
        return re.search(r"(?<![\d/.-])(?<!\d:)" + desen + r"(?![\d:/-])(?![.,]\d)", metin) is not None
    return re.search(r"(?<![a-z0-9])" + desen, metin) is not None


def sablon_tespit(konu, govde=""):
    """Davet konusu / metninden sablon -> (sablon, neden). Bulunamazsa ('genel', neden)."""
    for yer, metin, gruplar in (("davet konusunda", konu, SABLON_GUCLU + SABLON_ZAYIF),
                                ("davet metninde", govde, SABLON_GUCLU)):
        sade = _sablon_sade(metin or "")
        if not sade.strip():
            continue
        for sablon, anahtarlar in gruplar:
            for ad, desen in anahtarlar:
                if _anahtar_gecer(desen, sade):
                    return sablon, f"otomatik: {yer} '{ad}' geçiyor"
    return "genel", "otomatik: davette şablon ipucu yok"


def sablon_sec(ayar_degeri, konu="", govde=""):
    """Ayar degeri sabit bir sablonsa o (zorunlu), 'otomatik' / bos / bilinmeyense davetten tespit -> (sablon, neden)."""
    if ayar_degeri in SABLONLAR:
        return ayar_degeri, "ayarda sabit şablon"
    return sablon_tespit(konu, govde)


def sablon_etiketi(sablon):
    return SABLONLAR.get(sablon_adi(sablon), SABLONLAR["genel"])[0]


# ---------- yardimcilar ----------

def token_tahmin(s):
    return max(1, len(s) // 3)


def _tokenize(govde, bicim="prompt"):
    """POST {kok}/tokenize -> token sayisi | None. Ilk basarisizlikta neden bir kez bildirilir ve 5 dk
    boyunca sunucu sorulmaz (eskiden her cagri sessizce 30 sn bekleyip tahmine dusebiliyordu)."""
    global _TOKENIZE_KAPALI_SONA, _tokenize_bildirildi, _MESAJ_TOKENIZE
    if not SUNUCU_TOKENIZER or time.time() < _TOKENIZE_KAPALI_SONA:
        return None
    try:
        r = _http.post(TOKENIZE_URL, json=govde)
        if r.status_code == 200:
            return int(r.json()["count"])
        if bicim == "mesaj" and r.status_code in (400, 422):
            _MESAJ_TOKENIZE = False          # eski surum: 'messages' bicimi yok, 'prompt' ile devam
            return None
        neden = f"HTTP {r.status_code}"
    except Exception as e:
        neden = type(e).__name__
    _TOKENIZE_KAPALI_SONA = time.time() + 300
    if not _tokenize_bildirildi:
        _tokenize_bildirildi = True
        m = f"sunucunun /tokenize'ı kullanılamıyor ({neden}); token sayısı tahminle (karakter/3) yapılıyor"
        AYAR_UYARILARI.append(m)
        _gunluge_yaz(olay="tokenize_yok", zaman=dt.datetime.now().strftime("%H:%M:%S"), neden=neden)
        _uyar(m)
    return None


def token_say(metin):
    """Sunucunun tokenizer'iyla sayar; ulasamazsa kaba tahmin. Ayni metin (genel ozet gruplamasi,
    soru-cevap bolum secimi) tekrar sayilmaz."""
    anahtar = hash(metin)
    n = _token_onbellek.get(anahtar)        # tek okuma: 'in' + ayri okuma arasinda clear() KeyError veriyordu
    if n is not None:
        return n
    n = _tokenize({"model": MODEL, "prompt": metin})
    if n is None:
        return token_tahmin(metin)
    if len(_token_onbellek) > 512:
        _token_onbellek.clear()
    _token_onbellek[anahtar] = n
    return n


def _giris_token(mesajlar):
    """Sohbet girdisinin token sayisi. Sunucu 'messages' bicimini sayabiliyorsa sablon dahil kesin sayim;
    yoksa duz metin sayimi + sablon payi (60)."""
    if _MESAJ_TOKENIZE:
        n = _tokenize({"model": MODEL, "messages": mesajlar, "add_generation_prompt": True}, "mesaj")
        if n is not None:
            return n
    return token_say("\n".join(m["content"] for m in mesajlar)) + 60


def _metin(x):
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        return " — ".join(str(v) for v in x.values() if v not in (None, "", "-"))
    if isinstance(x, list):
        return ", ".join(_metin(i) for i in x)
    return "" if x is None else str(x)


def json_ayikla(metin):
    metin = re.sub(r"```(?:json)?", "", dusunce_temizle(metin)).strip()
    a, b = metin.find("{"), metin.rfind("}")
    if a < 0 or b < 0:
        raise ValueError("JSON yok")
    for aday in (metin[a:b + 1], metin[a:]):
        try:
            return json.loads(aday)
        except ValueError:
            pass
    # JSON'dan sonra gelen aciklama ('{...} Not: {x}') ilk nesneyi bozmasin; sondaki virgullar dizge disinda onarilir
    onarilmis = _son_virgul_sil(metin[a:])
    try:
        nesne, _ = json.JSONDecoder().raw_decode(onarilmis)
    except ValueError:
        return json.loads(_son_virgul_sil(metin[a:b + 1]))
    if not isinstance(nesne, dict):
        raise ValueError("JSON nesnesi yok")
    return nesne


def _son_virgul_sil(metin):
    """'[1, 2,]' / '{"a": 1,}' sondaki virgulu atar; metin degerlerinin ICINDEKI ', }' ya da ', ]' dokunulmaz
    (eskiden tum metinde regex: dizge icindeki ', }' bozuluyordu)."""
    cikti, i, n, dizge, kacis = [], 0, len(metin), False, False
    while i < n:
        c = metin[i]
        if dizge:
            cikti.append(c)
            if kacis:
                kacis = False
            elif c == "\\":
                kacis = True
            elif c == '"':
                dizge = False
        elif c == '"':
            dizge = True
            cikti.append(c)
        elif c == ",":
            k = i + 1
            while k < n and metin[k].isspace():
                k += 1
            if k < n and metin[k] in "}]":
                i += 1
                continue
            cikti.append(c)
        else:
            cikti.append(c)
        i += 1
    return "".join(cikti)


_DUSUNCE = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.S)
_DUSUNCE_ETIKET = re.compile(r"</?(?:think|thinking|reasoning)>")


def dusunce_temizle(metin):
    """Qwen dusunme blogunu cevaptan ayiklar. Sunucuda reasoning parser yoksa ya da cevap dusunme
    sirasinda max_tokens'a takildiysa (Ingilizce) dusunce metni content'e duser; notta Ingilizce
    paragraflar ve yarim kalan cevap olarak gorunur."""
    if not metin:
        return ""
    metin = _DUSUNCE.sub("", metin)
    if "<think>" in metin:                    # kapanmamis dusunce: cevap hic baslamamis
        metin = metin.split("<think>", 1)[0]
    if "</think>" in metin:                   # acilis etiketi sablonda verilmis, yalniz kapanis gelmis
        metin = metin.split("</think>", 1)[1]
    return metin.strip()


_EN_KELIME = {"the", "and", "of", "to", "is", "are", "was", "were", "be", "been", "will", "with", "for",
              "that", "this", "on", "in", "it", "we", "they", "should", "would", "which", "from", "by",
              "as", "an", "at", "not", "have", "has", "about", "their", "there", "discussed", "meeting"}
_TR_KELIME = {"ve", "bir", "bu", "için", "ile", "da", "de", "olarak", "olan", "gibi", "daha", "çok", "ama",
              "ancak", "ise", "sonra", "önce", "üzerinde", "konusunda", "toplantı", "karar", "edildi",
              "yapıldı", "belirtildi", "gerekiyor", "yapılacak", "ele", "alındı"}


def ingilizce_mi(metin):
    """Metin cogunlukla Ingilizce mi? (Turkce toplantida araya giren Ingilizce terimler sayilmaz.)"""
    kelimeler = re.findall(r"[a-zçğıöşü]+", (metin or "").replace("İ", "i").replace("I", "ı").lower())
    if len(kelimeler) < 6:
        return False
    en = sum(k in _EN_KELIME for k in kelimeler)
    tr = sum(k in _TR_KELIME or any(c in "çğıöşü" for c in k) for k in kelimeler)
    return en >= 3 and en > tr


GUNLUK_FN = None          # motor ayarlar: her LLM cagrisinin istatistigi (icerik degil) toplanti klasorune yazilir


def _gorev_adi(mesajlar):
    sistem = (mesajlar[0]["content"] if mesajlar else "").lstrip()
    for ad, sablon in (("duzeltme", DUZELT_ONEK), ("bolum", MAP_SISTEM), ("liste", LISTE_SISTEM),
                       ("genel", GENEL_SISTEM), ("soru", SORU_SISTEM)):
        if sistem.startswith(sablon):
            return ad
    return "onarim" if sistem.startswith("Bozuk JSON") else "diger"


def _gunluge_yaz(**kayit):
    if GUNLUK_FN:
        try:
            GUNLUK_FN(kayit)
        except Exception:
            pass


def sor(mesajlar, max_tokens, sema=None, effort="medium", temperature=0.6, dusunme=True):
    """Tek LLM cagrisi -> (metin, finish_reason). Hata LlmHatasi olarak (Turkce, tani degerleriyle) yukselir."""
    global SON_HATA
    if OTOMATIK_TANI:
        sunucuyu_tani()               # kilit altinda: tani suruyorsa bitmesi beklenir, yapildiysa hemen doner
    t0 = time.time()
    kayit = {"zaman": dt.datetime.now().strftime("%H:%M:%S"), "gorev": _gorev_adi(mesajlar),
             "dusunme": effort if dusunme else False, "sema": bool(sema), "model": MODEL, "pencere": BAGLAM_PENCERESI}
    try:
        metin, neden, ek = _sor(mesajlar, max_tokens, sema, effort, temperature, dusunme)
    except Exception as e:
        hm = hata_metni(e)
        SON_HATA = hm
        _gunluge_yaz(**kayit, sure_sn=round(time.time() - t0, 1), http=_hata_durumu(e),
                     tur=type(e).__name__, hata=hm[:600])
        if isinstance(e, (LlmHatasi, ValueError)):
            raise
        raise LlmHatasi(hm, _hata_durumu(e)) from e
    _gunluge_yaz(**kayit, **ek, finish=neden, sure_sn=round(time.time() - t0, 1), cevap_karakter=len(metin),
                 ingilizce=ingilizce_mi(metin))
    return metin, neden


def _yanit_coz(r):
    """Akissiz cevap -> (content, reasoning, finish_reason, usage)."""
    c = r.choices[0]
    m = c.message
    dusunce = getattr(m, "reasoning_content", None) or getattr(m, "reasoning", None) or ""
    return m.content or "", dusunce, c.finish_reason, getattr(r, "usage", None)


def _cagir(ortak):
    """Istek: AKIS acikken stream=True (parcalar geldikce route'un bosta kalma sayaci sifirlanir: OpenShift
    route varsayilani 30 sn, dusunmeli 3000-4500 token'lik bir bolum ozeti bunu asabiliyordu)."""
    if not AKIS:
        return _yanit_coz(client.chat.completions.create(**ortak))
    eb = dict(ortak.get("extra_body") or {}, stream_options={"include_usage": True})
    akis = client.chat.completions.create(stream=True, **dict(ortak, extra_body=eb))
    if hasattr(akis, "choices"):                       # akissiz cevap (sahte istemci, proxy)
        return _yanit_coz(akis)
    icerik, dusunce, neden, kullanim = [], [], None, None
    try:
        for p in akis:
            if getattr(p, "usage", None):
                kullanim = p.usage
            for c in getattr(p, "choices", None) or []:
                d = getattr(c, "delta", None)
                if d is not None:
                    if getattr(d, "content", None):
                        icerik.append(d.content)
                    rc = getattr(d, "reasoning_content", None) or getattr(d, "reasoning", None)
                    if rc:
                        dusunce.append(rc)
                if getattr(c, "finish_reason", None):
                    neden = c.finish_reason
    finally:
        kapat = getattr(akis, "close", None)
        if kapat:
            try:
                kapat()
            except Exception:
                pass
    return "".join(icerik), "".join(dusunce), neden, kullanim


def _sema_istegi(ortak, sema, ek):
    """json_schema'li istek. Yalnizca semayla ilgili (ya da nedeni belirsiz) 400/422'de semasiz tekrar;
    baglam tasmasi ve bilinmeyen model gibi 400'ler AYNEN yukselir (eskiden her 400 semasiz tekrar
    ediliyor, ayni hata iki kat surede geliyordu). Sema reddedildiyse oturum boyunca gonderilmez."""
    global _SEMA_DESTEGI
    try:
        return _cagir(dict(ortak, response_format={"type": "json_schema",
                                                   "json_schema": {"name": "cikti", "schema": sema}}))
    except (BadRequestError, UnprocessableEntityError) as e:
        g = _govde(e)
        if _BAGLAM_IFADE.search(g) or _BUTCE_IFADE.search(g) or "does not exist" in g:
            raise                              # baglam / dusunme butcesi alani: _sor ele alir (semasiz tekrar cozmez)
        sonuc = _cagir(ortak)
        ek["sema_dustu"] = True
        if _SEMA_IFADE.search(g) and _SEMA_DESTEGI:
            _SEMA_DESTEGI = False
            _uyar(f"LLM sunucusu json_schema'yı reddetti; şemasız devam ediliyor ({_kisalt(g, 160)})")
        return sonuc


def dusunme_butcesi(max_tokens):
    """Dusunmeli cagrida gonderilecek thinking_token_budget (vLLM): max_tokens'in en az DUSUNME_ICERIK_PAYI'si
    cevaba kalir; dusunme bunu asinca eskiden cevap bos ('content': null) donuyordu. 0/None: gonderilmez."""
    if not DUSUNME_BUTCESI or not _BUTCE_DESTEGI:
        return None
    tavan = int(max_tokens * (1 - DUSUNME_ICERIK_PAYI))
    return min(DUSUNME_BUTCESI, tavan) if tavan >= 64 else None


def _butce_kapat(govde):
    """thinking_token_budget oturum boyunca gonderilmez ve BIR KEZ bildirilir (desteklemeyen / eski sunucu her
    istekte ayni 400'u uretmesin)."""
    global _BUTCE_DESTEGI
    if not _BUTCE_DESTEGI:
        return
    _BUTCE_DESTEGI = False
    m = f"LLM sunucusu thinking_token_budget alanını reddetti; düşünme bütçesi gönderilmiyor ({_kisalt(govde, 160)})"
    AYAR_UYARILARI.append(m)
    _gunluge_yaz(olay="dusunme_butcesi_yok", zaman=dt.datetime.now().strftime("%H:%M:%S"), hata=_kisalt(govde, 300))
    _uyar(m)


def _butce_siniflandir(e):
    """400/422 hatasi dusunme butcesi alanindan mi? 'kesin': govde alani adiyla anıyor; 'supheli': baglam/model/sema
    nedeni de yok (alansiz tekrar gerekir); None: baska neden."""
    if not isinstance(e, (BadRequestError, UnprocessableEntityError)):
        return None
    g = _govde(e) or ""
    if _BUTCE_IFADE.search(g):
        return "kesin"
    if _BAGLAM_IFADE.search(g) or _SEMA_IFADE.search(g) or "does not exist" in g:
        return None
    return "supheli"


def _sor(mesajlar, max_tokens, sema, effort, temperature, dusunme):
    global BAGLAM_PENCERESI
    deneme = 0
    butce_denemesi = 0
    supheli_govde = None
    butce_yok = False                             # bu cagri icin alan atilir (reddedildi / nedeni belirsiz 400)
    while True:
        giris = _giris_token(mesajlar)
        izin = BAGLAM_PENCERESI - giris - MARJ
        if izin < 256:
            raise ValueError(f"girdi bağlam penceresine sığmıyor: girdi {giris} + pay {MARJ} token, "
                             f"pencere {BAGLAM_PENCERESI} (config.json 'context' / sunucu max_model_len)")
        mt = min(max_tokens, izin)
        kw = {"reasoning_effort": effort} if dusunme else {"enable_thinking": False}
        eb = {"chat_template_kwargs": kw}
        tb = dusunme_butcesi(mt) if dusunme and not butce_yok else None
        if tb:
            eb["thinking_token_budget"] = tb
        ortak = dict(model=MODEL, messages=mesajlar, max_tokens=mt, temperature=temperature, top_p=0.95,
                     extra_body=eb)
        ek = {}
        try:
            if sema and _SEMA_DESTEGI:
                icerik, dusunce, neden, u = _sema_istegi(ortak, sema, ek)
            else:
                icerik, dusunce, neden, u = _cagir(ortak)
            break
        except openai.APIError as e:
            # 400/422 ya da akis ici hata olayi (vLLM uzun istegi akis basladiktan sonra da reddedebilir)
            if not (isinstance(e, (BadRequestError, UnprocessableEntityError)) or _akis_hatasi(e)):
                raise
            sinif = _butce_siniflandir(e) if tb and butce_denemesi == 0 else None
            if sinif:
                butce_denemesi += 1               # ayni istek alansiz, pencere denemesini harcamadan
                butce_yok = True
                if sinif == "kesin":
                    _butce_kapat(_govde(e))
                else:
                    supheli_govde = _govde(e)     # alansiz istek basarili olursa neden bu alandi
                continue
            yeni = _baglam_siniri(_govde(e))
            if deneme == 0 and yeni and yeni < BAGLAM_PENCERESI:
                # sunucunun penceresi bildigimizden kucuk: ogren ve ayni istegi bir kez kirpilmis butceyle dene
                _uyar(f"LLM bağlam penceresi {BAGLAM_PENCERESI} değil {yeni}; bütçeler buna göre küçültüldü")
                BAGLAM_PENCERESI = yeni
                deneme += 1
                continue
            raise
    if supheli_govde is not None and not tb:
        _butce_kapat(supheli_govde)               # alansiz tekrar basardi: 400'un nedeni thinking_token_budget'ti
    ek["dusunme_butcesi"] = tb
    ayrinti = getattr(u, "completion_tokens_details", None)
    ek.update(giris_token=giris, max_tokens=mt, cikti_token=getattr(u, "completion_tokens", None),
              sunucu_giris_token=getattr(u, "prompt_tokens", None),
              dusunce_token=getattr(ayrinti, "reasoning_tokens", None) if ayrinti else None,
              dusunce_karakter=len(dusunce or ""), dusunce_icerikte=bool(_DUSUNCE_ETIKET.search(icerik or "")))
    return dusunce_temizle(icerik), neden, ek


def _sema_json(metin, sema):
    """JSON'u ayiklar ve semanin zorunlu anahtarlarini tasiyip tasimadigini dogrular; degilse ValueError."""
    v = json_ayikla(metin)
    # sablon ek alanlari zorunlu gonderilir (sunucu uygular) ama eksikse cevap reddedilmez: liste bos sayilir
    eksik = [k for k in (sema or {}).get("required", []) if k not in v and k not in ALAN_TURU]
    if eksik:
        raise ValueError(f"eksik alanlar: {eksik}")
    return v


def _json_sor(mesajlar, max_tokens, sema, effort="medium", temperature=0.6, dusunme=True):
    """JSON ister; kesilirse dusunmesiz ve daha genis tekrar dener, bozuksa onarim. Basarisizsa None.
    Teshis (vLLM 0.16 + qwen3 reasoning parser): dusunme aciksa json_schema UYGULANMIYOR, model duz metin
    donebiliyor. Duz metni 'onarmak' yanlis anahtarli JSON'a, yani sessizce bos bir parca ozetine yol
    aciyordu. Bu yuzden once istek dusunmesiz (sema zorunlu) tekrarlanir, onarim en son denenir."""
    metin, neden = sor(mesajlar, max_tokens, sema=sema, effort=effort, temperature=temperature, dusunme=dusunme)
    if neden == "length" or not metin:
        metin, neden = sor(mesajlar, int(max_tokens * 1.5), sema=sema, temperature=temperature, dusunme=False)
        dusunme = False
    try:
        return _sema_json(metin, sema)
    except Exception:
        pass
    if dusunme:
        metin, neden = sor(mesajlar, max_tokens, sema=sema, temperature=temperature, dusunme=False)
        try:
            return _sema_json(metin, sema)
        except Exception:
            pass
    if not metin or "{" not in metin:
        return None
    onarim = [{"role": "system", "content": "Bozuk JSON'u düzelt. Yalnızca geçerli JSON döndür."},
              {"role": "user", "content": metin}]
    onarilmis, _ = sor(onarim, max_tokens, sema=sema, temperature=0.1, dusunme=False)
    try:
        return _sema_json(onarilmis, sema)
    except Exception:
        return None


def _json_tam(mesajlar, max_tokens, sema, temperature=0.2):
    """Dusunmesiz JSON; kesilmis (length) cevabi KABUL ETMEZ — yarim liste sessizce eksik not demektir.
    Bir kez daha genis butceyle dener; yine olmazsa None (cagiran LLM'siz yedege duser)."""
    for b in (max_tokens, int(max_tokens * 1.6)):
        metin, neden = sor(mesajlar, b, sema=sema, temperature=temperature, dusunme=False)
        if neden == "length":
            continue
        try:
            return _sema_json(metin, sema)
        except Exception:
            continue
    return None


def baglam_metni(baslik, tarih, konusmacilar, terim_metni, gundem=""):
    m = (f"Toplantı: {baslik}\nTarih: {tarih.isoformat()} ({GUNLER[tarih.weekday()]})\n"
         f"Katılımcılar: {', '.join(konusmacilar) or '-'}\n"
         f"Terimler (doğru yazımlar): {terim_metni}")
    if gundem:
        m += f"\nGündem/davet metni: {gundem[:800]}"
    m += ("\nKural: Yalnızca bu toplantının konuşma metnini kullan. Terim listesi ve gündem sadece yazım/ad ipucudur; "
          "metinde geçmeyen bir konuyu, terimi ya da kararı ekleme, başka toplantılardan bilgi varsayma.")
    return m


# ---------- ozet ----------

def _isaretsiz(x):
    """Sablon alanlarinda (?) isaretini model yazmaz: kod koyar (dayanak / deger denetimi); modelinki atilir."""
    return normalize_bosluk(re.sub(r"\(\?\)", " ", _metin(x)))


def _evet(x):
    return x is True or (isinstance(x, str) and tr_kucuk(x.strip()) in ("true", "evet", "yes", "1"))


def _secenek_satiri(metin):
    """Duz metin donen secenek ('X | artı: a; b | eksi: c | seçildi: evet', liste girdisinin bicimi) -> nesne."""
    parcalar = [p.strip() for p in metin.split("|")]
    d = {"secenek": parcalar[0], "artilar": [], "eksiler": [], "secildi": False}
    for p in parcalar[1:]:
        ad, _, deger = p.partition(":")
        ad = _sade(ad)
        if ad in ("arti", "eksi"):
            d["artilar" if ad == "arti" else "eksiler"] = [x.strip() for x in deger.split(";") if x.strip(" -")]
        elif ad == "secildi":
            d["secildi"] = _evet(deger.strip())
    return d


def sablon_alani_normalize(v, tur):
    """Sablon ek alani -> temiz liste. metin: [str]; secenek: [{secenek, artilar, eksiler, secildi}]; fikir: [{fikir, tema}].
    Bos madde atilir; duz metin gelen secenek / fikir nesneye cevrilir."""
    if not isinstance(v, list):
        v = [v] if v else []
    sonuc = []
    for x in v:
        if tur == "metin":
            m = _isaretsiz(x.get("madde") if isinstance(x, dict) else x)
            if madde_dolu(m):
                sonuc.append(m)
        elif tur == "secenek":
            d = x if isinstance(x, dict) else _secenek_satiri(_metin(x))
            ad = _isaretsiz(d.get("secenek") or d.get("ad"))
            if not madde_dolu(ad):
                continue
            artilar, eksiler = d.get("artilar") or [], d.get("eksiler") or []
            sonuc.append({"secenek": ad,
                          "artilar": [_isaretsiz(a) for a in (artilar if isinstance(artilar, list) else [artilar])
                                      if madde_dolu(_isaretsiz(a))],
                          "eksiler": [_isaretsiz(a) for a in (eksiler if isinstance(eksiler, list) else [eksiler])
                                      if madde_dolu(_isaretsiz(a))],
                          "secildi": _evet(d.get("secildi"))})
        else:
            m = None if isinstance(x, dict) else re.match(r"^(.*?)\s*\(tema:\s*([^()]*)\)\s*$", _metin(x))
            d = x if isinstance(x, dict) else ({"fikir": m.group(1), "tema": m.group(2)} if m else {"fikir": x})
            fikir = _isaretsiz(d.get("fikir") or d.get("madde"))
            if madde_dolu(fikir):
                sonuc.append({"fikir": fikir, "tema": _isaretsiz(d.get("tema")) or "Diğer"})
    return sonuc


def normalize_ozet(o):
    if not isinstance(o, dict):
        return None
    temiz = {"ozet": _metin(o.get("ozet") or "").strip()}
    for alan in ("konular", "kararlar", "acik_sorular", "belirsiz_terimler"):
        v = o.get(alan) or []
        if not isinstance(v, list):
            v = [v]
        temiz[alan] = [_metin(i).strip() for i in v if _metin(i).strip()]
    aks = o.get("aksiyonlar") or []
    if not isinstance(aks, list):
        aks = [aks]
    temiz["aksiyonlar"] = []
    for a in aks:
        if isinstance(a, dict):
            madde = _metin(a.get("madde") or "").strip()
            if not madde_dolu(madde):
                continue          # bos maddeli aksiyon: tabloda '-' satiri, kisi listesinde bos is olur
            temiz["aksiyonlar"].append({"madde": madde,
                                        "sorumlu": _metin(a.get("sorumlu") or "belirsiz") or "belirsiz",
                                        "tarih": _metin(a.get("tarih") or "-") or "-"})
        elif madde_dolu(_metin(a)):
            temiz["aksiyonlar"].append({"madde": _metin(a).strip(), "sorumlu": "belirsiz", "tarih": "-"})
    for alan, tur in ALAN_TURU.items():             # sablon ek alanlari yalniz cevapta varsa (genel: hic yok)
        if alan in o:
            temiz[alan] = sablon_alani_normalize(o.get(alan), tur)
    return temiz


def alan_metni(oge, tur):
    """Sablon alani ogesinin karsilastirilan / denetlenen metni."""
    if tur == "secenek":
        return oge.get("secenek", "")
    if tur == "fikir":
        return oge.get("fikir", "")
    return str(oge)


def _sablon_metinleri(o):
    return [alan_metni(x, ALAN_TURU[a]) for a in ALAN_TURU for x in o.get(a) or []]


def madde_dolu(madde):
    """Aksiyon maddesi gercekten bir sey soyluyor mu? ('', '-', tek harf degil)"""
    return len(_metin(madde).strip().strip(" -.")) >= 2


def _ozet_dili(o):
    return " ".join([o["ozet"]] + o["kararlar"] + o["acik_sorular"] + [a["madde"] for a in o["aksiyonlar"]]
                    + _sablon_metinleri(o))


ONCEKI_BAGLAM_TOKEN = 400         # parca ortusmesi: onceki parcanin son satirlari + acik maddeler (MIMARI_v3 §6)
ONCEKI_SATIR = 6


def onceki_baglam_hazirla(konular=None, satirlar=None, acik=None, tavan=ONCEKI_BAGLAM_TOKEN):
    """Sonraki parcaya gececek baglam -> {"konular", "satirlar" (metin), "acik"}: son ONCEKI_SATIR satir (blok metni
    satirlari) + onceki parcanin cevapsiz kalan maddeleri; ikisi birlikte ~`tavan` token'i (len//3) gecmez. Once acik
    maddeler (en fazla 3, her biri <=160 karakter), kalan pay son satirlara; kirpma satir basindan (eski) yapilir."""
    konular = [_metin(k).strip() for k in (konular or []) if _metin(k).strip()]
    acik = [_kisalt(_metin(a), 160) for a in (acik or []) if _metin(a).strip()][:3]
    pay = tavan * 3 - sum(len(a) + 4 for a in acik)
    secilen = []
    for satir in reversed([x for x in (satirlar or []) if str(x).strip()][-ONCEKI_SATIR:]):
        satir = _kisalt(satir, 400)
        if pay - len(satir) - 1 < 0 and secilen:
            break
        pay -= len(satir) + 1
        secilen.append(satir)
    return {"konular": konular, "satirlar": "\n".join(reversed(secilen)), "acik": acik}


def bolum_ozetle(blok, sira, baglam, onceki_konular=None, sablon="genel"):
    """onceki_konular: onceki bolumun konu listesi (eski bicim) ya da onceki_baglam_hazirla() sozlugu
    (konular + son satirlar + acik maddeler). Satirlar YALNIZ baglamdir: bolumun ilk satirlari onlara cevap
    olabilir; madde bunlardan uretilmez. sablon: map prompt'u ve semasi sablonun ek alanlarini da ister
    (map_sistemi / bolum_semasi; 'genel' eskisiyle ayni)."""
    sistem, sema = map_sistemi(sablon), bolum_semasi(sablon)
    if isinstance(onceki_konular, dict):
        bag = onceki_konular
        onceki, son_satirlar, acik = bag.get("konular") or [], str(bag.get("satirlar") or "").strip(), bag.get("acik") or []
    else:
        onceki, son_satirlar, acik = onceki_konular or [], "", []
    onceki = [_metin(k) for k in onceki if _metin(k).strip()]
    devam = f"\nÖnceki bölümde konuşulanlar: {', '.join(onceki)}" if onceki else ""
    if acik:
        devam += "\nÖnceki bölümde cevapsız kalanlar: " + "; ".join(_metin(a) for a in acik)
    if son_satirlar:
        devam += ("\nÖnceki bölümün son satırları (YALNIZ bağlam; bunlardan madde ya da özet üretme, yalnızca bu bölümün "
                  "ilk satırlarını anlamak için kullan):\n" + son_satirlar)
    kullanici = {"role": "user", "content": f"{baglam}{devam}\n\nToplantının {sira}. bölümü:\n\n{blok}"}
    mesajlar = [{"role": "system", "content": sistem}, kullanici]
    # JSON cikarimi derin dusunme istemez: "low" ile dusunme tokenleri kisa kalir, cevap kesilmez
    o = normalize_ozet(_json_sor(mesajlar, butce("bolum"), sema, effort="low", temperature=0.3))
    if o and ingilizce_mi(_ozet_dili(o)):
        # Model (cogunlukla dusunme dilinin etkisiyle) Ingilizce yazdi: dusunmesiz, dil kurali vurgulu tekrar
        tekrar = [{"role": "system", "content": sistem + "\n" + TURKCE_KURAL}, kullanici]
        o2 = normalize_ozet(_json_sor(tekrar, butce("bolum"), sema, temperature=0.2, dusunme=False))
        if o2 and not ingilizce_mi(_ozet_dili(o2)):
            o = o2
    if o and not o["ozet"].strip(" -.") and len(blok) > 200:
        # listeler dolu ama ozet alani bos: tam notun ozet paragrafi bu alanlardan kurulur, bos birakilmaz
        tekrar = [{"role": "system", "content": sistem + "\n'ozet' alanını ASLA boş bırakma."}, kullanici]
        o2 = normalize_ozet(_json_sor(tekrar, butce("bolum"), sema, temperature=0.2, dusunme=False))
        if o2 and o2["ozet"].strip(" -.") and not ingilizce_mi(_ozet_dili(o2)):
            o = o2
        elif o["konular"]:
            o["ozet"] = "Bu bölümde " + ", ".join(o["konular"]) + " konuşuldu."
    if o:
        for alan, _, _ in sablon_alanlari(sablon):
            o.setdefault(alan, [])                   # sablonun alani hep var: birlestirme ve not ayni anahtarlari gorur
        if sablon_adi(sablon) != "genel":
            o["sablon"] = sablon_adi(sablon)         # parca hangi sablonla ozetlendi (sablon degisirse yeniden ozet)
    return o


# ---------- birlestirme (tam not) ----------

def _sade(s):
    s = normalize_bosluk(s).lower()
    for a, b in zip("çğıöşüâî", "cgiosuai"):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9 ]", "", s)


def normalize_bosluk(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _sayilar(s):
    """Metindeki sayilar (kume): '300 bin TL' ile '500 bin TL', 'Sunucu 3' ile 'Sunucu 4' ayni madde degildir."""
    return set(re.findall(r"\d+", str(s or "")))


def _benzer(a, b, esik=0.85):
    a, b = _sade(a), _sade(b)
    if not a:
        return False
    if a == b:
        return True
    if _sayilar(a) != _sayilar(b):                  # tutar / numara / tarih farkli: ayri madde (biri kaybolmasin)
        return False
    sm = difflib.SequenceMatcher(None, a, b)
    # ratio() pahali: ust sinir olan ucuz olculer esigin altindaysa sonuc zaten False (uzun toplantida O(n^2) karsilastirma)
    return sm.real_quick_ratio() >= esik and sm.quick_ratio() >= esik and sm.ratio() >= esik


def _aksiyon_birlestir(eski, yeni):
    """Ayni is iki bolumde gectiyse sonrakini tut; sonrakinde sorumlu/tarih bossa oncekinden al."""
    v = dict(yeni)
    if v.get("sorumlu") in ("", "belirsiz", None) and eski.get("sorumlu") not in ("", "belirsiz", None):
        v["sorumlu"] = eski["sorumlu"]
    if v.get("tarih") in ("", "-", None) and eski.get("tarih") not in ("", "-", None):
        v["tarih"] = eski["tarih"]
    return v


def _sorumlu_adi(a):
    """Aksiyonun adlandirilmis sorumlusu (sade, '(?)' atilmis); belirsiz / bos ise ''."""
    ad = _sade(re.sub(r"\(\?\)", " ", str((a or {}).get("sorumlu") or ""))).strip()
    return "" if ad in ("", "belirsiz", "-") else ad


def _ayni_aksiyon(v, oge):
    """Iki aksiyon tek madde mi? Metin benzer (sayilari ayni) VE iki taraf da adlandirilmissa sorumlu ayni."""
    if not _benzer(v.get("madde", ""), oge.get("madde", "")):
        return False
    a, b = _sorumlu_adi(v), _sorumlu_adi(oge)
    return not (a and b and a != b)


def listeleri_tekille(bolumler):
    """LLM'siz birlestirme: bolum listelerini sirayla toplar, benzer maddelerde sonraki bolumu tutar.
    Hem LLM birlestirmesi basarisiz olursa yedek, hem de onun sonucunu denetlemek icin olcut. Sayilari farkli maddeler
    ('300 bin' / '500 bin') ve sorumlusu farkli aksiyonlar ('Sunucu 3' Ali / 'Sunucu 4' Ayse) birlestirilmez."""
    sonuc = {"kararlar": [], "aksiyonlar": [], "acik_sorular": []}

    def ekle(liste, oge, anahtar, birlestir=None, ayni=None):
        for i, v in enumerate(liste):
            if ayni(v, oge) if ayni else _benzer(anahtar(v), anahtar(oge)):
                liste[i] = birlestir(v, oge) if birlestir else oge
                return
        liste.append(oge)

    for alan in _sablon_alanlari_var(bolumler):
        sonuc[alan] = []
    for b in bolumler:
        for k in b.get("kararlar") or []:
            ekle(sonuc["kararlar"], k, str)
        for a in b.get("aksiyonlar") or []:
            if isinstance(a, dict) and madde_dolu(a.get("madde")):
                ekle(sonuc["aksiyonlar"], a, lambda x: x.get("madde", ""), _aksiyon_birlestir, _ayni_aksiyon)
        for q in b.get("acik_sorular") or []:
            ekle(sonuc["acik_sorular"], q, str)
        for alan in _sablon_alanlari_var([b]):
            tur = ALAN_TURU[alan]
            for x in sablon_alani_normalize(b.get(alan), tur):
                ekle(sonuc[alan], x, lambda o, t=tur: alan_metni(o, t), _SABLON_BIRLESTIR.get(tur))
    return sonuc


def _sablon_alanlari_var(bolumler):
    """Bolumlerde gecen sablon ek alanlari (sirali; 'genel' bolumlerde hic yok)."""
    return [a for a in ALAN_TURU if any(a in (b or {}) for b in bolumler)]


def _metin_birlesimi(eski, yeni):
    sonuc = list(eski or [])
    for x in yeni or []:
        if not any(_benzer(x, y) for y in sonuc):
            sonuc.append(x)
    return sonuc


def _secenek_birlestir(eski, yeni):
    """Ayni secenek iki bolumde: artilar / eksiler birlesir, 'secildi' herhangi birinde secildiyse."""
    return {"secenek": yeni["secenek"], "artilar": _metin_birlesimi(eski.get("artilar"), yeni.get("artilar")),
            "eksiler": _metin_birlesimi(eski.get("eksiler"), yeni.get("eksiler")),
            "secildi": bool(eski.get("secildi") or yeni.get("secildi"))}


def _fikir_birlestir(eski, yeni):
    return {"fikir": yeni["fikir"], "tema": yeni.get("tema") if yeni.get("tema") not in ("", "Diğer") else
            eski.get("tema") or "Diğer"}


_SABLON_BIRLESTIR = {"secenek": _secenek_birlestir, "fikir": _fikir_birlestir}


_ALAN_BASLIGI = {alan: baslik for v in SABLON_ALANLARI.values() for alan, baslik, _ in v}


def _girdi_satiri(x, tur):
    """Liste birlestirme girdisinde sablon ogesinin satiri ('[Bn]' oneki cagiranda)."""
    if tur == "secenek":
        return (f"{x.get('secenek', '')} | artı: {'; '.join(x.get('artilar') or []) or '-'} | eksi: "
                f"{'; '.join(x.get('eksiler') or []) or '-'} | seçildi: {'evet' if x.get('secildi') else 'hayır'}")
    if tur == "fikir":
        return f"{x.get('fikir', '')} (tema: {x.get('tema') or 'Diğer'})"
    return str(x)


def _liste_girdisi(bolumler, ilk_no=1):
    bloklar, adet = [], 0
    for alan, baslik in (("kararlar", "Kararlar"), ("aksiyonlar", "Aksiyonlar"), ("acik_sorular", "Açık sorular")):
        ogeler = []
        for i, b in enumerate(bolumler, ilk_no):
            for x in b.get(alan) or []:
                if alan == "aksiyonlar":
                    ogeler.append(f"- [B{i}] {x.get('madde', '')} — {x.get('sorumlu', 'belirsiz')} — {x.get('tarih', '-')}")
                else:
                    ogeler.append(f"- [B{i}] {x}")
        adet += len(ogeler)
        bloklar.append(f"{baslik}:\n" + ("\n".join(ogeler) or "-"))
    for alan in _sablon_alanlari_var(bolumler):      # sablon ek listeleri: yalniz dolu olanlar (genel girdisi ayni kalir)
        ogeler = [f"- [B{i}] {_girdi_satiri(x, ALAN_TURU[alan])}" for i, b in enumerate(bolumler, ilk_no)
                  for x in b.get(alan) or []]
        if ogeler:
            adet += len(ogeler)
            bloklar.append(f"{_ALAN_BASLIGI[alan]} ({alan}):\n" + "\n".join(ogeler))
    return "\n\n".join(bloklar), adet


def _liste_dili(v):
    return " ".join(v["kararlar"] + v["acik_sorular"] + [a["madde"] for a in v["aksiyonlar"]] + _sablon_metinleri(v))


LISTE_GRUP_MADDE = 70          # ilk kademede bir LLM isteginin birlestirdigi en fazla madde (daha fazlasi gruplanir)
LISTE_KADEME = 4               # grup sonuclarini yeniden birlestirme tur siniri


def _liste_yedege_dus(ilerleme, mesaj, **ek):
    """LLM'siz birlestirmeye dusuldu: hem Olaylar'a hem llm_log.jsonl'e yazilir (sessiz dusus yok)."""
    ilerleme(mesaj)
    _gunluge_yaz(olay="liste_yedek", zaman=dt.datetime.now().strftime("%H:%M:%S"), neden=_kisalt(mesaj, 200), **ek)


def _alan_sablonu(bolumler):
    """Bolumlerdeki sablon ek alanlarindan sablon adi (liste prompt'u / semasi icin); ek alan yoksa 'genel'."""
    alanlar = set(_sablon_alanlari_var(bolumler))
    return next((ad for ad, v in SABLON_ALANLARI.items() if alanlar & {a for a, _, _ in v}), "genel")


def _liste_girdi_ust(baglam, sablon="genel"):
    """Bir birlestirme isteginin girdi token ust siniri: cevap (girdi x ~1.3) butce('liste')'e ve girdi + cevap +
    sablon pencereye sigmali."""
    sistem = token_say(liste_sistemi(sablon) + baglam) + 150
    return max(300, min(int(butce("liste") / 1.3), int((BAGLAM_PENCERESI - MARJ - sistem) / 2.3)))


def _liste_gruplari(bolumler, baglam, madde_tavani=0):
    """Ardisik bolumleri bir istege sigacak gruplara boler -> [[(indeks, bolum), ...], ...] (kronoloji korunur)."""
    ust = _liste_girdi_ust(baglam, _alan_sablonu(bolumler))
    gruplar, mevcut, tok, adet = [], [], 0, 0
    for i, b in enumerate(bolumler):
        girdi, n = _liste_girdisi([b])
        t = token_say(girdi)
        if mevcut and (tok + t > ust or (madde_tavani and adet + n > madde_tavani)):
            gruplar.append(mevcut)
            mevcut, tok, adet = [], 0, 0
        mevcut.append((i, b))
        tok += t
        adet += n
    if mevcut:
        gruplar.append(mevcut)
    return gruplar


LISTE_KAPSAMA_ESIK = 0.6       # girdi maddesi ciktidaki bir maddeyle bu kadar (cift yonlu) benzesmeli: yoksa dusmus sayilir


def _kok_kumesi(s):
    return set(re.findall(r"[a-z0-9]+", _sade(s)))


def _karsilanmayanlar(girdi, cikti):
    """girdi (listeleri_tekille sonucu) karar / aksiyon maddelerinden ciktida KARSILIGI olmayanlar ->
    {'kararlar': [...], 'aksiyonlar': [...]}. Karsilik: kararlar + aksiyon maddelerinden biri (karar <-> aksiyon tasimasi
    serbest) girdinin sayilarini tasiyor VE cift_yonlu_benzerlik >= LISTE_KAPSAMA_ESIK (kisaltma / birlestirme sayilir)."""
    hedef = [(m, _sayilar(m), _kok_kumesi(m)) for m in
             list(cikti.get("kararlar") or []) + [a.get("madde", "") for a in cikti.get("aksiyonlar") or []]]

    def karsilandi(x, hedef=hedef):
        sx, kx = _sayilar(x), _kok_kumesi(x)
        for m, sm, km in hedef:
            if not sx <= sm:
                continue
            if kx and km and len(kx & km) < 0.4 * min(len(kx), len(km)):
                continue                                       # ucuz on eleme (difflib pahali)
            if cift_yonlu_benzerlik(x, m) >= LISTE_KAPSAMA_ESIK:
                return True
        return False
    sonuc = {"kararlar": [k for k in girdi.get("kararlar") or [] if not karsilandi(k)],
             "aksiyonlar": [a for a in girdi.get("aksiyonlar") or [] if not karsilandi(a.get("madde", ""))]}
    for alan in _sablon_alanlari_var([girdi]):       # sablon listeleri: karsilik yalniz kendi listesinde aranir
        tur = ALAN_TURU[alan]
        kendi = [(m, _sayilar(m), _kok_kumesi(m)) for m in (alan_metni(x, tur) for x in cikti.get(alan) or [])]
        sonuc[alan] = [x for x in girdi.get(alan) or [] if not karsilandi(alan_metni(x, tur), kendi)]
    return sonuc


def _liste_sayisi(v):
    return len(v.get("kararlar") or []) + len(v.get("aksiyonlar") or [])


def _sablon_sayisi(v):
    return sum(len(v.get(a) or []) for a in ALAN_TURU)


def _etiket_sil(x, tur):
    """LLM cevabindaki '[B3]' onekini atar (sablon ogesinde metin alanindan)."""
    if tur == "metin":
        return re.sub(r"^\[B\d+\]\s*", "", x)
    anahtar = "secenek" if tur == "secenek" else "fikir"
    return dict(x, **{anahtar: re.sub(r"^\[B\d+\]\s*", "", x.get(anahtar, ""))})


def _liste_grup_birlestir(bolumler, baglam, ilerleme, ilk_no=1):
    """Tek istekte sigan bolum listelerini LLM ile tekillestirir. LLM cevabi kesik, bozuk, Ingilizce ya da
    supheli derecede kisaysa (madde kaybi) bu grup icin LLM'siz tekillestirme kullanilir (nedeni loglanir)."""
    yedek = listeleri_tekille(bolumler)
    girdi, adet = _liste_girdisi(bolumler, ilk_no)
    if adet <= 3:                                      # birlestirecek bir sey yok
        return yedek
    sablon = _alan_sablonu(bolumler)
    mesajlar = [{"role": "system", "content": liste_sistemi(sablon)},
                {"role": "user", "content": f"{baglam}\n\nBölümlerden gelen maddeler:\n\n{girdi}"}]
    cikti = min(butce("liste"), max(1000, int(token_say(girdi) * 1.3)))
    if _giris_token(mesajlar) + cikti + MARJ > BAGLAM_PENCERESI:
        _liste_yedege_dus(ilerleme, "  madde listesi bağlama sığmıyor, yerel birleştirme kullanıldı", madde=adet)
        return yedek
    try:
        v = _json_tam(mesajlar, cikti, liste_semasi(sablon))
    except Exception as e:
        _liste_yedege_dus(ilerleme, f"  ! liste birleştirme hatası, yerel birleştirme kullanıldı: {hata_metni(e)}",
                          madde=adet)
        return yedek
    if not v:
        _liste_yedege_dus(ilerleme, "  ! liste birleştirme kesildi/bozuk, yerel birleştirme kullanıldı", madde=adet)
        return yedek
    ham_aks = [a for a in (v.get("aksiyonlar") or []) if a]
    bos_madde = sum(1 for a in ham_aks if not madde_dolu(a.get("madde") if isinstance(a, dict) else a))
    if ham_aks and bos_madde / len(ham_aks) > 0.2:
        _liste_yedege_dus(ilerleme, f"  ! liste birleştirmesinde {bos_madde} aksiyonun maddesi boş, "
                                    f"yerel birleştirme kullanıldı", madde=adet)
        return yedek
    n = normalize_ozet({"ozet": "", **v})
    sonuc = {k: n[k] for k in ("kararlar", "aksiyonlar", "acik_sorular")}
    for alan in ("kararlar", "acik_sorular"):
        sonuc[alan] = [re.sub(r"^\[B\d+\]\s*", "", x) for x in sonuc[alan]]
    for a in sonuc["aksiyonlar"]:
        a["madde"] = re.sub(r"^\[B\d+\]\s*", "", a["madde"])
    for alan in _sablon_alanlari_var([yedek]):
        # cevapta olmayan sablon listesi yerel birlestirmeden alinir (bos donen liste kapsama denetiminde geri eklenir)
        sonuc[alan] = (listeleri_tekille([{alan: [_etiket_sil(x, ALAN_TURU[alan]) for x in n[alan]]}])[alan]
                       if alan in n else [dict(x) if isinstance(x, dict) else x for x in yedek[alan]])
    # karar->aksiyon tasimasi olabilecegi icin kayip iki listenin TOPLAMINDAN olculur; yalniz DOLU maddeler
    # sayilir (bos maddeli aksiyonlar normalize_ozet'te atildi, eskiden 'var' sayilip yedege dusulmuyordu)
    kayip = (len(sonuc["kararlar"]) + len(sonuc["aksiyonlar"])
             < (len(yedek["kararlar"]) + len(yedek["aksiyonlar"]) + 1) // 2)
    if kayip or ingilizce_mi(_liste_dili(sonuc)):
        _liste_yedege_dus(ilerleme, "  ! liste birleştirmesi madde kaybetti ya da dili bozuk, yerel birleştirme "
                                    "kullanıldı", madde=adet)
        return yedek
    # kapsama: esigin altindaki kayip da kademeler boyunca birikir (her grupta %40 -> iki turda %64): girdideki her madde
    # ciktida karsilanmali; karsilanmayan yerel haliyle geri eklenir (loglu)
    eksik = _karsilanmayanlar(yedek, sonuc)
    n_eksik = _liste_sayisi(eksik) + _sablon_sayisi(eksik)
    if n_eksik:
        sonuc["kararlar"] += eksik["kararlar"]
        sonuc["aksiyonlar"] += [dict(a) for a in eksik["aksiyonlar"]]
        for alan in _sablon_alanlari_var([eksik]):
            sonuc[alan] += [dict(x) if isinstance(x, dict) else x for x in eksik[alan]]
        ilerleme(f"  ! liste birleştirmesi {n_eksik} maddeyi düşürdü; maddeler geri eklendi")
        _gunluge_yaz(olay="liste_geri_ekle", zaman=dt.datetime.now().strftime("%H:%M:%S"), madde=adet, eksik=n_eksik,
                     ornek=[_kisalt(x, 80) for x in (eksik["kararlar"] + [a.get("madde", "") for a in eksik["aksiyonlar"]])[:3]])
    if yedek["acik_sorular"] and not sonuc["acik_sorular"]:
        # model acik sorulari bosaltti: yerel liste kullanilir (cevaplanmislari acik_sorulari_temizle sonra ayiklar)
        sonuc["acik_sorular"] = list(yedek["acik_sorular"])
        ilerleme(f"  ! liste birleştirmesi {len(yedek['acik_sorular'])} açık sorunun tümünü düşürdü; yerel liste "
                 f"kullanıldı")
        _gunluge_yaz(olay="liste_soru_geri", zaman=dt.datetime.now().strftime("%H:%M:%S"), madde=adet,
                     soru=len(yedek["acik_sorular"]))
    return sonuc


def _liste_bos(alanlar=()):
    return dict({"kararlar": [], "aksiyonlar": [], "acik_sorular": []}, **{a: [] for a in alanlar})


def _liste_ekle(hedef, v):
    for alan in ("kararlar", "aksiyonlar", "acik_sorular") + tuple(_sablon_alanlari_var([v])):
        hedef.setdefault(alan, [])
        hedef[alan] += list(v.get(alan) or [])


def _liste_kuyruk_ayir(v, tavan):
    """Birlesmis listeyi (sabit, kuyruk) ikiye boler: kuyruk her alanin SON maddeleri (kronoloji korunur), girdi tokeni
    tavan'i asmaz. Sigan kuyruk yoksa (sabit=v, kuyruk=None)."""
    toplam = token_say(_liste_girdisi([v])[0])
    oran = min(1.0, tavan / toplam) if toplam else 0.0
    ek = tuple(_sablon_alanlari_var([v]))
    while oran > 0.02:
        kuyruk, sabit = _liste_bos(ek), _liste_bos(ek)
        for alan in ("kararlar", "aksiyonlar", "acik_sorular") + ek:
            x = list(v.get(alan) or [])
            k = len(x) - int(len(x) * (1 - oran))          # en az bir madde (liste bos degilse)
            sabit[alan], kuyruk[alan] = x[:len(x) - k], x[len(x) - k:]
        if (_liste_sayisi(kuyruk) or _sablon_sayisi(kuyruk)) and token_say(_liste_girdisi([kuyruk])[0]) <= tavan:
            return sabit, kuyruk
        oran *= 0.8
    return v, None


def _liste_sinir_birlestir(seviye, baglam, ilerleme):
    """Grup sonuclari ikiser bile tek istege sigmiyor (toplam madde tek istek kapasitesini asiyor, model maddeleri
    azaltamiyor): her sonraki grup, oncekinin KUYRUGUYLA (kronolojik sinir: bitisik bolumlerin tekrarlari burada olur)
    birlikte LLM'e verilir; kuyrugun oncesi oldugu gibi kalir. Kucuk kalan grup yerel birlestirmeye dusmez, sinirdaki
    tekrarlar yine LLM ile birlesir (MIMARI_v3 §6 kademeli birlestirme). Dondurur: birlesik liste."""
    ust = _liste_girdi_ust(baglam, _alan_sablonu(seviye))
    sonuc, tasinan = _liste_bos(_sablon_alanlari_var(seviye)), seviye[0]
    _gunluge_yaz(olay="liste_sinir", zaman=dt.datetime.now().strftime("%H:%M:%S"), grup=len(seviye),
                 madde=_liste_girdisi(seviye)[1])
    for v in seviye[1:]:
        yer = min(ust - token_say(_liste_girdisi([v])[0]), ust // 2)
        sabit, kuyruk = _liste_kuyruk_ayir(tasinan, yer) if yer > 0 else (tasinan, None)
        _liste_ekle(sonuc, sabit)
        if kuyruk is None:                              # sonraki grup tek basina dolu: sinir birlestirmesi yok
            tasinan = v
            continue
        tasinan = _liste_grup_birlestir([kuyruk, v], baglam, ilerleme)
    _liste_ekle(sonuc, tasinan)
    return sonuc


def _listeleri_birlestir(bolumler, baglam, ilerleme=print):
    """Karar/aksiyon/acik soru listelerini tekillestirir. Liste tek istege sigiyorsa tek LLM cagrisi; uzun toplantida
    (170+ madde) bolumler sigan gruplara bolunur, her grup ayri birlestirilir, ardindan grup sonuclari yeniden
    birlestirilir (en cok LISTE_KADEME tur; ust turlarda tek basina kalan grup sonucu yeniden istenmez). Toplam tek istek
    kapasitesini asiyorsa (gruplar kuculmuyor) son basamak sinir birlestirmesidir (_liste_sinir_birlestir). LLM'siz
    yedege dusus YALNIZ sorunlu grup ya da tek bolumu bile sigmayan liste icin olur ve her zaman loglanir."""
    _, adet = _liste_girdisi(bolumler)
    if adet <= 3:                                      # birlestirecek bir sey yok
        return listeleri_tekille(bolumler)
    seviye = list(bolumler)
    for tur in range(LISTE_KADEME):
        gruplar = _liste_gruplari(seviye, baglam, LISTE_GRUP_MADDE if tur == 0 else 0)
        if len(gruplar) == 1:
            return _liste_grup_birlestir(seviye, baglam, ilerleme)
        if len(gruplar) >= len(seviye):                # her eleman tek basina bir grup: gruplamak ilerleme saglamaz
            if tur:
                ilerleme(f"  {_liste_girdisi(seviye)[1]} madde tek istekte sığmıyor: {len(seviye)} grup sonucu "
                         f"sınırlarından birleştiriliyor...")
                return _liste_sinir_birlestir(seviye, baglam, ilerleme)
            break
        ilerleme(f"  {_liste_girdisi(seviye)[1]} madde tek istekte sığmıyor: {len(gruplar)} grupta birleştiriliyor"
                 f"{f' (tur {tur + 1})' if tur else ''}...")
        # ust turda tek elemanli grup zaten birlesmis bir grup sonucudur: ayni listeyi yeniden istemek bosa cagri
        seviye = [g[0][1] if tur and len(g) == 1 else _liste_grup_birlestir([b for _, b in g], baglam, ilerleme,
                                                                              g[0][0] + 1)
                  for g in gruplar]
    else:
        ilerleme(f"  {_liste_girdisi(seviye)[1]} madde {LISTE_KADEME} turda tek isteğe inmedi: {len(seviye)} grup "
                 f"sonucu sınırlarından birleştiriliyor...")
        return _liste_sinir_birlestir(seviye, baglam, ilerleme)
    _liste_yedege_dus(ilerleme, "  ! madde listesi kademeli birleştirmeye de sığmadı, yerel birleştirme kullanıldı",
                      madde=_liste_girdisi(seviye)[1])
    return listeleri_tekille(seviye)


def listeleri_birlestir(bolumler, baglam, ilerleme=print):
    """Karar/aksiyon/acik soru listelerini tekillestirir (_listeleri_birlestir). Kayip TOPLAM uzerinden de denetlenir:
    son sonuc ilk girdinin yerel tekillestirmesinin yarisindan azsa (kademeler boyunca biriken kayip) ya da girdide
    acik soru varken hic kalmadiysa yerel sonuc kullanilir (loglu)."""
    sonuc = _listeleri_birlestir(bolumler, baglam, ilerleme)
    if _liste_girdisi(bolumler)[1] <= 3:
        return sonuc
    taban = listeleri_tekille(bolumler)
    if _liste_sayisi(sonuc) < (_liste_sayisi(taban) + 1) // 2:
        _liste_yedege_dus(ilerleme, f"  ! birleştirme toplamda madde kaybetti ({_liste_sayisi(sonuc)} / "
                                    f"{_liste_sayisi(taban)}), yerel birleştirme kullanıldı",
                          madde=_liste_girdisi(bolumler)[1])
        return taban
    for alan in _sablon_alanlari_var([taban]):
        if alan not in sonuc:                          # sablon listesi bir basamakta kaybolduysa yerel liste
            sonuc = dict(sonuc, **{alan: list(taban[alan])})
    if taban["acik_sorular"] and not sonuc["acik_sorular"]:
        sonuc = dict(sonuc, acik_sorular=list(taban["acik_sorular"]))
        ilerleme(f"  ! birleştirmede {len(taban['acik_sorular'])} açık sorunun tümü düştü; yerel liste kullanıldı")
        _gunluge_yaz(olay="liste_soru_geri", zaman=dt.datetime.now().strftime("%H:%M:%S"),
                     soru=len(taban["acik_sorular"]))
    return sonuc


def _genel_sor(ozetler, liste_metni, baglam, cikti=None):
    cikti = cikti or butce("genel")
    icerik = ("Bölüm özetleri (kronolojik):\n" + "\n".join(ozetler)
              + "\n\nBirleştirilmiş kararlar ve aksiyonlar:\n" + (liste_metni or "-"))
    kullanici = {"role": "user", "content": f"{baglam}\n\n{icerik}"}
    for sistem in (GENEL_SISTEM, GENEL_SISTEM + "\n" + TURKCE_KURAL):
        v = _json_tam([{"role": "system", "content": sistem}, kullanici], cikti, GENEL_SEMASI, temperature=0.3)
        ozet = normalize_bosluk(_metin((v or {}).get("ozet")))
        sonraki = normalize_bosluk(_metin((v or {}).get("sonraki_adim"))) or "-"
        if len(ozet.strip(" -.")) >= 40 and not ingilizce_mi(f"{ozet} {sonraki}"):   # '-' ya da tek kelime ozet degildir
            return {"ozet": ozet, "sonraki_adim": sonraki}
    return None


def _grupla(metinler, butce):
    gruplar, mevcut, tok = [], [], 0
    for m in metinler:
        t = token_say(m)
        if mevcut and tok + t > butce:
            gruplar.append(mevcut)
            mevcut, tok = [], 0
        mevcut.append(m)
        tok += t
    if mevcut:
        gruplar.append(mevcut)
    return gruplar


def genel_ozet(bolumler, listeler, baglam, ilerleme=print):
    """Ozet paragrafi + sonraki adim. Bolum ozetleri baglama sigmazsa once gruplar halinde ara ozet
    cikarilir (hiyerarsik). Basarisizsa bolum ozetleri sirayla birlestirilir (LLM'siz yedek)."""
    ozetler = [f"Bölüm {i} ({b.get('aralik') or '-'}): {b['ozet']}" for i, b in enumerate(bolumler, 1) if b.get("ozet")]
    yedek = {"ozet": yedek_ozet(bolumler, listeler), "sonraki_adim": "-"}
    if not ozetler:
        ilerleme("  ! bölüm özetleri boş; özet konu ve kararlardan kuruldu")
        return yedek
    liste_metni = "\n".join([f"- Karar: {k}" for k in listeler["kararlar"]]
                            + [f"- Aksiyon: {a['madde']} ({a['sorumlu']}, {a['tarih']})" for a in listeler["aksiyonlar"]]
                            + _sablon_liste_metni(listeler))
    cikti = butce("genel")
    sigan = BAGLAM_PENCERESI - MARJ - cikti - token_say(GENEL_SISTEM + TURKCE_KURAL + baglam + liste_metni) - 200
    for tur in range(3):
        if len(ozetler) <= 1 or token_say("\n".join(ozetler)) <= sigan:
            break
        gruplar = _grupla(ozetler, sigan)
        if len(gruplar) >= len(ozetler):
            break
        ilerleme(f"  özetler bağlama sığmıyor: {len(gruplar)} grupta ara özet çıkarılıyor...")
        yeni = []
        for n, g in enumerate(gruplar, 1):
            try:
                v = _genel_sor(g, "-", baglam, cikti)
            except Exception as e:             # ara ozet yoksa grubun ozetleri oldugu gibi kalir
                ilerleme(f"  ! ara özet üretilemedi: {hata_metni(e)}")
                v = None
            yeni.append(f"Ara özet {n}: {v['ozet']}" if v else " ".join(g))
        ozetler = yeni
    try:
        v = _genel_sor(ozetler, liste_metni, baglam, cikti)
    except Exception as e:
        ilerleme(f"  ! özet paragrafı üretilemedi: {hata_metni(e)}")
        v = None
    if not v:
        ilerleme("  ! özet paragrafı kesildi ya da Türkçe değildi; bölüm özetleri sırayla kullanıldı")
        return yedek
    return v


SABLON_OZET_TAVAN = 24          # ozet istegine giden sablon maddesi (girdi butcesi; tumu notta ayrica listelenir)


def _sablon_liste_metni(listeler):
    """Ozet paragrafi girdisi: sablon listeleri kisa satirlar ('- Fikir: ...'); yalniz bilgi, sonraki adima kaynak degil."""
    satirlar = []
    for alan, baslik, tur in _TUM_ALANLAR:
        for x in listeler.get(alan) or []:
            if tur == "secenek":
                durum = {True: " (seçildi)", "belirsiz": " (seçildiği kesin değil)"}.get(x.get("secildi"), "")
                satirlar.append(f"- Seçenek: {x.get('secenek', '')}{durum}")
            elif tur == "fikir":
                satirlar.append(f"- Fikir ({x.get('tema') or 'Diğer'}): {x.get('fikir', '')}")
            else:
                satirlar.append(f"- {baslik}: {x}")
    return satirlar[:SABLON_OZET_TAVAN]


def yedek_ozet(bolumler, listeler):
    """LLM'siz ozet: bolum ozetleri; onlar da bossa konu basliklari ve kararlardan kurulan cumleler.
    Not hicbir zaman '## Ozet\n-' ile cikmaz."""
    ozet = " ".join(b["ozet"] for b in bolumler if b.get("ozet"))
    if ozet:
        return ozet
    konular = []
    for b in bolumler:
        for k in b.get("konular") or []:
            if not any(_benzer(k, x) for x in konular):
                konular.append(k)
    cumleler = []
    if konular:
        cumleler.append("Toplantıda ele alınan konular: " + "; ".join(konular[:10]) + ".")
    if listeler.get("kararlar"):
        cumleler.append("Öne çıkan kararlar: " + "; ".join(k.rstrip(".") for k in listeler["kararlar"][:3]) + ".")
    return " ".join(cumleler) or "-"


def yedek_sonraki_adim(listeler, adet=3):
    """Model 'sonraki adim' yazmadiysa aksiyonlardan (sorumlusu belli olanlar once) kurulur."""
    aks = sorted(listeler.get("aksiyonlar") or [],
                 key=lambda a: a.get("sorumlu") in ("", "belirsiz", None) or "(?)" in str(a.get("sorumlu")))
    if not aks:
        return "-"
    return "Öncelikli aksiyonlar: " + "; ".join(
        f"{a['madde'].rstrip('.')} ({a.get('sorumlu') or 'belirsiz'})" for a in aks[:adet]) + "."


def konu_akisi(bolumler):
    """Bolum araliklari ve konu basliklari: toplantinin zaman cizelgesi (LLM'siz, kaynaktan)."""
    satirlar = []
    for b in bolumler:
        if b.get("konular"):
            satirlar.append(f"- {b.get('aralik') or '-'}: " + "; ".join(b["konular"]))
    return "\n".join(satirlar)


def _hucre(x):
    return normalize_bosluk(x).replace("|", "/") or "-"


KAYNAK_ISARETI = "⏱"         # "⏱10:12:03": notta kaynak zamani; arayuz tiklaninca transkripti o anda acar

SABLONLAR = {
    "genel": ("Genel", ""),
    "haftalik": ("Haftalık durum", "Özet paragrafını ilerleme, engeller ve bir sonraki dönem planı ekseninde yaz."),
    "karar": ("Karar toplantısı", "Özet paragrafında hangi seçeneklerin konuşulduğunu ve kararların gerekçesini öne çıkar."),
    "birebir": ("Birebir (1:1)", "Özet kısa olsun; konuşulan konuları, geri bildirimleri ve varılan anlaşmaları yaz."),
    "calistay": ("Çalıştay / beyin fırtınası", "Özet paragrafında ortaya atılan fikirleri ve öne çıkanları grupla; "
                                               "fikirleri karar gibi yazma."),
}


def kisi_bazli(aksiyonlar):
    """Aksiyonlari sorumluya gore gruplar: [(ad, [madde, ...])], en cok isi olan once, 'belirsiz' sonda.
    'Selin Kara, Kemal Tuncer' gibi ortak sorumlu her iki kisiye de yazilir. Sorumlusu '(?)' isaretli (sahibi
    belirsiz) aksiyon kisinin isi sayilmaz: 'belirsiz' grubuna '(aday: Ad)' notuyla girer."""
    gruplar = {}
    for a in aksiyonlar:
        if not madde_dolu(a.get("madde")):
            continue
        ham = a.get("sorumlu") or ""
        adlar = [x.strip() for x in re.split(r",|/| ve ", re.sub(r"\(\?\)", " ", ham)) if x.strip()] or ["belirsiz"]
        madde = a["madde"]
        if "(?)" in ham and adlar != ["belirsiz"]:
            madde = f"{madde.rstrip()} (aday: {', '.join(adlar)})"
            adlar = ["belirsiz"]
        for ad in adlar:
            gruplar.setdefault(ad, [])
            if madde not in gruplar[ad]:
                gruplar[ad].append(madde)
    return sorted(gruplar.items(), key=lambda kv: (kv[0] == "belirsiz", -len(kv[1]), kv[0]))


KISIYE_BASLIK = "## Kişiye göre iş listesi"


def kisiye_gore_satirlari(aksiyonlar):
    """'Kişiye göre iş listesi' bolumunun madde satirlari (kisi_bazli): en az 4 aksiyon ve 2 grup yoksa []."""
    gruplar = kisi_bazli(aksiyonlar)
    if len(aksiyonlar) < 4 or len(gruplar) < 2:
        return []
    return [f"- {ad} ({len(isler)}): " + "; ".join(normalize_bosluk(x).rstrip(".") for x in isler)
            for ad, isler in gruplar]


_TUM_ALANLAR = tuple((a, b, t) for v in SABLON_ALANLARI.values() for a, b, t in v)


def sablon_bolumleri_md(listeler, kaynak=None):
    """Sablon ek bolumlerinin Markdown satirlari (yalniz dolu olanlar: bos bolum yazilmaz). kaynak(metin) -> '⏱..' | ''.
    Secenek: '- Seçilen: X' (dogrulanmis), '- Seçilen (?): X' (model secildi dedi, kararlarda karsiligi yok), '- X'; artı /
    eksi alt maddeler. Fikirler temaya gore gruplanir ('Tema:' satiri + maddeler)."""
    kaynak = kaynak or (lambda m: "")

    def madde(x, on="- "):
        return f"{on}{normalize_bosluk(x)}" + (f" {kaynak(x)}".rstrip())
    parcalar = []
    for alan, baslik, tur in _TUM_ALANLAR:
        ogeler = listeler.get(alan) or []
        if not ogeler:
            continue
        satirlar = []
        if tur == "metin":
            satirlar = [madde(x) for x in ogeler]
        elif tur == "secenek":
            for x in ogeler:
                on = {True: "Seçilen: ", "belirsiz": "Seçilen (?): "}.get(x.get("secildi"), "")
                satirlar.append(f"- {on}{normalize_bosluk(x['secenek'])}" + f" {kaynak(x['secenek'])}".rstrip())
                satirlar += [madde(a, "  - Artı: ") for a in x.get("artilar") or []]
                satirlar += [madde(e, "  - Eksi: ") for e in x.get("eksiler") or []]
        else:
            temalar = {}
            for x in ogeler:
                temalar.setdefault(normalize_bosluk(x.get("tema")) or "Diğer", []).append(x["fikir"])
            for tema, fikirler in temalar.items():
                satirlar.append(f"{tema}:")
                satirlar += [madde(f) for f in fikirler]
        parcalar += [f"## {baslik}"] + satirlar + [""]
    return parcalar


def sablon_bolumlerini_temizle(not_md):
    """Denetimlerden (dayanak / son denetim) SONRA: maddesi kalmamis sablon bolumu ve maddesiz tema satiri notta yer almaz.
    Ortak bolumlere (Kararlar '-' vb.) dokunulmaz."""
    satirlar = not_md.split("\n")
    cikti, i = [], 0
    while i < len(satirlar):
        s = satirlar[i].strip()
        if not (s.startswith("## ") and sablon_bolumu_mu(s)):
            cikti.append(satirlar[i])
            i += 1
            continue
        j = i + 1
        while j < len(satirlar) and not satirlar[j].strip().startswith("#"):
            j += 1
        govde = satirlar[i + 1:j]
        tutulan = []
        for k, x in enumerate(govde):
            xs = x.strip()
            if xs and not xs.startswith(("-", "*")):           # tema satiri: altinda madde yoksa duser
                sonraki = next((y.strip() for y in govde[k + 1:] if y.strip()), "")
                if not sonraki.startswith(("-", "*")):
                    continue
            tutulan.append(x)
        if any(x.strip().startswith(("-", "*")) and x.strip().strip("-* ") for x in tutulan):
            cikti += [satirlar[i]] + tutulan
        else:                                                   # bolum bos: basligiyla birlikte (onceki bos satir dahil)
            while cikti and not cikti[-1].strip():
                cikti.pop()
            if j < len(satirlar) and cikti:
                cikti.append("")
        i = j
    return "\n".join(cikti)


def not_markdown(genel, listeler, kaynak_fn=None):
    """Notun Markdown'u kodda kurulur: basliklar sabit ve Turkce, tablo hic yarim kalmaz.
    kaynak_fn(metin, sorumlu) -> 'HH:MM:SS' | None: maddenin transkriptte gectigi an. Sablon ek bolumleri (listelerde
    varsa ve doluysa) konu akisindan sonra, kararlardan once."""
    def kaynak(metin, sorumlu=None):
        ts = kaynak_fn(metin, sorumlu) if kaynak_fn else None
        return f"{KAYNAK_ISARETI}{ts}" if ts else ""

    def maddeler(lst, isaret=False):
        return "\n".join(f"- {normalize_bosluk(x)}" + (f" {kaynak(x)}".rstrip() if isaret else "")
                         for x in lst) or "-"
    parcalar = ["## Özet", genel["ozet"] or "-", ""]
    if genel.get("konu_akisi"):
        parcalar += ["## Konu akışı", genel["konu_akisi"], ""]
    parcalar += sablon_bolumleri_md(listeler, kaynak)
    parcalar += ["## Kararlar", maddeler(listeler["kararlar"], isaret=True), "", "## Aksiyonlar"]
    if listeler["aksiyonlar"]:
        basliklar = "| # | Madde | Sorumlu | Tarih |" + (" Kaynak |" if kaynak_fn else "")
        parcalar += [basliklar, "|---|---|---|---|" + ("---|" if kaynak_fn else "")]
        for i, a in enumerate(listeler["aksiyonlar"], 1):
            satir = f"| {i} | {_hucre(a['madde'])} | {_hucre(a['sorumlu'])} | {_hucre(a['tarih'])} |"
            if kaynak_fn:
                satir += f" {kaynak(a['madde'], a['sorumlu']) or '-'} |"
            parcalar.append(satir)
        kisiye = kisiye_gore_satirlari(listeler["aksiyonlar"])
        if kisiye:
            # 'aksiyon/karar/soru' kelimesi gecmeyen baslik: dayanak kontrolu bu satirlari denetlemez; motor dayanak
            # kontrolunden SONRA bolumu son aksiyon tablosundan yeniden kurar (not_araclari.kisiye_gore_yenile)
            parcalar += ["", KISIYE_BASLIK] + kisiye
    else:
        parcalar.append("-")
    parcalar += ["", "## Açık sorular", maddeler(listeler["acik_sorular"]), "",
                 "## Bir sonraki adım", genel.get("sonraki_adim") or "-"]
    return "\n".join(parcalar) + "\n"


# ---------- not kalitesi: deterministik son islemler ----------
# Karar/aksiyon ayrimi, bos maddeler ve gurultulu acik sorular yalnizca prompt'a birakilinca notta kaliyordu
# (2026-09-23 notu: kararlarda aksiyonlar, 4-15. aksiyonlarin maddesi '-', 19 acik soru). Bunlar LLM'siz.

_GELECEK_FIIL = re.compile(r"[ae]c[ae]k(?:t[ıi]r|l[ae]r(?:d[ıi]r)?)?$")     # -ecek/-acak(tır), paylaşılacak...
_SON_PARANTEZ = re.compile(r"\s*\([^()]*\)\s*\.?\s*$")
_SON_BELIRSIZ = re.compile(r"\(\?\)\s*\.?\s*$")                                    # sondaki '(?)' belirsizlik isareti
_TEK_AD_PARANTEZ = re.compile(r"\s*\((\w+)\)\s*(\.?)\s*$")                  # "... (Selin)" / "... (Selin)."
_ALINTI_PARANTEZ = re.compile(r"\s*\([^()]*['\"“”‘’][^()]*\)\s*\.?\s*$")   # "... (Kemal: '...')"
# Modelin kendi anlama sorusu: kelimeye BITISIK '(?)' + kim/nedir. Kodun bastigi ' (?)' (bosluklu, madde sonunda)
# belirsizlik isaretidir ve soruyu silmez (MIMARI_v3 §6).
_ANLAMA_ISARETI = re.compile(r"\w\(\?\)\s*(?:kimdir|kim|kimin|nedir|hangi|ne olduğu)(?!\w)")
_ANLAMA_SORUSU = ("kimdir", "hangi sistemdir", "katılımcı listesinde", "katılımcılar arasında",
                  "ne olduğu belirsiz", "ne olduğu anlaşılmadı")
_BELIRSIZ = ("", "-", "belirsiz", "?")


def _gecen_adlar(metin, katilimcilar):
    """Metinde (ad ya da ad+soyad olarak) gecen katilimcilar, listedeki yazimla."""
    adlar = []
    for ad in katilimcilar or []:
        ad = normalize_bosluk(ad)
        if not ad or ad in _BELIRSIZ or tr_kucuk(ad) == "ben":
            continue
        if (ad_geciyor(ad, metin) or ad_geciyor(ad.split()[0], metin)) and ad not in adlar:
            adlar.append(ad)
    return adlar


def karar_aksiyon_ayikla(listeler, katilimcilar=None):
    """(a) bir aksiyona benzeyen karar (kelime koku benzerligi >= 0.6) kararlardan duser: ayni is iki yerde
    yazilmasin. (b) metninde bir katilimci adi gecen VE gelecek zaman/edilgen is fiiliyle biten karar
    ('Girdi tablolari Kemal tarafindan paylasilacak') bir istir: aksiyonlara o kisiyle tasinir.
    Sondaki '(Kemal onerdi, Selin onayladi)' gibi karar vereni gosteren parantez ad olarak sayilmaz."""
    aksiyonlar = [dict(a) for a in listeler.get("aksiyonlar") or []]
    kararlar = []
    for k in listeler.get("kararlar") or []:
        if any(cift_yonlu_benzerlik(k, a.get("madde", "")) >= 0.6 for a in aksiyonlar):
            continue
        govde = _SON_PARANTEZ.sub("", k).strip()
        adlar = _gecen_adlar(govde, katilimcilar)
        kelimeler = re.findall(r"\w+", tr_kucuk(govde))
        if adlar and kelimeler and _GELECEK_FIIL.search(kelimeler[-1]):
            isaret = " (?)" if _SON_BELIRSIZ.search(k) else ""        # kodun/girdinin (?) isareti tasinirken kaybolmaz
            aksiyonlar.append({"madde": govde.rstrip(" .") + isaret, "sorumlu": ", ".join(adlar), "tarih": "-"})
            continue
        kararlar.append(k)
    return dict(listeler, kararlar=kararlar, aksiyonlar=aksiyonlar)


def karar_parantez_temizle(kararlar):
    """Karar sonundaki tek kelimelik '(Selin)' parantezi anlamsiz: kaldirilir. '(?)' belirsizlik isareti kalir."""
    return [_TEK_AD_PARANTEZ.sub(r"\2", k).strip() or k for k in kararlar]


def belirsiz_aksiyonlari_ele(aksiyonlar, kaynak_fn=None):
    """Sorumlusu belirsiz, en fazla 4 kelimelik ve transkriptte kaynak satiri bulunamayan aksiyon
    ('Gereksinimleri anlamak') is degil genel ifadedir: duser. Kaynak bulucu yoksa dokunulmaz."""
    if not kaynak_fn:
        return list(aksiyonlar)
    kalan = []
    for a in aksiyonlar:
        madde = a.get("madde") or ""
        if (normalize_bosluk(a.get("sorumlu")).lower() in _BELIRSIZ and len(madde.split()) <= 4
                and not kaynak_fn(madde, None)):
            continue
        kalan.append(a)
    return kalan


def _soru_govdesi(q):
    """Tekrar karsilastirmasi icin soru ekleri ('mi', 'var mi') atilir; iki soru bunlarla benzer gorunmesin
    ya da bunlar yuzunden ayri kalmasin."""
    return re.sub(r"(?<!\w)(mi|mı|mu|mü|var|yok|acaba)(?!\w)", " ", tr_kucuk(q))


def acik_sorulari_temizle(sorular, kararlar=(), aksiyonlar=(), sonraki_adim="", en_fazla=10):
    """(a) modelin kendi anlama sorulari ('X kimdir?', 'katilimci listesinde gecmiyor') atilir; (b) sondaki
    alinti parantezi kirpilir; (c) birbirine benzeyenlerden (>= 0.6) ilki tutulur; (d) bir karar, aksiyon ya
    da sonraki adimla cevaplanmis olan (>= 0.5) atilir; (e) en fazla `en_fazla` soru."""
    cevaplar = [x for x in list(kararlar) + [a.get("madde", "") for a in aksiyonlar] if x]
    cevaplar += [c for c in re.split(r"[.;]\s+", sonraki_adim or "") if c.strip(" -.")]
    tutulan = []
    for q in sorular:
        q = normalize_bosluk(q)
        k = tr_kucuk(q)
        if any(i in k for i in _ANLAMA_SORUSU):
            continue
        if _ANLAMA_ISARETI.search(k):
            continue                                  # 'Varun(?) kimdir' gibi modelin kendi anlama sorusu
        q = _ALINTI_PARANTEZ.sub("", q).strip()
        if len(q.strip(" -.?")) < 3:
            continue
        if any(cift_yonlu_benzerlik(_soru_govdesi(q), _soru_govdesi(t)) >= 0.6 for t in tutulan):
            continue
        if any(cift_yonlu_benzerlik(q, c) >= 0.5 for c in cevaplar):
            continue
        tutulan.append(q)
    return tutulan[:en_fazla]


def listeleri_temizle(listeler, katilimcilar=None, kaynak_fn=None):
    """Birlestirilmis listelere karar/aksiyon ayrimi, karar parantezi ve belirsiz aksiyon temizligi."""
    v = karar_aksiyon_ayikla(listeler, katilimcilar)
    v["kararlar"] = karar_parantez_temizle(v["kararlar"])
    v["aksiyonlar"] = belirsiz_aksiyonlari_ele([a for a in v["aksiyonlar"] if madde_dolu(a.get("madde"))], kaynak_fn)
    return v


def secenekleri_dogrula(listeler):
    """Karar sablonu: model 'secildi' dediyse secim KODDA dogrulanir -- kararlar listesinde secenegin karsiligi (kok
    ortusmesi >= 0.6 ya da cift yonlu benzerlik >= 0.5) varsa secildi=True, yoksa 'belirsiz' (notta 'Seçilen (?)').
    Secilmedi denen secenek icin notta durum yazilmaz (secilmedigi de kesin degil)."""
    if not listeler.get("secenekler"):
        return listeler
    kararlar = [k for k in listeler.get("kararlar") or [] if k]
    yeni = []
    for x in listeler["secenekler"]:
        x = dict(x)
        if x.get("secildi"):
            sk = _kok_kumesi(x.get("secenek", "")) - {"ve", "ile", "icin"}
            karsilik = any((sk and len(sk & _kok_kumesi(k)) / len(sk) >= 0.6)
                           or cift_yonlu_benzerlik(x.get("secenek", ""), k) >= 0.5 for k in kararlar)
            x["secildi"] = True if karsilik else "belirsiz"
        else:
            x["secildi"] = False
        yeni.append(x)
    return dict(listeler, secenekler=yeni)


def birlestir(bolumler, baglam, ilerleme=print, kaynak_fn=None, sablon="genel", katilimcilar=None,
              aksiyon_fn=None, adim_fn=None, liste_fn=None):
    """aksiyon_fn(aksiyonlar) -> aksiyonlar: cagiranin son islemi (motor: sorumlu dogrulama).
    liste_fn(listeler) -> listeler: sablona bagli son islem (motor: calistay karar / fikir ayrimi, kaynak satirina bakar).
    adim_fn(metin): arayuzdeki ilerleme seridi icin asama bildirimi. sablon: ozet talimati + ek bolumler (yalniz bu
    sablonun alanlari notta yer alir)."""
    adim = adim_fn or (lambda m: None)
    bolumler = [b for b in bolumler if b]
    if not bolumler:
        return "_(özetlenebilen bölüm yok)_"
    sablon = sablon_adi(sablon)
    talimat = SABLONLAR[sablon][1]
    if talimat:
        baglam = f"{baglam}\nNot şablonu: {talimat}"
    adim("Kararlar ve aksiyonlar birleştiriliyor")
    ilerleme("  kararlar, aksiyonlar ve açık sorular birleştiriliyor...")
    benim = {a for a, _, _ in sablon_alanlari(sablon)}
    # baska sablonla ozetlenmis parcanin ek alanlari bu notta yer almaz
    bolumler = [{k: v for k, v in b.items() if k not in ALAN_TURU or k in benim} for b in bolumler]
    listeler = listeleri_temizle(listeleri_birlestir(bolumler, baglam, ilerleme), katilimcilar, kaynak_fn)
    for alan in benim:
        listeler.setdefault(alan, [])
    if aksiyon_fn:
        listeler["aksiyonlar"] = aksiyon_fn(listeler["aksiyonlar"])
    if liste_fn:
        listeler = liste_fn(listeler)
    listeler = secenekleri_dogrula(listeler)
    adim("Özet paragrafı yazılıyor")
    ilerleme("  özet paragrafı yazılıyor...")
    genel = genel_ozet(bolumler, listeler, baglam, ilerleme)
    if len(genel["ozet"].strip(" -.")) < 40:
        genel["ozet"] = yedek_ozet(bolumler, listeler)
    if genel.get("sonraki_adim", "-").strip(" -.") == "":
        genel["sonraki_adim"] = yedek_sonraki_adim(listeler)
    if len(bolumler) > 1:
        genel["konu_akisi"] = konu_akisi(bolumler)
    listeler["acik_sorular"] = acik_sorulari_temizle(listeler["acik_sorular"], listeler["kararlar"],
                                                     listeler["aksiyonlar"], genel.get("sonraki_adim", ""))
    return not_markdown(genel, listeler, kaynak_fn)


# ---------- toplantiya soru ----------

SORU_SISTEM = (
    "Sen bir toplantı asistanısın. Sana bir toplantının transkriptinden seçilmiş bölümler verilecek; her satır "
    "[SS:DD:ss] Konuşmacı: metin biçiminde. Kullanıcının sorusunu YALNIZCA bu satırlara dayanarak cevapla.\n"
    "- Kim ne dediyse adıyla yaz; dayandığın satırın zamanını ⏱SS:DD:ss biçiminde ekle (örn. ⏱10:12:03).\n"
    "- Etiketi 'Ad?' olan satırda konuşmacı kesin değildir ('Ad olabilir' de); 'Bilinmeyen' ise kim olduğu "
    "bilinmez: bunlara kesin kişi atama. ⟨...⟩ içindeki kelimeler belirsizdir: oradan sayı, tarih ya da karar "
    "aktarma, en fazla 'şu konu konuşuldu' de.\n"
    "- Transkriptte cevap yoksa 'Bu toplantının transkriptinde bu konu geçmiyor.' de; tahmin yürütme, genel "
    "bilgiyle doldurma.\n- Kısa ve net yaz.\n" + TURKCE_KURAL
)


KESIK_CEVAP_UYARISI = ("(Uyarı: cevap uzunluk sınırında kesildi, eksik olabilir. Soruyu daraltarak ya da parçalara "
                       "bölerek yeniden sorun.)")


def toplantiya_sor(soru, bolumler, baglam, cikti=None, sira=None):
    """bolumler: [(aralik, '[ts] Kim: metin' satirlari)], kronolojik. Soruyla en ilgili bolumler baglama
    sigdigi kadar secilir (sira verilmezse kelime koku ortakligina gore; rag.hibrit_sirala sirayi verir),
    kronolojik sirayla verilir. Girdi butcesinden ilk cagrinin GERCEK max_tokens'i (cevap + dusunme payi)
    dusulur. Dusunmesiz tekrar da 'length' ile biterse cevabin sonuna KESIK_CEVAP_UYARISI eklenir (kesik cevap tam
    cevap gibi gosterilmez)."""
    cikti = cikti or butce("soru")
    pay = butce("soru_dusunme")
    def kokler(m):
        return {k[:5] for k in re.findall(r"[a-z0-9]+", _sade(m)) if len(k) >= 3}
    sk = kokler(soru)
    if sira is not None:
        puanli = [(0, i) for i in sira if 0 <= i < len(bolumler)]
    else:
        puanli = sorted(((len(sk & kokler(metin)), i) for i, (_, metin) in enumerate(bolumler)), reverse=True)
    sigan = (min(BAGLAM_PENCERESI, SORU_GIRDI_UST) - MARJ - cikti - pay
             - token_say(SORU_SISTEM + baglam + soru) - 200)
    secilen, tok = [], 0
    for puan, i in puanli:
        t = token_say(bolumler[i][1])
        if secilen and tok + t > sigan:
            continue
        if t > sigan:                                 # tek bolum bile sigmiyorsa sonundan kirp
            secilen.append((i, bolumler[i][1][-max(300, int(sigan * 3)):]))
            break
        secilen.append((i, bolumler[i][1]))
        tok += t
    if not secilen:
        return "Bu toplantının transkripti boş."
    icerik = "\n\n".join(f"### Bölüm {i + 1} ({bolumler[i][0]})\n{metin}" for i, metin in sorted(secilen))
    mesajlar = [{"role": "system", "content": SORU_SISTEM},
                {"role": "user", "content": f"{baglam}\n\nTranskript:\n{icerik}\n\nSoru: {soru}"}]
    if pay:
        metin, neden = sor(mesajlar, cikti + pay, effort="low", temperature=0.3)
    else:
        metin, neden = "", "atlandi"                  # dar pencere: dusunmeye yer yok
    if neden in ("length", "atlandi") or not metin.strip() or ingilizce_mi(metin):
        metin, neden = sor(mesajlar, cikti, temperature=0.3, dusunme=False)
    if neden == "length":
        # dusunmesiz tekrar da uzunluk sinirina takildi: kesik cevap tam cevap gibi gosterilmez
        metin = (metin.rstrip() + "\n\n" if metin.strip() else "") + KESIK_CEVAP_UYARISI
    if len(secilen) < len(bolumler):
        metin += f"\n\n(Not: {len(bolumler)} bölümden soruyla en ilgili {len(secilen)} tanesine bakıldı.)"
    return metin


# ---------- arayuz: kisa sunucu testi ----------

def kisa_test():
    """Ayarlar -> 'LLM sunucusunu test et': dusunmesiz kisa bir sohbet (uygulamanin istemcisi, TLS ve
    proxy ayarlariyla). Dondurur (metin, basarili)."""
    mesajlar = [{"role": "system", "content": "Kısa ve Türkçe cevap ver."},
                {"role": "user", "content": "Bir toplantı notunda karar ile aksiyonun farkını tek cümleyle yaz."}]
    t0 = time.time()
    try:
        icerik, _, neden, u = _cagir(dict(model=MODEL, messages=mesajlar, max_tokens=200, temperature=0.3,
                                          top_p=0.95, extra_body={"chat_template_kwargs": {"enable_thinking": False}}))
    except Exception as e:
        return f"Sohbet denemesi başarısız: {hata_metni(e)}", False
    sure = time.time() - t0
    ct = getattr(u, "completion_tokens", None)
    hiz = f", {ct / sure:.0f} token/sn" if ct and sure > 0 else ""
    sizinti = "; düşünce metni cevaba sızıyor (sunucuda reasoning parser yok?)" if _DUSUNCE_ETIKET.search(icerik) else ""
    return (f"Sohbet denemesi: {sure:.1f} sn, finish={neden}, {ct if ct is not None else '?'} token{hiz}; "
            f"cevap Türkçe: {'HAYIR' if ingilizce_mi(icerik) else 'evet'}{sizinti}", bool(icerik.strip()))
