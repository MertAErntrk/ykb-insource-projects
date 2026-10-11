# Bağımsız semantik entegrasyon incelemesi

Tarih: 2026-10-11. İncelenenler: pipeline.py, excelio.py, demo.py ve bunların semantik API ile bütünleşmesi. Banka ağına bağlanılmadı; geçici sentetik .xlsx dosyaları üzerinden gerçek giriş/çıkış yolu çalıştırıldı.

## Düzeltilen P1: iş qualifier'larının kaybı

İlk parser, tanıdığı anahtar kelimelerden facts üretirken geri kalan iş tanımını atlayabiliyordu. “Başvuru bazında beyan edilen aylık gelir TRY” ve “... yıllık gelir TRY” aynı tanıma dönüşebiliyordu. Bağımsız diğer incelemede net/brüt gelir ve kredi kartı/tüketici kredisi limitleri için de aynı risk doğrulandı.

Çözüm: parser yalnız desteklenen anlam taşıyan kalıpları ve sınırlı dilbilgisi parçalarını tüketir. Geriye kalan sözcük varsa `unparsed_description_qualifier` eklenir ve kesin destek engellenir. Aynı tanınmayan qualifier iki metinde de bulunsa otomatik eşleşme verilmez. Aylık/yıllık gelir açıkça belirtilen para birimine `/month` ve `/year` eklenerek ayrılır; para birimi yoksa uydurulmaz. Doğrulanmış yapılandırılmış facts, daha zengin tanımlar için genişleme noktasıdır.

Parser coverage listesi genel bir Türkçe anlama sistemi değildir. Serbest metne eklenen iş kısıtı listeyi genişleten bir stop-word ile susturulmamalıdır; yeni ifade önce bir dimension'a dönüştürülmeli veya yetersiz olarak kalmalıdır. Bilinmeyen ürün, ülke, net/brüt, vergi, tarih, kapsam koşulları güvenle çekimser bırakılır.

Ek düzeltme: açıklama cümlesinden sonra gelen ilk enum kodu kaybolabiliyordu. Nokta sonrası `1 sabit faiz` gibi kodlar artık korunur. Kod listeleri eksiksiz karşılaştırılır.

İkinci bağımsız adversarial inceleme boş verified fact'ların (`""`, whitespace, `[]`, `{}`) iki tarafta eşit bulunarak desteklenebildiğini gösterdi. Bu değerler, scalar boyutlarda yanlış tipler, boş kanıt ve unknown/belirsiz placeholder'ları artık eksik sayılır. Kontrol listesinin boş structured değerlerde TypeError vermesi de düzeltildi. `zero_policy=0` geçerli kalır ve regresyon testi vardır.

## Düzeltilen bütünleşme sınırı: onaylı talep facts girişi

İnceleme anındaki pipeline her talep tanımını Excel açıklamasından `parse_definition` ile oluşturuyordu. Formül parser'ı yalnız pay/payda ve sıfır/NULL politikalarını tanıdığı için oran taleplerinde entity, grain ve reference_date'ı tamamlayan bir kullanıcı giriş yolu bulunmuyordu. Dolayısıyla `assess_production` API'si doğrudan yapılandırılmış doğrulanmış facts ile `candidate` üretebilse de mevcut Excel/CLI yolu aynı sonuca ulaşamıyordu.

Bu sınır ana ajana bildirildi ve `reviewed.py` ile isteğe bağlı onaylı tanım sidecar girişi eklendi. `config.reviewed_definitions_path`, yapılandırma dosyasına göre çözülür. Satır/katalog kimlikleri, gözden geçiren kişi/tarih/referans, bütün tanımı kapsama beyanı ve her fact için verified_metadata + kanıt zorunludur. Bu beyan dijital imza veya otomatik anlamsal doğruluk kanıtı değildir; kurumsal gözden geçirme sürecinin kaydıdır.

İkinci incelemede sidecar'ın açık kaynak facts'ı atlayarak kritik boyutları silebildiği ve bilinmeyen NULL politikasının onayla tamamlanmasını yanlışlıkla çelişki saydığı bulundu. Ana ajan düzeltti: mevcut açık facts korunur; açık çelişki silemez; bilinmeyen politika onaylı kanıtla tamamlanabilir; açık sıfır davranışı değiştirilemez; orijinal formül kaldırılamaz. Bu sınırlar ayrı regresyon testleriyle doğrulandı.

Gerçek Excel -> config-relative sidecar -> pipeline -> metadata -> dependency -> production akışında iki hazır metrik ve onaylı tam oran tanımı ile `candidate` sonucu elde edildi. Aynı senaryoda kaynak anahtarı silinince sonuç `insufficient` oldu. Böylece üretim adayı artık yalnız doğrudan API testlerine sınırlı değildir.

## Doğrulanan davranışlar

- `Halihazırda bildiriliyor mu=Evet` yalnız orijinal kullanıcı bilgisi olarak kalıyor; semantik doğruluk veya uzman kararı üretmiyor.
- Tam fiziksel isim aynı olsa da doğrulanmış/beyan edilmiş gelir farkı desteklenmiyor.
- İki tarafta para birimi eksikliği `missing:unit` üretiyor; TRY varsayılmıyor.
- Desteklenen dar statik şablon (`Başvuru bazında beyan edilen gelir TRY.`) gerçekten `supported` oluyor; çekimser kapı bütün eşleşmeleri körlemesine engellemiyor.
- Güvenli Excel `=IF(B3=0,-1,B2/B3)` ifadesi yalnız iki ad bağımlılığı ve sıfır kuralına dönüştürülüyor. NULL politikası ve referans tarihi icat edilmiyor; üretim `insufficient` kalıyor.
- Model yorumları facts ve semantic_status üzerinde yetkili değil.
- Girdi ve metadata/kod/yapılandırma imzaları checkpoint anahtarına dahil; üretim ve graph her devamda tekrar değerlendiriliyor.

## Kimlik dayanıklılığı notu

Talep Kimliği sütunu varsa giriş katmanı bu kimliği korur. Yoksa üretilen kimlik workbook bytes + sheet + row temellidir. Aynı dosyanın tekrar çalıştırılmasında kararlıdır; kimlik sütunu olmadan orijinal workbook düzenlenirse değişir. Uzun süreli uzman kararı takibi için kalıcı Talep Kimliği sütunu bulunan girdi kullanılmalı veya onaylı kimlikler girişe aktarılmalıdır. Bu, aynı dosyayı resume etme doğruluğunu bozmaz ancak farklı dosya sürümleri arasında kimlik sürekliliğini sınırlar.

## Gerçek test sonucu

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m unittest discover -s tests -p test_semantics.py
python -m unittest discover -s tests -p test_semantic_integration.py -v
python -m unittest discover -s tests -p test_reviewed.py -v
```

- Semantik suite: **41/41 geçti**, 0.028 saniye; son sertleştirmeler sonrası semantik + bağımsız Excel suite birlikte **46/46 geçti**, 0.492 saniye.
- Bağımsız Excel/pipeline kabul suite: **5/5 geçti**, 0.321 saniye.
- Onaylı sidecar sınırları ve uçtan uca üretim adayı suite: **8/8 geçti**, 0.282 saniye.
- İlk entegrasyon test denemesinde fixture yanlış `lexical` stratejisi kullanıyordu; modül sözleşmesindeki geçerli `bm25` ile düzeltildi. Hata bastırılmadı; aynı beklenen sonuçlar yeniden doğrulandı.

Bu sonuçlar sentetik yazılım testidir; Oracle erişimi, Qwen servis sözleşmesi veya gerçek bankacılık eşleştirme doğruluk oranı kanıtı değildir.
