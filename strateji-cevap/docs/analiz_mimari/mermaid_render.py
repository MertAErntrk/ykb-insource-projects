"""Mermaid (.mmd) → SVG, yerel mermaid.min.js ile headless Chromium'da. Kullanım: python mermaid_render.py <klasör>"""
import os, sys, json, pathlib, re
from playwright.sync_api import sync_playwright

MERMAID_JS = os.environ.get("MERMAID_JS", "node_modules/mermaid/dist/mermaid.min.js")   # npm i mermaid@11
CHROME = os.environ.get("CHROME") or None   # boşsa Playwright'in kendi Chromium'u

def render(klasor, secili=None):
    js = pathlib.Path(MERMAID_JS).read_text(encoding="utf-8")
    dosyalar = sorted(pathlib.Path(klasor).glob("*.mmd"))
    if secili:
        dosyalar = [d for d in dosyalar if d.name in secili]
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME) if CHROME else p.chromium.launch()
        pg = b.new_page()
        pg.set_content("<html><body><div id='out'></div></body></html>")
        pg.add_script_tag(content=js)
        pg.evaluate("""() => mermaid.initialize({startOnLoad:false, theme:'base', fontFamily:'Inter, DejaVu Sans, sans-serif',
            themeVariables:{primaryColor:'#e8eef7', primaryTextColor:'#1b2a41', primaryBorderColor:'#2f5d9e', lineColor:'#3b4a5a',
                            secondaryColor:'#f3f6fa', tertiaryColor:'#fff8e6', fontSize:'15px', noteBkgColor:'#fff8e6', noteBorderColor:'#d9a300',
                            actorBkg:'#e8eef7', actorBorder:'#2f5d9e', signalColor:'#1b2a41', clusterBkg:'#f7f9fc', clusterBorder:'#b8c4d6',
                            edgeLabelBackground:'#ffffff'},
            flowchart:{htmlLabels:true, curve:'basis', nodeSpacing:30, rankSpacing:40, padding:8},
            sequence:{actorMargin:40, messageMargin:28, mirrorActors:false, wrap:true, width:170},
            gantt:{fontSize:14, barHeight:24, barGap:6, topPadding:44, leftPadding:120, useWidth:1000, titleTopMargin:18}})""")
        for d in dosyalar:
            kod = d.read_text(encoding="utf-8")
            try:
                svg = pg.evaluate("""async ([id, kod]) => { const r = await mermaid.render(id, kod); return r.svg }""", [f"d{abs(hash(d.name))%100000}", kod])
            except Exception as e:
                print("HATA", d.name, str(e)[:300]); continue
            d.with_suffix(".svg").write_text(svg, encoding="utf-8")
            pg2 = b.new_page(viewport={"width": 1400, "height": 900}, device_scale_factor=1.5)
            pg2.set_content("<html><body style='margin:16px;background:white'>" + svg + "</body></html>")
            pg2.locator("svg").first.screenshot(path=str(d.with_suffix(".png")))
            pg2.close()
            vb = re.search(r'viewBox="([^"]+)"', svg); print("ok", d.name, "viewBox", vb.group(1) if vb else "?")
        b.close()

if __name__ == "__main__":
    render(sys.argv[1], sys.argv[2:] or None)
