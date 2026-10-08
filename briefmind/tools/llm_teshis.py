"""
llm_teshis.py — BriefMind LLM sunucusu teshisi. Toplanti ICERIGI kullanmaz: butun denemeler bu dosyadaki
SENTETIK metinlerle yapilir (uydurma kisi ve konular).

Kullanim (briefmind klasorunden):
  python tools\\llm_teshis.py           -> baglanti, model/baglam, tokenizer, dusunme, sema, hiz, baglam probu
  python tools\\llm_teshis.py --tam     -> + uygulamanin GERCEK boru hatti (duzeltme, bolum ozeti, not, soru)
  python tools\\llm_teshis.py --hizli   -> uzun (>30 sn) ve paralel olcumleri atla (~1 dk)

Cikti: ekrana ve llm_teshis_cikti.txt. LLM adresi <LLM_ADRES> olarak maskelenir, anahtar yazilmaz.
Paylasmadan once dosyaya bir kez goz gezdir. Bir adim hata verirse arac durmaz, hatayi yazip devam eder.
"""
import argparse
import datetime as dt
import glob
import json
import os
import platform
import re
import ssl
import sys
import threading
import time

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CIKTI_DOSYA = "llm_teshis_cikti.txt"

# ---------------------------------------------------------------- sentetik metinler (gercek icerik YOK)

SORU = ("Bir yazılım ekibinin haftalık toplantısında 'karar' ile 'aksiyon' arasındaki farkı iki örnekle, "
        "Türkçe ve kısa anlat.")

PARAGRAF = ("Ayşe Demir sprint planlamasında rapor ekranının gecikmesini anlattı; Ahmet Yılmaz test ortamının "
            "perşembeye hazır olacağını söyledi. Elif Kaya müşteri geri bildirimlerinin Jira'da toplanmasını "
            "önerdi ve ekip, kampanya modülünün canlıya geçişini bir hafta ertelemeye karar verdi. ")

KATILIMCILAR = ["Ayşe Demir", "Ahmet Yılmaz", "Elif Kaya", "Can Öztürk"]

_BOLUM_SATIRLARI = [
    [("Ayşe Demir", "Günaydın, bugün rapor ekranındaki gecikmeyi ve kampanya modülünü konuşacağız."),
     ("Ahmet Yılmaz", "Rapor ekranı açılırken veritabanı sorgusu yaklaşık sekiz saniye sürüyor."),
     ("Elif Kaya", "Müşteriler de bu gecikmeden şikâyet ediyor, geçen hafta üç kayıt açıldı."),
     ("Can Öztürk", "Sorguya indeks eklersek süre bir saniyenin altına iner diye düşünüyorum."),
     ("Ayşe Demir", "Bunu deneyelim. Can, indeks çalışmasını sen üstlenebilir misin?"),
     ("Can Öztürk", "Olur, çarşambaya kadar test ortamında denerim."),
     ("Ahmet Yılmaz", "Test ortamı şu an kapalı, perşembe sabahı tekrar açılacak."),
     ("Ayşe Demir", "O zaman Can'ın denemesi perşembeye kalsın."),
     ("Elif Kaya", "Ben müşteri kayıtlarını jandarma kaydı altında toplarım, yani Jira kaydı altında."),
     ("Ayşe Demir", "Tamam, Elif Jira'da tek bir ana kayıt açsın, diğerlerini ona bağlasın."),
     ("Ahmet Yılmaz", "Bir de raporun dışa aktarma düğmesi bazen boş dosya üretiyor."),
     ("Can Öztürk", "O hata dün komplo tit ile düzeldi, yani commit ile düzeldi, yarın canlıya çıkar."),
     ("Ayşe Demir", "Güzel. Rapor ekranı için karar şu: indeks çalışması bu sprintte yapılacak."),
     ("Elif Kaya", "Müşteriye ne zaman dönüş yapacağımızı da yazalım."),
     ("Ayşe Demir", "Perşembe akşamı sonuçları görünce Elif müşteriye bilgi versin.")],
    [("Ayşe Demir", "İkinci konu kampanya modülü. Canlıya geçiş tarihi bu cuma olarak planlanmıştı."),
     ("Ahmet Yılmaz", "Yük testleri tamamlanmadı, ödeme servisinde zaman aşımı görüyoruz."),
     ("Can Öztürk", "Zaman aşımı ödeme servisinin bağlantı havuzundan kaynaklanıyor olabilir."),
     ("Elif Kaya", "Pazarlama ekibi kampanyayı duyurmak için tarih bekliyor."),
     ("Ayşe Demir", "Riskli görünüyor. Canlıya geçişi bir hafta erteleyelim mi?"),
     ("Ahmet Yılmaz", "Bence ertelemek doğru, yük testine en az üç gün daha lazım."),
     ("Can Öztürk", "Katılıyorum, bağlantı havuzunu büyütüp testi yeniden koşarım."),
     ("Ayşe Demir", "Karar: kampanya modülünün canlıya geçişi bir hafta ertelendi."),
     ("Elif Kaya", "Pazarlama ekibine yeni tarihi ben iletirim, bugün öğleden sonra."),
     ("Ahmet Yılmaz", "Yük testi raporunu pazartesi paylaşırım."),
     ("Ayşe Demir", "Ödeme servisi ekibiyle ayrıca konuşmamız gerekir mi?"),
     ("Can Öztürk", "Bağlantı havuzu yetmezse evet, ama önce kendi testimizi bitirelim."),
     ("Elif Kaya", "Kampanya görsellerinin onayı da bekleniyor, onu kim takip edecek?"),
     ("Ayşe Demir", "Görsel onayını şimdilik açık bırakalım, tasarım ekibine sorarız.")],
    [("Ayşe Demir", "Son olarak yeni ekip üyesinin oryantasyonu var."),
     ("Ahmet Yılmaz", "Erişim talepleri açıldı ama veritabanı yetkisi hâlâ onaylanmadı."),
     ("Elif Kaya", "Oryantasyon belgesini güncelledim, paylaşım klasörüne koydum."),
     ("Can Öztürk", "İlk hafta benimle eşli çalışabilir, indeks işini birlikte yaparız."),
     ("Ayşe Demir", "Çok iyi. Ahmet, veritabanı yetkisinin takibini sen yapar mısın?"),
     ("Ahmet Yılmaz", "Yaparım, yarın güvenlik ekibine hatırlatırım."),
     ("Elif Kaya", "Ekip içi sunum için bir tarih belirleyelim mi?"),
     ("Ayşe Demir", "Gelecek salı on birde kısa bir tanışma sunumu yapalım."),
     ("Can Öztürk", "Sunumu ben hazırlarım, yarım saatlik olur."),
     ("Ayşe Demir", "Toplantıyı burada bitirelim, herkese teşekkürler.")],
]


def sentetik_transkript():
    """[(aralik, [(ts, kim, metin)])]: uc bolumluk uydurma toplanti (gercek icerik yok)."""
    bolumler, dakika = [], 0
    for satirlar in _BOLUM_SATIRLARI:
        zamanli = []
        for kim, metin in satirlar:
            zamanli.append((f"10:{dakika // 60:02d}:{dakika % 60:02d}", kim, metin))
            dakika += 20
        bolumler.append((f"{zamanli[0][0]}–{zamanli[-1][0]}", zamanli))
    return bolumler


def uzun_metin(token, kr_token=3.0):
    """Yaklasik `token` token'lik sentetik Turkce metin (numarali paragraflar: onbellek tekrarini engeller)."""
    hedef, parcalar, i = int(token * kr_token), [], 0
    while sum(len(p) for p in parcalar) < hedef:
        i += 1
        parcalar.append(f"[{i}] {PARAGRAF}")
    return "".join(parcalar)[:hedef]


# ---------------------------------------------------------------- rapor

class Rapor:
    """Satirlari ekrana ve listeye yazar; adresleri maskeler; bulgulari (oncelikli) toplar.
    yaz_fn verilirse (tools/teshis.py) yazma ve maskeleme ona birakilir."""

    SIRA = {"YUKSEK": 0, "ORTA": 1, "DUSUK": 2, "BILGI": 3}

    def __init__(self, yaz_fn=None):
        self.satirlar, self.gizli, self.bulgular, self.oneriler = [], [], [], {}
        self._yaz_fn = yaz_fn

    def gizle(self, adres, etiket="<LLM_ADRES>"):
        from urllib.parse import urlparse
        if not adres:
            return
        self.gizli.append((adres, etiket))
        host = urlparse(adres).hostname
        if host:
            self.gizli.append((host, etiket))
            parcalar = host.split(".")
            if not re.match(r"^[\d.]+$", host) and len(parcalar) >= 4:
                # alan adi (kurum ici) da gorunmesin: sertifika hatalari '*.<alan>' yazar
                for k in range(len(parcalar) - 1, 2, -1):
                    self.gizli.append((".".join(parcalar[-k:]), "<ALAN>"))

    def maskele(self, s):
        s = str(s)
        for gercek, yerine in self.gizli:
            if gercek:
                s = s.replace(gercek, yerine)
        return s

    def yaz(self, *satirlar):
        for s in satirlar:
            if self._yaz_fn:
                self._yaz_fn(s)
                continue
            s = self.maskele(s)
            self.satirlar.append(s)
            print(s, flush=True)

    def baslik(self, s):
        self.yaz("", "=" * 72, s, "=" * 72)

    def bulgu(self, seviye, metin, oneri=None):
        self.bulgular.append((seviye, metin, oneri))
        self.yaz(f"  >> [{seviye}] {metin}" + (f"  → {oneri}" if oneri else ""))

    def config_oner(self, anahtar, deger, neden):
        self.oneriler[anahtar] = (deger, neden)

    def ozet(self):
        self.baslik("TEŞHİS ÖZETİ (öncelik sırasıyla)")
        if not self.bulgular:
            self.yaz("  Sorun bulunmadı.")
        for seviye, metin, oneri in sorted(self.bulgular, key=lambda b: self.SIRA.get(b[0], 9)):
            self.yaz(f"- [{seviye}] {metin}")
            if oneri:
                self.yaz(f"      öneri: {oneri}")
        if self.oneriler:
            self.yaz("", "Önerilen config.json değerleri:")
            for k, (v, neden) in self.oneriler.items():
                self.yaz(f"  \"{k}\": {json.dumps(v, ensure_ascii=False)}    ({neden})")

    def kaydet(self, yol):
        with open(yol, "w", encoding="utf-8") as f:
            f.write("\n".join(self.satirlar) + "\n")


# ---------------------------------------------------------------- yardimcilar

def _kisa(s, n=300):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[:n] + "…"


def _istisna(e):
    parcalar, x = [], e
    while x is not None and len(parcalar) < 4:
        p = f"{type(x).__name__}: {_kisa(x, 200)}"
        if p not in parcalar:
            parcalar.append(p)
        x = x.__cause__ or (None if x.__suppress_context__ else x.__context__)
    return " ← ".join(parcalar)


def etiketler(icerik):
    """content icinde gorulen dusunce/ozel etiketler (dusunce_temizle'nin tanimadigi bir etiket var mi?)."""
    bulunan = sorted(set(re.findall(r"</?(?:think|thinking|reasoning|reflection|analysis)>", icerik or "")))
    bulunan += sorted(set(re.findall(r"<\|[a-z_]+\|>", icerik or "")))
    if re.match(r"\s*(Thinking|Reasoning|Okay,|Let me|We need)", icerik or ""):
        bulunan.append("(İngilizce düşünce önekli düz metin)")
    return bulunan


def sema_uyuyor(icerik, gerekli):
    """content dogrudan (ya da dusunce temizlenince) gecerli JSON mu ve zorunlu alanlari var mi."""
    metin = re.sub(r"<(think|thinking|reasoning)>.*?</\1>", "", icerik or "", flags=re.S).strip()
    dogrudan = metin.startswith("{")
    try:
        a, b = metin.find("{"), metin.rfind("}")
        v = json.loads(metin[a:b + 1]) if a >= 0 < b else None
    except Exception:
        v = None
    if not isinstance(v, dict):
        return False, "JSON değil"
    eksik = [k for k in gerekli if k not in v]
    if eksik:
        return False, f"eksik alan: {eksik}"
    return True, "doğrudan JSON" if dogrudan else "JSON metin içinde (önünde/arkasında yazı var)"


class Istemci:
    """httpx istemcisi + hic yukseltmeyen istek yardimcilari."""

    def __init__(self, cfg, verify=None, trust_env=None, timeout=600):
        import httpx
        if verify is None:
            yol = _ca_bundle(cfg)
            verify = ssl.create_default_context(cafile=yol) if yol else False
        if trust_env is None:
            trust_env = bool(cfg.get("llm_proxy", False))
        anahtar = str(cfg.get("llm_key") or cfg.get("api_key") or "").strip()
        bas = {"Authorization": f"Bearer {anahtar}"} if anahtar else {}
        self.h = httpx.Client(verify=verify, trust_env=trust_env, timeout=httpx.Timeout(timeout, connect=15.0),
                              headers=bas)

    def iste(self, yontem, url, govde=None, timeout=None):
        """-> {"durum", "sure", "json", "metin", "tur", "hata"}"""
        t0 = time.time()
        try:
            kw = {"json": govde} if govde is not None else {}
            if timeout:
                kw["timeout"] = timeout
            r = self.h.request(yontem, url, **kw)
        except Exception as e:
            return {"durum": None, "sure": time.time() - t0, "json": None, "metin": "", "tur": "", "hata": _istisna(e)}
        sonuc = {"durum": r.status_code, "sure": time.time() - t0, "json": None, "metin": r.text,
                 "tur": r.headers.get("content-type", ""), "hata": None}
        try:
            sonuc["json"] = r.json()
        except Exception:
            pass
        return sonuc

    def akis(self, url, govde, timeout=None):
        """stream=True sohbet: ilk token suresi (TTFT), toplam sure, uretilen token (usage), finish."""
        t0, ilk, icerik, dusunce, kullanim, neden, parca = time.time(), None, [], [], {}, None, 0
        govde = dict(govde, stream=True, stream_options={"include_usage": True})
        try:
            kw = {"timeout": timeout} if timeout else {}
            with self.h.stream("POST", url, json=govde, **kw) as r:
                if r.status_code != 200:
                    r.read()
                    return {"durum": r.status_code, "sure": time.time() - t0, "hata": _kisa(r.text, 400),
                            "tur": r.headers.get("content-type", "")}
                for satir in r.iter_lines():
                    if not satir.startswith("data:"):
                        continue
                    veri = satir[5:].strip()
                    if veri == "[DONE]":
                        break
                    try:
                        v = json.loads(veri)
                    except Exception:
                        continue
                    if v.get("usage"):
                        kullanim = v["usage"]
                    if v.get("error"):
                        return {"durum": 200, "sure": time.time() - t0, "hata": f"akış içi hata: {_kisa(v['error'])}"}
                    for c in v.get("choices") or []:
                        d = c.get("delta") or {}
                        if d.get("content") or d.get("reasoning_content") or d.get("reasoning"):
                            parca += 1
                            if ilk is None:
                                ilk = time.time() - t0
                        icerik.append(d.get("content") or "")
                        dusunce.append(d.get("reasoning_content") or d.get("reasoning") or "")
                        neden = c.get("finish_reason") or neden
        except Exception as e:
            return {"durum": None, "sure": time.time() - t0, "hata": _istisna(e)}
        return {"durum": 200, "sure": time.time() - t0, "ttft": ilk, "usage": kullanim, "finish": neden,
                "icerik": "".join(icerik), "dusunce": "".join(dusunce), "parca": parca, "hata": None}


def _ca_bundle(cfg):
    yol = (cfg.get("ca_bundle") or "").strip() if isinstance(cfg.get("ca_bundle"), str) else ""
    return yol if yol and os.path.isfile(yol) else False


def _kok(route):
    return re.sub(r"/v\d+$", "", (route or "").rstrip("/"))


def _sohbet(ctx, mesajlar, max_tokens, kw=None, ek=None):
    govde = {"model": ctx["model"], "messages": mesajlar, "max_tokens": max_tokens, "temperature": 0.3,
             "top_p": 0.95}
    if kw is not None:
        govde["chat_template_kwargs"] = kw
    govde.update(ek or {})
    return ctx["h"].iste("POST", ctx["route"] + "/chat/completions", govde)


def _cevap_coz(r):
    v = r.get("json") or {}
    c = (v.get("choices") or [{}])[0]
    m = c.get("message") or {}
    alan = "reasoning_content" if m.get("reasoning_content") else ("reasoning" if m.get("reasoning") else None)
    u = v.get("usage") or {}
    return {"icerik": m.get("content") or "", "dusunce": m.get(alan) if alan else "", "alan": alan,
            "finish": c.get("finish_reason"), "usage": u,
            "dusunce_token": (u.get("completion_tokens_details") or {}).get("reasoning_tokens")}


def _hata_satiri(r):
    html = "html" in (r.get("tur") or "") or "<html" in (r.get("metin") or "").lower()
    return (f"HTTP {r.get('durum')} ({r.get('sure', 0):.1f}s){' HTML gövde (route/proxy)' if html else ''}: "
            f"{_kisa(r.get('hata') or r.get('metin'), 400)}")


def _istek_hatasi(R, ctx, r, etiket):
    """Sohbet istegi basarisiz: satiri yazar; 5xx/HTML (route/proxy) ve 404 (model/yol) bir kez bulgu olur."""
    R.yaz(f"  [{etiket}] {_hata_satiri(r)}")
    html = "html" in (r.get("tur") or "") or "<html" in (r.get("metin") or "").lower()
    if (r.get("durum") in (502, 503, 504) or html) and not ctx.get("_5xx"):
        ctx["_5xx"] = True
        R.bulgu("YUKSEK", f"akışsız sohbet isteği HTTP {r.get('durum')}{' (HTML: route/proxy)' if html else ''} "
                          f"{r.get('sure', 0):.0f} sn sonra ({etiket})",
                "uygulama varsayılan olarak akışlı istek kullanır (config.json 'llm_akis': true); route zaman "
                "aşımıysa haproxy.router.openshift.io/timeout=600s")
    elif r.get("durum") == 404 and not ctx.get("_404"):
        ctx["_404"] = True
        R.bulgu("YUKSEK", f"sohbet isteği 404: {_kisa(r.get('metin'), 200)}", "model adı / adres '/v1' kontrol edin")
    elif r.get("durum") is None and not ctx.get("_baglanti"):
        ctx["_baglanti"] = True
        R.bulgu("YUKSEK", f"sohbet isteği bağlantı hatası: {_kisa(r.get('hata'), 200)}")


# ---------------------------------------------------------------- adimlar

def ortam(R, cfg):
    R.baslik("1) Ortam ve config.json (LLM alanları)")
    R.yaz(f"Python {sys.version.split()[0]} · {platform.platform()}")
    for p in ("openai", "httpx"):
        try:
            m = __import__(p)
            R.yaz(f"  {p:8} {getattr(m, '__version__', '?')}")
        except Exception as e:
            R.yaz(f"  {p:8} YOK ({type(e).__name__})")
    route = cfg.get("route") or ""
    R.yaz(f"  route: {route or '(yok)'}")
    if route.rstrip("/") != route:
        R.bulgu("ORTA", "route sonunda '/' var", "sondaki '/' silinmeli (uygulama artık kendisi düzeltir)")
    if route and not re.search(r"/v\d+/?$", route):
        R.bulgu("YUKSEK", "route '/v1' ile bitmiyor", "LLM adresi https://<sunucu>/v1 biçiminde olmalı")
    for k in ("model", "context", "llm_akis", "llm_proxy", "llm_tekrar", "llm_zaman_asimi", "parca_token", "paralel"):
        R.yaz(f"  {k}: {cfg.get(k)!r}")
    R.yaz(f"  llm_key: {'(ayarlı)' if cfg.get('llm_key') or cfg.get('api_key') else '(yok)'}")
    ca = cfg.get("ca_bundle") or ""
    R.yaz(f"  ca_bundle: {'(yok — TLS doğrulaması kapalı)' if not ca else ('dosya var' if _ca_bundle(cfg) else 'AYARLI AMA DOSYA YOK')}")
    for v in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy"):
        if os.environ.get(v):
            R.yaz(f"  ortam değişkeni {v}: (ayarlı)")


def baglanti(R, ctx, cfg):
    R.baslik("2) Bağlantı: TLS doğrulaması × sistem proxy'si (GET /v1/models)")
    secenekler = []
    if _ca_bundle(cfg):
        secenekler.append(("ca_bundle", ssl.create_default_context(cafile=_ca_bundle(cfg))))
    secenekler += [("doğrulama kapalı", False), ("sistem sertifikaları", True)]
    uygulama = ("ca_bundle" if _ca_bundle(cfg) else "doğrulama kapalı", bool(cfg.get("llm_proxy", False)))
    calisan = []
    for ad, verify in secenekler:
        for trust in (False, True):
            try:
                ist = Istemci(cfg, verify=verify, trust_env=trust, timeout=20)
                r = ist.iste("GET", ctx["route"] + "/models")
            except Exception as e:
                r = {"durum": None, "sure": 0, "hata": _istisna(e)}
            isaret = "  ← uygulamanın kullandığı" if (ad, trust) == uygulama else ""
            durum = f"HTTP {r['durum']}" if r["durum"] else "HATA"
            R.yaz(f"  TLS={ad:22} proxy={'açık ' if trust else 'kapalı'} → {durum} {r['sure']:.1f}s "
                  f"{'' if r['durum'] == 200 else _kisa(r.get('hata') or r.get('metin'), 220)}{isaret}")
            if r["durum"] == 200:
                calisan.append((ad, trust, ist))
    if not calisan:
        R.bulgu("YUKSEK", "LLM adresine hiçbir bağlantı biçimiyle ulaşılamadı",
                "adres doğru mu, VPN/iş ağı açık mı? Tarayıcıda <adres>/v1/models açılıyor mu?")
        return False
    secilen = next((c for c in calisan if (c[0], c[1]) == uygulama), None)
    if not secilen:
        secilen = calisan[0]
        R.bulgu("YUKSEK", f"uygulamanın bağlantı biçimi çalışmıyor; çalışan: TLS={secilen[0]}, "
                          f"proxy={'açık' if secilen[1] else 'kapalı'}",
                "proxy açık çalışıyorsa config.json 'llm_proxy': true; ca_bundle sorunluysa yeni sunucunun kök "
                "sertifikasını dışa aktarın ya da 'ca_bundle'ı geçici olarak boşaltın")
        if secilen[1]:
            R.config_oner("llm_proxy", True, "sunucuya yalnız sistem proxy'si üzerinden ulaşılıyor")
    ctx["h"] = secilen[2]
    return True


def modeller(R, ctx, cfg):
    R.baslik("3) Model ve bağlam penceresi (GET /v1/models, /version)")
    r = ctx["h"].iste("GET", ctx["route"] + "/models")
    veri = ((r.get("json") or {}).get("data") or []) if r["durum"] == 200 else []
    if r["durum"] != 200:
        R.bulgu("YUKSEK", f"/v1/models başarısız: {_hata_satiri(r)}")
    for m in veri:
        R.yaz(f"  model id: {m.get('id')!r}  max_model_len: {m.get('max_model_len')}  root: {m.get('root')!r}  "
              f"owned_by: {m.get('owned_by')!r}")
    s = ctx["h"].iste("GET", _kok(ctx["route"]) + "/version", timeout=10)
    R.yaz(f"  vLLM sürümü: {(s.get('json') or {}).get('version') if s['durum'] == 200 else 'alınamadı (HTTP ' + str(s['durum']) + ')'}")
    idler = [m.get("id") for m in veri if m.get("id")]
    cm = (cfg.get("model") or "").strip()
    if cm and cm in idler:
        R.yaz(f"  config 'model' {cm!r}: EŞLEŞİYOR")
        ctx["model"] = cm
    elif idler:
        ctx["model"] = idler[0] if len(idler) == 1 else (cm or idler[0])
        R.bulgu("YUKSEK" if cm else "BILGI",
                f"config 'model' {cm or '(boş)'!r} sunucuda {'YOK — UYUŞMUYOR' if cm else 'belirtilmemiş'}; "
                f"sunucudaki: {idler}",
                f"config.json \"model\": \"{idler[0]}\" (ya da boş bırakın: uygulama tek modeli kendisi seçer)"
                if len(idler) == 1 else "config.json 'model' alanına listedeki adlardan birini yazın")
        if cm:
            R.config_oner("model", idler[0] if len(idler) == 1 else "<listeden biri>", "sunucudaki model adı")
        R.yaz(f"  testlerde kullanılan model: {ctx['model']!r}")
    else:
        ctx["model"] = cm
    mml = next((m.get("max_model_len") for m in veri if m.get("id") == ctx["model"]), None)
    ctx_cfg = cfg.get("context")
    try:
        ctx_cfg = int(ctx_cfg or 0)
    except (TypeError, ValueError):
        ctx_cfg = 0
    if mml:
        R.yaz(f"  max_model_len: {mml}   config 'context': {ctx_cfg or '(yok/0 → sunucudan)'}")
        if ctx_cfg and ctx_cfg > mml:
            R.bulgu("YUKSEK", f"config 'context' ({ctx_cfg}) sunucunun penceresinden ({mml}) BÜYÜK: istekler 400 "
                              "'maximum context length' alır (uygulama artık sunucunun değerini kullanır)",
                    "config.json 'context': 0 (sunucudan otomatik)")
            R.config_oner("context", 0, f"sunucu {mml}")
        elif ctx_cfg and ctx_cfg < mml:
            R.bulgu("DUSUK", f"config 'context' ({ctx_cfg}) sunucudan ({mml}) küçük: kapasite kullanılmıyor",
                    "config.json 'context': 0")
            R.config_oner("context", 0, f"sunucu {mml}")
    else:
        R.bulgu("ORTA", "sunucu max_model_len bildirmiyor; pencere config/16384 varsayılır",
                "config.json 'context' değerini sunucunun --max-model-len değerine eşitleyin")
    ctx["pencere"] = int(mml or ctx_cfg or 16384)


def tokenizer(R, ctx):
    R.baslik("4) Tokenizer (/tokenize) ve sunucunun gerçek sayımı (usage.prompt_tokens)")
    url = _kok(ctx["route"]) + "/tokenize"
    metin = uzun_metin(400)
    mesajlar = [{"role": "system", "content": "Kısa ve Türkçe cevap ver."}, {"role": "user", "content": metin}]
    p = ctx["h"].iste("POST", url, {"model": ctx["model"], "prompt": metin}, timeout=30)
    n_p = (p.get("json") or {}).get("count") if p["durum"] == 200 else None
    R.yaz(f"  prompt biçimi   → HTTP {p['durum']} {p['sure']:.2f}s  token: {n_p}"
          + (f"  karakter/token: {len(metin) / n_p:.2f}" if n_p else f"  {_kisa(p.get('hata') or p.get('metin'), 200)}"))
    m = ctx["h"].iste("POST", url, {"model": ctx["model"], "messages": mesajlar, "add_generation_prompt": True},
                      timeout=30)
    n_m = (m.get("json") or {}).get("count") if m["durum"] == 200 else None
    R.yaz(f"  messages biçimi → HTTP {m['durum']} {m['sure']:.2f}s  token: {n_m}  "
          f"max_model_len: {(m.get('json') or {}).get('max_model_len')}"
          + ("" if n_m else f"  {_kisa(m.get('hata') or m.get('metin'), 200)}"))
    s = _sohbet(ctx, mesajlar, 1, {"enable_thinking": False})
    gercek = ((s.get("json") or {}).get("usage") or {}).get("prompt_tokens") if s["durum"] == 200 else None
    tahmin = len(metin) // 3 + 60
    R.yaz(f"  usage.prompt_tokens (sohbet): {gercek}   uygulamanın tahmini (karakter/3 + 60): {tahmin}")
    ctx["kr_token"] = len(metin) / n_p if n_p else 3.0
    if not n_p:
        R.bulgu("ORTA", f"/tokenize kullanılamıyor ({_hata_satiri(p)}); uygulama token sayısını tahmin eder",
                "route yalnızca /v1 yayınlıyorsa sorun değil; tahmin Türkçe için ~3 karakter/token")
    elif p["sure"] > 2:
        R.bulgu("ORTA", f"/tokenize yavaş ({p['sure']:.1f}s): her LLM çağrısı öncesi bu kadar beklenir")
    if gercek and tahmin and gercek > tahmin * 1.15:
        R.bulgu("ORTA", f"karakter/3 tahmini sunucunun sayımından %{100 * (gercek - tahmin) / gercek:.0f} düşük: "
                        "tokenize yoksa bağlam taşması riski", "/tokenize'ın erişilebilir olması önerilir")
    if gercek and n_m:
        R.yaz(f"  messages sayımı ile usage farkı: {gercek - n_m:+d} token (0'a yakın olmalı)")


def dusunme(R, ctx):
    R.baslik("5) Düşünme kontrolü (varsayılan / reasoning_effort / enable_thinking) ve içerik etiketleri")
    mesajlar = [{"role": "system", "content": "Kısa ve Türkçe cevap ver."}, {"role": "user", "content": SORU}]
    olcum = {}
    for etiket, kw, mt in (("varsayılan", None, 3000), ("effort=low", {"reasoning_effort": "low"}, 3000),
                           ("effort=medium", {"reasoning_effort": "medium"}, 4000),
                           ("enable_thinking=False", {"enable_thinking": False}, 800),
                           ("düşünmeli, max_tokens=120 (kesilme)", None, 120)):
        r = _sohbet(ctx, mesajlar, mt, kw)
        if r["durum"] != 200:
            _istek_hatasi(R, ctx, r, etiket)
            if r["durum"] == 400 and kw:
                R.bulgu("ORTA", f"chat_template_kwargs {kw} 400 aldı", "sunucu bu şablon parametresini reddediyor")
            continue
        c = _cevap_coz(r)
        ct = c["usage"].get("completion_tokens") or 0
        olcum[etiket] = c
        R.yaz(f"  [{etiket}] finish={c['finish']} {r['sure']:.1f}s  prompt={c['usage'].get('prompt_tokens')} "
              f"çıktı={ct} ({ct / max(r['sure'], 0.01):.0f} tok/sn)  düşünce_alanı={c['alan'] or 'yok'} "
              f"düşünce_karakter={len(c['dusunce'] or '')} düşünce_token={c['dusunce_token']}  "
              f"içerikteki_etiketler={etiketler(c['icerik']) or '-'}")
        R.yaz(f"      içerik ilk 160: {c['icerik'][:160]!r}")
    v, kapali, low = olcum.get("varsayılan"), olcum.get("enable_thinking=False"), olcum.get("effort=low")
    if v and not v["alan"] and "</think>" in v["icerik"]:
        R.bulgu("ORTA", "sunucuda reasoning parser yok: düşünce cevabın içine (<think>) düşüyor",
                "vLLM'e --reasoning-parser qwen3 eklenmeli; uygulama <think> bloğunu temizler ama bütçe harcanır")
    for c in olcum.values():
        tanimsiz = [e for e in etiketler(c["icerik"]) if "think" not in e]
        if tanimsiz:
            R.bulgu("ORTA", f"içerikte tanınmayan etiket/önek: {tanimsiz}", "llm.dusunce_temizle genişletilmeli")
            break
    if kapali and (len(kapali["dusunce"] or "") > 50 or "</think>" in kapali["icerik"]):
        R.bulgu("YUKSEK", "enable_thinking=False düşünmeyi KAPATMIYOR (yeni şablon tanımıyor)",
                "düşünmesiz çağrılar (düzeltme, liste, genel özet) kesilip yerel yedeğe düşebilir: şablon "
                "parametresi değişmeli (teşhis çıktısını paylaşın)")
    if v and low and len(v["dusunce"] or "") > 0:
        oran = len(low["dusunce"] or "") / max(1, len(v["dusunce"] or ""))
        R.yaz(f"  effort=low düşünce uzunluğu / varsayılan: {oran:.2f}")
        if oran > 0.8:
            R.bulgu("ORTA", "reasoning_effort=low düşünmeyi kısaltmıyor (şablon tanımıyor olabilir)",
                    "bölüm özeti ilk çağrıda uzun düşünüp kesilebilir; ölçüme göre düşünmesiz ilk çağrı düşünülmeli")
    ctx["dusunme"] = olcum


def sema(R, ctx):
    R.baslik("6) json_schema (yapılandırılmış çıktı) düşünmeli / düşünmesiz")
    gerekli = ["ozet", "kararlar", "aksiyonlar"]
    sema_ = {"type": "object", "properties": {"ozet": {"type": "string"},
                                              "kararlar": {"type": "array", "items": {"type": "string"}},
                                              "aksiyonlar": {"type": "array", "items": {"type": "string"}}},
             "required": gerekli}
    rf = {"type": "json_schema", "json_schema": {"name": "cikti", "schema": sema_}}
    mesajlar = [{"role": "system", "content": "Toplantı metnini JSON olarak özetle. Yalnızca JSON döndür."},
                {"role": "user", "content": PARAGRAF * 2}]
    sonuc = {}
    for etiket, kw in (("düşünmesiz", {"enable_thinking": False}), ("effort=low", {"reasoning_effort": "low"}),
                       ("varsayılan düşünme", None)):
        r = _sohbet(ctx, mesajlar, 3000, kw, {"response_format": rf})
        if r["durum"] != 200:
            _istek_hatasi(R, ctx, r, etiket)
            if r["durum"] in (400, 422):
                R.bulgu("ORTA", f"json_schema reddedildi ({etiket})", "uygulama şemasız devam eder; çıktı JSON "
                                                                       "olmayabilir, onarım çağrısı artar")
            continue
        c = _cevap_coz(r)
        uyar, neden = sema_uyuyor(c["icerik"], gerekli)
        sonuc[etiket] = uyar
        R.yaz(f"  [{etiket}] {r['sure']:.1f}s finish={c['finish']} şema_uygulandı={'EVET' if uyar else 'HAYIR'} "
              f"({neden})  düşünce_karakter={len(c['dusunce'] or '')}  içerik ilk 120: {c['icerik'][:120]!r}")
    if sonuc.get("düşünmesiz") is False:
        R.bulgu("YUKSEK", "düşünmesiz json_schema bile uygulanmıyor", "vLLM structured output (guided decoding) "
                                                                     "kapalı olabilir; uygulama JSON onarımına düşer")
    if sonuc.get("effort=low") is False and sonuc.get("düşünmesiz"):
        R.bulgu("BILGI", "düşünmeli çağrıda şema uygulanmıyor (eski sunucudaki gibi)",
                "bölüm özetinin ilk (düşünmeli) çağrısı boşa gidebilir; uygulama düşünmesiz tekrarla telafi eder")
    ctx["sema"] = sonuc


def hiz(R, ctx, hizli=False):
    R.baslik("7) Hız: üretim (tok/sn), ilk token süresi, ön işleme (prefill), route zaman aşımı")
    url = ctx["route"] + "/chat/completions"
    yaz_istek = [{"role": "user", "content": "Bir sprint toplantısının ayrıntılı tutanağını uzun uzun, Türkçe yaz; "
                                             "ad olarak yalnızca 'A', 'B', 'C' kullan."}]

    def govde(mt, en_az, kw=None, mesajlar=None):
        g = {"model": ctx["model"], "messages": mesajlar or yaz_istek, "max_tokens": mt, "temperature": 0.7,
             "chat_template_kwargs": kw if kw is not None else {"enable_thinking": False}}
        if en_az and ctx.get("min_tokens", True):
            g["min_tokens"] = en_az
        return g

    r = ctx["h"].iste("POST", url, govde(700, 600))
    if r["durum"] == 400 and "min_tokens" in (r.get("metin") or ""):
        ctx["min_tokens"] = False
        r = ctx["h"].iste("POST", url, govde(700, 0))
    if r["durum"] == 200:
        c = _cevap_coz(r)
        ct = c["usage"].get("completion_tokens") or 0
        R.yaz(f"  akışsız, düşünmesiz ~600 token: {ct} token {r['sure']:.1f}s → {ct / r['sure']:.0f} tok/sn")
        ctx["tok_sn"] = ct / max(r["sure"], 0.01)
    else:
        _istek_hatasi(R, ctx, r, "akışsız 600")
    a = ctx["h"].akis(url, govde(2000, 1800))
    if not a.get("hata"):
        ct = (a.get("usage") or {}).get("completion_tokens") or a.get("parca", 0)
        uretim = a["sure"] - (a.get("ttft") or 0)
        R.yaz(f"  akışlı, düşünmesiz ~2000 token: ilk token {a.get('ttft') or 0:.2f}s, {ct} token {a['sure']:.1f}s → "
              f"{ct / max(uretim, 0.01):.0f} tok/sn (finish={a.get('finish')})")
    else:
        R.yaz(f"  akışlı 2000: HTTP {a.get('durum')} {a['sure']:.1f}s {a.get('hata')}")
        R.bulgu("ORTA", "akışlı (stream) istek başarısız", "config.json 'llm_akis': false deneyin")
        R.config_oner("llm_akis", False, "akışlı istek bu sunucuda çalışmadı")
    d = ctx["h"].akis(url, govde(3000, 0, {"reasoning_effort": "low"}, [{"role": "user", "content": SORU}]))
    if not d.get("hata"):
        ct = (d.get("usage") or {}).get("completion_tokens") or 0
        R.yaz(f"  akışlı, düşünmeli (low): {ct} token {d['sure']:.1f}s, düşünce {len(d.get('dusunce') or '')} kr, "
              f"finish={d.get('finish')}")
    p_tok = min(4000, ctx.get("pencere", 16384) // 3)
    p = ctx["h"].iste("POST", url, govde(16, 0, mesajlar=[{"role": "user", "content": uzun_metin(p_tok, ctx.get("kr_token", 3.0))
                                                                   + "\n\nBu metni tek kelimeyle özetle."}]))
    if p["durum"] == 200:
        pt = ((p.get("json") or {}).get("usage") or {}).get("prompt_tokens") or 0
        R.yaz(f"  ön işleme: {pt} token girdi + 16 çıktı {p['sure']:.1f}s → ~{pt / max(p['sure'], 0.01):.0f} tok/sn")
    if hizli:
        R.yaz("  (--hizli: uzun istek / route zaman aşımı denemesi atlandı)")
        return
    hedef = max(1000, min(7000, ctx.get("pencere", 16384) - 1500))
    R.yaz(f"  uzun istek (~{hedef} token, 100-200 tok/sn'de 30 sn'yi aşar) akışsız ve akışlı deneniyor...")
    u = ctx["h"].iste("POST", url, govde(hedef + 200, hedef))
    if u["durum"] == 200:
        ct = ((u.get("json") or {}).get("usage") or {}).get("completion_tokens") or 0
        R.yaz(f"    akışsız: HTTP 200 {ct} token {u['sure']:.1f}s")
    else:
        R.yaz(f"    akışsız: {_hata_satiri(u)}")
    ua = ctx["h"].akis(url, govde(hedef + 200, hedef))
    R.yaz("    akışlı : " + (f"HTTP 200 {(ua.get('usage') or {}).get('completion_tokens')} token {ua['sure']:.1f}s"
                              if not ua.get("hata") else f"HTTP {ua.get('durum')} {ua['sure']:.1f}s {ua.get('hata')}"))
    if u["durum"] != 200 and not ua.get("hata"):
        R.bulgu("YUKSEK", f"uzun AKIŞSIZ istek {u['sure']:.0f} sn sonra başarısız ({u['durum']}), akışlı istek "
                          "çalışıyor: OpenShift route zaman aşımı",
                "config.json 'llm_akis': true (varsayılan); kalıcı çözüm route'a "
                "haproxy.router.openshift.io/timeout=600s")
        R.config_oner("llm_akis", True, "route zaman aşımı akışsız uzun istekleri kesiyor")
    elif u["durum"] != 200 and ua.get("hata"):
        R.bulgu("YUKSEK", "uzun istekler akışlı ve akışsız başarısız", _hata_satiri(u))


def paralel(R, ctx):
    R.baslik("8) Eşzamanlılık: 1 / 2 / 4 paralel akış (her biri ~500 token)")
    url = ctx["route"] + "/chat/completions"
    g = {"model": ctx["model"], "max_tokens": 600, "temperature": 0.7,
         "messages": [{"role": "user", "content": "Bir proje toplantısını Türkçe ve uzunca anlat."}],
         "chat_template_kwargs": {"enable_thinking": False}}
    if ctx.get("min_tokens", True):
        g["min_tokens"] = 500
    for n in (1, 2, 4):
        sonuclar = [None] * n

        def is_(i):
            sonuclar[i] = ctx["h"].akis(url, g)
        t0 = time.time()
        ipler = [threading.Thread(target=is_, args=(i,)) for i in range(n)]
        for t in ipler:
            t.start()
        for t in ipler:
            t.join()
        toplam = time.time() - t0
        tok = [(s.get("usage") or {}).get("completion_tokens") or 0 for s in sonuclar if s and not s.get("hata")]
        hatalar = [s.get("hata") for s in sonuclar if s and s.get("hata")]
        akis_hizi = [t / max(s["sure"] - (s.get("ttft") or 0), 0.01) for t, s in
                     zip(tok, [s for s in sonuclar if s and not s.get("hata")])]
        R.yaz(f"  {n} paralel: {toplam:.1f}s, toplam {sum(tok)} token → {sum(tok) / max(toplam, 0.01):.0f} tok/sn; "
              f"akış başına ort. {sum(akis_hizi) / max(1, len(akis_hizi)):.0f} tok/sn"
              + (f"; HATA: {hatalar[0]}" if hatalar else ""))
        if n == 2 and akis_hizi and sum(akis_hizi) / len(akis_hizi) >= 100:
            R.config_oner("paralel", 3, "2 paralelde akış başına ≥100 tok/sn (Yeniden özetle hızlanır)")


def baglam_probu(R, ctx):
    R.baslik("9) Bağlam probu: pencereye yakın girdi ve kasıtlı taşma (vLLM hata metni)")
    url = ctx["route"] + "/chat/completions"
    pencere = ctx.get("pencere", 16384)
    hedef = min(int(pencere * 0.85), 30000)
    mesaj = [{"role": "user", "content": uzun_metin(hedef, ctx.get("kr_token", 3.0)) + "\n\nTek kelimeyle özetle."}]
    r = ctx["h"].iste("POST", url, {"model": ctx["model"], "messages": mesaj, "max_tokens": 64,
                                    "chat_template_kwargs": {"enable_thinking": False}})
    pt = ((r.get("json") or {}).get("usage") or {}).get("prompt_tokens")
    R.yaz(f"  ~{hedef} token girdi (pencere {pencere}): " + (f"HTTP 200, prompt_tokens={pt}, {r['sure']:.1f}s"
                                                            if r["durum"] == 200 else _hata_satiri(r)))
    t = ctx["h"].iste("POST", url, {"model": ctx["model"], "messages": [{"role": "user", "content": "Merhaba"}],
                                    "max_tokens": pencere + 1000, "chat_template_kwargs": {"enable_thinking": False}})
    if t["durum"] == 200:
        c = _cevap_coz(t)
        R.yaz(f"  max_tokens = pencere+1000 → HTTP 200 (sunucu kırpıyor), finish={c['finish']}")
        return
    govde = t.get("metin") or t.get("hata") or ""
    R.yaz(f"  max_tokens = pencere+1000 → HTTP {t['durum']}; tam gövde:", f"    {_kisa(govde, 700)}")
    try:
        sys.path.insert(0, KOK)
        import llm
        tanindi = bool(llm._BAGLAM_IFADE.search(govde))
        sinir = llm._baglam_siniri(govde)
        R.yaz(f"  uygulama bu hatayı bağlam taşması olarak tanıyor mu: {'EVET' if tanindi else 'HAYIR'}; "
              f"okunan pencere: {sinir}")
        if not tanindi and t["durum"] in (400, 422):
            R.bulgu("ORTA", "bağlam taşması hata metni uygulamanın kalıbına uymuyor",
                    "llm._BAGLAM_IFADE genişletilmeli (bu çıktıyı paylaşın)")
    except Exception as e:
        R.yaz(f"  (llm modülü yüklenemedi: {_istisna(e)})")


def uygulama_yolu(R, cfg, olay):
    """Uygulamanin kendi istemcisi (openai SDK, TLS/proxy, akis, sunucu tanisi) ile kisa deneme."""
    R.baslik("10) Uygulamanın kendi yolu (llm modülü: openai istemcisi, sunucu tanısı, akış)")
    sys.path.insert(0, KOK)
    import llm
    llm.ayarla(cfg)
    llm.GUNLUK_FN = olay.append
    for u in llm.sunucuyu_tani(zorla=True):
        R.yaz(f"  tanı: {u}")
    llm.UYARI_FN = lambda m: R.yaz(f"  uyarı (çalışırken): {m}")
    R.yaz(f"  {llm.ayar_ozeti()}")
    R.yaz("  bütçeler: " + ", ".join(f"{k}={llm.butce(k)}" for k in
                                     ("duzeltme", "bolum", "liste", "genel", "soru", "soru_dusunme", "parca", "paralel")))
    metin, tamam = llm.kisa_test()
    R.yaz(f"  {metin}")
    if not tamam:
        R.bulgu("YUKSEK", f"uygulamanın LLM isteği başarısız: {metin}")
    return llm


def boru_hatti(R, llm_mod=None, olay=None):
    """--tam: uygulamanin GERCEK not boru hatti sentetik transkript uzerinde (ag gerekmeden de, llm.sor
    sahteyle test edilebilir). Dondurur: uretilen not (Markdown)."""
    R.baslik("11) Tam boru hattı (sentetik 3 bölümlük toplantı; gerçek içerik yok)")
    if llm_mod is None:
        sys.path.insert(0, KOK)
        import llm as llm_mod
    llm = llm_mod
    olay = olay if olay is not None else []
    if llm.GUNLUK_FN is None:
        llm.GUNLUK_FN = olay.append
    ilerleme = []
    tarih = dt.date(2026, 10, 8)
    sozluk = "Jira; commit; sprint; indeks; kampanya modülü"
    bolumler, ozetler, n0 = sentetik_transkript(), [], len(olay)
    for i, (aralik, satirlar) in enumerate(bolumler, 1):
        metin_satirlari = [f"{kim}: {m}" for _, kim, m in satirlar]
        baglam = llm.baglam_metni("Sentetik ekip toplantısı", tarih, KATILIMCILAR, sozluk)
        t0 = time.time()
        try:
            duzeltilmis, duzeltmeler = llm.satirlari_duzelt(metin_satirlari, baglam)
            R.yaz(f"  bölüm {i} düzeltme: {time.time() - t0:.1f}s, {len(duzeltmeler)} düzeltme "
                  f"{[f'{o} → {d}' for o, d in duzeltmeler][:4]}")
        except Exception as e:
            duzeltilmis = metin_satirlari
            R.bulgu("YUKSEK", f"bölüm {i} düzeltme HATASI: {llm.hata_metni(e)}")
        t0 = time.time()
        try:
            onceki = ozetler[-1].get("konular") if ozetler and ozetler[-1] else None
            o = llm.bolum_ozetle("\n".join(duzeltilmis), i, baglam, onceki)
        except Exception as e:
            o = None
            R.bulgu("YUKSEK", f"bölüm {i} özeti HATASI: {llm.hata_metni(e)}")
        if o:
            ing = llm.ingilizce_mi(" ".join([o["ozet"]] + o["kararlar"]))
            R.yaz(f"  bölüm {i} özet: {time.time() - t0:.1f}s, karar={len(o['kararlar'])} "
                  f"aksiyon={len(o['aksiyonlar'])} açık_soru={len(o['acik_sorular'])} İngilizce={ing}")
            if ing:
                R.bulgu("ORTA", f"bölüm {i} özeti İngilizce çıktı")
            o = dict(o, aralik=aralik)
        else:
            R.bulgu("YUKSEK", f"bölüm {i} özetlenemedi (None)")
        ozetler.append(o)
    t0 = time.time()
    not_md = ""
    try:
        not_md = llm.birlestir([o for o in ozetler if o], llm.baglam_metni("Sentetik ekip toplantısı", tarih,
                                                                         KATILIMCILAR, sozluk),
                               ilerleme=ilerleme.append, katilimcilar=KATILIMCILAR)
        R.yaz(f"  birleştirme (liste + özet paragrafı): {time.time() - t0:.1f}s")
    except Exception as e:
        R.bulgu("YUKSEK", f"not birleştirme HATASI: {llm.hata_metni(e)}")
    for m in ilerleme:
        R.yaz(f"    ilerleme: {m.strip()}")
    yedek = [m for m in ilerleme if "yerel" in m or "kullanıldı" in m or "üretilemedi" in m]
    if yedek:
        R.bulgu("ORTA", f"birleştirmede LLM'siz yedeğe düşüldü: {yedek[0].strip()}")
    bolum_satirlari = [(a, "\n".join(f"[{ts}] {kim}: {m}" for ts, kim, m in s)) for a, s in bolumler]
    t0 = time.time()
    try:
        cevap = llm.toplantiya_sor("Pazarlama ekibine yeni tarihi kim iletecek?", bolum_satirlari,
                                   "Toplantı: Sentetik ekip toplantısı")
        R.yaz(f"  toplantıya soru: {time.time() - t0:.1f}s → {cevap[:200]!r}")
        if "Elif" not in cevap:
            R.bulgu("DUSUK", "soru-cevap beklenen kişiyi (Elif Kaya) bulamadı")
    except Exception as e:
        R.bulgu("YUKSEK", f"toplantıya soru HATASI: {llm.hata_metni(e)}")
    cagri_tablosu(R, olay[n0:])
    R.yaz("", "  ---- üretilen not (sentetik) ----")
    for s in (not_md or "(not üretilemedi)").splitlines():
        R.yaz("  " + s)
    return not_md


def cagri_tablosu(R, kayitlar):
    """llm.GUNLUK_FN kayitlari (icerik yok) -> tablo."""
    if not kayitlar:
        return
    R.yaz("", "  LLM çağrıları (llm_log kayıtları):")
    R.yaz("  görev     düşünme şema finish  girdi(yerel/sunucu) max_tok çıktı düşünce_kr  süre  dil  hata")
    for k in kayitlar:
        if k.get("olay"):
            R.yaz(f"  [{k['olay']}] " + ", ".join(f"{a}={k[a]}" for a in k if a not in ("olay", "zaman")))
            continue
        R.yaz(f"  {str(k.get('gorev')):9} {str(k.get('dusunme')):7} {'+' if k.get('sema') else '-'}"
              f"{'(düştü)' if k.get('sema_dustu') else '':7} {str(k.get('finish')):7} "
              f"{k.get('giris_token')}/{k.get('sunucu_giris_token')}  {k.get('max_tokens')} {k.get('cikti_token')} "
              f"{k.get('dusunce_karakter')}{' (içerikte)' if k.get('dusunce_icerikte') else ''} "
              f"{k.get('sure_sn')}s {'EN' if k.get('ingilizce') else 'tr'} {k.get('hata') or ''}")


def llm_log_ozeti(R, kok="toplantilar", son=5):
    """Son toplantilarin llm_log.jsonl'i: gorev bazinda sayi, finish dagilimi, ort. sure, hata metinleri.
    Kayitlar icerik tasimaz (yalniz istatistik)."""
    R.baslik(f"12) Son {son} toplantının llm_log.jsonl özeti (içerik yok)")
    dosyalar = sorted(glob.glob(os.path.join(kok, "*", "llm_log.jsonl")), key=os.path.getmtime)[-son:]
    if not dosyalar:
        R.yaz(f"  {kok}/*/llm_log.jsonl yok (uygulamayı başka klasörden çalıştırıyorsan --kayit ile yol ver)")
        return {}
    ozet_tum = {}
    for yol in dosyalar:
        gorevler, hatalar, ayar = {}, [], []
        for ham in open(yol, encoding="utf-8", errors="replace"):
            try:
                k = json.loads(ham)
            except Exception:
                continue
            if k.get("olay"):
                ayar.append(k)
                continue
            g = gorevler.setdefault(k.get("gorev") or "?", {"n": 0, "finish": {}, "sure": 0.0, "hata": 0})
            g["n"] += 1
            g["sure"] += float(k.get("sure_sn") or 0)
            if k.get("hata"):
                g["hata"] += 1
                hatalar.append(f"{k.get('zaman')} {k.get('gorev')}: {k['hata']}")
            else:
                f = str(k.get("finish"))
                g["finish"][f] = g["finish"].get(f, 0) + 1
        klasor = os.path.basename(os.path.dirname(yol))
        R.yaz(f"  [{klasor[:10]}…]")
        for a in ayar[-2:]:
            R.yaz(f"    {a.get('olay')}: model={a.get('model')} pencere={a.get('pencere')} uyarı={a.get('uyari') or a.get('neden')}")
        for ad, g in sorted(gorevler.items()):
            R.yaz(f"    {ad:9} çağrı={g['n']:3} finish={g['finish']} hata={g['hata']} ort_süre={g['sure'] / g['n']:.1f}s")
        for h in hatalar[-20:]:
            R.yaz(f"    HATA {_kisa(h, 400)}")
        ozet_tum[klasor] = {"gorevler": gorevler, "hatalar": hatalar}
    return ozet_tum


# ---------------------------------------------------------------- calistirma

def _adim(R, ad, fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception as e:                   # bir adim butun araci dusurmesin
        R.yaz(f"  ! '{ad}' adımı hata verdi: {_istisna(e)}")
        return None


def config_oku():
    yol = os.path.join(KOK, "config.json")
    if not os.path.exists(yol):
        return {}
    with open(yol, encoding="utf-8") as f:
        return json.load(f)


def kisa(cfg, yaz_fn):
    """tools/teshis.py icin: baglanti, model/pencere, tokenizer, dusunme ve sema (hiz/probe yok)."""
    R = Rapor(yaz_fn)
    route = (cfg.get("route") or "").strip().rstrip("/")
    if not route:
        R.yaz("route ayarlı değil")
        return R
    if not re.search(r"/v\d+$", route):
        route += "/v1"
    ctx = {"route": route, "model": cfg.get("model") or ""}
    if _adim(R, "bağlantı", baglanti, R, ctx, cfg):
        _adim(R, "model", modeller, R, ctx, cfg)
        _adim(R, "tokenizer", tokenizer, R, ctx)
        _adim(R, "düşünme", dusunme, R, ctx)
        _adim(R, "şema", sema, R, ctx)
    R.ozet()
    R.yaz("", "Ayrıntılı ölçüm (hız, bağlam probu, gerçek boru hattı): python tools\\llm_teshis.py --tam")
    return R


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    import warnings
    warnings.filterwarnings("ignore")
    a = argparse.ArgumentParser(description="BriefMind LLM sunucusu teşhisi (sentetik metinlerle)")
    a.add_argument("--tam", action="store_true", help="uygulamanın gerçek boru hattını da sentetik toplantıyla çalıştır")
    a.add_argument("--hizli", action="store_true", help="uzun (>30 sn) ve paralel ölçümleri atla")
    a.add_argument("--kayit", default="toplantilar", help="toplantilar klasörünün yolu")
    a = a.parse_args(argv)
    os.chdir(KOK)
    sys.path.insert(0, KOK)
    R = Rapor()
    R.yaz(f"BriefMind LLM teşhisi — {dt.datetime.now():%Y-%m-%d %H:%M}  (mod: {'tam' if a.tam else 'temel'}"
          f"{', hızlı' if a.hizli else ''})")
    try:
        cfg = config_oku()
    except Exception as e:
        R.yaz(f"config.json okunamadı: {_istisna(e)}")
        cfg = {}
    route = (cfg.get("route") or "").strip().rstrip("/")
    R.gizle(_kok(route))
    _adim(R, "ortam", ortam, R, cfg)
    if route and not re.search(r"/v\d+$", route):
        route += "/v1"
    ctx = {"route": route, "model": cfg.get("model") or ""}
    olay = []
    if not route:
        R.bulgu("YUKSEK", "config.json 'route' (LLM adresi) yok")
    elif _adim(R, "bağlantı", baglanti, R, ctx, cfg):
        _adim(R, "model", modeller, R, ctx, cfg)
        _adim(R, "tokenizer", tokenizer, R, ctx)
        _adim(R, "düşünme", dusunme, R, ctx)
        _adim(R, "şema", sema, R, ctx)
        _adim(R, "hız", hiz, R, ctx, a.hizli)
        if not a.hizli:
            _adim(R, "paralel", paralel, R, ctx)
        _adim(R, "bağlam probu", baglam_probu, R, ctx)
        llm_mod = _adim(R, "uygulama yolu", uygulama_yolu, R, cfg, olay)
        if a.tam and llm_mod is not None:
            _adim(R, "boru hattı", boru_hatti, R, llm_mod, olay)
    _adim(R, "llm_log", llm_log_ozeti, R, a.kayit)
    R.ozet()
    R.kaydet(CIKTI_DOSYA)
    print("\nKaydedildi: " + os.path.abspath(CIKTI_DOSYA))
    return R


if __name__ == "__main__":
    main()
