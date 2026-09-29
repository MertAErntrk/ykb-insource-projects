"""
stt_test.py — Mikrofondan kaydeder ve Whisper STT servisine gonderip sonucu gosterir.
Tek dosya: kayit + istek + olcum.

Kurulum:  pip install sounddevice soundfile requests
Kullanim:
  python stt_test.py                         -> 15 sn kaydeder, cozumler
  python stt_test.py 30                      -> 30 sn
  python stt_test.py test.wav                -> var olan dosyayi gonderir
  python stt_test.py 15 --kaydet konusma.wav -> kaydi saklar

Servis adresi: asagidaki ROUTE ya da ortam degiskeni STT_URL.
  $env:STT_URL = "https://<stt-servisi-adresi>"
"""
import argparse
import json
import os
import sys
import time

ROUTE = os.environ.get("STT_URL", "http://localhost:8446")
IPUCU = "IFRS 9, Tera9, Jira kaydı, commit, Risk DWH, OpenShift, Airflow"
ORNEK = 16000


def kaydet(saniye, yol):
    import sounddevice as sd
    import soundfile as sf
    print(f"{saniye} sn kayıt — konuş. (Örnek: 'IFRS 9 raporunu perşembeye yetiştirelim, "
          f"Jira kaydı açıp commit atalım.')")
    for i in (3, 2, 1):
        print(f"  {i}...", end="", flush=True)
        time.sleep(1)
    print(" başla!")
    ses = sd.rec(int(saniye * ORNEK), samplerate=ORNEK, channels=1, dtype="int16")
    sd.wait()
    sf.write(yol, ses, ORNEK)
    print(f"kayıt bitti -> {yol}")
    return yol


def gonder(yol):
    import requests
    print(f"gönderiliyor: {ROUTE}/v1/audio/transcriptions")
    t0 = time.perf_counter()
    with open(yol, "rb") as f:
        r = requests.post(f"{ROUTE}/v1/audio/transcriptions", verify=False, timeout=300,
                          files={"file": (os.path.basename(yol), f, "audio/wav")},
                          data={"language": "tr", "prompt": IPUCU, "model": "whisper",
                                "response_format": "verbose_json"})
    gecen = time.perf_counter() - t0
    if r.status_code != 200:
        print(f"HATA {r.status_code}: {r.text[:500]}")
        return
    v = r.json()
    print("\n--- METİN ---")
    print(v.get("text", "").strip() or "(boş)")
    print("\n--- SEGMENTLER ---")
    for s in v.get("segments", []):
        print(f"[{s['start']:6.1f}-{s['end']:6.1f}] {s['text']}")
    ses_sn = v.get("duration", 0) or 0
    islem = v.get("islem_sn", gecen)
    print(f"\nses {ses_sn:.0f} sn | sunucu {islem:.1f} sn | toplam (ağ dahil) {gecen:.1f} sn"
          + (f" | hız {ses_sn / islem:.1f}x gerçek zaman" if islem else ""))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("girdi", nargs="?", default="15", help="saniye ya da .wav dosyası")
    p.add_argument("--kaydet", default=None, help="kaydı bu isimle sakla")
    a = p.parse_args()
    if a.girdi.lower().endswith(".wav"):
        yol = a.girdi
        if not os.path.exists(yol):
            sys.exit(f"dosya yok: {yol}")
    else:
        yol = kaydet(int(a.girdi), a.kaydet or "test.wav")
    gonder(yol)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    import warnings
    warnings.filterwarnings("ignore")
    main()
