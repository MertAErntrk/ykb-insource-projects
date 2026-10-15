"""Anonimleştirme kurallarının doğrulanması (compile_plan) ve Arrow batch'lerine uygulanması.

Doğruluk kuralları (plan Karar 8): PK/UNIQUE kolonda yalnız birebir kurallar; NOT NULL hedefte
NULL üreten kural yok ('' = NULL); çıktı hedef tipine ve uzunluğuna (BYTE/CHAR) sığar.
"""

import hashlib
import hmac as _hmac
import random as _random
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext

import pyarrow as pa
import pyarrow.compute as pc

from app.anon.fpe import FeistelFPE
from app.errors import AppError, ErrorCode
from app.oracle.sql_templates import ColumnDef

TEXT_TYPES = {"CHAR", "VARCHAR2", "NCHAR", "NVARCHAR2"}
FLOAT_TYPES = {"FLOAT", "BINARY_FLOAT", "BINARY_DOUBLE"}
_TIMESTAMP_RE = re.compile(r"TIMESTAMP(\([0-9]\))?")

# kural tipi -> izinli params anahtarları
RULE_PARAMS: dict[str, set[str]] = {
    "keep": set(),
    "exclude": set(),
    "null": set(),
    "constant": {"value"},
    "mask": {"show_last"},
    "hmac": set(),
    "num_token": set(),
    "text_token": set(),
    "date_token": set(),
    "date_shift": {"anchor", "max_days"},
    "random": set(),
}
ONE_TO_ONE = {"keep", "exclude", "num_token", "text_token", "date_token", "hmac"}
# veri tipi kategorisi -> standart (birebir, tip ve uzunluk koruyan, geri çevrilebilir) kural;
# float için standart yok
STANDARD_RULES = {"number": "num_token", "text": "text_token", "date": "date_token"}
HMAC_HEX_LEN = 64
HMAC_MIN_LEN = 16
CACHE_MAX = 1_000_000
_NULL_ANCHOR = b"\x00tt-null-anchor"
_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_RANDOM_DATE_LO = datetime(1950, 1, 1)
_RANDOM_DATE_SPAN = int((datetime(2031, 1, 1) - _RANDOM_DATE_LO).total_seconds())
_RNG = _random.SystemRandom()


@dataclass
class ColumnInfo:
    name: str
    data_type: str
    data_length: int | None
    char_length: int | None
    char_used: str | None  # 'B' | 'C' | None
    precision: int | None
    scale: int | None
    nullable: bool
    is_pk_or_unique: bool
    supported: bool


def is_supported_type(data_type: str) -> bool:
    return _category(data_type) is not None


def _category(data_type: object) -> str | None:
    if not isinstance(data_type, str):
        return None
    if data_type in TEXT_TYPES:
        return "text"
    if data_type == "NUMBER":
        return "number"
    if data_type in FLOAT_TYPES:
        return "float"
    if data_type == "DATE" or _TIMESTAMP_RE.fullmatch(data_type):
        return "date"
    return None


def _text_limit(c: ColumnInfo) -> tuple[int, str]:
    """(sınır, 'C'|'B'). N-tipleri ve CHAR semantiği karakter, BYTE semantiği UTF-8 bayt sayar."""
    if c.data_type in ("NCHAR", "NVARCHAR2") or c.char_used == "C":
        return int(c.char_length or c.data_length or 0), "C"
    return int(c.data_length or c.char_length or 0), "B"


def _text_len(s: str, unit: str) -> int:
    return len(s) if unit == "C" else len(s.encode("utf-8"))


def derive_key(key: bytes, purpose: bytes) -> bytes:
    return _hmac.new(key, purpose, hashlib.sha256).digest()


def canonical(v: object) -> str:
    """Kural girdisinin tipten bağımsız metni (aynı değer her tabloda aynı HMAC'i verir)."""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, Decimal):
        if v.is_finite() and v == v.to_integral_value():
            return str(int(v))
        return format(v.normalize(), "f")
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        return repr(v)
    if isinstance(v, str):
        return v.rstrip()
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return str(v)


def _fail(code: ErrorCode, msg: str) -> AppError:
    return AppError(code, msg)


@dataclass
class _Rule:
    column: str
    type: str
    params: dict
    src: ColumnInfo
    eff: ColumnInfo
    hmac_len: int = HMAC_HEX_LEN
    const_value: object = None
    trim: bool = False
    cache: dict = field(default_factory=dict)


# --- constant dönüşümü ----------------------------------------------------------------------


def _parse_constant(raw: str, eff: ColumnInfo, name: str) -> object:
    """Sabit metni hedef tipine çevirir; '' → None (Oracle'da NULL)."""
    if raw == "":
        return None
    cat = _category(eff.data_type)
    if cat == "text":
        limit, unit = _text_limit(eff)
        n = _text_len(raw, unit)
        if n > limit:
            birim = "karakter" if unit == "C" else "bayt"
            raise _fail(
                ErrorCode.RULE_INVALID,
                f"{name}: sabit değer hedef uzunluğuna sığmıyor ({n} > {limit} {birim}).",
            )
        return raw
    if cat == "number":
        try:
            d = Decimal(raw.strip())
        except InvalidOperation:
            raise _fail(ErrorCode.RULE_INVALID, f"{name}: sabit değer sayıya çevrilemiyor.") from None
        if not d.is_finite() or not _fits_number(d, eff.precision, eff.scale):
            raise _fail(
                ErrorCode.RULE_INVALID, f"{name}: sabit değer NUMBER hassasiyet/ölçeğine sığmıyor."
            )
        return d
    if cat == "float":
        try:
            f = float(raw.strip())
        except ValueError:
            raise _fail(ErrorCode.RULE_INVALID, f"{name}: sabit değer sayıya çevrilemiyor.") from None
        if f != f or f in (float("inf"), float("-inf")):
            raise _fail(ErrorCode.RULE_INVALID, f"{name}: sabit değer sonlu bir sayı olmalı.")
        return f
    if cat == "date":
        try:
            dt = datetime.fromisoformat(raw.strip())
        except ValueError:
            raise _fail(
                ErrorCode.RULE_INVALID, f"{name}: sabit tarih ISO-8601 biçiminde olmalı."
            ) from None
        if dt.tzinfo is not None:
            raise _fail(ErrorCode.RULE_INVALID, f"{name}: saat dilimli tarih desteklenmez.")
        return dt.replace(microsecond=0) if eff.data_type == "DATE" else dt
    raise _fail(ErrorCode.RULE_INVALID, f"{name}: bu kolon tipinde sabit değer kullanılamaz.")


def _fits_number(d: Decimal, precision: int | None, scale: int | None) -> bool:
    with localcontext() as ctx:
        ctx.prec = 200
        s = scale if scale is not None else (0 if precision is not None else None)
        if s is not None:
            q = d.quantize(Decimal(1).scaleb(-int(s)))
            if q != d:
                return False
            if precision is not None and abs(q) >= Decimal(10) ** (int(precision) - int(s)):
                return False
            return True
        return len(d.normalize().as_tuple().digits) <= 38


def _const_for(value: object, typ: pa.DataType, name: str) -> object:
    """Ayrıştırılmış sabiti girdi Arrow tipine uygun Python değerine çevirir."""
    if pa.types.is_integer(typ):
        if isinstance(value, (int, Decimal, float)) and value == int(value):
            return int(value)
    elif pa.types.is_decimal(typ):
        if isinstance(value, (int, Decimal)):
            with localcontext() as ctx:
                ctx.prec = 200
                return Decimal(value).quantize(Decimal(1).scaleb(-typ.scale))
    elif pa.types.is_floating(typ):
        if isinstance(value, (int, Decimal, float)):
            return float(value)
    elif pa.types.is_string(typ) or pa.types.is_large_string(typ):
        if isinstance(value, Decimal):
            return format(value, "f")
        if isinstance(value, datetime):
            return value.isoformat(sep=" ")
        return str(value)
    elif pa.types.is_timestamp(typ):
        if isinstance(value, datetime):
            return value
    elif pa.types.is_date(typ):
        if isinstance(value, datetime):
            return value.date()
    raise _fail(ErrorCode.RULE_INVALID, f"{name}: sabit değer kaynak veri tipine uygulanamıyor.")


# --- doğrulama ------------------------------------------------------------------------------


def _as_int_param(v: object, lo: int, hi: int) -> int | None:
    if type(v) is not int or not lo <= v <= hi:
        return None
    return v


def compile_plan(
    rules: list[dict],
    source_cols: list[ColumnInfo],
    *,
    mode: str,
    target_cols: dict[str, ColumnInfo] | None,
    key: bytes,
) -> "RulePlan":
    if mode not in ("create", "append"):
        raise _fail(ErrorCode.VALIDATION, "Geçersiz hedef modu.")
    if mode == "append" and target_cols is None:
        raise _fail(ErrorCode.VALIDATION, "Ekleme modunda hedef kolonları gerekli.")
    if not isinstance(rules, list):
        raise _fail(ErrorCode.VALIDATION, "Kural listesi geçersiz.")

    by_name: dict[str, dict] = {}
    for r in rules:
        if not isinstance(r, dict) or not isinstance(r.get("column"), str):
            raise _fail(ErrorCode.VALIDATION, "Kural biçimi geçersiz.")
        col = r["column"]
        if col in by_name:
            raise _fail(ErrorCode.VALIDATION, f"{col}: kolon için birden çok kural var.")
        by_name[col] = r
    src_names = [c.name for c in source_cols]
    extra = [n for n in by_name if n not in src_names]
    if extra:
        raise _fail(ErrorCode.VALIDATION, f"{extra[0]}: kaynakta olmayan kolon için kural var.")
    missing = [n for n in src_names if n not in by_name]
    if missing:
        raise _fail(ErrorCode.VALIDATION, f"{missing[0]}: kolon için kural eksik.")

    src_by_name = {c.name: c for c in source_cols}
    compiled: dict[str, _Rule] = {}
    shift_params: set[tuple[str, int]] = set()

    for col in source_cols:
        name = col.name
        r = by_name[name]
        t = r.get("type")
        params = r.get("params")
        params = {} if params is None else params
        if not isinstance(t, str) or t not in RULE_PARAMS:
            raise _fail(ErrorCode.RULE_INVALID, f"{name}: bilinmeyen kural tipi '{t}'.")
        if not isinstance(params, dict):
            raise _fail(ErrorCode.RULE_INVALID, f"{name}: kural parametreleri geçersiz.")
        unknown = set(params) - RULE_PARAMS[t]
        if unknown:
            raise _fail(
                ErrorCode.RULE_INVALID, f"{name}: bilinmeyen parametre '{sorted(unknown)[0]}'."
            )
        supported = col.supported and is_supported_type(col.data_type)
        if not supported and t != "exclude":
            raise _fail(
                ErrorCode.RULE_INVALID, f"{name}: bu kolon tipi taşınamaz, yalnız 'exclude' seçilebilir."
            )
        if t == "exclude":
            compiled[name] = _Rule(name, t, params, col, col)
            continue

        if mode == "append":
            eff = target_cols.get(name)  # type: ignore[union-attr]
            if eff is None:
                raise _fail(ErrorCode.VALIDATION, f"{name}: hedef tabloda kolon yok.")
        else:
            eff = col
        pk = col.is_pk_or_unique or (mode == "append" and eff.is_pk_or_unique)
        if pk and t not in ONE_TO_ONE:
            raise _fail(
                ErrorCode.RULE_INVALID,
                f"{name}: PK/UNIQUE kolonda '{t}' kuralı kullanılamaz (çakışma üretir).",
            )
        scat = _category(col.data_type)
        ecat = _category(eff.data_type)
        rule = _Rule(name, t, params, col, eff, trim=col.data_type in ("CHAR", "NCHAR"))

        if t == "null":
            if not eff.nullable:
                raise _fail(ErrorCode.RULE_INVALID, f"{name}: NOT NULL hedef kolona NULL yazılamaz.")
        elif t == "constant":
            v = params.get("value")
            if isinstance(v, bool) or not isinstance(v, (str, int, float)):
                raise _fail(ErrorCode.RULE_INVALID, f"{name}: sabit değer (value) metin olmalı.")
            rule.const_value = _parse_constant(str(v), eff, name)
            if rule.const_value is None and not eff.nullable:
                raise _fail(
                    ErrorCode.RULE_INVALID, f"{name}: NOT NULL hedefte boş sabit kullanılamaz ('' = NULL)."
                )
        elif t == "mask":
            if scat != "text" or ecat != "text":
                raise _fail(ErrorCode.RULE_INVALID, f"{name}: mask yalnız metin kolonlarda kullanılabilir.")
            show = _as_int_param(params.get("show_last", 4), 0, 10)
            if show is None:
                raise _fail(ErrorCode.RULE_INVALID, f"{name}: show_last 0..10 arası tamsayı olmalı.")
            rule.params = {"show_last": show}
        elif t == "hmac":
            if mode == "append":
                if ecat != "text":
                    raise _fail(
                        ErrorCode.RULE_INVALID, f"{name}: hmac çıktısı metindir; hedef kolon metin olmalı."
                    )
                limit = _text_limit(eff)[0]
                if limit < HMAC_HEX_LEN:
                    if pk:
                        raise _fail(
                            ErrorCode.RULE_INVALID,
                            f"{name}: PK/UNIQUE hedefte hmac tam 64 karakter sığmalı ({limit} < 64).",
                        )
                    if limit < HMAC_MIN_LEN:
                        raise _fail(
                            ErrorCode.RULE_INVALID,
                            f"{name}: hedef uzunluk hmac için çok kısa ({limit} < {HMAC_MIN_LEN}).",
                        )
                    rule.hmac_len = limit
        elif t == "num_token":
            if scat != "number" or ecat not in ("number", "text"):
                raise _fail(ErrorCode.RULE_INVALID, f"{name}: num_token yalnız NUMBER kolonda kullanılabilir.")
            for c in (col, eff):
                if c.data_type == "NUMBER" and c.scale is not None and c.scale < 0:
                    raise _fail(ErrorCode.RULE_INVALID, f"{name}: negatif ölçekli NUMBER'da num_token kullanılamaz.")
        elif t == "text_token":
            if scat != "text" or ecat != "text":
                raise _fail(ErrorCode.RULE_INVALID, f"{name}: text_token yalnız metin kolonda kullanılabilir.")
        elif t == "date_token":
            if scat != "date" or ecat != "date":
                raise _fail(
                    ErrorCode.RULE_INVALID, f"{name}: date_token yalnız DATE/TIMESTAMP kolonda kullanılabilir."
                )
        elif t == "date_shift":
            if scat != "date" or ecat != "date":
                raise _fail(
                    ErrorCode.RULE_INVALID, f"{name}: date_shift yalnız DATE/TIMESTAMP kolonda kullanılabilir."
                )
            anchor = params.get("anchor")
            if not isinstance(anchor, str) or anchor not in src_by_name:
                raise _fail(ErrorCode.RULE_INVALID, f"{name}: date_shift çapa kolonu (anchor) kaynakta yok.")
            a = src_by_name[anchor]
            if not (a.supported and is_supported_type(a.data_type)):
                raise _fail(ErrorCode.RULE_INVALID, f"{name}: çapa kolonu {anchor} okunamayan tipte.")
            max_days = _as_int_param(params.get("max_days"), 1, 3650)
            if max_days is None:
                raise _fail(ErrorCode.RULE_INVALID, f"{name}: max_days 1..3650 arası tamsayı olmalı.")
            rule.params = {"anchor": anchor, "max_days": max_days}
            shift_params.add((anchor, max_days))
            if len(shift_params) > 1:
                raise _fail(
                    ErrorCode.RULE_INVALID,
                    f"{name}: tüm date_shift kuralları aynı anchor ve max_days değerini kullanmalı.",
                )
        compiled[name] = rule

    return RulePlan(source_cols, compiled, mode, key)


# --- uygulama -------------------------------------------------------------------------------


def _is_str(typ: pa.DataType) -> bool:
    return pa.types.is_string(typ) or pa.types.is_large_string(typ)


def _mask(v: str, n: int) -> str:
    ln = len(v)
    if ln == 0:
        return "*"  # '' Oracle'da NULL olur; maskeli değer boşa düşmez
    if ln <= n:
        return "*" * ln
    return "*" * (ln - n) + (v[-n:] if n else "")


def _random_digits(d: int) -> int:
    return _RNG.randrange(0 if d == 1 else 10 ** (d - 1), 10**d)


def _shift(v: object, days: int, is_oracle_date: bool) -> object:
    try:
        return v + timedelta(days=days)  # type: ignore[operator]
    except OverflowError:
        if isinstance(v, datetime):
            if days < 0:
                return datetime(1, 1, 1)
            return datetime(9999, 12, 31, 23, 59, 59, 0 if is_oracle_date else 999999)
        return date(1, 1, 1) if days < 0 else date(9999, 12, 31)


class RulePlan:
    def __init__(self, source_cols: list[ColumnInfo], rules: dict[str, "_Rule"], mode: str, key: bytes):
        self.source_columns = list(source_cols)
        self.mode = mode
        self._rules = rules
        self.output_columns: list[str] = [c.name for c in source_cols if rules[c.name].type != "exclude"]
        self._hmac_key = derive_key(key, b"tt-hmac")
        self._date_key = derive_key(key, b"tt-date")
        self._fpe = FeistelFPE(derive_key(key, b"tt-fpe"))
        self._offset_cache: dict = {}

    def rule_type(self, column: str) -> str:
        return self._rules[column].type

    def target_column_defs(self) -> list[ColumnDef]:
        out = []
        for name in self.output_columns:
            r = self._rules[name]
            c = r.src
            nullable = "Y" if c.nullable else "N"
            if r.type == "hmac":
                out.append(ColumnDef(name, "VARCHAR2", HMAC_HEX_LEN, HMAC_HEX_LEN, "B", None, None, nullable))
            else:
                out.append(
                    ColumnDef(
                        name, c.data_type, c.data_length, c.char_length, c.char_used,
                        c.precision, c.scale, nullable,
                    )
                )
        return out

    def apply_batch(self, batch: pa.RecordBatch) -> pa.RecordBatch:
        offsets: list[int] | None = None
        arrays = []
        for name in self.output_columns:
            r = self._rules[name]
            arr = batch.column(name)
            if r.trim and _is_str(arr.type):
                arr = pc.utf8_rtrim(arr, characters=" ")
            if r.type == "date_shift" and offsets is None:
                offsets = self._offsets(batch.column(r.params["anchor"]), r.params["max_days"])
            arrays.append(self._apply(r, arr, offsets))
        return pa.RecordBatch.from_arrays(arrays, names=self.output_columns)

    # --- kural başına ---

    def _apply(self, r: _Rule, arr: pa.Array, offsets: list[int] | None) -> pa.Array:
        t = r.type
        n = len(arr)
        if t == "keep":
            return arr
        if t == "null":
            return pa.nulls(n, type=arr.type)
        if t == "constant":
            if r.const_value is None:
                return pa.nulls(n, type=arr.type)
            return pa.array([_const_for(r.const_value, arr.type, r.column)] * n, type=arr.type)
        values = arr.to_pylist()
        if t == "mask":
            k = r.params["show_last"]
            return pa.array([None if v is None else _mask(v, k) for v in values], type=arr.type)
        if t == "hmac":
            return pa.array([None if v is None else self._hmac(r, v) for v in values], type=pa.string())
        if t == "num_token":
            fn = self._token_fn(r, arr.type)
            out = [None if v is None else self._cached(r, v, fn) for v in values]
            try:
                return pa.array(out, type=arr.type)
            except (pa.ArrowInvalid, OverflowError):
                raise _fail(ErrorCode.RULE_INVALID, f"{r.column}: num_token sonucu veri tipine sığmadı.") from None
        if t in ("text_token", "date_token"):
            fn = self._fpe.token_text_std if t == "text_token" else self._fpe.token_date
            out = [None if v is None else self._cached(r, v, fn) for v in values]
            try:
                return pa.array(out, type=arr.type)
            except (pa.ArrowInvalid, pa.ArrowTypeError, TypeError):
                raise _fail(ErrorCode.RULE_INVALID, f"{r.column}: {t} bu veri tipinde uygulanamaz.") from None
        if t == "date_shift":
            is_date = r.src.data_type == "DATE"
            return pa.array(
                [None if v is None else _shift(v, off, is_date) for v, off in zip(values, offsets)],  # type: ignore[arg-type]
                type=arr.type,
            )
        if t == "random":
            return pa.array([None if v is None else self._random(r, v, arr.type) for v in values], type=arr.type)
        raise _fail(ErrorCode.RULE_INVALID, f"{r.column}: bilinmeyen kural tipi '{t}'.")

    @staticmethod
    def _cached(r: _Rule, v: object, fn) -> object:
        out = r.cache.get(v)
        if out is None:
            out = fn(v)
            if len(r.cache) < CACHE_MAX:
                r.cache[v] = out
        return out

    def _hmac(self, r: _Rule, v: object) -> str:
        def calc(x: object) -> str:
            h = _hmac.new(self._hmac_key, canonical(x).encode("utf-8"), hashlib.sha256).hexdigest()
            return h[: r.hmac_len]

        return self._cached(r, v, calc)  # type: ignore[return-value]

    def _token_fn(self, r: _Rule, typ: pa.DataType):
        f = self._fpe
        if pa.types.is_integer(typ):
            return f.token_int
        if pa.types.is_decimal(typ):
            return f.token_decimal
        if _is_str(typ):
            if _category(r.src.data_type) == "number":

                def num_text(s: str) -> str:
                    try:
                        d = Decimal(s.strip())
                    except InvalidOperation:
                        return f.token_text(s)
                    return format(f.token_decimal(d), "f")

                return num_text
            return f.token_text
        raise _fail(ErrorCode.RULE_INVALID, f"{r.column}: num_token bu veri tipinde uygulanamaz.")

    def _offsets(self, anchor: pa.Array, max_days: int) -> list[int]:
        out = []
        cache = self._offset_cache
        for v in anchor.to_pylist():
            msg = _NULL_ANCHOR if v is None else canonical(v).encode("utf-8")
            off = cache.get(msg)
            if off is None:
                h = _hmac.new(self._date_key, msg, hashlib.sha256).digest()
                x = int.from_bytes(h[:8], "big") % (2 * max_days)
                off = -(x + 1) if x < max_days else x - max_days + 1
                if len(cache) < CACHE_MAX:
                    cache[msg] = off
            out.append(off)
        return out

    def _random(self, r: _Rule, v: object, typ: pa.DataType) -> object:
        if isinstance(v, str):
            if _category(r.src.data_type) == "number":
                try:
                    d = Decimal(v.strip())
                    return format(self._random_decimal(d, max(0, -d.as_tuple().exponent)), "f")  # type: ignore[operator]
                except InvalidOperation:
                    pass
            return "".join(_RNG.choice(_ALNUM) for _ in range(len(v)))
        if isinstance(v, bool):
            return v
        if isinstance(v, int):
            x = _random_digits(len(str(abs(v))))
            return -x if v < 0 and x else x
        if isinstance(v, Decimal):
            return self._random_decimal(v, typ.scale if pa.types.is_decimal(typ) else 0)
        if isinstance(v, float):
            ip = _random_digits(len(str(int(abs(v)))))
            x = ip + _RNG.random()
            return -x if v < 0 else x
        if isinstance(v, (datetime, date)):
            dt = _RANDOM_DATE_LO + timedelta(seconds=_RNG.randrange(_RANDOM_DATE_SPAN))
            return dt if isinstance(v, datetime) else dt.date()
        raise _fail(ErrorCode.RULE_INVALID, f"{r.column}: random bu veri tipinde uygulanamaz.")

    @staticmethod
    def _random_decimal(v: Decimal, scale: int) -> Decimal:
        with localcontext() as ctx:
            ctx.prec = 200
            ip = _random_digits(len(str(int(abs(v)))))
            frac = Decimal(_RNG.randrange(10**scale)).scaleb(-scale) if scale > 0 else Decimal(0)
            x = (Decimal(ip) + frac).quantize(Decimal(1).scaleb(-scale))
            return -x if v < 0 and x else x
