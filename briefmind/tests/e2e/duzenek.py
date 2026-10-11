"""BriefMind uctan uca sentetik duzenek, Faz 1 + Faz 2 hatti (MIMARI_v3 §8 "Duzenek", §9 Faz 1 ve Faz 2 kabulu).

Gercek Motor + gercek Konusmaci + gercek SesServisi (klip olayi uretimi, STT metin suzgecleri, sirali yayim) +
gercek Yakalayici okuma olayi; sahte olanlar: STT (zemin gercegi metni), Teams paneli (zemin gercegindan uretilir),
yeniden kesme (SesServisi.yeniden_kes sozlesmesi: zarfta en dusuk enerjili 100 ms blok), LLM ve saat. Ag yok,
repo'ya dosya yazmaz: her calistirma kendi gecici klasorunde kosar ve bitince siler. Tum adlar kurgusal.

Akis (hepsi sanal saatte, olay yiginiyla):
  zemin gercegi (kim, bas, bit, metin)  ->  loopback / mikrofon VAD klipleri (C1: bas/bit epoch, zarf)
                                        ->  Teams paneli: gecikmeli, kelime artisli, revizyonlu satirlar, R ~1.2 sn
                                            okumalari (C2), altyazi kapali / okuyucu takilmasi pencereleri
  klip kapanir (bit + 0.55) -> STT (gecikme) -> SesServisi._sirali_yay -> Motor.ses_olayi
  okuma bitince -> Motor.okuma_ekle;  Motor kararlarinda `bol` -> sahte yeniden_kes -> iki cocuk klip.

Olcum klip_no ile yapilir (metin esleme yok): her son satirin klibi zemin gercegine bilinir.
Altyazi metni sentetik izlerle (capNNNkNN) isaretlidir: hicbir cikti dosyasinda gorunmemelidir.

Faz 2 (terim + duzeltme + halusinasyon filtresi): Motor gercek terim havuzu (terim.TerimHavuzu: davet + elle terimler) ve
gercek duzelt.duzelt kapisiyla kosar; SAHTE LLM (SahteLLM) gercekci bir karisim onerir: dogru terim duzeltmeleri, yaygin
kelimede yanlis duzeltmeler (komite -> commit), eki bozan duzeltmeler, tum satiri yeniden yazma, 'yazim' tahminleri,
satir/sema disi oneriler. Kapi onlari tutmali: bozulan (dogru kelimenin yanlis olmasi) 0. STT sahte (SahteStt) ama SesServisi._coz
gercek: Whisper prompt'u (terim.prompt) dusuk RMS klibe gitmez, sessiz klipte prompt metni cikiyorsa (sizinti) yine de kesin satir
olmaz. Halusinasyon klipleri (kara liste, dongu, kr/sn ucu, akici cumle) SesServisi'nin gercek stt.halusinasyon_puani +
Motor cizelge kuraliyla supheli ya da atilir. Ogrenme acik / kapali: ayni seri, 4 toplanti, son kelime hatasi acik <= kapali.
Faz 2 kapi senaryolari SENARYOLAR_EK'te (terim_duzeltme, halusinasyon_cesit, prompt_sizintisi); 14 Faz 1 senaryosu ayni
sahte LLM'in hafif haliyle kosar (kapilar degismez).

Kullanim (repo kokunden):  python tests/e2e/duzenek.py [senaryo ...] [--seed 7,11 | --tara 20] [--ayrinti]
"""
import bisect
import collections
import ctypes
import datetime as dt
import heapq
import itertools
import json
import math
import os
import queue
import random
import re
import shutil
import sys
import tempfile
import time as gercek_time
import types

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
_ARAC = os.path.join(REPO, "tools")
if _ARAC not in sys.path:
    sys.path.insert(0, _ARAC)


def _platform_destegi():
    """yakalayici.py Windows'a ozgu modulleri iceri aktarir; baska platformda bos taslak (conftest ile ayni)."""
    if "uiautomation" not in sys.modules:
        try:
            import uiautomation  # noqa: F401
        except Exception:
            sys.modules["uiautomation"] = types.ModuleType("uiautomation")
    if not hasattr(ctypes, "windll"):
        ctypes.windll = types.SimpleNamespace(user32=types.SimpleNamespace(SystemParametersInfoW=lambda *a: 0))


_platform_destegi()

import duzelt as duzelt_mod   # noqa: E402
import kelime         # noqa: E402
import llm            # noqa: E402
import motor as M     # noqa: E402
import olcum          # noqa: E402  (tools/olcum.py: kelime_etkisi)
import saat as saat_mod   # noqa: E402
import ses            # noqa: E402
import terim as terim_mod   # noqa: E402
import yakalayici     # noqa: E402
from konusmaci import Konusmaci   # noqa: E402
from metin import gorunen_ad      # noqa: E402

T0 = 1_760_000_000.0                     # sabit epoch (yerel saat dilimi yalniz 'ts' gosterimini etkiler)
BAS = dt.datetime.fromtimestamp(T0)
A, B, C, BEN = "Melek Taslak", "Kaan Uydurma", "Sinan Kurgu", "Zeynep Örnek"   # kurgusal adlar
ODA = "Salon Odası"                       # oda cihazi etiketi (kimlik_turu oda)
KONUK = "Konuk"                           # genel etiket (kimlik_turu genel)
HAYALET = "Uydurma Kişi"                  # altyazida bir kez beliren sezgisel ad (toplantida yok)
SEDLER = (7, 11, 23)                      # tablo tohumlari
SEDLER_GENIS = tuple(range(100, 110))     # kapi tohumlari ek kumesi (havuzlanmis oranlar icin)
BLOK = 0.1                                # zarf blogu (sn)
SES_ESIK_KISA = 0.65                      # VAD: sesli sure bundan kisaysa klip gonderilmez (MIN_SN - on-yuk - sonluk)
VAD_KAPANIS = ses.CUMLE_SESSIZLIK         # klip bit'ten bu kadar sonra kapanir (sessizlik beklenir)
PANEL_SATIR = 5                           # Teams panelinde ayni anda gorunen satir
KELIME_PERIYODU = 0.5                     # Teams altyazi parcasi yenileme sikligi (varsayim; Faz 0 verisiyle guncellenir)

# ---------------- zemin gercegi: metin havuzu ----------------
CUMLELER = [
    "Kubernetes kümesinde yeni sürümü dün akşam test ortamına aldık.",
    "Commit sonrası pipeline otomatik olarak çalışıyor ve raporu paylaşıyor.",
    "Kampanyayı gelecek ay müşterilere duyurmayı planlıyoruz.",
    "Teminatı yeniden hesaplamamız gerekiyor çünkü oranlar değişti.",
    "Mikroservisler arasındaki gecikme kabul edilebilir seviyede kaldı.",
    "Bu konuyu cuma gününe kadar netleştirelim lütfen.",
    "Pilot çağrı merkeziyle başlayacak, sonra şubelere yayılacak.",
    "Veri ambarındaki tabloların sahipliğini ekip üstlenecek.",
    "Test ortamı yeterli değil, ek sunucu talep edeceğiz.",
    "Kredi riski modelinin doğrulama raporu hazırlanıyor.",
    "Bütçe onayı gelmeden satın alma başlatamayız.",
    "Kullanıcı kabul testleri iki hafta sürecek gibi görünüyor.",
]
KISA_YANIT = ["Evet, aynen öyle.", "Tamam, anlaştık.", "Peki, ben bakarım.", "Olur, hallederiz.", "Tabii, hemen bakıyorum.",
              "Haklısın, öyle yapalım.", "Anladım, teşekkürler.", "Süper, devam edelim.", "Bence de öyle.",
              "Şimdilik bu kadar.", "Not aldım, dönerim.", "Doğru, katılıyorum."]
TERIM_KELIME = ["Kubernetes", "commit", "pipeline", "Mikroservisler"]          # Whisper'in bozdugu kelimeler
HALUSINASYON_METNI = ["Bu konuyu daha sonra tekrar konuşuruz.", "Evet, şimdi başlayabiliriz.",
                      "Anladım, o zaman devam edelim.", "Görüşmek üzere, iyi günler dilerim."]
# Whisper'in yanlis duydugu kelimeler (ham STT) -> dogru (zemin gercegi)
WHISPER_HATA = {"kubernetes": "kubernetis", "commit": "komit", "pipeline": "payplayn", "mikroservisler": "mikro servisler"}
SOZCUK = sorted({w.strip('.,').lower() for c in CUMLELER for w in c.split() if len(w) > 3})

# Senaryo tanimlari: 12 aile (gecikme 0 / 2 / 3.5 uc varyant). Parametreleri uretici okur.
SENARYOLAR = {
    "hizli_devir": dict(tur="hizli_devir"),
    "araya_girme": dict(tur="araya_girme"),
    "ayni_anda": dict(tur="ayni_anda"),
    "klip_ici_devir": dict(tur="klip_ici_devir"),
    "ben_uzak": dict(tur="ben_uzak", katilimcilar=(A, B, BEN)),
    "altyazi_kapali": dict(tur="karisik", kapali=((300.0, 420.0),)),
    "gecikme0": dict(tur="karisik", gecikme=0.0),
    "gecikme2": dict(tur="karisik", gecikme=2.0),
    "gecikme3_5": dict(tur="karisik", gecikme=3.5),
    "tekrar": dict(tur="karisik", tekrar=True),
    "halusinasyon": dict(tur="karisik", halusinasyon=True),
    "yanki": dict(tur="yanki", kulaklik=False, kapali=((200.0, 290.0),)),
    "okuyucu_takilmasi": dict(tur="karisik", takilma=((150.0, 175.0), (380.0, 400.0))),
    "stt_kesintisi": dict(tur="karisik", stt_kesinti=((120.0, 170.0, "kayip"), (300.0, 340.0, "diskte"))),
}
# Saat adimi (NTP/w32time, elle degisiklik): duvar saati toplanti ortasinda +-3 sn sicrar (goreli sn, fark). Zaman
# kaynagi saat.simdi() monotonik tabanli oldugundan zaman cizelgeleri etkilenmez: kapi 'kesin yanlis 0' ve sonuc adimsiz
# kosumla ayni. Bkz. test_saat_adimi.py. 'saat_eski': eski davranis (simdi = duvar saati) -- negatif kontrol.
SENARYOLAR_EK = {
    "saat_adimi": dict(tur="karisik", gecikme=2.0, saat_adimlari=((200.0, 3.0), (400.0, -6.0), (650.0, 3.0))),
}
# Faz 2 kapi senaryolari (MIMARI_v3 §9 Faz 2 kabulu); SENARYOLAR'a girmez (Faz 1 tablosu 14 senaryo kalir).
SENARYOLAR_FAZ2 = {
    # gercek terim kapisi: yogun sahte LLM (komite -> commit, eki bozan, satir yeniden yazma, 'yazim' tahmini), Faz 2 cumleleri
    "terim_duzeltme": dict(tur="karisik", cumleler="faz2", yogun=True, sure=600.0, ipucu=True),
    # halusinasyon cesitleri: sessiz bosluk (uzak) ve konusma ardi (yakin), loopback + mikrofon
    "halusinasyon_cesit": dict(tur="karisik", halusinasyon="cesit", sure=700.0, ipucu=True),
    # Whisper prompt'u: sessiz klipte prompt metni cikarsa (sizinti) kesin satir olmamali
    "prompt_sizintisi": dict(tur="karisik", halusinasyon="sessiz", sure=600.0, ipucu=True),
}
SEDLER_FAZ2 = SEDLER + SEDLER_GENIS[:2]
SURELER = {"karisik": 900.0, "hizli_devir": 420.0, "klip_ici_devir": 420.0, "araya_girme": 420.0, "ayni_anda": 320.0,
           "ben_uzak": 480.0, "yanki": 480.0}

# ---------------- Faz 2: terim havuzu, davet, Whisper hatalari, halusinasyon cesitleri ----------------
TERIM_VARSAYILAN = ("Kubernetes", "commit", "pipeline", "Mikroservisler", "Jenkins", "Jira", "Kampanya Modülü")   # elle / davet
DAVET_GOVDE = ("Gündem: Kubernetes geçişi, Jenkins hattı ve Jira kayıtları. Mikroservisler için yeni plan görüşülecek. "
               "Kampanya Modülü ele alınacak.")
DAVET = {"govde": DAVET_GOVDE, "ekler": []}
# Faz 2 senaryosu (terim_duzeltme) cumleleri: gercek kelime tuzaklari (komite, kampanyacı, teminat...), ekli terimler
# (Jenkins'e, Jira'ya, Kubernetes'in), sayi/tarih. {z}: zaman ifadesi (cumle cesitliligi: tekrar suzgeci az dusurur).
FAZ2_SABLON = [
    "Jenkins'e bağlanan işi {z} yeniden çalıştıralım.",
    "Jira'ya açtığımız kayıtlar {z} kapanacak.",
    "Kubernetes'in yeni sürümü {z} test ortamında denenecek.",
    "Komite toplantısı {z} saat 14:30'da yapılacak.",
    "Kampanyacı arkadaşlar raporu {z} gönderecek.",
    "Terminal ekranında gelen hatayı {z} birlikte inceleyelim.",
    "Taslak'ın hazırladığı rapor {z} 15 Ocak'ta paylaşılacak.",
    "Son commit'i {z} geri alıp pipeline yeniden çalıştıralım.",
    "Teminatlar için {z} yeni bir kampanya açacağız.",
    "Müşterinin talebi {z} kredi komitesine iletildi.",
    "Kubernetes kümesine {z} 3 yeni düğüm ekleyeceğiz.",
    "Bütçe {z} 2 milyon lira olarak onaylandı.",
    "Mikroservisler arasındaki gecikme {z} kabul edilebilir seviyede.",
    "Kredisi olan müşterilere {z} ayrı bir teklif hazırlayalım.",
    "Raporu {z} Jira'ya yükleyelim.",
    "Jenkins'e erişim izni için {z} talep açıldı.",
    "Komitede alınan karar {z} herkese duyurulacak.",
    "Kampanya modülünü {z} yayına alacağız.",
    "Pipeline sonucu başarısız olunca {z} ekibe haber gitsin.",
    "Teminat oranlarını {z} yeniden hesaplamamız gerekiyor.",
]
FAZ2_ZAMAN = ["bu akşam", "yarın sabah", "cuma günü", "pazartesi", "gelecek hafta", "öğleden sonra", "bu hafta", "ay sonunda"]
# Whisper'in Faz 2 cumlelerinde bozdugu ekli biçimler (anahtar: dogru biçimin kucuk harfi) -> duyulan
WHISPER_HATA_FAZ2 = dict(WHISPER_HATA, **{"jenkins'e": "cenkinse", "jira'ya": "ciraya", "kubernetes'in": "kubernetisin",
                                          "taslak'ın": "taşlakın", "commit'i": "komiti"})
# Halusinasyon cesitleri: (ad, metin, sesli sure sn, akis, icerik_yeterli). icerik_yeterli: metin kendi basina ayirt edilir
# (SesServisi suzgeci metni atar / kr-sn puani supheli yapar); False: akici kisa cumle ('Evet.' dongusu de suzgeçte tek
# 'Evet.'a iner) -- yalniz sessiz bosluktaki cizelge kurali yakalar, konusma hemen ardindan ayirt edilemez (izlenir).
HAL_CESIT = (
    ("akici", "Bu konuyu daha sonra tekrar konuşuruz.", 2.4, "loopback", False),
    ("kara_liste", "İzlediğiniz için teşekkürler.", 2.4, "loopback", True),
    ("altyazi_mk", "Altyazı M.K.", 2.0, "loopback", True),
    ("dongu", "Evet. Evet. Evet. Evet. Evet. Evet. Evet. Evet.", 2.4, "loopback", False),
    ("krsn_ust", "Bu konuyla ilgili daha önce konuştuğumuz bütün maddeleri tekrar gözden geçirip sonuçları herkese iletelim.",
     2.0, "loopback", True),
    ("krsn_alt", "Sağ olun.", 6.0, "loopback", True),
    ("akici_mikrofon", "Tamam, o zaman bir sonraki konuya geçelim.", 2.4, "mikrofon", False),
)
# Prompt sizintisi: sessiz klip (STT prompt verilmezse bos, verilirse prompt metnini soyler). (ad, sure, akis, zarf araligi)
HAL_SESSIZ = (
    ("sessiz_dusuk", 2.4, "loopback", (0.003, 0.009)),
    ("sessiz_sinir", 2.4, "loopback", (0.017, 0.030)),          # IPUCU_RMS (0.015) uzerinde ama konusma yok
    ("sessiz_dusuk_mikrofon", 2.4, "mikrofon", (0.003, 0.009)),
    ("sessiz_sinir_mikrofon", 2.4, "mikrofon", (0.017, 0.030)),
)


# ======================================================================================================
# zemin gercegi
# ======================================================================================================

def _slotlar(metin, hata=None):
    """Cumle -> [(dogru kelime, whisper kelimesi)]; Whisper'in yanlis duydugu kelimeler eslenir."""
    out = []
    hata = WHISPER_HATA if hata is None else hata
    for w in metin.split():
        kok = w.strip(".,?!")
        son = w[len(kok):]
        y = hata.get(kok.lower())
        out.append((w, (y + son) if y else w))
    return out


class _Uretici:
    """Konusma listesi kurucu: id'ler ve slotlar otomatik."""

    def __init__(self, r, havuz=None, hata=None):
        self.r = r
        self.ut = []
        self.havuz = havuz                   # Faz 2: sablon cumleleri ({z} zaman); None: Faz 1 cumle uretimi
        self.hata = hata

    def ekle(self, kim, bas, sure, metin, sessiz=False):
        u = {"id": len(self.ut), "kim": kim, "bas": bas, "bit": bas + sure, "metin": metin,
             "slot": _slotlar(metin, self.hata), "sessiz": sessiz}
        self.ut.append(u)
        return u

    def cumle(self, tekrar=False, kisa=0.12):
        """Benzersiz cumle (SesServisi tekrar suzgeci gercek: ayni cumle 90 sn icinde ya da 3. kez dusurulur); bazilarinda
        Whisper'in bozdugu bir terim. tekrar: %35 sabit havuzdan (tekrarlanan cumle senaryosu)."""
        r = self.r
        if self.havuz:
            m = r.choice(self.havuz).format(z=r.choice(FAZ2_ZAMAN))
            return r.choice(KISA_YANIT) if r.random() < kisa else m
        if tekrar and r.random() < 0.35:
            m = CUMLELER[r.randrange(len(CUMLELER))]
        else:
            k = r.sample(SOZCUK, r.randint(6, 10))
            if r.random() < 0.25:
                k[r.randrange(len(k))] = r.choice(TERIM_KELIME)
            m = " ".join(k).capitalize() + "."
        if r.random() < kisa:
            m = r.choice(KISA_YANIT)
        return m

    @staticmethod
    def sure(metin, taban=1.6):
        return max(taban, len(metin) / 14.0)


def uret_karisik(r, sure, kisiler, cakisma=0.05, hizli=0.12, tekrar=False, devam=0.12, uzun_sessizlik=0,
                 hizli_aralik=(0.15, 0.5), taban=1.6, kisa=0.12, havuz=None, hata=None, sessizlik=(9.0, 12.0)):
    """Genel toplanti: kisiler sirayla konusur; gecis: cakisma (kismi ust uste), hizli devir (<0.55 sn bosluk: VAD ayni
    klibe koyar), normal bosluk; ayni kisi duraklayip surdurebilir; uzun_sessizlik: her N sozde 9-12 sn sessizlik."""
    g = _Uretici(r, havuz, hata)
    t, son, sayac = 2.0, None, 0
    while t < sure:
        kim = son if (son and r.random() < devam) else r.choice([k for k in kisiler if k != son])
        metin = g.cumle(tekrar, kisa)
        d = g.sure(metin, taban)
        if son is None:
            bas = t
        else:
            onceki = g.ut[-1]
            if kim == son:
                bas = onceki["bit"] + r.uniform(0.8, 2.5)
            else:
                x = r.random()
                if x < cakisma:
                    ust = r.uniform(0.6, max(0.7, 0.6 * min(onceki["bit"] - onceki["bas"], d)))
                    bas = max(onceki["bas"] + 0.5, onceki["bit"] - ust)
                elif x < cakisma + hizli:
                    bas = onceki["bit"] + r.uniform(*hizli_aralik)
                else:
                    bas = onceki["bit"] + r.uniform(0.8, 3.0)
            if uzun_sessizlik and sayac and sayac % uzun_sessizlik == 0:
                bas = onceki["bit"] + r.uniform(*sessizlik)
        u = g.ekle(kim, bas, d, metin)
        t, son, sayac = u["bit"], kim, sayac + 1
    return g.ut


def uret_hizli_devir(r, sure):
    return uret_karisik(r, sure, [A, B, C], cakisma=0.03, hizli=0.45, hizli_aralik=(0.2, 1.0), devam=0.05)


def uret_klip_ici_devir(r, sure):
    """Cogu gecis 0.55 sn'den kisa bosluk: iki konusmaci tek klipte; her parca >= 1.0 sn (yeniden kesilebilir)."""
    return uret_karisik(r, sure, [A, B, C], cakisma=0.02, hizli=0.75, hizli_aralik=(0.1, 0.5), devam=0.0, taban=2.0,
                        kisa=0.0)


def uret_araya_girme(r, sure):
    """A uzun konusur; B 'evet/tamam' diye araya girer (tamamen ust uste: Whisper'da kaybolur) ya da sozunu keser
    (kismi ust uste). Sonra baska biri surdurur."""
    g = _Uretici(r)
    t, onceki = 2.0, None
    while t < sure:
        kim = r.choice([k for k in (A, B, C) if k != onceki])
        metin = g.cumle() + " " + g.cumle(kisa=0.0)
        d = min(max(5.5, g.sure(metin)), 10.5)
        u = g.ekle(kim, t, d, metin)
        diger = [k for k in (A, B, C) if k != kim]
        if r.random() < 0.5:                                 # geri bildirim: sozun ortasinda, tamamen ust uste
            g.ekle(r.choice(diger), u["bas"] + d * r.uniform(0.3, 0.6), 1.0, r.choice(["Evet.", "Tamam.", "Anladım."]))
            t = u["bit"] + r.uniform(0.9, 2.5)
        else:                                                # sozunu kesme: B, A bitmeden 1-1.6 sn once baslar
            metin2 = g.cumle(kisa=0.0)
            b = g.ekle(r.choice(diger), u["bit"] - r.uniform(1.0, 1.6), max(3.0, g.sure(metin2)), metin2)
            t = b["bit"] + r.uniform(0.9, 2.5)
            kim = b["kim"]
        onceki = kim
    return g.ut


def uret_ayni_anda(r, sure):
    """Iki kisi 3-4 sn gercekten es zamanli konusur (Whisper baskin = uzun olani yazar); aralarda sessizlik."""
    g = _Uretici(r)
    t = 2.0
    while t < sure:
        k1, k2 = r.sample([A, B, C], 2)
        m1, m2 = g.cumle(kisa=0.0), g.cumle(kisa=0.0)
        u1 = g.ekle(k1, t, max(4.5, g.sure(m1)), m1)
        g.ekle(k2, u1["bas"] + r.uniform(0.8, 1.6), max(4.0, g.sure(m2)), m2)
        t = max(u["bit"] for u in g.ut[-2:]) + r.uniform(1.5, 4.0)
        if r.random() < 0.4:                                 # arada normal bir soz
            m = g.cumle(kisa=0.0)
            u = g.ekle(r.choice([A, B, C]), t, g.sure(m), m)
            t = u["bit"] + r.uniform(1.0, 3.0)
    return g.ut


def uret_ben_uzak(r, sure):
    """Kullanici (mikrofon) + uzak katilimcilar + oda cihazi ve 'Konuk' etiketleri (kimlik_turu oda / genel); altyazida
    bir kez beliren hayalet ad (toplantida yok, sessizlikte)."""
    ut = uret_karisik(r, sure, [A, B, ODA, KONUK, BEN], cakisma=0.04, hizli=0.12, devam=0.15)
    i = next(i for i, u in enumerate(ut) if u["bit"] > 60)
    for u in ut[i + 1:]:                                     # 4 sn'lik sessizlik ac, hayalet satir oraya
        u["bas"] += 4.0
        u["bit"] += 4.0
    g = _Uretici(r)
    g.ut = ut
    g.ekle(HAYALET, ut[i]["bit"] + 1.4, 1.0, "ee şey tamam", sessiz=True)
    return g.ut


def uret_yanki(r, sure):
    """Kullanici uzak sozle ust uste konusmaz (>= 1.6 sn). Hoparlor: mikrofon her uzak sozun yankisini de alir."""
    g = _Uretici(r)
    t, son = 2.0, None
    while t < sure:
        kim = BEN if (son != BEN and r.random() < 0.3) else r.choice([k for k in (A, B, C) if k != son])
        metin = g.cumle()
        if son is None:
            bas = t
        elif kim == BEN or son == BEN:
            bas = g.ut[-1]["bit"] + r.uniform(1.6, 3.0)
        else:
            bas = g.ut[-1]["bit"] + r.uniform(0.8, 3.0)
        u = g.ekle(kim, bas, g.sure(metin), metin)
        t, son = u["bit"], kim
    return g.ut


def uret(tur, r, sec):
    sure = sec.get("sure", SURELER[tur])
    if tur == "karisik":
        hal = sec.get("halusinasyon")
        faz2 = sec.get("cumleler") == "faz2"
        return uret_karisik(r, sure, [A, B, C, BEN], cakisma=sec.get("cakisma", 0.05), hizli=sec.get("hizli", 0.12),
                            kisa=sec.get("kisa", 0.12), tekrar=sec.get("tekrar", False),
                            uzun_sessizlik=(6 if hal in ("cesit", "sessiz") else 18) if hal else 0,
                            havuz=FAZ2_SABLON if faz2 else None, hata=WHISPER_HATA_FAZ2 if faz2 else None,
                            sessizlik=(4.5, 12.0) if hal in ("cesit", "sessiz") else (9.0, 12.0))
    return {"hizli_devir": uret_hizli_devir, "klip_ici_devir": uret_klip_ici_devir, "araya_girme": uret_araya_girme,
            "ayni_anda": uret_ayni_anda, "ben_uzak": uret_ben_uzak, "yanki": uret_yanki}[tur](r, sure)


# ======================================================================================================
# Teams paneli (C2)
# ======================================================================================================

class Panel:
    """Zemin gercegindeki her soz icin altyazi satiri: gecikme (lag +-0.4), 0.8 sn'lik parcalarla kelime artisi,
    bazi sozlerde 1 sn'lik geri alma (kelime sayisi -1) ve kalici yeniden yazim (kelimeler degisir, sayi ayni);
    ayni kisinin kisa duraklamayla suren sozu tek satir. Panel son PANEL_SATIR satiri gosterir. Kapali pencerede
    (altyazi kapali) okuma `grup=False`; pencere bitince panel bostan baslar."""

    def __init__(self, ut, lag, kapali, r):
        self.kapali = sorted(kapali)
        self._onbellek = {}
        for u in ut:
            n = len(u["slot"])
            s = u["bas"] + max(0.0, lag + r.uniform(-0.4, 0.4))
            e = max(s + 0.6, u["bit"] + (s - u["bas"]))
            rev1 = rev2 = None
            if n >= 5 and r.random() < 0.2:
                a = s + (e - s) * r.uniform(0.3, 0.8)
                rev1 = (a, a + 1.0)
            if n >= 5 and r.random() < 0.2:
                a = s + (e - s) * r.uniform(0.5, 1.0)
                rev2 = (a, set(r.sample(range(n), max(1, int(0.3 * n)))))
            u["alt"] = {"s": s, "e": e, "n": n, "rev1": rev1, "rev2": rev2}
        gruplar = []
        for u in sorted(ut, key=lambda x: x["bas"]):         # ayni kisinin duraksamali devami tek satir
            if gruplar and gruplar[-1]["kim"] == u["kim"] and u["bas"] - gruplar[-1]["son"] < 2.5:
                gruplar[-1]["uts"].append(u)
                gruplar[-1]["son"] = max(gruplar[-1]["son"], u["bit"])
            else:
                gruplar.append({"kim": u["kim"], "uts": [u], "son": u["bit"]})
        for g in gruplar:
            g["ilk"] = min(u["alt"]["s"] for u in g["uts"])
        gruplar.sort(key=lambda g: g["ilk"])
        self.gruplar = gruplar
        self._ilkler = [g["ilk"] for g in gruplar]

    @staticmethod
    def _n(u, t):
        a = u["alt"]
        if t < a["s"]:
            return 0
        dur = max(a["e"] - a["s"], 0.5)
        k = math.floor((t - a["s"]) / KELIME_PERIYODU) + 1
        n = max(1, int(round(a["n"] * min(1.0, k * KELIME_PERIYODU / dur))))
        if a["rev1"] and a["rev1"][0] <= t < a["rev1"][1] and n >= 3:
            n -= 1
        return n

    def jetonlar(self, u, t):
        n = self._n(u, t)
        if n <= 0:
            return []
        a = u["alt"]
        # kapali pencere bitince panel bostan baslar: pencere sonrasi yalniz sonradan uretilen kelimeler gorunur
        taban = 0
        for w0, w1 in self.kapali:
            if w1 <= t and a["s"] < w1:
                taban = max(taban, self._n(u, w1) if a["e"] > w1 else a["n"])
        r2 = a["rev2"]
        yazildi = bool(r2) and r2[0] <= t
        anahtar = (u["id"], n, yazildi, taban)               # ayni durum ayni jetonlari verir (duzenek hizi)
        tok = self._onbellek.get(anahtar)
        if tok is None:
            tok = [f"cap{u['id']:03d}{'r' if yazildi and i in r2[1] else 'k'}{i:02d}" for i in range(taban, n)]
            self._onbellek[anahtar] = tok
        return tok

    def kapali_mi(self, t):
        return any(w0 <= t < w1 for w0, w1 in self.kapali)

    def gorunen(self, t):
        """t anindaki panel: [(kim, metin)] (eski -> yeni)."""
        i = bisect.bisect_right(self._ilkler, t)
        cikti = []
        while i > 0 and len(cikti) < PANEL_SATIR:
            i -= 1
            g = self.gruplar[i]
            tok = []
            for u in g["uts"]:
                tok += self.jetonlar(u, t)
            if tok:
                cikti.append((g["kim"], " ".join(tok)))
        cikti.reverse()
        return cikti


# ======================================================================================================
# klipler (C1)
# ======================================================================================================

def _kayip_belirle(utler):
    """Ust uste binen sozlerde Whisper baskin (uzun) sesi yazar: ortusme kisa olanin yarisini asarsa kisa olan kaybolur."""
    kayip = set()
    for i, u in enumerate(utler):
        for v in utler[i + 1:]:
            ov = min(u["bit"], v["bit"]) - max(u["bas"], v["bas"])
            if ov <= 0:
                continue
            kisa = u if (u["bit"] - u["bas"]) <= (v["bit"] - v["bas"]) else v
            if ov >= 0.5 * (kisa["bit"] - kisa["bas"]):
                kayip.add(kisa["id"])
    return kayip


def vad_araliklari(sozler):
    """Tek akisin sesli bolgeleri -> klip araliklari [(bas, bit)]: < CUMLE_SESSIZLIK bosluk ayni klip; 8 sn'yi asan klip
    ilk nefes payinda (>= 0.25 sn) kapanir; 12 sn'yi asan sesli bolge ZORLA_SN'de (sozun ortasinda da) kesilir."""
    ar, cur = [], None
    for u in sorted(sozler, key=lambda x: x["bas"]):
        if cur is not None:
            bosluk = u["bas"] - cur[1]
            if bosluk < ses.CUMLE_SESSIZLIK and not (cur[1] - cur[0] >= ses.TAVAN_SN and bosluk >= ses.NEFES_SESSIZLIK):
                cur[1] = max(cur[1], u["bit"])
                continue
        cur = [u["bas"], u["bit"]]
        ar.append(cur)
    out = []
    for bas, bit in ar:
        while bit - bas > ses.ZORLA_SN:
            out.append((bas, bas + ses.ZORLA_SN))
            bas += ses.ZORLA_SN
        out.append((bas, bit))
    return out


def zarf_uret(c, r):
    """Klibin 100 ms'lik enerji zarfi (RMS): sesli blok yuksek, aradaki bosluk dusuk, konusma ici kisa dalmalar
    bosluk gurultusunun uzerinde. Yeniden kesme bunun uzerinde en dusuk blogu arar."""
    n = max(1, int(round((c["bit"] - c["bas"]) / BLOK)))
    olcek = 0.35 if c["yanki"] else 1.0
    zarf = []
    for k in range(n):
        orta = c["bas"] + BLOK * (k + 0.5)
        if any(s["bas"] <= orta < s["bit"] for s in c["sozler"]):
            e = r.uniform(0.012, 0.03) if r.random() < 0.03 else r.uniform(0.03, 0.12)
            zarf.append(round(e * olcek, 5))
        else:
            zarf.append(round(r.uniform(0.0015, 0.008), 5))
    return zarf


def klip_kur(aralik, sozler, akis, r, yanki=False, gecikme=0.0, hayalet=None):
    """Bir VAD araligindan klip kaydi. aralik: (bas, bit); sozler: akisin zemin gercegi sozleri (araligi kesenler
    klibe girer, kelimeler zamanina gore dagilir). yanki: kopya, gecikmeli, bozuk. hayalet: sessizlikte uydurulan
    metin (bas, bit, metin, uid)."""
    if hayalet:
        bas, bit, metin, uid = hayalet
        kel = [(uid, i, w, w, "?", bas + (i + 0.5) * (bit - bas) / max(1, len(metin.split())))
               for i, w in enumerate(metin.split())]
        c = {"akis": akis, "bas": math.floor(bas / BLOK) * BLOK, "bit": math.ceil(bit / BLOK) * BLOK, "sozler": [],
             "kayip": set(), "kelimeler": kel, "yanki": False, "hayalet": True, "ust": None}
        c["zarf"] = [round(r.uniform(0.004, 0.02), 5) for _ in range(max(1, int(round((c["bit"] - c["bas"]) / BLOK))))]
        return c
    bas = math.floor((aralik[0] + gecikme) / BLOK) * BLOK
    bit = math.ceil((aralik[1] + gecikme) / BLOK) * BLOK
    kopya = [dict(u, bas=u["bas"] + gecikme, bit=u["bit"] + gecikme) for u in sozler
             if u["bit"] + gecikme > bas and u["bas"] + gecikme < bit]
    kayip = _kayip_belirle(kopya)
    kel = []
    for u in kopya:
        if u["id"] in kayip:
            continue
        n = len(u["slot"])
        for i, (dogru, whisper) in enumerate(u["slot"]):
            t = u["bas"] + (i + 0.5) * (u["bit"] - u["bas"]) / n
            if not (aralik[0] + gecikme <= t < aralik[1] + gecikme):      # kelime araliga zamanina gore dagilir
                continue
            if yanki and r.random() < 0.12:
                whisper = whisper[:-1] or whisper          # yanki bozuk: Whisper daha kotu duyar
            kel.append((u["id"], i, dogru, whisper, u["kim"], t))
    kel.sort(key=lambda x: x[5])
    c = {"akis": akis, "bas": bas, "bit": bit, "sozler": kopya, "kayip": kayip, "kelimeler": kel, "yanki": yanki,
         "hayalet": False, "ust": None}
    c["zarf"] = zarf_uret(c, r)
    return c


def klip_metni(c):
    return " ".join(k[3] for k in c["kelimeler"])


# ======================================================================================================
# kosum: olay yigini
# ======================================================================================================

class SahteStt:
    """SttIstemci yerine: coz(klip_kaydi, ipucu) -> segmentler. Klibin zemin gercegi metni (Whisper hatalari dahil) doner.
    Sessiz klipte ('sizinti' isaretli) Whisper'in davranisi modellenir (plan §10 deneme sonuclari): prompt verilmezse bos,
    verilirse prompt metnini soyler. Gonderilen prompt'lar `cagrilar`da (rms, uzunluk, sizinti) tutulur."""

    def __init__(self):
        self.cagrilar = []
        self.ornek_ipucu = ""                # gorulen ilk prompt (icerik / uzunluk kapilari)

    def coz(self, ses_, ipucu=""):
        c = ses_
        if ipucu and not self.ornek_ipucu and not c.get("sizinti"):
            self.ornek_ipucu = ipucu
        rms = sum(c["zarf"]) / max(1, len(c["zarf"]))
        self.cagrilar.append({"rms": rms, "ipucu_kr": len(ipucu or ""), "sizinti": bool(c.get("sizinti")),
                              "hayalet": bool(c["hayalet"])})
        metin = (ipucu or "") if c.get("sizinti") else klip_metni(c)
        return [{"id": 0, "text": metin, "tokens": [], "temperature": 0.0}]


class Kosum:
    """Tek senaryo kosumu. `alici`: zaman(an), okuma(ev), klip(ev), kontrol(), stt_durum(d) metotlari olan nesne.
    yeniden_kes(klip_no, an_epoch) -> bool bu nesnede (SesServisi.yeniden_kes sozlesmesi)."""

    def __init__(self, ut, sec, seed, alici):
        self.sec = sec
        self.r = random.Random(seed * 1000 + 1)
        self.ut = ut
        self.alici = alici
        self.saat = 0.0
        self.yigin = []
        self._sira = itertools.count()
        self._no = 0
        self.klipler = {}                    # klip_no -> klip kaydi
        self.teslim = set()                  # alicinin gordugu klip_no'lar
        self.bosluklar = []                  # STT'nin hic cozmedigi araliklar (zemin gercegi) [bas, bit] (goreli sn)
        self.kes_istek = self.kes_tamam = 0
        self.okuma_ornekleri = []            # panelin ornekleme anlari (goreli sn)
        self.lag = sec.get("gecikme", 2.0)
        self.ben = sec.get("ben", BEN)                       # mikrofon sahibi (zemin gercegindeki ad)
        self.kapali = list(sec.get("kapali", ()))
        self.takilma = list(sec.get("takilma", ()))
        self.kesinti = list(sec.get("stt_kesinti", ()))
        self.panel = Panel(ut, self.lag, self.kapali, self.r)
        self.yakalayici = yakalayici.Yakalayici()
        self.adim = bool(sec.get("saat_adimlari"))             # saat adimi modu: damgalar gercek kod yollarindan
        self._akislar = {}
        self.servis = ses.SesServisi("", olay_fn=self._servis_olayi, esikler=sec.get("esikler"))
        self.stt = SahteStt()                                        # SesServisi._coz gercek: prompt korumalari dahil
        self.servis.stt = self.stt
        if os.environ.get("BM_E2E_TEKRAR") == "kapali":              # tani: tekrar suzgecini kapat (varsayilan: gercek)
            self.servis._tekrar_mi = lambda *a, **k: False
        self._yayim_giris = {}                                       # klip_no -> suzgecten once metin
        self._yay0 = self.servis._olay_yay
        self.servis._olay_yay = self._olay_yay_izle
        self._kesinti_kuyrugu = {}           # pencere indeksi -> [klip]
        self._kur()
        if self.adim:
            self._adim_kur()

    # --- kurulum ---
    def at(self, t, tur, veri=None):
        heapq.heappush(self.yigin, (t, next(self._sira), tur, veri))

    def _kur(self):
        r = self.r
        uzak = [u for u in self.ut if u["kim"] != self.ben and not u["sessiz"]]
        ben = [u for u in self.ut if u["kim"] == self.ben]
        lb = [klip_kur(a, uzak, "loopback", r) for a in vad_araliklari(uzak)]
        mk = [klip_kur(a, ben, "mikrofon", r) for a in vad_araliklari(ben)]
        if self.sec.get("tur") == "yanki":                   # hoparlor: mikrofon uzak sozun yankisini de alir
            for a in vad_araliklari(uzak):
                c = klip_kur(a, uzak, "mikrofon", r, yanki=True, gecikme=r.uniform(0.08, 0.3))
                if r.random() < 0.85:                        # Whisper dusuk seviyeli yankidan bazen metin cikaramaz
                    mk.append(c)
        klipler = lb + mk
        if self.sec.get("halusinasyon"):
            klipler += self._halusinasyon(uzak + ben)
        for c in klipler:
            if c["bit"] - c["bas"] >= SES_ESIK_KISA:
                self.at(c["bit"] + VAD_KAPANIS, "kapandi", c)
        son = max(u["bit"] for u in self.ut) + 40
        t = r.uniform(0.2, 0.9)                              # okumalar: R ~1.2 sn (okuma + uyku), jitter
        while t < son:
            if any(a <= t < b for a, b in self.takilma):
                t = max(b for a, b in self.takilma if a <= t < b) + r.uniform(0.0, 1.0)
                continue
            d = r.uniform(1.0, 2.0) if r.random() < self.sec.get("yavas_okuma", 0.02) else r.uniform(0.1, 0.4)   # %2 yavas okuma
            self.at(t + d, "okuma", (t, d))
            t += d + r.uniform(0.7, 1.1)                     # tek okuyucu: bir sonraki okuma oncekinin ardindan
        kul = self.sec.get("kulaklik", True)                 # kulaklik pasif tespiti ~35 sn sonra
        if kul in (True, False):
            self.at(35.0, "kulaklik", {"tur": "kulaklik", "durum": kul, "guven": 0.8})
        for i in range(int(son) + 1):
            self.at(float(i), "tik")
        self.bitis = son

    def _halusinasyon_cesit(self, sozler, mod):
        """Faz 2 halusinasyon cesitleri. mod 'cesit': HAL_CESIT turleri dairesel; >= 8 sn bosluk -> ortada ('uzak'), 4-8 sn
        bosluk -> sozun 1.2 sn ardinda ('yakin', <= 3 sn'lik turler). Metni kendi basina ayirt edilemeyen turler (icerik_yeterli
        False) 'yakin'da izlenir ('hal_belirsiz'), digerleri kapidir. mod 'sessiz': HAL_SESSIZ turleri (STT prompt'u soyler)."""
        r = self.r
        liste = sorted(sozler, key=lambda x: x["bas"])
        cikti, uid, i_tur = [], -1, 0
        for i in range(len(liste) - 1):
            bosluk = liste[i + 1]["bas"] - liste[i]["bit"]
            uzak = bosluk >= 8.0
            if not uzak and not (mod == "cesit" and bosluk >= 4.0):
                continue
            if mod == "cesit":
                ad, metin, sure, akis, yeterli = HAL_CESIT[i_tur % len(HAL_CESIT)]
                if not uzak and sure > 3.0:
                    i_tur += 1
                    ad, metin, sure, akis, yeterli = HAL_CESIT[i_tur % len(HAL_CESIT)]
                zarf_ara = None
            else:
                ad, sure, akis, zarf_ara = HAL_SESSIZ[i_tur % len(HAL_SESSIZ)]
                metin, yeterli = "ee", True
            i_tur += 1
            bas = liste[i]["bit"] + (bosluk / 2 - sure / 2 if uzak else 1.2)
            c = klip_kur(None, None, akis, r, hayalet=(bas, bas + sure, metin, uid))
            c.update(yakin=not uzak, hal_tur=ad, hal_belirsiz=(not uzak) and not yeterli, sizinti=mod == "sessiz")
            if zarf_ara:
                c["zarf"] = [round(r.uniform(*zarf_ara), 5) for _ in c["zarf"]]
            uid -= 1
            cikti.append(c)
        return cikti

    def _halusinasyon(self, sozler):
        """Sessizlikte uydurulan klipler: >= 8 sn sessiz bosluklarin ortasinda ('uzak'; kesin ad verilmemeli) ve en
        fazla iki tane sozun hemen ardinda ('yakin'; yalniz izlenir, zaman tek basina ayiramaz)."""
        if self.sec.get("halusinasyon") in ("cesit", "sessiz"):
            return self._halusinasyon_cesit(sozler, self.sec["halusinasyon"])
        r = self.r
        liste = sorted(sozler, key=lambda x: x["bas"])
        cikti, yakin, uid = [], 0, -1
        for i in range(len(liste) - 1):
            bosluk = liste[i + 1]["bas"] - liste[i]["bit"]
            if bosluk >= 8.0:
                bas = liste[i]["bit"] + bosluk / 2 - 1.2
                c = klip_kur(None, None, "loopback", r, hayalet=(bas, bas + 2.4, r.choice(HALUSINASYON_METNI), uid))
                c["yakin"] = False
                c["hal_belirsiz"] = False
            elif bosluk >= 4.0 and yakin < 2 and liste[i]["kim"] != self.ben and r.random() < 0.5:
                bas = liste[i]["bit"] + 1.2
                c = klip_kur(None, None, "loopback", r, hayalet=(bas, bas + 2.0, r.choice(HALUSINASYON_METNI), uid))
                c["yakin"] = True
                c["hal_belirsiz"] = True
                yakin += 1
            else:
                continue
            uid -= 1
            cikti.append(c)
        return cikti

    # --- yardimcilar ---
    def _stt_suresi(self, c):
        s = 0.5 + 0.12 * (c["bit"] - c["bas"]) + self.r.uniform(0.0, 0.8)
        if self.r.random() < 0.05:
            s += self.r.uniform(2.0, 5.0)                     # arada yavas istek (sira takilmasi)
        return s

    # --- saat adimi modu: ses sayaci ankrajlari ve altyazi okumasi GERCEK kod yollarindan (ses.AkisYakalayici,
    #     yakalayici.Yakalayici.oku), sanal zaman saat.simdi() uzerinden; duvar saati adimli ---
    def _adim_kur(self):
        """Her akis icin gercek AkisYakalayici zaman tabani: ilk blok t=BLOK'ta gelir, sifirinci ornek T0'dadir."""
        self.alici.zaman(T0 + BLOK)
        for ad in ("loopback", "mikrofon"):
            ak = ses.AkisYakalayici(ad, queue.Queue(), loopback=(ad == "loopback"), olay=self.servis._akis_olayi)
            ak._capa_kur(int(round(BLOK * ses.ORNEK)))
            ak.toplam_ornek = int(round(BLOK * ses.ORNEK))
            self._akislar[ad] = ak
        self.alici.zaman(T0)
        self.yakalayici.okuma_fn = self.alici.okuma
        yakalayici._grup_bul = self._grup_bul_taslagi

    def _grup_bul_taslagi(self):
        self.alici.zaman(self._okuma_bit)                    # panel okumasi d sn surer
        return (object() if self._grup_var else None), ""

    def _akis_esitle(self, t):
        """Akis sayaclari sanal zamana yetisir (blok dongusu: her blokta toplam_ornek + _capa_kontrol)."""
        for ak in self._akislar.values():
            ak.toplam_ornek = int(round(t * ses.ORNEK))
            ak._capa_kontrol()

    def _p(self, c):
        bas_e, bit_e = T0 + c["bas"], T0 + c["bit"]
        if self.adim:                                        # epoch = akisin ornek sayaci + o anki ankraj
            ak = self._akislar[c["akis"]]
            bas_e = ak.ornek_epoch(int(round(c["bas"] * ses.ORNEK)))
            bit_e = ak.ornek_epoch(int(round(c["bit"] * ses.ORNEK)))
        return {"kaynak": c["akis"], "klip_no": c["klip_no"], "no": c["no"], "bas_epoch": bas_e,
                "bit_epoch": bit_e, "rms": sum(c["zarf"]) / max(1, len(c["zarf"])), "zarf": list(c["zarf"]),
                "ust_klip": c["ust"], "ses": c}                      # 'ses': SahteStt klip kaydini alir

    def _olay_yay_izle(self, ev):
        """SesServisi._olay_yay'i izler. Yeniden kesme parcalari (ust_klip) ses.py'de zaten tekrar suzgecinden muaf
        (bkz. test_e2e.test_o_...); sec['cocuk_tekrar_muaf'] (varsayilan acik) ayrica _tekrar_mi'yi kapatir. Digerleri
        gercek suzgecten gecer."""
        if ev.get("tur") == "klip":
            self._yayim_giris[ev["klip_no"]] = ev["text"]
        if ev.get("ust_klip") is not None and self.sec.get("cocuk_tekrar_muaf", True):
            gercek = self.servis._tekrar_mi
            self.servis._tekrar_mi = lambda *a, **k: False
            try:
                self._yay0(ev)
            finally:
                self.servis._tekrar_mi = gercek
        else:
            self._yay0(ev)

    def _servis_olayi(self, ev):
        if ev.get("tur") == "klip" and not ev.get("text"):
            self.alici.klip(ev)                                      # metinsiz (suzgecte dusen) loopback klibi:
            return                                                   # satir olusmaz, teslim sayilmaz
        if ev.get("tur") == "klip":
            self.teslim.add(ev["klip_no"])
            c = self.klipler.get(ev["klip_no"])
            if c is not None and ev["text"] != self._yayim_giris.get(ev["klip_no"], ev["text"]):
                c["suzgec_kismi"] = True                             # bazi cumleler tekrar suzgecinde dustu
        self.alici.klip(ev)

    def suzgecle_dusen(self):
        """Tekrar suzgecinin (SesServisi._tekrar_mi) tamamen ya da kismen dusurdugu kliplerin soz id'leri."""
        out = set()
        for no, c in self.klipler.items():
            if (no in self._yayim_giris and no not in self.teslim) or c.get("suzgec_kismi"):
                out |= {u["id"] for u in c["sozler"]}
        return out

    def _kesinti_penceresi(self, t):
        for i, (a, b, tur) in enumerate(self.kesinti):
            if a <= t < b:
                return i, a, b, tur
        return None

    def bosluklar_birlesik(self):
        out = []
        for a, b in sorted(self.bosluklar):
            if out and a - out[-1][1] < 30:
                out[-1][1] = max(out[-1][1], b)
            else:
                out.append([a, b])
        return out

    def _stt_durumu(self, t):
        w = self._kesinti_penceresi(t)
        return {"durum": "kesinti" if w else "ok", "bas": (T0 + w[1]) if w else None, "bekleyen": 0, "kayit": True,
                "eksik": [[T0 + a, T0 + b] for a, b in self.bosluklar_birlesik()], "hata": None}

    # --- olay isleme ---
    def calistir(self):
        while self.yigin:
            t, _, tur, v = heapq.heappop(self.yigin)
            if t > self.bitis:
                break
            self.saat = t
            self.alici.zaman(T0 + t)
            if self.adim:
                self._akis_esitle(t)
            getattr(self, "_o_" + tur)(t, v)
            if self.kesinti and tur in ("tik", "kapandi"):
                self.alici.stt_durum(self._stt_durumu(t))
            self.alici.kontrol()
        self.alici.zaman(T0 + self.bitis)

    def _o_tik(self, t, v):
        pass

    def _o_kulaklik(self, t, ev):
        self.servis._olay_ver(dict(ev))

    def _o_okuma(self, t, v):
        bas, d = v
        orta = bas + d / 2.0
        self.okuma_ornekleri.append(orta)
        grup = not self.panel.kapali_mi(orta)
        gorunen = self.panel.gorunen(orta) if grup else []
        if self.adim:                                        # gercek Yakalayici.oku(): bas/bit saat.simdi() ile damgalanir
            y = self.yakalayici
            y.grup = object() if grup else None
            self._grup_var, self._okuma_bit = grup, T0 + bas + d
            y._ciftler = lambda: (self.alici.zaman(self._okuma_bit), list(gorunen))[1]
            self.alici.zaman(T0 + bas)
            y.oku()                                          # okuma_fn -> alici.okuma
            self.alici.zaman(T0 + t)
            return
        self.alici.okuma(self.yakalayici._okuma_yayinla(T0 + bas, T0 + bas + d, grup, gorunen))

    def _yeni_klip_no(self, c):
        c["no"] = self._no
        c["klip_no"] = self._no + 1
        self._no += 1
        self.klipler[c["klip_no"]] = c

    def _o_kapandi(self, t, c):
        self._yeni_klip_no(c)
        w = self._kesinti_penceresi(t)
        if w is None:
            self.at(t + self._stt_suresi(c), "stt", c)
            return
        i, a, b, tur = w
        c["stt_yok"] = True
        self.servis._sirali_yay(c["no"], self.servis._bosluk_olaylari(self._p(c), "stt_yok"))
        if tur == "diskte":                                  # diske yazilir, STT donunce sirayla cozulur
            self._kesinti_kuyrugu.setdefault(i, []).append(c)
            if len(self._kesinti_kuyrugu[i]) == 1:
                self.at(b + 1.0, "gec", i)
        else:
            self.bosluklar.append((c["bas"], c["bit"]))

    def _o_gec(self, t, i):
        kuyruk = self._kesinti_kuyrugu.get(i) or []
        if not kuyruk:
            return
        c = kuyruk.pop(0)
        c["stt_yok"] = False
        p = self._p(c)
        ev = self.servis.klip_olayi_uret(p, self.servis._coz(p))
        with self.servis._kilit:
            self.servis._yay([ev] if ev else [])
        if kuyruk:
            self.at(t + 0.5 + 0.1 * (c["bit"] - c["bas"]), "gec", i)

    def _o_stt(self, t, c):
        p = self._p(c)
        ev = self.servis.klip_olayi_uret(p, self.servis._coz(p))   # gercek _coz: Whisper prompt'u (korumali) -> SahteStt
        self.servis._sirali_yay(c["no"], [ev] if ev else [])

    # --- yeniden kesme (SesServisi.yeniden_kes sozlesmesi) ---
    def yeniden_kes(self, klip_no, an):
        """Klibi an +- KES_ARALIK_SN icindeki en dusuk enerjili 100 ms blokta boler; bir parca KES_MIN_PARCA_SN'den
        kisaysa, blok sessiz degilse (ses.kesim_sessiz_mi) ya da klip bilinmiyor / zaten kesilmisse False. True ise iki
        cocuk klip STT'ye gider (ust_klip)."""
        c = self.klipler.get(klip_no)
        self.kes_istek += 1
        if c is None or c.get("kesildi"):
            return False
        an_g = an - T0
        if self.adim:                                        # epoch -> akis zamani (ankraj gecmisiyle)
            an_g = self._akislar[c["akis"]].epoch_ornek(an) / ses.ORNEK
        n = len(c["zarf"])
        k0 = int(math.ceil((max(c["bas"], an_g - ses.KES_ARALIK_SN) - c["bas"]) / BLOK))
        k1 = int((min(c["bit"], an_g + ses.KES_ARALIK_SN) - c["bas"]) // BLOK)
        aday = [(round(c["zarf"][k], 6), abs(c["bas"] + BLOK * (k + 0.5) - an_g), c["bas"] + BLOK * (k + 0.5), k)
                for k in range(max(0, k0), min(n, k1))]
        if not aday:
            return False
        e, _, kes, kk = min(aday)
        if kes - c["bas"] < ses.KES_MIN_PARCA_SN or c["bit"] - kes < ses.KES_MIN_PARCA_SN:
            return False
        if not ses.kesim_sessiz_mi(e, c["zarf"]):
            return False                                     # sessiz blok yok (ust uste binme): "A -> B"
        c["kesildi"] = True
        self.kes_tamam += 1
        for yar in (0, 1):
            ps = {"akis": c["akis"], "bas": c["bas"] if yar == 0 else kes, "bit": kes if yar == 0 else c["bit"],
                  "sozler": c["sozler"], "kayip": c["kayip"], "yanki": c["yanki"], "hayalet": c["hayalet"],
                  "ust": klip_no, "kelimeler": [k for k in c["kelimeler"] if (k[5] < kes) == (yar == 0)],
                  "zarf": (c["zarf"][:kk + 1] if yar == 0 else c["zarf"][kk + 1:]) or [0.01]}
            self._yeni_klip_no(ps)
            self.at(self.saat + self._stt_suresi(ps), "stt", ps)
        return True

    # --- zemin gercegi sorgulari ---
    def kapsamasiz(self, c, pay=1.0):
        """Klibin altyazi zamani penceresi tamamen kapali / takilma penceresinde mi? (kesin '?' beklenir)"""
        a, b = c["bas"] + self.lag, c["bit"] + self.lag
        return any(a >= w0 + pay and b <= w1 for w0, w1 in self.kapali + self.takilma)

    def kapsamali(self, c):
        """Klibin altyazi zamani hic kapali / takilma penceresine degmiyor mu (genis pay)?"""
        a, b = c["bas"] + self.lag - 1.0, c["bit"] + self.lag + 3.0
        return not any(a < w1 and b > w0 for w0, w1 in self.kapali + self.takilma)

    def L_gercek(self):
        """Idealize L: >= 1 sn sessizligin ardindan baslayan uzak sozun, klip basi ile panelde ilk goruldugu okuma
        ornegi arasindaki fark (medyan). Konusmaci'nin capa tanimi (MIMARI_v3 §3 madde 5a) ile ayni olcu."""
        uzak = sorted([u for u in self.ut if u["kim"] != self.ben and not u["sessiz"]], key=lambda x: x["bas"])
        farklar, son_bit, ornek = [], -1e9, self.okuma_ornekleri
        for u in uzak:
            ilk = u["bas"] - son_bit >= 1.0
            son_bit = max(son_bit, u["bit"])
            if not ilk or any(w0 - 3 <= u["bas"] + self.lag <= w1 + 3 for w0, w1 in self.kapali + self.takilma):
                continue
            for t in ornek[bisect.bisect_left(ornek, u["alt"]["s"]):]:
                if t > u["alt"]["s"] + 8:
                    break
                if self.panel.jetonlar(u, t):
                    farklar.append(t - u["bas"])
                    break
        farklar.sort()
        return farklar[len(farklar) // 2] if farklar else None


# ======================================================================================================
# alicilar
# ======================================================================================================

class SahteSaat:
    """saat.simdi yerine: time() sanal saat, gerisi gercek modul."""

    def __init__(self):
        self.t = T0

    def time(self):
        return self.t

    def __getattr__(self, ad):
        return getattr(gercek_time, ad)


class AdimliSaat:
    """Saat adimi duzenegi: sanal zaman (SahteSaat.t) 'monotonik' saattir; duvar saati ayrica adimlarla sicrar
    (adimlar: [(goreli sn, fark)]). saat.py'nin kendi `time` basvurusu bu ikisiyle degistirilir, saat.simdi()
    gercek koddur. time.time global olarak duvar saatine baglanir: dogrudan time.time() kullanan kod adimi gorur
    (gerileme yakalanir). eski: simdi() = duvar saati (adim oncesi davranis, negatif kontrol)."""

    def __init__(self, saat, adimlar, eski=False):
        self.saat, self.adimlar, self.eski = saat, sorted(adimlar), eski

    def ofset(self, goreli):
        return sum(f for a, f in self.adimlar if goreli >= a)

    def duvar(self):
        return self.saat.t + self.ofset(self.saat.t - T0)

    def mono(self):
        return self.saat.t - T0 + 1000.0

    def kur(self):
        self.saat.t = T0
        gercek_time.time = self.duvar
        saat_mod.time = types.SimpleNamespace(time=self.duvar, monotonic=self.mono)
        saat_mod.sifirla()
        if self.eski:
            saat_mod.simdi = self.duvar


class MotorAlici:
    def __init__(self, saat):
        self.m, self.saat = None, saat
        self.olaylar = []                    # (an, tip, id, durum)
        self.silinme = collections.Counter()  # satir_sil olaylarinin nedeni (yanki / halusinasyon ...)

    def zaman(self, an):
        self.saat.t = an

    def okuma(self, ev):
        self.m.okuma_ekle(ev)

    def klip(self, ev):
        self.m.ses_olayi(ev)

    def kontrol(self):
        self.m.kontrol()

    def stt_durum(self, d):
        self.m.stt_durumu_ayarla(d)

    def olay(self, tip, veri):
        if tip in ("satir", "satir_guncelle", "satir_sil", "satir_degistir"):
            self.olaylar.append((self.saat.t, tip, veri.get("id", veri.get("eski_id")), veri.get("durum")))
        if tip == "satir_sil":
            self.silinme[veri.get("neden")] += 1


class KonusmaciAlici:
    """Motor'suz: Konusmaci'yi dogrudan surer (oracle). Kararlar klip_no -> karar; `bol` -> sahte yeniden kesme."""

    def __init__(self, kon):
        self.kon = kon
        self.k = None
        self.karar = {}
        self.atilan = set()
        self.t = T0

    def zaman(self, an):
        self.t = an

    def okuma(self, ev):
        self.kon.okuma_ekle(ev)

    def klip(self, ev):
        if ev.get("tur") == "kulaklik":
            self.kon.kulaklik_ayarla(ev.get("durum"))
        elif ev.get("tur") == "klip":
            self.kon.klip_ekle(ev)

    def stt_durum(self, d):
        pass

    def kontrol(self):
        self._isle(self.kon.kontrol(self.t))

    def _isle(self, kararlar):
        for kr in kararlar:
            no = kr["klip_no"]
            if kr.get("bol") is not None and not kr.get("at"):
                if self.k.yeniden_kes(no, kr["bol"]):
                    self.karar.pop(no, None)
                    continue
                kr = self.kon.bolunemedi(no)
            if kr.get("at"):
                self.atilan.add(no)
                self.karar.pop(no, None)
                continue
            self.karar[no] = kr

    def bitir(self):
        self._isle(self.kon.bitir())


# ======================================================================================================
# degerlendirme
# ======================================================================================================

def _sinif(d):
    """Karar -> kesin | adayli ('Ad?') | devir ('A → B') | ayni ('A / B') | soru ('?')."""
    ad = (d.get("speaker") or "?").strip()
    if ad == "?" or not ad:
        return "soru"
    if " / " in ad:
        return "ayni"
    if " → " in ad:
        return "devir"
    g = d.get("spk_guven")
    return "kesin" if isinstance(g, (int, float)) and g >= 0.7 else "adayli"


def _ozet_klip(c):
    return {"klip_no": c["klip_no"], "ust": c["ust"], "akis": c["akis"], "bas": round(c["bas"], 1),
            "bit": round(c["bit"], 1), "sahipler": dict(collections.Counter(k[4] for k in c["kelimeler"])),
            "metin": klip_metni(c)[:48]}


def degerlendir(k, karar):
    """k: Kosum; karar: {klip_no: {"speaker","spk_guven","cakisma","devir",...}} (nihai, satiri olan klipler).
    Dondurur (sayaclar, ayrinti listesi, cift soz id'leri, eksik soz id'leri). 'kesin' = spk_guven >= 0.7 ve tek isim;
    kesin adin klip kelimelerindeki payi: >= %80 dogru, %50-80 kirli (metnin >= %20'si baskasinin), < %50 yanlis."""
    s = collections.Counter()
    ayr = []
    var_kel = collections.Counter()          # (soz id, kelime no) -> kac satirda
    for no, c in sorted(k.klipler.items()):
        if no not in k.teslim:
            continue
        d = karar.get(no)
        if d is None:
            if c.get("kesildi"):
                continue                      # cocuklari yerine gecti
            if c["yanki"]:
                s["yanki_atilan"] += 1
            elif c["hayalet"]:
                s["hayalet_atilan"] += 1
            else:
                s["yanlis_atilan"] += 1
                ayr.append(("YANLIS_ATILAN", _ozet_klip(c)))
            continue
        sinif = _sinif(d)
        ad = d["speaker"]
        for kel in c["kelimeler"]:
            var_kel[(kel[0], kel[1])] += 1
        if c["yanki"]:
            s["yanki_kalan"] += 1
            ayr.append(("YANKI_KALDI", _ozet_klip(c), ad))
            continue
        if c["hayalet"]:
            s["hayalet_klip"] += 1
            if sinif == "kesin":
                s["hayalet_kesin_yakin" if c.get("yakin") else "hayalet_kesin"] += 1
                ayr.append(("HAYALET_KESIN" + ("_YAKIN" if c.get("yakin") else ""), _ozet_klip(c), ad,
                            d.get("spk_guven")))
            continue
        s["klip"] += 1
        s[sinif] += 1
        sahipler = collections.Counter(kel[4] for kel in c["kelimeler"])
        sahip, pay = sahipler.most_common(1)[0] if sahipler else (None, 0)
        tek = pay >= 0.8 * (sum(sahipler.values()) or 1)
        kisiler = {u["kim"] for u in c["sozler"]}
        cakisma_gt = any(min(u["bit"], v["bit"]) - max(u["bas"], v["bas"]) >= 0.3
                         for i, u in enumerate(c["sozler"]) for v in c["sozler"][i + 1:] if u["kim"] != v["kim"])
        neden = d.get("spk_neden") or d.get("neden")
        if "capa_az" in (neden or ""):                       # ilk 3 L capasina kadar spk_guven tavani 0.6
            s["capa_az"] += 1
            if sinif != "kesin":
                s["capa_az_belirsiz"] += 1
        if cakisma_gt:
            s["dogal_belirsiz_klip"] += 1
        if sinif == "kesin":
            ad_pay = sahipler.get(ad, 0) / (sum(sahipler.values()) or 1)
            if ad_pay >= 0.8:
                s["dogru"] += 1
            elif ad_pay >= 0.5:                              # ad cogunlugun sahibi ama metnin >= %20'si baskasinin
                s["kirli"] += 1
                ayr.append(("KIRLI", _ozet_klip(c), ad, d.get("spk_guven"), neden, round(ad_pay, 2)))
            else:
                s["yanlis"] += 1
                ayr.append(("YANLIS", _ozet_klip(c), ad, d.get("spk_guven"), neden, round(ad_pay, 2)))
        elif sinif == "adayli":
            if not (tek and ad.rstrip("?") == sahip):
                s["adayli_yanlis_ad"] += 1
        elif sinif == "ayni":
            if not cakisma_gt:
                s["yanlis_ayni_anda_sirali" if len(kisiler) >= 2 else "yanlis_ayni_anda_dayanaksiz"] += 1
                ayr.append(("YANLIS_AYNI_ANDA", _ozet_klip(c), ad, neden,
                            "sirali" if len(kisiler) >= 2 else "dayanaksiz"))
        elif sinif == "devir" and len(kisiler) < 2:
            s["devir_dayanaksiz"] += 1
        if sahip in (ODA, KONUK):
            s["oda_klip"] += 1
            if sinif == "kesin":
                s["oda_kesin"] += 1
        if HAYALET in ad:
            s["hayalet_ad"] += 1
        if c["akis"] == "loopback" and k.kapsamasiz(c):
            s["kapsamasiz_uzak"] += 1
            if sinif != "soru":
                s["kapsamasiz_uzak_soru_degil"] += 1
                ayr.append(("KAPSAMASIZ_SORU_DEGIL", _ozet_klip(c), ad, d.get("spk_guven"), neden))
        if k.kapsamali(c):
            s["kapsamali"] += 1
            if sinif != "kesin":
                s["kapsamali_belirsiz"] += 1
    cift_ut = sorted({u for (u, _), v in var_kel.items() if v > 1})
    s["cift_ut"] = len(cift_ut)
    # eksik: hicbir kelimesi nihai satirlarda olmayan, kaybolmamasi gereken soz
    kayip_ut = k.suzgecle_dusen()
    for c in k.klipler.values():
        kayip_ut |= c["kayip"]
        if c.get("stt_yok") and not c.get("kesildi"):
            kayip_ut |= {u["id"] for u in c["sozler"]}
    kisa_ut = {u["id"] for u in k.ut if u["bit"] - u["bas"] < SES_ESIK_KISA}
    var_ut = {key[0] for key in var_kel}
    eksik = [u["id"] for u in k.ut if not u["sessiz"] and u["id"] not in kayip_ut and u["id"] not in kisa_ut
             and u["id"] not in var_ut]
    s["eksik"] = len(eksik)
    return s, ayr, cift_ut, eksik


def _yuzde(a, b):
    return round(100.0 * a / b, 1) if b else 0.0


# ======================================================================================================
# sahte LLM + motor kosumu
# ======================================================================================================

# Sahte LLM'in dogru duzeltmeleri: Whisper'in duydugu biçim -> (dogru biçim, neden). Kapi bunlardan bazilarini KABUL EDER
# (kubernetis, komit, cenkinse...), bazilarini bilerek reddeder (payplayn: edit uzakligi, mikro servisler: yaygin kelime).
DOGRU_ONERI = {                               # duyulan: (dogru biçim, neden, hedef terim / ad)
    "kubernetis": ("Kubernetes", "terim", "Kubernetes"), "komit": ("commit", "terim", "commit"),
    "payplayn": ("pipeline", "terim", "pipeline"), "cenkinse": ("Jenkins'e", "terim", "Jenkins"),
    "ciraya": ("Jira'ya", "terim", "Jira"), "kubernetisin": ("Kubernetes'in", "terim", "Kubernetes"),
    "taşlakın": ("Taslak'ın", "ad", "Taslak"), "komiti": ("commiti", "terim", "commit"),
    "mikro servisler": ("Mikroservisler", "terim", "Mikroservisler"),
}
ZOR_ONERI = {"payplayn", "mikro servisler"}   # kapi reddeder (edit uzakligi 0.4 asti / yaygin kelimeler): geri kazanim kaybi
# Ayni hatalarin EKI BOZAN / yanlis ekli biçimleri (model bazen boyle yazar); kapi hicbirini kabul etmemeli.
BOZUK_ONERI = {
    "kubernetis": "Kubernetesin", "komit": "commit'i", "cenkinse": "Jenkins'a", "ciraya": "Jira", "kubernetisin": "Kubernetesin",
    "taşlakın": "Taslak", "komiti": "commit",
}
# Yaygin Turkce kelimeyi terime ceviren yanlis duzeltmeler (komite != commit): eski -> yeni
YANLIS_YAYGIN = {"komite": "commit", "Komite": "commit", "komitesine": "commite", "komitede": "committe",
                 "Komitede": "commit'te", "kampanyacı": "Kampanya", "terminal": "Jenkins"}


def mesaj_coz(mesajlar):
    """duzelt.mesajlar() kullanici mesaji -> (terimler, adlar, [(satir id, metin)])."""
    icerik = mesajlar[-1]["content"]
    terim = re.search(r"^TERİMLER:\s*(.*)$", icerik, re.M)
    ad = re.search(r"^ADLAR:\s*(.*)$", icerik, re.M)

    def liste(m):
        return [x.strip() for x in m.group(1).split(";") if x.strip() and x.strip() != "-"] if m else []
    return liste(terim), liste(ad), re.findall(r"^\[(-?\d+)\]\s(.*)$", icerik.split("SATIRLAR:", 1)[-1], re.M)


def oneri_uret(terimler, adlar, satirlar, yogun=False):
    """Gercekci karisik oneri listesi (sema: satir/eski/yeni/neden). Dogru duzeltmeler yalniz TERIMLER/ADLAR listesindeki
    hedefe yapilir (model listeyi okur); her dogrunun onune (hafif: cagri basina bir tane, yogun: hepsi) eki bozan / yanlis
    ekli bir esi konur -- kapi onu reddedip dogruyu kabul etmeli (sira etkisi sinanir). Gurultu en sonda (EN_FAZLA_ONERI
    siniri gurultuyu keser, dogru duzeltmeleri degil): tum satir yeniden yazma, 'yazim' tahmini; yogun: yaygin kelime
    duzeltmeleri (komite -> commit), terim listesine benzeyen kelimeleri acgozlu terime cevirme, satir / sema disi oneriler."""
    tn = {kelime.normalize(t) for t in terimler}
    an = {kelime.normalize(x) for a in adlar for x in a.split()}
    dogru, gurultu, bozuk_hafif, ilk = [], [], 1, True
    for sid, metin in satirlar:
        i = int(sid)
        for eski, (yeni, neden, hedef) in DOGRU_ONERI.items():
            if not duzelt_mod.gecisler(metin, eski):
                continue
            if kelime.normalize(hedef) not in (an if neden == "ad" else tn):
                continue
            if eski in ZOR_ONERI and not yogun:                  # kapinin zaten reddettigi dogru duzeltmeler: yalniz yogun modda
                continue
            if eski in BOZUK_ONERI and (yogun or bozuk_hafif > 0):
                bozuk_hafif -= 1
                dogru.append({"satir": i, "eski": eski, "yeni": BOZUK_ONERI[eski], "neden": neden})
            dogru.append({"satir": i, "eski": eski, "yeni": yeni, "neden": neden})
        if ilk:                                              # bir tum satir yeniden yazma ('terim' diye)
            ilk = False
            govde = metin.rstrip(".?! ")
            gurultu.append({"satir": i, "eski": govde, "yeni": govde[:1].upper() + govde[1:] + " olarak", "neden": "terim"})
            uzun = max(re.findall(r"[^\W\d_]{6,}", metin) or [""], key=len)
            if uzun:                                         # 'yazim' tahmini: uygulanmaz, belirsiz isaretlenir
                gurultu.append({"satir": i, "eski": uzun, "yeni": uzun[:-1] + "ı", "neden": "yazim"})
        if not yogun:
            continue
        for eski, yeni in YANLIS_YAYGIN.items():
            if duzelt_mod.gecisler(metin, eski):
                gurultu.append({"satir": i, "eski": eski, "yeni": yeni, "neden": "terim"})
        for w in re.findall(r"[^\W\d_]{5,}(?:['’][^\W\d_]+)?", metin):    # acgozlu: en yakin terime (eksiz / eki koruyarak)
            if w in DOGRU_ONERI or w in YANLIS_YAYGIN:
                continue
            en = min(((duzelt_mod.uzaklik(w, t), t) for t in terimler if " " not in t), default=None)
            if en and 0 < en[0] <= 0.55:
                t = en[1]
                gurultu.append({"satir": i, "eski": w, "yeni": t, "neden": "terim"})
                if w.lower().startswith(t.lower()) and len(w) > len(t):
                    gurultu.append({"satir": i, "eski": w, "yeni": t + "'" + w[len(t):], "neden": "terim"})
    if yogun and satirlar:
        gurultu.append({"satir": 10 ** 6, "eski": "kubernetis", "yeni": "Kubernetes", "neden": "terim"})   # satir yok
        gurultu.append({"satir": int(satirlar[0][0]), "eski": "x"})                                        # sema disi
    return (dogru + gurultu)[:duzelt_mod.EN_FAZLA_ONERI]


def sahte_sor_fn(yogun=True):
    """duzelt.duzelt(sor_fn=): (mesajlar, sema) -> {"oneriler": [...]}"""
    return lambda mesajlar, sema: {"oneriler": oneri_uret(*mesaj_coz(mesajlar), yogun=yogun)}


class SahteLLM:
    def __init__(self, yogun=False):
        self.gorevler = []
        self.yogun = yogun
        self.mesajlar = []                   # duzeltme cagrilarinin kullanici mesajlari (sizinti taramasi icin)

    def __call__(self, mesajlar, max_tokens, sema=None, effort="medium", temperature=0.6, dusunme=True):
        g = llm._gorev_adi(mesajlar)
        self.gorevler.append(g)
        self.mesajlar.append(" ".join(m.get("content", "") for m in mesajlar))
        if g == "duzeltme":
            return json.dumps({"oneriler": oneri_uret(*mesaj_coz(mesajlar), yogun=self.yogun)}, ensure_ascii=False), "stop"
        if g == "bolum":
            return json.dumps({"ozet": "Kubernetes sürümü ve test ortamı konuşuldu.", "konular": ["test ortamı"],
                               "kararlar": ["Pilot çağrı merkeziyle başlayacak"],
                               "aksiyonlar": [{"madde": "Ek sunucu talep edilecek", "sorumlu": B, "tarih": "-"}],
                               "acik_sorular": [], "belirsiz_terimler": []}, ensure_ascii=False), "stop"
        if g == "liste":
            return json.dumps({"kararlar": ["Pilot çağrı merkeziyle başlayacak"],
                               "aksiyonlar": [{"madde": "Ek sunucu talep edilecek", "sorumlu": B, "tarih": "-"}],
                               "acik_sorular": []}, ensure_ascii=False), "stop"
        return json.dumps({"ozet": "Toplantıda test ortamı ve pilot konuşuldu.", "sonraki_adim": "Ek sunucu talebi."},
                          ensure_ascii=False), "stop"


def _ortam_kaydet():
    return {"saat": (saat_mod.simdi, saat_mod.duvar, saat_mod.duvardan, saat_mod.time), "gercek_time": gercek_time.time,
            "grup_bul": yakalayici._grup_bul, "PARCA_TOKEN": M.PARCA_TOKEN, "DUZELT_MIN_TOKEN": M.DUZELT_MIN_TOKEN,
            "sor": llm.sor, "token_say": llm.token_say, "BAGLAM_PENCERESI": llm.BAGLAM_PENCERESI,
            "BUTCE_AYAR": llm.BUTCE_AYAR}


def _ortam_geri_yukle(e):
    gercek_time.time = e["gercek_time"]
    yakalayici._grup_bul = e["grup_bul"]
    saat_mod.time = e["saat"][3]
    saat_mod.sifirla()                                       # ankrajlar gercek saate (adim modu sanal ankraj birakir)
    saat_mod.simdi, saat_mod.duvar, saat_mod.duvardan = e["saat"][:3]
    M.PARCA_TOKEN = e["PARCA_TOKEN"]
    M.DUZELT_MIN_TOKEN = e["DUZELT_MIN_TOKEN"]
    llm.sor = e["sor"]
    llm.token_say = e["token_say"]
    llm.BAGLAM_PENCERESI = e["BAGLAM_PENCERESI"]
    llm.BUTCE_AYAR = e["BUTCE_AYAR"]


_SIZINTI = re.compile(r"cap\d{3}[kr]\d{2}")


def calistir(seed=7, tur="karisik", katilimcilar=None, **sec):
    """Tek senaryo (Motor, uretim varsayilanlari: kaynak 'ses', altyazi_yedek kapali, K5 yok). Her cagri kendi gecici
    klasorunde kosar (cwd gecici klasore alinir, bitince geri doner ve klasor silinir); modul genel durumu (saat, LLM,
    esikler) cagri sonunda eski haline doner. Dondurur: olcum sozlugu (bkz. degerlendir + _motor_olcum)."""
    sec = dict(sec, tur=tur)
    gecici = tempfile.mkdtemp(prefix="bm_e2e_")
    onceki_cwd = os.getcwd()
    eski = _ortam_kaydet()
    m = None
    try:
        os.chdir(gecici)
        llm.BAGLAM_PENCERESI = llm.VARSAYILAN_PENCERE         # yerel config.json makineye bagli olmasin
        llm.BUTCE_AYAR = {}
        saat = SahteSaat()
        if sec.get("saat_adimlari"):
            AdimliSaat(saat, sec["saat_adimlari"], sec.get("saat_eski", False)).kur()
        else:
            saat_mod.sanal_kur(lambda: saat.t)               # simdi() sanal saat (duvar ofseti 0)
        llm.token_say = llm.token_tahmin
        sahte = SahteLLM(yogun=bool(sec.get("yogun")))
        llm.sor = sahte
        M.PARCA_TOKEN, M.DUZELT_MIN_TOKEN = 500, 200
        ut = uret(tur, random.Random(seed), sec)
        liste = list(katilimcilar) if katilimcilar is not None else [A, B, C, BEN]
        kl_dir = M.yeni_klasor(f"e2e {tur} {seed}", BAS.date())
        alici = MotorAlici(saat)
        havuz = terim_mod.TerimHavuzu(sec["terim_yol"]) if sec.get("terim_yol") else None     # None: toplantiya ozel havuz
        m = M.Motor(kl_dir, "E2E", BAS.date(), havuz, liste, duzelt=True, ben=BEN, olay=alici.olay,
                    esikler=sec.get("esikler"), davet=sec.get("davet", DAVET))
        alici.m = m
        m.terimleri_hazirla()                                # davet metninden terimler
        if sec.get("terimler", TERIM_VARSAYILAN):            # elle terimler (davetin cikaramadigi kucuk harfli teknik terimler)
            m.terimler.ekle(m.seri, list(sec.get("terimler", TERIM_VARSAYILAN)), "elle")
        if sec.get("ogrenme", True) is False:                # ogrenme kapali: gozlem de ogrenme de yok
            m._ogren = lambda: None
            m.terimler.gozlem_ekle = lambda *a, **kw: False
        k = Kosum(ut, sec, seed, alici)
        if sec.get("ipucu"):                                 # isler.py ile ayni: terim.prompt(seri, katilimcilar)
            k.servis._ipucu_fn = _ipucu_onbellekli(m)
        m.yeniden_kes_fn = k.yeniden_kes
        k.calistir()
        saat.t = T0 + k.bitis + 30
        m.kontrol()
        durum = dict(m.konusmaci.durum())
        m.bitir()
        stt_eksik = m.stt_eksik()
        not_md = m.notu_uret()
        satirlar = m.tum_satirlar()
        jsonl = M.altyazi_kayitlari(m.klasor)
        ham_cikti = _klasor_metni(m.klasor)                  # toplanti klasorundeki TUM dosyalar (sizinti taramasi)
        ek = {"terim_ogrenilen": sorted(v for e in m.terimler.terimler for v in e.get("ogrenilen", [])),
              "terim_gozlem": len(m.terimler.gozlemler), "duzelt_log": _duzelt_gunlugu_ozeti(m.klasor)}
        m.kapat()
        m = None
        return _motor_olcum(k, alici, satirlar, jsonl, not_md, ham_cikti, durum, stt_eksik, sahte, sec, seed, ek)
    finally:
        if m is not None:
            try:
                m.kapat()
            except Exception:
                pass
        _ortam_geri_yukle(eski)
        os.chdir(onceki_cwd)
        shutil.rmtree(gecici, ignore_errors=True)


def _ipucu_onbellekli(m):
    """isler.py'deki ipucu_fn (terim.prompt(seri, katilimcilar)) -- havuz ve katilimci listesi degismedikce ayni metin
    yeniden uretilmez (duzenek hizi; prompt yine gercek terim.prompt'tan gelir)."""
    onbellek = {}

    def fn(akis):
        h = m.terimler
        anahtar = (tuple(m.katilimcilar), len(h.terimler), sum(sum(e["seriler"].values()) for e in h.terimler),
                   round(sum(e["guven"] for e in h.terimler), 3), sum(bool(e["genel"]) for e in h.terimler))
        if anahtar not in onbellek:
            onbellek[anahtar] = h.prompt(m.seri, list(m.katilimcilar))
        return onbellek[anahtar]
    return fn


def _klasor_metni(klasor):
    """Klasordeki (alt klasorler dahil) tum dosyalarin metni, tek dize. ikili / okunamayan dosyalar atlanir."""
    parca = []
    for kok, _, adlar in os.walk(klasor):
        for ad in adlar:
            try:
                with open(os.path.join(kok, ad), encoding="utf-8") as f:
                    parca.append(f.read())
            except (OSError, UnicodeDecodeError):
                continue
    return "\n".join(parca)


def _duzelt_gunlugu_ozeti(klasor):
    """duzelt_log.jsonl (parca basina kabul / red kaydi) -> {"oneri", "kabul", "red": {sebep: n}, "neden": {neden: n}}."""
    o = {"oneri": 0, "kabul": 0, "red": collections.Counter(), "neden": collections.Counter()}
    try:
        with open(os.path.join(klasor, "duzelt_log.jsonl"), encoding="utf-8") as f:
            for ham in f:
                kayit = json.loads(ham)
                o["oneri"] += kayit.get("oneri", 0)
                o["kabul"] += len(kayit.get("kabul") or [])
                for d in kayit.get("kabul") or []:
                    o["neden"][d.get("neden")] += 1
                for d in kayit.get("red") or []:
                    o["red"][d.get("sebep", "?")] += 1
    except (OSError, ValueError):
        pass
    o["red"], o["neden"] = dict(o["red"]), dict(o["neden"])
    return o


def _toplu_is(is_):
    """is_ = (anahtar, kwargs[, islev adi]); islev varsayilan 'calistir' (ogrenme_kos gibi modul islevleri de olur)."""
    anahtar, kw = is_[0], is_[1]
    try:
        return anahtar, globals()[is_[2] if len(is_) > 2 else "calistir"](**kw), None
    except Exception:
        import traceback
        return anahtar, None, traceback.format_exc()


def paralel_sayisi():
    """Toplu kosumda ayni anda kosan surec sayisi (en cok 4). BM_E2E_PARALEL=0 ya da fork olmayan platformda 1 (seri)."""
    if os.environ.get("BM_E2E_PARALEL") == "0" or not hasattr(os, "fork"):
        return 1
    try:
        return max(1, min(4, len(os.sched_getaffinity(0))))
    except AttributeError:
        return max(1, min(4, os.cpu_count() or 1))


def toplu(isler):
    """isler: [(anahtar, kwargs[, islev adi])] -> {anahtar: (sonuc, hata_metni)}. Her kosum bagimsizdir (kendi gecici klasoru,
    modul durumu fork'lanan surecte kalir): `paralel_sayisi()` > 1 ise fork ile paralel, degilse seri. Sonuclar seri
    kosumla ayni (tohumlu). Hata tek kosumu etkiler: (None, izleme)."""
    isler = list(isler)
    n = min(paralel_sayisi(), len(isler))
    out = {}
    if n > 1:
        try:
            import multiprocessing
            with multiprocessing.get_context("fork").Pool(n) as havuz:
                for anahtar, sonuc, hata in havuz.imap_unordered(_toplu_is, isler, chunksize=1):
                    out[anahtar] = (sonuc, hata)
            return out
        except Exception:                                    # havuz kurulamadi: kalanlar seri kosulur
            pass
    for is_ in isler:
        if is_[0] not in out:
            anahtar, sonuc, hata = _toplu_is(is_)
            out[anahtar] = (sonuc, hata)
    return out


def _motor_olcum(k, alici, satirlar, jsonl, not_md, ham_cikti, durum, stt_eksik, sahte, sec, seed, ek=None):
    karar = {s["klip_no"]: s for s in satirlar if s.get("klip_no") is not None}
    sayac, ayr, cift_ut, eksik = degerlendir(k, karar)
    sonuc = dict(sayac)
    sonuc["satir"] = len(satirlar)
    sonuc["altyazi_metni"] = sum(1 for s in satirlar if str(s.get("kaynak", "")).startswith("altyazi"))
    sonuc["sizinti"] = (len(_SIZINTI.findall(ham_cikti)) + len(_SIZINTI.findall(not_md))
                        + sum(len(_SIZINTI.findall(m_)) for m_ in sahte.mesajlar))      # LLM'e giden mesajlar dahil
    # oda etiketi: gosterim 'Ad (oda)'
    sonuc["oda_gorunen_hatali"] = sum(1 for s in satirlar if s.get("speaker") == ODA and s.get("durum") == "karar"
                                      and _sinif(s) != "soru" and not gorunen_ad(s).startswith(ODA + " (oda)"))
    sonuc["gosterim_hatali"] = sum(1 for s in satirlar if not _gosterim_dogru(s))
    # jsonl ile bellekteki nihai satirlar uyumlu mu (guncelleme / silme / yerine kayitlari)
    a = {(s["id"], s.get("speaker"), s.get("spk_guven")) for s in satirlar}
    b = {(s["id"], s.get("speaker"), s.get("spk_guven")) for s in jsonl}
    sonuc["jsonl_tutarsiz"] = len(a ^ b)
    # zaman: satir -> karar gecikmesi
    ilk, karar_an = {}, {}
    for an, tip, sid, durum_ in alici.olaylar:
        if tip == "satir":
            ilk.setdefault(sid, an)
        elif tip == "satir_guncelle" and durum_ == "karar":
            karar_an.setdefault(sid, an)
    gec = sorted(karar_an[i] - ilk[i] for i in karar_an if i in ilk)
    sonuc["karar_gecikme_ort"] = round(sum(gec) / len(gec), 2) if gec else None
    sonuc["karar_gecikme_p95"] = round(gec[int(0.95 * (len(gec) - 1))], 2) if gec else None
    sonuc["bekleyen_kalan"] = sum(1 for s in satirlar if s.get("durum") == "beklemede")
    # L tahmini
    L_gercek = k.L_gercek()
    sonuc["L"] = durum.get("L")
    sonuc["L_gercek"] = round(L_gercek, 2) if L_gercek is not None else None
    sonuc["L_hata"] = round(abs(durum["L"] - L_gercek), 2) if L_gercek is not None else None
    sonuc["MAD"], sonuc["R"], sonuc["capa"] = durum.get("MAD"), durum.get("R"), durum.get("capa")
    sonuc["kes_istek"], sonuc["kes_tamam"] = k.kes_istek, k.kes_tamam
    # STT kesintisi: cozulemeyen klipler stt_eksik() icinde
    sonuc["stt_eksik_kapsanmayan"] = sum(
        1 for a_, b_ in k.bosluklar_birlesik() if not any(e[0] - 2 <= T0 + a_ and e[1] + 2 >= T0 + b_ for e in stt_eksik))
    sonuc["stt_eksik_notta"] = ("STT yok — transkript eksik" in not_md) if k.bosluklar else None
    sonuc["bozulan"] = _bozulan(k, satirlar)
    sonuc.update(_duzeltme_olcum(k, satirlar))
    sonuc.update(_hayalet_olcum(k, karar, ayr, "\n".join(sahte.mesajlar)))
    sonuc["satir_sil"] = dict(alici.silinme)
    sonuc.update(_ipucu_olcum(k, satirlar))
    sonuc.update(ek or {})
    sonuc["llm_gorevleri"] = {g: sahte.gorevler.count(g) for g in set(sahte.gorevler)}
    sonuc["not_uzunluk"] = len(not_md)
    if sec.get("saat_adimlari"):
        sonuc.update(_adim_olcum(k, satirlar, sec["saat_adimlari"]))
    sonuc["ayrinti"] = ayr
    sonuc["cift_ut_liste"] = cift_ut
    sonuc["eksik_liste"] = eksik
    sonuc["ayar"] = dict(sec, seed=seed)
    return sonuc


def _duzeltme_olcum(k, satirlar):
    """LLM duzeltmesinin zemin gercegine gore kelime duzeyi etkisi (olcum.kelime_etkisi): duzeltilen (yanlis -> dogru),
    bozulan_kelime (dogru -> yanlis; Faz 2 kapisi 0), son kelime hatasi ve ham (duzeltmesiz) hata. Ayrica eki kisalan
    kabul (ek_kaybi), 'komite' sozunun korunmasi (komite_commit) ve belirsiz isaretli kelimeler."""
    s = collections.Counter(dict.fromkeys(
        ("duz_kabul", "belirsiz_kelime", "duz_degisen_satir", "ek_kaybi", "duzeltilen", "bozulan_kelime", "kelime_ref",
         "kelime_hata_ham", "kelime_hata", "komite_say", "komite_commit"), 0))
    for sat in satirlar:
        s["duz_kabul"] += len(sat.get("duzeltmeler") or [])
        s["belirsiz_kelime"] += len(sat.get("belirsiz") or [])
        s["duz_degisen_satir"] += sat.get("raw") != sat.get("text")
        for d in sat.get("duzeltmeler") or []:
            if len(kelime._sade(d["yeni"])) < len(kelime._sade(d["eski"])):
                s["ek_kaybi"] += 1
        c = k.klipler.get(sat.get("klip_no"))
        if c is None or c["hayalet"] or c["yanki"] or not isinstance(sat.get("raw"), str):
            continue
        gt = " ".join(x[2] for x in c["kelimeler"])
        ref, raw_k = olcum.kelimeler(gt), olcum.kelimeler(sat["raw"])
        metin = sat.get("text") or ""
        s["kelime_ref"] += len(ref)
        hata_ham = olcum.duzenleme_mesafesi(ref, raw_k)
        s["kelime_hata_ham"] += hata_ham
        if metin == sat["raw"]:                              # degismeyen satir: etki yok, hata ayni
            s["kelime_hata"] += hata_ham
            text_k = raw_k
        else:
            text_k = olcum.kelimeler(metin)
            iyi, kotu = olcum.kelime_etkisi_kelimeden(ref, raw_k, text_k)
            s["duzeltilen"] += iyi
            s["bozulan_kelime"] += kotu
            s["kelime_hata"] += olcum.duzenleme_mesafesi(ref, text_k)
        n_gt = sum(1 for w in ref if w.startswith("komite"))
        if n_gt:
            s["komite_say"] += n_gt
            s["komite_commit"] += max(0, n_gt - sum(1 for w in text_k if w.startswith("komite")))
    return dict(s)


def _hayalet_olcum(k, karar, ayr, llm_metni=""):
    """Halusinasyon klipleri (Faz 2 kapisi): satiri olmayan = dusen (filtre / at / tekrar / satir_sil); satir var ve supheli
    (nota girmez) = hal_supheli; satir var ve supheli degil = hal_kesin (KAPI: 0). Konusma ardinda ayirt edilemeyen
    turler (hal_belirsiz: akici cumle) hal_belirsiz_kesin olarak yalniz izlenir."""
    s = collections.Counter(dict.fromkeys(
        ("hal_toplam", "hal_dusen", "hal_supheli", "hal_belirsiz_kesin", "hal_kesin", "hal_llm_girdi"), 0))
    dagilim = collections.Counter()              # "konum:tur:sonuc" -> n (kapsam gorunurlugu)
    for no, c in sorted(k.klipler.items()):
        if not c["hayalet"]:
            continue
        s["hal_toplam"] += 1
        sat = karar.get(no)
        if sat is None:
            sonuc = "dusen"
        elif sat.get("supheli"):
            sonuc = "supheli"
        elif c.get("hal_belirsiz"):
            sonuc = "belirsiz_kesin"
        else:
            sonuc = "kesin"
            ayr.append(("HALUSINASYON_KESIN", _ozet_klip(c), c.get("hal_tur"), sat.get("hal_puani"), sat.get("speaker")))
        s["hal_" + sonuc] += 1
        if sat is not None and len(sat.get("text") or "") >= 20 and sat["text"] in llm_metni:
            s["hal_llm_girdi"] += 1                   # supheli satir LLM'e (duzeltme / ozet) gitmemeli
        dagilim[f"{'yakin' if c.get('yakin') else 'uzak'}:{c.get('hal_tur', 'eski')}:{sonuc}"] += 1
    out = dict(s)
    out["hal_dagilim"] = dict(dagilim)
    return out


def _ipucu_olcum(k, satirlar):
    """Whisper prompt'u: giden / promptsuz tekrarlanan klip sayisi, dusuk RMS klibe giden prompt (KAPI: 0), en uzun prompt,
    prompt metninin transkripte sizmasi (sizinti_satir; KAPI: 0), atilan halusinasyon."""
    cagri = k.stt.cagrilar
    return {"ipucu_gonderilen": k.servis.ipucu_gonderilen, "ipucu_yeniden": k.servis.ipucu_yeniden,
            "ipucu_dusuk_rms": sum(1 for c in cagri if c["ipucu_kr"] and c["rms"] < ses.IPUCU_RMS),
            "ipucu_azami_kr": max([c["ipucu_kr"] for c in cagri] or [0]),
            "ipucu_sizinti_cagri": sum(1 for c in cagri if c["ipucu_kr"] and c["sizinti"]),
            "ipucu_sizinti_satir": sum(1 for sat in satirlar
                                       if "Terimler:" in (sat.get("raw") or "") + (sat.get("text") or "")
                                       or "Katılımcılar:" in (sat.get("raw") or "") + (sat.get("text") or "")),
            "hal_atilan": k.servis.atilan_halusinasyon, "ipucu_ornek": k.stt.ornek_ipucu}


def _adim_olcum(k, satirlar, adimlar):
    """Saat adimi: algilanan adim sayisi (saat.adim_sayisi) ve satir ts'i = yerel duvar saati mi? Adimlardan >= 30 sn
    uzakta baslayan klibin ts'i o anki duvar saatinin HH:MM:SS'i (+-1.5 sn); adim yakininda gosterim ofseti bir adim
    kadar (en cok 6.5 sn) farkli olabilir (ts, satir olusurken gecerli duvar ofsetiyle yazilir)."""
    def ofset(g):
        return sum(f for a, f in adimlar if g >= a)
    hatali = yakin_hatali = olculen = 0
    for s in satirlar:
        c = k.klipler.get(s.get("klip_no"))
        if c is None or not s.get("ts"):
            continue
        beklenen = dt.datetime.fromtimestamp(T0 + c["bas"] + ofset(c["bas"])).strftime("%H:%M:%S")
        sapma = abs(M._sn_fark(s["ts"], beklenen))
        if min(abs(c["bas"] - a) for a, _ in adimlar) >= 30.0:
            olculen += 1
            hatali += sapma > 1.5
        else:
            yakin_hatali += sapma > 6.5
    return {"saat_adim_algilanan": saat_mod.adim_sayisi(), "ts_olculen": olculen, "ts_hatali": hatali,
            "ts_yakin_hatali": yakin_hatali}


def _gosterim_dogru(s):
    """metin.gorunen_ad tek gosterim kurali (MIMARI_v3 §3): '?' -> Bilinmeyen; 'A / B' + cakisma -> '(aynı anda)';
    spk_guven < 0.7 olan kisi adi -> 'Ad?' (oda etiketi 'Ad (oda)'); kesin ad oldugu gibi; karar bekleyen -> beklemede…"""
    g = gorunen_ad(s)
    ad = str(s.get("speaker") or "").strip()
    if s.get("durum") == "beklemede":
        return g == "beklemede…"
    if ad in ("", "?"):
        return g == "Bilinmeyen"
    if " / " in ad:
        return g.endswith("(aynı anda)") == bool(s.get("cakisma"))
    if " → " in ad:
        return g == ad
    if s.get("kimlik_turu") == "oda":
        return g == ad + " (oda)"
    if s.get("spk_guven", 1.0) < 0.7:
        return g == ad + "?"
    return g == ad


def _bozulan(k, satirlar):
    """LLM duzeltmesi satiri zemin gercegi metninden uzaklastirdi mi (Faz 2 kapisi; Faz 1'de tavan olarak izlenir)."""
    import difflib

    def norm(s):
        return re.sub(r"[^\w\s]", "", s.lower()).split()

    def fark(a, b):
        sm = difflib.SequenceMatcher(None, a, b)
        return sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal")
    n = 0
    for s in satirlar:
        c = k.klipler.get(s.get("klip_no"))
        if c is None or c["hayalet"] or c["yanki"]:
            continue
        gt = norm(" ".join(x[2] for x in c["kelimeler"]))
        if fark(gt, norm(s.get("text") or "")) > fark(gt, norm(s.get("raw") or "")):
            n += 1
    return n


# ======================================================================================================
# dogrudan Konusmaci kosumu (oracle)
# ======================================================================================================

def konusmaci_kos(ut, seed=1, katilimcilar=None, ben=BEN, esikler=None, **sec):
    """Motor'suz: Konusmaci'yi dogrudan surer. ut: zemin gercegi sozleri ({"kim","bas","bit","metin"}; id/slot/sessiz
    eksikse tamamlanir). Dondurur (sayaclar, ayrinti, kosum, konusmaci)."""
    for i, u in enumerate(ut):
        u.setdefault("id", i)
        u.setdefault("slot", _slotlar(u["metin"]))
        u.setdefault("sessiz", False)
    sec = dict(sec)
    sec.setdefault("tur", "karisik")
    sec.setdefault("ben", ben)
    kon = Konusmaci(ben=ben, katilimcilar=katilimcilar if katilimcilar is not None else sorted({u["kim"] for u in ut}),
                    esikler=esikler)
    alici = KonusmaciAlici(kon)
    k = Kosum(ut, sec, seed, alici)
    alici.k = k
    k.calistir()
    alici.zaman(T0 + k.bitis + 30)
    alici.kontrol()
    alici.bitir()
    sayac, ayr, _cift, _eksik = degerlendir(k, alici.karar)
    return sayac, ayr, k, kon


# ======================================================================================================
# terim mikro olcumu (Faz 2 kapisi: duzelt.kapi + terim havuzu, sozluk.py yerine)
# ======================================================================================================

MIKRO_TERIMLER = ("teminat", "kampanya", "mikroservis", "Kubernetes", "pipeline", "Kredi", "müşteri", "rapor", "Jenkins",
                  "SQL", "commit", "Jira")           # eski sozluk terimleri + komit takma adinin hedefi + ekli terim (Jira'ya)
# 18 ornek satir. Adlar (SOZLUK_MIKRO_*) tests/test_duzelt.py ayni listeyi AST ile okudugu icin korunur (duz sabit olmali).
SOZLUK_MIKRO_ORNEK = ["Teminatı hesapla", "kampanyayı duyur", "kampanyalar", "mikroservisler yavaş", "müşteriler geldi",
                      "müşterinin talebi", "raporu gönder", "Krediyi onayla", "kredisi var", "Jenkinse bak", "kubernetis kümesi",
                      "komit attım", "komite toplandı", "pipelinelar", "teminatlar", "terminal açık", "kampanyacı", "Kubernetesi"]
# Bilinen zarar: bu sozcukler degismemeli (komite != commit, kampanyacı != kampanya)
SOZLUK_MIKRO_ZARAR = {"komite", "kampanyacı"}
# Kapinin KABUL etmesi gereken 4 dogru duzeltme (Whisper'in bozdugu terimler); kalan 14 satir degismemeli (yaygin kelime,
# ek tasiyan gercek kelime: komite, kampanyacı, terminal...)
MIKRO_BEKLENEN = {"Jenkinse bak": "Jenkins'e bak", "kubernetis kümesi": "Kubernetes kümesi", "komit attım": "commit attım",
                  "Kubernetesi": "Kubernetes'i"}
MIKRO_ORNEK = [(g, MIKRO_BEKLENEN.get(g)) for g in SOZLUK_MIKRO_ORNEK]
MIKRO_KORUNAN = SOZLUK_MIKRO_ZARAR
# Ek uyumu vakalari (duzelt.kapi): (satir, eski, yeni, neden, kabul beklenir)
EK_UYUM_VAKALARI = [
    ("cenkinse bak", "cenkinse", "Jenkins'e", "terim", True), ("cenkinse bak", "cenkinse", "Jenkins'a", "terim", False),
    ("cenkinse bak", "cenkinse", "Jenkins", "terim", False), ("ciraya kayıt aç", "ciraya", "Jira'ya", "terim", True),
    ("ciraya kayıt aç", "ciraya", "Jira'e", "terim", False), ("ciraya kayıt aç", "ciraya", "Jira", "terim", False),
    ("kubernetisin ayarı", "kubernetisin", "Kubernetes'in", "terim", True),
    ("kubernetisin ayarı", "kubernetisin", "Kubernetesin", "terim", False),
    ("kubernetis düştü", "kubernetis", "Kubernetes", "terim", True), ("kubernetis düştü", "kubernetis", "Kubernetesin", "terim", False),
    ("komit attım", "komit", "commit", "terim", True), ("komit attım", "komit", "commit'i", "terim", False),
    ("komite toplandı", "komite", "commit", "terim", False), ("komitede konuştuk", "komitede", "commit'te", "terim", False),
    ("kampanyacı geldi", "kampanyacı", "kampanya", "terim", False), ("raporu gönder", "raporu", "rapor", "terim", False),
    ("komit attım", "komit", "commit", "yazim", False), ("komit attım", "komit", "commit", "yok", False),
    ("komit attım bugün", "komit attım", "commit attım", "terim", False),
]


def _mikro_sor_fn(terimler):
    """Acgozlu sahte LLM: satirdaki her kelimeyi her terime (eksiz / eki koruyarak / eki bitisik) cevirmeyi dener +
    bilinen dogru duzeltmeler. Kapi yalnizca dogru olanlari gecirmeli."""
    def fn(mesajlar, sema):
        _t, adlar, satirlar = mesaj_coz(mesajlar)
        cikti = []
        for sid, metin in satirlar:
            i = int(sid)
            for eski, (yeni, neden, hedef) in DOGRU_ONERI.items():
                if duzelt_mod.gecisler(metin, eski):
                    cikti.append({"satir": i, "eski": eski, "yeni": yeni, "neden": neden})
            for w in re.findall(r"[^\W\d_]+", metin):
                for t in terimler:
                    if duzelt_mod.uzaklik(w, t) > 0.7:
                        continue
                    ek = w[len(t):] if w.lower().startswith(t.lower()) and len(w) > len(t) else ""
                    for yeni in {t, t + ek, t + "'" + ek if ek else t}:
                        cikti.append({"satir": i, "eski": w, "yeni": yeni, "neden": "terim"})
            for eski, yeni in YANLIS_YAYGIN.items():
                if duzelt_mod.gecisler(metin, eski):
                    cikti.append({"satir": i, "eski": eski, "yeni": yeni, "neden": "terim"})
        return {"oneriler": cikti[:duzelt_mod.EN_FAZLA_ONERI]}
    return fn


def terim_mikro():
    """18 ornek satirda terim havuzu + sahte (acgozlu) LLM + gercek duzelt.duzelt kapisi. Her satir ayri cagri (<= 25 oneri).
    Dondurur: {'toplam', 'degisen' (kabul edilen satir), 'yanlis': [(girdi, cikti, beklenen)], 'zarar': [(eski, yeni)]
    (korunan kelime degisti), 'ek_kaybi' (kabul edilen duzeltme eki kisaltti), 'oneri', 'kabul'}."""
    gecici = tempfile.mkdtemp(prefix="bm_mikro_")
    try:
        havuz = terim_mod.TerimHavuzu(os.path.join(gecici, "t.json"))
        havuz.ekle("mikro", list(MIKRO_TERIMLER), "elle")
        adaylar = havuz.adaylar("mikro")
        sor = _mikro_sor_fn(adaylar)
        sonuc = {"toplam": len(MIKRO_ORNEK), "degisen": 0, "yanlis": [], "zarar": [], "ek_kaybi": 0, "oneri": 0, "kabul": 0}
        for girdi, beklenen in MIKRO_ORNEK:
            yeni, rapor = duzelt_mod.duzelt([{"id": 1, "raw": girdi}], adaylar, [], sor_fn=sor)
            cikti = yeni[0]["text"]
            sonuc["oneri"] += rapor["oneri"]
            sonuc["kabul"] += rapor["kabul"]
            sonuc["degisen"] += cikti != girdi
            if cikti != (beklenen or girdi):
                sonuc["yanlis"].append((girdi, cikti, beklenen or girdi))
            for d in rapor["satirlar"]["1"]["kabul"]:
                if d["eski"].lower() in MIKRO_KORUNAN:
                    sonuc["zarar"].append((d["eski"], d["yeni"]))
                if len(kelime._sade(d["yeni"])) < len(kelime._sade(d["eski"])):
                    sonuc["ek_kaybi"] += 1
        return sonuc
    finally:
        shutil.rmtree(gecici, ignore_errors=True)


def ek_uyum_mikro():
    """EK_UYUM_VAKALARI: kapinin karari beklenenle ayni mi? Dondurur uyusmayanlar [(satir, eski, yeni, neden, beklenen, gercek)]."""
    gecici = tempfile.mkdtemp(prefix="bm_ek_")
    try:
        havuz = terim_mod.TerimHavuzu(os.path.join(gecici, "t.json"))
        havuz.ekle("mikro", list(MIKRO_TERIMLER), "elle")
        adaylar = havuz.adaylar("mikro")
        kotu = []
        for satir, eski, yeni, neden, beklenen in EK_UYUM_VAKALARI:
            kabul, _ = duzelt_mod.kapi(satir, {"satir": 1, "eski": eski, "yeni": yeni, "neden": neden}, adaylar, [])
            if kabul != beklenen:
                kotu.append((satir, eski, yeni, neden, beklenen, kabul))
        return kotu
    finally:
        shutil.rmtree(gecici, ignore_errors=True)


# ======================================================================================================
# ogrenme acik / kapali (Faz 2 kapisi: son kelime hatasi acik <= kapali)
# ======================================================================================================

SEDLER_OGRENME = (7, 11, 23, 100)                      # ayni seride 4 ardisik toplanti


def ogrenme_kos(ogrenme, seedler=SEDLER_OGRENME, sure=420.0):
    """Ayni seri (ortak terim havuzu dosyasi), `seedler` kadar ardisik toplanti; her biri Faz 2 cumleleri + yogun sahte LLM.
    ogrenme False: gozlem ve ogrenme yok (davet ve elle terimler yine var). Dondurur toplam sayaclar:
    kelime_hata, kelime_hata_ham, bozulan_kelime, duzeltilen, hal_kesin, ogrenilen (varyant listesi), gozlem."""
    gecici = tempfile.mkdtemp(prefix="bm_ogr_")
    try:
        yol = os.path.join(gecici, "terimler", "terimler.json")
        toplam = collections.Counter()
        ogrenilen, gozlem = [], 0
        for sd in seedler:
            r = calistir(seed=sd, tur="karisik", cumleler="faz2", yogun=True, sure=sure, terim_yol=yol, ogrenme=ogrenme)
            for a in ("kelime_hata", "kelime_hata_ham", "bozulan_kelime", "duzeltilen", "kelime_ref", "bozulan", "duz_kabul"):
                toplam[a] += r.get(a, 0)
            ogrenilen = r["terim_ogrenilen"]
            gozlem = r["terim_gozlem"]
        sonuc = dict(toplam)
        sonuc.update(ogrenilen=ogrenilen, gozlem=gozlem)
        return sonuc
    finally:
        shutil.rmtree(gecici, ignore_errors=True)


# ======================================================================================================
# ozet tablo
# ======================================================================================================

def belirsiz_sayisi(s):
    return s.get("soru", 0) + s.get("adayli", 0) + s.get("devir", 0) + s.get("ayni", 0)


def satir_ozeti(ad, seed, s):
    kl = s.get("klip", 0)
    return (f"{ad:18} s={seed:<3} klip={kl:<3} dogru={s.get('dogru', 0):<3} YANLIS={s.get('yanlis', 0)}+{s.get('kirli', 0)}k "
            f"?={s.get('soru', 0):<2} Ad?={s.get('adayli', 0):<2} A→B={s.get('devir', 0):<2} A/B={s.get('ayni', 0):<2} "
            f"bel%={_yuzde(belirsiz_sayisi(s), kl):<5} kapsamali_bel%={_yuzde(s.get('kapsamali_belirsiz', 0), s.get('kapsamali', 0)):<5} "
            f"kapsamasiz?={s.get('kapsamasiz_uzak', 0) - s.get('kapsamasiz_uzak_soru_degil', 0)}/{s.get('kapsamasiz_uzak', 0)} "
            f"cift={s.get('cift_ut', 0)} yAA={s.get('yanlis_ayni_anda_sirali', 0)}+{s.get('yanlis_ayni_anda_dayanaksiz', 0)} "
            f"yanki_kalan={s.get('yanki_kalan', 0)} yanlis_atilan={s.get('yanlis_atilan', 0)} "
            f"L={s.get('L')}/{s.get('L_gercek')} kes={s.get('kes_tamam', 0)}/{s.get('kes_istek', 0)} "
            f"eksik={s.get('eksik', 0)} bekl_p95={s.get('karar_gecikme_p95')}s "
            f"| F2 bozulan={s.get('bozulan_kelime', 0)} duzeltilen={s.get('duzeltilen', 0)} kabul={s.get('duz_kabul', 0)} "
            f"kelime_hata={s.get('kelime_hata', 0)}/{s.get('kelime_hata_ham', 0)} hal_kesin={s.get('hal_kesin', 0) + s.get('hal_belirsiz_kesin', 0)}"
            f"/{s.get('hal_toplam', 0)}")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    seedler, ayrinti, adlar, i = SEDLER, False, [], 0
    while i < len(argv):
        if argv[i] == "--seed":
            seedler = tuple(int(x) for x in argv[i + 1].split(","))
            i += 1
        elif argv[i] == "--tara":                            # genis tarama: N tohum (100..100+N-1)
            seedler = tuple(range(100, 100 + int(argv[i + 1])))
            i += 1
        elif argv[i] == "--ayrinti":
            ayrinti = True
        else:
            adlar.append(argv[i])
        i += 1
    tum = dict(SENARYOLAR, **SENARYOLAR_FAZ2)
    for ad, sc in tum.items():
        if adlar and ad not in adlar:
            continue
        for seed in seedler:
            t0 = gercek_time.time()
            s = calistir(seed=seed, **sc)
            print(satir_ozeti(ad, seed, s), f"({gercek_time.time() - t0:.1f}s)")
            if ayrinti:
                for a in s["ayrinti"][:12]:
                    print("    ", a)
    if not adlar:
        m = terim_mikro()
        print("terim_mikro", {k: v for k, v in m.items() if k != "yanlis"}, "yanlis", m["yanlis"])
        print("ek_uyum_mikro uyusmayan", ek_uyum_mikro())
        a, b = ogrenme_kos(True), ogrenme_kos(False)
        print("ogrenme acik ", {k: v for k, v in a.items() if k != "ogrenilen"}, a["ogrenilen"])
        print("ogrenme kapali", {k: v for k, v in b.items() if k != "ogrenilen"})


if __name__ == "__main__":
    main()
