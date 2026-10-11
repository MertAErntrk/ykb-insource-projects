# Bağımsız entegrasyon incelemesi — 2026-10-11

Bu inceleme yalnız kaynak kod, sahte transport/Oracle bağlantısı ve sentetik testlere dayanır. Kurum ağına erişilmedi; gerçek Oracle/Qwen yanıt sözleşmesi veya model kalitesi doğrulanmadı.

## Düzeltilen bulgular

- **P1, Oracle yorumları semantiğe ulaşmıyordu.** Envanter kaydı boş `definition.facts` içerdiğinde semantik katman yorum metnini tekrar parse etmiyordu. Oracle adapter artık kolon adı ve kaynak kolon yorumunu `parse_definition` ile işler, yorum kaynaklı fact'leri `verified_metadata` olarak işaretler. Bu, yorumun kaynak olarak doğrulandığını belirtir; iş anlamının kesin doğru olduğunu iddia etmez. Gerçek adapter snapshot → `evaluate_candidate` regresyon testi eklendi.
- Oracle bağlantısı `tcp_connect_timeout` (varsayılan 10 saniye, en çok 60), round-trip çağrıları `call_timeout` (varsayılan 30 saniye, en çok 120) ile sınırlandı. Gerçek sürücüyle etkisi henüz doğrulanmadı. [python-oracledb bağlantı parametreleri](https://python-oracledb.readthedocs.io/en/stable/api_manual/connect_params.html) ve [Connection.call_timeout](https://python-oracledb.readthedocs.io/en/stable/api_manual/connection.html) bu iki ayarı belgeliyor.
- Anahtar metadata'sında yalnız `STATUS='ENABLED'` ve `VALIDATED='VALIDATED'` olan P/U kısıtları tutulur. Devre dışı veya doğrulanmamış constraint'i anahtar kanıtı saymamak gerekir. [Oracle ALL_CONSTRAINTS](https://docs.oracle.com/en/database/oracle/oracle-database/18/refrn/ALL_CONSTRAINTS.html) bu durum alanlarını açıklar. P/U kaydı yine ilişki veya kolon lineage kanıtı değildir.
- Embedding cache anahtarı artık model/sürüm/boyut yanında tam endpoint, request template ve response mapping sözleşmesinin hash'ini taşır. Böylece aynı model adıyla değişen backend'in eski vektörleriyle karışma riski giderildi. Cache okumasında sonlu sayısal değer doğrulaması var.
- `test_adapter_integration.py`, bağlantısız config kontrolü, eksik gerçek mod izni, LLM şema dışı alanı ve taşınmayan ek gizli alan, embedding sözleşmesi değişimi için regresyon kontrolleri içerir.
- Gerçek Oracle tarama hatası testi, sonucun `incomplete`/`unknown` kaldığını ve sentetik katalog fonksiyonunun hiç çağrılmadığını doğrular.

## Son entegrasyon durumu

Ana ajan düzeltmesiyle `load_config(..., mode='real')`, OracleAdapter yapıcısını da bağlantı kurmadan çağırır; şema/kimlik/timeout biçimi LLM/embedding sözleşmesiyle birlikte doğrulanır. Canlı erişim ve ortam sırları ancak kullanıcının gerçek analiz komutunda sınanır.

`pipeline.py` gerçek Oracle tarama hatasını eksik snapshot ve satır hatasına çeviriyor; sentetik kataloğa geri dönmüyor. Servis timeout'u ve bozuk model cevabı da satırı `incomplete` yapıyor. `run_info` açık endpoint veya env secret değerlerini yazmıyor, yalnız model kimliği/sürümü gösteriyor. Yerel checkpoint, cache ve sonuç Excel'i gerçek metadata içerebilir; kurumun izin verdiği yerel dizinde tutulmalı ve Graphify/dış modellerin kapsamına sokulmamalı.

Graphify kaynak manifest kontrolü yalnız harici geliştirme incelemesine aittir. Bu araç ve betikleri uygulama reposunda bulunmaz; iş bilgisayarında çalıştırma adımı değildir.

## İş bilgisayarına uyarlanan gerçek mod bağımlılığı

Python 3.9.7 için `requirements-real.txt` Oracle sürücüsü:

```text
oracledb==4.0.2
```

[PyPI oracledb 4.0.2](https://pypi.org/project/oracledb/4.0.2/) Python 3.9 desteğini ve Windows x64 wheel'ini listeliyor. Önceki 3.12 hedefinin 26.0.1 sürümü, kullanıcının iş bilgisayarı 3.9.7 olduğu için değiştirildi. 4.0.2 ve transitif bağımlılıklar `requirements-real.lock` hash'leriyle gerçek 3.9.7 ortamına kuruldu; sürücü import'u ve `ConnectParams` timeout/TLS parametreleri bağlantısız doğrulandı. Thin mode varsayılandır. Gerçek Oracle bağlantısı yapılmadı; kurumun sürücü onay listesi ayrıca geçerlidir.
