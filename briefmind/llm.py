"""
llm.py — Qwen3.8 (vLLM): altyazi duzeltme, bolum ozeti, birlestirme.
"""
import json
import os
import re

import httpx
from openai import OpenAI

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
GRUP_BOYU = 10

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
    "ipucudur; oradan içerik üretme. Bölümde bir şey yoksa ilgili liste boş kalır, ozet 1 cümle olur. "
    "Türkçe yaz. Yalnızca JSON döndür."
)

REDUCE_SISTEM = (
    "Sen deneyimli bir toplantı not alıcısısın. Sana bir toplantının bölüm bölüm çıkarılmış notları "
    "verilecek (her bölümün özeti, kararları, aksiyonları, açık soruları). Bunları TEK bir toplantı notuna "
    "dönüştür.\n"
    "Nasıl:\n"
    "1) ## Özet: bölüm özetlerini KRONOLOJİK sırayla birbirine bağla; toplantının akışını anlat (neyle "
    "başladı, ne tartışıldı, nereye varıldı). Bölüm özetlerinde olmayan hiçbir bilgi ekleme; genel "
    "bilginle boşluk doldurma. Uzunluk içerikle orantılı: kısa toplantı 2-3 cümle, uzun toplantı en fazla 8.\n"
    "2) ## Kararlar: bölümlerdeki kararları birleştir; aynı karar birden fazla bölümde geçiyorsa tek satır; "
    "çelişkide sonraki bölüm geçerli. Karar olmayanı (öneri/niyet) buraya taşıma.\n"
    "3) ## Aksiyonlar: tablo; aynı iş tekrar ediyorsa tek satır; sorumlu adları katılımcı listesindeki "
    "yazımla; tarih yoksa '-'.\n"
    "4) ## Açık sorular: toplantı sonunda hâlâ açık kalanlar (sonraki bölümde cevaplananları çıkar).\n"
    "5) ## Bir sonraki adım: 1-2 cümle, yalnızca kararlardan/aksiyonlardan türetilmiş.\n"
    "KESİN KURALLAR: Yalnızca bölüm notlarında geçenleri yaz. Sözlük, gündem, katılımcı listesi ya da genel "
    "bilginden konu, karar, aksiyon EKLEME. Bölüm notları azsa not da kısa olsun; yapıyı doldurmak için "
    "uydurma; boş bölümü '-' bırak. (?) işaretli belirsizlikleri koru. Türkçe, kısa ve net.\n"
    "Şu Markdown yapısını kullan:\n"
    "## Özet\n...\n\n## Kararlar\n- ...\n\n"
    "## Aksiyonlar\n| # | Madde | Sorumlu | Tarih |\n|---|---|---|---|\n| 1 | ... |\n\n"
    "## Açık sorular\n- ...\n\n## Bir sonraki adım\n..."
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
    metin = re.sub(r"```(?:json)?", "", metin).strip()
    a, b = metin.find("{"), metin.rfind("}")
    if a < 0 or b < 0:
        raise ValueError("JSON yok")
    return json.loads(re.sub(r",\s*([}\]])", r"\1", metin[a:b + 1]))


def sor(mesajlar, max_tokens, sema=None, effort="medium", temperature=0.6, dusunme=True):
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
        except Exception:
            r = client.chat.completions.create(**ortak)
    else:
        r = client.chat.completions.create(**ortak)
    c = r.choices[0]
    return (c.message.content or "").strip(), c.finish_reason


def _json_sor(mesajlar, max_tokens, sema, effort="medium", temperature=0.6, dusunme=True):
    """JSON ister; kesilirse dusunmesiz ve daha genis tekrar dener, bozuksa onarim. Basarisizsa None."""
    metin, neden = sor(mesajlar, max_tokens, sema=sema, effort=effort, temperature=temperature, dusunme=dusunme)
    if neden == "length":
        metin, neden = sor(mesajlar, int(max_tokens * 1.5), sema=sema, temperature=temperature, dusunme=False)
    try:
        return json_ayikla(metin)
    except Exception:
        onarim = [{"role": "system", "content": "Bozuk JSON'u düzelt. Yalnızca geçerli JSON döndür."},
                  {"role": "user", "content": metin}]
        onarilmis, _ = sor(onarim, max_tokens, effort="low", temperature=0.1)
        try:
            return json_ayikla(onarilmis)
        except Exception:
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
            ne = re.sub(re.escape(o), y, ne, flags=re.IGNORECASE)
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


def bolum_ozetle(blok, sira, baglam, onceki_konular=None):
    onceki = [_metin(k) for k in (onceki_konular or []) if _metin(k).strip()]
    devam = f"\nÖnceki bölümde konuşulanlar: {', '.join(onceki)}" if onceki else ""
    mesajlar = [{"role": "system", "content": MAP_SISTEM},
                {"role": "user", "content": f"{baglam}{devam}\n\nToplantının {sira}. bölümü:\n\n{blok}"}]
    # JSON cikarimi derin dusunme istemez: "low" ile dusunme tokenleri kisa kalir, cevap kesilmez
    return normalize_ozet(_json_sor(mesajlar, 3000, BOLUM_SEMASI, effort="low", temperature=0.3))


def bolum_metni(b, no):
    """Bolum notunu modele JSON yerine okunur, kompakt metin olarak verir (daha az token, daha az kayma)."""
    if "ara_not" in b:
        return f"### Ara not {no}\n{b['ara_not']}"
    aralik = f" ({b['aralik']})" if b.get("aralik") else ""
    satirlar = [f"### Bölüm {no}{aralik}"]
    if b.get("ozet"):
        satirlar.append(f"Özet: {b['ozet']}")
    if b.get("konular"):
        satirlar.append("Konular: " + "; ".join(b["konular"]))
    if b.get("kararlar"):
        satirlar.append("Kararlar:\n" + "\n".join(f"- {k}" for k in b["kararlar"]))
    if b.get("aksiyonlar"):
        satirlar.append("Aksiyonlar:\n" + "\n".join(
            f"- {a.get('madde', '')} — {a.get('sorumlu', 'belirsiz')} — {a.get('tarih', '-')}" for a in b["aksiyonlar"]))
    if b.get("acik_sorular"):
        satirlar.append("Açık sorular:\n" + "\n".join(f"- {q}" for q in b["acik_sorular"]))
    return "\n".join(satirlar)


def _birlestir(bolumler, baglam, ilerleme=print):
    icerik = "\n\n".join(bolum_metni(b, i + 1) for i, b in enumerate(bolumler))
    mesajlar = [{"role": "system", "content": REDUCE_SISTEM},
                {"role": "user", "content": f"{baglam}\n\nBölüm notları ({len(bolumler)} bölüm):\n\n{icerik}"}]
    # Olgusal birlestirme: dusuk sicaklik, kisa dusunme. Cevap kesilirse dusunmesiz ve daha genis tekrar.
    metin, neden = sor(mesajlar, 3500, effort="low", temperature=0.3)
    if neden == "length" or not metin.strip():
        ilerleme("  birleştirme kesildi, düşünmesiz tekrar deneniyor...")
        metin, neden = sor(mesajlar, 4500, temperature=0.3, dusunme=False)
    return metin


def birlestir(bolumler, baglam, ilerleme=print):
    bolumler = [b for b in bolumler if b]
    if not bolumler:
        return "_(özetlenebilen bölüm yok)_"
    if len(bolumler) <= GRUP_BOYU:
        return _birlestir(bolumler, baglam, ilerleme)
    ara = []
    for g in range(0, len(bolumler), GRUP_BOYU):
        ilerleme(f"  ara birleştirme {g // GRUP_BOYU + 1}...")
        ara.append({"ara_not": _birlestir(bolumler[g:g + GRUP_BOYU], baglam, ilerleme)})
    return _birlestir(ara, baglam, ilerleme)
