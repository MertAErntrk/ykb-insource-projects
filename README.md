# ykb-insource-projects

YKB'de insource olarak geliştirilen projelerin reposudur.

## Projeler

| Klasör | Proje | Açıklama |
|---|---|---|
| [`briefmind/`](briefmind/) | BriefMind | Toplantı notu AI uygulaması |
| [`mrm-ai/`](mrm-ai/) | MRM AI | MRM AI projesi |
| [`laya/`](laya/) | Laya | Laya projesi |
| [`strateji-cevap/`](strateji-cevap/) | Strateji Cevap Motoru | Strateji çıktıları hakkındaki Smile çağrılarına otomatik cevap taslağı üreten motor |
| [`risk-feature-workbench/`](risk-feature-workbench/) | Risk Feature Workbench | Excel değişkenlerinden gerçek Oracle şema/tablo/kolon adaylarını bulan yerel web uygulaması; [iş bilgisayarı rehberi](risk-feature-workbench/docs/IS_BILGISAYARINDA_BASLANGIC.md) |
| [`api-catalog/`](api-catalog/) | API Atlas | Ekran URL’si, HAR ve OpenAPI üzerinden CLI ile API kataloğu; Qwen soru-cevap endpoint’iyle Türkçe açıklamalar |
| [`table_transporter/`](table_transporter/) | VeriKöprü | Oracle DB'ler arasında kolon bazlı anonimleştirerek tablo taşıyan web uygulaması (LDAP girişi, en çok 5M satır, yalnız CREATE/INSERT, tablo silmez); [dağıtım rehberi](table_transporter/docs/DEPLOY.md) |

Her proje kendi klasöründe durur. Yeni proje eklerken kök dizinde yeni bir klasör açıp bu tabloya bir satır ekle.

## İş bilgisayarında kullanım

İlk seferde bir kere:

```bash
git clone https://github.com/MertAErntrk/ykb-insource-projects.git
```

Sonra güncellemeleri almak için:

```bash
cd ykb-insource-projects
git pull
```

Repo herkese açıktır; clone/pull için normalde GitHub girişi gerekmez. Kurum ağında GitHub erişimi ayrıca kısıtlı olabilir.

## Dikkat

- Şifre, API anahtarı, `.env` dosyaları ve müşteri verisi **commit edilmez**. `.gitignore` bunların çoğunu engeller ama göndermeden önce kontrol et.
- Gizli değerler için her projede `.env.example` gibi değersiz bir örnek dosya tut.
