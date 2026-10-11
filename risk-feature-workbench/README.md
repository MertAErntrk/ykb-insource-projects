# Risk Feature Workbench

Excel ile gelen risk alanı taleplerini katalog kanıtlarıyla inceleyen yerel Python uygulaması. Bir adayın bulunması doğru eşleşme sayılmaz. Kritik bilgi eksikse sonuç çekimserdir. Müşteri verisi hesaplanmaz, üretim tablolarına yazılmaz.

**İş bilgisayarında ilk kez çalıştıracaksanız:** [adım adım kurulum, değiştirilecek ayarlar ve sorun çözme rehberi](docs/IS_BILGISAYARINDA_BASLANGIC.md).

## Hızlı başlangıç — Windows

Python 3.9.7 x64 (Tcl/Tk bileşeni açık) kurulu bir bilgisayarda bu klasörü açın:

```powershell
.\scripts\setup.ps1
.\scripts\start.ps1
```

Arayüzde **Sentetik örneği kullan → Analiz et ve sonucu kaydet**. Kurulum kurum ağına bağlanmaz. Varsayılan mod **Demo**.

Komut satırından aynı akış:

```powershell
.\.venv\Scripts\python.exe -m riskmatch demo --output-dir runtime\demo
.\.venv\Scripts\python.exe -m riskmatch analyze examples\sentetik_talepler.xlsx --mode demo --output runtime\sonuclar.xlsx
```

İlk dosya `examples/sentetik_talepler.xlsx`: verilen 23 örnek talep ve 5 açıkça sentetik kontrol, iki sayfada 28 satır. `Havale_L3M` uydurulmamıştır. Hazır örnek çıktı `examples/demo_run/demo_sonuclar.xlsx`.

## Sonuçları okuma

- **Sonuçlar:** özgün kullanıcı alanları, fiziksel ad kontrolü, semantik uyum, üretim ihtimali, gerekçe, bağımlılıklar ve doldurulabilir uzman kararı.
- **Adaylar:** tam kaynak yolu, alternatifler, olasılık olmayan retrieval skoru; varsa model yorumu ayrı sütunda.
- **Çalışma bilgisi:** demo/gerçek modu, kapsam, sürümler, zaman, hatalar ve tamamlanma durumu.
- **Kanıtlar:** her boyut için uyum/çelişki/eksik ve değişmemiş kaynak alıntıları.
- **Girdi ...:** bütün kaynak sayfaları, sıraları ve kullanıcı hücreleri. Formüller güvenli metindir, çalıştırılmaz.

`Halihazırda bildiriliyor mu` kullanıcı beyanıdır. `Görüş` ve `Hazırlama süresi` korunur, yeni süre tahmin edilmez. `Metadata ile destekleniyor` sınırlı deterministik kuralların sağlandığını gösterir; uzman doğrulaması gereksinimini kaldırmaz.

## Gerçek moda geçiş

[Windows kurulumu ve yapılandırma](docs/WINDOWS.md) içindeki adımları izleyin. `config/real.example.json` sır içermez. Gerçek tam endpoint, model kimliği/sürümü ve embedding sözleşmesi **boş bırakılmıştır**. Kurum adreslerinin URL biçiminden API uyumluluğu çıkarılmaz. Yapılandırmayı `config.local.json` olarak kopyalayıp iş bilgisayarında doldurun. TLS doğrulaması açıktır; redirect veya dış backend fallback yoktur.

```powershell
.\scripts\setup.ps1 -Real
.\.venv\Scripts\python.exe -m riskmatch check-config config.local.json --mode real
.\.venv\Scripts\python.exe -m riskmatch analyze talepler.xlsx --mode real --config config.local.json --output runtime\gercek_sonuclar.xlsx
```

`check-config` ağsız biçim kontrolüdür. Son `analyze --mode real` komutu, ayar dosyasında `allow_real_connections=true` ise Oracle ve yerel Qwen bağlantılarını başlatır. Başarısız gerçek mod demo sonucu üretmez. Bu teslim sırasında banka bağlantısı kurulmamıştır.

## Düzenleme, devam ve onaylı tanımlar

Kimlik sütunu olmayan dosyayı düzenlemeye başlamadan önce kimlikli bir kopya üretin:

```powershell
.\.venv\Scripts\python.exe -m riskmatch prepare talepler.xlsx --output talepler_kimlikli.xlsx
```

`Talep Kimliği` sütununu değiştirmeden satırları sıralayabilir ve metinleri düzenleyebilirsiniz. Kimliği olmayan özgün dosyalarda kimlik, dosya içeriği + sayfa + satırdan türetilir; aynı dosyayla kararlıdır, düzenlemeler arasında korunması için kimlikli kopya gerekir. Demo dosyasında kimlikler hazırdır. Sonuç dosyasını yeni talep girdisi gibi kullanmayın; kimlikli kaynak kopyayı kullanın.

Aynı analiz komutu aynı state dizininde kesilen işin tamamlanan satırlarından devam eder. Hatalı satırlar yeniden denenir. Giriş, kapsam, metadata içeriği, onaylı tanımlar, model/sözleşme veya kod değişince eski kararlar yeniden kullanılmaz. Yalnız metadata alım saatinin değişmesi yeniden hesaplama zorunluluğu yaratmaz.

Eksik kritik bilgileri onaylı belgeyle tamamlamak için opsiyonel [onaylı tanım dosyası](docs/REVIEWED_DEFINITIONS.md) kullanılır. Bu dosya zorunlu değildir; iş sözlüğü/lineage kataloğu olmadan demo ve ilk gerçek pilot çalışır.

## Mimari ve gerçek doğrulama

[Kararlar](docs/ARCHITECTURE.md), [ajan planı](docs/PLAN.md), [adapter sözleşmeleri](docs/ADAPTERS.md), [test kanıtları](docs/TEST_RESULTS.md), [bağımsız inceleme](docs/REVIEW.md).

Graphify yalnız geliştirme sırasında bu kod tabanını incelemek için dışarıdan kullanıldı. Uygulama paketinde Graphify kodu, aracı, bağımlılığı veya graph çıktısı bulunmaz. Harici geliştirme incelemesi bu repoya dahil değildir; iş bilgisayarında kurulması gerekmez. Uygulamanın kendi deterministik feature bağımlılık çözümleyicisi Graphify kullanmaz.

Test ve karşılaştırma:

```powershell
.\scripts\test.ps1
.\.venv\Scripts\python.exe -m riskmatch evaluate --output runtime\retrieval_comparison.json
```

BM25, embedding ve hibrit karşılaştırması için aynı etiketli sorguları alan altyapı hazır. Demo embedding deterministik hash simülatörüdür, Qwen değildir. Sentetik demo bankacılık doğruluk oranı ölçmez; gerçek pilot bağımsız uzman etiketli test seti ister.

## Sınırlar

Katalog taraması `ALL_*` erişimiyle sınırlıdır. Tamamlanma bu erişilebilir hedef kapsamı anlatır; tüm bankayı kapsadığı iddia edilmez. İlk sürümde serbest açıklama parser'ı sınırlıdır ve anlaşılmayan iş nitelemelerinde çekimser kalır. View SQL çıkarımı, tam kolon lineage ve farklı nesneler arasında otomatik güvenilir join keşfi yoktur. Bunlar varmış gibi gösterilmez. Gerçek servis protokolü, yetkiler, performans ve bankanın Windows ortamı iş bilgisayarında doğrulanmalıdır.
