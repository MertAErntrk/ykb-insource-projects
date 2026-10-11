"""
toplanti.py — tek komut: yakala -> (arka planda terim/ad duzeltmesi + ozetle) -> bitiste not -> Outlook taslagi

  python toplanti.py basla [--baslik "..."] [--outlook] [--sessiz] [--duzeltme-yok] [--otobitir 180]
      Baslik verilmezse Outlook takviminden aktif toplanti alinir (katilimcilar + gundem + davet terimleri de).
      Teams'te altyazi grubu gorunene kadar bekler, gorununce yakalamaya baslar. Metin sesten (STT), konusmaci
      altyazinin zamanindan gelir; Teams altyazi metni transkripte girmez.
      Altyazi grubu --otobitir saniye boyunca kaybolursa (toplantidan cikildi) kendiliginden bitirir;
      Ctrl+C de bitirir. Bitiste kullanici onayi sorulmaz: belirsiz kelime/konusmaci ozeti yazilir, not uretilir.
      (--inceleme-yok eski bayragi kabul edilir ve yok sayilir: inceleme akisi kaldirildi.)
  python toplanti.py bitir toplantilar\\2026-09-14_modeldev-haftalik [--outlook]
      Yarim kalmis toplantiyi diskten tamamlar (not uretimi dahil).
  python toplanti.py liste
"""
import argparse
import datetime as dt
import json
import os
import sys
import time

import ayar as ayar_mod
import llm
import motor as motor_mod
import outlook
import terim as terim_mod
from motor import Motor
from yakalayici import Yakalayici, ekran_okuyucu

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


olay.sessiz = False


# ---------- bitis ----------

def belirsiz_ozeti(belirsizler):
    """Salt-okunur bilgi (onay sorulmaz): tur basina sayi."""
    if not belirsizler:
        return "Belirsiz kelime/konuşmacı yok."
    sayac = {}
    for b in belirsizler:
        sayac[b.get("tur")] = sayac.get(b.get("tur"), 0) + 1
    ad = {"kelime": "belirsiz kelime", "konusmaci": "emin olunmayan konuşmacı satırı", "supheli": "şüpheli klip (nota girmez)",
          "terim": "bozuk duyulan terim"}
    return "Belirsizler: " + ", ".join(f"{n} {ad.get(t, t)}" for t, n in sorted(sayac.items(), key=lambda x: str(x[0])))


def tamamla(m, a_outlook, alicilar=None):
    belirsizler = m.bitir()
    print(belirsiz_ozeti(belirsizler))
    not_md = m.notu_uret()
    m.kapat()
    print("\n" + not_md + f"\n\nKaydedildi: {os.path.join(m.klasor, 'not.md')}")
    if a_outlook:
        outlook.taslak(f"Toplantı notu: {m.baslik} ({m.tarih.isoformat()})", not_md, alicilar)


# ---------- komutlar ----------

def ses_servisi_kur(m, ayar, klasor):
    """isler.YakalamaIsi ile ayni baglanti: SesServisi klip/bosluk/kulaklik olaylari -> m.ses_olayi (C1), yeniden kesme
    m.yeniden_kes_fn. Transkript metni yalniz sesten (STT) gelir. Kurulamazsa None ('STT yok' durumu motora yazilir;
    altyazi moduna dusulmez)."""
    try:
        import ses as ses_mod
        servis = ses_mod.SesServisi(
            ayar.get("stt_url", ""),
            ipucu_fn=lambda akis=None: m.terimler.prompt(m.seri, list(m.katilimcilar)),   # ad + <=20 ilgili terim
            mod="otomatik", mik_cihaz=ayar.get("mikrofon_cihaz"), olay=olay, olay_fn=m.ses_olayi,
            model=ayar.get("stt_model", "whisper"), api_key=ayar.get("stt_key", ""),
            proxy=ayar.get("stt_proxy", True), verify=ayar_mod.tls_dogrulama(ayar),
            esikler=ayar.get("esikler"), stt_ipucu=bool(ayar.get("stt_ipucu", True)),
            stt_repetition_penalty=ayar.get("stt_repetition_penalty"))
        if ayar.get("olcum_kaydi", False):
            servis.olcum_kaydi_ac(klasor)
        elif ayar.get("kesinti_kaydi", False):
            servis.kesinti_kaydi_ac(klasor)
        m.yeniden_kes_fn = servis.yeniden_kes
        if servis.baslat():
            return servis
        neden = "ses yakalama açılamadı"
    except Exception as e:
        neden = repr(e)
    m.yeniden_kes_fn = None
    print(f"✖ ses yakalama başlatılamadı: {neden} — transkript yazılamayacak (Teams altyazı metni transkripte girmez)")
    m.stt_durumu_ayarla({"durum": "kesinti", "bas": time.time(), "bekleyen": 0, "kayit": False, "eksik": [],
                         "hata": str(neden)[:200]})
    return None


def komut_basla(a):
    """Yakalama: metin sesten (SesServisi -> Motor.ses_olayi), konusmaci altyazi okumalarinin ZAMANINDAN
    (Yakalayici okuma_fn -> Motor.okuma_ekle). Teams altyazi metni transkripte girmez; Yakalayici satirlari yalniz
    Motor.altyazi_satiri'na gider (canli iz; opt-in 'altyazi_yedek' ve STT yokken yedek)."""
    olay.sessiz = a.sessiz
    ayar = ayar_mod.ayar_oku()
    tarih = dt.date.today()
    ctx = outlook.aktif_toplanti() or {}
    baslik = a.baslik or ctx.get("baslik") or input("Toplantı başlığı: ").strip() or "Toplantı"
    if ctx:
        print(f"Outlook: '{ctx['baslik']}' — {len(ctx['katilimcilar'])} katılımcı, gündem {'var' if ctx.get('gundem') else 'yok'}")
    klasor = motor_mod.yeni_klasor(baslik, tarih)
    katilimcilar = list(ctx.get("katilimcilar") or [])
    if getattr(a, "inceleme_yok", False):
        print("! --inceleme-yok artık gereksiz: toplantı bitince kullanıcı onayı hiç sorulmuyor.")
    m = Motor(klasor, baslik, tarih, terim_mod.TerimHavuzu(), katilimcilar, ctx.get("gundem", ""),
              duzelt=not a.duzeltme_yok, olay=olay, kaynak="ses", ben=outlook.ben_adi(ayar),
              esikler=ayar.get("esikler"), altyazi_yedek=bool(ayar.get("altyazi_yedek", False)),
              davet=ctx.get("davet"))
    m.terimleri_hazirla()                        # davet + onceki not terimleri Whisper prompt'u icin havuza
    servis = ses_servisi_kur(m, ayar, klasor)
    y = Yakalayici(katilimcilar=katilimcilar, okuma_fn=m.okuma_ekle)
    print(f"Klasör: {klasor}\nTeams'te altyazı bekleniyor (pencere arkada kalabilir, küçültme). Bitirmek için Ctrl+C.")

    ekran_okuyucu(True)
    basladi, son_durum, uyari = False, time.time(), 0
    try:
        while True:
            if servis is not None:
                try:
                    m.stt_durumu_ayarla(servis.stt_durumu)
                except Exception:
                    pass
            for satir in y.oku():                # okuma olayi okuma_fn ile motora; satir yalniz canli iz / yedek
                m.altyazi_satiri(satir)
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
                      f"{len(m.tum_satirlar())} satır —")
                son_durum = time.time()
            time.sleep(y.onerilen_aralik())      # A7: UIA okumasi yavassa aralik 1,2 sn'ye cikar
    except KeyboardInterrupt:
        print("\nToplantı bitiriliyor...")
    finally:
        ekran_okuyucu(False)
        if servis is not None:
            try:
                servis.durdur()
            except Exception as e:
                print(f"! ses servisi durdurulamadı: {e!r}")
        m.yeniden_kes_fn = None                   # ses durdu: bitiste devir bolunmez ('A → B')
    for satir in y.bitir():
        m.altyazi_satiri(satir)
    tamamla(m, a.outlook, ctx.get("katilimcilar"))


def komut_bitir(a):
    if getattr(a, "inceleme_yok", False):
        print("! --inceleme-yok artık gereksiz: toplantı bitince kullanıcı onayı hiç sorulmuyor.")
    m = Motor.yukle(a.klasor, terim_mod.TerimHavuzu(), olay=olay)
    tamamla(m, a.outlook, m.katilimcilar)


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
    b.add_argument("--inceleme-yok", action="store_true", help="(eski bayrak, yok sayılır: inceleme akışı kaldırıldı)")
    b.set_defaults(f=komut_basla)
    bt = alt.add_parser("bitir")
    bt.add_argument("klasor")
    bt.add_argument("--outlook", action="store_true")
    bt.add_argument("--inceleme-yok", action="store_true", help="(eski bayrak, yok sayılır: inceleme akışı kaldırıldı)")
    bt.set_defaults(f=komut_bitir)
    alt.add_parser("liste").set_defaults(f=komut_liste)
    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
