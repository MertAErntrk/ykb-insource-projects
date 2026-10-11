"""Bounded read-only connection diagnostics; no business rows or raw errors."""

from time import monotonic

from .adapters.oracle import OracleAdapter, oracle_error, oracle_error_code, read_context
from .errors import ConfigError, OracleOperationError


SCHEMA_LIMIT = 1000
SCHEMA_SQL = """
    SELECT USERNAME
      FROM (SELECT USERNAME FROM ALL_USERS
             WHERE USERNAME NOT IN ('SYS','SYSTEM','OUTLN','XDB')
             ORDER BY USERNAME)
     WHERE ROWNUM <= :schema_limit
"""
SELECTED_SCHEMA_SQL = """
    SELECT c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE,
           cc.COMMENTS AS COLUMN_COMMENT, tc.COMMENTS AS TABLE_COMMENT
      FROM ALL_TAB_COLUMNS c
      LEFT JOIN ALL_COL_COMMENTS cc
        ON cc.OWNER = c.OWNER AND cc.TABLE_NAME = c.TABLE_NAME
       AND cc.COLUMN_NAME = c.COLUMN_NAME
      LEFT JOIN ALL_TAB_COMMENTS tc
        ON tc.OWNER = c.OWNER AND tc.TABLE_NAME = c.TABLE_NAME
     WHERE c.OWNER = :schema_name AND ROWNUM <= 1
"""
SCHEMA_WARNING = (
    "Liste ALL_USERS görünümündeki şema adaylarını gösterir; bu şemaların tablo/kolon "
    "bilgilerini okuyabildiğiniz henüz doğrulanmadı. Şema seçtikten sonra erişim testini çalıştırın."
)


def _report():
    return {"ok": False, "connected": False, "db_name": None, "container": None,
            "schemas": [], "truncated": False, "schema_source": "ALL_USERS",
            "warning": "", "checks": []}


def _check(report, name, operation, message):
    started = monotonic()
    try:
        value = operation()
    except Exception as error:
        if isinstance(error, (OracleOperationError, ConfigError)):
            safe_message = str(error)
        else:
            safe_message = str(oracle_error(error, name))
        check = {"name": name, "status": "failed", "elapsed_ms": round((monotonic()-started)*1000),
                 "message": safe_message}
        code = oracle_error_code(error)
        if code:
            check["code"] = code
        report["checks"].append(check)
        report["error"] = safe_message
        return False, None
    report["checks"].append({"name": name, "status": "passed",
                             "elapsed_ms": round((monotonic()-started)*1000), "message": message})
    return True, value


def _skip(report, names, message="Önceki aşama tamamlanamadığı için çalıştırılmadı."):
    for name in names:
        report["checks"].append({"name": name, "status": "skipped", "elapsed_ms": 0, "message": message})


def _close(connection):
    if connection is not None:
        try:
            connection.close()
        except Exception:
            # Closing must not replace the measured diagnostic or expose driver text.
            pass


def _connect_and_context(adapter, report, timeout_ms):
    succeeded, connection = _check(report, "Oracle oturumu açma", adapter._connect,
                                    "Oracle oturumu açıldı; kullanıcı adı ve parola kabul edildi.")
    if not succeeded:
        _skip(report, ["Sorgu süre sınırı", "Veritabanı bağlamı"])
        return None, None
    report["connected"] = True
    succeeded, _ = _check(report, "Sorgu süre sınırı",
                           lambda: setattr(connection, "call_timeout", timeout_ms),
                           "Her veritabanı çağrısı için " + str(timeout_ms//1000) + " saniye sınırı ayarlandı.")
    if not succeeded:
        _skip(report, ["Veritabanı bağlamı"])
        return connection, None

    def context():
        cursor = connection.cursor()
        db_name, container = read_context(cursor)
        expected_db = adapter.config.get("expected_db_name")
        expected_container = adapter.config.get("container")
        if (expected_db and db_name.upper() != str(expected_db).upper()
                or expected_container != "PROBE" and container.upper() != expected_container.upper()):
            raise OracleOperationError("Oracle · veritabanı bağlamı: beklenen veritabanı/container doğrulanamadı.")
        return cursor, db_name, container

    succeeded, value = _check(report, "Veritabanı bağlamı", context,
                              "Veritabanı ve container bilgisi okundu.")
    if not succeeded:
        return connection, None
    cursor, report["db_name"], report["container"] = value
    return connection, cursor


def diagnose_oracle(config, credentials):
    """Authenticate and list schema candidates without scanning the column catalog."""
    adapter = OracleAdapter(config, credentials=credentials)
    report = _report()
    connection = None
    try:
        connection, cursor = _connect_and_context(adapter, report, 10000)
        if cursor is None:
            _skip(report, ["Şema adaylarını listeleme"])
            return report

        def candidates():
            cursor.arraysize = 100
            cursor.execute(SCHEMA_SQL, {"schema_limit": SCHEMA_LIMIT+1})
            rows = cursor.fetchmany(SCHEMA_LIMIT+1)
            return rows

        succeeded, rows = _check(report, "Şema adaylarını listeleme", candidates,
                                 "Şema adayları ALL_USERS görünümünden okundu; kolon kataloğu taranmadı.")
        if not succeeded:
            report["warning"] = "Oturum açıldı ve veritabanı bağlamı okundu; şema listesi alınamadı. Aşağıdaki başarısız aşamayı inceleyin."
            return report
        report["schemas"] = sorted({str(row[0]) for row in rows[:SCHEMA_LIMIT]})
        report["truncated"] = len(rows) > SCHEMA_LIMIT
        report["warning"] = SCHEMA_WARNING
        if report["truncated"]:
            report["warning"] += " Liste ilk 1.000 şema adayıyla sınırlı; listelenmeyen bir şema yok sayılmaz."
        if not report["schemas"]:
            report["error"] = "Oracle oturumu açıldı, ancak seçilebilecek şema adayı bulunamadı. Veritabanı ekibiyle görünür şema listesini kontrol edin."
        report["ok"] = bool(report["schemas"])
        return report
    finally:
        _close(connection)


def diagnose_selected_schemas(config, credentials, schemas):
    """Check at most one metadata row per selected schema within a time budget.

    Visibility does not establish full inventory completeness or access to business
    rows. The 45-second diagnostic budget reserves half the remainder per round-trip
    because execute and fetch are separate round-trips.
    """
    if (not isinstance(schemas, list) or not 1 <= len(schemas) <= 30
            or any(not isinstance(name, str) or not name or len(name) > 128 for name in schemas)):
        raise ConfigError("Test için 1–30 şema seçin.")
    selected = list(dict.fromkeys(schemas))
    scoped = dict(config, schemas=selected)
    adapter = OracleAdapter(scoped, credentials=credentials)
    report = _report()
    report.update(schemas=selected, schema_source="selected", accessible_schemas=[],
                  warning="Bu test her seçili şemadan en fazla bir kolonun katalog bilgisini okur. İş verisi okunmaz; tam tarama veya tüm tablolara erişim doğrulanmış sayılmaz.")
    connection = None
    deadline = monotonic()+45
    try:
        connection, cursor = _connect_and_context(adapter, report, 10000)
        if cursor is None:
            _skip(report, ["Şema erişimi · " + schema for schema in selected])
            return report
        for position, schema in enumerate(selected):
            remaining = deadline-monotonic()
            if remaining < 1:
                _skip(report, ["Şema erişimi · " + value for value in selected[position:]],
                      "Toplam şema testi süre sınırına ulaşıldı. Daha az şema seçip yeniden deneyin.")
                report.setdefault("error", "Şema testinin süre sınırına ulaşıldı; kalan şemalar test edilmedi.")
                break

            def metadata():
                connection.call_timeout = max(1, int(min(10, remaining/2)*1000))
                cursor.arraysize = 1
                cursor.prefetchrows = 1
                cursor.execute(SELECTED_SCHEMA_SQL, {"schema_name": schema})
                return cursor.fetchone()

            succeeded, row = _check(report, "Şema erişimi · " + schema, metadata,
                                    "Kolon ve açıklama kataloğuna sınırlı erişim doğrulandı.")
            if succeeded and row:
                report["accessible_schemas"].append(schema)
            elif succeeded:
                report["checks"][-1].update(status="failed", message="Bu şema için görünür kolon bulunamadı. Şema boş olabilir veya katalog görünürlüğünüz yetersiz olabilir; erişim doğrulanamadı.")
                report.setdefault("error", "Seçili şemalardan en az birinde katalog erişimi doğrulanamadı.")
            elif report["checks"][-1].get("code") in {"DPY-4024", "DPI-1067", "DPY-4011", "DPY-1001", "DPI-1080"}:
                _skip(report, ["Şema erişimi · " + value for value in selected[position+1:]],
                      "Zaman aşımı veya bağlantı kesintisinden sonra oturum yeniden kullanılmadı. Daha az şema seçerek yeniden deneyin.")
                break
        report["ok"] = len(report["accessible_schemas"]) == len(selected)
        return report
    finally:
        _close(connection)
