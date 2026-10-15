from app.anon.rules import ColumnInfo, compile_plan
from app.errors import AppError, ErrorCode
from app.transfer.compat import compat_report

KEY = b"k" * 32
TABLE = {"OBJECT_ID": 1, "CREATED": None, "OBJECT_TYPE": "TABLE"}


def col(name, data_type="VARCHAR2", length=80, *, precision=None, scale=None, nullable=True):
    text = data_type in ("CHAR", "VARCHAR2", "NCHAR", "NVARCHAR2")
    return ColumnInfo(
        name=name, data_type=data_type, data_length=length if text else 22,
        char_length=length if text else 0, char_used="B" if text else None,
        precision=precision, scale=scale, nullable=nullable, is_pk_or_unique=False, supported=True,
    )


SRC = [col("ID", "NUMBER", precision=12, scale=0, nullable=False), col("AD"), col("DOGUM", "DATE")]


def rules(**types):
    return [{"column": c.name, "type": types.get(c.name, "keep"), "params": {}} for c in SRC]


def report(target, extra=None, *, rule_list=None, obj=TABLE, triggers=(), plan=None):
    rule_list = rule_list or rules()
    return compat_report(SRC, (target, extra or {}), plan, obj, list(triggers), rules=rule_list)


def levels(r):
    return {(i["column"], i["level"]) for i in r["items"]}


def test_identical_is_ok():
    r = report(list(SRC))
    assert r == {"status": "ok", "target_is_table": True, "has_enabled_trigger": False, "items": []}


def test_trigger_and_view_are_errors():
    r = report(list(SRC), triggers=[{"OWNER": "T", "TRIGGER_NAME": "TRG_BI"}])
    assert r["status"] == "error" and r["has_enabled_trigger"] is True
    assert "TRG_BI" in r["items"][0]["message"]
    v = report(list(SRC), obj={"OBJECT_ID": 1, "CREATED": None, "OBJECT_TYPE": "VIEW"})
    assert v["status"] == "error" and v["target_is_table"] is False
    assert report(list(SRC), obj=None)["target_is_table"] is False


def test_short_text_warns_for_keep_and_mask():
    target = [SRC[0], col("AD", length=50), SRC[2]]
    r = report(target)
    assert r["status"] == "warn"
    assert r["items"] == [{"column": "AD", "level": "warn", "message": "Hedef kolon daha kısa (50 < 80)."}]
    assert report(target, rule_list=rules(AD="mask"))["status"] == "warn"
    assert report(target, rule_list=rules(AD="null"))["status"] == "ok"


def test_missing_column_and_type_family():
    r = report([SRC[0], SRC[2]])
    assert ("AD", "error") in levels(r)
    r = report([SRC[0], col("AD", "NUMBER", precision=5, scale=0), SRC[2]])
    assert r["status"] == "error" and "metin → sayı" in r["items"][0]["message"]
    r = report([SRC[0], col("AD"), col("DOGUM", "TIMESTAMP(6)")])
    assert r["status"] == "ok"  # tarih ailesi


def test_hmac_counts_as_text():
    target = [col("ID", "VARCHAR2", 64, nullable=False), SRC[1], SRC[2]]
    assert report(target, rule_list=rules(ID="hmac"))["status"] == "ok"
    assert report(list(SRC), rule_list=rules(ID="hmac"))["status"] == "error"


def test_virtual_identity_hidden_target_errors():
    for flag in ("virtual", "identity", "hidden"):
        r = report(list(SRC), {"AD": {flag: True}})
        assert ("AD", "error") in levels(r)


def test_extra_target_columns():
    extra_nn = col("ZORUNLU", nullable=False)
    assert ("ZORUNLU", "error") in levels(report([*SRC, extra_nn]))
    assert report([*SRC, extra_nn], {"ZORUNLU": {"default": "(tanımlı)"}})["status"] == "ok"
    r = report([*SRC, col("NOT_ALANI")])
    assert r["status"] == "warn" and r["items"][0]["message"].endswith("NULL kalır.")
    assert report([*SRC, col("V")], {"V": {"virtual": True}})["status"] == "ok"


def test_excluded_column_not_required_in_target():
    assert report([SRC[0], SRC[2]], rule_list=rules(AD="exclude"))["status"] == "ok"


def test_precision_and_scale_warn():
    r = report([col("ID", "NUMBER", precision=8, scale=0, nullable=False), SRC[1], SRC[2]])
    assert r["items"][0]["level"] == "warn" and "8 < 12" in r["items"][0]["message"]
    src = [col("T", "NUMBER", precision=10, scale=2)]
    r = compat_report(src, ([col("T", "NUMBER", precision=10, scale=0)], {}), None, TABLE, [],
                      rules=[{"column": "T", "type": "keep"}])
    assert r["status"] == "warn" and "ölçek" in r["items"][0]["message"]


def test_plan_and_plan_error():
    target = list(SRC)
    plan = compile_plan(rules(), SRC, mode="append", target_cols={c.name: c for c in target}, key=KEY)
    assert compat_report(SRC, (target, {}), plan, TABLE, [])["status"] == "ok"
    err = AppError(ErrorCode.RULE_INVALID, "AD: mask yalnız metin kolonlarda kullanılabilir.")
    r = report(target, plan=err)
    assert r["status"] == "error" and r["items"][-1]["message"] == err.detail
