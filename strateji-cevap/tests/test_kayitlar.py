import os
import re

import kayitlar
from kayitlar import KOK

CIF = re.compile(r"(?<![\d.])(\d{8})(?![\d.])")
# senaryo gereği başvuru dosyası bilerek yok: girdi/kapsam dışı soru, başvurusu olmayan müşteri
BASVURUSUZ = {"10000012", "10000099"}


def test_yukle_15_kayit():
    k = kayitlar.yukle()
    assert [r["Kayıt Numarası"] for r in k] == ["I10000%02d" % i for i in range(1, 16)]
    assert os.path.isfile(kayitlar.KAYITLAR_YOLU)


def test_cagri_yap_bicimi():
    r = kayitlar.yukle()[0]
    c = kayitlar.cagri_yap(r)
    assert c == {"id": r["Kayıt Numarası"], "tarih": r["Kaydın Havuza Giriş Tarihi"][:10],
                 "talep_eden": r["Kaydın İlk Grubu"], "metin": r["Kayıt Açıklaması"],
                 "kanal": r["Kanal"], "kategori": r["Kategori"]}
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", c["tarih"])


def test_beklenen_ve_basvuru_tutarliligi():
    for r in kayitlar.yukle():
        assert r["_beklenen"]["sinif"] and r["_beklenen"]["durum"] in ("otomatik", "inceleme_gerekli")
        assert r["Durum"] in ("Açık", "Kapalı") and r["Kanal"] in ("IVR", "Web", "Harmoni")
        for cif in CIF.findall(r["Kayıt Açıklaması"]):
            yol = os.path.join(KOK, "ornekler", "basvurular", "sentetik_%s.json" % cif)
            assert os.path.isfile(yol) != (cif in BASVURUSUZ), cif
