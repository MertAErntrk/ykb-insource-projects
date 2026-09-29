"""
llm.py — Qwen3.8 (vLLM): altyazi duzeltme, bolum ozeti, birlestirme.

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
import time

import httpx
from openai import BadRequestError, OpenAI, UnprocessableEntityError

CONFIG = "config.json"
_cfg = {}
if os.path.exists(CONFIG):
    try:
        with open(CONFIG, encoding="utf-8") as _f:
            _cfg = json.load(_f)
    except Exception:
        _cfg = {}

ROUTE = _cfg.get("route") or "http://localhost:8000/v1"          # config.json: LLM adresi (OpenAI uyumlu)
# OpenShift icinden: "http://<servis>.<namespace>.svc.cluster.local:8000/v1"
MODEL = _cfg.get("model") or "Qwen3.8-27B-FP8"
BAGLAM_PENCERESI = int(_cfg.get("context") or 16384)   # sunucunun max-model-len'i
MARJ = 350                                             # sablon + guvenlik payi

client = OpenAI(base_url=ROUTE, api_key="x",
                http_client=httpx.Client(verify=False, trust_env=False, timeout=900))

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
    "DEĞİLDİR; onlar 'acik_sorular'a gider. Kararı kimin verdiği belliyse belirt.\n"
    "- aksiyonlar: birinin üstlendiği somut işler; sorumlu katılımcı listesindeki yazımla, belli değilse "
    "'belirsiz'; tarih toplantı tarihine göre gerçek tarihe çevrilir ('perşembeye' -> 'YYYY-MM-DD (Perşembe)'), "
    "belli değilse '-'.\n"
    "- acik_sorular: cevaplanmadan kalan sorular, ertelenen konular, netleşmemiş öneriler.\n"
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
    "- Aksiyonlarda sorumlu adını katılımcı listesindeki yazımla ver, belli değilse 'belirsiz'; tarih yoksa '-'.\n"
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


_http = httpx.Client(verify=False, trust_env=False, timeout=30)


def token_say(metin):
    """Sunucunun tokenizer'iyla sayar; ulasamazsa kaba tahmin."""
    try:
        r = _http.post(ROUTE.replace("/v1", "/tokenize"), json={"model": MODEL, "prompt": metin})
        return int(r.json()["count"])
    except Exception:
        return token_tahmin(metin)


def _giris_token(mesajlar):
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


_DUSUNCE = re.compile(r"<think>.*?</think>", re.S)


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
                       ("genel", GENEL_SISTEM)):
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
    t0 = time.time()
    kayit = {"zaman": dt.datetime.now().strftime("%H:%M:%S"), "gorev": _gorev_adi(mesajlar),
             "dusunme": effort if dusunme else False, "sema": bool(sema)}
    try:
        metin, neden, ek = _sor(mesajlar, max_tokens, sema, effort, temperature, dusunme)
    except Exception as e:
        _gunluge_yaz(**kayit, sure_sn=round(time.time() - t0, 1), hata=f"{type(e).__name__}: {str(e)[:200]}")
        raise
    _gunluge_yaz(**kayit, **ek, finish=neden, sure_sn=round(time.time() - t0, 1), cevap_karakter=len(metin),
                 ingilizce=ingilizce_mi(metin))
    return metin, neden


def _sor(mesajlar, max_tokens, sema, effort, temperature, dusunme):
    giris = _giris_token(mesajlar)
    izin = BAGLAM_PENCERESI - giris - MARJ
    if izin < 256:
        raise ValueError(f"girdi bağlam penceresine sığmıyor ({giris} token)")
    max_tokens = min(max_tokens, izin)
    kw = {"reasoning_effort": effort} if dusunme else {"enable_thinking": False}
    ortak = dict(model=MODEL, messages=mesajlar, max_tokens=max_tokens,
                 temperature=temperature, top_p=0.95,
                 extra_body={"chat_template_kwargs": kw})
    if sema:
        try:
            r = client.chat.completions.create(
                response_format={"type": "json_schema", "json_schema": {"name": "cikti", "schema": sema}},
                **ortak)
        except (BadRequestError, UnprocessableEntityError):   # json_schema desteklenmiyor; zaman asimi tekrarlanmaz
            r = client.chat.completions.create(**ortak)
    else:
        r = client.chat.completions.create(**ortak)
    c = r.choices[0]
    u = getattr(r, "usage", None)
    dusunce = getattr(c.message, "reasoning_content", None) or getattr(c.message, "reasoning", None) or ""
    ek = {"giris_token": giris, "max_tokens": max_tokens,
          "cikti_token": getattr(u, "completion_tokens", None), "dusunce_karakter": len(dusunce)}
    return dusunce_temizle(c.message.content), c.finish_reason, ek


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
    for butce in (max_tokens, int(max_tokens * 1.6)):
        metin, neden = sor(mesajlar, butce, sema=sema, temperature=temperature, dusunme=False)
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
    v = _json_sor(mesajlar, 1200, DUZELT_SEMASI, temperature=0.2, dusunme=False)
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
            temiz["aksiyonlar"].append({"madde": _metin(a.get("madde") or ""),
                                        "sorumlu": _metin(a.get("sorumlu") or "belirsiz") or "belirsiz",
                                        "tarih": _metin(a.get("tarih") or "-") or "-"})
        elif _metin(a).strip():
            temiz["aksiyonlar"].append({"madde": _metin(a), "sorumlu": "belirsiz", "tarih": "-"})
    return temiz


def _ozet_dili(o):
    return " ".join([o["ozet"]] + o["kararlar"] + o["acik_sorular"] + [a["madde"] for a in o["aksiyonlar"]])


def bolum_ozetle(blok, sira, baglam, onceki_konular=None):
    onceki = [_metin(k) for k in (onceki_konular or []) if _metin(k).strip()]
    devam = f"\nÖnceki bölümde konuşulanlar: {', '.join(onceki)}" if onceki else ""
    kullanici = {"role": "user", "content": f"{baglam}{devam}\n\nToplantının {sira}. bölümü:\n\n{blok}"}
    mesajlar = [{"role": "system", "content": MAP_SISTEM}, kullanici]
    # JSON cikarimi derin dusunme istemez: "low" ile dusunme tokenleri kisa kalir, cevap kesilmez
    o = normalize_ozet(_json_sor(mesajlar, 3000, BOLUM_SEMASI, effort="low", temperature=0.3))
    if o and ingilizce_mi(_ozet_dili(o)):
        # Model (cogunlukla dusunme dilinin etkisiyle) Ingilizce yazdi: dusunmesiz, dil kurali vurgulu tekrar
        tekrar = [{"role": "system", "content": MAP_SISTEM + "\n" + TURKCE_KURAL}, kullanici]
        o2 = normalize_ozet(_json_sor(tekrar, 3000, BOLUM_SEMASI, temperature=0.2, dusunme=False))
        if o2 and not ingilizce_mi(_ozet_dili(o2)):
            o = o2
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
    cikti = min(6000, max(1000, int(token_say(girdi) * 1.3)))
    if _giris_token(mesajlar) + cikti + MARJ > BAGLAM_PENCERESI:
        ilerleme("  madde listesi bağlama sığmıyor, yerel birleştirme kullanıldı")
        return yedek
    try:
        v = _json_tam(mesajlar, cikti, LISTE_SEMASI)
    except Exception as e:
        ilerleme(f"  ! liste birleştirme hatası, yerel birleştirme kullanıldı: {e!r}")
        return yedek
    if not v:
        ilerleme("  ! liste birleştirme kesildi/bozuk, yerel birleştirme kullanıldı")
        return yedek
    n = normalize_ozet({"ozet": "", **v})
    sonuc = {k: n[k] for k in ("kararlar", "aksiyonlar", "acik_sorular")}
    for alan in ("kararlar", "acik_sorular"):
        sonuc[alan] = [re.sub(r"^\[B\d+\]\s*", "", x) for x in sonuc[alan]]
    for a in sonuc["aksiyonlar"]:
        a["madde"] = re.sub(r"^\[B\d+\]\s*", "", a["madde"])
    kayip = any(len(sonuc[k]) < (len(yedek[k]) + 1) // 2 for k in ("kararlar", "aksiyonlar"))
    if kayip or ingilizce_mi(_liste_dili(sonuc)):
        ilerleme("  ! liste birleştirmesi madde kaybetti ya da dili bozuk, yerel birleştirme kullanıldı")
        return yedek
    return sonuc


def _genel_sor(ozetler, liste_metni, baglam, cikti=1500):
    icerik = ("Bölüm özetleri (kronolojik):\n" + "\n".join(ozetler)
              + "\n\nBirleştirilmiş kararlar ve aksiyonlar:\n" + (liste_metni or "-"))
    kullanici = {"role": "user", "content": f"{baglam}\n\n{icerik}"}
    for sistem in (GENEL_SISTEM, GENEL_SISTEM + "\n" + TURKCE_KURAL):
        v = _json_tam([{"role": "system", "content": sistem}, kullanici], cikti, GENEL_SEMASI, temperature=0.3)
        ozet = normalize_bosluk(_metin((v or {}).get("ozet")))
        sonraki = normalize_bosluk(_metin((v or {}).get("sonraki_adim"))) or "-"
        if ozet and not ingilizce_mi(f"{ozet} {sonraki}"):
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
    yedek = {"ozet": " ".join(b["ozet"] for b in bolumler if b.get("ozet")) or "-", "sonraki_adim": "-"}
    if not ozetler:
        return yedek
    liste_metni = "\n".join([f"- Karar: {k}" for k in listeler["kararlar"]]
                            + [f"- Aksiyon: {a['madde']} ({a['sorumlu']}, {a['tarih']})" for a in listeler["aksiyonlar"]])
    cikti = 1500
    butce = BAGLAM_PENCERESI - MARJ - cikti - token_say(GENEL_SISTEM + TURKCE_KURAL + baglam + liste_metni) - 200
    for tur in range(3):
        if len(ozetler) <= 1 or token_say("\n".join(ozetler)) <= butce:
            break
        gruplar = _grupla(ozetler, butce)
        if len(gruplar) >= len(ozetler):
            break
        ilerleme(f"  özetler bağlama sığmıyor: {len(gruplar)} grupta ara özet çıkarılıyor...")
        yeni = []
        for n, g in enumerate(gruplar, 1):
            v = _genel_sor(g, "-", baglam, cikti)
            yeni.append(f"Ara özet {n}: {v['ozet']}" if v else " ".join(g))
        ozetler = yeni
    try:
        v = _genel_sor(ozetler, liste_metni, baglam, cikti)
    except Exception as e:
        ilerleme(f"  ! özet paragrafı üretilemedi: {e!r}")
        v = None
    if not v:
        ilerleme("  ! özet paragrafı kesildi ya da Türkçe değildi; bölüm özetleri sırayla kullanıldı")
        return yedek
    return v


def _hucre(x):
    return normalize_bosluk(x).replace("|", "/") or "-"


def not_markdown(genel, listeler):
    """Notun Markdown'u kodda kurulur: basliklar sabit ve Turkce, tablo hic yarim kalmaz."""
    def maddeler(lst):
        return "\n".join(f"- {normalize_bosluk(x)}" for x in lst) or "-"
    parcalar = ["## Özet", genel["ozet"] or "-", "", "## Kararlar", maddeler(listeler["kararlar"]), "",
                "## Aksiyonlar"]
    if listeler["aksiyonlar"]:
        parcalar += ["| # | Madde | Sorumlu | Tarih |", "|---|---|---|---|"]
        parcalar += [f"| {i} | {_hucre(a['madde'])} | {_hucre(a['sorumlu'])} | {_hucre(a['tarih'])} |"
                     for i, a in enumerate(listeler["aksiyonlar"], 1)]
    else:
        parcalar.append("-")
    parcalar += ["", "## Açık sorular", maddeler(listeler["acik_sorular"]), "",
                 "## Bir sonraki adım", genel.get("sonraki_adim") or "-"]
    return "\n".join(parcalar) + "\n"


def birlestir(bolumler, baglam, ilerleme=print):
    bolumler = [b for b in bolumler if b]
    if not bolumler:
        return "_(özetlenebilen bölüm yok)_"
    ilerleme("  kararlar, aksiyonlar ve açık sorular birleştiriliyor...")
    listeler = listeleri_birlestir(bolumler, baglam, ilerleme)
    ilerleme("  özet paragrafı yazılıyor...")
    genel = genel_ozet(bolumler, listeler, baglam, ilerleme)
    return not_markdown(genel, listeler)
