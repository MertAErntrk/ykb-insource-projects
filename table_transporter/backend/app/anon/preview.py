"""Kuralların ilk n satır üzerinde önce/sonra önizlemesi (API.md PreviewColumn biçimi)."""

from datetime import date, datetime, time
from decimal import Decimal

import pyarrow as pa

from app.anon.rules import RulePlan

_MAX_SAFE_INT = 2**53


def json_value(v: object) -> object:
    """JSON'da kayıpsız gösterilebilen değer: büyük int ve Decimal metin olur."""
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, int):
        return v if abs(v) <= _MAX_SAFE_INT else str(v)
    if isinstance(v, Decimal):
        return format(v, "f") if v.is_finite() else str(v)
    if isinstance(v, float):
        return v if v == v and v not in (float("inf"), float("-inf")) else str(v)
    if isinstance(v, (datetime, date, time)):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.hex()
    return str(v)


def build_preview(batch: pa.RecordBatch, plan: RulePlan, n: int = 5) -> list[dict]:
    head = batch.slice(0, n)
    after_batch = plan.apply_batch(head)
    present = set(head.schema.names)
    out = []
    for col in plan.source_columns:
        name = col.name
        rule_type = plan.rule_type(name)
        before_raw = head.column(name).to_pylist() if name in present else []
        if rule_type == "exclude":
            after_raw: list = []
        else:
            after_raw = after_batch.column(name).to_pylist()
        out.append(
            {
                "column": name,
                "type": rule_type,
                "before": [json_value(v) for v in before_raw],
                "after": [json_value(v) for v in after_raw],
                "null_before": sum(v is None for v in before_raw),
                "null_after": sum(v is None for v in after_raw),
            }
        )
    return out
