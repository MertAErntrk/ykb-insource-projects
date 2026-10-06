# Strateji Cevap Motoru — Uygulama planı v1

Tarih: 2026-10-06. Kaynaklar: 2026-09-23 toplantı notu ("Strateji Çıktılarının AI ile Yorumlanması ve
Soru-Cevap Mekanizması"), Boran'ın 2026-10 maili (smile2025, "Kurumsal ve Ticari Kredi Onay Yetki
Seviyeleri.xlsx", H221 ve yetki seviyesi örnekleri), BriefMind'dan taşınan LLM altyapısı (kurum içi
Qwen3.8-27B, vLLM, OpenAI uyumlu uç nokta).

Durum: smile2025, yetki Excel'i ve tam bir başvuru JSON'u henüz elimizde değil. Bölüm 12'deki varsayımlar
dosyalar gelince doğrulanacak; plan, katalog ve kod o zaman güncellenecek. Prototip iskeleti (bölüm 16)
sentetik veriyle uçtan uca çalışıyor ve 62 testle doğrulanıyor.

## 0. Tek paragraf

Strateji ekibi, kurumsal ve ticari kredi karar motorunun çıktıları hakkında ayda yaklaşık 120 çağrı alıyor;
çoğu "bu müşteride neden şu kural çalıştı", "onay yetki seviyesi neden bu" gibi tekrar eden kural soruları
ve elle cevaplanıyor. Motor, Smile'dan gelen çağrıyı okur, kural kodunu ve müşteri numarasını bulur, o
başvurunun strateji girdi/çıktı JSON'undan olguları çıkarır, strateji ekibinin onayladığı şablonla cevap
taslağı yazar; taslak ekipten biri onaylayınca gönderilir. LLM yalnızca soruyu anlamak ve birden çok kuralı
tek akıcı metne toplamak için kullanılır; sayı, tarih ve kural kodu her zaman koddan gelir. Hedef: 2027
planlama dönemine çalışan prototip ve ölçüm sonuçları.

## 1. Amaç ve kapsam

| | Faz 1 (prototip, 2027 planlama dönemi) | Sonrası |
|---|---|---|
| Girdi | Smile çağrısı: serbest metin + müşteri/başvuru numarası | Aynı |
| Çıktı | Cevap taslağı + durum (otomatik / inceleme gerekli) + gerekçe notları | Smile'a otomatik yazma |
| Sınıflar | Çıktı soruları (kural, yetki seviyesi) tam; girdi ve hata soruları yalnızca yönlendirme | Girdi soruları için veri kaynağı açıklamaları |
| Kural kapsamı | smile2025 dağılımına göre en sık N kural (hedef: çağrıların en az %70'i) | Katalog genişler |
| Onay | İnsan onayı zorunlu; düzeltmeler kaydedilir | Güven eşiğine göre otomatik gönderim |
| Arayüz | Yok: komut satırı, toplu çalıştırma, basit onay listesi | Ekran ya da chatbot |

Kapsam dışı (her fazda): girdi verisini düzeltmek, kural değişikliği önermek, strateji dışı sorular, kredi
kararını değiştirmek.

## 2. Bugünkü akış ve hedef akış

```
Bugün:
  Şube/Satış ─► Smile çağrısı ─► strateji ekibi: başvuruyu bulur, JSON'a bakar, kuralı hatırlar,
                                  cevabı elle yazar ─► Smile'a cevap

Faz 1:
  Smile çağrısı ─► sınıflandırma (sınıf, kural kodu, CIF) ─► başvuru JSON'u ─► kural başına çözüm (olgular)
                ─► taslak (onaylı şablon, tek selamlama, tek kapanış) ─► guardrail denetimi ─► durum
                ─► strateji ekibi onaylar / düzeltir ─► Smile'a cevap; düzeltme kaydedilir (faz 2'nin verisi)
```

## 3. Çağrı sınıfları

| Sınıf | Örnek soru | Cevabın kaynağı | Faz 1'de |
|---|---|---|---|
| çıktı | "H221 neden çalıştı?", "Yetki seviyesi neden 8?" | Başvuru JSON'u + katalog şablonu (+ yetki matrisi) | Tam otomatik taslak |
| girdi | "KKB verisi yanlış geliyor", "Bilanço eksik görünüyor" | Veri kaynağı, güncelleme tarihi, düzeltme kanalı | Yönlendirme notu; kural da sorulmuşsa kural cevabı |
| hata | "Strateji ekranı hata verdi, sonuç gelmedi" | BT / strateji destek | Yönlendirme, otomatik cevap yok |

Sınıflandırma anahtar kelimeyle başlar; LLM varsa sınıfı LLM belirler ama kod ve numara üretemez (G1).
smile2025 analizi gerçek dağılımı verir; anahtar kelime listeleri o veriyle düzeltilir.

## 4. Mimari ve bileşenler

```
cagri.json ─► siniflandirici.py ─► veri.py ─► cozumleyici.py ─► yazici.py ─► Taslak ─► insan onayı
                │ regex + anahtar       │ başvuru     │ katalog +        │ tek metin, guardrail,
                │ kelime, isteğe        │ JSON'u      │ matris.py        │ isteğe bağlı LLM yeniden yazım
                │ bağlı LLM             │ (dosya;     │ (olgular)        │
                └── katalog.py ─────────┘ hedefte DB/API)
                    kurallar.json, yetki_matrisi.json, sürüm ve geçerlilik tarihi
```

| Bileşen | Görev | Deterministik / LLM | Kaynak |
|---|---|---|---|
| `siniflandirici.py` | sınıf, kural kodları, CIF | deterministik; LLM zenginleştirir | yeni |
| `katalog.py` | kural tanımı, şablon, onay durumu, geçerlilik tarihi | deterministik | yeni |
| `veri.py` | başvuru JSON'u (CIF + tarih) | deterministik | yeni; hedefte DB/API uyarlayıcısı |
| `cozumleyici.py` | kural → olgular (liste_kosul, yetki_matrisi, sabit) | deterministik | yeni |
| `matris.py` | limit → seviye, nihai = max(matris, H) | deterministik | yeni |
| `yazici.py` | taslak, doğrulama, yeniden yazım | deterministik; LLM yalnızca yeniden yazım | yeni; dil kontrolü BriefMind'dan |
| `llm.py` | OpenAI uyumlu istemci, `<think>` temizliği, JSON şema | LLM | BriefMind/llm.py sadeleştirildi |
| `analiz_smile.py` | çağrı dökümü analizi, MVP kapsamı | deterministik | yeni |
| `motor.py` | boru hattı ve komut satırı | — | yeni |

## 5. Veri kaynakları ve entegrasyon

| Kaynak | İçerik | Prototipte | Hedefte | Netleşmesi gereken |
|---|---|---|---|---|
| Smile dökümü (smile2025) | 2025-01 – 2026-07 çağrılar ve cevaplar | Excel; analiz + değerlendirme seti | Smile'dan okuma/yazma (API ya da dışa aktarım) | sütunlar; başvuru no var mı; cevap metni var mı |
| Başvuru JSON'u (Indata / Outdata) | strateji girdi ve çıktıları | `ornekler/basvurular` (sentetik); gerçek örnekler `veri/` | DB tablosu ya da servis; CIF + tarihle sorgu | nerede duruyor, nasıl erişilir, saklama süresi |
| Kural kataloğu | kod, açıklama, mantık, şablon, geçerlilik | `katalog/kurallar.json` (H221, YETKI) | aynı dosya; sahibi strateji ekibi; değişiklik PR ile | mevcut bir katalog var mı, kaç kural var |
| Yetki matrisi | limit eşikleri → seviye | `katalog/yetki_matrisi.json` (sentetik) | Excel'den üretilen JSON | hangi ağırlıklı limit; segment/yola göre ayrı tablo var mı |
| LLM | Qwen3.8-27B, vLLM, OpenShift | `config.json` (BriefMind ile aynı uç nokta biçimi) | aynı | — |

Entegrasyonun kritik noktası başvuru JSON'una erişim. Prototipte JSON dosya olarak verilir; bu, değeri
göstermek için yeterlidir ama pilotta ekip her çağrı için JSON'u elle çekmek zorunda kalır. Bu yüzden
"CIF + tarih → JSON" sorgusunun nereden yapılabileceği workshop'ta ilk gündem maddesi.

## 6. Kural kataloğu

Katalog, projenin iş tarafındaki ana ürünü. Her kural için üç şey tanımlanır: veride nereye bakılacak,
hangi koşul süzülecek, hangi şablon doldurulacak. Biçim `katalog/kurallar.json`:

| Alan | Anlamı |
|---|---|
| `kod`, `ad`, `sinif`, `aciklama` | Kural kimliği ve iş açıklaması (RAG için de kaynak metin) |
| `gecerlilik.baslangic / bitis` | Kural değişince eski kayıt silinmez, `bitis` yazılır, aynı kodla yeni kayıt eklenir; cevap başvuru tarihinde geçerli sürümle üretilir |
| `cozum.tur` | `liste_kosul` (H221 gibi: liste, koşul, tarih penceresi, en eski/en yeni, banka alanı), `yetki_matrisi`, `sabit` (yalnızca açıklama) |
| `sablonlar.<ad>.metin` | `{cif}`, `{ay_yil}`, `{nominal}` gibi alanlarla şablon; dallanma şablon adıyla (`baska_banka`, `kendi_banka`, `kayit_yok`) |
| `sablonlar.<ad>.onay` | `taslak` ise cevap otomatik gitmez, "inceleme gerekli" olur; onaylı ise kim, ne zaman yazılır |
| `kapanis`, `anahtar_kelimeler` | Kapanış cümlesi (varsayılan katalog başında); kod yazılmamış sorular için konu eşleme |

H221 bugün Boran'ın mailindeki metinle "onaylı"; `kendi_banka` ve `kayit_yok` dalları taslak. YETKI
şablonlarının tamamı taslak. Katalogdaki her taslak şablon workshop'ta ya onaylanır ya düzeltilir.

Sahiplik: katalog strateji ekibinindir. Teknik ekip biçimi korur, doğrulama testini çalıştırır (bozuk katalog
yüklenmez), her değişiklik PR ile geçer. Kural değişikliği = katalogda yeni sürüm satırı; kod değişmez.

## 7. Onay yetki seviyesi

Boran'ın örneğinden çıkan mantık; tutarlar sentetik örnek başvurudan (gerçek değerler repoya yazılmadı):

| Alan | Değer | Rol |
|---|---|---|
| NOMINALLIMIT | 4.250.000,00 | matrise giren limit (sarı) |
| WEIGHTEDLIMWCASHCOLL | 1.375.000,00 | matrise giren ağırlıklı limit (sarı; hangisi olduğu Excel ile netleşecek) |
| WEIGHTEDLIMWOCASHCOLL | 590.000,00 | nakit teminat hariç ağırlıklı limit |
| AUTHLEVELFORMATRIX | 3 | matrisin verdiği seviye (yeşil): iki limitten yüksek seviyeye çıkaran |
| AUTHLEVELFORHRULES | 8 | H kurallarının zorladığı seviye |
| APPROVALAUTHORITY | 8 | nihai seviye = max(matris, H) (varsayım, Boran'ın örneğiyle tutarlı) |

Motor, matrisi kendisi de hesaplar ve JSON'daki seviyeyle karşılaştırır; fark varsa "inceleme gerekli"
notu düşer (matris dosyası eskimiş ya da yanlış ağırlıklı limit seçilmiş demektir). Seviyeyi yükselten H
kuralı çıktıdaki kural listesinden bulunur ve cevapta adıyla geçer. Excel gelince: eşikler JSON'a aktarılır,
`tests/test_matris.py` Boran'ın mailindeki örnek değerlerle (→ 3) doğrulanır; segment ya da
`PATHID`'ye göre farklı tablo varsa matris dosyası tablo başına ayrılır.

## 8. LLM kullanımı ve guardrail'ler

LLM (Qwen3.8-27B) yalnızca iki yerde: (1) çağrı metnini sınıflandırma, (2) birden çok kuralın şablon
metnini tek akıcı mesaja toplama. Tek kurallı cevaplarda LLM'e hiç gidilmez; şablon metni olduğu gibi
gider. LLM servisi yoksa motor aynı cevapları şablonla üretir (BriefMind'daki LLM'siz yedek mantığı).

| # | Guardrail | Nerede |
|---|---|---|
| G1 | LLM sayı, tarih, kural kodu, müşteri numarası üretemez: sınıflandırmada metinde/katalogda olmayan kod ve numara atılır; yeniden yazımda taslakta olmayan olgu geçerse LLM metni reddedilir | `siniflandirici.py`, `yazici.dogrula` |
| G2 | Taslaktaki olgu LLM metninde düşmüşse de reddedilir | `yazici.dogrula` |
| G3 | Onaysız (`taslak`) şablonla üretilen cevap otomatik gitmez | `cozumleyici`, `yazici.kur` |
| G4 | Sorulan kural başvuru çıktısında çalışmamışsa cevap incelemeye düşer | `motor.cevapla` |
| G5 | Veri ile katalog tutarsızsa (koşulu sağlayan kayıt yok, matris seviyesi uyuşmuyor, max varsayımı tutmuyor) incelemeye düşer | `cozumleyici` |
| G6 | İngilizceye kayan ya da kesik LLM cevabı atılır; düşünme kapalı, `<think>` temizlenir | `yazici.ingilizce_mi`, `llm.py` |
| G7 | Cevapta müşteri adı yok, yalnızca numara; cevaplar ve loglar müşteri verisi içerdiği için repoya girmez | şablonlar, `.gitignore` |
| G8 | Hata sınıfı ve kodu bulunamayan çağrılar otomatik cevap almaz, yönlendirilir | `motor.cevapla` |

RAG: katalogdaki `aciklama` alanları, çözümleyicisi olmayan kurallar için "açıklama" şablonuyla kullanılır.
Ayrı bir vektör arama katmanı faz 1'de gerekmez; katalog küçüktür ve kural kodu zaten anahtar.

## 9. Değerlendirme

Değerlendirme seti smile2025'ten çıkar: (çağrı metni, ekibin verdiği cevap) çiftleri. Her çağrı için
başvuru JSON'u da gerekir; en sık kurallardan örneklenmiş 100–200 çağrılık bir alt küme yeterli.

| Ölçüt | Tanım | Faz 1 hedefi |
|---|---|---|
| Kapsama | motorun bir kurala/konuya eşleyebildiği çağrı oranı | en az %70 |
| Olgu doğruluğu | taslaktaki tarih, sayı, kural kodu ekibin cevabıyla aynı | en az %95 (kapsanan çağrılarda) |
| Düzeltmesiz gönderim | pilotta ekibin hiç değiştirmeden gönderdiği taslak oranı | en az %60 (workshop'ta netleşir) |
| İnceleme oranı | "inceleme gerekli" düşen çağrı oranı ve nedenleri | izlenir; nedenler kataloğu büyütür |
| Süre | çağrı başına ekibin harcadığı süre, önce/sonra | pilotta ölçülür |

Yöntem: `motor.py` toplu modda çalıştırılır, taslaklar ekibin cevabıyla yan yana tabloya yazılır; olgu
doğruluğu kodla (token karşılaştırması), metin kalitesi ekipten iki kişinin 1–3 puanıyla ölçülür.

## 10. Gizlilik ve güvenlik

- Smile dökümü, gerçek başvuru JSON'ları, Excel ve üretilen raporlar `veri/` altında kalır; `.gitignore`
  bunları ve tüm `.xlsx/.csv` dosyalarını dışarıda tutar. Repoya yalnızca sentetik örnek girer.
- LLM kurum içi (OpenShift); dışarıya veri çıkmaz. TLS doğrulaması BriefMind'daki `ca_bundle` yöntemiyle.
- Cevapta kişisel veri yalnızca müşteri numarası; şablonlara ad/unvan alanı konmaz.
- KVKK: tarihsel çağrıların geliştirme amaçlı kullanımı için gerekli onay workshop öncesi netleşmeli.

## 11. Aşamalar ve takvim (varsayım: başlangıç Ekim 2026 ortası)

| Aşama | Süre | Çıktı | Kim |
|---|---|---|---|
| 0. Veri analizi | 1 hafta | `analiz_smile.py` raporu: aylık hacim, sınıf dağılımı, kural sıklığı, kapsama; MVP kural listesi | teknik ekip |
| 1. Workshop | aynı hafta | bölüm 13 gündemi; en sık kuralların katalog tanımı ve onaylı şablonları; JSON erişim kararı | strateji + teknik |
| 2. MVP | 3 hafta | kataloğa N kural; gerçek JSON yapısına göre yollar; Excel'den matris; smile2025 alt kümesinde değerlendirme | teknik; şablon onayı strateji |
| 3. Pilot | 3 hafta | gelen çağrılara taslak; ekip onaylar/düzeltir; düzeltmeler ve ölçütler kaydedilir | strateji ekibi + teknik |
| 4. Sunum | 1 hafta | 2027 planlama dönemine: prototip demosu, ölçüm sonuçları, faz 2 kapsamı (Smile entegrasyonu, girdi soruları) | proje ekibi |

Toplam yaklaşık 9 hafta; dosyaların ve JSON erişiminin ilk iki haftada gelmesine bağlı.

## 12. Varsayımlar (dosyalar gelince doğrulanacak)

| # | Varsayım | Nerede kullanıldı | Doğrulama |
|---|---|---|---|
| V1 | Başvuru JSON'unda `CIF`, `BasvuruTarihi`, `Indata.CRBNotice`, `Outdata.*` yolları | katalog, `config`, örnekler | tam JSON; yollar katalogda/ayarda değişir, kod değişmez |
| V2 | CRBNotice kaydında `Status`, `NoticeDate`, `BankCode` alanları; status 1/2'nin anlamı | H221 çözümü | tam JSON + Boran |
| V3 | "Farklı banka" bilgisi kayıt banka kodu ≠ bizim kod ile belirlenir | H221 şablon dalı | Boran |
| V4 | Çalışan kurallar `Outdata.HRules[{Code, Hit, AuthLevel}]` listesinde | G4, yetki açıklaması | tam JSON |
| V5 | Nihai yetki = max(matris, H kuralları) | `matris.py` | Boran / birden çok gerçek örnek |
| V6 | Matrise giren ağırlıklı limit nakit teminat dahil olan (`WEIGHTEDLIMWCASHCOLL`) | katalog YETKI | Excel |
| V7 | Tek matris; segment/yola göre ayrı tablo yok | `yetki_matrisi.json` | Excel |
| V8 | Müşteri numarası 8 haneli; çağrı metninde geçiyor | sınıflandırıcı | smile2025 |
| V9 | Smile dökümünde tarih, konu/açıklama ve cevap sütunları var | `analiz_smile.py` | smile2025 |
| V10 | Pencere "son 5 yıl" başvuru tarihine göre | H221 | Boran |

## 13. Workshop gündemi (çözüm merkezi örnekleriyle)

1. smile2025 analiz raporu: hacim, sınıflar, en sık kurallar, kapsama hedefi; MVP kural listesi kararı.
2. Başvuru JSON'una erişim: nerede, nasıl sorgulanır, kim yetki verir (en kritik karar).
3. Gerçek bir başvurunun tam JSON'u üzerinde H221 ve yetki seviyesi örneğinin birlikte okunması; V1–V7.
4. MVP kurallarının her biri için üçlü tanım (veri yolu, koşul, şablon) ve şablon onayı; dallanmalar.
5. Girdi ve hata sınıfı çağrılar için yönlendirme metinleri.
6. Onay akışı: taslağı kim görür, nasıl onaylar, düzeltme nasıl kaydedilir; başarı eşiği.
7. Kural değişiklik süreci: kim, nasıl bildirir; kataloğa sürüm satırı ekleme sorumlusu.
8. KVKK ve veri kullanımı onayı.

## 14. Açık sorular

| Soru | Kim | Neden önemli |
|---|---|---|
| Başvuru JSON'ları nerede, CIF + tarihle sorgulanabilir mi, saklama süresi ne? | Boran / BT | pilotun otomatik çalışması buna bağlı |
| H kurallarının kod, açıklama ve mantığını içeren bir katalog var mı, kaç kural var? | Boran | katalog yazım yükü |
| Smile: çağrı alanları, başvuru numarası, cevap metni, API ya da dışa aktarım? | Elif Bala / Smile sahibi | sınıflandırma ve faz 2 entegrasyonu |
| Excel: hangi ağırlıklı limit, segment/yol ayrımı, eşiklerin yorumu (≤ / <) | Boran | matris doğruluğu |
| CRBNotice status 1 ve 2 ne demek; "farklı banka" hangi alandan? | Boran | H221 dalları |
| Kurallar ne sıklıkla değişiyor, değişiklik kaydı tutuluyor mu? | strateji ekibi | sürümleme |
| Pilotta taslağı kim onaylar; düzeltmesiz gönderim hedefi ne? | Elif Bala | başarı ölçütü |
| Tarihsel çağrıların geliştirmede kullanımı için KVKK onayı gerekiyor mu? | hukuk / uyum | veri kullanımı |

## 15. Riskler

| Risk | Etki | Önlem |
|---|---|---|
| JSON erişimi gecikir | pilot elle JSON ile çalışır, hacim düşer | prototip dosya ile çalışır; erişim workshop'ta ilk madde |
| Katalog yazımı strateji ekibinin zamanını alır | kapsama düşük kalır | en sık N kuralla başla; smile2025'teki eski cevaplar şablon taslağı olarak kullanılır |
| Tarihsel cevaplar tutarsız | değerlendirme zor | workshop'ta kural başına tek onaylı şablon |
| Çağrıda müşteri/başvuru numarası yok | otomatik eşleme düşer | Smile'da zorunlu alan önerisi; LLM ile ad → numara eşlemesi yapılmaz |
| LLM Türkçeden kayar ya da uydurur | yanlış cevap riski | G1, G2, G6; tek kuralda LLM yok |
| Kural değişikliği bildirilmez | eski kurala göre cevap | sürüm satırı + G5 tutarsızlık notu |
| Yanlış cevap şubeye gider | güven kaybı | faz 1'de insan onayı zorunlu |

## 16. Prototip iskeleti: ne var, ne yok

Var: katalog biçimi ve doğrulaması; H221 ve yetki seviyesi çözümleyicileri; sentetik matris; sınıflandırıcı
(regex + anahtar kelime + LLM zenginleştirme); taslak kurma ve guardrail denetimi; dosya tabanlı başvuru
kaynağı; komut satırı; Smile analiz scripti; 62 test; CI (Linux Python 3.11 + Windows Python 3.9).

Yok (sırayla gelecek): gerçek JSON yolları (V1–V4), Excel'den matris, MVP kural listesi ve şablon onayları,
toplu değerlendirme scripti (taslak ile ekip cevabını yan yana), onay listesi arayüzü, Smile entegrasyonu,
girdi sınıfı veri kaynağı açıklamaları.
