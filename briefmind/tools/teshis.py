"""
teshis.py — BriefMind ortam teshisi (LLM, STT, kayitlar). Toplanti ICERIGI toplamaz.

Kullanim (briefmind klasorunden):
  python tools\\teshis.py                 -> teshis_cikti.txt
  python tools\\teshis.py --wav ornek.wav  -> STT'yi bu dosyayla da dener (metin ciktiya YAZILMAZ)

Ciktida LLM/STT adresleri <LLM_ADRES>/<STT_ADRES> olarak, anahtarlar hic yazilmaz.
Paylasmadan once teshis_cikti.txt'yi bir kez goz gezdir.
"""
import argparse
import datetime as dt
import glob
import importlib
import io
import json
import os
import platform
import re
import subprocess
import sys
import time
import wave

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(KOK)
sys.path.insert(0, KOK)

CIKTI = []
GIZLI = []


def _kes(s, son):
    s = (s or "").rstrip("/")
    return s[:-len(son)] if son and s.endswith(son) else s


def _gizle(adres, etiket):
    """Adresi hem tam hem yalniz sunucu adi olarak maskeler (hata mesajlarinda host='...' gecer)."""
    from urllib.parse import urlparse
    if not adres:
        return
    GIZLI.append((adres, etiket))
    host = urlparse(adres).hostname
    if host:
        GIZLI.append((host, etiket))


def yaz(*satirlar):
    for s in satirlar:
        s = str(s)
        for gercek, yerine in GIZLI:
            if gercek:
                s = s.replace(gercek, yerine)
        CIKTI.append(s)
        print(s, flush=True)


def baslik(s):
    yaz("", "=" * 70, s, "=" * 70)


def paket_surumleri():
    baslik("1) Ortam")
    yaz(f"Python: {sys.version.split()[0]}   OS: {platform.platform()}")
    for p in ("openai", "httpx", "requests", "numpy", "PyQt5", "uiautomation", "soundcard", "sounddevice",
              "win32com"):
        try:
            m = importlib.import_module(p)
            yaz(f"  {p:14} {getattr(m, '__version__', 'kurulu')}")
        except Exception as e:
            yaz(f"  {p:14} YOK ({type(e).__name__})")


def ayarlar():
    baslik("2) config.json")
    if not os.path.exists("config.json"):
        yaz("config.json yok (config.example.json'dan kopyalanmali)")
        return {}
    with open("config.json", encoding="utf-8") as f:
        cfg = json.load(f)
    _gizle(_kes(cfg.get("route"), "/v1"), "<LLM_ADRES>")
    _gizle(_kes(cfg.get("stt_url"), "/v1/audio/transcriptions"), "<STT_ADRES>")
    for k, v in cfg.items():
        if k in ("stt_key", "api_key", "ben"):
            v = "(ayarli)" if v and v != "EMPTY" else v
        yaz(f"  {k}: {v}")
    return cfg


def _http():
    import httpx
    return httpx.Client(verify=False, trust_env=False, timeout=120)


def llm_testleri(cfg):
    baslik("3) LLM sunucusu")
    route = (cfg.get("route") or "").rstrip("/")
    model = cfg.get("model")
    if not route:
        yaz("route ayarli degil")
        return
    h = _http()
    # 3a model listesi ve max_model_len
    try:
        r = h.get(f"{route}/models")
        yaz(f"GET /v1/models -> {r.status_code}")
        for m in r.json().get("data", []):
            yaz(f"  model: {m.get('id')}  max_model_len: {m.get('max_model_len')}  root: {m.get('root')}")
        yaz(f"  config 'context': {cfg.get('context')}  (max_model_len ile ayni olmali)")
    except Exception as e:
        yaz(f"  /v1/models HATA: {e!r}")
    # 3b tokenize
    ornek = "Toplantıda IFRS 9 raporunun perşembeye yetiştirilmesi ve Jira kaydı açılması kararlaştırıldı."
    try:
        r = h.post(_kes(route, "/v1") + "/tokenize", json={"model": model, "prompt": ornek})
        n = r.json().get("count")
        yaz(f"POST /tokenize -> {r.status_code}  token: {n}  karakter/token: {len(ornek) / n:.2f}" if n
            else f"POST /tokenize -> {r.status_code} {r.text[:200]}")
    except Exception as e:
        yaz(f"  /tokenize HATA: {e!r}")

    def sohbet(etiket, kw, max_tokens=600, response_format=None):
        govde = {"model": model, "max_tokens": max_tokens, "temperature": 0.3,
                 "messages": [{"role": "system", "content": "Kısa ve Türkçe cevap ver."},
                              {"role": "user", "content": "Bir toplantı notunda 'karar' ile 'aksiyon' farkını "
                                                          "iki cümleyle açıkla."}]}
        if kw is not None:
            govde["chat_template_kwargs"] = kw
        if response_format:
            govde["response_format"] = response_format
        t0 = time.time()
        try:
            r = h.post(f"{route}/chat/completions", json=govde)
        except Exception as e:
            yaz(f"  [{etiket}] HATA: {e!r}")
            return
        sure = time.time() - t0
        if r.status_code != 200:
            yaz(f"  [{etiket}] HTTP {r.status_code}: {r.text[:300]}")
            return
        v = r.json()
        c = v["choices"][0]
        msj = c.get("message") or {}
        icerik = msj.get("content") or ""
        dusunce = msj.get("reasoning_content") or msj.get("reasoning") or ""
        u = v.get("usage") or {}
        yaz(f"  [{etiket}] finish={c.get('finish_reason')}  sure={sure:.1f}s  "
            f"completion_tokens={u.get('completion_tokens')}  reasoning_alani={'VAR' if dusunce else 'yok'} "
            f"(len {len(dusunce)})  content_think_etiketi={'VAR' if '<think>' in icerik or '</think>' in icerik else 'yok'}  "
            f"content_len={len(icerik)}")
        yaz(f"      content ilk 160: {icerik[:160]!r}")

    yaz("Sohbet testleri (reasoning parser, dusunme kontrolu, reasoning_effort etkisi):")
    sohbet("varsayilan", None)
    sohbet("reasoning_effort=low", {"reasoning_effort": "low"})
    sohbet("enable_thinking=False", {"enable_thinking": False})
    sohbet("dusunme + max_tokens=120 (kesilme)", None, max_tokens=120)
    sema = {"type": "json_schema", "json_schema": {"name": "t", "schema": {
        "type": "object", "properties": {"karar": {"type": "string"}, "aksiyon": {"type": "string"}},
        "required": ["karar", "aksiyon"]}}}
    sohbet("json_schema + dusunmesiz", {"enable_thinking": False}, response_format=sema)
    sohbet("json_schema + dusunme", None, response_format=sema)


def _sessiz_wav(sn=2.0):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * sn))
    return b.getvalue()


def stt_testleri(cfg, wav_yolu=None):
    baslik("4) STT (Whisper) servisi")
    adres = _kes(cfg.get("stt_url"), "/v1/audio/transcriptions")
    if not adres:
        yaz("stt_url ayarli degil")
        return
    import requests
    bas = {"Authorization": f"Bearer {cfg['stt_key']}"} if cfg.get("stt_key") else {}
    for yol in ("/health", "/v1/models"):
        try:
            r = requests.get(adres + yol, headers=bas, verify=False, timeout=10)
            yaz(f"GET {yol} -> {r.status_code}  {r.text[:200]!r}")
        except Exception as e:
            yaz(f"GET {yol} HATA: {e!r}")
    ornekler = [("2 sn sessizlik", _sessiz_wav())]
    if wav_yolu:
        with open(wav_yolu, "rb") as f:
            ornekler.append((f"dosya ({os.path.basename(wav_yolu)})", f.read()))
    for etiket, veri in ornekler:
        for bicim in ("verbose_json", "json"):
            t0 = time.time()
            try:
                r = requests.post(adres + "/v1/audio/transcriptions", headers=bas, verify=False, timeout=300,
                                  files={"file": ("t.wav", veri, "audio/wav")},
                                  data={"language": "tr", "model": cfg.get("stt_model", "whisper"),
                                        "temperature": "0", "response_format": bicim})
            except Exception as e:
                yaz(f"  [{etiket} / {bicim}] HATA: {e!r}")
                continue
            sure = time.time() - t0
            if r.status_code != 200:
                yaz(f"  [{etiket} / {bicim}] HTTP {r.status_code}: {r.text[:200]}")
                continue
            v = r.json()
            seg = v.get("segments") or []
            alanlar = sorted(seg[0].keys()) if seg else []
            yaz(f"  [{etiket} / {bicim}] {sure:.2f}s  ust_alanlar={sorted(v.keys())}  segment={len(seg)}  "
                f"segment_alanlari={alanlar}  metin_uzunlugu={len(v.get('text') or '')}")
            metin = (v.get("text") or "").strip()
            if etiket.startswith("2 sn") and metin:
                yaz(f"      ! sessizlige metin uretti (halusinasyon): {metin[:80]!r}")
            elif not etiket.startswith("2 sn") and len(re.findall(r"\w+", metin)) < 3:
                yaz("      ! dosyadaki konusma TANINMADI (bos ya da '...'). Once dosyayi dinle: ses var mi? "
                    "Varsa STT sorunu, yoksa mikrofon/kayit cihazi sorunu.")
            else:
                yaz(f"      tanınan kelime sayısı: {len(metin.split())}")
            for s in seg[:5]:
                yaz(f"      segment {s.get('start')}-{s.get('end')}  no_speech_prob={s.get('no_speech_prob')}  "
                    f"avg_logprob={s.get('avg_logprob')}  compression_ratio={s.get('compression_ratio')}")


def _ingilizce_mi(metin):
    try:
        import llm
        return llm.ingilizce_mi(metin)
    except Exception:
        return None


def _sn(ts):
    try:
        p = [int(x) for x in str(ts).split(":")]
        return p[0] * 3600 + p[1] * 60 + (p[2] if len(p) > 2 else 0)
    except Exception:
        return None


def kayit_istatistikleri(son=10, kok="toplantilar"):
    baslik(f"5) Son {son} toplantı kaydı (yalnızca sayılar, içerik yok)")
    klasorler = sorted(glob.glob(os.path.join(kok, "*")), key=os.path.getmtime)[-son:]
    if not klasorler:
        yaz(f"{kok} klasörü boş ya da yok. Uygulamayı başka klasörden (ör. dist\\BriefMind) çalıştırıyorsan "
            "--kayit ile o klasördeki 'toplantilar' yolunu ver.")
        return
    for i, k in enumerate(klasorler, 1):
        try:
            meta = json.load(open(os.path.join(k, "meta.json"), encoding="utf-8"))
        except Exception:
            meta = {}
        parcalar = sorted(glob.glob(os.path.join(k, "parcalar", "parca_*.json")))
        ozetsiz, tokenler, nedenler = 0, [], {}
        for p in parcalar:
            try:
                v = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            ozetsiz += v.get("ozet") is None
            tokenler.append(v.get("token", 0))
            nedenler[v.get("neden")] = nedenler.get(v.get("neden"), 0) + 1
        satir, kaynak, geri, onceki, kelime, soru = 0, {}, 0, None, 0, 0
        yol = os.path.join(k, "altyazi.jsonl")
        if os.path.exists(yol):
            for ham in open(yol, encoding="utf-8"):
                try:
                    s = json.loads(ham)
                except Exception:
                    continue
                satir += 1
                kaynak[s.get("kaynak")] = kaynak.get(s.get("kaynak"), 0) + 1
                kelime += len((s.get("text") or "").split())
                soru += s.get("speaker") == "?"
                t = _sn(s.get("ts"))
                if t is not None and onceki is not None and t < onceki - 2:
                    geri += 1
                onceki = t if t is not None else onceki
        not_yol = os.path.join(k, "not.md")
        not_bilgi = "not.md yok"
        if os.path.exists(not_yol):
            md = open(not_yol, encoding="utf-8").read()
            eksik_baslik = [b for b in ("## Özet", "## Kararlar", "## Aksiyonlar", "## Açık sorular",
                                        "## Bir sonraki adım") if b not in md]
            not_bilgi = (f"not.md {len(md)} karakter, ingilizce={_ingilizce_mi(md)}, "
                         f"eksik_basliklar={eksik_baslik or '-'}, think_etiketi={'<think>' in md or '</think>' in md}, "
                         f"eksik_bolumler_notu={'## Eksik bölümler' in md}")
        yaz(f"[{i}] tarih={meta.get('tarih')} durum={meta.get('durum')} kaynak={meta.get('kaynak')} "
            f"katilimci={len(meta.get('katilimcilar') or [])}")
        yaz(f"    parça={len(parcalar)} özetsiz={ozetsiz} token(min/ort/maks)="
            f"{min(tokenler, default=0)}/{int(sum(tokenler) / max(1, len(tokenler)))}/{max(tokenler, default=0)} "
            f"kapanış={nedenler}")
        yaz(f"    satır={satir} kaynak={kaynak} ort_kelime/satır={kelime / max(1, satir):.1f} "
            f"konuşmacısız(?)={soru} zamanı_geri_giden_satır={geri}")
        yaz(f"    {not_bilgi}")


def hata_logu():
    baslik("6) hata.log (son 15 satır — paylaşmadan önce içeriğine bak)")
    if not os.path.exists("hata.log"):
        yaz("hata.log yok")
        return
    satirlar = open("hata.log", encoding="utf-8", errors="replace").read().splitlines()
    yaz(f"toplam {len(satirlar)} satır")
    for s in satirlar[-15:]:
        yaz("  " + s[:200])


def testler():
    baslik("7) Birim testleri")
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q"], capture_output=True, text=True,
                           timeout=300)
        for s in (r.stdout + r.stderr).strip().splitlines()[-12:]:
            yaz("  " + s)
    except Exception as e:
        yaz(f"pytest çalıştırılamadı: {e!r} (pip install pytest)")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    import warnings
    warnings.filterwarnings("ignore")
    a = argparse.ArgumentParser()
    a.add_argument("--wav", help="STT'yi bu dosyayla da dene (metin çıktıya yazılmaz)")
    a.add_argument("--kayit", default="toplantilar", help="toplantilar klasörünün yolu (varsayılan: ./toplantilar)")
    a.add_argument("--atla-llm", action="store_true")
    a.add_argument("--atla-stt", action="store_true")
    a = a.parse_args()
    yaz(f"BriefMind teşhis — {dt.datetime.now():%Y-%m-%d %H:%M}")
    paket_surumleri()
    cfg = ayarlar()
    for adim, atla in ((lambda: llm_testleri(cfg), a.atla_llm), (lambda: stt_testleri(cfg, a.wav), a.atla_stt),
                       (lambda: kayit_istatistikleri(kok=a.kayit), False), (hata_logu, False), (testler, False)):
        if atla:
            continue
        try:
            adim()
        except Exception as e:
            yaz(f"! adım hatası: {e!r}")
    with open("teshis_cikti.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(CIKTI) + "\n")
    print("\nKaydedildi: " + os.path.abspath("teshis_cikti.txt"))


if __name__ == "__main__":
    main()
