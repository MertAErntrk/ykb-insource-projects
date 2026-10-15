"""Anahtarlı biçim korumalı permütasyonlar (num_token, text_token, date_token).

Z_a x Z_a üzerinde dengeli Feistel (modüler toplama, a = ceil(sqrt(N))) + cycle-walking:
a*a >= N olduğundan alan dışına düşen değer yeniden şifrelenir; sonuç [lo, lo+N) alanında
birebirdir. Tur fonksiyonu anahtarlı blake2b (önceden hazırlanmış nesnenin kopyası, hızlı);
text_token'ın geniş alanında shake_256 (_wperm).
"""

import calendar
import hashlib
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
from math import isqrt

ROUNDS = 10
MAX_WALK = 100_000
MAX_DIGITS = 38
_CHUNK = 38  # encrypt_digits uzun dizileri bu uzunlukta parçalara böler (parça başına birebir)


class FeistelFPE:
    def __init__(self, key: bytes):
        if not isinstance(key, (bytes, bytearray)) or not key:
            raise ValueError("FPE anahtarı boş olmayan bytes olmalı.")
        # blake2b anahtarı en çok 64 bayt; sabit uzunlukta alt anahtar türetilir
        self._key = hashlib.blake2b(bytes(key), digest_size=32, person=b"tt-fpe-key").digest()
        self._domains: dict[tuple[bytes, int], tuple[int, int, object]] = {}

    # --- çekirdek ---------------------------------------------------------------------------

    def _domain(self, tweak: bytes, n: int) -> tuple[int, int, object]:
        d = self._domains.get((tweak, n))
        if d is None:
            a = isqrt(n - 1) + 1
            base = hashlib.blake2b(key=self._key, digest_size=16)
            base.update(tweak + b"|" + str(n).encode())
            d = (a, max(1, (a.bit_length() + 7) // 8), base)
            self._domains[(tweak, n)] = d
        return d

    def _feistel(self, x: int, a: int, nb: int, base, inverse: bool) -> int:
        left, right = divmod(x, a)
        if not inverse:
            for r in range(ROUNDS):
                h = base.copy()
                h.update(bytes((r,)) + right.to_bytes(nb, "big"))
                left, right = right, (left + int.from_bytes(h.digest(), "big")) % a
        else:
            for r in range(ROUNDS - 1, -1, -1):
                h = base.copy()
                h.update(bytes((r,)) + left.to_bytes(nb, "big"))
                left, right = (right - int.from_bytes(h.digest(), "big")) % a, left
        return left * a + right

    def _perm(self, x: int, n: int, tweak: bytes, inverse: bool = False) -> int:
        """[0, n) üzerinde birebir permütasyon (cycle-walking)."""
        if n == 1:
            return 0
        a, nb, base = self._domain(tweak, n)
        for _ in range(MAX_WALK):
            x = self._feistel(x, a, nb, base, inverse)
            if x < n:
                return x
        raise RuntimeError("FPE döngü sınırı aşıldı.")

    # --- tamsayı alanı ----------------------------------------------------------------------

    @staticmethod
    def _bounds(n: int, digits: int) -> tuple[int, int]:
        if type(digits) is not int or not 1 <= digits <= MAX_DIGITS:
            raise ValueError("Basamak sayısı 1..38 olmalı.")
        lo = 0 if digits == 1 else 10 ** (digits - 1)
        hi = 10**digits
        if type(n) is not int or not lo <= n < hi:
            raise ValueError("Değer basamak alanının dışında.")
        return lo, hi - lo

    def encrypt_int(self, n: int, digits: int) -> int:
        lo, size = self._bounds(n, digits)
        return lo + self._perm(n - lo, size, b"int" + bytes((digits,)))

    def decrypt_int(self, n: int, digits: int) -> int:
        lo, size = self._bounds(n, digits)
        return lo + self._perm(n - lo, size, b"int" + bytes((digits,)), inverse=True)

    # --- rakam dizisi alanı -----------------------------------------------------------------

    def _digits(self, s: str, inverse: bool) -> str:
        if not isinstance(s, str) or any(not "0" <= c <= "9" for c in s):
            raise ValueError("Yalnız rakam dizisi kabul edilir.")
        out = []
        for idx, start in enumerate(range(0, len(s), _CHUNK)):
            chunk = s[start : start + _CHUNK]
            ln = len(chunk)
            tweak = b"dig" + str(len(s)).encode() + b"." + str(idx).encode()
            out.append(str(self._perm(int(chunk), 10**ln, tweak, inverse)).zfill(ln))
        return "".join(out)

    def encrypt_digits(self, s: str) -> str:
        return self._digits(s, False)

    def decrypt_digits(self, s: str) -> str:
        return self._digits(s, True)

    # --- num_token değer dönüşümleri --------------------------------------------------------

    def token_int(self, n: int) -> int:
        """İşaret ve basamak sayısı korunur; Z üzerinde birebir."""
        if n >= 0:
            return self.encrypt_int(n, len(str(n)))
        m = -n
        d = len(str(m))
        if d == 1:  # negatif tek hane alanı [1,10): -0 üretilmez
            return -(1 + self._perm(m - 1, 9, b"neg1"))
        return -self.encrypt_int(m, d)

    def token_decimal(self, v: Decimal) -> Decimal:
        """Tamsayı kısmı permütasyon, kesir ve ölçek aynen; işaret korunur."""
        if not v.is_finite():
            return v
        with localcontext() as ctx:
            ctx.prec = 200
            neg = v.is_signed() and v != 0
            a = abs(v)
            i = int(a)
            frac = a - Decimal(i)
            if frac == 0:
                j = abs(self.token_int(-i if neg else i))
            else:
                j = self.encrypt_int(i, len(str(i)))
            out = Decimal(j) + frac
            return -out if neg else out

    def token_text(self, s: str) -> str:
        """Rakam alt dizisi yerinde permütasyon; rakam olmayan karakterler yerinde kalır."""
        pos = [i for i, c in enumerate(s) if "0" <= c <= "9"]
        if not pos:
            return s
        enc = self.encrypt_digits("".join(s[i] for i in pos))
        chars = list(s)
        for i, c in zip(pos, enc):
            chars[i] = c
        return "".join(chars)

    # --- tersleri (değer çözme; ileri fonksiyonların her dalını birebir geri alır) ----------

    def untoken_int(self, n: int) -> int:
        if n >= 0:
            return self.decrypt_int(n, len(str(n)))
        m = -n
        d = len(str(m))
        if d == 1:
            return -(1 + self._perm(m - 1, 9, b"neg1", inverse=True))
        return -self.decrypt_int(m, d)

    def untoken_decimal(self, v: Decimal) -> Decimal:
        if not v.is_finite():
            return v
        with localcontext() as ctx:
            ctx.prec = 200
            neg = v.is_signed() and v != 0
            a = abs(v)
            j = int(a)
            frac = a - Decimal(j)
            if frac == 0:
                i = abs(self.untoken_int(-j if neg else j))
            else:
                i = self.decrypt_int(j, len(str(j)))
            out = Decimal(i) + frac
            return -out if neg else out

    def untoken_text(self, s: str) -> str:
        pos = [i for i, c in enumerate(s) if "0" <= c <= "9"]
        if not pos:
            return s
        dec = self.decrypt_digits("".join(s[i] for i in pos))
        chars = list(s)
        for i, c in zip(pos, dec):
            chars[i] = c
        return "".join(chars)

    # --- geniş alan permütasyonu (text_token) -----------------------------------------------
    # Tur çıktısı alan yarısından ≥128 bit uzun (shake_256, nb+16 bayt): büyük alanda mod a
    # sonrası üst haneler de karışır. Alan boyutu str'e çevrilmez (int→str basamak sınırı).

    def _wperm(self, x: int, n: int, tweak: bytes, inverse: bool = False) -> int:
        if n == 1:
            return 0
        a = isqrt(n - 1) + 1
        nb = max(1, (a.bit_length() + 7) // 8)
        nn = n.to_bytes((n.bit_length() + 7) // 8, "big")
        base = hashlib.shake_256(self._key + b"|wide|" + len(tweak).to_bytes(4, "big") + tweak + nn)
        out = nb + 16

        def f(r: int, v: int) -> int:
            h = base.copy()
            h.update(bytes((r,)) + v.to_bytes(nb, "big"))
            return int.from_bytes(h.digest(out), "big")

        for _ in range(MAX_WALK):
            left, right = divmod(x, a)
            if not inverse:
                for r in range(ROUNDS):
                    left, right = right, (left + f(r, right)) % a
            else:
                for r in range(ROUNDS - 1, -1, -1):
                    left, right = (right - f(r, left)) % a, left
            x = left * a + right
            if x < n:
                return x
        raise RuntimeError("FPE döngü sınırı aşıldı.")

    def _classed(self, s: str, first_nonzero: bool, inverse: bool) -> str:
        """Sınıflı karakterler karma tabanlı tek sayı olarak permüte edilir; diğerleri yerinde."""
        pos, alph = [], []
        pattern = bytearray()
        for i, c in enumerate(s):
            k = _CLASS_OF.get(c)
            if k is None:
                pattern.append(255)
                continue
            if first_nonzero and i == 0:
                k = _NZ
            pattern.append(k)
            pos.append(i)
            alph.append(_CLASSES[k])
        if not pos:
            return s
        x, n = 0, 1
        for i, al in zip(pos, alph):
            x = x * len(al) + al.index(s[i])
            n *= len(al)
        tweak = b"txt" + len(s).to_bytes(4, "big") + bytes(pattern)
        y = self._wperm(x, n, tweak, inverse)
        chars = list(s)
        for i, al in zip(reversed(pos), reversed(alph)):
            y, d = divmod(y, len(al))
            chars[i] = al[d]
        return "".join(chars)

    def _text_std(self, s: str, inverse: bool) -> str:
        if not isinstance(s, str):
            raise ValueError("Metin bekleniyordu.")
        if s and all("0" <= c <= "9" for c in s):
            if len(s) == 1 or (s[0] != "0" and len(s) <= MAX_DIGITS):
                n = int(s)
                return str(self.untoken_int(n) if inverse else self.token_int(n))
            if s[0] == "0":
                return "0" + self._digits(s[1:], inverse)
            return self._classed(s, True, inverse)  # 38 haneden uzun, ilk hane 1-9 kalır
        return self._classed(s, False, inverse)

    def token_text_std(self, s: str) -> str:
        """text_token: her karakter kendi sınıfında kalır (uzunluk ve UTF-8 bayt uzunluğu aynen).

        Tamamı rakam ve 0 ile başlamıyorsa token_int ile aynı sonuç (NUMBER kolonla join korunur);
        0 ile başlıyorsa baştaki 0 kalır, kalan haneler encrypt_digits.
        """
        return self._text_std(s, False)

    def untoken_text_std(self, s: str) -> str:
        return self._text_std(s, True)

    # --- date_token -------------------------------------------------------------------------

    def _date(self, v, inverse: bool):
        y = v.year
        ndays = 366 if calendar.isleap(y) else 365
        doy = v.timetuple().tm_yday - 1
        nd = self._perm(doy, ndays, b"doy" + y.to_bytes(2, "big"), inverse)
        out_day = date(y, 1, 1) + timedelta(days=nd)
        if not isinstance(v, datetime):
            return out_day
        sod = v.hour * 3600 + v.minute * 60 + v.second
        if sod:
            sod = 1 + self._perm(sod - 1, 86399, b"sod", inverse)
        h, rem = divmod(sod, 3600)
        return v.replace(year=y, month=out_day.month, day=out_day.day, hour=h, minute=rem // 60,
                         second=rem % 60)

    def token_date(self, v):
        """date_token: yıl korunur, yılın günü ve (gece yarısı değilse) gün içi saniye permüte."""
        return self._date(v, False)

    def untoken_date(self, v):
        return self._date(v, True)


_CLASSES = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "abcdefghijklmnopqrstuvwxyz",
    "0123456789",
    "ÇĞİÖŞÜ",
    "çğıöşü",
    "123456789",
)
_NZ = 5  # yalnız 38 haneden uzun rakam dizisinin ilk hanesi
_CLASS_OF = {c: k for k, al in enumerate(_CLASSES[:5]) for c in al}
