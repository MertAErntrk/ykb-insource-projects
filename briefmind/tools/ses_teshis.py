"""
ses_teshis.py — Mikrofon ve hoparlor (loopback) yakalamasini dener: hangi cihaz ses aliyor?

Kullanim (briefmind klasorunden):
  python tools\\ses_teshis.py                  -> tum mikrofonlari 3'er sn dener (konus!)
  python tools\\ses_teshis.py --loopback       -> hoparlor cikisini 5 sn dener (bir video/ses cal)
  python tools\\ses_teshis.py --kaydet 3       -> 3 numarali cihazdan 15 sn test.wav kaydeder
  python tools\\ses_teshis.py --kaydet 3 --stt -> kaydi STT'ye de gonderir, taninan kelime sayisini yazar

Ses dosyaya yalnizca --kaydet ile yazilir (test.wav, .gitignore'da).
"""
import argparse
import json
import os
import sys
import time

import numpy as np

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(KOK)
sys.path.insert(0, KOK)
ORNEK = 16000


def db(x):
    return 20 * np.log10(max(float(x), 1e-9))


def seviye(ses):
    ses = np.asarray(ses, dtype="float32").reshape(-1)
    if not len(ses):
        return -180.0, -180.0
    return db(np.sqrt(np.mean(np.square(ses)))), db(np.max(np.abs(ses)))


def yorum(rms_db, tepe_db):
    if tepe_db < -120:
        return "SIFIR (tamamen sessiz: Windows gizlilik izni kapalı ya da cihaz devre dışı)"
    if rms_db < -60:
        return "ÇOK DÜŞÜK (sessiz/kapalı mikrofon ya da yanlış cihaz)"
    if rms_db < -45:
        return "düşük (konuştuysan seviye yetersiz; mikrofon düzeyini artır)"
    return "SES VAR ✓"


def kaydet_cihaz(cihaz, saniye):
    """Cihazin kendi ornekleme hizinda kaydeder, 16 kHz'e indirir. (WASAPI cihazlari 16 kHz kabul etmez.)"""
    import sounddevice as sd
    bilgi = sd.query_devices(cihaz, "input")
    hiz = int(bilgi["default_samplerate"]) or ORNEK
    ses = sd.rec(int(saniye * hiz), samplerate=hiz, channels=1, dtype="float32", device=cihaz)
    sd.wait()
    ses = ses[:, 0]
    if hiz != ORNEK:
        t = np.arange(0, len(ses) / hiz, 1 / ORNEK)
        ses = np.interp(t, np.arange(len(ses)) / hiz, ses).astype("float32")
    return ses


def mikrofonlar(sure):
    import sounddevice as sd
    try:
        varsayilan = sd.default.device[0]
    except Exception:
        varsayilan = None
    apiler = [a["name"] for a in sd.query_hostapis()]
    cihazlar = [(i, d) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
    print(f"{len(cihazlar)} giriş cihazı bulundu. Varsayılan giriş: {varsayilan}")
    print(f"Her cihaz {sure} sn dinlenecek — test boyunca SÜREKLİ KONUŞ (örn. 1'den 10'a say).\n")
    time.sleep(1.5)
    sonuc = []
    for i, d in cihazlar:
        api = apiler[d["hostapi"]] if d["hostapi"] < len(apiler) else "?"
        etiket = f"[{i:>2}] {d['name'][:45]:45} ({api})" + ("  <- varsayılan" if i == varsayilan else "")
        try:
            ses = kaydet_cihaz(i, sure)
            r, t = seviye(ses)
            print(f"{etiket}\n      rms {r:6.1f} dB  tepe {t:6.1f} dB  → {yorum(r, t)}")
            sonuc.append((r, i, d["name"], api))
        except Exception as e:
            print(f"{etiket}\n      açılamadı: {type(e).__name__}: {str(e)[:80]}")
    iyi = sorted([s for s in sonuc if s[0] >= -45], reverse=True)
    print()
    if not sonuc or max(s[0] for s in sonuc) < -120:
        print("HİÇBİR MİKROFON SES VERMEDİ (tam sıfır). En olası neden Windows gizlilik ayarı:")
        print("  Ayarlar → Gizlilik ve güvenlik → Mikrofon → 'Mikrofon erişimi' AÇIK,")
        print("  'Uygulamaların mikrofonunuza erişmesine izin verin' AÇIK,")
        print("  'Masaüstü uygulamalarının mikrofonunuza erişmesine izin verin' AÇIK.")
        print("  Kurum politikası bunu kilitliyorsa BT'den masaüstü uygulamaları için mikrofon izni iste.")
    elif iyi:
        r, i, ad, api = iyi[0]
        print(f"EN İYİ CİHAZ: [{i}] {ad} ({api}), rms {r:.1f} dB")
        print(f"  Test kaydı:     python tools\\ses_teshis.py --kaydet {i} --stt")
        print(f"  Uygulamada:     config.json → \"mikrofon_cihaz\": {i}   (ya da adı: \"{ad[:30]}\")")
        if varsayilan is not None and i != varsayilan:
            print("  Not: bu cihaz Windows'un varsayılan girişi DEĞİL. İstersen Ayarlar → Sistem → Ses → Giriş'ten")
            print("  varsayılan yap; o zaman config ayarına gerek kalmaz.")
    else:
        print("Ses var ama seviye düşük. Ayarlar → Sistem → Ses → Giriş → cihaz → Giriş düzeyi'ni artır,")
        print("mikrofonun fiziksel sessize alma tuşunu (kulaklık kablosu/klavye F4-F5) kontrol et.")


def loopback(sure):
    import soundcard as sc
    hoparlor = sc.default_speaker()
    print(f"Varsayılan hoparlör: {hoparlor.name}")
    print(f"{sure} sn hoparlör çıkışı dinlenecek — ŞİMDİ bir video/ses çal (Teams test çağrısı da olur).")
    time.sleep(1.5)
    mik = sc.get_microphone(str(hoparlor.name), include_loopback=True)
    with mik.recorder(samplerate=ORNEK, channels=1) as kayit:
        ses = kayit.record(numframes=int(sure * ORNEK)).reshape(-1)
    r, t = seviye(ses)
    print(f"loopback: rms {r:.1f} dB  tepe {t:.1f} dB → {yorum(r, t)}")
    if r < -60:
        print("Teams sesi farklı bir cihazdan çıkıyor olabilir: Teams → Ayarlar → Cihazlar → Hoparlör ile")
        print("Windows varsayılan hoparlörü AYNI olmalı (uygulama Windows varsayılanını dinler).")


def kaydet(cihaz, stt):
    try:
        cihaz = int(cihaz)
    except ValueError:
        pass
    print("15 sn kayıt — konuş (IFRS 9, Jira kaydı, commit gibi terimler de söyle)...")
    time.sleep(1)
    ses = kaydet_cihaz(cihaz, 15)
    r, t = seviye(ses)
    print(f"seviye: rms {r:.1f} dB  tepe {t:.1f} dB → {yorum(r, t)}")
    import ses as ses_mod
    with open("test.wav", "wb") as f:
        f.write(ses_mod.wav_bayt(ses))
    print("kaydedildi: test.wav (dinleyip kontrol edebilirsin)")
    if stt:
        with open("config.json", encoding="utf-8") as f:
            cfg = json.load(f)
        import ayar as ayar_mod
        s = ses_mod.SttIstemci(cfg.get("stt_url", ""), model=cfg.get("stt_model", "whisper"),
                               api_key=cfg.get("stt_key", ""), verify=ayar_mod.tls_dogrulama(cfg))
        t0 = time.time()
        segler = s.coz(ses)
        metin = " ".join((x.get("text") or "").strip() for x in segler).strip()
        kelime = len([k for k in metin.split() if any(c.isalpha() for c in k)])
        print(f"STT: {time.time() - t0:.1f} sn, {len(segler)} segment, {kelime} kelime tanındı")
        print("  (metnin kendisi ekrana yazdırılmadı; görmek istersen: --goster)" if not ARGS.goster else f"  {metin}")


def main():
    global ARGS
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    import warnings
    warnings.filterwarnings("ignore")
    a = argparse.ArgumentParser()
    a.add_argument("--sure", type=float, default=3)
    a.add_argument("--loopback", action="store_true")
    a.add_argument("--kaydet", help="cihaz numarası ya da adı")
    a.add_argument("--stt", action="store_true")
    a.add_argument("--goster", action="store_true", help="STT metnini ekrana yaz")
    ARGS = a.parse_args()
    if ARGS.loopback:
        loopback(max(ARGS.sure, 5))
    elif ARGS.kaydet is not None:
        kaydet(ARGS.kaydet, ARGS.stt)
    else:
        mikrofonlar(ARGS.sure)


ARGS = None

if __name__ == "__main__":
    main()
