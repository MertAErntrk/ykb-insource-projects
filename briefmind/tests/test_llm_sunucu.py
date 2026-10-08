"""LLM sunucusu degisikligine dayaniklilik: adres/tokenize yolu, sunucu tanisi (model + baglam penceresi),
hata siniflandirmasi, sema/baglam 400 ayrimi, akisli cevap, pencereye olcekli butceler ve
tools/llm_teshis.py'nin agsiz kisimlari. Ag kullanilmaz (sahte istemciler)."""
import json
import os
import ssl
import sys
import types

import httpx
import openai
import pytest

import llm
import motor as motor_mod

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import llm_teshis  # noqa: E402

GERCEK_TOKEN_SAY = llm.token_say             # otomatik fikstur token_say'i tahminle degistirir


@pytest.fixture(autouse=True)
def temiz_ayar(monkeypatch):
    """Her test kendi ayariyla baslar; sonunda varsayilan (config'siz) ayara donulur."""
    monkeypatch.setattr(llm, "token_say", llm.token_tahmin)
    monkeypatch.setattr(llm, "GUNLUK_FN", None)
    monkeypatch.setattr(llm, "UYARI_FN", None)
    yield
    llm.ayarla({})


def _istek():
    return httpx.Request("POST", "http://llm.ornek/v1/chat/completions")


def _durum_hatasi(sinif, kod, govde, tur="application/json"):
    icerik = govde if isinstance(govde, str) else json.dumps(govde)
    yanit = httpx.Response(kod, request=_istek(), content=icerik.encode(), headers={"content-type": tur})
    return sinif(f"Error code: {kod}", response=yanit, body=govde if isinstance(govde, dict) else icerik)


class SahteHttp:
    """llm._http yerine: GET /models ve POST /tokenize."""

    def __init__(self, modeller=None, hata=None, tokenize=None):
        self.modeller, self.hata, self.tokenize = modeller, hata, tokenize
        self.istekler = []

    def get(self, url, timeout=None):
        self.istekler.append(("GET", url))
        if self.hata:
            raise self.hata
        return httpx.Response(200, json={"data": self.modeller or []}, request=httpx.Request("GET", url))

    def post(self, url, json=None):
        self.istekler.append(("POST", url, json))
        if isinstance(self.tokenize, Exception):
            raise self.tokenize
        return httpx.Response(200 if self.tokenize is not None else 404, json={"count": self.tokenize},
                              request=httpx.Request("POST", url))


def _cevap(icerik="{}", neden="stop", dusunce=None, prompt=100, cikti=50):
    mesaj = types.SimpleNamespace(content=icerik, reasoning_content=dusunce)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(finish_reason=neden, message=mesaj)],
                                 usage=types.SimpleNamespace(prompt_tokens=prompt, completion_tokens=cikti))


# ---------------------------------------------------------------- adres ve tokenize yolu

@pytest.mark.parametrize("girdi, route, tokenize", [
    ("https://llm.ornek/v1", "https://llm.ornek/v1", "https://llm.ornek/tokenize"),
    ("https://llm.ornek/v1/", "https://llm.ornek/v1", "https://llm.ornek/tokenize"),
    ("https://llm.ornek", "https://llm.ornek/v1", "https://llm.ornek/tokenize"),
    (" https://llm.ornek/llm/v1 ", "https://llm.ornek/llm/v1", "https://llm.ornek/llm/tokenize"),
    ("https://llm.ornek/api/v1/x/v1", "https://llm.ornek/api/v1/x/v1", "https://llm.ornek/api/v1/x/tokenize"),
])
def test_route_ve_tokenize_yolu(girdi, route, tokenize):
    llm.ayarla({"route": girdi})
    assert llm.ROUTE == route and llm.TOKENIZE_URL == tokenize
    assert str(llm.client.base_url).rstrip("/") == route
    assert bool(llm.AYAR_UYARILARI) == (girdi.strip() != route)


def test_ayarla_ag_istegi_yapmaz_ve_varsayilanlar():
    llm.ayarla({"context": "32k", "model": "  "})
    assert llm.MODEL == "" and llm.CONFIG_PENCERE == 0 and llm.BAGLAM_PENCERESI == llm.VARSAYILAN_PENCERE
    assert llm.AKIS is True and llm.client.max_retries == 1
    llm.ayarla({"llm_akis": False, "llm_tekrar": 0, "llm_key": "gizli", "parca_token": 3500})
    assert llm.AKIS is False and llm.client.max_retries == 0 and llm.client.api_key == "gizli"
    assert llm._http.headers["Authorization"] == "Bearer gizli"
    assert llm.butce("parca") == 3500


def test_tokenize_messages_bicimi_ve_geri_cekilme(monkeypatch):
    monkeypatch.setattr(llm, "token_say", GERCEK_TOKEN_SAY)
    monkeypatch.setattr(llm, "SUNUCU_TOKENIZER", True)
    llm.ayarla({"route": "https://llm.ornek/v1", "model": "m"})
    h = SahteHttp(tokenize=123)
    monkeypatch.setattr(llm, "_http", h)
    mesajlar = [{"role": "user", "content": "merhaba"}]
    assert llm._giris_token(mesajlar) == 123                      # sablon dahil kesin sayim: +60 yok
    assert h.istekler[-1][1] == "https://llm.ornek/tokenize" and h.istekler[-1][2]["messages"] == mesajlar
    uyarilar = []
    monkeypatch.setattr(llm, "UYARI_FN", uyarilar.append)
    h.tokenize = httpx.ConnectError("yok")
    assert llm.token_say("abcdef") == 2 and llm.token_say("abcdefghi") == 3     # tahmine dustu
    n = len(h.istekler)
    llm.token_say("baska bir metin")                                            # 5 dk sunucu sorulmaz
    assert len(h.istekler) == n and len(uyarilar) == 1 and "ConnectError" in uyarilar[0]


# ---------------------------------------------------------------- sunucu tanisi

def test_tani_tek_modeli_secer_ve_pencereyi_sunucudan_alir(monkeypatch):
    llm.ayarla({"route": "https://llm.ornek/v1", "model": "Eski-Model"})
    monkeypatch.setattr(llm, "_http", SahteHttp([{"id": "yeni-model", "max_model_len": 40960}]))
    gunluk = []
    monkeypatch.setattr(llm, "GUNLUK_FN", gunluk.append)
    uyarilar = llm.sunucuyu_tani()
    assert llm.MODEL == "yeni-model" and llm.BAGLAM_PENCERESI == 40960
    assert any("Eski-Model" in u and "yeni-model" in u for u in uyarilar)
    assert gunluk[-1]["olay"] == "ayar" and gunluk[-1]["sunucu_pencere"] == 40960
    assert llm.butce("bolum") == 4000 and llm.butce("liste") == 8000 and llm.butce("genel") == 2000
    assert llm.sunucuyu_tani() == uyarilar                         # ikinci cagri sunucuyu yeniden sormaz


def test_tani_config_context_yalniz_ust_sinir(monkeypatch):
    llm.ayarla({"route": "https://llm.ornek/v1", "model": "m", "context": 65536})
    monkeypatch.setattr(llm, "_http", SahteHttp([{"id": "m", "max_model_len": 8192}]))
    uyarilar = llm.sunucuyu_tani()
    assert llm.BAGLAM_PENCERESI == 8192 and any("65536" in u for u in uyarilar)
    llm.ayarla({"route": "https://llm.ornek/v1", "model": "m", "context": 12000})
    monkeypatch.setattr(llm, "_http", SahteHttp([{"id": "m", "max_model_len": 32768}]))
    assert llm.sunucuyu_tani() == [] and llm.BAGLAM_PENCERESI == 12000


def test_tani_birden_cok_model_ve_erisilemeyen_sunucu(monkeypatch):
    llm.ayarla({"route": "https://llm.ornek/v1", "model": "x", "context": 16384})
    monkeypatch.setattr(llm, "_http", SahteHttp([{"id": "a"}, {"id": "b"}]))
    u = llm.sunucuyu_tani()
    assert llm.MODEL == "x" and any("a, b" in s for s in u)
    llm.ayarla({"route": "https://llm.ornek/v1", "model": "x"})
    monkeypatch.setattr(llm, "_http", SahteHttp(hata=httpx.ConnectError("ag yok")))
    u = llm.sunucuyu_tani()
    assert llm.BAGLAM_PENCERESI == 16384 and "sorgulanamadı" in u[0] and "ag yok" in u[0]


def test_ilk_istekten_once_tani_bir_kez(monkeypatch):
    llm.ayarla({"route": "https://llm.ornek/v1"})
    h = SahteHttp([{"id": "tek", "max_model_len": 16384}])
    monkeypatch.setattr(llm, "_http", h)
    monkeypatch.setattr(llm, "OTOMATIK_TANI", True)
    gorulen = []
    monkeypatch.setattr(llm.client.chat.completions, "create", lambda **kw: (gorulen.append(kw["model"]), _cevap("ok"))[1])
    llm.sor([{"role": "user", "content": "x"}], 100, dusunme=False)
    llm.sor([{"role": "user", "content": "y"}], 100, dusunme=False)
    assert gorulen == ["tek", "tek"] and sum(1 for i in h.istekler if i[0] == "GET") == 1


# ---------------------------------------------------------------- hata metinleri

def test_hata_metni_404_model_ve_yol():
    llm.ayarla({"route": "https://llm.ornek/v1", "model": "Qwen-eski"})
    e = _durum_hatasi(openai.NotFoundError, 404, {"object": "error", "message": "The model `Qwen-eski` does not exist."})
    m = llm.hata_metni(e)
    assert "'Qwen-eski' modelini tanımıyor" in m and "does not exist" in m and "Model" in m
    e = _durum_hatasi(openai.NotFoundError, 404, {"detail": "Not Found"})
    assert "'.../v1' ile bitmeli" in llm.hata_metni(e)


def test_hata_metni_baglam_anahtar_route_ve_baglanti():
    llm.ayarla({"route": "https://llm.ornek/v1", "model": "m", "context": 16384})
    govde = {"object": "error", "message": "This model's maximum context length is 8192 tokens. However, you "
                                           "requested 9000 tokens (6000 in the messages, 3000 in the completion)."}
    m = llm.hata_metni(_durum_hatasi(openai.BadRequestError, 400, govde))
    assert "bağlam penceresini aşıyor" in m and "16384" in m and "8192" in m
    assert llm._baglam_siniri(govde["message"]) == 8192
    assert "llm_key" in llm.hata_metni(_durum_hatasi(openai.AuthenticationError, 401, {"message": "no key"}))
    m = llm.hata_metni(_durum_hatasi(openai.InternalServerError, 504, "<html><h1>504 Gateway Time-out</h1></html>",
                                     "text/html"))
    assert "HTTP 504" in m and "route" in m and "llm_akis" in m
    hata = openai.APIConnectionError(request=_istek())
    try:
        raise hata from ssl.SSLCertVerificationError(1, "CERTIFICATE_VERIFY_FAILED unable to get local issuer")
    except openai.APIConnectionError as e:
        m = llm.hata_metni(e)
    assert "ulaşılamadı" in m and "CERTIFICATE_VERIFY_FAILED" in m and "TLS" in m
    assert "zaman aşımı" in llm.hata_metni(openai.APITimeoutError(request=_istek()))


def test_sor_hatayi_turkce_llmhatasi_yapar_ve_gunluge_yazar(monkeypatch):
    llm.ayarla({"route": "https://llm.ornek/v1", "model": "eski"})
    gunluk = []
    monkeypatch.setattr(llm, "GUNLUK_FN", gunluk.append)

    def create(**kw):
        raise _durum_hatasi(openai.NotFoundError, 404, {"message": "The model `eski` does not exist."})
    monkeypatch.setattr(llm.client.chat.completions, "create", create)
    with pytest.raises(llm.LlmHatasi) as h:
        llm.sor([{"role": "system", "content": llm.MAP_SISTEM}, {"role": "user", "content": "x"}], 100)
    assert "modelini tanımıyor" in str(h.value) and h.value.durum == 404
    assert "modelini tanımıyor" in repr(h.value) and llm.hata_metni(h.value) == str(h.value)
    k = gunluk[-1]
    assert k["http"] == 404 and k["tur"] == "NotFoundError" and k["model"] == "eski" and k["gorev"] == "bolum"
    assert llm.SON_HATA == str(h.value)


# ---------------------------------------------------------------- 400 ayrimi: sema / baglam

def test_baglam_400_semasiz_tekrar_edilmez(monkeypatch):
    llm.ayarla({"route": "https://llm.ornek/v1", "model": "m", "context": 16384})
    istekler = []

    def create(**kw):
        istekler.append(kw)
        raise _durum_hatasi(openai.BadRequestError, 400, {"message": "This model's maximum context length is "
                                                                     "16384 tokens. However, you requested 20000"})
    monkeypatch.setattr(llm.client.chat.completions, "create", create)
    with pytest.raises(llm.LlmHatasi, match="bağlam penceresini aşıyor"):
        llm.sor([{"role": "user", "content": "x"}], 3000, sema={"type": "object"})
    assert len(istekler) == 1 and "response_format" in istekler[0]


def test_baglam_400_pencereyi_ogrenip_bir_kez_tekrar_dener(monkeypatch):
    llm.ayarla({"route": "https://llm.ornek/v1", "model": "m", "context": 16384})
    istekler = []

    def create(**kw):
        istekler.append(kw["max_tokens"])
        if len(istekler) == 1:
            raise _durum_hatasi(openai.BadRequestError, 400, {"message": "This model's maximum context length is "
                                                                         "4096 tokens. However, you requested 5000"})
        return _cevap("tamam")
    monkeypatch.setattr(llm.client.chat.completions, "create", create)
    mesajlar = [{"role": "user", "content": "k" * 3000}]                       # ~1000 + 60 token
    assert llm.sor(mesajlar, 3000, dusunme=False) == ("tamam", "stop")
    assert llm.BAGLAM_PENCERESI == 4096 and istekler == [3000, 4096 - 1060 - llm.MARJ]


def test_sema_400_semasiz_devam_ve_ogrenilir(monkeypatch):
    llm.ayarla({"route": "https://llm.ornek/v1", "model": "m"})
    istekler, gunluk, uyarilar = [], [], []
    monkeypatch.setattr(llm, "GUNLUK_FN", gunluk.append)
    monkeypatch.setattr(llm, "UYARI_FN", uyarilar.append)

    def create(**kw):
        istekler.append("response_format" in kw)
        if "response_format" in kw:
            raise _durum_hatasi(openai.BadRequestError, 400, {"message": "guided_json/json_schema is not supported"})
        return _cevap('{"a": 1}')
    monkeypatch.setattr(llm.client.chat.completions, "create", create)
    assert llm.sor([{"role": "user", "content": "x"}], 100, sema={"type": "object"})[0] == '{"a": 1}'
    assert gunluk[-1]["sema_dustu"] is True and len(uyarilar) == 1
    llm.sor([{"role": "user", "content": "y"}], 100, sema={"type": "object"})
    assert istekler == [True, False, False]                              # ikinci cagri dogrudan semasiz


# ---------------------------------------------------------------- akisli cevap

def _parca(icerik=None, dusunce=None, neden=None, usage=None):
    secim = [types.SimpleNamespace(delta=types.SimpleNamespace(content=icerik, reasoning_content=dusunce),
                                   finish_reason=neden)] if usage is None else []
    return types.SimpleNamespace(choices=secim, usage=usage)


def test_akisli_cevap_birlestirilir(monkeypatch):
    llm.ayarla({"route": "https://llm.ornek/v1", "model": "m"})
    gorulen, gunluk = {}, []
    monkeypatch.setattr(llm, "GUNLUK_FN", gunluk.append)

    def create(**kw):
        gorulen.update(kw)
        return iter([_parca(dusunce="Düşünüyorum. "), _parca(icerik='{"ozet": '), _parca(icerik='"Türkçe"}'),
                     _parca(neden="stop"), _parca(usage=types.SimpleNamespace(prompt_tokens=77, completion_tokens=9))])
    monkeypatch.setattr(llm.client.chat.completions, "create", create)
    assert llm.sor([{"role": "user", "content": "x"}], 200) == ('{"ozet": "Türkçe"}', "stop")
    assert gorulen["stream"] is True and gorulen["extra_body"]["stream_options"] == {"include_usage": True}
    k = gunluk[-1]
    assert k["sunucu_giris_token"] == 77 and k["cikti_token"] == 9 and k["dusunce_karakter"] == len("Düşünüyorum. ")


def test_dusunce_etiketleri_temizlenir_ve_kaydedilir(monkeypatch):
    assert llm.dusunce_temizle("<reasoning>Let me think.</reasoning>Cevap") == "Cevap"
    assert llm.dusunce_temizle("<thinking>x</thinking>{\"a\": 1}") == '{"a": 1}'
    llm.ayarla({"route": "https://llm.ornek/v1", "model": "m", "llm_akis": False})
    gunluk = []
    monkeypatch.setattr(llm, "GUNLUK_FN", gunluk.append)
    monkeypatch.setattr(llm.client.chat.completions, "create", lambda **kw: _cevap("<think>uzun</think>Türkçe cevap"))
    assert llm.sor([{"role": "user", "content": "x"}], 100)[0] == "Türkçe cevap"
    assert gunluk[-1]["dusunce_icerikte"] is True


# ---------------------------------------------------------------- butceler

@pytest.mark.parametrize("pencere, bolum, liste, genel, soru, pay, parca", [
    (8192, 3000, 2500, 800, 1500, 0, 2500),
    (16384, 3000, 6000, 1500, 1500, 1500, 3000),
    (32768, 4000, 8000, 2000, 2500, 1500, 4000),
    (131072, 4000, 8000, 2000, 2500, 1500, 4000),
])
def test_butce_pencereye_olcekli(monkeypatch, pencere, bolum, liste, genel, soru, pay, parca):
    monkeypatch.setattr(llm, "BAGLAM_PENCERESI", pencere)
    assert (llm.butce("bolum"), llm.butce("liste"), llm.butce("genel"), llm.butce("soru"),
            llm.butce("soru_dusunme"), llm.butce("parca")) == (bolum, liste, genel, soru, pay, parca)
    assert llm.butce("duzeltme") == 1200 and motor_mod.parca_token() == parca and motor_mod.paralel() == 2


def test_dar_pencerede_soru_dusunmesiz_ve_buyukte_ust_sinirli(monkeypatch):
    cagrilar = []

    def sahte(mesajlar, max_tokens, sema=None, effort="medium", temperature=0.6, dusunme=True):
        cagrilar.append((max_tokens, dusunme, len(mesajlar[1]["content"])))
        return "Elif ⏱10:00:00", "stop"
    monkeypatch.setattr(llm, "sor", sahte)
    bolumler = [(f"{i}", f"[10:00:00] Elif: Bütçe {i}. " * 2000) for i in range(30)]   # ~16k token/bolum
    monkeypatch.setattr(llm, "BAGLAM_PENCERESI", 8192)
    llm.toplantiya_sor("Bütçe?", bolumler, "Toplantı: x")
    assert cagrilar[-1][:2] == (1500, False) and len(cagrilar) == 1
    cagrilar.clear()
    monkeypatch.setattr(llm, "BAGLAM_PENCERESI", 262144)
    llm.toplantiya_sor("Bütçe?", bolumler, "Toplantı: x")
    assert cagrilar[0][:2] == (4000, True)
    assert cagrilar[0][2] < llm.SORU_GIRDI_UST * 3.2                   # 256k pencerede bile girdi sinirli


def test_genel_ozet_ara_ozet_hatasi_notu_durdurmaz(monkeypatch):
    monkeypatch.setattr(llm, "BAGLAM_PENCERESI", 3000)
    cagri = {"n": 0}

    def sahte_genel(ozetler, liste_metni, baglam, cikti=None):
        cagri["n"] += 1
        if liste_metni == "-":
            raise llm.LlmHatasi("sunucu yok")
        return {"ozet": "Toplantıda bütçe ve takvim konuşuldu, ekip kararları gözden geçirdi.", "sonraki_adim": "-"}
    monkeypatch.setattr(llm, "_genel_sor", sahte_genel)
    bolumler = [{"ozet": f"Bölüm {i} bütçe konuşuldu. " * 40} for i in range(6)]
    ilerleme = []
    v = llm.genel_ozet(bolumler, {"kararlar": [], "aksiyonlar": []}, "Toplantı: x", ilerleme.append)
    assert v["ozet"].startswith("Toplantıda") and any("ara özet üretilemedi" in m and "sunucu yok" in m
                                                       for m in ilerleme)


# ---------------------------------------------------------------- llm_teshis (agsiz kisimlar)

def test_teshis_sentetik_transkript_gercek_icerik_tasimaz():
    bolumler = llm_teshis.sentetik_transkript()
    assert len(bolumler) == 3 and all(len(s) >= 10 for _, s in bolumler)
    kisiler = {kim for _, s in bolumler for _, kim, _ in s}
    assert kisiler <= set(llm_teshis.KATILIMCILAR)
    zamanlar = [ts for _, s in bolumler for ts, _, _ in s]
    assert zamanlar == sorted(zamanlar) and bolumler[0][0].startswith("10:00:00")
    assert 2900 <= len(llm_teshis.uzun_metin(1000)) <= 3000


def test_teshis_rapor_maskeler_ve_ozetler(tmp_path):
    R = llm_teshis.Rapor()
    R.gizle("https://qwen-dev.apps.kume.kurum.com.tr")
    R.yaz("GET https://qwen-dev.apps.kume.kurum.com.tr/v1/models", "sertifika *.kume.kurum.com.tr icin degil")
    assert R.satirlar[0] == "GET <LLM_ADRES>/v1/models" and "kurum" not in R.satirlar[1]
    R.bulgu("DUSUK", "kucuk")
    R.bulgu("YUKSEK", "model uyusmuyor", "config.json model")
    R.config_oner("context", 0, "sunucu 32768")
    R.ozet()
    ozet = R.satirlar[R.satirlar.index("TEŞHİS ÖZETİ (öncelik sırasıyla)"):]
    assert ozet.index("- [YUKSEK] model uyusmuyor") < ozet.index("- [DUSUK] kucuk")
    assert any('"context": 0' in s for s in ozet)
    R.kaydet(str(tmp_path / "c.txt"))
    assert "<LLM_ADRES>" in (tmp_path / "c.txt").read_text(encoding="utf-8")


def test_teshis_etiket_ve_sema_denetimi():
    assert llm_teshis.etiketler("<think>a</think>b") == ["</think>", "<think>"]
    assert "(İngilizce düşünce önekli düz metin)" in llm_teshis.etiketler("Okay, the user wants")
    assert llm_teshis.sema_uyuyor('{"a": 1, "b": []}', ["a", "b"]) == (True, "doğrudan JSON")
    assert llm_teshis.sema_uyuyor('Tabii: {"a": 1}', ["a", "b"])[0] is False
    assert llm_teshis.sema_uyuyor("düz metin", ["a"]) == (False, "JSON değil")


def _sahte_boru_hatti_sor(mesajlar, max_tokens, sema=None, effort="medium", temperature=0.6, dusunme=True):
    gorev = llm._gorev_adi(mesajlar)
    if gorev == "duzeltme":
        return json.dumps({"duzeltmeler": [{"orijinal": "jandarma kaydı", "duzeltilmis": "Jira kaydı"}]}), "stop"
    if gorev in ("bolum", "liste"):
        return json.dumps({"ozet": "Ekip rapor ekranındaki gecikmeyi ve kampanya takvimini konuştu.",
                           "konular": ["Rapor gecikmesi"], "kararlar": ["Kampanya bir hafta ertelendi"],
                           "aksiyonlar": [{"madde": "Pazarlama ekibine yeni tarihi iletmek", "sorumlu": "Elif Kaya",
                                           "tarih": "-"}],
                           "acik_sorular": [], "belirsiz_terimler": []}, ensure_ascii=False), "stop"
    if gorev == "genel":
        return json.dumps({"ozet": "Toplantıda rapor ekranındaki gecikme, kampanya takvimi ve oryantasyon "
                                   "konuşuldu; kampanyanın bir hafta ertelenmesine karar verildi.",
                           "sonraki_adim": "Elif Kaya yeni tarihi iletecek."}, ensure_ascii=False), "stop"
    return "Pazarlama ekibine yeni tarihi Elif Kaya iletecek ⏱10:05:40", "stop"


def test_teshis_boru_hatti_sahte_llm_ile(monkeypatch):
    monkeypatch.setattr(llm, "sor", _sahte_boru_hatti_sor)
    R = llm_teshis.Rapor(yaz_fn=lambda s: None)
    not_md = llm_teshis.boru_hatti(R, llm, [])
    assert "## Kararlar" in not_md and "Kampanya bir hafta ertelendi" in not_md
    assert "Elif Kaya" in not_md
    assert not [b for b in R.bulgular if b[0] == "YUKSEK"]


def test_teshis_boru_hatti_hatalari_bulgu_olur(monkeypatch):
    def bozuk(*a, **kw):
        raise llm.LlmHatasi("LLM sunucusu 'x' modelini tanımıyor")
    monkeypatch.setattr(llm, "sor", bozuk)
    R = llm_teshis.Rapor(yaz_fn=lambda s: None)
    llm_teshis.boru_hatti(R, llm, [])
    yuksek = [b[1] for b in R.bulgular if b[0] == "YUKSEK"]
    assert any("modelini tanımıyor" in b for b in yuksek) and len(yuksek) >= 4


def test_teshis_llm_log_ozeti(tmp_path):
    k = tmp_path / "toplantilar" / "2026-10-08_deneme"
    k.mkdir(parents=True)
    satirlar = [{"olay": "ayar", "model": "m", "pencere": 32768, "uyari": []},
                {"gorev": "bolum", "finish": "stop", "sure_sn": 4.0},
                {"gorev": "bolum", "finish": "length", "sure_sn": 6.0},
                {"gorev": "duzeltme", "sure_sn": 0.1, "hata": "LLM sunucusu 'x' modelini tanımıyor (HTTP 404)"}]
    (k / "llm_log.jsonl").write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in satirlar) + "\nbozuk\n",
                                     encoding="utf-8")
    R = llm_teshis.Rapor(yaz_fn=lambda s: None)
    ozet = llm_teshis.llm_log_ozeti(R, str(tmp_path / "toplantilar"))
    g = ozet["2026-10-08_deneme"]
    assert g["gorevler"]["bolum"]["n"] == 2 and g["gorevler"]["bolum"]["finish"] == {"stop": 1, "length": 1}
    assert g["gorevler"]["duzeltme"]["hata"] == 1 and "modelini tanımıyor" in g["hatalar"][0]
