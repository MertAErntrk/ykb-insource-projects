# Uygulama planı ve görev sınırları

## Mimari karar özeti
- Python 3.9+ modüler yerel uygulama; CLI + ince Tk masaüstü arayüzü. HTTP sunucusu, vektör DB, agent framework gerekli değil.
- Arama aday bulur. Ayrı deterministik kanıt kapısı karar verir. LLM yorumları hiçbir zaman eksik kritik kanıtı tamamlamaz.
- Tek snapshot hedef ortam/container kapsamını taşır. Oracle erişimi envanter görünürlüğüyle sınırlıdır.
- Formüller sınırlı AST, müşteri verisine uygulanmaz. Bağımlılıklar tüm çalışma kitabında kurulur.
- İlk sürüm kurum sözlüğü/ETL kataloğu olmadan çekimser çalışır; doğrulanmış ek tanımlar sonradan versioned snapshot'a eklenebilir.
- Demo ve gerçek mod ayrı, gerçek başarısızsa fallback yok. Bu teslim sırasında banka ağ çağrısı yok.
- Yeniden devam: giriş dosyası, katalog içeriği, parser/uygulama sürümü, model kimlikleri ve yapılandırma imzası aynıysa satır durumu kullanılır; hatalı satırlar yeniden denenir.

## Ajanlar ve dosya sahipliği
| Rol | Erişilebilir model / düşünme | Sorumluluk | Yazdığı dosyalar |
|---|---|---|---|
| Ana ajan | Oturumun modeli | Sözleşme, entegrasyon, Excel, config, UI, nihai kontrol | Diğer tüm proje dosyaları |
| semantics | gpt-6-astra / high | Mimari eleştiri, anlam/bağımlılık kuralları | semantics.py, test_semantics.py, ARCHITECTURE.md |
| adapters | gpt-6-sol / high | Oracle, Qwen, BM25/embedding/RRF | adapters/, retrieval.py, ilgili testler, ADAPTERS.md |
| graphify_review | gpt-6-astra / high | Geliştirme sırasında harici Graphify analizi ve bağımsız kalite incelemesi | Ayrı risk-feature-analysis klasörü; uygulamada yalnız inceleme notları |

En fazla 4 eşzamanlı ajan (ana dahil). Ortak sözleşme önce oluşturuldu. Alt ajanlara yalnız gerekli bağlam verildi. Hedef ajan bütçeleri yaklaşık 15–16k token, kısa devir özeti <=600 kelime; gerçek token telemetrisi yok, tüketim/tasarruf yüzdesi iddia edilmez. Rutin belge işi ana ajanda, ayrı ajan çağrısı yok.

## Aşamalar
1. Çalışma alanı/skill keşfi, sözleşme, mimari eleştiri.
2. Semantik çekirdek, read-only adapter/retrieval, Graphify hazırlığı paralel.
3. Excel koruma, demo dosyaları, devam/checkpoint, CLI/UI entegrasyonu.
4. Birim + sahte servis + uçtan uca test, bağımsız yanıltıcı eşleşme incelemesi.
5. Graphify son yenileme, demo sonuçları, Windows kurulum ve gerçek pilot kontrol listesi.

Test kanıtı docs/TEST_RESULTS.md içinde oluşturulur. Banka testi yalnız iş bilgisayarında kullanıcı tarafından yapılabilir.

Kullanıcının kapsam düzeltmesi: Graphify ürünün parçası değildir. Geliştirme betikleri, Graphify testleri ve graph çıktıları uygulama klasörü dışındaki `risk-feature-analysis` içine ayrıldı; uygulama zip'ine dahil edilmez.
