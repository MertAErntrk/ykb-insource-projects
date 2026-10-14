# Çözüm Merkezi için ilk kayıtlar

Amaç, “Yeni Gelen Kayıtlar” listesini ve “Üzerime al” düğmesini gözlenen çağrılarla eşleştirmektir. Fotoğrafta seçili `account` isteği tek başına bu iki işlemin API'sini göstermez. Görselde yalnızca Headers açık; yanıt alanları ve düğmenin gönderdiği gövde görülmüyor.

## 1. Yeni Gelen Kayıtlar

1. Uygulamaya normal kurumsal oturumunla gir.
2. F12 → Network → Fetch/XHR seç. **Preserve log** açık, **No throttling** seçili olsun.
3. Çöp kutusu/Clear ile eski istekleri temizle.
4. Yeni Gelen Kayıtlar ekranını aç veya listeyi yenile. Liste oluşana kadar bekle.
5. Listeyi dolduran aday çağrılarda Response/Preview içinde kayıt listesi ve toplam/sayfalama alanlarına bak. Sadece adrese göre kesin karar verme.
6. **Export HAR (sanitized)** ile `yeni-gelen-kayitlar.har` adıyla yerel kaydet.
7. Kataloğa uygulama **Çözüm Merkezi**, sayfa **Yeni Gelen Kayıtlar**, işlem **Listeyi getir** etiketiyle yükle.

## 2. Talep detayı

1. Liste ekranında Network kayıtlarını tekrar temizle.
2. Normalde inceleyeceğin bir talebe gir; sayfanın yüklenmesini bekle.
3. `talep-detayi.har` kaydını al.
4. Uygulama **Çözüm Merkezi**, sayfa **Talep detayı**, işlem **Detay aç** etiketiyle yükle.

## 3. Üzerime al

Bu düğme uygulamadaki kaydı değiştirir. Yalnızca normal işinde zaten üzerine alacağın bir talepte veya uygun test ortamında kayıt al; sırf keşif için fazladan üretim işlemi yapma.

1. Talep detay sayfası tamamen yüklendikten sonra Network listesini temizle.
2. **Üzerime al** düğmesine bir kez tıkla.
3. O anda oluşan isteklerde yöntem, Payload ve Response'u incele. GET/POST/PATCH gibi yöntem adını işlem amacının tek kanıtı sayma.
4. `uzerime-al.har` kaydını al.
5. Uygulama **Çözüm Merkezi**, sayfa **Talep detayı**, işlem **Üzerime al** etiketiyle yükle.

## Paylaşılabilecek yapısal özet

Ham HAR ve Copy as cURL çıktısı cookie, token ve iş verileri içerebilir. Bunları doğrudan göndermek yerine aşağıdaki komutla değerleri kaldırılmış yapısal özet çıkar:

```powershell
python inspect_capture.py yeni-gelen-kayitlar.har --application "Çözüm Merkezi" --page "Yeni Gelen Kayıtlar" --action "Listeyi getir" --output liste-ozeti.json
python inspect_capture.py uzerime-al.har --application "Çözüm Merkezi" --page "Talep detayı" --action "Üzerime al" --output atama-ozeti.json
```

Dosyayı göndermeden önce açıp incele. Gerçek alan adları yerine `[kurum içi adres]` yazılır; yakalanan alan değerleri ve auth sırları kaldırılır. Alan isimleri, doküman açıklamaları ve verdiğin etiketler yine kurum bilgisi olabilir.

Yalnızca ekran görüntüsü paylaşacaksan ilgili isteğin **Request Method**, kimlik bilgileri ve gerçek kayıt kimliği çıkarılmış **URL yolu**, **Payload alan adları** ve **Response alan yapısı** yeterlidir. Authorization/Cookie/Set-Cookie/CSRF değerlerini ve kişisel alan değerlerini paylaşma.

## Qwen için gereken bilgi

- Tam model ID'si.
- Endpoint bir base URL mi, yoksa tam chat/completions adresi mi?
- İstek/yanıt biçimi OpenAI-compatible mı? Bilinmiyorsa anahtarları ve kurum adresini çıkardığın çalışan bir örnek isteğin yapısı yeterlidir.
- Kimlik doğrulama gerekiyor mu? Anahtarın kendisini göndermene gerek yok; yerel ayarlar paneline gir.

`python diagnose.py` çıktısı Python sürümü, işletim sistemi ve 32/64 bit bilgisini verir. Qwen'e gerçek bağlantı testi iş bilgisayarından yapılmalıdır.
