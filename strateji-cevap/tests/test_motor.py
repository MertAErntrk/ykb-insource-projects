import json
import os

import pytest

from motor import Motor, ayar_oku

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AYAR = ayar_oku(os.path.join(KOK, "config.example.json"))
AYAR["basvurular"] = os.path.join(KOK, "ornekler", "basvurular")


def cagri(ad):
    with open(os.path.join(KOK, "ornekler", "cagrilar", ad + ".json"), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def motor():
    return Motor.kur(AYAR)


def test_h221_otomatik(motor):
    t = motor.cevapla(cagri("h221"))
    assert t.durum == "otomatik" and t.sinif == "cikti" and t.kurallar == ["H221"] and t.cif == "10000001"
    assert t.metin.startswith('Merhaba, "10000001" numaralı müşterinin ticari KKB kayıtlarına göre ilk olarak Nisan 2025')
    assert t.metin.endswith("İyi çalışmalar.") and not t.notlar and not t.uyarilar


def test_yetki_taslak_sablon_incelemeye_gider(motor):
    t = motor.cevapla(cagri("yetki"))
    assert t.kurallar == ["YETKI"] and t.durum == "inceleme_gerekli"
    assert t.notlar == ["YETKI: 'h_kurali_belirleyici' şablonu henüz onaylı değil"]
    assert "H999 kuralı yetki seviyesini 8. seviyeye" in t.metin


def test_iki_kural_tek_mesaj(motor):
    t = motor.cevapla(cagri("iki_kural"))
    assert t.kurallar == ["H221", "YETKI"] and t.metin.count("Merhaba") == 1 and t.metin.count("İyi çalışmalar.") == 1


def test_hata_sinifi_cevap_uretmez(motor):
    t = motor.cevapla(cagri("hata"))
    assert t.sinif == "hata" and t.durum == "inceleme_gerekli" and t.metin == "" and t.cif == "10000002"


def test_calismamis_kural_incelemeye(motor):
    t = motor.cevapla(cagri("calismamis"))
    assert t.durum == "inceleme_gerekli"
    assert any("çalışmamış görünüyor" in n for n in t.notlar) and any("koşulu sağlayan kayıt yok" in n for n in t.notlar)


def test_cif_yok_ve_basvuru_yok(motor):
    t = motor.cevapla({"metin": "H221 neden çalıştı?"})
    assert t.durum == "inceleme_gerekli" and "müşteri numarası bulunamadı" in t.notlar
    t = motor.cevapla({"metin": "55555555 müşterisinde H221 neden çalıştı?"})
    assert any("başvuru JSON'u bulunamadı" in n for n in t.notlar)


def test_bilinmeyen_kod_ve_birden_cok_cif(motor):
    t = motor.cevapla({"metin": "10000001 ve 10000002 müşterilerinde H555 kuralı neden çalıştı?"})
    assert t.cif == "10000001" and any("katalogda" in n for n in t.notlar) and any("birden çok" in u for u in t.uyarilar)


def test_konu_tespit_edilemezse(motor):
    t = motor.cevapla({"metin": "10000001 müşterisinin bilançosu stratejiye eksik geliyor, veri güncel değil."})
    assert t.sinif == "girdi" and t.durum == "inceleme_gerekli" and any("faz 2" in n for n in t.notlar)


def test_cagri_cif_alani_oncelikli(motor):
    t = motor.cevapla({"cif": "10000002", "metin": "Bu müşteride H221 neden çalışmadı, bağımsız denetim raporu?"})
    assert t.cif == "10000002"


def test_kural_listesi_yoksa_uyari(motor, tmp_path):
    with open(os.path.join(KOK, "ornekler", "basvurular", "sentetik_10000001.json"), encoding="utf-8") as f:
        b = json.load(f)
    del b["Outdata"]["HRules"]
    with open(os.path.join(str(tmp_path), "b.json"), "w", encoding="utf-8") as f:
        json.dump(b, f)
    m = Motor.kur(dict(AYAR, basvurular=str(tmp_path)))
    t = m.cevapla(cagri("h221"))
    assert t.durum == "otomatik" and any("doğrulanamadı" in u for u in t.uyarilar)


def test_cli(capsys):
    from motor import main
    assert main(["--cagri", os.path.join(KOK, "ornekler", "cagrilar", "h221.json"),
                 "--config", os.path.join(KOK, "config.example.json"), "--basvurular", AYAR["basvurular"]]) == 0
    cikti = capsys.readouterr().out
    assert "Durum: otomatik" in cikti and "Nisan 2025" in cikti
    assert main(["--cagri", os.path.join(KOK, "ornekler", "cagrilar", "hata.json"), "--json",
                 "--basvurular", AYAR["basvurular"]]) == 0
    assert json.loads(capsys.readouterr().out)["durum"] == "inceleme_gerekli"
