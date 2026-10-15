"""Ekleme (append) öncesi kaynak/hedef uyumluluk raporu (API.md POST /transfers/compat).

Hata kodu atmaz: engeller `error`, riskler `warn` olarak raporlanır. Başlatmada servis
`error` görürse INCOMPATIBLE / TRIGGER_PRESENT döner.
"""

from app.anon.rules import FLOAT_TYPES, TEXT_TYPES, ColumnInfo, RulePlan
from app.errors import AppError, ErrorCode

_LEVELS = {"ok": 0, "warn": 1, "error": 2}
_LENGTH_RULES = {"keep", "num_token", "text_token", "random", "mask"}
_NUMERIC_RULES = {"keep", "num_token", "random"}
_SKIP_FAMILY = {"null", "constant"}  # NULL her tipe yazılır; sabit hedef tipe göre derlenir


def _family(data_type: str) -> str | None:
    if data_type in TEXT_TYPES:
        return "text"
    if data_type == "NUMBER" or data_type in FLOAT_TYPES:
        return "number"
    if data_type == "DATE" or data_type.startswith("TIMESTAMP"):
        return "date"
    return None


_FAMILY_TR = {"text": "metin", "number": "sayı", "date": "tarih"}


def _text_limit(c: ColumnInfo) -> tuple[int, str]:
    if c.data_type in ("NCHAR", "NVARCHAR2") or c.char_used == "C":
        return int(c.char_length or c.data_length or 0), "C"
    return int(c.data_length or c.char_length or 0), "B"


def _item(items: list[dict], column: str | None, level: str, message: str) -> None:
    items.append({"column": column, "level": level, "message": message})


def compat_report(
    source_cols: list[ColumnInfo],
    target_cols: tuple[list[ColumnInfo], dict[str, dict]],
    plan_or_error: RulePlan | AppError | None,
    target_obj: dict | None,
    triggers: list[dict],
    *,
    rules: list[dict] | None = None,
) -> dict:
    """API.md compat yanıtı.

    target_cols: dictionary.read_columns çıktısı (kolonlar, ek bilgi {ad: {virtual, identity,
    hidden, default}}). plan_or_error: compile_plan sonucu ya da fırlattığı AppError (None → plan
    denetimi atlanır). rules: kural listesi (plan yoksa çıktı kolonları ve kural tipleri buradan).
    """
    items: list[dict] = []
    tcols, extra = target_cols
    by_target = {c.name: c for c in tcols}

    target_is_table = bool(target_obj) and str(target_obj.get("OBJECT_TYPE") or "").upper() == "TABLE"
    if not target_is_table:
        kind = (target_obj or {}).get("OBJECT_TYPE") or "nesne"
        _item(items, None, "error", f"Hedef bir tablo değil ({kind}); yalnız tabloya ekleme yapılabilir.")
    has_trigger = bool(triggers)
    if has_trigger:
        names = ", ".join(str(t.get("TRIGGER_NAME")) for t in triggers[:3])
        _item(items, None, "error", f"Hedef tabloda etkin trigger var ({names}); ekleme yapılamaz.")

    rule_types: dict[str, str] = {}
    if isinstance(plan_or_error, RulePlan):
        rule_types = {c.name: plan_or_error.rule_type(c.name) for c in plan_or_error.source_columns}
    elif rules:
        rule_types = {r["column"]: r.get("type") for r in rules if isinstance(r, dict) and "column" in r}
    if isinstance(plan_or_error, RulePlan):
        output = list(plan_or_error.output_columns)
    else:
        output = [c.name for c in source_cols if rule_types.get(c.name, "keep") != "exclude"]
    by_source = {c.name: c for c in source_cols}

    for name in output:
        src = by_source[name]
        tgt = by_target.get(name)
        rt = rule_types.get(name, "keep")
        if tgt is None:
            _item(items, name, "error", "Hedef tabloda bu kolon yok.")
            continue
        e = extra.get(name, {})
        flags = [w for w, k in (("sanal", "virtual"), ("identity", "identity"), ("gizli", "hidden")) if e.get(k)]
        if flags:
            _item(items, name, "error", f"Hedef kolon {'/'.join(flags)}; değer yazılamaz.")
            continue
        sfam = "text" if rt == "hmac" else _family(src.data_type)
        tfam = _family(tgt.data_type)
        if rt not in _SKIP_FAMILY and sfam != tfam:
            _item(items, name, "error",
                  f"Tip uyuşmuyor: {_FAMILY_TR.get(sfam, src.data_type)} → "
                  f"{_FAMILY_TR.get(tfam, tgt.data_type)} ({src.data_type} → {tgt.data_type}).")
            continue
        if rt in _LENGTH_RULES and sfam == "text" and tfam == "text":
            (slen, _), (tlen, _) = _text_limit(src), _text_limit(tgt)
            if tlen and slen and tlen < slen:
                _item(items, name, "warn", f"Hedef kolon daha kısa ({tlen} < {slen}).")
        if rt in _NUMERIC_RULES and sfam == "number" and tfam == "number" and tgt.data_type == "NUMBER":
            if tgt.precision is not None and (src.precision is None or tgt.precision < src.precision):
                _item(items, name, "warn",
                      f"Hedef hassasiyet daha küçük ({tgt.precision} < {src.precision or 38}).")
            elif (tgt.scale or 0) < (src.scale or 0):
                _item(items, name, "warn", f"Hedef ölçek daha küçük ({tgt.scale or 0} < {src.scale}).")

    out_set = set(output)
    for c in tcols:
        if c.name in out_set:
            continue
        e = extra.get(c.name, {})
        if e.get("virtual") or e.get("identity") or e.get("hidden"):
            continue
        if not c.nullable and not e.get("default"):
            _item(items, c.name, "error", "Hedefte NOT NULL kolon kaynak çıktısında yok (varsayılan değer yok).")
        elif c.nullable:
            _item(items, c.name, "warn", "Hedefte fazla kolon var; NULL kalır.")

    if isinstance(plan_or_error, AppError):
        has_error = any(i["level"] == "error" for i in items)
        if plan_or_error.code == ErrorCode.RULE_INVALID or not has_error:
            _item(items, None, "error", plan_or_error.detail)

    worst = max((_LEVELS[i["level"]] for i in items), default=0)
    status = next(k for k, v in _LEVELS.items() if v == worst)
    return {"status": status, "target_is_table": target_is_table, "has_enabled_trigger": has_trigger,
            "items": items}
