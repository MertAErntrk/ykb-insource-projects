# Onaylı tanımların isteğe bağlı eklenmesi

İlk sürüm belirsiz tanımları tamamlamak için LLM'ye yetki vermez. Bir uzman kanıtı gözden geçirip yapılandırılmış tanımı yerel JSON dosyasına ekleyebilir. Bu özellik iş sözlüğünün zorunlu bağımlılık olması anlamına gelmez; dosya yoksa sistem eksik bilgiyi raporlayarak çalışır.

`config/reviewed-definitions.example.json` dosyasını `reviewed.local.json` olarak kopyalayın. Config'e `"reviewed_definitions_path": "reviewed.local.json"` ekleyin. Göreli yol config dosyasına göredir. `review` alanında onaylayan ekip/rol, tarih ve kaynak belge referansını yazın. Bu alanlar dijital imza veya otomatik doğruluk garantisi değildir; kurum onay süreci gerekir.

`requests` anahtarları Excel'deki kalıcı `Talep Kimliği`; `catalog` anahtarları Adaylar sayfasındaki `Katalog Kimliği` değerleridir. Bulunmayan kimlik hata verir. Her tanım `covers_full_definition: true` ile bütün iş tanımının incelendiğini beyan eder. Her fact `value`, `source: verified_metadata`, boş olmayan `evidence` içerir. Model çıkarımı, boş veya belirsiz değer onaylı kanıt olamaz.

Örnek biçim (değerler sentetiktir, kurum anlamı olarak kullanılmaz):

```json
{
  "schema_version": 1,
  "version": "review-001",
  "review": {"reviewer": "RİSK EKİBİ", "reviewed_at": "2026-10-11", "reference": "ONAYLI TANIM REFERANSI"},
  "requests": {
    "KALICI_TALEP_KIMLIGI": {
      "covers_full_definition": true,
      "facts": {
        "grain": {"value": "customer", "source": "verified_metadata", "evidence": "Onaylı belge: müşteri başına bir kayıt."},
        "reference_date": {"value": "application_date", "source": "verified_metadata", "evidence": "Onaylı belge: başvuru tarihi referans alınır."}
      }
    }
  },
  "catalog": {}
}
```

Bu iki fact tek başına tam bir tanım değildir. İşlem, yön, ölçü, toplulaştırma sırası/gruplama, pencere sınırları, NULL/zero, anahtar ve kapsam gibi kritik boyutlar gerektiğinde ayrıca sağlanmalıdır. Boyut sözlüğü `docs/CONTRACT.md` içindedir. Özgün açıklamadaki bilinen facts korunur, açık çelişki override ile silinemez. `None`/bilinmeyen bir zero/NULL kuralı kaynak kanıtla tamamlanabilir. Kaynak formül silinemez veya ters çevrilemez.

Oran için `formula` yalnız `{op: divide, numerator: alan_adı, denominator: alan_adı, zero: sayı veya null, null: politika veya null}` biçimindedir. Metriklerden üretim adayı, hazır alan aramasından sonra ele alınır; iki operand zaten supported olmalı, aynı doğrulanmış nesne/anahtar/grain/referans/scope üzerinde bulunmalı ve tüm özel durumlar bilinmelidir. Günlük farklı kişi sayıları toplanarak dönemlik farklı kişi sayısı üretilmez.

`tests/test_reviewed.py` gerçek geçici Excel + config + sidecar + sentetik katalog yolunu çalıştırır ve üretim adayıyla eksik anahtar engelini doğrular. Canlı banka kural doğrulaması değildir.
