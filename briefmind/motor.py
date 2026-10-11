"""
motor.py — toplanti notu boru hatti (UI'dan bagimsiz).

Akis (Faz 1-2, docs/MIMARI_v3.md §3-5):
       ses_olayi(klip)     -> satir v2 hemen olusur (konusmaci 'beklemede…'), altyazi.jsonl + acik parca
       okuma_ekle(okuma)   -> konusmaci.py cizelgesi (Teams altyazi METNI transkripte girmez; yalniz ad + zaman)
       kontrol()           -> hazir konusmaci kararlari satirlara (satir_guncelle), yanki klibi atilir (satir_sil),
                              klip ici devirde yeniden kesme (yeniden_kes_fn; parcalar gelince satir_degistir)
       parca kapaninca arka planda: [duzelt.duzelt: LLM onerir, duzelt.kapi (deterministik) karar verir; metne kor
                              degisiklik YOK] -> ozet (kapanis bekleyen kararlari en fazla KAPANIS_BEKLE_SN bekler)
       bitir()             -> bekleyen kararlar zorlanir, son parcayi kapatir, ozetleri bekler, terim ogrenmesi
                              (terim.TerimHavuzu.ogren), belirsizler() listesini verir (onay akisi YOK)
       notu_uret()         -> birlestirme -> sorumlu dogrulama -> dayanak kontrolu -> not.md (supheli satirlar girmez)
       yeniden_ozetle()    -> (Gecmis) TUM parcalar yeniden duzeltme (yalniz 'raw'i olan satirlar) + ozet

Disk (toplantilar/<tarih>_<slug>/): meta.json, altyazi.jsonl (satir v2 + {'guncelle'} / {'sil'} kayitlari),
       parcalar/parca_NNN.json, not.md, duzelt_log.jsonl (kabul/red, kelime duzeyinde), cizelge.json (konusmaci araliklari, metin yok),
       olaylar.jsonl (konusmaci gunlugu)
olay(tip, veri) geri cagrisi: "satir" (yeni satir, konusmaci beklemede), "satir_guncelle" (satirin tamami + "gorunen":
       konusmaci karari geldi), "satir_sil" ({"id", "neden"}: yanki klibi atildi), "satir_degistir" ({"eski_id",
       "yeni": [satir, ...]}: yeniden kesilen klibin satiri parcalariyla degisti), "altyazi_izi" (canli altyazi
       gorunumu; transkripte girmez), "kapsama" ({"var", "bas"}: konusmaci bilgisi 60 sn'den uzun yok / geri geldi),
       "stt_bosluk", "parca_kapandi", "parca_ozetlendi", "log",
       "adim" ({"metin", "no", "toplam"}: not uretiminin/yeniden ozetlemenin o anki asamasi; arayuz seridi)
Gosterim: konusmaci adi her yuzeyde metin.gorunen_ad ile yazilir ("Ad?", "Bilinmeyen", "A / B (aynı anda)").
"""
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import shutil
import threading
import time  # noqa: F401  (zaman: saat.simdi(); motor.time modulunu testler/replay sorgular)
from collections import OrderedDict

import duzelt as duzelt_mod
import llm
import rag
import saat                    # adima dayanikli zaman kaynagi: tum zaman karsilastirmalari saat.simdi()
import tarih as tarih_mod
import terim as terim_mod
from kelime import normalize
from konusmaci import Konusmaci
from metin import ad_geciyor, gorunen_ad, kesin_ad_mi, tr_kucuk
from stt import hal_esik

KOK = "toplantilar"
SURUM = 2                       # satir veri modeli (MIMARI_v3 §3); surumsuz kayitlar yuklenirken goc eder
PARCA_TOKEN = 0                 # 0: LLM baglam penceresine gore (llm.butce("parca"): 8k 2500, 16k 3000, 32k+ 4000)
PARCA_SURE_SN = 12 * 60
SESSIZLIK_SN = 180
SESSIZLIK_MIN_TOKEN = 500
DUZELT_MIN_TOKEN = 800          # bundan kucuk (genelde son) parcada duzeltme gecisi atlanir
PARALEL = 0                     # 0: llm.butce("paralel") (varsayilan 2; config.json 'paralel' ile artirilabilir)
KAPANIS_BEKLE_SN = 20           # parca kapanisi satirlarinin konusmaci kararini en fazla bu kadar bekler
KESIM_BEKLE_SN = 20             # yeniden kesilen klibin iki parcasi en fazla bu kadar beklenir
GEC_KESIM_TAVAN = 500           # suresi dolmus (parcasi eksik) kesimlerde gec parca icin tutulan en fazla kayit
OZET_YENI_SN = 10               # kapanmis parcaya gec satir girdiyse yeniden ozet bu kadar sessizlikten sonra
KAPSAMA_UYARI_SN = 60           # konusmaci bilgisi (altyazi cizelgesi) bu kadar yoksa arayuz uyarir
CIZELGE_PUANI = 0.6             # stt.halusinasyon_puani cizelge kurali agirligi (testte stt ile esitligi sinanir)
YEDEK_GUVEN = 0.6               # altyazi yedegi satirinin spk_guven'i (gosterim 'Ad?', sahip uretmez)
ESKI_GUVEN = 0.5                # v2 oncesi kayitlarin spk_guven'i (goc)
KLIP_TAVAN = 5000               # klip_no -> satir eslemesinde tutulan en fazla kayit
# Bozuk (tasmis) kaydin algisi ve temizligi (transkript_temizle, tasma_var_mi)
TASMA_ALGI_DK = 120             # dakikada bundan fazla satir
TASMA_ALGI_TEKRAR = 0.30        # ya da (konusmaci, metin) birebir tekrar orani
ONEK_SN = 10 * 60               # temizlikte ayni konusmacinin onek-zinciri (yarim halleri) bu pencerede aranir


def parca_token():
    return PARCA_TOKEN or llm.butce("parca")


def paralel():
    return PARALEL or llm.butce("paralel")


def slug(s):
    s = (s or "").replace("İ", "i").replace("I", "ı").lower()          # Turkce I/İ (lower() 'İ'yi 'i'+nokta yapardi)
    for a, b in zip("çğıöşü", "cgiosu"):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40] or "toplanti"


def _kokler(metin):
    """Turkce ekleri kabaca yok saymak icin kelimelerin ilk 5 harfi (kucuk, sadesiz)."""
    m = metin.lower()
    for a, b in zip("çğıöşüâî", "cgiosuai"):
        m = m.replace(a, b)
    return {k[:5] for k in re.findall(r"[a-z0-9]+", m) if len(k) >= 3}


DAYANAK_SATIR_ESIK = 0.5        # karar/aksiyon/soru maddesi: 1-3 ardisik transkript satirinda kok ortusmesi en az bu kadar
DAYANAK_PENCERE = 3
SARMAL_PAY_SINIRI = 0.30        # dayanak satirlarinin ⟨⟩ payi bunu asarsa madde belirsiz
_MADDE_DURAK = {"ve", "ile", "icin", "olan", "olarak", "yapil", "edil", "kararl", "karar", "aksiy"}
_ETIKET = re.compile(r"^([^:\n]{1,80}):\s+(.*)$", re.S)
_ISARET = "⏱"


def _etiket_belirsiz(etiket):
    """Transkript satirinin konusmaci etiketi kesin bir kisi degil mi ('Ad?', 'Bilinmeyen', 'A → B', 'A / B',
    oda, altyazi yedegi)? Etiketsiz satir (duz metin) bilgi tasimaz: kesin sayilir."""
    e = (etiket or "").strip()
    if not e:
        return False
    return (e.endswith("?") or e.startswith(("Bilinmeyen", "beklemede")) or " → " in e or " / " in e
            or "düşük güven" in e or e.endswith("(oda)") or "(aynı anda)" in e)


_AY_GUN = ("ocak", "subat", "mart", "nisan", "mayis", "haziran", "temmuz", "agustos", "eylul", "ekim", "kasim", "aralik",
           "pazartesi", "sali", "carsamba", "persembe", "cuma", "cumartesi", "pazar")


_BIRIM = {"tl", "usd", "eur", "try", "kdv", "ok"}            # para birimi / kisa yazim: 'lira' diye de soylenir, zorunlu degil


def _sade_kelimeler(metin):
    """Kucuk, sadesiz (ASCII) kelimeler: zorunlu degerlerin (sayi / ay / gun / kod) aranmasi icin."""
    m = tr_kucuk(metin)
    for a, b in zip("çğıöşüâî", "cgiosuai"):
        m = m.replace(a, b)
    return re.findall(r"[a-z0-9]+", m)


def _zorunlu_degerler(madde):
    """Maddenin dayanakta AYNEN gecmesi gereken degerleri: sayilar / tutarlar ('480'), ay ve gun adlari ('mart', 'cuma'),
    kod / kisaltma adlari ('PD', 'S3'). Kok ortusmesi bunlari denetlemez: '480 bin' yalniz ⟨⟩ icindeyse komsu satirlarin
    ortak kokleri maddeyi yine 'dayanakli' gosterirdi."""
    deger = set(re.findall(r"\d+", madde or ""))
    for k in _sade_kelimeler(madde):
        deger.update(ad for ad in _AY_GUN if k.startswith(ad) and (k == ad or len(ad) >= 4))
    for k in re.findall(r"\w+", madde or ""):
        if (len(k) >= 2 and k.isupper() and not k.isdigit()) or (re.search(r"\d", k) and re.search(r"[^\W\d_]", k)):
            deger.update(x for x in _sade_kelimeler(k)[:1] if x not in _BIRIM)
    return deger


def _deger_geciyor(deger, kelimeler, sayilar):
    if deger.isdigit():
        return deger in sayilar
    return any(k == deger or (k.startswith(deger) and not deger.isdigit()) for k in kelimeler)


def _dayanak_dizini(transkript):
    """Transkript -> satir kayitlari: {'etiket', 'kok' (⟨⟩ disindaki kelime kokleri), 'tum' (⟨⟩ dahil kokler), 'n' (kelime),
    'c' (⟨⟩ kelime), 'duz' / 'sayi' (⟨⟩ disindaki sade kelimeler / sayilar), 'ic' / 'ic_sayi' (⟨⟩ icindekiler),
    'cakisma' (satir tumuyle ⟨⟩: 'A / B (ayni anda)')}."""
    dizin = []
    for ham in (transkript or "").split("\n"):
        ham = ham.strip()
        if not ham:
            continue
        m = _ETIKET.match(ham)
        etiket, govde = (m.group(1), m.group(2)) if m else (None, ham)
        sarmal = " ".join(_SARMAL.findall(govde))
        disi = sarmal_sil(govde)
        n, c = len(re.findall(r"\w+", govde)), len(re.findall(r"\w+", sarmal))
        dizin.append({"etiket": etiket, "kok": _kokler(disi), "tum": _kokler(govde), "n": n, "c": c,
                      "duz": _sade_kelimeler(disi), "sayi": set(re.findall(r"\d+", disi)),
                      "ic": _sade_kelimeler(sarmal), "ic_sayi": set(re.findall(r"\d+", sarmal)),
                      "cakisma": "(aynı anda)" in (etiket or "") or (n > 0 and c == n)})
    return dizin


def _dayanak_bul(ak, dizin, esik=DAYANAK_SATIR_ESIK, zorunlu=()):
    """Maddenin kokleri (ak) 1-3 ardisik satirda yeterince karsilaniyor mu? ⟨⟩ icindeki kelimeler dayanak SAYILMAZ.
    zorunlu: maddenin sayi / ay / gun / kod degerleri (_zorunlu_degerler); pencerenin ⟨⟩ DISINDAKI metninde gecmeli.
    Dondurur: None | {'oran', 'pay' (pencerenin ⟨⟩ payi), 'ana' (en cok karsilayan satirin kaydi), 'eksik' (pencerede ⟨⟩
    disinda gecmeyen zorunlu degerler), 'sarmal_kaynak' (maddeyi tek basina en iyi karsilayan satir ⟨⟩ / cakisma satiri)}.
    Secim: once zorunlu degerleri tasiyan, sonra ⟨⟩ payi sinir altindaki, sonra en yuksek oran, sonra en kisa pencere."""
    zorunlu = set(zorunlu)
    en = None
    for i in range(len(dizin)):
        birlesik, toplam, sarmal, kelimeler, sayilar = set(), 0, 0, [], set()
        for j in range(i, min(i + DAYANAK_PENCERE, len(dizin))):
            d = dizin[j]
            birlesik |= d["kok"]
            toplam += d["n"]
            sarmal += d["c"]
            kelimeler += d["duz"]
            sayilar |= d["sayi"]
            oran = len(ak & birlesik) / len(ak)
            if oran < esik:
                continue
            eksik = {z for z in zorunlu if not _deger_geciyor(z, kelimeler, sayilar)}
            pay = sarmal / toplam if toplam else 0.0
            anahtar = (not eksik, pay <= SARMAL_PAY_SINIRI, round(oran, 6), -(j - i))
            if en is None or anahtar > en[0]:
                en = (anahtar, i, j, oran, pay, eksik)
    if en is None:
        return None
    _, i, j, oran, pay, eksik = en
    ana = max(dizin[i:j + 1], key=lambda d: len(ak & d["kok"]))
    # tekil en iyi eslesme (⟨⟩ dahil, kaynak_fn'in gordugu gibi) cakisma / ⟨⟩ satiriysa madde oradan gelmistir
    tekil = max(dizin, key=lambda d: (len(ak & d["tum"]), -d["c"]))
    disi_en = max(len(ak & d["kok"]) for d in dizin)
    sarmal_kaynak = (len(ak & tekil["tum"]) > disi_en
                     and (tekil["cakisma"] or len(ak & (tekil["tum"] - tekil["kok"])) > len(ak & tekil["kok"])))
    return {"oran": oran, "pay": pay, "ana": ana, "eksik": eksik, "sarmal_kaynak": sarmal_kaynak}


def _yalniz_sarmalda(degerler, dizin):
    """Degerlerden biri transkriptte YALNIZ ⟨⟩ icinde mi geciyor (⟨⟩ disinda hicbir satirda yok)?"""
    disi = [k for d in dizin for k in d["duz"]]
    disi_sayi = set().union(*[d["sayi"] for d in dizin]) if dizin else set()
    ic = [k for d in dizin for k in d["ic"]]
    ic_sayi = set().union(*[d["ic_sayi"] for d in dizin]) if dizin else set()
    return any(_deger_geciyor(z, ic, ic_sayi) and not _deger_geciyor(z, disi, disi_sayi) for z in degerler)


def _hucre_isaretle(satir, indeks, ek=" (?)"):
    """'| 1 | madde | sorumlu | tarih |' satirinda indeks'inci (0'dan, ilk hucre '#') hucrenin sonuna ek yazar."""
    parcalar = satir.split("|")
    k = indeks + 1                      # parcalar[0] ilk '|' oncesi bos
    if k >= len(parcalar) - 1 or ek.strip() in parcalar[k]:
        return satir
    parcalar[k] = parcalar[k].rstrip() + ek + (" " if parcalar[k].endswith(" ") else "")
    return "|".join(parcalar)


def _madde_isaretle(satir, ek=" (?)"):
    """Madde satirinin sonuna (kaynak isareti ⏱.. varsa onun oncesine) belirsizlik isareti ekler."""
    if ek.strip() in satir:
        return satir
    i = satir.find(_ISARET)
    if i > 0:
        return satir[:i].rstrip() + ek + " " + satir[i:]
    return satir.rstrip() + ek


def dayanak_kontrolu(not_md, transkript, esik=0.34, ozet_kaynak=None):
    """Nottaki Karar/Aksiyon/Soru maddelerini ve ozet cumlelerini transkriptle karsilastirir; dayanagi olmayan
    maddeleri cikarir. Modelin terim listesi/gundemden ya da genel bilgisinden 'uydurdugu' satirlara karsi son
    sigorta. Dondurur (yeni_md, atilanlar).
    Karar/aksiyon/soru maddeleri SATIR bazli denetlenir (MIMARI_v3 §6): kok ortusmesi >= DAYANAK_SATIR_ESIK olan 1-3
    ardisik transkript satiri aranir; ⟨⟩ icindeki (eszamanli konusma) kelimeler dayanak sayilmaz. Maddedeki sayi / tutar,
    ay / gun adi ve kod adi (zorunlu deger) pencerenin ⟨⟩ disindaki metninde gecmeli: yalniz ⟨⟩ icinde geciyorsa madde
    atilir, hic gecmiyorsa (?) alir. Bulunan dayanagin ⟨⟩ payi > %30 ise ya da maddeyi tek basina en iyi karsilayan satir
    ⟨⟩ / cakisma satiriysa karar/aksiyon maddesinin sonuna (?) eklenir (madde belirsiz); dayanak satirinin konusmaci
    etiketi kesin degilse ('Ad?:', 'Bilinmeyen:', 'A → B:', 'A / B:') aksiyonun sorumlusu (?) olur.
    ozet/sonraki adim cumleleri esik (kok orani, tum transkript) ile denetlenir; ozet_kaynak: ozet paragrafi icin ek
    dayanak (bolum ozetleri). Ozet cumleleri soyutlayici fiiller ('gorusuldu', 'ele alindi') tasir; yalniz
    transkriptle olculunce dogru cumleler de atiliyordu."""
    kokler = _kokler(sarmal_sil(transkript))
    ozet_zemin = _kokler(transkript)                      # ozet ⟨⟩ icindekini 'tartisildi' diye anabilir
    if len(kokler) < 6:
        return not_md, []
    dizin = _dayanak_dizini(transkript)
    atilan, yeni = [], []
    bolum = ""
    for satir in not_md.split("\n"):
        cizgisiz = satir.strip()
        if cizgisiz.startswith("#"):
            bolum = cizgisiz.lower()
            yeni.append(satir)
            continue
        aday = None
        tablo = False
        if ("karar" in bolum or "aksiyon" in bolum or "soru" in bolum) and cizgisiz.startswith(("-", "*", "|")):
            if cizgisiz.startswith("|"):
                hucreler = [h.strip() for h in cizgisiz.strip("|").split("|")]
                if len(hucreler) < 2 or set(hucreler[0]) <= set("-: ") or hucreler[0] in ("#",):
                    yeni.append(satir)
                    continue
                aday = hucreler[1]
                tablo = True
            else:
                aday = cizgisiz.lstrip("-* ").strip()
        elif "sonraki" in bolum and cizgisiz and cizgisiz.strip(" -.") :
            # cumleyi ';' ile parcalara ayir, dayanagi olmayan parcayi dusur
            parcalar, tutulan = [x.strip() for x in cizgisiz.split(";") if x.strip()], []
            for pr in parcalar:
                pk = _kokler(pr)
                if not pk or len(pk & kokler) / len(pk) >= esik:
                    tutulan.append(pr)
                else:
                    atilan.append(pr)
            yeni.append("; ".join(tutulan).rstrip(",;") + ("" if not tutulan else ("." if not tutulan[-1].endswith(".") else "")))
            continue
        if aday is None:
            yeni.append(satir)
            continue
        ak = _kokler(aday) - _MADDE_DURAK
        if not ak:
            yeni.append(satir)
            continue
        dayanak = _dayanak_bul(ak, dizin, zorunlu=_zorunlu_degerler(aday))
        if dayanak is None or (dayanak["eksik"] and _yalniz_sarmalda(dayanak["eksik"], dizin)):
            atilan.append(aday)                 # dayanak yok ya da sayi / tarih / kod yalniz ⟨⟩ icinden geliyor
            continue
        belirsiz_madde = ((dayanak["pay"] > SARMAL_PAY_SINIRI or dayanak["eksik"] or dayanak["sarmal_kaynak"])
                          and "soru" not in bolum)
        belirsiz_sahip = belirsiz_madde or _etiket_belirsiz(dayanak["ana"]["etiket"])
        if tablo and "aksiyon" in bolum:
            h = [x.strip() for x in cizgisiz.strip("|").split("|")]
            if belirsiz_sahip and len(h) >= 3 and h[2].lower() not in ("", "-", "belirsiz", "?") and "(?)" not in h[2]:
                satir = _hucre_isaretle(satir, 2)
            if belirsiz_madde:
                satir = _hucre_isaretle(satir, 1)
        elif belirsiz_madde:
            satir = _madde_isaretle(satir)
        yeni.append(satir)
    # Ozet paragrafi: cumle cumle
    md = "\n".join(yeni)
    m = re.search(r"(## Özet\s*\n)(.*?)(\n## )", md, flags=re.S)
    if m:
        ozet_kokler = ozet_zemin | (_kokler(ozet_kaynak) if ozet_kaynak else set())
        cumleler = re.split(r"(?<=[.!?])\s+", m.group(2).strip())
        tutulan, ozet_atilan = [], []
        for c in cumleler:
            ck = _kokler(c)
            if not ck or len(ck & ozet_kokler) / len(ck) >= esik - 0.06:
                tutulan.append(c)
            else:
                ozet_atilan.append(c)
        if tutulan:                   # hepsi atiliyorsa olcut yanilmistir: ozeti bos birakma
            atilan += ozet_atilan
            md = md[:m.start(2)] + " ".join(tutulan) + "\n" + md[m.end(2):]
    return md, atilan


def _sn(x):
    """'HH:MM[:SS]' -> gun icindeki saniye (gecersizse 0). Iki saat arasindaki fark icin _sn_fark (gece yarisi)."""
    try:
        p = [int(k) for k in str(x).split(":")]
        return p[0] * 3600 + p[1] * 60 + (p[2] if len(p) > 2 else 0)
    except (ValueError, IndexError):
        return 0


def _sn_fark(a, b):
    """a - b (sn); gece yarisini gecen saatlerde 24 saat sarar: 00:00:05 - 23:59:50 = 15 (eskiden -86385)."""
    d = (_sn(a) - _sn(b)) % 86400
    return d - 86400 if d >= 43200 else d


def _ts(epoch):
    """simdi epoch'u -> yerel 'HH:MM:SS' (duvar saati ofsetiyle)."""
    try:
        return saat.ts(float(epoch))
    except (TypeError, ValueError, OverflowError, OSError):
        return "00:00:00"


def _konusan(s):
    """Transkriptte yazilan konusmaci: gosterim kurali (+ altyazi yedegi isareti)."""
    ad = gorunen_ad(s)
    if s.get("kaynak") == "altyazi-yedek":
        ad += " (altyazıdan, düşük güven)"
    return ad


def nota_girer(s):
    """Satir nota / ozete / dayanaga girer mi? Halusinasyon supheli satirlar girmez (canlı ekranda soluk gorunur,
    transkriptte kalir); altyazi yedegi 'supheli' isaretlidir ama bilincli olarak '(altyazıdan, düşük güven)' ile girer."""
    return not (s.get("supheli") and s.get("kaynak") != "altyazi-yedek")


SARMAL_AC, SARMAL_KAPA = "⟨", "⟩"      # belirsiz metin isareti: karar/aksiyon/sayi/tarih bunun icinden uretilmez
_SARMAL = re.compile(SARMAL_AC + r"[^" + SARMAL_KAPA + r"]*" + SARMAL_KAPA)


def sarmal_sil(metin):
    """Metindeki ⟨...⟩ bolumlerini atar (dayanak / sahip kaniti saymaz)."""
    return _SARMAL.sub(" ", metin or "")


def satir_metni(s):
    """Satirin nota/LLM'e giden metni: eszamanli konusma (cakisma) satirinin tamami ⟨⟩ icine alinir."""
    t = str(s.get("text") or "")
    if s.get("cakisma") and t.strip():
        return SARMAL_AC + t.replace(SARMAL_AC, "").replace(SARMAL_KAPA, "") + SARMAL_KAPA
    return t


def blok_metni(satirlar):
    """Transkript blogu (LLM girdisi, dayanak kontrolu): her satir 'gorunen_ad: metin'. Ardisik ayni konusmaci
    tek satirda birlesir. cakisma satirlari ⟨⟩ icinde; supheli (halusinasyon) satirlar girmez."""
    birlesik = []
    for s in satirlar:
        if not nota_girer(s):
            continue
        kim = _konusan(s)
        if birlesik and birlesik[-1][0] == kim:
            birlesik[-1] = (kim, birlesik[-1][1] + " " + satir_metni(s))
        else:
            birlesik.append((kim, satir_metni(s)))
    return "\n".join(f"{k}: {t}" for k, t in birlesik)


def satirlari_goc(satirlar, tarih):
    """v2 oncesi satirlar ('surum' yok, MIMARI_v3 §3 geriye uyum): bas=bit=ts'den epoch (gece yarisi sarmali),
    spk_guven 0.5, durum 'eski' -> gosterimde 'Ad?', sahip ve katilimci uretmez. Yerinde degistirir.
    Dondurur: goc eden satir sayisi."""
    n, onceki, gun = 0, None, 0
    try:
        taban = saat.duvardan(dt.datetime.combine(tarih, dt.time()).timestamp())
    except Exception:
        taban = 0.0
    for s in satirlar or []:
        if not isinstance(s, dict):
            continue
        t = _sn(s.get("ts"))
        if onceki is not None and t < onceki - 12 * 3600:
            gun += 1                                         # toplanti gece yarisini gecti
        onceki = t
        if s.get("surum"):
            continue
        e = taban + gun * 86400 + t
        s.setdefault("bas", e)
        s.setdefault("bit", s["bas"])
        if s.get("durum") != "eski":
            s["spk_guven"], s["durum"] = ESKI_GUVEN, "eski"
        for k, v in (("adaylar", []), ("devir", False), ("cakisma", False), ("supheli", False), ("belirsiz", []),
                     ("duzeltmeler", [])):
            s.setdefault(k, v)
        n += 1
    return n


class Motor:
    def __init__(self, klasor, baslik, tarih, terimler=None, katilimcilar=None, gundem="",
                 duzelt=True, olay=None, kaynak="ses", ben=None, sablon="genel", esikler=None, altyazi_yedek=False,
                 davet=None):
        """terimler: paylasilan terim.TerimHavuzu (isler/app/toplanti verir); verilmezse bu toplantiya ozel, toplanti
        klasorundeki bir havuz (replay/test: uygulama klasorundeki terimler/ dosyasina dokunulmaz).
        davet: {"govde": davet metni, "ekler": [dosya adlari]} (outlook.aktif_toplanti()['davet'])."""
        self.klasor, self.baslik, self.tarih = klasor, baslik, tarih
        self.terimler = terimler if terimler is not None else terim_mod.TerimHavuzu(
            os.path.join(klasor, "terimler", "terimler.json"))
        self.toplanti_id = os.path.basename(os.path.normpath(klasor))      # terim ogrenmesinde 'ayri toplanti' kimligi
        self.davet = self._davet_hazirla(davet)
        self._onceki_notlar = []                                           # kanit: ayni serinin onceki not.md metinleri
        self._terimler_hazir = False
        self.duzelt_sor_fn = None                                          # test/replay: duzelt.duzelt'e LLM yerine
        self.esikler = esikler
        ham = (esikler or {}).get("esikler") if isinstance((esikler or {}).get("esikler"), dict) else (esikler or {})
        self._hal_esikler = dict(ham)                                      # config 'esikler' (hal_supheli, hal_at ...)
        self.katilimcilar = list(katilimcilar or [])
        self.gundem = gundem
        self.duzelt = duzelt
        self.sablon = sablon                 # not sablonu (llm.SABLONLAR)
        self._olay = olay or (lambda tip, veri: None)
        self.parca_klasor = os.path.join(klasor, "parcalar")
        os.makedirs(self.parca_klasor, exist_ok=True)
        self.mevcut, self.mevcut_tok, self.mevcut_bas, self.son_satir = [], 0, None, None
        self.parca_no = len([f for f in os.listdir(self.parca_klasor) if f.endswith(".json")])
        self.seri = slug(baslik)             # toplanti serisi (terim kapsami, baglam ayirimi; terim.prompt ile ayni slug)
        self.kaynak = kaynak                 # yeni toplanti: hep "ses"; eski kayitta meta'daki deger korunur
        self.ben = ben                       # mikrofon akisinin sahibi (kullanicinin adi)
        self.kulaklik = None                 # pasif tespit (ses 'kulaklik' olayi): True | False | None
        self.altyazi_yedek = bool(altyazi_yedek)   # opt-in: STT yokken altyazi metni 'altyazi-yedek' olarak girer
        self.kilit = threading.Lock()
        self._gunluk_kilit = threading.Lock()
        self._iz_kilit = threading.RLock()   # ses olaylari (STT iscileri) ve okumalar (yakalama dongusu) ayni durumu kullanir
        self.konusmaci = Konusmaci(ben=ben, katilimcilar=self.katilimcilar, esikler=esikler,
                                   olay_fn=self._konusmaci_olayi)
        self.yeniden_kes_fn = None           # isler: SesServisi.yeniden_kes(klip_no, an) -> bool
        self._satir_no = 0
        self._klip_satir = {}                # klip_no -> satir (karar, kesim; kapanmis parcadakiler dahil, tavanli)
        self._satir_parca = {}               # satir id -> kapanmis parcanin sirasi
        self._parca_veri = {}                # sira -> parca verisi (gec karar/kesim; _isle ayni nesneyi yazar)
        self._kesimler = {}                  # ust klip_no -> {"son", "gelen", "parcalar": [satir]}
        self._kesilen = set()                # parcalari yerine konmus ust klip_no'lar
        self._gec_kesim = OrderedDict()      # ust klip_no -> [parca satir]: sure doldu, ozgun satir 'A → B' kaldi,
                                             # parcalar gec gelirse ikisi gelince ozgunun yerine gecer
        self._parca_nesil = {}               # sira -> kapanmis parcanin satir listesi degisim sayaci (gec satir)
        self._ozet_kirli = {}                # sira -> son gec satir zamani (yeniden ozet bekliyor)
        self._silinen = set()                # atilmis / degistirilmis satir id'leri
        self._kapanis = None                 # ertelenmis parca kapanisi {"neden", "son", "sinir": {id}}
        self._bitiyor = False
        self._goc = False                    # Motor.yukle: surumsuz satirlar okunurken goc eder
        self.stt_kesik = False               # STT kesintisi (isler: SesServisi.stt_durumu)
        self._stt_eksik = []                 # ses servisinin hic cozulemeyecek araliklari [[bas, bit]]
        self._bosluklar = []                 # [(akis, bas, bit)] cozulmemis klip araliklari ('bosluk' olaylari)
        self._yuklu_eksik = []               # yuklenen kaydin meta.json'daki stt_eksik'i
        self._yedek_son = {}                 # (konusmaci, metin) -> altyazi yedegi satir id'si (buyuyen satir)
        self._kapsama_yok_bas = None
        self._kapsama_uyarildi = False
        self.ses_olay_sayisi = 0             # gelen klip/bosluk olayi (isler: 'yakalama basladi')
        self.havuz = cf.ThreadPoolExecutor(max_workers=paralel())
        self.isler = []
        llm.GUNLUK_FN = self._llm_gunluk
        llm.UYARI_FN = lambda m: self.log(f"  ! LLM: {m}")
        saat.LOG_FN = lambda m: self.log(f"  ! {m}")       # duvar saati adimi (bir kez)
        llm.SON_HATA = None                  # arayuz 'Not eksik' satirinda bu toplantinin son LLM hatasini gosterir
        # Var olan kaydi yuklerken durumunu koru: eskiden Gecmis'te bir notu yalnizca ACMAK bile toplantiyi
        # 'devam' (yarim) olarak isaretliyordu (ve saklama suresi 'tamam' kayitlari bulamiyordu).
        onceki = None
        try:
            with open(os.path.join(klasor, "meta.json"), encoding="utf-8") as f:
                onceki = json.load(f).get("durum")
        except Exception:
            pass
        self.meta_yaz(onceki or "devam")

    # ---------- olay/log ----------
    def log(self, m):
        self._olay("log", m)

    def adim(self, metin, no=None, toplam=None):
        """Uzun islerin (not uretimi, yeniden ozetleme) asamasi: arayuz ilerleme seridine yazar."""
        self._olay("adim", {"metin": metin, "no": no, "toplam": toplam})

    def _llm_gunluk(self, kayit):
        """LLM cagri istatistigi (finish_reason, token, sure; icerik yok) -> llm_log.jsonl.
        'Not neden yarim/Ingilizce?' sorusunda ilk bakilacak yer."""
        with self._gunluk_kilit:
            with open(os.path.join(self.klasor, "llm_log.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(kayit, ensure_ascii=False) + "\n")

    # ---------- disk ----------
    def meta_yaz(self, durum, **ek):
        """ek: meta.json'a yazilacak ek alanlar (or. konusma_paylari)."""
        yol = os.path.join(self.klasor, "meta.json")
        meta = {}
        try:                                  # bilinmeyen alanlar (ör. transkript_silindi) korunur
            with open(yol, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            pass
        meta.update({"baslik": self.baslik, "tarih": self.tarih.isoformat(), "durum": durum,
                     "katilimcilar": self.katilimcilar, "gundem": self.gundem, "parca": self.parca_no,
                     "kaynak": self.kaynak})
        if self.davet:                        # terim kaniti (toplanti sonu ogrenme, Motor.yukle ile devam)
            meta["davet"] = self.davet
        meta.update(ek)
        with open(yol, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)

    def parca_yolu(self, no):
        return os.path.join(self.parca_klasor, f"parca_{no:03d}.json")

    def parca_oku(self, no):
        # kilit: paralel isciler ayni anda yazarken (parca_yaz dosyayi once bosaltir) yarim JSON okunmasin;
        # _onceki_konular bir onceki parcayi okurken o parcanin ozeti yaziliyor olabilir
        with self.kilit:
            with open(self.parca_yolu(no), encoding="utf-8") as f:
                veri = json.load(f)
        if self._goc:
            satirlari_goc(veri.get("satirlar"), self.tarih)       # v2 oncesi kayit: 'Ad?', sahip uretmez
        return veri

    def parca_yaz(self, veri):
        with self.kilit:
            with open(self.parca_yolu(veri["sira"]), "w", encoding="utf-8") as f:
                json.dump(veri, f, ensure_ascii=False, indent=1)

    def baglam(self, metin=None):
        """LLM baglami. Terimler terim.adaylar'dan (seri + genel + kok eslesmesi); metin verilirse YALNIZCA bu metinde
        gecenler eklenir: baska toplantilardan ogrenilmis terimler ozet/notta konu gibi belirmesin."""
        terimler = self.terimler.adaylar(self.seri, metin, self.katilimcilar)
        if metin is not None:
            terimler = terim_mod.ilgili(terimler, metin)
        return llm.baglam_metni(self.baslik, self.tarih, self.katilimcilar, "; ".join(terimler) or "-", self.gundem)

    def transkript_metni(self):
        parcalar = []
        for no in range(1, self.parca_no + 1):
            try:
                parcalar.append(blok_metni(self.parca_oku(no)["satirlar"]))
            except Exception:
                pass
        parcalar.append(blok_metni(self.mevcut))
        return "\n".join(parcalar)

    # ---------- yakalama tarafi (Faz 1: konusmaci yalniz zamandan, konusmaci.py) ----------
    def _konusmaci_olayi(self, d):
        """Konusmaci gunlugu (metin yok) -> olaylar.jsonl."""
        try:
            kayit = dict(d, an=round(saat.simdi(), 3))
            with self._gunluk_kilit:
                with open(os.path.join(self.klasor, "olaylar.jsonl"), "a", encoding="utf-8") as f:
                    f.write(json.dumps(kayit, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _jsonl(self, kayit):
        with self._gunluk_kilit:
            with open(os.path.join(self.klasor, "altyazi.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(kayit, ensure_ascii=False) + "\n")

    def ses_olayi(self, olay):
        """Ses servisinin olaylari (C1, SesServisi(olay_fn=...)); STT iscisi is parcacigindan cagrilir.
        klip: satir v2 hemen olusur, konusmaci 'beklemede…' (karar kontrol()'de gelir); ust_klip'li klip yeniden
        kesilen satirin parcasidir. bosluk: STT kesintisi araligi (nota 'transkript eksik'). kulaklik: pasif tespit.
        capa: ses saat capasi yenilendi (konusmaci L capalarini duzeltir).
        Dondurur: olusan satir | None."""
        if not isinstance(olay, dict):
            return None
        tur = olay.get("tur")
        with self._iz_kilit:
            if tur == "kulaklik":
                self.kulaklik = olay.get("durum") if olay.get("durum") in (True, False) else None
                self.konusmaci.kulaklik_ayarla(self.kulaklik)
                return None
            if tur == "capa":                   # ses saat capasi yenilendi: L capalari duzeltilir
                self.konusmaci.saat_capasi(olay.get("akis"), olay.get("fark"))
                return None
            if tur == "bosluk":
                self.ses_olay_sayisi += 1
                self._bosluk_ekle(olay)
                return None
            if tur != "klip":
                return None
            self.ses_olay_sayisi += 1
            return self._klip_ekle(olay)

    def _bosluk_ekle(self, olay):
        try:
            b = (olay.get("akis") or "loopback", float(olay["bas"]), float(olay["bit"]))
        except (KeyError, TypeError, ValueError):
            return
        self._bosluklar = (self._bosluklar + [b])[-2000:]
        self._olay("stt_bosluk", {"akis": b[0], "bas": b[1], "bit": b[2], "neden": olay.get("neden")})

    def _klip_ekle(self, olay):
        try:
            no = int(olay["klip_no"])
            bas = float(olay["bas"])
            bit = max(float(olay.get("bit", bas)), bas)
        except (KeyError, TypeError, ValueError):
            self.log("  · geçersiz klip olayı atlandı")
            return None
        akis = olay.get("akis") or "loopback"
        # kesinti sonrasi gec cozulen klip: ayni araligin boslugu artik eksik degil
        self._bosluklar = [x for x in self._bosluklar if not (x[0] == akis and x[1] < bit + 0.5 and x[2] > bas - 0.5)]
        self.konusmaci.klip_ekle(dict(olay, klip_no=no, akis=akis, bas=bas, bit=bit))
        raw = (olay.get("text") or "").strip()
        ust = olay.get("ust_klip")
        if not raw:
            if ust is not None:
                self._bos_parca(ust)          # metinsiz yeniden kesme parcasi: geldi sayilir, satir yok
            return None                       # metinsiz klip: satir yok (karari yok sayilir)
        satir = self._yeni_satir(no, akis, bas, bit, raw, ts=olay.get("ts"), supheli=bool(olay.get("supheli")),
                                 hal_puani=olay.get("hal_puani"), rms=olay.get("rms"))
        self._klip_satir[no] = satir
        while len(self._klip_satir) > KLIP_TAVAN:
            self._klip_satir.pop(next(iter(self._klip_satir)))
        if ust is not None and ust in self._kesilen:
            # ust klip parcalariyla zaten degisti: fazladan parca asla ayri satir olmaz
            self._silinen.add(satir["id"])
            self.log(f"  · yeniden kesme parçası fazladan geldi, atıldı (üst klip {ust})")
        elif ust is not None and ust in self._gec_kesim:
            self._gec_parca(ust, satir)
        elif ust is not None and (ust in self._kesimler or ust in self._klip_satir):
            self._kesim_parcasi(ust, satir)
        else:
            self._satir_yerlestir(satir)
        return satir

    def _yeni_satir(self, klip_no, akis, bas, bit, raw, ts=None, supheli=False, kaynak="ses", hal_puani=None,
                    rms=None):
        """text = raw: duzeltme parca kapaninca duzelt.duzelt kapisindan gecer (metne kor degisiklik yok).
        hal_puani/rms: ses.py halusinasyon puani ve sesli aralik RMS'i (cizelge kuraliyla yeniden puanlama icin)."""
        self._satir_no += 1
        satir = {"surum": SURUM, "id": self._satir_no, "klip_no": klip_no, "akis": akis,
                 "bas": round(bas, 3), "bit": round(bit, 3), "ts": ts or _ts(bas), "raw": raw, "text": raw,
                 "speaker": "?", "spk_guven": 0.0, "adaylar": [], "devir": False, "cakisma": False,
                 "supheli": supheli, "belirsiz": [], "duzeltmeler": [], "kaynak": kaynak, "durum": "beklemede"}
        if isinstance(hal_puani, (int, float)) and not isinstance(hal_puani, bool):
            satir["hal_puani"] = round(float(hal_puani), 4)
        if isinstance(rms, (int, float)) and not isinstance(rms, bool):
            satir["rms"] = round(float(rms), 5)
        return satir

    @staticmethod
    def _tok(s):
        return llm.token_tahmin(f"{s.get('speaker') or '?'}: {s.get('text', '')}")

    def _tok_yenile(self):
        self.mevcut_tok = sum(self._tok(s) for s in self.mevcut)

    def _satir_yerlestir(self, satir):
        """Yeni satir: altyazi.jsonl + acik parca (bas sirasiyla; kesinti sonrasi gec gelen klip araya girer),
        'satir' olayi, parca sinirlari. bas'i kapanmis bir parcanin araligina dusen gec klip (STT kesintisi sonrasi
        cozulen) o parcaya girer: transkript ve parca araliklari kronolojik kalir, parca yeniden yazilir/ozetlenir."""
        self._jsonl(satir)
        simdi = saat.simdi()
        sira = self._kapali_parca_bul(satir.get("bas"))
        if sira is not None:
            self._kapali_parcaya_ekle(sira, satir)
            return
        if self.mevcut and self.mevcut_tok >= SESSIZLIK_MIN_TOKEN and simdi - self.son_satir > SESSIZLIK_SN:
            self.parca_kapat("sessizlik")
        i = len(self.mevcut)
        while i > 0 and (self.mevcut[i - 1].get("bas") or 0) > satir["bas"]:
            i -= 1
        self.mevcut.insert(i, satir)
        self.mevcut_tok += self._tok(satir)
        self.mevcut_bas = self.mevcut_bas or simdi
        self.son_satir = simdi
        self._olay("satir", dict(satir, gorunen=gorunen_ad(satir)))
        if self.mevcut_tok >= parca_token():
            self.parca_kapat("token")
        elif simdi - self.mevcut_bas >= PARCA_SURE_SN:
            self.parca_kapat("süre")

    def _kapali_parca_bul(self, bas):
        """bas, son kapanmis parcanin bitisinden onceyse hedef parca sirasi: bas'i kapsayan (ya da ondan once baslayan
        en son) parca; hepsinden onceyse ilk parca. Aksi (normal akis) None: acik parca."""
        if not isinstance(bas, (int, float)) or not self._parca_veri:
            return None
        son = self._parca_veri[max(self._parca_veri)]
        if not isinstance(son.get("bit"), (int, float)) or bas >= son["bit"]:
            return None
        onceki = [n for n, v in self._parca_veri.items() if isinstance(v.get("bas"), (int, float)) and v["bas"] <= bas]
        return max(onceki) if onceki else min(self._parca_veri)

    def _kapali_parcaya_ekle(self, sira, satir):
        veri = self._parca_veri[sira]
        liste = veri["satirlar"]
        i = len(liste)
        while i > 0 and (liste[i - 1].get("bas") or 0) > satir["bas"]:
            i -= 1
        liste.insert(i, satir)
        self._satir_parca[satir["id"]] = sira
        if isinstance(veri.get("bas"), (int, float)):
            veri["bas"] = min(veri["bas"], satir["bas"])
            veri["bit"] = max(veri["bit"], satir.get("bit", satir["bas"]))
            veri["baslangic"], veri["bitis"] = _ts(veri["bas"]), _ts(veri["bit"])
        veri["token"] = sum(self._tok(x) for x in liste)
        self._parca_nesil[sira] = self._parca_nesil.get(sira, 0) + 1
        self._ozet_kirli[sira] = saat.simdi()
        self.parca_yaz(veri)
        self.log(f"  · geç gelen klip ({satir['ts']}) kapanmış parça {sira}'e eklendi; parça yeniden özetlenecek")
        self._olay("satir", dict(satir, gorunen=gorunen_ad(satir), parca=sira))

    def _ozetleri_yenile(self, simdi=None, hepsi=False):
        """Gec satir eklenmis kapanmis parcalari (OZET_YENI_SN sessizlikten sonra ya da hepsi) yeniden ozetler."""
        for sira in [n for n, t in self._ozet_kirli.items() if hepsi or (simdi or saat.simdi()) - t >= OZET_YENI_SN]:
            del self._ozet_kirli[sira]
            veri = self._parca_veri.get(sira)
            if veri is not None:
                # gec gelen satirin da terim/ad duzeltmesi gerekir (eskiden duzelt=False: gec satir ham kalip ozete
                # ham metinle giriyordu); 'raw'dan calistigi icin onceki duzeltmeler tekrar uygulanir (idempotent)
                self.isler.append(self.havuz.submit(self._isle, veri, self.duzelt))

    def _satir_bul(self, sid):
        """id -> (liste, indeks, parca sirasi | None: acik parca) ya da None (yerlesmemis / silinmis)."""
        if sid is None or sid in self._silinen:
            return None
        for i in range(len(self.mevcut) - 1, -1, -1):
            if self.mevcut[i]["id"] == sid:
                return self.mevcut, i, None
        sira = self._satir_parca.get(sid)
        veri = self._parca_veri.get(sira)
        if veri is not None:
            for i, s in enumerate(veri["satirlar"]):
                if s["id"] == sid:
                    return veri["satirlar"], i, sira
        return None

    def _degisti(self, sira):
        """Satir listesi degisti: acik parcada token yeniden sayilir, kapanmis parca diske yeniden yazilir."""
        if sira is None:
            self._tok_yenile()
        elif sira in self._parca_veri:
            self.parca_yaz(self._parca_veri[sira])

    # --- konusmaci kararlari ---
    def _kararlari_isle(self, kararlar):
        """Konusmaci kararlarini satirlara uygular. Dondurur: yeniden kesilecek [(klip_no, an)] (kilit disinda)."""
        kes = []
        for k in kararlar or []:
            if k.get("bol") is not None and not k.get("at"):
                satir = self._klip_satir.get(k.get("klip_no"))
                if satir is None or satir["id"] in self._silinen:
                    continue                  # metinsiz klip
                if self._bitiyor or self.yeniden_kes_fn is None or self._satir_bul(satir["id"]) is None:
                    self._karar_uygula(self.konusmaci.bolunemedi(k["klip_no"]))
                else:
                    kes.append((k["klip_no"], k["bol"]))
                continue
            self._karar_uygula(k)
        return kes

    def _kesimleri_yap(self, kes):
        """bol kararlari: SesServisi.yeniden_kes; False ise Konusmaci.bolunemedi ('A → B')."""
        for no, an in kes:
            try:
                tamam = bool(self.yeniden_kes_fn(no, an)) if self.yeniden_kes_fn else False
            except Exception as e:
                self.log(f"✖ yeniden kesme hatası: {e!r}")
                tamam = False
            with self._iz_kilit:
                if tamam and not self._bitiyor:
                    if no not in self._kesilen:
                        self._kesimler.setdefault(no, {"son": saat.simdi() + KESIM_BEKLE_SN, "gelen": 0,
                                                       "parcalar": []})
                else:
                    self._karar_uygula(self.konusmaci.bolunemedi(no))

    @staticmethod
    def cizelge_etkin(k):
        """Konusmaci karari -> klip araliginda altyazi etkinligi var miydi? True/False; None: kapsama yok (altyazi
        kapali/okunamiyor — halusinasyon cizelge kurali uygulanmaz). Loopback: uzak kisi (aday listesi), mikrofon:
        kullanici (aday listesinin ilki 'ben')."""
        if not k.get("kapsama", True):
            return None
        adaylar = k.get("adaylar") or []
        try:
            if k.get("akis") == "mikrofon":
                return bool(adaylar) and float(adaylar[0][1]) > 0
            return any(float(a[1]) > 0 for a in adaylar)
        except (TypeError, ValueError, IndexError):
            return None

    def _cizelge_puanla(self, satir, k):
        """Atama sonrasi halusinasyon yeniden puani (MIMARI_v3 §4 'cizelge kurali'): altyazi saglikliyken klip
        araliginda hic etkinlik yoksa +0.6. Dondurur 'at' (>= hal_at) | 'supheli' (>= hal_supheli) | None.
        Yalniz ses klipleri (kaynak 'ses'); ses.py cizelge_fn verilmediginden taban puan kurali icermez."""
        if satir.get("kaynak") != "ses" or satir.get("cizelge_etkin") is False or self.cizelge_etkin(k) is not False:
            return None                                  # (ikinci karar -- bolunemedi vb. -- puani tekrar artirmaz)
        taban = satir.get("hal_puani")
        taban = float(taban) if isinstance(taban, (int, float)) and not isinstance(taban, bool) else 0.0
        puan = round(min(1.0, taban + CIZELGE_PUANI), 4)
        satir["hal_puani"], satir["cizelge_etkin"] = puan, False
        if puan >= hal_esik(self._hal_esikler, "hal_at"):
            return "at"
        if puan >= hal_esik(self._hal_esikler, "hal_supheli"):
            satir["supheli"] = True
            return "supheli"
        return None

    def _karar_uygula(self, k):
        satir = self._klip_satir.get(k.get("klip_no"))
        if satir is None or satir["id"] in self._silinen:
            return
        yer = self._satir_bul(satir["id"])
        hal = None if k.get("at") else self._cizelge_puanla(satir, k)
        if k.get("at") or hal == "at":
            neden = (k.get("neden") or "yanki") if k.get("at") else "halusinasyon"
            self._silinen.add(satir["id"])
            for kesim in self._kesimler.values():         # yerlesmemis parca
                if satir in kesim["parcalar"]:
                    kesim["parcalar"].remove(satir)
            for g in self._gec_kesim.values():
                if satir in g["parcalar"]:
                    g["parcalar"].remove(satir)
            if yer is None:
                return
            liste, i, sira = yer
            del liste[i]
            self._degisti(sira)
            self._jsonl({"id": satir["id"], "sil": True, "neden": neden})
            self._olay("satir_sil", {"id": satir["id"], "neden": k.get("neden") if k.get("at") else neden})
            self.log(f"  · klip atıldı ({neden}, {satir['ts']}): {satir['text'][:40]}")
            return
        alan = {"speaker": k.get("speaker") or "?", "spk_guven": float(k.get("spk_guven") or 0.0),
                "adaylar": [list(a) for a in (k.get("adaylar") or [])], "devir": bool(k.get("devir")),
                "cakisma": bool(k.get("cakisma")), "kimlik_turu": k.get("kimlik_turu"),
                "kapsama": bool(k.get("kapsama", True)), "spk_neden": k.get("neden"), "durum": "karar"}
        satir.update(alan)
        if hal is not None:                              # cizelge kurali satiri supheli yapti: kalici + canli soluk
            alan.update(supheli=satir["supheli"], hal_puani=satir["hal_puani"], cizelge_etkin=False)
            self.log(f"  · şüpheli klip ({satir['ts']}): altyazıda etkinlik yok, halüsinasyon puanı "
                     f"{satir['hal_puani']:.2f}; nota girmez")
        if kesin_ad_mi(satir) and satir["speaker"] not in self.katilimcilar:
            self.katilimcilar.append(satir["speaker"])   # "?", "Ad?", birlesik adlar, oda/genel girmez
        if yer is None:
            return                                       # yerlesmemis parca: yerine konunca yazilir
        self._degisti(yer[2])
        self._jsonl(dict(alan, id=satir["id"], guncelle=True))
        if yer[2] is not None:
            self.log(f"  · kapanmış parça {yer[2]}: satır {satir['id']} konuşmacısı sonradan geldi")
        self._olay("satir_guncelle", dict(satir, gorunen=gorunen_ad(satir)))

    def _zaman_asimi(self, satir):
        """Karari gelmeyen satir (parca kapanisi zorlandi): '?' (konusmaci karari sonradan gelirse guncellenir)."""
        satir.update(speaker="?", spk_guven=0.0, durum="karar", spk_neden="zaman_asimi")
        self._jsonl({"id": satir["id"], "guncelle": True, "speaker": "?", "spk_guven": 0.0, "durum": "karar",
                     "spk_neden": "zaman_asimi"})
        self._olay("satir_guncelle", dict(satir, gorunen=gorunen_ad(satir)))

    # --- yeniden kesme ---
    def _bos_parca(self, ust):
        """Metni bos gelen yeniden kesme parcasi (STT bu yarida bir sey duymadi): parca geldi sayilir; diger parca
        ozgun satirin tamamini tasir."""
        if ust in self._kesilen:
            return
        if ust in self._gec_kesim:
            self._gec_parca(ust, None)
        elif ust in self._kesimler or ust in self._klip_satir:
            self._kesim_parcasi(ust, None)

    def _kesim_parcasi(self, ust, satir):
        k = self._kesimler.get(ust)
        if k is None:
            k = self._kesimler[ust] = {"son": saat.simdi() + KESIM_BEKLE_SN, "gelen": 0, "parcalar": []}
        k["gelen"] += 1
        if satir is not None:
            k["parcalar"].append(satir)
        if k["gelen"] >= 2:
            self._kesim_tamamla(ust)

    def _kesim_tamamla(self, ust):
        """Iki parca geldi (ya da sure doldu): ozgun satir parcalariyla degisir (sirayla). Sure dolup parca eksikse
        ozgun satir tum metniyle 'A → B' kalir (tek parcayla degismez: diger yarinin metni kaybolurdu); gec gelen
        parcalar _gec_parca ile beklenir, ikisi gelince ozgunun yerine gecer."""
        k = self._kesimler.pop(ust, None)
        if k is None:
            return
        eski = self._klip_satir.get(ust)
        yeni = sorted((s for s in k["parcalar"] if s["id"] not in self._silinen), key=lambda s: s["bas"])
        if k["gelen"] < 2 or not yeni:
            if k["gelen"] < 2:
                self._gec_kesim[ust] = {"gelen": k["gelen"], "parcalar": list(k["parcalar"])}
                while len(self._gec_kesim) > GEC_KESIM_TAVAN:
                    self._gec_kesim.popitem(last=False)
                if k["parcalar"]:
                    self.log(f"  · yeniden kesme: parçalardan biri {KESIM_BEKLE_SN} sn içinde gelmedi; özgün satır "
                             "korunuyor ('A → B')")
            else:
                self._kesilen.add(ust)
            self._karar_uygula(self.konusmaci.bolunemedi(ust))
            return
        self._kesilen.add(ust)
        self._yerine_koy(eski, yeni)

    def _gec_parca(self, ust, satir):
        """Suresi dolmus kesimin gec parcasi: ozgun satir ('A → B', tum metin) hala duruyorsa iki parca gelince onunla
        degisir (kapanmis parcadaysa parca yeniden yazilir); ozgun satir yoksa parcalar atilir. Asla ayri satir olmaz."""
        g = self._gec_kesim[ust]
        g["gelen"] += 1
        if satir is not None:
            g["parcalar"].append(satir)
        if g["gelen"] < 2:
            return
        del self._gec_kesim[ust]
        bekleyen = g["parcalar"]
        self._kesilen.add(ust)
        eski = self._klip_satir.get(ust)
        yeni = sorted((s for s in bekleyen if s["id"] not in self._silinen), key=lambda s: s["bas"])
        if not yeni or eski is None or self._satir_bul(eski["id"]) is None:
            for s in bekleyen:
                self._silinen.add(s["id"])
            self.log(f"  · geç gelen yeniden kesme parçaları atıldı (üst klip {ust}: "
                     + ("parçalar boş, özgün satır kaldı)" if not yeni else "özgün satır yok)"))
            return
        self.log(f"  · geç gelen yeniden kesme parçaları özgün satırın yerine yazıldı (üst klip {ust})")
        self._yerine_koy(eski, yeni)

    def _yerine_koy(self, eski, yeni):
        """Ozgun satiri (eski) parca satirlariyla degistirir; ozgun satir yoksa (atildi) parcalar ayri satir olur."""
        yer = self._satir_bul(eski["id"]) if eski else None
        if yer is None:
            for s in yeni:                                # ozgun satir yok (atildi): parcalar ayri satir
                self._satir_yerlestir(s)
            return
        liste, i, sira = yer
        for s in yeni:
            self._jsonl(dict(s, yerine_id=eski["id"]))
        self._jsonl({"id": eski["id"], "sil": True, "neden": "yeniden_kesildi", "yerine": [s["id"] for s in yeni]})
        liste[i:i + 1] = yeni
        self._silinen.add(eski["id"])
        if sira is not None:
            for s in yeni:
                self._satir_parca[s["id"]] = sira
        elif self._kapanis is not None and eski["id"] in self._kapanis["sinir"]:
            self._kapanis["sinir"].update(s["id"] for s in yeni)
        self._degisti(sira)
        self._olay("satir_degistir", {"eski_id": eski["id"], "yeni": [dict(s, gorunen=gorunen_ad(s)) for s in yeni]})

    # --- okuma / altyazi ---
    def okuma_ekle(self, okuma):
        """Yakalayicinin HER UIA okumasi (C2, Yakalayici(okuma_fn=...)): konusmaci cizelgesine gider (ad + zaman;
        altyazi metni transkripte girmez). Ardindan hazir kararlar uygulanir."""
        with self._iz_kilit:
            try:
                self.konusmaci.okuma_ekle(okuma)
            except Exception as e:
                self.log(f"✖ altyazı okuması işlenemedi: {e!r}")
                return
            kes = self._kararlari_isle(self.konusmaci.kontrol(saat.simdi()))
        self._kesimleri_yap(kes)

    def altyazi_satiri(self, satir):
        """Yakalayicinin altyazi satiri (Yakalayici.oku). Teams altyazi METNI kotudur, transkripte girmez: yalniz
        canli gorunum ('altyazi_izi', ayar altyazi_goster). Opt-in yedek (config altyazi_yedek): STT yokken satir
        kaynak 'altyazi-yedek', supheli=True, spk_guven 0.6 ile girer (gosterim 'Ad?', sahip uretmez)."""
        if not isinstance(satir, dict) or not (satir.get("text") or "").strip():
            return None
        self._olay("altyazi_izi", {"ts": satir.get("ts"), "speaker": satir.get("speaker") or "?",
                                   "text": satir["text"], "guncelle": bool(satir.get("guncelle"))})
        if not (self.altyazi_yedek and self.stt_kesik):
            return None
        return self.satir_ekle(satir, kaynak="altyazi-yedek")

    def satir_ekle(self, satir, kaynak="altyazi-yedek"):
        """Konusmacisi belli satiri dogrudan ekler (konusmaci kararina girmez). satir: {"ts", "speaker", "text"
        [, "bas", "bit", "spk_guven"]}; yakalayicinin buyume kaydi ({'guncelle', 'onceki_text'}) acik parcadaki ayni
        satiri gunceller. Varsayilan kaynak 'altyazi-yedek' (altyazi metni hic 'ses' diye yazilmaz): supheli,
        spk_guven 0.6. Dondurur: satir v2."""
        kim = satir.get("speaker") or "?"
        yedek = kaynak == "altyazi-yedek"
        with self._iz_kilit:
            if satir.get("guncelle"):
                sid = self._yedek_son.pop((kim, satir.get("onceki_text")), None)
                yer = self._satir_bul(sid)
                if yer is not None and yer[2] is None:
                    s = yer[0][yer[1]]
                    s["raw"] = s["text"] = satir["text"]
                    self._tok_yenile()
                    self._jsonl({"id": s["id"], "guncelle": True, "raw": s["raw"], "text": s["text"]})
                    self._yedek_son[(kim, satir["text"])] = s["id"]
                    self._olay("satir_guncelle", dict(s, gorunen=gorunen_ad(s)))
                    return s
            an = saat.simdi()
            bas = float(satir.get("bas", an))
            s = self._yeni_satir(None, satir.get("akis") or "loopback", bas, float(satir.get("bit", bas)),
                                 satir["text"], ts=satir.get("ts"), supheli=yedek or bool(satir.get("supheli")),
                                 kaynak=kaynak)
            s.update(speaker=kim, spk_guven=float(satir.get("spk_guven", YEDEK_GUVEN if yedek else 1.0)),
                     durum="altyazi" if yedek else "karar", kimlik_turu=self.konusmaci.kimlik_turu(kim))
            self._satir_yerlestir(s)
            self._yedek_son[(kim, satir["text"])] = s["id"]
            while len(self._yedek_son) > 200:
                self._yedek_son.pop(next(iter(self._yedek_son)))
            return s

    def ses_satiri(self, ts, konusmaci, metin, akis):
        """ESKI arayuz (tools/replay MevcutHat, SesServisi satir_fn yolu): (ts, kim, metin, akis) -> C1 klip olayi.
        Klip bitisi bilinmez (bas=bit=ts); konusmaci karari yalniz zaman cizelgesinden gelir (metin eslemesi yok)."""
        with self._iz_kilit:
            self._eski_no = getattr(self, "_eski_no", 0) - 1        # ses klip_no'lariyla carpismasin
            no = self._eski_no
        bas = self._ts_epoch(ts)
        return self.ses_olayi({"tur": "klip", "klip_no": no, "akis": akis or "loopback", "bas": bas, "bit": bas,
                               "text": metin, "ts": ts, "ust_klip": None, "supheli": False})

    @staticmethod
    def _ts_epoch(ts):
        """'HH:MM:SS' (yerel duvar saati) -> simdiye en yakin (±12 sa) gunun simdi epoch'u."""
        simdi = saat.simdi()
        duvar = saat.duvar(simdi)
        gun = dt.datetime.fromtimestamp(duvar).date()
        e = dt.datetime.combine(gun, dt.time()).timestamp() + _sn(ts)
        if e - duvar > 43200:
            e -= 86400
        elif duvar - e > 43200:
            e += 86400
        return saat.duvardan(e)

    # --- durum ---
    def stt_durumu_ayarla(self, durum):
        """isler: SesServisi.stt_durumu her turda. Altyazi yedegi yalniz STT kesikken calisir; hic cozulemeyecek
        araliklar nota 'STT yok — transkript eksik' olarak yazilir."""
        if not isinstance(durum, dict):
            return
        with self._iz_kilit:
            self.stt_kesik = durum.get("durum") == "kesinti"
            self._stt_eksik = [list(a)[:2] for a in (durum.get("eksik") or []) if len(a) >= 2]

    def stt_eksik(self):
        """Transkripti eksik araliklar [[bas, bit]] (epoch): ses servisinin cozulemeyecek araliklari + cozulmemis
        bosluk olaylari + yuklenen kayittakiler; 30 sn'den yakin araliklar birlesir."""
        with self._iz_kilit:
            ham = sorted([list(a) for a in self._stt_eksik] + [[b, e] for _, b, e in self._bosluklar]
                         + [list(a) for a in self._yuklu_eksik])
        out = []
        for b, e in ham:
            if out and b - out[-1][1] < 30:
                out[-1][1] = max(out[-1][1], e)
            else:
                out.append([b, e])
        return out

    def _kapsama_denetle(self, simdi):
        """Konusmaci kapsamasi (altyazi okumasi) KAPSAMA_UYARI_SN'den uzun yoksa arayuze 'kapsama' olayi."""
        try:
            var = bool(self.konusmaci.durum().get("kapsama"))
        except Exception:
            return
        if var:
            self._kapsama_yok_bas = None
            if self._kapsama_uyarildi:
                self._kapsama_uyarildi = False
                self._olay("kapsama", {"var": True, "bas": None})
                self.log("konuşmacı bilgisi geri geldi (altyazı okunuyor)")
            return
        if self._kapsama_yok_bas is None:
            self._kapsama_yok_bas = simdi
        elif not self._kapsama_uyarildi and simdi - self._kapsama_yok_bas > KAPSAMA_UYARI_SN:
            self._kapsama_uyarildi = True
            self._olay("kapsama", {"var": False, "bas": self._kapsama_yok_bas})
            self.log("! Konuşmacı bilgisi yok: Teams altyazısı kapalı ya da okunamıyor — konuşmacılar "
                     "'Bilinmeyen' yazılır")

    def _bekliyor_mu(self, s):
        return s.get("durum") == "beklemede"

    def kontrol(self):
        """Yakalama dongusunun her turunda: hazir konusmaci kararlari, yeniden kesme zaman asimi, ertelenmis
        parca kapanisi (en fazla KAPANIS_BEKLE_SN), parca suresi, konusmaci kapsama uyarisi."""
        saat.adim_denetle()                              # duvar saati sicradiysa bir kez loglar (zaman cizelgesi etkilenmez)
        simdi = saat.simdi()
        with self._iz_kilit:
            kes = self._kararlari_isle(self.konusmaci.kontrol(simdi))
            for ust in [u for u, k in self._kesimler.items() if simdi >= k["son"]]:
                self._kesim_tamamla(ust)
            self._kapsama_denetle(simdi)
            if self._ozet_kirli:
                self._ozetleri_yenile(simdi)
            if self._kapanis is not None:
                self._kapanis_dene(simdi)
            elif self.mevcut and self.mevcut_bas is not None and simdi - self.mevcut_bas >= PARCA_SURE_SN:
                self.parca_kapat("süre")
        self._kesimleri_yap(kes)

    def _kapanis_dene(self, simdi):
        kp = self._kapanis
        bekleyen = [s for s in self.mevcut if s["id"] in kp["sinir"] and self._bekliyor_mu(s)]
        if bekleyen and simdi < kp["son"]:
            return
        if bekleyen:
            # sure doldu: aralikta bekleyen klipler zorlanir (konusmaci baska aralikta bekleyen tasimiyorsa bitir)
            bas, bit = min(s["bas"] for s in bekleyen), max(s["bit"] for s in bekleyen)
            if self.konusmaci.bekleyen() <= self.konusmaci.bekleyen(bas - 1e-3, bit + 1e-3):
                eski, self._bitiyor = self._bitiyor, True          # zorlanan kapanista yeniden kesme yok
                try:
                    self._kararlari_isle(self.konusmaci.bitir())
                finally:
                    self._bitiyor = eski
            for ust in [u for u in self._kesimler if (self._klip_satir.get(u) or {}).get("id") in kp["sinir"]]:
                self._kesim_tamamla(ust)
            kalan = [s for s in self.mevcut if s["id"] in kp["sinir"] and self._bekliyor_mu(s)]
            for s in kalan:
                self._zaman_asimi(s)
            self.log(f"! parça kapanışı {KAPANIS_BEKLE_SN} sn bekledi: {len(bekleyen)} satırın konuşmacı kararı "
                     f"zorlandı ({len(kalan)} satır '?')")
        self._parca_kapat_simdi(kp["neden"], kp["sinir"])

    def parca_kapat(self, neden, zorla=False):
        """Acik parcayi kapatir. Konusmaci karari bekleyen satir varsa kapanis ertelenir (kontrol() en fazla
        KAPANIS_BEKLE_SN bekler, sonra zorlar); erteleme surerken gelen satirlar sonraki parcaya kalir."""
        with self._iz_kilit:
            if not self.mevcut:
                self._kapanis = None
                return
            if zorla:
                self._parca_kapat_simdi(neden, None)
                return
            if self._kapanis is not None:
                return
            sinir = {s["id"] for s in self.mevcut}
            if any(self._bekliyor_mu(s) for s in self.mevcut):
                self._kapanis = {"neden": neden, "son": saat.simdi() + KAPANIS_BEKLE_SN, "sinir": sinir}
                return
            self._parca_kapat_simdi(neden, sinir)

    def _parca_kapat_simdi(self, neden, sinir):
        self._kapanis = None
        if sinir is None:
            satirlar, kalan = self.mevcut, []
        else:
            satirlar = [s for s in self.mevcut if s["id"] in sinir]
            kalan = [s for s in self.mevcut if s["id"] not in sinir]
        if not satirlar:
            return
        self.parca_no += 1
        if all(isinstance(s.get("bas"), (int, float)) for s in satirlar):
            bas_e = min(s["bas"] for s in satirlar)
            bit_e = max(s.get("bit", s["bas"]) for s in satirlar)
            bas_ts, bit_ts = _ts(bas_e), _ts(bit_e)          # gece yarisi: metin min/max degil, epoch
        else:
            bas_e = bit_e = None
            sn = _gun_sn(satirlar)
            bas_ts = satirlar[sn.index(min(sn))]["ts"]
            bit_ts = satirlar[sn.index(max(sn))]["ts"]
        veri = {"sira": self.parca_no, "neden": neden, "token": sum(self._tok(s) for s in satirlar),
                "baslangic": bas_ts, "bitis": bit_ts, "bas": bas_e, "bit": bit_e, "surum": SURUM,
                "satirlar": satirlar, "duzeltmeler": [], "ozet": None}
        self._parca_veri[veri["sira"]] = veri
        for s in satirlar:
            self._satir_parca[s["id"]] = veri["sira"]
        self.parca_yaz(veri)
        self.mevcut = kalan
        self._tok_yenile()
        self.mevcut_bas = saat.simdi() if kalan else None
        self.meta_yaz("devam")
        self._cizelge_yaz()
        self._olay("parca_kapandi", {"sira": veri["sira"], "neden": neden, "token": veri["token"],
                                     "aralik": f"{veri['baslangic']}–{veri['bitis']}"})
        self.isler.append(self.havuz.submit(self._isle, veri, self.duzelt))

    def _cizelge_yaz(self):
        """Konusmaci araliklari (metin yok) -> cizelge.json."""
        try:
            araliklar = self.konusmaci.araliklar_listesi()
            if not araliklar:
                return
            with open(os.path.join(self.klasor, "cizelge.json"), "w", encoding="utf-8") as f:
                json.dump({"durum": self.konusmaci.durum(), "araliklar": araliklar}, f, ensure_ascii=False)
        except Exception as e:
            self.log(f"! konuşmacı çizelgesi yazılamadı: {e!r}")

    # ---------- arka plan ----------
    def _isle(self, veri, duzelt):
        """Kapanmis parca: terim/ad duzeltmesi (duzelt.duzelt) + ozet (arka plan). Satir listesi bu sirada degisebilir
        (gec karar ile atilan / yeniden kesilen satir, gec gelen klip): satirlarin anlik goruntusu kilit altinda alinir,
        sonuclar satir id'siyle geri yazilir (yalniz hala listede olanlara). Arada liste degistiyse (nesil) ozet
        yazilmaz; yeniden ozet zaten siradadir."""
        sira = veri.get("sira")
        with self._iz_kilit:
            nesil = self._parca_nesil.get(sira, 0)
        # 1) duzeltme gecisi -- basarisiz olursa ham metinle devam
        if duzelt and veri["token"] >= DUZELT_MIN_TOKEN:
            self.log(f"  parça {veri['sira']}: terim/ad düzeltmesi...")
            try:
                self._duzelt_parca(veri)
            except Exception as e:
                self.log(f"  ! parça {veri['sira']} düzeltme atlandı: {llm.hata_metni(e)}")
        # 2) ozet
        self.log(f"  parça {veri['sira']}: özetleniyor...")
        try:
            onceki = self._onceki_baglam(veri["sira"])
            with self._iz_kilit:
                metin = blok_metni(list(veri["satirlar"]))
            if metin.strip():
                ozet = llm.bolum_ozetle(metin, veri["sira"], self.baglam(metin), onceki)
            else:                                  # yalniz supheli (halusinasyon) satirlar: LLM'e gidecek metin yok
                ozet = llm.normalize_ozet({})
            for a in (ozet or {}).get("aksiyonlar", []):
                a["tarih"] = tarih_mod.tarih_coz(a.get("tarih"), self.tarih)
            with self._iz_kilit:
                eski_nesil = self._parca_nesil.get(sira, 0) != nesil
            if eski_nesil:
                self.log(f"  parça {veri['sira']}: özetlenirken satırlar değişti, yeniden özetlenecek")
            else:
                veri["ozet"] = ozet
        except Exception as e:
            self.log(f"✖ parça {veri['sira']} özetlenirken hata: {llm.hata_metni(e)}")
            veri["ozet"] = None
        with self._iz_kilit:
            self.parca_yaz(veri)
        self._olay("parca_ozetlendi", {"sira": veri["sira"], "ozet": veri["ozet"],
                                       "duzeltme": len(veri["duzeltmeler"])})
        return veri["ozet"]

    def _duzelt_parca(self, veri):
        """Parcanin satirlarinda terim/ad duzeltmesi. LLM yalniz ONERIR; her oneri duzelt.kapi'dan (deterministik, VE
        kurali) gecmek zorundadir. Girdi satirin 'raw'i (Whisper ciktisi): tekrar calistirmak idempotenttir. 'raw'i
        olmayan (v2 oncesi) ve supheli satirlara dokunulmaz. Kabul edilen terim|ad duzeltmeleri terim havuzuna gozlem
        olarak yazilir (ogrenme toplanti sonunda, bagimsiz kanit sartiyla)."""
        sira = veri["sira"]
        with self._iz_kilit:
            girdi = [{"id": s["id"], "raw": s["raw"]} for s in veri["satirlar"]
                     if isinstance(s.get("raw"), str) and s["raw"].strip() and nota_girer(s)]
        if not girdi:
            return
        metin = " ".join(g["raw"] for g in girdi)
        adaylar = self.terimler.adaylar(self.seri, metin, self.katilimcilar)
        adlar = [a for a in self.katilimcilar if isinstance(a, str) and a.strip()]
        # ogrenilmis / elle varyantlar kapida izinli 'eski -> terim' eslemesidir (kapi sayi / ek / kelime sayisi
        # kurallarini yine uygular); katilimci adlari terim sayilmaz
        varyantlar = self.terimler.varyant_ciftleri(self.seri, adlar)
        ek = {"varyantlar": varyantlar} if varyantlar else {}           # bos havuzda eski cagri imzasi (test / replay)
        yeni, rapor = duzelt_mod.duzelt(girdi, adaylar, adlar, sor_fn=self.duzelt_sor_fn, **ek)
        if rapor.get("uyari") and not getattr(self, "_duzelt_uyarildi", False):
            self._duzelt_uyarildi = True                # bir kez: liste yok, kapi yalniz harf/kesme farki kabul eder
            self.log(f"  ! düzeltme: {rapor['uyari']}")
        if rapor.get("hata"):
            self.log(f"  ! parça {sira} düzeltme: {rapor['hata']}")
            if not rapor["kabul"]:
                return                              # model hatasi: mevcut metin ve onceki duzeltmeler korunur
        harita = {str(y["id"]): y for y in yeni}
        cift, degisen = [], []
        with self._iz_kilit:
            for s in veri["satirlar"]:
                y = harita.get(str(s["id"]))
                if y is None:
                    continue
                onceki = (s.get("text"), s.get("duzeltmeler"), s.get("belirsiz"))
                s["text"] = y["text"]
                s["duzeltmeler"] = [dict(d) for d in (y.get("duzeltmeler") or [])]
                s["belirsiz"] = list(y.get("belirsiz") or [])
                cift += [[d["eski"], d["yeni"]] for d in s["duzeltmeler"]]
                if onceki != (s["text"], s["duzeltmeler"], s["belirsiz"]):
                    degisen.append(s)
        for s in degisen:                            # kurtarma icin altyazi.jsonl'e guncelleme kaydi
            self._jsonl({"id": s["id"], "guncelle": True, "text": s["text"], "duzeltmeler": s["duzeltmeler"],
                         "belirsiz": s["belirsiz"]})
        veri["duzeltmeler"] = cift
        for sid, kayit in rapor["satirlar"].items():
            for d in kayit["kabul"]:
                if d.get("neden") in ("terim", "ad"):
                    self.terimler.gozlem_ekle(self.seri, self.toplanti_id, d["eski"], d["yeni"], d["neden"])
        self._duzelt_gunlugu(sira, rapor)
        self.log(f"  parça {sira}: {rapor['oneri']} öneri, {rapor['kabul']} kabul, {rapor['red']} red, "
                 f"{rapor['belirsiz']} belirsiz kelime")
        try:
            self.terimler.kaydet()                   # gozlemler cokme durumunda kaybolmasin (atomik, degisiklik yoksa yazmaz)
        except OSError as e:
            self.log(f"  ! terim havuzu yazılamadı: {e!r}")

    def _duzelt_gunlugu(self, sira, rapor):
        """Kabul/red kaydi (kelime duzeyinde, satir metni yok) -> duzelt_log.jsonl. Red nedenleri kapi ayari icindir."""
        kayit = {"parca": sira, "oneri": rapor["oneri"], "kabul": [], "red": rapor["gecersiz"][:20]}
        for sid, k in rapor["satirlar"].items():
            kayit["kabul"] += [dict(d, satir=sid) for d in k["kabul"]]
            kayit["red"] += [dict(d, satir=sid) for d in k["red"]]
        try:
            with self._gunluk_kilit:
                with open(os.path.join(self.klasor, "duzelt_log.jsonl"), "a", encoding="utf-8") as f:
                    f.write(json.dumps(kayit, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _onceki_konular(self, sira):
        if sira <= 1 or not os.path.exists(self.parca_yolu(sira - 1)):
            return None
        return (self.parca_oku(sira - 1).get("ozet") or {}).get("konular") or None

    def _onceki_baglam(self, sira):
        """Parca ortusmesi (MIMARI_v3 §6): onceki parcanin konulari + SON 6 satiri + cevapsiz kalan maddeleri
        (~400 token). llm.bolum_ozetle bunu yalniz baglam olarak verir; sinirdaki karar / cumle iki parcaya bolunup
        kaybolmasin. Onceki parca yoksa ya da okunamazsa None (eski davranis: yalniz konular)."""
        if sira <= 1 or not os.path.exists(self.parca_yolu(sira - 1)):
            return None
        try:
            veri = self.parca_oku(sira - 1)
        except Exception:
            return self._onceki_konular(sira)
        ozet = veri.get("ozet") or {}
        satirlar = []
        for s in [x for x in veri.get("satirlar") or [] if nota_girer(x)][-llm.ONCEKI_SATIR:]:
            satirlar.append(blok_metni([s]))
        bag = llm.onceki_baglam_hazirla(ozet.get("konular"), [x for x in satirlar if x.strip()], ozet.get("acik_sorular"))
        return bag if (bag["konular"] or bag["satirlar"] or bag["acik"]) else None

    @staticmethod
    def _baglam_satir(veri, parca):
        p = normalize(parca)
        for s in veri["satirlar"]:
            if p and p in normalize(s["text"]):
                return s
        return None

    # ---------- terimler (MIMARI_v3 §5) ----------
    @staticmethod
    def _davet_hazirla(davet):
        """{"govde", "ekler"} -> govde Teams katilim metni / imza / URL / e-posta / telefon temizlenmis (meta.json'a
        yalniz temiz metin yazilir; terim kaniti icin yeterli)."""
        if not isinstance(davet, dict):
            return {}
        return {"govde": terim_mod.davet_temizle(str(davet.get("govde") or "")),
                "ekler": [str(x) for x in davet.get("ekler") or [] if str(x).strip()]}

    def davet_ayarla(self, davet):
        """outlook.aktif_toplanti()['davet'] ({"govde", "ekler"}); meta.json'a yazilir (Motor.yukle ile devam)."""
        h = self._davet_hazirla(davet)
        if h.get("govde") or h.get("ekler"):
            self.davet = h
            self._terimler_hazir = False
            self.meta_yaz("devam")

    def _onceki_notlari_oku(self, en_fazla=3):
        """Ayni serinin (slug) onceki toplantilarinin not.md metinleri, en yeniden eskiye."""
        kok = os.path.dirname(os.path.abspath(self.klasor))
        bulunan = []
        try:
            adlar = os.listdir(kok)
        except OSError:
            return []
        benim = os.path.abspath(self.klasor)
        for ad in adlar:
            k = os.path.join(kok, ad)
            if os.path.abspath(k) == benim:
                continue
            try:
                with open(os.path.join(k, "meta.json"), encoding="utf-8") as f:
                    meta = json.load(f)
                if slug(meta.get("baslik") or "") != self.seri or str(meta.get("tarih") or "") > self.tarih.isoformat():
                    continue
                with open(os.path.join(k, "not.md"), encoding="utf-8") as f:
                    bulunan.append((str(meta.get("tarih") or ""), ad, f.read()))
            except (OSError, ValueError):
                continue
        bulunan.sort(reverse=True)
        return [m for _, _, m in bulunan[:en_fazla]]

    def terimleri_hazirla(self):
        """Toplanti basinda (isler/toplanti) ve bitiste: davet metni + onceki notlardan terimleri bu serinin havuzuna
        ekler ve kaydeder (Whisper prompt'u ve duzeltme beyaz listesi bunlari kullanir). Tekrar cagrilar etkisizdir.
        Dondurur: yeni eklenen terim sayisi."""
        if self._terimler_hazir:
            return 0
        self._terimler_hazir = True
        yeni = 0
        try:
            govde = self.davet.get("govde") or self.gundem or ""
            adlar = [a for a in self.katilimcilar if isinstance(a, str)]
            yeni += len(self.terimler.ekle(self.seri, terim_mod.davetten_cikar(self.baslik, govde,
                                                                              self.davet.get("ekler") or ()), "davet",
                                           adlar=adlar))
            self._onceki_notlar = self._onceki_notlari_oku()
            for not_md in self._onceki_notlar:
                yeni += len(self.terimler.ekle(self.seri, terim_mod.notlardan_cikar(not_md), "not", adlar=adlar))
            self.terimler.kaydet()
        except Exception as e:                       # terim kaynaklari isteye bagli: toplanti bunun yuzunden aksamaz
            self.log(f"! davet/önceki not terimleri alınamadı: {e!r}")
        if yeni:
            self.log(f"  · {yeni} yeni terim havuza eklendi (davet / önceki not)")
        return yeni

    def _ogren(self):
        """Toplanti sonu: katilimci adlari havuza, ardindan bagimsiz kanit kuralina uyan duzeltmeler varyant olur
        (terim.TerimHavuzu.ogren). Kanit: davet / onceki not / katilimci adi -- duzeltilmis transkript kanit DEGILDIR."""
        try:
            self.terimleri_hazirla()
            self.terimler.ekle(self.seri, [a for a in self.katilimcilar if isinstance(a, str)], "kisi")
            kanit = {"davet": [self.baslik, self.gundem, self.davet.get("govde") or "", self.davet.get("ekler") or []],
                     "onceki_not": list(self._onceki_notlar), "katilimci": list(self.katilimcilar)}
            for eski, t in self.terimler.ogren(self.seri, self.toplanti_id, kanit):
                self.log(f"  · öğrenildi: '{eski}' → {t}")
            self.terimler.kaydet()
        except Exception as e:
            self.log(f"! terim öğrenmesi atlandı: {e!r}")

    # ---------- belirsizler (salt okunur) ----------
    def belirsizler(self):
        """Arayuzdeki salt-okunur 'Belirsizler' paneli ve toplanti_bitti sinyali. Onay akisi YOK. Her kayit
        {"tur": "kelime"|"konusmaci"|"supheli"|"terim", "metin", "ts" (kaynak baglantisi: 'HH:MM:SS'), "satir" (id),
        "parca" (sira | None: acik parca), "baglam"}. kelime: duzelt kapisinin 'yazim' diye isaretlediği; konusmaci:
        Ad? / Bilinmeyen / A → B / A / B; supheli: halusinasyon supheli (nota girmez); terim: ozetleyicinin bozuk
        duyuldu dedigi terimler."""
        cikti = []
        parcalar = []
        for no in range(1, self.parca_no + 1):
            try:
                parcalar.append((no, self.parca_oku(no)))
            except Exception:
                continue
        with self._iz_kilit:
            parcalar.append((None, {"satirlar": list(self.mevcut), "ozet": None}))
        eski_say, eski_ilk = 0, None
        for no, veri in parcalar:
            son_konusmaci = None                       # ardisik ayni belirsiz konusmaci tek kayit (yuzlerce satir olmasin)
            for s in veri.get("satirlar") or []:
                baglam = f"{gorunen_ad(s)}: {s.get('text', '')}"[:200]
                kayit = {"ts": s.get("ts"), "satir": s.get("id"), "parca": no, "baglam": baglam}
                if s.get("supheli") and s.get("kaynak") != "altyazi-yedek":
                    cikti.append(dict(kayit, tur="supheli", metin=(s.get("text") or "")[:80]))
                    son_konusmaci = None
                    continue
                for kelime_ in s.get("belirsiz") or []:
                    cikti.append(dict(kayit, tur="kelime", metin=kelime_))
                kim = gorunen_ad(s)
                if s.get("durum") == "eski":           # v2 oncesi kayit: tum satirlar 'Ad?' (goc); tek ozet kaydi
                    eski_say += 1
                    eski_ilk = eski_ilk or kayit
                    continue
                belirsiz_kim = s.get("durum") != "beklemede" and (
                    kim.endswith("?") or kim == "Bilinmeyen" or " → " in kim or "(aynı anda)" in kim)
                if not belirsiz_kim:
                    son_konusmaci = None
                elif son_konusmaci is not None and son_konusmaci["metin"] == kim:
                    son_konusmaci["satir_sayisi"] += 1
                else:
                    son_konusmaci = dict(kayit, tur="konusmaci", metin=kim, satir_sayisi=1)
                    cikti.append(son_konusmaci)
            for t in (veri.get("ozet") or {}).get("belirsiz_terimler") or []:
                s = self._baglam_satir(veri, t)
                cikti.append({"tur": "terim", "metin": t, "ts": (s or {}).get("ts"), "satir": (s or {}).get("id"),
                              "parca": no, "baglam": f"{gorunen_ad(s)}: {s.get('text', '')}"[:200] if s else ""})
        if eski_say:
            cikti.append(dict(eski_ilk, tur="konusmaci", metin="Ad?", satir_sayisi=eski_say,
                              baglam=f"Eski kayıt: {eski_say} satırın konuşmacısı zamandan doğrulanamaz (Ad? gösterilir)"))
        return cikti

    # ---------- eksik / yeniden ----------
    def eksikleri_isle(self):
        """Ozeti olmayan parcalari (cokme, hata) isler. Islenen parca sayisini dondurur."""
        eksik = [v for v in (self.parca_oku(n) for n in range(1, self.parca_no + 1)) if v.get("ozet") is None]
        if not eksik:
            return 0
        self.log(f"{len(eksik)} parça işlenmemiş, işleniyor ({paralel()} paralel)...")
        with cf.ThreadPoolExecutor(max_workers=paralel()) as ex:
            list(ex.map(lambda v: self._isle(v, self.duzelt), eksik))
        return len(eksik)

    def parca_yeniden(self, no):
        """Bir parcayi sifirdan (duzeltme + ozet) yeniden isler."""
        veri = self.parca_oku(no)
        veri["ozet"] = None
        self._isle(veri, self.duzelt)
        return self.parca_oku(no)

    # ---------- bitis ----------
    def bitir(self):
        """Bekleyen konusmaci kararlarini zorlar (yeniden kesme yok: 'A → B'), bekleyen kesimleri eldekiyle
        tamamlar, son parcayi kapatir, arka plan islerini bekler, terim ogrenmesini yapar; salt-okunur belirsizler()
        listesini dondurur (onay akisi yok)."""
        with self._iz_kilit:
            self._bitiyor = True
            self._kararlari_isle(self.konusmaci.bitir())
            for ust in list(self._kesimler):
                self._kesim_tamamla(ust)
            for s in list(self.mevcut):
                if self._bekliyor_mu(s):
                    self._zaman_asimi(s)
            self.parca_kapat("bitiş", zorla=True)
            self._ozetleri_yenile(hepsi=True)
            self._cizelge_yaz()
        bekleyen = [i for i in self.isler if not i.done()]
        if bekleyen:
            self.log(f"{len(bekleyen)} parça işlemi bekleniyor...")
        cf.wait(self.isler)
        self._ogren()
        self.meta_yaz("bitti", stt_eksik=self.stt_eksik())
        try:
            return self.belirsizler()
        except Exception as e:                      # liste yalniz bilgi icin: bitis yolu bunun yuzunden kirilmaz
            self.log(f"! belirsizler listesi çıkarılamadı: {e!r}")
            return []

    def not_yedekle(self):
        """Var olan not.md'yi not.md.yedek-YYYYMMDD-HHMM olarak kopyalar (yeniden ozetleme eskisini ezmesin).
        Dondurur: yedek yolu ya da None."""
        yol = os.path.join(self.klasor, "not.md")
        if not os.path.exists(yol):
            return None
        hedef = yol + dt.datetime.now().strftime(".yedek-%Y%m%d-%H%M")
        n = 2
        while os.path.exists(hedef):
            hedef = yol + dt.datetime.now().strftime(".yedek-%Y%m%d-%H%M") + f"-{n}"
            n += 1
        with open(yol, encoding="utf-8") as f, open(hedef, "w", encoding="utf-8") as g:
            g.write(f.read())
        return hedef

    def yeniden_ozetle(self):
        """Gecmis -> 'Yeniden ozetle': TUM parcalar sifirdan (duzeltme + ozet) PARALEL islenir. Duzeltme yalniz 'raw'i
        olan satirlarda calisir (v2 oncesi kayitlarin metnine dokunulmaz); terim havuzu (sonradan buyumus olabilir)
        yeni adaylari verir. Dondurur: islenen parca sayisi."""
        self.terimleri_hazirla()
        toplam = self.parca_no
        if not toplam:
            return 0
        self.log(f"{toplam} parça yeniden özetleniyor ({paralel()} paralel)...")

        def isle(no):
            self.adim(f"Parça {no}/{toplam} yeniden özetleniyor", no, toplam)
            try:
                veri = self.parca_oku(no)
            except Exception as e:
                self.log(f"✖ parça {no} okunamadı: {e!r}")
                return
            veri["ozet"] = None
            self._isle(veri, self.duzelt)

        with cf.ThreadPoolExecutor(max_workers=paralel()) as ex:
            list(ex.map(isle, range(1, toplam + 1)))
        return toplam

    def notu_uret(self):
        bolumler, eksik = [], []
        for no in range(1, self.parca_no + 1):
            veri = self.parca_oku(no)
            if veri.get("ozet") is None:
                self.adim(f"Parça {no}/{self.parca_no} özetleniyor", no, self.parca_no)
                self.log(f"  parça {no} özetleniyor...")
                self._isle(veri, False)
                veri = self.parca_oku(no)
            aralik = f"{veri.get('baslangic', '')}–{veri.get('bitis', '')}"
            if veri.get("ozet"):
                b = dict(veri["ozet"])
                b["aralik"] = aralik
                bolumler.append(b)
            else:
                eksik.append(aralik)
                self.log(f"  ! parça {no} notta yer almayacak ({aralik})")
        self.log("bölümler birleştiriliyor...")
        transkript = self.transkript_metni()
        satirlar = [s for s in self.tum_satirlar() if nota_girer(s)]      # supheli (halusinasyon) satir kaynak/sahip olmaz
        kaynak_fn = kaynak_bulucu(satirlar)
        asamalar = {"Kararlar ve aksiyonlar birleştiriliyor": 1, "Özet paragrafı yazılıyor": 2}
        not_md = f"# {self.baslik} — {self.tarih.isoformat()}\n\n" + llm.birlestir(
            bolumler, self.baglam(transkript), self.log, kaynak_fn=kaynak_fn, sablon=self.sablon,
            katilimcilar=self.katilimcilar,
            aksiyon_fn=lambda aks: self._aksiyonlari_kesinlestir(aks, satirlar),
            adim_fn=lambda m: self.adim(m, asamalar.get(m), 4))
        self.adim("Dayanak kontrolü", 3, 4)
        bolum_ozetleri = "\n".join(b.get("ozet", "") for b in bolumler)
        not_md, atilan = dayanak_kontrolu(not_md, transkript, ozet_kaynak=bolum_ozetleri)
        for madde in atilan:
            self.log(f"  ! transkriptte dayanağı yok, nottan çıkarıldı: {madde[:90]}")
        if eksik:
            # sessizce eksik not yerine okuyana hangi araligin notta olmadigini soyle
            not_md = not_md.rstrip("\n") + "\n\n## Eksik bölümler\n" + "\n".join(
                f"- {a} arası özetlenemedi; ayrıntı için transkripte bakın." for a in eksik) + "\n"
        stt_eksik = self.stt_eksik()
        if stt_eksik:
            # STT kesintisi: bu araliklarin transkripti yok (altyazi metni yerine konmaz)
            not_md = not_md.rstrip("\n") + "\n\n## Eksik transkript\n" + "\n".join(
                f"- {stt_eksik_metni([a])}" for a in stt_eksik) + "\n"
        if any(s.get("kaynak") == "altyazi-yedek" for s in satirlar):
            not_md = not_md.rstrip("\n") + "\n\n_Bazı bölümler Teams altyazısından alındı (altyazıdan, düşük güven)._\n"
        self.adim("Not kaydediliyor", 4, 4)
        with open(os.path.join(self.klasor, "not.md"), "w", encoding="utf-8") as f:
            f.write(not_md)
        import not_araclari                  # gec: not_araclari motor'u ice aktarir
        # N7: konusma paylari nota yazilmaz; Gecmis detayinda gosterilir
        self.meta_yaz("tamam", konusma_paylari=[list(p) for p in not_araclari.konusma_paylari(satirlar)],
                      konusma_belirsiz=not_araclari.konusma_belirsiz_payi(satirlar))
        return not_md

    def _kisi_katilimcilari(self):
        """Adlandirilmis sahip icin 'kisi' katilimcilari (oda/genel/hayalet adlari ve '?' girmez)."""
        kimlik = getattr(self.konusmaci, "kimlik_turu", None)
        sonuc = []
        for ad in self.katilimcilar:
            if not isinstance(ad, str) or not ad.strip() or "?" in ad:
                continue
            try:
                if kimlik is not None and kimlik(ad) not in ("kisi", "ben"):
                    continue
            except Exception:
                pass
            sonuc.append(ad)
        return sonuc

    def _aksiyonlari_kesinlestir(self, aksiyonlar, satirlar):
        """birlestir sonrasi son islem: sahip dogrulama (kesin/belirsiz/yok), sonra tarih cozumu (MIMARI_v3 §6:
        tarih_coz birlestir SONRASI; model birlestirirken gun adi ile tarihi tutarsiz yazabilir)."""
        sonuc = sorumlu_dogrula(aksiyonlar, satirlar, self.ben, katilimcilar=self._kisi_katilimcilari())
        for a in sonuc:
            a["tarih"] = tarih_mod.tarih_coz(a.get("tarih"), self.tarih)
        return sonuc

    def tum_satirlar(self):
        """Tum parcalarin (duzeltilmis) satirlari + acik parca, kronolojik."""
        satirlar = []
        for no in range(1, self.parca_no + 1):
            try:
                satirlar += self.parca_oku(no)["satirlar"]
            except Exception:
                pass
        return satirlar + list(self.mevcut)

    def soru_sor(self, soru):
        """Toplantiya soru: transkript kucuk ortusen pencerelere bolunur, soruyla en ilgilileri hibrit
        aramayla (BM25 + embedding, RRF; rag.py) secilir, kurum ici LLM cevaplar. Embedding ayarli
        degilse ya da hata verirse yalnizca kelime aramasi kullanilir."""
        satirlar = []
        for no in range(1, self.parca_no + 1):
            try:
                satirlar.extend(self.parca_oku(no)["satirlar"])
            except Exception:
                continue
        satirlar.extend(self.mevcut)
        satirlar = [dict(s, gorunen=_konusan(s)) for s in satirlar if nota_girer(s)]    # gosterim kurali ('Ad?', 'Bilinmeyen'); cakisma ⟨⟩ (rag)
        pen = rag.pencereler(satirlar)
        if not pen:
            return "Bu toplantının transkripti boş."
        metinler = [p["metin"] for p in pen]
        sira, yontem = rag.hibrit_sirala(soru, metinler, self.klasor, olay=self.log)
        self.log(f"Soru: {len(pen)} pencere, arama: {yontem}")
        bolumler = [(f"{p['bas']}–{p['son']}", p["metin"]) for p in pen]
        return llm.toplantiya_sor(soru, bolumler, self.baglam(), sira=sira)

    def kapat(self):
        self.havuz.shutdown(wait=True)
        try:
            self.terimler.kaydet()
        except OSError:
            pass

    # ---------- diskten devam ----------
    @classmethod
    def yukle(cls, klasor, terimler=None, olay=None, duzelt=True, ben=None, sablon="genel"):
        with open(os.path.join(klasor, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        m = cls(klasor, meta["baslik"], dt.date.fromisoformat(meta["tarih"]), terimler,
                meta.get("katilimcilar"), meta.get("gundem", ""), duzelt=duzelt, olay=olay,
                kaynak=meta.get("kaynak", "altyazi"), ben=ben, sablon=sablon, davet=meta.get("davet"))
        m._goc = True                        # surumsuz (v2 oncesi) satirlar okunurken goc eder: 'Ad?', sahip yok
        m._yuklu_eksik = [list(a)[:2] for a in (meta.get("stt_eksik") or []) if len(a) >= 2]
        return m


def kaynak_satiri_bulucu(satirlar, esik=0.3):
    """Notun maddesini transkriptte en iyi karsilayan SATIRA baglar -> fn(metin, sorumlu) -> satir | None.
    Kelime kokleri ortakligi olculur; sorumlu verilirse onun kendi satiri hafifce one alinir
    ('ben yaparim' diyen kisi)."""
    dizin = [(s, _kokler(s.get("text", ""))) for s in satirlar if s.get("text")]

    def bul(metin, sorumlu=None):
        mk = _kokler(metin) - {"ve", "ile", "icin", "olan", "olarak", "yapil", "edil"}
        if not mk or not dizin:
            return None
        en_iyi, en_puan = None, 0.0
        for s, kk in dizin:
            ham = len(mk & kk) / len(mk)
            if ham < esik:
                continue                     # sorumlu bonusu esigin ALTINDAKI satiri kaynak yapmaz (eskiden 0.25 + 0.1 gecerdi)
            puan = ham
            kim = s.get("speaker", "")
            if sorumlu and kim and normalize(sorumlu).split()[:1] == normalize(kim).split()[:1]:
                puan += 0.1
            if puan > en_puan:
                en_iyi, en_puan = s, puan
        return en_iyi
    return bul


def kaynak_bulucu(satirlar, esik=0.3):
    """Notun her maddesini transkriptte en iyi karsilayan satira baglar -> fn(metin, sorumlu) -> 'HH:MM:SS'."""
    bul = kaynak_satiri_bulucu(satirlar, esik)

    def ts(metin, sorumlu=None):
        s = bul(metin, sorumlu)
        return s["ts"] if s else None
    return ts


# ---------- sahip (K8, MIMARI_v3 §6): kanit kaliplari ----------
# Is fiili kokleri: konusan bunlarla kendine (1. tekil), muhataba (2. kisi) ya da adi gecen kisiye (3. kisi) IS yukler.
# 'bekle-', 'rica et-', 'oner-', 'sor-', 'soyle-', 'bul-' (gorus) is fiili DEGILDIR: 'Raporu Ayse'den bekliyorum' konusani
# sahip yapmaz. 'X et' bilesikleri yalniz _ET_ISIMLERI ile ('kontrol ederim'; 'rica ederim', 'tesekkur ederim' degil).
_IS_KOKLERI = ("hazırla", "gönder", "yolla", "ilet", "bak", "yap", "hallet", "incele", "güncelle", "yaz", "düzelt",
               "düzenle", "paylaş", "topla", "tamamla", "bitir", "çöz", "araştır", "hesapla", "oluştur", "derle", "aktar",
               "yükle", "sun", "ara", "üstlen", "getir", "ilgilen", "dene", "anlat", "çıkar", "et")
_ET_ISIMLERI = {"kontrol", "takip", "test", "teslim", "organize", "analiz", "revize", "koordine", "rapor", "tespit",
                "talep", "teyit", "kayıt", "takibini", "kontrolünü", "testini"}
_UNLU = "aeıioöuü"


def _fiil_kaliplari(kok):
    """Bir is fiili kokunun kip/kisi kaliplari -> {etiket: derlenmis regex}. '1': 1. tekil (gelecek / genis / simdiki /
    yeterlilik), '3': 3. kisi (gelecek / emir / genis), '2': muhatap (yalin emir, -In(Iz), -AcAksIn(Iz)) -- hepsi tam
    kelime; 'olumsuz' (-mA-, -mIyor) ve 'gecmis' (-DI, -mIs) kelime basi."""
    govde = [kok] + ([kok[:-1] + "d"] if kok.endswith("et") else [])             # et -> ed (yumusama)
    g = "(?:" + "|".join(re.escape(x) for x in govde) + ")"
    if kok[-1] in _UNLU:
        bir = (g + r"(?:y[ae]c[ae]ğ[ıi]m|r[ıi]m|y[ae]bil(?:ir[ıi]m|[ae]c[ae]ğ[ıi]m))|"
               + re.escape(kok[:-1]) + r"[ıiuü]yorum")
        uc = g + r"(?:y[ae]c[ae]k(?:t[ıi]r|l[ae]r)?|s[ıi]n(?:l[ae]r)?|r)"
        iki = g + r"(?:y[ıiuü]n(?:[ıiuü]z)?|y[ae]c[ae]ks[ıi]n(?:[ıi]z)?)?"
    else:
        bir = g + r"(?:[ae]c[ae]ğ[ıi]m|[aeıiuü]r[ıiuü]m|[ıiuü]yorum|[ae]bil(?:ir[ıi]m|[ae]c[ae]ğ[ıi]m))"
        uc = g + r"(?:[ae]c[ae]k(?:t[ıi]r|l[ae]r)?|s[ıi]n(?:l[ae]r)?|[aeıiuü]r)"
        iki = g + r"(?:[ıiuü]n(?:[ıiuü]z)?|[ae]c[ae]ks[ıi]n(?:[ıi]z)?)?"
    return {"1": re.compile(bir), "3": re.compile(uc), "2": re.compile(iki),
            "olumsuz": re.compile(g + r"m(?:[ae](?:y[ae]c[ae]|y[ıi]n|z$|z[ıi]|s[ıi]n$|s[ıi]nl[ae]r$|d[ıi]|m[ıi]ş|$)"
                                  r"|[ıiuü]yor)"),
            "gecmis": re.compile(g + r"(?:[dt][ıiuü]|m[ıiuü]ş)")}


_FIILLER = [(k, _fiil_kaliplari(k)) for k in _IS_KOKLERI]
_CIPLAK_BELIRSIZ = {"ara", "bak", "yaz", "sun", "topla", "yükle", "çıkar"}   # yalin hali isim / unlem de olur ('bu ara', 'bak')
# Birinci tekil kip eki (genel): 'ben' ile birlikte is fiili listesinde olmayan fiil de taahhut sayilir ('Ben katilirim').
_TAAHHUT = re.compile(r"\w{2,}[ae]c[ae]ğ[ıi]m|\w{2,}[ıiuü]yorum|\w{3,}(?:[ae]r[ıi]m|[ıi]r[ıi]m|[uü]r[uü]m)")
_TAAHHUT_DEGIL = re.compile(r"m[ıiuü]yor|m[ae]y[ae]c[ae][ğk]")
_TAAHHUT_BILGI = {"biliyorum", "bilirim", "düşünüyorum", "düşünürüm", "sanıyorum", "sanırım", "görüyorum", "görürüm",
                  "anlıyorum", "anlarım", "duyuyorum", "hatırlıyorum", "katılıyorum", "diyorum", "inanıyorum",
                  "umuyorum", "seviyorum", "istiyorum", "isterim", "zannediyorum", "hissediyorum", "hazırım",
                  "kararım", "başarım", "yarım", "sevinirim"}
_IS_DEGIL_KOK = ("bekl", "rica", "öner", "sor", "söyl", "bul", "dile", "tavsiye")      # bekliyorum, oneriyorum, soruyorum...
_IYELIK = re.compile(r"\w+l[ae]r[ıi]m(?:[ıi]z)?")                                  # dosyalarim, notlarimiz: fiil degil
_IKINCI_KISI = {"sen", "siz", "sana", "size", "seni", "sizi", "senin", "sizin", "senden", "sizden", "sende", "sizde"}
_RICA = re.compile(r"\w+m[ae]n(?:[ıi]z)?[ıi]\s+(?:rica|istiyorum|isterim|bekliyorum|bekleriz|istiyoruz)")
_SORU = re.compile(r"\?|(?<!\w)(?:m[ıiuü](?:s[ıiuü]n(?:[ıiuü]z)?|y[ıiuü]m|y[ıiuü]z|d[ıiuü]r)?|kim|kime|kimin|kimi"
                   r"|hangi|hangisi|nasıl|acaba)(?!\w)")
_OLUMSUZ = re.compile(r"(?<!\w)(?:değil|değiliz|değilim|hayır)(?!\w)")
_BENIM = ("ben", "Ben")


def _kelimeler(metin):
    return re.findall(r"\w+", tr_kucuk(sarmal_sil(metin)))


def _fiil_etiketleri(metin):
    """Metindeki (⟨⟩ disi) is fiili bicimleri -> etiket kumesi {'1', '2', '3', 'olumsuz', 'gecmis'}. 'X et' bilesigi
    yalniz is ismiyle sayilir."""
    kel = _kelimeler(metin)
    etiket = set()
    for i, k in enumerate(kel):
        for kok, kalip in _FIILLER:
            if not k.startswith(kok[:-1] if kok[-1] in _UNLU else kok[:2]):
                continue
            if kok == "et" and not (i and kel[i - 1] in _ET_ISIMLERI):
                continue
            for ad in ("1", "2", "3"):
                if kalip[ad].fullmatch(k):
                    if ad == "2" and k == kok and kok in _CIPLAK_BELIRSIZ and not set(kel) & {"sen", "siz"}:
                        continue
                    etiket.add(ad)
            for ad in ("olumsuz", "gecmis"):
                if kalip[ad].match(k):
                    etiket.add(ad)
    return etiket


def _soru_olumsuz_gecmis(metin):
    """Satir soru ('X mi yapacak?', 'kim?'), olumsuz ('incelemeyecek', 'degil') ya da gecmis ('inceledi', 'gondermistin')
    mi? Boyle bir satir sahip KANITI olamaz (yalniz aday)."""
    m = tr_kucuk(sarmal_sil(metin))
    if _SORU.search(m) or _OLUMSUZ.search(m) or any(_TAAHHUT_DEGIL.search(k) for k in _kelimeler(metin)):
        return True
    return bool(_fiil_etiketleri(metin) & {"olumsuz", "gecmis"})


def _konusan_taahhudu(metin):
    """Konusan ISI KENDINE aliyor mu (adlardan bagimsiz): 'ben' + 1. tekil fiil (bilgi / bekle / rica / oneri / iyelik
    bicimleri haric) ya da is fiili kokunun 1. tekil bicimi ('hazirlarim', 'gonderecegim', 'bakiyorum')."""
    if "1" in _fiil_etiketleri(metin):
        return True
    kel = _kelimeler(metin)
    if "ben" not in kel:
        return False
    for i, k in enumerate(kel):
        if k in _TAAHHUT_BILGI or _TAAHHUT_DEGIL.search(k) or k.startswith(_IS_DEGIL_KOK) or _IYELIK.fullmatch(k):
            continue
        if i and kel[i - 1] in _IS_DEGIL_KOK + ("teşekkür", "tebrik", "rica"):
            continue                                     # 'rica ediyorum', 'tesekkur ederim'
        if _TAAHHUT.fullmatch(k):
            return True
    return False


def _ikinci_kisi_var(metin):
    """Satir muhataba is veriyor ya da ona hitap ediyor mu ('sen', 'siz', yalin emir 'hazirla', 'gonderin',
    'gondermeni rica ediyorum')?"""
    if set(_kelimeler(metin)) & _IKINCI_KISI or _RICA.search(tr_kucuk(sarmal_sil(metin))):
        return True
    return "2" in _fiil_etiketleri(metin)


def birinci_tekil_taahhut(metin):
    """Metin (⟨⟩ disinda) konusanin kendi taahhudu mu? ('Raporu ben hazirlarim', 'yarin gonderecegim'.) Soru / olumsuz /
    gecmis ve muhataba is veren satir ('Zeynep, gondermeni rica ediyorum') taahhut degildir."""
    return (_konusan_taahhudu(metin) and not _soru_olumsuz_gecmis(metin) and not _ikinci_kisi_var(metin))


def _ayni_kisi(a, b):
    """'Deniz' ~ 'Deniz Can Ozturk': ilk ad ayni mi (yalniz on eleme; kimlik karari _ayni_kisi_mi)."""
    a, b = normalize(a or "").split()[:1], normalize(b or "").split()[:1]
    return bool(a) and a == b


def _ilk_ad_ortak(ad, kisiler):
    """Adin ilk adi birden fazla (farkli tam adli) katilimcida geciyor mu? ('Ali' -> Ali Demir, Ali Kaya)."""
    ilk = normalize(ad or "").split()[:1]
    tamlar = {normalize(k) for k in kisiler if normalize(k).split()[:1] == ilk and len(normalize(k).split()) > 1}
    return len(tamlar) > 1


def _ayni_kisi_mi(a, b, kisiler):
    """a ile b ayni katilimci mi? Normalize tam ad ayni; ya da biri yalniz ilk ad ve o ilk ad katilimcilarda tekil.
    Iki farkli tam ad ('Ali Demir' / 'Ali Kaya') ayni kisi DEGILDIR."""
    na, nb = normalize(a or ""), normalize(b or "")
    if not na or not nb:
        return False
    if na == nb:
        return True
    if not _ayni_kisi(a, b) or (len(na.split()) > 1 and len(nb.split()) > 1):
        return False
    return not _ilk_ad_ortak(a, kisiler)


def _ad_parcala(sorumlu):
    """Modelin sorumlu alani -> [(ad, isaretli)]; 'belirsiz', '-' atilir. isaretli: model adi '(?)' ile yazmis (kod bunu
    kesin YAPMAZ)."""
    sonuc = []
    for x in re.split(r",|/| ve ", sorumlu or ""):
        isaretli = "(?)" in x
        x = re.sub(r"\(\?\)", " ", x).strip()
        if x and x.lower() not in ("-", "belirsiz", "?"):
            sonuc.append((x, isaretli))
    return sonuc


def _kisi_listesi(satirlar, katilimcilar, ben):
    """Adlandirilmis sahip icin 'kisi' katilimcilari: verilen liste + kesin konusmacilar + ben (oda/genel/hayalet,
    '?' ve birlesik adlar girmez)."""
    adlar = []

    def ekle(ad):
        ad = str(ad or "").strip()
        if ad and ad.lower() != "ben" and "?" not in ad and normalize(ad) not in [normalize(x) for x in adlar]:
            adlar.append(ad)
    for ad in katilimcilar or []:
        ekle(ad)
    for s in satirlar:
        if kesin_ad_mi(s) and not s.get("cakisma"):
            ekle(s.get("speaker"))
    if ben:
        ekle(ben)
    return adlar


def _kisi_esle(ad, kisiler):
    """Modelin yazdigi ad -> tek bir katilimci adi (tam ad birebir ya da ilk ad eslesmesi; ilk ad birden fazla
    katilimcida ortaksa belirsiz -> None). Tam adli katilimci, ayni kisinin kisa (ilk ad) kaydina tercih edilir."""
    n = normalize(ad)
    tam = [k for k in kisiler if normalize(k) == n]
    if len(tam) == 1 and len(n.split()) > 1:
        return tam[0]
    if len(n.split()) > 1:
        return None
    if _ilk_ad_ortak(ad, kisiler):
        return None
    aday = [k for k in kisiler if _ayni_kisi(ad, k)]
    aday.sort(key=lambda k: -len(k.split()))
    return aday[0] if aday else None


def _adi_geciyor(kisi, metin, kisiler=()):
    """Katilimcinin adi (tam ya da -- tekilse -- ilk ad) metinde BIREBIR kelime olarak geciyor mu? 'Ayse' ~ 'Aysen' degil;
    ilk ad baska bir katilimciyla ortaksa yalniz tam ad sayilir."""
    if ad_geciyor(kisi, metin):
        return True
    return not _ilk_ad_ortak(kisi, kisiler) and ad_geciyor(kisi.split()[0], metin)


def _ad_gecisleri(kisi, metin, kisiler):
    """Adin metindeki (⟨⟩ disi) gecisleri -> ['hitap' | 'ozne' | 'hal'] (hal: 'Ayse'den', 'Mehmet'in' -- is sahibi degil)."""
    m = tr_kucuk(sarmal_sil(metin))
    adlar = [tr_kucuk(kisi)] + ([] if _ilk_ad_ortak(kisi, kisiler) else [tr_kucuk(kisi.split()[0])])
    sonuc = []
    for ad in {a for a in adlar if len(a) >= 3}:
        for g in re.finditer(r"(?<!\w)" + re.escape(ad) + r"(?!\w)(['’]\w+)?(\s*[,:]|\s+(?:sen|siz)(?!\w))?", m):
            if g.group(1):
                sonuc.append("hal")
            elif g.group(2):
                sonuc.append("hitap")
            else:
                sonuc.append("ozne")
    return sonuc


def _is_atanan(kisi, metin, kisiler):
    """Satir isi BU kisiye yukluyor mu? Hitap + muhatap bicimi ('Ayse, sen cumaya hazirla', 'Zeynep, gondermeni rica
    ediyorum') ya da ozne + 3. kisi gelecek / emir / genis ('taslagi Mehmet incelesin', 'Selin de bakar'). Soru / olumsuz /
    gecmis satir hicbir zaman ('Mehmet mi yapacak?', 'incelemeyecek', 'inceledi')."""
    if _soru_olumsuz_gecmis(metin):
        return False
    gecis = _ad_gecisleri(kisi, metin, kisiler)
    if "hitap" in gecis and _ikinci_kisi_fiili(metin):
        return True
    return "ozne" in gecis and "3" in _fiil_etiketleri(metin)


def _ikinci_kisi_fiili(metin):
    return "2" in _fiil_etiketleri(metin) or bool(_RICA.search(tr_kucuk(sarmal_sil(metin))))


def _hitap_edilen(kisiler, metin):
    """'Ayse, sen cumaya hazirla' gibi dogrudan hitapla is verilen katilimci (tek ise); yoksa None. Yalniz ADAY uretir
    (model sorumluyu yazmadiysa kod kesin sahip uretmez)."""
    bulunan = [k for k in kisiler if "hitap" in _ad_gecisleri(k, metin, kisiler)]
    if len(bulunan) != 1 or _soru_olumsuz_gecmis(metin) or not _ikinci_kisi_fiili(metin):
        return None
    return bulunan[0]


def _baska_ad_geciyor(konusan, kisiler, metin):
    return any(_adi_geciyor(k, metin, kisiler) for k in kisiler if not _ayni_kisi_mi(k, konusan, kisiler))


def _sahip_belirle(s, adlar, kisiler, ben):
    """Tek aksiyon icin sahip karari. s: kaynak satir | None; adlar: modelin sorumlu adlari [(ad, isaretli)].
    Dondurur: (durum, [(ad, dogrulandi)]); dogrulanan ad katilimci listesindeki yazimiyla doner."""
    if s is None:
        return ("belirsiz", [(x, False) for x, _ in adlar]) if adlar else ("yok", [])
    konusan = str(s.get("speaker") or "").strip()
    if konusan in _BENIM:
        konusan = ben or ""                          # mikrofon sahibinin adi bilinmiyorsa adlandirilamaz
    kesin = kesin_ad_mi(s) and not s.get("cakisma")
    metin = sarmal_sil(s.get("text"))
    ham_taahhut = _konusan_taahhudu(metin)
    # (a) konusan kendi isini ustleniyor: acik 1. tekil kanit, soru / olumsuz / gecmis degil, muhatap ya da baska
    # katilimci adi yok ('Raporu Ayse'den bekliyorum', 'Zeynep, gondermeni rica ediyorum', 'taslagi Mehmet incelesin')
    kendi = (kesin and bool(konusan) and ham_taahhut and not _soru_olumsuz_gecmis(metin)
             and not _ikinci_kisi_var(metin) and not _baska_ad_geciyor(konusan, kisiler, metin))
    if not adlar:
        # model 'belirsiz' dedi: kod kesin sahip URETMEZ, en fazla aday ('Ad (?)')
        if kesin and kendi:
            return "belirsiz", [(_kisi_esle(konusan, kisiler) or konusan, False)]
        if kesin and not ham_taahhut:
            hitap = _hitap_edilen([k for k in kisiler if not _ayni_kisi_mi(k, konusan, kisiler)], metin)
            if hitap:
                return "belirsiz", [(hitap, False)]
        return ("belirsiz" if ham_taahhut else "yok"), []
    sonuc = []
    for x, isaretli in adlar:
        dogru, ad = False, x
        kisi = _kisi_esle(x, kisiler)
        if kesin and kisi and not isaretli:
            if konusan and _ayni_kisi_mi(kisi, konusan, kisiler):
                dogru = kendi                        # (a)
            else:                                    # (b) adlandirilmis sahip; konusan kendisi ustleniyorsa belirsiz
                dogru = not ham_taahhut and _is_atanan(kisi, metin, kisiler)
            if dogru:
                ad = kisi
        sonuc.append((ad, dogru))
    durum = "kesin" if all(d for _, d in sonuc) else "belirsiz"
    return durum, sonuc


def sorumlu_dogrula(aksiyonlar, satirlar, ben=None, katilimcilar=None):
    """K8 (MIMARI_v3 §6): aksiyon sahibini transkriptteki kaynak satiriyla dogrular ve her aksiyona
    a['sahip'] = {'ad', 'durum': 'kesin' | 'belirsiz' | 'yok'} yazar; a['sorumlu'] (nota basilan metin) ondan turer:
    kesin -> ad; belirsiz -> 'Ad (?)' (aday yoksa 'belirsiz'); yok -> 'belirsiz'.
    KESIN yalniz model sahibi ADIYLA ve '(?)' olmadan yazdiysa ve su iki yoldan biri dogrularsa:
      (a) kaynak satir kesin tek konusmaci (spk_guven >= 0.7, 'Ad?', '?', 'A → B', 'A / B', oda, cakisma degil), modelin
          adi katilimci listesinde konusanla AYNI kisi (tam ad ya da tekil ilk ad; 'Ali Kaya' != 'Ali Demir', iki Ali
          varken 'Ali' belirsiz) ve satir konusanin kendi taahhudu: 'ben' + 1. tekil fiil ya da is fiili kokunun 1. tekil
          bicimi ('hazirlarim', 'gonderecegim'); bekle- / rica et- / oner- / sor- / soyle- / bul- ve iyelik ('dosyalarim')
          sayilmaz; satirda baska katilimci adi ya da muhatap ('sen', emir, '-mAnI rica') varsa yol kapanir;
      (b) adlandirilmis sahip: kaynak satirin konusmacisi kesin, ad bir 'kisi' katilimcisiyla eslesiyor, satirda ⟨⟩
          disinda BIREBIR geciyor ('Aysen' != 'Ayse'; ilk ad ortaksa tam ad) VE is o kisiye yukleniyor: hitap + muhatap
          bicimi ('Ayse, sen cumaya hazirla', 'Zeynep, gondermeni rica ediyorum') ya da ozne + 3. kisi gelecek / emir /
          genis ('taslagi Mehmet incelesin'); konusan isi kendisi ustleniyorsa belirsiz.
    Iki yolda da soru ('Mehmet mi yapacak?'), olumsuz ('incelemeyecek', 'degil') ve gecmis ('inceledi') satir kesin
    olmaz. Model 'belirsiz' dediyse kod kesin sahip URETMEZ: (a) / hitap kaniti varsa yalniz aday ('Ad (?)').
    Kesin ad notta katilimci listesindeki yazimiyla basilir. 'bana/benim/bende' sahip kaniti degildir. katilimcilar:
    toplanti katilimci adlari (yoksa kesin konusmacilar). Dondurur: yeni liste (girdi degismez)."""
    bul = kaynak_satiri_bulucu(satirlar)
    kisiler = _kisi_listesi(satirlar, katilimcilar, ben)
    sonuc = []
    for a in aksiyonlar:
        a = dict(a)
        sonuc.append(a)
        adlar = _ad_parcala(a.get("sorumlu"))
        s = bul(a.get("madde", ""))
        durum, parcalar = _sahip_belirle(s, adlar, kisiler, ben)
        if durum == "kesin":
            ad = ", ".join(x for x, _ in parcalar)
            a["sorumlu"] = ad
        elif parcalar:
            ad = ", ".join(x for x, _ in parcalar)
            a["sorumlu"] = ", ".join(x if d else f"{x} (?)" for x, d in parcalar)
        else:
            ad = ""
            a["sorumlu"] = "belirsiz"
        a["sahip"] = {"ad": ad, "durum": durum}
    return sonuc


def saklama_uygula(gun, kok=None, bugun=None):
    """KVKK: notu uretilmis ('tamam') ve `gun` gunden eski toplantilarin TRANSKRIPTINI siler; not.md,
    meta.json ve parca ozetleri kalir. gun <= 0: kapali. Dondurur: temizlenen klasor sayisi."""
    kok = kok or KOK
    if not gun or gun <= 0 or not os.path.isdir(kok):
        return 0
    bugun = bugun or dt.date.today()
    sayi = 0
    for ad in os.listdir(kok):
        k = os.path.join(kok, ad)
        try:
            with open(os.path.join(k, "meta.json"), encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("durum") != "tamam" or meta.get("transkript_silindi"):
                continue
            if (bugun - dt.date.fromisoformat(meta["tarih"])).days < gun:
                continue
        except Exception:
            continue
        for dosya in ("altyazi.jsonl", "oneriler.json", "duzelt_log.jsonl"):        # oneriler.json: eski kayitlar
            if os.path.exists(os.path.join(k, dosya)):
                os.remove(os.path.join(k, dosya))
        for ad_ in os.listdir(k):            # tasma temizliginin yedekleri de transkript tasir
            if ".yedek-" in ad_ and ad_.split(".yedek-")[0] in ("altyazi.jsonl", "oneriler.json", "duzelt_log.jsonl",
                                                                 "parcalar"):
                y = os.path.join(k, ad_)
                if os.path.isdir(y):
                    shutil.rmtree(y, ignore_errors=True)
                else:
                    os.remove(y)
        pk = os.path.join(k, "parcalar")
        for f_ in (os.listdir(pk) if os.path.isdir(pk) else []):
            yol = os.path.join(pk, f_)
            try:
                with open(yol, encoding="utf-8") as f:
                    v = json.load(f)
                v["satirlar"], v["duzeltmeler"] = [], []
                with open(yol, "w", encoding="utf-8") as f:
                    json.dump(v, f, ensure_ascii=False, indent=1)
            except Exception:
                continue
        meta["transkript_silindi"] = bugun.isoformat()
        with open(os.path.join(k, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
        sayi += 1
    return sayi


def yeni_klasor(baslik, tarih):
    """Her oturum kendi klasorunu alir; ayni gun ayni adla ikinci/ucuncu baslatma asla
    oncekinin klasorune yazmaz (eski surumde ayni dakikada iki baslatma cakisabiliyordu)."""
    taban = os.path.join(KOK, f"{tarih.isoformat()}_{slug(baslik)}")
    if not os.path.exists(taban):
        return taban
    klasor = taban + dt.datetime.now().strftime("_%H%M")
    n = 2
    while os.path.exists(klasor):
        klasor = f"{taban}{dt.datetime.now().strftime('_%H%M')}-{n}"
        n += 1
    return klasor


# ---------------------------------------------------------------- altyazi tasmasi: algi ve kurtarma

def _yedek_yolu(yol):
    """yol + '.yedek-YYYYMMDD-HHMM' (varsa -2, -3 ...)."""
    taban = yol + dt.datetime.now().strftime(".yedek-%Y%m%d-%H%M")
    hedef, n = taban, 2
    while os.path.exists(hedef):
        hedef = f"{taban}-{n}"
        n += 1
    return hedef


def altyazi_kayitlari(klasor):
    """altyazi.jsonl -> transkript satirlari (ilk gorulus sirasiyla). Guncelleme kayitlari ({'guncelle': true},
    ayni id: konusmaci karari, buyuyen satir) asil satira islenir, ayri satir sayilmaz; {'sil': true} satiri
    kaldirir (yanki klibi, yeniden kesilen klip); 'yerine_id' tasiyan satir (kesilen klibin parcasi) ozgun satirin
    yerine girer. Bozuk satir atlanir."""
    yol = os.path.join(klasor, "altyazi.jsonl")
    if not os.path.exists(yol):
        return []
    satirlar, id_ile = [], {}
    with open(yol, encoding="utf-8", errors="replace") as f:
        for ham in f:
            try:
                s = json.loads(ham)
            except Exception:
                continue
            if not isinstance(s, dict):
                continue
            if s.get("sil"):
                hedef = id_ile.pop(s.get("id"), None)
                if hedef is not None:
                    satirlar = [x for x in satirlar if x is not hedef]
                continue
            if s.get("guncelle"):
                hedef = id_ile.get(s.get("id"))
                if hedef is not None:
                    hedef.update({k: v for k, v in s.items() if k not in ("id", "guncelle", "onceki_text")})
                continue
            if "text" not in s:
                continue
            s = dict(s)
            yerine = id_ile.get(s.pop("yerine_id", None))
            if yerine is not None:                          # ozgun satirin hemen onune (sil kaydi onu kaldirir)
                satirlar.insert(next(i for i, x in enumerate(satirlar) if x is yerine), s)
            else:
                satirlar.append(s)
            if s.get("id") is not None:
                id_ile[s["id"]] = s
    return satirlar


def stt_eksik_metni(araliklar):
    """[[bas, bit], ...] (epoch) -> 'STT yok — transkript eksik (10:05–10:12, 10:40–10:41)'. bit None: suruyor."""
    def hhmm(e):
        try:
            return saat.yerel(float(e)).strftime("%H:%M")
        except (TypeError, ValueError, OverflowError, OSError):
            return "…"
    parca = [f"{hhmm(a[0])}–{hhmm(a[1]) if len(a) > 1 and a[1] is not None else '…'}" for a in araliklar or []]
    return "STT yok — transkript eksik" + (f" ({', '.join(parca)})" if parca else "")


def _gun_sn(satirlar):
    """Satir zamanlari (sn); toplanti gece yarisini gectiyse sabaha kalan saatler 24 saat ileri alinir."""
    sn = [_sn(s.get("ts")) for s in satirlar]
    if sn and max(sn) - min(sn) > 12 * 3600:
        sn = [t + 86400 if t < 12 * 3600 else t for t in sn]
    return sn


def tasma_olcumu(klasor):
    """Kaydin altyazi tasmasi tasiyip tasimadigi: {'satir', 'dk_en_cok', 'tekrar', 'tekrar_orani', 'tasma'}.
    Tasma: bir dakikada TASMA_ALGI_DK'dan fazla satir ya da (konusmaci, metin) birebir tekrar orani
    TASMA_ALGI_TEKRAR'dan buyuk (en az 50 satirlik kayitta)."""
    satirlar = altyazi_kayitlari(klasor)
    dakika, gorulen, tekrar = {}, set(), 0
    for s in satirlar:
        d = str(s.get("ts") or "")[:5]
        dakika[d] = dakika.get(d, 0) + 1
        a = (s.get("speaker"), normalize(s.get("raw") or s.get("text") or ""))
        if a in gorulen:
            tekrar += 1
        gorulen.add(a)
    n = len(satirlar)
    oran = tekrar / n if n else 0.0
    en_cok = max(dakika.values()) if dakika else 0
    return {"satir": n, "dk_en_cok": en_cok, "tekrar": tekrar, "tekrar_orani": round(oran, 3),
            "tasma": en_cok > TASMA_ALGI_DK or (n >= 50 and oran > TASMA_ALGI_TEKRAR)}


def tasma_var_mi(klasor):
    """Gecmis/'Yeniden ozetle': bu kayitta altyazi tasmasi var mi (satir/dk > 120 ya da tekrar > %30)?"""
    try:
        return tasma_olcumu(klasor)["tasma"]
    except Exception:
        return False


def _temiz_satirlar(satirlar):
    """(konusmaci, normalize(metin)) birebir tekrarlarini (ilki kalir; 20 karakterden kisa onaylarda yalniz ONEK_SN
    icindeki tekrar) ve ayni konusmacinin ONEK_SN icindeki onek-zinciri yarim hallerini (en uzunu kalir) eler;
    ts'e gore (kararli) siralar.
    Dondurur (temiz, tekrar_sayisi, onek_sayisi)."""
    gorulen, kisa_son, tekil = set(), {}, []
    for s, t in zip(satirlar, _gun_sn(satirlar)):
        a = (s.get("speaker"), normalize(s.get("raw") or s.get("text") or ""))
        if not a[1]:
            continue
        if len(a[1]) < 20:
            # kisa onaylar ('Evet.') toplanti boyunca gercekten tekrar edilir: yalniz ONEK_SN icindeki tekrar elenir
            if a in kisa_son and abs(t - kisa_son[a]) <= ONEK_SN:
                continue
            kisa_son[a] = t
        elif a in gorulen:
            continue
        gorulen.add(a)
        tekil.append(s)
    tekrar = len(satirlar) - len(tekil)
    zaman = _gun_sn(tekil)
    norm = [normalize(s.get("raw") or s.get("text") or "") for s in tekil]
    atilan = set()
    kisiye = {}
    for i, s in enumerate(tekil):
        kisiye.setdefault(s.get("speaker"), []).append(i)
    for idler in kisiye.values():
        sirali = sorted(idler, key=lambda i: norm[i])     # bir metnin uzantilari hemen arkasinda gelir
        for j, i in enumerate(sirali):
            for k in sirali[j + 1:]:
                if not norm[k].startswith(norm[i]):
                    break
                fark = abs(zaman[k] - zaman[i])
                # kelime sinirinda kesilmis yarim hal ONEK_SN icinde; kelime ortasinda ('rapor' -> 'raporu',
                # ama '1' -> '10' de) ya da cok kisa ise yalniz 60 sn icinde
                kelime_siniri = norm[k][len(norm[i]):len(norm[i]) + 1] == " "
                if fark <= ONEK_SN and ((kelime_siniri and len(norm[i]) >= 15) or fark <= 60):
                    atilan.add(i)        # i, k'nin yarim hali
                    break
    temiz = [(zaman[i], i, s) for i, s in enumerate(tekil) if i not in atilan]
    temiz.sort(key=lambda x: (x[0], x[1]))
    return [s for _, _, s in temiz], tekrar, len(atilan)


def _parcala(satirlar):
    """Temiz satirlardan canli kuralla (PARCA_TOKEN / PARCA_SURE_SN / sessizlik) parcalar; ozet=None."""
    parcalar, mevcut, tok, bas, son = [], [], 0, None, None

    def kapat(neden):
        zamanlar = [s["ts"] for s in mevcut]
        parcalar.append({"sira": len(parcalar) + 1, "neden": neden, "token": tok, "baslangic": min(zamanlar),
                         "bitis": max(zamanlar), "satirlar": list(mevcut), "duzeltmeler": [], "ozet": None})

    sinir = parca_token()
    for s, t in zip(satirlar, _gun_sn(satirlar)):
        if mevcut and tok >= SESSIZLIK_MIN_TOKEN and t - son > SESSIZLIK_SN:
            kapat("sessizlik")
            mevcut, tok, bas = [], 0, None
        mevcut.append(s)
        tok += llm.token_tahmin(f"{s.get('speaker')}: {s.get('text')}")
        bas = t if bas is None else bas
        son = t
        if tok >= sinir:
            kapat("token")
            mevcut, tok, bas = [], 0, None
        elif t - bas >= PARCA_SURE_SN:
            kapat("süre")
            mevcut, tok, bas = [], 0, None
    if mevcut:
        kapat("bitiş")
    return parcalar


def transkript_temizle(klasor, yedekle=True, kuru=False):
    """Altyazi tasmasiyla bozulmus kaydi kurtarir: altyazi.jsonl'deki birebir tekrarlar ve onek-zinciri yarim
    halleri elenir, satirlar zamana gore siralanir; parcalar/ temiz satirlardan yeniden parcalanir (ozetsiz),
    meta.json'daki parca sayisi guncellenir. yedekle: parcalar/ ve altyazi.jsonl once '.yedek-YYYYMMDD-HHMM'
    olarak saklanir. kuru: hicbir sey yazilmaz, yalniz sayilar.
    Dondurur {'once', 'sonra', 'parca_once', 'parca_sonra', 'tekrar', 'onek'} ya da altyazi.jsonl yoksa None."""
    yol = os.path.join(klasor, "altyazi.jsonl")
    if not os.path.exists(yol):
        return None
    satirlar = altyazi_kayitlari(klasor)
    temiz, tekrar, onek = _temiz_satirlar(satirlar)
    parcalar = _parcala(temiz)
    pk = os.path.join(klasor, "parcalar")
    parca_once = len([f for f in os.listdir(pk) if f.endswith(".json")]) if os.path.isdir(pk) else 0
    sonuc = {"once": len(satirlar), "sonra": len(temiz), "parca_once": parca_once, "parca_sonra": len(parcalar),
             "tekrar": tekrar, "onek": onek}
    if kuru:
        return sonuc
    if os.path.isdir(pk):
        if yedekle:
            os.rename(pk, _yedek_yolu(pk))
        else:
            shutil.rmtree(pk)
    os.makedirs(pk, exist_ok=True)
    for v in parcalar:
        with open(os.path.join(pk, f"parca_{v['sira']:03d}.json"), "w", encoding="utf-8") as f:
            json.dump(v, f, ensure_ascii=False, indent=1)
    if yedekle:
        shutil.copyfile(yol, _yedek_yolu(yol))
    with open(yol, "w", encoding="utf-8") as f:
        for s in temiz:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    meta_yol = os.path.join(klasor, "meta.json")
    try:
        with open(meta_yol, encoding="utf-8") as f:
            meta = json.load(f)
        meta["parca"] = len(parcalar)
        meta["tasma_temizlendi"] = dt.datetime.now().isoformat(timespec="minutes")
        with open(meta_yol, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    return sonuc

