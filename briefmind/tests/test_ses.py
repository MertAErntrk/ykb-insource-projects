"""Ses parcalama ve Whisper sonuc filtreleri (cihaz/STT gerekmez)."""
import queue

import numpy as np

import ses


def test_sonuc_gecerli_gurultulu_gercek_cumleyi_tutar():
    # sessizlik olasiligi yuksek ama model emin: gercek konusma (eskiden atiliyordu)
    assert ses.sonuc_gecerli({"no_speech_prob": 0.7, "avg_logprob": -0.4}, "Raporu perşembeye yetiştirelim.")
    # aksanli/gurultulu: -1.3 artik gecerli
    assert ses.sonuc_gecerli({"avg_logprob": -1.3}, "Jira kaydını açalım mı?")


def test_sonuc_gecerli_halusinasyonu_eler():
    assert not ses.sonuc_gecerli({"no_speech_prob": 0.8, "avg_logprob": -1.1}, "Evet evet.")
    assert not ses.sonuc_gecerli({"avg_logprob": -1.7}, "Bir şeyler söyledi galiba.")
    assert not ses.sonuc_gecerli({}, "İzlediğiniz için teşekkür ederim.")


def test_bolme_noktasi_en_sessiz_blok():
    enerji = [0.1] * 120
    enerji[112] = 0.001
    assert ses.bolme_noktasi(enerji) == 112
    assert 1 <= ses.bolme_noktasi([0.2, 0.2]) < 2


class SahteAkis(ses.AkisYakalayici):
    def __init__(self, bloklar, kuyruk):
        super().__init__("mikrofon", kuyruk)
        self._sahte = bloklar

    def _bloklar_mikrofon(self):
        yield from self._sahte


def _blok(genlik, rng):
    return (genlik * rng.standard_normal(ses.BLOK)).astype("float32")


def test_uzun_konusma_nefeste_bolunur_ses_kaybolmaz():
    rng = np.random.default_rng(0)
    # 1 sn sessizlik, 20 sn araliksiz konusma (tek bir kisa enerji cukuru 11.4. sn'de), 1 sn sessizlik
    bloklar = [_blok(0.0005, rng) for _ in range(10)]
    konusma = [_blok(0.1, rng) for _ in range(200)]
    konusma[114] = _blok(0.02, rng)
    bloklar += konusma + [_blok(0.0005, rng) for _ in range(10)]
    q = queue.Queue()
    a = SahteAkis(bloklar, q)
    a.run()
    parcalar = []
    while not q.empty():
        parcalar.append(q.get())
    assert len(parcalar) >= 2
    toplam = sum(len(p["ses"]) for p in parcalar) / ses.ORNEK
    assert toplam >= 20.0                       # konusmanin tamami gonderildi
    ilk = len(parcalar[0]["ses"]) / ses.ORNEK
    assert ilk < ses.ZORLA_SN                   # zorla sinira gelmeden, enerji cukurunda bolundu
    assert parcalar[1]["basla"] > parcalar[0]["basla"]


def test_kuyruk_kisa_gecikmede_parca_atmaz():
    q = queue.Queue()
    for _ in range(ses.KUYRUK_UYARI + 3):
        q.put({"ses": None})
    olaylar = []
    a = ses.AkisYakalayici("mikrofon", q, olay=lambda t, v: olaylar.append((t, v)))
    a._gonder([np.full(ses.ORNEK * 2, 0.1, dtype="float32")], None)
    assert q.qsize() == ses.KUYRUK_UYARI + 4


def test_mikrofon_48khz_cihaz_16khz_bloga_iner(monkeypatch):
    """WASAPI cihazlari 16 kHz acilmaz: cihaz kendi hizinda acilip bloklar 16 kHz'e indirilmeli."""
    import sys
    import types
    acilan = {}

    class Akis:
        def __init__(self, samplerate, channels, dtype, blocksize, device, callback):
            acilan.update(hiz=samplerate, blok=blocksize)
            self.cb = callback

        def __enter__(self):
            t = np.arange(self_blok := acilan["blok"]) / acilan["hiz"]
            self.cb(np.sin(2 * np.pi * 200 * t).astype("float32").reshape(-1, 1), self_blok, None, None)
            return self

        def __exit__(self, *a):
            return False

    sahte = types.SimpleNamespace(InputStream=Akis,
                                  query_devices=lambda cihaz, tur: {"default_samplerate": 48000.0})
    monkeypatch.setitem(sys.modules, "sounddevice", sahte)
    a = ses.AkisYakalayici("mikrofon", queue.Queue(), cihaz=3)
    blok = next(a._bloklar_mikrofon())
    assert acilan == {"hiz": 48000, "blok": 4800}
    assert len(blok) == ses.BLOK and blok.dtype == np.float32
    assert 0.6 < float(np.sqrt(np.mean(np.square(blok)))) < 0.8          # sinus enerjisi korunur


class _Cevap:
    def __init__(self, kod, govde=None):
        self.status_code, self._govde = kod, govde or {}

    def json(self):
        return self._govde

    def raise_for_status(self):
        if self.status_code >= 400:
            raise ses.requests.HTTPError(str(self.status_code))


def test_stt_kopmada_yeniden_dener(monkeypatch):
    monkeypatch.setattr(ses.time, "sleep", lambda s: None)
    s = ses.SttIstemci("https://stt.ornek", proxy=False)
    assert s._oturum.trust_env is False
    sira = [ses.requests.exceptions.ChunkedEncodingError("10053"), ses.requests.ConnectionError("10053"),
            _Cevap(200, {"text": "Raporu perşembeye yetiştirelim."})]

    def istek(ses_, bicim, onceki=""):
        x = sira.pop(0)
        if isinstance(x, Exception):
            raise x
        return x

    monkeypatch.setattr(s, "_istek", istek)
    segler = s.coz(np.zeros(16000, dtype="float32"))
    assert segler[0]["text"].startswith("Raporu") and s.yeniden_deneme == 1


def test_stt_kalici_kopmada_hata_verir(monkeypatch):
    monkeypatch.setattr(ses.time, "sleep", lambda s: None)
    s = ses.SttIstemci("https://stt.ornek")

    def istek(ses_, bicim, onceki=""):
        raise ses.requests.ConnectionError("10053")

    monkeypatch.setattr(s, "_istek", istek)
    import pytest
    with pytest.raises(ses.requests.ConnectionError):
        s.coz(np.zeros(16000, dtype="float32"))
