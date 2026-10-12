"""
not_araclari.py — notun ve kayitlarin uzerinde calisan yardimcilar (arayuzden bagimsiz):
toplantilar arasi arama, transkriptte bir ana gitme, nottan aksiyon ayiklama, kisiye ozel e-posta,
Word'e aktarma, Jira/Planner CSV'si, konusma paylari, katilimci bilgilendirme metni.

Sahip durumu (MIMARI_v3 §6): nottaki Sorumlu hucresi 'Ad' -> kesin, 'Ad (?)' -> belirsiz, 'belirsiz'/'-' -> yok.
E-posta taslagi ve Jira 'Assignee' yalniz kesin sahiplere gider; digerleri 'Sahibi belirsiz' blogunda kalir.
Konusmaci adi her yuzeyde metin.gorunen_ad ile yazilir.
Birebir (1:1) sablonunda kisiye ozel e-posta ve Jira taslagi varsayilan kapali (taslak_izni; ayar 'birebir_taslak').
"""
import csv
import json
import os
import re

import motor as motor_mod
from kelime import normalize
from metin import gorunen_ad, kesin_ad_mi

BILGILENDIRME = ("Bilgi: Bu toplantıda BriefMind ile not alınmaktadır. Ses kaydedilmez; konuşmalar kurum içi "
                 "sistemlerde metne çevrilip özetlenir ve not katılımcılarla paylaşılır. Not alınmasını "
                 "istemeyen katılımcı lütfen belirtsin.")
SATIR_TAVAN_SN = 15             # konusma payi: bir satirin sayilabilecegi en uzun sure (araya giren sessizlik)
JIRA_BASLIKLAR = ["Summary", "Assignee", "Due Date", "Description", "Issue Type"]


BIREBIR_TASLAK_KAPALI = ("Birebir görüşmede kişiye özel e-posta ve Jira taslağı varsayılan olarak kapalı (konuşulanlar "
                         "kişisel olabilir). Açmak için: Ayarlar → 'Birebir toplantıda da taslak'.")


def taslak_izni(sablon, ayar=None):
    """Kisiye ozel e-posta / Jira CSV taslagi bu toplantida acik mi? Birebir sablonda varsayilan KAPALI; ayar
    'birebir_taslak': true ile acilir. Dondurur (izin, kisa aciklama)."""
    if sablon == "birebir" and not bool((ayar or {}).get("birebir_taslak", False)):
        return False, BIREBIR_TASLAK_KAPALI
    return True, ""


def sablon_canli_satirlari(ozet):
    """Canli panel: dogrulanmis parca ozetindeki sablon maddeleri -> [(etiket, metin)] ('Fikir', 'Engel' ...)."""
    import llm
    etiket = {"ilerleme": "İlerleme", "engeller": "Engel", "gelecek_donem": "Gelecek dönem", "secenekler": "Seçenek",
              "geri_bildirimler": "Geri bildirim", "anlasilanlar": "Anlaşılan", "fikirler": "Fikir"}
    satirlar = []
    for alan, tur in llm.ALAN_TURU.items():
        for x in (ozet or {}).get(alan) or []:
            if tur == "secenek":
                metin = x.get("secenek", "") + {True: " — seçildi", "belirsiz": " — seçildi (?)"}.get(x.get("secildi"), "")
            elif tur == "fikir":
                metin = f"{x.get('fikir', '')} ({x.get('tema') or 'Diğer'})"
            else:
                metin = str(x)
            satirlar.append((etiket.get(alan, alan), metin))
    return satirlar


def _sn(ts):
    return motor_mod._sn(ts)


def _katla(metin):
    """Arama icin: buyuk/kucuk harf ve Turkce karakter farkini yok say ('ILETIN' ~ 'iletin', 'sube' ~ 'şube')."""
    m = normalize(metin or "")
    for a, b in zip("çğıöşüâî", "cgiosuai"):
        m = m.replace(a, b)
    return m


def _meta(klasor):
    try:
        with open(os.path.join(klasor, "meta.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def transkript_satirlari(klasor):
    """Toplantinin satirlari: once parca dosyalari (duzeltilmis metin), yoksa altyazi.jsonl."""
    satirlar = []
    pk = os.path.join(klasor, "parcalar")
    if os.path.isdir(pk):
        for ad in sorted(os.listdir(pk)):
            try:
                with open(os.path.join(pk, ad), encoding="utf-8") as f:
                    satirlar += json.load(f).get("satirlar") or []
            except Exception:
                continue
    if not satirlar:
        satirlar = motor_mod.altyazi_kayitlari(klasor)  # guncelleme kayitlari (buyuyen satir) birlestirilir
    return satirlar


def ana_git(klasor, ts, pencere_sn=90):
    """ts cevresindeki (±pencere) satirlar: [(ts, konusmaci, metin, hedef_mi)]."""
    hedef = _sn(ts)
    satirlar = [s for s in transkript_satirlari(klasor) if abs(_sn(s.get("ts")) - hedef) <= pencere_sn]
    if not satirlar:
        return []
    en_yakin = min(satirlar, key=lambda s: abs(_sn(s.get("ts")) - hedef))
    return [(s.get("ts"), gorunen_ad(s), s.get("text", ""), s is en_yakin) for s in satirlar]


def ara(sorgu, kok=None, en_fazla=200):
    """Tum toplantilarin transkript ve notlarinda arar (buyuk/kucuk harf ve Turkce karakter duyarsiz).
    Sorgudaki TUM kelimeler ayni satirda gecmeli. Dondurur: [{klasor, tarih, baslik, tur, ts, kim, metin}]."""
    kok = kok or motor_mod.KOK
    kelimeler = _katla(sorgu).split()
    if not kelimeler or not os.path.isdir(kok):
        return []
    sonuc = []
    for ad in sorted(os.listdir(kok), reverse=True):
        k = os.path.join(kok, ad)
        meta = _meta(k)
        if not meta:
            continue
        ortak = {"klasor": k, "tarih": meta.get("tarih", ""), "baslik": meta.get("baslik", ad)}
        not_yol = os.path.join(k, "not.md")
        if os.path.exists(not_yol):
            with open(not_yol, encoding="utf-8") as f:
                for satir in f:
                    if all(w in _katla(satir) for w in kelimeler):
                        ts = re.search(r"⏱(\d{1,2}:\d{2}:\d{2})", satir)
                        sonuc.append({**ortak, "tur": "not", "ts": ts.group(1) if ts else "", "kim": "",
                                      "metin": satir.strip().strip("|-# ")})
        for s in transkript_satirlari(k):
            if all(w in _katla(s.get("text", "")) for w in kelimeler):
                sonuc.append({**ortak, "tur": "transkript", "ts": s.get("ts", ""), "kim": gorunen_ad(s),
                              "metin": s.get("text", "")})
        if len(sonuc) >= en_fazla:
            break
    return sonuc[:en_fazla]


_SAHIPSIZ = ("", "-", "belirsiz", "?")


def sahip_coz(sorumlu, madde=""):
    """Nottaki Sorumlu hucresi -> {'ad', 'durum': kesin | belirsiz | yok}. '(?)' (sorumlu ya da madde hucresinde)
    sahibi belirsiz yapar; hucre bos/'belirsiz'/'-' ise 'yok'. 'ad' (?) isaretsiz, virgulle ayrilmis adlar."""
    ham = (sorumlu or "").strip()
    isaretli = "(?)" in ham or "(?)" in (madde or "")
    adlar = [x.strip() for x in re.split(r",|/| ve ", re.sub(r"\(\?\)", " ", ham))
             if x.strip() and x.strip().lower() not in _SAHIPSIZ]
    if not adlar:
        return {"ad": "", "durum": "yok"}
    return {"ad": ", ".join(adlar), "durum": "belirsiz" if isaretli else "kesin"}


def aksiyonlari_ayikla(not_md):
    """Nottaki '## Aksiyonlar' tablosu -> [{madde, sorumlu, tarih, kaynak, sahip}] (duzenlenmis notta da calisir).
    sahip: sahip_coz(sorumlu, madde) -> {'ad', 'durum'}."""
    aksiyonlar, bolum = [], ""
    for satir in not_md.splitlines():
        s = satir.strip()
        if s.startswith("#"):
            bolum = s.lower()
            continue
        if "aksiyon" not in bolum or not s.startswith("|"):
            continue
        h = [x.strip() for x in s.strip("|").split("|")]
        if len(h) < 4 or h[0] in ("#", "") or set(h[0]) <= set("-: "):
            continue
        aksiyonlar.append({"madde": h[1], "sorumlu": h[2], "tarih": h[3], "kaynak": h[4] if len(h) > 4 else "",
                           "sahip": sahip_coz(h[2], h[1])})
    return aksiyonlar


def _sahip(a):
    s = a.get("sahip")
    if isinstance(s, dict) and s.get("durum") in ("kesin", "belirsiz", "yok"):
        return s
    return sahip_coz(a.get("sorumlu"), a.get("madde"))


def sahibi_belirsiz_aksiyonlar(not_md):
    """Sahibi kesin olmayan (belirsiz / yok) aksiyonlar: e-postaya ve Jira atamasina girmez, 'Sahibi belirsiz' blogunda
    kalir. Dondurur: aksiyonlar_ayikla kayitlari."""
    return [a for a in aksiyonlari_ayikla(not_md) if a["madde"].strip(" -") and _sahip(a)["durum"] != "kesin"]


def sahibi_belirsiz_blogu(not_md):
    """'Sahibi belirsiz' blogu (metin): sahibi kesin olmayan aksiyonlar, varsa aday adla. Yoksa ''."""
    satirlar = []
    for a in sahibi_belirsiz_aksiyonlar(not_md):
        ad = _sahip(a)["ad"]
        satirlar.append(f"- {a['madde']}" + (f" (aday: {ad})" if ad else ""))
    return ("Sahibi belirsiz:\n" + "\n".join(satirlar)) if satirlar else ""


def _bolum_sil(satirlar, baslik):
    """satirlar icinden '## baslik' bolumunu (sonraki '## ' basligina kadar, onundeki bos satir dahil) atar."""
    try:
        i = next(k for k, x in enumerate(satirlar) if x.strip() == baslik)
    except StopIteration:
        return satirlar
    j = next((k for k in range(i + 1, len(satirlar)) if satirlar[k].startswith("## ")), len(satirlar))
    while j > i + 1 and not satirlar[j - 1].strip():
        j -= 1                                          # bolumden sonraki bos satir (sonraki basligin ayiraci) kalir
    bas = i - 1 if i and not satirlar[i - 1].strip() else i
    return satirlar[:bas] + satirlar[j:]


def aksiyon_sirasini_yenile(not_md):
    """Aksiyon tablosunun '#' sutununu 1..n yeniden numaralar (dayanak kontrolunun attigi satirlar bosluk birakmasin)."""
    satirlar, bolum, no = not_md.split("\n"), "", 0
    for k, satir in enumerate(satirlar):
        s = satir.strip()
        if s.startswith("#"):
            bolum = s.lower()
            continue
        if "aksiyon" not in bolum or not s.startswith("|"):
            continue
        m = re.match(r"^(\s*\|\s*)(\d+)(\s*\|.*)$", satir)
        if m:
            no += 1
            satirlar[k] = f"{m.group(1)}{no}{m.group(3)}"
    return "\n".join(satirlar)


def kisiye_gore_yenile(not_md):
    """'Kişiye göre iş listesi' bolumunu SON aksiyon tablosundan (dayanak kontrolu ve sahip dogrulamasindan sonra)
    yeniden kurar: tablodan atilan madde listede de yoktur; sahibi kesin olmayan is ('Ad (?)', madde '(?)') kisinin
    isi sayilmaz, 'belirsiz' altinda '(aday: Ad)' ile yazilir (sahip_coz: e-posta / Jira ile ayni kural). Tabloda en az
    4 aksiyon ve 2 grup yoksa bolum kalkar."""
    import llm
    girdi = []
    for a in aksiyonlari_ayikla(not_md):
        sahip = _sahip(a)
        if sahip["durum"] == "kesin":
            sorumlu = sahip["ad"]
        else:
            sorumlu = f"{sahip['ad']} (?)" if sahip["ad"] else "belirsiz"
        girdi.append({"madde": a["madde"], "sorumlu": sorumlu})
    satirlar = _bolum_sil(not_md.split("\n"), llm.KISIYE_BASLIK)
    yeni = llm.kisiye_gore_satirlari(girdi)
    if yeni:
        try:
            i = next(k for k, x in enumerate(satirlar) if x.strip().lower() == "## aksiyonlar")
        except StopIteration:
            return "\n".join(satirlar)
        j = i + 1
        while j < len(satirlar) and not satirlar[j].strip():
            j += 1
        if j >= len(satirlar) or not satirlar[j].strip().startswith("|"):
            return "\n".join(satirlar)                  # tablo yok
        while j < len(satirlar) and satirlar[j].strip().startswith("|"):
            j += 1
        satirlar[j:j] = ["", llm.KISIYE_BASLIK] + yeni
    return "\n".join(satirlar)


def kisiye_ozel_epostalar(not_md, baslik, tarih):
    """Sahibi KESIN her kisi icin (ad, konu, govde): yalnizca o kisinin isleri + notun ozeti. Sahibi belirsiz
    ('Ad (?)', 'belirsiz') aksiyonlar hicbir e-postaya girmez (bkz. sahibi_belirsiz_blogu)."""
    import llm
    aks = aksiyonlari_ayikla(not_md)
    ozet = ""
    m = re.search(r"## Özet\s*\n(.*?)(\n## |\Z)", not_md, flags=re.S)
    if m:
        ozet = m.group(1).strip()
    kesin = [dict(a, sorumlu=a["sahip"]["ad"]) for a in aks if a["sahip"]["durum"] == "kesin"]
    tarihler = {a["madde"]: a["tarih"] for a in aks}
    epostalar = []
    for ad, isler in llm.kisi_bazli(kesin):
        if ad in ("belirsiz", "-", "?"):
            continue
        govde = [f"Merhaba {ad.split()[0]},", "", f"{tarih} tarihli \"{baslik}\" toplantısında sana düşen işler:", ""]
        govde += [f"- {x}" + (f" (tarih: {tarihler[x]})" if tarihler.get(x) not in (None, "", "-") else "")
                  for x in isler]
        govde += ["", "Toplantı özeti:", ozet or "-", "", "Tam not ekte ya da ortak klasörde."]
        epostalar.append((ad, f"Toplantı aksiyonların: {baslik} ({tarih})", "\n".join(govde)))
    return epostalar


def word_kaydet(not_md, yol):
    """Notu .docx olarak kaydeder (python-docx). Kurulu degilse ImportError."""
    import docx
    belge = docx.Document()
    tablo, sutun = None, 0
    for ham in not_md.splitlines():
        s = ham.strip()
        if s.startswith("|"):
            h = [x.strip() for x in s.strip("|").split("|")]
            if all(set(x) <= set("-: ") for x in h):
                continue
            if tablo is None:
                sutun = len(h)
                tablo = belge.add_table(rows=0, cols=sutun)
                tablo.style = "Table Grid"
            hucreler = tablo.add_row().cells
            for i, x in enumerate(h[:sutun]):
                hucreler[i].text = x
            continue
        tablo = None
        if s.startswith("# "):
            belge.add_heading(s[2:], level=0)
        elif s.startswith("## "):
            belge.add_heading(s[3:], level=1)
        elif s.startswith(("- ", "* ")):
            belge.add_paragraph(s[2:], style="List Bullet")
        elif s:
            belge.add_paragraph(s)
    belge.save(yol)
    return yol


def _sure_dagilimi(satirlar):
    """Satir sureleri -> ({kesin tek konusmaci: sn}, belirsiz sn). Satir suresi = sonraki satira kadar gecen sure
    (en fazla SATIR_TAVAN_SN: arada sessizlik/mola olabilir); son satirin ya da ayni saniyedeki satirin sonrasi
    yoktur, kelime sayisindan tahmin edilir (~2,5 kelime/sn). Kesin tek konusmaci: metin.kesin_ad_mi (spk_guven >= 0.7)
    ve eszamanli (cakisma) degil; '?', 'Ad?', 'A → B', 'A / B', oda/genel etiketleri belirsiz sayilir."""
    satirlar = [s for s in satirlar if (s.get("text") or "").strip()]
    sure, belirsiz = {}, 0.0
    for i, s in enumerate(satirlar):
        fark = _sn(satirlar[i + 1].get("ts")) - _sn(s.get("ts")) if i + 1 < len(satirlar) else 0
        if fark <= 0:
            fark = len(s["text"].split()) / 2.5
        fark = min(SATIR_TAVAN_SN, fark)
        if kesin_ad_mi(s) and not s.get("cakisma"):
            kim = str(s.get("speaker") or "").strip()
            sure[kim] = sure.get(kim, 0.0) + fark
        else:
            belirsiz += fark
    return sure, belirsiz


def konusma_paylari(satirlar):
    """N7: konusmaci basina konusma suresi ve payi -> [(konusmaci, saniye, yuzde)], cok konusandan aza.
    YALNIZ kesin tek konusmacilar sayilir; '?', 'Ad?', 'A → B', 'A / B' yok sayilir (yuzde kesin konusmacilarin
    toplamina gore). Belirsiz payi ayri: konusma_belirsiz_payi. Nota yazilmaz (meta.json -> Gecmis detayi)."""
    sure, _ = _sure_dagilimi(satirlar)
    toplam = sum(sure.values())
    if not toplam:
        return []
    return sorted(((k, round(v, 1), round(100 * v / toplam)) for k, v in sure.items()),
                  key=lambda x: (-x[1], x[0]))


def konusma_belirsiz_payi(satirlar):
    """Konusmacisi kesin olmayan (bilinmeyen, 'Ad?', devir, eszamanli) konusmanin TUM konusma icindeki yuzdesi (0-100)."""
    sure, belirsiz = _sure_dagilimi(satirlar)
    toplam = sum(sure.values()) + belirsiz
    return round(100 * belirsiz / toplam) if toplam else 0


def konusma_payi_metni(paylar, belirsiz=0):
    """[(ad, sn, yuzde)] -> 'Konuşma payı: Selin %38, Emre %27 · konuşmacısı belirsiz %12' (Gecmis detay paneli)."""
    if not paylar and not belirsiz:
        return ""
    parcalar = [f"{p[0]} %{p[2]}" for p in paylar or []]
    if belirsiz:
        parcalar.append(f"konuşmacısı belirsiz %{int(belirsiz)} (tümüne göre)")
    return "Konuşma payı: " + ", ".join(parcalar)


def jira_csv(aksiyonlar, yol, proje=""):
    """N9: aksiyonlari Jira 'Import from CSV' (Planner'a da aktarilabilen) CSV'ye yazar: UTF-8 BOM'lu (Excel
    Turkce karakterleri bozmasin), basliklar Summary, Assignee, Due Date, Description, Issue Type.
    proje: toplantinin adi ve tarihi ('Strateji (2026-09-23)'), Description'a yazilir. Tarih yalnizca
    YYYY-MM-DD ise ve '(?)' tasimiyorsa Due Date'e girer (belirsiz tarih Description'a yazilir). Assignee YALNIZ sahip durumu kesin ise dolar ('Ad (?)' ve 'belirsiz' bos
    kalir, Description'a 'sahibi belirsiz' yazilir); birden cok sorumlu varsa ilki Assignee, hepsi Description'da.
    Dondurur: yazilan satir sayisi. (REST entegrasyonu yok.)"""
    satir_sayisi = 0
    with open(yol, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(JIRA_BASLIKLAR)
        for a in aksiyonlar:
            madde = (a.get("madde") or "").strip()
            if not madde or madde == "-":
                continue
            sahip = _sahip(a)
            adlar = [x.strip() for x in sahip["ad"].split(",") if x.strip()]
            ham_tarih = (a.get("tarih") or "").strip()
            # '(?)' isaretli (belirsiz) tarih Due Date olmaz: yanlis gun takvime girmesin; Description'da kalir
            tarih = None if "(?)" in ham_tarih else re.match(r"\d{4}-\d{2}-\d{2}", ham_tarih)
            if "(?)" in ham_tarih:
                aciklama_tarih = f" · tarih belirsiz: {ham_tarih}"
            else:
                aciklama_tarih = ""
            aciklama = "BriefMind — " + (proje or "toplantı")
            ts = re.search(r"\d{1,2}:\d{2}:\d{2}", a.get("kaynak") or "")
            if ts:
                aciklama += f" · kaynak {ts.group(0)}"
            aciklama += aciklama_tarih
            if sahip["durum"] != "kesin":
                aciklama += " · sahibi belirsiz" + (f" (aday: {sahip['ad']})" if sahip["ad"] else "")
            elif len(adlar) > 1:
                aciklama += " · sorumlular: " + ", ".join(adlar)
            atanan = adlar[0] if adlar and sahip["durum"] == "kesin" else ""
            w.writerow([madde, atanan, tarih.group(0) if tarih else "", aciklama, "Task"])
            satir_sayisi += 1
    return satir_sayisi
