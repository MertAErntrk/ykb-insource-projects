"""Read-only Oracle metadata inventory. No business rows are queried."""

import hashlib
import json
import os
from datetime import datetime, timezone

from riskmatch.errors import ConfigError, IntegrationError
from riskmatch.semantics import parse_definition


CONTEXT_SQL = "SELECT SYS_CONTEXT('USERENV','DB_NAME'), SYS_CONTEXT('USERENV','CON_NAME') FROM DUAL"


def _scope_clause(schemas):
    binds = {f"schema_{index}": schema for index, schema in enumerate(schemas)}
    placeholders = ", ".join(f":{key}" for key in binds)
    return f"c.OWNER IN ({placeholders})", binds


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


class OracleAdapter:
    def __init__(self, config, *, connector=None):
        self.config = dict(config)
        self.connector = connector
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
                                      ("call_timeout_seconds", 30, 120)):
            value = self.config.get(key, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= maximum:
                raise ConfigError(f"Oracle {key} 0–{maximum} aralığında olmalı.")

    def _connect(self):
        values = {key: os.environ.get(self.config[key]) for key in ("user_env", "password_env", "dsn_env")}
        if not all(values.values()):
            raise ConfigError("Oracle kullanıcı, parola ve DSN ortam değişkenleri doldurulmalı.")
        kwargs = {"user": values["user_env"], "password": values["password_env"],
                  "dsn": values["dsn_env"],
                  "tcp_connect_timeout": float(self.config.get("connect_timeout_seconds", 10))}
        wallet_name = self.config.get("wallet_dir_env")
        if wallet_name:
            wallet = os.environ.get(wallet_name)
            if not wallet:
                raise ConfigError("Oracle wallet dizini ortam değişkeni boş.")
            kwargs["wallet_location"] = wallet
            kwargs["config_dir"] = wallet
        wallet_password_name = self.config.get("wallet_password_env")
        if wallet_password_name:
            wallet_password = os.environ.get(wallet_password_name)
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
        except Exception:
            raise IntegrationError("Oracle bağlantısı kurulamadı.") from None

    def scan(self):
        connection = self._connect()
        captured_at = datetime.now(timezone.utc).isoformat()
        scope = {key: self.config[key] for key in ("source_database", "environment", "container")}
        scope["schemas"] = list(self.schemas)
        try:
            connection.call_timeout = int(float(self.config.get("call_timeout_seconds", 30)) * 1000)
            cursor = connection.cursor()
            cursor.execute(CONTEXT_SQL)
            context = cursor.fetchone()
            if not context or len(context) < 2:
                raise IntegrationError("Oracle veritabanı bağlamı okunamadı.")
            db_name, con_name = (str(context[0] or ""), str(context[1] or ""))
            expected_db = self.config.get("expected_db_name")
            if (con_name.upper() != self.config["container"].upper()
                    or expected_db and db_name.upper() != str(expected_db).upper()):
                raise IntegrationError("Oracle bağlantısı beklenen veritabanı/container kapsamında değil.")
            where, binds = _scope_clause(self.schemas)
            sql = f"""
                SELECT c.OWNER, c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE,
                       c.DATA_LENGTH, c.DATA_PRECISION, c.DATA_SCALE, c.NULLABLE,
                       cc.COMMENTS AS COLUMN_COMMENT, tc.COMMENTS AS TABLE_COMMENT,
                       tc.TABLE_TYPE AS OBJECT_TYPE
                  FROM ALL_TAB_COLUMNS c
                  LEFT JOIN ALL_COL_COMMENTS cc
                    ON cc.OWNER = c.OWNER AND cc.TABLE_NAME = c.TABLE_NAME
                   AND cc.COLUMN_NAME = c.COLUMN_NAME
                  LEFT JOIN ALL_TAB_COMMENTS tc
                    ON tc.OWNER = c.OWNER AND tc.TABLE_NAME = c.TABLE_NAME
                 WHERE {where}
                 ORDER BY c.OWNER, c.TABLE_NAME, c.COLUMN_ID
            """
            cursor.execute(sql, binds)
            rows = cursor.fetchall()
            records = []
            for row in rows:
                (schema, obj, column, data_type, length, precision, scale, nullable,
                 column_comment, table_comment, object_type) = row
                identity = {"source_database": scope["source_database"],
                            "environment": scope["environment"], "container": scope["container"],
                            "schema": schema, "object": obj, "column": column}
                technical = {"data_type": data_type, "data_length": length,
                             "data_precision": precision, "data_scale": scale, "nullable": nullable}
                definition = parse_definition(column, column_comment or "")
                for fact in definition["facts"].values():
                    if fact["source"] == "description":
                        fact["source"] = "verified_metadata"
                record = {**identity, "catalog_id": _digest(identity), "object_type": object_type or "UNKNOWN",
                          **technical, "column_comment": column_comment or "", "table_comment": table_comment or "",
                          "metadata_version": "", "captured_at": captured_at,
                          "missing": [], "definition": definition,
                          "key_columns": [], "verified_relationships": []}
                if not column_comment:
                    record["missing"].append("column_comment")
                if not object_type:
                    record["missing"].append("object_type")
                records.append(record)
            visible_schemas = {record["schema"] for record in records}
            errors = [f"Şema için erişilebilir kolon görülmedi: {schema}"
                      for schema in self.schemas if schema not in visible_schemas]
            try:
                key_sql = f"""
                    SELECT c.OWNER, c.TABLE_NAME, c.CONSTRAINT_TYPE, c.CONSTRAINT_NAME,
                           cc.COLUMN_NAME, cc.POSITION
                      FROM ALL_CONSTRAINTS c
                      JOIN ALL_CONS_COLUMNS cc
                        ON cc.OWNER = c.OWNER AND cc.CONSTRAINT_NAME = c.CONSTRAINT_NAME
                       AND cc.TABLE_NAME = c.TABLE_NAME
                     WHERE {where} AND c.CONSTRAINT_TYPE IN ('P','U')
                       AND c.STATUS = 'ENABLED' AND c.VALIDATED = 'VALIDATED'
                     ORDER BY c.OWNER, c.TABLE_NAME, c.CONSTRAINT_NAME, cc.POSITION
                """
                cursor.execute(key_sql, binds)
                key_rows = cursor.fetchall()
                keys = {}
                for owner, obj, kind, constraint_name, column, position in key_rows:
                    keys.setdefault((owner, obj), []).append({"constraint": constraint_name,
                                                                "type": kind, "column": column,
                                                                "position": position})
                for record in records:
                    record["key_columns"] = keys.get((record["schema"], record["object"]), [])
            except Exception:
                errors.append("Anahtar kısıtı metadata'sı okunamadı.")
                for record in records:
                    record["missing"].append("key_constraints")
            for record in records:
                versioned = {key: value for key, value in record.items()
                             if key not in ("metadata_version", "captured_at")}
                record["metadata_version"] = _digest(versioned)
            snapshot_version = _digest({"scope": scope, "records": [(item["catalog_id"], item["metadata_version"])
                                                                    for item in records]})
            return {"records": records, "scope": scope, "complete": not errors,
                    "errors": errors, "version": snapshot_version, "captured_at": captured_at}
        except IntegrationError:
            raise
        except Exception:
            raise IntegrationError("Oracle metadata taraması tamamlanamadı.") from None
        finally:
            connection.close()
