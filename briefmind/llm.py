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
import os
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
_cfg = {}
if os.path.exists(CONFIG):
    try:
        with open(CONFIG, encoding="utf-8") as _f:
            _cfg = json.load(_f)
    except Exception:
        _cfg = {}

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
    cfg = cfg or {}
    _AYAR_NESIL += 1
    ham = (cfg.get("route") or "http://localhost:8000/v1").strip()   # config.json: LLM adresi (OpenAI uyumlu)
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
    BUTCE_AYAR = {ad: _int(cfg.get(k)) for ad, k in (("parca", "parca_token"), ("paralel", "paralel"))
                  if _int(cfg.get(k)) > 0}
    SUNUCU, SON_HATA = None, None
    _TANI_YAPILDI, _SEMA_DESTEGI, _MESAJ_TOKENIZE = False, True, True
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
    global _TANI_YAPILDI
    with _tani_kilit:
        if _TANI_YAPILDI and not zorla:
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

DUZELT_SEMASI = {
    "type": "object",
    "properties": {
        "duzeltmeler": {"type": "array", "items": {"type": "object", "properties": {
            "orijinal": {"type": "string"}, "duzeltilmis": {"type": "string"}},
            "required": ["orijinal", "duzeltilmis"]}},
    },
    "required": ["duzeltmeler"],
}

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

DUZELT_SISTEM = (
    "Aşağıdaki satırlar Teams'in OTOMATİK ALTYAZISINDAN geldi; kelimeler yanlış duyulmuş, terimler ve "
    "kişi adları bozulmuş olabilir (örn. 'komplo tit' -> 'commit', 'jandarma kaydı' -> 'Jira kaydı', "
    "'atay'da' -> 'Ata'da'). Görevin: sözlükteki terimlere ve katılımcı adlarına göre, ya da bağlamda "
    "anlamsız duran ve sesçe yakın gerçek bir kelimesi olan yerleri bulmak. Satırları YENİDEN YAZMA; "
    "yalnızca düzeltme listesi döndür: her maddede altyazıdaki TAM ifade (orijinal) ve doğrusu (duzeltilmis). "
    "Emin olmadığını listeye alma. En fazla 25 madde. Yalnızca JSON döndür."
)

TURKCE_KURAL = ("ÇIKTI DİLİ: Bütün alanları yalnızca TÜRKÇE yaz. Terimleri, ürün ve kişi adlarını olduğu gibi "
                "bırak ama cümleleri asla İngilizce kurma.")

MAP_SISTEM = (
    "Sen deneyimli bir toplantı not alıcısısın. Sana bir toplantının BİR bölümünün transkripti verilecek "
    "(konuşmadan yazıya çevrilmiş; kısmen düzeltilmiş, hâlâ bozuk kelimeler olabilir).\n"
    "Görev: bu bölümden yalnızca gerçekten söylenenleri, aşağıdaki alanlarla JSON olarak çıkar.\n"
    "- ozet: bölümde ne konuşulduğunu 2-4 cümleyle, kronolojik ve olgusal anlat (kim ne önerdi, ne "
    "tartışıldı, neye varıldı). Yorum yok, genelleme yok, transkriptte olmayan bilgi yok.\n"
    "- konular: ele alınan başlıklar (kısa, 2-6 kelime).\n"
    "- kararlar: AÇIKÇA karara bağlanmış şeyler. Öneri, niyet ya da 'bakarız' düzeyindeki ifadeler karar "
    "DEĞİLDİR; onlar 'acik_sorular'a gider. Kararı vereni yalnızca gerçekten tartışmalı ya da tek kişinin "
    "verdiği kararlarda, cümle içinde yaz ('Elif'in önerisiyle ...'); sonuna parantezle tek ad ekleme.\n"
    "- aksiyonlar: bir kişinin üstlendiği, BİTİNCE BELLİ OLAN somut işler; 'madde' alanı asla boş kalmaz. "
    "Sorumlusu belirsiz ve fiili genel ('anlamak', 'değerlendirmek', 'bakmak') olan ifadeler aksiyon değildir, "
    "'konular'a gider. Sorumlu katılımcı listesindeki yazımla, belli değilse 'belirsiz'; tarih toplantı "
    "tarihine göre gerçek tarihe çevrilir ('perşembeye' -> 'YYYY-MM-DD (Perşembe)'), belli değilse '-'.\n"
    "- acik_sorular: yalnızca bu bölümde cevapsız kalan ve bir sonraki adımı etkileyen sorular; en fazla 4. "
    "Şunları YAZMA: kendi anlama soruların (kim/ne olduğu belirsiz ad ve terimler 'belirsiz_terimler'e "
    "gider), transkriptten alıntı ya da parantez içi açıklama, aynı bölümde cevaplanan soru.\n"
    "- belirsiz_terimler: bozuk duyulduğunu düşündüğün kelime/terimler (transkriptteki yazımıyla).\n"
    "Kurallar: emin olamadığın yeri olduğu gibi aktar ve sonuna (?) koy. Sözlük ve gündem yalnızca yazım "
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
    "- Yeni madde EKLEME; sözlük, gündem ya da genel bilgiden içerik üretme. İfadeleri kısaltabilirsin, anlamı "
    "değiştirme. [B..] etiketlerini çıktıya yazma. (?) işaretli belirsizlikleri koru.\n"
    + TURKCE_KURAL + " Yalnızca JSON döndür."
)

GENEL_SISTEM = (
    "Sen deneyimli bir toplantı not alıcısısın. Sana bir toplantının bölüm bölüm çıkarılmış özetleri ve "
    "birleştirilmiş kararları/aksiyonları verilecek.\n"
    "- ozet: bölüm özetlerini KRONOLOJİK sırayla bağlayıp toplantının akışını anlatan TEK paragraf (neyle "
    "başladı, ne tartışıldı, nereye varıldı). Bölüm özetlerinde olmayan bilgi ekleme; genel bilginle boşluk "
    "doldurma. Uzunluk içerikle orantılı: kısa toplantı 2-3 cümle, uzun toplantı en fazla 8 cümle.\n"
    "- sonraki_adim: 1-2 cümle, yalnızca verilen kararlardan ve aksiyonlardan türetilmiş; hiç karar ve "
    "aksiyon yoksa '-'.\n"
    + TURKCE_KURAL + " Yalnızca JSON döndür."
)


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
    return json.loads(re.sub(r",\s*([}\]])", r"\1", metin[a:b + 1]))


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
    sistem = mesajlar[0]["content"] if mesajlar else ""
    for ad, sablon in (("duzeltme", DUZELT_SISTEM), ("bolum", MAP_SISTEM), ("liste", LISTE_SISTEM),
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
        if _BAGLAM_IFADE.search(g) or "does not exist" in g:
            raise
        sonuc = _cagir(ortak)
        ek["sema_dustu"] = True
        if _SEMA_IFADE.search(g) and _SEMA_DESTEGI:
            _SEMA_DESTEGI = False
            _uyar(f"LLM sunucusu json_schema'yı reddetti; şemasız devam ediliyor ({_kisalt(g, 160)})")
        return sonuc


def _sor(mesajlar, max_tokens, sema, effort, temperature, dusunme):
    global BAGLAM_PENCERESI
    for deneme in range(2):
        giris = _giris_token(mesajlar)
        izin = BAGLAM_PENCERESI - giris - MARJ
        if izin < 256:
            raise ValueError(f"girdi bağlam penceresine sığmıyor: girdi {giris} + pay {MARJ} token, "
                             f"pencere {BAGLAM_PENCERESI} (config.json 'context' / sunucu max_model_len)")
        mt = min(max_tokens, izin)
        kw = {"reasoning_effort": effort} if dusunme else {"enable_thinking": False}
        ortak = dict(model=MODEL, messages=mesajlar, max_tokens=mt, temperature=temperature, top_p=0.95,
                     extra_body={"chat_template_kwargs": kw})
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
            yeni = _baglam_siniri(_govde(e))
            if deneme == 0 and yeni and yeni < BAGLAM_PENCERESI:
                # sunucunun penceresi bildigimizden kucuk: ogren ve ayni istegi bir kez kirpilmis butceyle dene
                _uyar(f"LLM bağlam penceresi {BAGLAM_PENCERESI} değil {yeni}; bütçeler buna göre küçültüldü")
                BAGLAM_PENCERESI = yeni
                continue
            raise
    ayrinti = getattr(u, "completion_tokens_details", None)
    ek.update(giris_token=giris, max_tokens=mt, cikti_token=getattr(u, "completion_tokens", None),
              sunucu_giris_token=getattr(u, "prompt_tokens", None),
              dusunce_token=getattr(ayrinti, "reasoning_tokens", None) if ayrinti else None,
              dusunce_karakter=len(dusunce or ""), dusunce_icerikte=bool(_DUSUNCE_ETIKET.search(icerik or "")))
    return dusunce_temizle(icerik), neden, ek


def _sema_json(metin, sema):
    """JSON'u ayiklar ve semanin zorunlu anahtarlarini tasiyip tasimadigini dogrular; degilse ValueError."""
    v = json_ayikla(metin)
    eksik = [k for k in (sema or {}).get("required", []) if k not in v]
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


def baglam_metni(baslik, tarih, konusmacilar, sozluk_metni, gundem=""):
    m = (f"Toplantı: {baslik}\nTarih: {tarih.isoformat()} ({GUNLER[tarih.weekday()]})\n"
         f"Katılımcılar: {', '.join(konusmacilar) or '-'}\n"
         f"Sözlük (doğru yazımlar): {sozluk_metni}")
    if gundem:
        m += f"\nGündem/davet metni: {gundem[:800]}"
    m += ("\nKural: Yalnızca bu toplantının konuşma metnini kullan. Sözlük ve gündem sadece yazım/ad ipucudur; "
          "metinde geçmeyen bir konuyu, terimi ya da kararı ekleme, başka toplantılardan bilgi varsayma.")
    return m


# ---------- duzeltme ----------

def satirlari_duzelt(satirlar, baglam):
    """satirlar: ['Ad: cümle', ...]. Modelden yalnizca duzeltme listesi alir (kucuk cikti),
    degisimleri metne kendisi uygular. Dondurur (duzeltilmis_satirlar, [(orijinal, duzeltilmis)])."""
    mesajlar = [{"role": "system", "content": DUZELT_SISTEM},
                {"role": "user", "content": f"{baglam}\n\nSatırlar:\n" + "\n".join(satirlar)}]
    v = _json_sor(mesajlar, butce("duzeltme"), DUZELT_SEMASI, temperature=0.2, dusunme=False)
    if not v:
        return satirlar, []
    duzeltmeler = []
    for d in (v.get("duzeltmeler") or []):
        if not isinstance(d, dict):
            continue
        o, y = _metin(d.get("orijinal")).strip(), _metin(d.get("duzeltilmis")).strip()
        if o and y and o.lower() != y.lower() and len(o) >= 3:
            duzeltmeler.append((o, y))
    cikti = []
    for satir in satirlar:
        kim, _, ne = satir.partition(":")
        for o, y in duzeltmeler:
            # kelime sinirli: 'ata' -> 'Ata' duzeltmesi 'hatalar'in icini bozmasin
            ne = re.sub(r"(?<!\w)" + re.escape(o) + r"(?!\w)", lambda _, y=y: y, ne, flags=re.IGNORECASE)
        cikti.append(f"{kim}:{ne}")
    return cikti, duzeltmeler


# ---------- ozet ----------

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
    return temiz


def madde_dolu(madde):
    """Aksiyon maddesi gercekten bir sey soyluyor mu? ('', '-', tek harf degil)"""
    return len(_metin(madde).strip().strip(" -.")) >= 2


def _ozet_dili(o):
    return " ".join([o["ozet"]] + o["kararlar"] + o["acik_sorular"] + [a["madde"] for a in o["aksiyonlar"]])


def bolum_ozetle(blok, sira, baglam, onceki_konular=None):
    onceki = [_metin(k) for k in (onceki_konular or []) if _metin(k).strip()]
    devam = f"\nÖnceki bölümde konuşulanlar: {', '.join(onceki)}" if onceki else ""
    kullanici = {"role": "user", "content": f"{baglam}{devam}\n\nToplantının {sira}. bölümü:\n\n{blok}"}
    mesajlar = [{"role": "system", "content": MAP_SISTEM}, kullanici]
    # JSON cikarimi derin dusunme istemez: "low" ile dusunme tokenleri kisa kalir, cevap kesilmez
    o = normalize_ozet(_json_sor(mesajlar, butce("bolum"), BOLUM_SEMASI, effort="low", temperature=0.3))
    if o and ingilizce_mi(_ozet_dili(o)):
        # Model (cogunlukla dusunme dilinin etkisiyle) Ingilizce yazdi: dusunmesiz, dil kurali vurgulu tekrar
        tekrar = [{"role": "system", "content": MAP_SISTEM + "\n" + TURKCE_KURAL}, kullanici]
        o2 = normalize_ozet(_json_sor(tekrar, butce("bolum"), BOLUM_SEMASI, temperature=0.2, dusunme=False))
        if o2 and not ingilizce_mi(_ozet_dili(o2)):
            o = o2
    if o and not o["ozet"].strip(" -.") and len(blok) > 200:
        # listeler dolu ama ozet alani bos: tam notun ozet paragrafi bu alanlardan kurulur, bos birakilmaz
        tekrar = [{"role": "system", "content": MAP_SISTEM + "\n'ozet' alanını ASLA boş bırakma."}, kullanici]
        o2 = normalize_ozet(_json_sor(tekrar, butce("bolum"), BOLUM_SEMASI, temperature=0.2, dusunme=False))
        if o2 and o2["ozet"].strip(" -.") and not ingilizce_mi(_ozet_dili(o2)):
            o = o2
        elif o["konular"]:
            o["ozet"] = "Bu bölümde " + ", ".join(o["konular"]) + " konuşuldu."
    return o


# ---------- birlestirme (tam not) ----------

def _sade(s):
    s = normalize_bosluk(s).lower()
    for a, b in zip("çğıöşüâî", "cgiosuai"):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9 ]", "", s)


def normalize_bosluk(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _benzer(a, b, esik=0.85):
    a, b = _sade(a), _sade(b)
    return bool(a) and (a == b or difflib.SequenceMatcher(None, a, b).ratio() >= esik)


def _aksiyon_birlestir(eski, yeni):
    """Ayni is iki bolumde gectiyse sonrakini tut; sonrakinde sorumlu/tarih bossa oncekinden al."""
    v = dict(yeni)
    if v.get("sorumlu") in ("", "belirsiz", None) and eski.get("sorumlu") not in ("", "belirsiz", None):
        v["sorumlu"] = eski["sorumlu"]
    if v.get("tarih") in ("", "-", None) and eski.get("tarih") not in ("", "-", None):
        v["tarih"] = eski["tarih"]
    return v


def listeleri_tekille(bolumler):
    """LLM'siz birlestirme: bolum listelerini sirayla toplar, benzer maddelerde sonraki bolumu tutar.
    Hem LLM birlestirmesi basarisiz olursa yedek, hem de onun sonucunu denetlemek icin olcut."""
    sonuc = {"kararlar": [], "aksiyonlar": [], "acik_sorular": []}

    def ekle(liste, oge, anahtar, birlestir=None):
        for i, v in enumerate(liste):
            if _benzer(anahtar(v), anahtar(oge)):
                liste[i] = birlestir(v, oge) if birlestir else oge
                return
        liste.append(oge)

    for b in bolumler:
        for k in b.get("kararlar") or []:
            ekle(sonuc["kararlar"], k, str)
        for a in b.get("aksiyonlar") or []:
            if isinstance(a, dict) and madde_dolu(a.get("madde")):
                ekle(sonuc["aksiyonlar"], a, lambda x: x.get("madde", ""), _aksiyon_birlestir)
        for q in b.get("acik_sorular") or []:
            ekle(sonuc["acik_sorular"], q, str)
    return sonuc


def _liste_girdisi(bolumler):
    bloklar, adet = [], 0
    for alan, baslik in (("kararlar", "Kararlar"), ("aksiyonlar", "Aksiyonlar"), ("acik_sorular", "Açık sorular")):
        ogeler = []
        for i, b in enumerate(bolumler, 1):
            for x in b.get(alan) or []:
                if alan == "aksiyonlar":
                    ogeler.append(f"- [B{i}] {x.get('madde', '')} — {x.get('sorumlu', 'belirsiz')} — {x.get('tarih', '-')}")
                else:
                    ogeler.append(f"- [B{i}] {x}")
        adet += len(ogeler)
        bloklar.append(f"{baslik}:\n" + ("\n".join(ogeler) or "-"))
    return "\n\n".join(bloklar), adet


def _liste_dili(v):
    return " ".join(v["kararlar"] + v["acik_sorular"] + [a["madde"] for a in v["aksiyonlar"]])


def listeleri_birlestir(bolumler, baglam, ilerleme=print):
    """Karar/aksiyon/acik soru listelerini tekillestirir. LLM cevabi kesik, Ingilizce ya da supheli
    derecede kisaysa (madde kaybi) LLM'siz tekillestirme kullanilir."""
    yedek = listeleri_tekille(bolumler)
    girdi, adet = _liste_girdisi(bolumler)
    if adet <= 3:                                      # birlestirecek bir sey yok
        return yedek
    mesajlar = [{"role": "system", "content": LISTE_SISTEM},
                {"role": "user", "content": f"{baglam}\n\nBölümlerden gelen maddeler:\n\n{girdi}"}]
    cikti = min(butce("liste"), max(1000, int(token_say(girdi) * 1.3)))
    if _giris_token(mesajlar) + cikti + MARJ > BAGLAM_PENCERESI:
        ilerleme("  madde listesi bağlama sığmıyor, yerel birleştirme kullanıldı")
        return yedek
    try:
        v = _json_tam(mesajlar, cikti, LISTE_SEMASI)
    except Exception as e:
        ilerleme(f"  ! liste birleştirme hatası, yerel birleştirme kullanıldı: {hata_metni(e)}")
        return yedek
    if not v:
        ilerleme("  ! liste birleştirme kesildi/bozuk, yerel birleştirme kullanıldı")
        return yedek
    ham_aks = [a for a in (v.get("aksiyonlar") or []) if a]
    bos_madde = sum(1 for a in ham_aks if not madde_dolu(a.get("madde") if isinstance(a, dict) else a))
    if ham_aks and bos_madde / len(ham_aks) > 0.2:
        ilerleme(f"  ! liste birleştirmesinde {bos_madde} aksiyonun maddesi boş, yerel birleştirme kullanıldı")
        return yedek
    n = normalize_ozet({"ozet": "", **v})
    sonuc = {k: n[k] for k in ("kararlar", "aksiyonlar", "acik_sorular")}
    for alan in ("kararlar", "acik_sorular"):
        sonuc[alan] = [re.sub(r"^\[B\d+\]\s*", "", x) for x in sonuc[alan]]
    for a in sonuc["aksiyonlar"]:
        a["madde"] = re.sub(r"^\[B\d+\]\s*", "", a["madde"])
    # karar->aksiyon tasimasi olabilecegi icin kayip iki listenin TOPLAMINDAN olculur; yalniz DOLU maddeler
    # sayilir (bos maddeli aksiyonlar normalize_ozet'te atildi, eskiden 'var' sayilip yedege dusulmuyordu)
    kayip = (len(sonuc["kararlar"]) + len(sonuc["aksiyonlar"])
             < (len(yedek["kararlar"]) + len(yedek["aksiyonlar"]) + 1) // 2)
    if kayip or ingilizce_mi(_liste_dili(sonuc)):
        ilerleme("  ! liste birleştirmesi madde kaybetti ya da dili bozuk, yerel birleştirme kullanıldı")
        return yedek
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
                            + [f"- Aksiyon: {a['madde']} ({a['sorumlu']}, {a['tarih']})" for a in listeler["aksiyonlar"]])
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
    aks = sorted(listeler.get("aksiyonlar") or [], key=lambda a: a.get("sorumlu") in ("", "belirsiz", None))
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
    'Elif Bala, Cemil Kahveci' gibi ortak sorumlu her iki kisiye de yazilir."""
    gruplar = {}
    for a in aksiyonlar:
        if not madde_dolu(a.get("madde")):
            continue
        adlar = [x.strip() for x in re.split(r",|/| ve ", a.get("sorumlu") or "") if x.strip()] or ["belirsiz"]
        for ad in adlar:
            gruplar.setdefault(ad, [])
            if a["madde"] not in gruplar[ad]:
                gruplar[ad].append(a["madde"])
    return sorted(gruplar.items(), key=lambda kv: (kv[0] == "belirsiz", -len(kv[1]), kv[0]))


def not_markdown(genel, listeler, kaynak_fn=None):
    """Notun Markdown'u kodda kurulur: basliklar sabit ve Turkce, tablo hic yarim kalmaz.
    kaynak_fn(metin, sorumlu) -> 'HH:MM:SS' | None: maddenin transkriptte gectigi an."""
    def kaynak(metin, sorumlu=None):
        ts = kaynak_fn(metin, sorumlu) if kaynak_fn else None
        return f"{KAYNAK_ISARETI}{ts}" if ts else ""

    def maddeler(lst, isaret=False):
        return "\n".join(f"- {normalize_bosluk(x)}" + (f" {kaynak(x)}".rstrip() if isaret else "")
                         for x in lst) or "-"
    parcalar = ["## Özet", genel["ozet"] or "-", ""]
    if genel.get("konu_akisi"):
        parcalar += ["## Konu akışı", genel["konu_akisi"], ""]
    parcalar += ["## Kararlar", maddeler(listeler["kararlar"], isaret=True), "", "## Aksiyonlar"]
    if listeler["aksiyonlar"]:
        basliklar = "| # | Madde | Sorumlu | Tarih |" + (" Kaynak |" if kaynak_fn else "")
        parcalar += [basliklar, "|---|---|---|---|" + ("---|" if kaynak_fn else "")]
        for i, a in enumerate(listeler["aksiyonlar"], 1):
            satir = f"| {i} | {_hucre(a['madde'])} | {_hucre(a['sorumlu'])} | {_hucre(a['tarih'])} |"
            if kaynak_fn:
                satir += f" {kaynak(a['madde'], a['sorumlu']) or '-'} |"
            parcalar.append(satir)
        gruplar = kisi_bazli(listeler["aksiyonlar"])
        if len(listeler["aksiyonlar"]) >= 4 and len(gruplar) >= 2:
            # 'aksiyon/karar/soru' kelimesi gecmeyen baslik: dayanak kontrolu bu ozet satirlarini denetlemez
            parcalar += ["", "## Kişiye göre iş listesi"]
            parcalar += [f"- {ad} ({len(isler)}): " + "; ".join(normalize_bosluk(x).rstrip(".") for x in isler)
                         for ad, isler in gruplar]
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
_TEK_AD_PARANTEZ = re.compile(r"\s*\((\w+)\)\s*(\.?)\s*$")                  # "... (Elif)" / "... (Elif)."
_ALINTI_PARANTEZ = re.compile(r"\s*\([^()]*['\"“”‘’][^()]*\)\s*\.?\s*$")   # "... (Cemil: '...')"
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
    ('Girdi tablolari Cemil tarafindan paylasilacak') bir istir: aksiyonlara o kisiyle tasinir.
    Sondaki '(Cemil onerdi, Elif onayladi)' gibi karar vereni gosteren parantez ad olarak sayilmaz."""
    aksiyonlar = [dict(a) for a in listeler.get("aksiyonlar") or []]
    kararlar = []
    for k in listeler.get("kararlar") or []:
        if any(cift_yonlu_benzerlik(k, a.get("madde", "")) >= 0.6 for a in aksiyonlar):
            continue
        govde = _SON_PARANTEZ.sub("", k).strip()
        adlar = _gecen_adlar(govde, katilimcilar)
        kelimeler = re.findall(r"\w+", tr_kucuk(govde))
        if adlar and kelimeler and _GELECEK_FIIL.search(kelimeler[-1]):
            aksiyonlar.append({"madde": govde.rstrip(" ."), "sorumlu": ", ".join(adlar), "tarih": "-"})
            continue
        kararlar.append(k)
    return dict(listeler, kararlar=kararlar, aksiyonlar=aksiyonlar)


def karar_parantez_temizle(kararlar):
    """Karar sonundaki tek kelimelik '(Elif)' parantezi anlamsiz: kaldirilir. '(?)' belirsizlik isareti kalir."""
    return [_TEK_AD_PARANTEZ.sub(r"\2", k).strip() or k for k in kararlar]


def belirsiz_aksiyonlari_ele(aksiyonlar, kaynak_fn=None):
    """Sorumlusu belirsiz, en fazla 4 kelimelik ve transkriptte kaynak satiri bulunamayan aksiyon
    ('Gereksinimleri anlamak') is degil genel ifadedir: duser. Kaynak bulucu yoksa dokunulmaz."""
    if not kaynak_fn:
        return list(aksiyonlar)
    kalan = []
    for a in aksiyonlar:
        if (normalize_bosluk(a.get("sorumlu")).lower() in _BELIRSIZ and len(a.get("madde", "").split()) <= 4
                and not kaynak_fn(a.get("madde", ""), None)):
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
        if "(?)" in q and re.search(r"(?<!\w)(kim|kimin|nedir|hangi|ne olduğu)(?!\w)", k):
            continue
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


def birlestir(bolumler, baglam, ilerleme=print, kaynak_fn=None, sablon="genel", katilimcilar=None,
              aksiyon_fn=None, adim_fn=None):
    """aksiyon_fn(aksiyonlar) -> aksiyonlar: cagiranin son islemi (motor: sorumlu dogrulama).
    adim_fn(metin): arayuzdeki ilerleme seridi icin asama bildirimi."""
    adim = adim_fn or (lambda m: None)
    bolumler = [b for b in bolumler if b]
    if not bolumler:
        return "_(özetlenebilen bölüm yok)_"
    talimat = SABLONLAR.get(sablon, SABLONLAR["genel"])[1]
    if talimat:
        baglam = f"{baglam}\nNot şablonu: {talimat}"
    adim("Kararlar ve aksiyonlar birleştiriliyor")
    ilerleme("  kararlar, aksiyonlar ve açık sorular birleştiriliyor...")
    listeler = listeleri_temizle(listeleri_birlestir(bolumler, baglam, ilerleme), katilimcilar, kaynak_fn)
    if aksiyon_fn:
        listeler["aksiyonlar"] = aksiyon_fn(listeler["aksiyonlar"])
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
    "- Transkriptte cevap yoksa 'Bu toplantının transkriptinde bu konu geçmiyor.' de; tahmin yürütme, genel "
    "bilgiyle doldurma.\n- Kısa ve net yaz.\n" + TURKCE_KURAL
)


def toplantiya_sor(soru, bolumler, baglam, cikti=None):
    """bolumler: [(aralik, '[ts] Kim: metin' satirlari)], kronolojik. Soruyla en ilgili bolumler baglama
    sigdigi kadar secilir (kelime koku ortakligina gore), kronolojik sirayla verilir. Girdi butcesinden
    ilk cagrinin GERCEK max_tokens'i (cevap + dusunme payi) dusulur."""
    cikti = cikti or butce("soru")
    pay = butce("soru_dusunme")
    def kokler(m):
        return {k[:5] for k in re.findall(r"[a-z0-9]+", _sade(m)) if len(k) >= 3}
    sk = kokler(soru)
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
