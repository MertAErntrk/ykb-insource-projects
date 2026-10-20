# Strateji Cevap Motoru

Smile'dan Çözüm Merkezi'ne gelen strateji sorularına ("H221 neden çalıştı?", "onay yetki seviyesi neden 8?")
cevap taslağı hazırlar. Sayı, tarih ve kural kodu başvurunun strateji JSON'undan ve ekibin onayladığı
şablonlardan gelir; LLM yalnızca soruyu anlamak ve birden çok kuralı tek metne toplamak için kullanılır.
Taslak kendiliğinden gitmez: ekran listesinden ekipten biri onaylar ya da düzeltir.

Akış ve ayrıntı: `docs/MIMARI.md`, plan ve açık sorular: `docs/UYGULAMA_PLANI.md`.

## Kurulum

```bash
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy config.example.json config.json
```

İş PC'sinde Python 3.9 yeter. Motor ve ekran yalnızca standart kütüphane kullanır; `openai`/`httpx` yalnız
`--llm` için, `pandas`/`openpyxl` Excel ve Smile analizi için, `oracledb` yalnız `kaynak: "db"` için.

## Komutlar

```bash
python motor.py --cagri ornekler/cagrilar/h221.json [--config config.json] [--llm] [--json]
python ekran.py [--config config.json] [--port 8765]     # http://127.0.0.1:8765
python matris_excel.py --excel veri/<yetki-excel>.xlsx --cikti veri/yetki_matrisi.json
python matris_excel.py --excel veri/<yetki-excel>.xlsx --dogrula ANAHTAR=DEGER ...
python excel_dok.py veri/<dosya>.xlsx [--hucreler] [--degersiz]   # yapı dökümü, maskeli
python analiz_smile.py veri/smile2025.xlsx --rapor veri/smile_analiz.md
python json_yapi.py veri/<kayit>.json [--cikti agac.txt]   # JSON anahtar ağacı, değersiz
python -m pytest tests -q
```

## config.json

`config.example.json` kopyalanır. Göreli yollar config dosyasının klasörüne göredir.

| Alan | Anlamı |
|---|---|
| `route`, `model`, `ca_bundle`, `zaman_asimi` | LLM adresi, model, TLS sertifikası, zaman aşımı (yalnız `--llm`) |
| `kaynak` | `dosya` (örnek klasör) ya da `db` (Hızlı Tahsis / Smile+ veritabanı) |
| `basvurular` | `kaynak: dosya` iken başvuru JSON klasörü |
| `cif_yolu`, `tarih_yolu`, `h_kurallari_yolu` | Başvuru JSON'unda müşteri no, tarih, çalışan kural listesi yolları |
| `kendi_banka_kodu` | KKB kayıtlarında bankamızın kodu (H221 farklı banka dalı) |
| `db.hizli_tahsis`, `db.smile_plus` | `dsn`, `kullanici`, `parola`: kullanıcı doldurur |
| `db.tablolar.identity`, `db.tablolar.evaluation` | Hızlı Tahsis kimlik ve değerlendirme tablo adları: kullanıcı doldurur |
| `db.tablolar.io_log`, `pc_log`, `proposals`, `rule_lkp` | Hazır geliyor; farklıysa değiştirilir |
| `db.sutunlar.cif`, `basvuru_no`, `tarih` | Sorgu sütunları: kullanıcı doldurur |
| `db.sutunlar.indata`, `outdata`, `action_code` | JSON ve aksiyon sütunları |
| `matris_dosya` | Boşsa sentetik `katalog/yetki_matrisi.json`; iş PC'sinde `veri/yetki_matrisi.json` |
| `varsayilan_segment`, `unvan_seviye` | JSON'da segment yoksa kullanılacak segment; unvan - seviye eşlemesi |
| `kayitlar_dosya`, `onaylar_dosya`, `ekran_port` | Çözüm Merkezi kayıtları, onay dosyası, ekran portu |

DB'ye geçmek için: DSN, kullanıcı ve parolayı, `identity`/`evaluation` tablo adlarını ve `cif`,
`basvuru_no`, `tarih` sütun adlarını doldur, `matris_dosya` ver, `"kaynak": "db"` yap.

## Durum modeli

| Durum | Anlamı |
|---|---|
| `otomatik` | Motor onaylı şablonla, tutarsızlık görmeden üretti |
| `onaya_gitti` | Motor inceleme istedi (taslak şablon, veri tutarsızlığı, çalışmamış kural, hata/girdi sınıfı) |
| `duzeltildi` | Ekip metni düzeltti, notla birlikte kaydedildi |
| `gonderildi` | Ekip onayladı |
| `kapsam_disi` | Ekip kapsam dışı işaretledi |

Ekibin kararları `veri/onaylar.json` dosyasına yazılır; ekran yeniden açılınca durumlar korunur.

## Gizlilik

`veri/`, `*.xlsx`, `*.csv` ve `config.json` git dışıdır (`.gitignore`). Gerçek müşteri verisi, Smile dökümü,
yetki Excel'i ve parolalar repoya girmez. `ornekler/` altındaki her şey sentetiktir.

## Sınırlar

- `katalog/yetki_matrisi.json` sentetik; gerçek Excel gelince `matris_excel.py` ile üretilecek.
- `AUTHLEVELFORMATRIX` ile unvan eşlemesi ve başvuru JSON'undaki segment alanı doğrulanmadı.
- Hızlı Tahsis ve Smile+ sütun adları, kayıt alan yapısı kullanıcıdan gelecek.
- Girdi sınıfı sorularda yalnız kural açıklaması verilir; veri kaynağı açıklaması sonraki faz.
