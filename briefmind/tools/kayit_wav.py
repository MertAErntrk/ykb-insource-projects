"""
kayit_wav.py — STT testi icin mikrofondan kisa bir WAV kaydeder (16 kHz, mono).
  pip install sounddevice soundfile numpy
  python kayit_wav.py 15        -> test.wav (15 sn)
"""
import sys

import sounddevice as sd
import soundfile as sf

saniye = int(sys.argv[1]) if len(sys.argv) > 1 else 15
print(f"{saniye} sn kayıt — konuş (IFRS 9, Jira kaydı, commit gibi terimler de söyle)...")
ses = sd.rec(int(saniye * 16000), samplerate=16000, channels=1, dtype="int16")
sd.wait()
sf.write("test.wav", ses, 16000)
print("kaydedildi: test.wav")
