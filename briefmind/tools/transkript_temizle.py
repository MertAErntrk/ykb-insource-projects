"""
transkript_temizle.py — altyazi tasmasiyla bozulmus bir toplanti kaydini kurtarir (bkz. GELISTIRME_PLANI 13).

Belirti: Gecmis'te bir kayit yuzlerce parca, altyazi.jsonl on binlerce satir; ayni cumleler tekrar tekrar
('altyazi-cakisma' kaynakli). Neden: Teams toplanti penceresinin altyazi paneli 30'dan fazla satir gosterince
eski yakalayici paneli her okumada yeniden yayiyordu.

Ne yapar: altyazi.jsonl'deki birebir tekrarlari ve ayni konusmacinin yarim (onek) hallerini eler, satirlari
zamana gore siralar, parcalari temiz satirlardan yeniden kurar (ozetsiz), oneriler.json'u sifirlar.
Yedekler: parcalar.yedek-YYYYMMDD-HHMM, altyazi.jsonl.yedek-..., oneriler.json.yedek-... (ayni klasorde).
Sonra uygulamada Gecmis -> 'Yeniden ozetle' (uygulama tasmayi kendisi de algilar ve bu temizligi yapar).

Kullanim (briefmind klasorunden):
  python tools\\transkript_temizle.py toplantilar\\2026-09-30_ornek --kuru   -> yalniz sayilar, hicbir sey yazmaz
  python tools\\transkript_temizle.py toplantilar\\2026-09-30_ornek          -> temizler (yedekleyerek)
Cikti icerik tasimaz: yalniz sayilar.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import motor  # noqa: E402


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    a = argparse.ArgumentParser(description="Altyazı taşmasıyla bozulmuş toplantı kaydını temizler")
    a.add_argument("klasor", help="toplantı klasörü (toplantilar/<tarih>_<ad>)")
    a.add_argument("--kuru", action="store_true", help="ne yapacağını sayılarla göster, hiçbir şey yazma")
    a = a.parse_args(argv)
    if not os.path.isfile(os.path.join(a.klasor, "altyazi.jsonl")):
        print(f"{a.klasor}: altyazi.jsonl yok (klasör doğru mu? saklama süresiyle silinmiş olabilir)")
        return 2
    o = motor.tasma_olcumu(a.klasor)
    print(f"Ölçüm: {o['satir']} satır, dakikada en çok {o['dk_en_cok']}, birebir tekrar {o['tekrar']} "
          f"(%{100 * o['tekrar_orani']:.0f}) → taşma: {'EVET' if o['tasma'] else 'hayır'}")
    t = motor.transkript_temizle(a.klasor, kuru=a.kuru)
    print(f"{'Yapılacak' if a.kuru else 'Yapıldı'}: {t['once']} → {t['sonra']} satır "
          f"({t['tekrar']} birebir tekrar, {t['onek']} yarım/önek hali elendi); "
          f"{t['parca_once']} → {t['parca_sonra']} parça (özetsiz)")
    if a.kuru:
        print("--kuru: hiçbir dosya değişmedi. Uygulamak için --kuru olmadan çalıştırın.")
    else:
        print("Yedekler klasörde (*.yedek-*). Şimdi uygulamada Geçmiş → 'Yeniden özetle'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
