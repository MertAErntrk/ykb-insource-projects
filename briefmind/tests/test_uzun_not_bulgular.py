"""Faz 3 / Faz 4 bulgulari (otomatik not, inceleme yok: belirsiz olan kesin gorunmez). Saf mantik + kucuk Motor; ag, ses
cihazi, LLM yok; adlar kurgusal.
  1) kisa ile uzun pencerenin uyusmadigi kelime / sayi / ad notta ⟨⟩: sahip kesin olmaz, Jira Assignee bos, sayi ⟨⟩
     icindeyse madde atilir (satir verisi duz kalir)
  2) uzun pencerenin kisada karsiligi olmayan uzun eklemesi (uydurma cumle) satira yazilmaz
  3) transkriptte hic gecmeyen (uydurma) tutar / tarih ozette ve konu akisinda '(?)', sonraki adimda cumle duser
  4) ozette '-acagini soyledi' / '-iyor' (tablodaki maddeyle eslesen) is cumlesi kesin sahip kuralina tabi
  5) madde sonundaki kaynak damgasi (⏱HH:MM:SS) zorunlu deger sayilmaz
  6) canli 'canli not' paneline giden bolum ozeti dogrulanir
  7) iki klibin sinirindaki eklemede cumle sonu noktalamasi tasiyan kelime sol klibe (onceki konusmaci) yazilir"""
import csv
import datetime as dt
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import llm
import motor as motor_mod
import not_araclari
import saat
import uzun
from motor import Motor

KAAN, SINAN, MELEK = "Kaan Uydurma", "Sinan Kurgu", "Melek Taslak"
SELIN, EMRE = "Selin Kara", "Emre Tan"
KISILER = [KAAN, SINAN, MELEK]
T0 = 1_760_000_000.0


def sat(kim, metin, guven=0.9, **kw):
    s = {"ts": "10:00:00", "speaker": kim, "text": metin, "raw": metin, "spk_guven": guven, "cakisma": False,
         "devir": False}
    s.update(kw)
    return s


def aks(madde, sorumlu="belirsiz"):
    return {"madde": madde, "sorumlu": sorumlu, "tarih": "-"}


def _jira(aksiyonlar, tmp_path):
    yol = str(tmp_path / "jira.csv")
    not_araclari.jira_csv(aksiyonlar, yol)
    with open(yol, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _bolum(md, baslik):
    return md.split(f"## {baslik}\n", 1)[1].split("\n## ", 1)[0].strip()


# ------------------------------------------------------------------ 1) kisa / uzun ayrismasi notta ⟨⟩

def test_uzun_belirsiz_kelime_nota_giden_metinde_sarmal_satir_verisi_duz():
    s = sat(MELEK, "Kaan, sen mabamo siride kopevo 178 için yarın gönder.", uzun_belirsiz=["Kaan"],
            raw_kisa="Sinan, sen mabamo siride kopevo 178 için yarın gönder.")
    assert motor_mod.satir_metni(s) == "⟨Kaan⟩, sen mabamo siride kopevo 178 için yarın gönder."
    assert "⟨" not in s["text"] and "⟨" not in s["raw"]                     # veri duz: f3_sarmal kapisi
    assert motor_mod.blok_metni([s]) == f"{MELEK}: ⟨Kaan⟩, sen mabamo siride kopevo 178 için yarın gönder."
    # ardisik isaretli kelimeler tek ⟨⟩; noktalama disarida; cakisma satirinda cift isaret yok
    assert motor_mod.belirsiz_isaretle("Gerisi zekotu bafira, tamam.", ["zekotu", "bafira"]) == \
        "Gerisi ⟨zekotu bafira⟩, tamam."
    c = sat(f"{KAAN} / {SINAN}", "Kaan ben bakarım", cakisma=True, uzun_belirsiz=["Kaan"])
    assert motor_mod.satir_metni(c) == "⟨Kaan ben bakarım⟩"


def test_hitap_adi_kisa_ile_uzunda_farkliysa_sahip_kesin_degil_jira_bos(tmp_path):
    """Bulgu (e1e): kisa 'Sinan, sen ...', uzun 'Kaan, sen ...'; uzun metin kazanir, 'Kaan' yalniz belirsiz listesindeydi
    ve aksiyon 'Kaan Uydurma kesin', Jira Assignee Kaan oluyordu."""
    madde = "mabamo siride kopevo 178 için yarın gönder"
    temiz = sat(MELEK, "Kaan, sen mabamo siride kopevo 178 için yarın gönder.")
    a = motor_mod.sorumlu_dogrula([aks(madde, KAAN)], [temiz], katilimcilar=KISILER)[0]
    assert a["sahip"] == {"ad": KAAN, "durum": "kesin"}                      # kontrol: ayrisma yoksa kesin
    ayrik = dict(temiz, uzun_belirsiz=["Kaan"], raw_kisa="Sinan, sen mabamo siride kopevo 178 için yarın gönder.")
    a = motor_mod.sorumlu_dogrula([aks(madde, KAAN)], [ayrik], katilimcilar=KISILER)[0]
    assert a["sahip"]["durum"] == "belirsiz" and a["sorumlu"] == f"{KAAN} (?)"
    satir = _jira([a], tmp_path)[0]
    assert satir["Assignee"] == "" and "sahibi belirsiz" in satir["Description"]
    # model sahibi yazmadiysa hitaptan aday bile cikmaz (ad ⟨⟩ icinde)
    a = motor_mod.sorumlu_dogrula([aks(madde)], [ayrik], katilimcilar=KISILER)[0]
    assert a["sahip"]["durum"] != "kesin" and KAAN not in a["sorumlu"]


def test_kisa_ile_uzun_sayi_ayrismasi_aksiyonu_kesin_birakmaz(tmp_path):
    """Bulgu (e1d): kisa klip '231', uzun pencere '132' duyar; uzun.pencere_hizala sayi kaybinda kisa metni birakir, 231
    yalniz 'belirsiz'deydi ve not / Jira / e-postada kesin kaliyordu. Artik ⟨231⟩: madde dayanak kontrolunde atilir."""
    kisa = "Ben mulevi vobopu gabonu 231 için hazırlarım."
    r = uzun.pencere_hizala("Ben mulevi vobopu gabonu 132 için hazırlarım.", [{"klip_no": 1, "bas": 0, "bit": 4,
                                                                             "text": kisa}])["klipler"][0]
    assert r["text"] is None and "231" in r["belirsiz"]                      # sayi kaybi: kisa kalir
    s = sat(KAAN, kisa, uzun_belirsiz=r["belirsiz"])
    assert "⟨231⟩" in motor_mod.satir_metni(s)
    transkript = (f"{SINAN}: Günaydın herkese, toplantıyı başlatıyoruz, gündemde kayıtlar var.\n"
                  + motor_mod.blok_metni([s]) + "\n")
    md = "# T\n\n" + llm.not_markdown({"ozet": "Kayıtlar konuşuldu.", "sonraki_adim": "-"},
                                      {"kararlar": [], "acik_sorular": [],
                                       "aksiyonlar": motor_mod.sorumlu_dogrula(
                                           [aks("mulevi vobopu gabonu 231 için hazırlarım", KAAN)], [s],
                                           katilimcilar=KISILER)})
    yeni, atilan = motor_mod.dayanak_kontrolu(md, transkript)
    assert any("231" in x for x in atilan)
    kalan = not_araclari.aksiyonlari_ayikla(yeni)
    assert not [a for a in kalan if "231" in a["madde"]]
    assert not [x for x in _jira(kalan, tmp_path) if "231" in x["Summary"] and x["Assignee"]]
    # ayrisma yoksa ayni madde kesin kalir (kontrol)
    duz = sat(KAAN, kisa)
    t2 = transkript.replace(motor_mod.blok_metni([s]), motor_mod.blok_metni([duz]))
    yeni, atilan = motor_mod.dayanak_kontrolu(md, t2)
    assert atilan == [] and "231" in yeni


# ------------------------------------------------------------------ 2) uzun pencerenin uydurma eklemesi

def test_uzun_pencere_kuyruga_ekledigi_cumle_satira_girmez():
    """Bulgu (e9 / e9b): Whisper uzun pencerenin sessiz kuyruguna akici bir cumle ekler; kisa klipler temiz. Ekleme klip
    oranini esigin altina indirmedigi icin satira (kesin konusmaci) giriyordu."""
    k = [{"klip_no": 1, "bas": 0, "bit": 4, "text": "Kodi kesas bota zale kelan 132 için hazırlarım."},
         {"klip_no": 2, "bas": 4.3, "bit": 9, "text": "Retener banala kayam meyas ralasen mayam sabek samam daradas."}]
    uzun_metin = ("Kodi kesas bota zale kelan 132 için hazırlarım. Retener banala kayam meyas ralasen mayam sabek "
                  "samam daradas. Gerisi zekotu bafira mudeli 901 için ben hazırlarım.")
    s = uzun.pencere_hizala(uzun_metin, k)
    metin = " ".join(r["text"] or "" for r in s["klipler"])
    assert "zekotu" not in metin and "901" not in metin
    assert s["atilan_ekleme"] == 8
    # kisa (1-2 belirtec) sinir eklemesi -- yarim duyulan kelime -- hala yazilir
    s = uzun.pencere_hizala("Kodi kesas bota zale kelan modas. Retener banala kayam meyas ralasen mayam sabek.",
                            [{"klip_no": 1, "bas": 0, "bit": 4, "text": "Kodi kesas bota zale kelan"},
                             {"klip_no": 2, "bas": 4.3, "bit": 9,
                              "text": "Retener banala kayam meyas ralasen mayam sabek."}])
    assert s["atilan_ekleme"] == 0
    assert " ".join(r["text"] or "" for r in s["klipler"]).count("modas") == 1
    # klip icindeki uzun ekleme de (kisa klip duymadi) yazilmaz
    s = uzun.pencere_hizala("bir iki üç bu konuyu sonra konuşuruz dört beş altı yedi",
                            [{"klip_no": 1, "bas": 0, "bit": 6, "text": "bir iki üç dört beş altı yedi"}])
    r = s["klipler"][0]
    assert r["text"] in (None, "bir iki üç dört beş altı yedi") and s["atilan_ekleme"] == 4


# ------------------------------------------------------------------ 7) sinir eklemesi komsu konusmaciya gocmez

def test_sinir_eklemesinde_cumle_sonu_kelimesi_sol_klibe_yazilir():
    """Bulgu (e3 / uzun_surekli 11): Melek'in 'kelan modas.' cumlesinin son kelimesi kisa yolda kayboldu; uzun pencere onu
    klip sinirina ekledi ve tam sinir ortasina dusen kelime (kayan nokta) sagdaki Sinan'in kesin satirina yazildi."""
    # sag klibin basinda bos yer var: zaman kurali kelimeyi saga (Sinan) yazardi; cumle sonu noktalamasi sola (Melek) yazar
    k = [{"klip_no": 1, "bas": 0.0, "bit": 3.0, "text": "kesas zale kelan"},
         {"klip_no": 2, "bas": 3.0, "bit": 9.0, "text": "Retener banala kayam"}]
    s = uzun.pencere_hizala("kesas zale kelan modas. Retener banala kayam", k)
    a, b = s["klipler"]
    assert a["text"] == "kesas zale kelan modas." and b["text"] in (None, "Retener banala kayam")
    s = uzun.pencere_hizala("kesas zale kelan modas Retener banala kayam", k)
    assert s["klipler"][1]["text"] == "modas Retener banala kayam"          # noktalamasiz: zaman kurali
    # noktalamasiz tam sinir ortasi (kayan nokta: tahmini an sinirdan 1e-17 buyuk cikar): sol
    k = [{"klip_no": 1, "bas": -2.98, "bit": 0.02, "text": "kesas zale kelan"},
         {"klip_no": 2, "bas": 0.02, "bit": 3.02, "text": "Retener banala kayam"}]
    s = uzun.pencere_hizala("kesas zale kelan modas Retener banala kayam", k)
    assert s["klipler"][0]["text"] == "kesas zale kelan modas"
    # noktalama sagdaki kelimelerde degilse zaman kurali gecerli: sag klibin basindaki bos yer -> sag
    k2 = [{"klip_no": 1, "bas": 0.0, "bit": 4.0, "text": "bir iki üç dört beş altı yedi sekiz"},
          {"klip_no": 2, "bas": 4.2, "bit": 8.0, "text": "dokuz on"}]
    s = uzun.pencere_hizala("bir iki üç dört beş altı yedi sekiz sıfır dokuz on", k2)
    assert s["klipler"][1]["text"] == "sıfır dokuz on"


# ------------------------------------------------------------------ 3) uydurma deger

ZEMIN = (f"{SELIN}: Günaydın herkese, toplantıyı başlatıyoruz, gündemde bütçe ve teslim var.\n"
         f"{EMRE}: Bütçe tablosunu ben cuma günü güncellerim, teslim planı da konuşulsun.\n"
         f"{SELIN} / {EMRE} (aynı anda): ⟨Teklif 950 bin lira olarak onaylandı⟩\n")


def _son(ozet, sonraki="-", konu="", aksiyonlar=(), transkript=ZEMIN):
    genel = {"ozet": ozet, "sonraki_adim": sonraki}
    if konu:
        genel["konu_akisi"] = konu
    satirlar = []
    for ln in transkript.strip().split("\n"):
        kim, metin = ln.split(": ", 1)
        if "(aynı anda)" not in kim:
            satirlar.append(sat("?" if kim == "Bilinmeyen" else kim, metin, 0.0 if kim == "Bilinmeyen" else 0.9))
    aksiyonlar = motor_mod.sorumlu_dogrula([dict(a) for a in aksiyonlar], satirlar, katilimcilar=[SELIN, EMRE])
    md = "# T\n\n" + llm.not_markdown(genel, {"kararlar": [], "aksiyonlar": aksiyonlar, "acik_sorular": []})
    md, _ = motor_mod.dayanak_kontrolu(md, transkript)
    return motor_mod.not_son_denetim(md, transkript, [SELIN, EMRE])


def test_transkriptte_hic_gecmeyen_tutar_ve_tarih_ozette_kesin_kalmaz():
    """Bulgu (e8): 'Toplam proje bütçesi 975 bin lira olarak onaylandı ve teslim 27 Kasım perşembe.' -- 975 / 27 / kasım
    transkriptte HIC yok; yalniz ⟨⟩ icindeki deger '(?)' oluyor, uydurma deger kesin kaliyordu."""
    md, olaylar = _son("Bütçe tablosu konuşuldu. Toplam bütçe 975 bin lira olarak onaylandı ve teslim 27 Kasım.",
                       sonraki="Bütçe tablosu cuma günü güncellenecek. Teklif 975 bin lira için 27 Kasım hazırlanacak.",
                       konu="- 10:00:00–10:05:00: bütçe tablosu; 975 bin lira teklif")
    ozet = _bolum(md, "Özet")
    assert "975" not in ozet and "27" not in ozet and "Kasım" not in ozet and "(?) bin lira" in ozet
    assert _bolum(md, "Konu akışı") == "- 10:00:00–10:05:00: bütçe tablosu; (?) bin lira teklif"   # saat araligi kalir
    sonraki = _bolum(md, "Bir sonraki adım")
    assert "975" not in sonraki and "Kasım" not in sonraki and "cuma" in sonraki
    assert "975" not in md and any("transkriptte yok" in o for o in olaylar)
    # birim tekrari: yalniz ⟨⟩ icindeki 950 de (?) (eski davranis korunur)
    md, _ = _son("Teklif 950 bin lira olarak ele alındı.")
    assert _bolum(md, "Özet") == "Teklif (?) bin lira olarak ele alındı."
    # kesin zeminde gecen deger, kaynak damgasi, ISO tarih ve notun kendi sayimi isaretlenmez
    md, _ = _son("Bütçe tablosu cuma günü 2026-10-16 için güncellenecek ⏱10:12:03; 3 madde konuşuldu.")
    assert _bolum(md, "Özet") == "Bütçe tablosu cuma günü 2026-10-16 için güncellenecek ⏱10:12:03; 3 madde konuşuldu."


# ------------------------------------------------------------------ 4) ozette is cumlesi tespiti

def test_ozette_acagini_soyledi_kalibi_belirsiz_kaynakli_isi_kisiye_kesin_atfetmez():
    """Bulgu: 'Selin Kara müşteri raporunu cuma günü hazırlayacağını söyledi.' -- is 'Bilinmeyen' satirindan, tabloda sahibi
    belirsiz; _IS_IFADESI '-acagini'yi kacirdigi icin cumle kesin kaliyor, ozet her e-postaya gidiyordu."""
    t = (f"{SELIN}: Müşteri raporunu konuşalım, cuma teslim edilmesi gerekiyor.\n"
         "Bilinmeyen: Müşteri raporunu ben cuma günü hazırlarım.\n"
         f"{EMRE}: Bütçe tablosunu ben güncellerim, sonra paylaşırım.\n")
    for cumle in (f"{SELIN} müşteri raporunu cuma günü hazırlayacağını söyledi.",
                  f"{SELIN} müşteri raporunu cuma günü hazırlıyor.",
                  f"Müşteri raporunu {SELIN} hazırlasın.",
                  f"{SELIN}'in müşteri raporunu hazırlaması gerekiyor."):
        md, olaylar = _son(cumle, aksiyonlar=[aks("Müşteri raporunu hazırla")], transkript=t)
        ozet = _bolum(md, "Özet")
        assert "Selin" not in ozet and "(?)" in ozet, (cumle, ozet)
        assert any("kişi adı çıkarıldı" in o for o in olaylar)
    # kesin sahipli is: ad kalir (kontrol)
    md, _ = _son(f"{EMRE} bütçe tablosunu güncelleyeceğini söyledi.", aksiyonlar=[aks("Bütçe tablosunu güncelle", EMRE)],
                 transkript=t)
    assert _bolum(md, "Özet") == f"{EMRE} bütçe tablosunu güncelleyeceğini söyledi."
    assert motor_mod._is_cumlesi("Ayşe raporu hazırlayacağını söyledi.", set(), [])
    assert motor_mod._is_cumlesi("Raporu Ayşe hazırlasın.", set(), [])
    assert not motor_mod._is_cumlesi("Ayşe sunumu anlattı.", motor_mod._kokler("sunumu anlattı"), [])


# ------------------------------------------------------------------ 5) kaynak damgasi zorunlu deger degil

def test_karar_maddesindeki_kaynak_damgasi_zorunlu_deger_sayilmaz():
    """Bulgu (not_faz4 / 7): '... 607 bin lira olarak kararlaştırıldı ⏱08:53:56' maddesinde 08/53/56 zorunlu deger
    sayiliyordu: her karar '(?)' aliyor; 53 / 56 bir ⟨⟩ satirinda gecince dayanakli karar siliniyordu."""
    t = (f"{SELIN}: Günaydın herkese, toplantıyı başlatıyoruz, gündemde bütçe kalemleri var.\n"
         f"{EMRE}: Bütçe 607 bin lira olarak onaylandı, kalem kapandı.\n")
    t_sarmal = t + f"{SELIN} / {EMRE} (aynı anda): ⟨kalem 53 ile 56 sonra konuşulur⟩\n"

    def kararlar(md):
        return _bolum(md, "Kararlar")
    for tr in (t, t_sarmal):
        damgali = "# T\n\n## Kararlar\n- Bütçe 607 bin lira olarak onaylandı ⏱10:53:56\n\n## Aksiyonlar\n-\n"
        damgasiz = "# T\n\n## Kararlar\n- Bütçe 607 bin lira olarak onaylandı\n\n## Aksiyonlar\n-\n"
        y1, at1 = motor_mod.dayanak_kontrolu(damgali, tr)
        y2, at2 = motor_mod.dayanak_kontrolu(damgasiz, tr)
        assert at1 == at2 == []
        assert kararlar(y1) == "- Bütçe 607 bin lira olarak onaylandı ⏱10:53:56"
        assert kararlar(y2) == "- Bütçe 607 bin lira olarak onaylandı"
    # gercekten eksik deger: (?) damganin ONUNE yazilir, damga korunur
    y, _ = motor_mod.dayanak_kontrolu("# T\n\n## Kararlar\n- Bütçe 708 bin lira olarak onaylandı ⏱10:53:56\n", t)
    assert kararlar(y) == "- Bütçe 708 bin lira olarak onaylandı (?) ⏱10:53:56"


# ------------------------------------------------------------------ 6) canli bolum ozeti dogrulanir

@pytest.fixture
def sanal():
    s = {"t": T0}
    geri = saat.sanal_kur(lambda: s["t"])
    yield s
    geri()


def test_canli_bolum_ozeti_sahibi_ve_kararlari_dogrulanmis_gosterir(tmp_path, sanal, monkeypatch):
    """Bulgu (e6): 'parca_ozetlendi' olayi llm.bolum_ozetle ciktisini ham yayiyordu: canli panel 'Aksiyon: ... — Sinan
    Kurgu' (kaynak 'Bilinmeyen'), ⟨⟩ kaynakli karar ve uydurma sahip kesin gorunuyordu."""
    olaylar = []
    m = Motor(str(tmp_path / "t"), "Canlı", dt.date(2026, 10, 11), None, KISILER, duzelt=False,
              olay=lambda t, v: olaylar.append((t, v)))
    satirlar = [sat(SINAN, "Günaydın herkese, toplantıyı başlatıyoruz, gündemde kayıtlar var."),
                sat("?", "Dirape bamode dumazu 100 için ben hazırlarım.", 0.0),
                sat(KAAN, "Ben ruseno padoge 981 için güncellerim."),
                sat(f"{SINAN} / {MELEK}", "Teklif 750 bin lira olarak onaylandı.", 0.5, cakisma=True),
                sat(MELEK, "Kayıt 120 için bütçe 620 bin lira olarak onaylandı.")]
    ham = {"ozet": "Kayıtlar konuşuldu; teklif 750 bin lira ele alındı. Sinan Kurgu kayıt 100 için hazırlayacak.",
           "konular": ["kayıtlar"], "acik_sorular": [], "belirsiz_terimler": [],
           "kararlar": ["Teklif 750 bin lira olarak onaylandı", "Kayıt 120 için bütçe 620 bin lira olarak onaylandı",
                        "Kayıt 130 için bütçe 999 bin lira olarak onaylandı"],
           "aksiyonlar": [{"madde": "dirape bamode dumazu 100 için hazırla", "sorumlu": SINAN, "tarih": "-"},
                          {"madde": "ruseno padoge 981 için güncelle", "sorumlu": KAAN, "tarih": "-"}]}
    monkeypatch.setattr(llm, "bolum_ozetle", lambda *a, **k: {**ham, "aksiyonlar": [dict(x) for x in ham["aksiyonlar"]]})
    veri = {"sira": 1, "token": 50, "satirlar": satirlar, "duzeltmeler": [], "ozet": None, "baslangic": "10:00:00",
            "bitis": "10:05:00"}
    m._parca_veri[1] = veri
    sonuc = m._isle(veri, False)
    assert sonuc["aksiyonlar"][0]["sorumlu"] == SINAN                     # parca dosyasindaki ham ozet degismez
    o = [v for t, v in olaylar if t == "parca_ozetlendi"][-1]["ozet"]
    sahip = {a["madde"]: a["sorumlu"] for a in o["aksiyonlar"]}
    assert sahip["dirape bamode dumazu 100 için hazırla"] == f"{SINAN} (?)"   # kaynak 'Bilinmeyen'
    assert sahip["ruseno padoge 981 için güncelle"] == KAAN                   # kesin kaynak kesin kalir
    assert "Teklif 750 bin lira olarak onaylandı" not in o["kararlar"]         # yalniz ⟨⟩ (cakisma) satirindan
    assert "Kayıt 120 için bütçe 620 bin lira olarak onaylandı" in o["kararlar"]
    assert "Kayıt 130 için bütçe 999 bin lira olarak onaylandı" not in o["kararlar"]
    assert "750" not in o["ozet"] and "Sinan Kurgu" not in o["ozet"] and o["dogrulandi"] is True
