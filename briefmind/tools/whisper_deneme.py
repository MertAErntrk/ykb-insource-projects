"""
whisper_deneme.py — Whisper'i kendi sesinle dene (izin gerektirmez, ses saklanmaz).

Kurulum:
  pip install faster-whisper sounddevice numpy
Model (ilk calismada indirilir, ~1.6 GB) Artifactory'nin HuggingFace onbelleginden gelsin diye:
  $env:HF_ENDPOINT = "https://<artifactory>/artifactory/api/huggingfaceml/<hf-repo>"
  $env:HF_HUB_ENABLE_HF_TRANSFER = "0"
  (SSL hatasi alirsan:  $env:CURL_CA_BUNDLE = ""   ya da   pip install pip-system-certs)
  Elle indirdiysen MODEL degiskenine klasor yolunu yaz.

Kullanim:
  python whisper_deneme.py            -> 20 sn mikrofondan kaydeder, yaziya doker
  python whisper_deneme.py 45         -> 45 sn
  python whisper_deneme.py kayit.wav  -> dosyadan
"""
import os
import sys
import time

MODEL = os.environ.get("WHISPER_MODEL", "large-v3-turbo")   # ya da: r"C:\modeller\faster-whisper-large-v3-turbo"
DIL = "tr"
ORNEK = 16000


def terim_istemi(seri=None):
    """Whisper'a verilecek ipucu: terim.prompt(seri, adlar) (katilimci adlari + serinin <=20 terimi). Terim havuzu
    (terimler/terimler.json) bossa yalniz adlar gider. Uygulamadaki ipucuyla ayni uretici."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import terim
    kisiler = ["Deniz Can Öztürk"]                    # istersen toplanti katilimcilarini ekle
    return terim.TerimHavuzu().prompt(seri or "", kisiler)


def mikrofondan(saniye):
    import numpy as np
    import sounddevice as sd
    print(f"{saniye} sn kayıt — konuşmaya başla...", flush=True)
    ses = sd.rec(int(saniye * ORNEK), samplerate=ORNEK, channels=1, dtype="float32")
    sd.wait()
    print("kayıt bitti, yazıya dökülüyor...", flush=True)
    return ses[:, 0]


def dosyadan(yol):
    import numpy as np
    import soundfile as sf                             # faster-whisper ile gelir; yoksa pip install soundfile
    ses, sr = sf.read(yol, dtype="float32")
    if ses.ndim > 1:
        ses = ses.mean(axis=1)
    if sr != ORNEK:
        idx = np.linspace(0, len(ses) - 1, int(len(ses) * ORNEK / sr)).astype(int)
        ses = ses[idx]
    return ses


def main():
    from faster_whisper import WhisperModel
    arg = sys.argv[1] if len(sys.argv) > 1 else "20"
    ses = dosyadan(arg) if arg.lower().endswith(".wav") else mikrofondan(int(arg))
    sure = len(ses) / ORNEK

    t0 = time.perf_counter()
    model = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=max(4, os.cpu_count() or 4))
    print(f"model yüklendi: {time.perf_counter() - t0:.1f} sn")

    t1 = time.perf_counter()
    parcalar, bilgi = model.transcribe(
        ses, language=DIL, beam_size=5, vad_filter=True,
        initial_prompt=terim_istemi(os.environ.get("BRIEFMIND_SERI")),
        condition_on_previous_text=False,
    )
    metin = []
    for p in parcalar:
        print(f"[{p.start:6.1f}–{p.end:6.1f}] {p.text.strip()}")
        metin.append(p.text.strip())
    gecen = time.perf_counter() - t1
    print(f"\nses {sure:.0f} sn, çözümleme {gecen:.1f} sn → hız {sure / gecen:.1f}x gerçek zaman "
          f"(1'in üstü = canlı kullanıma uygun)")
    print("\nTam metin:\n" + " ".join(metin))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    main()
