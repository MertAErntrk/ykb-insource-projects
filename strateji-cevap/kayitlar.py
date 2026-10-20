"""
kayitlar.py — sentetik Çözüm Merkezi kayıtlarını (smile2025 sütun düzeni) yükler ve motorun
çağrı biçimine çevirir: {id, tarih, talep_eden, metin, kanal, kategori}.
"""
import json
import os
from typing import List

KOK = os.path.dirname(os.path.abspath(__file__))
KAYITLAR_YOLU = os.path.join(KOK, "ornekler", "cozum_merkezi", "kayitlar.json")


def yukle(yol: str = KAYITLAR_YOLU) -> List[dict]:
    with open(yol, encoding="utf-8") as f:
        return json.load(f)


def cagri_yap(kayit: dict) -> dict:
    return {
        "id": kayit["Kayıt Numarası"],
        "tarih": str(kayit["Kaydın Havuza Giriş Tarihi"])[:10],
        "talep_eden": kayit["Kaydın İlk Grubu"],
        "metin": kayit["Kayıt Açıklaması"],
        "kanal": kayit["Kanal"],
        "kategori": kayit["Kategori"],
    }
