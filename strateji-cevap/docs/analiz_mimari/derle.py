"""bolumler/*.html parçalarını sırayla birleştirir, <!--SVG:ad|maxw=170|maxh=240|yatay--> yer tutucularını
viewBox'a göre ölçeklenmiş inline SVG'ye çevirir, h2 başlıklarından içindekiler üretir → dokuman.html"""
import pathlib, re, html

KOK = pathlib.Path(__file__).parent
SVG_RE = re.compile(r"<!--SVG:([^|>]+)((?:\|[^>]*)?)-->")
sekil_no = [0]

def svg_fig(m):
    ad = m.group(1).strip()
    secenek = dict(x.split("=", 1) if "=" in x else (x, "1") for x in m.group(2).strip("|").split("|") if x)
    maxw = float(secenek.get("maxw", 178)); maxh = float(secenek.get("maxh", 235))
    baslik = secenek.get("baslik", "")
    svg = (KOK / "diyagram" / (ad + ".svg")).read_text(encoding="utf-8")
    vb = re.search(r'viewBox="([^"]+)"', svg).group(1).split()
    w, h = float(vb[2]), float(vb[3])
    olcek = min(maxw / w, maxh / h)
    genislik = w * olcek
    svg = re.sub(r'<svg([^>]*?) width="[^"]*"', r'<svg\1', svg, count=1)
    svg = re.sub(r'<svg([^>]*?) style="[^"]*"', r'<svg\1', svg, count=1)
    svg = svg.replace("<svg", '<svg style="width:%.1fmm;height:auto" ' % genislik, 1)
    sekil_no[0] += 1
    cap = '<figcaption>Şekil %d. %s</figcaption>' % (sekil_no[0], html.escape(baslik)) if baslik else ""
    sinif = ' class="yatay-sayfa"' if "yatay" in secenek else ""
    return '<figure%s>%s%s</figure>' % (sinif, svg, cap)

def derle():
    parcalar = sorted((KOK / "bolumler").glob("*.html"))
    govde = "\n".join(p.read_text(encoding="utf-8") for p in parcalar)
    govde = SVG_RE.sub(svg_fig, govde)
    # içindekiler
    basliklar = re.findall(r'<h2 id="([^"]+)">(.*?)</h2>|<h3 id="([^"]+)">(.*?)</h3>', govde)
    toc = []
    for h2id, h2, h3id, h3 in basliklar:
        if h2id == "icindekiler":
            continue
        if h2id:
            toc.append('<li><a href="#%s">%s</a></li>' % (h2id, re.sub("<[^>]+>", "", h2.replace("</span>", "</span> "))))
        elif h3id:
            toc.append('<li class="alt"><a href="#%s">%s</a></li>' % (h3id, re.sub("<[^>]+>", "", h3)))
    govde = govde.replace("<!--TOC-->", '<div class="toc"><ol>%s</ol></div>' % "".join(toc))
    stil = (KOK / "stil.css").read_text(encoding="utf-8")
    sayfa = ('<!doctype html><html lang="tr"><head><meta charset="utf-8"><title>Strateji Cevap Motoru · Proje Analiz ve Mimari Dokümanı</title>'
             '<style>%s</style></head><body>%s</body></html>') % (stil, govde)
    (KOK / "dokuman.html").write_text(sayfa, encoding="utf-8")
    print("dokuman.html", len(sayfa), "karakter,", sekil_no[0], "şekil")

if __name__ == "__main__":
    derle()
