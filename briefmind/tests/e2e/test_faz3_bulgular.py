"""Faz 3 + Faz 4 bulgulari uctan uca (duzenek: gercek Motor, Konusmaci, SesServisi + ses.UzunPencere, not hatti; sahte STT /
LLM / saat; ag yok). Otomatik not, inceleme adimi yok: bilinen bir belirsizlik notta, Jira'da, e-postada ya da canli panelde
kesin gorunmez.
  - kisa klip ile uzun pencere sayi / hitap adinda ayrisir -> o maddenin kesin sahibi ve Jira Assignee'si yok
  - uzun pencere sessiz kuyruga akici uydurma cumle ekler -> satira, nota, Jira'ya girmez
  - LLM ozete transkriptte hic gecmeyen tutar / tarih yazar -> not.md ve e-postada kesin degil
  - canli 'canli not' paneli (parca_ozetlendi) son notun kesinlestirmediği sahibi / karari kesin gostermez
  - kararlardaki kaynak damgasi (⏱) her karari '(?)' yapmaz
Her senaryo ayrica fixture'in isirdigini (ayrisma / ekleme gercekten oldu) dogrular."""
import json
import re
import socket

import pytest

import duzenek as D
import llm
import motor as M
import not_araclari as NA

KOD = re.compile(r"(?<!\d)\d{3}(?!\d)")


@pytest.fixture(autouse=True)
def _agsiz(monkeypatch):
    def yasak(*_a, **_k):
        raise RuntimeError("duzenek ag erisimi yok")
    monkeypatch.setattr(socket.socket, "connect", yasak)
    monkeypatch.setattr(socket, "create_connection", yasak)


def _kos(monkeypatch, tmp_path, seed, tur, **sec):
    """D.calistir + son not / satirlar / aksiyonlar / Jira / e-posta / canli olaylar yakalanir."""
    y = {"canli": []}
    orj_not = M.Motor.notu_uret

    def notu(self, *a, **k):
        md = orj_not(self, *a, **k)
        y["md"], y["satirlar"] = md, [dict(s) for s in self.tum_satirlar()]
        y["aks"] = NA.aksiyonlari_ayikla(md)
        yol = str(tmp_path / f"jira_{len(y)}.csv")
        NA.jira_csv(y["aks"], yol)
        with open(yol, encoding="utf-8-sig") as f:
            y["jira"] = f.read().splitlines()[1:]
        y["eposta"] = NA.kisiye_ozel_epostalar(md, "E2E", "2026-10-11")
        return md
    monkeypatch.setattr(M.Motor, "notu_uret", notu)
    orj_olay = D.MotorAlici.olay

    def olay(self, tip, veri):
        if tip == "parca_ozetlendi":
            y["canli"].append(veri)
        return orj_olay(self, tip, veri)
    monkeypatch.setattr(D.MotorAlici, "olay", olay)
    D.calistir(seed=seed, tur=tur, **sec)
    assert "md" in y
    return y


def _jira_assignee(satir):
    return satir.split(",")[1] if satir.count(",") >= 1 else ""


def test_kisa_uzun_sayi_ayrismasi_kesin_aksiyon_ve_jira_uretmez(monkeypatch, tmp_path):
    """Bulgu (e1d): kisa klip kayit numarasini ters duyar (132 -> 231), uzun pencere dogru duyar; sayi kaybinda kisa metin
    kalir, 231 yalniz 'belirsiz'deydi: not.md aksiyonu kesin, Jira Assignee dolu, e-posta gidiyordu."""
    orj = D.kisa_stt_metni

    def bozuk(c):
        return re.sub(r"\b([1-5])(\d)([1-9]) için (ben |yarın ben |hazırla|yarın gönder)",
                      lambda m: f"{m.group(3)}{m.group(2)}{m.group(1)} için {m.group(4)}", orj(c))
    monkeypatch.setattr(D, "kisa_stt_metni", bozuk)
    y = _kos(monkeypatch, tmp_path, 7, "not_faz4", llm="dusman", uzun=True)
    celiski = {b for s in y["satirlar"] for b in (s.get("uzun_belirsiz") or []) if re.fullmatch(r"\d{3}", b)}
    assert len(celiski) >= 5, "fixture isirmiyor: kisa / uzun sayi ayrismasi yok"
    for s in y["satirlar"]:
        for b in s.get("uzun_belirsiz") or []:
            assert not re.search(r"(?<!\d)" + b + r"(?!\d)", M.sarmal_sil(M.satir_metni(s)))     # notta yalniz ⟨⟩ icinde
    kesin = [a for a in y["aks"] if a["sahip"]["durum"] == "kesin" and set(KOD.findall(a["madde"])) & celiski]
    assert kesin == []
    assert not [x for x in y["jira"] if set(KOD.findall(x.split(",")[0])) & celiski and _jira_assignee(x)]
    for _ad, _konu, govde in y["eposta"]:
        assert not [ln for ln in govde.splitlines() if ln.startswith("- ") and set(KOD.findall(ln)) & celiski]


def test_kisa_uzun_hitap_ayrismasi_sahibi_kesin_yapmaz(monkeypatch, tmp_path):
    """Bulgu (e1e): kisa 'Sinan, sen ...', uzun 'Kaan, sen ...'; uzun metin kazanir, 'Kaan' yalniz belirsiz listesindeydi:
    aksiyon 'Kaan Uydurma kesin', Jira Assignee Kaan."""
    orj = D.Kosum._uzun_ses

    def ses(self, akis, bas, bit):
        v = orj(self, akis, bas, bit)
        v["kelimeler"] = ["Kaan," if w == "Sinan," else w for w in v["kelimeler"]]
        return v
    monkeypatch.setattr(D.Kosum, "_uzun_ses", ses)
    y = _kos(monkeypatch, tmp_path, 7, "not_faz4", llm="dusman", uzun=True)
    deg = [s for s in y["satirlar"] if s.get("raw_kisa") and "Sinan," in s["raw_kisa"] and "Kaan," in s["text"]]
    assert deg, "fixture isirmiyor: hitap adi uzun pencereyle degismedi"
    kodlar = {KOD.findall(s["text"])[0] for s in deg}
    for s in deg:
        assert M.satir_metni(s).startswith(M.SARMAL_AC + "Kaan" + M.SARMAL_KAPA)
    assert not [a for a in y["aks"] if set(KOD.findall(a["madde"])) & kodlar and a["sahip"]["durum"] == "kesin"
                and "Kaan" in a["sahip"]["ad"]]
    assert not [x for x in y["jira"] if set(KOD.findall(x.split(",")[0])) & kodlar and "Kaan" in _jira_assignee(x)]


def test_uzun_pencere_kuyruk_uydurmasi_nota_jira_epostaya_girmez(monkeypatch, tmp_path):
    """Bulgu (e9b): uzun pencere sonuna 'Gerisi zekotu bafira mudeli 9xx için ben hazırlarım.' eklenir (kisa klipler
    temiz): 10+ Jira gorevi Assignee ile olusuyor, dort kisiye e-posta gidiyordu."""
    say = iter(range(901, 999))
    eklenen = []
    orj = D.SahteStt.coz

    def coz(self, ses_, ipucu=""):
        r = orj(self, ses_, ipucu)
        if ses_.get("uzun") and ses_.get("kelimeler"):
            r[0]["text"] = r[0]["text"] + f" Gerisi zekotu bafira mudeli {next(say)} için ben hazırlarım."
            eklenen.append(1)
        return r
    monkeypatch.setattr(D.SahteStt, "coz", coz)
    y = _kos(monkeypatch, tmp_path, 7, "not_faz4", llm="dusman", uzun=True, sinir_kes=True)
    assert len(eklenen) >= 5, "fixture isirmiyor: uzun pencere cozulmedi"
    assert not [s for s in y["satirlar"] if "zekotu" in (s.get("text") or "")]
    assert "zekotu" not in y["md"]
    assert not [x for x in y["jira"] if "zekotu" in x]
    assert not [g for _a, _k, g in y["eposta"] if "zekotu" in g]


def test_ozete_uydurulan_tutar_ve_tarih_kesin_kalmaz(monkeypatch, tmp_path):
    """Bulgu (e8): 'genel' cevabina transkriptte hic gecmeyen '975 bin lira ... 27 Kasım' eklenir: Ozet / Bir sonraki adim
    ve her e-postanin 'Toplantı özeti' bunu kesin tasiyordu."""
    orj = D.DusmanLLM.__call__

    def cagri(self, mesajlar, max_tokens, sema=None, effort="medium", temperature=0.6, dusunme=True):
        out, son = orj(self, mesajlar, max_tokens, sema, effort, temperature, dusunme)
        if llm._gorev_adi(mesajlar) == "genel":
            v = json.loads(out)
            v["ozet"] += " Toplam proje bütçesi 975 bin lira olarak onaylandı ve teslim 27 Kasım perşembe."
            v["sonraki_adim"] = "Toplam proje bütçesi 975 bin lira için teklif 27 Kasım perşembe günü hazırlanacak."
            out = json.dumps(v, ensure_ascii=False)
        return out, son
    monkeypatch.setattr(D.DusmanLLM, "__call__", cagri)
    y = _kos(monkeypatch, tmp_path, 7, "not_faz4", llm="dusman")
    tr = " ".join(s.get("text") or "" for s in y["satirlar"])
    assert "975" not in tr and "kasım" not in tr.lower()                  # deger gercekten uydurma
    assert "proje bütçesi (?) bin lira" in y["md"]                          # fixture isirdi, baglam kaldi
    assert "975" not in y["md"] and "Kasım" not in y["md"]
    assert not [g for _a, _k, g in y["eposta"] if "975" in g or "Kasım" in g]


def test_canli_panel_son_notun_kesinlestirmedigini_kesin_gostermez(monkeypatch, tmp_path):
    """Bulgu (e6, not_ust_uste / 7): canli panel bolum ozetini ham gosteriyordu: 59 canli aksiyonun 15'i son notta '(?)' /
    atilmis olan sahibi kesin, kararlarin hepsi dogrulamasiz."""
    sec = dict(D.SENARYOLAR_FAZ4["not_ust_uste"])
    y = _kos(monkeypatch, tmp_path, 7, sec.pop("tur"), **sec)
    son = {}
    for a in y["aks"]:
        for kod in KOD.findall(a["madde"]):
            son[kod] = a
    kararlar = [ln for ln in y["md"].split("## Kararlar\n", 1)[1].split("\n## ", 1)[0].splitlines() if ln.startswith("- ")]
    n_aks = n_kesin = 0
    for v in y["canli"]:
        o = v.get("ozet") or {}
        assert o.get("dogrulandi") is True
        for a in o.get("aksiyonlar", []):
            n_aks += 1
            if "(?)" in str(a["sorumlu"]) or a["sorumlu"] in ("belirsiz", "-", ""):
                continue
            n_kesin += 1
            kod = next(iter(KOD.findall(a["madde"])), None)
            f = son.get(kod)
            assert f is not None and f["sahip"]["durum"] == "kesin" and f["sahip"]["ad"] == a["sorumlu"], a
        for k in o.get("kararlar", []):
            if "(?)" in k:
                continue
            kod = next(iter(KOD.findall(k)), None)
            assert [ln for ln in kararlar if kod in ln and "(?)" not in ln], k
    assert n_aks > 20 and 0 < n_kesin < n_aks, "fixture isirmiyor"


def test_kaynak_damgasi_her_karari_belirsiz_yapmaz(monkeypatch, tmp_path):
    """Bulgu (not_faz4 / 7): dayanagi kesin konusmacinin ⟨⟩'siz satiri olan karar '... ⏱08:53:56' damgasi yuzunden '(?)'
    aliyordu (08 / 53 / 56 zorunlu deger): notta butun kararlar belirsiz gorunuyordu."""
    y = _kos(monkeypatch, tmp_path, 7, "not_faz4", llm="dusman")
    kararlar = [ln for ln in y["md"].split("## Kararlar\n", 1)[1].split("\n## ", 1)[0].splitlines() if ln.startswith("- ")]
    damgali = [ln for ln in kararlar if M._ISARET in ln]
    assert len(damgali) >= 5, "fixture isirmiyor: damgali karar yok"
    temiz = [ln for ln in damgali if "(?)" not in ln]
    assert len(temiz) * 2 >= len(damgali), kararlar[:6]
    for ln in damgali:                                                    # damgali ve damgasiz ayni karar
        aday = ln[2:]
        govde = M._KAYNAK_DAMGASI.sub(" ", aday).strip()
        assert ("(?)" in aday) == ("(?)" in govde)
