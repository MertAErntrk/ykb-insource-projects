"""
veri.py — başvuru (strateji girdi/çıktı) JSON'una erişim. Prototipte bir klasördeki dosyalar
okunur; hedefte aynı arayüzü sağlayan veri tabanı/API uyarlayıcısı yazılır
(docs/UYGULAMA_PLANI.md bölüm 5). Arayüz: bul(cif, tarih=None) -> dict | None.
tarih verilirse o tarihe en yakın önceki başvuru, yoksa en yeni başvuru döner.
"""
import datetime as dt
import glob
import json
import os
import re
from typing import List, Optional

from yardimci import tarih_ayristir, yol_al


class DosyaKaynagi:
    etiket = "dosya"

    def __init__(self, klasor: str, cif_yolu: str = "CIF", tarih_yolu: str = "BasvuruTarihi"):
        self.klasor = klasor
        self.cif_yolu = cif_yolu
        self.tarih_yolu = tarih_yolu
        self._kayitlar: Optional[List[dict]] = None

    def _yukle(self) -> List[dict]:
        if self._kayitlar is None:
            self._kayitlar = []
            for yol in sorted(glob.glob(os.path.join(self.klasor, "*.json"))):
                try:
                    with open(yol, encoding="utf-8") as f:
                        veri = json.load(f)
                except (OSError, ValueError):
                    continue
                if isinstance(veri, dict) and yol_al(veri, self.cif_yolu) is not None:
                    self._kayitlar.append(veri)
        return self._kayitlar

    def bul(self, cif: str, tarih: Optional[dt.date] = None) -> Optional[dict]:
        adaylar = [b for b in self._yukle() if str(yol_al(b, self.cif_yolu)) == str(cif)]
        if not adaylar:
            return None
        adaylar.sort(key=lambda b: tarih_ayristir(yol_al(b, self.tarih_yolu)) or dt.date.min)
        if tarih is not None:
            onceki = [b for b in adaylar if (tarih_ayristir(yol_al(b, self.tarih_yolu)) or dt.date.min) <= tarih]
            if onceki:
                return onceki[-1]
        return adaylar[-1]


_AD_DESENI = re.compile(r"^[A-Za-z_][A-Za-z0-9_$.]*$")


def _surucu():
    """Oracle sürücüsü (oracledb, yoksa cx_Oracle); ikisi de yoksa None."""
    try:
        import oracledb
        return oracledb
    except ImportError:
        try:
            import cx_Oracle
            return cx_Oracle
        except ImportError:
            return None


def _oku(deger):
    """CLOB benzeri nesneyi (read niteliği) metne çevirir."""
    return deger.read() if hasattr(deger, "read") else deger


def _json(deger):
    deger = _oku(deger)
    return json.loads(deger) if isinstance(deger, (str, bytes, bytearray)) else deger


def _gun(deger) -> Optional[str]:
    if deger is None:
        return None
    if hasattr(deger, "isoformat"):
        return deger.isoformat()[:10]
    return str(deger)[:10]


class DbKaynagi:
    """Oracle uyarlayıcısı; DosyaKaynagi ile aynı arayüz. cfg = ayar["db"]."""
    etiket = "db"

    def __init__(self, cfg: dict, baglanti=None):
        self.cfg = cfg
        self._baglantilar = {}
        self._enjekte = baglanti

    def _baglan(self, kanal: str):
        if self._enjekte is not None:
            return self._enjekte
        if kanal not in self._baglantilar:
            surucu = _surucu()
            if surucu is None:
                raise RuntimeError("Oracle sürücüsü (oracledb/cx_Oracle) yok")
            k = self.cfg.get(kanal) or {}
            self._baglantilar[kanal] = surucu.connect(
                user=k.get("kullanici"), password=k.get("parola"), dsn=k.get("dsn"))
        return self._baglantilar[kanal]

    def _ad(self, grup: str, anahtar: str) -> str:
        ad = (self.cfg.get(grup) or {}).get(anahtar) or ""
        yer = "config.json db.%s.%s" % (grup, anahtar)
        if not ad:
            raise ValueError("%s boş" % yer)
        if not _AD_DESENI.match(ad):
            raise ValueError("%s geçersiz ad" % yer)
        return ad

    def _sorgu(self, kanal: str, tarih_var: bool):
        s = lambda a: self._ad("sutunlar", a)
        t = lambda a: self._ad("tablolar", a)
        tarih_s = (self.cfg.get("sutunlar") or {}).get("tarih") or ""
        if tarih_var or tarih_s:
            tarih_s = s("tarih")
        secim = "%s, %s, %s, %s" % (s("indata"), s("outdata"), s("action_code"), tarih_s or "NULL")
        if kanal == "smile_plus":
            tablo = t("pc_log")
            kosul = "%s IN (SELECT GROUP_PROPOSAL_ID FROM %s WHERE %s = :cif)" % (
                s("basvuru_no"), t("proposals"), s("cif"))
        else:
            tablo = t("io_log")
            kaynak = (self.cfg.get("tablolar") or {}).get("identity") or "" or \
                (self.cfg.get("tablolar") or {}).get("evaluation") or ""
            if kaynak:
                kaynak = t("identity") if (self.cfg["tablolar"].get("identity")) else t("evaluation")
                kosul = "%s IN (SELECT %s FROM %s WHERE %s = :cif)" % (
                    s("basvuru_no"), s("basvuru_no"), kaynak, s("cif"))
            else:
                kosul = "%s = :cif" % s("cif")
        sql = "SELECT %s FROM %s WHERE %s" % (secim, tablo, kosul)
        if tarih_var:
            sql += " AND TRUNC(%s) <= :tarih" % tarih_s
        if tarih_s:
            sql += " ORDER BY %s DESC" % tarih_s
        return sql + " FETCH FIRST 1 ROWS ONLY"

    def bul(self, cif: str, tarih: Optional[dt.date] = None, kanal: str = "hizli_tahsis") -> Optional[dict]:
        sql = self._sorgu(kanal, tarih is not None)
        params = {"cif": cif}
        if tarih is not None:
            params["tarih"] = tarih
        imlec = self._baglan(kanal).cursor()
        imlec.execute(sql, params)
        satir = imlec.fetchone()
        if not satir:
            return None
        indata, outdata, aksiyon, basvuru_tarihi = satir[:4]
        return {"CIF": cif, "BasvuruTarihi": _gun(basvuru_tarihi),
                "Indata": _json(indata), "Outdata": _json(outdata), "ActionCode": aksiyon}


def kaynak_kur(ayar: dict):
    """ayar'a göre DbKaynagi ya da DosyaKaynagi; dosyaya düşme nedeni etikette."""
    nedeni = None
    if ayar.get("kaynak") == "db":
        db = ayar.get("db") or {}
        if not ((db.get("hizli_tahsis") or {}).get("dsn")):
            nedeni = "dsn boş"
        elif _surucu() is None:
            nedeni = "sürücü yok"
        else:
            k = DbKaynagi(db)
            try:
                k._sorgu("hizli_tahsis", True)  # tablo/sütun adı boş ya da geçersizse açılışta dosyaya düş
                return k
            except ValueError as e:
                nedeni = "ayar: %s" % e
    k = DosyaKaynagi(ayar["basvurular"], ayar["cif_yolu"], ayar["tarih_yolu"])
    k.etiket = "dosya" if nedeni is None else "dosya (db: %s)" % nedeni
    return k
