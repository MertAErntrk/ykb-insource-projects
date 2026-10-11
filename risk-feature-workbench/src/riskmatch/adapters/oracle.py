"""Read-only Oracle metadata inventory. No business rows are queried."""

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from time import monotonic

from riskmatch.errors import ConfigError, IntegrationError, OracleOperationError, AnalysisCancelled
from riskmatch.semantics import parse_definition


CONTEXT_SQL = "SELECT SYS_CONTEXT('USERENV','DB_NAME'), SYS_CONTEXT('USERENV','CON_NAME') FROM DUAL"


def oracle_error_code(error):
    """Return a public Oracle/driver identifier without exposing driver text."""
    detail = error.args[0] if error.args else error
    match = re.search(r"\b(?:ORA-\d{5}|DPY-\d{4}|DPI-\d{4})\b",
                      str(getattr(detail, "full_code", "")) + " " + str(error))
    return match.group(0) if match else None


def oracle_error(error, phase):
    """Extract only the public error identifier, never SQL or credentials."""
    code = oracle_error_code(error) or "kod alınamadı"
    hints = {
        "ORA-01017": "Kullanıcı adı veya parolayı kontrol edin.",
        "ORA-28000": "Hesap kilitli; veritabanı ekibiyle görüşün.",
        "ORA-28001": "Parolanın süresi dolmuş; parolanızı yenileyin.",
        "ORA-00942": "İlgili katalog görünümüne erişimi veritabanı ekibine kontrol ettirin.",
        "ORA-01031": "Bu işlem için katalog yetkisi yetersiz.",
        "ORA-12154": "Oracle servis adresi çözümlenemedi.",
        "ORA-12514": "Tanımlı Oracle servisi dinleyicide bulunamadı.",
        "DPY-3010": "Sunucu sürümü Thin bağlantıyı desteklemiyor; Oracle Client gereksinimini kontrol edin.",
        "DPY-3015": "Hesabın parola doğrulayıcısı Thin bağlantıyla uyumlu değil; veritabanı ekibiyle görüşün.",
        "DPY-4024": "Veritabanı çağrısı süre sınırını aştı. Bu kod parola veya yetki hatası anlamına gelmez. Daha dar şema kapsamıyla yeniden deneyin; sürerse bu aşamanın süresini veritabanı ekibine iletin.",
        "DPI-1067": "Veritabanı çağrısı süre sınırını aştı. Daha dar şema kapsamıyla yeniden deneyin.",
        "DPY-4011": "Veritabanı veya ağ bağlantıyı kapattı. Kurum ağı/VPN bağlantısını kontrol edip yeniden bağlanın.",
        "ORA-12170": "Bağlantı zaman aşımına uğradı. Kurum ağı/VPN ve Oracle servis erişimini kontrol edin.",
        "ORA-12541": "Oracle dinleyicisine erişilemedi. Kurum ağı ve servis erişimini kontrol edin.",
    }
    return OracleOperationError("Oracle · " + phase + " (" + code + "). " +
                                hints.get(code, "Bu aşama ve hata koduyla bağlantı/sürüm/yetki kontrolü yapılmalı."))


def read_context(cursor):
    """CON_NAME is unavailable on older servers; DB_NAME is the non-CDB scope."""
    try:
        cursor.execute(CONTEXT_SQL)
        context = cursor.fetchone()
    except Exception as error:
        if not any(code in str(oracle_error(error, "bağlam okuma")) for code in ("ORA-02003", "ORA-00904")):
            raise
        context = None
    if not context or len(context) < 2 or not context[1]:
        cursor.execute("SELECT SYS_CONTEXT('USERENV','DB_NAME') FROM DUAL")
        row = cursor.fetchone()
        context = (row[0], row[0]) if row and row[0] else None
    if not context or not context[0]:
        raise OracleOperationError("Oracle · veritabanı bağlamı okuma: DB_NAME alınamadı.")
    return str(context[0]), str(context[1])


def _scope_clause(schemas):
    binds = {f"schema_{index}": schema for index, schema in enumerate(schemas)}
    placeholders = ", ".join(f":{key}" for key in binds)
    return f"c.OWNER IN ({placeholders})", binds


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


class _ScopeMismatch(IntegrationError):
    """A reconnected session must never mix records from another container."""


class _CatalogBudget(OracleOperationError):
    """No new query may start once the catalog time budget is exhausted."""


class OracleAdapter:
    def __init__(self, config, *, connector=None, credentials=None):
        self.config = dict(config)
        self.connector = connector
        self.credentials = dict(credentials) if credentials is not None else None
        for key in ("source_database", "environment", "container"):
            if not isinstance(self.config.get(key), str) or not self.config[key]:
                raise ConfigError(f"Oracle {key} gerekli.")
        schemas = self.config.get("schemas")
        if not isinstance(schemas, list) or not schemas or any(
            not isinstance(name, str) or not name or len(name) > 128 for name in schemas
        ):
            raise ConfigError("Oracle schemas boş olmayan şema listesi olmalı.")
        self.schemas = sorted({name.upper() for name in schemas})
        if any(name in {"SYS", "SYSTEM", "OUTLN", "XDB"} for name in self.schemas):
            raise ConfigError("Sistem şemaları hedef kapsamda kullanılamaz.")
        for key in ("user_env", "password_env", "dsn_env"):
            if not isinstance(self.config.get(key), str) or not self.config[key]:
                raise ConfigError(f"Oracle {key} ortam değişkeni adı gerekli.")
        for key, default, maximum in (("connect_timeout_seconds", 10, 60),
                                      ("call_timeout_seconds", 30, 120),
                                      ("catalog_timeout_seconds", 300, 900)):
            value = self.config.get(key, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= maximum:
                raise ConfigError(f"Oracle {key} 0–{maximum} aralığında olmalı.")
        limit = self.config.get("max_records", 10000)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ConfigError("Oracle kayıt sınırı 1–10000 arasında olmalı.")

    def _connect(self):
        values = {key: (self.credentials.get(key.removesuffix("_env")) if self.credentials is not None else os.environ.get(self.config[key])) for key in ("user_env", "password_env", "dsn_env")}
        if not all(values.values()):
            raise ConfigError("Oracle kullanıcı, parola ve DSN ortam değişkenleri doldurulmalı.")
        kwargs = {"user": values["user_env"], "password": values["password_env"],
                  "dsn": values["dsn_env"],
                  "tcp_connect_timeout": float(self.config.get("connect_timeout_seconds", 10))}
        wallet_name = self.config.get("wallet_dir_env")
        if wallet_name:
            wallet = self.credentials.get("wallet_dir") if self.credentials is not None else os.environ.get(wallet_name)
            if not wallet:
                raise ConfigError("Oracle wallet dizini ortam değişkeni boş.")
            kwargs["wallet_location"] = wallet
            kwargs["config_dir"] = wallet
        wallet_password_name = self.config.get("wallet_password_env")
        if wallet_password_name:
            wallet_password = self.credentials.get("wallet_password") if self.credentials is not None else os.environ.get(wallet_password_name)
            if wallet_password:
                kwargs["wallet_password"] = wallet_password
        connector = self.connector
        if connector is None:
            try:
                import oracledb  # optional dependency, imported only for a real scan
            except ImportError:
                raise ConfigError("Gerçek Oracle taraması için python-oracledb kurulmalı.") from None
            connector = oracledb.connect
        try:
            return connector(**kwargs)
        except Exception as error:
            raise oracle_error(error, "oturum açma") from None

    def scan(self, progress=None):
        """Stream scoped metadata in small object batches, preserving partial evidence.

        No query joins the large column/comment views or sorts the entire catalog.
        Every metadata query has an owner, an object batch where relevant, and a
        server-side row limit. An interrupted schema cannot establish absence.
        """
        started = monotonic()
        deadline = started + float(self.config.get("catalog_timeout_seconds", 300))
        call_seconds = float(self.config.get("call_timeout_seconds", 30))
        limit = self.config.get("max_records", 10000)
        captured_at = datetime.now(timezone.utc).isoformat()
        scope = {key: self.config[key] for key in ("source_database", "environment", "container")}
        scope["schemas"] = list(self.schemas)
        records, errors, checks = {}, [], []
        incomplete_schemas = set()
        completed = 0
        connection = cursor = None
        observed_db_name = None
        schema = phase = ""

        def add_error(message):
            incomplete_schemas.add(schema)
            if message not in errors:
                errors.append(message)

        def close_connection():
            nonlocal connection, cursor
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
            connection = cursor = None

        def remaining_time():
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise _CatalogBudget("Oracle katalog taramasının toplam süre sınırına ulaşıldı. Okunan kayıtlar korundu; kalan kapsam taranmadı.")
            return remaining

        def before_call():
            connection.call_timeout = max(1, int(min(call_seconds, remaining_time()) * 1000))

        def notify(stage):
            if progress:
                progress({"stage": stage, "schema": schema, "records": len(records),
                          "schemas_completed": completed, "schemas_total": len(self.schemas)})

        def read_rows(sql, binds, maximum, consume):
            """Consume rows as they arrive so a later fetch timeout cannot erase them."""
            before_call()
            cursor.arraysize = 200
            cursor.prefetchrows = 200
            cursor.execute(sql, dict(binds, row_limit=maximum))
            count = 0
            if not hasattr(cursor, "fetchmany"):
                # Supports lightweight injected cursors; SQL still enforces the cap.
                before_call()
                chunks = [cursor.fetchall()[:maximum]]
                for chunk in chunks:
                    for row in chunk:
                        consume(row)
                        count += 1
                return count
            while count < maximum:
                before_call()
                chunk = cursor.fetchmany(min(200, maximum-count))
                if not chunk:
                    break
                for row in chunk[:maximum-count]:
                    consume(row)
                    count += 1
                notify(schema + " · " + phase)
            return count

        def measure(name, operation):
            nonlocal phase
            phase = name
            notify(schema + " · " + name)
            began = monotonic()
            try:
                count = operation()
            except (_ScopeMismatch, AnalysisCancelled):
                raise
            except Exception as error:
                safe = str(error) if isinstance(error, OracleOperationError) else str(oracle_error(error, schema + " · " + name))
                check = {"name": schema + " · " + name, "schema": schema, "phase": name,
                         "status": "failed", "elapsed_ms": round((monotonic()-began)*1000),
                         "records": len(records), "message": safe}
                code = oracle_error_code(error)
                if code:
                    check["code"] = code
                checks.append(check)
                add_error(safe)
                raise
            checks.append({"name": schema + " · " + name, "schema": schema, "phase": name,
                           "status": "passed", "elapsed_ms": round((monotonic()-began)*1000),
                           "records": count or 0, "message": "Katalog aşaması tamamlandı."})
            return count

        def connect_scope():
            nonlocal connection, cursor, observed_db_name
            remaining_time()
            connection = self._connect()
            before_call()
            cursor = connection.cursor()

            class ContextCursor:
                def execute(self, sql):
                    before_call()
                    return cursor.execute(sql)

                def fetchone(self):
                    before_call()
                    return cursor.fetchone()

            db_name, con_name = read_context(ContextCursor())
            expected_db = self.config.get("expected_db_name") or observed_db_name
            if (con_name.upper() != self.config["container"].upper()
                    or expected_db and db_name.upper() != str(expected_db).upper()):
                raise _ScopeMismatch("Oracle bağlantısı beklenen veritabanı/container kapsamında değil.")
            observed_db_name = db_name
            return 0

        def skip_schema(value, message):
            checks.append({"name": value + " · katalog taraması", "schema": value,
                           "phase": "katalog taraması", "status": "skipped", "elapsed_ms": 0,
                           "records": 0, "message": message})

        try:
            for schema_index, schema in enumerate(self.schemas):
                if len(records) >= limit or monotonic() >= deadline:
                    message = ("Katalog kayıt sınırına ulaşıldı; kalan kapsam taranmadı. Yokluk sonucu verilemez."
                               if len(records) >= limit else "Katalog süre sınırına ulaşıldı; kalan şemalar taranmadı.")
                    add_error(message)
                    for value in self.schemas[schema_index:]:
                        skip_schema(value, message)
                    break
                schema_start_count = len(records)
                try:
                    if connection is None:
                        measure("oturum ve veritabanı bağlamı", connect_scope)
                    objects = {}

                    def object_row(row):
                        obj, kind = row
                        if not isinstance(obj, str) or not obj or len(obj) > 128:
                            raise OracleOperationError("Oracle katalog nesne adı geçersiz; kapsam doğrulanamadı.")
                        if len(objects) < limit or obj in objects:
                            if obj not in objects or kind == "MATERIALIZED VIEW":
                                objects[obj] = kind

                    object_count = measure("tablo ve görünüm listesi", lambda: read_rows("""
                        SELECT OBJECT_NAME, OBJECT_TYPE FROM ALL_OBJECTS
                         WHERE OWNER = :schema_name
                           AND OBJECT_TYPE IN ('TABLE','VIEW','MATERIALIZED VIEW')
                           AND ROWNUM <= :row_limit
                    """, {"schema_name": schema}, limit+1, object_row))
                    if object_count > limit:
                        add_error(schema + ": nesne listesi kayıt sınırına ulaştı; tam kapsam doğrulanamadı.")
                    object_names = sorted(objects)
                    for offset in range(0, len(object_names), 25):
                        if len(records) >= limit:
                            add_error("Katalog kayıt sınırına ulaşıldı; kapsamı daraltın. Yokluk sonucu verilemez.")
                            break
                        names = object_names[offset:offset+25]
                        allowed_names = set(names)
                        binds = {"schema_name": schema}
                        binds.update({"object_"+str(i): value for i, value in enumerate(names)})
                        placeholders = ", ".join(":object_"+str(i) for i in range(len(names)))
                        where = "OWNER = :schema_name AND TABLE_NAME IN ("+placeholders+")"
                        batch_records = {}
                        available = limit-len(records)

                        def column_row(row):
                            owner, obj, column, data_type, length, precision, scale, nullable = row
                            if owner != schema or obj not in allowed_names:
                                raise _ScopeMismatch("Oracle kolon yanıtı istenen şema/nesne kapsamı dışında.")
                            if len(records) >= limit:
                                return
                            identity = {**{key: scope[key] for key in ("source_database", "environment", "container")},
                                        "schema": owner, "object": obj, "column": column}
                            record = {**identity, "catalog_id": _digest(identity), "object_type": objects[obj],
                                      "data_type": data_type, "data_length": length, "data_precision": precision,
                                      "data_scale": scale, "nullable": nullable, "column_comment": "", "table_comment": "",
                                      "captured_at": captured_at, "missing": ["column_comment", "key_constraints"],
                                      "key_columns": [], "verified_relationships": []}
                            records[record["catalog_id"]] = record
                            batch_records[(obj, column)] = record

                        column_count = measure("kolon kataloğu", lambda: read_rows("""
                            SELECT OWNER, TABLE_NAME, COLUMN_NAME, DATA_TYPE,
                                   DATA_LENGTH, DATA_PRECISION, DATA_SCALE, NULLABLE
                              FROM ALL_TAB_COLUMNS WHERE """+where+" AND ROWNUM <= :row_limit",
                            binds, available+1, column_row))
                        if column_count > available:
                            add_error("Katalog kayıt sınırına ulaşıldı; kapsamı daraltın. Yokluk sonucu verilemez.")
                        else:
                            invisible_objects = sorted(allowed_names-{obj for obj, _ in batch_records})
                            if invisible_objects:
                                message = (schema + ": nesne listesinde görülen " + str(len(invisible_objects))
                                           + " tablo/görünümün kolonları okunamadı; katalog görünürlüğü eksik olabilir. "
                                           + ", ".join(invisible_objects[:5]))
                                if len(invisible_objects) > 5:
                                    message += " (ilk 5 nesne)"
                                add_error(message)
                                checks.append({"name": schema + " · nesne kolon kapsamı", "schema": schema,
                                               "phase": "nesne kolon kapsamı", "status": "failed", "elapsed_ms": 0,
                                               "records": len(batch_records), "code": "OBJECT_COLUMNS_NOT_VISIBLE",
                                               "message": message})
                        if not batch_records:
                            continue

                        def comment_row(row):
                            obj, column, comment = row
                            record = batch_records.get((obj, column))
                            if record and comment:
                                record["column_comment"] = comment
                                if "column_comment" in record["missing"]:
                                    record["missing"].remove("column_comment")

                        comments = measure("kolon açıklamaları", lambda: read_rows(
                            "SELECT TABLE_NAME, COLUMN_NAME, COMMENTS FROM ALL_COL_COMMENTS WHERE "+where+" AND ROWNUM <= :row_limit",
                            binds, limit+1, comment_row))
                        if comments > limit:
                            add_error(schema + ": kolon açıklamaları kayıt sınırında kesildi.")

                        def table_comment_row(row):
                            obj, comment = row
                            for (record_obj, _), record in batch_records.items():
                                if obj == record_obj:
                                    record["table_comment"] = comment or ""

                        table_comments = measure("tablo açıklamaları", lambda: read_rows(
                            "SELECT TABLE_NAME, COMMENTS FROM ALL_TAB_COMMENTS WHERE "+where+" AND ROWNUM <= :row_limit",
                            binds, len(names)+1, table_comment_row))
                        if table_comments > len(names):
                            add_error(schema + ": tablo açıklamaları kayıt sınırında kesildi.")
                        key_map = {}

                        def key_row(row):
                            obj, kind, constraint, column, position = row
                            if obj in allowed_names:
                                key_map.setdefault(obj, []).append({"constraint": constraint, "type": kind,
                                                                   "column": column, "position": position})

                        key_limit = min(10000, max(200, len(batch_records)*4))
                        key_count = measure("anahtar kısıtları", lambda: read_rows("""
                            SELECT c.TABLE_NAME, c.CONSTRAINT_TYPE, c.CONSTRAINT_NAME,
                                   cc.COLUMN_NAME, cc.POSITION
                              FROM ALL_CONSTRAINTS c JOIN ALL_CONS_COLUMNS cc
                                ON cc.OWNER = c.OWNER AND cc.CONSTRAINT_NAME = c.CONSTRAINT_NAME
                               AND cc.TABLE_NAME = c.TABLE_NAME
                             WHERE c.OWNER = :schema_name AND c.TABLE_NAME IN ("""+placeholders+""")
                               AND c.CONSTRAINT_TYPE IN ('P','U')
                               AND c.STATUS = 'ENABLED' AND c.VALIDATED = 'VALIDATED'
                               AND ROWNUM <= :row_limit
                        """, binds, key_limit+1, key_row))
                        for (obj, _), record in batch_records.items():
                            if key_count <= key_limit:
                                record["key_columns"] = sorted(key_map.get(obj, []), key=lambda key: (key["constraint"], key["position"]))
                                record["missing"].remove("key_constraints")
                        if key_count > key_limit:
                            add_error(schema + ": anahtar kısıtları kayıt sınırında kesildi.")
                    if len(records) == schema_start_count:
                        add_error("Şema için erişilebilir kolon görülmedi: " + schema)
                    if schema not in incomplete_schemas:
                        completed += 1
                    notify(schema + " · şema taraması tamamlandı")
                except (_ScopeMismatch, AnalysisCancelled):
                    raise
                except Exception as error:
                    # measure() already captured the safe phase/code. Discard a
                    # possibly invalid session, retain all successfully read rows,
                    # and give the next schema its own authenticated connection.
                    if not checks or checks[-1]["status"] != "failed" or checks[-1].get("schema") != schema:
                        add_error(str(oracle_error(error, schema + " · " + phase)))
                    close_connection()
                    notify(schema + " · tarama eksik; okunan kayıtlar korundu")
        finally:
            close_connection()

        ordered = sorted(records.values(), key=lambda row: (row["schema"], row["object"], row["column"]))
        for record in ordered:
            definition = parse_definition(record["column"], record["column_comment"])
            for fact in definition["facts"].values():
                if fact["source"] == "description":
                    fact["source"] = "verified_metadata"
            record["definition"] = definition
            versioned = {key: value for key, value in record.items() if key not in ("metadata_version", "captured_at")}
            record["metadata_version"] = _digest(versioned)
        version = _digest({"scope": scope, "records": [(row["catalog_id"], row["metadata_version"]) for row in ordered]})
        return {"records": ordered, "scope": scope, "complete": not errors and completed == len(self.schemas),
                "errors": errors, "version": version, "captured_at": captured_at, "checks": checks,
                "scan_stats": {"record_count": len(ordered), "schemas_completed": completed,
                               "schemas_total": len(self.schemas), "elapsed_ms": round((monotonic()-started)*1000)}}
