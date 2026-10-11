"""Ses parcalama, zaman tabani, klip olaylari, halka tampon, pasif kulaklik, STT kesintisi (cihaz/STT/ag gerekmez)."""
import datetime as dt
import itertools
import json
import os
import queue
import threading
import time

import numpy as np
import pytest

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


# ================================================================ Faz 1: zaman tabani, klip olaylari, halka tampon,
# yeniden kesme, pasif kulaklik, STT kesintisi, sira (cihaz ve ag yok; sahte saat/STT)

T0 = 1_700_000_000.0


def _sessiz(rng, n):
    return [_blok(0.0005, rng) for _ in range(n)]


def _konusma(rng, n, dipler=None, genlik=0.1):
    """n blokluk konusma; dipler: {blok_indeksi: genlik} daha sessiz bloklar."""
    dipler = dipler or {}
    return [_blok(dipler.get(i, genlik), rng) for i in range(n)]


def _akis(bloklar, kuyruk=None, saat=None, sayac=None, **kw):
    class Sahte(ses.AkisYakalayici):
        def _bloklar_mikrofon(self_):
            yield from bloklar

    return Sahte("mikrofon", kuyruk if kuyruk is not None else queue.Queue(),
                 sayac=sayac if sayac is not None else itertools.count(), saat=saat or (lambda: T0), **kw)


def _tek_klip_akisi(dipler=None, n=20, **kw):
    rng = np.random.default_rng(7)
    a = _akis(_sessiz(rng, 10) + _konusma(rng, n, dipler) + _sessiz(rng, 10), **kw)
    a.run()
    return a


# ---------------------------------------------------------------- zaman tabani

def test_klip_zamani_ornek_sayacindan_gelir_varistan_degil():
    a = _tek_klip_akisi()
    p = a.kuyruk.get_nowait()
    capa = T0 - 0.1                                            # ilk blok T0'da bitti
    assert abs(a.epoch_capa - capa) < 1e-9
    assert p["v_ornek_bas"] == 10 * ses.BLOK and p["v_ornek_bit"] == 30 * ses.BLOK
    # bas = ilk SESLI blok (on-yuk disarida), bit = SON SESLI blogun sonu (sondaki sessizlik disarida)
    assert abs(p["bas_epoch"] - (capa + 1.0)) < 1e-9 and abs(p["bit_epoch"] - (capa + 3.0)) < 1e-9
    assert p["ornek_bas"] == 7 * ses.BLOK and p["ornek_bit"] > p["v_ornek_bit"]       # ses: on-yuk + sondaki sessizlik dahil
    # gercek saat 2023'ten sonra; zaman sahte saatten (sayactan) geldi: parca_bas artik now()'dan turemez
    assert abs(p["basla"].timestamp() - p["bas_epoch"]) < 1e-3 and abs(p["bitis"].timestamp() - p["bit_epoch"]) < 1e-3
    assert abs(time.time() - p["bas_epoch"]) > 1e6
    assert p["klip_no"] == 1 and p["no"] == 0 and p["ust_klip"] is None
    assert len(p["zarf"]) == 20 and all(0.05 < z < 0.2 for z in p["zarf"])             # 100 ms basina RMS (sesli aralik)
    assert 0.08 < p["rms"] < 0.12


def test_klip_numaralari_global_artar():
    rng = np.random.default_rng(3)
    bloklar = []
    for _ in range(3):
        bloklar += _sessiz(rng, 10) + _konusma(rng, 20)
    bloklar += _sessiz(rng, 10)
    a = _akis(bloklar)
    a.run()
    nolar = [a.kuyruk.get_nowait()["klip_no"] for _ in range(3)]
    assert nolar == [1, 2, 3]


def _sessiz_akis(saat, atlama_blok=None, atlama_sn=0.0, blok_sayisi=1300):
    def gen():
        for i in range(blok_sayisi):
            saat[0] += 0.1                                      # blok varis ani
            if i == atlama_blok:
                saat[0] += atlama_sn                            # cihaz takildi / saat kaydi
            yield np.zeros(ses.BLOK, dtype="float32")
    return gen()


def test_saat_capasi_60sn_da_kayma_varsa_yeniden_kurulur():
    saat = [T0]
    olaylar = []
    a = _akis(_sessiz_akis(saat, atlama_blok=700, atlama_sn=0.5), saat=lambda: saat[0],
              olay=lambda t, v: olaylar.append((t, v)))
    a.run()                                                     # 130 sn'lik akis
    assert a.capa_sayisi == 1                                   # 60. sn'de senkron, 120. sn'de 0.5 sn kayma
    capa_olay = [v for t, v in olaylar if t == "capa"]
    assert len(capa_olay) == 1 and abs(capa_olay[0]["fark"] - 0.5) < 1e-3 and capa_olay[0]["akis"] == "mikrofon"
    assert any(t == "log" and "çapa" in v for t, v in olaylar)
    ilk_capa = T0
    assert abs(a.epoch_capa - (ilk_capa + 0.5)) < 1e-3                                  # sahte saat 0.1'lik toplamlarla ilerler
    # eski orneklerin zamani degismez, yenilerinki yeni capayla
    assert abs(a.ornek_epoch(100 * ses.BLOK) - (ilk_capa + 10.0)) < 1e-3
    assert abs(a.ornek_epoch(1250 * ses.BLOK) - (ilk_capa + 0.5 + 125.0)) < 1e-3
    for n in (100 * ses.BLOK, 1250 * ses.BLOK):
        assert abs(a.epoch_ornek(a.ornek_epoch(n)) - n) < 1e-3


def test_kucuk_kayma_yeniden_capalamaz():
    saat = [T0]
    olaylar = []
    a = _akis(_sessiz_akis(saat, atlama_blok=700, atlama_sn=0.2), saat=lambda: saat[0],
              olay=lambda t, v: olaylar.append((t, v)))
    a.run()
    assert a.capa_sayisi == 0 and not [1 for t, _ in olaylar if t == "capa"]


def test_servis_capa_olayini_olaylar_jsonl_ye_yazar(tmp_path):
    s = ses.SesServisi("", olcum_klasor=str(tmp_path))
    s._akis_olayi("capa", {"akis": "loopback", "fark": 0.42})
    with open(s._olcum.olaylar_jsonl, encoding="utf-8") as f:
        satirlar = [json.loads(x) for x in f if x.strip()]
    assert satirlar[0]["tur"] == "capa" and satirlar[0]["akis"] == "loopback" and satirlar[0]["fark"] == 0.42


# ---------------------------------------------------------------- halka tampon

def test_halka_tampon_pencere_ve_dilim():
    assert ses.HALKA_SN == 600 and ses.HalkaTampon().maks * 2 == 19_200_000            # 10 dk float16 ~ 19 MB
    h = ses.HalkaTampon(sn=1)
    x = (0.5 * np.sin(np.arange(48000) / 11.0)).astype("float32")
    for i in range(30):
        h.ekle(i * ses.BLOK, x[i * ses.BLOK:(i + 1) * ses.BLOK])
    assert h.bitis == 48000 and h.baslangic == 32000
    d = h.al(40000, 41000)
    assert d.dtype == np.float32 and len(d) == 1000 and float(np.max(np.abs(d - x[40000:41000]))) < 1e-3
    assert h.al(0, 1000) is None and h.al(47000, 49000) is None                         # pencere disi
    assert len(h.al(5, 5)) == 0


def test_akis_halka_tamponu_dolar():
    a = _tek_klip_akisi()
    assert a.halka.bitis == a.toplam_ornek == 40 * ses.BLOK
    assert a.halka.al(0, ses.BLOK) is not None


# ---------------------------------------------------------------- yeniden kesme

def _kesilebilir(dipler, halka_sn=ses.HALKA_SN, n=60):
    a = _tek_klip_akisi(dipler, n=n, halka_sn=halka_sn)
    p = a.kuyruk.get_nowait()
    return a, p


def test_yeniden_kes_en_sessiz_blokta_boler():
    # 6 sn'lik klip; 3.0. sn'de dip (genlik 0.02); aralik disinda (5.5. sn) daha derin dip: pencere +-1 sn olmali
    a, p = _kesilebilir({30: 0.02, 55: 0.01})
    v0 = p["v_ornek_bas"]
    assert a.kes(p["klip_no"], p["bas_epoch"] + 3.0) is True
    ilk, ikinci = a.kuyruk.get_nowait(), a.kuyruk.get_nowait()
    kes = v0 + 30 * ses.BLOK + ses.BLOK // 2                                # dip blogunun ortasi
    assert ilk["v_ornek_bit"] == kes == ikinci["v_ornek_bas"]
    assert ilk["v_ornek_bas"] == v0 and ikinci["v_ornek_bit"] == p["v_ornek_bit"]
    assert ilk["ust_klip"] == p["klip_no"] == ikinci["ust_klip"]
    assert (ilk["klip_no"], ikinci["klip_no"]) == (2, 3)                    # yeni, artan klip_no'lar
    assert ilk["bit_epoch"] == ikinci["bas_epoch"]
    assert abs(ilk["bas_epoch"] - p["bas_epoch"]) < 1e-9 and abs(ikinci["bit_epoch"] - p["bit_epoch"]) < 1e-9
    assert abs((ilk["bit_epoch"] - ilk["bas_epoch"]) - 3.05) < 1e-6
    # ses: ilk parca on-yukten kesime, ikinci parca kesimden klibin sonuna (sondaki sessizlik dahil)
    assert len(ilk["ses"]) == kes - p["ornek_bas"] and len(ikinci["ses"]) == p["ornek_bit"] - kes
    assert len(ilk["zarf"]) == 31 and len(ikinci["zarf"]) == 30
    assert ilk["ornek_bas"] == p["ornek_bas"] and ikinci["ornek_bit"] == p["ornek_bit"]
    # ayni klip ikinci kez kesilmez
    assert a.kes(p["klip_no"], p["bas_epoch"] + 3.0) is False and a.kuyruk.empty()


def test_yeniden_kes_parca_1_sn_den_kisaysa_false():
    a, p = _kesilebilir({5: 0.02})                                          # dip 0.5. sn: ilk parca 0.55 sn
    assert a.kes(p["klip_no"], p["bas_epoch"] + 0.5) is False
    assert a.kuyruk.empty() and a.klipler[p["klip_no"]]["kesildi"] is False
    a2, p2 = _kesilebilir({55: 0.02})                                       # dip 5.5. sn: ikinci parca 0.45 sn
    assert a2.kes(p2["klip_no"], p2["bas_epoch"] + 5.5) is False and a2.kuyruk.empty()


def test_yeniden_kes_bilinmeyen_ya_da_halkadan_cikmis_klip_false():
    a, p = _kesilebilir({30: 0.02})
    assert a.kes(999, p["bas_epoch"] + 3.0) is False
    a2, p2 = _kesilebilir({30: 0.02}, halka_sn=2)                           # 2 sn'lik halka: 6 sn'lik klip sigmaz
    assert a2.kes(p2["klip_no"], p2["bas_epoch"] + 3.0) is False and a2.kuyruk.empty()


def test_servis_yeniden_kes_iki_parcayi_ust_klip_ile_yayar():
    olaylar = []
    s = ses.SesServisi("", olay_fn=olaylar.append)
    rng = np.random.default_rng(7)
    a = _akis(_sessiz(rng, 10) + _konusma(rng, 60, {30: 0.02}) + _sessiz(rng, 10), kuyruk=s.kuyruk, sayac=s._sayac)
    a.run()
    s.akislar = [a]
    metinler = iter(["Birinci konuşmacı raporu anlattı.", "Ardından ikincisi itiraz etti.",
                     "Sonunda ikisi de uzlaştı."])
    s.stt = _KlipStt(lambda ses_: [{"text": next(metinler)}])
    s._dur.set()
    s._calis()                                                              # ana klip
    ana = olaylar[-1]
    assert ana["tur"] == "klip" and ana["klip_no"] == 1 and ana["ust_klip"] is None
    assert s.yeniden_kes(1, ana["bas"] + 3.0) is True
    assert s.yeniden_kes(77, ana["bas"] + 3.0) is False
    s._calis()                                                              # iki parca STT'ye gider
    parcalar = olaylar[1:]
    assert [(e["klip_no"], e["ust_klip"]) for e in parcalar] == [(2, 1), (3, 1)]
    assert parcalar[0]["bit"] == parcalar[1]["bas"] and abs(parcalar[0]["bas"] - ana["bas"]) < 1e-9
    assert abs(parcalar[1]["bit"] - ana["bit"]) < 1e-9
    assert abs(parcalar[0]["sure"] + parcalar[1]["sure"] - ana["sure"]) < 1e-6


class _KlipStt:
    verbose = True
    yeniden_deneme = 0

    def __init__(self, fn):
        self._fn = fn

    def coz(self, ses_, onceki=""):
        return self._fn(ses_)

    def saglik(self, deneme=1):
        return True


# ---------------------------------------------------------------- C1 klip olayi

def _klip_p(no=0, akis="loopback", bas=T0 + 10.0, sure=2.0, klip_no=None, ust=None):
    x = (0.1 * np.sin(np.arange(int(16000 * (sure + 0.6))) / 7.0)).astype("float32")
    return {"kaynak": akis, "basla": dt.datetime.fromtimestamp(bas), "bitis": dt.datetime.fromtimestamp(bas + sure),
            "ses": x, "no": no, "klip_no": no + 1 if klip_no is None else klip_no, "bas_epoch": bas,
            "bit_epoch": bas + sure, "zarf": [0.1] * int(sure * 10), "rms": 0.07, "ust_klip": ust,
            "ornek_bas": 0, "ornek_bit": len(x), "epoch_capa": bas}


def test_bir_klip_bir_olay_metin_birden_cok_cumle_icerir():
    olaylar = []
    s = ses.SesServisi("", olay_fn=olaylar.append)
    s.stt = _KlipStt(lambda ses_: [{"text": "Raporu perşembeye yetiştirelim. Sonra toplantıyı kapatalım.",
                                    "avg_logprob": -0.3, "no_speech_prob": 0.01}])
    s.kuyruk.put(_klip_p())
    s._dur.set()
    s._calis()
    assert len(olaylar) == 1
    e = olaylar[0]
    assert set(e) == {"tur", "klip_no", "akis", "bas", "bit", "sure", "text", "ts", "rms", "zarf", "ust_klip", "supheli",
                    "hal_puani"}
    assert e["tur"] == "klip" and e["klip_no"] == 1 and e["akis"] == "loopback"
    assert e["text"] == "Raporu perşembeye yetiştirelim. Sonra toplantıyı kapatalım."
    assert e["bas"] == T0 + 10.0 and e["bit"] == T0 + 12.0 and abs(e["sure"] - 2.0) < 1e-9
    assert e["ts"] == dt.datetime.fromtimestamp(T0 + 10.0).strftime("%H:%M:%S")
    assert e["zarf"] == [0.1] * 20 and e["rms"] == 0.07 and e["ust_klip"] is None and e["supheli"] is False


def test_halusinasyon_klibi_olay_uretmez_sira_kapanir():
    olaylar = []
    s = ses.SesServisi("", olay_fn=olaylar.append)
    cevaplar = iter([[{"text": "İzlediğiniz için teşekkür ederim."}], [{"text": "Gerçek bir toplantı cümlesi."}]])
    s.stt = _KlipStt(lambda ses_: next(cevaplar))
    s.kuyruk.put(_klip_p(no=0))
    s.kuyruk.put(_klip_p(no=1, bas=T0 + 20.0))
    s._dur.set()
    s._calis()
    assert [e["klip_no"] for e in olaylar] == [2] and s.elenen >= 1


def test_ayni_cumle_ayni_akista_90sn_icinde_tekrar_olay_olmaz():
    olaylar = []
    s = ses.SesServisi("", olay_fn=olaylar.append)
    s.stt = _KlipStt(lambda ses_: [{"text": "Raporu perşembeye yetiştirelim."}])
    for no in range(3):
        s.kuyruk.put(_klip_p(no=no, bas=T0 + 10.0 + 10 * no))
    s._dur.set()
    s._calis()
    assert [e["klip_no"] for e in olaylar if e["text"]] == [1]
    # suzgecte dusen loopback klibi metinsiz yayilir (o anda loopback'te ses vardi: yanki tespiti / L capasi)
    assert [(e["klip_no"], e["text"]) for e in olaylar[1:]] == [(2, ""), (3, "")]


def test_suzgecte_dusen_mikrofon_klibi_olay_uretmez():
    olaylar = []
    s = ses.SesServisi("", olay_fn=olaylar.append)
    s.stt = _KlipStt(lambda ses_: [{"text": "Raporu perşembeye yetiştirelim."}])
    for no in range(3):
        s.kuyruk.put(_klip_p(no=no, akis="mikrofon", bas=T0 + 10.0 + 10 * no))
    s._dur.set()
    s._calis()
    assert [e["klip_no"] for e in olaylar] == [1]


def test_yeniden_kesme_parcalari_tekrar_suzgecinden_muaf():
    """Parcanin cumleleri ust klibin (kaydedilmis) cumleleridir: suzgec ikisini de dusurmemeli (bulgu: parcalar hic
    gelmiyor ya da biri dusunce ust klibin cumlesi kayboluyordu)."""
    olaylar = []
    s = ses.SesServisi("", olay_fn=olaylar.append)
    ust = {"tur": "klip", "klip_no": 1, "akis": "loopback", "bas": T0, "bit": T0 + 7.0, "sure": 7.0,
           "text": "Raporu ben hazirlarim. Peki ya sen bakar misin?", "ts": "10:00:00", "rms": 0.1,
           "zarf": [0.1] * 70, "ust_klip": None, "supheli": False}
    s._sirali_yay(None, [ust])
    s._sirali_yay(None, [dict(ust, klip_no=2, bit=T0 + 3.0, text="Raporu ben hazirlarim.", ust_klip=1)])
    s._sirali_yay(None, [dict(ust, klip_no=3, bas=T0 + 3.0, text="Peki ya sen bakar misin?", ust_klip=1)])
    assert [(e["klip_no"], e["text"]) for e in olaylar] == [
        (1, "Raporu ben hazirlarim. Peki ya sen bakar misin?"), (2, "Raporu ben hazirlarim."),
        (3, "Peki ya sen bakar misin?")]


def test_yeniden_kesme_parcasi_metni_bos_olsa_da_olay_olur():
    """Parcanin STT'si bos donerse (o yarida soz yok) olay metinsiz gelir: motor parcanin geldigini bilir."""
    s = ses.SesServisi("", olay_fn=lambda e: None)
    p = _klip_p(no=0, ust=7)
    ev = s.klip_olayi_uret(p, [{"text": ""}])
    assert ev is not None and ev["text"] == "" and ev["ust_klip"] == 7
    assert s.klip_olayi_uret(_klip_p(no=1), [{"text": ""}]) is None              # normal klip: olay yok


def test_saat_capasi_olay_fn_ile_motora_gider():
    olaylar = []
    s = ses.SesServisi("", olay_fn=olaylar.append)
    s._akis_olayi("capa", {"akis": "loopback", "fark": 2.0, "ornek": 1, "epoch_capa": 1.0, "saat": 2.0})
    assert olaylar == [{"tur": "capa", "akis": "loopback", "fark": 2.0}]


def test_olaylar_klip_sirasiyla_iki_isci_ters_bitirse_de(monkeypatch):
    olaylar = []
    s = ses.SesServisi("", olay_fn=olaylar.append)
    gecikme = {0: 0.3, 1: 0.0}

    def coz(ses_):
        no = int(round(float(ses_[0]) * 1000))
        time.sleep(gecikme[no])
        return [{"text": f"{no + 1} numaralı klip için uzun bir cümle."}]

    s.stt = _KlipStt(coz)
    for no in (0, 1):
        p = _klip_p(no=no, bas=T0 + 10.0 * no)
        p["ses"] = np.full(16000 * 2, no / 1000.0, dtype="float32")
        s.kuyruk.put(p)
    s._dur.set()
    isciler = [threading.Thread(target=s._calis, daemon=True) for _ in range(2)]
    for i in isciler:
        i.start()
    for i in isciler:
        i.join(timeout=5)
    assert [e["klip_no"] for e in olaylar] == [1, 2]


def test_eski_yol_olay_fn_yokken_satir_fn_calisir():
    satirlar = []
    s = ses.SesServisi("", satir_fn=lambda ts, kim, metin, akis: satirlar.append((kim, metin, akis)))
    s.stt = _KlipStt(lambda ses_: [{"text": "Raporu perşembeye yetiştirelim."}])
    s.kuyruk.put(_klip_p(akis="mikrofon"))
    s._dur.set()
    s._calis()
    assert satirlar == [("ben", "Raporu perşembeye yetiştirelim.", "mikrofon")]


def test_olcum_kaydi_klip_no_zarf_ve_sesli_zaman_yazar(tmp_path):
    s = ses.SesServisi("", olcum_klasor=str(tmp_path))
    s.stt = _KlipStt(lambda ses_: [{"text": "Raporu perşembeye yetiştirelim."}])
    s.kuyruk.put(_klip_p(ust=5))
    s._dur.set()
    s._calis()
    with open(os.path.join(str(tmp_path), "olcum", "klipler.jsonl"), encoding="utf-8") as f:
        (k,) = [json.loads(x) for x in f if x.strip()]
    assert k["klip_no"] == 1 and k["ust_klip"] == 5 and k["zarf"] == [0.1] * 20 and k["rms"] == 0.07
    assert k["bas"] == T0 + 10.0 and k["bit"] == T0 + 12.0                  # sesli aralik, ornek sayacindan degil
    assert len(k["tsler"]) == 1 and k["tsler"][0] == k["ts"]


# ---------------------------------------------------------------- sira: bastaki numara takilirsa

def test_bastaki_numara_60sn_takilirsa_atlanir_gec_gelen_kaybolmaz():
    olaylar = []
    s = ses.SesServisi("", olay_fn=olaylar.append)
    saat = [1000.0]
    s._mono = lambda: saat[0]
    ev = lambda no: {"tur": "klip", "klip_no": no + 1, "akis": "loopback", "bas": T0 + no, "bit": T0 + no + 1,
                     "sure": 1.0, "text": f"{no} numaralı konuşma cümlesi burada.", "ts": "10:00:00", "rms": 0.1,
                     "zarf": [], "ust_klip": None, "supheli": False}
    s._sirali_yay(1, [ev(1)])
    s._sirali_yay(2, [ev(2)])
    assert olaylar == []                                    # 0 numara bekleniyor
    saat[0] += ses.HOL_SN - 1
    s._hol_kontrol()
    assert olaylar == []
    saat[0] += 2
    s._hol_kontrol()                                        # 60 sn doldu: 0 atlanir, arkadakiler yayilir
    assert [e["klip_no"] for e in olaylar] == [2, 3] and s.atlanan_numara == 1
    s._sirali_yay(0, [ev(0)])                               # gec gelen sonuc kaybolmaz
    assert [e["klip_no"] for e in olaylar] == [2, 3, 1]
    s._sirali_yay(3, [ev(3)])                               # sira normale doner
    assert [e["klip_no"] for e in olaylar][-1] == 4


# ---------------------------------------------------------------- durdur: son klip kaybolmaz

def test_durdur_akisin_son_klibini_isciler_bitmeden_isler():
    olaylar = []
    s = ses.SesServisi("", olay_fn=olaylar.append)
    s.stt = _KlipStt(lambda ses_: [{"text": "Toplantı bitmeden söylenen son cümle."}])
    rng = np.random.default_rng(5)

    class Sahte(ses.AkisYakalayici):
        def _bloklar_mikrofon(self_):
            for _ in range(20):
                yield _blok(0.1, rng)
            while not self_._dur.is_set():                  # konusma suruyor; durdur() ile akis biter, yarim klip gonderilir
                time.sleep(0.01)
                yield _blok(0.1, rng)

    a = Sahte("mikrofon", s.kuyruk, sayac=s._sayac)
    s.akislar = [a]
    a.start()
    s._isciler = [threading.Thread(target=s._calis, daemon=True) for _ in range(2)]
    for i in s._isciler:
        i.start()
    time.sleep(0.2)
    s.durdur()                                              # eskiden isciler akis son klibi koymadan cikabilirdi
    assert len(olaylar) == 1 and olaylar[0]["akis"] == "mikrofon" and olaylar[0]["sure"] > 2.0


# ---------------------------------------------------------------- kuyruk tasmasi

def test_kuyruk_tavan_tasma_fn_varsa_klip_ona_gider_yoksa_dusurulur():
    q = queue.Queue()
    for _ in range(ses.KUYRUK_TAVAN):
        q.put({"ses": None})
    olaylar, alinan = [], []
    ses_ = np.full(ses.ORNEK * 2, 0.1, dtype="float32")
    a = ses.AkisYakalayici("mikrofon", q, olay=lambda t, v: olaylar.append((t, v)), sayac=itertools.count(),
                           tasma_fn=lambda p: alinan.append(p) or True)
    a._gonder([ses_], None)
    assert q.qsize() == ses.KUYRUK_TAVAN and len(alinan) == 1 and alinan[0]["klip_no"] == 1
    assert [v["atlandi"] for t, v in olaylar if t == "stt_gecikme"] == [False]     # diske alindi: atlanmadi
    b = ses.AkisYakalayici("mikrofon", q, olay=lambda t, v: olaylar.append((t, v)))     # eski davranis
    b._gonder([ses_], None)
    assert q.qsize() == ses.KUYRUK_TAVAN and olaylar[-1][1]["atlandi"] is True


# ---------------------------------------------------------------- pasif kulaklik tespiti

def _bursty(rng, n):
    """Konusma benzeri 100 ms zarf: var/yok bolumleri + hece duzeyinde dalgalanma."""
    z = np.zeros(n)
    i, acik = 0, bool(rng.integers(0, 2))
    while i < n:
        uz = int(rng.integers(8, 30))
        if acik:
            mod = 0.5 + 0.5 * np.abs(np.sin(np.arange(uz) * rng.uniform(0.6, 1.2) + rng.uniform(0, 6))) * rng.uniform(0.6, 1.4, uz)
            z[i:i + uz] = (rng.uniform(0.04, 0.15) * mod)[:len(z[i:i + uz])]
        else:
            z[i:i + uz] = 0.0005
        i += uz
        acik = not acik
    return z[:n]


def _kareler(L, M, faz=0.03):
    return {"loopback": [(0.05 + 0.1 * i, float(L[i])) for i in range(len(L))],
            "mikrofon": [(0.05 + faz + 0.1 * i, float(M[i])) for i in range(len(M))]}


def test_kulaklik_hoparlor_yankisi_yuksek_korelasyon():
    for tohum in range(5):
        rng = np.random.default_rng(tohum)
        L = _bursty(rng, 300)
        M = 0.2 * np.roll(L, 2) + 0.0005 * rng.random(300)               # 200 ms gecikmeli, zayif yanki
        durum, guven, r = ses.KulaklikTespit().hesapla(_kareler(L, M))
        assert durum is False and r > 0.8 and guven > 0.8


def test_kulaklik_yanki_arada_kullanici_konusunca_da_hoparlor():
    rng = np.random.default_rng(11)
    L = _bursty(rng, 300)
    M = 0.2 * np.roll(L, 3) + 0.0005 * rng.random(300) + 0.15 * _bursty(rng, 300) * (L < 0.01)
    durum, guven, r = ses.KulaklikTespit().hesapla(_kareler(L, M))
    assert durum is False and r >= ses.KULAKLIK_YUKSEK


def test_kulaklik_bagimsiz_akislar_kulaklik_var():
    for tohum in range(8):
        L = _bursty(np.random.default_rng(100 + tohum), 300)
        M = _bursty(np.random.default_rng(200 + tohum), 300)           # kullanici kendi bagimsiz sesi
        durum, guven, r = ses.KulaklikTespit().hesapla(_kareler(L, M))
        assert durum is True and r < ses.KULAKLIK_DUSUK, (tohum, r)


def test_kulaklik_yetersiz_veri_none():
    rng = np.random.default_rng(1)
    L, M = _bursty(rng, 300), _bursty(rng, 300)
    t = ses.KulaklikTespit()
    assert t.hesapla(_kareler(L[:100], M[:100]))[0] is None             # 10 sn < 15 sn
    assert t.hesapla({"loopback": [], "mikrofon": []})[0] is None
    sessiz = np.full(300, 0.0005)
    assert t.hesapla(_kareler(sessiz, M))[0] is None                    # karsi taraf konusmuyor: anlamsiz
    assert t.hesapla(_kareler(L, np.zeros(300)))[0] is True             # mikrofon sessiz (kapali): yanki yok


def test_kulaklik_olayi_yalniz_degisimde_ve_gecmis_korunur():
    olaylar = []
    t = ses.KulaklikTespit(olay_fn=olaylar.append)
    rng = np.random.default_rng(4)

    def besle(L, M, bas):
        for i in range(len(L)):
            t.blok_ekle("loopback", bas + 0.05 + 0.1 * i, float(L[i]))
            t.blok_ekle("mikrofon", bas + 0.08 + 0.1 * i, float(M[i]))

    L1 = _bursty(rng, 400)
    besle(L1, 0.2 * np.roll(L1, 2) + 0.0005 * rng.random(400), 0.0)                 # 40 sn hoparlor
    assert t.durum is False and [e["durum"] for e in olaylar] == [False]
    assert olaylar[0]["tur"] == "kulaklik" and 0.0 < olaylar[0]["guven"] <= 1.0
    L2 = _bursty(rng, 600)
    besle(L2, _bursty(rng, 600), 40.0)                                               # 60 sn bagimsiz: kulaklik takildi
    assert t.durum is True and [e["durum"] for e in olaylar] == [False, True]
    # karsi taraf susunca (veri yetersiz) durum degismez
    besle(np.full(400, 0.0005), _bursty(rng, 400), 100.0)
    assert t.durum is True and len(olaylar) == 2


def test_kulaklik_mod_ayari_tespiti_ezer():
    assert ses.SesServisi("", mod="cift").kulaklik is True
    assert ses.SesServisi("", mod="tek").kulaklik is False
    assert ses.SesServisi("", mod="otomatik").kulaklik is None
    olaylar = []
    t = ses.KulaklikTespit(olay_fn=olaylar.append)
    t.sabitle(True)
    assert olaylar == [{"tur": "kulaklik", "durum": True, "guven": 1.0}]
    rng = np.random.default_rng(2)
    L = _bursty(rng, 400)
    for i in range(400):                                                              # yanki gelse de sabit karar degismez
        t.blok_ekle("loopback", 0.1 * i, float(L[i]))
        t.blok_ekle("mikrofon", 0.1 * i, float(0.2 * L[i]))
    assert t.durum is True and len(olaylar) == 1


def test_akis_kare_rms_sini_kulaklik_tespitine_verir():
    kareler = []
    rng = np.random.default_rng(9)
    a = _akis(_sessiz(rng, 3) + _konusma(rng, 3), blok_fn=lambda akis, t, r: kareler.append((akis, t, r)))
    a.run()
    assert len(kareler) == 6 and all(k[0] == "mikrofon" for k in kareler)
    capa = a.epoch_capa
    assert abs(kareler[0][1] - (capa + 0.05)) < 1e-9 and abs(kareler[5][1] - (capa + 0.55)) < 1e-9   # blok ortasi (epoch)
    assert kareler[0][2] < 0.01 < kareler[5][2]


# ---------------------------------------------------------------- STT kesintisi

class _Kesintili:
    """coz: acik=False iken ConnectionError; saglik acik'a bagli."""
    verbose = True
    yeniden_deneme = 0

    def __init__(self):
        self.acik = False
        self.cagrilar = 0
        self.metin = lambda n: f"{['Birinci', 'İkinci', 'Üçüncü'][n]} klibin uzun bir toplantı cümlesi."
        self.saglik_cagrilari = 0

    def coz(self, ses_, onceki=""):
        self.cagrilar += 1
        if not self.acik:
            raise ses.requests.ConnectionError("baglanti reddedildi")
        return [{"text": self.metin(int(round(float(ses_[0]) * 1000)))}]

    def saglik(self, deneme=1):
        self.saglik_cagrilari += 1
        return self.acik


def _kesinti_p(no, akis="loopback"):
    p = _klip_p(no=no, akis=akis, bas=T0 + 10.0 * no)
    p["ses"] = np.full(16000 * 2, no / 1000.0, dtype="float32")
    return p


def _bekle(kosul, sn=5.0):
    son = time.time() + sn
    while time.time() < son:
        if kosul():
            return True
        time.sleep(0.01)
    return kosul()


def test_stt_kesintisi_bosluk_olaylari_ve_yedek_diske_yazilir_donunce_sirayla_cozulur(tmp_path, monkeypatch):
    monkeypatch.setattr(ses, "KESINTI_YOKLAMA", 0.02)
    olaylar, loglar = [], []
    s = ses.SesServisi("", olay_fn=olaylar.append, olay=lambda t, v: loglar.append((t, v)),
                       kesinti_klasor=str(tmp_path))
    stt_ = s.stt = _Kesintili()
    # worker: ilk istekte kesinti acilir, kalan klipler istek atmadan saklanir
    for no in (0, 1, 2):
        s._isle(_kesinti_p(no, "loopback" if no != 1 else "mikrofon"))
    assert stt_.cagrilar == 1
    assert [e["tur"] for e in olaylar] == ["bosluk"] * 3 and {e["neden"] for e in olaylar} == {"stt_yok"}
    assert [e["akis"] for e in olaylar] == ["loopback", "mikrofon", "loopback"]
    assert olaylar[0]["bas"] == T0 and olaylar[0]["bit"] == T0 + 2.0
    d = s.stt_durumu
    assert d["durum"] == "kesinti" and d["bekleyen"] == 3 and d["kayit"] is True and d["eksik"] == []
    assert any(t == "stt_durum" and v["durum"] == "kesinti" for t, v in loglar)
    assert not any("altyazı" in str(v) for t, v in loglar if t == "log")          # altyazi moduna dusme yok
    assert sorted(os.listdir(os.path.join(str(tmp_path), "olcum", "kesinti"))) == \
        [f"{n:06d}.{u}" for n in (1, 2, 3) for u in ("json", "wav")]
    # servis geri geldi: bekci diskteki klipleri klip_no sirasiyla cozer
    stt_.acik = True
    assert _bekle(lambda: len([e for e in olaylar if e["tur"] == "klip"]) == 3 and s.stt_durumu["durum"] == "ok")
    klipler = [e for e in olaylar if e["tur"] == "klip"]
    assert [e["klip_no"] for e in klipler] == [1, 2, 3]                            # sirayla, ilk gelen once
    assert [e["akis"] for e in klipler] == ["loopback", "mikrofon", "loopback"]
    assert klipler[0]["bas"] == T0 and klipler[1]["bas"] == T0 + 10.0 and klipler[0]["zarf"] == [0.1] * 20
    assert klipler[0]["text"].startswith("Birinci")
    assert s.stt_durumu["bekleyen"] == 0 and os.listdir(os.path.join(str(tmp_path), "olcum", "kesinti")) == []
    s._dur.set()


def test_kesinti_kuyrugu_basinda_kalici_5xx_veren_klip_atlanir_kalanlar_cozulur(tmp_path, monkeypatch):
    """Bulgu: /health 200 donerken tek klip kalici 500 verince kuyruk basinda sonsuza dek yeniden deneniyor, durum
    'kesinti' kaliyor, arkasindaki klipler hic cozulmuyordu. KUYRUK_KLIP_DENEME kez ust uste hatadan sonra klip
    'stt_hata' boslugu olur ve kuyruktan duser; kalanlar sirayla cozulur."""
    monkeypatch.setattr(ses, "KESINTI_YOKLAMA", 0.02)
    olaylar = []
    s = ses.SesServisi("", olay_fn=olaylar.append, kesinti_klasor=str(tmp_path))
    stt_ = s.stt = _Kesintili()
    stt_.metin = lambda n: f"{n}. klibin uzun bir toplantı cümlesi."
    gercek_coz = stt_.coz

    def coz(ses_, onceki=""):
        if stt_.acik and int(round(float(ses_[0]) * 1000)) == 0:
            e = ses.requests.HTTPError("500 Server Error")
            e.response = type("R", (), {"status_code": 500})()
            stt_.cagrilar += 1
            raise e
        return gercek_coz(ses_, onceki)

    stt_.coz = coz
    for no in range(5):
        s._isle(_kesinti_p(no))                                   # servis kapali: hepsi diske
    assert s.stt_durumu["bekleyen"] == 5
    stt_.acik = True
    assert _bekle(lambda: len([e for e in olaylar if e["tur"] == "klip"]) == 4 and s.stt_durumu["durum"] == "ok")
    assert [e["klip_no"] for e in olaylar if e["tur"] == "klip"] == [2, 3, 4, 5]
    assert [(e["neden"], e["bas"]) for e in olaylar if e["tur"] == "bosluk"][-1] == ("stt_hata", T0)
    d = s.stt_durumu
    assert d["bekleyen"] == 0 and [T0, T0 + 2.0] in d["eksik"]
    s._dur.set()


def test_stt_kesintisi_izin_yoksa_diske_yazilmaz_eksik_aralik_tutulur():
    olaylar = []
    s = ses.SesServisi("", olay_fn=olaylar.append)                                  # olcum_kaydi/kesinti_kaydi kapali
    s.stt = _Kesintili()
    for no in (0, 1, 2):
        s.kuyruk.put(_kesinti_p(no))
    s._dur.set()
    s._calis()
    assert [e["tur"] for e in olaylar] == ["bosluk"] * 3
    d = s.stt_durumu
    assert d["durum"] == "kesinti" and d["bekleyen"] == 0 and d["kayit"] is False
    assert d["eksik"] == [[T0, T0 + 22.0]]                                          # yakin araliklar birlesir (UI: "STT yok hh:mm-hh:mm")
    assert "baglanti" in d["hata"]


def test_stt_istemci_hatasi_400_yalniz_o_klibi_atar_kesinti_acmaz():
    olaylar = []
    s = ses.SesServisi("", olay_fn=olaylar.append)

    class Red:
        verbose = True
        yeniden_deneme = 0

        def coz(self, ses_, onceki=""):
            r = type("R", (), {"status_code": 422})()
            e = ses.requests.HTTPError("422")
            e.response = r
            raise e

    s.stt = Red()
    s.kuyruk.put(_kesinti_p(0))
    s._dur.set()
    s._calis()
    assert [(e["tur"], e["neden"]) for e in olaylar] == [("bosluk", "stt_hata")] and s.stt_durumu["durum"] == "ok"


def test_kuyruk_tasmasi_diske_yazilir_sira_kapanir(tmp_path, monkeypatch):
    monkeypatch.setattr(ses, "KESINTI_YOKLAMA", 0.02)
    olaylar = []
    s = ses.SesServisi("", olay_fn=olaylar.append, kesinti_klasor=str(tmp_path))
    stt_ = s.stt = _Kesintili()
    stt_.acik = True
    p = _kesinti_p(0)
    assert s._tasma(p) is True                                                      # diske yazildi, bosluk olayi cikti
    assert [(e["tur"], e["neden"]) for e in olaylar[:1]] == [("bosluk", "kuyruk_tasti")]
    assert s._sonraki == 1                                                          # numara kapandi: sira takilmaz
    assert _bekle(lambda: any(e["tur"] == "klip" for e in olaylar))                 # kuyruk bosalinca bekci cozer
    s._dur.set()


def test_baslat_stt_yoksa_altyazi_moduna_dusmez_yakalamayi_baslatir(monkeypatch):
    baslayan, loglar = [], []

    class SahteAkisT(threading.Thread):
        def __init__(self, kaynak, kuyruk, **kw):
            super().__init__(daemon=True)
            self.kaynak = kaynak
            self.kw = kw

        def run(self):
            baslayan.append(self.kaynak)

        def durdur(self):
            pass

    monkeypatch.setattr(ses, "AkisYakalayici", SahteAkisT)
    monkeypatch.setattr(ses, "KESINTI_YOKLAMA", 0.02)
    s = ses.SesServisi("https://stt.ornek", olay=lambda t, v: loglar.append((t, v)))
    s.stt = _Kesintili()                                                            # saglik False
    assert s.baslat() is True
    assert _bekle(lambda: sorted(baslayan) == ["loopback", "mikrofon"])
    assert s.stt_durumu["durum"] == "kesinti"
    assert not any("altyazı moduna" in str(v) for t, v in loglar)
    # pasif tespit akislara baglandi
    assert s.kulaklik is None and s._kulaklik_tespit is not None
    s.durdur()
    assert s._bekci is None or not s._bekci.is_alive()


def test_baslat_mod_cift_tek_kulaklik_olayi_yayar(monkeypatch):
    olaylar = []

    class SahteAkisT(threading.Thread):
        def __init__(self, *a, **kw):
            super().__init__(daemon=True)

        def run(self):
            pass

        def durdur(self):
            pass

    monkeypatch.setattr(ses, "AkisYakalayici", SahteAkisT)
    s = ses.SesServisi("https://stt.ornek", mod="cift", olay_fn=olaylar.append)
    s.stt = _KlipStt(lambda x: [])
    assert s.baslat() and s.kulaklik is True
    assert olaylar == [{"tur": "kulaklik", "durum": True, "guven": 1.0}]
    s.durdur()


# ================================================================ Faz 2: anti-alias'li yeniden ornekleme, dar tekrar suzgeci

def _ton(f, hiz, n, genlik=0.5):
    return (genlik * np.sin(2 * np.pi * f * np.arange(n) / hiz)).astype("float32")


def _bilesen(y, f, fs=ses.ORNEK):
    """y'nin f Hz'deki genligi (tek frekans DFT; kenar etkisi icin bas/son kirpilir)."""
    y = y[300:-300]
    t = np.arange(len(y)) / fs
    return float(abs(np.sum(y * np.exp(-2j * np.pi * f * t))) * 2 / len(y))


def _interp_eski(x, hiz):
    """Eski yol (np.interp, suzgecsiz): aliasing karsilastirmasi icin."""
    hedef = np.arange(len(x) * ses.ORNEK // hiz) * (hiz / ses.ORNEK)
    return np.interp(hedef, np.arange(len(x)), x).astype("float32")


@pytest.mark.parametrize("hiz", [44100, 48000])
def test_yeniden_ornekle_alias_enerjisi_yok_eskisi_vardi(hiz):
    """10 kHz tonu 16 kHz'te 6 kHz'e katlanir; suzgecli yolda enerji ~yok, np.interp'te genligin buyuk kismi."""
    x = _ton(10000, hiz, hiz * 2)
    yeni = ses.yeniden_ornekle(x, hiz)
    eski = _interp_eski(x, hiz)
    assert _bilesen(eski, 6000) > 0.1                                   # sorun gercek: alias genligi ~0.2-0.5
    assert _bilesen(yeni, 6000) < 0.5 * 1e-3                            # >= 60 dB bastirma
    for f in (12000, 15000, 20000):                                     # baska stopband tonlari
        al = abs(f - 16000 * round(f / 16000))
        assert _bilesen(ses.yeniden_ornekle(_ton(f, hiz, hiz * 2), hiz), al) < 0.5 * 1e-3


@pytest.mark.parametrize("hiz", [44100, 48000])
def test_yeniden_ornekle_bant_ici_ton_korunur(hiz):
    for f, alt in ((300, 0.97), (1000, 0.97), (3000, 0.97), (5000, 0.97), (6500, 0.85)):
        y = ses.yeniden_ornekle(_ton(f, hiz, hiz * 2), hiz)
        assert alt * 0.5 <= _bilesen(y, f) <= 1.03 * 0.5, (f, _bilesen(y, f))
    assert len(y) == int(np.ceil(hiz * 2 * ses.ORNEK / hiz))


def test_yeniden_ornekle_ayni_hizda_kopya():
    x = _ton(500, 16000, 1000)
    y = ses.yeniden_ornekle(x, 16000)
    assert np.array_equal(x, y) and y is not x


def test_akan_ornekleyici_bloklar_arasi_sureklilik_tek_seferle_ayni():
    """Blok blok akan sonuc (gecikmesiz mod) tek seferlik sonucla birebir; blok sinirlarinda sicrama yok."""
    rng = np.random.RandomState(3)
    x = (0.3 * rng.randn(44100 * 2)).astype("float32")
    tek = ses.yeniden_ornekle(x, 44100)
    r = ses.YenidenOrnekleyici(44100, gecikme=False)
    parcalar = [r.isle(x[i:i + 4410]) for i in range(0, len(x), 4410)] + [r.bitir()]
    akan = np.concatenate(parcalar)[:len(tek)]
    assert len(akan) == len(tek) and float(np.max(np.abs(akan - tek))) < 1e-5


@pytest.mark.parametrize("hiz,blok", [(48000, 4800), (44100, 4410), (32000, 3200), (22050, 2205)])
def test_mikrofon_modu_ilk_blok_tam_blok_uzunlugunda_ve_toplam_oran_dogru(hiz, blok):
    r = ses.YenidenOrnekleyici(hiz)
    x = _ton(440, hiz, blok * 40)
    ciktilar = [r.isle(x[i:i + blok]) for i in range(0, len(x), blok)]
    assert len(ciktilar[0]) == ses.BLOK
    assert abs(sum(len(c) for c in ciktilar) - 40 * ses.BLOK) <= 1
    y = np.concatenate(ciktilar)
    # sabit kisa gecikme (H ornek) disinda sinus bozulmaz: ardisik fark siniri
    assert float(np.max(np.abs(np.diff(y[200:])))) <= 0.5 * 2 * np.pi * 440 / ses.ORNEK * 1.05
    assert r.H / hiz < 0.004                                            # gecikme < 4 ms


def test_mikrofon_akisi_44khz_cihazda_alias_yok(monkeypatch):
    """Gercek yol (_bloklar_mikrofon): 44.1 kHz cihaz, 10 kHz tonu; geri cagirma ham bloklari koyar, donusum akis ipinde."""
    import sys
    import types
    hiz, blok = 44100, 4410
    x = _ton(10000, hiz, blok * 12)

    class Akis:
        def __init__(self, samplerate, channels, dtype, blocksize, device, callback):
            assert (samplerate, blocksize) == (hiz, blok)
            self.cb = callback

        def __enter__(self):
            for i in range(12):
                self.cb(x[i * blok:(i + 1) * blok].reshape(-1, 1), blok, None, None)
            return self

        def __exit__(self, *a):
            return False

    sahte = types.SimpleNamespace(InputStream=Akis, query_devices=lambda cihaz, tur: {"default_samplerate": float(hiz)})
    monkeypatch.setitem(sys.modules, "sounddevice", sahte)
    a = ses.AkisYakalayici("mikrofon", queue.Queue(), cihaz=3)
    gen = a._bloklar_mikrofon()
    bloklar = [next(gen) for _ in range(10)]
    assert len(bloklar[0]) == ses.BLOK and all(b.dtype == np.float32 for b in bloklar)
    y = np.concatenate(bloklar)
    assert _bilesen(y, 6000) < 0.5 * 1e-3 and float(np.sqrt(np.mean(y[300:-300] ** 2))) < 1e-3


def _sahte_resample_poly(olcek=1.0):
    """scipy.signal.resample_poly'nin davranisi (h *= up, merkezlenmis, zero-pad) numpy ile; olcek!=1: bozuk arka uc."""
    def resample_poly(x, up, down, window=None):
        h = np.asarray(window, dtype=np.float64) * up * olcek
        yari = (len(h) - 1) // 2
        xu = np.zeros(len(x) * up)
        xu[::up] = x
        tam = np.convolve(xu, h)
        n_cikis = int(np.ceil(len(x) * up / down))
        return tam[yari:yari + len(xu)][::down][:n_cikis]
    return resample_poly


@pytest.mark.parametrize("hiz", [48000, 32000, 24000])
def test_scipy_varsa_resample_poly_kullanilir_numpy_ile_ayni_sonuc(monkeypatch, hiz):
    rng = np.random.RandomState(5)
    x = (0.3 * rng.randn(hiz)).astype("float32")
    numpy_r = ses.YenidenOrnekleyici(hiz, arka_uc="numpy")
    assert numpy_r.arka_uc == "numpy"
    monkeypatch.setattr(ses, "_scipy_resample_poly", lambda: _sahte_resample_poly())
    scipy_r = ses.YenidenOrnekleyici(hiz)
    assert scipy_r.arka_uc == "scipy"
    blok = hiz // 10
    a = np.concatenate([numpy_r.isle(x[i:i + blok]) for i in range(0, len(x), blok)])
    b = np.concatenate([scipy_r.isle(x[i:i + blok]) for i in range(0, len(x), blok)])
    assert len(a) == len(b) and float(np.max(np.abs(a - b))) < 5e-3


def test_scipy_arka_ucu_beklenmedik_davranista_numpy_a_duser(monkeypatch):
    monkeypatch.setattr(ses, "_scipy_resample_poly", lambda: _sahte_resample_poly(olcek=1.5))     # kazanc bozuk
    assert ses.YenidenOrnekleyici(48000).arka_uc == "numpy"

    def patlar(*a, **k):
        raise ValueError("beklenmedik imza")
    monkeypatch.setattr(ses, "_scipy_resample_poly", lambda: patlar)
    assert ses.YenidenOrnekleyici(48000).arka_uc == "numpy"


def test_scipy_kurulu_degilse_numpy(monkeypatch):
    monkeypatch.setattr(ses, "_scipy_resample_poly", lambda: None)
    assert ses.YenidenOrnekleyici(44100).arka_uc == "numpy"


# --- dar tekrar suzgeci (SesServisi._tekrar_mi) ---

def _tekrar_dizisi(metin, sureler, akis="loopback"):
    s = ses.SesServisi("", olay_fn=lambda e: None)
    return [s._tekrar_mi(dt.datetime.fromtimestamp(T0 + t), akis, metin) for t in sureler]


def test_kisa_cevaplar_ve_iki_kelimelik_cumleler_hic_elenmez():
    assert _tekrar_dizisi("Tamam, anladım.", [0, 5, 10, 15, 20]) == [False] * 5
    assert _tekrar_dizisi("Evet.", [0, 1, 2, 3]) == [False] * 4
    assert _tekrar_dizisi("Peki, tamam.", [0, 2, 4, 6]) == [False] * 4


def test_ayni_uc_kelimelik_cumle_30sn_icinde_elenir_sonrasi_kalir():
    assert _tekrar_dizisi("Raporu perşembeye yetiştirelim.", [0, 10]) == [False, True]
    assert _tekrar_dizisi("Raporu perşembeye yetiştirelim.", [0, 45]) == [False, False]       # 30 sn'den uzak: gercek tekrar


def test_ucuncu_tekrar_pencerede_elenir_farkli_akis_yakinligi_saymaz():
    assert _tekrar_dizisi("Raporu perşembeye yetiştirelim.", [0, 100, 200]) == [False, False, True]
    s = ses.SesServisi("", olay_fn=lambda e: None)
    an = lambda t: dt.datetime.fromtimestamp(T0 + t)                                          # noqa: E731
    assert s._tekrar_mi(an(0), "loopback", "Raporu perşembeye yetiştirelim.") is False
    assert s._tekrar_mi(an(5), "mikrofon", "Raporu perşembeye yetiştirelim.") is False       # eko tekrar degil (konusmaci eler)


def test_tekrar_penceresi_asildiktan_sonra_sayac_sifirlanir():
    assert _tekrar_dizisi("Raporu perşembeye yetiştirelim.", [0, 100, ses.TEKRAR_PENCERE_SN + 500]) == [False] * 3
