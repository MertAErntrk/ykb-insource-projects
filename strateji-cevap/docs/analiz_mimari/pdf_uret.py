"""HTML → PDF (A4, üst/alt bilgi, sayfa numarası) ve sayfa PNG önizlemeleri.
python pdf_uret.py <giris.html> <cikti.pdf> [--png <klasör>]"""
import sys, os, pathlib
from playwright.sync_api import sync_playwright

CHROME = os.environ.get("CHROME") or None   # boşsa Playwright'in kendi Chromium'u
HEADER = """<div style="font-family:Inter,'DejaVu Sans',sans-serif;font-size:8px;color:#6b7785;width:100%;padding:0 16mm;display:flex;justify-content:space-between">
<span>Strateji Cevap Motoru · Proje Analiz ve Mimari Dokümanı</span><span>Kurumsal ve Ticari Krediler · Ekim 2026</span></div>"""
FOOTER = """<div style="font-family:Inter,'DejaVu Sans',sans-serif;font-size:8px;color:#6b7785;width:100%;padding:0 16mm;display:flex;justify-content:space-between">
<span>Kurum içi kullanım</span><span>Sayfa <span class="pageNumber"></span> / <span class="totalPages"></span></span></div>"""

def uret(html_yolu, pdf_yolu, png_klasor=None):
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME) if CHROME else p.chromium.launch()
        pg = b.new_page()
        pg.goto("file://" + os.path.abspath(html_yolu))
        pg.wait_for_timeout(300)
        pg.emulate_media(media="print")
        pg.pdf(path=pdf_yolu, format="A4", print_background=True, display_header_footer=True,
               header_template=HEADER, footer_template=FOOTER,
               margin={"top": "20mm", "bottom": "18mm", "left": "16mm", "right": "16mm"})
        b.close()
    print("pdf:", pdf_yolu, os.path.getsize(pdf_yolu), "bayt")
    if png_klasor:
        import pypdfium2 as pdfium
        pathlib.Path(png_klasor).mkdir(parents=True, exist_ok=True)
        doc = pdfium.PdfDocument(pdf_yolu)
        print("sayfa:", len(doc))
        for i, sayfa in enumerate(doc):
            img = sayfa.render(scale=1.4).to_pil()
            img.save(os.path.join(png_klasor, "s%02d.png" % (i + 1)))

if __name__ == "__main__":
    a = sys.argv
    uret(a[1], a[2], a[a.index("--png") + 1] if "--png" in a else None)
