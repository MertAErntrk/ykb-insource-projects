"""
ekran.py — Çözüm Merkezi kayıt listesi + onay API'si (stdlib http.server, yalnız 127.0.0.1).
Kayıtlar açılışta motorla çözülür ve bellekte tutulur; onay kararları onaylar.json'a atomik yazılır.
Tarayıcı sayfası kök/ekran.html olduğu gibi servis edilir.
"""
import argparse
import json
import os
import re
import sys
from dataclasses import asdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import List, Optional

import kayitlar as kayitlar_mod
import motor as motor_mod

KOK = os.path.dirname(os.path.abspath(__file__))
HTML_YOLU = os.path.join(KOK, "ekran.html")
GOVDE_SINIRI = 65536
NO_RE = re.compile(r"^I\d{1,10}$")
_SIRA = {"onaya_gitti": 0, "otomatik": 1}


class Hata(Exception):
    def __init__(self, kod: int, mesaj: str):
        super().__init__(mesaj)
        self.kod = kod
        self.mesaj = mesaj


class Uygulama:
    def __init__(self, motor, kayitlar: List[dict], onay_yolu: str):
        self.motor = motor
        self.onay_yolu = onay_yolu
        self.kaynak = getattr(motor.kaynak, "etiket", "?")
        self.kayitlar = {}
        for kayit in kayitlar:
            cagri = kayitlar_mod.cagri_yap(kayit)
            taslak = asdict(motor.cevapla(cagri))
            self.kayitlar[str(kayit["Kayıt Numarası"])] = {"kayit": kayit, "cagri": cagri, "taslak": taslak}
        self.onaylar = {}
        if os.path.exists(onay_yolu):
            with open(onay_yolu, encoding="utf-8") as f:
                self.onaylar = json.load(f)

    def _kayit(self, no: str) -> dict:
        if not NO_RE.match(no or "") or no not in self.kayitlar:
            raise Hata(404, "kayıt bulunamadı")
        return self.kayitlar[no]

    def _motor_durumu(self, k: dict) -> str:
        return "otomatik" if k["taslak"]["durum"] == "otomatik" else "onaya_gitti"

    def durum(self, no: str) -> str:
        k = self._kayit(no)
        if no in self.onaylar and self.onaylar[no].get("durum"):
            return self.onaylar[no]["durum"]
        return self._motor_durumu(k)

    def liste(self) -> List[dict]:
        satirlar = []
        for no, k in self.kayitlar.items():
            kayit = k["kayit"]
            satirlar.append({
                "kayit_no": no,
                "tarih": str(kayit["Kaydın Havuza Giriş Tarihi"]),
                "kanal": kayit["Kanal"],
                "kategori": kayit["Kategori"],
                "sinif": k["taslak"]["sinif"],
                "kurallar": k["taslak"]["kurallar"],
                "durum": self.durum(no),
            })
        satirlar.sort(key=lambda s: (_SIRA.get(s["durum"], 2), s["tarih"]))
        return satirlar

    def detay(self, no: str) -> dict:
        k = self._kayit(no)
        return {
            "kayit": {a: v for a, v in k["kayit"].items() if a != "_beklenen"},
            "cagri": k["cagri"],
            "taslak": k["taslak"],
            "durum": self.durum(no),
            "onay": self.onaylar.get(no),
            "kaynak": self.kaynak,
        }

    def _kaydet(self, no: str, durum: str, metin: str, not_: str) -> dict:
        k = self._kayit(no)
        onaylar = dict(self.onaylar)
        onaylar[no] = {
            "durum": durum, "metin": metin, "not": not_,
            "zaman": datetime.now().isoformat(timespec="seconds"),
            "orijinal_durum": self._motor_durumu(k),
        }
        gecici = self.onay_yolu + ".tmp"
        try:  # önce disk, başarılıysa bellek: yazma düşerse ikisi ayrışmaz
            os.makedirs(os.path.dirname(os.path.abspath(self.onay_yolu)), exist_ok=True)
            with open(gecici, "w", encoding="utf-8") as f:
                json.dump(onaylar, f, ensure_ascii=False, indent=2)
            os.replace(gecici, self.onay_yolu)
        except OSError as e:
            raise Hata(500, "onay dosyası yazılamadı: {}".format(e.__class__.__name__))
        self.onaylar = onaylar
        return {"kayit_no": no, "durum": durum, "onay": onaylar[no]}

    def onayla(self, no: str, metin: Optional[str] = None) -> dict:
        k = self._kayit(no)
        if metin is None:
            metin = k["taslak"]["metin"]
        return self._kaydet(no, "gonderildi", metin, "")

    def duzelt(self, no: str, metin: str, not_: str = "") -> dict:
        self._kayit(no)
        if not isinstance(metin, str) or not metin.strip():
            raise Hata(400, "metin boş olamaz")
        return self._kaydet(no, "duzeltildi", metin, not_)

    def kapsam_disi(self, no: str, not_: str = "") -> dict:
        self._kayit(no)
        return self._kaydet(no, "kapsam_disi", "", not_)


def isleyici_yap(uyg: Uygulama):
    class Isleyici(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _gonder(self, kod: int, govde: bytes, tur: str):
            self.send_response(kod)
            self.send_header("Content-Type", tur)
            self.send_header("Content-Length", str(len(govde)))
            self.end_headers()
            self.wfile.write(govde)
            sys.stderr.write("{} {} {}\n".format(self.command, self.path.split("?")[0], kod))

        def _json(self, kod: int, nesne):
            self._gonder(kod, json.dumps(nesne, ensure_ascii=False).encode("utf-8"),
                         "application/json; charset=utf-8")

        def _yol(self) -> str:
            return self.path.split("?", 1)[0]

        def _host_dogrula(self):
            """Host başlığı yerel adres değilse 403 (DNS rebinding ile veri okunmasın)."""
            host = (self.headers.get("Host") or "").strip().lower()
            port = self.server.server_address[1]
            if host not in ("127.0.0.1:%d" % port, "localhost:%d" % port, "[::1]:%d" % port):
                raise Hata(403, "Host başlığı yerel değil")

        def do_GET(self):
            try:
                self._host_dogrula()
                yol = self._yol()
                if yol == "/":
                    if not os.path.isfile(HTML_YOLU):
                        raise Hata(404, "ekran.html yok")
                    with open(HTML_YOLU, "rb") as f:
                        self._gonder(200, f.read(), "text/html; charset=utf-8")
                elif yol == "/api/kayitlar":
                    self._json(200, uyg.liste())
                else:
                    m = re.match(r"^/api/kayit/([^/]+)$", yol)
                    if not m:
                        raise Hata(404, "yol bulunamadı")
                    self._json(200, uyg.detay(m.group(1)))
            except Hata as e:
                self._json(e.kod, {"hata": e.mesaj})

        def do_POST(self):
            try:
                self._host_dogrula()
                m = re.match(r"^/api/kayit/([^/]+)/(onayla|duzelt|kapsam_disi)$", self._yol())
                if not m:
                    raise Hata(404, "yol bulunamadı")
                no, islem = m.groups()
                uyg._kayit(no)
                # Yalnız JSON gövde: tarayıcıdaki başka bir site form ya da text/plain ile POST atamasın (CSRF).
                tur = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                if tur != "application/json":
                    raise Hata(415, "Content-Type application/json olmalı")
                try:
                    uzunluk = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    raise Hata(400, "geçersiz Content-Length")
                if uzunluk < 0:  # read(-1) EOF'a kadar okur ve tek iş parçacıklı sunucuyu kilitler
                    raise Hata(400, "geçersiz Content-Length")
                if uzunluk > GOVDE_SINIRI:
                    raise Hata(413, "gövde çok büyük")
                ham = self.rfile.read(uzunluk) if uzunluk else b""
                try:
                    veri = json.loads(ham.decode("utf-8")) if ham.strip() else {}
                except ValueError:
                    raise Hata(400, "geçersiz JSON")
                if not isinstance(veri, dict):
                    raise Hata(400, "gövde nesne olmalı")
                metin, not_ = veri.get("metin"), veri.get("not", "")
                if metin is not None and not isinstance(metin, str):
                    raise Hata(400, "metin metin olmalı")
                if not isinstance(not_, str):
                    raise Hata(400, "not metin olmalı")
                if islem == "onayla":
                    sonuc = uyg.onayla(no, metin)
                elif islem == "duzelt":
                    if metin is None:
                        raise Hata(400, "metin zorunlu")
                    sonuc = uyg.duzelt(no, metin, not_)
                else:
                    sonuc = uyg.kapsam_disi(no, not_)
                self._json(200, sonuc)
            except Hata as e:
                self._json(e.kod, {"hata": e.mesaj})

    return Isleyici


def sunucu_kur(uyg: Uygulama, port: int) -> HTTPServer:
    return HTTPServer(("127.0.0.1", port), isleyici_yap(uyg))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Çözüm Merkezi onay ekranı (yerel)")
    p.add_argument("--config")
    p.add_argument("--port", type=int)
    p.add_argument("--kayitlar")
    p.add_argument("--onaylar")
    a = p.parse_args(argv)
    ayar = motor_mod.ayar_oku(a.config)
    port = a.port if a.port is not None else int(ayar["ekran_port"])
    m = motor_mod.Motor.kur(ayar)
    kayitlar = kayitlar_mod.yukle(a.kayitlar or ayar["kayitlar_dosya"])
    uyg = Uygulama(m, kayitlar, a.onaylar or ayar["onaylar_dosya"])
    sirali = uyg.liste()
    onaya = sum(1 for s in sirali if s["durum"] == "onaya_gitti")
    oto = sum(1 for s in sirali if s["durum"] == "otomatik")
    sunucu = sunucu_kur(uyg, port)
    print("{} kayıt: {} onaya gitti, {} otomatik — http://127.0.0.1:{} (kaynak: {})".format(
        len(sirali), onaya, oto, sunucu.server_address[1], uyg.kaynak), flush=True)
    try:
        sunucu.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        sunucu.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
