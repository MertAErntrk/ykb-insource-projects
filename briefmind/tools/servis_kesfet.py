"""
servis_kesfet.py — Bir model servisinin (embedding / reranker / LLM) hangi yollari sundugunu bulur.

Sunucu {"detail":"Not Found"} donuyorsa bu FastAPI'dir: vLLM/TEI'nin standart yollari yerine kendi yollarini
tanimlamistir. FastAPI cogu zaman yol listesini /openapi.json'da yayinlar; arac once ona bakar, sonra
bilinen yollari dener (embedding: govde bicimleri, boyut; reranker: /rerank, /score ...).

Kullanim (briefmind klasorunden; PowerShell tirnak sorunu olmaz):
  python tools\\servis_kesfet.py https://<embedding-adresi>
  python tools\\servis_kesfet.py https://<adres> --anahtar <token>      (servis token istiyorsa)
  python tools\\servis_kesfet.py https://<adres> --proxy                (Windows proxy'si uzerinden)
  python tools\\servis_kesfet.py --llm                                 (LLM: adres/model/anahtar config.json'dan)

Cikti ekrana ve servis_kesfet_cikti.txt'ye yazilir; adres <ADRES> diye maskelenir, paylasmak guvenlidir.
"""
import argparse
import json
import os
import ssl
import sys
from urllib.parse import urlparse

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(KOK)
CIKTI = "servis_kesfet_cikti.txt"
ORNEK = ["test ortami persembe hazir olacak", "butce onayi gelecek hafta"]
SORGU = "test ortami ne zaman hazir"
GET_YOLLAR = ["/", "/health", "/healthz", "/ping", "/info", "/v1/models", "/models", "/docs", "/redoc"]
EMB_YOLLAR = ["/v1/embeddings", "/embeddings", "/embed", "/v1/embed", "/encode", "/embedding", "/api/embed",
              "/api/embeddings", "/predict", "/invocations"]
SOHBET_YOLLAR = ["/v1/chat/completions", "/chat/completions", "/v1/completions", "/completions", "/generate",
                 "/v1/generate", "/chat", "/api/chat", "/invoke", "/v1/responses"]
RR_YOLLAR = ["/rerank", "/v1/rerank", "/score", "/v1/score", "/reranking", "/v1/reranking", "/api/rerank"]
SATIRLAR = []


def yaz(s=""):
    SATIRLAR.append(s)
    print(s)


def maskele(s, adres):
    s = str(s)
    h = urlparse(adres).netloc
    return s.replace(adres.rstrip("/"), "<ADRES>").replace(h, "<ADRES>") if h else s


def kisa(x, n=240):
    s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n] + " …"


def vektor_bul(x, derinlik=0):
    """Cevabin icindeki ilk sayi listesini (embedding) bulur -> (vektor sayisi, boyut) ya da None."""
    if derinlik > 6:
        return None
    if isinstance(x, list) and x:
        if all(isinstance(v, (int, float)) for v in x[:8]) and len(x) > 16:
            return 1, len(x)
        if isinstance(x[0], list) and x[0] and all(isinstance(v, (int, float)) for v in x[0][:8]) and len(x[0]) > 16:
            return len(x), len(x[0])
        for v in x[:3]:
            r = vektor_bul(v, derinlik + 1)
            if r:
                return (len(x), r[1]) if isinstance(v, dict) else r
    if isinstance(x, dict):
        for k in ("data", "embeddings", "embedding", "dense_vecs", "dense", "vectors", "outputs", "result"):
            if k in x:
                r = vektor_bul(x[k], derinlik + 1)
                if r:
                    return r
        for v in x.values():
            r = vektor_bul(v, derinlik + 1)
            if r:
                return r
    return None


class Istemci:
    def __init__(self, adres, anahtar, proxy, ca):
        import httpx
        verify = ssl.create_default_context(cafile=ca) if ca and os.path.isfile(ca) else False
        bas = {"Authorization": f"Bearer {anahtar}"} if anahtar else {}
        self.kok = adres.rstrip("/")
        self.h = httpx.Client(verify=verify, trust_env=proxy, headers=bas, timeout=httpx.Timeout(60, connect=15))

    def iste(self, yontem, yol, govde=None):
        try:
            r = self.h.request(yontem, self.kok + yol, **({"json": govde} if govde is not None else {}))
        except Exception as e:
            return None, f"{type(e).__name__}: {str(e)[:160]}"
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text


def openapi_oku(ist):
    for yol in ("/openapi.json", "/v1/openapi.json", "/api/openapi.json", "/docs/openapi.json"):
        durum, j = ist.iste("GET", yol)
        if durum == 200 and isinstance(j, dict) and j.get("paths"):
            return yol, j
    return None, None


def sema_alanlari(spec, islem):
    """POST govdesinin alan adlari (zorunlular * ile)."""
    try:
        sema = islem["requestBody"]["content"]["application/json"]["schema"]
        if "$ref" in sema:
            sema = spec["components"]["schemas"][sema["$ref"].split("/")[-1]]
        zorunlu = set(sema.get("required") or [])
        return [k + ("*" if k in zorunlu else "") for k in (sema.get("properties") or {})]
    except Exception:
        return []


def sohbet_dene(ist, spec, model_adi, m):
    """LLM: sohbet yolu ve govde bicimini bulur (404 -> yol yok; 422 -> yol var, govde farkli)."""
    yaz("\n== 5) Sohbet (LLM) denemesi ==")
    aday = list(SOHBET_YOLLAR)
    if spec:
        aday = [p for p, isl in spec["paths"].items() if "post" in isl and any(
            k in p.lower() for k in ("chat", "complet", "generat", "invoke", "respons"))] + aday
    mesaj = [{"role": "user", "content": "Tek kelimeyle cevap ver: merhaba"}]
    govdeler = [{"model": model_adi, "messages": mesaj, "max_tokens": 16},
                {"messages": mesaj, "max_tokens": 16},
                {"model": model_adi, "prompt": "merhaba", "max_tokens": 16},
                {"inputs": "merhaba", "parameters": {"max_new_tokens": 16}}]
    if not model_adi:
        govdeler = govdeler[1:]
    bulundu = False
    for yol in dict.fromkeys(aday):
        for govde in govdeler:
            durum, j = ist.iste("POST", yol, govde)
            if durum == 404 or durum is None:
                if durum is None:
                    yaz(f"  POST {yol:22} -> baglanti hatasi: {m(kisa(j, 160))}")
                break
            alanlar = sorted(j) if isinstance(j, dict) else type(j).__name__
            yaz(f"  POST {yol:22} govde {sorted(govde)} -> {durum}  alanlar: {alanlar}  {m(kisa(j, 220))}")
            if durum == 200:
                bulundu = True
                break
        if bulundu:
            break
    if bulundu:
        yaz(f"  CALISAN sohbet yolu: {yol}  (config 'route' bu yolun koku + gerekirse '/v1' olmali)")
    else:
        yaz("  calisan sohbet yolu bulunamadi; 404 disi cevaplari (ozellikle 401/403/422) bana gonder.")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    import warnings
    warnings.filterwarnings("ignore")
    a = argparse.ArgumentParser()
    a.add_argument("adres", nargs="?", default="")
    a.add_argument("--llm", action="store_true", help="adres/model/anahtar config.json'daki LLM ayarindan")
    a.add_argument("--anahtar", default="")
    a.add_argument("--model", default="", help="biliniyorsa model adi (govdeye eklenir)")
    a.add_argument("--proxy", action="store_true", help="Windows/ortam proxy ayarini kullan")
    a.add_argument("--ca", default="", help="kok sertifika dosyasi (yoksa TLS dogrulamasi kapali)")
    g = a.parse_args()
    if g.llm or not g.adres:
        try:
            with open("config.json", encoding="utf-8-sig") as f:
                cfg = json.load(f)
        except Exception as e:
            print(f"config.json okunamadi: {e}")
            return
        g.adres = g.adres or cfg.get("route", "")
        g.model = g.model or str(cfg.get("model") or "")
        g.anahtar = g.anahtar or str(cfg.get("llm_key") or cfg.get("api_key") or "")
        g.proxy = g.proxy or bool(cfg.get("llm_proxy", False))
        g.ca = g.ca or (cfg.get("ca_bundle") or "")
        g.llm = True
    adres = g.adres.strip().rstrip("/")
    if adres.endswith("/v1"):
        adres = adres[:-3]
    ist = Istemci(adres, g.anahtar, g.proxy, g.ca)
    m = lambda s: maskele(s, adres)                                   # noqa: E731

    yaz("== 1) GET yollari ==")
    for yol in GET_YOLLAR:
        durum, j = ist.iste("GET", yol)
        if yol in ("/docs", "/redoc") and durum == 200:
            j = "(HTML sayfasi var — tarayicida acip yollari gorebilirsin)"
        yaz(f"  GET {yol:12} -> {durum}  {m(kisa(j, 160))}")

    yaz("\n== 2) OpenAPI (FastAPI yol listesi) ==")
    yol, spec = openapi_oku(ist)
    emb_aday, rr_aday, model_adi = list(EMB_YOLLAR), list(RR_YOLLAR), g.model
    if spec:
        yaz(f"  bulundu: {yol}  baslik: {kisa((spec.get('info') or {}).get('title', '-'), 80)}")
        for p, islemler in spec["paths"].items():
            for yontem, islem in islemler.items():
                if not isinstance(islem, dict):
                    continue
                alan = sema_alanlari(spec, islem) if yontem.lower() == "post" else []
                yaz(f"  {yontem.upper():6} {p}" + (f"   govde: {', '.join(alan)}" if alan else ""))
                kucuk = p.lower()
                if yontem.lower() == "post" and any(k in kucuk for k in ("embed", "encode", "vector")):
                    emb_aday.insert(0, p)
                if yontem.lower() == "post" and any(k in kucuk for k in ("rerank", "score", "rank")):
                    rr_aday.insert(0, p)
    else:
        yaz("  /openapi.json yok ya da kapali. Tarayicida <adres>/docs acilmiyorsa yollari servis sahibine sor.")
    for yol in ("/v1/models", "/models"):
        durum, j = ist.iste("GET", yol)
        if durum == 200 and isinstance(j, dict) and j.get("data"):
            model_adi = model_adi or j["data"][0].get("id", "")
    yaz(f"  model adi: {model_adi or '(bilinmiyor — gerekirse --model ile ver)'}")

    if not g.llm:
        yaz("\n== 3) Embedding denemesi ==")
        govdeler = [{"input": ORNEK}, {"inputs": ORNEK}, {"texts": ORNEK}, {"sentences": ORNEK}, {"text": ORNEK[0]},
                    {"input": ORNEK[0]}]
        if model_adi:
            govdeler = [dict(x, model=model_adi) for x in govdeler] + govdeler
        bulunan = None
        for yol in dict.fromkeys(emb_aday):
            ilk = None
            for govde in govdeler:
                durum, j = ist.iste("POST", yol, govde)
                ilk = ilk or (durum, j)
                if durum == 404 or durum is None:
                    break                                                   # yol yok: diger govdeleri deneme
                v = vektor_bul(j) if durum == 200 else None
                if v:
                    bulunan = (yol, list(govde), v)
                    break
            if bulunan:
                break
            if ilk and ilk[0] not in (404,):
                yaz(f"  POST {yol:16} -> {ilk[0]}  {m(kisa(ilk[1], 200))}")
        if bulunan:
            yol, alanlar, (adet, boyut) = bulunan
            yaz(f"  CALISAN: POST {yol}  govde alanlari: {alanlar}  -> {adet} vektor, boyut {boyut}"
                + ("  (bge-m3 ile uyumlu)" if boyut == 1024 else ""))
        else:
            yaz("  calisan embedding yolu bulunamadi (yukarida 404 disi cevap varsa onu bana gonder).")

        yaz("\n== 4) Reranker denemesi ==")
        rr_govdeler = [{"query": SORGU, "texts": ORNEK}, {"query": SORGU, "documents": ORNEK},
                       {"text_1": SORGU, "text_2": ORNEK}, {"query": SORGU, "passages": ORNEK},
                       {"pairs": [[SORGU, x] for x in ORNEK]}]
        if model_adi:
            rr_govdeler = [dict(x, model=model_adi) for x in rr_govdeler] + rr_govdeler
        rr = None
        for yol in dict.fromkeys(rr_aday):
            ilk = None
            for govde in rr_govdeler:
                durum, j = ist.iste("POST", yol, govde)
                ilk = ilk or (durum, j)
                if durum == 404 or durum is None:
                    break
                if durum == 200:
                    rr = (yol, list(govde), j)
                    break
            if rr:
                break
            if ilk and ilk[0] != 404:
                yaz(f"  POST {yol:16} -> {ilk[0]}  {m(kisa(ilk[1], 200))}")
        if rr:
            yaz(f"  CALISAN: POST {rr[0]}  govde alanlari: {rr[1]}  cevap: {m(kisa(rr[2], 200))}")
        else:
            yaz("  bu adreste reranker yok (tum yollar 404). Sorun degil: siralama RRF ile yapilir.")
    else:
        sohbet_dene(ist, spec, model_adi, m)

    cikti = "servis_kesfet_llm_cikti.txt" if g.llm else CIKTI
    with open(cikti, "w", encoding="utf-8") as f:
        f.write("\n".join(SATIRLAR) + "\n")
    yaz(f"\nKaydedildi: {cikti} (adres maskeli). Bu dosyanin icerigini bana gonder.")


if __name__ == "__main__":
    main()
