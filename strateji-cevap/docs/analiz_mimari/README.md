# Proje Analiz ve Mimari Dokümanı — kaynak

`docs/PROJE_ANALIZ_VE_MIMARI.pdf` bu klasördeki parçalardan üretilir.

- `bolumler/*.html`: bölümler (sırayla birleştirilir); `<!--SVG:ad|maxw=..|maxh=..|yatay|baslik=..-->` yer tutucuları diyagram ekler.
- `diyagram/*.mmd`: mermaid diyagramları; `mermaid_render.py` bunları SVG'ye çevirir (yerel `node_modules/mermaid`, Playwright Chromium).
- `stil.css`: baskı stili (A4, Inter yazı tipi, yatay sayfa için `.yatay-sayfa`).
- `derle.py`: parçaları birleştirir, içindekileri üretir → `dokuman.html`.
- `pdf_uret.py`: `dokuman.html` → PDF (üst/alt bilgi, sayfa numarası); `--png <klasör>` ile sayfa önizlemeleri.

```bash
pip install playwright pypdfium2 && python -m playwright install chromium
npm i mermaid@11
python mermaid_render.py diyagram
python derle.py
python pdf_uret.py dokuman.html ../PROJE_ANALIZ_VE_MIMARI.pdf
```

Mermaid metinlerinde `;` kullanılmaz (ayırıcı sayılır). Gerçek müşteri verisi, tutar ya da numara bu belgeye girmez.
