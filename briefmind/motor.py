"""
motor.py — toplanti notu boru hatti (UI'dan bagimsiz).

Akis:  satir_ekle() -> sozluk (birebir+bulanik) -> altyazi.jsonl -> parca
       parca kapaninca arka planda: [duzeltme gecisi] -> ozet -> oneri kuyrugu
       bitir()             -> son parcayi kapatir, ozetleri bekler, bekleyen onerileri verir
       kararlari_uygula()  -> onaylananlar sozluge + tum parcalara uygulanir, degisen parcalar
                              yeniden ozetlenir (sadece onlar)
       notu_uret()         -> birlestirme -> sorumlu dogrulama -> dayanak kontrolu -> not.md
       yeniden_ozetle()    -> (Gecmis) sozluk tum parcalara, TUM parcalar yeniden duzeltme + ozet

Disk (toplantilar/<tarih>_<slug>/): meta.json, altyazi.jsonl, parcalar/parca_NNN.json, oneriler.json, not.md
olay(tip, veri) geri cagrisi: "satir", "parca_kapandi", "parca_ozetlendi", "oneri", "log",
       "adim" ({"metin", "no", "toplam"}: not uretiminin/yeniden ozetlemenin o anki asamasi; arayuz seridi)
"""
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import threading
import time

import llm
import tarih as tarih_mod
from metin import ad_geciyor, metin_benzerligi, tr_kucuk
from sozluk import Sozluk, normalize

KOK = "toplantilar"
PARCA_TOKEN = 0                 # 0: LLM baglam penceresine gore (llm.butce("parca"): 8k 2500, 16k 3000, 32k+ 4000)
PARCA_SURE_SN = 12 * 60
SESSIZLIK_SN = 180
SESSIZLIK_MIN_TOKEN = 500
DUZELT_MIN_TOKEN = 800          # bundan kucuk (genelde son) parcada duzeltme gecisi atlanir
PARALEL = 0                     # 0: llm.butce("paralel") (varsayilan 2; config.json 'paralel' ile artirilabilir)
CAKISMA_SN = 4                  # K5: Whisper satiriyla ayni anda (±) konusan baska kisinin altyazisi aranir
CAKISMA_KAPSAMA = 0.30          # K5: altyazi koklerinin en fazla bu kadari Whisper'da geciyorsa soz kaybolmus
IKISI_BEKLE_SN = 8              # A6: 'ikisi' modunda altyazi satiri Whisper karsiligini bu kadar bekler
AYNI_SOZ = 0.45                 # Whisper cumlesi ile altyazi satiri ayni sozu tasiyor (metin_benzerligi)


def parca_token():
    return PARCA_TOKEN or llm.butce("parca")


def paralel():
    return PARALEL or llm.butce("paralel")


def slug(s):
    s = s.lower()
    for a, b in zip("çğıöşü", "cgiosu"):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40] or "toplanti"


KISA_ONAY = {"evet", "hayır", "tamam", "peki", "yok", "ok", "olur", "aynen", "doğru", "tamamdır", "teşekkürler",
             "selam", "merhaba", "selamlar"}


def _kokler(metin):
    """Turkce ekleri kabaca yok saymak icin kelimelerin ilk 5 harfi (kucuk, sadesiz)."""
    m = metin.lower()
    for a, b in zip("çğıöşüâî", "cgiosuai"):
        m = m.replace(a, b)
    return {k[:5] for k in re.findall(r"[a-z0-9]+", m) if len(k) >= 3}


def ilgili_terimler(terimler, metin):
    """Sozluk terimlerinden metinde gercekten gecenler (kok eslesmesi)."""
    kokler = _kokler(metin)
    secilen = []
    for t in terimler:
        tk = _kokler(t)
        if tk and all(k in kokler for k in tk):
            secilen.append(t)
    return secilen


def dayanak_kontrolu(not_md, transkript, esik=0.34, ozet_kaynak=None):
    """Nottaki Karar/Aksiyon maddelerini ve ozet cumlelerini transkriptle karsilastirir; kelime
    koklerinin yeterli kismi transkriptte gecmeyen maddeleri cikarir. Modelin sozluk/gundemden
    ya da genel bilgisinden 'uydurdugu' satirlara karsi son sigorta. Dondurur (yeni_md, atilanlar).
    ozet_kaynak: ozet paragrafi icin ek dayanak (bolum ozetleri). Ozet cumleleri soyutlayici fiiller
    ('gorusuldu', 'ele alindi') tasir; yalniz transkriptle olculunce dogru cumleler de atiliyordu."""
    kokler = _kokler(transkript)
    if len(kokler) < 6:
        return not_md, []
    atilan, yeni = [], []
    bolum = ""
    for satir in not_md.split("\n"):
        cizgisiz = satir.strip()
        if cizgisiz.startswith("#"):
            bolum = cizgisiz.lower()
            yeni.append(satir)
            continue
        aday = None
        if ("karar" in bolum or "aksiyon" in bolum or "soru" in bolum) and cizgisiz.startswith(("-", "*", "|")):
            if cizgisiz.startswith("|"):
                hucreler = [h.strip() for h in cizgisiz.strip("|").split("|")]
                if len(hucreler) < 2 or set(hucreler[0]) <= set("-: ") or hucreler[0] in ("#",):
                    yeni.append(satir)
                    continue
                aday = hucreler[1]
            else:
                aday = cizgisiz.lstrip("-* ").strip()
        elif "sonraki" in bolum and cizgisiz and cizgisiz.strip(" -.") :
            # cumleyi ';' ile parcalara ayir, dayanagi olmayan parcayi dusur
            parcalar, tutulan = [x.strip() for x in cizgisiz.split(";") if x.strip()], []
            for pr in parcalar:
                pk = _kokler(pr)
                if not pk or len(pk & kokler) / len(pk) >= esik:
                    tutulan.append(pr)
                else:
                    atilan.append(pr)
            yeni.append("; ".join(tutulan).rstrip(",;") + ("" if not tutulan else ("." if not tutulan[-1].endswith(".") else "")))
            continue
        if aday is None:
            yeni.append(satir)
            continue
        ak = _kokler(aday) - {"ve", "ile", "icin", "olan", "olarak", "yapil", "edil", "kararl", "karar", "aksiy"}
        if not ak:
            yeni.append(satir)
            continue
        oran = len(ak & kokler) / len(ak)
        if oran >= esik:
            yeni.append(satir)
        else:
            atilan.append(aday)
    # Ozet paragrafi: cumle cumle
    md = "\n".join(yeni)
    m = re.search(r"(## Özet\s*\n)(.*?)(\n## )", md, flags=re.S)
    if m:
        ozet_kokler = kokler | (_kokler(ozet_kaynak) if ozet_kaynak else set())
        cumleler = re.split(r"(?<=[.!?])\s+", m.group(2).strip())
        tutulan, ozet_atilan = [], []
        for c in cumleler:
            ck = _kokler(c)
            if not ck or len(ck & ozet_kokler) / len(ck) >= esik - 0.06:
                tutulan.append(c)
            else:
                ozet_atilan.append(c)
        if tutulan:                   # hepsi atiliyorsa olcut yanilmistir: ozeti bos birakma
            atilan += ozet_atilan
            md = md[:m.start(2)] + " ".join(tutulan) + "\n" + md[m.end(2):]
    return md, atilan


def _sn(x):
    try:
        p = [int(k) for k in str(x).split(":")]
        return p[0] * 3600 + p[1] * 60 + (p[2] if len(p) > 2 else 0)
    except (ValueError, IndexError):
        return 0


def blok_metni(satirlar):
    birlesik = []
    for s in satirlar:
        if birlesik and birlesik[-1][0] == s["speaker"]:
            birlesik[-1] = (s["speaker"], birlesik[-1][1] + " " + s["text"])
        else:
            birlesik.append((s["speaker"], s["text"]))
    return "\n".join(f"{k}: {t}" for k, t in birlesik)


class Motor:
    def __init__(self, klasor, baslik, tarih, sozluk=None, katilimcilar=None, gundem="",
                 duzelt=True, olay=None, kaynak="altyazi", ben=None, sablon="genel"):
        self.klasor, self.baslik, self.tarih = klasor, baslik, tarih
        self.sozluk = sozluk or Sozluk()
        self.katilimcilar = list(katilimcilar or [])
        self.gundem = gundem
        self.duzelt = duzelt
        self.sablon = sablon                 # not sablonu (llm.SABLONLAR)
        self._olay = olay or (lambda tip, veri: None)
        self.parca_klasor = os.path.join(klasor, "parcalar")
        os.makedirs(self.parca_klasor, exist_ok=True)
        self.mevcut, self.mevcut_tok, self.mevcut_bas, self.son_satir = [], 0, None, None
        self.parca_no = len([f for f in os.listdir(self.parca_klasor) if f.endswith(".json")])
        self.seri = slug(baslik)             # toplanti serisi (sozluk kapsami, baglam ayirimi)
        self.kaynak = kaynak                 # altyazi | ses | ikisi
        self.ben = ben                       # mikrofon akisinin sahibi (kullanicinin adi)
        # altyazidan: {"ts", "speaker", "text", "kullanildi", "son"}. kullanildi: None | "ses" (bir Whisper
        # satiri bu sozu yazdi) | "altyazi" (altyazi metni transkripte girdi: K5 cakisma ya da A6 zaman asimi).
        # son: 'ikisi' modunda Whisper karsiliginin beklendigi son an (A6), digerlerinde None.
        self.konusmaci_izi = []
        self._son_ses = []                   # son Whisper satirlari {"ts", "text"} (K5 kapsama, A6 eslesme)
        self._iz_kilit = threading.RLock()   # altyazi (yakalama is parcacigi) ve Whisper (STT iscileri) ayni izi kullanir
        self.bekleyen_ses = []               # konusmacisi henuz bilinmeyen Whisper satirlari
        self.uyum = []                       # son Whisper cumlelerinin altyaziyla benzerligi (0-1)
        self.oneriler = []
        self._oneri_yolu = os.path.join(klasor, "oneriler.json")
        if os.path.exists(self._oneri_yolu):
            with open(self._oneri_yolu, encoding="utf-8") as f:
                self.oneriler = json.load(f)
        self.havuz = cf.ThreadPoolExecutor(max_workers=paralel())
        self.isler = []
        self.kilit = threading.Lock()
        self._gunluk_kilit = threading.Lock()
        llm.GUNLUK_FN = self._llm_gunluk
        llm.UYARI_FN = lambda m: self.log(f"  ! LLM: {m}")
        llm.SON_HATA = None                  # arayuz 'Not eksik' satirinda bu toplantinin son LLM hatasini gosterir
        # Var olan kaydi yuklerken durumunu koru: eskiden Gecmis'te bir notu yalnizca ACMAK bile toplantiyi
        # 'devam' (yarim) olarak isaretliyordu (ve saklama suresi 'tamam' kayitlari bulamiyordu).
        onceki = None
        try:
            with open(os.path.join(klasor, "meta.json"), encoding="utf-8") as f:
                onceki = json.load(f).get("durum")
        except Exception:
            pass
        self.meta_yaz(onceki or "devam")

    # ---------- olay/log ----------
    def log(self, m):
        self._olay("log", m)

    def adim(self, metin, no=None, toplam=None):
        """Uzun islerin (not uretimi, yeniden ozetleme) asamasi: arayuz ilerleme seridine yazar."""
        self._olay("adim", {"metin": metin, "no": no, "toplam": toplam})

    def _llm_gunluk(self, kayit):
        """LLM cagri istatistigi (finish_reason, token, sure; icerik yok) -> llm_log.jsonl.
        'Not neden yarim/Ingilizce?' sorusunda ilk bakilacak yer."""
        with self._gunluk_kilit:
            with open(os.path.join(self.klasor, "llm_log.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(kayit, ensure_ascii=False) + "\n")

    # ---------- disk ----------
    def meta_yaz(self, durum, **ek):
        """ek: meta.json'a yazilacak ek alanlar (or. konusma_paylari)."""
        yol = os.path.join(self.klasor, "meta.json")
        meta = {}
        try:                                  # bilinmeyen alanlar (ör. transkript_silindi) korunur
            with open(yol, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            pass
        meta.update({"baslik": self.baslik, "tarih": self.tarih.isoformat(), "durum": durum,
                     "katilimcilar": self.katilimcilar, "gundem": self.gundem, "parca": self.parca_no,
                     "kaynak": self.kaynak})
        meta.update(ek)
        with open(yol, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)

    def parca_yolu(self, no):
        return os.path.join(self.parca_klasor, f"parca_{no:03d}.json")

    def parca_oku(self, no):
        # kilit: paralel isciler ayni anda yazarken (parca_yaz dosyayi once bosaltir) yarim JSON okunmasin;
        # _onceki_konular bir onceki parcayi okurken o parcanin ozeti yaziliyor olabilir
        with self.kilit:
            with open(self.parca_yolu(no), encoding="utf-8") as f:
                return json.load(f)

    def parca_yaz(self, veri):
        with self.kilit:
            with open(self.parca_yolu(veri["sira"]), "w", encoding="utf-8") as f:
                json.dump(veri, f, ensure_ascii=False, indent=1)

    def onerileri_yaz(self):
        with self.kilit:
            with open(self._oneri_yolu, "w", encoding="utf-8") as f:
                json.dump(self.oneriler, f, ensure_ascii=False, indent=1)

    def baglam(self, metin=None):
        """LLM baglami. metin verilirse sozlukten YALNIZCA bu metinde gecen terimler eklenir:
        baska toplantilardan ogrenilmis terimler ozet/notta konu gibi belirmesin."""
        terimler = self.sozluk.terimler_icin(self.seri)
        if metin is not None:
            terimler = ilgili_terimler(terimler, metin)
        return llm.baglam_metni(self.baslik, self.tarih, self.katilimcilar, "; ".join(terimler) or "-", self.gundem)

    def transkript_metni(self):
        parcalar = []
        for no in range(1, self.parca_no + 1):
            try:
                parcalar.append(blok_metni(self.parca_oku(no)["satirlar"]))
            except Exception:
                pass
        parcalar.append(blok_metni(self.mevcut))
        return "\n".join(parcalar)

    # ---------- yakalama tarafi ----------
    def altyazi_satiri(self, satir):
        """Altyazidan gelen satir: konusmaci-zaman haritasina (iz) yazilir.
        'altyazi': normal satir olarak hemen islenir. 'ses': nota girmez (metni Whisper veriyor).
        'ikisi' (A6): hemen girmez; IKISI_BEKLE_SN icinde benzer bir Whisper satiri gelirse (ya da gelmisse)
        duser, gelmezse kontrol() altyazi satirini ekler (Whisper kacirmis)."""
        with self._iz_kilit:
            giris = {"ts": satir["ts"], "speaker": satir["speaker"], "text": satir.get("text", ""),
                     "kullanildi": None, "son": None}
            self.konusmaci_izi.append(giris)
            self.konusmaci_izi = self.konusmaci_izi[-400:]
            if satir["speaker"] not in self.katilimcilar and satir["speaker"] != "?":
                self.katilimcilar.append(satir["speaker"])
            if self.kaynak == "altyazi":
                giris["kullanildi"] = "altyazi"
                self.satir_ekle(satir)
                return
            if self.kaynak == "ikisi":
                giris["son"] = time.time() + IKISI_BEKLE_SN
                # altyazi genelde Whisper'dan 1-3 sn gec gelir: ayni soz zaten yazilmis olabilir
                hedef = _sn(giris["ts"])
                if any(abs(_sn(w["ts"]) - hedef) <= 30 and metin_benzerligi(w["text"], giris["text"]) >= AYNI_SOZ
                       for w in self._son_ses):
                    giris["kullanildi"] = "ses"
            self._olay("altyazi_izi", {"ts": satir["ts"], "speaker": satir["speaker"],
                                       "text": satir["text"]})
            self._bekleyenleri_coz()

    def _bekleyen_altyazilar(self, hepsi=False):
        """A6: Whisper karsiligi suresi icinde gelmeyen altyazi satirlarini transkripte ekler (hepsi=True:
        bitiste, sure beklemeden)."""
        with self._iz_kilit:
            simdi = time.time()
            for g in list(self.konusmaci_izi):
                if g["son"] is None or g["kullanildi"] or not (hepsi or simdi >= g["son"]):
                    continue
                g["kullanildi"] = "altyazi"
                self.satir_ekle({"ts": g["ts"], "speaker": g["speaker"], "text": g["text"]}, kaynak="altyazi")

    def _en_benzer_giris(self, ts, metin, pencere_sn=30):
        """ts cevresinde metne en benzeyen altyazi izi girdisi -> (giris, benzerlik) ya da (None, 0)."""
        hedef, en, en_b, en_fark = _sn(ts), None, 0.0, None
        for g in self.konusmaci_izi:
            fark = abs(hedef - _sn(g["ts"]))
            if fark > pencere_sn or not g["text"]:
                continue
            b = metin_benzerligi(metin, g["text"])
            if b > en_b or (en is not None and b == en_b and fark < en_fark):
                en, en_b, en_fark = g, b, fark
        return en, en_b

    def _cakisanlar(self, ts, kim, metin):
        """K5: Whisper satiri `kim`e yazildi; ±CAKISMA_SN icinde BASKA bir konusmacinin henuz kullanilmamis
        altyazi satiri var ve kelime koklerinin en fazla %30'u bu (ve civardaki) Whisper metninde geciyorsa
        Whisper o kisinin sozunu yazmamis demektir (iki kisi ayni anda konustu, baskin ses yazildi)."""
        hedef = _sn(ts)
        yazilan = _kokler(metin)
        for w in self._son_ses:
            if abs(_sn(w["ts"]) - hedef) <= CAKISMA_SN:
                yazilan |= _kokler(w["text"])
        sonuc = []
        for g in self.konusmaci_izi:
            if g["kullanildi"] or g["speaker"] in (kim, "?") or self._ben_mi(g["speaker"]):
                continue                    # kullanicinin kendi sozu mikrofon akisindan ayrica gelir
            if abs(_sn(g["ts"]) - hedef) > CAKISMA_SN:
                continue
            gk = _kokler(g["text"])
            if gk and len(gk & yazilan) / len(gk) <= CAKISMA_KAPSAMA:
                sonuc.append(g)
        return sonuc

    def _bekleyenleri_coz(self):
        """Altyazi Whisper'dan 1-3 sn gec gelir: '?' kalmis satirlara konusmaci sonradan atanir."""
        simdi = time.time()
        kalan = []
        for kayit in self.bekleyen_ses:
            satir, son = kayit
            kim, benzer = self._konusmaci_bul(satir["ts"], satir["text"], puanla=True)
            if benzer is not None and (benzer >= 0.25 or simdi >= son):
                self.uyum = (self.uyum + [benzer])[-30:]
            if kim and benzer < 0.25 and simdi >= son and not self._tek_konusmaci(satir["ts"], kim):
                kim = None      # sure doldu, metin hic eslesmedi ve pencerede baska konusan da var: tahmin etme
            if kim and (benzer >= 0.25 or simdi >= son):
                giris, gb = self._en_benzer_giris(satir["ts"], satir["text"])
                if giris is not None and gb >= AYNI_SOZ and not giris["kullanildi"]:
                    giris["kullanildi"] = "ses"
                satir["speaker"] = kim
                if kim not in self.katilimcilar:
                    self.katilimcilar.append(kim)
                self._olay("satir_guncelle", {"id": satir["id"], "speaker": kim})
            elif simdi < son:
                kalan.append(kayit)
        self.bekleyen_ses = kalan

    def ses_satiri(self, ts, konusmaci, metin, akis):
        """Whisper'dan gelen satir. konusmaci None ise altyaziyla zaman hizalamasi yapilir."""
        with self._iz_kilit:
            return self._ses_satiri(ts, konusmaci, metin, akis)

    def _ses_satiri(self, ts, konusmaci, metin, akis):
        if konusmaci == "ben":
            kim, benzer = (self.ben or "Ben"), None
            # Kulakliksiz (hoparlorle) calisilirken karsi tarafin sesi mikrofona da girer; eko tekillestirmesi
            # iki Whisper cikisi farkli yazinca kacirir. Altyazida bu cumleyi baskasi soylediyse ona yaz.
            alt_kim, alt_benzer = self._konusmaci_bul(ts, metin, puanla=True)
            if alt_kim and (alt_benzer or 0) >= 0.6 and not self._ben_mi(alt_kim):
                kim = alt_kim
        else:
            kim, benzer = self._konusmaci_bul(ts, metin, puanla=True)
            if benzer is not None:
                self.uyum = (self.uyum + [benzer])[-30:]
                # Cok kisa (<=2 kelime) ve altyazinin hic dogrulamadigi parca: buyuk olasilikla Whisper
                # gurultuye yazdi ("Rides", "Hesap etrafi"). Kisa onaylar (tamam/evet) haric elenir.
                # 3 kelimelik gercek cumleler, Teams altyazisi bozuk oldugunda da korunur.
                kelime = [k for k in re.findall(r"[^\W\d_]+", metin) if len(k) >= 3]
                if len(kelime) <= 2 and metin.lower().strip(" .?!,") not in KISA_ONAY:
                    alt_kok = self._altyazi_kokleri(ts)
                    if alt_kok and not any(_kokler(k) & alt_kok for k in kelime):
                        self._olay("log", f"  · elendi (altyazı doğrulamadı): {metin[:40]}")
                        return None
        if konusmaci != "ben" and kim and (benzer or 0) < 0.25 and not self._tek_konusmaci(ts, kim):
            # Metin hicbir altyazi satiriyla eslesmedi, yalnizca zamana gore tahmin: o aralikta birden fazla
            # kisi konusuyorsa yanlis kisiye yazmamak icin '?' ile baslar; altyazi gelince atanir.
            kim = None
        giris, gb = self._en_benzer_giris(ts, metin)
        if giris is not None and gb >= AYNI_SOZ:
            if giris["kullanildi"] == "altyazi":
                # K5/A6: bu soz altyazidan transkripte girdi; Whisper satiri ikinci kez yazilmaz
                self._olay("log", f"  · atlandı (altyazıdan zaten yazıldı): {metin[:40]}")
                return None
            giris["kullanildi"] = "ses"
        cakisan = self._cakisanlar(ts, kim, metin) if (konusmaci != "ben" and kim) else []
        for g in cakisan:
            g["kullanildi"] = "altyazi"
        once = [g for g in cakisan if _sn(g["ts"]) <= _sn(ts)]
        for g in once:                      # transkript zaman sirasiyla: onceki altyazi satiri once
            self.satir_ekle({"ts": g["ts"], "speaker": g["speaker"], "text": g["text"]}, kaynak="altyazi-cakisma")
        yeni = self.satir_ekle({"ts": ts, "speaker": kim or "?", "text": metin}, kaynak="ses")
        for g in cakisan:
            if g not in once:
                self.satir_ekle({"ts": g["ts"], "speaker": g["speaker"], "text": g["text"]}, kaynak="altyazi-cakisma")
        if cakisan:
            self._olay("log", f"  · aynı anda konuşma: {len(cakisan)} altyazı satırı eklendi "
                              f"({', '.join(g['speaker'] for g in cakisan)})")
        self._son_ses = (self._son_ses + [{"ts": ts, "text": metin}])[-50:]
        if konusmaci != "ben" and yeni is not None and (not kim or (benzer or 0) < 0.25):
            # altyazi henuz gelmemis olabilir: 8 sn boyunca yeniden dene
            self.bekleyen_ses.append((yeni, time.time() + 8))
        return yeni

    def _ben_mi(self, ad):
        """Altyazidaki ad uygulamayi kullanan kisi mi? ('Mert' ~ 'Mert Ali Erenturk')"""
        if not self.ben or not ad:
            return False
        a, b = set(normalize(self.ben).split()), set(normalize(ad).split())
        return bool(a & b)

    def _tek_konusmaci(self, ts, kim, pencere_sn=10):
        """ts cevresinde (±pencere) altyazida yalnizca `kim` mi konusuyor?"""
        hedef = _sn(ts)
        return all(g["speaker"] == kim for g in self.konusmaci_izi if abs(hedef - _sn(g["ts"])) <= pencere_sn)

    def _altyazi_kokleri(self, ts, pencere_sn=20):
        """ts cevresindeki (±pencere) altyazi satirlarinin kelime kokleri."""
        def sn(x):
            try:
                p = [int(k) for k in str(x).split(":")]
                return p[0] * 3600 + p[1] * 60 + (p[2] if len(p) > 2 else 0)
            except (ValueError, IndexError):
                return 0
        hedef, kokler = sn(ts), set()
        for g in self.konusmaci_izi:
            if abs(hedef - sn(g["ts"])) <= pencere_sn:
                kokler |= _kokler(g["text"])
        return kokler

    def uyum_orani(self):
        """Whisper cumleleri ile altyazi ne kadar ortusuyor (0-1)? Dusukse ses yanlis cihazdan
        geliyor ya da altyazi kapali olabilir."""
        return (sum(self.uyum) / len(self.uyum)) if self.uyum else None

    def _konusmaci_bul(self, ts, metin="", pencere_sn=30, puanla=False):
        """Whisper cumlesine konusmaci atar: altyazi izindeki satirlarla hem ZAMAN yakinligi hem
        METIN benzerligi (Teams'in bozuk altyazisi bile ayni cumlenin izini tasir) puanlanir."""

        def sn(x):
            try:
                p = [int(k) for k in str(x).split(":")]
                return p[0] * 3600 + p[1] * 60 + (p[2] if len(p) > 2 else 0)
            except (ValueError, IndexError):
                return 0

        def sade(m):
            m = (m or "").lower()
            for a, b in zip("çğıöşü", "cgiosu"):
                m = m.replace(a, b)
            return m

        hedef, m1 = sn(ts), sade(metin)
        en_iyi, en_puan, en_benzer, gorulen = None, -1.0, 0.0, False
        for g in reversed(self.konusmaci_izi):
            t, kim, alt = g["ts"], g["speaker"], g["text"]
            fark = hedef - sn(t)
            if abs(fark) > pencere_sn:
                continue
            gorulen = True
            zaman = 1.0 - abs(fark) / pencere_sn
            if fark < 0:
                zaman *= 0.6                                   # gelecekteki altyazi daha az guvenilir
            benzer = metin_benzerligi(metin, alt) if m1 and alt else 0.0
            en_benzer = max(en_benzer, benzer)
            if benzer >= 0.45:                                 # altyazi ayni cumleyi tasiyor: kesin eslesme
                puan = 1.0 + benzer + 0.2 * zaman
            elif benzer >= 0.25:
                puan = 0.35 * zaman + 0.65 * benzer
            else:
                puan = 0.35 * zaman
            if puan > en_puan:
                en_iyi, en_puan = kim, puan
        if puanla:
            return en_iyi, (en_benzer if gorulen else None)
        return en_iyi

    def satir_ekle(self, satir, kaynak="altyazi"):
        raw = satir["text"]
        metin, _ = self.sozluk.uygula(raw)
        self._satir_no = getattr(self, "_satir_no", 0) + 1
        satir = {"id": self._satir_no, "ts": satir["ts"], "speaker": satir["speaker"], "text": metin,
                 "raw": raw, "kaynak": kaynak}
        with open(os.path.join(self.klasor, "altyazi.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(satir, ensure_ascii=False) + "\n")
        if satir["speaker"] not in self.katilimcilar:
            self.katilimcilar.append(satir["speaker"])
        self._olay("satir", satir)

        simdi = time.time()
        if self.mevcut and self.mevcut_tok >= SESSIZLIK_MIN_TOKEN and simdi - self.son_satir > SESSIZLIK_SN:
            self.parca_kapat("sessizlik")
        self.mevcut.append(satir)
        self.mevcut_tok += llm.token_tahmin(f"{satir['speaker']}: {satir['text']}")
        self.mevcut_bas = self.mevcut_bas or simdi
        self.son_satir = simdi
        if self.mevcut_tok >= parca_token():
            self.parca_kapat("token")
        elif simdi - self.mevcut_bas >= PARCA_SURE_SN:
            self.parca_kapat("süre")
        return satir

    def kontrol(self):
        """Yakalama dongusunun her turunda: suresi dolan altyazi satirlari (A6) ve parca suresi."""
        if self.kaynak == "ikisi":
            self._bekleyen_altyazilar()
        if self.mevcut and time.time() - self.mevcut_bas >= PARCA_SURE_SN:
            self.parca_kapat("süre")

    def parca_kapat(self, neden):
        if not self.mevcut:
            return
        self.parca_no += 1
        zamanlar = [s["ts"] for s in self.mevcut]
        veri = {"sira": self.parca_no, "neden": neden, "token": self.mevcut_tok,
                "baslangic": min(zamanlar), "bitis": max(zamanlar),
                "satirlar": self.mevcut, "duzeltmeler": [], "ozet": None}
        self.parca_yaz(veri)
        self.mevcut, self.mevcut_tok, self.mevcut_bas = [], 0, None
        self.meta_yaz("devam")
        self._olay("parca_kapandi", {"sira": veri["sira"], "neden": neden, "token": veri["token"],
                                     "aralik": f"{veri['baslangic']}–{veri['bitis']}"})
        self.isler.append(self.havuz.submit(self._isle, veri, self.duzelt))

    # ---------- arka plan ----------
    def _isle(self, veri, duzelt):
        # 1) duzeltme gecisi — basarisiz olursa ham metinle devam
        if duzelt and veri["token"] >= DUZELT_MIN_TOKEN:
            self.log(f"  parça {veri['sira']}: altyazı düzeltmesi...")
            try:
                satirlar = [f"{s['speaker']}: {s['text']}" for s in veri["satirlar"]]
                yeni, duzeltmeler = llm.satirlari_duzelt(satirlar, self.baglam())
                for s, y in zip(veri["satirlar"], yeni):
                    kim, _, ne = y.partition(":")
                    if ne.strip():
                        s["text"] = ne.strip()
                veri["duzeltmeler"] = duzeltmeler
                for o, d in duzeltmeler:
                    self.oneri_ekle(veri["sira"], o, d, self._baglam_satiri(veri, o), "duzeltme")
            except Exception as e:
                self.log(f"  ! parça {veri['sira']} düzeltme atlandı: {llm.hata_metni(e)}")
        # 2) ozet
        self.log(f"  parça {veri['sira']}: özetleniyor...")
        try:
            onceki = self._onceki_konular(veri["sira"])
            metin = blok_metni(veri["satirlar"])
            ozet = llm.bolum_ozetle(metin, veri["sira"], self.baglam(metin), onceki)
            for a in (ozet or {}).get("aksiyonlar", []):
                a["tarih"] = tarih_mod.tarih_coz(a.get("tarih"), self.tarih)
            veri["ozet"] = ozet
            if ozet:
                for t in ozet.get("belirsiz_terimler", []):
                    self.oneri_ekle(veri["sira"], t, "", self._baglam_satiri(veri, t), "belirsiz")
        except Exception as e:
            self.log(f"✖ parça {veri['sira']} özetlenirken hata: {llm.hata_metni(e)}")
            veri["ozet"] = None
        self.parca_yaz(veri)
        self._olay("parca_ozetlendi", {"sira": veri["sira"], "ozet": veri["ozet"],
                                       "duzeltme": len(veri["duzeltmeler"])})
        return veri["ozet"]

    def _onceki_konular(self, sira):
        if sira <= 1 or not os.path.exists(self.parca_yolu(sira - 1)):
            return None
        return (self.parca_oku(sira - 1).get("ozet") or {}).get("konular") or None

    @staticmethod
    def _baglam_satiri(veri, parca):
        p = normalize(parca)
        for s in veri["satirlar"]:
            if p and p in normalize(s["text"]):
                return f"{s['speaker']}: {s['text']}"
        return ""

    # ---------- oneriler ----------
    def oneri_ekle(self, parca, yanlis, oneri, baglam, tur):
        yanlis = yanlis.strip()
        if not yanlis or self.sozluk.bilinen_mi(yanlis) or normalize(yanlis) == normalize(oneri):
            return
        with self.kilit:
            if any(normalize(o["yanlis"]) == normalize(yanlis) for o in self.oneriler):
                return
            o = {"id": len(self.oneriler) + 1, "parca": parca, "yanlis": yanlis, "oneri": oneri.strip(),
                 "baglam": baglam[:200], "tur": tur, "durum": "bekliyor"}
            self.oneriler.append(o)
        self.onerileri_yaz()
        self._olay("oneri", o)

    def bekleyen_oneriler(self):
        return [o for o in self.oneriler if o["durum"] == "bekliyor"]

    # ---------- eksik / yeniden ----------
    def eksikleri_isle(self):
        """Ozeti olmayan parcalari (cokme, hata) isler. Islenen parca sayisini dondurur."""
        eksik = [v for v in (self.parca_oku(n) for n in range(1, self.parca_no + 1)) if v.get("ozet") is None]
        if not eksik:
            return 0
        self.log(f"{len(eksik)} parça işlenmemiş, işleniyor ({paralel()} paralel)...")
        with cf.ThreadPoolExecutor(max_workers=paralel()) as ex:
            list(ex.map(lambda v: self._isle(v, self.duzelt), eksik))
        return len(eksik)

    def parca_yeniden(self, no):
        """Bir parcayi sifirdan (duzeltme + ozet) yeniden isler."""
        veri = self.parca_oku(no)
        veri["ozet"], veri["duzeltmeler"] = None, []
        self._isle(veri, self.duzelt)
        return self.parca_oku(no)

    # ---------- bitis ----------
    def bitir(self):
        """Son parcayi kapatir, arka plan islerini bekler; bekleyen onerileri dondurur."""
        self._bekleyen_altyazilar(hepsi=True)     # A6: Whisper karsiligi gelmemis son altyazi satirlari
        self.parca_kapat("bitiş")
        bekleyen = [i for i in self.isler if not i.done()]
        if bekleyen:
            self.log(f"{len(bekleyen)} parça işlemi bekleniyor...")
        cf.wait(self.isler)
        self.meta_yaz("inceleme")
        return self.bekleyen_oneriler()

    def kararlari_uygula(self, kararlar):
        """kararlar: {oneri_id: dogru_metin | None}. None = yoksay.
        Onaylananlar sozluge girer ve tum parcalara uygulanir; degisen parcalar yeniden ozetlenir."""
        onay = 0
        for o in self.oneriler:
            if o["id"] in kararlar:
                dogru = kararlar[o["id"]]
                if dogru:
                    self.sozluk.alias_ekle(o["yanlis"], dogru, seri=self.seri)
                    o["durum"], o["karar"] = "onay", dogru
                    onay += 1
                else:
                    o["durum"] = "yoksay"
        self.onerileri_yaz()
        if not onay:
            return 0
        kirli = self.sozlugu_uygula()
        if kirli:
            self.log(f"{len(kirli)} parça düzeltmelerle yeniden özetleniyor...")

            def isle(iv):
                i, v = iv
                self.adim(f"Parça {v['sira']} düzeltmelerle yeniden özetleniyor ({i}/{len(kirli)})", i, len(kirli))
                self._isle(v, False)
            with cf.ThreadPoolExecutor(max_workers=paralel()) as ex:
                list(ex.map(isle, enumerate(kirli, 1)))
        return len(kirli)

    def sozlugu_uygula(self):
        """Sozlugu (onayli alias'lar) tum parcalarin satirlarina uygular; metni degisen parcalarin ozeti
        silinir ve diske yazilir. Dondurur: degisen parcalar (veri sozlukleri)."""
        kirli = []
        for no in range(1, self.parca_no + 1):
            try:
                veri = self.parca_oku(no)
            except Exception:
                continue
            degisti = False
            for s in veri["satirlar"]:
                yeni, deg = self.sozluk.uygula(s["text"])
                if deg:
                    s["text"], degisti = yeni, True
            if degisti:
                veri["ozet"] = None
                self.parca_yaz(veri)
                kirli.append(veri)
        return kirli

    def not_yedekle(self):
        """Var olan not.md'yi not.md.yedek-YYYYMMDD-HHMM olarak kopyalar (yeniden ozetleme eskisini ezmesin).
        Dondurur: yedek yolu ya da None."""
        yol = os.path.join(self.klasor, "not.md")
        if not os.path.exists(yol):
            return None
        hedef = yol + dt.datetime.now().strftime(".yedek-%Y%m%d-%H%M")
        n = 2
        while os.path.exists(hedef):
            hedef = yol + dt.datetime.now().strftime(".yedek-%Y%m%d-%H%M") + f"-{n}"
            n += 1
        with open(yol, encoding="utf-8") as f, open(hedef, "w", encoding="utf-8") as g:
            g.write(f.read())
        return hedef

    def yeniden_ozetle(self):
        """Gecmis -> 'Yeniden ozetle': sozluk (sonradan buyumus olabilir) tum parcalara bir kez uygulanir,
        sonra TUM parcalar sifirdan (duzeltme + ozet) PARALEL islenir. Dondurur: islenen parca sayisi."""
        self.adim("Sözlük parçalara uygulanıyor")
        degisen = self.sozlugu_uygula()
        if degisen:
            self.log(f"sözlük {len(degisen)} parçada metni düzeltti")
        toplam = self.parca_no
        if not toplam:
            return 0
        self.log(f"{toplam} parça yeniden özetleniyor ({paralel()} paralel)...")

        def isle(no):
            self.adim(f"Parça {no}/{toplam} yeniden özetleniyor", no, toplam)
            try:
                veri = self.parca_oku(no)
            except Exception as e:
                self.log(f"✖ parça {no} okunamadı: {e!r}")
                return
            veri["ozet"], veri["duzeltmeler"] = None, []
            self._isle(veri, self.duzelt)

        with cf.ThreadPoolExecutor(max_workers=paralel()) as ex:
            list(ex.map(isle, range(1, toplam + 1)))
        return toplam

    def notu_uret(self):
        bolumler, eksik = [], []
        for no in range(1, self.parca_no + 1):
            veri = self.parca_oku(no)
            if veri.get("ozet") is None:
                self.adim(f"Parça {no}/{self.parca_no} özetleniyor", no, self.parca_no)
                self.log(f"  parça {no} özetleniyor...")
                self._isle(veri, False)
                veri = self.parca_oku(no)
            aralik = f"{veri.get('baslangic', '')}–{veri.get('bitis', '')}"
            if veri.get("ozet"):
                b = dict(veri["ozet"])
                b["aralik"] = aralik
                bolumler.append(b)
            else:
                eksik.append(aralik)
                self.log(f"  ! parça {no} notta yer almayacak ({aralik})")
        self.log("bölümler birleştiriliyor...")
        transkript = self.transkript_metni()
        satirlar = self.tum_satirlar()
        kaynak_fn = kaynak_bulucu(satirlar)
        asamalar = {"Kararlar ve aksiyonlar birleştiriliyor": 1, "Özet paragrafı yazılıyor": 2}
        not_md = f"# {self.baslik} — {self.tarih.isoformat()}\n\n" + llm.birlestir(
            bolumler, self.baglam(transkript), self.log, kaynak_fn=kaynak_fn, sablon=self.sablon,
            katilimcilar=self.katilimcilar,
            aksiyon_fn=lambda aks: sorumlu_dogrula(aks, satirlar, self.ben),
            adim_fn=lambda m: self.adim(m, asamalar.get(m), 4))
        self.adim("Dayanak kontrolü", 3, 4)
        bolum_ozetleri = "\n".join(b.get("ozet", "") for b in bolumler)
        not_md, atilan = dayanak_kontrolu(not_md, transkript, ozet_kaynak=bolum_ozetleri)
        for madde in atilan:
            self.log(f"  ! transkriptte dayanağı yok, nottan çıkarıldı: {madde[:90]}")
        if eksik:
            # sessizce eksik not yerine okuyana hangi araligin notta olmadigini soyle
            not_md = not_md.rstrip("\n") + "\n\n## Eksik bölümler\n" + "\n".join(
                f"- {a} arası özetlenemedi; ayrıntı için transkripte bakın." for a in eksik) + "\n"
        self.adim("Not kaydediliyor", 4, 4)
        with open(os.path.join(self.klasor, "not.md"), "w", encoding="utf-8") as f:
            f.write(not_md)
        import not_araclari                  # gec: not_araclari motor'u ice aktarir
        # N7: konusma paylari nota yazilmaz; Gecmis detayinda gosterilir
        self.meta_yaz("tamam", konusma_paylari=[list(p) for p in not_araclari.konusma_paylari(satirlar)])
        return not_md

    def tum_satirlar(self):
        """Tum parcalarin (duzeltilmis) satirlari + acik parca, kronolojik."""
        satirlar = []
        for no in range(1, self.parca_no + 1):
            try:
                satirlar += self.parca_oku(no)["satirlar"]
            except Exception:
                pass
        return satirlar + list(self.mevcut)

    def soru_sor(self, soru):
        """Toplantiya soru: transkript bolumleri (zaman damgali) uzerinden kurum ici LLM cevaplar."""
        bolumler = []
        for no in range(1, self.parca_no + 1):
            try:
                v = self.parca_oku(no)
            except Exception:
                continue
            satirlar = "\n".join(f"[{s['ts']}] {s['speaker']}: {s['text']}" for s in v["satirlar"])
            if satirlar:
                bolumler.append((f"{v.get('baslangic', '')}–{v.get('bitis', '')}", satirlar))
        if self.mevcut:
            bolumler.append(("açık parça", "\n".join(f"[{s['ts']}] {s['speaker']}: {s['text']}" for s in self.mevcut)))
        return llm.toplantiya_sor(soru, bolumler, self.baglam())

    def kapat(self):
        self.havuz.shutdown(wait=True)

    # ---------- diskten devam ----------
    @classmethod
    def yukle(cls, klasor, sozluk=None, olay=None, duzelt=True, ben=None, sablon="genel"):
        with open(os.path.join(klasor, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        m = cls(klasor, meta["baslik"], dt.date.fromisoformat(meta["tarih"]), sozluk,
                meta.get("katilimcilar"), meta.get("gundem", ""), duzelt=duzelt, olay=olay,
                kaynak=meta.get("kaynak", "altyazi"), ben=ben, sablon=sablon)
        return m


def kaynak_satiri_bulucu(satirlar, esik=0.3):
    """Notun maddesini transkriptte en iyi karsilayan SATIRA baglar -> fn(metin, sorumlu) -> satir | None.
    Kelime kokleri ortakligi olculur; sorumlu verilirse onun kendi satiri hafifce one alinir
    ('ben yaparim' diyen kisi)."""
    dizin = [(s, _kokler(s.get("text", ""))) for s in satirlar if s.get("text")]

    def bul(metin, sorumlu=None):
        mk = _kokler(metin) - {"ve", "ile", "icin", "olan", "olarak", "yapil", "edil"}
        if not mk or not dizin:
            return None
        en_iyi, en_puan = None, 0.0
        for s, kk in dizin:
            puan = len(mk & kk) / len(mk)
            kim = s.get("speaker", "")
            if sorumlu and kim and normalize(sorumlu).split()[:1] == normalize(kim).split()[:1]:
                puan += 0.1
            if puan > en_puan:
                en_iyi, en_puan = s, puan
        return en_iyi if en_puan >= esik else None
    return bul


def kaynak_bulucu(satirlar, esik=0.3):
    """Notun her maddesini transkriptte en iyi karsilayan satira baglar -> fn(metin, sorumlu) -> 'HH:MM:SS'."""
    bul = kaynak_satiri_bulucu(satirlar, esik)

    def ts(metin, sorumlu=None):
        s = bul(metin, sorumlu)
        return s["ts"] if s else None
    return ts


_BIRINCI_TEKIL = re.compile(
    r"(?<!\w)(ben|bana|benim|bende|bizzat)(?!\w)"
    r"|\w{2,}(?:[ae]c[ae]ğ[ıi]m|[ıiuü]yorum)(?!\w)"            # yapacağım, hazırlayacağım, bakıyorum
    r"|\w{3,}(?:[ae]r[ıi]m|[ıi]r[ıi]m|[uü]r[uü]m)(?!\w)")   # yaparım, hazırlarım, gönderirim, bulurum


def _ayni_kisi(a, b):
    """'Mert' ~ 'Mert Ali Erenturk': ilk ad ayniysa ayni kisi."""
    a, b = normalize(a or "").split()[:1], normalize(b or "").split()[:1]
    return bool(a) and a == b


def sorumlu_dogrula(aksiyonlar, satirlar, ben=None):
    """K8: aksiyon sorumlusunu transkriptteki kaynak satiriyla dogrular. Kaynak satiri birinci tekil sahis
    tasiyorsa ('ben hazirlarim', 'yapacagim', 'bana iletin') o sozu SOYLEYEN kisi (S) isin sahibidir:
    sorumlu 'belirsiz' ise S yazilir; sorumlu X, S degilse ve X o satirda adiyla gecmiyorsa 'X (?)' olur.
    Konusmacisi bilinmeyen ('?') satira dokunulmaz. Dondurur: yeni liste (girdi degismez)."""
    bul = kaynak_satiri_bulucu(satirlar)
    sonuc = []
    for a in aksiyonlar:
        a = dict(a)
        sonuc.append(a)
        s = bul(a.get("madde", ""))
        if not s:
            continue
        kim = (s.get("speaker") or "").strip()
        if kim in ("ben", "Ben") and ben:
            kim = ben
        if not kim or kim == "?":
            continue
        if not _BIRINCI_TEKIL.search(tr_kucuk(s.get("text"))):
            continue
        sorumlu = (a.get("sorumlu") or "").strip()
        if sorumlu.lower() in ("", "-", "belirsiz", "?"):
            a["sorumlu"] = kim
            continue
        if sorumlu.endswith("(?)"):
            continue
        adlar = [x.strip() for x in re.split(r",|/| ve ", sorumlu) if x.strip()]
        if any(_ayni_kisi(x, kim) for x in adlar):
            continue
        if any(ad_geciyor(x, s.get("text")) or ad_geciyor(x.split()[0], s.get("text")) for x in adlar):
            continue                    # 'Berk'e ilet, o baksin' gibi: X satirda adiyla geciyor
        a["sorumlu"] = f"{sorumlu} (?)"
    return sonuc


def saklama_uygula(gun, kok=None, bugun=None):
    """KVKK: notu uretilmis ('tamam') ve `gun` gunden eski toplantilarin TRANSKRIPTINI siler; not.md,
    meta.json ve parca ozetleri kalir. gun <= 0: kapali. Dondurur: temizlenen klasor sayisi."""
    kok = kok or KOK
    if not gun or gun <= 0 or not os.path.isdir(kok):
        return 0
    bugun = bugun or dt.date.today()
    sayi = 0
    for ad in os.listdir(kok):
        k = os.path.join(kok, ad)
        try:
            with open(os.path.join(k, "meta.json"), encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("durum") != "tamam" or meta.get("transkript_silindi"):
                continue
            if (bugun - dt.date.fromisoformat(meta["tarih"])).days < gun:
                continue
        except Exception:
            continue
        for dosya in ("altyazi.jsonl", "oneriler.json"):
            if os.path.exists(os.path.join(k, dosya)):
                os.remove(os.path.join(k, dosya))
        pk = os.path.join(k, "parcalar")
        for f_ in (os.listdir(pk) if os.path.isdir(pk) else []):
            yol = os.path.join(pk, f_)
            try:
                with open(yol, encoding="utf-8") as f:
                    v = json.load(f)
                v["satirlar"], v["duzeltmeler"] = [], []
                with open(yol, "w", encoding="utf-8") as f:
                    json.dump(v, f, ensure_ascii=False, indent=1)
            except Exception:
                continue
        meta["transkript_silindi"] = bugun.isoformat()
        with open(os.path.join(k, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
        sayi += 1
    return sayi


def yeni_klasor(baslik, tarih):
    """Her oturum kendi klasorunu alir; ayni gun ayni adla ikinci/ucuncu baslatma asla
    oncekinin klasorune yazmaz (eski surumde ayni dakikada iki baslatma cakisabiliyordu)."""
    taban = os.path.join(KOK, f"{tarih.isoformat()}_{slug(baslik)}")
    if not os.path.exists(taban):
        return taban
    klasor = taban + dt.datetime.now().strftime("_%H%M")
    n = 2
    while os.path.exists(klasor):
        klasor = f"{taban}{dt.datetime.now().strftime('_%H%M')}-{n}"
        n += 1
    return klasor
