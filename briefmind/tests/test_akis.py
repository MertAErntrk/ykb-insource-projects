"""Not akisi (UYGULAMA_PLANI_v2 A, B): 'adim' olaylari, Gecmis -> 'Yeniden ozetle' (yedek + tum parcalar + not;
terim/ad duzeltmesi duzelt.py kapisindan, yalniz 'raw'i olan satirlarda), ayar varsayilanlari, llm.ayarla.
LLM sunucusu gerekmez."""
import json
import os
import re
import threading

import pytest

import ayar as ayar_mod
import llm
import motor as motor_mod
import terim as terim_mod

PARCA_OZETI = {"ozet": "Selin MVP prototipini ve test ortamını anlattı.", "konular": ["MVP prototipi"],
               "kararlar": ["Pilot çağrı merkeziyle başlayacak"],
               "aksiyonlar": [{"madde": "MVP prototipini hazırlamak", "sorumlu": "belirsiz", "tarih": "-"}],
               "acik_sorular": [], "belirsiz_terimler": []}
GENEL = {"ozet": "Toplantıda MVP prototipi ve test ortamı konuşuldu; pilotun çağrı merkeziyle başlaması kararlaştırıldı.",
         "sonraki_adim": "MVP prototipi hazırlanacak."}


class SahteLLM:
    def __init__(self):
        self.gorevler = []
        self.kilit = threading.Lock()

    def __call__(self, mesajlar, max_tokens, sema=None, effort="medium", temperature=0.6, dusunme=True):
        gorev = llm._gorev_adi(mesajlar)
        with self.kilit:
            self.gorevler.append(gorev)
        if gorev == "bolum":
            return json.dumps(PARCA_OZETI, ensure_ascii=False), "stop"
        if gorev == "duzeltme":
            # duzelt.py cevabi: {"oneriler": [{satir, eski, yeni, neden}]}; "kubernetis" -> Kubernetes (gecerli),
            # "ortamı" -> "ortam" (yaygin kelime: kapi reddeder)
            oneriler = []
            for sid, metin in re.findall(r"^\[(\d+)\] (.*)$", mesajlar[1]["content"], flags=re.M):
                if "kubernetis" in metin:
                    oneriler.append({"satir": int(sid), "eski": "kubernetis", "yeni": "Kubernetes", "neden": "terim"})
                if "ortamı" in metin:
                    oneriler.append({"satir": int(sid), "eski": "ortamı", "yeni": "Kubernetes", "neden": "terim"})
            return json.dumps({"oneriler": oneriler}, ensure_ascii=False), "stop"
        if gorev == "liste":
            return json.dumps({k: PARCA_OZETI[k] for k in ("kararlar", "aksiyonlar", "acik_sorular")},
                              ensure_ascii=False), "stop"
        return json.dumps(GENEL, ensure_ascii=False), "stop"


@pytest.fixture(autouse=True)
def yerel(monkeypatch):
    monkeypatch.setattr(llm, "token_say", llm.token_tahmin)


def _havuz(tmp_path, terimler=("Kubernetes",)):
    h = terim_mod.TerimHavuzu(str(tmp_path / "terimler" / "terimler.json"))
    h.ekle("strateji", list(terimler), "davet")
    return h


def _toplanti(tmp_path, parca=3, ozetli=True, not_md=None, olay=None, surum=None, raw=False, duzelt=False,
              metin="MVP prototipini ben hazırlarım, kubernetis sonrası test ortamı {no}"):
    k = tmp_path / "toplantilar" / "2026-09-23_strateji"
    (k / "parcalar").mkdir(parents=True)
    (k / "meta.json").write_text(json.dumps({"baslik": "Strateji", "tarih": "2026-09-23", "durum": "devam",
                                             "katilimcilar": ["Selin Kara", "Kemal Tuncer"], "parca": parca,
                                             "kaynak": "ses"}), encoding="utf-8")
    for no in range(1, parca + 1):
        satirlar = [{"id": no, "ts": f"10:0{no}:00", "speaker": "Selin Kara",
                     "text": metin.format(no=no)}]
        if surum:                                    # Faz 1 satiri: konusmaci zamandan, kesin
            satirlar[0].update(surum=surum, spk_guven=0.9, durum="karar", kaynak="ses")
        if raw:                                      # v2 satiri: ham Whisper metni saklanir
            satirlar[0]["raw"] = satirlar[0]["text"]
        (k / "parcalar" / f"parca_{no:03d}.json").write_text(json.dumps(
            {"sira": no, "neden": "token", "token": 900, "baslangic": satirlar[0]["ts"], "bitis": satirlar[0]["ts"],
             "satirlar": satirlar, "duzeltmeler": [], "ozet": dict(PARCA_OZETI) if ozetli else None},
            ensure_ascii=False), encoding="utf-8")
    if not_md is not None:
        (k / "not.md").write_text(not_md, encoding="utf-8")
    olaylar = []
    m = motor_mod.Motor.yukle(str(k), _havuz(tmp_path),
                              olay=olay or (lambda t, v: olaylar.append((t, v))), duzelt=duzelt,
                              ben="Deniz Can Öztürk")
    return m, k, olaylar


def test_motor_eski_onay_akisi_yok(tmp_path):
    """Faz 2: sozluk / oneri / onay akisi silindi (Sozluk modulu yok, kararlari_uygula yok)."""
    m, k, _ = _toplanti(tmp_path)
    for ad in ("sozluk", "sozlugu_uygula", "kararlari_uygula", "oneri_ekle", "bekleyen_oneriler", "oneriler"):
        assert not hasattr(m, ad), ad
    assert isinstance(m.terimler, terim_mod.TerimHavuzu)
    m.kapat()


def test_yeniden_ozetle_kapidan_gecen_duzeltme_raw_korunur(tmp_path, monkeypatch):
    """raw'i olan (v2) satirda LLM onerisi duzelt.kapi'dan gecerse metin degisir, raw ayni kalir, kabul/red kaydi
    tutulur; yaygin kelimeyi terime ceviren oneri (ortamı -> Kubernetes) reddedilir. Gozlem terim havuzuna yazilir."""
    sahte = SahteLLM()
    monkeypatch.setattr(llm, "sor", sahte)
    m, k, olaylar = _toplanti(tmp_path, parca=2, surum=2, raw=True, duzelt=True)
    assert m.yeniden_ozetle() == 2
    assert sahte.gorevler.count("duzeltme") == 2
    for n in (1, 2):
        s = m.parca_oku(n)["satirlar"][0]
        assert s["raw"].count("kubernetis") == 1 and "kubernetis" not in s["text"] and "Kubernetes sonrası" in s["text"]
        assert "test ortamı" in s["text"] and s["duzeltmeler"][0]["eski"] == "kubernetis"
        assert m.parca_oku(n)["duzeltmeler"] == [["kubernetis", "Kubernetes"]]
    kayitlar = [json.loads(x) for x in (k / "duzelt_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(kayitlar) == 2 and kayitlar[0]["kabul"] and kayitlar[0]["red"][0]["eski"] == "ortamı"
    g = m.terimler.gozlemler
    assert len(g) == 1 and g[0]["eski"] == "kubernetis" and g[0]["hedef"] == "Kubernetes" and g[0]["sayi"] == 2
    assert g[0]["toplantilar"] == ["2026-09-23_strateji"]                 # iki parca, tek toplanti
    m.kapat()


def test_yeniden_ozetle_eski_kayitta_duzeltme_calismaz(tmp_path, monkeypatch):
    """raw'i olmayan (v2 oncesi) satir yeniden ozetlemede de duzeltilmez: LLM duzeltme istegi hic gitmez."""
    sahte = SahteLLM()
    monkeypatch.setattr(llm, "sor", sahte)
    m, k, _ = _toplanti(tmp_path, parca=2, duzelt=True)
    assert m.yeniden_ozetle() == 2
    assert "duzeltme" not in sahte.gorevler and sahte.gorevler.count("bolum") == 2
    assert "kubernetis" in m.parca_oku(1)["satirlar"][0]["text"]
    assert not (k / "duzelt_log.jsonl").exists()
    m.kapat()


def test_supheli_satir_ozete_girmez_duzeltilmez(tmp_path, monkeypatch):
    sahte = SahteLLM()
    monkeypatch.setattr(llm, "sor", sahte)
    m, k, _ = _toplanti(tmp_path, parca=1, surum=2, raw=True, duzelt=True)
    p = m.parca_oku(1)
    p["satirlar"][0]["supheli"] = True
    m.parca_yaz(p)
    m.yeniden_ozetle()
    assert sahte.gorevler == []                                   # duzeltme de ozet de LLM'e gitmedi
    assert "kubernetis" in m.parca_oku(1)["satirlar"][0]["text"] and m.parca_oku(1)["ozet"]["ozet"] == ""
    m.kapat()


def test_notu_uret_adim_olaylari_ve_sorumlu_dogrulama(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "sor", SahteLLM())
    m, k, olaylar = _toplanti(tmp_path, parca=2, surum=2)
    md = m.notu_uret()
    m.kapat()
    adimlar = [v["metin"] for t, v in olaylar if t == "adim"]
    assert adimlar == ["Kararlar ve aksiyonlar birleştiriliyor", "Özet paragrafı yazılıyor", "Dayanak kontrolü",
                       "Not kaydediliyor"]
    assert all(set(v) == {"metin", "no", "toplam"} for t, v in olaylar if t == "adim")
    # K8: 'belirsiz' aksiyonun kaynak satirinda Selin 'ben hazirlarim' diyor -> sorumlu Selin Kara (kesin satir)
    assert "| MVP prototipini hazırlamak | Selin Kara |" in md
    assert json.loads((k / "meta.json").read_text(encoding="utf-8"))["durum"] == "tamam"


def test_eski_kayitta_sorumlu_uretilmez_transkript_ad_soru_isaretli(tmp_path, monkeypatch):
    """Faz 1 gocu: surumsuz (v2 oncesi) satir spk_guven 0.5 -> 'Selin Kara?'; 'ben hazirlarim' sahip uretmez."""
    monkeypatch.setattr(llm, "sor", SahteLLM())
    m, k, _ = _toplanti(tmp_path, parca=2)
    md = m.notu_uret()
    assert "| MVP prototipini hazırlamak | belirsiz |" in md
    assert m.transkript_metni().startswith("Selin Kara?: ")
    m.kapat()


def test_notu_uret_ozetsiz_parca_adimi(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "sor", SahteLLM())
    m, k, olaylar = _toplanti(tmp_path, parca=2, ozetli=False)
    m.notu_uret()
    m.kapat()
    adimlar = [v for t, v in olaylar if t == "adim"]
    assert adimlar[0] == {"metin": "Parça 1/2 özetleniyor", "no": 1, "toplam": 2}


def test_not_yedekle(tmp_path):
    m, k, _ = _toplanti(tmp_path, not_md="# eski not\n")
    yedek = m.not_yedekle()
    assert yedek and os.path.basename(yedek).startswith("not.md.yedek-")
    assert open(yedek, encoding="utf-8").read() == "# eski not\n"
    ikinci = m.not_yedekle()                                     # ayni dakikada ikinci yedek eskisini ezmez
    assert ikinci != yedek and os.path.exists(yedek)
    m.kapat()
    m2, _, _ = _toplanti(tmp_path / "b")
    assert m2.not_yedekle() is None
    m2.kapat()


def test_yeniden_ozetle_tum_parcalar_ve_not(tmp_path, monkeypatch):
    """Gecmis -> 'Yeniden ozetle': yedek + TUM parcalar (ozetli olanlar da) + yeni not."""
    sahte = SahteLLM()
    monkeypatch.setattr(llm, "sor", sahte)
    m, k, olaylar = _toplanti(tmp_path, parca=3, not_md="# eski not\n")
    yedek = m.not_yedekle()
    assert m.yeniden_ozetle() == 3
    assert sahte.gorevler.count("bolum") == 3                   # ozetli parcalar da yeniden ozetlendi
    parca_adimlari = sorted(v["no"] for t, v in olaylar if t == "adim" and v["toplam"] == 3)
    assert parca_adimlari == [1, 2, 3]
    assert sorted(v["sira"] for t, v in olaylar if t == "parca_ozetlendi") == [1, 2, 3]
    m.bitir()
    md = m.notu_uret()
    m.kapat()
    assert md.startswith("# Strateji — 2026-09-23") and (k / "not.md").read_text(encoding="utf-8") == md
    assert open(yedek, encoding="utf-8").read() == "# eski not\n"


def test_ayar_varsayilanlari_stt_ipucu_acik_inceleme_ayari_yok(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    v = ayar_mod.ayar_oku()
    assert v["stt_ipucu"] is True and v["stt_repetition_penalty"] is None and "otomatik_not" not in v
    (tmp_path / "config.json").write_text(json.dumps({"stt_ipucu": False, "stt_repetition_penalty": 1.1}),
                                          encoding="utf-8")
    v = ayar_mod.ayar_oku()
    assert v["stt_ipucu"] is False and v["stt_repetition_penalty"] == 1.1
    assert ayar_mod.AYAR_HATASI is None
    (tmp_path / "config.json").write_text("{bozuk", encoding="utf-8")
    assert ayar_mod.ayar_oku()["stt_ipucu"] is True and ayar_mod.AYAR_HATASI
    assert ayar_mod.tls_dogrulama() is False


def test_llm_ayarla():
    eski = (llm.ROUTE, llm.MODEL, llm.BAGLAM_PENCERESI)
    try:
        llm.ayarla({"route": "http://llm.ornek:9000/v1", "model": "deneme-model", "context": "32768"})
        assert (llm.ROUTE, llm.MODEL, llm.BAGLAM_PENCERESI) == ("http://llm.ornek:9000/v1", "deneme-model", 32768)
        assert str(llm.client.base_url).rstrip("/") == "http://llm.ornek:9000/v1"
        llm.ayarla({"context": "gecersiz"})
        assert llm.ROUTE == "http://localhost:8000/v1" and llm.BAGLAM_PENCERESI == 16384
    finally:
        llm.ayarla({"route": eski[0], "model": eski[1], "context": eski[2]})
