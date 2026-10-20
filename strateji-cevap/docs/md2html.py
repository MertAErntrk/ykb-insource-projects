import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
md = src.read_text(encoding="utf-8").replace("</script", "<\\/script")
html = """<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Strateji Cevap Motoru: mimari</title>
<style>
body{font-family:Segoe UI,Arial,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;line-height:1.55;color:#222}
h1{font-size:1.7rem}h2{font-size:1.25rem;margin-top:2rem;border-bottom:1px solid #ddd;padding-bottom:.2rem}
table{border-collapse:collapse;width:100%;font-size:.95rem}td,th{border:1px solid #ccc;padding:.35rem .5rem;text-align:left;vertical-align:top}
code{background:#f3f3f3;padding:0 .2rem;border-radius:3px;font-size:.9em}pre.mermaid{background:#fff;text-align:center}
@media print{body{max-width:none;margin:0}h2{page-break-after:avoid}pre.mermaid{page-break-inside:avoid}}
</style></head><body>
<div id="out"></div>
<script type="text/markdown" id="md">
""" + md + """
</script>
<script src="https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"></script>
<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.esm.min.mjs";
const out = document.getElementById("out");
out.innerHTML = marked.parse(document.getElementById("md").textContent);
for (const code of out.querySelectorAll("code.language-mermaid")) {
  const pre = document.createElement("pre"); pre.className = "mermaid"; pre.textContent = code.textContent;
  code.parentElement.replaceWith(pre);
}
mermaid.initialize({ startOnLoad: false, theme: "neutral" });
await mermaid.run({ nodes: out.querySelectorAll("pre.mermaid") });
</script>
</body></html>
"""
dst.write_text(html, encoding="utf-8")
print(dst, dst.stat().st_size)
