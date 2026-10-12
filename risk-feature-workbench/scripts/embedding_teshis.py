"""Embedding servisini iş bilgisayarında tanılar.

Gösterir: uygulamanın bağlantı testi ne buldu (toplu istek dahil), model
kimliği hangi adreste okunabiliyor, tek metinlik ve toplu istekle hız ve
verilen belge sayısı için tahmini indeks süresi.

Kurum verisi gönderilmez; yalnız sabit deneme metinleri. API anahtarı
yazdırılmaz, dosyaya kaydedilmez.

Kullanım (proje klasöründe, PowerShell):
  .venv\\Scripts\\python.exe scripts\\embedding_teshis.py
  .venv\\Scripts\\python.exe scripts\\embedding_teshis.py --ca C:\\sertifikalar\\kurum-ca.pem --belge 150000
API anahtarı gerekiyorsa RISK_EMBEDDING_API_KEY ortam değişkenine yazın ya da --anahtar-sor kullanın.
"""
import argparse
import getpass
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from riskmatch.adapters.qwen import EmbeddingAdapter  # noqa: E402
from riskmatch.deployment import EMBEDDING_BASE_URL  # noqa: E402
from riskmatch.model_diagnostics import _Client, _Failure, discover_model  # noqa: E402
from riskmatch.web_services import model_config  # noqa: E402

CLUE_PATHS = ("/v1/models", "/models", "/info", "/health", "/version", "/openapi.json")
MODEL_KEYS = {"id", "model", "model_id", "model_name", "served_model_name", "name"}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Embedding servisi tanılama (model kimliği, toplu istek, hız).")
    p.add_argument("--base", default=EMBEDDING_BASE_URL, help="temel servis adresi (varsayılan deployment.py)")
    p.add_argument("--ca", default="", help="kurum CA sertifikası .pem yolu")
    p.add_argument("--model", default="", help="denenecek model kimliği (servis ekibinin bildirdiği)")
    p.add_argument("--belge", type=int, default=0, help="tahmini süre için belge sayısı (ekrandaki b değeri)")
    p.add_argument("--anahtar-sor", action="store_true", help="API anahtarını gizli olarak sor")
    return p.parse_args(argv)


def model_fields(value, found, depth=0):
    if depth > 4 or len(found) >= 8:
        return found
    if isinstance(value, dict):
        for key, child in value.items():
            if key in MODEL_KEYS and isinstance(child, str) and 0 < len(child) <= 200 and child.isprintable():
                if (key, child) not in found:
                    found.append((key, child))
            elif isinstance(child, (dict, list)):
                model_fields(child, found, depth + 1)
    elif isinstance(value, list):
        for child in value[:20]:
            model_fields(child, found, depth + 1)
    return found


def clues(base, key, ca):
    root = base[:-3] if base.endswith("/v1") else base
    print("\n2) Model kimliği ipuçları (salt okuma GET istekleri)")
    for path in CLUE_PATHS:
        start = time.monotonic()
        try:
            body = _Client(root, key, ca).get(root + path)
        except _Failure as failure:
            print("   %-14s %-14s %s" % (path, failure.code, failure.message[:110]))
            continue
        ms = round((time.monotonic() - start) * 1000)
        if path == "/openapi.json" and isinstance(body, dict):
            paths = sorted((body.get("paths") or {}).keys())
            print("   %-14s OK %5d ms  yollar: %s" % (path, ms, ", ".join(paths[:15]) or "-"))
            continue
        fields = model_fields(body, [])
        summary = ", ".join("%s=%s" % pair for pair in fields) or "model alanı yok; anahtarlar: %s" % (
            ", ".join(list(body)[:10]) if isinstance(body, dict) else type(body).__name__)
        print("   %-14s OK %5d ms  %s" % (path, ms, summary))


def texts(count, prefix):
    # Gerçek katalog belgesine benzer uzunlukta, kurum verisi içermeyen sentetik metin.
    return ["%s %03d | MUSTERI_ISLEM_OZET.TOPLAM_TUTAR_%03d | Müşterinin önceki 1 ay içerisinde "
            "gerçekleştirdiği işlemlerin toplam tutarı (TL). | Günlük müşteri işlem özet tablosu." % (prefix, i, i)
            for i in range(count)]


def batch_size(settings):
    # Açık modelde ayar yoksa uygulama 16 kullanır; sunucu varsayılanında yalnız doğrulanan boyut.
    default = 1 if settings.get("model_selection") == "server_default" else 16
    return int(settings.get("max_batch") or default)


def speed(settings, key, ca, documents):
    print("\n3) Hız denemesi (uygulamanın kendi embedding istemcisiyle)")
    rates = {}
    verified = batch_size(settings)
    for batch, count in [(1, 16)] + ([(verified, 64)] if verified > 1 else []):
        config = model_config({"embedding": {**settings, "max_batch": batch, **({"ca_file": ca} if ca else {})}}, "embedding")
        adapter = EmbeddingAdapter(config, api_key=key)
        start = time.monotonic()
        try:
            adapter.embed(texts(count, "Hiz denemesi b%d" % batch))
        except Exception as error:  # noqa: BLE001 - tanılama çıktısı; ayrıntı yazdırılmaz
            print("   %2d metin/istek: başarısız (%s)" % (batch, type(error).__name__ + ": " + str(error)[:120]))
            continue
        elapsed = time.monotonic() - start
        requests = -(-count // adapter.max_batch)
        rates[batch] = count / elapsed
        note = "" if adapter.max_batch == batch else "  (sunucu reddetti, tek tek gönderildi)"
        print("   %2d metin/istek: %d metin, %d istek, %.1f sn -> %.1f metin/sn%s"
              % (batch, count, requests, elapsed, rates[batch], note))
    if documents and rates:
        print("\n   %d belge için tahmini indeks süresi (indeks istekleri sırayla gönderir):" % documents)
        for batch, rate in sorted(rates.items()):
            print("   %2d metin/istek: ~%.0f dk" % (batch, documents / rate / 60))
    return rates


def main(argv=None):
    args = parse_args(argv)
    key = os.environ.get("RISK_EMBEDDING_API_KEY", "")
    if args.anahtar_sor:
        key = getpass.getpass("API anahtarı (yoksa Enter): ")
    raw = {"base_url": args.base, "api_key": key, "ca_file": args.ca, **({"model": args.model} if args.model else {})}
    print("Embedding tanılama · adres: %s · API anahtarı: %s · CA: %s"
          % (args.base, "var" if key else "yok", args.ca or "sistem"))
    print("\n1) Uygulamanın bağlantı testi")
    report = discover_model({"embedding": raw}, "embedding")
    for check in report["checks"]:
        print("   [%-11s] %5s ms  %s" % (check["status"], check["elapsed_ms"], check["name"]))
        if check["status"] != "passed":
            print("                         %s" % check.get("message", ""))
    print("   Sonuç: %s" % (report.get("message") or report.get("error") or ""))
    settings = report.get("settings") or {}
    if report.get("ok"):
        print("   Kip: %s · model: %s · toplu: %s metin/istek · boyut: %s · çağrı: %s" % (
            settings.get("model_selection") or "explicit", settings.get("model") or "(sunucu varsayılanı)",
            batch_size(settings), settings.get("dimensions"), settings.get("endpoint")))
    clues(args.base, key, args.ca)
    if report.get("ok"):
        speed(settings, key, args.ca, args.belge)
    print("\nNe yapmalı:")
    if not report.get("ok"):
        print("- Bağlantı testi geçmedi; yukarıdaki nedeni giderin (adres, API anahtarı, --ca).")
    elif batch_size(settings) > 1:
        print("- Toplu istek kullanılabilir (%d metin/istek). Uygulamayı güncelleyip Bağlantılar sayfasında" % batch_size(settings))
        print("  'Embedding bağlantısını test et' deyin; ek ayar gerekmez.")
    else:
        print("- Sunucu toplu isteği doğrulatmadı. 2. bölümde bir model kimliği görünüyorsa --model ile deneyin;")
        print("  yoksa servis ekibinden toplu (input listesi) istek desteğini ya da model kimliğini isteyin.")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
