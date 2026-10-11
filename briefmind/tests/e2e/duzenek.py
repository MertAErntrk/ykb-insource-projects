"""BriefMind uctan uca sentetik duzenek (MIMARI_v3 §8).

Gercek Motor + gercek ses.cumlelere_bol/tekrar_temizle/sonuc_gecerli + gercek Sozluk; sahte STT (klipler),
sahte altyazi, sahte LLM ve sahte saat. Ag yok, repo'ya dosya yazmaz: her calistirma kendi gecici klasorunde
kosar ve bitince klasoru siler. Tum adlar kurgusal.
Kullanim (repo kokunden): python tests/e2e/duzenek.py   (ozet tablo)
"""
import datetime as dt
import difflib
import json
import os
import random
import re
import shutil
import sys
import tempfile
import types

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import llm            # noqa: E402
import motor as M     # noqa: E402
import ses            # noqa: E402
from sozluk import Sozluk, normalize  # noqa: E402

BAS = dt.datetime(2026, 10, 1, 10, 0, 0)
A, B, C, BEN = "Melek Taslak", "Kaan Uydurma", "Sinan Kurgu", "Zeynep Örnek"   # kurgusal adlar
STT_GECIKME = 1.5
SEDLER = (7, 11, 23)

# ---------------- zemin gercegi: (konusmaci, bas_sn, bit_sn, metin) ----------------
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
# Whisper'in yanlis duydugu kelimeler (ham STT) -> dogru (zemin gercegi)
WHISPER_HATA = {"Kubernetes": "kubernetis", "Commit": "komit", "pipeline": "payplayn", "Mikroservisler": "mikro servisler"}

SOZCUK = sorted({w.strip('.,').lower() for c in CUMLELER for w in c.split() if len(w) > 3})

# Senaryo tanimlari (ad -> calistir() parametreleri). Ayni tanimlar testlerde ve ozet dokumunde kullanilir.
SENARYOLAR = {
    "gecikme0": dict(kaynak="ses", gecikme=0.0, kapali=(0, 0)),
    "gecikme2": dict(kaynak="ses", gecikme=2.0, kapali=(0, 0)),
    "altyazi_kapali": dict(kaynak="ses", gecikme=2.0, kapali=(300.0, 420.0)),
    "altyazi_kapali_3_5": dict(kaynak="ses", gecikme=3.5, kapali=(300.0, 420.0)),
    "ikisi": dict(kaynak="ikisi", gecikme=2.0, kapali=(300.0, 420.0)),
    "cakisma_yok": dict(kaynak="ses", gecikme=2.0, kapali=(0, 0), cakisma_orani=0.0, hizli_devir=0.0),
    "sozluksuz": dict(kaynak="ses", gecikme=2.0, kapali=(0, 0), sozluk_dolu=False, duzelt=False),
    "k5_kapali": dict(kaynak="ses", gecikme=2.0, kapali=(0, 0), k5=False),
    "k5_kapali_altyazi_kapali": dict(kaynak="ses", gecikme=2.0, kapali=(300.0, 420.0), k5=False),
    "k5_kapali_tekrar": dict(kaynak="ses", gecikme=2.0, kapali=(0, 0), k5=False, tekrar=True),
    "tekrar": dict(kaynak="ses", gecikme=2.0, kapali=(0, 0), tekrar=True),
}


def senaryo(seed=7, n=72, cakisma_orani=0.15, hizli_devir=0.25, tekrar=False):
    r = random.Random(seed)
    t, son, ut = 2.0, None, []
    kisiler = [A, B, C, BEN]
    for i in range(n):
        kim = r.choice([k for k in kisiler if k != son])
        if tekrar or r.random() < 0.25:
            metin = CUMLELER[r.randrange(len(CUMLELER))]      # terimli sabit cumle (sozluk/duzeltme olcumu)
        else:                                                  # benzersiz cumle (metin benzerligi yaniltmasin)
            metin = " ".join(r.sample(SOZCUK, r.randint(6, 10))).capitalize() + "."
        if r.random() < 0.15:
            metin = r.choice(["Evet, aynen öyle.", "Tamam, anlaştık.", "Peki, ben bakarım."])
        sure = max(1.6, len(metin) / 14.0)
        ut.append({"id": i, "kim": kim, "bas": t, "bit": t + sure, "metin": metin})
        x = r.random()
        if x < cakisma_orani:
            t += sure * 0.5           # sonraki konusmaci araya girer (es zamanli konusma)
        elif x < cakisma_orani + hizli_devir:
            t += sure + 0.3           # sessizlik < CUMLE_SESSIZLIK: VAD iki kisiyi tek klibe koyar
        else:
            t += sure + r.uniform(0.8, 3.0)
        son = kim
    return ut


def whisper_metni(m):
    for d, y in WHISPER_HATA.items():
        m = re.sub(r"(?<!\w)" + re.escape(d) + r"(?!\w)", y, m)
    return m


def klipler(ut):
    """Loopback (uzak konusmacilar) VAD modeli: <0.55 sn bosluk -> ayni klip; cakisan konusmada Whisper baskin
    (uzun) sesi yazar, digerinin sozu kaybolur. Mikrofon (BEN) ayri akis. Klip metni tek segment (ARGE: zaman yok)."""
    uzak = [u for u in ut if u["kim"] != BEN]
    out, cur = [], None
    for u in uzak:
        if cur and u["bas"] < cur["bit"] + ses.CUMLE_SESSIZLIK and (max(cur["bit"], u["bit"]) - cur["bas"]) <= ses.ZORLA_SN:
            if u["bas"] < cur["bit"]:                      # es zamanli: kisa olan kaybolur
                onceki = cur["utler"][-1]
                if (u["bit"] - u["bas"]) > (onceki["bit"] - onceki["bas"]):
                    cur["utler"][-1] = u
                    cur["kayip"].append(onceki["id"])
                else:
                    cur["kayip"].append(u["id"])
            else:
                cur["utler"].append(u)
            cur["bit"] = max(cur["bit"], u["bit"])
            continue
        cur = {"akis": "hoparlor", "bas": u["bas"], "bit": u["bit"], "utler": [u], "kayip": []}
        out.append(cur)
    for u in ut:
        if u["kim"] == BEN:
            out.append({"akis": "mikrofon", "bas": u["bas"], "bit": u["bit"], "utler": [u], "kayip": []})
    for k in out:
        k["metin"] = " ".join(whisper_metni(u["metin"]) for u in k["utler"])
    return out


def bozuk_altyazi(m, r, oran=0.35):
    k = m.split()
    for i in range(len(k)):
        if r.random() < oran:
            w = k[i]
            k[i] = (w[:-2] + "ler") if len(w) > 4 and r.random() < 0.5 else (w[::-1] if len(w) < 5 else w[:3])
    return " ".join(k)


def altyazilar(ut, gecikme, kapali, r):
    out = []
    for u in ut:
        ts = u["bas"] + gecikme
        if kapali[0] <= u["bas"] < kapali[1]:
            continue
        out.append({"varis": ts + 1.0, "ts": ts, "speaker": u["kim"], "text": bozuk_altyazi(u["metin"], r), "uid": u["id"]})
    return out


def ts_str(sn):
    return (BAS + dt.timedelta(seconds=sn)).strftime("%H:%M:%S")


# ---------------- sahte LLM ----------------
class SahteLLM:
    def __init__(self):
        self.gorevler = []

    def __call__(self, mesajlar, max_tokens, sema=None, effort="medium", temperature=0.6, dusunme=True):
        g = llm._gorev_adi(mesajlar)
        self.gorevler.append(g)
        if g == "duzeltme":
            # biri dogru (kubernetis), biri zararli (genel regex ile her satira uygulanir)
            return json.dumps({"duzeltmeler": [{"orijinal": "kubernetis", "duzeltilmis": "Kubernetes"},
                                                {"orijinal": "test ortamı", "duzeltilmis": "test ortamı (UAT)"}]},
                              ensure_ascii=False), "stop"
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


def kelime_fark(a, b):
    x, y = a.split(), b.split()
    sm = difflib.SequenceMatcher(None, x, y)
    return sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal")


def _ortam_kaydet():
    return {"time": M.time, "K5_UST": M.K5_UST, "PARCA_TOKEN": M.PARCA_TOKEN, "DUZELT_MIN_TOKEN": M.DUZELT_MIN_TOKEN,
            "sor": llm.sor, "token_say": llm.token_say, "BAGLAM_PENCERESI": llm.BAGLAM_PENCERESI,
            "BUTCE_AYAR": llm.BUTCE_AYAR}


def _ortam_geri_yukle(e):
    M.time = e["time"]
    M.K5_UST = e["K5_UST"]
    M.PARCA_TOKEN = e["PARCA_TOKEN"]
    M.DUZELT_MIN_TOKEN = e["DUZELT_MIN_TOKEN"]
    llm.sor = e["sor"]
    llm.token_say = e["token_say"]
    llm.BAGLAM_PENCERESI = e["BAGLAM_PENCERESI"]
    llm.BUTCE_AYAR = e["BUTCE_AYAR"]


def calistir(kaynak="ses", gecikme=2.0, kapali=(300.0, 420.0), seed=7, sozluk_dolu=True, duzelt=True,
             cakisma_orani=0.15, hizli_devir=0.25, k5=True, tekrar=False):
    """Tek senaryo. Her cagri kendi gecici klasorunde kosar (cwd gecici klasore alinir, bitince geri doner ve
    klasor silinir); modul genel durumu (saat, LLM, esikler, config) cagri sonunda eski haline doner."""
    gecici = tempfile.mkdtemp(prefix="bm_e2e_")
    onceki_cwd = os.getcwd()
    eski = _ortam_kaydet()
    m = None
    try:
        os.chdir(gecici)
        # config.json (yerel) baglam penceresi/butce gecersiz kilmalarini sabitle: sonuclar makineye bagli olmasin
        llm.BAGLAM_PENCERESI = llm.VARSAYILAN_PENCERE
        llm.BUTCE_AYAR = {}
        r = random.Random(seed + 1)
        ut = senaryo(seed, cakisma_orani=cakisma_orani, hizli_devir=hizli_devir, tekrar=tekrar)
        kl = klipler(ut)
        alt = altyazilar(ut, gecikme, kapali, r)
        saat = {"t": 0.0}
        M.time = types.SimpleNamespace(time=lambda: 1_000_000.0 + saat["t"])
        llm.token_say = llm.token_tahmin
        sahte = SahteLLM()
        llm.sor = sahte
        M.PARCA_TOKEN, M.DUZELT_MIN_TOKEN = 500, 200
        M.K5_UST = 3 if k5 else 0
        soz = Sozluk(os.path.join(gecici, "sozluk.json"))
        if sozluk_dolu:
            for t in ("Kubernetes", "teminat", "mikroservis", "kampanya", "pipeline"):
                soz.terim_ekle(t)
            soz.alias_ekle("komit", "commit")
            soz.alias_ekle("payplayn", "pipeline")
        kl_dir = M.yeni_klasor(f"e2e {kaynak} {gecikme} {seed}", BAS.date())
        m = M.Motor(kl_dir, "E2E", BAS.date(), soz, [A, B, C, BEN], duzelt=duzelt, kaynak=kaynak, ben=BEN)
        # olay kuyrugu: (varis_sn, tur, veri)
        olaylar = []
        for k in kl:
            sure = k["bit"] - k["bas"]
            for gorel, ham, s in ses.cumlelere_bol([{"id": 0, "text": k["metin"], "tokens": [], "temperature": 0.0}], sure):
                metin = ses.tekrar_temizle(ham)
                if len(metin) < 2 or not ses.sonuc_gecerli(s, metin):
                    continue
                olaylar.append((k["bit"] + STT_GECIKME, "ses", (ts_str(k["bas"] + gorel),
                                "ben" if k["akis"] == "mikrofon" else None, metin, k["akis"])))
        for a in alt:
            olaylar.append((a["varis"], "alt", {"ts": ts_str(a["ts"]), "speaker": a["speaker"], "text": a["text"]}))
        olaylar.sort(key=lambda x: x[0])
        for varis, tur, v in olaylar:
            saat["t"] = varis
            if tur == "ses":
                m.ses_satiri(*v)
            else:
                m.altyazi_satiri(v)
            m.kontrol()
        saat["t"] += 30
        m.kontrol()
        m.bitir()
        not_md = m.notu_uret()
        satirlar = m.tum_satirlar()
        m.kapat()
        m = None
        return olcum(ut, kl, satirlar, not_md, sahte, kaynak, gecikme, kapali)
    finally:
        if m is not None:
            try:
                m.kapat()
            except Exception:
                pass
        _ortam_geri_yukle(eski)
        os.chdir(onceki_cwd)
        shutil.rmtree(gecici, ignore_errors=True)


def olcum(ut, kl, satirlar, not_md, sahte, kaynak, gecikme, kapali):
    """Her final satiri zemin gercegine esle: ayni zaman penceresinde (±12 sn) en cok kelime ortusen utterance."""
    def kok(s):
        return {w[:5] for w in normalize(s).split() if len(w) >= 3}
    kayip_ut = {i for k in kl for i in k["kayip"]}
    eslesen = {}
    sonuc = {"satir": len(satirlar), "dogru": 0, "yanlis": 0, "soru": 0, "altyazi_metni": 0, "satir_ayrinti": []}
    for s in satirlar:
        t = (dt.datetime.combine(BAS.date(), dt.time.fromisoformat(s["ts"])) - BAS).total_seconds()
        raw = s.get("raw") or s["text"]
        aday = [(len(kok(raw) & kok(u["metin"])) / max(1, len(kok(u["metin"]))), -abs(u["bas"] - t), u)
                for u in ut if abs(u["bas"] - t) <= 12]
        aday.sort(key=lambda x: (x[0], x[1]), reverse=True)
        u = aday[0][2] if aday and aday[0][0] > 0 else None
        if s.get("kaynak", "").startswith("altyazi"):
            sonuc["altyazi_metni"] += 1
        if u is None:
            sonuc["satir_ayrinti"].append(("ESLESMEDI", s["ts"], s["speaker"], raw[:50]))
            continue
        eslesen.setdefault(u["id"], []).append(s)
        s["_gt"] = u
        if s["speaker"] == "?":
            sonuc["soru"] += 1
        elif s["speaker"] == u["kim"]:
            sonuc["dogru"] += 1
        else:
            sonuc["yanlis"] += 1
            sonuc["satir_ayrinti"].append(("YANLIS", s["ts"], f"{s['speaker']}!={u['kim']}", s.get("kaynak"), raw[:45]))
    atanmis = sonuc["dogru"] + sonuc["yanlis"] + sonuc["soru"]
    sonuc["konusmaci_hata_orani"] = round((sonuc["yanlis"] + sonuc["soru"]) / max(1, atanmis), 3)
    sonuc["yanlis_orani"] = round(sonuc["yanlis"] / max(1, atanmis), 3)
    sonuc["utterance"] = len(ut)
    sonuc["eksik_utterance"] = sorted(set(u["id"] for u in ut) - set(eslesen))
    sonuc["eksik_cakisma_kaybi"] = sorted(kayip_ut - set(eslesen))
    sonuc["cift_utterance"] = sorted(i for i, v in eslesen.items() if len(v) > 1)
    # kelime degisimi: ham (Whisper) -> final metin; zemin gercegi ile
    ham_wer = son_wer = bozulan = duzelen = 0
    gt_kelime = 0
    for s in satirlar:
        if s.get("kaynak", "").startswith("altyazi"):
            continue
        raw = s.get("raw") or s["text"]
        u = s.get("_gt")
        if u is None:
            continue
        gt = u["metin"]
        g, h, f = normalize(gt), normalize(raw), normalize(s["text"])
        gt_kelime += len(g.split())
        a, b = kelime_fark(g, h), kelime_fark(g, f)
        ham_wer += a
        son_wer += b
        if b > a:
            bozulan += 1
            sonuc["satir_ayrinti"].append(("METIN_BOZULDU", s["ts"], raw[:60], "->", s["text"][:70]))
        elif b < a:
            duzelen += 1
    sonuc.update({"gt_kelime": gt_kelime, "ham_kelime_hatasi": ham_wer, "son_kelime_hatasi": son_wer,
                  "satir_metni_bozulan": bozulan, "satir_metni_duzelen": duzelen,
                  "llm_gorevleri": {g: sahte.gorevler.count(g) for g in set(sahte.gorevler)},
                  "not_uzunluk": len(not_md), "not_soru_isareti_konusmaci": not_md.count("| ? |"),
                  "ayar": {"kaynak": kaynak, "gecikme": gecikme, "altyazi_kapali": kapali}})
    return sonuc


# ---------------- sozluk mikro olcumu (sozluk_mikro.py) ----------------
SOZLUK_MIKRO_ORNEK = ["Teminatı hesapla", "kampanyayı duyur", "kampanyalar", "mikroservisler yavaş", "müşteriler geldi",
                      "müşterinin talebi", "raporu gönder", "Krediyi onayla", "kredisi var", "Jenkinse bak", "kubernetis kümesi",
                      "komit attım", "komite toplandı", "pipelinelar", "teminatlar", "terminal açık", "kampanyacı", "Kubernetesi"]
# Bilinen zarar: bu sozcukler degismemeli (komite != commit, kampanyacı != kampanya)
SOZLUK_MIKRO_ZARAR = {"komite", "kampanyacı"}


def sozluk_mikro():
    """18 ornek cumlede sozluk uygulamasi. Dondurur: {'toplam', 'degisen', 'zarar': [(eski, yeni), ...]}."""
    gecici = tempfile.mkdtemp(prefix="bm_mikro_")
    try:
        s = Sozluk(os.path.join(gecici, "s.json"))
        for t in ("teminat", "kampanya", "mikroservis", "Kubernetes", "pipeline", "Kredi", "müşteri", "rapor", "Jenkins", "SQL"):
            s.terim_ekle(t)
        s.alias_ekle("komit", "commit")
        degisen, zarar = 0, []
        for o in SOZLUK_MIKRO_ORNEK:
            _y, d = s.uygula(o)
            if d:
                degisen += 1
            zarar.extend((eski, yeni) for eski, yeni in d if eski.lower() in SOZLUK_MIKRO_ZARAR)
        return {"toplam": len(SOZLUK_MIKRO_ORNEK), "degisen": degisen, "zarar": zarar}
    finally:
        shutil.rmtree(gecici, ignore_errors=True)


if __name__ == "__main__":
    for ad, sc in SENARYOLAR.items():
        for seed in SEDLER:
            s = calistir(seed=seed, **sc)
            print(f"{ad:26} seed={seed:<3} satir={s['satir']:<3} dogru={s['dogru']:<3} yanlis={s['yanlis']} "
                  f"?={s['soru']} SER={s['konusmaci_hata_orani']} eksik={len(s['eksik_utterance'])} "
                  f"cift={len(s['cift_utterance'])} altyazi_metni={s['altyazi_metni']} "
                  f"kelime_hata ham/son={s['ham_kelime_hatasi']}/{s['son_kelime_hatasi']} (gt {s['gt_kelime']}) "
                  f"bozulan={s['satir_metni_bozulan']}")
    print("sozluk_mikro", sozluk_mikro())
