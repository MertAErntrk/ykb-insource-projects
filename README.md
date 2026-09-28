# ykb-insource-projects

YKB'de insource olarak geliştirilen projelerin reposudur.

## Projeler

| Klasör | Proje | Açıklama |
|---|---|---|
| [`briefmind/`](briefmind/) | BriefMind | Toplantı notu AI uygulaması |
| [`mrm-ai/`](mrm-ai/) | MRM AI | MRM AI projesi |
| [`laya/`](laya/) | Laya | Laya projesi |

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

Repo private olduğu için giriş isterse şifre yerine GitHub Personal Access Token kullan
(GitHub → Settings → Developer settings → Personal access tokens).

## Dikkat

- Şifre, API anahtarı, `.env` dosyaları ve müşteri verisi **commit edilmez**. `.gitignore` bunların çoğunu engeller ama göndermeden önce kontrol et.
- Gizli değerler için her projede `.env.example` gibi değersiz bir örnek dosya tut.
