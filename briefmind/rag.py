"""
rag.py — Toplantiya soru icin hibrit arama: BM25 (Turkce kok) + embedding (vektor), RRF ile birlestirilir.

- Transkript, ust uste binen kucuk pencerelere (ornegin 6 satir, 2 satir ortusme) bolunur.
- Anahtar kelime: BM25, kelimeler Turkce karakter katlanip 5 harflik koke indirilir ('butceyi' ~ 'butce').
- Anlam: kurum ici embedding servisi (config.json -> embed_url; OpenAI uyumlu POST /v1/embeddings, govde
  {"input": [...]}). Qwen3-Embedding sorgulari talimatla gomer; vektorler embed_boyut'a kirpilip
  (Matryoshka) yeniden normalize edilir, diskte az yer kaplasin.
- Siralar Reciprocal Rank Fusion ile birlestirilir; reranker gerekmez.
- Vektorler toplanti klasorunde (rag_dizin.npz) saklanir; yalnizca yeni/degisen pencereler yeniden gomulur.
- Embedding servisi yoksa ya da hata verirse yalnizca BM25 kullanilir: soru-cevap hic durmaz.
Veri disariya yalnizca embedding servisine gider; dizin kullanicinin bilgisayarinda kalir.
"""
import hashlib
import json
import math
import os
import re
import ssl
import threading
from collections import Counter

import numpy as np

import ayar as ayar_mod

PENCERE_SATIR = 6
ORTUSME = 2
RRF_K = 60
PARTI = 32
SORGU_TALIMAT = ("Instruct: Toplantı transkriptinde bu soruyu yanıtlayan konuşma bölümlerini bul\n"
                 "Query: ")
DIZIN_AD = "rag_dizin.npz"

URL, ANAHTAR, BOYUT, TALIMAT = "", "", 1024, True
_http = None
_kilit = threading.Lock()
SON_HATA = None


def ayarla(cfg):
    """config.json: embed_url (bos -> yalnizca BM25), embed_key, embed_boyut (varsayilan 1024),
    embed_proxy, embed_talimat (Qwen3-Embedding sorgu talimati; baska model icin false)."""
    global URL, ANAHTAR, BOYUT, TALIMAT, _http, SON_HATA
    import httpx
    cfg = cfg or {}
    url = str(cfg.get("embed_url") or "").strip().rstrip("/")
    for son in ("/embeddings", "/v1"):
        if url.endswith(son):
            url = url[: -len(son)]
    URL = url
    ANAHTAR = str(cfg.get("embed_key") or "").strip()
    try:
        BOYUT = max(0, int(cfg.get("embed_boyut", 1024) or 0))
    except (TypeError, ValueError):
        BOYUT = 1024
    TALIMAT = bool(cfg.get("embed_talimat", True))
    yol = ayar_mod.tls_dogrulama(cfg)
    try:
        verify = ssl.create_default_context(cafile=yol) if yol else False
    except (ssl.SSLError, OSError, ValueError):
        verify = False
    _http = httpx.Client(verify=verify, trust_env=bool(cfg.get("embed_proxy", False)),
                         timeout=httpx.Timeout(60.0, connect=10.0),
                         headers={"Authorization": f"Bearer {ANAHTAR}"} if ANAHTAR else None)
    SON_HATA = None


def aktif():
    return bool(URL) and _http is not None


def _normalize(m):
    m = np.asarray(m, dtype="float32")
    if BOYUT and m.shape[1] > BOYUT:
        m = m[:, :BOYUT]
    n = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.maximum(n, 1e-9)


def gom(metinler, sorgu=False):
    """-> (n, boyut) float32, satirlar birim vektor. Hata: istisna."""
    if not aktif():
        raise RuntimeError("embedding adresi ayarlı değil (config.json → embed_url)")
    girdi = [(SORGU_TALIMAT + t) if (sorgu and TALIMAT) else t for t in metinler]
    sonuc = []
    for i in range(0, len(girdi), PARTI):
        r = _http.post(URL + "/v1/embeddings", json={"input": girdi[i:i + PARTI]})
        if r.status_code != 200:
            raise RuntimeError(f"embedding HTTP {r.status_code}: {r.text[:160]}")
        veri = r.json().get("data") or []
        veri = sorted(veri, key=lambda d: d.get("index", 0))
        sonuc.extend(d["embedding"] for d in veri)
    if len(sonuc) != len(girdi):
        raise RuntimeError(f"embedding: {len(girdi)} metin gönderildi, {len(sonuc)} vektör geldi")
    return _normalize(sonuc)


# ---------- pencereler ----------

def pencereler(satirlar, boy=PENCERE_SATIR, ortusme=ORTUSME):
    """satirlar: [{'ts','speaker','text'}] -> [{'bas','son','metin'}]; ust uste binen satir pencereleri."""
    satirlar = [s for s in satirlar if (s.get("text") or "").strip()]
    adim = max(1, boy - ortusme)
    sonuc = []
    for i in range(0, len(satirlar), adim):
        grup = satirlar[i:i + boy]
        if not grup:
            break
        sonuc.append({"bas": grup[0].get("ts", ""), "son": grup[-1].get("ts", ""),
                      "metin": "\n".join(f"[{s.get('ts', '')}] {s.get('speaker', '?')}: {s['text'].strip()}"
                                         for s in grup)})
        if i + boy >= len(satirlar):
            break
    return sonuc


# ---------- BM25 ----------

def _katla(s):
    s = (s or "").replace("İ", "i").replace("I", "ı").lower()
    for a, b in zip("çğıöşüâî", "cgiosuai"):
        s = s.replace(a, b)
    return s


_DURAK = {"bir", "bu", "su", "ve", "ile", "icin", "ama", "da", "de", "mi", "mu", "ne", "o", "ben", "sen", "biz",
          "siz", "onlar", "var", "yok", "gibi", "daha", "cok", "evet", "hayir", "tamam", "yani", "iste", "sey",
          "olarak", "olan", "kadar", "sonra", "once", "zaman", "nasil", "neden", "hangi", "kim", "kac"}


def terimler(s):
    """Turkce karakter katlanir, 2 harften kisa ve durak kelimeler atilir, 5 harflik koke indirilir."""
    return [k[:5] for k in re.findall(r"[a-z0-9]+", _katla(s)) if (len(k) >= 2 or k.isdigit()) and k not in _DURAK]


def bm25(sorgu, belgeler, k1=1.4, b=0.75):
    """-> her belge icin puan listesi."""
    dok = [terimler(d) for d in belgeler]
    n = len(dok)
    if not n:
        return []
    ort = sum(len(d) for d in dok) / n or 1.0
    df = Counter(t for d in dok for t in set(d))
    sk = set(terimler(sorgu))
    puan = []
    for d in dok:
        tf = Counter(d)
        p = 0.0
        for t in sk:
            if t not in tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            p += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / ort))
        puan.append(p)
    return puan


def rrf(*siralamalar, k=RRF_K):
    """siralamalar: indeks listeleri (en iyiden kotuye) -> birlesik indeks listesi."""
    toplam = Counter()
    for sira in siralamalar:
        for r, i in enumerate(sira):
            toplam[i] += 1.0 / (k + r + 1)
    return [i for i, _ in sorted(toplam.items(), key=lambda x: (-x[1], x[0]))]


# ---------- dizin (toplanti klasorunde onbellek) ----------

def _ozet(metin):
    return hashlib.sha1(metin.encode("utf-8")).hexdigest()[:16]


def vektorler(klasor, metinler):
    """Pencere vektorlerini dizinden okur, eksikleri gomup dizini gunceller. -> (n, boyut) | None."""
    yol = os.path.join(klasor, DIZIN_AD) if klasor else None
    eski = {}
    with _kilit:
        if yol and os.path.exists(yol):
            try:
                with np.load(yol, allow_pickle=False) as z:
                    if json.loads(str(z["ayar"])) == {"url": _ozet(URL), "boyut": BOYUT, "talimat": TALIMAT}:
                        eski = dict(zip(z["anahtar"].tolist(), z["vektor"].astype("float32")))
            except Exception:
                eski = {}
        anahtarlar = [_ozet(m) for m in metinler]
        eksik = [i for i, a in enumerate(anahtarlar) if a not in eski]
        if eksik:
            yeni = gom([metinler[i] for i in eksik])
            for j, i in enumerate(eksik):
                eski[anahtarlar[i]] = yeni[j]
        m = np.stack([eski[a] for a in anahtarlar]) if anahtarlar else np.zeros((0, BOYUT or 1), "float32")
        if yol and eksik:
            try:
                tut = sorted(set(anahtarlar))
                np.savez_compressed(yol, anahtar=np.array(tut), vektor=np.stack([eski[a] for a in tut]).astype("float16"),
                                    ayar=np.array(json.dumps({"url": _ozet(URL), "boyut": BOYUT, "talimat": TALIMAT})))
            except Exception:
                pass
    return m


def hibrit_sirala(soru, metinler, klasor=None, olay=None):
    """-> (indeks sirasi en ilgiliden, kullanilan yontem). Embedding hatasinda BM25'e duser."""
    global SON_HATA
    if not metinler:
        return [], "bos"
    puan = bm25(soru, metinler)
    kelime = [i for i in sorted(range(len(metinler)), key=lambda i: -puan[i]) if puan[i] > 0]
    if not aktif():
        return kelime + [i for i in range(len(metinler)) if i not in set(kelime)], "kelime"
    try:
        m = vektorler(klasor, metinler)
        q = gom([soru], sorgu=True)[0]
        benzer = m @ q
        anlam = list(np.argsort(-benzer))
        SON_HATA = None
        return rrf(kelime, [int(i) for i in anlam]), "hibrit"
    except Exception as e:
        SON_HATA = f"{type(e).__name__}: {str(e)[:160]}"
        if olay:
            olay(f"Anlamsal arama kullanılamadı, kelime araması ile devam: {SON_HATA}")
        return kelime + [i for i in range(len(metinler)) if i not in set(kelime)], "kelime"


def _ilk_ayar():
    try:
        with open(ayar_mod.AYAR_DOSYA, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


ayarla(_ilk_ayar())
