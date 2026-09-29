"""
toplanti.py — tek komut: yakala -> (arka planda duzelt + ozetle) -> bitiste inceleme -> not -> Outlook taslagi

  python toplanti.py basla [--baslik "..."] [--outlook] [--sessiz] [--duzeltme-yok] [--otobitir 180] [--inceleme-yok]
      Baslik verilmezse Outlook takviminden aktif toplanti alinir (katilimcilar + gundem de).
      Teams'te altyazi grubu gorunene kadar bekler, gorununce yakalamaya baslar.
      Altyazi grubu --otobitir saniye boyunca kaybolursa (toplantidan cikildi) kendiliginden bitirir;
      Ctrl+C de bitirir. Bitiste supheli terimler konsolda sorulur, sonra not uretilir.
      --inceleme-yok: konsol incelemesi atlanir, not dogrudan uretilir (oneriler oneriler.json'da kalir).
  python toplanti.py bitir toplantilar\\2026-09-14_modeldev-haftalik [--outlook] [--inceleme-yok]
      Yarim kalmis toplantiyi diskten tamamlar (inceleme dahil).
  python toplanti.py liste
"""
import argparse
import datetime as dt
import json
import os
import sys
import time

import llm
import motor as motor_mod
import outlook
from motor import Motor
from sozluk import Sozluk
from yakalayici import Yakalayici, ekran_okuyucu

OKUMA_ARALIK = 0.6
DURUM_ARALIK = 60


# ---------- konsol olaylari ----------

def olay(tip, v):
    if tip == "log":
        print(v, flush=True)
    elif tip == "satir":
        if not olay.sessiz:
            print(f"{v['ts']} | {v['speaker']}: {v['text']}", flush=True)
    elif tip == "parca_kapandi":
        print(f"▶ parça {v['sira']} kapandı ({v['neden']}, ~{v['token']} token, {v['aralik']}) → işleniyor", flush=True)
    elif tip == "parca_ozetlendi":
        o = v["ozet"]
        if o:
            print(f"✔ parça {v['sira']}: {len(o['kararlar'])} karar, {len(o['aksiyonlar'])} aksiyon, "
                  f"{v['duzeltme']} düzeltme", flush=True)
        else:
            print(f"✖ parça {v['sira']} özetlenemedi (bitişte tekrar denenecek)", flush=True)
    elif tip == "oneri":
        print(f"   ? öneri #{v['id']}: '{v['yanlis']}' → '{v['oneri'] or '?'}'", flush=True)


olay.sessiz = False


# ---------- inceleme ----------

def konsol_inceleme(oneriler):
    """Supheli terimleri tek tek sorar. Dondurur {id: dogru | None}."""
    kararlar = {}
    if not oneriler:
        print("İnceleme: şüpheli terim yok.")
        return kararlar
    print(f"\n=== İnceleme: {len(oneriler)} şüpheli terim ===")
    print("Enter = öneriyi onayla | d = kendin yaz | y = yoksay | h = kalanların hepsini onayla | g = kalanları geç\n")
    toplu = None
    for n, o in enumerate(oneriler, 1):
        if toplu == "onayla" and o["oneri"]:
            kararlar[o["id"]] = o["oneri"]
            continue
        if toplu == "gec":
            continue
        print(f"[{n}/{len(oneriler)}] parça {o['parca']} | '{o['yanlis']}' → '{o['oneri'] or '?'}'")
        if o["baglam"]:
            print(f"      bağlam: {o['baglam']}")
        try:
            c = input("      > ").strip()
        except (EOFError, KeyboardInterrupt):
            c = "g"
        if c == "" and o["oneri"]:
            kararlar[o["id"]] = o["oneri"]
        elif c in ("", "y"):
            kararlar[o["id"]] = None
        elif c == "d":
            d = input("      doğru yazım: ").strip()
            kararlar[o["id"]] = d or None
        elif c == "h":
            toplu = "onayla"
            kararlar[o["id"]] = o["oneri"] or None
        elif c == "g":
            toplu = "gec"
        else:
            kararlar[o["id"]] = c          # dogrudan yazilan metin = duzeltme
    return kararlar


def tamamla(m, a_outlook, alicilar=None, inceleme=True):
    oneriler = m.bitir()
    if inceleme:
        kararlar = konsol_inceleme(oneriler)
    else:
        kararlar = {}
        if oneriler:
            print(f"İnceleme atlandı: {len(oneriler)} şüpheli terim oneriler.json'da bekliyor.")
    yeniden = m.kararlari_uygula(kararlar)
    if yeniden:
        print(f"{yeniden} parça güncellendi.")
    not_md = m.notu_uret()
    m.kapat()
    print("\n" + not_md + f"\n\nKaydedildi: {os.path.join(m.klasor, 'not.md')}")
    if a_outlook:
        outlook.taslak(f"Toplantı notu: {m.baslik} ({m.tarih.isoformat()})", not_md, alicilar)


# ---------- komutlar ----------

def komut_basla(a):
    olay.sessiz = a.sessiz
    tarih = dt.date.today()
    ctx = outlook.aktif_toplanti() or {}
    baslik = a.baslik or ctx.get("baslik") or input("Toplantı başlığı: ").strip() or "Toplantı"
    if ctx:
        print(f"Outlook: '{ctx['baslik']}' — {len(ctx['katilimcilar'])} katılımcı, gündem {'var' if ctx.get('gundem') else 'yok'}")
    klasor = motor_mod.yeni_klasor(baslik, tarih)
    m = Motor(klasor, baslik, tarih, Sozluk(), ctx.get("katilimcilar"), ctx.get("gundem", ""),
              duzelt=not a.duzeltme_yok, olay=olay)
    y = Yakalayici()
    print(f"Klasör: {klasor}\nTeams'te altyazı bekleniyor (pencere arkada kalabilir, küçültme). Bitirmek için Ctrl+C.")

    ekran_okuyucu(True)
    basladi, son_durum, uyari = False, time.time(), 0
    try:
        while True:
            for satir in y.oku():
                m.satir_ekle(satir)
            if y.hazir and not basladi:
                print("altyazı bulundu, yakalama başladı.")
                basladi = True
            m.kontrol()
            if basladi and y.kayip_saniye() > 0:
                kalan = int(a.otobitir - y.kayip_saniye())
                if kalan <= 0:
                    print("altyazı kayboldu, toplantı bitti kabul ediliyor.")
                    break
                if kalan // 30 != uyari:
                    uyari = kalan // 30
                    print(f"altyazı görünmüyor, {kalan} sn sonra otomatik bitirilecek (Ctrl+C = şimdi bitir)")
            if time.time() - son_durum > DURUM_ARALIK and basladi:
                biten = sum(1 for i in m.isler if i.done())
                print(f"— {m.parca_no} parça kapandı, {biten} işlendi, açık parça ~{m.mevcut_tok} token, "
                      f"{len(m.bekleyen_oneriler())} öneri —")
                son_durum = time.time()
            time.sleep(OKUMA_ARALIK)
    except KeyboardInterrupt:
        print("\nToplantı bitiriliyor...")
    finally:
        ekran_okuyucu(False)
    for satir in y.bitir():
        m.satir_ekle(satir)
    tamamla(m, a.outlook, ctx.get("katilimcilar"), inceleme=not a.inceleme_yok)


def komut_bitir(a):
    m = Motor.yukle(a.klasor, Sozluk(), olay=olay)
    tamamla(m, a.outlook, m.katilimcilar, inceleme=not a.inceleme_yok)


def komut_liste(a):
    kok = motor_mod.KOK
    if not os.path.isdir(kok):
        print("kayıt yok")
        return
    for ad in sorted(os.listdir(kok)):
        try:
            with open(os.path.join(kok, ad, "meta.json"), encoding="utf-8") as f:
                mt = json.load(f)
            print(f"{ad:45} {mt['durum']:8} {mt['parca']:>3} parça  {mt['baslik']}")
        except Exception:
            print(f"{ad:45} (meta yok)")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    p = argparse.ArgumentParser(description="Teams altyazısından toplantı notu")
    alt = p.add_subparsers(dest="komut", required=True)
    b = alt.add_parser("basla")
    b.add_argument("--baslik")
    b.add_argument("--outlook", action="store_true")
    b.add_argument("--sessiz", action="store_true")
    b.add_argument("--duzeltme-yok", action="store_true", help="arka plan düzeltme geçişini kapat")
    b.add_argument("--otobitir", type=int, default=180, help="altyazı bu kadar sn kaybolunca bitir")
    b.add_argument("--inceleme-yok", action="store_true", help="bitişte şüpheli terimleri sorma, notu doğrudan üret")
    b.set_defaults(f=komut_basla)
    bt = alt.add_parser("bitir")
    bt.add_argument("klasor")
    bt.add_argument("--outlook", action="store_true")
    bt.add_argument("--inceleme-yok", action="store_true", help="şüpheli terimleri sorma, notu doğrudan üret")
    bt.set_defaults(f=komut_bitir)
    alt.add_parser("liste").set_defaults(f=komut_liste)
    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
