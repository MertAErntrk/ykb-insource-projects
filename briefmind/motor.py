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
       notu_uret()         -> birlestirme -> sorumlu dogrulama -> dayanak kontrolu -> not.md (icsel supheli satirlar girmez;
                              zaman cizelgesi suphesi 'cizelge' icerigi dusurmez: nota 'Bilinmeyen:' etiketiyle girer)
       kancalar (isler/yakalayici): son_loopback_ses() (panel canliligi), olay_kaydet(mesaj, **ek) (log + olaylar.jsonl),
                              yeniden_bul_fn (atama basarisizsa panel yeniden aranir; kontrol() -> _atama_denetle)
       yeniden_ozetle()    -> (Gecmis) TUM parcalar yeniden duzeltme (yalniz 'raw'i olan satirlar) + ozet
       ses_olayi(uzun)     -> Faz 3 (config uzun_pencere): uzun pencere metni klip satirina (raw/text; raw_kisa = kisa metin,
                              uyusmayan kelimeler 'belirsiz', notta ⟨⟩); konusmaci degismez. Parca kapanisi sonucu en fazla
                              UZUN_BEKLE_SN bekler; gec gelirse satirlar guncellenir, parca yeniden ozetlenir

Disk (toplantilar/<tarih>_<slug>/): meta.json, altyazi.jsonl (satir v2 + {'guncelle'} / {'sil'} kayitlari),
       parcalar/parca_NNN.json, not.md, duzelt_log.jsonl (kabul/red, kelime duzeyinde), cizelge.json (konusmaci araliklari, metin yok),
       olaylar.jsonl (konusmaci gunlugu)
olay(tip, veri) geri cagrisi: "satir" (yeni satir, konusmaci beklemede), "satir_guncelle" (satirin tamami + "gorunen":
       konusmaci karari geldi), "satir_sil" ({"id", "neden"}: yanki klibi atildi), "satir_degistir" ({"eski_id",
       "yeni": [satir, ...]}: yeniden kesilen klibin satiri parcalariyla degisti), "altyazi_izi" (canli altyazi
       gorunumu; transkripte girmez), "kapsama" ({"var", "bas"[, "neden", "mesaj"]}: konusmaci bilgisi 60 sn'den uzun yok / geri geldi),
       "atama" ({"var", "oran", "bas"}: son 2 dk'da kapsamali kararlarin >= %60'i '?' / duzeldi),
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
import ses_izi as ses_izi_mod
import saat                    # adima dayanikli zaman kaynagi: tum zaman karsilastirmalari saat.simdi()
import tarih as tarih_mod
import terim as terim_mod
import uzun as uzun_mod
from kelime import normalize
from konusmaci import Konusmaci
from metin import KONU_DAMGASI, ad_geciyor, cift_yonlu_benzerlik, gorunen_ad, kesin_ad_mi, tr_kucuk
from metin import nota_girer as metin_nota_girer
from stt import CIZELGE_PUANI, hal_esik       # CIZELGE_PUANI: tek sabit stt.py'de (0.3)

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
UZUN_BEKLE_SN = 20              # Faz 3: parca kapanisi kapsadigi araligin uzun pencere sonucunu en fazla bu kadar bekler
GEC_KESIM_TAVAN = 500           # suresi dolmus (parcasi eksik) kesimlerde gec parca icin tutulan en fazla kayit
OZET_YENI_SN = 10               # kapanmis parcaya gec satir girdiyse yeniden ozet bu kadar sessizlikten sonra
KAPSAMA_UYARI_SN = 60           # konusmaci bilgisi (altyazi cizelgesi) bu kadar yoksa arayuz uyarir
ATAMA_PENCERE_SN = 120          # atama denetimi: son bu kadar sn'nin loopback kararlari
ATAMA_EN_AZ = 10                # ... en az bu kadar kapsamali karar ve
ATAMA_ORAN = 0.60               # ... en az bu payi '?' ise uyari (CIZELGE_PUANI: stt.py ile tek sabit, testte esitligi sinanir)
ATAMA_TEMIZ_ORAN = 0.30         # '?' payi bunun altina inerse uyari kalkar
ATAMA_KESIN_ORAN = 0.30         # M2: kapsamali kararlarin kesin ad payi bunun altindaysa uyari ('kesin_az': yanlis adlarla
                                #     dolu cokuste '?' payi dusuk kalir); kesin pay bunun 2 katina cikinca kalkar
ATAMA_GECIKME_SN = 10.0         # konusmaci dogrulanmis L'yi supheli (capraz ilinti farkli gecikme) bildirip bu kadar
                                #     surerse uyari (gecikme sicradi: kararlar 'Ad?', L yeniden olculuyor)
ATAMA_YAPISAL = ("capa_az", "kisa", "L_basamak", "L_supheli", "L_onaysiz", "eksik", "bosluk", "kaba", "gec", "mad")
                                # ... kesin_az olcutunde sayilmayan (yapisal / gecici tavanli) karar nedenleri
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
    """Madde satirinin sonuna belirsizlik isareti ekler; damgalar varsa ILKININ oncesine: '- madde (?) [konu: X] ⏱ts'
    (konu damgasi ve kaynak isareti maddenin parcasi degildir)."""
    if ek.strip() in KONU_DAMGASI.sub("", satir):
        return satir
    k = KONU_DAMGASI.search(satir)
    yerler = [i for i in (satir.find(_ISARET), k.start() if k else -1) if i > 0]
    if yerler:
        i = min(yerler)
        return satir[:i].rstrip() + ek + " " + satir[i:].lstrip()
    return satir.rstrip() + ek


_KAYNAK_DAMGASI = re.compile(_ISARET + r"\s*\d{1,2}:\d{2}(?::\d{2})?")
_SON_DAMGA = re.compile(r"\s*" + _ISARET + r"\s*\d{1,2}:\d{2}(?::\d{2})?\s*$")


def sablon_satiri_ayir(satir):
    """Sablon bolumu madde satiri -> (on, govde, damga): on = girinti + '- ' + kodun oneki ('Seçilen: ', 'Artı: ' ...);
    govde = denetlenen madde metni (kaynak damgasi ve kodun (?) isareti haric); damga = sondaki '⏱HH:MM:SS' ('' yoksa)."""
    m = re.match(r"^(\s*[-*]\s+)(.*)$", satir)
    if not m:
        return "", satir.strip(), ""
    on, govde = m.group(1), m.group(2)
    for onek in llm.SABLON_ONEKLERI:
        if govde.startswith(onek):
            on, govde = on + onek, govde[len(onek):]
            break
    d = _SON_DAMGA.search(govde)
    damga = d.group(0).strip() if d else ""
    govde = (govde[:d.start()] if d else govde)
    govde = _KAYNAK_DAMGASI.sub(" ", govde)
    return on, re.sub(r"\s+", " ", govde).strip(), damga


def madde_dayanagi(aday, dizin, soru=False):
    """Tek karar / aksiyon / soru maddesinin satir bazli dayanak karari (dayanak_kontrolu'nun madde yolu; canli bolum
    ozeti de bunu kullanir). aday: madde metni (kaynak damgasi ayiklanmis); dizin: _dayanak_dizini(transkript).
    Dondurur: None (denetlenecek kok yok: madde oldugu gibi kalir) | 'at' (dayanak yok ya da sayi / tarih / kod yalniz
    ⟨⟩ icinde) | (belirsiz_madde, belirsiz_sahip). Soru maddesi belirsiz_madde almaz."""
    ak = _kokler(aday) - _MADDE_DURAK
    if not ak:
        return None
    dayanak = _dayanak_bul(ak, dizin, zorunlu=_zorunlu_degerler(aday))
    if dayanak is None or (dayanak["eksik"] and _yalniz_sarmalda(dayanak["eksik"], dizin)):
        return "at"
    belirsiz_madde = bool((dayanak["pay"] > SARMAL_PAY_SINIRI or dayanak["eksik"] or dayanak["sarmal_kaynak"])
                          and not soru)
    return belirsiz_madde, belirsiz_madde or _etiket_belirsiz(dayanak["ana"]["etiket"])


def dayanak_kontrolu(not_md, transkript, esik=0.34, ozet_kaynak=None):
    """Nottaki Karar/Aksiyon/Soru maddelerini ve ozet cumlelerini transkriptle karsilastirir; dayanagi olmayan
    maddeleri cikarir. Modelin terim listesi/gundemden ya da genel bilgisinden 'uydurdugu' satirlara karsi son
    sigorta. Dondurur (yeni_md, atilanlar).
    Karar/aksiyon/soru maddeleri SATIR bazli denetlenir (MIMARI_v3 §6; madde_dayanagi; maddenin kaynak damgasi
    ⏱HH:MM:SS deger sayilmaz, isaret damganin onune yazilir): kok ortusmesi >= DAYANAK_SATIR_ESIK olan 1-3
    ardisik transkript satiri aranir; ⟨⟩ icindeki (eszamanli konusma) kelimeler dayanak sayilmaz. Maddedeki sayi / tutar,
    ay / gun adi ve kod adi (zorunlu deger) pencerenin ⟨⟩ disindaki metninde gecmeli: yalniz ⟨⟩ icinde geciyorsa madde
    atilir, hic gecmiyorsa (?) alir. Bulunan dayanagin ⟨⟩ payi > %30 ise ya da maddeyi tek basina en iyi karsilayan satir
    ⟨⟩ / cakisma satiriysa karar/aksiyon maddesinin sonuna (?) eklenir (madde belirsiz); dayanak satirinin konusmaci
    etiketi kesin degilse ('Ad?:', 'Bilinmeyen:', 'A → B:', 'A / B:') aksiyonun sorumlusu (?) olur.
    ozet/sonraki adim cumleleri esik (kok orani, tum transkript) ile denetlenir; ozet_kaynak: ozet paragrafi icin ek
    dayanak (bolum ozetleri). Ozet cumleleri soyutlayici fiiller ('gorusuldu', 'ele alindi') tasir; yalniz
    transkriptle olculunce dogru cumleler de atiliyordu.
    Sablon ek bolumlerinin (llm.sablon_bolumu_mu: Ilerleme, Fikirler, Seçenekler ...) maddeleri de karar maddesi gibi
    denetlenir (kodun onekleri 'Seçilen:', 'Artı:' madde sayilmaz); atilan secenegin alt maddeleri (artı / eksi) de duser."""
    kokler = _kokler(sarmal_sil(transkript))
    ozet_zemin = _kokler(transkript)                      # ozet ⟨⟩ icindekini 'tartisildi' diye anabilir
    if len(kokler) < 6:
        return not_md, []
    dizin = _dayanak_dizini(transkript)
    atilan, yeni = [], []
    bolum, sablon_bolum, ust_atildi = "", False, False
    for satir in not_md.split("\n"):
        cizgisiz = satir.strip()
        if cizgisiz.startswith("#"):
            bolum = cizgisiz.lower()
            sablon_bolum, ust_atildi = llm.sablon_bolumu_mu(cizgisiz), False
            yeni.append(satir)
            continue
        if sablon_bolum and cizgisiz.startswith(("-", "*")):
            alt = satir[:1].isspace()                       # secenegin artı / eksi alt maddesi
            if alt and ust_atildi:
                continue                                   # ust madde atildi: gerekceleri de notta kalmaz
            _, govde, _ = sablon_satiri_ayir(satir)
            karar = madde_dayanagi(govde, dizin) if govde else None
            if not alt:
                ust_atildi = karar == "at"
            if karar == "at":
                atilan.append(govde)
                continue
            yeni.append(_madde_isaretle(satir) if karar and karar[0] else satir)
            continue
        durum = llm.durum_satiri_ayir(cizgisiz) if bolum.startswith("### ") else None
        if durum is not None:
            # durum sablonu konu satiri ('Durum: X. Risk: Y. ⏱ts'): durum ve risk ayri madde gibi denetlenir
            parca = []
            for metin in durum[:2]:
                govde = re.sub(r"\s*\(\?\)\s*$", "", metin).strip()
                karar = madde_dayanagi(govde, dizin) if llm.madde_dolu(govde) else None
                if karar == "at":
                    atilan.append(govde)
                    parca.append("")
                else:
                    parca.append(f"{govde} (?)" if (karar and karar[0]) or govde != metin else metin)
            yeni_satir = llm.durum_satiri(*parca)
            if yeni_satir:
                yeni.append(satir[:len(satir) - len(satir.lstrip())] + yeni_satir + (f" {durum[2]}" if durum[2] else ""))
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
        # kaynak damgasi (⏱HH:MM:SS) ve konu damgasi ([konu: X]) maddenin degeri degildir
        aday = KONU_DAMGASI.sub(" ", _KAYNAK_DAMGASI.sub(" ", aday)).strip()
        karar = madde_dayanagi(aday, dizin, soru="soru" in bolum)
        if karar is None:
            yeni.append(satir)
            continue
        if karar == "at":
            atilan.append(aday)                 # dayanak yok ya da sayi / tarih / kod yalniz ⟨⟩ icinden geliyor
            continue
        belirsiz_madde, belirsiz_sahip = karar
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


# ---------- not son denetimi: ozet / konu akisi / sonraki adim / kisiye gore liste (dayanak kontrolunden SONRA) ----------
# Aksiyon tablosu dayanak kontrolu + sahip dogrulamasiyla kesinlesir; ozet ve 'Bir sonraki adim' serbest cumledir ve
# yalniz kok ortusmesiyle denetleniyordu: ⟨⟩ tutari ozete, belirsiz kaynakli is sahibiyle sonraki adima giriyordu.

# gelecek / sifat-fiil ('hazirlayacak', 'hazirlayacagini', 'gondereceklerini', 'yapacagiz'), -mAlI, '-mAsI gerek / lazim',
# ustlen- / sorumlu / gorevlendiril- / atandi; 3. kisi emir ('hazirlasin') is fiili kaliplariyla (_is_cumlesi)
_IS_IFADESI = re.compile(r"(?<!\w)(?:\w+[ae]c[ae](?:k|ğ)\w*|\w+m[ae]l[ıi]\w*|"
                         r"\w+m[ae]s[ıi](?:n[ıi])?\s+(?:gerek|lazım|şart|bekleniyor|istendi|isteniyor)\w*|üstlen\w*|"
                         r"sorumlu\w*|görevlendiril\w*|atand[ıi]\w*)(?!\w)")
_CUMLE_SONU = re.compile(r"(?<=[.!?])\s+")
_SONRAKI_CUMLE = re.compile(r"(?<=[.!?;])\s+")
_KONU_SATIRI = re.compile(r"^(\s*-\s+\S+:\s)(.*)$")
NOT_ESLESME_ESIK = 0.5          # cumle -> aksiyon satiri: maddenin kokleri cumlede en az bu oranda


def _ay_gun(kelime):
    """Sade kelime bir ay / gun adi mi (_zorunlu_degerler ile ayni kural)? Dondurur: ad | None."""
    return next((ad for ad in _AY_GUN if kelime.startswith(ad) and (kelime == ad or len(ad) >= 4)), None)


class _DegerZemini:
    """Transkriptte sayi / ay-gun degerlerinin nerede gectigi: ⟨⟩ disi (kesin zemin) ve yalniz belirsiz zemin (⟨⟩ icleri
    + halusinasyon supheli satirlar; supheli satirlar transkripte hic girmez, metinleri ayrica verilir)."""

    def __init__(self, dizin, supheli_metin=""):
        self.disi = [k for d in dizin for k in d["duz"]]
        self.disi_sayi = set().union(*[d["sayi"] for d in dizin]) if dizin else set()
        self.ic = [k for d in dizin for k in d["ic"]] + _sade_kelimeler(supheli_metin)
        self.ic_sayi = (set().union(*[d["ic_sayi"] for d in dizin]) if dizin else set()) | set(
            re.findall(r"\d+", supheli_metin or ""))

    def belirsiz(self, deger):
        """deger kesin zeminde yok: ⟨⟩ disinda hicbir satirda gecmiyor (yalniz ⟨⟩ / supheli metinde geciyor ya da
        transkriptte HIC gecmiyor: modelin uydurdugu tutar / tarih). Kesin gorunmemeli."""
        return not _deger_geciyor(deger, self.disi, self.disi_sayi)

    def yalniz_ic(self, deger):
        """deger yalniz ⟨⟩ / supheli metinde geciyor (olay metni icin: 'yalniz ⟨⟩ icinde' / 'transkriptte yok')."""
        return _deger_geciyor(deger, self.ic, self.ic_sayi)


# kod / araclarin urettigi degerler: kaynak damgasi ('⏱10:12:03'), tarih_coz'un ISO tarihi, konu akisi saat araligi; notun
# kendi sayimi ('12 madde konuşuldu', '3 aksiyon'): transkriptte gecen bir deger degildir
_KORUNAN_DEGER = re.compile(_ISARET + r"\s*\d{1,2}:\d{2}(?::\d{2})?|(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)"
                            r"|(?<!\d)\d{1,2}:\d{2}(?::\d{2})?\s*[–-]\s*\d{1,2}:\d{2}(?::\d{2})?(?!\d)"
                            r"|(?<![\d.,])\d{1,3}\s+(?:madde|aksiyon|karar|konu|açık soru|soru|başlık|bölüm|katılımcı|kişi)"
                            r"(?:l[ae]r)?(?!\w)")


def _degerleri_isaretle(cumle, zemin):
    """Cumledeki sayi / tutar / ay-gun degerlerinden kesin zeminde (⟨⟩ disindaki transkript) GECMEYENLERIN yerine '(?)'
    yazar: yalniz ⟨⟩ (ya da supheli satir) icinde gecen ve transkriptte hic gecmeyen (uydurma) deger kesin gibi
    gorunmez, 'ele alindi' baglami kalir. Kaynak damgasi, ISO tarih ve saat araligi (kodun urettigi) muaftir.
    Dondurur (yeni_cumle, isaretlenen_degerler)."""
    isaretli = []

    def degistir(m):
        k = m.group(0)
        if re.search(r"\d", k):
            degerler = re.findall(r"\d+", k)
        else:
            sade = _sade_kelimeler(k)
            ad = _ay_gun(sade[0]) if sade else None
            degerler = [ad] if ad else []
        if any(zemin.belirsiz(d) for d in degerler):
            isaretli.append(k)
            return "(?)"
        return k
    parcalar, son = [], 0
    for m in _KORUNAN_DEGER.finditer(cumle):
        parcalar += [re.sub(r"\w+", degistir, cumle[son:m.start()]), m.group(0)]
        son = m.end()
    yeni = "".join(parcalar) + re.sub(r"\w+", degistir, cumle[son:])
    return re.sub(r"\(\?\)(?:\s*\(\?\))+", "(?)", yeni), isaretli


def _deger_olayi(yer, isaret, zemin):
    """not_son_denetim olay metni: isaretlenen degerler yalniz ⟨⟩ icinde mi, transkriptte hic yok mu."""
    ic = [x for x in isaret if any(zemin.yalniz_ic(d) for d in (re.findall(r"\d+", x) or _sade_kelimeler(x)[:1]))]
    yok = [x for x in isaret if x not in ic]
    neden = "; ".join(x for x in (f"yalnız ⟨⟩ içinde: {', '.join(ic)}" if ic else "",
                                  f"transkriptte yok: {', '.join(yok)}" if yok else "") if x)
    return f"{yer} kesin olmayan değer (?) ile işaretlendi ({neden})"


def _kisi_adlari(katilimcilar, aksiyonlar):
    """Cumlede aranacak kisi adlari: katilimcilar + tablodaki sahip adlari ('?', 'Ben', birlesik etiketler haric)."""
    adlar = []
    for ad in list(katilimcilar or []) + [x for a in aksiyonlar for x in a["sahip"]["ad"].split(",")]:
        ad = re.sub(r"\s+", " ", str(ad or "")).strip()
        if (len(ad) >= 3 and "?" not in ad and " → " not in ad and " / " not in ad and tr_kucuk(ad) != "ben"
                and ad not in adlar):
            adlar.append(ad)
    return adlar


def _ad_varyantlari(ad):
    """Kisinin metinde gecebilecek yazimlari, uzundan kisaya: tam ad, 'K. Soyad' / 'K.Soyad', soyad, ilk ad. Soyad tek
    basina (cok kelimeli adda, >= 3 harf) yalniz buyuk harfle yazilmissa sayilir (_ad_varyanti_geciyor): 'Kara',
    'Yılmaz' gibi soyadlar siradan kelime de olabilir."""
    parcalar = ad.split()
    v = [ad]
    if len(parcalar) >= 2 and len(parcalar[-1]) >= 3:
        soyad = parcalar[-1]
        v += [f"{parcalar[0][0]}. {soyad}", f"{parcalar[0][0]}.{soyad}", soyad]
    v.append(parcalar[0])
    return list(dict.fromkeys(v))


def _ad_varyanti_geciyor(parca, ad, metin):
    if parca == ad or parca == ad.split()[0]:
        return ad_geciyor(parca, metin)
    if "." in parca:                                   # 'K. Soyad': harf + nokta + soyad, buyuk-kucuk duyarsiz
        return re.search(r"(?<!\w)" + re.escape(tr_kucuk(parca)) + r"(?!\w)", tr_kucuk(metin)) is not None
    return re.search(r"(?<!\w)" + re.escape(parca) + r"(?!\w)", metin) is not None     # soyad: buyuk harfle


def _ad_geciyor_her_bicim(ad, metin):
    """Kisi metinde tam adi, ilk adi, soyadi ya da 'K. Soyad' bicimiyle geciyor mu?"""
    return any(_ad_varyanti_geciyor(p, ad, metin) for p in _ad_varyantlari(ad))


def _gecen_kisiler(cumle, adlar):
    """Cumlede tam adi, ilk adi, soyadi ya da 'K. Soyad' bicimi gecen kisiler (bir bicim birden cok kisiye uyuyorsa
    hepsi: belirsizdir)."""
    return [ad for ad in adlar if _ad_geciyor_her_bicim(ad, cumle)]


def _ad_cikar(cumle, adlar):
    """Cumleden kisi adlarini (tam ad, 'K. Soyad', soyad, ilk ad; ekli yazim ve 'tarafindan' dahil) atar, cumleyi '(?)'
    ile isaretler. Geriye anlamli bir sey kalmazsa None."""
    for ad in sorted(adlar, key=len, reverse=True):
        for parca in _ad_varyantlari(ad):
            bayrak = re.I if "." in parca else 0
            cumle = re.sub(r"(?<!\w)" + re.escape(parca) + r"(?:['’]\w*)?(?:\s+tarafından)?(?!\w)", " ", cumle,
                           flags=bayrak)
    m = re.search(r"[.!?;]*\s*$", cumle)
    son, govde = m.group(0).strip(), re.sub(r"\s+", " ", cumle[:m.start()]).strip()
    govde = re.sub(r"^(?:(?:ve|ile|,)\s*)+", "", govde)
    govde = re.sub(r"\s+(?:ve|ile)\s*(?=,|$)", "", govde)
    govde = re.sub(r"\s+,", ",", govde).strip(" ,")
    if len(re.findall(r"\w+", govde)) < 2:
        return None
    govde = govde[:1].upper() + govde[1:]
    if not govde.endswith("(?)"):
        govde += " (?)"
    return govde + (son or ".")


def _madde_eslesir(ck, cumle_sayi, madde):
    """Cumle (kokleri ck, sayilari cumle_sayi) bu aksiyon maddesini anlatiyor mu? Maddenin kokleri cumlede >= esik ve
    ikisinde de sayi varsa en az biri ortak."""
    ak = _kokler(re.sub(r"\(\?\)", " ", madde)) - _MADDE_DURAK
    if not ak or len(ak & ck) / len(ak) < NOT_ESLESME_ESIK:
        return False
    sayi = set(re.findall(r"\d+", madde))
    return not (sayi and cumle_sayi) or bool(sayi & cumle_sayi)


def _kesin_sahibi(ad, aksiyon):
    return aksiyon["sahip"]["durum"] == "kesin" and ad in [x.strip() for x in aksiyon["sahip"]["ad"].split(",")]


def _yalniz_belirsiz_etikette(ad, ck, dizin):
    """Cumlenin (kokleri ck) bu kisiye dayanagi yalniz belirsiz etiketli satirlarda mi ('Ad?', 'A → Ad', 'Ad / B',
    'Ad (oda)', Bilinmeyen degil: adi gecen etiket)? Kesin etiketli satirda ⟨⟩ icindeki kelimeler sayilmaz."""
    kesin = belirsiz = 0
    for d in dizin:
        e = d["etiket"] or ""
        if not _ad_geciyor_her_bicim(ad, e):
            continue
        if _etiket_belirsiz(e) or d["cakisma"]:
            belirsiz = max(belirsiz, len(ck & d["tum"]))
        else:
            kesin = max(kesin, len(ck & d["kok"]))
    return belirsiz > kesin


def _tema_kisisi_kesin(ad, ck, dizin):
    """'Kemal Tuncer önerileri:' gibi tema satiri: temanin maddeleri (kokleri ck) bu kisinin KESIN etiketli satirlarinda
    dayanak buluyor mu (ve belirsiz etiketli satirlardakinden az degil)? Degilse temadaki ad kesin degildir."""
    kesin = belirsiz = 0
    for d in dizin:
        e = d["etiket"] or ""
        if not _ad_geciyor_her_bicim(ad, e):
            continue
        if _etiket_belirsiz(e) or d["cakisma"]:
            belirsiz = max(belirsiz, len(ck & d["tum"]))
        else:
            kesin = max(kesin, len(ck & d["kok"]))
    return kesin > 0 and kesin >= belirsiz


def _is_cumlesi(cumle, ck, aksiyonlar):
    """Cumle bir isi / gorevi anlatiyor mu? Gelecek / gereklilik / ustlenme kalibi, is fiilinin 3. kisi emri / gelecegi
    ('hazirlasin') ya da -- zaman kipinden bagimsiz -- cumle son tablodaki bir aksiyon maddesiyle eslesiyor ('Selin
    raporu hazirliyor' / 'hazirlayacagini soyledi' tablodaki 'Raporu hazirla' ise is cumlesidir)."""
    if _IS_IFADESI.search(tr_kucuk(cumle)) or "3" in _fiil_etiketleri(cumle):
        return True
    sayi = set(re.findall(r"\d+", cumle))
    return any(_madde_eslesir(ck, sayi, a["madde"]) for a in aksiyonlar)


def _sahip_denetle(cumle, adlar, aksiyonlar, dizin, sonraki):
    """Kisi adlandiran cumle (ozet ya da sonraki adim). Is cumlesi (sonraki adimin her cumlesi; ozette gelecek zaman /
    -meli / ustlen- / sorumlu): adi gecen her kisi, cumlenin anlattigi DAYANAKLI aksiyonun (son tablo) KESIN sahibi olmali.
    Degilse: sonraki adimda is tabloda varsa (sahibi '(?)' / baskasi) cumle duser, yoksa ad cikar + '(?)'; ozette ad cikar
    + '(?)'. Is cumlesi olmayan ozet cumlesinde kisiye dayanak yalniz belirsiz etiketli satirlardaysa ad cikar.
    Dondurur (yeni_cumle | None (dustu), neden | None)."""
    gecen = _gecen_kisiler(cumle, adlar)
    if not gecen:
        return cumle, None
    ad_kok = set().union(*[_kokler(a) for a in gecen])
    ck = _kokler(cumle) - ad_kok - _MADDE_DURAK
    if sonraki or _is_cumlesi(cumle, ck, aksiyonlar):
        sayi = set(re.findall(r"\d+", cumle))
        eslesen = [a for a in aksiyonlar if _madde_eslesir(ck, sayi, a["madde"])]
        kotu = [ad for ad in gecen if not any(_kesin_sahibi(ad, a) for a in eslesen)]
        if not kotu:
            return cumle, None
        if sonraki and eslesen:
            return None, "işin sahibi kesin değil (tabloda '(?)' ile duruyor)"
        return _ad_cikar(cumle, kotu), "kişi dayanaklı bir işin kesin sahibi değil"
    kotu = [ad for ad in gecen if _yalniz_belirsiz_etikette(ad, ck, dizin)]
    if not kotu:
        return cumle, None
    return _ad_cikar(cumle, kotu), "kişiye dayanak yalnız belirsiz etiketli satırlarda"


def not_son_denetim(not_md, transkript, katilimcilar=None, supheli_metin=""):
    """dayanak_kontrolu + sahip dogrulamasindan SONRAKI son denetim (MIMARI_v3 §6, otomatik not: inceleme adimi yok):
    (1) aksiyon tablosu yeniden numaralanir; 'Kişiye göre iş listesi' SON tablodan yeniden kurulur (atilan madde listede
    yok; belirsiz sahip yalniz 'belirsiz' altinda '(aday: Ad)'). (2) Ozet / sonraki adim cumlesinde adi gecen kisi:
    _sahip_denetle. (3) Ozet paragrafi ve konu akisinda ⟨⟩ disindaki transkriptte gecmeyen sayi / tutar / ay-gun (yalniz ⟨⟩ ya da
    supheli satir icinde gecen, ya da hic gecmeyen: uydurma) '(?)' olur; sonraki adimda boyle deger tasiyan cumle duser
    (⟨⟩ icinden ya da yoktan is uretilmez). Sonraki adim bosalirsa kesin
    sahipli aksiyonlardan kurulur. (4) Sablon ek bolumleri (Ilerleme, Fikirler, Seçenekler ...): her madde / tema satirinda
    deger kurali (3) ve ozetteki sahip kurali (2) uygulanir; maddesi kalmayan sablon bolumu notta yer almaz
    (llm.sablon_bolumlerini_temizle). Dondurur (yeni_md, olaylar)."""
    import not_araclari                  # gec: not_araclari motor'u ice aktarir
    md = not_araclari.kisiye_gore_yenile(not_araclari.aksiyon_sirasini_yenile(not_md))
    aksiyonlar = not_araclari.aksiyonlari_ayikla(md)
    dizin = _dayanak_dizini(transkript)
    zemin = _DegerZemini(dizin, supheli_metin)
    adlar = _kisi_adlari(katilimcilar, aksiyonlar)
    olaylar, yeni, bolum, sablon_bolum, ust_dustu = [], [], "", False, False
    md_satirlari = md.split("\n")
    for i, satir in enumerate(md_satirlari):
        s = satir.strip()
        if s.startswith("#"):
            bolum = s.lstrip("# ").strip().lower()
            sablon_bolum = llm.sablon_bolumu_mu(s)
            yeni.append(satir)
            continue
        if not s or s.strip(" -.") == "" or s.startswith("_"):
            yeni.append(satir)
            continue
        if sablon_bolum:
            alt = satir[:1].isspace() and s.startswith(("-", "*"))
            if alt and ust_dustu:
                continue                                   # ust madde dustu: alt maddeleri (artı / eksi) de
            alt_metin = ""
            if not s.startswith(("-", "*")):            # 'Tema:' satiri: altindaki maddeler (sonraki tema / basliga kadar)
                alt = []
                for sonraki in md_satirlari[i + 1:]:
                    if not sonraki.strip().startswith(("-", "*")):
                        break
                    alt.append(sablon_satiri_ayir(sonraki)[1])
                alt_metin = " ".join(alt)
            satir = _sablon_satiri_denetle(satir, bolum, adlar, aksiyonlar, dizin, zemin, olaylar, alt_metin)
            if not alt:
                ust_dustu = satir is None
            if satir is not None:
                yeni.append(satir)
            continue
        if bolum == "özet":
            tutulan = []
            for c in _CUMLE_SONU.split(s):
                c, isaret = _degerleri_isaretle(c, zemin)
                if isaret:
                    olaylar.append(_deger_olayi("özette", isaret, zemin))
                c2, neden = _sahip_denetle(c, adlar, aksiyonlar, dizin, sonraki=False)
                if neden:
                    olaylar.append(f"özet cümlesinden kişi adı çıkarıldı ({neden}): {c[:90]}")
                if c2:
                    tutulan.append(c2)
            yeni.append(" ".join(tutulan) or "-")
        elif bolum == "konu akışı":
            m = _KONU_SATIRI.match(satir)
            if m:
                govde, isaret = _degerleri_isaretle(m.group(2), zemin)
                if isaret:
                    olaylar.append(_deger_olayi("konu akışında", isaret, zemin))
                satir = m.group(1) + govde
            yeni.append(satir)
        elif bolum == "bir sonraki adım":
            tutulan = []
            for c in _SONRAKI_CUMLE.split(s):
                _, isaret = _degerleri_isaretle(c, zemin)
                if isaret:
                    olaylar.append(f"sonraki adımdan çıkarıldı (değer kesin değil: ⟨⟩ dışında geçmiyor: "
                                   f"{', '.join(isaret)}): {c[:90]}")
                    continue
                c2, neden = _sahip_denetle(c, adlar, aksiyonlar, dizin, sonraki=True)
                if neden:
                    olaylar.append(f"sonraki adım {'cümlesi çıkarıldı' if c2 is None else 'cümlesinden kişi adı çıkarıldı'}"
                                   f" ({neden}): {c[:90]}")
                if c2:
                    tutulan.append(c2)
            if tutulan:
                yeni.append(" ".join(tutulan))
            else:
                kesin = [dict(a, sorumlu=a["sahip"]["ad"]) for a in aksiyonlar
                         if a["sahip"]["durum"] == "kesin" and a["madde"].strip(" -")]
                yeni.append(llm.yedek_sonraki_adim({"aksiyonlar": kesin}))
        else:
            yeni.append(satir)
    return llm.sablon_bolumlerini_temizle("\n".join(yeni)), olaylar


def _sablon_satiri_denetle(satir, bolum, adlar, aksiyonlar, dizin, zemin, olaylar, alt_metin=""):
    """Sablon bolumunun bir satiri (madde ya da 'Tema:' satiri): ⟨⟩ disinda gecmeyen sayi / tutar / ay-gun '(?)' olur; maddede
    adi gecen kisi ozet cumlesi kurallarina (_sahip_denetle) uyar. 'Tema:' satirinda adi gecen kisi (tam ad, ilk ad, soyad,
    'K. Soyad'), temanin maddeleri (alt_metin) o kisinin kesin etiketli satirlarinda dayanak bulmuyorsa temadan cikarilir
    ('Kemal Tuncer önerileri:' -> 'Diğer:'). Dondurur yeni satir | None (madde anlamsiz kaldi)."""
    madde = satir.strip().startswith(("-", "*"))
    if madde:
        on, govde, damga = sablon_satiri_ayir(satir)
    else:
        on, govde, damga = "", satir.strip(), ""
    yer = f"'{bolum}' bölümünde"
    govde, isaret = _degerleri_isaretle(govde, zemin)
    if isaret:
        olaylar.append(_deger_olayi(yer, isaret, zemin))
    if madde:
        g2, neden = _sahip_denetle(govde, adlar, aksiyonlar, dizin, sonraki=False)
        if neden:
            olaylar.append(f"{yer} madde{'den kişi adı çıkarıldı' if g2 else ' çıkarıldı'} ({neden}): {govde[:90]}")
        if not g2:
            return None
        if g2 != govde and not govde.endswith((".", "!", "?")):
            g2 = g2.rstrip(".")                            # madde satirina _ad_cikar'in cumle noktasi eklenmez
        govde = g2
    elif govde.endswith(":"):
        tema = govde[:-1].strip()
        gecen = _gecen_kisiler(tema, adlar)
        if gecen:
            ck = _kokler(sarmal_sil(alt_metin)) - set().union(*[_kokler(a) for a in gecen]) - _MADDE_DURAK
            kotu = [ad for ad in gecen if not _tema_kisisi_kesin(ad, ck, dizin)]
            if kotu:
                yeni = _ad_cikar(tema, kotu)
                olaylar.append(f"{yer} tema satırından kişi adı çıkarıldı (kişiye dayanak kesin etiketli satırda "
                               f"yok): {tema[:90]}")
                govde = (yeni.rstrip(".") if yeni else "Diğer") + ":"
    return on + govde + (f" {damga}" if damga else "")


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
    if s.get("supheli_neden") == "cizelge":
        return "Bilinmeyen"                              # konusmaci karari ne olursa olsun: cizelge suphesi etiket zorlar
    ad = gorunen_ad(s)
    if s.get("kaynak") == "altyazi-yedek":
        ad += " (altyazıdan, düşük güven)"
    return ad


def nota_girer(s):
    """Satir nota / ozete / dayanaga girer mi? Icsel halusinasyon supheli ('ic') satirlar girmez (canlı ekranda soluk
    gorunur, transkriptte kalir); altyazi yedegi 'supheli' isaretlidir ama bilincli olarak '(altyazıdan, düşük güven)'
    ile girer. Zaman cizelgesi suphesi ('cizelge': altyazi canliyken etkinlik yok) icerigi dusurmez: nota girer, yalniz
    'Bilinmeyen' etiketiyle (sahip / kesin ad uretmez). Kural metin.nota_girer'de (rag.py ayni kurali kullanir)."""
    return metin_nota_girer(s)


SARMAL_AC, SARMAL_KAPA = "⟨", "⟩"      # belirsiz metin isareti: karar/aksiyon/sayi/tarih bunun icinden uretilmez
_SARMAL = re.compile(SARMAL_AC + r"[^" + SARMAL_KAPA + r"]*" + SARMAL_KAPA)


def sarmal_sil(metin):
    """Metindeki ⟨...⟩ bolumlerini atar (dayanak / sahip kaniti saymaz)."""
    return _SARMAL.sub(" ", metin or "")


_KENAR = ".,;:!?\"()[]{}…«»“”-–—"


def belirsiz_isaretle(metin, kelimeler):
    """Metinde 'kelimeler'den biriyle (uzun.kelime_norm esitligi: buyuk / kucuk harf, noktalama, kesme farki yok) eslesen
    belirtecleri ⟨⟩ icine alir; bas / son noktalama disarida kalir, zaten ⟨⟩ icindeki bolume dokunulmaz, ardisik
    isaretli belirtecler tek ⟨⟩ olur ('⟨Kaan⟩, sen ...', '... ⟨231⟩ için'). Satirin kendisi (raw / text) degismez:
    yalniz nota / LLM'e / dayanaga giden gorunum."""
    hedef = {uzun_mod.kelime_norm(k) for k in (kelimeler or []) if isinstance(k, str)} - {""}
    if not hedef or not metin:
        return metin
    parcalar, icinde = [], False
    for p in re.split(r"(\s+)", metin):
        if not p or p.isspace() or icinde or SARMAL_AC in p or SARMAL_KAPA in p:
            if SARMAL_AC in p:
                icinde = p.rfind(SARMAL_AC) > p.rfind(SARMAL_KAPA)
            elif SARMAL_KAPA in p:
                icinde = False
            parcalar.append(p)
            continue
        cekirdek = p.strip(_KENAR)
        if cekirdek and uzun_mod.kelime_norm(cekirdek) in hedef:
            i = p.find(cekirdek)
            p = p[:i] + SARMAL_AC + cekirdek + SARMAL_KAPA + p[i + len(cekirdek):]
        parcalar.append(p)
    return re.sub(SARMAL_KAPA + r"(\s+)" + SARMAL_AC, r"\1", "".join(parcalar))


def _uzun_isaretli(s):
    """Satir metni, kisa ile uzun pencerenin uyusmadigi kelimeler (uzun_belirsiz) ⟨⟩ icinde (MIMARI_v3 §4)."""
    return belirsiz_isaretle(str(s.get("text") or ""), s.get("uzun_belirsiz"))


def satir_metni(s):
    """Satirin nota/LLM'e giden metni: eszamanli konusma (cakisma) satirinin tamami ⟨⟩ icine alinir; kisa klip ile uzun
    pencerenin farkli verdigi kelimeler (uzun_belirsiz: uzun metin kazandiysa uzunun, sayi kaybinda kisanin uyusmayan
    kelimeleri) ⟨⟩ icinde: dayanak / sahip kaniti sayilmaz, LLM onlardan karar / aksiyon / sayi uretmez."""
    t = str(s.get("text") or "")
    if s.get("cakisma") and t.strip():
        return SARMAL_AC + t.replace(SARMAL_AC, "").replace(SARMAL_KAPA, "") + SARMAL_KAPA
    return _uzun_isaretli(s)


def blok_metni(satirlar):
    """Transkript blogu (LLM girdisi, dayanak kontrolu): her satir 'gorunen_ad: metin'. Ardisik ayni konusmaci
    tek satirda birlesir. cakisma satirlari ⟨⟩ icinde; supheli (icsel halusinasyon) satirlar girmez, cizelge suphesi
    olanlar 'Bilinmeyen:' etiketiyle girer."""
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
                 davet=None, sablon_neden=None):
        """terimler: paylasilan terim.TerimHavuzu (isler/app/toplanti verir); verilmezse bu toplantiya ozel, toplanti
        klasorundeki bir havuz (replay/test: uygulama klasorundeki terimler/ dosyasina dokunulmaz).
        davet: {"govde": davet metni, "ekler": [dosya adlari]} (outlook.aktif_toplanti()['davet']).
        sablon: not sablonu (llm.SABLONLAR) ya da 'otomatik' (davet konusu / metninden secilir: llm.sablon_sec); secilen
        sablon ve nedeni meta.json'a yazilir, toplantiya baglidir (Motor.yukle onu kullanir). sablon_neden: kayitli neden."""
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
        self._olay = olay or (lambda tip, veri: None)
        if sablon_neden and sablon in llm.SABLONLAR:          # kayitli toplanti: meta.json'daki secim
            self.sablon, self.sablon_neden = sablon, sablon_neden
        else:                                                 # not sablonu (llm.SABLONLAR); 'otomatik': davetten
            self.sablon, self.sablon_neden = llm.sablon_sec(
                sablon, baslik, " ".join(x for x in (gundem, self.davet.get("govde")) if x))
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
        self.ses_izi = None                  # Faz 6 spike (config ses_izi, varsayilan kapali): ses_izi_kur ile baglanir
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
        self._kapanis = None                 # ertelenmis parca kapanisi {"neden", "son", "sinir": {id}, "uzun_son",
                                             # "kon_tamam": konusmaci beklemesi bitti (yalniz uzun pencere bekleniyor)}
        self.uzun_pencere = False            # Faz 3: ses servisi uzun pencere acik ({"tur": "uzun_durum"} olayi)
        self._uzun_kapsam = {}               # akis -> uzun pencere sonucu kesinlesen son an (epoch)
        self.uzun_guncellenen = 0            # uzun pencereyle metni degisen satir
        self.uzun_gec = 0                    # ... kapanmis parcada (parca yeniden ozetlenir)
        self._bitiyor = False
        self._uretim_suruyor = False         # notu_uret / yeniden_ozetle calisiyor: sablon_degistir reddeder
        self._konular = []                   # kanonik konu listesi (parca ozetlerinden; transkriptte dayanagi olanlar)
        self._konu_havuzu = []               # [(tur, madde, konu)]: parca ozetlerinin maddeleri (konu_fn eslesmesi)
        self._goc = False                    # Motor.yukle: surumsuz satirlar okunurken goc eder
        self.stt_kesik = False               # STT kesintisi (isler: SesServisi.stt_durumu)
        self._stt_eksik = []                 # ses servisinin hic cozulemeyecek araliklari [[bas, bit]]
        self._bosluklar = []                 # [(akis, bas, bit)] cozulmemis klip araliklari ('bosluk' olaylari)
        self._yuklu_eksik = []               # yuklenen kaydin meta.json'daki stt_eksik'i
        self._yedek_son = {}                 # (konusmaci, metin) -> altyazi yedegi satir id'si (buyuyen satir)
        self._kapsama_yok_bas = None
        self._kapsama_uyarildi = False
        self._atama_halka = OrderedDict()    # klip_no -> (karar ani, '?' mi, kapsama): son ATAMA_PENCERE_SN loopback kararlari
        self._atama_uyarildi = False         # B5: 'konusmaci atanamiyor' uyarisi acik
        self._atama_son = None               # son uyari / yeniden deneme ani (ATAMA_PENCERE_SN beklenir)
        self._atama_olcut = None             # acik uyarinin olcutu: soru | kesin_az | gecikme
        self._gecikme_supheli_bas = None     # konusmaci durum()['L_supheli'] ilk gorulme ani
        self.yeniden_bul_fn = None           # isler: Yakalayici.yeniden_bul() -> atama basarisizsa panel yeniden aranir
        self._lb_ses_bit = None              # son loopback klibinin sesli bitisi (epoch): yakalayici panel canliligi icin
        self._lb_sesli = []                  # K1b: suzgeci gecen (supheli olmayan) loopback kliplerinin [bas, bit] araliklari
        self.ses_olay_sayisi = 0             # gelen klip/bosluk olayi (isler: 'yakalama basladi')
        self.havuz = cf.ThreadPoolExecutor(max_workers=paralel())
        self.isler = []
        llm.GUNLUK_FN = self._llm_gunluk
        llm.UYARI_FN = lambda m: self.log(f"  ! LLM: {m}")
        saat.LOG_FN = lambda m: self.log(f"  ! {m}")       # duvar saati adimi (bir kez)
        llm.SON_HATA = None                  # arayuz 'Not eksik' satirinda bu toplantinin son LLM hatasini gosterir
        # Var olan kaydi yuklerken durumunu koru: eskiden Gecmis'te bir notu yalnizca ACMAK bile toplantiyi
        # 'devam' (yarim) olarak isaretliyordu (ve saklama suresi 'tamam' kayitlari bulamiyordu).
        onceki, kayitli_sablon = None, None
        try:
            with open(os.path.join(klasor, "meta.json"), encoding="utf-8") as f:
                eski_meta = json.load(f)
            onceki, kayitli_sablon = eski_meta.get("durum"), eski_meta.get("sablon")
        except Exception:
            pass
        if kayitli_sablon != self.sablon and (self.sablon != "genel" or self.sablon_neden.startswith("otomatik")):
            self.log(f"Not şablonu: {llm.sablon_etiketi(self.sablon)} ({self.sablon_neden})")
        self.meta_yaz(onceki or "devam")

    # ---------- olay/log ----------
    def log(self, m):
        self._olay("log", m)

    def olay_kaydet(self, mesaj, **ek):
        """Log (Olaylar paneli) + olaylar.jsonl: {"tur": "log", "kaynak": "yakalayici", "mesaj", **ek}. Yakalayici
        (log_fn) ve atama denetimi kullanir; alan metin icermez (ad / konusma yok). Hata yutulur."""
        self.log(mesaj)
        kayit = {"tur": "log", "kaynak": "yakalayici", "mesaj": str(mesaj)}
        kayit.update(ek)
        self._konusmaci_olayi(kayit)

    def son_loopback_ses(self):
        """Son loopback klibinin SESLI bitisi (epoch) | None: yakalayici bu aralikta hoparlorde konusma var mi diye
        bakar (altyazi paneli donmus mu?). Klip olayi yolunda (ses_olayi) guncellenir; yalniz icsel halusinasyon
        suzgecini gecen (supheli olmayan, hal_ic < hal_at) klipler sayilir (K1b)."""
        return self._lb_ses_bit

    def loopback_konusma_sn(self, bas_epoch):
        """K1b: bas_epoch'tan bu yana hoparlorde SESLI loopback konusmasi (sn; suzgeci gecen kliplerin birlesimi).
        Yakalayici panel donukluguna karar verirken tek kisa halusinasyon klibiyle degil, >= DONUK_SES_SN konusmayla
        tetiklenir."""
        try:
            bas = float(bas_epoch)
        except (TypeError, ValueError):
            return 0.0
        top, ux, uy = 0.0, None, None
        for a, b in self._lb_sesli:
            a, b = max(a, bas), b
            if b <= a:
                continue
            if ux is None or a > uy:
                if ux is not None:
                    top += uy - ux
                ux, uy = a, b
            elif b > uy:
                uy = b
        if ux is not None:
            top += uy - ux
        return round(top, 3)

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
                     "kaynak": self.kaynak, "sablon": self.sablon, "sablon_neden": self.sablon_neden})
        if self.davet:                        # terim kaniti (toplanti sonu ogrenme, Motor.yukle ile devam)
            meta["davet"] = self.davet
        meta.update(ek)
        with open(yol, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)

    def sablon_degistir(self, sablon, neden="elle seçildi"):
        """Arayuz (Canli 'Tür' kutusu / Not secicisi): toplantinin sablonu elle degisir, meta.json'a yazilir; yakalama
        surerken de cagrilabilir: sonraki parcalar yeni sablonla ozetlenir, baska sablonla ozetlenmis onceki parcalar
        notu_uret'te yeniden ozetlenir (log bunu soyler). neden: meta.json 'sablon_neden'. Dondurur: degisti mi."""
        sablon = llm.sablon_adi(sablon)
        if sablon == self.sablon:
            return False
        if self._uretim_suruyor:             # calisan not uretimi ortada sablon degistirmez (karisik not)
            self.log("Not şablonu değiştirilmedi: not üretimi sürüyor; bitince tekrar dene")
            return False
        if self.transkript_silindi():        # konusma yok: yeni sablonla ozetlenemez, eski ozetler bozulmasin
            self.log("Not şablonu değiştirilmedi: konuşma verisi saklama süresiyle silinmiş (not ve özetler korunur)")
            return False
        eski, self.sablon, self.sablon_neden = self.sablon, sablon, neden or "elle seçildi"
        durum = None
        try:
            with open(os.path.join(self.klasor, "meta.json"), encoding="utf-8") as f:
                durum = json.load(f).get("durum")
        except Exception:
            pass
        self.meta_yaz(durum or "devam")
        ek = ""
        if self.parca_no:
            ek = (f"; sonraki parçalar yeni şablonla özetlenir, önceki {self.parca_no} parça not üretilirken yeniden "
                  f"özetlenir")
        self.log(f"Not şablonu elle değişti: {llm.sablon_etiketi(eski)} → {llm.sablon_etiketi(sablon)}{ek}")
        return True

    @staticmethod
    def seri_son_sablon(baslik, kok=None, haric=None):
        """Seri hafizasi (Canli 'Tür' kutusu): ayni slug(baslik) ile kaydedilmis SON toplantinin sablonu, yalniz o toplantida
        elle secildiyse (meta.json 'sablon_neden' 'elle' iceriyor). Dondurur (sablon, neden) | None. haric: atlanacak klasor
        (acik toplantinin kendisi)."""
        kok = kok or KOK
        seri = slug(baslik)
        if not baslik or not os.path.isdir(kok):
            return None
        haric = os.path.realpath(haric) if haric else None
        adaylar = []
        for ad in os.listdir(kok):
            k = os.path.join(kok, ad)
            if not os.path.isdir(k) or (haric and os.path.realpath(k) == haric):
                continue
            try:
                with open(os.path.join(k, "meta.json"), encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                continue
            if not isinstance(meta, dict) or slug(meta.get("baslik") or "") != seri:
                continue
            adaylar.append((str(meta.get("tarih") or ""), ad, meta))
        if not adaylar:
            return None
        tarih, _, meta = max(adaylar, key=lambda x: (x[0], x[1]))
        neden = str(meta.get("sablon_neden") or "")
        if meta.get("sablon") not in llm.SABLONLAR or "elle" not in tr_kucuk(neden):
            return None
        if tr_kucuk(neden).startswith("seri:"):
            return meta["sablon"], neden          # zincir: elle secimin yapildigi ilk toplantinin tarihi korunur
        return meta["sablon"], f"seri: {tarih} toplantısında elle seçildi"

    def transkript_silindi(self):
        """Saklama suresi bu toplantinin konusma verisini sildi mi (meta.json 'transkript_silindi')? Diskten okunur: acik
        motorun toplantisi saklama turunda temizlenmis olabilir."""
        try:
            with open(os.path.join(self.klasor, "meta.json"), encoding="utf-8") as f:
                return bool(json.load(f).get("transkript_silindi"))
        except Exception:
            return False

    def _sablon_kw(self, sablon=None):
        # 'genel': eski imzayla cagri (bolum_ozetle yerine gecen yedek / test islevleri sablon almaz)
        sablon = sablon or self.sablon
        return {"sablon": sablon} if sablon != "genel" else {}

    def _sablon_damgala(self, ozet, sablon=None):
        """Parca ozetine sablon alanlari + hangi sablonla ozetlendigi (sablon degisirse notu_uret yeniden ozetler)."""
        sablon = sablon or self.sablon
        if ozet is not None and sablon != "genel":
            for alan, _, _ in llm.sablon_alanlari(sablon):
                ozet.setdefault(alan, [])
            ozet["sablon"] = sablon
        return ozet

    def _sablon_farkli(self, ozet, sablon=None):
        return (ozet.get("sablon") or "genel") != (sablon or self.sablon)

    def _sablon_listeleri(self, listeler, satirlar, sablon=None):
        """llm.birlestir liste_fn: calistay'da kaynak satirinda acik uzlasma olmayan karar Fikirler'e (loglu)."""
        if (sablon or self.sablon) != "calistay":
            return listeler
        listeler, olaylar = calistay_kararlari_ayikla(listeler, satirlar)
        for o in olaylar:
            self.log(f"  ! {o}")
        return listeler

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
            if tur == "uzun_durum":             # Faz 3: uzun pencere acik/kapali (parca kapanisi bekler mi)
                self.uzun_pencere = bool(olay.get("acik"))
                return None
            if tur == "uzun":                   # Faz 3: uzun pencere sonucu (klip metni; konusmaci degismez)
                self._uzun_uygula(olay)
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
        hal_ic = olay.get("hal_puani")
        hal_ic = float(hal_ic) if isinstance(hal_ic, (int, float)) and not isinstance(hal_ic, bool) else 0.0
        if (akis == "loopback" and (olay.get("text") or "").strip() and not olay.get("supheli")
                and hal_ic < hal_esik(self._hal_esikler, "hal_at")):
            # K1/K1b: son_loopback_ses() / loopback_konusma_sn(): yalniz icsel suzgeci gecen klip (halusinasyon klibi
            # sessiz panelde sahte 'donuk' tetiklemesin)
            self._lb_ses_bit = max(self._lb_ses_bit or 0.0, bit)
            if self._lb_sesli and bas < self._lb_sesli[-1][0]:
                self._lb_sesli.append([bas, bit])
                self._lb_sesli.sort()
            else:
                self._lb_sesli.append([bas, bit])
            del self._lb_sesli[:-400]
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
        hal_puani/rms: ses.py halusinasyon puani ve sesli aralik RMS'i; hal_puani satirda hal_ic olarak da saklanir
        (cizelge kurali yalniz hal_puani'ni oynatir, hal_ic degismez)."""
        self._satir_no += 1
        satir = {"surum": SURUM, "id": self._satir_no, "klip_no": klip_no, "akis": akis,
                 "bas": round(bas, 3), "bit": round(bit, 3), "ts": ts or _ts(bas), "raw": raw, "text": raw,
                 "speaker": "?", "spk_guven": 0.0, "adaylar": [], "devir": False, "cakisma": False,
                 "supheli": supheli, "belirsiz": [], "duzeltmeler": [], "kaynak": kaynak, "durum": "beklemede"}
        if isinstance(hal_puani, (int, float)) and not isinstance(hal_puani, bool):
            satir["hal_puani"] = satir["hal_ic"] = round(float(hal_puani), 4)    # hal_ic: ses.py icsel puani (silme yalniz bundan)
        if supheli and kaynak == "ses":
            satir["supheli_neden"] = "ic"
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

    @staticmethod
    def _hal_ic(satir):
        """Satirin icsel halusinasyon puani (ses.py): hal_ic, yoksa ilk hal_puani; yoksa 0."""
        for ad in ("hal_ic", "hal_puani"):
            v = satir.get(ad)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
        return 0.0

    def _cizelge_puanla(self, satir, k):
        """Atama sonrasi halusinasyon yeniden puani (cizelge kurali). SILME yalniz icsel puandan (hal_ic >= hal_at):
        zaman cizelgesi klibi asla silmez. Cizelge suphesi ('supheli_neden' = 'cizelge') ancak sunlarin hepsinde:
        karar cizelge_canli (altyazi canli, panel donuk degil, klip altyazi zamanindan sonra degismis), cizelge_etkin
        False (aralikta etkinlik yok), hal_ic >= hal_cizelge_taban ve hal_ic + CIZELGE_PUANI >= hal_supheli. Kapsama
        yok / panel_donuk kararlari (cizelge_etkin None) asla supheli yapmaz. Etkinlik sonradan gelirse (gec yukseltme)
        cizelge suphesi kalkar, hal_puani = hal_ic, cizelge_etkin anahtari silinir.
        Dondurur 'at' | dict (kalici alanlara eklenecek degisiklik; {} ise yok) | None (ses klibi degil)."""
        if satir.get("kaynak") != "ses":
            return None
        ic = self._hal_ic(satir)
        if ic >= hal_esik(self._hal_esikler, "hal_at"):
            return "at"
        etkin = self.cizelge_etkin(k)
        if etkin is True:                                # gec yukseltme: altyazida etkinlik bulundu
            if satir.get("cizelge_etkin") is not False:
                return {}
            satir.pop("cizelge_etkin", None)
            degisim = {"cizelge_etkin": None}
            if satir.get("supheli_neden") == "cizelge":
                satir["supheli"], satir["hal_puani"] = False, round(ic, 4)
                satir.pop("supheli_neden", None)
                degisim.update(supheli=False, supheli_neden=None, hal_puani=round(ic, 4))
            return degisim
        if (etkin is not False or satir.get("cizelge_etkin") is False or not k.get("cizelge_canli")
                or k.get("neden") in ("panel_donuk", "kapsama_yok")):
            return {}                                    # (ikinci karar -- bolunemedi vb. -- tekrar saymaz)
        satir["cizelge_etkin"] = False
        degisim = {"cizelge_etkin": False}
        if (not satir.get("supheli") and ic >= hal_esik(self._hal_esikler, "hal_cizelge_taban")
                and ic + CIZELGE_PUANI >= hal_esik(self._hal_esikler, "hal_supheli")):
            puan = round(min(1.0, ic + CIZELGE_PUANI), 4)
            satir.update(supheli=True, supheli_neden="cizelge", hal_puani=puan)
            degisim.update(supheli=True, supheli_neden="cizelge", hal_puani=puan, speaker="?", spk_guven=0.0,
                           devir=False, cakisma=False)
        return degisim

    def _karar_uygula(self, k):
        satir = self._klip_satir.get(k.get("klip_no"))
        if satir is None or satir["id"] in self._silinen:
            return
        yer = self._satir_bul(satir["id"])
        self._atama_kaydet(k)
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
        if hal:
            alan.update(hal)                             # cizelge_etkin / supheli (cizelge) / hal_puani degisimleri
        satir.update(alan)
        for ad in [a for a, v in alan.items() if v is None and a in ("cizelge_etkin", "supheli_neden")]:
            satir.pop(ad, None)                          # kalkan alanlar bellekte anahtar olarak da silinir
        if hal and hal.get("supheli_neden") == "cizelge":    # altyazi canliyken etkinlik yok: nota 'Bilinmeyen' girer
            self.log(f"  · şüpheli klip ({satir['ts']}): altyazı canlıyken etkinlik yok; nota Bilinmeyen olarak girer")
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
        """Konusmaci kapsamasi (altyazi okumasi) KAPSAMA_UYARI_SN'den uzun yoksa arayuze 'kapsama' olayi. Mesaj
        konusmaci durumunun nedenine gore: panel_donuk (panel degismiyor), okuma_yok (okuma dongusu duraklamis),
        digerleri (altyazi kapali / panel bulunamadi)."""
        try:
            d = self.konusmaci.durum()
            var = bool(d.get("kapsama"))
            neden = d.get("neden")
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
            if neden == "panel_donuk":
                mesaj = "Teams altyazı paneli güncellenmiyor (pencere küçültülmüş/arka planda olabilir)"
            elif neden == "okuma_yok":
                mesaj = "altyazı okuma döngüsü duraklıyor"
            else:
                mesaj = "Teams altyazısı kapalı ya da okunamıyor"
            self._olay("kapsama", {"var": False, "bas": self._kapsama_yok_bas, "neden": neden, "mesaj": mesaj})
            self.log(f"! Konuşmacı bilgisi yok: {mesaj} — konuşmacılar 'Bilinmeyen' yazılır")

    def _atama_kaydet(self, k):
        """B5: loopback kararlarinin halkasi (klip_no -> (an, '?' mi, kapsama)); gec guncelleme ayni klibi degistirir."""
        if k.get("at") or (k.get("akis") or "loopback") != "loopback" or k.get("klip_no") is None:
            return
        ek = set(str(k.get("neden") or "").split("+")[1:])
        yapisal = bool(ek & set(ATAMA_YAPISAL)) or k.get("neden") in ("panel_donuk", "kapsama_yok")
        kesin = kesin_ad_mi({"speaker": k.get("speaker"), "spk_guven": k.get("spk_guven"),
                             "kimlik_turu": k.get("kimlik_turu")})
        self._atama_halka[k["klip_no"]] = (saat.simdi(), (k.get("speaker") or "?") == "?", bool(k.get("kapsama", True)),
                                           kesin, yapisal)
        self._atama_halka.move_to_end(k["klip_no"])
        while len(self._atama_halka) > 2000:
            self._atama_halka.popitem(last=False)

    def _atama_denetle(self, simdi):
        """B5 atama basarisizligi: son ATAMA_PENCERE_SN'de >= ATAMA_EN_AZ kapsamali loopback kararinin >= %60'i '?'
        (olcut 'soru': altyazi okunuyor ama konusmalarla eslesmiyor) ya da M2: kesin ad payi < ATAMA_KESIN_ORAN (olcut
        'kesin_az': yanlis / belirsiz adlarla dolu cokuste '?' payi dusuk kalir) ya da konusmaci L'yi >= ATAMA_GECIKME_SN
        supheli bildiriyor (olcut 'gecikme': capraz ilinti farkli gecikme goruyor) -> BIR KEZ 'atama' olayi + log, panel
        yeniden aratilir (yeniden_bul_fn) ve gecikme yeniden olculur (konusmaci.L_yeniden_olc); hala bozuksa
        ATAMA_PENCERE_SN sonra yeniden denenir. Uyari olcutune gore kalkar: '?' payi < %30 / kesin pay >= 2 x
        ATAMA_KESIN_ORAN / L suphesi bitti."""
        for no in [n for n, v in self._atama_halka.items() if simdi - v[0] > ATAMA_PENCERE_SN]:
            del self._atama_halka[no]
        kapsamali = [v for v in self._atama_halka.values() if v[2]]
        n = len(kapsamali)
        oran = (sum(1 for v in kapsamali if v[1]) / float(n)) if n else 0.0
        # M2 kesin_az: yapisal tavanli (isinma, kisa klip, bosluk / kaba bant, L basamagi ...) kararlar disinda
        temiz = [v for v in kapsamali if len(v) > 4 and not v[4]]
        n_temiz = len(temiz)
        kesin_oran = (sum(1 for v in temiz if v[3]) / float(n_temiz)) if n_temiz else 1.0
        try:
            L_supheli = bool(self.konusmaci.durum().get("L_supheli"))
        except Exception:
            L_supheli = False
        if L_supheli:
            if self._gecikme_supheli_bas is None:
                self._gecikme_supheli_bas = simdi
        else:
            self._gecikme_supheli_bas = None
        olcut = None
        if n >= ATAMA_EN_AZ and oran >= ATAMA_ORAN:
            olcut = "soru"
        elif n_temiz >= ATAMA_EN_AZ and kesin_oran < ATAMA_KESIN_ORAN:
            olcut = "kesin_az"
        elif self._gecikme_supheli_bas is not None and simdi - self._gecikme_supheli_bas >= ATAMA_GECIKME_SN:
            olcut = "gecikme"
        sogumus = self._atama_son is None or simdi - self._atama_son >= ATAMA_PENCERE_SN
        if self._atama_uyarildi and olcut is None and self._temiz_mi(n, oran, kesin_oran, L_supheli):
            self._atama_uyarildi, self._atama_olcut = False, None
            self._olay("atama", {"var": True, "oran": round(oran, 3), "kesin_oran": round(kesin_oran, 3), "bas": None})
            self.log("konuşmacı ataması düzeldi")
        elif olcut is not None and sogumus:
            ilk_kez = not self._atama_uyarildi
            self._atama_uyarildi, self._atama_son, self._atama_olcut = True, simdi, olcut
            if olcut == "soru":
                mesaj = (f"! Konuşmacı atanamıyor: altyazı okunuyor ama konuşmalarla eşleşmiyor "
                         f"(son 2 dk %{int(round(oran * 100))} Bilinmeyen) — panel yeniden aranıyor, gecikme "
                         "yeniden ölçülüyor")
            elif olcut == "kesin_az":
                mesaj = (f"! Konuşmacı ataması belirsiz: son 2 dk kararların yalnız %{int(round(kesin_oran * 100))}'i "
                         "kesin — panel yeniden aranıyor, gecikme yeniden ölçülüyor")
            else:
                mesaj = ("! Altyazı gecikmesi değişmiş olabilir: konuşmacılar 'Ad?' yazılıyor — panel yeniden aranıyor, "
                         "gecikme yeniden ölçülüyor")
            self._olay("atama", {"var": False, "oran": round(oran, 3), "kesin_oran": round(kesin_oran, 3), "bas": simdi,
                                 "olcut": olcut, "mesaj": mesaj.lstrip("! ")})
            self.olay_kaydet(mesaj, kaynak="motor", olay="atama", olcut=olcut, oran=round(oran, 3),
                             kesin_oran=round(kesin_oran, 3), karar_sayisi=n, tekrar=not ilk_kez)
            for ad, fn in (("panel yeniden arama", self.yeniden_bul_fn),
                           ("gecikme yeniden ölçümü", getattr(self.konusmaci, "L_yeniden_olc", None))):
                if callable(fn):
                    try:
                        fn()
                    except Exception as e:
                        self.log(f"  ! {ad} başarısız: {e!r}")

    def _temiz_mi(self, n, oran, kesin_oran, L_supheli):
        """Acik atama uyarisi olcutune gore kalkar mi?"""
        if self._atama_olcut == "kesin_az":
            return kesin_oran >= 2.0 * ATAMA_KESIN_ORAN
        if self._atama_olcut == "gecikme":
            return not L_supheli
        return n > 0 and oran < ATAMA_TEMIZ_ORAN

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
            self._atama_denetle(simdi)
            if self._ozet_kirli:
                self._ozetleri_yenile(simdi)
            if self._kapanis is not None:
                self._kapanis_dene(simdi)
            elif self.mevcut and self.mevcut_bas is not None and simdi - self.mevcut_bas >= PARCA_SURE_SN:
                self.parca_kapat("süre")
        self._kesimleri_yap(kes)

    def _uzun_bekliyor_mu(self, s):
        """Faz 3: satirin araligi icin uzun pencere sonucu henuz gelmedi mi (uzun pencere acikken; ses klibi, supheli degil)."""
        if not self.uzun_pencere or s.get("kaynak") != "ses" or s.get("klip_no") is None or s.get("supheli"):
            return False
        bit = s.get("bit")
        if not isinstance(bit, (int, float)):
            return False
        kapsam = self._uzun_kapsam.get(s.get("akis") or "loopback")
        return kapsam is None or bit > kapsam + 1e-6

    def _uzun_uygula(self, olay):
        """Faz 3 uzun pencere sonucu (ses.UzunPencere): klip metni uzun pencerenin hizalanan bolumu olur, kisa ile uzunun
        farkli verdigi kelimeler 'belirsiz'e (ve 'uzun_belirsiz'e) girer; satir verisi duz kalir, nota / LLM'e giden
        gorunumde (satir_metni) bu kelimeler ⟨⟩ icindedir: dayanak / sahip kaniti olmaz. Konusmaci alanlarina ve klip sinirlarina DOKUNULMAZ. raw = yeni metin
        (duzeltme bundan calisir), raw_kisa = ozgun kisa metin. Kapanmis parcadaki satir degistiyse parca yeniden ozet
        kuyruguna girer (_ozet_kirli: OZET_YENI_SN sessizlikten sonra / bitiste); canli ekrana satir_guncelle."""
        akis = olay.get("akis") or "loopback"
        try:
            kapsam = float(olay.get("kapsam"))
        except (TypeError, ValueError):
            kapsam = None
        kirli = set()
        for k in olay.get("klipler") or []:
            satir = self._klip_satir.get(k.get("klip_no"))
            if satir is None or satir["id"] in self._silinen or satir.get("supheli") or satir.get("kaynak") != "ses":
                continue
            yer = self._satir_bul(satir["id"])
            yeni = k.get("text")
            yeni = re.sub(r"\s+", " ", yeni).strip() if isinstance(yeni, str) else None
            belirsiz = [str(b) for b in (k.get("belirsiz") or []) if str(b).strip()]
            if not yeni and not belirsiz:
                continue
            eski_belirsiz = list(satir.get("belirsiz") or [])
            alan = {}
            if yeni and yeni != satir.get("raw"):
                satir.setdefault("raw_kisa", satir.get("raw"))
                satir["raw"] = satir["text"] = yeni
                satir["duzeltmeler"] = []                    # duzeltme yeni metinden (raw) yeniden calisir
                satir["belirsiz"] = list(dict.fromkeys(belirsiz))
                alan.update(raw=yeni, text=yeni, raw_kisa=satir["raw_kisa"], duzeltmeler=[])
            else:
                satir["belirsiz"] = list(dict.fromkeys(eski_belirsiz + belirsiz))
            satir["uzun_belirsiz"] = list(dict.fromkeys(belirsiz))
            if isinstance(k.get("oran"), (int, float)):
                satir["uzun_oran"] = round(float(k["oran"]), 4)
            if not alan and satir["belirsiz"] == eski_belirsiz:
                continue
            alan.update(belirsiz=satir["belirsiz"], uzun_belirsiz=satir["uzun_belirsiz"], uzun_oran=satir.get("uzun_oran"))
            if alan.get("text"):
                self.uzun_guncellenen += 1
            if yer is None:
                continue                                     # yerlesmemis yeniden kesme parcasi: yerine konunca yazilir
            sira = yer[2]
            self._degisti(sira)
            self._jsonl(dict(alan, id=satir["id"], guncelle=True))
            if sira is not None and alan.get("text"):
                self._parca_nesil[sira] = self._parca_nesil.get(sira, 0) + 1
                self._ozet_kirli[sira] = saat.simdi()
                kirli.add(sira)
                self.uzun_gec += 1
            self._olay("satir_guncelle", dict(satir, gorunen=gorunen_ad(satir), uzun_degisen=list(satir["uzun_belirsiz"])))
        if kapsam is not None:
            self._uzun_kapsam[akis] = max(self._uzun_kapsam.get(akis, kapsam), kapsam)
        for sira in sorted(kirli):
            self.log(f"  · uzun pencere sonucu geç geldi: parça {sira} satırları güncellendi, yeniden özetlenecek")

    def _kapanis_dene(self, simdi):
        kp = self._kapanis
        if not kp.get("kon_tamam"):
            bekleyen = [s for s in self.mevcut if s["id"] in kp["sinir"] and self._bekliyor_mu(s)]
            if bekleyen and simdi < kp["son"]:
                return
            if bekleyen:
                self._kapanis_zorla(kp, bekleyen)
            kp["kon_tamam"] = True                   # konusmaci beklemesi bitti (eski davranista parca burada kapanirdi)
        uzun = [s for s in self.mevcut if s["id"] in kp["sinir"] and self._uzun_bekliyor_mu(s)]
        if uzun and simdi < kp.get("uzun_son", simdi):
            return                                   # Faz 3: uzun pencere sonucu en fazla UZUN_BEKLE_SN beklenir
        if uzun:
            self.log(f"! parça kapanışı uzun pencereyi {UZUN_BEKLE_SN} sn bekledi: {len(uzun)} satır kısa metinle "
                     "özetleniyor; sonuç gelirse satırlar güncellenip parça yeniden özetlenir")
        self._parca_kapat_simdi(kp["neden"], kp["sinir"])

    def _kapanis_zorla(self, kp, bekleyen):
        """Konusmaci beklemesi (KAPANIS_BEKLE_SN) doldu: araliktaki bekleyen kararlar zorlanir, kalanlar '?'."""
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

    def parca_kapat(self, neden, zorla=False):
        """Acik parcayi kapatir. Konusmaci karari bekleyen satir varsa kapanis ertelenir (kontrol() en fazla
        KAPANIS_BEKLE_SN bekler, sonra zorlar); erteleme surerken gelen satirlar sonraki parcaya kalir.
        Faz 3 (uzun pencere acik): kapsadigi araligin uzun pencere sonucu da en fazla UZUN_BEKLE_SN beklenir; gelmezse kisa
        metinle kapanir, sonuc sonra gelirse satirlar guncellenir ve parca yeniden ozetlenir (_uzun_uygula)."""
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
            kon = any(self._bekliyor_mu(s) for s in self.mevcut)
            uzun = any(self._uzun_bekliyor_mu(s) for s in self.mevcut)
            if kon or uzun:
                simdi = saat.simdi()
                self._kapanis = {"neden": neden, "son": simdi + KAPANIS_BEKLE_SN, "sinir": sinir}
                if uzun:
                    self._kapanis.update(uzun_son=simdi + UZUN_BEKLE_SN, kon_tamam=not kon)
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
        sablon = self.sablon                       # tek okuma: istek ve damga ayni sablonla
        if self.ses_izi is not None:
            self._ses_izi_isle(veri)               # Faz 6: altyazi kapali '?' satirlar icin ses izi ('Ad?'), ozetten once
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
        anlik = []
        try:
            onceki = self._onceki_baglam(veri["sira"])
            with self._iz_kilit:
                anlik = [dict(s) for s in veri["satirlar"]]
            metin = blok_metni(anlik)
            if metin.strip():
                ozet = llm.bolum_ozetle(metin, veri["sira"], self.baglam(metin), onceki, **self._sablon_kw(sablon))
            else:                                  # yalniz supheli (halusinasyon) satirlar: LLM'e gidecek metin yok
                ozet = llm.normalize_ozet({})
            ozet = self._sablon_damgala(ozet, sablon)
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
        canli = veri["ozet"]
        if canli:
            try:
                canli = self.canli_ozet(canli, anlik)
            except Exception as e:                 # dogrulanamayan ozet canli panele hic gitmez (son not etkilenmez)
                self.log(f"  ! parça {veri['sira']} canlı özet doğrulanamadı: {e!r}")
                canli = llm.normalize_ozet({})
        self._olay("parca_ozetlendi", {"sira": veri["sira"], "ozet": canli, "duzeltme": len(veri["duzeltmeler"])})
        return veri["ozet"]

    def canli_ozet(self, ozet, satirlar):
        """Canli 'canli not' paneline giden bolum ozeti: LLM'in ham bolum ozetinin DOGRULANMIS kopyasi (parca dosyasindaki ham
        ozet degismez; son not kendi denetiminden gecer). Son notla ayni kurallar (MIMARI_v3 §6): karar / aksiyon / soru
        maddesi satir bazli dayanak denetiminden (madde_dayanagi) gecer -- dayanaksiz ya da sayi / tarih / kodu yalniz ⟨⟩
        icinde olan madde gosterilmez, belirsiz madde '(?)' alir; aksiyon sahibi sorumlu_dogrula ile kesinlesir (kesin
        degilse 'Ad (?)' / 'belirsiz'; dayanak satirinin etiketi kesin degilse kesin sahip de '(?)'); bolum ozeti
        cumlelerinde ⟨⟩ disinda gecmeyen deger '(?)' olur, adi gecen kisi kesin sahip kuralina uyar."""
        sat = [s for s in satirlar if nota_girer(s)]
        transkript = blok_metni(sat)
        dizin = _dayanak_dizini(transkript)
        denetle = len(_kokler(sarmal_sil(transkript))) >= 6        # dayanak_kontrolu ile ayni alt sinir

        def karar_ver(madde, soru=False):
            return madde_dayanagi(_KAYNAK_DAMGASI.sub(" ", str(madde or "")).strip(), dizin, soru=soru) if denetle else None

        o = dict(ozet)
        # konu: parcanin etiketi, kanonik ada indirgenmis ve transkriptte dayanakli (yoksa gosterilmez)
        try:
            kanon = self._konu_kanonu()[0]
        except Exception:
            kanon = []
        etiket = llm._etiket_sozlugu(ozet)

        def konu_al(madde, tur, ham=None):
            k = llm.konu_kanonla(ham if ham is not None else etiket.get((tur, llm.normalize_bosluk(madde)), ""), kanon)
            return k if k in kanon else ""
        konu_etiketleri = []
        kararlar = []
        for k in ozet.get("kararlar") or []:
            karar = karar_ver(k)
            if karar != "at":
                kararlar.append(_madde_isaretle(str(k)) if karar and karar[0] else k)
                if konu_al(k, "karar"):
                    konu_etiketleri.append({"tur": "karar", "madde": kararlar[-1], "konu": konu_al(k, "karar")})
        aksiyonlar = []
        for a in sorumlu_dogrula(ozet.get("aksiyonlar") or [], sat, self.ben, katilimcilar=self._kisi_katilimcilari()):
            karar = karar_ver(a.get("madde"))
            if karar == "at":
                continue
            if karar and karar[0]:
                a["madde"] = _madde_isaretle(str(a.get("madde") or ""))
            if karar and karar[1] and a["sahip"]["durum"] == "kesin":
                a["sahip"] = dict(a["sahip"], durum="belirsiz")
                a["sorumlu"] = ", ".join(f"{x.strip()} (?)" for x in a["sahip"]["ad"].split(",") if x.strip())
            konu = konu_al(a.get("madde"), "aksiyon", a.get("konu") or "")
            a.pop("konu", None)
            if konu:
                a["konu"] = konu
            aksiyonlar.append(a)
        o["kararlar"], o["aksiyonlar"] = kararlar, aksiyonlar
        o["acik_sorular"] = [q for q in ozet.get("acik_sorular") or [] if karar_ver(q, soru=True) != "at"]
        konu_etiketleri += [{"tur": "soru", "madde": q, "konu": konu_al(q, "soru")} for q in o["acik_sorular"]
                            if konu_al(q, "soru")]
        o["konu_etiketleri"] = konu_etiketleri
        supheli = " ".join(str(s.get("text") or "") for s in satirlar if not nota_girer(s))
        zemin = _DegerZemini(dizin, supheli)

        def madde_denetle(metin):
            # sablon maddesi: dayanaksiz -> None; belirsiz -> '(?)'; ⟨⟩ disinda gecmeyen deger -> '(?)'
            karar = karar_ver(metin)
            if karar == "at":
                return None
            metin = _degerleri_isaretle(str(metin), zemin)[0]
            return _madde_isaretle(metin) if karar and karar[0] else metin
        for alan, tur in llm.ALAN_TURU.items():
            if alan not in ozet:
                continue
            tutulan = []
            for x in ozet.get(alan) or []:
                if tur == "metin":
                    x = madde_denetle(x)
                elif tur == "konu_durum":
                    # durum / risk ayri denetlenir; konu kanonik ve dayanakli degilse gosterilmez
                    durum = madde_denetle(x.get("durum")) if llm.madde_dolu(x.get("durum")) else ""
                    risk = madde_denetle(x.get("risk")) if llm.madde_dolu(x.get("risk")) else ""
                    konu = konu_al("", "", x.get("konu") or "")
                    x = {"konu": konu, "durum": durum or "", "risk": risk or ""} if konu and (durum or risk) else None
                else:
                    anahtar = "secenek" if tur == "secenek" else "fikir"
                    m = madde_denetle(x.get(anahtar))
                    x = None if m is None else dict(x, **{anahtar: m})
                    if x is not None and tur == "secenek":
                        x["artilar"] = [a for a in map(madde_denetle, x.get("artilar") or []) if a]
                        x["eksiler"] = [e for e in map(madde_denetle, x.get("eksiler") or []) if e]
                if x:
                    tutulan.append(x)
            o[alan] = tutulan
        if self.sablon == "calistay":
            o, _ = calistay_kararlari_ayikla(o, sat)       # canli panel de fikri karar gibi gostermez
        o = llm.secenekleri_dogrula(o)                      # 'secildi' yalniz bolumun kararlarinda karsiligi varsa
        if o.get("ozet"):
            adlar = _kisi_adlari(self.katilimcilar, aksiyonlar)
            tutulan = []
            for c in _CUMLE_SONU.split(str(o["ozet"]).strip()):
                c, _ = _degerleri_isaretle(c, zemin)
                c2, _ = _sahip_denetle(c, adlar, aksiyonlar, dizin, sonraki=False)
                if c2:
                    tutulan.append(c2)
            o["ozet"] = " ".join(tutulan)
        o["dogrulandi"] = True
        return o

    def ses_izi_kur(self, ayar, ses_al_fn, cikarici=None):
        """Faz 6 spike (ses_izi.py): ayar['ses_izi'] aciksa ve cikarici kurulabiliyorsa parca kapanisinda altyazi kapali '?'
        satirlara ses izi yardimi acilir. ses_al_fn(akis, bas, bit) -> 16 kHz ses (isler: halka tampon). Kapali/kurulamazsa
        False (tek log satiri); not hatti etkilenmez."""
        self.ses_izi = ses_izi_mod.kur(ayar, ses_al_fn, ben=self.ben, log=self.log, cikarici=cikarici)
        return self.ses_izi is not None

    def _ses_izi_isle(self, veri):
        """Parca kapanisi (arka plan): kesin satirlardan centroid ogrenir, altyazi kapali '?' satirlari karara baglar. Atanan
        satir speaker=Ad, spk_guven<=0.6 ('Ad?', sahip degil), kaynak_konusmaci 'ses_izi'. Hata not hattini bozmaz."""
        sira = veri.get("sira")
        try:
            with self._iz_kilit:
                anlik = list(veri["satirlar"])
            sonuc = self.ses_izi.parca_isle(anlik)            # agir is kilit disinda
            yazilan = []
            with self._iz_kilit:
                for satir, alan in sonuc:
                    if satir["id"] not in self._silinen and self.ses_izi.uygula(satir, alan):
                        yazilan.append((satir, alan))
                if yazilan:
                    self._degisti(sira)
            for satir, alan in yazilan:
                self._jsonl(dict(alan, id=satir["id"], guncelle=True))
                self._olay("satir_guncelle", dict(satir, gorunen=gorunen_ad(satir)))
            if yazilan:
                self.log(f"  parça {sira}: ses izi {len(yazilan)} '?' satırı 'Ad?' yaptı")
        except Exception as e:
            self.log(f"  ! parça {sira} ses izi atlandı: {e!r}")

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
        girdi_raw = {str(g["id"]): g["raw"] for g in girdi}
        cift, degisen = [], []
        with self._iz_kilit:
            for s in veri["satirlar"]:
                y = harita.get(str(s["id"]))
                if y is None:
                    continue
                if s.get("raw") != girdi_raw.get(str(s["id"])):
                    continue                        # Faz 3: duzeltme surerken metin uzun pencereyle degisti (yeniden isler)
                onceki = (s.get("text"), s.get("duzeltmeler"), s.get("belirsiz"))
                s["text"] = y["text"]
                s["duzeltmeler"] = [dict(d) for d in (y.get("duzeltmeler") or [])]
                s["belirsiz"] = list(dict.fromkeys(list(y.get("belirsiz") or []) + list(s.get("uzun_belirsiz") or [])))
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

    ONCEKI_KONU_TAVAN = 15               # sonraki parcaya verilen kanonik konu adi (en son gorulenler)

    def _parca_ozetleri(self, kadar=None):
        """1..kadar (verilmezse hepsi) parcanin ozetleri (okunamayan / ozetsiz parca atlanir)."""
        ozetler = []
        for no in range(1, (self.parca_no if kadar is None else min(kadar, self.parca_no)) + 1):
            try:
                o = self.parca_oku(no).get("ozet")
            except Exception:
                continue
            if o:
                ozetler.append(o)
        return ozetler

    def _konulari_kur(self, bolumler=None, satirlar=None):
        """Kanonik konu listesi (Motor._konular) ve madde havuzu (konu_fn) parca ozetlerinden (_konu_kanonu); not uretimi
        cagirir. Dondurur: kanonik liste."""
        kanon, havuz = self._konu_kanonu(bolumler, satirlar)
        self._konular, self._konu_havuzu = kanon, havuz
        return kanon

    def _konu_kanonu(self, bolumler=None, satirlar=None):
        """Parca ozetlerinin madde etiketleri, konu durumlari ve 'konular' basliklari llm.konu_kanonu ile tek yazima
        indirgenir ('DWH projesi' / 'DWH'). Konu YALNIZ transkriptte (⟨⟩ disi, supheli satir haric) dayanagi varsa listeye
        girer: ayirt edici koklerinin en az yarisi gecmeli (uydurma konu notta bolum acmaz). Durum degistirmez (canli ozet
        arka planda da cagirir). Dondurur (kanonik liste, madde havuzu)."""
        bolumler = self._parca_ozetleri() if bolumler is None else bolumler
        if satirlar is None:
            satirlar = [x for x in self.tum_satirlar() if nota_girer(x)]
        zemin = llm._konu_kokleri(sarmal_sil(" ".join(satir_metni(x) for x in satirlar)), ayirt=False)

        def dayanakli(konu):
            kk = llm._konu_kokleri(konu)
            return bool(kk) and 2 * len(kk & zemin) >= len(kk)
        return llm.konu_kanonu(llm.konu_adaylari(bolumler), kabul_fn=dayanakli), llm.konu_havuzu(bolumler)

    def konu_fn(self, madde, tur=None):
        """Not maddesinin konusu (karar | soru | aksiyon): parca maddelerine eslesme (llm.konu_eslestir; birlestirmedeki
        _konulari_tasi ile ayni kural) + kanonik listeye indirgeme; kanonda yoksa ya da belirsizse None."""
        return llm.konu_eslestir(madde, tur, self._konu_havuzu, self._konular) or None

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
        # konu etiketi tutarliligi: onceki TUM parcalarin kanonik konu adlari (model ayni konuya yeni ad vermesin)
        konular = llm.konu_kanonu(llm.konu_adaylari(self._parca_ozetleri(sira - 1)))[-self.ONCEKI_KONU_TAVAN:]
        bag = llm.onceki_baglam_hazirla(konular or ozet.get("konular"), [x for x in satirlar if x.strip()],
                                        ozet.get("acik_sorular"))
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
                if not nota_girer(s):                  # icsel supheli (cizelge suphesi nota Bilinmeyen girer, asagida)
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

    @staticmethod
    def _konusmasiz(veri):
        """Parcanin satirlari yok ama ozeti var (saklama suresi satirlari bosaltti): yeniden ozetlenirse ozet bos kalir."""
        return not veri.get("satirlar") and veri.get("ozet") is not None

    def parca_yeniden(self, no):
        """Bir parcayi sifirdan (duzeltme + ozet) yeniden isler. Konusmasi silinmis parcanin ozeti korunur."""
        veri = self.parca_oku(no)
        if self._konusmasiz(veri):
            self.log(f"  ! parça {no}: konuşma verisi yok (saklama süresiyle silinmiş), önceki özeti korunuyor")
            return veri
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
            if self._konusmasiz(veri):             # saklama satirlari bosaltti: ozet bostan yeniden yazilmaz
                self.log(f"  ! parça {no}: konuşma verisi yok (saklama süresiyle silinmiş), önceki özeti korunuyor")
                return
            veri["ozet"] = None
            self._isle(veri, self.duzelt)

        self._uretim_suruyor = True
        try:
            with cf.ThreadPoolExecutor(max_workers=paralel()) as ex:
                list(ex.map(isle, range(1, toplam + 1)))
        finally:
            self._uretim_suruyor = False
        return toplam

    def notu_uret(self, yedekle=False):
        """Parca ozetlerinden notu uretir, not.md'ye yazar. yedekle: var olan not.md (icerigi degisecekse) once
        not_yedekle ile kopyalanir ('Notu yeniden üret', sablon degisimi). Konusma verisi saklama suresiyle silinmis
        toplantinin var olan notu yeniden uretilmez (transkript yok: dogrulanamaz); oldugu gibi dondurulur."""
        yol = os.path.join(self.klasor, "not.md")
        if self.transkript_silindi() and os.path.exists(yol):
            self.log("Not yeniden üretilmedi: konuşma verisi saklama süresiyle silinmiş (transkript olmadan "
                     "doğrulanamaz); kayıtlı not ve parça özetleri korunuyor")
            with open(yol, encoding="utf-8") as f:
                return f.read()
        self._uretim_suruyor = True
        try:
            return self._notu_uret(self.sablon, yedekle)
        finally:
            self._uretim_suruyor = False

    def _notu_uret(self, sablon, yedekle):
        # sablon: calisma boyunca sabit (yerel); self.sablon ortada okunmaz
        bolumler, eksik = [], []
        for no in range(1, self.parca_no + 1):
            veri = self.parca_oku(no)
            eski = veri.get("ozet")
            farkli = eski is not None and self._sablon_farkli(eski, sablon)
            if farkli and self._konusmasiz(veri):
                # saklama satirlari bosaltti: bostan ozet eskisini silerdi; eski ozet kalir, yeni ek alanlar bos
                self.log(f"  ! parça {no}: konuşma verisi yok (saklama süresiyle silinmiş), yeni şablonla "
                         f"özetlenmedi; önceki özeti kullanılıyor")
                farkli = False
            if eski is None or farkli:
                self.adim(f"Parça {no}/{self.parca_no} özetleniyor", no, self.parca_no)
                if eski is None:
                    self.log(f"  parça {no} özetleniyor...")
                else:                                # sablon degisti: map asamasi yeni sablonun alanlarini istemeli
                    self.log(f"  parça {no}: şablon değişti ({llm.sablon_etiketi(eski.get('sablon'))} → "
                             f"{llm.sablon_etiketi(sablon)}), yeniden özetleniyor...")
                self._isle(veri, False)
                veri = self.parca_oku(no)
                if veri.get("ozet") is None and eski is not None:
                    self.log(f"  ! parça {no} yeni şablonla özetlenemedi; önceki özeti kullanılıyor")
                    veri["ozet"] = eski
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
        konular = self._konulari_kur(bolumler, satirlar)              # kanonik, transkriptte dayanakli konular
        uydurma = [k for k in llm.konu_kanonu(llm.konu_adaylari(bolumler)) if llm.konu_kanonla(k, konular) not in konular]
        if uydurma:
            self.log(f"  ! transkriptte dayanağı olmayan {len(uydurma)} konu etiketi kullanılmadı: "
                     f"{', '.join(k[:40] for k in uydurma[:5])}")
        asamalar = {"Kararlar ve aksiyonlar birleştiriliyor": 1, "Özet paragrafı yazılıyor": 2}
        not_md = f"# {self.baslik} — {self.tarih.isoformat()}\n\n" + llm.birlestir(
            bolumler, self.baglam(transkript), self.log, kaynak_fn=kaynak_fn, sablon=sablon,
            katilimcilar=self.katilimcilar,
            aksiyon_fn=lambda aks: self._aksiyonlari_kesinlestir(aks, satirlar),
            liste_fn=lambda v: self._sablon_listeleri(v, satirlar, sablon),
            adim_fn=lambda m: self.adim(m, asamalar.get(m), 4), konular=konular, konu_fn=self.konu_fn)
        self.adim("Dayanak kontrolü", 3, 4)
        bolum_ozetleri = "\n".join(b.get("ozet", "") for b in bolumler)
        not_md, atilan = dayanak_kontrolu(not_md, transkript, ozet_kaynak=bolum_ozetleri)
        for madde in atilan:
            self.log(f"  ! transkriptte dayanağı yok, nottan çıkarıldı: {madde[:90]}")
        supheli = " ".join(str(s.get("text") or "") for s in self.tum_satirlar() if not nota_girer(s))
        not_md, olaylar = not_son_denetim(not_md, transkript, self.katilimcilar, supheli)
        for olay in olaylar:
            self.log(f"  ! {olay}")
        not_md = llm.konu_bolumlerini_temizle(not_md)        # durum sablonu: maddesi kalmayan konu bolumu yazilmaz
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
        kapsamasiz = [s for s in satirlar if s.get("kapsama") is False and s.get("akis") != "mikrofon"]
        if kapsamasiz:
            # altyazi kapaliyken konusmaci bilinemez: okuyan '?' satirlarin nedenini notta gorsun
            uzak = [s for s in satirlar if s.get("akis") != "mikrofon"] or kapsamasiz
            not_md = not_md.rstrip("\n") + (
                f"\n\n_Konuşmacı bilgisi yok: Teams altyazısı kapalıyken konuşulan {len(kapsamasiz)} satırda "
                f"(%{round(100 * len(kapsamasiz) / len(uzak))}) konuşmacı belirlenemedi ve '?' gösterildi._\n")
        self.adim("Not kaydediliyor", 4, 4)
        yol = os.path.join(self.klasor, "not.md")
        if yedekle and os.path.exists(yol):
            try:
                with open(yol, encoding="utf-8") as f:
                    degisti = f.read() != not_md
            except Exception:
                degisti = True
            if degisti:                          # onceki not ezilmeden once kopyalanir (geri donulebilir)
                yedek = self.not_yedekle()
                if yedek:
                    self.log(f"önceki not yedeklendi: {os.path.basename(yedek)}")
        with open(yol, "w", encoding="utf-8") as f:
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
        """sablon: ayardaki deger; yalniz toplantinin kayitli sablonu (meta.json 'sablon') yoksa kullanilir ('otomatik':
        kayitli davet konusu / metninden)."""
        with open(os.path.join(klasor, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        kayitli = meta.get("sablon") if meta.get("sablon") in llm.SABLONLAR else None
        m = cls(klasor, meta["baslik"], dt.date.fromisoformat(meta["tarih"]), terimler,
                meta.get("katilimcilar"), meta.get("gundem", ""), duzelt=duzelt, olay=olay,
                kaynak=meta.get("kaynak", "altyazi"), ben=ben, sablon=kayitli or sablon, davet=meta.get("davet"),
                sablon_neden=(meta.get("sablon_neden") or "kayıtlı şablon") if kayitli else None)
        m._goc = True                        # surumsuz (v2 oncesi) satirlar okunurken goc eder: 'Ad?', sahip yok
        m._yuklu_eksik = [list(a)[:2] for a in (meta.get("stt_eksik") or []) if len(a) >= 2]
        return m


# ---------- calistay: fikir karar degildir (yalniz acik uzlasma) ----------
_UZLASMA = re.compile(r"(?<!\w)(?:karar\s+ver(?:dik|ildi|iyoruz)|kararlaştır(?:dık|ıldı)|kararımız|"
                      r"(?:böyle|bu\s+şekilde|öyle)\s+yapıyoruz|anlaştık|uzlaştık|mutabık(?:ız|kaldık)|"
                      r"onayla(?:dık|ndı)|onaylıyoruz|kesinleş(?:ti|tirdik)|kabul\s+(?:ettik|edildi)|bunu\s+seçtik|"
                      r"bununla\s+gidiyoruz)(?!\w)")


# uzlasma ifadesinin hemen ardindan: soru eki / olumsuzlama ('anlaştık mı', 'seçtik mi diyelim', 'anlaştık diyemeyiz')
_UZLASMA_SORU = re.compile(r"^\s*m[ıiuü](?:y[ıiuü]z|s[ıiuü]n(?:[ıiuü]z)?|d[ıiuü]r|yd[ıiuü]|)(?!\w)")
_UZLASMA_OLUMSUZ = re.compile(r"^\s*(?:\w+\s+)?(?:diyemey\w*|diyeme[mz]\w*|denemez|değil\w*|sayılmaz|sayama\w*|"
                              r"söylenemez|söyleyeme\w*|olmadı|olamaz)(?!\w)")
# ifadeden once ayni cumlede kosul: '-sa/-se' ekli kelime ('çıkarsa', 'olursa', 'uygunsa'), 'eğer', 'halinde' ...
_UZLASMA_KOSUL = re.compile(r"(?<!\w)(?:eğer|şayet|takdirde|durumunda|halinde|\w{2,}s[ae](?:k|m|n|nız|niz|lar|ler)?)"
                            r"(?!\w)")
_KOSUL_DEGIL = {"masa", "kasa", "pasa", "hisse", "kese", "nese", "kısa", "pırasa", "tasa", "bursa", "vesaire", "fırsa"}
# uzlasma ifadesinin yaninda anlam tasimayan sozcuk kokleri: 'Tamam, anlaştık.' ayri yan cumle olsa da maddeye baglanir
_UZLASMA_DOLGU = {"tamam", "evet", "peki", "zaman", "okey", "bunu", "buna", "boyle", "bunda", "bunla", "sekil", "oyle",
                  "hepim", "hepbe", "biz", "bence", "artik", "kesin", "yani", "konud", "konus"}


def _uzlasma_gecerli(cumle, m):
    """Cumledeki uzlasma eslesmesi (m) gercek bir uzlasma mi? Arkasindan soru eki / olumsuzlama ya da onunde (ayni
    cumlede) kosul varsa degildir."""
    sonra, once = cumle[m.end():], cumle[:m.start()]
    if _UZLASMA_SORU.match(sonra) or _UZLASMA_OLUMSUZ.match(sonra):
        return False
    return not any(k.group(0) not in _KOSUL_DEGIL for k in _UZLASMA_KOSUL.finditer(once))


def _cumleler(metin):
    """Metin -> [(cumle, [(bas, son) yan cumle araliklari])]; soru cumlesi ('?' ile biten) donmez (uzlasma degildir)."""
    cikti = []
    for m in re.finditer(r"[^.!?;…]+([.!?;…]*)", metin):
        if "?" in m.group(1):
            continue
        cumle = m.group(0)[:len(m.group(0)) - len(m.group(1))]
        if not cumle.strip():
            continue
        yanlar, bas = [], 0
        for ayrac in re.finditer(r"[,:]|\s(?:ama|fakat|ancak)\s", cumle):
            yanlar.append((bas, ayrac.start()))
            bas = ayrac.end()
        yanlar.append((bas, len(cumle)))
        cikti.append((cumle, [y for y in yanlar if cumle[y[0]:y[1]].strip()]))
    return cikti


def acik_uzlasma_var(metin, madde=None):
    """Metinde (⟨⟩ disinda) acik uzlasma ifadesi var mi ('karar verdik', 'böyle yapıyoruz', 'anlaştık' ...)? Oneri ve
    niyet ('yapalım mı', 'bence', 'karar verelim') uzlasma degildir; soru ('anlaştık mı?', 'seçtik mi diyelim?'),
    olumsuzlama ('anlaştık diyemeyiz', '... değil') ve kosul ('bütçe çıkarsa böyle yapıyoruz') da degildir.
    madde verilirse ifade maddenin koklerini tasiyan yan cumlenin kendisinde ya da onun hemen yanindaki yalniz onay
    tasiyan yan cumlede ('Tamam, karar verdik, pilotu ... yapıyoruz') aranir: satirin baska konudaki uzlasmasi
    ('Takvim konusunda anlaştık; QR ile ödeme fikri de var') bu maddeyi karar yapmaz."""
    t = tr_kucuk(sarmal_sil(metin or ""))
    ak = (_kokler(madde) - _MADDE_DURAK) if madde else set()
    for cumle, yanlar in _cumleler(t):
        gecerli = [m for m in _UZLASMA.finditer(cumle) if _uzlasma_gecerli(cumle, m)]
        if not gecerli:
            continue
        if not ak:
            return True
        madde_yan = [i for i, (b, s) in enumerate(yanlar) if _kokler(cumle[b:s]) & ak]
        for m in gecerli:
            j = next((i for i, (b, s) in enumerate(yanlar) if b <= m.start() < s), None)
            if j is None:
                continue
            if j in madde_yan:
                return True
            b, s = yanlar[j]
            kalan = _kokler(cumle[b:m.start()] + " " + cumle[m.end():s]) - _UZLASMA_DOLGU - _MADDE_DURAK
            if not kalan and (j - 1 in madde_yan or j + 1 in madde_yan):    # yalniz onay tasiyan komsu yan cumle
                return True
    return False


def _kaynak_satirlari(madde, satirlar):
    """Maddeyi (kok ortusmesi >= DAYANAK_SATIR_ESIK) tasiyan transkript satirlari (supheli satirlar haric)."""
    ak = _kokler(madde) - _MADDE_DURAK
    if not ak:
        return []
    return [s for s in satirlar if nota_girer(s) and len(ak & _kokler(sarmal_sil(satir_metni(s)))) / len(ak)
            >= DAYANAK_SATIR_ESIK]


def _fikre_benzer(madde, fikir):
    fk = _kokler(fikir) - _MADDE_DURAK
    return bool(fk) and (len(fk & _kokler(madde)) / len(fk) >= 0.6 or cift_yonlu_benzerlik(madde, fikir) >= 0.6)


def calistay_kararlari_ayikla(listeler, satirlar):
    """Calistay sablonu: karar YALNIZ kaynak satirinda acik uzlasma ifadesi varsa karardir. Uzlasmasiz 'karar' Fikirler'e
    tasinir (benzeri zaten fikirlerdeyse yalniz kararlardan duser: ayni sey iki yerde yazilmaz); uzlasmali karar
    Fikirler'de tekrar edilmez. LLM'in fikir / karar ayrimina birakilmaz. Dondurur (listeler, olaylar)."""
    fikirler = [dict(f) for f in listeler.get("fikirler") or []]
    kararlar, olaylar = [], []
    for k in listeler.get("kararlar") or []:
        govde = re.sub(r"\s+", " ", _KAYNAK_DAMGASI.sub(" ", re.sub(r"\(\?\)", " ", str(k)))).strip()
        benzer = [f for f in fikirler if _fikre_benzer(govde, f.get("fikir", ""))]
        if any(acik_uzlasma_var(satir_metni(s), govde) for s in _kaynak_satirlari(govde, satirlar)):
            kararlar.append(k)
            if benzer:
                fikirler = [f for f in fikirler if f not in benzer]
            continue
        if not benzer:
            fikirler.append({"fikir": govde, "tema": "Diğer"})
        olaylar.append(f"çalıştay: kaynak satırında açık uzlaşma yok, karar değil fikir olarak yazıldı: {govde[:90]}")
    return dict(listeler, kararlar=kararlar, fikirler=fikirler), olaylar


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
    # kisa / uzun pencerenin uyusmadigi kelimeler (uzun_belirsiz, ⟨⟩) kanit sayilmaz: uzun 'Kaan, sen ...' kisada
    # 'Sinan, sen ...' ise hitap kesin degildir
    metin = sarmal_sil(_uzun_isaretli(s))
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


# ---------------------------------------------------------------- KVKK: transkript saklama suresi

SAKLAMA_TRANSKRIPT = ("altyazi.jsonl", "oneriler.json", "duzelt_log.jsonl")      # oneriler.json: eski kayitlar
SAKLAMA_SES_UZANTI = (".wav", ".flac", ".mp3", ".ogg", ".opus", ".m4a", ".aac")
SAKLAMA_KORUNAN_YEDEK = ("not.md", "meta.json", "llm_log.jsonl")                  # bunlarin .yedek-* kopyasi konusma tasimaz
# llm_log.jsonl'de serbest metin alanlari: 'ornek' (madde parcalari), 'hata'/'neden' (sunucu govdesi istegi
# yansitabilir). Kalan alanlar sayi/etiket (token, sure, finish, gorev) ve toplantiya dair icerik tasimaz.
SAKLAMA_LLM_ALAN = ("ornek", "hata", "neden")
SAKLAMA_TUR_ETIKET = (("transkript", "transkript"), ("olcum", "ölçüm kaydı"), ("ses", "ses klibi"),
                      ("dizin", "arama dizini/önbellek"), ("replay", "replay çıktısı"), ("yedek", "transkript yedeği"),
                      ("llm_log", "LLM günlüğü içeriği"))


def _saklama_sil(yol):
    """Dosya/klasor/baglantiyi siler (baglanti izlenmez). -> True: silindi, False: yoktu; silinemezse OSError."""
    if os.path.islink(yol) or os.path.isfile(yol):
        os.remove(yol)
        return True
    if os.path.isdir(yol):
        shutil.rmtree(yol)
        if os.path.exists(yol):
            raise OSError(f"klasör silinemedi: {os.path.basename(yol)}")
        return True
    return False


def _saklama_tarih(klasor, meta):
    """Toplanti tarihi: meta.json 'tarih', yoksa klasor adinin 'YYYY-MM-DD_' oneki; bulunamazsa None."""
    for ham in ((meta or {}).get("tarih"), os.path.basename(klasor)[:10]):
        try:
            return dt.date.fromisoformat(str(ham))
        except (TypeError, ValueError):
            continue
    return None


def _saklama_json_yaz(yol, veri, indent=1):
    gecici = yol + ".tmp"
    with open(gecici, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=indent)
    os.replace(gecici, yol)


def _saklama_parcalar(k, tur):
    """parcalar/*.json: satirlar ve duzeltmeler bosaltilir, ozet kalir. Okunamayan (bozuk) parca dosyasi silinir:
    icinde konusma kalmis olabilir ve zaten kullanilamaz."""
    pk = os.path.join(k, "parcalar")
    if os.path.islink(pk) or not os.path.isdir(pk):
        return
    for f_ in sorted(os.listdir(pk)):
        yol = os.path.join(pk, f_)
        if not f_.endswith(".json") or os.path.islink(yol) or not os.path.isfile(yol):
            continue
        try:
            with open(yol, encoding="utf-8") as f:
                v = json.load(f)
            if not isinstance(v, dict):
                raise ValueError("parca sozluk degil")
        except Exception:
            _saklama_sil(yol)
            tur.add("transkript")
            continue
        if v.get("satirlar") or v.get("duzeltmeler"):
            v["satirlar"], v["duzeltmeler"] = [], []
            _saklama_json_yaz(yol, v)
            tur.add("transkript")


def _saklama_llm_log(k, tur):
    """llm_log.jsonl: SAKLAMA_LLM_ALAN alanlari satirlardan cikarilir (dosya ve sayilar kalir); degisiklik yoksa
    dosyaya dokunulmaz. Cozulemeyen satir (yarim yazim) tutulmaz."""
    yol = os.path.join(k, "llm_log.jsonl")
    if os.path.islink(yol) or not os.path.isfile(yol):
        return
    degisti, cikti = False, []
    with open(yol, encoding="utf-8", errors="replace") as f:
        for ham in f:
            if not ham.strip():
                continue
            try:
                s = json.loads(ham)
            except Exception:
                s = None
            if not isinstance(s, dict):
                degisti = True
                continue
            if any(a in s for a in SAKLAMA_LLM_ALAN):
                s = {a: v for a, v in s.items() if a not in SAKLAMA_LLM_ALAN}
                degisti = True
            cikti.append(json.dumps(s, ensure_ascii=False))
    if degisti:
        gecici = yol + ".tmp"
        with open(gecici, "w", encoding="utf-8") as f:
            f.write("".join(s + "\n" for s in cikti))
        os.replace(gecici, yol)
        tur.add("llm_log")


def _saklama_klasor(k):
    """Bir toplantinin transkript/ses/turev kalintilarini siler; not.md, meta.json, parca ozetleri, llm_log.jsonl
    (icerik alanlari ayiklanir), olaylar.jsonl/cizelge.json (metin yok) kalir. -> (silinen veri turleri, silinemeyen oge sayisi)."""
    tur, hata = set(), 0

    def sil(yol, ad_tur):
        nonlocal hata
        try:
            if _saklama_sil(yol):
                tur.add(ad_tur)
        except OSError:
            hata += 1                              # kilitli/yetkisiz: kayit 'temizlendi' sayilmaz, sonraki turda denenir

    for ad in SAKLAMA_TRANSKRIPT:
        sil(os.path.join(k, ad), "transkript")
    for ad in sorted(os.listdir(k)):
        yol, kucuk = os.path.join(k, ad), ad.lower()
        if ad in ("olcum", "kesinti"):             # olcum/: klip wav, ham STT, okumalar, gercek.jsonl, olcum/kesinti/
            sil(yol, "olcum")
        elif kucuk.startswith("rag_dizin") or kucuk.startswith("uzun"):
            sil(yol, "dizin")                      # rag_dizin.npz (pencere metinlerinin vektorleri), uzun pencere onbellegi
        elif kucuk.startswith("replay") or ad in ("satirlar.jsonl", "olcum.json"):
            sil(yol, "replay")
        elif ".yedek-" in ad and ad.split(".yedek-")[0] not in SAKLAMA_KORUNAN_YEDEK:
            sil(yol, "yedek")                      # tasma temizliginin altyazi/oneri/parcalar yedekleri
        elif os.path.isdir(yol) and not os.path.islink(yol) and ad != "parcalar":
            if any(os.path.exists(os.path.join(yol, x)) for x in ("satirlar.jsonl", "olcum.json")):
                sil(yol, "replay")                 # --cikti ile toplanti klasorune yazilmis replay sonucu
    for kok_, klasorler, dosyalar in os.walk(k):   # kalan her ses dosyasi (alt klasorler dahil)
        klasorler[:] = [d for d in klasorler if not os.path.islink(os.path.join(kok_, d))]
        for d in dosyalar:
            if d.lower().endswith(SAKLAMA_SES_UZANTI):
                sil(os.path.join(kok_, d), "ses")
    try:
        _saklama_parcalar(k, tur)
        _saklama_llm_log(k, tur)
    except OSError:
        hata += 1
    return tur, hata


def saklama_tara(gun, kok=None, bugun=None, aktif=None):
    """KVKK transkript saklama suresi. `gun` gunden eski toplantilarda konusma iceren her veri silinir: altyazi.jsonl,
    duzelt_log.jsonl, parca satirlari, olcum/ (klip wav, ham STT, okumalar, gercek.jsonl, kesinti kuyrugu), ses dosyalari,
    rag_dizin.npz, uzun pencere onbellegi, replay ciktilari, *.yedek-*; llm_log.jsonl'den serbest metin alanlari ayiklanir.
    not.md, meta.json, parca ozetleri kalir. gun <= 0: kapali.
    aktif: su an kaydedilen/ozetlenen/acik toplanti klasorleri (dokunulmaz). aktif VERILMEZSE (None) cagiran aktif seti
    bilmiyor demektir: yalniz notu uretilmis ('tamam') toplantilar temizlenir; aktif (bos olsa da) verilirse notu
    uretilmemis (yarim/devam/hata) toplantilar da tarihe gore temizlenir.
    Dondurur {'toplanti', 'tamam', 'yarim', 'tur': {tur: toplanti_sayisi}, 'aktif_atlanan', 'hata'} (yalniz sayilar)."""
    sonuc = {"toplanti": 0, "tamam": 0, "yarim": 0, "tur": {}, "aktif_atlanan": 0, "hata": 0}
    kok = kok or KOK
    if not gun or gun <= 0 or not os.path.isdir(kok):
        return sonuc
    bugun = bugun or dt.date.today()
    korunan = {os.path.realpath(a) for a in (aktif or []) if a}
    for ad in sorted(os.listdir(kok)):
        k = os.path.join(kok, ad)
        if os.path.islink(k) or not os.path.isdir(k):
            continue
        meta = None
        try:
            with open(os.path.join(k, "meta.json"), encoding="utf-8") as f:
                meta = json.load(f)
            if not isinstance(meta, dict):
                meta = None
        except Exception:
            meta = None
        tarih = _saklama_tarih(k, meta)
        if tarih is None or (bugun - tarih).days < gun:
            continue
        tamam = bool(meta) and meta.get("durum") == "tamam"
        if not tamam and aktif is None:
            continue                                # aktif set bilinmiyor: notsuz toplantiya dokunma
        if os.path.realpath(k) in korunan:
            sonuc["aktif_atlanan"] += 1
            continue
        try:
            tur, hata = _saklama_klasor(k)
        except OSError:
            sonuc["hata"] += 1
            continue
        sonuc["hata"] += hata
        if tur and meta is not None and not hata and not meta.get("transkript_silindi"):
            meta["transkript_silindi"] = bugun.isoformat()
            try:
                _saklama_json_yaz(os.path.join(k, "meta.json"), meta)
            except OSError:
                sonuc["hata"] += 1
        if tur:
            sonuc["toplanti"] += 1
            sonuc["tamam" if tamam else "yarim"] += 1
            for t in tur:
                sonuc["tur"][t] = sonuc["tur"].get(t, 0) + 1
    return sonuc


def saklama_uygula(gun, kok=None, bugun=None, aktif=None):
    """saklama_tara'nin kisa hali. Dondurur: temizlenen toplanti sayisi."""
    return saklama_tara(gun, kok, bugun, aktif)["toplanti"]


def saklama_ozeti(sonuc, gun):
    """Olaylar satiri: kac toplanti, hangi tur veri, kac toplanti kullanimda oldugu icin atlandi (yalniz sayilar, ad yok).
    Temizlik, atlanan ve hata yoksa None."""
    parcalar = []
    if sonuc["toplanti"]:
        turler = ", ".join(f"{etiket} {sonuc['tur'][t]}" for t, etiket in SAKLAMA_TUR_ETIKET if sonuc["tur"].get(t))
        parcalar.append(f"{sonuc['toplanti']} toplantı temizlendi (notu üretilmiş {sonuc['tamam']}, notsuz "
                        f"{sonuc['yarim']}); silinen veri: {turler}; not ve özetler duruyor")
    if sonuc["hata"]:
        parcalar.append(f"{sonuc['hata']} öğe silinemedi, sonraki turda yeniden denenecek")
    if sonuc.get("aktif_atlanan"):              # suresi dolmus ama su an calisan bir isin toplantisi: sessiz kalmaz
        parcalar.append(f"{sonuc['aktif_atlanan']} toplantı şu an kullanımda olduğu için atlandı, sonraki turda "
                        f"denenecek")
    return f"saklama süresi ({gun} gün): " + "; ".join(parcalar) if parcalar else None


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

