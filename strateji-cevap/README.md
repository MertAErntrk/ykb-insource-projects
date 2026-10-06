# Strateji Cevap Motoru

Kurumsal ve ticari kredi karar motorunun ("strateji") çıktıları hakkında Smile üzerinden gelen çağrılara
("H221 neden çalıştı?", "onay yetki seviyesi neden 8?") otomatik **cevap taslağı** üreten motor. Olgular
(tarih, tutar, seviye, kural kodu) başvurunun strateji JSON'undan ve strateji ekibinin onayladığı
şablonlardan gelir; LLM yalnızca soruyu anlamak ve birden çok kuralı tek metne toplamak için kullanılır.
Taslak gönderilmez, insan onayına gider. Plan ve açık sorular: [`docs/UYGULAMA_PLANI.md`](docs/UYGULAMA_PLANI.md).

Durum: prototip iskeleti, sentetik veriyle çalışıyor. smile2025 dökümü, yetki seviyeleri Excel'i ve gerçek
başvuru JSON'u gelince katalog yolları ve matris güncellenecek (planda bölüm 12).

## Akış

```
çağrı ─► siniflandirici.py ─► veri.py ─► cozumleyici.py ─► yazici.py ─► taslak + durum ─► insan onayı
          sınıf, kural kodu,   başvuru      katalog kuralı     tek selamlama/kapanış,
          CIF (regex; LLM      JSON'u       → olgular          guardrail (LLM metni taslağa
          isteğe bağlı)        (dosya)      (+ matris.py)      sadık mı), isteğe bağlı LLM
```

| Dosya | Görev |
|---|---|
| `motor.py` | Boru hattı ve komut satırı |
| `siniflandirici.py` | Sınıf (girdi / cikti / hata), kural kodları, müşteri numaraları |
| `katalog.py`, `katalog/kurallar.json` | Kural kataloğu: veri yolu, koşul, şablonlar, onay durumu, geçerlilik tarihi |
| `cozumleyici.py` | Kuralı başvuru JSON'una uygular, olguları çıkarır (`liste_kosul`, `yetki_matrisi`, `sabit`) |
| `matris.py`, `katalog/yetki_matrisi.json` | Limit → onay yetki seviyesi; nihai = max(matris, H kuralları). Matris SENTETİK |
| `yazici.py` | Taslak kurma, guardrail doğrulaması, isteğe bağlı LLM yeniden yazımı |
| `llm.py` | Kurum içi OpenAI uyumlu LLM istemcisi (Qwen, vLLM); BriefMind'dan sadeleştirildi |
| `veri.py` | Başvuru JSON'u kaynağı (klasör); hedefte DB/API uyarlayıcısı |
| `analiz_smile.py` | Smile dökümü analizi: aylık hacim, sınıflar, kural sıklığı, kapsama |
| `ornekler/` | Sentetik başvuru JSON'ları ve çağrılar |
| `tests/` | 62 test, LLM ya da ağ gerektirmez |

## Kurulum

Motor yalnızca standart kütüphane kullanır. LLM için `openai httpx`, analiz için `pandas openpyxl`:

```bash
pip install -r requirements.txt
copy config.example.json config.json      # LLM adresi, bankamızın KKB kodu, başvuru klasörü
```

Python 3.9 uyumlu (iş bilgisayarı). `config.json` repoya girmez.

## Kullanım

```bash
# tek çağrı, sentetik örnekle (config.example.json: kendi_banka_kodu dolu)
python motor.py --cagri ornekler/cagrilar/h221.json --config config.example.json
python motor.py --cagri ornekler/cagrilar/iki_kural.json --config config.example.json --json

# LLM ile (config.json'daki route/model): sınıflandırma ve çok kurallı yeniden yazım
python motor.py --cagri ornekler/cagrilar/iki_kural.json --llm

# Smile dökümü analizi (dosya veri/ altında, repoya girmez)
python analiz_smile.py veri/smile2025.xlsx --rapor veri/smile_analiz.md --csv veri/smile_siniflar.csv
```

Çıktı: sınıf, kurallar, müşteri numarası, durum (`otomatik` / `inceleme_gerekli`), inceleme notları ve
taslak metin. Onaysız (`taslak`) şablon, çalışmamış kural, veri tutarsızlığı ya da bilinmeyen kural her
zaman `inceleme_gerekli` üretir.

## Testler

```bash
python -m pytest tests -q
```

## Gerçek veri

`veri/` klasörü ve tüm `.xlsx/.csv` dosyaları `.gitignore` ile dışarıda; müşteri verisi commit edilmez.
Gerçek başvuru JSON'ları `veri/basvurular/` altına konup `--basvurular veri/basvurular` ile kullanılır.
