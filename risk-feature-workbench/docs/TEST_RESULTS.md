# Doğrulama — 11 Ekim 2026

## Son uygulama testleri

**86 uygulama testi geçti; hata veya başarısızlık yok.** Son repo doğrulaması: Windows x64 / **Python 3.9.7**, openpyxl 3.1.5; 15,708 saniye. Aynı 86 test önce Python 3.12.14 ile de geçti. İş bilgisayarı için teslim edilen kurulum 3.9.7 sürümüne göre düzenlendi. Koşu komutu:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

`test-results.txt` bu son koşunun çıktısını içerir. Uygulama testlerine Graphify dahil değildir. Harici geliştirme analizinde ayrıca **7 Graphify testi geçti**; bu harici araç ve çıktıları repoya dahil değildir.

| Kapsam | Gerçekten çalıştırılan kanıt |
|---|---|
| Semantik | Aynı isim/farklı anlam; farklı isim/tam tanım; açıklamasız metadata; max/sum/order; kişi/hesap/işlem; yön; pencere ve ters oran; yanlış grain/tarih; faiz türü/oranı; başvurulan/onaylanan; net/brüt ve ürün kapsamı; boş/yanlış fact tipleri |
| Bağımlılık | Eksik Havale_L3M, duplicate ad, belirsiz referans, bütün SCC döngü üyeleri ve büyük graph; sıfır ve NULL ayrımı; günlük distinct toplamama |
| Üretim | Geçici gerçek .xlsx + config-relative onaylı sidecar + sentetik katalog üzerinden iki supported operand ve production candidate; anahtar eksik olunca engel; kaynak facts/formül silinemiyor |
| Oracle | Sahte read-only connector ile ALL_* SQL, bound şema parametreleri, boş yorumların korunması, yanlış container, boş görünür şema, yorumun semantiğe akışı, validated/enabled P/U, timeout parametreleri |
| Qwen | Sahte transport ile ayrı LLM/embedding sözleşmeleri; model/endpoint/boyut doğrulama; uydurma kolon/kanıt/şema reddi; bozuk cevap ve timeout; TLS/allowlist/redirect sınırı |
| Retrieval/cache | Kesin ad, BM25, embedding, RRF; Türkçe normalizasyon; kapsam filtresi; stale/deleted kayıt; model ve endpoint/sözleşme değişiminde cache ayrımı; zorla aday seçmeme |
| İş akışı | Tüm çalışma kitabı, gerçek mod hatasında demo fallback olmaması, eksik kapsamda yokluk iddiası vermeme, satır hatası gizli değerlerinin loga çıkmaması |
| Devam | 4. satırda KeyboardInterrupt ile gerçek kesinti, aynı komutla 4 satırdan devam; hatalı satırlar yeniden denendi; içerik değişince invalidation, salt alım saati değişince 28 satır reuse |
| Excel | 2 sayfa/28 demo satırı; özgün Görüş/Hazırlama süresi; HTML/br/çok satır/Türkçe; duplicate adlar; boş kaynak satırları; formüllerin güvenli metin oluşu; kalıcı kimlikli kopya düzenleme |

## Demo ve arayüz

`python -m riskmatch demo` gerçekten çalıştırıldı: **28 satır**, çalışma tamamlandı. Demo sonucu **2 metadata ile desteklenen, 9 çelişkili, 17 kritik bilgi eksik**. Bunlar sentetik senaryo sayılarıdır, banka başarı oranı değildir. Hazır dosya `examples/demo_run/demo_sonuclar.xlsx`.

Sonuç kitabının altı sayfası render edildi ve görsel olarak kontrol edildi; tablolar, orijinal metin, kanıtlar ve tamamlanma alanları mevcut. Aktif Excel formülü sayısı **0**; kaynak formül metni aynen korunur. Kaynak örnek workbook `@oai/artifact-tool` ile oluşturuldu; uygulamanın yerel Excel okuma/yazma katmanı openpyxl'dir.

Python 3.9.7 ile gerçek Tk gizli penceresi oluşturuldu; **Sentetik örneği kullan → Analiz et → 28 satırlı, 6 sayfalı dosyayı kaydet → düğmeyi yeniden etkinleştir** akışı çalıştırıldı. Dosya kaydetme diyaloğunun seçimi otomatik testte taklit edildi. İnsanla görsel erişilebilirlik/ölçeklendirme kontrolü yapılmış sayılmaz. Kalıcı sistem ayarı değiştirilmedi.

## Kurulum kanıtı

- `scripts/setup.ps1 -Real -Python <Python-3.9.7-yolu>` gerçek 3.9.7 çalıştırıcısıyla proje sanal ortamında tamamlandı. Paket editable wheel olarak derlenip kuruldu; kurulu paket üzerinden 28 satırlı demo tekrar çalıştı. Python dosyaları resmi, imzalı Windows dağıtımından yalnız test klasörüne çıkarıldı.
- Windows x64/Python 3.9.7 için resmî PyPI resolver sonucu 7 gerçek-mod bağımlılığı sabit sürüm ve SHA256 hash'leriyle kilitlendi. `requirements-real.lock` gerçekten kurulabildi; sistem Python bağımlılıkları değiştirilmedi. Kurulum araçları da `requirements-build.lock` ile pip 25.3 ve setuptools 80.9.0 olarak sabitlendi.
- `oracledb==4.0.2` gerçek modülü Python 3.9.7'de import edildi, `ConnectParams(tcp_connect_timeout=10, ssl_server_dn_match=True)` bağlantısız kabul edildi. **Oracle connect veya gerçek Qwen inference çağrısı yapılmadı.**
- `requirements-demo.lock` yalnız openpyxl 3.1.5 ve et_xmlfile 2.0.0 içerir. Graphify hiçbir requirements/pyproject bağımlılığında bulunmaz.
- GitHub Actions için Windows / Python 3.9.7 kurulum, 86 test ve demo Excel akışı eklendi. Bu testler sentetik veri ve taklit servis kullanır; kurum erişim bilgisi gerekmez.

## Canlı doğrulanmayanlar

Kurum DNS/TLS/wallet, Oracle görünürlük ve performansı, yerel Qwen endpoint/protokol/model/boyutu, gerçek risk feature doğruluğu, kurum Windows kurulum politikası. Kullanıcı gerçek ayarları iş bilgisayarında doldurup `docs/WINDOWS.md` pilot listesini tamamlamalıdır. Bu sınırlar demo başarısıyla kapanmaz.

LLM'ye sıfır hata garantisi verilmez. Serbest iş tanımları sınırlı parser dışında kalabilir; kanıt eksikse kesin eşleşme verilmemesi tasarlanan davranıştır. Metin referansının geçerliliği tek başına semantik doğruluğu kanıtlamaz.
