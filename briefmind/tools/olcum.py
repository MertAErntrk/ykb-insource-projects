"""
olcum.py — Faz 0 olcum metrikleri: bir toplanti klasoru (olcum/ kaydi + etiketler) icin WER, SER-kesin, belirsiz oran,
halusinasyon ve klip sayilari. Cikti yalniz SAYI icerir (ad ve metin yok): paylasilabilir.

Girdi (toplanti klasoru):
  olcum/klipler.jsonl   ses.py'nin yazdigi klipler ({no, akis, bas, bit, ornek_*, sure, ts, tsler, raw, ...})
  olcum/gercek.jsonl    etiketle.py'nin yazdigi zemin gercegi ({klip_no, speaker, cakisma, halusinasyon, text})
  parcalar/parca_*.json sistemin son transkript satirlari (yoksa altyazi.jsonl); satir: {id, ts, speaker, text,
                        raw, kaynak}. --satirlar dosya.jsonl ile baska satir dosyasi (replay cikti) verilebilir.
  llm_log.jsonl         --token icin LLM kullanimi

Klip <-> satir eslemesi: satirda `klip_no` varsa dogrudan (Faz1 ekler). Yoksa (akis, ts) ile: satir ts'si klibin
`tsler` listesinde (yoksa [ts, ts+sure] penceresinde) olan klipler aday; birden fazla aday varsa satir metninin
klibin ham STT metniyle ortusmesi (sonra zaman yakinligi) belirler. SINIR: satirda akis yok, iki akis ayni saniyede
klip uretirse ve metinler benzerse yanlis esleme olabilir; `eslesme` sayaclari bunu gosterir. Gece yarisi gecisi
desteklenmez.

Kullanim:
  python tools/olcum.py toplantilar/<klasor> [--token] [--satirlar dosya.jsonl] [--ben ad] [--json cikti.json]
"""
import argparse
import json
import os
import re
import sys
import unicodedata

BELIRSIZ_AYIRAC = "/"          # "A / B": gercek eszamanlilik
DEVIR_AYIRAC = "→"             # "A → B": ardisik devir (kesin sayilmaz, belirsiz da degil)


# ---------------------------------------------------------------- okuma

def jsonl_oku(yol):
    """jsonl -> [dict]; yoksa ya da bozuk satirlar atlanir."""
    sonuc = []
    if not yol or not os.path.exists(yol):
        return sonuc
    with open(yol, encoding="utf-8", errors="replace") as f:
        for ham in f:
            ham = ham.strip()
            if not ham:
                continue
            try:
                v = json.loads(ham)
            except Exception:
                continue
            if isinstance(v, dict):
                sonuc.append(v)
    return sonuc


def klipler_oku(klasor):
    """olcum/klipler.jsonl -> {no: kayit}"""
    return {k["no"]: k for k in jsonl_oku(os.path.join(klasor, "olcum", "klipler.jsonl")) if "no" in k}


def gercek_oku(klasor):
    """olcum/gercek.jsonl -> {klip_no: kayit}; ayni klip birden fazla yazildiysa SON kayit gecerli."""
    sonuc = {}
    for g in jsonl_oku(os.path.join(klasor, "olcum", "gercek.jsonl")):
        if "klip_no" in g:
            sonuc[g["klip_no"]] = g
    return sonuc


def satirlar_oku(klasor):
    """Sistemin son transkript satirlari: parcalar/parca_*.json (LLM duzeltmesi ve konusmaci atamasi dahil);
    parca yoksa altyazi.jsonl (guncelleme kayitlari asil satiri degistirir)."""
    satirlar = []
    pk = os.path.join(klasor, "parcalar")
    if os.path.isdir(pk):
        for ad in sorted(os.listdir(pk)):
            if ad.startswith("parca_") and ad.endswith(".json"):
                try:
                    with open(os.path.join(pk, ad), encoding="utf-8") as f:
                        satirlar.extend(json.load(f).get("satirlar") or [])
                except Exception:
                    continue
    if satirlar:
        return satirlar
    id_ile = {}
    for s in jsonl_oku(os.path.join(klasor, "altyazi.jsonl")):
        if "text" not in s:
            continue
        if s.get("guncelle"):
            h = id_ile.get(s.get("id"))
            if h is not None:
                h.update({k: s[k] for k in ("text", "raw") if k in s})
            continue
        satirlar.append(s)
        if s.get("id") is not None:
            id_ile[s["id"]] = s
    return satirlar


# ---------------------------------------------------------------- metin

def tr_normalize(metin):
    """WER icin: Turkce kucuk harf (I -> ı, İ -> i), noktalama silinir (kesme isareti bitisik kalir), bosluk tekil."""
    m = unicodedata.normalize("NFC", metin or "")
    m = m.replace("İ", "i").replace("I", "ı").lower()
    m = re.sub(r"['’`´]", "", m)
    m = re.sub(r"[^\w\s]|_", " ", m)
    return " ".join(m.split())


def kelimeler(metin):
    n = tr_normalize(metin)
    return n.split() if n else []


def duzenleme_mesafesi(ref, hip):
    """Kelime listeleri arasi Levenshtein (ekleme/silme/degistirme)."""
    if not ref:
        return len(hip)
    onceki = list(range(len(hip) + 1))
    for i, r in enumerate(ref, 1):
        simdiki = [i] + [0] * len(hip)
        for j, h in enumerate(hip, 1):
            simdiki[j] = min(onceki[j] + 1, simdiki[j - 1] + 1, onceki[j - 1] + (r != h))
        onceki = simdiki
    return onceki[-1]


def wer(ref_metin, hip_metin):
    """-> (hata_sayisi, referans_kelime_sayisi)"""
    ref, hip = kelimeler(ref_metin), kelimeler(hip_metin)
    return duzenleme_mesafesi(ref, hip), len(ref)


def _kapsama(satir_metni, klip_metni):
    """Satir kelimelerinin ne kadari klibin ham metninde var (0-1)."""
    a, b = kelimeler(satir_metni), set(kelimeler(klip_metni))
    return sum(1 for k in a if k in b) / len(a) if a else 0.0


def _sn(ts):
    try:
        h, m, s = (ts or "").split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    except Exception:
        return None


# ---------------------------------------------------------------- esleme

def eslestir(klipler, satirlar):
    """-> ({klip_no: [satir]}, sayaclar). Yalniz Whisper kaynakli ('ses') ya da klip_no tasiyan satirlar esler;
    altyazi kaynakli satirlar klibe ait degildir (sayaclar['altyazi'])."""
    bilgi = {}
    for no, k in klipler.items():
        tsler = [_sn(t) for t in (k.get("tsler") or [])]
        tsler = [t for t in tsler if t is not None]
        t0 = min(tsler) if tsler else _sn(k.get("ts"))
        if t0 is None:
            continue
        bilgi[no] = {"t0": t0, "t1": t0 + float(k.get("sure") or 0), "tsler": set(tsler), "raw": k.get("raw") or ""}
    esl = {no: [] for no in klipler}
    say = {"klip_no": 0, "ts_tam": 0, "ts_pencere": 0, "belirsiz_aday": 0, "eslesmeyen": 0, "altyazi": 0}
    for s in satirlar:
        kn = s.get("klip_no")
        if kn is not None and kn in esl:
            esl[kn].append(s)
            say["klip_no"] += 1
            continue
        if s.get("kaynak", "ses") != "ses":
            say["altyazi"] += 1
            continue
        sn = _sn(s.get("ts"))
        if sn is None:
            say["eslesmeyen"] += 1
            continue
        aday = [no for no, b in bilgi.items() if sn in b["tsler"]]
        tur = "ts_tam"
        if not aday:
            aday = [no for no, b in bilgi.items() if b["t0"] - 1 <= sn <= b["t1"] + 1]
            tur = "ts_pencere"
        if not aday:
            say["eslesmeyen"] += 1
            continue
        if len(aday) > 1:
            say["belirsiz_aday"] += 1
            metin = s.get("raw") or s.get("text") or ""
            aday.sort(key=lambda no: (-_kapsama(metin, bilgi[no]["raw"]), abs(bilgi[no]["t0"] - sn)))
        esl[aday[0]].append(s)
        say[tur] += 1
    for l in esl.values():
        l.sort(key=lambda s: (_sn(s.get("ts")) or 0, s.get("id") or 0))
    return esl, say


# ---------------------------------------------------------------- metrikler

def belirsiz_mi(konusmaci):
    k = (konusmaci or "").strip()
    return k in ("", "?") or k.endswith("?") or BELIRSIZ_AYIRAC in k


def devir_mi(konusmaci):
    return DEVIR_AYIRAC in (konusmaci or "")


def _gercek_kisiler(speaker):
    return {tr_normalize(p) for p in re.split(r"[/→]|->", speaker or "") if tr_normalize(p)}


def _oran(a, b):
    return round(a / b, 4) if b else None


def hesapla(klipler, gercek, satirlar, ben=None):
    """Ana hesap. Dondurulen sozluk yalniz sayi/oran icerir."""
    esl, esl_say = eslestir(klipler, satirlar)

    # mikrofon klibinden gelen satirlarin konusmacisi 'ben' sayilir: adi verilmediyse en sik gorulenden cikarilir
    ben_n = tr_normalize(ben) if ben else None
    if not ben_n:
        adlar = {}
        for no, l in esl.items():
            if klipler[no].get("akis") == "mikrofon":
                for s in l:
                    a = tr_normalize(s.get("speaker"))
                    adlar[a] = adlar.get(a, 0) + 1
        ben_n = max(adlar, key=adlar.get) if adlar else None

    etiketli = {no: g for no, g in gercek.items() if no in klipler}
    akis_say = {}
    for k in klipler.values():
        akis_say[k.get("akis", "?")] = akis_say.get(k.get("akis", "?"), 0) + 1

    # WER
    hata = ref_n = hata_ham = ref_ham = wer_klip = 0
    for no, g in etiketli.items():
        if g.get("halusinasyon") or not tr_normalize(g.get("text")):
            continue
        h, n = wer(g["text"], " ".join(s.get("text") or "" for s in esl[no]))
        hata, ref_n, wer_klip = hata + h, ref_n + n, wer_klip + 1
        raw = klipler[no].get("raw")
        if raw is not None:
            h2, n2 = wer(g["text"], raw)
            hata_ham, ref_ham = hata_ham + h2, ref_ham + n2

    # SER-kesin / yanlis aynı anda
    kesin = yanlis = yanlis_ayni_anda = sirali_devir_klip = 0
    for no, g in etiketli.items():
        if g.get("halusinasyon"):
            continue
        kisiler = _gercek_kisiler(g.get("speaker"))
        if "ben" in kisiler and ben_n:
            kisiler = (kisiler - {"ben"}) | {ben_n}
        if DEVIR_AYIRAC in (g.get("speaker") or "") or "->" in (g.get("speaker") or ""):
            sirali_devir_klip += 1
        for s in esl[no]:
            k = s.get("speaker")
            if BELIRSIZ_AYIRAC in (k or "") and not g.get("cakisma"):
                yanlis_ayni_anda += 1
            if belirsiz_mi(k) or devir_mi(k) or not kisiler:
                continue
            kesin += 1
            if tr_normalize(k) not in kisiler:
                yanlis += 1

    # belirsiz oran (tum son satirlar)
    belirsiz = sum(1 for s in satirlar if belirsiz_mi(s.get("speaker")))
    devir = sum(1 for s in satirlar if devir_mi(s.get("speaker")))

    # halusinasyon: sessizlik etiketli klipten satir cikti mi
    hal_klip = [no for no, g in etiketli.items() if g.get("halusinasyon")]
    hal_cikti = [no for no in hal_klip if esl[no]]
    hal_kesin = [no for no in hal_cikti if any(not belirsiz_mi(s.get("speaker")) for s in esl[no])]

    return {
        "klip": {"toplam": len(klipler), "etiketli": len(etiketli), "akis": akis_say,
                 "cakisma_etiketli": sum(1 for g in etiketli.values() if g.get("cakisma"))},
        "satir": {"toplam": len(satirlar), "belirsiz": belirsiz, "devir": devir,
                  "belirsiz_oran": _oran(belirsiz, len(satirlar))},
        "wer": {"klip": wer_klip, "referans_kelime": ref_n, "hata": hata, "oran": _oran(hata, ref_n),
                "whisper_ham_hata": hata_ham, "whisper_ham_referans": ref_ham,
                "whisper_ham_oran": _oran(hata_ham, ref_ham)},
        "ser_kesin": {"kesin_satir": kesin, "yanlis": yanlis, "oran": _oran(yanlis, kesin)},
        "yanlis_ayni_anda": {"satir": yanlis_ayni_anda, "sirali_devir_klip": sirali_devir_klip},
        "halusinasyon": {"klip": len(hal_klip), "satir_cikaran": len(hal_cikti), "kesin_satir_cikaran": len(hal_kesin)},
        "eslesme": esl_say,
    }


def token_orani(klasor):
    """llm_log.jsonl -> karakter/token oranlari (LLM kullanimi; icerik yok).
    cikti: (cevap_karakter + dusunce_karakter) / cikti_token. girdi: kayitta `giris_karakter` varsa
    giris_karakter / sunucu_giris_token; yoksa kodun tahmini (giris_token) ile sunucunun sayimi (sunucu_giris_token)
    karsilastirilir: sunucu / tahmin > 1 ise tahmin (karakter/3) token sayisini eksik sayiyor demektir."""
    kayitlar = jsonl_oku(os.path.join(klasor, "llm_log.jsonl"))
    cikti_kar = cikti_tok = gk = gt = 0
    tah = sun = n_cikti = n_giris = 0
    for k in kayitlar:
        ct = k.get("cikti_token")
        if isinstance(ct, (int, float)) and ct > 0 and isinstance(k.get("cevap_karakter"), (int, float)):
            cikti_kar += k["cevap_karakter"] + (k.get("dusunce_karakter") or 0)
            cikti_tok += ct
            n_cikti += 1
        sg = k.get("sunucu_giris_token")
        if isinstance(sg, (int, float)) and sg > 0:
            if isinstance(k.get("giris_karakter"), (int, float)):
                gk, gt = gk + k["giris_karakter"], gt + sg
                n_giris += 1
            if isinstance(k.get("giris_token"), (int, float)) and k["giris_token"] > 0:
                tah, sun = tah + k["giris_token"], sun + sg
    return {
        "kayit": len(kayitlar),
        "cikti": {"cagri": n_cikti, "karakter_token": round(cikti_kar / cikti_tok, 3) if cikti_tok else None},
        "girdi": {"cagri": n_giris, "karakter_token": round(gk / gt, 3) if gt else None,
                  "sunucu_tahmin_orani": round(sun / tah, 3) if tah else None},
    }


# ---------------------------------------------------------------- cikti

def _yuzde(x):
    return "-" if x is None else f"%{x * 100:.1f}"


def ozet_metin(m):
    k, w, s = m["klip"], m["wer"], m["ser_kesin"]
    h, sa, e = m["halusinasyon"], m["satir"], m["eslesme"]
    satirlar = [
        f"Klip: {k['toplam']} (etiketli {k['etiketli']}); satır: {sa['toplam']}",
        f"WER: {_yuzde(w['oran'])} ({w['hata']}/{w['referans_kelime']} kelime, {w['klip']} klip); "
        f"Whisper ham: {_yuzde(w['whisper_ham_oran'])}",
        f"SER-kesin: {_yuzde(s['oran'])} ({s['yanlis']}/{s['kesin_satir']} kesin satır yanlış)",
        f"Belirsiz: {_yuzde(sa['belirsiz_oran'])} ({sa['belirsiz']} satır); devir satırı: {sa['devir']}",
        f"Yanlış 'aynı anda': {m['yanlis_ayni_anda']['satir']} satır",
        f"Halüsinasyon: {h['klip']} sessizlik klibinden {h['satir_cikaran']} tanesi satır üretti "
        f"(kesin: {h['kesin_satir_cikaran']})",
        f"Eşleme: {e['klip_no']} klip_no, {e['ts_tam']} ts-tam, {e['ts_pencere']} ts-pencere, "
        f"{e['belirsiz_aday']} çok-adaylı, {e['eslesmeyen']} eşleşmeyen",
    ]
    if "token" in m:
        t = m["token"]
        satirlar.append(f"Token: çıktı {t['cikti']['karakter_token']} kar/token ({t['cikti']['cagri']} çağrı); "
                        f"girdi {t['girdi']['karakter_token']} kar/token, sunucu/tahmin {t['girdi']['sunucu_tahmin_orani']}")
    return "\n".join(satirlar)


def calistir(klasor, satirlar=None, ben=None, token=False):
    """Kutuphane girisi (replay.py ve testler): metrik sozlugu. satirlar verilmezse klasorden okunur."""
    m = hesapla(klipler_oku(klasor), gercek_oku(klasor), satirlar if satirlar is not None else satirlar_oku(klasor),
                ben=ben)
    if token:
        m["token"] = token_orani(klasor)
    return m


def main(argv=None):
    ap = argparse.ArgumentParser(description="Faz 0 ölçüm metrikleri (yalnız sayı çıktısı)")
    ap.add_argument("klasor", help="toplantı klasörü (içinde olcum/ var)")
    ap.add_argument("--token", action="store_true", help="llm_log.jsonl'den karakter/token oranı")
    ap.add_argument("--satirlar", help="son satırlar için jsonl (varsayılan: parcalar/ ya da altyazi.jsonl)")
    ap.add_argument("--ben", help="mikrofon sahibinin transkriptteki adı (yoksa mikrofon satırlarından çıkarılır)")
    ap.add_argument("--json", dest="json_dosya", help="JSON çıktıyı bu dosyaya da yaz")
    a = ap.parse_args(argv)
    satirlar = jsonl_oku(a.satirlar) if a.satirlar else None
    m = calistir(a.klasor, satirlar, a.ben, a.token)
    print(ozet_metin(m))
    print(json.dumps(m, ensure_ascii=False, indent=1))
    if a.json_dosya:
        with open(a.json_dosya, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
