# Doğrulama kaydı

Geliştirme ortamı: Linux, Python 3.12.14. Test komutu `python -m unittest discover -s tests -v`.

**Sonuç: 19 test geçti.** Gerçek tarayıcı testi `ATLAS_BROWSER_TEST=1` ile etkinleştirilerek çalıştırıldı. Playwright 1.64.0 ve geçici test dizinindeki Chromium 153 kullanıldı. Standart Chromium indirmesi bu ortamda bozuk arşiv döndürdüğü için testte alternatif Chromium çalıştırılabilir dosyası kullanıldı; uygulamaya ek bir runtime bağımlılığı eklenmedi.

## Test kapsamı

- Yerel HTTP test sayfasını URL ile tarama: gerçek liste çağrısını yakalama, tıklanmayan atama için JavaScript adayı çıkarma, yüklenen OpenAPI belgesini içe alma. Tarama sırasında atama POST isteği gönderilmedi.
- HAR → SQLite → alan arama → JSON/Markdown/OpenAPI çıktıları. CIF, kaydı açan kişi ve açıklama alanlarının yolları korundu; gerçek örnek değerler rapor/veritabanına taşınmadı.
- Kimliklerin genellenmesi, yöntem ve sunucu ayrımı, tekrarlanan kayıtların birleştirilmesi, dokümante edilmiş parametrelerin HAR çıkarımlarından öncelikli olması.
- Base64 JSON yanıtları, form-urlencoded istekler ve eksik yanıt gövdeleri.
- Normal OpenAI sohbet HTTP biçimi ve özel soru-cevap JSON biçimiyle model analizi. Tool calling/ajan desteği kullanılmadı.
- Modelin uydurduğu alan yolunun yorumlardan çıkarılması, hatalı JSON durumunda kataloğun korunması, değişmeyen şema için model çağrısının tekrarlanmaması.
- Üretilen Python yardımcı modülünden yerel servise gerçek GET/POST çağrıları. Açık yazma seçimi olmadan POST gönderilmedi; modülü doğrudan çalıştırmak hedef çağrı üretmedi. Yönlendirme üzerinden kimlik bilgisi aktarılmadı.
- Python 3.9 sözdizimi denetimi. Python 3.9.25 Linux yorumlayıcısında da gerçek tarayıcı dahil aynı 19 test geçti; Playwright 1.58.0, greenlet 3.2.4 ve typing-extensions 4.15.0 kullanıldı. Windows Python 3.9 x64 için hazır Playwright/greenlet wheel dosyalarının çözümlemesi ve indirilmesi de doğrulandı. Windows runtime ve kurumsal paket deposu test edilmedi.

## Kurum bilgisayarında doğrulanacaklar

Windows Edge/Chrome başlatma, kurumsal SSO, proxy/sertifika ayarları ve gerçek Qwen request/response biçimi bu ortamdan doğrulanmadı. Çözüm Merkezi'nin gerçek liste/atama/yorum uç noktaları henüz bu araçla kaydedilmedi. Yerel test sonuçları, bu kuruma özgü bağlantıların çalıştığını göstermez.

## 0.2.1 kurulum düzeltmesi

Python 3.9 canlı kayıt sınırı kaldırıldı. Python 3.9 için uyumlu paketler sabitlendi; requirements dosyası yalnızca wheel kabul ediyor. Böylece C++ derleyicisi olmayan bilgisayarda greenlet kaynak derlemesi başlatılmıyor. Hazır wheel kurum deposunda yoksa paket bulunamadı hatası açıkça bildiriliyor. doctor komutu Python yolu/bit sayısı ve Playwright/greenlet sürümlerini de gösteriyor.
