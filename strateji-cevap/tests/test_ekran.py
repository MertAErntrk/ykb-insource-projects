import http.client
import json
import os
import threading

import pytest

import ekran
import kayitlar
import motor

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def ortam(tmp_path_factory):
    m = motor.Motor.kur(motor.ayar_oku(os.path.join(KOK, "config.example.json")))
    onay = str(tmp_path_factory.mktemp("onay") / "alt" / "onaylar.json")
    uyg = ekran.Uygulama(m, kayitlar.yukle(), onay)
    sunucu = ekran.sunucu_kur(uyg, 0)
    t = threading.Thread(target=sunucu.serve_forever, daemon=True)
    t.start()
    yield uyg, sunucu.server_address[1], onay, m
    sunucu.shutdown()
    sunucu.server_close()


def istek(port, yontem, yol, govde=None, ham=None, basliklar=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    veri = ham if ham is not None else (json.dumps(govde).encode("utf-8") if govde is not None else None)
    b = {"Content-Type": "application/json"} if yontem == "POST" else {}
    b.update(basliklar or {})
    c.request(yontem, yol, body=veri, headers=b)
    r = c.getresponse()
    icerik = r.read()
    c.close()
    return r.status, json.loads(icerik.decode("utf-8")) if icerik else None


def test_liste(ortam):
    _, port, _, _ = ortam
    kod, liste = istek(port, "GET", "/api/kayitlar")
    assert kod == 200 and len(liste) == 15
    assert all(s["durum"] for s in liste)
    durumlar = [s["durum"] for s in liste]
    assert "otomatik" in durumlar and "onaya_gitti" in durumlar
    assert any(s["kayit_no"] == "I1000001" and s["durum"] == "otomatik" for s in liste)
    assert durumlar.index("onaya_gitti") < durumlar.index("otomatik")


def test_detay(ortam):
    _, port, _, _ = ortam
    kod, d = istek(port, "GET", "/api/kayit/I1000001")
    assert kod == 200
    assert d["taslak"]["metin"] and "olgular" in d["taslak"]
    assert d["kaynak"] == "dosya" and d["onay"] is None
    assert "_beklenen" not in d["kayit"]


def test_hatalar(ortam):
    _, port, _, _ = ortam
    assert istek(port, "GET", "/api/kayit/I9999999999")[0] == 404
    assert istek(port, "GET", "/../x")[0] == 404
    assert istek(port, "GET", "/api/kayit/..%2Fx")[0] == 404
    assert istek(port, "POST", "/api/kayit/I1000001/duzelt", {"metin": "  "})[0] == 400
    assert istek(port, "POST", "/api/kayit/I1000001/duzelt", ham=b"{bozuk")[0] == 400
    assert istek(port, "POST", "/api/kayit/I1000001/duzelt", {"not": "x"})[0] == 400
    assert istek(port, "POST", "/api/kayit/I1/onayla", {})[0] == 404
    assert istek(port, "POST", "/api/kayit/I1000001/onayla", ham=b"x" * 70000)[0] == 413


def test_istek_kontrolleri(ortam):
    _, port, _, _ = ortam
    assert istek(port, "GET", "/api/kayitlar", basliklar={"Host": "evil.example"})[0] == 403
    assert istek(port, "GET", "/api/kayitlar", basliklar={"Host": "localhost:%d" % port})[0] == 200
    assert istek(port, "POST", "/api/kayit/I1000002/onayla", {}, basliklar={"Content-Type": "text/plain"})[0] == 415
    assert istek(port, "POST", "/api/kayit/I1000002/onayla", basliklar={"Content-Length": "-1"})[0] == 400
    assert istek(port, "GET", "/api/kayit/I1000002")[1]["onay"] is None


def test_onay_yazilamazsa_500(ortam, tmp_path):
    _, _, _, m = ortam
    engel = tmp_path / "engel"
    engel.write_text("x")                                   # klasör yerine dosya: makedirs düşer
    uyg = ekran.Uygulama(m, kayitlar.yukle(), str(engel / "onaylar.json"))
    with pytest.raises(ekran.Hata) as h:
        uyg.kapsam_disi("I1000002", "n")
    assert h.value.kod == 500 and uyg.onaylar == {} and uyg.durum("I1000002") != "kapsam_disi"


def test_onay_kalici(ortam):
    uyg, port, onay, m = ortam
    kod, s = istek(port, "POST", "/api/kayit/I1000001/onayla", {})
    assert kod == 200 and s["durum"] == "gonderildi"
    assert s["onay"]["metin"] == uyg.kayitlar["I1000001"]["taslak"]["metin"]
    with open(onay, encoding="utf-8") as f:
        assert json.load(f)["I1000001"]["durum"] == "gonderildi"
    assert istek(port, "POST", "/api/kayit/I1000001/duzelt", {"metin": "yeni", "not": "n"})[1]["durum"] == "duzeltildi"
    assert istek(port, "POST", "/api/kayit/I1000001/kapsam_disi", {})[1]["durum"] == "kapsam_disi"
    istek(port, "POST", "/api/kayit/I1000001/onayla", {"metin": "elle"})
    yeni = ekran.Uygulama(m, kayitlar.yukle(), onay)
    assert yeni.durum("I1000001") == "gonderildi"
    assert yeni.detay("I1000001")["onay"]["metin"] == "elle"
