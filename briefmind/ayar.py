"""
ayar.py — config.json okuma/yazma (arayuz ve komut satiri ortak).
"""
import json
import os

import llm

AYAR_DOSYA = "config.json"
AYAR_HATASI = None                         # config.json okunamadiysa neden (acilista kullaniciya gosterilir)


def ayar_oku():
    v = {"route": llm.ROUTE, "model": llm.MODEL, "otobitir": 180, "duzelt": True, "outlook": True,
         "otomatik_basla": False,
         "kaynak": "ses",                  # altyazi | ses | ikisi
         "stt_url": "",                        # config.json: STT servisi (OpenAI uyumlu /v1/audio/transcriptions)
         "stt_model": "whisper-large-v3-turbo-prod",     # ARGE GPU servisi (Turkce fine-tune)
         "stt_key": "EMPTY",                              # kendi CPU servisimiz icin: model "whisper", anahtar bos
         "mikrofon_modu": "otomatik",      # otomatik | cift | tek
         "ben": "",
         "altyazi_otomatik": True,         # altyazi gorunmezse Teams'te acmayi dene
         "altyazi_turkce": True,           # altyazi bulununca konusulan dili Turkce yapmayi dene
         "otomatik_not": True}             # toplanti bitince incelemeyi bekleme, notu dogrudan uret
    global AYAR_HATASI
    if os.path.exists(AYAR_DOSYA):
        try:
            with open(AYAR_DOSYA, encoding="utf-8") as f:
                v.update(json.load(f))
            AYAR_HATASI = None
        except Exception as e:
            # Eskiden sessizce varsayilanlara dusuluyordu: stt_url bos kalip "STT'ye ulasilamiyor" gorunuyordu
            AYAR_HATASI = f"{e}"
    return v


def ayar_yaz(v):
    with open(AYAR_DOSYA, "w", encoding="utf-8") as f:
        json.dump(v, f, ensure_ascii=False, indent=1)


def tls_dogrulama(ayar=None):
    """LLM/STT isteklerinde TLS dogrulamasi: kurum CA dosyasinin yolu ya da False.
    Simdilik hep False (kurum ici sertifika zinciri bilinmiyor); Asama 2'de config.json -> ca_bundle."""
    return False
