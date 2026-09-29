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
ROUTE = MODEL = client = _http = None
BAGLAM_PENCERESI = 16384


def ayarla(cfg):
    """LLM adresi/model/baglam penceresi ve istemciler calisirken yeniden kurulur (Ayarlar -> Kaydet
    sonrasi uygulamayi yeniden baslatmak gerekmez). cfg: config.json sozlugu."""
    global ROUTE, MODEL, BAGLAM_PENCERESI, client, _http
    cfg = cfg or {}
    ROUTE = cfg.get("route") or "http://localhost:8000/v1"          # config.json: LLM adresi (OpenAI uyumlu)
    # OpenShift icinden: "http://<servis>.<namespace>.svc.cluster.local:8000/v1"
    MODEL = cfg.get("model") or "Qwen3.8-27B-FP8"
    try:
        BAGLAM_PENCERESI = int(cfg.get("context") or 16384)   # sunucunun max-model-len'i
    except (TypeError, ValueError):
        BAGLAM_PENCERESI = 16384
    # eski istemciler kapatilmaz: o an baska bir is parcaciginda suren istek yarida kesilmesin
    client = OpenAI(base_url=ROUTE, api_key="x",
                    http_client=httpx.Client(verify=False, trust_env=False, timeout=900))
    _http = httpx.Client(verify=False, trust_env=False, timeout=30)


ayarla(_cfg)

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
    o = normalize_ozet(_json_sor(mesajlar, 3000, BOLUM_SEMASI, effort="low", temperature=0.3))
    if o and ingilizce_mi(_ozet_dili(o)):
        # Model (cogunlukla dusunme dilinin etkisiyle) Ingilizce yazdi: dusunmesiz, dil kurali vurgulu tekrar
        tekrar = [{"role": "system", "content": MAP_SISTEM + "\n" + TURKCE_KURAL}, kullanici]
        o2 = normalize_ozet(_json_sor(tekrar, 3000, BOLUM_SEMASI, temperature=0.2, dusunme=False))
        if o2 and not ingilizce_mi(_ozet_dili(o2)):
            o = o2
    if o and not o["ozet"].strip(" -.") and len(blok) > 200:
        # listeler dolu ama ozet alani bos: tam notun ozet paragrafi bu alanlardan kurulur, bos birakilmaz
        tekrar = [{"role": "system", "content": MAP_SISTEM + "\n'ozet' alanını ASLA boş bırakma."}, kullanici]
        o2 = normalize_ozet(_json_sor(tekrar, 3000, BOLUM_SEMASI, temperature=0.2, dusunme=False))
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


def _genel_sor(ozetler, liste_metni, baglam, cikti=1500):
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


def toplantiya_sor(soru, bolumler, baglam, cikti=1500):
    """bolumler: [(aralik, '[ts] Kim: metin' satirlari)], kronolojik. Soruyla en ilgili bolumler baglama
    sigdigi kadar secilir (kelime koku ortakligina gore), kronolojik sirayla verilir."""
    def kokler(m):
        return {k[:5] for k in re.findall(r"[a-z0-9]+", _sade(m)) if len(k) >= 3}
    sk = kokler(soru)
    puanli = sorted(((len(sk & kokler(metin)), i) for i, (_, metin) in enumerate(bolumler)), reverse=True)
    butce = BAGLAM_PENCERESI - MARJ - cikti - token_say(SORU_SISTEM + baglam + soru) - 200
    secilen, tok = [], 0
    for puan, i in puanli:
        t = token_say(bolumler[i][1])
        if secilen and tok + t > butce:
            continue
        if t > butce:                                 # tek bolum bile sigmiyorsa sonundan kirp
            secilen.append((i, bolumler[i][1][-int(butce * 3):]))
            break
        secilen.append((i, bolumler[i][1]))
        tok += t
    if not secilen:
        return "Bu toplantının transkripti boş."
    icerik = "\n\n".join(f"### Bölüm {i + 1} ({bolumler[i][0]})\n{metin}" for i, metin in sorted(secilen))
    mesajlar = [{"role": "system", "content": SORU_SISTEM},
                {"role": "user", "content": f"{baglam}\n\nTranskript:\n{icerik}\n\nSoru: {soru}"}]
    metin, neden = sor(mesajlar, cikti + 1500, effort="low", temperature=0.3)
    if neden == "length" or not metin.strip() or ingilizce_mi(metin):
        metin, neden = sor(mesajlar, cikti, temperature=0.3, dusunme=False)
    if len(secilen) < len(bolumler):
        metin += f"\n\n(Not: {len(bolumler)} bölümden soruyla en ilgili {len(secilen)} tanesine bakıldı.)"
    return metin
