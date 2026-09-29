# BriefMind

Microsoft Teams toplantılarını **yerel makinede** dinleyip (ses + canlı altyazı), kurum içi
Whisper STT ve LLM servisleriyle yapılandırılmış toplantı notu üreten Windows masaüstü uygulaması.
Ses hiçbir yere kaydedilmez; yalnızca metin diske yazılır. Tüm işleme kurum içi servislerde yapılır.

## Mimari

```
Ses (hoparlör + mikrofon) ─ cümle bazlı VAD ─► Whisper STT (OpenAI uyumlu)   [ne söylendi]
Teams canlı altyazısı (UI Automation) ────────► konuşmacı + zaman           [kim söyledi]
        └─ zaman + metin benzerliğiyle eşleşir; uyum ölçümü (ses doğru mu)
Motor: parçalar (≤3000 token / 12 dk) ─► bölüm özeti (JSON) ─► inceleme (şüpheli terimler)
     ─► birleştirme ─► dayanak kontrolü (transkriptte olmayan madde düşer) ─► Markdown not
     ─► Outlook taslağı (takvimdeki katılımcılara)
```

## Dosyalar

| Dosya | Görev |
|---|---|
| `app.py` | PyQt5 masaüstü uygulaması (Canlı / İnceleme / Not / Sözlük / Geçmiş / Ayarlar) |
| `isler.py` | Arayüzü dondurmayan arka plan işleri (yakalama, not üretimi, yeniden özetleme, soru) |
| `gorunum.py` | Tema (QSS), notun HTML görünümü, ikonlar |
| `ayar.py` | `config.json` okuma/yazma |
| `motor.py` | Parçalama, arka plan özetleme, konuşmacı hizalama, dayanak kontrolü, not üretimi |
| `llm.py` | LLM istemcisi, bölüm/birleştirme promptları, token bütçeleri, notun deterministik temizliği |
| `metin.py` | Metin benzerliği (kelime kökü), Türkçe küçük harf, ad eşleşmesi |
| `ses.py` | Ses yakalama (WASAPI loopback + mikrofon), VAD, STT istemcisi, tekrar/halüsinasyon filtreleri |
| `yakalayici.py` | Teams altyazısını UI Automation ile okuma, toplantı adı, altyazıyı otomatik açma |
| `outlook.py` | Takvimden toplantı eşleştirme, taslak e-posta, oturum kullanıcısı |
| `sozluk.py` | Sözlük: alias uygulama, toplantı serisine göre terim kapsamı |
| `toplanti.py` | Komut satırı arayüzü (başla / bitir / liste) |
| `tools/` | Teşhis ve yardımcı scriptler (STT testi, Teams düğme keşfi, sözlük temizliği) |
| `deploy/` | GPU'suz Whisper servisi için OpenShift manifestleri (CPU, faster-whisper) |

## Kurulum

```powershell
pip install -r requirements.txt
copy config.example.json config.json      # adresleri doldur
copy sozluk.example.json sozluk.json
python app.py
```

`config.json`:

| Alan | Açıklama |
|---|---|
| `route`, `model`, `context` | OpenAI uyumlu LLM adresi (`/v1`), model adı, bağlam penceresi |
| `stt_url`, `stt_model`, `stt_key` | OpenAI uyumlu STT adresi (`/v1/audio/transcriptions`), model, Bearer anahtarı |
| `kaynak` | `ses` (Whisper) / `ikisi` / `altyazi` |
| `ben` | Uygulamayı açan kişi (boşsa Outlook/Windows'tan alınır) |
| `mikrofon_cihaz` | (isteğe bağlı) Mikrofon cihaz numarası ya da adı; yoksa Windows varsayılanı. Doğru cihazı bulmak için: `python tools\ses_teshis.py` |
| `otomatik_not` | (varsayılan `true`) Toplantı bitince İnceleme beklenmez, not doğrudan üretilir. `false`: önce İnceleme sekmesi (eski akış). Ayarlar → "Toplantı bitince incelemeyi atla" |
| `stt_proxy` | (isteğe bağlı, varsayılan `true`) `false` yapılırsa STT'ye Windows proxy'si atlanarak doğrudan bağlanılır. STT'de sık `10053` bağlantı kopması görülürse denenir. |

Exe üretmek için `derle.bat` (PyInstaller, `dist\BriefMind\`).

## Kullanım

1. Teams toplantısına gir (altyazı açık olsun; kapalıysa uygulama Alt+Shift+C ile açmayı dener).
2. **Başlat** — üstteki listeden toplantı seçilir (takvimden, Teams penceresine göre otomatik).
3. Konuşmalar cümle cümle Canlı sekmesine düşer; parçalar arka planda özetlenir.
4. **Bitir** → not doğrudan üretilir (`otomatik_not`); Not sekmesindeki şeritte aşama ve geçen süre görünür
   ("Parça 3/12 özetleniyor", "Özet paragrafı yazılıyor"…) → Outlook taslağı. Şüpheli terimler İnceleme
   sekmesinde bilgi için durur: onaylayıp **Uygula ve notu üret** ile sözlüğe alınır ve not yeniden üretilir.
   `otomatik_not` kapalıysa eski akış: İnceleme → **Uygula ve notu üret** → Not.

Ayarlar → **Kaydet** hemen geçerlidir; LLM adresi/modeli için uygulamayı yeniden başlatmak gerekmez.
Komut satırı: `python toplanti.py basla --inceleme-yok` (ya da `bitir <klasör> --inceleme-yok`) incelemeyi atlar.

Kalıcı altyazı için Teams: … → Ayarlar → Erişilebilirlik → *Toplantılarımda her zaman alt yazıları göster*.

## Notla çalışma

- **Kaynağa git:** Karar ve aksiyonların yanındaki `⏱10:12:03`'e tıklayınca transkript o anda açılır.
- **Soru sor:** Not sekmesindeki kutuya "Test ortamı ne zaman hazır, kim söyledi?" gibi sorular yazılır; cevap yalnızca bu toplantının transkriptinden gelir.
- **Ara:** Geçmiş sekmesinde tüm toplantıların transkript ve notlarında arama yapılır.
- **Yeniden özetle:** Geçmiş sekmesinde bir toplantı seçilip basılır; **tüm** parçalar güncel sözlükle yeniden
  düzeltilip özetlenir ve yeni not üretilir (yarım kalmış kayıtlar için de). Eski not `not.md.yedek-YYYYMMDD-HHMM`
  olarak saklanır. Tek parça için: "Seçili parçayı yeniden özetle".
- **Düzenle / Word / PDF / Kişiye özel e-postalar:** Not sekmesindeki düğmeler.
- **Şablon ve saklama süresi:** Ayarlar → Not şablonu, Transkript saklama süresi.
- **Bilgilendirme:** Canlı sekmesi → "Katılımcıları bilgilendir" metni panoya kopyalar; Teams sohbetine yapıştırın.

## Test

```powershell
pip install pytest
python -m pytest tests
```

Testler LLM sunucusu ya da ses cihazı gerektirmez. GitHub Actions (`.github/workflows/briefmind.yml`, repo
kökünde) `briefmind/` değişince testleri Ubuntu + Python 3.11 ve Windows + Python 3.9 üzerinde çalıştırır; Actions
sekmesinden elle (**Run workflow**) tetiklenince Windows'ta exe derlenir ve `BriefMind` artefaktı olarak indirilir. Uçtan uca analiz ve yol haritası için bkz. [`docs/GELISTIRME_PLANI.md`](docs/GELISTIRME_PLANI.md).

## Gizlilik ve uyum

- Ses diske yazılmaz; STT'ye giden parça bellekte tutulur, yanıt gelince silinir.
- Sözlük, `config.json` ve `toplantilar/` klasörü `.gitignore`'dadır; repoya girmez.
- Kurum içi adresler yer tutucudur; gerçek adresler yalnızca `config.json`'da durur.
