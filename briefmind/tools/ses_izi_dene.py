"""
ses_izi_dene.py — Faz 6 spike olcumu (MIMARI_v3 §9 satir 6): ses izi ile altyazi kapali '?' satirlarina yardim.
Faz 0 kaydi (olcum/klipler.jsonl + klipler/NNNN.wav) ve etiketleri (olcum/gercek.jsonl) uzerinde, ses_izi.SesIzi'nin
KURALLARIYLA (esik, marj, en az klip, kesin satirdan centroid) cevrimdisi benzetim yapar ve sunlari raporlar:
  RTF        gomme suresi / ses suresi (kapi: < 0.3)
  kapsama    '?' satirlarin kacina ad atandi
  dogru      '?' satirlarin yuzde kaci DOGRU atandi (kapi: >= %50)
  yanlis     yanlis atanan (kapi: 0; gercek '?' ya da baska kisi olan satira atama da yanlistir)
Her esik icin satir; kapiyi gecen en dusuk (en genis kapsamli) esik onerilir. Cikti yalniz SAYI icerir (ad ve metin yok).

Benzetim varsayimlari (kayitta Teams altyazi karari yok; zemin gercegi altyazi karari yerine kullanilir):
  - Yalniz loopback klipleri, tek kisi etiketli (cakisma / '?' / sessizlik / "A → B" degil), >= 2 sn.
  - Zaman cizelgesi --blok-dk dakikalik bloklara bolunur; her --kapali-her'inci blok 'altyazi kapali' sayilir: oradaki
    klipler '?' olarak sorulur, digerleri 'kesin' sayilir (spk_guven >= 0.9 gibi) ve zamani gelince centroid'e eklenir.
  - Cevrimici: bir '?' klip yalniz kendinden ONCEKI kesin kliplerden kurulu centroid'lerle degerlendirilir.
  - Etiketi '?' olan loopback kliplerde atama yapilirsa yanlis sayilir (bilinmeyen kisiye ad vermek).

Gomme modeli: --model <dosya.onnx> (sherpa-onnx SpeakerEmbeddingExtractor; ayri kurulum: pip install sherpa-onnx).

Kullanim:
  python tools/ses_izi_dene.py toplantilar/<klasor> --model model.onnx [--ben ad] [--blok-dk 3] [--kapali-her 3]
                               [--esikler 0.6,0.65,0.7,0.75,0.8,0.85,0.9] [--marj 0.10] [--min-klip 5] [--json cikti.json]
Cikis kodu: 0 kapi gecti, 1 gecmedi, 2 kullanim / girdi hatasi.
"""
import argparse
import json
import os
import sys
import time
import wave

import numpy as np

_YOL = os.path.dirname(os.path.abspath(__file__))
_KOK = os.path.dirname(_YOL)
for _p in (_YOL, _KOK):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import olcum  # noqa: E402
import ses_izi as ses_izi_mod  # noqa: E402

ESIKLER = (0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9)
KAPI_RTF = 0.3
KAPI_DOGRU = 0.5
KAPI_YANLIS = 0
AZ_ORNEK = 20                   # bundan az '?' klipte "kanit zayif" uyarisi


def wav_oku(yol):
    with wave.open(yol, "rb") as w:
        if w.getframerate() != ses_izi_mod.ORNEK or w.getnchannels() != 1:
            raise ValueError("wav 16 kHz mono olmalı")
        ham = w.readframes(w.getnframes())
    return np.frombuffer(ham, dtype="<i2").astype("float32") / 32767.0


def tek_kisi(speaker, ben_n):
    """Etiket -> normalize ad | None (birlesik / sessizlik / ben / bos). '?' icin '?' doner."""
    s = (speaker or "").strip()
    if not s or any(a in s for a in ("/", "→", "->")):
        return None
    if s == "?":
        return "?"
    n = olcum.tr_normalize(s)
    if not n or n in ("sessizlik", "ben") or (ben_n and n == ben_n):
        return None
    return n


def klipleri_hazirla(klasor, ben=None, min_sn=ses_izi_mod.MIN_SN):
    """Etiketli loopback klipleri (zaman sirali): [{no, bas, sure, kisi}]; kisi normalize ad ya da '?'."""
    klipler, gercek = olcum.klipler_oku(klasor), olcum.gercek_oku(klasor)
    ben_n = olcum.tr_normalize(ben) if ben else None
    out = []
    for no, k in klipler.items():
        g = gercek.get(no)
        if g is None or k.get("akis") != "loopback" or g.get("cakisma") or g.get("halusinasyon"):
            continue
        kisi = tek_kisi(g.get("speaker"), ben_n)
        sure = k.get("sure")
        if kisi is None or not isinstance(sure, (int, float)) or sure < min_sn:
            continue
        out.append({"no": no, "bas": float(k.get("bas") or 0.0), "sure": float(sure), "kisi": kisi})
    out.sort(key=lambda x: (x["bas"], x["no"]))
    return out


def gomme_cikar(klasor, klipler, cikarici):
    """Her klibin wav'indan gomme (sure olculur). Dondurur (vektorler {no: v}, ses_sn, hesap_sn, atlanan)."""
    vek, ses_sn, hesap_sn, atlanan = {}, 0.0, 0.0, 0
    for k in klipler:
        yol = os.path.join(klasor, "olcum", "klipler", f"{k['no']:04d}.wav")
        try:
            ses = wav_oku(yol)
        except (OSError, ValueError, EOFError):
            atlanan += 1
            continue
        if len(ses) > ses_izi_mod.MAKS_SN * ses_izi_mod.ORNEK:
            ofs = (len(ses) - int(ses_izi_mod.MAKS_SN * ses_izi_mod.ORNEK)) // 2
            ses = ses[ofs:ofs + int(ses_izi_mod.MAKS_SN * ses_izi_mod.ORNEK)]
        t0 = time.perf_counter()
        try:
            v = cikarici.vektor(ses)
        except Exception:
            v = None
        hesap_sn += time.perf_counter() - t0
        ses_sn += len(ses) / ses_izi_mod.ORNEK
        if v is None:
            atlanan += 1
        else:
            vek[k["no"]] = v
    return vek, ses_sn, hesap_sn, atlanan


def bolme(klipler, blok_dk, kapali_her):
    """Kliplerin kapali (sorulan) olup olmadigi: {no: bool}. Zaman ilk klipten itibaren blok_dk dakikalik bloklar."""
    if not klipler:
        return {}
    ilk = klipler[0]["bas"]
    sonuc = {}
    for k in klipler:
        blok = int((k["bas"] - ilk) // (blok_dk * 60.0))
        sonuc[k["no"]] = kapali_her > 0 and blok % kapali_her == kapali_her - 1
    return sonuc


def benzet(klipler, vek, kapali, esik, marj=ses_izi_mod.MARJ, min_klip=ses_izi_mod.MIN_KLIP):
    """Tek esik icin cevrimici benzetim (ses_izi.SesIzi kurallari). Dondurur sayac sozlugu + yanlis klip numaralari."""
    izi = ses_izi_mod.SesIzi(ses_izi_mod.Cikarici(), None, esik=esik, marj=marj, min_klip=min_klip)
    r = {"esik": esik, "soru": 0, "cozulen": 0, "dogru": 0, "yanlis": 0, "bilinmeyene": 0, "ogrenilmemis": 0}
    yanlislar = []
    for k in klipler:
        v = vek.get(k["no"])
        if v is None:
            continue
        if not kapali[k["no"]]:
            if k["kisi"] != "?":
                izi.merkezler.ekle(k["kisi"], v)             # kesin satir: centroid'e (zamani geldi)
            continue
        r["soru"] += 1
        if k["kisi"] != "?" and izi.merkezler.klip_sayisi(k["kisi"]) < min_klip:
            r["ogrenilmemis"] += 1                           # kisi henuz taninmiyor: dogru cevap '?' kalmak
        alan = izi.karar_vektor(v)
        if alan is None:
            continue
        r["cozulen"] += 1
        if alan["speaker"] == k["kisi"]:
            r["dogru"] += 1
        else:
            r["yanlis"] += 1
            r["bilinmeyene"] += k["kisi"] == "?"
            yanlislar.append(k["no"])
    r["kapsama"] = round(r["cozulen"] / r["soru"], 4) if r["soru"] else None
    r["dogru_oran"] = round(r["dogru"] / r["soru"], 4) if r["soru"] else None
    r["dogruluk"] = round(r["dogru"] / r["cozulen"], 4) if r["cozulen"] else None
    return r, yanlislar


def oner(satirlar, rtf):
    """Kapiyi gecen en dusuk esik (en genis kapsam) ya da None; (esik | None, nedenler[])."""
    nedenler = []
    if rtf is None or rtf >= KAPI_RTF:
        nedenler.append(f"RTF {rtf} >= {KAPI_RTF}")
    uygun = [s for s in satirlar if s["yanlis"] <= KAPI_YANLIS and s["soru"] > 0]
    if not uygun:
        nedenler.append("hiçbir eşikte yanlış 0 değil")
    gecen = [s for s in uygun if (s["dogru_oran"] or 0) >= KAPI_DOGRU]
    if uygun and not gecen:
        nedenler.append(f"yanlışsız eşiklerde doğru oran < %{int(KAPI_DOGRU * 100)} "
                        f"(en iyi %{max(s['dogru_oran'] or 0 for s in uygun) * 100:.0f})")
    if nedenler:
        return None, nedenler
    return min(s["esik"] for s in gecen), []


def calistir(klasor, cikarici, esikler=ESIKLER, blok_dk=3.0, kapali_her=3, marj=ses_izi_mod.MARJ,
             min_klip=ses_izi_mod.MIN_KLIP, ben=None):
    """Tum olcum -> rapor sozlugu (yalniz sayi)."""
    klipler = klipleri_hazirla(klasor, ben)
    vek, ses_sn, hesap_sn, atlanan = gomme_cikar(klasor, klipler, cikarici)
    kapali = bolme(klipler, blok_dk, kapali_her)
    satirlar, yanlis_klip = [], {}
    for e in esikler:
        r, y = benzet(klipler, vek, kapali, e, marj, min_klip)
        satirlar.append(r)
        yanlis_klip[str(e)] = y
    rtf = round(hesap_sn / ses_sn, 4) if ses_sn > 0 else None
    esik, nedenler = oner(satirlar, rtf)
    kisiler = {}
    for k in klipler:
        if k["kisi"] != "?" and not kapali[k["no"]]:
            kisiler[k["kisi"]] = kisiler.get(k["kisi"], 0) + 1
    soru = satirlar[0]["soru"] if satirlar else 0
    return {"klip": len(klipler), "gomulen": len(vek), "atlanan": atlanan, "ses_sn": round(ses_sn, 1),
            "hesap_sn": round(hesap_sn, 2), "rtf": rtf, "soru": soru,
            "kesin_klip_kisi_basina": sorted(kisiler.values(), reverse=True),
            "blok_dk": blok_dk, "kapali_her": kapali_her, "marj": marj, "min_klip": min_klip,
            "esikler": satirlar, "yanlis_klip": yanlis_klip, "onerilen_esik": esik,
            "kapi": {"gecti": esik is not None, "nedenler": nedenler,
                     "olcut": f"RTF < {KAPI_RTF}, doğru oran >= %{int(KAPI_DOGRU * 100)}, yanlış {KAPI_YANLIS}"},
            "uyari": ([f"'?' klip sayısı az ({soru} < {AZ_ORNEK}): 'yanlış 0' kanıtı zayıf"] if soru < AZ_ORNEK else [])}


def yazdir(rapor, yaz=print):
    yaz(f"klip {rapor['klip']} (gömülen {rapor['gomulen']}, atlanan {rapor['atlanan']}), ses {rapor['ses_sn']} sn, "
        f"gömme {rapor['hesap_sn']} sn -> RTF {rapor['rtf']}")
    yaz(f"'?' (altyazı kapalı) klip: {rapor['soru']}; kesin klip/kişi: {rapor['kesin_klip_kisi_basina']}; "
        f"blok {rapor['blok_dk']} dk, her {rapor['kapali_her']}. blok kapalı; marj {rapor['marj']}, en az klip {rapor['min_klip']}")
    yaz(f"{'eşik':>6} {'çözülen':>8} {'kapsama':>8} {'doğru':>6} {'doğru%':>7} {'doğruluk':>9} {'yanlış':>7} {'bilinmeyene':>12}")
    for s in rapor["esikler"]:
        def yuz(x):
            return "-" if x is None else f"{x * 100:.0f}%"
        yaz(f"{s['esik']:>6.2f} {s['cozulen']:>8} {yuz(s['kapsama']):>8} {s['dogru']:>6} {yuz(s['dogru_oran']):>7} "
            f"{yuz(s['dogruluk']):>9} {s['yanlis']:>7} {s['bilinmeyene']:>12}")
    for u in rapor["uyari"]:
        yaz(f"! {u}")
    if rapor["kapi"]["gecti"]:
        yaz(f"KAPI GEÇTİ ({rapor['kapi']['olcut']}): önerilen eşik {rapor['onerilen_esik']:.2f} "
            "(config 'ses_izi_esik'; sonuç tek kayıttır, ikinci kayıtla doğrulayın)")
    else:
        yaz("KAPI GEÇMEDİ: " + "; ".join(rapor["kapi"]["nedenler"]) + " -> ses izi kapalı kalmalı")


def main(argv=None, cikarici=None):
    ap = argparse.ArgumentParser(description="Faz 6 ses izi ölçümü (RTF, kapsama, doğruluk, eşik önerisi)")
    ap.add_argument("klasor", help="toplantı klasörü (olcum/klipler.jsonl, klipler/*.wav, gercek.jsonl)")
    ap.add_argument("--model", help="gömme modeli (.onnx; sherpa-onnx SpeakerEmbeddingExtractor)")
    ap.add_argument("--ben", help="mikrofon sahibinin adı (etiketlerinde atlanır)")
    ap.add_argument("--blok-dk", type=float, default=3.0, help="zaman bloğu uzunluğu (dk)")
    ap.add_argument("--kapali-her", type=int, default=3, help="her N. blok altyazı kapalı sayılır (0: hiç)")
    ap.add_argument("--esikler", default=",".join(str(e) for e in ESIKLER), help="virgüllü eşik listesi")
    ap.add_argument("--marj", type=float, default=ses_izi_mod.MARJ)
    ap.add_argument("--min-klip", type=int, default=ses_izi_mod.MIN_KLIP)
    ap.add_argument("--json", help="raporu bu dosyaya yaz")
    a = ap.parse_args(argv)
    if not os.path.isfile(os.path.join(a.klasor, "olcum", "klipler.jsonl")):
        print("olcum/klipler.jsonl yok: önce config 'olcum_kaydi' ile kayıt alın (Faz 0).")
        return 2
    try:
        esikler = tuple(float(x) for x in a.esikler.split(",") if x.strip())
    except ValueError:
        print("--esikler sayı listesi olmalı")
        return 2
    if not esikler or a.blok_dk <= 0 or a.kapali_her < 0:
        print("--esikler boş / --blok-dk <= 0 / --kapali-her < 0")
        return 2
    if cikarici is None:
        if not a.model or not os.path.isfile(a.model):
            print("--model var olan bir .onnx dosyası olmalı")
            return 2
        try:
            cikarici = ses_izi_mod.SherpaCikarici(a.model)
        except ImportError:
            print("sherpa-onnx kurulu değil: pip install sherpa-onnx")
            return 2
        except Exception as e:
            print(f"model yüklenemedi: {type(e).__name__}")
            return 2
    rapor = calistir(a.klasor, cikarici, esikler, a.blok_dk, a.kapali_her, a.marj, a.min_klip, a.ben)
    yazdir(rapor)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(rapor, f, ensure_ascii=False, indent=1)
    return 0 if rapor["kapi"]["gecti"] else 1


if __name__ == "__main__":
    sys.exit(main())
