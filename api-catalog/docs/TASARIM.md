# Tasarım ve kabul ölçütleri

## Amaç

Bir ekran URL'si veya trafik kaydından, geliştiricinin “hangi API ne yapıyor, hangi alanı nasıl alırım?” sorusunu yanıtlayabileceği dosya tabanlı katalog üretmek. İlk örnek kullanım: takıma gelen kayıtların listesi, kaydı açan kişi, müşteri CIF ve açıklama alanlarını bulmak. Bir uygulama arayüzü veya hedef işlemleri otonom yapan ajan bu teslimatın kapsamında değildir.

## Bileşenler

1. `recorder.py`: Playwright ile URL açma / normal kullanıcı gezinmesini gözleme / ayrı profil ile giriş. Ağ çağrıları HAR biçimli nesnelere bellekte çevrilir. Her tamamlanan istek hemen ayrıştırılıp SQLite'a işlenir.
2. `discovery.py`: yalnızca yüklenen JavaScript dosyalarındaki statik adresler. Dinamik ifadeleri tam çözmez. Adaylar hedefe çağrı yapılarak denenmez.
3. `importers.py`, `privacy.py`: HAR ve yerel OpenAPI/Swagger JSON ayrıştırma, kimliklerin genellenmesi, değerlerin kaldırılması, alan/tür çıkarımı. Yeni uç nokta üretimini model değil bu katman belirler.
4. `store.py`: uygulama + origin + yöntem + genellenmiş yol + GraphQL operasyonuna göre birleştirme. Dosya/bağlam parmak iziyle tekrar içe aktarım önlenir; gözlem ve şema varyantları korunur.
5. `qwen.py`: standart sohbet veya yapılandırılabilir JSON soru-cevap endpoint'i. Tek uç nokta için analiz; ajan döngüsü ve araç çağrısı yok. Model mevcut uç noktayı değiştiremez; alan yorumları gözlenen alan yollarıyla sınırlanır.
6. `exporters.py`: Markdown, makine kataloğu, uygulama/origin bazında kısmi OpenAPI 3.1 ve Python yardımcı modülü. Kaynaklar ve çıkarımlar görünürdür.
7. `cli.py`: scan/login/record/import/analyze/search/show/stats/export/doctor/demo komutları.

## Ana akış

- Bir defalık login → URL scan → ağ istekleri + yüklenen JavaScript adayları → yerel katalog → isteğe bağlı Qwen açıklaması → çıktılar.
- Açılmayan düğmeler için record veya HAR eklenir. Kaynaklar aynı katalogda birleşir.
- Trafikte API belgesi yüklenirse otomatik içe alınır. Diskteki belge de elle import edilebilir. Belge URL'leri tahmin edilip otomatik aranmaz.

## Doğruluk kararı

- `har`: bu oturumda çağrılmış olma kanıtıdır; yöntem ve yanıt yapısı gözlemdir. 200 OK, bütün otomasyon koşullarının bilindiğini göstermez.
- `openapi`: belgelenmiş sözleşmedir; bu kurulumda yetki/çalışma kanıtı değildir.
- `javascript`: metinsel adaydır. Kısmi veya kullanılmayan kod olabilir. Yöntem bilinmiyorsa UNKNOWN tutulur; OpenAPI ve çalıştırılabilir örnek üretilmez.
- Model açıklamaları hiçbir zaman “canlı doğrulandı” olarak işaretlenmez. Alan adlarından yapılan anlam çıkarımları belirsizliği korur.
- Tek örnekten required, tüm enum değerleri, bütün sayfalama kuralları veya tüm hata cevapları belirlenmez.

## Oturum ve aksiyonlar

Tarayıcı ayrı profil kullanır; kullanıcının açık günlük Chrome profilini kopyalamaz. İlk giriş ve gerektiğinde yeniden giriş kullanıcının normal akışıyla olur. Profil kurum bilgisayarında kalır. Katalog oluşturma hedef çağrıları yeniden oynatmaz. Üretilen Python modülünde yazma yöntemleri açık allow_write seçimi gerektirir; otomatik tekrar ve otomatik cookie aktarımı yoktur.

## Kabul ölçütleri

- Arayüz/web sunucusu olmadan CLI'dan çalışmalı.
- HAR/Qwen/rapor çekirdeği Python 3.9 sözdizimiyle uyumlu olmalı; canlı tarayıcı da Python 3.9'u desteklemeli; bu sürüm için Playwright/greenlet wheel sürümleri sabitlenmeli.
- URL taraması sayfa yüklenmesindeki gerçek çağrıyı çıkarmalı ve tıklanmayan aksiyonu en fazla aday olarak göstermeli.
- Kaydı açan kişi/CIF/açıklama alanları yanıtta varsa yol ve türleri katalogda aranabilmeli.
- Normal soru-cevap model servisiyle, tool calling olmadan açıklama üretilmeli.
- Aynı kayıt iki kez import edilince şişme olmamalı; farklı origin/yöntemler karışmamalı.
- Gövde değerleri ve auth değerleri veritabanına/raporlara taşınmamalı.
- Çalıştırılmamış API veya model tahmini doğrulanmış diye sunulmamalı.

## Bilinen geliştirme alanları

Kuruma özgü SSO adaptörü, işlev bazında doğrulanmış Python fonksiyonları, kontrollü aksiyon zincirleri, JavaScript AST analizi, YAML belgeler, gelişmiş OpenAPI referansları ve WebSocket içerikleri sonraki geliştirmelerdir. Bunlar olmadan katalog çalışır; kapsamın eksiksiz olduğu iddia edilmez.
