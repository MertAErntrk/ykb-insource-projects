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
| `motor.py` | Parçalama, arka plan özetleme, konuşmacı hizalama, dayanak kontrolü, not üretimi |
| `llm.py` | LLM istemcisi, bölüm/birleştirme promptları, token bütçeleri |
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

Exe üretmek için `derle.bat` (PyInstaller, `dist\BriefMind\`).

## Kullanım

1. Teams toplantısına gir (altyazı açık olsun; kapalıysa uygulama Alt+Shift+C ile açmayı dener).
2. **Başlat** — üstteki listeden toplantı seçilir (takvimden, Teams penceresine göre otomatik).
3. Konuşmalar cümle cümle Canlı sekmesine düşer; parçalar arka planda özetlenir.
4. **Bitir** → İnceleme: şüpheli terimleri onayla → **Uygula ve notu üret** → Not → Outlook taslağı.

Kalıcı altyazı için Teams: … → Ayarlar → Erişilebilirlik → *Toplantılarımda her zaman alt yazıları göster*.

## Test

```powershell
pip install pytest
python -m pytest tests
```

Testler LLM sunucusu ya da ses cihazı gerektirmez. Uçtan uca analiz ve yol haritası için bkz. [`docs/GELISTIRME_PLANI.md`](docs/GELISTIRME_PLANI.md).

## Gizlilik ve uyum

- Ses diske yazılmaz; STT'ye giden parça bellekte tutulur, yanıt gelince silinir.
- Sözlük, `config.json` ve `toplantilar/` klasörü `.gitignore`'dadır; repoya girmez.
- Kurum içi adresler yer tutucudur; gerçek adresler yalnızca `config.json`'da durur.
