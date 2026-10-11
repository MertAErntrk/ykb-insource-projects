"""
etiketle.py — Faz 0 zemin gercegi etiketleyici (konsol; ek paket yok).
olcum/klipler.jsonl'deki klipleri sirayla calar (Windows'ta winsound; baska yerde calma atlanir), ham STT metnini
gosterir, konusmaciyi sectirir, metni kabul/duzelttirir ve olcum/gercek.jsonl'e ekler:
  {klip_no, speaker, cakisma, halusinasyon, text}
Devam edilebilir: etiketli klipler atlanir (--tekrar ile yeniden etiketlenir; dosyada SON kayit gecerlidir).
Konusmaci adaylari olcum/okumalar.jsonl'de gorulen adlardir (klibin zamaninda gorunenler once).

Kullanim:
  python tools/etiketle.py toplantilar/<klasor> [--bas 14:30 | 0] [--bit 14:40 | 10] [--tekrar] [--ben ad]
  --bas/--bit: saat (SS:DD[:SN]) ya da ilk klipten itibaren dakika; boylece 10 dakikalik pencere etiketlenir.

Secimler: sayi = konusmaci; ? = bilinmiyor; a = ayni anda (iki kisi); d = ardisik devir (iki kisi);
s = sessizlik/halusinasyon; y = yeni ad yaz; t = tekrar dinle; g = gec; q = cik.
"""
import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import olcum  # noqa: E402


def zaman_coz(deger, ilk_epoch):
    """'14:30[:00]' (saat) ya da '10' / '10.5' (ilk klipten dakika) -> epoch; bos -> None."""
    if deger is None or str(deger).strip() == "":
        return None
    d = str(deger).strip()
    if ":" in d:
        parca = [int(x) for x in d.split(":")] + [0, 0]
        gun = dt.datetime.fromtimestamp(ilk_epoch).replace(hour=parca[0], minute=parca[1], second=parca[2],
                                                           microsecond=0)
        return gun.timestamp()
    return ilk_epoch + float(d) * 60


def konusmaci_adaylari(okumalar, bas, bit):
    """Okumalarda gorulen konusmacilar; [bas, bit] araligindakiler once (sikliga gore), sonra digerleri."""
    aralikta, hepsi = {}, {}
    for o in okumalar:
        for satir in o.get("gorunen") or []:
            if not satir:
                continue
            kim = satir[0]
            hepsi[kim] = hepsi.get(kim, 0) + 1
            if bas - 5 <= (o.get("bit_epoch") or 0) <= bit + 5:
                aralikta[kim] = aralikta.get(kim, 0) + 1
    ilk = sorted(aralikta, key=lambda k: -aralikta[k])
    return ilk + sorted((k for k in hepsi if k not in aralikta), key=lambda k: -hepsi[k])


def oynat(yol):
    try:
        import winsound
        winsound.PlaySound(yol, winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        pass                                   # Windows disi / ses cihazi yok: yalniz metinle etiketlenir


def sec(adaylar, giris, yazdir, adet=1, ek=None):
    """Numarali listeden `adet` kisi sectirir -> [ad] | None (iptal)."""
    for i, a in enumerate(adaylar, 1):
        yazdir(f"   {i}) {a}")
    cevap = giris(f"   {adet} numara (boşlukla) ya da ad yaz: ").strip()
    if not cevap:
        return None
    sonuc = []
    for p in cevap.replace(",", " ").split():
        sonuc.append(adaylar[int(p) - 1] if p.isdigit() and 1 <= int(p) <= len(adaylar) else p)
    return sonuc[:adet] if len(sonuc) >= adet else None


def etiketle(klasor, bas=None, bit=None, tekrar=False, ben=None, giris=input, yazdir=print, oynat_fn=oynat):
    """Etiketleme dongusu (testler giris/yazdir/oynat_fn degistirir). Dondurur: yeni yazilan etiket sayisi."""
    klipler = olcum.klipler_oku(klasor)
    if not klipler:
        yazdir("olcum/klipler.jsonl yok ya da boş.")
        return 0
    okumalar = olcum.jsonl_oku(os.path.join(klasor, "olcum", "okumalar.jsonl"))
    mevcut = olcum.gercek_oku(klasor)
    sirali = sorted(klipler.values(), key=lambda k: k.get("bas") or 0)
    ilk = sirali[0].get("bas") or 0
    t0, t1 = zaman_coz(bas, ilk), zaman_coz(bit, ilk)
    secili = [k for k in sirali
              if (t0 is None or (k.get("bas") or 0) >= t0) and (t1 is None or (k.get("bas") or 0) < t1)
              and (tekrar or k["no"] not in mevcut)]
    yol = os.path.join(klasor, "olcum", "gercek.jsonl")
    yazilan = 0
    for sira, k in enumerate(secili, 1):
        adaylar = konusmaci_adaylari(okumalar, (k.get("bas") or 0), (k.get("bit") or 0))
        if ben and ben not in adaylar:
            adaylar.append(ben)
        wav = os.path.join(klasor, "olcum", "klipler", f"{k['no']:04d}.wav")
        yazdir(f"\n[{sira}/{len(secili)}] klip {k['no']:04d}  {k.get('akis')}  {k.get('ts')}  {k.get('sure')} sn")
        yazdir(f"   ham: {k.get('raw')}")
        oynat_fn(wav)
        kayit = None
        while kayit is None:
            c = giris("   konuşmacı [sayı/?/a/d/s/y/t/g/q]: ").strip().lower()
            if c == "q":
                return yazilan
            if c == "g":
                break
            if c == "t":
                oynat_fn(wav)
            elif c == "s":
                kayit = {"speaker": "sessizlik", "cakisma": False, "halusinasyon": True, "text": ""}
            elif c in ("?", "a", "d", "y") or (c.isdigit() and 1 <= int(c) <= len(adaylar)):
                if c == "?":
                    kim, cakisma = "?", False
                elif c in ("a", "d"):
                    ikisi = sec(adaylar, giris, yazdir, adet=2)
                    if not ikisi:
                        continue
                    kim, cakisma = (" / " if c == "a" else " → ").join(ikisi), c == "a"
                elif c == "y":
                    kim, cakisma = giris("   ad: ").strip(), False
                    if not kim:
                        continue
                else:
                    kim, cakisma = adaylar[int(c) - 1], False
                metin = giris("   metin (Enter = ham metin doğru): ").strip()
                kayit = {"speaker": kim, "cakisma": cakisma, "halusinasyon": False,
                         "text": metin or (k.get("raw") or "")}
            else:
                yazdir("   anlaşılmadı")
        if kayit is None:
            continue
        kayit = dict(klip_no=k["no"], **kayit)
        with open(yol, "a", encoding="utf-8") as f:
            f.write(json.dumps(kayit, ensure_ascii=False) + "\n")
        yazilan += 1
    yazdir(f"\nbitti: {yazilan} etiket yazıldı -> {yol}")
    return yazilan


def main(argv=None):
    ap = argparse.ArgumentParser(description="Faz 0 klip etiketleyici")
    ap.add_argument("klasor")
    ap.add_argument("--bas", help="başlangıç: SS:DD[:SN] ya da ilk klipten dakika")
    ap.add_argument("--bit", help="bitiş: SS:DD[:SN] ya da ilk klipten dakika")
    ap.add_argument("--tekrar", action="store_true", help="etiketli klipleri de yeniden sor")
    ap.add_argument("--ben", help="mikrofon sahibinin adı (aday listesine eklenir)")
    a = ap.parse_args(argv)
    etiketle(a.klasor, a.bas, a.bit, a.tekrar, a.ben)
    return 0


if __name__ == "__main__":
    sys.exit(main())
