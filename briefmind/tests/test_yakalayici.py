"""Teams altyazisi: onceki satir sonradan duzeltilince ayni satirlar yeniden yayilmamali."""
import yakalayici


def _oku(y, gorunen):
    y._ciftler = lambda: list(gorunen)
    y.grup = object()
    return y.oku()


def test_duzeltilen_satir_tekrar_yayilmaz():
    y = yakalayici.Yakalayici()
    a = ("Ahmet Yılmaz", "IFRS dokuz raporunda PD kolonu string geliyor")
    b = ("Ayşe Demir", "Test ortamı perşembe hazır olur diye düşünüyorum")
    c = ("Ahmet Yılmaz", "Tamam o zaman Jira kaydını sen açar mısın")
    d = ("Ayşe Demir", "Açarım.")
    yayilan = []
    yayilan += _oku(y, [a, b])
    yayilan += _oku(y, [a, b, c])
    # Teams ilk satiri duzeltti ("dokuz" -> "9"): hizalama kopar, eskiden a/b yeniden yayiliyordu
    a2 = ("Ahmet Yılmaz", "IFRS 9 raporunda PD kolonu string geliyor")
    yayilan += _oku(y, [a2, b, c, d])
    metinler = [s["text"] for s in yayilan]
    assert metinler.count(b[1]) == 1
    assert sum(1 for m in metinler if "PD kolonu" in m) == 1
    assert c[1] in metinler
    # sonraki okumada hizalama toparlanir, yeni satir bir kez gelir
    e = ("Ahmet Yılmaz", "Süper, teşekkürler herkese, görüşmek üzere")
    yayilan2 = _oku(y, [a2, b, c, d, e])
    assert [s["text"] for s in yayilan2] == [d[1]]
