import random
import time
from decimal import Decimal, localcontext

import pytest

from app.anon.fpe import FeistelFPE

KEY = b"k" * 32


@pytest.fixture(scope="module")
def fpe():
    return FeistelFPE(KEY)


@pytest.mark.parametrize("d", [1, 2, 3, 4, 5])
def test_exhaustive_bijection_small_domains(fpe, d):
    lo = 0 if d == 1 else 10 ** (d - 1)
    domain = range(lo, 10**d)
    out = [fpe.encrypt_int(n, d) for n in domain]
    assert sorted(out) == list(domain)
    assert all(fpe.decrypt_int(y, d) == x for x, y in zip(domain, out))


@pytest.mark.parametrize("d", [11, 18, 38])
def test_roundtrip_large_domains(fpe, d):
    rng = random.Random(d)
    lo, hi = 10 ** (d - 1), 10**d
    for _ in range(10_000):
        n = rng.randrange(lo, hi)
        y = fpe.encrypt_int(n, d)
        assert lo <= y < hi
        assert fpe.decrypt_int(y, d) == n


def test_encrypt_digits_keeps_length_and_leading_zeros(fpe):
    out = fpe.encrypt_digits("000123")
    assert len(out) == 6 and out.isdigit()
    assert fpe.decrypt_digits(out) == "000123"
    outs = {fpe.encrypt_digits(f"{i:03d}") for i in range(1000)}
    assert outs == {f"{i:03d}" for i in range(1000)}
    long = "0" * 10 + "9" * 50
    assert len(fpe.encrypt_digits(long)) == 60 and fpe.decrypt_digits(fpe.encrypt_digits(long)) == long


def test_different_key_different_mapping(fpe):
    other = FeistelFPE(b"z" * 32)
    a = [fpe.encrypt_int(n, 6) for n in range(100000, 100100)]
    b = [other.encrypt_int(n, 6) for n in range(100000, 100100)]
    assert a != b


def test_deterministic():
    a, b = FeistelFPE(KEY), FeistelFPE(KEY)
    assert [a.encrypt_int(n, 11) for n in range(10**10, 10**10 + 50)] == [
        b.encrypt_int(n, 11) for n in range(10**10, 10**10 + 50)
    ]


def test_out_of_domain_rejected(fpe):
    with pytest.raises(ValueError):
        fpe.encrypt_int(99, 3)
    with pytest.raises(ValueError):
        fpe.encrypt_int(5, 39)
    with pytest.raises(ValueError):
        fpe.encrypt_digits("12a")


def test_token_int_sign_and_bijection(fpe):
    vals = list(range(-999, 1000))
    out = [fpe.token_int(v) for v in vals]
    assert len(set(out)) == len(vals)
    for v, y in zip(vals, out):
        assert (v < 0) == (y < 0)
        assert len(str(abs(v))) == len(str(abs(y)))


def test_token_decimal_keeps_fraction_and_scale(fpe):
    v = Decimal("-123.45")
    y = fpe.token_decimal(v)
    assert y < 0 and str(y).endswith(".45") and len(str(int(abs(y)))) == 3
    assert fpe.token_decimal(Decimal("7.00")).as_tuple().exponent == -2
    vals = [Decimal(i) / 10 for i in range(-200, 200)]
    assert len({fpe.token_decimal(v) for v in vals}) == len(vals)


def test_token_text_in_place(fpe):
    s = "TR-00 12/34"
    y = fpe.token_text(s)
    assert len(y) == len(s)
    assert [c for c in y if not c.isdigit()] == [c for c in s if not c.isdigit()]
    assert fpe.token_text("ABC") == "ABC"


def test_speed_100k(fpe):
    rng = random.Random(1)
    vals = [rng.randrange(10**10, 10**11) for _ in range(100_000)]
    t = time.perf_counter()
    for v in vals:
        fpe.encrypt_int(v, 11)
    assert time.perf_counter() - t < 5


# --- tersler (değer çözme) ------------------------------------------------------------------


@pytest.mark.parametrize(
    "n", [0, 1, 7, 9, -1, -5, -9, -10, -42, -123456, 12, 999, 10**37, 10**38 - 1, -(10**38 - 1), -(10**37)]
)
def test_untoken_int_roundtrip(fpe, n):
    t = fpe.token_int(n)
    assert (t < 0) == (n < 0) and len(str(abs(t))) == len(str(abs(n)))
    assert fpe.untoken_int(t) == n


def test_untoken_int_exhaustive_small(fpe):
    domain = range(-999, 1000)
    out = [fpe.token_int(n) for n in domain]
    assert sorted(out) == list(domain)
    assert all(fpe.untoken_int(t) == n for n, t in zip(domain, out))
    assert all(fpe.untoken_int(t) != 0 for t in range(-9, 0))  # neg1 alanı -0 üretmez


@pytest.mark.parametrize(
    "s",
    ["0", "5", "-3", "-0.5", "0.25", "12.5", "-12.50", "123.000", "-7", "-7.0", "5.00", "-98765.4321",
     "99999999999999999999999999999999999999", "-12345678901234567890123456789012345678.123"],
)
def test_untoken_decimal_roundtrip(fpe, s):
    v = Decimal(s)
    t = fpe.token_decimal(v)
    assert t.as_tuple().exponent == v.as_tuple().exponent  # ölçek korunur
    r = fpe.untoken_decimal(t)
    assert r == v and str(r) == str(v)


def test_untoken_decimal_matches_int_for_integral(fpe):
    for n in (0, 4, -4, 77, -77, 10**20):
        assert fpe.untoken_decimal(Decimal(fpe.token_int(n))) == n
    assert fpe.untoken_decimal(Decimal("NaN")).is_nan()


def test_untoken_decimal_exhaustive_small(fpe):
    with localcontext() as ctx:
        ctx.prec = 50
        vals = [Decimal(i) / 10 for i in range(-999, 1000)] + [Decimal(i) for i in range(-99, 100)]
    for v in vals:
        assert fpe.untoken_decimal(fpe.token_decimal(v)) == v


@pytest.mark.parametrize(
    "s",
    ["TR-0012-AB", "ABC", "", "a1b2c3", "1234567890" * 5, "x" + "9" * 45 + "y", "00000", "ç1ğ2ş3", "  12 "],
)
def test_untoken_text_roundtrip(fpe, s):
    t = fpe.token_text(s)
    assert len(t) == len(s)
    assert [c for c in t if not c.isdigit()] == [c for c in s if not c.isdigit()]
    assert fpe.untoken_text(t) == s


def test_untoken_text_exhaustive_small(fpe):
    domain = [f"A{i:03d}" for i in range(1000)]
    out = [fpe.token_text(s) for s in domain]
    assert sorted(out) == domain
    assert all(fpe.untoken_text(t) == s for s, t in zip(domain, out))


# --- text_token / date_token -------------------------------------------------------------

from datetime import date as _date, datetime as _dt, timedelta as _td  # noqa: E402
from itertools import product  # noqa: E402

from app.anon.fpe import _CLASSES  # noqa: E402


@pytest.mark.parametrize("pattern", ["Aa", "a-0", "Ç9", "ğı", "0x", "A.b"])
def test_text_std_exhaustive_patterns(fpe, pattern):
    alph = {"A": _CLASSES[0], "a": _CLASSES[1], "0": _CLASSES[2], "9": _CLASSES[2],
            "Ç": _CLASSES[3], "ğ": _CLASSES[4], "ı": _CLASSES[4], "x": _CLASSES[1], "b": _CLASSES[1]}
    pools = [alph.get(c, c) for c in pattern]
    domain = ["".join(p) for p in product(*pools)]
    out = [fpe.token_text_std(s) for s in domain]
    assert sorted(out) == sorted(domain)  # aynı desen içinde birebir
    assert all(fpe.untoken_text_std(t) == s for s, t in zip(domain, out))


def test_text_std_digit_strings(fpe):
    assert fpe.token_text_std("100234") == str(fpe.token_int(100234))
    assert all(fpe.token_text_std(str(i)) == str(fpe.token_int(i)) for i in range(0, 1000))
    t = fpe.token_text_std("0012")
    assert t[0] == "0" and len(t) == 4 and t[1:] == fpe.encrypt_digits("012")
    two = ["".join(p) for p in product("0123456789", repeat=2)]
    assert sorted(fpe.token_text_std(s) for s in two) == two
    for s in ["1" * 39, "9" * 120, "0" * 50, "05" * 30]:
        t = fpe.token_text_std(s)
        assert len(t) == len(s) and t.isdigit() and (t[0] == "0") == (s[0] == "0")
        assert fpe.untoken_text_std(t) == s
    # mevcut çıktılar değişmez
    assert fpe.token_text("TR-0012") == "TR-" + fpe.encrypt_digits("0012")


def test_text_std_random_roundtrip(fpe):
    rng = random.Random(7)
    pool = _CLASSES[0] + _CLASSES[1] + _CLASSES[2] + _CLASSES[3] + _CLASSES[4] + " .@-_/"
    samples = ["ali.veli@ornek.com.tr", "TR33 0006 1005 1978 6457 8413 26", "Şükrü Çağlayan",
               "x" * 4000, "Ğ" * 4000, ""]
    samples += ["".join(rng.choice(pool) for _ in range(rng.randint(1, 60))) for _ in range(2000)]
    for s in samples:
        t = fpe.token_text_std(s)
        assert len(t) == len(s) and len(t.encode()) == len(s.encode())
        assert [c for c in s if c in " .@-_/"] == [t[i] for i, c in enumerate(s) if c in " .@-_/"]
        assert fpe.untoken_text_std(t) == s
    assert fpe.token_text_std("ali.veli@ornek.com.tr") != "ali.veli@ornek.com.tr"


@pytest.mark.parametrize("year", [2023, 2024, 1900, 2000])
def test_date_token_full_year(fpe, year):
    n = 366 if year in (2024, 2000) else 365
    days = [_date(year, 1, 1) + _td(days=i) for i in range(n)]
    out = [fpe.token_date(d) for d in days]
    assert sorted(out) == days and all(o.year == year for o in out)
    assert [fpe.untoken_date(o) for o in out] == days
    if n == 366:
        assert _date(year, 2, 29) in out


def test_date_token_time(fpe):
    mid = _dt(2024, 2, 29)
    t = fpe.token_date(mid)
    assert t.time() == mid.time() and t.year == 2024 and fpe.untoken_date(t) == mid
    rng = random.Random(3)
    for _ in range(2000):
        v = _dt(rng.randint(1, 9999), 1, 1) + _td(days=rng.randint(0, 364), seconds=rng.randint(0, 86399),
                                                  microseconds=rng.randint(0, 999999))
        t = fpe.token_date(v)
        assert t.year == v.year and t.microsecond == v.microsecond
        assert (t.hour, t.minute, t.second) != (0, 0, 0) or (v.hour, v.minute, v.second) == (0, 0, 0)
        assert fpe.untoken_date(t) == v
